// Twin of heaton_voxel/schem/transforms.py: rotation and mirroring — the
// direction vocabulary.
//
// The geometry is pure arithmetic, but rewriting block states — facing=north
// becoming facing=east — takes a vocabulary of the game's direction-bearing
// properties. That vocabulary is game knowledge, kept here in one place, and
// it is VALUE-GUARDED: a rule fires only when both the property name and its
// value match the game's conventions, so unknown states pass through
// untouched. Any property that then still LOOKS directional is reported on
// the result's TransformWarnings rather than silently left behind.
//
// Conventions verified against real saves: sign/banner `rotation` runs
// 0=south, 4=west, 8=north, 12=east; hanging entities store `Facing` (item
// frames: the 3D Direction order down,up,north,south,west,east) or `facing`
// (paintings: the 2D order south,west,north,east).

using System;
using System.Collections.Generic;
using System.Linq;
using HeatonVoxel.Blocks;
using HeatonVoxel.Nbt;

namespace HeatonVoxel.Schem
{
    /// <summary>Maps a point/cell/vector through the transform.</summary>
    public delegate (double X, double Y, double Z) PointMap(double x, double y, double z);

    public delegate (int X, int Y, int Z) CellMap(int x, int y, int z);

    public delegate double YawMap(double yaw);

    /// <summary>The direction vocabulary. Public for the same reason
    /// Python's schem package re-exports its private tables: the conformance
    /// corpus verifies these rules directly, so what ships is exactly what
    /// was checked. Not part of the everyday API — use Schematic.Rotated and
    /// Schematic.Flipped.</summary>
    public static class Transforms
    {
        private static Dictionary<string, string> Map(params string[] pairs)
        {
            var map = new Dictionary<string, string>(StringComparer.Ordinal);
            for (int i = 0; i < pairs.Length; i += 2)
                map[pairs[i]] = pairs[i + 1];
            return map;
        }

        /// <summary>One quarter turn clockwise seen from above: north faces
        /// become east.</summary>
        public static readonly Dictionary<string, string> DirCw =
            Map("north", "east", "east", "south", "south", "west", "west", "north");

        private static readonly Dictionary<string, string> AxisCw = Map("x", "z", "z", "x");

        private static readonly Dictionary<string, string> RailCw = Map(
            "north_south", "east_west",
            "east_west", "north_south",
            "ascending_north", "ascending_east",
            "ascending_east", "ascending_south",
            "ascending_south", "ascending_west",
            "ascending_west", "ascending_north",
            "north_east", "south_east",
            "south_east", "south_west",
            "south_west", "north_west",
            "north_west", "north_east");

        private static readonly Dictionary<string, Dictionary<string, string>> DirMirror =
            new Dictionary<string, Dictionary<string, string>>(StringComparer.Ordinal)
            {
                ["x"] = Map("east", "west", "west", "east"),
                ["z"] = Map("north", "south", "south", "north"),
                ["y"] = Map("up", "down", "down", "up"),
            };

        /// <summary>A vertical flip swaps a block's <c>up</c> and
        /// <c>down</c> <em>faces</em>, and only a block that has both has
        /// faces to swap: a mushroom block, a chorus plant, glow lichen, a
        /// sculk vein, a resin clump. A lone <c>up</c> is something else —
        /// a wall's post flag, the one face a vine hangs by, which side of
        /// a fire is burning — and has no <c>down</c> to become (34 blocks
        /// in 26.3: the 32 walls, the vine and fire). Renaming it would
        /// lose the property outright, since the game reads a name its
        /// block does not have as absent and takes the default.</summary>
        private static readonly string[] BothFaces = { "up", "down" };

        /// <summary>What a flip has no state to turn into, by axis and
        /// block, where the caller named the block. The hopper is the one
        /// block whose <c>facing</c> takes <c>down</c> and not <c>up</c>
        /// (HopperBlock), so a hopper turned over would be written
        /// <c>facing=up</c>, which the game does not have and loads as the
        /// default; it keeps the facing it has instead. Only a flip along
        /// <c>y</c> has such a case — <c>x</c> and <c>z</c> turn every
        /// state the game has into another one it has, checked state by
        /// state against the game's own BlockState.mirror.</summary>
        private static readonly Dictionary<string, Dictionary<string, HashSet<string>>>
            Unturnable = new Dictionary<string, Dictionary<string, HashSet<string>>>(
                StringComparer.Ordinal)
            {
                ["y"] = new Dictionary<string, HashSet<string>>(StringComparer.Ordinal)
                {
                    ["minecraft:hopper"] = new HashSet<string>(
                        StringComparer.Ordinal) { "facing" },
                },
            };

        private static readonly Dictionary<string, Dictionary<string, string>> RailMirror =
            new Dictionary<string, Dictionary<string, string>>(StringComparer.Ordinal)
            {
                ["x"] = Map(
                    "ascending_east", "ascending_west",
                    "ascending_west", "ascending_east",
                    "north_east", "north_west",
                    "north_west", "north_east",
                    "south_east", "south_west",
                    "south_west", "south_east"),
                ["z"] = Map(
                    "ascending_north", "ascending_south",
                    "ascending_south", "ascending_north",
                    "north_east", "south_east",
                    "south_east", "north_east",
                    "north_west", "south_west",
                    "south_west", "north_west"),
                ["y"] = Map(),
            };

        /// <summary>Any horizontal mirror flips chirality, so a stair
        /// corner's left becomes its right whichever axis it runs along.</summary>
        private static readonly Dictionary<string, string> StairMirror = Map(
            "inner_left", "inner_right", "inner_right", "inner_left",
            "outer_left", "outer_right", "outer_right", "outer_left");

        private static readonly Dictionary<string, string> LeftRight =
            Map("left", "right", "right", "left");

        private static readonly Dictionary<string, string> HalfMirrorY = Map(
            "top", "bottom", "bottom", "top", "upper", "lower", "lower", "upper");

        private static readonly Dictionary<string, string> TopBottom =
            Map("top", "bottom", "bottom", "top");

        private static readonly Dictionary<string, string> FaceMirrorY =
            Map("floor", "ceiling", "ceiling", "floor");

        private static readonly Dictionary<string, string> TrueFalse =
            Map("true", "false", "false", "true");

        /// <summary>Item frames (and shulker AttachFace): the 3D Direction
        /// enum order; down/up are fixed by a horizontal turn.</summary>
        public static readonly Dictionary<int, int> Facing3Cw =
            new Dictionary<int, int> { [2] = 5, [5] = 3, [3] = 4, [4] = 2 };

        public static readonly Dictionary<string, Dictionary<int, int>> Facing3Mirror =
            new Dictionary<string, Dictionary<int, int>>(StringComparer.Ordinal)
            {
                ["x"] = new Dictionary<int, int> { [4] = 5, [5] = 4 },
                ["z"] = new Dictionary<int, int> { [2] = 3, [3] = 2 },
                ["y"] = new Dictionary<int, int> { [0] = 1, [1] = 0 },
            };

        /// <summary>Paintings: the 2D order south, west, north, east — one
        /// step is a quarter turn clockwise.</summary>
        public static readonly Dictionary<int, int> Facing2Cw =
            new Dictionary<int, int> { [0] = 1, [1] = 2, [2] = 3, [3] = 0 };

        public static readonly Dictionary<string, Dictionary<int, int>> Facing2Mirror =
            new Dictionary<string, Dictionary<int, int>>(StringComparer.Ordinal)
            {
                ["x"] = new Dictionary<int, int> { [1] = 3, [3] = 1 },
                ["z"] = new Dictionary<int, int> { [0] = 2, [2] = 0 },
                ["y"] = new Dictionary<int, int>(),
            };

        /// <summary>Property names the transforms understand. Anything
        /// outside this set whose value is a bare direction word gets
        /// flagged, not guessed at.</summary>
        private static readonly HashSet<string> TransformKeys =
            new HashSet<string>(StringComparer.Ordinal)
            {
                "facing", "axis", "rotation", "shape", "type", "hinge", "half",
                "face", "orientation", "vertical_direction", "attachment", "hanging",
                "north", "south", "east", "west", "up", "down",
            };

        private static readonly HashSet<string> DirectionWords =
            new HashSet<string>(StringComparer.Ordinal)
            {
                "north", "south", "east", "west", "up", "down",
            };

        public static double WrapDegrees(double value)
        {
            // Python's % is floored, so a negative remainder never appears;
            // C#'s % is truncated and does return one. Fold it back.
            double wrapped = (value + 180.0) % 360.0;
            if (wrapped < 0)
                wrapped += 360.0;
            return wrapped - 180.0;
        }

        /// <summary>A sign/banner `rotation` step (mod 16), guarded against
        /// junk — a non-numeric value passes through.</summary>
        private static string RotateInt(string value, Func<int, int> step)
        {
            if (!int.TryParse(value, System.Globalization.NumberStyles.AllowLeadingSign,
                              System.Globalization.CultureInfo.InvariantCulture,
                              out int number))
                return value;
            int turned = step(number) % 16;
            if (turned < 0)
                turned += 16;  // floored modulo, as in the reference
            return turned.ToString(System.Globalization.CultureInfo.InvariantCulture);
        }

        private static string Lookup(Dictionary<string, string> table, string value) =>
            table.TryGetValue(value, out var mapped) ? mapped : value;

        /// <summary>One clockwise quarter turn of a state's properties.</summary>
        public static PropertyMap PropertiesRotatedCw(PropertyMap properties)
        {
            var output = new PropertyMap();
            foreach (var pair in properties)
            {
                // per-face booleans: north=true -> east=true
                string key = Lookup(DirCw, pair.Key);
                string value = pair.Value;
                switch (key)
                {
                    case "facing":
                        value = Lookup(DirCw, value);
                        break;
                    case "axis":
                        value = Lookup(AxisCw, value);
                        break;
                    case "rotation":
                        value = RotateInt(value, r => r + 4);
                        break;
                    case "shape":
                        value = Lookup(RailCw, value);  // stair shapes rotate with facing
                        break;
                    case "orientation":
                        value = string.Join("_", value.Split('_')
                            .Select(part => Lookup(DirCw, part)));
                        break;
                }
                output[key] = value;
            }
            return output;
        }

        public static PropertyMap PropertiesMirrored(PropertyMap properties, string axis)
            => PropertiesMirrored(properties, axis, null);

        /// <summary>A state's properties mirrored along <paramref name="axis"/>.
        /// Mirroring along <c>y</c> swaps the <c>up</c> and <c>down</c>
        /// faces only of a block that carries both, since a lone <c>up</c>
        /// has no <c>down</c> to become; <paramref name="block"/>, the id
        /// the properties belong to, names the handful of blocks a vertical
        /// flip cannot turn at all, whose property is left as it
        /// stands.</summary>
        public static PropertyMap PropertiesMirrored(PropertyMap properties, string axis,
                                                     string? block)
        {
            var pairs = DirMirror[axis];
            bool horizontal = axis == "x" || axis == "z";
            // Per-face keys swap sides, vertically only where there are two
            // to swap.
            bool swapFaces = horizontal
                || BothFaces.All(face => properties.Get(face) is not null);
            HashSet<string>? unturnable = null;
            if (block is not null && Unturnable.TryGetValue(axis, out var byBlock))
                byBlock.TryGetValue(block, out unturnable);
            var output = new PropertyMap();
            foreach (var entry in properties)
            {
                string key = swapFaces ? Lookup(pairs, entry.Key) : entry.Key;
                string value = entry.Value;
                if (unturnable is not null && unturnable.Contains(key))
                {
                    output[key] = value;
                    continue;
                }
                if (key == "facing")
                {
                    value = Lookup(pairs, value);
                }
                else if (key == "rotation" && horizontal)
                {
                    int origin = axis == "x" ? 16 : 8;
                    value = RotateInt(value, r => origin - r);
                }
                else if (key == "shape" && horizontal)
                {
                    value = RailMirror[axis].TryGetValue(value, out var railed)
                        ? railed : Lookup(StairMirror, value);
                }
                else if (key == "hinge" && horizontal)
                {
                    value = Lookup(LeftRight, value);
                }
                else if (key == "type")
                {
                    // chest halves / slab halves only
                    value = Lookup(horizontal ? LeftRight : TopBottom, value);
                }
                else if (key == "orientation")
                {
                    value = OrientationMirrored(value, pairs, horizontal);
                }
                else if (!horizontal)
                {
                    if (key == "half")
                        value = Lookup(HalfMirrorY, value);
                    else if (key == "face" || key == "attachment")
                        value = Lookup(FaceMirrorY, value);
                    else if (key == "vertical_direction")
                        value = Lookup(pairs, value);
                    else if (key == "hanging")
                        value = Lookup(TrueFalse, value);
                }
                output[key] = value;
            }
            return output;
        }

        /// <summary>A crafter's or jigsaw's <c>orientation</c> —
        /// <c>&lt;front&gt;_&lt;top&gt;</c> — mirrored. A horizontal mirror
        /// turns both halves; a vertical one turns the front alone. The
        /// game's FrontAndTop holds twelve orientations and not one of them
        /// tops downward, so a crafter fronting a horizontal direction has
        /// no upside-down state to become and keeps the one it has, while
        /// one fronting up or down turns over cleanly.</summary>
        private static string OrientationMirrored(string value,
                                                  Dictionary<string, string> pairs,
                                                  bool horizontal)
        {
            string[] parts = value.Split('_');
            if (horizontal)
                return string.Join("_", parts.Select(part => Lookup(pairs, part)));
            parts[0] = Lookup(pairs, parts[0]);
            return string.Join("_", parts);
        }

        public static BlockState MapState(BlockState state,
                                            Func<PropertyMap, PropertyMap> mapper)
        {
            var properties = state.Properties;
            if (properties.Count == 0)
                return state;
            var mapped = mapper(properties);
            if (Same(properties, mapped))
                return state;  // untouched states keep their original palette bytes
            return new BlockState(state.Name, mapped);
        }

        private static bool Same(PropertyMap left, PropertyMap right)
        {
            if (left.Count != right.Count)
                return false;
            foreach (var pair in left)
            {
                if (right.Get(pair.Key) is not string value
                    || !string.Equals(value, pair.Value, StringComparison.Ordinal))
                    return false;
            }
            return true;
        }

        /// <summary>States carrying direction-looking properties the
        /// vocabulary misses.</summary>
        public static List<string> DirectionalLeftovers(IEnumerable<BlockState> palette)
        {
            var warnings = new List<string>();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var state in palette)
            {
                foreach (var pair in state.Properties)
                {
                    if (!TransformKeys.Contains(pair.Key)
                        && DirectionWords.Contains(pair.Value))
                    {
                        string message =
                            $"{state.Name}: property {pair.Key}={pair.Value} looks "
                            + "directional but is outside the transform vocabulary; "
                            + "left as-is";
                        if (seen.Add(message))
                            warnings.Add(message);
                    }
                }
            }
            return warnings;
        }

        public static Dictionary<int, int> ComposeByteTable(
            Dictionary<int, int> table, int times)
        {
            var output = new Dictionary<int, int>();
            for (int source = 0; source < 6; source++)
            {
                int value = source;
                for (int i = 0; i < times; i++)
                    value = table.TryGetValue(value, out int next) ? next : value;
                if (value != source)
                    output[source] = value;
            }
            return output;
        }

        /// <summary>Rotate or mirror one entity compound, in place.
        /// Recurses into passengers.</summary>
        public static void TransformEntity(
            TagCompound tag, PointMap point, CellMap cell, YawMap yaw, double pitchSign,
            Dictionary<int, int> facing3, Dictionary<int, int> facing2, PointMap vector)
        {
            if (tag.Get("Pos") is TagList pos && pos.Count == 3)
            {
                var (x, y, z) = point(Tag.AsDouble(pos[0]), Tag.AsDouble(pos[1]),
                                      Tag.AsDouble(pos[2]));
                pos[0] = new TagDouble(x);
                pos[1] = new TagDouble(y);
                pos[2] = new TagDouble(z);
            }
            if (tag.Get("Rotation") is TagList rotation && rotation.Count == 2)
            {
                // Keep each entry's own tag type, as the reference does.
                rotation[0] = SameNumericKind(
                    rotation[0], WrapDegrees(yaw(Tag.AsDouble(rotation[0]))));
                rotation[1] = SameNumericKind(
                    rotation[1], pitchSign * Tag.AsDouble(rotation[1]));
            }
            if (tag.Get("Motion") is TagList motion && motion.Count == 3)
            {
                var (x, y, z) = vector(Tag.AsDouble(motion[0]), Tag.AsDouble(motion[1]),
                                       Tag.AsDouble(motion[2]));
                motion[0] = new TagDouble(x);
                motion[1] = new TagDouble(y);
                motion[2] = new TagDouble(z);
            }

            if (tag.Get("block_pos") is TagIntArray block && block.Values.Length == 3)
            {
                var (x, y, z) = cell(block.Values[0], block.Values[1], block.Values[2]);
                tag["block_pos"] = new TagIntArray(new[] { x, y, z });
            }
            if (tag.ContainsKey("TileX") && tag.ContainsKey("TileY")
                && tag.ContainsKey("TileZ"))
            {
                var (x, y, z) = cell(Tag.AsInt(tag["TileX"]), Tag.AsInt(tag["TileY"]),
                                     Tag.AsInt(tag["TileZ"]));
                tag["TileX"] = new TagInt(x);
                tag["TileY"] = new TagInt(y);
                tag["TileZ"] = new TagInt(z);
            }
            foreach (string key in new[] { "leash", "Leash" })
            {
                if (tag.Get(key) is TagCompound leash && leash.ContainsKey("X")
                    && leash.ContainsKey("Y") && leash.ContainsKey("Z"))
                {
                    var (x, y, z) = cell(Tag.AsInt(leash["X"]), Tag.AsInt(leash["Y"]),
                                         Tag.AsInt(leash["Z"]));
                    leash["X"] = new TagInt(x);
                    leash["Y"] = new TagInt(y);
                    leash["Z"] = new TagInt(z);
                }
            }
            if (tag.Get("sleeping_pos") is TagIntArray sleeping
                && sleeping.Values.Length == 3)
            {
                var (x, y, z) = cell(sleeping.Values[0], sleeping.Values[1],
                                     sleeping.Values[2]);
                tag["sleeping_pos"] = new TagIntArray(new[] { x, y, z });
            }

            foreach (var (name, table) in new[]
                     {
                         ("Facing", facing3), ("AttachFace", facing3), ("facing", facing2),
                     })
            {
                // Integer tags only: a painting's `facing` is a byte, but a
                // block entity's `facing` may be a string, which is not ours.
                if (tag.Get(name) is Tag stored && IsIntegerTag(stored)
                    && table.TryGetValue(Tag.AsInt(stored), out int mapped))
                    tag[name] = SameIntegerKind(stored, mapped);
            }

            if (tag.Get("Passengers") is TagList passengers)
            {
                foreach (var passenger in passengers)
                {
                    if (passenger is TagCompound compound)
                        TransformEntity(compound, point, cell, yaw, pitchSign,
                                        facing3, facing2, vector);
                }
            }
        }

        private static bool IsIntegerTag(Tag tag) =>
            tag is TagByte || tag is TagShort || tag is TagInt || tag is TagLong;

        private static Tag SameIntegerKind(Tag original, int value) => original switch
        {
            TagByte => new TagByte((sbyte)value),
            TagShort => new TagShort((short)value),
            TagLong => new TagLong(value),
            _ => new TagInt(value),
        };

        private static Tag SameNumericKind(Tag original, double value) => original switch
        {
            TagDouble => new TagDouble(value),
            TagByte => new TagByte((sbyte)value),
            TagShort => new TagShort((short)value),
            TagInt => new TagInt((int)value),
            TagLong => new TagLong((long)value),
            _ => new TagFloat((float)value),
        };
    }
}
