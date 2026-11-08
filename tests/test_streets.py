"""Tests for craft.build's street networks.

``Builder.road`` lays one road and knows of no other. Laid as a grid over
CastleWorld's terraces (2026-11-01), six of twenty-five crossings disagreed by a
block or two, the second road cut a step of up to five blocks into the first,
and every rise was a jump, so a walker who would not jump reached 71% of it. A
network is laid whole: crossings first, half-block ramps between them, and a
walk over all of it before anything is written.
"""

from __future__ import annotations

import collections
import os
import random
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from heaton_voxel.craft import (  # noqa: E402
    Material,
    NO_SPAWNS,
    simulate_block_light,
    Street,
    StreetNetwork,
    StreetSection,
)
from heaton_voxel.craft.build import Builder  # noqa: E402
from heaton_voxel.craft.build.streets import _climb, _laid, _profile  # noqa: E402
from heaton_voxel.craft.errors import IllegalBlock, NoSurface, Refused  # noqa: E402
from heaton_voxel.nbt import NBTError  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402
from tests.test_world import make_save  # noqa: E402

STONE = "minecraft:stone"
GROUND = 63
ROAD = "minecraft:polished_andesite"
ROAD_SLAB = "minecraft:polished_andesite_slab"
WALK = "minecraft:smooth_stone"
WALK_SLAB = "minecraft:smooth_stone_slab"
SECTION = StreetSection(5, 2)


def hills(save):
    """Ground that climbs a block every six columns along x and z."""
    for x in range(-64, 64):
        for z in range(-64, 64):
            rise = (x + 64) // 6 + (z + 64) // 6
            if rise:
                save.fill((x, GROUND + 1, z), (x, GROUND + rise, z), STONE)


class World(unittest.TestCase):
    """A world of stone ten chunks square, flat or as ``SHAPE`` raises it,
    built once and copied."""

    SHAPE = None
    RADIUS = 4

    @classmethod
    def setUpClass(cls):
        cls.pristine = tempfile.mkdtemp(prefix="streets-tests-")
        chunks = [(x, z) for x in range(-cls.RADIUS, cls.RADIUS)
                  for z in range(-cls.RADIUS, cls.RADIUS)]
        make_save(cls.pristine, chunks)
        save = MinecraftSave.load(cls.pristine)
        reach = cls.RADIUS * 16
        save.fill((-reach, 40, -reach), (reach - 1, GROUND, reach - 1), STONE)
        if cls.SHAPE is not None:
            cls.SHAPE(save)
        save.rebuild_heightmaps((-reach, 0, -reach), (reach - 1, 0, reach - 1))
        save.save()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.pristine, ignore_errors=True)

    def setUp(self):
        directory = tempfile.mkdtemp(prefix="streets-world-")
        os.rmdir(directory)
        shutil.copytree(self.pristine, directory)
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        self.save = MinecraftSave.load(directory)
        self.b = Builder(self.save, seed=7)

    def name(self, x, y, z):
        return self.save.get_block(x, y, z).name


class ProfileTests(unittest.TestCase):
    def test_it_holds_to_every_rule_on_any_ground(self):
        rng = random.Random(3)
        for _ in range(400):
            run = rng.randint(1, 3)
            rows = rng.randint(0, 40)
            height = rng.randint(100, 200)
            targets = []
            for _ in range(rows):
                height += rng.randint(-3, 3)
                targets.append(height)
            start = rng.randint(120, 180)
            room = _climb(rows + 1, 0, run)
            end = start + rng.randint(-room, room)
            tops = _profile(targets, start, end, run)
            self.assertEqual(len(tops), rows)
            path = [start, *tops, end]
            changes = [i for i, (a, b) in enumerate(zip(path, path[1:]))
                       if a != b]
            self.assertLessEqual(
                max((abs(a - b) for a, b in zip(path, path[1:])), default=0), 1)
            for a, b in zip(changes, changes[1:]):
                self.assertGreaterEqual(b - a, run, path)

    def test_flat_ground_stays_flat(self):
        self.assertEqual(_profile([130] * 9, 130, 130, 2), [130] * 9)

    def test_it_follows_the_ground_when_it_can(self):
        wanted = [130 + i // 2 for i in range(20)]
        tops = _profile(wanted, 130, 140, 2)
        self.assertLessEqual(sum(abs(a - b) for a, b in zip(tops, wanted)), 12)

    def test_it_is_the_cheapest_there_is(self):
        # Every profile of five rows, by hand, against the solver's: a half
        # block of earth is worth two changes of level.
        rng = random.Random(9)

        def cost(path, wanted):
            earth = sum(abs(a - b) for a, b in zip(path[1:-1], wanted))
            changes = sum(a != b for a, b in zip(path, path[1:]))
            return 2 * earth + changes

        def legal(path, run):
            last = -run
            for i, (a, b) in enumerate(zip(path, path[1:])):
                if abs(a - b) > 1:
                    return False
                if a != b:
                    if i - last < run:
                        return False
                    last = i
            return True

        for _ in range(60):
            run = rng.randint(1, 2)
            wanted = [rng.randint(0, 4) for _ in range(5)]
            start, end = rng.randint(0, 2), rng.randint(0, 2)
            best = None
            for code in range(3 ** 6):
                path = [start]
                for i in range(5):
                    path.append(path[-1] + code // 3 ** i % 3 - 1)
                path.append(end)
                if legal(path, run):
                    price = cost(path, wanted)
                    best = price if best is None else min(best, price)
            tops = _profile(wanted, start, end, run)
            self.assertEqual(cost([start, *tops, end], wanted), best)

    def test_ends_too_far_apart_are_refused(self):
        with self.assertRaises(NBTError):
            _profile([130] * 3, 130, 150, 1)

    def test_a_surface_is_a_block_or_a_slab(self):
        self.assertEqual(_laid(2 * 64 + 2), (64, "block"))
        self.assertEqual(_laid(2 * 64 + 1), (64, "slab"))


class SectionTests(unittest.TestCase):
    def test_what_is_no_section_is_refused(self):
        for args, kept, words in (((6,), {}, "odd number"),
                                  ((0,), {}, "odd number"),
                                  ((5, -1), {}, "no columns or more")):
            with self.subTest(args=args):
                with self.assertRaises(NBTError) as caught:
                    StreetSection(*args, **kept)
                self.assertIn(words, str(caught.exception))

    def test_a_block_with_no_slab_is_refused(self):
        with self.assertRaises(IllegalBlock):
            StreetSection(surface="minecraft:gold_block")

    def test_a_section_says_what_it_reaches(self):
        section = StreetSection(7, 2)
        self.assertEqual((section.half, section.reach, section.curb), (3, 5, 1))
        self.assertEqual(StreetSection(raised=False).curb, 0)
        self.assertEqual(section.state("road", 130).name, ROAD)
        self.assertEqual(section.state("road", 131).name, ROAD_SLAB)
        self.assertEqual(section.state("walk", 131).properties["type"],
                         "bottom")


class NetworkTests(unittest.TestCase):
    def test_it_finds_its_own_crossings(self):
        net = StreetNetwork(SECTION, ((0, 0), (60, 0)), ((30, -30), (30, 30)))
        self.assertIn((30, 0), net.nodes)
        self.assertEqual(len(net.nodes), 5)
        self.assertEqual(len(net.edges), 4)

    def test_a_street_meeting_another_is_a_tee(self):
        net = StreetNetwork(SECTION, ((0, 0), (60, 0)), ((30, 0), (30, 30)))
        self.assertIn((30, 0), net.nodes)
        self.assertEqual(len(net.edges), 3)

    def test_a_bend_is_a_node(self):
        net = StreetNetwork(SECTION, ((0, 0), (40, 0), (40, 40)))
        self.assertEqual(sorted(net.nodes), [(0, 0), (40, 0), (40, 40)])

    def test_a_grid_is_every_crossing(self):
        net = StreetNetwork.grid((0, 40, 80), (0, 40), SECTION)
        self.assertEqual(len(net.nodes), 6)
        self.assertEqual(len(net.edges), 7)

    def test_a_grid_of_one_street_stands(self):
        net = StreetNetwork.grid((0, 40), (7,), SECTION)
        self.assertEqual(len(net.edges), 1)

    def test_a_crossing_is_road_through_and_pavement_at_its_corners(self):
        net = StreetNetwork(SECTION, ((0, 0), (60, 0)), ((30, -30), (30, 30)))
        cells = net.nodes[(30, 0)].cells()
        self.assertEqual(cells[(30, 0)], "road")
        self.assertEqual(cells[(34, 0)], "road")
        self.assertEqual(cells[(30, -4)], "road")
        self.assertEqual(cells[(34, 4)], "walk")

    def test_a_street_end_has_pavement_round_it(self):
        net = StreetNetwork(SECTION, ((0, 0), (60, 0)))
        cells = net.nodes[(0, 0)].cells()
        self.assertEqual(cells[(4, 0)], "road")
        self.assertEqual(cells[(-4, 0)], "walk")

    def test_every_column_is_laid_once(self):
        net = StreetNetwork.grid((0, 40, 80), (0, 40, 80), SECTION)
        rows = sum(len(edge.rows) for edge in net.edges)
        self.assertEqual(len(net.columns), 9 * 9 ** 2 + rows * 9)

    def test_streets_of_two_sections_meet(self):
        wide = StreetSection(9, 3)
        net = StreetNetwork(SECTION, Street((0, 0), (80, 0), section=wide),
                            ((40, -40), (40, 40)))
        node = net.nodes[(40, 0)]
        self.assertEqual((node.reach_z, node.reach_x), (7, 4))
        self.assertEqual(node.cells()[(40, 4)], "road")

    def test_what_cannot_stand_is_refused(self):
        for streets, words in (
                ((((0, 0), (40, 40)),), "diagonal"),
                ((((0, 0), (0, 0)),), "follows itself"),
                ((((0, 0),),), "two points"),
                ((((0, 0), (50, 0)), ((20, 0), (70, 0))), "along one another"),
                ((((0, 0), (5, 0)),), "overlap"),
                ((((0, 0), (60, 0)), ((0, 6), (60, 6))), "two streets stand")):
            with self.subTest(streets=streets):
                with self.assertRaises(NBTError) as caught:
                    StreetNetwork(SECTION, *streets)
                self.assertIn(words, str(caught.exception))
        with self.assertRaises(NBTError):
            StreetNetwork(SECTION)
        with self.assertRaises(NBTError):
            StreetNetwork("wide", ((0, 0), (60, 0)))
        with self.assertRaises(NBTError) as caught:
            StreetNetwork(SECTION, ((0, 0), (60, 0)), pins={(5, 5): 64})
        self.assertIn("is none", str(caught.exception))


class LevelTests(unittest.TestCase):
    NET = StreetNetwork(SECTION, ((0, 0), (100, 0), (200, 0)))

    def test_heights_stay_within_what_the_streets_can_climb(self):
        rng = random.Random(5)
        net = StreetNetwork.grid((0, 40, 80, 120), (0, 50, 100), SECTION)
        for _ in range(200):
            ground = {key: rng.randint(50, 110) for key in net.nodes}
            run = rng.randint(1, 4)
            levels = net.levels(ground, run)
            for (a, b), blocks in net.climbs(run).items():
                self.assertLessEqual(abs(levels[a] - levels[b]), blocks)

    def test_ground_a_street_can_climb_is_left_alone(self):
        levels = self.NET.levels({(0, 0): 64, (100, 0): 70, (200, 0): 66}, 2)
        self.assertEqual(levels, {(0, 0): 64, (100, 0): 70, (200, 0): 66})

    def test_no_node_moves_further_than_it_has_to(self):
        short = StreetNetwork(SECTION, ((0, 0), (30, 0)))
        levels = short.levels({(0, 0): 60, (30, 0): 100}, 2)
        climb = short.climbs(2)[((0, 0), (30, 0))]
        self.assertEqual(levels[(30, 0)] - levels[(0, 0)], climb)
        self.assertLess(abs((60 - levels[(0, 0)])
                            - (levels[(30, 0)] - 100)), 2)

    def test_a_pin_holds(self):
        net = StreetNetwork(SECTION, ((0, 0), (30, 0)), pins={(0, 0): 70})
        levels = net.levels({(0, 0): 60, (30, 0): 100}, 2)
        self.assertEqual(levels[(0, 0)], 70)

    def test_pins_too_far_apart_are_refused(self):
        net = StreetNetwork(SECTION, ((0, 0), (30, 0)),
                            pins={(0, 0): 60, (30, 0): 100})
        with self.assertRaises(Refused):
            net.levels({(0, 0): 60, (30, 0): 100}, 2)


class FlatTests(World):
    def net(self):
        return StreetNetwork(SECTION, ((-40, 0), (40, 0)), ((0, -40), (0, 40)),
                             name="main")

    def test_it_lays_a_road_and_a_curb(self):
        result = self.b.streets(self.net())
        self.assertEqual(self.name(20, GROUND, 0), ROAD)
        self.assertEqual(self.name(20, GROUND, 2), ROAD)
        self.assertEqual(self.name(20, GROUND + 1, 3), WALK_SLAB)
        self.assertEqual(self.name(20, GROUND + 1, 4), WALK_SLAB)
        self.assertEqual(self.name(20, GROUND + 1, 5), "minecraft:air")
        self.assertEqual(result.top(20, 0), GROUND + 1)
        self.assertEqual(result.top(20, 3), GROUND + 1.5)
        self.assertEqual(result.level(20, 3), GROUND + 1)
        self.assertEqual(result.heights[(0, 0)], GROUND)
        self.assertEqual(result.slabs, len(
            [role for _, role in self.net().columns.values()
             if role == "walk"]))

    def test_a_crossing_is_road_through_with_its_corners_paved(self):
        self.b.streets(self.net())
        self.assertEqual(self.name(0, GROUND, 0), ROAD)
        self.assertEqual(self.name(4, GROUND, 0), ROAD)
        self.assertEqual(self.name(4, GROUND + 1, 4), WALK_SLAB)

    def test_a_flat_curb_needs_no_raised_slab(self):
        section = StreetSection(5, 2, raised=False)
        net = StreetNetwork(section, ((-40, 0), (40, 0)))
        self.b.streets(net)
        self.assertEqual(self.name(20, GROUND, 3), WALK)

    def test_it_says_what_it_laid(self):
        result = self.b.streets(self.net())
        self.assertIn("4 streets", repr(result))
        self.assertEqual(len(result.streets), 4)
        self.assertIn("LaidStreet", repr(result.streets[0]))
        self.assertEqual(len(result.streets[0].rows),
                         len(result.streets[0].tops))

    def test_it_is_a_keep_out_and_a_path_when_it_is_laid(self):
        self.b.streets(self.net())
        with self.assertRaises(Refused) as caught:
            self.b.road([(20, -20), (20, 20)], width=3, mix=ROAD)
        self.assertIn("main-", str(caught.exception))
        self.assertIn((20, 0), self.b._paved)

    def test_a_name_used_twice_is_refused(self):
        self.b.streets(self.net())
        with self.assertRaises(NBTError) as caught:
            self.b.streets(self.net())
        self.assertIn("give the network a name", str(caught.exception))

    def test_it_keeps_out_of_what_is_registered(self):
        self.b.reserve(10, -20, 14, 20, name="grove")
        with self.assertRaises(Refused) as caught:
            self.b.streets(self.net())
        self.assertIn("grove", str(caught.exception))
        self.assertEqual(self.name(20, GROUND, 0), STONE)
        Builder(self.save, seed=7)
        self.b.streets(self.net(), through=("grove",))
        self.assertEqual(self.name(12, GROUND, 0), ROAD)

    def test_it_keeps_out_of_a_building_it_was_never_told_of(self):
        self.save.fill((18, 64, -2), (22, 70, 2), "minecraft:bricks")
        before = self.name(20, 66, 0)
        with self.assertRaises(Refused) as caught:
            self.b.streets(self.net())
        self.assertIn("somebody built", str(caught.exception))
        self.assertEqual(self.name(20, 66, 0), before)
        self.assertEqual(self.name(-20, GROUND, 0), STONE)
        self.b.streets(self.net(), overwrite=True, allow_dam=True)
        self.assertEqual(self.name(20, 66, 0), "minecraft:air")

    def test_a_chunk_that_is_not_there_writes_nothing(self):
        net = StreetNetwork(SECTION, ((0, 0), (90, 0)))
        with self.assertRaises(NoSurface):
            self.b.streets(net)
        self.assertEqual(self.name(10, GROUND, 0), STONE)

    def test_arguments_are_held_to(self):
        for kept in ({"run": 0}, {"clearance": 2}, {"rule": "steep"}):
            with self.subTest(kept=kept):
                with self.assertRaises(NBTError):
                    self.b.streets(self.net(), **kept)
        with self.assertRaises(NBTError):
            self.b.streets("broadway")

    def test_the_same_network_is_the_same_bytes(self):
        one = self.b.streets(self.net())
        directory = tempfile.mkdtemp(prefix="streets-again-")
        os.rmdir(directory)
        shutil.copytree(self.pristine, directory)
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        other = Builder(MinecraftSave.load(directory), seed=99)
        two = other.streets(self.net())
        self.assertEqual(one.tops, two.tops)

    def test_a_retaining_course_holds_a_fill_at_its_edge(self):
        self.save.fill((-40, GROUND - 3, -20), (40, GROUND, 20), "minecraft:air")
        self.save.rebuild_heightmaps((-48, 0, -32), (47, 0, 31))
        section = StreetSection(5, 1)
        net = StreetNetwork(section, ((-30, 0), (30, 0)), pins={(-30, 0): 63,
                                                                (30, 0): 63})
        result = self.b.streets(net, retain="minecraft:cobblestone",
                                allow_dam=True)
        y = result.level(-23, 3)
        self.assertEqual(self.name(-23, y - 1, 3), "minecraft:cobblestone")
        self.assertEqual(self.name(-23, result.level(-23, 0) - 1, 0),
                         "minecraft:dirt")


class HillTests(World):
    """Ground climbing a block every six columns each way, so every street
    has to climb and no two agree unless something makes them."""

    SHAPE = staticmethod(hills)

    def grid(self, **kept):
        return StreetNetwork.grid((-40, 0, 40), (-40, 0, 40), SECTION, **kept)

    def lay(self, **kept):
        return self.b.streets(self.grid(), allow_dam=True, **kept)

    def test_no_crossing_has_two_heights(self):
        result = self.lay()
        for (x, z), y in result.heights.items():
            for dx, dz in ((2, 0), (-2, 0), (0, 2), (0, -2), (0, 0)):
                self.assertEqual(self.name(x + dx, y, z + dz), ROAD)

    def test_every_step_along_a_street_is_half_a_block(self):
        result = self.lay()
        for street in result.streets:
            ends = [result.heights[street.points[0]] * 2 + 2, *street.tops,
                    result.heights[street.points[1]] * 2 + 2]
            self.assertLessEqual(max(abs(a - b)
                                     for a, b in zip(ends, ends[1:])), 1)

    def test_a_rise_is_laid_as_a_slab(self):
        result = self.lay()
        self.assertGreater(result.slabs, 100)
        street = max(result.streets, key=lambda s: len(set(s.tops)))
        odd = next(i for i, top in enumerate(street.tops) if top % 2)
        along = street.rows[odd]
        x, z = ((along, street.points[0][1]) if street.axis == "x"
                else (street.points[0][0], along))
        self.assertEqual(self.name(x, result.level(x, z), z), ROAD_SLAB)

    def test_the_whole_of_it_is_walked_both_ways_with_no_jump(self):
        result = self.lay()
        cells = {(x, result.level(x, z), z) for x, z in result.tops}
        self.assertEqual(cells - set(result.walk.reached), set())
        self.assertEqual(cells & set(result.walk.stranded), set())

    def test_a_gentle_grade_takes_more_rows_to_climb(self):
        for run in (1, 3):
            with self.subTest(run=run):
                result = Builder(self.save, seed=7).streets(
                    StreetNetwork.grid((-40, 0, 40), (-40, 0, 40), SECTION,
                                       name=f"run-{run}"),
                    allow_dam=True, run=run, overwrite=True)
                for street in result.streets:
                    changes = [i for i, (a, b) in enumerate(
                        zip(street.tops, street.tops[1:])) if a != b]
                    for a, b in zip(changes, changes[1:]):
                        self.assertGreaterEqual(b - a, run)

    def test_cuts_and_fills_are_counted(self):
        result = self.lay()
        self.assertGreater(result.cut + result.filled, 100)

    def test_a_ravine_is_a_dam_unless_it_says_so(self):
        self.save.fill((-3, 46, -48), (3, 90, 47), "minecraft:air")
        self.save.rebuild_heightmaps((-8, 0, -48), (8, 0, 47))
        net = StreetNetwork(SECTION, ((-30, 0), (30, 0)))
        with self.assertRaises(Refused) as caught:
            self.b.streets(net)
        self.assertIn("would dam", str(caught.exception))
        self.assertIn("blocks of fill", str(caught.exception))
        self.b.streets(net, allow_dam=True)

    def test_a_pin_meets_what_is_there(self):
        net = self.grid(pins={(-40, -40): 70})
        result = self.b.streets(net, allow_dam=True)
        self.assertEqual(result.heights[(-40, -40)], 70)


class WaterTests(World):
    @staticmethod
    def SHAPE(save):
        save.fill((-8, GROUND - 4, -30), (8, GROUND, 30), "minecraft:air")
        save.fill((-8, GROUND - 4, -30), (8, GROUND, 30),
                  "minecraft:water[level=0]")

    def net(self):
        return StreetNetwork(SECTION, ((-40, 0), (40, 0)))

    def test_water_is_refused_unless_it_says_what_to_do(self):
        with self.assertRaises(Refused) as caught:
            self.b.streets(self.net())
        self.assertIn("water", str(caught.exception))
        self.assertEqual(self.name(-30, GROUND, 0), STONE)

    def test_it_fills_from_the_bed_when_told(self):
        result = self.b.streets(self.net(), over_water="fill", allow_dam=True)
        y = result.level(0, 0)
        self.assertEqual(self.name(0, y, 0), ROAD)
        self.assertEqual(self.name(0, GROUND - 2, 0), "minecraft:dirt")

    def test_a_fill_across_water_is_a_dam(self):
        with self.assertRaises(Refused) as caught:
            self.b.streets(self.net(), over_water="fill")
        self.assertIn("dam", str(caught.exception))

    def test_it_bridges_when_told(self):
        result = self.b.streets(self.net(), over_water="bridge", allow_dam=True)
        self.assertEqual(self.name(0, result.level(0, 0), 0), ROAD)
        self.assertIn("water", self.name(0, GROUND, 0))


class LampTests(World):
    SHAPE = staticmethod(hills)
    RADIUS = 5
    LAMP = dict(post=("minecraft:stone_brick_wall",
                      "minecraft:stone_brick_wall"),
                lamp="minecraft:lantern", light_level=NO_SPAWNS)

    def test_no_post_stands_in_a_street_crossing_it(self):
        net = StreetNetwork.grid((-40, 0, 40), (-40, 0, 40), SECTION)
        result = self.b.streets(net, allow_dam=True)
        lit = self.b.street_lamps(result, **self.LAMP)
        posts = [post for each in lit for post in each.posts]
        self.assertGreater(len(posts), 4)
        for x, y, z in posts:
            self.assertEqual(net.columns[(x, z)][1], "walk", (x, z))
            self.assertIn("stone_brick_wall", self.name(x, y, z))
        report = self.b.finish()
        self.assertEqual(report.unsupported, [])

    def test_the_middle_of_every_crossing_is_lit(self):
        net = StreetNetwork.grid((-40, 0, 40), (-40, 0, 40), SECTION)
        result = self.b.streets(net, allow_dam=True)
        self.b.street_lamps(result, **self.LAMP)
        self.b.finish()
        for (x, z), y in result.heights.items():
            box = ((x - 16, y - 2, z - 16), (x + 16, y + 8, z + 16))
            light = simulate_block_light(self.save, *box)
            self.assertGreaterEqual(light.level(x, y + 1, z), 1, (x, z))

    def test_a_post_keeps_out_of_a_column_it_is_told_to(self):
        self.b.fill((-20, GROUND, -1), (20, GROUND, 1), ROAD)
        lit = self.b.line_lamps(
            ((-20, 0), (20, 0)), width=3, **self.LAMP,
            avoid=[(x, -2) for x in range(-30, 30)])
        self.assertTrue(lit.posts)
        self.assertTrue(all(z == 2 for _, _, z in lit.posts))

    def test_a_post_on_a_pavement_stands_on_a_whole_block(self):
        net = StreetNetwork(SECTION, ((-40, -30), (40, -30)))
        result = self.b.streets(net, allow_dam=True)
        lit = self.b.street_lamps(result, **self.LAMP)
        for x, y, z in lit[0].posts:
            self.assertNotIn("_slab", self.name(x, y - 1, z))
            self.assertNotEqual(self.name(x, y - 1, z), "minecraft:air")

    def test_street_lamps_takes_what_streets_returned(self):
        with self.assertRaises(NBTError):
            self.b.street_lamps("main", **self.LAMP)


class ScatterTests(World):
    def test_a_planting_keeps_off_a_street(self):
        net = StreetNetwork(SECTION, ((-40, 0), (40, 0)))
        self.b.streets(net)
        self.save.fill((-30, GROUND, -10), (30, GROUND, 10),
                       "minecraft:grass_block")
        counts = collections.Counter()
        for x in range(-30, 31):
            for z in range(-4, 5):
                counts[self.b._paved.__contains__((x, z))] += 1
        self.assertEqual(counts[False], 0)


if __name__ == "__main__":
    unittest.main()
