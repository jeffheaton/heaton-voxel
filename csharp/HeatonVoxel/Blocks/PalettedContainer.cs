// Twin of heaton_voxel/blocks.py (PalettedContainer). A palette plus a
// bit-packed index array, as stored in a section; backs both block_states
// (4096 entries, minimum 4 bits) and biomes (64 entries, minimum 1 bit).
// Writes go straight through into the underlying NBT.

using System;
using System.Collections.Generic;
using System.Linq;
using HeatonVoxel.Nbt;

namespace HeatonVoxel.Blocks
{
    /// <summary>Entry codec: how palette entries of T appear as tags.
    /// <c>modern</c> asks for the 26.3 block-state form; a biome codec has
    /// no such form and ignores it.</summary>
    public interface IPaletteCodec<T> where T : notnull
    {
        T Decode(Tag entry);
        (T Key, Tag Entry) Encode(T value, bool modern);
    }

    public sealed class BlockStateCodec : IPaletteCodec<BlockState>
    {
        public static readonly BlockStateCodec Instance = new BlockStateCodec();
        public BlockState Decode(Tag entry) => BlockState.FromNbt(entry);

        public (BlockState, Tag) Encode(BlockState value, bool modern)
        {
            if (modern)
            {
                // The 26.3 codec's identity is the state with its block's
                // defaults filled in: water and water[level=0] are one
                // palette entry, as they are one block to the game.
                value = value.Canonical();
            }
            return (value, value.ToNbt(modern));
        }
    }

    public sealed class BiomeCodec : IPaletteCodec<string>
    {
        public static readonly BiomeCodec Instance = new BiomeCodec();
        public string Decode(Tag entry) => ((TagString)entry).Value;
        public (string, Tag) Encode(string value, bool modern) => (value, new TagString(value));
    }

    public sealed class PalettedContainer<T> where T : notnull
    {
        private readonly TagCompound _tag;
        private readonly int _count;
        private readonly int _minBits;
        private readonly IPaletteCodec<T> _codec;
        private readonly TagId _elementType;
        private readonly bool _modern;
        // Why this container refuses every write, or null if it takes them:
        // an old chunk's, whose containers are views of what it stores (or
        // stand in for what it does not), never the chunk's own tags.
        private readonly string? _readOnly;

        private readonly List<T> _palette = new List<T>();
        private readonly Dictionary<T, int> _lookup = new Dictionary<T, int>();
        private int[]? _indices;
        private int? _bits;

        public PalettedContainer(TagCompound tag, int count, int minBits,
                                 IPaletteCodec<T> codec, TagId elementType,
                                 bool modernStates = false, string? readOnly = null)
        {
            _tag = tag;
            _readOnly = readOnly;
            _count = count;
            _minBits = minBits;
            _codec = codec;
            _elementType = elementType;
            // A 26.3 block palette: bare ids for default states, wrappers in
            // a mixed list, defaults restored on read. Never a biome
            // container's -- Python's `modern_states and entries_are_compounds`,
            // where a compound element type is what "entries are compounds"
            // means here.
            _modern = modernStates && elementType == TagId.Compound;

            if (tag.Get("palette") is not TagList raw)
                throw new NbtException("paletted container has no palette");
            foreach (var entry in raw)
                _palette.Add(_codec.Decode(entry));
            // A duplicate entry keeps its LAST index -- the reference builds
            // this with `{value: i for i, value in enumerate(palette)}`, and
            // Prune() below rebuilds it the same way. Guarding the insert to
            // keep the first index instead makes one thaw encode to different
            // bytes in the two twins while every block state and every counter
            // agrees, which is the one disagreement only a digest can catch.
            for (int i = 0; i < _palette.Count; i++)
                _lookup[_palette[i]] = i;
        }

        /// <summary>The distinct values, in index order (a copy).</summary>
        public List<T> Palette => new List<T>(_palette);

        public int PaletteCount => _palette.Count;
        public T PaletteEntry(int index) => _palette[index];

        /// <summary>Whether this container writes the 26.3 block-state
        /// form; always false for biomes.</summary>
        public bool ModernStates => _modern;

        /// <summary>Whether every write here throws
        /// <see cref="ReadOnlyChunkException"/>.</summary>
        public bool ReadOnly => _readOnly != null;

        private void Writable()
        {
            if (_readOnly != null)
                throw new ReadOnlyChunkException(_readOnly);
        }

        // -- entry codecs --------------------------------------------------

        /// <summary>{"": id}: how a 26.3 palette carries a bare id in a list
        /// that also holds compounds. An entry that is already a compound
        /// passes through.</summary>
        private static Tag WrapBareId(Tag entry) =>
            entry is TagCompound ? entry : new TagCompound { [""] = entry };

        /// <summary>Add an encoded entry to the stored palette list.
        ///
        /// A 26.3 palette is a list of strings while every state in it is a
        /// default, and a list of compounds once one is not — each bare id
        /// wrapped as {"": id}, because a list holds one tag type. So a
        /// compound arriving in a string list widens the whole list, and a
        /// bare id arriving in a compound list is wrapped on its way in.</summary>
        private void AppendEntry(Tag entryTag)
        {
            var paletteTag = (TagList)_tag.Get("palette")!;
            if (!_modern)
            {
                paletteTag.Add(entryTag);
                if (paletteTag.ElementType == TagId.End)
                    paletteTag.ElementType = _elementType;
                return;
            }
            if (entryTag is TagCompound)
            {
                if (paletteTag.ElementType != TagId.Compound)
                {
                    var widened = new TagList(TagId.Compound);
                    foreach (var entry in paletteTag)
                        widened.Add(WrapBareId(entry));
                    _tag["palette"] = widened;
                    paletteTag = widened;
                }
                paletteTag.Add(entryTag);
            }
            else if (paletteTag.ElementType == TagId.Compound)
            {
                paletteTag.Add(WrapBareId(entryTag));
            }
            else
            {
                paletteTag.Add(entryTag);
                paletteTag.ElementType = TagId.String;
            }
        }

        /// <summary>A fresh stored palette holding exactly these encoded
        /// entries.</summary>
        private TagList NewPaletteList(IEnumerable<Tag> entryTags)
        {
            var entries = entryTags.ToList();
            if (!_modern)
            {
                var stored = (TagList)_tag.Get("palette")!;
                var list = new TagList(
                    stored.ElementType != TagId.End ? stored.ElementType : _elementType);
                foreach (var entry in entries)
                    list.Add(entry);
                return list;
            }
            bool anyCompound = entries.Any(entry => entry is TagCompound);
            var replacement = new TagList(anyCompound ? TagId.Compound : TagId.String);
            foreach (var entry in entries)
                replacement.Add(anyCompound ? WrapBareId(entry) : entry);
            return replacement;
        }

        // -- reading -------------------------------------------------------

        /// <summary>Bits per packed index, cached; every write path clears
        /// it. The array length is the authority when a shrunken palette
        /// left a wider packing behind.</summary>
        public int Bits
        {
            get
            {
                if (_bits == null)
                {
                    var data = _tag.Get("data") as TagLongArray;
                    _bits = data == null
                        ? Packing.BitsForPalette(_palette.Count, _minBits)
                        : Packing.InferBits(_palette.Count, data.Values.Length,
                                            _count, _minBits);
                }
                return _bits.Value;
            }
        }

        /// <summary>The value at a flat index, without unpacking the whole
        /// array.</summary>
        public T Get(int index)
        {
            if (_tag.Get("data") is not TagLongArray data)
                return _palette[0];  // uniform container: no array stored
            int bits = Bits;
            int perLong = 64 / bits;
            int word = index / perLong;
            int slot = index - word * perLong;
            ulong value = (ulong)data.Values[word];
            return _palette[(int)((value >> (slot * bits)) & ((1UL << bits) - 1))];
        }

        /// <summary>Every palette index, unpacked and cached — the
        /// expensive path. The array is live storage; treat as read-only.</summary>
        public int[] Indices()
        {
            if (_indices == null)
            {
                var data = _tag.Get("data") as TagLongArray;
                _indices = data == null
                    ? new int[_count]
                    : Packing.UnpackIndices(data.Values, _count, Bits);
            }
            return _indices;
        }

        /// <summary>Count of each palette value, in the order the cells
        /// first hold each. The indices are counted first, as integers, and
        /// named after, so a state is hashed once per palette entry rather
        /// than once per cell.</summary>
        public Dictionary<T, int> Histogram()
        {
            var tally = new int[_palette.Count];
            var order = new List<int>();
            foreach (int index in Indices())
            {
                if (tally[index]++ == 0)
                    order.Add(index);
            }
            var counts = new Dictionary<T, int>();
            foreach (int index in order)
            {
                var value = _palette[index];
                counts[value] = counts.TryGetValue(value, out int n) ? n + tally[index] : tally[index];
            }
            return counts;
        }

        // -- writing -------------------------------------------------------

        public void Set(int index, T value)
        {
            Writable();
            int oldBits = Bits;  // before the palette can grow
            int slot = SlotFor(value);

            int newBits = Packing.BitsForPalette(_palette.Count, _minBits);
            if (newBits != oldBits || _tag.Get("data") is not TagLongArray)
            {
                // widening (or materializing a uniform container) moves
                // every entry; rebuild the array
                int[] values = Indices();
                values[index] = slot;
                _indices = values;
                _bits = null;
                _tag["data"] = new TagLongArray(
                    Packing.PackIndices(values, _count, newBits));
                return;
            }

            var data = (TagLongArray)_tag.Get("data")!;
            int perLong = 64 / oldBits;
            int word = index / perLong;
            int position = index - word * perLong;
            int shift = position * oldBits;
            ulong mask = ((1UL << oldBits) - 1) << shift;
            ulong current = (ulong)data.Values[word];
            data.Values[word] = unchecked((long)(
                (current & ~mask) | ((ulong)(uint)slot << shift)));
            if (_indices != null)
                _indices[index] = slot;
        }

        private int SlotFor(T value)
        {
            var (key, entryTag) = _codec.Encode(value, _modern);
            if (_lookup.TryGetValue(key, out int slot))
                return slot;
            slot = _palette.Count;
            _palette.Add(key);
            _lookup[key] = slot;
            AppendEntry(entryTag);
            return slot;
        }

        /// <summary>Make every cell the same value: a single-entry palette
        /// with no index array, which is how the game stores a uniform
        /// section.</summary>
        public void FillAll(T value)
        {
            Writable();
            var (key, entryTag) = _codec.Encode(value, _modern);
            _tag["palette"] = NewPaletteList(new[] { entryTag });
            _tag.Remove("data");
            _palette.Clear();
            _palette.Add(key);
            _lookup.Clear();
            _lookup[key] = 0;
            _indices = new int[_count];
            _bits = null;
        }

        /// <summary>Set many cells to one value, unpacking and repacking
        /// once. Returns cells written.</summary>
        public int SetMany(IEnumerable<int> positions, T value)
        {
            Writable();
            int slot = SlotFor(value);
            int[] values = Indices();
            int written = 0;
            foreach (int position in positions)
            {
                values[position] = slot;
                written++;
            }
            _indices = values;
            _bits = null;
            _tag["data"] = new TagLongArray(Packing.PackIndices(
                values, _count, Packing.BitsForPalette(_palette.Count, _minBits)));
            return written;
        }

        /// <summary>Replace every cell from a sequence of exactly count
        /// values: palette rebuilt in first-seen order, packed once. For
        /// writers that own the whole section — a paste, not an edit.</summary>
        public void SetAll(IReadOnlyList<T> values)
        {
            Writable();
            if (values.Count != _count)
                throw new NbtException(
                    $"set_all needs exactly {_count} values, got {values.Count}");
            var palette = new List<T>();
            var lookup = new Dictionary<T, int>();
            var entryTags = new List<Tag>();
            var indices = new int[_count];
            for (int i = 0; i < values.Count; i++)
            {
                var (key, entryTag) = _codec.Encode(values[i], _modern);
                if (!lookup.TryGetValue(key, out int slot))
                {
                    slot = palette.Count;
                    palette.Add(key);
                    lookup[key] = slot;
                    entryTags.Add(entryTag);
                }
                indices[i] = slot;
            }
            _tag["palette"] = NewPaletteList(entryTags);
            _palette.Clear();
            _palette.AddRange(palette);
            _lookup.Clear();
            foreach (var pair in lookup)
                _lookup[pair.Key] = pair.Value;
            _indices = indices;
            _bits = null;
            if (palette.Count == 1)
                _tag.Remove("data");
            else
                _tag["data"] = new TagLongArray(Packing.PackIndices(
                    indices, _count, Packing.BitsForPalette(palette.Count, _minBits)));
        }

        /// <summary>Drop palette entries no longer referenced, repacking if
        /// that helps. Returns entries removed. The stored list keeps its
        /// element type and the kept entries are copied verbatim, so a
        /// pruned 26.3 palette keeps the form the game or an edit gave
        /// it.</summary>
        public int Prune()
        {
            Writable();
            int[] values = Indices();
            var used = new SortedSet<int>(values);
            if (used.Count == _palette.Count || used.Count == 0)
                return 0;

            var sourcePalette = (TagList)_tag.Get("palette")!;
            int removed = sourcePalette.Count - used.Count;

            var remap = new Dictionary<int, int>();
            var keptTags = new TagList(sourcePalette.ElementType);
            var newPalette = new List<T>();
            foreach (int old in used)
            {
                remap[old] = newPalette.Count;
                newPalette.Add(_palette[old]);
                keptTags.Add(sourcePalette[old]);
            }
            _palette.Clear();
            _palette.AddRange(newPalette);
            _lookup.Clear();
            for (int i = 0; i < _palette.Count; i++)
                _lookup[_palette[i]] = i;
            _tag["palette"] = keptTags;

            var remapped = new int[values.Length];
            for (int i = 0; i < values.Length; i++)
                remapped[i] = remap[values[i]];
            _indices = remapped;
            _bits = null;
            if (_palette.Count == 1)
            {
                _tag.Remove("data");
                _indices = new int[_count];
            }
            else
            {
                _tag["data"] = new TagLongArray(Packing.PackIndices(
                    remapped, _count,
                    Packing.BitsForPalette(_palette.Count, _minBits)));
            }
            return removed;
        }
    }
}
