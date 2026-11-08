"""Tests for the switchback stair, in heaton_voxel.craft.build.

A dogleg stair keeps its promises in places no picture shows them. Every
tread has to sit on wood the level below put there, or it is a beam. Every
tread needs three clear cells above it and not the two a standing body
occupies, because the game moves you sideways before it drops you --
clearance for two is a stair you can climb and cannot descend, and a
top-down render, a floor plan and a cutaway all draw it finished. And a
rule read out of a furnished building has to tell the treads from the
furniture, because a seat is a stair.

Each of those is a test here. The last two are checked by walking the
stair with :func:`~heaton_voxel.craft.survey.reachable` rather than by
looking at it, which is the only way either one shows up.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest

from heaton_voxel.craft.build import HEAD_ROOM, Builder, SwitchbackStair
from heaton_voxel.craft.errors import IllegalBlock
from heaton_voxel.craft.survey import StepSpace, reachable
from heaton_voxel.nbt import NBTError, TagInt
from heaton_voxel.world import MinecraftSave
from tests.test_world import make_save

CHUNKS = [(x, z) for x in range(-1, 2) for z in range(-1, 2)]
# The rectangle the treads sweep: seven rows of z, four columns of x.
CORNER1, CORNER2 = (2, 10), (5, 16)
GROUND = 63                     # solid, so the first tread has a footing
BASE = 64                       # the first tread


def tower(**changes):
    options = dict(axis="z", start="northeast", start_y=BASE, landing=3,
                   stair="minecraft:birch_stairs",
                   wood="minecraft:stripped_birch_wood")
    options.update(changes)
    return SwitchbackStair(CORNER1, CORNER2, **options)


class SwitchbackBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="switchback-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, CHUNKS)
        self.save = MinecraftSave.load(self.dir)
        self.b = Builder(self.save)

    def lay_ground(self, stair):
        for x, z in stair.columns:
            self.save.set_block(x, GROUND, z, "minecraft:stone")

    def lay_floor(self, stair, y, head):
        """A story slab at ``y`` keeping ``head`` levels of the well open.

        ``head=HEAD_ROOM`` is the rule; anything less is the mistake this
        whole class exists to make visible.
        """
        keep = set(stair.tread_cells(y)) | set(stair.landing_cells(y))
        keep |= set(stair.support_cells(y))
        for below in range(y - head, y):
            keep |= set(stair.tread_cells(below))
        for x, z in stair.columns:
            if (x, z) not in keep:
                self.save.set_block(x, y, z, "minecraft:stone")


class GeometryTests(SwitchbackBase):
    def test_the_period_is_two_runs_and_the_pitch_is_one(self):
        stair = tower()
        self.assertEqual(stair.run, 7)
        self.assertEqual(stair.width, 2)
        self.assertEqual(stair.period, 14)
        # A turn happens twice a cycle, so stories come every seven levels
        # and not every fourteen. This is the whole reason to keep a stair
        # as a rule: it decides where a floor can go.
        self.assertEqual(stair.pitch, 7)
        self.assertEqual(stair.landing_levels(BASE, BASE + 27),
                         (70, 77, 84, 91))

    def test_the_two_runs_walk_opposite_ways_in_their_own_halves(self):
        stair = tower()
        south = [stair.tread_cells(y) for y in range(BASE, BASE + 7)]
        north = [stair.tread_cells(y) for y in range(BASE + 7, BASE + 14)]
        self.assertEqual([stair.tread_facing(y) for y in range(BASE, BASE + 7)],
                         ["south"] * 7)
        self.assertEqual([stair.tread_facing(y)
                          for y in range(BASE + 7, BASE + 14)], ["north"] * 7)
        self.assertEqual([c[0][0] for c in south], [4] * 7, "the east half")
        self.assertEqual([c[0][0] for c in north], [2] * 7, "the west half")
        self.assertEqual([c[0][1] for c in south], list(range(10, 17)))
        self.assertEqual([c[0][1] for c in north], list(range(16, 9, -1)))
        # The turn keeps its row and steps across, which is the dogleg.
        self.assertEqual(south[-1][0][1], north[0][0][1])

    def test_every_tread_sits_on_the_wood_the_level_below_placed(self):
        stair = tower()
        for y in range(BASE, BASE + 28):
            self.assertEqual(set(stair.support_cells(y)),
                             set(stair.tread_cells(y + 1)),
                             f"y={y}: a tread with nothing under it is a beam")

    def test_a_landing_is_the_full_width_minus_its_own_treads(self):
        stair = tower()
        turn = 70                                   # phase 6, the south end
        cells = set(stair.landing_cells(turn))
        self.assertEqual(len(cells), 3 * 4 - stair.width)
        self.assertEqual({z for _, z in cells}, {16, 17, 18})
        self.assertFalse(cells & set(stair.tread_cells(turn)),
                         "the tread you arrive on is a tread, not landing")
        self.assertEqual(stair.landing_cells(71), (),
                         "a level that is not a turn has no landing")

    def test_a_pillar_named_in_skip_is_not_the_stairs_business(self):
        plain, dodged = tower(), tower(skip=[(5, 18)])
        self.assertIn((5, 18), plain.landing_cells(70))
        self.assertNotIn((5, 18), dodged.landing_cells(70))
        self.assertNotIn((5, 18), dodged.columns)


class ClaimedTests(SwitchbackBase):
    """The part no floor plan and no cutaway shows."""

    def test_three_things_claim_a_cell_and_the_third_is_invisible(self):
        stair = tower(span=(BASE, BASE + 40))
        y = 80
        claimed = stair.claimed(y)
        self.assertTrue(set(stair.tread_cells(y)) <= claimed, "the tread")
        self.assertTrue(set(stair.support_cells(y)) <= claimed, "its stringer")
        for below in range(1, HEAD_ROOM + 1):
            self.assertTrue(
                set(stair.tread_cells(y - below)) <= claimed,
                f"the head of whoever is on the tread {below} levels down")

    def test_the_head_room_is_three_treads_and_not_two(self):
        # Standing on a tread your head reaches two cells -- but the game
        # resolves horizontal movement before gravity, so for one tick as
        # you step off, your feet are still at the old height while your
        # box is over the next cell down the run.
        stair = tower(span=(BASE, BASE + 40))
        self.assertEqual(HEAD_ROOM, 3)
        third = set(stair.tread_cells(80 - 3))
        self.assertTrue(third <= stair.claimed(80))

    def test_the_bottom_of_a_stair_reserves_nothing_below_itself(self):
        bounded = tower(span=(BASE, BASE + 40))
        unbounded = tower()
        self.assertLess(len(bounded.claimed(BASE)), len(unbounded.claimed(BASE)),
                        "a known span must not reserve treads that are not there")

    def test_clear_is_claimed_inverted_and_sorted(self):
        stair = tower(span=(BASE, BASE + 40))
        free = stair.clear(80, stair.columns)
        self.assertEqual(free, sorted(free), "the palette order is the bytes")
        self.assertFalse(set(free) & stair.claimed(80))


class BuildTests(SwitchbackBase):
    def test_it_builds_what_the_rule_says(self):
        stair = tower()
        self.lay_ground(stair)
        result = self.b.switchback_stair(stair, BASE, BASE + 27)
        self.assertEqual(result.treads, 2 * 28)
        self.assertEqual(result.landings, 4)
        self.assertFalse(stair.verify(self.save, BASE, BASE + 26))
        self.assertEqual(stair.span, (BASE, BASE + 27))

    def test_a_cell_already_taken_stops_the_call(self):
        stair = tower()
        self.lay_ground(stair)
        self.save.set_block(4, 66, 12, "minecraft:stone")
        with self.assertRaises(NBTError) as caught:
            self.b.switchback_stair(stair, BASE, BASE + 27)
        self.assertIn("already taken", str(caught.exception))

    def test_a_ceiling_two_blocks_up_is_refused(self):
        stair = tower()
        self.lay_ground(stair)
        for x, z in stair.tread_cells(BASE + 3):
            self.save.set_block(x, BASE + 3 + 2, z, "minecraft:stone")
        with self.assertRaises(NBTError) as caught:
            self.b.switchback_stair(stair, BASE, BASE + 27)
        message = str(caught.exception)
        self.assertIn("headroom", message)
        self.assertIn("cannot descend", message)

    def test_a_torch_in_the_head_room_is_no_obstruction(self):
        # Head room is what a body can pass, as the walk judges it: a
        # stairwell lit by torches on its wall is not a low ceiling.
        stair = tower()
        self.lay_ground(stair)
        x, z = stair.walkway(BASE + 3)[0]
        self.save.set_block(x, BASE + 5, z, "minecraft:wall_torch[facing=east]")
        self.b.switchback_stair(stair, BASE, BASE + 27)
        self.assertEqual(self.save.get_block(x, BASE + 5, z).name,
                         "minecraft:wall_torch", "the torch is left alone")
        wrong, extras = stair.check(self.save, BASE, BASE + 26)
        self.assertEqual(wrong, [], "the stair is what the rule says")
        self.assertEqual(len(extras), 1, "and the torch is its one extra")

    def test_continuing_picks_up_past_the_top(self):
        stair = tower()
        self.lay_ground(stair)
        self.b.switchback_stair(stair, BASE, BASE + 13)
        read = SwitchbackStair.read(self.save, (2, 8), (5, 18))
        self.assertEqual(read.found, (BASE, BASE + 13))
        self.b.switchback_stair(read, None, BASE + 27)
        self.assertFalse(read.verify(self.save, BASE, BASE + 26))

    def test_continuing_backs_over_an_unfinished_tail(self):
        stair = tower()
        self.lay_ground(stair)
        self.b.switchback_stair(stair, BASE, BASE + 13)
        # Treads placed, their stringer not: work stopped mid-level.
        for x, z in stair.support_cells(BASE + 13):
            self.save.set_block(x, BASE + 13, z, "minecraft:air")
        read = SwitchbackStair.read(self.save, (2, 8), (5, 18))
        self.assertEqual(read.unfinished, (BASE + 13,))
        self.b.switchback_stair(read, None, BASE + 27)
        self.assertFalse(read.verify(self.save, BASE, BASE + 26),
                         "the join must not be left with a hole in it")

    def test_an_unfinished_tail_of_cave_air_is_missing_not_a_material(self):
        stair = tower()
        self.lay_ground(stair)
        self.b.switchback_stair(stair, BASE, BASE + 13)
        # Work stopped mid-level in an old cave: where the stringer is not
        # yet, the cave's own cave air is still there.
        for x, z in stair.support_cells(BASE + 13):
            self.save.set_block(x, BASE + 13, z, "minecraft:cave_air")
        read = SwitchbackStair.read(self.save, (2, 8), (5, 18))
        self.assertEqual(str(read.wood), "minecraft:stripped_birch_wood")
        self.assertEqual(read.unfinished, (BASE + 13,))
        self.b.switchback_stair(read, None, BASE + 27)
        self.assertFalse(read.verify(self.save, BASE, BASE + 26))

    def test_a_rule_read_off_an_old_chunk_is_held_to_todays_names(self):
        """A rule copies the states it reads. A stair saved before
        DataVersion 4541 holds ``minecraft:chain``, which the game renames
        only as it loads a chunk that old, so a continuation written with
        the old id is refused naming the replacement, before a block of it
        is written. Writing it anyway is a deliberate ``allow=``."""
        self.save.dimension().chunk(0, 0).data["DataVersion"] = TagInt(4000)
        stair = tower(wood="minecraft:chain")
        self.lay_ground(stair)
        for y in range(BASE, BASE + 14):
            for (x, z), state in stair.level(y).items():
                self.save.set_block(x, y, z, state)
        read = SwitchbackStair.read(self.save, (2, 8), (5, 18))
        self.assertEqual(read.found, (BASE, BASE + 13))
        self.assertEqual(read.wood.name, "minecraft:chain")

        b = Builder(self.save)
        with self.assertRaises(IllegalBlock) as caught:
            b.switchback_stair(read, None, BASE + 27)
        message = str(caught.exception)
        self.assertIn("switchback_stair", message)
        self.assertIn("renamed minecraft:iron_chain", message)
        self.assertEqual(caught.exception.suggestions,
                         ("minecraft:iron_chain",))
        self.assertEqual(b.blocks_set, 0)
        for y in range(BASE + 14, BASE + 28):
            for x, z in stair.columns:
                self.assertEqual(self.save.get_block(x, y, z).name,
                                 "minecraft:air", f"({x}, {y}, {z}) was written")

        Builder(self.save, allow=("minecraft:chain",)).switchback_stair(
            read, None, BASE + 27)
        self.assertFalse(read.verify(self.save, BASE, BASE + 26))


class ReadTests(SwitchbackBase):
    def build(self, first=BASE, last=BASE + 27, **changes):
        stair = tower(**changes)
        self.lay_ground(stair)
        self.b.switchback_stair(stair, first, last)
        return stair

    def test_a_stair_reads_back_as_the_rule_that_built_it(self):
        built = self.build()
        read = SwitchbackStair.read(self.save, (2, 8), (5, 18))
        self.assertEqual((read.axis, read.run, read.width, read.landing),
                         (built.axis, built.run, built.width, built.landing))
        self.assertEqual(read.found, (BASE, BASE + 27))
        for y in range(BASE, BASE + 28):
            self.assertEqual(read.level(y), built.level(y), f"y={y}")

    def test_every_start_corner_survives_the_round_trip(self):
        for corner in ("northeast", "northwest", "southeast", "southwest"):
            with self.subTest(corner=corner):
                self.setUp()
                built = self.build(start=corner)
                read = SwitchbackStair.read(self.save, (2, 8), (5, 18))
                for y in range(BASE, BASE + 28):
                    self.assertEqual(read.level(y), built.level(y),
                                     f"{corner} y={y}")

    def test_a_rule_that_cannot_reproduce_the_stair_is_refused(self):
        built = self.build()
        x, z = built.tread_cells(BASE + 4)[0]
        self.save.set_block(x, BASE + 4, z, "minecraft:stone")
        with self.assertRaises(NBTError) as caught:
            SwitchbackStair.read(self.save, (2, 8), (5, 18))
        self.assertIn("does not reproduce it", str(caught.exception))

    def test_cave_air_in_the_gaps_is_not_an_extra(self):
        """Cave air is air: a stairwell through an old cave holds nothing
        between its flights, and nothing is not furniture."""
        built = self.build()
        for y in range(BASE, BASE + 28):
            level = built.level(y)
            for x, z in built.columns:
                if (x, z) not in level:
                    self.save.set_block(x, y, z, "minecraft:cave_air")
        self.assertEqual(built.check(self.save, BASE, BASE + 27), ([], []))
        read = SwitchbackStair.read(self.save, (2, 8), (5, 18))
        self.assertEqual(read.found, (BASE, BASE + 27))
        self.assertEqual(read.extras, ())

    def test_furniture_in_the_stairwell_is_an_extra_and_not_a_fault(self):
        built = self.build()
        gap = next(c for c in built.columns if c not in built.level(BASE + 4))
        self.save.set_block(gap[0], BASE + 4, gap[1], "minecraft:bookshelf")
        read = SwitchbackStair.read(self.save, (2, 8), (5, 18))
        self.assertTrue(read.extras, "a block in a gap is worth reporting")
        self.assertIn("bookshelf", read.extras[0])
        self.assertFalse(read.check(self.save, BASE, BASE + 27)[0])

    def test_a_seat_is_a_stair_and_must_not_be_read_as_a_tread(self):
        built = self.build()
        # A chair on a landing: the same block as a tread, facing its own
        # way, in a row of its own. A greedy read counts it as the flight.
        seat = next(c for c in built.landing_cells(BASE + 6)
                    if c not in built.level(BASE + 7))
        self.save.set_block(seat[0], BASE + 7, seat[1],
                            "minecraft:birch_stairs[facing=east,half=bottom,"
                            "shape=straight,waterlogged=false]")
        read = SwitchbackStair.read(self.save, (2, 8), (5, 18))
        self.assertEqual(read.run, built.run)
        for y in range(BASE, BASE + 28):
            self.assertEqual(read.level(y), built.level(y), f"y={y}")
        self.assertTrue(any("birch_stairs" in line for line in read.extras))

    def test_one_straight_flight_is_not_a_switchback(self):
        self.build(first=BASE, last=BASE + 5)
        with self.assertRaises(NBTError) as caught:
            SwitchbackStair.read(self.save, (2, 8), (5, 18))
        self.assertIn("never turn", str(caught.exception))

    def test_a_gap_in_the_run_names_both_ends(self):
        built = self.build()
        for x, z in built.tread_cells(BASE + 10):
            self.save.set_block(x, BASE + 10, z, "minecraft:air")
        with self.assertRaises(NBTError) as caught:
            SwitchbackStair.read(self.save, (2, 8), (5, 18))
        message = str(caught.exception)
        self.assertIn("not one run", message)
        self.assertIn(f"y={BASE + 9}", message)

    def test_a_stringer_laid_every_which_way_is_still_one_material(self):
        built = self.build()
        # A hand-built flight lays its logs whichever way the builder was
        # facing, and that is not part of the stair.
        for y in (BASE + 2, BASE + 3, BASE + 9):
            for x, z in built.support_cells(y):
                self.save.set_block(x, y, z,
                                    "minecraft:stripped_birch_wood[axis=x]")
        read = SwitchbackStair.read(self.save, (2, 8), (5, 18))
        self.assertEqual(read.wood.name, "minecraft:stripped_birch_wood")
        self.assertEqual(read.wood.properties, {})
        self.assertFalse(read.check(self.save, BASE, BASE + 27)[0])

    def test_a_stringer_of_two_materials_is_refused(self):
        built = self.build()
        for x, z in built.support_cells(BASE + 2):
            self.save.set_block(x, BASE + 2, z, "minecraft:cobblestone")
        with self.assertRaises(NBTError) as caught:
            SwitchbackStair.read(self.save, (2, 8), (5, 18))
        self.assertIn("not one material", str(caught.exception))


class RefusalTests(unittest.TestCase):
    def test_an_odd_width_cannot_be_halved_into_two_runs(self):
        with self.assertRaises(NBTError) as caught:
            SwitchbackStair((2, 10), (6, 16))
        self.assertIn("halved", str(caught.exception))

    def test_a_corner_is_named_by_its_two_compass_words(self):
        with self.assertRaises(NBTError) as caught:
            SwitchbackStair(CORNER1, CORNER2, start="left")
        self.assertIn("corner", str(caught.exception))

    def test_the_treads_of_a_stair_are_stairs(self):
        with self.assertRaises(NBTError) as caught:
            SwitchbackStair(CORNER1, CORNER2, stair="minecraft:oak_planks")
        self.assertIn("not a stair", str(caught.exception))

    def test_a_stair_runs_along_x_or_z(self):
        with self.assertRaises(NBTError) as caught:
            SwitchbackStair(CORNER1, CORNER2, axis="y")
        self.assertIn("'x' or 'z'", str(caught.exception))


class WalkTests(SwitchbackBase):
    """The two questions a render cannot answer, asked of a real stair."""

    def climb(self, stair):
        space = StepSpace(self.save)
        (x0, z0), (x1, z1) = (2, 8), (5, 18)
        return space, reachable(space, (stair.tread_cells(BASE)[0][0], GROUND,
                                        stair.tread_cells(BASE)[0][1] - 1),
                                (x0, GROUND, z0), (x1, BASE + 30, z1))

    def test_a_finished_stair_is_a_round_trip(self):
        stair = tower()
        self.lay_ground(stair)
        self.b.switchback_stair(stair, BASE, BASE + 27)
        _, walk = self.climb(stair)
        top = stair.tread_cells(BASE + 27)[0]
        self.assertIn((top[0], BASE + 27, top[1]), walk.reached,
                      "the top tread must be reachable from the doorstep")
        self.assertFalse(walk.stranded, f"one-way cells: {sorted(walk.stranded)[:4]}")

    def test_a_stair_built_through_a_cave_is_a_round_trip(self):
        stair = tower()
        self.lay_ground(stair)
        self.save.fill((2, BASE, 8), (5, BASE + 30, 18), "minecraft:cave_air")
        result = self.b.switchback_stair(stair, BASE, BASE + 27)
        self.assertEqual(result.treads, 2 * 28)
        self.assertEqual(stair.check(self.save, BASE, BASE + 27), ([], []))
        _, walk = self.climb(stair)
        top = stair.tread_cells(BASE + 27)[0]
        self.assertIn((top[0], BASE + 27, top[1]), walk.reached)
        self.assertFalse(walk.stranded, f"one-way: {sorted(walk.stranded)[:4]}")

    def test_a_floor_with_two_levels_of_head_room_makes_it_one_way(self):
        # The failure that cost sixteen stories of chopping: the stair is
        # intact, every render draws it finished, and you cannot come down.
        stair = tower()
        self.lay_ground(stair)
        self.b.switchback_stair(stair, BASE, BASE + 27)
        self.lay_floor(stair, 84, head=HEAD_ROOM - 1)
        _, walk = self.climb(stair)
        self.assertTrue(walk.stranded,
                        "a forward-only check calls this tower finished")
        self.assertTrue(min(y for _, y, _ in walk.stranded) >= 84 - HEAD_ROOM)

    def test_the_same_floor_built_to_the_rule_stays_a_round_trip(self):
        stair = tower()
        self.lay_ground(stair)
        self.b.switchback_stair(stair, BASE, BASE + 27)
        self.lay_floor(stair, 84, head=HEAD_ROOM)
        _, walk = self.climb(stair)
        self.assertFalse(walk.stranded, f"one-way: {sorted(walk.stranded)[:4]}")
        top = stair.tread_cells(BASE + 27)[0]
        self.assertIn((top[0], BASE + 27, top[1]), walk.reached)


if __name__ == "__main__":
    unittest.main()
