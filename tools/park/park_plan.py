#!/usr/bin/env python3
"""Geometry and palettes for the JeffWorld park.

The site is the flattened rectangle north of the stadium and west of the
stained-glass tower grid: ``x -39..3, z 172..323``, lawn surface at y=61.
The east edge stops at x=3 so the biome repaint, which can only be written
in 4x4x4 cells, never reaches the tower grid that starts at x=5.
Everything that decides *where* something goes lives here; ``build_park.py``
decides *what* gets written.

Three things about this ground drove the layout, and none of them show in a
top-down render:

* the pale oak grove filling the north-west is player-planted (the creaking
  hearts read ``natural=false``), so it is kept and the promenade is routed
  east of it;
* the "frozen river" across the middle is a **sheet of ice over open air** --
  there is no river bed under it, so any water here needs a basin built
  before it is filled, not merely a hole dug;
* a solid copper mass is buried between ``z 216..240`` reaching y=61, and
  caves run to within a block of the lawn over much of the south half, so the
  build seals a crust rather than trusting the ground it stands on.
"""

# -- the site -----------------------------------------------------------------

X0, Z0, X1, Z1 = -39, 172, 3, 323
GY = 61          # top solid block of the lawn
WALK = GY + 1    # the air a walker stands in
WATER_TOP = 60   # the lake sits one block below the lawn

#: Boxes the build must not touch. Digested by ``Worksite.protect`` *and*
#: consulted by ``keep_out`` -- the guard catches a mistake, this avoids it.
PROTECT = [
    ("glass-towers", (5, 50, 168), (34, 100, 326)),
    ("north-road", (-42, 58, 164), (6, 72, 170)),
    ("copper-vault", (-23, 44, 214), (4, 60, 242)),
    ("copper-cap", (2, 61, 228), (4, 61, 234)),
    ("obsidian-pit", (-13, 58, 318), (-11, 61, 320)),
    ("east-brick", (3, 58, 316), (4, 61, 323)),
]

#: Blocks the build never removes: the grove, and what grows on it.
KEEPERS = (
    "minecraft:pale_oak_log", "minecraft:pale_oak_wood",
    "minecraft:stripped_pale_oak_log", "minecraft:pale_oak_leaves",
    "minecraft:creaking_heart", "minecraft:pale_hanging_moss",
    "minecraft:resin_clump", "minecraft:moss_carpet",
)

# -- palettes -----------------------------------------------------------------

AIR = "minecraft:air"
GRASS = "minecraft:grass_block[snowy=false]"
DIRT = "minecraft:dirt"
STONE = "minecraft:stone"
GLOW = "minecraft:glowstone"
WATER = "minecraft:water[level=0]"

#: The promenade and the plazas: the stone-brick and andesite vocabulary the
#: rest of this world's paving already speaks, with granite kept for trim.
PATH_MIX = [("minecraft:stone_bricks", 7), ("minecraft:andesite", 4),
            ("minecraft:polished_andesite", 3),
            ("minecraft:cracked_stone_bricks", 1)]
PLAZA_TRIM = "minecraft:polished_granite"

#: Garden walks: softer, so the formal spine reads as the formal one.
WALK_MIX = [("minecraft:gravel", 6), ("minecraft:dirt_path", 5),
            ("minecraft:coarse_dirt", 3), ("minecraft:cobblestone", 2)]

#: Lake bed. Laid on a stone liner, so the falling blocks are supported.
BED_MIX = [("minecraft:sand", 6), ("minecraft:gravel", 4),
           ("minecraft:clay", 3), ("minecraft:dirt", 2),
           ("minecraft:moss_block", 2)]

#: The bank between water and lawn.
SHORE_MIX = [("minecraft:moss_block", 5), ("minecraft:sand", 4),
             ("minecraft:gravel", 3), ("minecraft:coarse_dirt", 2),
             ("minecraft:podzol", 2)]

#: Lawn, mottled so it is not one flat green.
LAWN_MIX = [(GRASS, 26), ("minecraft:moss_block", 3)]

#: One-block flowers. Weighted so a bed reads as planted, not as confetti.
FLOWERS = [("minecraft:poppy", 6), ("minecraft:dandelion", 6),
           ("minecraft:oxeye_daisy", 5), ("minecraft:cornflower", 4),
           ("minecraft:azure_bluet", 4), ("minecraft:allium", 3),
           ("minecraft:red_tulip", 3), ("minecraft:orange_tulip", 2),
           ("minecraft:white_tulip", 2), ("minecraft:pink_tulip", 2),
           ("minecraft:lily_of_the_valley", 3), ("minecraft:blue_orchid", 1)]

#: Two-block flowers -- these must go in through ``place_block``; a lone
#: lower half is the kind of thing the game's next update deletes.
TALL_FLOWERS = ["minecraft:lilac", "minecraft:rose_bush", "minecraft:peony",
                "minecraft:sunflower"]

#: Ground cover, for the grove floor and the woodland.
UNDERSTORY = [("minecraft:short_grass", 10), ("minecraft:fern", 6),
               ("minecraft:moss_carpet", 5), ("minecraft:pink_petals", 2)]

HEDGE = "minecraft:oak_leaves[persistent=true]"
HEDGE_FLOWER = "minecraft:flowering_azalea_leaves[persistent=true]"

#: Avenue and specimen trees, in the order they alternate down the walk.
AVENUE = ["cherry", "oak", "cherry", "birch"]
WOODLAND = ["spruce", "birch", "oak", "spruce", "dark_oak", "cherry"]

# -- the spine ----------------------------------------------------------------

PROM_HALF = 2                    # 5 cells across
BRIDGE_DECK = 63                 # top solid block of the bridge
#: The run the bridge decks over. Fixed rather than derived from where the
#: water happens to fall: a deck computed from min/max of the wet cells
#: stretches to swallow one stray puddle and becomes a viaduct.
BRIDGE_Z = (259, 265)


def prom_center(z: int) -> int:
    """Center x of the promenade at ``z``.

    Straight down the east side past the grove, then a long easy swing west
    to the middle of the park for the lake, the fountain and the south gate.
    """
    if z <= 210:
        return -9
    if z <= 222:
        return round(-9 + (z - 210) * -3 / 12)
    if z <= 236:
        return round(-12 + (z - 222) * -6 / 14)
    return -18


#: Discs whose union is the lake: a broad west basin, a narrow neck the
#: promenade bridges, and a smaller east pool. Kept clear of the buried
#: copper (which ends at z=242) and inside the park edge, so there is
#: always a shore to walk.
LAKE_DISCS = [(-29, 255, 9), (-33, 247, 6), (-27, 265, 7),
              (-18, 262, 3), (-11, 263, 5)]
LAKE_BOX = (-37, 244, -7, 272)

#: The 3x3 obsidian box buried at the south end is somebody's, and stays.
#: The park makes a feature of it rather than pretending it is not there.
OBSIDIAN = (-12, 319)

#: A jetty off the west basin, reached straight off the promenade.
JETTY = (-21, 250, 6)            # shore x, z, how far it runs into the water

#: The four formal beds that break up the entrance court, flanking the walk.
GATE_BEDS = [(-15, 175, -12, 179), (-6, 175, -3, 179),
             (-15, 181, -12, 185), (-6, 181, -3, 185)]

#: Where dense flowering is the point rather than an accident.
MEADOW = (-11, 296, 3, 320)

#: Paved rooms: (name, kind, geometry). Circles are (cx, cz, r).
GATE_COURT = (-16, 172, -2, 187)
SOUTH_COURT = (-26, 312, -14, 323)
FOUNTAIN = (-18, 288, 11)        # plaza circle
FOUNTAIN_BASIN = 5               # water basin radius inside it

BANDSTAND = (-30, 226)           # center; 9x9 platform
PAVILION = (-3, 259)             # center; 7x7, on the east pool's shore
ROCKERY = (-35, 240)             # cascade mound, feeding the lake from the NW
PERGOLA_Z = (302, 314)           # the run the pergola covers
PARTERRE = (-5, 192, 1, 214)     # the formal flower garden, east lawn

#: Garden walks, as center-line polylines; each is laid 3 cells across.
GROVE_WALK = [(-12, 182), (-20, 184), (-26, 190), (-30, 198), (-31, 208),
              (-27, 214), (-22, 219)]
WOOD_WALK = [(-21, 274), (-28, 278), (-33, 286), (-34, 296), (-31, 306),
             (-26, 313)]
EAST_WALK = [(3, 187), (3, 216), (0, 228), (0, 240), (-2, 252), (-1, 266),
             (-4, 280), (-8, 296), (-12, 310), (-16, 320)]

#: Cross links: (x0, z0, x1, z1) rectangles.
CROSS = [(-31, 219, -10, 221), (-30, 232, -20, 234), (-16, 300, -4, 302)]


# -- helpers ------------------------------------------------------------------

def in_park(x: int, z: int) -> bool:
    return X0 <= x <= X1 and Z0 <= z <= Z1


def keep_out(x: int, y: int, z: int) -> bool:
    """True where the build must not write. Mirrors :data:`PROTECT`."""
    for _, c1, c2 in PROTECT:
        if (c1[0] <= x <= c2[0] and c1[1] <= y <= c2[1]
                and c1[2] <= z <= c2[2]):
            return True
    return False


def column_locked(x: int, z: int) -> bool:
    """True where even the lawn surface is off limits."""
    return keep_out(x, GY, z) or keep_out(x, WALK, z)


def disc_cells(cx: int, cz: int, r: int):
    for z in range(cz - r, cz + r + 1):
        for x in range(cx - r, cx + r + 1):
            if (x - cx) ** 2 + (z - cz) ** 2 <= r * r:
                yield x, z


def rect_cells(x0: int, z0: int, x1: int, z1: int):
    for z in range(z0, z1 + 1):
        for x in range(x0, x1 + 1):
            yield x, z


def polyline_cells(points, half: int):
    """Every cell within ``half`` of a polyline, walked at half-block steps."""
    cells = set()
    for (ax, az), (bx, bz) in zip(points, points[1:]):
        steps = max(abs(bx - ax), abs(bz - az)) * 2 or 1
        for i in range(steps + 1):
            t = i / steps
            cx = round(ax + (bx - ax) * t)
            cz = round(az + (bz - az) * t)
            for dz in range(-half, half + 1):
                for dx in range(-half, half + 1):
                    if abs(dx) + abs(dz) <= half + (half > 1):
                        cells.add((cx + dx, cz + dz))
    return {c for c in cells if in_park(*c)}


def lake_cells():
    """The water's footprint, clipped to the park and to :data:`LAKE_BOX`."""
    bx0, bz0, bx1, bz1 = LAKE_BOX
    cells = set()
    for cx, cz, r in LAKE_DISCS:
        for x, z in disc_cells(cx, cz, r):
            if bx0 <= x <= bx1 and bz0 <= z <= bz1 and in_park(x, z):
                cells.add((x, z))
    return {c for c in cells if not column_locked(*c)}


def promenade_cells():
    cells = set()
    for z in range(Z0, Z1 + 1):
        cx = prom_center(z)
        for x in range(cx - PROM_HALF, cx + PROM_HALF + 1):
            if in_park(x, z):
                cells.add((x, z))
    return cells


def depth_map(water):
    """Water depth per cell: 1 at the margin, 3 in the middle.

    A pond of one uniform depth reads as a tank. This is a distance
    transform, so the bed shelves the way a bank does.
    """
    from collections import deque
    dist = {}
    q = deque()
    for x, z in water:
        if any((x + dx, z + dz) not in water
               for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))):
            dist[(x, z)] = 1
            q.append((x, z))
    while q:
        x, z = q.popleft()
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, z + dz)
            if n in water and n not in dist:
                dist[n] = dist[(x, z)] + 1
                q.append(n)
    out = {}
    for cell, d in dist.items():
        out[cell] = 1 if d <= 1 else (2 if d <= 4 else 3)
    return out


def ring(cells, width: int):
    """Cells within ``width`` of ``cells`` but not in it."""
    out = set()
    for x, z in cells:
        for dz in range(-width, width + 1):
            for dx in range(-width, width + 1):
                if abs(dx) + abs(dz) <= width:
                    c = (x + dx, z + dz)
                    if c not in cells and in_park(*c):
                        out.add(c)
    return out
