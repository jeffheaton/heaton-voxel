// Twin of heaton_voxel/world/player.py: the player's side of a save —
// position, spawn, and the "~" notation.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using HeatonVoxel.Nbt;

namespace HeatonVoxel.World
{
    public sealed partial class MinecraftSave
    {
        private NbtFile? _player;
        private string? _playerPath;

        /// <summary>A dimension as a number, the way a player compound
        /// named it before 1.16 -- and still does, in a file the game has
        /// moved and not yet loaded -- read as the game's
        /// LegacyDimensionIdFix reads it: any number but -1 and 1 is the
        /// overworld.</summary>
        private static readonly Dictionary<int, string> LegacyDimensionIds =
            new Dictionary<int, string>
            {
                [-1] = DimensionNames.Nether,
                [1] = DimensionNames.End,
            };

        private static string UuidText(IEnumerable<int> words)
        {
            string digits = string.Concat(words.Select(word => ((uint)word).ToString("x8")));
            return $"{digits.Substring(0, 8)}-{digits.Substring(8, 4)}-"
                + $"{digits.Substring(12, 4)}-{digits.Substring(16, 4)}-"
                + $"{digits.Substring(20, 12)}";
        }

        /// <summary>The uuid a player compound carries: four ints from
        /// 1.16, two longs before (PlayerUUIDFix).</summary>
        private static string? PlayerUuid(TagCompound tag)
        {
            if (tag.Get("UUID") is TagIntArray words && words.Values.Length == 4)
                return UuidText(words.Values);
            if (tag.Get("UUIDMost") is Tag most && tag.Get("UUIDLeast") is Tag least)
            {
                ulong m = unchecked((ulong)Tag.AsLong(most));
                ulong l = unchecked((ulong)Tag.AsLong(least));
                return UuidText(new[]
                {
                    unchecked((int)(m >> 32)), unchecked((int)m),
                    unchecked((int)(l >> 32)), unchecked((int)l),
                });
            }
            return null;
        }

        /// <summary>Find the single-player's data. It lives in
        /// players/data/&lt;uuid&gt;.dat, and level.dat names the player
        /// through singleplayer_uuid — four signed 32-bit words, most
        /// significant first, which is how Minecraft encodes a UUID in NBT.</summary>
        private TagCompound LoadPlayer()
        {
            if (_player != null)
                return _player.Root!;
            if (Layout == "old")
                return LoadOldPlayer();

            string directory = System.IO.Path.Combine(Path, "players", "data");
            var candidates = new List<string>();

            var data = (TagCompound)Level!.Root!["Data"];
            if (data.Get("singleplayer_uuid") is TagIntArray rawUuid
                && rawUuid.Values.Length == 4)
                candidates.Add(System.IO.Path.Combine(directory, UuidText(rawUuid.Values) + ".dat"));

            // A world with no singleplayer_uuid still has exactly one player
            // file if it has only ever been played locally.
            if (Directory.Exists(directory))
            {
                candidates.AddRange(
                    Directory.GetFiles(directory)
                        .Where(p => p.EndsWith(".dat", StringComparison.Ordinal))
                        .OrderBy(p => p, StringComparer.Ordinal));
            }

            foreach (string candidate in candidates)
            {
                if (File.Exists(candidate))
                {
                    _player = NbtFile.Load(candidate);
                    _playerPath = candidate;
                    return _player.Root!;
                }
            }

            throw new NbtException(
                $"no player data in '{Path}': expected a .dat under players/data/");
        }

        /// <summary>The player of a save in the layout before 26.1, as the
        /// game keeps it when it moves the save: playerdata/&lt;uuid&gt;.dat
        /// of the player level.dat holds, which PlayerStorageFileFix moves
        /// to players/data/ while LevelDatToSavedDataFileFix drops
        /// level.dat's own copy and keeps only its uuid. Where that file is
        /// missing the copy in level.dat is read as it stands -- the game
        /// would lose it -- and a save with no player in level.dat (a
        /// server's) reads the first of playerdata/ in name order.</summary>
        private TagCompound LoadOldPlayer()
        {
            string directory = System.IO.Path.Combine(Path, "playerdata");
            var data = (TagCompound)Level!.Root!["Data"];
            var embedded = data.Get("Player") as TagCompound;
            if (embedded != null && PlayerUuid(embedded) is string uuid)
            {
                string candidate = System.IO.Path.Combine(directory, uuid + ".dat");
                if (File.Exists(candidate))
                {
                    _player = NbtFile.Load(candidate);
                    _playerPath = candidate;
                    return _player.Root!;
                }
            }
            if (embedded != null)
            {
                _player = new NbtFile { Root = embedded };
                _playerPath = null;
                return embedded;
            }
            if (Directory.Exists(directory))
            {
                foreach (string path in Directory.GetFiles(directory)
                             .Where(p => p.EndsWith(".dat", StringComparison.Ordinal))
                             .OrderBy(p => p, StringComparer.Ordinal))
                {
                    _player = NbtFile.Load(path);
                    _playerPath = path;
                    return _player.Root!;
                }
            }
            throw new NbtException(
                $"no player data in '{Path}': no Player in level.dat and no .dat "
                + "under playerdata/");
        }

        /// <summary>The player's exact position in global coordinates: the
        /// entity position, y at the feet, values fractional.</summary>
        public (double X, double Y, double Z) PlayerPosition()
        {
            if (LoadPlayer().Get("Pos") is not TagList pos || pos.Count != 3)
                throw new NbtException(
                    "player Pos has "
                    + (LoadPlayer().Get("Pos") is TagList p ? p.Count : 0)
                    + " entries, expected 3");
            return (Tag.AsDouble(pos[0]), Tag.AsDouble(pos[1]), Tag.AsDouble(pos[2]));
        }

        /// <summary>The block the player is standing in. Floors rather than
        /// truncates, so it stays correct at negative coordinates.</summary>
        public (int X, int Y, int Z) PlayerBlockPosition()
        {
            var (x, y, z) = PlayerPosition();
            return ((int)Math.Floor(x), (int)Math.Floor(y), (int)Math.Floor(z));
        }

        /// <summary>Move the player. Nothing reaches disk until Save().
        ///
        /// These are entity coordinates, not block coordinates: y is the
        /// feet, so standing someone on top of the block at by means by + 1,
        /// and the middle of a block column is bx + 0.5.
        ///
        /// Momentum and fall distance are cleared unless keepMotion, because
        /// a teleport should not arrive still traveling: a player moved
        /// while falling keeps the fall and takes the damage on load, which
        /// is a surprising way for an editor to hurt someone.</summary>
        public (double X, double Y, double Z) SetPlayerPosition(
            double x, double y, double z, bool keepMotion = false)
        {
            RefuseReadOnly("move the player of");
            var root = LoadPlayer();
            if (root.Get("Pos") is not TagList pos || pos.Count != 3)
                throw new NbtException("player data has no Pos to move");
            pos[0] = new TagDouble(x);
            pos[1] = new TagDouble(y);
            pos[2] = new TagDouble(z);

            if (!keepMotion)
            {
                if (root.Get("Motion") is TagList motion && motion.Count == 3)
                {
                    for (int axis = 0; axis < 3; axis++)
                        motion[axis] = new TagDouble(0.0);
                }
                // Both spellings exist across versions; write whichever is
                // there rather than inventing a second one beside it.
                foreach (string name in new[] { "fall_distance", "FallDistance" })
                {
                    if (root.ContainsKey(name))
                        root[name] = new TagFloat(0f);
                }
            }
            return (x, y, z);
        }

        /// <summary>The player's yaw and pitch, in degrees. Yaw follows the
        /// game's convention: 0 faces south (+z), 90 west (-x), 180 north
        /// (-z), 270 east (+x); values may be negative or wrap past 360.
        /// Pitch is -90 straight up to 90 straight down.</summary>
        public (double Yaw, double Pitch) PlayerRotation()
        {
            if (LoadPlayer().Get("Rotation") is not TagList rotation
                || rotation.Count != 2)
                throw new NbtException("player data has no Rotation");
            return (Tag.AsDouble(rotation[0]), Tag.AsDouble(rotation[1]));
        }

        /// <summary>Which dimension the player is in. A number, as a player
        /// saved before 1.16 names it, is read as the game reads it
        /// (LegacyDimensionIds).</summary>
        public string PlayerDimension()
        {
            var found = LoadPlayer().Get("Dimension");
            if (found == null)
                return DimensionNames.Overworld;
            if (found is TagString text)
                return text.Value;
            return LegacyDimensionIds.TryGetValue(Tag.AsInt(found), out var name)
                ? name : DimensionNames.Overworld;
        }

        /// <summary>The world spawn point: level.dat's spawn compound, or the
        /// SpawnX/SpawnY/SpawnZ a level.dat before 4548 kept, which the
        /// game's WorldSpawnDataFix makes it.</summary>
        public (int X, int Y, int Z) SpawnPosition()
        {
            var data = (TagCompound)Level!.Root!["Data"];
            if (data.Get("spawn") is not TagCompound spawn
                || spawn.Get("pos") is not Tag pos)
            {
                if (data.Get("SpawnX") is Tag sx && data.Get("SpawnY") is Tag sy
                    && data.Get("SpawnZ") is Tag sz)
                    return (Tag.AsInt(sx), Tag.AsInt(sy), Tag.AsInt(sz));
                throw new NbtException("level.dat has no spawn position");
            }
            int[] values = pos switch
            {
                TagIntArray array => array.Values,
                TagList list => list.Select(Tag.AsInt).ToArray(),
                _ => throw new NbtException("level.dat spawn pos has an unexpected shape"),
            };
            if (values.Length < 3)
                throw new NbtException("level.dat has no spawn position");
            return (values[0], values[1], values[2]);
        }

        /// <summary>Move the world spawn point. Nothing reaches disk until
        /// Save(). These are block coordinates. The spawn's dimension and
        /// facing are kept as they are unless given — moving the spawn
        /// point is usually about WHERE, and silently resetting the rest
        /// would surprise. Refuses a save whose level.dat predates the
        /// spawn compound (Minecraft 26.x) rather than inventing the old
        /// scalar fields.</summary>
        public (int X, int Y, int Z) SetSpawnPosition(
            int x, int y, int z, string? dimension = null,
            double? yaw = null, double? pitch = null)
        {
            RefuseReadOnly("move the spawn of");
            var data = (TagCompound)Level!.Root!["Data"];
            if (data.Get("spawn") is not TagCompound spawn)
                throw new NbtException(
                    "level.dat has no spawn compound: this world predates "
                    + "the current layout (Minecraft 26.x)");
            spawn["pos"] = new TagIntArray(new[] { x, y, z });
            if (dimension != null)
                spawn["dimension"] = new TagString(dimension);
            if (yaw.HasValue)
                spawn["yaw"] = new TagFloat((float)yaw.Value);
            if (pitch.HasValue)
                spawn["pitch"] = new TagFloat((float)pitch.Value);
            return (x, y, z);
        }

        /// <summary>Turn coordinate tokens into absolute block coordinates.
        ///
        /// Understands the game's relative notation alongside plain
        /// integers: "~" is the player's block position on that axis, "~5"
        /// five blocks beyond it, "~-3" three short of it. Which axis each
        /// token belongs to comes from axes, a string of x/y/z letters, one
        /// per token; left unset, tokens cycle x, y, z. The player is
        /// looked up only when a "~" actually appears, so absolute
        /// coordinates keep working in a world with no player data.</summary>
        public int[] ResolveCoordinates(IReadOnlyList<string> tokens,
                                        string? axes = null)
        {
            if (axes == null)
            {
                if (tokens.Count % 3 != 0)
                    throw new NbtException(
                        $"expected coordinates in x/y/z groups, got {tokens.Count} "
                        + "values; pass axes to name the axis of each");
                axes = string.Concat(
                    Enumerable.Repeat("xyz", (tokens.Count + 2) / 3))
                    .Substring(0, tokens.Count);
            }
            if (axes.Length != tokens.Count)
                throw new NbtException(
                    $"{tokens.Count} coordinates but {axes.Length} axis letters ('{axes}')");

            (int X, int Y, int Z)? player = null;
            var resolved = new int[tokens.Count];
            for (int i = 0; i < tokens.Count; i++)
            {
                string token = tokens[i].Trim();
                char axis = axes[i];
                int index = "xyz".IndexOf(axis);
                if (index < 0)
                    throw new NbtException($"axis letters must be x, y or z, got '{axis}'");
                if (token.StartsWith("~", StringComparison.Ordinal))
                {
                    if (player == null)
                    {
                        try
                        {
                            player = PlayerBlockPosition();
                        }
                        catch (NbtException exc)
                        {
                            throw new NbtException(
                                $"cannot resolve '{token}': relative coordinates "
                                + $"need a player position, and {exc.Message}");
                        }
                    }
                    string rest = token.Substring(1);
                    int delta = 0;
                    if (rest.Length > 0 && !int.TryParse(
                            rest, System.Globalization.NumberStyles.AllowLeadingSign,
                            System.Globalization.CultureInfo.InvariantCulture, out delta))
                        throw new NbtException(
                            $"bad relative coordinate '{token}': expected ~, ~N or ~-N");
                    int baseValue = index switch
                    {
                        0 => player.Value.X,
                        1 => player.Value.Y,
                        _ => player.Value.Z,
                    };
                    resolved[i] = baseValue + delta;
                    continue;
                }
                if (!int.TryParse(
                        token, System.Globalization.NumberStyles.AllowLeadingSign,
                        System.Globalization.CultureInfo.InvariantCulture,
                        out resolved[i]))
                {
                    if (token.IndexOf(System.IO.Path.DirectorySeparatorChar) >= 0)
                        // A shell expanded the user's bare ~ into their home
                        // directory before this code ever saw it.
                        throw new NbtException(
                            $"coordinate '{token}' looks like a shell-expanded home "
                            + "directory; quote relative coordinates so the shell "
                            + "passes them through, e.g. '~' or '~-5'");
                    throw new NbtException(
                        $"bad coordinate '{token}': expected an integer, ~, ~N or ~-N");
                }
            }
            return resolved;
        }
    }
}
