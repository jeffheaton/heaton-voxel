#!/usr/bin/env python3
"""Ask the game what every block state shows a wall standing under it.

A wall reads the block above it on every block update
(``WallBlock.updateShape``): the down face of that block's *collision*
shape, which it tests against four side shapes -- a side it is joined on
runs ``tall`` when the face covers that side's strip, ``low`` when it does
not -- and against a post shape; whether the block is in the
``wall_post_override`` tag (torches, signs, banners, pressure plates); and
whether it is itself a wall with its post up, which raises this one's
post outright (``WallBlock.shouldRaisePost``). None of that is in a block's
name. Leaves hold a full collision cube and no attachment face, so a wall
under a tree runs tall; a top slab covers nothing below it and a bottom
slab everything; an open trapdoor nothing, a closed bottom one the lot.

This runs a few lines of Java against the installed jar, through the
launcher's own runtime, as ``tools/probe_block_shapes.py`` does -- the
game's own ``isCovered`` and test shapes, by reflection, over every block
state, with the game's tags bound out of its bundled data pack -- and
writes one table per twin from the one run, so the two cannot disagree:

    heaton_voxel/wall_cover.py
    csharp/HeatonVoxel/WallCover.generated.cs

Both are derived; edit neither by hand. ``heaton_voxel.connect`` is the
reference for how the table is *used*. Each state's answer is a code:
north, east, south and west (the side strips its underside covers), post
(the post's center), and override (the tag), one bit each. Most blocks
answer one code in every state; the rest are keyed by the fewest
properties that decide it -- a slab's ``type``, a stair's ``half``, a
fence's arms -- and families sharing a table share one entry. The
summary is replayed over every state the game listed before anything is
written.

The same run asks the game for its own answer too: for every block state
above a cobblestone wall, and every joining of the wall's four sides, the
state ``WallBlock.updateSides`` and ``shouldRaisePost`` give it. Once the
table is written, ``heaton_voxel.connect`` derives all 16 joinings under
every state with it, and a single disagreement puts the old files back and
refuses -- so the table and the rule that reads it ship only together and
only as the game has them.

Usage::

    python tools/generate_wall_cover.py --version 26.3
"""

from __future__ import annotations

import argparse
import itertools
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
PYTHON_OUT = os.path.join(REPO_ROOT, "heaton_voxel", "wall_cover.py")
CSHARP_OUT = os.path.join(REPO_ROOT, "csharp", "HeatonVoxel",
                          "WallCover.generated.cs")

from generate_block_defaults import JRE, game_classpath  # noqa: E402

#: The code's bits, in the order the Java writes them.
NORTH, EAST, SOUTH, WEST, POST, OVERRIDE = 1, 2, 4, 8, 16, 32

PROBE = r"""
import java.io.PrintStream;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import net.minecraft.SharedConstants;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.Registry;
import net.minecraft.core.RegistryAccess;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.Bootstrap;
import net.minecraft.server.packs.PackType;
import net.minecraft.server.packs.repository.ServerPacksSource;
import net.minecraft.server.packs.resources.MultiPackResourceManager;
import net.minecraft.tags.BlockTags;
import net.minecraft.tags.TagLoader;
import net.minecraft.world.level.EmptyBlockGetter;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.WallBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.WallSide;
import net.minecraft.world.phys.shapes.VoxelShape;

public class WallCoverProbe {
    static int side(WallSide side) {
        return side == WallSide.NONE ? 0 : side == WallSide.LOW ? 1 : 2;
    }

    /** args: the table to write. */
    public static void main(String[] args) throws Exception {
        SharedConstants.tryDetectVersion();
        Bootstrap.bootStrap();
        // wall_post_override is a tag, which a bare Bootstrap binds empty:
        // bind the game's own, out of its bundled data pack.
        MultiPackResourceManager resources = new MultiPackResourceManager(
            PackType.SERVER_DATA,
            List.of(ServerPacksSource.createVanillaPackSource().fullResources()));
        for (Registry.PendingTags<?> pending : TagLoader.loadTagsForExistingRegistries(
                resources, RegistryAccess.fromRegistryOfRegistries(BuiltInRegistries.REGISTRY)))
            pending.apply();
        // The wall's own test, and its own shapes.
        Method covered = WallBlock.class.getDeclaredMethod(
            "isCovered", VoxelShape.class, VoxelShape.class);
        covered.setAccessible(true);
        Field postField = WallBlock.class.getDeclaredField("TEST_SHAPE_POST");
        postField.setAccessible(true);
        Field sidesField = WallBlock.class.getDeclaredField("TEST_SHAPES_WALL");
        sidesField.setAccessible(true);
        VoxelShape post = (VoxelShape) postField.get(null);
        @SuppressWarnings("unchecked")
        Map<Direction, VoxelShape> sides = (Map<Direction, VoxelShape>) sidesField.get(null);
        Direction[] order = {Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST};
        // And the wall's own answer under each state: updateShape's two
        // halves, for a cobblestone wall joined on each set of sides.
        Method updateSides = WallBlock.class.getDeclaredMethod("updateSides",
            BlockState.class, boolean.class, boolean.class, boolean.class,
            boolean.class, VoxelShape.class);
        updateSides.setAccessible(true);
        Method raisePost = WallBlock.class.getDeclaredMethod("shouldRaisePost",
            BlockState.class, BlockState.class, VoxelShape.class);
        raisePost.setAccessible(true);
        BlockState wall = Blocks.COBBLESTONE_WALL.defaultBlockState();
        WallBlock wallBlock = (WallBlock) wall.getBlock();
        PrintStream out = new PrintStream(args[0], "UTF-8");
        for (Block block : BuiltInRegistries.BLOCK) {
            String name = BuiltInRegistries.BLOCK.getKey(block).toString();
            for (BlockState state : block.getStateDefinition().getPossibleStates()) {
                String props = state.getValues().map(Object::toString).sorted()
                    .collect(Collectors.joining(","));
                // What WallBlock.updateShape reads of the block above.
                VoxelShape face = state.getCollisionShape(EmptyBlockGetter.INSTANCE, BlockPos.ZERO)
                                       .getFaceShape(Direction.DOWN);
                int code = 0;
                for (int i = 0; i < 4; i++)
                    if ((Boolean) covered.invoke(null, face, sides.get(order[i])))
                        code |= 1 << i;
                if ((Boolean) covered.invoke(null, face, post))
                    code |= 16;
                if (state.is(BlockTags.WALL_POST_OVERRIDE))
                    code |= 32;
                StringBuilder line = new StringBuilder(
                    name + "\t" + props + "\t" + code + "\t"
                    + (block instanceof WallBlock ? 1 : 0));
                // Joined north, east, south and west by the bits of m: the
                // sides, two bits each in that order, and 256 for the post.
                for (int m = 0; m < 16; m++) {
                    BlockState sided = (BlockState) updateSides.invoke(wallBlock, wall,
                        (m & 1) != 0, (m & 2) != 0, (m & 4) != 0, (m & 8) != 0, face);
                    boolean up = (Boolean) raisePost.invoke(wallBlock, sided, state, face);
                    line.append("\t").append(side(sided.getValue(WallBlock.NORTH))
                        | side(sided.getValue(WallBlock.EAST)) << 2
                        | side(sided.getValue(WallBlock.SOUTH)) << 4
                        | side(sided.getValue(WallBlock.WEST)) << 6
                        | (up ? 256 : 0));
                }
                out.println(line);
            }
        }
        out.close();
        System.exit(0);
    }
}
"""


def probe(version: str, classpath: list[str]):
    """``[(name, properties, code, is_wall, walls)]`` for every block state
    of the installed game, in registry order: ``walls`` is the game's own
    state for a cobblestone wall under it, for each of the 16 joinings of
    its sides (bit 1 north, 2 east, 4 south, 8 west), as two bits a side
    (0 none, 1 low, 2 tall) in that order and 256 for the post."""
    work = tempfile.mkdtemp(prefix="heaton-voxel-wall-cover-")
    try:
        source = os.path.join(work, "WallCoverProbe.java")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write(PROBE)
        table = os.path.join(work, "cover.tsv")
        print(f"probing every {version} block state ...", file=sys.stderr)
        run = subprocess.run(
            [JRE, "-Xmx2G", "-cp", os.pathsep.join(classpath), source, table],
            cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)
        if run.returncode != 0 or not os.path.exists(table):
            raise SystemExit(f"the probe failed -- it needs a JDK, which the "
                             f"launcher's runtime is:\n{run.stdout[-2000:]}")
        states = []
        with open(table, encoding="utf-8") as handle:
            for line in handle:
                name, props, code, wall, *walls = line.rstrip("\n").split("\t")
                if len(walls) != 16:
                    raise SystemExit(f"{name}: {len(walls)} wall answers, not 16")
                properties = (dict(pair.split("=", 1) for pair in props.split(","))
                              if props else {})
                states.append((name, properties, int(code), wall == "1",
                               tuple(int(found) for found in walls)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return states


def summarize(states):
    """``(uniform, tables, keyed)``: ``{code: [names]}`` for the blocks
    answering one code in every state; the distinct ``(keys, {values:
    code})`` tables, ``values`` the state's values of ``keys`` joined by
    commas; and ``{table index: [names]}`` for the blocks read through one.
    A block's keys are the fewest of its properties that decide its code,
    the first such set in name order."""
    by_block: dict[str, list[tuple[dict, int]]] = {}
    for name, properties, code, wall, _ in states:
        if wall != name.endswith("_wall"):
            raise SystemExit(
                f"{name}: {'a' if wall else 'not a'} WallBlock, and connect "
                f"tells a wall by its _wall suffix")
        by_block.setdefault(name, []).append((properties, code))
    uniform: dict[int, list[str]] = {}
    tables: list[tuple[tuple[str, ...], dict[str, int]]] = []
    keyed: dict[int, list[str]] = {}
    for name in sorted(by_block):
        found = by_block[name]
        if len({code & OVERRIDE for _, code in found}) > 1:
            raise SystemExit(f"{name}: in the override tag in some states and "
                             f"not others")
        codes = {code for _, code in found}
        if len(codes) == 1:
            uniform.setdefault(codes.pop(), []).append(name)
            continue
        chosen = None
        keys = sorted(found[0][0])
        for count in range(1, len(keys) + 1):
            for combo in itertools.combinations(keys, count):
                table: dict[str, int] = {}
                if all(table.setdefault(",".join(p[k] for k in combo), code) == code
                       for p, code in found):
                    chosen = (combo, dict(sorted(table.items())))
                    break
            if chosen is not None:
                break
        if chosen is None:
            raise SystemExit(f"{name}: no set of its properties decides it")
        if chosen not in tables:
            tables.append(chosen)
        keyed.setdefault(tables.index(chosen), []).append(name)
    return dict(sorted(uniform.items())), tables, keyed


def replay(states, uniform, tables, keyed) -> None:
    """Refuse a summary that does not give back every state's code."""
    code_of = {name: code for code, names in uniform.items() for name in names}
    table_of = {name: index for index, names in keyed.items() for name in names}
    for name, properties, code, _, _ in states:
        if name in code_of:
            got = code_of[name]
        else:
            keys, table = tables[table_of[name]]
            got = table.get(",".join(properties[k] for k in keys))
        if got != code:
            raise SystemExit(f"the summary gives {name}[{properties}] {got}, "
                             f"the game {code}")


def _provenance(game: str, data_version: int, states, uniform, tables,
                keyed) -> str:
    blocks = sum(len(names) for names in uniform.values()) + sum(
        len(names) for names in keyed.values())
    by_state = sum(len(names) for names in keyed.values())
    return (f"Generated by tools/generate_wall_cover.py from Minecraft {game} "
            f"(DataVersion {data_version}): {len(states)} states of {blocks} "
            f"blocks, {by_state} of them read by state through "
            f"{len(tables)} tables. Regenerate rather than edit; both twins "
            f"are written by the same run.")


def write_python(game, data_version, states, uniform, tables, keyed) -> None:
    lines = [
        '"""What the underside of every block state covers of a wall below',
        "it, from the game's own shapes. GENERATED.",
        "",
        *textwrap.wrap(_provenance(game, data_version, states, uniform, tables,
                                   keyed), 76),
        "",
        "A wall reads the block above it on every block update: the down face",
        "of that block's collision shape against the wall's four side strips",
        "and its post, and whether the block is in the game's",
        "``wall_post_override`` tag. :func:`wall_cover` is that answer as a",
        "code, :data:`NORTH` to :data:`OVERRIDE`, one bit each;",
        ":mod:`heaton_voxel.connect` is how it is used.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from .block_defaults import BLOCK_DEFAULTS",
        "",
        f'GAME_VERSION = "{game}"',
        f"DATA_VERSION = {data_version}",
        "",
        "#: The side strips a wall tests its neighbor above against, the post's",
        "#: center, and the tag that raises a post over anything.",
        "NORTH, EAST, SOUTH, WEST, POST, OVERRIDE = 1, 2, 4, 8, 16, 32",
        "",
        "#: Blocks answering one code in every state, by that code.",
        "_UNIFORM: dict[int, tuple[str, ...]] = {",
    ]
    for code, names in uniform.items():
        lines.append(f"    {code}: (")
        lines += [f'        "{name}",' for name in names]
        lines.append("    ),")
    lines += [
        "}",
        "",
        "#: ``(keys, {values: code})``: the properties that decide a block's",
        "#: code, and the code for each combination of their values, joined",
        "#: by commas in the order of ``keys``.",
        "_TABLES: tuple[tuple[tuple[str, ...], dict[str, int]], ...] = (",
    ]
    for keys, table in tables:
        spelled = ", ".join(f'"{key}"' for key in keys) + ("," if len(keys) == 1 else "")
        lines.append(f"    (({spelled}), {{")
        lines += [f'        "{values}": {code},' for values, code in table.items()]
        lines.append("    }),")
    lines += [
        ")",
        "",
        "#: Blocks read through a table, by its index in :data:`_TABLES`.",
        "_KEYED: dict[int, tuple[str, ...]] = {",
    ]
    for index, names in sorted(keyed.items()):
        lines.append(f"    {index}: (")
        lines += [f'        "{name}",' for name in names]
        lines.append("    ),")
    lines += [
        "}",
        "",
        "_CODE = {name: code for code, names in _UNIFORM.items() for name in names}",
        "_TABLE = {name: _TABLES[index] for index, names in _KEYED.items()",
        "          for name in names}",
        "",
        "",
        "def wall_cover(name: str, properties=None) -> int | None:",
        '    """What ``name[properties]`` shows a wall standing under it, as a',
        "    code of :data:`NORTH`, :data:`EAST`, :data:`SOUTH` and :data:`WEST`",
        "    (the side strips its underside covers, so a side joined there runs",
        "    tall), :data:`POST` (the post's center) and :data:`OVERRIDE` (in",
        "    ``wall_post_override``). A property the state leaves out is its",
        "    block's default, as the game reads it. None for a block the game",
        "    does not have, or a value one of its properties does not take.",
        '    """',
        "    code = _CODE.get(name)",
        "    if code is not None:",
        "        return code",
        "    found = _TABLE.get(name)",
        "    if found is None:",
        "        return None",
        "    keys, table = found",
        "    given = properties or {}",
        "    defaults = BLOCK_DEFAULTS.get(name, {})",
        '    values = ",".join(given.get(key, defaults.get(key, "")) for key in keys)',
        "    return table.get(values)",
        "",
    ]
    with open(PYTHON_OUT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def write_csharp(game, data_version, states, uniform, tables, keyed) -> None:
    lines = [
        "// <auto-generated>",
        *("//     " + line for line in textwrap.wrap(
            _provenance(game, data_version, states, uniform, tables, keyed), 70)),
        "// </auto-generated>",
        "//",
        "// Twin of heaton_voxel/wall_cover.py. A wall reads the block above it",
        "// on every block update: the down face of that block's collision shape",
        "// against the wall's four side strips and its post, and whether the",
        "// block is in the game's wall_post_override tag. Connect.cs is how it",
        "// is used.",
        "",
        "#nullable enable",
        "",
        "using System;",
        "using System.Collections.Generic;",
        "using HeatonVoxel.Blocks;",
        "",
        "namespace HeatonVoxel",
        "{",
        "    public static class WallCover",
        "    {",
        f'        public const string GameVersion = "{game}";',
        f"        public const int DataVersion = {data_version};",
        "",
        "        public const int North = 1, East = 2, South = 4, West = 8,",
        "            Post = 16, Override = 32;",
        "",
        "        private static readonly Dictionary<string, int> Codes =",
        "            new Dictionary<string, int>(StringComparer.Ordinal);",
        "        private static readonly Dictionary<string, (string[] Keys, Dictionary<string, int> Table)> Tables =",
        "            new Dictionary<string, (string[], Dictionary<string, int>)>(StringComparer.Ordinal);",
        "",
        "        static WallCover()",
        "        {",
    ]
    for code, names in uniform.items():
        lines.append(f"            Uniform({code},")
        lines += [f'                "{name}",' for name in names[:-1]]
        lines.append(f'                "{names[-1]}");')
    for index, (keys, table) in enumerate(tables):
        lines.append(f"            Keyed(new[] {{ {', '.join(chr(34) + k + chr(34) for k in keys)} }},")
        lines.append("                new Dictionary<string, int>(StringComparer.Ordinal)")
        lines.append("                {")
        lines += [f'                    ["{values}"] = {code},'
                  for values, code in table.items()]
        lines.append("                },")
        names = keyed[index]
        lines += [f'                "{name}",' for name in names[:-1]]
        lines.append(f'                "{names[-1]}");')
    lines += [
        "        }",
        "",
        "        private static void Uniform(int code, params string[] names)",
        "        {",
        "            foreach (string name in names)",
        "                Codes[name] = code;",
        "        }",
        "",
        "        private static void Keyed(string[] keys, Dictionary<string, int> table,",
        "                                  params string[] names)",
        "        {",
        "            foreach (string name in names)",
        "                Tables[name] = (keys, table);",
        "        }",
        "",
        "        /// <summary>What the state shows a wall standing under it, as a",
        "        /// code of North, East, South and West (the side strips its",
        "        /// underside covers, so a side joined there runs tall), Post (the",
        "        /// post's center) and Override (in wall_post_override). A property",
        "        /// the state leaves out is its block's default, as the game reads",
        "        /// it. Null for a block the game does not have, or a value one of",
        "        /// its properties does not take.</summary>",
        "        public static int? Of(BlockState state)",
        "        {",
        "            if (Codes.TryGetValue(state.Name, out int code))",
        "                return code;",
        "            if (!Tables.TryGetValue(state.Name, out var found))",
        "                return null;",
        "            var defaults = BlockDefaults.DefaultProperties(state.Name);",
        "            var values = new string[found.Keys.Length];",
        "            for (int i = 0; i < values.Length; i++)",
        "            {",
        "                string? value = state.GetProperty(found.Keys[i]);",
        "                if (value == null)",
        "                {",
        '                    value = "";',
        "                    foreach (var pair in defaults)",
        "                        if (pair.Key == found.Keys[i])",
        "                            value = pair.Value;",
        "                }",
        "                values[i] = value;",
        "            }",
        '            return found.Table.TryGetValue(string.Join(",", values), out int keyed)',
        "                ? keyed",
        "                : (int?)null;",
        "        }",
        "    }",
        "}",
        "",
    ]
    with open(CSHARP_OUT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def check_written(states) -> None:
    """Refuse unless the written table, read back, gives every state the
    game's code, and ``heaton_voxel.connect`` with it derives the game's own
    state for a cobblestone wall under every block state, joined on every
    set of sides: what ships is what the game said."""
    import importlib

    for module in ("heaton_voxel.wall_cover", "heaton_voxel.connect"):
        sys.modules.pop(module, None)
    written = importlib.import_module("heaton_voxel.wall_cover")
    connect = importlib.import_module("heaton_voxel.connect")
    from heaton_voxel.blocks import BlockState

    wall = BlockState("minecraft:cobblestone_wall")
    joined = BlockState("minecraft:cobblestone_wall")
    air = BlockState("minecraft:air")
    sides = ("north", "east", "south", "west")
    offsets = {(0, 0, -1): 1, (1, 0, 0): 2, (0, 0, 1): 4, (-1, 0, 0): 8}
    names = ("none", "low", "tall")
    for name, properties, code, _, walls in states:
        if written.wall_cover(name, properties) != code:
            raise SystemExit(f"the written table gives {name}[{properties}] "
                             f"{written.wall_cover(name, properties)}, the "
                             f"game {code}")
        above = BlockState(name, properties)
        for mask, want in enumerate(walls):
            def block_at(x, y, z):
                if (x, y, z) == (0, 1, 0):
                    return above
                bit = offsets.get((x, y, z))
                return joined if bit is not None and mask & bit else air

            got = connect._derived_state(wall, block_at, 0, 0, 0).properties
            game = {side: names[want >> (2 * i) & 3] for i, side in enumerate(sides)}
            game["up"] = "true" if want & 256 else "false"
            if any(got[key] != value for key, value in game.items()):
                raise SystemExit(
                    f"under {above}, joined {[s for i, s in enumerate(sides) if mask >> i & 1]}, "
                    f"connect derives {dict(got)} and the game {game}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True,
                        help="an installed game version, e.g. 26.3")
    args = parser.parse_args()
    classpath, version_info = game_classpath(args.version)
    game = str(version_info["id"])
    data_version = int(version_info["world_version"])
    states = probe(args.version, classpath)
    uniform, tables, keyed = summarize(states)
    replay(states, uniform, tables, keyed)
    before = {}
    for path in (PYTHON_OUT, CSHARP_OUT):
        if os.path.exists(path):
            with open(path, "rb") as handle:
                before[path] = handle.read()
    write_python(game, data_version, states, uniform, tables, keyed)
    write_csharp(game, data_version, states, uniform, tables, keyed)
    try:
        check_written(states)
    except SystemExit:
        # Refuse rather than leave a table the game disagrees with.
        for path in (PYTHON_OUT, CSHARP_OUT):
            if path in before:
                with open(path, "wb") as handle:
                    handle.write(before[path])
            else:
                os.remove(path)
        raise
    print(f"{game} (DataVersion {data_version}): "
          + _provenance(game, data_version, states, uniform, tables, keyed)
          .split(": ", 1)[1].split(". ", 1)[0])
    print(f"  wrote {os.path.relpath(PYTHON_OUT, REPO_ROOT)}")
    print(f"  wrote {os.path.relpath(CSHARP_OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
