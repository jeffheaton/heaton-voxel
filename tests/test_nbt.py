"""Tests for the NBT reader/writer.

The tests that need real Minecraft data skip themselves when the save is not
present, so the suite still runs on a machine without the game installed.
"""

from __future__ import annotations

import os
import stat
import struct
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from unittest import mock

import heaton_voxel.nbt as nbt
from heaton_voxel.nbt import (
    NBT,
    Chunk,
    NBTError,
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
    decode_modified_utf8,
    encode,
    encode_modified_utf8,
)

from fixtures import FIXTURE_LEVEL_DAT, FIXTURE_REGION, snapshot_region

# The suite runs against the vendored fixture -- real Minecraft output, but
# checked in, so the tests are self-contained and deterministic. Point
# MINECRAFT_SAVE at a world to additionally exercise a live save.
SAVE = os.environ.get("MINECRAFT_SAVE")
SOURCE_REGION = (
    os.path.join(SAVE, "dimensions/minecraft/overworld/region/r.0.0.mca")
    if SAVE
    else FIXTURE_REGION
)
LEVEL_DAT = os.path.join(SAVE, "level.dat") if SAVE else FIXTURE_LEVEL_DAT

# Tests work from a private copy, never the original. A world that is open in
# Minecraft has its region files rewritten underneath us -- chunks move between
# sectors and the file grows mid-read -- which would make these fail at random.
# The copy also guarantees nothing here can write to a real world.
REGION = None
_SNAPSHOT_DIR = None


def setUpModule():
    global REGION, _SNAPSHOT_DIR
    REGION, _SNAPSHOT_DIR = snapshot_region(SOURCE_REGION)


def tearDownModule():
    if _SNAPSHOT_DIR:
        shutil.rmtree(_SNAPSHOT_DIR, ignore_errors=True)


class ModifiedUTF8Tests(unittest.TestCase):
    """Java's writeUTF encoding, which is not standard UTF-8."""

    CASES = [
        ("", b""),
        ("hello", b"hello"),
        # NUL is C0 80, never a bare zero byte.
        ("\x00", b"\xc0\x80"),
        ("a\x00b", b"a\xc0\x80b"),
        ("é", b"\xc3\xa9"),
        ("€", b"\xe2\x82\xac"),
        # Astral characters become a CESU-8 surrogate pair, not 4-byte UTF-8.
        ("\U0001d11e", b"\xed\xa0\xb4\xed\xb4\x9e"),
        ("minecraft:stone", b"minecraft:stone"),
    ]

    def test_encode(self):
        for text, expected in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(encode_modified_utf8(text), expected)

    def test_decode(self):
        for text, encoded in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(decode_modified_utf8(encoded), text)

    def test_differs_from_standard_utf8(self):
        """The whole reason this codec exists."""
        for text in ("\x00", "\U0001d11e"):
            self.assertNotEqual(encode_modified_utf8(text), text.encode("utf-8"))

    def test_lone_surrogate_round_trips(self):
        text = "\ud800"
        self.assertEqual(decode_modified_utf8(encode_modified_utf8(text)), text)

    def test_non_canonical_encoding_is_preserved(self):
        """An overlong 'A' decodes correctly but must be written back as-is."""
        overlong = b"\xc1\x81"  # 2-byte form of U+0041
        self.assertEqual(decode_modified_utf8(overlong), "A")
        payload = _string_tag_payload(overlong)
        _, root = decode(payload)
        self.assertEqual(root["s"], "A")
        self.assertEqual(encode(root), payload)

    def test_bad_lead_byte_rejected(self):
        with self.assertRaises(NBTError):
            decode_modified_utf8(b"\xff")

    def test_truncated_sequence_rejected(self):
        with self.assertRaises(NBTError):
            decode_modified_utf8(b"\xe2\x82")


def _string_tag_payload(raw_string_bytes: bytes) -> bytes:
    """Hand-assemble an NBT payload holding one string with exact bytes."""
    out = bytearray()
    out.append(nbt.TAG_COMPOUND)
    out += struct.pack(">H", 0)  # root name ""
    out.append(nbt.TAG_STRING)
    out += struct.pack(">H", 1) + b"s"
    out += struct.pack(">H", len(raw_string_bytes)) + raw_string_bytes
    out.append(nbt.TAG_END)
    return bytes(out)


class TagRoundTripTests(unittest.TestCase):
    def assert_round_trips(self, root: TagCompound, name: str = ""):
        data = encode(root, name)
        decoded_name, decoded = decode(data)
        self.assertEqual(decoded_name, name)
        self.assertEqual(encode(decoded, decoded_name), data)
        return decoded

    def test_all_scalar_types(self):
        root = TagCompound(
            {
                "byte": TagByte(-128),
                "short": TagShort(-32768),
                "int": TagInt(-2147483648),
                "long": TagLong(-9223372036854775808),
                "float": TagFloat(0.5),
                "double": TagDouble(-1.25e300),
                "string": TagString("hello €"),
            }
        )
        out = self.assert_round_trips(root, "Level")
        self.assertEqual(out["byte"], -128)
        self.assertEqual(out["long"], -9223372036854775808)
        self.assertEqual(out["string"], "hello €")

    def test_arrays(self):
        root = TagCompound(
            {
                "bytes": TagByteArray([-128, -1, 0, 1, 127]),
                "ints": TagIntArray([-2147483648, 0, 2147483647]),
                "longs": TagLongArray([-9223372036854775808, 0, 9223372036854775807]),
                "empty": TagIntArray([]),
            }
        )
        out = self.assert_round_trips(root)
        self.assertEqual(list(out["bytes"]), [-128, -1, 0, 1, 127])
        self.assertEqual(list(out["ints"]), [-2147483648, 0, 2147483647])
        self.assertEqual(
            list(out["longs"]), [-9223372036854775808, 0, 9223372036854775807]
        )
        self.assertEqual(len(out["empty"]), 0)

    def test_nested_containers(self):
        root = TagCompound(
            {
                "list_of_compounds": TagList(
                    [TagCompound({"n": TagInt(i)}) for i in range(3)],
                    element_type=nbt.TAG_COMPOUND,
                ),
                "list_of_lists": TagList(
                    [TagList([TagInt(1)], element_type=nbt.TAG_INT)],
                    element_type=nbt.TAG_LIST,
                ),
                "deep": TagCompound({"a": TagCompound({"b": TagCompound({})})}),
            }
        )
        out = self.assert_round_trips(root)
        self.assertEqual(out["list_of_compounds"][2]["n"], 2)
        self.assertEqual(out["list_of_lists"][0][0], 1)

    def test_empty_list_element_type_is_preserved(self):
        """An empty list's declared type is the only record of what it held."""
        for declared in (nbt.TAG_END, nbt.TAG_COMPOUND, nbt.TAG_STRING):
            with self.subTest(declared=declared):
                root = TagCompound({"l": TagList([], element_type=declared)})
                out = self.assert_round_trips(root)
                self.assertEqual(out["l"].element_type, declared)

    def test_float_specials(self):
        root = TagCompound(
            {
                "neg_zero": TagDouble(-0.0),
                "inf": TagFloat(float("inf")),
                "ninf": TagDouble(float("-inf")),
                "nan": TagDouble(float("nan")),
                "tiny": TagDouble(5e-324),
            }
        )
        data = encode(root)
        _, out = decode(data)
        self.assertEqual(encode(out), data)
        # -0.0 must stay negative, not collapse to 0.0.
        self.assertEqual(struct.pack(">d", out["neg_zero"]), struct.pack(">d", -0.0))

    def test_nan_payload_bits_are_preserved(self):
        """A signaling NaN's payload does not survive float32 -> float64 ->
        float32, so the reader keeps the original bytes."""
        raw_bits = b"\x7f\x80\x00\x01"  # signaling NaN with payload 1
        payload = bytearray()
        payload.append(nbt.TAG_COMPOUND)
        payload += struct.pack(">H", 0)
        payload.append(nbt.TAG_FLOAT)
        payload += struct.pack(">H", 1) + b"f"
        payload += raw_bits
        payload.append(nbt.TAG_END)
        payload = bytes(payload)

        _, root = decode(payload)
        self.assertEqual(encode(root), payload)

    def test_unwrapped_value_is_rejected_clearly(self):
        root = TagCompound({"oops": 5})
        with self.assertRaises(NBTError) as ctx:
            encode(root)
        self.assertIn("TagInt", str(ctx.exception))

    def test_root_name_is_preserved(self):
        data = encode(TagCompound({"x": TagInt(1)}), "MyRoot")
        name, _ = decode(data)
        self.assertEqual(name, "MyRoot")

    def test_trailing_bytes_rejected(self):
        with self.assertRaises(NBTError):
            decode(encode(TagCompound({})) + b"\x00")

    def test_non_compound_root_rejected(self):
        with self.assertRaises(NBTError):
            decode(b"\x03")


class StandaloneFileTests(unittest.TestCase):
    def _write_and_reload(self, compression: int):
        source = NBT()
        source.kind = "nbt"
        source.compression = compression
        source.root_name = ""
        source.root = TagCompound({"Data": TagCompound({"Name": TagString("hi")})})

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.dat")
            source.save(path)
            reloaded = NBT.load(path)
            self.assertEqual(reloaded.kind, "nbt")
            self.assertEqual(reloaded.compression, compression)
            self.assertEqual(reloaded.root["Data"]["Name"], "hi")

    def test_gzip(self):
        self._write_and_reload(nbt.COMPRESSION_GZIP)

    def test_zlib(self):
        self._write_and_reload(nbt.COMPRESSION_ZLIB)

    def test_uncompressed(self):
        self._write_and_reload(nbt.COMPRESSION_NONE)

    @unittest.skipUnless(os.path.exists(LEVEL_DAT), "level.dat not available")
    def test_real_level_dat_round_trips(self):
        with open(LEVEL_DAT, "rb") as handle:
            original = handle.read()
        level = NBT.load(LEVEL_DAT)
        self.assertEqual(level.kind, "nbt")
        self.assertEqual(level.to_bytes(), original)

        # And the NBT layer itself is exact, independent of blob reuse.
        payload = nbt._decompress(level.compression, original)
        self.assertEqual(encode(level.root, level.root_name), payload)


@unittest.skipUnless(os.path.exists(SOURCE_REGION), "region file not available")
class RegionFileTests(unittest.TestCase):
    """Exercised against real Minecraft region data."""

    @classmethod
    def setUpClass(cls):
        with open(REGION, "rb") as handle:
            cls.original = handle.read()
        cls.region = NBT.load(REGION)
        # Which chunks exist depends on the region, so never assume (0, 0).
        cls.any_key = next(iter(cls.region.chunks))

    def test_loaded_shape(self):
        self.assertEqual(self.region.kind, "region")
        # Region coordinates come from the file name.
        name = os.path.basename(REGION)
        expected = tuple(int(part) for part in name.split(".")[1:3])
        self.assertEqual((self.region.region_x, self.region.region_z), expected)
        self.assertTrue(self.region.chunks)
        for (x, z), chunk in self.region.chunks.items():
            self.assertTrue(0 <= x < 32 and 0 <= z < 32)
            self.assertEqual((chunk.local_x, chunk.local_z), (x, z))

    def test_every_chunk_parsed(self):
        failures = [c for c in self.region if c.parse_error]
        self.assertEqual(failures, [], f"{len(failures)} chunks failed to parse")
        self.assertTrue(all(c.data is not None for c in self.region))

    def test_nbt_layer_is_exactly_lossless(self):
        """Re-encode every chunk from its parsed tree and compare against the
        decompressed source. This bypasses the original-bytes reuse in
        Chunk.payload(), so it tests the encoder rather than the cache."""
        checked = 0
        for chunk in self.region:
            source = zlib.decompress(chunk._blob)
            self.assertEqual(
                encode(chunk.data, chunk.data_name),
                source,
                f"chunk ({chunk.local_x}, {chunk.local_z}) did not re-encode exactly",
            )
            checked += 1
        self.assertGreater(checked, 0)

    def test_file_round_trip_is_byte_identical(self):
        self.assertEqual(self.region.to_bytes(), self.original)

    def test_untouched_save_does_no_decoding(self):
        """Loading is lazy, so a straight copy never builds a single tag."""
        lazy = NBT.load(REGION)
        self.assertTrue(all(not c._decoded for c in lazy))
        self.assertEqual(lazy.to_bytes(), self.original)
        self.assertTrue(
            all(not c._decoded for c in lazy), "save() decoded chunks it did not need"
        )

    def test_eager_load_decodes_everything(self):
        eager = NBT.load(REGION, eager=True)
        self.assertTrue(all(c._decoded for c in eager))
        self.assertTrue(all(c.loaded for c in eager))
        self.assertEqual(eager.to_bytes(), self.original)

    def test_release_frees_the_tree_and_keeps_bytes(self):
        region = NBT.load(REGION)
        chunk = region.chunks[self.any_key]
        status = str(chunk.data["Status"])
        self.assertTrue(chunk.loaded)

        chunk.release()
        self.assertFalse(chunk.loaded)
        self.assertFalse(chunk._decoded)
        # Re-reads transparently, and the file is still byte-exact.
        self.assertEqual(str(chunk.data["Status"]), status)
        self.assertEqual(region.to_bytes(), self.original)

    def test_release_flushes_pending_edits(self):
        """Releasing a modified chunk must not discard the modification."""
        region = NBT.load(REGION)
        chunk = region.chunks[self.any_key]
        chunk.data["Marker"] = TagString("kept")
        chunk.release()
        self.assertFalse(chunk.loaded)
        self.assertEqual(chunk.data["Marker"], "kept")

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, os.path.basename(REGION))
            region.save(path)
            self.assertEqual(NBT.load(path).chunks[self.any_key].data["Marker"], "kept")

    def test_streaming_pass_stays_flat_in_memory(self):
        """The whole-world access pattern: touch every chunk, release each."""
        import resource

        region = NBT.load(REGION)
        base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        for chunk in region:
            self.assertIsNotNone(chunk.data["Status"])
            chunk.release()
        growth = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - base) / 1024 / 1024
        # An eager load of this region costs ~139 MiB; streaming must not.
        self.assertLess(growth, 40, f"streaming pass grew {growth:.0f} MiB")
        self.assertEqual(region.to_bytes(), self.original)

    def test_sector_slack_bytes_are_preserved(self):
        """Minecraft leaves stale data in the tail of a chunk's last sector.
        Confirm this file actually has some, and that we keep it."""
        with_slack = [c for c in self.region if c._padding.strip(b"\x00")]
        self.assertTrue(with_slack, "expected some chunks with non-zero slack")
        rebuilt = self.region.to_bytes()
        for chunk in with_slack:
            end = (chunk._sector_offset + chunk._sector_count) * nbt.SECTOR
            start = end - len(chunk._padding)
            self.assertEqual(rebuilt[start:end], chunk._padding)

    def test_timestamps_preserved(self):
        rebuilt = self.region.to_bytes()
        self.assertEqual(
            rebuilt[nbt.SECTOR : 2 * nbt.SECTOR],
            self.original[nbt.SECTOR : 2 * nbt.SECTOR],
        )

    def test_chunk_accessor(self):
        (x, z), chunk = next(iter(self.region.chunks.items()))
        self.assertIs(self.region.chunk(x, z), chunk)
        self.assertIs(self.region.chunk(x + 32, z + 64), chunk)

    def test_modification_survives_save_and_reload(self):
        region = NBT.load(REGION)
        target = next(iter(region.chunks.values()))
        target.data["ClaudeTestMarker"] = TagString("hello world")
        target.timestamp = 1234567890

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            region.save(path)
            reloaded = NBT.load(path)

        again = reloaded.chunk(target.local_x, target.local_z)
        self.assertEqual(again.data["ClaudeTestMarker"], "hello world")
        self.assertEqual(again.timestamp, 1234567890)
        # Untouched chunks must come back unchanged.
        for key, chunk in reloaded.chunks.items():
            if key == (target.local_x, target.local_z):
                continue
            self.assertEqual(
                encode(chunk.data, chunk.data_name),
                encode(self.region.chunks[key].data, self.region.chunks[key].data_name),
            )

    def test_growth_forces_repack_and_stays_readable(self):
        """Add enough data that chunks no longer fit their original sectors."""
        region = NBT.load(REGION)
        for chunk in list(region.chunks.values())[:40]:
            chunk.data["Ballast"] = TagByteArray(bytes(200_000))

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            region.save(path)
            self.assertEqual(os.path.getsize(path) % nbt.SECTOR, 0)
            reloaded = NBT.load(path)

        self.assertEqual(set(reloaded.chunks), set(region.chunks))
        self.assertEqual([c for c in reloaded if c.parse_error], [])
        for key, chunk in reloaded.chunks.items():
            self.assertEqual(
                encode(chunk.data, chunk.data_name),
                encode(region.chunks[key].data, region.chunks[key].data_name),
            )

    def test_sectors_do_not_overlap_after_repack(self):
        region = NBT.load(REGION)
        # Force a repack by making one chunk too big for its slot.
        victim = next(iter(region.chunks.values()))
        victim._blob = zlib.compress(bytes(400_000), 6)
        victim._sector_count = 1

        data = region.to_bytes()
        seen = {}
        locations = data[: nbt.SECTOR]
        for index in range(1024):
            offset = int.from_bytes(locations[index * 4 : index * 4 + 3], "big")
            count = locations[index * 4 + 3]
            if not offset:
                continue
            self.assertGreaterEqual(offset, 2, "chunk overlaps the header")
            for sector in range(offset, offset + count):
                self.assertNotIn(sector, seen, f"sector {sector} used twice")
                seen[sector] = index
        self.assertEqual(len(data) % nbt.SECTOR, 0)

    def test_deleting_a_chunk(self):
        region = NBT.load(REGION)
        victim = next(iter(region.chunks))
        del region.chunks[victim]

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            region.save(path)
            reloaded = NBT.load(path)
        self.assertNotIn(victim, reloaded.chunks)
        self.assertEqual(len(reloaded.chunks), len(self.region.chunks) - 1)


class SaveSafetyTests(unittest.TestCase):
    """Atomic writes and the session.lock interlock."""

    def _small_region(self):
        region = NBT()
        region.kind = "region"
        region.region_x, region.region_z = 0, 0
        chunk = Chunk(0, 0)
        chunk.data = TagCompound({"Status": TagString("full")})
        region.chunks[(0, 0)] = chunk
        return region

    def test_interrupted_save_leaves_the_original_intact(self):
        """The whole point of writing via a temp file and renaming."""
        region = self._small_region()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            region.save(path)
            with open(path, "rb") as handle:
                original = handle.read()

            # Grow the region so the new content differs, then fail the rename.
            region.chunks[(0, 0)].data["Extra"] = TagString("x" * 5000)
            with mock.patch("os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    region.save(path)

            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), original, "original clobbered")
            leftovers = [f for f in os.listdir(tmp) if f.startswith(".nbt-")]
            self.assertEqual(leftovers, [], "temp file left behind")

    def test_save_leaves_no_temp_files(self):
        region = self._small_region()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            region.save(path)
            self.assertEqual(os.listdir(tmp), ["r.0.0.mca"])

    def test_permissions_are_preserved(self):
        """mkstemp makes 0600 files; the original's mode has to win."""
        region = self._small_region()
        for mode in (0o644, 0o600):
            with self.subTest(mode=oct(mode)):
                with tempfile.TemporaryDirectory() as tmp:
                    path = os.path.join(tmp, "r.0.0.mca")
                    region.save(path)
                    os.chmod(path, mode)
                    region.chunks[(0, 0)].data["N"] = TagInt(mode)
                    region.save(path)
                    self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), mode)

    def test_refuses_to_write_into_an_open_world(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = os.path.join(tmp, "world")
            os.makedirs(os.path.join(world, "region"))
            lock = os.path.join(world, nbt.SESSION_LOCK)
            with open(lock, "wb") as handle:
                handle.write("☃".encode())

            region = self._small_region()
            path = os.path.join(world, "region", "r.0.0.mca")
            self.assertEqual(nbt.find_world_root(path), world)

            # Unlocked: saving is fine.
            self.assertFalse(nbt.world_is_open(world))
            region.save(path)

            # Locked by another process, exactly as Minecraft holds it.
            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import fcntl,os,sys,time;"
                    "fd=os.open(sys.argv[1],os.O_RDWR);"
                    "fcntl.lockf(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);"
                    "print('held',flush=True);time.sleep(60)",
                    lock,
                ],
                stdout=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(holder.stdout.readline().strip(), "held")
                self.assertTrue(nbt.world_is_open(world))
                with self.assertRaises(NBTError) as ctx:
                    region.save(path)
                self.assertIn(nbt.SESSION_LOCK, str(ctx.exception))
                # force=True is the escape hatch.
                region.save(path, force=True)
            finally:
                holder.kill()
                holder.wait()
                holder.stdout.close()

            self.assertFalse(nbt.world_is_open(world))

    def test_saving_outside_a_world_is_unaffected(self):
        """No session.lock anywhere up the tree means no interlock."""
        region = self._small_region()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            self.assertIsNone(nbt.find_world_root(path))
            region.save(path)
            self.assertTrue(os.path.exists(path))

    def test_touch_modified_only_bumps_changed_chunks(self):
        region = self._small_region()
        region.chunks[(0, 0)].timestamp = 1000
        other = Chunk(1, 1, timestamp=2000)
        other.data = TagCompound({"Status": TagString("full")})
        region.chunks[(1, 1)] = other

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            region.save(path)

            reloaded = NBT.load(path)
            reloaded.chunk(0, 0).data["Changed"] = TagString("yes")
            before = int(time.time())
            reloaded.save(path, touch_modified=True)

            final = NBT.load(path)
            self.assertGreaterEqual(final.chunk(0, 0).timestamp, before)
            self.assertEqual(final.chunk(1, 1).timestamp, 2000, "untouched chunk bumped")

    def test_timestamps_untouched_by_default(self):
        """The default must keep round-tripping byte-exact."""
        region = self._small_region()
        region.chunks[(0, 0)].timestamp = 1234
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            region.save(path)
            reloaded = NBT.load(path)
            reloaded.chunk(0, 0).data["Changed"] = TagString("yes")
            reloaded.save(path)
            self.assertEqual(NBT.load(path).chunk(0, 0).timestamp, 1234)


class RegionEdgeCaseTests(unittest.TestCase):
    def test_empty_region(self):
        region = NBT()
        region.kind = "region"
        data = region.to_bytes()
        self.assertEqual(len(data), 2 * nbt.SECTOR)
        self.assertEqual(data, bytes(2 * nbt.SECTOR))

    def test_build_region_from_scratch(self):
        region = NBT()
        region.kind = "region"
        for x, z in ((0, 0), (5, 9), (31, 31)):
            chunk = Chunk(x, z, timestamp=1700000000)
            chunk.data = TagCompound(
                {"xPos": TagInt(x), "zPos": TagInt(z), "Status": TagString("full")}
            )
            region.chunks[(x, z)] = chunk

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            region.save(path)
            reloaded = NBT.load(path)
            with open(path, "rb") as handle:
                on_disk = handle.read()

        self.assertEqual(len(reloaded.chunks), 3)
        for x, z in ((0, 0), (5, 9), (31, 31)):
            chunk = reloaded.chunk(x, z)
            self.assertEqual(chunk.data["xPos"], x)
            self.assertEqual(chunk.data["Status"], "full")
            self.assertEqual(chunk.timestamp, 1700000000)
        self.assertEqual(reloaded.to_bytes(), on_disk)

    def test_zero_length_region_is_valid_and_stays_zero_length(self):
        """Minecraft writes 0-byte region files (common under poi/); they are
        empty regions, not corruption, and must not grow a header on save."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.1.0.mca")
            open(path, "wb").close()

            region = NBT.load(path)
            self.assertEqual(region.kind, "region")
            self.assertEqual(region.chunks, {})
            self.assertEqual(region.to_bytes(), b"")

            region.save(path)
            self.assertEqual(os.path.getsize(path), 0)

    def test_zero_length_region_grows_when_a_chunk_is_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.1.0.mca")
            open(path, "wb").close()

            region = NBT.load(path)
            chunk = Chunk(3, 4, timestamp=42)
            chunk.data = TagCompound({"Status": TagString("full")})
            region.chunks[(3, 4)] = chunk
            region.save(path)

            reloaded = NBT.load(path)
            self.assertEqual(len(reloaded.chunks), 1)
            self.assertEqual(reloaded.chunk(3, 4).data["Status"], "full")
            self.assertEqual(reloaded.chunk(3, 4).timestamp, 42)
            self.assertEqual(reloaded.to_bytes(), reloaded.to_bytes())

    def test_truncated_region_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            with open(path, "wb") as handle:
                handle.write(bytes(100))
            with self.assertRaises(NBTError):
                NBT.load(path)

    def test_out_of_range_sector_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            data = bytearray(2 * nbt.SECTOR)
            data[0:3] = (9999).to_bytes(3, "big")  # points past the end
            data[3] = 1
            with open(path, "wb") as handle:
                handle.write(data)
            with self.assertRaises(NBTError) as ctx:
                NBT.load(path)
            self.assertIn("outside", str(ctx.exception))

    def test_corrupt_chunk_keeps_its_bytes(self):
        """A chunk we cannot decode must still survive a save."""
        region = NBT()
        region.kind = "region"
        chunk = Chunk(0, 0)
        chunk._blob = b"not valid zlib data"
        chunk.compression = nbt.COMPRESSION_ZLIB
        region.chunks[(0, 0)] = chunk

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            region.save(path)
            reloaded = NBT.load(path)
            with open(path, "rb") as handle:
                on_disk = handle.read()

        broken = reloaded.chunk(0, 0)
        self.assertIsNotNone(broken.parse_error)
        self.assertIsNone(broken.data)
        self.assertEqual(broken._blob, b"not valid zlib data")
        # And it round-trips again from there.
        self.assertEqual(reloaded.to_bytes(), on_disk)

    def _oversized_region(self):
        """A region holding a chunk too big for the 255-sector limit.

        Random bytes so zlib cannot shrink it back under the threshold.
        """
        region = NBT()
        region.kind = "region"
        region.region_x, region.region_z = 0, 0
        chunk = Chunk(2, 3, timestamp=99)
        chunk.data = TagCompound(
            {
                "Marker": TagString("oversized"),
                "Bulk": TagByteArray(os.urandom(1_500_000)),
            }
        )
        region.chunks[(2, 3)] = chunk
        return region, chunk

    def test_oversized_chunk_moves_to_mcc_sidecar(self):
        region, chunk = self._oversized_region()

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            region.save(path)

            # Chunk 2,3 of region 0,0 is absolute chunk 2,3.
            sidecar = os.path.join(tmp, "c.2.3.mcc")
            self.assertTrue(os.path.exists(sidecar), os.listdir(tmp))
            self.assertTrue(chunk.external)
            # The region file itself now holds only a small stub record.
            self.assertLessEqual(os.path.getsize(path), 3 * nbt.SECTOR)

            reloaded = NBT.load(path)

        back = reloaded.chunk(2, 3)
        self.assertTrue(back.external)
        self.assertEqual(back.timestamp, 99)
        self.assertEqual(back.data["Marker"], "oversized")
        self.assertEqual(
            bytes(back.data["Bulk"].tobytes()), bytes(chunk.data["Bulk"].tobytes())
        )

    def test_oversized_chunk_refuses_to_silently_drop_payload(self):
        """to_bytes() cannot write sidecars, so it must not pretend to."""
        region, _ = self._oversized_region()
        with self.assertRaises(NBTError) as ctx:
            region.to_bytes()
        self.assertIn("save()", str(ctx.exception))

    def test_shrunken_chunk_returns_from_its_sidecar(self):
        """A chunk that drops back under the limit should stop being external,
        and its orphaned .mcc should not be left lying around."""
        region, _ = self._oversized_region()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            region.save(path)
            sidecar = os.path.join(tmp, "c.2.3.mcc")
            self.assertTrue(os.path.exists(sidecar))

            reloaded = NBT.load(path)
            self.assertTrue(reloaded.chunk(2, 3).external)
            # Shrink it back to something that fits inside the region.
            reloaded.chunk(2, 3).data = TagCompound({"Marker": TagString("small")})
            reloaded.save(path)

            self.assertFalse(reloaded.chunk(2, 3).external)
            self.assertFalse(os.path.exists(sidecar), "orphaned .mcc left behind")

            final = NBT.load(path)
            self.assertFalse(final.chunk(2, 3).external)
            self.assertEqual(final.chunk(2, 3).data["Marker"], "small")

    def test_saving_a_copy_never_deletes_the_original_sidecar(self):
        region, _ = self._oversized_region()
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = os.path.join(tmp, "source")
            other_dir = os.path.join(tmp, "other")
            os.makedirs(source_dir)
            os.makedirs(other_dir)
            region.save(os.path.join(source_dir, "r.0.0.mca"))
            sidecar = os.path.join(source_dir, "c.2.3.mcc")
            self.assertTrue(os.path.exists(sidecar))

            loaded = NBT.load(os.path.join(source_dir, "r.0.0.mca"))
            loaded.chunk(2, 3).data = TagCompound({"Marker": TagString("small")})
            loaded.save(os.path.join(other_dir, "r.0.0.mca"))

            self.assertTrue(os.path.exists(sidecar), "deleted a sidecar it does not own")

    def test_before_write_sees_a_sidecar_before_its_region_and_a_stale_one_after(self):
        region, _ = self._oversized_region()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            sidecar = os.path.join(tmp, "c.2.3.mcc")
            offered = []
            region.save(path, before_write=offered.append)
            self.assertEqual(offered, [os.path.abspath(sidecar), os.path.abspath(path)])

            reloaded = NBT.load(path)
            reloaded.chunk(2, 3).data = TagCompound({"Marker": TagString("small")})
            offered.clear()
            reloaded.save(path, skip_unchanged=True, before_write=offered.append)
            self.assertEqual(offered, [os.path.abspath(path), os.path.abspath(sidecar)])
            self.assertFalse(os.path.exists(sidecar))

            offered.clear()
            self.assertFalse(reloaded.save(path, skip_unchanged=True,
                                           before_write=offered.append))
            self.assertEqual(offered, [])

    def test_missing_sidecar_is_reported(self):
        region, _ = self._oversized_region()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            region.save(path)
            os.remove(os.path.join(tmp, "c.2.3.mcc"))
            with self.assertRaises(NBTError) as ctx:
                NBT.load(path)
            self.assertIn("missing external chunk", str(ctx.exception))

    def test_save_without_path_raises(self):
        region = NBT()
        region.kind = "region"
        with self.assertRaises(NBTError):
            region.save()


if __name__ == "__main__":
    unittest.main(verbosity=2)
