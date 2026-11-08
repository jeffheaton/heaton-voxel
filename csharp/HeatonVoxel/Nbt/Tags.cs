// Twin of heaton_voxel/nbt.py (tag classes). Python subclasses its value
// types; C# cannot subclass sealed built-ins, so tags are thin wrappers
// carrying a TagId and a Value, with the byte-preservation fields
// (RawBytes/RawBits) as proper members instead of Python's dynamic
// attributes. See PORTING.md, "The C# tag model".

using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace HeatonVoxel.Nbt
{
    /// <summary>Raised when a file cannot be parsed as NBT or as a region
    /// container — the twin of Python's NBTError.</summary>
    public class NbtException : Exception
    {
        public NbtException(string message) : base(message) { }
        public NbtException(string message, Exception inner) : base(message, inner) { }
    }

    public enum TagId : byte
    {
        End = 0,
        Byte = 1,
        Short = 2,
        Int = 3,
        Long = 4,
        Float = 5,
        Double = 6,
        ByteArray = 7,
        String = 8,
        List = 9,
        Compound = 10,
        IntArray = 11,
        LongArray = 12,
    }

    public abstract class Tag
    {
        public abstract TagId Id { get; }

        /// <summary>Python's ``int(tag)``: any integer tag as an int.</summary>
        public static long AsLong(Tag tag) => tag switch
        {
            TagByte b => b.Value,
            TagShort s => s.Value,
            TagInt i => i.Value,
            TagLong l => l.Value,
            _ => throw new NbtException($"{NameOf(tag.Id)} is not an integer tag"),
        };

        public static int AsInt(Tag tag) => checked((int)AsLong(tag));

        /// <summary>Python's ``float(tag)``: any numeric tag as a double.</summary>
        public static double AsDouble(Tag tag) => tag switch
        {
            TagFloat f => f.Value,
            TagDouble d => d.Value,
            _ => AsLong(tag),
        };

        /// <summary>Python's ``str(tag)`` for strings.</summary>
        public static string AsString(Tag tag) => tag switch
        {
            TagString s => s.Value,
            _ => throw new NbtException($"{NameOf(tag.Id)} is not a string tag"),
        };

        /// <summary>Python's ``str(tag)`` for any scalar tag, where
        /// <see cref="AsString"/> would refuse.
        ///
        /// Reading an id or a name out of storage that may be modded or
        /// damaged, the choice is between this and dropping the value: a
        /// numeric id rendered "42" keeps two entities distinct where an
        /// empty string would merge them. Non-scalar tags have no Python
        /// str() worth matching, so they render as their type name.</summary>
        public static string Text(Tag tag) => tag switch
        {
            TagString s => s.Value,
            TagByte b => b.Value.ToString(CultureInfo.InvariantCulture),
            TagShort s => s.Value.ToString(CultureInfo.InvariantCulture),
            TagInt i => i.Value.ToString(CultureInfo.InvariantCulture),
            TagLong l => l.Value.ToString(CultureInfo.InvariantCulture),
            TagFloat f => f.Value.ToString(CultureInfo.InvariantCulture),
            TagDouble d => d.Value.ToString(CultureInfo.InvariantCulture),
            _ => NameOf(tag.Id),
        };

        /// <summary>A new random UUID in Minecraft's NBT encoding: four
        /// signed 32-bit words, most significant first. Every entity written
        /// into a world needs one no other entity has — two entities sharing
        /// a UUID make one of them vanish — so anything that adds or copies
        /// entities mints fresh ones with this.</summary>
        public static TagIntArray FreshUuid()
        {
            byte[] bytes = Guid.NewGuid().ToByteArray();
            // Guid.ToByteArray is little-endian in its first three fields;
            // read the 16 bytes as big-endian words so the value is laid out
            // the way the game stores it.
            var words = new int[4];
            for (int i = 0; i < 4; i++)
            {
                words[i] = unchecked(
                    (bytes[i * 4] << 24) | (bytes[i * 4 + 1] << 16)
                    | (bytes[i * 4 + 2] << 8) | bytes[i * 4 + 3]);
            }
            return new TagIntArray(words);
        }

        public static string NameOf(TagId id) => id switch
        {
            TagId.End => "TAG_End",
            TagId.Byte => "TAG_Byte",
            TagId.Short => "TAG_Short",
            TagId.Int => "TAG_Int",
            TagId.Long => "TAG_Long",
            TagId.Float => "TAG_Float",
            TagId.Double => "TAG_Double",
            TagId.ByteArray => "TAG_Byte_Array",
            TagId.String => "TAG_String",
            TagId.List => "TAG_List",
            TagId.Compound => "TAG_Compound",
            TagId.IntArray => "TAG_Int_Array",
            TagId.LongArray => "TAG_Long_Array",
            _ => $"TAG_Unknown({(int)id})",
        };
    }

    public sealed class TagByte : Tag
    {
        public override TagId Id => TagId.Byte;
        public sbyte Value;
        public TagByte(sbyte value) { Value = value; }
    }

    public sealed class TagShort : Tag
    {
        public override TagId Id => TagId.Short;
        public short Value;
        public TagShort(short value) { Value = value; }
    }

    public sealed class TagInt : Tag
    {
        public override TagId Id => TagId.Int;
        public int Value;
        public TagInt(int value) { Value = value; }
    }

    public sealed class TagLong : Tag
    {
        public override TagId Id => TagId.Long;
        public long Value;
        public TagLong(long value) { Value = value; }
    }

    /// <summary>32-bit float. RawBits holds the original four bytes when the
    /// IEEE-754 pattern would not survive re-packing (a NaN payload);
    /// compare floats by bits, never by value.</summary>
    public sealed class TagFloat : Tag
    {
        public override TagId Id => TagId.Float;
        public float Value;
        public byte[]? RawBits;
        public TagFloat(float value) { Value = value; }
    }

    /// <summary>64-bit float; see <see cref="TagFloat"/> regarding RawBits.</summary>
    public sealed class TagDouble : Tag
    {
        public override TagId Id => TagId.Double;
        public double Value;
        public byte[]? RawBits;
        public TagDouble(double value) { Value = value; }
    }

    /// <summary>UTF-16 text. RawBytes holds the source bytes when they are a
    /// non-canonical modified-UTF-8 encoding that re-encoding would
    /// normalize away; the writer re-emits them so round trips stay
    /// byte-exact.</summary>
    public sealed class TagString : Tag
    {
        public override TagId Id => TagId.String;
        public string Value;
        public byte[]? RawBytes;
        public TagString(string value) { Value = value; }
    }

    public sealed class TagByteArray : Tag
    {
        public override TagId Id => TagId.ByteArray;
        public sbyte[] Values;
        public TagByteArray(sbyte[] values) { Values = values; }
    }

    public sealed class TagIntArray : Tag
    {
        public override TagId Id => TagId.IntArray;
        public int[] Values;
        public TagIntArray(int[] values) { Values = values; }
    }

    public sealed class TagLongArray : Tag
    {
        public override TagId Id => TagId.LongArray;
        public long[] Values;
        public TagLongArray(long[] values) { Values = values; }
    }

    /// <summary>Homogeneous sequence of unnamed tags. ElementType records
    /// the declared type, which matters for empty lists — the declaration is
    /// the only surviving evidence of what the list held.</summary>
    public sealed class TagList : Tag, IEnumerable<Tag>
    {
        public override TagId Id => TagId.List;
        public TagId ElementType;
        public readonly List<Tag> Items;

        public TagList(TagId elementType = TagId.End)
        {
            ElementType = elementType;
            Items = new List<Tag>();
        }

        public int Count => Items.Count;
        public Tag this[int index] { get => Items[index]; set => Items[index] = value; }
        public void Add(Tag item) => Items.Add(item);
        public IEnumerator<Tag> GetEnumerator() => Items.GetEnumerator();
        IEnumerator IEnumerable.GetEnumerator() => Items.GetEnumerator();
    }

    /// <summary>Mapping of name to tag, preserving file order.
    ///
    /// Insertion order is data: byte-exact round-tripping requires children
    /// to re-encode in the order they were read, and re-assigning an
    /// existing key keeps its original position, exactly like a Python
    /// dict. This is why a plain Dictionary cannot back a compound.</summary>
    public sealed class TagCompound : Tag, IEnumerable<KeyValuePair<string, Tag>>
    {
        public override TagId Id => TagId.Compound;

        private readonly List<string> _order = new List<string>();
        private readonly Dictionary<string, Tag> _map =
            new Dictionary<string, Tag>(StringComparer.Ordinal);

        public int Count => _order.Count;
        public IReadOnlyList<string> Keys => _order;

        public Tag this[string key]
        {
            get => _map[key];
            set
            {
                if (!_map.ContainsKey(key))
                    _order.Add(key);
                _map[key] = value;
            }
        }

        public bool ContainsKey(string key) => _map.ContainsKey(key);

        public bool TryGetValue(string key, out Tag value) =>
            _map.TryGetValue(key, out value!);

        public Tag? Get(string key) =>
            _map.TryGetValue(key, out var value) ? value : null;

        public bool Remove(string key)
        {
            if (!_map.Remove(key))
                return false;
            _order.Remove(key);
            return true;
        }

        public IEnumerator<KeyValuePair<string, Tag>> GetEnumerator()
        {
            foreach (var key in _order)
                yield return new KeyValuePair<string, Tag>(key, _map[key]);
        }

        IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
    }
}
