"""Tests for the straight flight: StraightStair and Builder.flight.

The flights builds kept carving by hand failed in ways no picture shows:
the Roman insulae's stair cut nothing above it, the lab's cut four rows
where three were needed, and the castle stacked its runs in one pair of
columns so every flight sat in the head room of the one below. A flight is
therefore a rule -- treads, the wood under them, and the space they claim
-- and building one walks it both ways before a block is written.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest

from heaton_voxel.craft.build import HEAD_ROOM, Builder, FlightResult, StraightStair
from heaton_voxel.craft.errors import Refused
from heaton_voxel.craft.survey import reachable
from heaton_voxel.world import MinecraftSave
from tests.test_world import make_save

STONE = "minecraft:stone"


class FlightBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="flight-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, [(0, 0)])
        self.save = MinecraftSave.load(self.dir)
        self.b = Builder(self.save)
        # Two floors: the ground at 63 and a slab at 67, all the way across.
        self.save.fill((0, 60, 0), (15, 63, 15), STONE)
        self.save.fill((0, 67, 0), (15, 67, 15), STONE)
        self.stair = StraightStair((5, 64, 10), "north", 4, width=2)

    def name(self, x, y, z):
        return self.save.get_block(x, y, z).name


class GeometryTests(FlightBase):
    def test_the_flight_joins_the_floor_under_it_to_the_one_it_is_flush_with(self):
        self.assertEqual(self.stair.story_levels(), (63, 67))
        self.assertEqual(self.stair.entry(), ((5, 63, 11), (6, 63, 11)))
        self.assertEqual(self.stair.exit(), ((5, 67, 6), (6, 67, 6)))

    def test_the_slab_above_is_claimed_three_treads_deep(self):
        opening = self.stair.claimed(67) - set(self.stair.tread_cells(67))
        self.assertEqual(sorted({z for _, z in opening}), [8, 9, 10],
                         f"{HEAD_ROOM} rows of the floor are where heads go")


class BuildTests(FlightBase):
    def test_a_flight_through_the_slab_walks_both_ways(self):
        result = self.b.flight(self.stair, floor_y=67)
        self.assertIsInstance(result, FlightResult)
        self.assertEqual((result.treads, result.wood), (8, 6))
        for z in (8, 9, 10):
            self.assertEqual(self.name(5, 67, z), "minecraft:air", f"z={z}")
        self.assertEqual(self.name(5, 67, 7), "minecraft:oak_stairs")
        walk = reachable(self.save, (5, 63, 11), (0, 60, 0), (15, 72, 15))
        self.assertIn((5, 67, 6), walk.reached)
        self.assertFalse(walk.stranded)

    def test_without_the_slab_cut_the_head_room_is_refused(self):
        with self.assertRaises(Refused) as caught:
            self.b.flight(self.stair)
        self.assertTrue(all("clear blocks above" in f or "holds" in f
                            for f in caught.exception.faults))
        self.assertEqual(self.name(5, 64, 10), "minecraft:air",
                         "a refusal writes nothing")

    def test_two_blocks_of_head_room_is_refused(self):
        # A beam over the foot of the flight, at the third block up.
        self.save.set_block(5, 67, 11, STONE)
        self.save.fill((5, 66, 10), (6, 66, 10), STONE)
        with self.assertRaises(Refused):
            self.b.flight(self.stair, floor_y=67)

    def test_a_torch_on_the_wall_is_no_obstruction(self):
        # Head room is what a body can pass: the Roman insulae light their
        # stairwells with wall torches, and the walk steps through them.
        self.save.fill((4, 64, 0), (4, 72, 15), STONE)
        self.save.set_block(5, 66, 10, "minecraft:wall_torch[facing=east]")
        self.b.flight(self.stair, floor_y=67)
        self.assertEqual(self.name(5, 66, 10), "minecraft:wall_torch")

    def test_the_sky_over_the_top_of_the_world_is_head_room(self):
        # A flight up to the build limit has nothing above its last treads
        # to read. Those treads were just read, so that is open sky, not a
        # ceiling and not a missing chunk.
        self.save.fill((0, 315, 0), (15, 315, 15), STONE)
        self.save.fill((0, 319, 0), (15, 319, 15), STONE)
        stair = StraightStair((5, 316, 10), "north", 4, width=2)
        self.assertEqual(stair.story_levels(), (315, 319))
        result = self.b.flight(stair, floor_y=319)
        self.assertEqual((result.treads, result.wood), (8, 6))
        self.assertEqual(self.name(5, 319, 7), "minecraft:oak_stairs")

    def test_a_flight_stacked_in_another_ones_head_room_is_refused(self):
        self.b.flight(self.stair, floor_y=67)
        # The insula mistake: the next story's flight laid back over the
        # first one, reversed, in the same columns.
        self.save.fill((0, 71, 0), (15, 71, 15), STONE)
        back = StraightStair((5, 68, 7), "south", 4, width=2)
        with self.assertRaises(Refused) as caught:
            self.b.flight(back, floor_y=71, walk=False)
        self.assertIn("claimed by an earlier one", str(caught.exception))

    def test_a_railing_goes_round_the_opening_but_not_where_you_arrive(self):
        result = self.b.flight(self.stair, floor_y=67, rail="minecraft:oak_fence")
        self.assertEqual(result.rails, 8)
        self.assertEqual(self.name(4, 68, 9), "minecraft:oak_fence")
        self.assertEqual(self.name(5, 68, 6), "minecraft:air", "the way off is open")

    def test_a_flight_to_nowhere_is_refused(self):
        self.save.fill((0, 67, 0), (15, 67, 6), "minecraft:air")
        with self.assertRaises(Refused) as caught:
            self.b.flight(self.stair, floor_y=67)
        self.assertIn("no floor", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
