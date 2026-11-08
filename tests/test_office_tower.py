"""``examples/office_tower.py``: ten floors with a stair inside, end to end.

The tower is the office block's hand on a slimmer plan, so what is held here
is what that plan had to settle: a switchback's floors are as far apart as its
run, so the ground story is two turns of it and every upper floor one, and the
core's wall opens at the end each flight arrives at. The whole session runs on
a small world of its own, its checks and its save, and a walk reaches every
floor. Without the stair the floors are shut, so that walk finds the stair.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "examples"))

import office_tower  # noqa: E402
from elevations import (  # noqa: E402
    SHORT_GROUND,
    SHORT_SHAFT,
    TOWER_FACES,
    TOWER_GROUND,
    TOWER_SHAFT,
    tower_stories,
)
from heaton_voxel.craft import Elevation, reachable  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402
from office_tower import Tower  # noqa: E402
from tests.test_world import make_save  # noqa: E402

CENTER = (40, 40)
GROUND = 50


def world_at(directory: str) -> str:
    make_save(directory, [(x, z) for x in range(0, 6) for z in range(0, 6)])
    save = MinecraftSave.load(directory)
    save.fill((0, GROUND - 8, 0), (95, GROUND, 95), "minecraft:stone")
    save.rebuild_heightmaps((0, 0, 0), (95, 0, 95))
    save.save()
    return directory


class PlanTests(unittest.TestCase):
    def test_the_rhythms_fit_the_block(self):
        self.assertEqual(len(TOWER_SHAFT), office_tower.WIDTH)
        self.assertEqual(len(TOWER_GROUND), office_tower.WIDTH)
        self.assertEqual(len(SHORT_SHAFT), office_tower.DEPTH - 2)
        self.assertEqual(len(SHORT_GROUND), office_tower.DEPTH - 2)

    def test_the_ground_piers_stand_under_the_shafts(self):
        shaft = {i for i, role in enumerate(TOWER_SHAFT.columns)
                 if role == "pier"}
        ground = {i for i, role in enumerate(TOWER_GROUND.columns)
                  if role in ("pier", "fin")}
        self.assertEqual(ground, shaft)

    def test_it_is_ten_floors(self):
        levels = tower_stories(office_tower.UPPER, 0)
        self.assertEqual(len(levels["stories"]) + 1, 10)
        self.assertEqual(levels["stories"][:2], (8, 12))
        self.assertEqual(levels["top"], 44)

    def test_every_floor_is_where_the_stair_lands(self):
        t = Tower(*CENTER, GROUND)
        self.assertEqual(t.stair.pitch, 4)
        landings = set(t.stair.landing_levels(GROUND, t.top))
        self.assertLessEqual(set(t.floors), landings)

    def test_the_core_opens_at_the_end_each_flight_arrives_at(self):
        t = Tower(*CENTER, GROUND)
        south, north = t.sz1 + 1, t.sz0 - 1
        self.assertEqual(t.core_opening(GROUND), south)
        self.assertEqual([t.core_opening(level) for level in t.floors[:3]],
                         [south, north, south])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="office-tower-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        world_at(self.dir)

    def run_it(self, **options):
        said = []
        return office_tower.run(self.dir, *CENTER, out=said.append, **options)

    def test_a_dry_run_passes_and_saves_nothing(self):
        before = MinecraftSave.load(self.dir).get_block(*CENTER[:1], GROUND + 1,
                                                        CENTER[1])
        found = self.run_it()
        self.assertEqual(found["problems"], [])
        self.assertFalse(found["saved"])
        self.assertEqual(MinecraftSave.load(self.dir).get_block(
            *CENTER[:1], GROUND + 1, CENTER[1]), before)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "worksites")))

    def test_it_stands_and_every_floor_is_reached_and_left(self):
        found = self.run_it(write=True)
        self.assertEqual(found["problems"], [])
        self.assertTrue(found["saved"])
        t = found["tower"]
        save = MinecraftSave.load(self.dir)
        x0, z0, x1, z1 = t.plaza
        walk = reachable(save, t.doorstep(), (x0, GROUND - 1, z0),
                         (x1, t.top, z1), allow_jump=False)
        for level in t.floors:
            self.assertTrue(walk.footing(level), level)
        self.assertFalse(walk.stranded)

    def test_it_reads_back_as_the_rule_it_was_built_by(self):
        found = self.run_it(write=True)
        save = MinecraftSave.load(self.dir)
        for face, wall in found["tower"].walls().items():
            plan = Elevation.read(save, wall).plan(wall)
            self.assertEqual(
                {cell: state.name for cell, state in plan.items()},
                {cell: state.name for cell, state in
                 TOWER_FACES[face].plan(wall).items()}, face)

    def test_without_the_stair_the_floors_are_shut(self):
        """The floors above are reached only by it, so the walk that reaches
        them has found the stair."""
        found = self.run_it(write=True)
        t = found["tower"]
        save = MinecraftSave.load(self.dir)
        save.fill((t.sx0, t.y + 1, t.sz0), (t.sx1, t.floors[-1], t.sz1),
                  "minecraft:air")
        x0, z0, x1, z1 = t.plaza
        walk = reachable(save, t.doorstep(), (x0, GROUND - 1, z0),
                         (x1, t.top, z1))
        self.assertFalse(walk.footing(t.floors[0]))

    def test_an_open_world_is_refused(self):
        from unittest import mock

        from heaton_voxel.nbt import NBTError
        with mock.patch.object(office_tower, "world_is_open", return_value=True):
            with self.assertRaises(NBTError):
                self.run_it(write=True)


if __name__ == "__main__":
    unittest.main()
