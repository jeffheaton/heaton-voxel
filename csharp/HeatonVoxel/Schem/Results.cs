// Twin of heaton_voxel/schem/results.py: result objects for schematic export
// and import, with the same honest accounting as every other whole-save
// operation.

using System.Collections.Generic;

namespace HeatonVoxel.Schem
{
    /// <summary>What an export actually captured. Mirrors FillResult's
    /// accounting: chunks the world never generated and chunks that exist but
    /// cannot be read are listed separately, because "nothing there" and
    /// "something there I cannot see" must never look alike. With the default
    /// allowIncomplete=false an export refuses instead, so these lists only
    /// fill in when the caller opted into air-filling the gaps.</summary>
    public sealed class SchemExportResult
    {
        public readonly Schematic Schematic;
        public string? Path;
        /// <summary>Cells copied from stored sections.</summary>
        public int BlocksExported;
        /// <summary>Cells written as air because the world holds nothing
        /// there: above or below the stored sections, or (with
        /// allowIncomplete) inside a missing or unreadable chunk.</summary>
        public int BlocksOutOfRange;
        public int BlockEntitiesExported;
        public int EntitiesExported;
        /// <summary>Biome cells filled with a placeholder for lack of stored
        /// data.</summary>
        public int BiomeCellsAbsent;
        public int ChunksRead;
        public readonly List<(int X, int Z)> MissingChunks = new List<(int, int)>();
        public readonly List<((int X, int Z) Chunk, string Reason)> UnreadableChunks =
            new List<((int, int), string)>();

        public SchemExportResult(Schematic schematic)
        {
            Schematic = schematic;
        }

        /// <summary>True if every cell in the box came from real stored data.</summary>
        public bool Complete =>
            MissingChunks.Count == 0 && UnreadableChunks.Count == 0
            && BlocksOutOfRange == 0 && BiomeCellsAbsent == 0;
    }

    /// <summary>What a paste actually changed, and what it could not. The
    /// same not-all-or-nothing accounting as FillResult: a paste reaching
    /// into chunks the world never generated skips them and says so, rather
    /// than inventing terrain or failing wholesale.</summary>
    public sealed class SchemImportResult
    {
        /// <summary>The world-coordinate box the schematic occupies,
        /// inclusive.</summary>
        public ((int X, int Y, int Z) Min, (int X, int Y, int Z) Max)? PlacedBox;
        public int BlocksPasted;
        /// <summary>Cells not written because pasteAir=false and the
        /// schematic holds air there.</summary>
        public int AirSkipped;
        /// <summary>structure_void cells left as the world has them
        /// (pasteVoids=false).</summary>
        public int VoidsSkipped;
        public int SectionsReplaced;
        public int ChunksModified;
        /// <summary>Chunks whose blocks moved and whose four maps were
        /// rewritten.</summary>
        public int HeightmapsRebuilt;
        /// <summary>Chunks the world has never generated: skipped, never
        /// created.</summary>
        public readonly List<(int X, int Z)> MissingChunks = new List<(int, int)>();
        /// <summary>Chunks the game has not finished generating: skipped,
        /// because it generates over whatever is written into them.</summary>
        public readonly List<(int X, int Z)> UnfinishedChunks = new List<(int, int)>();
        public readonly List<((int X, int Z) Chunk, string Reason)> UnreadableChunks =
            new List<((int, int), string)>();
        public int BlocksOutOfRange;
        public int BlockEntitiesAdded;
        /// <summary>Stale block entities removed from overwritten positions —
        /// a chest replaced by stone must not leave its inventory behind in
        /// the chunk.</summary>
        public int BlockEntitiesRemoved;
        public int EntitiesAdded;
        /// <summary>(entity id, world position) of entities that could not be
        /// placed: their chunk is ungenerated or unfinished, or its entity
        /// storage is unreadable.</summary>
        public readonly List<(string Id, (double X, double Y, double Z) At)>
            EntitiesSkipped = new List<(string, (double, double, double))>();
        public int BiomeCellsSet;
        public int BiomeCellsOutOfRange;
        /// <summary>The LightResetResult from the automatic relight, or null
        /// if relight was off or nothing was written.</summary>
        public World.LightResetResult? Lighting;
        /// <summary>From the schematic that was pasted: states whose
        /// properties look directional but sit outside the rotate/flip
        /// vocabulary.</summary>
        public List<string> TransformWarnings = new List<string>();

        /// <summary>True if everything the schematic holds was placed.</summary>
        public bool Complete =>
            MissingChunks.Count == 0 && UnfinishedChunks.Count == 0
            && UnreadableChunks.Count == 0
            && BlocksOutOfRange == 0 && EntitiesSkipped.Count == 0;
    }
}
