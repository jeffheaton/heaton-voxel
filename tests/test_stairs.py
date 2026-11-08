"""Tests for the winding stair and its shaft, in heaton_voxel.craft.build.

The interesting properties of a spiral stair are the ones you only notice
on foot: that every step has headroom, that every step is supported by the
level below, and that a run built in two sittings joins without a seam.
Each of those is a test here rather than something a render is trusted to
show, because none of them are visible from above -- which is exactly how
the tower this came from went 245 levels unbuilt with nobody able to say
where its stair was supposed to stop.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest

from heaton_voxel.blocks import AIR_BLOCKS
from heaton_voxel.craft.build import Builder, SpiralStair
from heaton_voxel.craft.errors import IllegalBlock, Refused
from heaton_voxel.craft.light import simulate_block_light
from heaton_voxel.craft.survey import reachable
from heaton_voxel.nbt import NBTError, TagInt
from heaton_voxel.world import MinecraftSave
from tests.test_world import make_save

CHUNKS = [(x, z) for x in range(-1, 2) for z in range(-1, 2)]
CENTER = (8, 8)


class StairBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="stair-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, CHUNKS)
        self.save = MinecraftSave.load(self.dir)
        self.b = Builder(self.save)

    def climb(self, spiral, y0, y1):
        """Walk the stair and complain about anything that stops you.

        The three ways a spiral stair fails a walker: a step with no
        footing under it, a step you cannot stand up in, and a step that is
        not where the next one expects it. Air is any of the game's airs:
        a shaft through an old cave is as open as one a pick dug.
        """
        faults = []
        for y in range(y0, y1 + 1):
            step = spiral.cell(y)
            under = self.save.get_block(step[0], y - 1, step[1])
            if under is None or under.name in AIR_BLOCKS:
                faults.append(f"y={y}: step at {step} has nothing under it")
            for column in spiral.walkway(y):
                for above in (y + 1, y + 2):
                    block = self.save.get_block(column[0], above, column[1])
                    if block is not None and block.name not in AIR_BLOCKS:
                        faults.append(f"y={y}: {column} blocked at {above}")
            here = self.save.get_block(step[0], y, step[1])
            self.assertIsNotNone(here)
            self.assertTrue(here.name.endswith("_stairs"),
                            f"y={y}: {here} is not a step")
        return faults


class GeometryTests(StairBase):
    def test_a_level_is_a_quadrant_with_one_step_in_it(self):
        spiral = SpiralStair(CENTER)
        level = spiral.level(0)
        self.assertEqual(len(level), 4, "newel, step and two treads")
        self.assertIn(CENTER, level, "the newel is in every level")
        steps = [c for c, s in level.items() if s.name.endswith("_stairs")]
        self.assertEqual(len(steps), 1)
        xs = {x for x, _ in level}
        zs = {z for _, z in level}
        self.assertEqual((len(xs), len(zs)), (2, 2), "a 2x2 quadrant")

    def test_the_quadrant_turns_a_quarter_per_level_and_repeats_at_four(self):
        spiral = SpiralStair(CENTER)
        cells = [spiral.cell(y) for y in range(8)]
        self.assertEqual(len(set(cells[:4])), 4, "four distinct cells a turn")
        self.assertEqual(cells[:4], cells[4:], "and then it repeats")

    def test_the_two_hands_are_mirror_images(self):
        right = SpiralStair(CENTER, turn="clockwise")
        left = SpiralStair(CENTER, turn="counterclockwise")
        for y in range(8):
            rx, rz = right.cell(y)
            lx, lz = left.cell(y)
            self.assertEqual((rx, rz), (lx, 2 * CENTER[1] - lz),
                             f"y={y}: the hands mirror across the x axis")

    def test_a_step_faces_the_cell_it_climbs_to(self):
        for turn in ("clockwise", "counterclockwise"):
            spiral = SpiralStair(CENTER, turn=turn)
            for y in range(4):
                nxt = spiral.cell(y + 1)
                offset = (nxt[0] - CENTER[0], nxt[1] - CENTER[1])
                compass = {(1, 0): "east", (0, 1): "south",
                           (-1, 0): "west", (0, -1): "north"}[offset]
                self.assertEqual(spiral.facing(y), compass,
                                 f"{turn} y={y}")

    def test_the_phase_is_where_you_put_it(self):
        spiral = SpiralStair(CENTER, start="west", start_y=73)
        self.assertEqual(spiral.cell(73), (CENTER[0] - 1, CENTER[1]))
        self.assertEqual(spiral.cell(77), (CENTER[0] - 1, CENTER[1]))

    def test_the_walkway_is_the_step_and_its_diagonal(self):
        spiral = SpiralStair(CENTER)
        for y in range(4):
            walkway = spiral.walkway(y)
            self.assertEqual(len(set(walkway)), 2)
            self.assertEqual(walkway[0], spiral.cell(y))
            self.assertNotIn(CENTER, walkway, "you never stand on the newel")

    def test_a_spiral_claims_its_whole_shaft(self):
        # Every gap of the 3x3 is the head room of some tread within
        # HEAD_ROOM below it, so nothing but the stair belongs anywhere in
        # the shaft -- whichever way it turns and wherever it starts.
        for turn in ("clockwise", "counterclockwise"):
            for start in ("east", "south", "west", "north"):
                spiral = SpiralStair(CENTER, turn=turn, start=start)
                for y in range(70, 74):
                    with self.subTest(turn=turn, start=start, y=y):
                        self.assertEqual(spiral.claimed(y),
                                         set(spiral.columns))
                        self.assertEqual(spiral.clear(y, spiral.columns), [])

    def test_stair_properties_are_filled_but_not_overridden(self):
        plain = SpiralStair(CENTER)
        self.assertEqual(plain.level(0)[plain.cell(0)].properties["shape"],
                         "straight")
        wet = SpiralStair(CENTER, stair="minecraft:oak_stairs[waterlogged=true]")
        self.assertEqual(wet.level(0)[wet.cell(0)].properties["waterlogged"],
                         "true")

    def test_a_block_that_is_not_a_stair_is_refused(self):
        with self.assertRaises(NBTError) as caught:
            SpiralStair(CENTER, stair="minecraft:oak_planks")
        self.assertIn("not a stair", str(caught.exception))

    def test_a_nonsense_hand_or_cell_is_refused(self):
        with self.assertRaises(NBTError):
            SpiralStair(CENTER, turn="widdershins")
        with self.assertRaises(NBTError):
            SpiralStair(CENTER, start="up")


class BuildTests(StairBase):
    def test_a_stair_you_can_actually_climb(self):
        spiral = SpiralStair(CENTER)
        result = self.b.spiral_stair(spiral, 64, 96)
        self.assertEqual(result.steps, 33)
        self.assertEqual(result.turns, 8.25)
        # The bottom step has nothing below it by definition; start at 65.
        self.assertEqual(self.climb(spiral, 65, 96), [])

    def walk(self):
        """Walk the stairwell from the shaft floor at 63, both ways."""
        return reachable(self.save, (CENTER[0] - 1, 63, CENTER[1] - 1),
                         (CENTER[0] - 2, 62, CENTER[1] - 2),
                         (CENTER[0] + 4, 101, CENTER[1] + 2))

    def test_a_block_three_above_the_landing_tread_is_refused(self):
        # The diagonal tread is where you land coming down, and for that
        # tick your head is in the third cell above it. A stone there is a
        # stair you can climb and cannot descend.
        self.save.fill((0, 60, 0), (15, 110, 15), "minecraft:stone")
        self.b.shaft(CENTER, 64, 99, wall="minecraft:stone_bricks")
        spiral = SpiralStair(CENTER)
        x, z = spiral.walkway(80)[1]
        self.save.set_block(x, 83, z, "minecraft:stone")
        with self.assertRaises(Refused) as caught:
            self.b.spiral_stair(spiral, 64, 96)
        self.assertIn("headroom", str(caught.exception))
        self.assertEqual(len(caught.exception.faults), 1)
        self.assertIn(str((x, 83, z)), caught.exception.faults[0])
        self.assertEqual(self.save.get_block(CENTER[0], 64, CENTER[1]).name,
                         "minecraft:air", "nothing was written")

        # And the refusal is right: built anyway, everything above the
        # stone is somewhere you can get to and not back from.
        self.b.spiral_stair(spiral, 64, 96, headroom=False)
        walk = self.walk()
        self.assertTrue(walk.stranded)
        self.assertGreaterEqual(min(y for _, y, _ in walk.stranded), 80)

    def stairwell(self, top):
        """A shaft carved to ``top`` over a stair to 96, with a door out of
        it at 97 beside the top tread. Returns the spiral and the door's
        far end, the block you stand on outside."""
        self.setUp()
        self.save.fill((0, 60, 0), (15, 110, 15), "minecraft:stone")
        self.b.shaft(CENTER, 64, top, wall="minecraft:stone_bricks")
        spiral = SpiralStair(CENTER)
        x, z = spiral.walkway(96)[1]
        out = 1 if x > CENTER[0] else -1
        for step in (2, 3):              # through the wall, and one beyond
            column = CENTER[0] + out * step
            self.save.set_block(column, 97, z, "minecraft:stone")
            self.save.fill((column, 98, z), (column, 99, z), "minecraft:air")
        return spiral, (CENTER[0] + out * 3, 97, z)

    def test_a_stair_whose_top_would_be_one_way_is_refused(self):
        # Two levels past the top step is enough to stand on it, and not to
        # come back down onto it from the door: the shaft's lid is the
        # third cell over the top tread.
        spiral, door = self.stairwell(98)
        with self.assertRaises(Refused) as caught:
            self.b.spiral_stair(spiral, 64, 96)
        self.assertIn("headroom", str(caught.exception))
        self.b.spiral_stair(spiral, 64, 96, headroom=False)
        walk = self.walk()
        self.assertIn(door, walk.reached)
        self.assertIn(door, walk.stranded, "two past is a one-way door")

        # Carved three past, the same stairwell builds and walks both ways.
        spiral, door = self.stairwell(99)
        self.b.spiral_stair(spiral, 64, 96)
        walk = self.walk()
        self.assertIn(door, walk.reached)
        self.assertFalse(walk.stranded, f"one-way: {sorted(walk.stranded)[:4]}")

    def test_only_the_stairs_own_tread_is_excused_over_a_step(self):
        # The third cell over a step is where the stair turns three levels
        # up, so the rule's own tread there is no ceiling. Over the top step
        # that level is not part of the call, and the excuse is for that
        # tread alone: a stone in its place is a lid.
        self.save.fill((0, 60, 0), (15, 110, 15), "minecraft:stone")
        self.b.shaft(CENTER, 64, 99, wall="minecraft:stone_bricks")
        spiral = SpiralStair(CENTER)
        x, z = spiral.cell(96)
        self.assertEqual(spiral.level(99)[(x, z)], spiral.tread)
        self.save.set_block(x, 99, z, "minecraft:stone")
        with self.assertRaises(Refused) as caught:
            self.b.spiral_stair(spiral, 64, 96)
        self.assertIn("headroom", str(caught.exception))
        self.assertEqual(len(caught.exception.faults), 1)
        self.assertIn(str((x, 99, z)), caught.exception.faults[0])
        self.assertEqual(self.save.get_block(CENTER[0], 64, CENTER[1]).name,
                         "minecraft:air", "nothing was written")

        # The tread the rule puts there is the stair's own, and passes.
        self.save.set_block(x, 99, z, spiral.tread)
        self.b.spiral_stair(spiral, 64, 96)
        self.assertEqual(spiral.verify(self.save, 64, 96), [])

    def test_a_torch_in_the_gaps_is_no_head_room(self):
        # Unlike a switchback's stairwell wall, a spiral's gaps are part of
        # its pattern: a torch a body would pass is still a block where the
        # rule wants nothing, and the stair could not be read back over it.
        self.save.fill((0, 60, 0), (15, 110, 15), "minecraft:stone")
        self.b.shaft(CENTER, 64, 99, wall="minecraft:stone_bricks")
        spiral = SpiralStair(CENTER)
        x, z = spiral.walkway(80)[1]
        facing = "west" if x > CENTER[0] else "east"   # hung on the wall
        self.save.set_block(x, 83, z, f"minecraft:wall_torch[facing={facing}]")
        with self.assertRaises(Refused) as caught:
            self.b.spiral_stair(spiral, 64, 96)
        self.assertIn("headroom", str(caught.exception))
        self.assertEqual(len(caught.exception.faults), 1)
        self.assertIn(str((x, 83, z)), caught.exception.faults[0])

        # Built anyway, the next read refuses the stair over it.
        self.b.spiral_stair(spiral, 64, 96, headroom=False)
        with self.assertRaises(NBTError) as caught:
            SpiralStair.read(self.save, CENTER)
        self.assertIn("expected air between the steps", str(caught.exception))

    def test_every_step_is_the_footing_of_the_next(self):
        spiral = SpiralStair(CENTER)
        self.b.spiral_stair(spiral, 64, 80)
        for y in range(65, 81):
            below = self.save.get_block(spiral.cell(y)[0], y - 1,
                                        spiral.cell(y)[1])
            self.assertEqual(below.name, "minecraft:oak_planks",
                             f"y={y} stands on the tread below it")

    def test_a_run_built_in_two_sittings_has_no_seam(self):
        spiral = SpiralStair(CENTER)
        self.b.spiral_stair(spiral, 64, 80)
        self.b.spiral_stair(spiral, 81, 96)
        self.assertEqual(spiral.verify(self.save, 64, 96), [],
                         "the join is invisible to the rule")
        self.assertEqual(self.climb(spiral, 65, 96), [])

    def test_reading_a_stair_back_recovers_the_rule_that_built_it(self):
        for turn in ("clockwise", "counterclockwise"):
            with self.subTest(turn=turn):
                self.setUp()
                built = SpiralStair(CENTER, turn=turn, start="north",
                                    start_y=70,
                                    stair="minecraft:spruce_stairs",
                                    tread="minecraft:spruce_planks",
                                    newel="minecraft:stripped_spruce_log")
                self.b.spiral_stair(built, 64, 96)
                read = SpiralStair.read(self.save, CENTER)
                self.assertEqual(read.turn, turn)
                self.assertEqual(read.found, (64, 96))
                self.assertEqual(read.unfinished, ())
                self.assertEqual(str(read.tread), "minecraft:spruce_planks")
                self.assertEqual(str(read.newel),
                                 "minecraft:stripped_spruce_log")
                for y in range(64, 97):
                    self.assertEqual(read.cell(y), built.cell(y))
                    self.assertEqual(read.facing(y), built.facing(y))

    def test_continuing_finishes_the_level_the_last_session_stopped_inside(self):
        spiral = SpiralStair(CENTER)
        self.b.spiral_stair(spiral, 64, 80)
        # Leave the top level the way a hand-built run is always left:
        # the step placed, its treads not yet.
        for column, state in spiral.level(80).items():
            if not state.name.endswith("_stairs") and column != CENTER:
                self.save.set_block(column[0], 80, column[1], "minecraft:air")

        read = SpiralStair.read(self.save, CENTER)
        self.assertEqual(read.found, (64, 80))
        self.assertEqual(read.unfinished, (80,), "the top level is unfinished")

        result = Builder(self.save).spiral_stair(read, None, 96)
        self.assertEqual(result.bottom, 80,
                         "it picked up inside the unfinished level, not above it")
        self.assertEqual(spiral.verify(self.save, 64, 96), [])
        self.assertEqual(self.climb(spiral, 65, 96), [])

    def test_continuing_downward_from_a_read_stair(self):
        spiral = SpiralStair(CENTER)
        self.b.spiral_stair(spiral, 64, 96)
        read = SpiralStair.read(self.save, CENTER)
        result = Builder(self.save).spiral_stair(read, None, 40)
        self.assertEqual((result.bottom, result.top), (40, 63))
        self.assertEqual(spiral.verify(self.save, 40, 96), [])

    def test_newel_false_leaves_an_existing_post_alone(self):
        self.save.fill((CENTER[0], 64, CENTER[1]), (CENTER[0], 96, CENTER[1]),
                       "minecraft:stone_bricks")
        spiral = SpiralStair(CENTER)
        result = self.b.spiral_stair(spiral, 64, 96, newel=False)
        self.assertEqual(result.newel, 0)
        self.assertEqual(self.save.get_block(CENTER[0], 70, CENTER[1]).name,
                         "minecraft:stone_bricks")

    def test_a_bare_center_is_taken_as_a_default_oak_stair(self):
        result = self.b.spiral_stair(CENTER, 64, 68)
        self.assertEqual(result.steps, 5)
        step = SpiralStair(CENTER).cell(64)
        self.assertEqual(self.save.get_block(step[0], 64, step[1]).name,
                         "minecraft:oak_stairs")

    def test_a_spiral_stair_climbs_through_cave_air(self):
        """A shaft through a cave cut before 1.18 is full of cave air, and
        cave air is air: the stair builds into it, keeps its head room
        through it, and leaves it between the steps as nothing rather than
        as furniture."""
        self.save.fill((CENTER[0] - 1, 64, CENTER[1] - 1),
                       (CENTER[0] + 1, 99, CENTER[1] + 1), "minecraft:cave_air")
        spiral = SpiralStair(CENTER)
        result = self.b.spiral_stair(spiral, 64, 96)
        self.assertEqual(result.steps, 33)
        self.assertEqual(spiral.verify(self.save, 64, 96), [])
        self.assertEqual(self.climb(spiral, 65, 96), [])

    def test_a_stair_left_unfinished_in_a_cave_reads_and_continues(self):
        """The reading side takes cave air for nothing too: in the gaps,
        and in the treads of the level the last session stopped inside,
        which must read as missing and not as a second tread material."""
        self.save.fill((CENTER[0] - 1, 64, CENTER[1] - 1),
                       (CENTER[0] + 1, 99, CENTER[1] + 1), "minecraft:cave_air")
        spiral = SpiralStair(CENTER)
        for y in range(64, 81):
            for column, state in spiral.level(y).items():
                if (y == 80 and not state.name.endswith("_stairs")
                        and column != CENTER):
                    continue        # the step placed, its treads not yet
                self.save.set_block(column[0], y, column[1], state)

        read = SpiralStair.read(self.save, CENTER)
        self.assertEqual(read.found, (64, 80))
        self.assertEqual(read.unfinished, (80,))
        self.assertEqual(str(read.tread), "minecraft:oak_planks")
        self.assertEqual(read.extras, ())

        result = Builder(self.save).spiral_stair(read, None, 96)
        self.assertEqual(result.bottom, 80)
        self.assertEqual(spiral.verify(self.save, 64, 96), [])
        self.assertEqual(self.climb(spiral, 65, 96), [])


class RefusalTests(StairBase):
    def test_something_in_the_shaft_stops_the_call_before_it_writes(self):
        spiral = SpiralStair(CENTER)
        step = spiral.cell(70)
        self.save.set_block(step[0], 70, step[1], "minecraft:stone")
        with self.assertRaises(NBTError) as caught:
            self.b.spiral_stair(spiral, 64, 96)
        self.assertIn("already taken", str(caught.exception))
        self.assertEqual(self.save.get_block(CENTER[0], 64, CENTER[1]).name,
                         "minecraft:air",
                         "not one block was written before the refusal")

    def test_overwrite_cuts_through_deliberately(self):
        spiral = SpiralStair(CENTER)
        step = spiral.cell(70)
        self.save.set_block(step[0], 70, step[1], "minecraft:stone")
        self.b.spiral_stair(spiral, 64, 96, overwrite=True)
        self.assertEqual(spiral.verify(self.save, 64, 96), [])

    def test_a_ceiling_you_would_hit_is_refused(self):
        spiral = SpiralStair(CENTER)
        step = spiral.cell(80)
        self.save.set_block(step[0], 82, step[1], "minecraft:stone")
        with self.assertRaises(NBTError) as caught:
            self.b.spiral_stair(spiral, 64, 96)
        self.assertIn("headroom", str(caught.exception))

    def test_headroom_false_builds_it_anyway(self):
        spiral = SpiralStair(CENTER)
        step = spiral.cell(80)
        self.save.set_block(step[0], 82, step[1], "minecraft:stone")
        self.b.spiral_stair(spiral, 64, 96, headroom=False)
        self.assertTrue(self.save.get_block(*(step[0], 80, step[1]))
                        .name.endswith("_stairs"))

    def test_reading_where_there_is_no_stair(self):
        with self.assertRaises(NBTError) as caught:
            SpiralStair.read(self.save, CENTER)
        self.assertIn("nothing to continue", str(caught.exception))

    def test_reading_a_run_with_a_gap_names_the_gap(self):
        spiral = SpiralStair(CENTER)
        self.b.spiral_stair(spiral, 64, 70)
        self.b.spiral_stair(spiral, 80, 90)
        with self.assertRaises(NBTError) as caught:
            SpiralStair.read(self.save, CENTER)
        message = str(caught.exception)
        self.assertIn("not one run", message)
        self.assertIn("y=70", message)
        self.assertIn("y=80", message)

    def test_a_single_step_does_not_say_which_way_it_turns(self):
        spiral = SpiralStair(CENTER)
        self.b.spiral_stair(spiral, 64, 64)
        with self.assertRaises(NBTError) as caught:
            SpiralStair.read(self.save, CENTER)
        self.assertIn("which way", str(caught.exception))

    def test_steps_that_do_not_turn_a_quarter_per_level_are_not_this_pattern(self):
        # A straight flight: same cell every level, no turn at all.
        for y in range(64, 70):
            self.save.set_block(CENTER[0] + 1, y, CENTER[1],
                                "minecraft:oak_stairs[facing=south,half=bottom,"
                                "shape=straight,waterlogged=false]")
        with self.assertRaises(NBTError) as caught:
            SpiralStair.read(self.save, CENTER)
        self.assertIn("quarter", str(caught.exception))

    def test_a_stray_block_between_the_steps_is_refused(self):
        spiral = SpiralStair(CENTER)
        self.b.spiral_stair(spiral, 64, 96)
        empty = [c for c in spiral.interior if c not in spiral.level(70)]
        self.save.set_block(empty[0][0], 70, empty[0][1], "minecraft:stone")
        with self.assertRaises(NBTError) as caught:
            SpiralStair.read(self.save, CENTER)
        message = str(caught.exception)
        self.assertIn("does not reproduce it", message)
        self.assertIn("expected air between the steps", message)

    def test_a_tread_swapped_for_another_material_is_refused(self):
        spiral = SpiralStair(CENTER)
        self.b.spiral_stair(spiral, 64, 96)
        # One tread swapped for something else: the geometry still reads,
        # the material no longer does.
        column = [c for c in spiral.level(70)
                  if c != CENTER and c != spiral.cell(70)][0]
        self.save.set_block(column[0], 70, column[1], "minecraft:stone")
        with self.assertRaises(NBTError) as caught:
            SpiralStair.read(self.save, CENTER)
        self.assertIn("not one material", str(caught.exception))

    def test_mixed_step_materials_are_refused(self):
        spiral = SpiralStair(CENTER)
        self.b.spiral_stair(spiral, 64, 96)
        step = spiral.cell(70)
        self.save.set_block(step[0], 70, step[1],
                            "minecraft:spruce_stairs[facing=%s,half=bottom,"
                            "shape=straight,waterlogged=false]"
                            % spiral.facing(70))
        with self.assertRaises(NBTError) as caught:
            SpiralStair.read(self.save, CENTER)
        self.assertIn("not all the same block", str(caught.exception))

    def test_continuing_a_stair_that_was_never_read(self):
        with self.assertRaises(NBTError) as caught:
            self.b.spiral_stair(SpiralStair(CENTER), None, 96)
        self.assertIn("nothing", str(caught.exception))

    def test_continuing_into_the_middle_of_the_run_is_ambiguous(self):
        spiral = SpiralStair(CENTER)
        self.b.spiral_stair(spiral, 64, 96)
        read = SpiralStair.read(self.save, CENTER)
        with self.assertRaises(NBTError) as caught:
            self.b.spiral_stair(read, None, 80)
        self.assertIn("inside the run", str(caught.exception))

    def test_a_rule_read_off_an_old_chunk_is_held_to_todays_names(self):
        """A stair saved before DataVersion 4541 holds ``minecraft:chain``,
        and the game renames it only as it loads a chunk that old. A rule
        read off the stair copies the old id, and the continuation is
        refused naming the replacement, before a block of it is written --
        an old name anywhere the game does not rename it is air. Writing
        it anyway is a deliberate ``allow=``."""
        self.save.dimension().chunk(0, 0).data["DataVersion"] = TagInt(4000)
        built = SpiralStair(CENTER, newel="minecraft:chain[axis=y]")
        for y in range(64, 81):
            for (x, z), state in built.level(y).items():
                self.save.set_block(x, y, z, state)
        read = SpiralStair.read(self.save, CENTER)
        self.assertEqual(str(read.newel), "minecraft:chain[axis=y]")

        b = Builder(self.save)
        with self.assertRaises(IllegalBlock) as caught:
            b.spiral_stair(read, None, 96)
        message = str(caught.exception)
        self.assertIn("spiral_stair", message)
        self.assertIn("renamed minecraft:iron_chain", message)
        self.assertEqual(caught.exception.suggestions, ("minecraft:iron_chain",))
        self.assertEqual(b.blocks_set, 0)
        for y in range(81, 97):
            for x, z in built.columns:
                self.assertEqual(self.save.get_block(x, y, z).name,
                                 "minecraft:air", f"({x}, {y}, {z}) was written")

        Builder(self.save, allow=("minecraft:chain",)).spiral_stair(
            read, None, 96)
        self.assertEqual(built.verify(self.save, 64, 96), [])


class ShaftTests(StairBase):
    def test_a_shaft_is_hollow_walled_and_lit(self):
        self.save.fill((0, 60, 0), (15, 100, 15), "minecraft:stone")
        result = self.b.shaft(CENTER, 64, 96, wall="minecraft:stone_bricks",
                              light="minecraft:glowstone", light_level=8)
        self.assertEqual(result.cleared, 9 * 33)
        self.assertGreater(result.lights, 0)
        self.assertEqual(self.save.get_block(CENTER[0], 70, CENTER[1]).name,
                         "minecraft:air")
        self.assertEqual(self.save.get_block(CENTER[0] + 2, 70, CENTER[1] + 2)
                         .name, "minecraft:stone_bricks")

    def darkest(self, y0, y1, radius=1):
        """The worst-lit cell inside the shaft at each level, by the game's
        own flood over the world as it now stands, counting only the light
        that lasts; a cell with a block standing in it holds no light and
        is not counted."""
        cx, cz = CENTER
        field = simulate_block_light(self.save, (cx - radius, y0, cz - radius),
                                     (cx + radius, y1, cz + radius),
                                     durable=True)
        return {y: min(field.level(x, y, z)
                       for z in range(cz - radius, cz + radius + 1)
                       for x in range(cx - radius, cx + radius + 1)
                       if field.dampening(x, y, z) < 15)
                for y in range(y0, y1 + 1)}

    def test_every_level_of_the_shaft_reaches_the_light_asked_for(self):
        result = self.b.shaft(CENTER, 64, 96, light="minecraft:glowstone",
                              light_level=8)
        lamps = [y for y in range(64, 97)
                 if self.save.get_block(CENTER[0] + 2, y, CENTER[1]).name
                 == "minecraft:glowstone"]
        self.assertEqual(len(lamps), result.lights)
        for y, worst in self.darkest(64, 96).items():
            self.assertGreaterEqual(worst, 8, f"y={y} falls dark")

    def test_a_lined_shaft_is_lit_behind_its_newel(self):
        """A shaft left solid round its newel -- hollow=False, lining one
        dug by hand -- is dark behind the newel, which the light from the
        wall has to go round: the arithmetic for a hollow shaft left it at
        7 there, and the flood patches it."""
        self.save.fill((0, 60, 0), (15, 100, 15), "minecraft:stone")
        cx, cz = CENTER
        self.save.fill((cx - 1, 64, cz - 1), (cx + 1, 96, cz + 1), "minecraft:air")
        self.save.fill((cx, 64, cz), (cx, 96, cz), "minecraft:stone")
        result = self.b.shaft(CENTER, 64, 96, wall="minecraft:stone_bricks",
                              hollow=False, light="minecraft:glowstone",
                              light_level=8)
        self.assertEqual(result.cleared, 0, "nothing was carved")
        self.assertEqual(self.save.get_block(cx, 70, cz).name, "minecraft:stone")
        for y, worst in self.darkest(64, 96).items():
            self.assertGreaterEqual(worst, 8, f"y={y} is dark behind the newel")

    def test_every_is_kept_and_the_flood_patches_what_it_leaves_dark(self):
        """``every`` is the spacing asked for, kept to the top of the run,
        not a proposal the flood trims wherever the lamp below already
        reaches. And it is still checked: round a newel, a level between
        two lamps every second level gets 9 behind it, under the 10 asked
        for, so the levels between get lamps too."""
        self.save.fill((0, 60, 0), (15, 100, 15), "minecraft:stone")
        cx, cz = CENTER
        self.save.fill((cx - 1, 64, cz - 1), (cx + 1, 96, cz + 1), "minecraft:air")
        self.save.fill((cx, 64, cz), (cx, 96, cz), "minecraft:stone")
        result = self.b.shaft(CENTER, 64, 96, hollow=False,
                              light="minecraft:glowstone", light_level=10,
                              every=2)
        lamps = {y for y in range(64, 97)
                 if self.save.get_block(cx + 2, y, cz).name == "minecraft:glowstone"}
        self.assertLessEqual(set(range(64, 97, 2)), lamps, "every second level")
        self.assertEqual(result.lights, len(lamps))
        for y, worst in self.darkest(64, 96).items():
            self.assertGreaterEqual(worst, 10, f"y={y} is dark behind the newel")
        # Open, a lamp at every level is a lamp at every level, the top
        # three included, though the one under each would have lit it.
        open_shaft = self.b.shaft((8, -8), 64, 96, light="minecraft:glowstone",
                                  every=1, light_level=8)
        self.assertEqual(
            [y for y in range(64, 97)
             if self.save.get_block(10, y, -8).name == "minecraft:glowstone"],
            list(range(64, 97)))
        self.assertEqual((open_shaft.lights, open_shaft.every), (33, 1))

    def test_a_shaft_its_wall_cannot_light_is_refused_before_writing(self):
        """A shaft whose interior is still rock next to its lamp column --
        the column inside the east wall never dug -- cannot be lit from that
        wall wherever the lamps go: refused naming every dark cell, with
        nothing carved, lined or lit. Straight-line arithmetic lit it
        through the rock."""
        self.save.fill((0, 60, 0), (31, 100, 31), "minecraft:stone")
        cx, cz = 20, 20
        self.save.fill((cx - 1, 64, cz - 1), (cx, 80, cz + 1), "minecraft:air")
        with self.assertRaises(Refused) as caught:
            self.b.shaft((cx, cz), 64, 80, wall="minecraft:stone_bricks",
                         hollow=False, light="minecraft:glowstone",
                         light_level=8)
        message = str(caught.exception)
        self.assertIn("cannot be lit to 8 from its east wall", message)
        self.assertIn("Nothing was written", message)
        self.assertEqual(len(caught.exception.faults), 6 * 17,
                         "every dug cell of every level")
        self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(self.save.get_block(cx + 2, 70, cz).name,
                         "minecraft:stone", "no lamp")
        self.assertEqual(self.save.get_block(cx + 2, 70, cz + 2).name,
                         "minecraft:stone", "no lining")

    def test_a_lamp_too_dim_for_the_shaft_is_refused_before_writing(self):
        with self.assertRaises(NBTError) as caught:
            self.b.shaft(CENTER, 64, 96, light="minecraft:magma_block",
                         light_level=8)
        self.assertIn("emits light 3", str(caught.exception))
        self.assertEqual(self.save.get_block(CENTER[0], 70, CENTER[1]).name,
                         "minecraft:air", "nothing was carved")

    def test_a_lamp_that_will_not_stay_lit_is_refused_before_writing(self):
        # A lit redstone lamp in a wall goes dark at the next block update
        # with nothing to power it; a shaft lit by one is lit until then.
        self.save.fill((0, 60, 0), (15, 100, 15), "minecraft:stone")
        with self.assertRaises(NBTError) as caught:
            self.b.shaft(CENTER, 64, 96, light="minecraft:redstone_lamp[lit=true]",
                         light_level=8)
        self.assertIn("does not last", str(caught.exception))
        self.assertEqual(self.save.get_block(CENTER[0], 70, CENTER[1]).name,
                         "minecraft:stone", "nothing was carved")

    def test_a_lamp_whose_light_fades_is_budgeted_at_what_it_keeps(self):
        # An unwaxed copper bulb weathers down to 4, too little for a
        # radius-1 shaft at 8; the cure is wax, not a brighter block.
        with self.assertRaises(NBTError) as caught:
            self.b.shaft(CENTER, 64, 96, light="minecraft:copper_bulb[lit=true]",
                         light_level=8)
        self.assertIn("emits light 4 that lasts", str(caught.exception))
        self.assertIn("wax it", str(caught.exception))
        self.assertEqual(self.save.get_block(CENTER[0], 70, CENTER[1]).name,
                         "minecraft:air", "nothing was carved")
        result = self.b.shaft(CENTER, 64, 96,
                              light="minecraft:waxed_copper_bulb[lit=true]",
                              light_level=8)
        self.assertGreater(result.lights, 0)

    def test_a_shaft_then_a_stair_is_a_stairwell(self):
        self.save.fill((0, 60, 0), (15, 110, 15), "minecraft:stone")
        # Three levels past the top step, for the head of whoever comes
        # down onto it.
        self.b.shaft(CENTER, 64, 99, wall="minecraft:stone_bricks",
                     light="minecraft:glowstone", light_level=8)
        spiral = SpiralStair(CENTER)
        self.b.spiral_stair(spiral, 64, 96)
        self.assertEqual(spiral.verify(self.save, 64, 96), [])
        self.assertEqual(self.climb(spiral, 65, 96), [])

    def test_a_shaft_carved_no_taller_than_its_stair_is_refused(self):
        # The sharp edge worth having a test for: a shaft that stops level
        # with the top step leaves that step with a stone ceiling, which no
        # top-down render would ever show.
        self.save.fill((0, 60, 0), (15, 110, 15), "minecraft:stone")
        self.b.shaft(CENTER, 64, 96)
        with self.assertRaises(NBTError) as caught:
            self.b.spiral_stair(SpiralStair(CENTER), 64, 96)
        self.assertIn("headroom", str(caught.exception))

    def test_a_radius_below_one_is_refused(self):
        with self.assertRaises(NBTError):
            self.b.shaft(CENTER, 64, 96, radius=0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
