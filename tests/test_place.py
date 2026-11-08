"""Tests for place_block: item-placement semantics over set_block."""

from __future__ import annotations

import shutil
import tempfile
import unittest

from heaton_voxel.world import MinecraftSave
from tests.test_world import make_save

CHUNKS = [(x, z) for x in range(-1, 2) for z in range(-1, 2)]
Y = 70


class PlaceBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="place-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, CHUNKS)
        self.save = MinecraftSave.load(self.dir)

    def state(self, x, y, z):
        return self.save.get_block(x, y, z)


class MultiBlockTests(PlaceBase):
    def test_a_bed_is_two_blocks(self):
        self.assertTrue(
            self.save.place_block(0, Y, 0, "minecraft:red_bed[facing=south]")
        )
        foot, head = self.state(0, Y, 0), self.state(0, Y, 1)
        self.assertEqual(foot.properties.get("part"), "foot")
        self.assertEqual(head.properties.get("part"), "head")
        self.assertEqual(head.name, "minecraft:red_bed")
        self.assertEqual(head.properties.get("facing"), "south")

    def test_a_head_half_places_its_foot_behind(self):
        self.save.place_block(0, Y, 0, "minecraft:cyan_bed[facing=east,part=head]")
        self.assertEqual(self.state(0, Y, 0).properties.get("part"), "head")
        self.assertEqual(self.state(-1, Y, 0).properties.get("part"), "foot")

    def test_a_door_is_two_blocks(self):
        self.save.place_block(
            0, Y, 0, "minecraft:oak_door[facing=west,hinge=right]"
        )
        lower, upper = self.state(0, Y, 0), self.state(0, Y + 1, 0)
        self.assertEqual(lower.properties.get("half"), "lower")
        self.assertEqual(upper.properties.get("half"), "upper")
        self.assertEqual(upper.properties.get("hinge"), "right")

    def test_tall_plants_get_their_upper_half(self):
        self.save.place_block(0, Y, 0, "minecraft:lilac")
        self.assertEqual(self.state(0, Y, 0).properties.get("half"), "lower")
        self.assertEqual(self.state(0, Y + 1, 0).properties.get("half"), "upper")

    def test_every_double_plant_the_game_places_whole_gets_its_upper_half(self):
        # DoublePlantBlock.setPlacedBy sets the upper half; the small
        # dripleaf's carries the lower's facing and takes its own water
        # (copyWaterloggedFrom), so flowing water under air is wet below
        # and dry above. A lone lower half breaks on the next update.
        self.save.set_block(2, Y, 0, "minecraft:water[level=3]")
        self.save.place_block(0, Y, 0, "minecraft:pitcher_plant")
        self.save.place_block(1, Y, 0, "minecraft:tall_seagrass")
        self.save.place_block(2, Y, 0, "minecraft:small_dripleaf[facing=east]")
        self.assertEqual(
            [str(self.state(x, y, 0)) for x in range(3) for y in (Y, Y + 1)],
            ["minecraft:pitcher_plant[half=lower]",
             "minecraft:pitcher_plant[half=upper]",
             "minecraft:tall_seagrass[half=lower]",
             "minecraft:tall_seagrass[half=upper]",
             "minecraft:small_dripleaf[facing=east,half=lower,waterlogged=true]",
             "minecraft:small_dripleaf[facing=east,half=upper]"])
        # The pitcher pod plants one block; the crop grows its upper half.
        self.save.place_block(3, Y, 0, "minecraft:pitcher_crop")
        self.assertEqual(self.state(3, Y + 1, 0).name, "minecraft:air")

    def test_the_tall_plants_are_the_games_two_block_plants(self):
        from heaton_voxel.block_defaults import BLOCK_DEFAULTS
        from heaton_voxel.place import _TALL_PLANTS

        halves = {name for name, props in BLOCK_DEFAULTS.items()
                  if props.get("half") == "lower" and not name.endswith("_door")}
        self.assertEqual(_TALL_PLANTS, halves - {"minecraft:pitcher_crop"})

    def test_refused_atomically_at_the_world_edge(self):
        # head would land in an ungenerated chunk: nothing may be written
        self.assertFalse(
            self.save.place_block(31, Y, 0, "minecraft:red_bed[facing=east]")
        )
        self.assertEqual(self.state(31, Y, 0), "minecraft:air")
        # a door poking out of the world's top is refused the same way
        self.assertFalse(self.save.place_block(0, 319, 0, "minecraft:oak_door"))
        self.assertEqual(self.state(0, 319, 0), "minecraft:air")


class DerivedStateTests(PlaceBase):
    def test_placing_a_fence_updates_both_sides(self):
        self.save.set_block(0, Y, 0, "minecraft:oak_fence")  # bare, stale
        self.save.place_block(1, Y, 0, "minecraft:oak_fence")
        placed = self.state(1, Y, 0).properties
        neighbor = self.state(0, Y, 0).properties
        self.assertEqual(placed.get("west"), "true")
        self.assertEqual(neighbor.get("east"), "true",
                         "the existing neighbor is updated too")

    def test_placing_next_to_a_stair_reshapes_it(self):
        self.save.set_block(0, Y, 0, "minecraft:oak_stairs[facing=south]")
        self.save.place_block(0, Y, 1, "minecraft:oak_stairs[facing=east]")
        self.assertEqual(self.state(0, Y, 0).properties.get("shape"), "outer_left")

    def test_settling_order_is_fixed_not_set_order(self):
        """The re-derivation sweep must not depend on set iteration.

        A cell's state enters the section palette the first time it is
        written, so the sweep's order is the palette's order -- and the
        palette's order is the chunk's bytes. Ordering only by height leaves
        cells at the same height in set order, which made an identical build
        encode differently. Found by the C# port, whose byte digests
        disagreed while every state matched.
        """
        self.save.set_block(1, Y, 1, "minecraft:oak_fence")
        # One placement whose sweep touches the fence to the south and the
        # stair itself, both at Y: south (z=1) must settle after (x, z) order
        # puts the stair's own cell (z=0) first.
        self.save.place_block(1, Y, 0, "minecraft:oak_stairs[facing=south]")

        section = self.save.dimension().blocks(0, 0).sections[Y >> 4]
        order = [str(entry) for entry in section.blocks.palette]
        stair = order.index("minecraft:oak_stairs[facing=south,shape=straight]")
        fence = order.index(
            "minecraft:oak_fence[east=false,north=true,south=false,west=false]")
        self.assertLess(stair, fence,
                        "the placed cell settles before the neighbor south "
                        f"of it; palette was {order}")

    def test_a_block_on_a_wall_raises_its_post(self):
        for x in (0, 1, 2):
            self.save.place_block(x, Y, 0, "minecraft:cobblestone_wall")
        self.assertEqual(self.state(1, Y, 0).properties.get("up"), "false")
        self.save.place_block(1, Y + 1, 0, "minecraft:lantern[hanging=false]")
        self.assertEqual(self.state(1, Y, 0).properties.get("up"), "true",
                         "the wall below the lantern re-derives its post")


class WaterloggingTests(PlaceBase):
    def test_placing_into_water_waterlogs_when_possible(self):
        self.save.set_block(0, Y, 0, "minecraft:water")
        self.save.set_block(1, Y, 0, "minecraft:water")
        self.save.place_block(0, Y, 0, "minecraft:spruce_fence")
        self.assertEqual(
            self.state(0, Y, 0).properties.get("waterlogged"), "true"
        )
        # dirt cannot hold water: it just displaces it
        self.save.place_block(1, Y, 0, "minecraft:dirt")
        self.assertEqual(self.state(1, Y, 0), "minecraft:dirt")

    def test_an_explicit_waterlogged_value_is_respected(self):
        self.save.set_block(0, Y, 0, "minecraft:water")
        self.save.place_block(0, Y, 0, "minecraft:oak_fence[waterlogged=false]")
        self.assertEqual(
            self.state(0, Y, 0).properties.get("waterlogged"), "false"
        )

    def test_gates_never_waterlog(self):
        self.save.set_block(0, Y, 0, "minecraft:water")
        self.save.place_block(0, Y, 0, "minecraft:oak_fence_gate[facing=north]")
        self.assertNotEqual(
            self.state(0, Y, 0).properties.get("waterlogged"), "true"
        )

    def test_a_chain_holds_water_and_a_bell_does_not(self):
        # The family list this replaced had these two exactly backwards.
        self.save.set_block(0, Y, 0, "minecraft:water")
        self.save.set_block(1, Y, 0, "minecraft:water")
        self.save.place_block(0, Y, 0, "minecraft:iron_chain[axis=y]")
        self.save.place_block(1, Y, 0, "minecraft:bell")
        self.assertEqual(
            self.state(0, Y, 0).properties.get("waterlogged"), "true")
        self.assertNotIn("waterlogged", self.state(1, Y, 0).properties)

    def test_what_waterlogs_is_what_the_game_gives_the_property(self):
        # fixtures/block_shapes.json records, from the game itself, every
        # block whose states carry `waterlogged`.
        from fixtures import block_shapes
        from heaton_voxel.place import _waterloggable

        game = {name for name, props, _ in block_shapes()
                if "waterlogged" in props}
        names = {name for name, _, _ in block_shapes()}
        self.assertEqual(sorted(n for n in names if _waterloggable(n) != (n in game)), [])


class WaterSourceTests(PlaceBase):
    """A placed block waterlogs only where the game's own placement would.

    Nearly every waterloggable block's ``getStateForPlacement`` sets
    ``waterlogged`` to ``fluidState.is(Fluids.WATER)`` (26.3, javap): the
    source, and not ``Fluids.FLOWING_WATER``, which ``LiquidBlock`` makes of
    every level from 1 up. Waterlogging a stair dropped into a stream wrote
    a new source into flowing water.
    """

    STAIR = "minecraft:oak_stairs[facing=north]"

    def wet(self, x, y, z):
        return self.state(x, y, z).properties.get("waterlogged")

    def test_only_a_source_waterlogs_a_stair(self):
        # The legacy codec stores a bare id as it is given, so (0, Y, 0)
        # reads back as plain minecraft:water: level 0, its default.
        self.save.set_block(0, Y, 0, "minecraft:water")
        for level in range(16):
            self.save.set_block(level + 1, Y, 0, f"minecraft:water[level={level}]")
        for x in range(17):
            self.save.place_block(x, Y, 0, self.STAIR)
        self.assertEqual(
            [self.wet(x, Y, 0) for x in range(17)],
            ["true", "true"] + [None] * 15,
            "the bare source and level 0 waterlog; flowing water (1-7) and "
            "falling water (8-15) are displaced")

    def test_a_block_that_holds_a_source_is_water_to_what_replaces_it(self):
        sources = ["minecraft:seagrass", "minecraft:tall_seagrass[half=lower]",
                   "minecraft:kelp[age=3]", "minecraft:kelp_plant",
                   "minecraft:bubble_column[drag=false]",
                   "minecraft:oak_slab[type=bottom,waterlogged=true]",
                   "minecraft:sea_pickle",          # waterlogged by default
                   "minecraft:sea_pickle[pickles=2,waterlogged=false]"]
        for x, state in enumerate(sources):
            self.save.set_block(x, Y, 0, state)
            self.save.place_block(x, Y, 0, "minecraft:oak_fence")
        self.assertEqual([self.wet(x, Y, 0) for x in range(len(sources))],
                         ["true"] * 7 + [None])

    def test_corals_and_the_conduit_take_falling_water_too(self):
        # is(FluidTags.WATER) && isFull(): amount 8, the source's and the
        # falling water's. Both are wet by default, so dry is written out.
        cases = [("minecraft:water[level=8]", "minecraft:tube_coral_fan", "true"),
                 ("minecraft:water[level=15]", "minecraft:conduit", "true"),
                 ("minecraft:water[level=0]", "minecraft:dead_horn_coral", "true"),
                 ("minecraft:water[level=3]", "minecraft:brain_coral", "false"),
                 ("minecraft:water[level=1]", "minecraft:conduit", "false")]
        for x, (water, block, _) in enumerate(cases):
            self.save.set_block(x, Y, 0, water)
            self.save.place_block(x, Y, 0, block)
        self.assertEqual([self.wet(x, Y, 0) for x in range(len(cases))],
                         [wet for _, _, wet in cases])

    def test_a_small_dripleaf_takes_any_water_and_a_light_block_none(self):
        # copyWaterloggedFrom asks isWaterAt, the water tag; the light block
        # has no getStateForPlacement and is placed in its default state.
        cells = [("minecraft:water[level=3]", "minecraft:small_dripleaf", "true"),
                 ("minecraft:seagrass", "minecraft:small_dripleaf", "true"),
                 ("minecraft:water[level=0]", "minecraft:light[level=15]", None),
                 ("minecraft:seagrass", "minecraft:light[level=15]", None)]
        for x, (water, block, _) in enumerate(cells):
            self.save.set_block(x, Y, 0, water)
            self.save.place_block(x, Y, 0, block)
        self.assertEqual([self.wet(x, Y, 0) for x in range(len(cells))],
                         [wet for _, _, wet in cells])

    def test_a_small_dripleaf_upper_half_takes_its_own_cells_water(self):
        """Each half of a two-cell placement asks the water in its own cell,
        and every cell is read before any is written: a dripleaf in water
        two deep is wet top and bottom, and one whose upper cell holds
        water the game does not have is refused with its lower cell, a
        source, left as it was -- no lone lower half."""
        from heaton_voxel.nbt import NBTError

        self.save.set_block(0, Y, 0, "minecraft:water[level=0]")
        self.save.set_block(0, Y + 1, 0, "minecraft:water[level=0]")
        self.save.place_block(0, Y, 0, "minecraft:small_dripleaf")
        self.assertEqual((self.wet(0, Y, 0), self.wet(0, Y + 1, 0)),
                         ("true", "true"))
        self.assertEqual(self.state(0, Y + 1, 0).properties["half"], "upper")
        self.save.set_block(2, Y, 0, "minecraft:water[level=0]")
        self.save.set_block(2, Y + 1, 0, "minecraft:water[level=16]")
        with self.assertRaises(NBTError):
            self.save.place_block(2, Y, 0, "minecraft:small_dripleaf")
        self.assertEqual(str(self.state(2, Y, 0)), "minecraft:water[level=0]")
        self.assertEqual(str(self.state(2, Y + 1, 0)), "minecraft:water[level=16]")

    def test_a_double_slab_and_a_campfire_are_placed_as_the_game_places_them(self):
        # SlabBlock's double branch sets waterlogged false without asking
        # the water; CampfireBlock sets lit to the opposite of the
        # waterlogged it sets. A block whose rule takes no water never
        # reads it, so water the game does not have is no concern of its.
        cases = [
            ("minecraft:water", "minecraft:oak_slab[type=double]",
             "minecraft:oak_slab[type=double]"),
            ("minecraft:water", "minecraft:campfire[facing=east]",
             "minecraft:campfire[facing=east,lit=false,waterlogged=true]"),
            ("minecraft:seagrass", "minecraft:soul_campfire",
             "minecraft:soul_campfire[lit=false,waterlogged=true]"),
            ("minecraft:water", "minecraft:campfire[lit=true]",
             "minecraft:campfire[lit=true,waterlogged=true]"),
            ("minecraft:water[level=3]", "minecraft:campfire",
             "minecraft:campfire"),
            ("minecraft:water[level=16]", "minecraft:stone_slab[type=double]",
             "minecraft:stone_slab[type=double]"),
            ("minecraft:water[level=16]", "minecraft:light",
             "minecraft:light"),
        ]
        for x, (water, block, _) in enumerate(cases):
            self.save.set_block(x, Y, 0, water)
            self.assertTrue(self.save.place_block(x, Y, 0, block), block)
        self.assertEqual([str(self.state(x, Y, 0)) for x in range(len(cases))],
                         [placed for _, _, placed in cases])

    def test_a_block_wet_by_default_is_placed_dry_out_of_water(self):
        # A bare sea pickle reads as waterlogged; the game places one dry
        # into air, and a wet one would be a new source standing in the air.
        for x, block in enumerate(("minecraft:sea_pickle", "minecraft:conduit",
                                   "minecraft:fire_coral_wall_fan[facing=east]")):
            self.save.place_block(x, Y, 0, block)
            self.assertEqual(self.wet(x, Y, 0), "false", block)
        self.save.place_block(5, Y, 0, "minecraft:sea_pickle[waterlogged=true]")
        self.assertEqual(self.wet(5, Y, 0), "true", "said outright, it stands")

    def test_water_the_game_does_not_have_refuses_the_placement(self):
        from heaton_voxel.nbt import NBTError

        self.save.set_block(0, Y, 0, "minecraft:water[level=16]")
        self.save.set_block(1, Y, 0, "minecraft:oak_slab[waterlogged=yes]")
        for x in (0, 1):
            before = self.state(x, Y, 0)
            with self.assertRaises(NBTError) as caught:
                self.save.place_block(x, Y, 0, self.STAIR)
            self.assertIn(f"({x}, {Y}, 0)", str(caught.exception))
            self.assertEqual(self.state(x, Y, 0), before, "nothing written")
        # A block that holds no water never asks.
        self.assertTrue(self.save.place_block(0, Y, 0, "minecraft:stone"))

    def test_the_water_tables_are_the_games(self):
        from fixtures import block_shapes
        from heaton_voxel.block_defaults import BLOCK_DEFAULTS
        from heaton_voxel.place import (
            _CORALS, _TAKES_WATER, _WATER_SOURCE_BLOCKS, _waterloggable)

        # Every block whose every state holds fluid, bar the two liquids,
        # is water whatever its state.
        fluid, dry = set(), set()
        for name, props, shape in block_shapes():
            (fluid if shape.fluid and "waterlogged" not in props else dry).add(name)
        self.assertEqual(_WATER_SOURCE_BLOCKS,
                         fluid - dry - {"minecraft:water", "minecraft:lava"})
        self.assertEqual(_CORALS, {
            name for name in BLOCK_DEFAULTS
            if name.endswith(("_coral", "_coral_fan", "_coral_wall_fan"))})
        self.assertEqual(sorted(n for n in _TAKES_WATER if not _waterloggable(n)),
                         [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
