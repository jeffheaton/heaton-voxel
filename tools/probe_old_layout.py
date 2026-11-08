"""Ask the game where an old-layout world's files and settings go.

    python tools/probe_old_layout.py --version 26.3

Until DataVersion 4772 a world kept its overworld in ``region/`` beside
``level.dat``, the nether under ``DIM-1/`` and the End under ``DIM1/``, its
players in ``playerdata/`` -- and the single player in ``level.dat``
itself -- and its weather, clock, border, spawn and difficulty as fields of
``level.dat``. The game moves all of it the first time a current version
opens the world (``FileFixerUpper``: ``DimensionStorageFileFix``,
``PlayerStorageFileFix``, ``LevelDatToSavedDataFileFix``), after data-fixing
``level.dat`` through every shape it took on the way: the border a compound
from 4544, the spawn from 4548, the game rules from 4658, the clock from
4766, the difficulty from 4771.

The library opens such a world where it stands, read-only, and answers
from the old fields what the game would answer from the new ones. This
asks the game for those answers. It builds old-layout worlds here -- a
``level.dat`` as 1.13.2, 1.16.5 and 1.21.4 wrote one, and the 1.21.4 one
carried by the game's own level fixer to the shapes of 4556 and 4671, with
players in ``level.dat``, in ``playerdata/`` or both, and real region files
from ``fixtures/`` under ``region/``, ``DIM-1/`` and ``DIM1/`` -- hands each
to the game's ``FileFixerUpper.fix``, the call it makes when it opens one,
and records every file before and after in ``fixtures/old_layout.json``.
``tests/test_old_layout.py`` opens both sides and holds the old one's
answers to the game's.

The game runs as a few lines of Java against the installed jar and its
libraries (``generate_block_defaults.game_classpath``), as
``tools/probe_block_shapes.py`` does. Every ``level.dat`` in the fixture
is made here; the region files are the vendored fixtures, moved, never
changed, which the probe checks. Regenerate after a game update, and
commit what it writes.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
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
    TAG_FLOAT,
    TAG_STRING,
    TagByte,
    TagCompound,
    TagDouble,
    TagFloat,
    TagInt,
    TagIntArray,
    TagList,
    TagLong,
    TagString,
)

OUT = os.path.join(REPO, "fixtures", "old_layout.json")
FIXTURES = os.path.join(REPO, "fixtures")

JAVA = r"""
import java.nio.file.*;
import com.mojang.serialization.Dynamic;
import net.minecraft.SharedConstants;
import net.minecraft.nbt.*;
import net.minecraft.server.Bootstrap;
import net.minecraft.util.datafix.DataFixTypes;
import net.minecraft.util.datafix.DataFixers;
import net.minecraft.util.worldupdate.UpgradeProgress;
import net.minecraft.world.level.storage.LevelStorageSource;

public class OldLayoutProbe {
    public static void main(String[] args) throws Exception {
        SharedConstants.tryDetectVersion();
        Bootstrap.bootStrap();
        if (args[0].equals("level")) {
            // level <in> <from> <to> <out>: a level.dat data-fixed between versions
            CompoundTag root = NbtIo.readCompressed(Paths.get(args[1]), NbtAccounter.unlimitedHeap());
            int from = Integer.parseInt(args[2]), to = Integer.parseInt(args[3]);
            CompoundTag data = DataFixTypes.LEVEL.update(
                DataFixers.getDataFixer(), root.getCompoundOrEmpty("Data"), from, to);
            data.putInt("DataVersion", to);
            CompoundTag version = data.getCompoundOrEmpty("Version");
            version.putInt("Id", to);
            data.put("Version", version);
            CompoundTag out = new CompoundTag();
            out.put("Data", data);
            NbtIo.writeCompressed(out, Paths.get(args[4]));
            return;
        }
        // fix <saves> <world>...: what the game does when it opens each
        LevelStorageSource source = LevelStorageSource.createDefault(Paths.get(args[1]));
        for (int i = 2; i < args.length; i++) {
            try (LevelStorageSource.LevelStorageAccess access = source.createAccess(args[i])) {
                Dynamic<?> data = access.getUnfixedDataTag(false);
                Dynamic<?> fixed = DataFixers.getFileFixer().fix(access, data, new UpgradeProgress.Noop());
                access.saveLevelData(fixed);
            }
        }
    }
}
"""

SEED = 4242
#: The region files the worlds are built of: vendored fixtures, each placed
#: where an old-layout world keeps its dimension's regions.
REGION_1_15 = "legacy/1.15.2/r.0.-5.mca"
REGION_1_16_END = "legacy/1.16.5-end/r.-1.-1.mca"
REGION_1_17 = "legacy/r.5.-1.mca"
REGION_26_2 = "r.-1.0.mca"


def _doubles(*values) -> TagList:
    return TagList([TagDouble(float(v)) for v in values], TAG_DOUBLE)


def _floats(*values) -> TagList:
    return TagList([TagFloat(float(v)) for v in values], TAG_FLOAT)


def _uuid_words(uuid: str) -> list[int]:
    digits = uuid.replace("-", "")
    words = [int(digits[i:i + 8], 16) for i in range(0, 32, 8)]
    return [w - (1 << 32) if w >> 31 else w for w in words]


def _uuid_halves(uuid: str) -> tuple[int, int]:
    digits = uuid.replace("-", "")
    most, least = int(digits[:16], 16), int(digits[16:], 16)
    signed = lambda v: v - (1 << 64) if v >> 63 else v  # noqa: E731
    return signed(most), signed(least)


def _player(version: int, uuid: str, pos, rotation, dimension, *, mode: int) -> TagCompound:
    """A player compound as ``version`` wrote one: the dimension a number
    and the uuid two longs before 1.16 (2514, 2537), a string and four ints
    after."""
    tag = TagCompound({
        "DataVersion": TagInt(version),
        "Pos": _doubles(*pos),
        "Rotation": _floats(*rotation),
        "Motion": _doubles(0, 0, 0),
        "OnGround": TagByte(1),
        "FallDistance": TagFloat(0.0),
        "Health": TagFloat(20.0),
        "playerGameType": TagInt(mode),
        "Inventory": TagList(element_type=TAG_COMPOUND),
    })
    if version < 2514:
        most, least = _uuid_halves(uuid)
        tag["UUIDMost"], tag["UUIDLeast"] = TagLong(most), TagLong(least)
    else:
        tag["UUID"] = TagIntArray(_uuid_words(uuid))
    if version < 2537:
        tag["Dimension"] = TagInt({"minecraft:overworld": 0, "minecraft:the_nether": -1,
                                   "minecraft:the_end": 1}[dimension])
    else:
        tag["Dimension"] = TagString(dimension)
    return tag


def _world_gen_1_16() -> TagCompound:
    def dimension(kind, settings, biomes):
        return TagCompound({"type": TagString(kind), "generator": TagCompound({
            "type": TagString("minecraft:noise"), "seed": TagLong(SEED),
            "settings": TagString(settings), "biome_source": biomes})})
    return TagCompound({
        "bonus_chest": TagByte(0), "seed": TagLong(SEED), "generate_features": TagByte(1),
        "dimensions": TagCompound({
            "minecraft:overworld": dimension("minecraft:overworld", "minecraft:overworld",
                TagCompound({"type": TagString("minecraft:vanilla_layered"),
                             "seed": TagLong(SEED), "large_biomes": TagByte(0)})),
            "minecraft:the_nether": dimension("minecraft:the_nether", "minecraft:nether",
                TagCompound({"type": TagString("minecraft:multi_noise"),
                             "seed": TagLong(SEED), "preset": TagString("minecraft:nether")})),
            "minecraft:the_end": dimension("minecraft:the_end", "minecraft:end",
                TagCompound({"type": TagString("minecraft:the_end"), "seed": TagLong(SEED)})),
        }),
    })


def _world_gen_1_21() -> TagCompound:
    def dimension(kind, settings, biomes):
        return TagCompound({"type": TagString(kind), "generator": TagCompound({
            "type": TagString("minecraft:noise"), "settings": TagString(settings),
            "biome_source": biomes})})
    return TagCompound({
        "bonus_chest": TagByte(0), "seed": TagLong(SEED), "generate_features": TagByte(1),
        "dimensions": TagCompound({
            "minecraft:overworld": dimension("minecraft:overworld", "minecraft:overworld",
                TagCompound({"type": TagString("minecraft:multi_noise"),
                             "preset": TagString("minecraft:overworld")})),
            "minecraft:the_nether": dimension("minecraft:the_nether", "minecraft:nether",
                TagCompound({"type": TagString("minecraft:multi_noise"),
                             "preset": TagString("minecraft:nether")})),
            "minecraft:the_end": dimension("minecraft:the_end", "minecraft:end",
                TagCompound({"type": TagString("minecraft:the_end")})),
        }),
    })


def _classic_level(version: int, name: str, release: str, **fields) -> TagCompound:
    """``level.dat``'s ``Data`` in the shape every version before 4544
    wrote: weather, clock, spawn, border and difficulty as fields of it."""
    data = TagCompound({
        "DataVersion": TagInt(version),
        "version": TagInt(19133),
        "Version": TagCompound({"Id": TagInt(version), "Name": TagString(release),
                                "Snapshot": TagByte(0)}),
        "LevelName": TagString(name),
        "LastPlayed": TagLong(1_600_000_000_000),
        "initialized": TagByte(1),
        "GameType": TagInt(fields["game_type"]),
        "allowCommands": TagByte(fields["commands"]),
        "hardcore": TagByte(fields["hardcore"]),
        "Difficulty": TagByte(fields["difficulty"]),
        "DifficultyLocked": TagByte(fields["locked"]),
        "raining": TagByte(fields["raining"]),
        "rainTime": TagInt(fields["rain_time"]),
        "thundering": TagByte(fields["thundering"]),
        "thunderTime": TagInt(fields["thunder_time"]),
        "clearWeatherTime": TagInt(fields["clear_time"]),
        "DayTime": TagLong(fields["day_time"]),
        "Time": TagLong(fields["day_time"] + 9000),
        "SpawnX": TagInt(fields["spawn"][0]),
        "SpawnY": TagInt(fields["spawn"][1]),
        "SpawnZ": TagInt(fields["spawn"][2]),
        "BorderCenterX": TagDouble(fields["border"][0]),
        "BorderCenterZ": TagDouble(fields["border"][1]),
        "BorderSize": TagDouble(fields["border"][2]),
        "BorderSafeZone": TagDouble(5.0),
        "BorderWarningBlocks": TagDouble(7.0),
        "BorderWarningTime": TagDouble(20.0),
        "BorderSizeLerpTarget": TagDouble(fields["border"][2]),
        "BorderSizeLerpTime": TagLong(0),
        "BorderDamagePerBlock": TagDouble(0.3),
        "GameRules": TagCompound({
            "doDaylightCycle": TagString("false"), "keepInventory": TagString("true"),
            "doWeatherCycle": TagString("true"), "mobGriefing": TagString("false")}),
        "DataPacks": TagCompound({"Enabled": TagList([TagString("vanilla")], TAG_STRING),
                                  "Disabled": TagList(element_type=TAG_STRING)}),
        "CustomBossEvents": TagCompound(),
    })
    if version >= 1952:   # 1.14
        data["SpawnAngle"] = TagFloat(0.0) if version >= 2566 else None
        if data["SpawnAngle"] is None:
            del data["SpawnAngle"]
        data["WanderingTraderSpawnChance"] = TagInt(25)
        data["WanderingTraderSpawnDelay"] = TagInt(24000)
        data["ServerBrands"] = TagList([TagString("vanilla")], TAG_STRING)
        data["WasModded"] = TagByte(0)
    if version < 2538:
        data["generatorName"] = TagString("default")
        data["generatorVersion"] = TagInt(1)
        data["RandomSeed"] = TagLong(SEED)
        data["MapFeatures"] = TagByte(1)
        data["generatorOptions"] = TagCompound()
    elif version < 2832:
        data["WorldGenSettings"] = _world_gen_1_16()
    else:
        data["WorldGenSettings"] = _world_gen_1_21()
    return data


UUID_A = "6b1d0c8e-2f4a-4c39-9d57-1e2f3a4b5c6d"
UUID_B = "0a9b8c7d-6e5f-4a3b-8c2d-1e0f9a8b7c6d"


def cases(derive) -> list[dict]:
    """The worlds put to the game. ``derive(data, from, to)`` is the game's
    level fixer, for the shapes no release before 4544 wrote."""
    out = []

    # 1.13.2: the player both in level.dat and in playerdata/, apart; no nether.
    data = _classic_level(1631, "Old 1.13.2", "1.13.2", game_type=1, commands=1, hardcore=0,
                          difficulty=3, locked=1, raining=1, rain_time=1234, thundering=1,
                          thunder_time=5678, clear_time=0, day_time=123456,
                          spawn=(40, 70, -2440), border=(10.5, -20.5, 5000.0))
    data["Player"] = _player(1631, UUID_A, (12.5, 66.0, -2447.5), (90.0, 10.0),
                             "minecraft:the_end", mode=1)
    out.append({
        "name": "1.13.2, a player in level.dat and in playerdata",
        "level": data,
        "files": {f"playerdata/{UUID_A}.dat": _player(
            1631, UUID_A, (1.5, 80.0, 2.5), (0.0, 0.0), "minecraft:overworld", mode=1)},
        "regions": {"region/r.0.-5.mca": REGION_1_15, "DIM1/region/r.-1.-1.mca": REGION_1_16_END},
        "dirs": [],
    })

    # 1.16.5: a server's world, the player only in playerdata; an empty nether.
    data = _classic_level(2586, "Old 1.16.5", "1.16.5", game_type=0, commands=0, hardcore=1,
                          difficulty=1, locked=0, raining=0, rain_time=0, thundering=0,
                          thunder_time=0, clear_time=48000, day_time=30000,
                          spawn=(-100, 64, 200), border=(0.0, 0.0, 59999968.0))
    out.append({
        "name": "1.16.5, a player only in playerdata, an empty nether",
        "level": data,
        "files": {f"playerdata/{UUID_B}.dat": _player(
            2586, UUID_B, (-3.25, 64.0, 7.75), (180.0, -30.0), "minecraft:the_nether", mode=0)},
        "regions": {"region/r.5.-1.mca": REGION_1_17, "DIM1/region/r.-1.-1.mca": REGION_1_16_END},
        "dirs": ["DIM-1/region"],
    })

    # 1.21.4 and the shapes the game's own fixer carries it to.
    base = _classic_level(4189, "Old 1.21.4", "1.21.4", game_type=2, commands=1, hardcore=0,
                          difficulty=2, locked=0, raining=1, rain_time=600, thundering=0,
                          thunder_time=9000, clear_time=0, day_time=777777,
                          spawn=(-20, 63, 15), border=(-100.0, 250.0, 2048.0))
    base["Player"] = _player(4189, UUID_A, (-10.5, 70.0, 5.5), (270.0, 45.0),
                             "minecraft:overworld", mode=2)
    for version, label in ((4189, "1.21.4"), (4556, "the border and spawn compounds of 4556"),
                           (4671, "the game rules of 4671")):
        data = base if version == 4189 else derive(base, 4189, version)
        out.append({
            "name": f"{label}, current chunks in the old layout",
            "level": data,
            "files": {},
            "regions": {"region/r.-1.0.mca": REGION_26_2},
            "dirs": ["DIM-1/region", "DIM1/region"],
        })
    return out


def _gz(tag: TagCompound, root_name: str = "") -> bytes:
    return gzip.compress(nbt.encode(tag, root_name), mtime=0)


def _digest(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _run(classpath, *args) -> None:
    done = subprocess.run([JRE, "-Xmx2G", "-cp", os.pathsep.join(classpath), *args],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"the probe failed:\n{done.stdout[-3000:]}\n{done.stderr[-4000:]}")


def _snapshot(world: str, regions: dict[str, str]) -> dict:
    """Every file under ``world`` -- a region file as the fixture it is,
    by digest, anything else as its bytes -- and every directory."""
    by_digest = {}
    for relative in regions.values():
        by_digest[_digest(os.path.join(FIXTURES, *relative.split("/")))] = relative
    files, dirs = {}, []
    for root, subdirs, names in os.walk(world):
        for sub in subdirs:
            dirs.append(os.path.relpath(os.path.join(root, sub), world).replace(os.sep, "/"))
        for name in names:
            if name == "session.lock":
                continue
            path = os.path.join(root, name)
            relative = os.path.relpath(path, world).replace(os.sep, "/")
            if name.endswith(".mca"):
                digest = _digest(path)
                if digest not in by_digest:
                    raise SystemExit(f"{relative} is no fixture region: the game changed one")
                files[relative] = {"fixture": by_digest[digest]}
            else:
                with open(path, "rb") as handle:
                    files[relative] = base64.b64encode(handle.read()).decode("ascii")
    return {"files": dict(sorted(files.items())), "dirs": sorted(dirs)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", default="26.3", help="installed game version")
    args = parser.parse_args()
    classpath, version_info = game_classpath(args.version)
    work = tempfile.mkdtemp(prefix="old-layout-probe-")
    try:
        source = os.path.join(work, "OldLayoutProbe.java")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write(JAVA)

        def derive(data, start, end):
            given, back = os.path.join(work, "given.dat"), os.path.join(work, "back.dat")
            with open(given, "wb") as handle:
                handle.write(_gz(TagCompound({"Data": data})))
            _run(classpath, source, "level", given, str(start), str(end), back)
            with open(back, "rb") as handle:
                return nbt.decode(gzip.decompress(handle.read()))[1]["Data"]

        worlds = cases(derive)
        saves = os.path.join(work, "saves")
        rows = []
        for index, case in enumerate(worlds):
            world = os.path.join(saves, f"w{index}")
            os.makedirs(world)
            with open(os.path.join(world, "level.dat"), "wb") as handle:
                handle.write(_gz(TagCompound({"Data": case["level"]})))
            for relative, tag in case["files"].items():
                path = os.path.join(world, *relative.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as handle:
                    handle.write(_gz(tag))
            for relative, fixture in case["regions"].items():
                path = os.path.join(world, *relative.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                shutil.copyfile(os.path.join(FIXTURES, *fixture.split("/")), path)
            for relative in case["dirs"]:
                os.makedirs(os.path.join(world, *relative.split("/")), exist_ok=True)
            rows.append({"name": case["name"],
                         "data_version": int(case["level"]["DataVersion"]),
                         "before": _snapshot(world, case["regions"])})
        _run(classpath, source, "fix", saves, *[f"w{i}" for i in range(len(worlds))])
        for index, (case, row) in enumerate(zip(worlds, rows)):
            row["after"] = _snapshot(os.path.join(saves, f"w{index}"), case["regions"])
    finally:
        shutil.rmtree(work, ignore_errors=True)

    game = version_info.get("name", args.version)
    with open(OUT, "w", encoding="ascii") as handle:
        json.dump({"game": game, "cases": rows}, handle, indent=1)
        handle.write("\n")
    print(f"wrote {os.path.relpath(OUT, REPO)}: {len(rows)} old-layout worlds through {game}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
