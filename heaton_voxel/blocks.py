"""Block and biome access for Minecraft chunks, on top of :mod:`heaton_voxel.nbt`.

A chunk stores its blocks as one *paletted container* per 16x16x16 section: a
palette of distinct block states, plus a bit-packed array of indices into it.
This module unpacks that representation and puts it back.

    from heaton_voxel import NBT, Blocks, BlockState

    region = NBT.load("r.0.0.mca")
    chunk = region.chunk(0, 0)

    blocks = Blocks(chunk)
    print(blocks[0, -60, 0])                       # minecraft:bedrock
    blocks[0, 64, 0] = BlockState.parse("minecraft:chest[facing=north]")
    region.save()

Coordinates are chunk-local ``x``/``z`` (0-15) and world ``y`` (e.g. -64..319),
which is how the data is actually laid out. ``Blocks.world_x`` and
``world_z`` give the chunk's origin if you need to convert.

Writes go straight through into the underlying NBT: setting a block usually
rewrites a single 64-bit word, so there is nothing to flush and no way to lose
an edit by forgetting to.

This reads and writes the current chunk format -- sections at the chunk
root, and indices packed so that no entry straddles a long -- and reads,
never writes, the ones before it: an *old chunk*, one Minecraft 1.13 to
1.17.1 saved, keeps everything under ``Level``, stores a block-carrying
section only where it holds something, runs from y=0 to 255, and numbers
its biomes per chunk rather than naming them per section -- one a 4x4x4
cell from 1.15, one a column before -- and before 1.16 packs its indices
and heightmaps end to end across longs. An old chunk reads as what it
stores, its blocks under the names it stored them by, and its biomes named
the way the game's own upgrade names them (:mod:`heaton_voxel.legacy_biomes`);
every write to one raises :class:`ReadOnlyChunk`, because the game rebuilds
an old chunk when it loads one -- deeper, with new terrain under it -- and
nothing here can do that for it. Anything older, and the 1.18 snapshots'
formats in between, is refused with a clear error rather than misread,
because those layouts differ in ways that would yield plausible-looking
nonsense instead of an obvious failure.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterator, Mapping

from .block_defaults import BLOCK_DEFAULTS, default_properties
from .heightmap_blocks import FLUIDS, LEAVES, NOT_IN_HEIGHTMAP
from .legacy_biomes import LEGACY_BIOMES
from .nbt import (
    Chunk,
    NBTError,
    TagByte,
    TagCompound,
    TagInt,
    TagIntArray,
    TagList,
    TagLong,
    TagLongArray,
    TagString,
    TAG_COMPOUND,
    TAG_END,
    TAG_LIST,
    TAG_STRING,
)

__all__ = [
    "MODERN_STATE_DATA_VERSION",
    "EMPTY_CHUNK_MIN_DATA_VERSION",
    "EMPTY_CHUNK_MAX_DATA_VERSION",
    "empty_chunk",
    "LEGACY_MIN_DATA_VERSION",
    "LEGACY_MAX_DATA_VERSION",
    "AIR_BLOCKS",
    "BlockState",
    "Blocks",
    "ReadOnlyChunk",
    "Section",
    "PalettedContainer",
    "unpack_indices",
    "pack_indices",
    "unpack_nibbles",
    "bits_for_palette",
    "heightmap_bits",
    "heightmap_mask",
    "chunk_finished",
    "HEIGHTMAP_NAMES",
]

# 21w39a (1.18), where sections moved out of the "Level" compound to the chunk
# root and the world grew its current height range. Everything here that
# writes assumes that layout.
MIN_DATA_VERSION = 2825

# Minecraft 1.13 (1519) through 1.17.1 (2730): the formats of palettes and
# ``Level``, read but never written. Chunks between this range and 1.18 were
# written only by the 1.18 snapshots, each a step of the move between the
# layouts, and are refused rather than guessed at; so is anything before
# 1.13, whose blocks are numbers, and the 1.13 snapshots before its release.
# Within the range the format moved twice, each at the schema of the game's
# own fix for it: below LEGACY_ALIGNED_DATA_VERSION a section's indices and a
# heightmap's run on across longs (BitStorageAlignFix), and below
# LEGACY_CELL_BIOMES_DATA_VERSION a chunk keeps one biome a column, 256 of
# them (ChunkBiomeFix). And what counts as finished was renamed twice
# (ChunkStatusFix, ChunkStatusFix2; see _legacy_finished).
LEGACY_MIN_DATA_VERSION = 1519
LEGACY_MAX_DATA_VERSION = 2730
LEGACY_ALIGNED_DATA_VERSION = 2527
LEGACY_CELL_BIOMES_DATA_VERSION = 2202
# Below 20w45a's schema a chunk keeps its entities in Level.Entities, not in
# entities/ beside the region.
LEGACY_ENTITY_STORAGE_DATA_VERSION = 2681

# The old overworld, the only height an old chunk is read at: sections 0..15,
# y 0..255. 1.17 let a datapack change a dimension's height; a chunk storing
# blocks outside these sections is refused rather than read at the wrong one.
LEGACY_SECTIONS = range(16)
LEGACY_BIOMES_PER_CHUNK = 1024  # 4x4 columns x 64 layers of 4 blocks
LEGACY_COLUMN_BIOMES = 256      # before 1.15: one a column


# The last DataVersion of each release the old formats span, its snapshots
# before it: 1.13.2, 1.14.4, 1.15.2, 1.16.5 and 1.17.1.
_LEGACY_RELEASES = ((1631, "1.13"), (1976, "1.14"), (2230, "1.15"),
                    (2586, "1.16"), (LEGACY_MAX_DATA_VERSION, "1.17"))


def legacy_release(data_version: int) -> str:
    """The release an old chunk's DataVersion belongs to, ``"1.13"`` to
    ``"1.17"``, a snapshot named for the release it led to."""
    for last, name in _LEGACY_RELEASES:
        if data_version <= last:
            return name
    raise ValueError(f"DataVersion {data_version} is past the old formats")


def _legacy_finished(status, data_version: int) -> bool:
    """Whether an old chunk's ``Status`` says the game finished it, by the
    game's own fixes: before ChunkStatusFix (1905) both ``fullchunk`` and
    ``postprocessed`` are finished, until ChunkStatusFix2 (1911) only
    ``fullchunk``, and from it ``full``."""
    if status is None:
        return False
    name = str(status).removeprefix("minecraft:")
    if data_version < 1905:
        return name in ("fullchunk", "postprocessed")
    if data_version < 1911:
        return name == "fullchunk"
    return name == "full"


def _unpack_run_on(longs, count: int, bits: int) -> list[int]:
    """Indices packed end to end across longs, a value straddling two where
    it falls on the seam: a section or heightmap before 1.16."""
    words = [int(v) & _UINT64 for v in longs]
    mask = (1 << bits) - 1
    out = []
    for index in range(count):
        start = index * bits
        word, shift = start >> 6, start & 63
        value = words[word] >> shift
        if shift + bits > 64:
            value |= words[word + 1] << (64 - shift)
        out.append(value & mask)
    return out

# 26.3, whose BlockStateFieldNamesFix targets schema 5006: from here a palette
# entry is ``id``/``properties`` rather than ``Name``/``Properties``, a state
# at its block's defaults is stored as the bare id, and a palette holding both
# forms wraps each bare id as ``{"": id}`` so the list stays one tag type.
# Chunks at or past this are written in that form; older ones keep the form
# they were read in, so a world half-visited since an update stays readable
# by the game in both halves.
MODERN_STATE_DATA_VERSION = 5006

#: The DataVersions of the finished chunks this library has read off the
#: game -- 26.2's (``fixtures/r.-1.0.mca``) and 26.3's
#: (``fixtures/modern/``) -- and so the range :func:`empty_chunk` builds a
#: chunk for. A world newer than the last is given chunks stamped with it,
#: which the game upgrades on load as it upgrades every older chunk; one
#: older than the first is refused, since its chunks may carry fields these
#: do not.
EMPTY_CHUNK_MIN_DATA_VERSION = 4903
EMPTY_CHUNK_MAX_DATA_VERSION = 5023

#: What the game calls air: its ``#minecraft:air`` tag
#: (``data/minecraft/tags/block/air.json``), exactly these three. The
#: carvers left ``cave_air`` in every cave cut before 1.18, dungeons,
#: mineshafts and strongholds still write it, and the game answers
#: ``void_air`` past the ends of the world; its ``isAir`` is true of all
#: three, so a test for "is this cell empty" that names only
#: ``minecraft:air`` sees a wall in every old cave. One set for the whole
#: library, in both twins.
AIR_BLOCKS = frozenset(("minecraft:air", "minecraft:cave_air", "minecraft:void_air"))

BLOCKS_PER_SECTION = 16 * 16 * 16  # 4096
BIOMES_PER_SECTION = 4 * 4 * 4  # 64, one entry per 4x4x4 cell
MIN_BLOCK_BITS = 4
MIN_BIOME_BITS = 1

_UINT64 = 0xFFFFFFFFFFFFFFFF
_SIGN_BIT = 0x8000000000000000


class ReadOnlyChunk(NBTError):
    """A write to a chunk this library reads but never writes: one saved by
    Minecraft 1.13 to 1.17 (:data:`LEGACY_MIN_DATA_VERSION` ..
    :data:`LEGACY_MAX_DATA_VERSION`). An :class:`NBTError`, so every writer
    that already reports or refuses a chunk it cannot use does the same
    with this one, naming why."""


# --------------------------------------------------------------------------
# Block states
# --------------------------------------------------------------------------

_STATE_SYNTAX = re.compile(r"^([^\[\]]+?)(?:\[(.*)\])?$")


class BlockState:
    """An immutable block identity: a name plus its properties.

    ``minecraft:oak_stairs[facing=north,half=bottom]`` is one state and
    ``[facing=south,half=bottom]`` is a different one. Hashable, so it works
    as a dictionary key when tallying a world.
    """

    __slots__ = ("_name", "_properties", "_key", "_source")

    def __init__(self, name: str, properties: Mapping[str, str] | None = None):
        self._name = str(name)
        self._properties = dict(properties or {})
        # Property order is not significant to the game, so normalize it for
        # comparison; the original tag is kept separately for writing back.
        self._key = (self._name, tuple(sorted(self._properties.items())))
        self._source: TagCompound | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def properties(self) -> dict[str, str]:
        """A copy; block states are immutable."""
        return dict(self._properties)

    @classmethod
    def parse(cls, text: str) -> "BlockState":
        """Build a state from ``name[key=value,...]`` notation."""
        match = _STATE_SYNTAX.match(text.strip())
        if not match:
            raise ValueError(f"cannot parse block state {text!r}")
        name, body = match.group(1).strip(), match.group(2)
        properties = {}
        if body and body.strip():
            for pair in body.split(","):
                key, sep, value = pair.partition("=")
                if not sep:
                    raise ValueError(f"property {pair!r} in {text!r} has no '='")
                properties[key.strip()] = value.strip()
        return cls(name, properties)

    @classmethod
    def from_nbt(cls, tag) -> "BlockState":
        """Decode a palette entry, in either codec.

        Before 26.3 an entry is ``{Name, Properties}`` and is taken exactly
        as stored. From 26.3 (:data:`MODERN_STATE_DATA_VERSION`) a state at
        its block's defaults is the bare id -- a string, or ``{"": id}``
        where the list also holds compounds -- and any other state is
        ``{id, properties}``. The properties that form leaves out are
        restored from :mod:`heaton_voxel.block_defaults`, so a 26.3 chunk
        reads exactly as the older format wrote it: every property present,
        in name order, and ``water`` is ``water[level=0]`` to a reader as it
        is to the game. The original tag is kept either way, so an untouched
        palette re-encodes to the same bytes.
        """
        source = tag
        if isinstance(tag, TagCompound) and len(tag) == 1 and "" in tag:
            tag = tag[""]
        if isinstance(tag, TagString):
            name, given, modern = str(tag), None, True
        elif isinstance(tag, TagCompound) and "id" in tag:
            name, given, modern = str(tag["id"]), tag.get("properties"), True
        elif isinstance(tag, TagCompound) and "Name" in tag:
            name, given, modern = str(tag["Name"]), tag.get("Properties"), False
        else:
            raise NBTError(f"palette entry is not a block state: {tag!r}")
        properties = {k: str(v) for k, v in (given or {}).items()}
        if modern:
            filled = default_properties(name)
            filled.update(properties)
            properties = dict(sorted(filled.items()))
        state = cls(name, properties)
        state._source = source
        return state

    def canonical(self) -> "BlockState":
        """This state with its block's defaults filled in and its properties
        in name order: the identity the 26.3 codec works in, where ``water``
        and ``water[level=0]`` are one block. A block the defaults table
        does not know keeps exactly the properties it was given."""
        filled = default_properties(self._name)
        filled.update(self._properties)
        ordered = dict(sorted(filled.items()))
        if list(ordered.items()) == list(self._properties.items()):
            return self
        return BlockState(self._name, ordered)

    def to_nbt(self, *, modern: bool = False):
        """Encode for a palette.

        Legacy (the default): ``{Properties, Name}``, or the very tag this
        state was decoded from when that was a legacy entry. ``modern``:
        the 26.3 form -- the bare id as a string when the state is its
        block's default (or has no properties and the block is unknown),
        otherwise ``{id, properties}`` carrying every property the block
        has, defaults filled in, in name order. The container decides how a
        bare id sits in its list; see ``PalettedContainer._append_entry``.
        """
        if modern:
            filled = default_properties(self._name)
            complete = dict(filled)
            complete.update(self._properties)
            if complete == filled:
                return TagString(self._name)
            tag = TagCompound()
            tag["id"] = TagString(self._name)
            tag["properties"] = TagCompound(
                {k: TagString(v) for k, v in sorted(complete.items())}
            )
            return tag
        if isinstance(self._source, TagCompound) and "Name" in self._source:
            return self._source
        tag = TagCompound()
        if self._properties:
            tag["Properties"] = TagCompound(
                {k: TagString(v) for k, v in self._properties.items()}
            )
        tag["Name"] = TagString(self._name)
        return tag

    def with_properties(self, **changes: str) -> "BlockState":
        """A copy with some properties replaced."""
        merged = dict(self._properties)
        merged.update({k: str(v) for k, v in changes.items()})
        return BlockState(self._name, merged)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, BlockState):
            return self._key == other._key
        if isinstance(other, str):
            return not self._properties and self._name == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._key)

    def __str__(self) -> str:
        if not self._properties:
            return self._name
        body = ",".join(f"{k}={v}" for k, v in sorted(self._properties.items()))
        return f"{self._name}[{body}]"

    def __repr__(self) -> str:
        return f"BlockState({str(self)!r})"


# --------------------------------------------------------------------------
# Bit packing
# --------------------------------------------------------------------------


def bits_for_palette(size: int, minimum: int) -> int:
    """Bits per index needed to address ``size`` palette entries."""
    if size <= 1:
        return minimum
    return max(minimum, (size - 1).bit_length())


def _to_signed(word: int) -> int:
    """NBT longs are signed; the packing treats them as unsigned bit fields."""
    word &= _UINT64
    return word - 0x10000000000000000 if word & _SIGN_BIT else word


def unpack_indices(longs, count: int, bits: int) -> list[int]:
    """Unpack ``count`` indices of ``bits`` bits each from a long array.

    Each long holds ``64 // bits`` whole entries and the leftover high bits
    are padding -- no entry crosses a long boundary.

    Python's integers are conceptually infinite two's complement, so shifting
    and masking a negative long extracts the right unsigned field without any
    conversion first.
    """
    mask = (1 << bits) - 1
    values: list[int] = []
    per_long = 64 // bits
    shifts = [k * bits for k in range(per_long)]
    for word in longs:
        for shift in shifts:
            values.append((word >> shift) & mask)
    del values[count:]
    if len(values) < count:
        raise NBTError(
            f"packed array holds {len(values)} entries at {bits} bits, need {count}"
        )
    return values


def pack_indices(values, count: int, bits: int) -> TagLongArray:
    """Pack indices back into a long array. Inverse of :func:`unpack_indices`."""
    per_long = 64 // bits
    words = []
    for base in range(0, count, per_long):
        word = 0
        for k, value in enumerate(values[base : base + per_long]):
            word |= value << (k * bits)
        words.append(_to_signed(word))
    return TagLongArray(words)


def unpack_nibbles(data) -> list[int]:
    """Unpack a light array: two 4-bit values per byte, low nibble first.

    ``BlockLight`` and ``SkyLight`` store one 0-15 level per block cell in
    2048 signed bytes, indexed in the same y, z, x order as everything else
    in a section. The byte for cell ``i`` is ``data[i >> 1]``; even cells
    take the low nibble.
    """
    if len(data) != BLOCKS_PER_SECTION // 2:
        raise NBTError(
            f"light array holds {len(data)} bytes, expected "
            f"{BLOCKS_PER_SECTION // 2}"
        )
    values: list[int] = []
    for byte in data:
        byte &= 0xFF
        values.append(byte & 0xF)
        values.append(byte >> 4)
    return values


def heightmap_bits(min_y: int, max_y: int) -> int:
    """Bits per heightmap entry for a world of this height.

    Entries count blocks above ``min_y``, so they run 0..height inclusive --
    height+1 distinct values. For the current 384-tall overworld that is 9.
    """
    height = max_y - min_y + 1
    return max(1, height.bit_length())


# The four surface maps the game maintains per fully generated chunk. A
# partly generated chunk has none of them -- treat that as "no surface yet",
# never scan for one.
HEIGHTMAP_NAMES = (
    "WORLD_SURFACE",
    "MOTION_BLOCKING",
    "MOTION_BLOCKING_NO_LEAVES",
    "OCEAN_FLOOR",
)
COLUMNS_PER_CHUNK = 256  # 16x16

# The order the game writes the four in, which a rebuild keeps: a map that
# was right comes back the same bytes.
_STORED_HEIGHTMAPS = (
    "OCEAN_FLOOR",
    "MOTION_BLOCKING_NO_LEAVES",
    "MOTION_BLOCKING",
    "WORLD_SURFACE",
)


def heightmap_mask(state: BlockState) -> int:
    """Which of the four maps stop at ``state``: one bit each, bit ``i`` for
    ``HEIGHTMAP_NAMES[i]``.

    ``WORLD_SURFACE`` stops at anything but air, ``OCEAN_FLOOR`` at a block
    the game's ``blocks_motion_in_heightmap`` tag names, and the two
    motion-blocking maps at a block their tags name or a state that holds
    fluid -- a waterlogged state, an omitted ``waterlogged`` taking the
    block's default, as the game reads it. A block the game has never heard
    of counts: the unknown is solid.
    """
    name = state.name
    if name in AIR_BLOCKS:
        return 0
    solid = name not in NOT_IN_HEIGHTMAP
    fluid = name in FLUIDS
    if not fluid:
        defaults = BLOCK_DEFAULTS.get(name)
        if defaults and "waterlogged" in defaults:
            fluid = state._properties.get(
                "waterlogged", defaults["waterlogged"]) == "true"
    mask = 1
    if solid or fluid:
        mask |= 2
    if (solid and name not in LEAVES) or fluid:
        mask |= 4
    if solid:
        mask |= 8
    return mask


def chunk_finished(status) -> bool:
    """Whether a chunk's ``Status`` says the game finished generating it:
    ``minecraft:full``, or the bare ``full`` of a 1.17 chunk or a hand-made
    one. Anything else is a chunk the generator set aside part-way, and the
    game generates over whatever is written into it."""
    return status is not None and str(status).removeprefix("minecraft:") == "full"


def empty_chunk(
    chunk_x: int,
    chunk_z: int,
    *,
    data_version: int,
    min_section: int,
    max_section: int,
    biome: str,
) -> TagCompound:
    """A finished chunk of air under one biome: the compound the game writes
    for one, every key it writes and in its order, sections ``min_section``
    to ``max_section``.

    ``Status`` is ``minecraft:full``, so the game loads the chunk as it
    stands and never generates over it; its four heightmaps are rebuilt from
    its blocks, all air, so they read the floor; and ``isLightOn`` is 0 with
    no light stored, which hands the lighting to the game the way
    :meth:`Blocks.reset_lighting` does -- it lights the chunk when it first
    loads it. No structures, ticks, block entities or entities: nothing
    stands in it yet. ``tests/test_create_chunks.py`` holds it, key for key
    and type for type, to the finished chunks of both fixtures.
    """
    version = int(data_version)
    if not EMPTY_CHUNK_MIN_DATA_VERSION <= version <= EMPTY_CHUNK_MAX_DATA_VERSION:
        raise NBTError(
            f"no finished chunk of DataVersion {version} has been read off the "
            f"game; an empty chunk is built for {EMPTY_CHUNK_MIN_DATA_VERSION} "
            f"(26.2) to {EMPTY_CHUNK_MAX_DATA_VERSION} (26.3), and a newer "
            f"world takes the latter, which the game upgrades on load")
    if min_section > max_section:
        raise NBTError(f"no sections between {min_section} and {max_section}")
    if not isinstance(biome, str) or not biome:
        raise NBTError(f"a chunk needs a biome id, not {biome!r}")
    air = BlockState("minecraft:air").to_nbt(modern=version >= MODERN_STATE_DATA_VERSION)
    sections = TagList(element_type=TAG_COMPOUND)
    for y in range(min_section, max_section + 1):
        sections.append(TagCompound({
            "block_states": TagCompound({"palette": TagList([air], air.ID)}),
            "biomes": TagCompound({"palette": TagList([TagString(biome)], TAG_STRING)}),
            "Y": TagByte(y),
        }))
    count = max_section - min_section + 1
    data = TagCompound({
        "Status": TagString("minecraft:full"),
        "zPos": TagInt(chunk_z),
        "block_entities": TagList(element_type=TAG_END),
        "yPos": TagInt(min_section),
        "LastUpdate": TagLong(0),
        "structures": TagCompound({"References": TagCompound(), "starts": TagCompound()}),
        "InhabitedTime": TagLong(0),
        "xPos": TagInt(chunk_x),
        "Heightmaps": TagCompound(),      # filled below, in its place
        "sections": sections,
        "isLightOn": TagByte(0),
        "block_ticks": TagList(element_type=TAG_END),
        "PostProcessing": TagList([TagList(element_type=TAG_END) for _ in range(count)],
                                  TAG_LIST),
        "DataVersion": TagInt(version),
        "fluid_ticks": TagList(element_type=TAG_END),
    })
    Blocks(data).rebuild_heightmaps()
    return data


def _infer_bits(palette_size: int, n_longs: int, count: int, minimum: int) -> int:
    """Bits per entry, cross-checked against the array's actual length.

    Normally derivable from the palette, but a section whose palette has
    shrunk can still be packed at the wider width the game last wrote, so the
    array length is the authority when the two disagree.
    """
    expected = bits_for_palette(palette_size, minimum)
    per_long = 64 // expected
    if per_long and -(-count // per_long) == n_longs:
        return expected
    for bits in range(minimum, 65):
        per_long = 64 // bits
        if per_long and -(-count // per_long) == n_longs:
            return bits
    raise NBTError(
        f"cannot reconcile a {n_longs}-long array with {count} entries "
        f"and a {palette_size}-entry palette"
    )


# --------------------------------------------------------------------------
# Paletted containers
# --------------------------------------------------------------------------


def _wrap_bare_id(entry):
    """``{"": id}``: how a 26.3 palette carries a bare id in a list that
    also holds compounds. An entry that is already a compound passes
    through."""
    return entry if isinstance(entry, TagCompound) else TagCompound({"": entry})


class PalettedContainer:
    """A palette plus a bit-packed index array, as stored in a section.

    Backs both ``block_states`` (4096 entries, minimum 4 bits) and ``biomes``
    (64 entries, minimum 1 bit). When every cell holds the same value the game
    omits the index array entirely, and this class restores it on demand.
    """

    def __init__(
        self,
        tag: TagCompound,
        count: int,
        min_bits: int,
        *,
        entries_are_compounds: bool = True,
        modern_states: bool = False,
        read_only: str | None = None,
    ):
        self._tag = tag
        self._count = count
        self._min_bits = min_bits
        self._compounds = entries_are_compounds
        # Why this container refuses every write, or None if it takes them:
        # an old chunk's, whose containers are views of what it stores (or
        # stand in for what it does not), never the chunk's own tags.
        self._read_only = read_only
        # A 26.3 block palette: bare ids for default states, wrappers in a
        # mixed list, defaults restored on read. Never a biome container's.
        self._modern = bool(modern_states) and entries_are_compounds

        raw_palette = tag.get("palette")
        if raw_palette is None:
            raise NBTError("paletted container has no palette")
        self._palette = [self._decode_entry(e) for e in raw_palette]
        self._lookup = {value: i for i, value in enumerate(self._palette)}
        self._indices: list[int] | None = None
        self._bits: int | None = None

    # -- entry codecs ------------------------------------------------------

    def _decode_entry(self, entry):
        return BlockState.from_nbt(entry) if self._compounds else str(entry)

    def _encode_entry(self, value):
        if self._compounds:
            if isinstance(value, str):
                value = BlockState.parse(value)
            if not isinstance(value, BlockState):
                raise TypeError(f"expected a BlockState, got {type(value).__name__}")
            if self._modern:
                # The 26.3 codec's identity is the state with its block's
                # defaults filled in: ``water`` and ``water[level=0]`` are
                # one palette entry, as they are one block to the game.
                value = value.canonical()
            return value, value.to_nbt(modern=self._modern)
        return str(value), TagString(str(value))

    def _element_type(self) -> int:
        return TAG_COMPOUND if self._compounds else TAG_STRING

    def _append_entry(self, entry_tag) -> None:
        """Add an encoded entry to the stored palette list.

        A 26.3 palette is a list of strings while every state in it is a
        default, and a list of compounds once one is not -- each bare id
        wrapped as ``{"": id}``, because a list holds one tag type. So a
        compound arriving in a string list widens the whole list, and a
        bare id arriving in a compound list is wrapped on its way in.
        """
        palette_tag = self._tag["palette"]
        if not self._modern:
            palette_tag.append(entry_tag)
            if not palette_tag.element_type:
                palette_tag.element_type = self._element_type()
            return
        if isinstance(entry_tag, TagCompound):
            if palette_tag.element_type != TAG_COMPOUND:
                palette_tag = TagList(
                    [_wrap_bare_id(entry) for entry in palette_tag],
                    element_type=TAG_COMPOUND,
                )
                self._tag["palette"] = palette_tag
            palette_tag.append(entry_tag)
        elif palette_tag.element_type == TAG_COMPOUND:
            palette_tag.append(_wrap_bare_id(entry_tag))
        else:
            palette_tag.append(entry_tag)
            palette_tag.element_type = TAG_STRING

    def _new_palette_list(self, entry_tags) -> TagList:
        """A fresh stored palette holding exactly these encoded entries."""
        entry_tags = list(entry_tags)
        if not self._modern:
            element_type = self._tag["palette"].element_type or self._element_type()
            return TagList(entry_tags, element_type=element_type)
        if any(isinstance(entry, TagCompound) for entry in entry_tags):
            return TagList([_wrap_bare_id(entry) for entry in entry_tags],
                           element_type=TAG_COMPOUND)
        return TagList(entry_tags, element_type=TAG_STRING)

    @property
    def read_only(self) -> bool:
        """Whether every write here raises :class:`ReadOnlyChunk`."""
        return self._read_only is not None

    def _writable(self) -> None:
        if self._read_only is not None:
            raise ReadOnlyChunk(self._read_only)

    # -- reading -----------------------------------------------------------

    @property
    def palette(self) -> list:
        """The distinct values in this container, in index order."""
        return list(self._palette)

    @property
    def bits(self) -> int:
        """Bits per packed index.

        Cached: this is read once per block lookup, and re-deriving it from the
        array length each time made point reads several times slower than they
        needed to be. Every write path clears it.
        """
        if self._bits is None:
            data = self._tag.get("data")
            self._bits = (
                bits_for_palette(len(self._palette), self._min_bits)
                if data is None
                else _infer_bits(
                    len(self._palette), len(data), self._count, self._min_bits
                )
            )
        return self._bits

    def get(self, index: int) -> Any:
        """The value at a flat index, without unpacking the whole array."""
        data = self._tag.get("data")
        if data is None:
            # Uniform container: one palette entry, no array.
            return self._palette[0]
        bits = self.bits
        per_long = 64 // bits
        word, slot = divmod(index, per_long)
        value = (data[word] >> (slot * bits)) & ((1 << bits) - 1)
        return self._palette[value]

    def indices(self) -> list[int]:
        """Every palette index, unpacked. The expensive path."""
        if self._indices is None:
            data = self._tag.get("data")
            if data is None:
                self._indices = [0] * self._count
            else:
                self._indices = unpack_indices(
                    data, self._count, self.bits
                )
        return self._indices

    def histogram(self) -> dict:
        """Count of each palette value in this container, in the order the
        cells first hold each. The indices are counted first, as integers,
        and named after: hashing a state for every cell cost five times the
        count."""
        counts: dict[Any, int] = {}
        palette = self._palette
        for index, n in Counter(self.indices()).items():
            value = palette[index]
            counts[value] = counts.get(value, 0) + n
        return counts

    # -- writing -----------------------------------------------------------

    def set(self, index: int, value: Any) -> None:
        """Set one cell, writing through to the underlying NBT.

        Usually rewrites a single 64-bit word. Growing the palette past a
        power of two widens every index, which repacks the whole array.
        """
        self._writable()
        # Read the current width before touching the palette; growing it is
        # what may change the answer.
        old_bits = self.bits
        slot = self._slot_for(value)

        new_bits = bits_for_palette(len(self._palette), self._min_bits)
        if new_bits != old_bits or "data" not in self._tag:
            # Widening (or materializing a previously uniform container)
            # means every entry moves; rebuild the array.
            values = self.indices()
            values[index] = slot
            self._indices = values
            self._bits = None
            self._tag["data"] = pack_indices(
                values, self._count, new_bits
            )
            return

        # Fast path: patch the one word holding this entry.
        data = self._tag["data"]
        bits = old_bits
        per_long = 64 // bits
        word, position = divmod(index, per_long)
        shift = position * bits
        mask = ((1 << bits) - 1) << shift
        current = data[word] & _UINT64
        data[word] = _to_signed((current & ~mask) | (slot << shift))
        if self._indices is not None:
            self._indices[index] = slot

    def _slot_for(self, value) -> int:
        """Palette index for a value, appending it if new."""
        key, entry_tag = self._encode_entry(value)
        slot = self._lookup.get(key)
        if slot is not None:
            return slot
        slot = len(self._palette)
        self._palette.append(key)
        self._lookup[key] = slot
        self._append_entry(entry_tag)
        return slot

    def fill_all(self, value) -> None:
        """Make every cell the same value.

        Collapses to a single-entry palette with no index array at all, which
        is how the game stores a uniform section -- so filling a solid volume
        costs constant work per section rather than rewriting 4096 indices.
        """
        self._writable()
        key, entry_tag = self._encode_entry(value)
        self._tag["palette"] = self._new_palette_list([entry_tag])
        self._tag.pop("data", None)
        self._palette = [key]
        self._lookup = {key: 0}
        self._indices = [0] * self._count
        self._bits = None

    def set_all(self, values) -> None:
        """Replace every cell from a sequence of ``count`` values, at once.

        The bulk counterpart of :meth:`fill_all` for non-uniform content: the
        palette is rebuilt to exactly the distinct values given (in first-seen
        order) and the array is packed once. Anything the old palette held is
        gone, so this is for writers that own the whole section -- a paste,
        not an edit.
        """
        self._writable()
        values = list(values)
        if len(values) != self._count:
            raise NBTError(
                f"set_all needs exactly {self._count} values, got {len(values)}"
            )

        palette: list = []
        lookup: dict = {}
        entry_tags: list = []
        indices: list[int] = []
        for value in values:
            key, entry_tag = self._encode_entry(value)
            slot = lookup.get(key)
            if slot is None:
                slot = len(palette)
                palette.append(key)
                lookup[key] = slot
                entry_tags.append(entry_tag)
            indices.append(slot)

        self._tag["palette"] = self._new_palette_list(entry_tags)
        self._palette = palette
        self._lookup = lookup
        self._indices = indices
        self._bits = None
        if len(palette) == 1:
            # Uniform: same storage fill_all produces, no index array.
            self._tag.pop("data", None)
        else:
            self._tag["data"] = pack_indices(
                indices, self._count, bits_for_palette(len(palette), self._min_bits)
            )

    def set_many(self, positions, value) -> int:
        """Set many cells at once, unpacking and repacking only once.

        Returns the number of cells written. Going through :meth:`set` instead
        would re-derive the bit width and patch a word per block; across a box
        fill that is the difference between seconds and minutes.
        """
        self._writable()
        slot = self._slot_for(value)
        values = self.indices()
        written = 0
        for position in positions:
            values[position] = slot
            written += 1
        self._indices = values
        self._bits = None
        self._tag["data"] = pack_indices(
            values,
            self._count,
            bits_for_palette(len(self._palette), self._min_bits),
        )
        return written

    def prune(self) -> int:
        """Drop palette entries no longer referenced, repacking if that helps.

        Editing tends to leave orphans behind -- replacing the last diamond
        block does not remove it from the palette -- and enough of them can
        push the container into a wider bit width than it needs. Returns how
        many entries were removed.
        """
        self._writable()
        values = list(self.indices())
        used = sorted(set(values))
        if len(used) == len(self._palette):
            return 0
        if not used:  # pragma: no cover - a container always has entries
            return 0

        source_palette = self._tag["palette"]
        removed = len(source_palette) - len(used)

        remap = {old: new for new, old in enumerate(used)}
        self._palette = [self._palette[i] for i in used]
        self._lookup = {value: i for i, value in enumerate(self._palette)}
        self._tag["palette"] = TagList(
            [source_palette[i] for i in used], element_type=source_palette.element_type
        )

        values = [remap[v] for v in values]
        self._indices = values
        self._bits = None
        bits = bits_for_palette(len(self._palette), self._min_bits)
        if len(self._palette) == 1:
            self._tag.pop("data", None)
            self._indices = [0] * self._count
        else:
            self._tag["data"] = pack_indices(
                values, self._count, bits
            )
        return removed


# --------------------------------------------------------------------------
# Sections and chunks
# --------------------------------------------------------------------------


_AIR_ENTRY = "minecraft:air"


def _legacy_biome_tag(names: list[str]) -> TagCompound:
    """A section's 64 biome names as the biome container a current chunk
    stores: a palette in first-seen order -- the order the game's own
    upgrade builds it in (``ChunkHeightAndBiomeFix.makeBiomeContainer``) --
    and an index array unless every cell is one biome."""
    palette: list[str] = []
    slots: dict[str, int] = {}
    indices = []
    for name in names:
        slot = slots.get(name)
        if slot is None:
            slot = slots[name] = len(palette)
            palette.append(name)
        indices.append(slot)
    tag = TagCompound(
        {"palette": TagList([TagString(n) for n in palette], element_type=TAG_STRING)}
    )
    if len(palette) > 1:
        tag["data"] = pack_indices(
            indices, BIOMES_PER_SECTION, bits_for_palette(len(palette), MIN_BIOME_BITS)
        )
    return tag


class Section:
    """One 16x16x16 slice of a chunk.

    An old chunk's sections (``legacy=``) are read through containers that
    refuse every write: their blocks a view of the ``Palette`` and
    ``BlockStates`` the section stores, a section of the world the chunk
    stores no blocks for -- they left out what was all air -- one entry of
    air, and their biomes built from the chunk's numbered array.
    """

    __slots__ = ("y", "_tag", "_blocks", "_biomes", "_block_light", "_sky_light",
                 "_modern", "_read_only", "_legacy_biomes", "_run_on")

    def __init__(
        self,
        tag: TagCompound,
        *,
        modern_states: bool = False,
        legacy: str | None = None,
        legacy_biomes: list[str] | None = None,
        run_on: bool = False,
    ):
        self._tag = tag
        self.y = int(tag["Y"])
        self._modern = bool(modern_states)
        # An old section's refusal, which is also what marks it as one.
        self._read_only = legacy
        self._legacy_biomes = legacy_biomes
        # Before 1.16 a section's indices run on across longs.
        self._run_on = run_on
        self._blocks: PalettedContainer | None = None
        self._biomes: PalettedContainer | None = None
        self._block_light: list[int] | None = None
        self._sky_light: list[int] | None = None

    @property
    def blocks(self) -> PalettedContainer | None:
        """Block container, or ``None`` for a section with no block data."""
        if self._blocks is None:
            if self._read_only is not None:
                tag = self._legacy_block_tag()
            else:
                tag = self._tag.get("block_states")
            if tag is None:
                return None
            self._blocks = PalettedContainer(
                tag,
                BLOCKS_PER_SECTION,
                MIN_BLOCK_BITS,
                entries_are_compounds=True,
                modern_states=self._modern,
                read_only=self._read_only,
            )
        return self._blocks

    def _legacy_block_tag(self) -> TagCompound | None:
        """What a current section would store as ``block_states``, over the
        old section's own tags: the same palette list and index array,
        never copies, so a read is a read of what the chunk holds."""
        palette = self._tag.get("Palette")
        if palette is not None:
            view = TagCompound({"palette": palette})
            states = self._tag.get("BlockStates")
            if states is not None:
                if self._run_on:
                    # Before 1.16 the indices run on across longs, as many
                    # bits each as fill the array exactly: repacked here,
                    # once, the way a current section packs them.
                    if len(states) % 64:
                        raise NBTError(
                            f"section Y={self.y} holds {len(states)} longs, which "
                            f"no whole number of bits a cell fills")
                    bits = len(states) // 64
                    states = pack_indices(_unpack_run_on(states, BLOCKS_PER_SECTION, bits),
                                          BLOCKS_PER_SECTION, bits)
                view["data"] = states
            return view
        if self.y in LEGACY_SECTIONS:
            return TagCompound({"palette": TagList(
                [TagCompound({"Name": TagString(_AIR_ENTRY)})],
                element_type=TAG_COMPOUND,
            )})
        return None  # a section of light alone, past either end of the world

    @property
    def biomes(self) -> PalettedContainer | None:
        """Biome container at 4x4x4 resolution, or ``None`` if absent."""
        if self._biomes is None:
            tag: TagCompound | None
            if self._read_only is not None:
                if self._legacy_biomes is None:
                    return None
                tag = _legacy_biome_tag(self._legacy_biomes)
            else:
                tag = self._tag.get("biomes")
            if tag is None:
                return None
            self._biomes = PalettedContainer(
                tag,
                BIOMES_PER_SECTION,
                MIN_BIOME_BITS,
                entries_are_compounds=False,
                read_only=self._read_only,
            )
        return self._biomes

    def block_light(self) -> list[int] | None:
        """Stored block-light levels, one 0-15 value per cell, or ``None``.

        This is the light the game *baked* -- torches, lava, glowstone --
        exactly as saved, unpacked once and cached. ``None`` means the
        section stores no array, which after a
        :meth:`Blocks.reset_lighting` is every section: absent light is
        "the game will recompute", not darkness. A viewer shading blocks
        should treat ``None`` as full-bright rather than black.
        """
        if self._block_light is None:
            raw = self._tag.get("BlockLight")
            if raw is None:
                return None
            self._block_light = unpack_nibbles(raw)
        return self._block_light

    def sky_light(self) -> list[int] | None:
        """Stored sky-light levels, one 0-15 value per cell, or ``None``.

        Same shape and caveats as :meth:`block_light`; 15 is open sky, 0 is
        a sealed interior. The game omits the array for sections it knows
        are uniformly dark or uniformly lit, so ``None`` here is common
        even in untouched chunks.
        """
        if self._sky_light is None:
            raw = self._tag.get("SkyLight")
            if raw is None:
                return None
            self._sky_light = unpack_nibbles(raw)
        return self._sky_light

    def __repr__(self) -> str:
        blocks = self.blocks
        size = len(blocks.palette) if blocks else 0
        return f"<Section Y={self.y} palette={size}>"


class Blocks:
    """Block and biome access for one chunk.

    ``x`` and ``z`` are chunk-local (0-15); ``y`` is a world coordinate.
    """

    def __init__(self, chunk: Chunk | TagCompound):
        data = chunk.data if isinstance(chunk, Chunk) else chunk
        if data is None:
            raise NBTError("chunk has no readable NBT")
        self._data = data

        version = int(data.get("DataVersion", TagInt(0)))
        self.data_version = version
        #: Whether this chunk's palettes use the 26.3 state codec; see
        #: :data:`MODERN_STATE_DATA_VERSION`. Decided once per chunk, by the
        #: DataVersion the game stamped on it, so an edit writes the form
        #: the game that made the chunk will read back.
        self.modern_states = version >= MODERN_STATE_DATA_VERSION
        #: Whether this is a chunk Minecraft 1.13 to 1.17.1 saved, which
        #: reads like any other and refuses every write (:class:`ReadOnlyChunk`).
        self.legacy = LEGACY_MIN_DATA_VERSION <= version <= LEGACY_MAX_DATA_VERSION
        if version and version < MIN_DATA_VERSION and not self.legacy:
            # Before 1.13 a chunk's blocks are numbers, and the 1.18
            # snapshots between 1.17.1 and the current layout each wrote a
            # step of the move. Reading either with these rules produces
            # plausible-looking nonsense rather than an obvious failure, so
            # refuse instead.
            raise NBTError(
                f"chunk DataVersion {version} is in neither format this "
                f"library reads: Minecraft 1.18 and later (DataVersion "
                f"{MIN_DATA_VERSION}+), or 1.13 to 1.17.1 "
                f"({LEGACY_MIN_DATA_VERSION}-{LEGACY_MAX_DATA_VERSION}), read "
                f"only. Open the world in a current Minecraft to upgrade it "
                f"first."
            )
        if self.legacy:
            self._read_legacy(data)
            return

        raw = data.get("sections")
        if raw is None:
            raise NBTError(
                "chunk has no 'sections' list"
                + (" (is this a pre-1.18 chunk?)" if not version else "")
            )
        self.sections: dict[int, Section] = {}
        for tag in raw:
            section = Section(tag, modern_states=self.modern_states)
            self.sections[section.y] = section
        self._heightmaps: dict[str, list[int] | None] = {}

        # The world's vertical extent, which is not the span of the stored
        # sections: the game keeps light one section past each end of the
        # world, so a chunk can hold a section at Y=-5 or Y=20 with light
        # and no blocks. Only sections carrying blocks are the world, and
        # the floor is the one the chunk records, which is the origin every
        # heightmap entry counts from.
        held = sorted(
            y for y, section in self.sections.items()
            if section._tag.get("block_states") is not None
        )
        floor = data.get("yPos")
        if floor is not None:
            self._min_y = int(floor) * 16
        else:
            self._min_y = held[0] * 16 if held else 0
        # A chunk storing no blocks at all spans nothing.
        self._max_y = (held[-1] + 1) * 16 - 1 if held else self._min_y - 1

    def _read_legacy(self, data: TagCompound) -> None:
        """A chunk Minecraft 1.13 to 1.17.1 saved: everything under
        ``Level``, block-carrying sections only where they hold something, a
        world of y 0..255, and the biomes one array of numbers for the whole
        chunk -- one a 4x4x4 cell from 1.15, one a column before."""
        level = data.get("Level")
        where = (f"chunk at DataVersion {self.data_version} "
                 f"(Minecraft {legacy_release(self.data_version)})")
        if not isinstance(level, TagCompound):
            raise NBTError(f"{where} has no 'Level' compound")
        self._data = level
        for key in ("xPos", "zPos"):
            if key not in level:
                raise NBTError(f"{where} has no {key}, so there is no telling where it stands")
        refusal = self._refusal("write")
        version = self.data_version

        names = None
        biomes = level.get("Biomes")
        if biomes is not None:
            if not isinstance(biomes, TagIntArray):
                raise NBTError(
                    f"{where} at ({self.world_x}, {self.world_z}) stores its "
                    f"biomes as something other than an int array")
            columns = version < LEGACY_CELL_BIOMES_DATA_VERSION
            wanted = LEGACY_COLUMN_BIOMES if columns else LEGACY_BIOMES_PER_CHUNK
            if len(biomes) != wanted:
                raise NBTError(
                    f"{where} at ({self.world_x}, {self.world_z}) stores "
                    f"{len(biomes)} biomes, not the {wanted} of its version's "
                    f"0..255 world")
            numbers = [int(value) for value in biomes]
            if columns:
                # One a column, as the game's ChunkBiomeFix makes cells of
                # them: each 4x4 takes the column at its center, (2, 2) in,
                # at every height.
                layer = [numbers[(z * 4 + 2) * 16 + x * 4 + 2]
                         for z in range(4) for x in range(4)]
                numbers = layer * 64
            # The game's own upgrade reads each entry's low byte
            # (ChunkHeightAndBiomeFix.getOldBiome).
            names = [LEGACY_BIOMES[value & 255] for value in numbers]

        run_on = version < LEGACY_ALIGNED_DATA_VERSION
        self.sections = {}
        for tag in level.get("Sections") or ():
            y = int(tag["Y"])
            if tag.get("Palette") is not None and y not in LEGACY_SECTIONS:
                raise NBTError(
                    f"{where} at ({self.world_x}, {self.world_z}) stores "
                    f"blocks in section Y={y}, outside the 0..255 world it is "
                    f"read at: a world whose height a datapack changed is not "
                    f"read rather than read at the wrong one")
            self.sections[y] = self._legacy_section(tag, y, names, refusal, run_on)
        # They left out a section of the world that was all air; this reads
        # it as the air it is, so every section from 0 to 15 is there.
        for y in LEGACY_SECTIONS:
            if y not in self.sections:
                self.sections[y] = self._legacy_section(
                    TagCompound({"Y": TagByte(y)}), y, names, refusal, run_on)
        self._heightmaps = {}
        self._min_y = LEGACY_SECTIONS.start * 16
        self._max_y = LEGACY_SECTIONS.stop * 16 - 1

    @staticmethod
    def _legacy_section(tag, y, names, refusal, run_on=False) -> Section:
        biomes = None
        if names is not None and y in LEGACY_SECTIONS:
            start = (y - LEGACY_SECTIONS.start) * BIOMES_PER_SECTION
            biomes = names[start:start + BIOMES_PER_SECTION]
        return Section(tag, legacy=refusal, legacy_biomes=biomes, run_on=run_on)

    @property
    def legacy_entities(self):
        """The entities a chunk before 20w45a keeps in ``Level.Entities``,
        or ``None`` for one whose entities live in ``entities/`` beside the
        region -- every current chunk, and 1.17's."""
        if not self.legacy or self.data_version >= LEGACY_ENTITY_STORAGE_DATA_VERSION:
            return None
        found = self._data.get("Entities")
        # as stored, so a tag that is no list is reported, never read as none
        return TagList(element_type=TAG_COMPOUND) if found is None else found

    def _refusal(self, doing: str) -> str:
        return (
            f"chunk ({self.world_x >> 4}, {self.world_z >> 4}) was saved by "
            f"Minecraft {legacy_release(self.data_version)} (DataVersion "
            f"{self.data_version}), which this "
            f"library reads but will not {doing}: open the world in a current "
            f"Minecraft to upgrade the chunk first"
        )

    def refuse_legacy(self, doing: str = "write") -> None:
        """Raise :class:`ReadOnlyChunk` if this is an old chunk, saying it
        will not ``doing`` it; nothing otherwise. What every writer above
        this one asks before its first write, so a call that reaches an old
        chunk is refused before it changes anything."""
        if self.legacy:
            raise ReadOnlyChunk(self._refusal(doing))

    # -- geometry ----------------------------------------------------------

    @property
    def status(self) -> str | None:
        """The chunk's generation stage, e.g. ``"minecraft:full"``.

        Anything else -- ``minecraft:carvers``, ``minecraft:biomes``, ... --
        is a chunk the game began generating and set aside. Such a chunk has
        blocks but no heightmaps, and visiting it in game will finish it.
        ``None`` if the chunk does not record a stage. As stored: an old
        chunk records its stages without the namespace (``"full"``).
        """
        found = self._data.get("Status")
        return str(found) if found is not None else None

    @property
    def finished(self) -> bool:
        """Whether the game finished generating this chunk; see
        :func:`chunk_finished`, and for a chunk before 1.14 the names its
        version gave finished (:func:`_legacy_finished`)."""
        if self.legacy:
            return _legacy_finished(self.status, self.data_version)
        return chunk_finished(self.status)

    @property
    def world_x(self) -> int:
        """World X of this chunk's western edge."""
        return int(self._data["xPos"]) * 16

    @property
    def world_z(self) -> int:
        """World Z of this chunk's northern edge."""
        return int(self._data["zPos"]) * 16

    @property
    def min_y(self) -> int:
        """The world's floor in this chunk: ``yPos * 16`` (-64 today).

        Every heightmap entry counts blocks above this, so it must be the
        floor the game counts from. The chunk's recorded ``yPos`` decides
        it; without one, the lowest section carrying blocks; without
        those, 0. A section stored below the floor holding only light --
        the game can keep one there, at Y=-5 -- is not part of the world,
        and taking it as the floor reads every heightmap 16 low and
        rebuilds them 16 high. An old chunk's floor is 0, which its
        heightmaps count from.
        """
        return self._min_y

    @property
    def max_y(self) -> int:
        """The topmost y this chunk can hold a block at (319 today).

        The top of the highest section carrying blocks. A section stored
        above it holding only light (Y=20) is above the build limit, so it
        is not counted here, and a range check against ``max_y`` refuses
        the cells it would. ``min_y - 1`` -- an empty range -- for a chunk
        storing no blocks. An old chunk's top is 255 whatever it stores,
        since they left out the sections that were all air.

        ``min_y..max_y`` is the whole chunk as far as blocks and biomes
        go: :meth:`get`, :meth:`set` and the biome accessors raise
        ``IndexError`` outside it, exactly as where nothing is stored, even
        where a section of light is -- otherwise a cell past the world
        would read as air in the chunks that keep one and as nothing in
        the chunks that do not. Only the light reads reach such a section.
        """
        return self._max_y

    def _locate(
        self, x: int, y: int, z: int, *, light: bool = False
    ) -> tuple[Section, int]:
        if not (0 <= x < 16 and 0 <= z < 16):
            raise IndexError(
                f"x and z are chunk-local and must be 0-15, got ({x}, {z}); "
                f"this chunk spans world x {self.world_x}-{self.world_x + 15}, "
                f"z {self.world_z}-{self.world_z + 15}"
            )
        section = self.sections.get(y >> 4)
        # A section past either end of the world holds light and nothing
        # else, so only a light read may land in one.
        if section is None or not (light or self._min_y <= y <= self._max_y):
            raise IndexError(
                f"y={y} is outside this chunk, which spans {self.min_y}..{self.max_y}"
            )
        # Blocks are ordered y, then z, then x.
        return section, (y & 15) * 256 + z * 16 + x

    # -- blocks ------------------------------------------------------------

    def get(self, x: int, y: int, z: int) -> BlockState:
        section, index = self._locate(x, y, z)
        container = section.blocks
        if container is None:
            return BlockState("minecraft:air")
        return container.get(index)

    def set(self, x: int, y: int, z: int, state: BlockState | str) -> None:
        self.refuse_legacy()
        section, index = self._locate(x, y, z)
        container = section.blocks
        if container is None:
            raise NBTError(f"section Y={y >> 4} has no block_states to write to")
        container.set(index, state)

    def __getitem__(self, position: tuple[int, int, int]) -> BlockState:
        return self.get(*position)

    def __setitem__(
        self, position: tuple[int, int, int], state: BlockState | str
    ) -> None:
        self.set(*position, state)

    # -- biomes ------------------------------------------------------------

    def get_biome(self, x: int, y: int, z: int) -> str:
        """Biome at a position. Biomes are stored per 4x4x4 cell."""
        section, _ = self._locate(x, y, z)
        container = section.biomes
        if container is None:
            raise NBTError(f"section Y={y >> 4} has no biome data")
        return container.get(((y & 15) >> 2) * 16 + (z >> 2) * 4 + (x >> 2))

    def set_biome(self, x: int, y: int, z: int, biome: str) -> None:
        self.refuse_legacy()
        section, _ = self._locate(x, y, z)
        container = section.biomes
        if container is None:
            raise NBTError(f"section Y={y >> 4} has no biome data")
        container.set(((y & 15) >> 2) * 16 + (z >> 2) * 4 + (x >> 2), biome)

    # -- light -------------------------------------------------------------

    def block_light(self, x: int, y: int, z: int) -> int | None:
        """Stored block light at a position, or ``None`` where no array is
        stored. Bulk readers should use :meth:`Section.block_light`."""
        section, index = self._locate(x, y, z, light=True)
        values = section.block_light()
        return None if values is None else values[index]

    def sky_light(self, x: int, y: int, z: int) -> int | None:
        """Stored sky light at a position, or ``None`` where no array is
        stored. Bulk readers should use :meth:`Section.sky_light`."""
        section, index = self._locate(x, y, z, light=True)
        values = section.sky_light()
        return None if values is None else values[index]

    # -- heightmaps --------------------------------------------------------

    def surface_y(self, x: int, z: int, heightmap: str = "MOTION_BLOCKING") -> int | None:
        """World y of the topmost surface block in a column, or ``None``.

        ``x`` and ``z`` are chunk-local (0-15), like every other accessor
        here. ``None`` means the chunk carries no such map -- partly
        generated chunks have none at all -- or that nothing in the column
        registers on it. Reading the map is a lookup, not a scan: ~90x
        faster than walking down the blocks, and it is exactly the answer
        the game itself maintains.

        The four maps answer different questions: ``WORLD_SURFACE`` is the
        topmost non-air block (it counts the flower you stand *in*),
        ``MOTION_BLOCKING`` the topmost thing that stops you (water counts,
        torches do not) -- the block you stand *on*, and the default --
        ``MOTION_BLOCKING_NO_LEAVES`` the same ignoring foliage, and
        ``OCEAN_FLOOR`` the topmost solid ignoring fluids: the seabed.

        Each map is unpacked once per accessor and cached; edits made to the
        stored heightmap tags afterwards are not seen until the chunk is
        released and re-read. A stored map whose length does not match the
        world's height raises rather than unpacking nonsense.
        """
        if not (0 <= x < 16 and 0 <= z < 16):
            raise IndexError(
                f"x and z are chunk-local and must be 0-15, got ({x}, {z})"
            )
        if heightmap not in self._heightmaps:
            self._heightmaps[heightmap] = self._unpack_heightmap(heightmap)
        values = self._heightmaps[heightmap]
        if values is None:
            return None
        height = values[z * 16 + x]
        if height == 0:
            return None  # nothing in this column registers on the map
        # The stored value counts blocks above min_y, so the topmost block
        # sits one below the height it reports.
        return self.min_y + height - 1

    def drop_heightmap_cache(self) -> None:
        """Forget the unpacked heightmaps, so the next read decodes fresh.

        For code that rewrites the stored ``Heightmaps`` tags on a chunk it
        keeps loaded: without this, :meth:`surface_y` keeps answering from
        the maps as they were first read.
        """
        self._heightmaps.clear()

    def derive_heightmaps(self) -> dict[str, list[int]]:
        """All four maps as the game would store them, read off the blocks.

        ``{name: heights}``, 256 values in storage order, each the number of
        cells from ``min_y`` to the top of the column's topmost block that
        map counts, 0 for a column with none. Refuses an old chunk: the
        tables behind it are the current game's, and it stores names and
        rules of its own.
        """
        self.refuse_legacy("derive heightmaps for")
        heights = {name: [0] * COLUMNS_PER_CHUNK for name in HEIGHTMAP_NAMES}
        found = [0] * COLUMNS_PER_CHUNK
        every_map = (1 << len(HEIGHTMAP_NAMES)) - 1
        for section_y in sorted(self.sections, reverse=True):
            container = self.sections[section_y].blocks
            if container is None:
                continue
            masks = [heightmap_mask(state) for state in container.palette]
            if not any(masks):
                continue
            indices = container.indices()
            base = section_y * 16 - self.min_y + 1
            for column in range(COLUMNS_PER_CHUNK):
                for ly in range(15, -1, -1):
                    if found[column] == every_map:
                        break
                    new = masks[indices[ly * 256 + column]] & ~found[column]
                    if new:
                        found[column] |= new
                        for bit, name in enumerate(HEIGHTMAP_NAMES):
                            if new >> bit & 1:
                                heights[name][column] = base + ly
            if all(mask == every_map for mask in found):
                break
        return heights

    def rebuild_heightmaps(self) -> None:
        """Rewrite the chunk's four stored maps from its blocks, in the
        order the game writes them, so a map that was right comes back the
        same bytes. Refuses an old chunk."""
        self.refuse_legacy("rebuild heightmaps in")
        bits = heightmap_bits(self.min_y, self.max_y)
        derived = self.derive_heightmaps()
        self._data["Heightmaps"] = TagCompound(
            {
                name: pack_indices(derived[name], COLUMNS_PER_CHUNK, bits)
                for name in _STORED_HEIGHTMAPS
            }
        )
        self.drop_heightmap_cache()

    def stale_heightmaps(self) -> dict[str, int]:
        """How many columns of each stored map disagree with the blocks.

        ``{name: columns}``, naming only the maps that are wrong, so an
        empty dict is a chunk whose maps are right. A map the chunk does not
        store is not stale: the game derives a missing map when it loads
        the chunk. The cost is reading every section's palette, and the
        indices of each one any column's map stops in. Refuses an old chunk.
        """
        derived = self.derive_heightmaps()
        stale = {}
        for name in HEIGHTMAP_NAMES:
            stored = self._unpack_heightmap(name)
            if stored is None:
                continue
            wrong = sum(a != b for a, b in zip(stored, derived[name]))
            if wrong:
                stale[name] = wrong
        return stale

    def _unpack_heightmap(self, heightmap: str) -> list[int] | None:
        maps = self._data.get("Heightmaps")
        if not maps or heightmap not in maps:
            return None
        raw = maps[heightmap]
        bits = heightmap_bits(self.min_y, self.max_y)
        if self.legacy and self.data_version < LEGACY_ALIGNED_DATA_VERSION:
            # before 1.16 a heightmap's entries run on across longs too
            expected = (COLUMNS_PER_CHUNK * bits + 63) // 64
            if len(raw) != expected:
                raise NBTError(
                    f"chunk at ({self.world_x}, {self.world_z}) has a "
                    f"{len(raw)}-long {heightmap} heightmap, expected {expected} "
                    f"run on at {bits} bits per entry")
            return _unpack_run_on(raw, COLUMNS_PER_CHUNK, bits)
        per_long = 64 // bits
        expected = (COLUMNS_PER_CHUNK + per_long - 1) // per_long
        if len(raw) != expected:
            # Refuse rather than unpack nonsense: a mismatch here means the
            # world height is not what this chunk's sections say it is.
            raise NBTError(
                f"chunk at ({self.world_x}, {self.world_z}) has a "
                f"{len(raw)}-long {heightmap} heightmap, expected {expected} "
                f"for a {self.min_y}..{self.max_y} world at {bits} bits per entry"
            )
        return unpack_indices(raw, COLUMNS_PER_CHUNK, bits)

    # -- lighting ----------------------------------------------------------

    def reset_lighting(self) -> int:
        """Make Minecraft recompute this chunk's light on its next load.

        Chunk light is *baked*: per-section ``BlockLight`` and ``SkyLight``
        arrays that the game trusts for as long as the chunk-level
        ``isLightOn`` flag stands. Editing blocks leaves that data
        describing a world that no longer exists -- a torch placed in a
        newly carved room lights nothing but its own block, because the
        darkness around it is stored data, not something the game
        re-derives. Clearing the flag (and dropping the stale arrays with
        it) hands lighting back to the game, which relights the chunk when
        it next loads it.

        Light propagates up to 15 blocks across chunk borders, so an edit
        near an edge needs the neighboring chunk reset as well;
        :meth:`heaton_voxel.world.MinecraftSave.reset_lighting` covers an
        area plus that ring in one call.

        Returns the number of stale light arrays removed. The flag is a
        byte in the game's own encoding, so a reset chunk still re-encodes
        in the format Minecraft wrote it in.
        """
        self.refuse_legacy()
        self._data["isLightOn"] = TagByte(0)
        removed = 0
        for tag in self._data.get("sections") or []:
            removed += tag.pop("BlockLight", None) is not None
            removed += tag.pop("SkyLight", None) is not None
        return removed

    # -- bulk --------------------------------------------------------------

    def histogram(self) -> dict[BlockState, int]:
        """Count every block state in the chunk. Unpacks everything."""
        counts: dict[BlockState, int] = {}
        for section in self.sections.values():
            container = section.blocks
            if container is None:
                continue
            for state, n in container.histogram().items():
                counts[state] = counts.get(state, 0) + n
        return counts

    def prune(self) -> int:
        """Drop unreferenced palette entries across every section.

        Covers both the block and the biome container of each section --
        ``set_biome`` orphans entries exactly the way ``set`` does. Returns the
        total number of entries removed, blocks and biomes together; the two
        palettes are independent, so a caller that needs them apart should
        prune ``section.blocks`` and ``section.biomes`` itself.
        """
        self.refuse_legacy()
        removed = 0
        for section in self.sections.values():
            for container in (section.blocks, section.biomes):
                if container is not None:
                    removed += container.prune()
        return removed

    def __iter__(self) -> Iterator[Section]:
        return iter(self.sections[y] for y in sorted(self.sections))

    def __repr__(self) -> str:
        return (
            f"<Blocks chunk at world ({self.world_x}, {self.world_z}) "
            f"y {self.min_y}..{self.max_y}, {len(self.sections)} sections>"
        )
