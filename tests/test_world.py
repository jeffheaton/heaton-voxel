"""Tests for MinecraftSave: whole-save access, player position, box fill.

These build a save from scratch in a temp directory rather than needing a real
world, so the boundary cases can be constructed exactly -- chunks that stop at
a known height, regions that do and do not exist, coordinates either side of
zero. Where a real chunk is wanted, the vendored fixture supplies one.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import heaton_voxel.nbt as nbt
from heaton_voxel.blocks import BLOCKS_PER_SECTION, BlockState, Blocks
from fixtures import FIXTURE_LEVEL_DAT, FIXTURE_REGION
from heaton_voxel.nbt import (
    NBT,
    TAG_DOUBLE,
    Chunk,
    NBTError,
    TagCompound,
    TagDouble,
    TagInt,
    TagList,
    TagString,
)
from heaton_voxel.world import (
    END,
    NETHER,
    OVERWORLD,
    BiomeFillResult,
    Dimension,
    EntityQueryResult,
    FillResult,
    FindResult,
    MinecraftSave,
)
from tests.test_blocks import SequenceAssertions

DATA_VERSION = 4903
SECTION_RANGE = range(-4, 20)  # y -64..319, the current overworld
MIN_Y = SECTION_RANGE.start * 16
MAX_Y = SECTION_RANGE.stop * 16 - 1


def make_chunk(chunk_x: int, chunk_z: int, fill: str = "minecraft:air") -> Chunk:
    """A structurally complete chunk of uniform blocks."""
    sections = TagList(element_type=nbt.TAG_COMPOUND)
    for section_y in SECTION_RANGE:
        sections.append(
            TagCompound(
                {
                    "Y": nbt.TagByte(section_y),
                    "block_states": TagCompound(
                        {
                            "palette": TagList(
                                [BlockState(fill).to_nbt()],
                                element_type=nbt.TAG_COMPOUND,
                            )
                        }
                    ),
                    "biomes": TagCompound(
                        {
                            "palette": TagList(
                                [TagString("minecraft:plains")],
                                element_type=nbt.TAG_STRING,
                            )
                        }
                    ),
                }
            )
        )
    chunk = Chunk(chunk_x & 31, chunk_z & 31)
    chunk.data = TagCompound(
        {
            "DataVersion": TagInt(DATA_VERSION),
            "xPos": TagInt(chunk_x),
            "zPos": TagInt(chunk_z),
            "yPos": TagInt(SECTION_RANGE.start),
            "Status": TagString("minecraft:full"),
            "sections": sections,
        }
    )
    return chunk


#: What :func:`make_save` wrote for each list of chunks and player, as
#: ``(relative path, bytes)`` pairs in the order it wrote them.
_MADE: dict[tuple, tuple[tuple[str, bytes], ...]] = {}

#: The overworld's region directory, which every save made here has, a
#: save of no chunks included: a save without one does not load.
_REGION_DIR = os.path.join("dimensions", "minecraft", "overworld", "region")


def make_save(directory: str, chunks: list[tuple[int, int]], *, player=(8.5, 70.0, 8.5)):
    """Write a minimal but genuine save containing exactly these chunks.

    The same chunks and player always make the same files, so the suite's
    thousand-odd calls encode and write them durably once each, and every
    other call writes the bytes they came to -- each new file 0600, as the
    library's atomic write leaves a new file, and one already there keeping
    its mode -- and makes the region directory, which holds no file when
    there are no chunks. ``MakeSaveTests`` holds every call to what the
    library writes."""
    chunks = list(chunks)
    key = (tuple(chunks), tuple(player))
    if key not in _MADE:
        _MADE[key] = _write_save(directory, chunks, player)
        return directory
    os.makedirs(os.path.join(directory, _REGION_DIR), exist_ok=True)
    for rel, data in _MADE[key]:
        path = os.path.join(directory, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    return directory


def _write_save(directory: str, chunks: list[tuple[int, int]], player
                ) -> tuple[tuple[str, bytes], ...]:
    """:func:`make_save`'s files, written into ``directory`` through the
    library, and read back as ``(relative path, bytes)``."""
    written = []
    region_dir = os.path.join(directory, _REGION_DIR)
    os.makedirs(region_dir, exist_ok=True)

    by_region: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for chunk_x, chunk_z in chunks:
        by_region.setdefault((chunk_x >> 5, chunk_z >> 5), []).append((chunk_x, chunk_z))
    for (region_x, region_z), members in by_region.items():
        region = NBT()
        region.kind = "region"
        region.region_x, region.region_z = region_x, region_z
        for chunk_x, chunk_z in members:
            chunk = make_chunk(chunk_x, chunk_z)
            region.chunks[(chunk_x & 31, chunk_z & 31)] = chunk
        written.append(os.path.join(region_dir, f"r.{region_x}.{region_z}.mca"))
        region.save(written[-1])

    level = NBT()
    level.kind = "nbt"
    level.compression = nbt.COMPRESSION_GZIP
    level.root = TagCompound(
        {
            "Data": TagCompound(
                {
                    "LevelName": TagString("test"),
                    "DataVersion": TagInt(DATA_VERSION),
                    "spawn": TagCompound(
                        {
                            "pos": nbt.TagIntArray([1, 2, 3]),
                            "dimension": TagString(OVERWORLD),
                        }
                    ),
                    "singleplayer_uuid": nbt.TagIntArray(
                        [-1106290927, -1783871873, -1161649435, -1312549508]
                    ),
                }
            )
        }
    )
    written.append(os.path.join(directory, "level.dat"))
    level.save(written[-1])

    player_dir = os.path.join(directory, "players", "data")
    os.makedirs(player_dir, exist_ok=True)
    player_nbt = NBT()
    player_nbt.kind = "nbt"
    player_nbt.compression = nbt.COMPRESSION_GZIP
    player_nbt.root = TagCompound(
        {
            "Pos": TagList(
                [TagDouble(v) for v in player], element_type=nbt.TAG_DOUBLE
            ),
            "Dimension": TagString(OVERWORLD),
        }
    )
    # Matches the singleplayer_uuid above.
    written.append(
        os.path.join(player_dir, "be0f5711-95ac-467f-bac2-a2e5b1c4157c.dat"))
    player_nbt.save(written[-1])
    made = []
    for path in written:
        with open(path, "rb") as handle:
            made.append((os.path.relpath(path, directory), handle.read()))
    return tuple(made)


class MakeSaveTests(unittest.TestCase):
    """:func:`make_save` remembers what it wrote, and a remembered save must
    be the save the library writes: every directory, every file's bytes and
    every file's mode, whatever the chunks and wherever the player."""

    @staticmethod
    def listing(directory):
        found = []
        for root, dirs, files in os.walk(directory):
            for name in dirs:
                path = os.path.join(root, name)
                found.append((os.path.relpath(path, directory), "dir", None))
            for name in files:
                path = os.path.join(root, name)
                with open(path, "rb") as handle:
                    data = handle.read()
                found.append((os.path.relpath(path, directory),
                              oct(os.stat(path).st_mode & 0o777), data))
        return sorted(found)

    def test_a_remembered_save_is_the_save_written(self):
        cases = ([], [(0, 0)], [(-1, -1), (0, 0), (40, -3)])
        players = ((8.5, 70.0, 8.5), (-3.5, 101.0, 12.25))
        for chunks in cases:
            for player in players:
                with self.subTest(chunks=chunks, player=player):
                    made = []
                    for _ in range(3):
                        directory = tempfile.mkdtemp(prefix="make-save-")
                        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
                        made.append(directory)
                    first, second, written = made
                    make_save(first, chunks, player=player)
                    make_save(second, chunks, player=player)
                    _write_save(written, chunks, player)
                    expected = self.listing(written)
                    self.assertEqual(self.listing(first), expected)
                    self.assertEqual(self.listing(second), expected)
                    self.assertEqual(
                        MinecraftSave.load(second).player_position(), player)


class SaveBase(unittest.TestCase):
    CHUNKS: list[tuple[int, int]] = [(x, z) for x in range(-2, 4) for z in range(-2, 4)]

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mcsave-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, self.CHUNKS)
        self.save = MinecraftSave.load(self.dir)


class LoadingTests(SaveBase):
    def test_indexes_regions_without_reading_them(self):
        self.assertIn(OVERWORLD, self.save.dimensions)
        self.assertGreater(self.save.region_count, 0)
        self.assertEqual(self.save.loaded_region_count, 0, "read a file too early")
        self.assertEqual(self.save.chunk_count, 0)

    def test_region_opens_on_first_access(self):
        self.save.get_block(0, 70, 0)
        self.assertEqual(self.save.loaded_region_count, 1)
        # A second look at the same region must not re-read it.
        self.save.get_block(1, 70, 1)
        self.assertEqual(self.save.loaded_region_count, 1)

    def test_load_all_reads_everything(self):
        for dim in self.save.dimensions.values():
            dim.load_all()
        self.assertEqual(self.save.chunk_count, len(self.CHUNKS))

    def test_eager_load_reads_everything_up_front(self):
        eager = MinecraftSave.load(self.dir, eager=True)
        self.assertEqual(eager.loaded_region_count, eager.region_count)
        self.assertEqual(eager.chunk_count, len(self.CHUNKS))

    def test_iterating_a_dimension_covers_unloaded_regions(self):
        """A sweep must not silently skip regions nobody has opened yet."""
        dim = self.save.dimension(OVERWORLD)
        self.assertEqual(len(dim.regions), 0)
        seen = sum(len(region.chunks) for region in dim)
        self.assertEqual(seen, len(self.CHUNKS))

    def test_absent_dimensions_are_not_invented(self):
        self.assertNotIn(NETHER, self.save.dimensions)
        self.assertNotIn(END, self.save.dimensions)
        with self.assertRaises(NBTError):
            self.save.dimension(NETHER)

    def test_rejects_a_directory_that_is_not_a_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(NBTError) as ctx:
                MinecraftSave.load(tmp)
            self.assertIn("level.dat", str(ctx.exception))

    def test_rejects_unknown_dimension_name(self):
        with self.assertRaises(NBTError):
            MinecraftSave.load(self.dir, dimensions=["minecraft:the_void"])

    def test_takes_any_path(self):
        """Nothing is tied to one particular world."""
        other = tempfile.mkdtemp(prefix="mcsave-other-")
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        make_save(other, [(0, 0)], player=(100.0, 65.0, -200.0))
        loaded = MinecraftSave.load(other, eager=True)
        self.assertEqual(loaded.chunk_count, 1)
        self.assertEqual(loaded.player_block_position(), (100, 65, -200))

    def test_path_is_expanded(self):
        self.assertTrue(os.path.isabs(self.save.path))


class PlayerTests(SaveBase):
    def test_position_is_global_and_exact(self):
        self.assertEqual(self.save.player_position(), (8.5, 70.0, 8.5))

    def test_block_position_floors_towards_negative_infinity(self):
        """truncation would put a player at x=-0.5 in block 0, not -1."""
        other = tempfile.mkdtemp(prefix="mcsave-neg-")
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        make_save(other, [(-1, -1)], player=(-0.5, 70.9, -16.1))
        save = MinecraftSave.load(other)
        self.assertEqual(save.player_block_position(), (-1, 70, -17))

    def test_dimension(self):
        self.assertEqual(self.save.player_dimension(), OVERWORLD)

    def test_dimension_defaults_to_overworld_without_a_tag(self):
        """A player .dat can lack Dimension; the default is the overworld.

        Regression: this path once raised NameError (unimported constant),
        found while porting the method to C#."""
        save = MinecraftSave.load(self.dir)
        player = save._load_player()
        del player["Dimension"]
        self.assertEqual(save.player_dimension(), OVERWORLD)

    def test_set_player_position_moves_and_stops_them(self):
        """A teleport should not arrive still falling."""
        save = MinecraftSave.load(self.dir)
        save.set_player_position(100.5, 72.0, -40.5)
        self.assertEqual(save.player_position(), (100.5, 72.0, -40.5))
        player = save._load_player()
        motion = player.get("Motion")
        if motion is not None:
            self.assertEqual([float(v) for v in motion], [0.0, 0.0, 0.0])
        for name in ("fall_distance", "FallDistance"):
            if name in player:
                self.assertEqual(float(player[name]), 0.0)

    def test_set_player_position_can_keep_momentum(self):
        save = MinecraftSave.load(self.dir)
        player = save._load_player()
        if player.get("Motion") is None:
            self.skipTest("fixture player has no Motion")
        player["Motion"][0] = nbt.TagDouble(1.5)
        save.set_player_position(1.0, 2.0, 3.0, keep_motion=True)
        self.assertEqual(float(save._load_player()["Motion"][0]), 1.5)

    def test_spawn_position(self):
        self.assertEqual(self.save.spawn_position(), (1, 2, 3))

    def test_missing_player_is_reported(self):
        shutil.rmtree(os.path.join(self.dir, "players"))
        save = MinecraftSave.load(self.dir)
        with self.assertRaises(NBTError) as ctx:
            save.player_position()
        self.assertIn("players/data", str(ctx.exception))


class BlockAccessTests(SaveBase):
    def test_get_and_set_round_trip(self):
        self.assertEqual(self.save.get_block(0, 70, 0).name, "minecraft:air")
        self.assertTrue(self.save.set_block(0, 70, 0, "minecraft:stone"))
        self.assertEqual(self.save.get_block(0, 70, 0).name, "minecraft:stone")

    def test_negative_coordinates(self):
        for x, z in ((-1, -1), (-16, -16), (-17, -17), (-32, -1)):
            with self.subTest(x=x, z=z):
                self.assertTrue(self.save.set_block(x, 70, z, "minecraft:stone"))
                self.assertEqual(self.save.get_block(x, 70, z).name, "minecraft:stone")

    def test_ungenerated_chunk_reads_none_and_writes_false(self):
        self.assertIsNone(self.save.get_block(100000, 70, 100000))
        self.assertFalse(self.save.set_block(100000, 70, 100000, "minecraft:stone"))

    def test_out_of_height_range_reads_none(self):
        self.assertIsNone(self.save.get_block(0, MAX_Y + 1, 0))
        self.assertIsNone(self.save.get_block(0, MIN_Y - 1, 0))


class FillTests(SaveBase):
    BLOCK = "minecraft:glass"

    def assert_box_filled(self, corner1, corner2, block=None):
        """Every block inside is set, and the shell outside is untouched."""
        block = block or self.BLOCK
        x0, x1 = sorted((corner1[0], corner2[0]))
        y0, y1 = sorted((corner1[1], corner2[1]))
        z0, z1 = sorted((corner1[2], corner2[2]))
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    found = self.save.get_block(x, y, z)
                    self.assertIsNotNone(found, f"({x},{y},{z}) missing")
                    self.assertEqual(found.name, block, f"({x},{y},{z}) not filled")
        for x, y, z in (
            (x0 - 1, y0, z0),
            (x1 + 1, y0, z0),
            (x0, y0 - 1, z0),
            (x0, y1 + 1, z0),
            (x0, y0, z0 - 1),
            (x0, y0, z1 + 1),
        ):
            found = self.save.get_block(x, y, z)
            if found is not None:
                self.assertNotEqual(found.name, block, f"leaked to ({x},{y},{z})")

    def test_single_block(self):
        self.save.fill((0, 70, 0), (0, 70, 0), self.BLOCK)
        self.assert_box_filled((0, 70, 0), (0, 70, 0))

    def test_within_one_section(self):
        result = self.save.fill((1, 70, 1), (4, 73, 4), self.BLOCK)
        self.assertEqual(result.blocks_filled, 4 * 4 * 4)
        self.assertTrue(result.complete)
        self.assert_box_filled((1, 70, 1), (4, 73, 4))

    def test_crosses_chunk_boundary(self):
        result = self.save.fill((14, 70, 14), (17, 70, 17), self.BLOCK)
        self.assertEqual(result.chunks_modified, 4)
        self.assert_box_filled((14, 70, 14), (17, 70, 17))

    def test_crosses_section_boundary(self):
        self.save.fill((0, 62, 0), (0, 66, 0), self.BLOCK)
        self.assert_box_filled((0, 62, 0), (0, 66, 0))

    def test_crosses_region_boundary(self):
        """Chunk -1 lives in region -1, chunk 0 in region 0."""
        result = self.save.fill((-17, 70, -17), (17, 70, 17), self.BLOCK)
        regions = {
            (cx >> 5, cz >> 5) for cx in range(-2, 2) for cz in range(-2, 2)
        }
        self.assertGreater(len(regions), 1)
        self.assertTrue(result.complete)
        self.assert_box_filled((-17, 70, -17), (17, 70, 17))

    def test_wholly_negative_box(self):
        self.save.fill((-30, 60, -30), (-20, 70, -20), self.BLOCK)
        self.assert_box_filled((-30, 60, -30), (-20, 70, -20))

    def test_corners_in_any_order(self):
        forward = self.save.fill((0, 70, 0), (3, 73, 3), self.BLOCK)
        self.setUp()  # fresh save
        backward = self.save.fill((3, 73, 3), (0, 70, 0), self.BLOCK)
        self.assertEqual(forward.blocks_filled, backward.blocks_filled)
        self.assert_box_filled((0, 70, 0), (3, 73, 3))

    def test_mixed_order_corners(self):
        self.save.fill((3, 70, 0), (0, 73, 3), self.BLOCK)
        self.assert_box_filled((0, 70, 0), (3, 73, 3))

    def test_whole_section_uses_the_constant_time_path(self):
        result = self.save.fill((0, 64, 0), (15, 79, 15), self.BLOCK)
        self.assertEqual(result.sections_replaced, 1)
        self.assertEqual(result.blocks_filled, BLOCKS_PER_SECTION)
        self.assert_box_filled((0, 64, 0), (15, 79, 15))

    def test_whole_section_collapses_the_palette(self):
        self.save.fill((0, 64, 0), (15, 79, 15), self.BLOCK)
        chunk = self.save.dimension(OVERWORLD).chunk(0, 0)
        container = Blocks(chunk).sections[4].blocks
        self.assertEqual(len(container.palette), 1)
        self.assertNotIn("data", container._tag, "uniform section kept an index array")

    def test_partial_section_keeps_its_neighbors(self):
        self.save.set_block(0, 70, 0, "minecraft:bedrock")
        self.save.fill((1, 70, 0), (15, 70, 15), self.BLOCK)
        self.assertEqual(self.save.get_block(0, 70, 0).name, "minecraft:bedrock")

    def test_below_world_floor_is_reported(self):
        result = self.save.fill((0, MIN_Y - 5, 0), (0, MIN_Y + 4, 0), self.BLOCK)
        self.assertEqual(result.blocks_filled, 5)
        self.assertEqual(result.blocks_out_of_range, 5)
        self.assertFalse(result.complete)

    def test_above_world_ceiling_is_reported(self):
        result = self.save.fill((0, MAX_Y - 4, 0), (0, MAX_Y + 5, 0), self.BLOCK)
        self.assertEqual(result.blocks_filled, 5)
        self.assertEqual(result.blocks_out_of_range, 5)
        self.assertFalse(result.complete)

    def test_ungenerated_chunks_are_skipped_not_created(self):
        result = self.save.fill((100000, 70, 100000), (100015, 70, 100015), self.BLOCK)
        self.assertEqual(result.blocks_filled, 0)
        self.assertTrue(result.missing_chunks)
        self.assertFalse(result.complete)
        # No region file should have appeared on disk for that empty area.
        region_dir = os.path.join(self.dir, "dimensions/minecraft/overworld/region")
        before = set(os.listdir(region_dir))
        self.save.save()
        self.assertEqual(set(os.listdir(region_dir)), before, "invented a region file")

    def test_old_format_chunk_is_reported_as_unreadable_not_missing(self):
        """A long-lived world holds chunks the game has not re-saved for
        years. One of those has a build in it; calling it 'ungenerated' would
        suggest the area is empty."""
        region_dir = os.path.join(self.dir, "dimensions/minecraft/overworld/region")
        region = NBT.load(os.path.join(region_dir, "r.0.0.mca"))
        target = (2, 2)
        self.assertIn(target, region.chunks)
        # A pre-1.18 chunk: sections lived under "Level", not at the root.
        region.chunks[target].data = TagCompound(
            {"DataVersion": TagInt(1493), "Level": TagCompound({})}
        )
        region.save()

        save = MinecraftSave.load(self.dir)
        base_x, base_z = target[0] * 16, target[1] * 16
        result = save.fill(
            (base_x, 70, base_z), (base_x + 3, 70, base_z + 3), self.BLOCK
        )
        self.assertEqual(result.missing_chunks, [], "called an existing chunk missing")
        self.assertEqual([where for where, _ in result.unreadable_chunks], [target])
        self.assertFalse(result.complete)
        self.assertEqual(result.blocks_filled, 0)

    def test_genuinely_absent_chunk_is_still_reported_as_missing(self):
        result = self.save.fill((100000, 70, 100000), (100003, 70, 100003), self.BLOCK)
        self.assertTrue(result.missing_chunks)
        self.assertEqual(result.unreadable_chunks, [])

    def test_partly_ungenerated_box(self):
        """Half inside the world, half beyond its edge."""
        edge = max(cx for cx, _ in self.CHUNKS) * 16 + 15
        result = self.save.fill((edge - 3, 70, 0), (edge + 4, 70, 0), self.BLOCK)
        self.assertEqual(result.blocks_filled, 4)
        self.assertTrue(result.missing_chunks)
        for x in range(edge - 3, edge + 1):
            self.assertEqual(self.save.get_block(x, 70, 0).name, self.BLOCK)

    def test_block_state_with_properties(self):
        state = BlockState.parse("minecraft:oak_stairs[facing=north,half=bottom]")
        self.save.fill((0, 70, 0), (2, 70, 2), state)
        self.assertEqual(self.save.get_block(1, 70, 1), state)

    def test_accepts_a_string_or_a_blockstate(self):
        self.save.fill((0, 70, 0), (0, 70, 0), "minecraft:stone")
        self.save.fill((1, 70, 0), (1, 70, 0), BlockState("minecraft:stone"))
        self.assertEqual(self.save.get_block(0, 70, 0).name, "minecraft:stone")
        self.assertEqual(self.save.get_block(1, 70, 0).name, "minecraft:stone")

    def test_fill_survives_save_and_reload(self):
        self.save.fill((-20, 60, -20), (20, 80, 20), self.BLOCK)
        self.save.save()
        reloaded = MinecraftSave.load(self.dir)
        for point in ((-20, 60, -20), (0, 70, 0), (20, 80, 20), (-1, 70, -1)):
            self.assertEqual(reloaded.get_block(*point).name, self.BLOCK, point)

    def test_repeated_fills_do_not_grow_the_palette(self):
        """Overwriting should not leave the palette accumulating junk."""
        for name in ("minecraft:stone", "minecraft:dirt", "minecraft:glass"):
            self.save.fill((0, 70, 0), (7, 70, 7), name)
        chunk = self.save.dimension(OVERWORLD).chunk(0, 0)
        container = Blocks(chunk).sections[4].blocks
        self.assertLessEqual(len(container.palette), 3)

    def test_release_keeps_memory_flat_but_preserves_edits(self):
        result = self.save.fill((-30, 60, -30), (30, 90, 30), self.BLOCK)
        self.assertTrue(result.complete)
        # keep_loaded defaults off, so chunks were released mid-fill.
        self.assertEqual(self.save.get_block(0, 70, 0).name, self.BLOCK)
        self.save.save()
        reloaded = MinecraftSave.load(self.dir)
        self.assertEqual(reloaded.get_block(0, 70, 0).name, self.BLOCK)


class BiomeAccessTests(SaveBase):
    """Point reads and writes. make_save gives every chunk uniform plains."""

    def test_reads_the_generated_biome(self):
        self.assertEqual(self.save.get_biome(0, 70, 0), "minecraft:plains")

    def test_missing_chunk_reads_none(self):
        self.assertIsNone(self.save.get_biome(10_000, 70, 10_000))

    def test_above_the_world_reads_none(self):
        self.assertIsNone(self.save.get_biome(0, MAX_Y + 1, 0))
        self.assertIsNone(self.save.get_biome(0, MIN_Y - 1, 0))

    def test_set_and_read_back(self):
        self.assertTrue(self.save.set_biome(0, 70, 0, "minecraft:snowy_taiga"))
        self.assertEqual(self.save.get_biome(0, 70, 0), "minecraft:snowy_taiga")

    def test_set_refuses_rather_than_creating_a_chunk(self):
        self.assertFalse(self.save.set_biome(10_000, 70, 0, "minecraft:snowy_taiga"))
        self.assertFalse(self.save.set_biome(0, MAX_Y + 1, 0, "minecraft:snowy_taiga"))

    def test_a_write_covers_the_whole_4x4x4_cell(self):
        """The documented coarseness: neighbors in the cell move together."""
        self.save.set_biome(0, 68, 0, "minecraft:snowy_taiga")
        for x in range(4):
            for y in range(68, 72):
                for z in range(4):
                    self.assertEqual(
                        self.save.get_biome(x, y, z),
                        "minecraft:snowy_taiga",
                        f"({x},{y},{z}) not in the written cell",
                    )
        # ...and stops at the cell boundary.
        for point in ((4, 68, 0), (0, 72, 0), (0, 68, 4), (-1, 68, 0)):
            self.assertEqual(self.save.get_biome(*point), "minecraft:plains", point)

    def test_negative_coordinates_floor_into_the_right_cell(self):
        """x=-1 belongs to cell -1, not cell 0. int(x/4) would truncate."""
        self.save.set_biome(-1, 70, -1, "minecraft:snowy_taiga")
        self.assertEqual(self.save.get_biome(-4, 70, -4), "minecraft:snowy_taiga")
        self.assertEqual(self.save.get_biome(0, 70, 0), "minecraft:plains")

    def test_survives_save_and_reload(self):
        self.save.set_biome(0, 70, 0, "minecraft:snowy_taiga")
        self.save.save()
        reloaded = MinecraftSave.load(self.dir)
        self.assertEqual(reloaded.get_biome(0, 70, 0), "minecraft:snowy_taiga")

    def test_setting_biome_leaves_blocks_alone(self):
        before = self.save.get_block(0, 70, 0)
        self.save.set_biome(0, 70, 0, "minecraft:snowy_taiga")
        self.assertEqual(self.save.get_block(0, 70, 0), before)


class BiomeFillTests(SaveBase):
    BIOME = "minecraft:snowy_taiga"

    def assert_box_is_biome(self, corner1, corner2, biome=None):
        """Every cell inside is set, and the shell outside is untouched."""
        biome = biome or self.BIOME
        x0, x1 = sorted((corner1[0], corner2[0]))
        y0, y1 = sorted((corner1[1], corner2[1]))
        z0, z1 = sorted((corner1[2], corner2[2]))
        for x in range(x0, x1 + 1, 4):
            for y in range(y0, y1 + 1, 4):
                for z in range(z0, z1 + 1, 4):
                    self.assertEqual(
                        self.save.get_biome(x, y, z), biome, f"({x},{y},{z}) not filled"
                    )
        for x, y, z in (
            (x0 - 4, y0, z0),
            (x1 + 4, y0, z0),
            (x0, y0 - 4, z0),
            (x0, y1 + 4, z0),
            (x0, y0, z0 - 4),
            (x0, y0, z1 + 4),
        ):
            found = self.save.get_biome(x, y, z)
            if found is not None:
                self.assertNotEqual(found, biome, f"leaked to ({x},{y},{z})")

    def test_single_cell(self):
        result = self.save.fill_biome((0, 68, 0), (3, 71, 3), self.BIOME)
        self.assertEqual(result.cells_filled, 1)
        self.assertTrue(result.complete)
        self.assert_box_is_biome((0, 68, 0), (3, 71, 3))

    def test_within_one_section(self):
        result = self.save.fill_biome((0, 64, 0), (7, 71, 7), self.BIOME)
        self.assertEqual(result.cells_filled, 2 * 2 * 2)
        self.assertTrue(result.complete)
        self.assert_box_is_biome((0, 64, 0), (7, 71, 7))

    def test_box_grows_outward_to_cell_boundaries(self):
        """A box starting mid-cell writes the whole cell, and says so."""
        result = self.save.fill_biome((1, 69, 1), (6, 70, 6), self.BIOME)
        self.assertEqual(result.written_box, ((0, 68, 0), (7, 71, 7)))
        self.assertEqual(result.cells_filled, 2 * 1 * 2)
        # The grown box really was written, not just reported.
        self.assertEqual(self.save.get_biome(0, 68, 0), self.BIOME)
        self.assertEqual(self.save.get_biome(7, 71, 7), self.BIOME)

    def test_corners_in_any_order(self):
        result = self.save.fill_biome((7, 71, 7), (0, 64, 0), self.BIOME)
        self.assertEqual(result.written_box, ((0, 64, 0), (7, 71, 7)))
        self.assert_box_is_biome((0, 64, 0), (7, 71, 7))

    def test_crosses_chunk_boundary(self):
        result = self.save.fill_biome((14, 70, 14), (17, 70, 17), self.BIOME)
        self.assertEqual(result.chunks_modified, 4)
        self.assertTrue(result.complete)
        self.assert_box_is_biome((12, 68, 12), (19, 71, 19))

    def test_crosses_section_boundary(self):
        result = self.save.fill_biome((0, 60, 0), (3, 67, 3), self.BIOME)
        self.assertTrue(result.complete)
        self.assert_box_is_biome((0, 60, 0), (3, 67, 3))

    def test_crosses_region_boundary(self):
        result = self.save.fill_biome((-17, 70, -17), (17, 70, 17), self.BIOME)
        self.assertTrue(result.complete)
        self.assertEqual(self.save.get_biome(-17, 70, -17), self.BIOME)
        self.assertEqual(self.save.get_biome(17, 70, 17), self.BIOME)

    def test_negative_coordinates(self):
        result = self.save.fill_biome((-8, 68, -8), (-5, 71, -5), self.BIOME)
        self.assertEqual(result.written_box, ((-8, 68, -8), (-5, 71, -5)))
        self.assert_box_is_biome((-8, 68, -8), (-5, 71, -5))

    def test_whole_section_collapses_to_a_uniform_palette(self):
        """The constant-work path: no index array at all afterwards."""
        result = self.save.fill_biome((0, 64, 0), (15, 79, 15), self.BIOME)
        self.assertEqual(result.sections_replaced, 1)
        self.assertEqual(result.cells_filled, 64)
        chunk = self.save.dimension(OVERWORLD).chunk(0, 0)
        container = Blocks(chunk).sections[4].biomes
        self.assertEqual(container.palette, [self.BIOME])
        self.assertIsNone(container._tag.get("data"))

    def test_missing_chunks_are_reported_not_created(self):
        result = self.save.fill_biome((10_000, 70, 10_000), (10_003, 70, 10_003), self.BIOME)
        self.assertFalse(result.complete)
        self.assertTrue(result.missing_chunks)
        self.assertEqual(result.cells_filled, 0)

    def test_above_the_world_counts_as_out_of_range(self):
        result = self.save.fill_biome((0, MAX_Y - 3, 0), (3, MAX_Y + 100, 3), self.BIOME)
        self.assertFalse(result.complete)
        self.assertGreater(result.cells_out_of_range, 0)
        # The part that was in range still landed.
        self.assertEqual(self.save.get_biome(0, MAX_Y - 3, 0), self.BIOME)

    def test_survives_save_and_reload(self):
        self.save.fill_biome((-20, 60, -20), (20, 80, 20), self.BIOME)
        self.save.save()
        reloaded = MinecraftSave.load(self.dir)
        for point in ((-20, 60, -20), (0, 70, 0), (20, 80, 20), (-1, 70, -1)):
            self.assertEqual(reloaded.get_biome(*point), self.BIOME, point)

    def test_repeated_fills_do_not_grow_the_palette(self):
        for name in ("minecraft:desert", "minecraft:taiga", "minecraft:snowy_taiga"):
            self.save.fill_biome((0, 68, 0), (7, 71, 7), name)
        chunk = self.save.dimension(OVERWORLD).chunk(0, 0)
        container = Blocks(chunk).sections[4].biomes
        self.assertLessEqual(len(container.palette), 3)

    def test_fill_leaves_blocks_alone(self):
        before = self.save.get_block(0, 70, 0)
        self.save.fill_biome((0, 64, 0), (15, 79, 15), self.BIOME)
        self.assertEqual(self.save.get_block(0, 70, 0), before)

    def test_release_keeps_memory_flat_but_preserves_edits(self):
        result = self.save.fill_biome((-30, 60, -30), (30, 90, 30), self.BIOME)
        self.assertTrue(result.complete)
        self.assertEqual(self.save.get_biome(0, 70, 0), self.BIOME)
        self.save.save()
        reloaded = MinecraftSave.load(self.dir)
        self.assertEqual(reloaded.get_biome(0, 70, 0), self.BIOME)


class LightingResetTests(SaveBase):
    """reset_lighting at save level: the box, its ring, and the refusals.

    The synthetic chunks carry no light data, which is exactly the blank
    slate wanted here: every flag and array a test observes afterwards was
    put there (or deliberately not) by the call under test.
    """

    def chunk_data(self, chunk_x, chunk_z):
        return self.save.dimension(OVERWORLD).chunk(chunk_x, chunk_z).data

    def seed_light(self, chunk_x, chunk_z):
        """Give a chunk the game-like state: flag standing, one stale array."""
        data = self.chunk_data(chunk_x, chunk_z)
        data["isLightOn"] = nbt.TagByte(1)
        data["sections"][4]["SkyLight"] = nbt.TagByteArray([15] * 2048)

    def test_resets_the_box_and_its_neighbor_ring(self):
        self.seed_light(0, 0)
        self.seed_light(1, 0)  # a neighbor: light crosses chunk borders
        result = self.save.reset_lighting((0, 64, 0), (15, 64, 15))
        self.assertEqual(result.chunks_reset, 9)
        self.assertEqual(result.arrays_removed, 2)
        self.assertEqual(int(self.chunk_data(0, 0)["isLightOn"]), 0)
        self.assertEqual(int(self.chunk_data(1, 0)["isLightOn"]), 0)
        self.assertNotIn("SkyLight", self.chunk_data(1, 0)["sections"][4])
        # Two chunks out is beyond the ring and stays untouched.
        self.assertNotIn("isLightOn", self.chunk_data(2, 0))

    def test_the_ring_can_be_left_alone(self):
        result = self.save.reset_lighting(
            (0, 64, 0), (15, 64, 15), neighbors=False
        )
        self.assertEqual(result.chunks_reset, 1)
        self.assertNotIn("isLightOn", self.chunk_data(1, 0))

    def test_corner_order_and_y_are_ignored(self):
        """Light is stored per chunk, so the vertical extent of the edit
        cannot matter, and corners arrive in whatever order a fill got."""
        tall = self.save.reset_lighting((15, 319, 15), (0, -64, 0))
        self.assertEqual(tall.chunks_reset, 9)

    def test_ungenerated_chunks_are_skipped_not_failed(self):
        """The ring around a corner chunk reaches off the map; a chunk that
        does not exist yet is lit at generation, so that is not a problem."""
        result = self.save.reset_lighting((48, 64, 48), (63, 64, 63))
        self.assertEqual(result.chunks_reset, 4)  # (2..3, 2..3) exist
        self.assertEqual(result.chunks_skipped, 5)  # the (4, *) and (*, 4) arc
        self.assertEqual(result.unreadable_chunks, [])

    def test_unreadable_chunks_are_reported_not_guessed_at(self):
        self.chunk_data(0, 0)["DataVersion"] = TagInt(100)  # pre-1.18
        result = self.save.reset_lighting(
            (0, 64, 0), (15, 64, 15), neighbors=False
        )
        self.assertEqual(result.chunks_reset, 0)
        self.assertEqual(len(result.unreadable_chunks), 1)
        self.assertEqual(result.unreadable_chunks[0][0], (0, 0))

    def test_the_reset_survives_save_and_reload(self):
        """What matters is what Minecraft will read off the disk."""
        self.save.reset_lighting((0, 64, 0), (15, 64, 15), neighbors=False)
        self.assertGreaterEqual(self.save.save(), 1)
        fresh = MinecraftSave.load(self.dir)
        flag = fresh.dimension(OVERWORLD).chunk(0, 0).data["isLightOn"]
        self.assertEqual(int(flag), 0)
        self.assertIsInstance(flag, nbt.TagByte)

    def test_repr_reads_like_a_report(self):
        self.seed_light(0, 0)
        result = self.save.reset_lighting((0, 64, 0), (48, 64, 48))
        text = repr(result)
        self.assertIn("chunks marked", text)
        self.assertIn("stale arrays", text)


class HistogramTests(SaveBase):
    """The box census, exercised where the counts can be known exactly."""

    def test_a_whole_uniform_chunk_counts_in_constant_style(self):
        result = self.save.histogram((0, MIN_Y, 0), (15, MAX_Y, 15))
        self.assertTrue(result.complete)
        self.assertEqual(result.blocks_counted, 24 * 4096)
        self.assertEqual(
            result.counts[BlockState("minecraft:air")], 24 * 4096
        )
        self.assertEqual(result.chunks_scanned, 1)

    def test_edits_show_up_with_exact_counts(self):
        self.save.fill((1, 64, 1), (3, 66, 3), "minecraft:stone_bricks")
        self.save.set_block(10, 70, 10, "minecraft:gold_block")
        result = self.save.histogram((0, 60, 0), (15, 75, 15))
        self.assertEqual(result.counts[BlockState("minecraft:stone_bricks")], 27)
        self.assertEqual(result.counts[BlockState("minecraft:gold_block")], 1)
        self.assertEqual(result.blocks_counted, 16 * 16 * 16)
        self.assertEqual(sum(result.counts.values()), result.blocks_counted)
        self.assertEqual(result.top(1)[0][0], BlockState("minecraft:air"))

    def test_a_small_box_counts_only_itself(self):
        self.save.fill((0, 64, 0), (1, 64, 1), "minecraft:stone")
        result = self.save.histogram((0, 64, 0), (1, 64, 1))
        self.assertEqual(result.blocks_counted, 4)
        self.assertEqual(result.counts, {BlockState("minecraft:stone"): 4})

    def test_corners_in_any_order(self):
        forward = self.save.histogram((0, 64, 0), (5, 70, 5))
        backward = self.save.histogram((5, 70, 5), (0, 64, 0))
        self.assertEqual(forward.counts, backward.counts)

    def test_missing_and_out_of_range_are_reported_not_zeroed(self):
        result = self.save.histogram((0, 64, 0), (200, 400, 15))
        self.assertFalse(result.complete)
        self.assertIn((5, 0), result.missing_chunks)
        self.assertGreater(result.blocks_out_of_range, 0)

    def test_unreadable_chunks_are_reported(self):
        dim = self.save.dimension(OVERWORLD)
        dim.chunk(0, 0).data["DataVersion"] = TagInt(100)
        result = self.save.histogram((0, 64, 0), (15, 64, 15))
        self.assertEqual(len(result.unreadable_chunks), 1)
        self.assertEqual(result.blocks_counted, 0)


class FindBlocksTests(SaveBase, SequenceAssertions):
    """The box search: what matched, in what order, and what was skipped."""

    def populate(self):
        """A handful of states chosen so every match mode can be told apart."""
        self.save.set_block(1, 70, 1, "minecraft:oak_door[facing=north,half=lower]")
        self.save.set_block(1, 71, 1, "minecraft:oak_door[facing=north,half=upper]")
        self.save.set_block(2, 70, 1, "minecraft:spruce_door[facing=east,half=lower]")
        self.save.set_block(3, 70, 1, "minecraft:oak_stairs[facing=north,half=bottom]")
        self.save.set_block(
            4, 70, 1, "minecraft:stone_stairs[facing=north,half=bottom]")
        self.save.set_block(5, 70, 1, "minecraft:glass")
        self.save.set_block(6, 70, 1, "minecraft:glowstone")

    def find(self, names, **kwargs):
        return self.save.find_blocks((0, 64, 0), (15, 80, 15), names, **kwargs)

    def test_name_matches_every_state_of_that_block(self):
        self.populate()
        result = self.find("oak_door")
        self.assertIsInstance(result, FindResult)
        self.assertTrue(result.complete)
        self.assertEqual(result.positions(), [(1, 70, 1), (1, 71, 1)],
                         "both halves of the door, however it is hinged")
        self.assertEqual(len(result.counts), 2, "two states, one name")
        self.assertEqual(len(result), 2)

    def test_state_matches_the_whole_state(self):
        self.populate()
        result = self.find("oak_door[facing=north,half=lower]", match="state")
        self.assertEqual(result.positions(), [(1, 70, 1)])

    def test_state_ignores_the_order_the_properties_were_typed_in(self):
        """Property order is not significant to the game, so it is not
        significant here either."""
        self.populate()
        typed_one_way = self.find(
            "oak_stairs[facing=north,half=bottom]", match="state")
        typed_the_other = self.find(
            "oak_stairs[half=bottom,facing=north]", match="state")
        self.assertEqual(typed_one_way.positions(), [(3, 70, 1)])
        self.assertEqual(typed_the_other.positions(), typed_one_way.positions())

    def test_suffix_asks_for_a_whole_family(self):
        self.populate()
        self.assertEqual(len(self.find("_door", match="suffix")), 3)
        self.assertEqual(
            self.find("_stairs", match="suffix").positions(),
            [(3, 70, 1), (4, 70, 1)],
        )
        several = self.find(("_door", "_stairs"), match="suffix")
        self.assertEqual(len(several), 5)

    def test_substring_catches_the_families_with_no_common_ending(self):
        self.populate()
        result = self.find("gl", match="substring")
        self.assertEqual(result.positions(), [(5, 70, 1), (6, 70, 1)])
        self.assertEqual(len(self.find("glow", match="substring")), 1)

    def test_name_with_properties_is_refused_and_says_what_to_use(self):
        with self.assertRaises(NBTError) as ctx:
            self.find("oak_door[half=lower]")
        self.assertIn("match='state'", str(ctx.exception))

    def test_an_unknown_match_mode_is_refused_by_name(self):
        with self.assertRaises(NBTError) as ctx:
            self.find("stone", match="regex")
        self.assertIn("regex", str(ctx.exception))
        self.assertIn("substring", str(ctx.exception), "the modes are listed")

    def test_searching_for_nothing_is_refused(self):
        with self.assertRaises(NBTError):
            self.find([])

    def test_unqualified_names_read_as_the_vanilla_namespace(self):
        """The way the game's own commands read them -- and a modded name
        keeps its own namespace rather than being silently vanilla-ised."""
        self.save.set_block(7, 70, 1, "mymod:thing")
        self.assertEqual(len(self.find("thing")), 0)
        self.assertEqual(self.find("mymod:thing").positions(), [(7, 70, 1)])
        self.save.set_block(8, 70, 1, "minecraft:stone")
        self.assertEqual(self.find("stone").positions(),
                         self.find("minecraft:stone").positions())

    def test_limit_stops_early_and_the_answer_says_it_is_partial(self):
        self.save.fill((0, 70, 0), (9, 70, 9), "minecraft:stone_bricks")
        full = self.find("stone_bricks")
        self.assertEqual(len(full), 100)
        self.assertTrue(full.complete)

        capped = self.find("stone_bricks", limit=5)
        self.assertEqual(len(capped), 5)
        self.assertTrue(capped.truncated)
        self.assertFalse(capped.complete,
                         "a capped answer must never read as exhaustive")
        self.assertSequencesEqual(capped.positions(), full.positions()[:5])
        self.assertIn("truncated", repr(capped))

    def test_the_palette_short_circuit_really_skips_sections(self):
        """A section whose palette holds nothing wanted is dismissed without
        unpacking its indices at all -- that is where a sweep gets its speed."""
        self.save.set_block(4, 70, 4, "minecraft:gold_block")
        result = self.save.find_blocks((0, MIN_Y, 0), (15, MAX_Y, 15), "gold_block")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.sections_scanned, 1)
        self.assertEqual(result.sections_skipped, 23,
                         "24 sections in the column, one of them worth reading")
        self.assertIn("skipped by palette", repr(result))

    def test_air_the_world_never_wrote_is_not_reported(self):
        """Sections the world stores nothing for are air, and are dismissed
        the same way -- a search for air must not invent 4096 blocks of it."""
        result = self.save.find_blocks((0, 320, 0), (15, 335, 15), "air")
        self.assertEqual(len(result), 0)
        self.assertEqual(result.sections_skipped, 1)
        self.assertEqual(result.sections_scanned, 0)
        self.assertTrue(result.complete, "nothing was withheld; there is nothing there")

    def test_ungenerated_chunks_are_missing_not_unreadable(self):
        """The two are different questions: 'nothing there' and 'there and
        broken' must never be indistinguishable."""
        result = self.save.find_blocks((0, 70, 0), (200, 70, 15), "stone")
        self.assertFalse(result.complete)
        self.assertIn((5, 0), result.missing_chunks)
        self.assertEqual(result.unreadable_chunks, [])
        self.assertIn("ungenerated", repr(result))

    def test_unreadable_chunks_are_reported_as_such(self):
        self.save.dimension(OVERWORLD).chunk(0, 0).data["DataVersion"] = TagInt(100)
        result = self.find("stone")
        self.assertFalse(result.complete)
        self.assertEqual(len(result.unreadable_chunks), 1)
        self.assertEqual(result.unreadable_chunks[0][0], (0, 0))
        self.assertEqual(result.missing_chunks, [])

    def test_the_order_is_fixed_and_repeatable(self):
        """Chunk by chunk, then section, then y, z, x -- so the same world
        always yields the same list and two ports compare line for line."""
        self.save.fill((0, 70, 0), (17, 71, 17), "minecraft:stone_bricks")
        expected = []
        for chunk_x in (0, 1):
            for chunk_z in (0, 1):
                for y in (70, 71):
                    for z in range(chunk_z * 16, min(17, chunk_z * 16 + 15) + 1):
                        for x in range(chunk_x * 16, min(17, chunk_x * 16 + 15) + 1):
                            expected.append((x, y, z))
        first = self.save.find_blocks((0, 70, 0), (17, 71, 17), "stone_bricks")
        self.assertSequencesEqual(first.positions(), expected)
        second = self.save.find_blocks((0, 70, 0), (17, 71, 17), "stone_bricks")
        self.assertSequencesEqual(second.positions(), first.positions())
        self.assertEqual(second.counts, first.counts)

    def test_corners_may_be_given_in_any_order(self):
        self.save.fill((2, 70, 2), (4, 70, 4), "minecraft:stone_bricks")
        forward = self.save.find_blocks((2, 70, 2), (4, 70, 4), "stone_bricks")
        backward = self.save.find_blocks((4, 70, 4), (2, 70, 2), "stone_bricks")
        self.assertSequencesEqual(backward.positions(), forward.positions())

    def test_counts_and_top_summarize_what_was_found(self):
        self.populate()
        result = self.find("_door", match="suffix")
        self.assertEqual(sum(result.counts.values()), len(result))
        self.assertEqual(
            result.counts[
                BlockState.parse("minecraft:spruce_door[facing=east,half=lower]")
            ],
            1,
        )
        self.assertEqual(len(result.top(2)), 2)
        counts = [count for _, count in result.top(10)]
        self.assertEqual(counts, sorted(counts, reverse=True),
                         "top() is most common first")

    def test_top_breaks_ties_by_name_so_the_twins_can_agree(self):
        """Equally common states come back in name order, not in whatever
        order the scan happened to meet them.

        Python's sort is stable and .NET's is not, so a top() that let ties
        fall out of encounter order would answer differently in the C# twin
        and could not be compared across the port at all."""
        self.save.fill((2, 70, 2), (4, 70, 2), "minecraft:oak_planks")
        self.save.fill((2, 70, 4), (4, 70, 4), "minecraft:birch_planks")
        result = self.find("_planks", match="suffix")
        tied = [str(state) for state, count in result.top(10) if count == 3]
        self.assertEqual(tied, ["minecraft:birch_planks",
                                "minecraft:oak_planks"])
        self.assertEqual([str(s) for s, _ in result.top(10)],
                         [str(s) for s, _ in result.top(10)])

    def test_a_nameless_search_term_is_refused_not_silently_empty(self):
        """A blank term is the one that must not come back "0 found".

        A name list that picked up an empty entry from config or user
        input has failed, and the whole point of the repo's refusal rule
        is that "could not read" never looks like "nothing there". This is
        also where the C# twin diverged: BlockState.Parse accepted a
        nameless term where Python's grammar requires a name."""
        for blank in ("", "   ", "[facing=north]"):
            for mode in ("name", "state"):
                with self.subTest(term=blank, match=mode):
                    with self.assertRaises(NBTError) as caught:
                        self.find(blank, match=mode)
                    self.assertIn("search term", str(caught.exception))

    def test_a_limit_of_zero_finds_nothing_and_says_so(self):
        self.populate()
        capped = self.find("_door", match="suffix", limit=0)
        self.assertEqual(len(capped), 0)
        self.assertTrue(capped.truncated)
        self.assertFalse(capped.complete)
        empty = self.find("diamond_block", limit=0)
        self.assertEqual(len(empty), 0)
        self.assertFalse(empty.truncated,
                         "nothing to find is not the same as stopping early")
        self.assertTrue(empty.complete)

    def test_an_empty_result_is_complete_and_empty(self):
        result = self.find("diamond_block")
        self.assertEqual(len(result), 0)
        self.assertEqual(result.counts, {})
        self.assertTrue(result.complete)
        self.assertEqual(result.chunks_scanned, 1)


class EntitiesInTests(SaveBase):
    """The read twin of spawn_entity, over storage that lives apart."""

    def entities_dir(self):
        path = os.path.join(self.dir, "dimensions/minecraft/overworld/entities")
        os.makedirs(path, exist_ok=True)
        return path

    def test_ids_filter_and_read_unqualified_names_as_vanilla(self):
        self.save.spawn_entity("minecraft:cow", 1.5, 70.0, 1.5)
        self.save.spawn_entity("minecraft:pig", 2.5, 70.0, 2.5)
        self.save.spawn_entity("minecraft:item", 3.5, 70.0, 3.5)
        box = ((0, 0, 0), (15, 100, 15))

        everything = self.save.entities_in(*box)
        self.assertIsInstance(everything, EntityQueryResult)
        self.assertEqual(len(everything), 3, "None reports mobs, items and all")
        self.assertEqual(len(self.save.entities_in(*box, ids="cow")), 1)
        self.assertEqual(
            len(self.save.entities_in(*box, ids=["minecraft:cow", "pig"])), 2
        )
        none_of_those = self.save.entities_in(*box, ids="sheep")
        self.assertEqual(len(none_of_those), 0)
        self.assertTrue(none_of_those.complete, "absence is a complete answer")

    def test_an_empty_id_filter_is_refused_rather_than_read_as_everything(self):
        with self.assertRaises(NBTError) as ctx:
            self.save.entities_in((0, 0, 0), (15, 100, 15), ids=[])
        self.assertIn("ids=None", str(ctx.exception))

    def test_negative_fractional_coordinates_floor_into_the_right_block(self):
        """An entity at x = -0.3 stands in block -1, not block 0 -- the
        truncation bug that lands things a chunk away."""
        self.save.spawn_entity("minecraft:cow", -0.3, 70.0, -0.3)
        here = self.save.entities_in((-1, 60, -1), (-1, 80, -1))
        self.assertEqual(len(here), 1)
        self.assertEqual(here.positions(), [(-0.3, 70.0, -0.3)],
                         "the exact doubles the game stores, not the floors")
        self.assertEqual(len(self.save.entities_in((0, 60, 0), (15, 80, 15))), 0)

    def test_the_box_bounds_the_vertical_axis_too(self):
        """x and z are covered by the flooring test; this is the y bound,
        which an entity query could plausibly forget to apply at all."""
        self.save.spawn_entity("minecraft:cow", 4.5, 70.0, 4.5)
        self.assertEqual(len(self.save.entities_in((0, 0, 0), (15, 60, 15))), 0)
        self.assertEqual(len(self.save.entities_in((0, 71, 0), (15, 100, 15))), 0)
        self.assertEqual(len(self.save.entities_in((0, 70, 0), (15, 70, 15))), 1)

    def test_chunks_with_no_storage_do_not_make_the_answer_incomplete(self):
        """Absent storage means no entities, which is a complete answer."""
        self.save.spawn_entity("minecraft:cow", 4.5, 70.0, 4.5)
        result = self.save.entities_in((0, 0, 0), (31, 100, 31))
        self.assertEqual(result.chunks_scanned, 1)
        self.assertEqual(result.chunks_without_storage, 3)
        self.assertTrue(result.complete)
        self.assertIn("with no storage", repr(result))

    def test_an_id_that_is_not_a_string_keeps_its_identity(self):
        """Modded or damaged storage can hold a non-string id. Collapsing
        it to "" would merge two distinct entities into one bucket."""
        self.save.spawn_entity("minecraft:cow", 1.5, 70.0, 1.5)
        chunk = self.save.dimension().entity_chunk(0, 0)
        stored = chunk.data["Entities"]
        odd = TagCompound(dict(stored[0]))
        odd["id"] = TagInt(42)
        odd["Pos"] = TagList(
            [TagDouble(2.5), TagDouble(70.0), TagDouble(2.5)],
            element_type=TAG_DOUBLE,
        )
        stored.append(odd)

        found = self.save.entities_in((0, 0, 0), (15, 100, 15))
        self.assertEqual(len(found), 2)
        self.assertEqual(sorted(found.counts), ["42", "minecraft:cow"])

    def test_a_non_finite_position_is_named_not_filed_somewhere_real(self):
        """NaN floors to nothing and infinity floors to everything; either
        way the entity has no block to be in. Guessing one would quietly
        file it in a block that exists."""
        self.save.spawn_entity("minecraft:cow", 1.5, 70.0, 1.5)
        chunk = self.save.dimension().entity_chunk(0, 0)
        stored = chunk.data["Entities"]
        broken = TagCompound(dict(stored[0]))
        broken["Pos"] = TagList(
            [TagDouble(float("nan")), TagDouble(70.0), TagDouble(2.5)],
            element_type=TAG_DOUBLE,
        )
        stored.append(broken)

        found = self.save.entities_in((0, 0, 0), (15, 100, 15))
        self.assertEqual(len(found), 1, "the sound entity still comes back")
        self.assertEqual(len(found.unreadable_chunks), 1)
        self.assertIn("non-finite", found.unreadable_chunks[0][1])
        self.assertFalse(found.complete)

    def test_an_entities_tag_that_is_not_a_list_is_unreadable_not_empty(self):
        """Storage that exists and makes no sense must not read as a quiet
        chunk with nothing in it."""
        self.save.spawn_entity("minecraft:cow", 1.5, 70.0, 1.5)
        chunk = self.save.dimension().entity_chunk(0, 0)
        chunk.data["Entities"] = TagString("not a list")

        found = self.save.entities_in((0, 0, 0), (15, 100, 15))
        self.assertEqual(len(found), 0)
        self.assertEqual(found.chunks_scanned, 0)
        self.assertEqual(len(found.unreadable_chunks), 1)
        self.assertIn("not a list", found.unreadable_chunks[0][1])
        self.assertFalse(found.complete)

    def test_storage_that_exists_and_cannot_be_read_is_the_case_worth_knowing(self):
        region = NBT()
        region.kind = "region"
        region.region_x = region.region_z = 0
        broken = Chunk(0, 0)
        broken._blob = b"not valid zlib data"
        broken.compression = nbt.COMPRESSION_ZLIB
        region.chunks[(0, 0)] = broken
        region.save(os.path.join(self.entities_dir(), "r.0.0.mca"))

        save = MinecraftSave.load(self.dir)
        result = save.entities_in((0, 0, 0), (15, 100, 15))
        self.assertFalse(result.complete)
        self.assertEqual(len(result.unreadable_chunks), 1)
        self.assertEqual(result.unreadable_chunks[0][0], (0, 0))
        self.assertIn("entity storage", result.unreadable_chunks[0][1])
        self.assertEqual(result.chunks_without_storage, 0,
                         "storage that is broken is not storage that is absent")

    def test_limit_stops_early_and_says_so(self):
        for i in range(3):
            self.save.spawn_entity("minecraft:cow", 1.5 + i, 70.0, 1.5)
        capped = self.save.entities_in((0, 0, 0), (15, 100, 15), limit=2)
        self.assertEqual(len(capped), 2)
        self.assertTrue(capped.truncated)
        self.assertFalse(capped.complete)
        self.assertIn("truncated", repr(capped))

    def test_the_whole_compound_comes_back_so_villager_data_is_reachable(self):
        self.save.spawn_entity(
            "minecraft:villager", 4.5, 70.0, 4.5,
            extra={
                "VillagerData": TagCompound(
                    {
                        "type": TagString("minecraft:plains"),
                        "profession": TagString("minecraft:librarian"),
                        "level": TagInt(1),
                    }
                )
            },
        )
        result = self.save.entities_in((0, 0, 0), (15, 100, 15), keep_loaded=True)
        (identity, x, y, z, tag), = result.entities
        self.assertEqual(identity, "minecraft:villager")
        self.assertEqual((x, y, z), (4.5, 70.0, 4.5))
        self.assertEqual(str(tag["VillagerData"]["profession"]), "minecraft:librarian")
        self.assertEqual(len(tag["UUID"]), 4, "the structural tags are there too")

        chunk = self.save.dimension(OVERWORLD).entity_chunk(0, 0)
        self.assertIs(tag, chunk.data["Entities"][0],
                      "keep_loaded hands back the live tag, not a copy")

    def test_counts_and_top_summarize_the_kinds(self):
        self.save.spawn_entity("minecraft:cow", 1.5, 70.0, 1.5)
        self.save.spawn_entity("minecraft:cow", 2.5, 70.0, 2.5)
        self.save.spawn_entity("minecraft:pig", 3.5, 70.0, 3.5)
        result = self.save.entities_in((0, 0, 0), (15, 100, 15))
        self.assertEqual(result.counts, {"minecraft:cow": 2, "minecraft:pig": 1})
        self.assertEqual(result.top(1), [("minecraft:cow", 2)])
        self.assertEqual(sum(result.counts.values()), len(result))

    def test_a_world_with_no_entity_storage_at_all_reports_nothing(self):
        result = self.save.entities_in((0, 0, 0), (15, 100, 15))
        self.assertEqual(len(result), 0)
        self.assertEqual(result.chunks_scanned, 0)
        self.assertEqual(result.chunks_without_storage, 1)
        self.assertTrue(result.complete)


class SearchResultExportTests(unittest.TestCase):
    """Both result types are part of the deterministic tier's public face."""

    def test_the_names_are_re_exported_from_both_levels(self):
        import heaton_voxel
        import heaton_voxel.world as world

        self.assertIs(heaton_voxel.FindResult, FindResult)
        self.assertIs(heaton_voxel.EntityQueryResult, EntityQueryResult)
        self.assertIs(world.FindResult, FindResult)
        for name in ("FindResult", "EntityQueryResult"):
            self.assertIn(name, heaton_voxel.__all__)
            self.assertIn(name, world.__all__)

    def test_complete_is_false_when_anything_was_missed(self):
        result = FindResult()
        self.assertTrue(result.complete)
        result.truncated = True
        self.assertFalse(result.complete)

        missing = FindResult()
        missing.missing_chunks.append((0, 0))
        self.assertFalse(missing.complete)

        unreadable = FindResult()
        unreadable.unreadable_chunks.append(((0, 0), "too old"))
        self.assertFalse(unreadable.complete)

        entities = EntityQueryResult()
        self.assertTrue(entities.complete)
        entities.chunks_without_storage = 5
        self.assertTrue(entities.complete, "absent storage is not a failure")
        entities.unreadable_chunks.append(((0, 0), "unreadable"))
        self.assertFalse(entities.complete)


class FixtureWorldTests(unittest.TestCase):
    """surface_y, chunk_status and histogram against real game data."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="mcsave-")
        region_dir = os.path.join(cls.dir, "dimensions/minecraft/overworld/region")
        os.makedirs(region_dir)
        shutil.copyfile(
            FIXTURE_REGION, os.path.join(region_dir, os.path.basename(FIXTURE_REGION))
        )
        shutil.copyfile(FIXTURE_LEVEL_DAT, os.path.join(cls.dir, "level.dat"))
        cls.save = MinecraftSave.load(cls.dir)
        cls.dim = cls.save.dimension(OVERWORLD)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_surface_y_finds_a_real_surface(self):
        y = self.dim.surface_y(-400 + 4, 16 + 4, "WORLD_SURFACE")
        self.assertIsNotNone(y)
        self.assertNotEqual(
            self.save.get_block(-396, y, 20).name, "minecraft:air"
        )
        self.assertEqual(
            self.save.get_block(-396, y + 1, 20).name, "minecraft:air"
        )

    def test_surface_y_folds_absence_together(self):
        """Ungenerated ground and a chunk with no heightmaps both have no
        surface; neither is worth an exception, both are worth None.

        Every chunk in the fixture happens to carry maps -- even the
        partly generated ones -- so the absent case is made by removing
        them, which is the true state of a chunk stopped before the
        surface stage in a live world.
        """
        self.assertIsNone(self.dim.surface_y(100_000, 100_000))
        data = self.dim.chunk(-32, 29).data
        stashed = data.pop("Heightmaps")
        try:
            self.assertIsNone(self.dim.surface_y(-32 * 16 + 4, 29 * 16 + 4))
        finally:
            data["Heightmaps"] = stashed

    def test_save_level_passthrough(self):
        self.assertEqual(
            self.save.surface_y(-396, 20, "OCEAN_FLOOR", dimension=OVERWORLD),
            self.dim.surface_y(-396, 20, "OCEAN_FLOOR"),
        )

    def test_chunk_status_tells_the_three_states_apart(self):
        self.assertEqual(self.dim.chunk_status(-24, 1), "minecraft:full")
        self.assertEqual(self.dim.chunk_status(-32, 29), "minecraft:carvers")
        self.assertIsNone(self.dim.chunk_status(500, 500))

    def test_histogram_agrees_with_the_chunk_level_census(self):
        """The boxed census over exactly one chunk must reproduce
        Blocks.histogram, which unpacks everything the slow way."""
        accessor = self.dim.blocks(-24, 1)
        expected = accessor.histogram()
        result = self.save.histogram(
            (-24 * 16, accessor.min_y, 16), (-24 * 16 + 15, accessor.max_y, 31),
            dimension=OVERWORLD,
        )
        self.assertTrue(result.complete)
        self.assertEqual(result.counts, expected)


class FillResultTests(unittest.TestCase):
    def test_complete_is_false_when_anything_was_missed(self):
        result = FillResult()
        self.assertTrue(result.complete)
        result.missing_chunks.append((0, 0))
        self.assertFalse(result.complete)

        other = FillResult()
        other.blocks_out_of_range = 1
        self.assertFalse(other.complete)

    def test_biome_complete_is_false_when_anything_was_missed(self):
        result = BiomeFillResult()
        self.assertTrue(result.complete)
        result.missing_chunks.append((0, 0))
        self.assertFalse(result.complete)

        other = BiomeFillResult()
        other.cells_out_of_range = 1
        self.assertFalse(other.complete)

        unreadable = BiomeFillResult()
        unreadable.unreadable_chunks.append(((0, 0), "too old"))
        self.assertFalse(unreadable.complete)


class RealChunkIntegrationTests(unittest.TestCase):
    """The fixture is genuine game output, unlike the synthetic saves above."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mcsave-real-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        region_dir = os.path.join(self.dir, "dimensions/minecraft/overworld/region")
        os.makedirs(region_dir)
        shutil.copyfile(
            FIXTURE_REGION, os.path.join(region_dir, os.path.basename(FIXTURE_REGION))
        )
        shutil.copyfile(FIXTURE_LEVEL_DAT, os.path.join(self.dir, "level.dat"))
        self.save = MinecraftSave.load(self.dir)

    def _a_real_chunk(self):
        for region in self.save.dimension(OVERWORLD):
            for chunk in region:
                data = chunk.data
                if data is not None and str(data.get("Status")) == "minecraft:full":
                    return chunk
        self.skipTest("no fully-generated chunk in the fixture")

    def test_works_without_player_data(self):
        """A world copied without players/ -- a server world, in effect --
        must still be readable; the player is only a hint at intent."""
        self.assertFalse(
            os.path.exists(os.path.join(self.dir, "players")), "fixture has no player"
        )
        with self.assertRaises(NBTError):
            self.save.player_position()
        self.assertEqual(self.save.dimension().name, OVERWORLD)

    def test_reads_real_terrain(self):
        chunk = self._a_real_chunk()
        accessor = Blocks(chunk)
        x, z = accessor.world_x, accessor.world_z
        self.assertEqual(
            self.save.get_block(x, accessor.min_y, z).name, "minecraft:bedrock"
        )

    def test_reads_real_biomes(self):
        """The fixture has genuine biome palettes, not a synthetic uniform one."""
        chunk = self._a_real_chunk()
        accessor = Blocks(chunk)
        x, z = accessor.world_x, accessor.world_z
        found = self.save.get_biome(x, 70, z)
        self.assertIsNotNone(found)
        self.assertTrue(found.startswith("minecraft:"), found)

    def test_fill_biome_agrees_with_the_chunk_level_reader(self):
        """fill_biome's cell arithmetic, checked through the other route.

        fill_biome derives the cell index in global cell coordinates;
        Blocks.get_biome derives it from chunk-local block coordinates. Since
        the read here goes through the latter, agreeing across chunk, section
        and cell boundaries is evidence rather than the same expression twice.

        This runs against the fixture on purpose: most of its sections have
        multi-entry biome palettes, so the packed index array is real here
        rather than the uniform no-data case the synthetic saves produce.
        """
        chunk = self._a_real_chunk()
        accessor = Blocks(chunk)
        x0, z0 = accessor.world_x, accessor.world_z
        # A box deliberately off cell boundaries, spanning two sections.
        corner1 = (x0 + 1, 62, z0 + 1)
        corner2 = (x0 + 14, 69, z0 + 14)
        biome = "minecraft:snowy_taiga"

        result = self.save.fill_biome(corner1, corner2, biome, keep_loaded=True)
        self.assertTrue(result.complete)

        # Reference: every block coordinate in the grown box must now report
        # the new biome, and nothing one cell outside it may have changed.
        (gx0, gy0, gz0), (gx1, gy1, gz1) = result.written_box
        for x in range(gx0, gx1 + 1):
            for y in range(gy0, gy1 + 1):
                for z in range(gz0, gz1 + 1):
                    self.assertEqual(
                        self.save.get_biome(x, y, z), biome, f"({x},{y},{z})"
                    )
        for point in (
            (gx0 - 1, gy0, gz0),
            (gx1 + 1, gy0, gz0),
            (gx0, gy0 - 1, gz0),
            (gx0, gy1 + 1, gz0),
            (gx0, gy0, gz0 - 1),
            (gx0, gy0, gz1 + 1),
        ):
            found = self.save.get_biome(*point)
            if found is not None:
                self.assertNotEqual(found, biome, f"leaked to {point}")

    def test_biome_fill_leaves_the_region_file_valid(self):
        chunk = self._a_real_chunk()
        accessor = Blocks(chunk)
        x0, z0 = accessor.world_x, accessor.world_z
        self.save.fill_biome((x0, 60, z0), (x0 + 15, 75, z0 + 15), "minecraft:desert")
        self.save.save()

        reloaded = MinecraftSave.load(self.dir)
        self.assertEqual(reloaded.get_biome(x0, 70, z0), "minecraft:desert")
        # Blocks came through untouched alongside the biome change.
        self.assertEqual(
            reloaded.get_block(x0, accessor.min_y, z0).name, "minecraft:bedrock"
        )

    def test_fill_into_real_terrain_and_reload(self):
        chunk = self._a_real_chunk()
        accessor = Blocks(chunk)
        x, z = accessor.world_x + 2, accessor.world_z + 2
        result = self.save.fill((x, 70, z), (x + 3, 74, z + 3), "minecraft:glass")
        self.assertTrue(result.complete)
        self.save.save()

        reloaded = MinecraftSave.load(self.dir)
        self.assertEqual(reloaded.get_block(x, 70, z).name, "minecraft:glass")
        self.assertEqual(reloaded.get_block(x + 3, 74, z + 3).name, "minecraft:glass")

    def test_region_files_stay_valid_after_a_fill(self):
        chunk = self._a_real_chunk()
        accessor = Blocks(chunk)
        self.save.fill(
            (accessor.world_x, 64, accessor.world_z),
            (accessor.world_x + 15, 79, accessor.world_z + 15),
            "minecraft:stone",
        )
        self.save.save()
        region_dir = os.path.join(self.dir, "dimensions/minecraft/overworld/region")
        for name in os.listdir(region_dir):
            path = os.path.join(region_dir, name)
            region = NBT.load(path, eager=True)
            self.assertEqual([c for c in region if c.parse_error], [])
            with open(path, "rb") as handle:
                self.assertEqual(region.to_bytes(), handle.read())


class SaveWriteTests(SaveBase):
    def test_refuses_to_write_an_open_world(self):
        lock = os.path.join(self.dir, nbt.SESSION_LOCK)
        with open(lock, "wb") as handle:
            handle.write("☃".encode())

        import subprocess
        import sys

        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import fcntl,os,sys,time;"
                "fd=os.open(sys.argv[1],os.O_RDWR);"
                "fcntl.lockf(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);"
                "print('held',flush=True);time.sleep(60)",
                lock,
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            self.save.fill((0, 70, 0), (0, 70, 0), "minecraft:stone")
            with self.assertRaises(NBTError) as ctx:
                self.save.save()
            self.assertIn(nbt.SESSION_LOCK, str(ctx.exception))
            self.save.save(force=True)
        finally:
            holder.kill()
            holder.wait()
            holder.stdout.close()

    def test_before_write_is_offered_every_file_before_it_changes(self):
        self.save.fill((0, 70, 0), (0, 70, 0), "minecraft:stone")
        self.save.set_player_position(1.5, 80.0, 1.5)
        offered = []

        def before_write(path):
            with open(path, "rb") as handle:
                offered.append((os.path.relpath(path, self.dir), handle.read()))

        self.assertEqual(self.save.save(before_write=before_write), 2)
        paths = sorted(path for path, _ in offered)
        self.assertEqual(paths, sorted([
            os.path.join(_REGION_DIR, "r.0.0.mca"),
            os.path.relpath(self.save._player_path, self.dir)]))
        # each was offered as it stood, and then written
        for path, before in offered:
            with open(os.path.join(self.dir, path), "rb") as handle:
                self.assertNotEqual(handle.read(), before, path)
        offered.clear()
        self.assertEqual(self.save.save(before_write=before_write), 0)
        self.assertEqual(offered, [], "an unchanged save offered a file")

    def test_a_refusal_from_before_write_stops_the_save_where_it_is(self):
        self.save.fill((0, 70, 0), (0, 70, 0), "minecraft:stone")
        region = os.path.join(self.dir, _REGION_DIR, "r.0.0.mca")
        with open(region, "rb") as handle:
            original = handle.read()

        def refuse(path):
            raise OSError(f"no room to back up {path}")

        with self.assertRaisesRegex(OSError, "no room to back up"):
            self.save.save(before_write=refuse)
        with open(region, "rb") as handle:
            self.assertEqual(handle.read(), original)
        # nothing was lost: the edit is still there to save
        self.assertEqual(self.save.save(), 1)
        self.assertEqual(str(MinecraftSave.load(self.dir).get_block(0, 70, 0)),
                         "minecraft:stone")


class ResolveCoordinatesTests(SaveBase):
    """``save.resolve_coordinates``: the game's ``~`` notation, for CLIs.

    The player in these synthetic saves stands at (8.5, 70.0, 8.5), so the
    block position every ``~`` resolves against is (8, 70, 8).
    """

    def test_absolute_values_pass_through(self):
        self.assertEqual(
            self.save.resolve_coordinates(["1", "-2", "+3"]), (1, -2, 3)
        )

    def test_tilde_is_the_players_block_position(self):
        self.assertEqual(self.save.resolve_coordinates(["~", "~", "~"]), (8, 70, 8))

    def test_offsets_mix_with_absolutes(self):
        self.assertEqual(
            self.save.resolve_coordinates(["~-2", "64", "~3"]), (6, 64, 11)
        )

    def test_six_tokens_cycle_the_axes(self):
        self.assertEqual(
            self.save.resolve_coordinates(["~-1", "~-1", "~-1", "~1", "~1", "~1"]),
            (7, 69, 7, 9, 71, 9),
        )

    def test_an_axes_string_overrides_the_cycle(self):
        self.assertEqual(
            self.save.resolve_coordinates(["~1", "~2", "~3", "~4"], axes="xzxz"),
            (9, 10, 11, 12),
        )

    def test_refusals_name_the_cause(self):
        cases = {
            ("~x", "0", "0"): "expected ~, ~N or ~-N",
            ("1", "2"): "x/y/z groups",
            ("pants", "0", "0"): "expected an integer",
        }
        for tokens, fragment in cases.items():
            with self.subTest(tokens):
                with self.assertRaises(NBTError) as ctx:
                    self.save.resolve_coordinates(list(tokens))
                self.assertIn(fragment, str(ctx.exception))
        with self.assertRaises(NBTError):
            self.save.resolve_coordinates(["1", "2", "3"], axes="xy")
        with self.assertRaises(NBTError):
            self.save.resolve_coordinates(["1"], axes="w")

    def test_a_shell_eaten_tilde_gets_a_quoting_hint(self):
        """An unquoted ~ reaches us as the user's home directory; the error
        must say what happened, not just 'not an integer'."""
        with self.assertRaises(NBTError) as ctx:
            self.save.resolve_coordinates([os.path.expanduser("~"), "0", "0"])
        self.assertIn("quote", str(ctx.exception))

    def test_without_a_player_absolutes_work_and_tilde_explains(self):
        shutil.rmtree(os.path.join(self.dir, "players"))
        fresh = MinecraftSave.load(self.dir)
        self.assertEqual(fresh.resolve_coordinates(["1", "2", "3"]), (1, 2, 3))
        with self.assertRaises(NBTError) as ctx:
            fresh.resolve_coordinates(["~", "0", "0"])
        self.assertIn("player", str(ctx.exception))

    def test_fill_cli_accepts_relative_coordinates(self):
        import contextlib
        import io

        from heaton_voxel.world import _main

        with contextlib.redirect_stdout(io.StringIO()):
            code = _main(
                [
                    self.dir, "--fill",
                    "~-1", "~", "~-1", "~1", "~2", "~1", "minecraft:glass",
                    "--write",
                ]
            )
        self.assertEqual(code, 0)
        fresh = MinecraftSave.load(self.dir)
        self.assertEqual(fresh.get_block(8, 71, 8), "minecraft:glass")
        self.assertEqual(fresh.get_block(10, 71, 8), "minecraft:air")

    def test_fill_cli_refuses_bad_coordinates_cleanly(self):
        import contextlib
        import io

        from heaton_voxel.world import _main

        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = _main(
                [self.dir, "--fill", "~oops", "0", "0", "1", "1", "1", "minecraft:glass"]
            )
        self.assertEqual(code, 2)
        self.assertIn("~oops", err.getvalue())


class DirectConstructionTests(unittest.TestCase):
    """``MinecraftSave(path)`` without :meth:`load` is an empty shell.

    The mistake used to surface as ``AttributeError: 'NoneType' object has
    no attribute 'root'`` from deep inside a mixin -- nowhere near the
    call that made it. Every accessor must instead refuse at once, naming
    ``MinecraftSave.load`` as the fix.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="shell-tests-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.shell = MinecraftSave(self.tmp)

    def assertRefusesByName(self, call):
        with self.assertRaises(NBTError) as caught:
            call()
        self.assertIn("MinecraftSave.load", str(caught.exception))

    def test_player_paths_refuse(self):
        self.assertRefusesByName(self.shell.player_position)
        self.assertRefusesByName(self.shell.spawn_position)
        self.assertRefusesByName(
            lambda: self.shell.set_spawn_position(0, 64, 0))

    def test_block_paths_refuse(self):
        self.assertRefusesByName(lambda: self.shell.surface_y(0, 0))
        self.assertRefusesByName(lambda: self.shell.get_block(0, 64, 0))
        self.assertRefusesByName(lambda: self.shell.dimension(OVERWORLD))

    def test_settings_paths_refuse(self):
        self.assertRefusesByName(self.shell.game_type)
        self.assertRefusesByName(self.shell.world_border)


if __name__ == "__main__":
    unittest.main(verbosity=2)
