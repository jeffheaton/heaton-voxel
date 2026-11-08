"""Builder.stamp: a schematic pasted into a build, checked and journaled as
every Builder write is.

A stamp is written by the lower tier's paste, so it must encode exactly
what ``save.import_schem`` encodes, in every one of the eight orientations
a flip and a turn make -- and before that paste runs, it must pass every
check a Builder write passes: legality, the chunks and heights it reaches,
the worksite's guard, and two refusals of its own, a state its turn cannot
turn and a block entity or an entity crossing a DataVersion.
"""

from __future__ import annotations

import os
import struct
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from heaton_voxel.blocks import AIR_BLOCKS, BlockState  # noqa: E402
from heaton_voxel.craft import Frame  # noqa: E402
from heaton_voxel.craft.build import Builder  # noqa: E402
from heaton_voxel.craft.errors import IllegalBlock, Refused  # noqa: E402
from heaton_voxel.craft.geometry import turn  # noqa: E402
from heaton_voxel.nbt import (  # noqa: E402
    TAG_COMPOUND,
    TAG_DOUBLE,
    NBTError,
    TagCompound,
    TagDouble,
    TagInt,
    TagIntArray,
    TagList,
    TagString,
)
from heaton_voxel.schem import Schematic, SchemImportResult  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402
from tests.test_style import MixWriteBase  # noqa: E402
from tests.test_world import DATA_VERSION  # noqa: E402
from tests.test_worksite import (  # noqa: E402
    ENTITIES_DIR,
    SyntheticSiteCase,
)

STONE = "minecraft:stone"
STAIRS = "minecraft:oak_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]"
FENCE = ("minecraft:oak_fence[east=false,north=false,south=false,west=false,"
         "waterlogged=false]")
CHEST = "minecraft:chest[facing=east,type=single,waterlogged=false]"

#: Every way a stamp can be laid: four turns, each unmirrored and mirrored
#: along either axis -- the eight orientations, the z mirror being the x
#: mirror a half turn on.
ORIENTATIONS = [(rotate, mirror) for rotate in (0, 90, 180, 270)
                for mirror in (None, "x", "z")]


def stand(identity: str, pos, uuid=(1, 2, 3, 4)) -> TagCompound:
    """An entity as a schematic holds one, ``pos`` from its minimum corner."""
    return TagCompound({
        "id": TagString(identity),
        "Pos": TagList([TagDouble(float(v)) for v in pos],
                       element_type=TAG_DOUBLE),
        "UUID": TagIntArray(list(uuid)),
    })


def lodge(data_version: int = DATA_VERSION) -> Schematic:
    """A small build with something to turn in every cell that matters: a
    stair, a fence, a chest and its block entity, a bed and a door (both
    halves), a block, and an armor stand -- with an origin off its corner."""
    sch = Schematic(4, 3, 5, data_version, offset=(-1, 0, -2))
    sch.set_block(0, 0, 0, STAIRS)
    sch.set_block(1, 0, 0, FENCE)
    sch.set_block(2, 0, 1, CHEST)
    sch.set_block_entity(2, 0, 1, TagCompound({
        "id": TagString("minecraft:chest"),
        "CustomName": TagString("larder")}))
    sch.set_block(3, 0, 4, "minecraft:red_bed[facing=south,occupied=false,part=foot]")
    sch.set_block(3, 0, 3, "minecraft:stone_bricks")
    sch.set_block(0, 1, 2, "minecraft:oak_door[facing=east,half=lower,hinge=left,"
                           "open=false,powered=false]")
    sch.set_block(0, 2, 2, "minecraft:oak_door[facing=east,half=upper,hinge=left,"
                           "open=false,powered=false]")
    sch.entities.append(stand("minecraft:armor_stand", (1.5, 1.0, 3.5)))
    return sch


def frame_of(at, rotate: int, mirror) -> Frame:
    """The frame a stamp's documentation says it lands in."""
    quarters = rotate // 90 + (2 if mirror == "z" else 0)
    return Frame(at, turn("south", quarters), mirrored=mirror is not None)


class StampBase(MixWriteBase):
    AT = (14, 64, 15)            # the lodge spans chunks (0, 0) and its neighbors

    def entities(self, save) -> list:
        """Every entity in the world, by id and position: everything but
        the identity, which every paste gives afresh."""
        found = []
        for (chunk_x, chunk_z) in [(x, z) for x in range(-1, 3)
                                   for z in range(-1, 3)]:
            chunk = save.dimension().entity_chunk(chunk_x, chunk_z)
            if chunk is None or chunk.data is None:
                continue
            for entity in chunk.data.get("Entities", []):
                found.append((str(entity["id"]),
                              tuple(float(v) for v in entity["Pos"])))
        return sorted(found)

    def uuids(self, save) -> list:
        """Every entity's UUID in the world, as four ints."""
        return [tuple(int(v) for v in entity["UUID"])
                for chunk_x in range(-1, 3) for chunk_z in range(-1, 3)
                for chunk in [save.dimension().entity_chunk(chunk_x, chunk_z)]
                if chunk is not None and chunk.data is not None
                for entity in chunk.data.get("Entities", [])]

    def assertUntouched(self, directory, save, b, drawn=None, before=None):
        """Nothing journaled, spawned or drawn, and -- saved -- the very
        bytes the world held before the refused call: ``before``, or a
        fresh copy's."""
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(b.entities_added, 0)
        if drawn is not None:
            self.assertEqual(b.rng.getstate(), drawn, "nothing drawn")
        save.save()
        if before is None:
            fresh, _ = self.world()
            before = self.regions(fresh)
        after = self.regions(directory)
        self.assertEqual(sorted(after), sorted(before))
        for name in after:
            self.assertTrue(after[name] == before[name],
                            f"nothing was written: {name} differs")
        self.assertEqual(self.entities(save), [])


class StampTests(StampBase):
    def test_a_stamp_is_import_schem_byte_for_byte(self):
        """In all eight orientations -- every turn, unmirrored and mirrored
        both ways -- and with the air masked, a stamp saves the very
        terrain bytes save.import_schem saves, puts the same entities in
        the same places, and reports what that paste reports -- the
        section palette pruned of a block it paves over, as that paste
        prunes it."""
        sch = lodge()
        cases = [(rotate, mirror, True) for rotate, mirror in ORIENTATIONS]
        cases += [(90, "x", False), (180, None, False)]
        gold = "minecraft:gold_block"
        for rotate, mirror, paste_air in cases:
            with self.subTest(rotate=rotate, mirror=mirror, paste_air=paste_air):
                stamped, save = self.world()
                # The only gold in its section, on the origin's cell -- air
                # in the lodge, so a paste with the air overwrites it, and
                # the section's palette shows whether the paste prunes.
                save.set_block(*self.AT, gold)
                b = Builder(save)
                result = b.stamp(sch, self.AT, rotate=rotate, mirror=mirror,
                                 paste_air=paste_air)
                self.assertIsInstance(result, SchemImportResult)
                self.assertEqual(save.get_block(*self.AT).name,
                                 "minecraft:air" if paste_air else gold)
                save.save()
                pasted, other = self.world()
                other.set_block(*self.AT, gold)
                theirs = other.import_schem(
                    sch, self.AT, rotate=rotate, flip=mirror,
                    paste_air=paste_air, relight=False, keep_loaded=True)
                other.save()
                self.assertSameBytes(stamped, pasted, "stamp against import_schem")
                self.assertEqual(self.entities(MinecraftSave.load(stamped)),
                                 self.entities(MinecraftSave.load(pasted)))
                self.assertNotIn((1, 2, 3, 4), self.uuids(MinecraftSave.load(stamped)),
                                 "a stamped entity gets a fresh identity")
                for field in ("placed_box", "blocks_pasted", "air_skipped",
                              "block_entities_added", "entities_added"):
                    self.assertEqual(getattr(result, field),
                                     getattr(theirs, field), field)
                self.assertEqual(b.blocks_set, result.blocks_pasted)
                self.assertEqual(b.entities_added, 1)
        self.assertEqual(str(sch.get_block(0, 0, 0)), str(BlockState.parse(STAIRS)),
                         "the caller's schematic is never turned")

    def test_a_stamp_from_a_file_is_the_file_pasted(self):
        """A path is loaded as Schematic.load reads it -- the palette in
        the file's order, which is the bytes' -- so stamping one is
        stamping what it loads and pasting the file itself."""
        from_file, save = self.world()
        path = os.path.join(self._scratch, "lodge.schem")
        lodge().save(path)
        Builder(save).stamp(path, self.AT, rotate=90)
        save.save()
        loaded, other = self.world()
        Builder(other).stamp(Schematic.load(path), self.AT, rotate=90)
        other.save()
        self.assertSameBytes(from_file, loaded, "a path is what it loads")
        pasted, third = self.world()
        third.import_schem(path, self.AT, rotate=90, relight=False,
                           keep_loaded=True)
        third.save()
        self.assertSameBytes(from_file, pasted, "stamp against import_schem")

    def test_a_stamp_lands_where_its_frame_puts_it(self):
        """Each of the schematic's cells, counted from its origin, lands at
        the documented frame's cell and is that frame's state: the frame
        and the stamp are one map."""
        at = (20, 64, 20)
        sch = Schematic(3, 2, 4, DATA_VERSION, offset=(-2, 0, -1))
        cells = {(0, 0, 0): STAIRS,
                 (2, 1, 3): "minecraft:oak_stairs[facing=east,half=top,"
                            "shape=inner_left,waterlogged=false]",
                 (1, 0, 2): CHEST}
        for cell, state in cells.items():
            sch.set_block(*cell, state)
        sch.set_block_entity(1, 0, 2, TagCompound({"id": TagString("minecraft:chest")}))
        ox, oy, oz = sch.offset
        for rotate, mirror in ORIENTATIONS:
            with self.subTest(rotate=rotate, mirror=mirror):
                _, save = self.world()
                Builder(save).stamp(sch, at, rotate=rotate, mirror=mirror,
                                    paste_air=False)
                frame = frame_of(at, rotate, mirror)
                for (x, y, z), state in cells.items():
                    world = frame.cell(ox + x, oy + y, oz + z)
                    self.assertEqual(save.get_block(*world), frame.state(state),
                                     (x, y, z))
                chest = frame.cell(ox + 1, oy, oz + 2)
                stored = [entry for entry in save.dimension().chunk(
                    chest[0] >> 4, chest[2] >> 4).data["block_entities"]
                    if (int(entry["x"]), int(entry["y"]), int(entry["z"])) == chest]
                self.assertEqual(len(stored), 1, "the chest's block entity "
                                 "came with it")

    def test_each_entity_is_held_to_the_cell_it_lands_in(self):
        """The cell a stamp holds each entity to -- its reach, the guard's
        spawn admission and its entity chunk's DataVersion -- is the cell
        the paste puts it in: counted from the corner the stored offset
        puts the schematic at, after the turn, in every orientation."""
        sch = lodge()
        sch.entities.append(stand("minecraft:pig", (3.5, 2.0, 0.5), (5, 6, 7, 8)))
        for rotate, mirror in ORIENTATIONS:
            with self.subTest(rotate=rotate, mirror=mirror):
                _, save = self.world()
                with mock.patch.object(Builder, "_stamp_versions", autospec=True,
                                       side_effect=Builder._stamp_versions) as asked:
                    Builder(save).stamp(sch, self.AT, rotate=rotate, mirror=mirror)
                (call,) = asked.call_args_list
                paste = call.args[1]
                held = dict(zip((str(entity["id"]) for entity in
                                 paste.schematic.entities), paste.entity_cells))
                landed = {identity: tuple(int(v // 1) for v in pos)
                          for identity, pos in self.entities(save)}
                self.assertEqual(held, landed)
                self.assertEqual(len(landed), 2)

    def test_a_stamp_journals_what_it_wrote(self):
        """With the air pasted, the box, counted as the blocks pasted;
        with it masked, exactly the cells that are not air. Either way
        under the stage that wrote it, and the plan's _commit returns
        what the paste wrote, and nothing for a plan with no paste."""
        sch = lodge()
        _, save = self.world()
        b = Builder(save)
        with b.stage("lodge"):
            result = b.stamp(sch, self.AT, rotate=270)
        (x0, y0, z0), (x1, y1, z1) = result.placed_box
        box = self.box((x0, y0, z0), (x1, y1, z1))
        self.assertEqual(result.blocks_pasted, len(box))
        self.assertEqual(b.journal.tally(), {"lodge": len(box)})
        self.assertEqual(sorted(b.journal.written()), sorted(box))

        _, save = self.world()
        b = Builder(save)
        result = b.stamp(sch, self.AT, rotate=270, paste_air=False)
        solid = [cell for cell in box
                 if save.get_block(*cell).name not in AIR_BLOCKS]
        self.assertEqual(len(solid), 7, "stair, fence, chest, bed foot, "
                         "bricks and both door halves: the bed's head is not "
                         "in the schematic, and a paste places nothing")
        self.assertEqual(sorted(b.journal.written()), sorted(solid))
        self.assertEqual(b.blocks_set, result.blocks_pasted)
        self.assertEqual(b._commit([("set", (0, 64, 0), STONE)], what="a set"), [])

    def test_a_stamp_takes_the_stale_block_entities_in_its_box(self):
        """A chest stamped over by stone takes its inventory with it, as
        the lower tier's paste removes what it overwrites."""
        _, save = self.world()
        save.set_block(20, 64, 20, CHEST)
        chunk = save.dimension().chunk(1, 1).data
        chunk["block_entities"] = TagList([TagCompound({
            "id": TagString("minecraft:chest"), "x": TagInt(20),
            "y": TagInt(64), "z": TagInt(20)})], element_type=TAG_COMPOUND)
        sch = Schematic(1, 1, 1, DATA_VERSION)
        sch.set_block(0, 0, 0, STONE)
        result = Builder(save).stamp(sch, (20, 64, 20))
        self.assertEqual(result.block_entities_removed, 1)
        self.assertEqual(len(chunk["block_entities"]), 0)

    def test_finish_settles_the_stamp_and_its_border(self):
        """A fence stamped beside a fence already standing joins it once
        finish() runs, from both sides, as the game's block update would
        join them; the stamped column's heightmap is rebuilt."""
        _, save = self.world()
        save.set_block(19, 64, 20, FENCE)
        sch = Schematic(1, 1, 1, DATA_VERSION)
        sch.set_block(0, 0, 0, FENCE)
        b = Builder(save)
        b.stamp(sch, (20, 64, 20))
        self.assertEqual(save.get_block(20, 64, 20).properties["west"], "false",
                         "pasted as the schematic held it")
        b.finish()
        self.assertEqual(save.get_block(20, 64, 20).properties["west"], "true")
        self.assertEqual(save.get_block(19, 64, 20).properties["east"], "true")
        self.assertEqual(save.surface_y(20, 20, "WORLD_SURFACE"), 64)


class StampRefusalTests(StampBase):

    def test_a_stamp_of_an_illegal_state_is_refused_naming_it(self):
        """A schematic from before the chain's rename holds
        minecraft:chain, which the game now loads as air: refused, named,
        with its replacement suggested, before anything moves."""
        sch = lodge()
        sch.set_block(3, 2, 0, "minecraft:chain[axis=y,waterlogged=false]")
        sch.set_block(2, 2, 0, "minecraft:quartz_wall")
        for rotate in (0, 90):
            with self.subTest(rotate=rotate):
                directory, save = self.world()
                b = Builder(save, seed=4)
                drawn = b.rng.getstate()
                with self.assertRaises(IllegalBlock) as caught:
                    b.stamp(sch, self.AT, rotate=rotate)
                message = str(caught.exception)
                self.assertIn("minecraft:chain", message)
                self.assertIn("minecraft:quartz_wall", message)
                self.assertIn("stamp of a 4x3x5 schematic", message)
                self.assertIn("minecraft:iron_chain",
                              caught.exception.suggestions)
                self.assertUntouched(directory, save, b, drawn)

    def test_a_stamp_reaching_an_ungenerated_chunk_is_refused_whole(self):
        """Into a chunk the world never generated, one it has not finished,
        or past the top of the world: refused whole, every fault named,
        and nothing written in the chunks it could have written. The cell
        an entity lands in is held to the same, as a spawn's is."""
        sch = lodge()
        cases = {
            "ungenerated": ((46, 64, 20), {}, "chunk (3, 1) is not generated"),
            "unfinished": ((14, 64, 15), {(0, 1): "minecraft:features"},
                           "chunk (0, 1) is at minecraft:features"),
            "past the world": ((14, 318, 15), {}, "past the world"),
        }
        for name, (at, statuses, fault) in cases.items():
            with self.subTest(name):
                directory, save = self.world()
                for chunk, status in statuses.items():
                    save.dimension().chunk(*chunk).data["Status"] = TagString(status)
                save.save()
                before = self.regions(directory)
                save = MinecraftSave.load(directory)
                b = Builder(save)
                with self.assertRaises(Refused) as caught:
                    b.stamp(sch, at)
                self.assertTrue(any(fault in f for f in caught.exception.faults),
                                caught.exception.faults)
                self.assertUntouched(directory, save, b, before=before)

        far = Schematic(1, 1, 1, DATA_VERSION)
        far.set_block(0, 0, 0, STONE)
        far.entities.append(stand("minecraft:cow", (40.5, 0.0, 0.5)))
        directory, save = self.world()
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.stamp(far, (20, 64, 20))
        self.assertIn("chunk (3, 1) is not generated", str(caught.exception))
        self.assertUntouched(directory, save, b)
        b.stamp(far, (20, 64, 20), entities=False)
        self.assertEqual(save.get_block(20, 64, 20).name, STONE,
                         "without its entity, the stamp is in reach")

    def test_a_state_overwritten_in_the_schematic_is_not_asked_about(self):
        """Legality and the turn check read the states the paste writes,
        not the palette: Schematic.set_block leaves an overwritten state in
        its palette, so a schematic whose renamed chain and modded gearbox
        were painted over still lists both, and stamps turned all the same,
        neither refused."""
        sch = Schematic(2, 1, 1, DATA_VERSION)
        sch.set_block(0, 0, 0, "minecraft:chain[axis=y,waterlogged=false]")
        sch.set_block(1, 0, 0, "create:gearbox[side=north]")
        sch.set_block(0, 0, 0, STAIRS)
        sch.set_block(1, 0, 0, STONE)
        listed = {str(state) for state in sch.palette}
        self.assertIn("create:gearbox[side=north]", listed)
        self.assertIn("minecraft:chain[axis=y,waterlogged=false]", listed)
        _, save = self.world()
        b = Builder(save)
        b.stamp(sch, (20, 64, 20), rotate=90)
        self.assertEqual(save.get_block(20, 64, 20),
                         Frame((20, 64, 20), "west").state(STAIRS))
        self.assertEqual(save.get_block(20, 64, 21).name, STONE)
        self.assertEqual(b.blocks_set, 2)

    def test_a_stamp_that_does_not_land_whole_is_corruption(self):
        """A finished chunk missing a section inside its own height passes
        every check before the write -- generated, finished, inside the
        world -- and the paste has nothing there to write into: an
        NBTError, never a stamp that quietly lands in part and journals it
        as whole."""
        directory, save = self.world()
        chunk = save.dimension().chunk(1, 1).data
        chunk["sections"] = TagList(
            [section for section in chunk["sections"] if int(section["Y"]) != 4],
            element_type=TAG_COMPOUND)
        save.save()
        save = MinecraftSave.load(directory)
        sch = Schematic(1, 1, 1, DATA_VERSION)
        sch.set_block(0, 0, 0, STONE)
        b = Builder(save)
        with self.assertRaises(NBTError) as caught:
            b.stamp(sch, (20, 70, 20))
        self.assertNotIsInstance(caught.exception, Refused)
        self.assertIn("did not land whole", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)

    def test_a_stamp_refuses_a_state_it_cannot_turn(self):
        """A modded state with a direction the schematic vocabulary does
        not know cannot be turned: a turned or mirrored stamp of it is
        refused, never left pointing the old way, and an unturned one is
        written as it stands."""
        sch = Schematic(2, 1, 1, DATA_VERSION)
        sch.set_block(0, 0, 0, "create:gearbox[side=north]")
        sch.set_block(1, 0, 0, STAIRS)
        for rotate, mirror in ((90, None), (0, "x"), (180, "z")):
            with self.subTest(rotate=rotate, mirror=mirror):
                directory, save = self.world()
                b = Builder(save, allow=("create:",))
                with self.assertRaises(Refused) as caught:
                    b.stamp(sch, (20, 64, 20), rotate=rotate, mirror=mirror)
                self.assertNotIsInstance(caught.exception, IllegalBlock)
                self.assertIn("side=north", caught.exception.faults[0])
                self.assertUntouched(directory, save, b)
        _, save = self.world()
        Builder(save, allow=("create:",)).stamp(sch, (20, 64, 20))
        self.assertEqual(str(save.get_block(20, 64, 20)), "create:gearbox[side=north]")

    def test_bad_stamp_arguments_are_refused_before_anything(self):
        sch = lodge()
        cases = {
            "a turn off the grid": dict(rotate=45),
            "a bool turn": dict(rotate=True),
            "a float turn": dict(rotate=90.0),
            "a bool mirror": dict(mirror=True),
            "an upside-down mirror": dict(mirror="y"),
            "a capital axis": dict(mirror="X"),
            "a paste_air word": dict(paste_air="yes"),
            "an entities number": dict(entities=1),
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                directory, save = self.world()
                b = Builder(save, seed=2)
                drawn = b.rng.getstate()
                with self.assertRaises(NBTError) as caught:
                    b.stamp(sch, self.AT, **kwargs)
                self.assertNotIsInstance(caught.exception, Refused)
                self.assertUntouched(directory, save, b, drawn)
        for at in ((14, 64), (14.0, 64, 15), (14, True, 15), "here"):
            with self.subTest(at=at):
                directory, save = self.world()
                b = Builder(save)
                with self.assertRaises(NBTError):
                    b.stamp(sch, at)
                self.assertUntouched(directory, save, b)
        directory, save = self.world()
        b = Builder(save)
        with self.assertRaises(TypeError):
            b.stamp({"blocks": []}, self.AT)
        with self.assertRaises(FileNotFoundError):
            b.stamp(os.path.join(directory, "no-such.schem"), self.AT)
        self.assertUntouched(directory, save, b)

    def test_a_schematic_newer_than_the_world_is_refused_before_anything(self):
        directory, save = self.world()
        b = Builder(save)
        with self.assertRaises(NBTError) as caught:
            b.stamp(lodge(DATA_VERSION + 100), self.AT)
        self.assertIn("newer Minecraft", str(caught.exception))
        self.assertUntouched(directory, save, b)


class StampVersionTests(StampBase):
    """The DataVersion policy: the game reads a stored block entity and a
    stored entity by the DataVersion of the chunk holding it, so neither
    may cross into a chunk of another version. Blocks may: each chunk
    keeps its own palette codec, and legality covers the states."""

    OLD = DATA_VERSION - 463           # an older Minecraft than the world's

    def test_block_entities_from_another_data_version_are_refused(self):
        """A chest from an older schematic is refused, naming its cell and
        both versions, entities or not -- a block entity is not an entity,
        and entities=False does not leave it out. At the world's own
        version it is stamped, its block entity with it."""
        sch = lodge(self.OLD)
        sch.entities.clear()
        for entities in (True, False):
            with self.subTest(entities=entities):
                directory, save = self.world()
                b = Builder(save)
                with self.assertRaises(Refused) as caught:
                    b.stamp(sch, self.AT, entities=entities)
                (fault,) = caught.exception.faults
                self.assertIn("minecraft:chest block entity", fault)
                self.assertIn(f"DataVersion {self.OLD}", fault)
                self.assertIn(f"DataVersion {DATA_VERSION}", fault)
                self.assertIn(str((self.AT[0] + 1, 64, self.AT[2] - 1)), fault)
                self.assertUntouched(directory, save, b)

        sch.data_version = DATA_VERSION
        _, save = self.world()
        result = Builder(save).stamp(sch, self.AT)
        self.assertEqual(result.block_entities_added, 1)

    def test_a_block_entity_is_judged_by_the_terrain_chunk_it_lands_in(self):
        """A world played across an update holds chunks of several
        versions, and a block entity is asked about the chunk it lands in,
        not level.dat: a chest saved at an older chunk's own version is
        stamped into it, and one at level.dat's version is refused there,
        naming the chunk's version."""
        sch = Schematic(1, 1, 1, DATA_VERSION)
        sch.set_block(0, 0, 0, CHEST)
        sch.set_block_entity(0, 0, 0, TagCompound({"id": TagString("minecraft:chest")}))
        for version, refused in ((DATA_VERSION, True), (self.OLD, False)):
            with self.subTest(version=version):
                directory, save = self.world()
                save.dimension().chunk(1, 1).data["DataVersion"] = TagInt(self.OLD)
                save.save()
                before = self.regions(directory)
                save = MinecraftSave.load(directory)
                self.assertEqual(save.dimension().blocks(1, 1).data_version, self.OLD)
                sch.data_version = version
                b = Builder(save)
                if refused:
                    with self.assertRaises(Refused) as caught:
                        b.stamp(sch, (20, 64, 20))
                    (fault,) = caught.exception.faults
                    self.assertIn(f"saved at DataVersion {DATA_VERSION}", fault)
                    self.assertIn(f"chunk (1, 1), at DataVersion {self.OLD}", fault)
                    self.assertUntouched(directory, save, b, before=before)
                else:
                    result = b.stamp(sch, (20, 64, 20))
                    self.assertEqual(result.block_entities_added, 1)

    def test_entity_storage_that_cannot_be_read_is_corruption(self):
        """An entity chunk the stamp would join that exists and cannot be
        read is not a version to compare or a chunk to make: a plain
        NBTError, before anything is written -- not a Refused, and not the
        blocks pasted with the entity left behind."""
        directory, save = self.world()
        self.assertTrue(save.spawn_entity("minecraft:pig", 20.5, 64.0, 20.5))
        save.save()
        path = os.path.join(directory, ENTITIES_DIR, "r.0.0.mca")
        with open(path, "r+b") as handle:
            index = 4 * ((1 & 31) + (1 & 31) * 32)
            handle.seek(index)
            sector = struct.unpack(">I", b"\0" + handle.read(3))[0]
            self.assertGreater(sector, 0, "the pig's entity chunk is stored")
            handle.seek(sector * 4096 + 5)
            handle.write(b"\xff" * 32)     # the compressed payload, garbled
        with open(path, "rb") as handle:
            entities = handle.read()
        before = self.regions(directory)
        save = MinecraftSave.load(directory)
        sch = Schematic(1, 1, 1, DATA_VERSION)
        sch.set_block(0, 0, 0, STONE)
        sch.entities.append(stand("minecraft:cow", (0.5, 0.0, 0.5)))
        b = Builder(save)
        with self.assertRaises(NBTError) as caught:
            b.stamp(sch, (21, 64, 21))
        self.assertNotIsInstance(caught.exception, Refused)
        self.assertIn("entity storage for chunk (1, 1)", str(caught.exception))
        self.assertEqual(save.get_block(21, 64, 21).name, "minecraft:air")
        self.assertUntouched(directory, save, b, before=before)
        with open(path, "rb") as handle:
            self.assertTrue(handle.read() == entities, "the entities file is "
                            "as it was")

    def test_a_masked_cell_brings_no_block_entity_to_refuse(self):
        """With the air masked, a block entity on an air cell is not
        written, so its version is not asked."""
        sch = Schematic(2, 1, 1, self.OLD)
        sch.set_block(0, 0, 0, STONE)
        sch.set_block_entity(1, 0, 0, TagCompound({"id": TagString("minecraft:chest")}))
        _, save = self.world()
        with self.assertRaises(Refused):
            Builder(save).stamp(sch, (20, 64, 20))
        _, save = self.world()
        result = Builder(save).stamp(sch, (20, 64, 20), paste_air=False)
        self.assertEqual(result.block_entities_added, 0)
        self.assertEqual(save.get_block(20, 64, 20).name, STONE)

    def test_entities_from_another_data_version_are_refused_unless_left_out(self):
        """An older schematic's blocks are stamped; its armor stand is
        refused, since the entity chunk the paste would make is at the
        world's version. entities=False leaves it out, written and checked
        not at all, and the blocks land."""
        sch = lodge(self.OLD)
        sch.set_block_entity(2, 0, 1, None)
        directory, save = self.world()
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.stamp(sch, self.AT)
        (fault,) = caught.exception.faults
        self.assertIn("minecraft:armor_stand", fault)
        self.assertIn("a new entity chunk", fault)
        self.assertIn(f"DataVersion {DATA_VERSION}", fault)
        self.assertUntouched(directory, save, b)

        _, save = self.world()
        b = Builder(save)
        result = b.stamp(sch, self.AT, entities=False)
        self.assertEqual(result.entities_added, 0)
        self.assertEqual(b.entities_added, 0)
        self.assertEqual(self.entities(save), [])
        self.assertEqual(str(save.get_block(self.AT[0] - 1, 64, self.AT[2] - 2)),
                         str(BlockState.parse(STAIRS)), "the blocks landed")

    def test_an_entity_is_judged_by_the_entity_chunk_it_joins(self):
        """Where an entity chunk is already stored, its own DataVersion is
        the one asked, not level.dat's: a stamp at the world's version is
        refused into an entity chunk stored by an older game, and one of
        that older version is taken."""
        sch = Schematic(1, 1, 1, DATA_VERSION)
        sch.set_block(0, 0, 0, STONE)
        sch.entities.append(stand("minecraft:cow", (0.5, 0.0, 0.5)))
        for version, refused in ((DATA_VERSION, True), (self.OLD, False)):
            with self.subTest(version=version):
                _, save = self.world()
                self.assertTrue(save.spawn_entity("minecraft:pig", 20.5, 64.0, 20.5))
                storage = save.dimension().entity_chunk(1, 1)
                storage.data["DataVersion"] = TagInt(self.OLD)
                sch.data_version = version
                b = Builder(save)
                if refused:
                    with self.assertRaises(Refused) as caught:
                        b.stamp(sch, (21, 64, 21))
                    self.assertIn("the entity chunk (1, 1)", str(caught.exception))
                    self.assertIn(f"at DataVersion {self.OLD}", str(caught.exception))
                    self.assertEqual(b.blocks_set, 0)
                else:
                    b.stamp(sch, (21, 64, 21))
                    self.assertEqual(b.entities_added, 1)
                    self.assertEqual(len(storage.data["Entities"]), 2)

    def test_west_of_zero_a_stamp_is_judged_by_the_chunks_it_lands_in(self):
        """Cells and chunks are floored, never truncated: a chest at x=-5
        lands in chunk (-1, 1) and a cow at x=-0.5 in the cell at x=-1 and
        entity chunk (-1, 1) -- not chunk (0, 1), where truncating toward
        zero would put both. With chunk (-1, 1) and its entity storage
        older than the world, a chest and a cow saved at the world's
        version are refused there, naming chunk (-1, 1), and the same
        stamps one chunk east, in chunk (0, 1), land."""
        chest = Schematic(1, 1, 1, DATA_VERSION)
        chest.set_block(0, 0, 0, CHEST)
        chest.set_block_entity(0, 0, 0, TagCompound(
            {"id": TagString("minecraft:chest")}))
        cow = Schematic(1, 1, 1, DATA_VERSION)
        cow.set_block(0, 0, 0, STONE)
        cow.entities.append(stand("minecraft:cow", (0.5, 0.0, 0.5)))

        directory, save = self.world()
        save.dimension().chunk(-1, 1).data["DataVersion"] = TagInt(self.OLD)
        save.save()
        save = MinecraftSave.load(directory)
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.stamp(chest, (-5, 64, 20))
        (fault,) = caught.exception.faults
        self.assertIn(f"chunk (-1, 1), at DataVersion {self.OLD}", fault)
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(b.stamp(chest, (11, 64, 20)).block_entities_added, 1)

        _, save = self.world()
        self.assertTrue(save.spawn_entity("minecraft:pig", -8.5, 64.0, 20.5))
        save.dimension().entity_chunk(-1, 1).data["DataVersion"] = \
            TagInt(self.OLD)
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.stamp(cow, (-1, 64, 21))
        self.assertIn("the entity chunk (-1, 1)", str(caught.exception))
        self.assertIn(f"at DataVersion {self.OLD}", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        b.stamp(cow, (15, 64, 21))
        self.assertEqual(b.entities_added, 1)
        self.assertIn(("minecraft:cow", (15.5, 64.0, 21.5)),
                      self.entities(save))


class StampGuardTests(SyntheticSiteCase):
    """Inside a worksite a stamp is admitted as a block write over its box
    and, separately, as a spawn at each of its entities' cells."""

    def test_a_stamp_into_a_protected_zone_is_refused_writing_nothing(self):
        site = self.open_site()
        site.protect((6, 64, 6), (6, 64, 6), name="shrine")
        b = site.builder()
        sch = lodge()
        with self.assertRaises(Refused) as caught:
            b.stamp(sch, (6, 64, 8))
        self.assertIn("'shrine'", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(b.entities_added, 0)
        self.assertEqual(site.diff((0, 60, 0), (15, 70, 15)), {})
        self.assertEqual(site.check(), [])
        result = b.stamp(sch, (10, 64, 8))
        self.assertEqual(b.blocks_set, result.blocks_pasted, "clear of it")

    def test_an_entity_outside_the_snapshot_is_refused_and_entities_false_drops_it(self):
        """The blocks land inside the snapshot; the cow would land in an
        entities file it does not hold, so the stamp is refused whole --
        and with entities=False it is admitted, blocks and all."""
        site = self.open_site()
        b = site.builder()
        sch = Schematic(1, 1, 1, DATA_VERSION)
        sch.set_block(0, 0, 0, STONE)
        sch.entities.append(stand("minecraft:cow", (640.5, 0.0, 0.5)))
        with self.assertRaises(Refused) as caught:
            b.stamp(sch, (5, 64, 5))
        self.assertIn(f"{ENTITIES_DIR}/r.1.0.mca", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(site.check(), [])
        b.stamp(sch, (5, 64, 5), entities=False)
        self.assertEqual(b.blocks_set, 1)
        self.assertEqual(b.entities_added, 0)
        self.assertEqual(site.save.get_block(5, 64, 5).name, STONE)


if __name__ == "__main__":
    unittest.main()
