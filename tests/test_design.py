"""Tests for tower variety: Rhythm.spaced, Fitted, recolored, Footprint, side
by side tiers, and design_tower, which works a whole building out from them.

An Elevation is drawn for one width and refuses every other, which is right for
a rule read off a wall and no use for a street of towers nobody has drawn. These
hold what a generated tower needs: bays worked out for any width, a rule that
composes itself for each wall, colors that change and leave the pattern alone,
plans that are no rectangle, and designs that stand. ``examples/tower_styles.py``
is the reference for what a family looks like.
"""

from __future__ import annotations

import collections
import copy
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "examples"))

import skyline  # noqa: E402
from elevations import PALACE_BASE, TOWER_FACES  # noqa: E402
from setback_tower import CROWN  # noqa: E402
from tower_styles import BRICK, DECO, GLASS, STYLES  # noqa: E402
from heaton_voxel.craft import (  # noqa: E402
    audit,
    Band,
    Core,
    design_tower,
    Doors,
    Elevation,
    Facade,
    Fitted,
    Footprint,
    Glazing,
    Layers,
    Massing,
    Material,
    Mix,
    Opening,
    reachable,
    Rhythm,
    Scheme,
    Tier,
    TowerDesign,
    TowerStyle,
    Wall,
)
from heaton_voxel.craft.build import (  # noqa: E402
    Builder,
    ROOFS,
    SHAPES,
    SwitchbackStair,
    WINDOWS,
)
from heaton_voxel.craft.errors import Refused  # noqa: E402
from heaton_voxel.nbt import NBTError  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402
from tests.test_world import make_save  # noqa: E402

STONE = "minecraft:stone"
BRICKS = "minecraft:bricks"
GLASS_PANE = "minecraft:gray_stained_glass_pane"
PLANKS = "minecraft:oak_planks"
SLATE = "minecraft:polished_andesite"
GROUND = 63


def plain_scheme(**options):
    kept = dict(trim=SLATE, tint="minecraft:light_gray_stained_glass_pane",
                roof=Material("minecraft:deepslate_tiles"))
    kept.update(options)
    return Scheme(BRICKS, GLASS_PANE, **kept)


def plain_face(wall):
    across = Rhythm.spaced(len(wall.along), closed=wall.ends)
    return Elevation(Band(None, across, {"pier": BRICKS, "bay": GLASS_PANE}))


class Flat(unittest.TestCase):
    """A flat world of stone, ten chunks square, for what stands on it."""

    RADIUS = 5

    @classmethod
    def setUpClass(cls):
        cls.pristine = tempfile.mkdtemp(prefix="design-tests-")
        chunks = [(x, z) for x in range(-cls.RADIUS, cls.RADIUS)
                  for z in range(-cls.RADIUS, cls.RADIUS)]
        make_save(cls.pristine, chunks)
        save = MinecraftSave.load(cls.pristine)
        reach = cls.RADIUS * 16
        save.fill((-reach, 40, -reach), (reach - 1, GROUND, reach - 1), STONE)
        save.rebuild_heightmaps((-reach, 0, -reach), (reach - 1, 0, reach - 1))
        save.save()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.pristine, ignore_errors=True)

    def setUp(self):
        directory = tempfile.mkdtemp(prefix="design-world-")
        os.rmdir(directory)
        shutil.copytree(self.pristine, directory)
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        self.save = MinecraftSave.load(directory)
        self.b = Builder(self.save, seed=7)

    def name(self, x, y, z):
        return self.save.get_block(x, y, z).name


class SpacedTests(unittest.TestCase):
    """A rhythm worked out for a width, not cut to one."""

    def test_it_is_exactly_as_wide_mirrored_and_even_at_every_width(self):
        count = 0
        for width in range(3, 100):
            for target in range(1, 7):
                for closed in (True, False):
                    for pier_width in (1, 2):
                        try:
                            rhythm = Rhythm.spaced(
                                width, target=target, closed=closed,
                                pier_width=pier_width)
                        except NBTError:
                            continue
                        count += 1
                        self.assertEqual(len(rhythm), width)
                        self.assertTrue(rhythm.symmetric)
                        bays = [n for role, n in rhythm.runs if role == "bay"]
                        self.assertLessEqual(max(bays) - min(bays), 1, rhythm)
        self.assertGreater(count, 2000)

    def test_the_wider_bays_go_to_the_middle(self):
        self.assertEqual(
            [n for role, n in Rhythm.spaced(22).runs if role == "bay"],
            [3, 3, 4, 3, 3])

    def test_a_closed_rhythm_ends_on_piers_and_an_open_one_on_bays(self):
        self.assertEqual(Rhythm.spaced(21).columns[0], "pier")
        self.assertEqual(Rhythm.spaced(21).columns[-1], "pier")
        self.assertEqual(Rhythm.spaced(21, closed=False).columns[0], "bay")
        self.assertEqual(Rhythm.spaced(21, closed=False).columns[-1], "bay")

    def test_ends_stand_at_both_ends(self):
        pavilion = Rhythm("post", ("glass", 5))
        rhythm = Rhythm.spaced(40, ends=pavilion)
        self.assertEqual(rhythm.columns[:6], pavilion.columns)
        self.assertEqual(rhythm.columns[-6:], pavilion.reversed().columns)
        self.assertEqual(len(rhythm), 40)

    def test_a_center_takes_the_middle_bay(self):
        rhythm = Rhythm.spaced(21, center="door")
        self.assertEqual(rhythm.columns.count("door"), 3)
        self.assertEqual(rhythm.columns[10], "door")
        fixed = Rhythm.spaced(24, center=("door", 2))
        self.assertEqual(fixed.columns.count("door"), 2)
        self.assertEqual(fixed.columns[11:13], ("door", "door"))
        self.assertTrue(fixed.symmetric)

    def test_what_cannot_stand_is_refused(self):
        for width, options, words in (
                (2, {}, "no room"),
                (21, {"center": ("door", 2)}, "mirrored in the middle"),
                (10, {"ends": Rhythm(("post", 5))}, "ends 5 wide"),
                (10, {"target": 0}, "at least one column"),
                (10, {"ends": "post"}, "is a Rhythm")):
            with self.subTest(width=width, options=options):
                with self.assertRaises(NBTError) as caught:
                    Rhythm.spaced(width, **options)
                self.assertIn(words, str(caught.exception))


class FittedTests(unittest.TestCase):
    def wall(self, width):
        return Wall((0, 0), (width - 1, 0), floor_y=64, top=75, outward="north",
                    ends=True)

    def test_one_rule_fits_walls_an_elevation_refuses(self):
        rule = Fitted(plain_face)
        for width in (9, 17, 22, 40):
            wall = self.wall(width)
            self.assertEqual(rule.plan(wall), plain_face(wall).plan(wall))
        with self.assertRaises(NBTError):
            plain_face(self.wall(9)).plan(self.wall(17))

    def test_it_makes_a_tier_of_any_rectangle(self):
        for width, depth in ((11, 13), (31, 17), (24, 40)):
            tier = Tier(0, 0, width - 1, depth - 1, Fitted(plain_face),
                        floor_y=64, top=80)
            for name, wall in tier.walls().items():
                self.assertTrue(tier.rule_of(name).plan(wall))

    def test_it_says_what_it_made(self):
        wall = self.wall(17)
        rule = Fitted(plain_face)
        self.assertEqual(rule.describe(wall), plain_face(wall).describe())
        self.assertEqual(rule.role(wall, 0, 66), "pier")
        self.assertIn("plain_face", repr(rule))
        facade = Fitted(lambda w: Facade(BRICKS, pier=SLATE, pitch=4))
        self.assertEqual(facade.role(wall, 4, 66), "pier")

    def test_what_is_no_maker_is_refused(self):
        with self.assertRaises(NBTError):
            Fitted(BRICKS)
        with self.assertRaises(NBTError) as caught:
            Fitted(lambda wall: BRICKS).plan(self.wall(9))
        self.assertIn("makes a Facade or an Elevation", str(caught.exception))


class RecoloredTests(unittest.TestCase):
    WALL = Wall((0, 0), (20, 0), floor_y=64, top=80, outward="north", ends=True,
                stories=(68, 72, 76))

    def test_the_pattern_stands_and_only_the_blocks_change(self):
        rule = PALACE_BASE["north"]
        wall = Tier(0, 0, 20, 36, floor_y=64, top=120,
                    stories=tuple(range(72, 118, 4))).walls()["north"]
        before = rule.plan(wall)
        after = rule.recolored(
            {"minecraft:white_wool": "minecraft:black_concrete"}).plan(wall)
        self.assertEqual(set(before), set(after))
        for cell, state in before.items():
            changed = state.name == "minecraft:white_wool"
            self.assertEqual(after[cell].name, "minecraft:black_concrete"
                             if changed else state.name)

    def test_every_way_a_role_holds_a_block_changes(self):
        across = Rhythm("a", "b", "c", "d")
        rule = Elevation(Band(2, across, {
            "a": BRICKS,
            "b": Glazing(BRICKS, GLASS_PANE, spandrel=BRICKS),
            "c": Layers("minecraft:air", BRICKS),
            "d": Doors("minecraft:oak_door")}))
        new = rule.recolored({BRICKS: SLATE,
                              "minecraft:oak_door": "minecraft:birch_door"})
        band = new.bands[0]
        self.assertEqual(band.roles["a"].name, SLATE)
        self.assertEqual(band.roles["b"].rows[0].name, SLATE)
        self.assertEqual(band.roles["b"].rows[1].name, GLASS_PANE)
        self.assertEqual(band.roles["b"].spandrel.name, SLATE)
        self.assertEqual(band.roles["c"].values[1].name, SLATE)
        self.assertEqual(band.roles["d"].door.name, "minecraft:birch_door")

    def test_a_mix_keeps_its_weights_and_a_stair_its_facing(self):
        mix = Mix([(BRICKS, 3), (STONE, 1)], seed=5, clump=2)
        rule = Elevation(Band(None, Rhythm(("a", 21)), {"a": mix}))
        new = rule.recolored({BRICKS: SLATE}).bands[0].roles["a"]
        self.assertEqual(new.seed, 5)
        self.assertEqual(new.clump, 2)
        self.assertEqual([weight for _, weight in new.entries], [3, 1])
        stairs = Elevation(Band(None, Rhythm(("a", 21)), {
            "a": "minecraft:oak_stairs[facing=east]"})).recolored(
                {"minecraft:oak_stairs": "minecraft:stone_stairs"})
        self.assertEqual(str(stairs.bands[0].roles["a"]),
                         "minecraft:stone_stairs[facing=east]")

    def test_a_mix_can_stand_in_for_a_block(self):
        mix = Mix([(SLATE, 1), (STONE, 1)])
        rule = Elevation(Band(None, Rhythm(("a", 21)), {"a": BRICKS}))
        self.assertIs(rule.recolored({BRICKS: mix}).bands[0].roles["a"], mix)

    def test_a_facade_a_crown_a_core_and_a_massing_change_too(self):
        facade = Facade(BRICKS, pier=STONE, pitch=4, head=(1, STONE)).recolored(
            {STONE: SLATE})
        self.assertEqual(facade.pier.name, SLATE)
        self.assertEqual(facade.head[1].name, SLATE)
        self.assertEqual(facade.field.name, BRICKS)
        crown = CROWN.recolored({"minecraft:white_wool": SLATE})
        self.assertEqual(crown.fin.name, SLATE)
        self.assertEqual(CROWN.fin.name, "minecraft:white_wool")
        massing = Massing(Tier(0, 0, 26, 22, faces=TOWER_FACES, floor_y=64,
                               top=75, floor=PLANKS, stories=(69, 73)))
        repainted = massing.recolored({PLANKS: STONE, BRICKS: SLATE})
        self.assertEqual(repainted.tiers[0].floor.name, STONE)
        self.assertEqual(massing.tiers[0].floor.name, PLANKS)
        core = Core(0, 0, 5, 5, wall=BRICKS, stair=SwitchbackStair(
            (1, 1), (4, 4), axis="z", start="southwest", start_y=65, landing=1,
            stair="minecraft:oak_stairs", wood=PLANKS))
        new = core.recolored({"minecraft:oak_stairs": "minecraft:stone_stairs",
                              PLANKS: STONE})
        self.assertEqual(str(new.stair.stair).split("[")[0],
                         "minecraft:stone_stairs")
        self.assertEqual(str(core.stair.stair).split("[")[0],
                         "minecraft:oak_stairs")

    def test_a_key_the_rule_does_not_hold_is_refused(self):
        rule = Elevation(Band(None, Rhythm(("a", 21)), {"a": BRICKS}))
        for mapping, words in (({STONE: SLATE}, "nothing in the rule holds"),
                               ({}, "takes {block: block}")):
            with self.subTest(mapping=mapping):
                with self.assertRaises(NBTError) as caught:
                    rule.recolored(mapping)
                self.assertIn(words, str(caught.exception))
        with self.assertRaises(NBTError):
            rule.recolored({Mix([(BRICKS, 1), (STONE, 1)]): SLATE})

    def test_a_fitted_rule_changes_for_each_wall(self):
        rule = Fitted(plain_face).recolored({BRICKS: SLATE, STONE: PLANKS})
        names = {state.name for state in rule.plan(self.WALL).values()}
        self.assertIn(SLATE, names)
        self.assertNotIn(BRICKS, names)


class FootprintTests(unittest.TestCase):
    ELL = dict(less=[(11, 0, 20, 9)])

    def covered(self, plan):
        cells = []
        for wall in plan.walls(floor_y=0, top=9).values():
            cells += [(wall.cell(a, 1, 0)[0], wall.cell(a, 1, 0)[2])
                      for a in wall.along]
        return cells

    def test_a_rectangle_is_its_ring(self):
        plan = Footprint((3, 4, 20, 15))
        self.assertEqual(
            plan.walls(floor_y=64, top=75, stories=(70,)),
            Wall.ring(3, 4, 20, 15, floor_y=64, top=75, stories=(70,)))

    def test_its_walls_are_its_rim_and_each_cell_is_laid_once(self):
        for label, plan in (
                ("ell", Footprint((0, 0, 20, 20), **self.ELL)),
                ("tee", Footprint((0, 0, 30, 20), less=[(0, 11, 9, 20),
                                                        (21, 11, 30, 20)])),
                ("you", Footprint((0, 0, 30, 20), less=[(10, 8, 20, 20)])),
                ("cross", Footprint((0, 0, 30, 30), less=[
                    (0, 0, 8, 8), (22, 0, 30, 8), (0, 22, 8, 30),
                    (22, 22, 30, 30)])),
                ("court", Footprint((0, 0, 20, 20), less=[(8, 8, 12, 12)]))):
            with self.subTest(plan=label):
                cells = self.covered(plan)
                self.assertEqual(sorted(cells), sorted(plan.rim))

    def test_the_inside_and_the_rim_are_the_plan(self):
        plan = Footprint((0, 0, 20, 20), **self.ELL)
        self.assertEqual(plan.inner | plan.rim, plan.columns)
        self.assertFalse(plan.inner & plan.rim)
        self.assertEqual(plan.bounds, (0, 0, 20, 20))

    def test_a_wall_names_the_way_it_faces(self):
        walls = Footprint((0, 0, 20, 20), **self.ELL).walls(floor_y=0, top=9)
        self.assertEqual(set(walls), {"north", "north-2", "east", "east-2",
                                      "south", "west"})
        self.assertEqual(walls["north-2"].outward, "north")
        self.assertTrue(walls["north"].ends)
        self.assertFalse(walls["east"].ends)

    def test_boxes_cover_it_exactly(self):
        plan = Footprint((0, 0, 30, 20), less=[(10, 8, 20, 20)])
        cells = [(x, z) for x0, z0, x1, z1 in Footprint.boxes(plan.columns)
                 for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)]
        self.assertEqual(sorted(cells), sorted(plan.columns))

    def test_eroded_steps_in_and_keeps_the_shape(self):
        plan = Footprint((0, 0, 30, 30), less=[(16, 0, 30, 14)])
        smaller = plan.eroded(2)
        self.assertEqual(smaller.bounds, (2, 2, 28, 28))
        self.assertLess(len(smaller.columns), len(plan.columns))
        self.assertTrue(smaller.columns < plan.columns)
        self.assertEqual(plan.eroded(0), plan)
        with self.assertRaises(NBTError):
            plan.eroded(-1)
        with self.assertRaises(NBTError):
            plan.eroded(40)

    def test_what_is_no_plan_is_refused(self):
        for args, kept, words in (
                ((), {}, "at least one"),
                (((0, 0, 5, 5),), {"less": [(0, 0, 5, 5)]}, "nothing left"),
                (((0, 0, 5, 5), (9, 9, 14, 14)), {}, "come apart"),
                (((0, 0, 10, 10), (11, 0, 12, 30)), {}, "at least three"),
                (((0, 0, 10, 10), (9, 9, 19, 19)), {}, "inside has to join"),
                (((0, 0),), {}, "(x0, z0, x1, z1)")):
            with self.subTest(args=args):
                with self.assertRaises(NBTError) as caught:
                    Footprint(*args, **kept)
                self.assertIn(words, str(caught.exception))


class ShellOnFootprintTests(Flat):
    def test_a_shell_stands_on_an_ell(self):
        plan = Footprint((0, 0, 20, 20), less=[(11, 0, 20, 9)])
        result = self.b.shell(*plan.bounds, Fitted(plain_face), floor_y=GROUND,
                              top=GROUND + 9, stories=(GROUND + 5,),
                              floor=PLANKS, deck=SLATE, parapet=SLATE,
                              footprint=plan)
        self.assertEqual(result.perimeter, ())
        self.assertEqual(self.name(15, GROUND + 5, 15), PLANKS)
        self.assertEqual(self.name(15, GROUND + 5, 5), "minecraft:air")
        self.assertEqual(self.name(15, GROUND + 10, 15), SLATE)
        self.assertEqual(self.name(15, GROUND + 10, 5), "minecraft:air")
        self.assertEqual(self.name(0, GROUND + 11, 5), SLATE)
        self.assertEqual(self.name(5, GROUND + 3, 5), "minecraft:air")

    def test_a_shell_on_a_footprint_is_one_block_thick(self):
        plan = Footprint((0, 0, 20, 20))
        with self.assertRaises(NBTError):
            self.b.shell(*plan.bounds, Fitted(plain_face), floor_y=GROUND,
                         top=GROUND + 9, thickness=2, footprint=plan)
        with self.assertRaises(NBTError):
            self.b.shell(*plan.bounds, Fitted(plain_face), floor_y=GROUND,
                         top=GROUND + 9, footprint=(0, 0, 20, 20))

    def test_a_named_wall_takes_a_rule_of_its_own(self):
        plan = Footprint((0, 0, 20, 20), less=[(11, 0, 20, 9)])
        faces = {"north-2": Fitted(lambda wall: Facade(SLATE))}
        self.b.shell(*plan.bounds, Fitted(plain_face), floor_y=GROUND,
                     top=GROUND + 4, faces=faces, footprint=plan)
        self.assertEqual(self.name(15, GROUND + 2, 10), SLATE)
        self.assertEqual(self.name(5, GROUND + 2, 0) in (BRICKS, GLASS_PANE),
                         True)


class SideBySideTests(Flat):
    def twin(self, *, overlap=False, y=GROUND, top=23):
        face = Fitted(plain_face)
        stair = dict(axis="z", start="southwest", start_y=y + 1, landing=1)
        base = Tier(0, 0, 40, 20, face, floor_y=y, top=y + 10,
                    stories=(y + 4, y + 8), floor=PLANKS, terrace=SLATE,
                    ground=SLATE,
                    openings=[("south", Opening.rect(3, 3), 19)])
        common = dict(floor_y=y + 8, top=y + top,
                      stories=(y + 12, y + 16, y + 20), floor=PLANKS)
        left = Tier(1, 1, 17, 19, face, deck=SLATE, **common)
        right = Tier(17 if overlap else 23, 1, 39, 19, face, deck=SLATE,
                     **common)
        cores = tuple(
            Core(cx - 3, 7, cx + 2, 12, wall=SLATE, top=y + top,
                 stair=SwitchbackStair((cx - 2, 8), (cx + 1, 11), **stair))
            for cx in (9, 31))
        return Massing(base, left, right), cores

    def test_two_towers_stand_on_one_base_with_a_core_each(self):
        massing, cores = self.twin()
        result = self.b.tower(massing, core=cores, doorstep=(20, GROUND, 22))
        self.assertEqual(len(result.walls), 3)
        self.assertIn(GROUND + 8, result.terraces)
        top_floor = result.floors[GROUND + 20]
        self.assertTrue(any(x < 20 for x, _, _ in top_floor))
        self.assertTrue(any(x > 20 for x, _, _ in top_floor))
        self.assertEqual(self.name(20, GROUND + 8, 10), SLATE)

    def test_two_towers_may_not_overlap(self):
        massing, cores = self.twin(overlap=True)
        with self.assertRaises(NBTError) as caught:
            self.b.tower(massing, core=cores)
        self.assertIn("overlap", str(caught.exception))

    def test_a_tower_with_no_way_up_is_refused(self):
        massing, cores = self.twin()
        with self.assertRaises(Refused):
            self.b.tower(massing, core=cores[0], doorstep=(20, GROUND, 22))

    def test_a_plain_stack_is_still_refused_where_it_reaches_past(self):
        face = Fitted(plain_face)
        low = Tier(0, 0, 20, 20, face, floor_y=GROUND, top=GROUND + 10,
                   stories=(GROUND + 4, GROUND + 8))
        high = Tier(2, 2, 18, 30, face, floor_y=GROUND + 8, top=GROUND + 14,
                    stories=(GROUND + 12,))
        with self.assertRaises(NBTError) as caught:
            self.b.tower(Massing(low, high))
        self.assertIn("reaches past", str(caught.exception))

    def test_a_crown_stands_on_each_tower(self):
        massing, cores = self.twin(top=24)
        crown = CROWN.recolored({"minecraft:white_wool": SLATE})
        small = copy.copy(crown)
        self.b.tower(Massing(massing.tiers[0], *(
            Tier(*tier.rectangle, tier.rule, floor_y=tier.floor_y, top=tier.top,
                 stories=tier.stories, floor=tier.floor)
            for tier in massing.tiers[1:])), core=cores, crown=small)
        first = GROUND + 25
        self.assertNotEqual(self.name(3, first, 3), "minecraft:air")
        self.assertNotEqual(self.name(25, first, 3), "minecraft:air")

    def test_a_crown_wants_a_rectangle(self):
        plan = Footprint((0, 0, 30, 30), less=[(16, 0, 30, 14)])
        tier = Tier.over(plan, Fitted(plain_face), floor_y=GROUND,
                         top=GROUND + 8)
        with self.assertRaises(NBTError) as caught:
            self.b.tower(Massing(tier), crown=CROWN)
        self.assertIn("rectangle", str(caught.exception))

    def test_a_tier_over_a_rectangular_footprint_is_a_plain_tier(self):
        tier = Tier.over(Footprint((0, 0, 20, 20)), Fitted(plain_face),
                         floor_y=GROUND, top=GROUND + 8)
        self.assertIsNone(tier.footprint)
        with self.assertRaises(NBTError):
            Tier.over((0, 0, 20, 20), floor_y=GROUND, top=GROUND + 8)
        with self.assertRaises(NBTError):
            Tier(0, 0, 20, 20, floor_y=GROUND, top=GROUND + 8,
                 footprint=(0, 0, 20, 20))

    def test_a_tier_may_stand_a_flat_roof_or_a_hip_on_itself(self):
        slate = Material("minecraft:deepslate_tiles")
        from heaton_voxel.craft import Roof
        flat = Tier(0, 0, 14, 14, Fitted(plain_face), floor_y=GROUND,
                    top=GROUND + 6, deck=SLATE, parapet=SLATE, parapet_height=2)
        self.b.tower(Massing(flat))
        self.assertEqual(self.name(0, GROUND + 9, 0), SLATE)
        hip = Tier(40, 0, 54, 14, Fitted(plain_face), floor_y=GROUND,
                   top=GROUND + 6, attic="hollow",
                   roof=Roof(rings="square", pitch=2, block=slate, slab=slate,
                             stair=slate))
        self.b.tower(Massing(hip))
        self.assertTrue(any("deepslate" in self.name(47, y, 7)
                            for y in range(GROUND + 7, GROUND + 16)))

    def test_verify_leaves_a_floor_run_through_a_recess_alone(self):
        recess = Layers("minecraft:air",
                        Glazing(GLASS_PANE, GLASS_PANE, spandrel=BRICKS))

        def face(wall):
            across = Rhythm.spaced(len(wall.along), closed=True)
            return Elevation(Band(None, across,
                                  {"pier": BRICKS, "bay": recess}))

        tier = Tier(0, 0, 20, 20, Fitted(face), floor_y=GROUND, top=GROUND + 9,
                    stories=(GROUND + 5,), floor=PLANKS)
        massing = Massing(tier)
        self.b.tower(massing)
        self.assertEqual(massing.verify(self.save), [])


class DesignTests(Flat):
    LOT = (0, 0, 32, 36)

    def style(self, **options):
        kept = dict(window="punched", tiers=(1,), roofs=("flat",),
                    shapes=("box",))
        kept.update(options)
        return TowerStyle("sample", plain_scheme(), **kept)

    def laid(self, design):
        """``design`` checked as far as a write, with nothing written, so a
        scene is shared by every case of a sweep."""
        b = Builder(self.save, seed=7)
        b._commit = lambda *args, **kept: []
        return b.tower(design.massing, core=design.cores, crown=design.crown,
                       doorstep=design.doorstep)

    def test_every_plan_and_roof_is_walked_to_every_floor_and_back(self):
        count = 0
        for shape in SHAPES:
            for roof in ROOFS:
                for tiers in (1, 3):
                    with self.subTest(shape=shape, roof=roof, tiers=tiers):
                        design = design_tower(self.LOT, GROUND, 7, self.style(),
                                              shape=shape, roof=roof,
                                              tiers=tiers)
                        result = self.laid(design)
                        self.assertEqual(result.walk.stranded, frozenset()
                                         if isinstance(result.walk.stranded,
                                                       frozenset) else set())
                        count += 1
        self.assertEqual(count, len(SHAPES) * len(ROOFS) * 2)

    def test_every_window_stands_at_both_pitches(self):
        for window in WINDOWS:
            for pitch in (4, 5):
                with self.subTest(window=window, pitch=pitch):
                    style = self.style(window=window, pitch=pitch, tiers=(2,),
                                       lobby=2)
                    self.laid(design_tower(self.LOT, GROUND, 6, style))

    def test_the_entrance_faces_the_way_it_is_asked(self):
        for front in ("north", "east", "south", "west"):
            with self.subTest(front=front):
                design = design_tower((20, 20, 44, 50), GROUND, 4, self.style(),
                                      front=front)
                self.assertEqual(design.front, front)
                result = self.laid(design)
                self.assertIsNotNone(result.walk)

    def test_a_lot_and_a_seed_are_one_building_in_any_session(self):
        one = design_tower(self.LOT, GROUND, 12, DECO, seed=4)
        two = design_tower(self.LOT, GROUND, 12, DECO, seed=4)
        self.assertEqual(one.describe(), two.describe())
        self.assertEqual(
            [tier.rectangle for tier in one.massing.tiers],
            [tier.rectangle for tier in two.massing.tiers])

    def test_seeds_and_lots_make_a_street_of_different_towers(self):
        seen: collections.Counter = collections.Counter()
        for index in range(60):
            lot = (index * 40, 0, index * 40 + 30, 34)
            style = STYLES[index % len(STYLES)]
            design = design_tower(lot, GROUND, 8 + index % 25, style, seed=9)
            seen[(style.name, design.shape, design.roof,
                  len(design.massing.tiers), str(design.scheme))] += 1
        self.assertGreater(len(seen), 25)

    def test_a_choice_named_is_not_rolled(self):
        style = self.style(roofs=("flat", "stepped"), shapes=("box", "ell"))
        scheme = plain_scheme(trim="minecraft:smooth_stone")
        design = design_tower(self.LOT, GROUND, 8, style, shape="ell",
                              roof="stepped", scheme=scheme)
        self.assertEqual(design.shape, "ell")
        self.assertEqual(design.roof, "stepped")
        self.assertIs(design.scheme, scheme)

    def test_a_plan_the_lot_cannot_hold_is_not_rolled(self):
        style = self.style(shapes=("you", "cross", "twin"))
        design = design_tower((0, 0, 16, 20), GROUND, 6, style)
        self.assertEqual(design.shape, "box")

    def test_a_roof_with_no_room_falls_back_on_a_flat_one(self):
        design = design_tower((0, 0, 12, 16), GROUND, 6, self.style(),
                              roof="crown", shape="box", tiers=3)
        self.assertIn(design.roof, ("flat", "crown"))
        self.laid(design)

    def test_what_is_no_design_is_refused(self):
        style = self.style()
        for args, kept, words in (
                (("x", GROUND, 8, None), {}, None),
                ((self.LOT, GROUND, 1, style), {}, "two stories"),
                ((self.LOT, GROUND, 8, style), {"front": "up"}, "front is"),
                ((self.LOT, GROUND, 8, style), {"shape": "ring"}, "shape is"),
                ((self.LOT, GROUND, 8, style), {"roof": "dome"}, "roof is"),
                (((0, 0, 8, 8), GROUND, 8, style), {}, "no room for a core"),
                (((0, 0, 16, 16), GROUND, 8, style), {"shape": "cross"},
                 "wants a lot")):
            with self.subTest(args=args[1:], kept=kept):
                with self.assertRaises((NBTError, TypeError)) as caught:
                    design_tower(*args, **kept)
                if words:
                    self.assertIn(words, str(caught.exception))

    def test_a_hip_wants_a_roof_to_be_cut_from(self):
        bare = Scheme(BRICKS, GLASS_PANE)
        with self.assertRaises(NBTError):
            TowerStyle("x", bare, roofs=("hip",))
        with self.assertRaises(NBTError):
            design_tower(self.LOT, GROUND, 8, TowerStyle("x", bare), roof="hip")

    def test_what_is_no_style_is_refused(self):
        scheme = plain_scheme()
        for kept, words in (({"window": "slit"}, "window is"),
                            ({"pitch": 7}, "pitch"),
                            ({"lobby": 3}, "lobby"),
                            ({"bay": 0}, "a bay"),
                            ({"step": 0}, "steps back"),
                            ({"tiers": ()}, "tiers"),
                            ({"roofs": ("dome",)}, "roofs"),
                            ({"shapes": ()}, "shapes")):
            with self.subTest(kept=kept):
                with self.assertRaises(NBTError) as caught:
                    TowerStyle("x", scheme, **kept)
                self.assertIn(words, str(caught.exception))
        with self.assertRaises(NBTError):
            TowerStyle("x", ())
        with self.assertRaises(TypeError):
            Scheme(BRICKS, GLASS_PANE, door=Mix([(BRICKS, 1), (STONE, 1)]))

    def test_it_says_what_it_is(self):
        design = design_tower(self.LOT, GROUND, 8, self.style())
        words = design.describe()
        self.assertIn("8 stories", words)
        self.assertIn("box plan", words)
        self.assertIn("bricks", words)
        self.assertIn("TowerDesign", repr(design))
        self.assertIsInstance(design, TowerDesign)
        self.assertGreater(design.top, GROUND + 8 * 4)


class StyleSampleTests(Flat):
    RADIUS = 6

    def test_each_family_stands_on_a_world_and_nothing_is_left_wrong(self):
        designs = []
        for index, style in enumerate(STYLES):
            x0 = -90 + (index % 3) * 60
            z0 = -80 + (index // 3) * 70
            design = design_tower((x0, z0, x0 + 32, z0 + 36), GROUND, 9, style,
                                  seed=index + 2)
            self.b.grade(design.plaza, GROUND, surface=SLATE, retain=None)
            design.lay(self.b)
            designs.append(design)
        report = self.b.finish()
        self.assertEqual(report.unsupported, [])
        self.save.save()
        fresh = MinecraftSave.load(self.save.path)
        for design in designs:
            with self.subTest(style=design.style.name):
                self.assertEqual(design.verify(fresh), [])
                box = ((design.plaza[0], GROUND - 1, design.plaza[1]),
                       (design.plaza[2], design.top + 2, design.plaza[3]))
                found = audit(fresh, *box)
                self.assertEqual(list(found.leaks), [])
                self.assertEqual(dict(found.heightmaps), {})
                x, y, z = design.doorstep
                walk = reachable(fresh, (x, y, z), box[0], box[1],
                                 allow_jump=False)
                self.assertGreater(len(walk.reached), 500)

    def test_a_family_can_be_repainted_after_it_is_read(self):
        self.assertEqual(GLASS.name, "glass")
        self.assertEqual(BRICK.pitch, 5)
        design = design_tower((0, 0, 24, 28), GROUND, 6, BRICK, seed=1)
        self.b.grade(design.plaza, GROUND, surface=SLATE, retain=None)
        design.lay(self.b)
        self.b.finish()
        read = Massing.read(self.save, (0, GROUND + 1, 0),
                            (24, design.massing.top, 28))
        self.assertTrue(read.tiers)


class SkylineTests(Flat):
    RADIUS = 6

    def test_a_dry_run_keeps_out_of_the_world(self):
        before = os.listdir(self.save.path)
        found = skyline.run(self.save.path, -60, -60, count=3,
                            out=lambda _: None)
        self.assertEqual(found["problems"], [])
        self.assertFalse(found["saved"])
        self.assertEqual(len(found["designs"]), 3)
        self.assertEqual(os.listdir(self.save.path), before)

    def test_a_row_of_towers_is_built_and_each_is_its_own(self):
        found = skyline.run(self.save.path, -60, -60, count=3, write=True,
                            out=lambda _: None)
        self.assertTrue(found["saved"])
        fresh = MinecraftSave.load(self.save.path)
        for design in found["designs"]:
            self.assertEqual(design.verify(fresh), [])
        self.assertGreater(len({str(design.scheme)
                                for design in found["designs"]}), 1)


if __name__ == "__main__":
    unittest.main()
