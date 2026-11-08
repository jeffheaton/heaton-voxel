"""Tests for the Sponge ``.schem`` layer: the file format, export, import.

Worlds are built from scratch in temp directories (see ``test_world.make_save``)
so both sides of a round trip are fully known; the vendored fixture supplies
real chunks -- and real block entities -- where genuine game output matters.
"""

from __future__ import annotations

import contextlib
import gzip
import io
import os
import shutil
import tempfile
import unittest

import heaton_voxel.nbt as nbt
from fixtures import FIXTURE_LEVEL_DAT, FIXTURE_REGION
from heaton_voxel.blocks import BlockState
from heaton_voxel.nbt import (
    NBT,
    Chunk,
    NBTError,
    TagByteArray,
    TagCompound,
    TagDouble,
    TagInt,
    TagIntArray,
    TagList,
    TagShort,
    TagString,
    encode,
)
from heaton_voxel.schem import (
    Schematic,
    _main,
    export_schem,
    import_schem,
)
from heaton_voxel.world import OVERWORLD, MinecraftSave
from tests.test_blocks import SequenceAssertions
from tests.test_world import DATA_VERSION, make_save

CHUNKS_3X3 = [(x, z) for x in range(-1, 2) for z in range(-1, 2)]


def write_raw_schem(path: str, root: TagCompound, root_name: str = "") -> None:
    """Write a hand-built tree as a gzip .schem, bypassing Schematic.save."""
    with open(path, "wb") as handle:
        handle.write(gzip.compress(encode(root, root_name), mtime=0))


def make_entity(identity: str, pos, uuid_words, **extra) -> TagCompound:
    tag = TagCompound(
        {
            "id": TagString(identity),
            "Pos": TagList(
                [TagDouble(float(v)) for v in pos], element_type=nbt.TAG_DOUBLE
            ),
            "UUID": TagIntArray(list(uuid_words)),
        }
    )
    tag.update(extra)
    return tag


def install_entities(world_dir: str, per_chunk) -> None:
    """Write entity region files holding exactly these entities.

    ``per_chunk`` maps ``(chunk_x, chunk_z)`` to a list of entity compounds.
    """
    entities_dir = os.path.join(
        world_dir, "dimensions", "minecraft", "overworld", "entities"
    )
    os.makedirs(entities_dir, exist_ok=True)
    by_region: dict[tuple[int, int], list] = {}
    for (chunk_x, chunk_z), members in per_chunk.items():
        by_region.setdefault((chunk_x >> 5, chunk_z >> 5), []).append(
            (chunk_x, chunk_z, members)
        )
    for (region_x, region_z), chunks in by_region.items():
        region = NBT()
        region.kind = "region"
        region.region_x, region.region_z = region_x, region_z
        for chunk_x, chunk_z, members in chunks:
            chunk = Chunk(chunk_x & 31, chunk_z & 31)
            chunk.data = TagCompound(
                {
                    "Position": TagIntArray([chunk_x, chunk_z]),
                    "DataVersion": TagInt(DATA_VERSION),
                    "Entities": TagList(members, element_type=nbt.TAG_COMPOUND),
                }
            )
            region.chunks[(chunk_x & 31, chunk_z & 31)] = chunk
        region.save(os.path.join(entities_dir, f"r.{region_x}.{region_z}.mca"))


class TempSaveBase(SequenceAssertions):
    CHUNKS = CHUNKS_3X3

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="schem-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, self.CHUNKS)
        self.save = MinecraftSave.load(self.dir)

    def schem_path(self, name: str = "box.schem") -> str:
        return os.path.join(self.dir, name)

    def box_states(self, save, corner1, corner2):
        """Every block in a box, flattened y-z-x, for sequence comparison."""
        (x0, y0, z0), (x1, y1, z1) = corner1, corner2
        return [
            save.get_block(x, y, z)
            for y in range(y0, y1 + 1)
            for z in range(z0, z1 + 1)
            for x in range(x0, x1 + 1)
        ]


# --------------------------------------------------------------------------
# The in-memory object
# --------------------------------------------------------------------------


class SchematicObjectTests(SequenceAssertions):
    def test_new_is_air_with_the_given_stamp(self):
        schematic = Schematic.new(3, 4, 5, DATA_VERSION)
        self.assertEqual(schematic.size, (3, 4, 5))
        self.assertEqual(schematic.volume, 60)
        self.assertEqual(schematic.data_version, DATA_VERSION)
        self.assertEqual(schematic.get_block(2, 3, 4), "minecraft:air")

    def test_set_get_and_item_access(self):
        schematic = Schematic.new(2, 2, 2, DATA_VERSION)
        schematic.set_block(0, 1, 0, "minecraft:stone")
        schematic[1, 0, 1] = BlockState.parse("minecraft:oak_stairs[facing=north]")
        self.assertEqual(schematic.get_block(0, 1, 0), "minecraft:stone")
        self.assertEqual(
            schematic[1, 0, 1], BlockState.parse("minecraft:oak_stairs[facing=north]")
        )
        counts = schematic.histogram()
        self.assertEqual(counts[BlockState("minecraft:air")], 6)

    def test_out_of_bounds_is_an_index_error(self):
        schematic = Schematic.new(2, 2, 2, DATA_VERSION)
        for position in ((2, 0, 0), (0, -1, 0), (0, 0, 5)):
            with self.assertRaises(IndexError):
                schematic.get_block(*position)

    def test_refuses_a_pre_118_data_version(self):
        with self.assertRaises(NBTError) as ctx:
            Schematic(1, 1, 1, 2824)
        self.assertIn("predates", str(ctx.exception))

    def test_refuses_impossible_sizes(self):
        for width in (0, -3, 0x10000):
            with self.assertRaises(NBTError):
                Schematic(width, 1, 1, DATA_VERSION)

    def test_block_entity_needs_an_id(self):
        schematic = Schematic.new(2, 2, 2, DATA_VERSION)
        with self.assertRaises(NBTError):
            schematic.set_block_entity(0, 0, 0, TagCompound({}))
        schematic.set_block_entity(
            0, 0, 0, TagCompound({"id": TagString("minecraft:chest")})
        )
        self.assertIsNotNone(schematic.get_block_entity(0, 0, 0))
        schematic.set_block_entity(0, 0, 0, None)
        self.assertIsNone(schematic.get_block_entity(0, 0, 0))

    def test_indices_is_the_bulk_read_contract(self):
        schematic = Schematic.new(2, 3, 4, DATA_VERSION)
        schematic.set_block(1, 2, 3, "minecraft:stone")
        indices = schematic.indices()
        self.assertEqual(len(indices), schematic.volume)
        position = (2 * schematic.length + 3) * schematic.width + 1
        self.assertEqual(schematic.palette[indices[position]],
                         BlockState("minecraft:stone"))
        self.assertEqual(indices.count(0), schematic.volume - 1, "rest is air")

    def test_first_set_biome_fills_the_box(self):
        schematic = Schematic.new(2, 2, 2, DATA_VERSION)
        self.assertFalse(schematic.has_biomes)
        self.assertIsNone(schematic.get_biome(0, 0, 0))
        schematic.set_biome(0, 0, 0, "minecraft:desert")
        self.assertEqual(schematic.get_biome(1, 1, 1), "minecraft:desert")
        schematic.set_biome(1, 1, 1, "minecraft:plains")
        self.assertEqual(schematic.get_biome(1, 1, 1), "minecraft:plains")
        self.assertEqual(schematic.get_biome(0, 0, 0), "minecraft:desert")


# --------------------------------------------------------------------------
# The file format
# --------------------------------------------------------------------------


class FileFormatTests(SequenceAssertions):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="schem-format-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def path(self, name="a.schem"):
        return os.path.join(self.dir, name)

    def big_palette_schematic(self) -> Schematic:
        """More than 127 distinct states, so varints need two bytes."""
        schematic = Schematic.new(10, 15, 3, DATA_VERSION)
        for i in range(schematic.volume):
            schematic._blocks[i] = schematic._intern(
                BlockState(f"minecraft:fake_{i % 300}")
            )
        return schematic

    def test_round_trip_both_versions(self):
        original = self.big_palette_schematic()
        original.offset = (-7, 3, 12)
        original.set_block_entity(
            1, 2, 1,
            TagCompound(
                {
                    "id": TagString("minecraft:chest"),
                    "Items": TagList(element_type=nbt.TAG_COMPOUND),
                    "CustomName": TagString("loot"),
                }
            ),
        )
        original.entities.append(
            make_entity("minecraft:pig", (1.5, 0.0, 2.5), [1, 2, 3, 4])
        )
        for version in (2, 3):
            with self.subTest(version=version):
                self.path_v = self.path(f"v{version}.schem")
                original.save(self.path_v, version=version)
                loaded = Schematic.load(self.path_v)
                self.assertEqual(loaded.source_version, version)
                self.assertEqual(loaded.size, original.size)
                self.assertEqual(loaded.data_version, DATA_VERSION)
                self.assertEqual(loaded.offset, (-7, 3, 12))
                self.assertSequencesEqual(
                    [loaded.get_block(x, y, z) for x, y, z in self.walk(loaded)],
                    [original.get_block(x, y, z) for x, y, z in self.walk(original)],
                )
                chest = loaded.get_block_entity(1, 2, 1)
                self.assertEqual(str(chest["id"]), "minecraft:chest")
                self.assertEqual(str(chest["CustomName"]), "loot")
                self.assertEqual(len(loaded.entities), 1)
                entity = loaded.entities[0]
                self.assertEqual(str(entity["id"]), "minecraft:pig")
                self.assertEqual(
                    [float(v) for v in entity["Pos"]], [1.5, 0.0, 2.5]
                )
                self.assertEqual(list(entity["UUID"]), [1, 2, 3, 4])

    @staticmethod
    def walk(schematic):
        width, height, length = schematic.size
        return (
            (x, y, z)
            for y in range(height)
            for z in range(length)
            for x in range(width)
        )

    def test_writer_is_deterministic_and_stable_through_a_reload(self):
        original = self.big_palette_schematic()
        original.save(self.path("one.schem"))
        original.save(self.path("two.schem"))
        with open(self.path("one.schem"), "rb") as a, open(
            self.path("two.schem"), "rb"
        ) as b:
            first, second = a.read(), b.read()
        self.assertEqual(first, second)
        Schematic.load(self.path("one.schem")).save(self.path("three.schem"))
        with open(self.path("three.schem"), "rb") as c:
            self.assertEqual(first, c.read())

    def test_v3_layout_matches_the_spec(self):
        schematic = Schematic.new(2, 2, 2, DATA_VERSION)
        schematic.set_biome(0, 0, 0, "minecraft:desert")
        schematic.save(self.path())
        with open(self.path(), "rb") as handle:
            name, root = nbt.decode(gzip.decompress(handle.read()))
        self.assertEqual(name, "")
        body = root["Schematic"]
        self.assertEqual(int(body["Version"]), 3)
        self.assertIn("Palette", body["Blocks"])
        self.assertIn("Data", body["Blocks"])
        self.assertIn("Biomes", body)
        self.assertEqual(list(body["Offset"]), [0, 0, 0])

    def test_v2_layout_keeps_worldedit_conventions(self):
        schematic = Schematic.new(2, 2, 2, DATA_VERSION)
        schematic.offset = (3, -1, 4)
        schematic.save(self.path(), version=2)
        with open(self.path(), "rb") as handle:
            name, root = nbt.decode(gzip.decompress(handle.read()))
        self.assertEqual(name, "Schematic")
        self.assertEqual(int(root["Version"]), 2)
        self.assertEqual(int(root["PaletteMax"]), len(root["Palette"]))
        metadata = root["Metadata"]
        self.assertEqual(
            [int(metadata[f"WEOffset{axis}"]) for axis in "XYZ"], [3, -1, 4]
        )
        loaded = Schematic.load(self.path())
        self.assertEqual(loaded.offset, (3, -1, 4))
        self.assertIsNone(loaded.metadata, "WEOffset alone is not metadata")

    def test_v2_cannot_hold_biomes(self):
        schematic = Schematic.new(2, 2, 2, DATA_VERSION)
        schematic.set_biome(0, 0, 0, "minecraft:desert")
        with self.assertRaises(NBTError) as ctx:
            schematic.save(self.path(), version=2)
        self.assertIn("version 2", str(ctx.exception))

    def test_v2_column_biome_data_is_noted_not_guessed_at(self):
        root = TagCompound(
            {
                "Version": TagInt(2),
                "DataVersion": TagInt(DATA_VERSION),
                "Width": TagShort(1),
                "Height": TagShort(1),
                "Length": TagShort(1),
                "Palette": TagCompound({"minecraft:air": TagInt(0)}),
                "BlockData": TagByteArray([0]),
                "BiomeData": TagByteArray([0]),
                "BiomePalette": TagCompound({"minecraft:plains": TagInt(0)}),
            }
        )
        write_raw_schem(self.path(), root, "Schematic")
        loaded = Schematic.load(self.path())
        self.assertTrue(loaded.had_2d_biomes)
        self.assertFalse(loaded.has_biomes)

    def test_refuses_the_legacy_schematic_format(self):
        root = TagCompound(
            {
                "Width": TagShort(1),
                "Height": TagShort(1),
                "Length": TagShort(1),
                "Materials": TagString("Alpha"),
                "Blocks": TagByteArray([1]),
                "Data": TagByteArray([0]),
            }
        )
        write_raw_schem(self.path(), root, "Schematic")
        with self.assertRaises(NBTError) as ctx:
            Schematic.load(self.path())
        self.assertIn("pre-1.13", str(ctx.exception))

    def test_refuses_sponge_version_1_and_unknown_versions(self):
        for version, fragment in ((1, "version 1"), (9, "only 2 and 3")):
            with self.subTest(version=version):
                root = TagCompound({"Version": TagInt(version)})
                write_raw_schem(self.path(), root, "Schematic")
                with self.assertRaises(NBTError) as ctx:
                    Schematic.load(self.path())
                self.assertIn(fragment, str(ctx.exception))

    def test_refuses_a_missing_or_ancient_data_version(self):
        without = TagCompound(
            {
                "Version": TagInt(2),
                "Width": TagShort(1),
                "Height": TagShort(1),
                "Length": TagShort(1),
            }
        )
        write_raw_schem(self.path(), without, "Schematic")
        with self.assertRaises(NBTError) as ctx:
            Schematic.load(self.path())
        self.assertIn("DataVersion", str(ctx.exception))

        ancient = TagCompound(dict(without, DataVersion=TagInt(2824)))
        write_raw_schem(self.path(), ancient, "Schematic")
        with self.assertRaises(NBTError) as ctx:
            Schematic.load(self.path())
        self.assertIn("predates", str(ctx.exception))

    def test_refuses_a_file_that_is_not_nbt_at_all(self):
        with open(self.path(), "wb") as handle:
            handle.write(b"not a schematic")
        with self.assertRaises(NBTError):
            Schematic.load(self.path())

    def test_refuses_corrupt_block_data(self):
        base = {
            "Version": TagInt(2),
            "DataVersion": TagInt(DATA_VERSION),
            "Width": TagShort(2),
            "Height": TagShort(1),
            "Length": TagShort(1),
        }
        cases = {
            "count": TagCompound(
                dict(
                    base,
                    Palette=TagCompound({"minecraft:air": TagInt(0)}),
                    BlockData=TagByteArray([0]),  # 1 entry for 2 cells
                )
            ),
            "reference": TagCompound(
                dict(
                    base,
                    Palette=TagCompound({"minecraft:air": TagInt(0)}),
                    BlockData=TagByteArray([0, 5]),  # index 5 of a 1-palette
                )
            ),
            "sparse palette": TagCompound(
                dict(
                    base,
                    Palette=TagCompound({"minecraft:air": TagInt(3)}),
                    BlockData=TagByteArray([0, 0]),
                )
            ),
        }
        for label, root in cases.items():
            with self.subTest(label):
                write_raw_schem(self.path(), root, "Schematic")
                with self.assertRaises(NBTError):
                    Schematic.load(self.path())


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


class ExportTests(TempSaveBase):
    def test_export_matches_the_world_block_for_block(self):
        self.save.fill((-5, 60, -5), (5, 64, 5), "minecraft:stone_bricks")
        self.save.fill((-2, 61, -2), (2, 63, 2), "minecraft:water[level=0]")
        result = self.save.export_schem((-5, 60, -5), (5, 64, 5), self.schem_path())
        self.assertTrue(result.complete)
        self.assertEqual(result.blocks_exported, 11 * 5 * 11)
        loaded = Schematic.load(self.schem_path())
        self.assertEqual(loaded.size, (11, 5, 11))
        self.assertEqual(loaded.data_version, DATA_VERSION)
        self.assertSequencesEqual(
            [
                loaded.get_block(x, y, z)
                for y in range(5)
                for z in range(11)
                for x in range(11)
            ],
            self.box_states(self.save, (-5, 60, -5), (5, 64, 5)),
        )

    def test_corners_in_any_order(self):
        result = self.save.export_schem((5, 64, 5), (-5, 60, -5), self.schem_path())
        self.assertEqual(result.schematic.size, (11, 5, 11))

    def test_origin_is_recorded_as_the_offset(self):
        result = self.save.export_schem(
            (0, 60, 0), (4, 62, 4), self.schem_path(), origin=(2, 60, 7)
        )
        self.assertEqual(result.schematic.offset, (-2, 0, -7))
        self.assertEqual(Schematic.load(self.schem_path()).offset, (-2, 0, -7))

    def test_refuses_ungenerated_chunks_by_default(self):
        with self.assertRaises(NBTError) as ctx:
            self.save.export_schem((0, 60, 0), (200, 62, 4), self.schem_path())
        self.assertIn("never been generated", str(ctx.exception))
        self.assertFalse(os.path.exists(self.schem_path()), "must not half-write")

    def test_allow_incomplete_air_fills_and_itemises(self):
        result = self.save.export_schem(
            (10, 60, 0), (40, 60, 0), self.schem_path(), allow_incomplete=True
        )
        self.assertFalse(result.complete)
        self.assertGreater(len(result.missing_chunks), 0)
        self.assertGreater(result.blocks_out_of_range, 0)
        loaded = Schematic.load(self.schem_path())
        self.assertEqual(loaded.get_block(30, 0, 0), "minecraft:air")

    def test_above_the_world_is_air_and_counted_but_not_fatal(self):
        result = self.save.export_schem((0, 315, 0), (0, 324, 0), self.schem_path())
        self.assertEqual(result.blocks_exported, 5)  # 315..319 exist
        self.assertEqual(result.blocks_out_of_range, 5)  # 320..324 do not
        self.assertFalse(result.complete)
        loaded = Schematic.load(self.schem_path())
        self.assertEqual(loaded.get_block(0, 9, 0), "minecraft:air")

    def test_refuses_unreadable_chunks_by_default(self):
        region_path = os.path.join(
            self.dir, "dimensions", "minecraft", "overworld", "region", "r.0.0.mca"
        )
        region = NBT.load(region_path)
        region.chunks[(0, 0)].data["DataVersion"] = TagInt(100)
        region.save()
        fresh = MinecraftSave.load(self.dir)
        with self.assertRaises(NBTError) as ctx:
            fresh.export_schem((0, 60, 0), (4, 62, 4), self.schem_path())
        self.assertIn("cannot be read", str(ctx.exception))
        result = fresh.export_schem(
            (0, 60, 0), (4, 62, 4), self.schem_path(), allow_incomplete=True
        )
        self.assertEqual(len(result.unreadable_chunks), 1)

    def test_from_save_returns_just_the_schematic(self):
        schematic = Schematic.from_save(self.save, (0, 60, 0), (2, 62, 2))
        self.assertIsInstance(schematic, Schematic)
        self.assertEqual(schematic.size, (3, 3, 3))


class FixtureExportTests(TempSaveBase):
    """Real game output: the vendored chunk at (-28, 14) holds a smoker and a
    campfire, block entities written by Minecraft itself."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="schem-fixture-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        region_dir = os.path.join(
            self.dir, "dimensions", "minecraft", "overworld", "region"
        )
        os.makedirs(region_dir)
        shutil.copyfile(FIXTURE_REGION, os.path.join(region_dir, "r.-1.0.mca"))
        shutil.copyfile(FIXTURE_LEVEL_DAT, os.path.join(self.dir, "level.dat"))
        self.save = MinecraftSave.load(self.dir, dimensions=[OVERWORLD])

    def test_block_entities_ride_along_with_real_data(self):
        box = ((-448, 60, 224), (-441, 70, 231))
        result = self.save.export_schem(*box, self.schem_path())
        self.assertTrue(result.complete)
        self.assertEqual(result.block_entities_exported, 2)
        loaded = Schematic.load(self.schem_path())
        smoker = loaded.get_block_entity(-446 - -448, 64 - 60, 225 - 224)
        self.assertEqual(str(smoker["id"]), "minecraft:smoker")
        self.assertIn("RecipesUsed", smoker)
        self.assertNotIn("x", smoker, "position belongs to the paste, not the file")
        self.assertNotIn("keepPacked", smoker)
        campfire = loaded.get_block_entity(2, 7, 1)
        self.assertEqual(str(campfire["id"]), "minecraft:campfire")
        self.assertEqual(len(campfire["CookingTimes"]), 4)

    def test_real_block_entities_survive_a_paste(self):
        box = ((-448, 60, 224), (-441, 70, 231))
        schematic = Schematic.from_save(self.save, *box)

        destination = tempfile.mkdtemp(prefix="schem-dest-")
        self.addCleanup(shutil.rmtree, destination, ignore_errors=True)
        make_save(destination, CHUNKS_3X3)
        target = MinecraftSave.load(destination)
        result = target.import_schem(schematic, (4, 80, 4), anchor="min")
        self.assertTrue(result.complete)
        self.assertEqual(result.block_entities_added, 2)

        chunk = target.dimension(OVERWORLD).chunk(0, 0)
        stored = {
            str(entry["id"]): entry for entry in chunk.data["block_entities"]
        }
        smoker = stored["minecraft:smoker"]
        self.assertEqual(
            (int(smoker["x"]), int(smoker["y"]), int(smoker["z"])), (6, 84, 5)
        )
        self.assertEqual(int(smoker["keepPacked"]), 0)
        self.assertEqual(
            target.get_block(6, 84, 5).name, "minecraft:smoker"
        )


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


class ImportTests(TempSaveBase):
    def checkerboard(self, width=6, height=4, length=6) -> Schematic:
        schematic = Schematic.new(width, height, length, DATA_VERSION)
        for y in range(height):
            for z in range(length):
                for x in range(width):
                    if (x + y + z) % 2 == 0:
                        schematic.set_block(x, y, z, "minecraft:stone")
        return schematic

    def test_anchor_min_places_the_minimum_corner_exactly(self):
        schematic = self.checkerboard()
        schematic.offset = (100, 100, 100)  # must be ignored
        result = self.save.import_schem(schematic, (-3, 70, -3), anchor="min")
        self.assertEqual(result.placed_box, ((-3, 70, -3), (2, 73, 2)))
        self.assertEqual(self.save.get_block(-3, 70, -3), "minecraft:stone")
        self.assertEqual(self.save.get_block(-2, 70, -3), "minecraft:air")

    def test_anchor_origin_applies_the_stored_offset(self):
        schematic = self.checkerboard()
        schematic.offset = (2, 0, 3)
        result = self.save.import_schem(schematic, (0, 70, 0))
        self.assertEqual(result.placed_box, ((2, 70, 3), (7, 73, 8)))

    def test_unknown_anchor_is_rejected(self):
        with self.assertRaises(ValueError):
            self.save.import_schem(self.checkerboard(), (0, 70, 0), anchor="corner")

    def test_export_import_at_the_same_origin_is_identity(self):
        self.save.fill((-6, 60, -6), (6, 68, 6), "minecraft:mossy_cobblestone")
        origin = (1, 61, -2)
        self.save.export_schem(
            (-6, 60, -6), (6, 68, 6), self.schem_path(), origin=origin
        )
        other_dir = tempfile.mkdtemp(prefix="schem-identity-")
        self.addCleanup(shutil.rmtree, other_dir, ignore_errors=True)
        make_save(other_dir, self.CHUNKS)
        other = MinecraftSave.load(other_dir)
        result = other.import_schem(self.schem_path(), origin)
        self.assertEqual(result.placed_box, ((-6, 60, -6), (6, 68, 6)))
        self.assertSequencesEqual(
            self.box_states(other, (-6, 60, -6), (6, 68, 6)),
            self.box_states(self.save, (-6, 60, -6), (6, 68, 6)),
        )

    def test_paste_air_false_lets_terrain_show_through(self):
        self.save.fill((0, 70, 0), (5, 70, 5), "minecraft:grass_block")
        schematic = self.checkerboard(6, 1, 6)  # stone/air checkerboard
        result = self.save.import_schem(
            schematic, (0, 70, 0), anchor="min", paste_air=False
        )
        self.assertEqual(result.air_skipped, 18)
        self.assertEqual(result.blocks_pasted, 18)
        self.assertEqual(self.save.get_block(0, 70, 0), "minecraft:stone")
        self.assertEqual(self.save.get_block(1, 70, 0), "minecraft:grass_block")

    def test_paste_air_true_reproduces_the_box_exactly(self):
        self.save.fill((0, 70, 0), (5, 70, 5), "minecraft:grass_block")
        result = self.save.import_schem(
            self.checkerboard(6, 1, 6), (0, 70, 0), anchor="min"
        )
        self.assertEqual(result.blocks_pasted, 36)
        self.assertEqual(self.save.get_block(1, 70, 0), "minecraft:air")

    def test_a_full_aligned_section_takes_the_wholesale_path(self):
        schematic = self.checkerboard(16, 16, 16)
        result = self.save.import_schem(schematic, (0, 64, 0), anchor="min")
        self.assertEqual(result.sections_replaced, 1)
        self.assertEqual(result.blocks_pasted, 4096)
        self.assertEqual(self.save.get_block(0, 64, 0), "minecraft:stone")
        self.assertEqual(self.save.get_block(1, 64, 0), "minecraft:air")

    def test_stale_block_entities_are_removed_from_overwritten_positions(self):
        chunk = self.save.dimension(OVERWORLD).chunk(0, 0)
        chunk.data["block_entities"] = TagList(
            [
                TagCompound(
                    {
                        "id": TagString("minecraft:chest"),
                        "x": TagInt(1),
                        "y": TagInt(70),
                        "z": TagInt(1),
                        "keepPacked": nbt.TagByte(0),
                    }
                ),
                TagCompound(
                    {
                        "id": TagString("minecraft:chest"),
                        "x": TagInt(12),
                        "y": TagInt(70),
                        "z": TagInt(12),
                        "keepPacked": nbt.TagByte(0),
                    }
                ),
            ],
            element_type=nbt.TAG_COMPOUND,
        )
        schematic = Schematic.new(4, 1, 4, DATA_VERSION, fill="minecraft:stone")
        result = self.save.import_schem(schematic, (0, 70, 0), anchor="min")
        self.assertEqual(result.block_entities_removed, 1)
        remaining = chunk.data["block_entities"]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(int(remaining[0]["x"]), 12, "outside the box; must stay")

    def test_masked_paste_spares_block_entities_under_air(self):
        chunk = self.save.dimension(OVERWORLD).chunk(0, 0)
        chunk.data["block_entities"] = TagList(
            [
                TagCompound(
                    {
                        "id": TagString("minecraft:chest"),
                        "x": TagInt(1),
                        "y": TagInt(70),
                        "z": TagInt(0),
                        "keepPacked": nbt.TagByte(0),
                    }
                )
            ],
            element_type=nbt.TAG_COMPOUND,
        )
        schematic = self.checkerboard(6, 1, 6)  # air at (1, 0, 0)
        result = self.save.import_schem(
            schematic, (0, 70, 0), anchor="min", paste_air=False
        )
        self.assertEqual(result.block_entities_removed, 0)
        self.assertEqual(len(chunk.data["block_entities"]), 1)

    def test_block_entities_paste_with_contents(self):
        schematic = Schematic.new(2, 1, 2, DATA_VERSION, fill="minecraft:barrel")
        schematic.set_block_entity(
            1, 0, 1,
            TagCompound(
                {
                    "id": TagString("minecraft:barrel"),
                    "CustomName": TagString("supplies"),
                }
            ),
        )
        result = self.save.import_schem(schematic, (-9, 71, -9), anchor="min")
        self.assertEqual(result.block_entities_added, 1)
        chunk = self.save.dimension(OVERWORLD).chunk(-1, -1)
        entry = chunk.data["block_entities"][0]
        self.assertEqual(
            (int(entry["x"]), int(entry["y"]), int(entry["z"])), (-8, 71, -8)
        )
        self.assertEqual(str(entry["CustomName"]), "supplies")
        self.assertEqual(int(entry["keepPacked"]), 0)

    def test_missing_chunks_are_skipped_and_reported(self):
        schematic = Schematic.new(40, 1, 1, DATA_VERSION, fill="minecraft:stone")
        result = self.save.import_schem(schematic, (10, 70, 0), anchor="min")
        self.assertFalse(result.complete)
        self.assertGreater(len(result.missing_chunks), 0)
        self.assertGreater(result.blocks_out_of_range, 0)
        self.assertGreater(result.blocks_pasted, 0, "the generated part is written")

    def test_above_the_world_is_reported_not_written(self):
        schematic = Schematic.new(1, 10, 1, DATA_VERSION, fill="minecraft:stone")
        result = self.save.import_schem(schematic, (0, 315, 0), anchor="min")
        self.assertEqual(result.blocks_pasted, 5)
        self.assertEqual(result.blocks_out_of_range, 5)
        self.assertFalse(result.complete)

    def test_refuses_content_from_a_newer_minecraft(self):
        schematic = self.checkerboard()
        schematic.data_version = DATA_VERSION + 1
        with self.assertRaises(NBTError) as ctx:
            self.save.import_schem(schematic, (0, 70, 0))
        self.assertIn("newer Minecraft", str(ctx.exception))
        # The documented escape hatch: claim the world's version, knowingly.
        schematic.data_version = DATA_VERSION
        self.save.import_schem(schematic, (0, 70, 0))

    def test_relight_hands_the_pasted_area_back_to_the_game(self):
        result = self.save.import_schem(
            self.checkerboard(), (0, 70, 0), anchor="min"
        )
        self.assertIsNotNone(result.lighting)
        self.assertGreater(result.lighting.chunks_reset, 0)
        chunk = self.save.dimension(OVERWORLD).chunk(0, 0)
        self.assertEqual(int(chunk.data["isLightOn"]), 0)

    def test_relight_can_be_declined(self):
        result = self.save.import_schem(
            self.checkerboard(), (0, 70, 0), anchor="min", relight=False
        )
        self.assertIsNone(result.lighting)
        chunk = self.save.dimension(OVERWORLD).chunk(0, 0)
        self.assertNotIn("isLightOn", chunk.data)

    def test_import_accepts_a_path_or_a_schematic(self):
        self.checkerboard().save(self.schem_path())
        by_path = self.save.import_schem(self.schem_path(), (0, 70, 0), anchor="min")
        self.assertGreater(by_path.blocks_pasted, 0)

    def test_changes_reach_disk_only_through_save(self):
        self.save.import_schem(self.checkerboard(), (0, 70, 0), anchor="min")
        fresh = MinecraftSave.load(self.dir)
        self.assertEqual(fresh.get_block(0, 70, 0), "minecraft:air")
        written = self.save.save()
        self.assertGreater(written, 0)
        fresh = MinecraftSave.load(self.dir)
        self.assertEqual(fresh.get_block(0, 70, 0), "minecraft:stone")


# --------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------

FRAME_UUID = [11, 12, 13, 14]
PIG_UUID = [21, 22, 23, 24]
CHICKEN_UUID = [31, 32, 33, 34]
OUTSIDE_UUID = [41, 42, 43, 44]


class EntityTests(TempSaveBase):
    def setUp(self):
        super().setUp()
        frame = make_entity(
            "minecraft:item_frame",
            (4.5, 65.0, 4.96875),
            FRAME_UUID,
            TileX=TagInt(4),
            TileY=TagInt(65),
            TileZ=TagInt(5),
        )
        pig = make_entity(
            "minecraft:pig",
            (2.25, 64.0, 3.75),
            PIG_UUID,
            leash=TagCompound({"UUID": TagIntArray(FRAME_UUID)}),
            Passengers=TagList(
                # 64.875 is exactly representable, so relative arithmetic
                # stays byte-for-byte comparable in the assertions below.
                [make_entity("minecraft:chicken", (2.25, 64.875, 3.75), CHICKEN_UUID)],
                element_type=nbt.TAG_COMPOUND,
            ),
        )
        outside = make_entity("minecraft:cow", (40.5, 64.0, 40.5), OUTSIDE_UUID)
        install_entities(
            self.dir, {(0, 0): [frame, pig], (2, 2): [outside]}
        )
        self.save = MinecraftSave.load(self.dir)
        self.box = ((0, 60, 0), (8, 70, 8))

    def test_entities_stay_behind_unless_asked_for(self):
        result = self.save.export_schem(*self.box, self.schem_path())
        self.assertEqual(result.entities_exported, 0)
        self.assertEqual(Schematic.load(self.schem_path()).entities, [])

    def test_export_makes_positions_relative_and_keeps_identity(self):
        result = self.save.export_schem(*self.box, self.schem_path(), entities=True)
        self.assertEqual(result.entities_exported, 2, "the cow is outside the box")
        loaded = Schematic.load(self.schem_path())
        by_id = {str(e["id"]): e for e in loaded.entities}
        frame = by_id["minecraft:item_frame"]
        self.assertEqual([float(v) for v in frame["Pos"]], [4.5, 5.0, 4.96875])
        self.assertEqual(
            (int(frame["TileX"]), int(frame["TileY"]), int(frame["TileZ"])),
            (4, 5, 5),
        )
        self.assertEqual(list(frame["UUID"]), FRAME_UUID, "the file keeps identity")
        pig = by_id["minecraft:pig"]
        passenger = pig["Passengers"][0]
        self.assertEqual(
            [float(v) for v in passenger["Pos"]], [2.25, 4.875, 3.75]
        )

    def test_paste_rerolls_identity_but_keeps_relationships(self):
        schematic = Schematic.from_save(self.save, *self.box, entities=True)
        # (16, 100, 16) keeps every entity inside generated chunk (1, 1).
        result = self.save.import_schem(schematic, (16, 100, 16), anchor="min")
        self.assertEqual(result.entities_added, 2)
        self.assertTrue(result.complete)

        chunk = self.save.dimension(OVERWORLD).entity_chunk(1, 1)
        by_id = {str(e["id"]): e for e in chunk.data["Entities"]}
        frame, pig = by_id["minecraft:item_frame"], by_id["minecraft:pig"]

        self.assertEqual([float(v) for v in frame["Pos"]], [20.5, 105.0, 20.96875])
        self.assertEqual(
            (int(frame["TileX"]), int(frame["TileY"]), int(frame["TileZ"])),
            (20, 105, 21),
        )
        self.assertNotEqual(list(frame["UUID"]), FRAME_UUID)
        self.assertNotEqual(list(pig["UUID"]), PIG_UUID)
        self.assertEqual(
            list(pig["leash"]["UUID"]),
            list(frame["UUID"]),
            "the pasted leash must point at the pasted frame",
        )
        passenger = pig["Passengers"][0]
        self.assertNotEqual(list(passenger["UUID"]), CHICKEN_UUID)
        self.assertEqual([float(v) for v in passenger["Pos"]], [18.25, 104.875, 19.75])

    def test_pasting_twice_never_duplicates_an_identity(self):
        schematic = Schematic.from_save(self.save, *self.box, entities=True)
        self.save.import_schem(schematic, (16, 100, 16), anchor="min")
        self.save.import_schem(schematic, (16, 100, 16), anchor="min")
        chunk = self.save.dimension(OVERWORLD).entity_chunk(1, 1)
        uuids = [tuple(e["UUID"]) for e in chunk.data["Entities"]]
        self.assertEqual(len(uuids), 4)
        self.assertEqual(len(set(uuids)), 4)
        self.assertNotIn(tuple(FRAME_UUID), uuids)

    def test_entities_can_be_left_behind_on_paste(self):
        schematic = Schematic.from_save(self.save, *self.box, entities=True)
        result = self.save.import_schem(
            schematic, (100, 100, 100), anchor="min", entities=False
        )
        self.assertEqual(result.entities_added, 0)
        self.assertIsNone(
            self.save.dimension(OVERWORLD).entity_chunk(6, 6)
        )

    def test_an_entity_with_no_ground_is_skipped_and_named(self):
        schematic = Schematic.from_save(self.save, *self.box, entities=True)
        result = self.save.import_schem(schematic, (500, 100, 500), anchor="min")
        self.assertEqual(result.entities_added, 0)
        self.assertEqual(len(result.entities_skipped), 2)
        self.assertFalse(result.complete)
        identities = {identity for identity, _ in result.entities_skipped}
        self.assertEqual(identities, {"minecraft:item_frame", "minecraft:pig"})

    def test_pasted_entities_reach_disk_and_reload(self):
        schematic = Schematic.from_save(self.save, *self.box, entities=True)
        # (-16, ...) puts both entities in chunk (-1, 0) -> region r.-1.0: an
        # entity region file that does not exist yet, so save must create it.
        result = self.save.import_schem(schematic, (-16, 90, 4), anchor="min")
        self.assertEqual(result.entities_added, 2)
        written = self.save.save()
        self.assertGreater(written, 0)
        entities_file = os.path.join(
            self.dir, "dimensions", "minecraft", "overworld", "entities", "r.-1.0.mca"
        )
        self.assertTrue(os.path.exists(entities_file))
        fresh = MinecraftSave.load(self.dir)
        chunk = fresh.dimension(OVERWORLD).entity_chunk(-1, 0)
        self.assertEqual(len(chunk.data["Entities"]), 2)
        self.assertEqual(int(chunk.data["DataVersion"]), DATA_VERSION)
        self.assertEqual(list(chunk.data["Position"]), [-1, 0])

    def test_block_pos_anchors_shift_on_a_plain_paste(self):
        """Current-format hangings store ``block_pos``, not TileX/Y/Z; a
        paste must translate it or every frame pops off its wall on load."""
        painting = make_entity(
            "minecraft:painting",
            (4.0, 65.5, 4.96875),
            [51, 52, 53, 54],
            block_pos=TagIntArray([4, 65, 4]),
            facing=nbt.TagByte(2),
        )
        install_entities(self.dir, {(0, 0): [painting]})
        fresh = MinecraftSave.load(self.dir)
        schematic = Schematic.from_save(fresh, *self.box, entities=True)
        stored = next(
            e for e in schematic.entities if str(e["id"]) == "minecraft:painting"
        )
        self.assertEqual(list(stored["block_pos"]), [4, 5, 4], "relative in the file")
        fresh.import_schem(schematic, (16, 100, 16), anchor="min")
        chunk = fresh.dimension(OVERWORLD).entity_chunk(1, 1)
        pasted = next(
            e for e in chunk.data["Entities"] if str(e["id"]) == "minecraft:painting"
        )
        self.assertEqual(list(pasted["block_pos"]), [20, 105, 20])
        self.assertEqual(int(pasted["facing"]), 2, "no transform asked for")

    def test_v2_files_carry_entities_too(self):
        schematic = Schematic.from_save(self.save, *self.box, entities=True)
        schematic.save(self.schem_path(), version=2)
        loaded = Schematic.load(self.schem_path())
        self.assertEqual(len(loaded.entities), 2)
        by_id = {str(e["id"]): e for e in loaded.entities}
        self.assertEqual(
            [float(v) for v in by_id["minecraft:item_frame"]["Pos"]],
            [4.5, 5.0, 4.96875],
        )


# --------------------------------------------------------------------------
# Rotation and mirroring
# --------------------------------------------------------------------------


class TransformTests(SequenceAssertions):
    def numbered(self, width=2, height=3, length=4) -> Schematic:
        """Every cell a distinct state, so any misplaced cell is visible."""
        schematic = Schematic.new(width, height, length, DATA_VERSION)
        i = 0
        for y in range(height):
            for z in range(length):
                for x in range(width):
                    schematic.set_block(x, y, z, f"minecraft:cell_{i}")
                    i += 1
        return schematic

    def test_rotated_geometry(self):
        schematic = self.numbered()
        turned = schematic.rotated(90)
        self.assertEqual(turned.size, (4, 3, 2))
        width, height, length = schematic.size
        for y in range(height):
            for z in range(length):
                for x in range(width):
                    self.assertEqual(
                        turned.get_block(length - 1 - z, y, x),
                        schematic.get_block(x, y, z),
                    )

    def test_four_quarter_turns_are_identity(self):
        schematic = self.numbered()
        schematic.offset = (5, -2, 7)
        schematic.set_block_entity(
            1, 0, 3, TagCompound({"id": TagString("minecraft:chest")})
        )
        schematic.entities.append(
            make_entity("minecraft:pig", (1.25, 0.0, 3.75), [1, 2, 3, 4])
        )
        turned = schematic
        for _ in range(4):
            turned = turned.rotated(90)
        self.assertEqual(turned.size, schematic.size)
        self.assertEqual(turned.offset, schematic.offset)
        self.assertSequencesEqual(
            [str(s) for s in turned._palette], [str(s) for s in schematic._palette]
        )
        self.assertSequencesEqual(turned._blocks, schematic._blocks)
        self.assertEqual(set(turned.block_entities), {(1, 0, 3)})
        self.assertEqual(
            [float(v) for v in turned.entities[0]["Pos"]], [1.25, 0.0, 3.75]
        )

    def test_rotations_compose(self):
        schematic = self.numbered()
        once_twice = schematic.rotated(90).rotated(90)
        at_180 = schematic.rotated(180)
        self.assertSequencesEqual(once_twice._blocks, at_180._blocks)
        self.assertEqual(once_twice.offset, at_180.offset)
        self.assertSequencesEqual(
            schematic.rotated(-90)._blocks, schematic.rotated(270)._blocks
        )

    def test_flipping_twice_is_identity(self):
        schematic = self.numbered()
        schematic.offset = (3, 1, -4)
        for axis in ("x", "y", "z"):
            with self.subTest(axis=axis):
                twice = schematic.flipped(axis).flipped(axis)
                self.assertSequencesEqual(twice._blocks, schematic._blocks)
                self.assertEqual(twice.offset, schematic.offset)

    def test_rejects_non_quarter_rotations_and_bad_axes(self):
        schematic = self.numbered()
        with self.assertRaises(NBTError) as ctx:
            schematic.rotated(45)
        self.assertIn("multiple of 90", str(ctx.exception))
        with self.assertRaises(NBTError):
            schematic.flipped("w")

    def test_direction_bearing_properties_rotate(self):
        cases = {
            # state -> after one clockwise quarter turn
            "minecraft:oak_stairs[facing=north,half=bottom,shape=inner_left]":
                "minecraft:oak_stairs[facing=east,half=bottom,shape=inner_left]",
            "minecraft:oak_log[axis=x]": "minecraft:oak_log[axis=z]",
            "minecraft:oak_log[axis=y]": "minecraft:oak_log[axis=y]",
            "minecraft:oak_sign[rotation=3]": "minecraft:oak_sign[rotation=7]",
            "minecraft:rail[shape=north_east]": "minecraft:rail[shape=south_east]",
            "minecraft:rail[shape=east_west]": "minecraft:rail[shape=north_south]",
            "minecraft:powered_rail[shape=ascending_north]":
                "minecraft:powered_rail[shape=ascending_east]",
            "minecraft:cobblestone_wall[east=low,north=tall,south=none,up=true,west=none]":
                "minecraft:cobblestone_wall[east=tall,north=none,south=low,up=true,west=none]",
            "minecraft:hopper[enabled=true,facing=down]":
                "minecraft:hopper[enabled=true,facing=down]",
            "minecraft:crafter[orientation=north_up]":
                "minecraft:crafter[orientation=east_up]",
            "minecraft:chest[facing=west,type=left]":
                "minecraft:chest[facing=north,type=left]",
        }
        for source, expected in cases.items():
            with self.subTest(source):
                schematic = Schematic.new(1, 1, 1, DATA_VERSION, fill=source)
                self.assertEqual(
                    str(schematic.rotated(90).get_block(0, 0, 0)), expected
                )

    def test_direction_bearing_properties_mirror(self):
        cases = {
            "x": {
                "minecraft:oak_stairs[facing=east,shape=outer_left]":
                    "minecraft:oak_stairs[facing=west,shape=outer_right]",
                "minecraft:oak_stairs[facing=north,shape=straight]":
                    "minecraft:oak_stairs[facing=north,shape=straight]",
                "minecraft:oak_sign[rotation=4]": "minecraft:oak_sign[rotation=12]",
                "minecraft:chest[facing=north,type=left]":
                    "minecraft:chest[facing=north,type=right]",
                "minecraft:oak_door[facing=east,hinge=left]":
                    "minecraft:oak_door[facing=west,hinge=right]",
                "minecraft:rail[shape=south_east]": "minecraft:rail[shape=south_west]",
                "minecraft:vine[east=true,north=false,south=false,up=false,west=false]":
                    "minecraft:vine[east=false,north=false,south=false,up=false,west=true]",
            },
            "z": {
                "minecraft:oak_stairs[facing=north,shape=inner_left]":
                    "minecraft:oak_stairs[facing=south,shape=inner_right]",
                "minecraft:oak_sign[rotation=0]": "minecraft:oak_sign[rotation=8]",
                "minecraft:powered_rail[shape=ascending_north]":
                    "minecraft:powered_rail[shape=ascending_south]",
                "minecraft:rail[shape=north_south]": "minecraft:rail[shape=north_south]",
            },
            "y": {
                "minecraft:oak_stairs[facing=north,half=bottom]":
                    "minecraft:oak_stairs[facing=north,half=top]",
                "minecraft:oak_slab[type=top]": "minecraft:oak_slab[type=bottom]",
                "minecraft:chest[facing=north,type=left]":
                    "minecraft:chest[facing=north,type=left]",
                "minecraft:lever[face=floor,facing=north]":
                    "minecraft:lever[face=ceiling,facing=north]",
                "minecraft:piston[extended=false,facing=up]":
                    "minecraft:piston[extended=false,facing=down]",
                "minecraft:lantern[hanging=true]": "minecraft:lantern[hanging=false]",
                "minecraft:oak_door[facing=east,half=upper,hinge=left]":
                    "minecraft:oak_door[facing=east,half=lower,hinge=left]",
                "minecraft:pointed_dripstone[vertical_direction=up]":
                    "minecraft:pointed_dripstone[vertical_direction=down]",
                "minecraft:oak_sign[rotation=5]": "minecraft:oak_sign[rotation=5]",
            },
        }
        for axis, table in cases.items():
            for source, expected in table.items():
                with self.subTest(axis=axis, state=source):
                    schematic = Schematic.new(1, 1, 1, DATA_VERSION, fill=source)
                    self.assertEqual(
                        str(schematic.flipped(axis).get_block(0, 0, 0)), expected
                    )

    def test_unknown_directional_properties_warn_not_guess(self):
        schematic = Schematic.new(1, 1, 1, DATA_VERSION)
        schematic.set_block(0, 0, 0, "minecraft:fake_block[widget=north]")
        turned = schematic.rotated(90)
        self.assertEqual(len(turned.transform_warnings), 1)
        self.assertIn("widget=north", turned.transform_warnings[0])
        self.assertEqual(
            str(turned.get_block(0, 0, 0)), "minecraft:fake_block[widget=north]"
        )
        self.assertEqual(schematic.rotated(0).transform_warnings, [])

    def test_entities_turn_with_the_box(self):
        schematic = Schematic.new(2, 1, 3, DATA_VERSION)
        frame = make_entity(
            "minecraft:item_frame",
            (0.5, 0.5, 0.03125),
            [1, 2, 3, 4],
            Facing=nbt.TagByte(2),  # north
            block_pos=TagIntArray([0, 0, 0]),
            Motion=TagList(
                [TagDouble(0.0), TagDouble(0.0), TagDouble(-1.0)],
                element_type=nbt.TAG_DOUBLE,
            ),
            Rotation=TagList(
                [nbt.TagFloat(180.0), nbt.TagFloat(10.0)], element_type=nbt.TAG_FLOAT
            ),
        )
        painting = make_entity(
            "minecraft:painting",
            (1.0, 0.5, 2.96875),
            [5, 6, 7, 8],
            facing=nbt.TagByte(2),  # north, the 2D order
            block_pos=TagIntArray([1, 0, 2]),
        )
        schematic.entities.extend([frame, painting])

        turned = schematic.rotated(90)  # length 3 becomes the new width
        frame2, painting2 = turned.entities
        self.assertEqual(
            [float(v) for v in frame2["Pos"]], [2.96875, 0.5, 0.5]
        )
        self.assertEqual(int(frame2["Facing"]), 5, "north (2) turns east (5)")
        self.assertEqual(list(frame2["block_pos"]), [2, 0, 0])
        self.assertEqual(float(frame2["Rotation"][0]), -90.0, "yaw 180 + 90, wrapped")
        self.assertEqual(float(frame2["Rotation"][1]), 10.0, "pitch untouched")
        self.assertEqual(
            [float(v) for v in frame2["Motion"]], [1.0, 0.0, 0.0],
            "motion is a direction and turns with the box",
        )
        self.assertEqual(int(painting2["facing"]), 3, "north (2) turns east (3) in 2D")
        self.assertEqual(list(painting2["block_pos"]), [0, 0, 1])

        mirrored = schematic.flipped("x")
        frame3 = mirrored.entities[0]
        self.assertEqual(int(frame3["Facing"]), 2, "north is untouched by an x flip")
        self.assertEqual([float(v) for v in frame3["Pos"]], [1.5, 0.5, 0.03125])
        upside_down = schematic.flipped("y")
        self.assertEqual(float(upside_down.entities[0]["Rotation"][1]), -10.0)

    def test_a_y_flip_still_wraps_the_stored_yaw(self):
        """Every transform writes yaw back wrapped, a y-flip included.

        It leaves the *facing* alone, but a stored yaw outside [-180, 180)
        comes back normalized rather than untouched -- which the conformance
        corpus once claimed otherwise, caught by the C# port disagreeing.
        """
        for stored, expected in ((350.0, -10.0), (-190.0, 170.0), (170.0, 170.0)):
            with self.subTest(yaw=stored):
                schematic = Schematic.new(2, 2, 2, DATA_VERSION)
                schematic.entities.append(make_entity(
                    "minecraft:pig",
                    (0.5, 0.5, 0.5),
                    [1, 2, 3, 4],
                    Rotation=TagList(
                        [nbt.TagFloat(stored), nbt.TagFloat(10.0)],
                        element_type=nbt.TAG_FLOAT,
                    ),
                ))
                flipped = schematic.flipped("y")
                self.assertEqual(
                    float(flipped.entities[0]["Rotation"][0]), expected
                )

    def test_biomes_and_block_entities_move_with_their_cells(self):
        schematic = Schematic.new(2, 1, 3, DATA_VERSION)
        schematic.fill_biomes("minecraft:plains")
        schematic.set_biome(0, 0, 2, "minecraft:desert")
        schematic.set_block(0, 0, 2, "minecraft:chest[facing=south,type=single]")
        schematic.set_block_entity(
            0, 0, 2, TagCompound({"id": TagString("minecraft:chest")})
        )
        turned = schematic.rotated(90)
        self.assertEqual(turned.get_biome(0, 0, 0), "minecraft:desert")
        self.assertEqual(turned.get_biome(1, 0, 0), "minecraft:plains")
        self.assertIsNotNone(turned.get_block_entity(0, 0, 0))
        self.assertEqual(
            str(turned.get_block(0, 0, 0)), "minecraft:chest[facing=west,type=single]"
        )

    def test_transformed_schematics_survive_the_file_format(self):
        schematic = self.numbered()
        turned = schematic.rotated(90)
        directory = tempfile.mkdtemp(prefix="schem-turn-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = os.path.join(directory, "turned.schem")
        turned.save(path)
        loaded = Schematic.load(path)
        self.assertEqual(loaded.size, turned.size)
        self.assertSequencesEqual(
            [str(loaded.get_block(x, 0, z)) for x in range(4) for z in range(2)],
            [str(turned.get_block(x, 0, z)) for x in range(4) for z in range(2)],
        )


class TransformPasteTests(TempSaveBase):
    def arrow(self) -> Schematic:
        """A 3x1x2 arrow: stairs facing north at (0,0,0), stone tail east."""
        schematic = Schematic.new(3, 1, 2, DATA_VERSION)
        schematic.set_block(0, 0, 0, "minecraft:oak_stairs[facing=north]")
        schematic.set_block(1, 0, 0, "minecraft:stone")
        schematic.set_block(2, 0, 0, "minecraft:stone")
        schematic.set_block(0, 0, 1, "minecraft:cobblestone")
        return schematic

    def test_paste_rotated(self):
        result = self.save.import_schem(
            self.arrow(), (0, 70, 0), anchor="min", rotate=90
        )
        self.assertTrue(result.complete)
        self.assertEqual(result.transform_warnings, [])
        # (x, z) -> (L-1-z, x): stairs (0,0) -> (1,0); tail runs south.
        self.assertEqual(
            str(self.save.get_block(1, 70, 0)), "minecraft:oak_stairs[facing=east]"
        )
        self.assertEqual(self.save.get_block(1, 70, 1), "minecraft:stone")
        self.assertEqual(self.save.get_block(0, 70, 0), "minecraft:cobblestone")

    def test_paste_flip_happens_before_rotate(self):
        by_options = tempfile.mkdtemp(prefix="schem-order-")
        self.addCleanup(shutil.rmtree, by_options, ignore_errors=True)
        make_save(by_options, CHUNKS_3X3)
        other = MinecraftSave.load(by_options)

        schematic = self.arrow()
        self.save.import_schem(
            schematic, (0, 70, 0), anchor="min", flip="x", rotate=90
        )
        other.import_schem(
            schematic.flipped("x").rotated(90), (0, 70, 0), anchor="min"
        )
        self.assertSequencesEqual(
            self.box_states(self.save, (0, 70, 0), (2, 70, 2)),
            self.box_states(other, (0, 70, 0), (2, 70, 2)),
        )

    def test_rotation_swings_about_the_origin(self):
        """Exporting with an origin and pasting rotated at that same point
        must turn the build around it, WorldEdit style: the block at the
        origin stays put."""
        self.save.fill((4, 70, 4), (8, 70, 8), "minecraft:stone")
        self.save.set_block(5, 70, 6, "minecraft:gold_block")
        origin = (5, 70, 6)
        schematic = Schematic.from_save(
            self.save, (4, 70, 4), (8, 70, 8), origin=origin
        )
        target_dir = tempfile.mkdtemp(prefix="schem-swing-")
        self.addCleanup(shutil.rmtree, target_dir, ignore_errors=True)
        make_save(target_dir, CHUNKS_3X3)
        target = MinecraftSave.load(target_dir)
        for degrees in (90, 180, 270):
            result = target.import_schem(schematic, origin, rotate=degrees)
            self.assertTrue(result.complete, result)
            self.assertEqual(
                target.get_block(*origin),
                BlockState("minecraft:gold_block"),
                f"the origin block moved under rotate={degrees}",
            )


# --------------------------------------------------------------------------
# Biomes
# --------------------------------------------------------------------------


class BiomeTests(TempSaveBase):
    def test_biomes_round_trip_through_a_v3_file(self):
        self.save.fill_biome((0, 60, 0), (7, 67, 7), "minecraft:desert")
        result = self.save.export_schem(
            (0, 60, 0), (7, 67, 7), self.schem_path(), biomes=True
        )
        self.assertTrue(result.complete)
        loaded = Schematic.load(self.schem_path())
        self.assertTrue(loaded.has_biomes)
        self.assertEqual(loaded.get_biome(3, 3, 3), "minecraft:desert")

        paste = self.save.import_schem(
            loaded, (-12, 80, -12), anchor="min", biomes=True
        )
        self.assertGreater(paste.biome_cells_set, 0)
        self.assertEqual(self.save.get_biome(-9, 83, -9), "minecraft:desert")

    def test_biomes_default_to_untouched_on_paste(self):
        self.save.fill_biome((0, 60, 0), (7, 67, 7), "minecraft:desert")
        schematic = Schematic.from_save(
            self.save, (0, 60, 0), (7, 67, 7), biomes=True
        )
        self.save.import_schem(schematic, (-12, 80, -12), anchor="min")
        self.assertEqual(self.save.get_biome(-9, 83, -9), "minecraft:plains")

    def test_asking_for_biomes_a_file_does_not_have_is_an_error(self):
        schematic = Schematic.new(2, 2, 2, DATA_VERSION)
        with self.assertRaises(NBTError) as ctx:
            self.save.import_schem(schematic, (0, 70, 0), biomes=True)
        self.assertIn("no biome data", str(ctx.exception))


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


class CliTests(TempSaveBase):
    def run_cli(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = _main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_export_info_import_flow(self):
        code, output, _ = self.run_cli(
            "export", self.dir, self.schem_path(),
            "--box", "0", "60", "0", "6", "64", "6", "--entities",
        )
        self.assertEqual(code, 0)
        self.assertIn("wrote", output)

        code, output, _ = self.run_cli("info", self.schem_path())
        self.assertEqual(code, 0)
        self.assertIn("7 x 5 x 7", output)
        self.assertIn(str(DATA_VERSION), output)

        code, output, _ = self.run_cli(
            "import", self.dir, self.schem_path(), "--at", "0", "80", "0"
        )
        self.assertEqual(code, 0)
        self.assertIn("dry run", output)
        fresh = MinecraftSave.load(self.dir)
        self.assertEqual(fresh.get_block(0, 80, 0), "minecraft:air")

        code, output, _ = self.run_cli(
            "import", self.dir, self.schem_path(),
            "--at", "0", "80", "0", "--write",
        )
        self.assertEqual(code, 0)
        fresh = MinecraftSave.load(self.dir)
        self.assertEqual(
            fresh.get_block(3, 82, 3), self.save.get_block(3, 62, 3)
        )

    def test_coordinates_can_be_player_relative(self):
        """~ notation resolves against the player, who stands at (8, 70, 8)
        in these worlds -- so --at '~' '~' '~' is WorldEdit's //paste."""
        code, output, err = self.run_cli(
            "export", self.dir, self.schem_path(),
            "--box", "~-2", "~-10", "~-2", "~2", "~-6", "~2",
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(Schematic.load(self.schem_path()).size, (5, 5, 5))

        code, output, err = self.run_cli(
            "import", self.dir, self.schem_path(),
            "--at", "~2", "~5", "~2", "--anchor", "min",
        )
        self.assertEqual(code, 0, err)
        self.assertIn("placed box: (10, 75, 10)", output)

    def test_bad_coordinates_fail_cleanly_not_with_a_traceback(self):
        code, _, err = self.run_cli(
            "import", self.dir, "missing.schem", "--at", "~oops", "0", "0"
        )
        self.assertEqual(code, 2)
        self.assertIn("~oops", err)
        # An unquoted ~ the shell already expanded gets the quoting hint.
        code, _, err = self.run_cli(
            "import", self.dir, "missing.schem",
            "--at", os.path.expanduser("~"), "0", "0",
        )
        self.assertEqual(code, 2)
        self.assertIn("quote", err)

    def test_transform_subcommand_preserves_container_version(self):
        schematic = Schematic.new(2, 1, 3, DATA_VERSION)
        schematic.set_block(0, 0, 0, "minecraft:oak_stairs[facing=north]")
        schematic.save(self.schem_path(), version=2)
        out_path = self.schem_path("turned.schem")
        code, output, _ = self.run_cli(
            "transform", self.schem_path(), out_path, "--rotate", "90"
        )
        self.assertEqual(code, 0)
        self.assertIn("container version 2", output)
        turned = Schematic.load(out_path)
        self.assertEqual(turned.source_version, 2)
        self.assertEqual(turned.size, (3, 1, 2))
        self.assertEqual(
            str(turned.get_block(2, 0, 0)), "minecraft:oak_stairs[facing=east]"
        )



class PublicTransformTests(unittest.TestCase):
    """The direction vocabulary's three functions, public under the names
    their C# twins already have (PORTING.md rule 3: member names match
    modulo casing, no renames-in-translation), so a caller can turn one
    state without building a schematic around it."""

    TWINS = {
        "properties_rotated_cw": "PropertiesRotatedCw",
        "properties_mirrored": "PropertiesMirrored",
        "directional_leftovers": "DirectionalLeftovers",
    }

    def test_the_property_transforms_are_public_under_their_twin_names(self):
        import heaton_voxel
        import heaton_voxel.schem as schem
        from heaton_voxel.schem import transforms

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "csharp", "HeatonVoxel", "Schem",
                               "Transforms.cs"), encoding="utf-8") as handle:
            csharp = handle.read()
        for name, twin in self.TWINS.items():
            with self.subTest(name=name):
                public = getattr(schem, name)
                self.assertIn(name, schem.__all__)
                self.assertIn(name, heaton_voxel.__all__)
                self.assertIs(getattr(heaton_voxel, name), public)
                # The private names every existing caller imports are the
                # same functions, so nothing moved.
                self.assertIs(getattr(transforms, "_" + name), public)
                self.assertTrue(public.__doc__)
                self.assertRegex(csharp, rf"public static [\w<>]+ {twin}\(")
                self.assertEqual("".join(part.capitalize()
                                         for part in name.split("_")), twin)

        turned = schem.properties_rotated_cw({"facing": "north", "half": "top"})
        self.assertEqual(list(turned.items()), [("facing", "east"), ("half", "top")])
        mirrored = schem.properties_mirrored({"facing": "east", "hinge": "left"}, "x")
        self.assertEqual(mirrored, {"facing": "west", "hinge": "right"})
        self.assertEqual(schem.directional_leftovers(
            [BlockState("mod:thing", {"side": "north"}),
             BlockState("minecraft:oak_stairs", {"facing": "north"})]),
            ["mod:thing: property side=north looks directional but is "
             "outside the transform vocabulary; left as-is"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
