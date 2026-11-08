// Twin of heaton_voxel/schem/structure.py: vanilla structure files -- the
// .nbt a structure block saves -- read into and written from a Schematic.
// The module docstring there states the format: a cell the file leaves out
// reads as structure_void, which paste skips, and a structure_void cell is
// written out of the list; states carry every property, {id, properties}
// from 26.3 and {Name, Properties} before; the first of several palettes is
// read.

using System;
using System.Collections.Generic;
using HeatonVoxel.Blocks;
using HeatonVoxel.Nbt;

namespace HeatonVoxel.Schem
{
    public static class Structure
    {
        /// <summary>The block a structure saves as "leave this cell
        /// alone": never a block in the file, a cell left out of it.</summary>
        public const string StructureVoid = "minecraft:structure_void";

        /// <summary>Is this the root of a vanilla structure file?</summary>
        public static bool IsStructure(TagCompound root) =>
            root.ContainsKey("size") && root.ContainsKey("blocks")
            && (root.ContainsKey("palette") || root.ContainsKey("palettes"));

        /// <summary>A block state as NbtUtils.writeBlockState writes it: the
        /// id and every property in name order, defaults filled in --
        /// {id, properties} when modern, else {Name, Properties}.</summary>
        public static TagCompound FullStateTag(BlockState state, bool modern)
        {
            var properties = new SortedDictionary<string, string>(StringComparer.Ordinal);
            foreach (var pair in BlockDefaults.DefaultProperties(state.Name))
                properties[pair.Key] = pair.Value;
            foreach (var pair in state.Properties)
                properties[pair.Key] = pair.Value;
            var tag = new TagCompound { [modern ? "id" : "Name"] = new TagString(state.Name) };
            if (properties.Count > 0)
            {
                var props = new TagCompound();
                foreach (var pair in properties)
                    props[pair.Key] = new TagString(pair.Value);
                tag[modern ? "properties" : "Properties"] = props;
            }
            return tag;
        }

        private static int[] Ints(Tag? tag, int count, string what)
        {
            if (!(tag is TagList list) || list.Count != count)
                throw new NbtException($"{what}: expected a list of {count} numbers");
            var values = new int[count];
            for (int i = 0; i < count; i++)
                values[i] = (int)Tag.AsDouble(list[i]);
            return values;
        }

        /// <summary>The Schematic a structure file holds -- the twin of
        /// read_structure.</summary>
        public static Schematic ReadStructure(TagCompound root, string path)
        {
            if (!root.ContainsKey("DataVersion"))
                throw new NbtException(
                    $"'{path}' has no DataVersion, so there is no telling which "
                    + "Minecraft its block names belong to; refusing to guess.");
            int[] size = Ints(root.Get("size"), 3, $"'{path}' size");
            var schematic = new Schematic(size[0], size[1], size[2], Tag.AsInt(root["DataVersion"]))
            {
                SourceFormat = "structure",
            };

            TagList paletteTag;
            if (root.Get("palettes") is Tag palettesTag)
            {
                if (!(palettesTag is TagList palettes) || palettes.Count == 0)
                    throw new NbtException($"'{path}': 'palettes' holds no palette");
                paletteTag = (TagList)palettes[0];
                schematic.SourcePalettes = palettes.Count;
            }
            else
            {
                paletteTag = (TagList)root["palette"];
                schematic.SourcePalettes = 1;
            }
            var states = new List<BlockState>();
            foreach (var entry in paletteTag)
                states.Add(BlockState.FromNbt(entry));

            int voidSlot = schematic.InternState(new BlockState(StructureVoid));
            int[] cells = schematic.Indices();
            Array.Fill(cells, voidSlot);
            var slots = new List<int>();
            foreach (var state in states)
                slots.Add(schematic.InternState(state));
            foreach (var item in (TagList)root["blocks"])
            {
                var entry = (TagCompound)item;
                int[] pos = Ints(entry.Get("pos"), 3, $"'{path}' block pos");
                int index = entry.Get("state") is Tag stateTag ? Tag.AsInt(stateTag) : -1;
                if (index < 0 || index >= slots.Count)
                    throw new NbtException(
                        $"'{path}': block at ({pos[0]}, {pos[1]}, {pos[2]}) names palette "
                        + $"entry {index} of {slots.Count}");
                cells[schematic.CellIndex(pos[0], pos[1], pos[2])] = slots[index];
                if (entry.Get("nbt") is TagCompound data)
                {
                    if (!data.ContainsKey("id"))
                        throw new NbtException(
                            $"'{path}': the block entity at ({pos[0]}, {pos[1]}, {pos[2]}) has no id");
                    schematic.BlockEntities[(pos[0], pos[1], pos[2])] = (TagCompound)Support.CopyTag(data);
                }
            }
            if (root.Get("entities") is TagList entities)
            {
                foreach (var item in entities)
                {
                    var entry = (TagCompound)item;
                    if (!(entry.Get("pos") is TagList pos) || pos.Count != 3)
                        throw new NbtException($"'{path}': an entity without a 3-number pos");
                    if (!(entry.Get("nbt") is TagCompound nbt) || !nbt.ContainsKey("id"))
                        throw new NbtException($"'{path}': an entity has no id");
                    var data = (TagCompound)Support.CopyTag(nbt);
                    var at = new TagList(TagId.Double);
                    foreach (var v in pos)
                        at.Add(new TagDouble(Tag.AsDouble(v)));
                    data["Pos"] = at;
                    schematic.Entities.Add(data);
                }
            }
            return schematic;
        }

        /// <summary>The structure file a schematic is -- the twin of
        /// structure_root: every cell but its structure_void ones listed,
        /// those without a block entity first and each run in y, x, z
        /// order, as the game lists them.</summary>
        public static TagCompound StructureRoot(Schematic schematic)
        {
            bool modern = schematic.DataVersion >= ChunkBlocks.ModernStateDataVersion;
            var (width, height, length) = schematic.Size;
            int[] cells = schematic.Indices();
            var order = new Dictionary<int, int>();
            var palette = new TagList(TagId.Compound);
            var plain = new List<Tag>();
            var holding = new List<Tag>();
            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    for (int z = 0; z < length; z++)
                    {
                        int slot = cells[schematic.CellIndex(x, y, z)];
                        var state = schematic.PaletteEntry(slot);
                        if (state.Name == StructureVoid)
                            continue;
                        if (!order.ContainsKey(slot))
                        {
                            order[slot] = order.Count;
                            palette.Add(FullStateTag(state, modern));
                        }
                        var pos = new TagList(TagId.Int);
                        pos.Add(new TagInt(x));
                        pos.Add(new TagInt(y));
                        pos.Add(new TagInt(z));
                        var entry = new TagCompound { ["pos"] = pos, ["state"] = new TagInt(order[slot]) };
                        if (schematic.BlockEntities.TryGetValue((x, y, z), out var data))
                        {
                            entry["nbt"] = Support.CopyTag(data);
                            holding.Add(entry);
                        }
                        else
                        {
                            plain.Add(entry);
                        }
                    }
                }
            }
            var blocks = new TagList(plain.Count + holding.Count > 0 ? TagId.Compound : TagId.End);
            blocks.Items.AddRange(plain);
            blocks.Items.AddRange(holding);
            var entities = new TagList(schematic.Entities.Count > 0 ? TagId.Compound : TagId.End);
            foreach (var data in schematic.Entities)
            {
                if (!data.ContainsKey("id"))
                    throw new NbtException("an entity in this schematic has no 'id'");
                var source = (TagList)data["Pos"];
                var pos = new TagList(TagId.Double);
                var blockPos = new TagList(TagId.Int);
                foreach (var v in source)
                {
                    double value = Tag.AsDouble(v);
                    pos.Add(new TagDouble(value));
                    blockPos.Add(new TagInt((int)Math.Floor(value)));
                }
                entities.Add(new TagCompound
                {
                    ["pos"] = pos,
                    ["blockPos"] = blockPos,
                    ["nbt"] = Support.CopyTag(data),
                });
            }
            var sizeTag = new TagList(TagId.Int);
            sizeTag.Add(new TagInt(width));
            sizeTag.Add(new TagInt(height));
            sizeTag.Add(new TagInt(length));
            return new TagCompound
            {
                ["size"] = sizeTag,
                ["entities"] = entities,
                ["blocks"] = blocks,
                ["palette"] = palette,
                ["DataVersion"] = new TagInt(schematic.DataVersion),
            };
        }
    }
}
