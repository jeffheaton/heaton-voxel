"""Shared plumbing for the schematic package: the format's varints, deep
tag copies, entity translation, and identity handling."""

from __future__ import annotations

from typing import Any, Iterable

from ..blocks import AIR_BLOCKS
from ..nbt import (
    NBTError,
    TagByteArray,
    TagCompound,
    TagDouble,
    TagInt,
    TagIntArray,
    TagList,
    TagLongArray,
    TagShort,
)
from ..nbt import fresh_uuid as _fresh_uuid

# AIR_BLOCKS, public here as schem.AIR_BLOCKS, is what WorldEdit's "-a" mask
# treats as air: pasting with paste_air=False skips these, so a schematic's
# empty space does not punch holes in terrain. It is the game's own air tag,
# the one set blocks.py keeps for the whole library.

_MAX_EDGE = 0xFFFF  # Width/Height/Length are unsigned shorts in the format


def _read_varints(raw, expected: int, what: str) -> list[int]:
    """Decode the format's LEB128-style varints (7 bits per byte, high bit
    means continue). ``raw`` is an NBT byte array, so entries are signed."""
    values: list[int] = []
    value = 0
    shift = 0
    for position, byte in enumerate(raw):
        byte &= 0xFF
        value |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
            if shift > 31:
                raise NBTError(f"{what}: varint longer than 5 bytes at byte {position}")
        else:
            values.append(value)
            value = 0
            shift = 0
    if shift:
        raise NBTError(f"{what}: truncated varint at end of array")
    if len(values) != expected:
        raise NBTError(f"{what}: holds {len(values)} entries, expected {expected}")
    return values


def _write_varints(values: Iterable[int]) -> TagByteArray:
    out = bytearray()
    for value in values:
        while True:
            byte = value & 0x7F
            value >>= 7
            if value:
                out.append(byte | 0x80)
            else:
                out.append(byte)
                break
    array = TagByteArray()
    array.frombytes(bytes(out))
    return array


def _unsigned_short(value: int, name: str) -> TagShort:
    """Encode 0..65535 into NBT's signed short, as the spec requires."""
    if not (0 <= value <= _MAX_EDGE):
        raise NBTError(f"{name} {value} does not fit the format's unsigned short")
    return TagShort(value - 0x10000 if value > 0x7FFF else value)


def _copy_tag(tag: Any) -> Any:
    """Deep-copy a tag tree.

    ``copy.deepcopy`` cannot be trusted here: reconstructing a ``TagList``
    through its constructor loses ``element_type``, and the array subclasses
    carry their typecode in ``__new__``. Scalars are immutable and shared.
    """
    if isinstance(tag, TagCompound):
        return TagCompound({key: _copy_tag(value) for key, value in tag.items()})
    if isinstance(tag, TagList):
        return TagList(
            [_copy_tag(item) for item in tag], element_type=tag.element_type
        )
    if isinstance(tag, (TagByteArray, TagIntArray, TagLongArray)):
        return type(tag)(tag)
    return tag




def _collect_uuids(tag: Any, found: dict) -> None:
    """Record every ``UUID`` int-array in a tree (passengers included)."""
    if isinstance(tag, TagCompound):
        stored = tag.get("UUID")
        if isinstance(stored, TagIntArray) and len(stored) == 4:
            found.setdefault(tuple(stored), None)
        for value in tag.values():
            _collect_uuids(value, found)
    elif isinstance(tag, TagList):
        for item in tag:
            _collect_uuids(item, found)


def _remap_uuids(tag: Any, mapping: dict) -> None:
    """Replace any four-int array matching a remapped UUID, in place.

    Applied to *every* int-array of that shape, not just ``UUID`` keys, so
    references between pasted entities -- a leash, a vehicle, an owner --
    keep pointing at each other after both ends get fresh identities. A
    coincidental match against an unrelated 128-bit value is not a realistic
    concern.
    """
    if isinstance(tag, TagCompound):
        for key, value in tag.items():
            if isinstance(value, TagIntArray) and len(value) == 4:
                replacement = mapping.get(tuple(value))
                if replacement is not None:
                    tag[key] = TagIntArray(replacement)
            else:
                _remap_uuids(value, mapping)
    elif isinstance(tag, TagList):
        for item in tag:
            _remap_uuids(item, mapping)


def _shift_entity(tag: TagCompound, dx: float, dy: float, dz: float) -> None:
    """Translate an entity compound, in place.

    ``Pos`` always moves. The other stored absolute positions this rewrites
    are the ones current entities actually carry: the ``block_pos`` a
    painting, item frame or leash knot hangs on (``TileX/Y/Z`` in chunks
    saved before the rename), a leashed mob's ``leash`` {X, Y, Z}, a
    ``sleeping_pos``, and the same again for every passenger. Anything more
    exotic (villager brain memories, patrol targets) is copied verbatim.
    """
    pos = tag.get("Pos")
    if isinstance(pos, TagList) and len(pos) == 3:
        for axis, delta in enumerate((dx, dy, dz)):
            pos[axis] = TagDouble(float(pos[axis]) + delta)
    block = tag.get("block_pos")
    if isinstance(block, TagIntArray) and len(block) == 3:
        tag["block_pos"] = TagIntArray(
            [int(block[0]) + int(dx), int(block[1]) + int(dy), int(block[2]) + int(dz)]
        )
    for name, delta in (("TileX", dx), ("TileY", dy), ("TileZ", dz)):
        if name in tag:
            tag[name] = TagInt(int(tag[name]) + int(delta))
    for key in ("leash", "Leash"):
        leash = tag.get(key)
        if isinstance(leash, TagCompound) and all(k in leash for k in "XYZ"):
            for letter, delta in (("X", dx), ("Y", dy), ("Z", dz)):
                leash[letter] = TagInt(int(leash[letter]) + int(delta))
    sleeping = tag.get("sleeping_pos")
    if isinstance(sleeping, TagIntArray) and len(sleeping) == 3:
        tag["sleeping_pos"] = TagIntArray(
            [int(sleeping[0]) + int(dx), int(sleeping[1]) + int(dy), int(sleeping[2]) + int(dz)]
        )
    passengers = tag.get("Passengers")
    if isinstance(passengers, TagList):
        for passenger in passengers:
            if isinstance(passenger, TagCompound):
                _shift_entity(passenger, dx, dy, dz)
