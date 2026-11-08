// Twin of heaton_voxel/world/results.py: result objects for whole-save
// operations. Every operation that can partially succeed reports exactly
// what happened; chunks the world never generated and chunks that exist but
// cannot be read are always listed apart, because "nothing there" and
// "something there I cannot see" mean opposite things to a user.

using System.Collections.Generic;

namespace HeatonVoxel.World
{
    /// <summary>What a <c>Fill</c> actually managed to do. A fill is not
    /// all-or-nothing: a box can reach into chunks the world has never
    /// generated, or above and below the buildable range.</summary>
    public sealed class FillResult
    {
        public int BlocksFilled;
        public int SectionsReplaced;
        public int ChunksModified;
        /// <summary>Chunks the world has never generated.</summary>
        public readonly List<(int X, int Z)> MissingChunks = new List<(int, int)>();
        /// <summary>Chunks that exist but could not be read — almost always
        /// written by a version older than this toolkit supports. Kept apart
        /// from MissingChunks on purpose: a long-lived world can hold a
        /// decade-old build in a chunk Minecraft has not re-saved since, and
        /// reporting that as "never generated" would suggest the area is
        /// empty when something is actually standing there.</summary>
        public readonly List<((int X, int Z) Chunk, string Reason)> UnreadableChunks =
            new List<((int, int), string)>();
        public int BlocksOutOfRange;

        /// <summary>True if every block in the box was written.</summary>
        public bool Complete =>
            MissingChunks.Count == 0 && UnreadableChunks.Count == 0
            && BlocksOutOfRange == 0;
    }

    /// <summary>What a <c>ReplaceBlocks</c> managed to do; the twin of
    /// ReplaceResult. Kept apart from FillResult because a replace stays out
    /// of two kinds of chunk a fill writes into without asking -- one the
    /// game has not finished generating, and one it cannot write, reported
    /// among UnreadableChunks -- and because it drops the block entities its
    /// rewrites orphan, which a fill leaves.</summary>
    public sealed class ReplaceResult
    {
        public int BlocksReplaced;
        public int SectionsReplaced;
        public int ChunksModified;
        public int BlockEntitiesRemoved;
        /// <summary>Chunks the world has never generated.</summary>
        public readonly List<(int X, int Z)> MissingChunks = new List<(int, int)>();
        /// <summary>Chunks the game has not finished generating, left as
        /// they are.</summary>
        public readonly List<(int X, int Z)> UnfinishedChunks = new List<(int, int)>();
        /// <summary>Chunks there but not writable here, with the reason.</summary>
        public readonly List<((int X, int Z) Chunk, string Reason)> UnreadableChunks =
            new List<((int, int), string)>();
        public int BlocksOutOfRange;

        /// <summary>True if every block in the box was read, and so every
        /// one the mapping names was rewritten.</summary>
        public bool Complete =>
            MissingChunks.Count == 0 && UnfinishedChunks.Count == 0
            && UnreadableChunks.Count == 0 && BlocksOutOfRange == 0;
    }

    /// <summary>What a <c>FillBiome</c> managed to do. Deliberately separate
    /// from FillResult rather than reusing it: biomes are stored per 4x4x4
    /// cell, so every count here is in cells, and naming them BlocksFilled
    /// would understate the affected volume by a factor of 64.</summary>
    public sealed class BiomeFillResult
    {
        public int CellsFilled;
        public int SectionsReplaced;
        public int ChunksModified;
        public readonly List<(int X, int Z)> MissingChunks = new List<(int, int)>();
        public readonly List<((int X, int Z) Chunk, string Reason)> UnreadableChunks =
            new List<((int, int), string)>();
        /// <summary>Cells in sections this chunk does not store, or that
        /// carry no biome container at all.</summary>
        public int CellsOutOfRange;
        /// <summary>The block-coordinate box actually written, cell-aligned.
        /// The box asked for is in block coordinates, but the smallest thing
        /// that can be written is a whole cell, so a box starting mid-cell
        /// writes the whole cell — reported rather than coerced silently.
        /// Null if the fill never got as far as writing anything.</summary>
        public ((int X, int Y, int Z) Min, (int X, int Y, int Z) Max)? WrittenBox;

        /// <summary>True if every cell in the box was written.</summary>
        public bool Complete =>
            MissingChunks.Count == 0 && UnreadableChunks.Count == 0
            && CellsOutOfRange == 0;
    }

    /// <summary>What an <c>Unfreeze</c> thawed. The three counters are the
    /// three things weather freezing writes, kept apart because they answer
    /// different questions: SnowRemoved is covering taken off, IceMelted is
    /// water handed back, and SnowyCleared is the snowy=true flag on the
    /// ground underneath, which is cosmetic and outlives both.
    ///
    /// There is no out-of-range count, unlike FillResult: a section the
    /// chunk does not store is air, and air is not frozen, so a box reaching
    /// past the world's height has nothing to fail at.
    ///
    /// FloatingWater is a <b>report, never a refusal</b>. It counts melted
    /// ice whose block below is air — a frozen lid over a drained lake,
    /// which thaws into water hanging in the sky and then falls when the
    /// game next updates it. Nothing about that is corrupt, but it is rarely
    /// what the caller pictured, and it is invisible from a top-down
    /// render.</summary>
    public sealed class UnfreezeResult
    {
        public int SnowRemoved;
        public int IceMelted;
        public int SnowyCleared;
        /// <summary>Snow left standing because it is deeper than freezing
        /// lays it: layers=2..8, which precipitation does not write unless a
        /// world raised the maxSnowAccumulationHeight game rule off its
        /// default of 1, so it is almost always a player's.
        /// Unfreeze(deepSnow: true) takes it; the counter is here so that
        /// leaving it is said out loud.</summary>
        public int DeepSnowKept;
        public int ChunksModified;
        /// <summary>Melted ice standing over air; see the class summary.</summary>
        public int FloatingWater;
        /// <summary>Chunks the world has never generated.</summary>
        public readonly List<(int X, int Z)> MissingChunks = new List<(int, int)>();
        /// <summary>Chunks that exist but could not be read; separate from
        /// MissingChunks for the reason FillResult gives.</summary>
        public readonly List<((int X, int Z) Chunk, string Reason)> UnreadableChunks =
            new List<((int, int), string)>();

        /// <summary>True if every chunk in the box was read and thawed.</summary>
        public bool Complete =>
            MissingChunks.Count == 0 && UnreadableChunks.Count == 0;
    }

    /// <summary>What a <c>DeleteChunks</c> removed. A chunk lives in three
    /// files, and the counters are per file rather than summed: terrain in
    /// region/, entities in entities/, cached points of interest in poi/. A
    /// world stores the last two only where it has something to store, so
    /// zeros there are ordinary.
    ///
    /// ChunksAbsent is chunks the box covered that had no terrain record to
    /// remove — already regenerating, in other words, which is the state
    /// this operation asks for. Nothing here is a failure, so there is no
    /// Complete: a chunk cannot be too damaged to delete, and deleting never
    /// decodes one.
    ///
    /// DeletedBox is the block-coordinate rectangle actually cleared, grown
    /// outward to chunk boundaries. It has no y: the whole column goes, and
    /// inventing bounds for a chunk that no longer exists would be a guess
    /// dressed as a fact.</summary>
    public sealed class ChunkDeleteResult
    {
        public int ChunksDeleted;
        public int ChunksAbsent;
        public int EntityChunksDeleted;
        public int PoiChunksDeleted;
        /// <summary>Region coordinates something was removed from. One
        /// (rx, rz) covers a terrain, an entity and a POI file, so this
        /// counts places on the map, not files on disk.</summary>
        public int RegionsTouched;
        /// <summary>The chunk-aligned rectangle cleared, ((x0, z0), (x1, z1)).</summary>
        public ((int X, int Z) Min, (int X, int Z) Max)? DeletedBox;
    }

    /// <summary>What a <c>CreateChunks</c> built, and what it left: only a
    /// chunk with no terrain record at all is built, and one the world holds
    /// is named, finished, unfinished or unreadable -- the twin of
    /// ChunkCreateResult.</summary>
    public sealed class ChunkCreateResult
    {
        public int ChunksCreated;
        public readonly List<(int X, int Z)> CreatedChunks = new List<(int, int)>();
        /// <summary>Region files the build made, which the world did not have.</summary>
        public int RegionsCreated;
        public readonly List<(int X, int Z)> ExistingChunks = new List<(int, int)>();
        public readonly List<(int X, int Z)> UnfinishedChunks = new List<(int, int)>();
        public readonly List<((int X, int Z) Chunk, string Reason)> UnreadableChunks =
            new List<((int, int), string)>();
        public int? DataVersion;
        public int? MinY;
        public int? MaxY;
        public string? Biome;

        /// <summary>Every chunk the box covers is now finished, built or
        /// found so.</summary>
        public bool Complete => UnfinishedChunks.Count == 0 && UnreadableChunks.Count == 0;
    }

    /// <summary>What a <c>Histogram</c> counted, and what it could not. A
    /// census over ground the world has never generated, or cannot read,
    /// must say so — a count that quietly treated those as zero would look
    /// exactly like "nothing there".</summary>
    public sealed class HistogramResult
    {
        public readonly Dictionary<Blocks.BlockState, int> Counts =
            new Dictionary<Blocks.BlockState, int>();
        public int BlocksCounted;
        public int ChunksScanned;
        public readonly List<(int X, int Z)> MissingChunks = new List<(int, int)>();
        public readonly List<((int X, int Z) Chunk, string Reason)> UnreadableChunks =
            new List<((int, int), string)>();
        public int BlocksOutOfRange;

        /// <summary>True if every block in the box was counted.</summary>
        public bool Complete =>
            MissingChunks.Count == 0 && UnreadableChunks.Count == 0
            && BlocksOutOfRange == 0;

        /// <summary>The n most common states, most common first.</summary>
        public List<KeyValuePair<Blocks.BlockState, int>> Top(int n = 10)
        {
            var ordered = new List<KeyValuePair<Blocks.BlockState, int>>(Counts);
            // Python sorts by -count alone, and its sort is stable, so ties
            // keep insertion order; List.Sort is not stable, so order the
            // ties explicitly by name to stay deterministic.
            ordered.Sort((a, b) =>
            {
                int byCount = b.Value.CompareTo(a.Value);
                return byCount != 0
                    ? byCount
                    : string.CompareOrdinal(a.Key.ToString(), b.Key.ToString());
            });
            return ordered.GetRange(0, System.Math.Min(n, ordered.Count));
        }
    }

    /// <summary>What a <c>ResetLighting</c> touched. Ungenerated chunks are
    /// not failures here — a chunk is lit by generation when it is born, so
    /// there is nothing stale to clear — but they are counted. Unreadable
    /// chunks are the real problem: their stale light cannot be cleared, and
    /// an edit beside one may keep a dark seam at the border.</summary>
    public sealed class LightResetResult
    {
        public int ChunksReset;
        public int ArraysRemoved;
        /// <summary>Chunks the world has never generated: skipped, by design.</summary>
        public int ChunksSkipped;
        public readonly List<((int X, int Z) Chunk, string Reason)> UnreadableChunks =
            new List<((int, int), string)>();
    }

    /// <summary>What a <c>RebuildHeightmaps</c> rebuilt. Chunks that were
    /// never generated are skipped and listed; chunks that exist but cannot
    /// be read are listed apart, because their stale heightmaps remain
    /// stale.</summary>
    public sealed class HeightmapResult
    {
        public int ChunksRebuilt;
        public readonly List<(int X, int Z)> MissingChunks = new List<(int, int)>();
        public readonly List<(int X, int Z)> UnfinishedChunks = new List<(int, int)>();
        public readonly List<((int X, int Z) Chunk, string Reason)> UnreadableChunks =
            new List<((int, int), string)>();

        public bool Complete =>
            MissingChunks.Count == 0 && UnfinishedChunks.Count == 0
            && UnreadableChunks.Count == 0;
    }

    /// <summary>The world border: a square wall centered on
    /// (CenterX, CenterZ). Size is the full diameter, so the wall stands
    /// Size / 2 out from the center on each axis. A world that has never
    /// had its border changed keeps no border file at all; that state comes
    /// back as the game's defaults with <see cref="Stored"/> false, so
    /// "default border" and "border somebody set" stay distinguishable.</summary>
    public sealed class WorldBorder
    {
        /// <summary>The default diameter: walls at x and z = +/-29,999,984.</summary>
        public const double DefaultSize = 59_999_968.0;

        public readonly double CenterX;
        public readonly double CenterZ;
        public readonly double Size;
        public readonly double SafeZone;
        public readonly double DamagePerBlock;
        public readonly int WarningBlocks;
        public readonly int WarningTime;
        public readonly double LerpTarget;
        public readonly long LerpTime;
        public readonly bool Stored;

        public WorldBorder(double centerX = 0.0, double centerZ = 0.0,
                           double size = DefaultSize, double safeZone = 5.0,
                           double damagePerBlock = 0.2, int warningBlocks = 5,
                           int warningTime = 15, double? lerpTarget = null,
                           long lerpTime = 0, bool stored = false)
        {
            CenterX = centerX;
            CenterZ = centerZ;
            Size = size;
            SafeZone = safeZone;
            DamagePerBlock = damagePerBlock;
            WarningBlocks = warningBlocks;
            WarningTime = warningTime;
            // A border at rest lerps to where it already is.
            LerpTarget = lerpTarget ?? size;
            LerpTime = lerpTime;
            Stored = stored;
        }

        public double MinX => CenterX - Size / 2;
        public double MaxX => CenterX + Size / 2;
        public double MinZ => CenterZ - Size / 2;
        public double MaxZ => CenterZ + Size / 2;

        /// <summary>Whether (x, z) is inside the border, walls inclusive.</summary>
        public bool Contains(double x, double z) =>
            MinX <= x && x <= MaxX && MinZ <= z && z <= MaxZ;

        /// <summary>Blocks from (x, z) to the nearest wall. Positive inside
        /// the border, negative outside, zero on a wall — signed so "how
        /// close am I" and "how far out am I" are one number.</summary>
        public double DistanceToEdge(double x, double z) =>
            System.Math.Min(
                System.Math.Min(x - MinX, MaxX - x),
                System.Math.Min(z - MinZ, MaxZ - z));

        /// <summary>The nearest point to (x, z) that is inside the border.
        /// margin pulls the allowed square in from the walls, for callers
        /// that want to stop short rather than dead on the line. A margin
        /// wider than the border itself collapses to the center.</summary>
        public (double X, double Z) Clamp(double x, double z, double margin = 0.0)
        {
            double loX = MinX + margin, hiX = MaxX - margin;
            double loZ = MinZ + margin, hiZ = MaxZ - margin;
            if (loX > hiX)
                loX = hiX = CenterX;
            if (loZ > hiZ)
                loZ = hiZ = CenterZ;
            return (System.Math.Min(System.Math.Max(x, loX), hiX),
                    System.Math.Min(System.Math.Max(z, loZ), hiZ));
        }
    }

    /// <summary>The world's weather exactly as stored, plus what it adds up
    /// to. The stored form is five fields because the game runs two timers
    /// at once; <see cref="Kind"/> collapses them into the one word a person
    /// means — "thunder" outranks "rain" outranks "clear", the same
    /// precedence the game's own renderer applies.</summary>
    public sealed class WeatherState
    {
        public readonly bool Raining;
        public readonly bool Thundering;
        public readonly int RainTime;
        public readonly int ThunderTime;
        public readonly int ClearWeatherTime;

        public WeatherState(bool raining, bool thundering, int rainTime,
                            int thunderTime, int clearWeatherTime)
        {
            Raining = raining;
            Thundering = thundering;
            RainTime = rainTime;
            ThunderTime = thunderTime;
            ClearWeatherTime = clearWeatherTime;
        }

        /// <summary>"clear", "rain" or "thunder".</summary>
        public string Kind =>
            Raining && Thundering ? "thunder" : Raining ? "rain" : "clear";
    }

    /// <summary>Where a block search found its matches, and what it could not
    /// see.
    ///
    /// <see cref="Matches"/> holds (x, y, z, state) in a fixed order — chunk
    /// by chunk, then section, then y, z, x inside it — so the same world
    /// always yields the same list and two ports can be compared line for
    /// line. Sort it if you want world order instead; the scan will not pay
    /// for an ordering nobody asked for.
    ///
    /// <see cref="SectionsSkipped"/> counts the sections dismissed on their
    /// palette alone: a section whose palette holds no wanted name cannot
    /// hold the block, and is never unpacked. That is where a sweep of a
    /// whole region gets its speed, and a low number next to a high
    /// SectionsScanned means the search terms were too broad to
    /// prune.</summary>
    public sealed class FindResult
    {
        /// <summary>How many entries Python's ``[:n]`` would keep out of
        /// ``count``. A negative n drops that many from the end there and
        /// must not throw here; GetRange would.</summary>
        internal static int SliceLength(int n, int count) =>
            n < 0 ? System.Math.Max(0, count + n) : System.Math.Min(n, count);

        public readonly List<(int X, int Y, int Z, Blocks.BlockState State)> Matches =
            new List<(int, int, int, Blocks.BlockState)>();
        public readonly Dictionary<Blocks.BlockState, int> Counts =
            new Dictionary<Blocks.BlockState, int>();
        /// <summary>Set when a limit stopped the search early.</summary>
        public bool Truncated;
        public int ChunksScanned;
        public int SectionsScanned;
        public int SectionsSkipped;
        /// <summary>Chunks the world has never generated.</summary>
        public readonly List<(int X, int Z)> MissingChunks = new List<(int, int)>();
        /// <summary>Chunks that exist but could not be read; separate from
        /// MissingChunks for the same reason as in FillResult.</summary>
        public readonly List<((int X, int Z) Chunk, string Reason)> UnreadableChunks =
            new List<((int, int), string)>();

        /// <summary>How many matches came back — Python's len(result).</summary>
        public int Count => Matches.Count;

        /// <summary>True if the whole box was searched and nothing was
        /// withheld. A search stopped by a limit is not complete — there may
        /// be more out there, and a caller that treats a truncated answer as
        /// exhaustive draws the wrong conclusion from it.</summary>
        public bool Complete =>
            MissingChunks.Count == 0 && UnreadableChunks.Count == 0 && !Truncated;

        /// <summary>Just the coordinates, in the same order as
        /// <see cref="Matches"/>.</summary>
        public List<(int X, int Y, int Z)> Positions()
        {
            var found = new List<(int X, int Y, int Z)>(Matches.Count);
            foreach (var (x, y, z, _) in Matches)
                found.Add((x, y, z));
            return found;
        }

        /// <summary>The n most common matched states, most common
        /// first.</summary>
        public List<KeyValuePair<Blocks.BlockState, int>> Top(int n = 10)
        {
            var ordered = new List<KeyValuePair<Blocks.BlockState, int>>(Counts);
            // Both twins order ties by name. Python's sort is stable and
            // List.Sort is not, so leaving ties to fall out of encounter
            // order would answer differently here than there -- and a
            // summary that cannot be compared across the port is a summary
            // the corpus cannot check.
            ordered.Sort((a, b) =>
            {
                int byCount = b.Value.CompareTo(a.Value);
                return byCount != 0
                    ? byCount
                    : string.CompareOrdinal(a.Key.ToString(), b.Key.ToString());
            });
            return ordered.GetRange(0, SliceLength(n, ordered.Count));
        }
    }

    /// <summary>Entities standing inside a box, and the storage that failed
    /// to read.
    ///
    /// <see cref="Entities"/> holds (identity, x, y, z, tag) — the exact
    /// double coordinates the game stores, and the whole compound, so callers
    /// can reach VillagerData, CustomName or anything else without a second
    /// pass. Order is chunk by chunk, then the order the chunk stores them
    /// in, which is the order the game will tick them.
    ///
    /// Chunks holding no entity storage are NOT a partial failure: absent
    /// storage means no entities, a complete answer. Only storage that exists
    /// and cannot be read lands in <see cref="UnreadableChunks"/>, and only
    /// that makes the result incomplete.</summary>
    public sealed class EntityQueryResult
    {
        public readonly List<(string Id, double X, double Y, double Z,
                              Nbt.TagCompound Tag)> Entities =
            new List<(string, double, double, double, Nbt.TagCompound)>();
        public readonly Dictionary<string, int> Counts =
            new Dictionary<string, int>(System.StringComparer.Ordinal);
        /// <summary>Set when a limit stopped the walk early.</summary>
        public bool Truncated;
        public int ChunksScanned;
        /// <summary>Chunks filing no entity storage at all: no entities, a
        /// complete answer, not a failure.</summary>
        public int ChunksWithoutStorage;
        public readonly List<((int X, int Z) Chunk, string Reason)> UnreadableChunks =
            new List<((int, int), string)>();

        /// <summary>How many entities came back — Python's len(result).</summary>
        public int Count => Entities.Count;

        public bool Complete => UnreadableChunks.Count == 0 && !Truncated;

        /// <summary>Just the coordinates, in the same order as
        /// <see cref="Entities"/>.</summary>
        public List<(double X, double Y, double Z)> Positions()
        {
            var found = new List<(double X, double Y, double Z)>(Entities.Count);
            foreach (var (_, x, y, z, _) in Entities)
                found.Add((x, y, z));
            return found;
        }

        /// <summary>The n most common entity ids, most common first.</summary>
        public List<KeyValuePair<string, int>> Top(int n = 10)
        {
            var ordered = new List<KeyValuePair<string, int>>(Counts);
            // Ordered by id within a tie, for the reason FindResult.Top
            // gives: stable in Python, not here, so the order is made
            // explicit on both sides.
            ordered.Sort((a, b) =>
            {
                int byCount = b.Value.CompareTo(a.Value);
                return byCount != 0
                    ? byCount
                    : string.CompareOrdinal(a.Key, b.Key);
            });
            return ordered.GetRange(0, FindResult.SliceLength(n, ordered.Count));
        }
    }
}
