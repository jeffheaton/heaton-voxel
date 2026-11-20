"""Tests for connect_blocks: neighbor-derived block states.

The rules here mirror what vanilla writes; they were validated against
22,616 vanilla-placed fences, walls, panes, gates and stairs in a real save
with zero disagreements, and these tests pin the cases that validation
exercised: mineshaft-style fence attachments (spawners, stair backs),
ruin-style wall runs (tall sides under cover, the symmetric-flush post
rule), and stair corner chirality.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from heaton_voxel.connect import ConnectResult
from heaton_voxel.world import OVERWORLD, MinecraftSave
from tests.test_world import make_save

CHUNKS = [(x, z) for x in range(-1, 2) for z in range(-1, 2)]
Y = 70
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConnectBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="connect-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, CHUNKS)
        self.save = MinecraftSave.load(self.dir)

    def put(self, x, y, z, block):
        self.assertTrue(self.save.set_block(x, y, z, block))

    def connect(self, c1=(-8, Y - 2, -8), c2=(16, Y + 6, 16)) -> ConnectResult:
        return self.save.connect_blocks(c1, c2)

    def props(self, x, y, z):
        return self.save.get_block(x, y, z).properties


class FenceTests(ConnectBase):
    def test_fences_join_each_other_and_solids(self):
        for x in (0, 1, 2):
            self.put(x, Y, 0, "minecraft:oak_fence")
        self.put(3, Y, 0, "minecraft:stone_bricks")
        result = self.connect()
        self.assertEqual(result.fences_updated, 3)
        middle = self.props(1, Y, 0)
        self.assertEqual((middle["west"], middle["east"]), ("true", "true"))
        self.assertEqual((middle["north"], middle["south"]), ("false", "false"))
        end = self.props(2, Y, 0)
        self.assertEqual(end["east"], "true", "solid face joins the run")
        self.assertEqual(self.props(0, Y, 0)["west"], "false")

    def test_wooden_and_nether_fences_stay_apart(self):
        self.put(0, Y, 0, "minecraft:oak_fence")
        self.put(1, Y, 0, "minecraft:nether_brick_fence")
        self.put(2, Y, 0, "minecraft:nether_brick_fence")
        self.connect()
        self.assertEqual(self.props(0, Y, 0)["east"], "false")
        self.assertEqual(self.props(1, Y, 0)["west"], "false")
        self.assertEqual(self.props(1, Y, 0)["east"], "true")

    def test_fence_joins_a_gate_only_along_its_bar(self):
        self.put(0, Y, 0, "minecraft:oak_fence")
        self.put(1, Y, 0, "minecraft:oak_fence_gate[facing=north]")
        self.put(1, Y, 1, "minecraft:oak_fence")
        self.connect()
        self.assertEqual(self.props(0, Y, 0)["east"], "true", "bar spans east-west")
        self.assertEqual(self.props(1, Y, 1)["north"], "false", "no bar that way")

    def test_fence_attaches_to_a_stair_back_and_double_slab(self):
        """Shipwreck geometry: the stair's facing side is a full face."""
        self.put(0, Y, 0, "minecraft:oak_fence")
        self.put(0, Y, -1, "minecraft:dark_oak_stairs[facing=south]")
        self.put(0, Y, 1, "minecraft:dark_oak_stairs[facing=south]")
        self.put(1, Y, 0, "minecraft:oak_slab[type=double]")
        self.put(-1, Y, 0, "minecraft:oak_slab[type=bottom]")
        self.connect()
        got = self.props(0, Y, 0)
        self.assertEqual(got["north"], "true", "stair faces us: full back face")
        self.assertEqual(got["south"], "false", "stair faces away")
        self.assertEqual(got["east"], "true", "double slab is a full cube")
        self.assertEqual(got["west"], "false", "half slab is not")

    def test_spawner_counts_as_a_full_cube(self):
        self.put(0, Y, 0, "minecraft:oak_fence")
        self.put(1, Y, 0, "minecraft:spawner")
        self.connect()
        self.assertEqual(self.props(0, Y, 0)["east"], "true")


class WallTests(ConnectBase):
    def test_straight_run_is_flush_and_corners_post(self):
        for x in range(0, 5):
            self.put(x, Y, 0, "minecraft:cobblestone_wall")
        self.put(4, Y, 1, "minecraft:cobblestone_wall")
        self.connect()
        middle = self.props(2, Y, 0)
        self.assertEqual((middle["west"], middle["east"]), ("low", "low"))
        self.assertEqual(middle["up"], "false", "straight runs flush")
        corner = self.props(4, Y, 0)
        self.assertEqual(corner["up"], "true", "an L raises the post")
        self.assertEqual(self.props(0, Y, 0)["up"], "true", "so does an end")

    def test_symmetric_cross_is_flush_but_a_lopsided_one_posts(self):
        for dx, dz in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
            self.put(dx, Y, dz, "minecraft:cobblestone_wall")
        self.connect()
        self.assertEqual(self.props(0, Y, 0)["up"], "false",
                         "vanilla runs a symmetric cross flush")
        # covering one arm makes the heights lopsided: post comes back
        self.put(1, Y + 1, 0, "minecraft:stone_bricks")
        self.put(1, Y, 0, "minecraft:cobblestone_wall")  # rewrite bare
        self.connect()
        self.assertEqual(self.props(1, Y, 0)["east"], "none")
        self.assertEqual(self.props(0, Y, 0)["east"], "low")

    def test_sides_run_tall_under_cover_and_lanterns_force_posts(self):
        for x in range(0, 3):
            self.put(x, Y, 0, "minecraft:cobblestone_wall")
            self.put(x, Y + 1, 0, "minecraft:stone_bricks")
        self.connect()
        covered = self.props(1, Y, 0)
        self.assertEqual((covered["west"], covered["east"]), ("tall", "tall"))
        self.assertEqual(covered["up"], "false", "flush under a solid ceiling")

        for x in range(4, 7):
            self.put(x, Y, 3, "minecraft:cobblestone_wall")
        self.put(5, Y + 1, 3, "minecraft:lantern[hanging=false]")
        self.connect()
        self.assertEqual(self.props(5, Y, 3)["up"], "true",
                         "a lantern on top forces the post")
        self.assertEqual(self.props(4, Y, 3)["up"], "true", "run end")

    def test_a_run_under_cave_air_is_flush_like_one_under_the_sky(self):
        # An old cave is full of cave_air and the world ends in void_air:
        # neither has a shape, so the game's shouldRaisePost finds
        # nothing perched on the wall and the run stays flush.
        for z, air in ((0, "minecraft:cave_air"), (3, "minecraft:void_air")):
            for x in range(0, 5):
                self.put(x, Y, z, "minecraft:cobblestone_wall")
                self.put(x, Y + 1, z, air)
        self.connect()
        for z, air in ((0, "minecraft:cave_air"), (3, "minecraft:void_air")):
            with self.subTest(air=air):
                middle = self.props(2, Y, z)
                self.assertEqual((middle["west"], middle["east"]), ("low", "low"))
                self.assertEqual(middle["up"], "false", f"{air} perches on nothing")
                self.assertEqual(self.props(0, Y, z)["up"], "true", "an end still posts")

    def test_a_wall_reads_the_shape_above_it_not_its_name(self):
        """What the game reads above a wall is that block's collision
        underside and its tag, never whether a fence would attach to it.
        A wall with its post up raises the post below it outright, a leaf
        is a full cube nothing attaches to, a top slab covers nothing below
        it and an open trapdoor nothing either. The JeffWorld walls under
        a spruce and under a posted wall are stored exactly this way."""
        wall = "minecraft:cobblestone_wall"
        cases = {
            "a posted wall": ("minecraft:cobblestone_wall[east=none,north=low,"
                              "south=low,up=true,west=none]",
                              ("tall", "tall", "true")),
            "spruce leaves": ("minecraft:spruce_leaves[distance=3,persistent=false]",
                              ("tall", "tall", "false")),
            "a bottom slab": ("minecraft:stone_slab[type=bottom]",
                              ("tall", "tall", "false")),
            "a top slab": ("minecraft:stone_slab[type=top]",
                           ("low", "low", "false")),
            "an open trapdoor": ("minecraft:oak_trapdoor[half=bottom,open=true]",
                                 ("low", "low", "false")),
            "a torch": ("minecraft:torch", ("low", "low", "true")),
            "a pot": ("minecraft:flower_pot", ("low", "low", "true")),
        }
        for x, (above, _) in enumerate(cases.values()):
            for z in (-1, 0, 1):
                self.put(3 * x, Y, z, wall)
            self.put(3 * x, Y + 1, 0, above)
        # The walls' own level only: the blocks above stay as stored.
        self.connect((-8, Y, -8), (3 * len(cases), Y, 8))
        for x, (why, (_, (north, south, up))) in enumerate(cases.items()):
            with self.subTest(under=why):
                found = self.props(3 * x, Y, 0)
                self.assertEqual((found["north"], found["south"], found["up"]),
                                 (north, south, up))

    def test_the_table_answers_every_state_the_game_has(self):
        """The cover table is generated from the game, and so is the shape
        fixture: every state the one lists, the other answers -- a block an
        update added and the table never heard of fails here by name -- and
        a state with no collision shape covers nothing of a wall."""
        from fixtures import block_shapes
        from heaton_voxel.wall_cover import POST, wall_cover

        unknown, covering = [], []
        for name, properties, shape in block_shapes():
            code = wall_cover(name, properties)
            if code is None:
                unknown.append(f"{name}[{properties}]")
            elif shape.top < 0 and code & (POST * 2 - 1):
                covering.append(f"{name}[{properties}]")
        self.assertEqual(unknown[:5], [], f"{len(unknown)} states unanswered; "
                         f"rerun tools/generate_wall_cover.py")
        self.assertEqual(covering[:5], [], "a state with no shape covers nothing")

    def test_two_high_wall_reads_the_wall_above(self):
        for x in range(0, 3):
            self.put(x, Y, 0, "minecraft:cobblestone_wall")
            self.put(x, Y + 1, 0, "minecraft:cobblestone_wall")
        self.connect()
        lower = self.props(1, Y, 0)
        upper = self.props(1, Y + 1, 0)
        self.assertEqual((upper["west"], upper["east"]), ("low", "low"))
        self.assertEqual((lower["west"], lower["east"]), ("tall", "tall"),
                         "the wall above covers the joints")
        self.assertEqual(lower["up"], "false")


class AttachmentTests(ConnectBase):
    """What a fence, a wall or a pane joins as a solid neighbor, where the
    game's answer is a rule the name table cannot spell: the blocks its
    ``Block.isExceptionForConnection`` excepts, full faces and all, and
    the trapdoors, whose one full side is an open one's plate."""

    def test_a_wall_beside_a_pumpkin_or_an_open_trapdoor_joins_as_the_game_does(self):
        """The fixture village's own two walls, as the game stores them: a
        wall with a pumpkin to its south and nothing joined there, and a
        chimney wall between three open trapdoors each facing away from it,
        their plates against it, joined to all three, run tall under the
        campfire over it. The name table joined the pumpkin and cut the
        trapdoors off, so connect_blocks rewrote both walls to states the
        game would never set."""
        wall = "minecraft:cobblestone_wall"
        self.put(0, Y, 0, wall)
        self.put(0, Y, -1, wall)
        self.put(0, Y, 1, "minecraft:pumpkin")
        self.put(4, Y, 0, wall)
        self.put(4, Y, -1, wall)
        self.put(4, Y + 1, 0, "minecraft:campfire")
        for (dx, dz), facing in (((1, 0), "east"), ((-1, 0), "west"),
                                 ((0, 1), "south")):
            self.put(4 + dx, Y, dz, f"minecraft:spruce_trapdoor[facing={facing},"
                                    f"half=top,open=true]")
        self.put(8, Y, 0, wall)
        for (dx, dz), facing in (((1, 0), "west"), ((-1, 0), "north"),
                                 ((0, 1), "south")):
            self.put(8 + dx, Y, dz, f"minecraft:spruce_trapdoor[facing={facing},"
                                    f"half=top,open={'false' if dz else 'true'}]")
        self.connect()
        pumpkin = self.props(0, Y, 0)
        self.assertEqual((pumpkin["north"], pumpkin["south"], pumpkin["up"]),
                         ("low", "none", "true"), "nothing joins a pumpkin")
        chimney = self.props(4, Y, 0)
        self.assertEqual({side: chimney[side] for side in
                          ("north", "east", "south", "west", "up")},
                         {"north": "tall", "east": "tall", "south": "tall",
                          "west": "tall", "up": "false"})
        edges = self.props(8, Y, 0)
        self.assertEqual({side: edges[side] for side in
                          ("north", "east", "south", "west")},
                         {"north": "none", "east": "none", "south": "none",
                          "west": "none"},
                         "a plate on the far side, its edge, or one shut")

    def test_the_excepted_blocks_and_every_trapdoor_join_as_the_game_says(self):
        """State by state against the game's own faces (the probe's
        ``SupportType.FULL`` masks): a trapdoor joins only where its face
        toward the joining block is full, and the blocks the game excepts
        from every connection -- leaves, the barrier, the pumpkins, the
        jack o'lantern and the melon -- never, though most have full faces
        all round."""
        from fixtures import block_shapes
        from heaton_voxel import connect
        from heaton_voxel.blocks import BlockState

        full = {"north": 4, "south": 8, "west": 16, "east": 32}
        opposite = {"north": "south", "south": "north", "west": "east",
                    "east": "west"}
        excepted = {"minecraft:barrier", "minecraft:pumpkin",
                    "minecraft:carved_pumpkin", "minecraft:jack_o_lantern",
                    "minecraft:melon"}
        wrong, seen = [], set()
        for name, properties, shape in block_shapes():
            exception = name in excepted or name.endswith("_leaves")
            if not (exception or name.endswith("_trapdoor")
                    or name.endswith("shulker_box")):
                continue
            seen.add(name)
            state = BlockState(name, properties)
            for direction, bit in full.items():
                game = (not exception
                        and bool(shape.sturdy & full[opposite[direction]]))
                if connect._face_full(state, direction) != game:
                    wrong.append((str(state), direction, game))
        self.assertEqual(wrong[:5], [], f"{len(wrong)} disagree")
        self.assertTrue(excepted <= seen)
        self.assertGreater(sum(name.endswith("_trapdoor") for name in seen), 10)
        self.assertGreater(sum(name.endswith("shulker_box") for name in seen), 16)

    def test_the_game_excepts_these_nineteen_blocks_and_no_others(self):
        """``Block.isExceptionForConnection`` is a list of names here, so
        it is held to the game's own list, block by block: the fourteen
        leaves, the two pumpkins, the jack o'lantern, the melon and the
        barrier. A shulker box was on this list and is not on the game's --
        in 26.1.2, 26.2 or 26.3 -- and its sides are full, so a fence,
        wall, pane or bar the game had joined to one came back a
        free-standing post."""
        from fixtures import block_shapes
        from heaton_voxel import connect

        # The game's answer: a block with a full side that nothing joins.
        full_sided, excepted = set(), set()
        for name, properties, shape in block_shapes():
            if shape.sturdy & (4 | 8 | 16 | 32):
                full_sided.add(name)
            if connect._no_connection(name):
                excepted.add(name)
        leaves = {name for name in excepted if name.endswith("_leaves")}
        self.assertEqual(len(leaves), 14, sorted(leaves))
        self.assertEqual(excepted - leaves,
                         {"minecraft:barrier", "minecraft:pumpkin",
                          "minecraft:carved_pumpkin",
                          "minecraft:jack_o_lantern", "minecraft:melon"})
        boxes = {name for name in full_sided if name.endswith("shulker_box")}
        self.assertEqual(len(boxes), 17, sorted(boxes))
        self.assertEqual(boxes & excepted, set(),
                         "the game joins a fence to a shulker box")

    def test_a_pane_joins_a_shulker_box(self):
        """The whole of the bug, as a build sees it."""
        self.put(0, Y, 0, "minecraft:purple_shulker_box[facing=up]")
        self.put(1, Y, 0, "minecraft:glass_pane")
        self.connect()
        self.assertEqual(self.props(1, Y, 0)["west"], "true")


class FaceTests(ConnectBase):
    """What is solid to join, held to the game state by state."""

    def test_every_state_joins_as_the_game_says(self):
        """Every side of every state the game has against its own
        ``SupportType.FULL`` faces, a stair among them. The rule this
        replaced was a list of name endings, which disagreed on 10,308 sides
        and left a copper tower's panes short of its piers (2026-11-01);
        stairs were then read by ``facing`` alone, which disagreed on 6,322
        states more (2026-11-20)."""
        from fixtures import block_shapes
        from heaton_voxel import connect
        from heaton_voxel.blocks import BlockState

        full = {"north": 4, "south": 8, "west": 16, "east": 32}
        opposite = {"north": "south", "south": "north", "west": "east",
                    "east": "west"}
        wrong = []
        count = 0
        stairs = 0
        for name, properties, shape in block_shapes():
            if connect._no_connection(name):
                continue
            state = BlockState(name, properties)
            stairs += name.endswith("_stairs")
            for direction in full:
                count += 1
                game = bool(shape.sturdy & full[opposite[direction]])
                if connect._face_full(state, direction) != game:
                    wrong.append((str(state), direction, game))
        self.assertEqual(wrong[:5], [], f"{len(wrong)} disagree")
        self.assertGreater(count, 100000)
        self.assertGreater(stairs, 7000, "stairs are in the table now")

    def test_a_stair_joins_by_its_shape_and_not_its_facing_alone(self):
        """The three answers a stair gives, each the game's own: a straight
        stair's facing side is full, an inner corner's flank beside it is
        too, and an outer corner -- a quarter step -- has no full side.
        Read by ``facing`` alone, the outer corner put an arm on a pane
        reaching at nothing and the inner corner left one short."""
        from heaton_voxel import connect
        from heaton_voxel.blocks import BlockState

        def face(shape, side):
            """Is the ``side`` face of a north-facing stair full? The
            argument runs from the connector to the stair, so a connector
            touching that face stands on the opposite side of it."""
            state = BlockState("minecraft:oak_stairs",
                               {"facing": "north", "half": "bottom",
                                "shape": shape, "waterlogged": "false"})
            opposite = {"north": "south", "south": "north",
                        "west": "east", "east": "west"}
            return connect._face_full(state, opposite[side])

        # the stair's own facing side
        self.assertTrue(face("straight", "north"))
        self.assertTrue(face("inner_left", "north"))
        self.assertTrue(face("inner_right", "north"))
        self.assertFalse(face("outer_left", "north"))
        self.assertFalse(face("outer_right", "north"))
        # the flank: an inner_left fills the one counter-clockwise of its
        # facing, an inner_right the one clockwise, and a straight neither
        self.assertTrue(face("inner_left", "west"))
        self.assertFalse(face("inner_right", "west"))
        self.assertFalse(face("straight", "west"))
        self.assertTrue(face("inner_right", "east"))
        self.assertFalse(face("inner_left", "east"))
        # the back and the open side are never full
        for shape in ("straight", "inner_left", "inner_right",
                      "outer_left", "outer_right"):
            self.assertFalse(face(shape, "south"), shape)

    def test_a_pane_beside_an_outer_corner_stair_stays_a_post(self):
        """The bug as a build sees it: a pane reaching at a quarter step.

        An outer corner always has a stair on its facing side -- that
        neighbor is what makes it a corner -- so a pane meets one only
        where the stair's shape is the stored one, which is what
        ``stairs=False`` says it is.
        """
        self.put(0, Y, 0, "minecraft:oak_stairs[facing=east,half=bottom,"
                          "shape=outer_left]")
        self.put(1, Y, 0, "minecraft:glass_pane")
        self.save.connect_blocks((-8, Y - 2, -8), (16, Y + 6, 16), stairs=False)
        self.assertEqual(self.props(1, Y, 0)["west"], "false")
        self.assertEqual(self.props(0, Y, 0)["shape"], "outer_left",
                         "stairs=False leaves the shape alone")

    def test_a_pane_joins_a_straight_stair_it_faces(self):
        self.put(0, Y, 0, "minecraft:oak_stairs[facing=east,half=bottom,"
                          "shape=straight]")
        self.put(1, Y, 0, "minecraft:glass_pane")
        self.connect()
        self.assertEqual(self.props(1, Y, 0)["west"], "true")

    def test_a_pane_joins_an_inner_corner_stair_flank(self):
        """A north-facing inner_left fills its west face too, so a pane to
        the west of it joins -- which reading the facing alone missed."""
        self.put(1, Y, 0, "minecraft:oak_stairs[facing=north,half=bottom,"
                          "shape=inner_left]")
        self.put(0, Y, 0, "minecraft:glass_pane")
        self.connect()
        self.assertEqual(self.props(0, Y, 0)["east"], "true")

    def test_the_blocks_the_name_list_missed(self):
        for name in ("minecraft:waxed_oxidized_copper",
                     "minecraft:waxed_oxidized_cut_copper",
                     "minecraft:copper_block", "minecraft:smooth_quartz",
                     "minecraft:oak_log", "minecraft:crafter",
                     "minecraft:red_concrete_powder"):
            with self.subTest(name=name):
                self.put(0, Y, 0, name)
                self.put(1, Y, 0, "minecraft:glass_pane")
                self.connect()
                self.assertEqual(self.props(1, Y, 0)["west"], "true")

    def test_a_state_left_unsaid_is_its_default(self):
        from heaton_voxel import connect
        from heaton_voxel.blocks import BlockState

        self.assertFalse(connect._face_full(
            BlockState("minecraft:oak_slab"), "north"))
        self.assertTrue(connect._face_full(
            BlockState("minecraft:oak_slab", {"type": "double"}), "north"))

    def test_a_block_the_game_does_not_have_has_no_full_face(self):
        from heaton_voxel import connect
        from heaton_voxel.blocks import BlockState

        self.assertFalse(connect._face_full(
            BlockState("somemod:strange_block"), "north"))

    def test_the_committed_tables_are_what_the_generator_writes(self):
        """Rerunning the generator after the probe is the whole job, so a
        table it would change is a table somebody forgot."""
        import importlib.util
        path = os.path.join(_ROOT, "tools", "generate_connect_faces.py")
        spec = importlib.util.spec_from_file_location("generate_faces", path)
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        built = generator.tables()
        for out, text in ((generator.PYTHON_OUT, generator.python_text(*built)),
                          (generator.CSHARP_OUT, generator.csharp_text(*built))):
            with self.subTest(path=out):
                with open(out, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), text,
                                     "rerun tools/generate_connect_faces.py")


class PaneAndGateTests(ConnectBase):
    def test_panes_and_bars_join_walls_and_solids(self):
        self.put(0, Y, 0, "minecraft:stone_bricks")
        self.put(1, Y, 0, "minecraft:glass_pane")
        self.put(2, Y, 0, "minecraft:iron_bars")
        self.put(3, Y, 0, "minecraft:cobblestone_wall")
        self.connect()
        pane = self.props(1, Y, 0)
        self.assertEqual((pane["west"], pane["east"]), ("true", "true"))
        bars = self.props(2, Y, 0)
        self.assertEqual((bars["west"], bars["east"]), ("true", "true"))
        self.assertEqual(self.props(3, Y, 0)["west"], "low")

    def test_gate_lowers_into_a_wall(self):
        self.put(0, Y, 0, "minecraft:cobblestone_wall")
        self.put(1, Y, 0, "minecraft:oak_fence_gate[facing=north]")
        self.put(2, Y, 0, "minecraft:cobblestone_wall")
        self.put(4, Y, 0, "minecraft:oak_fence_gate[facing=north]")
        result = self.connect()
        self.assertEqual(self.props(1, Y, 0)["in_wall"], "true")
        self.assertEqual(self.props(4, Y, 0).get("in_wall", "false"), "false")
        self.assertGreaterEqual(result.gates_updated, 1)


class StairShapeTests(ConnectBase):
    def test_outer_and_inner_corners(self):
        """Vanilla's getStairShape, both chiralities.

        An outer corner forms when the perpendicular stair sits *behind*
        (on the facing side); an inner one when it sits in front. The
        expectations match vanilla-placed corner stairs bit for bit (the
        validation pass covered 2,520 of them).
        """
        self.put(0, Y, 0, "minecraft:oak_stairs[facing=south]")
        self.put(0, Y, 1, "minecraft:oak_stairs[facing=east]")    # behind
        self.put(4, Y, 0, "minecraft:oak_stairs[facing=south]")
        self.put(4, Y, 1, "minecraft:oak_stairs[facing=west]")
        self.put(8, Y, 1, "minecraft:oak_stairs[facing=south]")
        self.put(8, Y, 0, "minecraft:oak_stairs[facing=east]")    # in front
        self.put(12, Y, 1, "minecraft:oak_stairs[facing=south]")
        self.put(12, Y, 0, "minecraft:oak_stairs[facing=west]")
        self.connect()
        self.assertEqual(self.props(0, Y, 0)["shape"], "outer_left")
        self.assertEqual(self.props(4, Y, 0)["shape"], "outer_right")
        self.assertEqual(self.props(8, Y, 1)["shape"], "inner_left")
        self.assertEqual(self.props(12, Y, 1)["shape"], "inner_right")
        self.assertEqual(self.props(0, Y, 1)["shape"], "straight",
                         "the east-facing stair itself has no turn to make")

    def test_matching_flank_suppresses_the_corner(self):
        """canTakeShape: a same-facing stair alongside keeps the run straight."""
        self.put(0, Y, 0, "minecraft:oak_stairs[facing=south]")
        self.put(0, Y, 1, "minecraft:oak_stairs[facing=east]")
        self.put(-1, Y, 0, "minecraft:oak_stairs[facing=south]")
        self.put(1, Y, 0, "minecraft:oak_stairs[facing=south]")
        self.connect()
        self.assertEqual(self.props(0, Y, 0)["shape"], "straight")

    def test_half_mismatch_stays_straight_and_stairs_can_be_skipped(self):
        self.put(0, Y, 0, "minecraft:oak_stairs[facing=south,half=bottom]")
        self.put(0, Y, 1, "minecraft:oak_stairs[facing=east,half=top]")
        self.connect()
        self.assertEqual(self.props(0, Y, 0)["shape"], "straight")

        self.put(4, Y, 0, "minecraft:oak_stairs[facing=south]")
        self.put(4, Y, 1, "minecraft:oak_stairs[facing=east]")
        self.save.connect_blocks((3, Y, -1), (5, Y, 2), stairs=False)
        self.assertEqual(self.props(4, Y, 0).get("shape", "straight"), "straight")


class PassBehaviorTests(ConnectBase):
    def test_idempotent_and_scoped_to_the_box(self):
        self.put(0, Y, 0, "minecraft:oak_fence")
        self.put(1, Y, 0, "minecraft:oak_fence")
        self.put(8, Y, 0, "minecraft:oak_fence")   # outside the box below
        first = self.save.connect_blocks((-1, Y, -1), (2, Y, 1))
        self.assertEqual(first.fences_updated, 2)
        second = self.save.connect_blocks((-1, Y, -1), (2, Y, 1))
        self.assertEqual(second.total_updated, 0, "second pass changes nothing")
        outside = self.props(8, Y, 0)
        self.assertNotIn("east", outside, "blocks outside the box stay bare")

    def test_a_connector_before_a_stale_stair_settles_on_the_second_pass(self):
        """Every neighbor is read as it stands, a stair's ``shape``
        included, so a connector the sweep reaches before the stair beside
        it reads the shape that stair was written with. A bulk build that
        left its stair shapes unsettled therefore wants a second call,
        after which nothing moves."""
        self.put(1, Y, 1, "minecraft:oak_stairs[facing=west,half=bottom,"
                          "shape=straight]")
        # written straight; its corner is really inner_left
        self.put(1, Y, 0, "minecraft:oak_stairs[facing=north,half=bottom,"
                          "shape=straight]")
        self.put(0, Y, 0, "minecraft:glass_pane")
        first = self.save.connect_blocks((-1, Y - 1, -1), (3, Y + 1, 3))
        self.assertEqual(self.props(1, Y, 0)["shape"], "inner_left")
        self.assertEqual(self.props(0, Y, 0)["east"], "false",
                         "the pane read the shape the stair was written with")
        second = self.save.connect_blocks((-1, Y - 1, -1), (3, Y + 1, 3))
        self.assertEqual(self.props(0, Y, 0)["east"], "true")
        third = self.save.connect_blocks((-1, Y - 1, -1), (3, Y + 1, 3))
        self.assertEqual(third.total_updated, 0, "and then it is settled")
        self.assertEqual((first.total_updated, second.total_updated), (2, 1))

    def test_waterlogged_and_other_properties_survive(self):
        self.put(0, Y, 0, "minecraft:oak_fence[waterlogged=true]")
        self.put(1, Y, 0, "minecraft:oak_fence")
        self.connect()
        got = self.props(0, Y, 0)
        self.assertEqual(got["waterlogged"], "true")
        self.assertEqual(got["east"], "true")

    def test_missing_chunks_are_reported(self):
        result = self.save.connect_blocks((0, Y, 0), (200, Y, 0))
        self.assertFalse(result.complete)
        self.assertGreater(len(result.missing_chunks), 0)

    def test_result_repr_reads(self):
        self.put(0, Y, 0, "minecraft:oak_fence")
        self.put(1, Y, 0, "minecraft:oak_fence")
        result = self.connect()
        self.assertIn("fences", repr(result))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
