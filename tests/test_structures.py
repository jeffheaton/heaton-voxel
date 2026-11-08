"""Vanilla structure files, held to the game's own ``StructureTemplate``.

``fixtures/structures.json`` is what ``tools/probe_structures.py`` got back
when it handed the game structure files this library wrote: each loaded by
the game, through its data fixer as the template manager does, and saved
again. The game's copy must read as the schematic that was written -- every
cell, block entity and entity -- and for a file already at the game's
version, the game must have saved back exactly what was written.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from heaton_voxel import nbt  # noqa: E402
from heaton_voxel.nbt import NBTError, TagCompound  # noqa: E402
from heaton_voxel.schem import Schematic  # noqa: E402
from heaton_voxel.schem.structure import read_structure, structure_root  # noqa: E402
from heaton_voxel.snbt import to_snbt  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402

FIXTURE = os.path.join(_ROOT, "fixtures", "structures.json")
with open(FIXTURE, encoding="ascii") as _handle:
    _GAME = json.load(_handle)


def _root(text: str) -> TagCompound:
    return nbt.decode(base64.b64decode(text))[1]


def _cells(schematic: Schematic) -> dict:
    width, height, length = schematic.size
    return {(x, y, z): str(schematic.get_block(x, y, z).canonical())
            for x in range(width) for y in range(height) for z in range(length)}


class GameAgreementTests(unittest.TestCase):
    def test_the_fixture_is_the_game_s(self):
        self.assertEqual(_GAME["game"], "26.3")
        self.assertEqual(len(_GAME["cases"]), 5)

    def test_the_game_s_copy_reads_as_what_was_written(self):
        for row in _GAME["cases"]:
            with self.subTest(case=row["name"]):
                written = read_structure(Schematic, _root(row["written"]), row["name"])
                saved = read_structure(Schematic, _root(row["game"]), row["name"])
                self.assertEqual(saved.size, written.size)
                self.assertEqual(_cells(saved), _cells(written))
                self.assertEqual({k: to_snbt(v) for k, v in saved.block_entities.items()},
                                 {k: to_snbt(v) for k, v in written.block_entities.items()})
                self.assertEqual([to_snbt(e) for e in saved.entities],
                                 [to_snbt(e) for e in written.entities])

    def test_at_the_game_s_version_the_game_saves_back_what_was_written(self):
        for row in _GAME["cases"]:
            ours, game = _root(row["written"]), _root(row["game"])
            if int(ours["DataVersion"]) != _GAME["data_version"]:
                continue
            with self.subTest(case=row["name"]):
                self.assertEqual(to_snbt(ours), to_snbt(game))

    def test_a_file_before_26_3_is_upgraded_by_the_game_to_the_same_cells(self):
        row = next(r for r in _GAME["cases"] if "before 26.3" in r["name"])
        ours, game = _root(row["written"]), _root(row["game"])
        self.assertIn("Name", ours["palette"][0])
        self.assertIn("id", game["palette"][0])
        self.assertEqual(int(game["DataVersion"]), _GAME["data_version"])

    def test_a_file_of_several_palettes_reads_the_first(self):
        row = next(r for r in _GAME["cases"] if "palettes" in r["name"])
        schematic = read_structure(Schematic, _root(row["game"]), row["name"])
        self.assertEqual(schematic.source_palettes, 2)
        self.assertEqual([str(schematic.get_block(x, 0, 0)) for x in range(2)],
                         ["minecraft:stone", "minecraft:dirt"])


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="heaton-voxel-structure-")
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)

    def test_a_cell_left_out_is_a_structure_void_and_back(self):
        schematic = Schematic(2, 1, 1, 5023)
        schematic.set_block(0, 0, 0, "minecraft:structure_void")
        schematic.set_block(1, 0, 0, "minecraft:stone")
        root = structure_root(schematic)
        self.assertEqual(len(root["blocks"]), 1)
        self.assertEqual([str(s.get("id")) for s in root["palette"]], ["minecraft:stone"])
        path = os.path.join(self.directory, "a.nbt")
        schematic.save(path)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(2), b"\x1f\x8b")          # gzip, as the game writes
        back = Schematic.load(path)
        self.assertEqual(back.source_format, "structure")
        self.assertEqual(str(back.get_block(0, 0, 0)), "minecraft:structure_void")

    def test_a_state_is_written_with_every_property_in_the_codec_of_its_version(self):
        for version, key, props in ((5023, "id", "properties"), (4903, "Name", "Properties")):
            schematic = Schematic(1, 1, 1, version)
            schematic.set_block(0, 0, 0, "minecraft:oak_stairs[facing=east]")
            entry = structure_root(schematic)["palette"][0]
            self.assertEqual(str(entry[key]), "minecraft:oak_stairs")
            self.assertEqual(dict((k, str(v)) for k, v in entry[props].items()),
                             {"facing": "east", "half": "bottom", "shape": "straight",
                              "waterlogged": "false"})

    def test_it_pastes_leaving_its_voids_as_the_world_has_them(self):
        from test_create_chunks import _world

        world = _world(4903)
        self.addCleanup(shutil.rmtree, world, ignore_errors=True)
        save = MinecraftSave.load(world)
        save.create_chunks((640, 0, 640), (640, 0, 640))
        save.fill((640, 60, 640), (642, 60, 640), "minecraft:dirt")
        schematic = Schematic(3, 1, 1, 4903)
        schematic.set_block(0, 0, 0, "minecraft:stone")
        schematic.set_block(1, 0, 0, "minecraft:structure_void")
        path = os.path.join(self.directory, "piece.nbt")
        schematic.save(path)
        result = save.import_schem(path, (640, 60, 640))
        self.assertEqual((result.voids_skipped, result.blocks_pasted), (1, 2))
        self.assertEqual([str(save.get_block(x, 60, 640)) for x in (640, 641, 642)],
                         ["minecraft:stone", "minecraft:dirt", "minecraft:air"])
        save.import_schem(path, (640, 60, 640), paste_voids=True)
        self.assertEqual(str(save.get_block(641, 60, 640)), "minecraft:structure_void")

    def test_biomes_have_nowhere_to_go(self):
        schematic = Schematic(1, 1, 1, 5023)
        schematic.fill_biomes("minecraft:plains")
        with self.assertRaisesRegex(NBTError, "nowhere to keep biomes"):
            schematic.save(os.path.join(self.directory, "b.nbt"))

    def test_refusals_name_their_reason(self):
        bad = structure_root(Schematic(1, 1, 1, 5023))
        bad["blocks"][0]["state"] = nbt.TagInt(9)
        with self.assertRaisesRegex(NBTError, "palette entry 9"):
            read_structure(Schematic, bad, "bad")
        old = structure_root(Schematic(1, 1, 1, 5023))
        old["DataVersion"] = nbt.TagInt(2586)
        with self.assertRaisesRegex(NBTError, "predates"):
            read_structure(Schematic, old, "old")
        del old["DataVersion"]
        with self.assertRaisesRegex(NBTError, "no DataVersion"):
            read_structure(Schematic, old, "none")

    def test_a_structure_file_is_told_from_a_schem_by_what_it_holds(self):
        schematic = Schematic(1, 1, 1, 5023)
        path = os.path.join(self.directory, "misnamed.schem")
        with open(path, "wb") as handle:
            handle.write(gzip.compress(nbt.encode(structure_root(schematic))))
        self.assertEqual(Schematic.load(path).source_format, "structure")


if __name__ == "__main__":
    unittest.main()
