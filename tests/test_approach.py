"""Tests for Builder.approach: steps or a ramp up the ground, walked both
ways without a jump.

The park's gates were made walkable by lifting or cutting each column on
its own until no step was more than a block -- a jump at every step, in
the game, since a block's top is a whole block up and a walker rises 0.6
unbidden. From the park's north edge in JeffWorld (-9, 61, 172), read
2026-10-22, a walk that may not jump reaches 8 cells, the edge row and one
beside the gate; one that may reaches the road at z=168. An approach climbs by
stairs or by slabs and blocks, each row level across its width, cut and
filled to the profile that moves the least earth, and refuses -- before
anything is written -- what would leave it one-way, opened onto water,
laid over somebody's building or into another stair's head room.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest

from heaton_voxel.craft import (ApproachResult, Builder, Facade, Material, Mix,
                                Piece, StraightStair)
from heaton_voxel.craft.build import HEAD_ROOM
from heaton_voxel.craft.build.approach import _solve
from heaton_voxel.craft.errors import IllegalBlock, NoSurface, Refused
from heaton_voxel.craft.survey import Unsupported, reachable
from heaton_voxel.nbt import NBTError
from heaton_voxel.world import MinecraftSave
from tests.test_world import make_save

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHUNKS = [(x, z) for x in range(-1, 2) for z in range(-1, 2)]
DIRT = "minecraft:dirt"
GRASS = "minecraft:grass_block[snowy=false]"
STONE = "minecraft:stone"
BRICKS = "minecraft:stone_bricks"
AIR = "minecraft:air"
STAIRS = "minecraft:stone_brick_stairs"
SLAB = "minecraft:stone_brick_slab"
WATER = "minecraft:water[level=0]"
LAVA = "minecraft:lava[level=0]"
COBBLE = "minecraft:cobblestone"
LEAVES = "minecraft:oak_leaves[distance=7,persistent=true,waterlogged=false]"


def _stair(facing: str) -> str:
    return (f"{STAIRS}[facing={facing},half=bottom,shape=straight,"
            f"waterlogged=false]")


class ApproachBase(unittest.TestCase):
    """A lawn of grass at y=61 on dirt, three chunks square, and helpers to
    make another like it with something built on it."""

    def world(self, shape=None, *, chunks=CHUNKS):
        directory = tempfile.mkdtemp(prefix="approach-tests-")
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
        return save

    @staticmethod
    def bank(save, z1, top, *, x0=-16, x1=31, z0=-16):
        """Ground raised to ``top`` over ``z0..z1``: dirt, grass on it."""
        save.fill((x0, 61, z0), (x1, top - 1, z1), DIRT)
        save.fill((x0, top, z0), (x1, top, z1), GRASS)

    @classmethod
    def hump(cls, save):
        """Ground at 62 north of z=6, and a hump three over the lawn across
        z 6..10, x 0..10, that an approach from (5, 61, 12) to (5, 62, 2)
        cuts through two deep: its rows from z=10 on stand at 62."""
        cls.bank(save, 5, 62)
        save.fill((0, 61, 6), (10, 63, 10), DIRT)
        save.fill((0, 64, 6), (10, 64, 10), GRASS)

    def at(self, x, y, z) -> str:
        return str(self.save.get_block(x, y, z))

    def snapshot(self, low, high):
        return [str(self.save.get_block(x, y, z))
                for x in range(low[0], high[0] + 1)
                for y in range(low[1], high[1] + 1)
                for z in range(low[2], high[2] + 1)]

    def walks(self, start, end, lo, hi):
        """Whether ``end`` is walked to from ``start`` and home again,
        without a jump, over the save as written."""
        r = reachable(self.save, start, lo, hi, allow_jump=False)
        return end in r.reached and end not in r.stranded and not (
            r.stranded & {end, start})


class ApproachClimbTests(ApproachBase):

    def test_steps_up_a_six_block_rise_walk_both_ways(self):
        """A hillside rising six blocks over twelve rows, a block every two:
        steps three wide from the lawn to the top, every row one level
        from side to side and a stair facing north wherever it rises, the
        last flush with the top. Without a jump it is walked up and back
        down -- the hillside itself, a block a step, is not."""
        def hill(save):
            for k in range(6):
                self.bank(save, 11 - 2 * k, 62 + k)
        save = self.world(hill)
        start, end = (8, 61, 14), (8, 67, 0)
        self.assertFalse(self.walks(start, end, (6, 55, 0), (10, 72, 14)))
        b = Builder(save)
        r = b.approach(start, end, kind="steps", material=BRICKS, width=3)
        self.assertIsInstance(r, ApproachResult)
        self.assertEqual((r.start, r.end, r.heading, r.kind),
                         (start, end, "north", "steps"))
        levels = [level for level, _ in r.profile]
        self.assertEqual(len(levels), 13)
        self.assertEqual(levels[-1], 67, "flush with the high end")
        self.assertEqual([b - a for a, b in zip([61] + levels, levels)],
                         [0 if form == "block" else 1
                          for _, form in r.profile])
        self.assertEqual(r.treads, 6 * 3)
        for z, (y, form) in zip(range(13, 0, -1), r.profile):
            for x in (7, 8, 9):
                self.assertEqual(self.at(x, y, z), _stair("north")
                                 if form == "stairs" else BRICKS, (x, y, z))
                for above in range(y + 1, y + HEAD_ROOM + 1):
                    self.assertEqual(self.at(x, above, z), "minecraft:air")
        self.assertTrue(self.walks(start, end, (6, 55, 0), (10, 72, 14)))
        self.assertTrue(self.walks(end, start, (6, 55, 0), (10, 72, 14)))
        self.assertEqual(r.columns[:3], ((7, 13), (8, 13), (9, 13)))

    def test_a_ramp_rises_half_a_block_a_row(self):
        """A ramp of three rows climbs two blocks: a slab, a block, a slab,
        then the high end's block -- half a block every step, the most
        three rows can climb that way, walked both ways without a jump. Two
        rows cannot climb it, and are refused."""
        save = self.world(lambda s: self.bank(s, 6, 63))
        b = Builder(save)
        r = b.approach((5, 61, 10), (5, 63, 6), kind="ramp", material=BRICKS)
        slab = f"{SLAB}[type=bottom,waterlogged=false]"
        self.assertEqual(r.profile,
                         ((62, "slab"), (62, "block"), (63, "slab")))
        self.assertEqual([self.at(5, y, z) for z, (y, _) in
                          zip((9, 8, 7), r.profile)], [slab, BRICKS, slab])
        self.assertEqual((r.slabs, r.blocks, r.treads), (2, 1, 0))
        self.assertTrue(self.walks((5, 61, 10), (5, 63, 6), (4, 56, 6),
                                   (6, 68, 10)))
        self.assertTrue(self.walks((5, 63, 6), (5, 61, 10), (4, 56, 6),
                                   (6, 68, 10)))
        before = b.blocks_set
        with self.assertRaises(Refused) as caught:
            b.approach((12, 61, 9), (12, 63, 6), kind="ramp", material=BRICKS)
        self.assertIn("an approach of 2 rows climbs at most 1 block as a "
                      "ramp, half a block a row; this one climbs 2, from "
                      "y=61 to y=63. Move an end to give it 1 more row, or "
                      "lay steps, which climb a block a row",
                      str(caught.exception))
        self.assertEqual(b.blocks_set, before)

    def test_too_steep_is_refused_and_writes_nothing(self):
        """Three blocks over two rows as steps, and two over two as a ramp,
        are refused before anything is written, naming the rows and the
        rise."""
        save = self.world(lambda s: self.bank(s, 6, 64))
        before = self.snapshot((4, 58, 5), (6, 68, 10))
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.approach((5, 61, 9), (5, 64, 6), kind="steps", material=BRICKS)
        self.assertIn("an approach of 2 rows climbs at most 2 blocks as "
                      "steps; this one climbs 3, from y=61 to y=64",
                      str(caught.exception))
        self.assertIn("Move an end to give it 1 more row. Nothing",
                      str(caught.exception))
        save2 = self.world(lambda s: self.bank(s, 6, 63))
        with self.assertRaises(Refused) as caught:
            Builder(save2).approach((5, 61, 9), (5, 63, 6), kind="ramp",
                                    material=BRICKS)
        self.assertIn("climbs at most 1 block as a ramp",
                      str(caught.exception))
        self.save = save
        self.assertEqual(self.snapshot((4, 58, 5), (6, 68, 10)), before)
        self.assertEqual(b.blocks_set, 0)

    def test_a_steep_approach_is_a_straight_stair_on_earth(self):
        """With as many rows as blocks to climb, every row rises, and the
        treads are a StraightStair's from the low end's next row up --
        cell for cell and state for state, three wide."""
        save = self.world(lambda s: self.bank(s, 6, 65))
        r = Builder(save).approach((5, 61, 11), (5, 65, 6), kind="steps",
                                   material=Material("minecraft:stone_bricks"),
                                   width=3)
        stair = StraightStair((4, 62, 10), "north", 4, width=3, stair=STAIRS)
        want = {}
        for y in range(62, 66):
            for (x, z), state in stair.level(y).items():
                if state.name == STAIRS:
                    want[(x, y, z)] = str(state)
        got = {(x, y, z): self.at(x, y, z) for x in (4, 5, 6)
               for z in range(7, 11) for y in range(61, 67)
               if self.at(x, y, z).startswith(STAIRS)}
        self.assertEqual(got, want)
        self.assertEqual(r.treads, 12)
        self.assertEqual([form for _, form in r.profile], ["stairs"] * 4)

    def test_a_descending_approach_faces_up_toward_its_start(self):
        """Laid from the top down, the run is the same and its stairs face
        the way it climbs: back toward its start."""
        save = self.world(lambda s: self.bank(s, 6, 63))
        r = Builder(save).approach((5, 63, 6), (5, 61, 10), kind="steps",
                                   material=BRICKS)
        self.assertEqual(r.heading, "north")
        self.assertEqual(r.profile, ((63, "stairs"), (62, "stairs"),
                                     (61, "block")))
        self.assertEqual(self.at(5, 63, 7), _stair("north"))
        self.assertEqual(self.at(5, 62, 8), _stair("north"))
        self.assertTrue(self.walks((5, 63, 6), (5, 61, 10), (4, 56, 6),
                                   (6, 68, 10)))


class ApproachProfileTests(ApproachBase):

    def test_the_profile_takes_least_earthwork_and_rises_late_on_a_tie(self):
        """On flat ground up to a platform two blocks higher, six rows stay
        on the ground and rise in the last two. A bump in the run two
        blocks high is cut, not climbed: the profile stays monotone. And
        where rising first and rising last move the same earth -- a bump
        one high, then a dip, before an end one higher -- it rises last,
        the lowest profile row by row."""
        def platform(save):
            self.bank(save, 4, 63)
            save.fill((5, 62, 8), (5, 63, 8), DIRT)        # the bump
        save = self.world(platform)
        r = Builder(save).approach((5, 61, 11), (5, 63, 4), kind="steps",
                                   material=BRICKS)
        self.assertEqual([level for level, _ in r.profile],
                         [61, 61, 61, 61, 62, 63])
        self.assertEqual(self.at(5, 61, 8), BRICKS)
        for y in (62, 63, 64):
            self.assertEqual(self.at(5, y, 8), "minecraft:air", y)
        cost = [lambda u, g=g: abs(u - g) for g in (62, 61, 62)]
        self.assertEqual(_solve(cost, 61, 62, ramp=False, floors=[61] * 3,
                                lintel=False), [61, 61, 62])
        self.assertEqual(_solve(cost, 61, 62, ramp=False, floors=[61] * 3,
                                lintel=True), [61, 61, 62])

    def test_every_row_is_level_across_its_width(self):
        """Ground sloping across the run -- a block a column -- gives each
        row one level, the middle column's, from side to side: the park's
        south gate, cut column by column, reads 62, 61, 60, 59 down one
        column and 62, 63, 63, 63 down another."""
        def slope(save):
            save.fill((6, 62, -16), (6, 62, 31), GRASS)
            save.fill((6, 61, -16), (6, 61, 31), DIRT)
            save.fill((4, 61, -16), (4, 61, 31), "minecraft:air")
            save.fill((4, 60, -16), (4, 60, 31), GRASS)
            self.bank(save, 2, 64, x0=3, x1=7)
        save = self.world(slope)
        r = Builder(save).approach((5, 61, 9), (5, 64, 2), kind="steps",
                                   material=BRICKS, width=3)
        for z, (y, form) in zip(range(8, 2, -1), r.profile):
            want = _stair("north") if form == "stairs" else BRICKS
            self.assertEqual([self.at(x, y, z) for x in (4, 5, 6)], [want] * 3)
        self.assertEqual(self.at(4, 60, 8), GRASS, "filled up to the row")
        self.assertEqual(self.at(4, 61, 8), BRICKS)

    def test_the_third_cell_over_every_row_is_cut(self):
        """Head room is three cells, not two: a leaf three over a row's
        surface -- one the column reader stands under, so nothing but the
        head room asks for it -- is cut, since a walker stepping down onto
        that row from the one above has their head in it. The run then
        walks both ways inside its own column, where the stair is the only
        way back down."""
        def canopy(save):
            self.bank(save, 6, 64)
            save.set_block(5, 66, 8, LEAVES)
        save = self.world(canopy)
        b = Builder(save)
        self.assertEqual(b.ground(5, 8), 61)
        r = b.approach((5, 61, 10), (5, 64, 6), kind="steps", material=BRICKS)
        self.assertEqual(r.profile, ((62, "stairs"), (63, "stairs"),
                                     (64, "stairs")))
        self.assertEqual(self.at(5, 66, 8), "minecraft:air")
        box = ((5, 56, 6), (5, 70, 10))
        self.assertTrue(self.walks((5, 61, 10), (5, 64, 6), *box))
        self.assertTrue(self.walks((5, 64, 6), (5, 61, 10), *box))

        # The same rows with the leaf left in, two cells cut over each: the
        # round trip refuses them, since it walks the run's own column and
        # nothing beside it, where a walker turned back by the leaf could
        # step off the stair onto the lawn and call that the way home.
        save = self.world(canopy)
        stair = _stair("north")
        ops = [("set", (5, 62, 9), stair),
               ("fill", (5, 63, 9), (5, 65, 9), "minecraft:air"),
               ("fill", (5, 62, 8), (5, 62, 8), BRICKS),
               ("set", (5, 63, 8), stair),
               ("fill", (5, 64, 8), (5, 65, 8), "minecraft:air"),
               ("fill", (5, 62, 7), (5, 63, 7), BRICKS),
               ("set", (5, 64, 7), stair),
               ("fill", (5, 65, 7), (5, 67, 7), "minecraft:air")]
        with self.assertRaises(Refused) as caught:
            Builder(save)._walk_approach(
                ops, (5, 61, 10), (5, 64, 6), [(5, 9), (5, 8), (5, 7)],
                [(5, 62, 9), (5, 63, 8), (5, 64, 7)])
        self.assertIn("does not walk both ways", str(caught.exception))

    def test_head_room_over_rows_and_the_low_end(self):
        """Over a row: an overhang of natural stone two over it is cut,
        and the hollow under it filled down to the ground; an eave of oak
        planks there is somebody's, refused unless ``overwrite=True``. Over
        the low end: a lintel three over where you stand, with room under
        it -- a doorway's -- holds the first row level with the end, since a
        first row that rose would be a stair you climb from the doorstep and
        cannot come back down; with no row to spare it is refused, naming
        the low end."""
        def overhang(save):
            self.bank(save, 6, 64)
            save.fill((5, 65, 8), (5, 65, 8), STONE)
            save.fill((9, 65, 8), (9, 65, 8), "minecraft:oak_planks")
        save = self.world(overhang)
        b = Builder(save)
        r = b.approach((5, 61, 10), (5, 64, 6), kind="steps", material=BRICKS)
        self.assertEqual(r.profile, ((62, "stairs"), (63, "stairs"),
                                     (64, "stairs")))
        self.assertEqual(self.at(5, 65, 8), "minecraft:air")
        self.assertEqual(self.at(5, 62, 8), BRICKS, "the hollow filled")
        self.assertEqual(self.at(5, 63, 8), _stair("north"))
        with self.assertRaises(Refused) as caught:
            b.approach((9, 61, 10), (9, 64, 6), kind="steps", material=BRICKS)
        self.assertEqual(caught.exception.faults,
                         ["(9, 65, 8) holds minecraft:oak_planks, where the "
                          "approach writes minecraft:air"])
        self.assertIn("pass overwrite=True", str(caught.exception))
        b.approach((9, 61, 10), (9, 64, 6), kind="steps", material=BRICKS,
                   overwrite=True)
        self.assertEqual(self.at(9, 65, 8), "minecraft:air")

        def doorways(save):
            self.bank(save, 6, 63)
            save.fill((0, 62, 11), (12, 64, 11), BRICKS)     # a wall,
            for x in (5, 9):                                 # two doorways
                save.fill((x, 62, 11), (x, 63, 11), "minecraft:air")
        save = self.world(doorways)
        # Rising from the doorstep, the first row is a stair a walker climbs
        # and cannot come back down from: the lintel is in their head.
        rising = {(5, 62, 10): _stair("north"), (5, 63, 9): _stair("north"),
                  (5, 63, 8): BRICKS, (5, 63, 7): BRICKS}
        for z, y in ((10, 62), (9, 63), (8, 63), (7, 63)):
            rising.update({(5, y + k, z): "minecraft:air" for k in (1, 2, 3)})
        r = reachable(save, (5, 61, 11), (4, 56, 6), (6, 68, 11),
                      overlay=rising, allow_jump=False)
        self.assertIn((5, 62, 10), r.stranded)
        b = Builder(save)
        r = b.approach((5, 61, 11), (5, 63, 6), kind="steps", material=BRICKS)
        self.assertEqual(r.profile, ((61, "block"), (61, "block"),
                                     (62, "stairs"), (63, "stairs")))
        self.assertTrue(self.walks((5, 61, 11), (5, 63, 6), (4, 56, 6),
                                   (6, 68, 11)))
        with self.assertRaises(Refused) as caught:
            b.approach((9, 61, 11), (9, 63, 8), kind="steps", material=BRICKS)
        self.assertIn("the approach's low end (9, 61, 11) has "
                      "minecraft:stone_bricks at (9, 64, 11)",
                      str(caught.exception))
        self.assertEqual(caught.exception.faults, [(9, 64, 11)])


class ApproachGroundTests(ApproachBase):

    def test_an_ore_in_the_cut_is_taken_and_a_built_block_is_not(self):
        """The cut takes what nobody built -- natural ground, an ore -- and
        refuses what somebody did, a fence across the run, naming every
        cell, unless ``overwrite=True``."""
        def bank(save):
            self.bank(save, 6, 63)
            save.fill((5, 63, 7), (5, 63, 7), "minecraft:iron_ore")
            save.fill((9, 62, 9), (9, 62, 9), "minecraft:oak_fence")
        save = self.world(bank)
        b = Builder(save)
        b.approach((5, 61, 10), (5, 63, 5), kind="steps", material=BRICKS)
        self.assertNotIn("iron_ore", self.at(5, 63, 7))
        with self.assertRaises(Refused) as caught:
            b.approach((9, 61, 10), (9, 63, 5), kind="steps", material=BRICKS)
        self.assertEqual(len(caught.exception.faults), 1)
        self.assertIn("(9, 62, 9) holds minecraft:oak_fence",
                      caught.exception.faults[0])
        self.assertEqual(self.at(9, 62, 9), "minecraft:oak_fence")
        b.approach((9, 61, 10), (9, 63, 5), kind="steps", material=BRICKS,
                   overwrite=True)
        self.assertNotIn("fence", self.at(9, 62, 9))

    def test_a_torch_on_the_cut_face_is_refused_by_the_audit(self):
        """A torch hung in a niche of the hump the run cuts through would
        drop at the next block update once the block it hangs on is cut:
        the survival audit refuses the plan, naming it, and nothing is
        written."""
        def hump(save):
            self.hump(save)
            save.set_block(6, 63, 9, "minecraft:wall_torch[facing=east]")
        save = self.world(hump)
        before = self.snapshot((4, 60, 2), (8, 67, 12))
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.approach((5, 61, 12), (5, 62, 2), kind="steps", material=BRICKS)
        (fault,) = caught.exception.faults
        self.assertIsInstance(fault, Unsupported)
        self.assertEqual(fault.cell, (6, 63, 9))
        self.assertEqual(self.snapshot((4, 60, 2), (8, 67, 12)), before)
        self.assertEqual(b.blocks_set, 0)

    def test_sand_filled_over_water_is_refused_by_the_audit(self):
        """A row bridged over a pond two blocks above its waterline is
        filled under its surface, on the water: sand there would fall
        through it at the next block update, and the survival audit
        refuses it, naming the cell, with nothing written. Filled with the
        material's block it stands."""
        def pond(save):
            self.bank(save, 6, 64)
            save.fill((4, 61, 8), (6, 61, 8), WATER)
        save = self.world(pond)
        before = self.snapshot((4, 60, 5), (6, 68, 10))
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.approach((5, 61, 10), (5, 64, 6), kind="steps", material=BRICKS,
                       fill="minecraft:sand", over_water="bridge")
        (fault,) = caught.exception.faults
        self.assertEqual((fault.kind, fault.cell), ("falls", (5, 62, 8)))
        self.assertEqual(self.snapshot((4, 60, 5), (6, 68, 10)), before)
        b.approach((5, 61, 10), (5, 64, 6), kind="steps", material=BRICKS,
                   over_water="bridge")
        self.assertEqual([self.at(5, y, 8) for y in (61, 62, 63)],
                         [WATER, BRICKS, _stair("north")])

    def test_keep_outs_bind_the_run(self):
        """A reservation across the run is refused, named, before anything
        is read; named in ``through=`` it is crossed."""
        save = self.world(lambda s: self.bank(s, 6, 63))
        b = Builder(save)
        b.reserve(0, 8, 10, 8, name="grove")
        with self.assertRaises(Refused) as caught:
            b.approach((5, 61, 10), (5, 63, 6), kind="steps", material=BRICKS)
        self.assertIn("approach would cross reservation 'grove' at (5, 8)",
                      str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        b.approach((5, 61, 10), (5, 63, 6), kind="steps", material=BRICKS,
                   through=("grove",))

    def test_an_end_on_a_pad_stands_at_its_top(self):
        """An end given as a column stands on its ground -- the height a
        pad this session leveled set there -- and is not written."""
        save = self.world()
        b = Builder(save)
        b.level_pad(0, 0, 10, 4, 63, name="yard")
        r = b.approach((5, 61, 10), (5, 4), kind="steps", material=BRICKS)
        self.assertEqual(r.end, (5, 63, 4))
        self.assertEqual(self.at(5, 63, 4), GRASS)
        self.assertEqual(r.profile[-1], (63, "stairs"))

    def test_water_is_refused_unless_bridged_or_filled(self):
        """A pond two deep in the run is refused, the wet columns named,
        unless ``over_water`` says what to do: ``"fill"`` fills it from its
        bed, leaving no water under the run; ``"bridge"`` lays the row at
        the waterline, on the water under it."""
        def pond(save):
            self.bank(save, 4, 63)
            for x in (5, 9, 13):
                save.fill((x - 1, 60, 7), (x + 1, 61, 8), WATER)
        save = self.world(pond)
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.approach((5, 61, 10), (5, 63, 4), kind="steps", material=BRICKS)
        self.assertIn("approach would build over water or ice at (5, 7) "
                      "(2 columns)", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        r = b.approach((9, 61, 10), (9, 63, 4), kind="steps", material=BRICKS,
                       over_water="fill")
        self.assertEqual([level for level, _ in r.profile],
                         [61, 61, 61, 62, 63])
        for z in (7, 8):
            self.assertEqual([self.at(9, y, z) for y in (59, 60, 61)],
                             [DIRT, BRICKS, BRICKS])
        self.assertEqual(r.filled, 3, "two cells of the pond, one of the bank")
        b.approach((13, 61, 10), (13, 63, 4), kind="steps", material=BRICKS,
                   over_water="bridge")
        for z in (7, 8):
            self.assertEqual([self.at(13, y, z) for y in (60, 61)],
                             [WATER, BRICKS])

    def test_a_cut_into_a_pond_is_refused_unless_sealed(self):
        """A pond on the hump the run cuts through, its water over the
        level the cut goes down to, would pour into the cut the first time
        anything near it updates: refused, naming the leak, and nothing
        written -- unless ``seal=`` walls its water off where it stands,
        the one cell of it beside the cut."""
        def pond(save):
            self.hump(save)
            save.fill((6, 64, 8), (7, 64, 8), WATER)
        save = self.world(pond)
        before = self.snapshot((4, 60, 2), (8, 67, 12))
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.approach((5, 61, 12), (5, 62, 2), kind="steps", material=BRICKS)
        self.assertEqual(
            [(leak.cell, leak.kind, leak.source)
             for leak in caught.exception.faults],
            [((5, 64, 8), "spreads", (6, 64, 8))])
        self.assertIn("seal=", str(caught.exception))
        self.assertEqual(self.snapshot((4, 60, 2), (8, 67, 12)), before)
        self.assertEqual(b.blocks_set, 0)
        r = b.approach((5, 61, 12), (5, 62, 2), kind="steps", material=BRICKS,
                       seal=COBBLE)
        self.assertEqual(r.sealed, 1)
        self.assertEqual(self.at(6, 64, 8), COBBLE)
        self.assertEqual(self.at(7, 64, 8), WATER)
        self.assertEqual(self.at(5, 64, 8), "minecraft:air")

    def test_a_run_into_a_tunnel_cuts_nothing_over_the_bore(self):
        """A hill of stone fifteen high with a tunnel three high bored into
        it: the column reader stands each column of the bore on the
        hill's top, and a level run from the lawn into the tunnel cuts
        nothing over the bore, which has its head room already -- not a
        slot up through the hill to the sky."""
        def hill(save):
            save.fill((-16, 61, -16), (31, 75, 5), STONE)
            save.fill((5, 62, -16), (5, 64, 5), "minecraft:air")
        save = self.world(hill)
        b = Builder(save)
        self.assertEqual(b.ground(5, 3), 75)
        r = b.approach((5, 61, 10), (5, 61, 0), kind="steps", material=BRICKS)
        self.assertEqual(r.profile, ((61, "block"),) * 9)
        self.assertEqual(r.cut, 0)
        for z in range(1, 6):
            self.assertEqual(self.at(5, 61, z), BRICKS)
            self.assertEqual({self.at(5, y, z) for y in range(65, 76)},
                             {STONE}, z)
        box = ((5, 56, 0), (5, 68, 10))
        self.assertTrue(self.walks((5, 61, 10), (5, 61, 0), *box))
        self.assertTrue(self.walks((5, 61, 0), (5, 61, 10), *box))

    def test_a_run_under_a_ledge_stays_on_the_ground_under_it(self):
        """A ledge of stone eight over the lawn, over five rows of the
        run: the column reader stands those columns on the ledge's top,
        but the earthwork is counted from the ground under it, so the
        approach stays on the lawn and rises in its last three rows -- the
        least it can move -- and the ledge is left whole, rather than
        climbing early toward the ledge and building a causeway under
        it."""
        def ledge(save):
            self.bank(save, 1, 64)
            save.fill((0, 69, 4), (10, 70, 8), STONE)
        save = self.world(ledge)
        b = Builder(save)
        self.assertEqual(b.ground(5, 6), 70)
        r = b.approach((5, 61, 11), (5, 64, 1), kind="steps", material=BRICKS)
        self.assertEqual([level for level, _ in r.profile],
                         [61] * 6 + [62, 63, 64])
        self.assertEqual((r.cut, r.filled), (0, 3))
        for z in range(4, 9):
            self.assertEqual([self.at(5, y, z) for y in (69, 70)],
                             [STONE, STONE], z)
        self.assertEqual(self.at(5, 62, 4), _stair("north"))

    def test_a_run_under_a_low_ledge_cuts_the_third_cell_over_each_row(self):
        """A ledge whose underside is the third cell over the lawn: the rows
        under it stand on the lawn, and each is cut to its ``HEAD_ROOM`` --
        the ledge's lower course, the third cell over the tread, goes, and
        the course over it is left. Cut only two, the run is a stair you
        climb and cannot come back down, and is refused as one."""
        def ledge(save):
            self.bank(save, 1, 64)
            save.fill((0, 64, 4), (10, 65, 9), STONE)
        save = self.world(ledge)
        b = Builder(save)
        self.assertEqual(b.ground(5, 6), 65)
        r = b.approach((5, 61, 12), (5, 64, 1), kind="steps", material=BRICKS)
        self.assertEqual([level for level, _ in r.profile],
                         [61] * 7 + [62, 63, 64])
        for z in range(5, 10):
            self.assertEqual(self.at(5, 61, z), BRICKS, z)
            self.assertEqual([self.at(5, y, z) for y in range(62, 66)],
                             [AIR, AIR, AIR, STONE], z)

    def test_a_bridged_run_cuts_the_third_cell_over_the_water(self):
        """Bridged over a pond with a canopy of leaves the third cell over
        its waterline -- leaves the column reader passes through, so the
        ground is the water -- each row on the water is cut to its
        ``HEAD_ROOM`` as on dry ground: the leaves go, and the run walks
        both ways."""
        def pond(save):
            self.bank(save, 1, 64)
            save.fill((4, 61, 6), (6, 61, 8), "minecraft:water[level=0]")
            save.fill((4, 64, 5), (6, 64, 9),
                      "minecraft:oak_leaves[persistent=true]")
        save = self.world(pond)
        b = Builder(save)
        self.assertTrue(b.probe(5, 7).wet)
        r = b.approach((5, 61, 12), (5, 64, 1), kind="steps", material=BRICKS,
                       over_water="bridge")
        self.assertEqual([level for level, _ in r.profile],
                         [61] * 7 + [62, 63, 64])
        for z in (6, 7, 8):
            self.assertEqual(self.at(5, 61, z), BRICKS, z)
            self.assertEqual([self.at(5, y, z) for y in (62, 63, 64)],
                             [AIR] * 3, z)

    def test_lava_beside_the_cut_is_refused_and_nothing_passes_it(self):
        """Lava on the hump the run cuts through would run into the cut
        and down onto the steps the first time anything near it updates.
        The water check follows water, so lava is asked about on its own:
        refused, naming the cell the cut opens and the lava beside it, and
        no seal and no ``overwrite`` passes it."""
        def hump(save):
            self.hump(save)
            save.fill((6, 64, 8), (7, 64, 8), LAVA)
        save = self.world(hump)
        before = self.snapshot((4, 60, 2), (8, 67, 12))
        b = Builder(save)
        for call in ({}, {"seal": COBBLE}, {"overwrite": True}):
            with self.subTest(**call):
                with self.assertRaises(Refused) as caught:
                    b.approach((5, 61, 12), (5, 62, 2), kind="steps",
                               material=BRICKS, **call)
                self.assertEqual(
                    caught.exception.faults,
                    [f"(5, 64, 8), which the approach opens, has {LAVA} "
                     f"beside it at (6, 64, 8)"])
                self.assertIn("approach would be laid in lava or open onto "
                              "it", str(caught.exception))
        self.assertEqual(self.snapshot((4, 60, 2), (8, 67, 12)), before)
        self.assertEqual(b.blocks_set, 0)

    def test_lava_in_the_run_is_lava_whatever_over_water_says(self):
        """A lava source in the lawn under a row is neither somebody's
        building nor the water ``over_water`` speaks for: each refusal
        names it as lava."""
        def lava(save):
            self.bank(save, 3, 63)
            save.set_block(5, 61, 7, LAVA)
        save = self.world(lava)
        b = Builder(save)
        for over_water in (None, "bridge", "fill"):
            with self.subTest(over_water=over_water):
                with self.assertRaises(Refused) as caught:
                    b.approach((5, 61, 10), (5, 63, 3), kind="steps",
                               material=BRICKS, over_water=over_water)
                self.assertEqual(caught.exception.faults,
                                 [f"(5, 61, 7) holds {LAVA}, where the "
                                  f"approach writes {BRICKS}"])
                self.assertIn("laid in lava", str(caught.exception))
                self.assertNotIn("somebody built", str(caught.exception))
        self.assertEqual(self.at(5, 61, 7), LAVA)

    def test_water_in_a_dry_cut_is_no_flags(self):
        """A spring in the bank -- a water source inside the hump, under
        its grass -- is cut into by the run. ``over_water`` speaks for the
        water a row stands over, and this column's ground is dry: refused
        with or without it, naming the water, and nothing written."""
        def spring(save):
            self.hump(save)
            save.set_block(5, 63, 8, WATER)
        save = self.world(spring)
        b = Builder(save)
        for over_water in (None, "bridge"):
            with self.subTest(over_water=over_water):
                with self.assertRaises(Refused) as caught:
                    b.approach((5, 61, 12), (5, 62, 2), kind="steps",
                               material=BRICKS, over_water=over_water)
                self.assertEqual(caught.exception.faults,
                                 [f"(5, 63, 8) holds {WATER}, where the "
                                  f"approach writes minecraft:air"])
                self.assertIn("in a column whose ground is dry",
                              str(caught.exception))
        self.assertEqual(self.at(5, 63, 8), WATER)
        self.assertEqual(b.blocks_set, 0)

    def test_a_bridged_row_may_not_starve_a_flow(self):
        """A row bridged on a water source takes the water it stands in, as
        a causeway at the waterline does: the flow beside it that the
        source fed would dry, so it is refused, naming the flow, and
        pointed at draining it first."""
        def pond(save):
            self.bank(save, 6, 63)
            save.set_block(5, 61, 9, WATER)
            save.set_block(6, 61, 9, "minecraft:water[level=1]")
        save = self.world(pond)
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.approach((5, 61, 10), (5, 63, 6), kind="steps", material=BRICKS,
                       over_water="bridge")
        self.assertEqual(caught.exception.faults, [(6, 61, 9)])
        self.assertIn("approach would cut flowing water off from what feeds "
                      "it", str(caught.exception))
        self.assertIn("grade(..., drain=True, allow_dam=True)",
                      str(caught.exception))
        self.assertEqual(self.at(5, 61, 9), WATER)
        self.assertEqual(b.blocks_set, 0)

    def test_a_seal_in_a_keep_out_is_refused(self):
        """The seal walling a pond off from the cut stands outside the run,
        so it answers to the keep-outs on its own: one in a reservation is
        refused, named, and nothing written; named in ``through=`` it is
        laid."""
        def pond(save):
            self.hump(save)
            save.fill((6, 64, 8), (7, 64, 8), WATER)
        save = self.world(pond)
        b = Builder(save)
        b.reserve(6, 8, 7, 8, name="pond")
        with self.assertRaises(Refused) as caught:
            b.approach((5, 61, 12), (5, 62, 2), kind="steps", material=BRICKS,
                       seal=COBBLE)
        self.assertIn("approach's seals would stand in reservation 'pond' "
                      "at (6, 8)", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        r = b.approach((5, 61, 12), (5, 62, 2), kind="steps", material=BRICKS,
                       seal=COBBLE, through=("pond",))
        self.assertEqual(r.sealed, 1)
        self.assertEqual(self.at(6, 64, 8), COBBLE)

    def test_a_waterline_over_what_a_row_may_stand_at_is_refused(self):
        """A tub on a pillar in the run, its water a block over the high
        end: bridged, the row would stand on the water, and no row between
        these ends stands that high. Refused, naming the wet column and its
        waterline, before anything is planned."""
        def tub(save):
            self.bank(save, 6, 63)
            save.fill((4, 61, 8), (6, 64, 8), DIRT)
            save.fill((5, 64, 7), (5, 64, 9), DIRT)
            save.set_block(5, 64, 8, WATER)
        save = self.world(tub)
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.approach((5, 61, 10), (5, 63, 6), kind="steps", material=BRICKS,
                       over_water="bridge")
        self.assertIn("the approach would stand on the water at (5, 8), its "
                      "waterline at y=64, and the ends let that row stand no "
                      "higher than y=63", str(caught.exception))
        self.assertEqual(caught.exception.faults, [(5, 8)])
        self.assertEqual(b.blocks_set, 0)

    def test_a_tall_plant_on_the_cut_is_taken_whole(self):
        """Tall grass on the hump, over a row cut two deep: its lower half
        is the cell over the ground, and its upper half -- over the row's
        head room -- goes with it, so no lone half is left to drop."""
        def hump(save):
            self.hump(save)
            save.set_block(5, 65, 8, "minecraft:tall_grass[half=lower]")
            save.set_block(5, 66, 8, "minecraft:tall_grass[half=upper]")
        save = self.world(hump)
        r = Builder(save).approach((5, 61, 12), (5, 62, 2), kind="steps",
                                   material=BRICKS)
        self.assertEqual(r.profile[3], (62, "block"))
        self.assertEqual([self.at(5, y, 8) for y in range(62, 68)],
                         [BRICKS] + ["minecraft:air"] * 5)

    def test_an_end_on_water_is_refused(self):
        """An end given as a column standing in water has nowhere to stand."""
        def pond(save):
            save.fill((5, 61, 3), (5, 61, 3), WATER)
        save = self.world(pond)
        with self.assertRaises(Refused) as caught:
            Builder(save).approach((5, 61, 10), (5, 3), kind="steps",
                                   material=BRICKS)
        self.assertIn("approach's end at (5, 3) is water",
                      str(caught.exception))


class ApproachClaimTests(ApproachBase):

    def test_approaches_and_flights_claim_their_head_room(self):
        """An approach claims its surface and the head room over it, as a
        flight claims its treads: a flight into an approach's claim is
        refused, naming the approach, and so is an approach into a
        flight's -- and a shell, which reads the same registry. The run is
        a path the session paved."""
        save = self.world(lambda s: self.bank(s, 6, 63))
        b = Builder(save)
        b.approach((5, 61, 10), (5, 63, 6), kind="steps", material=BRICKS)
        self.assertIn((5, 62, 8), b._claimed)
        self.assertIn((5, 65, 8), b._claimed)
        self.assertEqual({(5, 9), (5, 8), (5, 7)} - b._paved, set())
        with self.assertRaises(Refused) as caught:
            b.flight(StraightStair((4, 64, 8), "east", 3), walk=False)
        self.assertIn("claimed by an approach this session built",
                      str(caught.exception))
        with self.assertRaises(Refused) as caught:
            b.shell(4, 7, 6, 9, Facade(STONE), floor_y=62, top=65,
                    hollow=False, overwrite=True)
        self.assertIn("is claimed by an approach this session built",
                      caught.exception.faults[0])

        save = self.world(lambda s: self.bank(s, 6, 63))
        b = Builder(save)
        save.fill((10, 61, 7), (14, 64, 9), STONE)
        b.flight(StraightStair((11, 65, 8), "east", 2), walk=False)
        with self.assertRaises(Refused) as caught:
            b.approach((12, 61, 11), (12, 63, 6), kind="steps",
                       material=BRICKS, overwrite=True)
        self.assertIn("approach would be laid into what a flight this "
                      "session built claims", str(caught.exception))

    def test_furniture_in_an_approachs_head_room_is_refused(self):
        """A barrel set in the head room over an approach's tread is a stair
        climbed and not come back down: refused, naming the approach as the
        claim's owner, whatever ``overwrite`` says."""
        save = self.world(lambda s: self.bank(s, 6, 63))
        b = Builder(save)
        r = b.approach((5, 61, 10), (5, 63, 6), kind="steps", material=BRICKS)
        self.assertEqual(r.profile[1], (62, "stairs"))
        barrel = Piece.stack((0, 0), block="minecraft:barrel", dy=1)
        before = b.blocks_set
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                with self.assertRaises(Refused) as caught:
                    b.furnish(barrel.at(5, 62, 8), walk=False,
                              overwrite=overwrite)
                self.assertTrue(caught.exception.faults[0].startswith(
                    "(5, 64, 8) is claimed by an approach this session "
                    "built"))
        self.assertEqual(b.blocks_set, before)


class ApproachWalkTests(ApproachBase):

    def test_the_walk_refuses_an_end_with_no_room_to_stand(self):
        """The rows are laid right, and the high end is not somewhere to
        stand: a block two over it, or an end named a block too high, over
        the bank's grass. Only the round trip can see it, and it refuses,
        naming the end, with nothing written; ``walk=False`` lays the same
        rows without asking."""
        def bank(save):
            self.bank(save, 6, 63)
            save.set_block(5, 65, 6, STONE)
        save = self.world(bank)
        before = self.snapshot((4, 58, 5), (10, 68, 11))
        b = Builder(save)
        for start, end in (((5, 61, 10), (5, 63, 6)),
                           ((9, 61, 10), (9, 64, 6))):
            with self.subTest(end=end):
                with self.assertRaises(Refused) as caught:
                    b.approach(start, end, kind="steps", material=BRICKS)
                self.assertIn(f"the planned approach does not walk both ways "
                              f"between {start} and {end} without a jump",
                              str(caught.exception))
                self.assertEqual(caught.exception.faults, [end])
        self.assertEqual(self.snapshot((4, 58, 5), (10, 68, 11)), before)
        self.assertEqual(b.blocks_set, 0)
        r = b.approach((5, 61, 10), (5, 63, 6), kind="steps", material=BRICKS,
                       walk=False)
        self.assertEqual(r.profile, ((61, "block"), (62, "stairs"),
                                     (63, "stairs")))
        self.assertEqual(self.at(5, 63, 7), _stair("north"))


class ApproachArgumentTests(ApproachBase):

    def test_diagonal_and_adjacent_ends_are_refused(self):
        """An approach runs along x or z with a row between its ends; a
        diagonal, adjacent ends or one column are bad arguments, refused
        before anything is read -- here, over a world with no chunks."""
        empty = self.world(chunks=[(5, 5)])
        b = Builder(empty)
        for start, end, words in (
                ((0, 61, 0), (3, 63, 4), "on neither"),
                ((0, 61, 0), (0, 63, 1), "side by side"),
                ((0, 61, 0), (0, 0), "are one column"),
                ("pad-1", (0, 4), "the block stood on there")):
            with self.subTest(start=start, end=end):
                with self.assertRaises(NBTError) as caught:
                    b.approach(start, end, kind="steps", material=BRICKS)
                self.assertIn(words, str(caught.exception))
        with self.assertRaises(NoSurface):
            b.approach((0, 61, 0), (0, 63, 5), kind="steps", material=BRICKS)

    def test_bad_arguments_are_refused_before_anything_is_read(self):
        """The kind is required and one of two; the width a whole number;
        the material a family with the form the kind lays; the fill a
        block, a Material or a Mix -- all refused before a chunk is read."""
        empty = self.world(chunks=[(5, 5)])
        b = Builder(empty)
        with self.assertRaises(TypeError):
            b.approach((0, 61, 0), (0, 63, 5), material=BRICKS)
        for call, error, words in (
                (dict(kind="stairs", material=BRICKS), NBTError, "'steps'"),
                (dict(kind="steps", material=BRICKS, width=0), NBTError,
                 "width"),
                (dict(kind="steps", material=Mix([(BRICKS, 1)])), TypeError,
                 "is a Mix"),
                (dict(kind="steps", material=DIRT), IllegalBlock, "stairs"),
                (dict(kind="ramp", material="minecraft:bricks",
                      fill=[(DIRT, 1)]), TypeError, "fill"),
                (dict(kind="steps", material=BRICKS, over_water="swim"),
                 ValueError, "over_water"),
                (dict(kind="steps", material=BRICKS, seal="minecraft:sand"),
                 NBTError, "would not stay a wall")):
            with self.subTest(call=call):
                with self.assertRaises(error) as caught:
                    b.approach((0, 61, 0), (0, 63, 5), **call)
                self.assertIn(words, str(caught.exception))
        self.assertEqual(
            [kind.__name__ for kind in Builder.__mro__[10:14]],
            ["RailwayMixin", "ExtrudeMixin", "ApproachMixin", "PlantingMixin"])


class ApproachParkTests(ApproachBase):
    """``tools/park/build_park.py``'s own ``approach``, run beside the
    Builder's over the same ground."""

    CHUNKS = [(x, z) for x in range(-5, -2) for z in range(9, 12)]

    @staticmethod
    def park():
        saved = list(sys.path)
        try:
            spec = importlib.util.spec_from_file_location(
                "_approach_build_park",
                os.path.join(_ROOT, "tools", "park", "build_park.py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.path[:] = saved
        return module

    def test_the_parks_approach_where_shapes_agree(self):
        """A bank two blocks over the park's lawn, north of its edge row at
        z=172, five columns wide, away from the park's protected boxes. The
        park's approach cuts the first row outside to 62 and leaves the
        rest, 61, 62, 63, 63, 63; the Builder's lays the same profile in
        every column, cuts the same cells and fills nothing, and differs
        only in the surface of its three rows -- a stair where the profile
        rises, the material's block where it does not -- since the park
        wrote no row it did not move. The park's whole-block steps need a
        jump; the approach's do not."""
        park = self.park()

        def bank(save):
            self.bank(save, 171, 63, x0=-80, x1=-49, z0=144)
        copies = [self.world(bank, chunks=self.CHUNKS) for _ in range(2)]
        xs = range(-60, -55)

        class Gate:
            approach = park.Park.approach
            top_solid = park.Park.top_solid
            set = park.Park.set
            block = park.Park.block

            def __init__(self, save):
                self.save = save
                self.b = Builder(save)

        with contextlib.redirect_stdout(io.StringIO()):
            Gate(copies[0]).approach(xs, 172, -1)
        r = Builder(copies[1]).approach((-58, 61, 172), (-58, 63, 168),
                                        kind="steps", material=BRICKS,
                                        width=5)
        self.assertEqual(r.profile, ((62, "stairs"), (63, "stairs"),
                                     (63, "block")))
        surfaces = {}
        for z, (y, form) in zip((171, 170, 169), r.profile):
            for x in xs:
                self.assertEqual(Gate(copies[0]).top_solid(x, z), y, (x, z))
                surfaces[(x, y, z)] = (_stair("north") if form == "stairs"
                                       else BRICKS)
        for x in xs:
            for y in range(58, 70):
                for z in range(166, 175):
                    park_cell = str(copies[0].get_block(x, y, z))
                    ours = str(copies[1].get_block(x, y, z))
                    if (x, y, z) in surfaces:
                        self.assertEqual(ours, surfaces[(x, y, z)])
                        self.assertIn(park_cell, (DIRT, GRASS))
                    else:
                        self.assertEqual(ours, park_cell, (x, y, z))
        self.assertEqual(r.filled, 0)
        self.assertEqual(r.cut, 5)
        box = ((-61, 55, 166), (-55, 70, 172))
        self.save = copies[0]
        self.assertFalse(self.walks((-58, 61, 172), (-58, 63, 168), *box))
        self.save = copies[1]
        self.assertTrue(self.walks((-58, 61, 172), (-58, 63, 168), *box))


if __name__ == "__main__":
    unittest.main()
