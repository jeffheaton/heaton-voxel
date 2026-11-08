"""Lossless reader/writer for Minecraft NBT (Named Binary Tag) data.

Handles both shapes of NBT file that Minecraft (Java Edition) writes:

* **Standalone NBT** -- ``level.dat``, ``playerdata/*.dat``, structure files
  and so on, stored gzip-compressed, zlib-compressed or raw.
* **Anvil region containers** -- ``.mca`` / ``.mcr``, which pack up to 1024
  independently-compressed chunks behind a sector-allocation table.

Everything required to rebuild the original file byte-for-byte is retained,
including the details that naive implementations silently drop:

* Java's *modified* UTF-8 string encoding (``NUL`` as ``C0 80``, astral
  characters as CESU-8 surrogate pairs), with a raw-bytes fallback for
  non-canonical encodings that would not survive a decode/encode round trip.
* The declared element type of an *empty* ``TAG_List``.
* Per-chunk compression scheme, sector offsets and sector counts.
* Chunk timestamps, including those of chunks that are no longer present.
* The stale bytes Minecraft leaves in the slack space at the end of a
  chunk's final sector, and the contents of unallocated sectors.

Unmodified data is written back using its original compressed bytes, so
``load()`` followed by ``save()`` reproduces the input exactly rather than
merely equivalently.

Chunks are decoded lazily -- a loaded region holds compressed bytes until
something reads :attr:`Chunk.data` -- and :meth:`Chunk.release` hands the
memory back, so a pass over a whole world stays flat rather than growing to
tens of gigabytes.

Writing is done through a temporary file and a rename, and :meth:`NBT.save`
refuses by default to write into a world that Minecraft currently has open.

Pure standard library -- no Minecraft-specific dependencies.

Typical use::

    from heaton_voxel import NBT, TagString

    region = NBT.load("r.0.0.mca")
    print(region.chunk(0, 0).data["Status"])
    region.chunk(0, 0).data["Status"] = TagString("minecraft:empty")
    region.save()

    level = NBT.load("level.dat")
    print(level.root["Data"]["LevelName"])

Streaming a whole world without exhausting memory::

    for chunk in region:
        inspect(chunk.data)
        chunk.release()
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import stat
import struct
import sys
import tempfile
import time
import uuid as _uuid
import zlib
from array import array
from typing import Any, Callable, Iterator

__all__ = [
    "NBT",
    "Chunk",
    "Tag",
    "TagByte",
    "TagShort",
    "TagInt",
    "TagLong",
    "TagFloat",
    "TagDouble",
    "TagByteArray",
    "TagString",
    "TagList",
    "TagCompound",
    "TagIntArray",
    "TagLongArray",
    "NBTError",
    "dumps",
    "fresh_uuid",
    "atomic_write",
    "world_is_open",
    "find_world_root",
]


class NBTError(Exception):
    """Raised when a file cannot be parsed as NBT or as a region container."""


# --------------------------------------------------------------------------
# Tag type ids
# --------------------------------------------------------------------------

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12

TAG_NAMES = {
    TAG_END: "TAG_End",
    TAG_BYTE: "TAG_Byte",
    TAG_SHORT: "TAG_Short",
    TAG_INT: "TAG_Int",
    TAG_LONG: "TAG_Long",
    TAG_FLOAT: "TAG_Float",
    TAG_DOUBLE: "TAG_Double",
    TAG_BYTE_ARRAY: "TAG_Byte_Array",
    TAG_STRING: "TAG_String",
    TAG_LIST: "TAG_List",
    TAG_COMPOUND: "TAG_Compound",
    TAG_INT_ARRAY: "TAG_Int_Array",
    TAG_LONG_ARRAY: "TAG_Long_Array",
}


# --------------------------------------------------------------------------
# Java modified UTF-8
# --------------------------------------------------------------------------
#
# NBT strings use the encoding of java.io.DataOutputStream.writeUTF, which
# differs from standard UTF-8 in two ways: U+0000 is written as the two-byte
# sequence C0 80 so that no NUL appears in the stream, and characters outside
# the BMP are written as a CESU-8 surrogate pair (two 3-byte sequences)
# instead of a single 4-byte sequence.


def decode_modified_utf8(data: bytes) -> str:
    """Decode Java modified UTF-8 bytes into a ``str``.

    Lone surrogates are preserved as-is: they are legal in Java strings and
    Python permits them inside ``str``.
    """
    # Overwhelmingly the common case in Minecraft data, and provably identical
    # under both encodings.
    if data.isascii() and 0 not in data:
        return data.decode("ascii")

    units: list[int] = []
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        if b < 0x80:
            units.append(b)
            i += 1
        elif b & 0xE0 == 0xC0:
            if i + 1 >= n:
                raise NBTError(f"truncated 2-byte sequence at offset {i}")
            units.append(((b & 0x1F) << 6) | (data[i + 1] & 0x3F))
            i += 2
        elif b & 0xF0 == 0xE0:
            if i + 2 >= n:
                raise NBTError(f"truncated 3-byte sequence at offset {i}")
            units.append(
                ((b & 0x0F) << 12) | ((data[i + 1] & 0x3F) << 6) | (data[i + 2] & 0x3F)
            )
            i += 3
        else:
            raise NBTError(f"invalid modified UTF-8 lead byte 0x{b:02x} at offset {i}")

    # Recombine CESU-8 surrogate pairs into astral code points.
    out: list[str] = []
    j, m = 0, len(units)
    while j < m:
        u = units[j]
        if 0xD800 <= u <= 0xDBFF and j + 1 < m and 0xDC00 <= units[j + 1] <= 0xDFFF:
            out.append(chr(0x10000 + ((u - 0xD800) << 10) + (units[j + 1] - 0xDC00)))
            j += 2
        else:
            out.append(chr(u))
            j += 1
    return "".join(out)


def encode_modified_utf8(text: str) -> bytes:
    """Encode a ``str`` as Java modified UTF-8 bytes."""
    if text.isascii() and "\0" not in text:
        return text.encode("ascii")

    out = bytearray()
    for ch in text:
        cp = ord(ch)
        if 0x01 <= cp <= 0x7F:
            out.append(cp)
        elif cp == 0 or cp <= 0x7FF:
            out.append(0xC0 | (cp >> 6))
            out.append(0x80 | (cp & 0x3F))
        elif cp <= 0xFFFF:
            out.append(0xE0 | (cp >> 12))
            out.append(0x80 | ((cp >> 6) & 0x3F))
            out.append(0x80 | (cp & 0x3F))
        else:
            cp -= 0x10000
            for surrogate in (0xD800 | (cp >> 10), 0xDC00 | (cp & 0x3FF)):
                out.append(0xE0 | (surrogate >> 12))
                out.append(0x80 | ((surrogate >> 6) & 0x3F))
                out.append(0x80 | (surrogate & 0x3F))
    return bytes(out)


# --------------------------------------------------------------------------
# Tag classes
# --------------------------------------------------------------------------
#
# The scalar and container tags subclass their natural Python counterparts, so
# a parsed tree can be read and mutated with ordinary Python syntax while still
# carrying the tag type needed to write it back out.


class Tag:
    """Mixin marking a value as an NBT tag and carrying its type id."""

    __slots__ = ()
    ID = -1

    @property
    def tag_name(self) -> str:
        return TAG_NAMES.get(self.ID, f"TAG_Unknown({self.ID})")


class TagByte(int, Tag):
    ID = TAG_BYTE


class TagShort(int, Tag):
    ID = TAG_SHORT


class TagInt(int, Tag):
    ID = TAG_INT


class TagLong(int, Tag):
    ID = TAG_LONG


class TagFloat(float, Tag):
    """32-bit float.

    ``_raw`` is set only when the IEEE-754 bits do not survive the trip
    through a Python ``float`` (a NaN carrying a payload, for instance); the
    writer then re-emits the original four bytes.
    """

    ID = TAG_FLOAT
    _raw: bytes  # declared, never set on the class: absent unless needed


class TagDouble(float, Tag):
    """64-bit float. See :class:`TagFloat` regarding ``_raw``."""

    ID = TAG_DOUBLE
    _raw: bytes


class TagString(str, Tag):
    """UTF-8 text.

    ``_raw`` is set only when the source bytes are a non-canonical modified
    UTF-8 encoding (an overlong form, say) that re-encoding would normalize
    away; the writer then re-emits the original bytes.
    """

    ID = TAG_STRING
    _raw: bytes


class TagList(list, Tag):
    """Homogeneous sequence of unnamed tags.

    ``element_type`` records the type id declared in the file. It matters for
    empty lists, where the declaration is the only surviving evidence of what
    the list was meant to hold, and where writers disagree on whether to emit
    ``TAG_End`` or the real type.
    """

    ID = TAG_LIST

    def __init__(self, values=(), element_type: int = TAG_END):
        super().__init__(values)
        self.element_type = element_type

    def __repr__(self) -> str:
        return f"TagList[{TAG_NAMES.get(self.element_type, '?')}]({list.__repr__(self)})"


class TagCompound(dict, Tag):
    """Mapping of name to tag, preserving file order."""

    ID = TAG_COMPOUND

    def __repr__(self) -> str:
        return f"TagCompound({dict.__repr__(self)})"


# Integer array tags subclass array.array so large payloads stay compact and
# byte-swapping happens at C speed rather than one Python int at a time.

_I32 = "i" if array("i").itemsize == 4 else "l"
_I64 = "q" if array("q").itemsize == 8 else "l"
if array(_I32).itemsize != 4 or array(_I64).itemsize != 8:  # pragma: no cover
    raise ImportError("no array typecode with the required width on this platform")

_LITTLE_ENDIAN = sys.byteorder == "little"


class TagByteArray(array, Tag):
    """Array of signed 8-bit integers."""

    ID = TAG_BYTE_ARRAY

    def __new__(cls, values=()):
        return super().__new__(cls, "b", values)

    def __init__(self, values=()):  # noqa: D107 - array does the work in __new__
        super().__init__()

    def __repr__(self) -> str:
        return f"TagByteArray(len={len(self)})"


class TagIntArray(array, Tag):
    """Array of signed 32-bit integers."""

    ID = TAG_INT_ARRAY

    def __new__(cls, values=()):
        return super().__new__(cls, _I32, values)

    def __init__(self, values=()):
        super().__init__()

    def __repr__(self) -> str:
        return f"TagIntArray(len={len(self)})"


class TagLongArray(array, Tag):
    """Array of signed 64-bit integers."""

    ID = TAG_LONG_ARRAY

    def __new__(cls, values=()):
        return super().__new__(cls, _I64, values)

    def __init__(self, values=()):
        super().__init__()

    def __repr__(self) -> str:
        return f"TagLongArray(len={len(self)})"


def fresh_uuid() -> TagIntArray:
    """A new random UUID in Minecraft's NBT encoding.

    The game stores a UUID as four signed 32-bit words, most significant
    first. Every entity written into a world needs one that no other entity
    has -- two entities sharing a UUID make one of them vanish -- so
    anything that adds or copies entities mints fresh ones with this.
    """
    value = _uuid.uuid4().int
    words = []
    for shift in (96, 64, 32, 0):
        word = (value >> shift) & 0xFFFFFFFF
        words.append(word - 0x100000000 if word & 0x80000000 else word)
    return TagIntArray(words)


# --------------------------------------------------------------------------
# Binary decoding
# --------------------------------------------------------------------------

_S_BYTE = struct.Struct(">b")
_S_SHORT = struct.Struct(">h")
_S_USHORT = struct.Struct(">H")
_S_INT = struct.Struct(">i")
_S_UINT = struct.Struct(">I")
_S_LONG = struct.Struct(">q")
_S_FLOAT = struct.Struct(">f")
_S_DOUBLE = struct.Struct(">d")

# Each level of nesting costs a Python frame, so a corrupt or hostile file
# could otherwise exhaust the stack and raise RecursionError from deep inside
# the parser. Real Minecraft data nests around 10 levels.
MAX_DEPTH = 512
_TOO_DEEP = (
    "tag nesting exceeds the {0}-level limit at offset {{pos}}; the file is "
    "probably corrupt".format(MAX_DEPTH)
)


def _read_string(data: bytes, pos: int) -> tuple[TagString, int]:
    (length,) = _S_USHORT.unpack_from(data, pos)
    pos += 2
    raw = data[pos : pos + length]
    if len(raw) != length:
        raise NBTError(f"truncated string at offset {pos}")
    pos += length

    if raw.isascii() and 0 not in raw:
        return TagString(raw.decode("ascii")), pos

    text = TagString(decode_modified_utf8(raw))
    if encode_modified_utf8(text) != raw:
        # Non-canonical source encoding; keep the bytes so save() is exact.
        text._raw = raw
    return text, pos


def _read_array_payload(data: bytes, pos: int, cls, itemsize: int):
    (count,) = _S_INT.unpack_from(data, pos)
    pos += 4
    if count < 0:
        raise NBTError(f"negative array length {count} at offset {pos - 4}")
    end = pos + count * itemsize
    if end > len(data):
        raise NBTError(f"truncated array at offset {pos - 4}")
    values = cls()
    values.frombytes(data[pos:end])
    if _LITTLE_ENDIAN and itemsize > 1:
        values.byteswap()
    return values, end


def _read_payload(
    tag_id: int, data: bytes, pos: int, depth: int = 0
) -> tuple[Any, int]:
    """Read one tag payload, returning the value and the new offset."""
    if tag_id == TAG_BYTE:
        return TagByte(_S_BYTE.unpack_from(data, pos)[0]), pos + 1
    if tag_id == TAG_SHORT:
        return TagShort(_S_SHORT.unpack_from(data, pos)[0]), pos + 2
    if tag_id == TAG_INT:
        return TagInt(_S_INT.unpack_from(data, pos)[0]), pos + 4
    if tag_id == TAG_LONG:
        return TagLong(_S_LONG.unpack_from(data, pos)[0]), pos + 8

    if tag_id == TAG_FLOAT:
        raw = data[pos : pos + 4]
        value = TagFloat(_S_FLOAT.unpack(raw)[0])
        if _S_FLOAT.pack(value) != raw:
            value._raw = raw
        return value, pos + 4
    if tag_id == TAG_DOUBLE:
        raw = data[pos : pos + 8]
        double = TagDouble(_S_DOUBLE.unpack(raw)[0])
        if _S_DOUBLE.pack(double) != raw:
            double._raw = raw
        return double, pos + 8

    if tag_id == TAG_BYTE_ARRAY:
        return _read_array_payload(data, pos, TagByteArray, 1)
    if tag_id == TAG_STRING:
        return _read_string(data, pos)
    if tag_id == TAG_INT_ARRAY:
        return _read_array_payload(data, pos, TagIntArray, 4)
    if tag_id == TAG_LONG_ARRAY:
        return _read_array_payload(data, pos, TagLongArray, 8)

    if tag_id == TAG_LIST:
        if depth >= MAX_DEPTH:
            raise NBTError(_TOO_DEEP.format(pos=pos))
        element_type = data[pos]
        (count,) = _S_INT.unpack_from(data, pos + 1)
        pos += 5
        if count < 0:
            # Coercing this to an empty list would silently rewrite the file on
            # save; report it instead, and let the caller keep the raw bytes.
            raise NBTError(f"TAG_List with negative length {count} at offset {pos - 4}")
        if element_type == TAG_END and count:
            raise NBTError(f"TAG_List of TAG_End with {count} entries at {pos}")
        items = TagList(element_type=element_type)
        append = items.append
        for _ in range(count):
            value, pos = _read_payload(element_type, data, pos, depth + 1)
            append(value)
        return items, pos

    if tag_id == TAG_COMPOUND:
        if depth >= MAX_DEPTH:
            raise NBTError(_TOO_DEEP.format(pos=pos))
        compound = TagCompound()
        while True:
            child_id = data[pos]
            pos += 1
            if child_id == TAG_END:
                return compound, pos
            (name_len,) = _S_USHORT.unpack_from(data, pos)
            pos += 2
            name_bytes = data[pos : pos + name_len]
            pos += name_len
            if name_bytes.isascii() and 0 not in name_bytes:
                name = name_bytes.decode("ascii")
            else:
                name = decode_modified_utf8(name_bytes)
            compound[name], pos = _read_payload(child_id, data, pos, depth + 1)

    raise NBTError(f"unknown tag type {tag_id} at offset {pos - 1}")


def decode(data: bytes) -> tuple[str, TagCompound]:
    """Decode uncompressed NBT bytes into ``(root_name, root_tag)``."""
    if not data:
        raise NBTError("empty NBT payload")
    root_id = data[0]
    if root_id != TAG_COMPOUND:
        raise NBTError(
            f"expected root {TAG_NAMES[TAG_COMPOUND]}, got "
            f"{TAG_NAMES.get(root_id, root_id)}"
        )
    (name_len,) = _S_USHORT.unpack_from(data, 1)
    name = decode_modified_utf8(data[3 : 3 + name_len])
    root, pos = _read_payload(TAG_COMPOUND, data, 3 + name_len)
    if pos != len(data):
        raise NBTError(f"{len(data) - pos} trailing bytes after root tag")
    return name, root


# --------------------------------------------------------------------------
# Binary encoding
# --------------------------------------------------------------------------


def _tag_id_of(value: Any) -> int:
    if isinstance(value, Tag):
        return value.ID
    raise NBTError(
        f"cannot serialize {type(value).__name__}; wrap it in a Tag subclass "
        f"(TagInt, TagString, TagCompound, ...)"
    )


def _write_string_bytes(out: bytearray, value: Any) -> None:
    raw = getattr(value, "_raw", None)
    if raw is None:
        raw = encode_modified_utf8(value)
    if len(raw) > 0xFFFF:
        raise NBTError(f"string of {len(raw)} bytes exceeds the 65535-byte limit")
    out += _S_USHORT.pack(len(raw))
    out += raw


def _write_payload(out: bytearray, tag_id: int, value: Any) -> None:
    if tag_id == TAG_BYTE:
        out += _S_BYTE.pack(value)
    elif tag_id == TAG_SHORT:
        out += _S_SHORT.pack(value)
    elif tag_id == TAG_INT:
        out += _S_INT.pack(value)
    elif tag_id == TAG_LONG:
        out += _S_LONG.pack(value)
    elif tag_id == TAG_FLOAT:
        raw = getattr(value, "_raw", None)
        out += raw if raw is not None else _S_FLOAT.pack(value)
    elif tag_id == TAG_DOUBLE:
        raw = getattr(value, "_raw", None)
        out += raw if raw is not None else _S_DOUBLE.pack(value)
    elif tag_id == TAG_STRING:
        _write_string_bytes(out, value)
    elif tag_id in (TAG_BYTE_ARRAY, TAG_INT_ARRAY, TAG_LONG_ARRAY):
        out += _S_INT.pack(len(value))
        if _LITTLE_ENDIAN and value.itemsize > 1:
            swapped = array(value.typecode, value)
            swapped.byteswap()
            out += swapped.tobytes()
        else:
            out += value.tobytes()
    elif tag_id == TAG_LIST:
        element_type = getattr(value, "element_type", TAG_END)
        if value:
            element_type = _tag_id_of(value[0])
        out.append(element_type)
        out += _S_INT.pack(len(value))
        for position, item in enumerate(value):
            # NBT lists are homogeneous. Writing a mismatched item under the
            # list's type would either fail obscurely or silently change the
            # item's type on the way back in, so reject it here.
            item_id = _tag_id_of(item)
            if item_id != element_type:
                raise NBTError(
                    f"TAG_List is declared as {TAG_NAMES[element_type]} but item "
                    f"{position} is {TAG_NAMES[item_id]}; NBT lists cannot mix types"
                )
            _write_payload(out, element_type, item)
    elif tag_id == TAG_COMPOUND:
        for name, item in value.items():
            child_id = _tag_id_of(item)
            out.append(child_id)
            _write_string_bytes(out, name)
            try:
                _write_payload(out, child_id, item)
            except struct.error as exc:
                # Out-of-range scalar. Name the culprit; try/except costs
                # nothing on the happy path in CPython 3.11+.
                raise NBTError(
                    f"{name!r}: {item!r} does not fit in {TAG_NAMES[child_id]} ({exc})"
                ) from None
        out.append(TAG_END)
    else:
        raise NBTError(f"cannot serialize tag type {tag_id}")


def encode(root: TagCompound, root_name: str = "") -> bytes:
    """Encode a root compound tag into uncompressed NBT bytes."""
    out = bytearray()
    out.append(TAG_COMPOUND)
    _write_string_bytes(out, root_name)
    try:
        _write_payload(out, TAG_COMPOUND, root)
    except RecursionError:
        raise NBTError(
            f"tag nesting is deeper than Python's recursion limit "
            f"({sys.getrecursionlimit()}); the tree is probably cyclic"
        ) from None
    return bytes(out)


# --------------------------------------------------------------------------
# Compression
# --------------------------------------------------------------------------

COMPRESSION_GZIP = 1
COMPRESSION_ZLIB = 2
COMPRESSION_NONE = 3
COMPRESSION_LZ4 = 4
COMPRESSION_CUSTOM = 127

COMPRESSION_NAMES = {
    COMPRESSION_GZIP: "gzip",
    COMPRESSION_ZLIB: "zlib",
    COMPRESSION_NONE: "none",
    COMPRESSION_LZ4: "lz4",
    COMPRESSION_CUSTOM: "custom",
}

# Minecraft compresses chunks with zlib at its default level; matching that
# keeps rewritten chunks bit-identical to what the game would have produced.
ZLIB_LEVEL = 6


def _decompress(scheme: int, blob: bytes) -> bytes:
    if scheme == COMPRESSION_ZLIB:
        return zlib.decompress(blob)
    if scheme == COMPRESSION_GZIP:
        return gzip.decompress(blob)
    if scheme == COMPRESSION_NONE:
        return blob
    if scheme == COMPRESSION_LZ4:
        try:
            import lz4.block  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise NBTError(
                "chunk uses LZ4 compression; install the 'lz4' package to read it"
            ) from exc
        return lz4.block.decompress(blob)
    raise NBTError(f"unsupported compression scheme {scheme}")


def _compress(scheme: int, raw: bytes) -> bytes:
    if scheme == COMPRESSION_ZLIB:
        return zlib.compress(raw, ZLIB_LEVEL)
    if scheme == COMPRESSION_GZIP:
        # mtime=0 keeps the output deterministic.
        return gzip.compress(raw, mtime=0)
    if scheme == COMPRESSION_NONE:
        return raw
    if scheme == COMPRESSION_LZ4:
        try:
            import lz4.block  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise NBTError(
                "chunk uses LZ4 compression; install the 'lz4' package to write it"
            ) from exc
        return lz4.block.compress(raw)
    raise NBTError(f"unsupported compression scheme {scheme}")


def _digest(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=16).digest()


def _has_zlib_header(data: bytes) -> bool:
    """True if the bytes open with a valid zlib (RFC 1950) header."""
    if len(data) < 2:
        return False
    cmf, flg = data[0], data[1]
    return (cmf & 0x0F) == 8 and ((cmf << 8) | flg) % 31 == 0


# --------------------------------------------------------------------------
# Filesystem safety
# --------------------------------------------------------------------------

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None  # type: ignore[assignment]

SESSION_LOCK = "session.lock"


def _fsync_directory(directory: str) -> None:
    """Flush a directory entry, so a rename survives power loss."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover - not supported everywhere
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover
        pass
    finally:
        os.close(fd)


def atomic_write(path: str, data: bytes) -> None:
    """Write ``data`` to ``path`` without ever leaving it half-written.

    A region file is megabytes. A plain ``open(path, "wb")`` truncates first,
    so a crash, a full disk or an I/O error partway through would destroy the
    original with no copy anywhere. Writing a sibling temporary file and
    renaming makes the swap atomic, because rename within a filesystem is.
    Public because every writer in this package -- and anything built on it
    -- should write files this way rather than reinventing it.
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".nbt-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.exists(path):
            # mkstemp makes the file 0600; keep the original's permissions.
            os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:  # pragma: no cover
            pass
        raise
    _fsync_directory(directory)


def find_world_root(path: str | os.PathLike) -> str | None:
    """Walk up from a file to the save directory holding ``session.lock``."""
    directory = os.path.dirname(os.path.abspath(os.fspath(path)))
    while True:
        if os.path.exists(os.path.join(directory, SESSION_LOCK)):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def world_is_open(world_root: str | os.PathLike) -> bool:
    """True if something holds the world's ``session.lock``.

    Minecraft keeps a Java ``FileLock`` on that file for as long as the world
    is loaded, and writing underneath a running game either gets clobbered
    when it flushes its own chunk cache or corrupts the region outright.

    Java implements ``FileLock`` with POSIX record locks, so ``fcntl.lockf``
    is what can observe it -- ``flock`` uses a separate lock space and would
    report the world as free.
    """
    if fcntl is None:  # pragma: no cover - not POSIX
        return False
    lock = os.path.join(os.fspath(world_root), SESSION_LOCK)
    try:
        fd = os.open(lock, os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.lockf(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


# --------------------------------------------------------------------------
# Region container
# --------------------------------------------------------------------------

SECTOR = 4096
EXTERNAL_FLAG = 0x80


class Chunk:
    """One chunk inside a region file.

    The chunk's NBT is decoded lazily: a freshly loaded region holds only the
    compressed bytes, and the tree is built the first time :attr:`data` is
    read. Decoding a chunk costs roughly 30x its compressed size in memory, so
    a whole-world pass should call :meth:`release` when finished with each one.

    Attributes:
        local_x, local_z: Position within the region, 0-31.
        data: Root :class:`TagCompound`, decoded on first access, or ``None``
            if the chunk cannot be parsed (its bytes are still preserved).
        data_name: Name of the root tag, almost always ``""``.
        timestamp: Last-modified time, seconds since the Unix epoch.
        compression: One of the ``COMPRESSION_*`` constants.
        external: True when the payload lives in a sidecar ``.mcc`` file
            because it was too large for the 255-sector record limit.
        parse_error: The reason decoding failed, else ``None``.
    """

    __slots__ = (
        "local_x",
        "local_z",
        "timestamp",
        "compression",
        "external",
        "_data",
        "_data_name",
        "_decoded",
        "_parse_error",
        "_blob",
        "_digest",
        "dirty",
        "_sector_offset",
        "_sector_count",
        "_padding",
    )

    def __init__(
        self,
        local_x: int,
        local_z: int,
        data: TagCompound | None = None,
        data_name: str = "",
        timestamp: int = 0,
        compression: int = COMPRESSION_ZLIB,
        external: bool = False,
    ):
        self.local_x = local_x
        self.local_z = local_z
        self.timestamp = timestamp
        self.compression = compression
        self.external = external

        self._data = data
        self._data_name = data_name
        # True once decoding has been attempted (or the data was supplied
        # directly), which distinguishes "not yet decoded" from "decoded to
        # nothing because the chunk is corrupt".
        self._decoded = data is not None
        self._parse_error: str | None = None

        # Provenance used to reproduce the original bytes when nothing changed.
        self._blob: bytes | None = None
        self._digest: bytes | None = None
        # True once payload() has had to re-encode this chunk, i.e. its bytes
        # changed at some point since load. Only used to decide which chunks
        # `touch_modified` re-stamps; whether the *file* needs writing is
        # settled by comparing hashes, not by this flag.
        self.dirty = data is not None
        self._sector_offset = 0
        self._sector_count = 0
        self._padding = b""

    # -- lazy decoding -----------------------------------------------------

    def _decode(self) -> None:
        """Decode the compressed payload, recording any failure."""
        self._decoded = True
        if self._blob is None:
            return
        try:
            payload = _decompress(self.compression, self._blob)
            self._data_name, self._data = decode(payload)
            self._digest = _digest(payload)
        except Exception as exc:  # keep the bytes, report the problem
            self._parse_error = f"{type(exc).__name__}: {exc}"

    @property
    def data(self) -> TagCompound | None:
        if not self._decoded:
            self._decode()
        return self._data

    @data.setter
    def data(self, value: TagCompound | None) -> None:
        self._data = value
        self._decoded = True
        self._parse_error = None

    @property
    def data_name(self) -> str:
        if not self._decoded:
            self._decode()
        return self._data_name

    @data_name.setter
    def data_name(self, value: str) -> None:
        if not self._decoded:
            self._decode()
        self._data_name = value

    @property
    def parse_error(self) -> str | None:
        if not self._decoded:
            self._decode()
        return self._parse_error

    @property
    def loaded(self) -> bool:
        """True if this chunk's NBT is currently decoded in memory."""
        return self._decoded and self._data is not None

    def release(self) -> None:
        """Drop the decoded tree, keeping the compressed bytes.

        Any edits are flushed into those bytes first, so this is safe to call
        on a modified chunk; the tree is simply rebuilt on next access.
        """
        if self._decoded and self._data is not None:
            self.payload()  # flush into _blob/_digest, recording any change
        self._data = None
        self._decoded = False
        self._parse_error = None

    @property
    def index(self) -> int:
        """Offset of this chunk's entry in the region header tables."""
        return self.local_x + self.local_z * 32

    @property
    def compressed_size(self) -> int:
        """Bytes of compressed payload currently held for this chunk.

        Zero when the chunk holds only a decoded tree (freshly built in
        memory, never flushed). This is the honest measure of what a loaded
        region costs before decoding; the decoded tree is roughly 30x it.
        """
        return len(self._blob) if self._blob is not None else 0

    def payload(self) -> bytes:
        """Return the compressed payload, reusing the original bytes if the
        chunk's NBT is byte-for-byte what was loaded.

        Deliberately reads ``_data`` rather than :attr:`data`, so saving a
        region nobody has touched does no decoding at all.
        """
        if not self._decoded or self._data is None:
            if self._blob is None:
                raise NBTError(
                    f"chunk ({self.local_x}, {self.local_z}) has neither decoded "
                    f"data nor original bytes"
                )
            return self._blob
        raw = encode(self._data, self._data_name)
        digest = _digest(raw)
        if self._blob is not None and self._digest == digest:
            return self._blob
        # The tree no longer matches the bytes we were holding. Re-compress and
        # keep the result, so this is the chunk's current content from now on;
        # `dirty` is what remembers that disk is still behind.
        self._blob = _compress(self.compression, raw)
        self._digest = digest
        self.dirty = True
        return self._blob

    def __repr__(self) -> str:
        # Must not decode: repr() is used in debuggers and error messages.
        if not self._decoded:
            size = len(self._blob) if self._blob is not None else 0
            state = f"not decoded, {size:,} compressed bytes"
        elif self._data is None:
            state = f"unreadable ({self._parse_error})" if self._parse_error else "empty"
        else:
            state = f"{len(self._data)} root entries"
        return f"<Chunk ({self.local_x}, {self.local_z}) {state}>"


_REGION_NAME = re.compile(r"^r\.(-?\d+)\.(-?\d+)\.mc[ar]$", re.IGNORECASE)


class NBT:
    """A loaded Minecraft NBT file.

    Use :meth:`load` to read a file and :meth:`save` to write it back. The
    ``kind`` attribute says which of the two layouts was found:

    ``"nbt"``
        A standalone NBT file. The tree is in :attr:`root`, its name in
        :attr:`root_name`, and the container's compression in
        :attr:`compression`.

    ``"region"``
        An Anvil region container. The chunks are in :attr:`chunks`, keyed by
        their ``(x, z)`` position within the region (both 0-31).
    """

    def __init__(self) -> None:
        self.kind: str = "nbt"
        self.path: str | None = None

        # kind == "nbt"
        self.root: TagCompound | None = None
        self.root_name: str = ""
        self.compression: int = COMPRESSION_NONE

        # kind == "region"
        self.chunks: dict[tuple[int, int], Chunk] = {}
        self.region_x: int | None = None
        self.region_z: int | None = None

        # Provenance for byte-exact rewriting.
        self._raw: bytes | None = None
        self._digest: bytes | None = None
        self._timestamps = [0] * 1024
        self._total_sectors = 0
        self._gap_sectors: dict[int, bytes] = {}
        self._loaded_empty = False
        # Snapshot of the region's shape as loaded, so chunks appearing,
        # disappearing or being re-stamped counts as a change even though no
        # individual chunk's payload differs. None means "built in memory",
        # which is modified by definition.
        # Hash of this file exactly as it came off disk. The yardstick for
        # is_modified(); None means it never came from disk.
        self._load_digest: bytes | None = None
        # Sidecar paths present at load, so ones that stop being needed can be
        # cleaned up rather than lingering as orphans.
        self._loaded_sidecars: set[str] = set()

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(cls, path: str | os.PathLike, *, eager: bool = False) -> "NBT":
        """Load an NBT or region file.

        Args:
            path: File to read.
            eager: For region files, decode every chunk during the load
                instead of on first access. Costs time and a lot of memory,
                but surfaces any corrupt chunk immediately rather than when
                something reaches for it. Saving is byte-exact either way.
        """
        path = os.fspath(path)
        with open(path, "rb") as handle:
            data = handle.read()

        self = cls()
        self.path = path
        self._load_digest = _digest(data)
        if cls._looks_like_region(path, data):
            self._load_region(data)
            if eager:
                for chunk in self.chunks.values():
                    chunk._decode()
        else:
            self._load_standalone(data)
        return self

    @staticmethod
    def _looks_like_region(path: str, data: bytes) -> bool:
        if _REGION_NAME.match(os.path.basename(path)):
            return True
        # Anything carrying a recognizable NBT container header is standalone.
        if data[:2] == b"\x1f\x8b" or data[:1] == b"\x0a" or _has_zlib_header(data):
            return False
        # Otherwise fall back on shape: a region is a whole number of 4 KiB
        # sectors, at least the two header sectors long.
        return len(data) >= 2 * SECTOR and len(data) % SECTOR == 0

    def _load_standalone(self, data: bytes) -> None:
        self.kind = "nbt"
        self._raw = data
        if data[:2] == b"\x1f\x8b":
            self.compression = COMPRESSION_GZIP
            payload = gzip.decompress(data)
        elif data[:1] == b"\x0a":
            self.compression = COMPRESSION_NONE
            payload = data
        else:
            try:
                payload = zlib.decompress(data)
            except zlib.error as exc:
                raise NBTError(f"unrecognized NBT container: {exc}") from exc
            self.compression = COMPRESSION_ZLIB
        self.root_name, self.root = decode(payload)
        self._digest = _digest(payload)

    def _load_region(self, data: bytes) -> None:
        self.kind = "region"
        match = _REGION_NAME.match(os.path.basename(self.path or ""))
        if match:
            self.region_x, self.region_z = int(match.group(1)), int(match.group(2))

        if not data:
            # Minecraft leaves a zero-length file where a region exists but
            # holds nothing (common under poi/). It is a valid empty region,
            # and stays zero-length on the way out unless chunks are added.
            self._loaded_empty = True
            return
        if len(data) < 2 * SECTOR:
            raise NBTError(f"region file is only {len(data)} bytes; need at least 8192")

        self._total_sectors = len(data) // SECTOR
        locations = data[:SECTOR]
        timestamps = data[SECTOR : 2 * SECTOR]
        self._timestamps = [value for (value,) in _S_UINT.iter_unpack(timestamps)]

        occupied = bytearray(self._total_sectors)
        occupied[0] = occupied[1] = 1

        for index in range(1024):
            offset = int.from_bytes(locations[index * 4 : index * 4 + 3], "big")
            count = locations[index * 4 + 3]
            if offset == 0 and count == 0:
                continue
            if offset < 2 or offset + count > self._total_sectors:
                # A region is always a whole number of sectors. A ragged tail
                # means the file was caught mid-write -- almost always because
                # the world is open in Minecraft -- rather than corrupted.
                trailing = len(data) % SECTOR
                hint = ""
                if trailing:
                    hint = (
                        f". The file ends {trailing} bytes past a sector boundary, "
                        f"so it was probably read while being written; check "
                        f"world_is_open() and close the world first"
                    )
                raise NBTError(
                    f"chunk {index} claims sectors {offset}..{offset + count}, "
                    f"outside a {self._total_sectors}-sector file{hint}"
                )
            for sector in range(offset, offset + count):
                occupied[sector] = 1

            chunk = self._read_chunk_record(data, index, offset, count)
            chunk.timestamp = self._timestamps[index]
            self.chunks[(chunk.local_x, chunk.local_z)] = chunk

        # Unallocated sectors are usually zero-filled, but hold on to any that
        # are not so a rewrite reproduces them.
        for sector, used in enumerate(occupied):
            if not used:
                blob = data[sector * SECTOR : (sector + 1) * SECTOR]
                if blob.strip(b"\x00"):
                    self._gap_sectors[sector] = blob

    def _read_chunk_record(
        self, data: bytes, index: int, offset: int, count: int
    ) -> Chunk:
        start = offset * SECTOR
        (length,) = _S_UINT.unpack_from(data, start)
        scheme = data[start + 4]
        external = bool(scheme & EXTERNAL_FLAG)
        scheme &= ~EXTERNAL_FLAG

        chunk = Chunk(index % 32, index // 32, compression=scheme, external=external)
        chunk._sector_offset = offset
        chunk._sector_count = count

        if length < 1 or start + 4 + length > (offset + count) * SECTOR:
            raise NBTError(
                f"chunk {index} declares a {length}-byte record that overruns its "
                f"{count} allocated sectors"
            )

        if external:
            # Payload lives in a sidecar file; the record holds only the flag.
            side = self._external_path(chunk)
            try:
                with open(side, "rb") as handle:
                    chunk._blob = handle.read()
            except FileNotFoundError as exc:
                raise NBTError(f"missing external chunk file {side}") from exc
            self._loaded_sidecars.add(side)
        else:
            chunk._blob = data[start + 5 : start + 4 + length]

        # Trailing bytes in the final sector. Minecraft does not clear these
        # when a chunk shrinks, so they routinely hold fragments of older data.
        chunk._padding = data[start + 4 + length : (offset + count) * SECTOR]
        return chunk

    def _external_path(self, chunk: Chunk, directory: str | None = None) -> str:
        """Path of the ``.mcc`` sidecar holding an oversized chunk's payload.

        The name uses absolute chunk coordinates, so it depends on which region
        this is -- which is only knowable from the file name.
        """
        if directory is None:
            directory = os.path.dirname(self.path or "")
        if self.region_x is None or self.region_z is None:
            raise NBTError(
                "cannot locate .mcc sidecar files: the region's coordinates are "
                "unknown because the file is not named r.<x>.<z>.mca"
            )
        return os.path.join(
            directory,
            f"c.{self.region_x * 32 + chunk.local_x}.{self.region_z * 32 + chunk.local_z}.mcc",
        )

    # -- saving ------------------------------------------------------------

    def save(
        self,
        path: str | os.PathLike | None = None,
        *,
        force: bool = False,
        touch_modified: bool = False,
        skip_unchanged: bool = False,
        before_write: Callable[[str], None] | None = None,
    ) -> bool:
        """Write the file back out, atomically.

        Defaults to the path it was loaded from. Data that has not been
        modified is written using its original compressed bytes, so an
        untouched load/save cycle reproduces the input exactly.

        The write goes to a temporary file that is then renamed over the
        target, so an interrupted save cannot leave a truncated region behind.

        Args:
            path: Where to write. Defaults to the loaded path.
            force: Write even if the destination world is currently open in
                Minecraft. The default refuses, because the running game holds
                its own chunk cache and will either overwrite these changes or
                interleave with them and corrupt the region.
            touch_modified: Set the timestamp of every chunk whose bytes
                actually changed to now, as the game does. Off by default,
                since it would otherwise break byte-exact round-tripping.
            skip_unchanged: Do nothing if the serialized bytes match what was
                loaded. Only applies when writing back over the same path --
                saving somewhere new always writes.
            before_write: Called with the absolute path of every file this
                save is about to replace, create or remove -- each sidecar,
                the file itself, then each sidecar no longer referenced --
                once the save is sure to write and before any of them is
                touched. An exception from it stops the save with nothing
                of this file written, which is what a backup taken of each
                file before it changes is built on.

        Returns:
            True if a file was written, False if the write was skipped.

        Chunks too large for the region format are written to ``.mcc`` sidecar
        files alongside, matching Minecraft's own behavior.
        """
        target = os.fspath(path) if path is not None else self.path
        if target is None:
            raise NBTError("no path given and this NBT was not loaded from a file")
        writing_in_place = os.path.abspath(target) == os.path.abspath(self.path or "")

        if not force:
            world = find_world_root(target)
            if world is not None and world_is_open(world):
                raise NBTError(
                    f"the world at {world!r} is open in Minecraft (its "
                    f"{SESSION_LOCK} is held). Writing to it now would be lost "
                    f"when the game flushes, or corrupt the region. Close the "
                    f"world first, or pass force=True if you are sure."
                )

        directory = os.path.dirname(os.path.abspath(target))
        if self.kind == "region":
            data, sidecars = self._build_region(
                directory, touch_modified=touch_modified
            )
        else:
            data, sidecars = self._build_standalone(), {}

        digest = _digest(data)
        if (
            skip_unchanged
            and writing_in_place
            and self._load_digest is not None
            and digest == self._load_digest
        ):
            return False

        stale = self._stale_sidecars(directory, set(sidecars))
        if before_write is not None:
            for touched in [*sidecars, target, *stale]:
                before_write(os.path.abspath(touched))

        # Sidecars first: the region file is what references them, so it must
        # not become visible before the data it points at exists.
        for sidecar_path, blob in sidecars.items():
            atomic_write(sidecar_path, blob)
        atomic_write(target, data)

        # Finally drop sidecars that are no longer referenced. Doing this last
        # means an interrupted save leaves harmless orphans rather than a
        # region pointing at a file that is already gone.
        self._remove_stale_sidecars(directory, set(sidecars))
        self.path = target
        self._load_digest = digest  # disk now matches this object
        return True

    def _remove_stale_sidecars(self, directory: str, written: set[str]) -> None:
        """Delete ``.mcc`` files this region no longer refers to.

        Only ever touches sidecars that were present when this instance was
        loaded, and only in the directory it was loaded from, so saving a copy
        elsewhere never deletes anything.
        """
        if not self._sidecars_here(directory):
            return
        for sidecar_path in self._stale_sidecars(directory, written):
            try:
                os.unlink(sidecar_path)
            except OSError:  # pragma: no cover - already gone is fine
                pass
        self._loaded_sidecars &= written

    def _sidecars_here(self, directory: str) -> bool:
        """Whether this instance loaded sidecars from ``directory``, the
        only ones a save there may remove."""
        return bool(self._loaded_sidecars) and self.path is not None \
            and os.path.dirname(os.path.abspath(self.path)) == directory

    def _stale_sidecars(self, directory: str, written: set[str]) -> list[str]:
        """The sidecars a save into ``directory`` that writes ``written``
        removes, in a fixed order."""
        if not self._sidecars_here(directory):
            return []
        return sorted(self._loaded_sidecars - written)

    def to_bytes(self) -> bytes:
        """Serialize the file to bytes, without touching the filesystem.

        For a region whose chunks spill into ``.mcc`` sidecars this returns
        only the region file itself; use :meth:`save` to write the sidecars
        alongside it.
        """
        if self.kind == "region":
            return self._build_region()[0]
        return self._build_standalone()

    def _build_standalone(self) -> bytes:
        if self.root is None:
            raise NBTError("nothing to save: root tag is None")
        payload = encode(self.root, self.root_name)
        if self._raw is not None and self._digest == _digest(payload):
            return self._raw
        return _compress(self.compression, payload)

    def _build_region(
        self, sidecar_dir: str | None = None, *, touch_modified: bool = False
    ) -> tuple[bytes, dict[str, bytes]]:
        """Build the region file, returning it plus any ``.mcc`` payloads."""
        if not self.chunks and self._loaded_empty:
            return b"", {}

        now = int(time.time())
        records: list[tuple[Chunk, bytes, bytes, int]] = []
        for chunk in self.chunks.values():
            blob = chunk.payload()
            if touch_modified and chunk.dirty:
                chunk.timestamp = now

            # Always size the in-region record first, so a chunk that has
            # shrunk back under the limit stops using a sidecar.
            record = _S_UINT.pack(len(blob) + 1) + bytes([chunk.compression]) + blob
            needed = -(-len(record) // SECTOR)
            if needed > 255:
                # The sector-count field is one byte, so a payload this large
                # cannot live in the region file; Minecraft moves it to a .mcc
                # sidecar and leaves a flagged stub behind.
                if sidecar_dir is None:
                    raise NBTError(
                        f"chunk ({chunk.local_x}, {chunk.local_z}) is too large for "
                        f"the region format and must be written to a .mcc sidecar; "
                        f"use save() rather than to_bytes()"
                    )
                chunk.external = True
                record = _S_UINT.pack(1) + bytes([chunk.compression | EXTERNAL_FLAG])
                needed = 1
            else:
                chunk.external = False
            records.append((chunk, record, blob, needed))

        # Keeping every chunk at its original sector preserves the file's exact
        # layout; that is only possible while each record still fits where it
        # was. Otherwise repack in the original order, which keeps the result
        # deterministic and close to the input.
        in_place = all(
            chunk._sector_offset >= 2 and needed <= chunk._sector_count
            for chunk, _, _, needed in records
        )
        if in_place:
            total_sectors = self._total_sectors
            placements = [
                (chunk, record, chunk._sector_offset, chunk._sector_count)
                for chunk, record, _, _ in records
            ]
        else:
            cursor = 2
            placements = []
            for chunk, record, _, needed in sorted(
                records, key=lambda item: (item[0]._sector_offset, item[0].index)
            ):
                placements.append((chunk, record, cursor, needed))
                cursor += needed
            total_sectors = cursor

        # The two header sectors always exist, and the buffer has to cover every
        # placement: writing past the end of a bytearray extends it silently
        # rather than failing, which would quietly corrupt the layout.
        total_sectors = max(
            total_sectors, 2, *(offset + count for _, _, offset, count in placements)
        )

        out = bytearray(total_sectors * SECTOR)
        for sector, blob in self._gap_sectors.items():
            if sector < total_sectors:
                out[sector * SECTOR : (sector + 1) * SECTOR] = blob

        timestamps = list(self._timestamps)
        for chunk, record, offset, count in placements:
            index = chunk.index
            out[index * 4 : index * 4 + 3] = offset.to_bytes(3, "big")
            out[index * 4 + 3] = count
            timestamps[index] = chunk.timestamp & 0xFFFFFFFF

            start = offset * SECTOR
            out[start : start + len(record)] = record
            # Restore the original slack bytes when the record is unchanged in
            # length; otherwise leave the zeros already in place.
            slack = (offset + count) * SECTOR - (start + len(record))
            if slack and len(chunk._padding) == slack:
                out[start + len(record) : (offset + count) * SECTOR] = chunk._padding

        for index, value in enumerate(timestamps):
            out[SECTOR + index * 4 : SECTOR + index * 4 + 4] = _S_UINT.pack(
                value & 0xFFFFFFFF
            )

        sidecars = {}
        if sidecar_dir is not None:
            for chunk, _, blob, _ in records:
                if chunk.external:
                    sidecars[self._external_path(chunk, sidecar_dir)] = blob

        return bytes(out), sidecars

    # -- convenience -------------------------------------------------------

    # -- change tracking ---------------------------------------------------

    @property
    def from_disk(self) -> bool:
        """Whether this file was loaded from disk, as opposed to built in
        memory. A built-in-memory file is modified by definition, and callers
        deciding whether an empty container is worth writing at all (an
        entity region that never received an entity, say) key off this."""
        return self._load_digest is not None

    def is_modified(self) -> bool:
        """Whether this file differs from the bytes it was loaded from.

        Serializes and compares hashes. That is exact by construction: it does
        not matter how a change was made -- through the block API, by reaching
        into ``chunk.data`` directly, by adding or deleting a chunk, or by
        re-stamping a timestamp. A scheme built on flags at the editing entry
        points would miss the ones that went around them, and the failure mode
        is a save that silently drops work.

        Costs roughly a millisecond per megabyte, and nothing at all for a
        region whose chunks were never decoded.
        """
        if self._load_digest is None:
            return True  # built in memory, never written anywhere
        return _digest(self.to_bytes()) != self._load_digest

    # -- convenience -------------------------------------------------------

    def chunk(self, x: int, z: int) -> Chunk | None:
        """Return the chunk at region-local ``(x, z)``, or ``None``."""
        return self.chunks.get((x & 31, z & 31))

    def __iter__(self) -> Iterator[Chunk]:
        """Iterate this region's chunks (empty for a standalone NBT file)."""
        return iter(self.chunks.values())

    def __repr__(self) -> str:
        if self.kind == "region":
            where = ""
            if self.region_x is not None:
                where = f" r.{self.region_x}.{self.region_z}"
            return (
                f"<NBT region{where} {len(self.chunks)} chunks, "
                f"{self._total_sectors} sectors>"
            )
        return (
            f"<NBT {COMPRESSION_NAMES.get(self.compression, '?')} "
            f"root={self.root_name!r} {len(self.root or ())} entries>"
        )


# --------------------------------------------------------------------------
# Human-readable dump
# --------------------------------------------------------------------------


def dumps(tag: Any, name: str = "", indent: int = 0, max_items: int = 8) -> str:
    """Render a tag tree as indented text, abbreviating long sequences."""
    pad = "  " * indent
    label = f"{name}: " if name else ""

    if isinstance(tag, TagCompound):
        lines = [f"{pad}{label}TAG_Compound ({len(tag)} entries)"]
        for key, value in tag.items():
            lines.append(dumps(value, key, indent + 1, max_items))
        return "\n".join(lines)

    if isinstance(tag, TagList):
        kind = TAG_NAMES.get(tag.element_type, "?")
        lines = [f"{pad}{label}TAG_List of {kind} ({len(tag)} entries)"]
        for item in tag[:max_items]:
            lines.append(dumps(item, "", indent + 1, max_items))
        if len(tag) > max_items:
            lines.append(f"{pad}  ... {len(tag) - max_items} more")
        return "\n".join(lines)

    if isinstance(tag, (TagByteArray, TagIntArray, TagLongArray)):
        preview = ", ".join(str(v) for v in tag[:max_items])
        if len(tag) > max_items:
            preview += ", ..."
        return f"{pad}{label}{tag.tag_name} ({len(tag)}) [{preview}]"

    if isinstance(tag, TagString):
        return f"{pad}{label}TAG_String {str(tag)!r}"

    if isinstance(tag, Tag):
        return f"{pad}{label}{tag.tag_name} {tag}"

    return f"{pad}{label}{tag!r}"


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m heaton_voxel nbt",
        description="Inspect and round-trip Minecraft NBT and region files."
    )
    parser.add_argument("path", help="a .mca/.mcr region file or a .dat/.nbt file")
    parser.add_argument(
        "--dump",
        metavar="X,Z",
        nargs="?",
        const="",
        help="print the tag tree (for a region, of the chunk at region-local X,Z)",
    )
    parser.add_argument(
        "--snbt",
        action="store_true",
        help="print the file's root as SNBT, the text the game's commands read "
             "(for a region, of the chunk --dump X,Z names)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="re-serialize and confirm the output is byte-identical to the input",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="decode every chunk and report any that fail (slower)",
    )
    args = parser.parse_args(argv)

    nbt = NBT.load(args.path)
    print(nbt)

    if nbt.kind == "region":
        schemes: dict[str | int, int] = {}
        for chunk in nbt:
            key = COMPRESSION_NAMES.get(chunk.compression, chunk.compression)
            schemes[key] = schemes.get(key, 0) + 1
        print(f"  compression   : {schemes}")
        # Loading is lazy, so only decode everything when asked to.
        if args.check:
            failed = [c for c in nbt if c.parse_error]
            print(f"  chunks decoded: {len(nbt.chunks) - len(failed)}/{len(nbt.chunks)}")
            for chunk in failed[:5]:
                print(f"    ({chunk.local_x}, {chunk.local_z}): {chunk.parse_error}")
            if failed:
                return 1

    if args.dump is not None or args.snbt:
        if nbt.kind == "region":
            if not args.dump:
                print("a region names its chunk: --dump X,Z (region-local)")
                return 2
            x, z = (int(part) for part in args.dump.split(","))
            found = nbt.chunk(x, z)
            if found is None or found.data is None:
                print(f"no readable chunk at ({x}, {z})")
                return 1
            tag, name = found.data, found.data_name
        elif nbt.root is None:
            print("the file holds no compound to dump")
            return 1
        else:
            tag, name = nbt.root, nbt.root_name
        if args.snbt:
            from .snbt import to_snbt

            print(to_snbt(tag))
        else:
            print(dumps(tag, name or "(root)"))

    if args.verify:
        with open(args.path, "rb") as handle:
            original = handle.read()
        rebuilt = nbt.to_bytes()
        if rebuilt == original:
            print(f"  round trip    : byte-identical ({len(rebuilt)} bytes)")
        else:
            print(f"  round trip    : DIFFERS ({len(original)} -> {len(rebuilt)} bytes)")
            for i in range(min(len(original), len(rebuilt))):
                if original[i] != rebuilt[i]:
                    print(f"    first difference at byte {i}")
                    break
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point (``heaton-voxel-nbt``)."""
    return _main(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
