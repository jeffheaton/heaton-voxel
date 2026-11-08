"""Plan the sea-pickle clusters that finish lighting the lake. Read-only.

The lake is the wide basin the player stands on, not the canals that
leave it: the water mask is opened (eroded, then dilated back) so
channels narrower than five blocks fall away, and the body is the piece
that contains the player. Every surface cell of that body that is open
to the sky must end at block light >= 11 -- the level ice melts at, one
above the dead band where nothing freezes and nothing thaws.

Pickles go where Jeff put his: four to a cluster, in the lowest water
cell of a column, which in a column one block deep is the surface cell
itself. Nothing is planned deeper than y=60, on anything but natural
ground, or under a bridge. The lattice is a first guess; the plan is
then checked by the same flood the game runs and patched until the
flood says every cell is lit.

Writes plan.json and plan_map.txt beside this file.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from heaton_voxel.world import MinecraftSave                     # noqa: E402
from heaton_voxel.craft import simulate_block_light              # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WORLD = os.path.expanduser("~/Library/Application Support/minecraft/saves/JeffWorld")
SURFACE = 62
MELT = 11
BOX = (-392, 152, -278, 288)        # x0, z0, x1, z1: generous; the mask decides
OPEN_RADIUS = 2                     # channels up to 2*2 wide fall away
LOWEST_PICKLE = 60                  # never deeper: light would not reach 11
PICKLE = "minecraft:sea_pickle[pickles=4,waterlogged=true]"
LAKE_TOP = {"minecraft:water", "minecraft:ice"}
IN_WATER = {"minecraft:water", "minecraft:sea_pickle", "minecraft:seagrass",
            "minecraft:tall_seagrass", "minecraft:kelp", "minecraft:kelp_plant"}
GROUND = {"minecraft:sand", "minecraft:dirt", "minecraft:grass_block", "minecraft:clay",
          "minecraft:stone", "minecraft:gravel", "minecraft:coarse_dirt",
          "minecraft:podzol", "minecraft:mud", "minecraft:cobblestone",
          "minecraft:andesite", "minecraft:diorite", "minecraft:granite",
          "minecraft:tuff", "minecraft:deepslate", "minecraft:coal_ore",
          "minecraft:iron_ore", "minecraft:copper_ore", "minecraft:rooted_dirt"}


def main() -> int:
    save = MinecraftSave.load(WORLD)
    px, py, pz = save.player_block_position()
    x0, z0, x1, z1 = BOX

    def name(x, y, z):
        block = save.get_block(x, y, z)
        return None if block is None else block.name

    def state(x, y, z):
        return save.get_block(x, y, z)

    # -- the water mask, opened, and the body the player stands on -------
    mask = set()
    for z in range(z0, z1 + 1):
        for x in range(x0, x1 + 1):
            top = state(x, SURFACE, z)
            if top is None:
                continue
            if top.name in LAKE_TOP or (top.name == "minecraft:sea_pickle"
                                        and top.properties.get("waterlogged") == "true"):
                mask.add((x, z))
    r = OPEN_RADIUS
    eroded = {(x, z) for (x, z) in mask
              if all((x + dx, z + dz) in mask
                     for dx in range(-r, r + 1) for dz in range(-r, r + 1))}
    if (px, pz) not in eroded:
        raise SystemExit(f"the player at {(px, pz)} is not on open water")
    body = {(px, pz)}
    queue = deque([(px, pz)])
    while queue:
        x, z = queue.popleft()
        for n in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
            if n in eroded and n not in body:
                body.add(n)
                queue.append(n)
    lake = {(x, z) for (x, z) in mask
            if any((x + dx, z + dz) in body
                   for dx in range(-r, r + 1) for dz in range(-r, r + 1))}
    print(f"water cells in box {len(mask)}, lake body {len(lake)} "
          f"(dropped {len(mask) - len(lake)} in canals and other water)")

    # -- every column of the lake --------------------------------------
    columns = {}
    for (x, z) in sorted(lake, key=lambda c: (c[1], c[0])):
        y = SURFACE - 1
        while y > SURFACE - 40 and name(x, y, z) in IN_WATER:
            y -= 1
        top = state(x, SURFACE, z)
        column = {"top": top.name, "bed_y": y, "bed": name(x, y, z),
                  "above": name(x, SURFACE + 1, z)}
        # The one cell a pickle may take: the lowest water of the column,
        # or the surface cell itself when that is all there is.
        yp = y + 1
        cell = state(x, yp, z)
        cell_text = None if cell is None else str(cell)
        column["pickle_y"] = None
        # Source water only: a pickle dropped into a flowing or falling cell
        # would waterlog itself with a source and change what flows there.
        if (yp >= LOWEST_PICKLE and column["bed"] in GROUND
                and column["above"] == "minecraft:air"
                and (cell_text == "minecraft:water[level=0]"
                     or (yp == SURFACE and cell_text == "minecraft:ice"))):
            column["pickle_y"] = yp
        columns[(x, z)] = column
    # Ice sitting directly on air is a lid over a pocket -- a room or a
    # cave under the lake floor -- and the one thing that must NOT melt.
    # Ice melts at block light 11, so every lid stays at 10 or less, and a
    # cluster may not stand closer to a lid than its light needs to fall to
    # 10 by the time it arrives: 5 blocks for a surface cluster (15), 4 for
    # one a block down (14 at the surface), 3 for one two blocks down.
    lids = {(x, z) for (x, z), col in columns.items()
            if col["top"] == "minecraft:ice" and col["bed"] == "minecraft:air"
            and col["bed_y"] == SURFACE - 1}
    print(f"lids (ice over air): {len(lids)}")

    def lid_distance(x, z):
        return min((abs(x - lx) + abs(z - lz) for lx, lz in lids), default=99)

    for (x, z), col in columns.items():
        yp = col["pickle_y"]
        if yp is not None and lid_distance(x, z) < 5 - (SURFACE - yp):
            col["pickle_y"] = None
            col["too_close"] = True
    depth = Counter(SURFACE - 1 - c["bed_y"] for c in columns.values())
    print("depth below the surface cell:", sorted(depth.items()))
    print("bed:", Counter(c["bed"] for c in columns.values()).most_common(8))
    print("above:", Counter(c["above"] for c in columns.values()).most_common(6))
    candidates = {c: col["pickle_y"] for c, col in columns.items() if col["pickle_y"]}
    print(f"columns that may take a pickle: {len(candidates)} of {len(columns)}; "
          f"by height {sorted(Counter(candidates.values()).items())}")

    # Cells that must end lit: open to the sky, and still water or ice.
    targets = [(x, SURFACE, z) for (x, z), col in columns.items()
               if col["top"] in LAKE_TOP and col["above"] == "minecraft:air"
               and (x, z) not in lids]
    exempt = len(columns) - len(targets)
    print(f"targets {len(targets)}; exempt (roofed, a pickle, or a lid) {exempt}")
    lid_cells = [(x, SURFACE, z) for x, z in sorted(lids)]

    def reach(yp):
        return 15 - (SURFACE - yp) - MELT   # taxicab radius that still reads MELT

    def covers(cand, cell):
        (cx, cz), yp = cand
        return abs(cx - cell[0]) + abs(cz - cell[2]) <= reach(yp)

    plan: dict[tuple[int, int], int] = {}

    # A lattice at the common reach, phase-locked to world coordinates.
    step = 2 * reach(SURFACE - 1)
    for (x, z), yp in candidates.items():
        if (x + z) % step == 0 and (x - z) % step == 0:
            plan[(x, z)] = yp
    print(f"lattice: {len(plan)} clusters")

    baseline = simulate_block_light(save, (x0, 55, z0), (x1, 66, z1))
    dark0 = baseline.below(targets, MELT)
    print(f"before anything: {len(dark0)} of {len(targets)} targets below {MELT}")

    def overlay():
        return {(x, yp, z): PICKLE for (x, z), yp in plan.items()}

    leftovers = []
    for round_number in range(1, 16):
        field = simulate_block_light(save, (x0, 55, z0), (x1, 66, z1), overlay=overlay())
        dark = field.below(targets, MELT)
        print(f"round {round_number}: {len(plan)} clusters, {len(dark)} dark targets")
        if not dark:
            leftovers = []
            break
        dark_set = {(x, y, z) for x, y, z, _ in dark}
        fixed = set()
        added = 0
        for x, y, z, _ in sorted(dark, key=lambda c: (c[2], c[0])):
            if (x, y, z) in fixed:
                continue
            best, best_score = None, None
            for dx in range(-4, 5):
                for dz in range(-4, 5):
                    cand = (x + dx, z + dz)
                    yp = candidates.get(cand)
                    if yp is None or cand in plan or not covers((cand, yp), (x, y, z)):
                        continue
                    gain = sum(1 for cell in dark_set
                               if cell not in fixed and covers((cand, yp), cell))
                    score = (gain, cand[1], cand[0])
                    if best_score is None or score > best_score:
                        best, best_score = (cand, yp), score
            if best is None:
                leftovers.append((x, y, z))
                continue
            cand, yp = best
            plan[cand] = yp
            added += 1
            for cell in dark_set:
                if covers((cand, yp), cell):
                    fixed.add(cell)
        if added == 0:
            leftovers = [(x, y, z) for x, y, z, _ in dark]
            break
    else:
        leftovers = [(x, y, z) for x, y, z, _ in dark]

    field = simulate_block_light(save, (x0, 55, z0), (x1, 66, z1), overlay=overlay())
    dark = field.below(targets, MELT)
    print(f"final: {len(plan)} clusters ({4 * len(plan)} pickles), "
          f"{len(dark)} targets still below {MELT}")
    print("by height:", sorted(Counter(plan.values()).items()))
    if dark:
        near = sum(1 for x, y, z, _ in dark if lid_distance(x, z) <= 4)
        print(f"still dark: {len(dark)}, of which {near} within 4 of a lid "
              f"(unlightable without melting it); levels "
              f"{sorted(Counter(l for *_, l in dark).items())}")
        print("  first 20:", [(x, z, l) for x, y, z, l in dark[:20]])
    lid_light = Counter(field.level(x, y, z) for x, y, z in lid_cells)
    print("lid light after the plan:", sorted(lid_light.items()),
          "-- MUST be all <= 10")
    if any(level > 10 for level in lid_light):
        raise SystemExit("a lid would melt; refusing to write this plan")

    with open(os.path.join(HERE, "plan.json"), "w") as fh:
        json.dump({"pickle": PICKLE, "surface": SURFACE, "box": list(BOX),
                   "lake": sorted(lake), "targets": targets,
                   "plan": sorted([[x, yp, z] for (x, z), yp in plan.items()],
                                  key=lambda c: (c[2], c[0])),
                   "lids": sorted(lids),
                   "dark": [list(d) for d in dark]}, fh)

    # A picture in text: one character per column.
    existing = {(x, z) for (x, z), col in columns.items()
                if col["top"] == "minecraft:sea_pickle"
                or name(x, col["bed_y"] + 1, z) == "minecraft:sea_pickle"}
    lines = []
    for z in range(z0, z1 + 1):
        row = []
        for x in range(x0, x1 + 1):
            if (x, z) in plan:
                ch = "*"
            elif (x, z) in lids:
                ch = "L"
            elif (x, z) in existing:
                ch = "P"
            elif (x, z) in lake:
                col = columns[(x, z)]
                if col["above"] != "minecraft:air":
                    ch = "B"
                elif (x, SURFACE, z) in {(d[0], d[1], d[2]) for d in dark}:
                    ch = "!"
                else:
                    ch = "~" if col["pickle_y"] else "x"
            elif (x, z) in mask:
                ch = "."
            else:
                ch = " "
            row.append(ch)
        lines.append("".join(row))
    with open(os.path.join(HERE, "plan_map.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("wrote plan.json and plan_map.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
