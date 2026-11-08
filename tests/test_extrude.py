"""Tests for Builder.extrude: a cross-section swept along a line, following
the ground.

The two terrain-following walls built so far each wrote the sweep again:
the JeffWorld Roman walls (``tools/roman_city/jw_groundwork.py``,
``smoothed_line``) and the secret lab's curtain wall
(``tools/secret_lab/m1_groundwork.py``, ``_wall_tops``), which took the
plane, 66, for any column it could not read and planned its wall at 73 over
ground nobody had read. A section is drawn once, facing south, swept along
the band -- one line per across value, mitered at every right-angle corner
-- at a profile that follows the ground, levels every turn and steps a
whole block at most, read before anything is written, and written in one
total order.
"""

from __future__ import annotations

import importlib.util
import os
import random
import shutil
import sys
import tempfile
import types
import unittest

from heaton_voxel.blocks import BlockState
from heaton_voxel.craft import (Builder, CrossSection, ExtrudeResult, Material,
                                Merlon, Mix)
from heaton_voxel.craft import geometry
from heaton_voxel.craft.build.extrude import _band, _level_turns
from heaton_voxel.craft.build.walls import _path_roles
from heaton_voxel.craft.errors import NoSurface, Refused
from heaton_voxel.craft.style import FROM_PALETTE
from heaton_voxel.nbt import NBTError
from heaton_voxel.world import MinecraftSave
from tests.test_geometry import _tool_function
from tests.test_world import make_save

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "examples"))
import sections  # noqa: E402

CHUNKS = [(x, z) for x in range(0, 3) for z in range(0, 3)]
AIR = "minecraft:air"
DIRT = "minecraft:dirt"
GRASS = "minecraft:grass_block[snowy=false]"
STONE = "minecraft:stone_bricks"
COBBLE = "minecraft:cobblestone"
SLAB = "minecraft:stone_brick_slab[type=bottom,waterlogged=false]"
PLANKS = "minecraft:oak_planks"
WATER = "minecraft:water[level=0]"


def rectangle(x0, z0, x1, z1):
    """A closed path round a rectangle, clockwise from its northwest
    corner: +across is its inside."""
    return [(x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0)]


def _neighbors(x, z):
    return ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1))


class ExtrudeBase(unittest.TestCase):
    """A lawn of grass at y=61 on dirt, three chunks square from the origin,
    shaped by ``shape`` before its heightmaps are rebuilt."""

    def world(self, shape=None, *, chunks=CHUNKS):
        directory = tempfile.mkdtemp(prefix="extrude-tests-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        make_save(directory, chunks)
        save = MinecraftSave.load(directory)
        xs = [c[0] for c in chunks]
        zs = [c[1] for c in chunks]
        low = (min(xs) * 16, 40, min(zs) * 16)
        high = (max(xs) * 16 + 15, 61, max(zs) * 16 + 15)
        save.fill(low, (high[0], 60, high[2]), DIRT)
        save.fill((low[0], 61, low[2]), high, GRASS)
        if shape is not None:
            shape(save)
        save.rebuild_heightmaps((low[0], 0, low[2]), (high[0], 0, high[2]))
        self.save = save
        self.directory = directory
        return save

    def twin(self, save):
        """Another world holding just what ``save`` holds, in a directory
        of its own: ``save`` saved, and its files copied and loaded -- the
        same ground read back, for the cost of a copy rather than of
        raising it again."""
        save.save()
        directory = tempfile.mkdtemp(prefix="extrude-tests-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        shutil.rmtree(directory)
        shutil.copytree(save.path, directory)
        return MinecraftSave.load(directory)

    def at(self, x, y, z) -> str:
        return str(self.save.get_block(x, y, z))

    def name(self, x, y, z) -> str:
        return self.save.get_block(x, y, z).name


# --------------------------------------------------------------------------
# The section
# --------------------------------------------------------------------------

class CrossSectionTests(unittest.TestCase):
    def test_rows_reads_a_grid_top_first(self):
        """Character j of row r is the cell (left + j, top - r); '.' and
        ' ' leave a cell out, and so cannot be keys; a character the key
        does not name is named back."""
        section = CrossSection.rows(["ab.", " c "],
                                    {"a": STONE, "b": COBBLE, "c": DIRT},
                                    left=-1, top=1)
        self.assertEqual(dict(section.cells),
                         {(-1, 1): BlockState.parse(STONE),
                          (0, 1): BlockState.parse(COBBLE),
                          (0, 0): BlockState.parse(DIRT)})
        self.assertEqual(section.across, range(-1, 1))
        self.assertEqual(section.lowest(0), 0)
        self.assertEqual(section.lowest(-1), 1)
        self.assertIsNone(section.lowest(1))
        with self.assertRaisesRegex(NBTError, r"'x' at column 1"):
            CrossSection.rows(["ax"], {"a": STONE}, left=0, top=0)
        for bad in (".", " "):
            with self.assertRaisesRegex(NBTError, "cannot be a key"):
                CrossSection.rows(["a"], {"a": STONE, bad: COBBLE},
                                  left=0, top=0)
        with self.assertRaises(NBTError):
            CrossSection.rows("ab", {"a": STONE, "b": STONE}, left=0, top=0)

    def test_a_walk_needs_room_for_a_body(self):
        """A walk cell is a block of the section a body stands on, with
        nothing solid one or two over it -- air, a torch or nothing at all
        there is room; a block, a Merlon or a fence is not."""
        base = {(0, 0): STONE, (1, 0): STONE}
        for above in (None, AIR, "minecraft:torch", "minecraft:cave_air"):
            cells = dict(base)
            if above is not None:
                cells[(0, 1)] = above
                cells[(0, 2)] = above
            with self.subTest(above=above):
                self.assertEqual(CrossSection(cells, walk=[(0, 0)]).walk,
                                 ((0, 0),))
        for k in (1, 2):
            for solid in (STONE, Merlon(STONE), "minecraft:oak_fence", SLAB):
                with self.subTest(k=k, solid=solid):
                    with self.assertRaisesRegex(NBTError, "two cells over it"):
                        CrossSection({**base, (0, k): solid}, walk=[(0, 0)])
        # A third cell over it is the walk check's to judge, not the
        # section's.
        CrossSection({**base, (0, 3): STONE}, walk=[(0, 0)])
        for floor in (AIR, Merlon(STONE), "minecraft:oak_fence",
                      "minecraft:torch"):
            with self.subTest(floor=floor):
                with self.assertRaisesRegex(NBTError, "nobody stands on"):
                    CrossSection({(0, 0): floor}, walk=[(0, 0)])
        with self.assertRaisesRegex(NBTError, "not one of the section's"):
            CrossSection(base, walk=[(2, 0)])
        self.assertEqual(CrossSection({(0, 0): SLAB}, walk=[(0, 0)]).walk,
                         ((0, 0),))

    def test_bad_pieces_are_refused_by_kind(self):
        """A list of pairs is a TypeError pointing at Mix, a palette's
        sentinel a TypeError, None an NBTError (leave the cell out); an
        air, fluid or Merlon footing, and bury with no footing, are
        NBTErrors, and Merlon(None) is a TypeError, as battlement's
        block=None is."""
        with self.assertRaisesRegex(TypeError, "make it a Mix"):
            CrossSection({(0, 0): [(STONE, 1), (COBBLE, 1)]})
        with self.assertRaisesRegex(TypeError, "palette"):
            CrossSection({(0, 0): FROM_PALETTE})
        with self.assertRaisesRegex(NBTError, "leave the cell out"):
            CrossSection({(0, 0): None})
        with self.assertRaises(TypeError):
            CrossSection({(0, 0): 7})
        for footing in (AIR, "minecraft:cave_air"):
            with self.assertRaisesRegex(NBTError, "air holds nothing"):
                CrossSection({(0, 0): STONE}, footing=footing)
        for footing in (WATER, "minecraft:lava",
                        "minecraft:oak_stairs[waterlogged=true]"):
            with self.subTest(footing=footing):
                with self.assertRaisesRegex(NBTError, "water or lava"):
                    CrossSection({(0, 0): STONE}, footing=footing)
        with self.assertRaisesRegex(NBTError, "Merlon is a tooth"):
            CrossSection({(0, 0): STONE}, footing=Merlon(STONE))
        with self.assertRaisesRegex(NBTError, "pass footing="):
            CrossSection({(0, 0): STONE}, bury=2)
        with self.assertRaises(NBTError):
            CrossSection({(0, 0): STONE}, footing=STONE, bury=-1)
        with self.assertRaisesRegex(TypeError, r"Merlon\(None\)"):
            Merlon(None)
        with self.assertRaisesRegex(NBTError, "no teeth"):
            Merlon(AIR)
        with self.assertRaisesRegex(NBTError, "no cells"):
            CrossSection({})
        with self.assertRaises(NBTError):
            CrossSection({(0.5, 0): STONE})
        # What is kept: a Material as its block, any air as the cut, plain
        # air; a Mix as itself.
        mix = Mix([(STONE, 3), (COBBLE, 1)])
        section = CrossSection({(0, 0): Material("minecraft:bricks"),
                                (0, 1): "minecraft:cave_air", (1, 0): mix,
                                (-2, 0): "minecraft:sea_pickle"})
        self.assertEqual(section.cells[(0, 0)],
                         BlockState.parse("minecraft:bricks"))
        self.assertEqual(section.cells[(0, 1)], BlockState.parse(AIR))
        self.assertIs(section.cells[(1, 0)], mix)
        self.assertEqual((section.reach, section.across), (2, range(-2, 2)))
        self.assertTrue(section.water, "a bare sea pickle is waterlogged")
        self.assertFalse(CrossSection({(0, 0): STONE}).water)
        with self.assertRaises(TypeError):
            section.cells[(5, 5)] = STONE

    def test_a_section_holds_water_but_never_lava_or_a_flow(self):
        """A piece may hold water -- a source, a waterlogged slab, kelp --
        which is a channel. Lava, which no water check follows, and flowing
        water, which the game makes from its sources, are refused as the
        section is made, a Mix's every entry asked; and a merlon of water
        is no tooth."""
        for piece, words in (("minecraft:lava", "is lava"),
                             ("minecraft:water[level=3]", "flowing water"),
                             (Mix([(STONE, 9), ("minecraft:lava", 1)]),
                              "is lava")):
            with self.subTest(piece=piece):
                with self.assertRaisesRegex(NBTError, words) as caught:
                    CrossSection({(0, 0): STONE, (0, 1): piece})
                self.assertIn("(0, 1)", str(caught.exception))
        with self.assertRaisesRegex(NBTError, "tooth of water"):
            Merlon(WATER)
        for piece in (WATER, "minecraft:water",
                      "minecraft:stone_brick_slab[type=bottom,waterlogged=true]",
                      "minecraft:kelp_plant"):
            with self.subTest(piece=piece):
                self.assertTrue(CrossSection({(0, 0): piece}).water)


# --------------------------------------------------------------------------
# The band
# --------------------------------------------------------------------------

class BandTests(unittest.TestCase):
    @staticmethod
    def lines(band):
        found = {}
        for i, a, k, x, z, word, tooth in band.cells:
            found.setdefault(a, []).append((x, z))
        return found

    def test_a_rectangle_miters_exactly(self):
        """A closed 10 by 8 rectangle at reach 1: three nested rings of 44,
        36 and 28 cells, every cell claimed once, the outer ring the
        Chebyshev offset of the centerline's -- square corners, mitered
        exactly -- and the inner ring the centerline's inside one."""
        band = _band(rectangle(0, 0, 10, 8), [-1, 0, 1])
        rings = self.lines(band)
        self.assertEqual({a: len(cells) for a, cells in rings.items()},
                         {-1: 44, 0: 36, 1: 28})
        every = [cell for cells in rings.values() for cell in cells]
        self.assertEqual(len(every), len(set(every)), "a cell claimed twice")

        def ring(x0, z0, x1, z1):
            return {(x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)
                    if x in (x0, x1) or z in (z0, z1)}
        self.assertEqual(set(rings[0]), ring(0, 0, 10, 8))
        self.assertEqual(set(rings[-1]), ring(-1, -1, 11, 9))
        self.assertEqual(set(rings[1]), ring(1, 1, 9, 7))
        self.assertTrue(band.closed)
        self.assertEqual(len(band.line), 36)
        self.assertEqual(len(band.windows), 4)
        # A corner's miter takes the corner's own centerline cell.
        corner = band.line.index((10, 0))
        heights = {(x, z): i for i, a, k, x, z, w, t in band.cells}
        self.assertEqual(heights[(11, -1)], corner)
        self.assertEqual(heights[(10, 0)], corner)

    def test_a_turn_beside_a_diagonal_is_refused(self):
        """A turn from a diagonal run is refused by name -- beside a
        diagonal the band folds or leaves holes -- and so is any turn that
        is not a right angle between straight runs; the same path with
        straight stubs either side of its turn is a band, its runs bending
        within their headings."""
        for path, where in (([(0, 0), (19, 8), (29, 21)], r"\(19, 8\)"),
                            ([(0, 0), (10, 0), (20, 10)], r"\(10, 0\)"),
                            ([(0, 0), (10, 10), (20, 0)], r"\(10, 10\)")):
            with self.subTest(path=path):
                with self.assertRaisesRegex(NBTError, where + ".*right angle"):
                    _band(path, range(-2, 3))
        band = _band([(0, 0), (19, 8), (23, 8), (23, 12), (29, 21)],
                     range(-2, 3))
        cells = [(x, z) for _, _, _, x, z, _, _ in band.cells]
        self.assertEqual(len(cells), len(set(cells)))
        self.assertEqual(band.turns, (((23, 8), band.line.index((23, 8))),))
        # Every column of a run that bends but keeps its heading is the
        # centerline shifted across: five cells, edge to edge.
        for x in range(1, 19):
            column = sorted(z for cx, z in cells if cx == x)
            self.assertEqual(column, list(range(column[0], column[0] + 5)), x)

    def test_folds_u_turns_and_hairpins_are_refused(self):
        """A run shorter than the reach of the turn inside it folds the
        line back on itself; east then west is a turn back; and a hairpin
        whose legs stand closer than the section is wide comes back over
        itself -- each refused, naming where. A closed path is a
        rectangle."""
        with self.assertRaisesRegex(NBTError, r"\(0, 0\) to \(1, 0\).*at "
                                              r"least 2 blocks"):
            _band([(0, 0), (1, 0), (1, 10)], range(-2, 3))
        _band([(0, 0), (2, 0), (2, 10)], range(-2, 3))     # long enough
        with self.assertRaisesRegex(NBTError, r"turns back on itself at "
                                              r"\(10, 0\)"):
            _band([(0, 0), (10, 0), (5, 0)], [0])
        with self.assertRaisesRegex(NBTError, "comes back over itself"):
            _band([(0, 0), (10, 0), (10, 2), (0, 2)], range(-1, 2))
        _band([(0, 0), (10, 0), (10, 3), (0, 3)], range(-1, 2))
        with self.assertRaisesRegex(NBTError, "crosses itself"):
            _band([(0, 0), (10, 0), (10, 5), (5, 5), (5, -5)], [3])
        for path, why in (
                ([(0, 0), (10, 0), (10, 5), (5, 5), (5, 10), (0, 10), (0, 0)],
                 "turns 6 times"),
                ([(0, 0), (10, 0), (10, 10), (0, 10), (0, 2), (0, 0)],
                 None)):
            if why is None:
                _band(path, [0])                  # a waypoint on a side
                continue
            with self.subTest(path=path):
                with self.assertRaisesRegex(NBTError, "rectangle.*" + why):
                    _band(path, [0])

    @staticmethod
    def _profile(rng, n):
        heights, y = [], 70
        for _ in range(n):
            y += rng.choice((-3, -2, -1, 0, 0, 1, 2, 3))
            heights.append(y)
        return heights

    def test_no_two_neighbors_step_more_than_max_step(self):
        """Over a closed rectangle, an open path turning both ways and
        bending within a heading, a staircase of short runs and a single
        diagonal run, at every reach from 1 to 4 and 40 rough profiles
        each: once the turns are leveled, no two cells of the band side by
        side differ by more than one. Unleveled, the rectangle's inner
        corners at reach 3 put a cliff between neighbors."""
        shapes = {
            "rectangle": rectangle(0, 0, 24, 16),
            "open": [(0, 0), (14, 0), (14, 12), (20, 12), (30, 15), (40, 15),
                     (40, 3), (52, 3)],
            "staircase": [(0, 0), (9, 0), (9, 9), (18, 9), (18, 18), (27, 18)],
            "diagonal": [(0, 0), (30, 11)],
        }
        rng = random.Random(20261022)
        cliff = 0
        for name, path in shapes.items():
            for reach in range(1, 5):
                band = _band(path, range(-reach, reach + 1))
                where = {(x, z): i for i, a, k, x, z, w, t in band.cells}
                pairs = [(where[c], where[n]) for c in where
                         for n in _neighbors(*c) if n in where and n > c]
                for trial in range(40):
                    heights = self._profile(rng, len(band.line))
                    smooth = geometry.smoothed_profile(
                        heights, window=rng.randint(0, 3), closed=band.closed)
                    level = _level_turns(smooth, band.windows,
                                         closed=band.closed)
                    with self.subTest(shape=name, reach=reach, trial=trial):
                        worst = max(abs(level[i] - level[j]) for i, j in pairs)
                        self.assertLessEqual(worst, 1)
                        for window in band.windows:
                            self.assertEqual(len({level[i] for i in window}), 1)
                    if name == "rectangle" and reach == 3:
                        cliff = max(cliff, max(abs(smooth[i] - smooth[j])
                                               for i, j in pairs))
        self.assertGreater(cliff, 1, "unleveled, the inner corners are cliffs")

    def test_merlon_runs_match_battlement(self):
        """Over 150 seeded straight-run paths, open and closed, the teeth of
        the band's one line are battlement's own: merlon_offsets per run,
        counted from each run's corners."""
        rng = random.Random(149)
        compared = 0
        while compared < 150:
            if rng.random() < 0.3:
                x0, z0 = rng.randint(-5, 5), rng.randint(-5, 5)
                path = rectangle(x0, z0, x0 + rng.randint(1, 12),
                                 z0 + rng.randint(1, 12))
            else:
                path, (x, z) = [(0, 0)], (0, 0)
                word = rng.choice(geometry.CLOCKWISE)
                for _ in range(rng.randint(1, 6)):
                    dx, dz = geometry.COMPASS[word]
                    length = rng.randint(1, 9)
                    x, z = x + dx * length, z + dz * length
                    path.append((x, z))
                    if rng.random() < 0.2:
                        continue                # straight on through a vertex
                    word = geometry.turn(word, rng.choice((1, -1)))
            try:
                band = _band(path, [0])
            except NBTError:
                continue                        # the path crossed itself
            ours = {(x, z) for _, _, _, x, z, _, tooth in band.cells if tooth}
            theirs = {(x, z) for x, z, merlon in _path_roles(path) if merlon}
            with self.subTest(path=path):
                self.assertEqual(ours, theirs)
            compared += 1

    def test_level_turns_holds_its_pins(self):
        """A pinned turn is laid at its pin; two pins in one turn, or a
        high pin the clamp leaves standing, are named."""
        windows = ((3, 4, 5),)
        got = _level_turns((70, 71, 72, 73, 74, 75, 76, 77), windows,
                           pins={4: 72})
        self.assertEqual(got, (70, 71, 72, 72, 72, 72, 73, 74))
        got = _level_turns((70, 71, 72, 73, 74, 75, 76, 77), windows)
        self.assertEqual(got, (70, 71, 72, 73, 73, 73, 74, 75))
        with self.assertRaisesRegex(NBTError, "anchors at heights"):
            _level_turns((70,) * 8, windows, pins={3: 70, 5: 71})
        with self.assertRaisesRegex(NBTError, "stands 5 over"):
            _level_turns((70,) * 8, (), pins={1: 75})
        self.assertEqual(_level_turns((70,) * 8, (), pins={1: 75},
                                      max_step=None)[1], 75)


# --------------------------------------------------------------------------
# Extruding
# --------------------------------------------------------------------------

class ExtrudeTests(ExtrudeBase):
    def test_the_builder_is_assembled_as_decided(self):
        """The mixin sits after RailwayMixin and before ApproachMixin, in
        the one MRO line every phase-four mixin takes its place in."""
        self.assertEqual(
            [kind.__name__ for kind in Builder.__mro__[1:-1]],
            ["LightingMixin", "SiteMixin", "WaterMixin", "ShellsMixin",
             "WallsMixin", "RoofMixin", "FurnitureMixin", "VaultMixin",
             "StairsMixin", "RailwayMixin", "ExtrudeMixin", "ApproachMixin",
             "PlantingMixin", "MassingMixin", "CrownMixin", "StreetsMixin",
             "CityMixin"])

    def test_flat_battlement_equals_battlement(self):
        """On level ground a section of a course and a Merlon lays what
        battlement lays over the same path, cell for cell -- a closed
        rectangle, one walked from the middle of a side, and an open run
        with a corner -- and reports the same merlon tops."""
        paths = (rectangle(2, 2, 12, 9), [(4, 20), (13, 20), (13, 31),
                                          (2, 31), (2, 20), (4, 20)],
                 [(2, 36), (12, 36), (12, 44)])
        save = self.world()
        b = Builder(save)
        section = CrossSection({(0, 0): STONE, (0, 1): Merlon(COBBLE)})
        for path in paths:
            with self.subTest(path=path):
                result = b.extrude(path, section, follow=62)
                beside = [(x + 20, z) for x, z in path]
                tops = b.battlement(beside, 62, block=STONE, merlon=COBBLE)
                self.assertEqual({(x + 20, y, z) for x, y, z in result.merlons},
                                 set(tops))
                xs = [x for x, _ in path]
                zs = [z for _, z in path]
                for x in range(min(xs) - 1, max(xs) + 2):
                    for z in range(min(zs) - 1, max(zs) + 2):
                        for y in range(61, 65):
                            self.assertEqual(self.at(x, y, z),
                                             self.at(x + 20, y, z),
                                             (x, y, z))

    def test_unreadable_ground_is_refused_not_assumed(self):
        """A run into a chunk that was never generated raises NoSurface for
        every column it cannot read, the first in write order its cell, and
        writes nothing. The lab's _wall_tops planned those columns at the
        plane plus seven, 73, as if it had read them."""
        save = self.world(chunks=[(0, 0), (1, 0)])
        b = Builder(save)
        section = CrossSection({(0, 0): STONE, (0, 1): STONE}, footing=STONE)
        with self.assertRaises(NoSurface) as caught:
            b.extrude([(20, 5), (40, 5)], section)
        exc = caught.exception
        self.assertEqual(exc.cell, (32, 5))
        self.assertEqual(exc.reason, "ungenerated")
        self.assertEqual([fault.cell for fault in exc.faults],
                         [(x, 5) for x in range(32, 41)])
        self.assertIn("9 columns", str(exc))
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(self.at(20, 62, 5), AIR)
        # With a footing to found at one level, the same.
        with self.assertRaises(NoSurface):
            b.extrude([(20, 5), (40, 5)], section, follow=64)
        self.assertEqual(b.blocks_set, 0)

        wall_tops = _tool_function(
            os.path.join("secret_lab", "m1_groundwork.py"), "_wall_tops",
            P=types.SimpleNamespace(PLANE=66))
        tops = wall_tops(b, [(x, 5) for x in range(20, 41)],
                         [(x, 6) for x in range(20, 41)])
        self.assertEqual(tops[12:], [73] * 9, "the plane, assumed")

    def test_logs_lie_along_each_run(self):
        """States are drawn as they stand on a run heading south and turned
        for each run, defaults and all: a log drawn axis=z lies along every
        run, one drawn axis=x across it, and a stair drawn bare -- facing
        north, back up a south run -- faces back up every run."""
        save = self.world()
        b = Builder(save)
        section = CrossSection({(0, 0): "minecraft:spruce_log[axis=z]",
                                (0, 1): "minecraft:oak_log[axis=x]",
                                (0, 2): "minecraft:stone_brick_stairs"})
        b.extrude([(2, 2), (14, 2), (14, 14)], section, follow=62)
        logs = Mix([("minecraft:spruce_log[axis=z]", 1),
                    ("minecraft:birch_log[axis=z]", 1)])
        b.extrude([(2, 20), (14, 20)], CrossSection({(0, 0): logs}),
                  follow=62)
        for x in range(2, 15):
            picked = BlockState.parse(logs.pick(x, 62, 20))
            self.assertEqual(self.at(x, 62, 20),
                             str(picked.with_properties(axis="x")),
                             "picked at the world cell, then turned")

        def prop(x, y, z, key):
            return self.save.get_block(x, y, z).canonical().properties[key]
        for x in range(3, 14):
            self.assertEqual(prop(x, 62, 2, "axis"), "x")
            self.assertEqual(prop(x, 63, 2, "axis"), "z")
            self.assertEqual(prop(x, 64, 2, "facing"), "west")
        for z in range(3, 15):
            self.assertEqual(prop(14, 62, z, "axis"), "z")
            self.assertEqual(prop(14, 63, z, "axis"), "x")
            self.assertEqual(prop(14, 64, z, "facing"), "north")

    def test_a_turn_off_a_right_angle_is_refused_before_anything_is_read(self):
        save = self.world()
        b = Builder(save)
        for path in ([(2, 2), (12, 2), (22, 12)], [(2, 2), (12, 2), (2, 2)]):
            with self.subTest(path=path):
                with self.assertRaises(NBTError):
                    b.extrude(path, CrossSection({(0, 0): STONE}))
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(b._probes, {}, "nothing was read")

    def test_follow_is_a_height_a_profile_or_the_terrain(self):
        """follow=y lays it level; a sequence is one height per centerline
        cell, level through every turn, or refused; window, max_step and
        anchors are terrain's alone; a closed path's profile has a height
        per cell of its loop."""
        save = self.world()
        b = Builder(save)
        section = CrossSection({(0, 0): STONE})
        level = b.extrude([(2, 2), (8, 2)], section, follow=64)
        self.assertEqual(level.profile, (64,) * 7)
        self.assertEqual([self.name(x, 64, 2) for x in range(2, 9)],
                         [STONE] * 7)
        given = b.extrude([(2, 4), (8, 4)], section,
                          follow=[62, 62, 63, 63, 64, 64, 65])
        self.assertEqual([self.name(x, y, 4) for x, y in
                          zip(range(2, 9), given.profile)], [STONE] * 7)
        with self.assertRaisesRegex(NBTError, "3 heights for a centerline "
                                              "of 7"):
            b.extrude([(2, 6), (8, 6)], section, follow=[62, 62, 62])
        with self.assertRaisesRegex(NBTError, "not an int"):
            b.extrude([(2, 6), (8, 6)], section, follow=[62.0] * 7)
        corner = [(2, 10), (8, 10), (8, 16)]
        wide = CrossSection({(-1, 0): STONE, (0, 0): STONE, (1, 0): STONE})
        ramp = list(range(60, 73))
        with self.assertRaisesRegex(NBTError, r"not level through the turn "
                                              r"round \(8, 10\)"):
            b.extrude(corner, wide, follow=ramp)
        # One cell wide there is no inner line to meet the outer one, and a
        # ramp through the corner is laid as given.
        b.extrude(corner, section, follow=ramp)
        turned = [60, 61, 62, 63, 64, 65, 65, 65, 66, 67, 68, 69, 70]
        flat = b.extrude([(x + 20, z) for x, z in corner], wide, follow=turned)
        self.assertEqual(flat.profile, tuple(turned))
        for kwargs in ({"window": 5}, {"max_step": None},
                       {"anchors": {(2, 6): 62}}):
            with self.subTest(**kwargs):
                with self.assertRaisesRegex(NBTError, "terrain"):
                    b.extrude([(2, 6), (8, 6)], section, follow=62, **kwargs)
        for follow in ("level", True, None):
            with self.subTest(follow=follow):
                with self.assertRaises(NBTError):
                    b.extrude([(2, 6), (8, 6)], section, follow=follow)
        loop = b.extrude(rectangle(20, 20, 26, 24), section, follow=63)
        self.assertEqual(len(loop.profile), 2 * (6 + 4))
        self.assertEqual(loop.line[0], (20, 20))

    def test_taken_cells_refuse_unless_overwrite(self):
        """Air, ground cover, natural ground, an ore and a tree are taken
        freely; oak planks somebody laid are refused, every one named and
        nothing written, until overwrite=True."""
        def shape(save):
            save.set_block(6, 62, 5, "minecraft:stone")
            save.set_block(7, 62, 5, "minecraft:iron_ore")
            save.set_block(8, 62, 5, "minecraft:snow[layers=1]")
            save.set_block(9, 62, 5, "minecraft:oak_log[axis=y]")
            save.set_block(11, 62, 5, PLANKS)
            save.set_block(13, 63, 5, PLANKS)
        save = self.world(shape)
        b = Builder(save)
        section = CrossSection({(0, 0): STONE, (0, 1): STONE, (0, 2): STONE})
        with self.assertRaises(Refused) as caught:
            b.extrude([(5, 5), (15, 5)], section, follow=61)
        self.assertEqual(len(caught.exception.faults), 2)
        self.assertIn("(11, 62, 5) holds minecraft:oak_planks",
                      str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(self.name(6, 62, 5), "minecraft:stone")
        result = b.extrude([(5, 5), (15, 5)], section, follow=61,
                           overwrite=True)
        for x in range(5, 16):
            self.assertEqual([self.name(x, y, 5) for y in (61, 62, 63)],
                             [STONE] * 3, x)
        self.assertEqual(result.blocks, 33)

    def test_keep_outs_and_through_span_without_footing(self):
        """A band entering a reservation is refused; named in through= the
        section crosses it -- spanning it, with no footing and no pier
        founded inside it."""
        save = self.world()
        b = Builder(save)
        b.reserve(9, 0, 11, 15, name="lane")
        deck = {(0, 0): STONE, (0, 1): STONE}
        section = CrossSection(deck, footing=COBBLE, bury=2)
        with self.assertRaisesRegex(Refused, "reservation 'lane'"):
            b.extrude([(4, 5), (16, 5)], section, follow=64)
        self.assertEqual(b.blocks_set, 0)
        b.extrude([(4, 5), (16, 5)], section, follow=64, through=("lane",))
        self.assertEqual(b.known_ground(5, 5), 65, "founded: the deck's top")
        self.assertIsNone(b.known_ground(10, 5), "spanned: never read")
        for x in range(4, 17):
            inside = 9 <= x <= 11
            self.assertEqual(self.name(x, 64, 5), STONE)
            self.assertEqual([self.name(x, y, 5) for y in (60, 61, 63)],
                             [DIRT, "minecraft:grass_block", AIR] if inside
                             else [COBBLE] * 3, x)
        pier = CrossSection(deck, footing=COBBLE)
        b.extrude([(4, 9), (16, 9)], CrossSection(deck), follow=64, pier=pier,
                  pitch=2, phase=0, through=("lane",))
        self.assertEqual([x for x in range(4, 17)
                          if self.name(x, 63, 9) == COBBLE],
                         [4, 6, 8, 12, 14, 16])

    def test_piers_by_congruence(self):
        """An aqueduct's deck carried level at 70 heading north, a pier
        section at pitch 6, two wide from phase 0: exactly the columns with
        z % 6 in (0, 1) are founded from the ground, and the rest stand on
        air under the soffit."""
        save = self.world()
        b = Builder(save)
        deck = {(-1, 0): STONE, (0, 0): STONE, (1, 0): STONE,
                (-1, 1): STONE, (1, 1): STONE}
        pier = CrossSection({**deck, (0, -1): COBBLE, (0, -2): COBBLE},
                            footing=COBBLE)
        result = b.extrude([(20, 40), (20, 5)], CrossSection(deck), follow=70,
                           pier=pier, pitch=6, pier_width=2, phase=0)
        self.assertEqual(result.profile, (70,) * 36)
        for z in range(5, 41):
            founded = z % 6 in (0, 1)
            with self.subTest(z=z):
                self.assertEqual(self.name(20, 70, z), STONE)
                self.assertEqual({self.name(20, y, z) for y in range(62, 70)},
                                 {COBBLE} if founded else {AIR})
                self.assertEqual(self.name(21, 66, z),
                                 COBBLE if founded else AIR)
        with self.assertRaisesRegex(NBTError, "pitch"):
            b.extrude([(30, 40), (30, 5)], CrossSection(deck), follow=70,
                      pier=pier)
        with self.assertRaisesRegex(NBTError, "no span"):
            b.extrude([(30, 40), (30, 5)], CrossSection(deck), follow=70,
                      pier=pier, pitch=2, pier_width=2)
        with self.assertRaisesRegex(NBTError, "place a pier"):
            b.extrude([(30, 40), (30, 5)], CrossSection(deck), follow=70,
                      pitch=6)

    def _walled(self, *, lip: bool):
        cells = {(-1, 0): STONE, (0, 0): STONE, (1, 0): STONE,
                 (-1, 1): STONE, (-1, 2): STONE}
        for up in (-1, -2):
            for a in (-1, 0, 1):
                cells[(a, up)] = STONE
        if lip:
            cells[(1, 1)] = SLAB
        return CrossSection(cells, walk=[(0, 0)])

    def test_walk_round_trip(self):
        """A wall walk over a profile rising a block at a time, its parapet
        on the left and a slab lip on the right: the walk check walks every
        walk cell and back, and with the lip it finds no rise it must jump
        -- it stands a body on a bottom slab at the top of its cell, as on a
        stair's back, so each rise is two half steps up the lip. Without
        the lip every cell past the first rise needs a jump, reported in
        reach.jumped; a rise of two is refused, naming the walk cells it
        cuts off, and nothing is written."""
        save = self.world()
        profile = [70] * 6 + [71] * 6 + [72] * 6 + [71] * 6
        b = Builder(save)
        result = b.extrude([(2, 10), (25, 10)], self._walled(lip=True),
                           follow=profile)
        self.assertIsInstance(result, ExtrudeResult)
        self.assertEqual(result.walk, tuple((x, y, 10) for x, y in
                                            zip(range(2, 26), profile)))
        self.assertFalse(result.reach.stranded)
        self.assertFalse(set(result.walk) & result.reach.jumped)
        self.assertTrue(set(result.walk) <= result.reach.reached)

        result = b.extrude([(2, 20), (25, 20)], self._walled(lip=False),
                           follow=profile)
        self.assertFalse(result.reach.stranded)
        jumped = set(result.walk) & result.reach.jumped
        self.assertEqual(sorted(jumped), [(x, profile[x - 2], 20)
                                          for x in range(8, 26)])

        steep = [70] * 12 + [72] * 12
        before = b.blocks_set
        with self.assertRaises(Refused) as caught:
            b.extrude([(2, 30), (25, 30)], self._walled(lip=True),
                      follow=steep)
        self.assertEqual(caught.exception.faults,
                         [(x, 72, 30) for x in range(14, 26)])
        self.assertEqual(b.blocks_set, before)

    def test_the_walk_is_walked_on_the_wall_not_round_it(self):
        """A rise of two in the walk of an L-shaped wall, with ground inside
        the L a block over the low walk and a block under the high one: a
        walker free to step off the wall would jump down onto that ground
        and back up past the rise. The walk is walked on the band's own
        columns, so the rise is refused."""
        def plateau(save):
            save.fill((4, 62, 5), (19, 62, 20), DIRT)
            save.fill((4, 63, 5), (19, 63, 20), GRASS)
        save = self.world(plateau)
        b = Builder(save)
        section = CrossSection({(0, 0): STONE}, walk=[(0, 0)])
        with self.assertRaises(Refused) as caught:
            b.extrude([(4, 4), (20, 4), (20, 20)], section,
                      follow=[62] * 17 + [64] * 16)
        self.assertEqual(caught.exception.faults,
                         [(20, 64, z) for z in range(5, 21)])
        self.assertEqual(b.blocks_set, 0)

    def test_a_flights_or_an_approachs_claim_is_refused(self):
        """A block in the treads or head room of an approach this session
        laid is refused, overwrite or not, the refusal naming whose claim
        it is."""
        save = self.world()
        b = Builder(save)
        b.approach((5, 61, 3), (5, 61, 10), kind="steps", material=STONE)
        section = CrossSection({(0, 0): STONE, (0, 1): STONE})
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                with self.assertRaisesRegex(Refused, "an approach") as caught:
                    b.extrude([(2, 7), (10, 7)], section, follow=61,
                              overwrite=overwrite)
                self.assertEqual(caught.exception.faults,
                                 [(5, 61, 7), (5, 62, 7)])

    def test_a_step_down_needs_three_cells_of_head_room(self):
        """A stone three over the low walk cell at a step down: the walker
        coming down has their head in it, so the walk is refused -- the
        third cell of HEAD_ROOM, which the section leaves to the check."""
        save = self.world()
        b = Builder(save)
        cells = {(0, 0): STONE, (0, -1): STONE, (0, 3): STONE}
        section = CrossSection(cells, walk=[(0, 0)])
        with self.assertRaises(Refused) as caught:
            b.extrude([(2, 10), (9, 10)], section,
                      follow=[71, 71, 71, 71, 70, 70, 70, 70])
        self.assertIn((6, 70, 10), caught.exception.faults)
        b.extrude([(2, 12), (9, 12)], section, follow=[70] * 8)

    def test_a_one_wide_diagonal_walk_is_refused(self):
        """A walk one cell wide along a diagonal touches itself only at the
        corners where it steps across, and a body walks no corner: refused.
        Two wide, its cells meet edge to edge and it walks."""
        save = self.world()
        b = Builder(save)
        narrow = CrossSection({(0, 0): STONE, (0, -1): STONE},
                              walk=[(0, 0)])
        with self.assertRaisesRegex(Refused, "does not walk both ways"):
            b.extrude([(2, 2), (22, 12)], narrow, follow=70)
        self.assertEqual(b.blocks_set, 0)
        wide = CrossSection({(0, 0): STONE, (1, 0): STONE}, walk=[(0, 0), (1, 0)])
        result = b.extrude([(2, 2), (22, 12)], wide, follow=70)
        self.assertFalse(result.reach.stranded)
        self.assertEqual(len(result.walk), 42)

    def test_a_cut_is_written_only_where_something_stands(self):
        """A cut empties a cell where something stands -- grass, a flower
        -- and leaves any of the game's airs as it is, cave air included,
        counting it as already as wanted."""
        def shape(save):
            save.set_block(4, 62, 2, "minecraft:poppy")
            save.set_block(6, 62, 2, "minecraft:cave_air")
        save = self.world(shape)
        b = Builder(save)
        section = CrossSection({(0, 0): AIR, (0, 1): AIR})
        result = b.extrude([(2, 2), (8, 2)], section)
        self.assertEqual(self.name(4, 62, 2), AIR)
        self.assertEqual(self.name(6, 62, 2), "minecraft:cave_air")
        self.assertEqual(self.name(5, 61, 2), AIR)
        self.assertEqual((result.blocks, result.already), (8, 6))

    def test_the_audit_runs_over_the_plan(self):
        """A torch over a cut stands on nothing: refused by the survival
        audit, and nothing written."""
        save = self.world()
        b = Builder(save)
        section = CrossSection({(0, 0): AIR, (0, 1): "minecraft:torch"})
        with self.assertRaisesRegex(Refused, "will not keep"):
            b.extrude([(2, 2), (8, 2)], section)
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(self.name(4, 61, 2), "minecraft:grass_block")

    def test_a_cut_beside_water_and_water_under_the_band_are_refused(self):
        """A cut that opens a pond's bank lets the water out, and is
        refused; a section over the pond is refused until over_water says
        what to do, and over_water='fill' needs a footing to fill with."""
        def pond(save):
            save.fill((10, 58, 10), (14, 61, 14), WATER)
        save = self.world(pond)
        b = Builder(save)
        ditch = CrossSection({(0, 0): AIR, (0, -1): AIR})
        with self.assertRaisesRegex(Refused, "let water out"):
            b.extrude([(5, 9), (20, 9)], ditch)
        self.assertEqual(b.blocks_set, 0)
        wall = CrossSection({(0, 0): STONE, (0, 1): STONE})
        with self.assertRaisesRegex(Refused, "over water"):
            b.extrude([(5, 12), (20, 12)], wall)
        with self.assertRaisesRegex(NBTError, "footing"):
            b.extrude([(5, 12), (20, 12)], wall, over_water="fill")
        bridged = b.extrude([(5, 12), (20, 12)], wall, over_water="bridge",
                            follow=62)
        self.assertEqual(self.name(12, 61, 12), "minecraft:water")
        self.assertEqual(self.name(12, 62, 12), STONE)
        self.assertEqual(bridged.profile, (62,) * 16)

    def test_water_a_bridge_takes_or_a_fill_leaves_is_refused(self):
        """Over water, over_water='fill' fills each column from its bed with
        the footing, and a column it would leave water under -- a span
        between piers with none -- is refused, and so is a fill across a
        canal, which cuts its water in two. A section bridged at the
        waterline takes the water it stands in, and where that water feeds
        a flow, the flow would dry: refused."""
        def pond(save):
            save.fill((10, 58, 10), (14, 61, 14), WATER)
            save.set_block(10, 61, 30, WATER)
            for k, x in enumerate(range(11, 14), start=1):
                save.set_block(x, 61, 30, f"minecraft:water[level={k}]")
            save.fill((30, 58, 2), (32, 61, 40), WATER)        # a canal
        save = self.world(pond)
        b = Builder(save)
        deck = CrossSection({(0, 0): STONE})
        pier = CrossSection({(0, 0): STONE}, footing=COBBLE)
        with self.assertRaisesRegex(NBTError, r"\(10, 12\) gets none"):
            b.extrude([(5, 12), (20, 12)], deck, pier=pier, pitch=6,
                      phase=0, over_water="fill")
        with self.assertRaisesRegex(Refused, "cut flowing water off"):
            b.extrude([(5, 30), (10, 30)], deck, over_water="bridge")
        # Filled across, a canal is two ponds.
        with self.assertRaisesRegex(Refused, "a body of water in two"):
            b.extrude([(26, 20), (36, 20)], pier, over_water="fill")
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(self.name(10, 61, 30), "minecraft:water")

    def test_lava_beside_a_cut_is_refused_whatever_overwrite_says(self):
        """A cut that opens a cell beside lava lets the lava in, and lava
        written over is lava still there: refused, overwrite or not, and
        nothing written."""
        def lava(save):
            save.set_block(10, 61, 6, "minecraft:lava[level=0]")
        save = self.world(lava)
        b = Builder(save)
        ditch = CrossSection({(0, 0): AIR})
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                with self.assertRaisesRegex(Refused, "lava") as caught:
                    b.extrude([(5, 5), (15, 5)], ditch, overwrite=overwrite)
                self.assertIn("(10, 61, 5), which the extrusion opens",
                              str(caught.exception))
                with self.assertRaisesRegex(Refused, "lava"):
                    b.extrude([(5, 6), (15, 6)], CrossSection({(0, 0): STONE}),
                              follow=61, overwrite=overwrite)
        self.assertEqual(b.blocks_set, 0)

    def test_writes_are_total_and_repeatable(self):
        """The same call on two fresh saves writes the same region bytes;
        made again on the same site at the profile it was laid at, it
        writes nothing, finds every cell it names already as it wants, and
        leaves the journal as it was. (Following the terrain again, it
        would read its own top as the ground, as a later session reads the
        game's heightmap.)"""
        def build():
            save = self.world()
            b = Builder(save)
            section = CrossSection(
                {(-1, 0): STONE, (0, 0): Mix([(STONE, 2), (COBBLE, 1)]),
                 (1, 0): STONE, (-1, 1): Merlon(COBBLE), (0, 1): AIR,
                 (0, 2): AIR}, footing=COBBLE, bury=1)
            path = rectangle(4, 4, 20, 14)
            result = b.extrude(path, section)
            save.save()
            region = os.path.join(self.directory, "dimensions", "minecraft",
                                  "overworld", "region", "r.0.0.mca")
            with open(region, "rb") as handle:
                return b, section, path, result, handle.read()
        b, section, path, first, data = build()
        _, _, _, _, again = build()
        self.assertEqual(data, again)
        self.assertGreater(first.blocks, 0)
        total = b.blocks_set
        second = b.extrude(path, section, follow=first.profile)
        self.assertEqual(second.blocks, 0)
        self.assertEqual(second.already, first.blocks + first.already)
        self.assertEqual(b.blocks_set, total)
        self.assertEqual(second.merlons, first.merlons)

    def test_a_course_finish_has_joined_is_already_laid(self):
        """A fence course joined up by finish() differs from the plan only
        in what a block update derives, so laid again at its profile it
        is already there, cell for cell."""
        save = self.world()
        b = Builder(save)
        section = CrossSection({(0, 0): STONE, (0, 1): "minecraft:oak_fence"})
        first = b.extrude([(2, 5), (10, 5)], section)
        b.finish()
        self.assertEqual(save.get_block(5, 62, 5).properties.get("east"),
                         "true", "finish joined the course")
        total = b.blocks_set
        again = b.extrude([(2, 5), (10, 5)], section, follow=first.profile)
        self.assertEqual((again.blocks, again.already),
                         (0, first.blocks + first.already))
        self.assertEqual(b.blocks_set, total)

    def test_the_write_order_is_along_then_across_then_up(self):
        """The plan is written by the centerline cell each column takes its
        height from, then by its across value, each column bottom up --
        the order a section's palette, and so the chunk's bytes, takes."""
        save = self.world()
        b = Builder(save)
        order = []
        write = b._write_set

        def recording(x, y, z, state):
            order.append((x, y, z))
            return write(x, y, z, state)
        b._write_set = recording
        section = CrossSection({(-1, 0): STONE, (0, 0): COBBLE,
                                (1, 0): STONE, (0, 1): COBBLE})
        b.extrude([(4, 4), (12, 4)], section, follow=62)
        # Heading east, + across is south.
        expected = [(x, 62 + up, 4 + a) for x in range(4, 13)
                    for a, ups in ((-1, (0,)), (0, (0, 1)), (1, (0,)))
                    for up in ups]
        self.assertEqual(order, expected)

    def test_the_ground_left_is_what_the_column_reader_reads(self):
        """Each column's ground, once laid, is what the column reader finds
        there with the plan laid -- the same as a fresh read once the
        game's maps are rebuilt over it: a ditch's floor (the probe read
        there first, corrected), the grass over a course laid under it,
        the course under a hedge's leaves or a torch, a wall's top. A
        road laid over the buried course leaves it standing."""
        save = self.world()
        b = Builder(save)
        self.assertEqual(b.ground(10, 5), 61)        # read, and cached, first
        leaves = "minecraft:oak_leaves[persistent=true]"
        rows = {
            5: (CrossSection({(0, 0): AIR, (0, -1): AIR, (0, -2): AIR}), 58),
            8: (CrossSection({(0, -1): STONE, (0, -2): STONE}), 61),
            11: (CrossSection({(0, 1): leaves, (0, 2): leaves}), 61),
            14: (CrossSection({(0, 0): STONE, (0, 1): "minecraft:torch"}), 61),
            17: (CrossSection({(0, 0): STONE, (0, 1): STONE, (0, 2): STONE}),
                 63),
        }
        for z, (section, _) in rows.items():
            b.extrude([(5, z), (15, z)], section)
        save.rebuild_heightmaps((0, 0, 0), (47, 0, 47))
        fresh = Builder(save)
        for z, (_, y) in rows.items():
            for x in range(5, 16):
                with self.subTest(column=(x, z)):
                    self.assertEqual(b.ground(x, z), y)
                    self.assertEqual(fresh.ground(x, z), y)
        b.road([(10, 7), (10, 9)], width=1, mix="minecraft:gravel")
        self.assertEqual(self.name(10, 61, 8), "minecraft:gravel")
        self.assertEqual([self.name(10, y, 8) for y in (59, 60)], [STONE] * 2)

    def test_a_section_laid_at_a_height_meets_water_where_it_reaches_it(self):
        """Laid at a height given, a section reaching down to a pond's
        waterline is refused, as water, until over_water says what to do,
        and bridged it stands on the water; a deck carried over the pond,
        its lowest block over the waterline, leaves the pond as it is with
        no over_water at all. Water lying under dry ground, which no
        over_water reaches, is refused as water too."""
        def pond(save):
            save.fill((10, 58, 10), (14, 61, 14), WATER)
            save.fill((20, 58, 30), (30, 58, 32), WATER)      # under the lawn
        save = self.world(pond)
        b = Builder(save)
        wall = CrossSection({(0, 0): STONE, (0, 1): STONE})
        for follow in (61, [61] * 16):
            with self.subTest(follow=follow):
                with self.assertRaisesRegex(Refused, r"over water or ice at "
                                                     r"\(10, 11\) \(5 columns\)"):
                    b.extrude([(5, 11), (20, 11)], wall, follow=follow)
        self.assertEqual(b.blocks_set, 0)
        b.extrude([(5, 11), (20, 11)], wall, follow=61, over_water="bridge")
        self.assertEqual([self.name(12, y, 11) for y in range(58, 63)],
                         ["minecraft:water"] * 3 + [STONE] * 2)
        b.extrude([(5, 13), (20, 13)], CrossSection({(0, 0): STONE}),
                  follow=62)
        self.assertEqual([self.name(12, y, 13) for y in range(58, 63)],
                         ["minecraft:water"] * 4 + [STONE])
        deep = CrossSection({(0, up): STONE for up in range(-3, 1)})
        before = b.blocks_set
        with self.assertRaisesRegex(Refused, "water or ice that over_water "
                                             "does not reach") as caught:
            b.extrude([(22, 31), (28, 31)], deep, follow=61)
        self.assertEqual(caught.exception.faults[0],
                         "(22, 58, 31) holds minecraft:water[level=0]")
        self.assertEqual(b.blocks_set, before)

    def test_a_deck_at_a_height_needs_no_ground_under_it(self):
        """Laid at a height, a deck over a shaft open to the world's floor
        -- a column with no ground at all -- spans it: only the terrain
        and a footing need the ground. Into a chunk never generated it is
        refused as a write there, not as ground nobody read."""
        def shaft(save):
            save.fill((10, -64, 5), (10, 61, 5), AIR)
        save = self.world(shaft, chunks=[(0, 0), (1, 0)])
        b = Builder(save)
        with self.assertRaises(NoSurface):
            b.probe(10, 5)
        deck = CrossSection({(0, 0): STONE})
        b.extrude([(5, 5), (15, 5)], deck, follow=64)
        self.assertEqual([self.name(10, y, 5) for y in (63, 64)], [AIR, STONE])
        with self.assertRaises(Refused) as caught:
            b.extrude([(20, 9), (40, 9)], deck, follow=64)
        self.assertNotIsInstance(caught.exception, NoSurface)
        self.assertIn("(2, 0)", str(caught.exception))
        with self.assertRaises(NoSurface):
            b.extrude([(20, 9), (40, 9)], CrossSection({(0, 0): STONE},
                                                       footing=COBBLE),
                      follow=64)

    def test_a_wide_pier_reaches_past_its_deck_only_at_a_pier(self):
        """A pier wider than its deck lays its outer lines only at a pier,
        and the band is judged on what it lays: a reservation beside the
        deck between piers is in nobody's way, one a pier would enter is;
        and a run ending beside another, where only the pier's outer
        lines would meet it, comes back over nothing -- unless a pier
        stands there."""
        save = self.world()
        b = Builder(save)
        deck = CrossSection({(-1, 0): STONE, (0, 0): STONE, (1, 0): STONE})
        pier = CrossSection({(a, 0): STONE for a in range(-2, 3)},
                            footing=COBBLE)
        b.reserve(22, 14, 23, 16, name="shed")
        b.extrude([(20, 40), (20, 5)], deck, follow=70, pier=pier, pitch=6,
                  phase=0)
        self.assertEqual(self.name(22, 70, 18), STONE, "a pier")
        self.assertEqual(self.name(22, 70, 15), AIR, "between piers")
        b.reserve(32, 17, 33, 19, name="kiln")
        with self.assertRaisesRegex(Refused, r"reservation 'kiln' at "
                                             r"\(32, 18\) \(1 column\)"):
            b.extrude([(30, 40), (30, 5)], deck, follow=70, pier=pier,
                      pitch=6, phase=0)

        path = [(2, 2), (22, 2), (22, 12), (12, 12), (12, 4)]
        narrow = CrossSection({(0, 0): STONE})
        with self.assertRaisesRegex(NBTError, "comes back over itself"):
            _band(path, range(-2, 3))
        b = Builder(self.world())
        with self.assertRaisesRegex(NBTError, r"comes back over itself at "
                                              r"\(12, 4\)"):
            b.extrude(path, narrow, follow=70, pier=pier, pitch=6, phase=0)
        b.extrude(path, narrow, follow=70, pier=pier, pitch=6, phase=1)
        self.assertEqual([self.name(x, 70, 4) for x in (12, 13)], [STONE] * 2)

    def test_a_walk_cut_off_from_its_start_is_refused(self):
        """A walk dropping two from its first cell reaches the cells under
        the drop and cannot climb back to it: walked to but not home from,
        each is named, and nothing is written."""
        save = self.world()
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.extrude([(2, 30), (25, 30)], self._walled(lip=True),
                      follow=[72] * 12 + [70] * 12)
        self.assertIn("one-way", str(caught.exception))
        self.assertEqual(caught.exception.faults,
                         [(x, 70, 30) for x in range(14, 26)])
        self.assertEqual(b.blocks_set, 0)

    def test_a_bridge_founds_on_the_waterline_and_buries_no_water(self):
        """Bridged, a footed column stands on the waterline, its footing
        from there up and burying none of the water under it, while a dry
        column's footing is buried in the ground as asked."""
        def pond(save):
            save.fill((10, 58, 10), (14, 61, 14), WATER)
        save = self.world(pond)
        b = Builder(save)
        section = CrossSection({(0, 0): STONE, (0, 1): STONE}, footing=COBBLE,
                               bury=2)
        b.extrude([(5, 12), (20, 12)], section, over_water="bridge",
                  follow=63)
        for x in range(5, 21):
            column = [self.name(x, y, 12) for y in range(58, 65)]
            with self.subTest(x=x):
                if 10 <= x <= 14:
                    self.assertEqual(column, ["minecraft:water"] * 4
                                     + [COBBLE, STONE, STONE])
                else:
                    self.assertEqual(column, [DIRT] * 2 + [COBBLE] * 3
                                     + [STONE] * 2)

    def test_a_bridge_that_dams_a_canal_is_refused(self):
        """Following the terrain across a canal one block deep, bridged, the
        section stands in the canal's water and cuts it in two: refused.
        Carried a block over the water it spans the canal, which flows on
        under it."""
        def canal(save):
            save.fill((30, 61, 0), (32, 61, 47), WATER)
        save = self.world(canal)
        b = Builder(save)
        wall = CrossSection({(0, 0): STONE, (0, 1): STONE})
        with self.assertRaisesRegex(Refused, "a body of water in two"):
            b.extrude([(26, 20), (36, 20)], wall, over_water="bridge")
        self.assertEqual(b.blocks_set, 0)
        b.extrude([(26, 20), (36, 20)], wall, follow=62)
        self.assertEqual([self.name(31, y, 20) for y in (61, 62)],
                         ["minecraft:water", STONE])

    def test_every_block_is_named_and_turned_before_anything_is_read(self):
        """A block the game does not have is refused before a column is
        read, and so is a state no run's heading can turn -- a modded one
        passed by allow= -- even one a Mix might never pick. Heading south,
        nothing is turned, and it is laid as drawn."""
        from heaton_voxel.craft.errors import IllegalBlock
        save = self.world()
        b = Builder(save)
        with self.assertRaises(IllegalBlock):
            b.extrude([(2, 5), (12, 5)], CrossSection({(0, 0): "minecraft:chain"}))
        self.assertEqual(b._probes, {}, "nothing was read")
        odd = "mymod:thing[toward=north]"
        modded = Builder(save, allow=("mymod:",))
        mix = Mix([(STONE, 50), (odd, 1)])
        self.assertEqual({mix.pick(x, 62, 5) for x in range(2, 5)}, {STONE})
        for piece, end in ((odd, 12), (mix, 4)):
            with self.subTest(piece=piece):
                with self.assertRaisesRegex(Refused, "cannot be turned"):
                    modded.extrude([(2, 5), (end, 5)],
                                   CrossSection({(0, 0): piece}), follow=62)
                self.assertEqual(modded._probes, {}, "nothing was read")
        self.assertEqual(modded.blocks_set, 0)
        modded.extrude([(2, 8), (2, 14)], CrossSection({(0, 0): odd}),
                       follow=62)
        self.assertEqual(self.at(2, 62, 10), odd)

    def test_the_defaults_are_the_documented_ones(self):
        """Left out, phase is the first cell's coordinate along its run, so
        the first pier stands on the start, and window is road's
        ROAD_WINDOW; and a closed path's profile is smoothed round the
        loop, its seam with it."""
        save = self.world()
        b = Builder(save)
        b.extrude([(5, 3), (5, 20)], CrossSection({(0, 0): STONE}), follow=70,
                  pier=CrossSection({(0, 0): STONE}, footing=COBBLE), pitch=4)
        self.assertEqual([z for z in range(3, 21)
                          if self.name(5, 69, z) == COBBLE], [3, 7, 11, 15, 19])

        rng = random.Random(5)
        heights = {x: rng.randint(58, 66) for x in range(2, 40)}

        def rough(save):
            for x, top in heights.items():
                ExtrudeTerrainTests.column_range(save, x, 10, 10, top)
        section = CrossSection({(0, 0): STONE}, footing=COBBLE)
        profiles = {}
        for window in (None, geometry.ROAD_WINDOW, 0):
            kwargs = {} if window is None else {"window": window}
            profiles[window] = Builder(self.world(rough)).extrude(
                [(2, 10), (39, 10)], section, **kwargs).profile
        self.assertEqual(profiles[None], profiles[geometry.ROAD_WINDOW])
        self.assertNotEqual(profiles[None], profiles[0])

        def seam(save):
            for x in range(5, 9):
                ExtrudeTerrainTests.column_range(save, x, 4, 4, 65)
        path = rectangle(4, 4, 20, 14)
        ground = Builder(self.world(seam))
        sample = [ground.ground(x, z) for x, z in _band(path, [0]).line]
        loop = Builder(self.world(seam)).extrude(path, section)
        closed = geometry.smoothed_profile(sample, window=geometry.ROAD_WINDOW,
                                           closed=True)
        self.assertEqual(loop.profile, closed)
        self.assertNotEqual(closed, geometry.smoothed_profile(
            sample, window=geometry.ROAD_WINDOW))


class ExtrudeTerrainTests(ExtrudeBase):
    def slope(self, save, *, every=2, start=6, z0=0, z1=47):
        """Ground rising a block every ``every`` cells east of ``start``."""
        for x in range(start, 48, every):
            self.column_range(save, x, z0, z1, 61 + (x - start) // every,
                              x1=min(47, x + every - 1))

    @staticmethod
    def column_range(save, x, z0, z1, top, *, x1=None):
        """The lawn's ground over columns ``x..x1``, ``z0..z1`` moved to
        ``top``: dirt under grass, air over it -- writing only what
        differs from the lawn."""
        x1 = x if x1 is None else x1
        if top > 61:
            save.fill((x, 61, z0), (x1, top - 1, z1), DIRT)
        elif top < 61:
            save.fill((x, top + 1, z0), (x1, 61, z1), AIR)
        save.fill((x, top, z0), (x1, top, z1), GRASS)

    def test_a_wall_following_a_slope_steps_cleanly(self):
        """Up a hillside rising a block every two cells: the walk rides it
        a block at a time, every column founded on the ground by its
        footing, and the walk is walked up and back down."""
        save = self.world(self.slope)
        b = Builder(save)
        section = CrossSection(
            {(-1, 0): STONE, (0, 0): STONE, (1, 0): STONE, (-1, 1): STONE,
             (1, 1): SLAB, (0, 1): AIR, (0, 2): AIR, (0, 3): AIR},
            walk=[(0, 0)], footing=COBBLE)
        result = b.extrude([(2, 10), (40, 10)], section)
        profile = result.profile
        self.assertTrue(all(abs(p - q) <= 1 for p, q in
                            zip(profile, profile[1:])))
        self.assertGreater(profile[-1], profile[0] + 10)
        self.assertFalse(result.reach.stranded)
        for x, y, z in result.walk:
            self.assertEqual(self.name(x, y, z), STONE)
            ground = self.save.get_block(x, y - 1, 11).name
            self.assertIn(ground, (STONE, COBBLE, DIRT,
                                   "minecraft:grass_block"))
        # A founded column's ground is its highest block: the parapet's
        # top beside the walk, a block over the ground the probe read.
        self.assertEqual(b.known_ground(20, 9), profile[18] + 1)
        self.assertEqual(b.probe(20, 9).waterline, profile[18])

    def test_profile_is_the_roman_walls_smoothed_line(self):
        """On ground that varies only along a straight run, following the
        terrain at the Roman wall's window lays the profile its
        smoothed_line computed, cell for cell."""
        rng = random.Random(7)
        heights = {}
        y = 64
        for x in range(2, 46):
            y = max(56, min(72, y + rng.choice((-2, -1, 0, 0, 1, 2))))
            heights[x] = y

        def ground(save):
            for x, top in heights.items():
                self.column_range(save, x, 7, 13, top)
        save = self.world(ground)
        smoothed_line = _tool_function(
            os.path.join("roman_city", "jw_groundwork.py"), "smoothed_line")
        cells = [(x, 10) for x in range(2, 46)]
        expected = smoothed_line(Builder(save), cells)
        section = CrossSection({(-1, 0): STONE, (0, 0): STONE, (1, 0): STONE,
                                (-1, 1): STONE}, footing=COBBLE)
        result = Builder(save).extrude([(2, 10), (45, 10)], section,
                                       window=geometry.WALL_WINDOW)
        self.assertEqual(list(result.profile), expected)
        self.assertNotEqual(expected, [heights[x] for x in range(2, 46)])

    def test_profile_is_the_labs_wall_tops_less_seven(self):
        """With the ground on both faces at or over the lab's plane, the
        highest ground under the section with no window is the lab's
        _wall_tops less its seven."""
        rng = random.Random(11)
        outer, inner = {}, {}
        for x in range(2, 46):
            outer[x] = rng.randint(61, 66)
            inner[x] = rng.randint(61, 66)

        def ground(save):
            for x in range(2, 46):
                self.column_range(save, x, 9, 9, outer[x])
                self.column_range(save, x, 11, 11, inner[x])
                self.column_range(save, x, 10, 10, min(outer[x], inner[x]))
        save = self.world(ground)
        wall_tops = _tool_function(
            os.path.join("secret_lab", "m1_groundwork.py"), "_wall_tops",
            P=types.SimpleNamespace(PLANE=61))
        tops = wall_tops(Builder(save), [(x, 9) for x in range(2, 46)],
                         [(x, 11) for x in range(2, 46)])
        section = CrossSection({(-1, 0): STONE, (0, 0): STONE, (1, 0): STONE},
                               footing=COBBLE)
        result = Builder(save).extrude([(2, 10), (45, 10)], section, window=0)
        self.assertEqual(list(result.profile), [top - 7 for top in tops])

    def test_terrain_following_battlement(self):
        """Up a slope, round a corner: the course at each column's own
        height, teeth at merlon_offsets per run from that run's corners --
        the corner a tooth -- each standing on the course where the course
        is, and every one in the result. battlement takes one y."""
        save = self.world(self.slope)
        b = Builder(save)
        section = CrossSection({(0, 0): STONE, (0, 1): Merlon(COBBLE)},
                               footing=STONE)
        path = [(4, 4), (30, 4), (30, 20)]
        result = b.extrude(path, section)
        line = result.line
        roles = {(x, z): merlon for x, z, merlon in _path_roles(path)}
        self.assertEqual(set(roles), set(line))
        teeth = []
        for i, (x, z) in enumerate(line):
            y = result.profile[i]
            self.assertEqual(self.name(x, y, z), STONE)
            self.assertEqual(self.name(x, y + 1, z),
                             COBBLE if roles[(x, z)] else AIR, (x, z))
            if roles[(x, z)]:
                teeth.append((x, y + 1, z))
        self.assertEqual(result.merlons, tuple(teeth))
        self.assertTrue(roles[(30, 4)], "the corner is a tooth")
        self.assertGreater(len(set(result.profile)), 5)
        with self.assertRaises(NBTError):
            b.battlement(path, {(4, 4): 70}, block=STONE)

    def test_corners_are_laid_level(self):
        """A closed rectangle on a slope, three wide: every turn level over
        the section's reach, and no two columns of the band side by side
        more than a block apart."""
        save = self.world(self.slope)
        b = Builder(save)
        section = CrossSection({(-1, 0): STONE, (0, 0): STONE, (1, 0): STONE,
                                (0, 1): Merlon(STONE)}, footing=COBBLE)
        path = rectangle(4, 4, 34, 24)
        result = b.extrude(path, section)
        band = _band(path, [-1, 0, 1])
        self.assertEqual(band.line, result.line)
        for window in band.windows:
            self.assertEqual(len({result.profile[i] for i in window}), 1)
        base = {(x, z): result.profile[i] for i, a, k, x, z, w, t in band.cells}
        for (x, z), y in base.items():
            self.assertEqual(self.name(x, y, z), STONE)
            for near in _neighbors(x, z):
                if near in base:
                    self.assertLessEqual(abs(base[near] - y), 1, (x, z))
        self.assertGreater(max(result.profile) - min(result.profile), 5)

    def test_a_floating_terrain_section_is_refused(self):
        """Ground falling away across the run: a section with no footing
        would leave its low side standing on nothing, and is refused with
        the hint; given a footing, it is founded."""
        def cross(save):
            for x in range(2, 20):
                self.column_range(save, x, 11, 11, 59)
        save = self.world(cross)
        b = Builder(save)
        bare = {(-1, 0): STONE, (0, 0): STONE, (1, 0): STONE}
        with self.assertRaisesRegex(Refused, "footing=") as caught:
            b.extrude([(4, 10), (15, 10)], CrossSection(bare))
        self.assertEqual(len(caught.exception.faults), 12)
        self.assertIn("(4, 61, 11), 2 over the ground at y=59",
                      caught.exception.faults[0])
        self.assertEqual(b.blocks_set, 0)
        b.extrude([(4, 10), (15, 10)], CrossSection(bare, footing=COBBLE))
        self.assertEqual([self.name(8, y, 11) for y in (60, 61)],
                         [COBBLE, STONE])

    def test_a_roads_surface_serves_only_a_road_with_no_turns(self):
        """Along a road's own waypoints the centerline is the road's, so the
        surface it laid there is a profile, cell for cell: a hedge beside a
        straight road up a slope, following ``[r.top(*cell) for cell in
        r.line]``, stands on the road's edge all along it. The road's
        ``heights`` are not that surface: each column is cut by the first
        stretch that reaches it, so on a road three wide the profile is laid
        a cell further along, and a hedge following it would stand a block
        off the road's edge at every change of level. A road that turns
        climbing is not level through its turn, and is refused."""
        save = self.world(self.slope)
        b = Builder(save)
        road = b.road([(2, 10), (40, 10)], width=3, mix="minecraft:gravel")
        surface = [road.top(*cell) for cell in road.line]
        self.assertEqual(surface[1:], list(road.heights[:-1]),
                         "the profile, a cell further along")
        changes = sum(p != q for p, q in zip(road.heights, road.heights[1:]))
        self.assertEqual(sum(p != q for p, q in zip(surface, road.heights)),
                         changes, "a block off at every change of level")
        self.assertGreater(changes, 10)
        hedge = CrossSection({(2, 1): "minecraft:oak_leaves[persistent=true]"})
        result = b.extrude(road.points, hedge, follow=surface)
        self.assertEqual(result.line, road.line)
        self.assertEqual(result.profile, tuple(surface))
        for x, z in road.line:
            edge = road.top(x, z + 1)
            self.assertEqual(self.name(x, edge, z + 1), "minecraft:gravel")
            self.assertEqual(self.name(x, edge + 1, z + 2),
                             "minecraft:oak_leaves", (x, z))
        self.assertGreater(road.heights[-1], road.heights[0] + 10)
        turning = b.road([(2, 30), (30, 30), (30, 44)], width=3,
                         mix="minecraft:gravel")
        with self.assertRaisesRegex(NBTError, r"not level through the turn "
                                              r"round \(30, 30\)"):
            b.extrude(turning.points, hedge,
                      follow=[turning.top(*cell) for cell in turning.line])

    def test_anchors_pin_the_profile(self):
        """An anchor on the centerline pins it, pulling the run down to it a
        block a cell; one off the centerline pins nothing and is refused."""
        save = self.world(self.slope)
        b = Builder(save)
        section = CrossSection({(0, 0): STONE}, footing=COBBLE)
        with self.assertRaisesRegex(NBTError, "off the extrusion's centerline"):
            b.extrude([(10, 10), (30, 10)], section, anchors={(20, 11): 64})
        result = b.extrude([(10, 10), (30, 10)], section,
                           anchors={(20, 10): 63})
        self.assertEqual(result.profile[10], 63)
        self.assertTrue(all(abs(p - q) <= 1 for p, q in
                            zip(result.profile, result.profile[1:])))

    def test_a_span_is_neither_read_nor_followed(self):
        """Across a sunken lane named in through=, the terrain-following
        wall is carried at its neighbors' height: the lane's columns are
        never read, so they cannot pull the profile down into it, and the
        wall spans the lane with nothing founded under it."""
        def lane(save):
            for x in range(9, 12):
                self.column_range(save, x, 0, 15, 58)
        save = self.world(lane)
        b = Builder(save)
        b.reserve(9, 0, 11, 15, name="lane")
        section = CrossSection({(0, 0): STONE, (0, 1): STONE}, footing=COBBLE)
        result = b.extrude([(2, 5), (18, 5)], section, through=("lane",))
        self.assertEqual(result.profile, (61,) * 17)
        for x in range(9, 12):
            self.assertNotIn((x, 5), b._probes, "never read")
            self.assertEqual([self.name(x, y, 5) for y in range(58, 63)],
                             ["minecraft:grass_block", AIR, AIR, STONE, STONE])

    def test_a_footing_founds_only_the_columns_standing_on_a_block(self):
        """A footing goes under each across column whose lowest piece is a
        block: not under a cut beside it, a ditch, nor under a Merlon,
        which is a tooth or a gap. The cut lowers its column's ground;
        each tooth stands on the grass, and is its column's ground."""
        save = self.world()
        b = Builder(save)
        section = CrossSection({(0, 0): STONE, (1, 0): AIR,
                                (-1, 1): Merlon(STONE)},
                               footing=COBBLE, bury=2)
        result = b.extrude([(2, 5), (12, 5)], section)
        self.assertEqual(result.profile, (61,) * 11)
        teeth = {(x, z) for x, _, z in result.merlons}
        # Heading east, + across is south.
        for x in range(2, 13):
            with self.subTest(x=x):
                self.assertEqual([self.name(x, y, 5) for y in (59, 60, 61)],
                                 [DIRT, COBBLE, STONE])
                self.assertEqual([self.name(x, y, 6) for y in (59, 60, 61)],
                                 [DIRT, DIRT, AIR])
                self.assertEqual([self.name(x, y, 4) for y in (59, 60, 61)],
                                 [DIRT, DIRT, "minecraft:grass_block"])
                self.assertEqual(b.ground(x, 6), 60)
                self.assertEqual(b.ground(x, 4), 62 if (x, 4) in teeth else 61)
        self.assertTrue(teeth)

    def test_piers_carry_a_terrain_section_over_a_dip(self):
        """A trench under a terrain-following deck: with no piers its span
        is refused as standing on nothing, and with them the deck crosses
        the trench between piers, over air."""
        def trench(save):
            save.fill((11, 56, 0), (11, 61, 47), AIR)
        save = self.world(trench)
        b = Builder(save)
        deck = CrossSection({(0, 0): STONE})
        pier = CrossSection({(0, 0): STONE}, footing=COBBLE)
        with self.assertRaisesRegex(Refused, r"\(11, 60, 5\), 5 over the "
                                             r"ground at y=55"):
            b.extrude([(2, 5), (24, 5)], deck)
        result = b.extrude([(2, 5), (24, 5)], deck, pier=pier, pitch=4,
                           phase=0)
        y = result.profile[result.line.index((11, 5))]
        self.assertEqual([self.name(11, y - k, 5) for k in (0, 1, 2)],
                         [STONE, AIR, AIR])

    def test_following_the_terrain_over_its_own_wall_is_refused(self):
        """Made again, a terrain-following call would read its own top as
        the ground and stand a second wall on the first: refused, every
        such column named, nothing written; at the profile it was laid at
        it is already there. A ditch's floor is ground, not an extrusion's
        top, and a wall following the terrain crosses it."""
        save = self.world()
        b = Builder(save)
        section = CrossSection({(0, 0): STONE, (0, 1): STONE, (0, 2): STONE},
                               footing=COBBLE)
        first = b.extrude([(2, 5), (12, 5)], section)
        total = b.blocks_set
        with self.assertRaisesRegex(Refused, "top of an extrusion this "
                                             "session laid") as caught:
            b.extrude([(2, 5), (12, 5)], section)
        self.assertEqual(caught.exception.faults, [(x, 5) for x in range(2, 13)])
        self.assertEqual(b.blocks_set, total)
        self.assertEqual([self.name(6, y, 5) for y in (63, 64)], [STONE, AIR])
        again = b.extrude([(2, 5), (12, 5)], section, follow=first.profile)
        self.assertEqual(again.blocks, 0)
        b.extrude([(20, 2), (20, 12)], CrossSection({(0, 0): AIR, (0, -1): AIR}))
        self.assertEqual(b.ground(20, 7), 59)
        b.extrude([(16, 7), (24, 7)], section)
        self.assertEqual([self.name(20, y, 7) for y in (59, 60, 61)],
                         [DIRT, COBBLE, STONE], "founded on the ditch's floor")

    def test_following_the_terrain_down_its_own_ditch_is_refused(self):
        """A ditch made again following the terrain would read the floor
        its cut left as the ground and dig as deep again under it, every
        time it was made: refused, every such column named, nothing
        written; at the profile it was laid at it is already there. A
        section that stands on the floor rather than cutting into it -- a
        wall across the ditch -- still follows it."""
        save = self.world()
        b = Builder(save)
        ditch = CrossSection({(0, 0): AIR, (0, -1): AIR})
        first = b.extrude([(2, 5), (12, 5)], ditch)
        floor = [self.name(6, y, 5) for y in range(56, 64)]
        total = b.blocks_set
        with self.assertRaisesRegex(Refused, "the floor an extrusion this "
                                             "session cut") as caught:
            b.extrude([(2, 5), (12, 5)], ditch)
        self.assertEqual(caught.exception.faults, [(x, 5) for x in range(2, 13)])
        self.assertEqual(b.blocks_set, total)
        self.assertEqual([self.name(6, y, 5) for y in range(56, 64)], floor)
        again = b.extrude([(2, 5), (12, 5)], ditch, follow=first.profile)
        self.assertEqual(again.blocks, 0)
        self.assertEqual(b.ground(6, 5), 59)
        b.extrude([(6, 2), (6, 8)],
                  CrossSection({(0, 0): STONE, (0, 1): STONE}, footing=COBBLE))
        self.assertEqual(self.name(6, 59, 5), DIRT)
        self.assertIn(self.name(6, 60, 5), {COBBLE, STONE},
                      "a wall across it stands on the ditch's floor")


#: A canal: water between two banks of stone on a bed of it.
CANAL = CrossSection({(-1, 0): STONE, (0, 0): WATER, (1, 0): STONE,
                      (-1, -1): STONE, (0, -1): STONE, (1, -1): STONE})


class ExtrudeWaterTests(ExtrudeBase):
    """A section holding water -- a canal, an aqueduct's trough -- laid
    level, its water held to the one water check; the dams and the seals
    a dry extrusion's water is held to."""

    def test_a_channel_is_laid_level(self):
        """Water lies level: a channel following the terrain is refused
        before a column is read, and so is a profile that steps, named
        where it steps -- a pier holding water as much as the section --
        while a level one is laid."""
        b = Builder(self.world())
        with self.assertRaisesRegex(NBTError, r"holds water at \(0, 0\), and "
                                              r"water lies level"):
            b.extrude([(2, 10), (20, 10)], CANAL)
        with self.assertRaisesRegex(NBTError, r"steps from y=61 to y=60 at "
                                              r"\(11, 10\)"):
            b.extrude([(2, 10), (20, 10)], CANAL, follow=[61] * 9 + [60] * 10)
        with self.assertRaisesRegex(NBTError, "the pier holds water"):
            b.extrude([(2, 10), (20, 10)],
                      CrossSection({(0, 0): STONE}, footing=COBBLE),
                      pier=CrossSection(dict(CANAL.cells), footing=COBBLE),
                      pitch=4)
        self.assertEqual(b._probes, {}, "nothing was read")
        self.assertEqual(b.blocks_set, 0)
        b.extrude([(2, 10), (20, 10)], CANAL, follow=[61] * 19)
        self.assertEqual(self.name(11, 61, 10), "minecraft:water")

    def test_a_level_canal_keeps_its_water(self):
        """Cut into the lawn at its own level, a canal's water is held by
        the ground at its ends and its banks: written, and the water check
        over its cells finds no way out. Its columns' ground is the water,
        which the ground writers after it read as water."""
        from heaton_voxel.craft.survey import contained
        save = self.world()
        b = Builder(save)
        result = b.extrude([(5, 10), (20, 10)], CANAL, follow=61)
        self.assertEqual(result.profile, (61,) * 16)
        water = {(x, 61, 10): save.get_block(x, 61, 10) for x in range(5, 21)}
        self.assertTrue(all(state.name == "minecraft:water"
                            for state in water.values()))
        self.assertEqual([self.name(12, 61, z) for z in (9, 11)], [STONE] * 2)
        held = contained(save, water)
        self.assertEqual((held.leaks, held.standing), ([], []))
        self.assertEqual(b.known_ground(12, 10), 61)
        with self.assertRaisesRegex(Refused, "over water"):
            b.level_pad(11, 10, 13, 10, 62)

    def test_an_open_end_spills_only_where_it_is_allowed(self):
        """An aqueduct's trough carried over the lawn: both open ends let
        its water out, refused, each named -- and no seal mends the
        channel's own water. With the north end built first and the fall
        from the south end into a pond given as spill, it is held; a spill
        that stops short of the pond leaks where it stops."""
        from heaton_voxel.craft.survey import contained

        def pond(save):
            save.fill((9, 58, 21), (11, 61, 23), WATER)
        save = self.world(pond)
        b = Builder(save)
        path = [(10, 5), (10, 20)]
        with self.assertRaises(Refused) as caught:
            b.extrude(path, CANAL, follow=66)
        self.assertEqual([leak.cell for leak in caught.exception.faults],
                         [(10, 66, 4), (10, 66, 21)])
        self.assertIn("spill=", str(caught.exception))
        with self.assertRaisesRegex(Refused, "the plan's own"):
            b.extrude(path, CANAL, follow=66, seal=COBBLE)
        b.set(10, 66, 4, STONE)                         # the north end
        fall = [(10, y, 21) for y in range(62, 67)]
        with self.assertRaises(Refused) as caught:
            b.extrude(path, CANAL, follow=66, spill=fall[2:])
        (leak,) = caught.exception.faults
        self.assertEqual((leak.cell, leak.kind), ((10, 63, 21), "falls"))
        self.assertEqual(self.name(10, 66, 12), AIR, "nothing was written")
        result = b.extrude(path, CANAL, follow=66, spill=fall)
        self.assertEqual(result.blocks, 16 * 6)
        water = {(10, 66, z): save.get_block(10, 66, z) for z in range(5, 21)}
        held = contained(save, water, spill=fall)
        self.assertEqual((held.leaks, held.standing), ([], []))
        self.assertEqual([str(leak) for leak in contained(save, water).standing],
                         ["(10, 66, 21): the water at (10, 66, 20) spreads "
                          "into the minecraft:air"])
        with self.assertRaisesRegex(NBTError, "world cells"):
            b.extrude(path, CANAL, follow=66, spill=True)

    def test_a_fill_across_water_is_a_dam_unless_allowed(self):
        """Filled across, a canal is two ponds, and a deck bridged into a
        pond's source dries the flow it fed: each refused with allow_dam
        offered, and with allow_dam=True each is written -- the canal's
        water left either side of the fill, the flow's feed taken."""
        def water(save):
            save.fill((30, 58, 2), (32, 61, 40), WATER)        # a canal
            save.set_block(10, 61, 30, WATER)
            for k, x in enumerate(range(11, 14), start=1):
                save.set_block(x, 61, 30, f"minecraft:water[level={k}]")
        save = self.world(water)
        b = Builder(save)
        pier = CrossSection({(0, 0): STONE}, footing=COBBLE)
        deck = CrossSection({(0, 0): STONE})
        with self.assertRaisesRegex(Refused, r"a body of water in two.*"
                                             r"allow_dam=True"):
            b.extrude([(26, 20), (36, 20)], pier, over_water="fill")
        with self.assertRaisesRegex(Refused, r"cut flowing water off.*"
                                             r"allow_dam=True"):
            b.extrude([(5, 30), (10, 30)], deck, over_water="bridge")
        self.assertEqual(b.blocks_set, 0)
        b.extrude([(26, 20), (36, 20)], pier, over_water="fill",
                  allow_dam=True)
        self.assertEqual([self.name(31, y, 20) for y in range(58, 62)],
                         [COBBLE] * 3 + [STONE])
        self.assertEqual([self.name(31, 61, z) for z in (19, 21)],
                         ["minecraft:water"] * 2)
        b.extrude([(5, 30), (10, 30)], deck, over_water="bridge",
                  allow_dam=True)
        self.assertEqual(self.name(10, 61, 30), STONE)
        self.assertIsNone(b._stand((10, 30))[1],
                          "stone where the source stood is dry ground now")

    def test_a_cut_that_opens_a_pond_is_sealed_where_asked(self):
        """A ditch beside a pond lets its water in. With seal= a full block
        that stays a wall -- refused, before anything is read, when it is
        sand -- every cell of the pond the water would run from is walled
        off where it stands, and the ditch is cut."""
        from heaton_voxel.craft.survey import contained

        def pond(save):
            save.fill((10, 58, 10), (14, 61, 14), WATER)
        save = self.world(pond)
        b = Builder(save)
        ditch = CrossSection({(0, 0): AIR, (0, -1): AIR})
        with self.assertRaisesRegex(NBTError, "would not stay a wall"):
            b.extrude([(5, 9), (20, 9)], ditch, seal="minecraft:sand")
        self.assertEqual(b._probes, {}, "nothing was read")
        result = b.extrude([(5, 9), (20, 9)], ditch, seal=COBBLE)
        sealed = [(x, y, 10) for x in range(10, 15) for y in (60, 61)]
        self.assertEqual(result.sealed, len(sealed))
        self.assertEqual({self.name(*cell) for cell in sealed}, {COBBLE})
        self.assertEqual([self.name(12, y, 9) for y in (59, 60, 61)],
                         [DIRT, AIR, AIR])
        water = {(x, y, z): save.get_block(x, y, z) for x in range(10, 15)
                 for y in range(58, 62) for z in range(10, 15)
                 if save.get_block(x, y, z).name == "minecraft:water"}
        self.assertEqual(contained(save, water).standing, [])

    def test_a_fill_that_dries_a_flow_is_a_dam_unless_allowed(self):
        """Filled from its bed, a column takes out the water over it, and a
        source that fed a flow beside it leaves the flow to dry: refused
        by the starve check the fill is held to, allow_dam=True offered --
        extrude's own argument, never grade -- and nothing written. With
        allow_dam=True the fill is written, the source under the stone."""
        def flow(save):
            save.set_block(10, 61, 30, WATER)
            for k, x in enumerate(range(11, 14), start=1):
                save.set_block(x, 61, 30, f"minecraft:water[level={k}]")
        save = self.world(flow)
        b = Builder(save)
        pier = CrossSection({(0, 0): STONE}, footing=COBBLE)
        with self.assertRaises(Refused) as caught:
            b.extrude([(10, 28), (10, 32)], pier, over_water="fill")
        message = str(caught.exception)
        self.assertRegex(message, r"cut flowing water off.*Pass "
                                  r"allow_dam=True to fill it")
        self.assertNotIn("grade(", message)
        self.assertEqual(caught.exception.faults,
                         [(11, 61, 30), (12, 61, 30), (13, 61, 30)])
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(self.name(10, 61, 30), "minecraft:water")
        b.extrude([(10, 28), (10, 32)], pier, over_water="fill",
                  allow_dam=True)
        self.assertEqual(self.name(10, 61, 30), STONE)

    def _ditch_by(self, shape):
        """A lawn shaped by ``shape``, a Builder over it, and the ditch two
        deep along z=9 from x=5 to x=20 that cuts the bank of whatever
        water ``shape`` puts at z=10."""
        b = Builder(self.world(shape))
        ditch = CrossSection({(0, 0): AIR, (0, -1): AIR})
        return b, ditch, [(5, 9), (20, 9)]

    @staticmethod
    def _pond(save):
        save.fill((10, 58, 10), (14, 61, 14), WATER)

    def test_a_seal_in_a_keep_out_is_refused(self):
        """The ditch stands clear of the reservation over the pond's north
        row, and its seals would stand in it: refused, the reservation
        named, nothing written -- and with the reservation named in
        through= the seals are laid in it."""
        b, ditch, path = self._ditch_by(self._pond)
        b.reserve(10, 10, 14, 10, name="rim")
        with self.assertRaisesRegex(Refused, r"extrude's seals would stand "
                                             r"in.*'rim'"):
            b.extrude(path, ditch, seal=COBBLE)
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(self.name(12, 61, 10), "minecraft:water")
        result = b.extrude(path, ditch, seal=COBBLE, through=("rim",))
        self.assertEqual(result.sealed, 10)
        self.assertEqual(self.name(12, 61, 10), COBBLE)

    def test_a_seal_that_dries_a_flow_is_a_dam_unless_allowed(self):
        """A seal takes the water it stands in, and the source the ditch
        opens fed a flow running south from it: walled off, the source
        feeds nothing, and the flow would dry -- refused, allow_dam=True
        offered, nothing written; with allow_dam=True it is sealed."""
        def flow(save):
            save.set_block(12, 61, 10, WATER)
            for k, z in enumerate(range(11, 14), start=1):
                save.set_block(12, 61, z, f"minecraft:water[level={k}]")
        b, ditch, path = self._ditch_by(flow)
        with self.assertRaises(Refused) as caught:
            b.extrude(path, ditch, seal=COBBLE)
        self.assertRegex(str(caught.exception),
                         r"cut flowing water off.*Pass allow_dam=True")
        self.assertEqual(caught.exception.faults,
                         [(12, 61, 11), (12, 61, 12), (12, 61, 13)])
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(self.name(12, 61, 9), "minecraft:grass_block")
        result = b.extrude(path, ditch, seal=COBBLE, allow_dam=True)
        self.assertEqual(result.sealed, 1)
        self.assertEqual(self.name(12, 61, 10), COBBLE)
        self.assertEqual(self.name(12, 61, 9), AIR)

    def test_a_seal_is_audited_with_the_plan(self):
        """A chest sunk in the pond under a cell the seal walls off: under
        water it opens, under the seal -- a block that conducts -- it does
        not, so the survival audit refuses the plan its seals and all, and
        nothing is written."""
        def pond(save):
            self._pond(save)
            save.set_block(12, 59, 10, "minecraft:chest[facing=north,"
                                       "type=single,waterlogged=true]")
        b, ditch, path = self._ditch_by(pond)
        with self.assertRaisesRegex(Refused, "will not keep") as caught:
            b.extrude(path, ditch, seal=COBBLE)
        self.assertEqual([(fault.cell, fault.kind) for fault
                          in caught.exception.faults],
                         [((12, 59, 10), "blocked chest")])
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(self.name(12, 60, 10), "minecraft:water")

    def test_a_seal_is_walked_with_the_plan(self):
        """A source on a post of dirt three over the low walk cell of a
        step down: the walk's cut takes the post from under it, and its
        seal, walled off where it stands, is where the walker's head goes
        coming down -- refused by the walk check, which walks the seals
        with the plan, and nothing written. Without the source the walk
        walks."""
        cells = {(0, 0): STONE, (0, -1): STONE, (0, 1): AIR, (0, 2): AIR}
        section = CrossSection(cells, walk=[(0, 0)])
        profile = [71, 71, 71, 71, 70, 70, 70, 70]

        def post(save):
            save.fill((6, 62, 10), (6, 72, 10), DIRT)
            save.set_block(6, 73, 10, WATER)
        b = Builder(self.world(post))
        with self.assertRaises(Refused) as caught:
            b.extrude([(2, 10), (9, 10)], section, follow=profile,
                      over_water="bridge", seal=COBBLE)
        self.assertIn("does not walk both ways", str(caught.exception))
        self.assertIn((6, 70, 10), caught.exception.faults)
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(self.name(6, 73, 10), "minecraft:water")
        b.extrude([(2, 12), (9, 12)], section, follow=profile)


# --------------------------------------------------------------------------
# examples/sections.py: the samples, and the builds they were read off
# --------------------------------------------------------------------------

def _tool_module(*parts: str):
    """A ``tools/`` module that is a table of materials or a plan and
    writes nothing when imported, loaded by its path."""
    path = os.path.join(_ROOT, "tools", *parts)
    spec = importlib.util.spec_from_file_location(
        "_extrude_tool_" + "_".join(parts)[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Recorder:
    """Just enough Builder for a script's wall: every cell it sets or
    fills, the last write winning, as the world would keep it, and the
    ground ``ground`` answers from ``ground``, a function of the column."""

    def __init__(self, ground=None):
        self.cells: dict[tuple[int, int, int], BlockState] = {}
        self._ground: dict = {}
        self._read = ground

    def ground(self, x, z):
        return self._read(x, z)

    def set(self, x, y, z, block):
        self.cells[(x, y, z)] = (BlockState.parse(block)
                                 if isinstance(block, str) else block)

    def fill(self, corner1, corner2, block):
        (x0, x1), (y0, y1), (z0, z1) = (sorted(pair) for pair in
                                        zip(corner1, corner2))
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    self.set(x, y, z, block)


#: The modern fixture's one pair of finished chunks side by side, real
#: 26.3 ground -- grass, dirt, gravel and sand -- from 59 to 65, falling
#: east.
LAND = (-496, 0, -465, 15)


def _same(found, want) -> bool:
    return found.canonical() == want.canonical()


class ExtrudeExampleTests(ExtrudeBase):
    """examples/sections.py: each sample held to the script it was read
    off, and each extruded over the modern fixture's own ground -- the
    survival audit finding nothing new, its walk walked both ways, and its
    water held."""

    def assertSameCells(self, found, expected, cells):
        """Every one of ``cells`` holds the same state in ``found`` and
        ``expected`` (each a function of the cell), the first that differs
        named."""
        for cell in cells:
            a, b = found(cell), expected(cell)
            if not _same(a, b):
                self.fail(f"{cell} holds {a}, where the script wrote {b}")

    def test_the_roman_wall_is_the_scripts_column(self):
        """jw_groundwork.py's city_walls, run against a recorder over a south
        wall on uneven ground -- its own smoothed_line for the profile and
        its own ground reads -- wrote every cell ROMAN_WALL lays at that
        profile, from min(g - 2, base - 2) up to the parapet: the merlons
        apart, the script's on every even cell along, the section's counted
        per run from its corners."""
        rng = random.Random(3)
        heights = {(x, z): rng.randint(59, 63) for x in range(0, 44)
                   for z in range(16, 23)}

        def ground(save):
            for (x, z), top in heights.items():
                ExtrudeTerrainTests.column_range(save, x, z, z, top)
        pristine = self.world(ground)       # read by the script, never built on
        save = self.twin(pristine)
        common = _tool_module("roman_city", "common.py")
        segs = {"south": (20, (2, 40), "x")}
        seg_cell = _tool_function(os.path.join("roman_city", "jw_groundwork.py"),
                                  "seg_cell", WALL_SEGS=segs)
        smoothed_line = _tool_function(
            os.path.join("roman_city", "jw_groundwork.py"), "smoothed_line")
        city_walls = _tool_function(
            os.path.join("roman_city", "jw_groundwork.py"), "city_walls",
            WALL_SEGS=segs, GATES={"south": 10 ** 6}, seg_cell=seg_cell,
            smoothed_line=smoothed_line, wall_tower=lambda *a, **k: None,
            P=types.SimpleNamespace(WALL_T=3, WALL_H=10, TOWER_EVERY=38),
            C=common)
        reader = Builder(pristine)
        script = _Recorder(reader.ground)
        city_walls(script)
        line = smoothed_line(reader, [(u, 19) for u in range(2, 41)])
        # Clockwise round the city, the south wall runs west, its inside
        # (+ across) to the north: the script's t=0, 1, 2 are across -1, 0
        # and +1.
        result = Builder(save).extrude([(40, 19), (2, 19)], sections.ROMAN_WALL,
                                       follow=line[::-1])
        self.assertFalse(result.reach.stranded)
        cells = []
        for i, u in enumerate(range(2, 41)):
            base = line[i]
            for z in (18, 19, 20):
                g = reader.ground(u, z)
                cells += [(u, y, z) for y in range(min(g - 2, base - 2),
                                                   base + 12)]
        self.assertIsInstance(sections.ROMAN_WALL.cells[(-1, 12)], Merlon)
        self.assertSameCells(
            lambda c: save.get_block(*c),
            lambda c: script.cells.get(c) or pristine.get_block(*c), cells)
        self.assertEqual(
            {c for c in script.cells if c not in set(cells)},
            {(u, line[i] + 12, 20) for i, u in enumerate(range(2, 41))
             if u % 2 == 0}, "nothing else written but the script's merlons")
        self.assertTrue(all(_same(script.cells[c], BlockState.parse(
            "minecraft:bricks")) for c in script.cells if c not in set(cells)))

    def test_the_lab_sections_are_the_scripts_wall_column(self):
        """m1_groundwork.py's _wall_column, over a given top, writes in rows
        top-6..top+8 of its outer, core and inner columns, pilaster on and
        off, what LAB_WALL and LAB_PILASTER hold at up 1..15 -- the profile's
        height top-7 -- and the lab's pilasters stand where LAB_PIERS puts
        them."""
        lab = _tool_module("secret_lab", "lab_common.py")
        wall_column = _tool_function(
            os.path.join("secret_lab", "m1_groundwork.py"), "_wall_column",
            C=lab, P=types.SimpleNamespace(PLANE=66),
            _column_base=lambda b, x, z: 60)
        top = 80
        for pilaster, section in ((False, sections.LAB_WALL),
                                  (True, sections.LAB_PILASTER)):
            for role, across in (("outer", -1), ("core", 0), ("inner", 1)):
                with self.subTest(pilaster=pilaster, role=role):
                    script = _Recorder()
                    wall_column(script, 0, 0, role, top, pilaster)
                    for up in range(1, 16):
                        self.assertTrue(_same(section.cells[(across, up)],
                                              script.cells[(0, top - 7 + up, 0)]),
                                        f"up={up}")
        self.assertEqual((sections.LAB_PIERS["pitch"], sections.LAB_PIERS["phase"],
                          sections.LAB_PIERS["pier_width"]), (8, 2, 1))
        self.assertIs(sections.LAB_PIERS["pier"], sections.LAB_PILASTER)
        self.assertEqual(sections.LAB_WALL.walk, ((0, 7),))

    def test_the_lab_wall_is_the_scripts_under_the_plane(self):
        """m1_groundwork.py's south face, run against a recorder over uneven
        ground under the plane -- its own _wall_tops for the deck and its
        own _column_base for the foot of the slate -- wrote every cell
        LAB_WALL lays with LAB_PIERS at the tops less seven, from the
        ground's own depth down to eight over the deck: the deepslate from
        two under each column's ground, which is the footing's bury=3, and
        nothing under it."""
        rng = random.Random(5)
        heights = {(x, z): rng.randint(59, 65) for x in range(0, 44)
                   for z in range(16, 23)}

        def ground(save):
            for (x, z), top in heights.items():
                ExtrudeTerrainTests.column_range(save, x, z, z, top)
        pristine = self.world(ground)       # read by the script, never built on
        save = self.twin(pristine)
        lab = _tool_module("secret_lab", "lab_common.py")
        script_file = os.path.join("secret_lab", "m1_groundwork.py")
        plane = types.SimpleNamespace(PLANE=66)
        column_base = _tool_function(script_file, "_column_base", P=plane)
        wall_tops = _tool_function(script_file, "_wall_tops", P=plane)
        wall_column = _tool_function(script_file, "_wall_column", C=lab,
                                     P=plane, _column_base=column_base)
        reader = Builder(pristine)
        script = _Recorder(reader.ground)
        script.save = pristine
        # perimeter_wall's south face, sz0=18 and sz1=20: outer z=20, core
        # z=19, inner z=18, its tops read over the outer column and the cell
        # inside the inner one.
        xs = list(range(2, 41))
        tops = wall_tops(script, [(x, 20) for x in xs], [(x, 17) for x in xs])
        for x, top in zip(xs, tops):
            for role, z in (("outer", 20), ("core", 19), ("inner", 18)):
                wall_column(script, x, z, role, top, pilaster=(x % 8 == 2))
        # Clockwise round the campus the south face runs west, its inside
        # to the north: outer, core and inner are across -1, 0 and +1.
        result = Builder(save).extrude(
            [(40, 19), (2, 19)], sections.LAB_WALL,
            follow=[top - 7 for top in tops[::-1]], **sections.LAB_PIERS)
        self.assertFalse(result.reach.stranded)
        cells = []
        for x, top in zip(xs, tops):
            for z in (18, 19, 20):
                g = reader.ground(x, z)
                self.assertLess(g, plane.PLANE)
                cells += [(x, y, z) for y in range(g - 4, top + 9)]
        self.assertSameCells(
            lambda c: save.get_block(*c),
            lambda c: script.cells.get(c) or pristine.get_block(*c), cells)
        self.assertLessEqual(set(script.cells), set(cells))

    def test_the_aqueduct_is_the_scripts_arcade(self):
        """jw_groundwork.py's aqueduct, run against a recorder on a lawn at
        y=61 at its own coordinates, wrote along its arcade -- from the
        springhouse north to z=468, the piers skipping Jeff's path -- every
        cell AQUEDUCT and its piers lay at follow=73 through the path's
        reservation, but the soffit stairs it sprang the arch over the path
        from. Its channel's ends built first, its water is held."""
        from heaton_voxel.craft.survey import contained
        chunks = [(29, z) for z in range(29, 34)]
        save = self.world(chunks=chunks)
        common = _tool_module("roman_city", "common.py")
        aqueduct = _tool_function(
            os.path.join("roman_city", "jw_groundwork.py"), "aqueduct",
            C=common, P=types.SimpleNamespace(
                AQUA_X=477, AQUA_SOURCE_Z=528,
                ZONES={"castellum": ((472, 440, 482, 450), 65)}))
        script = _Recorder(lambda x, z: 61)
        aqueduct(script)
        b = Builder(save)
        b.reserve(476, 469, 478, 476, name="path")
        for z in (467, 524):
            b.set(477, 73, z, STONE)                # the channel's ends
        b.extrude([(477, 523), (477, 468)], sections.AQUEDUCT, follow=73,
                  through=("path",), **sections.AQUEDUCT_PIERS)
        soffit = {(x, 69, z) for x in (476, 477, 478) for z in (469, 476)}
        cells = [(x, y, z) for x in (476, 477, 478) for y in range(62, 74)
                 for z in range(468, 524) if (x, y, z) not in soffit]
        air = BlockState.parse(AIR)
        self.assertSameCells(lambda c: save.get_block(*c),
                             lambda c: script.cells.get(c, air), cells)
        self.assertEqual({script.cells[c].name for c in soffit},
                         {"minecraft:smooth_sandstone_stairs"})
        water = {(477, 73, z): save.get_block(477, 73, z)
                 for z in range(468, 524)}
        held = contained(save, water)
        self.assertEqual((held.leaks, held.standing), ([], []))

    def test_the_hedge_is_the_parks(self):
        """park_plan.py's hedge in the cell over the lawn (WALK, GY + 1) and
        the one over that, the top course flowering one cell in five."""
        park = _tool_module("park", "park_plan.py")
        self.assertEqual(park.WALK, park.GY + 1)
        hedge = sections.HEDGE.cells
        self.assertEqual(set(hedge), {(0, 1), (0, 2)})
        self.assertTrue(_same(hedge[(0, 1)], BlockState.parse(park.HEDGE)))
        top = dict(hedge[(0, 2)].entries)
        self.assertEqual(top, {park.HEDGE: 4, park.HEDGE_FLOWER: 1})

    def fixture(self):
        """A copy of the modern fixture, loaded."""
        from fixtures import MODERN_LEVEL_DAT, MODERN_REGION
        directory = tempfile.mkdtemp(prefix="extrude-examples-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        region = os.path.join(directory, "dimensions", "minecraft",
                              "overworld", "region")
        os.makedirs(region)
        shutil.copyfile(MODERN_REGION, os.path.join(region, "r.-1.0.mca"))
        shutil.copyfile(MODERN_LEVEL_DAT, os.path.join(directory, "level.dat"))
        return MinecraftSave.load(directory)

    def test_every_sample_extrudes_over_the_fixtures_ground(self):
        """Over real ground, each sample laid as its build laid it: the two
        curtain walls round a circuit and along a run, their walks walked
        both ways; the hedge along the slope; the aqueduct carried level on
        its piers, its west end built first and its east end falling into a
        basin through the spill it is given. Nothing the game will not keep
        is left that was not there before, and the aqueduct's water goes
        nowhere but its spill."""
        from heaton_voxel.craft.survey import contained, unsupported
        x0, z0, x1, z1 = LAND
        box = ((x0, -64, z0), (x1, 160, z1))
        circuit = rectangle(-492, 2, -470, 12)
        fall = [(-470, y, 12) for y in range(62, 71)]

        def aqueduct(b):
            b.basin((-470, 10, -467, 14), 61, floor="minecraft:sandstone",
                    lining="minecraft:sandstone",
                    rim="minecraft:smooth_sandstone")
            b.set(-495, 70, 12, "minecraft:smooth_sandstone")   # its west end
            return b.extrude([(-494, 12), (-471, 12)], sections.AQUEDUCT,
                             follow=70, spill=fall, **sections.AQUEDUCT_PIERS)
        calls = {
            "roman_wall": lambda b: b.extrude(circuit, sections.ROMAN_WALL,
                                              window=geometry.WALL_WINDOW),
            "lab_wall": lambda b: b.extrude(circuit, sections.LAB_WALL,
                                            window=0, **sections.LAB_PIERS),
            "hedge": lambda b: b.extrude([(-494, 3), (-467, 3)],
                                         sections.HEDGE),
            "aqueduct": aqueduct,
        }
        self.assertEqual(set(calls), set(sections.SECTIONS))
        for name, call in calls.items():
            with self.subTest(sample=name):
                save = self.fixture()
                before = unsupported(save, *box)
                b = Builder(save)
                result = call(b)
                self.assertGreater(result.blocks, 0)
                self.assertEqual(unsupported(save, *box), before)
                if result.walk:
                    reach = result.reach
                    self.assertEqual(
                        [cell for cell in result.walk
                         if cell not in reach.reached or cell in reach.stranded],
                        [])
                self.assertEqual(b.finish().unsupported, [])
        water = {(x, 70, 12): save.get_block(x, 70, 12)
                 for x in range(-494, -470)}
        self.assertEqual({state.name for state in water.values()},
                         {"minecraft:water"})
        held = contained(save, water, spill=fall)
        self.assertEqual((held.leaks, held.standing), ([], []))


if __name__ == "__main__":
    unittest.main()
