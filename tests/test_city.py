"""Tests for craft.build's cities: SiteMap, plan_city, Builder.city, and the
land writer they lean on, Builder.blend.

A city here is planned from what the land allows and laid from the streets out.
These hold what makes that a city rather than a pattern: streets that keep off
water and what stands, blocks the land decides the use of, every door level with
its pavement, and ground that comes down to a street instead of stopping at a
cliff. CastleWorld is the reference these came from (2026-11-01): fifty towers
round its lake, laid on a scratch copy, 429 by 557 columns in two and a half
minutes.
"""

from __future__ import annotations

import collections
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "examples"))

import city as city_example  # noqa: E402
from tower_styles import STYLES, ZONES  # noqa: E402
from heaton_voxel.craft import (  # noqa: E402
    CityPlan,
    plan_city,
    SiteMap,
    walk_surface,
    Worksite,
)
from heaton_voxel.craft.build import Builder  # noqa: E402
from heaton_voxel.craft.errors import Refused  # noqa: E402
from heaton_voxel.nbt import NBTError  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402
from tests.test_examples import read_png  # noqa: E402
from tests.test_world import make_save  # noqa: E402

STONE = "minecraft:stone"
SAND = "minecraft:sand"
BRICKS = "minecraft:bricks"
WATER = "minecraft:water[level=0]"
GROUND = 63
RADIUS = 12                  # chunks either way
REACH = RADIUS * 16


def land(save):
    """Flat stone, a pond in the north-west and a low hill in the south-east."""
    save.fill((-REACH, 40, -REACH), (REACH - 1, GROUND, REACH - 1), STONE)
    save.fill((-150, GROUND - 3, -150), (-100, GROUND, -100), "minecraft:air")
    save.fill((-150, GROUND - 3, -150), (-100, GROUND, -100), WATER)
    save.fill((100, GROUND + 1, 100), (150, GROUND + 4, 150), STONE)


def world_of(shape=land, radius=RADIUS):
    directory = tempfile.mkdtemp(prefix="city-tests-")
    make_save(directory, [(x, z) for x in range(-radius, radius)
                          for z in range(-radius, radius)])
    save = MinecraftSave.load(directory)
    shape(save)
    reach = radius * 16
    save.rebuild_heightmaps((-reach, 0, -reach), (reach - 1, 0, reach - 1))
    save.save()
    return directory


class Land(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pristine = world_of()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.pristine, ignore_errors=True)

    def setUp(self):
        directory = tempfile.mkdtemp(prefix="city-world-")
        os.rmdir(directory)
        shutil.copytree(self.pristine, directory)
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        self.save = MinecraftSave.load(directory)
        self.b = Builder(self.save, seed=7)

    def y(self, x, z):
        return walk_surface(self.save, x, z).y

    def name(self, x, y, z):
        return self.save.get_block(x, y, z).name


class BlendTests(Land):
    def pit(self):
        """A plaza's edge: stone cut down to y=55, x 0..9."""
        self.save.fill((0, 56, -10), (9, GROUND, 10), "minecraft:air")
        self.save.rebuild_heightmaps((-16, 0, -16), (15, 0, 15))

    def test_a_cliff_comes_down_a_block_a_column(self):
        self.pit()
        columns = [(x, z) for x in range(10, 30) for z in range(-10, 11)]
        result = self.b.blend(columns)
        heights = [self.b.ground(x, 0) for x in range(10, 22)]
        self.assertEqual(heights[:3], [56, 57, 58])
        self.assertLessEqual(max(abs(a - b) for a, b in zip(heights,
                                                            heights[1:])), 1)
        self.assertGreater(result.lowered, 0)
        self.assertEqual(result.raised, 0)

    def test_a_gentler_run_takes_more_columns(self):
        self.pit()
        columns = [(x, z) for x in range(10, 40) for z in range(-10, 11)]
        self.b.blend(columns, run=2)
        heights = [self.b.ground(x, 0) for x in range(10, 24)]
        self.assertLess(heights[0], 58)
        self.assertLessEqual(heights[12] - heights[0], 7)
        self.assertLessEqual(max(abs(a - b) for a, b in zip(heights,
                                                            heights[1:])), 1)

    def test_ground_within_the_slope_stays_where_it_is(self):
        columns = [(x, z) for x in range(0, 10) for z in range(0, 10)]
        result = self.b.blend(columns)
        self.assertEqual(result.left, len(columns))
        self.assertEqual(self.y(5, 5), GROUND)

    def test_it_keeps_its_surface(self):
        self.save.fill((0, GROUND, -10), (30, GROUND, 10), SAND)
        self.pit()
        self.save.fill((10, GROUND, -10), (30, GROUND, 10), SAND)
        self.b.blend([(x, z) for x in range(10, 30) for z in range(-10, 11)])
        self.assertEqual(self.name(12, 58, 0), SAND)

    def test_what_somebody_built_stays_and_is_met(self):
        self.pit()
        self.save.fill((12, 60, -2), (12, 70, 2), BRICKS)
        columns = [(x, z) for x in range(10, 30) for z in range(-10, 11)]
        self.b.blend(columns)
        self.assertEqual(self.name(12, 65, 0), BRICKS)

    def test_a_cut_beside_water_stops_at_the_waterline(self):
        columns = [(x, z) for x in range(-99, -80) for z in range(-130, -120)]
        self.save.fill((-99, GROUND + 1, -130), (-90, GROUND + 6, -120), STONE)
        self.save.rebuild_heightmaps((-110, 0, -140), (-80, 0, -110))
        self.b.blend(columns)
        self.assertGreaterEqual(self.b.ground(-99, -125), GROUND)
        self.assertLess(self.b.ground(-97, -125), GROUND + 6)

    def test_heights_no_slope_can_join_meet_halfway(self):
        self.save.fill((10, 56, -10), (10, GROUND, 10), "minecraft:air")
        self.save.fill((12, GROUND + 1, -10), (12, GROUND + 9, 10), BRICKS)
        self.save.rebuild_heightmaps((-16, 0, -16), (15, 0, 15))
        result = self.b.blend([(11, z) for z in range(-5, 6)])
        self.assertGreater(result.steep, 0)

    def test_a_bad_run_is_refused(self):
        for run in (0, True, 1.5):
            with self.assertRaises(NBTError):
                self.b.blend([(0, 0)], run=run)

    def test_it_says_what_it_did(self):
        self.pit()
        result = self.b.blend([(x, 0) for x in range(10, 14)])
        self.assertIn("lowered", repr(result))
        self.assertEqual(result.columns, 4)


class SiteMapTests(Land):
    def test_it_reads_ground_water_and_what_is_built(self):
        self.save.fill((60, 64, 60), (72, 70, 72), BRICKS)
        self.save.rebuild_heightmaps((48, 0, 48), (79, 0, 79))
        site = SiteMap.read(self.save, (0, 0), 180, step=4)
        self.assertEqual(site.heights[(0, 0)], GROUND)
        self.assertIn((-120, -120), site.wet)
        self.assertIn((64, 64), site.built)
        self.assertFalse(site.absent)
        self.assertIn("sampled", repr(site))

    def test_it_reads_a_worksite_as_built(self):
        Worksite.open(self.save.path, (20, 20), (40, 40), name="old",
                      directory=None).accept()
        site = SiteMap.read(self.save, (0, 0), 60, step=4)
        self.assertIn((32, 32), site.built)

    def test_keep_out_is_read_as_built(self):
        site = SiteMap.read(self.save, (0, 0), 60, step=4,
                            keep_out=[(0, 0, 8, 8)])
        self.assertIn((4, 4), site.built)

    def test_what_stands_on_a_rectangle(self):
        site = SiteMap.read(self.save, (0, 0), 180, step=4)
        self.assertTrue(site.open((0, 0, 40, 40)))
        self.assertFalse(site.open((-130, -130, -90, -90)))
        self.assertFalse(site.open((0, 0, 400, 40)))
        self.assertEqual(site.relief((0, 0, 40, 40)), 0)
        self.assertEqual(site.height(1, 1), GROUND)
        self.assertEqual(site.samples((0, 0, 8, 0)), [(0, 0), (4, 0), (8, 0)])


class PlanTests(Land):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.site = SiteMap.read(MinecraftSave.load(cls.pristine), (0, 0), 180)
        cls.plan = plan_city(cls.site, 12, ZONES, seed=5)

    def test_it_is_a_plan_of_what_was_asked(self):
        self.assertIsInstance(self.plan, CityPlan)
        self.assertEqual(len(self.plan.lots), 12)
        self.assertIn("12 towers", self.plan.describe())
        self.assertIn("CityPlan", repr(self.plan))

    def test_one_site_and_seed_is_one_plan(self):
        again = plan_city(self.site, 12, ZONES, seed=5)
        self.assertEqual([lot.rectangle for lot in again.lots],
                         [lot.rectangle for lot in self.plan.lots])
        other = plan_city(self.site, 12, ZONES, seed=6)
        self.assertNotEqual([lot.rectangle for lot in other.lots],
                            [lot.rectangle for lot in self.plan.lots])

    def test_no_lot_stands_in_a_street_or_on_another(self):
        taken: dict = {}
        for lot in self.plan.lots:
            a, b, c, d = lot.rectangle
            for x in range(a, c + 1):
                for z in range(b, d + 1):
                    self.assertNotIn((x, z), taken)
                    self.assertNotIn((x, z), self.plan.network.columns)
                    taken[(x, z)] = lot

    def test_every_door_is_on_a_pavement(self):
        for lot in self.plan.lots:
            owner, role = self.plan.network.columns[lot.door]
            self.assertEqual(role, "walk", lot)

    def test_streets_keep_off_water(self):
        for x, z in self.plan.network.columns:
            self.assertFalse(self.site.height(x, z) is None)
            snap = (self.site.x0 + (x - self.site.x0) // 4 * 4,
                    self.site.z0 + (z - self.site.z0) // 4 * 4)
            self.assertNotIn(snap, self.site.wet)

    def test_the_tallest_stand_downtown(self):
        cx, cz = self.plan.downtown
        near = sorted(self.plan.lots, key=lambda lot: abs(lot.rectangle[0] - cx)
                      + abs(lot.rectangle[1] - cz))
        self.assertGreater(sum(lot.stories for lot in near[:4]),
                           sum(lot.stories for lot in near[-4:]))

    def test_no_two_towers_need_be_alike(self):
        kinds = {(lot.style.name, lot.stories, lot.building[2] - lot.building[0])
                 for lot in self.plan.lots}
        self.assertGreater(len(kinds), 6)
        self.assertGreater(len({lot.front for lot in self.plan.lots}), 1)

    def test_zones_get_their_families(self):
        for lot in self.plan.lots:
            self.assertIn(lot.style, ZONES[lot.zone])

    def test_a_flat_list_of_styles_serves_every_zone(self):
        plan = plan_city(self.site, 6, STYLES, seed=5)
        self.assertEqual(len(plan.lots), 6)

    def test_water_in_a_block_makes_it_a_park(self):
        def ponds(save):
            land(save)
            for x in range(-180, 180, 40):
                for z in range(-180, 180, 40):
                    save.fill((x + 18, GROUND, z + 18), (x + 21, GROUND, z + 21),
                              WATER)
        directory = world_of(ponds, radius=12)
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        site = SiteMap.read(MinecraftSave.load(directory), (0, 0), 180)
        try:
            plan = plan_city(site, 4, ZONES, seed=2)
        except Refused:
            return
        self.assertTrue(plan.parks)

    def test_land_too_small_says_what_it_holds(self):
        site = SiteMap.read(MinecraftSave.load(self.pristine), (0, 0), 90)
        with self.assertRaises(Refused) as caught:
            plan_city(site, 200, ZONES)
        self.assertIn("at the most", str(caught.exception))

    def test_what_is_no_plan_is_refused(self):
        for args, kept in (((None, 5, ZONES), {}),
                           ((self.site, 0, ZONES), {}),
                           ((self.site, 5, ()), {}),
                           ((self.site, 5, ZONES), {"parks": 2})):
            with self.subTest(kept=kept):
                with self.assertRaises(NBTError):
                    plan_city(*args, **kept)

    def test_a_picture_is_drawn_before_anything_is_laid(self):
        path = os.path.join(tempfile.mkdtemp(prefix="city-png-"), "plan.png")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), ignore_errors=True)
        self.plan.picture(path, scale=1)
        picture = read_png(path)
        self.assertEqual(picture["width"], self.site.x1 - self.site.x0 + 1)
        self.assertEqual(picture["height"], self.site.z1 - self.site.z0 + 1)


class LaidTests(unittest.TestCase):
    """One small city laid once, and looked at."""

    @classmethod
    def setUpClass(cls):
        cls.directory = world_of()
        cls.save = MinecraftSave.load(cls.directory)
        # A rail where the first lot's plaza will go, which no sample sees.
        cls.site = SiteMap.read(cls.save, (0, 0), 180)
        cls.plan = plan_city(cls.site, 6, ZONES, seed=5)
        a, b, c, d = cls.plan.lots[0].building
        cls.taken = cls.plan.lots[0]
        cls.save.set_block(a - 1, GROUND + 1, b - 1,
                           "minecraft:rail[shape=north_south]")
        cls.b = Builder(cls.save, seed=7)
        cls.result = cls.b.city(cls.plan)
        cls.report = cls.b.finish()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.directory, ignore_errors=True)

    def test_every_tower_stands_as_its_rules_say(self):
        self.assertEqual(len(self.result.towers), 6)
        for design in self.result.designs:
            self.assertEqual(design.verify(self.save), [], design)

    def test_a_lot_with_something_on_it_is_left_and_a_spare_taken(self):
        self.assertEqual(len(self.result.skipped), 1)
        self.assertIs(self.result.skipped[0][0], self.taken)
        self.assertIn("somebody built", self.result.skipped[0][1])
        self.assertTrue(self.plan.spare)

    def test_every_door_is_walked_to_from_the_streets(self):
        self.assertEqual(self.result.unreached, [])

    def test_a_door_is_level_with_its_pavement(self):
        streets = self.result.streets
        for lot, design in zip(self.plan.lots[1:], self.result.designs):
            step = streets.tops[lot.door] - 2 * (design.doorstep[1] + 1)
            self.assertIn(step, (0, 1), lot)

    def test_nothing_is_left_that_the_game_will_not_keep(self):
        self.assertEqual(self.report.unsupported, [])

    def test_streets_and_lamps_are_there(self):
        self.assertGreater(self.result.lamps, 4)
        self.assertEqual(len(self.result.streets.streets),
                         len(self.plan.network.edges))

    def test_a_park_is_grass_with_paths_and_trees(self):
        parks = self.plan.parks
        if not parks:
            self.skipTest("this plan went without")
        x0, z0, x1, z1 = parks[0].rectangle
        counts = collections.Counter()
        for x in range(x0, x1 + 1):
            for z in range(z0, z1 + 1):
                y = walk_surface(self.save, x, z).y
                counts[self.save.get_block(x, y, z).name] += 1
        self.assertGreater(counts["minecraft:grass_block"], 100)
        self.assertGreater(counts["minecraft:gravel"], 20)
        biome = self.save.get_biome((x0 + x1) // 2, GROUND, (z0 + z1) // 2)
        self.assertEqual(biome, "minecraft:plains")

    def test_a_street_that_meets_something_is_drawn_again_round_it(self):
        directory = world_of()
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        save = MinecraftSave.load(directory)
        site = SiteMap.read(save, (0, 0), 180)
        plan = plan_city(site, 6, ZONES, seed=5)
        x, z = next(column for column, (_, role)
                    in sorted(plan.network.columns.items()) if role == "road")
        save.set_block(x, GROUND + 1, z, "minecraft:magma_block")
        result = Builder(save, seed=7).city(plan)
        self.assertEqual(result.replanned, 1)
        self.assertNotIn((x, z), result.plan.network.columns)
        self.assertEqual(save.get_block(x, GROUND + 1, z).name,
                         "minecraft:magma_block")

    def test_it_takes_a_plan(self):
        with self.assertRaises(NBTError):
            self.b.city("downtown")


class CityExampleTests(unittest.TestCase):
    def setUp(self):
        self.directory = world_of()
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)

    def test_a_plan_only_run_writes_only_its_picture(self):
        before = sorted(os.listdir(self.directory))
        scratch = tempfile.mkdtemp(prefix="city-pic-")
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        picture = os.path.join(scratch, "plan.png")
        found = city_example.run(self.directory, towers=4, at=(0, 0),
                                 radius=180, plan_only=True, picture=picture,
                                 out=lambda _: None)
        self.assertEqual(len(found["plan"].lots), 4)
        self.assertTrue(os.path.exists(picture))
        self.assertEqual(sorted(os.listdir(self.directory)), before)

    def test_a_dry_run_keeps_out_of_the_world_and_a_write_saves(self):
        before = sorted(os.listdir(self.directory))
        found = city_example.run(self.directory, towers=4, at=(0, 0),
                                 radius=180, out=lambda _: None)
        self.assertEqual(found["problems"], [])
        self.assertFalse(found["saved"])
        self.assertEqual(sorted(os.listdir(self.directory)), before)
        found = city_example.run(self.directory, towers=4, at=(0, 0),
                                 radius=180, write=True, out=lambda _: None)
        self.assertEqual(found["problems"], [])
        self.assertTrue(found["saved"])
        fresh = MinecraftSave.load(self.directory)
        for design in found["result"].designs:
            self.assertEqual(design.verify(fresh), [])


if __name__ == "__main__":
    unittest.main()
