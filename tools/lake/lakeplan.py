"""The whole lake job, decided before a single block is written.

A build this size is not a script, it is a *plan*: one pass that reads
the world and settles every cell it will touch, and a second pass that
does nothing but apply it. Keeping them apart is what makes the job
checkable -- the plan can be counted, drawn and argued with while the
world is still untouched, and the writer has no judgment left in it to
get wrong.

What the plan decides, in order:

``mask``
    Which columns are lake. Not simply "where is there water at y=62" --
    the rim today is *flowing* water spilling over ground a block or two
    below the surface, so the visible edge is ragged and a column can be
    wet without reaching the waterline. The lake is every **wet** column
    connected to it: one holding water somewhere between its ground and
    y=62. Wet, and only wet -- an earlier version claimed any column
    merely *open* at y=62 and walked seven blocks inland across dry
    meadow and over a ruin's floor, which is how a rule that sounds like
    "fill the lake to its own level" quietly becomes "enlarge the lake".

``lip``
    The dry columns the lake would then spill into: open at the
    waterline, next to the water, ground below it. Each is filled with
    sand up to y=62, which is both the innermost course of the beach and
    the thing that makes the containment promise true -- afterwards no
    column beside the lake is open at the waterline, so the water cannot
    spread a single block further than planned.

``floor``
    Depth is ``slope x distance-to-shore``, blurred and rippled. Distance
    to shore is 1-Lipschitz, so scaling it by the slope bounds every
    neighbor step by the slope and nothing steeper can exist: that
    identity is the whole design, and "as deep as the smooth descents
    will carry it" is not a wish but a consequence of it. See
    :mod:`field` for the transform and the blur.

``bed``
    What the floor is made of, graded by depth through sand, gravel and
    stone with clay pockets, boundaries broken up by noise so no ring
    shows.

``beach``
    Land within three blocks of the water, re-topped as sand -- but only
    where the ground is natural, is not a cliff, and will not leave sand
    hanging over air.

``plants``
    Seagrass on the shallows, kelp forests on the slopes, sea pickles on
    sunlit sand.

Nothing here writes to the world.
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections import Counter, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from heaton_voxel.nbt import NBTError                 # noqa: E402
from heaton_voxel.world import MinecraftSave          # noqa: E402
from lakelib import WORLD, WATER, AIR, ICE, names     # noqa: E402
from noise import fbm, unit                           # noqa: E402
from field import blur, edt                         # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

LEVEL = 62                      # top water block of the lake
SEED_XZ = (-599, -53)           # the player's island; the lake around it
BOUNDS = (-880, -260, -440, 110)

SLOPE = 0.75
BLUR_PASSES, BLUR_RADIUS = 3, 2
WAVE_AMP = 1.0
MIN_DEPTH = 1
FLOOR_LIMIT_Y = 20

BEACH_WIDTH = 3                 # blocks of sand outward from the waterline
BEACH_DEPTH = 3                 # blocks of sand downward into the ground
BEACH_MAX_RISE = 4              # above +4 it is a bluff, not a beach
LIP_MAX_RISE = 12               # deeper than this and it is a shaft, not a lip

#: Boxes nothing may be written into, as ``(x0, z0, x1, z1)``. The
#: fortress's moat is a *separate* water body at the same level as the
#: lake, and at its closest the two are two blocks apart -- a three-wide
#: beach laid without this would put sand in someone's moat.
PROTECT = {
    "fortress-moat": (-561, -5, -498, 58),
}

# --------------------------------------------------------------------------
# Block vocabulary
# --------------------------------------------------------------------------

WATER_SOURCE = "minecraft:water[level=0]"

#: Ground the build is allowed to reshape. Everything else -- a wall, a
#: path, a rail, a ruined portal's blackstone -- is left exactly as it
#: is, which is how the fortress, the blackstone keep, the villages and
#: the nether-brick wall stay safe without the build having to know
#: where any of them are. A whitelist, not a blacklist, because the
#: blacklist is the one that is wrong the day somebody builds out of
#: something nobody thought to list.
NATURAL = frozenset((
    "minecraft:grass_block", "minecraft:dirt", "minecraft:coarse_dirt",
    "minecraft:rooted_dirt", "minecraft:podzol", "minecraft:mycelium",
    "minecraft:sand", "minecraft:red_sand", "minecraft:gravel",
    "minecraft:stone", "minecraft:andesite", "minecraft:diorite",
    "minecraft:granite", "minecraft:tuff", "minecraft:deepslate",
    "minecraft:clay", "minecraft:mud", "minecraft:sandstone",
    "minecraft:red_sandstone", "minecraft:moss_block", "minecraft:calcite",
    "minecraft:packed_mud", "minecraft:snow_block", "minecraft:obsidian",
    "minecraft:coal_ore", "minecraft:iron_ore", "minecraft:copper_ore",
    "minecraft:lapis_ore", "minecraft:gold_ore", "minecraft:redstone_ore",
    "minecraft:magma_block", "minecraft:terracotta",
))

#: Cobblestone is the ambiguous one. Under the lake it is natural -- a
#: lava pocket met the water and made it, and there are 245 blocks of it
#: on the bed at x -627..-601. On land beside this lake it is a minecart
#: causeway's deck. So it is bed material and never beach material; the
#: distinction is cheaper than a protected rectangle and does not need
#: to know where the causeway runs.
NATURAL_BED = NATURAL | {"minecraft:cobblestone", "minecraft:mossy_cobblestone"}

#: Ground cover that cannot stand on sand, and so must go when the ground
#: under it becomes sand -- otherwise the game pops it off on its next
#: update and the beach is littered with dropped items.
SOFT = frozenset((
    "minecraft:short_grass", "minecraft:tall_grass", "minecraft:fern",
    "minecraft:large_fern", "minecraft:snow", "minecraft:leaf_litter",
    "minecraft:dandelion", "minecraft:poppy", "minecraft:blue_orchid",
    "minecraft:allium", "minecraft:azure_bluet", "minecraft:oxeye_daisy",
    "minecraft:cornflower", "minecraft:lily_of_the_valley", "minecraft:torchflower",
    "minecraft:red_tulip", "minecraft:orange_tulip", "minecraft:white_tulip",
    "minecraft:pink_tulip", "minecraft:sweet_berry_bush", "minecraft:moss_carpet",
    "minecraft:pink_petals", "minecraft:wildflowers", "minecraft:bush",
    "minecraft:firefly_bush", "minecraft:sunflower", "minecraft:lilac",
    "minecraft:rose_bush", "minecraft:peony", "minecraft:brown_mushroom",
    "minecraft:red_mushroom", "minecraft:azalea", "minecraft:flowering_azalea",
    "minecraft:short_dry_grass", "minecraft:tall_dry_grass", "minecraft:cactus",
    "minecraft:seagrass", "minecraft:tall_seagrass", "minecraft:kelp",
    "minecraft:kelp_plant", "minecraft:sea_pickle", "minecraft:lily_pad",
    "minecraft:dead_bush", "minecraft:sugar_cane", "minecraft:sapling",
))

#: Of those, the ones sand holds up perfectly well. Clearing these was
#: worse than leaving them: sugar cane is *three* blocks tall, so a
#: two-cell clear left the third stalk floating -- the exact litter the
#: clearing was for -- and cane, cactus and dead bush all stand on sand
#: anyway. They keep their footing; only what needs dirt is taken.
SAND_SAFE = frozenset((
    "minecraft:sugar_cane", "minecraft:cactus", "minecraft:dead_bush",
    "minecraft:short_dry_grass", "minecraft:tall_dry_grass",
))
CLEARABLE = SOFT - SAND_SAFE

#: Blocks a column may be *walked down through* to find its real ground.
#: A canopy is not the surface, and neither is the trunk standing on it.
CANOPY = ("leaves", "_log", "_wood", "_stem", "_hyphae", "vine", "hanging_roots",
          "mangrove_roots", "moss_carpet", "cocoa", "propagule", "sapling",
          "azalea", "glow_lichen", "snow")

SUBSTRATE = {
    "minecraft:obsidian": "minecraft:stone",
    "minecraft:sand": "minecraft:sandstone",
    "minecraft:gravel": "minecraft:stone",
    "minecraft:clay": "minecraft:stone",
    "minecraft:stone": "minecraft:stone",
}


# --------------------------------------------------------------------------
# Reading the world
# --------------------------------------------------------------------------

def _protected(x: int, z: int) -> str | None:
    for name, (a, b, c, d) in PROTECT.items():
        if a <= x <= c and b <= z <= d:
            return name
    return None


def _void_now(save, x: int, y: int, z: int) -> bool:
    """Would a falling block dropped here keep falling?

    Air, water and lava all let sand and gravel through. Testing only
    for air is the mistake that lays a beach on the bottom of the pond
    it was meant to edge.
    """
    n = names(save, x, y, z)
    return n is not None and (n in AIR or n in WATER or n == "minecraft:lava")


def _open_at_level(name: str | None) -> bool:
    """Would the lake, standing at y=62, occupy this cell?

    Air and water obviously. Also anything soft: a flower or a grass tuft
    at the waterline is not a shore, it is a wet edge the lake has not
    been told it owns.
    """
    if name is None:
        return False
    return name in AIR or name in WATER or name in SOFT


#: Blocks that *are* water without being named water. Seagrass and kelp
#: carry the fluid themselves -- a seagrass block at the waterline is
#: open lake, not a dry hollow -- and a wetness test that only looks for
#: ``minecraft:water`` punches a hole in the mask everywhere a plant
#: happens to reach the surface. Forty-seven of them, on this lake.
WET_PLANTS = frozenset((
    "minecraft:seagrass", "minecraft:tall_seagrass",
    "minecraft:kelp", "minecraft:kelp_plant", "minecraft:sea_pickle",
    "minecraft:bubble_column",
))


def is_wet(save, x: int, z: int, dim) -> bool:
    """Does this column hold any water between its ground and y=62?

    The test that separates the lake from the field beside it. "Open at
    the waterline" is not enough: a hollow in a meadow is open at y=62
    too, and claiming it turns a lake job into a flooding job.
    """
    y = LEVEL
    while y > LEVEL - 24:
        n = names(save, x, y, z)
        if n is None:
            return False
        if n in WATER or n in ICE or n in WET_PLANTS:
            return True
        if not (n in AIR or n in SOFT or any(k in n for k in CANOPY)):
            return False
        y -= 1
    return False


def build_mask(save, dim):
    """``(lake, lip, held)``: the wet columns, and the dry ones beside them.

    ``lake`` is every wet column connected to the player's lake and open
    at the waterline. ``lip`` is the columns the lake would spill into
    once its rim is made of source blocks -- dry, open at y=62, next to
    the water. They are not lake and they are not shore; they are the
    course of sand that makes the difference.
    """
    seeds = []
    px, pz = SEED_XZ
    for r in range(1, 40):
        for dx in range(-r, r + 1):
            for dz in (-r, r):
                if names(save, px + dx, LEVEL, pz + dz) in WATER:
                    seeds.append((px + dx, pz + dz))
        if seeds:
            break
    if not seeds:
        raise NBTError(f"no water at y={LEVEL} near {SEED_XZ}")

    lake: set[tuple[int, int]] = set()
    lip: set[tuple[int, int]] = set()
    q = deque(seeds)
    seen = set(seeds)
    blocked_by_zone = set()
    while q:
        x, z = q.popleft()
        n = names(save, x, LEVEL, z)
        if not (n in ICE or _open_at_level(n)):
            continue                 # solid at the waterline: this is shore
        if not is_wet(save, x, z, dim):
            lip.add((x, z))          # open, but dry: the lake stops here
            continue
        lake.add((x, z))
        if len(lake) > 200_000:
            raise NBTError("lake flood fill ran away; it is not closed")
        for nx, nz in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
            if (nx, nz) in seen:
                continue
            seen.add((nx, nz))
            if not (BOUNDS[0] <= nx <= BOUNDS[2] and BOUNDS[1] <= nz <= BOUNDS[3]):
                raise NBTError(f"the lake reached the search bound at ({nx}, {nz})")
            if _protected(nx, nz):
                blocked_by_zone.add((nx, nz))
                continue
            if dim.chunk(nx >> 4, nz >> 4) is None:
                continue
            q.append((nx, nz))
    lip -= lake
    return lake, lip, blocked_by_zone


def ground_y(save, x: int, z: int, dim, *, start: int | None = None):
    """The top of the real ground in a column: canopy, plants, snow and
    standing water all stepped through."""
    y = start
    if y is None:
        y = dim.surface_y(x, z, "WORLD_SURFACE")
        if y is None:
            return None, None
    while y > -64:
        n = names(save, x, y, z)
        if n is None:
            return None, None
        if (n in AIR or n in WATER or n in SOFT
                or any(k in n for k in CANOPY)):
            y -= 1
            continue
        return y, n
    return None, None


# --------------------------------------------------------------------------
# The floor
# --------------------------------------------------------------------------

def _wave(x: int, z: int) -> float:
    return WAVE_AMP * 0.5 * (
        math.sin(x / 37.0) * math.cos(z / 23.0)
        + math.sin((x + z) / 59.0) * math.cos((x - z) / 39.0))


def build_floor(lake, slope=SLOPE):
    """``(dist, target)``: distance to shore, and the y the floor wants.

    The grid is padded. Both the distance transform and the blur measure
    against cells *inside* the grid, so a lake column sitting on the
    bounding box's edge would have no shore on that side: its distance
    would be overstated and the bed cut deeper right against the bank.
    A margin of guaranteed-dry cells gives every side a real shore.
    """
    pad = BLUR_RADIUS + 2
    xs = [x for x, _ in lake]
    zs = [z for _, z in lake]
    x0, x1 = min(xs) - pad, max(xs) + pad
    z0, z1 = min(zs) - pad, max(zs) + pad
    w, h = x1 - x0 + 1, z1 - z0 + 1
    flat = [False] * (w * h)
    for x, z in lake:
        flat[(z - z0) * w + (x - x0)] = True

    d2 = edt(flat, w, h)
    dist = {}
    raw = [0.0] * (w * h)
    for j in range(h):
        for i in range(w):
            k = j * w + i
            if flat[k]:
                d = math.sqrt(d2[j][i])
                dist[(x0 + i, z0 + j)] = d
                raw[k] = slope * d
    smooth = blur(raw, flat, w, h, BLUR_PASSES, BLUR_RADIUS)

    target = {}
    for j in range(h):
        for i in range(w):
            k = j * w + i
            if not flat[k]:
                continue
            x, z = x0 + i, z0 + j
            d = smooth[k] + _wave(x, z)
            d = min(d, dist[(x, z)] * slope + WAVE_AMP)
            target[(x, z)] = max(
                FLOOR_LIMIT_Y, LEVEL - max(MIN_DEPTH, int(round(d))))

    # No neighbor may stand more than a block above another. This is an
    # assertion, not a repair, and deliberately so: the property comes
    # from the construction (a slope-scaled distance field is Lipschitz
    # in the slope, the blur is an average of Lipschitz values, and the
    # ripple's gradient is two orders of magnitude below the budget), so
    # a violation here means the construction changed and wants looking
    # at. The repair it replaced only ever moved the *higher* column
    # down, which would have answered a rounding wobble by digging the
    # whole bed a block deeper and called that smoothness.
    bad = [((x, z), target[(x, z)], (nx, nz), target[(nx, nz)])
           for (x, z) in target
           for nx, nz in ((x + 1, z), (x, z + 1))
           if (nx, nz) in target
           and abs(target[(x, z)] - target[(nx, nz)]) > 1]
    if bad:
        raise NBTError(
            f"the floor field is not smooth at {len(bad)} places, e.g. "
            f"{bad[:4]} -- slope={slope} with this blur no longer bounds "
            f"the step, so the construction needs revisiting")
    return dist, target, (x0 + pad, z0 + pad, x1 - pad, z1 - pad)


# --------------------------------------------------------------------------
# What the floor is made of
# --------------------------------------------------------------------------

def bed_block(x: int, z: int, depth: int) -> str:
    """Bed material, graded by depth with noise-blurred boundaries.

    A hard ring at "depth 7" would read as a contour line drawn on the
    lake; the noise is worth more here than the grading is.
    """
    m = depth + 5.0 * fbm(x, z, 26.0, 4211)
    # Every boundary here has to come from *coherent* noise. A per-cell
    # die roll gives the right proportions and the wrong picture: half
    # of all neighboring pairs come out different, which is television
    # static, not a lake bed. The sand/gravel line got this right from
    # the start; the gravel/stone line and the clay did not, and read as
    # salt-and-pepper over three thousand columns and as 127 lone blocks
    # nobody would call a pocket.
    if 4 <= depth <= 26 and fbm(x, z, 15.0, 5119) > 0.35 \
            and fbm(x, z, 6.0, 8807) > 0.42:
        return "minecraft:clay"
    if m < 7.0:
        return "minecraft:sand"
    if m < 17.0:
        return "minecraft:gravel"
    return "minecraft:gravel" if fbm(x, z, 19.0, 6607) > 0.03 else "minecraft:stone"


# --------------------------------------------------------------------------
# Plants
# --------------------------------------------------------------------------

def plant_for(x: int, z: int, depth: int, bed: str):
    """What grows on this bed cell: ``None``, or ``(kind, height)``.

    Everything here is clustered rather than sprinkled. An even scatter
    of seagrass at one-in-four reads as static from underwater; the same
    count gathered into meadows, with clear sand between them, reads as
    a lake. Kelp gets the same treatment at a coarser scale, because a
    kelp forest is a place you swim into and a kelp sprinkle is litter.
    """
    if depth < 2:
        return None
    roll = unit(x, z, 1301)
    meadow = fbm(x, z, 21.0, 6151)

    if 2 <= depth <= 7:
        if roll < 0.018 and bed == "minecraft:sand" and depth <= 5:
            return ("sea_pickle", 1 + int(unit(x, z, 1699) * 3))
        if meadow < 0.02:
            return ("seagrass", 1) if roll < 0.035 else None
        thick = 0.20 + 0.42 * min(1.0, (meadow - 0.02) / 0.35)
        if depth >= 4 and roll < thick * 0.28:
            return ("tall_seagrass", 2)
        if roll < thick:
            return ("seagrass", 1)
        return None

    if depth <= 24:
        forest = fbm(x, z, 33.0, 2731)
        if forest > 0.14 and roll < 0.34:
            reach = min(depth - 2, 4 + int(unit(x, z, 3733) * 9))
            if reach >= 2:
                return ("kelp", reach)
        if meadow > 0.10 and roll < 0.16:
            return ("tall_seagrass", 2) if roll < 0.04 else ("seagrass", 1)
        return None

    if meadow > 0.18 and roll < 0.09:
        return ("seagrass", 1)
    return None


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------

def make_plan(slope=SLOPE, verbose=True):
    save = MinecraftSave.load(WORLD)
    dim = save.dimension()

    lake, lip, blocked = build_mask(save, dim)
    if verbose:
        print(f"lake mask: {len(lake)} wet columns, {len(lip)} dry columns "
              f"beside them ({len(blocked)} held back by a protected zone)")

    # -- the lip ----------------------------------------------------------
    # Every dry column the lake would spill into gets sand up to the
    # waterline. A column whose ground is somebody's masonry is left
    # exactly as it is: the lake laps against it today and will lap
    # against it afterwards, which is not our business to change.
    lip_plan = {}
    lip_skipped = Counter()
    for (x, z) in sorted(lip):
        if not any(n in lake for n in ((x + 1, z), (x - 1, z),
                                       (x, z + 1), (x, z - 1))):
            lip_skipped["not against the water"] += 1
            continue
        g, gname = ground_y(save, x, z, dim)
        if g is None:
            lip_skipped["ungenerated"] += 1
            continue
        if gname not in NATURAL:
            lip_skipped[f"built: {gname}"] += 1
            continue
        if g >= LEVEL:
            # Ground *above* the waterline with air under it: a ledge,
            # not a hollow. Sand laid from the waterline up would be
            # sand laid on nothing. The cave mouth under it is the
            # seal's business, not the lip's.
            lip_skipped["a ledge over air, sealed instead"] += 1
            continue
        if LEVEL - g > LIP_MAX_RISE:
            lip_skipped[f"a pit {LEVEL - g} deep, not a lip"] += 1
            continue
        if LEVEL - g <= BEACH_DEPTH:
            # A shallow hollow: sand from the ground up to the waterline
            # and the beach's own three courses below -- but never so
            # deep that the lowest grain rests on nothing. Reaching the
            # ground is always supported: the ground is what it rests on.
            depth = LEVEL - g + BEACH_DEPTH
            while depth > LEVEL - g and _void_now(save, x, LEVEL - depth, z):
                depth -= 1
            lip_plan[(x, z)] = {"y": LEVEL, "depth": depth}
        else:
            # A real hole at the water's edge -- dry, and up to a dozen
            # blocks deep. It has to be filled or the new sources pour
            # into it, but a twelve-block column of sand is a pillar, not
            # a beach: stone does the filling and sand is only the three
            # courses that show.
            lip_plan[(x, z)] = {"y": LEVEL, "depth": BEACH_DEPTH,
                                "stone": [g + 1, LEVEL - BEACH_DEPTH]}
    if verbose:
        print(f"lip: {len(lip_plan)} columns raised to y={LEVEL} in sand")
        for k, v in lip_skipped.most_common(8):
            print(f"    left alone {v:5d}  {k}")

    dist, target, box = build_floor(lake, slope)
    x0, z0, x1, z1 = box

    # -- the bed ----------------------------------------------------------
    frozen = {}          # columns whose floor is somebody's blocks
    columns = {}
    lava_cells = []
    stats = Counter()
    for (x, z) in sorted(lake):
        t = target[(x, z)]
        d = LEVEL - t
        old, oldname = ground_y(save, x, z, dim, start=LEVEL)
        if old is None:
            stats["ungenerated"] += 1
            continue
        if oldname not in NATURAL_BED:
            # Someone's blocks are the floor here (a ruined portal, a
            # sunken wall). Leave the floor alone; only the water above
            # it is ours to fix.
            frozen[(x, z)] = old
            t = old
            d = LEVEL - old
            stats["frozen"] += 1
        bed = None if (x, z) in frozen else bed_block(x, z, max(1, d))
        if oldname == "minecraft:obsidian" and bed is not None:
            # The sheet at y=57, x -618..-611, is where a lava pocket met
            # the water and froze -- the only obsidian in this lake, and
            # the only block here a player cannot make again. Cutting the
            # bed fifteen blocks below it would delete all thirty-seven
            # with no drop, so the bed keeps their material where the
            # sheet stood: the same scar, relaid at the new depth.
            bed = "minecraft:obsidian"
        columns[(x, z)] = {
            "y": t, "depth": max(0, d), "was": old, "bed": bed,
        }
        for y in range(t - 2, LEVEL + 1):
            if names(save, x, y, z) == "minecraft:lava":
                lava_cells.append((x, y, z))

    if verbose:
        print(f"columns planned: {len(columns)}  frozen: {stats['frozen']}  "
              f"lava cells inside the cut: {len(lava_cells)}")

    # -- sealing ----------------------------------------------------------
    # A lake is only as good as the cup it sits in. Two ways this one
    # leaks, and each has a trap in it:
    #
    # Sideways -- a cell of the new water column has an open face into a
    # column the lake does not own. The trap is skipping a face because
    # the neighbor is *a lake column*: that only means water if the
    # neighbor's floor is below this cell, and a frozen column's floor
    # can sit many blocks above its neighbors'. Skipping on membership
    # instead of on height left thirty-seven open faces into a ruin.
    #
    # Downwards -- the new floor has nothing under it. The trap is
    # testing for air: sand and gravel fall through *water* too, and
    # water under a thin crust is exactly what a flooded cave looks like.
    def _void(x, y, z):
        return _void_now(save, x, y, z)

    seal = {}
    unsealed = []
    walled_off = Counter()
    open_faces = set()
    for (x, z), col in sorted(columns.items()):
        top = col["y"]
        for y in range(top + 1, LEVEL + 1):
            for nx, nz in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
                nc = columns.get((nx, nz))
                if nc is not None and y >= nc["y"]:
                    # Water above the neighbor's floor, or the floor
                    # cell itself -- which the bed pass fills. Strictly
                    # greater was the bug: it let the seal claim a
                    # neighbor's own floor cell while that cell was
                    # still air, and the seal runs after the bed, so 52
                    # columns of shallow sand came out gray.
                    continue
                n = names(save, nx, y, nz)
                if n is None or (n not in AIR and n != "minecraft:lava"):
                    continue
                if (nx, nz) not in columns:
                    g, gname = ground_y(save, nx, nz, dim)
                    if gname is not None and gname not in NATURAL_BED:
                        # Bricking up somebody's doorway is not sealing.
                        # The lake laps against this cell today -- the
                        # water beside it is already a source with this
                        # air next to it -- so leaving it changes
                        # nothing, and it is recorded rather than fixed.
                        walled_off[gname] += 1
                        open_faces.add((nx, y, nz))
                        continue
                seal[(nx, y, nz)] = ("minecraft:sand" if y >= LEVEL - 1
                                     else "minecraft:stone")
        if (x, z) in frozen:
            continue
        if top > col["was"]:
            # A filled column stands on its own substrate, and that
            # stands on the old floor. Reading the world here would see
            # the water the fill is about to replace and call it a void.
            continue
        # Under the floor: enough solid to hold a gravity block up. That
        # is *one* block -- stone does not fall, so a single course under
        # the sand both supports it and closes the void. Filling the
        # whole cavity would be the mistake: it would wall off cave
        # systems nobody has looked at, to buy nothing.
        need = 0
        for dy in (1, 2):
            if _void(x, top - dy, z):
                need = dy
            else:
                break
        if need:
            unsealed.append((x, z, top, need))
        for dy in range(1, need + 1):
            seal[(x, top - dy, z)] = SUBSTRATE.get(col["bed"], "minecraft:stone")
    # A plug that falls is not a plug. Sand is used at the rim so the
    # seal matches the beach it sits in, but sand needs something under
    # it -- and a cave mouth in the bank is exactly where there is
    # nothing. Stone does not fall, so where the support is a void the
    # plug is stone and the beach pass lays its sand over the top.
    for (x, y, z), material in list(seal.items()):
        if material != "minecraft:sand":
            continue
        below = seal.get((x, y - 1, z))
        if below is None and _void(x, y - 1, z):
            seal[(x, y, z)] = "minecraft:stone"

    if verbose:
        print(f"seal: {len(seal)} cells "
              f"({sum(1 for v in seal.values() if v == 'minecraft:sand')} "
              f"at the rim, "
              f"{sum(1 for k in seal if k[1] < LEVEL - 1)} below it); "
              f"{len(unsealed)} columns needed a floor put under them")
        if walled_off:
            print(f"    faces left open rather than bricked up: "
                  f"{dict(walled_off)}")

    # -- the beach --------------------------------------------------------
    r = BEACH_WIDTH
    offsets = [(dx, dz) for dx in range(-r, r + 1) for dz in range(-r, r + 1)
               if 0 < dx * dx + dz * dz <= r * r]
    ring = {}
    for x, z in lake:
        for dx, dz in offsets:
            n = (x + dx, z + dz)
            if n in lake:
                continue
            d = dx * dx + dz * dz
            if n not in ring or d < ring[n]:
                ring[n] = d

    beach = dict(lip_plan)          # the lip is the beach's first course
    skipped = Counter()
    for (x, z) in sorted(ring):
        if (x, z) in beach:
            continue
        if _protected(x, z):
            skipped["protected zone"] += 1
            continue
        g, gname = ground_y(save, x, z, dim)
        if g is None:
            skipped["ungenerated"] += 1
            continue
        if gname not in NATURAL:
            skipped[f"built: {gname}"] += 1
            continue
        if g > LEVEL + BEACH_MAX_RISE:
            skipped["bluff"] += 1
            continue
        # How deep the sand may go. Three limits, and all three bite: it
        # must not cut into anything somebody built (there is a birch
        # cellar buried under the sand at (-647, 33)); the block under
        # the lowest grain must be solid, or the whole stack falls the
        # first time the game looks at it; and *water* is not solid --
        # sand falls through it, which is how a beach ends up on the
        # bottom of the pond it was meant to edge.
        depth = 0
        for k in range(BEACH_DEPTH):
            if names(save, x, g - k, z) not in NATURAL:
                break
            depth = k + 1
        while depth > 0 and _void(x, g - depth, z):
            depth -= 1
        if depth == 0:
            skipped["nothing to lay sand on"] += 1
            continue
        beach[(x, z)] = {"y": g, "depth": depth}

    if verbose:
        print(f"beach: {len(beach)} columns of sand "
              f"({sum(skipped.values())} in the ring left alone)")
        for k, v in skipped.most_common(12):
            print(f"    skipped {v:5d}  {k}")

    # -- plants -----------------------------------------------------------
    plants = {}
    pstats = Counter()
    for (x, z), col in columns.items():
        if (x, z) in frozen:
            continue
        d = col["depth"]
        got = plant_for(x, z, d, col["bed"])
        if got is None:
            continue
        kind, size = got
        top = col["y"] + (size if kind == "kelp" else 1)
        if kind == "kelp" and top > LEVEL - 2:
            size = max(2, LEVEL - 2 - col["y"])
            top = col["y"] + size
        if top > LEVEL:
            continue
        plants[(x, z)] = (kind, size)
        pstats[kind] += 1
    if verbose:
        print(f"plants: {dict(pstats)}")

    plan = {
        "level": LEVEL, "slope": slope, "box": [x0, z0, x1, z1],
        "columns": {f"{x},{z}": v for (x, z), v in columns.items()},
        "frozen": [[x, z] for (x, z) in sorted(frozen)],
        "beach": {f"{x},{z}": v for (x, z), v in beach.items()},
        "lip": [[x, z] for (x, z) in sorted(lip_plan)],
        "plants": {f"{x},{z}": list(v) for (x, z), v in plants.items()},
        "seal": [[x, y, z, b] for (x, y, z), b in sorted(seal.items())],
        "open_faces": [list(c) for c in sorted(open_faces)],
        "lava": [list(c) for c in lava_cells],
        "protect": PROTECT,
    }
    return save, plan


def ledger(save, plan):
    """What the build destroys, named and counted.

    Reshaping ground is not mining: a block replaced by water simply
    stops existing, with no drop and no record. Anything with character
    -- ore, clay, an obsidian cap over a lava pocket -- deserves to be a
    line in a report rather than a silent side effect.
    """
    level = plan["level"]
    cols = {tuple(int(v) for v in k.split(",")): c
            for k, c in plan["columns"].items()}
    frozen = {tuple(c) for c in plan["frozen"]}
    cut = Counter()
    buried = Counter()
    for (x, z), c in cols.items():
        if (x, z) in frozen:
            continue
        for y in range(min(c["y"], c["was"]) + 1, c["was"] + 1):
            n = names(save, x, y, z)
            if n is None:
                continue
            if y > c["y"]:
                cut[n] += 1
        for y in range(c["was"] + 1, c["y"] + 1):
            n = names(save, x, y, z)
            if n is not None and n not in AIR and n not in WATER:
                buried[n] += 1
    return cut, buried


def summarize(save, plan):
    level = plan["level"]
    cols = {tuple(int(v) for v in k.split(",")): c
            for k, c in plan["columns"].items()}
    depths = Counter(c["depth"] for c in cols.values())
    print(f"\ndepth: min {min(depths)} max {max(depths)}  "
          f"deepest floor y={level - max(depths)}")
    dig = sum(c["was"] - c["y"] for c in cols.values() if c["y"] < c["was"])
    fill = sum(c["y"] - c["was"] for c in cols.values() if c["y"] > c["was"])
    print(f"  cut out: {dig:,} blocks   filled in: {fill:,} blocks")
    print(f"  water after: {sum(level - c['y'] for c in cols.values()):,} sources")
    beds = Counter(c["bed"] for c in cols.values() if c["bed"])
    print(f"  bed: {dict(beds)}")
    frozen = {tuple(c) for c in plan["frozen"]}
    steps = Counter()
    held = Counter()
    for (x, z), c in cols.items():
        for n in ((x + 1, z), (x, z + 1)):
            if n not in cols:
                continue
            step = abs(cols[n]["y"] - c["y"])
            # A column whose floor is somebody's blocks keeps whatever
            # height they built it at; the smoothness promise is about
            # the bed this pass *cuts*, not about a sunken ruin.
            if (x, z) in frozen or n in frozen:
                held[step] += 1
            else:
                steps[step] += 1
    print(f"  adjacent floor steps: {dict(sorted(steps.items()))}")
    print(f"  ... across a frozen column: {dict(sorted(held.items()))}")
    if max(steps) > 1:
        raise NBTError("the planned floor has a cliff in it")
    print(f"  seal cells: {len(plan['seal'])}   lava to stone: {len(plan['lava'])}")
    print(f"  beach: {len(plan['beach'])} columns "
          f"(of which {len(plan['lip'])} are the lip, raised to the waterline)"
          f"   plants: {len(plan['plants'])} columns")

    cut, buried = ledger(save, plan)
    print("\n  what the cut destroys, most of it plain ground:")
    for n, c in cut.most_common(10):
        print(f"    {c:8,}  {n}")
    notable = {n: c for n, c in (cut + buried).items()
               if "ore" in n or n in ("minecraft:clay", "minecraft:obsidian",
                                      "minecraft:amethyst_block",
                                      "minecraft:magma_block")}
    if notable:
        print("  worth naming, because a block replaced by water leaves no drop:")
        for n, c in sorted(notable.items(), key=lambda kv: -kv[1]):
            print(f"    {c:8,}  {n}")


def main() -> None:
    slope = float(sys.argv[1]) if len(sys.argv) > 1 else SLOPE
    save, plan = make_plan(slope)
    summarize(save, plan)
    out = os.path.join(HERE, "plan.json")
    with open(out, "w") as fh:
        json.dump(plan, fh)
    print(f"\nwrote {out} ({os.path.getsize(out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
