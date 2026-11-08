"""Generate the language-neutral conformance corpus in ``conformance/``.

The corpus is the portable form of this library's test knowledge: input
vectors and expected outputs that any implementation -- the C# port in
particular -- can verify against without running Python. Everything here is
produced *by* the Python implementation, which is the reference: it has been
validated byte-for-byte against real game output (see PORTING.md).

Run from the repository root::

    python fixtures/make_conformance.py

``tests/test_conformance.py`` regenerates the corpus in memory on every test
run and fails if the checked-in files disagree, so the corpus cannot drift
from the implementation silently; a deliberate behavior change means
rerunning this script and committing the diff.

Encoding conventions (also stated in the manifest):

* binary data is lowercase hex;
* 64-bit integers are decimal **strings** (JSON numbers lose precision);
* floats carry their IEEE-754 bits as hex in ``"bits"`` -- the number in
  ``"v"`` is informational only;
* strings that are not printable ASCII appear as ``{"cp": [codepoints]}``,
  because lone surrogates and control characters do not survive every JSON
  parser;
* digests are SHA-256, lowercase hex. Digests of NBT are always of the
  **uncompressed** encoding: compressed bytes are not portable across zlib
  implementations (see PORTING.md).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import itertools
import json
import math
import os
import random
import shutil
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import heaton_voxel.block_defaults as block_defaults
import heaton_voxel.connect as connect
import heaton_voxel.connect_faces as connect_faces
import heaton_voxel.nbt as nbt
import heaton_voxel.place as place
import heaton_voxel.schem as schem_module
from heaton_voxel.blocks import (
    BLOCKS_PER_SECTION,
    BlockState,
    Blocks,
    pack_indices,
    unpack_nibbles,
)
from heaton_voxel.connect import _derived_state
from heaton_voxel.nbt import (
    NBT,
    TagByte,
    TagByteArray,
    TagCompound,
    TagDouble,
    TagFloat,
    TagInt,
    TagIntArray,
    TagList,
    TagLong,
    TagLongArray,
    TagShort,
    TagString,
    decode,
    encode,
)
from heaton_voxel.schem import Schematic, _map_state, _properties_rotated_cw
from heaton_voxel.snbt import parse_snbt, to_snbt

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(_HERE, "conformance")

CORPUS_VERSION = 1


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump_json(payload) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=1, ensure_ascii=True)
            + "\n").encode("ascii")


# --------------------------------------------------------------------------
# Tag tree -> portable JSON
# --------------------------------------------------------------------------


def _json_string(text: str):
    if text.isascii() and all(32 <= ord(c) < 127 for c in text):
        return {"t": "string", "v": text}
    return {"t": "string", "cp": [ord(c) for c in text]}


def tag_to_json(tag):
    if isinstance(tag, TagCompound):
        return {"t": "compound", "v": {k: tag_to_json(v) for k, v in tag.items()}}
    if isinstance(tag, TagList):
        return {
            "t": "list",
            "e": nbt.TAG_NAMES[tag.element_type],
            "v": [tag_to_json(item) for item in tag],
        }
    if isinstance(tag, TagByteArray):
        return {"t": "byte_array", "v": [int(v) for v in tag]}
    if isinstance(tag, TagIntArray):
        return {"t": "int_array", "v": [int(v) for v in tag]}
    if isinstance(tag, TagLongArray):
        return {"t": "long_array", "v": [str(int(v)) for v in tag]}
    if isinstance(tag, TagString):
        return _json_string(str(tag))
    if isinstance(tag, TagByte):
        return {"t": "byte", "v": int(tag)}
    if isinstance(tag, TagShort):
        return {"t": "short", "v": int(tag)}
    if isinstance(tag, TagInt):
        return {"t": "int", "v": int(tag)}
    if isinstance(tag, TagLong):
        return {"t": "long", "v": str(int(tag))}
    if isinstance(tag, TagFloat):
        raw = getattr(tag, "_raw", None) or struct.pack(">f", tag)
        return {"t": "float", "bits": raw.hex(),
                "v": None if math.isnan(tag) else float(tag)}
    if isinstance(tag, TagDouble):
        raw = getattr(tag, "_raw", None) or struct.pack(">d", tag)
        return {"t": "double", "bits": raw.hex(),
                "v": None if math.isnan(tag) else float(tag)}
    raise TypeError(f"cannot represent {type(tag).__name__}")


# --------------------------------------------------------------------------
# NBT cases
# --------------------------------------------------------------------------


def nbt_cases():
    cases = []

    def add(name, description, root, root_name=""):
        binary = encode(root, root_name)
        redecoded_name, redecoded = decode(binary)
        assert encode(redecoded, redecoded_name) == binary
        cases.append({
            "name": name,
            "description": description,
            "binary": binary.hex(),
            "root_name": root_name,
            "expected": tag_to_json(redecoded),
            "roundtrip_exact": True,
        })

    add("scalars", "every scalar type at ordinary and extreme values",
        TagCompound({
            "byte_min": TagByte(-128), "byte_max": TagByte(127),
            "short_min": TagShort(-32768), "short_max": TagShort(32767),
            "int_min": TagInt(-2147483648), "int_max": TagInt(2147483647),
            "long_min": TagLong(-9223372036854775808),
            "long_max": TagLong(9223372036854775807),
            "float": TagFloat(1.5), "double": TagDouble(-0.001),
            "text": TagString("plain ascii"),
        }))

    add("modified-utf8",
        "Java modified UTF-8: NUL encodes as C0 80, astral characters as "
        "CESU-8 surrogate pairs -- not standard UTF-8",
        TagCompound({
            "nul_inside": TagString("a\x00b"),
            "astral": TagString("clef: \U0001d11e"),
            "bmp": TagString("café 世界"),
        }),
        root_name="namedé")

    add("empty-lists",
        "an empty list's declared element type is data and must survive",
        TagCompound({
            "of_end": TagList(element_type=nbt.TAG_END),
            "of_int": TagList(element_type=nbt.TAG_INT),
            "of_compound": TagList(element_type=nbt.TAG_COMPOUND),
        }))

    add("nesting", "lists of lists, compounds in lists",
        TagCompound({
            "grid": TagList([
                TagList([TagInt(1), TagInt(2)], element_type=nbt.TAG_INT),
                TagList([TagInt(3)], element_type=nbt.TAG_INT),
            ], element_type=nbt.TAG_LIST),
            "rows": TagList([
                TagCompound({"a": TagByte(1)}),
                TagCompound({}),
            ], element_type=nbt.TAG_COMPOUND),
        }))

    add("arrays-signed",
        "array payloads are signed; a negative long's high bits are real "
        "data, and reading fields across words must mask, not sign-extend",
        TagCompound({
            "bytes": TagByteArray([-128, -1, 0, 1, 127]),
            "ints": TagIntArray([-2147483648, -1, 0, 2147483647]),
            "longs": TagLongArray([-9223372036854775808, -1, 0,
                                   4611686018427387904,
                                   9223372036854775807]),
        }))

    # Non-canonical modified UTF-8: an overlong two-byte 'A' (C1 81). It
    # must decode to "A" and re-encode to the ORIGINAL bytes, not the
    # canonical one-byte form.
    overlong = bytes([
        nbt.TAG_COMPOUND, 0, 0,
        nbt.TAG_STRING, 0, 1, ord("s"), 0, 2, 0xC1, 0x81,
        nbt.TAG_END,
    ])
    name, root = decode(overlong)
    assert encode(root, name) == overlong
    cases.append({
        "name": "non-canonical-string",
        "description": "an overlong encoding decodes normally but the "
                       "original bytes must be preserved for re-encoding",
        "binary": overlong.hex(),
        "root_name": "",
        "expected": tag_to_json(root),
        "roundtrip_exact": True,
    })

    # A float NaN with a payload: the bits do not survive a trip through
    # binary64, so the writer must re-emit the original four bytes.
    nan_payload = bytes([
        nbt.TAG_COMPOUND, 0, 0,
        nbt.TAG_FLOAT, 0, 1, ord("f"), 0x7F, 0xA0, 0x00, 0x01,
        nbt.TAG_DOUBLE, 0, 1, ord("d"),
        0x7F, 0xF0, 0x00, 0x00, 0x00, 0x00, 0xBE, 0xEF,
        nbt.TAG_END,
    ])
    name, root = decode(nan_payload)
    assert encode(root, name) == nan_payload
    cases.append({
        "name": "nan-payloads",
        "description": "NaN bit patterns are data; compare floats by bits, "
                       "never by value",
        "binary": nan_payload.hex(),
        "root_name": "",
        "expected": tag_to_json(root),
        "roundtrip_exact": True,
    })

    return {"version": CORPUS_VERSION, "cases": cases}


# --------------------------------------------------------------------------
# Packing, varints, nibbles
# --------------------------------------------------------------------------


def packing_cases():
    cases = []
    for bits in (1, 2, 3, 4, 5, 6, 7, 9, 12, 16):
        rng = random.Random(bits * 7 + 1)
        top = (1 << bits) - 1
        values = [rng.randrange(top + 1) for _ in range(100)]
        packed = pack_indices(values, len(values), bits)
        cases.append({
            "bits": bits,
            "count": len(values),
            "values": values,
            "longs": [str(int(word)) for word in packed],
        })
    # every entry at maximum, so high slots set long sign bits: the reader
    # must mask to the field, not shift signed words around
    values = [(1 << 5) - 1] * BLOCKS_PER_SECTION
    packed = pack_indices(values, BLOCKS_PER_SECTION, 5)
    cases.append({
        "bits": 5,
        "count": BLOCKS_PER_SECTION,
        "values_uniform": (1 << 5) - 1,
        "longs_sha256": sha256(b"".join(
            int(w).to_bytes(8, "big", signed=True) for w in packed)),
        "note": "longs digested as big-endian signed 8-byte words",
    })
    # a palette that shrank leaves the array packed at the old, wider width;
    # the array length is the authority, not the palette size
    rng = random.Random(31)
    values = [rng.randrange(3) for _ in range(100)]
    packed = pack_indices(values, len(values), 5)
    cases.append({
        "kind": "wider_than_palette",
        "palette_size": 3,
        "minimum_bits": 4,
        "packed_bits": 5,
        "count": len(values),
        "values": values,
        "longs": [str(int(word)) for word in packed],
        "note": "3 entries need 4 bits at the block minimum, but these longs "
                "only reconcile with the count at 5 bits per entry; a reader "
                "must infer the width from the array length",
    })
    return {
        "version": CORPUS_VERSION,
        "layout": "entries never straddle a 64-bit word; each long holds "
                  "floor(64/bits) entries from its low bits up, leftover "
                  "high bits are padding",
        "cases": cases,
    }


def varint_cases():
    singles = []
    for value in (0, 1, 127, 128, 255, 300, 16383, 16384, 2097151,
                  2147483647):
        singles.append({
            "value": value,
            "bytes": bytes(
                b & 0xFF for b in schem_module._write_varints([value])
            ).hex(),
        })
    sequence = [0, 500, 1, 130000, 127, 128]
    return {
        "version": CORPUS_VERSION,
        "encoding": "LEB128-style: 7 data bits per byte, low group first, "
                    "high bit set means continue; at most 5 bytes",
        "singles": singles,
        "sequence": {
            "values": sequence,
            "bytes": bytes(
                b & 0xFF for b in schem_module._write_varints(sequence)
            ).hex(),
        },
    }


def nibble_cases():
    rng = random.Random(2048)
    raw = bytes(rng.randrange(256) for _ in range(2048))
    values = unpack_nibbles(TagByteArray(
        v - 256 if v > 127 else v for v in raw))
    doc = bytes([0x00, 0xFF, 0xF0, 0x0F])
    return {
        "version": CORPUS_VERSION,
        "layout": "one 0-15 level per cell in y,z,x order; byte i>>1, even "
                  "cells take the low nibble",
        "example": {
            "bytes": doc.hex(),
            "first_values": [0, 0, 15, 15, 0, 15, 15, 0],
        },
        "full": {
            "bytes": raw.hex(),
            "first_values": values[:32],
            "values_sha256": sha256(bytes(values)),
        },
    }


# --------------------------------------------------------------------------
# The vendored region file and a world assembled from it
# --------------------------------------------------------------------------


def region_expectations():
    path = os.path.join(_HERE, "r.-1.0.mca")
    with open(path, "rb") as handle:
        raw = handle.read()
    region = NBT.load(path)
    chunks = []
    for (x, z), chunk in sorted(region.chunks.items()):
        payload = encode(chunk.data, chunk.data_name)
        blocks = Blocks(chunk)
        chunks.append({
            "x": x, "z": z,
            "compression": chunk.compression,
            "timestamp": chunk.timestamp,
            "payload_sha256": sha256(payload),
            "data_version": blocks.data_version,
            "status": str(blocks.status),
        })
    return {
        "version": CORPUS_VERSION,
        "file": "../r.-1.0.mca",
        "file_sha256": sha256(raw),
        "untouched_roundtrip": "loading this file and saving it unmodified "
                               "must reproduce it byte for byte (original "
                               "compressed bytes are reused, so this holds "
                               "in any language)",
        "chunks": chunks,
        "empty_region": {
            "file": "empty/r.0.0.mca",
            "rule": "a zero-byte .mca is a valid empty region (Minecraft "
                    "writes them, commonly under poi/): it loads with no "
                    "chunks and saves back to zero bytes, never growing an "
                    "8 KiB header unless chunks are added",
        },
    }


def world_samples():
    directory = tempfile.mkdtemp(prefix="conformance-world-")
    try:
        region_dir = os.path.join(
            directory, "dimensions", "minecraft", "overworld", "region")
        os.makedirs(region_dir)
        shutil.copyfile(os.path.join(_HERE, "r.-1.0.mca"),
                        os.path.join(region_dir, "r.-1.0.mca"))
        shutil.copyfile(os.path.join(_HERE, "level.dat"),
                        os.path.join(directory, "level.dat"))
        from heaton_voxel.world import OVERWORLD, MinecraftSave

        save = MinecraftSave.load(directory, dimensions=[OVERWORLD])
        chunk_origins = [(-448, 224), (-320, 0), (-512, 32)]  # (4,14) (12,0) (0,2)

        blocks = []
        for ox, oz in chunk_origins:
            for y in (-40, 0, 63, 66, 70):
                for dx in range(1, 16, 6):
                    for dz in range(1, 16, 6):
                        state = save.get_block(ox + dx, y, oz + dz)
                        blocks.append([ox + dx, y, oz + dz,
                                       None if state is None else str(state)])
        biomes = []
        for ox, oz in chunk_origins:
            for y in (-40, 63):
                for dx in (2, 9):
                    biome = save.get_biome(ox + dx, y, oz + dx)
                    biomes.append([ox + dx, y, oz + dx, biome])
        surfaces = []
        for ox, oz in chunk_origins:
            for dx in (0, 5, 11):
                column = {"x": ox + dx, "z": oz + dx}
                for name in ("WORLD_SURFACE", "MOTION_BLOCKING",
                             "MOTION_BLOCKING_NO_LEAVES", "OCEAN_FLOOR"):
                    column[name] = save.surface_y(ox + dx, oz + dx, name)
                surfaces.append(column)
        light = []
        for ox, oz in ((-448, 224),):
            for y in (60, 64, 67, 69, 75):
                for dx in (2, 8, 13):
                    dim = save.dimension(OVERWORLD)
                    accessor = dim.blocks((ox + dx) >> 4, (oz + 1) >> 4)
                    light.append([
                        ox + dx, y, oz + 1,
                        accessor.sky_light(dx, y, 1),
                        accessor.block_light(dx, y, 1),
                    ])
        # The village chunk carries doors, paths, torches and walls, so it
        # exercises every match mode against real game data.
        searches = []
        for spec in (
            {"names": ["minecraft:dirt_path"], "match": "name"},
            {"names": ["dirt_path"], "match": "name",
             "why": "unqualified names read as minecraft:"},
            {"names": ["_door"], "match": "suffix"},
            {"names": ["_door", "_trapdoor"], "match": "suffix",
             "why": "any of several terms"},
            {"names": ["torch"], "match": "substring"},
            {"names": ["minecraft:dirt_path"], "match": "state",
             "why": "a state with no properties"},
            {"names": ["_door"], "match": "suffix", "limit": 3,
             "why": "a capped search reports truncated, never complete"},
            {"names": ["minecraft:dirt_path"], "match": "name",
             "box": [[-464, 60, 224], [-433, 80, 255]],
             "why": "spans a chunk the world never generated"},
        ):
            box = spec.get("box", [[-448, 60, 224], [-433, 80, 239]])
            found = save.find_blocks(
                tuple(box[0]), tuple(box[1]), spec["names"],
                match=spec["match"], limit=spec.get("limit"))
            searches.append({
                "box": box,
                "names": spec["names"],
                "match": spec["match"],
                "limit": spec.get("limit"),
                "why": spec.get("why"),
                "matches": [[x, y, z, str(state)]
                            for x, y, z, state in found.matches],
                "counts": {str(state): n for state, n in sorted(
                    found.counts.items(), key=lambda kv: str(kv[0]))},
                "chunks_scanned": found.chunks_scanned,
                "sections_scanned": found.sections_scanned,
                "sections_skipped": found.sections_skipped,
                "missing_chunks": [list(c) for c in found.missing_chunks],
                "truncated": found.truncated,
                "complete": found.complete,
            })

        # The fixture stores no entities at all, which is exactly the case a
        # port is most likely to get wrong: absent storage means "no entities
        # here", a complete answer, not a failure. A port that reports this
        # as incomplete, or as an unreadable chunk, fails here.
        empty = save.entities_in((-448, 0, 224), (-433, 100, 239))
        entities_query = {
            "box": [[-448, 0, 224], [-433, 100, 239]],
            "why": "no entity storage exists; absent is not a failure",
            "entities": [[identity, x, y, z]
                         for identity, x, y, z, _ in empty.entities],
            "chunks_scanned": empty.chunks_scanned,
            "chunks_without_storage": empty.chunks_without_storage,
            "unreadable_chunks": [list(c) for c, _ in empty.unreadable_chunks],
            "truncated": empty.truncated,
            "complete": empty.complete,
        }

        dim = save.dimension(OVERWORLD)
        chunk = dim.chunk(-28, 14)
        block_entities = [
            {"x": int(e["x"]), "y": int(e["y"]), "z": int(e["z"]),
             "id": str(e["id"])}
            for e in chunk.data["block_entities"]
        ]
        histogram = {
            str(state): count
            for state, count in sorted(
                dim.blocks(-28, 14).histogram().items(),
                key=lambda kv: (-kv[1], str(kv[0])),
            )[:10]
        }
        return {
            "version": CORPUS_VERSION,
            "assembly": "copy ../r.-1.0.mca to dimensions/minecraft/"
                        "overworld/region/ and ../level.dat to level.dat",
            "blocks": blocks,
            "biomes": biomes,
            "surfaces": surfaces,
            "light": {"columns": light,
                      "note": "[x, y, z, sky, block]; null = no stored array"},
            "block_entities": {"chunk": [-28, 14], "entries": block_entities},
            "histogram_top10": {"chunk": [-28, 14], "counts": histogram},
            "find_blocks": searches,
            "entities_in": entities_query,
        }
    finally:
        shutil.rmtree(directory, ignore_errors=True)


# --------------------------------------------------------------------------
# Schematic goldens
# --------------------------------------------------------------------------


def sample_schematic() -> Schematic:
    schematic = Schematic(5, 4, 6, 2825, offset=(-3, 1, 7))
    rng = random.Random(11)
    real = [
        "minecraft:oak_stairs[facing=north,half=bottom,shape=inner_left]",
        "minecraft:rail[shape=north_east]",
        "minecraft:oak_log[axis=x]",
        "minecraft:oak_sign[rotation=13]",
        "minecraft:cobblestone_wall[east=low,north=none,south=tall,up=true,west=none]",
        "minecraft:oak_door[facing=west,half=lower,hinge=right]",
        "minecraft:white_bed[facing=south,part=head]",
        "minecraft:water",
    ]
    for i in range(schematic.volume):
        if i % 3 == 0:
            schematic._blocks[i] = schematic._intern(
                BlockState(f"minecraft:cs{i % 140}"))
        else:
            schematic._blocks[i] = schematic._intern(
                BlockState.parse(rng.choice(real)))
    # push the palette past 127 so varints need two bytes
    for i in range(140):
        schematic._intern(BlockState(f"minecraft:cs{i}"))
    schematic.set_block_entity(
        2, 1, 3,
        TagCompound({
            "id": TagString("minecraft:chest"),
            "Items": TagList([TagCompound({
                "Slot": TagByte(0),
                "id": TagString("minecraft:bread"),
                "count": TagInt(7),
            })], element_type=nbt.TAG_COMPOUND),
            "CustomName": TagString("café"),
        }))
    schematic.entities.append(TagCompound({
        "id": TagString("minecraft:item_frame"),
        "Pos": TagList([TagDouble(1.5), TagDouble(2.0), TagDouble(3.03125)],
                       element_type=nbt.TAG_DOUBLE),
        "Rotation": TagList([TagFloat(180.0), TagFloat(0.0)],
                            element_type=nbt.TAG_FLOAT),
        "Facing": TagByte(2),
        "block_pos": TagIntArray([1, 2, 3]),
        "UUID": TagIntArray([1, 2, 3, 4]),
    }))
    for x in range(5):
        for y in range(4):
            for z in range(6):
                schematic.set_biome(
                    x, y, z,
                    ["minecraft:plains", "minecraft:desert",
                     "minecraft:river"][(x + y + z) % 3])
    return schematic


def schem_goldens(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    schematic = sample_schematic()
    v3 = os.path.join(out_dir, "sample_v3.schem")
    schematic.save(v3, version=3)
    without_biomes = sample_schematic()
    without_biomes._biomes = None
    without_biomes._biome_palette = None
    v2 = os.path.join(out_dir, "sample_v2.schem")
    without_biomes.save(v2, version=2)

    def payload_digest(path):
        import gzip
        with open(path, "rb") as handle:
            return sha256(gzip.decompress(handle.read()))

    def describe(s: Schematic):
        samples = []
        rng = random.Random(3)
        for _ in range(24):
            x = rng.randrange(s.width)
            y = rng.randrange(s.height)
            z = rng.randrange(s.length)
            samples.append([x, y, z, str(s.get_block(x, y, z))])
        return {
            "size": list(s.size),
            "offset": list(s.offset),
            "data_version": s.data_version,
            "palette_size": len(s.palette),
            "block_samples": samples,
            "block_entities": [
                {"pos": list(pos), "id": str(data["id"])}
                for pos, data in sorted(s.block_entities.items())
            ],
            "entities": [
                {"id": str(e["id"]),
                 "pos": [float(v) for v in e["Pos"]],
                 "facing_byte": int(e["Facing"]),
                 "block_pos": [int(v) for v in e["block_pos"]],
                 "uuid": [int(v) for v in e["UUID"]]}
                for e in s.entities
            ],
            "biome_samples": [
                [x, y, z, s.get_biome(x, y, z)]
                for x, y, z in (
                    (0, 0, 0),
                    (s.width // 2, s.height // 2, s.length // 2),
                    (s.width - 1, s.height - 1, s.length - 1),
                )
            ],
        }

    rotated = Schematic.load(v3).rotated(90)
    rotated_path = os.path.join(out_dir, "sample_v3_rot90.schem")
    rotated.save(rotated_path, version=3)

    # The other two formats, written from the loaded version-2 sample (they
    # hold no biomes), so a port writes them from the same schematic.
    structure = os.path.join(out_dir, "sample.nbt")
    Schematic.load(v2).save(structure)
    litematic = os.path.join(out_dir, "sample.litematic")
    Schematic.load(v2).save(litematic)

    return {
        "version": CORPUS_VERSION,
        "compression_note": "compare files by decoding: gzip output is not "
                            "portable across implementations. payload_sha256 "
                            "is of the uncompressed NBT; a writer verifies "
                            "itself by re-reading its own output.",
        "files": {
            "sample_v3.schem": dict(describe(Schematic.load(v3)),
                                    payload_sha256=payload_digest(v3),
                                    container_version=3, has_biomes=True),
            "sample_v2.schem": dict(describe(Schematic.load(v2)),
                                    payload_sha256=payload_digest(v2),
                                    container_version=2, has_biomes=False),
            "sample_v3_rot90.schem": dict(
                describe(Schematic.load(rotated_path)),
                payload_sha256=payload_digest(rotated_path),
                container_version=3, has_biomes=True,
                derivation="sample_v3.schem rotated 90 degrees clockwise "
                           "about +y, WorldEdit convention"),
            "sample.nbt": dict(
                describe(Schematic.load(structure)),
                payload_sha256=payload_digest(structure),
                container_version=None, has_biomes=False,
                source_format="structure", written_from="sample_v2.schem",
                derivation="sample_v2.schem saved as a vanilla structure: its "
                           "offset is not the format's to keep"),
            "sample.litematic": dict(
                describe(Schematic.load(litematic)),
                payload_sha256=payload_digest(litematic),
                container_version=7, has_biomes=False,
                source_format="litematic", written_from="sample_v2.schem",
                derivation="sample_v2.schem saved as a Litematica schematic"),
        },
    }


#: The two-block plants of world_edits' row z=236, one each, in this order:
#: every member of place._TALL_PLANTS but the small dripleaf, which row 226
#: places. The script refuses to be written if the two lists part, so a
#: plant added to the twins' list is a plant the corpus places.
_TALL_PLANT_ROW = ("sunflower", "lilac", "rose_bush", "peony", "tall_grass",
                   "large_fern", "pitcher_plant", "tall_seagrass")


def world_edit_script():
    """A deterministic edit script, and everything it produced.

    The read samples in ``world.json`` cannot catch a port whose *writes*
    diverge -- a fill that packs bits differently, a placement that lays a
    bed's halves the other way round, a heightmap rebuild off by one. This
    records one fixed sequence of operations *as data*, so a port executes
    the same script rather than transcribing it, and captures both the
    reported counts and the resulting bytes: ``chunk_payload_sha256`` is the
    uncompressed NBT of each touched chunk after the whole script, so a port
    that agrees here has produced the same save, not merely the same summary.

    No entities are spawned: every entity carries a fresh random UUID, which
    no two runs can share, and a nondeterministic byte in the chunk would
    make the digests useless.

    The script's last section is a winter it builds in order to thaw: the
    counts a thaw reports are the easy half, and the hard half is everything
    it must leave alone, so the box holds a snow_block, powder_snow,
    packed_ice, blue_ice, frosted_ice and a powder_snow_cauldron and the
    census afterwards says they are all still there.
    """
    tall = {f"minecraft:{plant}" for plant in _TALL_PLANT_ROW}
    if tall | {"minecraft:small_dripleaf"} != set(place._TALL_PLANTS):
        raise AssertionError(
            f"world_edits' tall-plant row {sorted(tall)} is not "
            f"place._TALL_PLANTS less the small dripleaf, "
            f"{sorted(place._TALL_PLANTS)}")
    # The script. Coordinates sit inside the fixture's generated chunks:
    # (-28, 14) is isolated, (-25, 1) and (-24, 1) adjoin, and (-27, 14) does
    # not exist at all -- which the "off the edge" fill relies on.
    script = [
        {"op": "fill", "why": "a 5x4x5 platform, well inside one chunk",
         "from": [-446, 80, 226], "to": [-442, 83, 230],
         "block": "minecraft:stone"},
        {"op": "fill", "why": "spans the border of two generated chunks",
         "from": [-390, 84, 20], "to": [-380, 84, 24],
         "block": "minecraft:oak_planks"},
        {"op": "fill", "why": "reaches into a chunk that does not exist; it "
                              "must be reported, never invented",
         "from": [-440, 84, 226], "to": [-425, 84, 227],
         "block": "minecraft:bricks"},
        {"op": "fill_biome", "why": "cell-aligned growth is reported, not implied",
         "from": [-446, 80, 226], "to": [-441, 83, 231],
         "biome": "minecraft:snowy_taiga"},
        {"op": "place", "why": "a bed lays both halves toward facing",
         "at": [-445, 84, 227], "block": "minecraft:red_bed[facing=south]"},
        {"op": "place", "why": "a door lays both halves upward",
         "at": [-443, 84, 227], "block": "minecraft:oak_door[facing=west]"},
        {"op": "place", "at": [-445, 84, 230], "block": "minecraft:oak_fence"},
        {"op": "place", "at": [-444, 84, 230], "block": "minecraft:oak_fence"},
        {"op": "place", "at": [-443, 84, 230],
         "block": "minecraft:oak_fence_gate[facing=north]"},
        {"op": "place", "at": [-445, 84, 229], "block": "minecraft:cobblestone_wall"},
        {"op": "place", "why": "stacked on the wall below, whose post changes",
         "at": [-445, 85, 229], "block": "minecraft:cobblestone_wall"},
        {"op": "place", "at": [-444, 84, 229],
         "block": "minecraft:oak_stairs[facing=south]"},
        # Bare bulk construction: fill writes states verbatim, so these stand
        # disconnected -- the case connect_blocks exists for, and the only way
        # to give it real work here (place_block settled everything above).
        {"op": "fill", "why": "bare fence run, disconnected until settled",
         "from": [-446, 84, 232], "to": [-442, 84, 232],
         "block": "minecraft:oak_fence"},
        {"op": "fill", "why": "bare wall run: heights and post flags",
         "from": [-446, 84, 234], "to": [-444, 84, 234],
         "block": "minecraft:cobblestone_wall"},
        {"op": "fill", "why": "two stairs whose corner is suppressed by the "
                              "matching stair flanking them",
         "from": [-446, 84, 236], "to": [-445, 84, 236],
         "block": "minecraft:oak_stairs[facing=south]"},
        {"op": "fill", "from": [-445, 84, 237], "to": [-445, 84, 237],
         "block": "minecraft:oak_stairs[facing=east]"},
        {"op": "fill", "why": "...and a corner with no flank beside it, which "
                              "does turn",
         "from": [-443, 84, 238], "to": [-443, 84, 238],
         "block": "minecraft:oak_stairs[facing=south]"},
        {"op": "fill", "from": [-443, 84, 239], "to": [-443, 84, 239],
         "block": "minecraft:oak_stairs[facing=east]"},
        {"op": "connect", "why": "settle the whole build, margin included",
         "from": [-447, 79, 225], "to": [-441, 86, 239]},
        {"op": "rebuild_heightmaps", "from": [-446, 0, 226], "to": [-442, 0, 237]},
        {"op": "reset_lighting", "from": [-446, 0, 226], "to": [-442, 0, 230],
         "neighbors": False},

        # Winter, and everything that only wears its coat. The fixture is a
        # summer afternoon -- not one snow layer, not one ice block -- so the
        # frozen state has to be built before it can be thawed, exactly as the
        # bare fence run above exists to give connect_blocks real work.
        #
        # Row z=228 is the whole of what a cold chunk tick writes.
        {"op": "fill", "why": "snow at three depths. Only layers=1 is a "
                              "freeze product -- max_snow_accumulation_height "
                              "defaults to 1 -- so 3 and 8 must SURVIVE and "
                              "be counted as deep_snow_kept",
         "from": [-446, 90, 228], "to": [-446, 90, 228],
         "block": "minecraft:snow[layers=1]"},
        {"op": "fill", "from": [-445, 90, 228], "to": [-445, 90, 228],
         "block": "minecraft:snow[layers=3]"},
        {"op": "fill", "from": [-444, 90, 228], "to": [-444, 90, 228],
         "block": "minecraft:snow[layers=8]"},
        {"op": "fill", "why": "a floor, so this ice is not a lid over air",
         "from": [-443, 89, 228], "to": [-443, 89, 228],
         "block": "minecraft:stone"},
        {"op": "fill", "from": [-443, 90, 228], "to": [-443, 90, 228],
         "block": "minecraft:ice"},
        {"op": "fill", "why": "the three tops that carry a snowy flag",
         "from": [-442, 90, 228], "to": [-442, 90, 228],
         "block": "minecraft:grass_block[snowy=true]"},
        {"op": "fill", "from": [-441, 90, 228], "to": [-441, 90, 228],
         "block": "minecraft:podzol[snowy=true]"},
        {"op": "fill", "from": [-440, 90, 228], "to": [-440, 90, 228],
         "block": "minecraft:mycelium[snowy=true]"},
        # Row z=230 is winter that the weather never wrote: worldgen,
        # crafting and a frost walker's footprints. Every one of these is
        # inside the thaw box below and every one must come through it
        # untouched -- a port matching "snow" or "ice" loosely eats the lot,
        # and with them every snowy hillside in a real save.
        {"op": "fill", "from": [-446, 90, 230], "to": [-446, 90, 230],
         "block": "minecraft:snow_block"},
        {"op": "fill", "from": [-445, 90, 230], "to": [-445, 90, 230],
         "block": "minecraft:powder_snow"},
        {"op": "fill", "from": [-444, 90, 230], "to": [-444, 90, 230],
         "block": "minecraft:packed_ice"},
        {"op": "fill", "from": [-443, 90, 230], "to": [-443, 90, 230],
         "block": "minecraft:blue_ice"},
        {"op": "fill", "from": [-442, 90, 230], "to": [-442, 90, 230],
         "block": "minecraft:frosted_ice[age=2]"},
        {"op": "fill", "from": [-441, 90, 230], "to": [-441, 90, 230],
         "block": "minecraft:powder_snow_cauldron[level=2]"},
        {"op": "fill", "why": "already thawed: it must keep snowy=false and "
                              "not be counted as cleared",
         "from": [-440, 90, 230], "to": [-440, 90, 230],
         "block": "minecraft:grass_block[snowy=false]"},
        # Row z=232: a fence attached to ice. Ice is a sturdy full cube
        # (connect_faces.WHOLE) and water is not, which is why melting it
        # is a connection change and unfreeze names connect_blocks in its
        # docstring. The fence is settled before the thaw and again after, so
        # the second pass has exactly this one block to correct.
        {"op": "fill", "from": [-445, 91, 232], "to": [-445, 91, 232],
         "block": "minecraft:oak_fence"},
        {"op": "fill", "from": [-444, 90, 232], "to": [-444, 90, 232],
         "block": "minecraft:stone"},
        {"op": "fill", "from": [-444, 91, 232], "to": [-444, 91, 232],
         "block": "minecraft:ice"},
        # Row z=234: what hangs under a lid. Ice over air melts into water
        # standing in the sky -- real, and reported rather than refused.
        {"op": "fill", "why": "a lid over nothing: the floating_water case",
         "from": [-446, 93, 234], "to": [-446, 93, 234],
         "block": "minecraft:ice"},
        {"op": "fill", "from": [-444, 92, 234], "to": [-444, 92, 234],
         "block": "minecraft:stone"},
        {"op": "fill", "why": "resting on stone: not floating",
         "from": [-444, 93, 234], "to": [-444, 93, 234],
         "block": "minecraft:ice"},
        {"op": "fill", "why": "resting on snow the same thaw removes. The "
                              "question is asked of the world as the call "
                              "found it, so this is NOT floating water, and "
                              "the answer cannot depend on which section the "
                              "sweep reached first",
         "from": [-442, 92, 234], "to": [-442, 92, 234],
         "block": "minecraft:snow[layers=1]"},
        {"op": "fill", "from": [-442, 93, 234], "to": [-442, 93, 234],
         "block": "minecraft:ice"},
        # Row z=236 is thawed by a box exactly one block high, so what sits
        # under each lid is outside it. Deciding floating water from inside
        # the box alone gets both of these wrong.
        {"op": "fill", "from": [-446, 93, 236], "to": [-446, 93, 236],
         "block": "minecraft:ice"},
        {"op": "fill", "from": [-444, 92, 236], "to": [-444, 92, 236],
         "block": "minecraft:stone"},
        {"op": "fill", "from": [-444, 93, 236], "to": [-444, 93, 236],
         "block": "minecraft:ice"},
        {"op": "fill", "from": [-442, 93, 236], "to": [-442, 93, 236],
         "block": "minecraft:snow[layers=2]"},
        # Row z=238: `snowy` is positional. `SnowyBlock.isSnowySetting` is
        # `state.is(BlockTags.SNOW)` on the block ABOVE, and that tag holds
        # snow, snow_block AND powder_snow. So a top under snow this thaw
        # deliberately leaves standing must KEEP snowy=true -- clearing it
        # writes a state vanilla never produces and `updateShape` only fires
        # on a neighbor change, so the ground would render dirt-sided
        # forever. A port deciding `snowy` from the top's own state alone
        # gets all three of these wrong.
        {"op": "fill", "from": [-446, 90, 238], "to": [-446, 90, 238],
         "block": "minecraft:grass_block[snowy=true]"},
        {"op": "fill", "why": "snow_block is in BlockTags.SNOW and survives "
                              "the thaw, so the grass under it stays snowy",
         "from": [-446, 91, 238], "to": [-446, 91, 238],
         "block": "minecraft:snow_block"},
        {"op": "fill", "from": [-444, 90, 238], "to": [-444, 90, 238],
         "block": "minecraft:podzol[snowy=true]"},
        {"op": "fill", "why": "powder_snow is in the tag too",
         "from": [-444, 91, 238], "to": [-444, 91, 238],
         "block": "minecraft:powder_snow"},
        {"op": "fill", "from": [-442, 90, 238], "to": [-442, 90, 238],
         "block": "minecraft:mycelium[snowy=true]"},
        {"op": "fill", "why": "deep snow, which the thaw also leaves, so "
                              "this top keeps its flag as well",
         "from": [-442, 91, 238], "to": [-442, 91, 238],
         "block": "minecraft:snow[layers=4]"},
        {"op": "fill", "from": [-440, 90, 238], "to": [-440, 90, 238],
         "block": "minecraft:grass_block[snowy=true]"},
        {"op": "fill", "why": "an ordinary layer, removed by the thaw below, "
                              "so THIS top does get unsnowed -- the pair with "
                              "the row above is what makes the vector "
                              "discriminate",
         "from": [-440, 91, 238], "to": [-440, 91, 238],
         "block": "minecraft:snow[layers=1]"},
        # Row z=239: lids over things other than air and stone. `_air_below`
        # is the only thing `floating_water` rests on, and a port that treats
        # water below as "nothing there" reports every frozen lake in the
        # game as floating.
        {"op": "fill", "from": [-446, 92, 239], "to": [-446, 92, 239],
         "block": "minecraft:water[level=0]"},
        {"op": "fill", "why": "ice on water: the ordinary frozen lake, and "
                              "NOT floating",
         "from": [-446, 93, 239], "to": [-446, 93, 239],
         "block": "minecraft:ice"},
        {"op": "fill", "from": [-444, 92, 239], "to": [-444, 92, 239],
         "block": "minecraft:cave_air"},
        {"op": "fill", "why": "cave_air is air: floating. A port whose air "
                              "set is just minecraft:air misses this one",
         "from": [-444, 93, 239], "to": [-444, 93, 239],
         "block": "minecraft:ice"},
        {"op": "fill", "why": "a lid at y=96, the first block of section 6, "
                              "so its support is in the section BELOW. A "
                              "port that looks for the block under it by "
                              "wrapping inside the current section reads the "
                              "wrong cell entirely",
         "from": [-442, 96, 239], "to": [-442, 96, 239],
         "block": "minecraft:ice"},
        {"op": "fill", "from": [-440, 95, 239], "to": [-440, 95, 239],
         "block": "minecraft:stone"},
        {"op": "fill", "why": "the same section boundary, but resting on the "
                              "stone below it: not floating",
         "from": [-440, 96, 239], "to": [-440, 96, 239],
         "block": "minecraft:ice"},
        # Chunks (-25,1) and (-24,1) adjoin at x -385|-384, and sections 5
        # and 6 at y 95|96. Frozen blocks on both sides of both lines, so a
        # box over them has to clip against the chunk and section base rather
        # than mask the low bits off the world coordinate.
        {"op": "fill", "from": [-387, 94, 20], "to": [-387, 94, 20],
         "block": "minecraft:snow[layers=1]"},
        {"op": "fill", "from": [-382, 94, 20], "to": [-382, 94, 20],
         "block": "minecraft:snow[layers=1]"},
        {"op": "fill", "from": [-387, 97, 20], "to": [-387, 97, 20],
         "block": "minecraft:snow[layers=1]"},
        {"op": "fill", "from": [-382, 97, 20], "to": [-382, 97, 20],
         "block": "minecraft:snow[layers=1]"},
        {"op": "fill", "from": [-386, 95, 21], "to": [-386, 95, 21],
         "block": "minecraft:ice"},
        {"op": "fill", "from": [-383, 96, 21], "to": [-383, 96, 21],
         "block": "minecraft:ice"},
        {"op": "connect", "why": "the fence takes hold of the ice beside it",
         "from": [-447, 88, 226], "to": [-437, 95, 236]},
        {"op": "unfreeze", "why": "the thaw: rows 228, 230, 232 and 234",
         "from": [-447, 88, 226], "to": [-437, 95, 235]},
        {"op": "unfreeze", "why": "one block high, so the floating-water "
                                  "check has to read below the box to answer",
         "from": [-447, 93, 236], "to": [-437, 93, 236]},
        {"op": "unfreeze", "why": "reaches a chunk that does not exist: "
                                  "reported, never invented, and nothing "
                                  "frozen in the part that does exist",
         "from": [-440, 84, 226], "to": [-425, 84, 227]},
        {"op": "unfreeze", "why": "the ground row of z=238 ALONE. Every snow "
                                  "block sits at y=91, one above the box, so "
                                  "all four tops keep snowy=true. A port that "
                                  "answers `snowy` from the top's own state, "
                                  "or that only looks inside the box, "
                                  "unsnows all four here",
         "from": [-447, 90, 238], "to": [-437, 90, 238]},
        {"op": "unfreeze", "why": "now z=238 and z=239 whole. Only the top "
                                  "under the ordinary layer is unsnowed; the "
                                  "three under snow_block, powder_snow and "
                                  "deep snow keep the flag, because that snow "
                                  "is still standing when the thaw ends",
         "from": [-447, 88, 238], "to": [-437, 96, 239]},
        {"op": "unfreeze", "why": "crosses a chunk line (x -385|-384) AND a "
                                  "section line (y 95|96), with frozen blocks "
                                  "on both sides of each. Every other thaw "
                                  "here fits inside one chunk and one "
                                  "section, which leaves the local-coordinate "
                                  "clip untested: a port masking with `x & 15` "
                                  "instead of clipping against the chunk base "
                                  "reproduces those byte for byte and then "
                                  "thaws NOTHING for a real box. This is also "
                                  "the corpus's only chunks_modified of 2",
         "from": [-387, 94, 20], "to": [-382, 98, 21]},
        {"op": "unfreeze", "why": "deep_snow=True finally takes the layers=3 "
                                  "and layers=8 left standing at row 228, so "
                                  "the flag is not merely declared",
         "deep_snow": True,
         "from": [-447, 88, 226], "to": [-437, 95, 235]},
        {"op": "connect", "why": "unfreeze runs no aftercare, so this is the "
                                 "caller's job: the fence lets go of water",
         "from": [-447, 88, 226], "to": [-437, 95, 236]},
        {"op": "rebuild_heightmaps", "why": "also the caller's job: taking "
                                            "a snow layer off lowers the "
                                            "surface",
         "from": [-447, 0, 226], "to": [-437, 0, 236]},

        # Rows z=224 and z=226 at y=100: what a placement waterlogs into.
        # Nearly every waterloggable block's getStateForPlacement asks
        # fluidState.is(Fluids.WATER), the SOURCE; LiquidBlock makes every
        # level from 1 up FLOWING_WATER, another fluid. A port that
        # waterlogs into any cell named minecraft:water writes a new source
        # into a stream. Every other cell is left empty, so nothing placed
        # here connects to a neighbor.
        {"op": "fill", "why": "the legacy codec stores the bare id as given: "
                              "level unsaid, which is its default, 0, the "
                              "source",
         "from": [-447, 100, 224], "to": [-447, 100, 224],
         "block": "minecraft:water"},
        {"op": "place", "why": "so the stair is waterlogged",
         "at": [-447, 100, 224], "block": "minecraft:oak_stairs[facing=north]"},
        {"op": "fill", "from": [-445, 100, 224], "to": [-445, 100, 224],
         "block": "minecraft:water[level=3]"},
        {"op": "place", "why": "flowing water waterlogs nothing: the stair "
                               "displaces it and stays dry",
         "at": [-445, 100, 224], "block": "minecraft:oak_stairs[facing=north]"},
        {"op": "fill", "from": [-443, 100, 224], "to": [-443, 100, 224],
         "block": "minecraft:water[level=8]"},
        {"op": "place", "why": "nor does falling water, though its amount is "
                               "the source's 8",
         "at": [-443, 100, 224], "block": "minecraft:oak_stairs[facing=north]"},
        {"op": "fill", "from": [-441, 100, 224], "to": [-441, 100, 224],
         "block": "minecraft:seagrass"},
        {"op": "place", "why": "seagrass is a water source in every state, "
                               "as are tall seagrass, kelp and a bubble "
                               "column: the fence takes its water",
         "at": [-441, 100, 224], "block": "minecraft:oak_fence"},
        {"op": "fill", "from": [-439, 100, 224], "to": [-439, 100, 224],
         "block": "minecraft:bubble_column[drag=true]"},
        {"op": "place", "at": [-439, 100, 224],
         "block": "minecraft:iron_chain[axis=y]"},
        {"op": "fill", "from": [-437, 100, 224], "to": [-437, 100, 224],
         "block": "minecraft:water[level=8]"},
        {"op": "place", "why": "a coral asks is(FluidTags.WATER) && "
                               "isFull(), an amount of 8: falling water "
                               "waterlogs it",
         "at": [-437, 100, 224], "block": "minecraft:tube_coral_fan"},
        {"op": "fill", "from": [-435, 100, 224], "to": [-435, 100, 224],
         "block": "minecraft:water[level=3]"},
        {"op": "place", "why": "the conduit asks the same, so flowing water "
                               "leaves it dry; it is wet by default, so dry "
                               "is written out, as the game writes it",
         "at": [-435, 100, 224], "block": "minecraft:conduit"},
        {"op": "fill", "from": [-447, 100, 226], "to": [-447, 100, 226],
         "block": "minecraft:water[level=3]"},
        {"op": "place", "why": "the small dripleaf is two blocks, and each "
                               "half copies isWaterAt, any water: flowing "
                               "waterlogs the lower, and the air above "
                               "leaves the upper dry",
         "at": [-447, 100, 226], "block": "minecraft:small_dripleaf"},
        {"op": "fill", "from": [-445, 100, 226], "to": [-445, 100, 226],
         "block": "minecraft:water[level=0]"},
        {"op": "place", "why": "the light block has no getStateForPlacement "
                               "and is placed in its default state, dry, "
                               "even into a source",
         "at": [-445, 100, 226], "block": "minecraft:light[level=15]"},
        {"op": "place", "why": "a sea pickle dropped into air is placed dry; "
                               "left bare it would read as its default, "
                               "waterlogged, a source standing in the air",
         "at": [-443, 100, 226], "block": "minecraft:sea_pickle"},
        {"op": "fill", "from": [-441, 100, 226], "to": [-441, 100, 226],
         "block": "minecraft:water[level=8]"},
        {"op": "place", "why": "falling water waterlogs it too; the upper "
                               "half takes the lower's facing",
         "at": [-441, 100, 226],
         "block": "minecraft:small_dripleaf[facing=east]"},
        {"op": "fill", "from": [-439, 100, 226], "to": [-439, 100, 226],
         "block": "minecraft:water[level=3]"},
        {"op": "place", "why": "a coral is the conduit's rule: flowing water "
                               "leaves it dry, written out",
         "at": [-439, 100, 226], "block": "minecraft:tube_coral"},
        {"op": "fill", "from": [-437, 100, 226], "to": [-437, 100, 226],
         "block": "minecraft:water[level=8]"},
        {"op": "place", "why": "and falling water waterlogs the conduit",
         "at": [-437, 100, 226], "block": "minecraft:conduit"},
        {"op": "fill", "from": [-435, 100, 226], "to": [-435, 100, 226],
         "block": "minecraft:water"},
        {"op": "place", "why": "a double slab is a whole block: SlabBlock "
                               "sets waterlogged false without asking the "
                               "water",
         "at": [-435, 100, 226], "block": "minecraft:oak_slab[type=double]"},

        # Row z=228: a campfire placed into water is put out, and every
        # block that is a water source in all its states is water to what
        # replaces it, as is a block wet by default and left bare.
        {"op": "fill", "from": [-447, 100, 228], "to": [-447, 100, 228],
         "block": "minecraft:water"},
        {"op": "place", "why": "CampfireBlock sets lit to the opposite of "
                               "the waterlogged it sets",
         "at": [-447, 100, 228], "block": "minecraft:campfire[facing=east]"},
        {"op": "fill", "from": [-445, 100, 228], "to": [-445, 100, 228],
         "block": "minecraft:water"},
        {"op": "place", "why": "unless the caller said lit",
         "at": [-445, 100, 228], "block": "minecraft:campfire[lit=true]"},
        {"op": "fill", "from": [-443, 100, 228], "to": [-443, 100, 228],
         "block": "minecraft:water[level=8]"},
        {"op": "place", "why": "falling water is not the source a campfire "
                               "takes: it stays as given, and lit",
         "at": [-443, 100, 228], "block": "minecraft:soul_campfire"},
        *[step
          for x, holder in ((-441, "minecraft:kelp"),
                            (-439, "minecraft:kelp_plant"),
                            (-437, "minecraft:tall_seagrass[half=lower]"),
                            (-435, "minecraft:sea_pickle"))
          for step in (
              {"op": "fill", "from": [x, 100, 228], "to": [x, 100, 228],
               "block": holder},
              {"op": "place", "at": [x, 100, 228],
               "block": "minecraft:oak_fence"})],

        # Row z=230: what a waterlogged block holds, and water the game does
        # not have -- refused under a block that would take it, never read
        # under one that takes none.
        {"op": "fill", "from": [-447, 100, 230], "to": [-447, 100, 230],
         "block": "minecraft:oak_slab[type=bottom,waterlogged=true]"},
        {"op": "place", "why": "a waterlogged block holds a source",
         "at": [-447, 100, 230], "block": "minecraft:oak_fence"},
        {"op": "fill", "from": [-445, 100, 230], "to": [-445, 100, 230],
         "block": "minecraft:oak_slab"},
        {"op": "place", "why": "and a bare slab is dry, its default",
         "at": [-445, 100, 230], "block": "minecraft:oak_fence"},
        {"op": "fill", "from": [-443, 100, 230], "to": [-443, 100, 230],
         "block": "minecraft:water[level=16]"},
        {"op": "place", "refused": True,
         "why": "level 16 is no water the game has: refused, nothing written",
         "at": [-443, 100, 230], "block": "minecraft:oak_stairs[facing=north]"},
        {"op": "fill", "from": [-441, 100, 230], "to": [-441, 100, 230],
         "block": "minecraft:oak_slab[waterlogged=maybe]"},
        {"op": "place", "refused": True,
         "why": "nor is a waterlogged that is neither true nor false",
         "at": [-441, 100, 230], "block": "minecraft:oak_fence"},
        {"op": "fill", "from": [-439, 100, 230], "to": [-439, 100, 230],
         "block": "minecraft:water[level=16]"},
        {"op": "place", "why": "a double slab reads no water",
         "at": [-439, 100, 230], "block": "minecraft:stone_slab[type=double]"},
        {"op": "fill", "from": [-437, 100, 230], "to": [-437, 100, 230],
         "block": "minecraft:water[level=16]"},
        {"op": "place", "why": "nor does the light block",
         "at": [-437, 100, 230], "block": "minecraft:light"},

        # Row z=236: every two-block plant place_block stands whole, both
        # halves read back -- the list the twins each keep (_TALL_PLANTS,
        # Place.TallPlants), which a port that drops one would otherwise
        # pass with its lower half standing alone. The small dripleaf is
        # row 226's. Tall seagrass goes into a source two high, the water
        # it grows in.
        {"op": "fill", "from": [-433, 100, 236], "to": [-433, 101, 236],
         "block": "minecraft:water[level=0]"},
        *({"op": "place", "at": [-447 + 2 * i, 100, 236],
           "block": f"minecraft:{plant}"}
          for i, plant in enumerate(_TALL_PLANT_ROW)),

        # Row z=238: a two-cell placement reads each cell's own water, and
        # every cell before writing any. A dripleaf in water two deep is
        # wet top and bottom; one whose upper cell is water the game does
        # not have is refused, its lower cell left the source it was.
        {"op": "fill", "from": [-447, 100, 238], "to": [-445, 101, 238],
         "block": "minecraft:water[level=0]"},
        {"op": "place", "why": "each half copies its own cell's water",
         "at": [-447, 100, 238], "block": "minecraft:small_dripleaf"},
        {"op": "fill", "from": [-445, 101, 238], "to": [-445, 101, 238],
         "block": "minecraft:water[level=16]"},
        {"op": "place", "refused": True,
         "why": "the upper cell is read before the lower is written: no "
                "lone lower half",
         "at": [-445, 100, 238], "block": "minecraft:small_dripleaf"},

        # Rows z=232 (live) and z=234 (dead): every coral that carries
        # waterlogged, each into falling water, which it takes.
        *[step
          for z, dead in ((232, ""), (234, "dead_"))
          for step in (
              {"op": "fill", "from": [-448, 100, z], "to": [-434, 100, z],
               "block": "minecraft:water[level=8]"},
              *({"op": "place", "at": [-448 + i, 100, z],
                 "block": f"minecraft:{dead}{color}_coral{form}"}
                for i, (color, form) in enumerate(
                    (color, form)
                    for color in ("tube", "brain", "bubble", "fire", "horn")
                    for form in ("", "_fan", "_wall_fan"))))],
        # Replacing what a mapping names, state for state. The village's
        # smoker (y=64) and campfire (y=67) each hold a block entity: one
        # turned keeps it, one made another block loses it.
        {"op": "replace", "why": "ground, a turned smoker and a soul "
                                 "campfire: two block entities, one kept",
         "from": [-448, 60, 224], "to": [-440, 70, 232],
         "mapping": [
             ["minecraft:dirt", "minecraft:coarse_dirt"],
             ["minecraft:grass_block[snowy=false]", "minecraft:moss_block"],
             ["minecraft:smoker[facing=south,lit=false]",
              "minecraft:smoker[facing=north,lit=false]"],
             ["minecraft:campfire[facing=north,lit=true,signal_fire=false,"
              "waterlogged=false]",
              "minecraft:soul_campfire[facing=north,lit=true,"
              "signal_fire=false,waterlogged=false]"],
         ]},
        {"op": "replace", "why": "a whole section of air to one block "
                                 "collapses to a one-entry palette",
         "from": [-448, 304, 224], "to": [-433, 319, 239],
         "mapping": [["minecraft:air", "minecraft:glass"]]},
        {"op": "replace", "why": "two chunks never generated and two the "
                                 "game has not finished: nothing written",
         "from": [-520, 60, 450], "to": [-500, 62, 470],
         "mapping": [["minecraft:stone", "minecraft:granite"],
                     ["minecraft:air", "minecraft:glass"]]},
    ]

    directory = tempfile.mkdtemp(prefix="conformance-edits-")
    try:
        region_dir = os.path.join(
            directory, "dimensions", "minecraft", "overworld", "region")
        os.makedirs(region_dir)
        shutil.copyfile(os.path.join(_HERE, "r.-1.0.mca"),
                        os.path.join(region_dir, "r.-1.0.mca"))
        shutil.copyfile(os.path.join(_HERE, "level.dat"),
                        os.path.join(directory, "level.dat"))
        from heaton_voxel.world import OVERWORLD, MinecraftSave

        save = MinecraftSave.load(directory, dimensions=[OVERWORLD])
        results = []
        for step in script:
            corner1 = tuple(step["from"]) if "from" in step else None
            corner2 = tuple(step["to"]) if "to" in step else None
            op = step["op"]
            if op == "fill":
                r = save.fill(corner1, corner2, step["block"], keep_loaded=True)
                results.append({
                    "blocks_filled": r.blocks_filled,
                    "sections_replaced": r.sections_replaced,
                    "chunks_modified": r.chunks_modified,
                    "blocks_out_of_range": r.blocks_out_of_range,
                    "missing_chunks": sorted(list(c) for c in r.missing_chunks),
                    "complete": r.complete,
                })
            elif op == "replace":
                results.append(_replace_report(save.replace_blocks(
                    corner1, corner2, dict(step["mapping"]), keep_loaded=True)))
            elif op == "fill_biome":
                r = save.fill_biome(corner1, corner2, step["biome"],
                                    keep_loaded=True)
                results.append({
                    "cells_filled": r.cells_filled,
                    "sections_replaced": r.sections_replaced,
                    "chunks_modified": r.chunks_modified,
                    "cells_out_of_range": r.cells_out_of_range,
                    "written_box": [list(r.written_box[0]), list(r.written_box[1])],
                    "complete": r.complete,
                })
            elif op == "place" and step.get("refused"):
                x, y, z = step["at"]
                try:
                    save.place_block(x, y, z, step["block"])
                except nbt.NBTError:
                    results.append({"refused": True})
                else:
                    raise AssertionError(f"{step} was not refused")
            elif op == "place":
                x, y, z = step["at"]
                results.append({"placed": bool(
                    save.place_block(x, y, z, step["block"]))})
            elif op == "unfreeze":
                r = save.unfreeze(corner1, corner2, keep_loaded=True,
                                  deep_snow=step.get("deep_snow", False))
                results.append({
                    "snow_removed": r.snow_removed,
                    "ice_melted": r.ice_melted,
                    "snowy_cleared": r.snowy_cleared,
                    "deep_snow_kept": r.deep_snow_kept,
                    "chunks_modified": r.chunks_modified,
                    "floating_water": r.floating_water,
                    "missing_chunks": sorted(
                        list(c) for c in r.missing_chunks),
                    "complete": r.complete,
                })
            elif op == "connect":
                r = save.connect_blocks(corner1, corner2, keep_loaded=True)
                results.append({
                    "blocks_scanned": r.blocks_scanned,
                    "fences_updated": r.fences_updated,
                    "walls_updated": r.walls_updated,
                    "panes_updated": r.panes_updated,
                    "gates_updated": r.gates_updated,
                    "stairs_updated": r.stairs_updated,
                    "total_updated": r.total_updated,
                    "complete": r.complete,
                })
            elif op == "rebuild_heightmaps":
                r = save.rebuild_heightmaps(corner1, corner2, keep_loaded=True)
                results.append({"chunks_rebuilt": r.chunks_rebuilt,
                                "complete": r.complete})
            elif op == "reset_lighting":
                r = save.reset_lighting(corner1, corner2,
                                        neighbors=step["neighbors"])
                results.append({"chunks_reset": r.chunks_reset,
                                "arrays_removed": r.arrays_removed,
                                "chunks_skipped": r.chunks_skipped})
            else:
                raise AssertionError(f"unknown op {op!r}")

        steps = [dict(step, result=result)
                 for step, result in zip(script, results)]

        # What the edits left behind, read back through the same accessors.
        after = []
        for x, y, z in [
            (-446, 80, 226), (-444, 82, 228), (-442, 83, 230),  # platform
            (-390, 84, 20), (-385, 84, 22), (-380, 84, 24),     # across chunks
            (-440, 84, 226), (-434, 84, 227),                   # up to the edge
            (-445, 84, 227), (-445, 84, 228),                   # bed halves
            (-443, 84, 227), (-443, 85, 227),                   # door halves
            (-445, 84, 230), (-444, 84, 230), (-443, 84, 230),  # fences + gate
            (-445, 84, 229), (-445, 85, 229),                   # stacked wall
            (-444, 84, 229),                                    # stair
            (-446, 84, 232), (-444, 84, 232), (-442, 84, 232),  # bare fence run
            (-446, 84, 234), (-445, 84, 234), (-444, 84, 234),  # bare wall run
            (-446, 84, 236), (-445, 84, 236), (-445, 84, 237),  # flanked stairs
            (-443, 84, 238), (-443, 84, 239),                   # stair corner
            # What the weather wrote, thawed
            (-446, 90, 228), (-445, 90, 228), (-444, 90, 228),  # snow depths
            (-443, 90, 228),                                    # ice -> water
            (-442, 90, 228), (-441, 90, 228), (-440, 90, 228),  # snowy tops
            # What the weather never wrote, still standing
            (-446, 90, 230), (-445, 90, 230), (-444, 90, 230),
            (-443, 90, 230), (-442, 90, 230), (-441, 90, 230),
            (-440, 90, 230),
            (-445, 91, 232), (-444, 91, 232),                   # fence + ice
            (-446, 93, 234), (-444, 93, 234),                   # lids on air,
            (-442, 93, 234), (-442, 92, 234),                   # stone, snow
            (-446, 93, 236), (-444, 93, 236), (-444, 92, 236),  # thawed by a
            (-442, 93, 236),                                    # one-high box
            # What a placement waterlogs into
            (-447, 100, 224), (-445, 100, 224), (-443, 100, 224),  # source,
            (-441, 100, 224), (-439, 100, 224),         # flowing, falling,
            (-437, 100, 224), (-435, 100, 224),         # sources by block,
            (-447, 100, 226), (-445, 100, 226),         # and the exceptions
            (-443, 100, 226),
            (-447, 101, 226), (-441, 100, 226), (-441, 101, 226),  # dripleaves
            (-439, 100, 226), (-437, 100, 226),         # coral, conduit,
            (-435, 100, 226),                           # double slab
            *((x, 100, 228) for x in range(-447, -434, 2)),  # campfires,
            *((x, 100, 230) for x in range(-447, -436, 2)),  # held water,
            *((x, 100, z) for z in (232, 234)                # refusals,
              for x in range(-448, -433)),                   # every coral
            *((-447 + 2 * i, y, 236)                         # every tall
              for i in range(len(_TALL_PLANT_ROW))           # plant, both
              for y in (100, 101)),                          # halves
            *((x, y, 238) for x in (-447, -445)              # dripleaves
              for y in (100, 101)),                          # in deep water
            # What the replaces wrote
            (-448, 60, 224), (-448, 62, 224),                # dirt, grass
            (-446, 64, 225), (-446, 67, 225),                # smoker, campfire
            (-446, 65, 225),                                 # a wall, unnamed
            (-440, 310, 230),                                # the glass section
        ]:
            state = save.get_block(x, y, z)
            after.append([x, y, z, None if state is None else str(state)])

        biomes_after = [
            [x, y, z, save.get_biome(x, y, z)]
            for x, y, z in ((-446, 80, 226), (-444, 82, 228), (-441, 83, 231),
                            (-390, 84, 20))
        ]
        surfaces_after = [
            {"x": x, "z": z, "WORLD_SURFACE": save.surface_y(x, z, "WORLD_SURFACE")}
            for x, z in ((-446, 226), (-444, 228), (-442, 230), (-390, 20),
                         # The pair that tells a thaw from a massacre: the
                         # snow column drops back to the platform under it,
                         # the snow_block column keeps every block of its
                         # height.
                         (-446, 228), (-446, 230),
                         (-443, 228), (-445, 232), (-446, 234), (-446, 236))
        ]

        # "And nothing else" is a claim about the whole box, not about the
        # cells anyone thought to sample, so it is made as a census: one
        # count per state over every cell the thaw could have reached.
        winter_box = ((-447, 88, 226), (-437, 95, 236))
        census = save.histogram(*winter_box)
        histogram_after = {
            "box": [list(winter_box[0]), list(winter_box[1])],
            "why": "the thaw box, counted exhaustively after it: a port that "
                   "ate one block nobody sampled still fails here",
            "counts": {str(state): count for state, count in sorted(
                census.counts.items(), key=lambda kv: str(kv[0]))},
            "complete": census.complete,
        }

        # The bytes themselves: whole-chunk NBT after every edit above.
        dim = save.dimension(OVERWORLD)
        block_entities_after = _block_entity_report(dim, (-28, 14))
        digests = []
        for chunk_x, chunk_z in ((-28, 14), (-25, 1), (-24, 1)):
            chunk = dim.chunk(chunk_x, chunk_z)
            digests.append({
                "chunk": [chunk_x, chunk_z],
                "payload_sha256": sha256(encode(chunk.data, chunk.data_name)),
            })

        return {
            "version": CORPUS_VERSION,
            "assembly": "as world.json: copy ../r.-1.0.mca to dimensions/"
                        "minecraft/overworld/region/ and ../level.dat to "
                        "level.dat, then run every step below in order",
            "note": "each step is the operation to perform and the result it "
                    "must report; fills, biome fills, connects and thaws run "
                    "with keep_loaded, so nothing is released mid-script. The "
                    "reported counts, the blocks read back, and the per-chunk "
                    "NBT digests must all match. Digests are of the "
                    "UNCOMPRESSED chunk NBT, so they are portable.",
            "steps": steps,
            "blocks_after": after,
            "biomes_after": biomes_after,
            "surfaces_after": surfaces_after,
            "histogram_after": histogram_after,
            "block_entities_after": block_entities_after,
            "chunk_payload_sha256": digests,
        }
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _replace_report(r) -> dict:
    """A ``replace_blocks`` result as both edit scripts record it."""
    return {
        "blocks_replaced": r.blocks_replaced,
        "sections_replaced": r.sections_replaced,
        "chunks_modified": r.chunks_modified,
        "block_entities_removed": r.block_entities_removed,
        "blocks_out_of_range": r.blocks_out_of_range,
        "missing_chunks": sorted(list(c) for c in r.missing_chunks),
        "unfinished_chunks": sorted(list(c) for c in r.unfinished_chunks),
        "unreadable_chunks": sorted(list(c) for c, _ in r.unreadable_chunks),
        "complete": r.complete,
    }


def _block_entity_report(dim, chunk: tuple[int, int]) -> dict:
    """The block entities a chunk stores, in the order it stores them: what
    a replace kept and what it took away, said outright rather than left to
    the chunk's digest."""
    data = dim.chunk(*chunk).data
    return {
        "chunk": list(chunk),
        "entities": [[int(entry["x"]), int(entry["y"]), int(entry["z"]),
                      str(entry["id"])]
                     for entry in data.get("block_entities") or ()],
    }


# --------------------------------------------------------------------------
# The 26.3 palette codec: bare ids, wrappers, id/properties
# --------------------------------------------------------------------------


def _palette_report(container):
    """A section palette as stored and as decoded, entry by entry.

    ``form`` is the shape of the stored tag -- ``string``, ``wrapped``
    (``{"": id}``), ``full`` (``{id, properties}``) or ``legacy``
    (``{Name, Properties}``) -- and ``state`` the text of what it decodes
    to, defaults restored. A port must agree on both: the first proves it
    writes what the game writes, the second that it reads what the game
    means.
    """
    from heaton_voxel.nbt import TAG_COMPOUND, TAG_STRING, TagCompound, TagString

    stored = container._tag["palette"]
    kind = {TAG_STRING: "string", TAG_COMPOUND: "compound"}.get(
        stored.element_type, "other")
    entries = []
    for tag, state in zip(stored, container.palette):
        if isinstance(tag, TagString):
            form = "string"
        elif isinstance(tag, TagCompound) and set(tag) == {""}:
            form = "wrapped"
        elif isinstance(tag, TagCompound) and "id" in tag:
            form = "full"
        elif isinstance(tag, TagCompound) and "Name" in tag:
            form = "legacy"
        else:
            form = "other"
        entries.append({"form": form, "state": str(state)})
    return {"stored": kind, "entries": entries}


def block_defaults_dump():
    """The default-state table, whole, so a port's copy is checked entry
    by entry rather than trusted to have been generated from the same
    report."""
    from heaton_voxel import block_defaults
    from heaton_voxel.blocks import MODERN_STATE_DATA_VERSION

    return {
        "version": CORPUS_VERSION,
        "game": block_defaults.GAME_VERSION,
        "data_version": block_defaults.DATA_VERSION,
        "modern_state_data_version": MODERN_STATE_DATA_VERSION,
        "why": "from 26.3 a palette stores a state at its block's defaults "
               "as the bare id, so decoding restores these and encoding "
               "consults them; both twins carry this table, generated by "
               "tools/generate_block_defaults.py from the game's own "
               "reports/blocks.json",
        "defaults": {
            name: ",".join(f"{k}={v}" for k, v in properties.items())
            for name, properties in sorted(block_defaults.BLOCK_DEFAULTS.items())
        },
        "unlisted_reads_as": "no properties: a bare id decodes to a state "
                             "with none, and a state with none encodes bare",
    }


def wall_cover_dump():
    """The wall-cover table, whole, so a port's generated copy is checked
    block by block and state by state rather than trusted to have come from
    the same run of the game."""
    from heaton_voxel import wall_cover

    tables = []
    keyed = {}
    for name, found in sorted(wall_cover._TABLE.items()):
        if found not in tables:
            tables.append(found)
        keyed[name] = tables.index(found)
    return {
        "version": CORPUS_VERSION,
        "game": wall_cover.GAME_VERSION,
        "data_version": wall_cover.DATA_VERSION,
        "why": "a wall reads the block above it on every update: the side "
               "strips and the post center that block's collision "
               "underside covers, and whether it is in "
               "wall_post_override. Both twins carry this table, generated "
               "by tools/generate_wall_cover.py from the game's own shapes",
        "bits": {"north": wall_cover.NORTH, "east": wall_cover.EAST,
                 "south": wall_cover.SOUTH, "west": wall_cover.WEST,
                 "post": wall_cover.POST, "override": wall_cover.OVERRIDE},
        "uniform": dict(sorted(wall_cover._CODE.items())),
        "tables": [{"keys": list(keys), "codes": codes}
                   for keys, codes in tables],
        "keyed": keyed,
        "unlisted_reads_as": "null: a block the game does not have, or a "
                             "value its properties do not take; a property "
                             "a state leaves out reads as its block's "
                             "default",
    }


def heightmap_cases():
    """The four maps rebuilt from the blocks: the table the game's tags were
    copied into, what it makes of every block, and a scene.

    What holds this to the game is the fixtures themselves: every finished
    chunk the game wrote, each named here, must derive its own stored maps
    and rebuild them byte for byte in a port as it does here. The scene is
    what a stored map cannot show: a map moved by an edit, and a paste that
    rewrites the maps of the chunks it moved and stays out of the ones the
    game has not finished.
    """
    from heaton_voxel import heightmap_blocks as table
    from heaton_voxel.block_defaults import BLOCK_DEFAULTS
    from heaton_voxel.blocks import (HEIGHTMAP_NAMES, MIN_DATA_VERSION,
                                     BlockState, chunk_finished, heightmap_mask)
    from heaton_voxel.nbt import NBT
    from heaton_voxel.schem import Schematic
    from heaton_voxel.world import OVERWORLD, MinecraftSave

    masks = {}
    for name in sorted(set(BLOCK_DEFAULTS) | table.NOT_IN_HEIGHTMAP
                       | {"modded:thing"}):
        masks[name] = heightmap_mask(BlockState(name))
        if "waterlogged" in BLOCK_DEFAULTS.get(name, ()):
            for value in ("false", "true"):
                state = BlockState(name, {"waterlogged": value})
                masks[str(state)] = heightmap_mask(state)

    regions = []
    for fixture in (".", "modern"):
        region = NBT.load(os.path.join(_HERE, fixture, "r.-1.0.mca"))
        finished = sorted(
            [region.region_x * 32 + x, region.region_z * 32 + z]
            for (x, z), chunk in region.chunks.items()
            if chunk_finished(chunk.data.get("Status")))
        regions.append({"fixture": fixture, "finished": finished})

    x, z = -392, 20
    script = [
        {"op": "set", "at": [x, 120, z], "block": "minecraft:water"},
        {"op": "set", "at": [x, 121, z], "block": "minecraft:oak_leaves"},
        {"op": "set", "at": [x, 122, z], "block": "minecraft:torch"},
        {"op": "stale", "chunk": [-25, 1],
         "why": "three cells in one column: every map one column out"},
        {"op": "rebuild", "from": [x, 0, z], "to": [x, 0, z]},
        {"op": "tops", "column": [x, z],
         "why": "the torch, the leaves, under them the water, and the leaves "
                "again: the four maps part at this column"},
        {"op": "stale", "chunk": [-25, 1]},
        {"op": "rebuild", "from": [-512, 0, 448], "to": [-497, 0, 479],
         "why": "two chunks the game has not finished: left as they stand"},
        {"op": "import", "at": [-385, 130, 31], "heightmaps": True,
         "why": "2x2x2 across the corner of four chunks, each rewritten"},
        {"op": "tops", "column": [-385, 31]},
        {"op": "tops", "column": [-384, 32]},
        {"op": "import", "at": [-385, 140, 16], "heightmaps": False,
         "why": "told to leave the maps, which then miss it"},
        {"op": "stale", "chunk": [-25, 1]},
        {"op": "import", "at": [-497, 130, 460], "heightmaps": True,
         "why": "across chunk (-32, 28), unfinished, and (-31, 28), never "
                "generated"},
    ]
    directory = tempfile.mkdtemp(prefix="conformance-heightmaps-")
    try:
        region_dir = os.path.join(
            directory, "dimensions", "minecraft", "overworld", "region")
        os.makedirs(region_dir)
        shutil.copyfile(os.path.join(_HERE, "r.-1.0.mca"),
                        os.path.join(region_dir, "r.-1.0.mca"))
        shutil.copyfile(os.path.join(_HERE, "level.dat"),
                        os.path.join(directory, "level.dat"))
        save = MinecraftSave.load(directory, dimensions=[OVERWORLD])
        dim = save.dimension(OVERWORLD)
        paste = Schematic(2, 2, 2, MIN_DATA_VERSION)
        for cell in itertools.product(range(2), repeat=3):
            paste.set_block(*cell, "minecraft:stone")
        steps = []
        for step in script:
            op, result = step["op"], None
            if op == "set":
                save.set_block(*step["at"], step["block"])
            elif op == "stale":
                result = dim.blocks(*step["chunk"]).stale_heightmaps()
            elif op == "rebuild":
                r = save.rebuild_heightmaps(step["from"], step["to"])
                result = {"chunks_rebuilt": r.chunks_rebuilt,
                          "missing": sorted(map(list, r.missing_chunks)),
                          "unfinished": sorted(map(list, r.unfinished_chunks)),
                          "complete": r.complete}
            elif op == "tops":
                result = {name: save.surface_y(*step["column"], name)
                          for name in HEIGHTMAP_NAMES}
            elif op == "import":
                r = save.import_schem(paste, tuple(step["at"]), anchor="min",
                                      heightmaps=step["heightmaps"])
                result = {"blocks_pasted": r.blocks_pasted,
                          "blocks_out_of_range": r.blocks_out_of_range,
                          "chunks_modified": r.chunks_modified,
                          "heightmaps_rebuilt": r.heightmaps_rebuilt,
                          "missing": sorted(map(list, r.missing_chunks)),
                          "unfinished": sorted(map(list, r.unfinished_chunks)),
                          "complete": r.complete}
            else:
                raise AssertionError(f"unknown op {op!r}")
            steps.append(dict(step, result=result))
    finally:
        shutil.rmtree(directory, ignore_errors=True)

    return {
        "version": CORPUS_VERSION,
        "game": table.GAME_VERSION,
        "data_version": table.DATA_VERSION,
        "why": "WORLD_SURFACE is format knowledge and the other three maps "
               "are the game's, in its tags. Both twins carry this table, "
               "generated by tools/generate_heightmap_blocks.py, and a "
               "rebuild writes all four in the order the game does, so a map "
               "that was right comes back the same bytes",
        "bits": {name: 1 << bit for bit, name in enumerate(HEIGHTMAP_NAMES)},
        "not_in_heightmap": sorted(table.NOT_IN_HEIGHTMAP),
        "leaves": sorted(table.LEAVES),
        "fluids": sorted(table.FLUIDS),
        "masks": masks,
        "regions": regions,
        "scene": steps,
    }


def modern_cases():
    """Real 26.3 output read back, then edited, then digested.

    ``fixtures/modern/`` is four chunks a 26.3 game wrote, cut so every
    one carries DataVersion >= 5006. Three claims are made about them and
    a port must hold all three: what each stored palette entry looks like
    and what it decodes to (defaults restored, properties in name order);
    what point reads and the four surface maps return through the world
    accessors; and, after a fixed edit script, both the palette forms the
    edits left behind and the whole-chunk NBT digests -- which is where a
    port that wraps a bare id differently, orders ``id`` and
    ``properties`` differently, or fills defaults differently is caught by
    the bytes rather than by a sample. The surface maps, the reads and
    the script's last steps also pin where a chunk's range starts and
    ends: one chunk stores light alone below the world, and the script
    adds the same above another; neither section is part of the world, so
    a cell in one reads as nothing, not as air. The last step takes away
    a chunk's bottom section, so its floor is the ``yPos`` it records and
    not the lowest section it stores.
    """
    from heaton_voxel.blocks import HEIGHTMAP_NAMES, MODERN_STATE_DATA_VERSION
    from heaton_voxel.world import OVERWORLD, MinecraftSave

    directory = tempfile.mkdtemp(prefix="conformance-modern-")
    try:
        region_dir = os.path.join(
            directory, "dimensions", "minecraft", "overworld", "region")
        os.makedirs(region_dir)
        shutil.copyfile(os.path.join(_HERE, "modern", "r.-1.0.mca"),
                        os.path.join(region_dir, "r.-1.0.mca"))
        shutil.copyfile(os.path.join(_HERE, "modern", "level.dat"),
                        os.path.join(directory, "level.dat"))
        save = MinecraftSave.load(directory, dimensions=[OVERWORLD])
        dim = save.dimension(OVERWORLD)
        region = dim.region(-1, 0)
        keys = sorted(region.chunks)

        # 1. What the game wrote, section by section.
        chunks = []
        for local_x, local_z in keys:
            chunk_x, chunk_z = region.region_x * 32 + local_x, region.region_z * 32 + local_z
            accessor = dim.blocks(chunk_x, chunk_z)
            sections = []
            for section_y in sorted(accessor.sections):
                container = accessor.sections[section_y].blocks
                if container is None:
                    sections.append({"y": section_y, "stored": "absent"})
                    continue
                sections.append(dict({"y": section_y}, **_palette_report(container)))
            chunks.append({
                "chunk": [chunk_x, chunk_z],
                "data_version": accessor.data_version,
                "modern_states": accessor.modern_states,
                "sections": sections,
            })

        # 2. Point reads through the world accessors. y=-65 is one below
        # the world: nothing in every chunk, including (-13, 19), which
        # stores a section of light alone there.
        reads = []
        for local_x, local_z in keys:
            ox = (region.region_x * 32 + local_x) * 16
            oz = (region.region_z * 32 + local_z) * 16
            for y in (-65, -60, -20, 0, 40, 60, 62, 63, 66, 70, 100):
                for dx, dz in ((1, 1), (8, 8), (14, 3), (3, 14)):
                    state = save.get_block(ox + dx, y, oz + dz)
                    reads.append([ox + dx, y, oz + dz,
                                  None if state is None else str(state)])

        # 2b. Every surface map, also before any edit. Chunk (-13, 19)
        # stores a section of light alone below the world (Y=-5), which is
        # not the floor the maps count from: a port that takes the lowest
        # stored section as the origin reads every column 16 low here.
        surfaces = []
        for local_x, local_z in keys:
            ox = (region.region_x * 32 + local_x) * 16
            oz = (region.region_z * 32 + local_z) * 16
            for dx in (0, 5, 11):
                column = {"x": ox + dx, "z": oz + dx}
                for name in HEIGHTMAP_NAMES:
                    column[name] = save.surface_y(ox + dx, oz + dx, name)
                surfaces.append(column)

        # 3. Edits, chosen for the codec's four transitions: a default
        # state joining a string list (stays strings), a non-default state
        # joining it (widens, every bare id wrapped), a default state
        # joining a compound list (wrapped on the way in), and a spelled-
        # out default finding the bare entry already there. Coordinates
        # sit inside chunks (-31, 0), (-30, 0) and (-13, 19).
        script = [
            {"op": "fill", "why": "source water into a deep, all-default "
                                  "section: the list stays strings",
             "from": [-490, -40, 5], "to": [-488, -40, 7],
             "block": "minecraft:water"},
            {"op": "fill", "why": "flowing water carries a property: the "
                                  "list widens and every bare id is wrapped",
             "from": [-490, -40, 9], "to": [-490, -40, 9],
             "block": "minecraft:water[level=8]"},
            {"op": "fill", "why": "a partial state: the omitted waterlogged "
                                  "is filled from the defaults before it is "
                                  "stored, in name order",
             "from": [-489, -40, 9], "to": [-489, -40, 9],
             "block": "minecraft:sea_pickle[pickles=4]"},
            {"op": "fill", "why": "a state spelled with its default value "
                                  "is the default: stored bare, wrapped",
             "from": [-488, -40, 9], "to": [-488, -40, 9],
             "block": "minecraft:oak_stairs[facing=north]"},
            {"op": "fill", "why": "spelled out in full, source water finds "
                                  "the bare entry the first step made",
             "from": [-487, -40, 5], "to": [-487, -40, 5],
             "block": "minecraft:water[level=0]"},
            {"op": "place", "why": "a chain dropped into water holds it: "
                                   "the game gives every chain waterlogged",
             "at": [-489, -40, 6], "block": "minecraft:iron_chain[axis=y]"},
            {"op": "place", "why": "a bell displaces it: the bell has no "
                                   "waterlogged property to set",
             "at": [-489, -40, 7], "block": "minecraft:bell"},
            {"op": "place", "why": "a lone fence derives all-false, which is "
                                   "its default: bare, in a uniform air "
                                   "section that must grow an index array",
             "at": [-486, 100, 5], "block": "minecraft:oak_fence"},
            {"op": "place", "why": "its neighbor connects both: two full "
                                   "entries, and the air list widens",
             "at": [-485, 100, 5], "block": "minecraft:oak_fence"},
            {"op": "fill", "from": [-484, 100, 5], "to": [-484, 100, 6],
             "block": "minecraft:ice"},
            {"op": "unfreeze", "why": "thawed ice is written as water[level=0], "
                                      "which the codec stores as the bare id",
             "from": [-486, 100, 4], "to": [-483, 100, 7]},
            {"op": "fill", "why": "a default state into a surface section "
                                  "the game already stored as compounds",
             "from": [-200, 63, 310], "to": [-198, 63, 312],
             "block": "minecraft:sand"},
            {"op": "fill", "why": "and a non-default one beside it",
             "from": [-197, 63, 310], "to": [-197, 63, 310],
             "block": "minecraft:sea_pickle[pickles=2,waterlogged=false]"},
            {"op": "connect", "from": [-487, 99, 4], "to": [-482, 101, 8]},
            {"op": "rebuild_heightmaps", "from": [-200, 0, 310], "to": [-197, 0, 312]},
            {"op": "reset_lighting", "from": [-490, 0, 5], "to": [-488, 0, 9],
             "neighbors": False},
            {"op": "light_section", "why": "not an edit but the case the "
                                           "fixture lacks: the game keeps a "
                                           "section of light alone one past "
                                           "the top of the world, Y=20, and "
                                           "it is no part of the chunk's "
                                           "range, which stays -64..319",
             "chunk": [-30, 0], "y": 20},
            {"op": "place", "why": "so a door on the top of the world, whose "
                                   "upper half would stand in that section, "
                                   "is refused whole: nothing is written",
             "at": [-478, 319, 3], "block": "minecraft:oak_door[facing=north]"},
            {"op": "replace", "why": "keys as 26.3 reads them, defaults "
                                     "filled: the bare furnace is its "
                                     "default, and a chest key that leaves "
                                     "facing out means north. The chests "
                                     "keep their block entities, the "
                                     "furnaces lose theirs",
             "from": [-206, 64, 311], "to": [-197, 64, 311],
             "mapping": [["minecraft:furnace", "minecraft:blast_furnace"],
                         ["minecraft:chest[type=left]",
                          "minecraft:chest[type=single]"]]},
            {"op": "drop_section", "why": "not an edit either: a chunk "
                                          "without the section at its floor "
                                          "still counts from the floor it "
                                          "records, yPos * 16, not from the "
                                          "lowest section it stores",
             "chunk": [-30, 0], "y": -4},
        ]
        results = []
        for step in script:
            corner1 = tuple(step["from"]) if "from" in step else None
            corner2 = tuple(step["to"]) if "to" in step else None
            op = step["op"]
            if op == "light_section":
                # Appended at the end, where the game keeps the top one, and
                # released so the next accessor is read with it.
                chunk_x, chunk_z = step["chunk"]
                dim.chunk(chunk_x, chunk_z).data["sections"].append(TagCompound({
                    "Y": TagByte(step["y"]),
                    "SkyLight": TagByteArray([-1] * 2048),
                }))
                dim.release(chunk_x, chunk_z)
                accessor = dim.blocks(chunk_x, chunk_z)
                results.append({"sections": len(accessor.sections),
                                "min_y": accessor.min_y,
                                "max_y": accessor.max_y})
            elif op == "drop_section":
                chunk_x, chunk_z = step["chunk"]
                sections = dim.chunk(chunk_x, chunk_z).data["sections"]
                (index,) = [i for i, tag in enumerate(sections)
                            if int(tag["Y"]) == step["y"]]
                del sections[index]
                dim.release(chunk_x, chunk_z)
                accessor = dim.blocks(chunk_x, chunk_z)
                results.append({"sections": len(accessor.sections),
                                "min_y": accessor.min_y,
                                "max_y": accessor.max_y})
            elif op == "fill":
                r = save.fill(corner1, corner2, step["block"], keep_loaded=True)
                results.append({
                    "blocks_filled": r.blocks_filled,
                    "sections_replaced": r.sections_replaced,
                    "chunks_modified": r.chunks_modified,
                    "blocks_out_of_range": r.blocks_out_of_range,
                    "missing_chunks": sorted(list(c) for c in r.missing_chunks),
                    "complete": r.complete,
                })
            elif op == "replace":
                results.append(_replace_report(save.replace_blocks(
                    corner1, corner2, dict(step["mapping"]), keep_loaded=True)))
            elif op == "place":
                x, y, z = step["at"]
                results.append({"placed": bool(
                    save.place_block(x, y, z, step["block"]))})
            elif op == "unfreeze":
                r = save.unfreeze(corner1, corner2, keep_loaded=True)
                results.append({
                    "snow_removed": r.snow_removed,
                    "ice_melted": r.ice_melted,
                    "snowy_cleared": r.snowy_cleared,
                    "deep_snow_kept": r.deep_snow_kept,
                    "chunks_modified": r.chunks_modified,
                    "floating_water": r.floating_water,
                    "missing_chunks": sorted(list(c) for c in r.missing_chunks),
                    "complete": r.complete,
                })
            elif op == "connect":
                r = save.connect_blocks(corner1, corner2, keep_loaded=True)
                results.append({
                    "blocks_scanned": r.blocks_scanned,
                    "fences_updated": r.fences_updated,
                    "walls_updated": r.walls_updated,
                    "panes_updated": r.panes_updated,
                    "gates_updated": r.gates_updated,
                    "stairs_updated": r.stairs_updated,
                    "total_updated": r.total_updated,
                    "complete": r.complete,
                })
            elif op == "rebuild_heightmaps":
                r = save.rebuild_heightmaps(corner1, corner2, keep_loaded=True)
                results.append({"chunks_rebuilt": r.chunks_rebuilt,
                                "complete": r.complete})
            elif op == "reset_lighting":
                r = save.reset_lighting(corner1, corner2,
                                        neighbors=step["neighbors"])
                results.append({"chunks_reset": r.chunks_reset,
                                "arrays_removed": r.arrays_removed,
                                "chunks_skipped": r.chunks_skipped})
            else:
                raise AssertionError(f"unknown op {op!r}")
        steps = [dict(step, result=result) for step, result in zip(script, results)]

        after = []
        for x, y, z in [
            (-490, -40, 5), (-488, -40, 7), (-487, -40, 5),      # source water
            (-490, -40, 9), (-489, -40, 9), (-488, -40, 9),      # flowing, pickle, stair
            (-486, 100, 5), (-485, 100, 5),                      # the fences
            (-484, 100, 5), (-484, 100, 6),                      # thawed ice
            (-200, 63, 310), (-198, 63, 312), (-197, 63, 310),   # sand, dead pickle
            (-478, 319, 3), (-478, 320, 3),                      # the refused door,
                                                                 # and light alone
            (-205, 64, 311), (-204, 64, 311),                    # blast furnaces
            (-201, 64, 311), (-200, 64, 311), (-198, 64, 311),   # chests, the right
            (-197, 64, 311),                                     # halves unnamed
        ]:
            state = save.get_block(x, y, z)
            after.append([x, y, z, None if state is None else str(state)])

        palettes_after = []
        for chunk_x, chunk_z, section_y in ((-31, 0, -3), (-31, 0, 6), (-13, 19, 3)):
            container = dim.blocks(chunk_x, chunk_z).sections[section_y].blocks
            palettes_after.append(dict(
                {"chunk": [chunk_x, chunk_z], "y": section_y},
                **_palette_report(container)))

        digests = []
        for chunk_x, chunk_z in ((-31, 0), (-13, 19)):
            chunk = dim.chunk(chunk_x, chunk_z)
            digests.append({
                "chunk": [chunk_x, chunk_z],
                "payload_sha256": sha256(encode(chunk.data, chunk.data_name)),
            })

        return {
            "version": CORPUS_VERSION,
            "modern_state_data_version": MODERN_STATE_DATA_VERSION,
            "assembly": "copy ../modern/r.-1.0.mca to dimensions/minecraft/"
                        "overworld/region/ and ../modern/level.dat to "
                        "level.dat; every chunk is DataVersion >= "
                        f"{MODERN_STATE_DATA_VERSION}, so the 26.3 codec "
                        "applies throughout",
            "note": "'chunks' is the fixture as read: each stored palette "
                    "entry's form and the state it decodes to, defaults "
                    "restored and properties in name order. 'reads' are "
                    "point reads and 'surfaces' all four heightmaps, both "
                    "before any edit; a read outside the chunk's "
                    "min_y..max_y is null even where a section of light "
                    "alone is stored. 'steps' run in order on "
                    "the same world (fills, connects and thaws with "
                    "keep_loaded); then 'blocks_after', 'palettes_after' "
                    "and the per-chunk digests of the UNCOMPRESSED NBT must "
                    "all match.",
            "chunks": chunks,
            "reads": reads,
            "surfaces": surfaces,
            "steps": steps,
            "blocks_after": after,
            "palettes_after": palettes_after,
            "block_entities_after": _block_entity_report(dim, (-13, 19)),
            "chunk_payload_sha256": digests,
        }
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _text_sha256(lines) -> str:
    """sha256 of the lines joined by newlines, as UTF-8."""
    return sha256("\n".join(lines).encode("utf-8"))


def _double(value) -> dict:
    """A double as the corpus carries one: its bits, and the number."""
    return {"bits": struct.pack(">d", float(value)).hex(), "v": float(value)}


def older_chunk_cases():
    """Chunks Minecraft 1.13 to 1.16 saved, read.

    ``fixtures/legacy/older.nbt`` is what ``tools/probe_legacy_chunks.py``
    had the game make of OldWorld's 1.15.2 overworld chunks and 1.16.5 End
    chunks, and of chunks made from a real 1.15.2 one and stamped as 1.13
    and 1.14 saved them; ``tests/test_legacy.py`` holds the reader to that
    cell by cell. What a port must agree with here is the reader itself,
    over the same chunks: the release each names, its stage and whether
    that is finished (``postprocessed`` or ``fullchunk`` before 1905,
    ``fullchunk`` until 1911, ``full`` after), each section's palette and
    the bits a cell it is read at -- indices run on across longs before
    DataVersion 2527 and are repacked once -- a digest of every block and
    every biome cell (a biome a column before 2202, each 4x4x4 cell taking
    its column at (2, 2) in), every heightmap's 256 columns (run on before
    2527 too), and the entities a chunk before 2681 keeps in its Level.
    """
    from heaton_voxel.blocks import HEIGHTMAP_NAMES, Blocks, legacy_release

    with open(os.path.join(_HERE, "legacy", "older.nbt"), "rb") as handle:
        _, root = nbt.decode(gzip.decompress(handle.read()))
    regions, chunks = {}, []
    for index, row in enumerate(root["cases"]):
        if "chunk" in row:
            data, where = row["chunk"], {"older_nbt_case": index}
        else:
            source = str(row["source"])
            if source not in regions:
                regions[source] = nbt.NBT.load(os.path.join(_HERE, "legacy", *source.split("/")))
            key = tuple(int(v) for v in row["key"])
            data, where = regions[source].chunks[key].data, {"region": source, "key": list(key)}
        accessor = Blocks(data)
        sections = []
        for y in range(16):
            blocks = accessor.sections[y].blocks
            sections.append({"y": y, "palette": [str(s) for s in blocks.palette],
                             "bits": blocks.bits})
        surfaces = {}
        for name in HEIGHTMAP_NAMES:
            column = [accessor.surface_y(x, z, name) for z in range(16) for x in range(16)]
            if any(v is not None for v in column):
                surfaces[name] = _text_sha256(str(v) for v in column)
        entities = accessor.legacy_entities
        chunks.append({
            "name": str(row["name"]),
            "input": where,
            "data_version": accessor.data_version,
            "release": legacy_release(accessor.data_version),
            "status": accessor.status,
            "finished": accessor.finished,
            "chunk": [accessor.world_x >> 4, accessor.world_z >> 4],
            "sections": sections,
            "blocks_sha256": _text_sha256(
                str(accessor.get(x, y, z)) for y in range(0, 256)
                for z in range(16) for x in range(16)),
            "biomes_sha256": _text_sha256(
                accessor.get_biome(x, y, z) for y in range(0, 256, 4)
                for z in range(0, 16, 4) for x in range(0, 16, 4)),
            "surfaces_sha256": surfaces,
            "legacy_entities": None if entities is None else [
                {"id": str(e.get("id", "")), "pos": [_double(v) for v in e["Pos"]]}
                for e in entities],
        })
    return {
        "version": CORPUS_VERSION,
        "chunks": chunks,
        "assembly": "a chunk with 'region' is the one at local 'key' in "
                    "../legacy/<region>; one with 'older_nbt_case' is "
                    "cases[n].chunk of ../legacy/older.nbt, a gzipped NBT file",
        "note": "'blocks_sha256' is every cell y 0..255, z and x 0..15 with x "
                "fastest, as a state string; 'biomes_sha256' every 4th y, z and "
                "x the same way; each 'surfaces_sha256' a heightmap's 256 "
                "surface_y answers, x fastest, 'None' for none, for every map "
                "the chunk stores",
    }


def old_layout_cases():
    """Saves in the layout before Minecraft 26.1, read where they stand.

    ``fixtures/old_layout.json`` is five such worlds and what the game's
    own ``FileFixerUpper`` made of each (``tools/probe_old_layout.py``);
    ``tests/test_old_layout.py`` holds every answer here to the game's. A
    port opens each world as it stood -- its overworld in ``region/``, the
    nether in ``DIM-1/region``, the End in ``DIM1/region`` -- and must give
    the same answers: its dimensions and region files; game mode, cheats
    and the difficulty (``LevelDatDifficultyFix``'s names: 0 peaceful, 1
    easy, 3 hard, anything else normal); weather from ``level.dat``'s own
    fields; the spawn from ``SpawnX``/``SpawnY``/``SpawnZ`` or the ``spawn``
    compound; one clock, the overworld's ``DayTime``; one border for every
    dimension, flat ``Border*`` keys or a ``world_border`` compound, the
    nether's centered an eighth as far out; and the player
    ``playerdata/<uuid>.dat`` of the one ``level.dat`` holds, else
    ``level.dat``'s own, else the first of ``playerdata/``, its dimension a
    number read as ``LegacyDimensionIdFix`` reads one. Then every write is
    refused, and nothing on disk moves.
    """
    from heaton_voxel.world import END, NETHER, OVERWORLD, MinecraftSave, ReadOnlySave

    with open(os.path.join(_HERE, "old_layout.json"), encoding="ascii") as handle:
        recorded = json.load(handle)

    def answer(call, shape):
        try:
            return shape(call())
        except nbt.NBTError:
            return "refused"

    def border(found):
        return {
            "center_x": _double(found.center_x), "center_z": _double(found.center_z),
            "size": _double(found.size), "safe_zone": _double(found.safe_zone),
            "damage_per_block": _double(found.damage_per_block),
            "warning_blocks": found.warning_blocks, "warning_time": found.warning_time,
            "lerp_target": _double(found.lerp_target), "lerp_time": str(found.lerp_time),
            "stored": found.stored,
        }

    worlds = []
    for index, case in enumerate(recorded["cases"]):
        directory = tempfile.mkdtemp(prefix="conformance-old-layout-")
        try:
            for relative in case["before"]["dirs"]:
                os.makedirs(os.path.join(directory, *relative.split("/")), exist_ok=True)
            for relative, content in case["before"]["files"].items():
                path = os.path.join(directory, *relative.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if isinstance(content, dict):
                    shutil.copyfile(os.path.join(_HERE, *content["fixture"].split("/")), path)
                else:
                    with open(path, "wb") as handle:
                        handle.write(base64.b64decode(content))
            save = MinecraftSave.load(directory)
            reads = {
                "layout": save.layout,
                "read_only": save.read_only is not None,
                "dimensions": {name: [list(key) for key in sorted(dim.available)]
                               for name, dim in sorted(save.dimensions.items())},
                "game_type": answer(save.game_type, str),
                "allow_commands": answer(save.allow_commands, bool),
                "difficulty": answer(save.difficulty, str),
                "hardcore": answer(save.hardcore, bool),
                "difficulty_locked": answer(save.difficulty_locked, bool),
                "weather": answer(save.weather, lambda w: {
                    "raining": w.raining, "thundering": w.thundering,
                    "rain_time": w.rain_time, "thunder_time": w.thunder_time,
                    "clear_weather_time": w.clear_weather_time}),
                "spawn": answer(save.spawn_position, list),
                "day_time": {name: answer(lambda: save.day_time(name), str)
                             for name in (OVERWORLD, NETHER, END)},
                "border": {name: answer(lambda: save.world_border(name), border)
                           for name in (OVERWORLD, NETHER, END)},
                "player": answer(lambda: (save.player_position(), save.player_rotation(),
                                          save.player_dimension()),
                                 lambda p: {"pos": [_double(v) for v in p[0]],
                                            "rotation": [_double(v) for v in p[1]],
                                            "dimension": p[2]}),
            }
            refused = []
            for name, write in (("save", save.save),
                                ("set_game_type", lambda: save.set_game_type("creative")),
                                ("set_weather", lambda: save.set_weather("clear")),
                                ("set_day_time", lambda: save.set_day_time("noon")),
                                ("set_spawn", lambda: save.set_spawn_position(0, 90, 0)),
                                ("create_chunks",
                                 lambda: save.create_chunks((640, 0, 640), (640, 0, 640)))):
                try:
                    write()
                except ReadOnlySave:
                    refused.append(name)
            reads["refused_writes"] = refused
            worlds.append({"name": case["name"], "case": index, "reads": reads})
        finally:
            shutil.rmtree(directory, ignore_errors=True)
    return {
        "version": CORPUS_VERSION,
        "worlds": worlds,
        "assembly": "world n is cases[n].before of ../old_layout.json: make "
                    "every directory in 'dirs', write every file in 'files' -- a "
                    "string is its bytes in base64, {fixture} a file of ../ copied "
                    "there -- and open it",
        "note": "'refused' is a reader that refused; 'refused_writes' the "
                "writes, run in order, each refused as read-only; 64-bit tick "
                "counts are decimal strings",
    }


def legacy_cases():
    """Real 1.17.1 output, read, and every write to it refused.

    ``fixtures/legacy/`` is sixteen chunks Minecraft 1.17.1 saved
    (DataVersion 2730), which the library reads and never writes. The
    claims a port must hold: the 256-entry table naming each 1.17 biome
    number (the game's own, by ``tools/probe_legacy_chunks.py``); each
    chunk's shape -- y 0..255 whatever it stores, every section of the
    world there, the ones 1.17 left out as one entry of air, sections of
    light alone outside the world -- with each section's palette as stored
    and its biomes as named; a digest of every block and every biome cell,
    which is where reading the wrong index or the wrong biome number is
    caught; point reads, light reads and every surface map; and then a
    script of every world write the library has, each refused as it would
    refuse a chunk it cannot use, after which ``save`` leaves the region
    file exactly the fixture's bytes -- the one file digest here that is
    portable, because nothing was recompressed.
    """
    from heaton_voxel.blocks import (HEIGHTMAP_NAMES, LEGACY_MAX_DATA_VERSION,
                                     LEGACY_MIN_DATA_VERSION, MIN_DATA_VERSION,
                                     ReadOnlyChunk)
    from heaton_voxel.legacy_biomes import LEGACY_BIOMES
    from heaton_voxel.schem import Schematic
    from heaton_voxel.world import OVERWORLD, MinecraftSave

    source = os.path.join(_HERE, "legacy", "r.5.-1.mca")
    directory = tempfile.mkdtemp(prefix="conformance-legacy-")
    try:
        region_dir = os.path.join(
            directory, "dimensions", "minecraft", "overworld", "region")
        os.makedirs(region_dir)
        region_path = os.path.join(region_dir, "r.5.-1.mca")
        shutil.copyfile(source, region_path)
        shutil.copyfile(os.path.join(_HERE, "legacy", "level.dat"),
                        os.path.join(directory, "level.dat"))
        save = MinecraftSave.load(directory, dimensions=[OVERWORLD])
        dim = save.dimension(OVERWORLD)
        region = dim.region(5, -1)
        keys = sorted(region.chunks)
        origin = [(region.region_x * 32 + lx, region.region_z * 32 + lz)
                  for lx, lz in keys]

        # 1. Each chunk's shape, and digests of everything it reads as.
        chunks = []
        for chunk_x, chunk_z in origin:
            accessor = dim.blocks(chunk_x, chunk_z)
            stored = {int(tag["Y"]): tag
                      for tag in dim.chunk(chunk_x, chunk_z).data["Level"]["Sections"]}
            sections = []
            for section_y in sorted(accessor.sections):
                section = accessor.sections[section_y]
                entry = {"y": section_y}
                if section.blocks is None:
                    entry["blocks"] = "absent"
                else:
                    held = stored.get(section_y)
                    entry["blocks"] = ("stored" if held is not None
                                       and "Palette" in held else "air")
                    entry["palette"] = [str(s) for s in section.blocks.palette]
                entry["biomes"] = (None if section.biomes is None
                                   else list(section.biomes.palette))
                entry["light"] = [name for name in ("BlockLight", "SkyLight")
                                  if section_y in stored and name in stored[section_y]]
                sections.append(entry)
            blocks = (str(accessor.get(x, y, z))
                      for y in range(accessor.min_y, accessor.max_y + 1)
                      for z in range(16) for x in range(16))
            biomes = (accessor.get_biome(x, y, z)
                      for y in range(accessor.min_y, accessor.max_y + 1, 4)
                      for z in range(0, 16, 4) for x in range(0, 16, 4))
            chunks.append({
                "chunk": [chunk_x, chunk_z],
                "data_version": accessor.data_version,
                "legacy": accessor.legacy,
                "status": accessor.status,
                "min_y": accessor.min_y,
                "max_y": accessor.max_y,
                "sections": sections,
                "blocks_sha256": _text_sha256(blocks),
                "biomes_sha256": _text_sha256(biomes),
            })

        # 2. Point reads, biome reads, light reads and every surface map.
        # y=-1 and y=256 are one past the world: nothing, even where a
        # section of light alone is stored at Y=-1.
        reads, biome_reads, light_reads, surfaces = [], [], [], []
        for chunk_x, chunk_z in origin:
            ox, oz = chunk_x * 16, chunk_z * 16
            for y in (-1, 0, 5, 40, 63, 64, 70, 100, 200, 255, 256):
                for dx, dz in ((1, 1), (8, 8), (14, 3)):
                    state = save.get_block(ox + dx, y, oz + dz)
                    reads.append([ox + dx, y, oz + dz,
                                  None if state is None else str(state)])
            for y in (-1, 0, 64, 255, 256):
                biome_reads.append([ox + 5, y, oz + 9,
                                    save.get_biome(ox + 5, y, oz + 9)])
            accessor = dim.blocks(chunk_x, chunk_z)
            for y in (-16, -1, 0, 70, 120):
                try:
                    block = accessor.block_light(3, y, 12)
                    sky = accessor.sky_light(3, y, 12)
                except IndexError:  # no section stored there at all
                    block = sky = "outside"
                light_reads.append({
                    "chunk": [chunk_x, chunk_z], "x": 3, "y": y, "z": 12,
                    "block": block, "sky": sky,
                })
            for dx in (0, 5, 11):
                column = {"x": ox + dx, "z": oz + dx}
                for name in HEIGHTMAP_NAMES:
                    column[name] = save.surface_y(ox + dx, oz + dx, name)
                surfaces.append(column)

        # 3. Every write, refused. Chunk (170, -29) is x 2720..2735,
        # z -464..-449; its surface at (2725, -460) is where the single
        # cells go.
        x, z = 2725, -460
        top = save.surface_y(x, z, "WORLD_SURFACE")
        box = ([2720, 60, -464], [2735, 70, -449])
        script = [
            {"op": "set_block", "at": [x, top + 1, z], "block": "minecraft:stone"},
            {"op": "place", "at": [x, top + 1, z], "block": "minecraft:oak_fence"},
            {"op": "set_biome", "at": [x, 64, z], "biome": "minecraft:desert"},
            {"op": "fill", "from": box[0], "to": box[1], "block": "minecraft:stone"},
            {"op": "fill_biome", "from": box[0], "to": box[1], "biome": "minecraft:desert"},
            {"op": "unfreeze", "from": box[0], "to": box[1]},
            {"op": "connect", "from": box[0], "to": box[1]},
            {"op": "reset_lighting", "from": box[0], "to": box[1], "neighbors": False},
            {"op": "rebuild_heightmaps", "from": box[0], "to": box[1]},
            {"op": "spawn_entity", "id": "minecraft:pig",
             "at": [x + 0.5, top + 1.0, z + 0.5]},
            {"op": "export_schem", "why": "with allow_incomplete: exported as "
                                          "air and reported, like a chunk "
                                          "that cannot be read",
             "from": [2722, 60, -462], "to": [2724, 62, -460]},
            {"op": "import_schem", "why": "four stone in a 2x2x2 schematic",
             "at": [2725, 80, -460],
             "cells": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 1]],
             "block": "minecraft:stone"},
        ]
        results = []
        for step in script:
            op = step["op"]
            corner1 = tuple(step["from"]) if "from" in step else None
            corner2 = tuple(step["to"]) if "to" in step else None
            if op == "set_block":
                results.append({"written": save.set_block(*step["at"], step["block"])})
            elif op == "place":
                results.append({"placed": save.place_block(*step["at"], step["block"])})
            elif op == "set_biome":
                results.append({"written": save.set_biome(*step["at"], step["biome"])})
            elif op == "spawn_entity":
                try:
                    save.spawn_entity(step["id"], *step["at"])
                except ReadOnlyChunk:
                    results.append({"refused": True})
                else:  # pragma: no cover - the corpus would be wrong
                    raise AssertionError("spawn_entity wrote into a 1.17 chunk")
            else:
                if op == "fill":
                    r = save.fill(corner1, corner2, step["block"])
                elif op == "fill_biome":
                    r = save.fill_biome(corner1, corner2, step["biome"])
                elif op == "unfreeze":
                    r = save.unfreeze(corner1, corner2)
                elif op == "connect":
                    r = save.connect_blocks(corner1, corner2)
                elif op == "reset_lighting":
                    r = save.reset_lighting(corner1, corner2,
                                            neighbors=step["neighbors"])
                elif op == "rebuild_heightmaps":
                    r = save.rebuild_heightmaps(corner1, corner2)
                elif op == "export_schem":
                    r = save.export_schem(
                        corner1, corner2, os.path.join(directory, "out.schem"),
                        allow_incomplete=True)
                elif op == "import_schem":
                    paste = Schematic(2, 2, 2, MIN_DATA_VERSION)
                    for cell in step["cells"]:
                        paste.set_block(*cell, step["block"])
                    r = save.import_schem(paste, tuple(step["at"]))
                else:
                    raise AssertionError(f"unknown op {op!r}")
                refused = sorted([list(c) for c, _ in r.unreadable_chunks])
                for _, reason in r.unreadable_chunks:
                    assert "Minecraft 1.17" in reason, reason
                results.append({"unreadable_chunks": refused})
        steps = [dict(step, result=result) for step, result in zip(script, results)]
        save.save()
        with open(region_path, "rb") as handle:
            after = handle.read()
        with open(source, "rb") as handle:
            assert after == handle.read(), "a refused write changed the region"

        return {
            "version": CORPUS_VERSION,
            "legacy_data_versions": [LEGACY_MIN_DATA_VERSION, LEGACY_MAX_DATA_VERSION],
            "legacy_biomes": list(LEGACY_BIOMES),
            "assembly": "copy ../legacy/r.5.-1.mca to dimensions/minecraft/"
                        "overworld/region/ and ../legacy/level.dat to "
                        "level.dat; every chunk is DataVersion 2730",
            "note": "'chunks' is each chunk's shape as read: its range, every "
                    "section in Y order with whether its blocks are stored, "
                    "air (1.17 left the section out) or absent (light alone, "
                    "outside the world), the palette as states, the biome "
                    "palette, the light arrays stored; 'blocks_sha256' digests "
                    "every block's text from min_y to max_y in y, z, x order, "
                    "and 'biomes_sha256' every 4x4x4 cell's biome the same "
                    "way, one per line. 'reads', 'biome_reads', 'light_reads' "
                    "and 'surfaces' are taken before any write. 'steps' run "
                    "in order; every one is refused -- a false return, a "
                    "ReadOnlyChunk, or the chunk named among the result's "
                    "unreadable_chunks with a reason naming Minecraft 1.17 "
                    "-- and after a save the region file is byte for byte "
                    "'region_sha256', the fixture's own.",
            "chunks": chunks,
            "reads": reads,
            "biome_reads": biome_reads,
            "light_reads": light_reads,
            "surfaces": surfaces,
            "steps": steps,
            "region_sha256": sha256(after),
        }
    finally:
        shutil.rmtree(directory, ignore_errors=True)


# --------------------------------------------------------------------------
# Deleting chunks: the one write whose whole file is portable
# --------------------------------------------------------------------------

#: The vendored fixture's own DataVersion (region.json repeats it per chunk),
#: so the entity and POI files below are stamped like the terrain they
#: accompany.
_FIXTURE_DATA_VERSION = 4903

#: The region the fixture is, and the only one these vectors touch.
_DELETE_REGION = (-1, 0)
_FAR_DELETE_REGION = (-1, -1)


def _sidecar_region(chunks, region_at=_DELETE_REGION):
    """An in-memory Anvil file holding ``{(chunk_x, chunk_z): (ts, root)}``.

    Written with compression scheme 3 -- *none*, a real Anvil scheme the
    game accepts -- because these two files are checked into the corpus and
    the drift test compares them byte for byte. Compressed, their bytes
    would be a property of whichever zlib built them (see PORTING.md) and
    could only ever be verified semantically, the way the .schem goldens
    are. Uncompressed, they are a function of the NBT and nothing else.
    """
    region = NBT()
    region.kind = "region"
    region.region_x, region.region_z = region_at
    for (chunk_x, chunk_z), (timestamp, root) in sorted(chunks.items()):
        key = (chunk_x & 31, chunk_z & 31)
        chunk = nbt.Chunk(key[0], key[1], timestamp=timestamp,
                          compression=nbt.COMPRESSION_NONE)
        chunk.data = root
        region.chunks[key] = chunk
    return region


def _entity_root(chunk_x, chunk_z, entities):
    """One ``entities/`` chunk: the shape ``EntityStorage`` reads.

    The UUIDs are written by hand rather than generated, for the reason
    ``world_edits.json`` spawns nothing at all: a fresh random UUID is a
    byte no two runs share, and these files are digested.
    """
    return TagCompound({
        "Position": TagIntArray([chunk_x, chunk_z]),
        "DataVersion": TagInt(_FIXTURE_DATA_VERSION),
        "Entities": TagList([
            TagCompound({
                "id": TagString(identity),
                "Pos": TagList([TagDouble(v) for v in pos],
                               element_type=nbt.TAG_DOUBLE),
                "Motion": TagList([TagDouble(0.0)] * 3,
                                  element_type=nbt.TAG_DOUBLE),
                "Rotation": TagList([TagFloat(0.0), TagFloat(0.0)],
                                    element_type=nbt.TAG_FLOAT),
                "UUID": TagIntArray(list(uuid)),
            })
            for identity, pos, uuid in entities
        ], element_type=nbt.TAG_COMPOUND),
    })


def _poi_root(chunk_x, chunk_z, section_y, records):
    """One ``poi/`` chunk: a section of claimed points, marked ``Valid``.

    ``Valid: 1`` is the flag that makes this data outlive the terrain:
    ``PoiSection.refresh`` returns early on it, so the game never rescans
    the chunk and a villager goes on holding a claim to a bed that a
    regenerated world does not contain.
    """
    return TagCompound({
        "DataVersion": TagInt(_FIXTURE_DATA_VERSION),
        "Sections": TagCompound({
            str(section_y): TagCompound({
                "Valid": TagByte(1),
                "Records": TagList([
                    TagCompound({
                        "pos": TagIntArray(list(pos)),
                        "type": TagString(kind),
                        "free_tickets": TagInt(free_tickets),
                    })
                    for pos, kind, free_tickets in records
                ], element_type=nbt.TAG_COMPOUND),
            }),
        }),
    })


def _delete_inputs():
    """The two sidecar trees, as ``(entities NBT, poi NBT)``.

    Between them they cover the three cases that matter: a chunk with
    entities *and* terrain, a chunk with entities and **no terrain at all**
    (deleting the other trees cannot be conditional on the terrain chunk
    existing), and a chunk of each kind outside the box, which has to
    survive byte for byte.
    """
    entities = _sidecar_region({
        (-25, 1): (1784414043, _entity_root(-25, 1, [
            ("minecraft:villager", (-396.5, 72.0, 20.5), [1, 2, 3, 4]),
            ("minecraft:cat", (-395.5, 72.0, 21.5), [5, 6, 7, 8]),
        ])),
        (-23, 2): (1784414099, _entity_root(-23, 2, [
            ("minecraft:armor_stand", (-360.5, 70.0, 40.5), [9, 10, 11, 12]),
        ])),
        (-28, 14): (1784414047, _entity_root(-28, 14, [
            ("minecraft:cow", (-440.5, 80.0, 230.5), [13, 14, 15, 16]),
        ])),
    })
    poi = _sidecar_region({
        (-25, 2): (1784414043, _poi_root(-25, 2, 4, [
            ((-396, 68, 40), "minecraft:home", 0),
        ])),
        (-24, 1): (1784414041, _poi_root(-24, 1, 4, [
            ((-380, 68, 20), "minecraft:home", 1),
            ((-379, 68, 22), "minecraft:armorer", 1),
        ])),
        (-28, 14): (1784414047, _poi_root(-28, 14, 5, [
            ((-440, 84, 230), "minecraft:home", 0),
        ])),
    })
    # A second entities file, one region up-left, holding a single chunk
    # at NEGATIVE z. Everything above lives in region (-1, 0) with wholly
    # positive z, which leaves two things untested: a box spanning two region
    # files at once, and `chunk_z & 31` -- where C#'s natural `% 32` gives
    # -1 for z = -1 and indexes off the end of the table. One chunk, so the
    # same delete also empties a file outright.
    far = _sidecar_region({
        (-25, -1): (1784414101, _entity_root(-25, -1, [
            ("minecraft:sheep", (-396.5, 74.0, -8.5), [17, 18, 19, 20]),
        ])),
    }, region_at=_FAR_DELETE_REGION)
    return entities, poi, far


def _world_chunk(region, chunk):
    """A chunk's world coordinates, from its place in the region."""
    return (region.region_x * 32 + chunk.local_x,
            region.region_z * 32 + chunk.local_z)


def _index_of(chunk):
    """Where a chunk's entry sits in the region's header tables."""
    return chunk.index


def _sidecar_summary(region, raw, path, holds):
    """What one input file contains, so a port can check it loaded it."""
    return {
        "file": path,
        "sha256": sha256(raw),
        "bytes": len(raw),
        "compression": "none (scheme 3), so these bytes are portable",
        "chunks": [
            {
                "chunk": list(_world_chunk(region, chunk)),
                "timestamp": chunk.timestamp,
                "payload_sha256": sha256(encode(chunk.data, chunk.data_name)),
                "holds": holds[_world_chunk(region, chunk)],
            }
            for chunk in sorted(region.chunks.values(), key=_index_of)
        ],
    }


def _region_delete_report(path, before, after, region, cleared):
    """One region file's before and after, told in bytes.

    ``cleared`` is the world chunk coordinates removed from it; everything
    still in ``region`` is a survivor, and has to be byte-identical sector
    for sector.
    """
    def sector_span(data, offset, count):
        return data[offset * nbt.SECTOR:(offset + count) * nbt.SECTOR]

    def timestamp(data, index):
        start = nbt.SECTOR + index * 4
        return struct.unpack(">I", data[start:start + 4])[0]

    locations = (before[:nbt.SECTOR], after[:nbt.SECTOR])
    timestamps = (before[nbt.SECTOR:2 * nbt.SECTOR],
                  after[nbt.SECTOR:2 * nbt.SECTOR])

    entries = []
    for chunk_x, chunk_z in sorted(
            cleared, key=lambda c: (c[0] & 31) + (c[1] & 31) * 32):
        index = (chunk_x & 31) + (chunk_z & 31) * 32
        location = before[index * 4:index * 4 + 4]
        offset = int.from_bytes(location[:3], "big")
        count = location[3]
        freed = sector_span(after, offset, count)
        entries.append({
            "chunk": [chunk_x, chunk_z],
            "index": index,
            "location_before": location.hex(),
            "location_after": after[index * 4:index * 4 + 4].hex(),
            "sectors_before": [offset, count],
            "freed_sectors_zeroed": freed == bytes(len(freed)),
            "timestamp_before": timestamp(before, index),
            "timestamp_after": timestamp(after, index),
        })

    survivors = []
    for chunk in sorted(region.chunks.values(), key=_index_of):
        offset, count = chunk._sector_offset, chunk._sector_count
        survivors.append({
            "chunk": list(_world_chunk(region, chunk)),
            "index": chunk.index,
            "sectors": [offset, count],
            "record_sha256": sha256(sector_span(after, offset, count)),
            "record_unmoved": (sector_span(before, offset, count)
                               == sector_span(after, offset, count)),
            "payload_sha256": sha256(encode(chunk.data, chunk.data_name)),
        })

    return {
        "file": path,
        "sha256_before": sha256(before),
        "sha256_after": sha256(after),
        "bytes_before": len(before),
        "bytes_after": len(after),
        "length_unchanged": len(before) == len(after),
        "location_table_sha256": {"before": sha256(locations[0]),
                                  "after": sha256(locations[1])},
        "timestamp_table_sha256": {"before": sha256(timestamps[0]),
                                   "after": sha256(timestamps[1])},
        "timestamp_table_unchanged": timestamps[0] == timestamps[1],
        "cleared": entries,
        "survivors": survivors,
    }


def chunk_delete_case():
    """Deleting chunks, checked on the region files themselves.

    Returns ``(expectations, entities bytes, poi bytes)``.

    Every other write in this corpus is checked through chunk NBT, and a
    deleted chunk has no NBT left to check: its whole effect is in the
    container around it -- a location entry cleared, sectors freed, the
    file's length and every other chunk's bytes untouched. So this vector
    digests the **files**, which it can do because deletion is also the one
    write here that recompresses nothing: survivors are re-emitted from the
    bytes they were loaded with, so the resulting file is the same in any
    language (see PORTING.md on compression).

    What each vector is built to catch travels with the artifact, under
    ``catches``, because the port reads the corpus and not this file. Two
    of them shape the script rather than the expectations: ``reads_before``
    is there to leave a decoded accessor behind for every chunk about to
    go, since a stale cache is invisible unless something read through it
    first; and ``save()`` is actually called, because a port that consults
    ``entity_available`` without opening the file computes every count here
    correctly and writes nothing.
    """
    # Corners deliberately given the wrong way round, with two unrelated y
    # values: order does not matter and y is not part of the question.
    box = ([-360, 200, 40], [-395, 5, 20])
    # Chunks -25..-23 x, 1..2 z. Terrain exists for five of the six; the
    # entity chunk at (-23, 2) sits under the sixth, which does not exist.
    watched = [(-25, 1), (-24, 2), (-23, 1), (-28, 14)]

    entities, poi, far = _delete_inputs()
    entity_bytes, poi_bytes = entities.to_bytes(), poi.to_bytes()
    far_bytes = far.to_bytes()
    entity_holds = {(-25, 1): "a villager and a cat",
                    (-23, 2): "an armor stand, over ground that was never "
                              "generated",
                    (-28, 14): "a cow, outside the box"}
    poi_holds = {(-25, 2): "a claimed bed",
                 (-24, 1): "a claimed bed and a claimed workstation",
                 (-28, 14): "a claimed bed, outside the box"}
    with open(os.path.join(_HERE, "r.-1.0.mca"), "rb") as handle:
        vendored = handle.read()
    inputs = {
        "region": {
            "file": "../r.-1.0.mca",
            "sha256": sha256(vendored),
            "why": "the same vendored region region.json describes, so the "
                   "survivors' payload digests are the ones it already lists",
        },
        "entities": _sidecar_summary(
            entities, entity_bytes, "chunk_deletes/entities/r.-1.0.mca",
            entity_holds),
        "poi": _sidecar_summary(
            poi, poi_bytes, "chunk_deletes/poi/r.-1.0.mca", poi_holds),
        "entities_far": _sidecar_summary(
            far, far_bytes, "chunk_deletes/entities/r.-1.-1.mca",
            {(-25, -1): "a sheep, at negative z, alone in its region file"}),
    }

    directory = tempfile.mkdtemp(prefix="conformance-deletes-")
    try:
        dimension_dir = os.path.join(
            directory, "dimensions", "minecraft", "overworld")
        for tree in ("region", "entities", "poi"):
            os.makedirs(os.path.join(dimension_dir, tree))
        region_path = os.path.join(dimension_dir, "region", "r.-1.0.mca")
        shutil.copyfile(os.path.join(_HERE, "r.-1.0.mca"), region_path)
        entity_path = os.path.join(dimension_dir, "entities", "r.-1.0.mca")
        poi_path = os.path.join(dimension_dir, "poi", "r.-1.0.mca")
        with open(entity_path, "wb") as handle:
            handle.write(entity_bytes)
        with open(poi_path, "wb") as handle:
            handle.write(poi_bytes)
        far_path = os.path.join(dimension_dir, "entities", "r.-1.-1.mca")
        with open(far_path, "wb") as handle:
            handle.write(far_bytes)
        shutil.copyfile(os.path.join(_HERE, "level.dat"),
                        os.path.join(directory, "level.dat"))
        from heaton_voxel.world import OVERWORLD, MinecraftSave

        save = MinecraftSave.load(directory, dimensions=[OVERWORLD])
        dim = save.dimension(OVERWORLD)
        with open(region_path, "rb") as handle:
            region_before = handle.read()

        def read_columns():
            return [
                {"chunk": [chunk_x, chunk_z],
                 "status": dim.chunk_status(chunk_x, chunk_z),
                 "x": chunk_x * 16 + 3, "z": chunk_z * 16 + 3,
                 "surface_y": save.surface_y(chunk_x * 16 + 3,
                                             chunk_z * 16 + 3)}
                for chunk_x, chunk_z in watched
            ]

        reads_before = read_columns()

        script = [
            {"op": "delete_chunks", "from": [-400, 0, -16], "to": [-390, 0, 24],
             "why": "spans two REGION files at once -- chunks (-25, -1) and "
                    "(-25, 1) sit either side of the r.-1.-1 | r.-1.0 line -- "
                    "and reaches negative z, which nothing else here does. A "
                    "port taking the region once from the box corner sends "
                    "every chunk at the wrong file; one writing "
                    "`chunk_z % 32` instead of `& 31` gets -1 for z = -1 and "
                    "indexes off the table. It also empties r.-1.-1.mca "
                    "outright, so a port that unlinks or forgets an emptied "
                    "region is caught here too"},
            {"op": "delete_chunks", "from": box[0], "to": box[1],
             "why": "corners in any order, y ignored; the terrain the step "
                    "above did not already take, one chunk of the six never "
                    "generated, and the sidecar trees cleared on their own "
                    "account"},
            {"op": "delete_chunks", "from": [-400, 0, -16], "to": [-390, 0, 8],
             "why": "spans two REGION files at once -- chunks z -1 and 0 sit "
                    "either side of the r.-1.-1 | r.-1.0 line -- and reaches "
                    "negative z, which nothing else here does. A port taking "
                    "the region once from the box corner sends every chunk at "
                    "the wrong file; one writing `chunk_z % 32` instead of "
                    "`& 31` gets -1 for z = -1 and indexes off the table. It "
                    "also empties r.-1.-1.mca outright, so a port that "
                    "unlinks or forgets an emptied region is caught here too"},
            {"op": "delete_chunks", "from": [5, 0, 5], "to": [20, 0, 20],
             "why": "a region the world has never written: four absent "
                    "chunks, nothing touched, and no file conjured into "
                    "existence"},
            {"op": "delete_chunks", "from": box[0], "to": box[1],
             "why": "again, over the same box: all six absent now, which is "
                    "what proves the first pass emptied the containers "
                    "rather than only reporting that it had"},
        ]
        results = []
        for step in script:
            result = save.delete_chunks(tuple(step["from"]), tuple(step["to"]))
            results.append({
                "chunks_deleted": result.chunks_deleted,
                "chunks_absent": result.chunks_absent,
                "entity_chunks_deleted": result.entity_chunks_deleted,
                "poi_chunks_deleted": result.poi_chunks_deleted,
                "regions_touched": result.regions_touched,
                "deleted_box": [list(result.deleted_box[0]),
                                list(result.deleted_box[1])],
            })
        steps = [dict(step, result=result)
                 for step, result in zip(script, results)]

        reads_after = read_columns()
        untouched_region = {
            "why": "a delete over a region with no files must not create "
                   "any; both of these are still absent afterwards",
            "entity_region_0_0": dim.entity_region(0, 0) is None,
            "poi_region_0_0": dim.poi_region(0, 0) is None,
        }

        region_after = dim.region(*_DELETE_REGION).to_bytes()
        entities_after = dim.entity_region(*_DELETE_REGION).to_bytes()
        poi_after = dim.poi_region(*_DELETE_REGION).to_bytes()
        files = {
            "region": _region_delete_report(
                "dimensions/minecraft/overworld/region/r.-1.0.mca",
                region_before, region_after, dim.region(*_DELETE_REGION),
                [(-25, 1), (-24, 1), (-23, 1), (-25, 2), (-24, 2)]),
            "entities": _region_delete_report(
                "dimensions/minecraft/overworld/entities/r.-1.0.mca",
                entity_bytes, entities_after,
                dim.entity_region(*_DELETE_REGION),
                [(-25, 1), (-23, 2)]),
            "poi": _region_delete_report(
                "dimensions/minecraft/overworld/poi/r.-1.0.mca",
                poi_bytes, poi_after, dim.poi_region(*_DELETE_REGION),
                [(-25, 2), (-24, 1)]),
        }

        written = save.save()
        on_disk = {}
        for name, path, expected in (("region", region_path, region_after),
                                     ("entities", entity_path, entities_after),
                                     ("poi", poi_path, poi_after)):
            with open(path, "rb") as handle:
                on_disk[name] = handle.read() == expected

        return {
            "version": CORPUS_VERSION,
            "why": "every other write in this corpus is checked through chunk "
                   "NBT, and a deleted chunk has none left to check: its "
                   "whole effect is the container around it. So this vector digests "
                   "the files themselves, which it can do because deleting is "
                   "also the one write here that recompresses nothing.",
            "catches": [
                "a port that stamps the timestamp table the way vanilla's "
                "RegionFile.clear does: nothing reads the timestamp of a "
                "chunk that is not there, and stamping the current second "
                "would make the file unreproducible",
                "a port that clears terrain and stops. Entity and POI storage "
                "are separate files that nothing rebuilds from the terrain, "
                "so what is left of them comes back in regenerated ground -- "
                "and the entity chunk at (-23, 2) has no terrain chunk at "
                "all, so clearing the sidecars only where terrain existed "
                "misses it",
                "a port that reads entity_available/poi_available instead of "
                "opening the files: it reports every count here correctly and "
                "writes nothing, because save() writes only opened regions",
                "a port that drops the container entry but leaves a cached "
                "chunk accessor standing, which goes on answering "
                "minecraft:full for ground that is gone",
                "a port dividing block coordinates by 16 instead of shifting: "
                "every x here is negative, and truncation lands the box a "
                "chunk to the east",
            ],
            "assembly": "as world.json -- copy ../r.-1.0.mca to dimensions/"
                        "minecraft/overworld/region/ and ../level.dat to "
                        "level.dat -- and additionally copy chunk_deletes/"
                        "entities/r.-1.0.mca to dimensions/minecraft/"
                        "overworld/entities/ and chunk_deletes/poi/"
                        "r.-1.0.mca to dimensions/minecraft/overworld/poi/, "
                        "then run every step below in order",
            "note": "the reads before the script are part of it: they leave a "
                    "decoded accessor behind for each chunk, which is what "
                    "makes reads_after able to tell a deleted chunk from a "
                    "cached one. Digests here are of whole FILES, which is "
                    "portable only because deleting recompresses nothing.",
            "inputs": inputs,
            "reads_before": reads_before,
            "steps": steps,
            "reads_after": reads_after,
            "untouched_region": untouched_region,
            "files": files,
            "save": {
                "files_written": written,
                "on_disk_matches_serialized": on_disk,
                "why": "save() writes only regions that were opened, so a "
                       "port that decided from entity_available without "
                       "opening the file reports every count above correctly "
                       "and leaves the entities and poi files on disk exactly "
                       "as they were",
            },
        }, entity_bytes, poi_bytes, far_bytes
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _border_field(name, kind, value):
    """One typed field of a border, bits first, value informational."""
    if kind == "double":
        return {"name": name, "t": "double",
                "bits": struct.pack(">d", value).hex(), "v": value}
    if kind == "long":
        return {"name": name, "t": "long", "v": str(value)}
    return {"name": name, "t": kind, "v": value}


def _border_tag(field):
    """The NBT tag a border field spec describes; bits win over value."""
    if field["t"] == "double":
        return TagDouble(
            struct.unpack(">d", bytes.fromhex(field["bits"]))[0])
    if field["t"] == "long":
        return TagLong(int(field["v"]))
    if field["t"] == "int":
        return TagInt(field["v"])
    raise AssertionError(f"unknown border field type {field['t']!r}")


def world_settings_script():
    """World-settings edits: the script, the reads, and the bytes.

    The same shape as ``world_edits.json`` for the same reason: a port that
    merely *reads* game mode and weather correctly can still write them in
    the wrong tag types or the wrong compounds, and only executing one fixed
    script and digesting the resulting NBT catches that. Also on purpose:
    one step must be *refused* (changing a locked difficulty), because the
    refusal is part of the interface.

    64-bit tick counts ride as decimal strings, per the corpus conventions.
    """
    script = [
        {"op": "set_game_type", "mode": "spectator",
         "why": "world default and the player's own mode together"},
        {"op": "set_allow_commands", "value": False},
        {"op": "set_hardcore", "value": True},
        {"op": "set_difficulty", "name": "hard"},
        {"op": "set_difficulty_locked", "value": True},
        {"op": "set_difficulty", "name": "peaceful", "refused": True,
         "why": "a locked difficulty must refuse, not comply"},
        {"op": "set_weather", "kind": "thunder", "duration": 2400,
         "why": "rain and thunder timers fill together, the command's way"},
        {"op": "set_day_time", "time": "noon",
         "why": "presets advance to the next occurrence, never backward"},
        {"op": "set_day_time", "time": 123456789,
         "dimension": "minecraft:the_end",
         "why": "absolute ticks, on the second stored clock"},
        {"op": "set_spawn", "pos": [-400, 71, 100], "yaw": 90.0,
         "why": "position and yaw move; dimension and pitch stay"},
        {"op": "world_border",
         "why": "no file, no flat keys: the defaults, marked unstored"},
        {"op": "world_border", "dimension": "minecraft:the_nether",
         "why": "a dimension with no border file of its own answers the "
                "same way; it is not an error"},
        {"op": "world_border", "dimension": "minecraft:the_aether",
         "refused": True,
         "why": "only the three vanilla dimensions are addressable"},
        {"op": "inject_old_border_keys", "fields": [
            _border_field("BorderSize", "double", 1000.5),
            _border_field("BorderCenterX", "double", -250.25),
            _border_field("BorderCenterZ", "double", 77.75),
            _border_field("BorderSafeZone", "double", 5.0),
            _border_field("BorderDamagePerBlock", "double", 0.2),
            _border_field("BorderWarningBlocks", "double", 6.0),
            _border_field("BorderWarningTime", "double", 12.0),
            _border_field("BorderSizeLerpTarget", "double", 1000.5),
            _border_field("BorderSizeLerpTime", "long", 0),
         ],
         "why": "a pre-26.x world kept the border in level.dat, warnings "
                "as doubles; tags land in listed order"},
        {"op": "world_border",
         "why": "the flat keys read back, warnings coerced to ints"},
        {"op": "write_world_border_file", "fields": [
            _border_field("center_x", "double", 1234.5),
            _border_field("center_z", "double", -987.25),
            _border_field("size", "double", 2048.125),
            _border_field("safe_zone", "double", 6.5),
            _border_field("damage_per_block", "double", 0.4),
            _border_field("warning_blocks", "int", 8),
            _border_field("warning_time", "int", 20),
            _border_field("lerp_target", "double", 2048.125),
            _border_field("lerp_time", "long", 0),
         ],
         "dimension": "minecraft:overworld",
         "path": "dimensions/minecraft/overworld/data/minecraft/"
                 "world_border.dat",
         "why": "the split file, gzip, root '' holding a data compound "
                "with tags in listed order, as the game lays it out. It "
                "goes under the DIMENSION, which is where the game keeps "
                "it -- a reader looking in data/minecraft/ finds nothing "
                "and reports the defaults for every real save"},
        {"op": "world_border",
         "why": "the split file outranks the flat keys"},
        {"op": "write_world_border_file", "fields": [
            _border_field("center_x", "double", -64.0),
            _border_field("center_z", "double", 64.0),
            _border_field("size", "double", 512.0),
            _border_field("safe_zone", "double", 5.0),
            _border_field("damage_per_block", "double", 0.2),
            _border_field("warning_blocks", "int", 5),
            _border_field("warning_time", "int", 15),
            _border_field("lerp_target", "double", 512.0),
            _border_field("lerp_time", "long", 0),
         ],
         "dimension": "minecraft:the_end",
         "path": "dimensions/minecraft/the_end/data/minecraft/"
                 "world_border.dat",
         "why": "a second dimension, with its own different border"},
        {"op": "world_border", "dimension": "minecraft:the_end",
         "why": "each dimension answers with its own, not the overworld's"},
        {"op": "world_border",
         "why": "and reading the end did not disturb the overworld's"},
    ]

    directory = tempfile.mkdtemp(prefix="conformance-settings-")
    try:
        region_dir = os.path.join(
            directory, "dimensions", "minecraft", "overworld", "region")
        data_dir = os.path.join(directory, "data", "minecraft")
        player_dir = os.path.join(directory, "players", "data")
        os.makedirs(region_dir)
        os.makedirs(data_dir)
        os.makedirs(player_dir)
        shutil.copyfile(os.path.join(_HERE, "r.-1.0.mca"),
                        os.path.join(region_dir, "r.-1.0.mca"))
        shutil.copyfile(os.path.join(_HERE, "level.dat"),
                        os.path.join(directory, "level.dat"))
        shutil.copyfile(os.path.join(_HERE, "weather.dat"),
                        os.path.join(data_dir, "weather.dat"))
        shutil.copyfile(os.path.join(_HERE, "world_clocks.dat"),
                        os.path.join(data_dir, "world_clocks.dat"))
        from fixtures import FIXTURE_PLAYER_UUID
        shutil.copyfile(os.path.join(_HERE, "player.dat"),
                        os.path.join(player_dir,
                                     f"{FIXTURE_PLAYER_UUID}.dat"))

        from heaton_voxel.world import OVERWORLD, MinecraftSave

        save = MinecraftSave.load(directory, dimensions=[OVERWORLD])
        the_end = "minecraft:the_end"

        def border_json(border):
            def dbl(value):
                return {"t": "double",
                        "bits": struct.pack(">d", value).hex(), "v": value}
            return {
                "stored": border.stored,
                "center_x": dbl(border.center_x),
                "center_z": dbl(border.center_z),
                "size": dbl(border.size),
                "safe_zone": dbl(border.safe_zone),
                "damage_per_block": dbl(border.damage_per_block),
                "warning_blocks": border.warning_blocks,
                "warning_time": border.warning_time,
                "lerp_target": dbl(border.lerp_target),
                "lerp_time": str(border.lerp_time),
                # Derived in doubles; IEEE makes the arithmetic portable.
                "min_x": dbl(border.min_x), "max_x": dbl(border.max_x),
                "min_z": dbl(border.min_z), "max_z": dbl(border.max_z),
            }

        def snapshot():
            weather = save.weather()
            return {
                # Both, because "the default dimension still means the
                # overworld" and "each dimension keeps its own" are two
                # different claims and the corpus should pin both.
                "world_border": border_json(save.world_border()),
                "world_border_the_end": border_json(save.world_border(the_end)),
                "game_type": save.game_type(),
                "allow_commands": save.allow_commands(),
                "difficulty": save.difficulty(),
                "hardcore": save.hardcore(),
                "difficulty_locked": save.difficulty_locked(),
                "weather": {
                    "kind": weather.kind,
                    "raining": weather.raining,
                    "thundering": weather.thundering,
                    "rain_time": weather.rain_time,
                    "thunder_time": weather.thunder_time,
                    "clear_weather_time": weather.clear_weather_time,
                },
                "day_time": {
                    OVERWORLD: str(save.day_time(OVERWORLD)),
                    the_end: str(save.day_time(the_end)),
                },
                "spawn": list(save.spawn_position()),
            }

        before = snapshot()

        results = []
        for step in script:
            op = step["op"]
            if step.get("refused"):
                try:
                    if op == "set_difficulty":
                        save.set_difficulty(step["name"])
                    elif op == "world_border":
                        save.world_border(step.get("dimension"))
                    else:
                        raise AssertionError(f"no refusal path for {op!r}")
                    raise AssertionError(f"{op} was expected to refuse")
                except nbt.NBTError:
                    results.append({"refused": True})
                continue
            if op == "set_game_type":
                save.set_game_type(step["mode"])
                results.append({"game_type": save.game_type()})
            elif op == "set_allow_commands":
                save.set_allow_commands(step["value"])
                results.append({"allow_commands": save.allow_commands()})
            elif op == "set_hardcore":
                save.set_hardcore(step["value"])
                results.append({"hardcore": save.hardcore()})
            elif op == "set_difficulty":
                save.set_difficulty(step["name"])
                results.append({"difficulty": save.difficulty()})
            elif op == "set_difficulty_locked":
                save.set_difficulty_locked(step["value"])
                results.append({"locked": save.difficulty_locked()})
            elif op == "set_weather":
                save.set_weather(step["kind"], duration=step.get("duration"))
                results.append({"kind": save.weather().kind})
            elif op == "set_day_time":
                total = save.set_day_time(step["time"],
                                          step.get("dimension"))
                results.append({"total_ticks": str(total)})
            elif op == "set_spawn":
                save.set_spawn_position(*step["pos"], yaw=step.get("yaw"))
                results.append({"spawn": list(save.spawn_position())})
            elif op == "world_border":
                results.append({"border": border_json(
                    save.world_border(step.get("dimension")))})
            elif op == "inject_old_border_keys":
                level = save.level.root["Data"]
                for field in step["fields"]:
                    level[field["name"]] = _border_tag(field)
                results.append({"injected": len(step["fields"])})
            elif op == "write_world_border_file":
                data = TagCompound()
                for field in step["fields"]:
                    data[field["name"]] = _border_tag(field)
                root = TagCompound()
                root["data"] = data
                handle = NBT()
                handle.root = root
                handle.compression = nbt.COMPRESSION_GZIP
                target = os.path.join(directory, *step["path"].split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                handle.save(target)
                results.append({"written": True})
            else:
                raise AssertionError(f"unknown op {op!r}")

        steps = [dict(step, result=result)
                 for step, result in zip(script, results)]
        after = snapshot()

        # Keyed by the path each file was loaded from, because the border
        # exists once per dimension and a bare stem cannot say which one.
        payload_files = {
            "level": "level.dat",
            "weather": "data/minecraft/weather.dat",
            "world_clocks": "data/minecraft/world_clocks.dat",
            "world_border_overworld":
                "dimensions/minecraft/overworld/data/minecraft/"
                "world_border.dat",
            "world_border_the_end":
                "dimensions/minecraft/the_end/data/minecraft/"
                "world_border.dat",
            "player": f"players/data/{FIXTURE_PLAYER_UUID}.dat",
        }

        def settings_digest(relative):
            handle = save._settings_files[relative]
            return sha256(nbt.encode(handle.root, handle.root_name))

        digests = {
            "level": sha256(nbt.encode(save.level.root, save.level.root_name)),
            "player": sha256(nbt.encode(save._player.root,
                                        save._player.root_name)),
        }
        for key, relative in payload_files.items():
            if key in digests:
                continue
            digests[key] = settings_digest(relative)

        return {
            "version": CORPUS_VERSION,
            "assembly": "as world.json, then ../weather.dat and "
                        "../world_clocks.dat to data/minecraft/, and "
                        "../player.dat to players/data/"
                        f"{FIXTURE_PLAYER_UUID}.dat; run every step in order",
            "note": "each step is the settings call to perform and the value "
                    "read back through the paired accessor; a step marked "
                    "refused must raise, not comply. The border ops build "
                    "tags of the listed types in the listed order -- "
                    "insertion order reaches the digests. A "
                    "write_world_border_file step names the path it writes: "
                    "the border is per dimension, under "
                    "dimensions/<ns>/<dim>/data/minecraft/, never in the "
                    "world-wide data/minecraft/. Digests are of the "
                    "UNCOMPRESSED root NBT of each touched file after the "
                    "whole script, keyed by payload_files, so they are "
                    "portable.",
            "player_target": f"players/data/{FIXTURE_PLAYER_UUID}.dat",
            "payload_files": payload_files,
            "steps": steps,
            "reads_before": before,
            "reads_after": after,
            "payload_sha256": digests,
        }
    finally:
        shutil.rmtree(directory, ignore_errors=True)


# --------------------------------------------------------------------------
# Derived states and transforms
# --------------------------------------------------------------------------

_AIR = "minecraft:air"


def _derive(center, neighbors):
    states = {(0, 0, 0): BlockState.parse(center)}
    offsets = {"north": (0, 0, -1), "south": (0, 0, 1), "west": (-1, 0, 0),
               "east": (1, 0, 0), "above": (0, 1, 0), "below": (0, -1, 0)}
    for key, state in neighbors.items():
        if state is not None:
            states[offsets[key]] = BlockState.parse(state)

    def block_at(x, y, z):
        return states.get((x, y, z), BlockState(_AIR))

    derived = _derived_state(states[(0, 0, 0)], block_at, 0, 0, 0)
    return str(derived if derived is not None else states[(0, 0, 0)])


def derived_state_cases():
    fence = "minecraft:oak_fence"
    wall = "minecraft:cobblestone_wall"
    stair = "minecraft:oak_stairs[facing={}]"
    raw = [
        ("fence-solo", fence, {}),
        ("fence-pair", fence, {"east": fence}),
        ("fence-to-solid", fence, {"north": "minecraft:stone"}),
        ("fence-not-to-slab", fence, {"north": "minecraft:oak_slab[type=bottom]"}),
        ("fence-to-double-slab", fence, {"north": "minecraft:oak_slab[type=double]"}),
        ("fence-to-stair-back", fence,
         {"north": "minecraft:oak_stairs[facing=south]"}),
        ("fence-not-to-stair-side", fence,
         {"north": "minecraft:oak_stairs[facing=east]"}),
        ("fence-to-spawner", fence, {"west": "minecraft:spawner"}),
        ("fence-not-to-grindstone", fence,
         {"west": "minecraft:grindstone[face=floor,facing=north]"}),
        ("fence-not-to-nether", fence, {"east": "minecraft:nether_brick_fence"}),
        ("nether-pair", "minecraft:nether_brick_fence",
         {"east": "minecraft:nether_brick_fence"}),
        ("fence-gate-along-bar", fence,
         {"east": "minecraft:oak_fence_gate[facing=north]"}),
        ("fence-gate-across-bar", fence,
         {"north": "minecraft:oak_fence_gate[facing=north]"}),
        ("fence-keeps-waterlogged", "minecraft:oak_fence[waterlogged=true]",
         {"east": fence}),
        # What is solid to join is the game's own answer, full faces read off
        # its shapes (heaton_voxel.connect_faces), and no name says these:
        # the list of endings that came before missed every one, which left
        # a copper tower's panes short of its piers.
        ("pane-to-waxed-copper", "minecraft:gray_stained_glass_pane",
         {"west": "minecraft:waxed_oxidized_copper",
          "east": "minecraft:waxed_oxidized_cut_copper"}),
        ("pane-to-smooth-quartz", "minecraft:glass_pane",
         {"north": "minecraft:smooth_quartz"}),
        ("fence-to-stem", fence, {"east": "minecraft:crimson_stem[axis=y]"}),
        ("fence-to-open-door-plate", fence,
         {"west": "minecraft:oak_door[facing=east,half=lower,hinge=left,"
                  "open=false,powered=false]"}),
        ("wall-to-crafter", wall,
         {"north": "minecraft:crafter[crafting=false,orientation=north_up,"
                   "triggered=false]"}),
        ("wall-solo", wall, {}),
        ("wall-straight", wall, {"east": wall, "west": wall}),
        ("wall-corner", wall, {"east": wall, "south": wall}),
        ("wall-tee", wall, {"east": wall, "west": wall, "south": wall}),
        ("wall-cross", wall,
         {"east": wall, "west": wall, "north": wall, "south": wall}),
        ("wall-under-solid", wall,
         {"east": wall, "west": wall, "above": "minecraft:stone"}),
        ("wall-under-lantern", wall,
         {"east": wall, "west": wall,
          "above": "minecraft:lantern[hanging=false]"}),
        # Air of every kind has no shape, so nothing is perched: a run
        # under a cave roof stays flush, as one under the sky does.
        ("wall-under-cave-air", wall,
         {"east": wall, "west": wall, "above": "minecraft:cave_air"}),
        ("wall-under-void-air", wall,
         {"east": wall, "west": wall, "above": "minecraft:void_air"}),
        ("wall-under-wall", wall,
         {"east": wall, "west": wall,
          "above": "minecraft:cobblestone_wall[east=low,north=none,"
                   "south=none,up=false,west=low]"}),
        ("wall-lopsided-under-wall", wall,
         {"east": wall, "west": wall, "north": wall, "south": wall,
          "above": "minecraft:cobblestone_wall[east=low,north=none,"
                   "south=none,up=true,west=none]"}),
        # What a wall reads above it is the game's own answer -- the
        # strips and the post center that block's collision underside
        # covers, and the wall_post_override tag (heaton_voxel.wall_cover)
        # -- not a name. A wall above with its post up raises this one's
        # outright; a leaf is a full cube that nothing attaches to; a
        # bottom slab covers the strips and a top slab nothing; an open
        # trapdoor covers nothing; a torch has no shape and raises the
        # post by its tag.
        ("wall-under-posted-wall", wall,
         {"north": wall, "south": wall,
          "above": "minecraft:cobblestone_wall[east=none,north=low,"
                   "south=low,up=true,west=none]"}),
        ("wall-under-leaves", wall,
         {"north": wall, "south": wall,
          "above": "minecraft:spruce_leaves[distance=3,persistent=false]"}),
        ("wall-under-bottom-slab", wall,
         {"east": wall, "west": wall,
          "above": "minecraft:stone_slab[type=bottom]"}),
        ("wall-under-top-slab", wall,
         {"east": wall, "west": wall,
          "above": "minecraft:stone_slab[type=top]"}),
        ("wall-under-open-trapdoor", wall,
         {"east": wall, "west": wall,
          "above": "minecraft:oak_trapdoor[half=bottom,open=true]"}),
        ("wall-under-torch", wall,
         {"east": wall, "west": wall, "above": "minecraft:torch"}),
        # What the game's Block.isExceptionForConnection excepts is never
        # joined, full faces and all: the pumpkins, the melon (and the
        # jack o'lantern, the barrier, leaves and shulker boxes). A
        # trapdoor's one full side is an open one's plate, opposite its
        # facing: joined from the side it faces away from, never edge on.
        ("wall-not-to-pumpkin", wall,
         {"north": wall, "south": "minecraft:pumpkin"}),
        ("fence-not-to-carved-pumpkin", fence,
         {"east": "minecraft:carved_pumpkin[facing=north]"}),
        ("pane-not-to-melon", "minecraft:glass_pane",
         {"west": "minecraft:melon"}),
        ("wall-to-open-trapdoor-plate", wall,
         {"east": "minecraft:spruce_trapdoor[facing=east,half=top,open=true]",
          "west": "minecraft:oak_trapdoor[facing=west,open=true]"}),
        ("wall-not-to-trapdoor-edge-or-shut", wall,
         {"east": "minecraft:spruce_trapdoor[facing=north,open=true]",
          "west": "minecraft:oak_trapdoor[facing=east,open=true]",
          "north": "minecraft:oak_trapdoor[facing=north,half=top]"}),
        ("wall-joins-pane", wall, {"north": "minecraft:glass_pane"}),
        ("pane-in-wall", "minecraft:glass_pane",
         {"east": "minecraft:stone", "west": "minecraft:stone"}),
        ("bars-join-wall", "minecraft:iron_bars", {"south": wall}),
        ("gate-in-wall", "minecraft:oak_fence_gate[facing=north]",
         {"east": wall, "west": wall}),
        ("gate-wrong-axis", "minecraft:oak_fence_gate[facing=east]",
         {"east": wall, "west": wall}),
        ("stair-straight", stair.format("south"), {}),
        ("stair-outer-left", stair.format("south"),
         {"south": stair.format("east")}),
        ("stair-outer-right", stair.format("south"),
         {"south": stair.format("west")}),
        ("stair-inner-left", stair.format("south"),
         {"north": stair.format("east")}),
        ("stair-inner-right", stair.format("south"),
         {"north": stair.format("west")}),
        ("stair-flank-suppresses", stair.format("south"),
         {"south": stair.format("east"), "west": stair.format("south")}),
        ("stair-half-mismatch", stair.format("south"),
         {"south": "minecraft:oak_stairs[facing=east,half=top]"}),
    ]
    cases = []
    for name, center, neighbors in raw:
        cases.append({
            "name": name,
            "center": center,
            "neighbors": neighbors,
            "expected": _derive(center, neighbors),
        })
    return {
        "version": CORPUS_VERSION,
        "meaning": "expected is the state a vanilla neighbor update would "
                   "store for center, given the neighbors (all other "
                   "positions are minecraft:air). Properties not shown in "
                   "an input state take vanilla defaults.",
        "cases": cases,
    }


def transform_cases():
    states = [
        "minecraft:oak_stairs[facing=north,half=bottom,shape=inner_left]",
        "minecraft:oak_stairs[facing=east,shape=outer_right]",
        "minecraft:oak_log[axis=x]", "minecraft:oak_log[axis=y]",
        "minecraft:oak_sign[rotation=3]", "minecraft:oak_sign[rotation=15]",
        "minecraft:rail[shape=north_east]", "minecraft:rail[shape=east_west]",
        "minecraft:powered_rail[shape=ascending_north]",
        "minecraft:cobblestone_wall[east=low,north=tall,south=none,up=true,west=none]",
        "minecraft:vine[east=true,north=false,south=false,up=true,west=false]",
        "minecraft:oak_door[facing=east,half=lower,hinge=left]",
        "minecraft:chest[facing=west,type=left]",
        "minecraft:oak_slab[type=top]",
        "minecraft:lever[face=floor,facing=north]",
        "minecraft:piston[extended=false,facing=up]",
        "minecraft:lantern[hanging=true]",
        "minecraft:pointed_dripstone[vertical_direction=up]",
        "minecraft:crafter[orientation=north_up]",
        "minecraft:white_bed[facing=south,part=foot]",
        "minecraft:fake_block[widget=north]",
    ]
    from heaton_voxel.schem import _properties_mirrored

    def apply(state_text, op):
        state = BlockState.parse(state_text)
        if op.startswith("rot"):
            quarters = int(op[3:]) // 90

            def mapper(props):
                for _ in range(quarters):
                    props = _properties_rotated_cw(props)
                return props

            return str(_map_state(state, mapper))
        axis = op[-1]
        return str(_map_state(
            state, lambda props: _properties_mirrored(props, axis)))

    ops = ("rot90", "rot180", "rot270", "flipx", "flipy", "flipz")
    state_cases = [
        {"state": text, **{op: apply(text, op) for op in ops}}
        for text in states
    ]
    return {
        "version": CORPUS_VERSION,
        "conventions": "rotations are clockwise seen from above (north "
                       "becomes east); flips run along the named axis "
                       "(flipx swaps east/west). fake_block's directional-"
                       "looking property is outside the vocabulary and "
                       "must pass through unchanged (and be warned about).",
        "states": state_cases,
        "facing_bytes": {
            "three_d_order": ["down", "up", "north", "south", "west", "east"],
            "rot90": {str(k): v for k, v in schem_module._FACING3_CW.items()},
            "mirror": {axis: {str(k): v for k, v in table.items()}
                       for axis, table in schem_module._FACING3_MIRROR.items()},
            "two_d_order": ["south", "west", "north", "east"],
            "two_d_rot90": {str(k): v
                            for k, v in schem_module._FACING2_CW.items()},
            "two_d_mirror": {axis: {str(k): v for k, v in table.items()}
                             for axis, table in
                             schem_module._FACING2_MIRROR.items()},
        },
        "geometry": {
            "box": [4, 3, 5],
            "cell_rot90": "(x,y,z) -> (length-1-z, y, x); size becomes "
                          "(length, height, width)",
            "point_rot90": "(x,y,z) -> (length-z, y, x)",
            "offset_rot90": "(ox,oy,oz) -> (-oz-length+1, oy, ox)",
            "yaw_rot90": "+90 degrees, wrapped to [-180, 180)",
            "cell_samples_rot90": [
                [list(src), [4 - src[2], src[1], src[0]]]
                for src in ((0, 0, 0), (3, 2, 4), (1, 0, 2))
            ],
        },
        "yaw": _yaw_cases(),
        "negative_rotation": {
            "rule": "degrees normalize as quarters = floormod(degrees / 90, "
                    "4); with truncating division use ((d/90)%4+4)%4",
            "equivalences": [[-90, 270], [-180, 180], [-270, 90], [450, 90]],
        },
    }


def _yaw_cases():
    """Entity yaw under every transform, wrap included -- the float modulo
    trap: the wrap must behave like Python's %, whose result follows the
    divisor's sign. Portable form: ((v % 360) + 540) % 360 - 180."""
    wrap = schem_module._wrap_degrees
    yaws = [0.0, 90.0, 170.0, -180.0, -90.0, 350.0, -190.0]
    cases = []
    for yaw in yaws:
        cases.append({
            "yaw": yaw,
            "rot90": wrap(yaw + 90.0),
            "rot180": wrap(yaw + 180.0),
            "rot270": wrap(yaw + 270.0),
            "flipx": wrap(-yaw),
            "flipz": wrap(180.0 - yaw),
            # A y-flip leaves the facing alone, but the stored value is still
            # wrapped on the way out like every other op -- so a yaw outside
            # [-180, 180) comes back normalized, not untouched. Recording the
            # raw value here claimed otherwise; the C# port caught it.
            "flipy": wrap(yaw),
        })
    return {
        "range": "[-180, 180)",
        "pitch_note": "pitch is untouched by every op except flipy, which "
                      "negates it",
        "cases": cases,
    }


def _create_result(r) -> dict:
    return {
        "chunks_created": r.chunks_created,
        "created_chunks": [list(c) for c in r.created_chunks],
        "regions_created": r.regions_created,
        "existing_chunks": [list(c) for c in r.existing_chunks],
        "unfinished_chunks": [list(c) for c in r.unfinished_chunks],
        "unreadable_chunks": len(r.unreadable_chunks),
        "data_version": r.data_version,
        "min_y": r.min_y,
        "max_y": r.max_y,
        "biome": r.biome,
        "complete": r.complete,
    }


def _run_creation(directory: str, script: list) -> list:
    """Run a chunk-creation script over the world in ``directory``; each
    step with the result it reports."""
    from heaton_voxel.world import MinecraftSave

    save = MinecraftSave.load(directory)
    steps = []
    for step in script:
        op = step["op"]
        if op == "stamp_level":
            save.level.root["Data"]["DataVersion"] = TagInt(step["data_version"])
            result = {}
        elif op == "create_chunks" and step.get("refused"):
            try:
                save.create_chunks(tuple(step["from"]), tuple(step["to"]))
            except nbt.NBTError:
                result = {"refused": True}
            else:
                raise AssertionError(f"{step} was not refused")
        elif op == "create_chunks":
            result = _create_result(save.create_chunks(
                tuple(step["from"]), tuple(step["to"]),
                dimension=step.get("dimension"), biome=step.get("biome")))
        elif op == "create_chunk":
            dim = save.dimension(step.get("dimension"))
            try:
                dim.create_chunk(*step["chunk"], data_version=step["data_version"],
                                 biome=step["biome"])
            except nbt.NBTError:
                result = {"refused": True}
            else:
                result = {"refused": False}
        elif op == "fill":
            r = save.fill(tuple(step["from"]), tuple(step["to"]), step["block"],
                          keep_loaded=True)
            result = {"blocks_filled": r.blocks_filled, "chunks_modified": r.chunks_modified}
        elif op == "place":
            result = {"placed": bool(save.place_block(*step["at"], step["block"]))}
        elif op == "rebuild_heightmaps":
            r = save.rebuild_heightmaps(tuple(step["from"]), tuple(step["to"]),
                                        keep_loaded=True)
            result = {"chunks_rebuilt": r.chunks_rebuilt}
        elif op == "import":
            paste = Schematic(*step["size"], step["data_version"])
            for x, y, z, block in step["cells"]:
                paste.set_block(x, y, z, block)
            r = save.import_schem(paste, tuple(step["at"]), anchor="min",
                                  paste_voids=step.get("paste_voids", False))
            result = {"blocks_pasted": r.blocks_pasted, "air_skipped": r.air_skipped,
                      "voids_skipped": r.voids_skipped, "chunks_modified": r.chunks_modified}
        else:
            raise AssertionError(f"unknown op {op!r}")
        steps.append(dict(step, result=result))
    return save, steps


def chunk_creation_cases():
    """Chunks built from nothing (``create_chunks``), over a copy of each
    fixture world, as scripts a port runs: what each step reports, then the
    blocks, statuses and surfaces read back, and the uncompressed NBT digest
    of every chunk built and of the unfinished one left beside them."""
    runs = []
    plans = [
        ("26.2", "r.-1.0.mca", "level.dat", [
            {"op": "create_chunks", "why": "around two unfinished chunks, which stay",
             "from": [-512, 0, 432], "to": [-465, 0, 495]},
            {"op": "create_chunks", "why": "over a finished chunk, which stays",
             "from": [-448, 0, 224], "to": [-433, 0, 239]},
            {"op": "create_chunks", "why": "a region file the world does not have",
             "from": [640, 0, 640], "to": [671, 0, 655], "biome": "minecraft:desert"},
            {"op": "create_chunks", "why": "a dimension the world never visited",
             "from": [0, 0, 0], "to": [15, 0, 15], "dimension": "minecraft:the_nether"},
            {"op": "create_chunk", "why": "one chunk where one stands",
             "chunk": [-28, 14], "data_version": 4903, "biome": "minecraft:plains"},
            {"op": "fill", "from": [640, 60, 640], "to": [660, 62, 650],
             "block": "minecraft:stone"},
            {"op": "place", "at": [645, 63, 645], "block": "minecraft:oak_fence"},
            {"op": "place", "at": [646, 63, 645], "block": "minecraft:oak_fence"},
            {"op": "rebuild_heightmaps", "from": [640, 0, 640], "to": [671, 0, 655]},
            {"op": "import", "why": "a structure's void leaves the stone under it",
             "at": [650, 62, 644], "size": [3, 1, 1], "data_version": 4903,
             "cells": [[0, 0, 0, "minecraft:gold_block"],
                       [1, 0, 0, "minecraft:structure_void"]]},
            {"op": "import", "why": "unless told to place the void itself",
             "at": [650, 62, 646], "size": [3, 1, 1], "data_version": 4903,
             "paste_voids": True,
             "cells": [[0, 0, 0, "minecraft:gold_block"],
                       [1, 0, 0, "minecraft:structure_void"]]},
            {"op": "stamp_level", "why": "a world newer than any chunk read",
             "data_version": 5100},
            {"op": "create_chunks", "from": [704, 0, 640], "to": [704, 0, 640]},
            {"op": "stamp_level", "why": "a world older than any", "data_version": 4440},
            {"op": "create_chunks", "from": [720, 0, 640], "to": [720, 0, 640],
             "refused": True},
        ], [[-31, 28, "minecraft:overworld"], [-32, 28, "minecraft:overworld"],
            [40, 40, "minecraft:overworld"], [41, 40, "minecraft:overworld"],
            [44, 40, "minecraft:overworld"], [0, 0, "minecraft:the_nether"]],
         [[641, 61, 641, "minecraft:overworld"], [661, 62, 645, "minecraft:overworld"],
          [650, 62, 644, "minecraft:overworld"], [651, 62, 644, "minecraft:overworld"],
          [652, 62, 644, "minecraft:overworld"], [651, 62, 646, "minecraft:overworld"],
          [645, 63, 645, "minecraft:overworld"], [646, 63, 645, "minecraft:overworld"],
          [-500, 0, 450, "minecraft:overworld"], [5, 100, 5, "minecraft:the_nether"]]),
        ("26.3", "modern/r.-1.0.mca", "modern/level.dat", [
            {"op": "create_chunks", "why": "beside the 26.3 chunks, in their codec",
             "from": [-416, 0, 0], "to": [-385, 0, 15]},
            {"op": "fill", "from": [-410, 70, 2], "to": [-390, 70, 4],
             "block": "minecraft:oak_stairs[facing=east]"},
            {"op": "rebuild_heightmaps", "from": [-416, 0, 0], "to": [-385, 0, 15]},
        ], [[-26, 0, "minecraft:overworld"], [-25, 0, "minecraft:overworld"]],
         [[-400, 70, 3, "minecraft:overworld"], [-400, 71, 3, "minecraft:overworld"]]),
    ]
    for label, region, level, script, digest_chunks, samples in plans:
        directory = tempfile.mkdtemp(prefix="conformance-create-")
        try:
            region_dir = os.path.join(directory, "dimensions", "minecraft", "overworld", "region")
            os.makedirs(region_dir)
            shutil.copyfile(os.path.join(_HERE, region), os.path.join(region_dir, "r.-1.0.mca"))
            shutil.copyfile(os.path.join(_HERE, level), os.path.join(directory, "level.dat"))
            save, steps = _run_creation(directory, script)
            blocks = [[x, y, z, name, str(save.get_block(x, y, z, dimension=name))]
                      for x, y, z, name in samples]
            surfaces = [[x, z, name, save.surface_y(x, z, dimension=name)]
                        for x, _, z, name in samples]
            digests = []
            for chunk_x, chunk_z, name in digest_chunks:
                chunk = save.dimension(name).chunk(chunk_x, chunk_z)
                digests.append({
                    "chunk": [chunk_x, chunk_z], "dimension": name,
                    "status": save.dimension(name).chunk_status(chunk_x, chunk_z),
                    "payload_sha256": sha256(encode(chunk.data, chunk.data_name)),
                })
            runs.append({
                "world": label,
                "region": region,
                "level": level,
                "assembly": f"copy ../{region} to dimensions/minecraft/overworld/region/"
                            f"r.-1.0.mca and ../{level} to level.dat, load every "
                            f"dimension, then run the steps in order",
                "steps": steps,
                "blocks_after": blocks,
                "surfaces_after": surfaces,
                "chunk_payload_sha256": digests,
            })
        finally:
            shutil.rmtree(directory, ignore_errors=True)
    return {"version": CORPUS_VERSION, "runs": runs,
            "note": "stamp_level sets level.dat's Data.DataVersion in memory; a "
                    "step marked refused must raise the port's NBT error and build "
                    "nothing. Digests are of the UNCOMPRESSED chunk NBT."}


def _text_node(text: str):
    """A string as ``snbt.json`` spells one: a JSON string, escapes and
    all, unless it holds a lone surrogate, which some JSON readers (.NET's
    among them) refuse, and then its code points."""
    if any(0xD800 <= ord(c) <= 0xDFFF for c in text):
        return {"cp": [ord(c) for c in text]}
    return {"v": text}


def dump_rows(payload) -> bytes:
    """``dump_json`` for an artifact of many small cases: each case on a
    line of its own, compact, so a few thousand of them stay readable and
    small."""
    lines = ["{"]
    keys = sorted(payload)
    for n, key in enumerate(keys):
        value = payload[key]
        tail = "," if n + 1 < len(keys) else ""
        if isinstance(value, list):
            lines.append(f" {json.dumps(key)}: [")
            for i, row in enumerate(value):
                comma = "," if i + 1 < len(value) else ""
                lines.append("  " + json.dumps(row, sort_keys=True, separators=(",", ":"),
                                               ensure_ascii=True) + comma)
            lines.append(" ]" + tail)
        else:
            lines.append(f" {json.dumps(key)}: {json.dumps(value, ensure_ascii=True)}{tail}")
    lines.append("}")
    return ("\n".join(lines) + "\n").encode("ascii")


#: Inputs the game's fixture does not carry: the nesting limit either side
#: of it, which the game itself would read past.
_SNBT_LIMITS = [
    "[" * nbt.MAX_DEPTH + "]" * nbt.MAX_DEPTH,
    "[" * (nbt.MAX_DEPTH + 1) + "]" * (nbt.MAX_DEPTH + 1),
    "{a:" * nbt.MAX_DEPTH + "1" + "}" * nbt.MAX_DEPTH,
    "{a:" * (nbt.MAX_DEPTH + 1) + "1" + "}" * (nbt.MAX_DEPTH + 1),
    "bool(" * 600 + "1" + ")" * 600,
]


def snbt_cases():
    """SNBT both ways (``heaton_voxel.snbt``), over every string and binary
    tag the game itself answered in ``fixtures/snbt.json`` and the limits
    of nesting: for a string, the binary of the tag it reads as (the
    uncompressed encoding of a root compound holding it under ``v``, keys
    in the order they were read, in hex) and that tag written back, or that it is
    refused; for a binary tag, the tag under ``v`` written. Compare text
    exactly, and a refusal only as a refusal: the messages are not part of
    the contract."""
    with open(os.path.join(_HERE, "snbt.json"), encoding="ascii") as handle:
        game = json.load(handle)
    parse = []
    for text in [row["in"] for row in game["parse"]] + _SNBT_LIMITS:
        entry = {"in": _text_node(text)}
        try:
            tag = parse_snbt(text)
        except nbt.NBTError:
            entry["refused"] = True
        else:
            entry["nbt"] = encode(TagCompound({"v": tag})).hex()
            entry["snbt"] = _text_node(to_snbt(tag))
        parse.append(entry)
    printed = []
    for row in game["print"]:
        binary = base64.b64decode(row["nbt"])
        printed.append({"nbt": binary.hex(),
                        "snbt": _text_node(to_snbt(decode(binary)[1]["v"]))})
    return {"game": game["game"], "parse": parse, "print": printed}


def table_dump():
    return {
        "version": CORPUS_VERSION,
        "provenance": "validated against vanilla-placed blocks in a real "
                      "save (0 disagreements in 22,616); regenerate rather "
                      "than editing by hand",
        "min_data_version": 2825,
        "modern_state_data_version": __import__(
            "heaton_voxel.blocks", fromlist=["MODERN_STATE_DATA_VERSION"]
        ).MODERN_STATE_DATA_VERSION,
        "heightmap_names": list(
            __import__("heaton_voxel.blocks", fromlist=["HEIGHTMAP_NAMES"]
                       ).HEIGHTMAP_NAMES),
        "air_blocks": sorted(schem_module.AIR_BLOCKS),
        "rotate_cw": {
            "direction": schem_module._DIR_CW,
            "axis": schem_module._AXIS_CW,
            "rail_shape": schem_module._RAIL_CW,
        },
        "mirror": {
            "direction": schem_module._DIR_MIRROR,
            "rail_shape": schem_module._RAIL_MIRROR,
            "stair_shape": schem_module._STAIR_MIRROR,
            "left_right": schem_module._LEFT_RIGHT,
            "half_y": schem_module._HALF_MIRROR_Y,
            "top_bottom": schem_module._TOP_BOTTOM,
            "face_y": schem_module._FACE_MIRROR_Y,
        },
        "transform_keys": sorted(schem_module._TRANSFORM_KEYS),
        "direction_words": sorted(schem_module._DIRECTION_WORDS),
        "sturdy": {
            "whole": sorted(connect_faces.WHOLE),
            "sides": dict(sorted(connect_faces.SIDES.items())),
            "by_state": {
                name: {"keys": list(keys),
                       "masks": {",".join(values): mask
                                 for values, mask in table.items()}}
                for name, (keys, table) in sorted(
                    connect_faces.BY_STATE.items())},
            "no_connection_exact": sorted(connect._NO_CONNECTION_EXACT),
            "no_connection_suffixes": sorted(connect._NO_CONNECTION_SUFFIXES),
        },
        # Every block whose default state carries `waterlogged`, from the
        # generated defaults table both twins share -- not a family list.
        "waterloggable": sorted(
            name for name in block_defaults.BLOCK_DEFAULTS
            if place._waterloggable(name)),
        "tall_plants": sorted(place._TALL_PLANTS),
    }


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def history_case():
    """Lazy capture, delete undo, and undo across Save use real region bytes."""
    from heaton_voxel.world import MinecraftSave, OVERWORLD

    with tempfile.TemporaryDirectory(prefix="conformance-history-") as world:
        region_dir = os.path.join(world, "dimensions", "minecraft", "overworld", "region")
        os.makedirs(region_dir)
        region_path = os.path.join(region_dir, "r.-1.0.mca")
        shutil.copyfile(os.path.join(_HERE, "r.-1.0.mca"), region_path)
        shutil.copyfile(os.path.join(_HERE, "level.dat"), os.path.join(world, "level.dat"))
        save = MinecraftSave.load(world)
        result = {}

        def capture(name):
            dim = save.dimension(OVERWORLD)
            chunk = dim.chunk(-25, 1)
            result[name] = {
                "level": sha256(encode(save.level.root, save.level.root_name)),
                "chunk": (sha256(encode(chunk.data, chunk.data_name))
                          if chunk is not None else None),
                "chunks": len(dim.region(-1, 0).chunks),
            }

        # Begin before capture opens the region: the record must discover it.
        edit = save.begin_edit()
        capture("before")
        save.fill((-400, 90, 20), (-398, 92, 22), "minecraft:gold_block")
        save.set_allow_commands(not save.allow_commands())
        edit.complete()
        capture("edited")
        deletion = save.begin_edit()
        save.delete_chunks((-400, 0, 16), (-385, 0, 31), dimension=OVERWORLD)
        deletion.complete()
        capture("deleted")
        deletion.undo()
        capture("delete_undone")
        save.save()
        edit.undo()
        capture("undo_after_save")
        save.save()
        with open(region_path, "rb") as handle:
            result["restored_region"] = sha256(handle.read())
        edit.redo()
        capture("redone")
        # Chunks built from nothing, in a region file the world lacks: the
        # record names that file, and taking them back leaves none behind.
        save.save()
        built = save.begin_edit()
        save.create_chunks((640, 0, 640), (655, 0, 655))
        built.complete()
        paths = [os.path.relpath(path, world).replace(os.sep, "/") for path in built.paths]
        dim = save.dimension(OVERWORLD)
        built.undo()
        result["built"] = {
            "paths": paths,
            "undone_absent": dim.chunk(40, 40) is None,
            "undone_written": save.save(),
            "undone_file": os.path.exists(os.path.join(world, *paths[0].split("/"))),
        }
        built.redo()
        result["built"]["redone_present"] = dim.chunk(40, 40) is not None
        return result


def save_hook_case():
    """What save(before_write=) is offered: every file the save writes, as
    it stood -- an edited region, a region built from nothing, level.dat --
    nothing on a save with nothing to write, and a refusal that stops the
    save with the file it was offered untouched."""
    from heaton_voxel.world import MinecraftSave

    with tempfile.TemporaryDirectory(prefix="conformance-hook-") as world:
        region_dir = os.path.join(world, "dimensions", "minecraft", "overworld", "region")
        os.makedirs(region_dir)
        region_path = os.path.join(region_dir, "r.-1.0.mca")
        shutil.copyfile(os.path.join(_HERE, "r.-1.0.mca"), region_path)
        shutil.copyfile(os.path.join(_HERE, "level.dat"), os.path.join(world, "level.dat"))
        save = MinecraftSave.load(world)
        save.fill((-400, 90, 20), (-398, 92, 22), "minecraft:gold_block")
        save.set_allow_commands(not save.allow_commands())
        save.create_chunks((640, 0, 640), (655, 0, 655))
        offered = []

        def record(path):
            offered.append({
                "path": os.path.relpath(path, world).replace(os.sep, "/"),
                "existed": os.path.exists(path),
            })

        written = save.save(before_write=record)
        first = sorted(offered, key=lambda entry: entry["path"])
        offered.clear()
        unchanged = save.save(before_write=record)
        save.fill((-400, 93, 20), (-398, 93, 22), "minecraft:stone")
        with open(region_path, "rb") as handle:
            before = sha256(handle.read())

        def refuse(path):
            raise OSError("refused")

        try:
            save.save(before_write=refuse)
            refused = False
        except OSError:
            refused = True
        with open(region_path, "rb") as handle:
            untouched = sha256(handle.read()) == before
        return {
            "written": written,
            "offered": first,
            "unchanged_written": unchanged,
            "unchanged_offered": len(offered),
            "refused": refused,
            "refused_left_file": untouched,
            "then_written": save.save(),
        }


def build_corpus():
    """Every artifact as {relative_path: bytes}; schem goldens are written
    to a scratch directory first because their expectations describe files."""
    scratch = tempfile.mkdtemp(prefix="conformance-schem-")
    try:
        goldens = schem_goldens(scratch)
        deletes, delete_entities, delete_poi, delete_far = chunk_delete_case()
        artifacts = {
            "empty/r.0.0.mca": b"",
            "chunk_deletes.json": dump_json(deletes),
            "chunk_deletes/entities/r.-1.0.mca": delete_entities,
            "chunk_deletes/poi/r.-1.0.mca": delete_poi,
            "chunk_deletes/entities/r.-1.-1.mca": delete_far,
            "nbt_cases.json": dump_json(nbt_cases()),
            "packing.json": dump_json(packing_cases()),
            "varints.json": dump_json(varint_cases()),
            "nibbles.json": dump_json(nibble_cases()),
            "region.json": dump_json(region_expectations()),
            "world.json": dump_json(world_samples()),
            "world_edits.json": dump_json(world_edit_script()),
            "modern.json": dump_json(modern_cases()),
            "legacy.json": dump_json(legacy_cases()),
            "older_chunks.json": dump_json(older_chunk_cases()),
            "old_layout.json": dump_json(old_layout_cases()),
            "block_defaults.json": dump_json(block_defaults_dump()),
            "wall_cover.json": dump_json(wall_cover_dump()),
            "heightmaps.json": dump_json(heightmap_cases()),
            "world_settings.json": dump_json(world_settings_script()),
            "history.json": dump_json(history_case()),
            "save_hook.json": dump_json(save_hook_case()),
            "derived_states.json": dump_json(derived_state_cases()),
            "transforms.json": dump_json(transform_cases()),
            "tables.json": dump_json(table_dump()),
            "snbt.json": dump_rows(snbt_cases()),
            "chunk_creation.json": dump_json(chunk_creation_cases()),
            "schem/expected.json": dump_json(goldens),
        }
        for name in ("sample_v2.schem", "sample_v3.schem",
                     "sample_v3_rot90.schem", "sample.nbt", "sample.litematic"):
            with open(os.path.join(scratch, name), "rb") as handle:
                artifacts[f"schem/{name}"] = handle.read()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    manifest = {
        "corpus_version": CORPUS_VERSION,
        "purpose": "language-neutral conformance suite for ports of "
                   "heaton-voxel; see PORTING.md",
        "conventions": {
            "binary": "lowercase hex",
            "int64": "decimal strings",
            "floats": "IEEE-754 bits as hex in 'bits'; compare bits, "
                      "never values",
            "strings": "non-printable-ASCII strings appear as codepoint "
                       "arrays under 'cp'; in snbt.json only those holding "
                       "a lone surrogate do, and its others are JSON "
                       "strings under 'v', escapes and all",
            "digests": "sha256, lowercase hex; NBT digests are always of "
                       "the UNCOMPRESSED encoding -- compressed bytes are "
                       "not portable across zlib implementations. "
                       "chunk_deletes.json is the one artifact that digests "
                       "whole region files, compressed records and all: "
                       "deleting a chunk recompresses nothing, so those "
                       "bytes are reproducible in any language",
        },
        "artifacts": {
            path: {"sha256": sha256(data), "bytes": len(data)}
            for path, data in sorted(artifacts.items())
        },
    }
    artifacts["manifest.json"] = dump_json(manifest)
    return artifacts


def main() -> int:
    artifacts = build_corpus()
    for path, data in sorted(artifacts.items()):
        target = os.path.join(OUT, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
        print(f"  {path:<28} {len(data):>7,} bytes")
    print(f"wrote {len(artifacts)} artifacts to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
