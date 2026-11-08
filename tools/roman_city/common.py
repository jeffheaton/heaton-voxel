"""Shared vocabulary for the Roman city build: palettes and architecture.

Everything here works through a :class:`heaton_voxel.craft.build.Builder` so the
aftercare litany (connections, heightmaps, POI, lighting) stays automatic.
Coordinates are global block coordinates; the world is the TestWorld
superflat, whose surface block layer sits at ``GROUND`` everywhere.
"""

from __future__ import annotations

import math

# The superflat's layers: bedrock -64, dirt -63/-62, grass -61.
GROUND = -61          # y of the block you stand ON
WALK = GROUND + 1     # feet level

AIR = "minecraft:air"
WATER = "minecraft:water"
GRASS = "minecraft:grass_block[snowy=false]"
DIRT = "minecraft:dirt"

# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

# Marble -- monumental orders (temples, basilica, forum porticoes).
MARBLE = "minecraft:smooth_quartz"
MARBLE_STAIR = "minecraft:smooth_quartz_stairs"
MARBLE_SLAB = "minecraft:smooth_quartz_slab"
MARBLE_PILLAR = "minecraft:quartz_pillar[axis=y]"
MARBLE_CAP = "minecraft:chiseled_quartz_block"
MARBLE_BRICK = "minecraft:quartz_bricks"

# Travertine -- civic stone (gates, colonnades, trim, quays).
TRAV = "minecraft:smooth_sandstone"
TRAV_STAIR = "minecraft:smooth_sandstone_stairs"
TRAV_SLAB = "minecraft:smooth_sandstone_slab"
TRAV_CUT = "minecraft:cut_sandstone"
TRAV_RAW = "minecraft:sandstone"
TRAV_CHISEL = "minecraft:chiseled_sandstone"
TRAV_WALL = "minecraft:sandstone_wall"

# Roman brick -- city walls, utilitarian mass, insulae ground floors.
BRICK = "minecraft:bricks"
BRICK_STAIR = "minecraft:brick_stairs"
BRICK_SLAB = "minecraft:brick_slab"
BRICK_WALL = "minecraft:brick_wall"

# Terracotta roof tiles.  Granite reads as weathered clay tile from above;
# civic buildings step up to brick, domes go to copper.
TILE = "minecraft:granite"
TILE_STAIR = "minecraft:granite_stairs"
TILE_SLAB = "minecraft:granite_slab"

# Weighted surface mixes ----------------------------------------------------

# Basalt pavers of the viae: big dark polygonal stones.
VIA_MIX = [("minecraft:smooth_basalt", 9), ("minecraft:basalt[axis=y]", 2),
           ("minecraft:deepslate_tiles", 2),
           ("minecraft:cracked_deepslate_tiles", 1),
           ("minecraft:cobbled_deepslate", 1)]

# Lesser streets and alleys.
LANE_MIX = [("minecraft:cobbled_deepslate", 5), ("minecraft:andesite", 4),
            ("minecraft:stone", 2), ("minecraft:gravel", 2)]

# Sidewalks and colonnade walks.
WALK_MIX = [(TRAV, 10), (TRAV_RAW, 2), (TRAV_CUT, 1)]

# Country roads (via glareata: rolled gravel).
COUNTRY_MIX = [("minecraft:gravel", 6), ("minecraft:coarse_dirt", 3),
               ("minecraft:cobblestone", 2), ("minecraft:stone", 1)]

# Forum and temple-precinct paving.
PLAZA_MIX = [("minecraft:polished_diorite", 6), ("minecraft:diorite", 3),
             ("minecraft:calcite", 3), (MARBLE, 2)]

# Garden / courtyard gravel.
GARDEN_MIX = [("minecraft:gravel", 5), ("minecraft:andesite", 1)]

# City-wall brickwork with occasional relieving courses.
WALL_MIX = [(BRICK, 14), ("minecraft:terracotta", 1),
            ("minecraft:granite", 1)]


def mixpick(rng, mix) -> str:
    total = sum(w for _, w in mix)
    roll = rng.randrange(total)
    for name, w in mix:
        roll -= w
        if roll < 0:
            return name
    return mix[0][0]


# ---------------------------------------------------------------------------
# Low-level shapes
# ---------------------------------------------------------------------------

def paved_rect(b, x0, z0, x1, z1, mix, y=GROUND) -> None:
    """Pave a rectangle with a weighted mix, one block thick, at ``y``."""
    x0, x1 = sorted((x0, x1))
    z0, z1 = sorted((z0, z1))
    for z in range(z0, z1 + 1):
        for x in range(x0, x1 + 1):
            b.set(x, y, z, mixpick(b.rng, mix))


def clear_above(b, x0, z0, x1, z1, height, y=GROUND) -> None:
    b.fill((x0, y + 1, z0), (x1, y + height, z1), AIR)


def slab_run(b, cells, y, slab, kind="bottom") -> None:
    for x, z in cells:
        b.set(x, y, z, f"{slab}[type={kind}]")


def disc(cx, cz, r):
    lim = r * r + r
    return [(cx + dx, cz + dz) for dx in range(-r, r + 1)
            for dz in range(-r, r + 1) if dx * dx + dz * dz <= lim]


def ring(cx, cz, r):
    inner = set(disc(cx, cz, r - 1)) if r > 0 else set()
    rim = [c for c in disc(cx, cz, r) if c not in inner]
    rim.sort(key=lambda c: math.atan2(c[1] - cz, c[0] - cx))
    return rim


def facing_of(dx, dz) -> str:
    if abs(dx) > abs(dz):
        return "east" if dx > 0 else "west"
    return "south" if dz > 0 else "north"


OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
DIRVEC = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}


# ---------------------------------------------------------------------------
# Orders: columns, colonnades, arches
# ---------------------------------------------------------------------------

def column(b, x, z, y0, height, *, shaft=MARBLE_PILLAR, base=MARBLE,
           cap=MARBLE_CAP) -> None:
    """A 1x1 classical column: base block, fluted shaft, carved capital."""
    b.set(x, y0, z, base)
    if height > 2:
        b.fill((x, y0 + 1, z), (x, y0 + height - 2, z), shaft)
    b.set(x, y0 + height - 1, z, cap)


def thin_colonnade(b, cells, y0, height, *, post=TRAV_WALL, beam=TRAV,
                   spacing=3) -> None:
    """Slender wall-post columns every ``spacing`` cells under a continuous
    architrave beam. ``cells`` is an ordered centerline of (x, z)."""
    for i, (x, z) in enumerate(cells):
        if i % spacing == 0:
            b.fill((x, y0, z), (x, y0 + height - 1, z), post)
        b.set(x, y0 + height, z, beam)


def portico_flat(b, x0, z0, x1, z1, face, y0, *, height=4, spacing=3,
                 post=TRAV_WALL, beam=TRAV, deck=TRAV_SLAB) -> None:
    """A flat-roofed covered walk over the rectangle, columns on ``face``.

    The rectangle is the walk itself; the colonnade stands on its ``face``
    edge ("north" means the walk's north edge carries the columns) and a
    slab deck at ``y0 + height + 1`` covers the whole walk.
    """
    x0, x1 = sorted((x0, x1))
    z0, z1 = sorted((z0, z1))
    if face == "north":
        cells = [(x, z0) for x in range(x0, x1 + 1)]
    elif face == "south":
        cells = [(x, z1) for x in range(x0, x1 + 1)]
    elif face == "west":
        cells = [(x0, z) for z in range(z0, z1 + 1)]
    else:
        cells = [(x1, z) for z in range(z0, z1 + 1)]
    # Deck first; the colonnade then overwrites its own edge with the beam.
    for z in range(z0, z1 + 1):
        for x in range(x0, x1 + 1):
            b.set(x, y0 + height + 1, z, f"{deck}[type=bottom]")
    thin_colonnade(b, cells, y0 + 1, height, post=post, beam=beam,
                   spacing=spacing)
    for i, (x, z) in enumerate(cells):
        if i % spacing == spacing // 2 + 1 and i % 6 == spacing // 2 + 1:
            b.set(x, y0 + height, z, "minecraft:lantern[hanging=true]")


def arch_portal(b, cx, y0, cz, axis, width, height, depth, *,
                stair=TRAV_STAIR, keystone=TRAV_CHISEL) -> None:
    """Cut an arched passage through existing mass and dress it.

    The passage runs along ``axis`` ("x": travel east-west), is ``width``
    across (odd), ``height`` of clear air at the center, ``depth`` long.
    Stair corners spring the arch; a chiselled keystone crowns each face.
    """
    half = width // 2
    d0 = -(depth // 2)
    d1 = d0 + depth - 1
    for off in range(-half, half + 1):
        h = height if abs(off) < half else height - 1
        for d in range(d0, d1 + 1):
            x, z = (cx + d, cz + off) if axis == "x" else (cx + off, cz + d)
            b.fill((x, y0, z), (x, y0 + h - 1, z), AIR)
    # Spring stairs arch the soffit along the whole passage.
    for d in range(d0, d1 + 1):
        for off in (-half, half):
            if axis == "x":
                x, z = cx + d, cz + off
                face = "south" if off < 0 else "north"
            else:
                x, z = cx + off, cz + d
                face = "east" if off < 0 else "west"
            b.set(x, y0 + height - 1, z,
                  f"{stair}[facing={face},half=top]")
    # Keystones in the face mass directly over each opening.
    for d in (d0, d1):
        x, z = (cx + d, cz) if axis == "x" else (cx, cz + d)
        b.set(x, y0 + height, z, keystone)


def arcade(b, p0, p1, y0, top_y, *, pier=TRAV_RAW, arch=TRAV,
           stair=TRAV_STAIR, span=6) -> None:
    """A run of piers and arches (aqueduct / bridge substructure).

    Straight line from ``p0`` to ``p1`` (must share an axis), solid piers
    2 wide every ``span`` cells, arches sprung between them, spandrel wall
    filled up to ``top_y`` (the deck rests on ``top_y``; build it after).
    """
    (ax, az), (bx, bz) = p0, p1
    if ax != bx and az != bz:
        raise ValueError("arcade must run along one axis")
    along_x = az == bz
    lo, hi = (min(ax, bx), max(ax, bx)) if along_x else (min(az, bz), max(az, bz))
    length = hi - lo + 1

    def cell(i):
        return (lo + i, az) if along_x else (ax, lo + i)

    for i in range(length):
        x, z = cell(i)
        k = i % span
        if k in (0, 1):                     # pier
            b.fill((x, y0, z), (x, top_y, z), pier)
        else:                               # open bay
            spring = top_y - 2
            b.fill((x, spring + 1, z), (x, top_y, z), arch)
            if k == 2:
                b.set(x, spring, z,
                      f"{stair}[facing={'west' if along_x else 'north'},half=top]")
            elif k == span - 1:
                b.set(x, spring, z,
                      f"{stair}[facing={'east' if along_x else 'south'},half=top]")


# ---------------------------------------------------------------------------
# Roofs
# ---------------------------------------------------------------------------

def hipped_roof(b, x0, z0, x1, z1, base_y, *, stair=TILE_STAIR, block=TILE,
                slab=TILE_SLAB, overhang=1) -> None:
    """A tiled hip roof: stair rings stepping inward from every edge.

    Works on any rectangle; the ridge forms along the long axis by itself.
    ``base_y`` is the eaves course (one above the wall top).
    """
    x0, x1 = sorted((x0, x1))
    z0, z1 = sorted((z0, z1))
    x0 -= overhang; z0 -= overhang; x1 += overhang; z1 += overhang
    y = base_y
    while True:
        w, l = x1 - x0, z1 - z0
        if w < 0 or l < 0:
            break
        if w == 0 and l == 0:
            b.set(x0, y, z0, f"{slab}[type=bottom]")
            break
        if w == 0:
            for z in range(z0, z1 + 1):
                b.set(x0, y, z, f"{slab}[type=bottom]")
            break
        if l == 0:
            for x in range(x0, x1 + 1):
                b.set(x, y, z0, f"{slab}[type=bottom]")
            break
        for x in range(x0, x1 + 1):
            b.set(x, y, z0, f"{stair}[facing=south,half=bottom]")
            b.set(x, y, z1, f"{stair}[facing=north,half=bottom]")
        for z in range(z0 + 1, z1):
            b.set(x0, y, z, f"{stair}[facing=east,half=bottom]")
            b.set(x1, y, z, f"{stair}[facing=west,half=bottom]")
        # Corner mass so the hip line reads solid from above.
        for cx, cz in ((x0, z0), (x1, z0), (x0, z1), (x1, z1)):
            b.set(cx, y, cz, block)
        x0 += 1; z0 += 1; x1 -= 1; z1 -= 1
        y += 1


def gable_roof(b, x0, z0, x1, z1, base_y, axis, *, stair=TILE_STAIR,
               plank=TRAV, slab=TILE_SLAB) -> None:
    """Builder.gable_roof with the terracotta kit and travertine gables."""
    b.gable_roof(x0, z0, x1, z1, base_y, axis, stair=stair, plank=plank,
                 slab=slab)


def shed_roof(b, x0, z0, x1, z1, base_y, toward, *, stair=TILE_STAIR) -> None:
    """A single-pitch tile roof rising toward ``toward`` one step per row."""
    x0, x1 = sorted((x0, x1))
    z0, z1 = sorted((z0, z1))
    if toward in ("north", "south"):
        rows = range(z0, z1 + 1) if toward == "north" else range(z1, z0 - 1, -1)
        for i, z in enumerate(rows):
            for x in range(x0, x1 + 1):
                b.set(x, base_y + i, z,
                      f"{stair}[facing={toward},half=bottom]")
    else:
        rows = range(x0, x1 + 1) if toward == "west" else range(x1, x0 - 1, -1)
        for i, x in enumerate(rows):
            for z in range(z0, z1 + 1):
                b.set(x, base_y + i, z,
                      f"{stair}[facing={toward},half=bottom]")


# ---------------------------------------------------------------------------
# Streets
# ---------------------------------------------------------------------------

def street(b, axis, center, lo, hi, *, carriage=7, walk=0, mix=VIA_MIX,
           curb=True) -> None:
    """A paved street along ``axis`` at ``center``, from ``lo`` to ``hi``.

    ``carriage`` is the paved width (odd); ``walk`` adds travertine
    sidewalks that many blocks wide on both sides, with a cut-stone curb
    course between. Everything sits flush at GROUND.
    """
    half = carriage // 2
    for t in range(lo, hi + 1):
        for off in range(-half, half + 1):
            x, z = (t, center + off) if axis == "x" else (center + off, t)
            b.set(x, GROUND, z, mixpick(b.rng, VIA_MIX if mix is None else mix))
        for w in range(walk):
            for sign in (-1, 1):
                off = sign * (half + 1 + w)
                x, z = (t, center + off) if axis == "x" else (center + off, t)
                block = (TRAV_CUT if (curb and w == 0)
                         else mixpick(b.rng, WALK_MIX))
                b.set(x, GROUND, z, block)


def lamp_post(b, x, z, y0=GROUND) -> None:
    """A travertine post with a lantern -- street lighting that reads Roman."""
    b.set(x, y0 + 1, z, TRAV_WALL)
    b.set(x, y0 + 2, z, TRAV_WALL)
    b.set(x, y0 + 3, z, "minecraft:lantern[hanging=false]")


def basin(b, x0, z0, x1, z1, *, rim=TRAV, water_depth=1, y=GROUND) -> None:
    """A water basin flush with the ground: rim course, water inside."""
    x0, x1 = sorted((x0, x1))
    z0, z1 = sorted((z0, z1))
    b.fill((x0, y, z0), (x1, y, z1), rim)
    if x1 - x0 >= 2 and z1 - z0 >= 2:
        b.fill((x0 + 1, y - water_depth + 1, z0 + 1),
               (x1 - 1, y - water_depth + 1, z1 - 1), WATER)


# ---------------------------------------------------------------------------
# Interiors
# ---------------------------------------------------------------------------

def stair_run(b, x, z, y0, height, toward, *, stair="minecraft:oak_stairs",
              support="minecraft:oak_planks") -> tuple[int, int]:
    """A straight interior stair climbing ``height`` toward ``toward``.

    Returns the (x, z) of the arrival cell (one past the top step).
    Cuts nothing: cut the floor opening yourself where the run arrives.
    """
    dx, dz = DIRVEC[toward]
    for i in range(height):
        sx, sz, sy = x + dx * i, z + dz * i, y0 + i
        b.set(sx, sy, sz, f"{stair}[facing={toward},half=bottom]")
        if i > 0:
            b.fill((sx, y0, sz), (sx, sy - 1, sz), support)
    return x + dx * height, z + dz * height


def ladder_shaft(b, x, z, y0, y1, face) -> None:
    for y in range(y0, y1 + 1):
        b.set(x, y, z, f"minecraft:ladder[facing={face}]")


# ---------------------------------------------------------------------------
# Civic architecture
# ---------------------------------------------------------------------------

def grand_portico(b, x0, z0, x1, z1, face, y0, *, col_h=6, spacing=4,
                  deck=MARBLE_SLAB, beam=MARBLE) -> None:
    """A marble colonnade: full columns under a block architrave, slab deck.

    Like :func:`portico_flat` but with proper 1x1 columns -- the forum's
    order, not the street's.
    """
    x0, x1 = sorted((x0, x1))
    z0, z1 = sorted((z0, z1))
    if face == "north":
        cells = [(x, z0) for x in range(x0, x1 + 1)]
    elif face == "south":
        cells = [(x, z1) for x in range(x0, x1 + 1)]
    elif face == "west":
        cells = [(x0, z) for z in range(z0, z1 + 1)]
    else:
        cells = [(x1, z) for z in range(z0, z1 + 1)]
    for z in range(z0, z1 + 1):
        for x in range(x0, x1 + 1):
            b.set(x, y0 + col_h + 1, z, f"{deck}[type=bottom]")
    for i, (x, z) in enumerate(cells):
        b.set(x, y0 + col_h + 1, z, beam)
        if i % spacing == 0:
            column(b, x, z, y0 + 1, col_h)
        elif i % spacing == spacing // 2 and i % (spacing * 2) == spacing // 2:
            b.set(x, y0 + col_h, z, "minecraft:lantern[hanging=true]")


def mosaic_floor(b, x0, z0, x1, z1, y, *, border=MARBLE,
                 a="minecraft:polished_diorite", c="minecraft:calcite") -> None:
    x0, x1 = sorted((x0, x1))
    z0, z1 = sorted((z0, z1))
    for z in range(z0, z1 + 1):
        for x in range(x0, x1 + 1):
            if x in (x0, x1) or z in (z0, z1):
                b.set(x, y, z, border)
            else:
                b.set(x, y, z, a if (x + z) % 2 else c)


def window_open(b, x, y, z, face) -> None:
    """An unglazed 1x2 arched window in a one-block wall."""
    b.set(x, y, z, AIR)
    b.set(x, y + 1, z, f"{TRAV_STAIR}[facing={face},half=top]")


def pediment(b, x0, x1, z, base_y, *, block=MARBLE, pitch=1) -> None:
    """A marble gable triangle across x at one z plane.

    ``pitch=1`` rises one course per inset (45 degrees); ``pitch=2`` rises
    one per two insets -- the shallow rake of a temple front.
    """
    x0, x1 = sorted((x0, x1))
    lo, hi = x0, x1
    y = base_y
    n = 0
    while lo <= hi:
        b.fill((lo, y, z), (hi, y, z), block)
        lo += 1
        hi -= 1
        n += 1
        if n % pitch == 0:
            y += 1


def low_hip(b, x0, z0, x1, z1, base_y, *, rise_per=3, slab=TILE_SLAB,
            block=TILE) -> None:
    """A shallow tiled roof: full-block rings stepping up every ``rise_per``
    inward rings -- the low pitch of a temple behind its pediment."""
    x0, x1 = sorted((x0, x1))
    z0, z1 = sorted((z0, z1))
    y = base_y
    step = 0
    while x0 <= x1 and z0 <= z1:
        for x in range(x0, x1 + 1):
            b.set(x, y, z0, block)
            b.set(x, y, z1, block)
        for z in range(z0 + 1, z1):
            b.set(x0, y, z, block)
            b.set(x1, y, z, block)
        x0 += 1; z0 += 1; x1 -= 1; z1 -= 1
        step += 1
        if step % rise_per == 0:
            y += 1
    # fill the plateau
    if x0 > x1 or z0 > z1:
        return
    b.fill((x0, y, z0), (x1, y, z1), block)


# ---------------------------------------------------------------------------
# Furnishings
# ---------------------------------------------------------------------------

def bench(b, x0, z0, x1, z1, y, facing, *, stair="minecraft:spruce_stairs") -> None:
    b.fill((min(x0, x1), y, min(z0, z1)), (max(x0, x1), y, max(z0, z1)),
           f"{stair}[facing={facing},half=bottom]")


def table(b, x, z, y, *, leg="minecraft:spruce_fence",
          top="minecraft:spruce_slab") -> None:
    b.set(x, y, z, leg)
    b.set(x, y + 1, z, f"{top}[type=bottom]")


def couch(b, x, z, y, along, *, wool="minecraft:red_wool",
          stair="minecraft:spruce_stairs") -> None:
    """A Roman dining couch: two cushions and a raised headrest."""
    dx, dz = DIRVEC[along]
    head_face = OPPOSITE[along]
    b.set(x, y, z, f"{stair}[facing={head_face},half=bottom]")
    b.set(x + dx, y, z + dz, wool)
    b.set(x + 2 * dx, y, z + 2 * dz, wool)


def amphora(b, x, z, y, facing="north") -> None:
    b.set(x, y, z, f"minecraft:decorated_pot[facing={facing}]")


def brazier(b, x, z, y) -> None:
    b.set(x, y, z, "minecraft:campfire[lit=true,facing=north]")


def candelabrum(b, x, z, y) -> None:
    b.set(x, y, z, TRAV_WALL)
    b.set(x, y + 1, z, "minecraft:torch")


def chain_lantern(b, x, y_beam, z, drop=1) -> None:
    for i in range(drop):
        b.set(x, y_beam - 1 - i, z, "minecraft:iron_chain[axis=y]")
    b.set(x, y_beam - 1 - drop, z, "minecraft:lantern[hanging=true]")


def statue_herm(b, x, z, y0, *, tall=2) -> None:
    b.set(x, y0, z, MARBLE_CAP)
    for i in range(tall):
        b.set(x, y0 + 1 + i, z, MARBLE_PILLAR)
    b.set(x, y0 + 1 + tall, z, f"{MARBLE_SLAB}[type=bottom]")


def dome(b, cx, cz, base_y, r, *, block=TRAV, oculus=2, squash=0.75) -> None:
    """A masonry dome: a squashed hemisphere shell with an open oculus."""
    for dx, dz in disc(0, 0, r):
        d2 = dx * dx + dz * dz
        if oculus and d2 <= oculus * oculus:
            continue
        h = round(math.sqrt(max(0, r * r - d2)) * squash)
        b.set(cx + dx, base_y + h, cz + dz, block)
        if h > 0:
            b.set(cx + dx, base_y + h - 1, cz + dz, block)
    top = base_y + round(r * squash)
    for dx, dz in ring(0, 0, oculus):
        b.set(cx + dx, top, cz + dz, f"{TRAV_SLAB}[type=bottom]")


def round_fountain(b, cx, cz, r, y=GROUND) -> None:
    """A round marble basin with a central jet."""
    for x, z in disc(cx, cz, r):
        b.set(x, y, z, MARBLE)
    for x, z in disc(cx, cz, r - 1):
        b.set(x, y, z, WATER)
    for x, z in ring(cx, cz, r):
        b.set(x, y + 1, z, f"{MARBLE_SLAB}[type=bottom]")
    b.set(cx, y, cz, MARBLE)
    b.set(cx, y + 1, cz, MARBLE_PILLAR)
    b.set(cx, y + 2, cz, WATER)
