// Twin of heaton_voxel/connect.py: recompute neighbor-derived block states.
//
// Fences, walls, glass panes, iron bars, fence gates and stairs store state
// the game derives from their neighbors — a fence's north=true, a wall's
// post flag, a stair's corner shape. Vanilla recomputes these only when a
// block update fires, so blocks written directly into a save render exactly
// as stored: bare fences stand as disconnected posts and roof corners stay
// straight until something nudges them.
//
// The rules are game knowledge, like render's colors: the connection
// families and the "which blocks count as solid" predicate were validated
// against vanilla-placed blocks in a real save (0 disagreements in 22,616),
// and anything unknown errs toward NOT connecting — a post where a stub
// might have been, never a stub jutting into nothing. What a wall reads of
// the block above it is not a name at all: it is the game's own answer,
// generated from its shapes into WallCover.generated.cs.

using System;
using System.Collections.Generic;
using System.Linq;
using HeatonVoxel.Blocks;
using HeatonVoxel.Nbt;
using HeatonVoxel.World;

namespace HeatonVoxel
{
    /// <summary>Supplies neighboring states; null for unloaded.</summary>
    public delegate BlockState? BlockLookup(int x, int y, int z);

    public static class Connect
    {
        // -- the vocabulary ------------------------------------------------

        private static readonly (string Name, int Dx, int Dz)[] Directions =
        {
            ("north", 0, -1), ("south", 0, 1), ("west", -1, 0), ("east", 1, 0),
        };

        private static readonly Dictionary<string, (int Dx, int Dz)> Vector =
            new Dictionary<string, (int, int)>(StringComparer.Ordinal)
            {
                ["north"] = (0, -1),
                ["south"] = (0, 1),
                ["west"] = (-1, 0),
                ["east"] = (1, 0),
            };

        private static readonly Dictionary<string, string> Opposite =
            new Dictionary<string, string>(StringComparer.Ordinal)
            {
                ["north"] = "south",
                ["south"] = "north",
                ["west"] = "east",
                ["east"] = "west",
            };

        /// <summary>Counter-clockwise seen from above, the game's
        /// Direction.getCounterClockWise.</summary>
        private static readonly Dictionary<string, string> Ccw =
            new Dictionary<string, string>(StringComparer.Ordinal)
            {
                ["north"] = "west",
                ["west"] = "south",
                ["south"] = "east",
                ["east"] = "north",
            };

        // The game's Block.isExceptionForConnection (26.3, read with javap):
        // what a fence, a wall, a pane or bars never join as a solid
        // neighbor, however full its faces -- leaves, the barrier, the
        // pumpkins, the jack o'lantern, the melon and the shulker boxes.
        // Every one of those predicates asks it before the face, so these
        // are never sturdy here.
        private static readonly HashSet<string> NoConnectionExact =
            new HashSet<string>(StringComparer.Ordinal)
            {
                "minecraft:barrier", "minecraft:pumpkin", "minecraft:carved_pumpkin",
                "minecraft:jack_o_lantern", "minecraft:melon",
            };

        private static readonly string[] NoConnectionSuffixes =
        {
            "_leaves", "shulker_box",
        };

        private static bool EndsWithAny(string name, string[] suffixes)
        {
            foreach (string suffix in suffixes)
            {
                if (name.EndsWith(suffix, StringComparison.Ordinal))
                    return true;
            }
            return false;
        }

        private static bool NoConnection(string name) =>
            NoConnectionExact.Contains(name) || EndsWithAny(name, NoConnectionSuffixes);

        private static bool IsFence(string name) =>
            name.EndsWith("_fence", StringComparison.Ordinal);

        private static bool IsWoodenFence(string name) =>
            IsFence(name) && name.IndexOf("nether_brick", StringComparison.Ordinal) < 0;

        private static bool IsNetherFence(string name) =>
            IsFence(name) && name.IndexOf("nether_brick", StringComparison.Ordinal) >= 0;

        private static bool IsGate(string name) =>
            name.EndsWith("_fence_gate", StringComparison.Ordinal);

        private static bool IsWall(string name) =>
            name.EndsWith("_wall", StringComparison.Ordinal);

        private static bool IsPane(string name) =>
            name.EndsWith("_pane", StringComparison.Ordinal)
            || string.Equals(name, "minecraft:iron_bars", StringComparison.Ordinal);

        private static bool IsStairs(string name) =>
            name.EndsWith("_stairs", StringComparison.Ordinal);

        /// <summary>A gate's bar runs perpendicular to its facing.</summary>
        private static bool GateSpans(BlockState gate, string direction)
        {
            string facing = gate.GetProperty("facing") ?? "north";
            if (facing == "east" || facing == "west")
                return direction == "north" || direction == "south";
            return direction == "east" || direction == "west";
        }

        private static bool Candidate(string name, bool stairs) =>
            IsFence(name) || IsWall(name) || IsPane(name) || IsGate(name)
            || (stairs && IsStairs(name));

        // -- the rules -----------------------------------------------------

        /// <summary>Does the neighbor present a full square face back
        /// toward us? direction runs from the connecting block to the
        /// neighbor, so the face that touches is the neighbor's opposite
        /// side, and whether it is full is the game's own answer, generated
        /// from its shapes into ConnectFaces. A stair's full face is its
        /// facing side (shipwreck fences attach to stair backs), read by
        /// that alone since its shape is derived in the same sweep. What the
        /// game excepts from every connection (NoConnection) is never joined
        /// as solid, and a block the game does not have has no full
        /// face.</summary>
        private static bool FaceFull(BlockState neighbor, string direction)
        {
            string name = neighbor.Name;
            if (NoConnection(name))
                return false;
            if (IsStairs(name))
                return (neighbor.GetProperty("facing") ?? "north") == Opposite[direction];
            return (ConnectFaces.FullSides(neighbor) & FaceBit(Opposite[direction])) != 0;
        }

        private static int FaceBit(string side) => side switch
        {
            "north" => ConnectFaces.North,
            "south" => ConnectFaces.South,
            "west" => ConnectFaces.West,
            _ => ConnectFaces.East,
        };

        private static bool FenceSide(string name, BlockState? neighbor, string direction)
        {
            if (neighbor == null)
                return false;
            string other = neighbor.Name;
            if (IsWoodenFence(name))
            {
                if (IsWoodenFence(other))
                    return true;
            }
            else if (IsNetherFence(other))
            {
                return true;
            }
            if (IsGate(other))
                return GateSpans(neighbor, direction);
            return FaceFull(neighbor, direction);
        }

        private static bool WallSide(BlockState? neighbor, string direction)
        {
            if (neighbor == null)
                return false;
            string other = neighbor.Name;
            if (IsWall(other) || IsPane(other))
                return true;
            if (IsGate(other))
                return GateSpans(neighbor, direction);
            return FaceFull(neighbor, direction);
        }

        private static bool PaneSide(BlockState? neighbor, string direction)
        {
            if (neighbor == null)
                return false;
            string other = neighbor.Name;
            return IsPane(other) || IsWall(other) || FaceFull(neighbor, direction);
        }

        /// <summary>The game's StairBlock.getStairShape, from stored states.
        /// neighborAt(direction) returns the horizontally adjacent state at
        /// the stair's own level. Chirality was validated against
        /// vanilla-placed corner stairs in a real save.</summary>
        private static string StairShape(BlockState state,
                                         Func<string, BlockState?> neighborAt)
        {
            string facing = state.GetProperty("facing") ?? "north";
            string half = state.GetProperty("half") ?? "bottom";

            bool FlankDiffers(string direction)
            {
                var n = neighborAt(direction);
                if (n == null || !IsStairs(n.Name))
                    return true;
                return (n.GetProperty("facing") ?? "north") != facing
                       || (n.GetProperty("half") ?? "bottom") != half;
            }

            var behind = neighborAt(facing);
            if (behind != null && IsStairs(behind.Name)
                && (behind.GetProperty("half") ?? "bottom") == half)
            {
                string other = behind.GetProperty("facing") ?? "north";
                if ((other == Ccw[facing] || other == Opposite[Ccw[facing]])
                    && FlankDiffers(Opposite[other]))
                    return other == Ccw[facing] ? "outer_left" : "outer_right";
            }

            var front = neighborAt(Opposite[facing]);
            if (front != null && IsStairs(front.Name)
                && (front.GetProperty("half") ?? "bottom") == half)
            {
                string other = front.GetProperty("facing") ?? "north";
                if ((other == Ccw[facing] || other == Opposite[Ccw[facing]])
                    && FlankDiffers(other))
                    return other == Ccw[facing] ? "inner_left" : "inner_right";
            }

            return "straight";
        }

        /// <summary>The side strip of a wall's own test each direction
        /// names (<see cref="WallCover"/>).</summary>
        private static readonly Dictionary<string, int> SideBit =
            new Dictionary<string, int>(StringComparer.Ordinal)
            {
                ["north"] = WallCover.North,
                ["east"] = WallCover.East,
                ["south"] = WallCover.South,
                ["west"] = WallCover.West,
            };

        /// <summary>What the block over a wall shows it, as a
        /// <see cref="WallCover.Of"/> code — the game's own answer, from its
        /// collision shapes and its wall_post_override tag, not from a name.
        /// Nothing where there is no block to read (an unloaded chunk, past
        /// the top of the world), which the game reads as air; the post's
        /// center alone for a block the table does not know, which runs no
        /// side tall and raises a post over any run: a post where a stub
        /// might have been, never a wall reaching up to nothing.</summary>
        private static int Cover(BlockState? above)
        {
            if (above == null)
                return 0;
            return WallCover.Of(above) ?? WallCover.Post;
        }

        /// <summary>The game's WallBlock.shouldRaisePost, in its own order: a
        /// wall above with its post up raises this one's outright; then any
        /// end, lone block, T, L or cross that is lopsided in what it joins
        /// raises one; a run tall on both sides of an axis — under a solid
        /// block, or the wall above it — runs flush; and otherwise the post
        /// rises only for what is in wall_post_override or covers the post's
        /// center (a lantern, a pot), so a straight run under a top slab, a
        /// leaf or open sky is flush.</summary>
        private static bool RaisesPost(PropertyMap sides, BlockState? above, int cover)
        {
            if (above != null && IsWall(above.Name))
            {
                string? up = above.GetProperty("up");
                if (up == null)
                {
                    foreach (var pair in BlockDefaults.DefaultProperties(above.Name))
                        if (pair.Key == "up")
                            up = pair.Value;
                }
                if (up == "true")
                    return true;
            }
            bool north = sides["north"] == "none", east = sides["east"] == "none";
            bool south = sides["south"] == "none", west = sides["west"] == "none";
            if ((north && east && south && west) || north != south || east != west)
                return true;
            if ((sides["north"] == "tall" && sides["south"] == "tall")
                || (sides["east"] == "tall" && sides["west"] == "tall"))
                return false;
            return (cover & (WallCover.Override | WallCover.Post)) != 0;
        }

        /// <summary>The state vanilla's neighbor update would produce, or
        /// null when the block is not one whose state is neighbor-derived.
        ///
        /// Shared by <see cref="ConnectBlocks"/>, <see cref="Place"/> and the
        /// conformance corpus, so what ships is exactly what was checked
        /// against the game's own output. (Module-private in Python, but
        /// imported by the corpus generator for the same reason.)</summary>
        public static BlockState? DerivedState(BlockState state, BlockLookup blockAt,
                                               int x, int y, int z, bool stairs = true)
        {
            string name = state.Name;
            bool fence = IsFence(name);
            bool wall = IsWall(name);
            bool pane = IsPane(name);
            bool gate = IsGate(name);
            bool stair = stairs && IsStairs(name);
            if (!(fence || wall || pane || gate || stair))
                return null;
            var props = state.Properties;

            if (fence || wall || pane)
            {
                // What a wall reads of the block above it, as
                // WallBlock.updateShape does: the strips and the post center
                // that block's underside covers, and whether it is in
                // wall_post_override.
                BlockState? above = wall ? blockAt(x, y + 1, z) : null;
                int cover = wall ? Cover(above) : 0;
                foreach (var (direction, dx, dz) in Directions)
                {
                    var n = blockAt(x + dx, y, z + dz);
                    bool linked = fence ? FenceSide(name, n, direction)
                                : wall ? WallSide(n, direction)
                                : PaneSide(n, direction);
                    if (wall)
                    {
                        if (!linked)
                            props[direction] = "none";
                        else if ((cover & SideBit[direction]) != 0)
                            // the block above covers this side, so the wall
                            // runs tall up to it: a solid block, a leaf, a
                            // bottom slab, the side of a wall above joined
                            // the same way
                            props[direction] = "tall";
                        else
                            props[direction] = "low";
                    }
                    else
                    {
                        props[direction] = linked ? "true" : "false";
                    }
                }
                if (wall)
                    props["up"] = RaisesPost(props, above, cover) ? "true" : "false";
            }
            else if (gate)
            {
                string[] beside = GateSpans(state, "north")
                    ? new[] { "north", "south" }
                    : new[] { "west", "east" };
                bool inWall = false;
                foreach (string d in beside)
                {
                    var n = blockAt(x + Vector[d].Dx, y, z + Vector[d].Dz);
                    if (n != null && IsWall(n.Name))
                    {
                        inWall = true;
                        break;
                    }
                }
                props["in_wall"] = inWall ? "true" : "false";
            }
            else  // stair
            {
                props["shape"] = StairShape(state, direction =>
                {
                    var (dx, dz) = Vector[direction];
                    return blockAt(x + dx, y, z + dz);
                });
            }

            return new BlockState(name, props);
        }

        // -- the pass ------------------------------------------------------

        /// <summary>Recompute neighbor-derived states in the box between
        /// two inclusive corners, in any order.
        ///
        /// Nothing else on the state is touched — waterlogged in particular
        /// is placement history the game never derives. Blocks the
        /// vocabulary does not recognize count as "not solid", which errs
        /// toward a free-standing post rather than an arm reaching at
        /// nothing. Give the box a one-block margin around new
        /// construction: neighbors are read beyond the box, but only blocks
        /// inside it are rewritten.</summary>
        public static ConnectResult ConnectBlocks(
            MinecraftSave save, (int X, int Y, int Z) corner1,
            (int X, int Y, int Z) corner2, string? dimension = null,
            bool stairs = true, bool keepLoaded = false)
        {
            var dim = save.Dimension(dimension);
            int x0 = Math.Min(corner1.X, corner2.X), x1 = Math.Max(corner1.X, corner2.X);
            int y0 = Math.Min(corner1.Y, corner2.Y), y1 = Math.Max(corner1.Y, corner2.Y);
            int z0 = Math.Min(corner1.Z, corner2.Z), z1 = Math.Max(corner1.Z, corner2.Z);
            var result = new ConnectResult();

            BlockState? BlockAt(int x, int y, int z)
            {
                ChunkBlocks? accessor;
                try
                {
                    accessor = dim.Blocks(x >> 4, z >> 4);
                }
                catch (NbtException)
                {
                    return null;
                }
                if (accessor == null)
                    return null;
                try
                {
                    return accessor.Get(x & 15, y, z & 15);
                }
                catch (ArgumentOutOfRangeException)
                {
                    return null;
                }
            }

            for (int chunkX = x0 >> 4; chunkX <= (x1 >> 4); chunkX++)
            {
                for (int chunkZ = z0 >> 4; chunkZ <= (z1 >> 4); chunkZ++)
                {
                    ChunkBlocks? accessor;
                    try
                    {
                        accessor = dim.WritableBlocks(chunkX, chunkZ);
                    }
                    catch (NbtException exc)
                    {
                        result.UnreadableChunks.Add(((chunkX, chunkZ), exc.Message));
                        continue;
                    }
                    if (accessor == null)
                    {
                        result.MissingChunks.Add((chunkX, chunkZ));
                        continue;
                    }

                    int baseX = chunkX * 16, baseZ = chunkZ * 16;
                    int lx0 = Math.Max(x0, baseX) - baseX;
                    int lx1 = Math.Min(x1, baseX + 15) - baseX;
                    int lz0 = Math.Max(z0, baseZ) - baseZ;
                    int lz1 = Math.Min(z1, baseZ + 15) - baseZ;

                    // Sections whose palettes hold nothing connectable are
                    // skipped outright, which is nearly all of them;
                    // candidates are visited top-down so a wall reads the
                    // already-settled state of the wall above it when
                    // deciding tall sides and its post.
                    foreach (int sectionY in accessor.Sections.Keys
                                 .OrderByDescending(key => key).ToList())
                    {
                        int baseY = sectionY * 16;
                        if (baseY > y1 || baseY + 15 < y0)
                            continue;
                        var container = accessor.Sections[sectionY].Blocks;
                        if (container == null)
                            continue;
                        var hits = new HashSet<int>();
                        for (int i = 0; i < container.PaletteCount; i++)
                        {
                            if (Candidate(container.PaletteEntry(i).Name, stairs))
                                hits.Add(i);
                        }
                        if (hits.Count == 0)
                            continue;
                        int[] indices = container.Indices();
                        for (int ly = 15; ly >= 0; ly--)
                        {
                            int y = baseY + ly;
                            if (y < y0 || y > y1)
                                continue;
                            int row = ly * 256;
                            for (int lz = lz0; lz <= lz1; lz++)
                            {
                                for (int lx = lx0; lx <= lx1; lx++)
                                {
                                    if (!hits.Contains(indices[row + lz * 16 + lx]))
                                        continue;
                                    int x = baseX + lx, z = baseZ + lz;
                                    var state = container.Get(row + lz * 16 + lx);
                                    var derived = DerivedState(
                                        state, BlockAt, x, y, z, stairs);
                                    result.BlocksScanned++;
                                    if (derived == null || derived.Equals(state))
                                        continue;
                                    accessor.Set(lx, y, lz, derived);
                                    string name = state.Name;
                                    if (IsFence(name)) result.FencesUpdated++;
                                    else if (IsWall(name)) result.WallsUpdated++;
                                    else if (IsPane(name)) result.PanesUpdated++;
                                    else if (IsGate(name)) result.GatesUpdated++;
                                    else result.StairsUpdated++;
                                }
                            }
                            indices = container.Indices();  // a Set may have repacked
                        }
                    }
                    if (!keepLoaded)
                        dim.Release(chunkX, chunkZ);
                }
            }
            return result;
        }
    }

    /// <summary>What a <see cref="Connect.ConnectBlocks"/> pass actually
    /// rewrote. Chunks the box reaches that the world never generated, or
    /// that cannot be read, are listed rather than silently treated as
    /// empty — their blocks keep whatever states they have.</summary>
    public sealed class ConnectResult
    {
        public int FencesUpdated;
        public int WallsUpdated;
        public int PanesUpdated;
        public int GatesUpdated;
        public int StairsUpdated;
        /// <summary>Connectable blocks examined (changed or not).</summary>
        public int BlocksScanned;
        public readonly List<(int X, int Z)> MissingChunks = new List<(int, int)>();
        public readonly List<((int X, int Z) Chunk, string Reason)> UnreadableChunks =
            new List<((int, int), string)>();

        public int TotalUpdated =>
            FencesUpdated + WallsUpdated + PanesUpdated + GatesUpdated + StairsUpdated;

        public bool Complete =>
            MissingChunks.Count == 0 && UnreadableChunks.Count == 0;
    }
}
