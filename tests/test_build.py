"""Tests for the construction layer: spawn_entity, rebuild_heightmaps,
invalidate_poi on MinecraftSave, and the Builder in heaton_voxel.craft.build."""

from __future__ import annotations

import math
import os
import shutil
import tempfile
import unittest

import fixtures
import heaton_voxel.nbt as nbt
from heaton_voxel.craft import biomes, geometry
from heaton_voxel.craft.build import (
    LANE_MIX,
    MELT_LIGHT,
    MELT_SNOW,
    NO_NEW_SNOW_OR_ICE,
    NO_SPAWNS,
    PLAZA_MIX,
    ROAD_MIX,
    Builder,
    PaveResult,
)
from heaton_voxel.blocks import BlockState
from heaton_voxel.craft.blockinfo import is_tree
from heaton_voxel.craft.errors import IllegalBlock, NoSurface, Refused
from heaton_voxel.craft.light import plan_lights, simulate_block_light
from heaton_voxel.craft.survey import SurfaceProbe, spawnable
from heaton_voxel.nbt import NBT, Chunk, NBTError, TagCompound, TagInt, TagString
from heaton_voxel.world import NETHER, OVERWORLD, MinecraftSave
from tests.test_world import DATA_VERSION, make_save

CHUNKS = [(x, z) for x in range(-1, 2) for z in range(-1, 2)]


class WorldBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="build-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, CHUNKS)
        self.save = MinecraftSave.load(self.dir)


class SpawnEntityTests(WorldBase):
    def test_structural_nbt_is_supplied(self):
        self.assertTrue(self.save.spawn_entity("minecraft:cow", 4.5, 70.0, 4.5, yaw=90.0))
        chunk = self.save.dimension(OVERWORLD).entity_chunk(0, 0)
        (cow,) = chunk.data["Entities"]
        self.assertEqual(str(cow["id"]), "minecraft:cow")
        self.assertEqual([float(v) for v in cow["Pos"]], [4.5, 70.0, 4.5])
        self.assertEqual(float(cow["Rotation"][0]), 90.0)
        self.assertEqual(len(cow["UUID"]), 4)

    def test_every_entity_gets_its_own_identity(self):
        self.save.spawn_entity("minecraft:pig", 1.5, 70.0, 1.5)
        self.save.spawn_entity("minecraft:pig", 2.5, 70.0, 2.5)
        chunk = self.save.dimension(OVERWORLD).entity_chunk(0, 0)
        uuids = [tuple(e["UUID"]) for e in chunk.data["Entities"]]
        self.assertEqual(len(set(uuids)), 2)

    def test_extra_tags_are_merged(self):
        self.save.spawn_entity(
            "minecraft:sheep", 3.5, 70.0, 3.5,
            extra={"Color": nbt.TagByte(12)},
        )
        chunk = self.save.dimension(OVERWORLD).entity_chunk(0, 0)
        self.assertEqual(int(chunk.data["Entities"][0]["Color"]), 12)

    def test_refuses_ungenerated_ground(self):
        self.assertFalse(self.save.spawn_entity("minecraft:cow", 500.5, 70.0, 500.5))


class RebuildHeightmapTests(WorldBase):
    def test_every_map_is_rebuilt(self):
        self.save.fill((0, 60, 0), (8, 63, 8), "minecraft:stone")
        self.save.set_block(4, 90, 4, "minecraft:stone_bricks")
        result = self.save.rebuild_heightmaps((0, 0, 0), (8, 0, 8))
        self.assertTrue(result.complete)
        self.assertEqual(result.chunks_rebuilt, 1)
        self.assertEqual(self.save.surface_y(4, 4, "WORLD_SURFACE"), 90)
        self.assertEqual(self.save.surface_y(2, 2, "WORLD_SURFACE"), 63)
        self.assertEqual(self.save.surface_y(6, 6, "MOTION_BLOCKING"), 63)
        maps = self.save.dimension(OVERWORLD).chunk(0, 0).data["Heightmaps"]
        self.assertEqual(list(maps), ["OCEAN_FLOOR", "MOTION_BLOCKING_NO_LEAVES",
                                      "MOTION_BLOCKING", "WORLD_SURFACE"])

    def test_empty_columns_report_no_surface(self):
        self.save.rebuild_heightmaps((0, 0, 0), (0, 0, 0))
        self.assertIsNone(self.save.surface_y(3, 3, "WORLD_SURFACE"))

    def test_missing_chunks_are_reported(self):
        result = self.save.rebuild_heightmaps((0, 0, 0), (200, 0, 0))
        self.assertFalse(result.complete)
        self.assertGreater(len(result.missing_chunks), 0)


class InvalidatePoiTests(WorldBase):
    def poi_path(self):
        directory = os.path.join(
            self.dir, "dimensions", "minecraft", "overworld", "poi"
        )
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, "r.0.0.mca")

    def write_poi(self, chunks):
        region = NBT()
        region.kind = "region"
        region.region_x = region.region_z = 0
        for cx, cz in chunks:
            chunk = Chunk(cx & 31, cz & 31)
            chunk.data = TagCompound(
                {"DataVersion": TagInt(DATA_VERSION), "Sections": TagCompound()}
            )
            region.chunks[(cx & 31, cz & 31)] = chunk
        region.save(self.poi_path())

    def test_records_in_the_box_are_dropped_and_saved(self):
        self.write_poi([(0, 0), (1, 1), (5, 5)])
        save = MinecraftSave.load(self.dir)
        removed = save.invalidate_poi((0, 0, 0), (31, 0, 31))
        self.assertEqual(removed, 2, "chunk (5,5) is outside the box")
        self.assertGreater(save.save(), 0)
        fresh = NBT.load(self.poi_path())
        self.assertEqual(sorted(fresh.chunks), [(5, 5)])

    def test_no_poi_file_means_nothing_to_drop(self):
        self.assertEqual(self.save.invalidate_poi((0, 0, 0), (31, 0, 31)), 0)


class BuilderBase(WorldBase):
    def setUp(self):
        super().setUp()
        # give the flat synthetic world some actual ground to build on
        self.save.fill((-16, 60, -16), (31, 63, 31), "minecraft:stone")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.b = Builder(self.save, seed=7)


class BuilderTests(BuilderBase):
    def test_ground_reads_a_rebuilt_chunk(self):
        """A rebuilt map is WORLD_SURFACE alone; the ground is read off the
        finished chunk's blocks instead, the way the game's maps would."""
        self.assertEqual(self.b.ground(4, 4), 63)

    def test_level_pad_terraces_with_a_retaining_ring(self):
        self.b.level_pad(0, 0, 8, 8, 65, name="yard")
        self.assertEqual(self.save.get_block(4, 65, 4).name, "minecraft:grass_block")
        self.assertEqual(self.save.get_block(0, 64, 0).name, "minecraft:stone_bricks")
        self.assertEqual(self.save.get_block(4, 64, 4).name, "minecraft:dirt")
        self.assertEqual(self.b.ground(4, 4), 65, "the cache follows the pad")

    def test_a_pad_with_no_clearance_keeps_its_surface(self):
        """No headroom is no headroom: a fill sorts its corners, so clearing
        ``top_y + 1 .. top_y + 0`` cleared the surface just laid. A negative
        clearance is refused before anything is written."""
        self.b.level_pad(2, 2, 4, 4, 65, clearance=0)
        self.assertEqual(self.save.get_block(3, 65, 3).name, "minecraft:grass_block")
        self.assertEqual(self.b.ground(3, 3), 65)

        written = self.b.blocks_set
        with self.assertRaises(NBTError):
            self.b.level_pad(6, 6, 8, 8, 65, clearance=-2)
        with self.assertRaises(NBTError):
            self.b.smooth_terrain((6, 6), (8, 8), clearance=-2)
        self.assertEqual(self.b.blocks_set, written, "nothing was written")
        self.assertEqual(self.save.get_block(7, 63, 7).name, "minecraft:stone")

    def test_roads_refuse_to_carve_through_pads(self):
        self.b.level_pad(0, 0, 8, 8, 64, name="cottage")
        with self.assertRaises(NBTError) as ctx:
            self.b.road([(-8, 4), (16, 4)], width=3)
        self.assertIn("cottage", str(ctx.exception))
        # the same road is allowed when the crossing is declared
        self.b.road([(-8, 4), (16, 4)], width=3, through=("cottage",))
        surface = self.save.get_block(-4, self.b.ground(-4, 4), 4)
        self.assertIn(surface.name, {
            "minecraft:cobblestone", "minecraft:andesite", "minecraft:gravel",
            "minecraft:stone", "minecraft:mossy_cobblestone",
        })

    def test_road_surfaces(self):
        self.b.road([(-10, -10), (10, -10)], width=2, mix=LANE_MIX)
        names = {self.save.get_block(x, self.b.ground(x, -10), -10).name
                 for x in range(-10, 11)}
        self.assertTrue(names <= {"minecraft:dirt_path", "minecraft:gravel",
                                  "minecraft:coarse_dirt"}, names)

    def test_a_pad_refusal_names_every_pad(self):
        """A road through two lots is refused once, naming both, each at
        the first column of it the road would have cut -- so one pass can
        reroute around them all. The road runs west, so that column is
        each lot's east edge, and it reaches the smithy first; the faults
        still come in the order the lots were laid out."""
        self.b.level_pad(0, 0, 4, 8, 64, name="cottage")
        self.b.level_pad(10, 0, 14, 8, 64, name="smithy")
        before = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.road([(20, 4), (-8, 4)], width=3)
        faults = caught.exception.faults
        self.assertEqual(len(faults), 2, faults)
        self.assertIn("pad 'cottage' at (4, 3) (15 columns)", faults[0])
        self.assertIn("pad 'smithy' at (14, 3) (15 columns)", faults[1])
        self.assertIn("smithy", str(caught.exception))
        self.assertEqual(self.b.blocks_set, before, "a refusal writes nothing")
        self.b.road([(20, 4), (-8, 4)], width=3, through=("cottage", "smithy"))

    def test_a_road_paves_the_swath_of_geometrys_line(self):
        """A road's centerline is geometry's line, and its swath the square
        stamped on each cell of it: every column once, along the centerline
        and then across it, each tagged with the stretch that reaches it
        first. A road paves exactly those columns, in that order, so
        anything laid along the same waypoints can follow it."""
        from heaton_voxel.craft.build.site import _swath
        from heaton_voxel.craft.geometry import line

        self.assertEqual(list(_swath(((0, 0), (1, 0), (2, 1)), 1).items()), [
            ((-1, -1), 0), ((-1, 0), 0), ((-1, 1), 0), ((0, -1), 0),
            ((0, 0), 0), ((0, 1), 0), ((1, -1), 0), ((1, 0), 0), ((1, 1), 0),
            ((2, -1), 1), ((2, 0), 1), ((2, 1), 1),
            ((1, 2), 2), ((2, 2), 2), ((3, 0), 2), ((3, 1), 2), ((3, 2), 2)])
        self.assertEqual(list(_swath(((5, 5), (6, 6)), 0).items()),
                         [((5, 5), 0), ((6, 6), 1)])

        points = [(-10, -10), (2, -4), (2, 8), (-4, 5)]   # ties, a corner
        swath = _swath(line(points), 1)
        self.b.road(points, width=3)
        self.assertEqual(list(self.b._ground), list(swath),
                         "the columns paved, in the order they were paved")
        names = {name for name, _ in ROAD_MIX}
        for x, z in swath:
            self.assertIn(self.save.get_block(x, 63, z).name, names, (x, z))

    def test_a_road_holds_its_waypoints_to_the_lines_rule(self):
        """A waypoint is a pair of ints, held to geometry's rule and
        refused in its words, naming it, before the road reads or writes a
        thing: a float, a whole one included -- one that is not would put
        the road's end where nobody asked, and was once rounded there -- a
        bool, a bare number, a triple, and no sequence at all. A road of
        one cell keeps its own refusal, and an iterator of waypoints is the
        list of them."""
        rng = self.b.rng.getstate()
        for points, bad in (([(0, 0), (2, 0.5)], (2, 0.5)),
                            ([(0, 0), (2, 2.0)], (2, 2.0)),
                            ([(0, 0), (True, 0)], (True, 0)),
                            ([(0, 0), 5], 5),
                            ([(0, [1]), (1, 1)], (0, [1])),
                            ([(0, 0, 0), (4, 0, 0)], (0, 0, 0))):
            with self.subTest(points=points):
                with self.assertRaises(NBTError) as caught:
                    self.b.road(points, width=1)
                self.assertEqual(str(caught.exception),
                                 f"a waypoint must be a pair of ints, got {bad!r}")
        with self.assertRaises(NBTError) as caught:
            self.b.road(5)
        self.assertEqual(str(caught.exception),
                         "a road takes a sequence of (x, z) waypoints, got 5")
        for points in ([(5, 5), (5, 5)], [(5, 5)], [], [[5, 5], (5, 5)],
                       iter([(5, 5), (5, 5), (5, 5)])):
            with self.subTest(points=points):
                with self.assertRaises(NBTError) as caught:
                    self.b.road(points)
                self.assertEqual(str(caught.exception),
                                 "a road needs at least two distinct waypoints")
        self.assertEqual(self.b.blocks_set, 0, "a refusal writes nothing")
        self.assertEqual(list(self.b.journal.written()), [])
        self.assertEqual(self.b.rng.getstate(), rng, "and draws nothing")
        self.b.road(iter([(0, 0), (6, 3)]), width=1)
        self.assertEqual(list(self.b._ground), [(0, 0), (1, 0), (2, 1), (3, 2),
                                                (4, 2), (5, 2), (6, 3)])

    def test_a_road_rides_the_smoothed_profile(self):
        """A road's surface is geometry's profile of the ground under its
        centerline at ``ROAD_WINDOW``, an anchor pinning its cell on every
        visit -- a line that doubles back or crosses itself passes one
        twice -- and one off the line pinning nothing. A high anchor stays a
        step, and a low one pulls the road down a block per cell. On a road
        wider than 1 the anchor's height lands ``width // 2`` cells along,
        where its stretch first reaches. The high anchor here stands 11
        blocks of fill over the ground, which is a dam the road refuses
        unless ``allow_dam=True`` says to fill it."""
        # A mesa (top 71) and a pit (floor 55) across the road's line.
        self.save.fill((4, 64, 2), (8, 71, 6), "minecraft:stone")
        self.save.fill((-10, 56, 2), (-6, 63, 6), "minecraft:air")
        self.save.fill((-10, 55, 2), (-6, 55, 6), "minecraft:stone")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        b = Builder(self.save, seed=7)
        points = [(-14, 4), (20, 4), (12, 4)]
        anchors = {(14, 4): 75, (-2, 4): 58, (0, 9): 99}
        centerline = geometry.line(points)
        heights = [b.ground(x, z) for x, z in centerline]
        pins = {i: anchors[cell] for i, cell in enumerate(centerline)
                if cell in anchors}
        self.assertEqual(sorted(pins), [12, 28, 40])     # (14, 4) twice
        smooth = geometry.smoothed_profile(heights, window=geometry.ROAD_WINDOW,
                                           anchors=pins)
        with self.assertRaises(Refused):
            b.road(points, width=1, anchors=anchors)
        b.road(points, width=1, anchors=anchors, allow_dam=True)
        first = {}
        for cell, y in zip(centerline, smooth):
            first.setdefault(cell, y)
        for (x, z), y in first.items():
            self.assertEqual(b.ground(x, z), y, (x, z))
            self.assertIn(self.save.get_block(x, y, z).name, {
                "minecraft:cobblestone", "minecraft:andesite", "minecraft:gravel",
                "minecraft:stone", "minecraft:mossy_cobblestone"}, (x, z))
        self.assertEqual(first[(14, 4)], 75)
        self.assertLess(max(first[(13, 4)], first[(15, 4)]), 74)
        self.assertEqual([first[(x, 4)] for x in range(-4, 1)],
                         [60, 59, 58, 59, 60])
        # A line that crosses itself with a low anchor on the crossing: the
        # second visit is pinned as well as the first, so the cells first
        # walked either side of it come down to it a block per cell.
        cross = [(0, 16), (12, 16), (12, 22), (6, 22), (6, 10)]
        self.assertEqual([i for i, cell in enumerate(geometry.line(cross))
                          if cell == (6, 16)], [6, 30])
        b.road(cross, width=1, anchors={(6, 16): 58})
        self.assertEqual([b.ground(6, z) for z in range(19, 11, -1)],
                         [61, 60, 59, 58, 59, 60, 61, 62])
        self.assertEqual([b.ground(x, 16) for x in range(4, 9)],
                         [60, 59, 58, 59, 60])
        # Wider than 1, each column is cut to the first stretch reaching
        # it, so the anchor's height lands width // 2 cells further along.
        b.road([(0, 28), (12, 28)], width=3, anchors={(6, 28): 66})
        self.assertEqual([b.ground(x, 28) for x in range(4, 10)],
                         [63, 63, 63, 66, 63, 63])
        self.assertEqual([b.ground(7, z) for z in (27, 29)], [66, 66])

    def test_a_road_names_an_anchor_that_is_not_a_whole_height(self):
        """An anchor's height is an int: a float -- a whole one too -- and a
        bool are refused, naming the cell, before anything is read, so a
        road running off the generated ground is refused for its anchor
        and not its ground. ``True`` was once a road anchored at y = 1,
        cut down to it."""
        rng = self.b.rng.getstate()
        for y in (66.5, 66.0, True):
            for points in ([(0, 4), (12, 4)], [(0, 4), (400, 4)]):
                with self.subTest(y=y, points=points):
                    with self.assertRaises(NBTError) as caught:
                        self.b.road(points, anchors={(6, 4): y})
                    self.assertEqual(str(caught.exception),
                                     f"a road's anchor is an int height, got "
                                     f"{y!r} at (6, 4)")
        self.assertEqual(self.b.blocks_set, 0, "a refusal writes nothing")
        self.assertEqual(list(self.b.journal.written()), [])
        self.assertEqual(self.b.rng.getstate(), rng, "and draws nothing")
        self.b.road([(0, 4), (12, 4)], width=1,
                    anchors={(6, 4): 66, (6, 5): 1.5})
        self.assertEqual(self.b.ground(6, 4), 66, "a float off the line pins nothing")

    def test_a_column_stands_where_the_ground_writers_stand_it(self):
        """``_stand`` reads one column before anything is written: dry
        ground on its probe, a column the session shaped on the height it
        set, and water -- open, or under a height set on it -- with its
        probe, for the writer to refuse, bridge or fill. No ground at all
        is NoSurface."""
        self.save.fill((7, 61, 7), (9, 63, 9), "minecraft:water[level=0]")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.assertEqual(self.b._stand((4, 4)), (63, None))
        self.b.set_ground(5, 5, 66)
        self.assertEqual(self.b._stand((5, 5)), (66, None))
        y, wet = self.b._stand((8, 8))
        self.assertEqual((y, wet.wet, wet.y, wet.waterline), (60, True, 60, 63))
        self.b.set_ground(8, 8, 63)
        y, wet = self.b._stand((8, 8))
        self.assertEqual((y, wet.wet, wet.waterline), (63, True, 63))
        with self.assertRaises(NoSurface) as caught:
            self.b._stand((500, 500))
        self.assertEqual(caught.exception.reason, "ungenerated")
        self.assertEqual(self.b.blocks_set, 0, "a read writes nothing")

    def test_clear_vegetation_fells_trees(self):
        for y in range(64, 68):
            self.save.set_block(2, y, 2, "minecraft:oak_log[axis=y]")
        self.save.set_block(2, 68, 2, "minecraft:oak_leaves[distance=1]")
        self.save.set_block(3, 64, 3, "minecraft:short_grass")
        removed = self.b.clear_vegetation((0, 0), (5, 5))
        self.assertEqual(removed, 6)
        self.assertEqual(self.save.get_block(2, 64, 2), "minecraft:air")
        self.assertEqual(self.save.get_block(3, 64, 3), "minecraft:air")

    def test_clear_vegetation_fells_the_whole_trunk(self):
        """A rebuilt map is WORLD_SURFACE alone, which tops out on the
        canopy; canopy is not ground, so the ground is read through it, off
        the top log. The trunk under it is the same tree: it is felled to
        the dirt it grew from, and a leaf over open ground is no ground at
        all."""
        self.save.set_block(2, 63, 2, "minecraft:dirt")
        for y in range(64, 68):
            self.save.set_block(2, y, 2, "minecraft:oak_log[axis=y]")
        leaf = "minecraft:oak_leaves[distance=1,persistent=false,waterlogged=false]"
        self.save.set_block(2, 68, 2, leaf)
        self.save.set_block(3, 68, 2, leaf)
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.assertEqual(self.b.ground(2, 2), 67, "the top log, under the leaf")
        self.assertEqual(self.b.clear_vegetation((0, 0), (5, 5)), 6)
        for x in (2, 3):
            self.assertEqual({self.save.get_block(x, y, 2).name
                              for y in range(64, 69)}, {"minecraft:air"},
                             f"the column at x={x}")
            self.assertEqual(self.b.ground(x, 2), 63, f"the ground at x={x}")
        self.assertEqual(self.save.get_block(2, 63, 2).name,
                         "minecraft:grass_block",
                         "the dirt the trunk stood on is grass again")
        self.assertEqual(self.save.get_block(3, 63, 2).name, "minecraft:stone")

    def test_clear_vegetation_takes_saplings_wood_stems_and_flowers(self):
        """The game's own trees and ground cover, held to its block tags --
        not a list that missed forty of its logs, leaves and saplings and
        two dozen of its flowers."""
        planted = {
            (2, 2): "minecraft:oak_sapling[stage=0]",
            (4, 4): "minecraft:oak_wood[axis=y]",
            (6, 6): "minecraft:crimson_stem[axis=y]",
            (8, 8): "minecraft:red_tulip",
            (10, 10): "minecraft:mangrove_propagule",
        }
        for (x, z), block in planted.items():
            self.save.set_block(x, 64, z, block)
        self.assertEqual(self.b.clear_vegetation((0, 0), (10, 10)), len(planted))
        for x, z in planted:
            self.assertEqual(self.save.get_block(x, 64, z).name,
                             "minecraft:air", planted[(x, z)])

    def test_clear_vegetation_reaches_the_top_of_a_tall_tree(self):
        """A jungle giant stands taller than the 26 blocks the sweep used
        to stop at. It stops at the top of the column now, its
        WORLD_SURFACE, and ``height=`` still caps it."""
        self.b.level_pad(0, 0, 4, 4, 63, name="lot")
        for y in range(64, 98):
            self.save.set_block(2, y, 2, "minecraft:jungle_log[axis=y]")
        self.save.set_block(2, 98, 2, "minecraft:jungle_leaves[distance=1,"
                                      "persistent=false,waterlogged=false]")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.assertEqual(self.b.clear_vegetation((2, 2), (2, 2), height=10), 9)
        self.assertEqual(self.save.get_block(2, 73, 2).name,
                         "minecraft:jungle_log", "height= caps the sweep")
        self.assertEqual(self.b.clear_vegetation((2, 2), (2, 2)), 26)
        self.assertEqual({self.save.get_block(2, y, 2).name
                          for y in range(64, 99)}, {"minecraft:air"})
        self.assertEqual(self.b.ground(2, 2), 63)

    def test_clear_vegetation_reads_a_mapless_column_from_its_blocks(self):
        """With no WORLD_SURFACE stored, a finished chunk's own blocks say
        where its columns end, so a canopy thirty blocks over ground a
        script shaped is still found -- a chunk a hand-made save marks
        plain ``full`` included. A chunk the generator set aside part-way
        is not read that way: its blocks are not yet its terrain."""
        dim = self.save.dimension(OVERWORLD)
        leaf = "minecraft:oak_leaves[distance=1,persistent=false,waterlogged=false]"
        for status, found in (("minecraft:full", 1), ("full", 1),
                              ("minecraft:features", 0)):
            with self.subTest(status=status):
                chunk = dim.chunk(0, 0)
                chunk.data["Heightmaps"] = TagCompound()
                chunk.data["Status"] = TagString(status)
                dim.release(0, 0)
                self.save.set_block(2, 93, 2, leaf)
                b = Builder(self.save, seed=7)
                b._ground[(2, 2)] = 63     # as a script that shaped it records it
                self.assertEqual(b.clear_vegetation((2, 2), (2, 2)), found)
                self.assertEqual(self.save.get_block(2, 93, 2).name,
                                 "minecraft:air" if found else
                                 "minecraft:oak_leaves")

    def test_clear_vegetation_fells_what_this_session_planted(self):
        """No write of the Builder's moves a map, so a canopy this session
        planted stands over a WORLD_SURFACE that has never heard of it, and
        the gap under a leaf ends a sweep bounded by that map. A column of
        a chunk the session has built in is read from its blocks instead.
        The nether tree is a stem and wart blocks, both trees by the
        game's tags."""
        self.b.ornamental_tree(4, 4)
        self.b.ornamental_tree(12, 12, kind="crimson")

        def trees():
            return sorted((x, y, z) for x in range(0, 17) for z in range(0, 17)
                          for y in range(64, 72)
                          if is_tree(self.save.get_block(x, y, z).name))

        planted = trees()
        self.assertGreater(len(planted), 40)
        self.assertEqual(self.b.clear_vegetation((0, 0), (16, 16)), len(planted))
        self.assertEqual(trees(), [])
        self.assertEqual((self.b.ground(4, 4), self.b.ground(12, 12)), (63, 63))

    def test_clear_vegetation_leaves_the_water_a_waterlogged_block_held(self):
        """The game puts a waterlogged block's water back when it removes
        the block, so clearing glow lichen off a pond's bank or a swamp
        oak's leaf at the waterline leaves the water, not a hole in the
        pond. The game's map passes over leaves, waterlogged or not, so the
        swamp's ground was the water under the leaf; once the leaf is water
        the surface is the ground."""
        self.save.fill((1, 61, 1), (6, 63, 6), "minecraft:water[level=0]")
        self.save.set_block(1, 63, 3, "minecraft:glow_lichen[waterlogged=true,"
                                      "west=true]")
        self.save.set_block(4, 63, 4, "minecraft:oak_leaves[distance=1,"
                                      "persistent=false,waterlogged=true]")
        self.save.set_block(4, 70, 4, "minecraft:oak_leaves[distance=1,"
                                      "persistent=false,waterlogged=false]")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.b._ground[(4, 4)] = 62        # where the game's map puts it
        self.assertEqual(self.b.clear_vegetation((0, 0), (7, 7)), 3)
        for cell in ((1, 63, 3), (4, 63, 4)):
            self.assertEqual(str(self.save.get_block(*cell)),
                             "minecraft:water[level=0]", f"the water at {cell}")
        self.assertEqual(self.save.get_block(4, 70, 4).name, "minecraft:air")
        self.assertEqual((self.b.ground(1, 3), self.b.ground(4, 4)), (63, 63))

    def test_clear_vegetation_stops_at_a_roof_over_the_ground(self):
        """Over a ground set beneath an overhang -- a pad under it, say --
        the roots hanging from the overhang are in the open column over
        that ground and go, and the grass growing on top of the overhang
        does not grow on this ground, so it stays."""
        self.save.fill((2, 72, 2), (6, 72, 6), "minecraft:stone")
        self.save.set_block(4, 71, 4, "minecraft:hanging_roots")
        self.save.set_block(4, 73, 4, "minecraft:short_grass")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.b._ground[(4, 4)] = 63        # as a script that shaped it records it
        self.assertEqual(self.b.clear_vegetation((4, 4), (4, 4)), 1)
        self.assertEqual(self.save.get_block(4, 71, 4).name, "minecraft:air")
        self.assertEqual(self.save.get_block(4, 73, 4).name,
                         "minecraft:short_grass", "on the roof, not the ground")
        self.assertEqual(self.b.ground(4, 4), 63)

    def test_clear_vegetation_lands_the_ground_where_the_games_map_would(self):
        """A rebuilt map is WORLD_SURFACE alone, which tops out on a torch,
        a rail or a carpet; the game's MOTION_BLOCKING_NO_LEAVES passes
        them, and so does the ground the Builder reads. Where a script set
        the ground on one, clearing the column leaves its ground on the
        block the game's map would name, and what stands on it where it
        was."""
        standing = {(2, 2): "minecraft:torch",
                    (4, 4): "minecraft:rail[shape=north_south,waterlogged=false]",
                    (6, 6): "minecraft:moss_carpet"}
        for (x, z), block in standing.items():
            self.save.set_block(x, 64, z, block)
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        for (x, z), block in standing.items():
            self.assertEqual(self.b.ground(x, z), 63, f"read under the {block}")
            self.b.set_ground(x, z, 64)     # as a script that built it recorded it
        self.assertEqual(self.b.clear_vegetation((0, 0), (8, 8)), 0)
        for (x, z), block in standing.items():
            self.assertEqual(self.b.ground(x, z), 63, block)
            self.assertEqual(self.save.get_block(x, 64, z).name,
                             block.split("[")[0])

    def test_clear_vegetation_fells_a_pale_oak_through_its_heart(self):
        """The game grows a creaking heart into a pale oak's trunk in place
        of a log. A trunk felled from its canopy down stopped at the heart,
        and the ground with it, two logs up a tree that was no longer
        there."""
        self.save.set_block(2, 63, 2, "minecraft:dirt")
        trunk = {64: "minecraft:pale_oak_log[axis=y]",
                 65: "minecraft:pale_oak_log[axis=y]",
                 66: "minecraft:creaking_heart[axis=y,"
                     "creaking_heart_state=dormant,natural=true]",
                 67: "minecraft:pale_oak_log[axis=y]",
                 68: "minecraft:pale_oak_log[axis=y]",
                 69: "minecraft:pale_oak_leaves[distance=1,persistent=false,"
                     "waterlogged=false]"}
        for y, block in trunk.items():
            self.save.set_block(2, y, 2, block)
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.assertEqual(self.b.clear_vegetation((2, 2), (2, 2)), len(trunk))
        self.assertEqual({self.save.get_block(2, y, 2).name for y in trunk},
                         {"minecraft:air"})
        self.assertEqual(self.b.ground(2, 2), 63)
        self.assertEqual(self.save.get_block(2, 63, 2).name,
                         "minecraft:grass_block")

    def test_clear_vegetation_keeps_what_hangs_under_an_overhang(self):
        """What a growing tree writes over takes in hanging roots and glow
        lichen, which cover cliffs and cave ceilings as much as the ground.
        A site is cleared over its ground: the flower on the cliff top and
        the lichen on its face, open to the sky, go; the roots, the lichen
        and the flower under a ledge, below the ground of their column,
        stay."""
        self.save.fill((2, 64, 2), (4, 72, 2), "minecraft:stone")    # the cliff
        self.save.set_block(5, 72, 2, "minecraft:stone")              # its ledge
        cleared = {(3, 73, 2): "minecraft:red_tulip",
                   (3, 68, 3): "minecraft:glow_lichen[north=true]"}
        kept = {(5, 71, 2): "minecraft:hanging_roots",
                (5, 66, 2): "minecraft:glow_lichen[west=true]",
                (5, 64, 2): "minecraft:red_tulip"}
        for cell, block in {**cleared, **kept}.items():
            self.save.set_block(*cell, block)
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.assertEqual(self.b.clear_vegetation((0, 0), (8, 8)), len(cleared))
        for cell in cleared:
            self.assertEqual(self.save.get_block(*cell).name, "minecraft:air",
                             f"{cleared[cell]} at {cell}")
        for cell, block in kept.items():
            self.assertEqual(self.save.get_block(*cell).name,
                             block.split("[")[0], f"under the ledge at {cell}")
        self.assertEqual(self.b.ground(3, 2), 72, "the cliff top")
        self.assertEqual(self.b.ground(3, 3), 63, "the foot of the cliff")
        self.assertEqual(self.b.ground(5, 2), 72, "the ledge")

    def test_shell_roof_fence_and_finish(self):
        self.b.level_pad(0, 0, 10, 8, 64, name="house")
        self.b.frame_walls(1, 1, 9, 7, 64, 4)
        self.b.clear_interior(1, 1, 9, 7, 64, 4)
        self.b.floor(2, 2, 8, 6, 64)
        self.b.gable_roof(1, 1, 9, 7, 68, "x")
        self.b.place(5, 65, 1, "minecraft:oak_door[facing=north]")
        self.b.place(3, 65, 3, "minecraft:blue_bed[facing=east]")
        self.b.window(4, 66, 1, "x")
        self.b.fence_rect(12, 0, 16, 6, gate=(12, 3, "west"))
        self.b.villager("librarian", 5, 5)
        self.b.spawn("minecraft:sheep", 14, 65, 3)

        report = self.b.finish()
        self.assertGreater(report.connections.blocks_scanned, 0)
        self.assertGreater(report.heightmaps.chunks_rebuilt, 0)
        self.assertGreater(report.lighting.chunks_reset, 0)
        self.assertEqual(report.poi_records, 0)

        # the aftercare really happened; the fence stands on unpadded
        # ground at 63, so its rails run at 64
        rail = self.save.get_block(14, 64, 0)
        self.assertEqual(rail.properties.get("east"), "true")
        self.assertEqual(rail.properties.get("west"), "true")
        ridge_y = self.save.surface_y(5, 4, "WORLD_SURFACE")
        self.assertGreaterEqual(ridge_y, 71, "the roof is on the heightmap")
        chunk = self.save.dimension(OVERWORLD).chunk(0, 0)
        self.assertEqual(int(chunk.data.get("isLightOn", 0)), 0)
        self.assertEqual(
            self.save.get_block(3, 65, 3).properties.get("part"), "foot"
        )
        self.assertEqual(
            self.save.get_block(4, 65, 3).properties.get("part"), "head"
        )
        entities = self.save.dimension(OVERWORLD).entity_chunk(0, 0)
        kinds = {str(e["id"]) for e in entities.data["Entities"]}
        self.assertIn("minecraft:villager", kinds)

    def test_finish_with_nothing_built_refuses(self):
        with self.assertRaises(NBTError):
            Builder(self.save).finish()

    def test_ornamental_tree_leaves_never_decay(self):
        self.b.ornamental_tree(20, 20)
        persistent = 0
        for y in range(64, 74):
            for dx in range(-2, 3):
                for dz in range(-2, 3):
                    b = self.save.get_block(20 + dx, y, 20 + dz)
                    if b is not None and b.name == "minecraft:oak_leaves":
                        self.assertEqual(b.properties.get("persistent"), "true")
                        persistent += 1
        self.assertGreater(persistent, 8)

    def test_an_ornamental_tree_leafs_into_cave_air(self):
        """Cave air is nothing, so a canopy grows into it exactly as it
        grows into the open: the same tree, leaf for leaf."""
        def canopy(x, z):
            return {(dx, y, dz)
                    for y in range(64, 74)
                    for dx in range(-2, 3) for dz in range(-2, 3)
                    if (b := self.save.get_block(x + dx, y, z + dz)) is not None
                    and b.name == "minecraft:oak_leaves"}

        Builder(self.save, seed=3).ornamental_tree(20, 20)
        self.save.fill((2, 64, 2), (6, 73, 6), "minecraft:cave_air")
        Builder(self.save, seed=3).ornamental_tree(4, 4)
        self.assertGreater(len(canopy(20, 20)), 8)
        self.assertEqual(canopy(4, 4), canopy(20, 20))

    def test_an_ornamental_tree_is_its_plan(self):
        """ornamental_tree is _tree_blocks' trunk and leaf, a trunk height
        drawn from the session's rng, and _tree_ops' plan of them over the
        world, committed whole and in its order: every op's cell holds its
        state afterwards, and the journal holds those cells. _tree_ops reads
        only through ``read``, so a planting planned before it and not yet
        written keeps the cells it holds; _tree_blocks refuses what
        ornamental_tree refuses, in its words, before anything is read; and
        the ``read`` ornamental_tree hands it is the Builder's own
        dimension, never the overworld's."""
        from heaton_voxel.craft.build.site import _tree_blocks

        trunk, leaf = _tree_blocks("cherry")
        self.assertEqual((trunk, leaf), ("minecraft:cherry_log[axis=y]",
                                         "minecraft:cherry_leaves[persistent=true]"))
        self.assertEqual(_tree_blocks("crimson"),
                         ("minecraft:crimson_stem[axis=y]",
                          "minecraft:nether_wart_block"))
        self.assertEqual(_tree_blocks("oak", "minecraft:azalea_leaves")[1],
                         "minecraft:azalea_leaves")
        twin = Builder(self.save, seed=3).rng
        height = 4 + twin.randrange(2)
        world = self.save.get_block
        ops = Builder._tree_ops(20, 64, 20, trunk, leaf, height, read=world)
        self.assertEqual(ops[:height], [("set", (20, 64 + i, 20), trunk)
                                        for i in range(height)])
        b = Builder(self.save, seed=3)
        committed = []
        commit = b._commit

        def spy(plan, **kwargs):
            committed.append(list(plan))
            return commit(plan, **kwargs)

        b._commit = spy
        b.ornamental_tree(20, 20, kind="cherry")
        self.assertEqual(committed, [ops])
        self.assertEqual(b.blocks_set, len(ops))
        self.assertEqual(set(b.journal.written()), {op[1] for op in ops})
        for _, cell, state in ops:
            self.assertEqual(self.save.get_block(*cell), BlockState.parse(state))
        self.assertEqual(b.rng.getstate(), twin.getstate(), "one draw")

        # Planned over a planting not yet written: its cells are taken.
        held = {(4 + dx, 68, 4): BlockState.parse("minecraft:stone")
                for dx in (-2, -1, 1, 2)}
        alone = Builder._tree_ops(4, 64, 4, trunk, leaf, 5, read=world)
        beside = Builder._tree_ops(
            4, 64, 4, trunk, leaf, 5,
            read=lambda *cell: held.get(cell) or self.save.get_block(*cell))
        self.assertEqual(beside, [op for op in alone if op[1] not in held])
        self.assertEqual(len(alone) - len(beside), 4)

        for kind, words in (("nope", "there is no nope_log"),
                            ("stripped_oak", "there is no stripped_oak_leaves")):
            with self.subTest(kind=kind):
                with self.assertRaises(NBTError) as direct:
                    _tree_blocks(kind)
                with self.assertRaises(NBTError) as planted:
                    self.b.ornamental_tree(9, 9, kind=kind)
                self.assertIn(words, str(direct.exception))
                self.assertEqual(str(planted.exception), str(direct.exception))
        self.assertEqual(self.b._probes, {}, "refused before a read")

        # The world a Builder plans over is its own dimension's: a nether
        # Builder's canopy grows round the netherrack the nether holds in
        # it, and into the cell where only the overworld has bricks. Both
        # cells are in the trunk's lowest five-by-five at either height.
        root = os.path.join(self.dir, "dimensions", "minecraft")
        shutil.copytree(os.path.join(root, "overworld"),
                        os.path.join(root, "the_nether"))
        save = MinecraftSave.load(self.dir)
        for dimension in (OVERWORLD, NETHER):
            save.fill((-16, 60, -16), (31, 63, 31), "minecraft:stone",
                      dimension=dimension)
            save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31),
                                    dimension=dimension)
        save.set_block(18, 67, 20, "minecraft:netherrack", dimension=NETHER)
        save.set_block(22, 67, 20, "minecraft:bricks")
        stem, wart = _tree_blocks("crimson")
        nether = Builder(save, seed=3, dimension=NETHER)
        height = 4 + Builder(save, seed=3).rng.randrange(2)
        ops = Builder._tree_ops(
            20, 64, 20, stem, wart, height,
            read=lambda x, y, z: save.get_block(x, y, z, dimension=NETHER))
        committed.clear()
        commit = nether._commit

        def spy(plan, **kwargs):
            committed.append(list(plan))
            return commit(plan, **kwargs)

        nether._commit = spy
        nether.ornamental_tree(20, 20, kind="crimson")
        self.assertEqual(committed, [ops])
        cells = {op[1] for op in ops}
        self.assertNotIn((18, 67, 20), cells, "the nether's netherrack stands")
        self.assertIn((22, 67, 20), cells, "the overworld's bricks are not here")
        self.assertEqual(str(save.get_block(18, 67, 20, dimension=NETHER)),
                         "minecraft:netherrack")
        self.assertEqual(str(save.get_block(22, 67, 20, dimension=NETHER)),
                         wart)
        self.assertEqual(save.get_block(22, 67, 20).name, "minecraft:bricks")
        self.assertEqual(save.get_block(20, 64, 20).name, "minecraft:air",
                         "nothing planted in the overworld")


class RoadResultTests(BuilderBase):
    """What ``road()`` gives back -- a :class:`RoadResult` -- and the fill it
    refuses as a dam unless told ``allow_dam=True``."""

    def setUp(self):
        super().setUp()
        # Stone deep enough to cut a ravine into: the ground stays at 63.
        self.save.fill((-16, 40, -16), (31, 59, 31), "minecraft:stone")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))

    def test_a_road_returns_what_it_laid(self):
        """A pond, a ridge twelve over the road, a hill three over it and
        a ravine ten deep, crossed by a road one wide: the result names the
        waypoints and width, the centerline, the height the road rides at
        each cell, the swath in the order it was paved -- each column at
        its stretch's height, which is the ground the session now knows --
        and what the ground called for, cell by cell and as stretches: a
        bridge over the water and the ravine, a tunnel through the ridge
        (cut open to the sky, as roads are) and a cut through the hill."""
        from heaton_voxel.craft import RoadResult, RoadRule, Stretch
        from heaton_voxel.craft.build.site import _swath

        self.save.fill((-8, 60, -16), (-7, 63, 31), "minecraft:water[level=0]")
        self.save.fill((-8, 59, -16), (-7, 59, 31), "minecraft:sand")
        self.save.fill((0, 64, -16), (1, 75, 31), "minecraft:stone")
        self.save.fill((10, 64, -16), (11, 66, 31), "minecraft:stone")
        self.save.fill((20, 54, -16), (21, 63, 31), "minecraft:air")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        points = [(-12, 4), (28, 4)]
        heights = [self.b.ground(x, 4) for x in range(-12, 29)]
        r = self.b.road(points, width=1, over_water="bridge", allow_dam=True)
        self.assertIsInstance(r, RoadResult)
        self.assertEqual(r.points, ((-12, 4), (28, 4)))
        self.assertEqual(r.width, 1)
        self.assertEqual(r.line, tuple(geometry.line(points)))
        self.assertEqual(r.heights, geometry.smoothed_profile(
            heights, window=geometry.ROAD_WINDOW))
        self.assertEqual(list(r.swath.items()), list(_swath(r.line, 0).items()))
        with self.assertRaises(TypeError):
            r.swath[(0, 0)] = 1                  # read-only
        for x, z in r.swath:
            self.assertEqual(r.top(x, z), self.b.ground(x, z), (x, z))
        with self.assertRaises(NBTError):
            r.top(0, 9)
        self.assertEqual(r.heights[12], 66, "the ridge's foot, over 75")
        runs = [(kind, r.line[start][0], r.line[end][0])
                for kind, start, end in r.stretches]
        self.assertEqual(runs, [
            ("grade", -12, -9), ("bridge", -8, -7), ("grade", -6, -1),
            ("tunnel", 0, 1), ("grade", 2, 9), ("cut", 10, 11),
            ("grade", 12, 15), ("cut", 16, 19), ("bridge", 20, 21),
            ("cut", 22, 25), ("grade", 26, 28)])
        self.assertTrue(all(isinstance(s, Stretch) for s in r.stretches))
        self.assertEqual(r.kinds, tuple(kind for kind, start, end in r.stretches
                                        for _ in range(start, end + 1)))
        self.assertEqual(RoadRule.stretches(r.kinds), r.stretches)
        self.assertEqual(self.b._paved, set(r.swath))
        # The ridge was cut open: the tunnel is a kind reported, not built.
        self.assertEqual(self.save.get_block(0, 70, 4).name, "minecraft:air")

    def test_a_high_anchor_is_a_step_the_result_shows(self):
        """The smoothing only lowers, so an anchor above the ground stands a
        step over its neighbors rather than being ramped up to -- here
        seven blocks, which ``heights`` shows -- and the fill under it is
        a dam until ``allow_dam=True``."""
        points = [(0, 4), (12, 4)]
        with self.assertRaises(Refused):
            self.b.road(points, width=1, anchors={(6, 4): 70})
        r = self.b.road(points, width=1, anchors={(6, 4): 70}, allow_dam=True)
        self.assertEqual(r.heights, (63,) * 6 + (70,) + (63,) * 6)
        self.assertEqual(r.kinds[6], "bridge")
        self.assertEqual(self.b.ground(6, 4), 70)

    def test_a_deep_fill_is_a_dam_unless_allowed(self):
        """A ravine two wide and ten deep across a road three wide: the
        road rides at 60 over it, on six blocks of fill -- more than the
        rule's ``shallow_fill`` of 3 -- so the road is refused, one fault
        for the stretch, naming it, the depth and the deepest column, with
        nothing written, drawn or recorded. ``allow_dam=True`` fills it,
        and reports the stretch a bridge; so does a rule whose
        ``shallow_fill`` takes six."""
        from heaton_voxel.craft import RoadRule, Stretch

        self.save.fill((10, 54, -16), (11, 63, 31), "minecraft:air")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        points = [(0, 4), (24, 4)]
        rng = self.b.rng.getstate()
        with self.assertRaises(Refused) as caught:
            self.b.road(points)
        self.assertEqual(caught.exception.faults, [
            "the stretch from (9, 4) to (10, 4) would stand on an embankment "
            "6 blocks deep at (10, 3), more than shallow_fill=3: a bridge "
            "belongs there"])
        message = str(caught.exception)
        for words in ("road would dam the ground", "allow_dam=True",
                      "Nothing was written"):
            self.assertIn(words, message)
        self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(list(self.b.journal.written()), [])
        self.assertEqual(self.b.rng.getstate(), rng)
        self.assertEqual(self.b._paved, set())
        self.assertEqual(self.save.get_block(10, 58, 4).name, "minecraft:air")

        r = self.b.road(points, allow_dam=True)
        self.assertIn(Stretch("bridge", 9, 10), r.stretches)
        self.assertEqual({column for column, i in r.swath.items()
                          if r.kinds[i] == "bridge"},
                         {(x, z) for x in (10, 11) for z in (3, 4, 5)})
        self.assertEqual(self.b.ground(10, 4), 60)
        self.assertEqual(self.save.get_block(10, 54, 4).name, "minecraft:dirt")
        # Whatever follows the road along its result stands beside it.
        lamps = self.b.line_lamps(r.points, width=r.width,
                                  post="minecraft:cobblestone_wall",
                                  lamp="minecraft:lantern",
                                  light_level=NO_SPAWNS)
        self.assertTrue(lamps.posts)
        self.assertFalse({(x, z) for x, _, z in lamps.posts} & set(r.swath))

        other = Builder(self.fresh_ravine(), seed=7)
        r = other.road(points, rule=RoadRule(shallow_fill=6))
        self.assertEqual(r.kinds[9:11], ("grade", "grade"),
                         "six blocks is shallow there")
        self.assertNotIn("bridge", r.kinds)
        with self.assertRaises(NBTError):
            other.road(points, rule=3)
        for bad in ({"shallow_fill": -1}, {"tunnel_from": 0},
                    {"skylight": 1.5}, {"shallow_fill": True}):
            with self.subTest(**bad):
                with self.assertRaises(NBTError):
                    RoadRule(**bad)

    def test_a_side_hill_fill_is_a_dam_the_rule_calls_a_cut(self):
        """A road three wide along a slope: a ridge three over it on one
        side, a trench six under its bed on the other. The rule asks about
        the hill first and calls every cell a cut, and the fill on the low
        side is refused as a dam all the same -- its fault saying it is a
        side-hill, not that a bridge was called for. ``allow_dam=True``
        builds the embankment, and the stretches still say cut."""
        from heaton_voxel.craft import Stretch

        self.save.fill((-16, 64, 3), (31, 66, 3), "minecraft:stone")
        self.save.fill((-16, 57, 5), (31, 63, 5), "minecraft:air")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        points = [(0, 4), (20, 4)]
        with self.assertRaises(Refused) as caught:
            self.b.road(points)
        self.assertEqual(caught.exception.faults, [
            "the stretch from (0, 4) to (20, 4) would stand on an embankment "
            "6 blocks deep at (-1, 5), more than shallow_fill=3: a side-hill, "
            "ground over the road beside it making it a cut, and the fill on "
            "its low side a dam all the same"])
        self.assertEqual(self.b.blocks_set, 0)
        r = self.b.road(points, allow_dam=True)
        self.assertEqual(r.stretches, (Stretch("cut", 0, 20),))
        self.assertEqual(self.save.get_block(10, 57, 5).name, "minecraft:dirt")

    def test_a_wide_road_is_judged_by_every_column_it_cuts(self):
        """Each cell of a road three wide is judged by every column its
        stretch cuts, edges included: one column of water at an edge makes
        it a bridge, one boulder two over the road at an edge a cut. The
        road runs toward -x, so the swath it hands back is in the order it
        paved, not sorted. Across a valley whose floor falls two further
        at one edge column of the second of two deep stretches, the dam's
        fault names that column and its depth -- the deepest -- not the
        first deep column it met."""
        from heaton_voxel.craft.build.site import _swath

        self.save.fill((17, 61, 5), (17, 63, 5), "minecraft:water[level=0]")
        self.save.fill((12, 64, 5), (12, 65, 5), "minecraft:stone")
        self.save.fill((5, 54, 3), (6, 63, 5), "minecraft:air")
        self.save.fill((5, 52, 5), (5, 53, 5), "minecraft:air")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        points = [(24, 4), (0, 4)]
        with self.assertRaises(Refused) as caught:
            self.b.road(points, over_water="bridge")
        self.assertEqual(caught.exception.faults, [
            "the stretch from (7, 4) to (6, 4) would stand on an embankment "
            "8 blocks deep at (5, 5), more than shallow_fill=3: a bridge "
            "belongs there"])
        r = self.b.road(points, over_water="bridge", allow_dam=True)
        kinds = {r.line[i][0]: kind for i, kind in enumerate(r.kinds)}
        self.assertEqual(kinds[18], "bridge", "its new edge column is wet")
        self.assertEqual((kinds[19], kinds[17]), ("grade", "grade"))
        self.assertEqual(kinds[13], "cut", "its new edge column is high")
        self.assertEqual((kinds[14], kinds[12]), ("grade", "grade"))
        self.assertEqual(list(r.swath.items()), list(_swath(r.line, 1).items()))
        self.assertNotEqual(list(r.swath), sorted(r.swath))
        self.assertEqual(list(r.swath)[:4], [(23, 3), (23, 4), (23, 5), (24, 3)])

    def test_a_road_filled_across_water_is_a_dam(self):
        """A canal four wide, running north and south through the stone,
        filled across from its bed by a road: the water north of the road
        and the water south of it, joined through the fill today, would be
        joined nowhere else -- a dam, refused with the road's other dams,
        naming both, with nothing written. A jetty that stops inside the
        canal leaves the water joined round its end and is built, and
        ``allow_dam=True`` fills the crossing."""
        self.save.fill((10, 60, -12), (13, 63, 28), "minecraft:water[level=0]")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        rng = self.b.rng.getstate()
        with self.assertRaises(Refused) as caught:
            self.b.road([(0, 4), (24, 4)], over_water="fill")
        self.assertEqual(caught.exception.faults, [
            "the fill at (10, 60, 3) cuts the water in two: (10, 60, 2) and "
            "(10, 60, 6), joined through it today, are joined nowhere else, "
            "so the water on one side is a pond the other side no longer "
            "feeds"])
        message = str(caught.exception)
        for words in ("road would dam", "allow_dam=True", "Nothing was written"):
            self.assertIn(words, message)
        self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(self.b.rng.getstate(), rng)
        self.assertTrue(self.wet(11, 62, 4))

        self.b.road([(0, 20), (11, 20)], over_water="fill")
        self.assertEqual(self.save.get_block(11, 60, 20).name, "minecraft:dirt")
        self.assertTrue(self.wet(13, 62, 20), "the jetty's end")
        r = self.b.road([(0, 4), (24, 4)], over_water="fill", allow_dam=True)
        self.assertEqual(self.b.ground(12, 4), r.top(12, 4))
        self.assertFalse(self.wet(12, 61, 4))

        # Water followed into a chunk that is not there cannot be said to
        # join or not: a canal running off the world's generated edge is
        # refused as water that could not be followed far enough.
        self.save.fill((20, 60, -16), (21, 63, 28), "minecraft:water[level=0]")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        with self.assertRaises(Refused) as caught:
            self.b.road([(16, 10), (26, 10)], over_water="fill")
        self.assertEqual(caught.exception.faults, [
            "the water beside the fill at (20, 60, 9) could not be followed "
            "far enough to see whether the fill cuts it in two: it reaches "
            "chunks that are not there to read, (1, -2)"])

        # Two ponds the road runs along the edge of, each left joined round
        # the strip filled out of it: a fill is judged piece by piece, and
        # water either side of the road that was never joined through it
        # is no split.
        directory = tempfile.mkdtemp(prefix="build-tests-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        make_save(directory, CHUNKS)
        save = MinecraftSave.load(directory)
        save.fill((-16, 40, -16), (31, 63, 31), "minecraft:stone")
        for x0 in (4, 14):
            save.fill((x0, 60, 4), (x0 + 4, 63, 10), "minecraft:water[level=0]")
        save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        Builder(save, seed=7).road([(0, 4), (24, 4)], over_water="fill")
        for x in (6, 16):
            self.assertEqual(save.get_block(x, 60, 5).name, "minecraft:dirt")
            block = save.get_block(x, 62, 8)
            self.assertEqual(block.name, "minecraft:water", "the pond beyond")

    def test_a_road_filled_across_a_ditch_of_still_water_is_a_dam(self):
        """The commonest creek crossing: a still canal one wide and one
        deep, filled across by a road three wide. The fill is one piece
        with two cells of water beside it, one each side, and those two,
        joined through it today, are joined nowhere else: a dam, refused,
        though no flow is starved -- every cell of the canal is a source."""
        self.save.fill((10, 63, -5), (10, 63, 14), "minecraft:water[level=0]")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        with self.assertRaises(Refused) as caught:
            self.b.road([(0, 4), (20, 4)], over_water="fill")
        self.assertEqual(caught.exception.faults, [
            "the fill at (10, 63, 3) cuts the water in two: (10, 63, 2) and "
            "(10, 63, 6), joined through it today, are joined nowhere else, "
            "so the water on one side is a pond the other side no longer "
            "feeds"])
        self.assertEqual(self.b.blocks_set, 0)
        self.assertTrue(self.wet(10, 63, 4))

    def test_a_causeway_a_block_over_a_stream_leaves_the_stream_under_it(self):
        """A stream one wide in a trench, running south from its source,
        crossed by a road one wide bridged a block over its waterline: the
        causeway stands on the water, and the water stays under it. The
        fill under a road's surface starts a block over the waterline, so
        there is none to lay; the fill's box, upside down, once sorted into
        the waterline and put dirt in the stream, starving everything
        downstream of the road with no refusal, since water the road takes
        out that way was never counted as taken."""
        self.save.fill((8, 63, 2), (8, 63, 9), "minecraft:air")
        self.save.set_block(8, 62, 2, "minecraft:water[level=0]")
        for level, z in enumerate(range(3, 10), start=1):
            self.save.set_block(8, 62, z, f"minecraft:water[level={level}]")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        stream = [str(self.save.get_block(8, 62, z)) for z in range(2, 10)]
        r = self.b.road([(0, 5), (16, 5)], width=1, over_water="bridge")
        self.assertEqual(r.top(8, 5), 63, "a block over the waterline")
        self.assertEqual(r.kinds[8], "bridge")
        self.assertIn(self.save.get_block(8, 63, 5).name,
                      {name for name, _ in ROAD_MIX}, "the causeway")
        self.assertEqual([str(self.save.get_block(8, 62, z))
                          for z in range(2, 10)], stream,
                         "the stream runs on under it, every level kept")

    def test_a_road_cut_beside_water_is_refused_unless_sealed(self):
        """A road anchored two below the ground beside a pond cuts its
        channel down past the pond's waterline, and the pond would run
        into it the first time anything near it updated: refused, naming
        every cell the water would enter and the water it comes from, with
        nothing written. ``seal=`` walls that water off where it stands,
        and written, no water stands beside the road's air."""
        self.save.fill((3, 61, 6), (9, 63, 9), "minecraft:water[level=0]")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        points, anchors = [(0, 4), (12, 4)], {(6, 4): 61}
        with self.assertRaises(Refused) as caught:
            self.b.road(points, anchors=anchors)
        leaks = caught.exception.faults
        cells = {leak.cell for leak in leaks}
        # Each swath column is cut to its first stretch's height: the
        # anchor's 61 lands a column on, at x=7, and 62 either side of it.
        self.assertEqual(cells, {(6, 63, 5), (7, 62, 5), (7, 63, 5),
                                 (8, 63, 5)})
        self.assertEqual({leak.source for leak in leaks},
                         {(x, y, 6) for x, y, _ in cells})
        self.assertIn("seal='minecraft:cobblestone'", str(caught.exception))
        self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(self.b._paved, set())

        # The seals stand off the swath, in the pond, and a keep-out there
        # binds them as it binds the road: refused, naming it, until
        # through= does.
        self.b.reserve(3, 6, 9, 9, name="pond")
        with self.assertRaises(Refused) as caught:
            self.b.road(points, anchors=anchors, seal="minecraft:cobblestone")
        message = str(caught.exception)
        self.assertIn("road's seals would stand in", message)
        self.assertIn("reservation 'pond' at (6, 6)", message)
        self.assertEqual(self.b.blocks_set, 0)
        r = self.b.road(points, anchors=anchors, seal="minecraft:cobblestone",
                        through=("pond",))
        self.assertEqual(r.heights[6], 61)
        for x, y, _ in cells:
            self.assertEqual(self.save.get_block(x, y, 6).name,
                             "minecraft:cobblestone")
        for (x, z) in r.swath:
            for y in range(r.top(x, z) + 1, r.top(x, z) + 5):
                if self.save.get_block(x, y, z).name != "minecraft:air":
                    continue
                for side in ((x + 1, y, z), (x - 1, y, z), (x, y, z + 1),
                             (x, y, z - 1)):
                    self.assertFalse(self.wet(*side), (side, (x, y, z)))
        # A seal that would not stay a wall is refused before a read.
        with self.assertRaises(NBTError):
            self.b.road([(0, 12), (12, 12)], seal="minecraft:gravel")

    def wet(self, x, y, z):
        from heaton_voxel.craft.block_facts import holds_water

        block = self.save.get_block(x, y, z)
        return holds_water(block.name, block.properties)

    def fresh_ravine(self):
        """Another world with the same ravine."""
        directory = tempfile.mkdtemp(prefix="build-tests-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        make_save(directory, CHUNKS)
        save = MinecraftSave.load(directory)
        save.fill((-16, 40, -16), (31, 63, 31), "minecraft:stone")
        save.fill((10, 54, -16), (11, 63, 31), "minecraft:air")
        save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        return save

    def test_road_and_pave_record_what_they_paved(self):
        """A road's swath and a pave's rectangle are recorded, once they are
        written, as the session's paths -- record-only, so the bytes are
        what they were -- and a refused call records nothing."""
        r = self.b.road([(0, 4), (12, 8)], width=3)
        self.assertEqual(self.b._paved, set(r.swath))
        self.b.pave(0, 20, 5, 24, 63, light=None)
        self.assertEqual(self.b._paved, set(r.swath) | {
            (x, z) for x in range(0, 6) for z in range(20, 25)})
        before = set(self.b._paved)
        with self.assertRaises(Refused):
            self.b.pave(20, 20, 40, 24, 63, light=None)    # off the world
        with self.assertRaises(Refused):
            self.b.road([(20, 0), (40, 0)])
        self.assertEqual(self.b._paved, before)
        # Recorded once the write is in, and not when the plan's own check
        # refuses it: an anchor past the top of the world is refused by
        # _commit, after every check of the road's own has passed, and a
        # pave refused there records nothing either.
        with self.assertRaises(Refused) as caught:
            self.b.road([(0, 12), (12, 12)], width=1, anchors={(6, 12): 400},
                        allow_dam=True)
        self.assertIn("past the world", str(caught.exception))
        from unittest import mock

        with mock.patch.object(self.b, "_commit",
                               side_effect=Refused("refused at the commit")):
            with self.assertRaises(Refused):
                self.b.pave(10, 20, 14, 24, 63, light=None)
            with self.assertRaises(Refused):
                self.b.pave(10, 20, 14, 24, 63, light_level=MELT_SNOW)
        self.assertEqual(self.b._paved, before)


class VillagerTests(BuilderBase):
    """A villager's type is the game's for where it stands.

    The game types a villager by the biome at its feet when it first
    spawns (``VillagerType.byBiome``), and takes the type a save hands it
    as final -- so a villager written into the snow as ``plains`` stays a
    plains villager there for good."""

    def types(self, save=None, dimension=None) -> dict:
        """``{(x, z): VillagerData type}`` of every villager in the world
        (the builder's, or ``save``; the overworld, or ``dimension``),
        keyed by the block each stands in -- floored, so a villager at
        ``-0.5`` is the one standing in block ``-1``."""
        found = (save or self.save).entities_in(
            (-16, -64, -16), (47, 319, 47), ids="villager",
            dimension=dimension)
        return {(math.floor(x), math.floor(z)): str(tag["VillagerData"]["type"])
                for _, x, _, z, tag in found.entities}

    def test_a_villager_takes_its_type_from_the_biome_it_stands_in(self):
        """Desert in the desert and the badlands, snow on snowy plains and in
        a snowy taiga, a swamp villager in a mangrove swamp -- each read from
        the 4x4x4 cell its feet stand in (64..67 over ground at 63), not the
        ground's.

        West and north of the origin the feet are at ``-0.5``: floored, the
        cell at -4..-1, in chunk ``(-1, -1)``; truncated toward zero, the
        cell across ``x = 0`` or ``z = 0``, in another chunk, which here
        says savanna or desert."""
        stands = {(1, 1): "minecraft:desert", (5, 1): "minecraft:badlands",
                  (9, 1): "minecraft:snowy_plains",
                  (13, 1): "minecraft:mangrove_swamp",
                  (-1, -1): "minecraft:snowy_taiga"}
        for (x, z), biome in stands.items():
            self.save.fill_biome((x, 64, z), (x, 67, z), biome)
            # The ground's cell under it says something else entirely.
            self.save.fill_biome((x, 60, z), (x, 63, z), "minecraft:jungle")
        # The cells a truncated -0.5 would read instead, beside (1, 1)'s.
        self.save.fill_biome((0, 64, -1), (0, 67, -1), "minecraft:savanna")
        self.save.fill_biome((-1, 64, 0), (-1, 67, 0), "minecraft:savanna")
        for x, z in stands:
            self.b.villager("farmer", x, z)
        self.assertEqual(self.types(), {
            (1, 1): "minecraft:desert", (5, 1): "minecraft:desert",
            (9, 1): "minecraft:snow", (13, 1): "minecraft:swamp",
            (-1, -1): "minecraft:snow"})

    def test_a_builder_in_another_dimension_reads_that_dimensions_biome(self):
        """The same x, z in two dimensions is two places. A builder in the
        nether types its villager by the nether's biome at its feet -- a
        nether waste, which the game's table does not name, so plains --
        and not by the snowfield the overworld holds at the same column,
        where the overworld's builder makes a snow villager."""
        root = os.path.join(self.dir, "dimensions", "minecraft")
        shutil.copytree(os.path.join(root, "overworld"),
                        os.path.join(root, "the_nether"))
        save = MinecraftSave.load(self.dir)
        for dimension in (OVERWORLD, NETHER):
            save.fill((-16, 60, -16), (31, 63, 31), "minecraft:stone",
                      dimension=dimension)
            save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31),
                                    dimension=dimension)
        save.fill_biome((2, 64, 2), (2, 67, 2), "minecraft:snowy_plains")
        save.fill_biome((2, 64, 2), (2, 67, 2), "minecraft:nether_wastes",
                        dimension=NETHER)
        Builder(save, dimension=NETHER).villager("farmer", 2, 2)
        Builder(save).villager("farmer", 2, 2)
        self.assertEqual(self.types(save, NETHER), {(2, 2): "minecraft:plains"})
        self.assertEqual(self.types(save), {(2, 2): "minecraft:snow"})

    def test_a_biome_the_table_does_not_name_gives_the_games_default(self):
        """A forest or a river names no type in the game's table, and
        neither does a datapack's biome: ``byBiome`` falls back on
        ``VillagerData.DEFAULT_TYPE``, which is plains, read from the
        game rather than assumed."""
        default = fixtures.villager_types().default
        self.assertEqual(default, "minecraft:plains")
        self.assertEqual(biomes.VILLAGER_TYPE_DEFAULT, default)
        stands = {(1, 5): "minecraft:forest", (5, 5): "minecraft:river",
                  (9, 5): "example:datapack_glacier"}
        for (x, z), biome in stands.items():
            self.save.fill_biome((x, 64, z), (x, 67, z), biome)
            self.assertEqual(biomes.villager_type(biome), default, biome)
            self.b.villager("farmer", x, z)
        self.assertEqual(self.types(), {cell: default for cell in stands})

    def test_a_villager_kind_the_game_lacks_is_refused(self):
        """An explicit kind overrides the biome, in either spelling; one
        the game lacks -- which it would load as plains -- is refused by
        name, listing the seven it has, before the ground or the chunk is
        read and before anything is written."""
        self.save.fill_biome((1, 64, 1), (1, 67, 1), "minecraft:desert")
        for bad in ("taigaa", "minecraft:pale_garden", "frozen"):
            with self.subTest(kind=bad), self.assertRaises(NBTError) as caught:
                self.b.villager("farmer", 1, 1, kind=bad)
            self.assertNotIsInstance(caught.exception, Refused)
            self.assertIn(repr(bad), str(caught.exception))
            self.assertIn(
                "desert, jungle, plains, savanna, snow, swamp, taiga",
                str(caught.exception))
        with self.assertRaises(NBTError) as caught:
            self.b.villager("farmer", 40, 4, kind="taigaa")
        self.assertIn("'taigaa'", str(caught.exception),
                      "refused by its kind, not by the ungenerated chunk")
        self.assertEqual(self.b.entities_added, 0)
        self.assertEqual(self.types(), {})

        self.b.villager("farmer", 1, 1, kind="snow")
        self.b.villager("farmer", 5, 1, kind="minecraft:taiga")
        self.assertEqual(self.types(), {(1, 1): "minecraft:snow",
                                        (5, 1): "minecraft:taiga"})

    def test_a_spot_that_records_no_biome_is_refused_rather_than_guessed(self):
        """Where the section the feet stand in carries no biome, the type
        the game would give is unknown: refused, nothing spawned, and
        ``kind=`` is the way past it."""
        directory = tempfile.mkdtemp(prefix="build-tests-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        make_save(directory, [(0, 0)])
        save = MinecraftSave.load(directory)
        for section in save.dimension(OVERWORLD).chunk(0, 0).data["sections"]:
            if int(section["Y"]) == 4:          # y 64..79, the feet
                del section["biomes"]
        save.fill((0, 60, 0), (15, 63, 15), "minecraft:stone")
        save.rebuild_heightmaps((0, 0, 0), (15, 0, 15))
        b = Builder(save)
        with self.assertRaises(NBTError) as caught:
            b.villager("farmer", 4, 4)
        self.assertIn("no biome is recorded at (4, 64, 4)",
                      str(caught.exception))
        self.assertEqual(b.entities_added, 0)
        b.villager("farmer", 4, 4, kind="plains")
        self.assertEqual(b.entities_added, 1)


class PaveTests(BuilderBase):
    """Paving and the light it carries.

    The promise is not "lights were placed on a nice pattern" -- it is
    that **every** cell ends lit. So every test here recomputes that from
    the blocks the builder actually wrote, by the game's own flood over the
    world as it now stands (``simulate_block_light``, counting only the
    light that lasts): the cell over each floor cell, where a walker's feet
    are, must read at least the level asked for.
    """

    GLOWSTONE = "minecraft:glowstone"

    def lamps(self, x0, z0, x1, z1, y0, y1=None):
        """Every light the builder actually wrote, read back from the world."""
        found = []
        for y in range(y0, (y1 if y1 is not None else y0) + 1):
            for z in range(z0, z1 + 1):
                for x in range(x0, x1 + 1):
                    block = self.save.get_block(x, y, z)
                    if block is not None and block.name == self.GLOWSTONE:
                        found.append((x, y, z))
        return found

    def assertLit(self, cells, lamps, *, light_level=MELT_LIGHT):
        """Every floor cell of ``cells`` has the cell over it lit to
        ``light_level`` by the flood -- whichever lamps did it, these or an
        earlier call's."""
        self.assertTrue(lamps, "nothing was lit at all")
        xs, ys, zs = zip(*cells)
        field = simulate_block_light(
            self.save, (min(xs), min(ys) + 1, min(zs)),
            (max(xs), max(ys) + 1, max(zs)), durable=True)
        for x, y, z in cells:
            level = field.level(x, y + 1, z)
            self.assertGreaterEqual(
                level, light_level,
                f"the cell over ({x}, {y}, {z}) is lit to {level}, under the "
                f"{light_level} the floor was asked for")

    def test_a_lamp_plan_keeps_a_limited_cell_under_its_limit(self):
        """``_plan_lamps(at_most=)`` is ``plan_lights``' ceiling, passed
        through: a strip lit to 10 beside a lid held at 5 gets its lamps
        only where their light cannot reach the lid past 5 -- here the two
        ends, as far as the taxicab bound allows -- and the strip is still
        lit. Without it the lattice stands a lamp mid-strip."""
        wanted = {(x, 4): 63 for x in range(0, 9)}
        lid = (4, 64, 9)
        fixtures = dict.fromkeys(wanted, self.GLOWSTONE)

        def plan(**kwargs):
            return self.b._plan_lamps(wanted, 10, 5, 4, lambda c: wanted[c],
                                      fixtures, **kwargs)

        free = plan()
        self.assertTrue(any(1 <= x <= 7 for x, _, _ in free.lamps), free.lamps)
        held = plan(at_most={lid: 5})
        self.assertEqual((held.dark, held.over), ([], []))
        self.assertTrue(held.lamps)
        self.assertTrue(all(x in (0, 8) for x, _, _ in held.lamps), held.lamps)
        self.assertEqual(sorted(held.excluded),
                         [(x, 63, 4) for x in range(1, 8)])
        self.assertEqual(self.b.blocks_set, 0, "a plan writes nothing")

    def test_every_paved_cell_ends_inside_the_light_budget(self):
        result = self.b.pave(0, 0, 9, 9, y=63, light_level=MELT_SNOW)
        self.assertEqual(result.cells, 100)
        self.assertEqual(result.light_level, MELT_LIGHT)
        self.assertEqual(result.trim, 0)
        self.assertEqual(result.snow_cleared, 0)
        lamps = self.lamps(0, 0, 9, 9, 63)
        self.assertEqual(len(lamps), result.lights,
                         "the report counts the lamps that reached the world")
        self.assertLit([(x, 63, z) for z in range(10) for x in range(10)], lamps)
        self.assertIn("100 cells", repr(result))

    def test_a_floor_in_the_top_layer_of_the_world_is_lit(self):
        """The cell a walker on the top layer stands in is past the build
        limit, in the section the game keeps light in all the same: a floor
        laid there, or lit there, is lit by the lamps set into it -- not
        refused as dark, and not certified by a flood that reads it shut."""
        result = self.b.pave(0, 0, 9, 9, y=319, light_level=MELT_SNOW)
        self.assertEqual(result.cells, 100)
        lamps = self.lamps(0, 0, 9, 9, 319)
        self.assertEqual(len(lamps), result.lights)
        self.assertLit([(x, 319, z) for z in range(10) for x in range(10)], lamps)
        strip = [(x, 319, 14) for x in range(10)]
        result = self.b.light_cells(strip, light_level=MELT_SNOW)
        self.assertLit(strip, self.lamps(0, 14, 9, 14, 319))

    def test_the_surface_is_mixed_rather_than_one_flat_texture(self):
        self.b.pave(0, 0, 9, 9, y=63, mix=PLAZA_MIX, light_level=MELT_SNOW)
        names = {self.save.get_block(x, 63, z).name
                 for z in range(10) for x in range(10)}
        allowed = {name for name, _ in PLAZA_MIX} | {self.GLOWSTONE}
        self.assertTrue(names <= allowed, names - allowed)
        self.assertGreater(len(names), 2, "a mix, not a single block")

    def test_two_adjoining_paves_leave_no_dark_seam(self):
        """The lattice is phase-locked to world coordinates, so the second
        rectangle continues the first one's pattern instead of restarting
        it -- and the join is lit like everywhere else."""
        first = self.b.pave(0, 0, 9, 9, y=63, light_level=MELT_SNOW)
        second = self.b.pave(10, 0, 19, 9, y=63, light_level=MELT_SNOW)
        lamps = self.lamps(0, 0, 19, 9, 63)
        self.assertEqual(len(lamps), first.lights + second.lights)
        self.assertLit([(x, 63, z) for z in range(10) for x in range(20)], lamps)
        seam = [(x, 63, z) for z in range(10) for x in (8, 9, 10, 11)]
        self.assertLit(seam, lamps)

    def test_a_second_pave_verifies_against_the_first_calls_lights(self):
        """One row of this lattice's phase carries no lattice point at all.
        Paved on its own it is lit entirely by the verification pass; paved
        inside ground the same session already lit, it needs no lamp of its
        own -- which is only true if the earlier lamps were consulted."""
        self.b.pave(0, 0, 9, 9, y=63, light_level=MELT_SNOW)
        inside = self.b.pave(0, 1, 9, 1, y=63, light_level=MELT_SNOW)
        self.assertEqual(inside.lights, 0)
        self.assertEqual(inside.patched, 0)
        lamps = self.lamps(0, 0, 9, 9, 63)
        self.assertLit([(x, 63, 1) for x in range(10)], lamps)

        alone = Builder(self.save, seed=7).pave(0, 21, 9, 21, y=63,
                                                light_level=MELT_SNOW)
        self.assertGreater(alone.patched, 0,
                           "with no lamp near it the row lights itself")
        self.assertEqual(alone.lights, alone.patched)

    def test_an_irregular_region_forces_the_verification_pass_to_patch(self):
        """A lattice alone leaves dark cells on odd shapes; the check is
        what makes the guarantee real rather than decorative."""
        cells = [(x, 1) for x in range(9)] + [(1, z) for z in range(1, 9)]
        result = self.b.light_cells(cells, light_level=MELT_SNOW)
        self.assertEqual(result.cells, len(set(cells)))
        self.assertGreater(result.patched, 0)
        self.assertEqual(result.lights, result.patched,
                         "this L sits entirely off the lattice phase")
        lamps = self.lamps(0, 0, 9, 9, 63)
        self.assertLit([(x, 63, z) for x, z in set(cells)], lamps)

    def test_cells_without_a_y_stand_on_the_current_ground(self):
        result = self.b.light_cells([(4, 4), (5, 5)], light_level=MELT_SNOW)
        self.assertEqual(result.cells, 2)
        self.assertEqual(self.save.get_block(4, 63, 4).name, self.GLOWSTONE)

    def test_paving_follows_the_ground_when_no_height_is_given(self):
        """Over a terrace that already steps, y=None is what you want -- and
        the light check has to reckon with lamps at two heights."""
        self.save.fill((5, 64, 0), (9, 64, 9), "minecraft:stone")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        builder = Builder(self.save, seed=7)
        result = builder.pave(0, 0, 9, 9, light_level=MELT_SNOW)
        self.assertEqual(result.cells, 100)
        low = self.save.get_block(2, 63, 2)
        high = self.save.get_block(7, 64, 7)
        self.assertNotEqual(low.name, "minecraft:air")
        self.assertNotEqual(high.name, "minecraft:air")
        self.assertEqual(self.save.get_block(7, 63, 7).name, "minecraft:stone",
                         "the step was surfaced, not leveled")
        cells = [(x, 64 if x >= 5 else 63, z)
                 for z in range(10) for x in range(10)]
        self.assertLit(cells, self.lamps(0, 0, 9, 9, 63, 64))

    def test_trim_paints_the_border_ring_only(self):
        trim = "minecraft:gold_block"
        result = self.b.pave(20, 20, 26, 26, y=63, trim=trim,
                             light_level=MELT_SNOW)
        self.assertEqual(result.trim, 24, "the perimeter of a 7x7")
        for z in range(20, 27):
            for x in range(20, 27):
                name = self.save.get_block(x, 63, z).name
                edge = x in (20, 26) or z in (20, 26)
                if edge:
                    self.assertIn(name, (trim, self.GLOWSTONE), (x, z))
                else:
                    self.assertNotEqual(name, trim, (x, z))
        self.assertIn("24 trim", repr(result))

    def test_snow_lying_on_the_paving_is_cleared(self):
        """A floor that melts itself in-game but ships covered looks wrong
        until someone walks past it."""
        self.save.fill((0, 64, 0), (4, 64, 4), "minecraft:snow[layers=2]")
        result = self.b.pave(0, 0, 4, 4, y=63, light_level=MELT_SNOW)
        self.assertEqual(result.snow_cleared, 25)
        self.assertEqual(self.save.get_block(2, 64, 2).name, "minecraft:air")
        self.assertIn("25 melted", repr(result))

    def test_clearing_the_cover_can_be_declined(self):
        """Snow left lying on the paving shuts out the light of the lamp
        under it -- a snow layer closes its underside -- so a floor that
        keeps its snow is one its lights cannot light, refused before a
        block is written or a die cast. Ice lets the light through, and is
        kept."""
        self.save.fill((0, 64, 0), (4, 64, 4), "minecraft:snow[layers=2]")
        before = self.b.rng.getstate()
        with self.assertRaises(Refused) as caught:
            self.b.pave(0, 0, 4, 4, y=63, clear_above=False,
                        light_level=MELT_SNOW)
        self.assertIn("clear_above=True", str(caught.exception))
        self.assertEqual(len(caught.exception.faults), 25)
        self.assertEqual(self.save.get_block(2, 63, 2).name, "minecraft:stone")
        self.assertEqual(self.save.get_block(2, 64, 2).name, "minecraft:snow")
        self.assertEqual(self.b.blocks_set, 0, "nothing was written")
        self.assertEqual(self.b.rng.getstate(), before, "nothing was drawn")
        self.save.fill((0, 64, 0), (4, 64, 4), "minecraft:ice")
        result = self.b.pave(0, 0, 4, 4, y=63, clear_above=False,
                             light_level=MELT_SNOW)
        self.assertEqual(result.snow_cleared, 0)
        self.assertEqual(self.save.get_block(2, 64, 2).name, "minecraft:ice")
        self.assertLit([(x, 63, z) for z in range(5) for x in range(5)],
                       self.lamps(0, 0, 4, 4, 63))

    def test_a_source_too_dim_for_the_level_is_refused(self):
        """A magma block emits 3 and cannot hold a floor at 12, so the
        arithmetic is checked before a single lamp is written."""
        with self.assertRaises(NBTError) as ctx:
            self.b.light_cells([(2, 2)], light="minecraft:magma_block",
                               light_level=MELT_SNOW)
        message = str(ctx.exception)
        self.assertIn("magma_block", message)
        self.assertIn("3", message, "the refusal names what the source emits")
        self.assertIn("12", message, "and the level it was asked to hold")
        self.assertEqual(self.save.get_block(2, 63, 2).name, "minecraft:stone",
                         "nothing was lit before the refusal")
        with self.assertRaises(NBTError):
            self.b.pave(0, 0, 4, 4, y=63, light="minecraft:magma_block",
                        light_level=MELT_SNOW)

    def test_a_source_that_would_hang_in_the_paving_is_refused(self):
        """Lights go *into* the floor, so a lantern cannot be one of them
        however bright it is: the refusal points to the posts that stand
        one beside the paving."""
        with self.assertRaises(NBTError) as ctx:
            self.b.light_cells([(2, 2)], light="minecraft:lantern",
                               light_level=MELT_SNOW)
        self.assertIn("full block", str(ctx.exception))
        self.assertIn("line_lamps", str(ctx.exception))
        self.assertEqual(self.save.get_block(2, 63, 2).name, "minecraft:stone")

    def test_a_lamp_is_credited_with_what_its_state_gives_off(self):
        # Credited by name, plain stone or an unlit lamp once counted as
        # strength 15, and a floor of them reported itself lit to 12.
        for dark in ("minecraft:stone", "minecraft:redstone_lamp",
                     "minecraft:copper_bulb"):
            with self.subTest(dark=dark), self.assertRaises(NBTError) as ctx:
                self.b.light_cells([(30, 30)], light=dark, light_level=MELT_SNOW)
            self.assertNotIsInstance(ctx.exception, IllegalBlock)
            self.assertIn("no light", str(ctx.exception))
        # A lamp the game does not have is dark because it is air to the
        # game, which is the refusal to give, wherever a lamp is weighed;
        # allowed, it is dark like any other.
        lamp = "minecraft:mod_lamp"
        weighed = {
            "light_cells": lambda b: b.light_cells([(30, 30)], light=lamp,
                                                   light_level=MELT_SNOW),
            "pave": lambda b: b.pave(26, 26, 30, 30, 63, light=lamp,
                                     light_level=MELT_SNOW),
            "hang_lamps": lambda b: b.hang_lamps(26, 26, 30, 30, 70, 63,
                                                 light=lamp, light_level=8),
            "shaft": lambda b: b.shaft((28, 28), 64, 70, light=lamp,
                                       light_level=8),
        }
        allowed = Builder(self.save, allow={lamp})
        for writer, call in weighed.items():
            with self.subTest(writer=writer):
                with self.assertRaises(IllegalBlock) as ctx:
                    call(self.b)
                self.assertIn("allow=", str(ctx.exception))
                with self.assertRaises(NBTError) as ctx:
                    call(allowed)
                self.assertNotIsInstance(ctx.exception, IllegalBlock)
                self.assertIn("no light", str(ctx.exception))
        # Lit is not lasting: a lit redstone lamp goes dark at the next
        # block update with nothing to power it, and an unwaxed copper bulb
        # weathers down to 4. Only the light a lamp keeps is credited.
        with self.assertRaises(NBTError) as ctx:
            self.b.light_cells([(30, 30)], light="minecraft:redstone_lamp[lit=true]",
                               light_level=MELT_SNOW)
        self.assertIn("does not last", str(ctx.exception))
        with self.assertRaises(NBTError) as ctx:
            self.b.light_cells([(30, 30)], light="minecraft:copper_bulb[lit=true]",
                               light_level=MELT_SNOW)
        self.assertIn("emits light 4", str(ctx.exception))
        self.assertEqual(self.save.get_block(30, 63, 30).name, "minecraft:stone")
        result = self.b.light_cells([(30, 30)],
                                    light="minecraft:waxed_copper_bulb[lit=true]",
                                    light_level=MELT_SNOW)
        self.assertIsInstance(result, PaveResult)
        self.assertEqual(result.lights, 1)

    def test_light_cells_lights_the_far_side_of_a_wall(self):
        """Light goes round a wall, not through it: straight-line arithmetic
        credited the lamp on one side with the cell on the other, which the
        game left at 4."""
        self.save.fill((5, 64, -3), (5, 67, 3), "minecraft:stone")
        result = self.b.light_cells([(4, 0), (6, 0)], light_level=MELT_SNOW)
        self.assertEqual(result.lights, 2, "one on each side of the wall")
        self.assertLit([(4, 63, 0), (6, 63, 0)], self.lamps(4, 0, 6, 0, 63))

    def test_a_lamp_overwritten_since_is_not_credited(self):
        """The light already there is read off the world, not remembered
        from the call that set it: a lamp somebody broke since is not a
        lamp, and the one it takes to light the floor again is set back."""
        self.b.pave(0, 0, 9, 9, y=63, light_level=MELT_SNOW)
        self.assertEqual(self.save.get_block(9, 63, 4).name, self.GLOWSTONE,
                         "the pave patched its east edge here")
        self.save.set_block(9, 63, 4, "minecraft:stone")
        cells = [(x, z) for z in range(10) for x in range(10)]
        result = self.b.light_cells(cells, light_level=MELT_SNOW)
        self.assertEqual(result.lights, 1, "the floor's own lamps are counted")
        self.assertEqual(self.save.get_block(9, 63, 4).name, self.GLOWSTONE)
        self.assertLit([(x, 63, z) for x, z in cells],
                       self.lamps(0, 0, 9, 9, 63))

    def test_a_cell_with_a_block_standing_in_it_is_skipped(self):
        """A post on the floor holds no light and needs none: it is counted,
        not refused as dark, and the floor round it is lit."""
        self.save.set_block(2, 64, 2, "minecraft:stone")
        cells = [(x, 63, z) for z in range(5) for x in range(5)]
        result = self.b.light_cells(cells, light_level=MELT_SNOW)
        self.assertEqual(result.blocked, 1)
        self.assertIn("1 blocked", repr(result))
        self.assertLit([cell for cell in cells if cell != (2, 63, 2)],
                       self.lamps(0, 0, 4, 4, 63))
        self.assertNotIn("blocked", repr(self.b.light_cells(
            [(20, 20)], light_level=MELT_SNOW)))

    def test_a_dimmer_floor_asks_for_fewer_lamps(self):
        bright = self.b.pave(0, 0, 15, 15, y=63, light_level=MELT_SNOW)
        dim = Builder(self.save, seed=7).pave(0, 16, 15, 31, y=63, light_level=6)
        self.assertLess(dim.lights, bright.lights)
        self.assertEqual(dim.light_level, 6)
        self.assertLit([(x, 63, z) for z in range(16, 32) for x in range(16)],
                       self.lamps(0, 16, 15, 31, 63), light_level=6)

    #: Every named level, as the refusal for a missing one spells it.
    NAMED = ("NO_SPAWNS (1 in the overworld and the end, 8 in the nether)",
             "NO_NEW_SNOW_OR_ICE (10)", "MELT_ICE (11)", "MELT_SNOW (12)")

    def test_a_light_writer_asks_for_its_level(self):
        """A light level is a rule of the game, so a writer that sets a
        light is told which one rather than guessing one -- 12 for a floor,
        8 for a hall or a shaft, the second the nether's no-spawn level and
        no rule of the overworld's. Leaving it out is a TypeError naming
        the rules, before anything is written or drawn; an unlit floor or
        shaft needs none."""
        self.save.fill((0, 72, 0), (9, 72, 9), "minecraft:stone")
        calls = {
            "pave": lambda: self.b.pave(0, 0, 9, 9, y=63),
            "light_cells": lambda: self.b.light_cells([(4, 4)]),
            "hang_lamps": lambda: self.b.hang_lamps(0, 0, 9, 9, 72, 63),
            "shaft": lambda: self.b.shaft((20, 20), 64, 70,
                                          light=self.GLOWSTONE),
        }
        drawn = self.b.rng.getstate()
        for writer, call in calls.items():
            with self.subTest(writer=writer):
                with self.assertRaises(TypeError) as caught:
                    call()
                message = str(caught.exception)
                self.assertTrue(message.startswith(
                    f"{writer} needs light_level="), message)
                for named in self.NAMED:
                    self.assertIn(named, message)
        with self.assertRaises(TypeError):
            self.b.pave(0, 0, 9, 9, y=63, light_level=True)
        self.assertEqual(self.b.blocks_set, 0, "nothing was written")
        self.assertEqual(self.b.rng.getstate(), drawn, "nothing was drawn")
        self.assertEqual(self.save.get_block(4, 63, 4).name, "minecraft:stone")
        self.assertIsNone(self.b.pave(0, 0, 9, 9, y=63, light=None).light_level)
        self.assertEqual(self.b.shaft((20, 20), 64, 70).lights, 0)

    def fresh(self, *, nether: bool = False) -> MinecraftSave:
        """Another world like this test's, stone to y=63 -- and the same
        again as the nether."""
        directory = tempfile.mkdtemp(prefix="build-tests-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        make_save(directory, CHUNKS)
        if nether:
            root = os.path.join(directory, "dimensions", "minecraft")
            shutil.copytree(os.path.join(root, "overworld"),
                            os.path.join(root, "the_nether"))
        save = MinecraftSave.load(directory)
        for dimension in (OVERWORLD, NETHER) if nether else (OVERWORLD,):
            save.fill((-16, 60, -16), (31, 63, 31), "minecraft:stone",
                      dimension=dimension)
            save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31),
                                    dimension=dimension)
        return save

    def test_a_floor_lit_to_no_spawns_takes_far_fewer_lamps(self):
        """What the rule costs. A glowstone floor's lattice is laid
        ``2 * (15 - level - 1)`` apart: 4 at MELT_SNOW, 8 at
        NO_NEW_SNOW_OR_ICE, a quarter as dense, and 26 at NO_SPAWNS, a
        forty-second -- the edges of a real floor add their own. And a floor
        lit to NO_SPAWNS is one the game's spawn rule finds nowhere on."""
        lights = {}
        for level in (MELT_SNOW, NO_NEW_SNOW_OR_ICE, NO_SPAWNS):
            save = self.fresh()
            if level is NO_SPAWNS:
                self.assertEqual(
                    len(spawnable(save, (0, 64, 0), (29, 64, 29))), 900,
                    "dark stone spawns everywhere")
            result = Builder(save, seed=7).pave(0, 0, 29, 29, y=63,
                                                light_level=level)
            self.assertEqual(result.light_level,
                             1 if level is NO_SPAWNS else level)
            lights[level] = result.lights
            if level is NO_SPAWNS:
                self.assertEqual(spawnable(save, (0, 64, 0), (29, 64, 29)), [])
        self.assertLess(lights[NO_NEW_SNOW_OR_ICE] * 2, lights[MELT_SNOW])
        self.assertLess(lights[NO_SPAWNS] * 2, lights[NO_NEW_SNOW_OR_ICE])
        self.assertLess(lights[NO_SPAWNS] * 10, lights[MELT_SNOW], lights)

    def test_an_open_floor_takes_no_more_lamps_than_the_flood_would(self):
        """An open square lit to NO_SPAWNS, where a glowstone reaches 13
        blocks: the lattice, 26 apart, leaves a wide margin, and pave,
        light_cells and hang_lamps light it along the edge -- no more lamps
        than plan_lights' own greedy takes over every cell, and never a
        stripe. Proposing for each cell short the furthest lamp reaching
        it hung a lamp in every column of one row, each reaching straight
        back up to the one edge cell it was for: 14 lamps, 13 of them in a
        row, on the 16-block square that needs 3, and 32 chains side by
        side across a 32-block hall."""
        glow, chain = self.GLOWSTONE, "minecraft:iron_chain[axis=y]"
        for x0, z0, x1, z1, needs in ((14, 14, 29, 29, 3),
                                      (-16, -16, 15, 15, None)):
            floor = [(x, z) for z in range(z0, z1 + 1)
                     for x in range(x0, x1 + 1)]
            targets = [(x, 64, z) for x, z in floor]
            hall = {(x, 66, z): {(x, 69, z): chain, (x, 68, z): chain,
                                 (x, 67, z): chain, (x, 66, z): glow}
                    for x, z in floor}
            writers = {
                "pave": (lambda b: b.pave(x0, z0, x1, z1, y=63,
                                          light_level=NO_SPAWNS),
                         {(x, 63, z): glow for x, z in floor}),
                "light_cells": (lambda b: b.light_cells(
                    floor, light_level=NO_SPAWNS),
                    {(x, 63, z): glow for x, z in floor}),
                "hang_lamps": (lambda b: b.hang_lamps(
                    x0, z0, x1, z1, 70, 63, light_level=NO_SPAWNS), hall),
            }
            for writer, (call, every) in writers.items():
                with self.subTest(writer=writer, square=(x0, z0, x1, z1)):
                    save = self.fresh()
                    if writer == "hang_lamps":
                        save.fill((x0, 70, z0), (x1, 70, z1), "minecraft:stone")
                    greedy = plan_lights(save, targets, NO_SPAWNS, every)
                    result = call(Builder(save, seed=7))
                    self.assertLessEqual(result.lights, len(greedy.lamps))
                    if needs is not None:
                        self.assertEqual(result.lights, needs)
                    rows = {}
                    for y in range(63, 67):
                        for x, z in floor:
                            if save.get_block(x, y, z).name == glow:
                                rows[z] = rows.get(z, 0) + 1
                    self.assertEqual(sum(rows.values()), result.lights)
                    self.assertLessEqual(max(rows.values()), 2, rows)
                    self.assertEqual(spawnable(save, (x0, 64, z0),
                                               (x1, 64, z1)), [])

    def test_no_spawns_is_read_for_the_builders_dimension(self):
        """NO_SPAWNS is the dimension's rule: 1 in the overworld, and 8 in
        the nether, whose dimension type lets a monster spawn at block
        light 7. A literal 1 is 1 in both, which in the nether leaves a
        floor monsters spawn on -- the confusion NO_SPAWNS not being an int
        rules out."""
        save = self.fresh(nether=True)
        for dimension, level in ((OVERWORLD, 1), (NETHER, 8)):
            with self.subTest(dimension=dimension):
                b = Builder(save, seed=7, dimension=dimension)
                result = b.pave(0, 0, 23, 23, y=63, light_level=NO_SPAWNS)
                self.assertEqual(result.light_level, level)
                self.assertEqual(spawnable(save, (0, 64, 0), (23, 64, 23),
                                           dimension=dimension), [])
        nether = Builder(save, seed=7, dimension=NETHER)
        self.assertEqual(nether.light_cells(
            [(30, 30)], light_level=NO_SPAWNS).light_level, 8)
        self.assertEqual(nether.shaft(
            (28, -12), 64, 70, light=self.GLOWSTONE,
            light_level=NO_SPAWNS).light_level, 8)
        save.fill((24, 72, 0), (31, 72, 7), "minecraft:stone", dimension=NETHER)
        self.assertEqual(nether.hang_lamps(
            24, 0, 31, 7, 72, 63, drop=2,
            light_level=NO_SPAWNS).light_level, 8)
        literal = nether.pave(-16, -16, -1, -1, y=63, light_level=1)
        self.assertEqual(literal.light_level, 1)
        self.assertTrue(spawnable(save, (-16, 64, -16), (-1, 64, -1),
                                  dimension=NETHER))


class LineLampTests(BuilderBase):
    """``Builder.line_lamps``: lamp posts beside a line, stood wherever the
    game's flood needs them to light the strip along it -- never in the
    strip, a keep-out or on water -- with the side named and the diagonal
    tie named at the call."""

    POST = "minecraft:cobblestone_wall"
    LAMP = "minecraft:lantern"

    fresh = PaveTests.fresh

    def strip(self, points, width: int = 3) -> dict:
        from heaton_voxel.craft.build.site import _swath
        from heaton_voxel.craft.geometry import line
        return _swath(line(points), width // 2)

    def dark(self, save, b, strip, level: int) -> list:
        """Every cell over a strip column, with nothing standing in it, that
        the game's flood -- durable light, read back off the world -- leaves
        under ``level``."""
        cells = [(x, b.ground(x, z) + 1, z) for x, z in strip]
        xs, ys, zs = zip(*cells)
        field = simulate_block_light(save, (min(xs), min(ys), min(zs)),
                                     (max(xs), max(ys), max(zs)), durable=True)
        return [(cell, field.level(*cell)) for cell in cells
                if save.get_block(*cell).name == "minecraft:air"
                and field.level(*cell) < level]

    def assertPosts(self, save, result, strip, post=POST, lamp=LAMP):
        """Every post the result names stands where it says, off the strip,
        with its lamp on top: and they are all the lamps there are."""
        self.assertTrue(result.posts, "no post was stood at all")
        for x, y, z in result.posts:
            self.assertNotIn((x, z), strip, "a post stands in the road")
            self.assertEqual(save.get_block(x, y, z), BlockState.parse(post))
            self.assertEqual(save.get_block(x, y + 1, z), BlockState.parse(lamp))

    def test_lamps_light_an_l_shaped_road_to_the_level(self):
        """Lamps along a road that turns a corner, with a pillar standing in
        it: every cell of the strip clears the level by the game's own flood
        -- checked here off the world, not taken from the planner -- save
        the one the pillar stands in, which needs none. No post stands in
        the road, every post has its lamp, and the level is required."""
        points = [(-12, 0), (16, 0), (16, 26)]
        self.b.road(points, width=3)
        self.save.fill((4, 64, 0), (4, 66, 0), "minecraft:stone")
        strip = self.strip(points)
        written = self.b.blocks_set
        with self.assertRaises(TypeError) as caught:
            self.b.line_lamps(points, post=self.POST, lamp=self.LAMP)
        self.assertIn("NO_SPAWNS", str(caught.exception))
        self.assertEqual(self.b.blocks_set, written)
        # From the inside of the bend alone, the outside of its corner is
        # out of reach at 10: refused whole, the dark cells its faults.
        with self.assertRaises(Refused) as caught:
            self.b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                              light_level=NO_NEW_SNOW_OR_ICE, side="right")
        self.assertIn("side='both'", str(caught.exception))
        self.assertTrue(caught.exception.faults)
        for x, y, z, level in caught.exception.faults:
            self.assertIn((x, z), strip)
            self.assertLess(level, 10)
        self.assertEqual(self.b.blocks_set, written)

        result = self.b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                                   light_level=NO_NEW_SNOW_OR_ICE)
        self.assertEqual(result.light_level, 10)
        self.assertEqual(result.cells, len(strip))
        self.assertEqual(result.blocked, 1, "the pillar's cell")
        self.assertEqual(self.dark(self.save, self.b, strip, 10), [])
        self.assertPosts(self.save, result, strip)
        self.assertEqual(self.b.blocks_set - written, 2 * len(result.posts))
        self.assertEqual(sum(
            1 for x in range(-16, 32) for z in range(-16, 32)
            if self.save.get_block(x, 65, z).name == "minecraft:lantern"),
            len(result.posts), "the result names every post")
        # Asked again, the road is lit already: no posts.
        again = self.b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                                  light_level=NO_NEW_SNOW_OR_ICE)
        self.assertEqual(again.posts, [])

    def test_the_flood_patches_what_a_wall_hides_and_posts_go_in_line_order(self):
        """A wall across the road and its south verge, three high. The
        arithmetic credits the post just west of it with the cells just
        east of it, which the game's light reaches only round the wall's
        far end or over its top, too late to light them: the flood finds
        them dark and patches a post beyond the wall, at (5, -2). The posts
        are written in the order the line offers them, from its first
        waypoint on, each from its foot up -- the wall, then its lamp -- in
        one plan handed to _commit."""
        from heaton_voxel.craft.geometry import line
        points = [(-12, 0), (16, 0), (16, 26)]
        self.b.road(points, width=3)
        self.save.fill((4, 64, -1), (4, 66, 3), "minecraft:stone")
        committed = []
        commit = self.b._commit

        def spy(ops, **kwargs):
            committed.append(list(ops))
            return commit(ops, **kwargs)

        self.b._commit = spy
        result = self.b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                                   light_level=NO_NEW_SNOW_OR_ICE)
        self.assertEqual(result.patched, 1)
        self.assertEqual(result.blocked, 3, "the wall's cells in the strip")
        self.assertEqual(result.passed_over, 1, "the place in the wall")
        self.assertEqual(self.dark(self.save, self.b, self.strip(points), 10),
                         [])
        self.assertEqual(result.posts, [
            (-12, 64, -2), (-7, 64, 2), (-2, 64, -2), (3, 64, 2), (5, 64, -2),
            (8, 64, -2), (13, 64, 2), (18, 64, 0), (18, 64, 6), (14, 64, 11),
            (18, 64, 16), (14, 64, 21), (18, 64, 26)])
        post, lamp = BlockState.parse(self.POST), BlockState.parse(self.LAMP)
        (ops,) = committed
        self.assertEqual(ops, [op for x, y, z in result.posts
                               for op in (("set", (x, y, z), post),
                                          ("set", (x, y + 1, z), lamp))])
        centerline = line(points)

        def along(x, _, z) -> int:
            return min(range(len(centerline)), key=lambda i: (
                abs(centerline[i][0] - x) + abs(centerline[i][1] - z), i))

        beside = [along(*cell) for cell in result.posts]
        self.assertEqual(beside, sorted(beside), "in the order of the line")

    def test_on_open_ground_the_arithmetic_spaces_the_posts(self):
        """Along a straight road over open ground the straight-line
        proposal is the plan -- the flood patches nothing -- and it is as
        sparse as the light allows. At 10 a lantern on a wall lights 5
        blocks out, the drop to a walker's feet among them, so beside a
        3-wide road a post reaches its own verge three cells either way and
        the far verge one: the posts alternate across the road five apart,
        the last beside the strip's end, a cell past the line's. Lit only so
        nothing spawns, three posts light the road, where the removed
        road(lanterns=True)'s one every twelve cells stood four. On a lane
        one wide each post lights seven cells, and with nothing to choose
        between the sides, each tie goes across the road from the post
        before."""
        def posts(result):
            return [(x, z) for x, y, z in result.posts if y == 64]

        points = [(-14, 4), (30, 4)]
        self.b.road(points, width=3)
        result = self.b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                                   light_level=NO_NEW_SNOW_OR_ICE)
        self.assertEqual(result.patched, 0)
        self.assertEqual(posts(result), [
            (-14, 2), (-9, 6), (-4, 2), (1, 6), (6, 2), (11, 6), (16, 2),
            (21, 6), (26, 2), (31, 6)])
        self.assertEqual(self.dark(self.save, self.b, self.strip(points), 10),
                         [])

        save = self.fresh()
        b = Builder(save, seed=7)
        b.road(points, width=3)
        result = b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                              light_level=NO_SPAWNS)
        self.assertEqual(result.patched, 0)
        self.assertEqual(posts(result), [(-5, 2), (18, 6), (31, 2)])
        every_twelve = sum(1 for i in range(len(self.strip(points, 1)))
                           if i % 12 == 6)
        self.assertEqual(every_twelve, 4)
        self.assertEqual(self.dark(save, b, self.strip(points), 1), [])

        save = self.fresh()
        b = Builder(save, seed=7)
        b.road(points, width=1)
        result = b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                              light_level=NO_NEW_SNOW_OR_ICE, width=1)
        self.assertEqual(result.patched, 0)
        self.assertEqual(posts(result), [
            (-11, 5), (-4, 3), (3, 5), (10, 3), (17, 5), (24, 3), (30, 5)])
        self.assertEqual(self.dark(save, b, self.strip(points, 1), 10), [])

    def test_the_strips_ends_are_lit_from_beside_them(self):
        """The strip is a square stamped on every cell of the line, so it
        runs past each end by half its width, and the middle of that end is
        the cell furthest from a post beside the line: a 3-wide road lit to
        melt snow, or a 5-wide one lit to 10, was refused for its two end
        cells alone. The line is walked on past its ends that far, and posts
        stand beside the strip's ends -- never in it, and never across the
        road's end, where a door or a junction would be."""
        for width, level in ((3, MELT_SNOW), (5, NO_NEW_SNOW_OR_ICE)):
            with self.subTest(width=width, level=level):
                save = self.fresh()
                b = Builder(save, seed=7)
                points = [(-10, 4), (20, 4)]
                b.road(points, width=width)
                half = width // 2
                strip = self.strip(points, width)
                result = b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                                      light_level=level, width=width)
                self.assertPosts(save, result, strip)
                self.assertEqual(self.dark(save, b, strip, level), [])
                xs = [x for x, _, _ in result.posts]
                self.assertEqual((min(xs), max(xs)), (-10 - half, 20 + half),
                                 "beside the strip's ends, not past them")
                for x, _, z in result.posts:
                    self.assertGreater(abs(z - 4), half, (x, z))

    def test_a_post_never_stands_on_the_road_or_in_a_keep_out(self):
        """A pad along one verge and a reservation along the other: the
        posts go where neither is, and the places in them are passed over,
        not written."""
        points = [(-12, 4), (28, 4)]
        self.b.road(points, width=3)
        yard = self.b.level_pad(-14, 0, 8, 2, 63, name="yard")
        grove = self.b.reserve(10, 6, 30, 8, name="grove")
        strip = self.strip(points)
        result = self.b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                                   light_level=NO_SPAWNS)
        self.assertGreater(result.passed_over, 0)
        self.assertPosts(self.save, result, strip)
        for x, _, z in result.posts:
            for name, (x0, z0, x1, z1) in (("yard", yard), ("grove", grove)):
                self.assertFalse(x0 <= x <= x1 and z0 <= z <= z1,
                                 f"a post stands in {name} at ({x}, {z})")
        self.assertEqual(self.dark(self.save, self.b, strip, 1), [])

    def test_a_post_never_stands_in_what_is_already_there(self):
        """A hedge along the road's right verge, a block off it where the
        posts would stand: every place in it is passed over and the call
        refused, naming the leaves, with the hedge untouched. Gaps cut in
        it take the posts -- a poppy or a tuft of grass at a post's foot is
        ground cover, which the post replaces -- but tall grass is two
        blocks, its upper half standing where the lamp would and a lone
        half of it one the game breaks, so its gap is passed over and both
        halves stay."""
        hedge = "minecraft:oak_leaves[persistent=true]"
        points = [(-12, 4), (28, 4)]
        self.b.road(points, width=3)
        self.save.fill((-16, 63, 6), (31, 63, 6), "minecraft:grass_block")
        self.save.fill((-16, 64, 6), (31, 64, 6), hedge)
        written = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                              light_level=NO_SPAWNS, side="right")
        for words in ("nowhere to stand a post", "holds " + hedge,
                      "Nothing was written"):
            self.assertIn(words, str(caught.exception))
        self.assertEqual(self.b.blocks_set, written)

        self.save.set_block(-6, 64, 6, "minecraft:poppy")
        self.save.set_block(8, 64, 6, "minecraft:short_grass")
        self.save.set_block(22, 64, 6, "minecraft:air")
        self.save.set_block(15, 64, 6, "minecraft:tall_grass[half=lower]")
        self.save.set_block(15, 65, 6, "minecraft:tall_grass[half=upper]")
        result = self.b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                                   light_level=NO_SPAWNS, side="right")
        self.assertEqual(result.posts, [(-6, 64, 6), (8, 64, 6), (22, 64, 6)])
        self.assertEqual(result.passed_over, 40, "39 in the hedge, 1 tall grass")
        self.assertPosts(self.save, result, self.strip(points))
        self.assertEqual([str(self.save.get_block(15, y, 6)) for y in (64, 65)],
                         ["minecraft:tall_grass[half=lower]",
                          "minecraft:tall_grass[half=upper]"])
        for x in range(-16, 32):
            if x not in (-6, 8, 15, 22):
                self.assertEqual(self.save.get_block(x, 64, 6),
                                 BlockState.parse(hedge), x)
        self.assertEqual(self.dark(self.save, self.b, self.strip(points), 1),
                         [])

    def test_a_post_past_the_top_of_the_world_is_passed_over(self):
        """A cliff to the build limit along the road's right verge leaves a
        post there nowhere to go but past the world: every such place is
        passed over, so posts on the right alone are refused, naming why,
        and with both sides allowed the left lights the road."""
        points = [(-12, 4), (28, 4)]
        self.b.road(points, width=3)
        self.save.fill((-16, 64, 6), (31, 318, 7), "minecraft:stone")
        self.save.rebuild_heightmaps((-16, 0, 6), (31, 0, 7))
        written = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                              light_level=NO_SPAWNS, side="right")
        for words in ("nowhere to stand a post", "cannot be written",
                      "past the world", "Nothing was written"):
            self.assertIn(words, str(caught.exception))
        self.assertEqual(self.b.blocks_set, written)
        result = self.b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                                   light_level=NO_SPAWNS)
        self.assertPosts(self.save, result, self.strip(points))
        self.assertEqual({z for _, _, z in result.posts}, {2})
        self.assertEqual(result.passed_over, 43, "every place on the cliff")
        self.assertEqual(self.dark(self.save, self.b, self.strip(points), 1),
                         [])

    def test_a_line_with_nowhere_to_stand_a_post_is_refused_whole(self):
        """Keep-outs over both verges leave the line no post: one refusal,
        naming them, with nothing written -- and named in through=, the
        posts stand in them."""
        points = [(-12, 26), (28, 26)]
        self.b.road(points, width=3)
        self.b.level_pad(-14, 22, 30, 24, 63, name="north")
        self.b.reserve(-14, 28, 30, 30, name="south")
        written = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                              light_level=NO_SPAWNS)
        message = str(caught.exception)
        for words in ("nowhere to stand a post", "reservation 'south'",
                      "Nothing was written"):
            self.assertIn(words, message)
        for name in ("pad 'north'", "reservation 'south'"):
            self.assertTrue(any(name in fault
                                for fault in caught.exception.faults), name)
        self.assertEqual(self.b.blocks_set, written)
        result = self.b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                                   light_level=NO_SPAWNS,
                                   through=("north", "south"))
        self.assertPosts(self.save, result, self.strip(points))

    def test_a_diagonal_road_takes_its_posts_on_the_named_side(self):
        """Down a road heading south-east, the right side is south-west and
        the left north-east, whichever way the diagonal is broken. Broken
        toward z, as the call names it, every step heads south, so a post
        stands due west of a cell on the right and due east on the left.
        Along the run that offers the same columns a tie toward x would --
        due south of a cell three back is due west of one three on -- so
        the tie shows at the road's end: the strip runs on south past
        (12, 12), and the last post stands beside that, at (10, 13) or
        (14, 13), where a tie toward x would stand it at (13, 14) or
        (13, 10)."""
        points = [(0, 0), (12, 12)]
        for side, west, expected in (("right", True, [(4, 64, 7),
                                                      (10, 64, 13)]),
                                     ("left", False, [(7, 64, 4),
                                                      (14, 64, 13)])):
            with self.subTest(side=side):
                save = self.fresh()
                b = Builder(save, seed=7)
                b.road(points)
                strip = self.strip(points)
                result = b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                                      light_level=NO_SPAWNS, side=side)
                self.assertPosts(save, result, strip)
                self.assertEqual(result.posts, expected)
                for x, _, z in result.posts:
                    self.assertEqual(x < z, west, (x, z))
                self.assertEqual(self.dark(save, b, strip, 1), [])

    def test_water_under_a_post_is_refused_by_default(self):
        """A pond along the road's right side: a post on it is refused --
        the call, when no post can stand anywhere else -- unless
        over_water='bridge' stands it on the water's surface. With both
        sides open the posts keep to the dry one. A strip over water is
        refused outright, as light_cells refuses one."""
        self.save.fill((-2, 61, 7), (16, 63, 10), "minecraft:water[level=0]")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        points = [(0, 5), (14, 5)]
        self.b.road(points)
        written = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                              light_level=NO_SPAWNS, side="right")
        for words in ("would stand on water", "over_water='bridge'",
                      "Nothing was written"):
            self.assertIn(words, str(caught.exception))
        with self.assertRaises(Refused) as caught:
            self.b.line_lamps([(0, 8), (14, 8)], post=self.POST,
                              lamp=self.LAMP, light_level=NO_SPAWNS)
        self.assertIn("over water", str(caught.exception))
        self.assertEqual(self.b.blocks_set, written, "a refusal writes nothing")

        result = self.b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                                   light_level=NO_SPAWNS, side="right",
                                   over_water="bridge")
        self.assertPosts(self.save, result, self.strip(points))
        for x, y, z in result.posts:
            self.assertEqual((y, z), (64, 7))
            self.assertEqual(self.save.get_block(x, 63, z).name,
                             "minecraft:water")

        save = self.fresh()
        save.fill((-2, 61, 7), (16, 63, 10), "minecraft:water[level=0]")
        save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        b = Builder(save, seed=7)
        b.road(points)
        result = b.line_lamps(points, post=self.POST, lamp=self.LAMP,
                              light_level=NO_SPAWNS)
        self.assertGreater(result.passed_over, 0)
        self.assertEqual({z for _, _, z in result.posts}, {3},
                         "every post on the dry side")

    def test_a_lamp_the_game_would_not_keep_is_refused(self):
        """The plan is audited before it is written: a standing lantern
        needs the middle of the block under it (LanternBlock.canSurvive),
        which a fence's or a wall's top gives and a bottom slab's does not,
        and a lantern hung from nothing falls. Each is refused whole; a
        lantern on a fence stands, and finish() finds nothing to report."""
        points = [(-10, 4), (10, 4)]
        written = self.b.blocks_set
        for post, lamp in (("minecraft:oak_slab[type=bottom]", self.LAMP),
                           ("minecraft:oak_fence",
                            "minecraft:lantern[hanging=true]")):
            with self.subTest(post=post, lamp=lamp):
                with self.assertRaises(Refused) as caught:
                    self.b.line_lamps(points, post=post, lamp=lamp,
                                      light_level=NO_SPAWNS)
                self.assertTrue(caught.exception.faults)
                for fault in caught.exception.faults:
                    self.assertEqual(fault.kind, "no footing")
                    self.assertEqual(fault.block.name, "minecraft:lantern")
                self.assertEqual(self.b.blocks_set, written)
        result = self.b.line_lamps(points, post="minecraft:oak_fence",
                                   lamp=self.LAMP, light_level=NO_SPAWNS)
        self.assertPosts(self.save, result, self.strip(points),
                         post="minecraft:oak_fence")
        self.assertEqual(self.b.finish().unsupported, [])

    def test_a_post_is_any_height_and_its_arguments_are_checked_first(self):
        """A post two walls high carries its lamp on top. A side, a width
        or an over_water the call does not take, an empty post, a dark lamp
        and a block the game lacks are each refused before anything is
        read or written."""
        points = [(-10, 4), (10, 4)]
        written = self.b.blocks_set
        for kwargs, error in (({"side": "up"}, NBTError),
                              ({"width": 0}, NBTError),
                              ({"over_water": "fill"}, ValueError),
                              ({"post": ()}, NBTError),
                              ({"lamp": "minecraft:stone"}, NBTError),
                              ({"post": "minecraft:cobblestone_fence"},
                               IllegalBlock)):
            with self.subTest(**{key: repr(value)
                                 for key, value in kwargs.items()}):
                call = {"post": self.POST, "lamp": self.LAMP,
                        "light_level": NO_SPAWNS, **kwargs}
                with self.assertRaises(error):
                    self.b.line_lamps(points, **call)
                self.assertEqual(self.b.blocks_set, written)
        result = self.b.line_lamps(points, post=(self.POST, self.POST),
                                   lamp=self.LAMP, light_level=NO_SPAWNS)
        for x, y, z in result.posts:
            self.assertEqual([self.save.get_block(x, y + k, z).name
                              for k in range(3)],
                             [self.POST, self.POST, self.LAMP])

    def test_two_identical_line_lamp_builds_encode_identically(self):
        """The posts are written in the order the line offers them, each
        from its foot up, and nothing is drawn: the same call in two worlds
        saves the same bytes."""
        saved = []
        for _ in range(2):
            save = self.fresh()
            b = Builder(save, seed=7)
            b.line_lamps([(-12, 0), (16, 0), (16, 26)], post=self.POST,
                         lamp=self.LAMP, light_level=NO_NEW_SNOW_OR_ICE)
            save.save()
            region = os.path.join(save.path, "dimensions", "minecraft",
                                  "overworld", "region")
            files = {}
            for name in sorted(os.listdir(region)):
                with open(os.path.join(region, name), "rb") as handle:
                    files[name] = handle.read()
            saved.append(files)
        self.assertEqual(saved[0].keys(), saved[1].keys())
        for name in saved[0]:
            self.assertTrue(saved[0][name] == saved[1][name], name)

    def test_road_lanterns_was_removed(self):
        """road(lanterns=True) was removed for line_lamps: a TypeError
        naming it, raised before anything else about the call is checked,
        read, written or drawn -- ahead of a road with no waypoints, an
        over_water the road does not take, a road off the world and a mix
        the game lacks, with no warning. lanterns=False, which the calls
        that turned the old posts off pass, is still taken, and builds the
        bytes leaving it out builds."""
        import warnings
        rng = self.b.rng.getstate()
        for kwargs in ({"points": [(-10, -10), (10, -10)]},
                       {"points": 5},
                       {"points": [(-10, -10), (10, -10)],
                        "over_water": "sideways"},
                       {"points": [(200, 4), (260, 4)]},
                       {"points": [(-10, -10), (10, -10)],
                        "mix": [("minecraft:quartz_wall", 1)]}):
            with self.subTest(**kwargs):
                with warnings.catch_warnings(record=True) as seen:
                    warnings.simplefilter("always")
                    with self.assertRaises(TypeError) as caught:
                        self.b.road(lanterns=True, **kwargs)
                self.assertEqual(seen, [])
                message = str(caught.exception)
                for words in ("road(lanterns=True) was removed",
                              "Builder.line_lamps(result.points, "
                              "width=result.width"):
                    self.assertIn(words, message)
        self.assertEqual(self.b.blocks_set, 0, "nothing was written")
        self.assertEqual(list(self.b.journal.written()), [])
        self.assertEqual(self.b.rng.getstate(), rng, "or drawn")
        self.assertEqual(self.b._probes, {}, "or read")

        saved = []
        for kwargs in ({}, {"lanterns": False}):
            save = self.fresh()
            b = Builder(save, seed=7)
            b.road([(-12, 0), (16, 0), (16, 26)], width=3, **kwargs)
            save.save()
            region = os.path.join(save.path, "dimensions", "minecraft",
                                  "overworld", "region")
            files = {}
            for name in sorted(os.listdir(region)):
                with open(os.path.join(region, name), "rb") as handle:
                    files[name] = handle.read()
            saved.append(files)
        self.assertEqual(saved[0].keys(), saved[1].keys())
        for name in saved[0]:
            self.assertTrue(saved[0][name] == saved[1][name], name)


class SmoothTerrainTests(BuilderBase):
    """Ground at y=63 everywhere, with defects punched into it."""

    def _roughness(self, x0, z0, x1, z1):
        """Adjacent columns differing by two blocks or more."""
        rough = 0
        for z in range(z0, z1 + 1):
            for x in range(x0, x1 + 1):
                here = self.b.ground(x, z)
                for dx, dz in ((1, 0), (0, 1)):
                    if x + dx <= x1 and z + dz <= z1:
                        if abs(self.b.ground(x + dx, z + dz) - here) >= 2:
                            rough += 1
        return rough

    def test_a_spike_and_a_pit_are_eased_but_the_slope_survives(self):
        self.save.fill((4, 64, 4), (4, 67, 4), "minecraft:stone")   # spike
        self.save.fill((8, 62, 8), (8, 63, 8), "minecraft:air")     # pit
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.b = Builder(self.save, seed=7)
        before = self._roughness(0, 0, 12, 12)
        self.b.smooth_terrain((0, 0), (12, 12), radius=2, max_shift=3)
        self.b.finish()                          # heightmaps follow the ground
        self.b = Builder(self.save, seed=7)      # drop the height cache
        after = self._roughness(0, 0, 12, 12)
        self.assertLess(after, before, "smoothing should reduce jaggedness")
        self.assertLess(self.b.ground(4, 4), 67, "the spike came down")
        self.assertGreater(self.b.ground(8, 8), 61, "the pit filled in")

    def test_protected_rectangles_are_neither_moved_nor_averaged_in(self):
        # a tower: fifty blocks of stone, the shape that would heave the field
        self.save.fill((6, 64, 6), (7, 113, 7), "minecraft:stone")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.b = Builder(self.save, seed=7)
        self.b.smooth_terrain((0, 0), (12, 12), radius=3, max_shift=3,
                              protect=[(6, 6, 7, 7)])
        self.assertEqual(self.save.get_block(6, 113, 6).name, "minecraft:stone",
                         "the protected tower still stands")
        self.b = Builder(self.save, seed=7)
        self.assertEqual(self.b.ground(0, 0), 63,
                         "flat ground beside it was not dragged upward")

    def test_water_keeps_its_level(self):
        self.save.fill((2, 63, 2), (5, 63, 5), "minecraft:water[level=0]")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.b = Builder(self.save, seed=7)
        self.b.smooth_terrain((0, 0), (12, 12), radius=2, max_shift=3)
        self.assertEqual(self.save.get_block(3, 63, 3).name, "minecraft:water",
                         "the pond was not filled in or drained")

    def test_each_column_keeps_its_own_surface_block(self):
        self.save.fill((9, 63, 1), (9, 63, 9), "minecraft:sand")
        self.save.fill((10, 64, 1), (10, 66, 9), "minecraft:stone")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.b = Builder(self.save, seed=7)
        self.b.smooth_terrain((0, 0), (12, 12), radius=2, max_shift=3)
        self.b.finish()
        self.b = Builder(self.save, seed=7)
        self.assertEqual(self.save.get_block(9, self.b.ground(9, 5), 5).name,
                         "minecraft:sand", "sand stayed sand, not dirt")

    def test_raising_ground_never_copies_cave_air_or_ore(self):
        """A raised column is built up from what lies under its surface,
        sand under sand, snow block under a snowfield, end stone in the End
        -- but a hollow under the grass is cave air and a vein is ore, and
        copying either would put a void or a seam into the hill. Neither is
        ground, so the column is raised with dirt."""
        pits = {(3, 4): ("minecraft:grass_block[snowy=false]",
                         "minecraft:cave_air", "minecraft:dirt"),
                (9, 4): ("minecraft:grass_block[snowy=false]",
                         "minecraft:diamond_ore", "minecraft:dirt"),
                (3, 7): ("minecraft:snow_block", "minecraft:snow_block",
                         "minecraft:snow_block"),
                (9, 7): ("minecraft:end_stone", "minecraft:end_stone",
                         "minecraft:end_stone")}
        for (x, z), (surface, under, _) in pits.items():
            self.save.set_block(x, 63, z, "minecraft:air")
            self.save.set_block(x, 62, z, surface)
            self.save.set_block(x, 61, z, under)
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.b = Builder(self.save, seed=7)
        report = self.b.smooth_terrain((0, 0), (12, 8), radius=1, max_shift=3)
        self.assertEqual(report["raised"], len(pits))
        for (x, z), (surface, under, filler) in pits.items():
            self.assertEqual(self.save.get_block(x, 63, z).name,
                             surface.split("[")[0], "the surface came up")
            self.assertEqual(self.save.get_block(x, 62, z).name, filler,
                             f"raised over {under}")
            self.assertEqual(self.save.get_block(x, 61, z).name, under,
                             "and what was under it is left alone")

    def test_no_column_moves_further_than_max_shift(self):
        self.save.fill((6, 64, 6), (6, 75, 6), "minecraft:stone")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.b = Builder(self.save, seed=7)
        report = self.b.smooth_terrain((0, 0), (12, 12), radius=3, max_shift=2)
        self.b = Builder(self.save, seed=7)
        self.assertGreaterEqual(self.b.ground(6, 6), 73,
                                "a 12-block spike cannot fall more than 2 in one pass")
        self.assertEqual(report["moved"], report["raised"] + report["lowered"])

    def test_finish_can_skip_the_connect_pass_that_rewrites_older_work(self):
        # fence posts spaced two apart: a decorative railing whose states a
        # block update would "correct" into unconnected posts
        for z in (2, 4, 6):
            self.save.set_block(2, 64, z,
                                "minecraft:spruce_fence[east=false,north=true,"
                                "south=true,waterlogged=false,west=false]")
        self.save.fill((10, 64, 10), (10, 66, 10), "minecraft:stone")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.b = Builder(self.save, seed=7)
        self.b.smooth_terrain((8, 8), (12, 12), radius=2, max_shift=3)
        report = self.b.finish(connect=False)
        self.assertIsNone(report.connections)
        self.assertIn("connections skipped", repr(report))
        self.assertEqual(
            self.save.get_block(2, 64, 4).properties["north"], "true",
            "the railing kept the state the earlier build gave it")

    def test_every_column_moves_by_move_column(self):
        """smooth_terrain plans each column it moves with _move_column, the
        one rule a column of ground is moved by: built up from the old
        ground with what lies under it, or cut down to the new, its own
        surface block on top, the clearance cleared over it and the ground
        recorded. The plan it commits is _move_column's ops, column after
        column in its order, and nothing else -- a spike cut down, a pit
        over sand raised with sand. The plan is also pinned as written out
        by hand, so a change inside _move_column fails here and not only
        on both sides of the comparison: the spike at 67 averages 63.16
        over its 25 columns and falls the max_shift of 3 to 64, the pit
        at 61 averages 62.92 and rises 2 to 63, and no other column's mean
        rounds to a move."""
        self.save.fill((4, 64, 4), (4, 67, 4), "minecraft:stone")   # spike
        self.save.fill((8, 62, 8), (8, 63, 8), "minecraft:air")     # pit
        self.save.fill((8, 60, 8), (8, 61, 8), "minecraft:sand")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.b = Builder(self.save, seed=7)
        planned, expected = [], []
        commit = self.b._commit

        def spy(ops, **kwargs):
            # Read as smooth_terrain read it: before anything is written.
            planned.extend(ops)
            for _, (x, z), new in (op for op in ops if op[0] == "ground"):
                g = self.b.ground(x, z)
                expected.extend(self.b._move_column(
                    x, z, g, new, str(self.save.get_block(x, g, z)), 2))
            return commit(ops, **kwargs)

        self.b._commit = spy
        report = self.b.smooth_terrain((0, 0), (12, 12), radius=2,
                                       max_shift=3, clearance=2)
        self.assertEqual((report["raised"], report["lowered"]), (1, 1))
        self.assertEqual(planned, expected)
        air, stone, sand = "minecraft:air", "minecraft:stone", "minecraft:sand"
        self.assertEqual(planned, [
            # the spike, cut down from 67 to 64, two cleared over it
            ("fill", (4, 65, 4), (4, 67, 4), air),
            ("set", (4, 64, 4), stone),
            ("fill", (4, 65, 4), (4, 66, 4), air),
            ("ground", (4, 4), 64),
            # the pit, raised from 61 to 63 with the sand under it
            ("fill", (8, 61, 8), (8, 62, 8), sand),
            ("set", (8, 63, 8), sand),
            ("fill", (8, 64, 8), (8, 65, 8), air),
            ("ground", (8, 8), 63)])
        for y in (65, 66, 67):
            self.assertEqual(self.save.get_block(4, y, 4).name, air)
        for y, name in ((61, sand), (62, sand), (63, sand), (64, air),
                        (65, air)):
            self.assertEqual(self.save.get_block(8, y, 8).name, name)
        self.assertEqual((self.b.ground(4, 4), self.b.ground(8, 8)), (64, 63))

    def test_move_column_is_one_columns_move_written_out(self):
        """_move_column's ops for one column, each written out by hand
        rather than taken from the rule itself -- the rule grade and its
        batters will move ground by too. Raised, the column is filled from
        its old ground ``g`` to under the new with what the block under
        ``g`` is the subsoil of: sand under sand, stone under stone, snow
        block under a snowfield, dirt under grass, and dirt again over cave
        air, an ore, water, anything built, or nothing at all past the
        world's floor. Lowered, it is cut away from over the new ground up
        to ``g``, and the filler is not wanted. Either way the surface is
        set at the new height, ``clearance`` blocks are cleared over it --
        none at 0 -- and the ground recorded there. Only the block under
        ``g`` is read, in the Builder's own dimension, and nothing is
        written."""
        air, stone = "minecraft:air", "minecraft:stone"
        grass = "minecraft:grass_block[snowy=false]"
        under = {(1, 1): "minecraft:sand", (2, 1): "minecraft:snow_block",
                 (3, 1): "minecraft:grass_block[snowy=false]",
                 (4, 1): "minecraft:cave_air", (5, 1): "minecraft:diamond_ore",
                 (6, 1): "minecraft:water[level=0]",
                 (7, 1): "minecraft:bricks"}
        for (x, z), block in under.items():
            self.save.set_block(x, 62, z, block)
        fillers = {(1, 1): "minecraft:sand", (2, 1): "minecraft:snow_block",
                   (3, 1): "minecraft:dirt", (4, 1): "minecraft:dirt",
                   (5, 1): "minecraft:dirt", (6, 1): "minecraft:dirt",
                   (7, 1): "minecraft:dirt",
                   (8, 1): "minecraft:stone"}      # stone under the stone
        written = self.b.blocks_set
        for (x, z), filler in fillers.items():
            with self.subTest(raised_over=(x, z)):
                self.assertEqual(self.b._move_column(x, z, 63, 66, grass, 0), [
                    ("fill", (x, 63, z), (x, 65, z), filler),
                    ("set", (x, 66, z), grass),
                    ("ground", (x, z), 66)])
        self.assertEqual(self.b._move_column(1, 1, 63, 64, grass, 2), [
            ("fill", (1, 63, 1), (1, 63, 1), "minecraft:sand"),
            ("set", (1, 64, 1), grass),
            ("fill", (1, 65, 1), (1, 66, 1), air),
            ("ground", (1, 1), 64)], "raised one, a clearance over it")
        self.assertEqual(self.b._move_column(9, 1, 63, 60, stone, 3), [
            ("fill", (9, 61, 1), (9, 63, 1), air),
            ("set", (9, 60, 1), stone),
            ("fill", (9, 61, 1), (9, 63, 1), air),
            ("ground", (9, 1), 60)], "lowered three, cleared three")
        self.assertEqual(self.b._move_column(1, 1, 63, 62, stone, 0), [
            ("fill", (1, 63, 1), (1, 63, 1), air),
            ("set", (1, 62, 1), stone),
            ("ground", (1, 1), 62)], "lowered one, nothing cleared")
        self.assertEqual(self.b._move_column(10, 1, -64, -62, grass, 1), [
            ("fill", (10, -64, 1), (10, -63, 1), "minecraft:dirt"),
            ("set", (10, -62, 1), grass),
            ("fill", (10, -61, 1), (10, -61, 1), air),
            ("ground", (10, 1), -62)], "nothing under the world's floor")
        self.assertEqual(self.b.blocks_set, written, "a plan writes nothing")
        self.assertEqual(list(self.b.journal.written()), [])
        self.assertEqual(self.b._ground, {}, "nor records a ground")

        # The block under is read in the Builder's own dimension: soul sand
        # under the nether's column, never the overworld's sand.
        root = os.path.join(self.dir, "dimensions", "minecraft")
        shutil.copytree(os.path.join(root, "overworld"),
                        os.path.join(root, "the_nether"))
        save = MinecraftSave.load(self.dir)
        save.set_block(1, 62, 1, "minecraft:sand")
        save.set_block(1, 62, 1, "minecraft:soul_sand", dimension=NETHER)
        nylium = "minecraft:crimson_nylium"
        self.assertEqual(
            Builder(save, dimension=NETHER)._move_column(1, 1, 63, 65,
                                                         nylium, 0),
            [("fill", (1, 63, 1), (1, 64, 1), "minecraft:soul_sand"),
             ("set", (1, 65, 1), nylium), ("ground", (1, 1), 65)])

    def test_registered_pads_are_held_like_roads_hold_them(self):
        self.b.level_pad(0, 0, 6, 6, 66, name="keep")
        self.b.smooth_terrain((-8, -8), (12, 12), radius=3, max_shift=3)
        self.assertEqual(self.save.get_block(3, 66, 3).name,
                         "minecraft:grass_block", "the pad surface is intact")


class ReserveTests(BuilderBase):
    """A reservation is a pad that writes nothing: a grove, a garden, a lot
    for later, kept out of the routed writers' way."""

    ROAD = [(-8, 4), (16, 4)]

    def test_a_reservation_writes_nothing_and_keeps_a_road_out(self):
        self.assertEqual(self.b.reserve(8, 6, 2, 2, name="grove"), (2, 2, 8, 6))
        self.assertEqual(self.b.blocks_set, 0)
        with self.assertRaises(NBTError) as caught:
            self.b.finish()
        self.assertIn("nothing was built", str(caught.exception))
        with self.assertRaises(Refused) as caught:
            self.b.road(self.ROAD, width=3)
        self.assertIn("reservation 'grove' at (2, 3)", str(caught.exception))
        self.assertEqual(self.b.blocks_set, 0, "a refusal writes nothing")
        self.b.road(self.ROAD, width=3, through=("grove",))
        self.assertGreater(self.b.blocks_set, 0)

    def test_a_reservation_holds_smooth_terrain(self):
        self.save.fill((4, 64, 4), (4, 67, 4), "minecraft:stone")   # a cairn
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.b = Builder(self.save, seed=7)
        self.b.reserve(3, 3, 5, 5, name="cairn")
        report = self.b.smooth_terrain((0, 0), (12, 12), radius=2, max_shift=3)
        self.assertEqual(report["held"], 1)
        self.assertEqual(self.save.get_block(4, 67, 4).name, "minecraft:stone",
                         "the cairn was neither cut down nor averaged in")

    def test_a_reservation_name_is_never_reused(self):
        """``through=`` names a keep-out, so no two may share a name."""
        self.b.level_pad(0, 0, 4, 4, 64, name="inn")
        self.b.reserve(10, 10, 14, 14, name="grove")
        refused = {
            "a second reservation": lambda: self.b.reserve(
                20, 20, 22, 22, name="grove"),
            "a pad's name": lambda: self.b.reserve(20, 20, 22, 22, name="inn"),
            "no name": lambda: self.b.reserve(20, 20, 22, 22, name=""),
            "a pad named for a reservation": lambda: self.b.level_pad(
                20, 20, 22, 22, 64, name="grove"),
        }
        before = self.b.blocks_set
        for why, call in refused.items():
            with self.subTest(why), self.assertRaises(NBTError):
                call()
        self.assertEqual(self.b.blocks_set, before, "a refusal writes nothing")
        # An unnamed pad skips a name already taken rather than taking it.
        self.b.reserve(-12, -12, -10, -10, name="pad-2")
        self.b.level_pad(20, 20, 22, 22, 64)
        with self.assertRaises(Refused) as caught:
            self.b.road([(-14, -11), (21, -11), (21, 24)], width=1)
        message = str(caught.exception)
        self.assertIn("reservation 'pad-2'", message)
        self.assertIn("pad 'pad-3'", message)

    def test_a_pad_name_is_never_given_to_a_second_lot(self):
        """A pad's name is its keep-out, so a second lot leveled under it
        would take the first one's protection away, and a road would carve
        straight through the first house. The same lot leveled again keeps
        its name; the keep-out is unchanged."""
        self.b.level_pad(2, 2, 8, 8, 64, name="house")
        self.b.level_pad(10, 2, 12, 4, 64)                 # pad-2
        self.save.fill((4, 65, 4), (6, 67, 6), "minecraft:stone_bricks")
        before = self.b.blocks_set
        for name, rect in (("house", (20, 20, 26, 26)),
                           ("pad-2", (-10, -10, -8, -8))):
            with self.subTest(name=name), self.assertRaises(NBTError) as caught:
                self.b.level_pad(*rect, 64, name=name)
            self.assertIn(f"{name!r} is already the pad at", str(caught.exception))
        self.assertEqual(self.b.blocks_set, before, "a refusal writes nothing")
        self.assertEqual(self.name_at(20, 64, 20), "minecraft:air")
        self.assertEqual(self.b._pads["house"], (2, 2, 8, 8))

        with self.assertRaises(Refused) as caught:
            self.b.road([(5, 0), (5, 14)], width=3)
        self.assertIn("pad 'house'", str(caught.exception))
        self.assertEqual(self.name_at(5, 66, 5), "minecraft:stone_bricks")

        self.assertEqual(self.b.level_pad(8, 8, 2, 2, 64, name="house",
                                          clearance=0), (2, 2, 8, 8),
                         "the same lot, its corners in either order, again")

    def name_at(self, x, y, z):
        return self.save.get_block(x, y, z).name

    def test_keep_outs_are_named_in_the_order_they_were_registered(self):
        """Pads and reservations together, in the order they were set out
        -- not the order the road reaches them, nor pads first."""
        self.b.reserve(10, 0, 14, 8, name="grove")
        self.b.level_pad(0, 0, 4, 8, 64, name="cottage")
        self.b.reserve(-6, 0, -4, 8, name="orchard")
        with self.assertRaises(Refused) as caught:
            self.b.road([(-12, 4), (20, 4)], width=3)
        faults = caught.exception.faults
        self.assertEqual(len(faults), 3, faults)
        for fault, words in zip(faults, (
                "reservation 'grove' at (10, 3) (15 columns)",
                "pad 'cottage' at (0, 3) (15 columns)",
                "reservation 'orchard' at (-6, 3) (9 columns)")):
            self.assertIn(words, fault)
        self.assertIn("3 keep-outs are in the way (reservation 'grove', pad "
                      "'cottage', reservation 'orchard')", str(caught.exception))

    def test_a_reservation_corner_is_a_whole_block(self):
        """A fraction is refused, not truncated: int(-0.5) is 0, so the
        column at x=-1 would quietly fall outside the reservation."""
        for corners in ((-0.5, 0, 2.7, 3), ("4", "4", "6", "6"),
                        (True, 0, 3, 3)):
            with self.subTest(corners):
                with self.assertRaises(NBTError) as caught:
                    self.b.reserve(*corners, name="hut")
                self.assertIn("whole block", str(caught.exception))
        self.assertEqual(self.b.reserve(-1, 0, 2, 3, name="hut"),
                         (-1, 0, 2, 3), "a refused corner registered nothing")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class WritePathTests(BuilderBase):
    """What Builder writes is what it tracks, and a refusal writes nothing."""

    def test_a_bed_whose_head_is_in_the_next_chunk_is_tracked_there(self):
        self.b.place(15, 64, 5, "minecraft:red_bed[facing=east]")
        self.assertEqual(self.save.get_block(16, 64, 5).properties["part"], "head")
        self.assertIn((1, 0), self.b.journal.chunks(), "finish() must settle the head too")
        self.assertEqual(self.b.blocks_set, 2, "two blocks, counted once each")

    def test_a_fill_into_a_missing_chunk_writes_nothing(self):
        with self.assertRaises(NBTError):
            self.b.fill((30, 64, 0), (40, 64, 0), "minecraft:stone")
        self.assertEqual(self.save.get_block(30, 64, 0).name, "minecraft:air")
        self.assertEqual(self.b.blocks_set, 0)

    def test_a_fill_past_the_top_of_the_world_writes_nothing(self):
        with self.assertRaises(NBTError):
            self.b.fill((0, 300, 0), (0, 330, 0), "minecraft:stone")
        self.assertEqual(self.save.get_block(0, 300, 0).name, "minecraft:air")

    def test_light_stored_above_the_world_is_not_more_world(self):
        # The game keeps a section of light alone one past the top (Y=20).
        # It is above the build limit, so it must not stretch the range a
        # fill or a placement is checked against.
        self.save.dimension(OVERWORLD).chunk(0, 0).data["sections"].append(
            TagCompound({"Y": nbt.TagByte(20),
                         "SkyLight": nbt.TagByteArray([-1] * 2048)}))
        self.save.save()
        save = MinecraftSave.load(self.dir)
        b = Builder(save)
        with self.assertRaises(NBTError):
            b.fill((0, 318, 0), (0, 320, 0), "minecraft:stone")
        with self.assertRaises(NBTError):
            b.set(1, 320, 0, "minecraft:stone")
        with self.assertRaises(NBTError):
            b.place(2, 319, 0, "minecraft:oak_door[facing=north]")
        for x in (0, 2):
            for y in (318, 319):
                self.assertEqual(save.get_block(x, y, 0).name, "minecraft:air",
                                 f"a refused write left ({x}, {y}, 0) changed")
        self.assertEqual(b.blocks_set, 0)

    def test_an_unlit_pave_lays_the_floor_and_says_it_is_unlit(self):
        result = self.b.pave(0, 0, 3, 3, 63, light=None)
        self.assertEqual((result.cells, result.lights), (16, 0))
        self.assertIsNone(result.light_level)
        self.assertIn("unlit", repr(result))
        self.assertNotEqual(self.save.get_block(2, 63, 2).name, "minecraft:air")


class PlanBeforeWriteTests(BuilderBase):
    """A writer plans its whole call and checks the plan before its first
    write: a chunk the world has not generated, one the generator set aside
    part-way, or a height past the world refuses the call, and nothing is
    written -- not the first columns of it, as a column-by-column write
    left them."""

    def unfinish(self, chunk_x, chunk_z, status="minecraft:features", *,
                 maps=True):
        """Set a chunk aside part-way. ``maps=False`` stops it short of the
        maps the column reader trusts, as a chunk set aside before its
        terrain stage is."""
        dim = self.save.dimension(OVERWORLD)
        chunk = dim.chunk(chunk_x, chunk_z)
        chunk.data["Status"] = TagString(status)
        if not maps and "Heightmaps" in chunk.data:
            del chunk.data["Heightmaps"]
        dim.release(chunk_x, chunk_z)

    def name_at(self, x, y, z):
        return self.save.get_block(x, y, z).name

    def test_each_writer_refuses_an_ungenerated_chunk_before_its_first_write(self):
        """Chunk (2, 0) is not generated. Each footprint starts in chunk
        (1, 0) and runs into it, so a writer that wrote as it went had
        written its first columns by the time it found out."""
        self.save.set_block(28, 64, 0, "minecraft:short_grass")
        cases = {
            "level_pad": (lambda: self.b.level_pad(28, 0, 36, 4, 66),
                          (28, 66, 0), "minecraft:air"),
            "road": (lambda: self.b.road([(20, 30), (30, 30)], width=5,
                                         mix=LANE_MIX),
                     (18, 63, 28), "minecraft:stone"),
            "clear_vegetation": (lambda: self.b.clear_vegetation((28, 0), (33, 0)),
                                 (28, 64, 0), "minecraft:short_grass"),
            "frame_walls": (lambda: self.b.frame_walls(28, 0, 34, 4, 64, 3),
                            (28, 65, 0), "minecraft:air"),
            "gable_roof": (lambda: self.b.gable_roof(26, 0, 31, 4, 64, "z"),
                           (25, 64, -1), "minecraft:air"),
            "shaft": (lambda: self.b.shaft((31, 8), 64, 70,
                                           wall="minecraft:stone_bricks"),
                      (29, 64, 6), "minecraft:air"),
        }
        for writer, (call, cell, before) in cases.items():
            with self.subTest(writer=writer):
                with self.assertRaises(Refused):
                    call()
                self.assertEqual(self.b.blocks_set, 0, "nothing was written")
                self.assertEqual(self.name_at(*cell), before,
                                 f"the generated cell {cell} is unchanged")

    def test_the_rule_writers_refuse_missing_ground_and_the_sky_by_name(self):
        """A stair, a vault and a railway read the cells their rule needs
        before they know what to write, so they check the plan's reach
        before that read: a chunk the world never generated, or a height
        past it, is the Refused every writer gives -- one a script catching
        refusals carries on past -- and not the plain NBTError a failed
        read raises, which reads as corruption. And a block the game lacks
        is named before the reach is judged, as in every plan."""
        from heaton_voxel.craft.build import (Railway, SpiralStair,
                                              StraightStair, SwitchbackStair,
                                              Vault)

        ungenerated = "chunk (2, 0) is not generated"
        cases = {
            "spiral_stair": (lambda: self.b.spiral_stair((33, 8), 64, 70),
                             ungenerated),
            "switchback_stair": (lambda: self.b.switchback_stair(
                SwitchbackStair((30, 4), (35, 9)), 64, 70), ungenerated),
            "flight": (lambda: self.b.flight(
                StraightStair((30, 64, 8), "east", 4)), ungenerated),
            "vault": (lambda: self.b.vault(
                Vault((40, 75, 8), axis="z", half_span=3), 0, 4), ungenerated),
            "railway": (lambda: self.b.railway(
                Railway([(20, 64, 8), (40, 64, 8)])), ungenerated),
            "spiral_stair past the top": (
                lambda: self.b.spiral_stair((8, 8), 318, 322),
                "y=320..322 is past the world's -64..319"),
            "flight past the top": (
                lambda: self.b.flight(StraightStair((4, 318, 8), "east", 4),
                                      walk=False),
                "y=320..321 is past the world's -64..319"),
            "vault past the top": (
                lambda: self.b.vault(Vault((8, 330, 8), axis="z",
                                           half_span=3), 0, 4),
                "y=328..330 is past the world's -64..319"),
            "railway past the top": (
                lambda: self.b.railway(Railway([(4, 319, 8), (10, 319, 8)])),
                "y=320..320 is past the world's -64..319"),
        }
        for writer, (call, fault) in cases.items():
            with self.subTest(writer=writer):
                with self.assertRaises(Refused) as caught:
                    call()
                self.assertIn(fault, caught.exception.faults)
                self.assertIn("Nothing was written", str(caught.exception))
                self.assertEqual(self.b.blocks_set, 0, "nothing was written")
        self.assertEqual(self.name_at(31, 64, 8), "minecraft:air")

        with self.assertRaises(IllegalBlock):
            self.b.spiral_stair(SpiralStair((33, 8), tread="minecraft:oak_plank"),
                                64, 70)
        with self.assertRaises(IllegalBlock):
            self.b.railway(Railway([(20, 64, 8), (40, 64, 8)],
                                   bed="minecraft:cobble"))
        with self.assertRaises(IllegalBlock):
            self.b.railway(Railway([(20, 64, 8), (40, 64, 8)]),
                           seal="minecraft:cobble")

        # A cell a writer reads outside the reach it checked -- a sand bed's
        # footing under the bottom of the world -- is refused the same way.
        for cell, fault in (((40, 64, 0), ungenerated),
                            ((0, 400, 0), "y=400..400 is past the world's -64..319")):
            with self.subTest(read=cell), self.assertRaises(Refused) as caught:
                self.b._read(cell)
            self.assertEqual(caught.exception.faults, [fault])

    def test_a_chunk_still_generating_is_refused_by_name(self):
        """A chunk at the features stage has blocks, and a map once one is
        rebuilt, so every read a plan makes there succeeds -- and the game
        will generate over whatever is written into it."""
        self.save.set_block(20, 64, 4, "minecraft:short_grass")
        self.unfinish(1, 0)
        cases = {
            "level_pad": lambda: self.b.level_pad(10, 2, 20, 6, 65),
            "road": lambda: self.b.road([(10, 4), (22, 4)], mix=LANE_MIX),
            "clear_vegetation": lambda: self.b.clear_vegetation((18, 4), (21, 4)),
            "gable_roof": lambda: self.b.gable_roof(10, 2, 20, 6, 70, "x"),
            "fence_rect": lambda: self.b.fence_rect(10, 2, 20, 6),
            "ornamental_tree": lambda: self.b.ornamental_tree(20, 8),
            "arcade": lambda: self.b.arcade((17, 2), (17, 10), 64, 66),
            "shaft": lambda: self.b.shaft((17, 8), 64, 70,
                                          wall="minecraft:stone_bricks"),
            "spiral_stair": lambda: self.b.spiral_stair((24, 8), 64, 70),
        }
        for writer, call in cases.items():
            with self.subTest(writer=writer):
                with self.assertRaises(Refused) as caught:
                    call()
                self.assertIn("chunk (1, 0) is at minecraft:features",
                              str(caught.exception))
                self.assertEqual(self.b.blocks_set, 0, "nothing was written")
        self.assertEqual(self.name_at(10, 65, 2), "minecraft:air",
                         "the pad's corner in the finished chunk is untouched")
        self.assertEqual(self.name_at(20, 64, 4), "minecraft:short_grass")

    def test_the_primitives_refuse_an_unfinished_chunk(self):
        """Set, place and fill check what every plan is checked for; a bed
        whose head would land in the unfinished chunk leaves its foot
        unwritten too. A hand-made chunk marked a bare ``full`` is
        finished."""
        self.unfinish(1, 0)
        calls = {
            "set": lambda: self.b.set(20, 64, 4, "minecraft:stone"),
            "place": lambda: self.b.place(15, 64, 5,
                                          "minecraft:red_bed[facing=east]"),
            "fill": lambda: self.b.fill((10, 64, 4), (20, 64, 4),
                                        "minecraft:stone"),
        }
        for primitive, call in calls.items():
            with self.subTest(primitive=primitive):
                with self.assertRaises(Refused) as caught:
                    call()
                self.assertIn("not finished terrain", str(caught.exception))
        for cell in ((20, 64, 4), (15, 64, 5), (10, 64, 4)):
            self.assertEqual(self.name_at(*cell), "minecraft:air")
        self.assertEqual(self.b.blocks_set, 0)

        self.unfinish(1, 0, "full")
        b = Builder(self.save)
        b.set(20, 64, 4, "minecraft:stone")
        self.assertEqual(self.name_at(20, 64, 4), "minecraft:stone")

    def test_a_block_the_game_lacks_is_named_before_missing_ground(self):
        """The writers that read the ground as they plan check the blocks
        they would write first, as the plan's own check takes them: over a
        chunk that is not generated, a surface, a mix entry or a fence the
        game lacks is the IllegalBlock, not the NoSurface of the ground."""
        calls = {
            "level_pad": lambda: self.b.level_pad(
                28, 0, 36, 4, 64, surface="minecraft:quartz_wall"),
            "road": lambda: self.b.road(
                [(28, 8), (40, 8)], mix=[("minecraft:quartz_wall", 1)]),
            "fence_rect": lambda: self.b.fence_rect(28, 0, 36, 4, wood="quartz"),
            "pave": lambda: self.b.pave(28, 0, 36, 4,
                                        mix=[("minecraft:quartz_wall", 1)],
                                        light_level=MELT_SNOW),
        }
        before = self.b.rng.getstate()
        for writer, call in calls.items():
            with self.subTest(writer=writer), self.assertRaises(IllegalBlock):
                call()
        self.assertEqual(self.b.rng.getstate(), before)
        self.assertEqual(self.b.blocks_set, 0)
        with self.assertRaises(NoSurface):
            self.b.level_pad(28, 0, 36, 4, 64)

    def test_a_spawn_is_checked_where_a_write_would_be(self):
        """A cow spawned into ground the game has still to generate, into
        no chunk at all, or under or over the world is not one anybody
        meant: each is refused as set refuses it, before the yaw is drawn
        and before anything is written."""
        self.unfinish(1, 0)
        calls = {
            "unfinished": (lambda: self.b.spawn("minecraft:cow", 20, 65, 4),
                           "not finished terrain"),
            "ungenerated": (lambda: self.b.spawn("minecraft:cow", 40, 65, 4),
                            "chunk (2, 0) is not generated"),
            "under the world": (lambda: self.b.spawn("minecraft:cow", 4, -100, 4),
                                "past the world"),
            "over the world": (lambda: self.b.villager("farmer", 4, 4, y=400),
                               "past the world"),
        }
        before = self.b.rng.getstate()
        for why, (call, words) in calls.items():
            with self.subTest(why), self.assertRaises(Refused) as caught:
                call()
            self.assertIn(words, str(caught.exception))
        self.assertEqual(self.b.entities_added, 0)
        self.assertEqual(self.b.rng.getstate(), before, "no yaw was drawn")
        self.b.spawn("minecraft:cow", 4, 64, 4)
        self.assertEqual(self.b.entities_added, 1)

    def test_a_plan_is_checked_whole_and_names_every_fault(self):
        """One fault per chunk that cannot be built in, and one per end of
        the world a plan passes, all of them -- and the ops before the bad
        ones are not written either."""
        stone = "minecraft:stone"
        with self.assertRaises(Refused) as caught:
            self.b._commit([("set", (0, 64, 0), stone),
                            ("fill", (1, 64, 1), (2, 64, 2), stone),
                            ("set", (40, 64, 0), stone),
                            ("fill", (0, 64, 40), (0, 64, 60), stone),
                            ("set", (0, 330, 0), stone),
                            ("fill", (1, 318, 1), (1, 325, 1), stone)],
                           what="the plan")
        self.assertEqual(caught.exception.faults, [
            "chunk (2, 0) is not generated",
            "chunk (0, 2) is not generated",
            "chunk (0, 3) is not generated",
            "y=320..330 is past the world's -64..319",
        ])
        self.assertIn("4 faults", str(caught.exception))
        self.assertEqual(self.name_at(0, 64, 0), "minecraft:air")
        self.assertEqual(self.name_at(1, 64, 1), "minecraft:air")
        self.assertEqual(self.b.blocks_set, 0)

    def test_the_water_a_placement_takes_is_read_before_the_first_write(self):
        """``place_block`` refuses water the game does not have under a
        block that would take it; a plan reads that water before its first
        write, so the fence ahead of the bad cell is not left standing."""
        fence = "minecraft:oak_fence"
        self.save.set_block(1, 64, 0, "minecraft:water[level=16]")
        with self.assertRaises(NBTError) as caught:
            self.b._commit([("place", (0, 64, 0), fence),
                            ("place", (1, 64, 0), fence)], what="two fences")
        self.assertIn("(1, 64, 0) has level=16", str(caught.exception))
        self.assertEqual(self.name_at(0, 64, 0), "minecraft:air")
        self.assertEqual(self.b.blocks_set, 0)
        # Water an earlier op writes is what the placement finds there, and
        # a block that holds no water reads none.
        self.save.set_block(2, 64, 0, "minecraft:water[level=16]")
        self.b._commit([("set", (1, 64, 0), "minecraft:water"),
                        ("place", (1, 64, 0), fence),
                        ("place", (2, 64, 0), "minecraft:stone")],
                       what="a fence in a pool")
        self.assertEqual(self.save.get_block(1, 64, 0).properties["waterlogged"],
                         "true")

    def test_walls_past_the_top_of_the_world_write_nothing(self):
        with self.assertRaises(Refused) as caught:
            self.b.frame_walls(0, 0, 4, 4, 316, 6)
        self.assertEqual(caught.exception.faults,
                         ["y=320..321 is past the world's -64..319"])
        self.assertEqual(self.name_at(0, 316, 0), "minecraft:air")
        self.assertEqual(self.b.blocks_set, 0)

    def test_a_refused_pave_draws_nothing(self):
        """A floor's surface is drawn as it is planned, so everything the
        floor reaches is checked first: a pave past the top of the world
        is refused with the Builder's dice where they were."""
        before = self.b.rng.getstate()
        with self.assertRaises(Refused) as caught:
            self.b.pave(0, 0, 4, 4, 320, light_level=MELT_SNOW)
        self.assertIn("past the world", str(caught.exception))
        self.assertEqual(self.b.rng.getstate(), before)
        self.assertEqual(self.b.blocks_set, 0)

    def test_a_refused_road_draws_nothing(self):
        """A road's surface is drawn as each column is written, so a road
        refused before its first write leaves the Builder's dice where they
        were -- the next build is the one it would have been."""
        self.unfinish(1, 0)
        before = self.b.rng.getstate()
        with self.assertRaises(Refused):
            self.b.road([(0, 4), (22, 4)])
        self.assertEqual(self.b.rng.getstate(), before)

    def test_the_lighting_writers_are_planned_whole(self):
        """A floor and its lights, the lights and the snow they clear, the
        chains and their lamps: each call one plan, refused before a block
        of it is written -- not a half-paved square refused at the first
        cell over the line, saying nothing was written."""
        self.unfinish(1, 0, maps=False)
        self.save.fill((8, 72, 0), (24, 72, 8), "minecraft:stone")
        self.save.set_block(4, 64, 4, "minecraft:snow[layers=1]")
        stage = "chunk (1, 0) is at minecraft:features"
        cases = {
            # The floor's ground is read first, and a chunk still generating
            # has none yet.
            "pave": (lambda: self.b.pave(10, 2, 20, 6, 63,
                                         light_level=MELT_SNOW),
                     f"no surface at (16, 2): {stage}"),
            "light_cells": (lambda: self.b.light_cells(
                [(x, 63, 4) for x in range(0, 30)], light_level=MELT_SNOW),
                f"light_cells cannot be written: {stage}"),
            "hang_lamps": (lambda: self.b.hang_lamps(8, 0, 24, 8, 72, 64, drop=2,
                                                     light_level=8),
                           f"hang_lamps cannot be written: {stage}"),
        }
        for writer, (call, words) in cases.items():
            with self.subTest(writer=writer):
                with self.assertRaises(Refused) as caught:
                    call()
                self.assertIn(words, str(caught.exception))
                self.assertEqual(self.b.blocks_set, 0, "nothing was written")
        self.assertEqual(self.name_at(10, 63, 2), "minecraft:stone")
        self.assertEqual(self.name_at(4, 64, 4), "minecraft:snow")
        self.assertFalse(self.save.find_blocks((8, 64, 0), (24, 71, 8),
                                               "minecraft:iron_chain").matches,
                         "no chain hangs")

    def test_the_planned_rule_writers_refuse_a_chunk_still_generating(self):
        """The writers that plan a rule -- terrain, a vault and the span
        under it, stairs, a railway, a door -- refuse the unfinished chunk
        their plan reaches as every other writer does, before writing. The
        chunk keeps the game's maps, as one set aside at a later stage
        does, so smoothing reads ground there and plans with it; one
        without them has none (see the next test)."""
        from heaton_voxel.craft.build import (Railway, StraightStair,
                                              SwitchbackStair, Vault)
        from heaton_voxel.nbt import TagLongArray

        for x in range(8, 24):
            for z in range(0, 8):
                h = 63 + (x * 7 + z * 3) % 4
                if h > 63:
                    self.save.fill((x, 64, z), (x, h, z), "minecraft:dirt")
        self.save.rebuild_heightmaps((0, 0, 0), (31, 0, 15))
        self.save.fill((14, 64, 15), (18, 66, 15), "minecraft:stone")
        # Stone and dirt alone: every map the game keeps is WORLD_SURFACE.
        maps = self.save.dimension(OVERWORLD).chunk(1, 0).data["Heightmaps"]
        for key in ("MOTION_BLOCKING_NO_LEAVES", "OCEAN_FLOOR"):
            maps[key] = TagLongArray(maps["WORLD_SURFACE"])
        self.unfinish(1, 0)
        nave = Vault((17, 80, 4), half_span=7, pitch=1,
                     depth=(1, 1, 1, 2, 1, 1, 1, 4),
                     slab="minecraft:cobblestone_slab",
                     block="minecraft:cobblestone",
                     stair="minecraft:cobblestone_stairs",
                     springer="minecraft:cobblestone_slab[type=double]")
        stair = SwitchbackStair((14, 8), (17, 14), axis="z", start="northeast",
                                start_y=64, landing=3)
        cases = {
            "smooth_terrain": lambda: self.b.smooth_terrain((8, 0), (23, 7)),
            "vault": lambda: self.b.vault(nave, 2, 8, clear_under=True,
                                          overwrite=True),
            "switchback_stair": lambda: self.b.switchback_stair(stair, 64, 70),
            "flight": lambda: self.b.flight(StraightStair((21, 64, 13), "north", 3),
                                            walk=False),
            "railway": lambda: self.b.railway(Railway([(4, 64, 4), (24, 64, 4)])),
            "doorway": lambda: self.b.doorway(16, 64, 15, "north"),
        }
        for writer, call in cases.items():
            with self.subTest(writer=writer):
                with self.assertRaises(Refused) as caught:
                    call()
                self.assertIn(f"{writer} cannot be written: chunk (1, 0) is at "
                              f"minecraft:features", str(caught.exception))
                self.assertEqual(self.b.blocks_set, 0, "nothing was written")
        self.assertEqual(self.name_at(15, 64, 15), "minecraft:stone")

    def test_smoothing_reads_no_ground_in_a_chunk_still_generating(self):
        """A chunk the generator set aside part-way has no ground yet -- its
        blocks are not the terrain they will be -- so smoothing surveys
        around it, counting its columns as absent and why, rather than
        averaging its blocks in and planning writes the check refuses."""
        for x in range(8, 24):
            for z in range(0, 8):
                h = 63 + (x * 7 + z * 3) % 4
                if h > 63:
                    self.save.fill((x, 64, z), (x, h, z), "minecraft:dirt")
        self.save.rebuild_heightmaps((0, 0, 0), (31, 0, 15))
        self.unfinish(1, 0, maps=False)

        def partial():
            return [str(self.save.get_block(x, y, z)) for x in range(16, 24)
                    for z in range(0, 8) for y in range(60, 72)]

        before = partial()
        report = self.b.smooth_terrain((8, 0), (23, 7))
        self.assertEqual(report["absent"], {"partial": 64})
        self.assertEqual(report["surveyed"], 64, "chunk (0, 0)'s half alone")
        self.assertGreater(report["moved"], 0)
        self.assertEqual(partial(), before, "nothing was written there")
        self.assertNotIn((1, 0), self.b.journal.chunks())

    def test_a_tree_reaching_ungenerated_ground_is_refused_whole(self):
        """A canopy cell with nothing to read is planned all the same, so the
        tree is refused rather than planted with one side missing -- and
        the trunk's height, drawn as it was planned, is put back, so the
        next build is the one it would have been."""
        before = self.b.rng.getstate()
        with self.assertRaises(Refused) as caught:
            self.b.ornamental_tree(30, 30)
        self.assertIn("chunk (2, 1) is not generated", caught.exception.faults)
        self.assertEqual(self.b.blocks_set, 0, "nothing was written")
        self.assertEqual(self.b.rng.getstate(), before, "nothing was drawn")
        self.assertEqual(self.name_at(30, 64, 30), "minecraft:air")
        self.b.ornamental_tree(20, 20)
        self.assertGreater(self.b.blocks_set, 0, "a tree with room is planted")

    def test_a_malformed_state_late_in_a_plan_writes_nothing(self):
        """Every state a plan names is parsed before its first write, a mix's
        entries too, so a typo in the infill does not surface after the
        plinth is in."""
        with self.assertRaises(ValueError):
            self.b.frame_walls(0, 0, 4, 4, 64, 3, infill="minecraft:bricks[axis")
        self.assertEqual(self.name_at(0, 64, 0), "minecraft:air")
        before = self.b.rng.getstate()
        with self.assertRaises(ValueError):
            self.b.road([(0, 4), (10, 4)],
                        mix=[("minecraft:gravel", 3), ("minecraft:dirt_path[", 1)])
        self.assertEqual(self.b.rng.getstate(), before, "nothing was drawn")
        self.assertEqual(self.b.blocks_set, 0, "nothing was written")

    def test_a_placement_may_not_rewrite_a_neighbor_in_an_unfinished_chunk(self):
        """A placement settles the fences beside it, as a player's does, so a
        fence in the unfinished chunk next door is a cell the placement
        writes. A stone there is not: nothing re-derives it."""
        self.save.set_block(16, 64, 5, "minecraft:oak_fence")
        self.save.set_block(16, 64, 9, "minecraft:stone")
        self.unfinish(1, 0)
        with self.assertRaises(Refused) as caught:
            self.b.place(15, 64, 5, "minecraft:oak_fence")
        self.assertIn("chunk (1, 0) is at minecraft:features", str(caught.exception))
        self.assertEqual(self.name_at(15, 64, 5), "minecraft:air")
        self.assertEqual(str(self.save.get_block(16, 64, 5)), "minecraft:oak_fence",
                         "the fence next door is not rewritten")
        self.b.place(15, 64, 9, "minecraft:oak_fence")
        self.assertEqual(self.name_at(15, 64, 9), "minecraft:oak_fence")

    def test_clear_vegetation_plans_the_leaf_only_a_planned_chunk_reveals(self):
        """The map has not heard of a tree planted after it was rebuilt, so
        the first column cleared in a chunk reads its top off the map and
        every later one off the blocks -- which the plan must know too: the
        chunk it means to write in is as unmapped as one it has. A birch with
        a gap under its leaf is where it shows."""
        from unittest import mock

        self.save.set_block(1, 64, 1, "minecraft:short_grass")
        for y in range(64, 68):
            self.save.set_block(3, y, 1, "minecraft:birch_log[axis=y]")
        self.save.set_block(3, 70, 1, "minecraft:birch_leaves[persistent=true]")
        plans = []
        real = Builder._commit

        def spy(builder, ops, *, what):
            ops = list(ops)
            plans.append((what, builder.blocks_set, ops))
            return real(builder, ops, what=what)

        with mock.patch.object(Builder, "_commit", spy):
            self.b.clear_vegetation((0, 1), (4, 1))
        ((what, written, ops),) = plans
        self.assertEqual((what, written), ("clear_vegetation", 0),
                         "the whole clearing is planned before its first write")
        self.assertIn(("set", (3, 70, 1), "minecraft:air"), ops)
        self.assertEqual(self.name_at(3, 70, 1), "minecraft:air")


class OverlayFormTests(BuilderBase):
    """One plan, three readers: each takes every form a plan entry may take."""

    FORMS = {
        "name": "minecraft:glowstone",
        "state": BlockState("minecraft:glowstone"),
        "pair": ("minecraft:glowstone", {}),
    }

    def test_the_walk_the_light_and_the_railway_read_every_form(self):
        from heaton_voxel.craft.build import Railway
        from heaton_voxel.craft.light import simulate_block_light
        from heaton_voxel.craft.survey import StepSpace

        line = Railway([(0, 64, 0), (3, 64, 0)], boost_every=None)
        for form, block in self.FORMS.items():
            with self.subTest(form=form):
                plan = {(1, 63, 0): block}
                self.assertEqual(StepSpace(self.save, overlay=plan).kind(1, 63, 0),
                                 "full")
                field = simulate_block_light(self.save, (1, 63, 0), (1, 63, 0),
                                             overlay=plan, margin=2)
                self.assertEqual(field.level(1, 63, 0), 15)
                self.assertIsInstance(line.verify(self.save, overlay=plan), list)


class RefusalTests(BuilderBase):
    """A refusal is catchable apart from corruption, and names every fault."""

    def test_no_ground_is_no_surface_and_still_an_nbt_error(self):
        with self.assertRaises(NoSurface) as caught:
            self.b.ground(500, 500)
        self.assertIsInstance(caught.exception, Refused)
        self.assertIsInstance(caught.exception, NBTError)

    def test_no_surface_says_why_and_where(self):
        """The reasons a survey records, carried by the refusal itself."""
        refusal = NoSurface("no surface at (500, 500)", reason="ungenerated",
                            cell=(500, 500))
        self.assertEqual((refusal.reason, refusal.cell), ("ungenerated", (500, 500)))
        self.assertEqual(refusal.faults, [])
        self.assertIsNone(NoSurface("no surface").reason, "the reason is optional")

    def test_a_refusal_carries_every_fault_not_just_the_first(self):
        from heaton_voxel.craft.build import Railway

        self.save.fill((0, 50, 0), (12, 63, 15), "minecraft:stone")
        for x in (3, 6):
            self.save.set_block(x, 64, 0, "minecraft:rail[shape=north_south]")
        with self.assertRaises(Refused) as caught:
            self.b.railway(Railway([(0, 64, 0), (9, 64, 0)], boost_every=None))
        self.assertEqual(len(caught.exception.faults), 2)

    def test_a_damaged_heightmap_is_corruption_not_empty_ground(self):
        from heaton_voxel.craft.survey import walk_surface
        from heaton_voxel.nbt import TagLongArray

        self.save.fill((0, 50, 0), (15, 63, 15), "minecraft:stone")
        self.save.rebuild_heightmaps((0, 0, 0), (15, 0, 15))
        self.save.save()
        save = MinecraftSave.load(self.dir)
        maps = save.dimension(OVERWORLD).chunk(0, 0).data["Heightmaps"]
        maps["WORLD_SURFACE"] = TagLongArray(list(maps["WORLD_SURFACE"])[:-1])

        with self.assertRaises(NBTError) as caught:
            walk_surface(save, 3, 3)
        self.assertNotIsInstance(caught.exception, NoSurface)
        builder = Builder(save)
        for call in (lambda: builder.ground(3, 3),
                     lambda: builder.smooth_terrain((0, 0), (4, 4))):
            with self.assertRaises(NBTError) as caught:
                call()
            self.assertNotIsInstance(caught.exception, NoSurface,
                                     "corruption must not read as no ground")


class StrictWriteTests(BuilderBase):
    """Every write is a block the game has. An id it lacks, or a property or
    a value the block does not take, is refused before anything is written:
    the game would load the id as air and the value as the block's default,
    and say so only in a log nobody reads, while every render drew it.
    ``allow=`` is the way past for a modded block."""

    def test_a_block_the_game_lacks_is_refused_and_nothing_is_written(self):
        before = self.save.get_block(2, 64, 2)
        with self.assertRaises(IllegalBlock) as ctx:
            self.b.set(2, 64, 2, "minecraft:quartz_wall")
        self.assertIsInstance(ctx.exception, Refused)
        message = str(ctx.exception)
        self.assertIn("minecraft:quartz_wall", message)
        self.assertIn("Nothing was written", message)
        self.assertIn("minecraft:quartz_slab", ctx.exception.suggestions)
        self.assertEqual(self.save.get_block(2, 64, 2), before)
        self.assertEqual(self.b.blocks_set, 0)
        # Legality reads nothing, and comes before the chunk check: a block
        # the game lacks is that refusal even where no chunk was generated.
        with self.assertRaises(IllegalBlock):
            self.b.set(500, 64, 500, "minecraft:quartz_wall")

    def test_a_retired_name_is_refused_naming_its_replacement(self):
        with self.assertRaises(IllegalBlock) as ctx:
            self.b.fill((2, 64, 2), (2, 66, 2), "minecraft:chain[axis=y]")
        self.assertIn("minecraft:iron_chain", str(ctx.exception))
        self.assertEqual(ctx.exception.suggestions, ("minecraft:iron_chain",))
        for y in (64, 65, 66):
            self.assertEqual(self.save.get_block(2, y, 2).name, "minecraft:air")
        self.assertEqual(self.b.blocks_set, 0)
        self.b.fill((2, 64, 2), (2, 66, 2), "minecraft:iron_chain[axis=y]")
        self.assertEqual(self.b.blocks_set, 3)
        # The other rename: what a decade of chunks call grass.
        with self.assertRaises(IllegalBlock) as ctx:
            self.b.set(3, 64, 2, "minecraft:grass")
        self.assertIn("renamed minecraft:short_grass", str(ctx.exception))
        self.assertEqual(ctx.exception.suggestions, ("minecraft:short_grass",))
        self.assertEqual(self.save.get_block(3, 64, 2).name, "minecraft:air")
        self.assertEqual(self.b.blocks_set, 3)

    def test_a_property_or_value_the_block_does_not_take_is_refused(self):
        for label, call in (
                ("set", lambda: self.b.set(
                    2, 64, 2, "minecraft:oak_stairs[facing=up]")),
                ("place", lambda: self.b.place(
                    2, 64, 2, "minecraft:oak_door[facing=up]")),
                ("fill", lambda: self.b.fill(
                    (2, 64, 2), (3, 64, 3), "minecraft:stone[snowy=false]")),
                ("a bool", lambda: self.b.set(2, 64, 2, BlockState(
                    "minecraft:oak_stairs", {"waterlogged": True})))):
            with self.subTest(write=label), self.assertRaises(IllegalBlock):
                call()
        with self.assertRaises(IllegalBlock) as ctx:
            self.b.set(2, 64, 2, "minecraft:oak_stairs[facing=up]")
        self.assertIn("facing is one of east, north, south, west",
                      str(ctx.exception))
        for cell in ((2, 64, 2), (2, 65, 2), (3, 64, 3)):
            self.assertEqual(self.save.get_block(*cell).name, "minecraft:air")
        self.assertEqual(self.b.blocks_set, 0)

    def test_a_roof_with_one_illegal_block_writes_none_of_itself(self):
        with self.assertRaises(IllegalBlock) as ctx:
            self.b.gable_roof(1, 1, 9, 7, 68, "x", slab="minecraft:quartz_wall")
        self.assertIn("gable_roof", str(ctx.exception))
        self.assertEqual(self.save.find_blocks(
            (-1, 60, -1), (11, 80, 9), "_stairs", match="suffix").matches, [],
            "not one of the roof's stairs was written")
        self.assertEqual(self.b.blocks_set, 0)

    def test_every_illegal_state_in_a_plan_is_named_at_once(self):
        with self.assertRaises(IllegalBlock) as ctx:
            self.b.frame_walls(0, 0, 4, 4, 64, 3, log="minecraft:quartz_wall",
                               infill="minecraft:oak_stairs[facing=up]",
                               plinth="minecraft:chain")
        refused = ctx.exception
        message = str(refused)
        self.assertIn("frame_walls cannot be written: 3 states", message)
        self.assertIn("(minecraft:chain, minecraft:quartz_wall, "
                      "minecraft:oak_stairs[facing=up])", message,
                      "every state, in the order the plan names them")
        self.assertEqual(len(refused.faults), 3)
        for fault, name in zip(refused.faults, ("minecraft:chain",
                                                "minecraft:quartz_wall",
                                                "minecraft:oak_stairs")):
            self.assertIn(name, fault)
        self.assertEqual(refused.suggestions[0], "minecraft:iron_chain")
        self.assertIn("minecraft:quartz_slab", refused.suggestions)
        self.assertEqual(self.save.get_block(0, 64, 0).name, "minecraft:air")
        self.assertEqual(self.b.blocks_set, 0)

    def test_a_mix_with_one_illegal_entry_is_refused_before_a_die_is_cast(self):
        """A mix is checked entry by entry, by name, and never by drawing:
        a refusal leaves the rng where it was, so a session that carries on
        builds what it would have built without the refused call."""
        mix = [("minecraft:cobblestone", 3),
               ("minecraft:polished_diorite_wall", 1)]
        for writer, call in (
                ("road", lambda: self.b.road([(0, 4), (12, 4)], mix=mix)),
                ("pave", lambda: self.b.pave(0, 0, 6, 6, 63, mix=mix,
                                             light_level=MELT_SNOW)),
                # A lamp that shines and still is not a state the game has
                # -- glowstone takes no properties -- is the floor's too.
                ("pave", lambda: self.b.pave(
                    0, 0, 6, 6, 63, light="minecraft:glowstone[lit=true]",
                    light_level=MELT_SNOW)),
                ("ornamental_tree", lambda: self.b.ornamental_tree(
                    20, 20, leaves="minecraft:quartz_leaves"))):
            with self.subTest(writer=writer):
                before = self.b.rng.getstate()
                with self.assertRaises(IllegalBlock) as ctx:
                    call()
                self.assertIn(writer, str(ctx.exception))
                self.assertEqual(self.b.rng.getstate(), before,
                                 "nothing was drawn")
                self.assertEqual(self.b.blocks_set, 0)
        counts = self.save.histogram((0, 63, 0), (12, 63, 6)).counts
        self.assertEqual({str(state): n for state, n in counts.items()},
                         {"minecraft:stone": 13 * 7}, "the ground is untouched")

    def test_a_lamp_the_game_lacks_is_named_with_the_rest_of_the_call(self):
        """A lamp is weighed before the plan is checked, and a lamp the game
        lacks is dark because the game loads it as air. That refusal is
        still the call's: opened by the writer, closed by "Nothing was
        written", and naming every other block of the call the game lacks
        beside the lamp -- not the lamp's alone, so that fixing it does not
        just uncover the next one."""
        lamp = "minecraft:mod_lamp"
        for writer, call, named in (
                ("light_cells", lambda: self.b.light_cells(
                    [(30, 30)], light=lamp, light_level=MELT_SNOW), (lamp,)),
                ("pave", lambda: self.b.pave(
                    0, 0, 6, 6, 63, mix=[("minecraft:quartz_wall", 1)],
                    light=lamp, light_level=MELT_SNOW),
                 ("minecraft:quartz_wall", lamp)),
                ("hang_lamps", lambda: self.b.hang_lamps(
                    26, 26, 30, 30, 70, 63, light=lamp,
                    chain="minecraft:chain[axis=y]", light_level=8),
                 ("minecraft:chain[axis=y]", lamp)),
                ("shaft", lambda: self.b.shaft(
                    (28, 28), 64, 70, wall="minecraft:quartz_wall",
                    light=lamp, light_level=8),
                 ("minecraft:quartz_wall", lamp))):
            with self.subTest(writer=writer):
                before = self.b.rng.getstate()
                with self.assertRaises(IllegalBlock) as ctx:
                    call()
                message = str(ctx.exception)
                self.assertTrue(message.startswith(
                    f"{writer} cannot be written: "), message)
                self.assertTrue(message.endswith("Nothing was written"),
                                message)
                self.assertEqual(len(ctx.exception.faults), len(named))
                for fault, name in zip(ctx.exception.faults, named):
                    self.assertIn(BlockState.parse(name).name, fault)
                self.assertEqual(self.b.rng.getstate(), before)
                self.assertEqual(self.b.blocks_set, 0)

    def test_the_lighting_writers_name_a_bad_block_before_reading_the_ground(self):
        """Legality is pure and comes first (Builder._commit), in a writer
        that reads the ground and the chunks before its plan as in any
        other: a lamp, a chain or a lining the game lacks is that refusal
        even where the cells run into a chunk never generated -- chunk
        (2, 0) -- or one still generating, and the hint naming its
        replacement is not held back until the chunk is put right."""
        dim = self.save.dimension(OVERWORLD)
        dim.chunk(1, 0).data["Status"] = TagString("minecraft:features")
        dim.release(1, 0)
        for writer, call, named in (
                ("light_cells", lambda: self.b.light_cells(
                    [(x, 4) for x in range(26, 40)],
                    light="minecraft:glowstone[lit=true]",
                    light_level=MELT_SNOW),
                 "minecraft:glowstone has no property 'lit'"),
                ("hang_lamps", lambda: self.b.hang_lamps(
                    26, 2, 40, 6, 72, 63, chain="minecraft:chain[axis=y]",
                    light_level=8),
                 "renamed minecraft:iron_chain"),
                ("shaft", lambda: self.b.shaft(
                    (20, 8), 64, 70, wall="minecraft:quartz_wall",
                    light="minecraft:glowstone", light_level=8),
                 "minecraft:quartz_wall")):
            with self.subTest(writer=writer):
                with self.assertRaises(IllegalBlock) as ctx:
                    call()
                message = str(ctx.exception)
                self.assertTrue(message.startswith(
                    f"{writer} cannot be written: "), message)
                self.assertIn(named, message)
                self.assertEqual(self.b.blocks_set, 0)

    def test_the_allowance_is_fixed_for_the_session(self):
        """What a session passed it remembers, so narrowing the allowance
        afterwards would let the first ids through unchecked: it is set
        once, as the Builder is made."""
        modded = Builder(self.save, allow=("create:",))
        modded.set(2, 64, 2, "create:gearbox")
        with self.assertRaises(AttributeError):
            modded.allow = frozenset()
        self.assertEqual(modded.allow, frozenset({"create:"}))

    def test_allow_passes_a_namespace_or_an_exact_id(self):
        with self.assertRaises(IllegalBlock) as ctx:
            self.b.set(2, 64, 2, "create:gearbox")
        self.assertIn("allow=", str(ctx.exception))
        modded = Builder(self.save, allow={"create:"})
        self.assertEqual(modded.allow, frozenset({"create:"}))
        modded.set(2, 64, 2, "create:gearbox")
        self.assertEqual(self.save.get_block(2, 64, 2).name, "create:gearbox")
        exact = Builder(self.save, allow=("create:gearbox",))
        exact.set(3, 64, 2, "create:gearbox")
        with self.assertRaises(IllegalBlock):
            exact.set(4, 64, 2, "create:shaft")
        with self.assertRaises(IllegalBlock, msg="allow= passes, it does "
                               "not stop checking the game's own blocks"):
            modded.set(4, 64, 2, "minecraft:quartz_wall")

    def test_an_allowance_that_could_match_nothing_is_refused(self):
        for allow in ({"gearbox"}, {"Create:"}, {"create:gearbox[facing=up]"}):
            with self.subTest(allow=allow), self.assertRaises(NBTError) as ctx:
                Builder(self.save, allow=allow)
            self.assertNotIsInstance(ctx.exception, Refused)

    def test_each_distinct_state_is_checked_once_a_session(self):
        from unittest import mock

        from heaton_voxel.craft.build import builder as module

        checked = []
        real = module.check_state

        def counting(block, **kwargs):
            checked.append(str(block))
            return real(block, **kwargs)

        with mock.patch.object(module, "check_state", counting):
            self.b.frame_walls(0, 0, 6, 6, 64, 4)
            self.assertEqual(sorted(checked), [
                "minecraft:cobblestone",
                "minecraft:stripped_spruce_log[axis=y]",
                "minecraft:white_terracotta"],
                "three states, each once, over every cell of the walls")
            self.b.frame_walls(8, 0, 14, 6, 64, 4)
            self.b.set(20, 64, 20, "minecraft:cobblestone")
            self.assertEqual(len(checked), 3, "a state that passed is not "
                             "checked again this session")


class GroundTests(BuilderBase):
    """The ground a Builder stands its work on: read once per column the way
    the column reader reads it, set where the session shaped it, and named,
    with why, where there is none."""

    def test_ground_under_snow_after_a_rebuild_is_the_ground(self):
        """A rebuilt map is WORLD_SURFACE alone, which lands on the snow. No
        motion-blocking map counts a snow layer, so the ground is the stone
        under it, and the snow is the probe's to report."""
        self.save.set_block(4, 64, 4, "minecraft:snow[layers=3]")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.assertEqual(self.save.surface_y(4, 4, "WORLD_SURFACE"), 64)
        self.assertEqual(self.b.ground(4, 4), 63)
        self.assertEqual(self.b.probe(4, 4).snow, 3)

    def test_set_known_and_forget_ground(self):
        b = self.b
        self.assertIsNone(b.known_ground(4, 4), "nothing read, nothing known")
        self.assertEqual(b.ground(4, 4), 63)
        self.assertEqual(b.known_ground(4, 4), 63, "the probe it read")
        b.set_ground(4, 4, 70)
        self.assertEqual((b.ground(4, 4), b.known_ground(4, 4)), (70, 70))
        # The probe is the world as first read; forgetting reads it again.
        self.save.set_block(4, 64, 4, "minecraft:stone")
        self.save.rebuild_heightmaps((0, 0, 0), (15, 0, 15))
        self.assertEqual(b.probe(4, 4).y, 63, "cached as first read")
        b.forget_ground(4, 4)
        self.assertIsNone(b.known_ground(4, 4))
        self.assertEqual(b.ground(4, 4), 64)
        b.set_ground(5, 5, 80)
        b.ground(6, 6)
        b.forget_ground()
        self.assertEqual((b.known_ground(5, 5), b.known_ground(6, 6)),
                         (None, None), "every column forgotten")
        for call in (lambda: b.forget_ground(4), lambda: b.forget_ground(z=4)):
            with self.assertRaises(TypeError):
                call()

    def test_stand_below_finds_the_floor_under_a_roof(self):
        self.save.fill((0, 64, 0), (4, 64, 4), "minecraft:stone")     # floor
        self.save.fill((0, 70, 0), (4, 70, 4), "minecraft:stone")     # roof
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.assertEqual(self.b.ground(2, 2), 70, "the roof is the top")
        self.assertEqual(self.b.stand_below(2, 2, 69), 64)
        # Read as it is now, never cached: what the session lays under the
        # roof is what is stood on.
        self.b.set(2, 65, 2, "minecraft:oak_planks")
        self.assertEqual(self.b.stand_below(2, 2, 69), 65)
        self.assertEqual(self.b.ground(2, 2), 70)
        with self.assertRaises(NoSurface) as caught:
            self.b.stand_below(500, 500, 69)
        self.assertEqual(caught.exception.reason, "ungenerated")

    def test_no_surface_names_its_reason_and_cell(self):
        """No chunk, a chunk still generating and a column of nothing are
        each a NoSurface that says which; a chunk that cannot be read is
        corruption, and a plain NBTError."""
        dim = self.save.dimension(OVERWORLD)
        self.save.fill((8, 60, 8), (8, 63, 8), "minecraft:air")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        chunk = dim.chunk(1, 0)
        chunk.data["Status"] = TagString("minecraft:features")
        del chunk.data["Heightmaps"]
        dim.release(1, 0)
        cases = {(500, 500): ("ungenerated", "chunk (31, 31) is not generated"),
                 (20, 4): ("partial", "chunk (1, 0) is at minecraft:features"),
                 (8, 8): ("empty", "nothing in the column")}
        # ground() first, on its own: it is what every writer reads through.
        for read in ("ground", "probe"):
            for cell, (reason, words) in cases.items():
                with self.subTest(cell=cell, read=read):
                    with self.assertRaises(NoSurface) as caught:
                        getattr(self.b, read)(*cell)
                    self.assertEqual((caught.exception.reason,
                                      caught.exception.cell), (reason, cell))
                    self.assertIn(words, str(caught.exception))
        dim.chunk(-1, -1).data["DataVersion"] = TagInt(100)
        dim.release(-1, -1)
        with self.assertRaises(NBTError) as caught:
            self.b.ground(-8, -8)
        self.assertNotIsInstance(caught.exception, NoSurface,
                                 "corruption must not read as no ground")

    def test_the_ground_writers_let_corruption_through(self):
        """A chunk that cannot be read is corruption, not ground that is
        missing: smoothing does not survey around it as it does round a
        chunk not yet generated, and a pad over it is not refused as one
        over no ground. Both raise the chunk's own error, before anything
        is written."""
        dim = self.save.dimension(OVERWORLD)
        dim.chunk(-1, -1).data["DataVersion"] = TagInt(100)
        dim.release(-1, -1)
        calls = {"smooth_terrain": lambda: self.b.smooth_terrain((-8, -8), (8, 8)),
                 "level_pad": lambda: self.b.level_pad(-4, -4, 4, 4, 66)}
        for writer, call in calls.items():
            with self.subTest(writer=writer):
                with self.assertRaises(NBTError) as caught:
                    call()
                self.assertNotIsInstance(caught.exception, NoSurface,
                                         "corruption must not read as no ground")
                self.assertIn("DataVersion 100", str(caught.exception))
                self.assertEqual(self.b.blocks_set, 0, "nothing was written")
        self.assertEqual(self.save.get_block(2, 64, 2).name, "minecraft:air")

    def test_a_pond_under_a_felled_branch_is_still_water(self):
        """Felling a branch that hung over a pond leaves that column's ground
        on the pond's surface, set by the session -- and it is still a
        pond: a pad over it is refused there with every other wet column,
        and filled from the bed like them. So is ground a script set on
        water it never filled."""
        self.save.fill((2, 61, 2), (6, 63, 6), "minecraft:water[level=0]")
        self.save.set_block(4, 68, 4, "minecraft:oak_log[axis=x]")
        self.save.set_block(4, 69, 4, "minecraft:oak_leaves[distance=1,"
                            "persistent=false,waterlogged=false]")
        self.save.fill((10, 61, 10), (10, 63, 10), "minecraft:water[level=0]")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.assertEqual(self.b.clear_vegetation((2, 2), (6, 6)), 2)
        self.assertEqual(self.b.ground(4, 4), 63, "the pond's surface")
        cleared = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.level_pad(2, 2, 6, 6, 64)
        faults = caught.exception.faults
        self.assertEqual(len(faults), 25, "the felled column with the rest")
        self.assertIn((4, 4), {(f.x, f.z) for f in faults})
        for call in (lambda: self.b.level_pad(4, 4, 4, 4, 64),
                     lambda: self.b.pave(4, 4, 4, 4, 64, light=None)):
            with self.assertRaises(Refused):
                call()
        self.assertEqual(self.b.blocks_set, cleared, "nothing was written")
        self.b.level_pad(2, 2, 6, 6, 64, over_water="fill")
        for x, z in ((3, 3), (4, 4)):
            self.assertEqual([self.save.get_block(x, y, z).name
                              for y in range(60, 65)],
                             ["minecraft:stone"] + ["minecraft:dirt"] * 3
                             + ["minecraft:grass_block"], f"filled at {(x, z)}")
        self.b.set_ground(10, 10, 63)
        with self.assertRaises(Refused) as caught:
            self.b.level_pad(10, 10, 10, 10, 64)
        self.assertEqual([(f.x, f.z, f.bed_y) for f in caught.exception.faults],
                         [(10, 10, 60)])

    def test_a_pad_reaching_ungenerated_ground_writes_nothing(self):
        """The pad's ground is read whole before its first column is cut, so
        the column beyond the generated world refuses the pad, naming
        itself and why."""
        with self.assertRaises(NoSurface) as caught:
            self.b.level_pad(28, 0, 36, 4, 66, name="edge")
        self.assertEqual((caught.exception.reason, caught.exception.cell),
                         ("ungenerated", (32, 0)))
        self.assertEqual(self.b.blocks_set, 0, "nothing was written")
        self.assertEqual(self.save.get_block(28, 66, 0).name, "minecraft:air")
        self.assertNotIn("edge", self.b._pads)

    def test_a_frozen_pond_is_held_by_smoothing(self):
        """Ice on water is no ground to average: the pond keeps its sheet
        and its water where they are -- a column whose ground the session
        set on the ice, as felling a branch over it does, among them."""
        self.save.fill((4, 61, 4), (5, 61, 5), "minecraft:water[level=0]")
        self.save.fill((4, 62, 4), (5, 62, 5), "minecraft:ice")
        self.save.fill((4, 63, 4), (5, 63, 5), "minecraft:air")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
        self.b.set_ground(5, 5, 62)
        report = self.b.smooth_terrain((0, 0), (9, 9), radius=2, max_shift=3)
        self.assertEqual(report["surveyed"], 96, "the four pond columns held")
        for x in (4, 5):
            for z in (4, 5):
                self.assertEqual([self.save.get_block(x, y, z).name
                                  for y in (61, 62, 63)],
                                 ["minecraft:water", "minecraft:ice",
                                  "minecraft:air"], f"the pond at {(x, z)}")

    def test_villager_fence_and_tree_take_an_explicit_floor(self):
        """``y=`` is the floor a thing stands on -- a deck at 70 over ground
        at 63 -- with its feet, posts or trunk one above it."""
        self.save.fill((0, 70, 0), (12, 70, 12), "minecraft:oak_planks")
        self.assertEqual(self.b.ground(6, 6), 63, "the map has not heard of it")
        self.b.villager("farmer", 6, 6, y=70)
        entities = self.save.dimension(OVERWORLD).entity_chunk(0, 0)
        (villager,) = entities.data["Entities"]
        self.assertEqual(float(villager["Pos"][1]), 71.0)
        self.b.fence_rect(1, 1, 3, 3, y=70, gate=(2, 1, "north"))
        self.assertEqual(self.save.get_block(1, 71, 1).name, "minecraft:oak_fence")
        self.assertEqual(self.save.get_block(3, 71, 3).name, "minecraft:oak_fence")
        self.assertEqual(self.save.get_block(2, 71, 1).name,
                         "minecraft:oak_fence_gate")
        self.b.ornamental_tree(9, 9, y=70)
        self.assertEqual(self.save.get_block(9, 71, 9).name, "minecraft:oak_log")
        for x, z in ((1, 1), (2, 1), (9, 9)):
            self.assertEqual(self.save.get_block(x, 64, z).name, "minecraft:air",
                             f"nothing stands on the ground at {(x, z)}")


class WaterTests(unittest.TestCase):
    """The ground writers over the legacy fixture's open sea: real game
    data, water to y=62 over a sand and stone bed twenty to forty blocks
    down. Water is refused unless the call says to bridge it or fill it."""

    def setUp(self):
        from tests.test_render import SEA_X0, SEA_Z0, make_world

        self.dir = tempfile.mkdtemp(prefix="build-water-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.save = MinecraftSave.load(make_world(self.dir))
        self.b = Builder(self.save, seed=7)
        self.x0, self.z0 = SEA_X0 + 2, SEA_Z0 + 2

    def column(self, x, z, y0, y1):
        return [str(self.save.get_block(x, y, z)) for y in range(y0, y1 + 1)]

    def holds_water(self, x, y, z):
        from heaton_voxel.craft.block_facts import holds_fluid

        block = self.save.get_block(x, y, z)
        return holds_fluid(block.name, block.properties)

    def test_a_pad_over_water_is_refused_and_nothing_is_written(self):
        x0, z0 = self.x0, self.z0
        before = self.column(x0 + 1, z0 + 1, 30, 70)
        with self.assertRaises(Refused) as caught:
            self.b.level_pad(x0, z0, x0 + 3, z0 + 3, 64, name="jetty")
        faults = caught.exception.faults
        self.assertEqual(len(faults), 16, "every wet column, not just the first")
        self.assertTrue(all(isinstance(f, SurfaceProbe) and f.wet for f in faults))
        self.assertEqual((faults[0].x, faults[0].z), (x0, z0))
        message = str(caught.exception)
        for words in (f"({x0}, {z0})", "16 columns", "over_water='bridge'",
                      "over_water='fill'"):
            self.assertIn(words, message)
        self.assertEqual(self.b.blocks_set, 0, "nothing was written")
        self.assertEqual(self.column(x0 + 1, z0 + 1, 30, 70), before)
        self.assertNotIn("jetty", self.b._pads)

    def test_a_road_across_water_is_refused_before_it_writes(self):
        rng = self.b.rng.getstate()
        with self.assertRaises(Refused) as caught:
            self.b.road([(self.x0, self.z0 + 6), (self.x0 + 11, self.z0 + 6)])
        self.assertEqual(len(caught.exception.faults), 42, "14 by 3")
        self.assertEqual(self.b.blocks_set, 0, "nothing was written")
        self.assertEqual(self.b.rng.getstate(), rng, "nothing was drawn")
        self.assertTrue(self.holds_water(self.x0 + 4, 62, self.z0 + 6))

    def test_paving_over_water_is_refused(self):
        for y in (62, None):
            with self.subTest(y=y):
                with self.assertRaises(Refused) as caught:
                    self.b.pave(self.x0, self.z0, self.x0 + 4, self.z0 + 4, y,
                                light_level=MELT_SNOW)
                self.assertEqual(len(caught.exception.faults), 25)
                self.assertEqual(self.b.blocks_set, 0, "nothing was written")
        self.assertTrue(self.holds_water(self.x0 + 2, 62, self.z0 + 2))

    def test_lights_set_into_water_are_refused_unless_bridged(self):
        """A bare (x, z) over the sea stands on the water's surface, and a
        light set there is set into the sea: refused, naming every wet
        cell, unless the call says to bridge it. Nothing is filled under a
        light, so 'fill' is not offered and is refused by name."""
        x0, z0 = self.x0, self.z0
        cells = [(x0 + dx, z0) for dx in range(5)]
        with self.assertRaises(Refused) as caught:
            self.b.light_cells(cells, light_level=MELT_SNOW)
        self.assertEqual(len(caught.exception.faults), 5)
        message = str(caught.exception)
        self.assertIn("over_water='bridge'", message)
        self.assertNotIn("over_water='fill'", message)
        self.assertEqual(self.b.blocks_set, 0, "nothing was written")
        self.assertTrue(self.holds_water(x0 + 2, 62, z0))
        with self.assertRaises(ValueError):
            self.b.light_cells(cells, over_water="fill", light_level=MELT_SNOW)
        result = self.b.light_cells(cells, over_water="bridge",
                                    light_level=MELT_SNOW)
        self.assertGreater(result.lights, 0)
        lit = [x for x, z in cells
               if self.save.get_block(x, 62, z).name == "minecraft:glowstone"]
        self.assertEqual(len(lit), result.lights, "set into the surface")

    def test_bridge_builds_over_the_water_as_before(self):
        """Bridged, the build stands on the water's surface, the way a
        heightmap's ground always put it, and the sea stays under it."""
        x0, z0 = self.x0, self.z0
        self.b.level_pad(x0, z0, x0 + 3, z0 + 3, 64, over_water="bridge")
        for (x, z), wall in (((x0, z0), "minecraft:stone_bricks"),
                             ((x0 + 1, z0 + 1), "minecraft:dirt")):
            self.assertEqual(self.save.get_block(x, 64, z).name,
                             "minecraft:grass_block")
            self.assertEqual(self.save.get_block(x, 63, z).name, wall)
            self.assertTrue(self.holds_water(x, 62, z), "the sea is under it")
        self.b.road([(x0, z0 + 8), (x0 + 11, z0 + 8)], over_water="bridge")
        self.assertIn(self.save.get_block(x0 + 5, 62, z0 + 8).name,
                      {name for name, _ in ROAD_MIX}, "a causeway at the surface")
        self.assertTrue(self.holds_water(x0 + 5, 61, z0 + 8))
        self.b.pave(x0 + 6, z0, x0 + 9, z0 + 3, 62, over_water="bridge",
                    light=None)
        self.assertNotEqual(self.save.get_block(x0 + 7, 62, z0 + 1).name,
                            "minecraft:water")
        self.assertTrue(self.holds_water(x0 + 7, 61, z0 + 1))

    def test_fill_replaces_the_water_from_the_bed(self):
        """Filled, the pad is built up from the sea bed: the ring wall at
        its edge and dirt inside it, where the water was, so no water is
        left standing under the surface. Paving and a road fill their
        cells with dirt the same way."""
        x0, z0 = self.x0, self.z0
        beds = {(x, z): self.b.probe(x, z).y
                for z in range(z0, z0 + 8) for x in range(x0, x0 + 4)}
        swath = {(x, z): self.b.probe(x, z).y
                 for z in range(z0 + 8, z0 + 11) for x in range(x0 - 1, x0 + 8)}
        self.b.level_pad(x0, z0, x0 + 3, z0 + 3, 63, over_water="fill")
        self.b.pave(x0, z0 + 4, x0 + 3, z0 + 7, 62, over_water="fill",
                    light=None)
        self.b.road([(x0, z0 + 9), (x0 + 6, z0 + 9)], over_water="fill")
        for (x, z), bed in swath.items():
            with self.subTest(road=(x, z)):
                self.assertEqual({self.save.get_block(x, y, z).name
                                  for y in range(bed + 1, 62)}, {"minecraft:dirt"})
                self.assertIn(self.save.get_block(x, 62, z).name,
                              {name for name, _ in ROAD_MIX})
        for (x, z), bed in beds.items():
            pad = z < z0 + 4
            top = 63 if pad else 62
            edge = x in (x0, x0 + 3) or z in (z0, z0 + 3)
            fill = ("minecraft:stone_bricks" if pad and edge else "minecraft:dirt")
            with self.subTest(column=(x, z)):
                self.assertEqual({self.save.get_block(x, y, z).name
                                  for y in range(bed + 1, top)}, {fill})
                self.assertFalse(self.holds_water(x, top, z))
        self.assertEqual(self.b.ground(x0 + 1, z0 + 1), 63)

    def test_fill_refuses_a_pad_below_the_waterline(self):
        with self.assertRaises(Refused) as caught:
            self.b.level_pad(self.x0, self.z0, self.x0 + 3, self.z0 + 3, 60,
                             over_water="fill")
        message = str(caught.exception)
        for words in ("waterline at y=62", "y=60", "16 columns"):
            self.assertIn(words, message)
        self.assertEqual(self.b.blocks_set, 0, "nothing was written")

    def test_fill_refuses_a_lid(self):
        """Ice over a room has no bed to fill from: filled, it is a lid
        whoever is underneath would have poured onto them."""
        d = tempfile.mkdtemp(prefix="build-lid-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        make_save(d, [(0, 0)])
        save = MinecraftSave.load(d)
        save.fill((0, 50, 0), (15, 59, 15), "minecraft:stone")
        save.fill((0, 63, 0), (15, 63, 15), "minecraft:ice")
        save.rebuild_heightmaps((0, 0, 0), (15, 0, 15))
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.level_pad(2, 2, 4, 4, 64, over_water="fill")
        self.assertTrue(all(f.lid and f.bed_y is None
                            for f in caught.exception.faults))
        self.assertIn("no bed", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        b.level_pad(2, 2, 4, 4, 64, over_water="bridge")
        self.assertEqual(save.get_block(3, 63, 3).name, "minecraft:ice",
                         "bridged, the sheet stays a lid")

    def test_an_unknown_over_water_choice_is_refused_by_name(self):
        x0, z0 = self.x0, self.z0
        calls = {"level_pad": lambda: self.b.level_pad(x0, z0, x0 + 1, z0 + 1, 64,
                                                       over_water="drain"),
                 "road": lambda: self.b.road([(x0, z0), (x0 + 4, z0)],
                                             over_water="drain"),
                 "pave": lambda: self.b.pave(x0, z0, x0 + 1, z0 + 1, 62,
                                             over_water="drain",
                                             light_level=MELT_SNOW)}
        for writer, call in calls.items():
            with self.subTest(writer=writer):
                with self.assertRaises(ValueError) as caught:
                    call()
                self.assertIn("over_water", str(caught.exception))
                self.assertIn("'drain'", str(caught.exception))
        self.assertEqual(self.b.blocks_set, 0)

    def test_ground_this_session_shaped_is_not_refused(self):
        """A script that filled the water itself says so, and the pad stands
        on what it built -- the secret lab's wet ground, filled and set."""
        x0, z0 = self.x0, self.z0
        for z in range(z0, z0 + 4):
            for x in range(x0, x0 + 4):
                self.b.fill((x, self.b.probe(x, z).y + 1, z), (x, 62, z),
                            "minecraft:dirt")
                self.b.set_ground(x, z, 62)
        self.b.level_pad(x0, z0, x0 + 3, z0 + 3, 63)
        self.assertEqual(self.save.get_block(x0 + 1, 63, z0 + 1).name,
                         "minecraft:grass_block")
        self.assertEqual(self.save.get_block(x0 + 1, 62, z0 + 1).name,
                         "minecraft:dirt")

    def test_kelp_column_is_not_smoothed(self):
        """WORLD_SURFACE and both motion-blocking maps stop at a kelp tip at
        the waterline, because it holds water, while OCEAN_FLOOR lands on
        the gravel under it: the column is the sea, not ground to average."""
        from tests.test_survey import KELP_COLUMN

        x, z = KELP_COLUMN
        report = self.b.smooth_terrain((x - 2, z - 2), (x + 2, z + 2))
        self.assertEqual(report["surveyed"], 0)
        self.assertEqual(report["absent"], {})

    def test_ground_set_on_the_kelp_is_not_smoothed(self):
        """A height the session set where the block is still water -- the
        kelp tip at the waterline, which is no water by its name -- is the
        sea all the same, and smoothing holds it."""
        from tests.test_survey import KELP_COLUMN

        x, z = KELP_COLUMN
        self.assertEqual(self.save.get_block(x, 62, z).name, "minecraft:kelp")
        self.b.set_ground(x, z, 62)
        report = self.b.smooth_terrain((x, z), (x, z))
        self.assertEqual(report["surveyed"], 0)


class DoorwayTests(BuilderBase):
    def wall(self, block="minecraft:stone"):
        self.save.fill((0, 50, 0), (15, 63, 15), "minecraft:stone")
        for x in range(0, 16):
            for y in (64, 65, 66):
                self.save.set_block(x, y, 8, block)

    def test_an_iron_doorway_opens_from_both_sides(self):
        from heaton_voxel.craft.survey import reachable

        self.wall()
        written = self.b.doorway(8, 64, 8, "south", door="minecraft:iron_door")
        self.assertEqual(len(written), 4, "two halves and two buttons")
        self.assertEqual(self.save.get_block(8, 65, 8).properties["half"], "upper")
        walk = reachable(self.save, (0, 63, 0), (0, 60, 0), (15, 70, 15))
        self.assertIn((8, 63, 12), walk.reached)
        self.assertFalse(walk.stranded)

    def test_a_wooden_doorway_needs_no_buttons(self):
        self.wall()
        self.assertEqual(len(self.b.doorway(8, 64, 8, "south")), 2)

    def test_an_iron_doorway_in_glass_is_refused_before_anything_is_written(self):
        """A refusal, naming both sides -- the doorway is the caller's to
        move, not a damaged chunk -- and a button the game lacks is named
        first, as in every plan."""
        self.wall("minecraft:glass")
        with self.assertRaises(Refused) as caught:
            self.b.doorway(8, 64, 8, "south", door="minecraft:iron_door")
        self.assertIn("conducting", str(caught.exception))
        self.assertEqual(len(caught.exception.faults), 2)
        self.assertEqual(self.save.get_block(8, 64, 8).name, "minecraft:glass")
        with self.assertRaises(IllegalBlock):
            self.b.doorway(8, 64, 8, "south", door="minecraft:iron_door",
                           button="minecraft:stone_buton")
        self.assertEqual(self.b.blocks_set, 0)

    def test_a_door_the_game_would_drop_is_refused_before_it_is_hung(self):
        """The door in a shell's opening is the last write of the recipe,
        and the one the survival audit would otherwise never see before
        finish(): a door over a hole, or a doorway cut from behind a torch,
        is refused whole, as a wall's or a shell's plan is."""
        self.wall()
        self.save.set_block(8, 63, 8, "minecraft:air")
        with self.assertRaises(Refused) as caught:
            self.b.doorway(8, 64, 8, "south", door="minecraft:iron_door")
        self.assertEqual([(f.cell, f.kind) for f in caught.exception.faults],
                         [((8, 64, 8), "no footing")])
        self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(self.save.get_block(8, 64, 8).name, "minecraft:stone")

        self.save.set_block(8, 63, 8, "minecraft:stone")
        self.save.set_block(8, 65, 9, "minecraft:wall_torch[facing=south]")
        with self.assertRaises(Refused) as caught:
            self.b.doorway(8, 64, 8, "south")
        self.assertEqual([(f.cell, f.kind) for f in caught.exception.faults],
                         [((8, 65, 9), "no footing")])
        self.assertEqual(self.b.blocks_set, 0)
        self.save.set_block(8, 65, 9, "minecraft:air")
        self.assertEqual(len(self.b.doorway(8, 64, 8, "south")), 2)


class FinishAuditTests(BuilderBase):
    def test_finish_lists_what_the_game_will_not_keep(self):
        self.save.fill((0, 50, 0), (15, 63, 15), "minecraft:stone")
        self.b.set(3, 64, 3, "minecraft:red_bed[facing=east,occupied=false,part=foot]")
        report = self.b.finish()
        self.assertEqual([f.kind for f in report.unsupported], ["lone half"])
        self.assertIn("1 unsupported", repr(report))

    def test_strict_finish_refuses_and_names_every_fault(self):
        self.save.fill((0, 50, 0), (15, 63, 15), "minecraft:stone")
        self.b.set(3, 64, 3, "minecraft:red_bed[facing=east,occupied=false,part=foot]")
        self.b.set(6, 64, 6, "minecraft:torch")
        self.b.set(6, 63, 6, "minecraft:air")
        with self.assertRaises(Refused) as caught:
            self.b.finish(strict=True)
        self.assertEqual(len(caught.exception.faults), 2)

    def test_finish_lists_a_blocked_chest_and_strict_refuses_it(self):
        """A chest under a block that conducts power will not open
        (``ChestBlock.isBlockedChestByBlock``): finish() lists it, and
        strict=True refuses it, as it refuses anything but a one-way door.
        A chest under glass is neither."""
        self.save.fill((0, 50, 0), (15, 63, 15), "minecraft:stone")
        self.save.set_block(3, 64, 3, "minecraft:chest")
        self.save.set_block(5, 64, 3, "minecraft:chest")
        self.b.set(3, 65, 3, "minecraft:bookshelf")
        self.b.set(5, 65, 3, "minecraft:glass")
        report = self.b.finish()
        self.assertEqual([(f.cell, f.kind) for f in report.unsupported],
                         [((3, 64, 3), "blocked chest")])
        strict = Builder(self.save)
        strict.set(3, 65, 3, "minecraft:bookshelf")
        with self.assertRaises(Refused) as caught:
            strict.finish(strict=True)
        self.assertEqual([f.kind for f in caught.exception.faults],
                         ["blocked chest"])


class PlanAuditTests(BuilderBase):
    """``Builder._audit_plan``: the one survival audit a writer runs before
    it writes, refusing only what its own plan would leave behind."""

    WALL = "minecraft:stone"
    DOOR = ("minecraft:iron_door[facing=south,half={},hinge=left,open=false,"
            "powered=false]")
    NORTH = "minecraft:stone_button[face=wall,facing=north,powered=false]"
    SOUTH = "minecraft:stone_button[face=wall,facing=south,powered=false]"

    def setUp(self):
        super().setUp()
        self.save.fill((0, 50, 0), (15, 63, 15), "minecraft:stone")

    def refused(self, ops, what="the plan"):
        """The faults of a refused audit, as (cell, kind), having checked
        that it read and wrote nothing."""
        before = self.b.rng.getstate()
        with self.assertRaises(Refused) as caught:
            self.b._audit_plan(ops, what=what)
        message = str(caught.exception)
        self.assertTrue(message.startswith(f"{what} would leave "), message)
        self.assertTrue(message.endswith("Nothing was written"), message)
        self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(self.b.rng.getstate(), before)
        return [(fault.cell, fault.kind) for fault in caught.exception.faults]

    def door(self, *buttons):
        """An iron door in a stone wall along z=8, with ``buttons``."""
        for x in range(0, 16):
            for y in (64, 65, 66):
                self.save.set_block(x, y, 8, self.WALL)
        self.save.set_block(8, 64, 8, self.DOOR.format("lower"))
        self.save.set_block(8, 65, 8, self.DOOR.format("upper"))
        for cell, button in buttons:
            self.save.set_block(*cell, button)

    def test_a_plan_that_blocks_a_chest_is_refused(self):
        self.save.set_block(4, 64, 4, "minecraft:chest")
        faults = self.refused([("set", (4, 65, 4), "minecraft:gravel")],
                              what="the counter")
        self.assertEqual(faults, [((4, 64, 4), "blocked chest")])
        self.assertEqual(self.save.get_block(4, 65, 4).name, "minecraft:air")
        # A top that does not conduct is no fault.
        self.b._audit_plan([("set", (4, 65, 4),
                             "minecraft:smooth_quartz_slab[type=bottom]")],
                           what="the counter")

    def test_a_plan_that_unsupports_a_torch_is_refused(self):
        """A wall cut from behind a torch, and a torch set on nothing: both
        the plan's, named together in cell order."""
        self.save.set_block(6, 64, 6, "minecraft:stone")
        self.save.set_block(6, 64, 7, "minecraft:wall_torch[facing=south]")
        faults = self.refused([
            ("set", (9, 70, 9), "minecraft:torch"),
            ("fill", (5, 64, 6), (7, 66, 6), "minecraft:air")])
        self.assertEqual(faults, [((6, 64, 7), "no footing"),
                                  ((9, 70, 9), "no footing")])

    def test_a_fault_the_plan_did_not_make_is_not_its_to_answer_for(self):
        """A torch already on nothing and a chest already blocked, inside
        the audited box: a plan beside them that leaves them as they were
        passes, gravel for stone over the chest included."""
        self.save.set_block(5, 66, 5, "minecraft:torch")
        self.save.set_block(6, 64, 5, "minecraft:chest")
        self.save.set_block(6, 65, 5, "minecraft:stone")
        self.b._audit_plan([("set", (5, 64, 5), "minecraft:stone"),
                            ("set", (6, 65, 5), "minecraft:gravel")],
                           what="the wall")
        self.assertEqual(self.b.blocks_set, 0, "an audit writes nothing")
        # A new fault beside them is still the plan's.
        self.assertEqual(self.refused([("set", (4, 66, 5), "minecraft:torch")]),
                         [((4, 66, 5), "no footing")])

    def test_a_placement_is_audited_whole(self):
        """A ``place`` brings its partner cells into the plan -- a bed's
        head -- so a bed placed is whole, while a foot set alone is a lone
        half."""
        bed = "minecraft:red_bed[facing=east,occupied=false,part=foot]"
        self.b._audit_plan([("place", (3, 64, 3), bed)], what="the bedroom")
        self.assertEqual(self.refused([("set", (3, 64, 3), bed)]),
                         [((3, 64, 3), "lone half")])

    def test_a_door_is_the_plans_fault_only_where_it_shuts_it_further(self):
        # Two-way, then one-way: glass sticks a button and conducts nothing.
        self.door(((7, 65, 7), self.NORTH), ((9, 65, 9), self.SOUTH))
        self.assertEqual(
            self.refused([("set", (9, 65, 8), "minecraft:glass")]),
            [((8, 64, 8), "one-way door")])
        # One-way, then sealed.
        self.save.set_block(9, 65, 9, "minecraft:air")
        self.assertEqual(
            self.refused([("set", (7, 65, 8), "minecraft:glass")]),
            [((8, 64, 8), "sealed door")])
        # Sealed, then one-way, is better, if not good: passed.
        self.save.set_block(7, 65, 7, "minecraft:air")
        self.b._audit_plan([("set", (7, 65, 7), self.NORTH)],
                           what="a button")
        # And a door hung that opens from one side only is the plan's.
        self.save.fill((8, 64, 8), (8, 65, 8), self.WALL)
        self.assertEqual(self.refused([
            ("place", (8, 64, 8), self.DOOR.format("lower")),
            ("set", (7, 65, 7), self.NORTH)]),
            [((8, 64, 8), "one-way door")])

    def test_a_door_left_as_it_was_is_not_the_plans(self):
        """A one-way door and a sealed one already in the wall, inside the
        audited box: a plan beside them, or one that writes the one-way
        door back as it stands, leaves each as shut as it was and passes.
        Only a door the plan shuts further is its fault."""
        self.door(((7, 65, 7), self.NORTH))                   # one-way
        self.save.set_block(12, 64, 8, self.DOOR.format("lower"))
        self.save.set_block(12, 65, 8, self.DOOR.format("upper"))   # sealed
        self.b._audit_plan([("set", (10, 64, 10), "minecraft:oak_planks")],
                           what="the floor")
        self.b._audit_plan([("place", (8, 64, 8), self.DOOR.format("lower")),
                            ("set", (10, 64, 10), "minecraft:oak_planks")],
                           what="the door")
        self.assertEqual(self.b.blocks_set, 0)

    def test_a_fault_the_plan_writes_over_is_its_own(self):
        """A torch already on nothing is somebody else's; a lantern the
        plan sets in its place, or another torch, is the plan's, while the
        same torch written back is not."""
        self.save.set_block(5, 70, 5, "minecraft:torch")
        for block in ("minecraft:lantern[hanging=false,waterlogged=false]",
                      "minecraft:soul_torch"):
            with self.subTest(block=block):
                self.assertEqual(self.refused([("set", (5, 70, 5), block)]),
                                 [((5, 70, 5), "no footing")])
        self.b._audit_plan([("set", (5, 70, 5), "minecraft:torch")],
                           what="the same torch")

    def test_a_fault_across_a_chunk_border_is_seen(self):
        """The plan writes in one chunk and leaves the fault in the next: a
        torch at x=15 hangs on a wall at x=16, which the plan cuts."""
        self.save.set_block(16, 64, 5, "minecraft:stone")
        self.save.set_block(15, 64, 5, "minecraft:wall_torch[facing=west]")
        self.assertEqual(self.refused([("set", (16, 64, 5), "minecraft:air")]),
                         [((15, 64, 5), "no footing")])
        # And the other way: a torch set in chunk 1 on chunk 0's wall.
        self.save.set_block(15, 64, 9, "minecraft:stone")
        self.b._audit_plan([("set", (16, 64, 9),
                             "minecraft:wall_torch[facing=east]")],
                           what="the torch")

    def test_a_later_op_is_audited_over_an_earlier_one(self):
        """The plan as the writes would leave it: a fill that clears a
        torch's wall and a set that puts the wall back is no fault, and the
        same two the other way round is."""
        self.save.set_block(5, 64, 5, "minecraft:stone")
        self.save.set_block(5, 64, 6, "minecraft:wall_torch[facing=south]")
        clear = ("fill", (4, 64, 5), (6, 64, 5), "minecraft:air")
        wall = ("set", (5, 64, 5), "minecraft:stone")
        self.b._audit_plan([clear, wall], what="the wall")
        self.assertEqual(self.refused([wall, clear]),
                         [((5, 64, 6), "no footing")])

    def test_a_bare_state_is_audited_as_the_game_loads_it(self):
        """A property a planned state leaves out is the game's default: a
        bare wall torch faces north and hangs on nothing here, and a bare
        iron door placed in a wall with no button is shut from both
        sides."""
        self.assertEqual(
            self.refused([("set", (5, 66, 5), "minecraft:wall_torch")]),
            [((5, 66, 5), "no footing")])
        for x in range(0, 16):
            for y in (64, 65, 66):
                self.save.set_block(x, y, 8, self.WALL)
        self.assertEqual(
            self.refused([("place", (6, 64, 8), "minecraft:iron_door")]),
            [((6, 64, 8), "sealed door")])

    def test_a_mix_drawn_as_it_is_written_cannot_be_audited(self):
        from heaton_voxel.craft.build._common import _Mix

        with self.assertRaises(NBTError) as caught:
            self.b._audit_plan([("set", (3, 64, 3), _Mix(ROAD_MIX))],
                               what="the road")
        self.assertNotIsInstance(caught.exception, Refused)
        self.assertIn("mix", str(caught.exception))

    def test_a_placement_is_planned_with_the_water_it_takes(self):
        """A placement is planned as ``place_block`` writes it, water and
        all: a coral fan takes falling water as well as a source, and a
        stair the source alone; the small dripleaf any water, flowing
        included; a block wet by default -- the fan, a sea pickle -- is
        planned dry where it takes none; and a campfire placed into a
        source is out. Each plan is the very state ``place_block`` then
        writes there."""
        waters = {2: "minecraft:water[level=0]", 4: "minecraft:water[level=3]",
                  6: "minecraft:water[level=8]", 8: None}
        blocks = ["minecraft:tube_coral_fan", "minecraft:oak_stairs[facing=north]",
                  "minecraft:small_dripleaf[facing=east]", "minecraft:sea_pickle",
                  "minecraft:campfire[facing=south]"]
        cells = {}
        for row, block in enumerate(blocks):
            z = 2 + 2 * row
            for x, water in waters.items():
                if water is not None:
                    self.save.set_block(x, 64, z, water)
                cells[(block, x)] = (x, 64, z)
        plans = {key: self.b._plan_cells([("place", cell, key[0])], what="t")
                 for key, cell in cells.items()}
        self.assertEqual(self.b.blocks_set, 0, "planning writes nothing")

        def wet(key):
            return plans[key][cells[key]].properties.get("waterlogged")

        fan, stair, leaf, pickle, fire = blocks
        self.assertEqual([wet((fan, x)) for x in waters],
                         ["true", "false", "true", "false"])
        self.assertEqual([wet((stair, x)) for x in waters],
                         ["true", None, None, None])
        self.assertEqual([wet((leaf, x)) for x in waters],
                         ["true", "true", "true", None])
        self.assertEqual([wet((pickle, x)) for x in waters],
                         ["true", "false", "false", "false"])
        self.assertEqual(plans[(fire, 2)][cells[(fire, 2)]].properties["lit"],
                         "false")
        upper = plans[(leaf, 4)][(4, 65, cells[(leaf, 4)][2])]
        self.assertEqual(upper.properties["half"], "upper")

        for (block, x), cell in cells.items():
            self.assertTrue(self.save.place_block(*cell, block))
        for key, plan in plans.items():
            for where, state in plan.items():
                with self.subTest(block=key[0], water=waters[key[1]],
                                  cell=where):
                    self.assertEqual(state.properties.get("waterlogged"),
                                     self.save.get_block(*where).properties
                                     .get("waterlogged"))


    def test_a_placement_reads_the_plan_before_it(self):
        """A placement takes its water from what the plan before it leaves
        in its cell, ahead of the world, as ``_commit`` writes the plan op
        by op: a stair placed where the plan has just cleared a source is
        dry, and a coral fan placed where the plan has just set one is
        wet. Each plan is what ``_commit`` then writes."""
        source = "minecraft:water[level=0]"
        self.save.set_block(3, 64, 10, source)
        cases = {
            (3, 64, 10): [("set", (3, 64, 10), "minecraft:air"),
                          ("place", (3, 64, 10),
                           "minecraft:oak_stairs[facing=north]")],
            (5, 64, 10): [("set", (5, 64, 10), source),
                          ("place", (5, 64, 10), "minecraft:tube_coral_fan")],
        }
        wet = {}
        for cell, ops in cases.items():
            wet[cell] = self.b._plan_cells(ops, what="t")[cell] \
                .properties.get("waterlogged")
        self.assertEqual(wet, {(3, 64, 10): None, (5, 64, 10): "true"})
        for cell, ops in cases.items():
            self.b._commit(ops, what="t")
            self.assertEqual(self.save.get_block(*cell).properties
                             .get("waterlogged"), wet[cell], cell)


class ContainPlanTests(BuilderBase):
    """``Builder._contain_plan``: the one water check a writer runs over its
    plan before it writes -- refusing a plan that lets water out, naming
    every leak, or walling the water off where it stands with ``seal=``.
    Lava and a chunk that is not there are never sealed."""

    WATER = "minecraft:water[level=0]"

    def setUp(self):
        super().setUp()
        # A pond in the stone, x 2..6, z 2..6, water 61..63 on a floor at 60.
        self.save.fill((2, 61, 2), (6, 63, 6), self.WATER)
        self.bank = [("fill", (7, 61, 2), (9, 63, 6), "minecraft:air")]
        self.opened = [(7, y, z) for y in range(61, 64) for z in range(2, 7)]

    def test_a_plan_that_opens_a_lake_to_air_is_refused_naming_the_leaks(self):
        rng = self.b.rng.getstate()
        with self.assertRaises(Refused) as caught:
            self.b._contain_plan(self.bank, what="the cut")
        leaks = caught.exception.faults
        self.assertEqual([leak.cell for leak in leaks], self.opened)
        self.assertEqual({leak.kind for leak in leaks}, {"spreads"})
        self.assertEqual([leak.source for leak in leaks],
                         [(6, y, z) for _, y, z in self.opened])
        message = str(caught.exception)
        self.assertTrue(message.startswith(
            "the cut would let water out: 15 leaks, first: (7, 61, 2): the "
            "water at (6, 61, 2) spreads into the minecraft:air"), message)
        self.assertIn("seal='minecraft:cobblestone'", message)
        self.assertTrue(message.endswith("Nothing was written"), message)
        self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(self.b.rng.getstate(), rng)
        # A plan that keeps the water -- the cut a block further off --
        # passes and asks for no seal.
        self.assertEqual(self.b._contain_plan(
            [("fill", (8, 61, 2), (9, 63, 6), "minecraft:air")],
            what="the cut"), [])

    def test_a_seal_walls_the_water_off_where_it_stands(self):
        """Each leak's water is replaced by the seal, railway's way, and the
        plan with its seals keeps the water: written, the pond is a block
        smaller and nothing runs into the cut."""
        from heaton_voxel.craft.style import Mix
        from heaton_voxel.craft.survey import contained

        seals = self.b._contain_plan(self.bank, what="the cut",
                                     seal="minecraft:cobblestone")
        self.assertEqual(seals, [("set", (6, y, z), "minecraft:cobblestone")
                                 for _, y, z in self.opened])
        self.assertEqual(self.b.blocks_set, 0, "a check writes nothing")
        self.b._commit(self.bank + seals, what="the cut")
        self.assertEqual(self.save.get_block(6, 62, 4).name,
                         "minecraft:cobblestone")
        self.assertEqual(self.save.get_block(5, 62, 4), BlockState.parse(self.WATER))
        after = contained(self.save, {(7, 62, 4): "minecraft:air"})
        self.assertEqual((after.leaks, after.standing), ([], []))
        # A seal water passes, enters or waterlogs is no seal, and neither
        # is a mix or a block the game lacks: each refused before a read.
        # An anvil and a door are no blocks water enters, but their sides
        # are not whole, so water passes them.
        for seal, error in (("minecraft:oak_slab[type=bottom]", NBTError),
                            ("minecraft:oak_leaves", NBTError),
                            ("minecraft:glass_pane", NBTError),
                            ("minecraft:anvil", NBTError),
                            ("minecraft:oak_door[half=lower]", NBTError),
                            ("minecraft:quartz_wall", IllegalBlock),
                            (Mix([("minecraft:stone", 1)]), TypeError)):
            with self.subTest(seal=seal):
                with self.assertRaises(error):
                    self.b._contain_plan(self.bank, what="the cut", seal=seal)
        # Whole to water and nothing it enters leaves no state holding a
        # fluid, which is why the seal check asks nothing more of one:
        # every state that does is a fluid or waterlogged, and is entered.
        import itertools

        from heaton_voxel.craft.block_facts import FULL_FACES, facts, holds_fluid
        from heaton_voxel.craft.block_registry import BLOCK_IDS, FLUIDS, properties_of

        wet_ones = []
        for name in sorted(BLOCK_IDS):
            props = properties_of(name)
            if name not in FLUIDS and "waterlogged" not in props:
                continue
            keys = sorted(props)
            for values in itertools.product(*(props[k] for k in keys)):
                state = dict(zip(keys, values))
                info = facts(name, state)
                if (info.collision == FULL_FACES and not info.enters
                        and holds_fluid(name, state)):
                    wet_ones.append((name, state))
        self.assertEqual(wet_ones, [])

    def test_a_seal_that_would_not_stay_a_wall_is_refused(self):
        """Whole on every face, and nothing water enters, but no wall for
        long: sand, gravel and concrete powder fall, standing where the
        water stood; ice and frosted ice melt back into water; a sponge
        soaks the pond up. Each is refused, naming why, before the plan is
        read -- here a plan nothing can read. Packed ice, which never
        melts, and a wet sponge, which soaks up nothing, are seals."""
        for seal, why in (("minecraft:sand", "falls"),
                          ("minecraft:gravel", "falls"),
                          ("minecraft:red_concrete_powder", "falls"),
                          ("minecraft:ice", "melts"),
                          ("minecraft:frosted_ice", "melts"),
                          ("minecraft:sponge", "soaks up")):
            with self.subTest(seal=seal):
                with self.assertRaises(NBTError) as caught:
                    self.b._contain_plan([("paste",)], what="the cut",
                                         seal=seal)
                message = str(caught.exception)
                self.assertIn("would not stay a wall", message)
                self.assertIn(why, message)
        for seal in ("minecraft:packed_ice", "minecraft:wet_sponge"):
            with self.subTest(seal=seal):
                self.assertEqual(
                    self.b._contain_plan(self.bank, what="the cut", seal=seal),
                    [("set", (6, y, z), seal) for _, y, z in self.opened])

    def test_a_seal_never_buries_what_stands_in_the_water(self):
        """A seal replaces water alone. A waterlogged chest whose water
        gets out through its sides is somebody's chest: the plan is
        refused, naming it, rather than walled off over it. Seagrass and
        kelp are water at every state, and are sealed over like it."""
        chest = "minecraft:chest[facing=east,waterlogged=true]"
        self.save.set_block(6, 62, 4, chest)
        with self.assertRaises(Refused) as caught:
            self.b._contain_plan(self.bank, what="the cut",
                                 seal="minecraft:cobblestone")
        self.assertEqual(caught.exception.faults, [
            f"(6, 62, 4) holds {BlockState.parse(chest)}, which the seal "
            f"would write over"])
        self.assertIn("A seal replaces the water alone", str(caught.exception))
        self.assertEqual(self.b.blocks_set, 0)
        # Without a seal it is the leak it always was.
        with self.assertRaises(Refused) as caught:
            self.b._contain_plan(self.bank, what="the cut")
        self.assertIn((6, 62, 4), [leak.source for leak in caught.exception.faults])

        # A refusal never offers a seal where a seal would be refused: the
        # chest's water alone getting out, it says what holds it instead.
        with self.assertRaises(Refused) as caught:
            self.b._contain_plan([("set", (7, 62, 4), "minecraft:air")],
                                 what="the cut")
        leak, = caught.exception.faults
        self.assertEqual((leak.cell, leak.source), ((7, 62, 4), (6, 62, 4)))
        message = str(caught.exception)
        self.assertNotIn("seal=", message)
        self.assertIn(f"The water at (6, 62, 4) is held by "
                      f"{BlockState.parse(chest)}", message)
        self.assertIn("a seal replaces water alone", message)
        # Beside water a seal can take, it offers both.
        with self.assertRaises(Refused) as caught:
            self.b._contain_plan(self.bank, what="the cut")
        message = str(caught.exception)
        self.assertIn("Pass seal='minecraft:cobblestone'", message)
        self.assertIn("(6, 62, 4) is held by", message)

        self.save.set_block(6, 62, 4, self.WATER)
        self.save.set_block(6, 61, 3, "minecraft:seagrass")
        self.save.set_block(6, 61, 5, "minecraft:kelp_plant")
        self.save.set_block(6, 62, 5, "minecraft:kelp")
        seals = self.b._contain_plan(self.bank, what="the cut",
                                     seal="minecraft:cobblestone")
        self.assertEqual(seals, [("set", (6, y, z), "minecraft:cobblestone")
                                 for _, y, z in self.opened])

    def test_spill_is_where_the_plan_lets_water_go(self):
        """``spill=`` reaches ``contained``: a cell cut out of the bank
        and named as spill takes the water, which is held there by the
        stone round it, and the plan passes; the same cut without it is a
        leak. A cell past the spill is the leak, as it is without it."""
        cut = [("set", (7, 62, 4), "minecraft:air")]
        with self.assertRaises(Refused) as caught:
            self.b._contain_plan(cut, what="the spout")
        self.assertEqual([leak.cell for leak in caught.exception.faults],
                         [(7, 62, 4)])
        self.assertEqual(self.b._contain_plan(cut, what="the spout",
                                              spill={(7, 62, 4)}), [])
        further = cut + [("set", (8, 62, 4), "minecraft:air")]
        with self.assertRaises(Refused) as caught:
            self.b._contain_plan(further, what="the spout",
                                 spill={(7, 62, 4)})
        self.assertEqual([leak.cell for leak in caught.exception.faults],
                         [(8, 62, 4)])
        # The water past the spill stands in a cell the plan cuts, so a
        # seal, which goes where the water is, would go over the plan.
        with self.assertRaises(Refused) as caught:
            self.b._contain_plan(further, what="the spout",
                                 spill={(7, 62, 4)},
                                 seal="minecraft:cobblestone")
        self.assertIn("the plan's own", str(caught.exception))

    def test_a_seal_that_opens_a_way_of_its_own_is_refused(self):
        """Falling water over a pond turns to its sides once the pond cell
        under it is sealed: the plan with its seals is checked again, and
        what the seal let out refuses it."""
        self.save.fill((2, 62, 2), (6, 63, 6), "minecraft:stone")
        self.save.fill((6, 62, 4), (6, 63, 4), "minecraft:water[level=8]")
        self.save.set_block(6, 64, 4, self.WATER)
        cut = [("fill", (7, 61, 4), (7, 62, 4), "minecraft:air")]
        with self.assertRaises(Refused) as caught:
            self.b._contain_plan(cut, what="the cut")
        self.assertEqual([leak.cell for leak in caught.exception.faults],
                         [(7, 61, 4)])
        with self.assertRaises(Refused) as caught:
            self.b._contain_plan(cut, what="the cut",
                                 seal="minecraft:cobblestone")
        leak, = caught.exception.faults
        self.assertEqual((leak.cell, leak.source, leak.kind),
                         ((7, 62, 4), (6, 62, 4), "spreads"))
        self.assertIn("walled off with minecraft:cobblestone, the water would "
                      "still get out", str(caught.exception).lower())
        self.assertEqual(self.b.blocks_set, 0)

    def test_water_the_plan_writes_is_never_sealed(self):
        """A stair placed into the pond is planned waterlogged, as
        ``place_block`` writes it, and its water gets out through its open
        upper half into the cut beside it: the leak's water is the plan's
        own, so a seal, which goes where the water is, would write over
        the plan, and the plan is refused with or without one."""
        ops = [("place", (6, 62, 4), "minecraft:oak_stairs[facing=north]"),
               ("set", (7, 62, 4), "minecraft:air")]
        for seal in (None, "minecraft:cobblestone"):
            with self.subTest(seal=seal):
                with self.assertRaises(Refused) as caught:
                    self.b._contain_plan(ops, what="the step", seal=seal)
                leak, = caught.exception.faults
                self.assertEqual((leak.cell, leak.source, leak.kind),
                                 ((7, 62, 4), (6, 62, 4), "spreads"))
                self.assertIn("the plan's own", str(caught.exception))
        self.assertEqual(self.b.blocks_set, 0)

    def test_lava_and_a_chunk_not_there_are_never_sealed(self):
        """Water set beside lava, and water set at the world's edge, beside
        a chunk never generated: no seal mends either, so the plan is
        refused with or without one, naming them. So with the world's own
        water: lava the plan sets against the pond, and a source the plan
        sets beside a spent flow at the world's edge, which it raises
        until it runs on into the chunk that is not there -- where a seal
        would otherwise go in the world's water, not the plan's."""
        self.save.set_block(8, 62, 4, "minecraft:lava")
        self.save.fill((28, 61, 2), (30, 63, 6), self.WATER)
        for ops, kind in (([("set", (7, 62, 4), self.WATER)], "lava"),
                          ([("set", (31, 62, 4), self.WATER)], "ungenerated")):
            for seal in (None, "minecraft:cobblestone"):
                with self.subTest(kind=kind, seal=seal):
                    with self.assertRaises(Refused) as caught:
                        self.b._contain_plan(ops, what="the spout", seal=seal)
                    faults = caught.exception.faults
                    self.assertEqual([leak.kind for leak in faults], [kind])
                    message = str(caught.exception).lower()
                    self.assertIn("no seal mends", message)
                    self.assertNotIn("pass seal=", message)
        self.assertEqual(self.b.blocks_set, 0)

        self.save.set_block(8, 62, 4, "minecraft:stone")
        self.save.fill((28, 61, 2), (30, 63, 6), "minecraft:stone")
        self.save.set_block(31, 62, 4, "minecraft:water[level=7]")
        for ops, kind, cell, source in (
                ([("set", (7, 62, 4), "minecraft:lava")], "lava",
                 (7, 62, 4), (6, 62, 4)),
                ([("set", (30, 62, 4), self.WATER)], "ungenerated",
                 (32, 62, 4), (31, 62, 4))):
            for seal in (None, "minecraft:cobblestone"):
                with self.subTest(kind=kind, seal=seal, water="the world's"):
                    with self.assertRaises(Refused) as caught:
                        self.b._contain_plan(ops, what="the spout", seal=seal)
                    leak, = caught.exception.faults
                    self.assertEqual((leak.kind, leak.cell, leak.source),
                                     (kind, cell, source))
                    self.assertNotIn(leak.source, {op[1] for op in ops})
                    self.assertIn("no seal mends",
                                  str(caught.exception).lower())
        self.assertEqual(self.b.blocks_set, 0)


class JournalTests(BuilderBase):
    """What a Builder wrote is journaled: once each, under the stage that
    wrote it, and never by a refusal."""

    def test_stages_tally_what_each_wrote(self):
        with self.b.stage("pad") as same:
            self.assertIs(same, self.b, "the block yields the builder")
            self.b.level_pad(0, 0, 10, 8, 64)
        with self.b.stage("roof"):
            self.b.gable_roof(1, 1, 9, 7, 68, "x")
            with self.b.stage("ridge"):
                self.b.set(5, 74, 4, "minecraft:stone_bricks")
            self.assertEqual(self.b.journal.stage, "roof",
                             "leaving a stage restores the one around it")
            self.b.set(5, 74, 5, "minecraft:stone_bricks")
        self.b.set(12, 64, 12, "minecraft:stone")
        self.assertIsNone(self.b.journal.stage)
        tally = self.b.journal.tally()
        self.assertEqual(list(tally), ["pad", "roof", "ridge", None])
        self.assertTrue(all(count > 0 for count in tally.values()), tally)
        self.assertEqual(tally["ridge"], 1)
        self.assertEqual(tally[None], 1)
        self.assertEqual(sum(tally.values()), self.b.blocks_set)
        self.assertEqual(self.b.journal.total, self.b.blocks_set)
        for name in ("", None, 7):
            with self.assertRaises(NBTError, msg=repr(name)):
                self.b.stage(name)

    def test_written_is_every_cell_once_in_a_total_order(self):
        """The order is the cells', not the writes': the bed's head lands
        in chunk (1, 0) before the fill reaches the next section up of
        chunk (0, 0), and is listed after it all the same."""
        self.b.set(3, 64, 3, "minecraft:stone")
        self.b.set(3, 64, 3, "minecraft:stone")
        self.b.place(15, 64, 5, "minecraft:red_bed[facing=east]")
        self.b.fill((0, 80, 0), (1, 80, 1), "minecraft:stone")
        self.assertEqual(list(self.b.journal.written()), [
            (3, 64, 3), (15, 64, 5),                  # chunk (0, 0), y=64
            (0, 80, 0), (1, 80, 0), (0, 80, 1), (1, 80, 1),   # its next section
            (16, 64, 5),                              # chunk (1, 0)
        ])
        self.assertEqual(self.b.blocks_set, 8, "a write is counted each time")
        self.assertEqual(self.b.journal.chunks(), ((0, 0), (1, 0)))
        self.assertEqual(self.b.journal.chunks(), ((0, 0), (1, 0)))
        with self.assertRaises(AttributeError):
            self.b.blocks_set = 0

    def test_a_refusal_journals_nothing(self):
        """Nor does one in a chunk already written in, whose span the write
        check remembers: a height past the world is refused there too."""
        from heaton_voxel.craft.build import StraightStair

        self.b.set(1, 64, 1, "minecraft:stone")
        before = (list(self.b.journal.written()), self.b.journal.tally())
        with self.assertRaises(Refused):
            self.b.fill((30, 64, 0), (40, 64, 0), "minecraft:stone")
        with self.assertRaises(Refused):
            self.b.set(1, 400, 1, "minecraft:stone")
        with self.assertRaises(Refused):
            # Nothing above the ground to climb to: a flight to nowhere.
            self.b.flight(StraightStair((5, 64, 10), "north", 4, width=2))
        self.assertEqual((list(self.b.journal.written()), self.b.journal.tally()),
                         before)
        self.assertEqual(self.b.blocks_set, 1)

        fresh = Builder(self.save)
        with self.assertRaises(Refused):
            fresh.set(1, 400, 1, "minecraft:stone")
        self.assertFalse(fresh.journal)
        self.assertEqual(list(fresh.journal.written()), [])
        self.assertEqual(fresh.journal.tally(), {})
        self.assertEqual(fresh.blocks_set, 0)

    def test_a_stage_ends_when_its_block_raises(self):
        with self.b.stage("walls"):
            with self.assertRaises(Refused):
                with self.b.stage("roof"):
                    self.b.set(1, 64, 1, "minecraft:stone")
                    self.b.fill((30, 64, 0), (40, 64, 0), "minecraft:stone")
            self.assertEqual(self.b.journal.stage, "walls",
                             "a refusal inside a stage leaves the stage")
            self.b.set(2, 64, 2, "minecraft:stone")
        self.assertIsNone(self.b.journal.stage)
        self.assertEqual(self.b.journal.tally(), {"roof": 1, "walls": 1})

    def test_a_fill_is_journaled_cell_by_cell_across_sections_and_chunks(self):
        self.b.fill((0, 64, 0), (15, 79, 15), "minecraft:stone")   # a whole section
        # Corners in either order, across four chunks and two sections.
        self.b.fill((2, 66, 1), (-3, 62, -2), "minecraft:glass")
        cells = ({(x, y, z) for x in range(16) for y in range(64, 80) for z in range(16)}
                 | {(x, y, z) for x in range(-3, 3) for y in range(62, 67)
                    for z in range(-2, 2)})
        expected = sorted(cells, key=lambda c: (c[0] >> 4, c[2] >> 4, c[1], c[2], c[0]))
        written = list(self.b.journal.written())
        self.assertEqual(len(written), len(expected))
        # Not assertEqual on the lists: a diff of four thousand tuples is
        # minutes nobody reads.
        mismatch = next(((i, got, want) for i, (got, want)
                         in enumerate(zip(written, expected)) if got != want), None)
        self.assertIsNone(mismatch, "the first cell out of place: (index, written, expected)")
        self.assertEqual(self.b.journal._chunk_boxes(), {
            (-1, -1): (-3, 62, -2, -1, 66, -1),
            (-1, 0): (-3, 62, 0, -1, 66, 1),
            (0, -1): (0, 62, -2, 2, 66, -1),
            (0, 0): (0, 62, 0, 15, 79, 15),
        })
        self.assertEqual(self.b.journal.chunks(), ((-1, -1), (-1, 0), (0, -1), (0, 0)))


class FinishScopeTests(BuilderBase):
    """finish() settles what was written, chunk by chunk, and a margin
    around it -- not a rectangle of whole chunks around everything."""

    RAILING = ("minecraft:spruce_fence[east=false,north=true,south=true,"
               "waterlogged=false,west=false]")

    poi_path = InvalidatePoiTests.poi_path
    write_poi = InvalidatePoiTests.write_poi

    def test_finish_leaves_far_work_in_the_same_chunk_alone(self):
        # A decorative railing at x=2, posts spaced two apart, in the chunk
        # the build writes in, ten blocks away from anything it writes.
        for z in (2, 4, 6):
            self.save.set_block(2, 64, z, self.RAILING)
        self.b.set(12, 64, 12, "minecraft:oak_fence")
        self.b.set(12, 64, 13, "minecraft:oak_fence")
        report = self.b.finish()
        self.assertEqual(self.save.get_block(2, 64, 4).properties["north"], "true",
                         "a railing far from every write keeps its state")
        self.assertEqual(report.connections.blocks_scanned, 2)
        self.assertEqual(self.save.get_block(12, 64, 12).properties["south"], "true")

    def test_finish_settles_a_neighbor_across_a_chunk_edge(self):
        self.save.set_block(16, 64, 5, "minecraft:oak_fence")      # chunk (1, 0)
        self.b.set(15, 64, 5, "minecraft:oak_fence")               # chunk (0, 0)
        report = self.b.finish()
        self.assertEqual(self.save.get_block(15, 64, 5).properties["east"], "true")
        self.assertEqual(self.save.get_block(16, 64, 5).properties["west"], "true",
                         "the fence across the border joins, as a block "
                         "update would join it")
        self.assertTrue(report.connections.complete)

    def test_finish_writes_nothing_into_a_chunk_still_generating(self):
        # Chunk (0, 0) is still generating, between the two the build
        # writes in; its fences touch both writes. Chunk (0, -1) is still
        # generating too, with nothing to connect beside the stone set next
        # to it.
        for x in (0, 15):
            self.save.set_block(x, 64, 5, "minecraft:oak_fence")
        dim = self.save.dimension(OVERWORLD)
        for chunk in ((0, 0), (0, -1)):
            dim.chunk(*chunk).data["Status"] = TagString("minecraft:features")
            dim.release(*chunk)
        self.b.set(-1, 64, 5, "minecraft:oak_fence")               # chunk (-1, 0)
        self.b.set(16, 64, 5, "minecraft:oak_fence")               # chunk (1, 0)
        self.b.set(-1, 64, -8, "minecraft:stone")                  # chunk (-1, -1)
        report = self.b.finish()
        for x in (0, 15):
            self.assertEqual(self.save.get_block(x, 64, 5),
                             BlockState("minecraft:oak_fence"),
                             f"the unfinished chunk's fence at x={x} was rewritten")
        self.assertEqual(self.save.get_block(-1, 64, 5).properties["east"], "true",
                         "the written fence still reads its neighbor there")
        self.assertEqual(self.save.get_block(16, 64, 5).properties["west"], "true")
        self.assertEqual(report.connections.blocks_scanned, 2)
        # What it left is said, so the border does not read as settled; the
        # chunk with nothing there to settle is not listed.
        (left,) = report.connections.unreadable_chunks
        self.assertEqual(left[0], (0, 0))
        self.assertIn("minecraft:features", left[1])
        self.assertIn("2 connecting blocks", left[1])
        self.assertFalse(report.connections.complete)

    def test_finish_settles_up_to_the_edge_of_the_world_and_no_further(self):
        # Chunk (1, 0) is at the world's east edge: past x=31 nothing was
        # ever generated, so the margin has nothing there to settle or to
        # report, while the fence across the south border joins.
        self.save.set_block(31, 64, 16, "minecraft:oak_fence")     # chunk (1, 1)
        self.b.set(31, 64, 15, "minecraft:oak_fence")              # chunk (1, 0)
        report = self.b.finish()
        self.assertEqual(self.save.get_block(31, 64, 16).properties["north"], "true")
        self.assertEqual(report.connections.missing_chunks, [])
        self.assertTrue(report.connections.complete)

    def test_finish_relights_the_chunks_written_and_the_ring_around_each(self):
        # Five by five chunks, written in two diagonal ones: each brings the
        # nine chunks around it, (0, 0) is in both, and the eight in neither
        # -- the other two corners' quarters -- keep their light.
        directory = tempfile.mkdtemp(prefix="build-tests-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        make_save(directory, [(x, z) for x in range(-2, 3) for z in range(-2, 3)])
        save = MinecraftSave.load(directory)
        dim = save.dimension(OVERWORLD)
        for x in range(-2, 3):
            for z in range(-2, 3):
                dim.chunk(x, z).data["isLightOn"] = nbt.TagByte(1)
                dim.release(x, z)
        builder = Builder(save, seed=7)
        builder.set(-8, 64, -8, "minecraft:stone")                  # chunk (-1, -1)
        builder.set(24, 64, 24, "minecraft:stone")                  # chunk (1, 1)
        report = builder.finish()
        self.assertEqual(report.lighting.chunks_reset, 17)
        self.assertEqual(report.lighting.chunks_skipped, 0)
        lit = {(x, z) for x in range(-2, 3) for z in range(-2, 3)
               if int(dim.chunk(x, z).data["isLightOn"])}
        self.assertEqual(lit, {(x, z) for x in (1, 2) for z in (-1, -2)}
                         | {(x, z) for x in (-1, -2) for z in (1, 2)})

    def test_finish_lists_the_faults_in_cell_order_across_chunks(self):
        # A torch with nothing under it in each of two chunks: chunk (0, 0)
        # is settled first, but the torch in chunk (0, 1) has the lower x,
        # and stands two below the write beside it.
        self.save.set_block(10, 63, 3, "minecraft:air")
        self.save.set_block(2, 61, 20, "minecraft:air")
        self.save.set_block(2, 62, 20, "minecraft:torch")
        self.b.set(10, 64, 3, "minecraft:torch")                   # chunk (0, 0)
        self.b.set(2, 64, 20, "minecraft:stone_bricks")            # chunk (0, 1)
        report = self.b.finish()
        self.assertEqual([fault.cell for fault in report.unsupported],
                         [(2, 62, 20), (10, 64, 3)])

    def test_finish_reports_a_neighbor_it_cannot_read(self):
        # Chunk (1, 0) predates 1.18: there, and unreadable. The fence the
        # build sets beside it is settled, the margin's reach into it is
        # reported rather than taken for empty ground, and the audit, which
        # cannot read it either, does not stop the finish.
        dim = self.save.dimension(OVERWORLD)
        dim.chunk(1, 0).data["DataVersion"] = TagInt(2586)
        dim.release(1, 0)
        self.b.set(15, 64, 5, "minecraft:oak_fence")
        self.b.set(15, 64, 6, "minecraft:oak_fence")
        report = self.b.finish()
        self.assertEqual([chunk for chunk, _ in report.connections.unreadable_chunks],
                         [(1, 0)])
        self.assertIn((1, 0), [chunk for chunk, _ in report.lighting.unreadable_chunks])
        self.assertEqual(self.save.get_block(15, 64, 5).properties["south"], "true")
        self.assertEqual(report.unsupported, [])

    def test_finish_keeps_poi_of_chunks_it_did_not_touch(self):
        self.save.save()
        self.write_poi([(0, 0), (0, 1), (1, 1)])
        self.save = MinecraftSave.load(self.dir)
        self.b = Builder(self.save, seed=7)
        self.b.set(3, 64, 3, "minecraft:stone")                    # chunk (0, 0)
        self.b.set(20, 64, 20, "minecraft:stone")                  # chunk (1, 1)
        report = self.b.finish()
        self.assertEqual(report.poi_records, 2)
        region = self.save.dimension(OVERWORLD).poi_region(0, 0)
        self.assertEqual(sorted(region.chunks), [(0, 1)],
                         "a chunk nothing was written in keeps its records")
        self.assertEqual(report.heightmaps.chunks_rebuilt, 2)

    def test_finish_audits_a_door_two_below_a_write(self):
        self.save.set_block(8, 64, 8, "minecraft:iron_door[facing=south,half=lower,"
                            "hinge=left,open=false,powered=false]")
        self.save.set_block(8, 65, 8, "minecraft:iron_door[facing=south,half=upper,"
                            "hinge=left,open=false,powered=false]")
        self.save.set_block(8, 66, 8, "minecraft:stone")
        self.save.set_block(8, 66, 9, "minecraft:stone_button[face=wall,"
                            "facing=south,powered=false]")
        self.save.set_block(8, 64, 7, "minecraft:stone_button[face=floor,"
                            "facing=north,powered=false]")
        # Taking the button off the south side, two levels over the door's
        # lower half, leaves the door opening from the north only.
        self.b.set(8, 66, 9, "minecraft:air")
        report = self.b.finish()
        self.assertEqual([(f.kind, f.cell) for f in report.unsupported],
                         [("one-way door", (8, 64, 8))])

    def test_audit_and_connect_reach_cover_what_the_checks_read(self):
        from heaton_voxel.connect import _derived_state
        from heaton_voxel.craft.build import _AUDIT_REACH, _CONNECT_REACH
        from heaton_voxel.craft.survey import _check

        def reach(run, cell, around):
            """The farthest from ``cell`` that ``run`` reads, along any axis."""
            far = 0

            def block_at(x, y, z):
                nonlocal far
                far = max(far, abs(x - cell[0]), abs(y - cell[1]), abs(z - cell[2]))
                found = around.get((x - cell[0], y - cell[1], z - cell[2]))
                return BlockState.parse(found or "minecraft:stone")

            run(block_at)
            return far

        door = "minecraft:iron_door[facing=south,hinge=left,open=false,powered=false,half="
        audited = {
            "a closed iron door": (door + "lower]", {(0, 1, 0): door + "upper]"}),
            "a bed foot": ("minecraft:red_bed[facing=east,occupied=false,part=foot]", {}),
            "sugar cane": ("minecraft:sugar_cane[age=0]", {(0, -1, 0): "minecraft:sand"}),
            "a wall torch": ("minecraft:wall_torch[facing=north]", {}),
            "a pressure plate": ("minecraft:stone_pressure_plate[powered=false]",
                                 {(0, -1, 0): "minecraft:air"}),
        }
        cell = (8, 64, 8)
        reads = {}
        for what, (state, around) in audited.items():
            state = BlockState.parse(state)
            reads[what] = reach(lambda block_at: _check(block_at, cell, state),
                                cell, {(0, 0, 0): str(state), **around})
            self.assertLessEqual(reads[what], _AUDIT_REACH, what)
        self.assertEqual(reads["a closed iron door"], _AUDIT_REACH,
                         "the door reads the button over its upper half")

        connected = {
            "a wall": ("minecraft:cobblestone_wall", {(0, 1, 0): "minecraft:cobblestone_wall"}),
            "a fence": ("minecraft:oak_fence", {}),
            "a pane": ("minecraft:glass_pane", {}),
            "a gate": ("minecraft:oak_fence_gate[facing=north]",
                       {(1, 0, 0): "minecraft:cobblestone_wall"}),
            "a stair": ("minecraft:oak_stairs[facing=north,half=bottom]",
                        {(0, 0, -1): "minecraft:oak_stairs[facing=east,half=bottom]",
                         (0, 0, 1): "minecraft:oak_stairs[facing=west,half=bottom]"}),
        }
        for what, (state, around) in connected.items():
            state = BlockState.parse(state)
            far = reach(lambda block_at: _derived_state(state, block_at, *cell),
                        cell, {(0, 0, 0): str(state), **around})
            self.assertEqual(far, _CONNECT_REACH, what)

    def test_merge_refuses_a_field_it_does_not_know(self):
        from heaton_voxel.connect import ConnectResult
        from heaton_voxel.craft.build import _merge

        into, part = ConnectResult(), ConnectResult()
        into.fences_updated, part.fences_updated = 2, 3
        part.missing_chunks.append((4, 4))
        self.assertIs(_merge(into, part), into)
        self.assertEqual((into.fences_updated, into.missing_chunks), (5, [(4, 4)]))

        class Labeled:
            __slots__ = ("count", "label")

            def __init__(self):
                self.count, self.label = 1, "chunk"

        with self.assertRaises(NBTError) as caught:
            _merge(Labeled(), Labeled())
        self.assertIn("Labeled.label", str(caught.exception))
        with self.assertRaises(NBTError):
            _merge(ConnectResult(), Labeled())
