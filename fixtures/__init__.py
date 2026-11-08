"""Vendored Minecraft data for the test suite.

The files here are genuine game output, not synthesized: each chunk's
compressed payload, its record and the stale slack bytes in the tail of its
last sector are copied verbatim from a real save. Only the sector allocation
order is ours, and Minecraft does not guarantee that anyway.

Checking them in keeps the suite self-contained and deterministic. Reading a
live world instead means the tests silently skip wherever no save exists, and
drift underneath you while you play.

Provenance -- ``r.-1.0.mca``:
    16 chunks lifted from the overworld region of the same name, chosen to
    cover every packed bit width the world contained (1, 2, 4, 5, 6 and 7 --
    width 7 occurred exactly once in 80,312 containers), three different
    generation states, and 14 chunks carrying non-zero slack bytes. Each chunk
    keeps its original position in the region, so its own ``xPos``/``zPos``
    stay consistent with the file name.

Provenance -- ``level.dat``:
    Copied unmodified from the same save.

Provenance -- ``weather.dat``, ``world_clocks.dat``:
    Copied unmodified from the same save (Minecraft 26.2, DataVersion 4903),
    which keeps weather and the per-dimension day clocks split out of
    level.dat under ``data/minecraft/``, and its players under
    ``players/data/<uuid>.dat``.

Provenance -- ``player.dat``:
    Synthetic, in the shape of that save's player: the UUID the fixture
    level.dat's own ``singleplayer_uuid`` names (so a world assembled from
    these files resolves its player exactly as the game would), its
    DataVersion, ``playerGameType``, ``previousPlayerGameType`` and
    ``abilities``, a position over the fixture region, and an empty
    inventory and ender chest. Nothing else of the real player is kept.

Provenance -- ``modern/r.-1.0.mca`` and ``modern/level.dat``:
    4 chunks lifted from the overworld region of the same name in a save
    played on Minecraft 26.3 (DataVersion 5023), cut with
    ``make_fixture.py --min-data-version 5006`` so that every chunk carries
    the 26.3 palette codec: bare ids in a string list wherever every state
    is its block's default, and ``{"": id}`` wrappers beside
    ``{id, properties}`` compounds wherever one is not. The older fixture
    above holds neither form. ``level.dat`` is that save's, unmodified.

Provenance -- ``legacy/r.5.-1.mca``, ``legacy/level.dat`` and
``legacy/upgraded.nbt``:
    16 chunks lifted from the overworld region of the same name in OldWorld,
    the ones it still holds as Minecraft 1.17.1 saved them (DataVersion
    2730), cut with ``make_fixture.py --min-data-version 2724
    --max-data-version 2730``: 14 finished and two partly generated, sections
    of light alone below the world and above the blocks, and the sections
    1.17 left out where the world was all air. The library reads them and
    writes none of them. ``level.dat`` is that save's, unmodified.
    ``upgraded.nbt`` is the game's own upgrade of those 16 chunks, run by
    ``tools/probe_legacy_chunks.py`` -- the call the game makes when it
    loads an old chunk -- which ``tests/test_legacy.py`` holds the reader
    to cell by cell.

Provenance -- ``legacy/1.15.2/r.0.-5.mca``, ``legacy/1.16.5-end/r.-1.-1.mca``
and ``legacy/older.nbt``:
    The chunks OldWorld still holds as Minecraft 1.15.2 and 1.16.5 saved
    them: the four 1.15.2 chunks of its overworld ``r.0.-5.mca`` (DataVersion
    2230; two finished, two set aside, sections whose indices run on across
    longs at four and five bits), and eight 1.16.5 chunks of its End
    ``r.-1.-1.mca`` (DataVersion 2586), chosen by name with ``make_fixture.py
    --keys`` since the End's sections are one block each: those holding
    blocks, the empty void, and three stages set aside. ``older.nbt`` is the
    game's own upgrade of each, as its own dimension, and of five chunks
    ``tools/probe_legacy_chunks.py`` makes from a real 1.15.2 one and stamps
    as 1.13 and 1.14 saved them (a biome a column, the stage names of their
    day, sections of six and seven bits a cell), with every distinct state
    of each palette upgraded on its own and the stage 1.17.1 names it.
    ``tests/test_legacy.py`` holds the reader to all of it, cell by cell.

Provenance -- ``block_shapes.json``:
    Every block state of Minecraft 26.3, as the game itself describes it:
    the height of its collision shape, whether that shape stands where a
    body walking through the cell would be, whether its top face is rigid
    enough for a rail, which faces are sturdy, whether it is solid and
    conducts redstone, the light it gives and takes, whether it falls,
    whether it holds fluid, the faces its shape closes to light, whether a
    monster can stand on it, and whether it passes the game's per-cell test
    of room for one to spawn in (both with the game's own block tags bound,
    as a server binds them); and how water moves through it -- whether its
    fluid is water, whether water can enter the cell at all, whether it is
    a container that water waterlogs and takes a water source, and the
    faces of its collision shape that water's wall test sees (held to the
    game's own ``canPassThroughWall`` for every pair of distinct collision
    shapes before the file is written). Per block, which motion-blocking
    heightmaps count it.
    Written by ``tools/probe_block_shapes.py``, which runs a few lines of
    Java against the installed jar; :func:`block_shapes` expands it back
    into states. Not a world, so nothing here is round-tripped -- the
    tests use it to hold the judged tier's block tables to the game.
    The same run vendors, straight out of the jar's own data files, the
    block tags the judged tier is held to (:func:`block_tags`, the four
    ``supports_*`` footings among them, and the block that waters sugar
    cane besides water; the Java run lists the same tags
    from the registry it bound, and the two agree member for member) and
    each dimension type's monster spawn light rules
    (:func:`dimension_types`); and, from the game's classes, which villager
    type each biome gives (:func:`villager_types`).

Set ``MINECRAFT_SAVE`` to a world directory to run the suite against a live
save as well; the fixture is used either way.
"""

from __future__ import annotations

import functools
import itertools
import json
import os
import shutil
import tempfile
import unittest
from typing import Iterator, NamedTuple

_HERE = os.path.dirname(os.path.abspath(__file__))

FIXTURE_REGION = os.path.join(_HERE, "r.-1.0.mca")
FIXTURE_LEVEL_DAT = os.path.join(_HERE, "level.dat")
FIXTURE_WEATHER = os.path.join(_HERE, "weather.dat")
FIXTURE_WORLD_CLOCKS = os.path.join(_HERE, "world_clocks.dat")
FIXTURE_PLAYER = os.path.join(_HERE, "player.dat")
#: The uuid the fixture level.dat's singleplayer_uuid encodes — the name
#: player.dat takes under players/data/ when assembling a world.
FIXTURE_PLAYER_UUID = "be0f5711-95ac-467f-bac2-a2e5b1c4157c"

#: A program that takes a world's ``session.lock`` the way Minecraft does and
#: holds it, printing ``held`` once it has it: by ``fcntl.lockf`` where Java's
#: FileLock is a POSIX record lock, and by ``msvcrt.locking`` on Windows,
#: where it is a byte-range lock. Run it as
#: ``[sys.executable, "-c", LOCK_HOLDER, path]`` and kill it to let go.
LOCK_HOLDER = (
    "import os,sys,time\n"
    "fd=os.open(sys.argv[1],os.O_RDWR)\n"
    "try:\n"
    "    import fcntl\n"
    "    fcntl.lockf(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
    "except ImportError:\n"
    "    import msvcrt\n"
    "    msvcrt.locking(fd,msvcrt.LK_NBLCK,1)\n"
    "print('held',flush=True)\n"
    "time.sleep(60)\n"
)

#: The 26.3 sample: same region name, newer palette codec.
MODERN_REGION = os.path.join(_HERE, "modern", "r.-1.0.mca")
MODERN_LEVEL_DAT = os.path.join(_HERE, "modern", "level.dat")

#: The 1.17 sample, read-only, and the game's upgrade of it.
LEGACY_REGION = os.path.join(_HERE, "legacy", "r.5.-1.mca")
LEGACY_LEVEL_DAT = os.path.join(_HERE, "legacy", "level.dat")
LEGACY_UPGRADED = os.path.join(_HERE, "legacy", "upgraded.nbt")

#: Chunks from before 1.17, read-only -- OldWorld's 1.15.2 overworld chunks
#: and 1.16.5 End chunks -- and the game's upgrade of those and of chunks
#: made from them: see tools/probe_legacy_chunks.py.
LEGACY_DIR = os.path.join(_HERE, "legacy")
LEGACY_1_15_REGION = os.path.join(_HERE, "legacy", "1.15.2", "r.0.-5.mca")
LEGACY_1_16_END_REGION = os.path.join(_HERE, "legacy", "1.16.5-end", "r.-1.-1.mca")
LEGACY_OLDER_UPGRADED = os.path.join(_HERE, "legacy", "older.nbt")

#: Every block state's shape, from the game: see :func:`block_shapes`.
FIXTURE_BLOCK_SHAPES = os.path.join(_HERE, "block_shapes.json")


class BlockShape(NamedTuple):
    """What the game says about one block state's shape."""

    #: Height of the collision shape's top, in sixteenths; -1 for none.
    top: int
    #: Whether the collision stands in the middle of the cell, where a
    #: body walking through it would be -- a ladder or a door does not.
    body: bool
    #: Whether the top face is a full rigid square: what a rail needs.
    rigid: bool
    #: Whether the block falls when there is nothing under it.
    falls: bool
    #: Whether the state holds water or lava.
    fluid: bool
    #: Faces sturdy enough for anything (``SupportType.FULL``), one bit per
    #: face in the game's order: down, up, north, south, west, east.
    sturdy: int = 0
    #: Faces sturdy at their center (``SupportType.CENTER``), same order.
    center: int = 0
    #: ``isSolid``: what a sign needs behind or under it.
    solid: bool = False
    #: ``isRedstoneConductor``: whether power reaching it passes on.
    conductor: bool = False
    #: Block light it gives off.
    emission: int = 0
    #: What it costs light to cross (``getLightDampening``).
    dampening: int = 0
    #: Which motion-blocking heightmaps count the block: ``both``,
    #: ``leaves`` (MOTION_BLOCKING only) or ``none`` -- the two
    #: ``blocks_motion_in_heightmap`` tags. Fluid counts besides.
    heightmap: str = "both"
    #: Whether a monster can stand on it (``isValidSpawn``, for a zombie or
    #: a fire-immune wither skeleton).
    spawn_floor: bool = False
    #: Whether it passes the game's per-cell test of room to spawn in
    #: (``isValidEmptySpawnBlock``, for a zombie). Not the whole test: the
    #: game also needs the mob's box clear of collision (``noCollision`` on
    #: ``getSpawnAABB``), which no one state answers, so a fence, a wall, a
    #: chest or a bottom slab passes.
    spawn_space: bool = False
    #: The faces its shape closes to light (``LightEngine.getOcclusionShape``),
    #: one 256-bit mask per face, same order: bit ``u * 16 + v`` is the
    #: sixteenth ``[u, u+1) x [v, v+1)`` of the face, ``(u, v)`` being
    #: ``(y, z)``, ``(x, z)`` or ``(x, y)`` as the face looks along x, y or z.
    faces: tuple[int, ...] = (0,) * 6
    #: Whether the state's fluid is water: the game's ``#water`` fluid tag,
    #: a flowing level as much as a source, and never lava.
    water: bool = False
    #: Whether water can enter the cell at all (``canHoldAnyFluid``): a
    #: ``LiquidBlockContainer``, or a block tagged ``washed_away_by_fluids``
    #: -- which holds water and lava themselves.
    enters: bool = False
    #: Whether it is a ``LiquidBlockContainer``, which water waterlogs
    #: rather than washes away.
    container: bool = False
    #: Whether it is a container that takes a water source
    #: (``canPlaceLiquid`` for ``Fluids.WATER``, with nothing around it).
    takes_source: bool = False
    #: The faces of its collision shape that water's wall test sees
    #: (``Shapes.mergedFaceOccludes``, which ``canPassThroughWall`` asks),
    #: one 256-bit mask per face, laid out as ``faces`` is: the layer of the
    #: shape's grid nearest a face it ends on, and nothing on a face it stops
    #: short of or runs past; a sixteenth counts where the shape covers it
    #: whole, and a shape the game offsets by position is read at the
    #: origin. Water passes between two cells exactly when the two masks
    #: they show each other together leave a gap.
    collision: tuple[int, ...] = (0,) * 6


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _entry(blocks: dict, name: str) -> dict:
    entry = blocks[name]
    # A "like" block's every state reads exactly as the named block's.
    return blocks[entry["like"]] if "like" in entry else entry


def block_shapes(path: str = FIXTURE_BLOCK_SHAPES
                 ) -> Iterator[tuple[str, dict[str, str], BlockShape]]:
    """Every block state in the fixture, as ``(name, properties, shape)``.

    The file keeps each block's commonest record plus its exceptions, keyed
    by the fewest properties that pick them out, and the same again for how
    water moves through it (``flow`` and ``flow_except``); this expands
    every state back out of the block's property domains.

    The expansion is done once per content of the file -- the suite asks
    for it some thirty times, and each took most of a second -- and every
    call hands out the same states in the same order, each ``properties``
    a fresh dict, so a caller that changes one changes nothing for the
    next. The file is read again on every call: a file rewritten at the
    same path (the probe reading back what it has just written) is
    expanded anew.
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    for name, properties, shape in _expanded(raw):
        yield name, dict(properties), shape


@functools.lru_cache(maxsize=4)
def _expanded(raw: bytes) -> tuple[tuple[str, tuple, BlockShape], ...]:
    """:func:`block_shapes`' states for one content of the file, keyed by
    the content itself, each ``properties`` as the pairs it is made of."""
    return tuple((name, tuple(properties.items()), shape)
                 for name, properties, shape in _expand(raw))


def _expand(raw: bytes
            ) -> Iterator[tuple[str, dict[str, str], BlockShape]]:
    """Expand the fixture's text, as :func:`block_shapes` describes."""
    document = json.loads(raw.decode("utf-8"))
    masks = [int(mask, 16) for mask in document["masks"]]
    face_sets = [tuple(masks[i] for i in faces) for faces in document["faces"]]
    walls = [int(mask, 16) for mask in document["collision_masks"]]
    wall_sets = [tuple(walls[i] for i in faces)
                 for faces in document["collision_faces"]]
    blocks = document["blocks"]
    for name in blocks:
        entry = _entry(blocks, name)
        domains = entry.get("props", {})
        keys = sorted(domains)
        for values in itertools.product(*(domains[key] for key in keys)):
            properties = dict(zip(keys, values))
            record = entry["shape"]
            for when, shape in entry.get("except", ()):
                if all(properties.get(key) == value for key, value in when.items()):
                    record = shape
                    break
            fluid = entry.get("fluid")
            (top, body, rigid, sturdy, center, solid, conductor, emission,
             dampening, spawn_floor, spawn_space, faces) = record
            flow = entry["flow"]
            for when, found in entry.get("flow_except", ()):
                if all(properties.get(key) == value for key, value in when.items()):
                    flow = found
                    break
            enters, container, takes_source, collision = flow
            water = entry.get("water")
            yield name, properties, BlockShape(
                top=top, body=bool(body), rigid=bool(rigid),
                falls=bool(entry.get("falls")),
                fluid=(fluid == "always" or (fluid == "waterlogged"
                       and properties.get("waterlogged") == "true")),
                sturdy=sturdy, center=center, solid=bool(solid),
                conductor=bool(conductor), emission=emission,
                dampening=dampening,
                heightmap=entry.get("heightmap", "both"),
                spawn_floor=bool(spawn_floor), spawn_space=bool(spawn_space),
                faces=face_sets[faces],
                water=(water == "always" or (water == "waterlogged"
                       and properties.get("waterlogged") == "true")),
                enters=bool(enters), container=bool(container),
                takes_source=bool(takes_source),
                collision=wall_sets[collision])


def block_tags(path: str = FIXTURE_BLOCK_SHAPES) -> dict[str, frozenset[str]]:
    """The game's block tags the fixture vendors, by name without the
    namespace (``"air"``, ``"logs"``), each expanded to the block ids it
    holds. The two ``blocks_motion_in_heightmap`` tags are read back from
    each block's ``heightmap`` rather than stored twice.
    """
    document = _load(path)
    tags = {name: frozenset(members)
            for name, members in document["tags"].items()}
    blocks = document["blocks"]
    counted = {name: _entry(blocks, name).get("heightmap", "both")
               for name in blocks}
    tags["blocks_motion_in_heightmap"] = frozenset(
        name for name, where in counted.items() if where != "none")
    tags["blocks_motion_in_heightmap_no_leaves"] = frozenset(
        name for name, where in counted.items() if where == "both")
    return tags


def dimension_types(path: str = FIXTURE_BLOCK_SHAPES
                    ) -> dict[str, dict[str, int]]:
    """Each of the game's dimension types and its monster spawn light rule,
    ``{id: {"block_light_limit": n, "light_level_max": m}}``: a monster
    spawns at block light no higher than the limit (15 meaning none), and at
    a light level no higher than ``light_level_max``, the most the type's
    ``monster_spawn_light_level`` can draw.
    """
    return {name: dict(rule)
            for name, rule in _load(path)["dimension_types"].items()}


class VillagerTypes(NamedTuple):
    """Which villager type the game gives a villager standing in a biome:
    ``VillagerType.byBiome``, which ``Villager.finalizeSpawn`` asks of the
    biome at the villager's feet."""

    #: Biome id -> villager type id: the game's ``VillagerType.BY_BIOME``.
    by_biome: dict[str, str]
    #: The type of every biome ``by_biome`` does not name:
    #: ``VillagerData.DEFAULT_TYPE``, which ``byBiome`` falls back on.
    default: str
    #: Every villager type the game has: its ``villager_type`` registry.
    kinds: frozenset[str]


def villager_types(path: str = FIXTURE_BLOCK_SHAPES) -> VillagerTypes:
    """The game's villager types and the biomes that give each, as
    :class:`VillagerTypes`."""
    table = _load(path)["villager_types"]
    return VillagerTypes(dict(table["by_biome"]), table["default"],
                         frozenset(table["kinds"]))


def snapshot_region(source: str) -> tuple[str | None, str | None]:
    """Copy a region file somewhere private and confirm it parses.

    Returns ``(path, temp_dir)``. The caller removes ``temp_dir`` when done.
    Raises :class:`unittest.SkipTest` if the copy will not load, which for a
    live save almost always means the world is open in Minecraft.
    """
    import heaton_voxel.nbt as nbt

    if not os.path.exists(source):
        return None, None

    temp_dir = tempfile.mkdtemp(prefix="nbt-tests-")
    # Keep the r.<x>.<z>.mca name; region coordinates are parsed from it.
    candidate = os.path.join(temp_dir, os.path.basename(source))
    shutil.copyfile(source, candidate)
    try:
        nbt.NBT.load(candidate, eager=True)
    except nbt.NBTError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise unittest.SkipTest(
            f"could not snapshot {source}: {exc}. Is that world open in "
            f"Minecraft? Close it and re-run."
        ) from None
    return candidate, temp_dir
