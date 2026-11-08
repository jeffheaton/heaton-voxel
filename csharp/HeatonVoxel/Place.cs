// Twin of heaton_voxel/place.py: placing blocks the way the game places
// items.
//
// SetBlock writes exactly the state it is given, which is the right
// primitive for a format library — and the wrong tool for building, because
// vanilla items place more than one block and derive state from the
// surroundings. A bed is two blocks; a door is two blocks; writing one half
// leaves an orphan the game breaks on its next update. A fence written bare
// stands disconnected until something nudges it. A lantern dropped into a
// water source should be waterlogged -- and one dropped into flowing water
// should not, which is the game's own rule (TakesWater, below).
//
// Not simulated: survival placement rules. Nothing checks support or
// reachability — a door placed on air is written faithfully, like every
// other editor write in this toolkit.

using System;
using System.Collections.Generic;
using HeatonVoxel.Blocks;
using HeatonVoxel.Nbt;
using HeatonVoxel.World;

namespace HeatonVoxel
{
    public static class Place
    {
        private static readonly Dictionary<string, (int Dx, int Dz)> Vector =
            new Dictionary<string, (int, int)>(StringComparer.Ordinal)
            {
                ["north"] = (0, -1), ["south"] = (0, 1),
                ["west"] = (-1, 0), ["east"] = (1, 0),
            };

        /// <summary>Two-block plants: lower half at the placed cell, upper
        /// half above. Every DoublePlantBlock whose setPlacedBy sets the
        /// upper half (26.3, javap): the small dripleaf's carries the lower
        /// half's facing, and takes its own water by copyWaterloggedFrom,
        /// which is the cell's own rule here. The pitcher crop's setPlacedBy
        /// sets nothing -- the pod plants one block, and the crop grows its
        /// upper half -- so it is not one of them.</summary>
        private static readonly HashSet<string> TallPlants =
            new HashSet<string>(StringComparer.Ordinal)
            {
                "minecraft:sunflower", "minecraft:lilac", "minecraft:rose_bush",
                "minecraft:peony", "minecraft:tall_grass", "minecraft:large_fern",
                "minecraft:pitcher_plant", "minecraft:small_dripleaf",
                "minecraft:tall_seagrass",
            };

        // Whether the block carries a `waterlogged` property at all, read from
        // the game's own block report (BlockDefaults, generated beside the
        // Python table from one parse) rather than from a list of families:
        // the hand-kept list this replaced missed 98 blocks -- every chain,
        // copper lantern, chest and lightning rod -- and named the bell, which
        // has no such property. A block the table has never heard of displaces
        // the water, the harmless direction to be wrong in.
        private static bool Waterloggable(string name)
        {
            foreach (var pair in BlockDefaults.DefaultProperties(name))
            {
                if (pair.Key == "waterlogged")
                    return true;
            }
            return false;
        }

        // The water a cell holds, as the game's getFluidState reads it (26.3,
        // javap). LiquidBlock maps level 0 to the source, 1-7 to flowing
        // water of amount 8 - level, and every level from 8 up to falling
        // water of amount 8. Flowing and falling water are both
        // Fluids.FLOWING_WATER, another Fluid from the source Fluids.WATER.
        private const string Source = "source";
        private const string Flowing = "flowing";
        private const string Falling = "falling";
        private static readonly HashSet<string> FlowingLevels =
            new HashSet<string>(StringComparer.Ordinal)
            { "1", "2", "3", "4", "5", "6", "7" };
        private static readonly HashSet<string> FallingLevels =
            new HashSet<string>(StringComparer.Ordinal)
            { "8", "9", "10", "11", "12", "13", "14", "15" };

        /// <summary>Blocks the game reads as a water source whatever their
        /// state: each one's getFluidState returns
        /// Fluids.WATER.getSource(false) outright. Every other block holds
        /// water only when it is waterlogged.</summary>
        private static readonly HashSet<string> WaterSourceBlocks =
            new HashSet<string>(StringComparer.Ordinal)
            {
                "minecraft:bubble_column", "minecraft:kelp", "minecraft:kelp_plant",
                "minecraft:seagrass", "minecraft:tall_seagrass",
            };

        private static readonly string[] SourceOnly = { Source };

        /// <summary>Which water a placed block takes, where the game's rule
        /// is not the usual one. Nearly every waterloggable block's
        /// getStateForPlacement sets waterlogged to
        /// fluidState.is(Fluids.WATER) (or isSourceOfType, the same test):
        /// the source alone, SourceOnly, the default. The 26.3 jar's
        /// exceptions: corals (plants, fans and wall fans, live and dead)
        /// and the conduit test is(FluidTags.WATER) &amp;&amp; isFull(), an
        /// amount of 8, which falling water has as well as a source; the
        /// small dripleaf copies isWaterAt (DoublePlantBlock.
        /// copyWaterloggedFrom), any water, flowing included; the light block
        /// has no getStateForPlacement of its own and is placed in its
        /// default state, dry, wherever it goes. A slab of type=double takes
        /// none either, whatever its name: it is a whole block, and
        /// SlabBlock makes one with waterlogged false without asking the
        /// water (Takes).</summary>
        private static readonly Dictionary<string, string[]> TakesWater = BuildTakesWater();

        /// <summary>The campfires (CampfireBlock), whose placement sets lit
        /// to the opposite of the waterlogged it sets: one placed into water
        /// is out.</summary>
        private static readonly HashSet<string> Campfires =
            new HashSet<string>(StringComparer.Ordinal)
            {
                "minecraft:campfire", "minecraft:soul_campfire",
            };

        /// <summary>The water <paramref name="state"/>'s placement takes
        /// (TakesWater): none for a double slab, the only blocks whose type
        /// is double.</summary>
        private static string[] Takes(BlockState state)
        {
            if (state.GetProperty("type") == "double")
                return Array.Empty<string>();
            return TakesWater.TryGetValue(state.Name, out var takes) ? takes : SourceOnly;
        }

        private static Dictionary<string, string[]> BuildTakesWater()
        {
            var table = new Dictionary<string, string[]>(StringComparer.Ordinal);
            var full = new[] { Source, Falling };
            foreach (var dead in new[] { "", "dead_" })
                foreach (var color in new[] { "tube", "brain", "bubble", "fire", "horn" })
                    foreach (var form in new[] { "", "_fan", "_wall_fan" })
                        table[$"minecraft:{dead}{color}_coral{form}"] = full;
            table["minecraft:conduit"] = full;
            table["minecraft:small_dripleaf"] = new[] { Source, Flowing, Falling };
            table["minecraft:light"] = Array.Empty<string>();
            return table;
        }

        private static string? DefaultProperty(string name, string key)
        {
            foreach (var pair in BlockDefaults.DefaultProperties(name))
            {
                if (pair.Key == key)
                    return pair.Value;
            }
            return null;
        }

        /// <summary>The water <paramref name="state"/> holds, as the game
        /// reads its fluid: Source, Flowing, Falling, or null for none. An
        /// omitted property is its default, so a bare minecraft:water (as a
        /// legacy chunk may store one) is the source and a bare sea pickle
        /// is waterlogged. A level or a waterlogged the game does not have
        /// is refused, naming the cell, never read as dry.</summary>
        private static string? WaterIn(BlockState state, (int X, int Y, int Z) cell)
        {
            string name = state.Name;
            if (name == "minecraft:water")
            {
                string? level = state.GetProperty("level") ?? DefaultProperty(name, "level");
                if (level == "0")
                    return Source;
                if (level != null && FlowingLevels.Contains(level))
                    return Flowing;
                if (level != null && FallingLevels.Contains(level))
                    return Falling;
                throw new NbtException(
                    $"water at ({cell.X}, {cell.Y}, {cell.Z}) has level={level}, "
                    + "which the game does not have (0-15)");
            }
            if (WaterSourceBlocks.Contains(name))
                return Source;
            string? waterlogged = state.GetProperty("waterlogged") ?? DefaultProperty(name, "waterlogged");
            if (waterlogged == "true")
                return Source;
            if (waterlogged == null || waterlogged == "false")
                return null;
            throw new NbtException(
                $"{name} at ({cell.X}, {cell.Y}, {cell.Z}) has waterlogged={waterlogged}, "
                + "which is neither true nor false");
        }

        /// <summary><paramref name="state"/>, a waterloggable block whose
        /// waterlogged the caller left unsaid, as the game places it over
        /// <paramref name="displaced"/>: waterlogged when the water there is
        /// one its rule takes -- and a campfire put out, unless the caller
        /// said lit -- written dry when it is not and the block is wet by
        /// default, and otherwise as given. A block whose rule takes no
        /// water never reads <paramref name="displaced"/>.</summary>
        private static BlockState PlacedWet(BlockState state, BlockState? displaced,
                                            (int X, int Y, int Z) cell)
        {
            var takes = Takes(state);
            string? water = displaced == null || takes.Length == 0
                ? null : WaterIn(displaced, cell);
            if (water != null && Array.IndexOf(takes, water) >= 0)
            {
                var wet = state.WithProperties(("waterlogged", "true"));
                if (Campfires.Contains(state.Name) && state.GetProperty("lit") == null)
                    wet = wet.WithProperties(("lit", "false"));
                return wet;
            }
            if (DefaultProperty(state.Name, "waterlogged") == "true")
                return state.WithProperties(("waterlogged", "false"));
            return state;
        }

        /// <summary>The full set of (position, state) a placement writes.</summary>
        private static List<((int X, int Y, int Z) Cell, BlockState State)> CellsFor(
            BlockState state, int x, int y, int z)
        {
            string name = state.Name;
            var props = state.Properties;
            var cells = new List<((int, int, int), BlockState)>();

            if (name.EndsWith("_bed", StringComparison.Ordinal))
            {
                string facing = state.GetProperty("facing") ?? "north";
                var (dx, dz) = Vector[facing];
                (int X, int Y, int Z) foot, head;
                if (state.GetProperty("part") == "head")
                {
                    head = (x, y, z);
                    foot = (x - dx, y, z - dz);
                }
                else
                {
                    foot = (x, y, z);
                    head = (x + dx, y, z + dz);
                }
                var baseProps = props;
                baseProps.Remove("part");
                if (!baseProps.ContainsKey("facing"))
                    baseProps["facing"] = facing;
                cells.Add((foot, WithProperty(name, baseProps, "part", "foot")));
                cells.Add((head, WithProperty(name, baseProps, "part", "head")));
                return cells;
            }

            if (name.EndsWith("_door", StringComparison.Ordinal)
                || TallPlants.Contains(name))
            {
                var baseProps = props;
                baseProps.Remove("half");
                cells.Add(((x, y, z), WithProperty(name, baseProps, "half", "lower")));
                cells.Add(((x, y + 1, z), WithProperty(name, baseProps, "half", "upper")));
                return cells;
            }

            cells.Add(((x, y, z), state));
            return cells;
        }

        private static BlockState WithProperty(
            string name, PropertyMap baseProps, string key, string value)
        {
            var merged = new PropertyMap(baseProps) { [key] = value };
            return new BlockState(name, merged);
        }

        /// <summary>Place a block with the game's placement semantics: both
        /// halves of a bed, door or tall plant; neighbor-derived state
        /// settled on the placed cells and their neighbors; waterlogging
        /// into a water source, and only where the game's rule for the block
        /// takes the water it displaces (TakesWater), a campfire put out
        /// there; and a block wet by default written dry where its rule
        /// finds no water.
        ///
        /// Returns true when everything was written. False — with nothing
        /// written at all — when any cell of the placement falls in a chunk
        /// the world has never generated or cannot read, so a bed can never
        /// end up half-placed across a chunk border. Throws NbtException,
        /// again with nothing written, when a block whose water it must read
        /// -- one that can take water, placed with waterlogged unsaid -- is
        /// displacing water at a level, or a waterlogged, the game does not
        /// have.</summary>
        public static bool PlaceBlock(MinecraftSave save, int x, int y, int z,
                                      BlockState block, string? dimension = null)
        {
            var dim = save.Dimension(dimension);
            var cells = CellsFor(block, x, y, z);

            // Refuse atomically before writing anything.
            foreach (var (cell, _) in cells)
            {
                ChunkBlocks? accessor;
                try
                {
                    accessor = dim.WritableBlocks(cell.X >> 4, cell.Z >> 4);
                }
                catch (NbtException)
                {
                    return false;
                }
                if (accessor == null)
                    return false;
                if (cell.Y < accessor.MinY || cell.Y > accessor.MaxY)
                    return false;
            }

            BlockState? BlockAt(int bx, int by, int bz) =>
                save.GetBlock(bx, by, bz, dimension);

            // Every cell's water is read before any cell is written, so a
            // displaced state the game does not have refuses the placement
            // whole.
            if (block.GetProperty("waterlogged") == null)
            {
                for (int i = 0; i < cells.Count; i++)
                {
                    var (cell, original) = cells[i];
                    if (Waterloggable(original.Name))
                        cells[i] = (cell, PlacedWet(original, BlockAt(cell.X, cell.Y, cell.Z), cell));
                }
            }

            var written = new List<(int X, int Y, int Z)>();
            foreach (var (cell, state) in cells)
            {
                if (!save.SetBlock(cell.X, cell.Y, cell.Z, state, dimension))
                    return false;  // cannot happen after the checks above, but honest
                written.Add(cell);
            }

            // Settle neighbor-derived state on the placed cells, their
            // orthogonal neighbors, and the cells below (a wall's post can
            // change when something lands on it) — the block updates a
            // player placement fires.
            var touched = new HashSet<(int X, int Y, int Z)>(written);
            foreach (var cell in written)
            {
                foreach (var (dx, dz) in Vector.Values)
                    touched.Add((cell.X + dx, cell.Y, cell.Z + dz));
                touched.Add((cell.X, cell.Y - 1, cell.Z));
            }
            var ordered = new List<(int X, int Y, int Z)>(touched);
            // Top-down, as in the reference; x/z break ties so the sweep is
            // reproducible (a HashSet has no order to inherit).
            ordered.Sort((a, b) =>
            {
                int byY = b.Y.CompareTo(a.Y);
                if (byY != 0) return byY;
                int byX = a.X.CompareTo(b.X);
                return byX != 0 ? byX : a.Z.CompareTo(b.Z);
            });
            foreach (var cell in ordered)
            {
                var state = BlockAt(cell.X, cell.Y, cell.Z);
                if (state == null)
                    continue;
                var derived = Connect.DerivedState(state, BlockAt, cell.X, cell.Y, cell.Z);
                if (derived != null && !derived.Equals(state))
                    save.SetBlock(cell.X, cell.Y, cell.Z, derived, dimension);
            }
            return true;
        }
    }
}
