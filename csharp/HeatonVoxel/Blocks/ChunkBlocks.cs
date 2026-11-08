// Twin of heaton_voxel/blocks.py (the Blocks accessor). Sanctioned rename
// #2: Python's class `Blocks` is `ChunkBlocks` here, because C# resolves a
// class named like its own namespace to the namespace. x and z are
// chunk-local (0-15); y is a world coordinate. The current chunk format,
// read and written, and Minecraft 1.13's to 1.17's, read and never written:
// every write to one throws ReadOnlyChunkException. Anything older, and the
// 1.18 snapshots' formats in between, is refused rather than misread.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using HeatonVoxel.Nbt;

namespace HeatonVoxel.Blocks
{
    /// <summary>A write to a chunk this library reads but never writes: one
    /// saved by Minecraft 1.13 to 1.17 (ChunkBlocks.LegacyMinDataVersion ..
    /// LegacyMaxDataVersion). An NbtException, so every writer that already
    /// reports or refuses a chunk it cannot use does the same with this
    /// one, naming why. Python's ReadOnlyChunk.</summary>
    public class ReadOnlyChunkException : NbtException
    {
        public ReadOnlyChunkException(string message) : base(message) { }
    }

    public sealed class ChunkBlocks : IEnumerable<Section>
    {
        // 21w39a (1.18): sections at the chunk root, no-straddle packing.
        // Everything here that writes assumes that layout.
        public const int MinDataVersion = 2825;

        // Minecraft 1.13 (1519) through 1.17.1 (2730): the formats of
        // palettes and Level, read but never written. Chunks between this
        // range and 1.18 were written only by the 1.18 snapshots, each a
        // step of the move between the layouts, and are refused rather than
        // guessed at; so is anything before 1.13, whose blocks are numbers,
        // and the 1.13 snapshots before its release. Within the range the
        // format moved twice, each at the schema of the game's own fix for
        // it: below LegacyAlignedDataVersion a section's indices and a
        // heightmap's run on across longs (BitStorageAlignFix), and below
        // LegacyCellBiomesDataVersion a chunk keeps one biome a column, 256
        // of them (ChunkBiomeFix). And what counts as finished was renamed
        // twice (ChunkStatusFix, ChunkStatusFix2; see LegacyFinished).
        public const int LegacyMinDataVersion = 1519;
        public const int LegacyMaxDataVersion = 2730;
        public const int LegacyAlignedDataVersion = 2527;
        public const int LegacyCellBiomesDataVersion = 2202;
        // Below 20w45a's schema a chunk keeps its entities in
        // Level.Entities, not in entities/ beside the region.
        public const int LegacyEntityStorageDataVersion = 2681;

        // The old overworld, the only height an old chunk is read at:
        // sections 0..15, y 0..255. A chunk storing blocks outside them --
        // a datapack changed the height -- is refused rather than read at
        // the wrong one.
        public const int LegacyMinSection = 0;
        public const int LegacyMaxSection = 15;
        public const int LegacyBiomesPerChunk = 1024;  // 4x4 columns x 64 layers
        public const int LegacyColumnBiomes = 256;     // before 1.15: one a column

        // The last DataVersion of each release the old formats span, its
        // snapshots before it: 1.13.2, 1.14.4, 1.15.2, 1.16.5 and 1.17.1.
        private static readonly (int Last, string Name)[] LegacyReleases =
        {
            (1631, "1.13"), (1976, "1.14"), (2230, "1.15"), (2586, "1.16"),
            (LegacyMaxDataVersion, "1.17"),
        };

        /// <summary>The release an old chunk's DataVersion belongs to,
        /// "1.13" to "1.17", a snapshot named for the release it led to.
        /// Python's legacy_release.</summary>
        public static string LegacyRelease(int dataVersion)
        {
            foreach (var (last, name) in LegacyReleases)
                if (dataVersion <= last)
                    return name;
            throw new ArgumentOutOfRangeException(
                nameof(dataVersion), $"DataVersion {dataVersion} is past the old formats");
        }

        /// <summary>Whether an old chunk's Status says the game finished
        /// it, by the game's own fixes: before ChunkStatusFix (1905) both
        /// fullchunk and postprocessed are finished, until ChunkStatusFix2
        /// (1911) only fullchunk, and from it full.</summary>
        public static bool LegacyFinished(string? status, int dataVersion)
        {
            if (status == null)
                return false;
            string name = status.StartsWith("minecraft:", StringComparison.Ordinal)
                ? status.Substring("minecraft:".Length) : status;
            if (dataVersion < 1905)
                return name == "fullchunk" || name == "postprocessed";
            if (dataVersion < 1911)
                return name == "fullchunk";
            return name == "full";
        }

        // 26.3, whose BlockStateFieldNamesFix targets schema 5006: from here a
        // palette entry is id/properties rather than Name/Properties, a state
        // at its block's defaults is the bare id, and a palette holding both
        // forms wraps each bare id as {"": id} so the list stays one tag
        // type. Chunks at or past this are written in that form; older ones
        // keep the form they were read in, so a world half-visited since an
        // update stays readable by the game in both halves.
        public const int ModernStateDataVersion = 5006;

        // The DataVersions of the finished chunks this library has read off
        // the game -- 26.2's and 26.3's -- and so the range EmptyChunk builds
        // a chunk for. A world newer than the last is given chunks stamped
        // with it, which the game upgrades on load; one older than the first
        // is refused, since its chunks may carry fields these do not.
        public const int EmptyChunkMinDataVersion = 4903;
        public const int EmptyChunkMaxDataVersion = 5023;

        /// <summary>A finished chunk of air under one biome: the compound
        /// the game writes for one, every key it writes and in its order,
        /// sections minSection to maxSection -- the twin of empty_chunk.
        /// Status is finished, so the game never generates over it; its
        /// heightmaps are rebuilt from its blocks; and isLightOn is 0 with no
        /// light stored, which hands the lighting to the game.</summary>
        public static TagCompound EmptyChunk(int chunkX, int chunkZ, int dataVersion,
                                             int minSection, int maxSection, string biome)
        {
            if (dataVersion < EmptyChunkMinDataVersion || dataVersion > EmptyChunkMaxDataVersion)
                throw new NbtException(
                    $"no finished chunk of DataVersion {dataVersion} has been read off the "
                    + $"game; an empty chunk is built for {EmptyChunkMinDataVersion} "
                    + $"(26.2) to {EmptyChunkMaxDataVersion} (26.3), and a newer "
                    + "world takes the latter, which the game upgrades on load");
            if (minSection > maxSection)
                throw new NbtException($"no sections between {minSection} and {maxSection}");
            if (string.IsNullOrEmpty(biome))
                throw new NbtException($"a chunk needs a biome id, not '{biome}'");
            var air = new BlockState("minecraft:air").ToNbt(dataVersion >= ModernStateDataVersion);
            var sections = new TagList(TagId.Compound);
            var postProcessing = new TagList(TagId.List);
            for (int y = minSection; y <= maxSection; y++)
            {
                var blockPalette = new TagList(air.Id);
                blockPalette.Add(air);
                var biomePalette = new TagList(TagId.String);
                biomePalette.Add(new TagString(biome));
                sections.Add(new TagCompound
                {
                    ["block_states"] = new TagCompound { ["palette"] = blockPalette },
                    ["biomes"] = new TagCompound { ["palette"] = biomePalette },
                    ["Y"] = new TagByte((sbyte)y),
                });
                postProcessing.Add(new TagList(TagId.End));
            }
            var data = new TagCompound
            {
                ["Status"] = new TagString("minecraft:full"),
                ["zPos"] = new TagInt(chunkZ),
                ["block_entities"] = new TagList(TagId.End),
                ["yPos"] = new TagInt(minSection),
                ["LastUpdate"] = new TagLong(0),
                ["structures"] = new TagCompound
                {
                    ["References"] = new TagCompound(),
                    ["starts"] = new TagCompound(),
                },
                ["InhabitedTime"] = new TagLong(0),
                ["xPos"] = new TagInt(chunkX),
                ["Heightmaps"] = new TagCompound(),      // filled below, in its place
                ["sections"] = sections,
                ["isLightOn"] = new TagByte(0),
                ["block_ticks"] = new TagList(TagId.End),
                ["PostProcessing"] = postProcessing,
                ["DataVersion"] = new TagInt(dataVersion),
                ["fluid_ticks"] = new TagList(TagId.End),
            };
            new ChunkBlocks(data).RebuildHeightmaps();
            return data;
        }

        public static readonly string[] HeightmapNames =
        {
            "WORLD_SURFACE", "MOTION_BLOCKING",
            "MOTION_BLOCKING_NO_LEAVES", "OCEAN_FLOOR",
        };

        // The order the game writes the four in, which a rebuild keeps: a
        // map that was right comes back the same bytes.
        private static readonly string[] StoredHeightmaps =
        {
            "OCEAN_FLOOR", "MOTION_BLOCKING_NO_LEAVES",
            "MOTION_BLOCKING", "WORLD_SURFACE",
        };

        /// <summary>Which of the four maps stop at a state: one bit each,
        /// bit i for HeightmapNames[i]. WORLD_SURFACE stops at anything but
        /// air, OCEAN_FLOOR at a block the game's
        /// blocks_motion_in_heightmap tag names, and the two motion-blocking
        /// maps at a block their tags name or a state that holds fluid -- a
        /// waterlogged state, an omitted waterlogged taking the block's
        /// default, as the game reads it. A block the game has never heard
        /// of counts: the unknown is solid.</summary>
        public static int HeightmapMask(BlockState state)
        {
            string name = state.Name;
            if (AirBlocks.Contains(name))
                return 0;
            bool solid = !HeightmapBlocks.NotInHeightmap.Contains(name);
            bool fluid = HeightmapBlocks.Fluids.Contains(name);
            if (!fluid)
            {
                foreach (var pair in BlockDefaults.DefaultProperties(name))
                {
                    if (pair.Key == "waterlogged")
                    {
                        fluid = (state.GetProperty("waterlogged") ?? pair.Value) == "true";
                        break;
                    }
                }
            }
            int mask = 1;
            if (solid || fluid)
                mask |= 2;
            if ((solid && !HeightmapBlocks.Leaves.Contains(name)) || fluid)
                mask |= 4;
            if (solid)
                mask |= 8;
            return mask;
        }

        /// <summary>Whether a chunk's Status says the game finished
        /// generating it: minecraft:full, or the bare full of a 1.17 chunk
        /// or a hand-made one. Anything else is a chunk the generator set
        /// aside part-way, and the game generates over whatever is written
        /// into it.</summary>
        public static bool ChunkFinished(string? status) =>
            status == "minecraft:full" || status == "full";

        /// <summary>What the game calls air: its #minecraft:air tag
        /// (data/minecraft/tags/block/air.json), exactly these three. The
        /// carvers left cave_air in every cave cut before 1.18, dungeons,
        /// mineshafts and strongholds still write it, and the game answers
        /// void_air past the ends of the world; its isAir is true of all
        /// three, so a test for "is this cell empty" that names only
        /// minecraft:air sees a wall in every old cave. One set for the
        /// whole library, in both twins, and read-only as Python's
        /// frozenset is: the schematic paste mask, the heightmap rebuild and
        /// unfreeze all read it, so a consumer that could add to it would
        /// change every one of them at once.</summary>
        public static readonly IReadOnlyCollection<string> AirBlocks =
            new HashSet<string>(StringComparer.Ordinal)
            {
                "minecraft:air", "minecraft:cave_air", "minecraft:void_air",
            };

        private readonly TagCompound _data;
        public readonly int DataVersion;

        /// <summary>Whether this is a chunk Minecraft 1.13 to 1.17.1 saved,
        /// which reads like any other and refuses every write.</summary>
        public readonly bool Legacy;

        /// <summary>Whether this chunk's palettes use the 26.3 state codec;
        /// see <see cref="ModernStateDataVersion"/>. Decided once per chunk,
        /// by the DataVersion the game stamped on it, so an edit writes the
        /// form the game that made the chunk will read back.</summary>
        public readonly bool ModernStates;
        public readonly Dictionary<int, Section> Sections = new Dictionary<int, Section>();
        private readonly Dictionary<string, int[]?> _heightmaps =
            new Dictionary<string, int[]?>(StringComparer.Ordinal);

        public ChunkBlocks(Chunk chunk)
            : this(chunk.Data ?? throw new NbtException("chunk has no readable NBT")) { }

        public ChunkBlocks(TagCompound data)
        {
            _data = data;
            DataVersion = data.Get("DataVersion") is Tag version ? Tag.AsInt(version) : 0;
            ModernStates = DataVersion >= ModernStateDataVersion;
            Legacy = DataVersion >= LegacyMinDataVersion && DataVersion <= LegacyMaxDataVersion;
            if (DataVersion != 0 && DataVersion < MinDataVersion && !Legacy)
                throw new NbtException(
                    $"chunk DataVersion {DataVersion} is in neither format this "
                    + $"library reads: Minecraft 1.18 and later (DataVersion "
                    + $"{MinDataVersion}+), or 1.13 to 1.17.1 "
                    + $"({LegacyMinDataVersion}-{LegacyMaxDataVersion}), read "
                    + "only. Open the world in a current Minecraft to upgrade it "
                    + "first.");
            if (Legacy)
            {
                // A chunk 1.13 to 1.17 saved: everything under Level,
                // block-carrying sections only where they hold something, a
                // world of y 0..255, and the biomes one array of numbers.
                _data = data.Get("Level") as TagCompound
                    ?? throw new NbtException(
                        $"{LegacyWhere} has no 'Level' compound");
                ReadLegacy();
                MinY = LegacyMinSection * 16;
                MaxY = (LegacyMaxSection + 1) * 16 - 1;
                return;
            }

            if (data.Get("sections") is not TagList raw)
                throw new NbtException(
                    "chunk has no 'sections' list"
                    + (DataVersion == 0 ? " (is this a pre-1.18 chunk?)" : ""));
            foreach (var tag in raw)
            {
                var section = new Section((TagCompound)tag, ModernStates);
                Sections[section.Y] = section;
            }

            // The world's vertical extent, which is not the span of the
            // stored sections: the game keeps light one section past each
            // end of the world, so a chunk can hold a section at Y=-5 or
            // Y=20 with light and no blocks. Only sections carrying blocks
            // are the world, and the floor is the one the chunk records,
            // which is the origin every heightmap entry counts from.
            var held = Sections
                .Where(pair => pair.Value.RawTag.Get("block_states") != null)
                .Select(pair => pair.Key)
                .OrderBy(y => y)
                .ToList();
            if (data.Get("yPos") is Tag floor)
                MinY = Tag.AsInt(floor) * 16;
            else
                MinY = held.Count > 0 ? held[0] * 16 : 0;
            // A chunk storing no blocks at all spans nothing.
            MaxY = held.Count > 0 ? (held[held.Count - 1] + 1) * 16 - 1 : MinY - 1;
        }

        private string LegacyWhere =>
            $"chunk at DataVersion {DataVersion} (Minecraft {LegacyRelease(DataVersion)})";

        private void ReadLegacy()
        {
            foreach (var key in new[] { "xPos", "zPos" })
                if (_data.Get(key) == null)
                    throw new NbtException(
                        $"{LegacyWhere} has no {key}, so there is no telling where it stands");
            string refusal = Refusal("write");
            string[]? names = null;
            if (_data.Get("Biomes") is TagIntArray biomes)
            {
                bool columns = DataVersion < LegacyCellBiomesDataVersion;
                int wanted = columns ? LegacyColumnBiomes : LegacyBiomesPerChunk;
                if (biomes.Values.Length != wanted)
                    throw new NbtException(
                        $"{LegacyWhere} at ({WorldX}, {WorldZ}) stores "
                        + $"{biomes.Values.Length} biomes, not the {wanted} of its "
                        + "version's 0..255 world");
                int[] numbers = biomes.Values;
                if (columns)
                {
                    // One a column, as the game's ChunkBiomeFix makes cells
                    // of them: each 4x4 takes the column at its center,
                    // (2, 2) in, at every height.
                    numbers = new int[LegacyBiomesPerChunk];
                    for (int i = 0; i < numbers.Length; i++)
                    {
                        int z = (i >> 2) & 3, x = i & 3;
                        numbers[i] = biomes.Values[(z * 4 + 2) * 16 + x * 4 + 2];
                    }
                }
                // The game's own upgrade reads each entry's low byte
                // (ChunkHeightAndBiomeFix.getOldBiome).
                names = new string[numbers.Length];
                for (int i = 0; i < names.Length; i++)
                    names[i] = LegacyBiomes.ByNumber[numbers[i] & 255];
            }
            else if (_data.Get("Biomes") != null)
            {
                throw new NbtException(
                    $"{LegacyWhere} at ({WorldX}, {WorldZ}) stores its biomes as "
                    + "something other than an int array");
            }
            bool runOn = DataVersion < LegacyAlignedDataVersion;

            if (_data.Get("Sections") is TagList raw)
            {
                foreach (var entry in raw)
                {
                    var tag = (TagCompound)entry;
                    int y = Tag.AsInt(tag.Get("Y")
                        ?? throw new NbtException("section has no Y"));
                    if (tag.Get("Palette") != null
                        && (y < LegacyMinSection || y > LegacyMaxSection))
                        throw new NbtException(
                            $"{LegacyWhere} at ({WorldX}, {WorldZ}) stores blocks in "
                            + $"section Y={y}, outside the 0..255 world it is read "
                            + "at: a world whose height a datapack changed is not "
                            + "read rather than read at the wrong one");
                    Sections[y] = LegacySection(tag, y, names, refusal, runOn);
                }
            }
            // They left out a section of the world that was all air; this
            // reads it as the air it is, so every section from 0 to 15 is
            // there.
            for (int y = LegacyMinSection; y <= LegacyMaxSection; y++)
            {
                if (!Sections.ContainsKey(y))
                    Sections[y] = LegacySection(
                        new TagCompound { ["Y"] = new TagByte((sbyte)y) }, y, names,
                        refusal, runOn);
            }
        }

        /// <summary>The entities a chunk before 20w45a keeps in
        /// Level.Entities, as stored -- so a tag that is no list is
        /// reported, never read as none -- or null for one whose entities
        /// live in entities/ beside the region: every current chunk, and
        /// 1.17's.</summary>
        public Tag? LegacyEntities =>
            !Legacy || DataVersion >= LegacyEntityStorageDataVersion
                ? null
                : _data.Get("Entities") ?? new TagList(TagId.Compound);

        private static Section LegacySection(TagCompound tag, int y, string[]? names,
                                             string refusal, bool runOn)
        {
            string[]? biomes = null;
            if (names != null && y >= LegacyMinSection && y <= LegacyMaxSection)
            {
                biomes = new string[Packing.BiomesPerSection];
                Array.Copy(names, (y - LegacyMinSection) * Packing.BiomesPerSection,
                           biomes, 0, Packing.BiomesPerSection);
            }
            return new Section(tag, legacy: refusal, legacyBiomes: biomes, runOn: runOn);
        }

        private string Refusal(string doing) =>
            $"chunk ({WorldX >> 4}, {WorldZ >> 4}) was saved by Minecraft "
            + $"{LegacyRelease(DataVersion)} "
            + $"(DataVersion {DataVersion}), which this library reads but will "
            + $"not {doing}: open the world in a current Minecraft to upgrade "
            + "the chunk first";

        /// <summary>Throw ReadOnlyChunkException if this is an old chunk,
        /// saying it will not <paramref name="doing"/> it; nothing
        /// otherwise. What every writer above this one asks before its first
        /// write, so a call that reaches an old chunk is refused before it
        /// changes anything.</summary>
        public void RefuseLegacy(string doing = "write")
        {
            if (Legacy)
                throw new ReadOnlyChunkException(Refusal(doing));
        }

        internal TagCompound RawData => _data;

        // -- geometry ------------------------------------------------------

        /// <summary>Generation stage, e.g. "minecraft:full"; anything else
        /// has no heightmaps or surface yet. As stored: an old chunk records
        /// its stages without the namespace ("full").</summary>
        public string? Status =>
            _data.Get("Status") is Tag status ? Tag.AsString(status) : null;

        /// <summary>Whether the game finished generating this chunk; see
        /// <see cref="ChunkFinished"/>, and for a chunk before 1.14 the
        /// names its version gave finished (<see cref="LegacyFinished"/>).</summary>
        public bool Finished => Legacy ? LegacyFinished(Status, DataVersion) : ChunkFinished(Status);

        public int WorldX => Tag.AsInt(_data.Get("xPos")!) * 16;
        public int WorldZ => Tag.AsInt(_data.Get("zPos")!) * 16;

        /// <summary>The world's floor in this chunk: yPos * 16 (-64 today).
        /// Every heightmap entry counts blocks above this, so it must be the
        /// floor the game counts from. The chunk's recorded yPos decides it;
        /// without one, the lowest section carrying blocks; without those,
        /// 0. A section stored below the floor holding only light — the
        /// game can keep one there, at Y=-5 — is not part of the world, and
        /// taking it as the floor reads every heightmap 16 low and rebuilds
        /// them 16 high.</summary>
        public int MinY { get; }

        /// <summary>The topmost y this chunk can hold a block at (319
        /// today): the top of the highest section carrying blocks. A
        /// section stored above it holding only light (Y=20) is above the
        /// build limit, so it is not counted here, and a range check
        /// against MaxY refuses the cells it would. MinY - 1 — an empty
        /// range — for a chunk storing no blocks.
        ///
        /// MinY..MaxY is the whole chunk as far as blocks and biomes go:
        /// Get, Set and the biome accessors throw
        /// ArgumentOutOfRangeException outside it, exactly as where nothing
        /// is stored, even where a section of light is — otherwise a cell
        /// past the world would read as air in the chunks that keep one
        /// and as nothing in the chunks that do not. Only the light reads
        /// reach such a section.</summary>
        public int MaxY { get; }

        private (Section Section, int Index) Locate(int x, int y, int z,
                                                    bool light = false)
        {
            if (x < 0 || x > 15 || z < 0 || z > 15)
                throw new ArgumentOutOfRangeException(
                    nameof(x),
                    $"x and z are chunk-local and must be 0-15, got ({x}, {z}); "
                    + $"this chunk spans world x {WorldX}-{WorldX + 15}, "
                    + $"z {WorldZ}-{WorldZ + 15}");
            // A section past either end of the world holds light and
            // nothing else, so only a light read may land in one.
            if (!Sections.TryGetValue(y >> 4, out var section)
                || !(light || (MinY <= y && y <= MaxY)))
                throw new ArgumentOutOfRangeException(
                    nameof(y),
                    $"y={y} is outside this chunk, which spans {MinY}..{MaxY}");
            // blocks are ordered y, then z, then x
            return (section, (y & 15) * 256 + z * 16 + x);
        }

        // -- blocks --------------------------------------------------------

        public BlockState Get(int x, int y, int z)
        {
            var (section, index) = Locate(x, y, z);
            var container = section.Blocks;
            return container == null ? new BlockState("minecraft:air")
                                     : container.Get(index);
        }

        public void Set(int x, int y, int z, BlockState state)
        {
            RefuseLegacy();
            var (section, index) = Locate(x, y, z);
            var container = section.Blocks
                ?? throw new NbtException(
                    $"section Y={y >> 4} has no block_states to write to");
            container.Set(index, state);
        }

        public void Set(int x, int y, int z, string state) =>
            Set(x, y, z, BlockState.Parse(state));

        // -- biomes --------------------------------------------------------

        private static int BiomeIndex(int x, int y, int z) =>
            ((y & 15) >> 2) * 16 + (z >> 2) * 4 + (x >> 2);

        /// <summary>Biome at a position; biomes live per 4x4x4 cell.</summary>
        public string GetBiome(int x, int y, int z)
        {
            var (section, _) = Locate(x, y, z);
            var container = section.Biomes
                ?? throw new NbtException($"section Y={y >> 4} has no biome data");
            return container.Get(BiomeIndex(x, y, z));
        }

        public void SetBiome(int x, int y, int z, string biome)
        {
            RefuseLegacy();
            var (section, _) = Locate(x, y, z);
            var container = section.Biomes
                ?? throw new NbtException($"section Y={y >> 4} has no biome data");
            container.Set(BiomeIndex(x, y, z), biome);
        }

        // -- light ---------------------------------------------------------

        /// <summary>Stored block light at a position, or null where no
        /// array is stored. Bulk readers use the Section methods.</summary>
        public int? BlockLight(int x, int y, int z)
        {
            var (section, index) = Locate(x, y, z, light: true);
            return section.BlockLight()?[index];
        }

        public int? SkyLight(int x, int y, int z)
        {
            var (section, index) = Locate(x, y, z, light: true);
            return section.SkyLight()?[index];
        }

        // -- heightmaps ----------------------------------------------------

        /// <summary>World y of the topmost surface block in a column, or
        /// null — a lookup of the stored map, never a scan. Each map is
        /// unpacked once per accessor and cached.</summary>
        public int? SurfaceY(int x, int z, string heightmap = "MOTION_BLOCKING")
        {
            if (x < 0 || x > 15 || z < 0 || z > 15)
                throw new ArgumentOutOfRangeException(
                    nameof(x), $"x and z are chunk-local and must be 0-15, got ({x}, {z})");
            if (!_heightmaps.TryGetValue(heightmap, out var values))
            {
                values = UnpackHeightmap(heightmap);
                _heightmaps[heightmap] = values;
            }
            if (values == null)
                return null;
            int height = values[z * 16 + x];
            if (height == 0)
                return null;  // nothing in this column registers on the map
            return MinY + height - 1;
        }

        /// <summary>Forget the unpacked heightmaps, so the next read
        /// decodes fresh — for code that rewrites the stored tags.</summary>
        public void DropHeightmapCache() => _heightmaps.Clear();

        /// <summary>All four maps as the game would store them, read off the
        /// blocks: 256 values in storage order, each the number of cells from
        /// MinY to the top of the column's topmost block that map counts, 0
        /// for a column with none. Refuses an old chunk: the tables behind it
        /// are the current game's, and it stores names and rules of its
        /// own.</summary>
        public Dictionary<string, int[]> DeriveHeightmaps()
        {
            RefuseLegacy("derive heightmaps for");
            var heights = new Dictionary<string, int[]>();
            foreach (string name in HeightmapNames)
                heights[name] = new int[Packing.ColumnsPerChunk];
            var found = new int[Packing.ColumnsPerChunk];
            int everyMap = (1 << HeightmapNames.Length) - 1;
            foreach (int sectionY in Sections.Keys.OrderByDescending(key => key).ToList())
            {
                var container = Sections[sectionY].Blocks;
                if (container == null)
                    continue;
                var masks = new int[container.PaletteCount];
                bool counted = false;
                for (int i = 0; i < masks.Length; i++)
                {
                    masks[i] = HeightmapMask(container.PaletteEntry(i));
                    counted |= masks[i] != 0;
                }
                if (!counted)
                    continue;
                int[] indices = container.Indices();
                int bottom = sectionY * 16 - MinY + 1;
                for (int column = 0; column < Packing.ColumnsPerChunk; column++)
                {
                    for (int ly = 15; ly >= 0 && found[column] != everyMap; ly--)
                    {
                        int fresh = masks[indices[ly * 256 + column]] & ~found[column];
                        if (fresh == 0)
                            continue;
                        found[column] |= fresh;
                        for (int bit = 0; bit < HeightmapNames.Length; bit++)
                        {
                            if ((fresh >> bit & 1) != 0)
                                heights[HeightmapNames[bit]][column] = bottom + ly;
                        }
                    }
                }
                if (found.All(mask => mask == everyMap))
                    break;
            }
            return heights;
        }

        /// <summary>Rewrite the chunk's four stored maps from its blocks, in
        /// the order the game writes them, so a map that was right comes back
        /// the same bytes. Refuses an old chunk.</summary>
        public void RebuildHeightmaps()
        {
            RefuseLegacy("rebuild heightmaps in");
            int bits = Packing.HeightmapBits(MinY, MaxY);
            var derived = DeriveHeightmaps();
            var maps = new TagCompound();
            foreach (string name in StoredHeightmaps)
                maps[name] = new TagLongArray(Packing.PackIndices(
                    derived[name], Packing.ColumnsPerChunk, bits));
            _data["Heightmaps"] = maps;
            DropHeightmapCache();
        }

        /// <summary>How many columns of each stored map disagree with the
        /// blocks, naming only the maps that are wrong, so an empty result is
        /// a chunk whose maps are right. A map the chunk does not store is
        /// not stale: the game derives a missing map when it loads the chunk.
        /// Refuses an old chunk.</summary>
        public Dictionary<string, int> StaleHeightmaps()
        {
            var derived = DeriveHeightmaps();
            var stale = new Dictionary<string, int>();
            foreach (string name in HeightmapNames)
            {
                int[]? stored = UnpackHeightmap(name);
                if (stored == null)
                    continue;
                int wrong = 0;
                for (int column = 0; column < Packing.ColumnsPerChunk; column++)
                {
                    if (stored[column] != derived[name][column])
                        wrong++;
                }
                if (wrong > 0)
                    stale[name] = wrong;
            }
            return stale;
        }

        private int[]? UnpackHeightmap(string heightmap)
        {
            if (_data.Get("Heightmaps") is not TagCompound maps
                || maps.Get(heightmap) is not TagLongArray raw)
                return null;
            int bits = Packing.HeightmapBits(MinY, MaxY);
            if (Legacy && DataVersion < LegacyAlignedDataVersion)
            {
                // before 1.16 a heightmap's entries run on across longs too
                int runOn = (Packing.ColumnsPerChunk * bits + 63) / 64;
                if (raw.Values.Length != runOn)
                    throw new NbtException(
                        $"chunk at ({WorldX}, {WorldZ}) has a {raw.Values.Length}-long "
                        + $"{heightmap} heightmap, expected {runOn} run on at {bits} "
                        + "bits per entry");
                return Packing.UnpackRunOn(raw.Values, Packing.ColumnsPerChunk, bits);
            }
            int perLong = 64 / bits;
            int expected = (Packing.ColumnsPerChunk + perLong - 1) / perLong;
            if (raw.Values.Length != expected)
                throw new NbtException(
                    $"chunk at ({WorldX}, {WorldZ}) has a {raw.Values.Length}-long "
                    + $"{heightmap} heightmap, expected {expected} for a "
                    + $"{MinY}..{MaxY} world at {bits} bits per entry");
            return Packing.UnpackIndices(raw.Values, Packing.ColumnsPerChunk, bits);
        }

        // -- lighting ------------------------------------------------------

        /// <summary>Make Minecraft recompute this chunk's light on next
        /// load: clear isLightOn, drop the stale arrays. Returns arrays
        /// removed.</summary>
        public int ResetLighting()
        {
            RefuseLegacy();
            _data["isLightOn"] = new TagByte(0);
            int removed = 0;
            if (_data.Get("sections") is TagList sections)
            {
                foreach (var tag in sections)
                {
                    var section = (TagCompound)tag;
                    if (section.Remove("BlockLight")) removed++;
                    if (section.Remove("SkyLight")) removed++;
                }
            }
            return removed;
        }

        // -- bulk ----------------------------------------------------------

        /// <summary>Count every block state in the chunk; unpacks everything.</summary>
        public Dictionary<BlockState, int> Histogram()
        {
            var counts = new Dictionary<BlockState, int>();
            foreach (var section in Sections.Values)
            {
                var container = section.Blocks;
                if (container == null)
                    continue;
                foreach (var pair in container.Histogram())
                    counts[pair.Key] = counts.TryGetValue(pair.Key, out int n)
                        ? n + pair.Value : pair.Value;
            }
            return counts;
        }

        /// <summary>Drop unreferenced palette entries across every section,
        /// blocks and biomes both. Returns entries removed.</summary>
        public int Prune()
        {
            RefuseLegacy();
            int removed = 0;
            foreach (var section in Sections.Values)
            {
                removed += section.Blocks?.Prune() ?? 0;
                removed += section.Biomes?.Prune() ?? 0;
            }
            return removed;
        }

        public IEnumerator<Section> GetEnumerator() =>
            Sections.OrderBy(pair => pair.Key).Select(pair => pair.Value).GetEnumerator();

        IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
    }
}
