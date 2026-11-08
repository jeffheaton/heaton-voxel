"""The conformance corpus stays true to the implementation.

``fixtures/conformance/`` is the language-neutral test suite ports verify
against, so nothing may drift: this regenerates the corpus in memory on
every run and compares it with what is checked in. A deliberate behavior
change therefore fails here until ``fixtures/make_conformance.py`` is rerun
and the diff committed -- which is exactly the audit trail a reference
implementation owes its ports.

One nuance: the ``.schem`` goldens are gzip files, and gzip output is not
portable across zlib builds. They (and the manifest digests covering them)
are verified *semantically* -- decode and compare content and uncompressed
payload digests -- while every pure-text artifact must match byte for byte.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import unittest

from fixtures.make_conformance import OUT, build_corpus
from heaton_voxel.schem import Schematic

BYTE_COMPARED = {
    "empty/r.0.0.mca", "nbt_cases.json", "packing.json", "varints.json",
    "nibbles.json", "region.json", "world.json", "world_edits.json",
    "modern.json", "legacy.json", "older_chunks.json", "old_layout.json",
    "block_defaults.json", "wall_cover.json",
    "heightmaps.json",
    "world_settings.json", "history.json", "save_hook.json",
    # The chunk-deletion inputs are byte-compared like everything else here
    # because their chunks are stored uncompressed; a zlib-compressed input
    # would have to join the goldens below instead.
    "chunk_deletes.json", "chunk_deletes/entities/r.-1.0.mca",
    "chunk_deletes/entities/r.-1.-1.mca",
    "chunk_deletes/poi/r.-1.0.mca",
    "derived_states.json",
    "transforms.json", "tables.json", "snbt.json", "chunk_creation.json", "schem/expected.json",
}
ZLIB_DEPENDENT = {
    "manifest.json", "schem/sample_v2.schem", "schem/sample_v3.schem",
    "schem/sample_v3_rot90.schem", "schem/sample.nbt", "schem/sample.litematic",
}


def read(path: str) -> bytes:
    with open(os.path.join(OUT, path), "rb") as handle:
        return handle.read()


class ConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(OUT):
            raise unittest.SkipTest(
                "fixtures/conformance/ is missing; run "
                "python fixtures/make_conformance.py"
            )
        cls.regenerated = build_corpus()

    def test_manifest_covers_exactly_the_expected_artifacts(self):
        manifest = json.loads(read("manifest.json"))
        self.assertEqual(
            set(manifest["artifacts"]), BYTE_COMPARED | ZLIB_DEPENDENT - {"manifest.json"}
        )

    def test_checked_in_files_match_their_manifest_digests(self):
        manifest = json.loads(read("manifest.json"))
        for path, entry in manifest["artifacts"].items():
            data = read(path)
            self.assertEqual(
                hashlib.sha256(data).hexdigest(), entry["sha256"],
                f"{path} does not match its manifest digest",
            )
            self.assertEqual(len(data), entry["bytes"], path)

    def test_text_artifacts_regenerate_byte_for_byte(self):
        for path in sorted(BYTE_COMPARED):
            with self.subTest(path):
                self.assertEqual(
                    read(path), self.regenerated[path],
                    f"{path} drifted from the implementation; rerun "
                    f"fixtures/make_conformance.py deliberately and commit "
                    f"the diff",
                )

    def test_schem_goldens_match_their_expectations(self):
        expected = json.loads(read("schem/expected.json"))
        for name, spec in expected["files"].items():
            with self.subTest(name):
                raw = read(f"schem/{name}")
                payload = gzip.decompress(raw)
                self.assertEqual(
                    hashlib.sha256(payload).hexdigest(),
                    spec["payload_sha256"],
                    "uncompressed payload digest",
                )
                path = os.path.join(OUT, "schem", name)
                schematic = Schematic.load(path)
                self.assertEqual(list(schematic.size), spec["size"])
                self.assertEqual(list(schematic.offset), spec["offset"])
                self.assertEqual(schematic.data_version, spec["data_version"])
                self.assertEqual(len(schematic.palette), spec["palette_size"])
                self.assertEqual(schematic.has_biomes, spec["has_biomes"])
                self.assertEqual(schematic.source_version,
                                 spec["container_version"])
                for x, y, z, state in spec["block_samples"]:
                    self.assertEqual(str(schematic.get_block(x, y, z)), state)
                for entry in spec["block_entities"]:
                    stored = schematic.get_block_entity(*entry["pos"])
                    self.assertEqual(str(stored["id"]), entry["id"])
                for want, got in zip(spec["entities"], schematic.entities):
                    self.assertEqual(str(got["id"]), want["id"])
                    self.assertEqual([float(v) for v in got["Pos"]],
                                     want["pos"])
                    self.assertEqual(int(got["Facing"]), want["facing_byte"])
                    self.assertEqual([int(v) for v in got["block_pos"]],
                                     want["block_pos"])
                for x, y, z, biome in spec["biome_samples"]:
                    self.assertEqual(schematic.get_biome(x, y, z), biome)

    def test_a_port_can_verify_the_way_the_manifest_says(self):
        """Consume one vector of each kind exactly as a port would."""
        import heaton_voxel.nbt as nbt
        from heaton_voxel.blocks import pack_indices, unpack_nibbles
        from heaton_voxel.nbt import TagByteArray, decode, encode

        case = json.loads(read("nbt_cases.json"))["cases"][0]
        name, root = decode(bytes.fromhex(case["binary"]))
        self.assertEqual(name, case["root_name"])
        self.assertEqual(encode(root, name).hex(), case["binary"])

        packing = json.loads(read("packing.json"))["cases"][0]
        packed = pack_indices(packing["values"], packing["count"],
                              packing["bits"])
        self.assertEqual([str(int(w)) for w in packed], packing["longs"])

        nibbles = json.loads(read("nibbles.json"))["full"]
        raw = bytes.fromhex(nibbles["bytes"])
        values = unpack_nibbles(
            TagByteArray(v - 256 if v > 127 else v for v in raw))
        self.assertEqual(values[:32], nibbles["first_values"])
        self.assertEqual(hashlib.sha256(bytes(values)).hexdigest(),
                         nibbles["values_sha256"])

        region = json.loads(read("region.json"))
        path = os.path.join(OUT, region["file"])
        loaded = nbt.NBT.load(path)
        with open(path, "rb") as handle:
            expected = handle.read()
        self.assertEqual(loaded.to_bytes(), expected,
                         "untouched round trip must be byte-identical")
        first = region["chunks"][0]
        chunk = loaded.chunks[(first["x"], first["z"])]
        payload = encode(chunk.data, chunk.data_name)
        self.assertEqual(hashlib.sha256(payload).hexdigest(),
                         first["payload_sha256"])

        empty = nbt.NBT.load(os.path.join(OUT, region["empty_region"]["file"]))
        self.assertEqual(empty.kind, "region")
        self.assertEqual(len(empty.chunks), 0)
        self.assertEqual(empty.to_bytes(), b"",
                         "an empty region stays zero bytes")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
