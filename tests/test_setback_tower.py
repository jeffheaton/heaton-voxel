"""``examples/setback_tower.py``: three tiers, a core and a crown, in one call.

What the Palace rules are held to here is the numbers read off the building
they describe, on 2026-11-01, wall by wall, none of it vendored: the same rules
were replayed over it, 11,616 wall cells with 25 wrong, every one of them
accounted for: a door out onto a terrace, a false elevator door, and the top
tier's second course, a block out of true on each face.
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

import setback_tower  # noqa: E402
from elevations import (  # noqa: E402
    PALACE_BASE,
    PALACE_LOWER,
    PALACE_TOP,
    PALACE_UPPER,
)
from heaton_voxel.craft import Massing, Wall, reachable  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402
from setback_tower import CROWN, Tower  # noqa: E402
from tests.test_world import make_save  # noqa: E402

CENTER = (48, 48)
GROUND = 50
#: As few floors as the facades' fixed courses leave room for.
FLOORS = dict(lower=10, upper=5, top=1)

#: Each wall read off the building: rows and columns, and how many cells of
#: wool and of glass it holds, north wall and east wall of each tier. The
#: top tier's two iron doors stand in what its north rule calls glass.
READ = {
    "lower": (PALACE_LOWER, (-416, 229, -396, 265), -53, 19, 515, 997, 776, 1744),
    "upper": (PALACE_UPPER, (-414, 231, -398, 263), 19, 49, 196, 314, 296, 634),
}


def census(rule, wall):
    return collections.Counter(state.name.removeprefix("minecraft:")
                               for state in rule.plan(wall).values())


def world_at(directory):
    make_save(directory, [(x, z) for x in range(0, 7) for z in range(0, 7)])
    save = MinecraftSave.load(directory)
    save.fill((0, GROUND - 8, 0), (111, GROUND, 111), "minecraft:stone")
    save.rebuild_heightmaps((0, 0, 0), (111, 0, 111))
    save.save()


class SampleTests(unittest.TestCase):
    def test_the_walls_hold_what_the_buildings_do(self):
        for name, (faces, box, floor_y, top, wool_n, glass_n, wool_e,
                   glass_e) in READ.items():
            with self.subTest(tier=name):
                ring = Wall.ring(*box, floor_y=floor_y, top=top)
                north = census(faces["north"], ring["north"])
                east = census(faces["east"], ring["east"])
                self.assertEqual((north["white_wool"],
                                  north["light_blue_stained_glass"]),
                                 (wool_n, glass_n))
                self.assertEqual((east["white_wool"],
                                  east["light_blue_stained_glass"]),
                                 (wool_e, glass_e))

    def test_the_top_tiers_wall_comes_to_the_buildings_within_its_door(self):
        ring = Wall.ring(-412, 241, -400, 253, floor_y=49, top=63)
        north = census(PALACE_TOP["north"], ring["north"])
        self.assertEqual(len(ring["north"].along) * len(ring["north"].rows), 182)
        self.assertEqual(north["white_wool"] + north[
            "light_blue_stained_glass"], 182)

    def test_each_rule_stretches_to_any_height_over_its_courses(self):
        for faces, width in ((PALACE_UPPER, 17), (PALACE_TOP, 13)):
            for extra in (0, 8, 80):
                wall = Wall.ring(0, 0, width - 1, width - 1, floor_y=0,
                                 top=40 + extra)["north"]
                self.assertEqual(len(faces["north"].plan(wall)),
                                 width * (40 + extra))

    def test_the_arcade_stands_under_the_lowest_face(self):
        wall = Wall.ring(0, 0, 20, 36, floor_y=0, top=80,
                         stories=())["south"]
        cells = PALACE_BASE["south"].plan(wall)
        self.assertEqual(cells[wall.cell(0, 3)].name, "minecraft:white_wool")
        doors = [cell for cell, state in cells.items()
                 if state.name.endswith("_door")]
        self.assertEqual(len(doors), 6, "three doors, two halves each")


class PlanTests(unittest.TestCase):
    def test_each_tier_steps_back_from_the_last(self):
        t = Tower(*CENTER, GROUND)
        low, mid, top = t.massing.tiers
        self.assertEqual((low.width, low.depth), (21, 37))
        self.assertEqual((mid.width, mid.depth), (17, 33))
        self.assertEqual((top.width, top.depth), (13, 13))
        self.assertTrue(low.holds(mid) and mid.holds(top))

    def test_every_floor_is_where_the_stair_lands(self):
        t = Tower(*CENTER, GROUND)
        landings = set(t.stair.landing_levels(GROUND, t.massing.top))
        self.assertLessEqual(set(t.floors), landings)

    def test_a_tier_stands_on_a_floor_of_the_one_under_it(self):
        low, mid, top = Tower(*CENTER, GROUND).massing.tiers
        self.assertIn(mid.floor_y, low.stories)
        self.assertIn(top.floor_y, mid.stories)


class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pristine = tempfile.mkdtemp(prefix="setback-tower-world-")
        cls.addClassCleanup(shutil.rmtree, cls.pristine, ignore_errors=True)
        world_at(cls.pristine)
        cls.built = tempfile.mkdtemp(prefix="setback-tower-built-")
        cls.addClassCleanup(shutil.rmtree, cls.built, ignore_errors=True)
        shutil.rmtree(cls.built)
        shutil.copytree(cls.pristine, cls.built)
        said: list = []
        cls.found = setback_tower.run(cls.built, *CENTER, write=True,
                                      out=said.append, **FLOORS)
        cls.save = MinecraftSave.load(cls.built)

    def test_it_passes_every_check_and_is_saved(self):
        self.assertEqual(self.found["problems"], [])
        self.assertTrue(self.found["saved"])

    def test_a_dry_run_saves_nothing(self):
        directory = tempfile.mkdtemp(prefix="setback-tower-dry-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        shutil.rmtree(directory)
        shutil.copytree(self.pristine, directory)
        found = setback_tower.run(directory, *CENTER, out=lambda _: None,
                                  **FLOORS)
        self.assertEqual(found["problems"], [])
        self.assertFalse(found["saved"])
        self.assertFalse(os.path.exists(os.path.join(directory, "worksites")))

    def test_every_floor_is_walked_there_and_back_without_a_jump(self):
        t = self.found["tower"]
        x0, z0, x1, z1 = t.plaza
        walk = reachable(self.save, t.doorstep(), (x0, GROUND - 1, z0),
                         (x1, t.massing.top + 2, z1), allow_jump=False)
        self.assertFalse(walk.stranded)
        for level in t.floors:
            self.assertTrue(walk.footing(level), level)

    def test_without_the_stair_the_floors_are_shut(self):
        """The floors are reached only by it, so the walk that reaches them
        has found the stair."""
        directory = tempfile.mkdtemp(prefix="setback-tower-shut-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        shutil.rmtree(directory)
        shutil.copytree(self.built, directory)
        save = MinecraftSave.load(directory)
        t = self.found["tower"]
        c = t.core
        save.fill((c.x0 + 1, GROUND + 1, c.z0 + 1),
                  (c.x1 - 1, t.floors[-1], c.z1 - 1), "minecraft:air")
        x0, z0, x1, z1 = t.plaza
        walk = reachable(save, t.doorstep(), (x0, GROUND - 1, z0),
                         (x1, t.massing.top + 2, z1))
        self.assertFalse(walk.footing(t.floors[0]))

    def test_it_reads_back_as_the_massing_it_was_built_by(self):
        t = self.found["tower"]
        low, mid, top = t.massing.tiers
        x0, z0, x1, z1 = t.plaza
        read = Massing.read(self.save, (x0, GROUND + 1, z0),
                            (x1, top.top + CROWN.height + 1, z1))
        outlines = [tier.rectangle for tier in read.tiers]
        for tier in (low, mid, top):
            self.assertIn(tier.rectangle, outlines)
        self.assertEqual(read.verify(self.save), [])
        rings = [tier for tier in read.tiers if tier.floor_y >= top.top]
        self.assertEqual([tier.width for tier in rings], [9, 7, 5, 3])


if __name__ == "__main__":
    unittest.main()
