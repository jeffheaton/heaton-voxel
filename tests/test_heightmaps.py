"""A chunk's four heightmaps, rebuilt from its blocks by the game's own rule.

``WORLD_SURFACE`` is format knowledge, and the other three are the game's:
which blocks stop a map is in its tags. ``heightmap_blocks`` copies those
tags down to the lower tier, so ``rebuild_heightmaps`` writes all four, and
what holds the copy to the game is the maps the game wrote itself: every
finished chunk of both fixtures derives its stored maps column for column
and rebuilds them byte for byte. A paste rewrites the maps of the chunks it
moved, and leaves alone the chunks the game has not finished, which it
generates over.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
import unittest

from fixtures import FIXTURE_REGION, LEGACY_REGION, MODERN_REGION
from heaton_voxel import heightmap_blocks
from heaton_voxel.block_defaults import BLOCK_DEFAULTS
from heaton_voxel.blocks import (
    AIR_BLOCKS,
    HEIGHTMAP_NAMES,
    Blocks,
    BlockState,
    ReadOnlyChunk,
    chunk_finished,
    heightmap_bits,
    heightmap_mask,
    unpack_indices,
)
from heaton_voxel.craft.block_facts import holds_fluid, in_heightmap
from heaton_voxel.craft.block_registry import BLOCK_IDS
from heaton_voxel.nbt import NBT, TagString
from heaton_voxel.schem import Schematic
from heaton_voxel.world import OVERWORLD, MinecraftSave
from tests.test_world import make_save

WORLD_SURFACE, MOTION_BLOCKING, NO_LEAVES, OCEAN_FLOOR = 1, 2, 4, 8

#: The fixtures the game wrote whole, by codec: 26.2's and 26.3's.
REGIONS = (FIXTURE_REGION, MODERN_REGION)


def _generator():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools",
                        "generate_heightmap_blocks.py")
    spec = importlib.util.spec_from_file_location("generate_heightmap_blocks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _finished_chunks(name):
    """Every finished chunk of a fixture region, as ``(key, chunk)``."""
    region = NBT.load(name)
    return [(key, chunk) for key, chunk in region.chunks.items()
            if chunk_finished(chunk.data.get("Status"))]


class TableTests(unittest.TestCase):
    """The lower tier's copy of the tags, held to the probe's."""

    def test_the_committed_tables_are_what_the_generator_writes(self):
        """Rerunning the generator after the probe is the whole job, so a
        table it would change is a table somebody forgot."""
        generator = _generator()
        for path, text in ((generator.PYTHON_OUT, generator.python_source()),
                           (generator.CSHARP_OUT, generator.csharp_source())):
            with self.subTest(path=path):
                with open(path, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), text,
                                     "rerun tools/generate_heightmap_blocks.py")

    def test_every_block_counts_as_the_probe_read_it(self):
        for name in sorted(BLOCK_IDS):
            states = [BlockState(name)]
            if "waterlogged" in BLOCK_DEFAULTS.get(name, ()):
                states += [BlockState(name, {"waterlogged": "true"}),
                           BlockState(name, {"waterlogged": "false"})]
            for state in states:
                fluid = holds_fluid(state.name, state.properties)
                want = 0
                if name not in AIR_BLOCKS:
                    want = WORLD_SURFACE
                    if in_heightmap(name) or fluid:
                        want |= MOTION_BLOCKING
                    if in_heightmap(name, leaves=False) or fluid:
                        want |= NO_LEAVES
                    if in_heightmap(name):
                        want |= OCEAN_FLOOR
                self.assertEqual(heightmap_mask(state), want, str(state))

    def test_the_maps_part_where_their_blocks_do(self):
        cases = {
            "minecraft:air": 0,
            "minecraft:cave_air": 0,
            "minecraft:void_air": 0,
            "minecraft:stone": 15,
            "minecraft:oak_leaves": WORLD_SURFACE | MOTION_BLOCKING | OCEAN_FLOOR,
            "minecraft:torch": WORLD_SURFACE,
            "minecraft:water": WORLD_SURFACE | MOTION_BLOCKING | NO_LEAVES,
            "minecraft:lava": WORLD_SURFACE | MOTION_BLOCKING | NO_LEAVES,
            "minecraft:kelp": WORLD_SURFACE | MOTION_BLOCKING | NO_LEAVES,
            # A dry stair and a wet one.
            "minecraft:oak_stairs[waterlogged=false]": 15,
            "minecraft:oak_stairs[waterlogged=true]": 15,
            # Wet by default: an omitted waterlogged takes it, as the game
            # reads it. No tag names either, so it is their water alone
            # that a motion map stops at.
            "minecraft:sea_pickle": WORLD_SURFACE | MOTION_BLOCKING | NO_LEAVES,
            "minecraft:tube_coral": WORLD_SURFACE | MOTION_BLOCKING | NO_LEAVES,
            "minecraft:tube_coral[waterlogged=false]": WORLD_SURFACE,
            # The name a chunk the game has not rewritten still holds.
            "minecraft:grass": WORLD_SURFACE,
            "minecraft:short_grass": WORLD_SURFACE,
            # The game has never heard of it: the unknown is solid.
            "modded:thing": 15,
        }
        for text, want in cases.items():
            with self.subTest(state=text):
                self.assertEqual(heightmap_mask(BlockState.parse(text)), want)

    def test_a_chunk_is_finished_at_full_alone(self):
        for status in ("minecraft:full", "full"):
            self.assertTrue(chunk_finished(status))
        for status in (None, "minecraft:carvers", "minecraft:initialize_light",
                       "minecraft:features", "structure_starts"):
            self.assertFalse(chunk_finished(status), status)


class GameMapTests(unittest.TestCase):
    """The rule against every map the game wrote into the fixtures."""

    def test_the_fixtures_hold_real_maps(self):
        for name in REGIONS:
            self.assertGreater(len(_finished_chunks(name)), 3, name)

    def test_derived_maps_are_the_maps_the_game_stored(self):
        for name in REGIONS:
            for key, chunk in _finished_chunks(name):
                blocks = Blocks(chunk)
                derived = blocks.derive_heightmaps()
                bits = heightmap_bits(blocks.min_y, blocks.max_y)
                for map_name in HEIGHTMAP_NAMES:
                    stored = unpack_indices(chunk.data["Heightmaps"][map_name],
                                            256, bits)
                    self.assertEqual(derived[map_name], stored,
                                     f"{name} chunk {key} {map_name}")

    def test_nothing_the_game_wrote_reads_stale(self):
        for name in REGIONS:
            for key, chunk in _finished_chunks(name):
                self.assertEqual(Blocks(chunk).stale_heightmaps(), {},
                                 f"{name} chunk {key}")

    def test_a_rebuild_of_a_right_map_changes_no_byte(self):
        """Order included: the game writes OCEAN_FLOOR first, and the bytes
        of a chunk are the bytes of its tags in order."""
        for name in REGIONS:
            for key, chunk in _finished_chunks(name):
                before = list(chunk.data["Heightmaps"].items())
                before = [(map_name, list(raw)) for map_name, raw in before]
                Blocks(chunk).rebuild_heightmaps()
                after = [(map_name, list(raw))
                         for map_name, raw in chunk.data["Heightmaps"].items()]
                self.assertEqual(after, before, f"{name} chunk {key}")

    def test_a_1_17_chunk_is_refused(self):
        """Its names and rules are its own, and it is never written."""
        region = NBT.load(LEGACY_REGION)
        chunk = next(iter(region.chunks.values()))
        for call in ("derive_heightmaps", "rebuild_heightmaps",
                     "stale_heightmaps"):
            with self.subTest(call=call), self.assertRaises(ReadOnlyChunk):
                getattr(Blocks(chunk), call)()


class SceneBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="heightmaps-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, [(0, 0), (1, 0), (0, 1), (1, 1)])
        self.save = MinecraftSave.load(self.dir)
        self.dim = self.save.dimension(OVERWORLD)
        self.save.fill((0, 60, 0), (31, 60, 31), "minecraft:stone")
        self.save.rebuild_heightmaps((0, 0, 0), (31, 0, 31))

    def tops(self, x, z):
        return [self.save.surface_y(x, z, name) for name in HEIGHTMAP_NAMES]


class RebuildTests(SceneBase):
    def test_each_map_stops_where_it_should(self):
        """One column of everything: a torch on a leaf over water over a
        stone, each map stopping at its own."""
        self.save.set_block(3, 61, 3, "minecraft:water")
        self.save.set_block(3, 62, 3, "minecraft:oak_leaves")
        self.save.set_block(3, 63, 3, "minecraft:torch")
        self.save.rebuild_heightmaps((3, 0, 3), (3, 0, 3))
        self.assertEqual(self.save.surface_y(3, 3, "WORLD_SURFACE"), 63)
        self.assertEqual(self.save.surface_y(3, 3, "MOTION_BLOCKING"), 62)
        self.assertEqual(self.save.surface_y(3, 3, "MOTION_BLOCKING_NO_LEAVES"), 61)
        self.assertEqual(self.save.surface_y(3, 3, "OCEAN_FLOOR"), 62)

    def test_it_reads_past_a_section_of_nothing_it_counts(self):
        """A torch alone high up, with the motion maps a whole section
        further down."""
        self.save.set_block(5, 100, 5, "minecraft:torch")
        self.save.rebuild_heightmaps((5, 0, 5), (5, 0, 5))
        self.assertEqual(self.tops(5, 5), [100, 60, 60, 60])

    def test_it_says_how_many_columns_were_stale_until_it_runs(self):
        self.save.fill((0, 61, 0), (3, 61, 1), "minecraft:stone")
        blocks = self.dim.blocks(0, 0)
        self.assertEqual(blocks.stale_heightmaps(),
                         {name: 8 for name in HEIGHTMAP_NAMES})
        self.save.rebuild_heightmaps((0, 0, 0), (0, 0, 0))
        self.assertEqual(self.dim.blocks(0, 0).stale_heightmaps(), {})

    def test_a_map_the_chunk_does_not_store_is_not_stale(self):
        """The game derives a missing map when it loads the chunk, so an
        older rebuild's chunk, WORLD_SURFACE alone, reads as right."""
        self.save.fill((0, 61, 0), (0, 61, 0), "minecraft:stone")
        maps = self.dim.chunk(0, 0).data["Heightmaps"]
        for name in ("MOTION_BLOCKING", "MOTION_BLOCKING_NO_LEAVES",
                     "OCEAN_FLOOR"):
            del maps[name]
        self.dim.blocks(0, 0).drop_heightmap_cache()
        self.assertEqual(self.dim.blocks(0, 0).stale_heightmaps(),
                         {"WORLD_SURFACE": 1})

    def test_an_unfinished_chunk_is_skipped_and_said(self):
        """The game writes its maps when it finishes it."""
        chunk = self.dim.chunk(1, 0)
        chunk.data["Status"] = TagString("minecraft:terrain")
        before = list(chunk.data["Heightmaps"]["WORLD_SURFACE"])
        self.save.set_block(20, 90, 4, "minecraft:stone")
        result = self.save.rebuild_heightmaps((0, 0, 0), (31, 0, 0),
                                              keep_loaded=True)
        self.assertEqual(result.chunks_rebuilt, 1)
        self.assertEqual(result.unfinished_chunks, [(1, 0)])
        self.assertFalse(result.complete)
        self.assertIn("1 unfinished skipped", repr(result))
        self.assertEqual(list(chunk.data["Heightmaps"]["WORLD_SURFACE"]), before)


class PasteTests(SceneBase):
    def setUp(self):
        super().setUp()
        self.save.fill((0, 80, 0), (3, 81, 3), "minecraft:bricks")
        self.schematic = Schematic.from_save(self.save, (0, 80, 0), (3, 81, 3))

    def paste(self, at=(14, 90, 14), **options):
        return self.save.import_schem(self.schematic, at, anchor="min", **options)

    def test_a_paste_moves_every_map_in_every_chunk_it_reaches(self):
        result = self.paste()
        self.assertEqual(result.heightmaps_rebuilt, 4)
        for x in range(14, 18):
            for z in range(14, 18):
                self.assertEqual(self.tops(x, z), [91] * 4, (x, z))
        for chunk in ((0, 0), (1, 0), (0, 1), (1, 1)):
            self.assertEqual(self.dim.blocks(*chunk).stale_heightmaps(), {}, chunk)

    def test_a_paste_can_be_told_to_leave_them(self):
        result = self.paste(heightmaps=False)
        self.assertEqual(result.heightmaps_rebuilt, 0)
        self.assertEqual(self.tops(15, 15), [60] * 4)
        self.assertTrue(self.dim.blocks(0, 0).stale_heightmaps())

    def test_it_survives_the_save(self):
        self.paste()
        self.save.save()
        saved = MinecraftSave.load(self.dir)
        self.assertEqual(saved.surface_y(15, 15), 91)
        self.assertEqual(saved.dimension(OVERWORLD).blocks(1, 1).stale_heightmaps(),
                         {})

    def test_a_paste_that_moved_nothing_in_a_chunk_leaves_its_maps(self):
        """All air, masked: nothing written, so a stale map here is
        somebody else's, and not this paste's to mend."""
        empty = Schematic.from_save(self.save, (0, 120, 0), (3, 121, 3))
        stale = self.dim.blocks(0, 0).stale_heightmaps()
        self.assertTrue(stale, "the bricks setUp filled moved no map")
        result = self.save.import_schem(empty, (0, 100, 0), anchor="min",
                                        paste_air=False)
        self.assertEqual(result.heightmaps_rebuilt, 0)
        self.assertEqual(self.dim.blocks(0, 0).stale_heightmaps(), stale)

    def test_paste_air_lowers_a_surface(self):
        self.save.fill((20, 61, 20), (20, 70, 20), "minecraft:stone")
        self.save.rebuild_heightmaps((20, 0, 20), (20, 0, 20))
        air = Schematic.from_save(self.save, (0, 120, 0), (0, 129, 0))
        self.save.import_schem(air, (20, 61, 20), anchor="min")
        self.assertEqual(self.tops(20, 20), [60] * 4)


class UnfinishedPasteTests(SceneBase):
    """The game generates over whatever is written into a chunk it has not
    finished, so a paste no more writes there than it invents a chunk."""

    def setUp(self):
        super().setUp()
        self.save.fill((0, 80, 0), (3, 81, 3), "minecraft:bricks")
        self.schematic = Schematic.from_save(self.save, (0, 80, 0), (3, 81, 3))
        self.dim.chunk(1, 0).data["Status"] = TagString("minecraft:features")

    def test_it_is_skipped_counted_and_said(self):
        result = self.save.import_schem(self.schematic, (14, 90, 2), anchor="min",
                                        keep_loaded=True)
        self.assertEqual(result.unfinished_chunks, [(1, 0)])
        self.assertEqual(result.missing_chunks, [])
        self.assertEqual(result.blocks_pasted, 16)
        self.assertEqual(result.blocks_out_of_range, 16)
        self.assertFalse(result.complete)
        self.assertIn("1 chunks unfinished", repr(result))
        self.assertEqual(self.save.get_block(15, 90, 2).name, "minecraft:bricks")
        self.assertEqual(self.save.get_block(16, 90, 2).name, "minecraft:air")

    def test_its_block_entities_stay_out_too(self):
        self.save.set_block(1, 80, 1, "minecraft:chest")
        schematic = Schematic.from_save(self.save, (0, 80, 0), (3, 81, 3))
        self.save.import_schem(schematic, (16, 90, 0), anchor="min",
                               keep_loaded=True)
        chunk = self.dim.chunk(1, 0)
        self.assertFalse(list(chunk.data.get("block_entities", ())))
        self.assertEqual(self.save.get_block(17, 90, 1).name, "minecraft:air")

    def test_an_entity_over_it_is_skipped(self):
        """The same refusal as the blocks under it, as a 1.17 chunk's is."""
        from heaton_voxel.nbt import TagCompound, TagDouble, TagList, TagString as S

        schematic = Schematic.from_save(self.save, (0, 80, 0), (3, 81, 3))
        schematic.entities.append(TagCompound({
            "id": S("minecraft:pig"),
            "Pos": TagList([TagDouble(1.5), TagDouble(1.0), TagDouble(1.5)]),
        }))
        result = self.save.import_schem(schematic, (16, 90, 0), anchor="min",
                                        keep_loaded=True)
        self.assertEqual(result.entities_added, 0)
        self.assertEqual([found[0] for found in result.entities_skipped],
                         ["minecraft:pig"])


if __name__ == "__main__":
    unittest.main()
