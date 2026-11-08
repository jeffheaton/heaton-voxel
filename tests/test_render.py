"""Tests for heaton_voxel.craft.render: area maps and vertical cutaways.

PNGWriter and ColorMap moved here from the world_map example and keep their
original tests in test_examples (imported through the example, which now
re-exports them). These cover what is new: the two renderers and the CLI,
against the vendored fixture, whose ocean has a known look -- water at the
surface, air above, a sky/ground split any cutaway must reproduce.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from fixtures import FIXTURE_LEVEL_DAT, FIXTURE_REGION  # noqa: E402
from heaton_voxel.craft.render import (  # noqa: E402
    CAVE,
    SKY,
    UNGENERATED,
    ColorMap,
    render_area,
    render_plan,
    render_slice,
)
from heaton_voxel.world import OVERWORLD, MinecraftSave  # noqa: E402
from tests.test_examples import read_png  # noqa: E402

# A fully generated fixture chunk over open ocean: world chunk (-24, 1).
SEA_X0, SEA_Z0 = -384, 16


def make_world(tmpdir: str) -> str:
    world = os.path.join(tmpdir, "world")
    region_dir = os.path.join(world, "dimensions/minecraft/overworld/region")
    os.makedirs(region_dir)
    shutil.copyfile(
        FIXTURE_REGION, os.path.join(region_dir, os.path.basename(FIXTURE_REGION))
    )
    shutil.copyfile(FIXTURE_LEVEL_DAT, os.path.join(world, "level.dat"))
    return world


class RenderBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="render-")
        cls.save = MinecraftSave.load(make_world(cls.dir))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def out(self, name: str) -> str:
        return os.path.join(self.dir, name)


class AreaRenderTests(RenderBase):
    def test_size_scale_and_sea_color(self):
        path = self.out("area.png")
        render_area(
            self.save, (SEA_X0, 0, SEA_Z0), (SEA_X0 + 15, 0, SEA_Z0 + 15),
            path, dimension=OVERWORLD, scale=2,
        )
        img = read_png(path)
        self.assertEqual((img["width"], img["height"]), (32, 32))
        r, g, b = img["rows"][16][16 * 3:16 * 3 + 3]
        self.assertGreater(b, r, "open ocean should render blue-dominant")
        self.assertGreater(b, g)

    def test_ungenerated_ground_is_black(self):
        path = self.out("edge.png")
        # One row inside the map, one row far outside it.
        render_area(
            self.save, (100_000, 0, 100_000), (100_003, 0, 100_000),
            path, dimension=OVERWORLD,
        )
        img = read_png(path)
        self.assertEqual(tuple(img["rows"][0][:3]), UNGENERATED)

    def test_water_keeps_its_depth_without_its_ocean_floor(self):
        """Water is darkened by its depth. A chunk an older library rebuilt
        has lost OCEAN_FLOOR, which is where the depth used to come from, so
        every edited lake drew as a puddle; the column reader finds the bed
        from the blocks instead, and the picture does not change."""
        save = MinecraftSave.load(make_world(tempfile.mkdtemp(dir=self.dir)))
        box = ((SEA_X0, 0, SEA_Z0), (SEA_X0 + 15, 0, SEA_Z0 + 15))
        before, after = self.out("sea-before.png"), self.out("sea-after.png")
        render_area(save, *box, before, dimension=OVERWORLD)
        chunk = save.dimension(OVERWORLD).chunk(SEA_X0 >> 4, SEA_Z0 >> 4)
        del chunk.data["Heightmaps"]["OCEAN_FLOOR"]
        save.dimension(OVERWORLD).blocks(
            SEA_X0 >> 4, SEA_Z0 >> 4).drop_heightmap_cache()
        self.assertIsNone(save.surface_y(SEA_X0, SEA_Z0, "OCEAN_FLOOR"))
        render_area(save, *box, after, dimension=OVERWORLD)
        self.assertEqual(read_png(after)["rows"], read_png(before)["rows"])

    def test_what_stands_in_the_water_is_darkened_and_the_bed_is_not(self):
        """A kelp tip at the surface is drawn darkened by the sea's depth,
        like the water beside it; a waterlogged stair on the seabed, drawn
        by OCEAN_FLOOR, is the bed and is drawn as itself."""
        from tests.test_survey import _pack_maps
        from tests.test_world import make_save

        world = tempfile.mkdtemp(dir=self.dir)
        make_save(world, [(0, 0)])
        save = MinecraftSave.load(world)
        save.fill((0, 40, 0), (15, 50, 15), "minecraft:stone")
        save.fill((0, 51, 0), (15, 51, 15), "minecraft:oak_stairs[waterlogged=true]")
        save.fill((0, 52, 0), (15, 62, 15), "minecraft:water[level=0]")
        save.fill((9, 52, 8), (9, 61, 8), "minecraft:kelp_plant")
        save.set_block(9, 62, 8, "minecraft:kelp[age=3]")
        _pack_maps(save, (0, 0), WORLD_SURFACE=62, MOTION_BLOCKING=62,
                   MOTION_BLOCKING_NO_LEAVES=62, OCEAN_FLOOR=51)
        colors = ColorMap()
        k = 1.0 - 11 / 44.0                      # eleven cells over the stair

        def pixel(heightmap, x, z):
            path = self.out(f"stand-in-{heightmap}.png")
            render_area(save, (0, 0, 0), (15, 0, 15), path, dimension=OVERWORLD,
                        heightmap=heightmap, shade=False, colors=colors)
            return tuple(read_png(path)["rows"][z][x * 3:x * 3 + 3])

        for (x, z), name in (((8, 8), "minecraft:water"), ((9, 8), "minecraft:kelp")):
            with self.subTest(drawn=name):
                self.assertEqual(pixel("WORLD_SURFACE", x, z),
                                 tuple(int(c * k) for c in colors(name)))
        self.assertEqual(pixel("OCEAN_FLOOR", 8, 8), tuple(colors("minecraft:oak_stairs")),
                         "the seabed is not under itself")

    def test_unknown_blocks_are_reported_on_the_returned_map(self):
        colors = render_area(
            self.save, (SEA_X0, 0, SEA_Z0), (SEA_X0 + 3, 0, SEA_Z0 + 3),
            self.out("known.png"), dimension=OVERWORLD,
        )
        self.assertIsInstance(colors, ColorMap)
        self.assertEqual(colors.unknown, {}, "the fixture sea is all known blocks")


class SliceRenderTests(RenderBase):
    def test_sky_above_water_below_seabed_between(self):
        """A cutaway through the ocean: sky at the top, no sky underground."""
        path = self.out("slice.png")
        render_slice(
            self.save, "x", SEA_X0 + 8, (0, 30, SEA_Z0), (0, 90, SEA_Z0 + 15),
            path, dimension=OVERWORLD,
        )
        img = read_png(path)
        self.assertEqual((img["width"], img["height"]), (16, 61))
        top = tuple(img["rows"][0][:3])
        self.assertEqual(top, SKY, "y=90 over the ocean is open sky")
        # y=60: below sea level, inside the water column: blue, not sky.
        below = tuple(img["rows"][90 - 60][:3])
        self.assertNotEqual(below, SKY)
        self.assertGreater(below[2], below[0], "water reads blue")
        # y=30: under the seabed, solid ground or covered air, never sky.
        deep = tuple(img["rows"][60][:3])
        self.assertNotEqual(deep, SKY)

    def test_axis_z_and_scale(self):
        path = self.out("slice_z.png")
        render_slice(
            self.save, "z", SEA_Z0 + 8, (SEA_X0, 50, 0), (SEA_X0 + 7, 70, 0),
            path, dimension=OVERWORLD, scale=3,
        )
        img = read_png(path)
        self.assertEqual((img["width"], img["height"]), (24, 63))

    def test_covered_air_reads_as_cave_not_sky(self):
        """Carve an air pocket under the seabed and slice through it."""
        x, z = SEA_X0 + 4, SEA_Z0 + 8
        floor = self.save.dimension(OVERWORLD).surface_y(x, z, "OCEAN_FLOOR")
        self.save.fill((x, floor - 6, z), (x, floor - 4, z), "minecraft:air")
        path = self.out("cave.png")
        render_slice(
            self.save, "z", z, (x, floor - 8, 0), (x, floor + 2, 0),
            path, dimension=OVERWORLD,
        )
        img = read_png(path)
        pocket_row = (floor + 2) - (floor - 5)
        self.assertEqual(tuple(img["rows"][pocket_row][:3]), CAVE)

    def test_unknown_axis_is_refused(self):
        with self.assertRaises(ValueError):
            render_slice(self.save, "y", 0, (0, 0, 0), (1, 1, 1), self.out("no.png"))


class PlanRenderTests(RenderBase):
    """The third renderer: a horizontal cut at one y.

    A roofed box is built over the fixture's ocean, high above anything
    the fixture generated, so the story under test is one this test made
    and nothing else can explain the pixels. Heightmaps are rebuilt after
    the build for the same reason the library insists on it everywhere
    else: WORLD_SURFACE is what tells air-with-a-roof from open sky, and
    a stale map would draw the inside of the building as sky.
    """

    BOX_X0, BOX_Z0 = SEA_X0 + 2, SEA_Z0 + 2
    BOX_X1, BOX_Z1 = SEA_X0 + 8, SEA_Z0 + 8
    FLOOR_Y, ROOF_Y = 100, 104
    CUT_Y = 102

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.save.fill(
            (cls.BOX_X0, cls.FLOOR_Y, cls.BOX_Z0),
            (cls.BOX_X1, cls.ROOF_Y, cls.BOX_Z1), "minecraft:stone_bricks",
        )
        cls.save.fill(
            (cls.BOX_X0 + 1, cls.FLOOR_Y, cls.BOX_Z0 + 1),
            (cls.BOX_X1 - 1, cls.ROOF_Y - 1, cls.BOX_Z1 - 1), "minecraft:air",
        )
        cls.save.rebuild_heightmaps(
            (SEA_X0, 0, SEA_Z0), (SEA_X0 + 15, 0, SEA_Z0 + 15)
        )

    def plan(self, name, *, scale=1, y=None):
        path = self.out(name)
        colors = render_plan(
            self.save, self.CUT_Y if y is None else y,
            (SEA_X0, 0, SEA_Z0), (SEA_X0 + 15, 0, SEA_Z0 + 15), path,
            dimension=OVERWORLD, scale=scale,
        )
        return read_png(path), colors

    @staticmethod
    def pixel(img, x, z, scale=1):
        row = img["rows"][(z - SEA_Z0) * scale]
        start = (x - SEA_X0) * scale * 3
        return tuple(row[start:start + 3])

    def test_size_honors_scale(self):
        img, _ = self.plan("plan_scaled.png", scale=3)
        self.assertEqual((img["width"], img["height"]), (48, 48))
        plain, _ = self.plan("plan_plain.png")
        self.assertEqual((plain["width"], plain["height"]), (16, 16))

    def test_a_room_reads_as_a_room(self):
        """Air under the roof draws cave-dark; air beside the building,
        with nothing but sky over it, draws light. That one distinction is
        the whole point of the renderer."""
        img, _ = self.plan("plan_room.png", scale=2)
        inside = self.pixel(img, SEA_X0 + 5, SEA_Z0 + 5, scale=2)
        outside = self.pixel(img, SEA_X0 + 13, SEA_Z0 + 13, scale=2)
        self.assertEqual(inside, CAVE)
        self.assertEqual(outside, SKY)
        self.assertNotEqual(inside, outside)

    def test_walls_draw_as_their_own_block(self):
        img, colors = self.plan("plan_walls.png")
        wall = self.pixel(img, self.BOX_X0, self.BOX_Z0 + 3)
        self.assertEqual(wall, colors("minecraft:stone_bricks"))
        self.assertNotIn(wall, (SKY, CAVE, UNGENERATED))
        self.assertEqual(colors.unknown, {})

    def test_a_gap_in_the_wall_shows_up_as_a_gap(self):
        """The question an area map cannot answer: is there a way in?"""
        doorway = (self.BOX_X0, self.CUT_Y, self.BOX_Z0 + 3)
        self.save.fill(doorway, doorway, "minecraft:air")
        try:
            img, _ = self.plan("plan_door.png")
            self.assertEqual(self.pixel(img, doorway[0], doorway[2]), CAVE,
                             "the doorway is air with a roof over it")
        finally:
            self.save.fill(doorway, doorway, "minecraft:stone_bricks")

    def test_ungenerated_ground_is_black(self):
        path = self.out("plan_edge.png")
        render_plan(
            self.save, 64, (100_000, 0, 100_000), (100_003, 0, 100_003),
            path, dimension=OVERWORLD,
        )
        img = read_png(path)
        self.assertEqual((img["width"], img["height"]), (4, 4))
        self.assertEqual(tuple(img["rows"][0][:3]), UNGENERATED)

    def test_corner_y_is_ignored_in_favor_of_the_y_argument(self):
        """The corners carry a y for symmetry with the other renderers; the
        story comes from the argument, and nowhere else."""
        first = self.out("plan_y_a.png")
        second = self.out("plan_y_b.png")
        render_plan(self.save, self.CUT_Y, (SEA_X0, 999, SEA_Z0),
                    (SEA_X0 + 7, -60, SEA_Z0 + 7), first, dimension=OVERWORLD)
        render_plan(self.save, self.CUT_Y, (SEA_X0, 0, SEA_Z0),
                    (SEA_X0 + 7, 0, SEA_Z0 + 7), second, dimension=OVERWORLD)
        with open(first, "rb") as a, open(second, "rb") as b:
            self.assertEqual(a.read(), b.read())


class CliTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="render-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.world = make_world(self.dir)

    def run_main(self, *argv):
        import contextlib
        import io

        from heaton_voxel.__main__ import main

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_area_subcommand_writes_a_png(self):
        target = os.path.join(self.dir, "cli.png")
        code, out, err = self.run_main(
            "render", self.world, "--area", str(SEA_X0), str(SEA_Z0),
            str(SEA_X0 + 7), str(SEA_Z0 + 7), "-o", target,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(read_png(target)["width"], 8)

    def test_slice_subcommand_writes_a_png(self):
        target = os.path.join(self.dir, "cli_slice.png")
        code, out, err = self.run_main(
            "render", self.world, "--slice", "x", str(SEA_X0 + 8),
            str(SEA_Z0), str(SEA_Z0 + 7), "40", "80", "-o", target,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(read_png(target)["height"], 41)

    def test_plan_subcommand_writes_a_png(self):
        target = os.path.join(self.dir, "cli_plan.png")
        code, out, err = self.run_main(
            "render", self.world, "--plan", "64", str(SEA_X0), str(SEA_Z0),
            str(SEA_X0 + 7), str(SEA_Z0 + 3), "-o", target, "--scale", "2",
        )
        self.assertEqual(code, 0, err)
        img = read_png(target)
        self.assertEqual((img["width"], img["height"]), (16, 8))

    def test_plan_relative_coordinates_without_a_player_explain_themselves(self):
        """'~' on the height argument needs a player, and this fixture world
        has none -- the refusal must say so rather than stack-trace."""
        code, _, err = self.run_main(
            "render", self.world, "--plan", "~", "0", "0", "8", "8"
        )
        self.assertEqual(code, 2)
        self.assertIn("player", err)

    def test_exactly_one_mode_is_required(self):
        code, _, err = self.run_main("render", self.world)
        self.assertEqual(code, 1)
        self.assertIn("exactly one", err)

    def test_two_modes_at_once_are_refused(self):
        """Naming two forms is as wrong as naming none -- and used to be
        accepted, because the check only knew about two."""
        code, _, err = self.run_main(
            "render", self.world,
            "--area", "0", "0", "8", "8",
            "--plan", "64", "0", "0", "8", "8",
        )
        self.assertEqual(code, 1)
        self.assertIn("exactly one", err)

    def test_relative_coordinates_without_a_player_explain_themselves(self):
        """This fixture world has no player data, so '~' cannot resolve --
        the refusal must say that, not stack-trace."""
        code, _, err = self.run_main(
            "render", self.world, "--area", "~", "0", "~8", "8"
        )
        self.assertEqual(code, 2)
        self.assertIn("player", err)

    def test_bad_slice_axis_is_refused_cleanly(self):
        code, _, err = self.run_main(
            "render", self.world, "--slice", "y", "0", "0", "1", "0", "1"
        )
        self.assertEqual(code, 2)
        self.assertIn("x or z", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
