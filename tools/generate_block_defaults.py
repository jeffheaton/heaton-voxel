#!/usr/bin/env python3
"""Generate the block default-state tables from the game's own block report.

Since 26.3 a chunk palette stores a state at its block's *default* as the
bare id -- ``minecraft:water`` means ``water[level=0]``,
``minecraft:grass_block`` means ``grass_block[snowy=false]`` -- and only a
state that differs from that default as ``{id, properties}``. Reading such a
file therefore needs every block's default state, and the game is the one
authority on it: ``reports/blocks.json`` from its data generator lists each
block's properties and flags the default state. This turns that report into
one table per twin:

    heaton_voxel/block_defaults.py
    csharp/HeatonVoxel/Blocks/BlockDefaults.generated.cs

Both are derived; edit neither by hand. Python remains the reference for how
the table is *used* (``blocks.py``); this is only the data, and the two
files are written from one parse so they cannot disagree.

Usage::

    python tools/generate_block_defaults.py --version 26.3
    python tools/generate_block_defaults.py --report path/to/reports/blocks.json

``--version`` runs the game's generator itself: the launcher's own JRE, the
version's library manifest for the classpath (macOS layout, rules honored),
``net.minecraft.data.Main --reports``. The jar ships unobfuscated and the
report needs no network. ``--report`` takes a ``blocks.json`` produced any
other way, for a different platform or a server jar.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import textwrap
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_OUT = os.path.join(REPO_ROOT, "heaton_voxel", "block_defaults.py")
CSHARP_OUT = os.path.join(
    REPO_ROOT, "csharp", "HeatonVoxel", "Blocks", "BlockDefaults.generated.cs")

LAUNCHER = os.path.expanduser("~/Library/Application Support/minecraft")
JRE = os.path.join(
    LAUNCHER, "runtime", "java-runtime-epsilon", "mac-os-arm64",
    "java-runtime-epsilon", "jre.bundle", "Contents", "Home", "bin", "java")


# --------------------------------------------------------------------------
# Running the game's generator
# --------------------------------------------------------------------------


def _library_allowed(rules) -> bool:
    """The launcher's per-library OS rules, for the machine this runs on."""
    if not rules:
        return True
    system = {"Darwin": "osx", "Linux": "linux", "Windows": "windows"}.get(
        platform.system(), "")
    arch = platform.machine().lower()
    allowed = False
    for rule in rules:
        wanted = rule.get("os", {})
        matches = True
        if "name" in wanted and wanted["name"] != system:
            matches = False
        if "arch" in wanted and wanted["arch"] not in (arch, "arm64" if arch == "aarch64" else arch):
            matches = False
        if matches:
            allowed = rule.get("action") == "allow"
    return allowed


def game_classpath(version: str) -> tuple[list[str], dict]:
    """``(classpath, version.json)`` for an installed game version: its jar
    and every library the launcher would put beside it on this machine.
    Shared with ``tools/probe_block_shapes.py``."""
    version_dir = os.path.join(LAUNCHER, "versions", version)
    jar = os.path.join(version_dir, f"{version}.jar")
    manifest_path = os.path.join(version_dir, f"{version}.json")
    for path in (jar, manifest_path, JRE):
        if not os.path.exists(path):
            raise SystemExit(f"not installed: {path}")

    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    classpath = [jar]
    for library in manifest["libraries"]:
        artifact = library.get("downloads", {}).get("artifact")
        if artifact is None or not _library_allowed(library.get("rules")):
            continue
        path = os.path.join(LAUNCHER, "libraries", artifact["path"])
        if not os.path.exists(path):
            raise SystemExit(f"library missing from the launcher: {path}")
        classpath.append(path)

    with zipfile.ZipFile(jar) as archive:
        version_info = json.loads(archive.read("version.json"))
    return classpath, version_info


def run_game_generator(version: str) -> tuple[dict, str, int]:
    """``(blocks report, game id, DataVersion)`` from the installed version."""
    classpath, version_info = game_classpath(version)
    out = tempfile.mkdtemp(prefix="heaton-voxel-reports-")
    command = [JRE, "-Xmx2G", "-cp", os.pathsep.join(classpath),
               "net.minecraft.data.Main", "--reports", "--output", out]
    print(f"running the {version} data generator ...", file=sys.stderr)
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    with open(os.path.join(out, "reports", "blocks.json"), encoding="utf-8") as handle:
        report = json.load(handle)
    return report, str(version_info["id"]), int(version_info["world_version"])


# --------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------


def default_states(report: dict) -> dict[str, dict[str, str]]:
    """Every block with properties, mapped to its default state's properties
    in name order -- the order the game writes them."""
    table: dict[str, dict[str, str]] = {}
    for name, block in report.items():
        properties = block.get("properties") or {}
        if not properties:
            continue
        defaults = [state for state in block["states"] if state.get("default")]
        if len(defaults) != 1:
            raise SystemExit(f"{name}: {len(defaults)} default states in the report")
        chosen = defaults[0]["properties"]
        if set(chosen) != set(properties):
            raise SystemExit(f"{name}: default state names {sorted(chosen)}, "
                             f"block declares {sorted(properties)}")
        table[name] = {key: str(chosen[key]) for key in sorted(chosen)}
    return table


def shared_groups(table: dict[str, dict[str, str]]) -> list[tuple[str, list[str]]]:
    """Blocks grouped by identical defaults, as ``(text, names)``, both sorted.

    Stairs, fences, slabs and logs each share one default set across dozens
    of blocks, so grouping cuts the file by two thirds and makes a diff
    between two game versions read as what actually changed.
    """
    groups: dict[str, list[str]] = {}
    for name, properties in table.items():
        text = ",".join(f"{key}={value}" for key, value in properties.items())
        groups.setdefault(text, []).append(name)
    return [(text, sorted(names)) for text, names in sorted(groups.items())]


def _provenance(groups, game: str, data_version: int, block_count: int) -> str:
    with_properties = sum(len(names) for _, names in groups)
    return (f"Generated by tools/generate_block_defaults.py from Minecraft "
            f"{game} (DataVersion {data_version}): {block_count} blocks, "
            f"{with_properties} of them with properties, in {len(groups)} "
            f"distinct default sets. Regenerate rather than edit; both twins "
            f"are written by the same run.")


def write_python(groups, game: str, data_version: int, block_count: int) -> None:
    lines = [
        '"""Default block states, from the game\'s own block report. GENERATED.',
        "",
        *textwrap.wrap(_provenance(groups, game, data_version, block_count), 76),
        "",
        "Why it exists: since 26.3 a chunk palette stores a state at its block's",
        "default as the bare id (``minecraft:water`` is ``water[level=0]``) and",
        "omits nothing else, so restoring the defaults is part of *decoding*, and",
        "deciding whether a state may be stored bare is part of encoding.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        f'GAME_VERSION = "{game}"',
        f"DATA_VERSION = {data_version}",
        "",
        "#: Blocks sharing one default property set, keyed by that set with the",
        "#: properties in name order -- the order the game itself writes them.",
        "_SHARED: dict[str, tuple[str, ...]] = {",
    ]
    for text, names in groups:
        lines.append(f'    "{text}": (')
        for name in names:
            lines.append(f'        "{name}",')
        lines.append("    ),")
    lines += [
        "}",
        "",
        "#: Block id -> its default properties. One dict per group, shared;",
        "#: readers copy through :func:`default_properties` rather than mutate.",
        "BLOCK_DEFAULTS: dict[str, dict[str, str]] = {}",
        "for _text, _names in _SHARED.items():",
        '    _properties = dict(pair.split("=", 1) for pair in _text.split(","))',
        "    for _name in _names:",
        "        BLOCK_DEFAULTS[_name] = _properties",
        "del _text, _names, _properties, _name",
        "",
        "",
        "def default_properties(name: str) -> dict[str, str]:",
        '    """A fresh copy of the block\'s default properties, in name order.',
        "",
        "    Empty for a block that has none -- and for one this table has never",
        "    heard of, which reads its bare id as a state with no properties and",
        "    stores a property-less state bare: the game's own behavior for the",
        "    blocks it knows, and the harmless direction to be wrong in for a",
        "    block from a newer version than the table.",
        '    """',
        "    found = BLOCK_DEFAULTS.get(name)",
        "    return dict(found) if found else {}",
        "",
    ]
    with open(PYTHON_OUT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def write_csharp(groups, game: str, data_version: int, block_count: int) -> None:
    lines = [
        "// <auto-generated>",
        *("//     " + line for line in textwrap.wrap(
            _provenance(groups, game, data_version, block_count), 70)),
        "// </auto-generated>",
        "//",
        "// Twin of heaton_voxel/block_defaults.py. Since 26.3 a chunk palette stores",
        "// a state at its block's default as the bare id, so restoring the defaults",
        "// is part of decoding and knowing them is part of encoding.",
        "",
        "using System;",
        "using System.Collections.Generic;",
        "",
        "namespace HeatonVoxel.Blocks",
        "{",
        "    public static class BlockDefaults",
        "    {",
        f'        public const string GameVersion = "{game}";',
        f"        public const int DataVersion = {data_version};",
        "",
        "        private static readonly Dictionary<string, KeyValuePair<string, string>[]> Table = Build();",
        "",
        "        /// <summary>The block's default properties in name order, as a",
        "        /// fresh array; empty for a block that has none, or for one this",
        "        /// table has never heard of.</summary>",
        "        public static KeyValuePair<string, string>[] DefaultProperties(string name)",
        "        {",
        "            return Table.TryGetValue(name, out var pairs)",
        "                ? (KeyValuePair<string, string>[])pairs.Clone()",
        "                : Array.Empty<KeyValuePair<string, string>>();",
        "        }",
        "",
        "        private static void Share(",
        "            Dictionary<string, KeyValuePair<string, string>[]> table,",
        "            string text, params string[] names)",
        "        {",
        "            string[] parts = text.Split(',');",
        "            var pairs = new KeyValuePair<string, string>[parts.Length];",
        "            for (int i = 0; i < parts.Length; i++)",
        "            {",
        "                int eq = parts[i].IndexOf('=');",
        "                pairs[i] = new KeyValuePair<string, string>(",
        "                    parts[i].Substring(0, eq), parts[i].Substring(eq + 1));",
        "            }",
        "            foreach (string name in names)",
        "                table[name] = pairs;",
        "        }",
        "",
        "        private static Dictionary<string, KeyValuePair<string, string>[]> Build()",
        "        {",
        "            var table = new Dictionary<string, KeyValuePair<string, string>[]>(",
        "                StringComparer.Ordinal);",
    ]
    for text, names in groups:
        lines.append(f'            Share(table, "{text}",')
        for index, name in enumerate(names):
            end = ");" if index == len(names) - 1 else ","
            lines.append(f'                "{name}"{end}')
    lines += [
        "            return table;",
        "        }",
        "    }",
        "}",
        "",
    ]
    with open(CSHARP_OUT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--version", help="an installed game version, e.g. 26.3")
    source.add_argument("--report", help="a blocks.json from the game's data generator")
    parser.add_argument("--game", help="game id to record with --report (default: from the file name's parent)")
    parser.add_argument("--data-version", type=int, help="DataVersion to record with --report")
    args = parser.parse_args()

    if args.version:
        report, game, data_version = run_game_generator(args.version)
    else:
        with open(args.report, encoding="utf-8") as handle:
            report = json.load(handle)
        if args.game is None or args.data_version is None:
            parser.error("--report needs --game and --data-version to label the table")
        game, data_version = args.game, args.data_version

    table = default_states(report)
    groups = shared_groups(table)
    write_python(groups, game, data_version, len(report))
    write_csharp(groups, game, data_version, len(report))
    print(f"{game} (DataVersion {data_version}): {len(report)} blocks, "
          f"{len(table)} with properties, {len(groups)} default sets")
    print(f"  wrote {os.path.relpath(PYTHON_OUT, REPO_ROOT)}")
    print(f"  wrote {os.path.relpath(CSHARP_OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
