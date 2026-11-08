"""JeffWorld districts and life: markets, warehouses, homes, citizens.

The macellum gets its tholos, the horrea their vaulted sheds, and the
residential blocks are filled by a seeded insula generator -- shops on the
street fronts, stairs and furnished apartments above, courtyards behind --
plus two atrium-and-peristyle domus. Then the softer layer: trees, stalls,
neighborhood fountains, gate banners, and villagers with beds and
workstations for the game to bind on load.

    python tools/roman_city/jw_districts.py [world] [--seed N]
"""

from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

from heaton_voxel.nbt import world_is_open  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402
from heaton_voxel.craft.build import Builder  # noqa: E402

import common as C  # noqa: E402
import jw_plan as P  # noqa: E402
from jw_groundwork import verify_paths  # noqa: E402

STUCCO = "minecraft:white_terracotta"
MUD = "minecraft:mud_bricks"
TIMBER = "minecraft:spruce_wood"
PLANKS = "minecraft:spruce_planks"
SHOPFLOOR = "minecraft:polished_andesite"

BEDS = ["minecraft:red_bed", "minecraft:white_bed", "minecraft:brown_bed",
        "minecraft:lime_bed"]
STATIONS = ["minecraft:smoker[facing=%s]", "minecraft:loom[facing=%s]",
            "minecraft:barrel[facing=up]", "minecraft:stonecutter[facing=%s]",
            "minecraft:cartography_table", "minecraft:fletching_table",
            "minecraft:smithing_table", "minecraft:composter"]

INSULA_BLOCKS = [
    (288, 404, 338, 436),
    (344, 404, 390, 436),
    (288, 444, 338, 464),
    (344, 444, 390, 464),
    (340, 356, 366, 392),
    (482, 406, 498, 436),
]
DOMUS_SITES = [(368, 356, 390, 392), (352, 444, 390, 464)]


# ---------------------------------------------------------------------------

def base_of(b, x0, z0, x1, z1):
    gs = [b.ground(x, z) for x, z in ((x0, z0), (x1, z0), (x0, z1), (x1, z1),
                                      ((x0 + x1) // 2, (z0 + z1) // 2))]
    return round(sum(gs) / len(gs))


def unit(b, x0, z0, x1, z1, face, *, floors=2, shop=False, poor=False):
    """One insula unit: pad, shell, stairs, furniture, roof."""
    base = base_of(b, x0, z0, x1, z1)
    b.level_pad(x0, z0, x1, z1, base, surface=SHOPFLOOR if shop else PLANKS,
                wall=C.BRICK, clearance=14)
    wall_g = MUD if poor else C.BRICK
    story = 4
    top = base + floors * story
    # Shell: ground story brick, upper stories stucco with timber bands.
    for x in range(x0, x1 + 1):
        for z in range(z0, z1 + 1):
            if x in (x0, x1) or z in (z0, z1):
                b.fill((x, base + 1, z), (x, base + story, z), wall_g)
                for f in range(1, floors):
                    y0 = base + f * story + 1
                    b.set(x, y0 - 1, z, TIMBER)
                    b.fill((x, y0, z), (x, min(y0 + story - 1, top), z),
                           STUCCO)
    b.fill((x0 + 1, base + 1, z0 + 1), (x1 - 1, top, z1 - 1), C.AIR)
    for f in range(1, floors):                     # upper floors
        b.fill((x0 + 1, base + f * story, z0 + 1),
               (x1 - 1, base + f * story, z1 - 1), PLANKS)

    dx, dz = C.DIRVEC[face]
    fx = x0 if face == "west" else x1 if face == "east" else (x0 + x1) // 2
    fz = z0 if face == "north" else z1 if face == "south" else (z0 + z1) // 2
    if shop:
        # Wide shop mouth with a top-stair lintel and a counter beside it.
        if face in ("north", "south"):
            b.fill((fx - 1, base + 1, fz), (fx + 1, base + 3, fz), C.AIR)
            b.set(fx - 2, base + 1, fz, f"{C.TRAV_SLAB}[type=top]")
        else:
            b.fill((fx, base + 1, fz - 1), (fx, base + 3, fz + 1), C.AIR)
            b.set(fx, base + 1, fz - 2, f"{C.TRAV_SLAB}[type=top]")
        st = b.rng.choice(STATIONS)
        pos = (x0 + 2 - dx * 0, z0 + 2)
        b.set(x0 + 2, base + 1, z0 + 2,
              st % C.OPPOSITE[face] if "%s" in st else st)
        C.amphora(b, x1 - 2, z1 - 2, base + 1, facing=face)
        b.set(x1 - 2, base + 1, z0 + 2, "minecraft:barrel[facing=up]")
    else:
        b.place(fx, base + 1, fz, f"minecraft:spruce_door[facing={face}]")
    # Windows on the street face, every upper story.
    for f in range(1, floors):
        wy = base + f * story + 2
        if face in ("north", "south"):
            for wx in range(x0 + 2, x1 - 1, 3):
                C.window_open(b, wx, wy, fz, face)
        else:
            for wz in range(z0 + 2, z1 - 1, 3):
                C.window_open(b, fx, wy, wz, face)
    # A balcony now and then.
    if floors > 1 and b.rng.random() < 0.4:
        by = base + story + 1
        if face in ("north", "south"):
            for wx in range(fx - 2, fx + 3):
                b.set(wx, by - 1, fz + dz, f"{C.TRAV_SLAB}[type=top]")
                b.set(wx, by, fz + dz, "minecraft:spruce_fence")
        else:
            for wz in range(fz - 2, fz + 3):
                b.set(fx + dx, by - 1, wz, f"{C.TRAV_SLAB}[type=top]")
                b.set(fx + dx, by, wz, "minecraft:spruce_fence")
    # Interior stair against the back wall, holes cut per floor.
    sx = x0 + 1 if face != "west" else x1 - 1
    sz0 = z0 + 2 if face != "north" else z1 - 2
    toward = "south" if face != "north" else "north"
    tdx, tdz = C.DIRVEC[toward]
    for f in range(1, floors):
        y0 = base + 1 + (f - 1) * story
        start_z = sz0 if f % 2 == 1 else sz0 + tdz * story
        run_toward = toward if f % 2 == 1 else C.OPPOSITE[toward]
        C.stair_run(b, sx, start_z, y0, story, run_toward,
                    stair="minecraft:spruce_stairs", support=PLANKS)
        rdx, rdz = C.DIRVEC[run_toward]
        hz = start_z + rdz * (story - 1)
        b.set(sx, base + f * story, hz, C.AIR)
        b.set(sx, base + f * story, hz + rdz, C.AIR)
    # Furnishings per floor.
    for f in range(floors):
        y = base + 1 + f * story
        if f == 0 and shop:
            continue
        bed = b.rng.choice(BEDS)
        b.place(x1 - 2, y, z1 - 2 if face != "south" else z0 + 2,
                f"{bed}[facing=west]")
        C.table(b, x0 + 2, z1 - 2, y)
        b.set(x0 + 2, y, z1 - 3, "minecraft:spruce_stairs[facing=south]")
        b.set(x0 + 3, y, z0 + 1 + 1, "minecraft:chest[facing=south]")
        C.amphora(b, x1 - 3, z0 + 2, y)
        b.set(x0 + 1, y + 2, z0 + 2, "minecraft:wall_torch[facing=east]")
    # Roof: gabled or flat terrace.
    if b.rng.random() < 0.6:
        axis = "x" if (x1 - x0) >= (z1 - z0) else "z"
        C.gable_roof(b, x0 + 1, z0 + 1, x1 - 1, z1 - 1, top + 1, axis,
                     plank=STUCCO)
    else:
        b.fill((x0, top + 1, z0), (x1, top + 1, z1), C.TILE)
        for x in range(x0, x1 + 1):
            b.set(x, top + 2, z0, f"{C.TILE_SLAB}[type=bottom]")
            b.set(x, top + 2, z1, f"{C.TILE_SLAB}[type=bottom]")
        for z in range(z0, z1 + 1):
            b.set(x0, top + 2, z, f"{C.TILE_SLAB}[type=bottom]")
            b.set(x1, top + 2, z, f"{C.TILE_SLAB}[type=bottom]")


def split(rng, lo, hi, wmin=9, wmax=14):
    """Cut [lo, hi] into adjacent frontage spans."""
    spans = []
    at = lo
    while hi - at + 1 >= wmin:
        w = rng.randrange(wmin, wmax + 1)
        if hi - (at + w) + 1 < wmin:
            w = hi - at + 1
        spans.append((at, min(at + w - 1, hi)))
        at += w
    return spans


def insula_block(b, rect) -> int:
    x0, z0, x1, z1 = rect
    depth = 10
    built = 0
    # North and south rows face outward; the yard sits between.
    for bz0, bz1, face in ((z0, z0 + depth, "north"),
                           (z1 - depth, z1, "south")):
        for sx0, sx1 in split(b.rng, x0, x1):
            floors = 2 if b.rng.random() < 0.55 else 3
            unit(b, sx0, bz0, sx1, bz1, face,
                 floors=floors, shop=b.rng.random() < 0.5,
                 poor=b.rng.random() < 0.3)
            built += 1
    # Courtyard: gravel, a well, a tree.
    cy0, cy1 = z0 + depth + 1, z1 - depth - 1
    if cy1 - cy0 >= 3:
        gy = base_of(b, x0 + 2, cy0, x1 - 2, cy1)
        b.level_pad(x0 + 1, cy0, x1 - 1, cy1, gy, surface=C.GRASS,
                    wall=C.BRICK, clearance=10)
        for z in range(cy0, cy1 + 1):
            for x in range(x0 + 1, x1 - 1 + 1):
                if b.rng.random() < 0.5:
                    b.set(x, gy, z, C.mixpick(b.rng, C.GARDEN_MIX))
        wx, wz = (x0 + x1) // 2, (cy0 + cy1) // 2
        b.fill((wx, gy + 1, wz), (wx + 1, gy + 1, wz + 1), C.TRAV_WALL)
        b.fill((wx, gy, wz), (wx + 1, gy, wz + 1), C.WATER)
        b.fill((wx, gy - 1, wz), (wx + 1, gy - 1, wz + 1), C.TRAV)
        C.round_fountain
        tx = x0 + 3
        b.fill((tx, gy + 1, wz), (tx, gy + 2, wz), "minecraft:spruce_log[axis=y]")
        b.fill((tx, gy + 3, wz), (tx, gy + 5, wz),
               "minecraft:spruce_leaves[persistent=true]")
    return built


def domus(b, rect) -> None:
    """An atrium-and-peristyle townhouse filling its site."""
    x0, z0, x1, z1 = rect
    base = base_of(b, x0, z0, x1, z1)
    b.level_pad(x0, z0, x1, z1, base, surface=PLANKS, wall=C.BRICK,
                clearance=12)
    h = 5
    b.fill((x0, base + 1, z0), (x1, base + h, z1), C.BRICK)
    b.fill((x0 + 1, base + 1, z0 + 1), (x1 - 1, base + h, z1 - 1), C.AIR)
    C.mosaic_floor(b, x0 + 1, z0 + 1, x1 - 1, z1 - 1, base,
                   a="minecraft:polished_andesite", c="minecraft:calcite")
    midx = (x0 + x1) // 2
    # Fauces from the south, atrium with impluvium, then the peristyle.
    b.place(midx, base + 1, z1, "minecraft:spruce_door[facing=south]")
    az = z1 - 8                                    # atrium center line
    b.fill((midx - 1, base, az - 1), (midx + 1, base, az + 1), C.MARBLE)
    b.fill((midx - 1, base, az), (midx + 1, base, az), C.WATER)
    b.set(midx, base, az, C.WATER)
    # Tablinum partition.
    tz = z0 + 12
    b.fill((x0 + 1, base + 1, tz), (x1 - 1, base + h, tz), C.BRICK)
    b.fill((midx - 1, base + 1, tz), (midx + 1, base + 3, tz), C.AIR)
    # Peristyle garden with its colonnade.
    py0, py1 = z0 + 2, tz - 2
    b.fill((x0 + 3, base, py0 + 1), (x1 - 3, base, py1), C.GRASS)
    cells = ([(x, py0 + 1) for x in range(x0 + 3, x1 - 2)]
             + [(x, py1) for x in range(x0 + 3, x1 - 2)]
             + [(x0 + 3, z) for z in range(py0 + 2, py1)]
             + [(x1 - 3, z) for z in range(py0 + 2, py1)])
    C.thin_colonnade(b, cells, base + 1, 3, post="minecraft:quartz_wall",
                     beam=C.MARBLE, spacing=2)
    C.round_fountain(b, midx, (py0 + py1) // 2, 2, y=base)
    for gx, gz in ((x0 + 5, py0 + 3), (x1 - 5, py1 - 2)):
        b.set(gx, base + 1, gz, "minecraft:flowering_azalea")
    # Rooms: triclinium west of the atrium, cubicula east.
    C.couch(b, x0 + 2, az - 2, base + 1, "south")
    C.couch(b, x0 + 4, az + 2, base + 1, "east")
    C.table(b, x0 + 3, az, base + 1)
    b.place(x1 - 2, base + 1, az - 2, "minecraft:red_bed[facing=north]")
    b.place(x1 - 2, base + 1, az + 2, "minecraft:white_bed[facing=north]")
    for lx in (x0 + 2, x1 - 2):
        C.candelabrum(b, lx, z1 - 2, base + 1)
    C.amphora(b, midx - 2, z1 - 2, base + 1)
    C.brazier(b, midx + 3, az, base + 1)
    # Compluvium: a hip roof with the eye left open over the impluvium.
    C.hipped_roof(b, x0, z0, x1, z1, base + h + 1, overhang=1)
    b.fill((midx - 1, base + h + 1, az - 1), (midx + 1, base + h + 6, az + 1),
           C.AIR)


# ---------------------------------------------------------------------------

def macellum(b: Builder) -> None:
    (x0, z0, x1, z1), Y = P.ZONES["macellum"]
    C.paved_rect(b, x0, z0, x1, z1, C.WALK_MIX, y=Y)
    b.fill((x0, Y + 1, z0), (x1, Y + 6, z0), C.BRICK)
    b.fill((x0, Y + 1, z1), (x1, Y + 6, z1), C.BRICK)
    b.fill((x0, Y + 1, z0), (x0, Y + 6, z1), C.BRICK)
    b.fill((x1, Y + 1, z0), (x1, Y + 6, z1), C.BRICK)
    # Entrances: north from the harbor lane, south from the decumanus.
    for gz, face in ((z0, "south"), (z1, "north")):
        mid = (x0 + x1) // 2
        b.fill((mid - 2, Y + 1, gz), (mid + 2, Y + 4, gz), C.AIR)
        for s in (-2, 2):
            b.set(mid + s, Y + 4, gz, f"{C.TRAV_STAIR}"
                  f"[facing={'east' if s < 0 else 'west'},half=top]")
        b.set(mid, Y + 5, gz, C.TRAV_CHISEL)
    # Shop stalls around the inside face.
    for x in range(x0 + 3, x1 - 3, 5):
        for gz, face in ((z0 + 1, "south"), (z1 - 1, "north")):
            b.set(x, Y + 1, gz, f"{C.TRAV_SLAB}[type=top]")
            b.set(x + 1, Y + 1, gz, "minecraft:barrel[facing=up]")
            C.amphora(b, x - 1, gz, Y + 1)
    # Central tholos with cone roof and fish basin.
    cx, cz = (x0 + x1) // 2, (z0 + z1) // 2
    for dx, dz in C.ring(cx, cz, 6)[::2]:
        C.column(b, dx, dz, Y + 1, 5)
    for dx, dz in C.ring(cx, cz, 6):
        b.set(dx, Y + 6, dz, C.MARBLE)
    for r, dy in ((6, 7), (5, 7), (4, 8), (3, 8), (2, 9), (1, 9), (0, 10)):
        for dx, dz in C.ring(cx, cz, r):
            b.set(dx, Y + dy, dz, C.TILE)
        if r == 0:
            b.set(cx, Y + dy, cz, C.TILE)
    C.round_fountain(b, cx, cz, 3, y=Y)
    b.fill((x0, Y + 7, z0), (x1, Y + 7, z0), f"{C.TILE_SLAB}[type=bottom]")


def horrea(b: Builder) -> None:
    for name in ("horrea_w", "horrea_e"):
        (x0, z0, x1, z1), Y = P.ZONES[name]
        C.paved_rect(b, x0, z0, x1, z1, C.WALK_MIX, y=Y)
        # Two vaulted sheds side by side, doors north toward the quay.
        w = (x1 - x0 - 2) // 2
        for sx0 in (x0, x0 + w + 2):
            sx1 = sx0 + w
            b.fill((sx0, Y + 1, z0 + 1), (sx1, Y + 5, z1 - 1), C.BRICK)
            b.fill((sx0 + 1, Y + 1, z0 + 2), (sx1 - 1, Y + 5, z1 - 2), C.AIR)
            mid = (sx0 + sx1) // 2
            b.fill((mid - 1, Y + 1, z0 + 1), (mid + 1, Y + 3, z0 + 1), C.AIR)
            b.set(mid, Y + 4, z0 + 1, C.TRAV_CHISEL)
            for z in range(z0 + 4, z1 - 2, 4):
                C.window_open(b, sx0, Y + 4, z, "east")
                C.window_open(b, sx1, Y + 4, z, "west")
            # Stores: barrels, crates, grain sacks, pots; a loft over half.
            for z in range(z0 + 3, z1 - 2, 2):
                b.set(sx0 + 1, Y + 1, z, "minecraft:barrel[facing=up]")
                if z % 4 == 1:
                    b.set(sx0 + 1, Y + 2, z, "minecraft:barrel[facing=up]")
                b.set(sx1 - 1, Y + 1, z, "minecraft:chest[facing=west]")
            for z in range(z0 + 4, z1 - 3, 5):
                C.amphora(b, mid, z, Y + 1)
                b.set(mid - 1, Y + 1, z, "minecraft:hay_block")
            b.fill((sx0 + 1, Y + 4, z1 - 6), (sx1 - 1, Y + 4, z1 - 2), PLANKS)
            C.ladder_shaft(b, mid, z1 - 3, Y + 1, Y + 4, "north")
            b.set(mid, Y + 4, z1 - 3, C.AIR)
            C.ladder_shaft(b, mid, z1 - 3, Y + 1, Y + 4, "north")
            b.set(sx0 + 1, Y + 3, z0 + 3, "minecraft:lantern[hanging=true]")
            C.gable_roof(b, sx0, z0 + 1, sx1, z1 - 1, Y + 6, "z",
                         plank=C.BRICK)


def greenery_and_stalls(b: Builder) -> None:
    # Cypress promenade along the harbor street.
    for x in range(296, 342, 8):
        z = 353
        g = b.ground(x, z)
        b.fill((x, g + 1, z), (x, g + 2, z), "minecraft:spruce_log[axis=y]")
        b.fill((x, g + 3, z), (x, g + 7, z),
               "minecraft:spruce_leaves[persistent=true]")
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            b.fill((x + dx, g + 3, z + dz), (x + dx, g + 5, z + dz),
                   "minecraft:spruce_leaves[persistent=true]")
    # Market stalls on the quay and by the south gate.
    def stall(sx, sz, along="x"):
        g = b.ground(sx, sz)
        for i in range(3):
            x, z = (sx + i, sz) if along == "x" else (sx, sz + i)
            b.set(x, g + 1, z, f"{C.TRAV_SLAB}[type=top]")
            wool = "minecraft:red_wool" if i % 2 else "minecraft:white_wool"
            b.set(x, g + 3, z, wool)
        for i in (0, 2):
            x, z = (sx + i, sz + 1) if along == "x" else (sx + 1, sz + i)
        b.set(sx, g + 1, sz - 1 if along == "x" else sz,
              "minecraft:barrel[facing=up]")
        C.amphora(b, sx + 2 if along == "x" else sx, sz - 1, g + 1)
    stall(300, 349)
    stall(316, 349)
    stall(388, 462, "x")
    stall(404, 466, "x")
    # Neighborhood fountains.
    C.round_fountain(b, 350, 350, 3, y=63)
    C.round_fountain(b, 302, 397, 2, y=b.ground(302, 397))
    C.round_fountain(b, 392, 441, 2, y=b.ground(392, 441))
    # Banners on the four gatehouses.
    for x, z, y in ((P.CARDO_X - 4, 468, 78), (P.CARDO_X + 4, 468, 78),
                    (P.CARDO_X - 4, 303, 78), (P.CARDO_X + 4, 303, 78)):
        b.set(x, y, z, "minecraft:red_banner[rotation=0]")
    for x, z, y in ((507, P.DECUMANUS_Z - 4, 78), (507, P.DECUMANUS_Z + 4, 78),
                    (283, P.DECUMANUS_Z - 4, 78), (283, P.DECUMANUS_Z + 4, 78)):
        b.set(x, y, z, "minecraft:red_banner[rotation=0]")


def citizens(b: Builder) -> None:
    (fx0, fz0, fx1, fz1), F = P.ZONES["forum"]
    spots = [
        ("librarian", 380, 370), ("cartographer", 410, 380),
        ("mason", 400, 366), ("nitwit", 388, 378),
        ("cleric", 470, 348),                      # temple precinct
        ("armorer", P.CARDO_X - 2, 460),           # south gate guard post
        ("butcher", 316, 374), ("farmer", 310, 366),
        ("fisherman", 320, 350), ("fisherman", 340, 350),
        ("shepherd", 300, 420), ("mason", 350, 420),
        ("toolsmith", 370, 452), ("weaponsmith", 490, 420),
        ("farmer", 296, 450), ("nitwit", 360, 410),
        ("librarian", 420, 350), ("nitwit", 448, 380),
    ]
    for prof, x, z in spots:
        b.villager(prof, x, z)
    for x, z in ((398, 372), (300, 360)):
        b.spawn("minecraft:cat", x, b.ground(x, z) + 1, z,
                extra=None)
    # A little stock pen in a courtyard.
    pen = (292, 448, 300, 456)
    gy = b.ground(296, 452)
    b.fence_rect(*pen, wood="spruce", gate=(296, 448, "north"))
    for ent, ex, ez in (("minecraft:pig", 294, 452), ("minecraft:pig", 297, 454),
                        ("minecraft:chicken", 295, 450),
                        ("minecraft:chicken", 298, 451)):
        b.spawn(ent, ex, gy + 1, ez)


STAGES = [
    ("macellum", macellum),
    ("horrea", horrea),
    ("insulae", lambda b: print(f"    units built: "
                                f"{sum(insula_block(b, r) for r in INSULA_BLOCKS)}",
                                flush=True)),
    ("domus", lambda b: [domus(b, r) for r in DOMUS_SITES] and None),
    ("greenery+stalls", greenery_and_stalls),
    ("citizens", citizens),
    ("verify paths", verify_paths),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("world", nargs="?",
                    default=os.path.join(HERE, "worksite", "jw_work"))
    ap.add_argument("--seed", type=int, default=1453)
    args = ap.parse_args()
    if world_is_open(args.world):
        sys.exit("refusing: that world is open in Minecraft")
    save = MinecraftSave.load(args.world)
    b = Builder(save, seed=args.seed)
    t00 = time.time()
    for name, fn in STAGES:
        t0 = time.time()
        fn(b)
        print(f"  {name:16s} {time.time() - t0:6.1f}s  "
              f"{b.blocks_set:,} blocks", flush=True)
    fin = b.finish()
    print(f"  finish(): {fin!r}", flush=True)
    files = save.save()
    print(f"done in {time.time() - t00:.1f}s; {files} files written")


if __name__ == "__main__":
    main()
