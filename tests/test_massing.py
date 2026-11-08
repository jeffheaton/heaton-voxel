"""Towers: tiers, the core through them, and the crown on top.

The building these rules were read off is somebody else's, a block of
Greenfield, so nothing of it is vendored: what is held here is the numbers
read off it, on 2026-11-01, and small towers made for the purpose. Its tiers
at their real rectangles, its 474 crown cells and the fins' stepped tops are
the numbers; ``Massing.read`` found the tiers from nothing but a box, and the
rules were replayed over it, 12,226 cells with none wrong.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heaton_voxel.craft import (  # noqa: E402
    Builder,
    Core,
    Crown,
    Facade,
    Ladder,
    Massing,
    Opening,
    SwitchbackStair,
    Tier,
    reachable,
)
from heaton_voxel.craft.build.massing import _outline  # noqa: E402
from heaton_voxel.craft.errors import Refused  # noqa: E402
from heaton_voxel.nbt import NBTError  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402
from tests.test_world import make_save  # noqa: E402

G = 50
BRICK = "minecraft:stone_bricks"
PLANKS = "minecraft:oak_planks"
WOOL = "minecraft:white_wool"
GLASS = "minecraft:light_blue_stained_glass"
LAMP = "minecraft:glowstone"
AIR = "minecraft:air"
WALL = Facade(BRICK)
DOOR = ("south", Opening.rect(3, 3), 19, 0)

#: The crown the hotel wears, on its 13-square tier.
PALACE_CROWN = dict(rings=((2, 2), (2, 3), (2, 4), (2, 5)), skin=GLASS,
                    corner=WOOL, liner=WOOL, fin=WOOL, reach=-1, inner=4,
                    rise=3, pitch=2, lamp=LAMP, lamps=4, cornice=WOOL,
                    brackets=2)


def small_stair():
    return SwitchbackStair((19, 19), (22, 22), axis="z", start="southwest",
                           start_y=G + 1, landing=1,
                           stair="minecraft:quartz_stairs",
                           wood="minecraft:quartz_block")


def small_core(**options):
    options.setdefault("stair", small_stair())
    return Core(18, 18, 23, 23, wall=WOOL, **options)


def small_massing(**options):
    """Two tiers on a ground floor, the first with a door to the south."""
    lower = dict(floor_y=G, top=G + 14, stories=(G + 8, G + 12), floor=PLANKS,
                 openings=(DOOR,))
    lower.update(options.pop("lower", {}))
    upper = dict(floor_y=G + 12, top=G + 23, stories=(G + 16, G + 20),
                 floor=PLANKS, deck=BRICK)
    upper.update(options.pop("upper", {}))
    return Massing(Tier(10, 10, 31, 31, WALL, **lower),
                   Tier(14, 14, 27, 27, WALL, **upper))


class Scene(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="massing-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, [(x, z) for x in range(0, 4) for z in range(0, 4)])
        self.save = MinecraftSave.load(self.dir)
        self.save.fill((0, G - 3, 0), (63, G, 63), "minecraft:stone")
        self.save.rebuild_heightmaps((0, 0, 0), (63, 0, 63))
        self.b = Builder(self.save)

    def name(self, x, y, z):
        return self.save.get_block(x, y, z).name


class OutlineTests(unittest.TestCase):
    def rectangle(self, x0, z0, x1, z1):
        return {(x, z) for x in range(x0, x1 + 1) for z in (z0, z1)} | {
            (x, z) for z in range(z0, z1 + 1) for x in (x0, x1)}

    def test_a_ring_is_its_rectangle(self):
        self.assertEqual(_outline(self.rectangle(2, 3, 9, 14)), (2, 3, 9, 14))

    def test_nothing_has_no_outline(self):
        self.assertIsNone(_outline(set()))

    def test_an_arcades_piers_are_not_the_wall_behind_them(self):
        piers = {(0, z) for z in range(0, 21, 8)} | {(20, z)
                                                     for z in range(0, 21, 8)}
        self.assertEqual(_outline(self.rectangle(4, 2, 16, 18) | piers),
                         (4, 2, 16, 18))

    def test_a_sign_on_a_wall_is_not_the_wall(self):
        sign = {(x, 21) for x in range(2, 20, 2)}
        self.assertEqual(_outline(self.rectangle(1, 1, 21, 20) | sign),
                         (1, 1, 21, 20))

    def test_a_crowns_fins_are_not_its_ring(self):
        fins = {(i, i) for i in range(0, 4)} | {(12 - i, 12 - i)
                                                for i in range(0, 4)} | {
            (i, 12 - i) for i in range(0, 4)} | {(12 - i, i)
                                                 for i in range(0, 4)}
        self.assertEqual(_outline(self.rectangle(4, 4, 8, 8) | fins),
                         (4, 4, 8, 8))


class MassingReadTests(Scene):
    def box(self, x0, z0, x1, z1, y0, y1, block=GLASS):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                for z in (z0, z1):
                    self.save.set_block(x, y, z, block)
            for z in range(z0, z1 + 1):
                for x in (x0, x1):
                    self.save.set_block(x, y, z, block)

    def test_each_outline_is_a_tier(self):
        self.box(10, 10, 30, 30, G + 1, G + 20)
        self.box(14, 14, 26, 26, G + 21, G + 30)
        self.box(18, 18, 22, 22, G + 31, G + 34)
        found = Massing.read(self.save, (0, G + 1, 0), (40, G + 40, 40))
        self.assertEqual([(t.rectangle, t.floor_y, t.top) for t in found.tiers],
                         [((10, 10, 30, 30), G, G + 20),
                          ((14, 14, 26, 26), G + 20, G + 30),
                          ((18, 18, 22, 22), G + 30, G + 34)])
        self.assertEqual(found.verify(self.save), [])
        self.assertIn("set back 4", found.describe())

    def test_its_rules_rebuild_what_they_were_read_from(self):
        self.box(10, 10, 30, 30, G + 1, G + 20)
        self.box(14, 14, 26, 26, G + 21, G + 30)
        found = Massing.read(self.save, (0, G + 1, 0), (40, G + 40, 40))
        for index, tier in enumerate(found.tiers):
            self.assertIsNotNone(tier.rule, index)
            for name, wall in tier.walls().items():
                self.assertEqual(tier.rule_of(name).verify(self.save, wall), [])

    def test_a_row_no_wall_can_be_said_of_is_a_tier_and_says_so(self):
        self.box(10, 10, 30, 30, G + 1, G + 8)
        self.box(9, 9, 31, 31, G + 9, G + 9)
        found = Massing.read(self.save, (0, G + 1, 0), (40, G + 12, 40))
        self.assertEqual([t.rectangle for t in found.tiers],
                         [(10, 10, 30, 30), (9, 9, 31, 31)])
        self.assertIn(1, found.unread)
        self.assertIsNone(found.tiers[1].rule)
        self.assertIn("walls not read", found.describe())
        self.assertEqual(found.verify(self.save), [])

    def test_rules_off_reads_only_the_outlines(self):
        self.box(10, 10, 30, 30, G + 1, G + 8)
        found = Massing.read(self.save, (0, G + 1, 0), (40, G + 12, 40),
                             rules=False)
        self.assertIsNone(found.tiers[0].rule)
        self.assertEqual(found.unread, {})

    def test_an_empty_box_says_so(self):
        with self.assertRaisesRegex(NBTError, "nothing stands"):
            Massing.read(self.save, (0, G + 1, 0), (40, G + 12, 40))


class TierTests(unittest.TestCase):
    def test_its_shape(self):
        tier = Tier(30, 40, 10, 20, WALL, floor_y=5, top=20)
        self.assertEqual(tier.rectangle, (10, 20, 30, 40))
        self.assertEqual((tier.width, tier.depth), (21, 21))
        self.assertEqual(tier.inside, (11, 21, 29, 39))
        self.assertEqual(list(tier.rows), list(range(6, 21)))

    def test_a_face_is_its_own_or_the_tiers(self):
        other = Facade(PLANKS)
        tier = Tier(0, 0, 9, 9, WALL, floor_y=0, top=5, faces={"north": other})
        self.assertIs(tier.rule_of("north"), other)
        self.assertIs(tier.rule_of("south"), WALL)

    def test_what_it_refuses(self):
        with self.assertRaisesRegex(NBTError, "Facade or an Elevation"):
            Tier(0, 0, 9, 9, "minecraft:stone", floor_y=0, top=5)
        with self.assertRaisesRegex(NBTError, "names the walls"):
            Tier(0, 0, 9, 9, WALL, floor_y=0, top=5, faces={"up": WALL})
        with self.assertRaisesRegex(NBTError, "Facade or an Elevation"):
            Tier(0, 0, 9, 9, faces={"north": "stone"}, floor_y=0, top=5)

    def test_a_massing_needs_tiers(self):
        with self.assertRaisesRegex(NBTError, "at least one"):
            Massing()
        with self.assertRaisesRegex(NBTError, "made of Tiers"):
            Massing("a tier")


class CoreTests(unittest.TestCase):
    def test_what_climbs_it_stands_inside_its_wall(self):
        with self.assertRaisesRegex(NBTError, "outside its wall"):
            Core(20, 20, 23, 23, wall=WOOL, stair=small_stair())
        with self.assertRaisesRegex(NBTError, "outside its wall"):
            Core(18, 18, 23, 23, wall=WOOL, ladder=Ladder(18, 20, 0, 9, on="west"))

    def test_a_door_is_in_a_face(self):
        with self.assertRaisesRegex(NBTError, "north, east, south"):
            Core(18, 18, 23, 23, wall=WOOL, doors={50: ("up", 19, 2)})
        with self.assertRaisesRegex(NBTError, r"\(face, at, width\)"):
            Core(18, 18, 23, 23, wall=WOOL, doors={50: "north"})

    def test_a_stair_says_where_its_doors_go(self):
        """Over the ground the end the first flight sets out from, and then
        the end each flight arrives at, as the office tower cut them."""
        core = small_core()
        self.assertEqual(core.door_of(G, G), ("south", 19, 4))
        self.assertEqual(core.door_of(G + 8, G), ("south", 19, 4))
        self.assertEqual(core.door_of(G + 12, G), ("north", 19, 4))
        self.assertEqual(core.door_of(G + 16, G), ("south", 19, 4))

    def test_a_floor_off_the_stair_is_said_to_be(self):
        with self.assertRaisesRegex(NBTError, "not where the stair lands"):
            small_core().door_of(G + 9, G)

    def test_doors_given_are_the_doors(self):
        core = small_core(doors={G: ("west", 20, 2)})
        self.assertEqual(core.door_of(G, G), ("west", 20, 2))
        self.assertIsNone(core.door_of(G + 8, G))

    def test_its_lamps_go_beside_the_run_at_every_floor_and_turn(self):
        core = small_core(lamp=LAMP)
        core.stair.span = (G + 1, G + 8)
        self.assertEqual(core.lamp_cells((G,)),
                         [(x, y, 20) for y in (G + 2, G + 6, G + 10)
                          for x in (18, 23)])


class TowerTests(Scene):
    def doorstep(self):
        return (20, G, 34)

    def test_it_stands_and_every_floor_is_walked_both_ways(self):
        result = self.b.tower(small_massing(), core=small_core(),
                              doorstep=self.doorstep())
        self.assertEqual(sorted(result.floors),
                         [G, G + 8, G + 12, G + 16, G + 20])
        self.b.finish()
        walk = reachable(self.save, self.doorstep(), (8, G - 1, 8),
                         (33, G + 25, 35), allow_jump=False)
        self.assertFalse(walk.stranded)
        for level, cells in result.floors.items():
            self.assertEqual([c for c in cells if c not in walk.reached], [],
                             level)
        self.assertEqual(small_massing().verify(self.save), [])

    def test_without_the_stair_the_walk_stops_at_the_ground(self):
        result = self.b.tower(small_massing(), core=small_core(),
                              doorstep=self.doorstep())
        self.b.finish()
        self.save.fill((19, G + 1, 19), (22, G + 20, 22), AIR)
        walk = reachable(self.save, self.doorstep(), (8, G - 1, 8),
                         (33, G + 25, 35))
        self.assertTrue(all(cell not in walk.reached
                            for cell in result.floors[G + 8]))

    def test_floors_stop_at_the_core_and_its_wall_runs_through_them(self):
        self.b.tower(small_massing(), core=small_core())
        self.assertEqual(self.name(17, G + 12, 20), PLANKS)
        self.assertEqual(self.name(18, G + 12, 20), WOOL)
        self.assertEqual(self.name(24, G + 12, 20), PLANKS)
        self.assertNotEqual(self.name(21, G + 16, 19), PLANKS)

    def test_a_door_opens_the_end_each_flight_arrives_at(self):
        self.b.tower(small_massing(), core=small_core())
        self.assertEqual(self.name(20, G + 9, 23), AIR)
        self.assertEqual(self.name(20, G + 9, 18), WOOL)
        self.assertEqual(self.name(20, G + 13, 18), AIR)
        self.assertEqual(self.name(20, G + 13, 23), WOOL)

    def test_a_terrace_is_the_part_of_its_floor_left_open(self):
        massing = small_massing(lower=dict(terrace=WOOL))
        result = self.b.tower(massing)
        self.assertEqual(self.name(12, G + 12, 12), WOOL)
        self.assertEqual(self.name(14, G + 12, 14), WOOL, "under the wall")
        self.assertEqual(self.name(15, G + 12, 15), PLANKS)
        self.assertEqual(self.name(12, G + 8, 12), PLANKS)
        self.assertIn((12, G + 12, 12), result.terraces[G + 12])
        self.assertNotIn((15, G + 12, 15), result.terraces[G + 12])

    def test_a_tower_that_cannot_stand_writes_nothing(self):
        self.save.set_block(14, G + 18, 20, "minecraft:gold_block")
        with self.assertRaisesRegex(Refused, "already taken") as caught:
            self.b.tower(small_massing(), core=small_core())
        self.assertEqual(len(caught.exception.faults), 1)
        self.assertEqual(self.name(10, G + 1, 10), AIR,
                         "the first tier never went up")
        self.assertEqual(self.name(19, G + 1, 22), AIR, "nor the stair")

    def test_overwrite_builds_through(self):
        self.save.set_block(14, G + 18, 20, "minecraft:gold_block")
        self.b.tower(small_massing(), overwrite=True)
        self.assertEqual(self.name(14, G + 18, 20), BRICK)

    def test_a_tower_nobody_can_walk_is_refused_before_it_is_written(self):
        core = small_core(doors={})
        with self.assertRaisesRegex(Refused, "nobody can walk") as caught:
            self.b.tower(small_massing(), core=core, doorstep=self.doorstep())
        self.assertGreaterEqual(len(caught.exception.faults), 4)
        self.assertEqual(self.name(10, G + 1, 10), AIR)

    def test_a_ladder_core_is_walked_up_and_down(self):
        tall = G + 20
        core = Core(18, 18, 23, 23, wall=WOOL,
                    ladder=Ladder(19, 19, G + 1, tall + 1, on="west"),
                    doors={level: ("north", 19, 1)
                           for level in (G, G + 8, G + 12, G + 16, tall)})
        result = self.b.tower(small_massing(), core=core,
                              doorstep=self.doorstep())
        self.assertEqual(self.save.get_block(19, G + 5, 19).properties["facing"],
                         "east")
        self.assertEqual(result.walk.stranded, frozenset())

    def test_a_ladder_that_stops_short_of_a_floor_is_found(self):
        tall = G + 20
        core = Core(18, 18, 23, 23, wall=WOOL,
                    ladder=Ladder(19, 19, G + 1, G + 13, on="west"),
                    doors={level: ("north", 19, 1)
                           for level in (G, G + 8, G + 12, G + 16, tall)})
        with self.assertRaisesRegex(Refused, f"floor at y={G + 16}"):
            self.b.tower(small_massing(), core=core, doorstep=self.doorstep())

    def test_a_crown_stands_on_the_last_tier(self):
        crown = Crown(((2, 2), (2, 4)), skin=GLASS)
        massing = small_massing(upper=dict(deck=None))
        self.b.tower(massing, crown=crown)
        self.assertEqual(self.name(16, G + 24, 16), GLASS)
        self.assertEqual(self.name(18, G + 26, 18), GLASS)
        self.assertEqual(crown.verify(self.save, 14, 14, 27, 27, G + 24), [])

    def test_a_floor_off_the_stair_is_refused_before_anything_is_read(self):
        massing = small_massing(lower=dict(stories=(G + 9, G + 12)))
        with self.assertRaisesRegex(NBTError, "not where the stair lands"):
            self.b.tower(massing, core=small_core())

    def test_a_core_that_will_not_fit_is_refused(self):
        tight = Massing(Tier(10, 10, 31, 31, WALL, floor_y=G, top=G + 8),
                        Tier(19, 19, 28, 28, WALL, floor_y=G + 9, top=G + 16))
        core = Core(18, 18, 23, 23, wall=WOOL)
        with self.assertRaisesRegex(NBTError, "does not fit"):
            self.b.tower(tight, core=core)

    def test_a_tier_stands_on_a_floor(self):
        massing = Massing(
            Tier(10, 10, 31, 31, WALL, floor_y=G, top=G + 10),
            Tier(14, 14, 27, 27, WALL, floor_y=G + 7, top=G + 16))
        with self.assertRaisesRegex(NBTError, "has no floor there"):
            self.b.tower(massing)

    def test_a_tier_stands_on_the_row_over_the_walls_under_it(self):
        massing = Massing(
            Tier(10, 10, 31, 31, WALL, floor_y=G, top=G + 10, deck=BRICK),
            Tier(14, 14, 27, 27, WALL, floor_y=G + 11, top=G + 16))
        self.b.tower(massing)
        self.assertEqual(self.name(14, G + 12, 14), BRICK)

    def test_a_tier_reaching_past_the_one_under_it_is_refused(self):
        massing = Massing(
            Tier(14, 14, 27, 27, WALL, floor_y=G, top=G + 10, stories=(G + 7,)),
            Tier(10, 10, 31, 31, WALL, floor_y=G + 7, top=G + 16))
        with self.assertRaisesRegex(NBTError, "reaches past"):
            self.b.tower(massing)

    def test_a_tier_read_without_a_rule_cannot_be_built(self):
        massing = Massing(Tier(10, 10, 31, 31, floor_y=G, top=G + 10))
        with self.assertRaisesRegex(NBTError, "no rule"):
            self.b.tower(massing)

    def test_it_takes_what_it_says_it_takes(self):
        with self.assertRaisesRegex(NBTError, "Massing"):
            self.b.tower("a tower")
        with self.assertRaisesRegex(NBTError, "core is a Core"):
            self.b.tower(small_massing(), core="a core")
        with self.assertRaisesRegex(NBTError, "crown is a Crown"):
            self.b.tower(small_massing(), crown="a crown")

    def test_a_second_tower_call_finds_every_cell_right(self):
        self.b.tower(small_massing(), core=small_core())
        again = self.b.tower(small_massing(), core=small_core())
        self.assertEqual(again.already, again.blocks)


class LadderTests(Scene):
    def test_it_is_named_by_the_wall_it_hangs_on(self):
        """Facing is the way out from the wall: the Roman town's 337 ladders
        said it the other way round, and every one faced into its wall."""
        rule = Ladder(5, 6, 10, 20, on="north")
        self.assertEqual(rule.state.properties["facing"], "south")
        self.assertEqual(rule.wall, (5, 5))
        self.assertEqual(sorted(rule.cells()),
                         [(5, y, 6) for y in range(10, 21)])

    def test_a_side_is_a_compass_word(self):
        with self.assertRaisesRegex(NBTError, "north, east, south or west"):
            Ladder(0, 0, 0, 5, on="up")

    def test_it_is_hung_on_its_wall_and_stays(self):
        self.save.fill((6, G + 1, 6), (6, G + 6, 6), BRICK)
        rule = Ladder(6, 7, G + 1, G + 6, on="north")
        self.assertEqual(self.b.ladder(rule), 6)
        self.assertEqual(rule.verify(self.save), [])
        self.assertEqual(self.b.finish().unsupported, [])

    def test_one_with_nothing_to_hang_on_is_refused(self):
        with self.assertRaisesRegex(Refused, "will not keep"):
            self.b.ladder(Ladder(6, 7, G + 1, G + 6, on="north"))
        self.assertEqual(self.name(6, G + 1, 7), AIR)

    def test_something_in_the_column_is_refused_by_name(self):
        self.save.fill((6, G + 1, 6), (6, G + 6, 6), BRICK)
        self.save.set_block(6, G + 3, 7, "minecraft:gold_block")
        with self.assertRaisesRegex(Refused, "gold_block"):
            self.b.ladder(Ladder(6, 7, G + 1, G + 6, on="north"))

    def test_it_takes_a_ladder(self):
        with self.assertRaisesRegex(NBTError, "takes a Ladder"):
            self.b.ladder("a ladder")


class CrownTests(Scene):
    def plan(self):
        return Crown(**PALACE_CROWN).plan(0, 0, 12, 12, 100)

    def rows(self, plan):
        out: dict = {}
        for (x, y, z), state in plan.items():
            out.setdefault(y, {})[(x, z)] = state.name
        return out

    def test_it_is_the_hotels_cell_for_cell_count(self):
        """474 cells of the crown as it stands, 470 of them as it stands:
        the other four are the hatch out of its first ring."""
        self.assertEqual(len(self.plan()), 474)
        self.assertEqual(Crown(**PALACE_CROWN).height, 13)

    def test_the_rings_step_in_and_are_lined(self):
        rows = self.rows(self.plan())
        ring = [(x, 2) for x in range(2, 11)]
        self.assertEqual([rows[100][cell] for cell in ring],
                         [WOOL] + [GLASS] * 7 + [WOOL])
        self.assertEqual([rows[100][(x, 3)] for x in range(3, 10)], [WOOL] * 7)
        self.assertEqual(rows[102][(3, 3)], WOOL)
        self.assertEqual(rows[102][(4, 3)], GLASS)
        self.assertEqual(rows[104][(4, 4)], WOOL)
        self.assertEqual(rows[104][(5, 4)], GLASS)

    def test_a_ring_three_wide_is_all_skin_round_its_liner(self):
        rows = self.rows(self.plan())
        for cell in ((5, 5), (6, 5), (7, 5), (5, 6), (7, 6), (5, 7), (7, 7)):
            self.assertEqual(rows[106][cell], GLASS, cell)
        self.assertEqual(rows[106][(6, 6)], WOOL)

    def test_a_fin_climbs_two_rows_a_column(self):
        tops = {}
        for (x, y, z) in self.plan():
            if x == z and y >= 100:
                tops[x] = max(tops.get(x, 0), y)
        self.assertEqual([tops[i] for i in (-1, 0, 1, 2, 3, 4)],
                         [102, 104, 106, 108, 110, 112],
                         "the hotel's tops, y 126 up to 136 at two a step")

    def test_the_last_steps_carry_lamps(self):
        plan = self.plan()
        lamps = sorted(cell for cell, state in plan.items()
                       if state.name == LAMP)
        self.assertEqual(len(lamps), 16)
        self.assertEqual({y for _, y, _ in lamps}, {106, 108, 110, 112})
        self.assertIn((1, 106, 1), lamps)
        self.assertIn((11, 112 - 6, 1), lamps)

    def test_the_fin_stops_at_the_ring(self):
        rows = self.rows(self.plan())
        self.assertEqual(rows[100][(1, 1)], WOOL)
        self.assertEqual(rows[100][(2, 2)], WOOL, "the ring's own corner")
        self.assertNotIn((3, 3), {k for k in rows[106] if k != (3, 3)})

    def test_the_cornice_stands_on_brackets(self):
        rows = self.rows(self.plan())
        self.assertEqual(len([c for c in rows[99]]), 56)
        self.assertEqual(sorted(rows[98]), sorted(rows[97]))
        self.assertEqual(sorted(rows[98]),
                         sorted([(-1, 0), (0, -1), (13, 0), (12, -1),
                                 (-1, 12), (0, 13), (13, 12), (12, 13)]))

    def test_a_deck_closes_the_top(self):
        plan = Crown(((2, 2),), skin=GLASS, deck=WOOL).plan(0, 0, 12, 12, 100)
        self.assertEqual(plan[(6, 99, 6)].name, WOOL)
        self.assertNotIn((0, 99, 0), plan)

    def test_any_rectangle_takes_it(self):
        plan = Crown(((2, 1), (2, 2)), skin=GLASS).plan(0, 0, 8, 20, 10)
        self.assertIn((1, 10, 19), plan)
        self.assertIn((2, 12, 18), plan)

    def test_what_it_refuses(self):
        with self.assertRaisesRegex(NBTError, "at least one ring"):
            Crown((), skin=GLASS)
        with self.assertRaisesRegex(NBTError, "step inward"):
            Crown(((2, 3), (2, 1)), skin=GLASS)
        with self.assertRaisesRegex(NBTError, "at least one row"):
            Crown(((0, 3),), skin=GLASS)
        with self.assertRaisesRegex(NBTError, r"\(rows, inset\)"):
            Crown((3,), skin=GLASS)
        with self.assertRaisesRegex(NBTError, "pass reach="):
            Crown(((2, 3),), skin=GLASS, fin=WOOL)
        with self.assertRaisesRegex(NBTError, "no fin="):
            Crown(((2, 3),), skin=GLASS, reach=-1)
        with self.assertRaisesRegex(NBTError, "no lamp="):
            Crown(((2, 3),), skin=GLASS, fin=WOOL, reach=0, lamps=1)
        with self.assertRaisesRegex(NBTError, "no room"):
            Crown(((2, 3),), skin=GLASS, fin=WOOL, reach=0, inner=1, lamp=LAMP,
                  lamps=3)
        with self.assertRaisesRegex(NBTError, "short of"):
            Crown(((2, 3),), skin=GLASS, fin=WOOL, reach=2, inner=1)
        with self.assertRaisesRegex(NBTError, "does not fit"):
            Crown(((2, 4),), skin=GLASS).plan(0, 0, 6, 6, 10)

    def test_the_writer(self):
        crown = Crown(**PALACE_CROWN)
        self.assertEqual(self.b.crown(crown, 20, 20, 32, 32, G + 4), 474)
        self.assertEqual(crown.verify(self.save, 20, 20, 32, 32, G + 4), [])
        self.assertEqual(self.b.crown(crown, 20, 20, 32, 32, G + 4), 0)

    def test_the_writer_refuses_what_is_in_the_way(self):
        self.save.set_block(23, G + 8, 22, "minecraft:gold_block")
        with self.assertRaisesRegex(Refused, "gold_block"):
            self.b.crown(Crown(**PALACE_CROWN), 20, 20, 32, 32, G + 8)
        self.assertEqual(self.name(22, G + 8, 22), AIR)

    def test_the_writer_takes_a_crown(self):
        with self.assertRaisesRegex(NBTError, "takes a Crown"):
            self.b.crown("a crown", 0, 0, 12, 12, 100)

    def test_verify_finds_a_cell_gone(self):
        crown = Crown(**PALACE_CROWN)
        self.b.crown(crown, 20, 20, 32, 32, G + 4)
        self.save.set_block(22, G + 4, 22, AIR)
        self.assertEqual(len(crown.verify(self.save, 20, 20, 32, 32, G + 4)), 1)


if __name__ == "__main__":
    unittest.main()
