"""``MinecraftSave.replace_blocks``: a state-for-state rewrite over a box.

What is held here is what separates it from ``fill``: it rewrites only
what its mapping names, it matches a stored state as the 26.3 codec reads
one, it stays out of chunks the game has not finished and chunks it may
only read, and it takes away the block entities its rewrites orphan --
while keeping the ones a change of properties leaves in the right block.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from fixtures import (
    FIXTURE_LEVEL_DAT,
    FIXTURE_REGION,
    LEGACY_LEVEL_DAT,
    LEGACY_REGION,
    MODERN_LEVEL_DAT,
    MODERN_REGION,
)
from heaton_voxel.blocks import BlockState
from heaton_voxel.nbt import NBTError, encode
from heaton_voxel.world import OVERWORLD, MinecraftSave, ReplaceResult

# The 26.2 fixture's village chunk, (-28, 14): a smoker at y=64 and a
# campfire at y=67 over dirt and grass, each with its block entity.
VILLAGE = ((-448, 60, 224), (-440, 70, 232))
SMOKER = (-446, 64, 225)
CAMPFIRE = (-446, 67, 225)
CAMPFIRE_STATE = ("minecraft:campfire[facing=north,lit=true,"
                  "signal_fire=false,waterlogged=false]")
# Its top section, all air.
TOP_SECTION = ((-448, 304, 224), (-433, 319, 239))
# Two chunks the region never generated, (-33, 28) and (-33, 29), and two
# the game has not finished, (-32, 28) at initialize_light and (-32, 29)
# at carvers.
EDGE = ((-520, 60, 450), (-500, 62, 470))
# The 26.3 fixture's chunk (-13, 19): a double chest pair at x -201/-200
# and -198/-197, and two furnaces at -205 and -204, all at (y=64, z=311).
CHEST_ROW = ((-206, 64, 311), (-197, 64, 311))


def _load(region: str, level: str, test: unittest.TestCase) -> MinecraftSave:
    directory = tempfile.mkdtemp(prefix="replace-")
    test.addCleanup(shutil.rmtree, directory, ignore_errors=True)
    region_dir = os.path.join(directory, "dimensions", "minecraft", "overworld", "region")
    os.makedirs(region_dir)
    shutil.copyfile(region, os.path.join(region_dir, os.path.basename(region)))
    shutil.copyfile(level, os.path.join(directory, "level.dat"))
    return MinecraftSave.load(directory, dimensions=[OVERWORLD])


def _entity_ids(save: MinecraftSave, chunk: tuple[int, int]) -> dict:
    data = save.dimension(OVERWORLD).chunk(*chunk).data
    return {(int(e["x"]), int(e["y"]), int(e["z"])): str(e["id"])
            for e in data.get("block_entities") or ()}


def _payload(save: MinecraftSave, chunk: tuple[int, int]) -> bytes:
    found = save.dimension(OVERWORLD).chunk(*chunk)
    return encode(found.data, found.data_name)


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.save = _load(FIXTURE_REGION, FIXTURE_LEVEL_DAT, self)

    def test_only_the_named_states_change(self):
        before = self.save.histogram(*VILLAGE).counts
        result = self.save.replace_blocks(*VILLAGE, {
            "minecraft:dirt": "minecraft:coarse_dirt",
            "minecraft:grass_block[snowy=false]": "minecraft:moss_block",
        }, keep_loaded=True)
        after = self.save.histogram(*VILLAGE).counts

        dirt = before[BlockState("minecraft:dirt")]
        grass = before[BlockState("minecraft:grass_block", {"snowy": "false"})]
        self.assertIsInstance(result, ReplaceResult)
        self.assertEqual(result.blocks_replaced, dirt + grass)
        self.assertEqual(result.chunks_modified, 1)
        self.assertTrue(result.complete)
        self.assertNotIn(BlockState("minecraft:dirt"), after)
        self.assertEqual(after[BlockState("minecraft:coarse_dirt")], dirt)
        self.assertEqual(after[BlockState("minecraft:moss_block")], grass)
        for state, count in before.items():
            if state.name not in ("minecraft:dirt", "minecraft:grass_block"):
                self.assertEqual(after[state], count, state)

    def test_nothing_outside_the_box_moves(self):
        (x0, y0, z0), (x1, y1, z1) = VILLAGE
        outside = [(x0 - 1, y0, z0), (x1 + 1, y0, z0), (x0, y0 - 1, z0),
                   (x0, y1 + 1, z0), (x0, y0, z0 - 1), (x0, y0, z1 + 1)]
        kept = [self.save.get_block(*cell) for cell in outside]
        self.save.replace_blocks(*VILLAGE, {
            str(state): "minecraft:glass"
            for state in self.save.histogram(
                (x0 - 1, y0 - 1, z0 - 1), (x1 + 1, y1 + 1, z1 + 1)).counts
        }, keep_loaded=True)
        self.assertEqual([self.save.get_block(*cell) for cell in outside], kept)
        self.assertEqual(set(self.save.histogram(*VILLAGE).counts),
                         {BlockState("minecraft:glass")})

    def test_a_key_names_a_state_with_its_defaults_filled_in(self):
        save = _load(MODERN_REGION, MODERN_LEVEL_DAT, self)
        result = save.replace_blocks(*CHEST_ROW, {
            # The furnaces stand at their default, which 26.3 stores bare.
            "minecraft:furnace": "minecraft:blast_furnace",
            # A key that leaves facing and waterlogged out means the default.
            "minecraft:chest[type=left]": "minecraft:chest[type=single]",
        }, keep_loaded=True)
        self.assertEqual(result.blocks_replaced, 4)
        self.assertEqual(str(save.get_block(-205, 64, 311)),
                         "minecraft:blast_furnace[facing=north,lit=false]")
        self.assertEqual(str(save.get_block(-201, 64, 311)),
                         "minecraft:chest[facing=north,type=single,waterlogged=false]")
        # the right halves were not named
        self.assertEqual(str(save.get_block(-200, 64, 311)),
                         "minecraft:chest[facing=north,type=right,waterlogged=false]")

    def test_a_key_without_a_namespace_is_the_game_s(self):
        result = self.save.replace_blocks(*VILLAGE, {"dirt": "minecraft:coarse_dirt"},
                                          keep_loaded=True)
        self.assertGreater(result.blocks_replaced, 0)
        self.assertNotIn(BlockState("minecraft:dirt"),
                         self.save.histogram(*VILLAGE).counts)

    def test_a_target_is_written_with_its_defaults(self):
        self.save.replace_blocks(*VILLAGE, {"minecraft:dirt": "minecraft:oak_stairs"},
                                 keep_loaded=True)
        self.assertEqual(
            str(self.save.get_block(-448, 60, 224)),
            "minecraft:oak_stairs[facing=north,half=bottom,shape=straight,"
            "waterlogged=false]")

    def test_one_target_spelled_two_ways_enters_a_palette_once(self):
        self.save.replace_blocks(*VILLAGE, {
            "minecraft:dirt": "minecraft:oak_stairs",
            "minecraft:grass_block[snowy=false]":
                "minecraft:oak_stairs[facing=north,half=bottom,shape=straight,"
                "waterlogged=false]",
        }, keep_loaded=True)
        container = self.save.dimension(OVERWORLD).blocks(-28, 14).sections[3].blocks
        stairs = [state for state in container.palette
                  if state.name == "minecraft:oak_stairs"]
        self.assertEqual(len(stairs), 1)

    def test_a_state_mapped_to_itself_writes_nothing(self):
        before = _payload(self.save, (-28, 14))
        result = self.save.replace_blocks(*VILLAGE, {
            "minecraft:dirt": "minecraft:dirt",
            "minecraft:grass_block": "minecraft:grass_block[snowy=false]",
        }, keep_loaded=True)
        self.assertEqual(result.blocks_replaced, 0)
        self.assertEqual(result.chunks_modified, 0)
        self.assertEqual(_payload(self.save, (-28, 14)), before)

    def test_a_whole_section_mapped_to_one_block_collapses(self):
        result = self.save.replace_blocks(*TOP_SECTION, {"minecraft:air": "minecraft:glass"},
                                          keep_loaded=True)
        self.assertEqual(result.sections_replaced, 1)
        self.assertEqual(result.blocks_replaced, 4096)
        container = self.save.dimension(OVERWORLD).blocks(-28, 14).sections[19].blocks
        self.assertEqual(container.palette, [BlockState("minecraft:glass")])

    def test_cells_past_the_world_are_counted_not_written(self):
        result = self.save.replace_blocks((-448, 318, 224), (-448, 321, 224),
                                          {"minecraft:air": "minecraft:glass"},
                                          keep_loaded=True)
        self.assertEqual(result.blocks_replaced, 2)
        self.assertEqual(result.blocks_out_of_range, 2)
        self.assertFalse(result.complete)


class BlockEntityTests(unittest.TestCase):
    def test_a_change_of_id_drops_the_entity_and_of_properties_keeps_it(self):
        save = _load(FIXTURE_REGION, FIXTURE_LEVEL_DAT, self)
        result = save.replace_blocks(*VILLAGE, {
            "minecraft:smoker[facing=south,lit=false]":
                "minecraft:smoker[facing=north,lit=false]",
            CAMPFIRE_STATE: CAMPFIRE_STATE.replace("campfire", "soul_campfire"),
        }, keep_loaded=True)
        self.assertEqual(result.block_entities_removed, 1)
        self.assertEqual(_entity_ids(save, (-28, 14)), {SMOKER: "minecraft:smoker"})

    def test_chests_keep_theirs_and_furnaces_lose_theirs(self):
        save = _load(MODERN_REGION, MODERN_LEVEL_DAT, self)
        before = _entity_ids(save, (-13, 19))
        result = save.replace_blocks(*CHEST_ROW, {
            "minecraft:furnace": "minecraft:blast_furnace",
            "minecraft:chest[type=left]": "minecraft:chest[type=single]",
        }, keep_loaded=True)
        after = _entity_ids(save, (-13, 19))
        self.assertEqual(result.block_entities_removed, 2)
        self.assertEqual(set(before) - set(after), {(-205, 64, 311), (-204, 64, 311)})
        for cell in ((-201, 64, 311), (-198, 64, 311)):
            self.assertEqual(after[cell], "minecraft:chest")

    def test_an_entity_outside_the_box_is_left(self):
        save = _load(FIXTURE_REGION, FIXTURE_LEVEL_DAT, self)
        result = save.replace_blocks((-446, 66, 225), (-446, 66, 225),
                                     {CAMPFIRE_STATE: "minecraft:air"},
                                     keep_loaded=True)
        self.assertEqual(result.block_entities_removed, 0)
        self.assertEqual(len(_entity_ids(save, (-28, 14))), 2)


class ChunkTests(unittest.TestCase):
    def test_unfinished_and_missing_chunks_are_left_as_they_are(self):
        save = _load(FIXTURE_REGION, FIXTURE_LEVEL_DAT, self)
        before = {chunk: _payload(save, chunk) for chunk in ((-32, 28), (-32, 29))}
        result = save.replace_blocks(*EDGE, {"minecraft:stone": "minecraft:granite",
                                             "minecraft:air": "minecraft:glass"},
                                     keep_loaded=True)
        self.assertEqual(sorted(result.missing_chunks), [(-33, 28), (-33, 29)])
        self.assertEqual(sorted(result.unfinished_chunks), [(-32, 28), (-32, 29)])
        self.assertEqual(result.blocks_replaced, 0)
        self.assertEqual(result.chunks_modified, 0)
        self.assertEqual(result.blocks_out_of_range, 21 * 3 * 21)
        self.assertFalse(result.complete)
        for chunk, payload in before.items():
            self.assertEqual(_payload(save, chunk), payload, chunk)

    def test_a_chunk_minecraft_1_17_saved_is_refused_as_unreadable(self):
        save = _load(LEGACY_REGION, LEGACY_LEVEL_DAT, self)
        dim = save.dimension(OVERWORLD)
        (region,) = list(dim)
        chunk_x, chunk_z = next(iter(sorted(
            (region.region_x * 32 + x, region.region_z * 32 + z)
            for (x, z) in region.chunks)))
        low = (chunk_x * 16, 60, chunk_z * 16)
        high = (chunk_x * 16 + 3, 62, chunk_z * 16 + 3)
        before = _payload(save, (chunk_x, chunk_z))
        result = save.replace_blocks(low, high, {"minecraft:stone": "minecraft:granite"},
                                     keep_loaded=True)
        self.assertEqual([chunk for chunk, _ in result.unreadable_chunks],
                         [(chunk_x, chunk_z)])
        self.assertEqual(result.blocks_out_of_range, 4 * 3 * 4)
        self.assertEqual(_payload(save, (chunk_x, chunk_z)), before)


class DeterminismTests(unittest.TestCase):
    MAPPING = {
        "minecraft:dirt": "minecraft:coarse_dirt",
        "minecraft:grass_block[snowy=false]": "minecraft:moss_block",
        "minecraft:cobblestone": "minecraft:mossy_cobblestone",
        "minecraft:air": "minecraft:glass",
    }

    def test_the_same_replace_writes_the_same_bytes(self):
        payloads = []
        for _ in range(2):
            save = _load(FIXTURE_REGION, FIXTURE_LEVEL_DAT, self)
            save.replace_blocks(*VILLAGE, self.MAPPING, keep_loaded=True)
            payloads.append(_payload(save, (-28, 14)))
        self.assertEqual(payloads[0], payloads[1])

    def test_what_is_saved_reads_back(self):
        save = _load(FIXTURE_REGION, FIXTURE_LEVEL_DAT, self)
        save.replace_blocks(*VILLAGE, self.MAPPING)
        expected = save.histogram(*VILLAGE).counts
        save.save()
        again = MinecraftSave.load(save.path, dimensions=[OVERWORLD])
        self.assertEqual(again.histogram(*VILLAGE).counts, expected)


class RefusalTests(unittest.TestCase):
    def setUp(self):
        self.save = _load(FIXTURE_REGION, FIXTURE_LEVEL_DAT, self)

    def test_one_state_replaced_two_ways_is_refused(self):
        with self.assertRaises(NBTError) as caught:
            self.save.replace_blocks(*VILLAGE, {
                "minecraft:grass_block": "minecraft:dirt",
                "minecraft:grass_block[snowy=false]": "minecraft:sand",
            })
        self.assertIn("two ways", str(caught.exception))

    def test_a_key_that_is_no_state_is_refused(self):
        with self.assertRaises(NBTError):
            self.save.replace_blocks(*VILLAGE, {42: "minecraft:dirt"})  # type: ignore[dict-item]
        with self.assertRaises(NBTError):
            self.save.replace_blocks(*VILLAGE, {"minecraft:dirt[": "minecraft:stone"})


if __name__ == "__main__":
    unittest.main()
