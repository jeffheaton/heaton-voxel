// Twin of heaton_voxel/blocks.py (Section): one 16x16x16 slice of a chunk,
// with its block and biome containers and its stored light. A section of a
// chunk Minecraft 1.13 to 1.17 saved is read through containers that refuse
// every write: their blocks a view of the Palette and BlockStates the
// section stores (repacked once, before 1.16, where the indices run on
// across longs), a section of the world the chunk stores no blocks for --
// they left out what was all air -- one entry of air, and their biomes
// built from the chunk's numbered array.

using System.Collections.Generic;
using HeatonVoxel.Nbt;

namespace HeatonVoxel.Blocks
{
    public sealed class Section
    {
        public readonly int Y;
        private readonly TagCompound _tag;
        private readonly bool _modern;
        // An old section's refusal, which is also what marks it as one.
        private readonly string? _readOnly;
        private readonly string[]? _legacyBiomes;
        // Before 1.16 a section's indices run on across longs.
        private readonly bool _runOn;
        private PalettedContainer<BlockState>? _blocks;
        private PalettedContainer<string>? _biomes;
        private int[]? _blockLight;
        private int[]? _skyLight;

        /// <summary><paramref name="modernStates"/> is the chunk's verdict
        /// (<see cref="ChunkBlocks.ModernStates"/>): it reaches the block
        /// container and never the biomes.</summary>
        public Section(TagCompound tag, bool modernStates = false,
                       string? legacy = null, string[]? legacyBiomes = null,
                       bool runOn = false)
        {
            _tag = tag;
            Y = Tag.AsInt(tag.Get("Y")
                ?? throw new NbtException("section has no Y"));
            _modern = modernStates;
            _readOnly = legacy;
            _legacyBiomes = legacyBiomes;
            _runOn = runOn;
        }

        internal TagCompound RawTag => _tag;

        /// <summary>Block container, or null for a section with no block
        /// data.</summary>
        public PalettedContainer<BlockState>? Blocks
        {
            get
            {
                if (_blocks == null)
                {
                    var tag = _readOnly != null
                        ? LegacyBlockTag()
                        : _tag.Get("block_states") as TagCompound;
                    if (tag == null)
                        return null;
                    _blocks = new PalettedContainer<BlockState>(
                        tag, Packing.BlocksPerSection, Packing.MinBlockBits,
                        BlockStateCodec.Instance, TagId.Compound,
                        modernStates: _modern, readOnly: _readOnly);
                }
                return _blocks;
            }
        }

        /// <summary>Biome container at 4x4x4 resolution, or null.</summary>
        public PalettedContainer<string>? Biomes
        {
            get
            {
                if (_biomes == null)
                {
                    TagCompound? tag;
                    if (_readOnly != null)
                        tag = _legacyBiomes == null ? null : LegacyBiomeTag(_legacyBiomes);
                    else
                        tag = _tag.Get("biomes") as TagCompound;
                    if (tag == null)
                        return null;
                    _biomes = new PalettedContainer<string>(
                        tag, Packing.BiomesPerSection, Packing.MinBiomeBits,
                        BiomeCodec.Instance, TagId.String, readOnly: _readOnly);
                }
                return _biomes;
            }
        }

        /// <summary>What a current section would store as block_states,
        /// over the old section's own tags: the same palette list and
        /// index array, never copies, so a read is a read of what the
        /// chunk holds.</summary>
        private TagCompound? LegacyBlockTag()
        {
            if (_tag.Get("Palette") is TagList palette)
            {
                var view = new TagCompound { ["palette"] = palette };
                if (_tag.Get("BlockStates") is Tag states)
                {
                    if (_runOn && states is TagLongArray longs)
                    {
                        // Before 1.16 the indices run on across longs, as
                        // many bits each as fill the array exactly:
                        // repacked here, once, the way a current section
                        // packs them.
                        if (longs.Values.Length % 64 != 0)
                            throw new NbtException(
                                $"section Y={Y} holds {longs.Values.Length} longs, which "
                                + "no whole number of bits a cell fills");
                        int bits = longs.Values.Length / 64;
                        states = new TagLongArray(Packing.PackIndices(
                            Packing.UnpackRunOn(longs.Values, Packing.BlocksPerSection, bits),
                            Packing.BlocksPerSection, bits));
                    }
                    view["data"] = states;
                }
                return view;
            }
            if (Y >= ChunkBlocks.LegacyMinSection && Y <= ChunkBlocks.LegacyMaxSection)
            {
                var air = new TagList(TagId.Compound)
                {
                    new TagCompound { ["Name"] = new TagString("minecraft:air") },
                };
                return new TagCompound { ["palette"] = air };
            }
            return null;  // a section of light alone, past either end of the world
        }

        /// <summary>A section's 64 biome names as the biome container a
        /// current chunk stores: a palette in first-seen order -- the order
        /// the game's own upgrade builds it in
        /// (ChunkHeightAndBiomeFix.makeBiomeContainer) -- and an index
        /// array unless every cell is one biome.</summary>
        private static TagCompound LegacyBiomeTag(string[] names)
        {
            var palette = new List<string>();
            var slots = new Dictionary<string, int>(System.StringComparer.Ordinal);
            var indices = new int[names.Length];
            for (int i = 0; i < names.Length; i++)
            {
                if (!slots.TryGetValue(names[i], out int slot))
                {
                    slot = slots[names[i]] = palette.Count;
                    palette.Add(names[i]);
                }
                indices[i] = slot;
            }
            var list = new TagList(TagId.String);
            foreach (string name in palette)
                list.Add(new TagString(name));
            var tag = new TagCompound { ["palette"] = list };
            if (palette.Count > 1)
                tag["data"] = new TagLongArray(Packing.PackIndices(
                    indices, Packing.BiomesPerSection,
                    Packing.BitsForPalette(palette.Count, Packing.MinBiomeBits)));
            return tag;
        }

        /// <summary>Stored block-light levels, one 0-15 value per cell, or
        /// null where no array is stored — which means "the game will
        /// recompute", not darkness.</summary>
        public int[]? BlockLight()
        {
            if (_blockLight == null)
            {
                if (_tag.Get("BlockLight") is not TagByteArray raw)
                    return null;
                _blockLight = Packing.UnpackNibbles(raw.Values);
            }
            return _blockLight;
        }

        /// <summary>Stored sky-light levels; same shape and caveats as
        /// <see cref="BlockLight"/>.</summary>
        public int[]? SkyLight()
        {
            if (_skyLight == null)
            {
                if (_tag.Get("SkyLight") is not TagByteArray raw)
                    return null;
                _skyLight = Packing.UnpackNibbles(raw.Values);
            }
            return _skyLight;
        }
    }
}
