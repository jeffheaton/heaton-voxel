"""Ask the game whether it reads the structure files this library writes.

    python tools/probe_structures.py --version 26.3

``heaton_voxel.schem.structure`` writes the vanilla structure ``.nbt`` a
structure block saves. The game is the judge of whether it wrote one, so
this builds a few schematics here -- every kind of cell the format holds:
plain blocks in both state codecs, states at and away from their defaults,
air, cells left out, block entities, entities, and a file of several
palettes made by hand -- writes each with the library, and hands the bytes
to the game's own ``StructureTemplate``: upgraded by its data fixer from the
file's DataVersion as ``StructureTemplateManager`` does, loaded, and saved
again. ``fixtures/structures.json`` records what went in and what the game
wrote back, and ``tests/test_structures.py`` reads the game's copy and
holds it to the schematic that was written: the same cells, block entities
and entities.

Nothing in the fixture comes from the jar: every structure in it is made
here. The game runs as a few lines of Java against the installed jar and
its libraries (``generate_block_defaults.game_classpath``), as
``tools/probe_block_shapes.py`` does. Regenerate after a game update, and
commit what it writes.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

from generate_block_defaults import JRE, game_classpath  # noqa: E402

from heaton_voxel import nbt  # noqa: E402
from heaton_voxel.nbt import (  # noqa: E402
    TAG_COMPOUND,
    TAG_DOUBLE,
    TAG_INT,
    TagCompound,
    TagDouble,
    TagFloat,
    TagInt,
    TagIntArray,
    TagList,
    TagString,
)
from heaton_voxel.schem import Schematic  # noqa: E402
from heaton_voxel.schem.structure import structure_root  # noqa: E402

OUT = os.path.join(REPO, "fixtures", "structures.json")

JAVA = r"""
import java.io.*;
import java.nio.file.*;
import java.util.*;
import com.google.gson.*;
import net.minecraft.SharedConstants;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.nbt.*;
import net.minecraft.server.Bootstrap;
import net.minecraft.util.datafix.DataFixTypes;
import net.minecraft.util.datafix.DataFixers;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplate;

public class StructureProbe {
    public static void main(String[] args) throws Exception {
        SharedConstants.tryDetectVersion();
        Bootstrap.bootStrap();
        int current = SharedConstants.getCurrentVersion().dataVersion().version();
        JsonArray cases = JsonParser.parseString(Files.readString(Path.of(args[0]))).getAsJsonArray();
        StringBuilder out = new StringBuilder();
        out.append("{\"game\":\"").append(SharedConstants.getCurrentVersion().name())
           .append("\",\"data_version\":").append(current).append(",\"saved\":[");
        boolean first = true;
        for (JsonElement e : cases) {
            byte[] raw = Base64.getDecoder().decode(e.getAsString());
            CompoundTag tag = NbtIo.read(new DataInputStream(new ByteArrayInputStream(raw)));
            int version = tag.getIntOr("DataVersion", 500);
            CompoundTag fixed = DataFixTypes.STRUCTURE.update(DataFixers.getDataFixer(), tag, version, current);
            StructureTemplate template = new StructureTemplate();
            template.load(BuiltInRegistries.BLOCK, fixed);
            CompoundTag saved = template.save(new CompoundTag());
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            NbtIo.write(saved, new DataOutputStream(b));
            if (!first) out.append(",");
            first = false;
            out.append("\"").append(Base64.getEncoder().encodeToString(b.toByteArray())).append("\"");
        }
        out.append("]}");
        Files.writeString(Path.of(args[1]), out.toString());
    }
}
"""


def _entity(entity_id: str, x: float, y: float, z: float, **extra) -> TagCompound:
    tag = TagCompound({
        "id": TagString(entity_id),
        "Pos": TagList([TagDouble(x), TagDouble(y), TagDouble(z)], TAG_DOUBLE),
        "Rotation": TagList([TagFloat(90.0), TagFloat(0.0)], nbt.TAG_FLOAT),
        "UUID": TagIntArray([1, 2, 3, 4]),
    })
    tag.update(extra)
    return tag


def schematics() -> list[tuple[str, Schematic]]:
    """The structures put to the game, each a name and a schematic."""
    out = []

    small = Schematic(3, 2, 2, 5023)
    small.set_block(0, 0, 0, "minecraft:stone")
    small.set_block(1, 0, 0, "minecraft:oak_stairs[facing=east]")
    small.set_block(2, 0, 0, "minecraft:oak_stairs[facing=north,half=top,shape=outer_left]")
    small.set_block(0, 1, 0, "minecraft:structure_void")      # left out
    small.set_block(1, 1, 1, "minecraft:water")
    small.set_block(2, 1, 1, "minecraft:chest[facing=west,type=left]")
    small.set_block_entity(2, 1, 1, TagCompound({
        "id": TagString("minecraft:chest"),
        "Items": TagList([TagCompound({"Slot": nbt.TagByte(0), "id": TagString("minecraft:apple"),
                                       "count": TagInt(3)})], TAG_COMPOUND),
    }))
    out.append(("blocks, defaults and not, a void, a chest", small))

    legacy = Schematic(2, 1, 2, 4903)
    legacy.set_block(0, 0, 0, "minecraft:oak_log[axis=x]")
    legacy.set_block(1, 0, 1, "minecraft:grass_block")
    legacy.set_block(1, 0, 0, "minecraft:structure_void")
    out.append(("the codec before 26.3, upgraded by the game", legacy))

    lively = Schematic(4, 3, 4, 5023)
    for x in range(4):
        for z in range(4):
            lively.set_block(x, 0, z, "minecraft:smooth_stone")
    lively.set_block(1, 1, 1, "minecraft:oak_sign[rotation=4]")
    lively.set_block_entity(1, 1, 1, TagCompound({
        "id": TagString("minecraft:sign"), "is_waxed": nbt.TagByte(0),
        "front_text": TagCompound({"messages": TagList([TagString('"hi"')] * 4, nbt.TAG_STRING)}),
    }))
    lively.entities.append(_entity("minecraft:armor_stand", 2.5, 1.0, 2.5))
    lively.entities.append(_entity("minecraft:item_frame", 0.96875, 1.5, 3.5,
                                   Facing=nbt.TagByte(4),
                                   block_pos=TagIntArray([0, 1, 3])))
    out.append(("a sign, an armor stand, a frame on a wall", lively))

    tall = Schematic(1, 40, 1, 5023)
    for y in range(40):
        tall.set_block(0, y, 0, "minecraft:ladder[facing=south]" if y % 2 else "minecraft:air")
    out.append(("a column, air every other cell", tall))
    return out


def palettes_by_hand() -> TagCompound:
    """A structure of two palettes, which nothing here writes."""
    def state(name):
        return TagCompound({"id": TagString(name)})
    pos = lambda x: TagList([TagInt(x), TagInt(0), TagInt(0)], TAG_INT)  # noqa: E731
    return TagCompound({
        "size": TagList([TagInt(2), TagInt(1), TagInt(1)], TAG_INT),
        "entities": TagList(element_type=nbt.TAG_END),
        "blocks": TagList([TagCompound({"pos": pos(0), "state": TagInt(0)}),
                           TagCompound({"pos": pos(1), "state": TagInt(1)})], TAG_COMPOUND),
        "palettes": TagList([
            TagList([state("minecraft:stone"), state("minecraft:dirt")], TAG_COMPOUND),
            TagList([state("minecraft:cobblestone"), state("minecraft:gravel")], TAG_COMPOUND),
        ], nbt.TAG_LIST),
        "DataVersion": TagInt(5023),
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="an installed game version, e.g. 26.3")
    args = parser.parse_args()
    cases = []
    for name, schematic in schematics():
        cases.append((name, nbt.encode(structure_root(schematic))))
    cases.append(("two palettes, made by hand", nbt.encode(palettes_by_hand())))

    classpath, _ = game_classpath(args.version)
    work = tempfile.mkdtemp(prefix="heaton-voxel-structures-")
    try:
        source = os.path.join(work, "StructureProbe.java")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write(JAVA)
        given = os.path.join(work, "cases.json")
        with open(given, "w", encoding="ascii") as handle:
            json.dump([base64.b64encode(raw).decode("ascii") for _, raw in cases], handle)
        answered = os.path.join(work, "saved.json")
        run = subprocess.run(
            [JRE, "-Xmx1G", "-cp", os.pathsep.join(classpath), source, given, answered],
            cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if run.returncode != 0:
            raise SystemExit(f"the probe failed -- it needs a JDK, which the "
                             f"launcher's runtime is:\n{run.stdout[-2000:]}")
        with open(answered, encoding="ascii") as handle:
            answers = json.load(handle)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    rows = [{"name": name, "written": base64.b64encode(raw).decode("ascii"), "game": saved}
            for (name, raw), saved in zip(cases, answers["saved"])]
    with open(OUT, "w", encoding="ascii") as handle:
        json.dump({"game": answers["game"], "data_version": answers["data_version"],
                   "cases": rows}, handle, indent=1)
        handle.write("\n")
    print(f"wrote {os.path.relpath(OUT, REPO)}: {len(rows)} structures through "
          f"{answers['game']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
