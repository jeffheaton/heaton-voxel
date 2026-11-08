"""Secret lab M1 -- groundwork: claim the island and build the campus bones.

Stages: re-biome to plains; clear vegetation; smooth the land (the hill
keeps its shape); fill every wet column inside the walls; terrace the
pads; lay the internal roads; raise the terrain-following perimeter wall
with corner towers, seawall stretches and the single road gate; cut the
grand stair up the hill.

Jeff's road (x 728..731) is never written to.

    python tools/secret_lab/m1_groundwork.py [world] [--seed N]
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

import lab_common as C  # noqa: E402
import lab_plan as P  # noqa: E402

IX0, IZ0, IX1, IZ1 = P.INNER


def inside(rect, x, z) -> bool:
    return rect[0] <= x <= rect[2] and rect[1] <= z <= rect[3]


# ---------------------------------------------------------------------------

def climate(b: Builder) -> None:
    r = b.save.fill_biome((732, 30, 914), (855, 110, 1046), "minecraft:plains")
    print(f"    biome cells -> plains: {r.cells_filled}", flush=True)


def vegetation(b: Builder) -> None:
    n = b.clear_vegetation((732, 912), (856, 1046))
    print(f"    vegetation cleared: {n}", flush=True)


def smooth(b: Builder) -> None:
    r = b.smooth_terrain((IX0, IZ0), (IX1, IZ1), radius=4, max_shift=3,
                         margin=6)
    print(f"    {r}", flush=True)


def wetfill(b: Builder) -> None:
    """Every wet or sunken column inside the walls becomes lawn at y=64.

    The hill zone keeps its ground, except the hill-foot pond which is
    always filled -- the containment forecourt sits on it.
    """
    filled = 0
    for z in range(IZ0, IZ1 + 1):
        for x in range(IX0, IX1 + 1):
            if inside(P.HILL_ZONE, x, z) and not inside(P.WETFILL_EXTRA, x, z):
                continue
            y = b.save.surface_y(x, z)
            if y is None:
                continue
            blk = b.save.get_block(x, y, z)
            wet = blk is not None and blk.name in (C.WATER, "minecraft:ice",
                                                   "minecraft:seagrass",
                                                   "minecraft:kelp",
                                                   "minecraft:kelp_plant",
                                                   "minecraft:lily_pad")
            if not wet and y >= 64:
                continue
            floor = b.save.surface_y(x, z, "OCEAN_FLOOR")
            if floor is None or floor > 63:
                floor = 60
            if y > 64:
                b.fill((x, 65, z), (x, y + 1, z), C.AIR)
            b.fill((x, floor, z), (x, 63, z), C.DIRT)
            b.set(x, 64, z, C.GRASS)
            b.fill((x, 65, z), (x, 70, z), C.AIR)
            b._ground[(x, z)] = 64
            filled += 1
    print(f"    wet/low columns raised to lawn: {filled}", flush=True)


def pads(b: Builder) -> None:
    surfaces = {
        "plaza": C.PANEL2, "mainlab": C.GRASS, "pavilion": C.PANEL2,
        "stables": C.GRASS, "paddock": C.GRASS, "reactor": C.GRASS,
        "range": C.GRASS, "terrace": C.PANEL2, "gateyard": C.PANEL2,
    }
    for name, (rect, plane) in P.PADS.items():
        x0, z0, x1, z1 = rect
        clearance = 14 if name == "terrace" else 8
        b.level_pad(x0, z0, x1, z1, plane, surface=surfaces[name],
                    wall=C.SLATE, clearance=clearance, name=name)
        print(f"    pad {name} at y={plane}", flush=True)
    # The containment forecourt: carved into the terrace-side slope at grade.
    x0, z0, x1, z1 = P.DOOR_COURT
    for z in range(z0, z1 + 1):
        for x in range(x0, x1 + 1):
            g = b.ground(x, z)
            if g > P.PLANE:
                b.fill((x, P.PLANE + 1, z), (x, g + 1, z), C.AIR)
            elif g < P.PLANE:
                b.fill((x, g + 1, z), (x, P.PLANE - 1, z), C.DIRT)
            b.set(x, P.PLANE, z, C.TILES)
            b.fill((x, P.PLANE + 1, z), (x, P.PLANE + 8, z), C.AIR)
            b._ground[(x, z)] = P.PLANE
    print("    containment forecourt carved", flush=True)


def roads(b: Builder) -> None:
    flat = {}
    for pts, w, through in (
        (P.AVENUE, P.ROAD_W, ("gateyard", "plaza")),
        (P.NORTH_RD, P.BRANCH_W, ("plaza",)),
        (P.EAST_RD, P.BRANCH_W, ()),
        (P.PAV_RD, P.BRANCH_W, ()),
    ):
        anchors = {}
        for (ax, az), (bx, bz) in zip(pts, pts[1:]):
            steps = max(abs(bx - ax), abs(bz - az))
            for i in range(steps + 1):
                anchors[(ax + round((bx - ax) * i / steps),
                         az + round((bz - az) * i / steps))] = P.PLANE
        b.road(pts, width=w, mix=C.LAB_ROAD_MIX, lanterns=False,
               anchors=anchors, through=through)
        flat.update(anchors)
    # runway glow: sea lanterns flush in the avenue edges
    for x in range(738, 800, 8):
        b.set(x, P.PLANE, 971, C.GLOW)
        b.set(x, P.PLANE, 975, C.GLOW)
    # forecourt between avenue end and the main lab's west face
    for z in range(966, 981):
        for x in range(799, 806):
            g = b.ground(x, z)
            if g > P.PLANE:
                b.fill((x, P.PLANE + 1, z), (x, g + 1, z), C.AIR)
            elif g < P.PLANE:
                b.fill((x, g + 1, z), (x, P.PLANE - 1, z), C.DIRT)
            b.set(x, P.PLANE, z, C.mixpick(b.rng, C.WALK_MIX))
            b._ground[(x, z)] = P.PLANE
    print("    roads laid", flush=True)


def grand_stair(b: Builder) -> None:
    x0, z0, x1, z1 = P.GRAND_STAIR          # 749..757 x 986..1003
    width = x1 - x0 + 1
    # connector row from the plaza edge
    for x in range(x0, x1 + 1):
        b.fill((x, 60, 985), (x, P.PLANE - 1, 985), C.PANEL2)
        b.set(x, P.PLANE, 985, C.QUARTZ)
        b.fill((x, P.PLANE + 1, 985), (x, P.PLANE + 6, 985), C.AIR)
    y = C.flight(b, x0, 986, P.PLANE, 0, 1, 7, stair=C.QUARTZ_STAIR,
                 fill=C.PANEL2, width=width, clear=6)          # -> 73
    for z in (993, 994):
        for x in range(x0, x1 + 1):
            b.fill((x, y - 3, z), (x, y - 1, z), C.PANEL2)
            b.set(x, y, z, C.QUARTZ)
            b.fill((x, y + 1, z), (x, y + 6, z), C.AIR)
    y = C.flight(b, x0, 995, y, 0, 1, 7, stair=C.QUARTZ_STAIR,
                 fill=C.PANEL2, width=width, clear=6)          # -> 80
    for z in (1002, 1003):
        for x in range(x0, x1 + 1):
            b.fill((x, y - 3, z), (x, y - 1, z), C.PANEL2)
            b.set(x, y, z, C.QUARTZ)
            b.fill((x, y + 1, z), (x, y + 6, z), C.AIR)
    # cheek walls following the flight profile, with glowing studs
    for cx in (x0 - 1, x1 + 1):
        for z in range(985, 1004):
            if z <= 985:
                top = P.PLANE
            elif z <= 992:
                top = P.PLANE + (z - 985)
            elif z <= 994:
                top = 73
            elif z <= 1001:
                top = 73 + (z - 994)
            else:
                top = 80
            b.fill((cx, 60, z), (cx, top, z), C.PANEL2)
            b.set(cx, top + 1, z, C.BULB if z % 4 == 2 else C.SLATE_SLAB)
            b.fill((cx, top + 2, z), (cx, top + 7, z), C.AIR)
    print("    grand stair cut", flush=True)


# -- the perimeter wall ------------------------------------------------------

def _column_base(b: Builder, x: int, z: int) -> int:
    """Solid footing: the ocean floor where the wall stands in water."""
    floor = b.save.surface_y(x, z, "OCEAN_FLOOR")
    g = b.ground(x, z)
    base = min(g, floor if floor is not None else g)
    return min(base, P.PLANE - 1)


def _wall_tops(b: Builder, cells_out, cells_in) -> list[int]:
    tops = []
    for (ox, oz), (nx, nz) in zip(cells_out, cells_in):
        try:
            g_out = b.ground(ox, oz)
        except Exception:
            g_out = P.PLANE
        try:
            g_in = b.ground(nx, nz)
        except Exception:
            g_in = P.PLANE
        tops.append(max(g_out, g_in, P.PLANE) + 7)
    for _ in range(3):
        for i in range(1, len(tops)):
            if tops[i] > tops[i - 1] + 1:
                tops[i] = tops[i - 1] + 1
        for i in range(len(tops) - 2, -1, -1):
            if tops[i] > tops[i + 1] + 1:
                tops[i] = tops[i + 1] + 1
    return tops


def _wall_column(b: Builder, x: int, z: int, role: str, top: int,
                 pilaster: bool) -> None:
    base = _column_base(b, x, z) - 2
    b.fill((x, base, z), (x, P.PLANE, z), C.SLATE)
    body = C.PANEL2 if (pilaster and role == "outer") else C.PANEL
    b.fill((x, P.PLANE + 1, z), (x, top - 3, z), body)
    if role == "outer":
        b.set(x, top - 2, z, C.BULB if pilaster else C.TINTED)
    else:
        b.set(x, top - 2, z, C.PANEL)
    b.set(x, top - 1, z, C.SLATE)
    b.set(x, top, z, C.DECK)
    if role == "outer":
        b.set(x, top + 1, z, C.BLACK_WALL)
        b.fill((x, top + 2, z), (x, top + 8, z), C.AIR)
    elif role == "inner":
        b.set(x, top + 1, z, C.SLATE_SLAB)
        b.fill((x, top + 2, z), (x, top + 8, z), C.AIR)
    else:
        b.fill((x, top + 1, z), (x, top + 8, z), C.AIR)
    b._ground[(x, z)] = top


def perimeter_wall(b: Builder) -> None:
    wx0, wx1 = P.WALL_W_X
    ex0, ex1 = P.WALL_E_X
    nz0, nz1 = P.WALL_N_Z
    sz0, sz1 = P.WALL_S_Z

    # west face: skip the gate opening
    zs = [z for z in range(P.CAMPUS[1], P.CAMPUS[3] + 1)]
    tops = _wall_tops(b, [(wx0, z) for z in zs], [(wx1 + 1, z) for z in zs])
    for z, top in zip(zs, tops):
        if P.GATE_Z0 <= z <= P.GATE_Z1:
            continue
        for role, x in (("outer", wx0), ("core", wx0 + 1), ("inner", wx1)):
            _wall_column(b, x, z, role, top, pilaster=(z % 8 == 2))

    # east face (seawall down into the river where needed)
    tops = _wall_tops(b, [(ex1, z) for z in zs], [(ex0 - 1, z) for z in zs])
    for z, top in zip(zs, tops):
        for role, x in (("outer", ex1), ("core", ex0 + 1), ("inner", ex0)):
            _wall_column(b, x, z, role, top, pilaster=(z % 8 == 2))

    # north and south faces
    xs = [x for x in range(P.CAMPUS[0], P.CAMPUS[2] + 1)]
    tops = _wall_tops(b, [(x, nz0) for x in xs], [(x, nz1 + 1) for x in xs])
    for x, top in zip(xs, tops):
        for role, z in (("outer", nz0), ("core", nz0 + 1), ("inner", nz1)):
            _wall_column(b, x, z, role, top, pilaster=(x % 8 == 2))
    tops = _wall_tops(b, [(x, sz1) for x in xs], [(x, sz0 - 1) for x in xs])
    for x, top in zip(xs, tops):
        for role, z in (("outer", sz1), ("core", sz0 + 1), ("inner", sz0)):
            _wall_column(b, x, z, role, top, pilaster=(x % 8 == 2))

    print("    perimeter wall raised", flush=True)


def corner_towers(b: Builder) -> None:
    for cx, cz in P.CORNERS:
        x0 = cx if cx == P.CAMPUS[0] else cx - 6
        z0 = cz if cz == P.CAMPUS[1] else cz - 6
        x1, z1 = x0 + 6, z0 + 6
        g = max(P.PLANE, max(b.ground(x, z) for x, z in
                             ((x0, z0), (x1, z1), (x0, z1), (x1, z0))))
        top = g + 8
        for z in range(z0, z1 + 1):
            for x in range(x0, x1 + 1):
                base = _column_base(b, x, z) - 2
                b.fill((x, base, z), (x, P.PLANE, z), C.SLATE)
                edge = x in (x0, x1) or z in (z0, z1)
                b.fill((x, P.PLANE + 1, z), (x, top - 3, z),
                       C.PANEL if edge else C.DECK)
                b.set(x, top - 2, z, C.TINTED if edge else C.DECK)
                b.set(x, top - 1, z, C.SLATE if edge else C.DECK)
                b.set(x, top, z, C.DECK)
                b.fill((x, top + 1, z), (x, top + 10, z), C.AIR)
                if edge:
                    b.set(x, top + 1, z,
                          C.BULB if (x in (x0, x1) and z in (z0, z1))
                          else C.BLACK_WALL)
                b._ground[(x, z)] = top
        mx, mz = (x0 + x1) // 2, (z0 + z1) // 2
        b.set(mx, top + 1, mz, C.STEEL)
        b.set(mx, top + 2, mz, C.STEEL)
        b.fill((mx, top + 3, mz), (mx, top + 5, mz), C.BLACK_WALL)
        b.set(mx, top + 6, mz, "minecraft:lightning_rod[facing=up]")
    print("    corner towers up", flush=True)


def gate_threshold(b: Builder) -> None:
    # apron strip flush with the road curb, and the paved gate throat
    for z in range(966, 981):
        for x in (P.APRON_X0, P.APRON_X1):
            g = b.ground(x, z)
            if g > P.PLANE:
                b.fill((x, P.PLANE + 1, z), (x, g + 1, z), C.AIR)
            b.fill((x, g - 2, z), (x, P.PLANE - 1, z), C.SLATE)
            b.set(x, P.PLANE, z, C.SLATE)
            b.fill((x, P.PLANE + 1, z), (x, P.PLANE + 6, z), C.AIR)
            b._ground[(x, z)] = P.PLANE
    for z in range(P.GATE_Z0, P.GATE_Z1 + 1):
        for x in range(P.WALL_W_X[0], P.WALL_W_X[1] + 1):
            b.fill((x, 60, z), (x, P.PLANE - 1, z), C.SLATE)
            b.set(x, P.PLANE, z, C.TILES)
            b.fill((x, P.PLANE + 1, z), (x, P.PLANE + 8, z), C.AIR)
            b._ground[(x, z)] = P.PLANE
    for z in (971, 973, 975):
        b.set(735, P.PLANE, z, C.GLOW)
    # hazard-striped jambs
    for x in range(P.WALL_W_X[0], P.WALL_W_X[1] + 1):
        for z in (P.GATE_Z0 - 1, P.GATE_Z1 + 1):
            for y in range(P.PLANE + 1, P.PLANE + 8):
                b.set(x, y, z, C.HAZARD if y % 2 else C.CARBON)
    print("    gate threshold and jambs", flush=True)


def lamps(b: Builder) -> None:
    for x in range(742, 800, 12):
        C.lamp_post(b, x, 968)
        C.lamp_post(b, x + 6, 978)
    for z in range(934, 970, 12):
        C.lamp_post(b, 772, z)
    for z in range(980, 1000, 12):
        C.lamp_post(b, 804, z)
        C.lamp_post(b, 798, z + 6)
    C.lamp_post(b, 767, 1008)      # containment forecourt
    C.lamp_post(b, 760, 930)       # stable approach
    print("    lamps up", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("world", nargs="?",
                    default=os.path.join(HERE, "worksite", "jw_work"))
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    if world_is_open(args.world):
        sys.exit("REFUSING: that world is open in Minecraft.")
    t0 = time.time()
    save = MinecraftSave.load(args.world)
    b = Builder(save, seed=args.seed)

    for stage in (climate, vegetation, smooth, wetfill, pads, roads,
                  grand_stair, perimeter_wall, corner_towers, gate_threshold,
                  lamps):
        print(f"[{time.time() - t0:6.1f}s] {stage.__name__}", flush=True)
        stage(b)

    print(f"[{time.time() - t0:6.1f}s] finish()", flush=True)
    print(f"    {b.finish()}", flush=True)
    n = save.save()
    print(f"[{time.time() - t0:6.1f}s] saved {n} files; "
          f"{b.blocks_set:,} blocks set", flush=True)


if __name__ == "__main__":
    main()
