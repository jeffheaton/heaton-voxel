"""Tests for examples/road.py.

The route logic is pure (classify, smoothing, spans) and tested as such.
The integration test has a wrinkle worth knowing about: the fixture is open
ocean, which no grounded road can cross, so the test first raises a ridge of
known shape -- flat, a small hill, a big hill, a gap of untouched sea, a
shallow dip -- through a Mason, whose ``finish()`` refreshes the heightmaps.
The road's survey then reads that terrain the same way it would read a real
world's, and every station kind is forced in turn.
"""

from __future__ import annotations

import contextlib
import io
import os
import random
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "examples"))

import heaton_voxel.nbt as nbt
from castle import Mason  # noqa: E402
from fixtures import FIXTURE_LEVEL_DAT, FIXTURE_REGION  # noqa: E402
from heaton_voxel.nbt import NBT, TagCompound, TagDouble, TagList, TagString  # noqa: E402
from heaton_voxel.world import OVERWORLD, MinecraftSave  # noqa: E402
from road import (  # noqa: E402
    BOAT_ROOM,
    BRIDGE,
    CUT,
    GRADE,
    HEADROOM,
    MAX_BRIDGE,
    TUNNEL,
    RoadBuilder,
    Station,
    classify,
    facing_cardinal,
    is_vegetation,
    longest_run,
    main,
    plan_arches,
    smooth_kinds,
    survey_route,
    true_ground,
    usable_length,
)

ROAD_Y = 69
ROAD_X = -388  # the road runs south down this line
START_Z = 18


def make_world(tmpdir: str, *, player=None, yaw=0.0) -> str:
    """A fixture copy, with the player wherever the test needs them."""
    world = os.path.join(tmpdir, "world")
    region_dir = os.path.join(world, "dimensions/minecraft/overworld/region")
    os.makedirs(region_dir)
    shutil.copyfile(
        FIXTURE_REGION, os.path.join(region_dir, os.path.basename(FIXTURE_REGION))
    )
    shutil.copyfile(FIXTURE_LEVEL_DAT, os.path.join(world, "level.dat"))
    if player is not None:
        data_dir = os.path.join(world, "players", "data")
        os.makedirs(data_dir)
        record = NBT()
        record.kind = "nbt"
        record.compression = nbt.COMPRESSION_GZIP
        record.root = TagCompound(
            {
                "Pos": TagList(
                    [TagDouble(v) for v in player], element_type=nbt.TAG_DOUBLE
                ),
                "Rotation": TagList(
                    [nbt.TagFloat(yaw), nbt.TagFloat(0.0)],
                    element_type=nbt.TAG_FLOAT,
                ),
                "Dimension": TagString(OVERWORLD),
            }
        )
        record.save(os.path.join(data_dir, "test-player.dat"))
    return world


def raise_test_ridge(save: MinecraftSave) -> None:
    """Known terrain along the road line, one feature per stretch.

    Stations (z - 18): 0-5 flat, 6-8 small hill (cut), 9-11 flat, 12-17
    big hill (tunnel), 18-20 flat, 21-24 open sea left alone (bridge),
    25-26 shallow dip (embankment), 27-29 flat.
    """
    mason = Mason(save, save.dimension(OVERWORLD))
    wide = (ROAD_X - 7, ROAD_X + 7)
    flat = ROAD_Y

    def slab(z0, z1, top):
        mason.fill((wide[0], 45, z0), (wide[1], top, z1), "minecraft:stone")

    slab(16, 23, flat)
    slab(24, 26, flat + 4)  # the small hill: cut
    slab(27, 29, flat)
    slab(30, 35, flat + 9)  # the big hill: tunnel
    slab(36, 38, flat)
    # z 39..42 stays ocean: the gap the bridge crosses.
    slab(43, 44, flat - 3)  # the shallow dip: embankment
    slab(45, 47, flat)
    mason.finish()  # refresh heightmaps so the survey sees this terrain


class FacingTests(unittest.TestCase):
    def test_cardinals(self):
        self.assertEqual(facing_cardinal(0.0)[0], "south")
        self.assertEqual(facing_cardinal(90.0)[0], "west")
        self.assertEqual(facing_cardinal(180.0)[0], "north")
        self.assertEqual(facing_cardinal(270.0)[0], "east")

    def test_snapping_and_wrapping(self):
        """Logged-out players carry raw yaw like -437.6."""
        self.assertEqual(facing_cardinal(44.9)[0], "south")
        self.assertEqual(facing_cardinal(45.0)[0], "west")
        self.assertEqual(facing_cardinal(-90.0)[0], "east")
        self.assertEqual(facing_cardinal(-437.6)[0], "east")
        self.assertEqual(facing_cardinal(719.0)[0], "south")

    def test_vectors_match_names(self):
        for yaw, (dx, dz) in ((0, (0, 1)), (90, (-1, 0)), (180, (0, -1)), (270, (1, 0))):
            self.assertEqual(facing_cardinal(yaw)[1:], (dx, dz))


class ClassifyTests(unittest.TestCase):
    Y = 70

    def test_flat_ground_is_grade(self):
        self.assertEqual(classify(self.Y, self.Y, self.Y, False), GRADE)
        self.assertEqual(classify(self.Y, self.Y - 1, self.Y - 1, False), GRADE)

    def test_shallow_dips_are_graded_not_bridged(self):
        """Three or fewer air blocks under the bed get filled."""
        self.assertEqual(classify(self.Y, self.Y - 4, self.Y - 4, False), GRADE)
        self.assertEqual(classify(self.Y, self.Y - 5, self.Y - 5, False), BRIDGE)

    def test_the_dip_boundary_is_exact(self):
        # solid = Y-4 -> 3 air below the bed: still an embankment.
        self.assertEqual(classify(self.Y, self.Y - 4, self.Y - 4, False), GRADE)
        # solid = Y-5 -> 4 air below: a bridge.
        self.assertEqual(classify(self.Y, self.Y - 5, self.Y - 5, False), BRIDGE)

    def test_hills_cut_then_tunnel(self):
        self.assertEqual(classify(self.Y, self.Y + 1, self.Y, False), CUT)
        self.assertEqual(classify(self.Y, self.Y + 6, self.Y, False), CUT)
        self.assertEqual(classify(self.Y, self.Y + 7, self.Y, False), TUNNEL)
        self.assertEqual(classify(self.Y, self.Y + 40, self.Y, False), TUNNEL)

    def test_water_forces_a_bridge_over_everything_else(self):
        self.assertEqual(classify(self.Y, self.Y + 3, self.Y - 9, True), BRIDGE)

    def test_a_hill_beats_a_dip_on_a_knife_edge(self):
        """One road column on rock, one over a drop: cut the rock."""
        self.assertEqual(classify(self.Y, self.Y + 2, self.Y - 9, False), CUT)


class SmoothingTests(unittest.TestCase):
    def test_pinprick_skylights_are_closed(self):
        kinds = [TUNNEL] * 4 + [CUT] + [TUNNEL] * 4
        self.assertEqual(smooth_kinds(kinds), [TUNNEL] * 9)
        kinds = [TUNNEL] * 2 + [GRADE, GRADE, GRADE] + [TUNNEL] * 2
        self.assertEqual(smooth_kinds(kinds), [TUNNEL] * 7)

    def test_real_gaps_stay_open(self):
        kinds = [TUNNEL] * 3 + [CUT] * 4 + [TUNNEL] * 3
        self.assertEqual(smooth_kinds(kinds), kinds)

    def test_ends_are_never_closed(self):
        kinds = [CUT, CUT] + [TUNNEL] * 3 + [CUT, CUT]
        self.assertEqual(smooth_kinds(kinds), kinds)

    def test_longest_run(self):
        kinds = [BRIDGE, BRIDGE, GRADE, BRIDGE, BRIDGE, BRIDGE]
        self.assertEqual(longest_run(kinds, BRIDGE), 3)
        self.assertEqual(longest_run([], BRIDGE), 0)
        self.assertEqual(longest_run([GRADE], BRIDGE), 0)


class RoadRuleAgreementTests(unittest.TestCase):
    """``craft.RoadRule`` is this example's classifier promoted, its
    thresholds named; the example stays a demonstration of the
    deterministic library, so the two are held equal here instead."""

    def test_the_rule_is_the_examples_classifier(self):
        from heaton_voxel.craft import RoadRule
        from heaton_voxel.craft.build import (BRIDGE as R_BRIDGE, CUT as R_CUT,
                                              GRADE as R_GRADE,
                                              TUNNEL as R_TUNNEL)

        import road as example

        rule = RoadRule()
        self.assertEqual((rule.shallow_fill, rule.tunnel_from, rule.skylight),
                         (example.SHALLOW_FILL, example.TUNNEL_FROM, 3))
        self.assertEqual((R_GRADE, R_CUT, R_TUNNEL, R_BRIDGE),
                         (GRADE, CUT, TUNNEL, BRIDGE))
        differ = [(high, low, wet)
                  for wet in (False, True)
                  for high in range(55, 91)
                  for low in range(50, high + 1)
                  if rule.kind(70, high, low, wet) != classify(70, high, low, wet)]
        self.assertEqual(differ, [])
        # Skylights: a station standing for each kind, so the rule's kinds
        # are the sequence given, closed as smooth_kinds closes it.
        station = {GRADE: (70, 70, 70, False), CUT: (70, 72, 70, False),
                   TUNNEL: (70, 80, 70, False), BRIDGE: (70, 60, 60, False)}
        rng = random.Random(8)
        differ = []
        for _ in range(5000):
            kinds = [rng.choice((TUNNEL, TUNNEL, CUT, GRADE, BRIDGE))
                     for _ in range(rng.randrange(0, 24))]
            got = rule.kinds([station[kind] for kind in kinds])
            if list(got) != smooth_kinds(kinds):
                differ.append(kinds)
        self.assertEqual(differ, [])
        # A cell with no ground of its own takes the kind before it.
        self.assertEqual(rule.kinds([None, station[CUT], None, station[BRIDGE]]),
                         (GRADE, CUT, CUT, BRIDGE))
        self.assertEqual(RoadRule.stretches((GRADE, CUT, CUT, BRIDGE)),
                         (("grade", 0, 0), ("cut", 1, 2), ("bridge", 3, 3)))


class TruncationTests(unittest.TestCase):
    """An over-long span no longer refuses the route; it ends it."""

    def test_a_legal_route_is_usable_in_full(self):
        kinds = [GRADE] * 5 + [BRIDGE] * MAX_BRIDGE + [GRADE] * 5
        self.assertEqual(usable_length(kinds), len(kinds))

    def test_the_road_stops_at_the_near_shore(self):
        kinds = [GRADE] * 7 + [BRIDGE] * (MAX_BRIDGE + 1) + [GRADE] * 5
        self.assertEqual(usable_length(kinds), 7)

    def test_a_road_that_starts_on_an_impossible_span_is_zero(self):
        kinds = [BRIDGE] * (MAX_BRIDGE + 1)
        self.assertEqual(usable_length(kinds), 0)

    def test_earlier_legal_spans_do_not_confuse_the_cut(self):
        kinds = [BRIDGE] * 10 + [GRADE] * 3 + [BRIDGE] * (MAX_BRIDGE + 1)
        self.assertEqual(usable_length(kinds), 13)

    def test_the_limit_is_inclusive(self):
        self.assertEqual(usable_length([BRIDGE] * MAX_BRIDGE), MAX_BRIDGE)


class ArchPlanTests(unittest.TestCase):
    """Deck heights over a bridge run: up, across, down, walkable."""

    Y = 64

    def run_of(self, length, water_top):
        stations = [
            Station(i, 0, i, BRIDGE, self.Y - 9, self.Y - 9, water_top)
            for i in range(length)
        ]
        plan_arches(stations, self.Y)
        return [s.deck_y for s in stations]

    def test_a_dry_ravine_bridge_stays_level(self):
        decks = self.run_of(12, None)
        self.assertEqual(decks, [self.Y] * 12)

    def test_water_far_below_needs_no_arch(self):
        # Clearance is already better than BOAT_ROOM: stay level.
        decks = self.run_of(12, self.Y - BOAT_ROOM - 2)
        self.assertEqual(decks, [self.Y] * 12)

    def test_water_at_road_level_lifts_the_deck(self):
        water = self.Y - 1
        decks = self.run_of(14, water)
        peak = water + BOAT_ROOM + 1
        self.assertEqual(max(decks), peak)
        self.assertEqual(decks[0], self.Y, "the arch must join the road")
        self.assertEqual(decks[-1], self.Y)
        for a, b in zip(decks, decks[1:]):
            self.assertLessEqual(abs(a - b), 1, "a step no stair can climb")
        # The clearance the arch exists for: BOAT_ROOM air over the water.
        self.assertGreaterEqual(max(decks) - 1 - water, BOAT_ROOM)

    def test_a_short_span_rises_as_far_as_it_can(self):
        water = self.Y + 2  # a pond higher than the road: a big rise needed
        decks = self.run_of(5, water)
        self.assertEqual(max(decks), self.Y + 2, "capped by the run's length")
        self.assertEqual(decks, [self.Y, self.Y + 1, self.Y + 2, self.Y + 1, self.Y])

    def test_non_bridge_stations_keep_road_level(self):
        stations = [Station(0, 0, 0, GRADE, self.Y, self.Y)]
        plan_arches(stations, self.Y)
        self.assertEqual(stations[0].deck_y, self.Y)


class RouteTests(unittest.TestCase):
    """The full machine: survey, build, read the road back off disk."""

    LENGTH = 30

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="road-")
        cls.world = make_world(cls.dir)
        save = MinecraftSave.load(cls.world)
        raise_test_ridge(save)
        dim = save.dimension(OVERWORLD)

        cls.stations, unknown = survey_route(
            dim, ROAD_X, START_Z, 0, 1, ROAD_Y, cls.LENGTH
        )
        assert not unknown, f"test terrain left unknown stations: {unknown}"
        mason = Mason(save, dim)
        builder = RoadBuilder(mason, dim, ROAD_Y, 0, 1)
        builder.build(cls.stations)
        mason.finish()
        cls.report = mason.report
        cls.lamps = builder.lamps
        save.save()
        cls.save = MinecraftSave.load(cls.world)  # assert against the disk

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def kind_at(self, station_index: int) -> str:
        return self.stations[station_index].kind

    def block(self, x, y, z) -> str:
        state = self.save.get_block(x, y, z, dimension=OVERWORLD)
        self.assertIsNotNone(state, f"({x}, {y}, {z}) unexpectedly missing")
        return state.name

    def test_the_survey_classified_every_feature(self):
        kinds = [s.kind for s in self.stations]
        self.assertEqual(kinds[0:6], [GRADE] * 6)
        self.assertEqual(kinds[6:9], [CUT] * 3)
        self.assertEqual(kinds[12:18], [TUNNEL] * 6)
        self.assertEqual(kinds[21:25], [BRIDGE] * 4)
        self.assertEqual(kinds[25:27], [GRADE] * 2, "the dip should be graded")

    def test_the_road_is_level_stone_the_whole_way(self):
        for station in self.stations:
            for o in (-1, 0, 1):
                self.assertEqual(
                    self.block(station.x + o, ROAD_Y, station.z),
                    "minecraft:stone_bricks",
                    f"station {station.index} ({station.kind})",
                )
                self.assertEqual(
                    self.block(station.x + o, ROAD_Y + 1, station.z), "minecraft:air"
                )

    def test_the_cut_has_level_shoulders_and_tapered_walls(self):
        z = START_Z + 7  # mid-cut through the +4 hill
        for o in (2, 3):  # level shoulders both sides
            for side in (-1, 1):
                self.assertEqual(
                    self.block(ROAD_X + o * side, ROAD_Y, z), "minecraft:grass_block"
                )
                self.assertEqual(
                    self.block(ROAD_X + o * side, ROAD_Y + 1, z), "minecraft:air"
                )
        # One step past the shoulders the wall may stand one block higher.
        self.assertEqual(self.block(ROAD_X + 4, ROAD_Y + 1, z), "minecraft:grass_block")
        self.assertEqual(self.block(ROAD_X + 4, ROAD_Y + 2, z), "minecraft:air")

    def test_the_tunnel_is_bored_lined_and_lit(self):
        z = START_Z + 14  # mid-tunnel
        for dy in range(1, HEADROOM + 1):
            self.assertEqual(self.block(ROAD_X, ROAD_Y + dy, z), "minecraft:air")
        self.assertEqual(
            self.block(ROAD_X, ROAD_Y + HEADROOM + 1, z), "minecraft:stone_bricks"
        )
        for side in (-2, 2):
            self.assertEqual(
                self.block(ROAD_X + side, ROAD_Y + 2, z), "minecraft:stone_bricks"
            )
        self.assertGreater(self.report.blocks_placed, 0)
        torches = sum(
            1
            for z in range(START_Z + 12, START_Z + 18)
            for o in (-1, 1)
            if self.block(ROAD_X + o, ROAD_Y + 2, z) == "minecraft:wall_torch"
        )
        self.assertGreaterEqual(torches, 1, "a dark tunnel")

    def test_the_hill_still_stands_over_the_tunnel(self):
        """A tunnel bores; it must not scalp the hill above itself."""
        z = START_Z + 14
        self.assertEqual(
            self.block(ROAD_X, ROAD_Y + HEADROOM + 3, z), "minecraft:stone"
        )

    def test_the_bridge_is_railed_and_piered(self):
        z = START_Z + 22  # over the sea gap
        for side in (-2, 2):
            self.assertEqual(
                self.block(ROAD_X + side, ROAD_Y, z), "minecraft:stone_bricks"
            )
            self.assertEqual(
                self.block(ROAD_X + side, ROAD_Y + 1, z), "minecraft:stone_brick_wall"
            )
        self.assertEqual(
            self.block(ROAD_X, ROAD_Y - 8, z), "minecraft:water",
            "the sea should still flow under the deck",
        )
        pier_z = START_Z + 24  # station 24: a pier interval
        found = any(
            self.block(ROAD_X + side, ROAD_Y - 5, pier_z) == "minecraft:stone_bricks"
            for side in (-2, 2)
        )
        self.assertTrue(found, "no pier reaches the water")

    def test_the_dip_is_filled_with_stone_below(self):
        z = START_Z + 25
        self.assertEqual(self.kind_at(25), GRADE)
        self.assertEqual(self.block(ROAD_X, ROAD_Y - 1, z), "minecraft:stone")
        self.assertEqual(self.block(ROAD_X, ROAD_Y - 2, z), "minecraft:stone")

    def test_lamps_line_the_route(self):
        self.assertGreaterEqual(self.lamps, 2)
        lanterns = 0
        for station in self.stations:
            for side in (-2, 2):
                if self.block(station.x + side, ROAD_Y + 3, station.z) == "minecraft:lantern":
                    lanterns += 1
        self.assertEqual(lanterns, self.lamps)

    def test_everything_landed_on_generated_ground(self):
        self.assertTrue(self.report.complete, self.report.summary())

    def test_touched_chunks_were_relit(self):
        dim = self.save.dimension(OVERWORLD)
        chunk = dim.chunk(ROAD_X >> 4, (START_Z + 14) >> 4)
        self.assertEqual(int(chunk.data["isLightOn"]), 0)


class ArchBridgeTests(unittest.TestCase):
    """A bridge low over the water climbs for boat clearance and comes back.

    The pad stands one block proud of the fixture sea (road level 63, water
    at 62), so every span past it must arch: peak at 66, three blocks of
    air over the water beneath the deck, stairs on the ramps both ways.
    """

    PAD_Y = 63
    WATER = 62

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="road-")
        cls.world = make_world(
            cls.dir, player=(ROAD_X + 0.5, cls.PAD_Y + 1.0, 17.5), yaw=0.0
        )
        save = MinecraftSave.load(cls.world)
        mason = Mason(save, save.dimension(OVERWORLD))
        mason.fill((ROAD_X - 7, 45, 16), (ROAD_X + 7, cls.PAD_Y, 19), "minecraft:stone")
        mason.finish()
        save.save()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cls.code = main([cls.world, "20"])
        cls.out, cls.err = out.getvalue(), err.getvalue()
        cls.save = MinecraftSave.load(cls.world)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def block(self, x, y, z):
        return self.save.get_block(x, y, z, dimension=OVERWORLD).name

    def test_the_run_succeeded_and_reported_the_arch(self):
        self.assertEqual(self.code, 0, self.err)
        self.assertIn("arching 3 over the water", self.out)

    def test_the_ramp_is_stairs_and_the_crossing_is_level(self):
        # Stations: z 18-19 on the pad, the sea from z 20; arch k=0 at z 20.
        self.assertEqual(self.block(ROAD_X, self.PAD_Y, 20), "minecraft:stone_bricks")
        self.assertEqual(
            self.block(ROAD_X, self.PAD_Y + 1, 21), "minecraft:stone_brick_stairs"
        )
        self.assertEqual(
            self.block(ROAD_X, self.PAD_Y + 3, 28), "minecraft:stone_bricks",
            "mid-span should ride level at the peak",
        )
        self.assertEqual(
            self.block(ROAD_X, self.PAD_Y + 2, 35), "minecraft:stone_brick_stairs",
            "the far ramp must come back down",
        )
        # The last step down sits at road level and is itself a stair --
        # that is what makes the ramp climbable on the way back.
        self.assertEqual(
            self.block(ROAD_X, self.PAD_Y, 37), "minecraft:stone_brick_stairs"
        )

    def test_a_boat_fits_under_the_crossing(self):
        z = 28
        self.assertEqual(self.block(ROAD_X, self.WATER, z), "minecraft:water")
        for y in range(self.WATER + 1, self.WATER + 1 + BOAT_ROOM):
            self.assertEqual(self.block(ROAD_X, y, z), "minecraft:air", f"y={y}")

    def test_the_rails_follow_the_deck_up(self):
        z = 28
        self.assertEqual(
            self.block(ROAD_X + 2, self.PAD_Y + 4, z), "minecraft:stone_brick_wall"
        )


class VegetationTests(unittest.TestCase):
    """Trees are not terrain: fell them, never tunnel through them.

    Regression for a live-world bug: every heightmap counts trunks or
    leaves, so a tall tree read as an eight-block hill and the road roofed
    itself under the canopy. The route here is flat stone with an oak and
    an eleven-block megatrunk planted on the center line; the survey must
    call every station GRADE, and the build must fell both, canopy included.
    """

    LENGTH = 20

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="road-")
        cls.world = make_world(cls.dir)
        save = MinecraftSave.load(cls.world)
        mason = Mason(save, save.dimension(OVERWORLD))
        mason.fill((ROAD_X - 7, 45, 16), (ROAD_X + 7, ROAD_Y, 40), "minecraft:stone")
        # A small oak on the center line: trunk plus a persistent canopy.
        mason.fill((ROAD_X, ROAD_Y + 1, 24), (ROAD_X, ROAD_Y + 4, 24), "minecraft:oak_log[axis=y]")
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                mason.fill(
                    (ROAD_X + dx, ROAD_Y + 4, 24 + dz),
                    (ROAD_X + dx, ROAD_Y + 6, 24 + dz),
                    "minecraft:oak_leaves[persistent=true]",
                )
        # The megatrunk: tall enough that it used to classify as a tunnel.
        mason.fill((ROAD_X, ROAD_Y + 1, 30), (ROAD_X, ROAD_Y + 11, 30), "minecraft:spruce_log[axis=y]")
        mason.finish()
        dim = save.dimension(OVERWORLD)
        # Capture what the survey saw before the build changes the world.
        cls.trunk_ground = true_ground(dim, ROAD_X, 30)
        cls.bare_ground = true_ground(dim, ROAD_X, 20)
        cls.stations, unknown = survey_route(
            dim, ROAD_X, START_Z, 0, 1, ROAD_Y, cls.LENGTH
        )
        assert not unknown
        build_mason = Mason(save, dim)
        RoadBuilder(build_mason, dim, ROAD_Y, 0, 1).build(cls.stations)
        build_mason.finish()
        save.save()
        cls.save = MinecraftSave.load(cls.world)  # assert against the disk

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_true_ground_sees_through_the_trees(self):
        self.assertEqual(self.trunk_ground, (ROAD_Y, ROAD_Y + 11))
        self.assertEqual(self.bare_ground, (ROAD_Y, None))

    def test_no_station_is_a_tunnel_or_cut_for_a_tree(self):
        kinds = {s.kind for s in self.stations}
        self.assertEqual(kinds, {GRADE}, [s.kind for s in self.stations])

    def test_the_build_fells_the_trees_completely(self):
        for y in range(ROAD_Y + 1, ROAD_Y + 12):
            self.assertEqual(
                self.save.get_block(ROAD_X, y, 30, dimension=OVERWORLD).name,
                "minecraft:air", f"megatrunk survives at y={y}",
            )
        for y in range(ROAD_Y + 1, ROAD_Y + 7):
            self.assertEqual(
                self.save.get_block(ROAD_X, y, 24, dimension=OVERWORLD).name,
                "minecraft:air", f"oak survives at y={y}",
            )
        self.assertEqual(
            self.save.get_block(ROAD_X + 1, ROAD_Y + 5, 25, dimension=OVERWORLD).name,
            "minecraft:air", "canopy overhanging the shoulder survives",
        )
        self.assertEqual(
            self.save.get_block(ROAD_X, ROAD_Y, 30, dimension=OVERWORLD).name,
            "minecraft:stone_bricks",
        )

    def test_vegetation_names(self):
        for name in (
            "minecraft:oak_log", "minecraft:dark_oak_wood", "minecraft:spruce_leaves",
            "minecraft:mushroom_stem", "minecraft:snow", "minecraft:cactus",
        ):
            self.assertTrue(is_vegetation(name), name)
        for name in ("minecraft:stone", "minecraft:stone_bricks", "minecraft:snow_block"):
            self.assertFalse(is_vegetation(name), name)


class RefusalTests(unittest.TestCase):
    """What the road still refuses: blind ground and silly lengths."""

    def run_main(self, world, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([world, *argv])
        return code, out.getvalue(), err.getvalue()

    def test_a_route_into_ungenerated_ground_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="road-") as tmpdir:
            # Facing north from the fixture pad runs off the map at z < 0.
            world = make_world(
                tmpdir, player=(ROAD_X + 0.5, 70.0, 18.5), yaw=180.0
            )
            save = MinecraftSave.load(world)
            mason = Mason(save, save.dimension(OVERWORLD))
            mason.fill((ROAD_X - 7, 45, 16), (ROAD_X + 7, ROAD_Y, 19), "minecraft:stone")
            mason.finish()
            save.save()
            code, _, err = self.run_main(world, "40", "--dry-run")
            self.assertEqual(code, 1)
            self.assertIn("not generated", err)

    def test_length_limits(self):
        with tempfile.TemporaryDirectory(prefix="road-") as tmpdir:
            world = make_world(tmpdir, player=(0.5, 70.0, 0.5))
            for bad in ("501", "7", "0"):
                code, _, err = self.run_main(world, bad)
                self.assertEqual(code, 1, bad)
                self.assertIn("length", err)


class CliTests(unittest.TestCase):
    def test_a_real_run_builds_and_reports_the_route(self):
        with tempfile.TemporaryDirectory(prefix="road-") as tmpdir:
            world = make_world(tmpdir, player=(ROAD_X + 0.5, 70.0, 17.5), yaw=0.0)
            save = MinecraftSave.load(world)
            raise_test_ridge(save)
            save.save()
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main([world, "30"])
            self.assertEqual(code, 0, err.getvalue())
            text = out.getvalue()
            self.assertIn("facing south", text)
            self.assertIn("tunnel", text)
            self.assertIn("bridge", text)
            self.assertIn("wrote", text)
            fresh = MinecraftSave.load(world)
            self.assertEqual(
                fresh.get_block(ROAD_X, ROAD_Y, START_Z + 10, dimension=OVERWORLD).name,
                "minecraft:stone_bricks",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
