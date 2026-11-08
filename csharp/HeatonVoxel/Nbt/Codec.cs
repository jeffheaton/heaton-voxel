// Twin of heaton_voxel/nbt.py (binary decoding / encoding). Big-endian
// throughout; every preservation rule the Python reference keeps is kept
// here: non-canonical string bytes, float NaN payloads, the declared type
// of empty lists, and the refusal rules (negative lengths, unknown tags,
// trailing bytes, the 512-level nesting limit).

using System;
using System.Buffers.Binary;
using System.IO;

namespace HeatonVoxel.Nbt
{
    public static class Codec
    {
        // Real Minecraft data nests around 10 levels; a corrupt or hostile
        // file must not be able to exhaust the stack.
        public const int MaxDepth = 512;

        // -- decoding ------------------------------------------------------

        public static (string RootName, TagCompound Root) Decode(byte[] data)
        {
            if (data.Length == 0)
                throw new NbtException("empty NBT payload");
            if (data[0] != (byte)TagId.Compound)
                throw new NbtException(
                    $"expected root {Tag.NameOf(TagId.Compound)}, got "
                    + Tag.NameOf((TagId)data[0]));
            int pos = 1;
            string name = ReadName(data, ref pos);
            var root = (TagCompound)ReadPayload(TagId.Compound, data, ref pos, 0);
            if (pos != data.Length)
                throw new NbtException($"{data.Length - pos} trailing bytes after root tag");
            return (name, root);
        }

        private static string ReadName(byte[] data, ref int pos)
        {
            ushort length = ReadUShort(data, ref pos);
            if (pos + length > data.Length)
                throw new NbtException($"truncated string at offset {pos}");
            // Names are decoded without raw-byte preservation; only string
            // VALUES keep non-canonical encodings, matching the reference.
            string name = ModifiedUtf8.Decode(data, pos, length);
            pos += length;
            return name;
        }

        private static Tag ReadPayload(TagId id, byte[] data, ref int pos, int depth)
        {
            switch (id)
            {
                case TagId.Byte:
                    Need(data, pos, 1);
                    return new TagByte(unchecked((sbyte)data[pos++]));
                case TagId.Short:
                    return new TagShort(ReadShort(data, ref pos));
                case TagId.Int:
                    return new TagInt(ReadInt(data, ref pos));
                case TagId.Long:
                    return new TagLong(ReadLong(data, ref pos));

                case TagId.Float:
                    {
                        Need(data, pos, 4);
                        var raw = new byte[4];
                        Array.Copy(data, pos, raw, 0, 4);
                        float value = BitConverter.Int32BitsToSingle(
                            BinaryPrimitives.ReadInt32BigEndian(raw));
                        var tag = new TagFloat(value);
                        Span<byte> repacked = stackalloc byte[4];
                        BinaryPrimitives.WriteInt32BigEndian(
                            repacked, BitConverter.SingleToInt32Bits(value));
                        if (!repacked.SequenceEqual(raw))
                            tag.RawBits = raw;
                        pos += 4;
                        return tag;
                    }
                case TagId.Double:
                    {
                        Need(data, pos, 8);
                        var raw = new byte[8];
                        Array.Copy(data, pos, raw, 0, 8);
                        double value = BitConverter.Int64BitsToDouble(
                            BinaryPrimitives.ReadInt64BigEndian(raw));
                        var tag = new TagDouble(value);
                        Span<byte> repacked = stackalloc byte[8];
                        BinaryPrimitives.WriteInt64BigEndian(
                            repacked, BitConverter.DoubleToInt64Bits(value));
                        if (!repacked.SequenceEqual(raw))
                            tag.RawBits = raw;
                        pos += 8;
                        return tag;
                    }

                case TagId.String:
                    {
                        ushort length = ReadUShort(data, ref pos);
                        if (pos + length > data.Length)
                            throw new NbtException($"truncated string at offset {pos}");
                        var raw = new byte[length];
                        Array.Copy(data, pos, raw, 0, length);
                        string text = ModifiedUtf8.Decode(data, pos, length);
                        pos += length;
                        var tag = new TagString(text);
                        var canonical = ModifiedUtf8.Encode(text);
                        if (!SpansEqual(canonical, raw))
                            tag.RawBytes = raw;  // keep the source so save() is exact
                        return tag;
                    }

                case TagId.ByteArray:
                    {
                        int count = ReadArrayHeader(data, ref pos, 1);
                        var values = new sbyte[count];
                        for (int i = 0; i < count; i++)
                            values[i] = unchecked((sbyte)data[pos + i]);
                        pos += count;
                        return new TagByteArray(values);
                    }
                case TagId.IntArray:
                    {
                        int count = ReadArrayHeader(data, ref pos, 4);
                        var values = new int[count];
                        for (int i = 0; i < count; i++)
                            values[i] = BinaryPrimitives.ReadInt32BigEndian(
                                data.AsSpan(pos + i * 4, 4));
                        pos += count * 4;
                        return new TagIntArray(values);
                    }
                case TagId.LongArray:
                    {
                        int count = ReadArrayHeader(data, ref pos, 8);
                        var values = new long[count];
                        for (int i = 0; i < count; i++)
                            values[i] = BinaryPrimitives.ReadInt64BigEndian(
                                data.AsSpan(pos + i * 8, 8));
                        pos += count * 8;
                        return new TagLongArray(values);
                    }

                case TagId.List:
                    {
                        if (depth >= MaxDepth)
                            throw new NbtException(
                                $"tag nesting exceeds the {MaxDepth}-level limit at "
                                + $"offset {pos}; the file is probably corrupt");
                        Need(data, pos, 5);
                        var elementType = (TagId)data[pos];
                        int count = ReadInt(data, ref Skip(ref pos, 1));
                        if (count < 0)
                            throw new NbtException(
                                $"TAG_List with negative length {count} at offset {pos - 4}");
                        if (elementType == TagId.End && count != 0)
                            throw new NbtException(
                                $"TAG_List of TAG_End with {count} entries at {pos}");
                        var list = new TagList(elementType);
                        for (int i = 0; i < count; i++)
                            list.Add(ReadPayload(elementType, data, ref pos, depth + 1));
                        return list;
                    }

                case TagId.Compound:
                    {
                        if (depth >= MaxDepth)
                            throw new NbtException(
                                $"tag nesting exceeds the {MaxDepth}-level limit at "
                                + $"offset {pos}; the file is probably corrupt");
                        var compound = new TagCompound();
                        while (true)
                        {
                            Need(data, pos, 1);
                            var childId = (TagId)data[pos++];
                            if (childId == TagId.End)
                                return compound;
                            string name = ReadName(data, ref pos);
                            compound[name] = ReadPayload(childId, data, ref pos, depth + 1);
                        }
                    }

                default:
                    throw new NbtException($"unknown tag type {(int)id} at offset {pos - 1}");
            }
        }

        // -- encoding ------------------------------------------------------

        public static byte[] Encode(TagCompound root, string rootName = "")
        {
            var buffer = new MemoryStream();
            buffer.WriteByte((byte)TagId.Compound);
            WriteName(buffer, rootName);
            WritePayload(buffer, root);
            return buffer.ToArray();
        }

        private static void WriteName(MemoryStream buffer, string name)
        {
            WriteStringBytes(buffer, ModifiedUtf8.Encode(name));
        }

        private static void WriteStringBytes(MemoryStream buffer, byte[] raw)
        {
            if (raw.Length > 0xFFFF)
                throw new NbtException(
                    $"string of {raw.Length} bytes exceeds the 65535-byte limit");
            Span<byte> header = stackalloc byte[2];
            BinaryPrimitives.WriteUInt16BigEndian(header, (ushort)raw.Length);
            buffer.Write(header);
            buffer.Write(raw, 0, raw.Length);
        }

        private static void WritePayload(MemoryStream buffer, Tag tag)
        {
            Span<byte> scratch = stackalloc byte[8];
            switch (tag)
            {
                case TagByte b:
                    buffer.WriteByte(unchecked((byte)b.Value));
                    break;
                case TagShort s:
                    BinaryPrimitives.WriteInt16BigEndian(scratch, s.Value);
                    buffer.Write(scratch.Slice(0, 2));
                    break;
                case TagInt i:
                    BinaryPrimitives.WriteInt32BigEndian(scratch, i.Value);
                    buffer.Write(scratch.Slice(0, 4));
                    break;
                case TagLong l:
                    BinaryPrimitives.WriteInt64BigEndian(scratch, l.Value);
                    buffer.Write(scratch.Slice(0, 8));
                    break;
                case TagFloat f:
                    if (f.RawBits != null)
                    {
                        buffer.Write(f.RawBits, 0, 4);
                    }
                    else
                    {
                        BinaryPrimitives.WriteInt32BigEndian(
                            scratch, BitConverter.SingleToInt32Bits(f.Value));
                        buffer.Write(scratch.Slice(0, 4));
                    }
                    break;
                case TagDouble d:
                    if (d.RawBits != null)
                    {
                        buffer.Write(d.RawBits, 0, 8);
                    }
                    else
                    {
                        BinaryPrimitives.WriteInt64BigEndian(
                            scratch, BitConverter.DoubleToInt64Bits(d.Value));
                        buffer.Write(scratch.Slice(0, 8));
                    }
                    break;
                case TagString str:
                    WriteStringBytes(buffer, str.RawBytes ?? ModifiedUtf8.Encode(str.Value));
                    break;
                case TagByteArray bytes:
                    BinaryPrimitives.WriteInt32BigEndian(scratch, bytes.Values.Length);
                    buffer.Write(scratch.Slice(0, 4));
                    foreach (sbyte value in bytes.Values)
                        buffer.WriteByte(unchecked((byte)value));
                    break;
                case TagIntArray ints:
                    BinaryPrimitives.WriteInt32BigEndian(scratch, ints.Values.Length);
                    buffer.Write(scratch.Slice(0, 4));
                    foreach (int value in ints.Values)
                    {
                        BinaryPrimitives.WriteInt32BigEndian(scratch, value);
                        buffer.Write(scratch.Slice(0, 4));
                    }
                    break;
                case TagLongArray longs:
                    BinaryPrimitives.WriteInt32BigEndian(scratch, longs.Values.Length);
                    buffer.Write(scratch.Slice(0, 4));
                    foreach (long value in longs.Values)
                    {
                        BinaryPrimitives.WriteInt64BigEndian(scratch, value);
                        buffer.Write(scratch.Slice(0, 8));
                    }
                    break;
                case TagList list:
                    {
                        // A non-empty list's stored type comes from its first
                        // item; the declared type only survives for empty lists.
                        TagId elementType = list.Count > 0 ? list.Items[0].Id : list.ElementType;
                        buffer.WriteByte((byte)elementType);
                        BinaryPrimitives.WriteInt32BigEndian(scratch, list.Count);
                        buffer.Write(scratch.Slice(0, 4));
                        for (int i = 0; i < list.Count; i++)
                        {
                            if (list.Items[i].Id != elementType)
                                throw new NbtException(
                                    $"TAG_List is declared as {Tag.NameOf(elementType)} but "
                                    + $"item {i} is {Tag.NameOf(list.Items[i].Id)}; NBT lists "
                                    + "cannot mix types");
                            WritePayload(buffer, list.Items[i]);
                        }
                        break;
                    }
                case TagCompound compound:
                    foreach (var pair in compound)
                    {
                        buffer.WriteByte((byte)pair.Value.Id);
                        WriteName(buffer, pair.Key);
                        WritePayload(buffer, pair.Value);
                    }
                    buffer.WriteByte((byte)TagId.End);
                    break;
                default:
                    throw new NbtException($"cannot serialize tag type {tag.Id}");
            }
        }

        // -- primitives ----------------------------------------------------

        private static void Need(byte[] data, int pos, int count)
        {
            if (pos + count > data.Length)
                throw new NbtException($"truncated tag payload at offset {pos}");
        }

        private static ref int Skip(ref int pos, int by)
        {
            pos += by;
            return ref pos;
        }

        private static ushort ReadUShort(byte[] data, ref int pos)
        {
            Need(data, pos, 2);
            ushort value = BinaryPrimitives.ReadUInt16BigEndian(data.AsSpan(pos, 2));
            pos += 2;
            return value;
        }

        private static short ReadShort(byte[] data, ref int pos)
        {
            Need(data, pos, 2);
            short value = BinaryPrimitives.ReadInt16BigEndian(data.AsSpan(pos, 2));
            pos += 2;
            return value;
        }

        private static int ReadInt(byte[] data, ref int pos)
        {
            Need(data, pos, 4);
            int value = BinaryPrimitives.ReadInt32BigEndian(data.AsSpan(pos, 4));
            pos += 4;
            return value;
        }

        private static long ReadLong(byte[] data, ref int pos)
        {
            Need(data, pos, 8);
            long value = BinaryPrimitives.ReadInt64BigEndian(data.AsSpan(pos, 8));
            pos += 8;
            return value;
        }

        private static int ReadArrayHeader(byte[] data, ref int pos, int itemSize)
        {
            int count = ReadInt(data, ref pos);
            if (count < 0)
                throw new NbtException($"negative array length {count} at offset {pos - 4}");
            if (pos + (long)count * itemSize > data.Length)
                throw new NbtException($"truncated array at offset {pos - 4}");
            return count;
        }

        private static bool SpansEqual(byte[] a, byte[] b)
        {
            return a.AsSpan().SequenceEqual(b);
        }
    }
}
