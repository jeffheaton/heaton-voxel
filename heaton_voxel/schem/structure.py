"""Vanilla structure files: the ``.nbt`` a structure block saves, and the
pieces a data pack's jigsaws assemble, read into and written from a
:class:`~heaton_voxel.schem.Schematic`.

The game's own format (``StructureTemplate.save``)::

    {size: [w, h, l],
     palette: [{id, properties}, ...],        # {Name, Properties} before 26.3
     blocks: [{pos: [x, y, z], state: i, nbt: {id, ...}}, ...],
     entities: [{pos: [x, y, z], blockPos: [x, y, z], nbt: {id, ...}}, ...],
     DataVersion: n}

A cell the file does not list is one the structure leaves as it stands
when placed -- the game saves a ``structure_void`` that way, and never as a
block -- so a structure reads with ``minecraft:structure_void`` in every
cell it leaves out, which :meth:`MinecraftSave.import_schem` skips, and
writes every ``structure_void`` cell back out of the list. Air is a block
like any other: a structure that clears a room says so cell by cell.

A file may hold several palettes (``palettes``), one of which the game
picks at random each time it places the structure -- a shipwreck's
weathering, say. The first is read, and :attr:`Schematic.source_palettes`
says how many there were. A block's states are written with every property
its block has, as the game writes them, in the codec the schematic's
DataVersion reads: ``{id, properties}`` from 26.3 (DataVersion 5006),
``{Name, Properties}`` before.

``tests/test_structures.py`` holds both directions to the game: files this
module writes, loaded and saved again by the game's own ``StructureTemplate``
(``tools/probe_structures.py``), read back as what was written.
"""

from __future__ import annotations

import math

from ..block_defaults import default_properties
from ..blocks import MODERN_STATE_DATA_VERSION, BlockState
from ..nbt import (
    TAG_COMPOUND,
    TAG_DOUBLE,
    TAG_END,
    TAG_INT,
    NBTError,
    TagCompound,
    TagDouble,
    TagInt,
    TagList,
    TagString,
)
from .support import _copy_tag

__all__ = ["STRUCTURE_VOID", "is_structure"]

#: The block a structure saves as "leave this cell alone": never a block in
#: the file, a cell left out of it.
STRUCTURE_VOID = "minecraft:structure_void"


def is_structure(root: TagCompound) -> bool:
    """Is this the root of a vanilla structure file?"""
    return ("size" in root and "blocks" in root
            and ("palette" in root or "palettes" in root))


def full_state_tag(state: BlockState, *, modern: bool) -> TagCompound:
    """A block state as ``NbtUtils.writeBlockState`` writes it: the id and,
    for a block that has properties, every one of them in name order, its
    defaults filled in -- ``{id, properties}`` when ``modern``, otherwise
    ``{Name, Properties}``. A block the defaults table does not know keeps
    the properties it was given."""
    properties = default_properties(state.name)
    properties.update(state.properties)
    tag = TagCompound()
    tag["id" if modern else "Name"] = TagString(state.name)
    if properties:
        tag["properties" if modern else "Properties"] = TagCompound(
            {key: TagString(value) for key, value in sorted(properties.items())})
    return tag


def _ints(tag, count: int, what: str) -> list[int]:
    if not isinstance(tag, TagList) or len(tag) != count:
        raise NBTError(f"{what}: expected a list of {count} numbers")
    return [int(v) for v in tag]


def read_structure(cls, root: TagCompound, path: str):
    """The :class:`Schematic` a structure file holds; ``cls`` is the class
    to build. Refuses, with its reason, a file with no DataVersion or one
    from before 1.18, a position outside the size, a state index outside
    the palette, and a block entity or entity with no id."""
    if "DataVersion" not in root:
        raise NBTError(f"{path!r} has no DataVersion, so there is no telling which "
                       f"Minecraft its block names belong to; refusing to guess.")
    width, height, length = _ints(root["size"], 3, f"{path!r} size")
    schematic = cls(width, height, length, int(root["DataVersion"]))
    schematic.source_format = "structure"

    palettes = root.get("palettes")
    if palettes is not None:
        if not isinstance(palettes, TagList) or not palettes:
            raise NBTError(f"{path!r}: 'palettes' holds no palette")
        palette_tag = palettes[0]
        schematic.source_palettes = len(palettes)
    else:
        palette_tag = root["palette"]
        schematic.source_palettes = 1
    states = [BlockState.from_nbt(entry) for entry in palette_tag]

    void = schematic._intern(BlockState(STRUCTURE_VOID))
    schematic._blocks = [void] * schematic.volume
    slots = [schematic._intern(state) for state in states]
    for entry in root["blocks"]:
        x, y, z = _ints(entry.get("pos"), 3, f"{path!r} block pos")
        index = int(entry.get("state", TagInt(-1)))
        if not 0 <= index < len(slots):
            raise NBTError(f"{path!r}: block at ({x}, {y}, {z}) names palette entry "
                           f"{index} of {len(slots)}")
        schematic._blocks[schematic._index(x, y, z)] = slots[index]
        data = entry.get("nbt")
        if isinstance(data, TagCompound):
            if "id" not in data:
                raise NBTError(f"{path!r}: the block entity at ({x}, {y}, {z}) has no id")
            schematic.block_entities[(x, y, z)] = _copy_tag(data)
    for entry in root.get("entities") or []:
        pos = entry.get("pos")
        if not isinstance(pos, TagList) or len(pos) != 3:
            raise NBTError(f"{path!r}: an entity without a 3-number pos")
        data = entry.get("nbt")
        if not isinstance(data, TagCompound) or "id" not in data:
            raise NBTError(f"{path!r}: an entity at {tuple(float(v) for v in pos)} has no id")
        data = _copy_tag(data)
        data["Pos"] = TagList([TagDouble(float(v)) for v in pos], TAG_DOUBLE)
        schematic.entities.append(data)
    return schematic


def structure_root(schematic) -> TagCompound:
    """The structure file a schematic is: every cell but its
    ``structure_void`` ones listed, those without a block entity first and
    each run in ``y, x, z`` order, as the game lists them."""
    modern = schematic.data_version >= MODERN_STATE_DATA_VERSION
    width, height, length = schematic.size
    order: dict[int, int] = {}
    palette = TagList(element_type=TAG_COMPOUND)
    plain, holding = [], []
    for y in range(height):
        for x in range(width):
            for z in range(length):
                slot = schematic._blocks[schematic._index(x, y, z)]
                state = schematic._palette[slot]
                if state.name == STRUCTURE_VOID:
                    continue
                if slot not in order:
                    order[slot] = len(order)
                    palette.append(full_state_tag(state, modern=modern))
                entry = TagCompound({
                    "pos": TagList([TagInt(x), TagInt(y), TagInt(z)], TAG_INT),
                    "state": TagInt(order[slot]),
                })
                data = schematic.block_entities.get((x, y, z))
                if data is not None:
                    entry["nbt"] = _copy_tag(data)
                    holding.append(entry)
                else:
                    plain.append(entry)
    blocks = TagList(plain + holding, TAG_COMPOUND if plain or holding else TAG_END)
    entities = TagList(element_type=TAG_COMPOUND if schematic.entities else TAG_END)
    for data in schematic.entities:
        if "id" not in data:
            raise NBTError("an entity in this schematic has no 'id'")
        pos = [float(v) for v in data["Pos"]]
        entities.append(TagCompound({
            "pos": TagList([TagDouble(v) for v in pos], TAG_DOUBLE),
            "blockPos": TagList([TagInt(math.floor(v)) for v in pos], TAG_INT),
            "nbt": _copy_tag(data),
        }))
    return TagCompound({
        "size": TagList([TagInt(width), TagInt(height), TagInt(length)], TAG_INT),
        "entities": entities,
        "blocks": blocks,
        "palette": palette,
        "DataVersion": TagInt(schematic.data_version),
    })
