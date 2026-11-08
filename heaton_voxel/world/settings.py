"""World-level settings: game mode, cheats, difficulty, weather, time.

A mixin of :class:`~heaton_voxel.world.MinecraftSave` -- one file per
concern, mirroring the C# port's partial classes (see PORTING.md).

Where these live moved in Minecraft 26.x: game mode, cheats and difficulty
stay in ``level.dat``, while weather and the per-dimension day clocks were
split out into ``data/minecraft/weather.dat`` and ``world_clocks.dat``.
Those two belong to the world. **The border does not**: it is per dimension,
and the game keeps one at
``dimensions/minecraft/<dim>/data/minecraft/world_border.dat`` for each --
which is why :meth:`SettingsOps.world_border` takes a dimension the way
:meth:`SettingsOps.day_time` does. This module writes the split layout
only -- the shapes the current game writes. A save in the layout before
26.1 (``MinecraftSave.layout == "old"``) kept all of it as fields of
``level.dat``, and is read from them, every setter refusing it: each
answer is the one the game's own upgrade of that ``level.dat`` gives
(``tools/probe_old_layout.py`` asks it, and ``tests/test_old_layout.py``
holds every reader here to it).

Like every other edit, nothing reaches disk until :meth:`MinecraftSave.save`.
"""

from __future__ import annotations

import os

from ..nbt import (
    NBT,
    NBTError,
    TagByte,
    TagCompound,
    TagInt,
    TagLong,
    TagString,
)
from ._state import SaveState
from .dimension import NETHER, OVERWORLD, _DIMENSION_ROOTS
from .results import WeatherState, WorldBorder

GAME_MODES = ("survival", "creative", "adventure", "spectator")
DIFFICULTIES = ("peaceful", "easy", "normal", "hard")
WEATHER_KINDS = ("clear", "rain", "thunder")

#: The game's own named times of day, in ticks past dawn.
DAY_TIMES = {"day": 1000, "noon": 6000, "night": 13000, "midnight": 18000}

_TICKS_PER_DAY = 24000

#: A ``Difficulty`` number as the game's ``LevelDatDifficultyFix`` names
#: it: a number missing is 2, and any number not here is normal.
_LEGACY_DIFFICULTIES = {0: "peaceful", 1: "easy", 3: "hard"}

#: How far the nether's border is from the overworld's: the game's
#: ``LevelDatToSavedDataFileFix`` gives every dimension the one border a
#: save kept before 26.1, the nether's center divided by its coordinate
#: scale.
_NETHER_BORDER_SCALE = 8.0


class SettingsOps(SaveState):
    """Game mode, cheats, difficulty, weather, clocks and the border."""

    # -- the level.dat side ------------------------------------------------

    def game_type(self) -> str:
        """The world's default game mode.

        This is the mode a *new* player joins in; the players who have
        already been in the world each carry their own (see
        :meth:`set_game_type`).
        """
        value = self._level_data().get("GameType")
        if value is None:
            raise NBTError("level.dat has no GameType")
        mode = int(value)
        if not 0 <= mode < len(GAME_MODES):
            raise NBTError(f"level.dat GameType is {mode}, expected 0-3")
        return GAME_MODES[mode]

    def set_game_type(self, mode: str, *, include_player: bool = True) -> None:
        """Set the world's game mode by name.

        With ``include_player`` (the default) the single player's own
        ``playerGameType`` is set too -- the world value alone is only the
        default for players the world has never seen, so flipping a
        survival world to creative without it would leave its player in
        survival. Pass ``False`` for a world with no player data (a
        server's, say), where the world default is the whole story.
        """
        self._refuse_read_only("change the game mode of")
        if mode not in GAME_MODES:
            raise NBTError(
                f"unknown game mode {mode!r}; expected one of "
                f"{', '.join(GAME_MODES)}"
            )
        self._level_data()["GameType"] = TagInt(GAME_MODES.index(mode))
        if include_player:
            self._load_player()["playerGameType"] = TagInt(
                GAME_MODES.index(mode))

    def allow_commands(self) -> bool:
        """Whether cheats are allowed in this world."""
        value = self._level_data().get("allowCommands")
        if value is None:
            raise NBTError("level.dat has no allowCommands")
        return bool(int(value))

    def set_allow_commands(self, allowed: bool) -> None:
        self._refuse_read_only("change the cheats of")
        self._level_data()["allowCommands"] = TagByte(1 if allowed else 0)

    def _difficulty_settings(self) -> TagCompound:
        found = self._level_data().get("difficulty_settings")
        if not isinstance(found, TagCompound):
            raise NBTError(
                "level.dat has no difficulty_settings compound: this world "
                "predates the current layout (Minecraft 26.x)"
            )
        return found

    def _difficulty_view(self) -> TagCompound:
        """The difficulty as :meth:`_difficulty_settings` holds it, or, for
        a save in the layout before 26.1 whose ``level.dat`` predates that
        compound, as the game's ``LevelDatDifficultyFix`` makes it of the
        ``Difficulty``, ``hardcore`` and ``DifficultyLocked`` fields. For
        reading: every setter refuses such a save."""
        data = self._level_data()
        if self.layout == "old" and not isinstance(data.get("difficulty_settings"), TagCompound):
            number = int(data.get("Difficulty", 2))
            return TagCompound({
                "difficulty": TagString(_LEGACY_DIFFICULTIES.get(number, "normal")),
                "hardcore": TagByte(1 if int(data.get("hardcore", 0)) else 0),
                "locked": TagByte(1 if int(data.get("DifficultyLocked", 0)) else 0),
            })
        return self._difficulty_settings()

    def difficulty(self) -> str:
        """``"peaceful"``, ``"easy"``, ``"normal"`` or ``"hard"``."""
        name = str(self._difficulty_view().get("difficulty", ""))
        if name not in DIFFICULTIES:
            raise NBTError(
                f"level.dat difficulty is {name!r}, expected one of "
                f"{', '.join(DIFFICULTIES)}"
            )
        return name

    def set_difficulty(self, name: str) -> None:
        """Set the difficulty by name.

        Refuses while the difficulty is locked: the lock exists precisely
        to stop casual flipping, so overriding it silently would make the
        lock meaningless. Unlock first with
        ``set_difficulty_locked(False)`` if you mean it.
        """
        self._refuse_read_only("change the difficulty of")
        if name not in DIFFICULTIES:
            raise NBTError(
                f"unknown difficulty {name!r}; expected one of "
                f"{', '.join(DIFFICULTIES)}"
            )
        settings = self._difficulty_settings()
        if bool(int(settings.get("locked", 0))):
            raise NBTError(
                "difficulty is locked; call set_difficulty_locked(False) "
                "first if you mean to change it"
            )
        settings["difficulty"] = TagString(name)

    def hardcore(self) -> bool:
        return bool(int(self._difficulty_view().get("hardcore", 0)))

    def set_hardcore(self, value: bool) -> None:
        self._refuse_read_only("change hardcore in")
        self._difficulty_settings()["hardcore"] = TagByte(1 if value else 0)

    def difficulty_locked(self) -> bool:
        return bool(int(self._difficulty_view().get("locked", 0)))

    def set_difficulty_locked(self, value: bool) -> None:
        self._refuse_read_only("change the difficulty lock of")
        self._difficulty_settings()["locked"] = TagByte(1 if value else 0)

    # -- the split data/minecraft/*.dat side -------------------------------

    @staticmethod
    def _settings_path(stem: str, dimension: str | None = None) -> str:
        """Where a split settings file lives, relative to the save root.

        World-wide by default. A dimension names one of that dimension's own
        files instead -- the border is the only one this library reads, but
        the game keeps its raids and chunk tickets in the same place.
        """
        if dimension is None:
            return f"data/minecraft/{stem}.dat"
        return f"{_DIMENSION_ROOTS[dimension]}/data/minecraft/{stem}.dat"

    def _settings_file(self, stem: str, dimension: str | None = None) -> NBT:
        """One of the split data files, loaded once and cached.

        Cached under the path it came from, not under its stem: the same
        stem exists once per dimension, and a handle filed under the bare
        name would be written back by :meth:`_settings_files_to_save` to
        whichever path that name spelled -- which for a per-dimension file
        is a place the game does not read.

        Refuses when the file does not exist, naming the path rather than
        diagnosing the world's age. The file being absent used to be taken
        as proof of an older layout; it is at least as often proof that the
        caller asked for a dimension this world does not keep one for.
        """
        relative = self._settings_path(stem, dimension)
        held = self._settings_files.get(relative)
        if held is not None:
            return held
        path = os.path.join(self.path, *relative.split("/"))
        if not os.path.exists(path):
            raise NBTError(
                f"{path!r} does not exist: this world keeps no {stem} there. "
                f"A save from before Minecraft 26.x kept these values inside "
                f"level.dat in shapes this module does not write, and "
                f"inventing the file would produce settings the player's own "
                f"game version never reads"
            )
        self._settings_files[relative] = NBT.load(path)
        return self._settings_files[relative]

    @staticmethod
    def _settings_data(handle: NBT, stem: str) -> TagCompound:
        found = handle.root.get("data") if handle.root is not None else None
        if not isinstance(found, TagCompound):
            raise NBTError(f"{stem}.dat has no data compound")
        return found

    def weather(self) -> WeatherState:
        """The world's weather, exactly as stored: in ``weather.dat``, or
        in ``level.dat``'s own fields for a save in the layout before 26.1,
        which are what the game moves there."""
        if self.layout == "old":
            data = self._level_data()
            return WeatherState(
                raining=bool(int(data.get("raining", 0))),
                thundering=bool(int(data.get("thundering", 0))),
                rain_time=int(data.get("rainTime", 0)),
                thunder_time=int(data.get("thunderTime", 0)),
                clear_weather_time=int(data.get("clearWeatherTime", 0)),
            )
        data = self._settings_data(self._settings_file("weather"), "weather")
        return WeatherState(
            raining=bool(int(data.get("raining", 0))),
            thundering=bool(int(data.get("thundering", 0))),
            rain_time=int(data.get("rain_time", 0)),
            thunder_time=int(data.get("thunder_time", 0)),
            clear_weather_time=int(data.get("clear_weather_time", 0)),
        )

    def set_weather(self, kind: str, *, duration: int | None = None) -> None:
        """Set the weather the way the game's ``/weather`` command does.

        ``duration`` is in ticks (20 per second) and sets how long the
        chosen weather holds before the game rolls its next change; left
        ``None``, the timers are zeroed and the game rolls a natural
        duration on next load. ``"clear"`` fills the clear timer;
        ``"rain"`` and ``"thunder"`` fill the rain and thunder timers
        together, which is exactly the command's behavior.
        """
        if kind not in WEATHER_KINDS:
            raise NBTError(
                f"unknown weather {kind!r}; expected one of "
                f"{', '.join(WEATHER_KINDS)}"
            )
        self._refuse_read_only("change the weather of")
        if duration is not None and duration < 0:
            raise NBTError(f"weather duration must be >= 0, got {duration}")
        ticks = duration or 0
        data = self._settings_data(self._settings_file("weather"), "weather")
        data["raining"] = TagByte(1 if kind != "clear" else 0)
        data["thundering"] = TagByte(1 if kind == "thunder" else 0)
        data["clear_weather_time"] = TagInt(ticks if kind == "clear" else 0)
        data["rain_time"] = TagInt(ticks if kind != "clear" else 0)
        data["thunder_time"] = TagInt(ticks if kind != "clear" else 0)

    def world_border(self, dimension: str | None = None) -> WorldBorder:
        """One dimension's border, read-only. The overworld by default.

        **The border is per dimension**, and the game keeps one for each at
        ``dimensions/minecraft/<dim>/data/minecraft/world_border.dat``. It
        writes them unconditionally, from the world's first save, whether or
        not anybody has moved a border -- so in practice the file is always
        there and ``stored`` is always True on a current save.

        A missing file is still answered rather than refused, and still
        comes back as the default border with ``stored`` False. That case is
        no longer the ordinary one it was thought to be, but it is the right
        answer for a dimension a world never generated.

        A save in the layout before 26.1 kept one border, in ``level.dat``:
        flat ``Border*`` keys until 4544, a ``world_border`` compound of
        the current file's keys after (``LegacyWorldBorderFix``). The game
        gives every dimension that border when it moves the save
        (``LevelDatToSavedDataFileFix``), the nether's center divided by its
        coordinate scale of 8, and so does this, for whichever dimension
        was asked for. The flat keys are read the same way in a current
        save too, which only a hand-assembled or third-party world carries,
        because reporting the defaults for a world whose border somebody
        moved would be wrong in a way nothing downstream could detect.

        It has never lived in ``data/minecraft/world_border.dat``, which is
        where this used to look: that path is the one shape the game does
        not write, and the border is the single entry in
        ``LevelDatToSavedDataFileFix`` that goes to a dimension while
        weather, clocks and game rules go world-wide.
        """
        name = dimension if dimension is not None else OVERWORLD
        if name not in _DIMENSION_ROOTS:
            raise NBTError(
                f"unknown dimension {name!r}; expected one of "
                f"{sorted(_DIMENSION_ROOTS)}")
        # Touch the level first: a MinecraftSave built directly is an empty
        # shell, and asking it for a border must refuse rather than hand back
        # a default that looks like an answer.
        self._loaded_level()

        relative = self._settings_path("world_border", name)
        path = os.path.join(self.path, *relative.split("/"))
        if os.path.exists(path):
            data = self._settings_data(
                self._settings_file("world_border", name), "world_border")
            size = float(data.get("size", WorldBorder.DEFAULT_SIZE))
            return WorldBorder(
                center_x=float(data.get("center_x", 0.0)),
                center_z=float(data.get("center_z", 0.0)),
                size=size,
                safe_zone=float(data.get("safe_zone", 5.0)),
                damage_per_block=float(data.get("damage_per_block", 0.2)),
                warning_blocks=int(data.get("warning_blocks", 5)),
                warning_time=int(data.get("warning_time", 15)),
                lerp_target=float(data.get("lerp_target", size)),
                lerp_time=int(data.get("lerp_time", 0)),
                stored=True,
            )

        level = self._level_data()
        scale = _NETHER_BORDER_SCALE if name == NETHER else 1.0
        nested = level.get("world_border")
        if isinstance(nested, TagCompound):
            # 4544 to the move: the current file's keys, in level.dat.
            size = float(nested.get("size", WorldBorder.DEFAULT_SIZE))
            return WorldBorder(
                center_x=float(nested.get("center_x", 0.0)) / scale,
                center_z=float(nested.get("center_z", 0.0)) / scale,
                size=size,
                safe_zone=float(nested.get("safe_zone", 5.0)),
                damage_per_block=float(nested.get("damage_per_block", 0.2)),
                warning_blocks=int(nested.get("warning_blocks", 5)),
                warning_time=int(nested.get("warning_time", 15)),
                lerp_target=float(nested.get("lerp_target", size)),
                lerp_time=int(nested.get("lerp_time", 0)),
                stored=True,
            )
        if "BorderSize" in level:
            # The monolithic layout: doubles throughout, warnings included.
            size = float(level["BorderSize"])
            return WorldBorder(
                center_x=float(level.get("BorderCenterX", 0.0)) / scale,
                center_z=float(level.get("BorderCenterZ", 0.0)) / scale,
                size=size,
                safe_zone=float(level.get("BorderSafeZone", 5.0)),
                damage_per_block=float(
                    level.get("BorderDamagePerBlock", 0.2)),
                warning_blocks=int(
                    float(level.get("BorderWarningBlocks", 5.0))),
                warning_time=int(
                    float(level.get("BorderWarningTime", 15.0))),
                lerp_target=float(level.get("BorderSizeLerpTarget", size)),
                lerp_time=int(level.get("BorderSizeLerpTime", 0)),
                stored=True,
            )

        return WorldBorder()

    def _clock(self, dimension: str | None) -> tuple[TagCompound, str]:
        name = dimension if dimension is not None else OVERWORLD
        if self.layout == "old":
            # level.dat's DayTime, which the game moves to the overworld's
            # clock, and the only clock such a save has.
            data = self._level_data()
            if name != OVERWORLD:
                raise NBTError(
                    f"a save in the layout before 26.1 keeps one clock, the "
                    f"overworld's (level.dat's DayTime), and none for {name!r}")
            if "DayTime" not in data:
                raise NBTError("level.dat has no DayTime")
            return TagCompound({"total_ticks": TagLong(int(data["DayTime"]))}), name
        data = self._settings_data(
            self._settings_file("world_clocks"), "world_clocks")
        clock = data.get(name)
        if not isinstance(clock, TagCompound) or "total_ticks" not in clock:
            have = ", ".join(sorted(str(k) for k in data.keys())) or "none"
            raise NBTError(
                f"world_clocks.dat has no clock for {name!r} (has: {have})"
            )
        return clock, name

    def day_time(self, dimension: str | None = None) -> int:
        """A dimension's clock, in total ticks since its day zero.

        The overworld by default. Day number and time of day derive from
        it: ``ticks // 24000`` and ``ticks % 24000``, with 0 being dawn.
        Dimensions without a stored clock -- the nether keeps none -- are
        refused by name.
        """
        clock, _ = self._clock(dimension)
        return int(clock["total_ticks"])

    def set_day_time(self, time: int | str,
                     dimension: str | None = None) -> int:
        """Set a dimension's clock; returns the new total ticks.

        An integer is absolute -- the whole clock, day count included. A
        name from :data:`DAY_TIMES` (``"day"``, ``"noon"``, ``"night"``,
        ``"midnight"``) advances to that time's *next* occurrence, the way
        sleeping does: the clock never runs backward, so the moon phase
        and every day-counting mechanic stay consistent.
        """
        self._refuse_read_only("change the clock of")
        clock, _ = self._clock(dimension)
        if isinstance(time, str):
            if time not in DAY_TIMES:
                raise NBTError(
                    f"unknown time of day {time!r}; expected one of "
                    f"{', '.join(sorted(DAY_TIMES))} or an absolute tick count"
                )
            current = int(clock["total_ticks"])
            target = (current // _TICKS_PER_DAY) * _TICKS_PER_DAY + DAY_TIMES[time]
            if target <= current:
                target += _TICKS_PER_DAY
        else:
            target = int(time)
            if target < 0:
                raise NBTError(f"day time must be >= 0, got {target}")
        clock["total_ticks"] = TagLong(target)
        return target

    # -- persistence hooks -------------------------------------------------

    def _settings_files_to_save(self) -> list[tuple[NBT, str]]:
        """The split files that have been touched, for :meth:`save`.

        Each handle goes back to the path it was loaded from. Re-deriving
        the path from the stem would send a dimension's border file to the
        world-wide location, writing a file the game does not read and
        leaving the real one stale.
        """
        return [
            (handle, os.path.join(self.path, *relative.split("/")))
            for relative, handle in sorted(self._settings_files.items())
        ]
