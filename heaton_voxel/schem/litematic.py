"""Litematica schematics: the ``.litematic`` the technical community shares
builds in, read into and written from a
:class:`~heaton_voxel.schem.Schematic`.

The format is the mod's own (``LitematicaSchematic``, version 7 since
1.20.5, read here from version 5, its first of block states)::

    {MinecraftDataVersion, Version, SubVersion,
     Metadata: {Name, Author, Description, RegionCount, TotalVolume,
                TotalBlocks, TimeCreated, TimeModified, EnclosingSize: {x,y,z}},
     Regions: {name: {Position: {x,y,z}, Size: {x,y,z},
                      BlockStatePalette: [{Name, Properties}, ...],
                      BlockStates: [L; ...],
                      TileEntities: [{x, y, z, id, ...}, ...],
                      Entities: [{id, Pos, ...}, ...],
                      PendingBlockTicks, PendingFluidTicks}}}

What reading it takes to get right, each from the mod's source:

* **A size can be negative.** ``Position`` is the corner the region was
  selected from and ``Size`` counts away from it either way, so the region
  spans ``Position`` to ``Position + Size -/+ 1``. Blocks and tile entities
  count from the region's minimum corner; entities from ``Position``.
* **Blocks pack tightly, across longs.** Each cell takes
  ``max(2, bits(len(palette) - 1))`` bits, a cell may straddle two longs, and
  cells run ``x`` fastest, then ``z``, then ``y`` (``LitematicaBitArray``),
  where a chunk's palette never lets one straddle.
* **Air is the palette's first entry** whether or not any cell holds it,
  which writing keeps, as the mod's container does.

A file of several regions reads as one schematic enclosing them all, each
region where it stands, a later one over an earlier where they overlap, and
``minecraft:structure_void`` -- which paste skips -- in every cell no
region covers. Pending block and fluid ticks are not carried. Writing makes
one region, named ``Unnamed`` as a fresh Litematica selection is, its states
``{Name, Properties}`` with every property, as the mod writes them, and its
``Metadata`` from the schematic's where it has one.
"""

from __future__ import annotations

from ..blocks import BlockState
from ..nbt import (
    TAG_COMPOUND,
    TAG_END,
    NBTError,
    TagCompound,
    TagInt,
    TagList,
    TagLong,
    TagLongArray,
    TagString,
)
from .structure import STRUCTURE_VOID, full_state_tag
from .support import _copy_tag, _shift_entity

__all__ = ["LITEMATIC_VERSION", "is_litematic"]

#: The container version written, and the oldest one read: version 5 is the
#: first to store block states rather than numeric ids.
LITEMATIC_VERSION = 7
LITEMATIC_SUB_VERSION = 1
_OLDEST_VERSION = 5

_MASK64 = (1 << 64) - 1


def is_litematic(root: TagCompound) -> bool:
    """Is this the root of a Litematica schematic?"""
    return "Regions" in root and "Metadata" in root and "Version" in root


def _bits(palette_size: int) -> int:
    """``LitematicaBlockStateContainer``'s bits per cell for a palette."""
    return max(2, (palette_size - 1).bit_length())


def _vector(tag, what: str) -> tuple[int, int, int]:
    if not isinstance(tag, TagCompound) or not all(k in tag for k in "xyz"):
        raise NBTError(f"{what}: expected {{x, y, z}}")
    return int(tag["x"]), int(tag["y"]), int(tag["z"])


def unpack_tight(longs, count: int, bits: int) -> list[int]:
    """``count`` values of ``bits`` bits each packed end to end across a
    long array, a value straddling two longs where it falls on the seam --
    Litematica's ``LitematicaBitArray``."""
    needed = (count * bits + 63) // 64
    if len(longs) < needed:
        raise NBTError(f"block data holds {len(longs)} longs, {needed} needed")
    words = [int(v) & _MASK64 for v in longs]
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


def pack_tight(values, bits: int) -> TagLongArray:
    """The inverse of :func:`unpack_tight`."""
    words = [0] * ((len(values) * bits + 63) // 64)
    for index, value in enumerate(values):
        start = index * bits
        word, shift = start >> 6, start & 63
        words[word] |= (value << shift) & _MASK64
        if shift + bits > 64:
            words[word + 1] |= value >> (64 - shift)
    return TagLongArray([w - (1 << 64) if w >> 63 else w for w in words])


def _region_box(position, size, what: str):
    """``(minimum corner, dimensions)`` of a region selected from
    ``position`` counting ``size`` away from it, either way."""
    low, dims = [], []
    for axis, (start, count) in enumerate(zip(position, size)):
        if count == 0:
            raise NBTError(f"{what}: a region of size 0 along axis {'xyz'[axis]}")
        end = start + (count - 1 if count > 0 else count + 1)
        low.append(min(start, end))
        dims.append(abs(count))
    return tuple(low), tuple(dims)


def read_litematic(cls, root: TagCompound, path: str):
    """The :class:`Schematic` a Litematica file holds, its regions in one
    box; ``cls`` is the class to build. Refuses a container older than
    version 5, a file with no regions, a state index outside a palette, and
    a tile entity or entity with no id."""
    version = int(root["Version"])
    if not _OLDEST_VERSION <= version <= LITEMATIC_VERSION:
        raise NBTError(f"{path!r} is Litematica version {version}; versions "
                       f"{_OLDEST_VERSION} to {LITEMATIC_VERSION} store block states")
    if "MinecraftDataVersion" not in root:
        raise NBTError(f"{path!r} has no MinecraftDataVersion, so there is no telling "
                       f"which Minecraft its block names belong to; refusing to guess.")
    regions = root["Regions"]
    if not isinstance(regions, TagCompound) or not regions:
        raise NBTError(f"{path!r} holds no regions")
    boxes = {}
    for name, region in regions.items():
        what = f"{path!r} region {name!r}"
        position = _vector(region.get("Position"), f"{what} Position")
        size = _vector(region.get("Size"), f"{what} Size")
        boxes[name] = (position, *_region_box(position, size, what))
    low = tuple(min(box[1][a] for box in boxes.values()) for a in range(3))
    high = tuple(max(box[1][a] + box[2][a] - 1 for box in boxes.values()) for a in range(3))
    width, height, length = (high[a] - low[a] + 1 for a in range(3))

    schematic = cls(width, height, length, int(root["MinecraftDataVersion"]), offset=low)
    schematic.source_format = "litematic"
    schematic.source_version = version
    metadata = root.get("Metadata")
    schematic.metadata = _copy_tag(metadata) if isinstance(metadata, TagCompound) else None
    void = schematic._intern(BlockState(STRUCTURE_VOID))
    schematic._blocks = [void] * schematic.volume

    for name, region in regions.items():
        what = f"{path!r} region {name!r}"
        position, corner, (sx, sy, sz) = boxes[name]
        ox, oy, oz = (corner[a] - low[a] for a in range(3))
        palette = region.get("BlockStatePalette")
        if not isinstance(palette, TagList) or not palette:
            raise NBTError(f"{what}: no BlockStatePalette")
        slots = [schematic._intern(BlockState.from_nbt(entry)) for entry in palette]
        cells = unpack_tight(region.get("BlockStates") or TagLongArray(),
                             sx * sy * sz, _bits(len(slots)))
        index = 0
        for y in range(sy):
            for z in range(sz):
                for x in range(sx):
                    value = cells[index]
                    index += 1
                    if value >= len(slots):
                        raise NBTError(f"{what}: block data names palette entry "
                                       f"{value} of {len(slots)}")
                    schematic._blocks[schematic._index(ox + x, oy + y, oz + z)] = slots[value]
        for entry in region.get("TileEntities") or []:
            x, y, z = (int(entry.get(k, TagInt(0))) for k in ("x", "y", "z"))
            data = TagCompound({k: _copy_tag(v) for k, v in entry.items()
                                if k not in ("x", "y", "z")})
            if "id" not in data:
                raise NBTError(f"{what}: the tile entity at ({x}, {y}, {z}) has no id")
            cell = (ox + x, oy + y, oz + z)
            schematic._index(*cell)
            schematic.block_entities[cell] = data
        for entry in region.get("Entities") or []:
            if "id" not in entry or "Pos" not in entry:
                raise NBTError(f"{what}: an entity with no id or no Pos")
            data = _copy_tag(entry)
            # entities count from the region's Position; the schematic's
            # from its own minimum corner
            _shift_entity(data, *(float(position[a] - low[a]) for a in range(3)))
            schematic.entities.append(data)
    return schematic


def litematic_root(schematic, *, region: str = "Unnamed") -> TagCompound:
    """The Litematica file a schematic is: one region at its offset."""
    width, height, length = schematic.size
    air = BlockState("minecraft:air")
    order = {air: 0}
    palette = TagList([full_state_tag(air, modern=False)], TAG_COMPOUND)
    values = []
    non_air = 0
    for y in range(height):
        for z in range(length):
            for x in range(width):
                state = schematic._palette[schematic._blocks[schematic._index(x, y, z)]]
                if state.name != "minecraft:air":
                    non_air += 1
                if state not in order:
                    order[state] = len(order)
                    palette.append(full_state_tag(state, modern=False))
                values.append(order[state])
    tiles = TagList(element_type=TAG_COMPOUND if schematic.block_entities else TAG_END)
    for (x, y, z), data in sorted(schematic.block_entities.items()):
        entry = _copy_tag(data)
        entry["x"], entry["y"], entry["z"] = TagInt(x), TagInt(y), TagInt(z)
        tiles.append(entry)
    entities = TagList([_copy_tag(data) for data in schematic.entities],
                       TAG_COMPOUND if schematic.entities else TAG_END)

    def vector(x, y, z):
        return TagCompound({"x": TagInt(x), "y": TagInt(y), "z": TagInt(z)})

    given = schematic.metadata or TagCompound()
    metadata = TagCompound()
    for key in ("Name", "Author", "Description"):
        metadata[key] = TagString(str(given.get(key, TagString(""))))
    metadata["RegionCount"] = TagInt(1)
    metadata["TotalVolume"] = TagInt(width * height * length)
    metadata["TotalBlocks"] = TagInt(non_air)
    # the schematic's own times, or 0: never the clock, so a schematic
    # writes the same bytes every time it is written
    for key in ("TimeCreated", "TimeModified"):
        metadata[key] = TagLong(int(given.get(key, TagLong(0))))
    metadata["EnclosingSize"] = vector(width, height, length)
    if "PreviewImageData" in given:
        metadata["PreviewImageData"] = _copy_tag(given["PreviewImageData"])

    body = TagCompound({
        "BlockStatePalette": palette,
        "BlockStates": pack_tight(values, _bits(len(order))),
        "TileEntities": tiles,
        # empty, as the mod writes them for a region taken from a world:
        # this library carries no pending ticks, and readers expect the lists
        "PendingBlockTicks": TagList(element_type=TAG_END),
        "PendingFluidTicks": TagList(element_type=TAG_END),
        "Entities": entities,
        "Position": vector(*schematic.offset),
        "Size": vector(width, height, length),
    })
    return TagCompound({
        "MinecraftDataVersion": TagInt(schematic.data_version),
        "Version": TagInt(LITEMATIC_VERSION),
        "SubVersion": TagInt(LITEMATIC_SUB_VERSION),
        "Metadata": metadata,
        "Regions": TagCompound({region: body}),
    })
