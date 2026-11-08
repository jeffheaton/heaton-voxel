"""Litematica schematics, held to a second implementation and to the
mod's own rules.

``fixtures/litematic/litemapy.litematic`` was written by litemapy, which
owes this library nothing, as the mod stores a selection made from its far
corner: a region of negative width and length. ``tools/check_litematic.py``
made it, and checks the other direction, litemapy reading files written
here at every palette width, with litemapy installed; this reads it back
without.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from heaton_voxel import nbt  # noqa: E402
from heaton_voxel.nbt import (  # noqa: E402
    NBTError,
    TagCompound,
    TagDouble,
    TagInt,
    TagList,
    TagString,
)
from heaton_voxel.schem import Schematic  # noqa: E402
from heaton_voxel.schem.litematic import (  # noqa: E402
    _bits,
    litematic_root,
    pack_tight,
    read_litematic,
    unpack_tight,
)

FIXTURE = os.path.join(_ROOT, "fixtures", "litematic")


class LitemapyFileTests(unittest.TestCase):
    def test_a_region_selected_from_its_far_corner_reads_cell_for_cell(self):
        schematic = Schematic.load(os.path.join(FIXTURE, "litemapy.litematic"))
        with open(os.path.join(FIXTURE, "litemapy.json"), encoding="ascii") as handle:
            expected = json.load(handle)
        self.assertEqual(schematic.source_format, "litematic")
        self.assertEqual(schematic.size, (4, 3, 3))
        self.assertEqual(schematic.offset, (2, 1, -4))      # the minimum corner
        for x, y, z, block in expected["cells"]:
            got = schematic.get_block(x - schematic.offset[0], y - schematic.offset[1],
                                      z - schematic.offset[2])
            self.assertEqual(got.name, block, (x, y, z))
        self.assertEqual(str(schematic.metadata["Author"]), "litemapy")


class PackingTests(unittest.TestCase):
    def test_cells_straddle_longs_as_the_mod_packs_them(self):
        # three bits a cell: the 22nd cell runs from bit 63 of the first
        # long into the second
        values = [i % 8 for i in range(50)]
        packed = pack_tight(values, 3)
        self.assertEqual(len(packed), 3)
        self.assertEqual(unpack_tight(packed, 50, 3), values)
        words = [int(v) & ((1 << 64) - 1) for v in packed]
        whole = words[0] | words[1] << 64 | words[2] << 128
        self.assertEqual([(whole >> (3 * i)) & 7 for i in range(50)], values)

    def test_bits_follow_the_palette_as_the_mod_s_container_does(self):
        self.assertEqual([_bits(n) for n in (1, 2, 4, 5, 8, 9, 16, 17, 33)],
                         [2, 2, 2, 3, 3, 4, 4, 5, 6])

    def test_short_block_data_is_refused(self):
        with self.assertRaisesRegex(NBTError, "longs"):
            unpack_tight(nbt.TagLongArray([0]), 100, 3)


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="heaton-voxel-litematic-")
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)

    def sample(self) -> Schematic:
        schematic = Schematic(5, 3, 4, 5023, offset=(-2, 1, 3))
        for x in range(5):
            for z in range(4):
                schematic.set_block(x, 0, z, "minecraft:stone_bricks")
        schematic.set_block(2, 1, 1, "minecraft:barrel[facing=up]")
        schematic.set_block_entity(2, 1, 1, TagCompound({"id": TagString("minecraft:barrel")}))
        schematic.entities.append(TagCompound({
            "id": TagString("minecraft:armor_stand"),
            "Pos": TagList([TagDouble(1.5), TagDouble(1.0), TagDouble(2.5)], nbt.TAG_DOUBLE)}))
        return schematic

    def test_it_round_trips_offset_cells_tiles_and_entities(self):
        path = os.path.join(self.directory, "a.litematic")
        self.sample().save(path)
        back = Schematic.load(path)
        self.assertEqual((back.size, back.offset), ((5, 3, 4), (-2, 1, 3)))
        self.assertEqual(str(back.get_block(2, 1, 1).name), "minecraft:barrel")
        self.assertEqual(list(back.block_entities), [(2, 1, 1)])
        self.assertEqual([float(v) for v in back.entities[0]["Pos"]], [1.5, 1.0, 2.5])

    def test_air_comes_first_in_the_palette_and_states_carry_every_property(self):
        root = litematic_root(self.sample())
        palette = root["Regions"]["Unnamed"]["BlockStatePalette"]
        self.assertEqual(str(palette[0]["Name"]), "minecraft:air")
        barrel = next(e for e in palette if str(e["Name"]) == "minecraft:barrel")
        self.assertEqual(sorted(barrel["Properties"]), ["facing", "open"])
        meta = root["Metadata"]
        self.assertEqual((int(meta["TotalVolume"]), int(meta["TotalBlocks"])), (60, 21))
        self.assertEqual([int(meta[k]) for k in ("TimeCreated", "TimeModified")], [0, 0])

    def test_regions_read_as_one_box_with_voids_between(self):
        first = litematic_root(Schematic(2, 1, 1, 5023, offset=(0, 0, 0)))
        second = litematic_root(Schematic(1, 1, 1, 5023, offset=(4, 0, 0)))
        regions = TagCompound({"a": first["Regions"]["Unnamed"], "b": second["Regions"]["Unnamed"]})
        regions["b"]["BlockStatePalette"].append(TagCompound({"Name": TagString("minecraft:dirt")}))
        regions["b"]["BlockStates"] = pack_tight([1], 2)
        first["Regions"] = regions
        schematic = read_litematic(Schematic, first, "two")
        self.assertEqual(schematic.size, (5, 1, 1))
        self.assertEqual([schematic.get_block(x, 0, 0).name for x in range(5)],
                         ["minecraft:air", "minecraft:air", "minecraft:structure_void",
                          "minecraft:structure_void", "minecraft:dirt"])

    def test_a_negative_size_counts_from_the_far_corner(self):
        root = litematic_root(Schematic(3, 1, 2, 5023))
        body = root["Regions"]["Unnamed"]
        body["Position"] = TagCompound({"x": TagInt(10), "y": TagInt(0), "z": TagInt(5)})
        body["Size"] = TagCompound({"x": TagInt(-3), "y": TagInt(1), "z": TagInt(-2)})
        body["Entities"] = TagList([TagCompound({
            "id": TagString("minecraft:pig"),
            "Pos": TagList([TagDouble(-0.5), TagDouble(0.0), TagDouble(-0.5)], nbt.TAG_DOUBLE)})],
            nbt.TAG_COMPOUND)
        schematic = read_litematic(Schematic, root, "far")
        self.assertEqual((schematic.size, schematic.offset), ((3, 1, 2), (8, 0, 4)))
        # an entity counts from Position, so half a block in from (10, 0, 5)
        self.assertEqual([float(v) for v in schematic.entities[0]["Pos"]], [1.5, 0.0, 0.5])

    def test_refusals_name_their_reason(self):
        root = litematic_root(Schematic(1, 1, 1, 5023))
        root["Version"] = TagInt(4)
        with self.assertRaisesRegex(NBTError, "Litematica version 4"):
            read_litematic(Schematic, root, "old")
        root = litematic_root(Schematic(1, 1, 1, 5023))
        root["Regions"]["Unnamed"]["Size"]["x"] = TagInt(0)
        with self.assertRaisesRegex(NBTError, "size 0"):
            read_litematic(Schematic, root, "flat")
        root = litematic_root(Schematic(1, 1, 1, 5023))
        root["Regions"] = TagCompound()
        with self.assertRaisesRegex(NBTError, "no regions"):
            read_litematic(Schematic, root, "empty")


if __name__ == "__main__":
    unittest.main()
