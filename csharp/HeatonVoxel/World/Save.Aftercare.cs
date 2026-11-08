// Twin of heaton_voxel/world/aftercare.py: the aftercare litany — lighting,
// heightmaps, POI, derived states. Editing a world leaves baked data
// describing the world as it was; each of these hands one kind back to the
// game.

using System;
using System.Collections.Generic;
using System.Linq;
using HeatonVoxel.Blocks;
using HeatonVoxel.Nbt;

namespace HeatonVoxel.World
{
    public sealed partial class MinecraftSave
    {
        /// <summary>Hand lighting in an area back to the game. Call after
        /// editing.
        ///
        /// Chunk light is baked: the game stores computed light per section
        /// and trusts it for as long as a chunk's isLightOn flag stands, so
        /// any block edit leaves light describing the world as it was — a
        /// torch placed in a newly carved room lights only its own block.
        /// Light is stored per chunk, so whole chunks are reset and the y
        /// coordinates are ignored.
        ///
        /// neighbors widens the reset one chunk outward: light propagates up
        /// to 15 blocks across chunk borders, and a relit chunk beside an
        /// unmarked one pulls the neighbor's stale darkness back in.</summary>
        public LightResetResult ResetLighting(
            (int X, int Y, int Z) corner1, (int X, int Y, int Z) corner2,
            string? dimension = null, bool neighbors = true)
        {
            var dim = Dimension(dimension);
            int x0 = Math.Min(corner1.X, corner2.X), x1 = Math.Max(corner1.X, corner2.X);
            int z0 = Math.Min(corner1.Z, corner2.Z), z1 = Math.Max(corner1.Z, corner2.Z);
            int ring = neighbors ? 1 : 0;

            var result = new LightResetResult();
            for (int chunkX = (x0 >> 4) - ring; chunkX <= (x1 >> 4) + ring; chunkX++)
            {
                for (int chunkZ = (z0 >> 4) - ring; chunkZ <= (z1 >> 4) + ring; chunkZ++)
                {
                    ChunkBlocks? accessor;
                    try
                    {
                        accessor = dim.WritableBlocks(chunkX, chunkZ);
                    }
                    catch (NbtException exc)
                    {
                        // There, but not safely editable: an old chunk, or damaged.
                        result.UnreadableChunks.Add(((chunkX, chunkZ), exc.Message));
                        continue;
                    }
                    if (accessor == null)
                    {
                        result.ChunksSkipped++;
                        continue;
                    }
                    result.ArraysRemoved += accessor.ResetLighting();
                    result.ChunksReset++;
                }
            }
            return result;
        }

        /// <summary>Recompute stored heightmaps for every chunk a box
        /// touches, all four from the blocks by the game's own tags
        /// (ChunkBlocks.HeightmapMask), byte for byte what the game stores
        /// for a map that was right. The y coordinates are ignored, whole
        /// chunks are rebuilt. Ungenerated chunks are skipped and reported,
        /// and so are unfinished ones: the game generates over them, and
        /// writes their maps when it finishes them.</summary>
        public HeightmapResult RebuildHeightmaps(
            (int X, int Y, int Z) corner1, (int X, int Y, int Z) corner2,
            string? dimension = null, bool keepLoaded = false)
        {
            var dim = Dimension(dimension);
            int x0 = Math.Min(corner1.X, corner2.X), x1 = Math.Max(corner1.X, corner2.X);
            int z0 = Math.Min(corner1.Z, corner2.Z), z1 = Math.Max(corner1.Z, corner2.Z);
            var result = new HeightmapResult();

            for (int chunkX = x0 >> 4; chunkX <= (x1 >> 4); chunkX++)
            {
                for (int chunkZ = z0 >> 4; chunkZ <= (z1 >> 4); chunkZ++)
                {
                    ChunkBlocks? accessor;
                    try
                    {
                        accessor = dim.WritableBlocks(chunkX, chunkZ);
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
                    if (accessor.Finished)
                    {
                        accessor.RebuildHeightmaps();
                        result.ChunksRebuilt++;
                    }
                    else
                    {
                        result.UnfinishedChunks.Add((chunkX, chunkZ));
                    }
                    if (!keepLoaded)
                        dim.Release(chunkX, chunkZ);
                }
            }
            return result;
        }

        /// <summary>Drop stored points-of-interest records for every chunk
        /// in a box, so the game rescans and rebuilds them from the actual
        /// blocks — the safe direction, since the cache is derived data.
        ///
        /// POI storage is the game's cache of where beds, workstations,
        /// bells and portals stand, and is NOT updated by editing blocks, so
        /// a villager can hold a claim on a bed that no longer exists. The y
        /// coordinates are ignored. Returns the number of chunk records
        /// dropped (an area that never had records needs nothing dropped:
        /// absent records already mean "rescan").</summary>
        public int InvalidatePoi((int X, int Y, int Z) corner1,
                                 (int X, int Y, int Z) corner2,
                                 string? dimension = null)
        {
            var dim = Dimension(dimension);
            dim.RefuseReadOnly("drop points of interest from");
            int x0 = Math.Min(corner1.X, corner2.X), x1 = Math.Max(corner1.X, corner2.X);
            int z0 = Math.Min(corner1.Z, corner2.Z), z1 = Math.Max(corner1.Z, corner2.Z);
            int removed = 0;
            for (int chunkX = x0 >> 4; chunkX <= (x1 >> 4); chunkX++)
            {
                for (int chunkZ = z0 >> 4; chunkZ <= (z1 >> 4); chunkZ++)
                {
                    var region = dim.PoiRegion(chunkX >> 5, chunkZ >> 5);
                    if (region == null)
                        continue;
                    if (region.Chunks.Remove((chunkX & 31, chunkZ & 31)))
                        removed++;
                }
            }
            return removed;
        }

        /// <summary>Recompute neighbor-derived states in a box: fence, wall,
        /// pane and iron-bar connections, gate in_wall flags, and stair
        /// corner shapes. Call it over any area where such blocks were
        /// placed, the way ResetLighting is called over any area that was
        /// edited — the game only derives these states on a block update, so
        /// blocks written directly render disconnected until then.</summary>
        public ConnectResult ConnectBlocks(
            (int X, int Y, int Z) corner1, (int X, int Y, int Z) corner2,
            string? dimension = null, bool stairs = true, bool keepLoaded = false) =>
            Connect.ConnectBlocks(this, corner1, corner2, dimension, stairs, keepLoaded);
    }
}
