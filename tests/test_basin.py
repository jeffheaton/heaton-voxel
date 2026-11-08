"""Tests for Builder.basin: a contained body of water, shaped by distance to
its shore.

A basin's floor is where the outline's distance field puts it -- the lake
around Jeff's island was cut ``0.75 x distance-to-shore`` deep, the
deepest bed with no cliff in it -- and before anything is written it is
held to what the game would do with its water: a floor over a hole is
lined or refused, a gap in the bank is rimmed or refused, water let in
from a river is sealed or refused, a flow the basin takes the feed of is
refused, and a lamp in it never melts the ice roofing a room nearby.
"""

from __future__ import annotations

import importlib.util
import os
import random
import shutil
import tempfile
import unittest

from heaton_voxel.blocks import BlockState
from heaton_voxel.craft import (BasinResult, Builder, Mix, NO_NEW_SNOW_OR_ICE,
                                SHORE_SLOPE, contained, geometry,
                                simulate_block_light, water_body)
from heaton_voxel.craft.block_facts import holds_water
from heaton_voxel.craft.build._common import _falls_through, _takes
from heaton_voxel.craft.build.water import _depths
from heaton_voxel.craft.errors import NoSurface, Refused
from heaton_voxel.craft.field import distance_to_edge
from heaton_voxel.craft.survey import Leak
from heaton_voxel.nbt import NBTError
from heaton_voxel.world import MinecraftSave
from tests.test_world import make_save

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHUNKS = [(x, z) for x in range(-1, 2) for z in range(-1, 2)]
STONE = "minecraft:stone"
SAND = "minecraft:sand"
WATER = "minecraft:water[level=0]"
COBBLE = "minecraft:cobblestone"
BRICKS = "minecraft:stone_bricks"
LANTERN = "minecraft:sea_lantern"


class BasinBase(unittest.TestCase):
    """A flat world of stone to y=63, three chunks square, and helpers to
    make another like it."""

    def world(self, shape=None, *, chunks=CHUNKS, top=63):
        directory = tempfile.mkdtemp(prefix="basin-tests-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        make_save(directory, chunks)
        save = MinecraftSave.load(directory)
        xs = [c[0] for c in chunks]
        zs = [c[1] for c in chunks]
        low = (min(xs) * 16, 40, min(zs) * 16)
        high = (max(xs) * 16 + 15, top, max(zs) * 16 + 15)
        save.fill(low, high, STONE)
        if shape is not None:
            shape(save)
        save.rebuild_heightmaps((low[0], 0, low[2]), (high[0], 0, high[2]))
        return save

    def name(self, save, x, y, z):
        return save.get_block(x, y, z).name

    def wet(self, save, x, y, z):
        block = save.get_block(x, y, z)
        return holds_water(block.name, block.properties)

    def snapshot(self, save, low, high):
        return [str(save.get_block(x, y, z))
                for x in range(low[0], high[0] + 1)
                for y in range(low[1], high[1] + 1)
                for z in range(low[2], high[2] + 1)]


class BasinShapeTests(BasinBase):

    def test_a_basin_is_deepest_farthest_from_shore(self):
        """A disc of radius 5 is cut ``max(1, round(0.75 * d))`` deep at
        each column, ``d`` its distance to the nearest column outside it:
        four in the middle, one at the edge, no two side neighbors' floors
        more than a block apart. Sand on the floor, sources from it to the
        surface, and the body that stands there afterwards is exactly the
        water planned, all of it still. A cave under the middle is roofed:
        each floor over it gets one course of the lining, and nothing
        else is lined."""
        def cave(save):
            save.fill((6, 56, 6), (10, 59, 10), "minecraft:cave_air")
        save = self.world(cave)
        b = Builder(save)
        disc = geometry.disc(5, center=(8, 8), rounding="inside")
        r = b.basin(disc, 63, floor=SAND, lining=STONE)
        self.assertIsInstance(r, BasinResult)
        distance = distance_to_edge(disc)
        want = {c: 63 - max(1, round(0.75 * distance[c])) for c in disc}
        self.assertEqual(r.floor, want)
        self.assertEqual(list(r.floor), sorted(disc, key=lambda c: (c[1], c[0])))
        self.assertEqual(min(want.values()), 59)
        self.assertEqual(max(want.values()), 62)
        for (x, z), y in want.items():
            for side in ((x + 1, z), (x, z + 1)):
                if side in want:
                    self.assertLessEqual(abs(y - want[side]), 1, (x, z))
            self.assertEqual(self.name(save, x, y, z), "minecraft:sand")
            for wy in range(y + 1, 64):
                self.assertEqual(str(save.get_block(x, wy, z)),
                                 "minecraft:water[level=0]")
            self.assertEqual(self.name(save, x, 64, z), "minecraft:air")
        water = {(x, wy, z) for (x, z), y in want.items()
                 for wy in range(y + 1, 64)}
        self.assertEqual(r.water, len(water))
        self.assertEqual((r.cut, r.raised), (len(disc), 0))
        body = water_body(save, (8, 63, 8))
        self.assertTrue(body.still)
        self.assertEqual(body.cells, frozenset(water))
        # The lids under the crust: the floors over the cave, and no more.
        lined = sorted((x, y - 1, z) for (x, z), y in want.items()
                       if 6 <= x <= 10 and 6 <= z <= 10 and 57 <= y - 1 <= 59)
        self.assertEqual(r.lined, tuple(lined))
        self.assertTrue(lined)
        for cell in lined:
            self.assertEqual(self.name(save, *cell), "minecraft:stone")
        self.assertEqual(self.name(save, 8, 57, 8), "minecraft:cave_air")
        self.assertEqual(SHORE_SLOPE, 0.75)

    def test_depth_never_exceeds_the_slope_times_the_distance(self):
        """The default profile is the nearest whole depth to ``slope * d``,
        so never more than half a block past it, and never less than one:
        over 200 random outlines and every slope up to 1, each column is
        ``max(1, round(slope * d))`` -- capped by ``max_depth`` -- and no
        two side neighbors differ by more than a block, which is why a
        slope past 1 (or not over 0) is refused. A basin laid at a slope of
        1 writes those floors."""
        rng = random.Random(41)
        for _ in range(200):
            cells = set()
            for _ in range(rng.randint(1, 4)):
                cx, cz, r = rng.randint(0, 20), rng.randint(0, 20), rng.randint(1, 7)
                cells.update(geometry.disc(r, center=(cx, cz)))
            columns = sorted(cells, key=lambda c: (c[1], c[0]))
            distance = distance_to_edge(columns)
            for slope in (0.25, 0.5, SHORE_SLOPE, 0.9, 1):
                depth = _depths(columns, None, slope, None)
                for c in columns:
                    self.assertEqual(depth[c], max(1, round(slope * distance[c])))
                    self.assertLessEqual(depth[c], max(1, slope * distance[c] + 0.5))
                for x, z in columns:
                    for side in ((x + 1, z), (x, z + 1)):
                        if side in depth:
                            self.assertLessEqual(abs(depth[(x, z)] - depth[side]), 1)
                capped = _depths(columns, None, slope, 2)
                self.assertEqual(capped, {c: min(2, d) for c, d in depth.items()})
        save = self.world()
        b = Builder(save)
        blob = sorted(set(geometry.disc(6, center=(10, 10)))
                      | set(geometry.disc(3, center=(17, 10))))
        r = b.basin(blob, 63, floor=SAND, slope=1)
        distance = distance_to_edge(blob)
        self.assertEqual(r.floor, {c: 63 - max(1, round(distance[c]))
                                   for c in sorted(blob, key=lambda c: (c[1], c[0]))})
        for slope in (0, -0.5, 1.05, True, "0.75", None):
            with self.subTest(slope=slope):
                with self.assertRaises(NBTError) as caught:
                    Builder(save).basin(blob, 63, floor=SAND, slope=slope)
                self.assertIn("cliff-free", str(caught.exception))


class BasinGroundTests(BasinBase):

    def test_a_basin_over_a_cave_is_refused_without_a_lining(self):
        """A floor that would lie on a cave, on ice roofing a room, or on
        a flooded cave's water is a lid over a hole -- light can melt the
        ice, and sand falls through air and water -- so without
        ``lining=`` the basin is refused, one refusal naming every such
        column and the opt-in, and nothing is written. With one, one course
        goes under each floor and the hole below it is left as it was."""
        def holes(save):
            save.fill((4, 58, 4), (6, 61, 6), "minecraft:cave_air")  # a cave
            save.fill((10, 58, 4), (12, 60, 6), "minecraft:air")      # a room
            save.fill((10, 61, 4), (12, 61, 6), "minecraft:ice")     # its lid
            save.fill((4, 59, 10), (6, 61, 12), WATER)               # flooded
        outline = (3, 3, 13, 13)
        save = self.world(holes)
        before = self.snapshot(save, (2, 55, 2), (14, 66, 14))
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.basin(outline, 63, floor=SAND, depth=1)
        message = str(caught.exception)
        self.assertIn("lining=", message)
        self.assertIn("a lid over a hole", message)
        faults = caught.exception.faults
        self.assertEqual(len(faults), 27)
        self.assertTrue(faults[0].startswith("(4, 4): the floor at y=62 would "
                                             "lie on minecraft:cave_air"))
        self.assertTrue(any(f.startswith("(10, 4): the floor at y=62 would lie "
                                         "on minecraft:ice") for f in faults))
        self.assertTrue(any(f.startswith("(4, 10): the floor at y=62 would lie "
                                         "on minecraft:water") for f in faults))
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(self.snapshot(save, (2, 55, 2), (14, 66, 14)), before)

        r = b.basin(outline, 63, floor=SAND, depth=1, lining=STONE)
        self.assertEqual(len(r.lined), 27)
        for x, y, z in r.lined:
            self.assertEqual(y, 61)
            self.assertEqual(self.name(save, x, y, z), "minecraft:stone")
        self.assertEqual(self.name(save, 5, 60, 5), "minecraft:cave_air")
        self.assertEqual(self.name(save, 11, 60, 5), "minecraft:air")
        self.assertEqual(self.name(save, 11, 62, 5), "minecraft:sand")

    def test_a_floor_on_what_gives_way_is_a_lid(self):
        """The game drops a falling block through what ``FallingBlock.isFree``
        answers yes for -- hanging roots or a light block under a cave's
        crust as much as its air -- and drops the lowest of a stack of sand
        first and the rest after it. So a floor on hanging roots, on a light
        block, or on natural sand or gravel standing on the cave, is a lid,
        refused without ``lining=`` and lined with it in the cell under the
        floor, the stack beneath left as it was -- a light block, which
        somebody set, only with ``overwrite=True``; a floor on kelp, which
        holds sand up, is laid as it is."""
        def crust(block, depth=1):
            def shape(save):
                save.fill((4, 56, 4), (6, 61 - depth, 6), "minecraft:cave_air")
                save.fill((4, 62 - depth, 4), (6, 61, 6), block)
            return shape
        for block, depth, words in (
                ("minecraft:hanging_roots", 1,
                 "would lie on minecraft:hanging_roots at y=61"),
                ("minecraft:light[level=15]", 1,
                 "would lie on minecraft:light[level=15] at y=61"),
                (SAND, 1, "would lie on minecraft:sand at y=61, which falls "
                          "through minecraft:cave_air at y=60"),
                ("minecraft:gravel", 3, "would lie on minecraft:gravel at "
                                        "y=61, which falls through "
                                        "minecraft:cave_air at y=58")):
            with self.subTest(block):
                save = self.world(crust(block, depth))
                before = self.snapshot(save, (3, 55, 3), (7, 65, 7))
                b = Builder(save)
                with self.assertRaises(Refused) as caught:
                    b.basin((4, 4, 6, 6), 63, floor=SAND, depth=1)
                self.assertEqual(len(caught.exception.faults), 9)
                self.assertEqual(caught.exception.faults[0],
                                 f"(4, 4): the floor at y=62 {words}, a lid "
                                 f"over a hole")
                self.assertEqual(b.blocks_set, 0)
                self.assertEqual(self.snapshot(save, (3, 55, 3), (7, 65, 7)),
                                 before)
                r = b.basin((4, 4, 6, 6), 63, floor=SAND, depth=1,
                            lining=STONE,
                            overwrite=block.startswith("minecraft:light"))
                self.assertEqual(r.lined, tuple((x, 61, z) for x in range(4, 7)
                                                for z in range(4, 7)))
                self.assertEqual(self.name(save, 5, 61, 5), STONE)
                self.assertEqual(self.name(save, 5, 62, 5), SAND)
                for y in range(62 - depth, 61):
                    self.assertEqual(str(save.get_block(5, y, 5)), block)
        # A sand bed on sandstone holds, however deep the sand.
        save = self.world(crust("minecraft:sandstone", 4))
        save.fill((4, 59, 4), (6, 61, 6), SAND)
        r = Builder(save).basin((4, 4, 6, 6), 63, floor=SAND, depth=1)
        self.assertEqual(r.lined, ())
        # Kelp hangs in its water and holds a falling block up.
        def kelp(save):
            save.fill((4, 59, 4), (6, 61, 6), "minecraft:kelp_plant")
        save = self.world(kelp)
        r = Builder(save).basin((4, 4, 6, 6), 63, floor=SAND, depth=1)
        self.assertEqual(r.lined, ())
        self.assertEqual(self.name(save, 5, 61, 5), "minecraft:kelp_plant")

    def test_a_raised_floor_over_an_ice_sheet_stands_on_the_bed(self):
        """An ice sheet over a pond's water is no ground to build on: a
        floor raised over the pond stands on its bed, the lining filling
        the water under the floor from the bed up, so no water is left
        sealed beneath it."""
        def pond(save):
            save.fill((4, 60, 4), (6, 62, 6), WATER)
            save.fill((4, 63, 4), (6, 63, 6), "minecraft:ice")
        save = self.world(pond)
        r = Builder(save).basin((4, 4, 6, 6), 63, floor=SAND, depth=1,
                                lining=STONE)
        self.assertEqual((r.cut, r.raised), (0, 9))
        self.assertEqual(r.lined, tuple((x, y, z) for x in range(4, 7)
                                        for y in (60, 61) for z in range(4, 7)))
        for y in (60, 61):
            self.assertEqual(self.name(save, 5, y, 5), STONE)
        self.assertEqual(self.name(save, 5, 62, 5), SAND)
        self.assertEqual(str(save.get_block(5, 63, 5)), WATER)

    def test_low_ground_is_built_up_with_the_lining(self):
        """A pit dug to 60 under a basin one deep at 63 has its floor at
        62: the lining builds the ground up from the pit's bottom to under
        the floor, and without one the basin is refused, naming how far
        the floor stands over the ground."""
        def pit(save):
            save.fill((4, 61, 4), (6, 63, 6), "minecraft:air")
        save = self.world(pit)
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.basin((4, 4, 6, 6), 63, floor=SAND, depth=1)
        self.assertIn("lining=", str(caught.exception))
        self.assertEqual(caught.exception.faults[0],
                         "(4, 4): the floor at y=62 stands 1 over the ground "
                         "at y=60")
        self.assertEqual(b.blocks_set, 0)
        r = b.basin((4, 4, 6, 6), 63, floor=SAND, depth=1, lining=BRICKS)
        self.assertEqual((r.cut, r.raised), (0, 9))
        self.assertEqual(r.lined, tuple((x, 61, z) for x in range(4, 7)
                                        for z in range(4, 7)))
        for x in range(4, 7):
            for z in range(4, 7):
                self.assertEqual(self.name(save, x, 61, z), "minecraft:stone_bricks")
                self.assertEqual(self.name(save, x, 62, z), "minecraft:sand")
                self.assertTrue(self.wet(save, x, 63, z))

    def test_what_somebody_built_is_refused_unless_overwrite(self):
        """Every cell a basin writes over must hold what nobody built:
        ground, ore, cover, a tree, water and what is in it, freeze ice,
        a lily pad. A wall or a sea pickle somebody set as a lamp is named
        and refused, nothing written, unless ``overwrite=True``."""
        def site(save):
            save.set_block(5, 62, 5, "minecraft:iron_ore")
            save.set_block(6, 64, 6, "minecraft:short_grass")
            save.set_block(4, 64, 6, "minecraft:oak_log[axis=y]")
            save.fill((8, 61, 4), (10, 62, 6), WATER)
            save.set_block(8, 61, 4, "minecraft:kelp_plant")
            save.set_block(9, 61, 4, "minecraft:seagrass")
            save.set_block(10, 63, 6, "minecraft:ice")
            save.set_block(9, 63, 5, "minecraft:lily_pad")
            save.set_block(8, 63, 5, "minecraft:air")
        save = self.world(site)
        Builder(save).basin((4, 4, 10, 6), 63, floor=SAND, depth=2)
        self.assertEqual(self.name(save, 5, 62, 5), "minecraft:water")
        self.assertEqual(self.name(save, 9, 61, 4), "minecraft:sand")

        def built(save):
            site(save)
            save.set_block(5, 62, 4, BRICKS)
            save.set_block(9, 62, 6, "minecraft:sea_pickle[pickles=2,waterlogged=true]")
        save = self.world(built)
        before = self.snapshot(save, (3, 60, 3), (11, 66, 7))
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.basin((4, 4, 10, 6), 63, floor=SAND, depth=2)
        self.assertEqual(caught.exception.faults, [
            "(5, 62, 4) holds minecraft:stone_bricks, where the basin writes "
            "minecraft:water[level=0]",
            "(9, 62, 6) holds minecraft:sea_pickle[pickles=2,waterlogged=true], "
            "where the basin writes minecraft:water[level=0]"])
        self.assertIn("overwrite=True", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(self.snapshot(save, (3, 60, 3), (11, 66, 7)), before)
        b.basin((4, 4, 10, 6), 63, floor=SAND, depth=2, overwrite=True)
        self.assertTrue(self.wet(save, 5, 62, 4))
        self.assertEqual(str(save.get_block(9, 62, 6)), WATER)

        # The very block a basin writes is no obstacle to it: a pool of
        # bricks laid again over its own brick floor.
        def pool(save):
            save.fill((4, 62, 4), (6, 62, 6), BRICKS)
        save = self.world(pool)
        r = Builder(save).basin((4, 4, 6, 6), 63, floor=BRICKS, depth=1)
        self.assertEqual(set(r.floor.values()), {62})
        self.assertTrue(self.wet(save, 5, 63, 5))

    def test_what_the_basin_leaves_the_game_will_not_keep_is_refused(self):
        """A torch hung on the bank the cut takes away, and sand laid as a
        lining over a cave, are what the game would drop at its next block
        update: the survival audit refuses either, naming it, with nothing
        written."""
        def bank(save):
            save.set_block(8, 64, 6, STONE)
            save.set_block(9, 64, 6, "minecraft:wall_torch[facing=east]")
        save = self.world(bank)
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.basin((4, 4, 8, 8), 63, floor=SAND, depth=1)
        fault, = caught.exception.faults
        self.assertEqual((fault.cell, fault.kind), ((9, 64, 6), "no footing"))
        self.assertEqual(b.blocks_set, 0)

        def cave(save):
            save.fill((4, 58, 4), (8, 60, 8), "minecraft:cave_air")
        save = self.world(cave)
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.basin((4, 4, 8, 8), 63, floor=STONE, depth=2, lining=SAND)
        self.assertEqual({f.kind for f in caught.exception.faults}, {"falls"})
        self.assertEqual(len(caught.exception.faults), 25)
        self.assertEqual(b.blocks_set, 0)

    def test_a_basin_past_the_world_is_refused_as_the_write_would_be(self):
        """A surface over the world's top is refused before any water is
        read, in the words the write itself would use; and a floor under
        the world's floor is past the world too, whatever lies under it,
        not a lid a lining could hold -- refused so with a lining or
        without one."""
        save = self.world()
        b = Builder(save)
        for surface, depth, kwargs in ((321, 1, {"lining": STONE}),
                                       (-63, 5, {}),
                                       (-63, 5, {"lining": STONE})):
            with self.subTest(surface=surface, **kwargs):
                with self.assertRaises(Refused) as caught:
                    b.basin((4, 4, 6, 6), surface, floor=SAND, depth=depth,
                            **kwargs)
                message = str(caught.exception)
                self.assertTrue(message.startswith("basin cannot be written"),
                                message)
                self.assertIn("past the world", message)
                self.assertNotIn("lining=", message)
                self.assertEqual(b.blocks_set, 0)

    def test_takes_what_nobody_built(self):
        """The one occupancy rule of the ground writers, and the one void
        under a floor."""
        for name in ("minecraft:air", "minecraft:cave_air", "minecraft:stone",
                     "minecraft:grass_block[snowy=false]", "minecraft:gravel",
                     "minecraft:deepslate_iron_ore", "minecraft:poppy",
                     "minecraft:snow[layers=2]", "minecraft:oak_leaves",
                     "minecraft:birch_log[axis=x]", "minecraft:grass"):
            with self.subTest(name):
                self.assertTrue(_takes(BlockState.parse(name)))
        for name in ("minecraft:water[level=3]", "minecraft:lava",
                     "minecraft:kelp", "minecraft:bubble_column",
                     "minecraft:ice", "minecraft:frosted_ice",
                     "minecraft:lily_pad"):
            with self.subTest(name):
                self.assertFalse(_takes(BlockState.parse(name)))
                self.assertTrue(_takes(BlockState.parse(name), wet=True))
        for name in ("minecraft:stone_bricks", "minecraft:torch",
                     "minecraft:rail", "minecraft:packed_ice",
                     "minecraft:oak_stairs[waterlogged=true]",
                     "minecraft:sea_pickle", "mymod:pipe",
                     "minecraft:suspicious_sand"):
            with self.subTest(name):
                self.assertFalse(_takes(BlockState.parse(name), wet=True))
        # The game's FallingBlock.isFree: an air, fire, a liquid, or a
        # block whose properties make it replaceable -- hanging roots, leaf
        # litter, a light block among them -- and an id it loads as air.
        for state in (None, "minecraft:air", "minecraft:void_air",
                      "minecraft:water[level=0]", "minecraft:lava",
                      "minecraft:bubble_column", "minecraft:fire",
                      "minecraft:snow", "minecraft:short_grass",
                      "minecraft:grass", "minecraft:glow_lichen",
                      "minecraft:hanging_roots", "minecraft:light[level=15]",
                      "minecraft:leaf_litter", "minecraft:crimson_roots",
                      "minecraft:structure_void", "minecraft:bush",
                      "mymod:nothing"):
            with self.subTest(state):
                self.assertTrue(_falls_through(
                    None if state is None else BlockState.parse(state)))
        # Kelp holds water and holds sand up: it is neither a liquid nor
        # replaceable.
        for state in ("minecraft:stone", "minecraft:ice", "minecraft:sand",
                      "minecraft:oak_slab", "minecraft:glass",
                      "minecraft:oak_stairs[waterlogged=true]",
                      "minecraft:kelp", "minecraft:kelp_plant",
                      "minecraft:poppy", "minecraft:torch"):
            with self.subTest(state):
                self.assertFalse(_falls_through(BlockState.parse(state)))


class BasinWaterTests(BasinBase):

    def test_a_gap_in_the_bank_is_refused_and_a_rim_closes_it(self):
        """A notch cut two deep in the bank beside a basin two deep: its
        water would spread into both cells of it, so the basin is refused
        naming each Leak -- the plan's own water, which no seal mends --
        unless ``rim=`` walls it: then the notch is stone bricks,
        ``result.rim`` names its two cells, and nothing else outside the
        outline moved. A flower in the notch is rimmed the same way, and
        the rim stands in no keep-out the call did not name."""
        def notch(save):
            save.fill((9, 62, 6), (9, 63, 6), "minecraft:air")
        save = self.world(notch)
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.basin((4, 4, 8, 8), 63, floor=SAND, depth=2)
        faults = caught.exception.faults
        self.assertEqual([(f.cell, f.kind, f.source) for f in faults],
                         [((9, 62, 6), "spreads", (8, 62, 6)),
                          ((9, 63, 6), "spreads", (8, 63, 6))])
        self.assertTrue(all(isinstance(f, Leak) for f in faults))
        self.assertIn("would let water out", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(self.name(save, 9, 63, 6), "minecraft:air")

        b.reserve(9, 6, 9, 6, name="lane")
        with self.assertRaises(Refused) as caught:
            b.basin((4, 4, 8, 8), 63, floor=SAND, depth=2, rim=BRICKS)
        self.assertIn("basin's rim would stand in reservation 'lane'",
                      str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        outside = self.snapshot(save, (10, 60, 3), (12, 65, 9))
        r = b.basin((4, 4, 8, 8), 63, floor=SAND, depth=2, rim=BRICKS,
                    through=("lane",))
        self.assertEqual(r.rim, ((9, 62, 6), (9, 63, 6)))
        for cell in r.rim:
            self.assertEqual(self.name(save, *cell), "minecraft:stone_bricks")
        self.assertEqual(self.snapshot(save, (10, 60, 3), (12, 65, 9)), outside)
        self.assertTrue(water_body(save, (6, 63, 6)).still)

        def flower(save):
            save.set_block(9, 62, 6, "minecraft:grass_block")
            save.set_block(9, 63, 6, "minecraft:poppy")
        save = self.world(flower)
        r = Builder(save).basin((4, 4, 8, 8), 63, floor=SAND, depth=1,
                                rim=Mix([(BRICKS, 1), (COBBLE, 1)]))
        self.assertEqual(r.rim, ((9, 63, 6),))
        self.assertIn(self.name(save, 9, 63, 6), (BRICKS, COBBLE))

    def test_the_rim_never_covers_what_somebody_built_and_lava_is_refused_or_rimmed(self):
        """A torch in the notch would wash away, and a dry rail between
        three arms of the water would be waterlogged by them: the rim
        covers neither -- bricking up somebody's track is not sealing --
        so either is refused with ``rim=`` given. Lava in the notch is a
        ``"lava"`` leak no seal mends, and the rim takes its place."""
        def torch(save):
            save.set_block(9, 63, 6, "minecraft:torch")
        save = self.world(torch)
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.basin((4, 4, 8, 8), 63, floor=SAND, depth=1, rim=BRICKS)
        leak, = caught.exception.faults
        self.assertEqual((leak.cell, leak.kind, leak.block.name),
                         ((9, 63, 6), "spreads", "minecraft:torch"))
        self.assertEqual(b.blocks_set, 0)

        def rail(save):
            save.set_block(8, 63, 6, "minecraft:rail")
        save = self.world(rail)
        b = Builder(save)
        notched = [(x, z) for x in range(4, 9) for z in range(4, 9)
                   if (x, z) != (8, 6)]
        with self.assertRaises(Refused) as caught:
            b.basin(notched, 63, floor=SAND, depth=1, rim=BRICKS)
        kinds = {(f.cell, f.kind) for f in caught.exception.faults}
        self.assertEqual(kinds, {((8, 63, 6), "waterlogs")})
        self.assertEqual(self.name(save, 8, 63, 6), "minecraft:rail")
        # Lichen there is ground cover, waterlogged or not: the rim takes it.
        def lichen(save):
            save.set_block(8, 63, 6, "minecraft:glow_lichen[down=true,"
                                     "waterlogged=false]")
        save = self.world(lichen)
        r = Builder(save).basin(notched, 63, floor=SAND, depth=1, rim=BRICKS)
        self.assertEqual(r.rim, ((8, 63, 6),))

        def lava(save):
            save.set_block(9, 63, 6, "minecraft:lava")
        for kwargs in ({}, {"seal": COBBLE}):
            save = self.world(lava)
            b = Builder(save)
            with self.assertRaises(Refused) as caught:
                b.basin((4, 4, 8, 8), 63, floor=SAND, depth=1, **kwargs)
            leak, = caught.exception.faults
            self.assertEqual((leak.cell, leak.kind), ((9, 63, 6), "lava"))
            self.assertIn("No seal mends lava", str(caught.exception))
            self.assertEqual(b.blocks_set, 0)
        save = self.world(lava)
        r = Builder(save).basin((4, 4, 8, 8), 63, floor=SAND, depth=1,
                                rim=BRICKS)
        self.assertEqual(r.rim, ((9, 63, 6),))
        self.assertEqual(self.name(save, 9, 63, 6), "minecraft:stone_bricks")

    def test_a_basin_opening_into_a_river_is_refused_unless_sealed(self):
        """A basin cut beside a river, its surface three under the river's,
        opens the river's bank: the river would pour into the air the cut
        leaves over the basin's water. Refused, naming every cell -- the
        river's water, which ``seal=`` may wall off where it stands -- and
        with a seal the river's edge is cobblestone and the basin is laid
        under it. Level with the river the two are one water, and nothing
        is refused."""
        def river(save):
            save.fill((0, 61, 12), (16, 63, 12), WATER)
        save = self.world(river)
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.basin((2, 4, 10, 11), 60, floor=SAND, depth=1)
        faults = caught.exception.faults
        self.assertEqual(len(faults), 27)
        self.assertEqual({f.source for f in faults},
                         {(x, y, 12) for x in range(2, 11) for y in (61, 62, 63)})
        self.assertEqual({f.cell for f in faults},
                         {(x, y, 11) for x in range(2, 11) for y in (61, 62, 63)})
        self.assertIn("seal=", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        # A rim walls the basin's own water in, never another's out: it
        # leaves the river's way into the cut as it found it.
        with self.assertRaises(Refused) as caught:
            b.basin((2, 4, 10, 11), 60, floor=SAND, depth=1, rim=BRICKS)
        self.assertEqual(len(caught.exception.faults), 27)
        self.assertEqual(b.blocks_set, 0)
        b.reserve(0, 12, 16, 12, name="river")
        with self.assertRaises(Refused) as caught:
            b.basin((2, 4, 10, 11), 60, floor=SAND, depth=1, seal=COBBLE)
        self.assertIn("basin's seals would stand in reservation 'river'",
                      str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        r = b.basin((2, 4, 10, 11), 60, floor=SAND, depth=1, seal=COBBLE,
                    through=("river",))
        self.assertEqual(r.sealed, 27)
        for x in range(2, 11):
            for y in (61, 62, 63):
                self.assertEqual(self.name(save, x, y, 12), COBBLE)
                self.assertEqual(self.name(save, x, y, 11), "minecraft:air")
            self.assertTrue(self.wet(save, x, 60, 11))
        self.assertTrue(self.wet(save, 1, 62, 12))
        # A stream running past: walling its water off where it stands
        # takes the feed of the flow below the seals, which dries.
        def stream(save):
            save.set_block(0, 63, 12, WATER)
            for level in range(1, 8):
                save.set_block(level, 63, 12, f"minecraft:water[level={level}]")
        save = self.world(stream)
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.basin((1, 4, 5, 11), 61, floor=SAND, depth=1, seal=COBBLE)
        self.assertEqual(caught.exception.faults, [(6, 63, 12), (7, 63, 12)])
        self.assertIn("basin would cut flowing water off", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        r = b.basin((1, 4, 5, 11), 61, floor=SAND, depth=1, seal=COBBLE,
                    allow_dam=True)
        self.assertEqual(r.sealed, 5)
        save = self.world(river)
        r = Builder(save).basin((2, 4, 10, 11), 63, floor=SAND, depth=3)
        self.assertEqual(r.sealed, 0)
        self.assertTrue(water_body(save, (6, 63, 6)).cells
                        >= {(6, 62, 12), (6, 63, 11)})

    def test_a_basin_lowering_a_pond_that_feeds_a_stream_is_refused(self):
        """A pond cell at 63 feeds a stream running east from it, levels
        1..7. Laid at 62, the basin takes that cell's water out, and the
        game re-reads every flow from what feeds it: the whole stream
        would dry where it stands. Refused, naming every cell starved,
        unless ``allow_dam=True`` -- and then the stream, no longer held
        back by the source, would run into the air over the lowered water,
        a leak sealed only by walling it off. A pond lowered that feeds
        nothing is laid."""
        def stream(save):
            save.set_block(2, 63, 4, WATER)
            for level in range(1, 8):
                save.set_block(2 + level, 63, 4, f"minecraft:water[level={level}]")
        save = self.world(stream)
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.basin((0, 2, 2, 6), 62, floor=SAND, depth=1)
        self.assertEqual(caught.exception.faults,
                         [(x, 63, 4) for x in range(3, 10)])
        message = str(caught.exception)
        self.assertIn("basin would cut flowing water off", message)
        self.assertIn("allow_dam=True", message)
        self.assertEqual(b.blocks_set, 0)
        with self.assertRaises(Refused) as caught:
            b.basin((0, 2, 2, 6), 62, floor=SAND, depth=1, allow_dam=True)
        leak, = caught.exception.faults
        self.assertEqual((leak.cell, leak.source), ((2, 63, 4), (3, 63, 4)))
        self.assertEqual(b.blocks_set, 0)
        r = b.basin((0, 2, 2, 6), 62, floor=SAND, depth=1, allow_dam=True,
                    seal=COBBLE)
        self.assertEqual(r.sealed, 1)
        self.assertEqual(self.name(save, 2, 63, 4), "minecraft:air")
        self.assertEqual(self.name(save, 3, 63, 4), COBBLE)
        self.assertTrue(self.wet(save, 2, 62, 4))

        def pond(save):
            save.fill((4, 61, 4), (6, 63, 6), WATER)
            save.set_block(5, 64, 5, "minecraft:lily_pad")
            save.set_block(7, 64, 5, "minecraft:tall_grass[half=lower]")
            save.set_block(7, 65, 5, "minecraft:tall_grass[half=upper]")
        save = self.world(pond)
        Builder(save).basin((4, 4, 7, 6), 62, floor=SAND, depth=1)
        # What lay on the water, and the plant over the cut bank, go with
        # them: nothing is left hanging over air.
        for y in (63, 64):
            self.assertEqual(self.name(save, 5, y, 5), "minecraft:air")
        for y in (63, 64, 65):
            self.assertEqual(self.name(save, 7, y, 5), "minecraft:air")
        self.assertTrue(self.wet(save, 5, 62, 5))
        self.assertTrue(self.wet(save, 7, 62, 5))

        # A snow layer on a frosted ice sheet -- the game keeps none on
        # ice or packed ice, its cannot_support_snow_layer tag, but frosted
        # and blue ice hold one -- goes with the sheet the basin turns to
        # water: it would lie on water.
        def frosted(save):
            save.fill((4, 61, 4), (6, 62, 6), WATER)
            save.fill((4, 63, 4), (6, 63, 6), "minecraft:frosted_ice[age=0]")
            save.set_block(5, 64, 5, "minecraft:snow[layers=1]")
        save = self.world(frosted)
        Builder(save).basin((4, 4, 6, 6), 63, floor=SAND, depth=2)
        self.assertEqual(str(save.get_block(5, 63, 5)), WATER)
        self.assertEqual(self.name(save, 5, 64, 5), "minecraft:air")

    def test_a_moat_poured_from_the_top_falls_and_a_basin_is_still(self):
        """The walled city's moat was poured in from above: a skin of
        sources over a trench of air, which falls -- ``contained`` names
        every source of the skin a ``"falls"`` leak, so no writer lays it.
        A basin twelve deep in the same trench writes every cell a source
        from its floor up, and the moat is still."""
        def trench(save):
            save.fill((4, 52, 4), (8, 63, 6), "minecraft:air")
        save = self.world(trench)
        skin = {(x, 63, z): WATER for x in range(4, 9) for z in range(4, 7)}
        held = contained(save, skin)
        self.assertEqual({leak.kind for leak in held.leaks}, {"falls"})
        self.assertEqual(len(held.leaks), 15)
        r = Builder(save).basin((4, 4, 8, 6), 63, floor=STONE, depth=12)
        self.assertEqual(set(r.floor.values()), {51})
        body = water_body(save, (6, 63, 5))
        self.assertTrue(body.still)
        self.assertEqual(len(body), 15 * 12)

    def test_a_basin_is_water_to_the_ground_writers_after_it(self):
        """The session learns every column's ground at the basin's surface,
        and that height is water: a pad laid over it afterwards is refused
        as over water, unless told to bridge it. ``finish()`` settles what
        was written, and the game keeps all of it."""
        save = self.world()
        b = Builder(save)
        b.basin((4, 4, 8, 8), 63, floor=SAND, depth=2)
        for x in range(4, 9):
            for z in range(4, 9):
                self.assertEqual(b.ground(x, z), 63)
        with self.assertRaises(Refused) as caught:
            b.level_pad(6, 6, 10, 10, 64)
        self.assertIn("over water", str(caught.exception))
        b.level_pad(6, 6, 10, 10, 64, over_water="bridge", clearance=0)
        self.assertTrue(self.wet(save, 6, 63, 6))
        done = b.finish()
        self.assertEqual(done.unsupported, [])
        self.assertEqual(save.surface_y(5, 5, "WORLD_SURFACE"), 63)


class BasinParkTests(BasinBase):

    def test_the_parks_lake_comes_out_as_the_park_laid_it(self):
        """``tools/park/build_park.py`` laid the park's lake by hand from
        ``park_plan.depth_map``: each column's bed at ``WATER_TOP - d``,
        water from there up to 60 and air over it. Given the same map over
        the same 499 columns, on ground of stone to the lawn at 61, a basin
        puts every bed there -- 103, 244 and 152 columns one, two and three
        deep -- and the water read back is the plan's depth everywhere."""
        spec = importlib.util.spec_from_file_location(
            "_basin_park_plan", os.path.join(_ROOT, "tools", "park", "park_plan.py"))
        park = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(park)
        lake = park.lake_cells()
        depth = park.depth_map(lake)
        self.assertEqual(len(lake), 499)
        save = self.world(chunks=[(x, z) for x in range(-3, 0)
                                  for z in range(15, 18)], top=park.GY)
        r = Builder(save).basin(lake, park.WATER_TOP, floor=SAND, depth=depth)
        self.assertEqual(r.floor, {c: park.WATER_TOP - depth[c]
                                   for c in sorted(lake, key=lambda c: (c[1], c[0]))})
        counts = {}
        for d in depth.values():
            counts[d] = counts.get(d, 0) + 1
        self.assertEqual(counts, {1: 103, 2: 244, 3: 152})
        for x, z in sorted(lake):                       # build_park.water's
            bed_y = park.WATER_TOP - depth[(x, z)]
            self.assertEqual(self.name(save, x, bed_y, z), "minecraft:sand")
            for y in range(bed_y + 1, park.WATER_TOP + 1):
                self.assertEqual(str(save.get_block(x, y, z)), park.WATER)
            for y in range(park.WATER_TOP + 1, 64):
                self.assertEqual(self.name(save, x, y, z), park.AIR)
        body = water_body(save, min(lake))
        self.assertTrue(body.still)
        self.assertEqual(body.depth, depth)
        self.assertEqual(r.water, sum(depth.values()))


class BasinLightTests(BasinBase):

    @staticmethod
    def lid(x, z, y=63):
        """A room three high under an ice lid at ``(x, y, z)``."""
        def shape(save):
            save.fill((x, y - 3, z), (x, y - 1, z), "minecraft:air")
            save.set_block(x, y, z, "minecraft:ice")
        return shape

    def test_a_lit_basin_keeps_a_nearby_lid_frozen(self):
        """A pond lit to stay open (``NO_NEW_SNOW_OR_ICE``, 10) by sea
        lanterns in its floor: every surface cell reaches 10 by the game's
        flood. A room roofed with ice one block off the shore would be
        melted by the lantern the lattice puts at the corner (13 there,
        and ice melts at 11), so the lamps go elsewhere and the lid holds
        at 10 or under. A trough three long with a lid two off its side
        has nowhere to put a lamp that could not melt it, and is refused,
        naming the dark surface; a lid six off is out of every lamp's reach
        and limits nothing."""
        lamped = dict(floor=SAND, depth=1, lamp=LANTERN,
                      light_level=NO_NEW_SNOW_OR_ICE)
        save = self.world()
        open_pond = Builder(save).basin((4, 4, 12, 12), 63, **lamped)
        self.assertIn((12, 62, 4), open_pond.light.lamps)
        self.assertEqual(open_pond.lids, ())

        save = self.world(self.lid(13, 4))
        r = Builder(save).basin((4, 4, 12, 12), 63, **lamped)
        self.assertEqual(r.lids, ((13, 63, 4),))
        self.assertNotIn((12, 62, 4), r.light.lamps)
        for x, y, z in r.light.lamps:
            self.assertGreaterEqual(abs(x - 13) + abs(y - 63) + abs(z - 4), 5)
            self.assertEqual(self.name(save, x, y, z), "minecraft:sea_lantern")
        field = simulate_block_light(save, (0, 55, 0), (20, 68, 20))
        self.assertLessEqual(field.level(13, 63, 4), 10)
        for x in range(4, 13):
            for z in range(4, 13):
                self.assertGreaterEqual(field.level(x, 63, z), 10, (x, z))
        self.assertEqual(self.name(save, 13, 63, 4), "minecraft:ice")
        corner = simulate_block_light(save, (0, 55, 0), (20, 68, 20),
                                      overlay={(12, 62, 4): LANTERN})
        self.assertGreater(corner.level(13, 63, 4), 10)

        # Ice the basin itself cuts away is no lid, though it stood over
        # air: a sheet on a post in the outline, cleared with the rest.
        def sheet(save):
            save.set_block(12, 65, 4, "minecraft:ice")
        save = self.world(sheet)
        r = Builder(save).basin((4, 4, 12, 12), 63, **lamped)
        self.assertEqual(r.lids, ())
        self.assertEqual(self.name(save, 12, 65, 4), "minecraft:air")

        # Ice lying on the ground roofs nothing: it is no lid.
        def ice(save):
            save.set_block(13, 63, 4, "minecraft:ice")
        save = self.world(ice)
        r = Builder(save).basin((4, 4, 12, 12), 63, **lamped)
        self.assertEqual(r.lids, ())
        self.assertIn((12, 62, 4), r.light.lamps)

        save = self.world(self.lid(5, 6))
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.basin((4, 4, 6, 4), 63, **lamped)
        message = str(caught.exception)
        self.assertIn("3 of its 3 surface cells stay under light 10", message)
        self.assertIn("1 ice lid within reach", message)
        self.assertIn("3 places a lamp could go were held back for it",
                      message)
        self.assertEqual(b.blocks_set, 0)

        save = self.world(self.lid(5, 10))
        r = Builder(save).basin((4, 4, 6, 4), 63, **lamped)
        self.assertEqual(r.lids, ())
        field = simulate_block_light(save, (0, 55, 0), (10, 68, 14))
        self.assertLessEqual(field.level(5, 63, 10), 10)
        self.assertTrue(all(field.level(x, 63, 4) >= 10 for x in range(4, 7)))

    def test_a_lid_at_the_edge_of_a_lamps_reach_is_found(self):
        """A sea lantern's 15 falls to 11 four steps away, and ice melts at
        11: a lid exactly four from the one place a lamp could go, along a
        tunnel level with it, or three below it down a shaft, is held at
        10 -- that place is held back, so the one-column pool is dark and
        refused, nothing written -- where the lamp would have melted it.
        Ice between two pools, out of either's reach though inside the box
        the two span, limits nothing."""
        lamped = dict(floor=SAND, depth=1, lamp=LANTERN,
                      light_level=NO_NEW_SNOW_OR_ICE)

        def tunnel(save):
            save.fill((7, 62, 5), (9, 62, 5), "minecraft:air")
            save.fill((10, 59, 5), (10, 61, 5), "minecraft:air")
            save.set_block(10, 62, 5, "minecraft:ice")

        def shaft(save):
            save.fill((7, 61, 5), (7, 62, 5), "minecraft:air")
            save.fill((7, 57, 5), (7, 59, 5), "minecraft:air")
            save.set_block(7, 60, 5, "minecraft:ice")
        for shape, lid in ((tunnel, (10, 62, 5)), (shaft, (7, 60, 5))):
            with self.subTest(lid=lid):
                save = self.world(shape)
                melted = simulate_block_light(save, lid, lid,
                                              overlay={(6, 62, 5): LANTERN})
                self.assertGreater(melted.level(*lid), 10)
                b = Builder(save)
                with self.assertRaises(Refused) as caught:
                    b.basin((6, 5, 6, 5), 63, **lamped)
                message = str(caught.exception)
                self.assertIn("1 of its 1 surface cells stay under light 10",
                              message)
                self.assertIn("1 ice lid within reach", message)
                self.assertIn("1 place a lamp could go was held back", message)
                self.assertEqual(b.blocks_set, 0)
                self.assertEqual(self.name(save, *lid), "minecraft:ice")

        def between(save):
            save.fill((12, 60, 12), (12, 62, 12), "minecraft:air")
            save.set_block(12, 63, 12, "minecraft:ice")
        save = self.world(between)
        pools = sorted(set((x, z) for x in range(2, 5) for z in range(2, 5))
                       | set((x, z) for x in range(20, 23)
                             for z in range(20, 23)))
        r = Builder(save).basin(pools, 63, **lamped)
        self.assertEqual((r.lids, r.melting), ((), ()))

    def test_a_lid_the_world_already_melts_is_not_the_basins(self):
        """Ice a glowstone beside it already lights to 14 melts whatever
        the basin does: it is no limit on the lamps and no refusal, and
        the result names it in ``melting``. Ice lit to 14 by lava the
        basin fills with water is not melting once the basin is laid, and
        its lamps hold it at 10. Ice the basin's own floor would light past
        10 -- glowstone laid two from it -- is the basin's, and refused as
        such, since no lamp could do it."""
        lamped = dict(depth=1, lamp=LANTERN, light_level=NO_NEW_SNOW_OR_ICE)

        def lit(save):
            self.lid(14, 8)(save)
            save.set_block(15, 63, 8, "minecraft:glowstone")
        save = self.world(lit)
        self.assertEqual(simulate_block_light(save, (14, 63, 8), (14, 63, 8))
                         .level(14, 63, 8), 14)
        r = Builder(save).basin((4, 4, 12, 12), 63, floor=SAND, **lamped)
        self.assertEqual((r.lids, r.melting), ((), ((14, 63, 8),)))
        self.assertTrue(r.light.ok)

        # Lava in the outline lights the lid to 14 too, but the basin
        # fills it with water: the lid is its lamps' to keep frozen then.
        def lava(save):
            self.lid(13, 4)(save)
            save.set_block(12, 63, 4, "minecraft:lava")
        save = self.world(lava)
        self.assertEqual(simulate_block_light(save, (13, 63, 4), (13, 63, 4))
                         .level(13, 63, 4), 14)
        r = Builder(save).basin((4, 4, 12, 12), 63, floor=SAND, **lamped)
        self.assertEqual((r.lids, r.melting), (((13, 63, 4),), ()))
        self.assertNotIn((12, 62, 4), r.light.lamps)
        self.assertLessEqual(simulate_block_light(save, (13, 63, 4),
                                                  (13, 63, 4)).level(13, 63, 4),
                             10)

        save = self.world(self.lid(13, 4))
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.basin((4, 4, 12, 12), 63, floor="minecraft:glowstone", **lamped)
        message = str(caught.exception)
        self.assertTrue(message.startswith(
            "basin would melt the ice near its water: 1 ice lid would pass "
            "light 10 by what the basin itself lays or cuts"), message)
        self.assertIn("Keep the water further from the lids", message)
        self.assertEqual(b.blocks_set, 0)

    def test_a_lid_is_ice_over_what_the_game_loads_as_air(self):
        """A lid is ice over air as the game loads the world and as the
        plan leaves it: over an id the game does not have, which it loads
        as air, ice is a lid; over a notch in the bank the rim fills, it
        is not, though air stood there before."""
        lamped = dict(floor=SAND, depth=1, lamp=LANTERN,
                      light_level=NO_NEW_SNOW_OR_ICE)

        def modded(save):
            save.set_block(13, 62, 4, "mymod:pipe")
            save.set_block(13, 63, 4, "minecraft:ice")
        save = self.world(modded)
        r = Builder(save).basin((4, 4, 12, 12), 63, **lamped)
        self.assertEqual(r.lids, ((13, 63, 4),))
        self.assertNotIn((12, 62, 4), r.light.lamps)

        def notch(save):
            save.set_block(13, 63, 8, "minecraft:air")
            save.set_block(13, 64, 8, "minecraft:ice")
        save = self.world(notch)
        r = Builder(save).basin((4, 4, 12, 12), 63, rim=BRICKS, **lamped)
        self.assertEqual(r.rim, ((13, 63, 8),))
        self.assertEqual(r.lids, ())

    def test_a_lamp_in_the_water_stands_in_its_lowest_cell(self):
        """A lamp that holds water -- four sea pickles, as the frozen lake
        was lit -- stands in the lowest cell of water over the floor, the
        surface itself in water one deep, and is the water's own there; a
        lamp neither whole nor wet is refused before the ground is read, as
        is one that gives off no light."""
        pickles = "minecraft:sea_pickle[pickles=4,waterlogged=true]"
        save = self.world()
        r = Builder(save).basin((4, 4, 12, 12), 63, floor=SAND, depth=3,
                                lamp=pickles, light_level=NO_NEW_SNOW_OR_ICE)
        self.assertTrue(r.light.lamps)
        for x, y, z in r.light.lamps:
            self.assertEqual(y, r.floor[(x, z)] + 1)
            self.assertEqual(str(save.get_block(x, y, z)), pickles)
        self.assertTrue(water_body(save, (8, 63, 8)).still)
        save = self.world()
        r = Builder(save).basin((4, 4, 8, 8), 63, floor=SAND, depth=1,
                                lamp=pickles, light_level=10)
        self.assertTrue(all(y == 63 for _, y, _ in r.light.lamps))
        for lamp, error in (("minecraft:lantern", NBTError),
                            ("minecraft:stone", NBTError),
                            (Mix([(LANTERN, 1)]), TypeError)):
            with self.subTest(lamp=lamp):
                save = self.world()
                b = Builder(save)
                with self.assertRaises(error):
                    b.basin((4, 4, 8, 8), 63, floor=SAND, depth=1, lamp=lamp,
                            light_level=10)
                self.assertEqual(b.blocks_set, 0)


class BasinArgumentTests(BasinBase):

    def test_the_depth_takes_a_tank_a_mapping_or_a_profile(self):
        """A whole depth is a tank; a mapping gives each column its own and
        must cover them all with whole depths of at least one; a callable
        is a profile of the distance; ``max_depth`` caps whichever it is.
        Anything else is an ``NBTError`` before anything is written."""
        outline = sorted(geometry.disc(4, center=(8, 8)),
                         key=lambda c: (c[1], c[0]))
        save = self.world()
        r = Builder(save).basin(outline, 63, floor=SAND, depth=2)
        self.assertEqual(set(r.floor.values()), {61})
        save = self.world()
        mapping = {c: 1 + (c[0] + c[1]) % 2 for c in outline}
        mapping[(30, 30)] = 9          # outside the outline: not read
        r = Builder(save).basin(outline, 63, floor=SAND, depth=mapping)
        self.assertEqual(r.floor, {c: 63 - mapping[c] for c in outline})
        save = self.world()
        r = Builder(save).basin(outline, 63, floor=SAND,
                                depth=lambda d: 1 + int(d >= 3), max_depth=1)
        self.assertEqual(set(r.floor.values()), {62})
        distance = distance_to_edge(outline)
        save = self.world()
        r = Builder(save).basin(outline, 63, floor=SAND,
                                depth=lambda d: 1 + int(d >= 3))
        self.assertEqual(r.floor, {c: 62 - int(distance[c] >= 3)
                                   for c in outline})
        save = self.world()
        for depth, words in ((0, "at least 1"), (True, "a whole depth"),
                             ({outline[0]: 1}, "every column"),
                             ({c: 0 for c in outline}, "every column"),
                             (lambda d: 0, "at least 1"),
                             (lambda d: 1.5, "at least 1"),
                             ("deep", "a whole depth")):
            with self.subTest(depth=depth):
                b = Builder(save)
                with self.assertRaises(NBTError) as caught:
                    b.basin(outline, 63, floor=SAND, depth=depth)
                self.assertIn(words, str(caught.exception))
                self.assertEqual(b.blocks_set, 0)
        for max_depth in (0, 1.0, True):
            with self.assertRaises(NBTError):
                Builder(save).basin(outline, 63, floor=SAND, max_depth=max_depth)

    def test_arguments_are_refused_before_anything_is_written(self):
        """A surface that is no whole y, an empty outline, a lamp without a
        level or a level without a lamp, no floor, a rim water passes or
        enters -- a slab, a copper grate it waterlogs -- or that melts, and
        a seal that is no wall: each refused before a
        block moves. The Builder takes the basin after the ground, as one
        writer of the earthwork."""
        save = self.world()
        before = self.snapshot(save, (2, 60, 2), (10, 65, 10))
        b = Builder(save)
        rect = (4, 4, 8, 8)
        for call, error, words in (
                (lambda: b.basin(rect, 63.0, floor=SAND), NBTError, "whole y"),
                (lambda: b.basin([], 63, floor=SAND), NBTError,
                 "outline holds no columns, so there is nothing to lay"),
                (lambda: b.basin(rect, 63, floor=SAND, light_level=10),
                 NBTError, "needs a lamp="),
                (lambda: b.basin(rect, 63, floor=SAND, lamp=LANTERN),
                 NBTError, "needs a light_level="),
                (lambda: b.basin(rect, 63, floor=None), TypeError, "floor=None"),
                (lambda: b.basin(rect, 63, floor=SAND,
                                 rim="minecraft:stone_brick_slab"),
                 NBTError, "would not hold water back"),
                (lambda: b.basin(rect, 63, floor=SAND,
                                 rim="minecraft:copper_grate"),
                 NBTError, "would not hold water back"),
                (lambda: b.basin(rect, 63, floor=SAND,
                                 rim=Mix([(BRICKS, 3), ("minecraft:ice", 1)])),
                 NBTError, "would not stay a wall"),
                (lambda: b.basin(rect, 63, floor=SAND, seal="minecraft:sand"),
                 NBTError, "would not stay a wall"),
                (lambda: b.basin(rect, 63, floor="minecraft:no_such_floor"),
                 Refused, "no_such_floor")):
            with self.subTest(words):
                with self.assertRaises(error) as caught:
                    call()
                self.assertIn(words, str(caught.exception))
                self.assertEqual(b.blocks_set, 0)
        self.assertEqual(self.snapshot(save, (2, 60, 2), (10, 65, 10)), before)
        # A rim and a seal are held to one wall-to-water test: a block the
        # game does not have, passed by allow=, is taken at its word, so
        # one that says it is waterlogged holds water and is no wall for
        # either, and one that says nothing is.
        modded = Builder(save, allow=("minecraft:mod_wall",))
        for role in ("rim", "seal"):
            with self.subTest(role=role):
                with self.assertRaises(NBTError) as caught:
                    modded.basin(rect, 63, floor=SAND,
                                 **{role: "minecraft:mod_wall[waterlogged=true]"})
                self.assertIn("would not hold water back", str(caught.exception))
                self.assertEqual(modded.blocks_set, 0)
        self.assertEqual(self.snapshot(save, (2, 60, 2), (10, 65, 10)), before)
        empty = self.world(chunks=[(0, 0)])
        with self.assertRaises(NoSurface):
            Builder(empty).basin((10, 10, 20, 12), 63, floor=SAND)
        self.assertEqual(
            [kind.__name__ for kind in Builder.__mro__[1:11]],
            ["LightingMixin", "SiteMixin", "WaterMixin", "ShellsMixin",
             "WallsMixin", "RoofMixin", "FurnitureMixin", "VaultMixin",
             "StairsMixin", "RailwayMixin"])


if __name__ == "__main__":
    unittest.main()
