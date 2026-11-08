"""Tests for Builder.scatter: planting at scale, keyed by position.

The park was planted by hand with dice drawn from a session's random
stream, so one more tree early in a script re-rolled every flower after
it, and TestWorld (2)'s Roman gardens put flowers on gravel paths. A
scatter rolls every die on the column itself, spaces its plantings by
dart throwing in an order keyed on the cell, plants only what craft holds
to the game's rule of what keeps it, on the soil that rule names, and
never on water, a path or a keep-out -- and refuses, before anything is
written, a call that would plant nothing.
"""

from __future__ import annotations

import os
import random
import shutil
import tempfile
import unittest
import zlib

import fixtures
from heaton_voxel.blocks import BlockState
from heaton_voxel.craft import (LANE_MIX, Builder, Material, Mix,
                                ScatterResult, Tree, unsupported)
from heaton_voxel.craft.build import StraightStair
from heaton_voxel.craft.build.planting import _canonical, _JITTER, SKIP_REASONS
from heaton_voxel.craft.errors import IllegalBlock, Refused
from heaton_voxel.craft.noise import MASK, patch
from heaton_voxel.nbt import NBTError
from heaton_voxel.world import MinecraftSave
from tests.test_world import make_save
from tests.test_worksite import SyntheticSiteCase

GRASS = "minecraft:grass_block[snowy=false]"
DIRT = "minecraft:dirt"
STONE = "minecraft:stone"
SAND = "minecraft:sand"
WATER = "minecraft:water[level=0]"
POPPY = "minecraft:poppy"
DANDELION = "minecraft:dandelion"
BOULDER = "minecraft:mossy_cobblestone"


class ScatterBase(unittest.TestCase):
    """Copies of one grass field -- dirt from y=56, grass at 63, air above,
    over the sixteen chunks x, z -16..47 -- so two sessions start from the
    same bytes and their saves can be compared byte for byte."""

    CHUNKS = [(x, z) for x in range(-1, 3) for z in range(-1, 3)]

    def world(self, shape=None):
        """A fresh copy of the field, ``shape(save)`` applied and the
        surface map rebuilt over it, and its directory."""
        if not hasattr(self, "_origin"):
            scratch = tempfile.mkdtemp(prefix="scatter-tests-")
            self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
            self._scratch = scratch
            self._origin = os.path.join(scratch, "origin")
            make_save(self._origin, self.CHUNKS)
            save = MinecraftSave.load(self._origin)
            save.fill((-16, 56, -16), (47, 62, 47), DIRT)
            save.fill((-16, 63, -16), (47, 63, 47), GRASS)
            save.rebuild_heightmaps((-16, 0, -16), (47, 0, 47))
            save.save()
            self._copies = 0
        self._copies += 1
        directory = os.path.join(self._scratch, f"copy-{self._copies}")
        shutil.copytree(self._origin, directory)
        save = MinecraftSave.load(directory)
        if shape is not None:
            shape(save)
            save.rebuild_heightmaps((-16, 0, -16), (47, 0, 47))
        return directory, save

    @staticmethod
    def region(directory, name="r.0.0.mca") -> bytes:
        path = os.path.join(directory, "dimensions/minecraft/overworld/region",
                            name)
        with open(path, "rb") as handle:
            return handle.read()

    def name(self, save, x, y, z) -> str:
        return save.get_block(x, y, z).name

    def assertClean(self, save, corner1=(-16, 56, -16), corner2=(47, 80, 47)):
        """Nothing in the box the game will not keep."""
        self.assertEqual(unsupported(save, corner1, corner2), [])


class FootingTests(ScatterBase):
    """A thing is planted only on the ground its game tag names, and the
    ground is natural ground unless ``on=`` says otherwise."""

    def test_flowers_go_only_on_their_soil(self):
        """A row of grass, dirt, gravel, sand, stone and a dirt path: the
        poppies stand on the grass and the dirt and nowhere else. Gravel,
        sand and stone are natural ground a poppy will not keep on --
        TestWorld (2)'s garden flowers on gravel -- and a path is no
        natural ground at all. What was planted the game keeps."""
        grounds = ("minecraft:grass_block", DIRT, "minecraft:gravel", SAND,
                   STONE, "minecraft:dirt_path")

        def shape(save):
            for i, ground in enumerate(grounds):
                save.fill((3 * i, 63, 4), (3 * i + 2, 63, 4), ground)
        _, save = self.world(shape)
        b = Builder(save)
        r = b.scatter((0, 4, 17, 4), POPPY)
        self.assertIsInstance(r, ScatterResult)
        self.assertEqual(r.planted, tuple((x, 64, 4) for x in range(6)))
        self.assertEqual(r.columns, tuple((x, 4) for x in range(6)))
        self.assertEqual(r.kinds, {POPPY: 6})
        self.assertEqual(r.skipped, {
            "wrong ground": tuple((x, 4) for x in range(15, 18)),
            "off its soil": tuple((x, 4) for x in range(6, 15))})
        self.assertEqual((r.thinned, r.crowded), (0, 0))
        for x in range(18):
            self.assertEqual(self.name(save, x, 64, 4),
                             POPPY if x < 6 else "minecraft:air", x)
        self.assertEqual(b.finish().unsupported, [])
        self.assertClean(save)

    def test_every_thing_stands_on_its_tags_soil(self):
        """Over every ground a tag names, and some no tag does, each with
        water beside it and ``on=`` taking anything: a flower, a sapling
        and a tall plant are planted exactly on ``supports_vegetation``,
        an azalea on ``supports_azalea``, a dead bush on
        ``supports_dry_vegetation`` (no glazed terracotta), sugar cane on
        ``supports_sugar_cane`` (no farmland), a Tree on its sapling's
        soil, and a boulder on everything -- the game's own tags, vendored
        from the jar, and the game keeps every one."""
        tags = fixtures.block_tags()
        named = (tags["supports_dry_vegetation"] | tags["supports_azalea"]
                 | tags["supports_sugar_cane"])
        grounds = sorted(named | {
            STONE, "minecraft:gravel", "minecraft:white_glazed_terracotta",
            "minecraft:snow_block", "minecraft:dirt_path", "minecraft:calcite",
            "minecraft:andesite", "minecraft:bricks"})
        self.assertLessEqual(len(grounds), 40)
        sites = {(-14 + 3 * (i % 20), -14 + 3 * (i // 20)): ground
                 for i, ground in enumerate(grounds)}

        def shape(save):
            for (x, z), ground in sites.items():
                save.set_block(x, 63, z, ground)
                save.set_block(x + 1, 63, z, WATER)
        anything = set(grounds)
        cases = (
            (POPPY, tags["supports_vegetation"]),
            ("minecraft:oak_sapling", tags["supports_vegetation"]),
            ("minecraft:lilac", tags["supports_vegetation"]),
            (Tree("oak"), tags["supports_vegetation"]),
            ("minecraft:flowering_azalea", tags["supports_azalea"]),
            ("minecraft:dead_bush", tags["supports_dry_vegetation"]),
            ("minecraft:sugar_cane", tags["supports_sugar_cane"]),
            (BOULDER, anything),
        )
        for thing, soil in cases:
            with self.subTest(thing=thing):
                _, save = self.world(shape)
                b = Builder(save)
                r = b.scatter(list(sites), thing, spacing=3,
                              on=lambda name: True)
                stood = {sites[(x, z)] for x, _, z in r.planted}
                self.assertEqual(stood, soil & anything)
                passed = {sites[c] for c in r.skipped.get("off its soil", ())}
                self.assertEqual(passed, anything - soil)
                self.assertLessEqual(set(r.skipped), {"off its soil"})
                for x, y, z in r.planted:
                    self.assertEqual(y, 64)
                self.assertClean(save, (-16, 56, -16), (47, 75, 47))

    def test_sugar_cane_needs_water_beside_its_block(self):
        """Cane on sand: planted where water stands beside the sand on any
        of its four sides, ``"dry cane"`` where it stands diagonal to it,
        under its block (dirt, which stays put over water), or nowhere, as
        the game's ``SugarCaneBlock`` asks."""
        def shape(save):
            save.fill((0, 63, 10), (20, 63, 10), SAND)
            save.set_block(1, 63, 9, WATER)       # north of x=1
            save.set_block(4, 63, 11, WATER)      # south of x=4
            save.set_block(6, 63, 10, WATER)      # east of 5, west of 7
            save.set_block(21, 63, 11, WATER)     # diagonal to x=20
            save.set_block(15, 63, 10, DIRT)      # cane's soil as well,
            save.set_block(15, 62, 10, WATER)     # with water under it
        _, save = self.world(shape)
        b = Builder(save)
        r = b.scatter((0, 10, 20, 10), "minecraft:sugar_cane")
        self.assertEqual(r.columns, ((1, 10), (4, 10), (5, 10), (7, 10)))
        self.assertEqual(r.skipped["water"], ((6, 10),))
        dry = tuple((x, 10) for x in range(21) if x not in (1, 4, 5, 6, 7))
        self.assertEqual(r.skipped["dry cane"], dry)
        self.assertClean(save)


class KeyTests(ScatterBase):
    """Every die is keyed on the column, so what a session did before, the
    order it calls in and the order an area is given in move nothing."""

    def test_the_planting_is_keyed_on_position_not_the_session(self):
        """Two sessions seeded apart, the second having laid a list-mix
        road and an ornamental tree first -- both drawn from its rng --
        plant the same flowers and trees in the same columns, and save the
        same bytes in the region they planted in."""
        things = [(POPPY, 3), (DANDELION, 2), (Tree("birch"), 1)]

        def build(seed, warm):
            directory, save = self.world()
            b = Builder(save, seed=seed)
            if warm:
                b.road([(-15, -12), (-3, -12)], width=1, mix=LANE_MIX)
                b.ornamental_tree(-8, -5, kind="oak")
            drawn = b.rng.getstate()
            r = b.scatter((0, 0, 40, 40), things, spacing=4, density=0.5,
                          seed=11)
            self.assertEqual(b.rng.getstate(), drawn, "nothing drawn")
            save.save()
            return directory, r

        first, r1 = build(0, False)
        second, r2 = build(99, True)
        self.assertEqual(r1.planted, r2.planted)
        self.assertEqual(r1.kinds, r2.kinds)
        self.assertEqual(r1.skipped, r2.skipped)
        self.assertEqual((r1.thinned, r1.crowded), (r2.thinned, r2.crowded))
        self.assertEqual(set(r1.kinds), {POPPY, DANDELION, "tree:birch"})
        self.assertTrue(self.region(first) == self.region(second),
                        "the region planted in saves the same bytes")
        self.assertFalse(self.region(first, "r.-1.-1.mca")
                         == self.region(second, "r.-1.-1.mca"),
                         "the warm-up writes elsewhere")

    def test_the_order_of_calls_and_of_the_area_does_not_matter(self):
        """Two scatters over two chunks, called in either order, plant what
        each plants alone and save the same bytes; and an area given as a
        rectangle or as its columns reversed, or twice over, is one area."""
        def build(order):
            directory, save = self.world()
            b = Builder(save)
            results = {}
            for name in order:
                area = (0, 0, 15, 15) if name == "a" else (16, 0, 31, 15)
                results[name] = b.scatter(area, [(POPPY, 1), (DANDELION, 1)],
                                          density=0.3, spacing=2, seed=5)
            save.save()
            return directory, results

        ab_dir, ab = build("ab")
        ba_dir, ba = build("ba")
        for name in "ab":
            self.assertEqual(ab[name].planted, ba[name].planted)
        self.assertTrue(self.region(ab_dir) == self.region(ba_dir))

        _, save = self.world()
        rect = Builder(save).scatter((0, 0, 15, 15), [(POPPY, 1), (DANDELION, 1)],
                                     density=0.3, spacing=2, seed=5)
        self.assertEqual(rect.planted, ab["a"].planted)
        columns = [(x, z) for z in range(16) for x in range(16)]
        for given in (columns[::-1], set(columns), columns + columns):
            _, save = self.world()
            r = Builder(save).scatter(given, [(POPPY, 1), (DANDELION, 1)],
                                      density=0.3, spacing=2, seed=5)
            self.assertEqual(r.planted, rect.planted)
            self.assertEqual(r.kinds, rect.kinds)

    def test_the_keyed_rolls_are_pinned(self):
        """A 16 by 16 lawn, poppies and dandelions alike, a quarter of the
        columns kept, seed 7: the very columns, pinned, so a change to a
        salt, the key or the order the dice are read in fails here. A
        Tree's trunk is four or five blocks by its own die, pinned too."""
        _, save = self.world()
        b = Builder(save)
        r = b.scatter((0, 0, 15, 15), [(POPPY, 1), (DANDELION, 1)],
                      density=0.25, seed=7)
        got = tuple((x, z, save.get_block(x, 64, z).name[10:])
                    for x, _, z in r.planted)
        self.assertEqual(got, PINNED_LAWN)
        self.assertEqual(r.thinned, 256 - len(PINNED_LAWN))

        _, save = self.world()
        r = Builder(save).scatter((0, 0, 31, 31), Tree("oak"), spacing=7,
                                  seed=3)
        trunks = tuple((x, z, sum(1 for y in range(64, 70)
                                  if save.get_block(x, y, z).name
                                  == "minecraft:oak_log"))
                       for x, _, z in r.planted)
        self.assertEqual(trunks, PINNED_GROVE)

    def test_the_clumped_rolls_are_pinned(self):
        """Poppies and dandelions in clumps of four, three in ten of them
        kept, seed 7, drawn as a picture of the lawn: so a change to the
        patches' jitter, the cover die or the kind picked per patch fails
        here, which the unclumped lawn above never rolls."""
        _, save = self.world()
        r = Builder(save).scatter((0, 0, 15, 15), [(POPPY, 1), (DANDELION, 1)],
                                  clump=4, cover=0.3, seed=7)
        self.assertEqual(_picture(save, 16, 16), PINNED_CLUMPS)
        self.assertEqual(r.kinds, {POPPY: 57, DANDELION: 34})
        self.assertEqual(r.thinned, 256 - 91)

    def test_a_mix_is_keyed_on_its_seed_and_clump(self):
        """A Mix's picks are its own, but the dice that thin, order and
        place a scatter of it are keyed on the whole Mix -- its entries,
        seed and clump, which its picks read -- so a Mix built again the
        same plants the same columns, one with another seed or clump other
        columns, and the planting is pinned."""
        def mix(seed=4, clump=2):
            return Mix([(POPPY, 2), (DANDELION, 1),
                        ("minecraft:cornflower", 1)], seed=seed, clump=clump)

        def plant(things):
            _, save = self.world()
            r = Builder(save).scatter((0, 0, 7, 7), things, density=0.5,
                                      seed=7)
            return save, r

        save, r = plant(mix())
        self.assertEqual(_picture(save, 8, 8), PINNED_MIX)
        self.assertEqual(r.thinned, 33)
        self.assertEqual(plant(mix())[1].columns, r.columns)
        self.assertNotEqual(plant(mix(seed=5))[1].columns, r.columns)
        self.assertNotEqual(plant(mix(clump=3))[1].columns, r.columns)


def _picture(save, width, depth):
    """The lawn's cells over the grass, a row per ``z`` from 0: ``p`` a
    poppy, ``d`` a dandelion, ``c`` a cornflower, ``.`` air."""
    letters = {"minecraft:air": ".", POPPY: "p", DANDELION: "d",
               "minecraft:cornflower": "c"}
    return tuple("".join(letters.get(save.get_block(x, 64, z).name, "?")
                         for x in range(width)) for z in range(depth))


#: Read off the rule as written, 2026-10-22: ``(x, z, flower)`` in the
#: order planted, and ``(x, z, trunk height)``; then two lawns as
#: :func:`_picture` draws them.
PINNED_LAWN = ((2, 0, 'dandelion'), (3, 0, 'poppy'), (9, 0, 'poppy'),
               (11, 1, 'dandelion'), (13, 1, 'poppy'), (14, 1, 'dandelion'),
               (15, 1, 'dandelion'), (0, 2, 'poppy'), (1, 2, 'dandelion'),
               (6, 2, 'dandelion'), (9, 2, 'dandelion'), (12, 2, 'poppy'),
               (14, 2, 'dandelion'), (15, 2, 'poppy'), (12, 3, 'dandelion'),
               (15, 3, 'dandelion'), (4, 4, 'poppy'), (6, 4, 'dandelion'),
               (7, 4, 'dandelion'), (15, 4, 'dandelion'),
               (0, 5, 'dandelion'), (1, 5, 'dandelion'), (2, 5, 'dandelion'),
               (3, 5, 'dandelion'), (5, 5, 'dandelion'),
               (11, 5, 'dandelion'), (12, 5, 'dandelion'), (3, 6, 'poppy'),
               (5, 6, 'poppy'), (6, 6, 'poppy'), (10, 6, 'poppy'),
               (11, 6, 'dandelion'), (4, 7, 'poppy'), (5, 7, 'dandelion'),
               (1, 8, 'poppy'), (2, 8, 'dandelion'), (3, 8, 'poppy'),
               (5, 8, 'poppy'), (14, 8, 'dandelion'), (11, 9, 'poppy'),
               (14, 9, 'dandelion'), (2, 10, 'poppy'), (4, 10, 'dandelion'),
               (6, 10, 'poppy'), (12, 10, 'dandelion'), (0, 11, 'dandelion'),
               (9, 11, 'poppy'), (12, 11, 'poppy'), (15, 11, 'dandelion'),
               (1, 12, 'poppy'), (10, 12, 'poppy'), (11, 12, 'dandelion'),
               (13, 12, 'dandelion'), (14, 12, 'poppy'),
               (0, 13, 'dandelion'), (5, 13, 'dandelion'), (8, 13, 'poppy'),
               (14, 13, 'dandelion'), (15, 13, 'poppy'), (2, 14, 'poppy'),
               (6, 14, 'poppy'), (8, 14, 'dandelion'), (12, 14, 'dandelion'),
               (2, 15, 'dandelion'), (3, 15, 'dandelion'),
               (4, 15, 'dandelion'), (8, 15, 'poppy'), (10, 15, 'dandelion'),
               (11, 15, 'dandelion'), (12, 15, 'dandelion'),
               (15, 15, 'dandelion'))
PINNED_GROVE = ((25, 0, 4), (10, 3, 4), (17, 3, 4), (2, 5, 4), (31, 5, 4),
                (23, 8, 4), (15, 14, 4), (8, 15, 4), (27, 15, 5), (0, 16, 5),
                (25, 22, 5), (9, 24, 4), (1, 25, 5), (18, 27, 5),
                (28, 29, 4), (5, 31, 5), (12, 31, 4))
PINNED_CLUMPS = (".......pp.......",
                 ".....pppp.......",
                 "....dppppp......",
                 "....dppppp...d..",
                 "dddddpppp..dddd.",
                 "dddd........ddd.",
                 "dddd........dd..",
                 "ddd.........dd..",
                 ".pp.............",
                 "pppp...........p",
                 "pppp............",
                 "ppppp...........",
                 "ppppp...........",
                 "ppppp...........",
                 "ppppp..........p",
                 "pppp......dddd.p")
PINNED_MIX = ("d.d...dc",
              "d.....d.",
              "pp..p...",
              "p.c.p..d",
              "pp.p.p.p",
              ".p..pppc",
              ".pppc...",
              ".ppp...c")


class SpacingTests(ScatterBase):

    def test_spacing_is_kept_and_the_planting_is_maximal(self):
        """No two plantings closer than the spacing, center to center, and
        no column left that a planting could have taken: every column of
        the lawn not planted is within the spacing of one that is, and it
        is counted as crowded."""
        _, save = self.world()
        r = Builder(save).scatter((0, 0, 31, 31), POPPY, spacing=4, seed=2)
        cols = r.columns
        self.assertGreater(len(cols), 40)
        for i, (ax, az) in enumerate(cols):
            for bx, bz in cols[i + 1:]:
                self.assertGreaterEqual((ax - bx) ** 2 + (az - bz) ** 2, 16)
        planted = set(cols)
        for x in range(32):
            for z in range(32):
                if (x, z) not in planted:
                    self.assertTrue(any((x - px) ** 2 + (z - pz) ** 2 < 16
                                        for px, pz in cols), (x, z))
        self.assertEqual(r.crowded, 32 * 32 - len(cols))
        self.assertEqual(r.planted, tuple(sorted(
            r.planted, key=lambda c: (c[2], c[0]))), "written in (z, x) order")

    def test_apart_from_keeps_spacing_from_earlier_plantings(self):
        """A grove first, then flowers kept three blocks from every trunk
        by the grove's own columns: none stands within three of a trunk,
        and the flowers fill the lawn elsewhere. Under the canopy is lawn
        like any other, the leaves three blocks up."""
        _, save = self.world()
        b = Builder(save)
        grove = b.scatter((0, 0, 31, 31), Tree("cherry"), spacing=8, seed=4)
        flowers = b.scatter((0, 0, 31, 31), POPPY, spacing=3,
                            apart_from=grove.columns, seed=4)
        for fx, fz in flowers.columns:
            for tx, tz in grove.columns:
                self.assertGreaterEqual((fx - tx) ** 2 + (fz - tz) ** 2, 9)
        self.assertGreater(len(flowers.planted), 60)
        self.assertEqual(set(flowers.skipped), {"occupied"})
        self.assertEqual(set(flowers.skipped["occupied"]), set(grove.columns),
                         "each trunk stands in its own flower's cell")

    def test_a_planting_that_cannot_be_written_crowds_out_nobody(self):
        """Trees along the world's edge: a trunk whose canopy box would
        reach the chunk past it is passed over as one that cannot be
        written, and crowds out nobody -- every column neither planted nor
        passed over for a reason is within the spacing of a trunk. The
        rest is planted, and the call is not refused for the few."""
        _, save = self.world()
        b = Builder(save)
        r = b.scatter((36, 0, 47, 31), Tree("oak"), spacing=3, seed=1)
        edge = r.skipped["cannot be written"]
        self.assertTrue(edge)
        self.assertEqual({x for x, _ in edge} - {46, 47}, set())
        self.assertEqual(set(r.skipped), {"cannot be written"})
        self.assertTrue(all(x <= 45 for x, _ in r.columns))
        passed = set(edge)
        for x in range(36, 48):
            for z in range(32):
                if (x, z) in passed or (x, z) in r.columns:
                    continue
                self.assertTrue(any((x - px) ** 2 + (z - pz) ** 2 < 9
                                    for px, pz in r.columns), (x, z))
        self.assertEqual(r.crowded, 12 * 32 - len(r.columns) - len(edge))

    def test_trees_closer_than_three_are_refused(self):
        """A Tree with ``spacing`` under 3 is an ``NBTError`` before any
        column is read -- over ground that is not there, which a read
        would have refused as no ground."""
        _, save = self.world()
        b = Builder(save)
        for spacing in (1, 2):
            with self.assertRaises(NBTError) as caught:
                b.scatter((500, 500, 510, 510), [(Tree("oak"), 1), (POPPY, 3)],
                          spacing=spacing)
            self.assertIs(type(caught.exception), NBTError)
            self.assertIn("spacing of 3", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)


class PassOverTests(ScatterBase):
    """Where nothing is planted, and why it is counted."""

    def test_never_on_water_paths_or_keep_outs(self):
        """A pond, a lane laid this session -- coarse dirt among its
        surface, which is soil -- a reservation and a pad: nothing is
        planted on any of them, each column counted under its reason, and
        a reservation named in ``through`` is planted like any lawn."""
        def shape(save):
            save.fill((2, 62, 2), (5, 63, 5), WATER)
            save.set_block(3, 63, 8, "minecraft:ice")       # a sheet, over
            save.set_block(3, 62, 8, WATER)                 # water
        _, save = self.world(shape)
        b = Builder(save, seed=3)
        lane = b.road([(0, 12), (31, 12)], width=3, mix=LANE_MIX)
        laid = {self.name(save, x, lane.top(x, z), z) for x, z in lane.swath}
        self.assertIn("minecraft:coarse_dirt", laid)
        b.reserve(20, 0, 24, 4, name="grove")
        b.level_pad(26, 2, 30, 6, 63, name="pad", wall=None)
        r = b.scatter((0, 0, 31, 31), POPPY, avoid={(10, 20), (11, 20)})
        pond = {(x, z) for x in range(2, 6) for z in range(2, 6)} | {(3, 8)}
        paved = {c for c in lane.swath if 0 <= c[0] <= 31 and 0 <= c[1] <= 31}
        kept = ({(x, z) for x in range(20, 25) for z in range(5)}
                | {(x, z) for x in range(26, 31) for z in range(2, 7)})
        self.assertEqual(set(r.skipped["water"]), pond)
        self.assertEqual(set(r.skipped["paved"]), paved)
        self.assertEqual(set(r.skipped["keep-out"]), kept)
        self.assertEqual(r.skipped["avoided"], ((10, 20), (11, 20)))
        self.assertEqual(list(r.skipped), [reason for reason in SKIP_REASONS
                                           if reason in r.skipped])
        self.assertEqual(set(r.columns) & (pond | paved | kept), set())
        self.assertEqual(len(r.columns), 32 * 32 - len(pond | paved | kept) - 2)
        for x, z in pond | paved | kept:
            self.assertNotEqual(self.name(save, x, 64, z), POPPY, (x, z))

        _, save = self.world(shape)
        b = Builder(save)
        b.reserve(20, 0, 24, 4, name="grove")
        r = b.scatter((20, 0, 24, 4), POPPY, through=("grove",))
        self.assertEqual(len(r.planted), 25)

    def test_a_canopy_keeps_out_of_keep_outs_paths_and_claims(self):
        """A tree is its canopy as well as its trunk. Beside a reservation,
        every trunk whose leaves would reach into it is passed over as kept
        out, and nothing is grown there, unless the reservation is named in
        ``through``; a trunk in a dip beside a road would hang its leaves in
        the road's head room, and is passed over as paved; a canopy over an
        avoided column is avoided -- but not across the corner its leaves
        never fill -- and one reaching into a flight's head room is
        claimed."""
        _, save = self.world()
        b = Builder(save)
        b.reserve(10, 0, 20, 20, name="shrine")
        r = b.scatter((0, 0, 9, 9), Tree("oak"), spacing=3, seed=1)
        self.assertTrue(r.planted)
        self.assertEqual(r.skipped, {"keep-out": tuple(
            (x, z) for z in range(10) for x in (8, 9))})
        for x in range(10, 21):
            for z in range(21):
                for y in range(64, 72):
                    self.assertEqual(self.name(save, x, y, z), "minecraft:air",
                                     (x, y, z))
        _, save = self.world()
        b = Builder(save)
        b.reserve(10, 0, 20, 20, name="shrine")
        r = b.scatter([(8, 5)], Tree("oak"), spacing=3, through=("shrine",))
        self.assertEqual(r.columns, ((8, 5),))
        self.assertEqual(self.name(save, 10, 66, 5), "minecraft:oak_leaves")

        def dip(save):
            save.fill((8, 61, 0), (12, 63, 8), "minecraft:air")
            save.fill((8, 61, 0), (12, 61, 8), GRASS)
        _, save = self.world(dip)
        b = Builder(save)
        road = b.road([(0, 10), (30, 10)], width=3)
        r = b.scatter([(10, 2), (10, 7)], Tree("oak"), spacing=3)
        self.assertEqual(r.columns, ((10, 2),))
        self.assertEqual(r.skipped, {"paved": ((10, 7),)})
        for x, z in road.swath:
            for y in range(road.top(x, z) + 1, road.top(x, z) + 4):
                self.assertEqual(self.name(save, x, y, z), "minecraft:air",
                                 (x, y, z))

        _, save = self.world()
        r = Builder(save).scatter([(4, 20), (4, 26)], Tree("oak"), spacing=3,
                                  avoid={(6, 21), (6, 28)})
        self.assertEqual(r.columns, ((4, 26),))
        self.assertEqual(r.skipped, {"avoided": ((4, 20),)})

        _, save = self.world()
        b = Builder(save)
        b.flight(StraightStair((5, 64, 10), "north", 4), walk=False)
        r = b.scatter([(7, 9), (8, 9)], Tree("oak"), spacing=3)
        self.assertEqual(r.columns, ((8, 9),))
        self.assertEqual(r.skipped, {"claimed": ((7, 9),)})
        self.assertFalse([cell for cell in b._claimed
                          if save.get_block(*cell).name
                          == "minecraft:oak_leaves"])
        from heaton_voxel.craft.build.planting import _CANOPY, _TREE_CELLS
        self.assertEqual(len(_CANOPY), 21)
        self.assertEqual({(dx, dz) for dx, _, dz in _TREE_CELLS[5]},
                         set(_CANOPY), "one canopy at either trunk height")

    def test_columns_with_no_ground_are_passed_over(self):
        """An area running off the generated world plants the half that is
        there and passes the rest over as no ground, rather than refusing
        the call for the chunks that are missing."""
        _, save = self.world()
        r = Builder(save).scatter((40, 0, 55, 1), POPPY)
        self.assertEqual(r.columns, tuple((x, z) for z in range(2)
                                          for x in range(40, 48)))
        self.assertEqual(r.skipped, {"no ground": tuple(
            (x, z) for z in range(2) for x in range(48, 56))})



class ScatterSiteTests(SyntheticSiteCase):
    """A scatter inside a worksite, on the worksite tests' own stone
    world."""

    def test_a_worksite_zone_passes_a_planting_over(self):
        """Boulders scattered over a site with a protected zone in it: the
        zone's columns are passed over as refused by the worksite, the zone
        stays as it was, and every other column is planted."""
        site = self.open_site()
        site.protect((6, 64, 6), (8, 66, 8), name="shrine")
        b = site.builder()
        r = b.scatter((0, 0, 15, 15), BOULDER)
        zone = tuple((x, z) for z in range(6, 9) for x in range(6, 9))
        self.assertEqual(r.skipped, {"refused by worksite": zone})
        self.assertEqual(len(r.planted), 256 - 9)
        for x, z in zone:
            self.assertEqual(site.save.get_block(x, 64, z).name,
                             "minecraft:air")
        self.assertEqual(site.check(), [])


class OccupancyTests(ScatterBase):
    """A planting stands only in cells that hold nothing, unless told to
    replace ground cover."""

    def test_tall_plants_are_placed_whole_and_need_their_upper_cell(self):
        """A lilac gets both its halves; one with a leaf in its upper cell
        -- under a canopy, which is never the ground -- is passed over as
        occupied, and so is one in a flower's cell."""
        def shape(save):
            save.set_block(2, 65, 0, "minecraft:oak_leaves[persistent=true]")
            save.set_block(4, 64, 0, POPPY)
        _, save = self.world(shape)
        b = Builder(save)
        r = b.scatter((0, 0, 5, 0), "minecraft:lilac")
        self.assertEqual(r.columns, ((0, 0), (1, 0), (3, 0), (5, 0)))
        self.assertEqual(r.skipped, {"occupied": ((2, 0), (4, 0))})
        for x, _ in r.columns:
            self.assertEqual(str(save.get_block(x, 64, 0)),
                             "minecraft:lilac[half=lower]")
            self.assertEqual(str(save.get_block(x, 65, 0)),
                             "minecraft:lilac[half=upper]")
        self.assertClean(save)

    def test_what_is_placed_whole_is_place_blocks_tall_plants(self):
        """The things scatter plants as a placement -- both halves -- are
        exactly the plants among place_block's two-block ones, read off
        the game's default states (a lower ``half``) rather than its
        list."""
        from heaton_voxel.block_defaults import BLOCK_DEFAULTS
        from heaton_voxel.craft.build.planting import _Thing
        from heaton_voxel.place import _TALL_PLANTS
        tall = set()
        for name in BLOCK_DEFAULTS:
            try:
                thing = _Thing(name)
            except NBTError:
                continue
            if thing.tall:
                tall.add(name)
        self.assertEqual(tall, {"minecraft:sunflower", "minecraft:lilac",
                                "minecraft:rose_bush", "minecraft:peony",
                                "minecraft:tall_grass",
                                "minecraft:large_fern"})
        self.assertLessEqual(tall, _TALL_PLANTS)

    def test_overwrite_replaces_cover_and_clears_its_other_half(self):
        """Tall grass, lying snow and a fern stand in the way of a poppy,
        and of nothing it may replace without ``overwrite=True``. With it,
        the poppy takes their cells and the grass's upper half goes with
        its lower, leaving no lone half; a torch somebody set is still in
        the way."""
        def shape(save):
            save.set_block(0, 64, 0, "minecraft:tall_grass[half=lower]")
            save.set_block(0, 65, 0, "minecraft:tall_grass[half=upper]")
            save.set_block(1, 64, 0, "minecraft:snow[layers=1]")
            save.set_block(2, 64, 0, "minecraft:fern")
            save.set_block(3, 64, 0, "minecraft:torch")
        _, save = self.world(shape)
        b = Builder(save)
        with self.assertRaises(Refused):
            b.scatter((0, 0, 3, 0), POPPY)
        self.assertEqual(b.blocks_set, 0)
        r = b.scatter((0, 0, 3, 0), POPPY, overwrite=True)
        self.assertEqual(r.columns, ((0, 0), (1, 0), (2, 0)))
        self.assertEqual(r.skipped, {"occupied": ((3, 0),)})
        for x in range(3):
            self.assertEqual(self.name(save, x, 64, 0), POPPY)
        self.assertEqual(self.name(save, 0, 65, 0), "minecraft:air")
        self.assertClean(save)

    def test_the_ground_is_snowy_as_what_stands_on_it_makes_it(self):
        """A grass block, podzol or mycelium is ``snowy`` exactly when a
        block of the ``snow`` tag stands on it, which the game's block
        update sets when a planting goes in: so a poppy or a trunk taking a
        snow layer's place leaves the ground under it unsnowed, a poppy on
        a stale ``snowy=true`` with nothing over it too, a snow block
        boulder snows the grass it stands on, and dirt, which has no flag,
        is left as it was. The copies of the tag and of the blocks with
        the flag are the lower tier's, the ones unfreeze reads."""
        from heaton_voxel.block_defaults import BLOCK_DEFAULTS
        from heaton_voxel.craft.build.planting import _SNOW_TAG
        from heaton_voxel.world import editing
        self.assertEqual(_SNOW_TAG, editing._SNOW_TAG)
        self.assertEqual({name for name, defaults in BLOCK_DEFAULTS.items()
                          if "snowy" in defaults}, set(editing._SNOWY_TOPS))

        def shape(save):
            for x, ground in ((0, "minecraft:grass_block[snowy=true]"),
                              (1, "minecraft:podzol[snowy=true]"),
                              (2, "minecraft:mycelium[snowy=true]")):
                save.set_block(x, 63, 0, ground)
            save.set_block(0, 64, 0, "minecraft:snow[layers=1]")
            save.set_block(1, 64, 0, "minecraft:snow[layers=1]")
            save.set_block(8, 63, 8, "minecraft:grass_block[snowy=true]")
            save.set_block(8, 64, 8, "minecraft:snow[layers=1]")
            save.set_block(1, 63, 4, DIRT)
        _, save = self.world(shape)
        b = Builder(save)
        r = b.scatter((0, 0, 3, 0), POPPY, overwrite=True)
        self.assertEqual(len(r.planted), 4)
        self.assertEqual([str(save.get_block(x, 63, 0)) for x in range(4)],
                         ["minecraft:grass_block[snowy=false]",
                          "minecraft:podzol[snowy=false]",
                          "minecraft:mycelium[snowy=false]", GRASS])
        b.scatter([(8, 8)], Tree("oak"), spacing=3, overwrite=True)
        self.assertEqual(self.name(save, 8, 64, 8), "minecraft:oak_log")
        self.assertEqual(str(save.get_block(8, 63, 8)), GRASS)
        b.scatter((0, 4, 1, 4), "minecraft:snow_block")
        self.assertEqual(str(save.get_block(0, 63, 4)),
                         "minecraft:grass_block[snowy=true]")
        self.assertEqual(str(save.get_block(1, 63, 4)), DIRT)
        self.assertClean(save)

    def test_cover_something_stands_on_is_not_taken(self):
        """Sugar cane two high beside water: a boulder told to overwrite
        cover takes a lone cane, but not the foot of a stack, since the
        cane over it would be left on stone the game drops it from."""
        def shape(save):
            save.fill((0, 63, 0), (3, 63, 0), SAND)
            save.fill((0, 63, 1), (3, 63, 1), WATER)
            save.set_block(0, 64, 0, "minecraft:sugar_cane")
            save.set_block(2, 64, 0, "minecraft:sugar_cane")
            save.set_block(2, 65, 0, "minecraft:sugar_cane")
        _, save = self.world(shape)
        b = Builder(save)
        r = b.scatter((0, 0, 3, 0), BOULDER, overwrite=True)
        self.assertEqual(r.columns, ((0, 0), (1, 0), (3, 0)))
        self.assertEqual(r.skipped, {"occupied": ((2, 0),)})
        self.assertEqual(self.name(save, 2, 65, 0), "minecraft:sugar_cane")
        self.assertClean(save)


class RefusalTests(ScatterBase):

    def test_a_call_that_plants_nothing_is_refused(self):
        """A lawn of stone takes no poppy: the call is refused, naming why
        every column was passed over, and nothing is written."""
        def shape(save):
            save.fill((0, 63, 0), (7, 63, 7), STONE)
        _, save = self.world(shape)
        b = Builder(save, seed=1)
        drawn = b.rng.getstate()
        with self.assertRaises(Refused) as caught:
            b.scatter((0, 0, 7, 7), POPPY)
        self.assertIn("nothing to plant", str(caught.exception))
        self.assertIn("64 off its soil", str(caught.exception))
        self.assertEqual(caught.exception.faults,
                         ["64 off its soil, first (0, 0)"])
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(b.rng.getstate(), drawn)
        with self.assertRaises(Refused) as caught:
            b.scatter((0, 0, 7, 7), POPPY, density=1e-9)
        self.assertIn("64 thinned", str(caught.exception))
        with self.assertRaises(Refused) as caught:
            b.scatter((300, 300, 302, 302), POPPY)        # no chunks there
        self.assertIs(type(caught.exception), Refused,
                      "passed over as no ground, not a NoSurface let out")
        self.assertEqual(caught.exception.faults,
                         ["9 no ground, first (300, 300)"])
        self.assertEqual(b.blocks_set, 0)

    def test_nether_trees_are_refused(self):
        """A crimson or warped Tree is refused as it is made: a fungus grows
        on nylium, a footing no tag of craft's holds it to. ornamental_tree
        still plants one where it is told."""
        for kind in ("crimson", "warped"):
            with self.assertRaises(NBTError) as caught:
                Tree(kind)
            self.assertIs(type(caught.exception), NBTError)
            self.assertIn("nylium", str(caught.exception))
        with self.assertRaises(NBTError):
            Tree("poplar")                 # no such log
        with self.assertRaises(TypeError):
            Tree("oak", leaves=Mix([("minecraft:oak_leaves", 1)]))
        _, save = self.world()
        Builder(save).ornamental_tree(4, 4, kind="crimson")
        self.assertEqual(self.name(save, 4, 64, 4), "minecraft:crimson_stem")

    def test_a_thing_craft_cannot_hold_to_its_footing_is_refused(self):
        """A cactus, bamboo, a mushroom, a lily pad, a pitcher plant, dry
        grass, a bamboo shoot, sand, a chest, a slab and a torch each ask
        something of what they stand on that nothing in craft copies, or
        can be lost to it: an ``NBTError`` before any column is read --
        over ground that is not there. A block the game lacks is its
        IllegalBlock, a Material or a bare list a TypeError, and a bad
        number an NBTError, all as early."""
        _, save = self.world()
        b = Builder(save)
        nowhere = (500, 500, 505, 505)
        for thing in ("minecraft:cactus", "minecraft:bamboo",
                      "minecraft:brown_mushroom", "minecraft:lily_pad",
                      "minecraft:pitcher_plant", "minecraft:short_dry_grass",
                      "minecraft:bamboo_sapling", SAND, "minecraft:chest",
                      "minecraft:oak_slab", "minecraft:torch",
                      [(POPPY, 1), ("minecraft:cactus", 1)],
                      Mix([(POPPY, 1), ("minecraft:red_mushroom", 1)])):
            with self.subTest(thing=thing):
                with self.assertRaises(NBTError) as caught:
                    b.scatter(nowhere, thing)
                self.assertIs(type(caught.exception), NBTError)
        with self.assertRaises(IllegalBlock):
            b.scatter(nowhere, "minecraft:poppyy")
        for thing in (Material("minecraft:bricks"), [POPPY, DANDELION], 5,
                      [(Mix([(POPPY, 1)]), 1)]):
            with self.subTest(thing=thing):
                with self.assertRaises(TypeError):
                    b.scatter(nowhere, thing)
        for kwargs in ({"density": 0}, {"density": 1.5}, {"cover": True},
                       {"spacing": 0}, {"spacing": 2.0}, {"clump": 0},
                       {"clump": 2000}):
            with self.subTest(**kwargs):
                with self.assertRaises(NBTError) as caught:
                    b.scatter(nowhere, POPPY, **kwargs)
                self.assertIs(type(caught.exception), NBTError)
        for area, thing in ((nowhere, [(POPPY, 0)]), (nowhere, []),
                            ((0, 0, 1), POPPY), ([], POPPY)):
            with self.subTest(area=area, thing=thing):
                with self.assertRaises(NBTError) as caught:
                    b.scatter(area, thing)
                self.assertIs(type(caught.exception), NBTError)
        self.assertEqual(b.blocks_set, 0)

    def test_a_modded_thing_is_refused_under_allow(self):
        """``Builder(allow=)`` lets a modded id be written, but what keeps a
        modded plant or stone is no rule craft copies, whatever its name
        says -- so a modded poppy, a modded rock and an allowed id the game
        does not have are each an ``NBTError`` before any column is read,
        never judged as the vanilla block their name ends in, or as a cube
        planted anywhere."""
        _, save = self.world()
        b = Builder(save, allow=("examplemod:", "minecraft:fancy_poppy"))
        for thing in ("examplemod:poppy", "examplemod:rock",
                      "minecraft:fancy_poppy"):
            with self.subTest(thing=thing):
                with self.assertRaises(NBTError) as caught:
                    b.scatter((500, 500, 505, 505), thing)
                self.assertIs(type(caught.exception), NBTError)
                self.assertIn("by guess", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)

    def test_a_boulder_that_melts_is_refused(self):
        """Ice, frosted ice and a snow layer are sturdy on every face (a
        snow layer at eight layers) and the audit asks nothing of them, but
        the game melts each where it stands -- ice into a water source on
        the lawn -- so each is an ``NBTError`` before any column is read.
        Packed and blue ice keep, and are planted; every block on the list
        is one the sturdy rule alone would have let through."""
        from heaton_voxel.craft.block_facts import facts
        from heaton_voxel.craft.build.planting import _ALL_FACES, _MELTS
        from heaton_voxel.block_defaults import BLOCK_DEFAULTS
        _, save = self.world()
        b = Builder(save)
        for thing in ("minecraft:ice", "minecraft:frosted_ice",
                      "minecraft:snow[layers=8]", "minecraft:snow"):
            with self.subTest(thing=thing):
                with self.assertRaises(NBTError) as caught:
                    b.scatter((500, 500, 505, 505), thing)
                self.assertIs(type(caught.exception), NBTError)
                self.assertIn("melts", str(caught.exception))
        r = b.scatter((0, 0, 3, 3), [("minecraft:packed_ice", 1),
                                     ("minecraft:blue_ice", 1)])
        self.assertEqual(len(r.planted), 16)
        for bare in _MELTS:
            with self.subTest(stale=bare):
                name = f"minecraft:{bare}"
                whole = ({"layers": "8"} if "layers"
                         in BLOCK_DEFAULTS.get(name, {}) else {})
                self.assertEqual(facts(name, whole).sturdy, _ALL_FACES)

    def test_the_whole_plan_is_audited(self):
        """Boulders told they may stand on anything, over a chest: a stone
        on a chest shuts it, which the survival audit refuses for the
        whole call, naming it, before anything is written."""
        def shape(save):
            save.set_block(3, 63, 3, "minecraft:chest[facing=north]")
        _, save = self.world(shape)
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.scatter((0, 0, 7, 7), STONE, on=lambda name: True)
        self.assertEqual([(f.cell, f.kind) for f in caught.exception.faults],
                         [((3, 63, 3), "blocked chest")])
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(self.name(save, 0, 64, 0), "minecraft:air")


class TreeTests(ScatterBase):

    def test_a_scattered_tree_is_an_ornamental_tree(self):
        """A grove of one, and an ornamental tree of the same wood and
        trunk height planted by hand at its column, save the same bytes;
        so do an oak and a birch three apart, their canopies reaching over
        each other, and ornamental_tree at each in (z, x) order -- the
        first wins every leaf both could grow, so the oak's leaves stand
        where the birch's would."""
        def trunk(save, x, z):
            log = save.get_block(x, 64, z).name
            return log, sum(1 for y in range(64, 71)
                            if save.get_block(x, y, z).name == log)

        trees = [(Tree("oak"), 1), (Tree("birch"), 1)]
        for area in ([(8, 8)], [(8, 8), (11, 8)], [(8, 8), (11, 9)]):
            with self.subTest(area=area):
                scattered, save = self.world()
                r = Builder(save).scatter(area, trees, spacing=3, seed=5)
                self.assertEqual(r.columns, tuple(sorted(
                    area, key=lambda c: (c[1], c[0]))))
                grown = [trunk(save, x, z) for x, z in r.columns]
                if len(area) == 2:
                    self.assertEqual([log for log, _ in grown],
                                     ["minecraft:oak_log", "minecraft:birch_log"])
                    self.assertTrue(any(
                        save.get_block(x, y, z).name == "minecraft:oak_leaves"
                        for x in (9, 10) for z in (8, 9) for y in range(64, 72)),
                        "the oak's canopy reaches into the birch's")
                save.save()
                by_hand, save = self.world()
                heights = [height - 4 for _, height in grown]
                seed = next(s for s in range(200)
                            if _rolls(s, len(heights)) == heights)
                b = Builder(save, seed=seed)
                for (x, z), (log, _) in zip(r.columns, grown):
                    b.ornamental_tree(x, z, kind=log[10:-4])
                save.save()
                self.assertTrue(self.region(scattered) == self.region(by_hand))

    def test_a_tree_is_its_kind_and_its_canopy_state(self):
        """Two Trees are one when they grow one tree: the wood's own leaves
        named outright, as text or as a state, are the wood's own -- equal,
        hashed alike, counted as ``tree:oak`` and keyed alike, so they plant
        the same grove to the byte -- and a canopy's properties may come in
        any order; leaves without ``persistent``, or another wood, are
        another tree."""
        own = Tree("oak")
        named = "minecraft:oak_leaves[persistent=true]"
        for spelled in (Tree("oak", leaves=named),
                        Tree("oak", leaves=BlockState(
                            "minecraft:oak_leaves", {"persistent": "true"}))):
            with self.subTest(spelled=repr(spelled)):
                self.assertEqual(spelled, own)
                self.assertEqual(hash(spelled), hash(own))
                self.assertEqual(str(spelled), "tree:oak")
        self.assertEqual(
            Tree("oak", leaves="minecraft:azalea_leaves[waterlogged=false,"
                               "persistent=true]"),
            Tree("oak", leaves="minecraft:azalea_leaves[persistent=true,"
                               "waterlogged=false]"))
        self.assertNotEqual(Tree("oak", leaves="minecraft:oak_leaves"), own)
        self.assertNotEqual(Tree("birch"), own)

        regions = []
        for tree in (own, Tree("oak", leaves=named)):
            directory, save = self.world()
            r = Builder(save).scatter((0, 0, 15, 15), tree, spacing=5, seed=1)
            self.assertEqual(r.kinds, {"tree:oak": len(r.planted)})
            save.save()
            regions.append((r.planted, self.region(directory)))
        self.assertTrue(regions[0] == regions[1], "one grove, to the byte")

    def test_trunks_take_both_heights(self):
        """Over a grove the trunk die gives both of ornamental_tree's
        heights, four and five, and the game keeps every tree."""
        _, save = self.world()
        r = Builder(save).scatter((0, 0, 40, 40), Tree("spruce"), spacing=5)
        heights = {sum(1 for y in range(64, 71)
                       if save.get_block(x, y, z).name == "minecraft:spruce_log")
                   for x, _, z in r.planted}
        self.assertEqual(heights, {4, 5})
        self.assertEqual(r.kinds, {"tree:spruce": len(r.planted)})
        self.assertClean(save)


def _rolls(seed, n):
    """The first ``n`` trunk dice ornamental_tree draws from a Builder
    seeded ``seed``."""
    rng = random.Random(seed)
    return [rng.randrange(2) for _ in range(n)]


class ClumpTests(ScatterBase):

    def test_clumps_plant_whole_patches(self):
        """``cover=0.3`` keeps about three columns in ten whichever the
        clump; at ``clump=4`` it keeps whole patches, so a planted column's
        neighbor is planted far more often than an independent die would
        give, while at ``clump=1`` about as often. ``density`` is rolled
        per column whatever the clump: ``density=0.3`` at ``clump=4``
        thins without clumping."""
        rates = {}
        for clump, share in ((1, "cover"), (4, "cover"), (4, "density")):
            _, save = self.world()
            r = Builder(save).scatter((-16, -16, 47, 47), POPPY, clump=clump,
                                      seed=6, **{share: 0.3})
            planted = set(r.columns)
            self.assertAlmostEqual(len(planted) / 64 ** 2, 0.3, delta=0.08)
            pairs = [(x + 1, z) in planted for x, z in planted if x < 47]
            rates[clump, share] = sum(pairs) / len(pairs)
        self.assertAlmostEqual(rates[1, "cover"], 0.3, delta=0.05)
        self.assertAlmostEqual(rates[4, "density"], 0.3, delta=0.05)
        self.assertGreater(rates[4, "cover"], 2 * rates[1, "cover"])

    def test_one_kind_per_patch(self):
        """From a list, one kind is picked per patch, as the park's clumps
        were one flower each: every planting in a patch is the same kind."""
        _, save = self.world()
        r = Builder(save).scatter((0, 0, 31, 31), [(POPPY, 1), (DANDELION, 1)],
                                  clump=4, cover=0.5, seed=8)
        by_patch = {}
        key = (8 ^ zlib.crc32(_canonical([(POPPY, 1), (DANDELION, 1)], None)
                              .encode("utf-8"))) & MASK
        for x, y, z in r.planted:
            cube = patch(x, 0, z, 4, key ^ _JITTER)
            by_patch.setdefault(cube, set()).add(save.get_block(x, y, z).name)
        self.assertGreater(len(by_patch), 10)
        self.assertTrue(all(len(kinds) == 1 for kinds in by_patch.values()))
        self.assertEqual(set().union(*by_patch.values()), {POPPY, DANDELION})

    def test_a_mix_is_picked_where_it_lands(self):
        """A Mix of flowers plants at each column its pick at ``(x, 0, z)``,
        as it would be picked anywhere else."""
        mix = Mix([(POPPY, 2), (DANDELION, 1), ("minecraft:cornflower", 1)],
                  seed=4, clump=2)
        _, save = self.world()
        r = Builder(save).scatter((0, 0, 15, 15), mix, density=0.5)
        self.assertGreater(len(r.planted), 60)
        for x, y, z in r.planted:
            self.assertEqual(save.get_block(x, y, z).name, mix.pick(x, 0, z))
        self.assertEqual(set(r.kinds), {POPPY, DANDELION, "minecraft:cornflower"})


if __name__ == "__main__":
    unittest.main()
