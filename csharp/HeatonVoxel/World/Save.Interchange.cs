// Twin of heaton_voxel/world/interchange.py: .schem export and import, as
// save methods, delegated to HeatonVoxel.Schem.

using HeatonVoxel.Schem;

namespace HeatonVoxel.World
{
    public sealed partial class MinecraftSave
    {
        /// <summary>Copy the box between two corners into a Sponge .schem
        /// file. The corners are inclusive global block coordinates in any
        /// order, the same box a Fill takes. Block entities inside it (chest
        /// contents, sign text, spawner settings) travel with their blocks;
        /// entities=true also copies mobs, item frames and armor stands.
        /// This both builds the schematic and writes it.</summary>
        public SchemExportResult ExportSchem(
            (int X, int Y, int Z) corner1, (int X, int Y, int Z) corner2, string path,
            (int X, int Y, int Z)? origin = null, bool entities = false,
            bool biomes = false, int version = 3, string? dimension = null,
            bool allowIncomplete = false, bool keepLoaded = false) =>
            Copy.ExportSchem(this, corner1, corner2, path, origin, entities, biomes,
                             version, dimension, allowIncomplete, keepLoaded);

        /// <summary>Paste a .schem file into the world. at is where the
        /// schematic's ORIGIN lands — for a WorldEdit file, stand at at and
        /// the build appears exactly as //paste would place it. Pass
        /// anchor="min" to put the minimum corner at at instead, ignoring any
        /// stored offset. rotate (multiples of 90 degrees, clockwise from
        /// above) and flip ("x", "y" or "z"; applied before the rotation)
        /// transform the copy on the way in. Nothing reaches disk until
        /// Save().</summary>
        public SchemImportResult ImportSchem(
            string path, (int X, int Y, int Z) at, string anchor = "origin",
            bool entities = true, bool biomes = false, bool pasteAir = true,
            bool relight = true, bool heightmaps = true, string? dimension = null,
            bool prune = true, bool keepLoaded = false, int rotate = 0,
            string? flip = null, bool pasteVoids = false) =>
            Copy.ImportSchem(this, path, at, anchor, entities, biomes, pasteAir,
                             relight, heightmaps, dimension, prune, keepLoaded,
                             rotate, flip, pasteVoids);

        /// <summary>Paste an already-loaded schematic; see the path
        /// overload for the rules.</summary>
        public SchemImportResult ImportSchem(
            HeatonVoxel.Schem.Schematic source, (int X, int Y, int Z) at,
            string anchor = "origin", bool entities = true, bool biomes = false,
            bool pasteAir = true, bool relight = true, bool heightmaps = true,
            string? dimension = null, bool prune = true, bool keepLoaded = false,
            int rotate = 0, string? flip = null, bool pasteVoids = false) =>
            Copy.ImportSchem(this, source, at, anchor, entities, biomes, pasteAir,
                             relight, heightmaps, dimension, prune, keepLoaded,
                             rotate, flip, pasteVoids);
    }
}
