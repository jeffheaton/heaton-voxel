"""The 26.3 block-state codec: bare ids, wrappers, ``id``/``properties``.

From DataVersion 5006 a palette stores a state at its block's defaults as
the bare id -- ``minecraft:water`` is ``water[level=0]`` -- and only a state
that differs as ``{id, properties}``; a list holding both wraps each bare id
as ``{"": id}``. These tests pin the three forms on synthetic containers and
then on ``fixtures/modern/``, four chunks of real 26.3 output, where the
byte-exact promise has to hold against what the game actually wrote.

One of those chunks also stores a section of light alone below the world,
which is the case that pins where a chunk's range starts and ends:
``WorldRangeTests``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import zlib

import heaton_voxel.nbt as nbt
from heaton_voxel.block_defaults import default_properties
from heaton_voxel.blocks import (
    BLOCKS_PER_SECTION,
    MODERN_STATE_DATA_VERSION,
    BlockState,
    Blocks,
    PalettedContainer,
    heightmap_bits,
    pack_indices,
    unpack_indices,
)
from heaton_voxel.nbt import (
    NBT,
    NBTError,
    TagByte,
    TagByteArray,
    TagCompound,
    TagInt,
    TagList,
    TagString,
    encode,
)
from heaton_voxel.world import OVERWORLD, MinecraftSave

from fixtures import MODERN_LEVEL_DAT, MODERN_REGION, snapshot_region

REGION = None
_SNAPSHOT_DIR = None


def setUpModule():
    global REGION, _SNAPSHOT_DIR
    REGION, _SNAPSHOT_DIR = snapshot_region(MODERN_REGION)


def tearDownModule():
    if _SNAPSHOT_DIR:
        shutil.rmtree(_SNAPSHOT_DIR, ignore_errors=True)


def _full(name: str, **properties: str) -> TagCompound:
    """A ``{id, properties}`` entry as the game writes it."""
    tag = TagCompound()
    tag["id"] = TagString(name)
    tag["properties"] = TagCompound({k: TagString(v) for k, v in properties.items()})
    return tag


def _wrapped(name: str) -> TagCompound:
    return TagCompound({"": TagString(name)})


def _strings(*names: str) -> TagCompound:
    """A section container whose palette is a string list."""
    tag = TagCompound()
    tag["palette"] = TagList([TagString(n) for n in names], element_type=nbt.TAG_STRING)
    return tag


def _modern(tag: TagCompound) -> PalettedContainer:
    return PalettedContainer(tag, BLOCKS_PER_SECTION, 4, modern_states=True)


def _forms(palette: TagList) -> list[str]:
    """How each stored entry is shaped: ``string``, ``wrapped`` or ``full``."""
    out = []
    for entry in palette:
        if isinstance(entry, TagString):
            out.append("string")
        elif isinstance(entry, TagCompound) and set(entry) == {""}:
            out.append("wrapped")
        elif isinstance(entry, TagCompound) and "id" in entry:
            out.append("full")
        else:
            out.append(f"other:{entry!r}")
    return out


class DecodeTests(unittest.TestCase):
    def test_bare_id_is_the_default_state(self):
        state = BlockState.from_nbt(TagString("minecraft:grass_block"))
        self.assertEqual(state, BlockState("minecraft:grass_block", {"snowy": "false"}))
        self.assertEqual(
            BlockState.from_nbt(TagString("minecraft:water")),
            BlockState.parse("minecraft:water[level=0]"),
        )
        self.assertEqual(BlockState.from_nbt(TagString("minecraft:stone")).properties, {})

    def test_wrapper_reads_like_the_bare_id(self):
        self.assertEqual(
            BlockState.from_nbt(_wrapped("minecraft:oak_fence")),
            BlockState.from_nbt(TagString("minecraft:oak_fence")),
        )
        self.assertEqual(
            sorted(BlockState.from_nbt(_wrapped("minecraft:oak_fence")).properties),
            ["east", "north", "south", "waterlogged", "west"],
        )

    def test_full_form_reads_complete_and_in_name_order(self):
        state = BlockState.from_nbt(_full("minecraft:sea_pickle", waterlogged="true", pickles="4"))
        self.assertEqual(list(state.properties), ["pickles", "waterlogged"])
        self.assertEqual(state.properties["pickles"], "4")

    def test_full_form_fills_what_it_leaves_out(self):
        partial = TagCompound({"id": TagString("minecraft:oak_stairs")})
        self.assertEqual(
            BlockState.from_nbt(partial).properties,
            default_properties("minecraft:oak_stairs"),
        )
        half = _full("minecraft:oak_stairs", facing="south")
        state = BlockState.from_nbt(half)
        self.assertEqual(state.properties["facing"], "south")
        self.assertEqual(state.properties["shape"], "straight")

    def test_unknown_block_keeps_exactly_what_it_was_given(self):
        self.assertEqual(BlockState.from_nbt(TagString("example:widget")).properties, {})
        state = BlockState.from_nbt(_full("example:widget", b="1", a="2"))
        self.assertEqual(list(state.properties.items()), [("a", "2"), ("b", "1")])

    def test_legacy_entry_is_taken_as_stored(self):
        """The old codec wrote every property itself, so nothing is added:
        a library-written {Name} without Properties reads as it always has."""
        state = BlockState.from_nbt(TagCompound({"Name": TagString("minecraft:grass_block")}))
        self.assertEqual(state.properties, {})

    def test_the_source_tag_survives_every_form(self):
        for tag in (TagString("minecraft:stone"), _wrapped("minecraft:stone"),
                    _full("minecraft:water", level="8"),
                    TagCompound({"Name": TagString("minecraft:stone")})):
            with self.subTest(tag=tag):
                self.assertIs(BlockState.from_nbt(tag)._source, tag)

    def test_anything_else_is_refused(self):
        for tag in (TagCompound({"foo": TagString("bar")}), TagInt(3),
                    TagCompound({"": TagInt(3)})):
            with self.subTest(tag=tag):
                with self.assertRaises(NBTError):
                    BlockState.from_nbt(tag)


class EncodeTests(unittest.TestCase):
    def test_a_default_state_encodes_bare(self):
        for text in ("minecraft:water", "minecraft:water[level=0]", "minecraft:stone",
                     "minecraft:oak_stairs[facing=north]"):
            with self.subTest(text=text):
                tag = BlockState.parse(text).to_nbt(modern=True)
                self.assertIsInstance(tag, TagString)
                self.assertEqual(str(tag), text.split("[")[0])

    def test_any_other_state_encodes_complete_and_ordered(self):
        tag = BlockState.parse("minecraft:sea_pickle[pickles=4]").to_nbt(modern=True)
        self.assertEqual(list(tag), ["id", "properties"])
        self.assertEqual(str(tag["id"]), "minecraft:sea_pickle")
        self.assertEqual(
            [(k, str(v)) for k, v in tag["properties"].items()],
            [("pickles", "4"), ("waterlogged", "true")],
        )

    def test_unknown_block_encodes_what_it_has(self):
        self.assertIsInstance(BlockState.parse("example:widget").to_nbt(modern=True), TagString)
        tag = BlockState.parse("example:widget[b=1,a=2]").to_nbt(modern=True)
        self.assertEqual(list(tag["properties"]), ["a", "b"])

    def test_legacy_encoding_is_unchanged(self):
        tag = BlockState.parse("minecraft:oak_fence").to_nbt()
        self.assertEqual(list(tag), ["Name"])
        tag = BlockState.parse("minecraft:oak_stairs[facing=south]").to_nbt()
        self.assertEqual(list(tag), ["Properties", "Name"])
        self.assertEqual(list(tag["Properties"]), ["facing"])

    def test_a_modern_source_is_not_reused_for_a_legacy_write(self):
        """A state read out of a 26.3 chunk and written into an older one
        must come out in that chunk's own form, not as the string it came in as."""
        state = BlockState.from_nbt(TagString("minecraft:grass_block"))
        tag = state.to_nbt()
        self.assertIsInstance(tag, TagCompound)
        self.assertEqual(str(tag["Name"]), "minecraft:grass_block")
        self.assertEqual(str(tag["Properties"]["snowy"]), "false")

    def test_canonical_fills_and_orders(self):
        state = BlockState.parse("minecraft:oak_stairs[waterlogged=true,facing=south]")
        canonical = state.canonical()
        self.assertEqual(list(canonical.properties), ["facing", "half", "shape", "waterlogged"])
        self.assertEqual(canonical.properties["facing"], "south")
        self.assertIs(canonical.canonical(), canonical)
        stone = BlockState.parse("minecraft:stone")
        self.assertIs(stone.canonical(), stone)


class ContainerTests(unittest.TestCase):
    def test_default_states_keep_a_string_list(self):
        container = _modern(_strings("minecraft:stone"))
        container.set(0, "minecraft:water")
        container.set(1, BlockState.parse("minecraft:grass_block[snowy=false]"))
        palette = container._tag["palette"]
        self.assertEqual(palette.element_type, nbt.TAG_STRING)
        self.assertEqual(_forms(palette), ["string", "string", "string"])
        self.assertEqual(container.get(1), BlockState.parse("minecraft:grass_block[snowy=false]"))

    def test_a_non_default_state_widens_the_list(self):
        container = _modern(_strings("minecraft:stone", "minecraft:water"))
        container.set(7, "minecraft:water[level=8]")
        palette = container._tag["palette"]
        self.assertEqual(palette.element_type, nbt.TAG_COMPOUND)
        self.assertEqual(_forms(palette), ["wrapped", "wrapped", "full"])
        self.assertEqual(str(palette[0][""]), "minecraft:stone")
        self.assertEqual(container.get(7), BlockState.parse("minecraft:water[level=8]"))
        # A fresh parse of what was written reads the same.
        fresh = _modern(container._tag)
        self.assertEqual(fresh.get(7), BlockState.parse("minecraft:water[level=8]"))
        self.assertEqual(fresh.get(0), BlockState.parse("minecraft:stone"))

    def test_a_default_state_joins_a_compound_list_wrapped(self):
        container = _modern(_strings("minecraft:stone"))
        container.set(1, "minecraft:water[level=8]")
        container.set(2, "minecraft:sand")
        self.assertEqual(_forms(container._tag["palette"]), ["wrapped", "full", "wrapped"])
        self.assertEqual(container.get(2), BlockState.parse("minecraft:sand"))

    def test_bare_and_spelled_out_defaults_share_one_entry(self):
        container = _modern(_strings("minecraft:stone", "minecraft:water"))
        container.set(0, BlockState.parse("minecraft:water[level=0]"))
        container.set(1, BlockState.parse("minecraft:water"))
        self.assertEqual(len(container.palette), 2)
        self.assertEqual(container.get(0), container.get(1))

    def test_fill_all_and_set_all_choose_the_list_form(self):
        container = _modern(_strings("minecraft:stone"))
        container.fill_all("minecraft:water")
        self.assertEqual(_forms(container._tag["palette"]), ["string"])
        container.fill_all("minecraft:water[level=8]")
        self.assertEqual(_forms(container._tag["palette"]), ["full"])
        values = [BlockState.parse("minecraft:stone")] * BLOCKS_PER_SECTION
        values[5] = BlockState.parse("minecraft:sea_pickle[pickles=4]")
        container.set_all(values)
        self.assertEqual(_forms(container._tag["palette"]), ["wrapped", "full"])
        container.set_all([BlockState.parse("minecraft:sand")] * (BLOCKS_PER_SECTION - 1)
                          + [BlockState.parse("minecraft:stone")])
        self.assertEqual(container._tag["palette"].element_type, nbt.TAG_STRING)

    def test_prune_keeps_the_stored_form(self):
        container = _modern(_strings("minecraft:stone"))
        container.set(1, "minecraft:water[level=8]")
        container.set(1, "minecraft:stone")
        self.assertEqual(container.prune(), 1)
        palette = container._tag["palette"]
        self.assertEqual(palette.element_type, nbt.TAG_COMPOUND)
        self.assertEqual(_forms(palette), ["wrapped"])

    def test_a_legacy_container_never_sees_the_new_form(self):
        tag = TagCompound()
        tag["palette"] = TagList([BlockState("minecraft:stone").to_nbt()],
                                 element_type=nbt.TAG_COMPOUND)
        container = PalettedContainer(tag, BLOCKS_PER_SECTION, 4)
        container.set(0, "minecraft:water[level=8]")
        container.set(1, "minecraft:water")
        self.assertEqual(
            [list(entry) for entry in container._tag["palette"]],
            [["Name"], ["Properties", "Name"], ["Name"]],
        )

    def test_a_biome_container_is_never_modern(self):
        tag = TagCompound()
        tag["palette"] = TagList([TagString("minecraft:plains")], element_type=nbt.TAG_STRING)
        container = PalettedContainer(tag, 64, 1, entries_are_compounds=False,
                                      modern_states=True)
        container.set(3, "minecraft:snowy_taiga")
        self.assertEqual(container.get(3), "minecraft:snowy_taiga")
        self.assertEqual(_forms(container._tag["palette"]), ["string", "string"])

    def test_blocks_chooses_the_codec_by_data_version(self):
        for version, modern in ((MODERN_STATE_DATA_VERSION, True),
                                (MODERN_STATE_DATA_VERSION - 1, False), (0, False)):
            with self.subTest(version=version):
                data = TagCompound({"DataVersion": TagInt(version), "sections": TagList([])})
                self.assertEqual(Blocks(data).modern_states, modern)


class ModernFixtureTests(unittest.TestCase):
    """Real 26.3 output: what the game wrote must read, and come back."""

    @classmethod
    def setUpClass(cls):
        with open(REGION, "rb") as handle:
            cls.original = handle.read()
        cls.region = NBT.load(REGION)

    def _sections(self, region=None):
        for chunk in (region or self.region):
            blocks = Blocks(chunk)
            for section in blocks:
                if section.blocks is not None:
                    yield chunk, blocks, section

    def test_every_chunk_is_modern(self):
        versions = {int(chunk.data["DataVersion"]) for chunk in self.region}
        self.assertTrue(versions, "no chunks in the fixture")
        self.assertGreaterEqual(min(versions), MODERN_STATE_DATA_VERSION)
        self.assertTrue(all(Blocks(chunk).modern_states for chunk in self.region))

    def test_the_fixture_holds_every_entry_form(self):
        seen = set()
        for _, _, section in self._sections():
            seen.update(_forms(section.blocks._tag["palette"]))
        self.assertEqual(seen, {"string", "wrapped", "full"})

    def test_every_known_state_reads_complete_and_ordered(self):
        checked = 0
        for _, _, section in self._sections():
            for state in section.blocks.palette:
                expected = default_properties(state.name)
                if not expected:
                    continue
                self.assertEqual(list(state.properties), list(expected), state)
                checked += 1
        self.assertGreater(checked, 100)

    def test_reading_re_encodes_exactly(self):
        checked = 0
        for chunk, _, section in self._sections():
            section.blocks.palette
            section.biomes and section.biomes.palette
        for chunk in self.region:
            self.assertEqual(chunk.compression, 2, "the fixture is zlib-compressed")
            self.assertEqual(encode(chunk.data, chunk.data_name),
                             zlib.decompress(chunk._blob),
                             f"chunk ({chunk.local_x}, {chunk.local_z})")
            checked += 1
        self.assertGreater(checked, 0)
        self.assertEqual(self.region.to_bytes(), self.original)

    def test_repacking_reproduces_the_games_own_bytes(self):
        checked = 0
        for _, _, section in self._sections():
            for container in (section.blocks, section.biomes):
                if container is None or "data" not in container._tag:
                    continue
                original = list(container._tag["data"])
                repacked = list(pack_indices(container.indices(), container._count,
                                             container.bits))
                first = next((i for i, (a, b) in enumerate(zip(repacked, original))
                              if a != b), None)
                # Report the first mismatch, never the whole 1024-word diff.
                self.assertIsNone(first, f"{section}: word {first} differs")
                self.assertEqual(len(repacked), len(original), f"{section}")
                checked += 1
        self.assertGreater(checked, 0)

    def test_an_edit_survives_save_and_reload(self):
        # A section whose palette is still a string list, so the write has
        # to widen it -- the case a legacy reader would corrupt. Edits go
        # through a private load so the class's own copy stays pristine.
        region = NBT.load(REGION)
        target = None
        for chunk, blocks, section in self._sections(region):
            if section.blocks._tag["palette"].element_type == nbt.TAG_STRING \
                    and len(section.blocks.palette) > 1:
                target = (chunk, blocks, section)
                break
        self.assertIsNotNone(target, "no multi-entry string-list section in the fixture")
        chunk, blocks, section = target
        y = section.y * 16 + 3
        pickle = BlockState.parse("minecraft:sea_pickle[pickles=4]")
        blocks[2, y, 5] = pickle
        blocks[3, y, 5] = "minecraft:water"
        forms = _forms(section.blocks._tag["palette"])
        self.assertEqual(forms[-2:], ["full", "wrapped"])
        self.assertTrue(all(form != "string" for form in forms))
        region.save()

        reloaded = Blocks(NBT.load(REGION).chunk(chunk.local_x, chunk.local_z))
        self.assertEqual(reloaded[2, y, 5],
                         BlockState.parse("minecraft:sea_pickle[pickles=4,waterlogged=true]"))
        self.assertEqual(reloaded[3, y, 5], BlockState.parse("minecraft:water[level=0]"))
        self.assertEqual(_forms(reloaded.sections[section.y].blocks._tag["palette"]), forms)

    def test_a_spelled_out_default_finds_the_bare_entry(self):
        for _, blocks, section in self._sections(NBT.load(REGION)):
            palette = section.blocks.palette
            if BlockState.parse("minecraft:water[level=0]") in palette:
                before = len(palette)
                blocks[0, section.y * 16, 0] = BlockState.parse("minecraft:water[level=0]")
                self.assertEqual(len(section.blocks.palette), before)
                return
        self.skipTest("no section holding source water")


#: The chunk of the modern fixture whose sections run from Y=-5: the game
#: stored a section of light alone below the world there.
LIGHT_BELOW = (-13, 19)

#: Empty, as far as a heightmap is concerned (the game's ``#air`` tag).
_AIR = ("minecraft:air", "minecraft:cave_air", "minecraft:void_air")


def _top(blocks: Blocks, lx: int, lz: int) -> int | None:
    """The topmost non-air block of a column, found by walking it."""
    for y in range(blocks.max_y, blocks.min_y - 1, -1):
        if blocks.get(lx, y, lz).name not in _AIR:
            return y
    return None


class WorldRangeTests(unittest.TestCase):
    """The world's floor and ceiling are not the ends of the stored sections.

    The game keeps light one section past each end of the world, so a chunk
    can store a section at Y=-5 or Y=20 holding light and no blocks. The
    floor every heightmap counts from is ``yPos * 16``, and nothing can be
    written above the highest section that carries blocks.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="modern-world-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        region_dir = os.path.join(self.dir, "dimensions", "minecraft",
                                  "overworld", "region")
        os.makedirs(region_dir)
        self.region_path = os.path.join(region_dir, "r.-1.0.mca")
        shutil.copyfile(REGION, self.region_path)
        shutil.copyfile(MODERN_LEVEL_DAT, os.path.join(self.dir, "level.dat"))

    def test_a_light_only_section_below_the_world_is_not_the_heightmap_origin(self):
        chunk = NBT.load(self.region_path).chunk(LIGHT_BELOW[0] & 31, LIGHT_BELOW[1] & 31)
        blocks = Blocks(chunk)
        below = blocks.sections[-5]
        self.assertIsNone(below.blocks, "fixture drifted: Y=-5 should hold light alone")
        self.assertIsNotNone(below.sky_light())
        self.assertEqual(int(chunk.data["yPos"]) * 16, -64)
        self.assertEqual((blocks.min_y, blocks.max_y), (-64, 319))
        wrong = []
        for lz in range(16):
            for lx in range(16):
                stored, top = blocks.surface_y(lx, lz, "WORLD_SURFACE"), _top(blocks, lx, lz)
                if stored != top:
                    wrong.append(((lx, lz), stored, top))
        # The count and the first few, never a 256-entry diff.
        self.assertEqual(len(wrong), 0, f"columns as (cell, map, top): {wrong[:4]}")

    def test_a_rebuilt_surface_counts_from_the_worlds_floor(self):
        save = MinecraftSave.load(self.dir, dimensions=[OVERWORLD])
        dim = save.dimension(OVERWORLD)
        chunk = dim.chunk(*LIGHT_BELOW)
        bits = heightmap_bits(-64, 319)
        game = unpack_indices(chunk.data["Heightmaps"]["WORLD_SURFACE"], 256, bits)

        origin = (LIGHT_BELOW[0] * 16, 0, LIGHT_BELOW[1] * 16)
        result = save.rebuild_heightmaps(origin, origin)
        self.assertEqual(result.chunks_rebuilt, 1)
        rebuilt = unpack_indices(chunk.data["Heightmaps"]["WORLD_SURFACE"], 256, bits)
        # WORLD_SURFACE is format knowledge, so a rebuild over blocks nobody
        # touched reproduces the map the game wrote, entry for entry.
        differ = [(i, rebuilt[i], game[i]) for i in range(256) if rebuilt[i] != game[i]]
        self.assertEqual(len(differ), 0,
                         f"entries as (column, rebuilt, game): {differ[:4]}")

    def test_below_the_floor_is_nothing_even_where_light_is_stored(self):
        save = MinecraftSave.load(self.dir, dimensions=[OVERWORLD])
        x, z = LIGHT_BELOW[0] * 16 + 1, LIGHT_BELOW[1] * 16 + 1
        # The same answer as in chunk (-3, 19), which stores no section there.
        self.assertIsNone(save.get_block(x, -65, z),
                          "a section of light alone is no part of the world")
        self.assertIsNotNone(save.get_block(-3 * 16 + 1, -64, z))
        self.assertIsNone(save.get_block(-3 * 16 + 1, -65, z))
        self.assertIsNotNone(save.get_block(x, -64, z))
        # So ice on the world's floor hangs over nothing, not over air for
        # its water to fall through.
        self.assertTrue(save.set_block(x, -64, z, "minecraft:ice"))
        result = save.unfreeze((x, -64, z), (x, -64, z))
        self.assertEqual(result.ice_melted, 1)
        self.assertEqual(result.floating_water, 0)

    def test_a_light_only_section_above_the_world_takes_no_block(self):
        region = NBT.load(self.region_path)
        chunk = region.chunk(-30 & 31, 0)
        chunk.data["sections"].append(TagCompound({
            "Y": TagByte(20), "SkyLight": TagByteArray([-1] * 2048),
        }))
        region.save()

        save = MinecraftSave.load(self.dir, dimensions=[OVERWORLD])
        x, z = -478, 3
        self.assertEqual(save.get_block(x, 319, z).name, "minecraft:air")
        # A door on the top of the world would stand its upper half above
        # the build limit: refused whole, rather than leaving a lower half
        # the game breaks on its next update.
        self.assertFalse(save.place_block(x, 319, z, "minecraft:oak_door[facing=north]"))
        self.assertEqual(save.get_block(x, 319, z).name, "minecraft:air",
                         "a refused placement wrote half a door")
        self.assertFalse(save.set_block(x, 320, z, "minecraft:stone"))
        self.assertIsNone(save.get_block(x, 320, z),
                          "above the build limit is nothing, light or no light")
        blocks = save.dimension(OVERWORLD).blocks(-30, 0)
        self.assertEqual(len(blocks.sections), 25)
        self.assertEqual((blocks.min_y, blocks.max_y), (-64, 319))
