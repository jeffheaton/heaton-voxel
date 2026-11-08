"""Saves in the layout before Minecraft 26.1: read where they stand, never
written, every answer the game's.

Until DataVersion 4772 a world kept its overworld in ``region/`` beside
``level.dat``, the nether under ``DIM-1/``, the End under ``DIM1/``, its
players in ``playerdata/`` and its weather, clock, border, spawn and
difficulty in ``level.dat``. ``fixtures/old_layout.json`` is five such
worlds, from a 1.13.2 ``level.dat`` to the shapes of 4671, each beside what
the game's own ``FileFixerUpper`` made of it (``tools/probe_old_layout.py``).
Each is opened twice here -- as it stood, in the old layout, and as the
game left it, in the current one -- and every answer the old one gives is
held to the one the game's gives.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import unittest

from heaton_voxel.blocks import ReadOnlyChunk
from heaton_voxel.nbt import NBTError, TagDouble, TagInt, TagList
from heaton_voxel.world import (
    DIMENSION_STORAGE_DATA_VERSION,
    END,
    NETHER,
    OVERWORLD,
    MinecraftSave,
    ReadOnlySave,
)

from fixtures import FIXTURE_LEVEL_DAT, FIXTURE_REGION

_HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(os.path.dirname(_HERE), "fixtures")
with open(os.path.join(FIXTURES, "old_layout.json"), encoding="ascii") as _handle:
    _GAME = json.load(_handle)


def assemble(snapshot: dict, directory: str) -> str:
    """Write a world the probe recorded: its directories, its files, and
    each region file the vendored fixture it is."""
    for relative in snapshot["dirs"]:
        os.makedirs(os.path.join(directory, *relative.split("/")), exist_ok=True)
    for relative, content in snapshot["files"].items():
        path = os.path.join(directory, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if isinstance(content, dict):
            shutil.copyfile(os.path.join(FIXTURES, *content["fixture"].split("/")), path)
        else:
            with open(path, "wb") as handle:
                handle.write(base64.b64decode(content))
    return directory


def _answer(call):
    """A reader's answer, its fields where it is a result object, or the
    fact that it refused."""
    try:
        found = call()
    except NBTError:
        return "refused"
    slots = getattr(type(found), "__slots__", None)
    return {name: getattr(found, name) for name in slots} if slots else found


def answers(save: MinecraftSave) -> dict:
    """Everything the readers say about a save."""
    out = {
        "dimensions": {name: sorted(dim.available) for name, dim in sorted(save.dimensions.items())},
        "game_type": _answer(save.game_type),
        "allow_commands": _answer(save.allow_commands),
        "difficulty": _answer(save.difficulty),
        "hardcore": _answer(save.hardcore),
        "difficulty_locked": _answer(save.difficulty_locked),
        "weather": _answer(save.weather),
        "spawn": _answer(save.spawn_position),
    }
    for name in (OVERWORLD, NETHER, END):
        out[f"day_time {name}"] = _answer(lambda: save.day_time(name))
        out[f"border {name}"] = _answer(lambda: save.world_border(name))
    out["player"] = _answer(lambda: (save.player_position(), save.player_rotation(),
                                     save.player_dimension()))
    return out


class AgainstTheGameTests(unittest.TestCase):
    """Each old-layout world against the game's move of it."""

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="old-layout-")
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)

    def open(self, case, side) -> MinecraftSave:
        index = _GAME["cases"].index(case)
        return MinecraftSave.load(assemble(
            case[side], os.path.join(self.directory, f"{side}{index}")))

    def test_the_fixture_is_the_game_s(self):
        self.assertEqual(_GAME["game"], "26.3")
        versions = [case["data_version"] for case in _GAME["cases"]]
        self.assertEqual(versions, [1631, 2586, 4189, 4556, 4671])
        self.assertTrue(all(v < DIMENSION_STORAGE_DATA_VERSION for v in versions))

    def test_every_answer_is_the_one_the_game_s_move_gives(self):
        for case in _GAME["cases"]:
            with self.subTest(case=case["name"]):
                old, moved = self.open(case, "before"), self.open(case, "after")
                self.assertEqual((old.layout, moved.layout), ("old", "current"))
                ours, game = answers(old), answers(moved)
                if not any(f.startswith("playerdata/") for f in case["before"]["files"]):
                    # Its one player is level.dat's own, and no playerdata
                    # holds it: the game's move keeps the uuid and drops the
                    # player, so there the old save answers and the moved
                    # one cannot.
                    self.assertEqual(game.pop("player"), "refused")
                    self.assertEqual(ours.pop("player")[0], (-10.5, 70.0, 5.5))
                self.assertEqual(ours, game)

    def test_the_cases_ask_what_they_are_for(self):
        by_name = {case["name"]: case for case in _GAME["cases"]}
        first = answers(self.open(by_name["1.13.2, a player in level.dat and in playerdata"],
                                  "before"))
        # playerdata's player, not level.dat's, and its dimension a number
        self.assertEqual(first["player"], ((1.5, 80.0, 2.5), (0.0, 0.0), OVERWORLD))
        self.assertEqual(first["difficulty"], "hard")
        self.assertNotIn(NETHER, first["dimensions"])
        self.assertEqual(first[f"day_time {END}"], "refused")
        # the nether's border, centered an eighth as far out
        self.assertEqual((first[f"border {NETHER}"]["center_x"],
                          first[f"border {NETHER}"]["center_z"]), (10.5 / 8, -20.5 / 8))
        second = answers(self.open(by_name["1.16.5, a player only in playerdata, an empty nether"],
                                   "before"))
        self.assertEqual(second["dimensions"][NETHER], [])
        self.assertEqual(second["player"][2], NETHER)
        self.assertTrue(second["hardcore"])


class OldLayoutTests(unittest.TestCase):
    """What opening a save in the old layout means, and what it refuses."""

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="old-layout-")
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.world = self.assemble(0)
        self.save = MinecraftSave.load(self.world)

    def assemble(self, index: int) -> str:
        return assemble(_GAME["cases"][index]["before"],
                        os.path.join(self.directory, f"w{index}"))

    @staticmethod
    def snapshot(world) -> dict:
        out = {}
        for root, _, names in os.walk(world):
            for name in names:
                path = os.path.join(root, name)
                with open(path, "rb") as handle:
                    out[os.path.relpath(path, world)] = handle.read()
        return out

    def test_its_chunks_read_where_they_stand(self):
        self.assertEqual(self.save.layout, "old")
        self.assertIn("layout before Minecraft 26.1", self.save.read_only)
        self.assertEqual(sorted(self.save.dimensions), [OVERWORLD, END])
        self.assertTrue(self.save.dimension(END).region_dir.endswith(os.path.join("DIM1", "region")))
        # chunk (0, -154), a finished 1.15.2 chunk
        top = self.save.surface_y(5, -2460, "WORLD_SURFACE")
        self.assertIsNotNone(top)
        self.assertNotEqual(self.save.get_block(5, top, -2460).name, "minecraft:air")
        self.assertEqual(len(self.save.entities_in((0, 0, -2448), (15, 255, -2433)).entities), 1)

    def test_every_write_is_refused_and_no_byte_moves(self):
        # a 1.13.2 save of 1.15 chunks, and a 1.21.4 one of current chunks,
        # which only the layout refuses
        for index in (0, 2):
            with self.subTest(case=_GAME["cases"][index]["name"]):
                self.refuses_every_write(self.assemble(index))

    def refuses_every_write(self, world):
        before = self.snapshot(world)
        save = MinecraftSave.load(world)
        region = save.dimension(OVERWORLD).load_all() and next(
            iter(save.dimension(OVERWORLD).regions.values()))
        lx, lz = sorted(key for key, chunk in region.chunks.items()
                        if save.dimension(OVERWORLD).blocks(
                            region.region_x * 32 + key[0], region.region_z * 32 + key[1]).finished)[0]
        x, z = (region.region_x * 32 + lx) * 16 + 5, (region.region_z * 32 + lz) * 16 + 4
        writes = [
            ("save", save.save),
            ("set_block", lambda: save.set_block(x, 100, z, "minecraft:stone")),
            ("fill", lambda: save.fill((x, 100, z), (x + 3, 101, z + 3), "minecraft:stone")),
            ("place_block", lambda: save.place_block(x, 100, z, "minecraft:oak_fence")),
            ("set_biome", lambda: save.set_biome(x, 100, z, "minecraft:desert")),
            ("connect_blocks", lambda: save.connect_blocks((x, 60, z), (x + 3, 70, z + 3))),
            ("reset_lighting", lambda: save.reset_lighting((x, 60, z), (x + 3, 70, z + 3))),
            ("rebuild_heightmaps", lambda: save.rebuild_heightmaps((x, 0, z), (x, 0, z))),
            ("spawn_entity", lambda: save.spawn_entity("minecraft:pig", x + 0.5, 100.0, z + 0.5)),
            ("create_chunks", lambda: save.create_chunks((640, 0, 640), (650, 0, 650))),
            ("delete_chunks", lambda: save.delete_chunks((x, 0, z), (x, 0, z))),
            ("invalidate_poi", lambda: save.invalidate_poi((x, 0, z), (x, 0, z))),
            ("set_game_type", lambda: save.set_game_type("creative")),
            ("set_allow_commands", lambda: save.set_allow_commands(False)),
            ("set_difficulty", lambda: save.set_difficulty("easy")),
            ("set_hardcore", lambda: save.set_hardcore(True)),
            ("set_difficulty_locked", lambda: save.set_difficulty_locked(False)),
            ("set_weather", lambda: save.set_weather("clear")),
            ("set_day_time", lambda: save.set_day_time("noon")),
            ("set_player_position", lambda: save.set_player_position(0.5, 90.0, 0.5)),
            ("set_spawn_position", lambda: save.set_spawn_position(0, 90, 0)),
        ]
        for name, write in writes:
            with self.subTest(write=name, world=os.path.basename(world)):
                try:
                    result = write()
                except ReadOnlySave as caught:
                    self.assertIn("will not", str(caught))
                    continue
                # a writer that reports what it could not write, as it does a
                # chunk that cannot be used: a cell's by writing nothing
                if isinstance(result, bool):
                    self.assertFalse(result, name)
                    continue
                self.assertTrue(result.unreadable_chunks, name)
                self.assertTrue(all("layout before Minecraft 26.1" in reason
                                    for _, reason in result.unreadable_chunks), name)
        self.assertTrue(issubclass(ReadOnlySave, ReadOnlyChunk))
        with self.assertRaises(ReadOnlySave):
            save.save(force=True)
        self.assertEqual(self.snapshot(world), before)

    def test_a_save_holding_both_layouts_is_refused(self):
        os.makedirs(os.path.join(self.world, "dimensions", "minecraft", "overworld", "region"))
        with self.assertRaises(NBTError) as caught:
            MinecraftSave.load(self.world)
        self.assertIn("both layouts", str(caught.exception))

    def test_a_level_dat_newer_than_the_layout_is_refused(self):
        level = os.path.join(self.world, "level.dat")
        shutil.copyfile(FIXTURE_LEVEL_DAT, level)       # 26.2's, DataVersion 4903
        with self.assertRaises(NBTError) as caught:
            MinecraftSave.load(self.world)
        self.assertIn("do not belong together", str(caught.exception))

    def test_a_current_save_is_still_current(self):
        world = os.path.join(self.directory, "current")
        region = os.path.join(world, "dimensions", "minecraft", "overworld", "region")
        os.makedirs(region)
        shutil.copyfile(FIXTURE_REGION, os.path.join(region, "r.-1.0.mca"))
        shutil.copyfile(FIXTURE_LEVEL_DAT, os.path.join(world, "level.dat"))
        save = MinecraftSave.load(world)
        self.assertEqual((save.layout, save.read_only), ("current", None))

    def test_no_regions_in_either_layout_is_refused_naming_both(self):
        world = os.path.join(self.directory, "bare")
        os.makedirs(world)
        shutil.copyfile(FIXTURE_LEVEL_DAT, os.path.join(world, "level.dat"))
        with self.assertRaises(NBTError) as caught:
            MinecraftSave.load(world)
        self.assertIn("DIM-1/region", str(caught.exception))
        self.assertIn("dimensions/", str(caught.exception))


class LegacyDimensionTests(unittest.TestCase):
    """A player's dimension as a number, which a moved and not yet loaded
    player file still holds, read as the game's ``LegacyDimensionIdFix``."""

    def test_numbers_read_as_the_game_reads_them(self):
        directory = tempfile.mkdtemp(prefix="old-layout-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        save = MinecraftSave.load(assemble(_GAME["cases"][0]["after"], directory))
        player = save._load_player()
        for number, name in ((0, OVERWORLD), (-1, NETHER), (1, END), (7, OVERWORLD)):
            player["Dimension"] = TagInt(number)
            self.assertEqual(save.player_dimension(), name)
        self.assertIsInstance(player["Pos"], TagList)
        self.assertIsInstance(player["Pos"][0], TagDouble)


if __name__ == "__main__":
    unittest.main()
