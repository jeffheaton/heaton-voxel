// Twin of heaton_voxel/world/settings.py: world-level settings — game mode,
// cheats, difficulty, weather, and the per-dimension day clocks.
//
// Where these live moved in Minecraft 26.x: game mode, cheats and
// difficulty stay in level.dat, but weather, the day clocks and the world
// border were split out into data/minecraft/weather.dat, world_clocks.dat
// and world_border.dat. This file writes the split layout only — the
// shapes the current game writes. A save in the layout before 26.1
// (Layout == "old") kept all of it as fields of level.dat, and is read from
// them, every setter refusing it: each answer is the one the game's own
// upgrade of that level.dat gives (tools/probe_old_layout.py). Like every
// other edit, nothing reaches disk until Save().

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using HeatonVoxel.Nbt;

namespace HeatonVoxel.World
{
    public sealed partial class MinecraftSave
    {
        public static readonly string[] GameModes =
            { "survival", "creative", "adventure", "spectator" };
        public static readonly string[] Difficulties =
            { "peaceful", "easy", "normal", "hard" };
        public static readonly string[] WeatherKinds =
            { "clear", "rain", "thunder" };

        /// <summary>The game's own named times of day, in ticks past dawn.</summary>
        public static readonly IReadOnlyDictionary<string, long> DayTimes =
            new Dictionary<string, long>
            {
                ["day"] = 1000,
                ["noon"] = 6000,
                ["night"] = 13000,
                ["midnight"] = 18000,
            };

        private const long TicksPerDay = 24000;

        /// <summary>A Difficulty number as the game's LevelDatDifficultyFix
        /// names it: a number missing is 2, and any number not here is
        /// normal.</summary>
        private static readonly Dictionary<int, string> LegacyDifficulties =
            new Dictionary<int, string> { [0] = "peaceful", [1] = "easy", [3] = "hard" };

        /// <summary>How far the nether's border is from the overworld's:
        /// the game's LevelDatToSavedDataFileFix gives every dimension the
        /// one border a save kept before 26.1, the nether's center divided
        /// by its coordinate scale.</summary>
        private const double NetherBorderScale = 8.0;

        /// <summary>The split data files touched so far, keyed by their
        /// path relative to the save root; written back by Save(). Keyed by
        /// path and not by stem because the border exists once per
        /// dimension, and a handle filed under the bare name would be
        /// written back to whichever path that name spelled.</summary>
        private readonly Dictionary<string, NbtFile> _settingsFiles =
            new Dictionary<string, NbtFile>();

        // -- the level.dat side -------------------------------------------

        private TagCompound LevelData()
        {
            if (Level?.Root == null)
                throw new NbtException("this save has no level.dat loaded");
            return (TagCompound)Level.Root["Data"];
        }

        /// <summary>The world's default game mode — the mode a NEW player
        /// joins in; players the world has seen each carry their own (see
        /// <see cref="SetGameType"/>).</summary>
        public string GameType()
        {
            var value = LevelData().Get("GameType");
            if (value == null)
                throw new NbtException("level.dat has no GameType");
            int mode = Tag.AsInt(value);
            if (mode < 0 || mode >= GameModes.Length)
                throw new NbtException(
                    $"level.dat GameType is {mode}, expected 0-3");
            return GameModes[mode];
        }

        /// <summary>Set the world's game mode by name. With includePlayer
        /// (the default) the single player's own playerGameType is set too —
        /// the world value alone is only the default for players the world
        /// has never seen, so flipping a survival world to creative without
        /// it would leave its player in survival. Pass false for a world
        /// with no player data (a server's, say).</summary>
        public void SetGameType(string mode, bool includePlayer = true)
        {
            RefuseReadOnly("change the game mode of");
            int index = Array.IndexOf(GameModes, mode);
            if (index < 0)
                throw new NbtException(
                    $"unknown game mode '{mode}'; expected one of "
                    + string.Join(", ", GameModes));
            LevelData()["GameType"] = new TagInt(index);
            if (includePlayer)
                LoadPlayer()["playerGameType"] = new TagInt(index);
        }

        /// <summary>Whether cheats are allowed in this world.</summary>
        public bool AllowCommands()
        {
            var value = LevelData().Get("allowCommands");
            if (value == null)
                throw new NbtException("level.dat has no allowCommands");
            return Tag.AsInt(value) != 0;
        }

        public void SetAllowCommands(bool allowed)
        {
            RefuseReadOnly("change the cheats of");
            LevelData()["allowCommands"] = new TagByte((sbyte)(allowed ? 1 : 0));
        }

        private TagCompound DifficultySettings()
        {
            if (LevelData().Get("difficulty_settings") is not TagCompound found)
                throw new NbtException(
                    "level.dat has no difficulty_settings compound: this "
                    + "world predates the current layout (Minecraft 26.x)");
            return found;
        }

        /// <summary>The difficulty as DifficultySettings holds it, or, for a
        /// save in the layout before 26.1 whose level.dat predates that
        /// compound, as the game's LevelDatDifficultyFix makes it of the
        /// Difficulty, hardcore and DifficultyLocked fields. For reading:
        /// every setter refuses such a save.</summary>
        private TagCompound DifficultyView()
        {
            var data = LevelData();
            if (Layout == "old" && data.Get("difficulty_settings") is not TagCompound)
            {
                int Field(string name, int fallback) =>
                    data.Get(name) is Tag tag ? Tag.AsInt(tag) : fallback;
                return new TagCompound
                {
                    ["difficulty"] = new TagString(
                        LegacyDifficulties.TryGetValue(Field("Difficulty", 2), out var named)
                            ? named : "normal"),
                    ["hardcore"] = new TagByte((sbyte)(Field("hardcore", 0) != 0 ? 1 : 0)),
                    ["locked"] = new TagByte((sbyte)(Field("DifficultyLocked", 0) != 0 ? 1 : 0)),
                };
            }
            return DifficultySettings();
        }

        /// <summary>"peaceful", "easy", "normal" or "hard".</summary>
        public string Difficulty()
        {
            var found = DifficultyView().Get("difficulty");
            string name = found != null ? Tag.AsString(found) : "";
            if (Array.IndexOf(Difficulties, name) < 0)
                throw new NbtException(
                    $"level.dat difficulty is '{name}', expected one of "
                    + string.Join(", ", Difficulties));
            return name;
        }

        /// <summary>Set the difficulty by name. Refuses while the
        /// difficulty is locked: the lock exists precisely to stop casual
        /// flipping, so overriding it silently would make it meaningless.
        /// Unlock first with SetDifficultyLocked(false) if you mean it.</summary>
        public void SetDifficulty(string name)
        {
            RefuseReadOnly("change the difficulty of");
            if (Array.IndexOf(Difficulties, name) < 0)
                throw new NbtException(
                    $"unknown difficulty '{name}'; expected one of "
                    + string.Join(", ", Difficulties));
            var settings = DifficultySettings();
            var locked = settings.Get("locked");
            if (locked != null && Tag.AsInt(locked) != 0)
                throw new NbtException(
                    "difficulty is locked; call SetDifficultyLocked(false) "
                    + "first if you mean to change it");
            settings["difficulty"] = new TagString(name);
        }

        public bool Hardcore()
        {
            var found = DifficultyView().Get("hardcore");
            return found != null && Tag.AsInt(found) != 0;
        }

        public void SetHardcore(bool value)
        {
            RefuseReadOnly("change hardcore in");
            DifficultySettings()["hardcore"] = new TagByte((sbyte)(value ? 1 : 0));
        }

        public bool DifficultyLocked()
        {
            var found = DifficultyView().Get("locked");
            return found != null && Tag.AsInt(found) != 0;
        }

        public void SetDifficultyLocked(bool value)
        {
            RefuseReadOnly("change the difficulty lock of");
            DifficultySettings()["locked"] = new TagByte((sbyte)(value ? 1 : 0));
        }

        // -- the split data/minecraft/*.dat side --------------------------

        /// <summary>Where a split settings file lives, relative to the
        /// save root. World-wide by default; a dimension names one of that
        /// dimension's own files instead — the border is the only one this
        /// class reads, but the game keeps its raids and chunk tickets in
        /// the same place.</summary>
        private static string SettingsPath(string stem, string? dimension) =>
            dimension is null
                ? $"data/minecraft/{stem}.dat"
                : $"{DimensionNames.Roots[dimension]}/data/minecraft/{stem}.dat";

        /// <summary>One of the split data files, loaded once and cached
        /// under the path it came from. Refuses when the file does not
        /// exist, naming the path rather than diagnosing the world's age.
        /// The file being absent used to be taken as proof of an older
        /// layout; it is at least as often proof that the caller asked for
        /// a dimension this world does not keep one for.</summary>
        private NbtFile SettingsFile(string stem, string? dimension = null)
        {
            string relative = SettingsPath(stem, dimension);
            if (_settingsFiles.TryGetValue(relative, out var held))
                return held;
            string path = System.IO.Path.Combine(
                Path, relative.Replace('/', System.IO.Path.DirectorySeparatorChar));
            if (!File.Exists(path))
                throw new NbtException(
                    $"'{path}' does not exist: this world keeps no {stem} "
                    + "there. A save from before Minecraft 26.x kept these "
                    + "values inside level.dat in shapes this class does not "
                    + "write, and inventing the file would produce settings "
                    + "the player's own game version never reads");
            _settingsFiles[relative] = NbtFile.Load(path);
            return _settingsFiles[relative];
        }

        private static TagCompound SettingsData(NbtFile handle, string stem)
        {
            if (handle.Root?.Get("data") is not TagCompound found)
                throw new NbtException($"{stem}.dat has no data compound");
            return found;
        }

        /// <summary>The world's weather, exactly as stored: in weather.dat,
        /// or in level.dat's own fields for a save in the layout before
        /// 26.1, which are what the game moves there.</summary>
        public WeatherState Weather()
        {
            bool old = Layout == "old";
            var data = old ? LevelData() : SettingsData(SettingsFile("weather"), "weather");
            int Field(string name) =>
                data.Get(name) is Tag tag ? Tag.AsInt(tag) : 0;
            return new WeatherState(
                raining: Field("raining") != 0,
                thundering: Field("thundering") != 0,
                rainTime: Field(old ? "rainTime" : "rain_time"),
                thunderTime: Field(old ? "thunderTime" : "thunder_time"),
                clearWeatherTime: Field(old ? "clearWeatherTime" : "clear_weather_time"));
        }

        /// <summary>Set the weather the way the game's /weather command
        /// does. duration is in ticks (20 per second) and sets how long the
        /// chosen weather holds before the game rolls its next change; left
        /// null, the timers are zeroed and the game rolls a natural duration
        /// on next load. "clear" fills the clear timer; "rain" and "thunder"
        /// fill the rain and thunder timers together, which is exactly the
        /// command's behavior.</summary>
        public void SetWeather(string kind, int? duration = null)
        {
            RefuseReadOnly("change the weather of");
            if (Array.IndexOf(WeatherKinds, kind) < 0)
                throw new NbtException(
                    $"unknown weather '{kind}'; expected one of "
                    + string.Join(", ", WeatherKinds));
            if (duration is < 0)
                throw new NbtException(
                    $"weather duration must be >= 0, got {duration}");
            int ticks = duration ?? 0;
            var data = SettingsData(SettingsFile("weather"), "weather");
            data["raining"] = new TagByte((sbyte)(kind != "clear" ? 1 : 0));
            data["thundering"] = new TagByte((sbyte)(kind == "thunder" ? 1 : 0));
            data["clear_weather_time"] = new TagInt(kind == "clear" ? ticks : 0);
            data["rain_time"] = new TagInt(kind != "clear" ? ticks : 0);
            data["thunder_time"] = new TagInt(kind != "clear" ? ticks : 0);
        }

        /// <summary>One dimension's border, read-only. The overworld by
        /// default.
        ///
        /// The border is PER DIMENSION, and the game keeps one for each at
        /// dimensions/minecraft/&lt;dim&gt;/data/minecraft/world_border.dat. It
        /// writes them unconditionally, from the world's first save, whether
        /// or not anybody has moved a border — so in practice the file is
        /// always there and Stored is always true on a current save.
        ///
        /// A missing file is still answered rather than refused, and still
        /// comes back as the default border with Stored false. That case is
        /// no longer the ordinary one it was thought to be, but it is the
        /// right answer for a dimension a world never generated.
        ///
        /// A save in the layout before 26.1 kept one border, in level.dat:
        /// flat Border* keys until 4544, a world_border compound of the
        /// current file's keys after (LegacyWorldBorderFix). The game gives
        /// every dimension that border when it moves the save
        /// (LevelDatToSavedDataFileFix), the nether's center divided by its
        /// coordinate scale of 8, and so does this, for whichever dimension
        /// was asked for. The flat keys are read the same way in a current
        /// save too, which only a hand-assembled or third-party world
        /// carries, because reporting the defaults for a world whose border
        /// somebody moved would be wrong in a way nothing downstream could
        /// detect.
        ///
        /// It has never lived in data/minecraft/world_border.dat, which is
        /// where this used to look: that path is the one shape the game does
        /// not write, and the border is the single entry in
        /// LevelDatToSavedDataFileFix that goes to a dimension while
        /// weather, clocks and game rules go world-wide.</summary>
        public WorldBorder WorldBorder(string? dimension = null)
        {
            string name = dimension ?? DimensionNames.Overworld;
            if (!DimensionNames.Roots.ContainsKey(name))
                throw new NbtException(
                    $"unknown dimension '{name}'; expected one of "
                    + "['minecraft:overworld', 'minecraft:the_end', "
                    + "'minecraft:the_nether']");
            // Touch the level first: a MinecraftSave built directly is an
            // empty shell, and asking it for a border must refuse rather
            // than hand back a default that looks like an answer.
            LevelData();

            string relative = SettingsPath("world_border", name);
            string path = System.IO.Path.Combine(
                Path, relative.Replace('/', System.IO.Path.DirectorySeparatorChar));
            if (File.Exists(path))
            {
                var data = SettingsData(
                    SettingsFile("world_border", name), "world_border");
                double D(string name, double fallback) =>
                    data.Get(name) is Tag tag ? Tag.AsDouble(tag) : fallback;
                int I(string name, int fallback) =>
                    data.Get(name) is Tag tag ? Tag.AsInt(tag) : fallback;
                double size = D("size", World.WorldBorder.DefaultSize);
                return new WorldBorder(
                    centerX: D("center_x", 0.0),
                    centerZ: D("center_z", 0.0),
                    size: size,
                    safeZone: D("safe_zone", 5.0),
                    damagePerBlock: D("damage_per_block", 0.2),
                    warningBlocks: I("warning_blocks", 5),
                    warningTime: I("warning_time", 15),
                    lerpTarget: D("lerp_target", size),
                    lerpTime: data.Get("lerp_time") is Tag lerp
                        ? Tag.AsLong(lerp) : 0,
                    stored: true);
            }

            var level = LevelData();
            double scale = name == DimensionNames.Nether ? NetherBorderScale : 1.0;
            if (level.Get("world_border") is TagCompound nested)
            {
                // 4544 to the move: the current file's keys, in level.dat.
                double D(string name, double fallback) =>
                    nested.Get(name) is Tag tag ? Tag.AsDouble(tag) : fallback;
                int I(string name, int fallback) =>
                    nested.Get(name) is Tag tag ? Tag.AsInt(tag) : fallback;
                double size = D("size", World.WorldBorder.DefaultSize);
                return new WorldBorder(
                    centerX: D("center_x", 0.0) / scale,
                    centerZ: D("center_z", 0.0) / scale,
                    size: size,
                    safeZone: D("safe_zone", 5.0),
                    damagePerBlock: D("damage_per_block", 0.2),
                    warningBlocks: I("warning_blocks", 5),
                    warningTime: I("warning_time", 15),
                    lerpTarget: D("lerp_target", size),
                    lerpTime: nested.Get("lerp_time") is Tag lerp
                        ? Tag.AsLong(lerp) : 0,
                    stored: true);
            }
            if (level.Get("BorderSize") != null)
            {
                // The monolithic layout: doubles throughout, warnings included.
                double D(string name, double fallback) =>
                    level.Get(name) is Tag tag ? Tag.AsDouble(tag) : fallback;
                double size = Tag.AsDouble(level["BorderSize"]);
                return new WorldBorder(
                    centerX: D("BorderCenterX", 0.0) / scale,
                    centerZ: D("BorderCenterZ", 0.0) / scale,
                    size: size,
                    safeZone: D("BorderSafeZone", 5.0),
                    damagePerBlock: D("BorderDamagePerBlock", 0.2),
                    warningBlocks: (int)D("BorderWarningBlocks", 5.0),
                    warningTime: (int)D("BorderWarningTime", 15.0),
                    lerpTarget: D("BorderSizeLerpTarget", size),
                    lerpTime: level.Get("BorderSizeLerpTime") is Tag lerp
                        ? Tag.AsLong(lerp) : 0,
                    stored: true);
            }

            return new WorldBorder();
        }

        private TagCompound Clock(string? dimension)
        {
            string name = dimension ?? DimensionNames.Overworld;
            if (Layout == "old")
            {
                // level.dat's DayTime, which the game moves to the
                // overworld's clock, and the only clock such a save has.
                var level = LevelData();
                if (name != DimensionNames.Overworld)
                    throw new NbtException(
                        "a save in the layout before 26.1 keeps one clock, the "
                        + $"overworld's (level.dat's DayTime), and none for '{name}'");
                if (level.Get("DayTime") is not Tag ticks)
                    throw new NbtException("level.dat has no DayTime");
                return new TagCompound { ["total_ticks"] = new TagLong(Tag.AsLong(ticks)) };
            }
            var data = SettingsData(
                SettingsFile("world_clocks"), "world_clocks");
            if (data.Get(name) is not TagCompound clock
                || clock.Get("total_ticks") == null)
            {
                string have = data.Keys.Count > 0
                    ? string.Join(", ", data.Keys.OrderBy(k => k, StringComparer.Ordinal))
                    : "none";
                throw new NbtException(
                    $"world_clocks.dat has no clock for '{name}' (has: {have})");
            }
            return clock;
        }

        /// <summary>A dimension's clock, in total ticks since its day zero.
        /// The overworld by default. Day number and time of day derive from
        /// it: ticks / 24000 and ticks % 24000, with 0 being dawn.
        /// Dimensions without a stored clock — the nether keeps none — are
        /// refused by name.</summary>
        public long DayTime(string? dimension = null) =>
            Tag.AsLong(Clock(dimension)["total_ticks"]);

        /// <summary>Set a dimension's clock to an absolute tick count — the
        /// whole clock, day count included. Returns the new total.</summary>
        public long SetDayTime(long ticks, string? dimension = null)
        {
            RefuseReadOnly("change the clock of");
            if (ticks < 0)
                throw new NbtException($"day time must be >= 0, got {ticks}");
            var clock = Clock(dimension);
            clock["total_ticks"] = new TagLong(ticks);
            return ticks;
        }

        /// <summary>Set a dimension's clock to a named time of day from
        /// <see cref="DayTimes"/> ("day", "noon", "night", "midnight"),
        /// advancing to that time's NEXT occurrence the way sleeping does:
        /// the clock never runs backward, so the moon phase and every
        /// day-counting mechanic stay consistent. Returns the new total.</summary>
        public long SetDayTime(string time, string? dimension = null)
        {
            RefuseReadOnly("change the clock of");
            if (!DayTimes.TryGetValue(time, out long past))
                throw new NbtException(
                    $"unknown time of day '{time}'; expected one of "
                    + string.Join(", ", DayTimes.Keys.OrderBy(k => k, StringComparer.Ordinal))
                    + " or an absolute tick count");
            var clock = Clock(dimension);
            long current = Tag.AsLong(clock["total_ticks"]);
            long target = current / TicksPerDay * TicksPerDay + past;
            if (target <= current)
                target += TicksPerDay;
            clock["total_ticks"] = new TagLong(target);
            return target;
        }

        // -- persistence hook ---------------------------------------------

        /// <summary>The split files that have been touched, for Save().</summary>
        private IEnumerable<(NbtFile Handle, string Path)> SettingsFilesToSave() =>
            _settingsFiles
                .OrderBy(pair => pair.Key, StringComparer.Ordinal)
                .Select(pair => (pair.Value, System.IO.Path.Combine(
                    Path,
                    pair.Key.Replace('/', System.IO.Path.DirectorySeparatorChar))));
    }
}
