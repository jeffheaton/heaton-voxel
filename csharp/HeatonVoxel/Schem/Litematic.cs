// Twin of heaton_voxel/schem/litematic.py: Litematica schematics read into
// and written from a Schematic. The module docstring there states the
// format and what reading it takes: a region's Size can be negative and
// counts away from its Position, blocks and tile entities count from the
// region's minimum corner and entities from its Position, cells pack tightly
// across longs at max(2, bits(palette - 1)) bits, x fastest then z then y,
// and air is the palette's first entry.

using System;
using System.Collections.Generic;
using System.Linq;
using HeatonVoxel.Blocks;
using HeatonVoxel.Nbt;

namespace HeatonVoxel.Schem
{
    public static class Litematic
    {
        /// <summary>The container version written; version 5, the first of
        /// block states, is the oldest read.</summary>
        public const int LitematicVersion = 7;
        public const int LitematicSubVersion = 1;
        private const int OldestVersion = 5;

        /// <summary>Is this the root of a Litematica schematic?</summary>
        public static bool IsLitematic(TagCompound root) =>
            root.ContainsKey("Regions") && root.ContainsKey("Metadata") && root.ContainsKey("Version");

        /// <summary>LitematicaBlockStateContainer's bits per cell.</summary>
        internal static int Bits(int paletteSize)
        {
            int bits = 0;
            for (int value = paletteSize - 1; value > 0; value >>= 1)
                bits++;
            return Math.Max(2, bits);
        }

        private static (int X, int Y, int Z) Vector(Tag? tag, string what)
        {
            if (!(tag is TagCompound c) || !c.ContainsKey("x") || !c.ContainsKey("y") || !c.ContainsKey("z"))
                throw new NbtException($"{what}: expected {{x, y, z}}");
            return (Tag.AsInt(c["x"]), Tag.AsInt(c["y"]), Tag.AsInt(c["z"]));
        }

        /// <summary>count values of bits bits each packed end to end across
        /// a long array, straddling two longs on a seam --
        /// LitematicaBitArray.</summary>
        public static int[] UnpackTight(long[] longs, int count, int bits)
        {
            long needed = ((long)count * bits + 63) / 64;
            if (longs.Length < needed)
                throw new NbtException($"block data holds {longs.Length} longs, {needed} needed");
            var output = new int[count];
            ulong mask = (1UL << bits) - 1;
            for (int index = 0; index < count; index++)
            {
                long start = (long)index * bits;
                int word = (int)(start >> 6), shift = (int)(start & 63);
                ulong value = (ulong)longs[word] >> shift;
                if (shift + bits > 64)
                    value |= (ulong)longs[word + 1] << (64 - shift);
                output[index] = (int)(value & mask);
            }
            return output;
        }

        /// <summary>The inverse of UnpackTight.</summary>
        public static TagLongArray PackTight(IReadOnlyList<int> values, int bits)
        {
            var words = new ulong[((long)values.Count * bits + 63) / 64];
            for (int index = 0; index < values.Count; index++)
            {
                long start = (long)index * bits;
                int word = (int)(start >> 6), shift = (int)(start & 63);
                ulong value = (ulong)values[index];
                words[word] |= value << shift;
                if (shift + bits > 64)
                    words[word + 1] |= value >> (64 - shift);
            }
            return new TagLongArray(words.Select(w => unchecked((long)w)).ToArray());
        }

        private static ((int X, int Y, int Z) Low, (int X, int Y, int Z) Dims) RegionBox(
            (int X, int Y, int Z) position, (int X, int Y, int Z) size, string what)
        {
            int[] start = { position.X, position.Y, position.Z };
            int[] count = { size.X, size.Y, size.Z };
            var low = new int[3];
            var dims = new int[3];
            for (int axis = 0; axis < 3; axis++)
            {
                if (count[axis] == 0)
                    throw new NbtException($"{what}: a region of size 0 along axis {"xyz"[axis]}");
                int end = start[axis] + (count[axis] > 0 ? count[axis] - 1 : count[axis] + 1);
                low[axis] = Math.Min(start[axis], end);
                dims[axis] = Math.Abs(count[axis]);
            }
            return ((low[0], low[1], low[2]), (dims[0], dims[1], dims[2]));
        }

        /// <summary>The Schematic a Litematica file holds, its regions in
        /// one box -- the twin of read_litematic.</summary>
        public static Schematic ReadLitematic(TagCompound root, string path)
        {
            int version = Tag.AsInt(root["Version"]);
            if (version < OldestVersion || version > LitematicVersion)
                throw new NbtException(
                    $"'{path}' is Litematica version {version}; versions {OldestVersion} "
                    + $"to {LitematicVersion} store block states");
            if (!root.ContainsKey("MinecraftDataVersion"))
                throw new NbtException(
                    $"'{path}' has no MinecraftDataVersion, so there is no telling which "
                    + "Minecraft its block names belong to; refusing to guess.");
            if (!(root["Regions"] is TagCompound regions) || regions.Count == 0)
                throw new NbtException($"'{path}' holds no regions");
            var boxes = new List<(string Name, (int X, int Y, int Z) Position,
                                   (int X, int Y, int Z) Low, (int X, int Y, int Z) Dims)>();
            foreach (var pair in regions)
            {
                string what = $"'{path}' region '{pair.Key}'";
                var region = (TagCompound)pair.Value;
                var position = Vector(region.Get("Position"), $"{what} Position");
                var size = Vector(region.Get("Size"), $"{what} Size");
                var (low0, dims0) = RegionBox(position, size, what);
                boxes.Add((pair.Key, position, low0, dims0));
            }
            var low = (X: boxes.Min(b => b.Low.X), Y: boxes.Min(b => b.Low.Y), Z: boxes.Min(b => b.Low.Z));
            var high = (X: boxes.Max(b => b.Low.X + b.Dims.X - 1), Y: boxes.Max(b => b.Low.Y + b.Dims.Y - 1),
                        Z: boxes.Max(b => b.Low.Z + b.Dims.Z - 1));
            var schematic = new Schematic(high.X - low.X + 1, high.Y - low.Y + 1, high.Z - low.Z + 1,
                                          Tag.AsInt(root["MinecraftDataVersion"]), (low.X, low.Y, low.Z))
            {
                SourceFormat = "litematic",
                SourceVersion = version,
            };
            schematic.Metadata = root.Get("Metadata") is TagCompound metadata
                ? (TagCompound)Support.CopyTag(metadata) : null;
            int voidSlot = schematic.InternState(new BlockState(Structure.StructureVoid));
            int[] cells = schematic.Indices();
            Array.Fill(cells, voidSlot);

            foreach (var box in boxes)
            {
                string what = $"'{path}' region '{box.Name}'";
                var region = (TagCompound)regions[box.Name];
                int ox = box.Low.X - low.X, oy = box.Low.Y - low.Y, oz = box.Low.Z - low.Z;
                var (sx, sy, sz) = box.Dims;
                if (!(region.Get("BlockStatePalette") is TagList palette) || palette.Count == 0)
                    throw new NbtException($"{what}: no BlockStatePalette");
                var slots = new List<int>();
                foreach (var entry in palette)
                    slots.Add(schematic.InternState(BlockState.FromNbt(entry)));
                long[] longs = region.Get("BlockStates") is TagLongArray stored
                    ? stored.Values : new long[0];
                int[] values = UnpackTight(longs, sx * sy * sz, Bits(slots.Count));
                int index = 0;
                for (int y = 0; y < sy; y++)
                {
                    for (int z = 0; z < sz; z++)
                    {
                        for (int x = 0; x < sx; x++)
                        {
                            int value = values[index++];
                            if (value >= slots.Count)
                                throw new NbtException(
                                    $"{what}: block data names palette entry {value} of {slots.Count}");
                            cells[schematic.CellIndex(ox + x, oy + y, oz + z)] = slots[value];
                        }
                    }
                }
                if (region.Get("TileEntities") is TagList tiles)
                {
                    foreach (var item in tiles)
                    {
                        var entry = (TagCompound)item;
                        int Coordinate(string key) => entry.Get(key) is Tag t ? Tag.AsInt(t) : 0;
                        int x = Coordinate("x"), y = Coordinate("y"), z = Coordinate("z");
                        var data = new TagCompound();
                        foreach (var field in entry)
                        {
                            if (field.Key != "x" && field.Key != "y" && field.Key != "z")
                                data[field.Key] = Support.CopyTag(field.Value);
                        }
                        if (!data.ContainsKey("id"))
                            throw new NbtException($"{what}: the tile entity at ({x}, {y}, {z}) has no id");
                        var cell = (ox + x, oy + y, oz + z);
                        schematic.CellIndex(cell.Item1, cell.Item2, cell.Item3);
                        schematic.BlockEntities[cell] = data;
                    }
                }
                if (region.Get("Entities") is TagList entities)
                {
                    foreach (var item in entities)
                    {
                        var entry = (TagCompound)item;
                        if (!entry.ContainsKey("id") || !entry.ContainsKey("Pos"))
                            throw new NbtException($"{what}: an entity with no id or no Pos");
                        var data = (TagCompound)Support.CopyTag(entry);
                        // entities count from the region's Position; the
                        // schematic's from its own minimum corner
                        Support.ShiftEntity(data, box.Position.X - low.X, box.Position.Y - low.Y,
                                            box.Position.Z - low.Z);
                        schematic.Entities.Add(data);
                    }
                }
            }
            return schematic;
        }

        private static TagCompound VectorTag(int x, int y, int z) =>
            new TagCompound { ["x"] = new TagInt(x), ["y"] = new TagInt(y), ["z"] = new TagInt(z) };

        /// <summary>The Litematica file a schematic is: one region at its
        /// offset -- the twin of litematic_root.</summary>
        public static TagCompound LitematicRoot(Schematic schematic, string region = "Unnamed")
        {
            var (width, height, length) = schematic.Size;
            int[] cells = schematic.Indices();
            var air = new BlockState("minecraft:air");
            var order = new Dictionary<BlockState, int> { [air] = 0 };
            var palette = new TagList(TagId.Compound);
            palette.Add(Structure.FullStateTag(air, false));
            var values = new List<int>(width * height * length);
            int nonAir = 0;
            for (int y = 0; y < height; y++)
            {
                for (int z = 0; z < length; z++)
                {
                    for (int x = 0; x < width; x++)
                    {
                        var state = schematic.PaletteEntry(cells[schematic.CellIndex(x, y, z)]);
                        if (state.Name != "minecraft:air")
                            nonAir++;
                        if (!order.TryGetValue(state, out int id))
                        {
                            id = order.Count;
                            order[state] = id;
                            palette.Add(Structure.FullStateTag(state, false));
                        }
                        values.Add(id);
                    }
                }
            }
            var tiles = new TagList(schematic.BlockEntities.Count > 0 ? TagId.Compound : TagId.End);
            foreach (var pair in schematic.BlockEntities.OrderBy(pair => pair.Key))
            {
                var entry = (TagCompound)Support.CopyTag(pair.Value);
                entry["x"] = new TagInt(pair.Key.X);
                entry["y"] = new TagInt(pair.Key.Y);
                entry["z"] = new TagInt(pair.Key.Z);
                tiles.Add(entry);
            }
            var entities = new TagList(schematic.Entities.Count > 0 ? TagId.Compound : TagId.End);
            foreach (var data in schematic.Entities)
                entities.Add(Support.CopyTag(data));

            var given = schematic.Metadata ?? new TagCompound();
            var metadata = new TagCompound();
            foreach (string key in new[] { "Name", "Author", "Description" })
                metadata[key] = new TagString(given.Get(key) is Tag t ? Tag.Text(t) : "");
            metadata["RegionCount"] = new TagInt(1);
            metadata["TotalVolume"] = new TagInt(width * height * length);
            metadata["TotalBlocks"] = new TagInt(nonAir);
            // the schematic's own times, or 0: never the clock, so a
            // schematic writes the same bytes every time it is written
            foreach (string key in new[] { "TimeCreated", "TimeModified" })
                metadata[key] = new TagLong(given.Get(key) is Tag t ? Tag.AsLong(t) : 0);
            metadata["EnclosingSize"] = VectorTag(width, height, length);
            if (given.Get("PreviewImageData") is Tag preview)
                metadata["PreviewImageData"] = Support.CopyTag(preview);

            var body = new TagCompound
            {
                ["BlockStatePalette"] = palette,
                ["BlockStates"] = PackTight(values, Bits(order.Count)),
                ["TileEntities"] = tiles,
                // empty, as the mod writes them for a region taken from a
                // world: this library carries no pending ticks
                ["PendingBlockTicks"] = new TagList(TagId.End),
                ["PendingFluidTicks"] = new TagList(TagId.End),
                ["Entities"] = entities,
                ["Position"] = VectorTag(schematic.Offset.X, schematic.Offset.Y, schematic.Offset.Z),
                ["Size"] = VectorTag(width, height, length),
            };
            return new TagCompound
            {
                ["MinecraftDataVersion"] = new TagInt(schematic.DataVersion),
                ["Version"] = new TagInt(LitematicVersion),
                ["SubVersion"] = new TagInt(LitematicSubVersion),
                ["Metadata"] = metadata,
                ["Regions"] = new TagCompound { [region] = body },
            };
        }
    }
}
