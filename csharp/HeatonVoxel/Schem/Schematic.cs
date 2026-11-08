// Twin of heaton_voxel/schem/schematic.py: the Schematic itself — a box of
// blocks with no world attached, its .schem reader and writer, and its
// rotate/flip transforms.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using HeatonVoxel.Blocks;
using HeatonVoxel.Nbt;

namespace HeatonVoxel.Schem
{
    /// <summary>A box of blocks with no world attached.
    ///
    /// Coordinates are relative to the minimum corner: x runs 0..width-1
    /// (east), y 0..height-1 (up), z 0..length-1 (south). Storage is a
    /// palette of BlockState plus one index per cell, which is also how the
    /// file encodes it.</summary>
    public sealed class Schematic
    {
        private readonly int _width;
        private readonly int _height;
        private readonly int _length;

        /// <summary>The Minecraft data version the block states belong to.
        /// Assignable, for the caller who knows better than the check.</summary>
        public int DataVersion;

        /// <summary>(x, y, z) of the minimum corner relative to the author's
        /// copy origin — the file's Offset. (0, 0, 0) unless the exporter
        /// passed an origin.</summary>
        public (int X, int Y, int Z) Offset;

        private List<BlockState> _palette;
        private Dictionary<BlockState, int> _lookup;
        private int[] _blocks;

        private List<string>? _biomePalette;
        private Dictionary<string, int> _biomeLookup =
            new Dictionary<string, int>(StringComparer.Ordinal);
        private int[]? _biomes;

        /// <summary>{(x, y, z): TagCompound} — the block entity's NBT as the
        /// chunk stores it, minus its position and keepPacked (both
        /// re-derived on paste); "id" stays inside.</summary>
        public readonly Dictionary<(int X, int Y, int Z), TagCompound> BlockEntities =
            new Dictionary<(int, int, int), TagCompound>();

        /// <summary>Entity compounds exactly as entity storage holds them —
        /// id, UUID and all — except that Pos (and the other stored
        /// positions) are relative to the minimum corner.</summary>
        public readonly List<TagCompound> Entities = new List<TagCompound>();

        /// <summary>The file's Metadata compound, carried through, or null.
        /// WorldEdit's WEOffsetX/Y/Z live in <see cref="Offset"/> instead.</summary>
        public TagCompound? Metadata;

        /// <summary>Container version the file was loaded from (2 or 3), or
        /// null for one built in memory.</summary>
        public int? SourceVersion;

        /// <summary>"sponge", "structure" or "litematic": the format the
        /// file was loaded from; null for one built in memory.</summary>
        public string? SourceFormat;

        /// <summary>How many palettes a structure file held, the first being
        /// the one read; null for every other source.</summary>
        public int? SourcePalettes;

        /// <summary>True if a loaded version-2 file carried the old
        /// column-based biome data, which this module does not apply.</summary>
        public bool Had2dBiomes;

        /// <summary>Set by Rotated()/Flipped() on their result: states whose
        /// properties look directional but sit outside the vocabulary.</summary>
        public List<string> TransformWarnings = new List<string>();

        public Schematic(int width, int height, int length, int dataVersion,
                         (int X, int Y, int Z) offset = default)
        {
            foreach (var (name, value) in new[]
                     { ("width", width), ("height", height), ("length", length) })
            {
                if (value < 1 || value > Support.MaxEdge)
                    throw new NbtException(
                        $"schematic {name} must be 1..{Support.MaxEdge}, got {value}");
            }
            if (dataVersion < ChunkBlocks.MinDataVersion)
                throw new NbtException(
                    $"schematic DataVersion {dataVersion} predates the current "
                    + $"format (needs {ChunkBlocks.MinDataVersion}, Minecraft 1.18); "
                    + "its block names would not mean the same thing in a current "
                    + "world.");
            _width = width;
            _height = height;
            _length = length;
            DataVersion = dataVersion;
            Offset = offset;

            var air = new BlockState("minecraft:air");
            _palette = new List<BlockState> { air };
            _lookup = new Dictionary<BlockState, int> { [air] = 0 };
            _blocks = new int[_width * _height * _length];
        }

        // -- geometry ------------------------------------------------------

        /// <summary>Size along x (east).</summary>
        public int Width => _width;

        /// <summary>Size along y (up).</summary>
        public int Height => _height;

        /// <summary>Size along z (south).</summary>
        public int Length => _length;

        public (int Width, int Height, int Length) Size => (_width, _height, _length);

        public int Volume => _width * _height * _length;

        /// <summary>The storage index of a cell, for the format readers and
        /// writers beside this class; refuses one outside the box.</summary>
        internal int CellIndex(int x, int y, int z) => Index(x, y, z);

        private int Index(int x, int y, int z)
        {
            if (x < 0 || x >= _width || y < 0 || y >= _height || z < 0 || z >= _length)
                throw new ArgumentOutOfRangeException(
                    nameof(x),
                    $"({x}, {y}, {z}) is outside this "
                    + $"{_width}x{_height}x{_length} schematic");
            // The format's order: y, then z, then x.
            return (y * _length + z) * _width + x;
        }

        // -- blocks --------------------------------------------------------

        /// <summary>The distinct states, in index order. Entry 0 need not be
        /// air.</summary>
        public List<BlockState> Palette => new List<BlockState>(_palette);

        public int PaletteCount => _palette.Count;

        public BlockState PaletteEntry(int index) => _palette[index];

        /// <summary>Every cell's palette index, in the format's y, z, x
        /// order: cell (x, y, z) lives at (y * length + z) * width + x. The
        /// array is live storage — treat it as read-only, since writing to
        /// it would bypass palette interning.</summary>
        public int[] Indices() => _blocks;

        private int Intern(BlockState state)
        {
            if (_lookup.TryGetValue(state, out int slot))
                return slot;
            slot = _palette.Count;
            _palette.Add(state);
            _lookup[state] = slot;
            return slot;
        }

        public BlockState GetBlock(int x, int y, int z) =>
            _palette[_blocks[Index(x, y, z)]];

        public void SetBlock(int x, int y, int z, BlockState state) =>
            _blocks[Index(x, y, z)] = Intern(state);

        public void SetBlock(int x, int y, int z, string state) =>
            SetBlock(x, y, z, BlockState.Parse(state));

        /// <summary>Count of every state in the box, air included.</summary>
        public Dictionary<BlockState, int> Histogram()
        {
            var tally = new Dictionary<int, int>();
            foreach (int index in _blocks)
                tally[index] = tally.TryGetValue(index, out int n) ? n + 1 : 1;
            var counts = new Dictionary<BlockState, int>();
            foreach (var pair in tally)
                counts[_palette[pair.Key]] = pair.Value;
            return counts;
        }

        // -- block entities ------------------------------------------------

        public TagCompound? GetBlockEntity(int x, int y, int z)
        {
            Index(x, y, z);  // bounds check
            return BlockEntities.TryGetValue((x, y, z), out var data) ? data : null;
        }

        /// <summary>Attach block entity NBT to a cell, or remove it with
        /// null. data is the chunk-storage shape minus position: it must
        /// carry its "id" (minecraft:chest, ...), and must not carry x/y/z —
        /// those belong to wherever the schematic gets pasted.</summary>
        public void SetBlockEntity(int x, int y, int z, TagCompound? data)
        {
            Index(x, y, z);  // bounds check
            if (data == null)
            {
                BlockEntities.Remove((x, y, z));
                return;
            }
            if (!data.ContainsKey("id"))
                throw new NbtException(
                    $"block entity at ({x}, {y}, {z}) has no 'id'; the game could "
                    + "not tell what it is");
            BlockEntities[(x, y, z)] = data;
        }

        // -- biomes --------------------------------------------------------

        public bool HasBiomes => _biomes != null;

        /// <summary>The stored biome for a cell, or null if the schematic
        /// carries no biome data. One entry per block, as version 3 stores
        /// it.</summary>
        public string? GetBiome(int x, int y, int z)
        {
            int index = Index(x, y, z);
            return _biomes == null ? null : _biomePalette![_biomes[index]];
        }

        private int InternBiome(string name)
        {
            if (_biomeLookup.TryGetValue(name, out int slot))
                return slot;
            slot = _biomePalette!.Count;
            _biomePalette.Add(name);
            _biomeLookup[name] = slot;
            return slot;
        }

        /// <summary>Give every cell this biome, creating the biome data if
        /// absent.</summary>
        public void FillBiomes(string biome)
        {
            _biomePalette = new List<string> { biome };
            _biomeLookup = new Dictionary<string, int>(StringComparer.Ordinal)
            {
                [biome] = 0,
            };
            _biomes = new int[Volume];
        }

        /// <summary>Set one cell's biome. A schematic either carries a biome
        /// for every cell or none at all — the format has no "unset" — so the
        /// first ever SetBiome fills the whole box with the given biome and
        /// goes from there.</summary>
        public void SetBiome(int x, int y, int z, string biome)
        {
            int index = Index(x, y, z);
            if (_biomes == null)
            {
                FillBiomes(biome);
                return;
            }
            _biomes[index] = InternBiome(biome);
        }

        internal int[]? BiomeIndices => _biomes;

        internal List<string>? BiomePalette => _biomePalette;

        /// <summary>Palette interning for the copy layer, which builds a
        /// per-section index map rather than going cell by cell.</summary>
        internal int InternState(BlockState state) => Intern(state);

        internal int InternBiomeName(string name) => InternBiome(name);

        // -- construction ---------------------------------------------------

        /// <summary>An empty schematic to build in memory. dataVersion is
        /// required and deliberately has no default: it stamps which
        /// Minecraft the block names belong to, and this toolkit does not
        /// hardwire a game version.</summary>
        public static Schematic New(int width, int height, int length, int dataVersion,
                                    string fill = "minecraft:air")
        {
            var schematic = new Schematic(width, height, length, dataVersion);
            if (fill != "minecraft:air")
            {
                int slot = schematic.Intern(BlockState.Parse(fill));
                for (int i = 0; i < schematic._blocks.Length; i++)
                    schematic._blocks[i] = slot;
            }
            return schematic;
        }

        /// <summary>Copy a box out of a save. See Copy.ExportSchem, which
        /// also writes the file and returns the full accounting; this returns
        /// just the in-memory schematic.</summary>
        public static Schematic FromSave(
            World.MinecraftSave save, (int X, int Y, int Z) corner1,
            (int X, int Y, int Z) corner2, (int X, int Y, int Z)? origin = null,
            bool entities = false, bool biomes = false, string? dimension = null,
            bool allowIncomplete = false, bool keepLoaded = false) =>
            Copy.CopyOut(save, corner1, corner2, origin, entities, biomes, dimension,
                         allowIncomplete, keepLoaded).Schematic;

        /// <summary>Paste into a save; see Copy.ImportSchem for the full
        /// rules.</summary>
        public SchemImportResult Paste(
            World.MinecraftSave save, (int X, int Y, int Z) at,
            string anchor = "origin", bool entities = true, bool biomes = false,
            bool pasteAir = true, bool relight = true, bool heightmaps = true,
            string? dimension = null, bool prune = true, bool keepLoaded = false,
            int rotate = 0, string? flip = null, bool pasteVoids = false) =>
            Copy.PasteIn(save, this, at, anchor, entities, biomes, pasteAir, relight,
                         heightmaps, dimension, prune, keepLoaded, rotate, flip, pasteVoids);

        // -- rotation and mirroring ----------------------------------------

        /// <summary>A copy rotated about the vertical axis; the original is
        /// untouched. degrees must be a multiple of 90; positive is
        /// CLOCKWISE SEEN FROM ABOVE — what faced north faces east — matching
        /// WorldEdit's //rotate. The rotation is about the schematic's
        /// origin (the stored offset turns with the box).</summary>
        public Schematic Rotated(int degrees)
        {
            if (degrees % 90 != 0)
                throw new NbtException(
                    $"rotation must be a multiple of 90 degrees, got {degrees}; "
                    + "block states have no in-between orientations");
            int quarters = (degrees / 90) % 4;
            if (quarters < 0)
                quarters += 4;  // floored, as Python's % gives
            var (width, height, length) = Size;
            var (ox, oy, oz) = Offset;

            (int, int, int) size;
            (int, int, int) offset;
            CellMap cell;
            PointMap point;
            PointMap vector;
            switch (quarters)
            {
                case 0:
                    size = (width, height, length);
                    offset = (ox, oy, oz);
                    cell = (x, y, z) => (x, y, z);
                    point = (x, y, z) => (x, y, z);
                    vector = (x, y, z) => (x, y, z);
                    break;
                case 1:
                    size = (length, height, width);
                    offset = (-oz - length + 1, oy, ox);
                    cell = (x, y, z) => (length - 1 - z, y, x);
                    point = (x, y, z) => (length - z, y, x);
                    vector = (x, y, z) => (-z, y, x);
                    break;
                case 2:
                    size = (width, height, length);
                    offset = (-ox - width + 1, oy, -oz - length + 1);
                    cell = (x, y, z) => (width - 1 - x, y, length - 1 - z);
                    point = (x, y, z) => (width - x, y, length - z);
                    vector = (x, y, z) => (-x, y, -z);
                    break;
                default:
                    size = (length, height, width);
                    offset = (oz, oy, -ox - width + 1);
                    cell = (x, y, z) => (z, y, width - 1 - x);
                    point = (x, y, z) => (z, y, width - x);
                    vector = (x, y, z) => (z, y, -x);
                    break;
            }

            PropertyMap Mapper(PropertyMap properties)
            {
                for (int i = 0; i < quarters; i++)
                    properties = Transforms.PropertiesRotatedCw(properties);
                return properties;
            }

            double yawDelta = 90.0 * quarters;
            var result = Transformed(
                size, offset, cell,
                state => Transforms.MapState(state, Mapper),
                new EntityOp
                {
                    Point = point,
                    Cell = cell,
                    Yaw = value => value + yawDelta,
                    PitchSign = 1.0,
                    Facing3 = Transforms.ComposeByteTable(Transforms.Facing3Cw, quarters),
                    Facing2 = Transforms.ComposeByteTable(Transforms.Facing2Cw, quarters),
                    Vector = vector,
                });
            if (quarters != 0)
                result.TransformWarnings = Transforms.DirectionalLeftovers(_palette);
            return result;
        }

        /// <summary>A mirrored copy; the original is untouched. axis names
        /// the axis the flip runs along: "x" swaps east and west, "z" swaps
        /// north and south, "y" turns the build upside down. Chirality flips
        /// with either horizontal mirror, so stair corners swap left for
        /// right, chest pairs swap halves, and door hinges change sides.</summary>
        public Schematic Flipped(string axis)
        {
            if (axis != "x" && axis != "y" && axis != "z")
                throw new NbtException($"flip axis must be 'x', 'y' or 'z', got '{axis}'");
            var (width, height, length) = Size;
            var (ox, oy, oz) = Offset;

            (int, int, int) offset;
            CellMap cell;
            PointMap point;
            PointMap vector;
            YawMap yaw;
            double pitchSign;
            if (axis == "x")
            {
                offset = (-ox - width + 1, oy, oz);
                cell = (x, y, z) => (width - 1 - x, y, z);
                point = (x, y, z) => (width - x, y, z);
                vector = (x, y, z) => (-x, y, z);
                yaw = value => -value;
                pitchSign = 1.0;
            }
            else if (axis == "z")
            {
                offset = (ox, oy, -oz - length + 1);
                cell = (x, y, z) => (x, y, length - 1 - z);
                point = (x, y, z) => (x, y, length - z);
                vector = (x, y, z) => (x, y, -z);
                yaw = value => 180.0 - value;
                pitchSign = 1.0;
            }
            else
            {
                offset = (ox, -oy - height + 1, oz);
                cell = (x, y, z) => (x, height - 1 - y, z);
                point = (x, y, z) => (x, height - y, z);
                vector = (x, y, z) => (x, -y, z);
                yaw = value => value;
                pitchSign = -1.0;
            }

            var result = Transformed(
                (width, height, length), offset, cell,
                state => Transforms.MapState(
                    state, properties => Transforms.PropertiesMirrored(properties, axis)),
                new EntityOp
                {
                    Point = point,
                    Cell = cell,
                    Yaw = yaw,
                    PitchSign = pitchSign,
                    Facing3 = Transforms.Facing3Mirror[axis],
                    Facing2 = Transforms.Facing2Mirror[axis],
                    Vector = vector,
                });
            result.TransformWarnings = Transforms.DirectionalLeftovers(_palette);
            return result;
        }

        internal sealed class EntityOp
        {
            public PointMap Point = null!;
            public CellMap Cell = null!;
            public YawMap Yaw = null!;
            public double PitchSign;
            public Dictionary<int, int> Facing3 = null!;
            public Dictionary<int, int> Facing2 = null!;
            public PointMap Vector = null!;
        }

        /// <summary>Rebuild every store of this schematic through a
        /// coordinate map.</summary>
        private Schematic Transformed((int W, int H, int L) size,
                                      (int X, int Y, int Z) offset, CellMap cell,
                                      Func<BlockState, BlockState> mapState,
                                      EntityOp entityOp)
        {
            var result = new Schematic(size.W, size.H, size.L, DataVersion, offset)
            {
                SourceVersion = SourceVersion,
                Had2dBiomes = Had2dBiomes,
            };
            if (Metadata != null)
                result.Metadata = (TagCompound)Support.CopyTag(Metadata);

            result._palette = new List<BlockState>();
            result._lookup = new Dictionary<BlockState, int>();
            var indexMap = _palette.Select(state => result.Intern(mapState(state)))
                .ToArray();

            var (width, height, length) = Size;
            int newWidth = size.W, newLength = size.L;
            int[] output = result._blocks;
            int[]? biomesOut = null;
            if (_biomes != null)
            {
                result._biomePalette = new List<string>(_biomePalette!);
                result._biomeLookup = new Dictionary<string, int>(
                    _biomeLookup, StringComparer.Ordinal);
                result._biomes = new int[_biomes.Length];
                biomesOut = result._biomes;
            }

            int source = 0;
            for (int y = 0; y < height; y++)
            {
                for (int z = 0; z < length; z++)
                {
                    for (int x = 0; x < width; x++)
                    {
                        var (nx, ny, nz) = cell(x, y, z);
                        int target = (ny * newLength + nz) * newWidth + nx;
                        output[target] = indexMap[_blocks[source]];
                        if (biomesOut != null)
                            biomesOut[target] = _biomes![source];
                        source++;
                    }
                }
            }

            foreach (var pair in BlockEntities)
            {
                var (x, y, z) = pair.Key;
                result.BlockEntities[cell(x, y, z)] =
                    (TagCompound)Support.CopyTag(pair.Value);
            }
            foreach (var entity in Entities)
            {
                var copy = (TagCompound)Support.CopyTag(entity);
                Transforms.TransformEntity(
                    copy, entityOp.Point, entityOp.Cell, entityOp.Yaw,
                    entityOp.PitchSign, entityOp.Facing3, entityOp.Facing2,
                    entityOp.Vector);
                result.Entities.Add(copy);
            }
            return result;
        }

        // -- files ---------------------------------------------------------

        /// <summary>Read a .schem file, container version 2 or 3.
        ///
        /// Refusals, each with its reason: the pre-1.13 .schematic layout
        /// (numeric block ids — a different format, not an older version of
        /// this one), Sponge version 1, any file without a DataVersion, and
        /// any DataVersion older than 1.18.
        ///
        /// A version-2 file's column biome data (BiomeData) is NOTED —
        /// <see cref="Had2dBiomes"/> — but not carried: it stores one biome
        /// per column where the game now wants one per 4x4x4 cell, and
        /// inventing the vertical dimension would be a guess.</summary>
        public static Schematic Load(string path)
        {
            byte[] blob = File.ReadAllBytes(path);
            byte[] payload;
            if (blob.Length >= 2 && blob[0] == 0x1F && blob[1] == 0x8B)
            {
                payload = Compression.GzipDecompress(blob);
            }
            else if (blob.Length >= 1 && blob[0] == 0x0A)
            {
                payload = blob;
            }
            else
            {
                try
                {
                    payload = Compression.ZlibDecompress(blob);
                }
                catch (NbtException)
                {
                    throw new NbtException(
                        $"'{path}' is not gzip, zlib or bare NBT; not a .schem file");
                }
            }
            var (_, root) = Codec.Decode(payload);
            if (Structure.IsStructure(root))
                return Structure.ReadStructure(root, path);
            if (Litematic.IsLitematic(root))
                return Litematic.ReadLitematic(root, path);

            var body = root.Get("Schematic") as TagCompound ?? root;
            if (!body.ContainsKey("Version"))
            {
                if (body.Get("Blocks") is TagByteArray && body.ContainsKey("Materials"))
                    throw new NbtException(
                        $"'{path}' is a legacy pre-1.13 .schematic (numeric block "
                        + "ids), which is a different format, not an old version of "
                        + ".schem. Convert it with WorldEdit first.");
                throw new NbtException(
                    $"'{path}' has no Version tag; not a Sponge schematic");
            }
            int version = Tag.AsInt(body["Version"]);
            if (version == 1)
                throw new NbtException(
                    $"'{path}' is Sponge schematic version 1, which predates the "
                    + "block content this toolkit supports (Minecraft 1.18).");
            if (version != 2 && version != 3)
                throw new NbtException(
                    $"'{path}' is Sponge schematic version {version}; only 2 and 3 "
                    + "are known here");

            if (!body.ContainsKey("DataVersion"))
                throw new NbtException(
                    $"'{path}' has no DataVersion, so there is no telling which "
                    + "Minecraft its block names belong to; refusing to guess.");

            int width = Dimension(body, "Width");
            int height = Dimension(body, "Height");
            int length = Dimension(body, "Length");

            var schematic = new Schematic(width, height, length,
                                          Tag.AsInt(body["DataVersion"]))
            {
                SourceVersion = version,
                SourceFormat = "sponge",
            };
            if (version == 2)
                schematic.LoadV2(body, path);
            else
                schematic.LoadV3(body, path);
            return schematic;
        }

        /// <summary>Width/Height/Length are unsigned shorts stored in NBT's
        /// signed short, so read them back through a 16-bit mask.</summary>
        private static int Dimension(TagCompound body, string name) =>
            body.Get(name) is Tag tag ? Tag.AsInt(tag) & 0xFFFF : 0;

        private void LoadPalette(Tag? paletteTag, Tag? volumeTag, string what)
        {
            if (paletteTag is not TagCompound palette || volumeTag is not TagByteArray data)
                throw new NbtException($"{what}: no palette or block data");
            var entries = new BlockState?[palette.Count];
            foreach (var pair in palette)
            {
                int index = Tag.AsInt(pair.Value);
                if (index < 0 || index >= entries.Length || entries[index] != null)
                    throw new NbtException(
                        $"{what}: palette index {index} for '{pair.Key}' is out of "
                        + "range or duplicated");
                entries[index] = BlockState.Parse(pair.Key);
            }
            _palette = entries.Select(entry => entry!).ToList();  // dense by the check
            _lookup = new Dictionary<BlockState, int>();
            for (int i = 0; i < _palette.Count; i++)
            {
                if (!_lookup.ContainsKey(_palette[i]))
                    _lookup[_palette[i]] = i;
            }
            int[] values = Support.ReadVarints(data.Values, Volume, $"{what}: block data");
            int top = _palette.Count;
            foreach (int value in values)
            {
                if (value >= top)
                    throw new NbtException(
                        $"{what}: block data refers to palette entry {value} of {top}");
            }
            _blocks = values;
        }

        private void LoadBlockEntity(TagCompound entry, string what)
        {
            if (entry.Get("Pos") is not Tag posTag || PositionInts(posTag) is not int[] pos
                || pos.Length != 3)
                throw new NbtException($"{what}: block entity without a 3-int Pos");
            int x = pos[0], y = pos[1], z = pos[2];
            Index(x, y, z);  // bounds check: outside the box is corruption
            TagCompound data;
            if (entry.Get("Data") is TagCompound nested)  // version 3 nests the payload
            {
                data = (TagCompound)Support.CopyTag(nested);
            }
            else
            {
                data = new TagCompound();
                foreach (var pair in entry)
                {
                    if (pair.Key != "Pos" && pair.Key != "Id" && pair.Key != "Data")
                        data[pair.Key] = Support.CopyTag(pair.Value);
                }
            }
            var identity = entry.Get("Id") ?? data.Get("id");
            if (identity == null)
                throw new NbtException(
                    $"{what}: block entity at ({x}, {y}, {z}) has no Id");
            data["id"] = new TagString(Tag.AsString(identity));
            BlockEntities[(x, y, z)] = data;
        }

        private static int[]? PositionInts(Tag tag) => tag switch
        {
            TagIntArray array => array.Values,
            TagList list => list.Select(Tag.AsInt).ToArray(),
            _ => null,
        };

        private static double[]? PositionDoubles(Tag tag) => tag switch
        {
            TagList list => list.Select(Tag.AsDouble).ToArray(),
            _ => null,
        };

        private void LoadEntity(TagCompound entry, string what)
        {
            if (entry.Get("Pos") is not Tag posTag
                || PositionDoubles(posTag) is not double[] pos || pos.Length != 3)
                throw new NbtException($"{what}: entity without a 3-double Pos");
            TagCompound data;
            if (entry.Get("Data") is TagCompound nested)  // version 3 nests the payload
            {
                data = (TagCompound)Support.CopyTag(nested);
            }
            else
            {
                data = new TagCompound();
                foreach (var pair in entry)
                {
                    if (pair.Key != "Id" && pair.Key != "Data")
                        data[pair.Key] = Support.CopyTag(pair.Value);
                }
            }
            var identity = entry.Get("Id") ?? data.Get("id");
            if (identity == null)
                throw new NbtException(
                    $"{what}: entity at ({pos[0]}, {pos[1]}, {pos[2]}) has no Id");
            data["id"] = new TagString(Tag.AsString(identity));
            var stored = new TagList(TagId.Double);
            foreach (double value in pos)
                stored.Add(new TagDouble(value));
            data["Pos"] = stored;
            Entities.Add(data);
        }

        private void LoadV2(TagCompound body, string path)
        {
            string what = Path.GetFileName(path);
            LoadPalette(body.Get("Palette"), body.Get("BlockData"), what);
            if (body.Get("BlockEntities") is TagList blockEntities)
            {
                foreach (var entry in blockEntities)
                    LoadBlockEntity((TagCompound)entry, what);
            }
            if (body.Get("Entities") is TagList entities)
            {
                foreach (var entry in entities)
                    LoadEntity((TagCompound)entry, what);
            }
            if (body.Get("Metadata") is TagCompound metadata)
            {
                var kept = (TagCompound)Support.CopyTag(metadata);
                // WorldEdit keeps the copy origin here in version 2; it is
                // this module's Offset, not opaque metadata.
                var offsets = new[] { "X", "Y", "Z" }
                    .Select(axis => kept.Get("WEOffset" + axis)).ToArray();
                foreach (string axis in new[] { "X", "Y", "Z" })
                    kept.Remove("WEOffset" + axis);
                if (offsets.All(value => value != null))
                    Offset = (Tag.AsInt(offsets[0]!), Tag.AsInt(offsets[1]!),
                              Tag.AsInt(offsets[2]!));
                if (kept.Count > 0)
                    Metadata = kept;
            }
            // Version 2's Offset is written as absolute world coordinates by
            // WorldEdit, against the spec's wording; nothing sane can be done
            // with it on a machine that was not there, so it is ignored — as
            // WorldEdit's own reader ignores it.
            if (body.Get("BiomeData") != null)
                Had2dBiomes = true;
        }

        private void LoadV3(TagCompound body, string path)
        {
            string what = Path.GetFileName(path);
            if (body.Get("Blocks") is not TagCompound blocks)
                throw new NbtException($"{what}: version 3 file has no Blocks container");
            LoadPalette(blocks.Get("Palette"), blocks.Get("Data"), what);
            if (blocks.Get("BlockEntities") is TagList blockEntities)
            {
                foreach (var entry in blockEntities)
                    LoadBlockEntity((TagCompound)entry, what);
            }
            if (body.Get("Entities") is TagList entities)
            {
                foreach (var entry in entities)
                    LoadEntity((TagCompound)entry, what);
            }
            if (body.Get("Offset") is Tag offsetTag
                && PositionInts(offsetTag) is int[] offset && offset.Length == 3)
                Offset = (offset[0], offset[1], offset[2]);
            if (body.Get("Metadata") is TagCompound metadata && metadata.Count > 0)
                Metadata = (TagCompound)Support.CopyTag(metadata);
            if (body.Get("Biomes") is TagCompound biomes)
            {
                if (biomes.Get("Palette") is not TagCompound palette
                    || biomes.Get("Data") is not TagByteArray data)
                    throw new NbtException(
                        $"{what}: Biomes container missing Palette or Data");
                var entries = new string?[palette.Count];
                foreach (var pair in palette)
                {
                    int index = Tag.AsInt(pair.Value);
                    if (index < 0 || index >= entries.Length || entries[index] != null)
                        throw new NbtException(
                            $"{what}: biome palette index {index} for '{pair.Key}' is "
                            + "out of range or duplicated");
                    entries[index] = pair.Key;
                }
                int[] values = Support.ReadVarints(data.Values, Volume,
                                                   $"{what}: biome data");
                int top = entries.Length;
                foreach (int value in values)
                {
                    if (value >= top)
                        throw new NbtException(
                            $"{what}: biome data refers to palette entry {value} "
                            + $"of {top}");
                }
                _biomePalette = entries.Select(entry => entry!).ToList();
                _biomeLookup = new Dictionary<string, int>(StringComparer.Ordinal);
                for (int i = 0; i < _biomePalette.Count; i++)
                {
                    if (!_biomeLookup.ContainsKey(_biomePalette[i]))
                        _biomeLookup[_biomePalette[i]] = i;
                }
                _biomes = values;
            }
        }

        /// <summary>Write a .schem file, gzip-compressed, atomically.
        /// version=3 (the default) is the current Sponge spec, what WorldEdit
        /// 7.3+ writes; version=2 is readable by older tooling but cannot
        /// hold biome data — saving a schematic that carries biomes as
        /// version 2 refuses rather than dropping them.</summary>
        /// <summary>Write a schematic file, gzip-compressed, atomically --
        /// the twin of save. format is "sponge", "structure" or "litematic",
        /// and unless given is taken from the extension: .nbt a structure,
        /// .litematic a Litematica schematic, anything else a .schem.</summary>
        public void Save(string path, int version = 3, string? format = null)
        {
            format ??= FormatOf(path);
            if (format != "sponge")
            {
                FileSafety.AtomicWrite(path, ToBytes(version, format));
                return;
            }
            string name;
            TagCompound root;
            if (version == 3)
            {
                name = "";
                root = new TagCompound { ["Schematic"] = BuildV3() };
            }
            else if (version == 2)
            {
                name = "Schematic";
                root = BuildV2();
            }
            else
            {
                throw new NbtException(
                    $"unknown .schem container version {version}; use 2 or 3");
            }
            FileSafety.AtomicWrite(path, Compression.GzipCompress(
                Codec.Encode(root, name)));
        }

        private static string FormatOf(string path)
        {
            string suffix = System.IO.Path.GetExtension(path).ToLowerInvariant();
            return suffix == ".nbt" ? "structure" : suffix == ".litematic" ? "litematic" : "sponge";
        }

        /// <summary>Serialize without touching the filesystem — the same
        /// bytes Save writes.</summary>
        public byte[] ToBytes(int version = 3, string format = "sponge")
        {
            if (format == "structure" || format == "litematic")
            {
                if (HasBiomes)
                    throw new NbtException(
                        $"a {format} file has nowhere to keep biomes; save as a "
                        + ".schem (version 3), or drop the biomes");
                var root = format == "structure" ? Structure.StructureRoot(this) : Litematic.LitematicRoot(this);
                return Compression.GzipCompress(Codec.Encode(root, ""));
            }
            if (format != "sponge")
                throw new NbtException(
                    $"unknown schematic format '{format}'; use sponge, structure or litematic");
            if (version == 3)
                return Compression.GzipCompress(Codec.Encode(
                    new TagCompound { ["Schematic"] = BuildV3() }, ""));
            if (version == 2)
                return Compression.GzipCompress(Codec.Encode(BuildV2(), "Schematic"));
            throw new NbtException(
                $"unknown .schem container version {version}; use 2 or 3");
        }

        private TagCompound PaletteTag()
        {
            var tag = new TagCompound();
            for (int i = 0; i < _palette.Count; i++)
                tag[_palette[i].ToString()] = new TagInt(i);
            return tag;
        }

        private TagCompound CommonHeader(int version)
        {
            var body = new TagCompound
            {
                ["Version"] = new TagInt(version),
                ["DataVersion"] = new TagInt(DataVersion),
                ["Width"] = Support.UnsignedShort(_width, "Width"),
                ["Height"] = Support.UnsignedShort(_height, "Height"),
                ["Length"] = Support.UnsignedShort(_length, "Length"),
            };
            return body;
        }

        private TagList BlockEntityEntries(int version)
        {
            var entries = new TagList(TagId.Compound);
            foreach (var pair in BlockEntities.OrderBy(pair => pair.Key))
            {
                var (x, y, z) = pair.Key;
                var data = pair.Value;
                var identity = new TagString(Tag.AsString(data["id"]));
                var pos = new TagIntArray(new[] { x, y, z });
                var payload = new TagCompound();
                foreach (var field in data)
                {
                    if (field.Key != "id")
                        payload[field.Key] = Support.CopyTag(field.Value);
                }
                if (version == 3)
                {
                    entries.Add(new TagCompound
                    {
                        ["Id"] = identity, ["Pos"] = pos, ["Data"] = payload,
                    });
                }
                else
                {
                    payload["Id"] = identity;
                    payload["Pos"] = pos;
                    entries.Add(payload);
                }
            }
            return entries;
        }

        private TagList EntityEntries(int version)
        {
            var entries = new TagList(TagId.Compound);
            foreach (var data in Entities)
            {
                string name = data.Get("id") is Tag stored ? Tag.AsString(stored) : "";
                if (name.Length == 0)
                    throw new NbtException("an entity in this schematic has no 'id'");
                var identity = new TagString(name);
                var pos = new TagList(TagId.Double);
                foreach (var value in (TagList)data["Pos"])
                    pos.Add(new TagDouble(Tag.AsDouble(value)));
                if (version == 3)
                {
                    var payload = new TagCompound();
                    foreach (var field in data)
                    {
                        if (field.Key != "id" && field.Key != "Pos")
                            payload[field.Key] = Support.CopyTag(field.Value);
                    }
                    entries.Add(new TagCompound
                    {
                        ["Id"] = identity, ["Pos"] = pos, ["Data"] = payload,
                    });
                }
                else
                {
                    var entry = new TagCompound();
                    foreach (var field in data)
                    {
                        if (field.Key != "id")
                            entry[field.Key] = Support.CopyTag(field.Value);
                    }
                    entry["Id"] = identity;
                    entry["Pos"] = pos;
                    entries.Add(entry);
                }
            }
            return entries;
        }

        private TagCompound BuildV2()
        {
            if (HasBiomes)
                throw new NbtException(
                    "version 2 stores one biome per column, not per cell; saving "
                    + "this schematic's 3D biome data there would flatten it. Save "
                    + "as version=3, or drop the biomes.");
            var body = CommonHeader(2);
            var metadata = Metadata != null
                ? (TagCompound)Support.CopyTag(Metadata) : new TagCompound();
            metadata["WEOffsetX"] = new TagInt(Offset.X);
            metadata["WEOffsetY"] = new TagInt(Offset.Y);
            metadata["WEOffsetZ"] = new TagInt(Offset.Z);
            body["Metadata"] = metadata;
            body["Palette"] = PaletteTag();
            body["PaletteMax"] = new TagInt(_palette.Count);
            body["BlockData"] = new TagByteArray(Support.WriteVarints(_blocks));
            if (BlockEntities.Count > 0)
                body["BlockEntities"] = BlockEntityEntries(2);
            if (Entities.Count > 0)
                body["Entities"] = EntityEntries(2);
            return body;
        }

        private TagCompound BuildV3()
        {
            var body = CommonHeader(3);
            body["Offset"] = new TagIntArray(new[] { Offset.X, Offset.Y, Offset.Z });
            if (Metadata != null)
                body["Metadata"] = Support.CopyTag(Metadata);
            var blocks = new TagCompound
            {
                ["Palette"] = PaletteTag(),
                ["Data"] = new TagByteArray(Support.WriteVarints(_blocks)),
            };
            if (BlockEntities.Count > 0)
                blocks["BlockEntities"] = BlockEntityEntries(3);
            body["Blocks"] = blocks;
            if (HasBiomes)
            {
                var palette = new TagCompound();
                for (int i = 0; i < _biomePalette!.Count; i++)
                    palette[_biomePalette[i]] = new TagInt(i);
                body["Biomes"] = new TagCompound
                {
                    ["Palette"] = palette,
                    ["Data"] = new TagByteArray(Support.WriteVarints(_biomes!)),
                };
            }
            if (Entities.Count > 0)
                body["Entities"] = EntityEntries(3);
            return body;
        }
    }
}
