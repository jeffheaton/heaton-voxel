#!/usr/bin/env python3
"""Ask the game what shape every block state is, and vendor the answer.

The judged tier classifies blocks by name -- what a body walks through
(:func:`heaton_voxel.craft.survey.step_kind`), what a rail can stand on
(``heaton_voxel.craft.build._holds_rail_top``), what is water -- and a name
is only a guess about a shape. The game knows the shape. This runs a few
lines of Java against the installed jar, through the launcher's own
runtime (a full JDK, so a single source file runs as it is), and records
for every block state the game has:

* the height of its collision shape, in sixteenths of a block (-1: none)
* whether that shape stands in the middle of the cell, where a body
  walking through the cell would be
* whether its top face is ``RIGID`` -- a full square, which is what a
  rail needs under it
* which of its six faces are sturdy, ``FULL`` and ``CENTER`` -- what a
  button, a torch, a ladder or a lantern needs to stay attached
* whether it is solid (a sign's footing) and whether it conducts
  redstone (a button on glass sticks, and opens nothing)
* the light it gives off and the light it takes to cross
* whether the block falls (``Fallable``) and whether the state holds fluid
* the faces its shape closes to light -- a slab's solid half, a snow
  layer's underside -- one 16x16 mask per face from
  ``LightEngine.getOcclusionShape``, and the mask model is proven against
  the game's own ``Shapes.faceShapeOccludes`` for every pair of distinct
  face shapes before anything is written
* whether a monster can stand on it (``isValidSpawn``, for a zombie or a
  fire-immune wither skeleton) and whether the cell passes the game's
  per-cell test of room to spawn in (``NaturalSpawner.isValidEmptySpawnBlock``,
  for a zombie). That test is not the whole of it: the game then also
  needs the mob's box clear of collision (``noCollision`` on
  ``getSpawnAABB``), which no one state answers, so a fence, a wall, a
  chest or a bottom slab passes it. Both read block tags, so the run first
  binds the game's own tags out of its bundled data pack, the way a server
  does: a rail (``prevent_mob_spawning_inside``) or a fire is no place to
  spawn
* how water moves through it, as ``FlowingFluid`` asks: whether its fluid
  is water (the ``#water`` fluid tag, so a flowing level counts and lava
  does not); whether water can enter the cell at all (the game's own
  ``canHoldAnyFluid``, called by reflection once the tags are bound: a
  ``LiquidBlockContainer``, or a block tagged ``washed_away_by_fluids`` --
  which holds water and lava themselves); whether it is such a container,
  and takes a water source (``canPlaceLiquid``); and the faces of its
  collision shape that water's wall test sees, one 16x16 mask per face, cut
  as ``Shapes.mergedFaceOccludes`` cuts them. Two proofs run before
  anything is written: the masks are held to the game's own private
  ``canPassThroughWall`` for every ordered pair of distinct collision
  shapes (told apart by their boxes and by the grid each is sliced on),
  each way along every axis, and no container may take flowing
  water, which the flow model built on these facts assumes

The jar's data, read straight out of the zip with no Java, supplies the
rest: the block tags the judged tier is held to (the Java run lists the
same tags from the bound registry, and the two must agree member for
member), each block's place in the motion-blocking heightmaps (the two
``blocks_motion_in_heightmap`` tags, which 26.3's ``Heightmap$Types``
test), and each dimension type's monster spawn light rules.

One game fact rides along that is not about blocks: which villager type a
biome gives a villager standing in it. ``VillagerType.byBiome`` is the
biome's entry in the private ``BY_BIOME`` table, which the Java run reads
by reflection, or ``VillagerData.DEFAULT_TYPE`` for a biome the table does
not name; the run lists both, and every type the ``villager_type`` registry
holds, and every biome the table names must be one the jar ships.

It writes that to ``fixtures/block_shapes.json``, one block per line,
each block's states summarized as its commonest record plus the
exceptions, keyed by the fewest properties that pick them out. The tests
hold the classifiers to it, and ``craft.biomes``' villager table. After a
game update, rerun this and read the failures: each one is a block the
tables have never heard of, or a shape the update changed.

The same run writes ``heaton_voxel/craft/block_facts.py``: the support,
power, light, occlusion and spawn facts, for every block that is not a
plain cube, plus heightmap membership and fluid, which is what the
survival audit, the iron-door check and the light simulation read at run
time, and beside them, in a table of their own so the first is untouched,
how water moves through every block that is not a plain cube to water;
and ``heaton_voxel/craft/block_registry.py``: every block id the game has,
the values each of its properties takes, and which blocks fall and which
are always fluid, and always water. All three are generated; edit none by
hand.

Usage::

    python tools/probe_block_shapes.py --version 26.3
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from fixtures import (  # noqa: E402
    FIXTURE_BLOCK_SHAPES,
    BlockShape,
    VillagerTypes,
    block_shapes,
    block_tags,
    dimension_types,
    villager_types,
)
from generate_block_defaults import JRE, game_classpath  # noqa: E402

PROBE = """\
import java.io.PrintStream;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import com.google.common.math.DoubleMath;

import net.minecraft.SharedConstants;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.HolderSet;
import net.minecraft.core.Registry;
import net.minecraft.core.RegistryAccess;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.Bootstrap;
import net.minecraft.server.packs.PackType;
import net.minecraft.server.packs.repository.ServerPacksSource;
import net.minecraft.server.packs.resources.MultiPackResourceManager;
import net.minecraft.tags.FluidTags;
import net.minecraft.tags.TagKey;
import net.minecraft.tags.TagLoader;
import net.minecraft.world.entity.EntityTypes;
import net.minecraft.world.entity.npc.villager.VillagerData;
import net.minecraft.world.entity.npc.villager.VillagerType;
import net.minecraft.world.level.BlockGetter;
import net.minecraft.world.level.EmptyBlockGetter;
import net.minecraft.world.level.NaturalSpawner;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Fallable;
import net.minecraft.world.level.block.LiquidBlockContainer;
import net.minecraft.world.level.block.SupportType;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.lighting.LightEngine;
import net.minecraft.world.level.material.FlowingFluid;
import net.minecraft.world.level.material.Fluids;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.shapes.BooleanOp;
import net.minecraft.world.phys.shapes.Shapes;
import net.minecraft.world.phys.shapes.SliceShape;
import net.minecraft.world.phys.shapes.VoxelShape;

public class ShapeProbe {
    /** The column a player's 0.6-wide body sweeps through a cell's middle. */
    static final VoxelShape BODY = Shapes.box(0.2, 0.0, 0.2, 0.8, 1.0, 0.8);

    /** Refuse, in a file beside the table, since the game owns stdout. */
    static void fail(String table, String why) throws Exception {
        PrintStream out = new PrintStream(table + ".failed", "UTF-8");
        out.println(why);
        out.close();
        System.exit(3);
    }

    /** A bound in sixteenths, refusing one off the grid. */
    static int sixteenths(double bound, String table, String what) throws Exception {
        double scaled = bound * 16;
        long rounded = Math.round(scaled);
        if (Math.abs(scaled - rounded) > 1e-6)
            fail(table, what + " has a face bound off the 1/16 grid: " + bound);
        return (int) Math.max(0, Math.min(16, rounded));
    }

    /** A bound in sixteenths, rounded into the box it bounds. */
    static int inward(double bound, boolean lower) {
        double scaled = bound * 16;
        long rounded = lower ? (long) Math.ceil(scaled - 1e-6)
                             : (long) Math.floor(scaled + 1e-6);
        return (int) Math.max(0, Math.min(16, rounded));
    }

    /**
     * A face shape as a 256-bit mask on the face's plane: bit u*16+v is the
     * sixteenth [u, u+1) x [v, v+1), u and v being (y, z) on an X face,
     * (x, z) on a Y face and (x, y) on a Z face.
     */
    static long[] mask(VoxelShape shape, Direction.Axis axis, String table,
                       String what) throws Exception {
        return mask(shape, axis, table, what, false);
    }

    /**
     * mask(), but with whole set a bound off the grid is rounded into its
     * box rather than refused, so the mask holds the sixteenths the shape
     * covers whole: a chain or a bamboo stalk stands on thirty-seconds, and
     * covers none of the sixteenths it only cuts into.
     */
    static long[] mask(VoxelShape shape, Direction.Axis axis, String table,
                       String what, boolean whole) throws Exception {
        long[] words = new long[4];
        for (AABB box : shape.toAabbs()) {
            double u0, u1, v0, v1;
            if (axis == Direction.Axis.X) {
                u0 = box.minY; u1 = box.maxY; v0 = box.minZ; v1 = box.maxZ;
            } else if (axis == Direction.Axis.Y) {
                u0 = box.minX; u1 = box.maxX; v0 = box.minZ; v1 = box.maxZ;
            } else {
                u0 = box.minX; u1 = box.maxX; v0 = box.minY; v1 = box.maxY;
            }
            int ua = whole ? inward(u0, true) : sixteenths(u0, table, what);
            int ub = whole ? inward(u1, false) : sixteenths(u1, table, what);
            int va = whole ? inward(v0, true) : sixteenths(v0, table, what);
            int vb = whole ? inward(v1, false) : sixteenths(v1, table, what);
            for (int u = ua; u < ub; u++)
                for (int v = va; v < vb; v++) {
                    int bit = u * 16 + v;
                    words[bit >> 6] |= 1L << (bit & 63);
                }
        }
        return words;
    }

    static boolean covers(long[] a, long[] b) {
        for (int i = 0; i < 4; i++)
            if ((a[i] | b[i]) != -1L) return false;
        return true;
    }

    static String hex(long[] words) {
        return String.format("%016x%016x%016x%016x", words[3], words[2], words[1], words[0]);
    }

    /**
     * The face of a collision shape that water's wall test sees, cut as
     * Shapes.mergedFaceOccludes cuts it: the layer of the shape's own grid
     * nearest the face when the shape ends on the face, and nothing when it
     * stops short of the face or runs past it (a fence's top, a block and a
     * half up, shows the cell above nothing). The layer is the grid's, not
     * the face's: a pointed dripstone the game offsets toward the corner of
     * its cell keeps an empty layer of its grid outside the cell there, and
     * shows that side nothing -- read, like every offset shape, where the
     * probe asks, at the origin.
     */
    static VoxelShape wallFace(VoxelShape shape, Direction face) {
        Direction.Axis axis = face.getAxis();
        if (face.getAxisDirection() == Direction.AxisDirection.POSITIVE) {
            if (!DoubleMath.fuzzyEquals(shape.max(axis), 1.0, 1.0E-7))
                return Shapes.empty();
            return new SliceShape(shape, axis, shape.getCoords(axis).size() - 2);
        }
        if (!DoubleMath.fuzzyEquals(shape.min(axis), 0.0, 1.0E-7))
            return Shapes.empty();
        return new SliceShape(shape, axis, 0);
    }

    /**
     * args: the state table to write, the tag table to write, the villager
     * table to write, the tags to list.
     */
    public static void main(String[] args) throws Exception {
        SharedConstants.tryDetectVersion();
        Bootstrap.bootStrap();
        List<BlockState> states = new ArrayList<>();
        List<StringBuilder> lines = new ArrayList<>();
        // One of each distinct face shape on each axis, and its mask.
        List<Map<String, VoxelShape>> faceShapes = new ArrayList<>();
        List<Map<String, long[]>> faceMasks = new ArrayList<>();
        for (int axis = 0; axis < 3; axis++) {
            faceShapes.add(new LinkedHashMap<>());
            faceMasks.add(new LinkedHashMap<>());
        }
        // How water moves through each state, written after the spawn
        // facts; and one state of each distinct collision shape, with the
        // six faces water's wall test sees of it.
        List<StringBuilder> flows = new ArrayList<>();
        Map<String, BlockState> collisionStates = new LinkedHashMap<>();
        Map<String, long[][]> collisionMasks = new LinkedHashMap<>();
        for (Block block : BuiltInRegistries.BLOCK) {
            String name = BuiltInRegistries.BLOCK.getKey(block).toString();
            for (BlockState state : block.getStateDefinition().getPossibleStates()) {
                String props = state.getValues().map(Object::toString).sorted()
                    .collect(Collectors.joining(","));
                VoxelShape shape = state.getCollisionShape(EmptyBlockGetter.INSTANCE, BlockPos.ZERO);
                boolean empty = shape.isEmpty();
                long top = empty ? -1 : Math.round(shape.max(Direction.Axis.Y) * 16);
                boolean body = !empty && Shapes.joinIsNotEmpty(shape, BODY, BooleanOp.AND);
                boolean rigid = state.isFaceSturdy(
                    EmptyBlockGetter.INSTANCE, BlockPos.ZERO, Direction.UP, SupportType.RIGID);
                // One bit per face, in Direction order: down, up, north,
                // south, west, east.
                int sturdy = 0, center = 0;
                for (Direction face : Direction.values()) {
                    if (state.isFaceSturdy(EmptyBlockGetter.INSTANCE, BlockPos.ZERO, face, SupportType.FULL))
                        sturdy |= 1 << face.ordinal();
                    if (state.isFaceSturdy(EmptyBlockGetter.INSTANCE, BlockPos.ZERO, face, SupportType.CENTER))
                        center |= 1 << face.ordinal();
                }
                boolean solid = state.isSolid();
                boolean conductor = state.isRedstoneConductor(EmptyBlockGetter.INSTANCE, BlockPos.ZERO);
                int emission = state.getLightEmission();
                int dampening = state.getLightDampening();
                boolean falls = block instanceof Fallable;
                boolean fluid = !state.getFluidState().isEmpty();
                StringBuilder line = new StringBuilder(
                    name + "\\t" + props + "\\t" + top + "\\t" + (body ? 1 : 0)
                    + "\\t" + (rigid ? 1 : 0) + "\\t" + sturdy + "\\t" + center
                    + "\\t" + (solid ? 1 : 0) + "\\t" + (conductor ? 1 : 0)
                    + "\\t" + emission + "\\t" + dampening
                    + "\\t" + (falls ? 1 : 0) + "\\t" + (fluid ? 1 : 0));
                // The face each side shows light: empty unless the block
                // occludes by its shape (a slab, a stair, a snow layer).
                for (Direction face : Direction.values()) {
                    VoxelShape occludes = LightEngine.getOcclusionShape(state, face);
                    long[] bits = mask(occludes, face.getAxis(), args[0],
                                       name + "[" + props + "] " + face);
                    line.append("\\t").append(hex(bits));
                    int axis = face.getAxis().ordinal();
                    String key = occludes.toAabbs().toString();
                    if (faceShapes.get(axis).putIfAbsent(key, occludes) == null)
                        faceMasks.get(axis).put(key, bits);
                }
                // A container (LiquidBlockContainer) takes what its
                // canPlaceLiquid accepts, with nothing around it. Flowing
                // water it must never take: the flow model holds that a
                // flow waterlogs nothing, and a container that took one
                // would make it wrong, so nothing is written.
                boolean container = block instanceof LiquidBlockContainer;
                boolean takesSource = container
                    && ((LiquidBlockContainer) block).canPlaceLiquid(
                        null, EmptyBlockGetter.INSTANCE, BlockPos.ZERO, state, Fluids.WATER);
                if (container && ((LiquidBlockContainer) block).canPlaceLiquid(
                        null, EmptyBlockGetter.INSTANCE, BlockPos.ZERO, state,
                        Fluids.FLOWING_WATER))
                    fail(args[0], name + "[" + props + "] takes flowing water, and the"
                         + " flow model holds that flowing water waterlogs nothing");
                StringBuilder flow = new StringBuilder(
                    "\\t" + (container ? 1 : 0) + "\\t" + (takesSource ? 1 : 0));
                long[][] walls = new long[6][];
                for (Direction face : Direction.values()) {
                    walls[face.ordinal()] = mask(wallFace(shape, face), face.getAxis(),
                                                 args[0], name + "[" + props
                                                 + "] collision " + face, true);
                    flow.append("\\t").append(hex(walls[face.ordinal()]));
                }
                // canPassThroughWall asks Shapes.block() and Shapes.empty()
                // by identity, so a shape is told apart by those as well as
                // by its boxes; and mergedFaceOccludes slices the shape's
                // own grid, which two shapes with the same boxes need not
                // share (an offset dripstone keeps a layer outside its
                // cell), so by its grid on each axis too.
                String key = (shape == Shapes.block() ? "block " : "")
                    + (shape == Shapes.empty() ? "empty " : "") + shape.toAabbs()
                    + " x" + shape.getCoords(Direction.Axis.X)
                    + " y" + shape.getCoords(Direction.Axis.Y)
                    + " z" + shape.getCoords(Direction.Axis.Z);
                if (collisionStates.putIfAbsent(key, state) == null)
                    collisionMasks.put(key, walls);
                states.add(state);
                lines.add(line);
                flows.add(flow);
            }
        }

        // Water passes between two cells exactly when the faces their
        // collision shapes show each other leave a gap -- or the model is
        // wrong, and nothing is written. The game's own canPassThroughWall
        // is asked of every ordered pair of distinct collision shapes, each
        // way along every axis, so the masks answer as the game does for
        // every pair of states there is.
        Method passes = FlowingFluid.class.getDeclaredMethod(
            "canPassThroughWall", Direction.class, BlockGetter.class, BlockPos.class,
            BlockState.class, BlockPos.class, BlockState.class);
        passes.setAccessible(true);
        List<String> shapeKeys = new ArrayList<>(collisionStates.keySet());
        for (Direction face : Direction.values()) {
            int back = face.getOpposite().ordinal();
            for (String from : shapeKeys) {
                for (String to : shapeKeys) {
                    boolean game = (Boolean) passes.invoke(
                        null, face, EmptyBlockGetter.INSTANCE, BlockPos.ZERO,
                        collisionStates.get(from), BlockPos.ZERO, collisionStates.get(to));
                    boolean model = !covers(collisionMasks.get(from)[face.ordinal()],
                                            collisionMasks.get(to)[back]);
                    if (game != model)
                        fail(args[0], face + ": the collision masks say " + model
                             + " and canPassThroughWall says " + game + " from "
                             + collisionStates.get(from) + " " + from + " to "
                             + collisionStates.get(to) + " " + to);
                }
            }
        }

        // Two neighbors close their shared face exactly when the union of
        // their face masks is the whole face -- or the model is wrong, and
        // nothing is written. Every pair of distinct shapes is asked, so the
        // masks answer as the game does for every pair of states there is.
        for (int axis = 0; axis < 3; axis++) {
            List<String> keys = new ArrayList<>(faceShapes.get(axis).keySet());
            for (int i = 0; i < keys.size(); i++) {
                for (int j = i; j < keys.size(); j++) {
                    boolean game = Shapes.faceShapeOccludes(
                        faceShapes.get(axis).get(keys.get(i)),
                        faceShapes.get(axis).get(keys.get(j)));
                    boolean model = covers(faceMasks.get(axis).get(keys.get(i)),
                                           faceMasks.get(axis).get(keys.get(j)));
                    if (game != model)
                        fail(args[0], "axis " + axis + ": the masks say " + model
                             + " and faceShapeOccludes says " + game + " for "
                             + keys.get(i) + " against " + keys.get(j));
                }
            }
        }

        // The spawn rules read block tags (prevent_mob_spawning_inside,
        // fire), which a bare Bootstrap binds empty. Bind the game's own,
        // out of its bundled data pack, as a server does on start.
        MultiPackResourceManager resources = new MultiPackResourceManager(
            PackType.SERVER_DATA,
            List.of(ServerPacksSource.createVanillaPackSource().fullResources()));
        for (Registry.PendingTags<?> pending : TagLoader.loadTagsForExistingRegistries(
                resources, RegistryAccess.fromRegistryOfRegistries(BuiltInRegistries.REGISTRY)))
            pending.apply();

        // Whether water can enter a cell at all is the game's own
        // canHoldAnyFluid: a container, or a block tagged
        // washed_away_by_fluids -- which reads the bound tags too.
        Method holdsAny = FlowingFluid.class.getDeclaredMethod(
            "canHoldAnyFluid", BlockState.class);
        holdsAny.setAccessible(true);

        PrintStream out = new PrintStream(args[0], "UTF-8");
        for (int i = 0; i < states.size(); i++) {
            BlockState state = states.get(i);
            // Standing on it: a zombie's rule, or a wither skeleton's, which
            // is the fire-immune one (magma).
            boolean floor = state.isValidSpawn(EmptyBlockGetter.INSTANCE, BlockPos.ZERO,
                                               EntityTypes.ZOMBIE)
                || state.isValidSpawn(EmptyBlockGetter.INSTANCE, BlockPos.ZERO,
                                      EntityTypes.WITHER_SKELETON);
            // Standing in it: what each of a zombie's two cells must be,
            // cell by cell. The game also wants the mob's box clear of
            // collision (noCollision on getSpawnAABB), which no one state
            // answers: a fence or a bottom slab passes this.
            boolean space = NaturalSpawner.isValidEmptySpawnBlock(
                EmptyBlockGetter.INSTANCE, BlockPos.ZERO, state, state.getFluidState(),
                EntityTypes.ZOMBIE);
            // Its fluid is water: the #water fluid tag, which a flowing
            // level is in as much as a source, and lava is not.
            boolean water = state.getFluidState().is(FluidTags.WATER);
            boolean enters = (Boolean) holdsAny.invoke(null, state);
            out.println(lines.get(i).append("\\t").append(floor ? 1 : 0)
                        .append("\\t").append(space ? 1 : 0)
                        .append("\\t").append(water ? 1 : 0)
                        .append("\\t").append(enters ? 1 : 0)
                        .append(flows.get(i)));
        }
        out.close();

        // The bound tags, member by member, to hold the zip reading to.
        PrintStream tags = new PrintStream(args[1], "UTF-8");
        for (int i = 3; i < args.length; i++) {
            TagKey<Block> key = TagKey.create(Registries.BLOCK, Identifier.parse(args[i]));
            HolderSet.Named<Block> members = BuiltInRegistries.BLOCK.get(key).orElse(null);
            tags.println(args[i] + "\\t" + (members == null ? "!" : members.stream()
                .map(holder -> BuiltInRegistries.BLOCK.getKey(holder.value()).toString())
                .sorted().collect(Collectors.joining(","))));
        }
        tags.close();

        // The villager type a biome gives (Villager.finalizeSpawn types a
        // villager by the biome at its feet): VillagerType.byBiome is the
        // biome's entry in BY_BIOME, or VillagerData.DEFAULT_TYPE for one
        // the table does not name. BY_BIOME is private; read it as it is.
        PrintStream villagers = new PrintStream(args[2], "UTF-8");
        villagers.println("default\\t" + VillagerData.DEFAULT_TYPE.identifier());
        for (ResourceKey<VillagerType> kind : BuiltInRegistries.VILLAGER_TYPE.registryKeySet())
            villagers.println("kind\\t" + kind.identifier());
        Field byBiome = VillagerType.class.getDeclaredField("BY_BIOME");
        byBiome.setAccessible(true);
        for (Map.Entry<?, ?> entry : ((Map<?, ?>) byBiome.get(null)).entrySet())
            villagers.println("biome\\t" + ((ResourceKey<?>) entry.getKey()).identifier()
                              + "\\t" + ((ResourceKey<?>) entry.getValue()).identifier());
        villagers.close();
        System.exit(0);
    }
}
"""

#: The game's block tags the fixture carries, each beside what it holds the
#: judged tier to. Read out of the jar and expanded (:func:`read_tags`); the
#: Java run lists the same tags from the registry it bound, and the two must
#: agree.
VENDORED_TAGS = {
    "air": "what is nothing: blocks.AIR_BLOCKS",
    "logs": "what a tree is: its trunk",
    "leaves": "what a tree is: its crown",
    "saplings": "what a tree is: before it grows",
    "wart_blocks": "what a tree is: a nether fungus's crown",
    "replaceable_by_trees": "ground cover, which a growing tree overwrites",
    "small_flowers": "ground cover",
    "dirt": "natural ground",
    "sand": "natural ground",
    "terracotta": "natural ground, in the badlands",
    "base_stone_overworld": "natural ground, under the soil",
    "base_stone_nether": "natural ground, in the nether",
    "substrate_overworld": "natural ground, the soil",
    "stairs": "a shape cut from a block",
    "slabs": "a shape cut from a block",
    "walls": "a shape cut from a block",
    "climbable": "a way up: what a walk climbs (LivingEntity.onClimbable)",
    "supports_vegetation": "a footing: a flower, a grass, a fern or a sapling "
                           "(VegetationBlock)",
    "supports_azalea": "a footing: an azalea (AzaleaBlock)",
    "supports_dry_vegetation": "a footing: a dead bush or dry grass "
                               "(DryVegetationBlock)",
    "supports_sugar_cane": "a footing: sugar cane (SugarCaneBlock)",
    "supports_sugar_cane_adjacently": "beside sugar cane's footing, a block "
                                      "that waters it (SugarCaneBlock asks "
                                      "this of the block, and a fluid tag of "
                                      "the same name of its fluid)",
}
#: What the motion-blocking heightmaps count (26.3's ``Heightmap$Types``:
#: MOTION_BLOCKING is the first tag or fluid, MOTION_BLOCKING_NO_LEAVES the
#: second or fluid, OCEAN_FLOOR the first alone). Kept per block, as its
#: ``heightmap``, rather than as two thousand-name lists.
HEIGHTMAP_TAGS = ("blocks_motion_in_heightmap",
                  "blocks_motion_in_heightmap_no_leaves")


def probe(version: str, classpath: list[str], tags=()
          ) -> tuple[list[tuple[str, dict, BlockShape]], dict[str, frozenset],
                     dict]:
    """``(states, tags, villagers)``, straight from the installed game, run
    on ``classpath`` (:func:`game_classpath`'s): every block state, the
    members of each tag in ``tags`` as the registry binds them, and the
    villager type each biome gives (:func:`read_villagers`)."""
    work = tempfile.mkdtemp(prefix="heaton-voxel-shapes-")
    try:
        source = os.path.join(work, "ShapeProbe.java")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write(PROBE)
        table = os.path.join(work, "shapes.tsv")
        listed = os.path.join(work, "tags.tsv")
        typed = os.path.join(work, "villagers.tsv")
        print(f"probing every {version} block state ...", file=sys.stderr)
        run = subprocess.run(
            [JRE, "-Xmx2G", "-cp", os.pathsep.join(classpath), source, table,
             listed, typed, *(f"minecraft:{tag}" for tag in tags)],
            cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)
        if run.returncode != 0:
            failed = table + ".failed"
            if os.path.exists(failed):
                with open(failed, encoding="utf-8") as handle:
                    raise SystemExit(f"the probe refused: {handle.read()}")
            raise SystemExit(f"the probe failed -- it needs a JDK, which the "
                             f"launcher's runtime is:\n{run.stdout[-2000:]}")
        states = read_states(table)
        bound = {}
        with open(listed, encoding="utf-8") as handle:
            for line in handle:
                tag, members = line.rstrip("\n").split("\t")
                if members == "!":
                    raise SystemExit(f"the game binds no tag {tag}")
                bound[tag.removeprefix("minecraft:")] = frozenset(
                    members.split(",") if members else ())
        villagers = read_villagers(typed)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return states, bound, villagers


#: A line of the Java run's state table: the name and the properties; the
#: collision top, body and rigid top; the sturdy and center faces, solid,
#: conductor, emission and dampening; falls and fluid; the six faces closed
#: to light; the two spawn facts; then how water moves through the state
#: -- water, enters, container, takes_source and the six faces of its
#: collision shape water's wall test sees.
STATE_FIELDS = 2 + 11 + 6 + 2 + 4 + 6


def read_states(path: str) -> list[tuple[str, dict, BlockShape]]:
    """The Java run's state table, one ``(name, properties, shape)`` per
    line.

    A line with fields missing or over, a flag that is neither 0 nor 1, and
    a state whose water facts contradict each other -- a water source taken
    by what is no container, a container water cannot enter, water in a
    state that holds no fluid -- are each refused by line: a table read past
    any of them would tell the flow model something the game never said."""
    flags = ("body", "rigid", "falls", "fluid", "solid", "conductor",
             "spawn_floor", "spawn_space", "water", "enters", "container",
             "takes_source")
    states = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            fields = line.rstrip("\n").split("\t")
            if len(fields) != STATE_FIELDS:
                raise SystemExit(f"state table line {number} has "
                                 f"{len(fields)} fields, not {STATE_FIELDS}: "
                                 f"{fields[0]!r}")
            (name, props, top, body, rigid, sturdy, center, solid, conductor,
             emission, dampening, falls, fluid) = fields[:13]
            faces, collision = fields[13:19], fields[25:31]
            spawn_floor, spawn_space, water, enters, container, takes_source = (
                fields[19:25])
            read = dict(zip(flags, (body, rigid, falls, fluid, solid, conductor,
                                    spawn_floor, spawn_space, water, enters,
                                    container, takes_source)))
            stray = sorted(flag for flag, value in read.items()
                           if value not in ("0", "1"))
            if stray:
                raise SystemExit(f"state table line {number}, {name}: "
                                 f"{', '.join(stray)} neither 0 nor 1")
            held = {flag: value == "1" for flag, value in read.items()}
            for wrong, why in (
                    (held["takes_source"] and not held["container"],
                     "takes a water source and is no container"),
                    (held["container"] and not held["enters"],
                     "is a container water cannot enter"),
                    (held["water"] and not held["fluid"],
                     "holds water and no fluid")):
                if wrong:
                    raise SystemExit(f"state table line {number}: {name}[{props}] "
                                     f"{why}")
            properties = (dict(pair.split("=", 1) for pair in props.split(","))
                          if props else {})
            states.append((name, properties, BlockShape(
                top=int(top), sturdy=int(sturdy), center=int(center),
                emission=int(emission), dampening=int(dampening),
                faces=tuple(int(face, 16) for face in faces),
                collision=tuple(int(face, 16) for face in collision),
                **held)))
    return states


def read_villagers(path: str) -> dict:
    """The Java run's villager table as the fixture keeps it:
    ``{"by_biome": {biome: type}, "default": type, "kinds": [type, ...]}``,
    the biomes and the kinds sorted.

    The table is one ``default`` line, a ``kind`` line per type the
    registry holds and a ``biome`` line per ``BY_BIOME`` entry. A line of
    any other shape, a second default or none, a biome listed twice, and a
    type -- the default's or a biome's -- the kinds do not hold are each
    refused: a table read past any of them would type villagers some way
    the game does not."""
    default, kinds, by_biome = None, set(), {}
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            fields = line.rstrip("\n").split("\t")
            if fields[0] == "default" and len(fields) == 2:
                if default is not None:
                    raise SystemExit(f"villager table line {number}: a second "
                                     f"default, {fields[1]} after {default}")
                default = fields[1]
            elif fields[0] == "kind" and len(fields) == 2:
                kinds.add(fields[1])
            elif fields[0] == "biome" and len(fields) == 3:
                if fields[1] in by_biome:
                    raise SystemExit(f"villager table line {number}: biome "
                                     f"{fields[1]} is listed twice")
                by_biome[fields[1]] = fields[2]
            else:
                raise SystemExit(f"villager table line {number} is not a "
                                 f"default, a kind or a biome: "
                                 f"{line.rstrip()!r}")
    if default is None:
        raise SystemExit("the villager table names no default type")
    stray = sorted({default, *by_biome.values()} - kinds)
    if stray:
        raise SystemExit(f"the villager table gives types its kinds do not "
                         f"hold: {stray}")
    return {"by_biome": dict(sorted(by_biome.items())), "default": default,
            "kinds": sorted(kinds)}


def jar_json(archive: zipfile.ZipFile, path: str):
    """One JSON document out of the game's jar, refusing one it lacks: every
    tag and dimension type this probe reads goes through here. (The jar's
    ``version.json`` is :func:`game_classpath`'s, which the defaults
    generator shares.)"""
    try:
        raw = archive.read(path)
    except KeyError:
        raise SystemExit(f"the jar has no {path}") from None
    return json.loads(raw)


def read_tags(archive: zipfile.ZipFile, names, blocks) -> dict[str, frozenset]:
    """Each block tag in ``names``, expanded through the tags it names, as
    ``{name: frozenset(block ids)}``. Refuses a tag file the jar lacks and a
    required member the probe never saw: a list that silently lost a name
    would hold the tables to less than the game says."""
    expanded: dict[str, frozenset] = {}

    def expand(tag: str, through: tuple) -> frozenset:
        if tag in expanded:
            return expanded[tag]
        if tag in through:
            raise SystemExit(f"tag {tag} includes itself: {' -> '.join(through)}")
        namespace, _, path = tag.rpartition(":")
        document = jar_json(
            archive, f"data/{namespace or 'minecraft'}/tags/block/{path}.json")
        members: set[str] = set()
        for value in document["values"]:
            required = True
            if isinstance(value, dict):
                required = value.get("required", True)
                value = value["id"]
            if value.startswith("#"):
                try:
                    members |= expand(value[1:], through + (tag,))
                except SystemExit:
                    if required:
                        raise
            elif value in blocks:
                members.add(value)
            elif required:
                raise SystemExit(f"tag {tag} names {value}, a block the probe "
                                 f"never saw")
        expanded[tag] = frozenset(members)
        return expanded[tag]

    return {name: expand(f"minecraft:{name}", ()) for name in names}


def read_dimension_types(archive: zipfile.ZipFile) -> dict[str, dict[str, int]]:
    """Each dimension type's monster spawn light rules:
    ``{id: {"block_light_limit": n, "light_level_max": m}}``. A monster
    spawns only at block light up to the limit (no limit at 15) and at a
    light level no higher than a draw from ``monster_spawn_light_level``,
    whose highest value is ``light_level_max``."""
    prefix = "data/minecraft/dimension_type/"
    found = {}
    for path in sorted(archive.namelist()):
        if not (path.startswith(prefix) and path.endswith(".json")):
            continue
        document = jar_json(archive, path)
        name = "minecraft:" + path[len(prefix):-len(".json")]
        limit = document["monster_spawn_block_light_limit"]
        level = document["monster_spawn_light_level"]
        if isinstance(level, dict) and level.get("type") == "minecraft:uniform":
            level = level["max_inclusive"]
        if not isinstance(limit, int) or not isinstance(level, int):
            raise SystemExit(f"{name}: a monster spawn light rule this probe "
                             f"cannot read: {limit!r}, {level!r}")
        found[name] = {"block_light_limit": limit, "light_level_max": level}
    if "minecraft:overworld" not in found:
        raise SystemExit("the jar has no overworld dimension type")
    return found


def check_villager_biomes(archive: zipfile.ZipFile, villagers: dict) -> None:
    """Refuse a biome the villager table types (:func:`read_villagers`)
    that the jar ships no ``worldgen/biome`` file for: a key read off the
    wrong field, or a biome the game has since retired, would otherwise be
    vendored as a place villagers stand."""
    for biome in villagers["by_biome"]:
        namespace, _, path = biome.rpartition(":")
        jar_json(archive, f"data/{namespace or 'minecraft'}/worldgen/biome/"
                          f"{path}.json")


def heightmap_membership(names, tags) -> dict[str, str]:
    """Per block: ``both`` heightmaps count it, ``leaves`` only the one
    that counts leaves (MOTION_BLOCKING), or ``none``."""
    motion = tags["blocks_motion_in_heightmap"]
    no_leaves = tags["blocks_motion_in_heightmap_no_leaves"]
    found = {}
    for name in names:
        if name in no_leaves and name not in motion:
            raise SystemExit(f"{name} counts without leaves and not with them")
        found[name] = ("both" if name in no_leaves
                       else "leaves" if name in motion else "none")
    return found


def _project(properties: dict, keys) -> tuple:
    return tuple((key, properties[key]) for key in keys)


def _picking_keys(states, records, record) -> list[str]:
    """The fewest properties that tell ``record``'s states from the rest,
    dropping keys in name order while membership stays a function of what
    is left."""
    keys = sorted(states[0][1])
    inside = [r == record for r in records]

    def separates(subset) -> bool:
        members = {_project(s[1], subset) for s, hit in zip(states, inside) if hit}
        return all((_project(s[1], subset) in members) == hit
                   for s, hit in zip(states, inside))

    for key in list(keys):
        trial = [k for k in keys if k != key]
        if separates(trial):
            keys = trial
    return keys


#: What a fixture record holds, in order; ``falls``, ``fluid`` and
#: ``heightmap`` are kept per block instead, and ``faces`` is an index into
#: the file's own table of face sets. The order is the file's, so append,
#: never reorder.
RECORD = ("top", "body", "rigid", "sturdy", "center", "solid", "conductor",
          "emission", "dampening", "spawn_floor", "spawn_space", "faces")
#: The run-time table's record: what holds a block up, what powers it, the
#: light it gives and takes, where a monster can spawn, and the faces it
#: closes to light. Append, never reorder.
FACTS = ("sturdy", "center", "solid", "conductor", "emission", "dampening",
         "spawn_floor", "spawn_space", "faces")
#: How water moves through a state, the fixture's record and the run-time
#: table's alike, kept beside the records above rather than in them so that
#: neither changes: whether water can enter it at all, whether it is a
#: container, whether it takes a water source, and its collision faces as
#: an index into the file's own table of collision face sets. ``water`` is
#: kept per block, as ``fluid`` is. Append, never reorder.
FLOW = ("enters", "container", "takes_source", "collision")
#: A face covered all over: what a full cube shows each side.
FULL_FACE = (1 << 256) - 1


def _mask_table(states, field: str, empty: str
                ) -> tuple[list[int], list[tuple], dict[tuple, int]]:
    masks = sorted({mask for _, _, shape in states
                    for mask in getattr(shape, field)})
    where = {mask: i for i, mask in enumerate(masks)}
    sets = sorted({tuple(where[mask] for mask in getattr(shape, field))
                   for _, _, shape in states})
    if masks[0] != 0 or sets[0] != (0,) * 6:
        raise SystemExit(empty)
    index = {tuple(masks[i] for i in faces): n for n, faces in enumerate(sets)}
    return masks, sets, index


def face_table(states) -> tuple[list[int], list[tuple], dict[tuple, int]]:
    """``(masks, face sets, index)``: every distinct face mask, sorted, so
    index 0 is the empty one; every distinct set of six as mask indices,
    sorted, so index 0 closes nothing; and each set's index by its masks."""
    return _mask_table(states, "faces", "no block state closes nothing to light")


def collision_table(states) -> tuple[list[int], list[tuple], dict[tuple, int]]:
    """:func:`face_table` for the faces water's wall test sees: index 0 of
    each is the empty one, which shows water nothing. Refuses a game where
    no state is that, or no state is a wall to water on every face -- the
    plain cube the run-time table assumes of every block it does not
    list."""
    masks, sets, index = _mask_table(states, "collision",
                                     "no block state shows water nothing")
    if (FULL_FACE,) * 6 not in index:
        raise SystemExit("no block state is a wall to water on every face")
    return masks, sets, index


def _records(group, fields, faces, collision=None) -> list[tuple]:
    """Each state's record: its ``fields`` as integers, ``faces`` as the
    index of its set of face masks and ``collision`` as the index of its
    set of collision faces."""
    return [tuple(faces[s[2].faces] if field == "faces"
                  else collision[s[2].collision] if field == "collision"
                  else int(getattr(s[2], field)) for field in fields)
            for s in group]


def _exceptions(group, records, default) -> list:
    exceptions = []
    for record in sorted(set(records)):
        if record == default:
            continue
        chosen = _picking_keys(group, records, record)
        whens = sorted({_project(s[1], chosen)
                        for s, r in zip(group, records) if r == record})
        exceptions.extend([dict(when), list(record)] for when in whens)
    return exceptions


def _whole_blocks(states, field: str) -> frozenset[str]:
    by_block: dict[str, list] = {}
    for name, properties, shape in states:
        by_block.setdefault(name, []).append((properties, getattr(shape, field)))
    always = frozenset(name for name, group in by_block.items()
                       if all(held for _, held in group))
    for name, group in sorted(by_block.items()):
        if name in always:
            continue
        for properties, held in group:
            if held != (properties.get("waterlogged") == "true"):
                raise SystemExit(
                    f"{name}{properties} {'holds' if held else 'holds no'} "
                    f"{field}, and is not always {field}; no table can say "
                    f"which of its states do")
    return always


def fluid_blocks(states) -> frozenset[str]:
    """The blocks whose every state holds fluid. Any other state must hold
    it exactly when it is waterlogged -- or no table could say which states
    do, and this refuses."""
    return _whole_blocks(states, "fluid")


def water_blocks(states) -> frozenset[str]:
    """:func:`fluid_blocks` for water alone: the blocks whose every state
    holds water, every other state holding it exactly when it is
    waterlogged. Lava is in neither half."""
    return _whole_blocks(states, "water")


def summarize(states, faces, collision) -> dict[str, dict]:
    """Per block: property domains, the commonest record, the exceptions;
    whether its states hold fluid and water; and the commonest record of
    how water moves through it, with that record's own exceptions."""
    by_block: dict[str, list] = {}
    for state in states:
        by_block.setdefault(state[0], []).append(state)
    blocks = {}
    for name in sorted(by_block):
        group = by_block[name]
        entry: dict = {}
        keys = sorted(group[0][1])
        if keys:
            entry["props"] = {key: sorted({s[1][key] for s in group})
                              for key in keys}
        records = _records(group, RECORD, faces)
        counts = collections.Counter(records)
        default = max(sorted(counts), key=lambda r: counts[r])
        entry["shape"] = list(default)
        exceptions = _exceptions(group, records, default)
        if exceptions:
            entry["except"] = exceptions
        fluid = [s[2].fluid for s in group]
        if all(fluid):
            entry["fluid"] = "always"
        elif any(fluid):
            if not all(f == (s[1].get("waterlogged") == "true")
                       for f, s in zip(fluid, group)):
                raise SystemExit(f"{name} holds fluid in states that are not "
                                 f"waterlogged; this fixture cannot say which")
            entry["fluid"] = "waterlogged"
        if group[0][2].falls:
            entry["falls"] = True
        if group[0][2].heightmap != "both":
            entry["heightmap"] = group[0][2].heightmap
        water = [s[2].water for s in group]
        if all(water):
            entry["water"] = "always"
        elif any(water):
            if not all(w == (s[1].get("waterlogged") == "true")
                       for w, s in zip(water, group)):
                raise SystemExit(f"{name} holds water in states that are not "
                                 f"waterlogged; this fixture cannot say which")
            entry["water"] = "waterlogged"
        flows = _records(group, FLOW, faces, collision)
        counts = collections.Counter(flows)
        default = max(sorted(counts), key=lambda r: counts[r])
        entry["flow"] = list(default)
        exceptions = _exceptions(group, flows, default)
        if exceptions:
            entry["flow_except"] = exceptions
        blocks[name] = entry
    return _alias(blocks)


def _alias(blocks: dict) -> dict:
    """Point each block whose every state reads exactly like an earlier
    block's at that block: sixty stairs and twenty walls share one table
    each, and spelling it out sixty times is a megabyte of nothing."""
    seen: dict[str, str] = {}
    out = {}
    for name, entry in blocks.items():
        key = json.dumps(entry, sort_keys=True)
        if key in seen:
            out[name] = {"like": seen[key]}
        else:
            seen[key] = name
            out[name] = entry
    return out


def write(blocks: dict, game: str, data_version: int, count: int, *,
          tags: dict, spawn_light: dict, villagers: dict, masks: list,
          face_sets: list, collision_masks: list,
          collision_sets: list) -> None:
    def compact(value) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    def mapping(key: str, items: dict) -> list[str]:
        names = list(items)
        return [f' "{key}": {{', *(
            f"  {json.dumps(name)}: {compact(items[name])}"
            f"{',' if i + 1 < len(names) else ''}"
            for i, name in enumerate(names)), " },"]

    def listing(key: str, items: list) -> list[str]:
        return [f' "{key}": [', *(
            f"  {compact(item)}{',' if i + 1 < len(items) else ''}"
            for i, item in enumerate(items)), " ],"]

    lines = [
        "{",
        f' "generated_by": "tools/probe_block_shapes.py -- rerun it after a '
        f'game update, never edit by hand",',
        f' "game": {json.dumps(game)},',
        f' "data_version": {data_version},',
        f' "states": {count},',
        *mapping("dimension_types", spawn_light),
        *mapping("villager_types", villagers),
        *mapping("tags", {name: sorted(members)
                          for name, members in tags.items()}),
        *listing("masks", [format(mask, "064x") for mask in masks]),
        *listing("faces", [list(faces) for faces in face_sets]),
        *listing("collision_masks",
                 [format(mask, "064x") for mask in collision_masks]),
        *listing("collision_faces", [list(faces) for faces in collision_sets]),
        ' "blocks": {',
    ]
    names = list(blocks)
    for i, name in enumerate(names):
        comma = "," if i + 1 < len(names) else ""
        lines.append(f"  {json.dumps(name)}: {compact(blocks[name])}{comma}")
    lines += [" }", "}", ""]
    with open(FIXTURE_BLOCK_SHAPES, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


FACTS_OUT = os.path.join(REPO_ROOT, "heaton_voxel", "craft", "block_facts.py")
#: Every face sturdy both ways, solid, conducting, dark and opaque, a floor
#: a monster stands on and no room for one, closing no face to light by its
#: shape: a plain cube, and what the run-time table assumes for any block it
#: does not list.
CUBE = (63, 63, 1, 1, 0, 15, 1, 0, 0)


def _names(names) -> list[str]:
    return [f"    {name!r}," for name in sorted(names)]


def _table_rows(by_block: dict, fields, cube: tuple, faces: dict,
                collision: dict | None = None) -> tuple[list[str], list[str]]:
    """``(rows, likes)``: the run-time table of ``fields`` for every block
    whose states are not all ``cube``, each as its commonest record and the
    exceptions, and each block that reads exactly as an earlier one as a
    pointer to it."""
    rows, likes, seen = [], [], {}
    for name in sorted(by_block):
        group = by_block[name]
        records = _records(group, fields, faces, collision)
        counts = collections.Counter(records)
        default = max(sorted(counts), key=lambda r: counts[r])
        exceptions = _exceptions(group, records, default)
        if default == cube and not exceptions:
            continue
        excepted = tuple((tuple(sorted(when.items())), tuple(record))
                         for when, record in exceptions)
        text = f"({default!r}, {excepted!r})"
        if text in seen:
            likes.append(f"    {name!r}: {seen[text]!r},")
        else:
            seen[text] = name
            rows.append(f"    {name!r}: {text},")
    return rows, likes


def write_facts(states, game: str, data_version: int, faces: dict,
                masks: list, face_sets: list, collision: dict,
                collision_masks: list, collision_sets: list) -> tuple[int, int]:
    """The run-time facts table: every block that is not a plain cube, plus
    heightmap membership and fluid; and beside it the table of how water
    moves through every block that is not a plain cube to water. Returns
    how many blocks each lists."""
    by_block: dict[str, list] = {}
    for state in states:
        by_block.setdefault(state[0], []).append(state)
    rows, likes = _table_rows(by_block, FACTS, CUBE, faces)
    # A plain cube to water: nothing enters it, it holds nothing, and its
    # collision is a wall on every face.
    cube_flow = (0, 0, 0, collision[(FULL_FACE,) * 6])
    flow_rows, flow_likes = _table_rows(by_block, FLOW, cube_flow, faces,
                                        collision)
    membership = {name: group[0][2].heightmap for name, group in by_block.items()}
    lines = [
        '"""How each block state holds, powers, lights and spawns, from the game. GENERATED.',
        "",
        f"Written by tools/probe_block_shapes.py from Minecraft {game} (DataVersion",
        f"{data_version}). Regenerate rather than edit.",
        "",
        "Every block the game keeps attached to another -- a button, a torch, a",
        "ladder, a lantern, a sign -- is kept only while the face it hangs on",
        "is sturdy enough, and an iron door opens only when a powered *conductor*",
        "touches it. :func:`facts` answers both, state by state, from the",
        "game's own ``isFaceSturdy``, ``isSolid`` and ``isRedstoneConductor``,",
        "and gives the ``getLightEmission`` and ``getLightDampening`` the light",
        "simulation runs on, the faces a shape closes to light",
        "(``LightEngine.getOcclusionShape``), whether a monster can stand on",
        "the state (``isValidSpawn``), and whether the state passes the game's",
        "per-cell test of room to spawn in (``isValidEmptySpawnBlock``; the",
        "mob's box must also clear collision, which no one state answers).",
        "",
        "It also says how water moves through the state, as the game's",
        "``FlowingFluid`` asks: whether water can enter the cell at all",
        "(``canHoldAnyFluid``: a ``LiquidBlockContainer``, or a block tagged",
        "``washed_away_by_fluids`` -- air, plants, torches, rails, carpets, snow",
        "layers, and water and lava themselves), whether the state is such a",
        "container and takes a water source (``canPlaceLiquid``; none takes a",
        "flowing one), and the faces of its collision shape that water's wall",
        "test sees (``canPassThroughWall``, held to the game for every pair of",
        "distinct collision shapes when this table was written).",
        "",
        "Beside them: whether a motion-blocking heightmap counts a block",
        "(:func:`in_heightmap`, by block, as the game's tags are), whether a",
        "state holds fluid (:func:`holds_fluid`) and whether it holds water",
        "(:func:`holds_water`), and whether the game has the",
        "block at all (:func:`known`) -- for an id it has never heard of",
        ":func:`facts` answers a cube, the safe guess for a wall, while the",
        "game loads that id as air.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import NamedTuple",
        "",
        "from ..block_defaults import BLOCK_DEFAULTS, default_properties",
        "from .block_registry import BLOCK_IDS, FLUIDS, WATER_FLUIDS",
        "",
        f'GAME_VERSION = "{game}"',
        f"DATA_VERSION = {data_version}",
        "",
        "#: Face order of every mask: bit ``i`` is ``FACES[i]``.",
        'FACES = ("down", "up", "north", "south", "west", "east")',
        "",
        "#: A face closed to light all over. A face mask is 256 bits on the",
        "#: face's plane: bit ``u * 16 + v`` is the sixteenth",
        "#: ``[u, u+1) x [v, v+1)``, ``(u, v)`` being ``(y, z)`` on a west or",
        "#: east face, ``(x, z)`` on a down or up face and ``(x, y)`` on a north",
        "#: or south one. Two neighbors close the face between them exactly",
        "#: when their two masks together are this (``Shapes.faceShapeOccludes``,",
        "#: checked against the game pair by pair when this table was written).",
        "FULL_FACE = (1 << 256) - 1",
        "#: What a block that does not occlude by its shape shows each side: a",
        "#: full opaque cube stops light by its dampening instead.",
        "NO_FACES = (0, 0, 0, 0, 0, 0)",
        "#: What a full cube's collision shows water each side: a wall all over.",
        "#: The collision masks are laid out as the light masks are, and two",
        "#: neighbors let water between them exactly when their two masks",
        "#: together are not ``FULL_FACE`` (``FlowingFluid.canPassThroughWall``,",
        "#: checked against the game for every pair of distinct collision",
        "#: shapes when this table was written).",
        "FULL_FACES = (FULL_FACE,) * 6",
        "",
        "",
        "class Facts(NamedTuple):",
        '    """How a block state holds, powers, lights, spawns and takes water."""',
        "",
        "    #: Faces sturdy enough for anything (``SupportType.FULL``), as a mask.",
        "    sturdy: int",
        "    #: Faces sturdy at their center (``SupportType.CENTER``): a torch's",
        "    #: or a lantern's footing, as a mask.",
        "    center: int",
        "    #: What a sign needs to stand on or hang from.",
        "    solid: bool",
        "    #: Whether power reaching it passes on to its neighbors.",
        "    conductor: bool",
        "    #: Block light it gives off.",
        "    emission: int",
        "    #: What it costs light to cross, before the floor of one per step.",
        "    dampening: int",
        "    #: Whether a monster can stand on it: ``isValidSpawn`` for a zombie,",
        "    #: or for a wither skeleton, the fire-immune rule (magma).",
        "    spawn_floor: bool = True",
        "    #: Whether it passes the game's per-cell test of room to spawn in:",
        "    #: ``isValidEmptySpawnBlock`` for a zombie -- not a full collision",
        "    #: cube, no fluid, no signal source, nothing tagged",
        "    #: ``prevent_mob_spawning_inside``, nothing that burns or hurts (a",
        "    #: wither rose, a berry bush, a cactus, powder snow). A monster needs",
        "    #: it in its feet cell and the cell above. It is not the whole test:",
        "    #: the game then also needs the mob's box clear of collision",
        "    #: (``noCollision`` on ``getSpawnAABB``), which this does not answer,",
        "    #: so a fence, a wall, a chest or a bottom slab passes.",
        "    spawn_space: bool = False",
        "    #: The faces its shape closes to light, one mask per face in ``FACES``",
        "    #: order (see ``FULL_FACE``).",
        "    faces: tuple = NO_FACES",
        "    #: Whether water can enter the cell at all (``canHoldAnyFluid``): a",
        "    #: container, or a block tagged ``washed_away_by_fluids`` -- which",
        "    #: holds water and lava themselves, so a flow's neighbor enters too.",
        "    enters: bool = False",
        "    #: Whether it is a ``LiquidBlockContainer``: water reaching it",
        "    #: waterlogs it, where it takes what reaches it, and never washes it",
        "    #: away.",
        "    container: bool = False",
        "    #: Whether, a container, it takes a water source (``canPlaceLiquid``",
        "    #: for ``Fluids.WATER``): a bottom slab does and a double one does",
        "    #: not. No container takes flowing water.",
        "    takes_source: bool = False",
        "    #: The faces of its collision shape that water's wall test sees, one",
        "    #: mask per face in ``FACES`` order (see ``FULL_FACES``), as",
        "    #: ``Shapes.mergedFaceOccludes`` cuts them: the layer of the shape's",
        "    #: grid nearest a face it ends on, and nothing on a face it stops",
        "    #: short of or runs past -- a fence's top. A sixteenth counts only",
        "    #: where the shape covers it whole (a chain stands on thirty-seconds),",
        "    #: and a shape the game offsets by position (bamboo, pointed",
        "    #: dripstone) is read at the origin.",
        "    collision: tuple = FULL_FACES",
        "",
        "    def holds(self, face: str, *, center: bool = False) -> bool:",
        '        """Whether ``face`` is sturdy, fully or (``center=True``) at its middle."""',
        "        return bool((self.center if center else self.sturdy)",
        "                    >> FACES.index(face) & 1)",
        "",
        "",
        "#: A plain cube: every block the table below does not name.",
        f"CUBE = Facts({CUBE[0]}, {CUBE[1]}, True, True, {CUBE[4]}, {CUBE[5]}, "
        f"True, False, NO_FACES)",
        "",
        "#: Every distinct face mask; index 0 is the empty face.",
        "_MASKS = (",
        *(f"    {mask:#x}," for mask in masks),
        ")",
        "",
        "#: Every distinct set of six faces, in ``FACES`` order; index 0 closes",
        "#: nothing.",
        "_FACE_SETS = tuple(tuple(_MASKS[i] for i in faces) for faces in (",
        *(f"    {faces!r}," for faces in face_sets),
        "))",
        "",
        "#: Block id -> (record, ((properties, record), ...)), record being",
        "#: (sturdy, center, solid, conductor, emission, dampening, spawn_floor,",
        "#: spawn_space, index into _FACE_SETS). The first matching exception",
        "#: wins.",
        "_FACTS = {",
        *rows,
        "}",
        "",
        "#: Blocks whose every state holds things exactly as another block's do.",
        "_LIKE = {",
        *likes,
        "}",
        "",
        "#: Blocks no motion-blocking heightmap counts: in neither",
        "#: ``blocks_motion_in_heightmap`` tag.",
        "_NOT_IN_HEIGHTMAP = frozenset((",
        *_names(n for n, where in membership.items() if where == "none"),
        "))",
        "",
        "#: Blocks only MOTION_BLOCKING counts, and MOTION_BLOCKING_NO_LEAVES",
        "#: does not.",
        "_LEAVES = frozenset((",
        *_names(n for n, where in membership.items() if where == "leaves"),
        "))",
        "",
        "#: Every distinct collision face mask; index 0 is the empty face.",
        "_COLLISION_MASKS = (",
        *(f"    {mask:#x}," for mask in collision_masks),
        ")",
        "",
        "#: Every distinct set of six collision faces, in ``FACES`` order; index",
        "#: 0 shows water nothing.",
        "_COLLISION_SETS = tuple(tuple(_COLLISION_MASKS[i] for i in faces)",
        "                        for faces in (",
        *(f"    {faces!r}," for faces in collision_sets),
        "))",
        "",
        "#: Block id -> (record, ((properties, record), ...)) as in ``_FACTS``,",
        "#: record being (enters, container, takes_source, index into",
        "#: _COLLISION_SETS), for every block that is not a plain cube to water:",
        "#: a block it does not name lets nothing in and is a wall all over.",
        "_FLOW = {",
        *flow_rows,
        "}",
        "",
        "#: Blocks water moves through exactly as another block's states let it.",
        "_FLOW_LIKE = {",
        *flow_likes,
        "}",
        "",
        "",
        "def _pick(entry, props) -> tuple:",
        '    """The record ``entry`` gives a state with ``props``: its first',
        '    matching exception\'s, or its commonest."""',
        "    record, exceptions = entry",
        "    for when, found in exceptions:",
        "        if all(props.get(key) == value for key, value in when):",
        "            return found",
        "    return record",
        "",
        "",
        "def facts(name: str, properties=None) -> Facts:",
        '    """What the game says about ``name[properties]``. Omitted properties',
        '    take their defaults, and a block the tables do not name is a cube."""',
        "    entry = _FACTS.get(_LIKE.get(name, name))",
        "    flow = _FLOW.get(_FLOW_LIKE.get(name, name))",
        "    if entry is None and flow is None:",
        "        return CUBE",
        "    props = None",
        "    if (entry is not None and entry[1]) or (flow is not None and flow[1]):",
        "        props = default_properties(name)",
        "        props.update(properties or {})",
        "    if entry is None:",
        "        held = CUBE[:9]",
        "    else:",
        "        (sturdy, center, solid, conductor, emission, dampening, spawn_floor,",
        "         spawn_space, faces) = _pick(entry, props)",
        "        held = (sturdy, center, bool(solid), bool(conductor), emission,",
        "                dampening, bool(spawn_floor), bool(spawn_space),",
        "                _FACE_SETS[faces])",
        "    if flow is None:",
        "        return Facts(*held)",
        "    enters, container, takes_source, collision = _pick(flow, props)",
        "    return Facts(*held, bool(enters), bool(container), bool(takes_source),",
        "                 _COLLISION_SETS[collision])",
        "",
        "",
        "def in_heightmap(name: str, *, leaves: bool = True) -> bool:",
        '    """Whether a motion-blocking heightmap stops at ``name``: the game\'s',
        "    ``blocks_motion_in_heightmap`` tag (MOTION_BLOCKING, and alone",
        "    OCEAN_FLOOR), or with ``leaves=False``",
        "    ``blocks_motion_in_heightmap_no_leaves``",
        "    (MOTION_BLOCKING_NO_LEAVES). By block, not by state: the tags name",
        "    blocks. Fluid is the caller's to add, as the game adds it -- the two",
        "    motion-blocking maps also stop at any state that holds fluid",
        "    (:func:`holds_fluid`). A block the game has never heard of counts,",
        '    so the unknown is solid."""',
        "    if name in _NOT_IN_HEIGHTMAP:",
        "        return False",
        "    return leaves or name not in _LEAVES",
        "",
        "",
        "def holds_fluid(name: str, properties=None) -> bool:",
        '    """Whether ``name[properties]`` holds water or lava: a block every',
        "    state of which is fluid (``FLUIDS``: water, lava, a bubble column,",
        "    kelp, seagrass), or a waterlogged state. An omitted ``waterlogged``",
        "    takes the block's default, so a bare sea pickle or coral is wet, as",
        "    the game reads it. A block with no ``waterlogged`` property never is,",
        "    whatever the properties say, and neither is one the game has never",
        '    heard of."""',
        "    if name in FLUIDS:",
        "        return True",
        "    defaults = BLOCK_DEFAULTS.get(name)",
        '    if not defaults or "waterlogged" not in defaults:',
        "        return False",
        '    value = (properties or {}).get("waterlogged", defaults["waterlogged"])',
        '    return value == "true"',
        "",
        "",
        "def holds_water(name: str, properties=None) -> bool:",
        '    """Whether ``name[properties]`` holds water: a block every state of',
        "    which does (``WATER_FLUIDS``: water, a bubble column, kelp, seagrass),",
        "    or a waterlogged state, an omitted ``waterlogged`` taking the block's",
        "    default as in :func:`holds_fluid`, so a bare sea pickle is wet. Water",
        "    at any level counts, a flow as much as a source -- the game's",
        "    ``#water`` fluid tag -- and lava never does, which :func:`holds_fluid`",
        '    counts besides."""',
        "    return name in WATER_FLUIDS or (name not in FLUIDS",
        "                                    and holds_fluid(name, properties))",
        "",
        "",
        "def known(name: str) -> bool:",
        '    """Whether the game has a block called ``name``. :func:`facts` answers',
        "    a cube for an id it does not know, the safe guess for a wall, and the",
        "    game loads that id as air; a caller who must tell the two apart asks",
        '    this."""',
        "    return name in BLOCK_IDS",
        "",
    ]
    with open(FACTS_OUT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return len(rows) + len(likes), len(flow_rows) + len(flow_likes)


REGISTRY_OUT = os.path.join(REPO_ROOT, "heaton_voxel", "craft", "block_registry.py")


def _domain(values) -> tuple[str, ...]:
    """A property's values ascending: as numbers when every one is a number."""
    if all(value.isdigit() for value in values):
        return tuple(sorted(values, key=int))
    return tuple(sorted(values))


def write_registry(states, game: str, data_version: int,
                   fluids: frozenset, waters: frozenset) -> tuple[int, int, int]:
    """Every block id, its property domains, what falls and what is always
    fluid, and always water. Returns ``(blocks, with properties, distinct
    property sets)``."""
    domains: dict[str, dict[str, set]] = {}
    falls = set()
    for name, properties, shape in states:
        block = domains.setdefault(name, {})
        for key, value in properties.items():
            block.setdefault(key, set()).add(value)
        if shape.falls:
            falls.add(name)
    chosen = {name: tuple((key, _domain(values))
                          for key, values in sorted(block.items()))
              for name, block in domains.items()}
    sets = sorted(set(chosen.values()))
    where = {properties: i for i, properties in enumerate(sets)}
    with_properties = sum(1 for properties in chosen.values() if properties)
    lines = [
        '"""Every block the game has, and the values its properties take. GENERATED.',
        "",
        f"Written by tools/probe_block_shapes.py from Minecraft {game} (DataVersion",
        f"{data_version}): {len(chosen)} blocks, {with_properties} of them with "
        f"properties, in {len(sets) - 1}",
        "distinct sets. Regenerate rather than edit.",
        "",
        "The game does not refuse a block it does not have: it loads the id as",
        "air, logs a line nobody reads, and writes the air back. A property the",
        "block does not take, or a value outside one, it drops the same way. This",
        "is the list it checks them against. :func:`properties_of` answers None",
        "for an id the game does not have and an empty dict for a block with no",
        "properties, so the two never look alike. The judged tier's; the lower",
        "tier writes whatever it is given, by design.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        f'GAME_VERSION = "{game}"',
        f"DATA_VERSION = {data_version}",
        "",
        "#: Every distinct property set, ``((property, (value, ...)), ...)``: the",
        "#: properties in name order, the values ascending, as numbers where",
        "#: every value is one. Index 0 is the block with none.",
        "_SETS: tuple[tuple[tuple[str, tuple[str, ...]], ...], ...] = (",
        *(f"    {properties!r}," for properties in sets),
        ")",
        "",
        "#: Block id -> its property set, an index into ``_SETS``.",
        "_BLOCKS: dict[str, int] = {",
        *(f"    {name!r}: {where[chosen[name]]}," for name in sorted(chosen)),
        "}",
        "",
        "#: Every block id the game has.",
        "BLOCK_IDS = frozenset(_BLOCKS)",
        "",
        "#: Blocks that fall when nothing holds them up: the game's ``Fallable``.",
        "FALLS = frozenset((",
        *_names(falls),
        "))",
        "",
        "#: Blocks every state of which holds fluid. Any other state holds",
        "#: fluid exactly when it is waterlogged.",
        "FLUIDS = frozenset((",
        *_names(fluids),
        "))",
        "",
        "#: Blocks every state of which holds water: the ``FLUIDS`` that are",
        "#: not lava, whose fluid is the game's ``#water`` at every level. Any",
        "#: other state holds water exactly when it is waterlogged.",
        "WATER_FLUIDS = frozenset((",
        *_names(waters),
        "))",
        "",
        "",
        "def properties_of(name: str) -> dict[str, tuple[str, ...]] | None:",
        '    """Each property ``name`` takes, in name order, and the values it may',
        "    hold; ``{}`` for a block with none, and None for an id the game does",
        '    not have."""',
        "    index = _BLOCKS.get(name)",
        "    return None if index is None else dict(_SETS[index])",
        "",
    ]
    if sets[0] != ():
        raise SystemExit("every block has properties; _SETS[0] is not empty")
    with open(REGISTRY_OUT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return len(chosen), with_properties, len(sets) - 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True,
                        help="an installed game version, e.g. 26.3")
    args = parser.parse_args()

    names = (*VENDORED_TAGS, *HEIGHTMAP_TAGS)
    classpath, version_info = game_classpath(args.version)
    game = str(version_info["id"])
    data_version = int(version_info["world_version"])
    states, bound, villagers = probe(args.version, classpath, names)
    seen = {name for name, _, _ in states}
    # The game's own jar comes first on its classpath.
    with zipfile.ZipFile(classpath[0]) as archive:
        tags = read_tags(archive, names, seen)
        spawn_light = read_dimension_types(archive)
        check_villager_biomes(archive, villagers)
    # The zip reading is held to the game's own tag loader, member by member.
    for name in names:
        if tags[name] != bound[name]:
            raise SystemExit(
                f"tag {name}: the jar's files give {len(tags[name])} blocks, "
                f"the bound registry {len(bound[name])}; only in the files: "
                f"{sorted(tags[name] - bound[name])[:5]}, only bound: "
                f"{sorted(bound[name] - tags[name])[:5]}")
    heightmap = heightmap_membership(sorted(seen), tags)
    states = [(name, properties, shape._replace(heightmap=heightmap[name]))
              for name, properties, shape in states]
    fluids = fluid_blocks(states)
    waters = water_blocks(states)
    masks, face_sets, faces = face_table(states)
    collision_masks, collision_sets, collision = collision_table(states)
    blocks = summarize(states, faces, collision)
    write(blocks, game, data_version, len(states),
          tags={name: tags[name] for name in VENDORED_TAGS},
          spawn_light=spawn_light, villagers=villagers, masks=masks,
          face_sets=face_sets, collision_masks=collision_masks,
          collision_sets=collision_sets)
    listed, flowing = write_facts(states, game, data_version, faces, masks,
                                  face_sets, collision, collision_masks,
                                  collision_sets)
    registered, with_properties, property_sets = write_registry(
        states, game, data_version, fluids, waters)

    # The summary must give back every state exactly, or the tests would be
    # holding the tables to something the game never said.
    expanded = sorted((n, sorted(p.items()), s) for n, p, s in block_shapes())
    original = sorted((n, sorted(p.items()), s) for n, p, s in states)
    if expanded != original:
        raise SystemExit("the written summary does not reproduce the probe")
    if block_tags() != tags or dimension_types() != spawn_light:
        raise SystemExit("the written tags do not reproduce the jar's")
    if villager_types() != VillagerTypes(villagers["by_biome"],
                                         villagers["default"],
                                         frozenset(villagers["kinds"])):
        raise SystemExit("the written villager types do not reproduce the "
                         "game's")
    exceptions = sum(len(entry.get("except", ())) for entry in blocks.values())
    flow_exceptions = sum(len(entry.get("flow_except", ()))
                          for entry in blocks.values())
    print(f"{game} (DataVersion {data_version}): {len(states)} states of "
          f"{len(blocks)} blocks, {exceptions} exceptions")
    print(f"  wrote {os.path.relpath(FIXTURE_BLOCK_SHAPES, REPO_ROOT)} "
          f"({len(VENDORED_TAGS)} tags, {len(masks)} face masks in "
          f"{len(face_sets)} sets, {len(collision_masks)} collision masks "
          f"in {len(collision_sets)} sets with {flow_exceptions} flow "
          f"exceptions, {len(spawn_light)} dimension types, "
          f"{len(villagers['kinds'])} villager types over "
          f"{len(villagers['by_biome'])} biomes)")
    print(f"  wrote {os.path.relpath(FACTS_OUT, REPO_ROOT)} "
          f"({listed} blocks that are not plain cubes, {flowing} that are "
          f"not plain cubes to water)")
    print(f"  wrote {os.path.relpath(REGISTRY_OUT, REPO_ROOT)} "
          f"({registered} blocks, {with_properties} with properties, in "
          f"{property_sets} sets; {len(fluids)} always fluid, {len(waters)} "
          f"always water)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
