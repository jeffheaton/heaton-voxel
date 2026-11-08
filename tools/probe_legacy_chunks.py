"""Ask the game what a chunk Minecraft 1.13 to 1.17 saved is, by running its
own upgrade.

    python tools/probe_legacy_chunks.py --version 26.3

The library reads chunks Minecraft 1.17 and 1.17.1 saved (DataVersion 2724
to 2730), and writes none. Most of what it reads there is the format, but
two answers are the game's, and this asks the game for both:

* **The biome names.** A 1.17 chunk numbers its biomes. The game names a
  number when it upgrades the chunk -- ``ChunkHeightAndBiomeFix``'s table,
  plains for a number it does not have, and then the 1.18 renames
  (``CavesAndCliffsRenames``: ``mountains`` is ``windswept_hills`` today)
  -- so each of the 256 numbers a chunk can hold is upgraded on its own and
  the name the game ends with written into both twins:
  ``heaton_voxel/legacy_biomes.py`` and
  ``csharp/HeatonVoxel/Blocks/LegacyBiomes.generated.cs``.
* **What a real chunk becomes.** ``fixtures/legacy/upgraded.nbt`` is the
  game's upgrade of every chunk in ``fixtures/legacy/r.5.-1.mca``, which
  ``tests/test_legacy.py`` holds the reader to cell by cell: the blocks of
  y 0..255, the biomes, and the surface the heightmaps give.
* **What an older chunk becomes.** ``fixtures/legacy/older.nbt`` is the
  same for the chunks before 1.17: OldWorld's own 1.15.2 overworld chunks
  (``1.15.2/r.0.-5.mca``, whose sections run on across longs) and 1.16.5
  End chunks (``1.16.5-end/r.-1.-1.mca``, upgraded as the End), and chunks
  made here from a real 1.15.2 one and stamped as 1.13 and 1.14 saved them
  -- a biome a column, the stage names of their day, and sections of six
  and seven bits a cell, which straddle longs, holding blocks that have
  been renamed or reshaped since -- each with every distinct state in its
  palette upgraded by the game on its own (``References.BLOCK_STATE``), so
  a cell the reader reads under its old name can be held to the cell the
  game made of it. The stage each would have in 1.17.1 is recorded beside
  it (``status_1_17``, the chunk upgraded to 2730 and no further), since
  the 1.18 upgrade resets every old overworld chunk to ``empty`` and folds
  every stage from ``spawn`` up into its ``below_zero_retrogen``.

Both come from the launcher's JDK running a few lines of Java against the
installed jar and its libraries (``generate_block_defaults.game_classpath``),
the way ``tools/probe_block_shapes.py`` does. The upgrade is the call
``SimpleRegionStorage.upgradeChunkTag`` makes when the game loads an old
chunk: ``DataFixTypes.CHUNK.update`` from the chunk's DataVersion to the
game's, with the context ``ChunkMap.getChunkDataFixContextTag`` gives it --
the overworld of a noise world, the world the fixture came from. Without
the fixture the second half is skipped, with a note; the first never needs
it. Regenerate after a game update, and commit what it writes.
"""

from __future__ import annotations

import argparse
import gzip
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

from generate_block_defaults import JRE, game_classpath  # noqa: E402

from heaton_voxel import nbt  # noqa: E402

PYTHON_OUT = os.path.join(REPO, "heaton_voxel", "legacy_biomes.py")
CSHARP_OUT = os.path.join(REPO, "csharp", "HeatonVoxel", "Blocks",
                          "LegacyBiomes.generated.cs")
FIXTURE = os.path.join(REPO, "fixtures", "legacy", "r.5.-1.mca")
UPGRADED = os.path.join(REPO, "fixtures", "legacy", "upgraded.nbt")
OLDER = os.path.join(REPO, "fixtures", "legacy", "older.nbt")
#: The real regions before 1.17, and the dimension each is upgraded as.
OLDER_REGIONS = (("1.15.2/r.0.-5.mca", "minecraft:overworld"),
                 ("1.16.5-end/r.-1.-1.mca", "minecraft:the_end"))

# The newest 1.17 DataVersion (1.17.1), which the biome chunks are stamped
# with: the table belongs to every 1.17 version alike, since no fix runs
# between 2724 and 2730.
LEGACY_DATA_VERSION = 2730

JAVA = r"""
import java.nio.file.*;
import java.util.*;
import com.mojang.datafixers.DataFixer;
import com.mojang.serialization.Dynamic;
import net.minecraft.SharedConstants;
import net.minecraft.nbt.*;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.util.datafix.fixes.References;
import net.minecraft.server.Bootstrap;
import net.minecraft.server.level.ChunkMap;
import net.minecraft.util.datafix.DataFixTypes;
import net.minecraft.util.datafix.DataFixers;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.chunk.storage.SimpleRegionStorage;

public class LegacyProbe {
    static DataFixer fixer;
    static int current;
    static CompoundTag context;

    // What SimpleRegionStorage.upgradeChunkTag does, context and all.
    static CompoundTag upgrade(CompoundTag tag) {
        return upgrade(tag, context);
    }

    static CompoundTag upgrade(CompoundTag tag, CompoundTag context) {
        int version = tag.getIntOr("DataVersion", -1);
        SimpleRegionStorage.injectDatafixingContext(tag, context);
        CompoundTag out = DataFixTypes.CHUNK.update(fixer, tag, version, current);
        out.remove("__context");
        out.putInt("DataVersion", current);
        return out;
    }

    public static void main(String[] args) throws Exception {
        SharedConstants.tryDetectVersion();
        Bootstrap.bootStrap();
        fixer = DataFixers.getDataFixer();
        current = SharedConstants.getCurrentVersion().dataVersion().version();
        context = ChunkMap.getChunkDataFixContextTag(
            Level.OVERWORLD, Optional.of(Identifier.withDefaultNamespace("noise")));

        CompoundTag result = new CompoundTag();
        result.putInt("data_version", current);

        // 1. Every biome number, each in a chunk of its own.
        ListTag biomes = new ListTag();
        for (int id = 0; id < 256; id++) {
            CompoundTag level = new CompoundTag();
            level.putInt("xPos", 0);
            level.putInt("zPos", 0);
            level.putString("Status", "full");
            level.put("Sections", new ListTag());
            int[] numbers = new int[1024];
            Arrays.fill(numbers, id);
            level.putIntArray("Biomes", numbers);
            CompoundTag chunk = new CompoundTag();
            chunk.putInt("DataVersion", %(legacy)d);
            chunk.put("Level", level);
            CompoundTag out = upgrade(chunk);
            String name = null;
            ListTag sections = out.getListOrEmpty("sections");
            for (int i = 0; i < sections.size(); i++) {
                CompoundTag section = sections.getCompoundOrEmpty(i);
                int y = section.getIntOr("Y", -99);
                if (y < 0 || y > 15) continue;
                ListTag palette = section.getCompoundOrEmpty("biomes").getListOrEmpty("palette");
                if (palette.size() != 1)
                    throw new IllegalStateException("biome " + id + ": section " + y
                        + " has " + palette.size() + " biomes, not one");
                String here = palette.getStringOr(0, null);
                if (name != null && !name.equals(here))
                    throw new IllegalStateException("biome " + id + " is " + name
                        + " in one section and " + here + " in another");
                name = here;
            }
            if (name == null)
                throw new IllegalStateException("biome " + id + ": no section 0..15 came back");
            biomes.add(StringTag.valueOf(name));
        }
        result.put("biomes", biomes);

        // 2. The fixture's chunks, as the game loads them.
        ListTag chunks = new ListTag();
        if (args.length > 1) {
            List<Path> inputs = new ArrayList<>();
            try (DirectoryStream<Path> listing = Files.newDirectoryStream(Paths.get(args[1]))) {
                for (Path p : listing) inputs.add(p);
            }
            Collections.sort(inputs);
            for (Path p : inputs)
                chunks.add(upgrade(NbtIo.readCompressed(p, NbtAccounter.unlimitedHeap())));
        }
        result.put("chunks", chunks);

        // 3. Older chunks, each upgraded as its own dimension's, and every
        // distinct state in its palette upgraded on its own.
        ListTag older = new ListTag();
        if (args.length > 2) {
            List<Path> inputs = new ArrayList<>();
            try (DirectoryStream<Path> listing = Files.newDirectoryStream(Paths.get(args[2]))) {
                for (Path p : listing) inputs.add(p);
            }
            Collections.sort(inputs);
            for (Path p : inputs) {
                CompoundTag given = NbtIo.readCompressed(p, NbtAccounter.unlimitedHeap());
                ResourceKey<Level> dimension = switch (given.getStringOr("dimension", "")) {
                    case "minecraft:overworld" -> Level.OVERWORLD;
                    case "minecraft:the_end" -> Level.END;
                    case "minecraft:the_nether" -> Level.NETHER;
                    default -> throw new IllegalStateException("no dimension in " + p);
                };
                CompoundTag chunk = given.getCompoundOrEmpty("chunk");
                int version = chunk.getIntOr("DataVersion", -1);
                ListTag states = new ListTag();
                Set<CompoundTag> seen = new LinkedHashSet<>();
                ListTag sections = chunk.getCompoundOrEmpty("Level").getListOrEmpty("Sections");
                for (int i = 0; i < sections.size(); i++) {
                    ListTag palette = sections.getCompoundOrEmpty(i).getListOrEmpty("Palette");
                    for (int j = 0; j < palette.size(); j++) seen.add(palette.getCompoundOrEmpty(j));
                }
                for (CompoundTag state : seen) {
                    CompoundTag pair = new CompoundTag();
                    pair.put("from", state.copy());
                    pair.put("to", (Tag) fixer.update(References.BLOCK_STATE,
                        new Dynamic<>(NbtOps.INSTANCE, state.copy()), version, current).getValue());
                    states.add(pair);
                }
                CompoundTag here = ChunkMap.getChunkDataFixContextTag(
                    dimension, Optional.of(Identifier.withDefaultNamespace("noise")));
                CompoundTag answer = new CompoundTag();
                answer.put("upgraded", upgrade(chunk.copy(), here));
                answer.put("states", states);
                // The stage 1.17.1 names it, before the 1.18 upgrade folds
                // every overworld stage from spawn up into one retrogen.
                CompoundTag at117 = DataFixTypes.CHUNK.update(fixer, chunk.copy(), version, 2730);
                answer.putString("status_1_17", at117.getCompoundOrEmpty("Level").getStringOr("Status", ""));
                older.add(answer);
            }
        }
        result.put("older", older);
        NbtIo.writeCompressed(result, Paths.get(args[0]));
    }
}
"""


def run(classpath: list[str], chunks: list, older: list) -> nbt.TagCompound:
    """The probe's answer: ``{data_version, biomes, chunks, older}``, the
    last one ``{upgraded, states}`` for each ``(dimension, chunk)`` of
    ``older``."""
    with tempfile.TemporaryDirectory(prefix="legacy-probe-") as work:
        source = os.path.join(work, "LegacyProbe.java")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write(JAVA % {"legacy": LEGACY_DATA_VERSION})
        inputs = os.path.join(work, "chunks")
        os.makedirs(inputs)
        for index, root in enumerate(chunks):
            # Named so the probe's sorted listing is the fixture's order.
            path = os.path.join(inputs, f"{index:04d}.nbt")
            with open(path, "wb") as handle:
                handle.write(gzip.compress(nbt.encode(root), mtime=0))
        older_inputs = os.path.join(work, "older")
        os.makedirs(older_inputs)
        for index, (dimension, root) in enumerate(older):
            path = os.path.join(older_inputs, f"{index:04d}.nbt")
            given = nbt.TagCompound({"dimension": nbt.TagString(dimension), "chunk": root})
            with open(path, "wb") as handle:
                handle.write(gzip.compress(nbt.encode(given), mtime=0))
        output = os.path.join(work, "result.nbt")
        command = [JRE, "-Xmx2G", "-cp", os.pathsep.join(classpath), source, output,
                   inputs, older_inputs]
        done = subprocess.run(command, capture_output=True, text=True, cwd=work)
        if done.returncode != 0:
            raise SystemExit(f"the probe failed:\n{done.stdout[-2000:]}\n{done.stderr[-4000:]}")
        _, root = nbt.decode(gzip.decompress(open(output, "rb").read()))
        return root


def fixture_chunks() -> list:
    """Every chunk of the legacy fixture, in region order, decoded."""
    if not os.path.exists(FIXTURE):
        return []
    region = nbt.NBT.load(FIXTURE)
    return [region.chunks[key].data for key in sorted(region.chunks)]


_COLORS = ("white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray",
           "light_gray", "cyan", "purple", "blue", "brown", "green", "red", "black")

# Blocks 1.13 had under names or shapes it has since changed -- the sign
# and the stone slab renamed by 1.14, the path by 1.17, the grass by 1.20.3,
# a wall's booleans made heights by 1.16, redstone's sides rewired -- and
# plain ones beside them.
_RESHAPED = (
    ("minecraft:sign", {"rotation": "4", "waterlogged": "false"}),
    ("minecraft:wall_sign", {"facing": "north", "waterlogged": "false"}),
    ("minecraft:stone_slab", {"type": "bottom", "waterlogged": "false"}),
    ("minecraft:grass_path", {}),
    ("minecraft:grass", {}),
    ("minecraft:cobblestone_wall", {"east": "true", "north": "false", "south": "true",
                                    "up": "true", "waterlogged": "false", "west": "false"}),
    ("minecraft:redstone_wire", {"east": "none", "north": "side", "power": "0",
                                 "south": "none", "west": "none"}),
    ("minecraft:oak_log", {"axis": "x"}),
    ("minecraft:chest", {"facing": "east", "type": "single", "waterlogged": "false"}),
    ("minecraft:oak_stairs", {"facing": "north", "half": "bottom", "shape": "straight",
                              "waterlogged": "false"}),
    ("minecraft:tall_grass", {"half": "lower"}),
    ("minecraft:rose_bush", {"half": "upper"}),
    ("minecraft:air", {}), ("minecraft:stone", {}), ("minecraft:dirt", {}),
    ("minecraft:sand", {}), ("minecraft:gravel", {}), ("minecraft:oak_planks", {}),
    ("minecraft:bricks", {}), ("minecraft:glass", {}),
)

# Biome numbers 1.13 had, one a column, so no two columns a cell's center
# might be read from by mistake agree.
_OLD_BIOMES = (1, 2, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 16, 21, 24, 27, 29, 30, 32, 35,
               36, 37, 38, 44, 45, 46, 48, 49, 50, 129, 130, 131, 132, 134, 140, 149, 151,
               155, 156, 157, 158, 160, 161, 162, 163, 164, 165, 166, 167)

#: The chunks made here, each ``(name, DataVersion, Status, base)``: the
#: base a local chunk of the 1.15.2 region, the first holding an entity.
SYNTHETIC = (
    ("1.14.4, a biome a column", 1976, "full", (0, 7)),
    ("1.14 snapshot between the stage renames, finished", 1908, "fullchunk", (0, 7)),
    ("1.13.2, finished as postprocessed", 1631, "postprocessed", (0, 7)),
    ("1.13, finished as fullchunk", 1519, "fullchunk", (0, 7)),
    ("1.13.2, set aside lighted", 1631, "lighted", (1, 7)),
)


def _palette(states) -> nbt.TagList:
    out = nbt.TagList(element_type=nbt.TAG_COMPOUND)
    for name, properties in states:
        entry = nbt.TagCompound({"Name": nbt.TagString(name)})
        if properties:
            entry["Properties"] = nbt.TagCompound(
                {k: nbt.TagString(v) for k, v in sorted(properties.items())})
        out.append(entry)
    return out


def _run_on_section(y: int, states) -> nbt.TagCompound:
    """A section as 1.13 to 1.15 stored one: as many bits a cell as the
    palette needs, four at least, packed end to end across longs."""
    from heaton_voxel.schem.litematic import pack_tight
    bits = max(4, (len(states) - 1).bit_length())
    cells = [(i * 37 + (i >> 8) * 11) % len(states) for i in range(4096)]
    return nbt.TagCompound({
        "Y": nbt.TagByte(y),
        "Palette": _palette(states),
        "BlockStates": pack_tight(cells, bits),
    })


def synthetic_chunks() -> list:
    """The chunks :data:`SYNTHETIC` names, each a deep copy of a real 1.15.2
    chunk restamped: its DataVersion and stage, a biome a column, and two
    sections of six and seven bits a cell over its own at y 10 and 11."""
    region = nbt.NBT.load(os.path.join(REPO, "fixtures", "legacy", OLDER_REGIONS[0][0]))
    wool = [(f"minecraft:{color}_wool", {}) for color in _COLORS]
    many = list(_RESHAPED)
    for kind in ("wool", "concrete", "terracotta", "stained_glass", "concrete_powder"):
        many += [(f"minecraft:{color}_{kind}", {}) for color in _COLORS]
    out = []
    for name, version, status, base in SYNTHETIC:
        _, data = nbt.decode(nbt.encode(region.chunks[base].data))
        data["DataVersion"] = nbt.TagInt(version)
        level = data["Level"]
        level["Status"] = nbt.TagString(status)
        level["Biomes"] = nbt.TagIntArray(
            [_OLD_BIOMES[(x * 5 + z * 11) % len(_OLD_BIOMES)]
             for z in range(16) for x in range(16)])
        sections = [s for s in level["Sections"] if int(s["Y"]) not in (10, 11)]
        sections.append(_run_on_section(10, list(_RESHAPED) + wool))     # 36: six bits
        sections.append(_run_on_section(11, many))                     # 100: seven
        level["Sections"] = nbt.TagList(sections, nbt.TAG_COMPOUND)
        out.append((name, data))
    return out


def older_cases() -> list:
    """``(name, dimension, (source, key), chunk)`` for every chunk the older
    half upgrades: the real regions' in region order, then the synthetic
    ones, whose source is None."""
    cases = []
    for relative, dimension in OLDER_REGIONS:
        region = nbt.NBT.load(os.path.join(REPO, "fixtures", "legacy", relative))
        for key in sorted(region.chunks):
            cases.append((f"{relative} {key}", dimension, (relative, key),
                          region.chunks[key].data))
    for name, data in synthetic_chunks():
        cases.append((name, "minecraft:overworld", None, data))
    return cases


def write_python(game: str, data_version: int, names: list[str]) -> None:
    rows = []
    for start in range(0, 256, 4):
        rows.append("    " + " ".join(f"{name!r}," for name in names[start:start + 4]))
    text = f'''"""The name the game gives each biome number a Minecraft 1.17 chunk
stores. GENERATED.

Generated by tools/probe_legacy_chunks.py from Minecraft {game} (DataVersion
{data_version}). Regenerate rather than edit; both twins are written by the
same run.

A 1.17 chunk keeps its biomes as one array of 1,024 numbers, a 4x4x4 cell
each, and the game names them only when it upgrades the chunk: its own
table (``ChunkHeightAndBiomeFix``), plains for a number that table does
not have, and then every rename since -- the 1.18 ones turned ``mountains``
into ``windswept_hills``. Each number was upgraded by the game on its own,
and this is the name it came back with, so a 1.17 biome reads as one the
game has today. Indexed by the number's low byte, which is all the game
reads of it.
"""

from __future__ import annotations

GAME_VERSION = {game!r}
DATA_VERSION = {data_version}

LEGACY_BIOMES: tuple[str, ...] = (
''' + "\n".join(rows) + "\n)\n"
    with open(PYTHON_OUT, "w", encoding="utf-8") as handle:
        handle.write(text)


def write_csharp(game: str, data_version: int, names: list[str]) -> None:
    rows = []
    for start in range(0, 256, 4):
        rows.append("            " + " ".join(f'"{name}",' for name in names[start:start + 4]))
    text = f"""// <auto-generated>
//     Generated by tools/probe_legacy_chunks.py from Minecraft {game}
//     (DataVersion {data_version}). Regenerate rather than edit; both twins
//     are written by the same run.
// </auto-generated>
//
// Twin of heaton_voxel/legacy_biomes.py: the name the game gives each
// biome number a Minecraft 1.17 chunk stores, when it upgrades the chunk.
// Indexed by the number's low byte, which is all the game reads of it.

#nullable enable

namespace HeatonVoxel.Blocks
{{
    public static class LegacyBiomes
    {{
        public const string GameVersion = "{game}";
        public const int DataVersion = {data_version};

        public static readonly string[] ByNumber =
        {{
""" + "\n".join(rows) + """
        };
    }
}
"""
    with open(CSHARP_OUT, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", default="26.3", help="installed game version")
    args = parser.parse_args()

    classpath, version_info = game_classpath(args.version)
    chunks = fixture_chunks()
    cases = older_cases()
    result = run(classpath, chunks, [(dimension, data) for _, dimension, _, data in cases])
    data_version = int(result["data_version"])
    names = [str(name) for name in result["biomes"]]
    if len(names) != 256:
        raise SystemExit(f"the probe named {len(names)} biome numbers, not 256")
    game = version_info.get("name", args.version)
    write_python(game, data_version, names)
    write_csharp(game, data_version, names)
    print(f"wrote {os.path.relpath(PYTHON_OUT, REPO)} and "
          f"{os.path.relpath(CSHARP_OUT, REPO)}: "
          f"{len(set(names))} distinct names over 256 numbers")

    if not chunks:
        print(f"no {os.path.relpath(FIXTURE, REPO)}: the upgrade fixture was not written")
        return 0
    upgraded = nbt.TagCompound({
        "data_version": nbt.TagInt(data_version),
        "game": nbt.TagString(game),
        "chunks": result["chunks"],
    })
    with open(UPGRADED, "wb") as handle:
        handle.write(gzip.compress(nbt.encode(upgraded), mtime=0))
    print(f"wrote {os.path.relpath(UPGRADED, REPO)}: the game's upgrade of "
          f"{len(result['chunks'])} chunks")

    rows = nbt.TagList(element_type=nbt.TAG_COMPOUND)
    for (name, dimension, source, data), answer in zip(cases, result["older"]):
        row = nbt.TagCompound({"name": nbt.TagString(name),
                               "dimension": nbt.TagString(dimension)})
        if source is None:
            row["chunk"] = data           # made here, so kept beside its answer
        else:
            row["source"] = nbt.TagString(source[0])
            row["key"] = nbt.TagIntArray(list(source[1]))
        row["upgraded"] = answer["upgraded"]
        row["states"] = answer["states"]
        row["status_1_17"] = answer["status_1_17"]
        rows.append(row)
    older = nbt.TagCompound({
        "data_version": nbt.TagInt(data_version),
        "game": nbt.TagString(game),
        "cases": rows,
    })
    with open(OLDER, "wb") as handle:
        handle.write(gzip.compress(nbt.encode(older), mtime=0))
    print(f"wrote {os.path.relpath(OLDER, REPO)}: the game's upgrade of "
          f"{len(rows)} chunks from before 1.17")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
