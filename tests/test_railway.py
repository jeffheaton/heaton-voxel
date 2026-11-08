"""Tests for the railway rule and builder, in heaton_voxel.craft.build.

A rail line keeps its promises in places no picture shows them. A rail
needs a *rigid* top face under it -- leaves, a fence, a bottom slab and a
path block all hold a player and drop a rail at the next block update. A
climbing rail is the low cell of a step, and the low cell of a step needs
three clear blocks over its support, not two, or the line walks one way. A
tunnel open to a lake fills again however well it is drained. And a rail
already standing in the way is somebody's line.

The miniature here is the Stronghold link in JeffWorld, scaled down: a
station stop to keep, a ramp off the platform, a causeway over a lake, a
portal into a bore somebody already dug whose mouth is open to that lake,
a ramp out at the far end, and the surviving line with a gap in it.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest

from fixtures import block_shapes
from heaton_voxel.blocks import BlockState
from heaton_voxel.craft.build import (
    HEAD_ROOM,
    Builder,
    Railway,
    RailwayResult,
    _holds_rail_top,
    _settled,
    _wet,
)
from heaton_voxel.craft.errors import Refused
from heaton_voxel.nbt import NBTError
from heaton_voxel.world import MinecraftSave
from tests.test_world import make_save

CHUNKS = [(x, z) for x in range(-1, 5) for z in range(-1, 2)]
Z = 8
STONE = "minecraft:stone"
RAIL_EW = "minecraft:rail[shape=east_west,waterlogged=false]"
STOP = "minecraft:powered_rail[powered=false,shape=east_west,waterlogged=false]"

#: The miniature link: platform, causeway, portal, bore, ramp, old line.
LINK = [(3, 66, Z), (6, 63, Z), (19, 63, Z), (22, 60, Z), (39, 60, Z),
        (43, 64, Z), (56, 64, Z)]


class RailBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="railway-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, CHUNKS)
        self.save = MinecraftSave.load(self.dir)
        self.b = Builder(self.save)

    def block(self, x, y, z=Z):
        return self.save.get_block(x, y, z)

    def name(self, x, y, z=Z):
        return self.block(x, y, z).name

    def lay_link_country(self):
        """The ground the miniature link runs through."""
        s = self.save
        s.fill((0, 50, 0), (63, 62, 15), STONE)              # ground to 62
        s.fill((0, 63, 0), (3, 65, 15), STONE)               # the platform
        for x in range(0, 3):
            s.set_block(x, 66, Z, RAIL_EW)
        s.set_block(3, 66, Z, STOP)                          # a station stop
        s.fill((6, 58, 0), (20, 62, 15), "minecraft:water")  # the lake
        s.fill((21, 63, 0), (43, 70, 15), STONE)             # the hill
        s.fill((21, 60, Z), (40, 61, Z), "minecraft:air")    # the old bore
        s.fill((21, 60, Z), (25, 61, Z), "minecraft:water")  # flooded mouth
        s.fill((44, 63, Z), (56, 63, Z), "minecraft:cobblestone")
        for x in range(44, 57):
            if x not in (48, 49, 50, 52):                    # the gaps
                s.set_block(x, 64, Z, RAIL_EW)
        s.set_block(52, 63, Z, "minecraft:air")              # bed gone too


class GeometryTests(unittest.TestCase):
    def test_shapes_climb_toward_the_higher_neighbor(self):
        line = Railway([(0, 64, 0), (3, 64, 0), (6, 67, 0), (9, 67, 0)])
        shapes = dict(zip((c[0] for c in line.cells), line.shapes))
        self.assertEqual(shapes[0], "east_west")
        self.assertEqual(shapes[3], "ascending_east",
                         "the foot of a ramp already climbs")
        self.assertEqual(shapes[5], "ascending_east")
        self.assertEqual(shapes[6], "east_west", "the top is level")
        down = Railway([(9, 67, 0), (6, 67, 0), (3, 64, 0)])
        self.assertIn("ascending_east", down.shapes,
                      "a rail's shape does not care which way you build it")

    def test_a_corner_is_a_curve_named_north_south_first(self):
        line = Railway([(0, 64, 0), (4, 64, 0), (4, 64, 4)])
        self.assertEqual(line.shapes[4], "south_west")
        line = Railway([(4, 64, 4), (4, 64, 0), (8, 64, 0)])
        self.assertEqual(line.shapes[4], "south_east")

    def test_what_no_rail_can_do_is_refused_rather_than_guessed(self):
        cases = {
            "diagonal": [(0, 64, 0), (3, 64, 3)],
            "climb in place": [(0, 64, 0), (0, 66, 0)],
            "one block per block": [(0, 64, 0), (6, 66, 0)],
            "climb both ways": [(0, 66, 0), (2, 64, 0), (4, 66, 0)],
            "turns and climbs": [(0, 64, 0), (3, 64, 0), (3, 67, 3)],
            "twice": [(0, 64, 0), (4, 64, 0), (4, 64, 2), (2, 64, 2),
                      (2, 68, -2)],
        }
        for why, points in cases.items():
            with self.subTest(why), self.assertRaises(NBTError) as caught:
                Railway(points)
            self.assertIn(why.split()[0], str(caught.exception))

    def test_a_rider_needs_two_clear_blocks_and_a_bed_must_hold_a_rail(self):
        with self.assertRaises(NBTError):
            Railway([(0, 64, 0), (4, 64, 0)], clearance=1)
        with self.assertRaises(NBTError):
            Railway([(0, 64, 0), (4, 64, 0)], bed="minecraft:oak_leaves")

    def test_every_climb_is_boosted_and_the_level_at_a_spacing(self):
        line = Railway([(0, 64, 0), (20, 64, 0), (23, 67, 0), (40, 67, 0)],
                       boost_every=6)
        climbs = {c for c, s in zip(line.cells, line.shapes)
                  if s.startswith("ascending_")}
        self.assertTrue(climbs <= set(line.boosters))
        level = sorted(c[0] for c in line.boosters if c not in climbs)
        self.assertEqual(level[:3], [5, 11, 17], "every sixth level cell")
        self.assertEqual(
            line.state(line.cells.index((5, 64, 0))),
            BlockState("minecraft:powered_rail", {
                "powered": "true", "shape": "east_west",
                "waterlogged": "false"}))
        self.assertEqual(Railway([(0, 64, 0), (6, 64, 0), (9, 67, 0)],
                                 boost_every=None)
                         .boosters, ())

    def test_a_booster_due_on_a_corner_waits_for_a_straight_cell(self):
        line = Railway([(0, 64, 0), (4, 64, 0), (4, 64, 4)], boost_every=5)
        self.assertNotIn((4, 64, 0), line.boosters, "a powered rail cannot curve")
        self.assertIn((4, 64, 1), line.boosters)

    def test_the_low_side_of_a_step_gets_the_third_block(self):
        line = Railway([(0, 64, 0), (3, 64, 0), (5, 66, 0), (8, 66, 0)])
        rooms = {c[0]: line.head_room(i) for i, c in enumerate(line.cells)}
        self.assertEqual(rooms[2], 2, "the flat needs two")
        self.assertEqual(rooms[3], HEAD_ROOM, "the foot of the ramp")
        self.assertEqual(rooms[4], HEAD_ROOM)
        self.assertEqual(rooms[5], 2, "the top of the ramp needs nothing more")
        self.assertEqual(line.running_space(3),
                         ((3, 64, 0), (3, 65, 0), (3, 66, 0)))


class SupportRuleTests(unittest.TestCase):
    """The rule a rail stands on, held to the game on every block state.

    ``fixtures/block_shapes.json`` is the game's own ``isFaceSturdy(UP,
    RIGID)``, ``Fallable`` and fluid state for each of them, written by
    ``tools/probe_block_shapes.py``. After a game update a new block that
    the tables name wrongly fails here by name.
    """

    def test_the_rule_is_the_games_on_every_state(self):
        wrong = [f"{name}{props}" for name, props, shape in block_shapes()
                 if _holds_rail_top(name, props) != shape.rigid]
        self.assertEqual(wrong[:5], [], f"{len(wrong)} states disagree with "
                                        f"the game's rigid top face")

    def test_what_holds_water_and_what_falls_are_the_games_answers(self):
        air = BlockState("minecraft:air")
        wet, falls = [], []
        for name, props, shape in block_shapes():
            state = BlockState(name, props)
            if _wet(state) != shape.fluid:
                wet.append(str(state))
            # Only a block that can hold a rail is ever a support worth
            # asking about.
            if shape.rigid and _settled(state, air) == shape.falls:
                falls.append(str(state))
        self.assertEqual(wet[:5], [], f"{len(wet)} states disagree on water")
        self.assertEqual(falls[:5], [], f"{len(falls)} supports disagree on "
                                        f"falling")

    def test_a_state_the_fixture_cannot_show_is_wet_as_the_game_reads_it(self):
        """Every state in the fixture names ``waterlogged`` outright, and
        every one is a block the game has today, so the test above cannot
        see a state that leaves it out, a retired name or a modded block.
        A sea pickle is waterlogged unless it says otherwise, so a bare one
        is wet, as the game reads it."""
        self.assertTrue(_wet(BlockState("minecraft:sea_pickle")))
        self.assertFalse(_wet(BlockState("minecraft:sea_pickle",
                                         {"waterlogged": "false"})))
        self.assertFalse(_wet(BlockState("minecraft:oak_stairs")))
        self.assertTrue(_wet(BlockState("minecraft:water")))
        # An old chunk's chain is the iron chain the game renames it to, and
        # a modded block the tables have never heard of is taken at its
        # word: a line that missed either's water would flood.
        self.assertTrue(_wet(BlockState("minecraft:chain",
                                        {"axis": "y", "waterlogged": "true"})))
        self.assertTrue(_wet(BlockState("mymod:pipe", {"waterlogged": "true"})))
        self.assertFalse(_wet(BlockState("mymod:pipe")))
        self.assertFalse(_wet(BlockState("minecraft:stone",
                                         {"waterlogged": "true"})),
                         "stone has no waterlogged, whatever it is told")

    def test_what_holds_a_rail(self):
        holds = {
            "minecraft:grass_block": True, "minecraft:glass": True,
            "minecraft:crimson_stem": True, "minecraft:scaffolding": True,
            "minecraft:oak_leaves": False, "minecraft:oak_fence": False,
            "minecraft:cobblestone_wall": False, "minecraft:dirt_path": False,
            "minecraft:farmland": False, "minecraft:chest": False,
            "minecraft:candle": False, "minecraft:glass_pane": False,
            "minecraft:oak_slab[type=top]": True,
            "minecraft:oak_slab[type=bottom]": False,
            "minecraft:stone_stairs[half=top]": True,
            "minecraft:stone_stairs[half=bottom]": False,
            "minecraft:oak_trapdoor[half=top,open=false]": True,
            "minecraft:oak_trapdoor[half=top,open=true]": False,
            "minecraft:snow[layers=8]": True, "minecraft:snow[layers=7]": False,
            "minecraft:piston[extended=true,facing=up]": False,
            "minecraft:piston[extended=true,facing=down]": True,
            "minecraft:piston_head[facing=up]": True,
        }
        for text, expected in holds.items():
            state = BlockState.parse(text)
            with self.subTest(text):
                self.assertIs(_holds_rail_top(state.name, state.properties),
                              expected)


class LinkTests(RailBase):
    def setUp(self):
        super().setUp()
        self.lay_link_country()
        self.line = Railway(LINK, boost_every=8)

    def test_water_touching_the_line_is_refused_before_anything_is_written(self):
        with self.assertRaises(NBTError) as caught:
            self.b.railway(self.line)
        self.assertIn("seal=", str(caught.exception))
        self.assertEqual(self.name(22, 60), "minecraft:water",
                         "a refusal writes nothing")
        self.assertEqual(self.b.blocks_set, 0)

    def test_the_link_is_laid_and_holds_up_on_disk(self):
        result = self.b.railway(self.line, seal="minecraft:cobblestone")
        self.assertIsInstance(result, RailwayResult)
        self.assertEqual(result.sealed, 2, "the lake on either side of the portal")
        self.assertEqual(result.kept, 1)
        self.assertEqual(result.already, 9)
        self.assertEqual(result.laid + result.kept + result.already, len(self.line))
        self.assertEqual(self.line.verify(self.save), [])

        # The station stop is somebody's on purpose: kept, still unpowered.
        self.assertEqual(self.block(3, 66), BlockState.parse(STOP))
        # The bore's flooded mouth is dry, and its foot has the third block.
        self.assertEqual(self.name(23, 61), "minecraft:air")
        self.assertEqual(self.name(39, 62), "minecraft:air")
        self.assertEqual(self.name(38, 62), STONE, "the flat keeps its roof")
        # Boosters stand on their power; the lake under the causeway is bed.
        self.assertEqual(self.name(14, 62), "minecraft:redstone_block")
        self.assertEqual(self.name(13, 62), "minecraft:cobblestone")
        self.assertEqual(self.name(20, 62, Z - 1), "minecraft:cobblestone")

        self.b.finish(connect=False)
        self.save.save()
        self.assertEqual(self.line.verify(MinecraftSave.load(self.dir)), [])

    def test_a_seal_that_would_not_stay_a_wall_is_refused(self):
        """Sand and gravel are full blocks that fall wherever nothing holds
        them up, and a seal stands where the lake stood; ice melts back
        into water; a sponge soaks the lake up. Each is refused, naming
        why, before anything is written -- the seal rule every writer's
        seal is held to -- and cobblestone walls the lake off."""
        for seal, why in (("minecraft:sand", "falls"),
                          ("minecraft:gravel", "falls"),
                          ("minecraft:ice", "melts"),
                          ("minecraft:frosted_ice", "melts"),
                          ("minecraft:sponge", "soaks up")):
            with self.subTest(seal=seal):
                with self.assertRaises(NBTError) as caught:
                    self.b.railway(self.line, seal=seal)
                message = str(caught.exception)
                self.assertIn("would not stay a wall", message)
                self.assertIn(why, message)
                self.assertEqual(self.b.blocks_set, 0)
                self.assertEqual(self.name(22, 60), "minecraft:water")
        result = self.b.railway(self.line, seal="minecraft:packed_ice")
        self.assertEqual(result.sealed, 2)

    def test_a_seal_water_can_enter_is_refused_before_anything_is_read(self):
        """A seal is held to the one rule every writer's seal is: leaves, a
        grate, mangrove roots, a chest and a bottom slab are whole enough
        to walk on, and water waterlogs every one -- default oak leaves
        decay besides -- so the lake would be walled off with blocks it
        runs into. Each is refused, naming why, with nothing written; so
        is a modded block that says it is waterlogged, and a seal is
        checked whether or not the line finds any water to seal."""
        for seal in ("minecraft:oak_leaves",
                     "minecraft:oak_leaves[persistent=true]",
                     "minecraft:copper_grate", "minecraft:mangrove_roots",
                     "minecraft:chest", "minecraft:oak_slab[type=bottom]"):
            with self.subTest(seal=seal):
                with self.assertRaises(NBTError) as caught:
                    self.b.railway(self.line, seal=seal)
                self.assertIn("would not hold water back", str(caught.exception))
                self.assertEqual(self.b.blocks_set, 0)
                self.assertEqual(self.name(22, 60), "minecraft:water")
        modded = Builder(self.save, allow=("mymod:",))
        with self.assertRaises(NBTError) as caught:
            modded.railway(self.line, seal="mymod:wall[waterlogged=true]")
        self.assertIn("would not hold water back", str(caught.exception))
        self.assertEqual(modded.blocks_set, 0)
        self.assertEqual(modded.railway(self.line, seal="mymod:wall").sealed, 2)

        # A dry line, east of the hill: nothing to seal, and the seal is
        # refused all the same.
        dry = Railway([(45, 63, 2), (55, 63, 2)])
        with self.assertRaises(NBTError):
            self.b.railway(dry, seal="minecraft:oak_leaves")
        self.assertEqual(self.b.railway(dry, seal="minecraft:cobblestone").sealed,
                         0)

    def test_a_rail_that_does_not_run_on_is_somebody_elses(self):
        self.save.set_block(46, 64, Z, "minecraft:rail[shape=north_south]")
        with self.assertRaises(NBTError) as caught:
            self.b.railway(self.line, seal="minecraft:cobblestone")
        self.assertIn("(46, 64, 8)", str(caught.exception))
        result = self.b.railway(self.line, seal="minecraft:cobblestone",
                                overwrite=True)
        self.assertEqual(result.relaid, 1)
        self.assertEqual(self.block(46, 64).properties["shape"], "east_west")

    def test_a_pad_stops_a_railway_as_it_stops_a_road(self):
        self.b._pads["depot"] = (30, 0, 33, 15)
        with self.assertRaises(NBTError) as caught:
            self.b.railway(self.line, seal="minecraft:cobblestone")
        self.assertIn("depot", str(caught.exception))
        self.b.railway(self.line, seal="minecraft:cobblestone",
                       through=("depot",))

    def test_a_reservation_stops_a_railway_and_through_names_it(self):
        self.assertEqual(self.b.reserve(33, 15, 30, 0, name="yard"),
                         (30, 0, 33, 15))
        with self.assertRaises(Refused) as caught:
            self.b.railway(self.line, seal="minecraft:cobblestone")
        self.assertIn("reservation 'yard' at (30, 8)", str(caught.exception))
        self.assertEqual(self.b.blocks_set, 0, "a refusal writes nothing")
        self.b.railway(self.line, seal="minecraft:cobblestone",
                       through=("yard",))
        self.assertEqual(self.line.verify(self.save), [])


class KeepOutTests(RailBase):
    def test_a_seal_is_held_out_of_a_pad_like_the_rails_are(self):
        """The line keeps clear of the pier, but the water beside it is the
        pier's, and the seal that walls it off would be written there."""
        self.save.set_block(5, 64, 9, "minecraft:water")
        self.b._pads["pier"] = (0, 9, 15, 15)
        line = Railway([(0, 64, Z), (9, 64, Z)])
        with self.assertRaises(Refused) as caught:
            self.b.railway(line, seal="minecraft:cobblestone")
        self.assertIn("pad 'pier' at (5, 9)", str(caught.exception))
        self.assertEqual(self.name(5, 64, 9), "minecraft:water")
        self.assertEqual(self.b.blocks_set, 0, "a refusal writes nothing")
        result = self.b.railway(line, seal="minecraft:cobblestone",
                                through=("pier",))
        self.assertEqual(result.sealed, 1)
        self.assertEqual(self.name(5, 64, 9), "minecraft:cobblestone")


class VerifyTests(RailBase):
    def lay(self, line, supports=STONE):
        for i, (x, y, z) in enumerate(line.cells):
            self.save.set_block(x, y - 1, z, supports)
            self.save.set_block(x, y, z, line.state(i))

    def test_a_ramp_in_a_tunnel_two_blocks_high_walks_one_way(self):
        line = Railway([(0, 64, Z), (4, 64, Z), (6, 66, Z), (10, 66, Z)])
        self.save.fill((0, 60, 0), (12, 70, 15), STONE)
        for i, (x, y, z) in enumerate(line.cells):
            self.save.fill((x, y, z), (x, y + 1, z), "minecraft:air")
            self.save.set_block(x, y, z, line.state(i))
        faults = line.verify(self.save)
        self.assertTrue(any("one way on foot" in f for f in faults), faults)
        self.assertTrue(any("needs 3 clear" in f for f in faults), faults)

    def test_rails_on_what_cannot_hold_them_are_found_and_replaced(self):
        line = Railway([(0, 64, Z), (5, 64, Z)], boost_every=None)
        self.lay(line)
        for x, under in ((1, "minecraft:oak_leaves"),
                         (2, "minecraft:oak_slab[type=bottom]"),
                         (3, "minecraft:dirt_path")):
            self.save.set_block(x, 63, Z, under)
        faults = line.verify(self.save)
        self.assertEqual(sum("cannot hold the rail" in f for f in faults), 3)
        result = self.b.railway(line)
        self.assertEqual(result.supports, 3)
        self.assertEqual(line.verify(self.save), [])

    def test_sand_over_a_hole_takes_the_rail_with_it(self):
        line = Railway([(0, 64, Z), (5, 64, Z)], boost_every=None)
        self.lay(line, supports="minecraft:sand")
        self.save.fill((0, 60, Z), (5, 62, Z), STONE)
        self.save.set_block(2, 62, Z, "minecraft:air")
        faults = line.verify(self.save)
        self.assertEqual(len(faults), 1)
        self.assertIn("falls", faults[0])

    def test_a_kept_end_that_climbs_needs_something_to_climb_onto(self):
        line = Railway([(0, 64, Z), (5, 64, Z)], boost_every=None)
        self.lay(line)
        self.save.set_block(0, 64, Z, "minecraft:rail[shape=ascending_west]")
        faults = line.verify(self.save)
        self.assertTrue(any("nothing rigid to climb onto" in f
                            for f in faults), faults)

    def test_cutting_the_bed_from_under_a_crossing_line_is_refused(self):
        line = Railway([(0, 64, Z), (8, 64, Z)], boost_every=None)
        self.save.fill((0, 50, 0), (12, 63, 15), STONE)
        # A line crossing over at x=4, its bed two blocks over this rail.
        self.save.set_block(4, 66, Z, "minecraft:cobblestone")
        self.save.set_block(4, 67, Z, "minecraft:rail[shape=north_south]")
        line3 = Railway([(0, 64, Z), (8, 64, Z)], boost_every=None,
                        clearance=3)
        with self.assertRaises(NBTError) as caught:
            self.b.railway(line3)
        self.assertIn("drops a rail", str(caught.exception))
        self.b.railway(line)                    # two clear blocks: no conflict
        self.assertEqual(self.name(4, 67), "minecraft:rail")


if __name__ == "__main__":
    unittest.main()
