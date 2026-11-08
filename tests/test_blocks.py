"""Tests for the block/biome layer.

Like the NBT tests, anything needing real Minecraft data works from a private
snapshot so a world that happens to be open in the game cannot make these
fail at random.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import heaton_voxel.nbt as nbt
from heaton_voxel.nbt import NBT, TagCompound, TagList, TagLongArray, TagString, NBTError
from heaton_voxel.blocks import (
    BLOCKS_PER_SECTION,
    MIN_DATA_VERSION,
    BlockState,
    Blocks,
    PalettedContainer,
    bits_for_palette,
    pack_indices,
    unpack_indices,
)

from fixtures import FIXTURE_REGION, snapshot_region

# See fixtures/__init__.py: real Minecraft output, checked in, so the suite is
# self-contained. MINECRAFT_SAVE points it at a live world instead.
SAVE = os.environ.get("MINECRAFT_SAVE")
SOURCE_REGION = (
    os.path.join(SAVE, "dimensions/minecraft/overworld/region/r.0.0.mca")
    if SAVE
    else FIXTURE_REGION
)
REGION = None
_SNAPSHOT_DIR = None


def setUpModule():
    global REGION, _SNAPSHOT_DIR
    REGION, _SNAPSHOT_DIR = snapshot_region(SOURCE_REGION)


def tearDownModule():
    if _SNAPSHOT_DIR:
        shutil.rmtree(_SNAPSHOT_DIR, ignore_errors=True)


class BlockStateTests(unittest.TestCase):
    def test_parse_plain_name(self):
        state = BlockState.parse("minecraft:stone")
        self.assertEqual(state.name, "minecraft:stone")
        self.assertEqual(state.properties, {})
        self.assertEqual(str(state), "minecraft:stone")

    def test_parse_with_properties(self):
        state = BlockState.parse("minecraft:oak_stairs[facing=north,half=bottom]")
        self.assertEqual(state.name, "minecraft:oak_stairs")
        self.assertEqual(state.properties, {"facing": "north", "half": "bottom"})

    def test_round_trips_through_text(self):
        for text in (
            "minecraft:stone",
            "minecraft:lava[level=8]",
            "minecraft:oak_stairs[facing=north,half=bottom]",
        ):
            with self.subTest(text=text):
                self.assertEqual(str(BlockState.parse(text)), text)

    def test_malformed_text_rejected(self):
        for text in ("minecraft:stone[facing]", "minecraft:stone[a=1,b]"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    BlockState.parse(text)

    def test_property_order_does_not_affect_identity(self):
        a = BlockState.parse("minecraft:x[a=1,b=2]")
        b = BlockState.parse("minecraft:x[b=2,a=1]")
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))

    def test_different_properties_are_different_states(self):
        north = BlockState.parse("minecraft:oak_stairs[facing=north]")
        south = BlockState.parse("minecraft:oak_stairs[facing=south]")
        self.assertNotEqual(north, south)
        self.assertEqual(len({north, south}), 2)

    def test_compares_equal_to_a_plain_name(self):
        self.assertEqual(BlockState("minecraft:stone"), "minecraft:stone")
        # ...but only when it has no properties to lose.
        self.assertNotEqual(BlockState("minecraft:lava", {"level": "8"}), "minecraft:lava")

    def test_nbt_round_trip(self):
        state = BlockState.parse("minecraft:lava[level=8]")
        tag = state.to_nbt()
        self.assertEqual(str(tag["Name"]), "minecraft:lava")
        self.assertEqual(str(tag["Properties"]["level"]), "8")
        self.assertEqual(BlockState.from_nbt(tag), state)

    def test_from_nbt_preserves_the_source_tag(self):
        """Key order in the palette entry must survive, for byte-exactness."""
        tag = TagCompound()
        tag["Properties"] = TagCompound({"axis": TagString("y")})
        tag["Name"] = TagString("minecraft:deepslate")
        state = BlockState.from_nbt(tag)
        self.assertIs(state.to_nbt(), tag)
        self.assertEqual(list(state.to_nbt().keys()), ["Properties", "Name"])

    def test_with_properties(self):
        state = BlockState.parse("minecraft:oak_stairs[facing=north,half=bottom]")
        turned = state.with_properties(facing="south")
        self.assertEqual(turned.properties["facing"], "south")
        self.assertEqual(turned.properties["half"], "bottom")
        self.assertEqual(state.properties["facing"], "north", "original mutated")


class SequenceAssertions(unittest.TestCase):
    """assertEqual on two differing 4096-element lists spends minutes building
    a diff nobody reads. Report the first mismatch instead."""

    def assertSequencesEqual(self, actual, expected, msg=""):
        actual, expected = list(actual), list(expected)
        if actual == expected:
            return
        prefix = f"{msg}: " if msg else ""
        if len(actual) != len(expected):
            self.fail(f"{prefix}length {len(actual)} != {len(expected)}")
        for i, (a, b) in enumerate(zip(actual, expected)):
            if a != b:
                self.fail(f"{prefix}first difference at index {i}: {a!r} != {b!r}")


class BitPackingTests(SequenceAssertions):
    def test_bits_for_palette(self):
        # Blocks never go below 4 bits.
        self.assertEqual(bits_for_palette(1, 4), 4)
        self.assertEqual(bits_for_palette(16, 4), 4)
        self.assertEqual(bits_for_palette(17, 4), 5)
        self.assertEqual(bits_for_palette(32, 4), 5)
        self.assertEqual(bits_for_palette(33, 4), 6)
        # Biomes can.
        self.assertEqual(bits_for_palette(2, 1), 1)
        self.assertEqual(bits_for_palette(3, 1), 2)

    def test_round_trip_every_bit_width(self):
        for bits in range(1, 17):
            with self.subTest(bits=bits):
                count = BLOCKS_PER_SECTION
                limit = (1 << bits) - 1
                values = [(i * 7 + bits) % (limit + 1) for i in range(count)]
                packed = pack_indices(values, count, bits)
                self.assertSequencesEqual(unpack_indices(packed, count, bits), values)

    def test_entries_never_straddle_a_long(self):
        """The current layout wastes the leftover high bits of each long
        rather than continuing an entry into the next one."""
        for bits in (3, 5, 6, 7, 9):
            with self.subTest(bits=bits):
                per_long = 64 // bits
                packed = pack_indices(
                    [0] * BLOCKS_PER_SECTION, BLOCKS_PER_SECTION, bits
                )
                expected = -(-BLOCKS_PER_SECTION // per_long)
                self.assertEqual(len(packed), expected)

    def test_high_bit_values_survive(self):
        """Values with bit 63 set become negative NBT longs."""
        values = [0xFFFF] * 4 + [0] * (BLOCKS_PER_SECTION - 4)
        packed = pack_indices(values, BLOCKS_PER_SECTION, 16)
        self.assertTrue(any(v < 0 for v in packed), "expected a negative long")
        self.assertSequencesEqual(unpack_indices(packed, BLOCKS_PER_SECTION, 16), values)

    def test_packed_array_is_a_long_array_tag(self):
        packed = pack_indices([0] * 64, 64, 1)
        self.assertIsInstance(packed, TagLongArray)


@unittest.skipUnless(os.path.exists(SOURCE_REGION), "region file not available")
class RealChunkTests(SequenceAssertions):
    def setUp(self):
        self.region = NBT.load(REGION)
        self.chunk = None
        for chunk in self.region:
            data = chunk.data
            if data is not None and str(data.get("Status")) == "minecraft:full":
                self.chunk = chunk
                break
        if self.chunk is None:
            self.skipTest("no fully-generated chunk in this region")
        self.blocks = Blocks(self.chunk)

    def test_geometry(self):
        self.assertEqual(self.blocks.world_x % 16, 0)
        self.assertEqual(self.blocks.world_z % 16, 0)
        self.assertLess(self.blocks.min_y, self.blocks.max_y)
        self.assertTrue(self.blocks.sections)

    def test_bedrock_is_at_the_bottom(self):
        floor = self.blocks[0, self.blocks.min_y, 0]
        self.assertEqual(floor.name, "minecraft:bedrock")

    def test_repacking_reproduces_the_games_own_bytes(self):
        """The real proof that the bit layout is right."""
        checked = 0
        for section in self.blocks:
            for container in (section.blocks, section.biomes):
                if container is None or "data" not in container._tag:
                    continue
                original = list(container._tag["data"])
                repacked = pack_indices(
                    container.indices(), container._count, container.bits
                )
                self.assertSequencesEqual(repacked, original, f"{section}")
                checked += 1
        self.assertGreater(checked, 0)

    def test_indices_are_in_range(self):
        for section in self.blocks:
            container = section.blocks
            if container is None:
                continue
            size = len(container.palette)
            self.assertTrue(all(0 <= i < size for i in container.indices()))

    def test_point_read_matches_bulk_unpack(self):
        """get() extracts one entry directly; it must agree with the array."""
        for section in self.blocks:
            container = section.blocks
            if container is None:
                continue
            indices = container.indices()
            palette = container.palette
            for index in (0, 1, 17, 255, 1000, 4095):
                self.assertEqual(container.get(index), palette[indices[index]])
            break

    def test_reading_does_not_modify_the_chunk(self):
        """Merely inspecting blocks must leave the file byte-identical."""
        with open(REGION, "rb") as handle:
            original = handle.read()
        for section in self.blocks:
            if section.blocks:
                section.blocks.indices()
                section.blocks.histogram()
        self.assertEqual(self.region.to_bytes(), original)

    def test_histogram_totals_a_full_section(self):
        for section in self.blocks:
            if section.blocks is None:
                continue
            self.assertEqual(sum(section.blocks.histogram().values()), BLOCKS_PER_SECTION)
            break

    def test_biomes_are_readable(self):
        biome = self.blocks.get_biome(0, self.blocks.min_y + 8, 0)
        self.assertTrue(str(biome).startswith("minecraft:"))

    # -- writing -----------------------------------------------------------

    def test_set_and_read_back(self):
        y = self.blocks.min_y + 8
        self.blocks[3, y, 5] = "minecraft:diamond_block"
        self.assertEqual(self.blocks[3, y, 5].name, "minecraft:diamond_block")
        # Neighbors must be untouched.
        self.assertNotEqual(self.blocks[4, y, 5].name, "minecraft:diamond_block")
        self.assertNotEqual(self.blocks[3, y, 6].name, "minecraft:diamond_block")

    def test_set_persists_through_save_and_reload(self):
        y = self.blocks.min_y + 9
        state = BlockState.parse("minecraft:chest[facing=north]")
        self.blocks[7, y, 7] = state
        key = (self.chunk.local_x, self.chunk.local_z)

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            self.region.save(path)
            reloaded = NBT.load(path)

        again = Blocks(reloaded.chunks[key])
        self.assertEqual(again[7, y, 7], state)

    def test_set_writes_through_with_no_flush(self):
        """The edit must reach the NBT immediately, not sit in a buffer."""
        y = self.blocks.min_y + 10
        before = self.region.to_bytes()
        self.blocks[1, y, 1] = "minecraft:glowstone"
        self.assertNotEqual(self.region.to_bytes(), before)

    def test_palette_growth_repacks_correctly(self):
        """Pushing the palette past a power of two widens every index."""
        section = self.blocks.sections[self.blocks.min_y >> 4]
        container = section.blocks
        start_bits = container.bits
        expected = list(container.indices())

        added = 0
        while container.bits == start_bits and added < 400:
            index = len(container.palette)
            container.set(0, BlockState(f"test:filler_{added}"))
            expected[0] = index
            added += 1
        self.assertGreater(container.bits, start_bits, "palette never widened")

        # Everything else must have survived the rewrite.
        self.assertSequencesEqual(container.indices(), expected)
        fresh = PalettedContainer(container._tag, container._count, container._min_bits)
        self.assertSequencesEqual(fresh.indices(), expected)

    def test_set_every_block_in_a_section(self):
        section_y = (self.blocks.min_y >> 4) + 4
        if section_y not in self.blocks.sections:
            self.skipTest("section not present")
        base = section_y * 16
        for x in range(16):
            for z in range(16):
                self.blocks[x, base + 3, z] = "minecraft:glass"
        for x in range(16):
            for z in range(16):
                self.assertEqual(self.blocks[x, base + 3, z].name, "minecraft:glass")

    def test_prune_removes_orphaned_palette_entries(self):
        section = self.blocks.sections[self.blocks.min_y >> 4]
        container = section.blocks
        before = len(container.palette)

        container.set(0, BlockState("test:temporary"))
        self.assertEqual(len(container.palette), before + 1)
        # Overwrite it again; the entry is now unreferenced.
        container.set(0, container.palette[0])
        removed = container.prune()

        self.assertGreater(removed, 0)
        self.assertNotIn(BlockState("test:temporary"), container.palette)
        # Contents survive the renumbering.
        fresh = PalettedContainer(container._tag, container._count, container._min_bits)
        self.assertSequencesEqual(fresh.indices(), container.indices())

    def _biome_section(self):
        """The lowest section that actually carries biome data."""
        for section in self.blocks:
            if section.biomes is not None:
                return section
        self.skipTest("no section with biome data")

    def test_chunk_prune_covers_biome_palettes(self):
        """Blocks.prune() must clean biomes too, not just block containers."""
        section = self._biome_section()
        container = section.biomes
        original = container.get(0)
        before = len(container.palette)

        container.set(0, "test:temporary")
        self.assertEqual(len(container.palette), before + 1)
        container.set(0, original)  # now unreferenced

        removed = self.blocks.prune()
        self.assertGreater(removed, 0)
        self.assertNotIn("test:temporary", container.palette)
        self.assertEqual(container.get(0), original)

    def test_chunk_prune_sums_block_and_biome_removals(self):
        section = self._biome_section()
        blocks, biomes = section.blocks, section.biomes
        if blocks is None:
            self.skipTest("section has no block data")

        block_original = blocks.get(0)
        biome_original = biomes.get(0)
        blocks.set(0, BlockState("test:temporary"))
        blocks.set(0, block_original)
        biomes.set(0, "test:temporary")
        biomes.set(0, biome_original)

        self.assertEqual(self.blocks.prune(), 2)
        self.assertEqual(self.blocks.prune(), 0, "a second pass finds nothing")

    def test_chunk_prune_collapses_a_biome_palette_to_uniform(self):
        """The min_bits=1 collapse path, driven through the chunk-level prune."""
        section = self._biome_section()
        container = section.biomes
        container.fill_all("minecraft:plains")
        container.set(0, "minecraft:the_void")
        container.set(0, "minecraft:plains")

        self.assertEqual(self.blocks.prune(), 1)
        self.assertEqual(len(container.palette), 1)
        self.assertNotIn("data", container._tag, "should drop the redundant array")
        self.assertEqual(container.get(0), "minecraft:plains")

    def test_biome_write(self):
        y = self.blocks.min_y + 8
        self.blocks.set_biome(0, y, 0, "minecraft:the_void")
        self.assertEqual(self.blocks.get_biome(0, y, 0), "minecraft:the_void")

    # -- bounds ------------------------------------------------------------

    def test_out_of_range_coordinates_are_rejected(self):
        y = self.blocks.min_y + 8
        for x, z in ((16, 0), (-1, 0), (0, 16), (0, -1)):
            with self.subTest(x=x, z=z):
                with self.assertRaises(IndexError) as ctx:
                    self.blocks[x, y, z]
                self.assertIn("chunk-local", str(ctx.exception))

    def test_out_of_range_y_is_rejected(self):
        with self.assertRaises(IndexError) as ctx:
            self.blocks[0, self.blocks.max_y + 100, 0]
        self.assertIn("outside this chunk", str(ctx.exception))


class SyntheticContainerTests(SequenceAssertions):
    """Cases the real world does not happen to contain."""

    def _uniform(self):
        """A container with one palette entry and no data array, as the game
        writes for a section of solid stone."""
        tag = TagCompound()
        tag["palette"] = TagList(
            [BlockState("minecraft:stone").to_nbt()], element_type=nbt.TAG_COMPOUND
        )
        return PalettedContainer(tag, BLOCKS_PER_SECTION, 4)

    def test_uniform_container_reads_without_a_data_array(self):
        container = self._uniform()
        self.assertNotIn("data", container._tag)
        self.assertEqual(container.get(0).name, "minecraft:stone")
        self.assertEqual(container.get(4095).name, "minecraft:stone")
        self.assertEqual(len(container.indices()), BLOCKS_PER_SECTION)
        self.assertEqual(set(container.indices()), {0})

    def test_writing_to_a_uniform_container_creates_the_array(self):
        container = self._uniform()
        container.set(100, BlockState("minecraft:diamond_block"))
        self.assertIn("data", container._tag)
        self.assertEqual(container.get(100).name, "minecraft:diamond_block")
        self.assertEqual(container.get(99).name, "minecraft:stone")
        self.assertEqual(container.get(4095).name, "minecraft:stone")

        # And it survives a fresh parse of the tag.
        fresh = PalettedContainer(container._tag, BLOCKS_PER_SECTION, 4)
        self.assertEqual(fresh.get(100).name, "minecraft:diamond_block")
        self.assertEqual(fresh.get(99).name, "minecraft:stone")

    def test_setting_the_same_block_is_a_no_op(self):
        container = self._uniform()
        container.set(10, BlockState("minecraft:stone"))
        self.assertEqual(len(container.palette), 1)

    def test_prune_collapses_back_to_uniform(self):
        container = self._uniform()
        container.set(5, BlockState("minecraft:dirt"))
        container.set(5, BlockState("minecraft:stone"))
        container.prune()
        self.assertEqual(len(container.palette), 1)
        self.assertNotIn("data", container._tag, "should drop the redundant array")
        self.assertEqual(container.get(5).name, "minecraft:stone")

    def test_set_all_rebuilds_palette_and_array_in_one_pass(self):
        container = self._uniform()
        states = [BlockState(f"minecraft:b{i}") for i in range(20)]
        values = [states[i % 20] for i in range(BLOCKS_PER_SECTION)]
        container.set_all(values)
        self.assertEqual(len(container.palette), 20)
        self.assertEqual(container.get(0).name, "minecraft:b0")
        self.assertEqual(container.get(21).name, "minecraft:b1")

        # Survives a fresh parse of the tag it wrote.
        fresh = PalettedContainer(container._tag, BLOCKS_PER_SECTION, 4)
        self.assertSequencesEqual(
            [fresh.get(i).name for i in range(40)],
            [f"minecraft:b{i % 20}" for i in range(40)],
        )

    def test_set_all_collapses_a_uniform_sequence(self):
        container = self._uniform()
        container.set(5, BlockState("minecraft:dirt"))  # give it an array first
        container.set_all([BlockState("minecraft:deepslate")] * BLOCKS_PER_SECTION)
        self.assertEqual(len(container.palette), 1)
        self.assertNotIn("data", container._tag, "uniform content stores no array")
        self.assertEqual(container.get(4095).name, "minecraft:deepslate")

    def test_set_all_refuses_the_wrong_count(self):
        container = self._uniform()
        with self.assertRaises(NBTError) as ctx:
            container.set_all([BlockState("minecraft:stone")] * 10)
        self.assertIn("4096", str(ctx.exception))

    def test_nibble_unpacking(self):
        """Light arrays: two 4-bit levels per byte, low nibble first."""
        from heaton_voxel.blocks import unpack_nibbles

        data = nbt.TagByteArray([0] * 2048)
        data[0] = -1          # 0xFF -> cells 0 and 1 both 15
        data[1] = 0x0F        # cell 2 = 15, cell 3 = 0
        data[2] = -16         # 0xF0 -> cell 4 = 0, cell 5 = 15
        values = unpack_nibbles(data)
        self.assertEqual(values[:6], [15, 15, 15, 0, 0, 15])
        self.assertEqual(len(values), BLOCKS_PER_SECTION)
        with self.assertRaises(NBTError):
            unpack_nibbles(nbt.TagByteArray([0] * 100))

    def test_wider_than_necessary_array_is_honored(self):
        """A palette that shrank leaves the array packed at the old width; the
        length is the authority, not the palette size."""
        tag = TagCompound()
        tag["palette"] = TagList(
            [BlockState(f"minecraft:b{i}").to_nbt() for i in range(3)],
            element_type=nbt.TAG_COMPOUND,
        )
        # 3 entries needs 4 bits, but pack at 5 as the game may have left it.
        values = [i % 3 for i in range(BLOCKS_PER_SECTION)]
        tag["data"] = pack_indices(values, BLOCKS_PER_SECTION, 5)
        container = PalettedContainer(tag, BLOCKS_PER_SECTION, 4)
        self.assertEqual(container.bits, 5)
        self.assertSequencesEqual(container.indices(), values)

    def test_old_chunks_are_refused_not_misread(self):
        """Reading a chunk older than 1.13, one of 1.13's own snapshots, or
        one of the 1.18 snapshots' formats between 1.17.1 and 1.18, with
        either rule would yield plausible-looking nonsense, so it must fail
        loudly instead. (1.13 to 1.17 are read, never written:
        tests/test_legacy.py.)"""
        for version in (1343, 1493, 1518, 2731, 2824):
            with self.subTest(version=version):
                data = TagCompound(
                    {
                        "DataVersion": nbt.TagInt(version),
                        "Level": TagCompound({}),
                    }
                )
                with self.assertRaises(NBTError) as ctx:
                    Blocks(data)
                self.assertIn("neither format this library reads", str(ctx.exception))

    def test_current_data_version_is_accepted(self):
        data = TagCompound(
            {"DataVersion": nbt.TagInt(MIN_DATA_VERSION), "sections": TagList([])}
        )
        self.assertEqual(Blocks(data).data_version, MIN_DATA_VERSION)


class LightReadTests(unittest.TestCase):
    """Stored light against real game output in the fixture."""

    @classmethod
    def setUpClass(cls):
        region = nbt.NBT.load(FIXTURE_REGION)
        cls.blocks = Blocks(region.chunks[(4, 14)])  # the campfire chunk

    def test_light_is_physically_coherent(self):
        surface = self.blocks.surface_y(2, 1)
        self.assertEqual(self.blocks.sky_light(2, surface + 2, 1), 15,
                         "open sky above the surface")
        # the campfire at local (2, 67, 1) emits full block light
        self.assertEqual(self.blocks.block_light(2, 67, 1), 15)
        self.assertEqual(self.blocks.block_light(2, 69, 1), 13,
                         "light falls off one level per block")

    def test_absent_arrays_read_as_none_not_darkness(self):
        self.assertIsNone(self.blocks.sky_light(2, -40, 1))
        self.assertIsNone(self.blocks.block_light(2, -40, 1))

    def test_section_bulk_arrays(self):
        section = self.blocks.sections[4]  # holds y 64..79, the surface
        sky = section.sky_light()
        self.assertEqual(len(sky), 4096)
        self.assertTrue(all(0 <= v <= 15 for v in sky))


class SurfaceLookupTests(unittest.TestCase):
    """Blocks.surface_y against the fixture's genuine heightmaps."""

    FULL = (8, 1)
    PROTO = (0, 29)  # carvers: has maps in the fixture, but see below

    def setUp(self):
        self.region = NBT.load(FIXTURE_REGION)
        self.blocks = Blocks(self.region.chunks[self.FULL])

    def test_matches_a_manual_unpack_everywhere(self):
        """The wrapped lookup is the documented recipe, cell for cell."""
        from heaton_voxel.blocks import heightmap_bits

        chunk = self.region.chunks[self.FULL]
        bits = heightmap_bits(self.blocks.min_y, self.blocks.max_y)
        raw = unpack_indices(chunk.data["Heightmaps"]["WORLD_SURFACE"], 256, bits)
        for lx in (0, 7, 15):
            for lz in (0, 8, 15):
                expected = self.blocks.min_y + raw[lz * 16 + lx] - 1
                self.assertEqual(
                    self.blocks.surface_y(lx, lz, "WORLD_SURFACE"), expected
                )

    def test_the_surface_is_real(self):
        """The block above the reported surface is air; the surface is not."""
        y = self.blocks.surface_y(4, 4, "WORLD_SURFACE")
        self.assertNotEqual(self.blocks.get(4, y, 4).name, "minecraft:air")
        self.assertEqual(self.blocks.get(4, y + 1, 4).name, "minecraft:air")

    def test_ocean_floor_never_sits_above_world_surface(self):
        for lx, lz in ((0, 0), (5, 9), (15, 15)):
            floor = self.blocks.surface_y(lx, lz, "OCEAN_FLOOR")
            top = self.blocks.surface_y(lx, lz, "WORLD_SURFACE")
            self.assertLessEqual(floor, top)

    def test_unknown_map_name_reports_no_surface(self):
        self.assertIsNone(self.blocks.surface_y(0, 0, "NOT_A_MAP"))

    def test_out_of_chunk_coordinates_are_refused(self):
        with self.assertRaises(IndexError):
            self.blocks.surface_y(16, 0)

    def test_a_wrong_length_map_raises_rather_than_guessing(self):
        chunk = self.region.chunks[self.FULL]
        maps = chunk.data["Heightmaps"]
        maps["WORLD_SURFACE"] = TagLongArray(list(maps["WORLD_SURFACE"])[:-1])
        with self.assertRaises(NBTError) as ctx:
            Blocks(chunk).surface_y(0, 0, "WORLD_SURFACE")
        self.assertIn("heightmap", str(ctx.exception))

    def test_maps_are_cached_per_accessor(self):
        """Documented: edits to the stored tags are not seen until the chunk
        is re-read. The cache is the reason a per-column sweep is cheap."""
        before = self.blocks.surface_y(3, 3, "WORLD_SURFACE")
        self.region.chunks[self.FULL].data["Heightmaps"].pop("WORLD_SURFACE")
        self.assertEqual(self.blocks.surface_y(3, 3, "WORLD_SURFACE"), before)

    def test_dropping_the_cache_reads_the_tags_again(self):
        """The escape hatch for code that rewrites Heightmaps in place --
        without it, a rewrite is invisible to the accessor that did it."""
        before = self.blocks.surface_y(3, 3, "WORLD_SURFACE")
        self.assertIsNotNone(before)
        self.region.chunks[self.FULL].data["Heightmaps"].pop("WORLD_SURFACE")
        self.blocks.drop_heightmap_cache()
        self.assertIsNone(self.blocks.surface_y(3, 3, "WORLD_SURFACE"))


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.region = NBT.load(FIXTURE_REGION)

    def test_generation_stages_read_back(self):
        self.assertEqual(
            Blocks(self.region.chunks[(8, 1)]).status, "minecraft:full"
        )
        self.assertEqual(
            Blocks(self.region.chunks[(0, 29)]).status, "minecraft:carvers"
        )

    def test_absent_status_is_none_not_a_string(self):
        data = TagCompound(
            {"DataVersion": nbt.TagInt(MIN_DATA_VERSION), "sections": TagList([])}
        )
        self.assertIsNone(Blocks(data).status)


class WorldRangeTests(unittest.TestCase):
    """``min_y``/``max_y`` are the world, not the span of stored sections.

    The game keeps light one section past each end of the world; a section
    holding light and no blocks is neither the floor nor the ceiling. The
    real-chunk cases are in ``test_modern_states``; these are the fallbacks.
    """

    @staticmethod
    def chunk(*section_ys, light=(), y_pos=None):
        sections = TagList(element_type=nbt.TAG_COMPOUND)
        for section_y in sorted(set(section_ys) | set(light)):
            tag = TagCompound({"Y": nbt.TagByte(section_y)})
            if section_y in section_ys:
                tag["block_states"] = TagCompound({"palette": TagList(
                    [BlockState("minecraft:air").to_nbt()],
                    element_type=nbt.TAG_COMPOUND)})
            else:
                tag["SkyLight"] = nbt.TagByteArray([0] * 2048)
            sections.append(tag)
        data = TagCompound({"DataVersion": nbt.TagInt(MIN_DATA_VERSION),
                            "sections": sections})
        if y_pos is not None:
            data["yPos"] = nbt.TagInt(y_pos)
        return Blocks(data)

    def test_without_y_pos_the_floor_is_the_lowest_section_holding_blocks(self):
        blocks = self.chunk(*range(-4, 20), light=(-5, 20))
        self.assertEqual((blocks.min_y, blocks.max_y), (-64, 319))

    def test_the_recorded_floor_wins(self):
        # yPos and the lowest section holding blocks disagree here, so only
        # the recorded floor gives -64: the sections alone say -48 (-80
        # counting the light).
        blocks = self.chunk(*range(-3, 20), light=(-5, 20), y_pos=-4)
        self.assertEqual((blocks.min_y, blocks.max_y), (-64, 319))
        # A recorded floor holds with no blocks at all, over an empty range.
        blocks = self.chunk(light=(-5,), y_pos=-4)
        self.assertEqual((blocks.min_y, blocks.max_y), (-64, -65))

    def test_a_chunk_storing_no_blocks_spans_nothing(self):
        blocks = self.chunk(light=(-5,))
        self.assertLess(blocks.max_y, blocks.min_y)
        self.assertEqual(blocks.min_y, 0)

    def test_a_section_of_light_alone_holds_no_block(self):
        # Past either end of the world is outside the chunk, whether or not
        # light is stored there: a cell must not read as air in the chunks
        # that keep such a section and as nothing in the chunks that do not.
        blocks = self.chunk(*range(-4, 20), light=(-5, 20), y_pos=-4)
        for y in (-80, -65, 320, 335):
            with self.subTest(y=y):
                with self.assertRaises(IndexError):
                    blocks.get(0, y, 0)
                with self.assertRaises(IndexError):
                    blocks.set(0, y, 0, "minecraft:stone")
                with self.assertRaises(IndexError):
                    blocks.get_biome(0, y, 0)
                with self.assertRaises(IndexError):
                    blocks.set_biome(0, y, 0, "minecraft:plains")
                # The light it stores is still there to read.
                self.assertEqual(blocks.sky_light(0, y, 0), 0)
                self.assertIsNone(blocks.block_light(0, y, 0))
        self.assertEqual(blocks.get(0, -64, 0).name, "minecraft:air")
        self.assertEqual(blocks.get(0, 319, 0).name, "minecraft:air")


class AirBlocksTests(unittest.TestCase):
    def test_one_air_set_for_the_whole_library(self):
        from heaton_voxel import blocks, schem

        # The game's own #minecraft:air tag, data/minecraft/tags/block/air.json.
        self.assertEqual(blocks.AIR_BLOCKS, frozenset(
            {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}))
        self.assertIn("AIR_BLOCKS", blocks.__all__)
        self.assertIs(schem.AIR_BLOCKS, blocks.AIR_BLOCKS,
                      "the schematic mask is the same set, not a copy")


class LightingResetTests(unittest.TestCase):
    """reset_lighting against genuine game chunks.

    The game trusts stored light for as long as ``isLightOn`` stands, so an
    edited chunk that keeps it renders torches that light only their own
    block. The fixture carries real flags and real light arrays; these
    mutate a fresh in-memory copy each time and never write it back.
    """

    FULL = (8, 1)  # a full chunk: isLightOn=1, several sections with light
    PROTO = (0, 28)  # initialize_light: carries light arrays but no flag

    def setUp(self):
        self.region = NBT.load(FIXTURE_REGION)

    def stored_arrays(self, chunk) -> int:
        return sum(
            ("BlockLight" in tag) + ("SkyLight" in tag)
            for tag in chunk.data["sections"]
        )

    def test_clears_the_flag_with_the_games_own_type(self):
        chunk = self.region.chunks[self.FULL]
        self.assertEqual(int(chunk.data["isLightOn"]), 1, "fixture drifted")
        Blocks(chunk).reset_lighting()
        flag = chunk.data["isLightOn"]
        self.assertEqual(int(flag), 0)
        # A byte, as the game wrote it -- an int here would change the tag
        # type on disk and hand Minecraft a chunk it never produced.
        self.assertIsInstance(flag, nbt.TagByte)

    def test_strips_every_stored_light_array_and_counts_them(self):
        chunk = self.region.chunks[self.FULL]
        before = self.stored_arrays(chunk)
        self.assertGreater(before, 0, "fixture drifted: no light to strip")
        removed = Blocks(chunk).reset_lighting()
        self.assertEqual(removed, before)
        self.assertEqual(self.stored_arrays(chunk), 0)

    def test_reset_is_idempotent(self):
        chunk = self.region.chunks[self.FULL]
        accessor = Blocks(chunk)
        accessor.reset_lighting()
        self.assertEqual(accessor.reset_lighting(), 0)
        self.assertEqual(int(chunk.data["isLightOn"]), 0)

    def test_a_chunk_without_the_flag_gains_it_cleared(self):
        """A partly generated chunk stores light before it stores the flag;
        resetting must still leave it in the "relight me" state."""
        chunk = self.region.chunks[self.PROTO]
        self.assertNotIn("isLightOn", chunk.data, "fixture drifted")
        before = self.stored_arrays(chunk)
        removed = Blocks(chunk).reset_lighting()
        self.assertEqual(removed, before)
        self.assertEqual(self.stored_arrays(chunk), 0)
        self.assertEqual(int(chunk.data["isLightOn"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
