"""World-settings accessors: game mode, cheats, difficulty, weather, clocks,
and the spawn point.

Every write goes the whole way: edit, save(), reload from disk, read back.
The refusals are tested as carefully as the successes -- a settings write
that silently lands in the wrong shape is exactly how a save gets values the
game never reads.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from fixtures import (  # noqa: E402
    FIXTURE_LEVEL_DAT,
    FIXTURE_PLAYER,
    FIXTURE_PLAYER_UUID,
    FIXTURE_REGION,
    FIXTURE_WEATHER,
    FIXTURE_WORLD_CLOCKS,
)
from heaton_voxel.nbt import NBT, NBTError  # noqa: E402
from heaton_voxel.world import END, NETHER, OVERWORLD, MinecraftSave  # noqa: E402
from heaton_voxel.world.settings import DAY_TIMES  # noqa: E402


def make_world(tmpdir: str, *, split_files: bool = True,
               player: bool = True) -> str:
    """A throwaway save assembled from the fixture, current layout."""
    world = os.path.join(tmpdir, "world")
    region_dir = os.path.join(world, "dimensions/minecraft/overworld/region")
    os.makedirs(region_dir)
    shutil.copyfile(
        FIXTURE_REGION, os.path.join(region_dir, os.path.basename(FIXTURE_REGION))
    )
    shutil.copyfile(FIXTURE_LEVEL_DAT, os.path.join(world, "level.dat"))
    if split_files:
        data_dir = os.path.join(world, "data", "minecraft")
        os.makedirs(data_dir)
        shutil.copyfile(FIXTURE_WEATHER, os.path.join(data_dir, "weather.dat"))
        shutil.copyfile(
            FIXTURE_WORLD_CLOCKS, os.path.join(data_dir, "world_clocks.dat"))
    if player:
        player_dir = os.path.join(world, "players", "data")
        os.makedirs(player_dir)
        shutil.copyfile(
            FIXTURE_PLAYER,
            os.path.join(player_dir, f"{FIXTURE_PLAYER_UUID}.dat"))
    return world


class SettingsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="settings-tests-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.world = make_world(self.tmp)
        self.save = MinecraftSave.load(self.world)

    def reload(self) -> MinecraftSave:
        """Write, drop everything, and read back from the files."""
        self.save.save(force=True)
        return MinecraftSave.load(self.world)


class GameTypeTests(SettingsTestCase):
    def test_reads_the_fixture_mode(self):
        self.assertEqual(self.save.game_type(), "creative")

    def test_set_updates_world_and_player(self):
        self.save.set_game_type("spectator")
        again = self.reload()
        self.assertEqual(again.game_type(), "spectator")
        self.assertEqual(int(again._load_player()["playerGameType"]), 3)

    def test_set_without_player(self):
        world = make_world(os.path.join(self.tmp, "solo"), player=False)
        save = MinecraftSave.load(world)
        save.set_game_type("adventure", include_player=False)
        self.assertEqual(save.game_type(), "adventure")
        # And with include_player it refuses, naming the missing data.
        with self.assertRaises(NBTError) as ctx:
            save.set_game_type("survival")
        self.assertIn("player", str(ctx.exception))

    def test_unknown_mode_refused(self):
        with self.assertRaises(NBTError) as ctx:
            self.save.set_game_type("peaceful")
        self.assertIn("survival", str(ctx.exception))


class CommandsAndDifficultyTests(SettingsTestCase):
    def test_allow_commands_round_trip(self):
        self.assertTrue(self.save.allow_commands())
        self.save.set_allow_commands(False)
        self.assertFalse(self.reload().allow_commands())

    def test_difficulty_round_trip(self):
        self.assertEqual(self.save.difficulty(), "normal")
        self.save.set_difficulty("hard")
        self.assertEqual(self.reload().difficulty(), "hard")

    def test_locked_difficulty_refuses(self):
        self.save.set_difficulty_locked(True)
        self.assertTrue(self.save.difficulty_locked())
        with self.assertRaises(NBTError) as ctx:
            self.save.set_difficulty("peaceful")
        self.assertIn("locked", str(ctx.exception))
        # Unlocking is exactly the documented way through.
        self.save.set_difficulty_locked(False)
        self.save.set_difficulty("peaceful")
        self.assertEqual(self.reload().difficulty(), "peaceful")

    def test_hardcore_round_trip(self):
        self.assertFalse(self.save.hardcore())
        self.save.set_hardcore(True)
        self.assertTrue(self.reload().hardcore())

    def test_unknown_difficulty_refused(self):
        with self.assertRaises(NBTError):
            self.save.set_difficulty("impossible")


class WeatherTests(SettingsTestCase):
    def test_reads_the_fixture_weather(self):
        state = self.save.weather()
        # The vendored file was captured mid-rain.
        self.assertTrue(state.raining)
        self.assertFalse(state.thundering)
        self.assertEqual(state.kind, "rain")
        self.assertGreater(state.rain_time, 0)

    def test_set_clear_round_trip(self):
        self.save.set_weather("clear", duration=12000)
        state = self.reload().weather()
        self.assertEqual(state.kind, "clear")
        self.assertFalse(state.raining)
        self.assertEqual(state.clear_weather_time, 12000)
        self.assertEqual(state.rain_time, 0)

    def test_set_thunder_fills_both_timers(self):
        self.save.set_weather("thunder", duration=2400)
        state = self.reload().weather()
        self.assertEqual(state.kind, "thunder")
        self.assertEqual(state.rain_time, 2400)
        self.assertEqual(state.thunder_time, 2400)
        self.assertEqual(state.clear_weather_time, 0)

    def test_bad_inputs_refused(self):
        with self.assertRaises(NBTError):
            self.save.set_weather("snow")
        with self.assertRaises(NBTError):
            self.save.set_weather("rain", duration=-1)

    def test_missing_file_refused_by_name(self):
        world = make_world(os.path.join(self.tmp, "old"), split_files=False)
        save = MinecraftSave.load(world)
        with self.assertRaises(NBTError) as ctx:
            save.weather()
        self.assertIn("weather.dat", str(ctx.exception))
        with self.assertRaises(NBTError) as ctx:
            save.set_weather("clear")
        self.assertIn("weather.dat", str(ctx.exception))


class DayTimeTests(SettingsTestCase):
    def test_reads_both_stored_clocks(self):
        overworld = self.save.day_time()
        self.assertEqual(overworld, self.save.day_time(OVERWORLD))
        self.assertGreater(overworld, 0)
        self.assertGreater(self.save.day_time(END), 0)
        self.assertNotEqual(overworld, self.save.day_time(END))

    def test_absolute_set_round_trips(self):
        self.assertEqual(self.save.set_day_time(123456), 123456)
        self.assertEqual(self.reload().day_time(), 123456)

    def test_preset_advances_never_rewinds(self):
        self.save.set_day_time(10 * 24000 + 9000)  # day 10, past "noon"
        result = self.save.set_day_time("noon")
        self.assertEqual(result, 11 * 24000 + DAY_TIMES["noon"])
        result = self.save.set_day_time("midnight")  # still ahead on day 11
        self.assertEqual(result, 11 * 24000 + DAY_TIMES["midnight"])

    def test_unknown_dimension_refused_with_inventory(self):
        with self.assertRaises(NBTError) as ctx:
            self.save.day_time("minecraft:the_nether")
        self.assertIn("minecraft:overworld", str(ctx.exception))

    def test_bad_inputs_refused(self):
        with self.assertRaises(NBTError):
            self.save.set_day_time("dusk")
        with self.assertRaises(NBTError):
            self.save.set_day_time(-1)


class SpawnTests(SettingsTestCase):
    def test_set_position_keeps_facing_and_dimension(self):
        before = self.save.level.root["Data"]["spawn"]
        yaw = float(before["yaw"])
        dimension = str(before["dimension"])
        self.save.set_spawn_position(-400, 71, 100)
        again = self.reload()
        self.assertEqual(again.spawn_position(), (-400, 71, 100))
        spawn = again.level.root["Data"]["spawn"]
        self.assertEqual(float(spawn["yaw"]), yaw)
        self.assertEqual(str(spawn["dimension"]), dimension)

    def test_set_position_with_facing(self):
        self.save.set_spawn_position(1, 2, 3, yaw=90.0, pitch=-10.0,
                                     dimension=OVERWORLD)
        spawn = self.reload().level.root["Data"]["spawn"]
        self.assertEqual(float(spawn["yaw"]), 90.0)
        self.assertEqual(float(spawn["pitch"]), -10.0)


class WorldBorderTests(SettingsTestCase):
    def _write_border_file(self, dimension="overworld", **fields):
        """A world_border.dat where the game puts one: per dimension."""
        from heaton_voxel.nbt import (
            COMPRESSION_GZIP, TagCompound, TagDouble, TagInt, TagLong,
        )
        data = TagCompound()
        defaults = dict(center_x=200.0, center_z=-300.0, size=5000.0,
                        safe_zone=5.0, damage_per_block=0.2,
                        warning_blocks=5, warning_time=15,
                        lerp_target=5000.0, lerp_time=0)
        defaults.update(fields)
        for name in ("center_x", "center_z", "size", "safe_zone",
                     "damage_per_block", "lerp_target"):
            data[name] = TagDouble(defaults[name])
        data["warning_blocks"] = TagInt(defaults["warning_blocks"])
        data["warning_time"] = TagInt(defaults["warning_time"])
        data["lerp_time"] = TagLong(defaults["lerp_time"])
        root = TagCompound()
        root["data"] = data
        out = NBT()
        out.root = root
        out.compression = COMPRESSION_GZIP
        directory = os.path.join(self.world, "dimensions", "minecraft",
                                 dimension, "data", "minecraft")
        os.makedirs(directory, exist_ok=True)
        out.save(os.path.join(directory, "world_border.dat"))

    def test_each_dimension_keeps_its_own_border(self):
        """The border is per dimension, and the game writes one for each.

        Reading them all through a single world-wide accessor was the shape
        of the bug: it looked at a path no Minecraft version has ever
        written and reported the default for every real save.
        """
        self._write_border_file("overworld", size=5000.0)
        self._write_border_file("the_nether", size=700.0, center_x=64.0)
        save = MinecraftSave.load(self.world)
        self.assertEqual(save.world_border().size, 5000.0)
        self.assertEqual(save.world_border(OVERWORLD).size, 5000.0)
        self.assertEqual(save.world_border(NETHER).size, 700.0)
        self.assertEqual(save.world_border(NETHER).center_x, 64.0)
        # The end was never given one, which is a real answer, not a fault.
        self.assertFalse(save.world_border(END).stored)

    def test_reading_one_dimension_does_not_move_another(self):
        """The cache is keyed by path, so two files with the same stem can
        both be held -- and each goes back where it came from."""
        self._write_border_file("overworld", size=5000.0)
        self._write_border_file("the_nether", size=700.0)
        save = MinecraftSave.load(self.world)
        save.world_border(OVERWORLD)
        save.world_border(NETHER)
        targets = {os.path.relpath(path, self.world).replace(os.sep, "/")
                   for _, path in save._settings_files_to_save()}
        self.assertIn(
            "dimensions/minecraft/overworld/data/minecraft/world_border.dat",
            targets)
        self.assertIn(
            "dimensions/minecraft/the_nether/data/minecraft/world_border.dat",
            targets)
        self.assertNotIn("data/minecraft/world_border.dat", targets)

    def test_unknown_dimension_is_refused(self):
        with self.assertRaises(NBTError) as caught:
            self.save.world_border("minecraft:the_aether")
        self.assertIn("unknown dimension", str(caught.exception))

    def test_missing_file_is_the_default_border(self):
        border = self.save.world_border()
        self.assertFalse(border.stored)
        self.assertEqual(border.size, 59_999_968.0)
        self.assertEqual((border.center_x, border.center_z), (0.0, 0.0))
        self.assertEqual(border.max_x, 29_999_984.0)
        self.assertEqual(border.min_z, -29_999_984.0)

    def test_reads_the_split_file(self):
        self._write_border_file(center_x=200.0, center_z=-300.0,
                                size=5000.0, warning_blocks=8)
        border = MinecraftSave.load(self.world).world_border()
        self.assertTrue(border.stored)
        self.assertEqual(border.size, 5000.0)
        self.assertEqual((border.center_x, border.center_z), (200.0, -300.0))
        self.assertEqual(border.warning_blocks, 8)
        self.assertEqual(border.min_x, -2300.0)
        self.assertEqual(border.max_z, 2200.0)

    def test_reads_the_old_flat_keys(self):
        """A pre-26.x world kept the border in level.dat; reporting the
        defaults for one somebody moved would be silently wrong."""
        from heaton_voxel.nbt import TagDouble, TagLong
        level = self.save.level.root["Data"]
        level["BorderSize"] = TagDouble(1000.0)
        level["BorderCenterX"] = TagDouble(50.0)
        level["BorderCenterZ"] = TagDouble(-60.0)
        level["BorderWarningBlocks"] = TagDouble(9.0)  # doubles, historically
        level["BorderSizeLerpTime"] = TagLong(0)
        border = self.save.world_border()
        self.assertTrue(border.stored)
        self.assertEqual(border.size, 1000.0)
        self.assertEqual((border.center_x, border.center_z), (50.0, -60.0))
        self.assertEqual(border.warning_blocks, 9)
        self.assertEqual(border.lerp_target, 1000.0)

    def test_split_file_wins_over_old_keys(self):
        from heaton_voxel.nbt import TagDouble
        self._write_border_file(size=5000.0)
        save = MinecraftSave.load(self.world)
        save.level.root["Data"]["BorderSize"] = TagDouble(1.0)
        self.assertEqual(save.world_border().size, 5000.0)

    def test_geometry_helpers(self):
        from heaton_voxel.world import WorldBorder
        border = WorldBorder(center_x=100.0, center_z=-100.0, size=200.0)
        self.assertTrue(border.contains(0.0, 0.0))
        self.assertTrue(border.contains(200.0, -200.0))  # on the wall
        self.assertFalse(border.contains(201.0, 0.0))
        self.assertEqual(border.distance_to_edge(100.0, -100.0), 100.0)
        self.assertEqual(border.distance_to_edge(0.0, -100.0), 0.0)
        self.assertEqual(border.distance_to_edge(210.0, -100.0), -10.0)
        self.assertEqual(border.clamp(500.0, -500.0), (200.0, -200.0))
        self.assertEqual(border.clamp(500.0, -500.0, margin=10.0),
                         (190.0, -190.0))
        # A margin wider than the border collapses to the center.
        self.assertEqual(border.clamp(500.0, 0.0, margin=150.0),
                         (100.0, -100.0))

    def test_reading_does_not_mark_the_file_for_writing(self):
        """world_border() is read-only: loading it must not cause save()
        to rewrite the file."""
        self._write_border_file()
        save = MinecraftSave.load(self.world)
        save.world_border()
        self.assertEqual(save.save(force=True), 0)


class PersistenceTests(SettingsTestCase):
    def test_untouched_settings_write_nothing(self):
        """save() must not rewrite level.dat or the split files when no
        setting changed -- the byte-exact promise extends to them."""
        level_path = os.path.join(self.world, "level.dat")
        weather_path = os.path.join(
            self.world, "data", "minecraft", "weather.dat")
        with open(level_path, "rb") as handle:
            level_before = handle.read()
        before_mtime = os.path.getmtime(weather_path)
        self.assertEqual(self.save.save(force=True), 0)
        with open(level_path, "rb") as handle:
            self.assertEqual(handle.read(), level_before)
        self.assertEqual(os.path.getmtime(weather_path), before_mtime)

    def test_one_change_writes_one_file(self):
        self.save.set_weather("clear")
        self.assertEqual(self.save.save(force=True), 1)

    def test_level_change_writes_level_only(self):
        self.save.set_allow_commands(False)
        self.assertEqual(self.save.save(force=True), 1)


if __name__ == "__main__":
    unittest.main()
