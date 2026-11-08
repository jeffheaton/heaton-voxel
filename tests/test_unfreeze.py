"""Tests for unfreeze: the three states freezing writes, and nothing that
merely looks like winter.

The fixture world holds no snow and no ice at all, so every frozen thing
here is built first and then thawed -- which is also the only way to put a
lid of ice over open air, the case the counters exist to report.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest

from heaton_voxel.blocks import BlockState
from heaton_voxel.nbt import NBTError, TagInt
from heaton_voxel.world import MinecraftSave
from tests.test_world import make_save

CHUNKS = [(x, z) for x in range(-1, 2) for z in range(-1, 2)]
Y = 70


class UnfreezeBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="unfreeze-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, CHUNKS)
        self.save = MinecraftSave.load(self.dir)

    def state(self, x, y, z) -> str:
        """The block as the game writes it: name and every property."""
        return str(self.save.get_block(x, y, z))


class ThawTests(UnfreezeBase):
    def test_a_single_layer_goes_however_it_was_written(self):
        """One layer is what freezing lays, and a bare ``minecraft:snow`` is
        the same state as ``layers=1`` -- the game's default for the block."""
        for i, block in enumerate(("minecraft:snow", "minecraft:snow[layers=1]")):
            self.save.set_block(i, Y, 0, block)
        result = self.save.unfreeze((0, Y, 0), (1, Y, 0))
        self.assertEqual(result.snow_removed, 2)
        self.assertEqual(result.deep_snow_kept, 0)
        self.assertEqual(result.chunks_modified, 1)
        self.assertTrue(result.complete)
        for i in range(2):
            self.assertEqual(self.state(i, Y, 0), "minecraft:air")

    def test_ice_becomes_a_water_source_exactly(self):
        """Ice only ever freezes from a source, so it thaws back to one.
        Flowing water would drain away, and a bare ``minecraft:water`` is
        not a state the game writes."""
        self.save.set_block(0, Y, 0, "minecraft:ice")
        result = self.save.unfreeze((0, Y, 0), (0, Y, 0))
        self.assertEqual(result.ice_melted, 1)
        self.assertEqual(self.state(0, Y, 0), "minecraft:water[level=0]")

    def test_snowy_ground_is_unsnowed(self):
        for i, block in enumerate(
            ("minecraft:grass_block", "minecraft:podzol", "minecraft:mycelium")
        ):
            self.save.set_block(i, Y, 0, f"{block}[snowy=true]")
        result = self.save.unfreeze((0, Y, 0), (2, Y, 0))
        self.assertEqual(result.snowy_cleared, 3)
        self.assertEqual(self.state(0, Y, 0), "minecraft:grass_block[snowy=false]")
        self.assertEqual(self.state(1, Y, 0), "minecraft:podzol[snowy=false]")
        self.assertEqual(self.state(2, Y, 0), "minecraft:mycelium[snowy=false]")

    def test_unsnowing_keeps_every_other_property(self):
        """No vanilla top carries a second property today, so this state is
        invented. What it pins down is that the flag is flipped on the state
        that is standing there rather than a fresh one built from the name,
        which would silently drop whatever else it carried."""
        self.save.set_block(
            0, Y, 0, "minecraft:grass_block[snowy=true,waterlogged=true]"
        )
        self.save.unfreeze((0, Y, 0), (0, Y, 0))
        self.assertEqual(
            self.state(0, Y, 0),
            "minecraft:grass_block[snowy=false,waterlogged=true]",
        )

    def test_unsnowing_lands_on_the_palette_entry_already_there(self):
        """Both flags are in this section's palette, and the thaw must map
        onto the one that is rather than append a second copy of it."""
        self.save.set_block(0, Y, 0, "minecraft:grass_block[snowy=false]")
        self.save.set_block(1, Y, 0, "minecraft:grass_block[snowy=true]")
        section = self.save.dimension().blocks(0, 0).sections[Y >> 4]
        self.save.unfreeze((0, Y, 0), (1, Y, 0), keep_loaded=True)
        self.assertEqual(
            [str(state) for state in section.blocks.palette],
            ["minecraft:air", "minecraft:grass_block[snowy=false]"],
        )

    def test_ground_that_was_never_snowy_is_left_alone(self):
        self.save.set_block(0, Y, 0, "minecraft:grass_block[snowy=false]")
        result = self.save.unfreeze((0, Y, 0), (0, Y, 0))
        self.assertEqual(result.snowy_cleared, 0)
        self.assertEqual(result.chunks_modified, 0)

    def test_the_orphaned_ice_entry_leaves_the_palette(self):
        self.save.set_block(0, Y, 0, "minecraft:ice")
        section = self.save.dimension().blocks(0, 0).sections[Y >> 4]
        self.assertIn(BlockState("minecraft:ice"), section.blocks.palette)
        self.save.unfreeze((0, Y, 0), (0, Y, 0), keep_loaded=True)
        self.assertNotIn(BlockState("minecraft:ice"), section.blocks.palette)

    def test_the_thaw_survives_a_save_and_reload(self):
        self.save.fill((0, Y, 0), (3, Y, 3), "minecraft:ice")
        self.save.save()

        reopened = MinecraftSave.load(self.dir)
        result = reopened.unfreeze((0, Y, 0), (3, Y, 3))
        self.assertEqual(result.ice_melted, 16)
        self.assertGreaterEqual(reopened.save(), 1)

        fresh = MinecraftSave.load(self.dir)
        self.assertEqual(str(fresh.get_block(2, Y, 2)), "minecraft:water[level=0]")


class DeepSnowTests(UnfreezeBase):
    """Freezing lays one layer and stops -- ``tickPrecipitation`` deepens
    snow only to ``maxSnowAccumulationHeight``, registered in 26.2 as
    ``registerInteger("max_snow_accumulation_height", UPDATES, 1, 0, 8)``,
    and worldgen places ``Blocks.SNOW.defaultBlockState()``. So on a default
    world a deeper stack is a player's, and taking it would gut the builds
    this operation is narrow to protect."""

    DEEP = ("minecraft:snow[layers=2]", "minecraft:snow[layers=3]",
            "minecraft:snow[layers=8]")

    def place_deep(self):
        for i, block in enumerate(self.DEEP):
            self.save.set_block(i, Y, 0, block)

    def test_deeper_than_a_freeze_stays_and_is_counted(self):
        self.place_deep()
        result = self.save.unfreeze((0, Y, 0), (len(self.DEEP) - 1, Y, 0))
        self.assertEqual((result.snow_removed, result.deep_snow_kept), (0, 3))
        self.assertEqual(result.chunks_modified, 0)
        for i, block in enumerate(self.DEEP):
            self.assertEqual(self.state(i, Y, 0), block)

    def test_deep_snow_takes_every_layer_count(self):
        """For a world that raised the game rule, where drifts really are
        weather again."""
        self.place_deep()
        self.save.set_block(len(self.DEEP), Y, 0, "minecraft:snow[layers=1]")
        result = self.save.unfreeze(
            (0, Y, 0), (len(self.DEEP), Y, 0), deep_snow=True
        )
        self.assertEqual((result.snow_removed, result.deep_snow_kept), (4, 0))
        for i in range(len(self.DEEP) + 1):
            self.assertEqual(self.state(i, Y, 0), "minecraft:air")

    def test_what_was_kept_is_in_the_repr(self):
        """Silence would read exactly like there having been nothing there."""
        self.place_deep()
        result = self.save.unfreeze((0, Y, 0), (len(self.DEEP) - 1, Y, 0))
        self.assertIn("3 deep snow kept", repr(result))


class SnowyIsPositionalTests(UnfreezeBase):
    """``snowy`` belongs to the column, not the block: 26.2's
    ``SnowyBlock.isSnowySetting`` is ``state.is(BlockTags.SNOW)`` on the
    block above, and that tag is ``{snow, snow_block, powder_snow}``.
    Clearing the flag under something the thaw leaves standing writes a
    state vanilla never produces and never repairs -- ``updateShape`` fires
    only on a neighbor change -- so the ground renders dirt-sided forever."""

    def test_the_ordinary_case_still_clears_it(self):
        """The layer above goes in this same call, so nothing is left up
        there to derive the flag from."""
        self.save.set_block(0, Y, 0, "minecraft:grass_block[snowy=true]")
        self.save.set_block(0, Y + 1, 0, "minecraft:snow[layers=1]")
        result = self.save.unfreeze((0, Y, 0), (0, Y + 1, 0))
        self.assertEqual((result.snow_removed, result.snowy_cleared), (1, 1))
        self.assertEqual(self.state(0, Y, 0), "minecraft:grass_block[snowy=false]")

    def test_a_surviving_snow_block_keeps_the_flag(self):
        """The headline promise is that snow_blocks are not touched. Unsnowing
        the ground under one contradicts it in the same call."""
        self.save.set_block(0, Y, 0, "minecraft:grass_block[snowy=true]")
        self.save.set_block(0, Y + 1, 0, "minecraft:snow_block")
        result = self.save.unfreeze((0, Y, 0), (0, Y + 1, 0))
        self.assertEqual(result.snowy_cleared, 0)
        self.assertEqual(result.chunks_modified, 0)
        self.assertEqual(self.state(0, Y, 0), "minecraft:grass_block[snowy=true]")
        self.assertEqual(self.state(0, Y + 1, 0), "minecraft:snow_block")

    def test_powder_snow_keeps_it_too(self):
        """The third member of the tag, and the one a name-matched guess at
        the rule would miss."""
        self.save.set_block(0, Y, 0, "minecraft:podzol[snowy=true]")
        self.save.set_block(0, Y + 1, 0, "minecraft:powder_snow")
        result = self.save.unfreeze((0, Y, 0), (0, Y + 1, 0))
        self.assertEqual(result.snowy_cleared, 0)
        self.assertEqual(self.state(0, Y, 0), "minecraft:podzol[snowy=true]")

    def test_snow_above_the_box_keeps_the_flag(self):
        """A ground row thawed by itself: the layer one block up is outside
        the box and still standing, so the flag it derives is still true."""
        self.save.set_block(0, Y, 0, "minecraft:mycelium[snowy=true]")
        self.save.set_block(0, Y + 1, 0, "minecraft:snow[layers=1]")
        result = self.save.unfreeze((0, Y, 0), (0, Y, 0))
        self.assertEqual((result.snow_removed, result.snowy_cleared), (0, 0))
        self.assertEqual(self.state(0, Y, 0), "minecraft:mycelium[snowy=true]")
        self.assertEqual(self.state(0, Y + 1, 0), "minecraft:snow[layers=1]")

    def test_deep_snow_kept_keeps_the_flag_with_it(self):
        """The two defaults meet here: the drift stays, so the ground under
        it must stay snowy or the pair is a state the game never wrote."""
        self.save.set_block(0, Y, 0, "minecraft:grass_block[snowy=true]")
        self.save.set_block(0, Y + 1, 0, "minecraft:snow[layers=3]")
        result = self.save.unfreeze((0, Y, 0), (0, Y + 1, 0))
        self.assertEqual((result.deep_snow_kept, result.snowy_cleared), (1, 0))
        self.assertEqual(self.state(0, Y, 0), "minecraft:grass_block[snowy=true]")

    def test_the_layer_above_may_be_in_the_next_section(self):
        """The plan is built a section at a time, and the ground at the top
        of one section is covered by snow in the next. Which section the
        sweep reaches first must not decide the answer."""
        self.save.set_block(0, 63, 0, "minecraft:grass_block[snowy=true]")
        self.save.set_block(0, 64, 0, "minecraft:snow[layers=1]")
        self.save.set_block(2, 63, 0, "minecraft:grass_block[snowy=true]")
        self.save.set_block(2, 64, 0, "minecraft:snow_block")
        result = self.save.unfreeze((0, 60, 0), (2, 70, 0))
        self.assertEqual((result.snow_removed, result.snowy_cleared), (1, 1))
        self.assertEqual(self.state(0, 63, 0), "minecraft:grass_block[snowy=false]")
        self.assertEqual(self.state(2, 63, 0), "minecraft:grass_block[snowy=true]")

    def test_nothing_at_all_above_clears_it(self):
        """Air, and the top of the world above that: neither is in the tag."""
        self.save.set_block(0, Y, 0, "minecraft:grass_block[snowy=true]")
        result = self.save.unfreeze((0, Y, 0), (0, Y, 0))
        self.assertEqual(result.snowy_cleared, 1)
        self.assertEqual(self.state(0, Y, 0), "minecraft:grass_block[snowy=false]")


class WinterThatIsNotWeatherTests(UnfreezeBase):
    """The whole point of the operation: eating these would gut snowy
    hillsides and real builds. Most of them freezing never writes at all,
    the cauldron being the exception -- ``tickPrecipitation`` calls
    ``Block.handlePrecipitation`` and ``CauldronBlock.handlePrecipitation``
    fills one with powder snow -- and it is excluded by choice, because a
    container's level is not ground covering."""

    SURVIVORS = (
        "minecraft:snow_block",
        "minecraft:powder_snow",
        "minecraft:packed_ice",
        "minecraft:blue_ice",
        "minecraft:frosted_ice[age=2]",
        "minecraft:powder_snow_cauldron[level=2]",
    )

    def place_survivors(self, save):
        for i, block in enumerate(self.SURVIVORS):
            save.set_block(i, Y, 0, block)

    def test_none_of_them_are_touched(self):
        self.place_survivors(self.save)
        result = self.save.unfreeze(
            (0, Y - 1, 0), (len(self.SURVIVORS), Y + 1, 0)
        )
        self.assertEqual(
            (result.snow_removed, result.ice_melted, result.snowy_cleared),
            (0, 0, 0),
        )
        self.assertEqual(result.chunks_modified, 0)
        for i, block in enumerate(self.SURVIVORS):
            self.assertEqual(self.state(i, Y, 0), block)

    def test_a_thaw_with_nothing_to_do_writes_no_files(self):
        self.place_survivors(self.save)
        self.assertGreaterEqual(self.save.save(), 1)

        reopened = MinecraftSave.load(self.dir)
        reopened.unfreeze((0, Y - 1, 0), (len(self.SURVIVORS), Y + 1, 0))
        self.assertEqual(
            reopened.save(), 0, "a thaw that changed nothing must rewrite nothing"
        )


class FloatingWaterTests(UnfreezeBase):
    def test_ice_over_ground_is_not_floating(self):
        self.save.set_block(0, Y - 1, 0, "minecraft:stone")
        self.save.set_block(0, Y, 0, "minecraft:ice")
        result = self.save.unfreeze((0, Y, 0), (0, Y, 0))
        self.assertEqual((result.ice_melted, result.floating_water), (1, 0))

    def test_ice_over_air_below_the_box_is_reported(self):
        """A drained frozen lake: the lid is in the box and the air under it
        is not, so a check that only looked inside would miss every one."""
        self.save.set_block(0, Y, 0, "minecraft:ice")
        result = self.save.unfreeze((0, Y, 0), (0, Y, 0))
        self.assertEqual((result.ice_melted, result.floating_water), (1, 1))
        # A report, never a refusal -- the water is written either way.
        self.assertEqual(self.state(0, Y, 0), "minecraft:water[level=0]")

    def test_only_melted_ice_is_counted(self):
        """Snow over air is ordinary and nobody needs telling about it."""
        self.save.set_block(0, Y, 0, "minecraft:snow[layers=1]")
        self.save.set_block(1, Y, 0, "minecraft:grass_block[snowy=true]")
        result = self.save.unfreeze((0, Y, 0), (1, Y, 0))
        self.assertEqual(result.floating_water, 0)
        self.assertEqual((result.snow_removed, result.snowy_cleared), (1, 1))

    def test_snow_under_a_lid_does_not_change_the_answer(self):
        """Both lids sit on snow this same call removes. Which section the
        sweep reaches first must not decide whether the water is floating --
        one of these crosses a section line and the other does not."""
        self.save.set_block(0, 63, 0, "minecraft:snow[layers=1]")
        self.save.set_block(0, 64, 0, "minecraft:ice")
        self.save.set_block(2, 69, 0, "minecraft:snow[layers=1]")
        self.save.set_block(2, 70, 0, "minecraft:ice")
        result = self.save.unfreeze((0, 60, 0), (2, 80, 0))
        self.assertEqual((result.ice_melted, result.snow_removed), (2, 2))
        self.assertEqual(
            result.floating_water, 0, "both lids sat on snow when the call began"
        )


class BoxTests(UnfreezeBase):
    def test_a_box_crossing_chunk_and_section_boundaries(self):
        # x 12..19 crosses the chunk line at 16, z -2..5 the one at 0, and
        # y 60..67 the section line at 64.
        self.save.fill((12, 60, -2), (19, 67, 5), "minecraft:snow[layers=1]")
        result = self.save.unfreeze((12, 60, -2), (19, 67, 5))
        self.assertEqual(result.snow_removed, 8 * 8 * 8)
        self.assertEqual(result.chunks_modified, 4)
        self.assertTrue(result.complete)
        left = self.save.find_blocks((12, 60, -2), (19, 67, 5), "minecraft:snow")
        self.assertEqual(len(left), 0)

    def test_snow_outside_the_box_survives(self):
        self.save.fill((0, Y, 0), (4, Y, 0), "minecraft:snow[layers=1]")
        result = self.save.unfreeze((1, Y, 0), (3, Y, 0))
        self.assertEqual(result.snow_removed, 3)
        self.assertEqual(self.state(0, Y, 0), "minecraft:snow[layers=1]")
        self.assertEqual(self.state(4, Y, 0), "minecraft:snow[layers=1]")

    def test_corners_may_be_given_in_any_order(self):
        self.save.fill((0, Y, 0), (3, Y + 3, 3), "minecraft:ice")
        result = self.save.unfreeze((3, Y + 3, 3), (0, Y, 0))
        self.assertEqual(result.ice_melted, 4 * 4 * 4)

    def test_ungenerated_chunks_are_reported_not_created(self):
        result = self.save.unfreeze((0, Y, 0), (200, Y, 0))
        self.assertFalse(result.complete)
        self.assertIn((12, 0), result.missing_chunks)
        self.assertIsNone(
            self.save.dimension().chunk(12, 0), "a thaw must never invent terrain"
        )

    def test_a_chunk_too_old_to_read_is_reported_not_skipped_silently(self):
        """"Cannot see it" and "nothing there" have to stay distinguishable:
        the snow in an unreadable chunk is still standing."""
        self.save.dimension().chunk(0, 0).data["DataVersion"] = TagInt(100)
        with self.assertRaises(NBTError):
            self.save.dimension().blocks(0, 0)

        result = self.save.unfreeze((0, Y, 0), (15, Y, 15))
        self.assertFalse(result.complete)
        self.assertEqual(result.missing_chunks, [])
        self.assertEqual([where for where, _ in result.unreadable_chunks], [(0, 0)])

    def test_a_box_reaching_past_the_world_height_is_still_complete(self):
        """Unlike a fill, there is nothing to fail at above the top section:
        what a chunk does not store is air, and air never froze."""
        self.save.set_block(0, Y, 0, "minecraft:snow[layers=1]")
        result = self.save.unfreeze((0, -128, 0), (0, 400, 0))
        self.assertEqual(result.snow_removed, 1)
        self.assertTrue(result.complete)


if __name__ == "__main__":
    unittest.main(verbosity=2)
