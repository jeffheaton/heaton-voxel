"""JeffWorld groundwork: claim the site and build the city's bones.

Stages: re-biome to plains and melt the snow; clear vegetation; drain the
swamp ponds; smooth the meadow; terrace the monument pads; lay the smoothed
streets; raise terrain-following walls with towers and four gates; cut the
harbor basin with quay, mole and pharos; and run the aqueduct in from the
southern hill, crossing Jeff's path on one tall arch.

Jeff's stone-brick paths (z 472-473 and x 512-513) are never written to.

    python tools/roman_city/jw_groundwork.py [world] [--seed N]
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

X0, Z0, X1, Z1 = P.CITY
WATERY = ("minecraft:water", "minecraft:ice", "minecraft:lily_pad",
          "minecraft:seagrass", "minecraft:tall_seagrass", "minecraft:kelp",
          "minecraft:kelp_plant")


def in_harbor(x, z):
    hx0, hz0, hx1, hz1 = P.HARBOR_KEEP
    return hx0 <= x <= hx1 and hz0 <= z <= hz1


# ---------------------------------------------------------------------------

def climate(b: Builder) -> None:
    r = b.save.fill_biome((X0 - 10, 40, Z0 - 22), (X1 + 6, 100, Z1 + 5),
                          "minecraft:plains")
    r2 = b.save.fill_biome((P.AQUA_X - 10, 40, Z1 + 1),
                           (P.AQUA_X + 10, 100, P.AQUA_SOURCE_Z + 8),
                           "minecraft:plains")
    print(f"    biome cells: {r.cells_filled + r2.cells_filled}", flush=True)
    # Ice to water ahead of the drain/dredge passes.
    melted = 0
    for z in range(Z0 - 20, Z1 + 1):
        for x in range(X0 - 4, X1 + 1):
            y = b.save.surface_y(x, z)
            if y is None:
                continue
            blk = b.save.get_block(x, y, z)
            if blk is not None and blk.name == "minecraft:ice":
                b.set(x, y, z, "minecraft:water")
                melted += 1
    print(f"    ice melted: {melted}", flush=True)


def vegetation(b: Builder) -> None:
    n = b.clear_vegetation((X0 - 6, Z0 - 8), (X1 + 4, Z1 + 1))
    n += b.clear_vegetation((P.AQUA_X - 8, Z1 + 4),
                            (P.AQUA_X + 8, P.AQUA_SOURCE_Z + 8))
    # Snowy grass keeps its white cap as stored state; scrub it.
    fixed = 0
    for z in range(Z0 - 8, Z1 + 2):
        for x in range(X0 - 6, X1 + 5):
            y = b.ground(x, z)
            blk = b.save.get_block(x, y, z)
            if blk is not None and blk.name == "minecraft:grass_block" \
                    and blk.properties.get("snowy") == "true":
                b.set(x, y, z, C.GRASS)
                fixed += 1
    print(f"    vegetation cleared: {n}, snowy grass scrubbed: {fixed}",
          flush=True)


def drain(b: Builder) -> None:
    filled = 0
    for z in range(Z0, Z1 + 1):
        for x in range(X0, X1 + 1):
            if in_harbor(x, z):
                continue
            y = b.save.surface_y(x, z)
            if y is None:
                continue
            blk = b.save.get_block(x, y, z)
            if blk is None or blk.name not in WATERY:
                continue
            floor = b.save.surface_y(x, z, "OCEAN_FLOOR")
            if floor is None or floor >= 62:
                floor = 58
            if y > 62:
                b.fill((x, 63, z), (x, y, z), C.AIR)
            b.fill((x, floor, z), (x, 61, z), C.DIRT)
            b.set(x, 62, z, C.GRASS)
            b._ground[(x, z)] = 62
            filled += 1
    print(f"    pond columns filled: {filled}", flush=True)


def smooth(b: Builder) -> None:
    r = b.smooth_terrain((X0 + 2, Z0 + 2), (X1 - 2, Z1 - 2),
                         radius=4, max_shift=3, margin=6)
    print(f"    {r}", flush=True)


def pads(b: Builder) -> None:
    for name in P.ZONES:
        (x0, z0, x1, z1), y = P.ZONES[name]
        b.level_pad(x0, z0, x1, z1, y, wall=C.TRAV_CUT, clearance=10,
                    name=name)
        print(f"    pad {name} at y={y}", flush=True)


def streets(b: Builder) -> None:
    for s0, s1 in P.CARDO_SEGS:
        b.road([(P.CARDO_X, s0), (P.CARDO_X, s1)], width=P.STREET_W,
               mix=C.VIA_MIX, lanterns=False)
    b.road([(X0 + 2, P.DECUMANUS_Z), (X1 - 2, P.DECUMANUS_Z)],
           width=P.STREET_W, mix=C.VIA_MIX, lanterns=False)
    b.road([(P.MINOR_X, Z0 + 6), (P.MINOR_X, Z1 - 5)], width=4,
           mix=C.LANE_MIX, lanterns=False)
    b.road([(288, 352), (352, 352)], width=4, mix=C.LANE_MIX, lanterns=False)
    b.road([(288, 440), (390, 440)], width=4, mix=C.LANE_MIX, lanterns=False)
    b.road([(500, 310), (500, 464)], width=4, mix=C.LANE_MIX, lanterns=False)
    b.road([(384, 306), (440, 306)], width=4, mix=C.LANE_MIX, lanterns=False)
    # Roman street lamps along the axes, clear of the crossing.
    for s0, s1 in P.CARDO_SEGS:
        for z in range(min(s0, s1) + 8, max(s0, s1) - 4, 22):
            if abs(z - P.DECUMANUS_Z) < 6:
                continue
            y = b.ground(P.CARDO_X + 4, z)
            C.lamp_post(b, P.CARDO_X + 4, z, y)
    for x in range(X0 + 10, X1 - 8, 26):
        if abs(x - P.CARDO_X) < 6:
            continue
        y = b.ground(x, P.DECUMANUS_Z + 4)
        C.lamp_post(b, x, P.DECUMANUS_Z + 4, y)


# -- walls -------------------------------------------------------------------

def smoothed_line(b, cells):
    """Ground heights along a path, averaged and clamped to 1-block steps."""
    hs = [b.ground(x, z) for x, z in cells]
    out = []
    for i in range(len(hs)):
        lo, hi = max(0, i - 5), min(len(hs), i + 6)
        out.append(round(sum(hs[lo:hi]) / (hi - lo)))
    for _ in range(4):
        for i in range(1, len(out)):
            if out[i] > out[i - 1] + 1:
                out[i] = out[i - 1] + 1
        for i in range(len(out) - 2, -1, -1):
            if out[i] > out[i + 1] + 1:
                out[i] = out[i + 1] + 1
    return out


WALL_SEGS = {
    # name: (fixed axis value of outer face, along-axis lo..hi, orientation)
    "south": (471, (280, 510), "x"),
    "east": (510, (300, 471), "z"),
    "north": (300, (P.NORTH_WALL_X0, 510), "x"),
    "west": (280, (P.WEST_WALL_Z0, 471), "z"),
}
GATES = {                      # per segment: gap center on the along-axis
    "south": P.CARDO_X,
    "east": P.DECUMANUS_Z,
    "west": P.DECUMANUS_Z,
    "north": P.CARDO_X,
}


def seg_cell(name, u, t):
    """Global (x, z) for along-coordinate u, t blocks inward from the face."""
    face, _, orient = WALL_SEGS[name]
    inward = {"south": -1, "east": -1, "north": 1, "west": 1}[name]
    if orient == "x":
        return u, face + inward * t
    return face + inward * t, u


def city_walls(b: Builder) -> None:
    for name, (face, (u0, u1), orient) in WALL_SEGS.items():
        cells = [seg_cell(name, u, 1) for u in range(u0, u1 + 1)]
        line = smoothed_line(b, cells)
        gate = GATES[name]
        for i, u in enumerate(range(u0, u1 + 1)):
            if abs(u - gate) <= 5:
                continue                      # gatehouse rebuilds this
            base = line[i]
            for t in range(P.WALL_T):
                x, z = seg_cell(name, u, t)
                g = b.ground(x, z)
                b.fill((x, min(g - 2, base - 2), z), (x, base, z), C.TRAV_RAW)
                b.fill((x, base + 1, z), (x, base + P.WALL_H, z), C.BRICK)
                if t == 1:
                    b.set(x, base + P.WALL_H, z, C.TRAV)   # walk course
            for yb in (base + 4, base + 7):               # relieving bands
                for t in range(P.WALL_T):
                    x, z = seg_cell(name, u, t)
                    b.set(x, yb, z, C.TRAV_RAW)
            ox, oz = seg_cell(name, u, 0)
            b.set(ox, base + P.WALL_H + 1, oz, C.BRICK)   # parapet
            if u % 2 == 0:
                b.set(ox, base + P.WALL_H + 2, oz, C.BRICK)
            ix, iz = seg_cell(name, u, P.WALL_T - 1)
            b.set(ix, base + P.WALL_H + 1, iz,
                  f"{C.BRICK_SLAB}[type=bottom]")

        # Towers along the run and at the ends, clamped so no tower ever
        # reaches past its own segment (the paths run just beyond two of
        # the corners).
        end_towers = [u0 + 3, u1 - 3]
        field = [u for u in range(u0 + P.TOWER_EVERY,
                                  u1 - P.TOWER_EVERY // 2, P.TOWER_EVERY)
                 if abs(u - gate) > 12]
        for u in end_towers + field:
            grand = u in end_towers
            r = 4 if grand else 3
            uc = max(u0 + r, min(u, u1 - r))
            i = max(0, min(uc - u0, len(line) - 1))
            wall_tower(b, name, uc, line[i], grand=grand)


# How far each segment's towers may project outward: never toward a path.
PROJ = {"south": 0, "east": 0, "north": 2, "west": 2}


def wall_tower(b: Builder, seg, u, base, *, grand=False) -> None:
    r = 4 if grand else 3
    top = base + P.WALL_H + (7 if grand else 4)
    cx, cz = seg_cell(seg, u, r - PROJ[seg])
    x0, z0, x1, z1 = cx - r, cz - r, cx + r, cz + r
    g = min(b.ground(x, z) for x in (x0, x1) for z in (z0, z1))
    b.fill((x0, min(g - 2, base - 2), z0), (x1, base, z1), C.TRAV_RAW)
    b.fill((x0, base + 1, z0), (x1, top, z1), C.BRICK)
    b.fill((x0 + 1, base + 1, z0 + 1), (x1 - 1, top - 1, z1 - 1), C.AIR)
    b.fill((x0 + 1, base + P.WALL_H - 1, z0 + 1),
           (x1 - 1, base + P.WALL_H - 1, z1 - 1), "minecraft:spruce_planks")
    # Doorways where the wall walk passes through.
    walk = base + P.WALL_H + 1
    if seg in ("south", "north"):
        b.fill((x0, walk, cz), (x0, walk + 2, cz), C.AIR)
        b.fill((x1, walk, cz), (x1, walk + 2, cz), C.AIR)
    else:
        b.fill((cx, walk, z0), (cx, walk + 2, z0), C.AIR)
        b.fill((cx, walk, z1), (cx, walk + 2, z1), C.AIR)
    # Ladder from the ground, hatches through the mid floor and the deck.
    lx, lz = cx, cz
    b.set(lx, base + P.WALL_H - 1, lz, C.AIR)
    b.set(lx, top, lz, C.AIR)
    C.ladder_shaft(b, lx, lz, g + 1, top, "north")
    per = ([(x, z0) for x in range(x0, x1 + 1)]
           + [(x, z1) for x in range(x0, x1 + 1)]
           + [(x0, z) for z in range(z0 + 1, z1)]
           + [(x1, z) for z in range(z0 + 1, z1)])
    if grand:
        # No eaves overhang: several of these stand flush against the paths.
        C.hipped_roof(b, x0, z0, x1, z1, top + 1, overhang=0)
    else:
        for i, (x, z) in enumerate(per):
            b.set(x, top + 1, z, C.BRICK)
            if i % 2 == 0:
                b.set(x, top + 2, z, C.BRICK)


def gates(b: Builder) -> None:
    for seg, gate in GATES.items():
        face, (u0, u1), orient = WALL_SEGS[seg]
        cells = [seg_cell(seg, gate, 1)]
        base = smoothed_line(b, [seg_cell(seg, u, 1)
                                 for u in range(gate - 8, gate + 9)])[8]
        inward = {"south": -1, "east": -1, "north": 1, "west": 1}[seg]
        # Gatehouse mass flush with the outer face -- it may never reach a
        # path -- and running six deep into the city.
        near, far = face, face + inward * 6
        if orient == "x":
            gx0, gx1 = gate - 6, gate + 6
            gz0, gz1 = sorted((near, far))
        else:
            gz0, gz1 = gate - 6, gate + 6
            gx0, gx1 = sorted((near, far))
        g = min(b.ground(x, z) for x in (gx0, gx1) for z in (gz0, gz1))
        b.fill((gx0, g - 2, gz0), (gx1, base, gz1), C.TRAV_RAW)
        b.fill((gx0, base + 1, gz0), (gx1, base + P.WALL_H + 4, gz1), C.BRICK)
        # The arch: one grand passage the width of the street.
        axis = "z" if orient == "x" else "x"
        mid = face + inward * 3
        if orient == "x":
            C.arch_portal(b, gate, base + 1, mid, axis, 7, 7, 7)
            C.paved_rect(b, gate - 3, min(face, face + inward * 8),
                         gate + 3, max(face, face + inward * 8),
                         C.VIA_MIX, y=base)
        else:
            C.arch_portal(b, mid, base + 1, gate, axis, 7, 7, 7)
            C.paved_rect(b, min(face, face + inward * 8), gate - 3,
                         max(face, face + inward * 8), gate + 3,
                         C.VIA_MIX, y=base)
        # Crown and flanking turrets.
        top = base + P.WALL_H + 4
        if orient == "x":
            per = [(x, gz0) for x in range(gx0, gx1 + 1)] + \
                  [(x, gz1) for x in range(gx0, gx1 + 1)]
        else:
            per = [(gx0, z) for z in range(gz0, gz1 + 1)] + \
                  [(gx1, z) for z in range(gz0, gz1 + 1)]
        for i, (x, z) in enumerate(per):
            b.set(x, top + 1, z, C.BRICK)
            if i % 2 == 0:
                b.set(x, top + 2, z, C.BRICK)
        for du in (-9, 9):
            wall_tower(b, seg, gate + du, base, grand=True)


# -- harbor -----------------------------------------------------------------

def harbor(b: Builder) -> None:
    hx0, hz0, hx1, hz1 = P.HARBOR_KEEP
    dredged = 0
    for z in range(hz0, P.QUAY_Z):
        for x in range(hx0, P.QUAY_X):
            # Heightmaps are stale mid-build; the Builder's ground cache is
            # the truth the earlier stages maintained.
            try:
                g = b.ground(x, z)
            except Exception:
                continue
            if g >= 63:                         # islet, spit or shore: cut it
                b.fill((x, 63, z), (x, g + 2, z), C.AIR)
            b.set(x, P.DREDGE_FLOOR, z, "minecraft:gravel")
            b.fill((x, P.DREDGE_FLOOR + 1, z), (x, 62, z), C.WATER)
            b._ground[(x, z)] = 62
            dredged += 1
    print(f"    basin columns: {dredged}", flush=True)
    # Quays: south face along z=QUAY_Z, east face along x=QUAY_X.
    for x in range(hx0, P.QUAY_X + 1):
        b.fill((x, P.DREDGE_FLOOR, P.QUAY_Z), (x, 62, P.QUAY_Z), C.TRAV_CUT)
        b.set(x, 63, P.QUAY_Z, C.TRAV)
    for z in range(hz0, P.QUAY_Z + 1):
        b.fill((P.QUAY_X, P.DREDGE_FLOOR, z), (P.QUAY_X, 62, z), C.TRAV_CUT)
        b.set(P.QUAY_X, 63, z, C.TRAV)
    for x in range(hx0 + 2, P.QUAY_X - 2, 6):
        b.set(x, 64, P.QUAY_Z, C.TRAV_WALL)     # bollards
    for z in range(hz0 + 4, P.QUAY_Z - 2, 6):
        b.set(P.QUAY_X, 64, z, C.TRAV_WALL)
    # Paved aprons behind both quays, leveled at 63.
    for z in range(P.QUAY_Z + 1, P.QUAY_Z + 7):
        for x in range(hx0, P.QUAY_X + 7):
            g = b.ground(x, z)
            if g > 63:
                b.fill((x, 64, z), (x, g + 1, z), C.AIR)
            elif g < 63:
                b.fill((x, g, z), (x, 62, z), C.DIRT)
            b.set(x, 63, z, C.mixpick(b.rng, C.WALK_MIX))
            b._ground[(x, z)] = 63
    for z in range(hz0, P.QUAY_Z + 1):
        for x in range(P.QUAY_X + 1, P.QUAY_X + 7):
            g = b.ground(x, z)
            if g > 63:
                b.fill((x, 64, z), (x, g + 1, z), C.AIR)
            elif g < 63:
                b.fill((x, g, z), (x, 62, z), C.DIRT)
            b.set(x, 63, z, C.mixpick(b.rng, C.WALK_MIX))
            b._ground[(x, z)] = 63
    # Wooden pier out into the basin.
    for z in range(334, P.QUAY_Z):
        for x in range(328, 331):
            b.set(x, 63, z, "minecraft:spruce_planks")
        if z % 3 == 1:
            for x in (328, 330):
                b.fill((x, P.DREDGE_FLOOR + 1, z), (x, 62, z),
                       "minecraft:spruce_fence")
    # The mole, and the pharos at its tip.
    pts = [(P.QUAY_X, 299)] + list(P.MOLE)
    for (ax, az), (bx, bz) in zip(pts, pts[1:]):
        steps = max(abs(bx - ax), abs(bz - az))
        if steps == 0:
            continue
        for i in range(steps + 1):
            mx = ax + round((bx - ax) * i / steps)
            mz = az + round((bz - az) * i / steps)
            for dx in range(-2, 2):
                for dz in range(-2, 2):
                    x, z = mx + dx, mz + dz
                    surf = b.save.surface_y(x, z)
                    if surf is not None and surf > 63:   # islet in the way
                        b.fill((x, 64, z), (x, surf + 1, z), C.AIR)
                    floor = b.save.surface_y(x, z, "OCEAN_FLOOR") or 56
                    b.fill((x, floor, z), (x, 62, z), C.TRAV_RAW)
                    b.set(x, 63, z, C.TRAV)
    px, pz = P.MOLE[-1]
    for dx, dz in C.disc(px, pz, 3):
        floor = b.save.surface_y(dx, dz, "OCEAN_FLOOR") or 56
        b.fill((dx, floor, dz), (dx, 63, dz), C.TRAV_RAW)
    for dx, dz in C.disc(px, pz, 2):
        b.fill((dx, 64, dz), (dx, 74, dz), C.BRICK)
    for dx, dz in C.disc(px, pz, 1):
        b.fill((dx, 64, dz), (dx, 74, dz), C.AIR)
    C.ladder_shaft(b, px, pz, 64, 74, "north")
    for y in (68, 72):
        b.fill((px, y, pz - 2), (px, y + 1, pz - 2), C.AIR)
    for dx, dz in C.ring(px, pz, 2):
        b.set(dx, 75, dz, C.TRAV)
    b.fill((px - 1, 75, pz - 1), (px + 1, 75, pz + 1), C.TRAV)
    b.set(px, 76, pz, "minecraft:campfire[lit=true,facing=north]")
    for dx, dz in C.ring(px, pz, 2)[::2]:
        b.set(dx, 76, dz, C.BRICK_WALL)
    # Boats at the pier.
    b.spawn("minecraft:oak_boat", 326.5, 63.0, 338.5)
    b.spawn("minecraft:oak_boat", 333.5, 63.0, 342.5)


# -- aqueduct ----------------------------------------------------------------

def aqueduct(b: Builder) -> None:
    ax = P.AQUA_X
    deck = 73                       # channel floor through the arcade
    # Springhouse on the hill.
    sz = P.AQUA_SOURCE_Z
    g = b.ground(ax, sz)
    b.fill((ax - 4, g - 1, sz - 4), (ax + 4, g + 4, sz + 4), C.TRAV_RAW)
    b.fill((ax - 3, g + 1, sz - 3), (ax + 3, g + 4, sz + 3), C.AIR)
    b.fill((ax - 3, g, sz - 3), (ax + 3, g, sz + 3), C.TRAV)
    b.fill((ax - 1, g + 1, sz - 1), (ax + 1, g + 1, sz + 1), C.WATER)
    C.hipped_roof(b, ax - 4, sz - 4, ax + 4, sz + 4, g + 5, overhang=1)
    b.fill((ax - 1, g + 1, sz - 4), (ax + 1, g + 3, sz - 4), C.AIR)
    b.set(ax, g + 3, sz - 4, f"{C.TRAV_STAIR}[facing=south,half=top]")
    # Arcade north to the wall, water channel on top; piers skip the path.
    for z in range(sz - 5, 467, -1):
        pier_row = (z % 6) in (0, 1) and not (469 <= z <= 476)
        for dx in (-1, 0, 1):
            x = ax + dx
            if pier_row:
                gz = b.ground(x, z)
                b.fill((x, gz + 1, z), (x, deck - 1, z), C.TRAV_RAW)
            else:
                b.fill((x, deck - 3, z), (x, deck - 1, z), C.TRAV)
        b.set(ax - 1, deck, z, C.TRAV)
        b.set(ax + 1, deck, z, C.TRAV)
        b.set(ax, deck, z, C.WATER)
        b.set(ax - 1, deck - 1, z, C.TRAV)
        b.set(ax + 1, deck - 1, z, C.TRAV)
        b.set(ax, deck - 1, z, C.TRAV)
    # Soffit stairs to spring the big arch over Jeff's path.
    for z, facing in ((476, "south"), (469, "north")):
        for dx in (-1, 0, 1):
            b.set(ax + dx, deck - 4, z,
                  f"{C.TRAV_STAIR}[facing={facing},half=top]")
    # Castellum inside the south-east corner.
    (cx0, cz0, cx1, cz1), py = P.ZONES["castellum"]
    b.fill((cx0, py, cz0), (cx1, py + 8, cz1), C.BRICK)
    b.fill((cx0, py, cz0), (cx1, py + 1, cz1), C.TRAV_RAW)
    b.fill((cx0 + 1, py + 6, cz0 + 1), (cx1 - 1, py + 8, cz1 - 1), C.AIR)
    b.fill((cx0 + 1, py + 5, cz0 + 1), (cx1 - 1, py + 5, cz1 - 1), C.TRAV)
    b.fill((cx0 + 1, py + 6, cz0 + 1), (cx1 - 1, py + 7, cz1 - 1), C.WATER)
    b.fill((cx0, py + 9, cz0), (cx1, py + 9, cz1), C.TRAV)
    b.fill((cx0 + 1, py + 9, cz0 + 1), (cx1 - 1, py + 9, cz1 - 1), C.AIR)
    # Channel from the wall to the castellum tank, arcaded like the rest.
    for z in range(467, cz1, -1):
        if (z % 5) in (0, 1):
            for dx in (-1, 0, 1):
                gz = b.ground(ax + dx, z)
                b.fill((ax + dx, gz + 1, z), (ax + dx, deck - 3, z),
                       C.TRAV_RAW)
        for dx in (-1, 0, 1):
            b.fill((ax + dx, deck - 2, z), (ax + dx, deck - 1, z), C.TRAV)
        b.set(ax - 1, deck, z, C.TRAV)
        b.set(ax + 1, deck, z, C.TRAV)
        b.set(ax, deck, z, C.WATER)
    # Spout and basin on the castellum's west face.
    b.fill((cx0, py + 3, 445), (cx0, py + 4, 445), C.AIR)
    b.set(cx0, py + 4, 445, C.WATER)
    C.basin(b, cx0 - 5, 442, cx0, 448, y=py - 1)


def verify_paths(b: Builder) -> None:
    """Jeff's paths must survive untouched: every path brick identical, and
    nothing new built into the air over them (the aqueduct deck crossing at
    x 474..480, ten blocks up, is the one sanctioned exception)."""
    pristine = MinecraftSave.load(os.path.join(HERE, "worksite",
                                               "jw_snapshot"))
    bad = []

    def check(x, y, z, over_ok):
        p = pristine.get_block(x, y, z)
        a = b.save.get_block(x, y, z)
        if p is None or a is None:
            return
        if p.name == "minecraft:stone_bricks" and str(a) != str(p):
            bad.append(("brick", x, y, z, str(p), str(a)))
        elif p.name == "minecraft:air" and a.name != "minecraft:air" \
                and not over_ok:
            bad.append(("intrusion", x, y, z, str(p), str(a)))

    for z in (472, 473):
        for x in range(276, 515):
            for y in range(55, 86):
                check(x, y, z, over_ok=474 <= x <= 480 and y >= 69)
    for x in (512, 513):
        for z in range(296, 475):
            for y in range(55, 86):
                check(x, y, z, over_ok=False)
    if bad:
        for entry in bad[:12]:
            print("    CLOBBERED:", entry, flush=True)
        raise SystemExit(f"path verification failed: {len(bad)} cells; "
                         f"NOT saving")
    print("    both paths verified untouched", flush=True)


STAGES = [
    ("climate", climate),
    ("vegetation", vegetation),
    ("drain", drain),
    ("smooth", smooth),
    ("pads", pads),
    ("streets", streets),
    ("city walls", city_walls),
    ("gates", gates),
    ("harbor", harbor),
    ("aqueduct", aqueduct),
    ("verify paths", verify_paths),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("world", nargs="?",
                    default=os.path.join(HERE, "worksite", "jw_work"))
    ap.add_argument("--seed", type=int, default=753)   # ab urbe condita
    args = ap.parse_args()
    if world_is_open(args.world):
        sys.exit("refusing: that world is open in Minecraft")
    save = MinecraftSave.load(args.world)
    b = Builder(save, seed=args.seed)
    t00 = time.time()
    for name, fn in STAGES:
        t0 = time.time()
        fn(b)
        print(f"  {name:12s} {time.time() - t0:6.1f}s  "
              f"{b.blocks_set:,} blocks", flush=True)
    fin = b.finish()
    print(f"  finish(): {fin!r}", flush=True)
    files = save.save()
    print(f"done in {time.time() - t00:.1f}s; {files} files written")


if __name__ == "__main__":
    main()
