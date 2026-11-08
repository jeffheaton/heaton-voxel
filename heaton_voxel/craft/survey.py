"""Reading the ground before you build on it.

Every build session starts the same way: an hour of working out what is
actually there. Where does the road run, where does the ground step, what
is that gray shape from the air, is the snow hiding a path. The library
had the tools for *writing* a build and none for surveying one, so each
session hand-rolled the same three loops -- and got them subtly wrong in
the same three ways.

    from heaton_voxel.craft import survey, walk_surface

    probe = walk_surface(save, -84, 320)
    print(probe.y, probe.block.name, probe.snow)      # 64 stone_bricks 1

    ground = survey(save, (-100, 300), (-56, 350))
    print(ground.text())                              # the map, as characters
    print(ground.text(mode="height"))                 # the step pattern
    print(ground.counts)                              # {'paving': 812, ...}

:func:`walk_surface` answers one column and exists because "the surface"
is several different numbers and picking the wrong one is silent. A snow
layer is part of ``WORLD_SURFACE`` and of no motion-blocking map -- those
sit *under* it, so ``surface_y`` can never see snow, while a top-down
render sees only the snow; water stops every motion-blocking map, so over
a lake they land on the water; and ``WORLD_SURFACE`` lands on the snow,
the leaves and the torch. A
probe reports the block you would stand on with the snow and the water
stepped past, and reports what it stepped past as their own numbers, so
nothing is hidden by being averaged in.

It reads one column the same way whatever the chunk carries: the game's
own maps where it wrote all of them, and the blocks themselves -- by the
game's own heightmap tags and fluids, from
:mod:`~heaton_voxel.craft.block_facts` -- where something took them away,
but only on a chunk the game finished. A partly generated chunk without
its maps is reported as such rather than walked, because its blocks are
not yet the ground they will be.

:func:`survey` is that probe over a rectangle, plus the character map the
sessions kept rewriting. The classification -- what counts as a path,
what counts as paving rather than natural stone -- is game knowledge and
taste, which is why this lives in ``craft`` and not below it: a cobbled
square is *built* in a survey's eyes even though the game will happily
generate cobblestone in a cave.

:func:`reachable` is the third question, and the one no picture can
answer: having read the ground and drawn the plan, *can you walk it*. It
models the player -- a two-cell body, the game's 0.6 auto-step, a step
you take, a step you jump and a fence no jump clears -- over the blocks
that are there plus the
blocks a build is about to write, and it walks the moves backwards as
well as forwards, because getting there and getting home are two
questions.

:func:`spawnable` is the night's question: which cells a monster could
spawn in, by the game's own floors, room and collision and by the light
that lasts (:func:`~heaton_voxel.craft.light.simulate_block_light` with
``durable=True``), so a hall or a plaza is spawn-proofed before it is
built rather than after something walks out of it.

:func:`water_body`, :func:`contained` and :func:`starved` are the water's:
where a body of water is -- every cell joined to a seed, under its surface
too, with the ice frozen on it -- whether a plan keeps the water it sits
beside or holds, by the game's own first step of spread, and which flows
a plan cuts off from what feeds them. The last two read one amount model,
the game's ``getNewLiquid``, so what one calls fed the other does too.

Nothing here writes to the world.
"""

from __future__ import annotations

from collections import deque
from typing import NamedTuple

from ..nbt import NBTError
from ..block_defaults import BLOCK_DEFAULTS
from ..blocks import AIR_BLOCKS, BlockState
from ..blocks import chunk_finished as _finished
from ._plan import read_overlay
from .block_facts import (FULL_FACE, facts, holds_fluid, holds_water,
                          in_heightmap, known)
from .blockinfo import _current, falls
from .errors import Refused
from .geometry import COMPASS, OPPOSITE
from .light import MONSTER_SPAWN_LIGHT, _game_reads, simulate_block_light

__all__ = [
    "SurfaceProbe",
    "SurveyMap",
    "walk_surface",
    "survey",
    "classify",
    "KIND_GLYPHS",
    "AUTO_STEP",
    "JUMP_HEIGHT",
    "REACH_GLYPHS",
    "ReachMap",
    "StepSpace",
    "reachable",
    "step_kind",
    "iron_door_opens",
    "unsupported",
    "Unsupported",
    "spawnable",
    "SpawnCell",
    "MONSTER_SPAWN_LIGHT",
    "water_body",
    "WaterBody",
    "contained",
    "Containment",
    "Leak",
    "starved",
]

#: The snow *layer* block. ``snow_block`` is a full block and real ground;
#: this is the thin covering that sits on top of whatever is underneath.
SNOW_LAYER = "minecraft:snow"

#: What freezes into a sheet: a probe that stands on one over water or air
#: reports the sheet, the water under it and the bed under that.
_ICE = frozenset(("minecraft:ice", "minecraft:frosted_ice",
                  "minecraft:packed_ice", "minecraft:blue_ice"))

#: A fluid, and a surface: the reader steps through water, never lava.
_LAVA = "minecraft:lava"

#: Most of what a probe wades through, asked for by name before anything
#: slower.
_PLAIN_WATER = "minecraft:water"

#: The properties a block holds no water in unless it always does: what
#: :func:`classify` asks :func:`holds_water` with, by name alone.
_DRY = {"waterlogged": "false"}

#: Why a column has no probe, in the words a :class:`SurveyMap` repr uses.
_ABSENT = {
    "ungenerated": "ungenerated",
    "unreadable": "unreadable",
    "partial": "partly generated",
    "empty": "empty",
}

#: The three maps a chunk must carry for its column to be read from them:
#: the game writes all of them, and ``rebuild_heightmaps`` deletes all but
#: the first, so the three together say the game's maps are still there.
#: The first two are read; the bed under water is walked down to, because
#: the water's cells are counted on the way. ``MOTION_BLOCKING`` is not
#: one: nothing here stands on leaves.
_GAME_MAPS = ("WORLD_SURFACE", "MOTION_BLOCKING_NO_LEAVES", "OCEAN_FLOOR")

#: One character per kind, for the character map. Chosen so that built
#: things are dense (``#``, ``W``) and natural ones are light (``.``,
#: ``,``) -- a town reads as a dark patch on a pale field.
KIND_GLYPHS = {
    "ungenerated": " ",
    "unreadable": "?",
    "water": "~",
    "ice": "i",
    "tree": "T",
    "plant": '"',
    "farm": "f",
    "path": "P",
    "ground": ".",
    "sand": ",",
    "stone": "^",
    "paving": "#",
    "wood": "W",
    "light": "*",
    "glass": "g",
    "other": "+",
}

_EXACT_KINDS = {
    "minecraft:dirt_path": "path",
    "minecraft:grass_block": "ground", "minecraft:dirt": "ground",
    "minecraft:coarse_dirt": "ground", "minecraft:rooted_dirt": "ground",
    "minecraft:podzol": "ground", "minecraft:mycelium": "ground",
    "minecraft:moss_block": "ground", "minecraft:mud": "ground",
    "minecraft:clay": "ground", "minecraft:snow_block": "ground",
    "minecraft:sand": "sand", "minecraft:red_sand": "sand",
    "minecraft:gravel": "sand", "minecraft:suspicious_sand": "sand",
    "minecraft:suspicious_gravel": "sand",
    "minecraft:farmland": "farm", "minecraft:hay_block": "farm",
    "minecraft:wheat": "farm", "minecraft:carrots": "farm",
    "minecraft:potatoes": "farm", "minecraft:beetroots": "farm",
    "minecraft:pumpkin_stem": "farm", "minecraft:melon_stem": "farm",
    "minecraft:pumpkin": "farm", "minecraft:melon": "farm",
    "minecraft:composter": "farm",
    "minecraft:stone": "stone", "minecraft:deepslate": "stone",
    "minecraft:granite": "stone", "minecraft:diorite": "stone",
    "minecraft:andesite": "stone", "minecraft:tuff": "stone",
    "minecraft:calcite": "stone", "minecraft:basalt": "stone",
    "minecraft:netherrack": "stone", "minecraft:blackstone": "stone",
    "minecraft:end_stone": "stone", "minecraft:bedrock": "stone",
    "minecraft:obsidian": "stone", "minecraft:magma_block": "stone",
    "minecraft:cobblestone": "paving", "minecraft:mossy_cobblestone": "paving",
    "minecraft:bricks": "paving", "minecraft:quartz_block": "paving",
    "minecraft:sandstone": "paving", "minecraft:red_sandstone": "paving",
    "minecraft:purpur_block": "paving", "minecraft:bone_block": "paving",
    "minecraft:reinforced_deepslate": "paving",
    "minecraft:glowstone": "light", "minecraft:sea_lantern": "light",
    "minecraft:shroomlight": "light", "minecraft:redstone_lamp": "light",
    "minecraft:jack_o_lantern": "light", "minecraft:beacon": "light",
    "minecraft:campfire": "light", "minecraft:soul_campfire": "light",
    "minecraft:torch": "light", "minecraft:wall_torch": "light",
    "minecraft:lantern": "light", "minecraft:soul_lantern": "light",
    "minecraft:end_rod": "light", "minecraft:conduit": "light",
    "minecraft:ice": "ice", "minecraft:packed_ice": "ice",
    "minecraft:blue_ice": "ice", "minecraft:frosted_ice": "ice",
    "minecraft:powder_snow": "ice",
    "minecraft:dandelion": "plant", "minecraft:poppy": "plant",
    "minecraft:azure_bluet": "plant", "minecraft:oxeye_daisy": "plant",
    "minecraft:cornflower": "plant", "minecraft:allium": "plant",
    "minecraft:lily_of_the_valley": "plant", "minecraft:blue_orchid": "plant",
    "minecraft:wither_rose": "plant", "minecraft:torchflower": "plant",
    "minecraft:pink_petals": "plant", "minecraft:dead_bush": "plant",
    "minecraft:brown_mushroom": "plant", "minecraft:red_mushroom": "plant",
    "minecraft:sweet_berry_bush": "plant", "minecraft:bush": "plant",
    "minecraft:cactus": "plant", "minecraft:bamboo": "plant",
    "minecraft:sugar_cane": "plant", "minecraft:lily_pad": "plant",
    "minecraft:vine": "plant", "minecraft:moss_carpet": "plant",
}

# Ordered: the first match wins, so put the specific before the general.
# "_slab"/"_stairs"/"_wall" carry no material of their own, which is why
# the wood test runs before them -- an oak stair is wood, a stone stair
# is paving, and the name is all there is to go on.
_KIND_HEURISTICS = (
    ("_log", "tree"), ("_wood", "tree"), ("_leaves", "tree"),
    ("_sapling", "tree"), ("_stem", "tree"), ("_hyphae", "tree"),
    ("mushroom_block", "tree"), ("bee_nest", "tree"),
    ("_candle", "light"), ("froglight", "light"), ("_lamp", "light"),
    ("glass", "glass"),
    ("_planks", "wood"), ("_fence", "wood"), ("_door", "wood"),
    ("_trapdoor", "wood"), ("_sign", "wood"), ("_barrel", "wood"),
    ("bookshelf", "wood"), ("_ladder", "wood"), ("scaffolding", "wood"),
    ("_ore", "stone"), ("_bricks", "paving"), ("_brick", "paving"),
    ("polished_", "paving"), ("smooth_", "paving"), ("cut_", "paving"),
    ("chiseled_", "paving"), ("cracked_", "paving"), ("_tiles", "paving"),
    ("_concrete", "paving"), ("terracotta", "paving"),
    ("prismarine", "paving"), ("purpur", "paving"), ("quartz", "paving"),
    ("_slab", "paving"), ("_stairs", "paving"), ("_wall", "paving"),
    ("_carpet", "wood"), ("_wool", "wood"),
    ("_grass", "plant"), ("fern", "plant"), ("_flower", "plant"),
    ("tulip", "plant"), ("_bush", "plant"), ("seagrass", "plant"),
    ("kelp", "plant"), ("coral", "plant"), ("_sprouts", "plant"),
    ("dripleaf", "plant"), ("azalea", "plant"), ("lichen", "plant"),
    ("_roots", "plant"), ("_petals", "plant"), ("_crop", "farm"),
)

_WOODS = (
    "oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove",
    "cherry", "bamboo", "crimson", "warped", "pale_oak",
)


def classify(name: str) -> str:
    """What kind of thing ``name`` is, for a survey map.

    Best-effort game knowledge, in the spirit of
    :class:`~heaton_voxel.craft.render.ColorMap`: the answer is a
    judgment, not a fact the format stores. Anything unplaced comes back
    ``"other"`` rather than guessing.
    """
    kind = _EXACT_KINDS.get(name)
    if kind is not None:
        return kind
    for token, found in _KIND_HEURISTICS:
        if token in name:
            # A wooden stair is wood; a stone one is paving. Only the
            # shape-suffixes are ambiguous, so only they consult the species.
            if found == "paving" and token in ("_slab", "_stairs", "_wall"):
                bare = name.split(":", 1)[-1]
                if any(bare.startswith(wood) or f"_{wood}_" in bare
                       for wood in _WOODS):
                    return "wood"
            return found
    # Water whatever its state: water and a bubble column. The always-wet
    # plants have been called plants above, by name, and lava, which
    # holds_water never counts, is a surface.
    if holds_water(name, _DRY):
        return "water"
    return "other"


#: :class:`SurfaceProbe`'s ``bed_y`` when none is given: the same as ``y``.
_SAME = object()


class SurfaceProbe:
    """What one column looks like from above, with nothing hidden.

    :attr:`y` is the block you would stand on: snow layers are stepped
    past, and open water is stepped through to the bed under it, and what
    was stepped past is reported separately in :attr:`snow` and
    :attr:`water` rather than quietly shifting the height. An ice sheet
    over water or air is something you stand on, so there :attr:`y` is the
    ice, and :attr:`ice`, :attr:`water` and :attr:`bed_y` say what is under
    it. :attr:`top_y` is the topmost non-air thing in the column, which is
    what ``WORLD_SURFACE`` and a top-down render see -- when the two
    disagree, something is resting on the ground.
    """

    __slots__ = ("x", "z", "y", "block", "top_y", "top", "snow", "water",
                 "kind", "bed_y", "ice", "lid", "status", "_waterline")

    def __init__(self, x, z, y, block, top_y, top, snow, water, kind, *,
                 bed_y=_SAME, ice=0, lid=False, status=None, waterline=None):
        self.x = x
        self.z = z
        #: World y of the block you would stand on.
        self.y = y
        #: The :class:`BlockState` at :attr:`y`.
        self.block = block
        #: World y of the topmost non-air block, snow and plants included.
        self.top_y = top_y
        #: The :class:`BlockState` at :attr:`top_y`.
        self.top = top
        #: Snow layers resting on :attr:`y`; 0 when there is no snow.
        self.snow = snow
        #: Cells of water in the column: standing over :attr:`y`, or sealed
        #: under the ice :attr:`y` is; 0 when dry. Only the cells that hold
        #: water count -- where air hangs in the run, under a waterlogged
        #: sign or in a pocket in a lake, :attr:`waterline` stands higher
        #: over :attr:`y` than this. Water only: lava is a surface, and is
        #: stood on.
        self.water = water
        #: The :func:`classify` verdict for :attr:`block`.
        self.kind = kind
        #: World y of what holds the water up: :attr:`y` itself, unless
        #: :attr:`y` is an ice sheet, when it is the first block under the
        #: sheet and its water -- or None, when the sheet lies on air.
        self.bed_y = y if bed_y is _SAME else bed_y
        #: Thickness of the ice sheet :attr:`y` is, counted only where the
        #: sheet lies on water or air; 0 for anything else, ice on rock
        #: included.
        self.ice = ice
        #: True when the block this probe relies on -- the ground, the bed,
        #: or a sheet with nothing under it -- has air directly under it: ice
        #: over a room, a lake bed over a cave, a one-block roof. Melting or
        #: digging it opens what is underneath.
        self.lid = lid
        #: The chunk's ``Status``, as stored.
        self.status = status
        self._waterline = ((y if ice else y + water) if waterline is None
                           else waterline)

    @property
    def buried(self) -> bool:
        """True when something is resting on the ground here.

        The case that misleads a top-down render: the map draws the snow,
        the survey wants the path underneath.
        """
        return self.top_y > self.y

    @property
    def waterline(self) -> int:
        """The top of what stops a fall: the ice where :attr:`y` is a sheet,
        the water's surface over a lake, else :attr:`y` -- the
        ``MOTION_BLOCKING_NO_LEAVES`` the game writes. Over open water it is
        ``y + water`` unless something other than water hangs in the run
        (see :attr:`water`), when it is higher."""
        return self._waterline

    @property
    def wet(self) -> bool:
        """True where the probe is water or an ice sheet -- open water,
        frozen water, or ice over air: what ground work would have to
        bridge or fill rather than stand on. A waterlogged rail or sign
        holds water too, and makes its column wet."""
        return self.water > 0 or self.ice > 0

    @property
    def glyph(self) -> str:
        return KIND_GLYPHS.get(self.kind, "+")

    def __repr__(self) -> str:
        parts = [f"({self.x}, {self.z})", f"y={self.y}", self.kind]
        if self.snow:
            parts.append(f"{self.snow} snow")
        if self.ice:
            parts.append(f"{self.ice} ice")
        if self.water:
            parts.append(f"{self.water} deep")
        if self.lid:
            parts.append("lid")
        return f"<SurfaceProbe {', '.join(parts)}>"


def _column(accessor, lx: int, lz: int, start: int):
    """``(y, state)`` down one column from ``start``, every section holding
    nothing but air passed over by its palette alone."""
    low = accessor.min_y
    for section_y in sorted(accessor.sections, reverse=True):
        base = section_y * 16
        if base > start:
            continue
        container = accessor.sections[section_y].blocks
        if container is None or all(s.name in AIR_BLOCKS for s in container.palette):
            continue
        for y in range(min(base + 15, start, accessor.max_y), max(base, low) - 1, -1):
            yield y, accessor.get(lx, y, lz)


def _game_maps(accessor, lx: int, lz: int, start: int | None = None):
    """One column's ``WORLD_SURFACE``, ``MOTION_BLOCKING``,
    ``MOTION_BLOCKING_NO_LEAVES`` and ``OCEAN_FLOOR``, as world ys (None for
    a map nothing in the column registers on), derived from its blocks the
    way the game derives them: the topmost block that is not air; the
    topmost the ``blocks_motion_in_heightmap`` tag names or that holds
    fluid; the same by the ``_no_leaves`` tag; and the topmost the tag
    names, fluid or not. ``start`` begins the walk there instead of at the
    top of the world.
    """
    top = motion = stand = floor = None
    for y, state in _column(accessor, lx, lz,
                            accessor.max_y if start is None else start):
        name = state.name
        if top is None:
            if name in AIR_BLOCKS:
                continue
            top = y
        tagged = in_heightmap(name)
        if floor is None and tagged:
            floor = y
        if stand is None:
            fluid = holds_fluid(name, state.properties)
            if motion is None and (tagged or fluid):
                motion = y
            if fluid or in_heightmap(name, leaves=False):    # _stops
                stand = y
        if stand is not None and floor is not None:
            break
    return top, motion, stand, floor


def _wades(state) -> bool:
    """Whether a probe steps down through ``state``: it holds water, and
    nothing ``MOTION_BLOCKING_NO_LEAVES`` stops at by its tag stands in it
    -- water, a bubble column, kelp, seagrass, a waterlogged sea pickle or
    coral, and waterlogged leaves, because canopy is not ground wet or dry:
    a mangrove swamp's leaves at the waterline are the swamp's water, not
    dry land. A waterlogged stair or fence is stood on like a dry one, and
    lava, which :func:`~heaton_voxel.craft.block_facts.holds_water` never
    counts, is a surface."""
    return (holds_water(state.name, state.properties)
            and not in_heightmap(state.name, leaves=False))


def _bed(state) -> bool:
    """Whether ``state`` holds water up, for a probe wading down: what
    ``OCEAN_FLOOR`` stops at (the tag, leaves included) and does not itself
    wade, or lava, which is a surface."""
    return state.name == _LAVA or (in_heightmap(state.name) and not _wades(state))


def _stops(state) -> bool:
    """Whether ``MOTION_BLOCKING_NO_LEAVES`` stops at ``state``: the
    ``_no_leaves`` tag, or fluid -- what a probe stands on, or wades from,
    and the ground ``Builder.clear_vegetation`` clears a column down to and
    the roof it never clears past."""
    return (in_heightmap(state.name, leaves=False)
            or holds_fluid(state.name, state.properties))


def _first(accessor, lx: int, lz: int, start: int, test) -> int | None:
    """The first y at or below ``start`` whose state passes ``test``."""
    return next((y for y, state in _column(accessor, lx, lz, start)
                 if test(state)), None)


def _layers(state) -> int:
    try:
        return int(state.properties.get("layers", 1))
    except (TypeError, ValueError):
        return 1


def _read_column(dim, x: int, z: int, below: int | None = None):
    """One column, as a :class:`SurfaceProbe` or the reason there is none:
    ``"ungenerated"``, ``"partial"`` or ``"empty"``. A chunk that cannot be
    read, and a heightmap that contradicts its own chunk, raise a plain
    :class:`~heaton_voxel.nbt.NBTError`. Every read stays in the one column.

    The game's maps are trusted where the chunk carries all three it needs,
    whatever its status -- they are what the game itself stands on -- as
    far as the blocks they name agree. Where any is missing (an earlier
    ``rebuild_heightmaps`` kept only ``WORLD_SURFACE``), a finished chunk
    is walked, by the predicates the game writes its maps with
    (:func:`_game_maps`); an unfinished one is ``"partial"``, because its
    blocks are not yet the ground they will be and a scan of them invents
    terrain. ``below`` always walks. Under water the bed is walked down to
    on either path, counting the cells that hold water on the way.
    """
    chunk_x, chunk_z = x >> 4, z >> 4
    accessor = dim.blocks(chunk_x, chunk_z)        # raises if unreadable
    if accessor is None:
        return "ungenerated"
    lx, lz = x & 15, z & 15
    status = accessor.status
    high = accessor.max_y
    maps = dim.chunk(chunk_x, chunk_z).data.get("Heightmaps")
    mapped = maps is not None and all(key in maps for key in _GAME_MAPS)

    if below is None and mapped:
        top_y = accessor.surface_y(lx, lz, "WORLD_SURFACE")
        stand = accessor.surface_y(lx, lz, "MOTION_BLOCKING_NO_LEAVES")
        # Trusted, but not past the block a map names: one written before
        # 26.3 stops at a sign, which this game's tag does not, and one
        # gone stale can name air -- or a cell above the world, which is
        # air as surely (a map counted from the wrong floor reads sixteen
        # high). Each is walked down from, to what a current map names.
        if top_y is not None and (
                top_y > high or accessor.get(lx, top_y, lz).name in AIR_BLOCKS):
            top_y = _first(accessor, lx, lz, min(top_y, high),
                           lambda state: state.name not in AIR_BLOCKS)
        if stand is not None and (
                stand > high or not _stops(accessor.get(lx, stand, lz))):
            stand = _first(accessor, lx, lz, min(stand - 1, high), _stops)
    elif _finished(status):
        if below is not None:
            start = min(below, high)
        elif maps is not None and "WORLD_SURFACE" in maps:
            start = accessor.surface_y(lx, lz, "WORLD_SURFACE")
            if start is None:
                return "empty"
            start = min(start, high)        # above the world: stale, as above
        else:
            start = high
        top_y, _, stand, _ = _game_maps(accessor, lx, lz, start)
    else:
        return "partial"
    if top_y is None or stand is None:
        return "empty"

    def at(y):
        return accessor.get(lx, y, lz) if y >= accessor.min_y else None

    block = accessor.get(lx, stand, lz)
    y, water, ice, bed_y = stand, 0, 0, stand
    if _wades(block):
        # Down through the water to its bed, counting the cells that hold
        # water: air under a waterlogged sign, or a pocket in a lake, lies
        # in the run and is not water.
        floor = None
        for cell, state in _column(accessor, lx, lz, stand):
            if state.name == _PLAIN_WATER or _wades(state):
                water += 1
            elif _bed(state):
                floor, block = cell, state
                break
        if floor is None:
            return "empty"                  # water over nothing at all
        y = bed_y = floor
    elif block.name in _ICE:
        bottom = stand
        while (below_sheet := at(bottom - 1)) is not None and below_sheet.name in _ICE:
            bottom -= 1
        cell, under = bottom - 1, below_sheet
        if under is not None and (under.name in AIR_BLOCKS or _wades(under)):
            ice = stand - bottom + 1
            while under is not None and _wades(under):
                water += 1
                cell -= 1
                under = at(cell)
            bed_y = None if under is None or under.name in AIR_BLOCKS else cell
    if bed_y is None:
        lid = True
    else:
        floor_block = at(bed_y - 1)
        lid = floor_block is not None and floor_block.name in AIR_BLOCKS
    # Snow lies on what you stand on, and no motion-blocking map counts it,
    # so it is read from the cell above rather than stepped down from --
    # above the bed, too, where the water hangs over air and not on it. One
    # block carries its whole depth in ``layers``.
    snow = 0
    if y < top_y:
        above = accessor.get(lx, y + 1, lz)
        if above.name == SNOW_LAYER:
            snow = _layers(above)

    return SurfaceProbe(
        x, z, y, block, top_y, accessor.get(lx, top_y, lz), snow, water,
        classify(block.name), bed_y=bed_y, ice=ice, lid=lid, status=status,
        waterline=stand)


def walk_surface(save, x: int, z: int, *, dimension: str | None = None,
                 below: int | None = None) -> SurfaceProbe | None:
    """Probe one column: what you would stand on at ``(x, z)``.

    ``None`` means nothing to stand on, and there are three kinds of
    nothing, which :func:`survey` keeps apart in
    :attr:`SurveyMap.missing`: no chunk there; a chunk the generator set
    aside part-way that lacks the game's maps -- it never grew them, or
    an earlier ``rebuild_heightmaps`` left it only ``WORLD_SURFACE`` -- whose
    blocks are not yet the ground they will be, so a scan for a surface there is
    ninety times slower than a lookup and invents terrain; or a column with
    nothing in it to stand on. A chunk that cannot be read -- damaged, or
    older than the toolkit reads -- raises a plain
    :class:`~heaton_voxel.nbt.NBTError`, and so does a heightmap that
    contradicts its own chunk: corruption must never read as empty ground.

    The column is read the same way whatever the chunk carries. Where the
    game wrote ``WORLD_SURFACE``, ``MOTION_BLOCKING_NO_LEAVES`` and
    ``OCEAN_FLOOR``, the first two are trusted, at any status -- though not
    past the block a map names: a map written before 26.3 stops at a sign,
    which nothing stands on, and a map gone stale can name air, or a cell
    above the world, and the probe walks down from any of them. Where any
    of the three is missing -- an earlier
    :meth:`~heaton_voxel.world.MinecraftSave.rebuild_heightmaps` kept only
    the first -- a finished chunk is read from its blocks, by the game's
    own heightmap tags and fluids
    (:func:`~heaton_voxel.craft.block_facts.in_heightmap`,
    :func:`~heaton_voxel.craft.block_facts.holds_fluid`), which give back
    the maps the game wrote.

    Snow layers are no part of any motion-blocking map, so they are read
    from the cell above what you stand on and reported as
    :attr:`SurfaceProbe.snow`. Water -- with kelp, seagrass and every
    waterlogged state nothing solid stands in, waterlogged leaves among
    them -- is stepped through to the bed, and the cells that hold it are
    reported as :attr:`SurfaceProbe.water`; lava is a surface. An ice sheet
    over water or air is stood on, and reports its thickness, the water
    sealed under it and the bed under that. Under a canopy the probe
    reports the ground, not the leaves, but a *trunk* is something you
    cannot walk through and is reported as itself.

    ``below`` reads the floor under a point rather than the top of the
    column -- the first thing at or below ``y=below``, for an interior, a
    cave or the Nether's roofed world. That is a read of the blocks, so it
    answers on a finished chunk only.
    """
    found = _read_column(save.dimension(dimension), int(x), int(z),
                         None if below is None else int(below))
    return found if isinstance(found, SurfaceProbe) else None


class SurveyMap:
    """A rectangle of :class:`SurfaceProbe`, and the maps you read it with.

    Missing entries are columns with nothing to report, kept as ``None``
    rather than dropped, so a hole in the survey looks like a hole and not
    like a smaller rectangle. :attr:`missing` says why, column by column:
    ``"ungenerated"``, ``"unreadable"`` (call :func:`walk_surface` there
    for the error), ``"partial"`` (partly generated and unmapped) or
    ``"empty"``.
    """

    __slots__ = ("x0", "z0", "x1", "z1", "step", "probes", "counts",
                 "missing")

    def __init__(self, x0, z0, x1, z1, step):
        self.x0, self.z0, self.x1, self.z1 = x0, z0, x1, z1
        self.step = step
        self.probes: dict[tuple[int, int], SurfaceProbe | None] = {}
        self.counts: dict[str, int] = {}
        #: ``(x, z)`` -> why that column has no probe.
        self.missing: dict[tuple[int, int], str] = {}

    @property
    def xs(self) -> range:
        return range(self.x0, self.x1 + 1, self.step)

    @property
    def zs(self) -> range:
        return range(self.z0, self.z1 + 1, self.step)

    def probe(self, x: int, z: int) -> SurfaceProbe | None:
        return self.probes.get((x, z))

    def cells(self, kind: str) -> list[tuple[int, int]]:
        """Every surveyed column of one kind, in ``(z, x)`` reading order."""
        return [(x, z) for z in self.zs for x in self.xs
                if (p := self.probes.get((x, z))) is not None and p.kind == kind]

    def heights(self) -> list[int]:
        """Every standing height in the survey, unsorted."""
        return [p.y for p in self.probes.values() if p is not None]

    def text(self, mode: str = "kind", *, base: int | None = None,
             legend: bool = False) -> str:
        """The survey as characters, with an x ruler and z labels.

        ``mode`` picks what each cell shows:

        * ``"kind"`` -- one :data:`KIND_GLYPHS` character, the map that
          answers "what is down there"
        * ``"height"`` -- two digits of height above ``base`` (the lowest
          surveyed column unless given), the map that answers "where does
          the ground step"
        * ``"snow"`` / ``"water"`` -- depth digits, ``.`` for none

        ``legend=True`` appends the glyphs actually used and their names,
        so the map explains itself when it is pasted somewhere else.
        """
        if mode not in ("kind", "height", "snow", "water"):
            raise ValueError(
                f"unknown survey mode {mode!r}: use 'kind', 'height', "
                f"'snow' or 'water'"
            )
        if mode == "height":
            found = self.heights()
            if base is None:
                base = min(found) if found else 0
            width = 2
        else:
            width = 1

        xs = list(self.xs)
        label = max(len(str(z)) for z in self.zs) if self.zs else 1
        lines = []
        pad = " " * (label + 1)
        if width == 1:
            lines.append(pad + "".join(str(abs(x) // 10 % 10) for x in xs))
            lines.append(pad + "".join(str(abs(x) % 10) for x in xs))
        else:
            lines.append(pad + "".join(f"{abs(x) // 10 % 10:>{width}}"
                                       for x in xs))
            lines.append(pad + "".join(f"{abs(x) % 10:>{width}}" for x in xs))

        for z in self.zs:
            row = []
            for x in xs:
                probe = self.probes.get((x, z))
                if probe is None:
                    row.append("?".rjust(width))
                elif mode == "kind":
                    row.append(probe.glyph)
                elif mode == "height":
                    delta = probe.y - base
                    row.append(f"{delta:>{width}}" if 0 <= delta < 100 else "**")
                else:
                    depth = probe.snow if mode == "snow" else probe.water
                    row.append(str(depth) if 0 < depth < 10
                               else ("+" if depth else "."))
            lines.append(f"{z:>{label}} " + "".join(row))

        if mode == "height":
            lines.append(f"{pad}heights above y={base}")
        if legend:
            used = sorted(self.counts, key=lambda k: -self.counts[k])
            lines.append(pad + "  ".join(
                f"{KIND_GLYPHS.get(k, '+')}={k}" for k in used))
        return "\n".join(lines)

    def __len__(self) -> int:
        return sum(1 for p in self.probes.values() if p is not None)

    def __repr__(self) -> str:
        top = sorted(self.counts.items(), key=lambda kv: -kv[1])[:3]
        summary = ", ".join(f"{n} {kind}" for kind, n in top)
        missing = sum(1 for p in self.probes.values() if p is None)
        parts = [f"({self.x0}, {self.z0})..({self.x1}, {self.z1})"]
        if self.step != 1:
            parts.append(f"every {self.step}")
        if summary:
            parts.append(summary)
        if missing:
            why = [f"{n} {_ABSENT[reason]}" for reason in _ABSENT
                   if (n := sum(1 for r in self.missing.values() if r == reason))]
            parts.append(f"{missing} columns with no surface"
                         + (f" ({', '.join(why)})" if why else ""))
        return f"<SurveyMap {', '.join(parts)}>"


def survey(save, corner1, corner2, *, dimension: str | None = None,
           step: int = 1) -> SurveyMap:
    """Probe every column in a rectangle. Read-only.

    Corners are ``(x, z)`` or ``(x, y, z)``; ``y`` is ignored, because a
    survey reads the ground wherever it happens to be. ``step`` samples a
    coarser grid -- a whole region at ``step=4`` costs a sixteenth of the
    lookups and still shows where the roads run.

    Each column is read as :func:`walk_surface` reads it, with one
    difference: a chunk that cannot be read, or whose heightmap contradicts
    it, does not stop the survey. Its columns are holes like any other, and
    :attr:`SurveyMap.missing` names them ``"unreadable"`` -- told apart
    from ground that is not there, never mistaken for it -- so a damaged
    chunk can be surveyed around; :func:`walk_surface` on one of its
    columns raises the error itself.
    """
    x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
    z0, z1 = sorted((int(corner1[-1]), int(corner2[-1])))
    if step < 1:
        raise ValueError("survey step must be at least 1")

    dim = save.dimension(dimension)
    result = SurveyMap(x0, z0, x1, z1, step)
    for z in result.zs:
        for x in result.xs:
            try:
                found = _read_column(dim, x, z)
            except NBTError:
                found = "unreadable"
            if isinstance(found, str):
                result.probes[(x, z)] = None
                result.missing[(x, z)] = found
                continue
            result.probes[(x, z)] = found
            result.counts[found.kind] = result.counts.get(found.kind, 0) + 1
    return result


# --------------------------------------------------------------------------
# Can you actually walk there?
# --------------------------------------------------------------------------

#: Vanilla auto-step: the height you climb without pressing jump. A stair
#: or a bottom slab is half a block from the side and so inside it; a full
#: block is not, which is the whole reason a staircase of treads walks and
#: a staircase of beams does not.
AUTO_STEP = 0.6

#: What a standing jump clears.
JUMP_HEIGHT = 1.25

#: The game's ``climbable`` tag, by bare name, held to the vendored tag by
#: ``tests/test_survey.py``. A body in one of these holds on: it does not
#: fall, and it rises for a collision or for a jump held down
#: (``LivingEntity.handleRelativeFrictionAndCalculateMovement``, javap, 26.3),
#: so a vine with nothing behind it climbs as a ladder does.
_CLIMBABLE = frozenset("""
ladder vine scaffolding cave_vines cave_vines_plant twisting_vines
twisting_vines_plant weeping_vines weeping_vines_plant
""".split())

#: What you can walk through. Matched **exactly**, or by whole
#: suffix/prefix -- never by bare substring: see :func:`step_kind`. Held
#: to the game's own collision shapes by ``tests/test_survey.py``, against
#: ``fixtures/block_shapes.json``.
_PASSABLE = frozenset("""
air cave_air void_air water bubble_column snow powder_snow light
vine glow_lichen short_grass tall_grass fern large_fern dead_bush bush
seagrass tall_seagrass kelp kelp_plant sugar_cane cobweb lily_pad
tripwire tripwire_hook ladder redstone_wire lever repeater comparator
sweet_berry_bush cave_vines cave_vines_plant hanging_roots moss_carpet
pale_moss_carpet pale_hanging_moss small_dripleaf big_dripleaf_stem
bamboo_sapling rail powered_rail detector_rail activator_rail scaffolding
torch torchflower nether_sprouts twisting_vines twisting_vines_plant
weeping_vines weeping_vines_plant dandelion golden_dandelion poppy
blue_orchid allium azure_bluet oxeye_daisy cornflower lily_of_the_valley
wither_rose sunflower lilac rose_bush peony red_tulip orange_tulip
white_tulip pink_tulip pitcher_plant open_eyeblossom closed_eyeblossom
pink_petals wildflowers leaf_litter short_dry_grass tall_dry_grass
cactus_flower red_shrub spore_blossom frogspawn mangrove_propagule
resin_clump sculk_vein brown_mushroom red_mushroom crimson_fungus
warped_fungus wheat carrots potatoes beetroots nether_wart
torchflower_crop melon_stem pumpkin_stem attached_melon_stem
attached_pumpkin_stem structure_void
""".split())
_PASSABLE_SUFFIX = (
    "_torch", "_sign", "_banner", "_carpet", "_sapling", "_pressure_plate",
    "_rail", "_button", "_wire", "_bush", "_roots", "_sprouts", "_coral",
    "_coral_fan", "_coral_wall_fan", "_tulip",
)

#: Whole blocks whose names a passable suffix would claim: a mangrove's
#: roots are something you stand on, not the hanging kind you walk through.
_SOLID = frozenset(("mangrove_roots", "muddy_mangrove_roots"))

#: Climbed from the side at half a block, which is inside the auto-step:
#: anything whose top is no higher than the 0.6 the game steps up unasked.
_HALF_SUFFIX = ("_slab", "_stairs", "_bed", "_trapdoor", "_candle")
_HALF_PREFIX = ("potted_",)
_HALF = frozenset("""
daylight_detector flower_pot sea_pickle candle cake heavy_core
stonecutter sculk_sensor calibrated_sculk_sensor sculk_shrieker turtle_egg
pitcher_crop
""".split())

#: Lanterns: half a block standing, and a block's worth hanging, since a
#: hanging lantern's top is above the auto-step and its body is where your
#: head goes.
_LANTERNS = frozenset(
    "lantern soul_lantern copper_lantern exposed_copper_lantern "
    "weathered_copper_lantern oxidized_copper_lantern waxed_copper_lantern "
    "waxed_exposed_copper_lantern waxed_weathered_copper_lantern "
    "waxed_oxidized_copper_lantern".split()
)

#: Heads and skulls on the floor are half a block high; on a wall they
#: stand out across the cell at head height, and a piston's head is part
#: of the piston.
_HEAD_SUFFIX = ("_head", "_skull")
_WALL_HEAD_SUFFIX = ("_wall_head", "_wall_skull")

#: One character per verdict, for :meth:`ReachMap.text`. A stranded cell is
#: louder than a reachable one on purpose -- it is the finding.
REACH_GLYPHS = {
    "walked": ".",
    "jumped": "j",
    "stranded": "!",
    "marooned": "#",
    "void": " ",
}


def _bare(name: str) -> str:
    return name.split("[")[0].removeprefix("minecraft:")


def step_kind(name: str, properties=None) -> str:
    """``air`` (walk through), ``half`` (climb at half a block), ``full``
    (jump onto) or ``tall`` (a fence or a wall: higher than any jump).

    The one rule worth stating twice: **match exactly, or by whole
    suffix or prefix, never by bare substring, and default the unknown to
    solid.** A substring test made ``grass_block`` passable because it
    starts with "grass", and took every ``light_blue_*`` block with it
    because they start with "light" -- so the checker deleted the floor,
    and a walk check that deletes the floor reports every room as
    reachable no matter what is standing in it. A wrong ``full`` raises a
    false alarm somebody reads; a wrong ``air`` hides a wall nobody sees.

    The tables are held to the game's own collision shapes, state by
    state, by ``tests/test_survey.py`` -- and the places this departs from
    them are listed there with the reason for each: a door counts as air
    because you open it, a stair as half because you climb it from the
    low side, lava as solid because nobody should walk into it.
    """
    bare = _bare(name)
    props = properties or {}
    if bare in _SOLID:
        return "full"
    if bare.startswith(_HALF_PREFIX):
        return "half"          # a flower pot, whatever is planted in it
    if bare in _PASSABLE or bare.endswith(_PASSABLE_SUFFIX):
        return "air"
    if bare.endswith("_slab"):
        return "full" if props.get("type") in ("double", "top") else "half"
    if bare.endswith("_stairs"):
        return "full" if props.get("half") == "top" else "half"
    if bare.endswith("_trapdoor"):
        # Open, it is a panel standing in the cell; shut at the top of the
        # cell, its top is a whole block up.
        if props.get("open") == "true" or props.get("half") == "top":
            return "full"
        return "half"
    if bare.endswith("_door") or bare.endswith("_fence_gate"):
        return "air"           # a door is opened, and so is a gate
    if bare.endswith("_fence") or bare.endswith("_wall"):
        return "tall"          # a block and a half: no jump clears it
    if bare.endswith("_pane") or bare.endswith("_bars"):
        return "full"          # a whole block high, and you cannot pass
    if bare.endswith(("_bud", "_cluster")):
        return "half" if props.get("facing") == "up" else "full"
    if bare == "big_dripleaf":
        return "air" if props.get("tilt") == "full" else "full"
    if bare in _LANTERNS:
        return "full" if props.get("hanging") == "true" else "half"
    if (bare.endswith(_HEAD_SUFFIX) and not bare.endswith(_WALL_HEAD_SUFFIX)
            and bare != "piston_head"):
        return "half"
    if bare in _HALF or bare.endswith(_HALF_SUFFIX):
        return "half"
    return "full"


#: The doors a hand cannot open: only power does.
_POWER_DOORS = frozenset(("minecraft:iron_door",))

_UNIT = {word: COMPASS[word] for word in ("north", "south", "west", "east")}
_FACING_OF = {vector: name for name, vector in _UNIT.items()}


def _opener(name: str) -> bool:
    """A button, a lever or a pressure plate: what a hand works."""
    bare = _bare(name)
    return bare == "lever" or bare.endswith(("_button", "_pressure_plate"))


def iron_door_opens(block_at, door, side) -> bool:
    """Whether someone on ``side`` of the iron door can open it.

    ``door`` is the door's lower half, ``(x, y, z)``; ``side`` is the
    horizontal unit vector ``(dx, dz)`` from the door toward the person;
    ``block_at(x, y, z)`` reads a block. The game opens an iron door when
    power reaches either half, and from one side that means:

    * an opener -- a button, a lever, a pressure plate -- standing in a cell
      that touches the door on that side, or
    * an opener on that side attached to a *conductor* that touches the
      door: the wall beside it or the block above it. A button on glass
      sticks fine and opens nothing, because glass does not conduct.

    An open door opens. Anything cleverer -- wire, repeaters, a lever three
    blocks away -- is not seen, and reads as a door that does not open: a
    false alarm, never a hidden wall.
    """
    x, y, z = door
    lower = block_at(x, y, z)
    if lower is not None and lower.properties.get("open") == "true":
        return True
    dx, dz = side
    perp = (dz, dx)
    halves = ((x, y, z), (x, y + 1, z))
    for hx, hy, hz in halves:
        found = block_at(hx + dx, hy, hz + dz)
        if found is not None and _opener(found.name):
            return True
    facing = _FACING_OF.get((dx, dz))
    conductors = [(hx + px, hy, hz + pz) for hx, hy, hz in halves
                  for px, pz in (perp, (-perp[0], -perp[1]))]
    conductors.append((x, y + 2, z))
    for cx, cy, cz in conductors:
        wall = block_at(cx, cy, cz)
        if wall is None or not facts(wall.name, wall.properties).conductor:
            continue
        found = block_at(cx + dx, cy, cz + dz)
        if (found is not None and _opener(found.name)
                and found.properties.get("face", "wall") == "wall"
                and found.properties.get("facing") == facing):
            return True
    return False


class StepSpace:
    """The blocks that are there, with the blocks a build will write on top.

    ``overlay`` maps ``(x, y, z)`` to a block -- a name, a ``BlockState``,
    or ``(name, properties)`` -- and is what makes this answer questions
    about a build that has not happened yet. A plan checked before it is
    written costs nothing to change; the same plan checked afterward costs
    a rollback.
    """

    __slots__ = ("save", "overlay", "dimension", "_cache", "_doors", "_holds")

    def __init__(self, save, *, overlay=None, dimension: str | None = None):
        self.save = save
        self.overlay = read_overlay(overlay)
        self.dimension = dimension
        self._cache: dict[tuple[int, int, int], str] = {}
        self._doors: dict[tuple, bool] = {}
        self._holds: set[tuple[int, int, int]] = set()

    def block(self, x: int, y: int, z: int):
        """The block at one cell, the plan's if it has one."""
        found = self.overlay.get((x, y, z))
        if found is not None:
            return found
        return self.save.get_block(x, y, z, dimension=self.dimension)

    def _through_door(self, cell, move) -> bool:
        """Whether a body stepping by ``move`` into the column whose floor is
        ``cell`` gets past any iron door standing there. A door you came
        through you have already opened, so only the way in is checked."""
        nx, ny, nz = cell
        for y in (ny + 1, ny + 2):
            door = self.block(nx, y, nz)
            if (door is None or door.name not in _POWER_DOORS
                    or door.properties.get("open") == "true"):
                continue
            lower = y - 1 if door.properties.get("half") == "upper" else y
            facing = _UNIT.get(door.properties.get("facing", "north"))
            if facing is None or move not in (facing, (-facing[0], -facing[1])):
                return False         # into the door's side: no way through
            side = (-move[0], -move[1])
            key = (nx, lower, nz, side)
            if key not in self._doors:
                self._doors[key] = iron_door_opens(self.block, (nx, lower, nz), side)
            if not self._doors[key]:
                return False
        return True

    def kind(self, x: int, y: int, z: int) -> str:
        """``air``, ``half`` or ``full`` at one cell."""
        key = (x, y, z)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        if key in self.overlay:
            state = self.overlay[key]
            name = state.name
            out = step_kind(name, state.properties)
        else:
            block = self.save.get_block(x, y, z, dimension=self.dimension)
            name = None if block is None else block.name
            out = ("air" if block is None
                   else step_kind(name, dict(block.properties)))
        if name is not None and _bare(name) in _CLIMBABLE:
            self._holds.add(key)
        self._cache[key] = out
        return out

    def climbable(self, x: int, y: int, z: int) -> bool:
        """Whether a body with its feet in this cell holds on instead of
        falling: the game's ``climbable`` tag, ladders and vines."""
        self.kind(x, y, z)
        return (x, y, z) in self._holds

    def stand_top(self, x: int, y: int, z: int) -> float | None:
        """The height you stand at having landed on the block in cell ``y``,
        or ``None`` if you cannot stand there at all.

        A fence or a wall is never somewhere you stand: it is a block and a
        half high, so nothing beside it at the same level can climb onto
        it, and treating it as a step is how a walk check lets you jump out
        of a pen. A rung is somewhere you hold, whatever stands under it:
        cell ``y`` may be air where ``y + 1`` is a ladder.
        """
        if self.kind(x, y + 1, z) != "air" or self.kind(x, y + 2, z) != "air":
            return None                      # no room for a body above it
        if (self.kind(x, y, z) in ("air", "tall")
                and not self.climbable(x, y + 1, z)):
            return None
        return y + 1.0

    def entry_top(self, x: int, y: int, z: int) -> float:
        """The height you must clear to step onto the block in cell ``y``."""
        return y + {"half": 0.5, "tall": 1.5}.get(self.kind(x, y, z), 1.0)

    def steps(self, cell, box, *, allow_jump: bool = True):
        """Where you can get from ``cell`` in one move, as ``(cell, hopped)``.

        Scanning starts two levels up so that a block at head height in the
        destination column stops the move -- which is what makes a floor
        slab overhanging a stair impassable even though the stair itself is
        intact.

        A body on a rung has two more moves, a row up and a row down, while
        the next cell along still holds it, and neither is a jump. It can
        lift its feet to the top of the cell it holds, head room allowing,
        so a floor level with a ladder's top is a step off it; nobody jumps
        from a ladder, so that is as far as it reaches. A step into a
        ladder's column catches the rung at your own height or below rather
        than falling to its foot.
        """
        (x0, y0, z0), (x1, y1, z1) = box
        cx, cy, cz = cell
        here = self.stand_top(cx, cy, cz)
        if here is None:
            return
        held = self.climbable(cx, cy + 1, cz)
        if held:
            if (cy + 1 <= y1 and self.climbable(cx, cy + 2, cz)
                    and self.stand_top(cx, cy + 1, cz) is not None):
                yield (cx, cy + 1, cz), False
            if (y0 <= cy - 1 and self.climbable(cx, cy, cz)
                    and self.stand_top(cx, cy - 1, cz) is not None):
                yield (cx, cy - 1, cz), False
        feet = here + 1.0 if held and self.kind(cx, cy + 3, cz) == "air" else here
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, nz = cx + dx, cz + dz
            for ny in range(cy + 2, cy - 40, -1):        # a step up, or a fall
                if not (x0 <= nx <= x1 and y0 <= ny <= y1 and z0 <= nz <= z1):
                    continue
                if (self.kind(nx, ny, nz) == "air"
                        and (ny > cy or not self.climbable(nx, ny + 1, nz))):
                    continue
                if self.stand_top(nx, ny, nz) is None:
                    break                    # solid but no head room: blocked
                rise = self.entry_top(nx, ny, nz) - feet
                if rise > (AUTO_STEP if held else JUMP_HEIGHT):
                    break                    # too tall to get onto: blocked
                hopped = rise > AUTO_STEP
                if (not (hopped and not allow_jump)
                        and self._through_door((nx, ny, nz), (dx, dz))):
                    yield (nx, ny, nz), hopped
                break                        # first solid found is the landing


class ReachMap:
    """Where you can get on foot, and -- separately -- where you can get back from.

    Cells are ``(x, y, z)`` of **the block you stand on**, so a floor at
    ``y`` shows up as cells at ``y``. :attr:`walked` got there without
    jumping, :attr:`jumped` needed one somewhere on the way, and
    :attr:`stranded` is the finding: cells you can reach and cannot get
    home from.
    """

    __slots__ = ("start", "box", "reached", "jumped", "stranded", "allow_jump")

    def __init__(self, start, box, reached, jumped, stranded, allow_jump):
        self.start = start
        self.box = box
        self.reached = frozenset(reached)
        self.jumped = frozenset(jumped)
        self.stranded = frozenset(stranded)
        self.allow_jump = allow_jump

    @property
    def walked(self) -> frozenset:
        """Cells reached without jumping anywhere along the way."""
        return self.reached - self.jumped

    def footing(self, y: int) -> set[tuple[int, int]]:
        """The columns you can stand on at one level."""
        return {(x, z) for x, cy, z in self.reached if cy == int(y)}

    def missing(self, columns, y: int) -> list[tuple[int, int]]:
        """Those of ``columns`` you cannot stand on at level ``y``.

        The question a floor plan cannot answer: a room is drawn finished
        whether or not you can get into it, and a bookshelf stack one cell
        from the wall seals the twelve floor cells behind it without
        changing a single line of the picture.
        """
        got = self.footing(y)
        return sorted(c for c in ((int(a), int(b)) for a, b in columns)
                      if c not in got)

    def text(self, y: int, space=None) -> str:
        """One level as a character map -- see :data:`REACH_GLYPHS`.

        ``space`` (the :class:`StepSpace` this was walked over) tells a
        cell nobody can reach from one there is nothing to stand on at
        all, which is the difference between a sealed room and open air.
        """
        y = int(y)
        (x0, _, z0), (x1, _, z1) = self.box
        lines = []
        for z in range(z0, z1 + 1):
            row = []
            for x in range(x0, x1 + 1):
                cell = (x, y, z)
                if cell in self.stranded:
                    row.append(REACH_GLYPHS["stranded"])
                elif cell in self.jumped:
                    row.append(REACH_GLYPHS["jumped"])
                elif cell in self.reached:
                    row.append(REACH_GLYPHS["walked"])
                elif space is not None and space.stand_top(x, y, z) is not None:
                    row.append(REACH_GLYPHS["marooned"])
                else:
                    row.append(REACH_GLYPHS["void"])
            lines.append("".join(row))
        return "\n".join(lines)

    def __repr__(self) -> str:
        parts = [f"{len(self.reached):,} reached"]
        if self.jumped:
            parts.append(f"{len(self.jumped):,} needing a jump")
        parts.append(f"{len(self.stranded):,} one-way"
                     if self.stranded else "round trip everywhere")
        return f"<ReachMap from {self.start} {', '.join(parts)}>"


def reachable(space, start, corner1, corner2, *, overlay=None,
              allow_jump: bool = True, dimension: str | None = None
              ) -> ReachMap:
    """Walk a build the way the player does, and say where you got to.

    ``space`` is a save or a :class:`StepSpace` (pass ``overlay=`` to walk
    a build that has not been written yet); ``start`` is the block you
    stand on to begin -- the doorstep -- and the corners bound the search
    as ``(x, y, z)``, inclusive. Nothing here writes.

    **The round trip is not optional.** Getting somewhere and getting home
    are two questions, and a staircase is exactly where they come apart: a
    slab overhanging a tread is half a block going up and a wall coming
    down. A forward-only search called a seventeen-story tower finished
    -- 2,332 cells walked, none stranded -- when every floor above the
    door was a one-way trip, so this records the moves it made and then
    walks them backwards from ``start``. Whatever cannot get home lands in
    :attr:`ReachMap.stranded`, which is the answer worth reading first.

    The other thing no render shows: a step you walk and a step you jump
    are different, and only the first is a floor plan that works. Pass
    ``allow_jump=False`` to hold the whole build to that.

    A ladder is a way up, and a vine: the game's ``climbable`` tag. A body
    on one is a cell too, the block under its feet, which may be air, so
    :meth:`ReachMap.footing` counts the rungs of the level it is asked
    about; to ask after a floor, ask :meth:`ReachMap.missing` for its
    columns.
    """
    if not isinstance(space, StepSpace):
        space = StepSpace(space, overlay=overlay, dimension=dimension)
    elif overlay:
        raise NBTError("pass overlay= with a save, not with a StepSpace that "
                       "already carries one")
    x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
    y0, y1 = sorted((int(corner1[1]), int(corner2[1])))
    z0, z1 = sorted((int(corner1[2]), int(corner2[2])))
    box = ((x0, y0, z0), (x1, y1, z1))
    start = (int(start[0]), int(start[1]), int(start[2]))
    if not (x0 <= start[0] <= x1 and y0 <= start[1] <= y1
            and z0 <= start[2] <= z1):
        raise NBTError(f"the doorstep {start} is outside the box {box}; a "
                       f"walk that starts outside its own bounds reaches "
                       f"nothing and looks like a sealed building")
    if space.stand_top(*start) is None:
        raise NBTError(
            f"there is nothing to stand on at {start}, or no room above it "
            f"-- give the block your feet are on, not the air you are in")

    forward: dict[tuple[int, int, int], list] = {}
    hopped_here = {start: False}
    queue = deque([start])
    while queue:
        cell = queue.popleft()
        forward[cell] = list(space.steps(cell, box, allow_jump=allow_jump))
        for nxt, hopped in forward[cell]:
            needs = hopped_here[cell] or hopped
            if nxt not in hopped_here or (hopped_here[nxt] and not needs):
                hopped_here[nxt] = needs
                queue.append(nxt)

    # ... and now the same moves, walked backwards from the doorstep. The
    # move relation is not symmetric, so this is a second search and not a
    # rearrangement of the first.
    back: dict[tuple[int, int, int], set] = {}
    for cell, moves in forward.items():
        for nxt, _ in moves:
            back.setdefault(nxt, set()).add(cell)
    homeward = {start}
    queue = deque([start])
    while queue:
        cell = queue.popleft()
        for previous in back.get(cell, ()):
            if previous not in homeward:
                homeward.add(previous)
                queue.append(previous)

    reached = set(hopped_here)
    return ReachMap(start, box, reached,
                    {c for c, j in hopped_here.items() if j},
                    reached - homeward, allow_jump)


# --------------------------------------------------------------------------
# Will the game keep it?
# --------------------------------------------------------------------------

# What each thing that grows will stand on is a block tag of the game's,
# which its block class asks of the block under it. Each table below is a
# copy of one, by bare name, held to the vendored tag by
# ``tests/test_survey.py``, so an update that adds a block to a tag fails
# there by name rather than reporting the new block's plants off their soil.

#: ``supports_vegetation`` (``VegetationBlock.mayPlaceOn``): the
#: ``substrate_overworld`` tag -- dirt, grass, moss and mud and their kin
#: -- plus farmland. What a flower, a sapling or a tuft of grass grows on.
_SOIL = frozenset("""
dirt grass_block podzol coarse_dirt mycelium rooted_dirt moss_block
pale_moss_block mud muddy_mangrove_roots farmland
""".split())
#: The ``sand`` tag.
_SAND = frozenset("sand red_sand suspicious_sand".split())
#: The ``terracotta`` tag: plain terracotta and the sixteen dyed ones. The
#: glazed terracottas are not in it.
_TERRACOTTA = frozenset("""
terracotta white_terracotta orange_terracotta magenta_terracotta
light_blue_terracotta yellow_terracotta lime_terracotta pink_terracotta
gray_terracotta light_gray_terracotta cyan_terracotta purple_terracotta
blue_terracotta brown_terracotta green_terracotta red_terracotta
black_terracotta
""".split())
#: ``supports_azalea`` (``AzaleaBlock.mayPlaceOn``): the soil, and clay.
_AZALEA_SOIL = _SOIL | {"clay"}
#: ``supports_dry_vegetation`` (``DryVegetationBlock.mayPlaceOn``, the
#: dead bush's): the soil, sand and terracotta.
_DRY_SOIL = _SOIL | _SAND | _TERRACOTTA
#: ``supports_sugar_cane`` (``SugarCaneBlock.canSurvive``):
#: ``substrate_overworld`` and sand -- the soil but farmland, which the
#: game does not grow cane on.
_CANE_SOIL = (_SOIL - {"farmland"}) | _SAND
#: The block tag ``supports_sugar_cane_adjacently``: a block that waters
#: sugar cane from beside the block it stands on, as water does by the
#: fluid tag of the same name (see :func:`_wet_beside`). It holds frosted
#: ice, the ice a Frost Walker leaves on water, and never plain ice.
_CANE_WATER = frozenset(("frosted_ice",))

#: Plants that die off their soil. Matched exactly or by whole suffix.
_PLANTS = frozenset("""
short_grass fern tall_grass large_fern dandelion golden_dandelion poppy
blue_orchid allium azure_bluet oxeye_daisy cornflower lily_of_the_valley
torchflower sunflower lilac rose_bush peony pink_petals wildflowers bush
open_eyeblossom closed_eyeblossom sweet_berry_bush
""".split())
_PLANT_SUFFIX = ("_tulip", "_sapling")
#: The azaleas, which grow on the soil or clay.
_AZALEAS = frozenset(("azalea", "flowering_azalea"))


def _plant(bare: str) -> bool:
    """Whether a bare name is a plant that dies off its soil (``_SOIL``):
    every one is a ``VegetationBlock`` that keeps its ``mayPlaceOn``. Never
    one in a flower pot (``potted_red_tulip``, ``potted_oak_sapling``),
    which the game keeps on anything: ``FlowerPotBlock`` asks nothing of
    the block under it. Nor a bamboo shoot, whose suffix is a sapling's
    and whose block is not: ``BambooSaplingBlock`` stands on the
    ``supports_bamboo`` tag (sand, gravel and bamboo, not farmland), which
    nothing here copies, so a shoot is not judged rather than misjudged."""
    return bare in _PLANTS or (bare.endswith(_PLANT_SUFFIX)
                               and not bare.startswith("potted_")
                               and bare != "bamboo_sapling")


def _footing(bare: str) -> frozenset[str] | None:
    """What a thing that grows, named bare, must stand on: the copy of its
    game tag above, or None for a block this module does not hold to a
    footing -- one that asks nothing of the block under it, or asks
    something not copied here (cactus, bamboo, a mushroom, a lily pad, a
    crop). Of a tall plant, only the lower half stands on anything."""
    if _plant(bare):
        return _SOIL
    if bare in _AZALEAS:
        return _AZALEA_SOIL
    if bare == "dead_bush":
        return _DRY_SOIL
    if bare == "sugar_cane":
        return _CANE_SOIL
    return None


def _wet_beside(block_at, x: int, y: int, z: int) -> bool:
    """Whether one of the four blocks beside ``(x, y, z)`` waters sugar
    cane standing on it. ``SugarCaneBlock.canSurvive`` asks each one's
    *fluid* for the fluid tag ``supports_sugar_cane_adjacently``, which is
    ``#water`` -- a flow as much as a source, so seagrass, kelp, a bubble
    column and every waterlogged block count, which is
    :func:`~heaton_voxel.craft.block_facts.holds_water` -- and its block
    for the block tag of the same name, ``_CANE_WATER``. Lava and plain ice
    never do. ``block_at`` reads blocks as the game loads them, an omitted
    ``waterlogged`` at its default, and a name the game has retired as the
    block it became (:data:`~heaton_voxel.craft.blockinfo.RENAMED`), as
    the game's fixer renames it out of an old chunk -- so an old chunk's
    waterlogged ``chain`` waters cane as an ``iron_chain`` does. An id the
    game never had is air to it, whatever it says it holds; nothing read is
    no water."""
    for dx, dz in _UNIT.values():
        beside = block_at(x + dx, y, z + dz)
        if beside is None:
            continue
        name = _current(beside.name)
        if (holds_water(name, beside.properties)
                or _bare(name) in _CANE_WATER):
            return True
    return False

#: What a falling block falls through: the game's ``replaceable`` block
#: tag, which in 26.3 is exactly the blocks ``FallingBlock.isFree`` is
#: true of at every state -- an air, the ``fire`` tag, a ``liquid()``
#: block (water, lava, a bubble column) or one whose properties make it
#: ``canBeReplaced`` -- read off the jar on 2026-10-22 by running
#: ``isFree`` over every state the game has. Kelp is not among them,
#: though it holds water: sand stands on kelp. The tag is not among those
#: ``fixtures/block_shapes.json`` vendors (phase 4's regeneration did not
#: add it), so this is a hand copy, held by ``tests/test_survey.py`` only
#: to its count and to the block list -- a known gap: vendoring the tag
#: would hold it to the game, as the ``supports_*`` tables are held. What
#: falls is the game's own answer, :func:`~heaton_voxel.craft.blockinfo.falls`.
_FALLS_THROUGH = frozenset("""
air water lava short_grass fern dead_bush bush red_shrub short_dry_grass
tall_dry_grass seagrass tall_seagrass fire soul_fire snow vine glow_lichen
resin_clump light tall_grass large_fern structure_void void_air cave_air
bubble_column warped_roots nether_sprouts crimson_roots leaf_litter
hanging_roots
""".split())


def _falls_into(state: BlockState) -> bool:
    """Whether a falling block falls into ``state`` from above: the one
    void-under test, :data:`_FALLS_THROUGH` read as the game reads the
    cell -- an old name as the block it became (``RENAMED``), so sand on a
    pre-1.20.3 ``grass`` falls as it does on ``short_grass``, and an id the
    game does not have as the air it loads it as. The survival audit's
    falls check asks it of the block under a falling one, and the ground
    writers' :func:`~heaton_voxel.craft.build._common._falls_through` of
    the cell under a floor."""
    name = _current(state.name)
    return not known(name) or _bare(name) in _FALLS_THROUGH

_STANDING_TORCHES = frozenset("torch soul_torch redstone_torch copper_torch".split())


class Unsupported:
    """One block the game will not keep, or a door or a chest nobody can open.

    :attr:`kind` names what is wrong -- ``"lone half"``, ``"no footing"``,
    ``"falls"``, ``"off its soil"``, ``"dry cane"``, ``"blocked chest"``,
    ``"sealed door"`` or ``"one-way door"`` -- and only the last is ever
    meant: an exit with a pressure plate on the inside only is a design,
    which is why :meth:`~heaton_voxel.craft.build.Builder.finish` lists it
    and never refuses it, ``strict`` or not. A writer's own pre-write audit
    (``Builder._audit_plan``) is stricter about its own plan: a door's
    fault may not get worse there, so it refuses a plan that leaves a door
    one-way where it opened both ways, or hangs a door that opens from one
    side only.
    """

    __slots__ = ("cell", "block", "kind", "reason")

    def __init__(self, cell, block, kind: str, reason: str):
        self.cell = cell
        self.block = block
        self.kind = kind
        self.reason = reason

    def __str__(self) -> str:
        return f"{self.cell}: {self.block} -- {self.reason}"

    def __repr__(self) -> str:
        return f"<Unsupported {self.kind} at {self.cell}>"


def _chest(bare: str) -> bool:
    """Whether a bare name is a chest: the plain, trapped and ender chests
    and the eight copper ones -- the eleven blocks the game will not open
    under a conductor, by ``ChestBlock.isBlockedChestByBlock`` (which the
    trapped and copper chests inherit) or the ender chest's own copy of
    it. By whole name or whole suffix, never a bare substring."""
    return bare == "chest" or bare.endswith("_chest")


def _fragile(name: str) -> bool:
    """Whether a block can be lost to its surroundings, or shut by them, by
    name: the cheap test that lets a whole section be skipped by its
    palette alone."""
    bare = _bare(name)
    if name in _POWER_DOORS or falls(name) or _chest(bare):
        return True
    if "half" in BLOCK_DEFAULTS.get(name, ()) and BLOCK_DEFAULTS[name]["half"] == "lower":
        return True        # doors and two-block plants
    if bare.endswith(("_bed", "_torch", "_button", "_sign", "_banner", "_carpet",
                      "_pressure_plate")) or _plant(bare):
        return True
    return bare in _STANDING_TORCHES or bare in _AZALEAS or bare in (
        "ladder", "lever", "lantern", "soul_lantern", "sugar_cane",
        "dead_bush") or bare.endswith("_lantern") and "hanging" in BLOCK_DEFAULTS.get(name, ())


def _as_loaded(state: BlockState) -> BlockState:
    """``state`` as the game loads it: a property the state leaves out
    takes its block's default, because the palette codec fills it from the
    default state (``StateDefinition.appendPropertyCodec``, ``orElseGet``).
    So a bare ``wall_torch`` faces north and hangs on the block to its
    south, a bare stone button is on a wall facing north, and a bare iron
    door is a closed lower half facing north. A block the defaults table
    does not name is left as it is."""
    defaults = BLOCK_DEFAULTS.get(state.name)
    if not defaults:
        return state
    props = state.properties
    if all(key in props for key in defaults):
        return state
    return BlockState(state.name, {**defaults, **props})


def _check(block_at, cell, state) -> list[Unsupported]:
    """What, if anything, the game will not keep about one block.
    ``block_at`` reads blocks as the game loads them (:func:`_as_loaded`),
    ``state`` among them."""
    x, y, z = cell
    name, props = state.name, state.properties
    bare = _bare(name)
    out: list[Unsupported] = []

    def fault(kind, reason):
        out.append(Unsupported(cell, state, kind, reason))

    def face(dx, dy, dz, toward, *, center=False, solid=False):
        """Whether the neighbor at the offset holds us on its ``toward`` face."""
        found = block_at(x + dx, y + dy, z + dz)
        if found is None:
            return False
        held = facts(found.name, found.properties)
        return held.solid if solid else held.holds(toward, center=center)

    # Two-block things: a half on its own breaks at the next update.
    if bare.endswith("_bed"):
        unit = _UNIT.get(props.get("facing", "north"), (0, -1))
        sign = 1 if props.get("part", "foot") == "foot" else -1
        other = block_at(x + sign * unit[0], y, z + sign * unit[1])
        want = "head" if sign == 1 else "foot"
        if (other is None or other.name != name
                or other.properties.get("part") != want):
            fault("lone half", f"a bed {props.get('part', 'foot')} with no {want} "
                               f"beside it breaks at the next block update")
    elif BLOCK_DEFAULTS.get(name, {}).get("half") == "lower":
        lower = props.get("half", "lower") == "lower"
        other = block_at(x, y + (1 if lower else -1), z)
        want = "upper" if lower else "lower"
        if other is None or other.name != name or other.properties.get("half") != want:
            fault("lone half", f"its {want} half is missing, so it breaks at "
                               f"the next block update")
        if lower and bare.endswith("_door") and not face(0, -1, 0, "up"):
            fault("no footing", "a door's lower half needs a sturdy block under it")

    # What hangs on, or stands on, something else.
    facing = _UNIT.get(props.get("facing", ""))
    if bare.endswith("_wall_torch") or bare == "wall_torch" or bare == "ladder":
        if facing and not face(-facing[0], 0, -facing[1], props["facing"]):
            fault("no footing", "nothing sturdy behind it to hang on")
    elif bare.endswith("_button") or bare == "lever":
        where = props.get("face", "wall")
        held = (face(0, -1, 0, "up") if where == "floor"
                else face(0, 1, 0, "down") if where == "ceiling"
                else bool(facing) and face(-facing[0], 0, -facing[1], props["facing"]))
        if not held:
            fault("no footing", "nothing sturdy for it to be attached to")
    elif bare in _STANDING_TORCHES:
        if not face(0, -1, 0, "up", center=True):
            fault("no footing", "a torch needs the middle of the block under it")
    elif "hanging" in BLOCK_DEFAULTS.get(name, ()) and bare.endswith("lantern"):
        held = (face(0, 1, 0, "down", center=True) if props.get("hanging") == "true"
                else face(0, -1, 0, "up", center=True))
        if not held:
            fault("no footing", "a lantern needs the middle of what it hangs "
                                "from or stands on")
    elif bare.endswith(("_wall_sign", "_wall_banner")):
        if facing and not face(-facing[0], 0, -facing[1], props["facing"], solid=True):
            fault("no footing", "a wall sign or banner needs a solid block behind it")
    elif (bare.endswith(("_sign", "_banner")) and not bare.endswith("_hanging_sign")):
        if not face(0, -1, 0, "up", solid=True):
            fault("no footing", "it needs a solid block under it")
    elif bare.endswith("_pressure_plate"):
        if not (face(0, -1, 0, "up") or face(0, -1, 0, "up", center=True)):
            fault("no footing", "a pressure plate needs a sturdy block under it")
    elif bare.endswith("_carpet"):
        below = block_at(x, y - 1, z)
        if below is None or below.name in AIR_BLOCKS:
            fault("no footing", "a carpet needs something under it")

    # What falls.
    if falls(name):
        below = block_at(x, y - 1, z)
        if below is not None and _falls_into(below):
            fault("falls", f"it falls through the {below.name} under it at the "
                           f"next block update")

    # What grows: on the footing its game tag names, the lower half of a
    # tall plant only, and sugar cane on cane or with water beside its block.
    soil = _footing(bare)
    if soil is not None and props.get("half", "lower") == "lower":
        below = block_at(x, y - 1, z)
        if bare == "sugar_cane":
            if below is not None and below.name != name:
                if _bare(below.name) not in soil:
                    fault("off its soil", f"sugar cane needs sand or dirt, not {below}")
                elif not _wet_beside(block_at, x, y - 1, z):
                    fault("dry cane", "sugar cane needs water beside the block "
                                      "it is planted in")
        elif below is None or _bare(below.name) not in soil:
            if bare == "dead_bush":
                reason = f"a dead bush needs sand, terracotta or dirt, not {below}"
            elif bare in _AZALEAS:
                reason = f"an azalea needs dirt, grass, moss, mud or clay, not {below}"
            else:
                reason = f"it grows only on dirt, grass and their kin, not on {below}"
            fault("off its soil", reason)

    # Chests nothing can open: the game refuses to open one whose block
    # above conducts power -- the test is isRedstoneConductor, not
    # opacity, so glass, a slab or a carpet over a chest is fine and a
    # block of gravel is not. An id the game lacks is air to it.
    if _chest(bare):
        above = block_at(x, y + 1, z)
        if (above is not None and known(above.name)
                and facts(above.name, above.properties).conductor):
            fault("blocked chest", f"a chest will not open under "
                                   f"{above.name}, a block that conducts "
                                   f"power -- nor will either half of a "
                                   f"double chest it belongs to")

    # Doors nothing can open.
    if name in _POWER_DOORS and props.get("half", "lower") == "lower" and facing:
        sides = [word for word in (props["facing"], _OPPOSITE_WORD[props["facing"]])
                 if iron_door_opens(block_at, cell, _UNIT[word])]
        if not sides:
            fault("sealed door", "nothing opens it from either side: an iron "
                                 "door opens only to power")
        elif len(sides) == 1:
            fault("one-way door", f"it opens only from its {sides[0]} side")
    return out


_OPPOSITE_WORD = OPPOSITE


def unsupported(save, corner1, corner2, *, overlay=None,
                dimension: str | None = None) -> list[Unsupported]:
    """Every block in a box the game will not keep, and every door nothing opens.

    The questions a render never asks:

    * a bed, a door or a tall plant missing its other half, which breaks at
      the next block update;
    * a torch, a ladder, a button, a lever, a lantern, a sign, a banner, a
      pressure plate or a carpet with nothing able to hold it -- by the
      game's own sturdy faces, from :mod:`~heaton_voxel.craft.block_facts`,
      so a button on a fence or a torch on leaves is caught;
    * sand, gravel, concrete powder and anvils over a hole;
    * a flower, a sapling, grass, an azalea, a dead bush or sugar cane off
      the footing its block's game tag names (``supports_vegetation``,
      ``supports_azalea``, ``supports_dry_vegetation``,
      ``supports_sugar_cane``: so cane on farmland and a dead bush on
      glazed terracotta are off it), and sugar cane with no water beside
      its block -- a flow, seagrass, kelp, a bubble column, anything
      waterlogged or frosted ice waters it, as the game reads it;
    * a chest -- any of the eleven, ender and copper chests included --
      under a block that conducts power, which the game will not open
      (``"blocked chest"``; a double chest blocked at either half opens at
      neither, and the half under the conductor is the one reported);
    * an iron door nothing can open from either side -- or from only one,
      which is reported as ``"one-way door"`` and is often meant (``finish``
      never refuses one; a writer's pre-write audit refuses a plan that
      makes one, see :class:`Unsupported`).

    ``overlay`` is a plan, read over the save, so a build is audited before
    it is written. Every block, the plan's and the world's, is judged as
    the game loads it: a property a state leaves out takes its block's
    default, so a bare ``wall_torch`` is one facing north, and hangs on the
    block south of it or on nothing. Sections whose palettes hold nothing
    fragile are skipped unread. Rails are :meth:`~heaton_voxel.craft.build.Railway.verify`'s
    business, and are not looked at here.
    """
    plan = read_overlay(overlay)
    dim = save.dimension(dimension)
    x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
    y0, y1 = sorted((int(corner1[1]), int(corner2[1])))
    z0, z1 = sorted((int(corner1[2]), int(corner2[2])))
    loaded: dict = {}

    def block_at(x, y, z):
        found = plan.get((x, y, z))
        if found is None:
            found = save.get_block(x, y, z, dimension=dimension)
        if found is None:
            return None
        spelled = loaded.get(found)
        if spelled is None:
            spelled = loaded[found] = _as_loaded(found)
        return spelled

    candidates: dict[tuple[int, int, int], object] = {}
    for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
        for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
            accessor = dim.blocks(chunk_x, chunk_z)
            if accessor is None:
                continue
            for section_y in range(y0 >> 4, (y1 >> 4) + 1):
                section = accessor.sections.get(section_y)
                container = section.blocks if section is not None else None
                if container is None:
                    continue
                wanted = {i for i, state in enumerate(container.palette)
                          if _fragile(state.name)}
                if not wanted:
                    continue
                indices = container.indices()
                for index, slot in enumerate(indices):
                    if slot not in wanted:
                        continue
                    x = chunk_x * 16 + (index & 15)
                    z = chunk_z * 16 + ((index >> 4) & 15)
                    y = section_y * 16 + (index >> 8)
                    if x0 <= x <= x1 and y0 <= y <= y1 and z0 <= z <= z1:
                        candidates[(x, y, z)] = container.palette[slot]
    for cell, state in plan.items():
        if (x0 <= cell[0] <= x1 and y0 <= cell[1] <= y1 and z0 <= cell[2] <= z1):
            if _fragile(state.name):
                candidates[cell] = state
            else:
                candidates.pop(cell, None)

    faults: list[Unsupported] = []
    for cell in sorted(candidates):
        state = block_at(*cell)
        if state is not None:
            faults.extend(_check(block_at, cell, state))
    return faults


# --------------------------------------------------------------------------
# Will anything spawn here?
# --------------------------------------------------------------------------

# MONSTER_SPAWN_LIGHT -- the most block light a monster on the game's
# Monster light rule can spawn at, per dimension -- lives in craft.light
# beside NO_SPAWNS, the level one past it, and is imported here: a spawn
# check and a lamp plan read the one table.


class SpawnCell(NamedTuple):
    """A cell a monster can spawn in. ``y`` is the cell its feet stand in,
    one above the floor; ``light`` is the lasting block light there."""

    x: int
    y: int
    z: int
    light: int


#: Blocks with no collision at all, whatever :func:`step_kind` makes of
#: them for a walker: a portal or a fire is solid there because nobody
#: should walk into one, and a moving piston's collision is its block
#: entity's.
_NO_COLLISION = frozenset(
    "end_gateway end_portal nether_portal moving_piston fire soul_fire".split())

#: Blocks a zombie's per-cell test of room refuses as dangerous and a
#: monster on the same Monster light rule may spawn in all the same, with
#: the monster each lets in. ``block_facts``' ``spawn_space`` is the game's
#: ``isValidEmptySpawnBlock`` asked for a zombie, and the game asks it per
#: monster: ``EntityType.isBlockDangerous`` passes whatever is in the
#: monster's ``immuneTo`` tag, and every burning block for a fire-immune
#: one. Of the monsters on the rule only a stray and a wither skeleton have
#: either. None of these has any collision, so the box fits through them.
#: A lit campfire and a lava cauldron pass a wither skeleton's per-cell
#: test too, but stand in the middle of the cell at feet and at head alike,
#: so no monster's box fits beside either and they are left out.
#: ``tests/test_survey.py`` holds every entry to a zombie's refusal, so one
#: the probe starts to pass fails as stale.
_SPAWN_IMMUNE = {
    "minecraft:powder_snow": "a stray is immune to it (stray_immune_to) and "
                             "spawns in it under open sky",
    "minecraft:wither_rose": "a wither skeleton is immune to it "
                             "(wither_skeleton_immune_to)",
    "minecraft:fire": "a wither skeleton is fire-immune, so fire is no "
                      "danger to it",
    "minecraft:soul_fire": "a wither skeleton is fire-immune, so fire is no "
                           "danger to it",
}

_SIDES = tuple(COMPASS)


def _in_the_way(name: str, properties=None) -> bool:
    """Whether a state's collision stands in the middle of its cell, where
    every monster's box is: the 0.6-wide column a zombie stands in, which a
    spider's 1.4-wide box contains.

    :func:`step_kind`, with the places a walker's answer is not a
    monster's: a carpet, a lily pad, lying snow two layers deep and more, a
    shut gate and a hanging sign on a wall are walked through or over and
    still stand in that column; an open trapdoor, a side-facing small
    amethyst bud, the last slice of a cake, a wall with no post and nothing
    to join, the top of a pitcher crop, a portal and a fire leave it clear.
    Scaffolding, which a walker climbs through, stands there whole: the
    game asks for a spawn's room with no entity (``noCollision`` on the
    spawn box), and scaffolding answers that with its whole frame.
    ``tests/test_survey.py`` holds this to the game's collision shapes
    state by state for every state a monster may stand in."""
    bare = _bare(name)
    props = properties or {}
    if bare == "scaffolding":
        return True
    if bare == "pale_moss_carpet":
        return props.get("bottom", "true") == "true"
    if (bare.endswith("_carpet") or bare == "lily_pad"
            or bare.endswith("_wall_hanging_sign")):
        return True
    if bare == "snow":
        return props.get("layers", "1") != "1"
    if bare.endswith("_fence_gate"):
        return props.get("open", "false") != "true"
    if bare in _NO_COLLISION:
        return False
    if bare.endswith("_trapdoor") and props.get("open") == "true":
        return False
    if bare == "cake":
        return props.get("bites", "0") != "6"
    if bare == "pitcher_crop":
        return props.get("half", "lower") == "lower"
    if bare == "small_amethyst_bud":
        return props.get("facing", "up") in ("up", "down")
    if (bare.endswith("_wall") and props.get("up", "true") == "false"
            and all(props.get(side, "none") == "none" for side in _SIDES)):
        return False
    return step_kind(name, props) != "air"


#: What a cell offers a spawn: a floor to stand on, room for the feet, and
#: the game's per-cell test passed, which is all the cell above needs.
_FLOOR, _ROOM, _OPEN = 1, 2, 4

_AIR_STATE = BlockState("minecraft:air")


def _spawn_flags(state: BlockState, cache: dict) -> int:
    found = cache.get(state)
    if found is None:
        read = _game_reads(state)              # a bare id is minecraft's
        name, props = read.name, read.properties
        if not known(name):
            name, props = _AIR_STATE.name, {}   # the game loads it as air
        info = facts(name, props)
        found = 0
        if info.spawn_floor:
            found |= _FLOOR
        if info.spawn_space or name in _SPAWN_IMMUNE:
            found |= _OPEN
            if not _in_the_way(name, props):
                found |= _ROOM
        cache[state] = found
    return found


def spawnable(save, corner1, corner2, *, dimension: str | None = None,
              overlay=None) -> list[SpawnCell]:
    """Every cell in a box a monster can spawn in, by the light that lasts.

    The corners bound the cells a monster's feet would stand in, inclusive.
    A cell is listed when the block under it is a floor a monster stands
    on (the game's ``isValidSpawn``, for a zombie or for a fire-immune
    monster: a sturdy top and, unless the block says otherwise, light
    under 14, so glowstone is no floor and magma is one), it and the cell
    above both pass the game's per-cell test of room
    (``isValidEmptySpawnBlock``: not a whole collision cube, no signal
    source -- a lever, a button, a redstone torch -- no fluid, no rail,
    nothing that burns or hurts the monster), for whichever monster on the
    rule it lets in: powder snow holds a stray, and a wither rose or a fire
    a wither skeleton (:data:`_SPAWN_IMMUNE`). Its own block must leave the
    middle of the cell clear, and its lasting block
    light (:func:`~heaton_voxel.craft.light.simulate_block_light` with
    ``durable=True``) is no more than :data:`MONSTER_SPAWN_LIGHT` allows:
    0 in the overworld and the end, 7 in the nether. ``overlay`` is a plan,
    read over the save, so a room is spawn-proofed before it is built.
    Cells come back in ``(y, z, x)`` order.

    The scope is the monsters on the game's Monster light rule -- zombies,
    skeletons, creepers, spiders, endermen, witches and their kin.
    Drowned are on the rule too, but spawn in water, which this does not
    read; zombified piglins, ghasts, magma cubes, blazes, piglins, slimes
    and phantoms spawn by rules of their own. None of them is answered.

    What is approximated, and which way each errs:

    * **The box.** No one state answers whether a monster's box fits, so
      the feet cell must leave the middle of the cell clear by the game's
      collision shapes (:func:`_in_the_way`, held to them state by state),
      and the cell above needs only the per-cell test: a spider is 0.9
      tall and fits under anything that passes it. A zombie does not fit
      under a top slab at head height, so a spawn listed under one may be
      a spider's -- or nobody's, where walls leave a spider no width, since
      its neighbors are not read: a false alarm, never a missed spawn.
      Nothing reaches up into the feet cell from below, because no block
      taller than a whole one is a floor (held to the game, state by
      state, too).
    * **Which monster.** Each cell is passed for whichever monster it lets
      in, not all of them for the same one, so a stray's powder snow over
      magma only a wither skeleton stands on is listed; a stray needs open
      sky over its powder snow and a wither skeleton, 2.4 tall, a third
      cell clear, and neither is read. Each is a false alarm, never a
      missed spawn.
    * **Light.** Only light that lasts is counted, so a lit furnace or a
      redstone lamp held on by a lever does not spawn-proof a room here:
      a false alarm the other way round from the game's. Light from a
      chunk the world has never generated is not counted either.
    * **Ids.** An id the game does not have is air, as the game loads it,
      and one with no namespace is minecraft's, as the game reads it.

    A chunk inside the box that has never been generated, or that the
    generator set aside part-way, is a refusal -- a
    :class:`~heaton_voxel.craft.errors.Refused` naming every such chunk --
    since nothing can be said about what spawns in ground that is not
    there yet, and "no spawns" must not be the answer for a chunk that
    could not be looked at. A chunk that cannot be read raises its own
    ``NBTError``: that is corruption, not absence.
    """
    dim = save.dimension(dimension)
    limit = MONSTER_SPAWN_LIGHT.get(dim.name)
    if limit is None:
        raise NBTError(
            f"no monster spawn rule is known for dimension {dim.name!r}: its "
            f"dimension type's light rule is not one this reads (known: "
            f"{', '.join(sorted(MONSTER_SPAWN_LIGHT))})")
    plan = read_overlay(overlay)
    x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
    y0, y1 = sorted((int(corner1[1]), int(corner2[1])))
    z0, z1 = sorted((int(corner1[2]), int(corner2[2])))
    lo, hi = y0 - 1, y1 + 1
    w, d = x1 - x0 + 1, z1 - z0 + 1
    cache: dict = {}
    air = _spawn_flags(_AIR_STATE, cache)
    flags = bytearray([air]) * (w * d * (hi - lo + 1))

    def index(x, y, z):
        return ((y - lo) * d + (z - z0)) * w + (x - x0)

    # Every chunk the box reaches, before any is read: one missing or
    # unfinished is a refusal naming all of them, and one that cannot be
    # read is corruption, raised as its own NBTError.
    faults = []
    for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
        for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
            accessor = dim.blocks(chunk_x, chunk_z)   # raises if unreadable
            if accessor is None:
                faults.append(f"chunk ({chunk_x}, {chunk_z}) has never been "
                              f"generated")
            elif not _finished(accessor.status):
                faults.append(f"chunk ({chunk_x}, {chunk_z}) is only partly "
                              f"generated ({accessor.status}): its blocks are "
                              f"not yet the ground they will be")
    if faults:
        count = (f"{len(faults)} chunks cannot be, first: {faults[0]}"
                 if len(faults) > 1 else faults[0])
        raise Refused(f"nothing can be said about what would spawn in ground "
                      f"that is not there yet: {count}", faults=faults)

    for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
        for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
            accessor = dim.blocks(chunk_x, chunk_z)
            base_x, base_z = chunk_x * 16, chunk_z * 16
            xs = range(max(x0, base_x), min(x1, base_x + 15) + 1)
            zs = range(max(z0, base_z), min(z1, base_z + 15) + 1)
            for y in range(lo, hi + 1):
                if not (accessor.min_y <= y <= accessor.max_y):
                    # Outside the world: void air, room for a head and no
                    # floor, and no monster's feet are ever placed there.
                    for z in zs:
                        start = index(xs.start, y, z)
                        flags[start:start + len(xs)] = bytes([_OPEN]) * len(xs)
            for section_y in range(lo >> 4, (hi >> 4) + 1):
                section = accessor.sections.get(section_y)
                container = section.blocks if section is not None else None
                if container is None:
                    continue                    # air, as the flags start
                codes = [_spawn_flags(state, cache) for state in container.palette]
                if all(code == air for code in codes):
                    continue
                indices = container.indices()
                base_y = section_y * 16
                for y in range(max(lo, base_y), min(hi, base_y + 15) + 1):
                    if not (accessor.min_y <= y <= accessor.max_y):
                        continue
                    for z in zs:
                        row = (y - base_y) * 256 + (z - base_z) * 16 - base_x
                        out = index(x0, y, z) - x0
                        for x in xs:
                            flags[out + x] = codes[indices[row + x]]
    for (x, y, z), state in plan.items():
        if x0 <= x <= x1 and lo <= y <= hi and z0 <= z <= z1:
            flags[index(x, y, z)] = _spawn_flags(state, cache)

    field = simulate_block_light(save, (x0, y0, z0), (x1, y1, z1),
                                 dimension=dim.name, overlay=plan, durable=True)
    plane = w * d
    found: list[SpawnCell] = []
    for y in range(y0, y1 + 1):
        for z in range(z0, z1 + 1):
            for x in range(x0, x1 + 1):
                here = index(x, y, z)
                if (flags[here - plane] & _FLOOR and flags[here] & _ROOM
                        and flags[here + plane] & _OPEN):
                    level = field.level(x, y, z)
                    if level <= limit:
                        found.append(SpawnCell(x, y, z, level))
    return found


# --------------------------------------------------------------------------
# Where is the water, and will it stay?
# --------------------------------------------------------------------------

#: The ice water freezes into: ``ServerLevel.tickPrecipitation`` writes
#: ``Blocks.ICE``, and a Frost Walker leaves frosted ice on the water it
#: walks. A body is followed across these and never across packed or blue
#: ice, which freezing never writes -- a body under a packed-ice road ends
#: at the road.
_FREEZE_ICE = frozenset(("minecraft:ice", "minecraft:frosted_ice"))

#: A flood's six steps, in its fixed order: east, west, south, north, up,
#: down.
_STEPS = ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0), (0, -1, 0))

#: Every offset within two steps of a cell (taxicab), the cell included:
#: as far as one cell's first step of spread reads.
_REACH = tuple(sorted(
    (dx, dy, dz)
    for dx in range(-2, 3) for dy in range(-2, 3) for dz in range(-2, 3)
    if abs(dx) + abs(dy) + abs(dz) <= 2))

# Faces of a collision tuple, in block_facts.FACES order.
_DOWN, _UP = 0, 1
#: The four horizontal steps, each with the face it leaves by and the face
#: it arrives on: north, south, west, east.
_FLOW_SIDES = ((0, -1, 2, 3), (0, 1, 3, 2), (-1, 0, 4, 5), (1, 0, 5, 4))

#: A cell's water as one number, ordered as the game ranks it: 0 none,
#: 1..7 a flow of that amount (``level`` 7..1), 8 falling water -- every
#: ``level`` from 8 to 15, which ``LiquidBlock.getFluidState`` reads as
#: ``min(level, 8)`` -- and 9 a source: ``level`` 0, a bubble column, kelp,
#: seagrass, anything waterlogged.
_FALLING, _SOURCE = 8, 9


class _Flow(NamedTuple):
    """What water asks of one block state (``FlowingFluid``'s questions)."""

    #: Its water, as a code above.
    code: int
    #: Whether it is lava.
    lava: bool
    #: Whether water may enter the cell at all (``canHoldAnyFluid``).
    enters: bool
    #: Whether it is a ``LiquidBlockContainer``, which water waterlogs.
    container: bool
    #: Whether, a container, it takes a water source.
    takes_source: bool
    #: ``isSolid``: what turns two sources beside a cell into a third.
    solid: bool
    #: The faces of its collision shape water's wall test sees.
    collision: tuple
    #: Whether the cell is past the world's top or floor: void air, which
    #: water enters and nothing is ever written into.
    void: bool = False


_VOID_FLOW = _Flow(0, False, True, False, False, False, (0,) * 6, True)


def _water_code(name: str, props: dict) -> int:
    """The water code (see ``_SOURCE``) of a state the game has."""
    if name == _PLAIN_WATER:
        try:
            level = int(props.get("level", "0"))
        except (TypeError, ValueError):
            level = 0                       # loaded as the default, a source
        if not 0 <= level <= 15:
            level = 0
        return _SOURCE if level == 0 else 8 - level if level < 8 else _FALLING
    return _SOURCE if holds_water(name, props) else 0


def _flow(state: BlockState, cache: dict) -> _Flow:
    """``state`` as water sees it, the way the game loads it: an id with no
    namespace is minecraft's, a retired name is the block it became, an id
    the game does not have is air, and a property left out takes its
    default."""
    found = cache.get(state)
    if found is None:
        read = _game_reads(state)
        name, props = _current(read.name), read.properties
        if not known(name):
            name, props = "minecraft:air", {}
        info = facts(name, props)
        found = cache[state] = _Flow(
            _water_code(name, props), name == _LAVA, info.enters,
            info.container, info.takes_source, info.solid, info.collision)
    return found


def _open(here: tuple, there: tuple, face: int, back: int) -> bool:
    """``FlowingFluid.canPassThroughWall`` between two neighbors: water
    passes unless the two collision faces they meet on cover the whole
    face between them (``block_facts.FULL_FACE``)."""
    return (here[face] | there[back]) != FULL_FACE


class _WaterWorld:
    """The world as water sees it, through a plan, one chunk at a time.

    A chunk that was never generated, or that the generator set aside
    part-way, is never read: ``missing`` names it, with ``"ungenerated"``
    or ``"partial"``, and its cells read as None -- unknown, never dry. A
    chunk that cannot be read raises its ``NBTError`` (``unreadable="raise"``)
    or is named ``"unreadable"`` beside them. ``codes`` holds the water a
    model has moved into a cell, over the code of the block standing there.
    """

    __slots__ = ("dim", "plan", "unreadable", "chunks", "missing", "flows",
                 "codes", "_dry", "_plan_wet")

    def __init__(self, save, dimension, plan, *, unreadable: str = "raise"):
        self.dim = save.dimension(dimension)
        self.plan = plan
        self.unreadable = unreadable
        self.chunks: dict = {}
        self.missing: dict[tuple[int, int], str] = {}
        self.flows: dict = {}
        self.codes: dict = {}
        self._dry: dict = {}
        self._plan_wet = None

    def section_codes(self, key) -> frozenset:
        """Every water code the world's own blocks in section ``key``,
        ``(chunk_x, section_y, chunk_z)``, may hold, read off its palette
        (0, none, left out). A palette may keep a state no cell holds any
        longer, so a section may read wetter than it is, never drier. A
        chunk not there to read, and a section with no blocks or past the
        world, hold none, as :meth:`flow` reads them."""
        found = self._dry.get(key)
        if found is None:
            accessor = self.accessor(key[0], key[2])
            section = (None if accessor is None
                       else accessor.sections.get(key[1]))
            container = None if section is None else section.blocks
            found = self._dry[key] = frozenset(
                () if container is None else
                (code for state in container.palette
                 if (code := _flow(state, self.flows).code)))
        return found

    def dry_section(self, key) -> bool:
        """Whether no cell of section ``key`` holds water of the world's own
        (:meth:`section_codes`)."""
        return not self.section_codes(key)

    def still_section(self, key) -> bool:
        """Whether no cell of section ``key`` holds flowing or falling water
        of the world's own -- only sources, or none -- which is all a flow
        the game re-reads can be (:meth:`section_codes`)."""
        return all(code == _SOURCE for code in self.section_codes(key))

    def wet_sections(self) -> set:
        """The sections where a cell's water is not the world's own: those
        holding a plan cell that holds water, or a cell a model has moved
        water into. Every other cell's water is its section's to say
        (:meth:`dry_section`)."""
        if self._plan_wet is None:
            self._plan_wet = frozenset(
                (x >> 4, y >> 4, z >> 4) for (x, y, z), state
                in self.plan.items() if _flow(state, self.flows).code)
        return self._plan_wet | {(x >> 4, y >> 4, z >> 4)
                                 for (x, y, z), code in self.codes.items()
                                 if code}

    def accessor(self, chunk_x: int, chunk_z: int):
        key = (chunk_x, chunk_z)
        try:
            return self.chunks[key]
        except KeyError:
            pass
        why = None
        try:
            found = self.dim.blocks(chunk_x, chunk_z)
        except NBTError:
            if self.unreadable == "raise":
                raise
            found, why = None, "unreadable"
        else:
            if found is None:
                why = "ungenerated"
            elif not _finished(found.status):
                found, why = None, "partial"
        if why is not None:
            self.missing[key] = why
        self.chunks[key] = found
        return found

    def state(self, x: int, y: int, z: int):
        """The block at a cell, the plan's first; None where its chunk is
        not there to read, and ``_VOID`` past the world's top or floor."""
        accessor = self.accessor(x >> 4, z >> 4)
        if accessor is None:
            return None
        if not accessor.min_y <= y <= accessor.max_y:
            return _VOID
        found = self.plan.get((x, y, z))
        return found if found is not None else accessor.get(x & 15, y, z & 15)

    def flow(self, x: int, y: int, z: int):
        state = self.state(x, y, z)
        if state is None:
            return None
        if state is _VOID:
            return _VOID_FLOW
        return _flow(state, self.flows)

    def code(self, cell, flow: _Flow) -> int:
        found = self.codes.get(cell)
        return flow.code if found is None else found


#: What :meth:`_WaterWorld.state` answers past the world's top or floor:
#: the game's void air there, which water enters and nothing is ever
#: written into.
_VOID = object()


def _new_liquid(world: _WaterWorld, x: int, y: int, z: int, here: _Flow) -> int:
    """``FlowingFluid.getNewLiquid`` for water at a cell whose block is
    ``here``, as a code: the cell becomes a source where two sources beside
    it reach it through their walls and the block under it is solid or a
    source (``WATER_SOURCE_CONVERSION``, the game rule, taken as on, its
    default); else falling water where water above reaches it; else the
    strongest neighbor that reaches it, less one -- a source and falling
    water count 8, a flow its amount (javap, 26.3)."""
    sources = best = 0
    for dx, dz, face, back in _FLOW_SIDES:
        cell = (x + dx, y, z + dz)
        beside = world.flow(*cell)
        if beside is None:
            continue
        code = world.code(cell, beside)
        if code and _open(here.collision, beside.collision, face, back):
            if code == _SOURCE:
                sources += 1
            best = max(best, min(code, _FALLING))
    if sources >= 2:
        cell = (x, y - 1, z)
        under = world.flow(*cell)
        if under is not None and (under.solid
                                  or world.code(cell, under) == _SOURCE):
            return _SOURCE
    cell = (x, y + 1, z)
    over = world.flow(*cell)
    if (over is not None and world.code(cell, over)
            and _open(here.collision, over.collision, _UP, _DOWN)):
        return _FALLING
    return max(best - 1, 0)


def _first_step(world: _WaterWorld, cell, code: int, here: _Flow):
    """Where water at ``cell`` goes on its next step, as
    ``(target, kind, code)``: every cell ``FlowingFluid.spread`` lets it
    into that holds no water now -- ``"falls"``, ``"spreads"`` or, into a
    container, ``"waterlogs"`` -- with the water it would hold there; every
    lava cell touching it (``"lava"``); and every side it could run to
    whose chunk is not there to read (``"ungenerated"``).

    Down first, and only into a container where two sources around it
    make it one (no container takes flowing water). Then the sides: where
    nothing fell, from a source, or from a flow with no water hole under
    it; where something fell, only with three sources beside it. A flow
    reaches them with one less than its amount, falling water with 7. The
    game then takes only the sides nearest a way down; this reports every
    side its rules let water into, so a side reported may be one the game
    would not feed this tick, and where nothing is reported the game feeds
    nothing."""
    x, y, z = cell
    found = []
    for dx, dy, dz in _STEPS:
        near = (x + dx, y + dy, z + dz)
        flow = world.flow(*near)
        if flow is not None and flow.lava:
            found.append((near, "lava", 0))

    below = (x, y - 1, z)
    under = world.flow(*below)
    under_code = world.code(below, under)
    fell = False
    if (under_code != _SOURCE and under.enters
            and _open(here.collision, under.collision, _DOWN, _UP)):
        new = _new_liquid(world, x, y - 1, z, under)
        if under_code == 0 and (not under.container
                                or (new == _SOURCE and under.takes_source)):
            fell = True
            if not (under.lava or under.void):
                found.append((below, "waterlogs" if under.container
                              else "falls", new))
    if fell:
        sides = sum(1 for dx, dz, _, _ in _FLOW_SIDES
                    if (flow := world.flow(x + dx, y, z + dz)) is not None
                    and world.code((x + dx, y, z + dz), flow) == _SOURCE) >= 3
    else:
        hole = (_open(here.collision, under.collision, _DOWN, _UP)
                and (under_code > 0 or (under.enters and not under.container)))
        sides = code == _SOURCE or not hole
    if not sides:
        return found
    amount = 7 if code == _FALLING else min(code, _FALLING) - 1
    if amount <= 0:
        return found
    for dx, dz, face, back in _FLOW_SIDES:
        if here.collision[face] == FULL_FACE:
            continue
        near = (x + dx, y, z + dz)
        beside = world.flow(*near)
        if beside is None:
            found.append((near, "ungenerated", 0))
            continue
        near_code = world.code(near, beside)
        if (near_code or not beside.enters or beside.lava or beside.void
                or not _open(here.collision, beside.collision, face, back)):
            continue
        new = _new_liquid(world, *near, beside)
        if beside.container and not (new == _SOURCE and beside.takes_source):
            continue
        found.append((near, "waterlogs" if beside.container else "spreads", new))
    return found


def _raise_flows(world: _WaterWorld, seeds) -> set:
    """Raise every flow a plan feeds to what ``getNewLiquid`` gives it
    under the plan, through whole chains of flows: a source set beside a
    stale rim of level-7 water lifts the rim cell beside it to 7, the next
    to 6, and so on, and the cell that reaches air may be several cells
    from the source. A flow keeps the more of what it holds and what it is
    given -- a plan that cuts off a feed is :func:`starved`'s question.
    Returns the cells raised; ``world.codes`` holds what each was raised
    to."""
    work = deque()
    wet = world.wet_sections()
    for x, y, z in sorted(seeds):
        # A seed whose step reaches no section that can hold a flow has no
        # flow beside it to raise -- a sea is sources -- and its section's
        # palette says so.
        if (1 <= x & 15 <= 14 and 1 <= y & 15 <= 14 and 1 <= z & 15 <= 14
                and (key := (x >> 4, y >> 4, z >> 4)) not in wet
                and world.still_section(key)):
            continue
        for dx, dy, dz in ((0, 0, 0),) + _STEPS:
            cell = (x + dx, y + dy, z + dz)
            key = (cell[0] >> 4, cell[1] >> 4, cell[2] >> 4)
            if key not in wet and world.still_section(key):
                continue
            flow = world.flow(*cell)
            if flow is not None and 0 < world.code(cell, flow) < _SOURCE:
                work.append(cell)
    raised = set()
    while work:
        cell = work.popleft()
        flow = world.flow(*cell)
        code = world.code(cell, flow)
        if not 0 < code < _SOURCE:
            continue
        new = _new_liquid(world, *cell, flow)
        if new <= code:
            continue
        world.codes[cell] = new
        raised.add(cell)
        x, y, z = cell
        for dx, dy, dz in _STEPS:
            near = (x + dx, y + dy, z + dz)
            beside = world.flow(*near)
            if beside is not None and 0 < world.code(near, beside) < _SOURCE:
                work.append(near)
    return raised


def _wet_near(world: _WaterWorld, centers) -> set:
    """Every cell within two steps of ``centers`` that holds water. A
    cell is read only where its section can hold water
    (:meth:`_WaterWorld.dry_section`, :meth:`_WaterWorld.wet_sections`):
    elsewhere it holds none, and reading a dry pad's every cell to say so
    is most of what a plan beside no water would cost."""
    out, looked = set(), set()
    wet = world.wet_sections()
    for x, y, z in centers:
        if (2 <= x & 15 <= 13 and 2 <= y & 15 <= 13 and 2 <= z & 15 <= 13
                and (key := (x >> 4, y >> 4, z >> 4)) not in wet
                and world.dry_section(key)):
            continue                    # its whole reach is one dry section
        for dx, dy, dz in _REACH:
            cell = (x + dx, y + dy, z + dz)
            if cell in looked:
                continue
            looked.add(cell)
            key = (cell[0] >> 4, cell[1] >> 4, cell[2] >> 4)
            if key not in wet and world.dry_section(key):
                continue
            flow = world.flow(*cell)
            if flow is not None and world.code(cell, flow):
                out.add(cell)
    return out


def _dry_around(world: _WaterWorld, cells) -> bool:
    """Whether no cell within two steps of ``cells`` holds water, read from
    the section palettes rather than cell by cell: none of the plan's own
    states holds any, and no palette of a section those cells reach holds
    a state that does -- a section of air, stone and dirt cannot. A palette
    may keep a state no cell holds any longer, so a section is read as wet
    where it may not be, never the other way; a chunk not there to read
    holds none, as :meth:`_WaterWorld.flow` reads it, and only the chunks
    the cells reach are read. With nothing wet near a plan, no step of
    water starts anywhere, so :func:`contained` has nothing to follow: this
    is the answer it would give, without the cell-by-cell read a dry pad's
    every cell would cost."""
    if world.wet_sections():
        return False
    sections = set()
    for x, y, z in cells:
        if 2 <= x & 15 <= 13 and 2 <= y & 15 <= 13 and 2 <= z & 15 <= 13:
            sections.add((x >> 4, y >> 4, z >> 4))
            continue
        for dx, dy, dz in _REACH:
            sections.add(((x + dx) >> 4, (y + dy) >> 4, (z + dz) >> 4))
    return all(world.dry_section(key) for key in sorted(sections))


def _leak_steps(world: _WaterWorld, seeds, spill: frozenset,
                into=None) -> dict:
    """``{(target, source): kind}``: every step water takes out of the cells
    a plan touches, as :func:`contained` reads them -- the flows the plan
    feeds raised first, and water let into ``spill`` carried on from there
    with what it holds, falls and all.

    ``into``, with no ``spill``, asks only for the steps into those cells,
    which must be among ``seeds``: a step goes from a cell to one beside
    it, and every cell of water beside a seed is one the whole read would
    step from, so the steps into them are the same, read from only the
    water beside them."""
    raised = _raise_flows(world, seeds)
    if into is not None and not spill:
        near = set()
        for x, y, z in into:
            for dx, dy, dz in _STEPS:
                cell = (x + dx, y + dy, z + dz)
                if cell in near:
                    continue
                flow = world.flow(*cell)
                if flow is not None and world.code(cell, flow):
                    near.add(cell)
        work = deque(sorted(near))
    else:
        work = deque(sorted(_wet_near(world, set(seeds) | raised)))
    queued = set(work)
    found: dict = {}
    while work:
        cell = work.popleft()
        queued.discard(cell)
        here = world.flow(*cell)
        code = world.code(cell, here)
        if not code:
            continue
        for target, kind, new in _first_step(world, cell, code, here):
            if target in spill and kind not in ("lava", "ungenerated"):
                flow = world.flow(*target)
                if new > world.code(target, flow):
                    world.codes[target] = new
                    for near in sorted(_wet_near(world, (target,))):
                        if near not in queued:
                            queued.add(near)
                            work.append(near)
                continue
            found.setdefault((target, cell), kind)
    return found


class Leak(NamedTuple):
    """One way water would get out of a plan.

    :attr:`cell` is where it would go -- the cell it enters, or the lava it
    touches -- and :attr:`block` what stands there (None in a chunk that is
    not there to read); :attr:`source` is the water it comes from. A seal
    goes where the water is, at :attr:`source`.
    """

    #: The cell water would enter, or the lava it touches.
    cell: tuple
    #: What stands at :attr:`cell`, plan first; None where its chunk is not
    #: there to read.
    block: object
    #: ``"falls"``, ``"spreads"``, ``"waterlogs"``, ``"lava"`` or
    #: ``"ungenerated"``.
    kind: str
    #: Why, in words.
    reason: str
    #: The water cell it comes from.
    source: tuple

    def __str__(self) -> str:
        return f"{self.cell}: {self.reason}"


class Containment(NamedTuple):
    """What :func:`contained` found: the leaks a plan makes, and the leaks
    standing already that it neither makes nor mends."""

    #: Every way water gets out that the plan makes, sorted by cell.
    leaks: list
    #: Every way water gets out today that the plan leaves as it is: a
    #: cell some water beside or above it already enters the same way,
    #: and that the plan puts nothing of its own into.
    standing: list

    @property
    def held(self) -> bool:
        """True when the plan lets no water out that is not out already."""
        return not self.leaks


#: A chunk the water cannot be followed into, in words.
_NOT_THERE = {"ungenerated": "never generated",
              "partial": "only partly generated",
              "unreadable": "unreadable"}


def _leak_reason(world: _WaterWorld, cell, kind: str, source, block) -> str:
    if kind == "lava":
        return (f"lava touches the water at {source}, and turns to obsidian, "
                f"cobblestone or stone where water meets it")
    if kind == "ungenerated":
        chunk = (cell[0] >> 4, cell[2] >> 4)
        state = _NOT_THERE[world.missing.get(chunk, "ungenerated")]
        return (f"the water at {source} reaches chunk {chunk}, which is "
                f"{state}: nothing can be said about where it goes there")
    if kind == "waterlogs":
        return (f"two water sources reach it and turn its cell into a third, "
                f"so the water at {source} waterlogs the {block}")
    way = "falls into" if kind == "falls" else "spreads into"
    what = f"the {block}"
    if block is not None and not known(_current(_game_reads(block).name)):
        what += ", which the game loads as air"
    return f"the water at {source} {way} {what}"


def contained(save, overlay, *, spill=(), dimension: str | None = None
              ) -> Containment:
    """Whether a plan keeps its water: every way water gets out of it.

    ``overlay`` is the plan, ``{(x, y, z): block}`` as anywhere else
    (:func:`~heaton_voxel.craft._plan.read_overlay`). The model is the
    game's first step of spread (``FlowingFluid.spread``, javap, 26.3), run
    over every cell holding water within two steps of a plan cell -- as far
    as one cell's step reads -- in the world with the plan laid over it:
    water falls into the cell under it, or spreads to the four beside it,
    wherever the game's rules let it in, and a :class:`Leak` names each
    cell it would enter and the water it comes from. The rules, each read
    off the jar:

    * Water enters a cell only through its wall (``canPassThroughWall``:
      the two collision faces the cells meet on, ``block_facts``'
      ``collision``, do not cover the face between them -- so a
      waterlogged bottom slab lets its water out through the open upper
      half of each side, and a copper grate, whole, does not), and
      only a cell that holds no water and that water can enter at all
      (``canHoldAnyFluid``: a container, or a block washed away --
      ``washed_away_by_fluids`` holds air, plants, torches, rails, carpets
      and snow, and water and lava themselves, which is why "holds no
      water" is asked besides).
    * **Flowing water never waterlogs anything.** A container takes only a
      source (``SimpleWaterloggedBlock.canPlaceLiquid``; never a double
      slab), so a dry stair or fence beside a single source holds the water
      back. But a cell two sources reach through their walls, over a block
      that is solid or a source, becomes a source itself
      (``getNewLiquid``), and a container there is waterlogged
      (``"waterlogs"``). That is the game rule ``WATER_SOURCE_CONVERSION``,
      taken as on, its default: off, a ``"waterlogs"`` is a refusal the
      game would not need.
    * Water goes down first. Where it does, it goes to the sides only with
      three sources beside it; where it does not, a source goes to the
      sides always and a flow only where there is no water hole under it.
      A flow reaches the sides with one less than its amount and falling
      water with 7 -- **falling water is amount 8 at every level from 8 to
      15** (``LiquidBlock.getFluidState``) -- and a flow of 1 goes nowhere.
    * A flow the plan feeds is re-read by the game's own ``getNewLiquid``
      first, and keeps the more of that and what it holds: a source set
      beside a stale rim of level-7 water lifts the whole rim, and a cell
      four along it that the plan never touched spreads into the air
      beside it. Every cell whose water is lifted widens the cells looked
      at by two more steps.
    * Lava touching water anywhere is a leak (``"lava"``): it turns to
      obsidian, cobblestone or stone.
    * A side whose chunk was never generated, or only partly, is a leak
      (``"ungenerated"``): nothing can be said about it. A cell further
      out, read only to count the sources that would make a third, holds
      no water there, since a chunk not there yet holds none. A chunk
      that cannot be read raises its own ``NBTError``.
    * Water on the world's floor falls into the void air under it, as the
      game lets it (``Level.getBlockState`` answers void air there), and
      the write goes nowhere: nothing is reported, and having fallen it
      goes to its sides only beside three sources.

    The game then takes only the sides nearest a way down (``getSpread``,
    where a flow beside a flow can set the nearest without being taken);
    this reports every side its rules let water into, so a side reported
    may be one the game would not feed this tick -- a refusal it would not
    need, never a leak missed. A shape the game offsets by position
    (bamboo, pointed dripstone) is read at the origin, as ``block_facts``
    reads it.

    ``spill`` is world cells water may run into: water let into one is
    carried on with what it would hold there, falls and all, and the
    first cell it reaches outside them is the leak -- a fountain's spout
    over the basin it runs into.

    A leak is the plan's unless water already enters its cell the same
    way without the plan, and the plan put nothing of its own there: one
    the world makes today is in :attr:`Containment.standing` instead, so
    a build beside water that runs somewhere already is not refused for
    it. Lava the plan writes where water runs is still the plan's leak,
    and so is a torch, a rail or a flower it writes there for the water
    to wash away; air written there, or the block standing there written
    back as it is, puts nothing in the water's way. So a plan made of the
    world's own blocks, which changes nothing, has no leaks by
    construction, and what it reports in ``standing`` is exactly where
    water would go from those cells today. An id the game does not have
    is air, as the game loads it, and a plan cell in a chunk not there to
    read, or past the world's top or floor, is a
    :class:`~heaton_voxel.craft.errors.Refused` naming each such chunk and
    the cells past the world.
    """
    plan = read_overlay(overlay)
    spill = frozenset((int(c[0]), int(c[1]), int(c[2])) for c in spill)
    after = _WaterWorld(save, dimension, plan)
    absent = sorted({(x >> 4, z >> 4) for x, _, z in plan
                     if after.accessor(x >> 4, z >> 4) is None})
    faults = [f"chunk {chunk} is {_NOT_THERE[after.missing[chunk]]}"
              for chunk in absent]
    past = sorted(cell for cell in plan if after.state(*cell) is _VOID)
    if past:
        accessor = after.accessor(past[0][0] >> 4, past[0][2] >> 4)
        faults.append(f"{len(past)} plan cells are past the world's "
                      f"{accessor.min_y}..{accessor.max_y}, first {past[0]}"
                      if len(past) > 1 else f"the plan cell {past[0]} is past "
                      f"the world's {accessor.min_y}..{accessor.max_y}")
    if faults:
        more = f", and {len(faults) - 1} more" if len(faults) > 1 else ""
        raise Refused(f"nothing can be said about water where the world is "
                      f"not there: {faults[0]}{more}", faults=faults)
    if _dry_around(after, plan):
        return Containment([], [])
    found = _leak_steps(after, set(plan), spill)
    if not found:
        return Containment([], [])
    before = _WaterWorld(save, dimension, {})
    targets = {target for target, _ in found}
    already = {(target, kind) for (target, _), kind in _leak_steps(
        before, set(plan) | targets, spill, into=targets).items()}
    leaks, standing = [], []
    for (target, source), kind in sorted(found.items()):
        block = after.state(*target)
        if block is _VOID:
            block = None
        leak = Leak(target, block, kind,
                    _leak_reason(after, target, kind, source, block), source)
        mine = (target in plan and not _airy(block)
                and _loads_as(block) != _loads_as(before.state(*target)))
        (standing if (target, kind) in already and not mine
         else leaks).append(leak)
    return Containment(leaks, standing)


def starved(save, removed, *, overlay=None, dimension: str | None = None
            ) -> list[tuple[int, int, int]]:
    """Every cell of flowing water that loses its feed when ``removed`` goes.

    ``removed`` is the water cells a plan takes away; each reads as
    ``overlay`` has it, or, where the overlay says nothing, as solid
    ground. Every flow and every column of falling water beside what is
    left is then re-read by the game's own ``getNewLiquid`` -- the one
    amount model :func:`contained` reads with: a source and falling water
    count 8, a flow its amount, each only through its wall -- and wherever
    that gives less than the cell holds, the cell is starved, and the flows
    it fed are read again in turn, down to whatever else feeds them or to
    nothing. A source never is: the game never re-reads one. Returns the
    starved cells, sorted: water that will weaken or dry where it stands,
    which is a dam's damage that no picture shows.
    """
    plan = read_overlay(overlay)
    gone = {(int(c[0]), int(c[1]), int(c[2])) for c in removed}
    world = _WaterWorld(save, dimension,
                        {**{cell: _FILLED for cell in gone}, **plan})
    for cell in gone:
        world.codes[cell] = 0
    work = deque()
    for x, y, z in sorted(gone):
        for dx, dy, dz in _STEPS:
            cell = (x + dx, y + dy, z + dz)
            if cell in gone:
                continue
            flow = world.flow(*cell)
            if flow is not None and 0 < world.code(cell, flow) < _SOURCE:
                work.append(cell)
    starved_cells = set()
    while work:
        cell = work.popleft()
        flow = world.flow(*cell)
        code = world.code(cell, flow)
        if not 0 < code < _SOURCE:
            continue
        new = _new_liquid(world, *cell, flow)
        if new >= code:
            continue
        world.codes[cell] = new
        starved_cells.add(cell)
        x, y, z = cell
        for dx, dy, dz in _STEPS:
            near = (x + dx, y + dy, z + dz)
            beside = world.flow(*near)
            if (near not in gone and beside is not None
                    and 0 < world.code(near, beside) < _SOURCE):
                work.append(near)
    return sorted(starved_cells)


#: What a removed water cell reads as where the plan says nothing: solid
#: ground, a wall to water on every face.
_FILLED = BlockState("minecraft:stone")


class WaterBody:
    """One body of water: every cell joined to a seed through cells that
    hold water, and the ice frozen on it. Built by :func:`water_body`.

    :attr:`cells` holds water -- water at any level, a bubble column, kelp,
    seagrass, anything waterlogged -- and :attr:`ice` the freeze ice on it,
    crossed but not counted as water. :attr:`columns` reads each column
    the body stands in with the one column reader, and is read the first
    time it is asked for.
    """

    __slots__ = ("seed", "surface", "cells", "flowing", "ice", "lids",
                 "shore", "clipped", "missing", "_tops", "_dim", "_columns")

    def __init__(self, seed, surface, cells, flowing, ice, lids, shore,
                 clipped, missing, tops, dim):
        #: The seed cell, ``(x, y, z)``.
        self.seed = seed
        #: The y of the seed column's waterline: the top of the body over
        #: the seed -- its ice, where it is frozen.
        self.surface = surface
        #: Every cell that holds water, as a frozenset of ``(x, y, z)``.
        self.cells = cells
        #: The cells of ``minecraft:water`` that are not sources: flowing
        #: water and falling water, which a game tick moves.
        self.flowing = flowing
        #: The freeze ice on the body: ``minecraft:ice`` and frosted ice
        #: lying on it, beside it, or beside ice that is, at its own y.
        self.ice = ice
        #: The ice with air directly under it: a lid over a room or a cave,
        #: which light melts at 11 into whatever is below.
        self.lids = lids
        #: The dry columns beside the body: every ``(x, z)`` 4-neighbor of a
        #: column it stands in that it does not stand in, outside the chunks
        #: that are not there to read and the columns it was cut off at.
        self.shore = shore
        #: Cells past ``within=`` that the flood would have entered.
        self.clipped = clipped
        #: ``{(chunk_x, chunk_z): why}`` for every chunk the flood reached
        #: and could not read: ``"ungenerated"``, ``"partial"`` or
        #: ``"unreadable"``. Could not read is never dry.
        self.missing = missing
        self._tops = tops
        self._dim = dim
        self._columns = None

    @property
    def columns(self) -> dict:
        """``{(x, z): SurfaceProbe | None}`` for every column the body
        stands in, in ``(z, x)`` order, each read by the column reader
        (:func:`walk_surface`'s, with ``below=`` the column's top body or
        ice cell) from the world as it stands: the waterline, the depth in
        :attr:`SurfaceProbe.water`, the bed (``y``, ``bed_y``, ``block``),
        the ice and whether it is a lid. None where nothing is under the
        water. Read once, the first time it is asked for."""
        if self._columns is None:
            found = {}
            for x, z in sorted(self._tops, key=lambda c: (c[1], c[0])):
                probe = _read_column(self._dim, x, z, below=self._tops[(x, z)])
                found[(x, z)] = probe if isinstance(probe, SurfaceProbe) else None
            self._columns = found
        return self._columns

    @property
    def still(self) -> bool:
        """True when nothing in the body flows or falls."""
        return not self.flowing

    @property
    def depth(self) -> dict:
        """``{(x, z): cells of water}`` over :attr:`columns`."""
        return {column: probe.water for column, probe in self.columns.items()
                if probe is not None}

    def __len__(self) -> int:
        return len(self.cells)

    def __repr__(self) -> str:
        parts = [f"{len(self.cells)} cells", f"{len(self._tops)} columns",
                 f"surface {self.surface}"]
        if self.flowing:
            parts.append(f"{len(self.flowing)} flowing")
        if self.ice:
            parts.append(f"{len(self.ice)} ice"
                         + (f" ({len(self.lids)} lids)" if self.lids else ""))
        if self.clipped:
            parts.append(f"{len(self.clipped)} clipped")
        if self.missing:
            parts.append(f"{len(self.missing)} chunks not read")
        return f"<WaterBody at {self.seed}: {', '.join(parts)}>"


def water_body(save, seed, *, within=None, overlay=None,
               dimension: str | None = None, limit: int = 500_000) -> WaterBody:
    """The body of water at ``seed``: every cell joined to it through cells
    that hold water, and the ice frozen on it. Read-only.

    ``seed`` is a cell, ``(x, y, z)``, holding water or freeze ice, or a
    column, ``(x, z)``, meaning its waterline as :func:`walk_surface` reads
    it -- the top water cell, or the ice where it is frozen. A seed that
    holds neither is an ``NBTError`` naming the cell and its block, or the
    world's heights where it is past them.

    The flood is breadth first over the six neighbors of each cell, in a
    fixed order. A cell joins :attr:`WaterBody.cells` when it holds water
    as the game loads it (``block_facts.holds_water``: water at any level,
    a bubble column, kelp, seagrass, anything waterlogged). Freeze ice --
    ``minecraft:ice`` and frosted ice, never packed or blue, which freezing
    never writes -- joins :attr:`WaterBody.ice` where it lies on a cell of
    the body, or beside one, or beside ice that has joined at its own y: a
    lid on the same lake, crossed and not counted as water. The flood is
    3-D, so pools joined under their surfaces are one body: a moat beside a
    lake that meets it under the waterline is part of the lake, and a lake
    that drains down a shaft takes the shaft with it. A caller that wants
    one surface filters the columns by waterline and passes ``within=``.

    ``within`` is a rectangle, ``((x0, z0), (x1, z1))`` (a corner may carry
    a y, which is ignored): the flood never leaves it, and every cell past
    it that it would have entered is in :attr:`WaterBody.clipped`.
    ``overlay`` is a plan read over the world, as anywhere else -- the
    removed cells of a fill as solid ground, say -- though
    :attr:`WaterBody.columns` reads the world as it stands. A chunk never
    generated, set aside part-way or that cannot be read is never entered,
    and :attr:`WaterBody.missing` names it: could not read is never dry.
    Past ``limit`` cells of water and ice the flood is a
    :class:`~heaton_voxel.craft.errors.Refused`, since a body that size is
    a sea: pass ``within=`` to bound it.
    """
    plan = read_overlay(overlay)
    world = _WaterWorld(save, dimension, plan, unreadable="record")
    if len(seed) == 2:
        x, z = int(seed[0]), int(seed[1])
        probe = walk_surface(save, x, z, dimension=dimension)
        if probe is None or not probe.wet:
            what = "nothing to stand on" if probe is None else probe.block
            raise NBTError(f"no water at column ({x}, {z}) to seed a body "
                           f"from: its surface is {what}")
        start = (x, probe.waterline, z)
    else:
        start = (int(seed[0]), int(seed[1]), int(seed[2]))
    if within is None:
        box = None
    else:
        (ax, az), (bx, bz) = ((int(c[0]), int(c[-1])) for c in within)
        box = (min(ax, bx), min(az, bz), max(ax, bx), max(az, bz))
        if not (box[0] <= start[0] <= box[2] and box[1] <= start[2] <= box[3]):
            raise NBTError(f"the seed {start} is outside within={within!r}, "
                           f"which the flood never leaves")
    state = world.state(*start)
    if state is None:
        chunk = (start[0] >> 4, start[2] >> 4)
        raise NBTError(f"no water at {start} to seed a body from: chunk "
                       f"{chunk} is {_NOT_THERE[world.missing[chunk]]}")

    looked: dict = {}

    def kind(cell):
        found = looked.get(cell, _UNSEEN)
        if found is _UNSEEN:
            here = world.state(*cell)
            if here is None or here is _VOID:
                found = None
            elif _flow(here, world.flows).code:
                found = "water"
            elif _current(_game_reads(here).name) in _FREEZE_ICE:
                found = "ice"
            else:
                found = None
            looked[cell] = found
        return found

    if state is _VOID:
        accessor = world.accessor(start[0] >> 4, start[2] >> 4)
        raise NBTError(f"no water at {start} to seed a body from: it is past "
                       f"the world's {accessor.min_y}..{accessor.max_y}")
    first = kind(start)
    if first is None:
        raise NBTError(f"no water at {start} to seed a body from: it is "
                       f"{state}, which holds no water and is no ice "
                       f"frozen on any")
    cells, ice, clipped = set(), set(), set()
    (cells if first == "water" else ice).add(start)
    joined = {start}
    work = deque([start])
    while work:
        cell = work.popleft()
        on_ice = cell in ice
        x, y, z = cell
        for dx, dy, dz in _STEPS:
            near = (x + dx, y + dy, z + dz)
            if near in joined:
                continue
            found = kind(near)
            if found is None:
                continue
            # Ice joins lying on the body, beside it, or beside joined ice
            # at its own y; never under water, nor stacked on ice.
            if found == "ice" and not (dy == 0 or (dy == 1 and not on_ice)):
                continue
            joined.add(near)
            if box is not None and not (box[0] <= near[0] <= box[2]
                                        and box[1] <= near[2] <= box[3]):
                clipped.add(near)
                continue
            (cells if found == "water" else ice).add(near)
            if len(cells) + len(ice) > limit:
                raise Refused(
                    f"the water at {start} runs on past {limit:,} cells of "
                    f"water and ice without ending: pass within= to bound "
                    f"it, or a larger limit=")
            work.append(near)

    tops: dict = {}
    for x, y, z in cells | ice:
        if tops.get((x, z), y - 1) < y:
            tops[(x, z)] = y
    surface = start[1]
    while (start[0], surface + 1, start[2]) in cells or (
            start[0], surface + 1, start[2]) in ice:
        surface += 1
    flowing = frozenset(cell for cell in cells
                        if 0 < world.code(cell, world.flow(*cell)) < _SOURCE)
    lids = frozenset(cell for cell in ice if _airy(
        world.state(cell[0], cell[1] - 1, cell[2])))
    cut = {(x, z) for x, _, z in clipped}
    shore = set()
    for x, z in tops:
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            column = (x + dx, z + dz)
            if (column not in tops and column not in cut
                    and (column[0] >> 4, column[1] >> 4) not in world.missing):
                shore.add(column)
    return WaterBody(start, surface, frozenset(cells), flowing,
                     frozenset(ice), lids, frozenset(shore),
                     frozenset(clipped), dict(sorted(world.missing.items())),
                     tops, world.dim)


_UNSEEN = object()


def _loads_as(state):
    """``state`` as the game loads it, to tell whether a plan changes what
    stands in a cell: under the id the game reads (no namespace is
    minecraft's, a retired name the block it became), an id it does not
    have as air, and every property left out at its default. None and
    ``_VOID`` are themselves."""
    if state is None or state is _VOID:
        return state
    read = _game_reads(state)
    name = _current(read.name)
    if not known(name):
        return _AIR_STATE
    return _as_loaded(BlockState(name, read.properties))


def _airy(state) -> bool:
    """Whether a block read for water is air as the game loads it: any of
    its three airs, or an id it does not have. Past the world, and in a
    chunk not there to read, it is not."""
    if state is None or state is _VOID:
        return False
    name = _current(_game_reads(state).name)
    return name in AIR_BLOCKS or not known(name)
