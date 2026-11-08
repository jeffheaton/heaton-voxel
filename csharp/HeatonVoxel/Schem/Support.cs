// Twin of heaton_voxel/schem/support.py: shared plumbing for the schematic
// package — the format's varints, deep tag copies, entity translation, and
// identity handling.

using System;
using System.Collections.Generic;
using System.Linq;
using HeatonVoxel.Blocks;
using HeatonVoxel.Nbt;

namespace HeatonVoxel.Schem
{
    public static class Support
    {
        /// <summary>Blocks WorldEdit's "-a" mask treats as air: pasting with
        /// pasteAir=false skips these, so a schematic's empty space does not
        /// punch holes in terrain. It is the game's own air tag, the one set
        /// <see cref="ChunkBlocks.AirBlocks"/> keeps for the whole
        /// library.</summary>
        public static readonly IReadOnlyCollection<string> AirBlocks = ChunkBlocks.AirBlocks;

        /// <summary>Width/Height/Length are unsigned shorts in the format.</summary>
        internal const int MaxEdge = 0xFFFF;

        /// <summary>Decode the .schem format's LEB128-style varints: 7 data
        /// bits per byte, low group first, high bit means continue, at most
        /// 5 bytes per value.</summary>
        public static int[] ReadVarints(ReadOnlySpan<sbyte> raw, int expected, string what)
        {
            var values = new List<int>(expected);
            uint value = 0;
            int shift = 0;
            for (int position = 0; position < raw.Length; position++)
            {
                int b = raw[position] & 0xFF;
                value |= (uint)(b & 0x7F) << shift;
                if ((b & 0x80) != 0)
                {
                    shift += 7;
                    if (shift > 31)
                        throw new NbtException(
                            $"{what}: varint longer than 5 bytes at byte {position}");
                }
                else
                {
                    values.Add((int)value);
                    value = 0;
                    shift = 0;
                }
            }
            if (shift != 0)
                throw new NbtException($"{what}: truncated varint at end of array");
            if (values.Count != expected)
                throw new NbtException(
                    $"{what}: holds {values.Count} entries, expected {expected}");
            return values.ToArray();
        }

        public static sbyte[] WriteVarints(IEnumerable<int> values)
        {
            var output = new List<sbyte>();
            foreach (int item in values)
            {
                uint value = (uint)item;
                while (true)
                {
                    byte b = (byte)(value & 0x7F);
                    value >>= 7;
                    if (value != 0)
                        output.Add(unchecked((sbyte)(b | 0x80)));
                    else
                    {
                        output.Add(unchecked((sbyte)b));
                        break;
                    }
                }
            }
            return output.ToArray();
        }

        /// <summary>Encode 0..65535 into NBT's signed short, as the spec
        /// requires.</summary>
        internal static TagShort UnsignedShort(int value, string name)
        {
            if (value < 0 || value > MaxEdge)
                throw new NbtException(
                    $"{name} {value} does not fit the format's unsigned short");
            return new TagShort((short)(value > 0x7FFF ? value - 0x10000 : value));
        }

        /// <summary>Deep-copy a tag tree. Lists must keep their declared
        /// ElementType and arrays their own storage; scalars are immutable
        /// in effect and shared.</summary>
        internal static Tag CopyTag(Tag tag)
        {
            switch (tag)
            {
                case TagCompound compound:
                    {
                        var copy = new TagCompound();
                        foreach (var pair in compound)
                            copy[pair.Key] = CopyTag(pair.Value);
                        return copy;
                    }
                case TagList list:
                    {
                        var copy = new TagList(list.ElementType);
                        foreach (var item in list)
                            copy.Add(CopyTag(item));
                        return copy;
                    }
                case TagByteArray bytes:
                    return new TagByteArray((sbyte[])bytes.Values.Clone());
                case TagIntArray ints:
                    return new TagIntArray((int[])ints.Values.Clone());
                case TagLongArray longs:
                    return new TagLongArray((long[])longs.Values.Clone());
                default:
                    return tag;
            }
        }

        /// <summary>Record every UUID int-array in a tree (passengers
        /// included), in first-seen order.</summary>
        internal static void CollectUuids(Tag tag, List<int[]> found,
                                          HashSet<string> seen)
        {
            if (tag is TagCompound compound)
            {
                if (compound.Get("UUID") is TagIntArray stored
                    && stored.Values.Length == 4 && seen.Add(UuidKey(stored.Values)))
                    found.Add(stored.Values);
                foreach (var pair in compound)
                    CollectUuids(pair.Value, found, seen);
            }
            else if (tag is TagList list)
            {
                foreach (var item in list)
                    CollectUuids(item, found, seen);
            }
        }

        internal static string UuidKey(int[] words) =>
            string.Join(",", words.Select(word => word.ToString(
                System.Globalization.CultureInfo.InvariantCulture)));

        /// <summary>Replace any four-int array matching a remapped UUID, in
        /// place. Applied to EVERY int-array of that shape, not just UUID
        /// keys, so references between pasted entities — a leash, a vehicle,
        /// an owner — keep pointing at each other after both ends get fresh
        /// identities. A coincidental match against an unrelated 128-bit
        /// value is not a realistic concern.</summary>
        internal static void RemapUuids(Tag tag, Dictionary<string, int[]> mapping)
        {
            if (tag is TagCompound compound)
            {
                foreach (string key in compound.Keys.ToList())
                {
                    var value = compound[key];
                    if (value is TagIntArray array && array.Values.Length == 4)
                    {
                        if (mapping.TryGetValue(UuidKey(array.Values),
                                                out var replacement))
                            compound[key] = new TagIntArray((int[])replacement.Clone());
                    }
                    else
                    {
                        RemapUuids(value, mapping);
                    }
                }
            }
            else if (tag is TagList list)
            {
                foreach (var item in list)
                    RemapUuids(item, mapping);
            }
        }

        /// <summary>Translate an entity compound, in place.
        ///
        /// Pos always moves. The other stored absolute positions rewritten
        /// here are the ones current entities actually carry: the block_pos a
        /// painting, item frame or leash knot hangs on (TileX/Y/Z in chunks
        /// saved before the rename), a leashed mob's leash {X, Y, Z}, a
        /// sleeping_pos, and the same again for every passenger. Anything
        /// more exotic (villager brain memories, patrol targets) is copied
        /// verbatim.</summary>
        internal static void ShiftEntity(TagCompound tag, double dx, double dy, double dz)
        {
            if (tag.Get("Pos") is TagList pos && pos.Count == 3)
            {
                pos[0] = new TagDouble(Tag.AsDouble(pos[0]) + dx);
                pos[1] = new TagDouble(Tag.AsDouble(pos[1]) + dy);
                pos[2] = new TagDouble(Tag.AsDouble(pos[2]) + dz);
            }
            if (tag.Get("block_pos") is TagIntArray block && block.Values.Length == 3)
                tag["block_pos"] = new TagIntArray(new[]
                {
                    block.Values[0] + (int)dx,
                    block.Values[1] + (int)dy,
                    block.Values[2] + (int)dz,
                });
            foreach (var (name, delta) in new[]
                     { ("TileX", dx), ("TileY", dy), ("TileZ", dz) })
            {
                if (tag.ContainsKey(name))
                    tag[name] = new TagInt(Tag.AsInt(tag[name]) + (int)delta);
            }
            foreach (string key in new[] { "leash", "Leash" })
            {
                if (tag.Get(key) is TagCompound leash && leash.ContainsKey("X")
                    && leash.ContainsKey("Y") && leash.ContainsKey("Z"))
                {
                    leash["X"] = new TagInt(Tag.AsInt(leash["X"]) + (int)dx);
                    leash["Y"] = new TagInt(Tag.AsInt(leash["Y"]) + (int)dy);
                    leash["Z"] = new TagInt(Tag.AsInt(leash["Z"]) + (int)dz);
                }
            }
            if (tag.Get("sleeping_pos") is TagIntArray sleeping
                && sleeping.Values.Length == 3)
                tag["sleeping_pos"] = new TagIntArray(new[]
                {
                    sleeping.Values[0] + (int)dx,
                    sleeping.Values[1] + (int)dy,
                    sleeping.Values[2] + (int)dz,
                });
            if (tag.Get("Passengers") is TagList passengers)
            {
                foreach (var passenger in passengers)
                {
                    if (passenger is TagCompound compound)
                        ShiftEntity(compound, dx, dy, dz);
                }
            }
        }
    }
}
