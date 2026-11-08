"""Tests for craft.build's walls: Wall, Facade and Opening, the writers
that lay them (Builder.wall, shell and cut_opening), and what crowns them
(merlon_offsets and Builder.battlement).

A facade's bays count along the wall's own run, from the world: the secret
lab's stables tested ``x % 4`` along walls whose x never changes, and came
out with two walls all posts and two with none. The lab's HQ and main hall,
the stables as they were meant, the Roman insula's courses and the m4
insula's mass-and-hollow are copied here as the references each rule is
held to cell for cell, and the writers are walked with doorway and flight
the way a building is used: in through the door, up the stairs, and back.

A battlement's teeth are counted per straight run, from that run's own
corners, so a corner is a merlon by construction. The crowns written by
hand counted round the whole perimeter as one list -- four sides
concatenated, a merlon on every other cell -- which alternates cleanly only
while every side is odd: seven of JeffWorld's Roman field towers and the
castle example's gatehouse turrets wear the result, two bare corners each.
``examples/castle.py`` is the oracle for the rule itself: its
``merlon_offsets`` is the one this promotes, and its round towers are
crowned by alternating round ``ring_offsets``, which ``battlement(ring=)``
reproduces cell for cell.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "examples"))

import castle  # noqa: E402
from heaton_voxel.blocks import BlockState  # noqa: E402
from heaton_voxel.craft import Material, Mix, merlon_offsets  # noqa: E402
from heaton_voxel.craft import Facade, Opening, Wall, WallResult  # noqa: E402
from heaton_voxel.craft.block_facts import facts  # noqa: E402
from heaton_voxel.craft.build import (  # noqa: E402
    Builder,
    Roof,
    StraightStair,
    arch_profile,
    pointed_arch_profile,
)
from heaton_voxel.craft.errors import IllegalBlock, Refused  # noqa: E402
from heaton_voxel.craft.geometry import ring  # noqa: E402
from heaton_voxel.craft.survey import (  # noqa: E402
    iron_door_opens,
    reachable,
    unsupported,
)
from heaton_voxel.craft.style import FROM_PALETTE  # noqa: E402
from heaton_voxel.nbt import NBTError  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402
from tests.test_world import make_save  # noqa: E402

CHUNKS = [(x, z) for x in range(0, 3) for z in range(0, 3)]
AIR = "minecraft:air"
STONE = "minecraft:stone_bricks"
Y = 70


def rectangle(x0, z0, x1, z1, *, start=None):
    """A closed path round a rectangle, clockwise from its northwest
    corner -- or from ``start``, a cell on its north side."""
    corners = [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
    if start is None:
        return corners + [(x0, z0)]
    return [start] + corners[1:] + [corners[0], start]


def perimeter(x0, z0, x1, z1):
    """The rectangle's rim, cell by cell, clockwise from its northwest
    corner: the order a parapet is walked."""
    return ([(x, z0) for x in range(x0, x1)]
            + [(x1, z) for z in range(z0, z1)]
            + [(x, z1) for x in range(x1, x0, -1)]
            + [(x0, z) for z in range(z1, z0, -1)])


class MerlonOffsetsTests(unittest.TestCase):
    def test_craft_counts_merlons_as_the_castle_does(self):
        for length in range(-2, 65):
            self.assertEqual(merlon_offsets(length),
                             castle.merlon_offsets(length), f"length {length}")

    def test_both_ends_are_teeth_and_an_even_run_doubles_at_its_far_end(self):
        self.assertEqual(merlon_offsets(7), [0, 2, 4, 6])
        self.assertEqual(merlon_offsets(8), [0, 2, 4, 6, 7])
        self.assertEqual(merlon_offsets(1), [0])
        self.assertEqual(merlon_offsets(0), [])


class BattlementBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="walls-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, CHUNKS)
        self.save = MinecraftSave.load(self.dir)
        self.b = Builder(self.save)

    def name(self, x, y, z):
        return self.save.get_block(x, y, z).name

    def merlons(self, cells, y=Y):
        """The cells of ``cells`` with something standing on the course."""
        return [(x, z) for x, z in cells if self.name(x, y + 1, z) != AIR]


class BattlementTests(BattlementBase):
    def test_a_straight_run_takes_the_castles_teeth_cell_for_cell(self):
        """The castle crowns each curtain wall with merlon_offsets counted
        along it; an open path along the same run is that crown."""
        for length in range(2, 21):
            z = 2 * length
            with self.subTest(length=length):
                run = [(2 + i, z) for i in range(length)]
                tops = self.b.battlement([run[0], run[-1]], Y, block=STONE)
                teeth = set(castle.merlon_offsets(length))
                self.assertEqual(self.merlons(run),
                                 [cell for i, cell in enumerate(run)
                                  if i in teeth])
                self.assertEqual(tops, tuple((x, Y + 1, z) for x, z in run
                                             if x - 2 in teeth))
                self.assertTrue(all(self.name(x, Y, z) == STONE
                                    for x, z in run), "the course runs whole")

    def test_every_corner_of_a_rectangle_is_a_merlon(self):
        for x0, z0, x1, z1 in ((2, 2, 8, 8), (12, 2, 19, 9), (24, 2, 29, 11),
                               (2, 14, 14, 20), (32, 32, 33, 33)):
            with self.subTest(rectangle=(x0, z0, x1, z1)):
                self.b.battlement(rectangle(x0, z0, x1, z1), Y, block=STONE)
                for corner in ((x0, z0), (x1, z0), (x1, z1), (x0, z1)):
                    self.assertNotEqual(self.name(corner[0], Y + 1, corner[1]),
                                        AIR, f"corner {corner} is bare")

    def test_the_hand_written_crown_leaves_two_corners_bare_and_this_does_not(self):
        """The castle's gatehouse turret rim, as examples/castle.py builds
        it: two rows, then two columns, one list, a merlon at every even
        index. On a 7 by 7 rim the second row starts on a gap, so two of
        its four corners are bare -- the field towers' crown in JeffWorld.
        A battlement over the same rectangle crowns all four."""
        tx0, tz0, tx1, tz1 = 3, 3, 7, 7               # the turret, inside its rim
        rim = ([(x, tz0 - 1) for x in range(tx0 - 1, tx1 + 2)]
               + [(x, tz1 + 1) for x in range(tx0 - 1, tx1 + 2)]
               + [(tx0 - 1, z) for z in range(tz0, tz1 + 1)]
               + [(tx1 + 1, z) for z in range(tz0, tz1 + 1)])
        by_hand = {cell for i, cell in enumerate(rim) if i % 2 == 0}
        corners = [(tx0 - 1, tz0 - 1), (tx1 + 1, tz0 - 1),
                   (tx1 + 1, tz1 + 1), (tx0 - 1, tz1 + 1)]
        self.assertEqual(sum(corner not in by_hand for corner in corners), 2)

        self.b.battlement(rectangle(tx0 - 1, tz0 - 1, tx1 + 1, tz1 + 1), Y,
                          block=STONE)
        self.assertEqual([corner for corner in corners
                          if self.name(corner[0], Y + 1, corner[1]) == AIR], [])

    def test_odd_sides_alternate_all_the_way_round(self):
        self.b.battlement(rectangle(2, 2, 8, 8), Y, block=STONE)
        rim = perimeter(2, 2, 8, 8)
        roles = [self.name(x, Y + 1, z) != AIR for x, z in rim]
        for i, role in enumerate(roles):
            self.assertNotEqual(role, roles[(i + 1) % len(roles)],
                                f"{rim[i]} and the next cell alike")

    def test_an_even_side_doubles_beside_its_far_corner(self):
        self.b.battlement([(2, 2), (9, 2)], Y, block=STONE)
        self.assertEqual([x - 2 for x, _ in self.merlons(
            [(x, 2) for x in range(2, 10)])], [0, 2, 4, 6, 7])
        # Round a closed 8 by 8, each side in path order doubles at its end.
        self.b.battlement(rectangle(20, 20, 27, 27), Y, block=STONE)
        self.assertEqual(self.merlons([(27, z) for z in range(20, 28)]),
                         [(27, z) for z in (20, 22, 24, 26, 27)],
                         "the east side, walked south, doubles at its south end")
        self.assertEqual(self.merlons([(x, 27) for x in range(27, 19, -1)]),
                         [(x, 27) for x in (27, 25, 23, 21, 20)],
                         "the south side, walked west, doubles at its west end")

    def test_a_straight_vertex_does_not_restart_the_rhythm(self):
        through = self.b.battlement([(2, 2), (6, 2), (12, 2)], Y, block=STONE)
        whole = self.b.battlement([(2, 4), (12, 4)], Y, block=STONE)
        self.assertEqual([(x, z) for x, _, z in through],
                         [(x, 2) for x, _, _ in whole])
        # A closed path begun mid-side is walked from its first corner.
        mid = self.b.battlement(rectangle(20, 2, 27, 9, start=(23, 2)), Y,
                                block=STONE)
        corner = self.b.battlement(rectangle(30, 2, 37, 9), Y, block=STONE)
        self.assertEqual(sorted((x - 20, z) for x, _, z in mid),
                         sorted((x - 30, z) for x, _, z in corner))

    def test_it_returns_the_merlon_tops_in_path_order(self):
        tops = self.b.battlement([(10, 2), (2, 2), (2, 6)], Y, block=STONE,
                                 height=2)
        self.assertEqual(tops, ((10, Y + 2, 2), (8, Y + 2, 2), (6, Y + 2, 2),
                                (4, Y + 2, 2), (2, Y + 2, 2), (2, Y + 2, 4),
                                (2, Y + 2, 6)))

    def test_height_stands_every_merlon_that_tall(self):
        self.b.battlement([(2, 2), (8, 2)], Y, block=STONE,
                          merlon="minecraft:cobblestone", height=3)
        for x in range(2, 9):
            column = [self.name(x, y, 2) for y in range(Y, Y + 5)]
            if x % 2 == 0:
                self.assertEqual(column, [STONE] + ["minecraft:cobblestone"] * 3
                                 + [AIR], f"x={x}")
            else:
                self.assertEqual(column, [STONE] + [AIR] * 4, f"x={x}")

    def test_a_course_already_laid_is_taken_as_it_is(self):
        """A wall's solid top course is laid first; crenellating it is a
        battlement laid over it."""
        self.b.fill((2, Y, 2), (8, Y, 2), STONE)
        self.b.battlement([(2, 2), (8, 2)], Y, block=STONE)
        self.assertEqual(len(self.merlons([(x, 2) for x in range(2, 9)])), 4)

        self.b.fill((2, Y, 4), (8, Y, 4), "minecraft:cobblestone")
        before = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.battlement([(2, 4), (8, 4)], Y, block=STONE)
        self.assertEqual(len(caught.exception.faults), 7)
        self.assertIn("(2, 70, 4) holds minecraft:cobblestone",
                      caught.exception.faults[0])
        self.assertEqual(self.b.blocks_set, before, "and nothing was written")
        self.assertEqual(self.name(2, Y + 1, 4), AIR)

        self.b.battlement([(2, 4), (8, 4)], Y, block=STONE, overwrite=True)
        self.assertEqual(self.name(5, Y, 4), STONE)

    def test_a_filled_gap_is_refused_and_overwrite_cuts_it(self):
        self.save.set_block(3, Y + 1, 2, "minecraft:oak_planks")
        with self.assertRaises(Refused) as caught:
            self.b.battlement([(2, 2), (8, 2)], Y, block=STONE)
        self.assertEqual(caught.exception.faults,
                         ["(3, 71, 2) holds minecraft:oak_planks, where the "
                          "battlement needs minecraft:air"])
        self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(self.name(2, Y, 2), AIR)

        self.b.battlement([(2, 2), (8, 2)], Y, block=STONE, overwrite=True)
        self.assertEqual(self.name(3, Y + 1, 2), AIR, "the gap is cut")
        self.assertEqual(self.name(2, Y + 1, 2), STONE)

    def test_a_gap_is_held_empty_all_the_way_up_the_merlons(self):
        """A gap is a gap as high as the teeth stand, not only beside the
        first block of them."""
        self.save.set_block(3, Y + 2, 2, "minecraft:oak_planks")
        with self.assertRaises(Refused) as caught:
            self.b.battlement([(2, 2), (8, 2)], Y, block=STONE, height=2)
        self.assertEqual(caught.exception.faults,
                         ["(3, 72, 2) holds minecraft:oak_planks, where the "
                          "battlement needs minecraft:air"])
        self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(self.name(2, Y + 1, 2), AIR)

        self.b.battlement([(2, 2), (8, 2)], Y, block=STONE, height=2,
                          overwrite=True)
        self.assertEqual(self.name(3, Y + 2, 2), AIR, "the gap is cut")
        self.assertEqual(self.name(2, Y + 2, 2), STONE)
        self.assertEqual(self.b.blocks_set, 7 + 4 * 2 + 1)

    def test_overwrite_will_not_cut_a_gap_from_behind_a_torch(self):
        """overwrite cuts through what fills a gap, but a gap cut from
        behind a torch leaves the torch on nothing, and the survival audit
        refuses that before a block is written, as it does a wall's window;
        with the torch hung on a merlon instead, the same call builds."""
        torch = "minecraft:wall_torch[facing=south]"
        self.save.set_block(1, Y + 1, 0, STONE)
        self.save.set_block(1, Y + 1, 1, torch)
        with self.assertRaises(Refused) as caught:
            self.b.battlement([(0, 0), (4, 0)], Y, block=STONE, overwrite=True)
        self.assertEqual([(f.cell, f.kind) for f in caught.exception.faults],
                         [((1, Y + 1, 1), "no footing")])
        self.assertEqual(self.b.blocks_set, 0, "nothing was written")
        self.assertEqual(self.name(1, Y + 1, 0), STONE)
        self.assertEqual(self.name(0, Y, 0), AIR)

        self.save.set_block(1, Y + 1, 1, AIR)
        self.save.set_block(2, Y + 1, 1, torch)
        self.b.battlement([(0, 0), (4, 0)], Y, block=STONE, overwrite=True)
        self.assertEqual(self.name(1, Y + 1, 0), AIR, "the gap is cut")
        self.assertEqual(self.name(2, Y + 1, 0), STONE)
        self.assertEqual(self.name(2, Y + 1, 1), "minecraft:wall_torch")

    def test_a_crown_finish_has_joined_up_is_found_again_as_it_stands(self):
        """finish() sets a fence's, a wall's and a pane's sides, and a
        wall's post, from their neighbors: the course is still the one
        that was laid, so the same call made again finds it and writes
        nothing, and the course keeps the sides it was given."""
        calls = {"minecraft:oak_fence": 2, "minecraft:cobblestone_wall": 6,
                 "minecraft:iron_bars": 10}
        tops = {block: self.b.battlement([(2, z), (12, z)], Y, block=block)
                for block, z in calls.items()}
        self.b.finish()
        joined = {(x, y, z): self.save.get_block(x, y, z)
                  for z in calls.values() for x in range(2, 13)
                  for y in (Y, Y + 1)}
        self.assertEqual(joined[(4, Y, 2)].properties.get("east"), "true",
                         "finish joined the fence up")
        self.assertEqual(joined[(4, Y, 6)].properties.get("east"), "low")

        again = Builder(self.save)
        for block, z in calls.items():
            with self.subTest(block=block):
                self.assertEqual(again.battlement([(2, z), (12, z)], Y,
                                                  block=block), tops[block])
        self.assertEqual(again.blocks_set, 0, "the crowns were all there")
        self.assertEqual({cell: self.save.get_block(*cell) for cell in joined},
                         joined, "and keep the sides finish gave them")

        # What a block update does not derive is still a different block.
        self.save.set_block(4, Y, 14, "minecraft:oak_fence[waterlogged=true]")
        with self.assertRaises(Refused) as caught:
            again.battlement([(2, 14), (12, 14)], Y,
                             block="minecraft:oak_fence")
        self.assertEqual(len(caught.exception.faults), 1)
        self.assertIn("(4, 70, 14) holds minecraft:oak_fence[",
                      caught.exception.faults[0])

    def test_a_gap_of_cave_air_is_empty_and_left_as_it_is(self):
        self.save.set_block(3, Y + 1, 2, "minecraft:cave_air")
        self.b.battlement([(2, 2), (8, 2)], Y, block=STONE)
        self.assertEqual(self.name(3, Y + 1, 2), "minecraft:cave_air")
        self.assertEqual(self.b.blocks_set, 7 + 4, "the course and the teeth")

    def test_a_diagonal_path_is_refused(self):
        for path in ([(2, 2), (8, 8)], [(2, 2), (8, 2), (12, 6)]):
            with self.subTest(path=path):
                with self.assertRaises(NBTError) as caught:
                    self.b.battlement(path, Y, block=STONE)
                self.assertIn("diagonal", str(caught.exception))
                self.assertNotIsInstance(caught.exception, Refused)
        self.assertEqual(self.b.blocks_set, 0)

    def test_a_path_that_turns_back_or_crosses_itself_is_refused(self):
        with self.assertRaises(NBTError) as caught:
            self.b.battlement([(2, 2), (10, 2), (5, 2)], Y, block=STONE)
        self.assertIn("turns back on itself at (10, 2)", str(caught.exception))
        # (6, 5) is a merlon along the first run and a gap along the last.
        with self.assertRaises(NBTError) as caught:
            self.b.battlement([(2, 5), (10, 5), (10, 8), (6, 8), (6, 2)], Y,
                              block=STONE)
        self.assertIn("crosses itself at (6, 5)", str(caught.exception))
        self.assertEqual(self.b.blocks_set, 0)

    def test_a_path_needs_two_vertices_that_differ(self):
        for path in ([(2, 2)], [(2, 2), (2, 2)], [], "north"):
            with self.subTest(path=path):
                with self.assertRaises(NBTError):
                    self.b.battlement(path, Y, block=STONE)

    def test_a_ring_is_crowned_as_the_castle_crowns_its_towers(self):
        """Every other cell round ``ring_offsets``, from the first, is how
        the castle sets the merlons on its tower crowns."""
        cx, cz = 24, 24
        for r in range(2, 11):
            with self.subTest(r=r):
                cells = ring(r, center=(cx, cz))
                tops = self.b.battlement(y=Y, ring=cells, block=STONE)
                expect = [(cx + dx, cz + dz) for i, (dx, dz)
                          in enumerate(castle.ring_offsets(r)) if i % 2 == 0]
                self.assertEqual([(x, z) for x, _, z in tops], expect)
                self.assertEqual(self.merlons(cells),
                                 [cell for cell in cells if cell in expect])

    def test_an_odd_ring_is_refused(self):
        cells = list(ring(4, center=(24, 24)))
        with self.assertRaises(NBTError) as caught:
            self.b.battlement(y=Y, ring=cells + [(24, 24)], block=STONE)
        self.assertIn("cannot alternate all the way round", str(caught.exception))
        with self.assertRaises(NBTError) as caught:
            self.b.battlement(y=Y, ring=sorted(cells), block=STONE)
        self.assertIn("do not touch", str(caught.exception))
        with self.assertRaises(NBTError) as caught:
            self.b.battlement(y=Y, ring=cells[:-1] + cells[:1], block=STONE)
        self.assertIn("twice", str(caught.exception))
        self.assertEqual(self.b.blocks_set, 0)

    def test_a_ring_closes_on_its_first_cell(self):
        """A ring is walked all the way round, the last cell beside the
        first: an arc of a ring, even and in order, is not one -- its two
        ends would be crowned as though they met."""
        cells = list(ring(5, center=(24, 24)))
        arc = cells[:14]
        for a, b in zip(arc, arc[1:]):
            self.assertLessEqual(max(abs(a[0] - b[0]), abs(a[1] - b[1])), 1)
        with self.assertRaises(NBTError) as caught:
            self.b.battlement(y=Y, ring=arc, block=STONE)
        self.assertIn(f"steps from {arc[-1]} to {arc[0]}, which do not touch",
                      str(caught.exception))
        self.assertEqual(self.b.blocks_set, 0)

    def test_the_plan_is_checked_before_the_world_is_read(self):
        """A block the game lacks is named, and a chunk with nothing to
        build into or a height past the world refused, before a cell is
        read -- the order every planned writer keeps, so the refusal is
        the plan's, in the plan's words, and never a failed read."""
        reads = []
        read = self.b._read
        self.b._read = lambda cell: reads.append(cell) or read(cell)
        with self.assertRaises(IllegalBlock):
            self.b.battlement([(40, 2), (52, 2)], Y,
                              block="minecraft:quartz_wall")
        for call, fault in (
                (lambda: self.b.battlement([(40, 2), (52, 2)], Y,
                                           block=STONE),
                 "chunk (3, 0) is not generated"),
                (lambda: self.b.battlement([(2, 2), (8, 2)], 319,
                                           block=STONE),
                 "y=320..320 is past the world's -64..319")):
            with self.subTest(fault=fault):
                with self.assertRaises(Refused) as caught:
                    call()
                self.assertIn(fault, caught.exception.faults)
                self.assertIn("Nothing was written", str(caught.exception))
        self.assertEqual(reads, [], "nothing was read")
        self.assertEqual(self.b.blocks_set, 0)

    def test_it_takes_a_path_or_a_ring(self):
        cells = ring(3, center=(24, 24))
        for call in (dict(), dict(path=[(2, 2), (8, 2)], ring=cells)):
            with self.subTest(call=sorted(call)):
                with self.assertRaises(NBTError) as caught:
                    self.b.battlement(y=Y, block=STONE, **call)
                self.assertIn("exactly one", str(caught.exception))
        for y in (None, 70.0, True):
            with self.subTest(y=y):
                with self.assertRaises(NBTError):
                    self.b.battlement([(2, 2), (8, 2)], y, block=STONE)
        for height in (0, -1, 1.5):
            with self.subTest(height=height):
                with self.assertRaises(NBTError):
                    self.b.battlement([(2, 2), (8, 2)], Y, block=STONE,
                                      height=height)

    def test_the_block_is_named_never_a_palettes(self):
        with self.assertRaises(TypeError):
            self.b.battlement([(2, 2), (8, 2)], Y)
        for block in (FROM_PALETTE, None, [(STONE, 1)]):
            with self.subTest(block=block):
                with self.assertRaises(TypeError):
                    self.b.battlement([(2, 2), (8, 2)], Y, block=block)
        with self.assertRaises(TypeError):
            self.b.battlement([(2, 2), (8, 2)], Y, block=STONE,
                              merlon=FROM_PALETTE)
        self.assertEqual(self.b.blocks_set, 0)

    def test_a_block_the_game_lacks_writes_nothing(self):
        for call in (dict(block="minecraft:quartz_wall"),
                     dict(block=STONE, merlon="minecraft:polished_diorite_wall")):
            with self.subTest(call=call):
                with self.assertRaises(IllegalBlock):
                    self.b.battlement([(2, 2), (8, 2)], Y, **call)
        self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(self.name(2, Y, 2), AIR)

    def test_a_chunk_that_is_not_there_refuses_it_whole(self):
        with self.assertRaises(Refused) as caught:
            self.b.battlement([(40, 2), (52, 2)], Y, block=STONE)
        self.assertIn("not generated", str(caught.exception))
        self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(self.name(40, Y, 2), AIR)

    def test_a_mix_is_picked_at_the_world_cell(self):
        course = Mix([(STONE, 3), ("minecraft:mossy_stone_bricks", 1),
                      ("minecraft:cracked_stone_bricks", 1)], seed=5)
        teeth = Mix([("minecraft:cobblestone", 1),
                     ("minecraft:mossy_cobblestone", 1)])
        tops = self.b.battlement(rectangle(2, 2, 12, 9), Y, block=course,
                                 merlon=teeth)
        for x, z in perimeter(2, 2, 12, 9):
            self.assertEqual(self.save.get_block(x, Y, z),
                             BlockState.parse(course.pick(x, Y, z)))
        for x, y, z in tops:
            self.assertEqual(self.save.get_block(x, y, z),
                             BlockState.parse(teeth.pick(x, y, z)))
        self.assertGreater(len({self.name(x, Y, z) for x, z
                                in perimeter(2, 2, 12, 9)}), 1)
        # The same Mix laid again finds its own course already there.
        again = self.b.battlement(rectangle(2, 2, 12, 9), Y, block=course,
                                  merlon=teeth)
        self.assertEqual(again, tops)

    def test_a_material_is_its_block(self):
        self.b.battlement([(2, 2), (8, 2)], Y,
                          block=Material("minecraft:brick_stairs"))
        self.assertEqual(self.name(4, Y, 2), "minecraft:bricks")
        self.assertEqual(self.name(4, Y + 1, 2), "minecraft:bricks")

    def test_two_battlements_built_alike_encode_alike(self):
        digests = []
        for _ in range(2):
            directory = tempfile.mkdtemp(prefix="walls-tests-")
            self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
            make_save(directory, CHUNKS)
            save = MinecraftSave.load(directory)
            b = Builder(save)
            b.battlement(rectangle(10, 10, 30, 21), Y, block=Mix(
                [(STONE, 2), ("minecraft:andesite", 1)]),
                merlon="minecraft:cobblestone", height=2)
            b.battlement(y=Y, ring=ring(6, center=(24, 36)), block=STONE)
            b.finish()
            save.save()
            region = os.path.join(directory,
                                  "dimensions/minecraft/overworld/region")
            files = {}
            for name in sorted(os.listdir(region)):
                with open(os.path.join(region, name), "rb") as handle:
                    files[name] = hashlib.sha256(handle.read()).hexdigest()
            digests.append(files)
        self.assertEqual(digests[0], digests[1])


# --------------------------------------------------------------------------
# Wall, Facade and Opening: the rules, with no world in them
# --------------------------------------------------------------------------

# The secret lab's skins (tools/secret_lab/lab_common.py).
PANEL = "minecraft:white_concrete"
PANEL2 = "minecraft:light_gray_concrete"
SKYGLASS = "minecraft:light_blue_stained_glass"
TINTED = "minecraft:tinted_glass"
PANE = "minecraft:glass_pane"
LOG = "minecraft:stripped_dark_oak_log[axis=y]"
# The Roman city's (tools/roman_city/common.py, jw_districts.py, m4_districts.py).
BRICK = "minecraft:bricks"
STUCCO = "minecraft:white_terracotta"
TIMBER = "minecraft:spruce_wood"
TRAV_CUT = "minecraft:cut_sandstone"
TRAV_STAIR = "minecraft:smooth_sandstone_stairs"
SPRINGER = "minecraft:stone_brick_stairs"


def laid(facade, walls):
    """Every cell a facade lays over a dict of walls, as text."""
    out = {}
    for name, wall in walls.items():
        rule = facade[name] if isinstance(facade, dict) else facade
        for cell, state in rule.plan(wall).items():
            assert cell not in out, f"{cell} laid twice"
            out[cell] = str(state)
    return out


def lab_hq_reference(x0, z0, x1, z1, T, f2, f3, roof):
    """``hq()``'s shell loop from tools/secret_lab/m2_buildings.py, as it
    stands, writing into a dict instead of a Builder."""
    out = {}
    for z in range(z0, z1 + 1):
        for x in range(x0, x1 + 1):
            edge = x in (x0, x1) or z in (z0, z1)
            if not edge:
                continue
            corner = x in (x0, x1) and z in (z0, z1)
            pier = corner or (z % 3 == 0 if x in (x0, x1) else x % 3 == 0)
            for y in range(T + 1, roof):
                if pier:
                    out[(x, y, z)] = PANEL
                elif y in (f2 - 1, f2, f3 - 1, f3):
                    out[(x, y, z)] = PANEL2
                elif z == z1 and not corner:
                    out[(x, y, z)] = TINTED
                else:
                    out[(x, y, z)] = SKYGLASS
    return out


def lab_hall_reference(x0, z0, x1, z1, plane, top):
    """``mainlab()``'s shell loop from the same script, its wings' walls --
    one top, the atrium strip's taller one left out."""
    out = {}
    for z in range(z0, z1 + 1):
        for x in range(x0, x1 + 1):
            edge = x in (x0, x1) or z in (z0, z1)
            if edge:
                pier = (z % 3 == 0) if x in (x0, x1) else (x % 3 == 0)
                corner = x in (x0, x1) and z in (z0, z1)
                for y in range(plane + 1, top + 1):
                    if corner or pier:
                        out[(x, y, z)] = PANEL
                    else:
                        out[(x, y, z)] = (SKYGLASS if y <= top - 2
                                          else PANEL2)
    return out


def stables_reference(x0, z0, x1, z1, plane, top, *, as_built: bool):
    """``stables()``'s shell loop from the same script. ``as_built`` is the
    script verbatim, testing x along the walls whose x never changes and z
    along the ones whose z never does; otherwise each wall's posts are
    counted along its own run, which is what the script meant."""
    out = {}
    for z in range(z0, z1 + 1):
        for x in range(x0, x1 + 1):
            edge = x in (x0, x1) or z in (z0, z1)
            if not edge:
                continue
            corner = x in (x0, x1) and z in (z0, z1)
            if as_built:
                is_post = corner or (x % 4 == 0 if x in (x0, x1) else z % 4 == 0)
            else:
                is_post = corner or (z % 4 == 0 if x in (x0, x1) else x % 4 == 0)
            for y in range(plane + 1, top + 1):
                if is_post:
                    out[(x, y, z)] = LOG
                elif y in (plane + 3, plane + 4) and not corner:
                    out[(x, y, z)] = PANE
                else:
                    out[(x, y, z)] = PANEL
    return out


def insula_reference(x0, z0, x1, z1, base, floors, story=4, *,
                     windows: bool = False):
    """``unit()``'s shell from tools/roman_city/jw_districts.py: a brick
    ground story, then a timber band at each upper floor and stucco over
    it, up to the top -- and with ``windows``, its street face's
    ``window_open`` on every upper story (tools/roman_city/common.py), the
    street on the south: an open cell two rows over the floor, every third
    column from ``x0 + 2`` short of the last two, a top stair facing the
    street over it."""
    out = {}
    top = base + floors * story
    for x in range(x0, x1 + 1):
        for z in range(z0, z1 + 1):
            if x in (x0, x1) or z in (z0, z1):
                for y in range(base + 1, base + story + 1):
                    out[(x, y, z)] = BRICK
                for f in range(1, floors):
                    y0 = base + f * story + 1
                    out[(x, y0 - 1, z)] = TIMBER
                    for y in range(y0, min(y0 + story - 1, top) + 1):
                        out[(x, y, z)] = STUCCO
    if windows:
        for f in range(1, floors):
            wy = base + f * story + 2
            for wx in range(x0 + 2, x1 - 1, 3):
                out[(wx, wy, z1)] = AIR
                out[(wx, wy + 1, z1)] = f"{TRAV_STAIR}[facing=south,half=top]"
    return out


class WallTests(unittest.TestCase):
    def test_a_wall_runs_along_the_worlds_own_coordinate(self):
        east_west = Wall((10, 5), (2, 5), floor_y=63, top=68, outward="north")
        self.assertEqual((east_west.axis, east_west.at), ("x", 5))
        self.assertEqual(east_west.along, range(2, 11))
        self.assertEqual(east_west.rows, range(64, 69))
        self.assertEqual(east_west.inward, "south")
        self.assertEqual(east_west.origin, 2, "the low end, whichever came first")
        self.assertEqual(east_west.cell(4, 66), (4, 66, 5))
        self.assertEqual(east_west.cell(4, 66, 2), (4, 66, 7), "two blocks in")
        north_south = Wall((7, 9), (7, 3), floor_y=63, top=68, outward="east",
                           thickness=3, origin=1)
        self.assertEqual((north_south.axis, north_south.at), ("z", 7))
        self.assertEqual(north_south.along, range(3, 10))
        self.assertEqual(north_south.cell(4, 66, 2), (5, 66, 4))
        self.assertEqual(north_south.origin, 1)
        self.assertEqual(Wall((4, 4), (4, 4), floor_y=63, top=66,
                              outward="west").axis, "z",
                         "a one-column wall runs across its outside")

    def test_a_ring_gives_its_corners_to_the_north_and_south_walls(self):
        for t in (1, 2, 3):
            with self.subTest(thickness=t):
                walls = Wall.ring(20, 12, 4, 30, floor_y=63, top=70,
                                  thickness=t)
                self.assertEqual(list(walls), ["north", "east", "south", "west"])
                self.assertTrue(walls["north"].ends and walls["south"].ends)
                self.assertFalse(walls["east"].ends or walls["west"].ends)
                self.assertEqual(walls["north"].along, range(4, 21))
                self.assertEqual(walls["east"].along, range(12 + t, 31 - t))
                self.assertEqual({w.origin for w in walls.values()}, {4, 12},
                                 "all four count from the corner (4, 12)")
                cells = [wall.cell(a, 64, layer) for wall in walls.values()
                         for a in wall.along for layer in range(t)]
                self.assertEqual(len(cells), len(set(cells)), "no cell twice")
                ring_ = {(x, 64, z) for x in range(4, 21) for z in range(12, 31)
                         if min(x - 4, 20 - x, z - 12, 30 - z) < t}
                self.assertEqual(set(cells), ring_, "every cell of the ring")
                north = walls["north"]
                self.assertEqual([a for a in north.along if north._end(a)],
                                 [*range(4, 4 + t), *range(21 - t, 21)],
                                 "a thick wall's corner is square")

    def test_story_stacking_heights(self):
        wall = Wall((0, 0), (8, 0), floor_y=63, top=77, outward="north",
                    stories=(68, 73))
        self.assertEqual(wall.floors, (63, 68, 73))
        self.assertEqual([wall.story(y) for y in (64, 67, 68, 72, 73, 77)],
                         [0, 0, 1, 1, 2, 2],
                         "a floor's own row is its story's")
        self.assertEqual([wall.floor_of(s) for s in (0, 1, 2)], [63, 68, 73])
        self.assertEqual(wall.door(3), (3, 64, 0, "south"))
        self.assertEqual(wall.door(3, story=2), (3, 74, 0, "south"))
        for bad in (lambda: wall.floor_of(3), lambda: wall.story(63),
                    lambda: wall.door(9)):
            with self.assertRaises(NBTError):
                bad()

    def test_bad_walls_are_refused_by_name(self):
        common = dict(floor_y=63, top=70)
        for call, words in (
                (lambda: Wall((0, 0), (5, 5), outward="north", **common),
                 "diagonal"),
                (lambda: Wall((0, 0), (5, 0), outward="east", **common),
                 "points along the run"),
                (lambda: Wall((0, 0), (5, 0), outward="up", **common),
                 "outward is one of"),
                (lambda: Wall((0, 0), (5, 0), outward="north", floor_y=63,
                              top=63), "top row stands above"),
                (lambda: Wall((0, 0), (5, 0), outward="north", thickness=0,
                              **common), "one block thick"),
                (lambda: Wall((0, 0), (5, 0), outward="north",
                              stories=(68, 66), **common), "stories go up"),
                (lambda: Wall((0, 0), (5, 0), outward="north", stories=(72,),
                              **common), "above the wall's top"),
                (lambda: Wall((0, 0), (5, 0), outward="north", stories=(65,),
                              **common), "1 clear rows"),
                (lambda: Wall((0, 0), (5, 0), outward="north", stories=(69,),
                              **common), "1 clear rows under the top"),
                (lambda: Wall.ring(0, 0, 3, 9, thickness=2, **common),
                 "needs at least 5 blocks")):
            with self.subTest(words=words):
                with self.assertRaises(NBTError) as caught:
                    call()
                self.assertIn(words, str(caught.exception))

    def test_a_wall_is_a_value(self):
        import copy
        import pickle

        for wall in Wall.ring(1, 2, 9, 12, floor_y=63, top=70, thickness=2,
                              stories=(66,)).values():
            self.assertEqual(pickle.loads(pickle.dumps(wall)), wall)
            self.assertIs(copy.deepcopy(wall), wall)
            self.assertEqual(hash(Wall((wall.low, 0), (wall.high, 0),
                                       floor_y=63, top=70, outward="north")),
                             hash(Wall((wall.high, 0), (wall.low, 0),
                                       floor_y=63, top=70, outward="north")))
            with self.assertRaises(AttributeError):
                wall.top = 80

    def test_the_perimeter_is_the_path_a_battlement_takes(self):
        self.assertEqual(Wall.perimeter(16, 14, 4, 4),
                         ((4, 4), (16, 4), (16, 14), (4, 14), (4, 4)))


class FacadeTests(unittest.TestCase):
    def test_the_lab_curtain_wall_is_one_rule(self):
        """The HQ tower's shell (m2_buildings.py, ``hq``): piers every third
        column, counted on each wall's own run from 0, light gray bands under
        and on each upper floor, tinted glass on the south face. In JeffWorld
        the rule matches the tower's south, west and east faces in all 825
        cells, and the north face but for its 9 entrance cells (read
        2026-10-21)."""
        x0, z0, x1, z1 = 743, 1006, 763, 1024
        T, f2, f3, roof = 80, 86, 91, 96
        walls = Wall.ring(x0, z0, x1, z1, floor_y=T, top=roof - 1,
                          stories=(f2, f3))
        common = dict(pitch=3, phase=0, pier=PANEL, corner=PANEL,
                      bands={-1: PANEL2, 0: PANEL2})
        glass = Facade(SKYGLASS, **common)
        rules = {name: glass for name in walls}
        rules["south"] = Facade(TINTED, **common)
        self.assertEqual(laid(rules, walls),
                         lab_hq_reference(x0, z0, x1, z1, T, f2, f3, roof))

    def test_the_lab_hall_wears_a_two_row_head(self):
        """The main hall's wings (``mainlab``): the same piers, glass up to
        two rows under the top, and light gray in those two."""
        x0, z0, x1, z1 = 806, 964, 836, 992
        walls = Wall.ring(x0, z0, x1, z1, floor_y=66, top=73)
        rule = Facade(SKYGLASS, pitch=3, phase=0, pier=PANEL, corner=PANEL,
                      head=(2, PANEL2))
        self.assertEqual(laid(rule, walls),
                         lab_hall_reference(x0, z0, x1, z1, 66, 73))

    def test_a_world_locked_phase_holds_west_and_north_of_zero(self):
        """An integer phase locks the bays to the world on both sides of
        zero, where most of JeffWorld stands: a wall at x -19..-2 is the
        same wall moved 40 east -- ten pitches -- cell for cell, and so is
        one at z -19..-2 moved 40 south. Its first bay, from -19 to the
        pier at -16, is glazed with the rest; a division that truncated
        toward zero would start the bays one pitch late and leave it
        blank."""
        rule = Facade(PANEL, pitch=4, phase=0, pier=LOG,
                      window=Opening.rect(None, 2, sill=2, fill=PANE))
        for start, end, outward, (dx, dz) in (
                ((-19, -30), (-2, -30), "north", (40, 0)),
                ((-30, -19), (-30, -2), "west", (0, 40))):
            with self.subTest(outward=outward):
                west = Wall(start, end, floor_y=63, top=68, outward=outward)
                east = Wall((start[0] + dx, start[1] + dz),
                            (end[0] + dx, end[1] + dz), floor_y=63, top=68,
                            outward=outward)
                moved = {(x + dx, y, z + dz): state
                         for (x, y, z), state in rule.plan(west).items()}
                self.assertEqual(moved, rule.plan(east))
                glazed = sorted({x + z + 30 for (x, y, z), state
                                 in rule.plan(west).items()
                                 if state.name == PANE})
                self.assertEqual(glazed[:3], [-19, -18, -17])
                self.assertEqual([a for a in west.along
                                  if rule.kind(west, a) == "pier"],
                                 [-16, -12, -8, -4])

    def test_bays_count_along_each_walls_own_run(self):
        """The stables (``stables``) tested ``x % 4`` along the walls whose
        x is 741 or 757 and ``z % 4`` along the ones at z 924 and 936: the
        north and south walls came out all posts, all 17 columns of them,
        and the east and west walls with none -- as they stand in JeffWorld
        (read 2026-10-21). A facade counts each wall along its own run, so
        its posts fall every fourth column of every wall, and its band of
        panes fills the bays between them."""
        x0, z0, x1, z1, plane, top = 741, 924, 757, 936, 66, 72
        walls = Wall.ring(x0, z0, x1, z1, floor_y=plane, top=top)
        rule = Facade(PANEL, pitch=4, phase=0, pier=LOG, corner=LOG,
                      window=Opening.rect(None, 2, sill=3, fill=PANE))
        self.assertEqual(laid(rule, walls),
                         stables_reference(x0, z0, x1, z1, plane, top,
                                           as_built=False))
        for name, wall in walls.items():
            posts = [a for a in wall.along if rule.kind(wall, a) != "bay"]
            with self.subTest(wall=name):
                self.assertEqual(
                    posts, [a for a in wall.along
                            if a % 4 == 0 or wall._end(a)])
                self.assertLess(len(posts), len(wall.along), "not all posts")
                self.assertTrue(posts, "and not none")
        # The script as it stands: every column of two walls a post, and
        # none in the other two.
        built = stables_reference(x0, z0, x1, z1, plane, top, as_built=True)
        self.assertTrue(all(built[(x, y, z0)] == LOG
                            for x in range(x0, x1 + 1) for y in walls["north"].rows))
        self.assertFalse(any(built[(x0, y, z)] == LOG
                             for z in walls["west"].along for y in walls["west"].rows))

    def test_two_walls_given_one_phase_agree_on_every_pier(self):
        """Bays lock to the world: ``along`` is the world's own coordinate,
        so walls that are given the same integer phase put their piers in
        the same congruence class wherever they stand, and the default --
        each wall's own low end -- does not."""
        rule = Facade(SKYGLASS, pitch=3, phase=0, pier=PANEL)
        first = Wall((2, 4), (12, 4), floor_y=63, top=67, outward="north")
        second = Wall((15, 9), (29, 9), floor_y=63, top=67, outward="north")
        third = Wall((7, 20), (7, 31), floor_y=63, top=67, outward="west")
        for wall in (first, second, third):
            self.assertEqual([a for a in wall.along
                              if rule.kind(wall, a) == "pier"],
                             [a for a in wall.along if a % 3 == 0])
        default = Facade(SKYGLASS, pitch=3, pier=PANEL)
        shifted = Wall((16, 9), (29, 9), floor_y=63, top=67, outward="north")
        self.assertEqual([a for a in shifted.along
                          if default.kind(shifted, a) == "pier"],
                         list(range(16, 30, 3)), "from its own low end")
        self.assertEqual({a % 3 for a in first.along
                          if default.kind(first, a) == "pier"}, {2})
        self.assertEqual({a % 3 for a in shifted.along
                          if default.kind(shifted, a) == "pier"}, {1},
                         "so two walls left to their defaults disagree")

    def test_the_default_phase_counts_every_wall_from_the_rings_corner(self):
        walls = Wall.ring(5, 7, 17, 22, floor_y=63, top=67)
        rule = Facade(SKYGLASS, pitch=4, pier=PANEL, corner=TRAV_CUT)
        piers = {name: [a for a in wall.along if rule.kind(wall, a) == "pier"]
                 for name, wall in walls.items()}
        self.assertEqual(piers["north"], piers["south"])
        self.assertEqual(piers["north"], [9, 13])
        self.assertEqual(piers["east"], piers["west"])
        self.assertEqual(piers["east"], [11, 15, 19])
        self.assertEqual(rule.kind(walls["north"], 5), "corner")

    def test_a_window_centers_in_its_bay_rounding_toward_lower_along(self):
        """Odd slack leaves its extra column on the high-along side: a
        2-wide window in a 5-wide bay starts one column past the pier and
        leaves two after it."""
        wall = Wall((0, 0), (24, 0), floor_y=63, top=69, outward="north")
        rule = Facade(SKYGLASS, pitch=6, phase=0, pier=PANEL,
                      window=Opening.rect(2, 2, sill=2))
        plan = rule.plan(wall)
        glazed = sorted({x for (x, y, z), state in plan.items()
                         if state.name == AIR})
        self.assertEqual(glazed, [2, 3, 8, 9, 14, 15, 20, 21])
        self.assertEqual(sorted({y for (x, y, z), state in plan.items()
                                 if state.name == AIR}), [65, 66])
        offset = Facade(SKYGLASS, pitch=6, phase=0, pier=PANEL,
                        window=Opening.rect(2, 2, sill=2), window_offset=4)
        self.assertEqual(sorted({x for (x, y, z), s in offset.plan(wall).items()
                                 if s.name == AIR}),
                         [4, 5, 10, 11, 16, 17, 22, 23])

    def test_a_window_that_does_not_fit_a_truncated_bay_is_left_out(self):
        """A window is placed whole or not at all: never truncated by the
        wall's end, never against its end column, never within the margin.
        A window that fills its bay glazes whichever of its columns the wall
        holds."""
        wall = Wall((0, 0), (10, 0), floor_y=63, top=67, outward="north")
        whole = Facade(SKYGLASS, pitch=4, phase=0, pier=PANEL,
                       window=Opening.rect(3, 1, sill=2))
        self.assertEqual(sorted({x for (x, y, z), s in whole.plan(wall).items()
                                 if s.name == AIR}), [1, 2, 3, 5, 6, 7],
                         "the bay 9..11 runs off the wall at 10")
        margin = Facade(SKYGLASS, pitch=4, phase=0, pier=PANEL, margin=2,
                        window=Opening.rect(3, 1, sill=2))
        self.assertEqual(sorted({x for (x, y, z), s in margin.plan(wall).items()
                                 if s.name == AIR}), [5, 6, 7])
        filling = Facade(SKYGLASS, pitch=4, phase=0, pier=PANEL,
                         window=Opening.rect(None, 1, sill=2, fill=PANE))
        self.assertEqual(sorted({x for (x, y, z), s in
                                 filling.plan(wall).items()
                                 if s.name == PANE}),
                         [1, 2, 3, 5, 6, 7, 9, 10], "the clipped bay too")
        ends = Wall((0, 0), (10, 0), floor_y=63, top=67, outward="north",
                    ends=True)
        self.assertEqual(sorted({x for (x, y, z), s in
                                 filling.plan(ends).items()
                                 if s.name == PANE}),
                         [1, 2, 3, 5, 6, 7, 9], "but never a corner")

    def test_bands_and_the_plinth_follow_the_stories(self):
        """The Roman insula unit (``jw_districts.unit``): brick for the
        ground story's first three rows, a timber band on each upper floor's
        row, stucco over it, cell for cell."""
        for floors in (2, 3):
            with self.subTest(floors=floors):
                base = 64
                walls = Wall.ring(10, 20, 22, 30, floor_y=base,
                                  top=base + 4 * floors,
                                  stories=[base + 4 * f
                                           for f in range(1, floors)])
                rule = Facade(STUCCO, plinth=(3, BRICK), bands={0: TIMBER})
                self.assertEqual(laid(rule, walls),
                                 insula_reference(10, 20, 22, 30, base, floors))

    def test_the_insula_windows_its_upper_stories_and_keeps_its_margin(self):
        """The unit's street face, windows and all: every upper story
        (``window_stories=range(1, floors)``) and never the ground one,
        every third column two clear of each end (``margin=2``), a top
        stair over each. Its reference stops short of the last two columns,
        which is the margin."""
        for floors in (2, 3):
            with self.subTest(floors=floors):
                base = 64
                walls = Wall.ring(10, 20, 22, 30, floor_y=base,
                                  top=base + 4 * floors,
                                  stories=[base + 4 * f
                                           for f in range(1, floors)])
                courses = dict(plinth=(3, BRICK), bands={0: TIMBER})
                street = Facade(
                    STUCCO, pitch=3, window_offset=2, margin=2,
                    window_stories=range(1, floors),
                    window=Opening.rect(
                        1, 1, sill=2,
                        head=f"{TRAV_STAIR}[facing=south,half=top]"),
                    **courses)
                rules = {name: Facade(STUCCO, **courses) for name in walls}
                rules["south"] = street
                self.assertEqual(
                    laid(rules, walls),
                    insula_reference(10, 20, 22, 30, base, floors,
                                     windows=True))

    def test_where_two_stories_bands_meet_the_lower_storys_wins(self):
        """Stories three rows apart and bands under the floor and two rows
        over it: row 68 is two over the floor at 66 and one under the floor
        at 69, and takes the lower story's band."""
        wall = Wall((0, 0), (8, 0), floor_y=63, top=74, outward="north",
                    stories=(66, 69))
        rule = Facade(STUCCO, bands={-1: BRICK, 2: TIMBER})
        plan = rule.plan(wall)
        self.assertEqual([plan[(3, y, 0)].name for y in wall.rows],
                         [STUCCO, BRICK, STUCCO, STUCCO, TIMBER, STUCCO,
                          STUCCO, TIMBER, STUCCO, STUCCO, STUCCO])

    def test_a_window_never_stands_against_a_walls_end_column(self):
        """A window of its own width keeps a column of wall at each end
        even with no margin; the bay at 9..11 would put this one on the
        wall's last column, 10, and it is left out."""
        wall = Wall((0, 0), (10, 0), floor_y=63, top=67, outward="north")
        rule = Facade(SKYGLASS, pitch=4, phase=0, pier=PANEL,
                      window=Opening.rect(1, 1, sill=2))
        self.assertEqual(sorted({x for (x, y, z), s in rule.plan(wall).items()
                                 if s.name == AIR}), [2, 6])

    def test_the_head_course_crowns_the_bays_and_not_the_piers(self):
        wall = Wall((0, 0), (8, 0), floor_y=63, top=72, outward="north",
                    stories=(68,))
        rule = Facade(STUCCO, pitch=4, phase=0, pier=TRAV_CUT,
                      plinth=(5, BRICK), bands={0: TIMBER, 1: TIMBER},
                      head=(2, "minecraft:granite"))
        plan = rule.plan(wall)
        column = [plan[(1, y, 0)].name for y in wall.rows]
        self.assertEqual(column, [BRICK] * 4 + [TIMBER, TIMBER, STUCCO]
                         + ["minecraft:granite"] * 2,
                         "a band over the plinth, a head over the field")
        self.assertEqual({plan[(4, y, 0)].name for y in wall.rows}, {TRAV_CUT})

    def test_a_mix_is_picked_at_the_world_cell_and_a_material_is_its_block(self):
        mix = Mix([(SKYGLASS, 3), ("minecraft:cyan_stained_glass", 1)], seed=3)
        wall = Wall((2, 4), (20, 4), floor_y=63, top=68, outward="north")
        plan = Facade(mix, pitch=3, phase=0,
                      pier=Material("minecraft:quartz_stairs")).plan(wall)
        for (x, y, z), state in plan.items():
            if x % 3:
                self.assertEqual(state, BlockState.parse(mix.pick(x, y, z)))
            else:
                self.assertEqual(state.name, "minecraft:quartz_block")
        self.assertGreater(len({s.name for s in plan.values()}), 2)

    def test_materials_are_named_never_a_palettes(self):
        with self.assertRaises(TypeError):
            Facade()
        for field in (FROM_PALETTE, None, [(STUCCO, 1)]):
            with self.subTest(field=field):
                with self.assertRaises(TypeError):
                    Facade(field)
        with self.assertRaises(TypeError):
            Facade(STUCCO, pitch=3, pier=FROM_PALETTE)
        with self.assertRaises(TypeError):
            Opening.rect(1, 2, fill=FROM_PALETTE)

    def test_bad_facades_are_refused_by_name(self):
        wall = Wall((0, 0), (12, 0), floor_y=63, top=72, outward="north",
                    stories=(68,))
        for call, words in (
                (lambda: Facade(STUCCO, pier=BRICK), "no pitch"),
                (lambda: Facade(STUCCO, window=Opening.rect(1, 1)),
                 "no pitch"),
                (lambda: Facade(STUCCO, pitch=3, pier=BRICK, pier_width=3),
                 "leaves no bay"),
                (lambda: Facade(STUCCO, pitch=3, pier=BRICK,
                                window=Opening.rect(3, 1)),
                 "does not fit a bay 2 wide"),
                (lambda: Facade(STUCCO, pitch=4, pier=BRICK, window_offset=0,
                                window=Opening.rect(2, 1)), "on a pier"),
                (lambda: Facade(STUCCO, pitch=4, pier=BRICK, window_offset=1,
                                window=Opening.rect(None, 1)), "no offset"),
                (lambda: Facade(STUCCO, pitch=4, pier=BRICK,
                                window=Opening.rect(1, 5, sill=1)).plan(wall),
                 "past the row under the next floor"),
                (lambda: Facade(STUCCO, pitch=4, pier=BRICK, window_stories=(2,),
                                window=Opening.rect(1, 1)).plan(wall),
                 "stories 0..1"),
                (lambda: Facade(STUCCO, plinth=(0, BRICK)), "at least one row")):
            with self.subTest(words=words):
                with self.assertRaises(NBTError) as caught:
                    call()
                self.assertIn(words, str(caught.exception))


class OpeningTests(unittest.TestCase):
    def test_an_arch_springs_from_its_jambs(self):
        """The cells at the foot of the head are top stairs whose full side
        is toward the jamb beside them -- the game's own sturdy face -- as
        the castle example's windows and entrance spring theirs; the
        middle is open, and a keystone sits over an odd arch's crown."""
        runs = (Wall((0, 5), (20, 5), floor_y=63, top=75, outward="north"),
                Wall((5, 0), (5, 20), floor_y=63, top=75, outward="west"))
        for wall in runs:
            low, high = (("west", "east") if wall.axis == "x"
                         else ("north", "south"))
            for width in (2, 3, 5):
                with self.subTest(axis=wall.axis, width=width):
                    arch = Opening.arch(width, 4, springer=SPRINGER,
                                        keystone=None if width % 2 == 0
                                        else "minecraft:chiseled_stone_bricks")
                    cells = arch.cells(wall, 3)
                    head = [cells[wall.cell(3 + k, 67)] for k in range(width)]
                    for state, jamb in ((head[0], low), (head[-1], high)):
                        self.assertTrue(state.name.endswith("_stairs"))
                        self.assertEqual(state.properties["half"], "top")
                        self.assertTrue(facts(state.name, state.properties)
                                        .holds(jamb), f"sturdy {jamb}")
                    for state in head[1:-1]:
                        self.assertEqual(state.name, AIR)
                    for k in range(width):
                        for y in (64, 65, 66):
                            self.assertEqual(cells[wall.cell(3 + k, y)].name, AIR)
                    key = cells.get(wall.cell(3 + (width - 1) // 2, 68))
                    if width % 2:
                        self.assertEqual(key.name, "minecraft:chiseled_stone_bricks")
                    else:
                        self.assertIsNone(key)

    def test_an_arched_opening_with_a_pointed_profile(self):
        """A lancet's head follows ``pointed_arch_profile``, a vault's
        section turned upside down in the same half blocks: each column
        clear to its fall below the crown, a top slab over half a block, a
        springer facing its jamb where the head falls a whole block or more
        to the column outside."""
        wall = Wall((0, 5), (20, 5), floor_y=63, top=75, outward="north")
        profile = pointed_arch_profile(2, 3)
        self.assertEqual(profile, (0, 1, 6))
        arch = Opening.arch(5, 4, springer=SPRINGER, profile=profile,
                            slab="minecraft:stone_brick_slab")
        cells = arch.cells(wall, 3)

        def column(k):
            return [str(cells.get(wall.cell(3 + k, y), "wall"))
                    for y in range(64, 68)]

        air = "minecraft:air"
        top_slab = "minecraft:stone_brick_slab[type=top,waterlogged=false]"
        self.assertEqual(column(2), [air] * 4, "the crown")
        self.assertEqual(column(1), [air] * 3 + [top_slab])
        self.assertEqual(column(3), column(1))
        self.assertEqual(column(0)[0], air)
        self.assertEqual(column(0)[2:], ["wall", "wall"],
                         "over the springer the wall stands")
        self.assertIn("facing=west,half=top", column(0)[1])
        self.assertIn("facing=east,half=top", column(4)[1])
        # A round head on the same span falls less, and its own way.
        round_ = Opening.arch(5, 4, springer=Material("minecraft:stone_bricks"),
                              profile=arch_profile(2, 2)).cells(wall, 3)
        self.assertNotEqual(round_, cells)
        # With no profile, the castle's flat head: one springer each end.
        flat = Opening.arch(5, 4, springer=SPRINGER).cells(wall, 3)
        self.assertEqual(flat, Opening.arch(5, 4, springer=SPRINGER,
                                            profile=(0, 0, 2)).cells(wall, 3))
        for bad, words in ((dict(profile=(0, 1)), "has 3 drops"),
                           (dict(profile=(1, 2, 3)), "starts at 0"),
                           (dict(profile=(0, 4, 2)), "climbs outward"),
                           (dict(profile=(0, 2, 8)), "no whole clear row"),
                           (dict(profile=(0, 1, 2)), "pass slab="),
                           (dict(keystone=BRICK, width=4), "two")):
            with self.subTest(words=words):
                call = {**dict(width=5, height=4, springer=SPRINGER), **bad}
                with self.assertRaises(NBTError) as caught:
                    Opening.arch(**call)
                self.assertIn(words, str(caught.exception))
        with self.assertRaises(TypeError):
            Opening.arch(3, 3, springer=Mix([(SPRINGER, 1)]))
        with self.assertRaises(NBTError):
            Opening.arch(3, 3, springer=BRICK)

    def test_a_round_opening_is_the_disc_rule_on_its_side(self):
        wall = Wall((7, 0), (7, 20), floor_y=63, top=75, outward="east")
        cells = Opening.round(2, sill=2).cells(wall, 4)
        self.assertEqual(len(cells), 21)
        self.assertEqual({(z - 6, y - 67) for x, y, z in cells},
                         {(da, dy) for da in range(-2, 3) for dy in range(-2, 3)
                          if da * da + dy * dy <= 6})
        self.assertEqual({state.name for state in cells.values()}, {AIR})

    def test_a_thick_wall_is_glazed_on_its_outer_face_and_open_behind(self):
        wall = Wall((0, 10), (12, 10), floor_y=63, top=72, outward="south",
                    thickness=3)
        cells = Opening.rect(2, 2, sill=2, fill=PANE, head=TRAV_CUT).cells(wall, 4)
        for x in (4, 5):
            for y in (65, 66):
                self.assertEqual([cells[(x, y, z)].name for z in (10, 9, 8)],
                                 [PANE, AIR, AIR], "layer 0 is the outside")
            self.assertEqual([cells[(x, 67, z)].name for z in (10, 9, 8)],
                             [TRAV_CUT] * 3, "the lintel goes through")
        self.assertEqual(len(cells), 2 * 2 * 3 + 2 * 3)

    def test_a_keystone_is_on_the_outer_face_and_the_springers_go_through(self):
        wall = Wall((0, 10), (12, 10), floor_y=63, top=72, outward="south",
                    thickness=3)
        key = "minecraft:chiseled_stone_bricks"
        cells = Opening.arch(3, 3, springer=SPRINGER, keystone=key).cells(wall, 4)
        self.assertEqual(cells[wall.cell(5, 67)].name, key)
        for layer in (1, 2):
            self.assertNotIn(wall.cell(5, 67, layer), cells,
                             "behind the keystone the wall stands")
        for layer in (0, 1, 2):
            for a in (4, 6):
                self.assertEqual(cells[wall.cell(a, 66, layer)].name, SPRINGER)

    def test_an_opening_stays_on_its_wall(self):
        wall = Wall((0, 0), (8, 0), floor_y=63, top=68, outward="north")
        for call, words in (
                (lambda: Opening.rect(3, 2).cells(wall, 7), "off the wall"),
                (lambda: Opening.rect(1, 6).cells(wall, 2), "over the wall's top"),
                (lambda: Opening.rect(None, 2).cells(wall, 2), "facade's bay"),
                (lambda: Opening.rect(1, 2, sill=0), "sill=1"),
                (lambda: Opening.round(0), "radius of at least 1"),
                (lambda: Opening.arch(1, 3, springer=SPRINGER), "at least 2")):
            with self.subTest(words=words):
                with self.assertRaises(NBTError) as caught:
                    call()
                self.assertIn(words, str(caught.exception))


# --------------------------------------------------------------------------
# The writers: Builder.wall, shell and cut_opening
# --------------------------------------------------------------------------

GROUND = 63
STONE_BRICKS = "minecraft:stone_bricks"
PLANKS = "minecraft:spruce_planks"
GRANITE = "minecraft:granite"
IRON_DOOR = "minecraft:iron_door"


def _region_digests(directory):
    region = os.path.join(directory, "dimensions/minecraft/overworld/region")
    files = {}
    for name in sorted(os.listdir(region)):
        with open(os.path.join(region, name), "rb") as handle:
            files[name] = hashlib.sha256(handle.read()).hexdigest()
    return files


class WallsBase(unittest.TestCase):
    """A save of nine chunks with stone up to y=63, the ground every wall
    here stands on."""

    def setUp(self):
        self.save, self.dir = self.fresh()
        self.b = Builder(self.save)

    def fresh(self):
        directory = tempfile.mkdtemp(prefix="walls-tests-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        make_save(directory, CHUNKS)
        save = MinecraftSave.load(directory)
        save.fill((0, 50, 0), (47, GROUND, 47), "minecraft:stone")
        return save, directory

    def name(self, x, y, z, save=None):
        return (save or self.save).get_block(x, y, z).name

    def opens(self, door):
        def block_at(x, y, z):
            return self.save.get_block(x, y, z)
        return [iron_door_opens(block_at, door, side)
                for side in ((0, 1), (0, -1))]


class ShellTests(WallsBase):
    def test_floors_stop_at_a_void_and_count_it_as_kept_open(self):
        x0, z0, x1, z1 = 4, 6, 18, 20
        level = GROUND + 6
        well = {(x, z) for x in (10, 11) for z in (12, 13)}
        result = self.b.shell(x0, z0, x1, z1, Facade(STONE_BRICKS),
                              floor_y=GROUND, top=GROUND + 11, stories=(level,),
                              floor="minecraft:oak_planks", voids=well)
        self.assertEqual(result.kept_open, 4)
        self.assertEqual(self.save.get_block(10, level, 12).name, AIR)
        self.assertEqual(self.save.get_block(9, level, 12).name,
                         "minecraft:oak_planks")

    def test_a_shell_is_the_mass_and_the_hollow_in_one_plan(self):
        """The m4 insula's shell (tools/roman_city/m4_districts.py,
        ``insula``) is four fills: the mass, the plinth over it, a quoin up
        each corner, and the hollow. One shell is the same building, every
        cell of the box, and nothing outside the footprint changes."""
        x0, z0, x1, z1 = 4, 6, 18, 20
        top = GROUND + 6 * 2
        tint, base = "minecraft:mud_bricks", STONE_BRICKS
        ref, _ = self.fresh()
        r = Builder(ref)
        r.fill((x0, GROUND + 1, z0), (x1, top, z1), tint)
        r.fill((x0, GROUND + 1, z0), (x1, GROUND + 5, z1), base)
        for qx, qz in ((x0, z0), (x0, z1), (x1, z0), (x1, z1)):
            r.fill((qx, GROUND + 1, qz), (qx, top, qz), TRAV_CUT)
        r.fill((x0 + 1, GROUND + 1, z0 + 1), (x1 - 1, top, z1 - 1), AIR)
        rule = Facade(tint, plinth=(5, base), corner=TRAV_CUT)

        result = self.b.shell(x0, z0, x1, z1, rule, floor_y=GROUND, top=top)
        self.assertIsInstance(result, WallResult)
        self.assertEqual(result.outline, (x0, z0, x1, z1))
        # And laid over the mass itself -- the m4's first three fills
        # standing -- the walls are already there and the hollow is what
        # the shell cuts out of it, every row of it up to the top.
        massed, _ = self.fresh()
        m = Builder(massed)
        m.fill((x0, GROUND + 1, z0), (x1, top, z1), tint)
        m.fill((x0, GROUND + 1, z0), (x1, GROUND + 5, z1), base)
        for qx, qz in ((x0, z0), (x0, z1), (x1, z0), (x1, z1)):
            m.fill((qx, GROUND + 1, qz), (qx, top, qz), TRAV_CUT)
        over = m.shell(x0, z0, x1, z1, rule, floor_y=GROUND, top=top)
        rim = 2 * ((x1 - x0) + (z1 - z0)) * (top - GROUND)
        self.assertEqual(over.already, rim, "every wall cell")
        for built in (self.save, massed):
            for x in range(x0 - 2, x1 + 3):
                for z in range(z0 - 2, z1 + 3):
                    for y in range(GROUND - 1, top + 3):
                        self.assertEqual(built.get_block(x, y, z),
                                         ref.get_block(x, y, z), (x, y, z))

    def test_stories_stack_their_floors_bands_and_hollows(self):
        """Over an inside packed solid, each story's hollow runs from the
        row over its floor to the row under the next, the last one to the
        top, and every floor row keeps its floor."""
        self.save.fill((5, 64, 5), (13, 77, 11), "minecraft:dirt")
        result = self.b.shell(
            4, 4, 14, 12, Facade(STUCCO, plinth=(2, BRICK), bands={0: TIMBER}),
            floor_y=GROUND, top=77, stories=(68, 73), floor=PLANKS,
            deck=GRANITE, overwrite=True)
        self.assertEqual(result.floors, (63, 68, 73))
        self.assertEqual(result.top, 77)
        column = [AIR] * 4 + [PLANKS] + [AIR] * 4 + [PLANKS] + [AIR] * 4 + [GRANITE]
        for x in range(5, 14):
            for z in range(5, 12):
                self.assertEqual([self.name(x, y, z) for y in range(64, 79)],
                                 column, (x, z))
        face = [self.name(9, y, 4) for y in range(64, 78)]
        self.assertEqual(face, [BRICK] * 2 + [STUCCO] * 2 + [TIMBER]
                         + [STUCCO] * 4 + [TIMBER] + [STUCCO] * 4)

    def test_two_shells_given_one_phase_agree_on_their_piers(self):
        """Bays lock to the world: two shells side by side with one
        facade, phase 0, keep one rhythm across both, their corners
        apart."""
        rule = Facade(SKYGLASS, pitch=3, phase=0, pier=PANEL, corner=TRAV_CUT)
        self.b.shell(2, 4, 12, 12, rule, floor_y=GROUND, top=68)
        self.b.shell(13, 4, 26, 12, rule, floor_y=GROUND, top=68)
        for z in (4, 12):
            with self.subTest(z=z):
                self.assertEqual(
                    [x for x in range(2, 27) if self.name(x, 66, z) == PANEL],
                    [x for x in range(2, 27)
                     if x % 3 == 0 and x not in (2, 12, 13, 26)])
                self.assertEqual(
                    [x for x in range(2, 27)
                     if self.name(x, 66, z) == TRAV_CUT], [2, 12, 13, 26])

    def test_a_shell_refuses_what_stands_and_overwrite_builds_through_it(self):
        self.save.set_block(8, 65, 4, "minecraft:oak_planks")
        rule = Facade(STUCCO)
        with self.assertRaises(Refused) as caught:
            self.b.shell(4, 4, 14, 12, rule, floor_y=GROUND, top=68)
        self.assertEqual(caught.exception.faults,
                         [f"(8, 65, 4) holds minecraft:oak_planks, where the "
                          f"shell needs {STUCCO}"])
        self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(self.name(4, 64, 4), AIR, "nothing was written")
        self.b.shell(4, 4, 14, 12, rule, floor_y=GROUND, top=68,
                     overwrite=True)
        self.assertEqual(self.name(8, 65, 4), STUCCO)
        # Laid again over itself, every cell is already right.
        before = self.b.blocks_set
        again = self.b.shell(4, 4, 14, 12, rule, floor_y=GROUND, top=68,
                             hollow=False)
        self.assertEqual(again.already, again.blocks)
        self.assertEqual(self.b.blocks_set, before)

    def test_a_door_hung_in_an_opening_is_the_opening_already(self):
        rule = Facade(STUCCO)
        door = ("south", Opening.rect(1, 2), 9)
        result = self.b.shell(4, 4, 14, 12, rule, floor_y=GROUND, top=68,
                              openings=[door])
        self.assertEqual(result.openings, 2)
        self.b.doorway(*result.walls["south"].door(9))
        before = self.b.blocks_set
        again = self.b.shell(4, 4, 14, 12, rule, floor_y=GROUND, top=68,
                             openings=[door], hollow=False)
        self.assertEqual(self.b.blocks_set, before, "nothing to write")
        self.assertEqual(again.already, again.blocks)
        self.assertEqual(self.name(9, 64, 12), "minecraft:oak_door")
        self.assertEqual(self.b.cut_opening(result.walls["south"],
                                            Opening.rect(1, 2), 9), [])
        self.assertEqual(self.name(9, 65, 12), "minecraft:oak_door")

    def test_water_or_a_cobweb_in_a_doorway_stands_in_it(self):
        """Only a door or a gate hung in an opening is the hole already
        there. Water left in a doorway beside the hollowed floor would
        flood the house; it, powder snow, a cobweb and scaffolding are
        each something in the way, refused by name, and overwrite clears
        the doorway to air."""
        door = ("south", Opening.rect(1, 2), 9)
        for block in ("minecraft:water", "minecraft:bubble_column",
                      "minecraft:powder_snow", "minecraft:cobweb",
                      "minecraft:scaffolding"):
            with self.subTest(block=block):
                save, _ = self.fresh()
                b = Builder(save)
                save.set_block(9, 64, 12, block)
                with self.assertRaises(Refused) as caught:
                    b.shell(4, 4, 14, 12, Facade(STUCCO), floor_y=GROUND,
                            top=68, openings=[door])
                self.assertEqual(len(caught.exception.faults), 1)
                self.assertIn(f"(9, 64, 12) holds {block}",
                              caught.exception.faults[0])
                self.assertIn("where the shell needs minecraft:air",
                              caught.exception.faults[0])
                self.assertEqual(b.blocks_set, 0)
                result = b.shell(4, 4, 14, 12, Facade(STUCCO), floor_y=GROUND,
                                 top=68, openings=[door], overwrite=True)
                self.assertEqual(self.name(9, 64, 12, save), AIR)
                self.assertEqual(result.already, 1, "only the empty head cell")

    def test_a_thick_shell_floors_its_inside_and_its_door_goes_through(self):
        result = self.b.shell(4, 4, 14, 12, Facade(STONE_BRICKS),
                              floor_y=GROUND, top=73, thickness=2,
                              stories=(68,), floor=PLANKS,
                              openings=[("south", Opening.rect(1, 2), 9)])
        self.assertEqual(result.walls["east"].along, range(6, 11))
        self.assertEqual([self.name(x, 66, 8) for x in range(4, 15)],
                         [STONE_BRICKS] * 2 + [AIR] * 7 + [STONE_BRICKS] * 2)
        self.assertEqual([self.name(x, 68, 8) for x in range(4, 15)],
                         [STONE_BRICKS] * 2 + [PLANKS] * 7 + [STONE_BRICKS] * 2,
                         "the floor is inside both layers")
        self.assertEqual([self.name(9, 68, z) for z in range(4, 13)],
                         [STONE_BRICKS] * 2 + [PLANKS] * 5 + [STONE_BRICKS] * 2)
        for z in (12, 11):
            self.assertEqual([self.name(9, y, z) for y in (64, 65, 66)],
                             [AIR, AIR, STONE_BRICKS], f"layer at z={z}")

    def test_ground_replaces_the_pad_the_shell_stands_on(self):
        self.b.shell(4, 4, 14, 12, Facade(STUCCO), floor_y=GROUND, top=68,
                     ground=GRANITE)
        self.assertEqual({self.name(x, GROUND, z) for x in range(4, 15)
                          for z in range(4, 13)}, {GRANITE})
        for cell in ((3, GROUND, 8), (15, GROUND, 8), (9, GROUND, 3),
                     (9, GROUND, 13), (9, GROUND - 1, 8)):
            self.assertEqual(self.name(*cell), "minecraft:stone", cell)
        self.assertEqual(self.name(9, GROUND + 1, 8), AIR)

    def test_an_opening_goes_in_the_story_it_names(self):
        self.b.shell(4, 4, 14, 12, Facade(STUCCO), floor_y=GROUND, top=73,
                     stories=(68,), floor=PLANKS,
                     openings=[("north", Opening.rect(1, 2), 8, 1)])
        self.assertEqual([self.name(8, y, 4) for y in range(64, 74)],
                         [STUCCO] * 5 + [AIR, AIR] + [STUCCO] * 3)

    def test_shell_floors_leave_a_flights_stairwell_open_and_hollow_refuses_one(self):
        stair = StraightStair((8, 64, 10), "north", 5)
        self.assertEqual(stair.story_levels(), (GROUND, 68))
        self.b.flight(stair, walk=False)
        built = self.b.blocks_set
        rule = Facade(STUCCO)
        with self.assertRaises(Refused) as caught:
            self.b.shell(4, 4, 14, 12, rule, floor_y=GROUND, top=73,
                         stories=(68,), floor=PLANKS)
        self.assertIn("build the shell before its flights",
                      str(caught.exception))
        self.assertEqual(self.b.blocks_set, built, "nothing was written")
        result = self.b.shell(4, 4, 14, 12, rule, floor_y=GROUND, top=73,
                              stories=(68,), floor=PLANKS, hollow=False)
        well = stair.claimed(68)
        self.assertEqual(result.kept_open, len(well))
        self.assertGreater(result.kept_open, 1)
        for x, z in well:
            self.assertNotEqual(self.name(x, 68, z), PLANKS, (x, z))
        self.assertEqual(self.name(8, 68, 5), PLANKS, "the floor you step onto")

    def test_a_flight_climbs_a_shell_and_the_house_walks_both_ways(self):
        """In at the door, up the flight through the floor it cut, round
        the upper story, and back out again: the round trip, from the
        doorstep outside."""
        rule = Facade(STUCCO, pitch=4, phase=0, pier=TRAV_CUT)
        result = self.b.shell(4, 4, 14, 12, rule, floor_y=GROUND, top=73,
                              stories=(68,), floor=PLANKS, deck=GRANITE)
        self.b.doorway(*result.walls["south"].door(9))
        flight = self.b.flight(StraightStair((8, 64, 10), "north", 5),
                               floor_y=68)
        self.assertGreater(flight.cut, 0, "the flight cut its stairwell")
        walk = reachable(self.save, (9, GROUND, 13), (3, 60, 3), (15, 76, 15))
        self.assertFalse(walk.stranded)
        upstairs = {(x, z) for x, y, z in walk.reached if y == 68}
        self.assertIn((5, 5), upstairs, "the far corner of the upper story")
        self.assertIn((13, 11), upstairs)

    def test_an_iron_door_beside_a_pier_opens_and_one_between_panes_is_refused(self):
        """A glazed bay gives an iron door a jamb to carry its buttons only
        beside a pier: the lab's HQ hung its door between two panes."""
        curtain = Facade(SKYGLASS, pitch=3, phase=0, pier=PANEL, corner=PANEL)
        result = self.b.shell(4, 4, 16, 12, curtain, floor_y=GROUND, top=68)
        south = result.walls["south"]
        self.assertEqual((curtain.kind(south, 6), curtain.kind(south, 7)),
                         ("pier", "bay"))
        self.b.doorway(*south.door(7), door=IRON_DOOR)
        self.assertEqual(self.opens((7, 64, 12)), [True, True])
        walk = reachable(self.save, (7, GROUND, 13), (3, 60, 3), (17, 70, 13))
        self.assertIn((10, GROUND, 8), walk.reached)
        self.assertFalse(walk.stranded)

        wide = Facade(SKYGLASS, pitch=4, phase=0, pier=PANEL, corner=PANEL)
        other = self.b.shell(20, 4, 32, 12, wide, floor_y=GROUND, top=68)
        self.assertEqual(wide.kind(other.walls["south"], 26), "bay")
        before = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.doorway(*other.walls["south"].door(26), door=IRON_DOOR)
        self.assertIn("conducting", str(caught.exception))
        self.assertEqual(self.b.blocks_set, before)
        self.assertEqual(self.name(26, 64, 12), SKYGLASS)

    def test_a_shell_will_not_glaze_over_a_doors_opener(self):
        """Rebuilt in glass over a stone wall with an iron door in it, the
        bay beside the door is where both its buttons hang: the shell is
        refused for the door it would seal, and builds once a pier keeps
        that column stone."""
        stone = Facade(STONE_BRICKS)
        result = self.b.shell(4, 4, 14, 12, stone, floor_y=GROUND, top=68)
        self.b.doorway(*result.walls["south"].door(9), door=IRON_DOOR)
        self.assertEqual(self.opens((9, 64, 12)), [True, True])
        before = self.b.blocks_set
        door = ("south", Opening.rect(1, 2), 9)
        with self.assertRaises(Refused) as caught:
            self.b.shell(4, 4, 14, 12, Facade(SKYGLASS, corner=STONE_BRICKS),
                         floor_y=GROUND, top=68, openings=[door],
                         hollow=False, overwrite=True)
        self.assertIn("sealed door",
                      [fault.kind for fault in caught.exception.faults])
        self.assertEqual(self.b.blocks_set, before)
        self.assertEqual(self.name(8, 65, 12), STONE_BRICKS)
        self.b.shell(4, 4, 14, 12,
                     Facade(SKYGLASS, pitch=3, phase=2, pier=STONE_BRICKS,
                            corner=STONE_BRICKS),
                     floor_y=GROUND, top=68, openings=[door], hollow=False,
                     overwrite=True)
        self.assertEqual(self.name(10, 65, 12), SKYGLASS)
        self.assertEqual(self.name(9, 64, 12), IRON_DOOR, "the door is kept")
        self.assertEqual(self.opens((9, 64, 12)), [True, True])

    def test_a_shell_will_not_put_a_chest_under_a_floor_that_conducts(self):
        self.save.set_block(9, 67, 8, "minecraft:chest[facing=south]")
        common = dict(floor_y=GROUND, top=73, stories=(68,), hollow=False)
        with self.assertRaises(Refused) as caught:
            self.b.shell(4, 4, 14, 12, Facade(STUCCO), floor=PLANKS, **common)
        self.assertEqual([(f.cell, f.kind) for f in caught.exception.faults],
                         [((9, 67, 8), "blocked chest")])
        self.assertEqual(self.b.blocks_set, 0)
        self.b.shell(4, 4, 14, 12, Facade(STUCCO), floor="minecraft:glass",
                     **common)
        self.assertEqual(self.name(9, 68, 8), "minecraft:glass")

    def test_the_parapet_and_the_battlement_share_one_perimeter(self):
        """The shell lays a solid course on the deck's rim; a battlement
        over the shell's own perimeter takes that course as it stands and
        stands its merlons on it, every corner one."""
        result = self.b.shell(4, 4, 15, 12, Facade(STONE_BRICKS),
                              floor_y=GROUND, top=68, deck=GRANITE,
                              parapet=STONE_BRICKS)
        self.assertEqual(result.perimeter,
                         ((4, 4), (15, 4), (15, 12), (4, 12), (4, 4)))
        rim = {(x, z) for x in range(4, 16) for z in range(4, 13)
               if x in (4, 15) or z in (4, 12)}
        self.assertEqual({(x, z) for x in range(2, 18) for z in range(2, 15)
                          if self.name(x, 70, z) == STONE_BRICKS}, rim)
        before = self.b.blocks_set
        tops = self.b.battlement(result.perimeter, result.top + 2,
                                 block=STONE_BRICKS)
        self.assertEqual(self.b.blocks_set - before, len(tops),
                         "only the merlons were written")
        for corner in ((4, 4), (15, 4), (15, 12), (4, 12)):
            self.assertEqual(self.name(corner[0], 71, corner[1]), STONE_BRICKS,
                             f"corner {corner}")

    def test_a_taller_parapet_is_crenellated_at_its_top_row(self):
        """A parapet two blocks high stands on the rim at top + 2 and top +
        3, nowhere inside, and its battlement goes at its top row, top + 1 +
        parapet_height: at top + 2 its gaps would hold the parapet's upper
        row, and it is refused."""
        result = self.b.shell(4, 4, 15, 12, Facade(STONE_BRICKS),
                              floor_y=GROUND, top=68, deck=GRANITE,
                              parapet=STONE_BRICKS, parapet_height=2)
        rim = {(x, z) for x in range(4, 16) for z in range(4, 13)
               if x in (4, 15) or z in (4, 12)}
        for y in (70, 71):
            self.assertEqual({(x, z) for x in range(2, 18) for z in range(2, 15)
                              if self.name(x, y, z) == STONE_BRICKS}, rim, y)
        self.assertEqual({self.name(x, 72, z) for x, z in rim}, {AIR})
        before = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.battlement(result.perimeter, result.top + 2,
                              block=STONE_BRICKS)
        self.assertIn(f"holds {STONE_BRICKS}, where the battlement needs "
                      f"minecraft:air", caught.exception.faults[0])
        tops = self.b.battlement(result.perimeter, result.top + 1 + 2,
                                 block=STONE_BRICKS)
        self.assertEqual(self.b.blocks_set - before, len(tops),
                         "only the merlons were written")
        for corner in ((4, 4), (15, 4), (15, 12), (4, 12)):
            self.assertEqual(self.name(corner[0], 72, corner[1]), STONE_BRICKS,
                             f"corner {corner}")

    def test_no_shell_cell_goes_into_a_flights_claim_whatever_overwrite_says(self):
        """A flight built first claims its treads and the head room over
        them; a shell whose north wall would run through that head room is
        refused for each claimed cell, overwrite or not."""
        self.b.flight(StraightStair((8, 64, 10), "north", 5), walk=False)
        built = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.shell(4, 8, 14, 16, Facade(STONE_BRICKS), floor_y=GROUND,
                         top=70, hollow=False, overwrite=True)
        self.assertEqual(
            caught.exception.faults,
            [f"(8, {y}, 8) is claimed by a flight this session built, where "
             f"the shell would put {STONE_BRICKS}: build the shell before its "
             f"flights" for y in range(65, 70)])
        self.assertEqual(self.b.blocks_set, built, "nothing was written")
        self.assertEqual(self.name(8, 66, 8), "minecraft:oak_stairs")

    def test_each_wall_writer_names_a_block_the_game_lacks_before_it_reads(self):
        """The plan is checked whole before a cell is read, as every
        planned writer keeps it: a block the game does not have is named by
        IllegalBlock, and nothing is read or written."""
        reads = []
        read = self.b._read
        self.b._read = lambda cell: reads.append(cell) or read(cell)
        lacking = "minecraft:quartz_wall"
        near = Wall((4, 20), (16, 20), floor_y=GROUND, top=68, outward="north")
        for what, call in (
                ("shell", lambda: self.b.shell(4, 4, 14, 12, Facade(lacking),
                                               floor_y=GROUND, top=68)),
                ("wall", lambda: self.b.wall(near, Facade(lacking))),
                ("cut_opening", lambda: self.b.cut_opening(
                    near, Opening.rect(1, 2, fill=lacking), 8))):
            with self.subTest(what=what):
                with self.assertRaises(IllegalBlock):
                    call()
                self.assertEqual(reads, [], "nothing was read")
        self.assertEqual(self.b.blocks_set, 0)

    def test_a_shell_lays_its_roof_in_the_same_plan(self):
        roof = Roof(eaves=("north", "south"), block=PLANKS,
                    slab="minecraft:spruce_slab", stair="minecraft:spruce_stairs",
                    close_ends=PLANKS)
        self.b.shell(4, 4, 14, 12, Facade(STUCCO), floor_y=GROUND, top=68,
                     roof=roof, attic="hollow")
        self.save.save()
        other, directory = self.fresh()
        b = Builder(other)
        b.shell(4, 4, 14, 12, Facade(STUCCO), floor_y=GROUND, top=68)
        b.roof(roof, (4, 4, 14, 12), 69, attic="hollow")
        other.save()
        self.assertEqual(_region_digests(self.dir), _region_digests(directory))
        for call, words in (
                (dict(roof=roof, attic="hollow", deck=GRANITE), "pass the roof"),
                (dict(attic="hollow"), "pass roof="),
                (dict(roof=roof), "attic=None")):
            with self.subTest(words=words):
                with self.assertRaises((NBTError, TypeError)) as caught:
                    self.b.shell(4, 20, 14, 28, Facade(STUCCO), floor_y=GROUND,
                                 top=68, **call)
                self.assertIn(words, str(caught.exception))

    def test_bad_shells_are_refused_by_name(self):
        rule = Facade(STUCCO, corner=TRAV_CUT)
        for call, words in (
                (dict(faces={"east": Facade(STUCCO, corner=BRICK)}),
                 "would never be laid"),
                (dict(faces={"up": rule}), "faces names the walls"),
                (dict(openings=[("roof", Opening.rect(1, 2), 6)]),
                 "goes in the north"),
                (dict(openings=[(Opening.rect(1, 2), 6)]), "(face, Opening"),
                (dict(parapet=STONE_BRICKS), "pass deck="),
                (dict(deck=AIR), "lays nothing"),
                (dict(parapet_height=0, deck=GRANITE, parapet=STONE_BRICKS),
                 "at least one block high")):
            with self.subTest(words=words):
                with self.assertRaises(NBTError) as caught:
                    self.b.shell(4, 4, 14, 12, rule, floor_y=GROUND, top=68,
                                 **call)
                self.assertIn(words, str(caught.exception))
        with self.assertRaises(NBTError):
            self.b.shell(4, 4, 14, 12, STUCCO, floor_y=GROUND, top=68)
        self.b.shell(4, 4, 14, 12, rule, floor_y=GROUND, top=68,
                     faces={"east": Facade(STUCCO, corner=TRAV_CUT)})
        self.assertEqual(self.name(4, 66, 4), TRAV_CUT)

    def test_each_wall_writer_refuses_an_ungenerated_chunk_before_its_first_write(self):
        far = Wall((40, 5), (52, 5), floor_y=GROUND, top=68, outward="north")
        reads = []
        read = self.b._read
        self.b._read = lambda cell: reads.append(cell) or read(cell)
        for call in (
                lambda: self.b.shell(40, 4, 52, 12, Facade(STUCCO),
                                     floor_y=GROUND, top=68),
                lambda: self.b.wall(far, Facade(STUCCO)),
                lambda: self.b.cut_opening(far, Opening.rect(2, 2), 48,
                                           overwrite=True)):
            with self.assertRaises(Refused) as caught:
                call()
            self.assertIn("chunk (3, 0) is not generated",
                          caught.exception.faults)
        self.assertEqual(reads, [], "refused before a cell was read")
        self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(self.name(40, 64, 5), AIR)

    def test_identical_shells_encode_identically(self):
        digests = []
        for _ in range(2):
            save, directory = self.fresh()
            b = Builder(save)
            rule = Facade(Mix([(STUCCO, 3), ("minecraft:mud_bricks", 1)]),
                          pitch=4, phase=0, pier=TRAV_CUT, plinth=(2, BRICK),
                          bands={0: TIMBER},
                          window=Opening.arch(2, 2, springer=TRAV_STAIR,
                                              sill=2))
            b.shell(4, 4, 20, 14, rule, floor_y=GROUND, top=73, stories=(68,),
                    floor=PLANKS, deck=GRANITE, parapet=STONE_BRICKS,
                    openings=[("south", Opening.rect(1, 2), 10)])
            b.finish()
            save.save()
            digests.append(_region_digests(directory))
        self.assertEqual(digests[0], digests[1])


class WallWriterTests(WallsBase):
    def test_a_wall_is_one_run_of_its_facade(self):
        wall = Wall((4, 20), (16, 20), floor_y=GROUND, top=68,
                    outward="north", ends=True)
        rule = Facade(SKYGLASS, pitch=3, phase=0, pier=PANEL, corner=TRAV_CUT)
        arch = Opening.arch(3, 3, springer=SPRINGER)
        result = self.b.wall(wall, rule, openings=[(arch, 7)])
        self.assertEqual(result.walls, {"north": wall})
        self.assertIsNone(result.perimeter)
        self.assertEqual(result.openings, 9)
        plan = rule.plan(wall)
        plan.update(arch.cells(wall, 7))
        for cell, state in plan.items():
            self.assertEqual(self.save.get_block(*cell), state, cell)
        self.assertEqual(result.blocks, len(plan))
        for bad in ((arch,), (arch, 7, 0, 1), ("north", arch, 7)):
            with self.assertRaises(NBTError):
                self.b.wall(wall, rule, openings=[bad])

    def test_verify_holds_a_wall_to_its_facade(self):
        wall = Wall((4, 20), (16, 20), floor_y=GROUND, top=68,
                    outward="north", ends=True)
        rule = Facade(SKYGLASS, pitch=3, phase=0, pier=PANEL, corner=TRAV_CUT)
        self.b.wall(wall, rule)
        self.assertEqual(rule.verify(self.save, wall), [])
        self.save.set_block(7, 66, 20, "minecraft:dirt")
        self.save.set_block(8, 66, 20, AIR)
        wrong = (f"(7, 66, 20) holds minecraft:dirt, where the facade has "
                 f"{SKYGLASS}")
        self.assertEqual(rule.verify(self.save, wall),
                         [wrong, f"(8, 66, 20) holds {AIR}, where the facade "
                                 f"has {SKYGLASS}"])
        self.assertEqual(rule.verify(self.save, wall, missing_ok=True), [wrong],
                         "a block mined out is forgiven, a wrong one never")
        far = Wall((50, 5), (51, 5), floor_y=GROUND, top=65, outward="north")
        self.assertEqual(rule.verify(self.save, far),
                         [f"{(x, y, 5)}: nothing to read -- the chunk is not "
                          f"generated, or y is outside the world"
                          for x in (50, 51) for y in (64, 65)])

    def test_a_pane_wall_finish_has_joined_is_the_wall_that_was_laid(self):
        """``finish`` joins a pane wall's panes to each other; verify still
        finds the facade, and laying the wall again finds every cell
        already right and writes nothing."""
        wall = Wall((4, 30), (16, 30), floor_y=GROUND, top=66,
                    outward="south")
        rule = Facade(PANE)
        self.b.wall(wall, rule)
        self.b.finish()
        joined = self.save.get_block(9, 65, 30)
        self.assertEqual((joined.properties.get("east"),
                          joined.properties.get("west")), ("true", "true"))
        self.assertEqual(rule.verify(self.save, wall), [])
        before = self.b.blocks_set
        again = self.b.wall(wall, rule)
        self.assertEqual(again.already, again.blocks)
        self.assertEqual(self.b.blocks_set, before)
        self.assertEqual(self.save.get_block(9, 65, 30), joined)

    def test_a_wall_will_not_cut_a_torch_off_its_wall(self):
        """Laid again with a window where a torch hangs on its inside face,
        a wall is refused for the torch it would leave on nothing; one
        column over, the window goes in."""
        wall = Wall((4, 20), (16, 20), floor_y=GROUND, top=68, outward="north")
        rule = Facade(STONE_BRICKS)
        self.b.wall(wall, rule)
        self.save.set_block(8, 66, 21, "minecraft:wall_torch[facing=south]")
        window = Opening.rect(1, 1, sill=3)
        before = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.wall(wall, rule, openings=[(window, 8)])
        self.assertEqual([(f.cell, f.kind) for f in caught.exception.faults],
                         [((8, 66, 21), "no footing")])
        self.assertEqual(self.b.blocks_set, before)
        self.assertEqual(self.name(8, 66, 20), STONE_BRICKS)
        self.b.wall(wall, rule, openings=[(window, 10)])
        self.assertEqual(self.name(10, 66, 20), AIR)


class CutOpeningTests(WallsBase):
    def setUp(self):
        super().setUp()
        self.rule = Facade(STONE_BRICKS)
        self.shell = self.b.shell(4, 4, 14, 12, self.rule, floor_y=GROUND,
                                  top=70)
        self.north = self.shell.walls["north"]
        self.south = self.shell.walls["south"]

    def test_cut_opening_cuts_the_wall_its_facade_built(self):
        opening = Opening.rect(2, 2, head=TRAV_CUT)
        with self.assertRaises(Refused) as caught:
            self.b.cut_opening(self.north, opening, 8)
        self.assertIn(f"(8, 64, 4) holds {STONE_BRICKS}",
                      caught.exception.faults[0])
        self.assertIn("facade=", str(caught.exception))
        written = self.b.cut_opening(self.north, opening, 8, facade=self.rule)
        self.assertEqual(written, [(8, 64, 4), (8, 65, 4), (8, 66, 4),
                                   (9, 64, 4), (9, 65, 4), (9, 66, 4)])
        self.assertEqual([self.name(8, y, 4) for y in (64, 65, 66, 67)],
                         [AIR, AIR, TRAV_CUT, STONE_BRICKS])
        # Something that is not the wall's own stone is refused by name, and
        # overwrite cuts it.
        self.save.set_block(12, 64, 4, "minecraft:bookshelf")
        with self.assertRaises(Refused) as caught:
            self.b.cut_opening(self.north, Opening.rect(1, 2), 12,
                               facade=self.rule)
        self.assertEqual(len(caught.exception.faults), 1)
        self.assertIn("minecraft:bookshelf", caught.exception.faults[0])
        self.b.cut_opening(self.north, Opening.rect(1, 2), 12, overwrite=True)
        self.assertEqual(self.name(12, 64, 4), AIR)

    def test_cut_opening_will_not_take_an_iron_doors_button_wall(self):
        self.b.doorway(*self.south.door(9), door=IRON_DOOR)
        self.assertEqual(self.opens((9, 64, 12)), [True, True])
        before = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.cut_opening(self.south, Opening.rect(1, 1, sill=2), 8,
                               facade=self.rule)
        kinds = [fault.kind for fault in caught.exception.faults]
        self.assertIn("sealed door", kinds)
        self.assertIn((9, 64, 12), [fault.cell for fault in
                                    caught.exception.faults])
        self.assertEqual(self.b.blocks_set, before)
        self.assertEqual(self.name(8, 65, 12), STONE_BRICKS)

    def test_cut_opening_will_not_leave_a_torch_on_nothing(self):
        self.save.set_block(8, 66, 5, "minecraft:wall_torch[facing=south]")
        before = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.cut_opening(self.north, Opening.rect(1, 1, sill=3), 8,
                               facade=self.rule)
        self.assertEqual([(f.cell, f.kind) for f in caught.exception.faults],
                         [((8, 66, 5), "no footing")])
        self.assertEqual(self.b.blocks_set, before)
        self.assertEqual(self.name(8, 66, 4), STONE_BRICKS)

    def test_cut_opening_will_not_block_a_chest(self):
        """A blind panel -- a recess filled with a stone that conducts --
        over a cupboard chest built into a glass wall would leave the chest
        shut for good."""
        glass = Facade(SKYGLASS)
        wall = Wall((4, 30), (14, 30), floor_y=GROUND, top=68,
                    outward="south")
        self.b.wall(wall, glass)
        self.save.set_block(8, 64, 30, "minecraft:chest[facing=north]")
        panel = Opening.rect(1, 1, sill=2, fill="minecraft:chiseled_stone_bricks")
        with self.assertRaises(Refused) as caught:
            self.b.cut_opening(wall, panel, 8, facade=glass)
        self.assertEqual([(f.cell, f.kind) for f in caught.exception.faults],
                         [((8, 64, 30), "blocked chest")])
        self.assertEqual(self.name(8, 65, 30), SKYGLASS)
        self.b.cut_opening(wall, panel, 10, facade=glass)
        self.assertEqual(self.name(10, 65, 30), "minecraft:chiseled_stone_bricks")

    def test_water_in_the_hole_is_in_the_way_and_overwrite_clears_it(self):
        self.save.set_block(9, 65, 4, "minecraft:water")
        with self.assertRaises(Refused) as caught:
            self.b.cut_opening(self.north, Opening.rect(1, 2), 9,
                               facade=self.rule)
        self.assertEqual(len(caught.exception.faults), 1)
        self.assertIn("(9, 65, 4) holds minecraft:water",
                      caught.exception.faults[0])
        self.assertEqual(self.name(9, 64, 4), STONE_BRICKS, "nothing was cut")
        self.assertEqual(self.b.cut_opening(self.north, Opening.rect(1, 2), 9,
                                            overwrite=True),
                         [(9, 64, 4), (9, 65, 4)])
        self.assertEqual([self.name(9, y, 4) for y in (64, 65)], [AIR, AIR])

    def test_a_fault_already_standing_is_not_the_cuts(self):
        """A torch hanging on nothing and a chest under a block that shuts
        it stood before the cut, both within two blocks of it, which is as
        far as the audit looks; they are still there after, and the cut is
        not refused for them. Nor is the cut beside a chest the wall
        already shuts, which lets it open."""
        self.save.set_block(11, 66, 4, AIR)
        self.save.set_block(11, 66, 5, "minecraft:wall_torch[facing=south]")
        self.save.set_block(7, 64, 5, "minecraft:chest[facing=south]")
        self.save.set_block(7, 65, 5, STONE_BRICKS)
        standing = [((7, 64, 5), "blocked chest"), ((11, 66, 5), "no footing")]

        def faults():
            return sorted((f.cell, f.kind)
                          for f in unsupported(self.save, (4, 60, 2),
                                               (14, 72, 14)))

        self.assertEqual(faults(), standing)
        self.b.cut_opening(self.north, Opening.rect(1, 2), 9, facade=self.rule)
        self.assertEqual([self.name(9, y, 4) for y in (64, 65)], [AIR, AIR])
        self.assertEqual(faults(), standing, "as they stood")
        self.save.set_block(6, 64, 4, "minecraft:chest[facing=north]")
        self.b.cut_opening(self.north, Opening.rect(1, 1, sill=2), 6,
                           facade=self.rule)
        self.assertEqual(self.name(6, 65, 4), AIR)
        self.assertEqual(faults(), standing)


if __name__ == "__main__":
    unittest.main()
