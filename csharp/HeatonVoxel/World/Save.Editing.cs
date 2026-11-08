// Twin of heaton_voxel/world/editing.py: reading and editing the world —
// blocks, biomes, fills, entities.

using System;
using System.Collections.Generic;
using System.Linq;
using HeatonVoxel.Blocks;
using HeatonVoxel.Nbt;

namespace HeatonVoxel.World
{
    public sealed partial class MinecraftSave
    {
        /// <summary>How <c>FindBlocks</c> may compare a state to what you
        /// asked for. Python's module-level MATCH_MODES; C# has no module
        /// scope, so it hangs off the class the method does.</summary>
        public static readonly IReadOnlyList<string> MatchModes =
            Array.AsReadOnly(new[] { "name", "state", "suffix", "substring" });

        // ------------------------------------------------------------------
        // What freezing writes, and nothing else
        // ------------------------------------------------------------------
        //
        // Freezing writes exactly three things, so Unfreeze rewrites exactly
        // three: a snow[layers=1] covering, ice where a water *source* stood
        // (flowing water is refused), and snowy=true on the grass_block,
        // podzol or mycelium underneath -- which no writer sets directly. The
        // layer goes down with ServerLevel.setBlockAndUpdate, and the block
        // update runs SnowyBlock.updateShape, which derives snowy from
        // whatever is above.
        //
        // **Two writers, not one, and a save cannot tell them apart.** 26.2's
        // ServerLevel.tickPrecipitation writes those three per cold chunk
        // tick; worldgen's SnowAndFreezeFeature (Feature.FREEZE_TOP_LAYER,
        // shipped as the minecraft:freeze_top_layer placed feature and listed
        // by snowy_taiga and friends) writes the same
        // Blocks.SNOW.defaultBlockState(), the same
        // Blocks.ICE.defaultBlockState() and the same SNOWY, through the same
        // Biome.shouldFreeze / Biome.shouldSnow gates. Nothing in the bytes
        // distinguishes the snow a storm laid from the snow the chunk was
        // born with, so over a naturally cold biome this is a de-icing and
        // not an undo.
        //
        // Everything else that looks like winter -- snow_block, powder_snow,
        // packed_ice, blue_ice, frosted_ice, powder_snow_cauldron -- is left
        // standing. Thawing those would eat snowy hillsides and real builds:
        // one live world here holds 1,289,399 snow_blocks and 2,381,441
        // packed_ice. Most of them freezing never writes at all, but
        // powder_snow_cauldron is an exclusion by *choice*, not by innocence:
        // tickPrecipitation calls Block.handlePrecipitation, and
        // CauldronBlock.handlePrecipitation fills a cauldron with powder snow
        // on Precipitation.SNOW. A cauldron's contents are a container's
        // level, not ground covering, and emptying one is not in this
        // vocabulary.

        private const string SnowLayer = "minecraft:snow";
        private const string WeatherIce = "minecraft:ice";

        /// <summary>The tops that carry a snowy flag; nothing else in the
        /// game has one.</summary>
        private static readonly HashSet<string> SnowyTops =
            new HashSet<string>(StringComparer.Ordinal)
            {
                "minecraft:grass_block", "minecraft:podzol", "minecraft:mycelium",
            };

        /// <summary>BlockTags.SNOW -- what SnowyBlock.isSnowySetting accepts
        /// above a top. data/minecraft/tags/block/snow.json, and it is three
        /// blocks, not one.</summary>
        private static readonly HashSet<string> SnowTag =
            new HashSet<string>(StringComparer.Ordinal)
            {
                "minecraft:snow", "minecraft:snow_block", "minecraft:powder_snow",
            };

        private static readonly BlockState ThawedAir = new BlockState("minecraft:air");

        /// <summary>Ice was a source block before it froze, so it is one
        /// again afterwards.</summary>
        private static readonly BlockState ThawedWater =
            new BlockState("minecraft:water", new PropertyMap { ["level"] = "0" });

        /// <summary>(what it was before it froze, which kind), or null.
        ///
        /// Pure, and matched against the palette rather than the blocks, so a
        /// section holding no winter at all is dismissed without unpacking a
        /// single index.</summary>
        private static (BlockState State, string Kind)? Thawed(
            BlockState state, bool deepSnow)
        {
            string name = state.Name;
            if (string.Equals(name, SnowLayer, StringComparison.Ordinal))
            {
                // Freezing lays one layer and stops. tickPrecipitation
                // deepens snow only up to the maxSnowAccumulationHeight game
                // rule, and 26.2 registers it
                // registerInteger("max_snow_accumulation_height", UPDATES, 1,
                // 0, 8) -- default **1** -- while worldgen's
                // SnowAndFreezeFeature places Blocks.SNOW.defaultBlockState(),
                // also one layer. So on a default world every deeper stack was
                // piled by a player, and taking it would gut the builds that
                // are the whole reason this operation is narrow.
                // deepSnow: true is for worlds that raised the rule.
                if (deepSnow
                    || string.Equals(state.GetProperty("layers") ?? "1", "1",
                                     StringComparison.Ordinal))
                {
                    return (ThawedAir, "snow");
                }
                return (state, "deep_snow");
            }
            if (string.Equals(name, WeatherIce, StringComparison.Ordinal))
                return (ThawedWater, "ice");
            if (SnowyTops.Contains(name)
                && string.Equals(state.GetProperty("snowy"), "true",
                                 StringComparison.Ordinal))
            {
                // Every other property survives, and the unsnowed twin is
                // nearly always already in the section's palette — rebuilding
                // the state by name would add a duplicate entry beside it.
                // Whether this one is rewritten at all is not the state's own
                // business: see SnowAbove.
                return (state.WithProperties(("snowy", "false")), "snowy");
            }
            return null;
        }

        /// <summary>Will something in BlockTags.SNOW still stand over y
        /// afterwards?
        ///
        /// <c>snowy</c> is positional, not a property of the ground alone:
        /// SnowyBlock.isSnowySetting is state.is(BlockTags.SNOW) on the block
        /// <b>above</b>, and that tag is snow, snow_block and powder_snow.
        /// Clearing the flag under a snow_block this thaw deliberately leaves
        /// standing would write a state vanilla never produces and cannot
        /// repair: updateShape only fires on a neighbor change, so the ground
        /// would render dirt-sided forever.
        ///
        /// Reads the world, box or no box -- a thaw of a ground row alone
        /// leaves the layer one block above it untouched, and that layer still
        /// keeps the flag. <paramref name="melting"/> is what this same call
        /// turns to air, so the answer is the world as the thaw will leave it
        /// rather than as it stands now.</summary>
        private static bool SnowAbove(
            ChunkBlocks accessor, int lx, int y, int lz,
            HashSet<(int X, int Y, int Z)> melting)
        {
            if (melting.Contains((lx, y + 1, lz)))
                return false;
            try
            {
                return SnowTag.Contains(accessor.Get(lx, y + 1, lz).Name);
            }
            catch (ArgumentOutOfRangeException)
            {
                // Above the top of the world: air, and air is not snow.
                return false;
            }
        }

        /// <summary>Is the block under y air? Reads the world, box or no box.
        ///
        /// Deliberately outside the box being thawed: ice is a <i>lid</i>,
        /// and what hangs under it is the whole question.</summary>
        private static bool AirBelow(ChunkBlocks accessor, int lx, int y, int lz)
        {
            try
            {
                return ChunkBlocks.AirBlocks.Contains(accessor.Get(lx, y - 1, lz).Name);
            }
            catch (ArgumentOutOfRangeException)
            {
                // Below the world's floor, where the world simply stops,
                // whatever light the chunk keeps there. That is not air for
                // water to fall through.
                return false;
            }
        }

        /// <summary>Build the predicate <c>FindBlocks</c> tests each palette
        /// entry with. Separated out because it is pure: given the search
        /// terms it returns a function of one BlockState, and the scan itself
        /// never has to think about namespaces or property order again.
        ///
        /// Refuses rather than guesses, in this order: an empty term list, a
        /// match mode that is not one of MatchModes, a term that will not
        /// parse as a block state, and — under match="name" — a term carrying
        /// properties, which the comparison would ignore, quietly answering a
        /// question nobody asked.</summary>
        private static Func<BlockState, bool> BlockMatcher(
            IEnumerable<string> names, string match)
        {
            string[] wanted = names.ToArray();
            if (wanted.Length == 0)
                throw new NbtException(
                    "FindBlocks needs at least one name to look for");
            if (!MatchModes.Contains(match, StringComparer.Ordinal))
                throw new NbtException(
                    $"unknown match mode '{match}': use one of "
                    + string.Join(", ", MatchModes));

            bool byName = string.Equals(match, "name", StringComparison.Ordinal);
            if (byName || string.Equals(match, "state", StringComparison.Ordinal))
            {
                var lookup = new HashSet<string>(StringComparer.Ordinal);
                foreach (string text in wanted)
                {
                    BlockState state;
                    try
                    {
                        state = BlockState.Parse(text);
                    }
                    catch (NbtException exc)
                    {
                        throw new NbtException(
                            $"cannot read search term '{text}': {exc.Message}", exc);
                    }
                    // An unqualified name means the vanilla namespace, the way
                    // the game's own commands read it.
                    string full = state.Name.IndexOf(':') >= 0
                        ? state.Name
                        : "minecraft:" + state.Name;
                    if (byName)
                    {
                        if (state.PropertyCount > 0)
                            throw new NbtException(
                                $"match='name' compares names alone, but '{text}' "
                                + "carries properties -- use match='state' for those");
                        lookup.Add(full);
                    }
                    else
                    {
                        // Round-trip through BlockState so the properties are
                        // in the canonical order ToString emits, whatever
                        // order they were typed.
                        lookup.Add(new BlockState(full, state.Properties).ToString());
                    }
                }
                if (byName)
                    return state => lookup.Contains(state.Name);
                return state => lookup.Contains(state.ToString());
            }

            if (string.Equals(match, "suffix", StringComparison.Ordinal))
            {
                // Python's str.endswith takes a tuple and means "any of
                // these"; spell that out.
                return state =>
                {
                    foreach (string text in wanted)
                    {
                        if (state.Name.EndsWith(text, StringComparison.Ordinal))
                            return true;
                    }
                    return false;
                };
            }
            return state =>
            {
                foreach (string text in wanted)
                {
                    if (state.Name.IndexOf(text, StringComparison.Ordinal) >= 0)
                        return true;
                }
                return false;
            };
        }

        /// <summary>The block at global coordinates, or null if not
        /// generated, unreadable, or outside the vertical range.</summary>
        public BlockState? GetBlock(int x, int y, int z, string? dimension = null)
        {
            var dim = Dimension(dimension);
            ChunkBlocks? accessor;
            try
            {
                accessor = dim.Blocks(x >> 4, z >> 4);
            }
            catch (NbtException)  // exists but unreadable; see Dimension.Blocks
            {
                return null;
            }
            if (accessor == null)
                return null;
            try
            {
                return accessor.Get(x & 15, y, z & 15);
            }
            catch (ArgumentOutOfRangeException)  // outside the vertical range
            {
                return null;
            }
        }

        /// <summary>Set one block. False if that chunk is not generated.</summary>
        public bool SetBlock(int x, int y, int z, BlockState block,
                             string? dimension = null)
        {
            var dim = Dimension(dimension);
            try
            {
                var accessor = dim.WritableBlocks(x >> 4, z >> 4);
                if (accessor == null)
                    return false;
                accessor.Set(x & 15, y, z & 15, block);
            }
            catch (ArgumentOutOfRangeException)
            {
                return false;
            }
            catch (NbtException)
            {
                return false;
            }
            return true;
        }

        public bool SetBlock(int x, int y, int z, string block,
                             string? dimension = null) =>
            SetBlock(x, y, z, BlockState.Parse(block), dimension);

        /// <summary>Place a block the way the game places an ITEM.
        ///
        /// Where SetBlock writes exactly the state given, this completes
        /// what placement implies: a bed, a door or a tall plant gets both
        /// halves, a fence or stair placed here (and its existing neighbors)
        /// get their connection states settled, and a block set into a water
        /// source is waterlogged when it can be -- into flowing water it is
        /// not, and a block wet by default placed where its rule finds no
        /// water is written waterlogged=false. Building code should use
        /// this; SetBlock remains the exact-write primitive. See
        /// Place.PlaceBlock for the full rules.
        ///
        /// Returns false, writing nothing at all, if any cell of the
        /// placement falls outside the generated world — a bed never ends up
        /// half-placed. Throws NbtException, again writing nothing, when a
        /// block that can take water, placed with waterlogged unsaid,
        /// displaces water at a level, or a waterlogged, the game does not
        /// have.</summary>
        public bool PlaceBlock(int x, int y, int z, BlockState block,
                               string? dimension = null) =>
            Place.PlaceBlock(this, x, y, z, block, dimension);

        public bool PlaceBlock(int x, int y, int z, string block,
                               string? dimension = null) =>
            Place.PlaceBlock(this, x, y, z, BlockState.Parse(block), dimension);

        /// <summary>World y of the topmost surface block at global (x, z);
        /// see Dimension.SurfaceY — this resolves the dimension first.</summary>
        public int? SurfaceY(int x, int z, string heightmap = "MOTION_BLOCKING",
                             string? dimension = null) =>
            Dimension(dimension).SurfaceY(x, z, heightmap);

        /// <summary>Count every block state in the box between two corners,
        /// inclusive — the census equivalent of Fill: corners in any order,
        /// ungenerated and unreadable chunks reported rather than counted as
        /// empty. A section that lies entirely inside the box and holds one
        /// uniform state is counted in constant time, so sweeping a large
        /// volume costs far less than reading it block by block.
        ///
        /// keepLoaded keeps scanned chunks decoded in memory; off by default
        /// so a large census stays flat.</summary>
        public HistogramResult Histogram(
            (int X, int Y, int Z) corner1, (int X, int Y, int Z) corner2,
            string? dimension = null, bool keepLoaded = false)
        {
            var dim = Dimension(dimension);
            int x0 = Math.Min(corner1.X, corner2.X), x1 = Math.Max(corner1.X, corner2.X);
            int y0 = Math.Min(corner1.Y, corner2.Y), y1 = Math.Max(corner1.Y, corner2.Y);
            int z0 = Math.Min(corner1.Z, corner2.Z), z1 = Math.Max(corner1.Z, corner2.Z);

            var result = new HistogramResult();
            var counts = result.Counts;
            for (int chunkX = x0 >> 4; chunkX <= (x1 >> 4); chunkX++)
            {
                for (int chunkZ = z0 >> 4; chunkZ <= (z1 >> 4); chunkZ++)
                {
                    int baseX = chunkX * Grid.Chunk, baseZ = chunkZ * Grid.Chunk;
                    int lx0 = Math.Max(x0, baseX) - baseX;
                    int lx1 = Math.Min(x1, baseX + 15) - baseX;
                    int lz0 = Math.Max(z0, baseZ) - baseZ;
                    int lz1 = Math.Min(z1, baseZ + 15) - baseZ;
                    int column = (lx1 - lx0 + 1) * (lz1 - lz0 + 1);

                    ChunkBlocks? accessor;
                    try
                    {
                        accessor = dim.Blocks(chunkX, chunkZ);
                    }
                    catch (NbtException exc)
                    {
                        result.UnreadableChunks.Add(((chunkX, chunkZ), exc.Message));
                        result.BlocksOutOfRange += column * (y1 - y0 + 1);
                        continue;
                    }
                    if (accessor == null)
                    {
                        result.MissingChunks.Add((chunkX, chunkZ));
                        result.BlocksOutOfRange += column * (y1 - y0 + 1);
                        continue;
                    }

                    for (int sectionY = y0 >> 4; sectionY <= (y1 >> 4); sectionY++)
                    {
                        accessor.Sections.TryGetValue(sectionY, out var section);
                        var container = section?.Blocks;
                        int baseY = sectionY * Grid.Section;
                        int ly0 = Math.Max(y0, baseY) - baseY;
                        int ly1 = Math.Min(y1, baseY + 15) - baseY;
                        if (container == null)
                        {
                            result.BlocksOutOfRange += column * (ly1 - ly0 + 1);
                            continue;
                        }
                        bool whole = lx0 == 0 && lx1 == 15 && ly0 == 0 && ly1 == 15
                                     && lz0 == 0 && lz1 == 15;
                        if (whole && container.PaletteCount == 1)
                        {
                            // Uniform and fully inside: one dictionary bump.
                            var only = container.PaletteEntry(0);
                            counts[only] = counts.TryGetValue(only, out int had)
                                ? had + 4096 : 4096;
                            result.BlocksCounted += 4096;
                            continue;
                        }
                        int[] indices = container.Indices();
                        var tally = new Dictionary<int, int>();
                        if (whole)
                        {
                            foreach (int index in indices)
                                tally[index] = tally.TryGetValue(index, out int n)
                                    ? n + 1 : 1;
                        }
                        else
                        {
                            for (int ly = ly0; ly <= ly1; ly++)
                            {
                                for (int lz = lz0; lz <= lz1; lz++)
                                {
                                    int row = ly * 256 + lz * 16;
                                    for (int lx = lx0; lx <= lx1; lx++)
                                    {
                                        int index = indices[row + lx];
                                        tally[index] = tally.TryGetValue(index, out int n)
                                            ? n + 1 : 1;
                                    }
                                }
                            }
                        }
                        foreach (var pair in tally)
                        {
                            var state = container.PaletteEntry(pair.Key);
                            counts[state] = counts.TryGetValue(state, out int had)
                                ? had + pair.Value : pair.Value;
                            result.BlocksCounted += pair.Value;
                        }
                    }
                    result.ChunksScanned++;
                    if (!keepLoaded)
                        dim.Release(chunkX, chunkZ);
                }
            }
            return result;
        }

        /// <summary>Every block in the box whose state matches names, where
        /// match says how to compare:
        ///
        /// <list type="bullet">
        /// <item>"name" (the default) — the block name exactly, ignoring
        /// properties, so "oak_door" finds both halves of every oak door
        /// however it is hinged. An unqualified name is read as
        /// "minecraft:", the way the game's commands read it.</item>
        /// <item>"state" — the whole state including properties, written
        /// "oak_door[facing=north,half=lower]"; property order does not
        /// matter.</item>
        /// <item>"suffix" — names ending in any of the terms, which is how a
        /// whole family is asked for: "_door", "_stairs", "_fence".</item>
        /// <item>"substring" — names containing the term, for the families
        /// that share no common ending ("glass", "glow").</item>
        /// </list>
        ///
        /// The scan reads each section's palette first, and a section whose
        /// palette holds nothing wanted is dismissed without unpacking its
        /// bit-packed indices at all — so sweeping a region for something
        /// rare costs a fraction of reading it. Sections the world stores
        /// nothing for are air, and are dismissed the same way: a search for
        /// air will not report the implicit air of a section that was never
        /// written.
        ///
        /// limit stops the search early and sets Truncated on the result, so
        /// a capped answer can never be mistaken for an exhaustive one.
        /// Ungenerated and unreadable chunks are reported apart, as
        /// everywhere else here. keepLoaded keeps scanned chunks decoded in
        /// memory; off by default so a large sweep stays flat.</summary>
        public FindResult FindBlocks(
            (int X, int Y, int Z) corner1, (int X, int Y, int Z) corner2,
            IEnumerable<string> names, string match = "name",
            string? dimension = null, int? limit = null, bool keepLoaded = false)
        {
            var dim = Dimension(dimension);
            var wanted = BlockMatcher(names, match);
            int x0 = Math.Min(corner1.X, corner2.X), x1 = Math.Max(corner1.X, corner2.X);
            int y0 = Math.Min(corner1.Y, corner2.Y), y1 = Math.Max(corner1.Y, corner2.Y);
            int z0 = Math.Min(corner1.Z, corner2.Z), z1 = Math.Max(corner1.Z, corner2.Z);

            var result = new FindResult();
            var matches = result.Matches;
            var counts = result.Counts;
            for (int chunkX = x0 >> 4; chunkX <= (x1 >> 4); chunkX++)
            {
                for (int chunkZ = z0 >> 4; chunkZ <= (z1 >> 4); chunkZ++)
                {
                    int baseX = chunkX * Grid.Chunk, baseZ = chunkZ * Grid.Chunk;
                    int lx0 = Math.Max(x0, baseX) - baseX;
                    int lx1 = Math.Min(x1, baseX + 15) - baseX;
                    int lz0 = Math.Max(z0, baseZ) - baseZ;
                    int lz1 = Math.Min(z1, baseZ + 15) - baseZ;

                    ChunkBlocks? accessor;
                    try
                    {
                        accessor = dim.Blocks(chunkX, chunkZ);
                    }
                    catch (NbtException exc)
                    {
                        result.UnreadableChunks.Add(((chunkX, chunkZ), exc.Message));
                        continue;
                    }
                    if (accessor == null)
                    {
                        result.MissingChunks.Add((chunkX, chunkZ));
                        continue;
                    }

                    for (int sectionY = y0 >> 4; sectionY <= (y1 >> 4); sectionY++)
                    {
                        accessor.Sections.TryGetValue(sectionY, out var section);
                        var container = section?.Blocks;
                        if (container == null)
                        {
                            result.SectionsSkipped++;
                            continue;
                        }
                        // Membership only — these flags never order anything
                        // that reaches the result, so the sweep keeps its
                        // total order. Reading the palette is the whole trick:
                        // a section with no wanted name is dismissed here,
                        // before Indices() unpacks 4096 bit fields.
                        int paletteCount = container.PaletteCount;
                        var hits = new bool[paletteCount];
                        bool anyHit = false;
                        for (int i = 0; i < paletteCount; i++)
                        {
                            if (wanted(container.PaletteEntry(i)))
                                hits[i] = anyHit = true;
                        }
                        if (!anyHit)
                        {
                            result.SectionsSkipped++;
                            continue;
                        }
                        result.SectionsScanned++;
                        int baseY = sectionY * Grid.Section;
                        int ly0 = Math.Max(y0, baseY) - baseY;
                        int ly1 = Math.Min(y1, baseY + 15) - baseY;
                        int[] indices = container.Indices();
                        for (int ly = ly0; ly <= ly1; ly++)
                        {
                            for (int lz = lz0; lz <= lz1; lz++)
                            {
                                int row = ly * 256 + lz * 16;
                                for (int lx = lx0; lx <= lx1; lx++)
                                {
                                    int index = indices[row + lx];
                                    // An index past the palette cannot be a
                                    // hit — the reference's set membership
                                    // skips it rather than reaching for a
                                    // palette entry that is not there.
                                    if (index >= paletteCount || !hits[index])
                                        continue;
                                    if (limit.HasValue && matches.Count >= limit.Value)
                                    {
                                        result.Truncated = true;
                                        result.ChunksScanned++;
                                        if (!keepLoaded)
                                            dim.Release(chunkX, chunkZ);
                                        return result;
                                    }
                                    var state = container.PaletteEntry(index);
                                    matches.Add(
                                        (baseX + lx, baseY + ly, baseZ + lz, state));
                                    counts[state] = counts.TryGetValue(state, out int had)
                                        ? had + 1 : 1;
                                }
                            }
                        }
                    }
                    result.ChunksScanned++;
                    if (!keepLoaded)
                        dim.Release(chunkX, chunkZ);
                }
            }
            return result;
        }

        /// <summary>FindBlocks for a single name — Python takes one name or
        /// an iterable in the same argument.</summary>
        public FindResult FindBlocks(
            (int X, int Y, int Z) corner1, (int X, int Y, int Z) corner2,
            string name, string match = "name", string? dimension = null,
            int? limit = null, bool keepLoaded = false) =>
            FindBlocks(corner1, corner2, new[] { name }, match, dimension, limit,
                       keepLoaded);

        /// <summary>The biome at global coordinates, or null if not
        /// available. Takes block coordinates, but biomes are stored per
        /// 4x4x4 cell, so the four blocks either side of any coordinate
        /// share an answer.</summary>
        public string? GetBiome(int x, int y, int z, string? dimension = null)
        {
            var dim = Dimension(dimension);
            ChunkBlocks? accessor;
            try
            {
                accessor = dim.Blocks(x >> 4, z >> 4);
            }
            catch (NbtException)  // exists but unreadable
            {
                return null;
            }
            if (accessor == null)
                return null;
            try
            {
                return accessor.GetBiome(x & 15, y, z & 15);
            }
            catch (ArgumentOutOfRangeException)  // outside the vertical range
            {
                return null;
            }
            catch (NbtException)  // section carries no biome container
            {
                return null;
            }
        }

        /// <summary>Set the biome cell containing a block — a whole 4x4x4
        /// cell, not one block; there is no finer granularity in the format.
        /// Use FillBiome when the area matters, so the return value
        /// describes the real extent. False if it cannot be set.</summary>
        public bool SetBiome(int x, int y, int z, string biome,
                             string? dimension = null)
        {
            var dim = Dimension(dimension);
            try
            {
                var accessor = dim.WritableBlocks(x >> 4, z >> 4);
                if (accessor == null)
                    return false;
                accessor.SetBiome(x & 15, y, z & 15, biome);
            }
            catch (ArgumentOutOfRangeException)
            {
                return false;
            }
            catch (NbtException)
            {
                return false;
            }
            return true;
        }

        /// <summary>Set the biome across the box between two inclusive
        /// corners, in any order.
        ///
        /// Because a biome cell is 4x4x4, the box is grown outward to cell
        /// boundaries before anything is written — a box from x=1 to x=6
        /// covers cells 0 and 1, which is blocks 0 through 7. The box
        /// actually written is reported as WrittenBox rather than left
        /// implicit. This is a genuine 3D box, not a column operation:
        /// biomes vary with height (that is how cave biomes work).</summary>
        public BiomeFillResult FillBiome(
            (int X, int Y, int Z) corner1, (int X, int Y, int Z) corner2, string biome,
            string? dimension = null, bool prune = true, bool keepLoaded = false)
        {
            var dim = Dimension(dimension);

            int x0 = Math.Min(corner1.X, corner2.X), x1 = Math.Max(corner1.X, corner2.X);
            int y0 = Math.Min(corner1.Y, corner2.Y), y1 = Math.Max(corner1.Y, corner2.Y);
            int z0 = Math.Min(corner1.Z, corner2.Z), z1 = Math.Max(corner1.Z, corner2.Z);

            // Work in global cell coordinates from here: one cell is 4
            // blocks, and >> 2 floors for negatives where x / 4 would not.
            int cx0 = x0 >> 2, cx1 = x1 >> 2;
            int cy0 = y0 >> 2, cy1 = y1 >> 2;
            int cz0 = z0 >> 2, cz1 = z1 >> 2;

            var result = new BiomeFillResult
            {
                WrittenBox = ((cx0 * 4, cy0 * 4, cz0 * 4),
                              (cx1 * 4 + 3, cy1 * 4 + 3, cz1 * 4 + 3)),
            };

            for (int chunkX = cx0 >> 2; chunkX <= (cx1 >> 2); chunkX++)
            {
                for (int chunkZ = cz0 >> 2; chunkZ <= (cz1 >> 2); chunkZ++)
                {
                    // This chunk's slice of the box, in global cell
                    // coordinates. A chunk is 16 blocks across, so 4 cells.
                    int baseCx = chunkX * 4, baseCz = chunkZ * 4;
                    int sliceCx0 = Math.Max(cx0, baseCx), sliceCx1 = Math.Min(cx1, baseCx + 3);
                    int sliceCz0 = Math.Max(cz0, baseCz), sliceCz1 = Math.Min(cz1, baseCz + 3);
                    int column = (sliceCx1 - sliceCx0 + 1) * (sliceCz1 - sliceCz0 + 1);

                    ChunkBlocks? accessor;
                    try
                    {
                        accessor = dim.WritableBlocks(chunkX, chunkZ);
                    }
                    catch (NbtException exc)
                    {
                        // The chunk is there, we just cannot read it. Say so
                        // rather than implying the area is empty.
                        result.UnreadableChunks.Add(((chunkX, chunkZ), exc.Message));
                        result.CellsOutOfRange += column * (cy1 - cy0 + 1);
                        continue;
                    }
                    if (accessor == null)
                    {
                        result.MissingChunks.Add((chunkX, chunkZ));
                        result.CellsOutOfRange += column * (cy1 - cy0 + 1);
                        continue;
                    }

                    int written = FillBiomeChunk(
                        accessor, biome,
                        (sliceCx0 - baseCx, sliceCx1 - baseCx), (cy0, cy1),
                        (sliceCz0 - baseCz, sliceCz1 - baseCz), column, prune, result);
                    if (written > 0)
                        result.ChunksModified++;
                    if (!keepLoaded)
                        dim.Release(chunkX, chunkZ);
                }
            }
            return result;
        }

        /// <summary>Fill one chunk's share of the biome box. Returns cells
        /// written.</summary>
        private static int FillBiomeChunk(
            ChunkBlocks accessor, string biome, (int Lo, int Hi) localCx,
            (int Lo, int Hi) worldCy, (int Lo, int Hi) localCz, int column,
            bool prune, BiomeFillResult result)
        {
            var (lcx0, lcx1) = localCx;
            var (lcz0, lcz1) = localCz;
            var (cy0, cy1) = worldCy;
            int written = 0;

            // A section is 16 blocks tall, so 4 cells: section Y = cell Y >> 2.
            for (int sectionY = cy0 >> 2; sectionY <= (cy1 >> 2); sectionY++)
            {
                accessor.Sections.TryGetValue(sectionY, out var section);
                var container = section?.Biomes;
                if (container == null)
                {
                    // Above or below what this chunk stores, or a section
                    // that carries blocks but no biome container.
                    int sectionBase = sectionY * 4;
                    int overlap = Math.Min(cy1, sectionBase + 3)
                                  - Math.Max(cy0, sectionBase) + 1;
                    result.CellsOutOfRange += column * overlap;
                    continue;
                }

                int baseCy = sectionY * 4;
                int lcy0 = Math.Max(cy0, baseCy) - baseCy;
                int lcy1 = Math.Min(cy1, baseCy + 3) - baseCy;

                if (lcx0 == 0 && lcx1 == 3 && lcy0 == 0 && lcy1 == 3
                    && lcz0 == 0 && lcz1 == 3)
                {
                    // The whole section is inside the box: collapse it
                    // instead of rewriting 64 indices.
                    container.FillAll(biome);
                    result.SectionsReplaced++;
                    written += Packing.BiomesPerSection;
                    continue;
                }

                // Cells are ordered y, then z, then x, same as blocks.
                var positions = new List<int>();
                for (int lcy = lcy0; lcy <= lcy1; lcy++)
                    for (int lcz = lcz0; lcz <= lcz1; lcz++)
                        for (int lcx = lcx0; lcx <= lcx1; lcx++)
                            positions.Add(lcy * 16 + lcz * 4 + lcx);
                written += container.SetMany(positions, biome);
                if (prune)
                    container.Prune();
            }

            result.CellsFilled += written;
            return written;
        }

        /// <summary>Fill the axis-aligned box between two inclusive corners,
        /// in any order. Chunks the world has not generated are skipped and
        /// reported rather than created, since a chunk invented here would
        /// have no terrain, heightmaps or lighting.
        ///
        /// Sections that end up entirely inside the box are replaced
        /// wholesale with a single-entry palette, so a large solid fill
        /// costs a constant amount of work per 16x16x16 section instead of
        /// per block.</summary>
        public FillResult Fill(
            (int X, int Y, int Z) corner1, (int X, int Y, int Z) corner2,
            BlockState block, string? dimension = null, bool prune = true,
            bool keepLoaded = false)
        {
            var dim = Dimension(dimension);

            int x0 = Math.Min(corner1.X, corner2.X), x1 = Math.Max(corner1.X, corner2.X);
            int y0 = Math.Min(corner1.Y, corner2.Y), y1 = Math.Max(corner1.Y, corner2.Y);
            int z0 = Math.Min(corner1.Z, corner2.Z), z1 = Math.Max(corner1.Z, corner2.Z);

            var result = new FillResult();
            for (int chunkX = x0 >> 4; chunkX <= (x1 >> 4); chunkX++)
            {
                for (int chunkZ = z0 >> 4; chunkZ <= (z1 >> 4); chunkZ++)
                {
                    // This chunk's slice of the box, in global coordinates.
                    int baseX = chunkX * Grid.Chunk, baseZ = chunkZ * Grid.Chunk;
                    int sliceX0 = Math.Max(x0, baseX), sliceX1 = Math.Min(x1, baseX + 15);
                    int sliceZ0 = Math.Max(z0, baseZ), sliceZ1 = Math.Min(z1, baseZ + 15);
                    int column = (sliceX1 - sliceX0 + 1) * (sliceZ1 - sliceZ0 + 1);

                    ChunkBlocks? accessor;
                    try
                    {
                        accessor = dim.WritableBlocks(chunkX, chunkZ);
                    }
                    catch (NbtException exc)
                    {
                        // The chunk is there, we just cannot read it.
                        result.UnreadableChunks.Add(((chunkX, chunkZ), exc.Message));
                        result.BlocksOutOfRange += column * (y1 - y0 + 1);
                        continue;
                    }
                    if (accessor == null)
                    {
                        result.MissingChunks.Add((chunkX, chunkZ));
                        result.BlocksOutOfRange += column * (y1 - y0 + 1);
                        continue;
                    }

                    int written = FillChunk(
                        accessor, block, (sliceX0 - baseX, sliceX1 - baseX), (y0, y1),
                        (sliceZ0 - baseZ, sliceZ1 - baseZ), column, prune, result);
                    if (written > 0)
                        result.ChunksModified++;
                    if (!keepLoaded)
                        dim.Release(chunkX, chunkZ);
                }
            }
            return result;
        }

        public FillResult Fill(
            (int X, int Y, int Z) corner1, (int X, int Y, int Z) corner2, string block,
            string? dimension = null, bool prune = true, bool keepLoaded = false) =>
            Fill(corner1, corner2, BlockState.Parse(block), dimension, prune, keepLoaded);

        /// <summary>Fill one chunk's share of the box. Returns blocks written.</summary>
        private static int FillChunk(
            ChunkBlocks accessor, BlockState block, (int Lo, int Hi) localX,
            (int Lo, int Hi) worldY, (int Lo, int Hi) localZ, int column,
            bool prune, FillResult result)
        {
            var (lx0, lx1) = localX;
            var (lz0, lz1) = localZ;
            var (y0, y1) = worldY;
            int written = 0;

            for (int sectionY = y0 >> 4; sectionY <= (y1 >> 4); sectionY++)
            {
                accessor.Sections.TryGetValue(sectionY, out var section);
                var container = section?.Blocks;
                if (container == null)
                {
                    // Above or below what this chunk actually stores.
                    int sectionBase = sectionY * Grid.Section;
                    int overlap = Math.Min(y1, sectionBase + 15)
                                  - Math.Max(y0, sectionBase) + 1;
                    result.BlocksOutOfRange += column * overlap;
                    continue;
                }

                int baseY = sectionY * Grid.Section;
                int ly0 = Math.Max(y0, baseY) - baseY;
                int ly1 = Math.Min(y1, baseY + 15) - baseY;

                if (lx0 == 0 && lx1 == 15 && ly0 == 0 && ly1 == 15
                    && lz0 == 0 && lz1 == 15)
                {
                    // The whole section is inside the box: collapse it
                    // instead of rewriting 4096 indices.
                    container.FillAll(block);
                    result.SectionsReplaced++;
                    written += 4096;
                    continue;
                }

                var positions = new List<int>();
                for (int ly = ly0; ly <= ly1; ly++)
                    for (int lz = lz0; lz <= lz1; lz++)
                        for (int lx = lx0; lx <= lx1; lx++)
                            positions.Add(ly * 256 + lz * 16 + lx);
                written += container.SetMany(positions, block);
                if (prune)
                    container.Prune();
            }

            result.BlocksFilled += written;
            return written;
        }

        /// <summary>Rewrite every block in a box that the mapping names,
        /// state for state, and leave every other block as it stands. The
        /// twin of replace_blocks.
        ///
        /// A key matches a stored state as the 26.3 codec reads one, each
        /// with its block's defaults filled in (BlockState.Canonical), and a
        /// value is written the same way, so one target spelled two ways
        /// never enters a palette twice. A key without a namespace is the
        /// game's. A chunk the game has not finished is left alone and
        /// reported in UnfinishedChunks, since the game generates over it; a
        /// chunk it may only read is reported among UnreadableChunks.
        ///
        /// Where a cell's block id changes, the block entity stored for it
        /// is dropped; where only its properties change it is kept. The
        /// writing order is total -- chunks by x then z, sections from the
        /// bottom, cells in y, z, x order, targets in the order a cell first
        /// needed each -- so the bytes match the Python twin's. A section
        /// wholly inside the box whose every palette entry maps to one
        /// target collapses to it. No aftercare: ConnectBlocks,
        /// RebuildHeightmaps, InvalidatePoi and ResetLighting are the
        /// caller's.</summary>
        public ReplaceResult ReplaceBlocks(
            (int X, int Y, int Z) corner1, (int X, int Y, int Z) corner2,
            IEnumerable<KeyValuePair<BlockState, BlockState>> mapping,
            string? dimension = null, bool prune = true, bool keepLoaded = false)
        {
            var table = ReplacementTable(mapping);
            var dim = Dimension(dimension);

            int x0 = Math.Min(corner1.X, corner2.X), x1 = Math.Max(corner1.X, corner2.X);
            int y0 = Math.Min(corner1.Y, corner2.Y), y1 = Math.Max(corner1.Y, corner2.Y);
            int z0 = Math.Min(corner1.Z, corner2.Z), z1 = Math.Max(corner1.Z, corner2.Z);

            var result = new ReplaceResult();
            for (int chunkX = x0 >> 4; chunkX <= (x1 >> 4); chunkX++)
            {
                for (int chunkZ = z0 >> 4; chunkZ <= (z1 >> 4); chunkZ++)
                {
                    int baseX = chunkX * Grid.Chunk, baseZ = chunkZ * Grid.Chunk;
                    int lx0 = Math.Max(x0, baseX) - baseX, lx1 = Math.Min(x1, baseX + 15) - baseX;
                    int lz0 = Math.Max(z0, baseZ) - baseZ, lz1 = Math.Min(z1, baseZ + 15) - baseZ;
                    int column = (lx1 - lx0 + 1) * (lz1 - lz0 + 1);

                    ChunkBlocks? accessor;
                    try
                    {
                        accessor = dim.WritableBlocks(chunkX, chunkZ);
                    }
                    catch (NbtException exc)
                    {
                        result.UnreadableChunks.Add(((chunkX, chunkZ), exc.Message));
                        result.BlocksOutOfRange += column * (y1 - y0 + 1);
                        continue;
                    }
                    if (accessor == null)
                    {
                        result.MissingChunks.Add((chunkX, chunkZ));
                        result.BlocksOutOfRange += column * (y1 - y0 + 1);
                        continue;
                    }
                    if (!accessor.Finished)
                    {
                        result.UnfinishedChunks.Add((chunkX, chunkZ));
                        result.BlocksOutOfRange += column * (y1 - y0 + 1);
                        if (!keepLoaded)
                            dim.Release(chunkX, chunkZ);
                        continue;
                    }

                    // Before any write, while each block entity's cell still
                    // holds the block it belongs to.
                    int removed = DropOrphanedBlockEntities(
                        dim.Chunk(chunkX, chunkZ)?.Data, accessor, table,
                        (baseX + lx0, y0, baseZ + lz0), (baseX + lx1, y1, baseZ + lz1));
                    int written = ReplaceChunk(
                        accessor, table, (lx0, lx1), (y0, y1), (lz0, lz1),
                        column, prune, result);
                    result.BlockEntitiesRemoved += removed;
                    if (written > 0 || removed > 0)
                        result.ChunksModified++;
                    if (!keepLoaded)
                        dim.Release(chunkX, chunkZ);
                }
            }
            return result;
        }

        public ReplaceResult ReplaceBlocks(
            (int X, int Y, int Z) corner1, (int X, int Y, int Z) corner2,
            IEnumerable<KeyValuePair<string, string>> mapping,
            string? dimension = null, bool prune = true, bool keepLoaded = false) =>
            ReplaceBlocks(corner1, corner2,
                mapping.Select(pair => new KeyValuePair<BlockState, BlockState>(
                    BlockState.Parse(pair.Key), BlockState.Parse(pair.Value))),
                dimension, prune, keepLoaded);

        /// <summary>The mapping keyed by canonical state, with canonical
        /// targets. Two keys naming one state with different targets are
        /// refused.</summary>
        private static Dictionary<BlockState, BlockState> ReplacementTable(
            IEnumerable<KeyValuePair<BlockState, BlockState>> mapping)
        {
            var table = new Dictionary<BlockState, BlockState>();
            foreach (var pair in mapping)
            {
                var source = pair.Key;
                if (source.Name.IndexOf(':') < 0)
                    source = new BlockState("minecraft:" + source.Name, source.Properties);
                source = source.Canonical();
                var target = pair.Value.Canonical();
                if (table.TryGetValue(source, out var had) && !had.Equals(target))
                    throw new NbtException(
                        $"the mapping replaces {source} two ways: with {had} and with {target}");
                table[source] = target;
            }
            return table;
        }

        /// <summary>What the table writes over this stored state, or null to
        /// leave it: unnamed, or named with itself.</summary>
        private static BlockState? ReplaceTarget(
            Dictionary<BlockState, BlockState> table, BlockState state)
        {
            var canonical = state.Canonical();
            if (!table.TryGetValue(canonical, out var target) || target.Equals(canonical))
                return null;
            return target;
        }

        /// <summary>Drop each stored block entity in the box whose cell the
        /// replace gives another block id. An entry whose x, y or z is not a
        /// whole-number tag is kept, as somebody else's to judge.</summary>
        private static int DropOrphanedBlockEntities(
            TagCompound? chunkData, ChunkBlocks accessor,
            Dictionary<BlockState, BlockState> table,
            (int X, int Y, int Z) low, (int X, int Y, int Z) high)
        {
            if (!(chunkData?.Get("block_entities") is TagList stored) || stored.Count == 0)
                return 0;
            var kept = new List<Tag>();
            int removed = 0;
            foreach (var item in stored)
            {
                if (!(item is TagCompound entry)
                    || !(entry.Get("x") is Tag tx && IsInteger(tx))
                    || !(entry.Get("y") is Tag ty && IsInteger(ty))
                    || !(entry.Get("z") is Tag tz && IsInteger(tz)))
                {
                    kept.Add(item);
                    continue;
                }
                long x = Tag.AsLong(tx), y = Tag.AsLong(ty), z = Tag.AsLong(tz);
                if (x < low.X || x > high.X || y < low.Y || y > high.Y
                    || z < low.Z || z > high.Z)
                {
                    kept.Add(item);
                    continue;
                }
                accessor.Sections.TryGetValue((int)(y >> 4), out var section);
                var container = section?.Blocks;
                if (container == null)
                {
                    kept.Add(item);
                    continue;
                }
                var state = container.Get((int)(((y & 15) * 256) + ((z & 15) * 16) + (x & 15)));
                var target = ReplaceTarget(table, state);
                if (target != null && !string.Equals(target.Name, state.Name, StringComparison.Ordinal))
                    removed++;
                else
                    kept.Add(item);
            }
            if (removed > 0)
            {
                stored.Items.Clear();
                stored.Items.AddRange(kept);
            }
            return removed;
        }

        private static bool IsInteger(Tag tag) =>
            tag is TagByte || tag is TagShort || tag is TagInt || tag is TagLong;

        /// <summary>Replace one chunk's share of the box. Returns blocks
        /// written. The palette answers first, so a section none of whose
        /// entries the table names is passed over without unpacking.</summary>
        private static int ReplaceChunk(
            ChunkBlocks accessor, Dictionary<BlockState, BlockState> table,
            (int Lo, int Hi) localX, (int Lo, int Hi) worldY, (int Lo, int Hi) localZ,
            int column, bool prune, ReplaceResult result)
        {
            var (lx0, lx1) = localX;
            var (lz0, lz1) = localZ;
            var (y0, y1) = worldY;
            int written = 0;

            for (int sectionY = y0 >> 4; sectionY <= (y1 >> 4); sectionY++)
            {
                accessor.Sections.TryGetValue(sectionY, out var section);
                var container = section?.Blocks;
                int baseY = sectionY * Grid.Section;
                int ly0 = Math.Max(y0, baseY) - baseY;
                int ly1 = Math.Min(y1, baseY + 15) - baseY;
                if (container == null)
                {
                    // Above or below what this chunk stores.
                    result.BlocksOutOfRange += column * (ly1 - ly0 + 1);
                    continue;
                }

                var targets = new Dictionary<int, BlockState>();
                for (int slot = 0; slot < container.PaletteCount; slot++)
                {
                    var target = ReplaceTarget(table, container.PaletteEntry(slot));
                    if (target != null)
                        targets[slot] = target;
                }
                if (targets.Count == 0)
                    continue;

                bool whole = lx0 == 0 && lx1 == 15 && ly0 == 0 && ly1 == 15
                             && lz0 == 0 && lz1 == 15;
                if (whole && targets.Count == container.PaletteCount
                    && targets.Values.Distinct().Count() == 1)
                {
                    // Every block in the section becomes the one target:
                    // collapse it, as a fill does.
                    container.FillAll(targets[0]);
                    result.SectionsReplaced++;
                    written += 4096;
                    continue;
                }

                int[] indices = container.Indices();
                // Targets in the order a cell first needed each.
                var order = new List<BlockState>();
                var groups = new Dictionary<BlockState, List<int>>();
                for (int ly = ly0; ly <= ly1; ly++)
                {
                    for (int lz = lz0; lz <= lz1; lz++)
                    {
                        int row = ly * 256 + lz * 16;
                        for (int lx = lx0; lx <= lx1; lx++)
                        {
                            if (!targets.TryGetValue(indices[row + lx], out var target))
                                continue;
                            if (!groups.TryGetValue(target, out var positions))
                            {
                                positions = new List<int>();
                                groups[target] = positions;
                                order.Add(target);
                            }
                            positions.Add(row + lx);
                        }
                    }
                }
                foreach (var target in order)
                    written += container.SetMany(groups[target], target);
                if (order.Count > 0 && prune)
                    container.Prune();
            }

            result.BlocksReplaced += written;
            return written;
        }

        /// <summary>Rewrite the three states freezing writes, and nothing
        /// else.
        ///
        /// Freezing writes exactly three things, so this rewrites exactly
        /// those three:
        /// <list type="bullet">
        /// <item>minecraft:snow[layers=1] → minecraft:air</item>
        /// <item>minecraft:ice → minecraft:water[level=0], the source it
        /// froze from</item>
        /// <item>grass_block/podzol/mycelium with snowy=true → the same block
        /// with snowy=false, every other property kept — but only where
        /// nothing in BlockTags.SNOW (snow, snow_block, powder_snow) is left
        /// standing above it, since snowy is derived from that block and not
        /// from the ground</item>
        /// </list>
        ///
        /// <b>This is not an undo, and a save cannot make it one.</b> Two
        /// writers produce those states through the same Biome.shouldFreeze /
        /// Biome.shouldSnow gates: ServerLevel.tickPrecipitation on a cold
        /// chunk tick, and worldgen's SnowAndFreezeFeature, shipped as the
        /// minecraft:freeze_top_layer placed feature and listed by snowy_taiga
        /// and every other cold biome. The bytes are identical, so over a
        /// naturally cold biome this is a <b>de-icing</b>: what it clears the
        /// weather will lay down again, and regeneration would too.
        ///
        /// <b>Nothing else is touched, which is the point.</b> snow_block,
        /// powder_snow, packed_ice, blue_ice, frosted_ice and
        /// powder_snow_cauldron are left standing — eating them would take out
        /// snowy hillsides and real builds along with the weather. Most of
        /// them freezing never writes; the cauldron is excluded by choice, as
        /// tickPrecipitation does fill one with powder snow through
        /// Block.handlePrecipitation, and a container's level is not ground
        /// covering.
        ///
        /// deepSnow takes snow at layers=2..8 as well. Freezing does not lay
        /// it — maxSnowAccumulationHeight defaults to 1 — so on an ordinary
        /// world every deeper stack is a player's, and what is left standing
        /// comes back as DeepSnowKept rather than passing in silence.
        ///
        /// Like <c>Fill</c>, this writes states and stops. It runs <b>no
        /// aftercare</b>, and three passes are worth making afterwards:
        /// ConnectBlocks, because ice is a sturdy full cube (see
        /// Connect.SturdySuffixes) and melting it genuinely changes how the
        /// fences, walls and panes beside it connect; RebuildHeightmaps,
        /// because a snow layer is part of WORLD_SURFACE (the motion-blocking
        /// maps sit under it) and taking it off lowers that surface; and
        /// ResetLighting, because opaque snow and ice let no light through
        /// and the stored light still says they do.
        ///
        /// The result's FloatingWater counts melted ice standing over air — a
        /// frozen lid on a drained lake — which is reported, never
        /// refused.</summary>
        public UnfreezeResult Unfreeze(
            (int X, int Y, int Z) corner1, (int X, int Y, int Z) corner2,
            string? dimension = null, bool deepSnow = false,
            bool keepLoaded = false)
        {
            var dim = Dimension(dimension);

            int x0 = Math.Min(corner1.X, corner2.X), x1 = Math.Max(corner1.X, corner2.X);
            int y0 = Math.Min(corner1.Y, corner2.Y), y1 = Math.Max(corner1.Y, corner2.Y);
            int z0 = Math.Min(corner1.Z, corner2.Z), z1 = Math.Max(corner1.Z, corner2.Z);

            var result = new UnfreezeResult();
            for (int chunkX = x0 >> 4; chunkX <= (x1 >> 4); chunkX++)
            {
                for (int chunkZ = z0 >> 4; chunkZ <= (z1 >> 4); chunkZ++)
                {
                    int baseX = chunkX * Grid.Chunk, baseZ = chunkZ * Grid.Chunk;
                    int lx0 = Math.Max(x0, baseX) - baseX;
                    int lx1 = Math.Min(x1, baseX + 15) - baseX;
                    int lz0 = Math.Max(z0, baseZ) - baseZ;
                    int lz1 = Math.Min(z1, baseZ + 15) - baseZ;

                    ChunkBlocks? accessor;
                    try
                    {
                        accessor = dim.WritableBlocks(chunkX, chunkZ);
                    }
                    catch (NbtException exc)
                    {
                        // There, just not readable. Say so rather than
                        // implying the ground under it thawed.
                        result.UnreadableChunks.Add(((chunkX, chunkZ), exc.Message));
                        continue;
                    }
                    if (accessor == null)
                    {
                        result.MissingChunks.Add((chunkX, chunkZ));
                        continue;
                    }

                    int written = UnfreezeChunk(
                        accessor, (lx0, lx1), (y0, y1), (lz0, lz1), deepSnow, result);
                    if (written > 0)
                        result.ChunksModified++;
                    if (!keepLoaded)
                        dim.Release(chunkX, chunkZ);
                }
            }
            return result;
        }

        /// <summary>Thaw one chunk's share of the box. Returns blocks
        /// rewritten.
        ///
        /// Every rewrite is worked out before any of them is made. The
        /// floating-water check reads the block <i>below</i> a melted lid,
        /// and that block may itself be snow this same call removes —
        /// deciding it against a half-thawed chunk would make the answer
        /// depend on which section the sweep happened to reach first.</summary>
        private static int UnfreezeChunk(
            ChunkBlocks accessor, (int Lo, int Hi) localX, (int Lo, int Hi) worldY,
            (int Lo, int Hi) localZ, bool deepSnow, UnfreezeResult result)
        {
            var (lx0, lx1) = localX;
            var (lz0, lz1) = localZ;
            var (y0, y1) = worldY;

            // Scan every section first, then decide. Two of the three
            // rewrites are answered by a neighbor rather than by the cell:
            // floating water reads the block below a melted lid, and snowy
            // reads the block above a top — and either may itself be snow
            // this same call removes. Deciding against a half-thawed chunk
            // would make both answers depend on which section the sweep
            // happened to reach first.
            var found =
                new List<(int SectionY,
                          List<(int Position, BlockState State, string Kind,
                                int Lx, int Y, int Lz)> Candidates)>();
            var melting = new HashSet<(int X, int Y, int Z)>();

            for (int sectionY = y0 >> 4; sectionY <= (y1 >> 4); sectionY++)
            {
                accessor.Sections.TryGetValue(sectionY, out var section);
                var container = section?.Blocks;
                if (container == null)
                {
                    // A section the chunk does not store is air, and air
                    // never froze.
                    continue;
                }

                var palette = container.Palette;
                var targets = new Dictionary<int, (BlockState State, string Kind)>();
                for (int slot = 0; slot < palette.Count; slot++)
                {
                    var thawed = Thawed(palette[slot], deepSnow);
                    if (thawed != null)
                        targets[slot] = thawed.Value;
                }
                if (targets.Count == 0)
                {
                    // Nothing frozen in this section's palette, so none of
                    // its 4096 indices need unpacking at all.
                    continue;
                }

                int baseY = sectionY * Grid.Section;
                int ly0 = Math.Max(y0, baseY) - baseY;
                int ly1 = Math.Min(y1, baseY + 15) - baseY;
                int[] indices = container.Indices();
                var candidates =
                    new List<(int Position, BlockState State, string Kind,
                              int Lx, int Y, int Lz)>();
                for (int ly = ly0; ly <= ly1; ly++)
                {
                    for (int lz = lz0; lz <= lz1; lz++)
                    {
                        int row = ly * 256 + lz * 16;
                        for (int lx = lx0; lx <= lx1; lx++)
                        {
                            if (!targets.TryGetValue(indices[row + lx], out var hit))
                                continue;
                            candidates.Add(
                                (row + lx, hit.State, hit.Kind, lx, baseY + ly, lz));
                            if (hit.Kind == "snow")
                                melting.Add((lx, baseY + ly, lz));
                        }
                    }
                }
                if (candidates.Count > 0)
                    found.Add((sectionY, candidates));
            }

            // Section Y -> [(state to write, positions)], both still in scan
            // order, so the same thaw lays the same palette down every time it
            // is run. Python's dicts are insertion-ordered and the palette —
            // so the chunk's bytes — follows the order the sweep writes in; a
            // Dictionary here would encode identical thaws differently.
            var planned =
                new List<(int SectionY,
                          List<(BlockState State, List<int> Positions)> Groups)>();
            foreach (var (sectionY, candidates) in found)
            {
                var groups = new List<(BlockState State, List<int> Positions)>();
                var groupOf = new Dictionary<BlockState, int>();
                foreach (var (position, state, kind, lx, y, lz) in candidates)
                {
                    if (kind == "snow")
                    {
                        result.SnowRemoved++;
                    }
                    else if (kind == "deep_snow")
                    {
                        // Deeper than freezing lays it, so it stays —
                        // counted rather than passed over in silence.
                        result.DeepSnowKept++;
                        continue;
                    }
                    else if (kind == "ice")
                    {
                        result.IceMelted++;
                        if (AirBelow(accessor, lx, y, lz))
                            result.FloatingWater++;
                    }
                    else
                    {
                        if (SnowAbove(accessor, lx, y, lz, melting))
                        {
                            // Snow this thaw leaves standing, so snowy=true is
                            // the state the game itself would derive here.
                            continue;
                        }
                        result.SnowyCleared++;
                    }
                    if (!groupOf.TryGetValue(state, out int group))
                    {
                        group = groups.Count;
                        groupOf[state] = group;
                        groups.Add((state, new List<int>()));
                    }
                    groups[group].Positions.Add(position);
                }
                if (groups.Count > 0)
                    planned.Add((sectionY, groups));
            }

            int written = 0;
            foreach (var (sectionY, groups) in planned)
            {
                var container = accessor.Sections[sectionY].Blocks!;
                foreach (var (state, positions) in groups)
                    written += container.SetMany(positions, state);
                // Melting a lake usually orphans the ice entry outright.
                container.Prune();
            }
            return written;
        }

        /// <summary>Build finished chunks of air wherever a box has none, so
        /// a world can be built on past the ground the game has generated --
        /// the twin of create_chunks. y is ignored; a chunk the world holds
        /// is left as it stands and named; a vanilla dimension never visited
        /// is made; the chunks are stamped with the world's DataVersion, or
        /// 26.3's for a newer world, and a world older than 26.2 is
        /// refused.</summary>
        public ChunkCreateResult CreateChunks(
            (int X, int Y, int Z) corner1, (int X, int Y, int Z) corner2,
            string? dimension = null, string? biome = null)
        {
            RefuseReadOnly("build chunks in");
            string name = dimension ?? DefaultDimension();
            if (!Dimensions.TryGetValue(name, out var dim))
            {
                if (!DimensionNames.Directories.TryGetValue(name, out var relative))
                {
                    string known = string.Join(", ", DimensionNames.Directories.Keys
                        .OrderBy(k => k, StringComparer.Ordinal));
                    throw new NbtException($"unknown dimension '{name}'; expected one of {known}");
                }
                dim = new Dimension(name, System.IO.Path.Combine(
                    Path, relative.Replace('/', System.IO.Path.DirectorySeparatorChar)));
                dim.Index();
                Dimensions[name] = dim;
            }
            if (biome == null)
            {
                biome = name switch
                {
                    DimensionNames.Overworld => "minecraft:plains",
                    DimensionNames.Nether => "minecraft:nether_wastes",
                    DimensionNames.End => "minecraft:the_end",
                    _ => throw new NbtException($"{name} has no biome of its own to default to; name one"),
                };
            }

            var stampTag = LevelData().Get("DataVersion")
                ?? throw new NbtException("level.dat records no DataVersion to stamp new chunks with");
            int stamp = Tag.AsInt(stampTag);
            if (stamp < ChunkBlocks.EmptyChunkMinDataVersion)
                throw new NbtException(
                    $"this world was last saved by DataVersion {stamp}, older than the "
                    + $"oldest finished chunk this library has read "
                    + $"({ChunkBlocks.EmptyChunkMinDataVersion}, 26.2); open it in a "
                    + "current Minecraft first");
            int version = Math.Min(stamp, ChunkBlocks.EmptyChunkMaxDataVersion);

            int x0 = Math.Min(corner1.X, corner2.X), x1 = Math.Max(corner1.X, corner2.X);
            int z0 = Math.Min(corner1.Z, corner2.Z), z1 = Math.Max(corner1.Z, corner2.Z);
            var result = new ChunkCreateResult { DataVersion = version, Biome = biome };
            (int Low, int High)? range = null;
            for (int chunkX = x0 >> 4; chunkX <= x1 >> 4; chunkX++)
            {
                for (int chunkZ = z0 >> 4; chunkZ <= z1 >> 4; chunkZ++)
                {
                    if (dim.Chunk(chunkX, chunkZ) != null)
                    {
                        ChunkBlocks found;
                        try
                        {
                            found = dim.Blocks(chunkX, chunkZ)!;
                        }
                        catch (NbtException exc)
                        {
                            result.UnreadableChunks.Add(((chunkX, chunkZ), exc.Message));
                            continue;
                        }
                        if (found.Finished)
                            result.ExistingChunks.Add((chunkX, chunkZ));
                        else
                            result.UnfinishedChunks.Add((chunkX, chunkZ));
                        continue;
                    }
                    if (range == null)
                    {
                        range = dim.SectionRange();
                        result.MinY = range.Value.Low * Grid.Section;
                        result.MaxY = (range.Value.High + 1) * Grid.Section - 1;
                    }
                    bool newRegion = !dim.Available.ContainsKey((chunkX >> 5, chunkZ >> 5));
                    dim.CreateChunk(chunkX, chunkZ, version, biome, range);
                    if (newRegion)
                        result.RegionsCreated++;
                    result.ChunksCreated++;
                    result.CreatedChunks.Add((chunkX, chunkZ));
                }
            }
            return result;
        }

        /// <summary>Remove whole chunks, so Minecraft generates them again on
        /// sight.
        ///
        /// The corners are global <b>block</b> coordinates in any order, and
        /// y is ignored: a chunk is a column and the whole column goes. The
        /// smallest thing that can be deleted is a chunk, so a box that
        /// starts mid-chunk clears the whole chunk — the rectangle actually
        /// cleared comes back as DeletedBox rather than being left implicit,
        /// the way FillBiome reports WrittenBox.
        ///
        /// A chunk lives in three files and all three are cleared, because
        /// nothing rebuilds the other two from the terrain: EntityStorage
        /// reads entities/ without consulting the chunk, so leftover
        /// villagers and armor stands would reappear floating in fresh
        /// terrain, and PoiSection.refresh returns early on a persisted
        /// Valid: true, so stale points of interest are never rescanned and
        /// villagers keep claims on beds that no longer exist.
        ///
        /// Deleting never decodes a chunk, so a chunk too damaged to read is
        /// deleted like any other — which is usually why one is being
        /// deleted. The change reaches disk with <c>Save</c>, and an emptied
        /// region file is left in place: a region with a hole in it is
        /// exactly what Minecraft writes when a chunk has never been visited.
        ///
        /// <b>This is not an undo.</b> The game regenerates from the world
        /// seed with the <i>current</i> version's generator, so what comes
        /// back is new terrain, not the terrain that was there
        /// before.</summary>
        public ChunkDeleteResult DeleteChunks(
            (int X, int Y, int Z) corner1, (int X, int Y, int Z) corner2,
            string? dimension = null)
        {
            var dim = Dimension(dimension);
            dim.RefuseReadOnly("delete chunks from");

            // Corners in any order; y is not part of the question.
            int x0 = Math.Min(corner1.X, corner2.X), x1 = Math.Max(corner1.X, corner2.X);
            int z0 = Math.Min(corner1.Z, corner2.Z), z1 = Math.Max(corner1.Z, corner2.Z);
            int cx0 = x0 >> 4, cx1 = x1 >> 4;
            int cz0 = z0 >> 4, cz1 = z1 >> 4;

            var result = new ChunkDeleteResult
            {
                DeletedBox = ((cx0 * Grid.Chunk, cz0 * Grid.Chunk),
                              (cx1 * Grid.Chunk + 15, cz1 * Grid.Chunk + 15)),
            };

            var touched = new HashSet<(int X, int Z)>();
            for (int chunkX = cx0; chunkX <= cx1; chunkX++)
            {
                for (int chunkZ = cz0; chunkZ <= cz1; chunkZ++)
                {
                    // First, always: Dimension.Blocks memoizes its accessors
                    // and answers from that cache without looking at
                    // region.Chunks again, so a chunk deleted underneath a
                    // live accessor would go on reporting minecraft:full and
                    // a surface height for ground that is no longer there.
                    dim.Release(chunkX, chunkZ);

                    int regionX = chunkX >> 5, regionZ = chunkZ >> 5;
                    var key = (chunkX & 31, chunkZ & 31);
                    bool removed = false;

                    var region = dim.Region(regionX, regionZ);
                    if (region != null && region.Chunks.Remove(key))
                    {
                        result.ChunksDeleted++;
                        removed = true;
                    }
                    else
                    {
                        result.ChunksAbsent++;
                    }

                    // Both of these must be *opened* to be emptied: Save
                    // writes only the regions it holds open, so deciding from
                    // EntityAvailable alone would compute the right answer
                    // and write nothing. A null return is the honest "the
                    // world has never stored any there".
                    var entities = dim.EntityRegion(regionX, regionZ);
                    if (entities != null && entities.Chunks.Remove(key))
                    {
                        result.EntityChunksDeleted++;
                        removed = true;
                    }
                    var poi = dim.PoiRegion(regionX, regionZ);
                    if (poi != null && poi.Chunks.Remove(key))
                    {
                        result.PoiChunksDeleted++;
                        removed = true;
                    }

                    if (removed)
                        touched.Add((regionX, regionZ));
                }
            }
            result.RegionsTouched = touched.Count;
            return result;
        }

        /// <summary>Add an entity to the world, with the structural NBT
        /// handled: id, exact Pos, Rotation, and a FRESH UUID (two entities
        /// sharing one make the game drop a copy, so there is no way to pass
        /// a UUID in), filed in the right entity chunk — creating that chunk
        /// and its region file in memory if the world has never stored an
        /// entity there. Pass extra for the tags a kind needs beyond the
        /// shared ones (VillagerData, a sheep's Color, ...).
        ///
        /// Coordinates are exact entity coordinates: to stand something in
        /// the middle of block (bx, by, bz), pass bx + 0.5, by, bz + 0.5.
        /// Refuses (false, nothing written) when the terrain chunk was never
        /// generated — an entity needs ground under it.</summary>
        public bool SpawnEntity(string identity, double x, double y, double z,
                                double yaw = 0.0, double pitch = 0.0,
                                IReadOnlyDictionary<string, Tag>? extra = null,
                                string? dimension = null)
        {
            var dim = Dimension(dimension);
            dim.RefuseReadOnly("add an entity to");
            int chunkX = (int)Math.Floor(x) >> 4, chunkZ = (int)Math.Floor(z) >> 4;
            if (dim.Chunk(chunkX, chunkZ) == null)
                return false;
            ChunkBlocks? terrain;
            try
            {
                terrain = dim.Blocks(chunkX, chunkZ);
            }
            catch (NbtException)
            {
                terrain = null;  // unreadable ground: entities keep their own file
            }
            // Over an old chunk, which the library reads and never writes.
            terrain?.RefuseLegacy("add an entity to");

            int stamp;
            var levelData = Level?.Root?.Get("Data") as TagCompound;
            if (levelData?.Get("DataVersion") is Tag version)
                stamp = Tag.AsInt(version);
            else
                stamp = dim.Blocks(chunkX, chunkZ)!.DataVersion;
            var chunk = dim.EntityChunk(chunkX, chunkZ, create: true, dataVersion: stamp)!;
            if (chunk.Data == null)
                throw new NbtException(
                    $"entity storage for chunk ({chunkX}, {chunkZ}) exists but "
                    + $"cannot be read: {chunk.ParseError}");
            if (chunk.Data.Get("Entities") is not TagList stored)
            {
                stored = new TagList(TagId.Compound);
                chunk.Data["Entities"] = stored;
            }
            var tag = new TagCompound
            {
                ["id"] = new TagString(identity),
                ["Pos"] = new TagList(TagId.Double)
                {
                    new TagDouble(x), new TagDouble(y), new TagDouble(z),
                },
                ["Rotation"] = new TagList(TagId.Float)
                {
                    new TagFloat((float)yaw), new TagFloat((float)pitch),
                },
                ["UUID"] = Tag.FreshUuid(),
            };
            if (extra != null)
            {
                foreach (var pair in extra)
                    tag[pair.Key] = pair.Value;
            }
            stored.Add(tag);
            return true;
        }

        /// <summary>Every entity standing inside the box.
        ///
        /// The read twin of SpawnEntity: entities live apart from terrain, in
        /// entities/r.X.Z.mca, and this walks the chunks the box covers and
        /// reports what is filed there. ids filters by entity type — one id
        /// or a sequence, unqualified names read as "minecraft:" — and null
        /// reports everything, mobs, items, armor stands and all. An empty
        /// filter is refused rather than read as "nothing", since it is far
        /// more likely to be a list that failed to fill than a request for no
        /// entities at all.
        ///
        /// Membership is by the block the entity stands in: its stored double
        /// coordinates are floored, so an entity at x = -0.3 is in block -1,
        /// not 0.
        ///
        /// A chunk with no entity storage is not a failure — absent storage
        /// means no entities, which is a complete answer — so those are
        /// counted in ChunksWithoutStorage and nothing else. Storage that
        /// exists and cannot be read is the case worth knowing about, and
        /// lands in UnreadableChunks.
        ///
        /// Each entry carries the entity's whole compound, so VillagerData,
        /// CustomName and the rest need no second pass. Those tags are the
        /// live ones while their chunk is loaded; this releases each chunk as
        /// it finishes, and a tag edited after its chunk was released is
        /// edited on a tree the world no longer reads. Pass keepLoaded when
        /// the plan is to modify what comes back.
        ///
        /// Python takes one id or an iterable in this argument; there is
        /// deliberately no single-string overload here, because null is a
        /// meaningful value for ids and a string overload would make
        /// EntitiesIn(c1, c2, null) — the obvious spelling of "every
        /// entity" — an ambiguous call. Ask for one kind with
        /// new[] { "minecraft:villager" }.</summary>
        public EntityQueryResult EntitiesIn(
            (int X, int Y, int Z) corner1, (int X, int Y, int Z) corner2,
            IEnumerable<string>? ids = null, string? dimension = null,
            int? limit = null, bool keepLoaded = false)
        {
            var dim = Dimension(dimension);
            int x0 = Math.Min(corner1.X, corner2.X), x1 = Math.Max(corner1.X, corner2.X);
            int y0 = Math.Min(corner1.Y, corner2.Y), y1 = Math.Max(corner1.Y, corner2.Y);
            int z0 = Math.Min(corner1.Z, corner2.Z), z1 = Math.Max(corner1.Z, corner2.Z);

            HashSet<string>? wanted = null;
            if (ids != null)
            {
                string[] listed = ids.ToArray();
                if (listed.Length == 0)
                    throw new NbtException(
                        "EntitiesIn was given an empty id filter; pass ids=null "
                        + "to report every entity");
                wanted = new HashSet<string>(StringComparer.Ordinal);
                foreach (string text in listed)
                    wanted.Add(text.IndexOf(':') >= 0 ? text : "minecraft:" + text);
            }

            var result = new EntityQueryResult();
            for (int chunkX = x0 >> 4; chunkX <= (x1 >> 4); chunkX++)
            {
                for (int chunkZ = z0 >> 4; chunkZ <= (z1 >> 4); chunkZ++)
                {
                    var chunk = dim.EntityChunk(chunkX, chunkZ);
                    Action release;
                    Tag? entitiesTag;
                    if (chunk == null)
                    {
                        // Before 20w45a a chunk kept its entities in its own
                        // Level, and a world nobody has stood in since keeps
                        // them there still.
                        var terrain = dim.Chunk(chunkX, chunkZ);
                        int? version = terrain?.Data?.Get("DataVersion") is Tag stamp
                            ? Tag.AsInt(stamp) : (int?)null;
                        if (version == null
                            || version.Value >= ChunkBlocks.LegacyEntityStorageDataVersion)
                        {
                            // No storage filed for this chunk: no entities,
                            // which is an answer, not a gap.
                            result.ChunksWithoutStorage++;
                            continue;
                        }
                        try
                        {
                            entitiesTag = dim.Blocks(chunkX, chunkZ)!.LegacyEntities;
                        }
                        catch (NbtException exc)
                        {
                            result.UnreadableChunks.Add(
                                ((chunkX, chunkZ), $"entities in the chunk: {exc.Message}"));
                            continue;
                        }
                        // released as terrain, so its accessor goes with it
                        int cx = chunkX, cz = chunkZ;
                        release = () => dim.Release(cx, cz);
                    }
                    else if (chunk.Data == null)
                    {
                        result.UnreadableChunks.Add(
                            ((chunkX, chunkZ), $"entity storage: {chunk.ParseError}"));
                        continue;
                    }
                    else
                    {
                        entitiesTag = chunk.Data.Get("Entities");
                        release = chunk.Release;
                    }
                    if (entitiesTag != null && !(entitiesTag is TagList))
                    {
                        // Storage that exists and makes no sense is
                        // unreadable, not empty. Reporting it as "no
                        // entities here" would let a corrupt chunk look
                        // like a quiet one.
                        result.UnreadableChunks.Add(
                            ((chunkX, chunkZ),
                             "entity storage: Entities is a "
                             + entitiesTag.GetType().Name + ", not a list"));
                        if (!keepLoaded)
                            release();
                        continue;
                    }
                    result.ChunksScanned++;
                    if (entitiesTag is TagList stored)
                    {
                        foreach (var item in stored)
                        {
                            var entity = (TagCompound)item;
                            if (entity.Get("Pos") is not TagList pos || pos.Count != 3)
                                continue;
                            double ex = Tag.AsDouble(pos[0]);
                            double ey = Tag.AsDouble(pos[1]);
                            double ez = Tag.AsDouble(pos[2]);
                            // Python stringifies whatever tag is there; a
                            // modded or damaged id that is not a string
                            // must keep its identity, not collapse to "".
                            var storedId = entity.Get("id");
                            string identity =
                                storedId == null ? "" : Tag.Text(storedId);
                            if (double.IsNaN(ex) || double.IsInfinity(ex)
                                || double.IsNaN(ey) || double.IsInfinity(ey)
                                || double.IsNaN(ez) || double.IsInfinity(ez))
                            {
                                // NaN floors to nothing and infinity floors
                                // to everything; either way the entity has
                                // no block to be in, and a cast would
                                // quietly file it in one that is real.
                                result.UnreadableChunks.Add(
                                    ((chunkX, chunkZ),
                                     "entity " + (identity.Length == 0 ? "?" : identity)
                                     + $" has non-finite Pos ({ex}, {ey}, {ez})"));
                                continue;
                            }
                            int bx = (int)Math.Floor(ex);
                            int by = (int)Math.Floor(ey);
                            int bz = (int)Math.Floor(ez);
                            if (bx < x0 || bx > x1 || by < y0 || by > y1
                                || bz < z0 || bz > z1)
                                continue;
                            if (wanted != null && !wanted.Contains(identity))
                                continue;
                            if (limit.HasValue && result.Entities.Count >= limit.Value)
                            {
                                result.Truncated = true;
                                if (!keepLoaded)
                                    release();
                                return result;
                            }
                            result.Entities.Add((identity, ex, ey, ez, entity));
                            result.Counts[identity] =
                                result.Counts.TryGetValue(identity, out int had)
                                    ? had + 1 : 1;
                        }
                    }
                    if (!keepLoaded)
                        release();
                }
            }
            return result;
        }
    }
}
