"""Chunks built from nothing, held to the chunks the game builds.

``empty_chunk`` makes a finished chunk of air the way the game writes one,
and these hold it to the game's own finished chunks in both fixtures -- 26.2
in ``fixtures/r.-1.0.mca``, 26.3 in ``fixtures/modern/`` -- key for key, in
the game's order, type for type, an all-air section tag for tag. Then
``create_chunks`` over a copy of each: it builds only where the world has no
chunk, names what it leaves, makes the region files and the dimension it
needs, stamps the world's own DataVersion (26.3's for a newer world), and
what it builds saves, reloads and takes a build like any chunk the game
made.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from heaton_voxel import nbt  # noqa: E402
from heaton_voxel.blocks import (  # noqa: E402
    EMPTY_CHUNK_MAX_DATA_VERSION,
    EMPTY_CHUNK_MIN_DATA_VERSION,
    MODERN_STATE_DATA_VERSION,
    Blocks,
    empty_chunk,
)
from heaton_voxel.nbt import NBT, NBTError, TagCompound, TagInt, TagList  # noqa: E402
from heaton_voxel.world import END, NETHER, OVERWORLD, MinecraftSave  # noqa: E402

FIXTURES = os.path.join(_ROOT, "fixtures")
#: (region file, level.dat) of each fixture world, and the DataVersion its
#: finished chunks carry.
WORLDS = {
    4903: (os.path.join(FIXTURES, "r.-1.0.mca"), os.path.join(FIXTURES, "level.dat")),
    5023: (os.path.join(FIXTURES, "modern", "r.-1.0.mca"),
           os.path.join(FIXTURES, "modern", "level.dat")),
}


def _game_chunk(version: int) -> TagCompound:
    """A finished chunk the game wrote at ``version``."""
    region = NBT.load(WORLDS[version][0])
    for key in sorted(region.chunks):
        data = region.chunks[key].data
        if data is not None and str(data.get("Status")) == "minecraft:full":
            return data
    raise AssertionError(f"no finished chunk in the {version} fixture")


def _shape(tag):
    """A tag's types and keys, in order, with no values: what the game's
    reader asks of a chunk before it asks what is in it."""
    if isinstance(tag, TagCompound):
        return ("compound", tuple((key, _shape(value)) for key, value in tag.items()))
    if isinstance(tag, TagList):
        first = _shape(tag[0]) if tag else None
        return ("list", tag.element_type if tag else "empty", first)
    return type(tag).__name__


def _world(version: int) -> str:
    """A copy of a fixture world, overworld only."""
    directory = tempfile.mkdtemp(prefix="heaton-voxel-create-")
    region_dir = os.path.join(directory, "dimensions", "minecraft", "overworld", "region")
    os.makedirs(region_dir)
    region, level = WORLDS[version]
    shutil.copyfile(region, os.path.join(region_dir, "r.-1.0.mca"))
    shutil.copyfile(level, os.path.join(directory, "level.dat"))
    return directory


def _restamp(directory: str, version: int) -> None:
    level = NBT.load(os.path.join(directory, "level.dat"))
    level.root["Data"]["DataVersion"] = TagInt(version)
    level.save(os.path.join(directory, "level.dat"))


class EmptyChunkShapeTests(unittest.TestCase):
    """An empty chunk is the game's finished chunk with nothing in it."""

    def built(self, version: int) -> TagCompound:
        game = _game_chunk(version)
        return empty_chunk(int(game["xPos"]), int(game["zPos"]), data_version=version,
                           min_section=int(game["yPos"]), max_section=19,
                           biome="minecraft:plains")

    def test_its_keys_are_the_game_s_in_the_game_s_order(self):
        for version in WORLDS:
            with self.subTest(version=version):
                game = _game_chunk(version)
                ours = self.built(version)
                # blending_data is what the game leaves on a chunk it
                # upgraded from an older world, never on one it built
                theirs = [key for key in game if key != "blending_data"]
                self.assertEqual(list(ours), theirs)

    def test_every_field_is_of_the_game_s_type(self):
        for version in WORLDS:
            with self.subTest(version=version):
                game = _game_chunk(version)
                ours = self.built(version)
                for key in ours:
                    if key in ("sections", "Heightmaps", "structures", "PostProcessing",
                               "block_entities", "block_ticks", "fluid_ticks"):
                        continue
                    self.assertIs(type(ours[key]), type(game[key]), key)
                self.assertEqual(list(ours["Heightmaps"]), list(game["Heightmaps"]))
                for name in ours["Heightmaps"]:
                    self.assertEqual(len(ours["Heightmaps"][name]), len(game["Heightmaps"][name]))
                self.assertEqual(list(ours["structures"]), list(game["structures"]))
                self.assertEqual(_shape(ours["PostProcessing"]), _shape(game["PostProcessing"]))
                for key in ("block_entities", "block_ticks"):
                    self.assertEqual(_shape(ours[key]), _shape(game[key]), key)

    def test_an_all_air_section_is_the_game_s_all_air_section(self):
        for version in WORLDS:
            with self.subTest(version=version):
                game = _game_chunk(version)
                ours = self.built(version)
                top = game["sections"][-1]
                self.assertEqual(list(ours["sections"][-1]), list(top))
                # the game's highest section in both fixtures holds air alone
                self.assertEqual(nbt.encode(TagCompound({"v": ours["sections"][-1]["block_states"]})),
                                 nbt.encode(TagCompound({"v": top["block_states"]})))
                self.assertEqual(int(ours["sections"][-1]["Y"]), int(top["Y"]))

    def test_it_is_finished_unlit_and_air_throughout(self):
        for version in WORLDS:
            with self.subTest(version=version):
                chunk = Blocks(self.built(version))
                self.assertTrue(chunk.finished)
                self.assertEqual(chunk.modern_states, version >= MODERN_STATE_DATA_VERSION)
                self.assertEqual((chunk.min_y, chunk.max_y), (-64, 319))
                self.assertEqual(chunk.histogram(), {chunk.get(0, -64, 0): 16 * 16 * 384})
                self.assertEqual(str(chunk.get(15, 319, 15)), "minecraft:air")
                self.assertEqual(chunk.stale_heightmaps(), {})
                data = self.built(version)
                self.assertEqual(int(data["isLightOn"]), 0)
                self.assertFalse(any("BlockLight" in s or "SkyLight" in s for s in data["sections"]))

    def test_only_a_version_whose_chunk_has_been_read_is_built(self):
        for version in (EMPTY_CHUNK_MIN_DATA_VERSION - 1, EMPTY_CHUNK_MAX_DATA_VERSION + 1):
            with self.assertRaisesRegex(NBTError, "no finished chunk of DataVersion"):
                empty_chunk(0, 0, data_version=version, min_section=-4, max_section=19,
                            biome="minecraft:plains")
        with self.assertRaises(NBTError):
            empty_chunk(0, 0, data_version=5023, min_section=2, max_section=1,
                        biome="minecraft:plains")
        with self.assertRaises(NBTError):
            empty_chunk(0, 0, data_version=5023, min_section=0, max_section=1, biome="")


class CreateChunksTests(unittest.TestCase):
    def setUp(self):
        self.directory = _world(4903)
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.save = MinecraftSave.load(self.directory)

    def test_it_builds_only_where_there_is_no_chunk_and_names_the_rest(self):
        # chunks -32..-30 by 27..30: (-32, 28) stopped at initialize_light
        # and (-32, 29) at carvers; the rest the world has never made
        result = self.save.create_chunks((-512, 0, 432), (-465, 0, 495))
        self.assertEqual(result.chunks_created, 10)
        self.assertEqual(result.unfinished_chunks, [(-32, 28), (-32, 29)])
        self.assertEqual(result.existing_chunks, [])
        self.assertFalse(result.complete)
        self.assertEqual(result.regions_created, 0)
        dim = self.save.dimension(OVERWORLD)
        self.assertEqual(dim.chunk_status(-32, 28), "minecraft:initialize_light")
        self.assertEqual(dim.chunk_status(-31, 28), "minecraft:full")
        # and a finished chunk the world made is left alone
        again = self.save.create_chunks((-448, 0, 224), (-433, 0, 239))
        self.assertEqual((again.chunks_created, again.existing_chunks), (0, [(-28, 14)]))
        self.assertTrue(again.complete)

    def test_what_it_builds_takes_a_build_and_reads_back_from_disk(self):
        result = self.save.create_chunks((640, 0, 640), (671, 0, 655))
        self.assertEqual((result.chunks_created, result.regions_created), (2, 1))
        self.assertEqual((result.data_version, result.min_y, result.max_y, result.biome),
                         (4903, -64, 319, "minecraft:plains"))
        self.save.fill((640, 60, 640), (660, 62, 650), "minecraft:stone")
        self.assertTrue(self.save.place_block(645, 63, 645, "minecraft:oak_fence"))
        self.save.rebuild_heightmaps((640, 0, 640), (671, 0, 655))
        self.save.save()
        self.assertTrue(os.path.exists(os.path.join(
            self.directory, "dimensions", "minecraft", "overworld", "region", "r.1.1.mca")))
        again = MinecraftSave.load(self.directory)
        self.assertEqual(str(again.get_block(650, 62, 645)), "minecraft:stone")
        self.assertEqual(str(again.get_block(661, 62, 645)), "minecraft:air")
        self.assertEqual(again.surface_y(650, 645), 62)
        self.assertEqual(again.dimension().chunk_status(41, 40), "minecraft:full")
        # a built region round-trips byte for byte like any other
        path = os.path.join(self.directory, "dimensions", "minecraft", "overworld",
                            "region", "r.1.1.mca")
        with open(path, "rb") as handle:
            self.assertEqual(NBT.load(path).to_bytes(), handle.read())

    def test_a_dimension_never_visited_is_made_at_its_own_height(self):
        result = self.save.create_chunks((0, 0, 0), (15, 0, 15), dimension=NETHER)
        self.assertEqual((result.min_y, result.max_y, result.biome),
                         (0, 255, "minecraft:nether_wastes"))
        end = self.save.create_chunks((0, 0, 0), (0, 0, 0), dimension=END)
        self.assertEqual(end.biome, "minecraft:the_end")
        self.save.save()
        again = MinecraftSave.load(self.directory)
        self.assertEqual(again.dimension(NETHER).chunk_status(0, 0), "minecraft:full")
        self.assertEqual(sorted(again.dimensions), sorted([OVERWORLD, NETHER, END]))

    def test_the_height_is_read_off_the_dimension_s_own_chunks(self):
        dim = self.save.dimension(OVERWORLD)
        self.assertEqual(dim.section_range(), (-4, 19))

    def test_the_stamp_is_the_world_s_own_or_26_3_s(self):
        result = self.save.create_chunks((640, 0, 640), (640, 0, 640))
        self.assertEqual(result.data_version, 4903)
        newer = _world(4903)
        self.addCleanup(shutil.rmtree, newer, ignore_errors=True)
        _restamp(newer, 5100)
        built = MinecraftSave.load(newer).create_chunks((640, 0, 640), (640, 0, 640))
        self.assertEqual(built.data_version, EMPTY_CHUNK_MAX_DATA_VERSION)
        older = _world(4903)
        self.addCleanup(shutil.rmtree, older, ignore_errors=True)
        _restamp(older, 4440)
        with self.assertRaisesRegex(NBTError, "older than the oldest finished chunk"):
            MinecraftSave.load(older).create_chunks((640, 0, 640), (640, 0, 640))

    def test_a_26_3_world_gets_26_3_chunks(self):
        directory = _world(5023)
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        save = MinecraftSave.load(directory)
        result = save.create_chunks((-416, 0, 0), (-401, 0, 15))
        self.assertEqual((result.chunks_created, result.data_version), (1, 5023))
        palette = save.dimension().chunk(-26, 0).data["sections"][0]["block_states"]["palette"]
        self.assertEqual(list(palette), ["minecraft:air"])     # the bare-id codec

    def test_one_chunk_is_refused_where_one_stands(self):
        dim = self.save.dimension(OVERWORLD)
        for chunk in ((-28, 14), (-32, 28)):
            with self.assertRaisesRegex(NBTError, "already exists"):
                dim.create_chunk(*chunk, data_version=4903, biome="minecraft:plains")
        built = dim.create_chunk(100, 100, data_version=4903, biome="minecraft:desert")
        self.assertEqual(built.get_biome(0, 0, 0), "minecraft:desert")
        self.assertIs(dim.blocks(100, 100), built)

    def test_a_region_built_and_left_empty_is_not_written(self):
        dim = self.save.dimension(OVERWORLD)
        dim.region(9, 9, create=True)
        self.save.save()
        self.assertFalse(os.path.exists(os.path.join(
            self.directory, "dimensions", "minecraft", "overworld", "region", "r.9.9.mca")))


if __name__ == "__main__":
    unittest.main()
