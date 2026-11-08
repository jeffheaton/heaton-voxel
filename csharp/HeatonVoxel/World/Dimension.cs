// Twin of heaton_voxel/world/dimension.py: one dimension's files — terrain
// regions, entity storage, points of interest — addressed by global
// coordinates and loaded on demand.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Collections;
using System.Text.RegularExpressions;
using HeatonVoxel.Blocks;
using HeatonVoxel.Nbt;

namespace HeatonVoxel.World
{
    /// <summary>The dimension names and their region directories — the
    /// twin of Python's module-level OVERWORLD/NETHER/END constants.
    /// (Named this way because MinecraftSave.Dimensions, the loaded-
    /// dimension map, would otherwise shadow it inside that class.)</summary>
    public static class DimensionNames
    {
        public const string Overworld = "minecraft:overworld";
        public const string Nether = "minecraft:the_nether";
        public const string End = "minecraft:the_end";

        /// <summary>Each dimension's own directory. Its region, entity and
        /// poi directories hang off this, and so does its
        /// <c>data/minecraft/</c> — which is where the game keeps the
        /// settings that belong to one dimension rather than to the world,
        /// the border among them.</summary>
        internal static readonly Dictionary<string, string> Roots =
            new Dictionary<string, string>(StringComparer.Ordinal)
            {
                [Overworld] = "dimensions/minecraft/overworld",
                [Nether] = "dimensions/minecraft/the_nether",
                [End] = "dimensions/minecraft/the_end",
            };

        internal static readonly Dictionary<string, string> Directories =
            new Dictionary<string, string>(StringComparer.Ordinal)
            {
                [Overworld] = Roots[Overworld] + "/region",
                [Nether] = Roots[Nether] + "/region",
                [End] = Roots[End] + "/region",
            };

        /// <summary>Where each dimension kept its region files before the
        /// game's DimensionStorageFileFix moved them under dimensions/ at
        /// <see cref="StorageDataVersion"/>: the overworld's at the save's
        /// root, the nether's under DIM-1, the End's under DIM1. Python's
        /// _OLD_DIMENSION_DIRS.</summary>
        internal static readonly Dictionary<string, string> OldDirectories =
            new Dictionary<string, string>(StringComparer.Ordinal)
            {
                [Overworld] = "region",
                [Nether] = "DIM-1/region",
                [End] = "DIM1/region",
            };

        /// <summary>The first DataVersion whose saves keep each dimension
        /// under dimensions/minecraft/&lt;dim&gt;/ (26.1's
        /// DimensionStorageFileFix). Python's
        /// DIMENSION_STORAGE_DATA_VERSION.</summary>
        public const int StorageDataVersion = 4772;
    }

    /// <summary>A write to a save this library reads but never writes: one
    /// in the layout before Minecraft 26.1, which the game moves into the
    /// current one the first time it opens it. A ReadOnlyChunkException, so
    /// every writer that refuses or reports a chunk it will not write does
    /// the same with every chunk of such a save. Python's
    /// ReadOnlySave.</summary>
    public sealed class ReadOnlySaveException : ReadOnlyChunkException
    {
        public ReadOnlySaveException(string message) : base(message) { }
    }

    /// <summary>The grid sizes — Python's module-level SECTION, CHUNK and
    /// REGION_CHUNKS in dimension.py. A holder class because C# has no
    /// module scope, and putting Chunk on Dimension itself would collide
    /// with its Chunk(x, z) lookup.</summary>
    public static class Grid
    {
        public const int Section = 16;
        public const int Chunk = 16;
        public const int RegionChunks = 32;
    }

    public sealed class Dimension : IEnumerable<NbtFile>
    {
        private static readonly Regex RegionName =
            new Regex(@"^r\.(-?\d+)\.(-?\d+)\.mca$", RegexOptions.IgnoreCase);

        public readonly string Name;
        public readonly string RegionDir;
        public readonly string EntitiesDir;
        public readonly string PoiDir;
        public readonly Dictionary<(int X, int Z), string> Available =
            new Dictionary<(int, int), string>();
        public readonly Dictionary<(int X, int Z), NbtFile> Regions =
            new Dictionary<(int, int), NbtFile>();
        public readonly Dictionary<(int X, int Z), string> EntityAvailable =
            new Dictionary<(int, int), string>();
        public readonly Dictionary<(int X, int Z), NbtFile> EntityRegions =
            new Dictionary<(int, int), NbtFile>();
        public readonly Dictionary<(int X, int Z), string> PoiAvailable =
            new Dictionary<(int, int), string>();
        public readonly Dictionary<(int X, int Z), NbtFile> PoiRegions =
            new Dictionary<(int, int), NbtFile>();
        private readonly Dictionary<(int X, int Z), ChunkBlocks> _blocksCache =
            new Dictionary<(int, int), ChunkBlocks>();

        /// <summary>Why this dimension refuses every write -- the save it
        /// belongs to is in the layout before 26.1 -- or null; see
        /// <see cref="RefuseReadOnly"/>.</summary>
        public readonly string? ReadOnly;

        public Dimension(string name, string regionDir, string? readOnly = null)
        {
            Name = name;
            RegionDir = regionDir;
            ReadOnly = readOnly;
            string parent = Path.GetDirectoryName(regionDir)!;
            EntitiesDir = Path.Combine(parent, "entities");
            PoiDir = Path.Combine(parent, "poi");
        }

        /// <summary>Note which region files exist, without reading any.</summary>
        public int Index()
        {
            if (!Directory.Exists(RegionDir))
                return 0;
            ScanDirectory(RegionDir, Available);
            if (Directory.Exists(EntitiesDir))
                ScanDirectory(EntitiesDir, EntityAvailable);
            if (Directory.Exists(PoiDir))
                ScanDirectory(PoiDir, PoiAvailable);
            return Available.Count;
        }

        private static void ScanDirectory(
            string directory, Dictionary<(int, int), string> into)
        {
            foreach (string path in Directory.GetFiles(directory)
                         .OrderBy(p => p, StringComparer.Ordinal))
            {
                var match = RegionName.Match(Path.GetFileName(path));
                if (match.Success)
                    into[(int.Parse(match.Groups[1].Value),
                          int.Parse(match.Groups[2].Value))] = path;
            }
        }

        public int LoadAll()
        {
            foreach (var key in Available.Keys.OrderBy(k => k))
                Region(key.X, key.Z);
            return Regions.Count;
        }

        /// <summary>Throw ReadOnlySaveException if this dimension's save is
        /// in the old layout, saying it will not <paramref name="doing"/>
        /// it; nothing otherwise. Every write path asks before it changes
        /// anything.</summary>
        public void RefuseReadOnly(string doing = "write")
        {
            if (ReadOnly != null)
                throw new ReadOnlySaveException(
                    $"{ReadOnly}; the library reads it but will not {doing} it");
        }

        // -- lookup --------------------------------------------------------

        /// <summary>The region file covering these region coordinates,
        /// loading it now; null if the world has no such file, unless create,
        /// which builds an empty one in memory that reaches disk on the next
        /// Save once it holds a chunk.</summary>
        public NbtFile? Region(int regionX, int regionZ, bool create = false)
        {
            var key = (regionX, regionZ);
            if (Regions.TryGetValue(key, out var loaded))
                return loaded;
            if (!Available.TryGetValue(key, out var path))
            {
                if (!create)
                    return null;
                RefuseReadOnly("make a region file in");
                var fresh = new NbtFile
                {
                    Kind = NbtKind.Region,
                    RegionX = regionX,
                    RegionZ = regionZ,
                    Path = System.IO.Path.Combine(RegionDir, $"r.{regionX}.{regionZ}.mca"),
                };
                Available[key] = fresh.Path!;
                Regions[key] = fresh;
                return fresh;
            }
            var region = NbtFile.Load(path);
            Regions[key] = region;
            return region;
        }

        /// <summary>The chunk at these chunk coordinates, or null if never
        /// generated. Arithmetic shifts floor, keeping negatives right.</summary>
        public Chunk? Chunk(int chunkX, int chunkZ)
        {
            var region = Region(chunkX >> 5, chunkZ >> 5);
            return region?.Chunk(chunkX & 31, chunkZ & 31);
        }

        /// <summary>Block accessor for a chunk, cached. Null only when the
        /// chunk was never generated; a chunk that exists but cannot be
        /// read throws, so "nothing here" and "something here I cannot see"
        /// stay distinguishable. A chunk Minecraft 1.13 to 1.17 saved is read, and
        /// refuses every write; a writer asks WritableBlocks instead, which
        /// refuses it first.</summary>
        public ChunkBlocks? Blocks(int chunkX, int chunkZ)
        {
            var key = (chunkX, chunkZ);
            if (_blocksCache.TryGetValue(key, out var cached))
                return cached;
            var chunk = Chunk(chunkX, chunkZ);
            if (chunk == null)
                return null;
            if (chunk.Data == null)
                throw new NbtException(
                    $"chunk ({chunkX}, {chunkZ}) exists but could not be "
                    + $"decoded: {chunk.ParseError}");
            var accessor = new ChunkBlocks(chunk);  // refuses older than 1.13
            _blocksCache[key] = accessor;
            return accessor;
        }

        /// <summary>Blocks, for a caller about to write: a chunk Minecraft
        /// 1.13 to 1.17 saved, which reads like any other, throws
        /// ReadOnlyChunkException here instead, and so does every chunk of
        /// a save in the layout before 26.1 (ReadOnlySaveException). It is an NbtException, so
        /// every writer that skips and reports, or refuses, a chunk it
        /// cannot read does the same with one it will not write — before it
        /// writes anything there.</summary>
        public ChunkBlocks? WritableBlocks(int chunkX, int chunkZ)
        {
            RefuseReadOnly();
            var accessor = Blocks(chunkX, chunkZ);
            accessor?.RefuseLegacy();
            return accessor;
        }

        /// <summary>The chunk's generation stage, or null if never
        /// generated; raises if unreadable, as everywhere else.</summary>
        public string? ChunkStatus(int chunkX, int chunkZ) =>
            Blocks(chunkX, chunkZ)?.Status;

        // -- building ------------------------------------------------------

        /// <summary>The sections a vanilla dimension's chunks span, for one
        /// that holds no finished chunk to read its height off.</summary>
        private static readonly Dictionary<string, (int Low, int High)> VanillaSections =
            new Dictionary<string, (int, int)>(StringComparer.Ordinal)
            {
                [DimensionNames.Overworld] = (-4, 19),
                [DimensionNames.Nether] = (0, 15),
                [DimensionNames.End] = (0, 15),
            };

        /// <summary>The sections this dimension's chunks span, (lowest,
        /// highest): read off the first finished chunk it holds, opened
        /// regions first, or a vanilla dimension's vanilla height; refused
        /// where there is neither -- the twin of section_range.</summary>
        public (int Low, int High) SectionRange()
        {
            var regions = Regions.Values.ToList();
            foreach (var key in Available.Keys.OrderBy(k => k))
            {
                if (!Regions.ContainsKey(key))
                    regions.Add(Region(key.X, key.Z)!);
            }
            foreach (var region in regions)
            {
                foreach (var key in region.Chunks.Keys.OrderBy(k => k).ToList())
                {
                    var chunk = region.Chunks[key];
                    if (chunk.Data == null)
                        continue;
                    ChunkBlocks accessor;
                    try
                    {
                        accessor = new ChunkBlocks(chunk);
                    }
                    catch (NbtException)
                    {
                        continue;
                    }
                    if (accessor.Legacy || !accessor.Finished)
                        continue;
                    return (accessor.MinY >> 4, accessor.MaxY >> 4);
                }
            }
            if (VanillaSections.TryGetValue(Name, out var known))
                return known;
            throw new NbtException(
                $"{Name} holds no finished chunk to read its height off, "
                + "and has no vanilla height to fall back on");
        }

        /// <summary>A finished chunk of air under biome where the dimension
        /// has no chunk at all, ready to build on; its block accessor -- the
        /// twin of create_chunk. Refuses a chunk the dimension already
        /// holds, finished or not.</summary>
        public ChunkBlocks CreateChunk(int chunkX, int chunkZ, int dataVersion, string biome,
                                       (int Low, int High)? sections = null)
        {
            RefuseReadOnly("build a chunk in");
            if (Chunk(chunkX, chunkZ) != null)
                throw new NbtException(
                    $"chunk ({chunkX}, {chunkZ}) already exists; only a chunk the "
                    + "world has no record of is built, so delete it first to build "
                    + "there afresh");
            var (low, high) = sections ?? SectionRange();
            var data = ChunkBlocks.EmptyChunk(chunkX, chunkZ, dataVersion, low, high, biome);
            var region = Region(chunkX >> 5, chunkZ >> 5, create: true)!;
            var local = (chunkX & 31, chunkZ & 31);
            var chunk = new Chunk(local.Item1, local.Item2, data);
            region.Chunks[local] = chunk;
            var accessor = new ChunkBlocks(chunk);
            _blocksCache[(chunkX, chunkZ)] = accessor;
            return accessor;
        }

        // -- entities and points of interest -------------------------------

        public NbtFile? EntityRegion(int regionX, int regionZ, bool create = false)
        {
            var key = (regionX, regionZ);
            if (EntityRegions.TryGetValue(key, out var loaded))
                return loaded;
            if (!EntityAvailable.TryGetValue(key, out var path))
            {
                if (!create)
                    return null;
                RefuseReadOnly("make an entity file in");
                var fresh = new NbtFile
                {
                    Kind = NbtKind.Region,
                    RegionX = regionX,
                    RegionZ = regionZ,
                    Path = Path.Combine(EntitiesDir, $"r.{regionX}.{regionZ}.mca"),
                };
                EntityAvailable[key] = fresh.Path!;
                EntityRegions[key] = fresh;
                return fresh;
            }
            var region = NbtFile.Load(path);
            EntityRegions[key] = region;
            return region;
        }

        public Chunk? EntityChunk(int chunkX, int chunkZ, bool create = false,
                                  int? dataVersion = null)
        {
            if (create)
                RefuseReadOnly("add an entity to");
            var region = EntityRegion(chunkX >> 5, chunkZ >> 5, create);
            if (region == null)
                return null;
            var chunk = region.Chunk(chunkX & 31, chunkZ & 31);
            if (chunk == null && create)
            {
                if (dataVersion == null)
                    throw new NbtException(
                        "creating an entity chunk needs a data_version to stamp it with");
                var data = new TagCompound
                {
                    ["Position"] = new TagIntArray(new[] { chunkX, chunkZ }),
                    ["DataVersion"] = new TagInt(dataVersion.Value),
                    ["Entities"] = new TagList(TagId.Compound),
                };
                chunk = new Chunk(chunkX & 31, chunkZ & 31, data);
                region.Chunks[(chunkX & 31, chunkZ & 31)] = chunk;
            }
            return chunk;
        }

        /// <summary>POI storage — the game's cache of beds, workstations,
        /// bells and portals. Loaded on demand, never created.</summary>
        public NbtFile? PoiRegion(int regionX, int regionZ)
        {
            var key = (regionX, regionZ);
            if (PoiRegions.TryGetValue(key, out var loaded))
                return loaded;
            if (!PoiAvailable.TryGetValue(key, out var path))
                return null;
            var region = NbtFile.Load(path);
            PoiRegions[key] = region;
            return region;
        }

        // -- misc ----------------------------------------------------------

        /// <summary>World y of the topmost surface block at global (x, z),
        /// or null folding together every "no surface to report" case.</summary>
        public int? SurfaceY(int x, int z, string heightmap = "MOTION_BLOCKING")
        {
            ChunkBlocks? accessor;
            try
            {
                accessor = Blocks(x >> 4, z >> 4);
            }
            catch (NbtException)
            {
                return null;  // exists but unreadable
            }
            return accessor?.SurfaceY(x & 15, z & 15, heightmap);
        }

        /// <summary>Drop a chunk's decoded tree, flushing edits into its
        /// bytes, and its cached accessor with it.</summary>
        public void Release(int chunkX, int chunkZ)
        {
            _blocksCache.Remove((chunkX, chunkZ));
            Chunk(chunkX, chunkZ)?.Release();
        }

        public IEnumerable<NbtFile> LoadedRegions() => Regions.Values.ToList();

        internal void ClearBlockAccessors() => _blocksCache.Clear();

        /// <summary>Every region file in the dimension, opening each as
        /// it is reached — a sweep must not silently skip unread regions.
        /// Use <see cref="LoadedRegions"/> for the cheap subset.</summary>
        public IEnumerator<NbtFile> GetEnumerator()
        {
            foreach (var key in Available.Keys.OrderBy(k => k).ToList())
            {
                var region = Region(key.X, key.Z);
                if (region != null)
                    yield return region;
            }
        }

        IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
    }
}
