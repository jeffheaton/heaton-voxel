// Twin of heaton_voxel/blocks.py (BlockState). Immutable identity: a name
// plus properties, compared and printed with ordinally-sorted keys —
// StringComparer.Ordinal everywhere, never culture-aware comparison, or
// palette identities drift by locale (PORTING.md).

using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using HeatonVoxel.Nbt;

namespace HeatonVoxel.Blocks
{
    /// <summary>An insertion-ordered string map — Python's dict, for block
    /// properties.
    ///
    /// Order is data here, not decoration: the palette tag writes properties
    /// in this order, so a state rebuilt with a different order re-encodes to
    /// different bytes and breaks the byte-exact promise. C#'s Dictionary
    /// makes no ordering guarantee and visibly reuses freed slots after a
    /// Remove, so it cannot back this (PORTING.md, "Dictionary lacks
    /// insertion order"). Re-assigning an existing key keeps its position and
    /// a new key appends, exactly like a Python dict.</summary>
    public sealed class PropertyMap : IEnumerable<KeyValuePair<string, string>>
    {
        private readonly List<string> _order = new List<string>();
        private readonly Dictionary<string, string> _map =
            new Dictionary<string, string>(StringComparer.Ordinal);

        public PropertyMap() { }

        public PropertyMap(IEnumerable<KeyValuePair<string, string>> source)
        {
            foreach (var pair in source)
                this[pair.Key] = pair.Value;
        }

        public int Count => _order.Count;

        public string this[string key]
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

        public string? Get(string key) =>
            _map.TryGetValue(key, out var value) ? value : null;

        public bool Remove(string key)
        {
            if (!_map.Remove(key))
                return false;
            _order.Remove(key);
            return true;
        }

        public IEnumerator<KeyValuePair<string, string>> GetEnumerator()
        {
            foreach (string key in _order)
                yield return new KeyValuePair<string, string>(key, _map[key]);
        }

        IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
    }

    public sealed class BlockState : IEquatable<BlockState>
    {
        public readonly string Name;
        private readonly KeyValuePair<string, string>[] _sorted;
        private readonly PropertyMap _properties;

        /// <summary>The palette entry this state was decoded from, in either
        /// codec, so an untouched palette re-encodes to the same bytes, key
        /// order included. A legacy {Name, Properties} compound; or, from
        /// 26.3, a bare-id string, its {"": id} wrapper, or an {id,
        /// properties} compound.</summary>
        internal Tag? SourceTag;

        public BlockState(string name)
            : this(name, EmptyProperties) { }

        public BlockState(string name, IEnumerable<KeyValuePair<string, string>> properties)
        {
            Name = name;
            _properties = new PropertyMap(properties);
            _sorted = _properties
                .OrderBy(pair => pair.Key, StringComparer.Ordinal)
                .ToArray();
        }

        private static readonly KeyValuePair<string, string>[] EmptyProperties =
            Array.Empty<KeyValuePair<string, string>>();

        /// <summary>A copy in the order the state stores them; block states
        /// are immutable, so mutate the copy and build a new state from it.
        /// The order carries into the palette tag — see PropertyMap.</summary>
        public PropertyMap Properties => new PropertyMap(_properties);

        public string? GetProperty(string key) => _properties.Get(key);

        public int PropertyCount => _properties.Count;

        /// <summary>Build a state from ``name[key=value,...]`` notation.</summary>
        public static BlockState Parse(string text)
        {
            string trimmed = text.Trim();
            int open = trimmed.IndexOf('[');
            if (open < 0)
            {
                if (trimmed.IndexOf(']') >= 0 || trimmed.Length == 0)
                    throw new NbtException($"cannot parse block state '{text}'");
                return new BlockState(trimmed);
            }
            if (!trimmed.EndsWith("]", StringComparison.Ordinal))
                throw new NbtException($"cannot parse block state '{text}'");
            string name = trimmed.Substring(0, open).Trim();
            // Python's grammar is ^([^\[\]]+?)(?:\[(.*)\])?$ -- the name is
            // at least one character and holds no bracket of either kind. A
            // nameless term must refuse here rather than search for "" and
            // come back with a blameless "nothing found": that is exactly
            // "could not read" being mistaken for "nothing there".
            if (name.Length == 0 || name.IndexOf(']') >= 0)
                throw new NbtException($"cannot parse block state '{text}'");
            string body = trimmed.Substring(open + 1, trimmed.Length - open - 2);
            var properties = new List<KeyValuePair<string, string>>();
            if (body.Trim().Length > 0)
            {
                foreach (string pair in body.Split(','))
                {
                    int eq = pair.IndexOf('=');
                    if (eq < 0)
                        throw new NbtException(
                            $"property '{pair}' in '{text}' has no '='");
                    properties.Add(new KeyValuePair<string, string>(
                        pair.Substring(0, eq).Trim(),
                        pair.Substring(eq + 1).Trim()));
                }
            }
            return new BlockState(name, properties);
        }

        /// <summary>Decode a palette entry, in either codec.
        ///
        /// Before 26.3 an entry is {Name, Properties} and is taken exactly as
        /// stored. From 26.3 (<see cref="ChunkBlocks.ModernStateDataVersion"/>)
        /// a state at its block's defaults is the bare id — a string, or
        /// {"": id} where the list also holds compounds — and any other state
        /// is {id, properties}. The properties that form leaves out are
        /// restored from <see cref="BlockDefaults"/>, so a 26.3 chunk reads
        /// exactly as the older format wrote it: every property present, in
        /// name order, and water is water[level=0] to a reader as it is to
        /// the game. The original tag is kept either way, so an untouched
        /// palette re-encodes to the same bytes.</summary>
        public static BlockState FromNbt(Tag tag)
        {
            Tag source = tag;
            if (tag is TagCompound wrapper && wrapper.Count == 1 && wrapper.ContainsKey(""))
                tag = wrapper[""];
            string name;
            Tag? given;
            bool modern;
            if (tag is TagString bare)
            {
                name = bare.Value;
                given = null;
                modern = true;
            }
            else if (tag is TagCompound full && full.ContainsKey("id"))
            {
                name = Tag.Text(full["id"]);
                given = full.Get("properties");
                modern = true;
            }
            else if (tag is TagCompound legacy && legacy.ContainsKey("Name"))
            {
                name = Tag.Text(legacy["Name"]);
                given = legacy.Get("Properties");
                modern = false;
            }
            else
            {
                throw new NbtException(
                    "palette entry is not a block state: " + Describe(tag));
            }

            // In stored order: for a legacy entry that order is the bytes.
            var properties = new List<KeyValuePair<string, string>>();
            if (given is TagCompound props)
            {
                foreach (var pair in props)
                    properties.Add(new KeyValuePair<string, string>(
                        pair.Key, Tag.Text(pair.Value)));
            }
            else if (given != null)
            {
                throw new NbtException(
                    $"block state {name}: properties are {Tag.NameOf(given.Id)}, "
                    + "not a compound");
            }

            IEnumerable<KeyValuePair<string, string>> ordered = properties;
            if (modern)
            {
                // Defaults first, then what was stored on top (a stored key
                // keeps the default's position and takes its value, a new
                // one appends), then name order -- Python's
                // dict(sorted(filled.items())).
                var filled = new PropertyMap(BlockDefaults.DefaultProperties(name));
                foreach (var pair in properties)
                    filled[pair.Key] = pair.Value;
                ordered = filled.OrderBy(pair => pair.Key, StringComparer.Ordinal).ToArray();
            }
            var state = new BlockState(name, ordered);
            state.SourceTag = source;  // original bytes and key order survive
            return state;
        }

        private static string Describe(Tag tag) => tag is TagCompound compound
            ? $"{Tag.NameOf(tag.Id)} with keys [{string.Join(", ", compound.Keys)}]"
            : Tag.NameOf(tag.Id);

        /// <summary>This state with its block's defaults filled in and its
        /// properties in name order: the identity the 26.3 codec works in,
        /// where water and water[level=0] are one block. A block the
        /// defaults table does not know keeps exactly the properties it was
        /// given. Returns this very instance when nothing would change.</summary>
        public BlockState Canonical()
        {
            var filled = new PropertyMap(BlockDefaults.DefaultProperties(Name));
            foreach (var pair in _properties)
                filled[pair.Key] = pair.Value;
            var ordered = filled.OrderBy(pair => pair.Key, StringComparer.Ordinal).ToArray();
            if (ordered.Length == _properties.Count)
            {
                bool same = true;
                int i = 0;
                foreach (var pair in _properties)
                {
                    if (!string.Equals(ordered[i].Key, pair.Key, StringComparison.Ordinal)
                        || !string.Equals(ordered[i].Value, pair.Value, StringComparison.Ordinal))
                    {
                        same = false;
                        break;
                    }
                    i++;
                }
                if (same)
                    return this;
            }
            return new BlockState(Name, ordered);
        }

        /// <summary>Encode for a palette.
        ///
        /// Legacy (the default): {Properties, Name}, or the very tag this
        /// state was decoded from when that was a legacy entry. modern: the
        /// 26.3 form — the bare id as a string when the state is its block's
        /// default (or has no properties and the block is unknown), otherwise
        /// {id, properties} carrying every property the block has, defaults
        /// filled in, in name order. A source tag is never reused for a
        /// modern write. The container decides how a bare id sits in its
        /// list; see PalettedContainer.AppendEntry.</summary>
        public Tag ToNbt(bool modern = false)
        {
            if (modern)
            {
                var defaults = BlockDefaults.DefaultProperties(Name);
                var complete = new PropertyMap(defaults);
                foreach (var pair in _properties)
                    complete[pair.Key] = pair.Value;
                // Python's `complete == filled`: no key beyond the defaults,
                // and every default at its default value.
                bool isDefault = complete.Count == defaults.Length;
                for (int i = 0; isDefault && i < defaults.Length; i++)
                {
                    if (!string.Equals(complete[defaults[i].Key], defaults[i].Value,
                                       StringComparison.Ordinal))
                        isDefault = false;
                }
                if (isDefault)
                    return new TagString(Name);
                var full = new TagCompound();
                full["id"] = new TagString(Name);
                var properties = new TagCompound();
                foreach (var pair in complete.OrderBy(pair => pair.Key, StringComparer.Ordinal))
                    properties[pair.Key] = new TagString(pair.Value);
                full["properties"] = properties;
                return full;
            }
            if (SourceTag is TagCompound legacy && legacy.ContainsKey("Name"))
                return legacy;
            var tag = new TagCompound();
            if (_properties.Count > 0)
            {
                // Insertion order, not sorted order: this is what the palette
                // tag stores, and the reference writes its dict the same way.
                var props = new TagCompound();
                foreach (var pair in _properties)
                    props[pair.Key] = new TagString(pair.Value);
                tag["Properties"] = props;
            }
            tag["Name"] = new TagString(Name);
            return tag;
        }

        /// <summary>A copy with some properties replaced. Replacing an
        /// existing property keeps its position; a new one appends, matching
        /// Python's dict(props) + update.</summary>
        public BlockState WithProperties(params (string Key, string Value)[] changes)
        {
            var merged = new PropertyMap(_properties);
            foreach (var (key, value) in changes)
                merged[key] = value;
            return new BlockState(Name, merged);
        }

        public bool Equals(BlockState? other)
        {
            if (other is null || !string.Equals(Name, other.Name, StringComparison.Ordinal)
                || _sorted.Length != other._sorted.Length)
                return false;
            for (int i = 0; i < _sorted.Length; i++)
            {
                if (!string.Equals(_sorted[i].Key, other._sorted[i].Key, StringComparison.Ordinal)
                    || !string.Equals(_sorted[i].Value, other._sorted[i].Value, StringComparison.Ordinal))
                    return false;
            }
            return true;
        }

        /// <summary>Matches Python's convenience: a state equals a plain
        /// string only when it has no properties.</summary>
        public bool Equals(string name) =>
            _properties.Count == 0 && string.Equals(Name, name, StringComparison.Ordinal);

        public override bool Equals(object? obj) => obj switch
        {
            BlockState state => Equals(state),
            string name => Equals(name),
            _ => false,
        };

        public override int GetHashCode()
        {
            var hash = new HashCode();
            hash.Add(Name, StringComparer.Ordinal);
            foreach (var pair in _sorted)
            {
                hash.Add(pair.Key, StringComparer.Ordinal);
                hash.Add(pair.Value, StringComparer.Ordinal);
            }
            return hash.ToHashCode();
        }

        public override string ToString()
        {
            if (_sorted.Length == 0)
                return Name;
            var text = new StringBuilder(Name).Append('[');
            for (int i = 0; i < _sorted.Length; i++)
            {
                if (i > 0)
                    text.Append(',');
                text.Append(_sorted[i].Key).Append('=').Append(_sorted[i].Value);
            }
            return text.Append(']').ToString();
        }
    }
}
