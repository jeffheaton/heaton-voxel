// Twin of heaton_voxel/world/save.py: the save itself — loading, dimension
// resolution, writing, reporting. The operational concerns live in sibling
// partial-class files (Save.Player.cs, Save.Editing.cs, ...), one file each,
// mirroring the Python mixins (see PORTING.md).

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using HeatonVoxel.Nbt;

namespace HeatonVoxel.World
{
    public sealed partial class MinecraftSave
    {
        public readonly string Path;
        public NbtFile? Level;
        public readonly Dictionary<string, Dimension> Dimensions =
            new Dictionary<string, Dimension>(StringComparer.Ordinal);

        /// <summary>"current", or "old" for a save in the layout before
        /// Minecraft 26.1 (<see cref="DimensionNames.StorageDataVersion"/>):
        /// its overworld in region/ beside level.dat, the nether under
        /// DIM-1/, the End under DIM1/, and its settings and single player
        /// in level.dat. Such a save is read where it stands and every write
        /// to it refused (<see cref="ReadOnly"/>).</summary>
        public string Layout { get; private set; } = "current";

        /// <summary>Why every write to this save is refused, or null.</summary>
        public string? ReadOnly { get; private set; }

        private MinecraftSave(string path)
        {
            Path = System.IO.Path.GetFullPath(ExpandUser(path));
        }

        private static string ExpandUser(string path)
        {
            if (path == "~" || path.StartsWith("~/", StringComparison.Ordinal))
            {
                string home = Environment.GetFolderPath(
                    Environment.SpecialFolder.UserProfile);
                return home + path.Substring(1);
            }
            return path;
        }

        /// <summary>Open a save. Region files are indexed but not read:
        /// each one is opened the first time something asks for a chunk
        /// inside it. Pass eager to read everything up front. A save in the
        /// layout before Minecraft 26.1 opens where it stands, read-only;
        /// one holding region directories in both layouts is refused, as is
        /// one whose level.dat is newer than the old layout.</summary>
        public static MinecraftSave Load(string path,
                                         IEnumerable<string>? dimensions = null,
                                         bool eager = false)
        {
            var save = new MinecraftSave(path);
            string levelPath = System.IO.Path.Combine(save.Path, "level.dat");
            if (!File.Exists(levelPath))
                throw new NbtException(
                    $"'{save.Path}' does not look like a Minecraft save: no level.dat");
            save.Level = NbtFile.Load(levelPath);

            string Local(string relative) => System.IO.Path.Combine(
                save.Path, relative.Replace('/', System.IO.Path.DirectorySeparatorChar));
            bool current = DimensionNames.Directories.Values.Any(r => Directory.Exists(Local(r)));
            bool old = DimensionNames.OldDirectories.Values.Any(r => Directory.Exists(Local(r)));
            if (current && old)
                throw new NbtException(
                    $"'{save.Path}' holds region directories in both layouts, under "
                    + "dimensions/ and at the save's root (region/, DIM-1/, DIM1/). The "
                    + "game moves the old ones when it opens a world, so a save holding "
                    + "both was put together by hand, and which of them is the world "
                    + "cannot be told");
            var table = DimensionNames.Directories;
            if (old)
            {
                int? stamp = (save.Level.Root?.Get("Data") as TagCompound)?.Get("DataVersion")
                    is Tag found ? Tag.AsInt(found) : (int?)null;
                if (stamp is int newer && newer >= DimensionNames.StorageDataVersion)
                    throw new NbtException(
                        $"'{save.Path}' keeps its regions in the layout before 26.1, but "
                        + $"its level.dat is DataVersion {newer}, which keeps them "
                        + "under dimensions/: the two do not belong together");
                save.Layout = "old";
                table = DimensionNames.OldDirectories;
                string version = stamp is int v ? $", DataVersion {v}" : "";
                save.ReadOnly =
                    $"'{save.Path}' is in the save layout before Minecraft 26.1{version}, "
                    + "which a current Minecraft moves into the new one the first time "
                    + "it opens it";
            }

            var wanted = dimensions?.ToList() ?? table.Keys.ToList();
            foreach (string name in wanted)
            {
                if (!table.TryGetValue(name, out var relative))
                {
                    string known = string.Join(
                        ", ", table.Keys.OrderBy(k => k, StringComparer.Ordinal));
                    throw new NbtException(
                        $"unknown dimension '{name}'; expected one of {known}");
                }
                string directory = Local(relative);
                if (!Directory.Exists(directory))
                    continue;  // dimension never visited
                var dimension = new Dimension(name, directory, save.ReadOnly);
                dimension.Index();
                if (eager)
                    dimension.LoadAll();
                save.Dimensions[name] = dimension;
            }

            if (save.Dimensions.Count == 0)
                throw new NbtException(
                    $"'{save.Path}' has no region directories: none under dimensions/, "
                    + "and none of the layout before 26.1 (region/, DIM-1/region, "
                    + "DIM1/region) either.");
            return save;
        }

        /// <summary>Throw ReadOnlySaveException if this save is in the
        /// layout before 26.1, saying it will not <paramref name="doing"/>
        /// it.</summary>
        internal void RefuseReadOnly(string doing = "write")
        {
            if (ReadOnly != null)
                throw new ReadOnlySaveException(
                    $"{ReadOnly}; the library reads it but will not {doing} it");
        }

        /// <summary>A loaded dimension. With no name, prefers the one the
        /// player is in, then the overworld, then the only one loaded — the
        /// player is the best guess at intent, not a requirement.</summary>
        public Dimension Dimension(string? name = null)
        {
            name ??= DefaultDimension();
            if (Dimensions.TryGetValue(name, out var found))
                return found;
            string known = Dimensions.Count > 0
                ? string.Join(", ", Dimensions.Keys.OrderBy(k => k, StringComparer.Ordinal))
                : "none";
            throw new NbtException($"dimension '{name}' is not loaded (have: {known})");
        }

        private string DefaultDimension()
        {
            string? where;
            try
            {
                where = PlayerDimension();
            }
            catch (NbtException)
            {
                where = null;
            }
            if (where != null && Dimensions.ContainsKey(where))
                return where;
            if (Dimensions.ContainsKey(DimensionNames.Overworld))
                return DimensionNames.Overworld;
            if (Dimensions.Count == 1)
                return Dimensions.Keys.First();
            throw new NbtException(
                "cannot tell which dimension you mean: there is no player data "
                + "and no overworld loaded. Pass dimension explicitly (have: "
                + string.Join(", ", Dimensions.Keys.OrderBy(k => k, StringComparer.Ordinal))
                + ").");
        }

        /// <summary>Write back the files that actually changed. Returns how
        /// many. A region is a candidate only if it was opened at all, and
        /// is written only if serializing it produces different bytes from
        /// the ones it was loaded with. Refuses by default if the world is
        /// open in Minecraft.
        ///
        /// <paramref name="beforeWrite"/> is called with the full path of
        /// every file the save is about to replace, create or remove, before
        /// it is touched (<see cref="NbtFile.Save"/>), and an exception from
        /// it stops the save there: every file written before it was offered
        /// to it first, and nothing after it is written.</summary>
        public int Save(bool force = false, bool touchModified = false,
                        bool forceAll = false, Action<string>? beforeWrite = null)
        {
            RefuseReadOnly("save");
            if (ActiveEdit != null)
                throw new NbtException("complete or roll back the active edit before saving");
            int written = 0;
            foreach (var dim in Dimensions.Values)
            {
                foreach (var region in dim.LoadedRegions())
                {
                    if (!region.FromDisk)
                    {
                        // Made in memory and never given a chunk: writing it
                        // would leave an empty container file behind.
                        if (region.Chunks.Count == 0)
                            continue;
                        // CreateChunks may build the first region of a
                        // dimension the world has never visited.
                        string directory = System.IO.Path.GetDirectoryName(region.Path ?? "") ?? "";
                        if (directory.Length > 0 && !Directory.Exists(directory))
                            Directory.CreateDirectory(directory);
                    }
                    if (region.Save(force: force, touchModified: touchModified,
                                    skipUnchanged: !forceAll, beforeWrite: beforeWrite))
                        written++;
                }
                foreach (var region in dim.EntityRegions.Values.ToList())
                {
                    if (!region.FromDisk && region.Chunks.Count == 0)
                        continue;  // created in memory but never given a chunk
                    // A region built by EntityRegion(create) may be the first
                    // entity ever stored here; entities/ may not exist yet.
                    string directory = System.IO.Path.GetDirectoryName(
                        region.Path ?? "") ?? "";
                    if (directory.Length > 0 && !Directory.Exists(directory))
                        Directory.CreateDirectory(directory);
                    if (region.Save(force: force, touchModified: touchModified,
                                    skipUnchanged: !forceAll, beforeWrite: beforeWrite))
                        written++;
                }
                foreach (var region in dim.PoiRegions.Values.ToList())
                {
                    if (region.Save(force: force, touchModified: touchModified,
                                    skipUnchanged: !forceAll, beforeWrite: beforeWrite))
                        written++;
                }
            }
            if (_player != null && _playerPath != null)
            {
                if (_player.Save(_playerPath, force: force,
                                 skipUnchanged: !forceAll, beforeWrite: beforeWrite))
                    written++;
            }
            // level.dat and the split settings files ride the same rule as
            // everything else: candidates only once loaded, written only
            // when their bytes would differ.
            if (Level != null)
            {
                if (Level.Save(System.IO.Path.Combine(Path, "level.dat"),
                               force: force, skipUnchanged: !forceAll,
                               beforeWrite: beforeWrite))
                    written++;
            }
            foreach (var (handle, path) in SettingsFilesToSave())
            {
                if (handle.Save(path, force: force, skipUnchanged: !forceAll,
                                beforeWrite: beforeWrite))
                    written++;
            }
            return written;
        }

        /// <summary>Region files present in the save, loaded or not.</summary>
        public int RegionCount =>
            Dimensions.Values.Sum(dim => dim.Available.Count);

        public int LoadedRegionCount =>
            Dimensions.Values.Sum(dim => dim.Regions.Count);

        /// <summary>Chunks in the regions opened so far.</summary>
        public int ChunkCount =>
            Dimensions.Values.Sum(
                dim => dim.LoadedRegions().Sum(region => region.Chunks.Count));

        /// <summary>Roughly how many bytes of compressed chunk data are held.</summary>
        public long MemoryEstimate() =>
            Dimensions.Values.Sum(
                dim => dim.LoadedRegions().Sum(
                    region => region.Chunks.Values.Sum(
                        chunk => (long)chunk.CompressedSize)));
    }
}
