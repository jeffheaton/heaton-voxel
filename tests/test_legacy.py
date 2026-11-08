"""Chunks Minecraft 1.13 to 1.17 saved: read like any other, written never.

A chunk Minecraft 1.17 or 1.17.1 saved (DataVersion 2724..2730) keeps
everything under ``Level``, stores a block-carrying section only where it
holds something, runs from y=0 to 255, and numbers its biomes for the whole
chunk. ``fixtures/legacy/`` is 16 such chunks from OldWorld, and beside
them the game's own upgrade of the same chunks, which is what the reader is
held to here, cell by cell (``AgainstTheGameTests``). The rest pins what the
reader gives everything above it -- the shape of a 1.17 chunk, and a refusal
of every write, from the accessor up through the world, the schematic
paths and the craft Builder -- and that none of it changes a byte.

The chunks before 1.17 are the same shape with three differences, each
held to the game's own upgrade of real and made chunks in
``OlderAgainstTheGameTests``: before 1.16 a section's indices and a
heightmap's entries run on across longs, before 1.15 there is a biome a
column rather than a 4x4x4 cell, and before 1.14 a finished chunk was
named ``postprocessed`` or ``fullchunk``. Before 1.17 a chunk keeps its own
entities, too.
"""

from __future__ import annotations

import gzip
import os
import shutil
import tempfile
import unittest

import heaton_voxel.nbt as nbt
from heaton_voxel.blocks import (
    AIR_BLOCKS,
    HEIGHTMAP_NAMES,
    LEGACY_ALIGNED_DATA_VERSION,
    LEGACY_CELL_BIOMES_DATA_VERSION,
    LEGACY_ENTITY_STORAGE_DATA_VERSION,
    LEGACY_MAX_DATA_VERSION,
    LEGACY_MIN_DATA_VERSION,
    MIN_DATA_VERSION,
    BlockState,
    Blocks,
    ReadOnlyChunk,
    _unpack_run_on,
    legacy_release,
)
from heaton_voxel.legacy_biomes import LEGACY_BIOMES
from heaton_voxel.nbt import NBT, NBTError, TagCompound, TagInt, TagIntArray
from heaton_voxel.world import OVERWORLD, MinecraftSave

from fixtures import (
    LEGACY_1_15_REGION,
    LEGACY_DIR,
    LEGACY_LEVEL_DAT,
    LEGACY_OLDER_UPGRADED,
    LEGACY_REGION,
    LEGACY_UPGRADED,
)

REGION_X, REGION_Z = 5, -1


def _region() -> NBT:
    return NBT.load(LEGACY_REGION)


def _upgraded() -> list[TagCompound]:
    with open(LEGACY_UPGRADED, "rb") as handle:
        _, root = nbt.decode(gzip.decompress(handle.read()))
    return list(root["chunks"])


def _copy(tag):
    """A deep copy of a tag, by encoding it and decoding it back."""
    _, root = nbt.decode(nbt.encode(tag))
    return root


def _world_chunk(local) -> tuple[int, int]:
    return REGION_X * 32 + local[0], REGION_Z * 32 + local[1]


class AgainstTheGameTests(unittest.TestCase):
    """The reader against ``fixtures/legacy/upgraded.nbt``: what the game
    itself made of the same 16 chunks when it upgraded them."""

    @classmethod
    def setUpClass(cls):
        region = _region()
        cls.pairs = [
            (key, Blocks(region.chunks[key]), Blocks(up))
            for key, up in zip(sorted(region.chunks), _upgraded())
        ]

    def test_the_upgrade_is_of_these_chunks(self):
        self.assertEqual(len(self.pairs), 16)
        for _, legacy, upgraded in self.pairs:
            self.assertEqual((legacy.world_x, legacy.world_z),
                             (upgraded.world_x, upgraded.world_z))
            self.assertTrue(legacy.legacy)
            self.assertFalse(upgraded.legacy)

    def test_every_block_of_the_world_is_the_one_the_game_upgrades_it_to(self):
        # The game renames on its way up; the reader reads what is stored.
        # The only renames it makes in these 16 chunks are craft's table's,
        # which lists every one the game has made since 1.17.
        from heaton_voxel.craft.blockinfo import RENAMED

        cells = renamed = 0
        for key, legacy, upgraded in self.pairs:
            for y in range(16):
                old, new = legacy.sections[y].blocks, upgraded.sections[y].blocks
                old_palette = [state.canonical() for state in old.palette]
                expected = [
                    BlockState(RENAMED.get(s.name, (s.name,))[0], s.properties).canonical()
                    for s in old_palette
                ]
                new_palette = new.palette
                old_indices, new_indices = old.indices(), new.indices()
                for index in range(4096):
                    got = old_palette[old_indices[index]]
                    want = new_palette[new_indices[index]]
                    if got != want:
                        self.assertEqual(
                            expected[old_indices[index]], want,
                            f"chunk {_world_chunk(key)} section {y} cell {index}")
                        renamed += 1
                    cells += 1
        self.assertEqual(cells, 16 * 16 * 4096)
        self.assertGreater(renamed, 0, "the fixture holds 1.17 grass")

    def test_every_biome_is_the_name_the_game_gives_it(self):
        for key, legacy, upgraded in self.pairs:
            for y in range(16):
                self.assertEqual(
                    [legacy.sections[y].biomes.get(i) for i in range(64)],
                    [upgraded.sections[y].biomes.get(i) for i in range(64)],
                    f"chunk {_world_chunk(key)} section {y}")

    def test_every_surface_is_where_the_game_puts_it(self):
        # The game shifts each 1.17 entry by 64 for the deeper world; read
        # from a floor of 0, the stored entry is already that surface.
        for key, legacy, upgraded in self.pairs:
            for name in HEIGHTMAP_NAMES:
                for z in range(16):
                    for x in range(16):
                        self.assertEqual(
                            legacy.surface_y(x, z, name),
                            upgraded.surface_y(x, z, name),
                            f"chunk {_world_chunk(key)} {name} ({x}, {z})")

    def test_the_biome_table_is_the_games_and_reads_the_low_byte(self):
        self.assertEqual(len(LEGACY_BIOMES), 256)
        # 1.17's mountains, renamed by 1.18; a number 1.17 never had.
        self.assertEqual(LEGACY_BIOMES[3], "minecraft:windswept_hills")
        self.assertEqual(LEGACY_BIOMES[1], "minecraft:plains")
        self.assertEqual(LEGACY_BIOMES[200], "minecraft:plains")
        region = _region()
        data = _copy(region.chunks[sorted(region.chunks)[0]].data)
        data["Level"]["Biomes"] = TagIntArray([256 + 3] * 1024)
        self.assertEqual(Blocks(data).get_biome(0, 0, 0), "minecraft:windswept_hills")


def _older():
    """``(name, chunk, the game's upgrade, its state map, its 1.17.1
    stage)`` for every case of ``fixtures/legacy/older.nbt``, the chunk read
    from its region where it came from one."""
    with open(LEGACY_OLDER_UPGRADED, "rb") as handle:
        _, root = nbt.decode(gzip.decompress(handle.read()))
    regions, cases = {}, []
    for row in root["cases"]:
        if "chunk" in row:
            data = row["chunk"]
        else:
            source = str(row["source"])
            if source not in regions:
                regions[source] = NBT.load(os.path.join(LEGACY_DIR, *source.split("/")))
            data = regions[source].chunks[tuple(int(v) for v in row["key"])].data
        states = {BlockState.from_nbt(pair["from"]).canonical():
                  BlockState.from_nbt(pair["to"]).canonical() for pair in row["states"]}
        cases.append((str(row["name"]), data, row["upgraded"], states, str(row["status_1_17"])))
    return cases


class OlderAgainstTheGameTests(unittest.TestCase):
    """The reader against ``fixtures/legacy/older.nbt``: what the game made
    of chunks 1.13 to 1.16 saved, real and made, when it upgraded them."""

    @classmethod
    def setUpClass(cls):
        cls.cases = [(name, Blocks(data), Blocks(up), up, states, stage)
                     for name, data, up, states, stage in _older()]

    def test_the_cases_cover_every_format_the_range_has(self):
        versions = [legacy.data_version for _, legacy, *_ in self.cases]
        self.assertEqual(len(self.cases), 17)
        self.assertTrue(min(versions) == LEGACY_MIN_DATA_VERSION)
        for boundary in (1802, 1905, 1911, LEGACY_CELL_BIOMES_DATA_VERSION,
                         LEGACY_ALIGNED_DATA_VERSION):
            self.assertTrue(any(v < boundary for v in versions), boundary)
            self.assertTrue(any(v >= boundary for v in versions), boundary)
        widths = {section.blocks.bits for _, legacy, *_ in self.cases
                  for y, section in legacy.sections.items()
                  if 0 <= y < 16 and "data" in section.blocks._tag}
        # five, six and seven bits a cell straddle longs when they run on
        self.assertTrue({4, 5, 6, 7} <= widths, widths)
        self.assertEqual({legacy_release(v) for v in versions},
                         {"1.13", "1.14", "1.15", "1.16"})

    def test_every_block_is_the_one_the_game_upgrades_it_to(self):
        # The reader reads a block under the name its chunk stored it by;
        # each state the game upgraded on its own is what the reader's is
        # held to, cell for cell, in the chunk the game upgraded whole.
        cells = changed = 0
        air = BlockState("minecraft:air").canonical()
        for name, legacy, upgraded, _, states, _ in self.cases:
            for y in range(16):
                old, new = legacy.sections[y].blocks, upgraded.sections[y].blocks
                expected = [states.get(state.canonical(), air if state.canonical() == air else None)
                            for state in old.palette]
                old_indices, new_indices = old.indices(), new.indices()
                new_palette = [state.canonical() for state in new.palette]
                for index in range(4096):
                    want = expected[old_indices[index]]
                    self.assertIsNotNone(want, f"{name}: no upgrade of "
                                         f"{old.palette[old_indices[index]]}")
                    got = new_palette[new_indices[index]]
                    if got != want:
                        self.fail(f"{name} section {y} cell {index}: the reader's "
                                  f"{old.palette[old_indices[index]]} upgrades to {want}, "
                                  f"the game made {got}")
                    if old.palette[old_indices[index]].canonical() != want:
                        changed += 1
                    cells += 1
        self.assertEqual(cells, 17 * 16 * 4096)
        self.assertGreater(changed, 0, "renamed and reshaped blocks, held to the game")

    def test_every_biome_is_the_name_the_game_gives_it(self):
        for name, legacy, upgraded, *_ in self.cases:
            for y in range(16):
                self.assertEqual(
                    [legacy.sections[y].biomes.get(i) for i in range(64)],
                    [upgraded.sections[y].biomes.get(i) for i in range(64)],
                    f"{name} section {y}")

    def test_every_surface_is_where_the_game_puts_it(self):
        compared = 0
        for name, legacy, upgraded, raw, *_ in self.cases:
            stored = set(raw.get("Heightmaps", {}))
            for heightmap in HEIGHTMAP_NAMES:
                if heightmap not in stored:
                    continue
                for z in range(16):
                    for x in range(16):
                        self.assertEqual(
                            legacy.surface_y(x, z, heightmap),
                            upgraded.surface_y(x, z, heightmap),
                            f"{name} {heightmap} ({x}, {z})")
                        compared += 1
        self.assertGreater(compared, 0)

    def test_finished_is_what_1_17_calls_full(self):
        # the 1.18 upgrade folds an overworld chunk's stages from spawn up
        # into one retrogen, so the stage is held to the one 1.17.1 gives
        for name, legacy, _, _, _, stage in self.cases:
            self.assertEqual(legacy.finished, stage == "full", f"{name}: {legacy.status}")

    def test_entities_before_1_17_are_the_ones_the_game_carries_up(self):
        held = 0
        for name, legacy, _, raw, *_ in self.cases:
            entities = legacy.legacy_entities
            if legacy.data_version >= LEGACY_ENTITY_STORAGE_DATA_VERSION:
                self.assertIsNone(entities, name)
                continue
            game = list(raw.get("entities") or ())
            self.assertEqual(len(entities), len(game), name)
            for ours, theirs in zip(entities, game):
                self.assertEqual([float(v) for v in ours["Pos"]],
                                 [float(v) for v in theirs["Pos"]], name)
                held += 1
        self.assertGreater(held, 0, "the fixture holds an entity")


class RunOnTests(unittest.TestCase):
    """The packing before 1.16, unit by unit."""

    def test_a_value_straddling_two_longs_is_read_across_the_seam(self):
        from heaton_voxel.schem.litematic import pack_tight
        for bits in (4, 5, 6, 7, 9, 12):
            values = [(i * 2654435761) % (1 << bits) for i in range(4096)]
            self.assertEqual(_unpack_run_on(pack_tight(values, bits), 4096, bits), values, bits)

    def test_a_section_whose_length_fills_no_whole_width_is_refused(self):
        data = _older()[-1][1]
        _, data = nbt.decode(nbt.encode(data))
        section = next(s for s in data["Level"]["Sections"] if int(s["Y"]) == 11)
        section["BlockStates"] = nbt.TagLongArray(list(section["BlockStates"])[:-1])
        with self.assertRaises(NBTError) as caught:
            Blocks(data).sections[11].blocks
        self.assertIn("no whole number of bits", str(caught.exception))

    def test_a_column_of_biomes_takes_its_cell_s_center(self):
        _, data = nbt.decode(nbt.encode(_older()[-2][1]))
        numbers = [0] * 256
        numbers[(4 * 1 + 2) * 16 + 4 * 3 + 2] = 2        # the center of cell x=3, z=1
        data["Level"]["Biomes"] = TagIntArray(numbers)
        accessor = Blocks(data)
        self.assertEqual(accessor.get_biome(13, 5, 5), "minecraft:desert")
        self.assertEqual(accessor.get_biome(13, 200, 5), "minecraft:desert")
        self.assertEqual(accessor.get_biome(12, 5, 4), "minecraft:desert")
        self.assertEqual(accessor.get_biome(11, 5, 5), "minecraft:ocean")
        data["Level"]["Biomes"] = TagIntArray([1] * 1024)
        with self.assertRaises(NBTError) as caught:
            Blocks(data)
        self.assertIn("not the 256", str(caught.exception))

    def test_a_refusal_names_the_release(self):
        cases = _older()
        names = {legacy_release(int(data["DataVersion"])) for _, data, *_ in cases}
        self.assertEqual(names, {"1.13", "1.14", "1.15", "1.16"})
        with self.assertRaises(ReadOnlyChunk) as caught:
            Blocks(cases[-1][1]).refuse_legacy()
        self.assertIn("Minecraft 1.13 (DataVersion 1631)", str(caught.exception))
        _, data = nbt.decode(nbt.encode(cases[0][1]))
        del data["Level"]["xPos"]
        with self.assertRaises(NBTError) as caught:
            Blocks(data)
        self.assertIn("no xPos", str(caught.exception))

    def test_a_1_15_region_s_entities_answer_through_the_world(self):
        directory = tempfile.mkdtemp(prefix="older-world-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        region_dir = os.path.join(directory, "dimensions", "minecraft", "overworld", "region")
        os.makedirs(region_dir)
        shutil.copyfile(LEGACY_1_15_REGION, os.path.join(region_dir, "r.0.-5.mca"))
        shutil.copyfile(LEGACY_LEVEL_DAT, os.path.join(directory, "level.dat"))
        save = MinecraftSave.load(directory, dimensions=[OVERWORLD])
        # the one entity, in chunk (0, -153), which keeps it in its Level
        found = save.entities_in((0, -64, -2448), (15, 320, -2433))
        self.assertEqual(found.chunks_scanned, 1)
        self.assertEqual(len(found.entities), 1)
        self.assertTrue(found.complete)
        everywhere = save.entities_in((0, -64, -2560), (31, 320, -2400))
        self.assertEqual(len(everywhere.entities), 1)
        self.assertEqual(everywhere.chunks_scanned, 4)


class ShapeTests(unittest.TestCase):
    """A 1.17 chunk as everything above the reader sees it."""

    @classmethod
    def setUpClass(cls):
        cls.region = _region()
        cls.chunks = {key: Blocks(cls.region.chunks[key]) for key in sorted(cls.region.chunks)}

    def test_its_world_is_zero_to_255_whatever_it_stores(self):
        for key, accessor in self.chunks.items():
            self.assertTrue(accessor.legacy)
            self.assertFalse(accessor.modern_states)
            self.assertTrue(LEGACY_MIN_DATA_VERSION <= accessor.data_version
                            <= LEGACY_MAX_DATA_VERSION)
            self.assertEqual((accessor.min_y, accessor.max_y), (0, 255))
            self.assertEqual((accessor.world_x >> 4, accessor.world_z >> 4),
                             _world_chunk(key))
            for y in range(16):
                self.assertIsNotNone(accessor.sections[y].blocks, (key, y))
            with self.assertRaises(IndexError):
                accessor.get(0, -1, 0)
            with self.assertRaises(IndexError):
                accessor.get(0, 256, 0)

    def test_a_section_left_out_is_air(self):
        accessor = self.chunks[(10, 3)]
        stored = {int(t["Y"]) for t in self.region.chunks[(10, 3)].data["Level"]["Sections"]
                  if "Palette" in t}
        missing = [y for y in range(16) if y not in stored]
        self.assertTrue(missing)
        for y in missing:
            self.assertEqual(accessor.get(7, y * 16 + 5, 9), BlockState("minecraft:air"))
            self.assertEqual(accessor.sections[y].blocks.histogram(),
                             {BlockState("minecraft:air"): 4096})

    def test_a_section_of_light_alone_is_light_and_nothing_else(self):
        accessor = self.chunks[(10, 3)]
        below = accessor.sections[-1]
        self.assertIsNone(below.blocks)
        self.assertIsNone(below.biomes)
        accessor.sky_light(0, -1, 0)  # a light read reaches it; no IndexError

    def test_its_stage_is_as_stored(self):
        self.assertEqual(self.chunks[(10, 3)].status, "full")
        self.assertEqual(self.chunks[(20, 0)].status, "features")

    def test_its_blocks_are_real_and_its_surface_is_on_them(self):
        accessor = self.chunks[(10, 3)]
        histogram = accessor.histogram()
        self.assertEqual(sum(histogram.values()), 16 * 4096)
        self.assertIn(BlockState("minecraft:bedrock"), histogram)
        for x, z in ((0, 0), (5, 5), (15, 15)):
            top = accessor.surface_y(x, z, "WORLD_SURFACE")
            self.assertNotIn(accessor.get(x, top, z).name, AIR_BLOCKS)
            self.assertIn(accessor.get(x, top + 1, z).name, AIR_BLOCKS)


class ReadOnlyTests(unittest.TestCase):

    def setUp(self):
        self.region = _region()
        self.chunk = self.region.chunks[(10, 3)]
        self.accessor = Blocks(self.chunk)

    def test_every_write_is_refused(self):
        accessor = self.accessor
        section = accessor.sections[2]
        stone = BlockState("minecraft:stone")
        writes = [
            lambda: accessor.set(1, 20, 1, stone),
            lambda: accessor.set_biome(1, 20, 1, "minecraft:desert"),
            accessor.reset_lighting,
            accessor.prune,
            lambda: section.blocks.set(0, stone),
            lambda: section.blocks.fill_all(stone),
            lambda: section.blocks.set_all([stone] * 4096),
            lambda: section.blocks.set_many([0, 1], stone),
            section.blocks.prune,
            lambda: section.biomes.set(0, "minecraft:desert"),
            lambda: accessor.sections[12].blocks.set(0, stone),  # a section left out
        ]
        for write in writes:
            with self.assertRaises(ReadOnlyChunk) as caught:
                write()
            self.assertIn("1.17", str(caught.exception))
            self.assertIn("(170, -29)", str(caught.exception))
        self.assertTrue(issubclass(ReadOnlyChunk, NBTError))
        self.assertTrue(section.blocks.read_only)

    def test_a_refused_write_changes_no_byte(self):
        with open(LEGACY_REGION, "rb") as handle:
            original = handle.read()
        self.test_every_write_is_refused()
        for chunk in self.region.chunks.values():
            Blocks(chunk).histogram()  # every container read, every biome built
        self.assertFalse(self.region.is_modified())
        self.assertEqual(self.region.to_bytes(), original)

    def test_refuse_legacy_names_what_it_will_not_do(self):
        with self.assertRaises(ReadOnlyChunk) as caught:
            self.accessor.refuse_legacy("export to a schematic")
        self.assertIn("will not export to a schematic", str(caught.exception))


class RefusalTests(unittest.TestCase):
    """What the reader will not read as a 1.17 chunk."""

    def setUp(self):
        region = _region()
        self.data = _copy(region.chunks[(10, 3)].data)

    def test_versions_outside_both_ranges_are_refused(self):
        for version in (LEGACY_MIN_DATA_VERSION - 1, LEGACY_MAX_DATA_VERSION + 1,
                        MIN_DATA_VERSION - 1, 1343):
            self.data["DataVersion"] = TagInt(version)
            with self.assertRaises(NBTError) as caught:
                Blocks(self.data)
            self.assertNotIsInstance(caught.exception, ReadOnlyChunk)
            self.assertIn("neither format", str(caught.exception))

    def test_a_chunk_without_its_level_is_refused(self):
        del self.data["Level"]
        with self.assertRaises(NBTError):
            Blocks(self.data)

    def test_a_biome_array_of_another_height_is_refused(self):
        self.data["Level"]["Biomes"] = TagIntArray([1] * 1536)
        with self.assertRaises(NBTError):
            Blocks(self.data)

    def test_blocks_outside_the_1_17_world_are_refused(self):
        sections = self.data["Level"]["Sections"]
        high = _copy(next(t for t in sections if "Palette" in t))
        high["Y"] = nbt.TagByte(16)
        sections.append(high)
        with self.assertRaises(NBTError) as caught:
            Blocks(self.data)
        self.assertIn("datapack", str(caught.exception))


class WorldTests(unittest.TestCase):
    """The world accessors over a 1.17 region: every read answers, every
    write is refused the way a chunk that cannot be used always was, and
    the files come out of a save exactly as they went in."""

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="legacy-world-")
        region_dir = os.path.join(self.directory, "dimensions", "minecraft",
                                  "overworld", "region")
        os.makedirs(region_dir)
        self.region_path = os.path.join(region_dir, os.path.basename(LEGACY_REGION))
        shutil.copyfile(LEGACY_REGION, self.region_path)
        shutil.copyfile(LEGACY_LEVEL_DAT, os.path.join(self.directory, "level.dat"))
        self.save = MinecraftSave.load(self.directory, dimensions=[OVERWORLD])
        # Chunk (170, -29): x 2720..2735, z -464..-449.
        self.x, self.z = 2725, -460
        self.top = self.save.surface_y(self.x, self.z, "WORLD_SURFACE")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _unchanged(self):
        self.save.save()
        with open(LEGACY_REGION, "rb") as want, open(self.region_path, "rb") as got:
            self.assertEqual(got.read(), want.read())

    def test_reads_answer(self):
        save, x, z = self.save, self.x, self.z
        self.assertIsNotNone(self.top)
        self.assertNotIn(save.get_block(x, self.top, z).name, AIR_BLOCKS)
        self.assertEqual(save.get_block(x, 200, z), BlockState("minecraft:air"))
        self.assertIsNone(save.get_block(x, 256, z))
        self.assertTrue(save.get_biome(x, 64, z).startswith("minecraft:"))
        self.assertEqual(save.dimension().chunk_status(170, -29), "full")
        census = save.histogram((2720, 0, -464), (2735, 255, -449))
        self.assertFalse(census.unreadable_chunks)
        self.assertEqual(sum(census.counts.values()), 16 * 16 * 256)
        found = save.find_blocks((2720, 0, -464), (2735, 255, -449), "minecraft:bedrock")
        self.assertFalse(found.unreadable_chunks)
        self.assertTrue(found.matches)

    def test_writes_are_refused_and_change_nothing(self):
        save, x, z, top = self.save, self.x, self.z, self.top
        box = ((2720, 60, -464), (2735, 70, -449))
        self.assertFalse(save.set_block(x, top + 1, z, "minecraft:stone"))
        self.assertFalse(save.place_block(x, top + 1, z, "minecraft:oak_fence"))
        self.assertFalse(save.set_biome(x, 64, z, "minecraft:desert"))
        for result in (
            save.fill(*box, "minecraft:stone"),
            save.fill_biome(*box, "minecraft:desert"),
            save.unfreeze(*box),
            save.connect_blocks(*box),
            save.reset_lighting(*box, neighbors=False),
            save.rebuild_heightmaps(*box),
        ):
            self.assertEqual([chunk for chunk, _ in result.unreadable_chunks],
                             [(170, -29)], type(result).__name__)
            self.assertIn("1.17", result.unreadable_chunks[0][1])
        with self.assertRaises(ReadOnlyChunk):
            save.spawn_entity("minecraft:pig", x + 0.5, top + 1.0, z + 0.5)
        self.assertEqual(save.get_block(x, top + 1, z), BlockState("minecraft:air"))
        self._unchanged()

    def test_a_schematic_neither_comes_from_nor_goes_into_one(self):
        from heaton_voxel.schem import Schematic

        save = self.save
        box = ((2722, 60, -462), (2724, 62, -460))
        target = os.path.join(self.directory, "out.schem")
        with self.assertRaises(NBTError) as caught:
            save.export_schem(*box, target)
        self.assertIn("1.17", str(caught.exception))
        exported = save.export_schem(*box, target, allow_incomplete=True)
        self.assertEqual([chunk for chunk, _ in exported.unreadable_chunks], [(170, -29)])

        paste = Schematic(2, 2, 2, MIN_DATA_VERSION)
        for cell in ((0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 1)):
            paste.set_block(*cell, "minecraft:stone")
        pasted = save.import_schem(paste, (2725, 80, -460))
        self.assertEqual([chunk for chunk, _ in pasted.unreadable_chunks], [(170, -29)])
        self._unchanged()

    def test_the_builder_refuses_before_it_writes(self):
        from heaton_voxel.craft import Builder, Refused

        builder = Builder(self.save)
        with self.assertRaises(Refused) as caught:
            builder.fill((2722, 90, -462), (2740, 92, -460), "minecraft:stone")
        self.assertIn("1.17", str(caught.exception))
        self.assertEqual(list(builder.journal.written()), [])
        self._unchanged()

    def test_the_ground_reader_reads_it(self):
        from heaton_voxel.craft import walk_surface

        probe = walk_surface(self.save, self.x, self.z)
        self.assertIsNotNone(probe)
        self.assertLessEqual(probe.y, self.top)


if __name__ == "__main__":
    unittest.main()
