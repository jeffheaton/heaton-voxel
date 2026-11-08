"""Tests for craft.build's elevations: Rhythm, Glazing, Band and Elevation,
and the writers that take one wherever they take a Facade.

A Facade is one rule for a whole wall, and a tower is composed: bays of 7, 4,
4, 3, 4, 4, 7 are no congruence, and a ground story with its own piers does
not line up with the ones above it. An Elevation stacks bands, each with its
own rhythm, and nothing else: a plinth, a belt, a crown and a head course are
bands like any other.

``examples/elevations.py`` is the reference: a sample read off a five-story
office block somebody else built, replayed against it cell for cell when it
was written (3,348 cells of its four walls, the recessed glass and the three
doors among them, none wrong). Its chunks are not ours to vendor, so it is
held here to the numbers read off it: where its piers stand, what each row
holds, how its doors hang, and how many of each block a face takes.
"""

from __future__ import annotations

import collections
import contextlib
import copy
import io
import os
import pickle
import shutil
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "examples"))

from elevations import (  # noqa: E402
    AIR,
    BLACK,
    BRICK,
    GRAY,
    LIGHT,
    LONG_GROUND,
    LONG_SHAFT,
    OFFICE_EAST_WEST,
    OFFICE_FACES,
    OFFICE_NORTH,
    OFFICE_SOUTH,
    SANDSTONE,
    SHORT_GROUND,
    SHORT_SHAFT,
    WOOL,
    office_stories,
)
from heaton_voxel.__main__ import main  # noqa: E402
from heaton_voxel.blocks import BlockState  # noqa: E402
from heaton_voxel.craft import (  # noqa: E402
    Band,
    Doors,
    Elevation,
    Facade,
    Glazing,
    Layers,
    Material,
    Mix,
    Opening,
    Rhythm,
    Wall,
    floors_behind,
    render_elevation,
)
from heaton_voxel.craft.build import Builder  # noqa: E402
from heaton_voxel.craft.errors import IllegalBlock, Refused  # noqa: E402
from heaton_voxel.craft.style import FROM_PALETTE  # noqa: E402
from heaton_voxel.craft.survey import reachable, unsupported  # noqa: E402
from heaton_voxel.nbt import NBTError  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402
from tests.test_examples import read_png  # noqa: E402
from tests.test_world import make_save  # noqa: E402

STONE = "minecraft:stone_bricks"
GLASS = "minecraft:glass_pane"
PLANKS = "minecraft:oak_planks"
OAK_DOOR = "minecraft:oak_door"
ONE = Rhythm(("wall", 9))


def wall(*, low=0, high=8, at=0, floor_y=63, top=79, stories=(68, 72, 76),
         outward="north", **options):
    return Wall((low, at), (high, at), floor_y=floor_y, top=top,
                outward=outward, stories=stories, **options)


def names(plan) -> dict:
    return {cell: state.name for cell, state in plan.items()}


class RhythmTests(unittest.TestCase):
    def test_runs_are_names_and_pairs(self):
        rhythm = Rhythm("post", ("bay", 3), "pier")
        self.assertEqual(rhythm.columns,
                         ("post", "bay", "bay", "bay", "pier"))
        self.assertEqual(len(rhythm), 5)
        self.assertEqual(rhythm.roles, {"post", "bay", "pier"})
        self.assertEqual(rhythm.runs, (("post", 1), ("bay", 3), ("pier", 1)))

    def test_rhythms_add_repeat_turn_and_mirror(self):
        bay = Rhythm("pier", ("bay", 2))
        self.assertEqual(bay + "post", Rhythm("pier", ("bay", 2), "post"))
        self.assertEqual(bay + bay, bay * 2)
        self.assertEqual(2 * bay, bay * 2)
        self.assertEqual(Rhythm("a", ("b", 2)).reversed(),
                         Rhythm(("b", 2), "a"))
        self.assertEqual(Rhythm("a", "b").mirrored(),
                         Rhythm("a", "b", "b", "a"))
        self.assertEqual(Rhythm("a", "b").mirrored(center=("c", 3)),
                         Rhythm("a", "b", ("c", 3), "b", "a"))

    def test_a_mirrored_rhythm_reads_the_same_from_both_ends(self):
        self.assertEqual(LONG_SHAFT, LONG_SHAFT.reversed())

    def test_runs_of_one_role_are_one_run_however_they_were_made(self):
        self.assertEqual(Rhythm("a", "a", ("a", 2)).runs, (("a", 4),))
        self.assertEqual(Rhythm(("a", 2)) * 2, Rhythm(("a", 4)))

    def test_it_is_a_value(self):
        rhythm = Rhythm("a", ("b", 3))
        self.assertEqual(hash(rhythm), hash(Rhythm("a", "b", "b", "b")))
        self.assertIs(copy.copy(rhythm), rhythm)
        self.assertIs(copy.deepcopy(rhythm), rhythm)
        self.assertEqual(pickle.loads(pickle.dumps(rhythm)), rhythm)
        with self.assertRaises(AttributeError):
            rhythm.columns = ()
        self.assertEqual(repr(rhythm), "Rhythm('a', ('b', 3))")

    def test_what_is_no_rhythm_is_refused(self):
        for runs, words in (((), "at least one column"),
                            ((("a", 0),), "at least one column wide"),
                            ((("", 1),), "is a name"),
                            (((1, 2),), "is a name"),
                            ((("a", 2, 3),), "role names and"),
                            ((7,), "role names and")):
            with self.subTest(runs=runs):
                with self.assertRaises(NBTError) as caught:
                    Rhythm(*runs)
                self.assertIn(words, str(caught.exception))
        with self.assertRaises(NBTError):
            Rhythm("a") * 0


class GlazingTests(unittest.TestCase):
    def ladder(self, glazing, tall=wall()) -> list:
        """What the first column holds, bottom row first."""
        plan = Elevation(Band(None, ONE, {"wall": glazing})).plan(tall)
        return [plan[(0, y, 0)].name for y in tall.rows]

    def test_a_spandrel_takes_the_floor_row_and_the_rows_stand_over_it(self):
        got = self.ladder(Glazing(GRAY, LIGHT, GRAY, spandrel=BRICK))
        # The ground story, 64..67, begins at once: it has no floor row in
        # the wall.
        self.assertEqual(got[:4], [GRAY, LIGHT, GRAY, GRAY])
        self.assertEqual(got[4:8], [BRICK, GRAY, LIGHT, GRAY])      # from 68
        self.assertEqual(got[8:12], [BRICK, GRAY, LIGHT, GRAY])     # from 72

    def test_without_one_the_rows_run_through_the_floors(self):
        got = self.ladder(Glazing(GRAY, GRAY, LIGHT, LIGHT))
        self.assertEqual(got[4:8], [GRAY, GRAY, LIGHT, LIGHT])      # from 68
        self.assertEqual(got[8:12], [GRAY, GRAY, LIGHT, LIGHT])     # from 72

    def test_it_starts_again_at_every_floor_whatever_the_story_heights(self):
        tall = wall(top=85, stories=(70, 73, 80))
        got = self.ladder(Glazing(GRAY, LIGHT, spandrel=BRICK), tall)
        by_row = dict(zip(tall.rows, got))
        for floor in (70, 73, 80):
            self.assertEqual(by_row[floor], BRICK)
            self.assertEqual(by_row[floor + 1], GRAY)
        self.assertEqual([by_row[y] for y in range(81, 86)],
                         [GRAY, LIGHT, GRAY, LIGHT, GRAY])

    def test_a_mix_is_picked_at_each_world_cell(self):
        glass = Mix([(GRAY, 1), (LIGHT, 1)], seed=4)
        plan = Elevation(Band(None, ONE, {"wall": Glazing(glass)})).plan(wall())
        for cell, state in plan.items():
            self.assertEqual(state.name, glass.pick(*cell), cell)

    def test_a_material_is_its_block(self):
        got = self.ladder(Glazing(Material(BRICK)))
        self.assertEqual(set(got), {BRICK})

    def test_start_says_how_far_over_the_floor_the_rows_begin(self):
        """A ground story's rows read from the bottom, whatever its height."""
        got = self.ladder(Glazing(GRAY, LIGHT, LIGHT, LIGHT, start=1))
        self.assertEqual(got[:4], [GRAY, LIGHT, LIGHT, LIGHT])
        self.assertEqual(Glazing(GRAY).start, 0)
        self.assertEqual(Glazing(GRAY, spandrel=BRICK).start, 1)
        self.assertIn("start=2", repr(Glazing(GRAY, start=2)))
        with self.assertRaises(NBTError):
            Glazing(GRAY, start=-1)

    def test_it_holds_a_row_at_least(self):
        with self.assertRaises(NBTError):
            Glazing()
        with self.assertRaises(TypeError):
            Glazing(None)
        with self.assertRaises(TypeError):
            Glazing(GRAY, spandrel=FROM_PALETTE)

    def test_it_says_what_it_is(self):
        self.assertIn("spandrel", repr(Glazing(GRAY, spandrel=BRICK)))
        self.assertNotIn("spandrel", repr(Glazing(GRAY)))


class BandTests(unittest.TestCase):
    def test_every_role_is_said_and_none_is_left_over(self):
        rhythm = Rhythm("pier", ("bay", 8))
        with self.assertRaises(NBTError) as caught:
            Band(1, rhythm, {"pier": BRICK})
        self.assertIn("['bay']", str(caught.exception))
        with self.assertRaises(NBTError) as caught:
            Band(1, rhythm, {"pier": BRICK, "bay": GLASS, "bays": GLASS})
        self.assertIn("['bays']", str(caught.exception))

    def test_rows_are_a_whole_number_or_the_rest(self):
        for rows in (0, -1):
            with self.assertRaises(NBTError):
                Band(rows, ONE, {"wall": BRICK})
        with self.assertRaises(NBTError):
            Band(1.5, ONE, {"wall": BRICK})
        self.assertIsNone(Band(None, ONE, {"wall": BRICK}).rows)
        self.assertIn("the rest", repr(Band(None, ONE, {"wall": BRICK})))

    def test_a_rhythm_is_a_rhythm_and_roles_a_mapping(self):
        with self.assertRaises(NBTError):
            Band(1, ("wall", 9), {"wall": BRICK})
        with self.assertRaises(NBTError):
            Band(1, ONE, [("wall", BRICK)])

    def test_a_material_is_named_at_the_call(self):
        with self.assertRaises(TypeError):
            Band(1, ONE, {"wall": None})
        with self.assertRaises(TypeError) as caught:
            Band(1, ONE, {"wall": FROM_PALETTE})
        self.assertIn("named at the call", str(caught.exception))
        with self.assertRaises(TypeError):
            Band(1, ONE, {"wall": 7})
        self.assertEqual(Band(1, ONE, {"wall": Material(BRICK)}).roles["wall"],
                         BlockState(BRICK))


class LayersTests(unittest.TestCase):
    RHYTHM = Rhythm("pier", ("bay", 7), "pier")

    def plan(self, layers, tall=None):
        rule = Elevation(Band(None, self.RHYTHM, {"pier": BRICK, "bay": layers}))
        return rule.plan(tall or wall(top=66, stories=()))

    def test_they_run_front_to_back_from_the_wall_line(self):
        tall = wall(top=66, stories=())          # faces north, at z=0
        plan = self.plan(Layers(AIR, GLASS), tall)
        self.assertEqual(plan[tall.cell(4, 65, 0)].name, AIR)
        self.assertEqual(plan[tall.cell(4, 65, 1)].name, GLASS)
        self.assertEqual(tall.cell(4, 65, 1), (4, 65, 1))

    def test_they_go_inward_whichever_way_the_wall_faces(self):
        for outward, back in (("north", (4, 65, 1)), ("south", (4, 65, -1))):
            tall = wall(top=66, stories=(), outward=outward)
            self.assertEqual(self.plan(Layers(AIR, GLASS), tall)[back].name,
                             GLASS, outward)

    def test_a_role_without_them_fills_the_wall_and_no_more(self):
        tall = wall(top=66, stories=())
        plan = self.plan(Layers(AIR, GLASS), tall)
        self.assertNotIn(tall.cell(0, 65, 1), plan)
        self.assertEqual(len(plan), (2 + 2 * 7) * 3)

    def test_they_say_exactly_their_own_whatever_the_wall_is_thick(self):
        thick = wall(top=66, stories=(), thickness=3)
        plan = self.plan(Layers(AIR, GLASS), thick)
        self.assertEqual(plan[thick.cell(0, 65, 2)].name, BRICK)
        self.assertNotIn(thick.cell(4, 65, 2), plan)

    def test_a_glazing_and_a_mix_stand_in_a_layer(self):
        tall = wall()
        plan = self.plan(Layers(AIR, Glazing(GRAY, LIGHT, spandrel=BRICK)), tall)
        self.assertEqual(plan[tall.cell(4, 68, 1)].name, BRICK)
        self.assertEqual(plan[tall.cell(4, 69, 1)].name, GRAY)
        self.assertEqual(plan[tall.cell(4, 70, 1)].name, LIGHT)
        glass = Mix([(GRAY, 1), (LIGHT, 1)], seed=2)
        cell = tall.cell(4, 70, 1)
        self.assertEqual(self.plan(Layers(AIR, glass), tall)[cell].name,
                         glass.pick(*cell))

    def test_every_layer_is_named(self):
        with self.assertRaises(NBTError):
            Layers()
        with self.assertRaises(TypeError):
            Layers(AIR, None)
        with self.assertRaises(TypeError):
            Layers(FROM_PALETTE)
        self.assertIn("Layers(", repr(Layers(GLASS)))


class DoorsTests(unittest.TestCase):
    COMPASS = ("north", "east", "south", "west")

    def run_of(self, n, *, outward="north"):
        """A wall facing ``outward``, with ``n`` doors from its low end."""
        along = Wall((0, 0), (9, 0), floor_y=63, top=66, outward=outward) \
            if outward in ("north", "south") else \
            Wall((0, 0), (0, 9), floor_y=63, top=66, outward=outward)
        rhythm = Rhythm(("door", n), ("wall", 10 - n))
        rule = Elevation(
            Band(2, rhythm, {"door": Doors(OAK_DOOR), "wall": STONE}),
            Band(None, Rhythm(("wall", 10)), {"wall": STONE}))
        plan = rule.plan(along)
        return along, [plan[along.cell(along.low + i, 64)] for i in range(n)]

    def test_each_is_two_halves_on_its_floor(self):
        along, _ = self.run_of(1)
        rule = Elevation(Band(2, Rhythm("door", ("wall", 9)),
                              {"door": Doors(OAK_DOOR), "wall": STONE}),
                         Band(None, Rhythm(("wall", 10)), {"wall": STONE}))
        plan = rule.plan(along)
        self.assertEqual(plan[(0, 64, 0)].properties["half"], "lower")
        self.assertEqual(plan[(0, 65, 0)].properties["half"], "upper")
        self.assertEqual(plan[(0, 65, 0)].properties["hinge"],
                         plan[(0, 64, 0)].properties["hinge"])

    def test_they_open_inward_as_wall_door_hangs_one(self):
        for outward in self.COMPASS:
            along, (door,) = self.run_of(1, outward=outward)
            self.assertEqual(door.properties["facing"], along.inward, outward)
            self.assertEqual(door.properties["facing"],
                             along.door(along.low)[3], outward)

    def test_each_hinges_toward_the_nearer_end_of_its_run(self):
        """``right`` is clockwise of the way a door faces, the game's word,
        so which it is turns with the wall; where it falls does not."""
        clockwise = dict(zip(self.COMPASS, self.COMPASS[1:] + self.COMPASS[:1]))
        opposite = {"north": "south", "south": "north",
                    "east": "west", "west": "east"}
        for outward in self.COMPASS:
            low = "west" if outward in ("north", "south") else "north"
            for n in (1, 2, 3, 4, 5):
                _, doors = self.run_of(n, outward=outward)
                for i, door in enumerate(doors):
                    facing = door.properties["facing"]
                    side = (clockwise[facing]
                            if door.properties["hinge"] == "right"
                            else opposite[clockwise[facing]])
                    want = low if i < (n + 1) // 2 else opposite[low]
                    self.assertEqual(side, want, (outward, n, i))

    def test_two_runs_each_hinge_at_their_own_ends(self):
        along = Wall((0, 0), (9, 0), floor_y=63, top=66, outward="north")
        rhythm = Rhythm(("door", 2), ("wall", 2), ("door", 2), ("wall", 4))
        rule = Elevation(
            Band(2, rhythm, {"door": Doors(OAK_DOOR), "wall": STONE}),
            Band(None, Rhythm(("wall", 10)), {"wall": STONE}))
        plan = rule.plan(along)
        hinges = [plan[(x, 64, 0)].properties["hinge"] for x in (0, 1, 4, 5)]
        self.assertEqual(hinges[:2], hinges[2:])
        self.assertNotEqual(hinges[0], hinges[1])

    def test_a_door_off_its_floor_is_refused_when_it_is_laid(self):
        along = Wall((0, 0), (9, 0), floor_y=63, top=66, outward="north")
        rule = Elevation(Band(3, Rhythm(("door", 10)), {"door": Doors(OAK_DOOR)}))
        with self.assertRaises(NBTError) as caught:
            rule.plan(along)
        self.assertIn("two rows over its floor", str(caught.exception))

    def test_what_a_rule_cannot_hang_is_refused(self):
        with self.assertRaises(NBTError) as caught:
            Doors("minecraft:iron_door")
        self.assertIn("doorway", str(caught.exception))
        with self.assertRaises(NBTError):
            Doors(STONE)
        with self.assertRaises(NBTError):
            Doors("minecraft:oak_trapdoor")
        with self.assertRaises(NBTError) as caught:
            Doors("minecraft:oak_door[hinge=left]")
        self.assertIn("hinge", str(caught.exception))
        with self.assertRaises(TypeError):
            Doors(Mix([(OAK_DOOR, 1)]))
        with self.assertRaises(TypeError):
            Doors(None)
        self.assertIn("oak_door", repr(Doors(OAK_DOOR)))


class ElevationTests(unittest.TestCase):
    def test_bands_stack_from_the_bottom_and_each_keeps_its_own_rhythm(self):
        low = Rhythm("pier", ("gap", 3), "pier", ("gap", 3), "pier")
        high = Rhythm(("gap", 4), "pier", ("gap", 4))
        rule = Elevation(Band(4, low, {"pier": BRICK, "gap": AIR}),
                         Band(None, high, {"pier": STONE, "gap": GLASS}))
        plan = rule.plan(wall())
        self.assertEqual(plan[(0, 64, 0)].name, BRICK)
        self.assertEqual(plan[(4, 67, 0)].name, BRICK)
        self.assertEqual(plan[(1, 64, 0)].name, AIR)
        self.assertEqual(plan[(0, 68, 0)].name, GLASS)
        self.assertEqual(plan[(4, 68, 0)].name, STONE)
        self.assertEqual(plan[(8, 79, 0)].name, GLASS)

    def test_the_plan_is_every_cell_once_in_order(self):
        tall = wall()
        plan = Elevation(Band(None, ONE, {"wall": BRICK})).plan(tall)
        self.assertEqual(len(plan), len(tall.along) * len(tall.rows))
        self.assertEqual(list(plan), sorted(plan))

    def test_it_is_pure(self):
        rule = Elevation(Band(None, ONE, {"wall": Mix([(BRICK, 1), (STONE, 1)])}))
        self.assertEqual(rule.plan(wall()), rule.plan(wall()))

    def test_every_layer_of_a_thick_wall_is_the_role(self):
        tall = wall(thickness=2)
        plan = Elevation(Band(None, ONE, {"wall": BRICK})).plan(tall)
        self.assertEqual(len(plan), 2 * len(tall.along) * len(tall.rows))
        self.assertEqual(plan[tall.cell(3, 70, 1)].name, BRICK)

    def test_the_columns_run_from_the_low_end_whichever_way_it_faces(self):
        rhythm = Rhythm("a", ("b", 8))
        rule = Elevation(Band(None, rhythm, {"a": BRICK, "b": STONE}))
        for outward in ("north", "south"):
            plan = rule.plan(wall(outward=outward))
            self.assertEqual(plan[(0, 70, 0)].name, BRICK, outward)
            self.assertEqual(plan[(8, 70, 0)].name, STONE, outward)
        for outward in ("east", "west"):
            run = Wall((0, 0), (0, 8), floor_y=63, top=70, outward=outward)
            plan = rule.plan(run)
            self.assertEqual(plan[(0, 70, 0)].name, BRICK, outward)
            self.assertEqual(plan[(0, 70, 8)].name, STONE, outward)

    def test_a_wall_of_any_height_stretches_the_one_band(self):
        rule = Elevation(Band(2, ONE, {"wall": BRICK}),
                         Band(None, ONE, {"wall": GLASS}),
                         Band(1, ONE, {"wall": STONE}))
        for top in (67, 90, 200):
            plan = rule.plan(wall(top=top, stories=()))
            self.assertEqual(plan[(0, 64, 0)].name, BRICK)
            self.assertEqual(plan[(0, 66, 0)].name, GLASS)
            self.assertEqual(plan[(0, top, 0)].name, STONE)

    def test_a_rhythm_the_wrong_width_is_refused_not_cut(self):
        rule = Elevation(Band(None, Rhythm(("wall", 8)), {"wall": BRICK}))
        with self.assertRaises(NBTError) as caught:
            rule.plan(wall())
        self.assertIn("8 columns", str(caught.exception))
        self.assertIn("0..8, 9", str(caught.exception))

    def test_bands_that_do_not_make_the_height_are_refused(self):
        rule = Elevation(Band(3, ONE, {"wall": BRICK}))
        with self.assertRaises(NBTError) as caught:
            rule.plan(wall())
        self.assertIn("3 rows", str(caught.exception))
        self.assertIn("16", str(caught.exception))

    def test_a_stretching_band_may_come_to_nothing_and_no_less(self):
        """A crown standing straight on its base is a building."""
        rule = Elevation(Band(16, ONE, {"wall": BRICK}),
                         Band(None, ONE, {"wall": GLASS}))
        self.assertEqual({state.name for state in rule.plan(wall()).values()},
                         {BRICK})
        taller = Elevation(Band(17, ONE, {"wall": BRICK}),
                           Band(None, ONE, {"wall": GLASS}))
        with self.assertRaises(NBTError) as caught:
            taller.plan(wall())
        self.assertIn("17 rows", str(caught.exception))
        self.assertIn("too low", str(caught.exception))

    def test_what_is_no_elevation_is_refused(self):
        with self.assertRaises(NBTError):
            Elevation()
        with self.assertRaises(NBTError):
            Elevation("a band")
        with self.assertRaises(NBTError):
            Elevation(Band(None, ONE, {"wall": BRICK}),
                      Band(None, ONE, {"wall": STONE}))
        with self.assertRaises(NBTError):
            Elevation(Band(1, ONE, {"wall": BRICK})).plan("a wall")

    def test_role_says_what_a_column_is_at_a_row(self):
        rule = Elevation(Band(4, Rhythm("a", ("b", 8)), {"a": BRICK, "b": AIR}),
                         Band(None, Rhythm(("c", 8), "d"),
                              {"c": BRICK, "d": STONE}))
        tall = wall()
        self.assertEqual(rule.role(tall, 0, 64), "a")
        self.assertEqual(rule.role(tall, 0, 68), "c")
        self.assertEqual(rule.role(tall, 8, 79), "d")
        for along, y in ((9, 70), (0, 80), (0, 63)):
            with self.subTest(along=along, y=y), self.assertRaises(NBTError):
                rule.role(tall, along, y)

    def test_it_says_what_it_is(self):
        rule = Elevation(Band(2, ONE, {"wall": BRICK}))
        self.assertEqual(repr(rule), "Elevation(Band(2 rows, 9 columns))")


class WritersBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="elevation-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, [(x, z) for x in range(0, 4) for z in range(0, 4)])
        self.save = MinecraftSave.load(self.dir)
        self.b = Builder(self.save)

    def name(self, x, y, z):
        return self.save.get_block(x, y, z).name


class WriterTests(WritersBase):
    RULE = Elevation(Band(2, Rhythm(("wall", 9)), {"wall": BRICK}),
                     Band(None, Rhythm("pier", ("bay", 7), "pier"),
                          {"pier": STONE, "bay": Glazing(GLASS)}))
    RUN = Wall((2, 5), (10, 5), floor_y=63, top=69, outward="north")

    def test_wall_lays_it_and_verify_holds_it(self):
        result = self.b.wall(self.RUN, self.RULE)
        self.assertEqual(result.blocks, 9 * 6)
        self.assertEqual(self.name(2, 64, 5), BRICK)
        self.assertEqual(self.name(2, 66, 5), STONE)
        self.assertEqual(self.name(5, 69, 5), GLASS)
        self.assertEqual(self.RULE.verify(self.save, self.RUN), [])

    def test_verify_names_the_cell_and_what_it_should_hold(self):
        self.b.wall(self.RUN, self.RULE)
        self.save.set_block(5, 66, 5, "minecraft:dirt")
        wrong = self.RULE.verify(self.save, self.RUN)
        self.assertEqual(len(wrong), 1)
        self.assertIn("(5, 66, 5) holds minecraft:dirt", wrong[0])
        self.assertIn("where the elevation has", wrong[0])

    def test_laying_it_twice_writes_nothing_new(self):
        self.b.wall(self.RUN, self.RULE)
        self.assertEqual(self.b.wall(self.RUN, self.RULE).already, 9 * 6)

    def test_what_stands_in_its_way_is_refused_whole(self):
        self.save.set_block(5, 66, 5, "minecraft:dirt")
        with self.assertRaises(Refused):
            self.b.wall(self.RUN, self.RULE)
        self.assertEqual(self.name(2, 64, 5), "minecraft:air")
        self.b.wall(self.RUN, self.RULE, overwrite=True)
        self.assertEqual(self.name(5, 66, 5), GLASS)

    def test_a_block_the_game_lacks_is_refused_before_a_write(self):
        rule = Elevation(Band(None, Rhythm(("wall", 9)),
                              {"wall": "minecraft:quartz_wall"}))
        with self.assertRaises(IllegalBlock):
            self.b.wall(self.RUN, rule)
        self.assertEqual(self.name(2, 64, 5), "minecraft:air")

    def test_a_column_of_air_is_kept_clear(self):
        rule = Elevation(Band(None, Rhythm("pier", ("gap", 7), "pier"),
                              {"pier": STONE, "gap": AIR}))
        self.save.set_block(5, 66, 5, "minecraft:dirt")
        with self.assertRaises(Refused):
            self.b.wall(self.RUN, rule)

    def test_an_opening_cuts_through_it_as_through_a_facade(self):
        result = self.b.wall(self.RUN, self.RULE,
                             openings=[(Opening.rect(2, 3), 5)])
        self.assertEqual(result.openings, 6)
        self.assertEqual(self.name(5, 64, 5), "minecraft:air")
        self.assertEqual(self.name(7, 64, 5), BRICK)

    def test_cut_opening_knows_the_wall_it_built(self):
        self.b.wall(self.RUN, self.RULE)
        with self.assertRaises(Refused):
            self.b.cut_opening(self.RUN, Opening.rect(2, 3), 5)
        cut = self.b.cut_opening(self.RUN, Opening.rect(2, 3), 5,
                                 facade=self.RULE)
        self.assertEqual(len(cut), 6)

    def test_a_writer_takes_a_rule_and_nothing_else(self):
        for call in (lambda: self.b.wall(self.RUN, "a facade"),
                     lambda: self.b.shell(2, 5, 10, 9, "a facade",
                                          floor_y=63, top=69),
                     lambda: self.b.shell(2, 5, 10, 9, self.RULE, faces={
                         "north": "a facade"}, floor_y=63, top=69),
                     lambda: self.b.cut_opening(self.RUN, Opening.rect(2, 3), 5,
                                                facade="a facade")):
            with self.assertRaises(NBTError) as caught:
                call()
            self.assertIn("Elevation", str(caught.exception))


class ShellTests(WritersBase):
    def test_a_shell_takes_an_elevation_a_side(self):
        """Each face's rhythm is its own width: the north and south walls
        own the corners, and the east and west ones run between them."""
        long = Elevation(Band(None, Rhythm("post", ("bay", 9), "post"),
                              {"post": BRICK, "bay": Glazing(GLASS)}))
        short = Elevation(Band(None, Rhythm(("bay", 5)), {"bay": Glazing(GLASS)}))
        result = self.b.shell(2, 2, 12, 8, long,
                              faces={"east": short, "west": short},
                              floor_y=63, top=66, floor=PLANKS)
        self.assertEqual(self.name(2, 64, 2), BRICK)
        self.assertEqual(self.name(12, 64, 8), BRICK)
        self.assertEqual(self.name(3, 64, 2), GLASS)
        self.assertEqual(self.name(2, 64, 3), GLASS)
        self.assertEqual(long.verify(self.save, result.walls["north"]), [])
        self.assertEqual(short.verify(self.save, result.walls["east"]), [])

    def test_a_facade_and_an_elevation_share_a_shell(self):
        short = Elevation(Band(None, Rhythm(("bay", 5)), {"bay": BRICK}))
        self.b.shell(2, 2, 12, 8, Facade(STONE),
                     faces={"east": short, "west": short}, floor_y=63, top=66)
        self.assertEqual(self.name(5, 64, 2), STONE)
        self.assertEqual(self.name(2, 64, 5), BRICK)

    def test_a_wall_too_short_for_its_rhythm_is_refused_before_a_write(self):
        narrow = Elevation(Band(None, Rhythm(("bay", 11)), {"bay": BRICK}))
        with self.assertRaises(NBTError):
            self.b.shell(2, 2, 12, 8, narrow, floor_y=63, top=66)
        self.assertEqual(self.name(2, 64, 2), "minecraft:air")

    def recessed(self):
        """Glass a block behind two-square posts, on a 11 by 7 shell. The
        long walls own the corners, so theirs are two wide and the short
        walls' one, the second layer of which meets them."""
        long = Elevation(Band(None, Rhythm(("post", 2), ("bay", 7), ("post", 2)),
                              {"post": Layers(BRICK, BRICK),
                               "bay": Layers(AIR, GLASS)}))
        short = Elevation(Band(None, Rhythm("post", ("bay", 3), "post"),
                               {"post": Layers(BRICK, BRICK),
                                "bay": Layers(AIR, GLASS)}))
        return long, {"east": short, "west": short}

    def test_a_recess_stands_inside_the_walls_and_the_hollow_leaves_it(self):
        long, faces = self.recessed()
        result = self.b.shell(2, 2, 12, 8, long, faces=faces, floor_y=63, top=66)
        self.assertEqual(self.name(5, 64, 2), "minecraft:air")
        self.assertEqual(self.name(5, 64, 3), GLASS)
        self.assertEqual(self.name(5, 64, 5), "minecraft:air")
        self.assertEqual(self.name(3, 64, 3), BRICK)
        for face, wall_ in result.walls.items():
            rule = long if face in ("north", "south") else faces[face]
            self.assertEqual(rule.verify(self.save, wall_), [], face)

    def test_what_the_hollow_will_clear_is_no_cell_taken(self):
        """The hollow is a cut, so dirt filling the inside is gone before a
        recess is laid in it; the same dirt where no cut reaches is in the
        way."""
        long, faces = self.recessed()
        self.save.fill((3, 64, 3), (11, 66, 7), "minecraft:dirt")
        with self.assertRaises(Refused):
            self.b.shell(2, 2, 12, 8, long, faces=faces, floor_y=63, top=66,
                         hollow=False)
        self.b.shell(2, 2, 12, 8, long, faces=faces, floor_y=63, top=66)
        self.assertEqual(self.name(5, 64, 3), GLASS)
        self.assertEqual(self.name(5, 64, 5), "minecraft:air")

    def test_walls_that_meet_in_a_cell_have_to_agree(self):
        long, faces = self.recessed()
        other = Elevation(Band(None, Rhythm("post", ("bay", 3), "post"),
                               {"post": Layers(STONE, STONE),
                                "bay": Layers(AIR, GLASS)}))
        with self.assertRaises(NBTError) as caught:
            self.b.shell(2, 2, 12, 8, long, faces={"east": other, "west": other},
                         floor_y=63, top=66)
        self.assertIn("have to agree", str(caught.exception))
        self.assertEqual(self.name(2, 64, 2), "minecraft:air")

    def test_a_shells_floor_runs_through_a_recess(self):
        long, faces = self.recessed()
        self.b.shell(2, 2, 12, 8, long, faces=faces, floor_y=63, top=69,
                     stories=(66,), floor=PLANKS)
        self.assertEqual(self.name(5, 65, 3), GLASS)
        self.assertEqual(self.name(5, 66, 3), PLANKS)
        self.assertEqual(self.name(5, 67, 3), GLASS)

    def test_a_door_over_a_hole_is_refused(self):
        rule = Elevation(
            Band(2, Rhythm(("wall", 4), "door", ("wall", 4)),
                 {"door": Doors(OAK_DOOR), "wall": STONE}),
            Band(None, ONE, {"wall": STONE}))
        with self.assertRaises(Refused):
            self.b.wall(wall(low=2, high=10, at=5, top=66, stories=()), rule)

    def test_a_door_somebody_opened_is_the_door_that_was_hung(self):
        self.save.fill((2, 63, 5), (10, 63, 5), STONE)
        run = wall(low=2, high=10, at=5, top=66, stories=())
        rule = Elevation(
            Band(2, Rhythm(("wall", 4), "door", ("wall", 4)),
                 {"door": Doors(OAK_DOOR), "wall": STONE}),
            Band(None, ONE, {"wall": STONE}))
        self.b.wall(run, rule)
        for y in (64, 65):
            door = self.save.get_block(6, y, 5)
            self.save.set_block(6, y, 5, door.with_properties(open="true"))
        self.assertEqual(rule.verify(self.save, run), [])
        self.assertEqual(self.b.wall(run, rule).already,
                         len(rule.plan(run)))
        self.assertEqual(self.save.get_block(6, 64, 5).properties["open"], "true")

    def test_panes_join_when_the_build_is_finished(self):
        rule = Elevation(Band(None, Rhythm("post", ("bay", 9), "post"),
                              {"post": BRICK, "bay": Glazing(GLASS)}))
        run = Wall((2, 5), (12, 5), floor_y=63, top=66, outward="north")
        self.b.wall(run, rule)
        self.assertNotIn("east", self.save.get_block(5, 64, 5).properties)
        self.b.finish()
        joined = self.save.get_block(5, 64, 5).properties
        self.assertEqual((joined["east"], joined["west"]), ("true", "true"))
        self.assertEqual(rule.verify(self.save, run), [])


class OfficeTests(unittest.TestCase):
    """``examples/elevations.py``, held to what was read off the building."""

    WALLS = Wall.ring(100, 200, 140, 222, **office_stories(4, 30))
    #: Every block each face takes, from the building it was read off.
    NORTH = {AIR: 145, BLACK: 10, BRICK: 244, GRAY: 293, LIGHT: 292, WOOL: 75,
             SANDSTONE: 42, OAK_DOOR: 6}
    SOUTH = {AIR: 145, BLACK: 10, BRICK: 238, GRAY: 293, LIGHT: 304, WOOL: 75,
             SANDSTONE: 42}
    SHORT = {AIR: 80, BRICK: 111, GRAY: 128, LIGHT: 208, WOOL: 20, SANDSTONE: 20}

    def plan(self, face):
        return OFFICE_FACES[face].plan(self.WALLS[face])

    def column(self, face, along, layer=0) -> list:
        """A column of a face as block names, bottom row up, None where
        the rule says nothing."""
        wall_ = self.WALLS[face]
        plan = self.plan(face)
        cells = (plan.get(wall_.cell(along, y, layer)) for y in wall_.rows)
        return [None if state is None else state.name for state in cells]

    def test_the_footprint_and_the_stories(self):
        self.assertEqual(office_stories(4, -60), {
            "floor_y": -60, "top": -38, "stories": (-54, -50, -46, -42)})
        self.assertEqual(len(LONG_SHAFT), 41)
        self.assertEqual(len(LONG_GROUND), 41)
        self.assertEqual(len(SHORT_SHAFT), 21)
        self.assertEqual(len(SHORT_GROUND), 21)

    def test_every_block_of_every_face(self):
        for face, census in (("north", self.NORTH), ("south", self.SOUTH),
                             ("east", self.SHORT), ("west", self.SHORT)):
            with self.subTest(face=face):
                self.assertEqual(
                    collections.Counter(names(self.plan(face)).values()), census)

    def test_the_short_faces_are_one_wall(self):
        east, west = self.WALLS["east"], self.WALLS["west"]
        e, w = self.plan("east"), self.plan("west")
        self.assertEqual(len(e), len(w))
        for a in east.along:
            for y in east.rows:
                for layer in (0, 1):
                    one = e.get(east.cell(a, y, layer))
                    other = w.get(west.cell(a, y, layer))
                    self.assertEqual(one, other, (a, y, layer))

    def test_the_long_faces_differ_only_at_the_entrance(self):
        north, south = self.WALLS["north"], self.WALLS["south"]
        n, s = self.plan("north"), self.plan("south")
        for a in north.along:
            for y in north.rows:
                for layer in (0, 1):
                    one = n.get(north.cell(a, y, layer))
                    other = s.get(south.cell(a, y, layer))
                    same = (one is None) == (other is None) and (
                        one is None or one.name == other.name)
                    if layer == 1 and a in (119, 120, 121):
                        continue
                    self.assertTrue(same, (a, y, layer))

    def test_the_upper_piers_stand_5_5_4_5_5_apart(self):
        self.assertEqual(
            [i for i, role in enumerate(LONG_SHAFT.columns) if role == "pier"],
            [8, 13, 18, 22, 27, 32])
        self.assertEqual(
            [i for i, role in enumerate(SHORT_SHAFT.columns) if role == "pier"],
            [5, 10, 15])

    def test_the_ground_piers_stand_out_of_line_with_those_above(self):
        ground = [i for i, role in enumerate(LONG_GROUND.columns)
                  if role == "pier"]
        self.assertEqual(ground, [6, 11, 16, 24, 29, 34])
        self.assertFalse(set(ground) & {8, 13, 18, 22, 27, 32})
        self.assertEqual(
            [i for i, role in enumerate(LONG_GROUND.columns) if role == "fin"],
            [18, 22])
        self.assertEqual(
            [i for i, role in enumerate(SHORT_GROUND.columns) if role == "pier"],
            [5, 10, 15])

    def test_a_corner_post_and_its_courses(self):
        self.assertEqual(self.column("south", 100),
                         [SANDSTONE] * 2 + [BRICK] * 4 + [WOOL] * 16)

    def test_a_corner_bay_is_a_curtain_past_the_floors(self):
        column = self.column("south", 103)
        self.assertEqual(column[:6], [AIR] * 5 + [BRICK])
        self.assertEqual(column[6:], ([GRAY, LIGHT, LIGHT, GRAY]) * 4)

    def test_a_long_window_between_its_spandrels(self):
        column = self.column("south", 110)
        self.assertEqual(column[6:18],
                         ([GRAY, LIGHT, GRAY, BRICK]) * 3)
        self.assertEqual(column[18:], [GRAY, LIGHT, GRAY, WOOL])

    def test_a_short_window_is_glazed_its_own_way(self):
        wall_ = self.WALLS["east"]
        self.assertEqual(self.column("east", wall_.low + 7)[6:9],
                         [GRAY, LIGHT, LIGHT])

    def test_the_crown_turns_the_piers_to_wool(self):
        self.assertEqual(self.column("south", 108),
                         [AIR] * 5 + [BRICK] * 13 + [WOOL] * 4)

    def test_the_first_floor_edge_runs_the_whole_way_round(self):
        wall_ = self.WALLS["south"]
        plan = self.plan("south")
        self.assertEqual({plan[wall_.cell(a, 36)].name for a in wall_.along},
                         {BRICK})

    def test_the_ground_glass_stands_a_block_behind_the_piers(self):
        self.assertEqual(self.column("south", 103, 1)[:5],
                         [GRAY, LIGHT, LIGHT, LIGHT, LIGHT])
        self.assertEqual(self.column("south", 103)[:5], [AIR] * 5)

    def test_the_upper_stories_are_one_block_thick(self):
        wall_ = self.WALLS["south"]
        plan = self.plan("south")
        self.assertFalse([cell for cell in plan
                          if cell[1] > 35 and cell[2] != wall_.at])

    def test_the_piers_are_two_deep_and_the_corners_two_square(self):
        for layer in (0, 1):
            self.assertEqual(self.column("south", 106, layer)[:5],
                             [SANDSTONE] * 2 + [BRICK] * 3)
        plan = self.plan("south")
        for cell in ((100, 31, 222), (101, 31, 222), (100, 31, 221),
                     (101, 31, 221)):
            self.assertEqual(plan[cell].name, SANDSTONE, cell)
        east = self.plan("east")
        self.assertEqual(east[(140, 31, 221)].name, SANDSTONE)
        self.assertEqual(east[(139, 31, 221)].name, SANDSTONE)

    def test_the_fins_stand_before_the_jambs(self):
        for along in (118, 122):
            self.assertEqual(self.column("north", along)[:5], [BLACK] * 5)
            self.assertEqual(self.column("north", along, 1)[:5],
                             [SANDSTONE] + [BRICK] * 4)

    def test_the_entrance_is_three_doors_under_a_window(self):
        for along in (119, 120, 121):
            self.assertEqual(self.column("north", along, 1)[:5],
                             [OAK_DOOR, OAK_DOOR, BRICK, GRAY, BRICK])

    def test_its_doors_hang_as_the_game_has_them(self):
        """Read off the building: all three face south, into it, hinged
        right, right and left."""
        wall_ = self.WALLS["north"]
        plan = self.plan("north")
        for along, hinge in ((119, "right"), (120, "right"), (121, "left")):
            for y, half in ((31, "lower"), (32, "upper")):
                self.assertEqual(
                    plan[wall_.cell(along, y, 1)].canonical(),
                    BlockState(OAK_DOOR, {
                        "facing": "south", "half": half, "hinge": hinge,
                        "open": "false", "powered": "false"}).canonical())

    def test_the_south_front_is_glass_where_the_north_has_doors(self):
        self.assertEqual(self.column("south", 120, 1)[:5],
                         [GRAY, LIGHT, LIGHT, LIGHT, LIGHT])

    def test_the_shaft_takes_any_number_of_stories(self):
        for upper in (1, 4, 12, 40):
            walls = Wall.ring(100, 200, 140, 222, **office_stories(upper, 30))
            tall = walls["south"]
            plan = OFFICE_SOUTH.plan(tall)
            # The crown keeps its height and the base stays where it was.
            self.assertEqual(plan[tall.cell(108, tall.top)].name, WOOL)
            self.assertEqual(plan[tall.cell(108, tall.top - 3)].name, WOOL)
            self.assertEqual(plan[tall.cell(100, 31)].name, SANDSTONE)
            self.assertEqual(plan[tall.cell(103, tall.top)].name, GRAY)
            if upper > 1:
                self.assertEqual(plan[tall.cell(108, tall.top - 4)].name, BRICK)

    def test_one_upper_story_stands_the_crown_on_the_base(self):
        walls = Wall.ring(100, 200, 140, 222, **office_stories(1, 30))
        self.assertEqual(len(OFFICE_SOUTH.plan(walls["south"])),
                         41 * 10 + 41 * 5)

    def build(self, *, faces=OFFICE_FACES, **options):
        directory = tempfile.mkdtemp(prefix="elevation-office-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        make_save(directory, [(x, z) for x in range(0, 4) for z in range(0, 4)])
        save = MinecraftSave.load(directory)
        builder = Builder(save)
        options.setdefault("ground", STONE)
        result = builder.shell(4, 4, 44, 26, OFFICE_SOUTH, faces=faces,
                               **office_stories(4, 40), **options)
        return save, builder, result

    def test_it_stands_and_the_game_keeps_every_block(self):
        save, builder, result = self.build(floor=PLANKS, deck=PLANKS)
        builder.finish()
        for face, wall_ in result.walls.items():
            self.assertEqual(OFFICE_FACES[face].verify(save, wall_), [], face)
        self.assertEqual(unsupported(save, (4, 40, 4), (44, 63, 26)), [])

    def test_you_walk_in_through_the_doors_and_back_out(self):
        """From the ground before the north front, under the piers, through
        the doors, into the lobby and home again."""
        save, builder, _ = self.build(floor=PLANKS, deck=PLANKS)
        save.fill((4, 40, -1), (44, 40, 3), STONE)
        builder.finish()
        walked = reachable(save, (24, 40, 2), (0, 40, -1), (48, 45, 26))
        self.assertIn((24, 40, 10), walked.reached, "the lobby")
        self.assertIn((23, 40, 8), walked.reached, "the lobby")
        self.assertFalse(walked.stranded)

    def test_with_glass_for_doors_the_lobby_is_shut(self):
        """The walk above finds the doors, and not a gap beside them: the
        same block with the south front's glass in their place keeps a
        walker out."""
        save, builder, _ = self.build(
            faces=dict(OFFICE_FACES, north=OFFICE_SOUTH),
            floor=PLANKS, deck=PLANKS)
        save.fill((4, 40, -1), (44, 40, 3), STONE)
        builder.finish()
        walked = reachable(save, (24, 40, 2), (0, 40, -1), (48, 45, 26))
        self.assertIn((24, 40, 3), walked.reached, "before the front")
        self.assertNotIn((24, 40, 10), walked.reached)

    def test_a_deck_that_falls_is_refused_as_any_wall_would_be(self):
        """Gravel over the hollow under it: the audit a wall answers to."""
        with self.assertRaises(Refused):
            self.build(deck="minecraft:gravel")

    def test_doors_over_no_floor_are_refused(self):
        with self.assertRaises(Refused):
            self.build(ground=None)


class ReadBase(unittest.TestCase):
    STORIES = office_stories(4, 40)

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="elevation-read-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, [(x, z) for x in range(0, 4) for z in range(0, 4)])
        self.save = MinecraftSave.load(self.dir)
        self.b = Builder(self.save)

    def office(self):
        """The office block, its floors in and its panes joined, with a
        chair behind a window on every upper story."""
        result = self.b.shell(4, 4, 44, 26, OFFICE_SOUTH, faces=OFFICE_FACES,
                              ground=STONE, floor=PLANKS, deck=PLANKS,
                              **self.STORIES)
        for level in self.STORIES["stories"]:
            self.save.set_block(20, level + 1, 25,
                                "minecraft:oak_stairs[facing=south]")
        self.b.finish()
        return result.walls


class FloorsBehindTests(ReadBase):
    def test_it_finds_the_floors_and_not_the_glass_or_the_furniture(self):
        walls = self.office()
        for face, wall_ in walls.items():
            found = floors_behind(self.save, wall_)
            self.assertEqual(found, self.STORIES["stories"], face)

    def test_they_make_a_wall(self):
        walls = self.office()
        bare = Wall.ring(4, 4, 44, 26, floor_y=40, top=62)["south"]
        again = Wall((4, 26), (44, 26), floor_y=40, top=62, outward="south",
                     stories=floors_behind(self.save, bare))
        self.assertEqual(again.stories, walls["south"].stories)

    def test_a_solid_mass_is_no_floor(self):
        self.save.fill((2, 64, 3), (12, 75, 3), STONE)
        run = Wall((2, 2), (12, 2), floor_y=63, top=75, outward="north")
        self.assertEqual(floors_behind(self.save, run), ())

    def test_nothing_behind_a_wall_is_no_floor(self):
        run = Wall((2, 2), (12, 2), floor_y=63, top=75, outward="north")
        self.assertEqual(floors_behind(self.save, run), ())

    def test_a_row_too_near_the_floor_or_the_top_is_no_story(self):
        run = Wall((2, 2), (12, 2), floor_y=63, top=75, outward="north")
        for y in (65, 74):
            self.save.fill((2, y, 3), (12, y, 3), STONE)
        self.assertEqual(floors_behind(self.save, run), ())
        self.save.fill((2, 69, 3), (12, 69, 3), STONE)
        self.assertEqual(floors_behind(self.save, run), (69,))

    def test_it_takes_a_wall(self):
        with self.assertRaises(NBTError):
            floors_behind(self.save, "a wall")


class ReadTests(ReadBase):
    def test_every_face_reads_back_to_what_was_laid(self):
        for face, wall_ in self.office().items():
            rule = Elevation.read(self.save, wall_)
            self.assertEqual(names(rule.plan(wall_)),
                             names(OFFICE_FACES[face].plan(wall_)), face)
            self.assertEqual(rule.verify(self.save, wall_), [], face)

    def test_it_finds_the_bands(self):
        wall_ = self.office()["south"]
        rule = Elevation.read(self.save, wall_)
        self.assertEqual([band.rows for band in rule.bands],
                         [5, 1, None, 4])

    def test_it_finds_the_rhythm(self):
        rule = Elevation.read(self.save, self.office()["south"])
        shaft = rule.bands[2].rhythm
        self.assertEqual([width for _, width in shaft.runs],
                         [1, 7, 1, 4, 1, 4, 1, 3, 1, 4, 1, 4, 1, 7, 1])
        self.assertTrue(shaft.symmetric)
        ground = rule.bands[0].rhythm
        self.assertEqual([i for i, role in enumerate(ground.columns)
                          if ground.columns.count(role) < 3],
                         [18, 22], "the fins, each a role of their own")

    def test_it_finds_the_windows(self):
        rule = Elevation.read(self.save, self.office()["south"])
        glass = [value for value in rule.bands[2].roles.values()
                 if isinstance(value, Glazing)]
        self.assertEqual(
            sorted((str(g.spandrel), tuple(r.name for r in g.rows)) for g in glass),
            [("None", (GRAY, GRAY, LIGHT, LIGHT)),
             (BRICK, (GRAY, LIGHT))])

    def test_it_takes_what_a_block_update_derives_out_of_a_pane(self):
        """Joined, a pane says its sides; read, it is its id."""
        wall_ = self.office()["south"]
        rule = Elevation.read(self.save, wall_)
        glass = next(value for value in rule.bands[2].roles.values()
                     if isinstance(value, Glazing))
        self.assertEqual(glass.rows[0], BlockState(GRAY))

    def test_a_recess_is_read_and_the_furniture_is_not(self):
        wall_ = self.office()["south"]
        plan = Elevation.read(self.save, wall_).plan(wall_)
        self.assertEqual(plan[wall_.cell(7, 41, 1)].name, GRAY)
        layer = [cell for cell in plan if cell[2] == wall_.at - 1]
        self.assertTrue(layer)
        self.assertLess(max(cell[1] for cell in layer), 46,
                        "only the ground story is read past the wall")

    def test_depth_is_how_far_it_may_look(self):
        wall_ = self.office()["south"]
        shallow = Elevation.read(self.save, wall_, depth=1).plan(wall_)
        self.assertFalse([cell for cell in shallow if cell[2] != wall_.at])
        deep = Elevation.read(self.save, wall_, depth=4).plan(wall_)
        self.assertFalse([cell for cell in deep if cell[2] < wall_.at - 1],
                         "it stops where every open column has met something")

    def test_the_doors_read_as_doors(self):
        wall_ = self.office()["north"]
        rule = Elevation.read(self.save, wall_)
        band = rule.bands[0]
        self.assertEqual(band.rows, 2)
        hung = band.roles["doors"]
        self.assertIsInstance(hung, Layers)
        self.assertIsInstance(hung.values[1], Doors)
        self.assertEqual(hung.values[1].door, BlockState(OAK_DOOR))

    def test_an_opened_door_reads_the_same(self):
        wall_ = self.office()["north"]
        for y in (41, 42):
            door = self.save.get_block(25, y, 5)
            self.save.set_block(25, y, 5, door.with_properties(open="true"))
        rule = Elevation.read(self.save, wall_)
        self.assertIsInstance(rule.bands[0].roles["doors"].values[1], Doors)

    def lone_doors(self, hinges):
        """A wall with doors standing in it, the hinges given."""
        self.save.fill((0, 63, 3), (9, 63, 3), STONE)
        run = Wall((0, 3), (9, 3), floor_y=63, top=66, outward="north")
        self.save.fill((0, 64, 3), (9, 66, 3), STONE)
        for x, hinge in enumerate(hinges, start=2):
            for y, half in ((64, "lower"), (65, "upper")):
                self.save.set_block(x, y, 3, BlockState(OAK_DOOR, {
                    "facing": "south", "half": half, "hinge": hinge}))
        return run

    def test_doors_hung_another_way_keep_their_states(self):
        run = self.lone_doors(["left", "right"])     # hinged at the middle
        rule = Elevation.read(self.save, run)
        self.assertFalse(any(isinstance(value, Doors)
                             for band in rule.bands
                             for value in band.roles.values()))
        self.assertEqual(rule.verify(self.save, run), [])

    def test_doors_as_doors_would_hang_are_doors(self):
        run = self.lone_doors(["right", "left"])
        rule = Elevation.read(self.save, run)
        self.assertTrue(any(isinstance(value, Doors)
                            for band in rule.bands
                            for value in band.roles.values()))

    def test_an_iron_door_keeps_its_states(self):
        self.save.fill((0, 63, 3), (9, 63, 3), STONE)
        self.save.fill((0, 64, 3), (9, 66, 3), STONE)
        run = Wall((0, 3), (9, 3), floor_y=63, top=66, outward="north")
        for y, half in ((64, "lower"), (65, "upper")):
            self.save.set_block(4, y, 3, BlockState(
                "minecraft:iron_door", {"facing": "south", "half": half}))
        rule = Elevation.read(self.save, run)
        self.assertEqual(rule.verify(self.save, run), [])

    def test_the_longest_repeat_stretches(self):
        wall_ = self.office()["south"]
        rule = Elevation.read(self.save, wall_)
        taller = Wall.ring(4, 4, 44, 26, **office_stories(12, 40))["south"]
        plan = rule.plan(taller)
        self.assertEqual(names(plan), names(OFFICE_SOUTH.plan(taller)))

    def test_it_can_be_told_not_to(self):
        wall_ = self.office()["south"]
        rule = Elevation.read(self.save, wall_, stretch=False)
        self.assertEqual(rule.verify(self.save, wall_), [])
        self.assertTrue(all(band.rows for band in rule.bands))
        taller = Wall.ring(4, 4, 44, 26, **office_stories(12, 40))["south"]
        with self.assertRaises(NBTError):
            rule.plan(taller)

    def test_a_building_read_stands_again_taller(self):
        """Read a wall, and lay what came back somewhere else, higher."""
        wall_ = self.office()["south"]
        rule = Elevation.read(self.save, wall_)
        again = Wall((4, 45), (44, 45), outward="south",
                     **office_stories(2, 40))
        self.save.fill((4, 40, 44), (44, 40, 46), STONE)
        self.b.wall(again, rule)
        self.assertEqual(rule.verify(self.save, again), [])

    def test_a_block_out_of_place_is_reproduced_not_hidden(self):
        """The read answers for what stands, so a fault comes back as more
        rule, which is how a reader sees it."""
        wall_ = self.office()["south"]
        plain = Elevation.read(self.save, wall_)
        self.save.set_block(14, 50, wall_.at, "minecraft:dirt")
        faulted = Elevation.read(self.save, wall_)
        self.assertEqual(faulted.verify(self.save, wall_), [])
        self.assertGreater(len(faulted.bands), len(plain.bands))

    def test_a_wall_with_no_rule_comes_back_as_one_and_says_so(self):
        mixed = Mix([(BRICK, 1), (STONE, 1), ("minecraft:dirt", 1)], seed=3)
        run = Wall((0, 0), (20, 0), floor_y=63, top=75, outward="north",
                   stories=(68,))
        self.b.wall(run, Elevation(Band(None, Rhythm(("wall", 21)),
                                        {"wall": mixed})))
        rule = Elevation.read(self.save, run)
        self.assertEqual(rule.verify(self.save, run), [])
        self.assertGreater(sum(len(band.roles) for band in rule.bands), 8)

    def test_a_facade_reads_as_its_piers_and_bays(self):
        run = Wall((0, 0), (12, 0), floor_y=63, top=70, outward="north")
        self.b.wall(run, Facade(STONE, pier=BRICK, pitch=4, phase=0))
        rule = Elevation.read(self.save, run, stretch=False)
        self.assertEqual(len(rule.bands), 1)
        self.assertEqual(rule.bands[0].rhythm,
                         Rhythm("bricks", ("stone_bricks", 3)) * 3 + "bricks")

    def test_a_thick_wall_reads_each_of_its_layers(self):
        run = Wall((0, 0), (8, 0), floor_y=63, top=66, outward="north",
                   thickness=2)
        rule = Elevation(Band(None, ONE, {"wall": Layers(BRICK, STONE)}))
        self.b.wall(run, rule)
        read = Elevation.read(self.save, run)
        self.assertEqual(read.verify(self.save, run), [])
        self.assertEqual(names(read.plan(run)), names(rule.plan(run)))

    def test_a_wall_with_no_stories_still_reproduces(self):
        wall_ = self.office()["south"]
        bare = Wall((4, 26), (44, 26), floor_y=40, top=62, outward="south")
        self.assertEqual(wall_.at, bare.at)
        rule = Elevation.read(self.save, bare)
        self.assertEqual(rule.verify(self.save, bare), [])

    def test_a_cell_it_cannot_read_is_refused(self):
        with self.assertRaises(NBTError) as caught:
            Elevation.read(self.save, Wall((90, 0), (120, 0), floor_y=63,
                                           top=66, outward="north"))
        self.assertIn("nothing to read", str(caught.exception))

    def test_a_rule_that_does_not_replay_is_refused(self):
        """The replay is the tool: what it reports is what comes back."""
        wall_ = self.office()["south"]
        with mock.patch.object(Elevation, "verify", return_value=["a fault"]):
            with self.assertRaises(Refused) as caught:
                Elevation.read(self.save, wall_)
        self.assertIn("a fault", str(caught.exception))
        self.assertEqual(caught.exception.faults, ["a fault"])

    def test_it_takes_a_wall(self):
        with self.assertRaises(NBTError):
            Elevation.read(self.save, "a wall")

    def test_describe_says_the_rule_in_words(self):
        said = Elevation.read(self.save, self.office()["north"]).describe()
        self.assertIn("the rest of the wall, 41 columns, mirrored", said)
        self.assertIn("1 row, 41 columns, mirrored: bricks 41", said)
        self.assertIn("doors of oak_door", said)
        self.assertIn("over bricks", said)
        self.assertNotIn("minecraft:", said)

    def test_a_rule_written_by_hand_says_itself_too(self):
        self.assertIn("air, then gray_stained_glass_pane",
                      Elevation(Band(2, Rhythm(("bay", 9)), {
                          "bay": Layers(AIR, GRAY)})).describe())


class CommandLineTests(ReadBase):
    def run_it(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["elevation", self.dir, *arguments])
        return code, out.getvalue(), err.getvalue()

    def test_it_finds_the_stories_and_says_each_wall(self):
        self.office()
        self.save.save()
        code, out, _ = self.run_it("--box", "4", "4", "44", "26", "--floor", "40",
                                   "--top", "62")
        self.assertEqual(code, 0)
        self.assertIn("stories at 46, 50, 54, 58 (read from", out)
        for face in ("north", "east", "south", "west"):
            self.assertIn(f"{face} wall", out)
        self.assertEqual(out.count("reproduced"), 4)

    def test_stories_can_be_given(self):
        self.office()
        self.save.save()
        code, out, _ = self.run_it(
            "--box", "4", "4", "44", "26", "--floor", "40", "--top", "62",
            "--stories", "46", "50", "54", "58")
        self.assertEqual(code, 0)
        self.assertIn("(as given)", out)

    def test_a_refusal_exits_2_and_says_why(self):
        self.save.save()
        code, _, err = self.run_it("--box", "90", "90", "120", "120", "--floor",
                                   "40", "--top", "62")
        self.assertEqual(code, 2)
        self.assertIn("nothing to read", err)


class RenderElevationTests(ReadBase):
    """A box as you see it from one side."""

    RED, BLUE = "minecraft:red_wool", "minecraft:blue_wool"

    def picture(self, side, corner1=(0, 64, 0), corner2=(4, 66, 4), **options):
        path = os.path.join(self.dir, f"{side}.png")
        render_elevation(self.save, side, corner1, corner2, path, **options)
        return read_png(path)

    def row(self, png, y=0):
        """A row of pixels as ``(r, g, b)``."""
        line = png["rows"][y]
        return [tuple(line[i:i + 3]) for i in range(0, len(line), 3)]

    def test_each_side_is_as_you_would_see_it(self):
        """A red west end and a blue east one: west is on your left looking
        north, and on your right looking south. The north side is the
        mirror of the south, and the same for east and west."""
        self.save.fill((0, 64, 0), (0, 66, 4), self.RED)
        self.save.fill((4, 64, 0), (4, 66, 4), self.BLUE)
        self.save.fill((1, 64, 2), (3, 66, 2), BRICK)
        from_south, from_north = self.picture("south"), self.picture("north")
        self.assertEqual(self.row(from_south), self.row(from_north)[::-1])
        red = self.row(from_south)[0]
        self.assertGreater(red[0], red[2], "west on the left, from the south")
        self.assertLess(self.row(from_north)[0][0], self.row(from_north)[0][2])
        self.save.fill((8, 64, 0), (12, 66, 0), self.RED)
        self.save.fill((8, 64, 4), (12, 66, 4), self.BLUE)
        self.save.fill((10, 64, 1), (10, 66, 3), BRICK)
        box = ((8, 64, 0), (12, 66, 4))
        from_east, from_west = self.picture("east", *box), self.picture("west", *box)
        self.assertEqual(self.row(from_east), self.row(from_west)[::-1])
        self.assertGreater(self.row(from_west)[0][0], self.row(from_west)[0][2],
                           "north on the left, from the west")

    def test_the_top_of_the_box_is_the_top_of_the_picture(self):
        self.save.fill((0, 66, 2), (4, 66, 2), self.RED)
        self.save.fill((0, 64, 2), (4, 64, 2), self.BLUE)
        png = self.picture("south")
        self.assertGreater(self.row(png, 0)[0][0], self.row(png, 0)[0][2])
        self.assertLess(self.row(png, 2)[0][0], self.row(png, 2)[0][2])

    def test_the_first_thing_met_hides_what_stands_behind_it(self):
        self.save.fill((0, 64, 0), (4, 66, 0), self.BLUE)
        self.save.fill((0, 64, 3), (4, 66, 3), self.RED)
        self.assertGreater(self.row(self.picture("south"))[0][0], 100)

    def test_a_recess_is_darker_than_the_plane_it_stands_behind(self):
        self.save.fill((0, 64, 3), (4, 66, 3), BRICK)
        self.save.fill((2, 64, 3), (2, 66, 3), AIR)
        self.save.fill((2, 64, 1), (2, 66, 1), BRICK)
        flat = self.row(self.picture("south", shade=False))
        self.assertEqual(flat[1], flat[2])
        shaded = self.row(self.picture("south"))
        self.assertEqual(shaded[1], flat[1])
        self.assertLess(shaded[2], shaded[1])

    def test_what_stands_in_front_of_the_wall_does_not_shade_it(self):
        """The plaza's edge is nearer than the wall, and most of the picture
        is the wall."""
        self.save.fill((0, 64, 1), (4, 66, 1), BRICK)
        self.save.fill((0, 64, 4), (0, 66, 4), BRICK)
        plain = self.row(self.picture("south", shade=False))
        self.assertEqual(self.row(self.picture("south"))[1:], plain[1:])

    def test_nothing_met_is_sky_and_no_ground_is_black(self):
        sky = self.row(self.picture("south"))[0]
        self.assertGreater(sky[2], sky[0])
        black = self.row(self.picture("south", (140, 64, 0), (144, 66, 4)))[0]
        self.assertLess(max(black), 40)

    def test_scale_repeats_pixels(self):
        png = self.picture("south", scale=3)
        self.assertEqual((png["width"], png["height"]), (15, 9))

    def test_a_side_is_a_compass_word(self):
        with self.assertRaises(ValueError):
            self.picture("up")

    def test_the_command_line_draws_it(self):
        self.save.fill((0, 64, 0), (4, 66, 4), BRICK)
        self.save.save()
        path = os.path.join(self.dir, "out.png")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["render", self.dir, "--elevation", "south", "0", "64",
                         "0", "4", "66", "4", "-o", path])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(path))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(["render", self.dir, "--elevation", "up", "0", "64",
                         "0", "4", "66", "4", "-o", path])
        self.assertEqual(code, 2)
        self.assertIn("seen from", err.getvalue())


if __name__ == "__main__":
    unittest.main()
