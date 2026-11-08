"""Tests for the reusable logic inside examples/.

The examples are mostly glue, but the map renderer contains two things worth
pinning down: a hand-rolled PNG encoder, and the clustering that decides which
part of a world to draw. Both are easy to get subtly wrong in ways that only
show up as a corrupt file or a 30-million-block image.

The craft session is the other kind of example: a whole building session,
start to finish, which is only worth showing if it does what it says --
builds, checks, reports and saves only when told -- so it is run here, on
copies of the fixture, in each sample palette.
"""

from __future__ import annotations

import ast
import contextlib
import errno
import importlib.util
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)  # so `fixtures` and `heaton_voxel` resolve wherever run from
sys.path.insert(0, os.path.join(_ROOT, "examples"))

from biome_zone import (  # noqa: E402
    DIRECTIONS,
    HEIGHTMAP,
    heightmap_bits,
    surface_y,
    vertical_span,
)
import craft_session  # noqa: E402
from heaton_voxel import nbt  # noqa: E402
from heaton_voxel.blocks import Blocks  # noqa: E402
from heaton_voxel.craft import (  # noqa: E402
    NO_NEW_SNOW_OR_ICE,
    Builder,
    Palette,
    Worksite,
    list_worksites,
    reachable,
    simulate_block_light,
    spawnable,
    unsupported,
)
from heaton_voxel.craft.blockinfo import is_air  # noqa: E402
from fixtures import FIXTURE_LEVEL_DAT, FIXTURE_REGION, LOCK_HOLDER  # noqa: E402
from heaton_voxel.world import OVERWORLD, MinecraftSave  # noqa: E402
from palettes import LAB, PALETTES, ROMAN  # noqa: E402
from tests.test_settings import make_world  # noqa: E402

from world_map import (  # noqa: E402
    GLYPH_H,
    GLYPH_W,
    REGION_BLOCKS,
    UNGENERATED,
    UNREADABLE,
    ColorMap,
    PNGWriter,
    _GLYPHS,
    _draw_grid,
    _draw_text,
    largest_cluster,
)


def read_png(path):
    """Decode a PNG far enough to check it, without Pillow."""
    with open(path, "rb") as handle:
        data = handle.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "bad signature"
    pos = 8
    chunks = []
    idat = bytearray()
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])
        assert crc == zlib.crc32(tag + body) & 0xFFFFFFFF, f"bad CRC on {tag}"
        chunks.append(tag)
        if tag == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", body[:10])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(bytes(idat))
    stride = width * 3
    rows = []
    for i in range(height):
        start = i * (stride + 1)
        assert raw[start] == 0, "unexpected scanline filter"
        rows.append(raw[start + 1:start + 1 + stride])
    return {"width": width, "height": height, "depth": depth, "color": color,
            "chunks": chunks, "rows": rows}


class PNGWriterTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="png-")
        self.path = os.path.join(self.dir, "out.png")
        self.addCleanup(lambda: os.path.isdir(self.dir) and __import__("shutil").rmtree(self.dir))

    def test_writes_a_valid_png(self):
        with PNGWriter(self.path, 4, 3) as png:
            for _ in range(3):
                png.write_row(bytes([255, 0, 0] * 4))
        out = read_png(self.path)
        self.assertEqual((out["width"], out["height"]), (4, 3))
        self.assertEqual(out["depth"], 8)
        self.assertEqual(out["color"], 2, "should be truecolor RGB")
        self.assertEqual(out["chunks"][0], b"IHDR")
        self.assertEqual(out["chunks"][-1], b"IEND")

    def test_pixels_survive_exactly(self):
        want = [
            bytes([1, 2, 3, 4, 5, 6]),
            bytes([250, 251, 252, 253, 254, 255]),
            bytes([0, 0, 0, 255, 255, 255]),
        ]
        with PNGWriter(self.path, 2, 3) as png:
            for row in want:
                png.write_row(row)
        self.assertEqual(read_png(self.path)["rows"], want)

    def test_rejects_a_wrong_length_row(self):
        with self.assertRaises(ValueError):
            with PNGWriter(self.path, 4, 1) as png:
                png.write_row(bytes(9))  # 3 pixels, not 4

    def test_rejects_too_few_rows(self):
        with self.assertRaises(ValueError):
            png = PNGWriter(self.path, 2, 5)
            png.write_row(bytes(6))
            png.close()

    def test_survives_multiple_idat_chunks(self):
        """Rows are flushed once 1 MiB of *compressed* output has accumulated,
        so the data here has to be incompressible to reach that at all -- flat
        color would deflate to a few KiB and stay in one IDAT."""
        width, height = 512, 900
        state = 12345

        def noisy_row():
            nonlocal state
            out = bytearray()
            for _ in range(width * 3):
                state = (state * 1103515245 + 12345) & 0x7FFFFFFF
                out.append((state >> 16) & 255)
            return bytes(out)

        rows = [noisy_row() for _ in range(height)]
        with PNGWriter(self.path, width, height) as png:
            for row in rows:
                png.write_row(row)
        out = read_png(self.path)
        self.assertGreater(out["chunks"].count(b"IDAT"), 1, "expected several IDATs")
        self.assertEqual(out["rows"], rows, "pixels changed across the IDAT split")


class ColorMapTests(unittest.TestCase):
    def setUp(self):
        self.colors = ColorMap()

    def test_known_blocks(self):
        self.assertEqual(self.colors("minecraft:water"), (63, 118, 228))
        self.assertEqual(self.colors("minecraft:grass_block"), (95, 159, 53))

    def test_air_is_the_background(self):
        self.assertEqual(self.colors("minecraft:air"), UNGENERATED)

    def test_dye_families_are_expanded(self):
        self.assertEqual(self.colors("minecraft:red_wool"), (161, 39, 34))
        self.assertEqual(self.colors("minecraft:blue_concrete"), (53, 57, 157))

    def test_heuristics_catch_unlisted_blocks(self):
        for name in ("minecraft:jungle_leaves", "minecraft:cherry_leaves"):
            with self.subTest(name=name):
                self.assertEqual(self.colors(name), (58, 130, 41))
        self.assertNotEqual(self.colors("minecraft:mangrove_log"), (155, 155, 155))

    def test_unmapped_blocks_are_counted_every_time(self):
        """The color cache used to short-circuit the counter, making every
        unmapped block look like it appeared exactly once."""
        for _ in range(5):
            self.colors("modded:something_unheard_of")
        self.assertEqual(self.colors.unknown["modded:something_unheard_of"], 5)

    def test_known_blocks_are_not_counted_as_unknown(self):
        self.colors("minecraft:stone")
        self.assertEqual(self.colors.unknown, {})


class GridOverlayTests(unittest.TestCase):
    """The region grid is what makes a map navigable back to the files."""

    def _band(self, width):
        return bytearray(width * REGION_BLOCKS * 3)

    def test_every_glyph_is_the_declared_size(self):
        for char, glyph in _GLYPHS.items():
            with self.subTest(char=char):
                self.assertEqual(len(glyph), GLYPH_H)
                for row in glyph:
                    self.assertEqual(len(row), GLYPH_W)
                    self.assertTrue(set(row) <= {"0", "1"})

    def test_region_file_names_only_need_glyphs_we_have(self):
        for region_x, region_z in ((0, 0), (-12, 58594), (7, -14)):
            for char in f"r.{region_x}.{region_z}":
                self.assertIn(char, _GLYPHS, f"no glyph for {char!r}")

    def test_draws_boundaries_on_region_edges(self):
        width = REGION_BLOCKS * 2
        band = self._band(width)
        _draw_grid(band, width, 0, 0, present=set(), text_size=2)
        # A vertical line at x=0 and x=512, nothing at x=100.
        for x, expected in ((0, True), (REGION_BLOCKS, True), (100, False)):
            off = (300 * width + x) * 3  # row 300, clear of the labels
            lit = band[off] > 0
            self.assertEqual(lit, expected, f"x={x}")

    def test_labels_only_regions_that_exist(self):
        width = REGION_BLOCKS * 2
        with_file = self._band(width)
        _draw_grid(with_file, width, 0, 0, present={(0, 0)}, text_size=2)
        without = self._band(width)
        _draw_grid(without, width, 0, 0, present=set(), text_size=2)
        # The label sits in the top-left of cell (0,0); only one band has it.
        patch = slice((20 * width + 20) * 3, (20 * width + 120) * 3)
        self.assertNotEqual(with_file[patch], without[patch])

    def test_text_clips_at_the_edge_without_wrapping(self):
        """Drawing past the right edge must clip. Without the bounds check a
        pixel at x >= width lands at the start of the *next* row instead."""
        width = REGION_BLOCKS
        band = bytearray(b"\x20" * (width * REGION_BLOCKS * 3))
        _draw_text(band, width, width - 8, 40, "r.0.0", 4)

        self.assertEqual(len(band), width * REGION_BLOCKS * 3, "band resized")
        # Something landed in the sliver that is still on-screen...
        tail = band[(40 * width + width - 8) * 3:(40 * width + width) * 3]
        self.assertNotEqual(bytes(tail), b"\x20" * len(tail), "clipped everything")
        # ...and nothing wrapped onto the left edge of any following row.
        for row in range(40, 60):
            left = band[(row * width) * 3:(row * width + 8) * 3]
            self.assertEqual(bytes(left), b"\x20" * len(left), f"wrapped onto row {row}")

    def test_text_is_visible_over_terrain(self):
        """The backing box has to darken whatever is underneath, or labels
        vanish against pale ground."""
        width = REGION_BLOCKS
        band = bytearray(b"\xc8" * (width * REGION_BLOCKS * 3))  # pale terrain
        _draw_text(band, width, 10, 10, "r.0.0", 3)
        # Scan the whole label area rather than one row: the backing box starts
        # at the origin but the glyphs are inset by `size`.
        darker = brighter = 0
        for y in range(10, 10 + 30):
            for x in range(10, 10 + 100):
                value = band[(y * width + x) * 3]
                darker += value < 0xC8
                brighter += value > 0xC8
        self.assertGreater(darker, 0, "no darkened backing box")
        self.assertGreater(brighter, 0, "no bright glyph pixels")


class ClusterTests(unittest.TestCase):
    def test_single_blob(self):
        coords = {(0, 0), (1, 0), (0, 1), (1, 1)}
        self.assertEqual(largest_cluster(coords), coords)

    def test_far_flung_regions_are_dropped(self):
        """A world-border excursion must not define the map's bounds."""
        main = {(x, z) for x in range(6) for z in range(6)}
        outliers = {(58593, 58594), (58594, 58594)}
        self.assertEqual(largest_cluster(main | outliers), main)

    def test_gap_tolerance_bridges_small_holes(self):
        coords = {(0, 0), (2, 0)}  # one empty region between them
        self.assertEqual(largest_cluster(coords, gap=2), coords)

    def test_a_real_gap_splits_the_blobs(self):
        left = {(0, 0), (1, 0), (2, 0)}
        right = {(40, 0), (41, 0)}
        self.assertEqual(largest_cluster(left | right), left)

    def test_empty_input(self):
        self.assertEqual(largest_cluster(set()), set())

    def test_unreadable_is_visually_distinct_from_ungenerated(self):
        self.assertNotEqual(UNREADABLE, UNGENERATED)
        self.assertEqual(UNREADABLE, (255, 0, 255))


class HeightmapBitsTests(unittest.TestCase):
    """Getting this wrong unpacks the heightmap into plausible nonsense."""

    def test_current_overworld(self):
        # y -64..319 is 384 tall, so entries run 0..384 and need 9 bits.
        self.assertEqual(heightmap_bits(-64, 319), 9)

    def test_pre_1_18_height_would_need_fewer(self):
        # y 0..255 is 256 tall: 0..256 still needs 9 bits, not 8.
        self.assertEqual(heightmap_bits(0, 255), 9)

    def test_a_short_world(self):
        # 0..15 is 16 tall, entries 0..16, which needs 5 bits.
        self.assertEqual(heightmap_bits(0, 15), 5)

    def test_matches_the_fixture(self):
        """The vendored region is real game output; 9 bits packs to 37 longs."""
        bits = heightmap_bits(-64, 319)
        per_long = 64 // bits
        self.assertEqual((256 + per_long - 1) // per_long, 37)


class VerticalSpanTests(unittest.TestCase):
    MIN_Y, MAX_Y = -64, 319

    def span(self, player_y, ground):
        return vertical_span(player_y, ground, self.MIN_Y, self.MAX_Y)

    def test_standing_on_the_surface_fills_upward(self):
        """Feet sit one above the topmost solid block, so this is above ground."""
        y0, y1, word = self.span(63, 62)
        self.assertEqual((y0, y1), (62, self.MAX_Y))
        self.assertIn("sky", word)

    def test_high_above_the_surface_fills_upward(self):
        y0, y1, _ = self.span(200, 62)
        self.assertEqual((y0, y1), (62, self.MAX_Y))

    def test_underground_fills_downward(self):
        y0, y1, word = self.span(30, 62)
        self.assertEqual((y0, y1), (self.MIN_Y, 62))
        self.assertIn("bedrock", word)

    def test_level_with_the_surface_block_counts_as_underground(self):
        """y == ground means inside the topmost block, not standing on it."""
        y0, y1, _ = self.span(62, 62)
        self.assertEqual((y0, y1), (self.MIN_Y, 62))

    def test_the_two_directions_meet_at_ground_and_do_not_overlap(self):
        below = self.span(30, 62)
        above = self.span(70, 62)
        self.assertEqual(below[1], above[0], "ground level belongs to both spans")
        self.assertEqual(below[0], self.MIN_Y)
        self.assertEqual(above[1], self.MAX_Y)

    def test_span_never_escapes_the_world(self):
        for player_y in (-64, 0, 62, 63, 319):
            y0, y1, _ = self.span(player_y, 62)
            self.assertGreaterEqual(y0, self.MIN_Y)
            self.assertLessEqual(y1, self.MAX_Y)

    # -- explicit direction ------------------------------------------------

    def test_up_anchors_at_the_player_not_the_ground(self):
        """The whole point of the flag: auto measures from the ground."""
        y0, y1, _ = vertical_span(90, 62, self.MIN_Y, self.MAX_Y, "up")
        self.assertEqual((y0, y1), (90, self.MAX_Y))
        auto_y0, _, _ = vertical_span(90, 62, self.MIN_Y, self.MAX_Y, "auto")
        self.assertEqual(auto_y0, 62, "auto should still start at ground level")

    def test_down_anchors_at_the_player_not_the_ground(self):
        y0, y1, _ = vertical_span(90, 62, self.MIN_Y, self.MAX_Y, "down")
        self.assertEqual((y0, y1), (self.MIN_Y, 90))

    def test_down_overrides_the_automatic_choice(self):
        """A player above ground would auto-fill upward; down must not."""
        _, _, auto_word = vertical_span(90, 62, self.MIN_Y, self.MAX_Y, "auto")
        self.assertIn("sky", auto_word)
        y0, y1, word = vertical_span(90, 62, self.MIN_Y, self.MAX_Y, "down")
        self.assertIn("bedrock", word)
        self.assertEqual((y0, y1), (self.MIN_Y, 90))

    def test_up_overrides_the_automatic_choice(self):
        _, _, auto_word = vertical_span(30, 62, self.MIN_Y, self.MAX_Y, "auto")
        self.assertIn("bedrock", auto_word)
        y0, y1, word = vertical_span(30, 62, self.MIN_Y, self.MAX_Y, "up")
        self.assertIn("sky", word)
        self.assertEqual((y0, y1), (30, self.MAX_Y))

    def test_standing_on_a_hilltop_fills_from_the_feet(self):
        """ground == player_y is exactly where anchoring matters.

        auto would collapse to a zero-height span at the ground; down from the
        player still covers everything beneath them.
        """
        y0, y1, _ = vertical_span(87, 87, self.MIN_Y, self.MAX_Y, "down")
        self.assertEqual((y0, y1), (self.MIN_Y, 87))

    def test_explicit_direction_needs_no_ground_level(self):
        """An explicit direction must work where there is no heightmap."""
        self.assertEqual(
            vertical_span(70, None, self.MIN_Y, self.MAX_Y, "up")[:2],
            (70, self.MAX_Y),
        )
        self.assertEqual(
            vertical_span(70, None, self.MIN_Y, self.MAX_Y, "down")[:2],
            (self.MIN_Y, 70),
        )

    def test_auto_without_a_ground_level_is_refused(self):
        with self.assertRaises(ValueError):
            vertical_span(70, None, self.MIN_Y, self.MAX_Y, "auto")

    def test_unknown_direction_is_refused(self):
        with self.assertRaises(ValueError):
            vertical_span(70, 62, self.MIN_Y, self.MAX_Y, "sideways")

    def test_the_declared_directions_all_work(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                y0, y1, _ = vertical_span(70, 62, self.MIN_Y, self.MAX_Y, direction)
                self.assertGreaterEqual(y0, self.MIN_Y)
                self.assertLessEqual(y1, self.MAX_Y)
                self.assertLess(y0, y1)


class SurfaceYTests(unittest.TestCase):
    """surface_y against the vendored region, which has genuine heightmaps."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mcbiome-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        region_dir = os.path.join(self.dir, "dimensions/minecraft/overworld/region")
        os.makedirs(region_dir)
        shutil.copyfile(
            FIXTURE_REGION, os.path.join(region_dir, os.path.basename(FIXTURE_REGION))
        )
        shutil.copyfile(FIXTURE_LEVEL_DAT, os.path.join(self.dir, "level.dat"))
        self.save = MinecraftSave.load(self.dir)
        self.dim = self.save.dimension(OVERWORLD)

    def _a_real_chunk(self):
        for region in self.dim:
            for chunk in region:
                data = chunk.data
                if data is not None and str(data.get("Status")) == "minecraft:full":
                    return Blocks(chunk)
        self.skipTest("no fully-generated chunk in the fixture")

    def test_finds_a_surface_inside_the_world(self):
        accessor = self._a_real_chunk()
        found = surface_y(self.dim, accessor.world_x, accessor.world_z)
        self.assertIsNotNone(found)
        self.assertGreaterEqual(found, accessor.min_y)
        self.assertLessEqual(found, accessor.max_y)

    def test_the_surface_block_is_not_air_and_the_one_above_is(self):
        """The off-by-one that separates 'height' from 'topmost block y'.

        WORLD_SURFACE means topmost non-air, which includes water -- over the
        fixture's ocean this lands on the sea surface, not the seabed.
        """
        accessor = self._a_real_chunk()
        x, z = accessor.world_x, accessor.world_z
        found = surface_y(self.dim, x, z)
        self.assertNotEqual(
            self.save.get_block(x, found, z).name, "minecraft:air", "surface is air"
        )
        above = self.save.get_block(x, found + 1, z)
        self.assertEqual(above.name, "minecraft:air", "something sits above the surface")

    def test_ungenerated_column_has_no_surface(self):
        self.assertIsNone(surface_y(self.dim, 100_000, 100_000))

    def test_ocean_floor_is_never_above_the_world_surface(self):
        """The --heightmap choice has to actually reach a different map."""
        accessor = self._a_real_chunk()
        x, z = accessor.world_x, accessor.world_z
        top = surface_y(self.dim, x, z, "WORLD_SURFACE")
        floor = surface_y(self.dim, x, z, "OCEAN_FLOOR")
        self.assertIsNotNone(floor)
        self.assertLessEqual(floor, top)

    def test_default_heightmap_is_what_you_stand_on(self):
        """Regression: WORLD_SURFACE counts the plant you stand *in*.

        A player on grass with a flower at their feet has WORLD_SURFACE level
        with them, which vertical_span reads as underground and fills the wrong
        way. MOTION_BLOCKING reports the block that actually stops you, one
        lower, so the same player reads as above ground.
        """
        self.assertEqual(HEIGHTMAP, "MOTION_BLOCKING")

    def test_a_column_with_something_standing_on_it(self):
        """The fixture has torches and leaf litter over its surface."""
        found = None
        for x, z in self._sample_columns():
            top = surface_y(self.dim, x, z, "WORLD_SURFACE")
            standing = surface_y(self.dim, x, z, HEIGHTMAP)
            if top is not None and standing is not None and top != standing:
                found = (x, z, top, standing)
                break
        if found is None:
            self.skipTest("no column in the fixture where the two maps disagree")

        x, z, top, standing = found
        self.assertGreater(top, standing, "WORLD_SURFACE should sit higher")
        # A player standing on that block has their feet at standing+1. The
        # default must call that above ground; WORLD_SURFACE would not.
        player_y = standing + 1
        _, _, word = vertical_span(player_y, standing, -64, 319)
        self.assertIn("sky", word)
        _, _, wrong = vertical_span(player_y, top, -64, 319)
        self.assertIn("bedrock", wrong, "the bug this default prevents")

    def _sample_columns(self):
        for region in self.dim:
            for chunk in region:
                data = chunk.data
                if data is None or str(data.get("Status")) != "minecraft:full":
                    continue
                accessor = Blocks(chunk)
                for local_x in range(0, 16, 3):
                    for local_z in range(0, 16, 3):
                        yield accessor.world_x + local_x, accessor.world_z + local_z

    def test_unknown_heightmap_name_reports_no_surface(self):
        accessor = self._a_real_chunk()
        self.assertIsNone(
            surface_y(self.dim, accessor.world_x, accessor.world_z, "NOT_A_MAP")
        )

    def test_agrees_across_a_whole_chunk(self):
        """Every column resolves, and the index math does not wrap.

        A transposed x/z or a wrong stride still produces 256 plausible
        numbers, so the check is that each one really is the top solid block.
        """
        accessor = self._a_real_chunk()
        base_x, base_z = accessor.world_x, accessor.world_z
        for local_x in (0, 1, 15):
            for local_z in (0, 7, 15):
                x, z = base_x + local_x, base_z + local_z
                found = surface_y(self.dim, x, z)
                if found is None:
                    continue
                self.assertEqual(
                    self.save.get_block(x, found + 1, z).name,
                    "minecraft:air",
                    f"column ({x}, {z}) reports a surface with something above it",
                )


# --------------------------------------------------------------------------
# examples/palettes.py and examples/craft_session.py
# --------------------------------------------------------------------------

#: The fixture's one 2x2 patch of finished chunks, x -400..-369, z 16..47,
#: where test_castle.py builds too: a 32-block lot fits it exactly. It is
#: ocean, so the lot is filled up to the sea from its bed.
LOT_CENTER = (-384, 32)

# World-relative, with forward slashes on every platform, as a worksite
# names its files; Windows takes them back in a filesystem path.
REGION = "dimensions/minecraft/overworld/region"
ENTITIES = "dimensions/minecraft/overworld/entities"

#: The biome every session held to its villager's type is given under the
#: villager's column first: one the game types a villager by -- snow --
#: where the fixture's cold ocean gives the default, plains, which a
#: villager typed by nothing at all would be too.
SNOWY = "minecraft:snowy_plains"

#: The lamp posts and the doors each build had, spelled out here rather
#: than read from the session's FITTINGS, so a change there is one these
#: see (PaletteTests holds FITTINGS to the tools' own constants).
POSTS = {"roman": ("minecraft:sandstone_wall", "minecraft:lantern"),
         "lab": ("minecraft:polished_blackstone_wall",
                 "minecraft:sea_lantern")}
DOORS = {"roman": "minecraft:spruce_door", "lab": "minecraft:iron_door"}


def _files(world: str) -> dict[str, bytes]:
    """Every file under a world directory, world-relative -> bytes."""
    found = {}
    for root, _, names in os.walk(world):
        for name in names:
            path = os.path.join(root, name)
            with open(path, "rb") as handle:
                found[os.path.relpath(path, world).replace(os.sep, "/")] = handle.read()
    return found


def _outside_worksites(files: dict[str, bytes]) -> dict[str, bytes]:
    return {rel: data for rel, data in files.items()
            if not rel.startswith("worksites/")}


def _quietly(call, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return call(*args, **kwargs)


def _load_tool(*parts: str):
    """A module of tools/, loaded by its path, since tools/ is no package.
    Only ever one that is a table of materials and writes nothing when
    imported."""
    path = os.path.join(_ROOT, "tools", *parts)
    spec = importlib.util.spec_from_file_location(
        "_tool_" + "_".join(parts)[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _strings(*parts: str) -> dict[str, str]:
    """The module-level string constants of a tools/ script, read from its
    source without running it -- for a script that loads a world when
    imported."""
    with open(os.path.join(_ROOT, "tools", *parts), encoding="utf-8") as h:
        tree = ast.parse(h.read())
    return {node.targets[0].id: node.value.value for node in tree.body
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)}


def _snowy_villager(world: str) -> None:
    """Give the column the session's villager stands in :data:`SNOWY`."""
    vx, vz = craft_session.Scene(*LOT_CENTER, 0).villager
    save = MinecraftSave.load(world)
    save.fill_biome((vx, -64, vz), (vx, 319, vz), SNOWY)
    save.save()


class _WorldFiles:
    """Compares worlds file by file, never with an ``assertEqual`` of their
    bytes, whose diff of a region file takes a minute to build and says
    nothing: the names first, then each file, the first that differs
    named."""

    def assertSameFiles(self, found: dict[str, bytes],
                        expected: dict[str, bytes]) -> None:
        self.assertEqual(sorted(found), sorted(expected))
        for rel in sorted(expected):
            self.assertTrue(found[rel] == expected[rel], f"{rel} differs")


class PaletteTests(unittest.TestCase):
    """The sample palettes are the builds they are named for."""

    def test_the_roman_palette_is_the_roman_gable_wrapper(self):
        """tools/roman_city/common.py wrapped gable_roof to name three ids
        by hand -- granite stairs and slabs, travertine gables. Left to
        the ROMAN palette, the writer lays the same bytes."""
        scratch = tempfile.mkdtemp(prefix="palette-tests-")
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        regions = []
        for copy, palette in (("palette", ROMAN), ("by-hand", None)):
            world = make_world(os.path.join(scratch, copy))
            save = MinecraftSave.load(world)
            b = Builder(save, palette=palette)
            for args in ((-398, 18, -390, 25, 63, "x"),
                         (-385, 18, -380, 27, 63, "z")):
                if palette is None:
                    b.gable_roof(*args, stair="minecraft:granite_stairs",
                                 plank="minecraft:smooth_sandstone",
                                 slab="minecraft:granite_slab")
                else:
                    b.gable_roof(*args)
            save.save()
            region = os.path.join(world, REGION, "r.-1.0.mca")
            with open(region, "rb") as handle:
                regions.append(handle.read())
        self.assertTrue(regions[0] == regions[1])

    def test_each_role_is_read_off_the_build_it_is_named_for(self):
        """Every role of both palettes, and the session's fittings from
        each build, held to the constants of the scripts that laid them:
        the two tables of materials loaded by path, and the Roman
        districts' strings read from their source. A role neither build
        used keeps the writers' default."""
        roman = _load_tool("roman_city", "common.py")
        districts = _strings("roman_city", "jw_districts.py")
        lab = _load_tool("secret_lab", "lab_common.py")
        defaults = Palette()

        self.assertEqual(ROMAN["road"].entries, tuple(roman.VIA_MIX))
        self.assertEqual(ROMAN["paving"].entries, tuple(roman.PLAZA_MIX))
        for role in ("retaining", "trim", "frame"):
            self.assertEqual(ROMAN[role], roman.TRAV_CUT, role)
        self.assertEqual(ROMAN["plinth"], roman.BRICK)
        self.assertEqual(ROMAN["wall"], districts["STUCCO"])
        self.assertEqual(ROMAN["floor"], districts["PLANKS"])
        self.assertEqual((ROMAN["roof"].base, ROMAN["roof"].form("stairs"),
                          ROMAN["roof"].form("slab")),
                         (roman.TILE, roman.TILE_STAIR, roman.TILE_SLAB))
        self.assertEqual(ROMAN["gable"], roman.TRAV)
        for role in ("pad", "light", "glass"):
            self.assertEqual(ROMAN[role], defaults[role], role)

        self.assertEqual(LAB["road"].entries, tuple(lab.LAB_ROAD_MIX))
        self.assertEqual(LAB["paving"].entries, tuple(lab.PLAZA_MIX))
        for role in ("retaining", "trim", "plinth"):
            self.assertEqual(LAB[role], lab.SLATE, role)
        self.assertEqual(LAB["light"], lab.GLOW)
        self.assertEqual((LAB["frame"], LAB["gable"]), (lab.PANEL2,) * 2)
        self.assertEqual(LAB["wall"], lab.PANEL)
        self.assertEqual(LAB["floor"], lab.TILES)
        self.assertEqual((LAB["roof"].base, LAB["roof"].form("stairs"),
                          LAB["roof"].form("slab")),
                         (lab.SLATE, lab.SLATE_STAIR, lab.SLATE_SLAB))
        self.assertEqual(LAB["glass"], lab.SKYPANE)
        self.assertEqual(LAB["pad"], defaults["pad"])

        roman_fit = craft_session.FITTINGS["roman"]
        self.assertEqual(roman_fit["post"], (roman.TRAV_WALL,) * 2)
        self.assertEqual(roman_fit["wood"], districts["PLANKS"])
        lab_fit = craft_session.FITTINGS["lab"]
        self.assertEqual(lab_fit["post"], (lab.BLACK_WALL,) * 2)
        self.assertEqual(
            (lab_fit["lamp"], lab_fit["door"], lab_fit["stair"],
             lab_fit["wood"]),
            (lab.GLOW, lab.IRON_DOOR, lab.SLATE_STAIR, lab.PANEL))
        for palette, fittings in craft_session.FITTINGS.items():
            self.assertEqual((fittings["post"][0], fittings["lamp"]),
                             POSTS[palette])
            self.assertEqual(fittings["door"], DOORS[palette])

    def test_each_palette_is_named_for_the_session_to_take(self):
        self.assertEqual(PALETTES, {"roman": ROMAN, "lab": LAB})
        self.assertEqual(set(craft_session.FITTINGS), set(PALETTES))
        self.assertNotEqual(ROMAN, LAB)


class CraftSessionTests(_WorldFiles, unittest.TestCase):
    """examples/craft_session.py on copies of the fixture, in each palette:
    built twice with ``write=True`` into two worlds, each with the
    villager's column made snowy first; once more as a dry run through the
    command line; with each check made to fail, with the world written
    under it, with the game opening it after the save and with a read-back
    that differs; and refused while the world is open."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="craft-session-tests-")
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.original = MinecraftSave.load(
            make_world(os.path.join(cls.tmp, "original")))
        cls.runs, cls.before, cls.printed = {}, {}, {}
        for palette in PALETTES:
            for copy in ("first", "second"):
                world = make_world(os.path.join(cls.tmp, palette, copy))
                _snowy_villager(world)
                cls.before[palette, copy] = _files(world)
                lines = []
                session = _quietly(craft_session.run, world, *LOT_CENTER,
                                   palette=palette, write=True,
                                   out=lines.append)
                cls.runs[palette, copy] = (world, session)
                cls.printed[palette, copy] = "\n".join(lines)

    def sessions(self):
        """``(palette, world, session)`` for each palette's first run."""
        return [(palette, *self.runs[palette, "first"])
                for palette in PALETTES]

    def test_each_palette_builds_its_lot_and_passes_every_check(self):
        for palette, world, session in self.sessions():
            with self.subTest(palette=palette):
                self.assertEqual(session.problems, [])
                self.assertTrue(session.saved)
                (site,) = list_worksites(world)
                self.assertEqual(site.name, "craft-session")
                self.assertIsNotNone(site.accepted)
                self.assertEqual(sorted(site.zones), [
                    f"beside the lot, {side}"
                    for side in ("east", "north", "south", "west")])
                self.assertEqual(session.check, [])
                self.assertEqual(session.spawns, [])
                scene, materials = session.scene, PALETTES[palette]
                saved = MinecraftSave.load(world)
                wall_z = scene.house[3]
                door = saved.get_block(scene.door, scene.y + 1, wall_z)
                self.assertEqual(door.name, DOORS[palette])
                if palette == "lab":
                    # An iron door, opened by a button on each side of
                    # its wall.
                    buttons = saved.find_blocks(
                        (scene.door - 2, scene.y + 1, wall_z - 1),
                        (scene.door + 2, scene.y + 3, wall_z + 1),
                        "polished_blackstone_button").matches
                    self.assertEqual(sorted(z - wall_z
                                            for _, _, z, _ in buttons),
                                     [-1, 1])
                x0, z0, x1, z1 = scene.house
                names = saved.histogram((x0 - 1, scene.y, z0 - 1),
                                        (x1 + 1, scene.top + 8, z1 + 1)).counts
                for block in (materials["roof"].form("stairs"),
                              materials["wall"], materials["floor"]):
                    self.assertIn(block, {state.name for state in names})
                # The villager, standing in the square, typed by the biome at
                # its feet: snow, in the snowy plains its column was given.
                vx, vz = scene.villager
                found = saved.entities_in((vx, scene.y + 1, vz),
                                          (vx, scene.y + 1, vz),
                                          ids="villager").entities
                self.assertEqual(len(found), 1)
                data = found[0][4]["VillagerData"]
                self.assertEqual(str(data["profession"]),
                                 f"minecraft:{craft_session.PROFESSION}")
                self.assertEqual(saved.get_biome(vx, scene.y + 1, vz), SNOWY)
                self.assertEqual(str(data["type"]), "minecraft:snow")
                self.assertEqual(
                    session.rollback.files,
                    [f"{ENTITIES}/r.-1.0.mca",
                     f"{REGION}/r.-1.0.mca"],
                    "undoing it puts the region back and takes the "
                    "villager's entities file away")

    def test_the_lane_the_square_and_the_tower_stand_as_built(self):
        """Read back from disk: a lamp post of the build's own beside the
        lane; every corner of the tower a merlon; the square lit, by the
        light that lasts, to the level no snow or ice forms at."""
        self.assertEqual(int(NO_NEW_SNOW_OR_ICE), 10)
        for palette, world, session in self.sessions():
            with self.subTest(palette=palette):
                saved = MinecraftSave.load(world)
                scene, built = session.scene, session.built
                post, lamp = POSTS[palette]
                self.assertTrue(built["lamps"].posts)
                for x, y, z in built["lamps"].posts:
                    self.assertEqual(
                        [saved.get_block(x, y + dy, z).name
                         for dy in range(3)], [post, post, lamp])
                tx0, tz0, tx1, tz1 = scene.tower
                merlons = set(built["merlons"])
                self.assertEqual(len(merlons), 8)
                for corner in ((tx0, tz0), (tx1, tz0), (tx0, tz1),
                               (tx1, tz1)):
                    self.assertIn((corner[0], scene.deck + 2, corner[1]),
                                  merlons)
                for x, y, z in merlons:
                    self.assertEqual(saved.get_block(x, y, z).name,
                                     PALETTES[palette]["plinth"])
                    self.assertTrue(is_air(saved.get_block(x, y + 1, z).name))
                # Nobody climbs to the deck, and nothing spawns on it: not
                # inside the parapet, in its gaps or on a merlon.
                self.assertEqual(spawnable(saved, (tx0, scene.deck, tz0),
                                           (tx1, scene.deck + 4, tz1)), [])
                sx0, sz0, sx1, sz1 = scene.square
                feet = scene.y + 1
                light = simulate_block_light(saved, (sx0, feet, sz0),
                                             (sx1, feet, sz1), durable=True)
                dark = [(x, z) for x in range(sx0, sx1 + 1)
                        for z in range(sz0, sz1 + 1)
                        if is_air(saved.get_block(x, feet, z).name)
                        and light.level(x, feet, z) < 10]
                self.assertEqual(dark, [])

    def test_finish_adds_nothing_the_game_will_not_keep(self):
        """The fixture has gravel over holes down by the bedrock, which the
        audit reports; the session adds nothing to them, by its own count
        and by an audit of the saved world against the original's."""
        for palette, world, session in self.sessions():
            with self.subTest(palette=palette):
                self.assertEqual(session.new_faults, [])
                x0, z0, x1, z1 = session.scene.lot
                box = ((x0 - 2, -64, z0 - 2), (x1 + 2, 319, z1 + 2))
                before = {craft_session.fault_key(fault)
                          for fault in unsupported(self.original, *box)}
                self.assertTrue(before, "the fixture's own faults are there")
                audited = {craft_session.fault_key(fault)
                           for fault in session.finish.unsupported}
                self.assertLessEqual(audited, before)
                after = {craft_session.fault_key(fault) for fault in
                         unsupported(MinecraftSave.load(world), *box)}
                self.assertLessEqual(after, before)

    def test_the_house_walks_from_its_door_to_the_top_story_and_back(self):
        """Read back from disk: in at the door -- an iron one in the lab's
        palette, opened by its buttons -- up the flight, round the upper
        story past the bed, and home again."""
        for palette, world, session in self.sessions():
            with self.subTest(palette=palette):
                scene = session.scene
                x0, z0, x1, z1 = scene.house
                rx0, rz0, rx1, rz1 = scene.room
                walk = reachable(MinecraftSave.load(world), scene.doorstep,
                                 (x0, scene.y - 1, z0),
                                 (x1, scene.top + 1, scene.doorstep[2]))
                self.assertEqual(walk.stranded, frozenset())
                upstairs = walk.footing(scene.upper)
                for corner in ((rx0, rz0), (rx1, rz0), (rx1, rz1)):
                    self.assertIn(corner, upstairs)
                self.assertIn((rx1, rz1), walk.footing(scene.y))
                self.assertEqual(walk.footing(scene.upper),
                                 session.walk.footing(scene.upper),
                                 "the session walked what reached the disk")

    def test_what_the_build_changed_is_what_the_builder_wrote(self):
        """The diff is the content truth and the journal the record: every
        cell the diff names, the Builder wrote, read before and after from
        two worlds loaded apart; and a cell the Builder wrote that the
        diff leaves out holds just what it held. (Not the other way round:
        the headroom over a pad and a lane is air written over air, which
        the journal lists and nothing changed.)"""
        for palette, world, session in self.sessions():
            with self.subTest(palette=palette):
                saved = MinecraftSave.load(world)
                self.assertGreater(len(session.diff), 1000)
                self.assertLessEqual(set(session.diff), session.written)
                for cell, change in session.diff.items():
                    self.assertEqual(change.before,
                                     self.original.get_block(*cell))
                    self.assertEqual(change.after, saved.get_block(*cell))
                for cell in sorted(session.written - set(session.diff)):
                    self.assertEqual(saved.get_block(*cell),
                                     self.original.get_block(*cell), cell)

    def test_two_runs_encode_identically(self):
        """Every region file, byte for byte. (Not the entities file: every
        spawn mints its villager a fresh, random UUID.)"""
        for palette in PALETTES:
            with self.subTest(palette=palette):
                first, _ = self.runs[palette, "first"]
                second, _ = self.runs[palette, "second"]
                names = sorted(os.listdir(os.path.join(first, REGION)))
                self.assertEqual(names, sorted(os.listdir(
                    os.path.join(second, REGION))))
                for name in names:
                    with open(os.path.join(first, REGION, name), "rb") as a:
                        ours = a.read()
                    with open(os.path.join(second, REGION, name), "rb") as b:
                        self.assertTrue(b.read() == ours, name)

    def test_the_rollback_plan_is_printed(self):
        """After the save, the command line's own dry run of the undo:
        the region put back, the entities file the villager made taken
        away, and nothing moved. (And before it, the journal's count: its
        cells, and its writes by stage, which count a cell written twice
        twice and so add up to more.)"""
        for palette in PALETTES:
            with self.subTest(palette=palette):
                _, session = self.runs[palette, "first"]
                printed = self.printed[palette, "first"]
                writes = sum(session.tally.values())
                self.assertGreater(writes, len(session.written))
                self.assertIn(f"the Builder wrote {len(session.written):,} "
                              f"cells in {writes:,} writes, by stage: ",
                              printed)
                self.assertIn("the undo would move 2 files:", printed)
                self.assertRegex(printed, r"\n  delete +" + re.escape(
                    f"{ENTITIES}/r.-1.0.mca") + " ")
                self.assertRegex(printed, r"\n  restore +" + re.escape(
                    f"{REGION}/r.-1.0.mca") + " ")
                self.assertIn("dry run: nothing moved", printed)

    def test_rolling_a_session_back_puts_every_file_back(self):
        """``Worksite.rollback`` on a copy of a saved session: every file
        outside the worksite byte for byte as it was before the session --
        the entities file the villager made gone again."""
        for palette in PALETTES:
            with self.subTest(palette=palette):
                world, _ = self.runs[palette, "first"]
                copy = os.path.join(self.tmp, "rolled-back", palette)
                shutil.copytree(world, copy)
                undone = Worksite.rollback(copy, "craft-session")
                self.assertEqual(len(undone.files), 2)
                self.assertSameFiles(_outside_worksites(_files(copy)),
                                     self.before[palette, "first"])

    def test_a_dry_run_writes_nothing(self):
        """No --write: built, checked and reported -- and the world's
        directory byte for byte as it was, not even a worksite in it. (In
        the lab's palette, which the command line takes by name: the
        other tests run it in the default.)"""
        world = make_world(os.path.join(self.tmp, "dry"))
        before = _files(world)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = craft_session.main([world, "--at", *map(str, LOT_CENTER),
                                       "--palette", "lab"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("building the lab lot", out.getvalue())
        self.assertIn("dry run: nothing was saved", out.getvalue())
        self.assertIn("worksite rollback (dry run): nothing to move -- the "
                      "world is as the snapshot found it", out.getvalue())
        self.assertSameFiles(_files(world), before)

    def test_a_failed_check_saves_nothing(self):
        """A torch hung on nothing, added after the build: finish()'s audit
        finds it, the session exits 1, and with --write the build is not
        saved -- only the snapshot it took first is left, never accepted."""
        world = make_world(os.path.join(self.tmp, "failed"))
        before = _files(world)
        real = craft_session.build

        def build(b, scene, fittings):
            done = real(b, scene, fittings)
            x0, z0 = scene.lot[:2]
            b.set(x0 + 30, scene.y + 3, z0 + 1,
                  "minecraft:wall_torch[facing=north]")
            return done

        out = io.StringIO()
        with mock.patch.object(craft_session, "build", build), \
                contextlib.redirect_stdout(out):
            code = craft_session.main([world, "--at", *map(str, LOT_CENTER),
                                       "--write"])
        self.assertEqual(code, 1)
        self.assertIn("FAILED: the game will not keep", out.getvalue())
        after = _files(world)
        self.assertSameFiles(_outside_worksites(after), before)
        manifest = json.loads(after["worksites/craft-session/manifest.json"])
        self.assertIsNone(manifest["accepted"])

    def test_every_check_says_what_it_found(self):
        """Each check, made to fail by what is set after the build, names
        what it found, and the session saves nothing: a stone in the
        stairwell three over the flight's second tread, which makes the
        stair one to climb and not to come down; the downstairs lamp taken
        out; a block set beside the lot round the Builder, as another tool
        would set it; and, alone, a stone across the stairwell one tread
        further up, which leaves no way up at all."""

        def stairwell(dz):
            def spoil(b, scene):
                fx, _, fz = scene.flight
                b.set(fx, scene.upper, fz + dz, "minecraft:stone")
            return spoil

        def lamp_out(b, scene):
            rx0, rz0 = scene.room[:2]
            for y in (scene.y + 1, scene.y + 2):
                b.set(rx0 + 2, y, rz0 + 3, "minecraft:air")

        def beside(b, scene):
            _, z0, x1, _ = scene.lot
            b.save.set_block(x1 + craft_session.BESIDE, scene.y, z0 + 5,
                             "minecraft:stone")

        cases = [
            ((stairwell(-1), lamp_out, beside),
             ["cells of the house can be reached and not left",
              "cells the scene lit are dark enough to spawn in",
              "site.check() found ['beside the lot, east']"]),
            ((stairwell(-2),),
             ["the top story cannot be reached from the door"]),
        ]
        real = craft_session.build
        for number, (spoilers, found) in enumerate(cases):
            with self.subTest(found=found):
                world = make_world(os.path.join(self.tmp, f"spoiled-{number}"))
                before = _files(world)

                def build(b, scene, fittings):
                    done = real(b, scene, fittings)
                    for spoil in spoilers:
                        spoil(b, scene)
                    return done

                lines = []
                with mock.patch.object(craft_session, "build", build):
                    session = _quietly(craft_session.run, world, *LOT_CENTER,
                                       out=lines.append)
                self.assertEqual(len(session.problems), len(found),
                                 session.problems)
                for expected in found:
                    self.assertTrue(any(expected in problem
                                        for problem in session.problems),
                                    (expected, session.problems))
                self.assertFalse(session.saved)
                self.assertIn("nothing was saved", lines)
                self.assertSameFiles(_files(world), before)

    def test_a_failed_session_leaves_the_world_as_it_now_is(self):
        """Nothing of a failed session's reached the disk, so it puts
        nothing back: a world file somebody else wrote while it ran -- the
        game, saving -- is left as they wrote it, never restored to the
        snapshot under them, and the rollback's dry run says it changed."""
        world = make_world(os.path.join(self.tmp, "played-in"))
        region = os.path.join(world, REGION, "r.-1.0.mca")
        with open(region, "rb") as handle:
            played = handle.read() + bytes(4096)
        real = craft_session.build

        def build(b, scene, fittings):
            done = real(b, scene, fittings)
            with open(region, "ab") as handle:       # the game saving
                handle.write(bytes(4096))
            x0, z0 = scene.lot[:2]
            b.set(x0 + 30, scene.y + 3, z0 + 1,
                  "minecraft:wall_torch[facing=north]")
            return done

        out = io.StringIO()
        with mock.patch.object(craft_session, "build", build), \
                contextlib.redirect_stdout(out):
            code = craft_session.main([world, "--at", *map(str, LOT_CENTER)])
        self.assertEqual(code, 1, out.getvalue())
        with open(region, "rb") as handle:
            self.assertTrue(handle.read() == played,
                            "the region was put back over the play")
        self.assertIn(f"1 of the world's files changed while the session "
                      f"ran, not by it: {REGION}/r.-1.0.mca",
                      out.getvalue())

    def test_once_the_build_is_saved_nothing_raises(self):
        """An after render that cannot be written, and the game opening
        the world once the build is saved, leave the build saved and
        accepted: each is a note, the session exits 0, and nothing says
        the build went unsaved."""
        world = make_world(os.path.join(self.tmp, "opened-after"))
        site_module = sys.modules["heaton_voxel.craft.worksite"]
        real_render, real_open = Worksite.render, site_module.world_is_open
        opened = []

        def render(site, label, **kwargs):
            if label == "after":
                opened.append(label)              # and the game takes it
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_render(site, label, **kwargs)

        def world_is_open(path):
            return bool(opened) or real_open(path)

        out, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(Worksite, "render", render), \
                mock.patch.object(site_module, "world_is_open",
                                  world_is_open), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(errors):
            code = craft_session.main([world, "--at", *map(str, LOT_CENTER),
                                       "--write"])
        printed = out.getvalue()
        self.assertEqual(code, 0, printed + errors.getvalue())
        self.assertEqual(errors.getvalue(), "")
        self.assertIn("note: the after render was not written", printed)
        self.assertIn("saved and accepted", printed)
        self.assertIn("worksite rollback (dry run) refused", printed)
        (site,) = list_worksites(world)
        self.assertIsNotNone(site.accepted)

    def test_a_read_back_that_differs_is_saved_and_not_accepted(self):
        """What reached the disk read back as something else: the session
        exits 1, the build saved and the worksite not accepted, with its
        snapshot there to undo it."""
        world = make_world(os.path.join(self.tmp, "torn"))
        before = _files(world)
        x0, z0 = craft_session.Scene.lot_of(*LOT_CENTER)[:2]

        class Torn(MinecraftSave):
            @classmethod
            def load(cls, *args, **kwargs):
                save = MinecraftSave.load(*args, **kwargs)
                save.set_block(x0 + 1, 70, z0 + 1, "minecraft:stone")
                return save

        out = io.StringIO()
        with mock.patch.object(craft_session, "MinecraftSave", Torn), \
                contextlib.redirect_stdout(out):
            code = craft_session.main([world, "--at", *map(str, LOT_CENTER),
                                       "--write"])
        self.assertEqual(code, 1, out.getvalue())
        self.assertIn("FAILED: what reached the disk is not what was built; "
                      "saved, not accepted", out.getvalue())
        rel = f"{REGION}/r.-1.0.mca"
        self.assertFalse(_files(world)[rel] == before[rel], "saved")
        (site,) = list_worksites(world)
        self.assertIsNone(site.accepted)

    def test_an_open_world_is_refused(self):
        world = make_world(os.path.join(self.tmp, "open"))
        lock = os.path.join(world, nbt.SESSION_LOCK)
        with open(lock, "wb") as handle:
            handle.write("☃".encode())
        before = _files(world)
        holder = subprocess.Popen(
            [sys.executable, "-c", LOCK_HOLDER, lock],
            stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            for extra in ([], ["--write"]):
                errors = io.StringIO()
                with contextlib.redirect_stderr(errors):
                    code = _quietly(craft_session.main,
                                    [world, "--at", *map(str, LOT_CENTER),
                                     *extra])
                self.assertEqual(code, 2)
                self.assertIn("open in Minecraft", errors.getvalue())
                with self.assertRaises(nbt.NBTError):
                    craft_session.run(world, *LOT_CENTER, write=True,
                                      out=lambda *_: None)
        finally:
            holder.kill()
            holder.wait()
            holder.stdout.close()
        self.assertSameFiles(_files(world), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
