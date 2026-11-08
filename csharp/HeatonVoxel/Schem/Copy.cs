// Twin of heaton_voxel/schem/copy.py: moving boxes between a save and a
// Schematic — export and paste, with the same not-all-or-nothing accounting
// as fill.

using System;
using System.Collections.Generic;
using System.Linq;
using HeatonVoxel.Blocks;
using HeatonVoxel.Nbt;
using HeatonVoxel.World;

namespace HeatonVoxel.Schem
{
    public static class Copy
    {
        internal static (Schematic Schematic, SchemExportResult Result) CopyOut(
            MinecraftSave save, (int X, int Y, int Z) corner1,
            (int X, int Y, int Z) corner2, (int X, int Y, int Z)? origin,
            bool entities, bool biomes, string? dimension, bool allowIncomplete,
            bool keepLoaded)
        {
            var dim = save.Dimension(dimension);
            int x0 = Math.Min(corner1.X, corner2.X), x1 = Math.Max(corner1.X, corner2.X);
            int y0 = Math.Min(corner1.Y, corner2.Y), y1 = Math.Max(corner1.Y, corner2.Y);
            int z0 = Math.Min(corner1.Z, corner2.Z), z1 = Math.Max(corner1.Z, corner2.Z);
            int width = x1 - x0 + 1, height = y1 - y0 + 1, length = z1 - z0 + 1;

            // The data version is stamped from the chunks themselves once one
            // is seen; MinDataVersion is only a floor to get the object built.
            var schematic = new Schematic(width, height, length,
                                          ChunkBlocks.MinDataVersion);
            if (origin != null)
                schematic.Offset = (x0 - origin.Value.X, y0 - origin.Value.Y,
                                    z0 - origin.Value.Z);
            var result = new SchemExportResult(schematic);
            int seenDataVersion = 0;
            int[] blocks = schematic.Indices();
            int[]? biomeValues = null;
            if (biomes)
            {
                schematic.FillBiomes("minecraft:plains");  // placeholder for absences
                biomeValues = schematic.BiomeIndices;
            }

            for (int chunkX = x0 >> 4; chunkX <= (x1 >> 4); chunkX++)
            {
                for (int chunkZ = z0 >> 4; chunkZ <= (z1 >> 4); chunkZ++)
                {
                    int baseX = chunkX * 16, baseZ = chunkZ * 16;
                    int lx0 = Math.Max(x0, baseX) - baseX;
                    int lx1 = Math.Min(x1, baseX + 15) - baseX;
                    int lz0 = Math.Max(z0, baseZ) - baseZ;
                    int lz1 = Math.Min(z1, baseZ + 15) - baseZ;
                    int column = (lx1 - lx0 + 1) * (lz1 - lz0 + 1);

                    ChunkBlocks? accessor;
                    try
                    {
                        accessor = dim.Blocks(chunkX, chunkZ);
                        // A schematic holds one DataVersion for all its
                        // blocks, and an old chunk's states and block
                        // entities are not the current game's, so it is not
                        // exported as if they were: it is refused, or
                        // exported as air, like a chunk that cannot be read.
                        accessor?.RefuseLegacy("export to a schematic");
                    }
                    catch (NbtException exc)
                    {
                        if (!allowIncomplete)
                            throw new NbtException(
                                $"chunk ({chunkX}, {chunkZ}) in the export box exists "
                                + $"but cannot be read ({exc.Message}); something may "
                                + "be standing there that this export would "
                                + "misrepresent as air. Pass allowIncomplete to "
                                + "export air there anyway.");
                        result.UnreadableChunks.Add(((chunkX, chunkZ), exc.Message));
                        result.BlocksOutOfRange += column * height;
                        continue;
                    }
                    if (accessor == null)
                    {
                        if (!allowIncomplete)
                            throw new NbtException(
                                $"chunk ({chunkX}, {chunkZ}) in the export box has "
                                + "never been generated; there are no blocks there "
                                + "to copy. Pass allowIncomplete to export air there "
                                + "instead.");
                        result.MissingChunks.Add((chunkX, chunkZ));
                        result.BlocksOutOfRange += column * height;
                        continue;
                    }

                    seenDataVersion = Math.Max(seenDataVersion, accessor.DataVersion);
                    for (int sectionY = y0 >> 4; sectionY <= (y1 >> 4); sectionY++)
                    {
                        int baseY = sectionY * 16;
                        int ly0 = Math.Max(y0, baseY) - baseY;
                        int ly1 = Math.Min(y1, baseY + 15) - baseY;
                        accessor.Sections.TryGetValue(sectionY, out var section);
                        var container = section?.Blocks;
                        if (container == null)
                        {
                            // Above or below what the chunk stores: air, counted.
                            result.BlocksOutOfRange += column * (ly1 - ly0 + 1);
                        }
                        else
                        {
                            int[] indices = container.Indices();
                            var localMap = new int[container.PaletteCount];
                            for (int i = 0; i < localMap.Length; i++)
                                localMap[i] = schematic.InternState(
                                    container.PaletteEntry(i));
                            for (int ly = ly0; ly <= ly1; ly++)
                            {
                                int sy = baseY + ly - y0;
                                int rowY = ly * 256;
                                for (int lz = lz0; lz <= lz1; lz++)
                                {
                                    int src = rowY + lz * 16 + lx0;
                                    int dst = (sy * length + (baseZ + lz - z0)) * width
                                              + (baseX + lx0 - x0);
                                    for (int lx = lx0; lx <= lx1; lx++)
                                    {
                                        blocks[dst] = localMap[indices[src]];
                                        src++;
                                        dst++;
                                    }
                                }
                            }
                            result.BlocksExported += column * (ly1 - ly0 + 1);
                        }

                        if (biomeValues != null)
                        {
                            var bio = section?.Biomes;
                            if (bio == null)
                            {
                                result.BiomeCellsAbsent += column * (ly1 - ly0 + 1);
                                if (!allowIncomplete)
                                    throw new NbtException(
                                        $"chunk ({chunkX}, {chunkZ}) stores no biome "
                                        + $"data for section y={sectionY}; the export "
                                        + "cannot say what the climate there is. Pass "
                                        + "allowIncomplete to write minecraft:plains "
                                        + "there instead.");
                            }
                            else
                            {
                                int[] bioIndices = bio.Indices();
                                var bioMap = new int[bio.PaletteCount];
                                for (int i = 0; i < bioMap.Length; i++)
                                    bioMap[i] = schematic.InternBiomeName(
                                        bio.PaletteEntry(i));
                                for (int ly = ly0; ly <= ly1; ly++)
                                {
                                    int sy = baseY + ly - y0;
                                    int cellY = (ly >> 2) * 16;
                                    for (int lz = lz0; lz <= lz1; lz++)
                                    {
                                        int cellYz = cellY + (lz >> 2) * 4;
                                        int dst = (sy * length + (baseZ + lz - z0))
                                                  * width + (baseX + lx0 - x0);
                                        for (int lx = lx0; lx <= lx1; lx++)
                                        {
                                            biomeValues[dst] =
                                                bioMap[bioIndices[cellYz + (lx >> 2)]];
                                            dst++;
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // Block entities ride with their blocks; position becomes
                    // relative, keepPacked is the chunk's business.
                    var chunk = dim.Chunk(chunkX, chunkZ);
                    if (chunk?.Data?.Get("block_entities") is TagList stored)
                    {
                        foreach (var item in stored)
                        {
                            var entry = (TagCompound)item;
                            int ex = Tag.AsInt(entry["x"]);
                            int ey = Tag.AsInt(entry["y"]);
                            int ez = Tag.AsInt(entry["z"]);
                            if (ex < x0 || ex > x1 || ey < y0 || ey > y1
                                || ez < z0 || ez > z1)
                                continue;
                            var data = new TagCompound();
                            foreach (var field in entry)
                            {
                                if (field.Key != "x" && field.Key != "y"
                                    && field.Key != "z" && field.Key != "keepPacked")
                                    data[field.Key] = Support.CopyTag(field.Value);
                            }
                            if (!data.ContainsKey("id"))
                                throw new NbtException(
                                    $"block entity at ({ex}, {ey}, {ez}) has no 'id'; "
                                    + "the chunk is damaged");
                            schematic.BlockEntities[(ex - x0, ey - y0, ez - z0)] = data;
                            result.BlockEntitiesExported++;
                        }
                    }

                    result.ChunksRead++;
                    if (!keepLoaded)
                        dim.Release(chunkX, chunkZ);
                }
            }

            if (entities)
            {
                for (int chunkX = x0 >> 4; chunkX <= (x1 >> 4); chunkX++)
                {
                    for (int chunkZ = z0 >> 4; chunkZ <= (z1 >> 4); chunkZ++)
                    {
                        var chunk = dim.EntityChunk(chunkX, chunkZ);
                        if (chunk == null)
                            continue;
                        if (chunk.Data == null)
                        {
                            string reason = $"entity storage: {chunk.ParseError}";
                            if (!allowIncomplete)
                                throw new NbtException(
                                    $"entity storage for chunk ({chunkX}, {chunkZ}) "
                                    + $"cannot be read ({chunk.ParseError}); entities "
                                    + "may be standing there. Pass allowIncomplete to "
                                    + "export without them.");
                            result.UnreadableChunks.Add(((chunkX, chunkZ), reason));
                            continue;
                        }
                        if (chunk.Data.Get("Entities") is TagList list)
                        {
                            foreach (var item in list)
                            {
                                var entity = (TagCompound)item;
                                if (entity.Get("Pos") is not TagList pos || pos.Count != 3)
                                    continue;
                                int ex = (int)Math.Floor(Tag.AsDouble(pos[0]));
                                int ey = (int)Math.Floor(Tag.AsDouble(pos[1]));
                                int ez = (int)Math.Floor(Tag.AsDouble(pos[2]));
                                if (ex < x0 || ex > x1 || ey < y0 || ey > y1
                                    || ez < z0 || ez > z1)
                                    continue;
                                var copy = (TagCompound)Support.CopyTag(entity);
                                Support.ShiftEntity(copy, -x0, -y0, -z0);
                                schematic.Entities.Add(copy);
                                result.EntitiesExported++;
                            }
                        }
                        if (!keepLoaded)
                            chunk.Release();
                    }
                }
            }

            if (seenDataVersion != 0)
            {
                schematic.DataVersion = seenDataVersion;
            }
            else
            {
                // Nothing readable in the whole box (allowIncomplete with only
                // gaps); fall back to the save's own stamp before giving up.
                var levelData = save.Level?.Root?.Get("Data") as TagCompound;
                if (levelData?.Get("DataVersion") is not Tag stamp)
                    throw new NbtException(
                        "the export box contained no readable chunk and level.dat has "
                        + "no DataVersion; cannot tell which Minecraft this world is");
                schematic.DataVersion = Tag.AsInt(stamp);
            }
            return (schematic, result);
        }

        /// <summary>Copy the inclusive box between two corners into a .schem
        /// file. Corners are global block coordinates in any order. Block
        /// entities inside the box always travel with their blocks; the rest
        /// is opt-in. A box reaching into chunks the world has never
        /// generated — or has, but cannot be read — is refused unless
        /// allowIncomplete, because a schematic has no way to say "unknown"
        /// and would silently claim air.</summary>
        public static SchemExportResult ExportSchem(
            MinecraftSave save, (int X, int Y, int Z) corner1,
            (int X, int Y, int Z) corner2, string path,
            (int X, int Y, int Z)? origin = null, bool entities = false,
            bool biomes = false, int version = 3, string? dimension = null,
            bool allowIncomplete = false, bool keepLoaded = false)
        {
            var (schematic, result) = CopyOut(save, corner1, corner2, origin, entities,
                                              biomes, dimension, allowIncomplete,
                                              keepLoaded);
            schematic.Save(path, version);
            result.Path = path;
            return result;
        }

        internal static SchemImportResult PasteIn(
            MinecraftSave save, Schematic schematic, (int X, int Y, int Z) at,
            string anchor, bool entities, bool biomes, bool pasteAir, bool relight,
            bool heightmaps, string? dimension, bool prune, bool keepLoaded,
            int rotate, string? flip, bool pasteVoids = false)
        {
            if (anchor != "origin" && anchor != "min")
                throw new ArgumentException(
                    $"anchor must be 'origin' or 'min', got '{anchor}'", nameof(anchor));

            // Transforms come first, flip then rotate, so the composition is
            // fixed and documented rather than an accident of parameter order.
            // Both return copies; the caller's schematic is never altered.
            if (flip != null)
                schematic = schematic.Flipped(flip);
            if (rotate % 360 != 0)
                schematic = schematic.Rotated(rotate);

            // Content stamped by a newer Minecraft than the world's cannot be
            // right: the world's version does not know those blocks, and the
            // game will not data-fix them because the chunks claim to be
            // current already.
            var levelData = save.Level?.Root?.Get("Data") as TagCompound;
            int? worldVersion = levelData?.Get("DataVersion") is Tag stamp
                ? Tag.AsInt(stamp) : (int?)null;
            if (worldVersion != null && schematic.DataVersion > worldVersion)
                throw new NbtException(
                    $"this schematic was made by a newer Minecraft (DataVersion "
                    + $"{schematic.DataVersion}) than the world (DataVersion "
                    + $"{worldVersion}); its blocks may not exist there. Update the "
                    + "world first, or -- knowing the risk -- assign "
                    + $"schematic.DataVersion = {worldVersion} and paste again.");

            var dim = save.Dimension(dimension);
            var (ox, oy, oz) = anchor == "origin" ? schematic.Offset : (0, 0, 0);
            int px = at.X + ox, py = at.Y + oy, pz = at.Z + oz;
            var (width, height, length) = schematic.Size;
            int x1 = px + width - 1, y1 = py + height - 1, z1 = pz + length - 1;

            var result = new SchemImportResult
            {
                PlacedBox = ((px, py, pz), (x1, y1, z1)),
                TransformWarnings = new List<string>(schematic.TransformWarnings),
            };
            var airIds = new HashSet<int>();
            for (int i = 0; i < schematic.PaletteCount; i++)
            {
                if (Support.AirBlocks.Contains(schematic.PaletteEntry(i).Name))
                    airIds.Add(i);
            }
            // The cells a paste leaves as they stand: air when asked to, and
            // every structure_void unless asked not to.
            var skippedIds = new HashSet<int>();
            if (!pasteAir)
                skippedIds.UnionWith(airIds);
            if (!pasteVoids)
            {
                for (int i = 0; i < schematic.PaletteCount; i++)
                {
                    if (schematic.PaletteEntry(i).Name == Structure.StructureVoid)
                        skippedIds.Add(i);
                }
            }
            int[]? biomeCells = biomes && schematic.HasBiomes
                ? schematic.BiomeIndices : null;
            if (biomes && biomeCells == null)
            {
                if (schematic.Had2dBiomes)
                    throw new NbtException(
                        "this version-2 schematic carries only the old one-biome-per-"
                        + "column data, which cannot be applied to a world that stores "
                        + "biomes per 4x4x4 cell without inventing the vertical "
                        + "dimension. Re-export it as version 3.");
                throw new NbtException(
                    "biomes was requested, but this schematic carries no biome data");
            }

            for (int chunkX = px >> 4; chunkX <= (x1 >> 4); chunkX++)
            {
                for (int chunkZ = pz >> 4; chunkZ <= (z1 >> 4); chunkZ++)
                {
                    int baseX = chunkX * 16, baseZ = chunkZ * 16;
                    int lx0 = Math.Max(px, baseX) - baseX;
                    int lx1 = Math.Min(x1, baseX + 15) - baseX;
                    int lz0 = Math.Max(pz, baseZ) - baseZ;
                    int lz1 = Math.Min(z1, baseZ + 15) - baseZ;
                    int column = (lx1 - lx0 + 1) * (lz1 - lz0 + 1);

                    ChunkBlocks? accessor;
                    try
                    {
                        accessor = dim.WritableBlocks(chunkX, chunkZ);
                    }
                    catch (NbtException exc)
                    {
                        result.UnreadableChunks.Add(((chunkX, chunkZ), exc.Message));
                        result.BlocksOutOfRange += column * height;
                        continue;
                    }
                    if (accessor == null)
                    {
                        result.MissingChunks.Add((chunkX, chunkZ));
                        result.BlocksOutOfRange += column * height;
                        continue;
                    }
                    if (!accessor.Finished)
                    {
                        result.UnfinishedChunks.Add((chunkX, chunkZ));
                        result.BlocksOutOfRange += column * height;
                        if (!keepLoaded)
                            dim.Release(chunkX, chunkZ);
                        continue;
                    }

                    int written = PasteChunkBlocks(
                        schematic, accessor, (px, py, pz), (baseX, baseZ),
                        (lx0, lx1, lz0, lz1), airIds, skippedIds, prune, result);
                    if (biomeCells != null)
                        PasteChunkBiomes(schematic, accessor, (px, py, pz), y1,
                                         (baseX, baseZ), (lx0, lx1, lz0, lz1), result);
                    var (removed, added) = PasteChunkBlockEntities(
                        schematic, accessor, dim.Chunk(chunkX, chunkZ)!.Data!,
                        (px, py, pz), (baseX, baseZ), (lx0, lx1, lz0, lz1),
                        skippedIds);
                    result.BlockEntitiesRemoved += removed;
                    result.BlockEntitiesAdded += added;
                    if (written > 0 || removed > 0 || added > 0)
                        result.ChunksModified++;
                    if (heightmaps && written > 0)
                    {
                        accessor.RebuildHeightmaps();
                        result.HeightmapsRebuilt++;
                    }
                    if (!keepLoaded)
                        dim.Release(chunkX, chunkZ);
                }
            }

            if (entities && schematic.Entities.Count > 0)
                PasteEntities(save, dim, schematic, (px, py, pz), result);

            if (relight && result.ChunksModified > 0)
                result.Lighting = save.ResetLighting((px, py, pz), (x1, y1, z1),
                                                     dimension: dim.Name);
            return result;
        }

        private static int PasteChunkBlocks(
            Schematic schematic, ChunkBlocks accessor, (int X, int Y, int Z) place,
            (int X, int Z) chunkBase, (int X0, int X1, int Z0, int Z1) local,
            HashSet<int> airIds, HashSet<int> skippedIds, bool prune, SchemImportResult result)
        {
            var (px, py, pz) = place;
            var (baseX, baseZ) = chunkBase;
            var (lx0, lx1, lz0, lz1) = local;
            var (width, height, length) = schematic.Size;
            int y1 = py + height - 1;
            int[] cells = schematic.Indices();
            int column = (lx1 - lx0 + 1) * (lz1 - lz0 + 1);
            int written = 0;

            for (int sectionY = py >> 4; sectionY <= (y1 >> 4); sectionY++)
            {
                int baseY = sectionY * 16;
                int ly0 = Math.Max(py, baseY) - baseY;
                int ly1 = Math.Min(y1, baseY + 15) - baseY;
                accessor.Sections.TryGetValue(sectionY, out var section);
                var container = section?.Blocks;
                if (container == null)
                {
                    // Above or below what this chunk stores; nothing to write into.
                    result.BlocksOutOfRange += column * (ly1 - ly0 + 1);
                    continue;
                }

                bool whole = lx0 == 0 && lx1 == 15 && ly0 == 0 && ly1 == 15
                             && lz0 == 0 && lz1 == 15;
                if (whole && skippedIds.Count == 0)
                {
                    // The schematic owns the entire section: rebuild in one go.
                    var values = new List<BlockState>(4096);
                    for (int ly = 0; ly < 16; ly++)
                    {
                        int sy = baseY + ly - py;
                        for (int lz = 0; lz < 16; lz++)
                        {
                            int src = (sy * length + (baseZ + lz - pz)) * width
                                      + (baseX - px);
                            for (int lx = 0; lx < 16; lx++)
                                values.Add(schematic.PaletteEntry(cells[src + lx]));
                        }
                    }
                    container.SetAll(values);
                    result.SectionsReplaced++;
                    written += 4096;
                    continue;
                }

                var groups = new Dictionary<int, List<int>>();
                for (int ly = ly0; ly <= ly1; ly++)
                {
                    int sy = baseY + ly - py;
                    int row = ly * 256;
                    for (int lz = lz0; lz <= lz1; lz++)
                    {
                        int src = (sy * length + (baseZ + lz - pz)) * width + (baseX - px);
                        int rowZ = row + lz * 16;
                        for (int lx = lx0; lx <= lx1; lx++)
                        {
                            int slot = cells[src + lx];
                            if (skippedIds.Contains(slot))
                            {
                                if (airIds.Contains(slot))
                                    result.AirSkipped++;
                                else
                                    result.VoidsSkipped++;
                                continue;
                            }
                            if (!groups.TryGetValue(slot, out var positions))
                            {
                                positions = new List<int>();
                                groups[slot] = positions;
                            }
                            positions.Add(rowZ + lx);
                        }
                    }
                }
                foreach (var pair in groups)
                    written += container.SetMany(pair.Value,
                                                 schematic.PaletteEntry(pair.Key));
                if (groups.Count > 0 && prune)
                    container.Prune();
            }

            result.BlocksPasted += written;
            return written;
        }

        /// <summary>Apply the schematic's per-block biomes at the world's
        /// 4x4x4 grain. Every world cell the pasted box touches is set from
        /// the schematic's entry at the overlap's minimum corner. A box that
        /// starts mid-cell therefore recolors the whole cell — the format
        /// has no finer granularity, the same trade FillBiome documents.</summary>
        private static void PasteChunkBiomes(
            Schematic schematic, ChunkBlocks accessor, (int X, int Y, int Z) place,
            int y1, (int X, int Z) chunkBase, (int X0, int X1, int Z0, int Z1) local,
            SchemImportResult result)
        {
            var (px, py, pz) = place;
            var (baseX, baseZ) = chunkBase;
            var (lx0, lx1, lz0, lz1) = local;
            var (width, _, length) = schematic.Size;
            var biomePalette = schematic.BiomePalette!;
            int[] biomeCells = schematic.BiomeIndices!;

            for (int cellX = (baseX + lx0) >> 2; cellX <= ((baseX + lx1) >> 2); cellX++)
            {
                int sx = Math.Max(cellX * 4, baseX + lx0);
                for (int cellZ = (baseZ + lz0) >> 2; cellZ <= ((baseZ + lz1) >> 2); cellZ++)
                {
                    int sz = Math.Max(cellZ * 4, baseZ + lz0);
                    for (int cellY = py >> 2; cellY <= (y1 >> 2); cellY++)
                    {
                        int sy = Math.Max(cellY * 4, py);
                        string name = biomePalette[biomeCells[
                            ((sy - py) * length + (sz - pz)) * width + (sx - px)]];
                        try
                        {
                            accessor.SetBiome(sx - baseX, sy, sz - baseZ, name);
                        }
                        catch (ArgumentOutOfRangeException)
                        {
                            result.BiomeCellsOutOfRange++;
                            continue;
                        }
                        catch (NbtException)
                        {
                            result.BiomeCellsOutOfRange++;
                            continue;
                        }
                        result.BiomeCellsSet++;
                    }
                }
            }
        }

        private static (int Removed, int Added) PasteChunkBlockEntities(
            Schematic schematic, ChunkBlocks accessor, TagCompound chunkData,
            (int X, int Y, int Z) place, (int X, int Z) chunkBase,
            (int X0, int X1, int Z0, int Z1) local, HashSet<int> skippedIds)
        {
            var (px, py, pz) = place;
            var (baseX, baseZ) = chunkBase;
            var (lx0, lx1, lz0, lz1) = local;
            var (width, height, length) = schematic.Size;
            int y1 = py + height - 1;
            int[] cells = schematic.Indices();

            // Was the block at this world position written by the paste?
            bool Overwritten(int ex, int ey, int ez)
            {
                if (ex < px || ex > px + width - 1 || ey < py || ey > y1
                    || ez < pz || ez > pz + length - 1)
                    return false;
                accessor.Sections.TryGetValue(ey >> 4, out var section);
                if (section?.Blocks == null)
                    return false;  // out of the chunk's range; nothing was written
                if (skippedIds.Count == 0)
                    return true;
                int slot = cells[((ey - py) * length + (ez - pz)) * width + (ex - px)];
                return !skippedIds.Contains(slot);
            }

            int removed = 0;
            var stored = chunkData.Get("block_entities") as TagList;
            if (stored != null && stored.Count > 0)
            {
                var kept = new List<Tag>();
                foreach (var item in stored)
                {
                    var entry = (TagCompound)item;
                    if (Overwritten(Tag.AsInt(entry["x"]), Tag.AsInt(entry["y"]),
                                    Tag.AsInt(entry["z"])))
                        removed++;
                    else
                        kept.Add(item);
                }
                if (removed > 0)
                {
                    stored.Items.Clear();
                    stored.Items.AddRange(kept);
                }
            }

            int added = 0;
            foreach (var pair in schematic.BlockEntities.OrderBy(pair => pair.Key))
            {
                var (rx, ry, rz) = pair.Key;
                int ex = px + rx, ey = py + ry, ez = pz + rz;
                if (ex < baseX + lx0 || ex > baseX + lx1
                    || ez < baseZ + lz0 || ez > baseZ + lz1)
                    continue;  // a different chunk's business
                accessor.Sections.TryGetValue(ey >> 4, out var section);
                if (section?.Blocks == null)
                    continue;  // its block was not written; an orphan would linger
                if (skippedIds.Contains(cells[(ry * length + rz) * width + rx]))
                    continue;
                if (stored == null)
                {
                    stored = new TagList(TagId.Compound);
                    chunkData["block_entities"] = stored;
                }
                var entry = (TagCompound)Support.CopyTag(pair.Value);
                entry["x"] = new TagInt(ex);
                entry["y"] = new TagInt(ey);
                entry["z"] = new TagInt(ez);
                if (!entry.ContainsKey("keepPacked"))
                    entry["keepPacked"] = new TagByte(0);
                stored.Add(entry);
                added++;
            }
            return (removed, added);
        }

        private static void PasteEntities(
            MinecraftSave save, Dimension dim, Schematic schematic,
            (int X, int Y, int Z) place, SchemImportResult result)
        {
            var (px, py, pz) = place;

            // Fresh identities for everything pasted, remapped consistently so
            // the pasted copies still reference each other (leads, riders,
            // owners). The originals keep their UUIDs; a world cannot hold two
            // entities with one identity, and pasting the same file twice must
            // not try.
            var found = new List<int[]>();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var entity in schematic.Entities)
                Support.CollectUuids(entity, found, seen);
            var mapping = new Dictionary<string, int[]>(StringComparer.Ordinal);
            foreach (var old in found)
                mapping[Support.UuidKey(old)] = Tag.FreshUuid().Values;

            var levelData = save.Level?.Root?.Get("Data") as TagCompound;
            int fallbackVersion = levelData?.Get("DataVersion") is Tag stamp
                ? Tag.AsInt(stamp) : schematic.DataVersion;

            foreach (var entity in schematic.Entities)
            {
                string identity = entity.Get("id") is Tag id ? Tag.AsString(id) : "?";
                if (entity.Get("Pos") is not TagList pos || pos.Count != 3)
                {
                    result.EntitiesSkipped.Add((identity, (0.0, 0.0, 0.0)));
                    continue;
                }
                double ex = Tag.AsDouble(pos[0]) + px;
                double ey = Tag.AsDouble(pos[1]) + py;
                double ez = Tag.AsDouble(pos[2]) + pz;
                int chunkX = (int)Math.Floor(ex) >> 4, chunkZ = (int)Math.Floor(ez) >> 4;

                // An entity needs ground under it: no terrain chunk, no
                // paste; and one over an old chunk, which the library never
                // writes, is not pasted either -- the blocks under it were
                // refused the same way.
                if (dim.Chunk(chunkX, chunkZ) == null || ClosedTerrain(dim, chunkX, chunkZ))
                {
                    result.EntitiesSkipped.Add((identity, (ex, ey, ez)));
                    continue;
                }
                var chunk = dim.EntityChunk(chunkX, chunkZ, create: true,
                                            dataVersion: fallbackVersion)!;
                if (chunk.Data == null)
                {
                    result.EntitiesSkipped.Add((identity, (ex, ey, ez)));
                    continue;
                }
                if (chunk.Data.Get("Entities") is not TagList stored)
                {
                    stored = new TagList(TagId.Compound);
                    chunk.Data["Entities"] = stored;
                }

                var copy = (TagCompound)Support.CopyTag(entity);
                Support.ShiftEntity(copy, px, py, pz);
                Support.RemapUuids(copy, mapping);
                stored.Add(copy);
                result.EntitiesAdded++;
            }
        }

        /// <summary>Whether the terrain chunk here is one Minecraft 1.13 to 1.17
        /// saved. A chunk that cannot be read is not: its entities keep
        /// their own file, as they always have.</summary>
        private static bool ClosedTerrain(Dimension dim, int chunkX, int chunkZ)
        {
            try
            {
                var accessor = dim.Blocks(chunkX, chunkZ);
                return accessor != null && (accessor.Legacy || !accessor.Finished);
            }
            catch (NbtException)
            {
                return false;
            }
        }

        /// <summary>Paste a schematic into a save, in memory; save.Save()
        /// commits it. A schematic stamped by a newer Minecraft than the
        /// world is refused. Stale block entities at overwritten positions
        /// are removed, so pasting stone over a chest does not leave the
        /// chest's inventory haunting the chunk. A chunk the game has not
        /// finished generating is skipped and reported like one it never
        /// generated, since it generates over whatever is written there, and
        /// heightmaps rewrites the maps of every chunk whose blocks the paste
        /// moved.</summary>
        public static SchemImportResult ImportSchem(
            MinecraftSave save, Schematic source, (int X, int Y, int Z) at,
            string anchor = "origin", bool entities = true, bool biomes = false,
            bool pasteAir = true, bool relight = true, bool heightmaps = true,
            string? dimension = null, bool prune = true, bool keepLoaded = false,
            int rotate = 0, string? flip = null, bool pasteVoids = false) =>
            PasteIn(save, source, at, anchor, entities, biomes, pasteAir, relight,
                    heightmaps, dimension, prune, keepLoaded, rotate, flip, pasteVoids);

        public static SchemImportResult ImportSchem(
            MinecraftSave save, string path, (int X, int Y, int Z) at,
            string anchor = "origin", bool entities = true, bool biomes = false,
            bool pasteAir = true, bool relight = true, bool heightmaps = true,
            string? dimension = null, bool prune = true, bool keepLoaded = false,
            int rotate = 0, string? flip = null, bool pasteVoids = false) =>
            PasteIn(save, Schematic.Load(path), at, anchor, entities, biomes, pasteAir,
                    relight, heightmaps, dimension, prune, keepLoaded, rotate, flip,
                    pasteVoids);
    }
}
