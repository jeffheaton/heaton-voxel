"""Survey the frozen lake under the player: extent, depth, bed, pickles, lamps.

Read-only. Floods outward over the water/ice surface from where the player
stands, then reports what a pickle plan has to know: how deep each column
is, what the bed is made of, where Jeff's own pickles already sit and what
else nearby gives light.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from heaton_voxel.world import MinecraftSave                     # noqa: E402
from heaton_voxel.craft.render import render_area, render_plan   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WORLD = os.path.expanduser("~/Library/Application Support/minecraft/saves/JeffWorld")
SURFACE = 62
TOP = {"minecraft:water", "minecraft:ice"}
IN_WATER = {"minecraft:water", "minecraft:sea_pickle", "minecraft:seagrass",
            "minecraft:tall_seagrass", "minecraft:kelp", "minecraft:kelp_plant"}
LIMIT = 40000


def main() -> None:
    save = MinecraftSave.load(WORLD)
    px, py, pz = save.player_block_position()
    print("player", (px, py, pz), "biome", save.get_biome(px, py, pz))

    def name(x, y, z):
        b = save.get_block(x, y, z)
        return None if b is None else b.name

    seen = {(px, pz)}
    queue = deque([(px, pz)])
    columns = {}
    while queue:
        x, z = queue.popleft()
        top = name(x, SURFACE, z)
        if top not in TOP:
            continue
        # Down to the bed.
        y = SURFACE - 1
        while y > SURFACE - 30 and name(x, y, z) in IN_WATER:
            y -= 1
        bed = name(x, y, z)
        columns[(x, z)] = {"top": top, "bed_y": y, "bed": bed,
                           "above": name(x, SURFACE + 1, z)}
        if len(columns) >= LIMIT:
            print("hit the column limit; the flood is not a lake")
            break
        for nx, nz in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
            if (nx, nz) not in seen:
                seen.add((nx, nz))
                queue.append((nx, nz))

    xs = [c[0] for c in columns]; zs = [c[1] for c in columns]
    x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
    print(f"lake: {len(columns)} surface cells, x {x0}..{x1}, z {z0}..{z1}")
    print("surface:", Counter(c["top"] for c in columns.values()))
    print("above surface:", Counter(c["above"] for c in columns.values()))
    print("water depth below the surface cell (SURFACE-1 - bed_y):",
          sorted(Counter(SURFACE - 1 - c["bed_y"] for c in columns.values()).items()))
    print("bed:", Counter(c["bed"] for c in columns.values()).most_common())

    pickles = save.find_blocks((x0 - 4, 40, z0 - 4), (x1 + 4, 70, z1 + 4),
                               ("minecraft:sea_pickle",), match="name")
    found = [(x, y, z, str(s)) for x, y, z, s in pickles.matches]
    print(f"sea pickles in the box: {len(found)}")
    for row in found:
        print("  ", row)
    lamps = save.find_blocks((x0 - 16, 40, z0 - 16), (x1 + 16, 80, z1 + 16),
                             ("minecraft:glowstone", "minecraft:sea_lantern", "minecraft:lantern",
                              "minecraft:torch", "minecraft:wall_torch", "minecraft:shroomlight",
                              "minecraft:jack_o_lantern", "minecraft:soul_lantern",
                              "minecraft:campfire", "minecraft:redstone_lamp", "minecraft:lava",
                              "minecraft:magma_block", "minecraft:end_rod", "minecraft:froglight"),
                             match="name")
    print(f"other light sources within 16 of the box: {len(lamps.matches)}")
    for x, y, z, s in lamps.matches[:40]:
        print("  ", (x, y, z, str(s)))

    with open(os.path.join(HERE, "survey.json"), "w") as fh:
        json.dump({"surface": SURFACE, "box": [x0, z0, x1, z1],
                   "columns": {f"{x},{z}": c for (x, z), c in columns.items()},
                   "pickles": found}, fh, indent=0)
    render_area(save, (x0 - 12, 0, z0 - 12), (x1 + 12, 0, z1 + 12),
                os.path.join(HERE, "before_area.png"), scale=4)
    render_plan(save, SURFACE, (x0 - 12, SURFACE, z0 - 12), (x1 + 12, SURFACE, z1 + 12),
                os.path.join(HERE, "before_plan62.png"), scale=4)
    render_plan(save, SURFACE - 1, (x0 - 12, SURFACE - 1, z0 - 12), (x1 + 12, SURFACE - 1, z1 + 12),
                os.path.join(HERE, "before_plan61.png"), scale=4)
    print("wrote survey.json and the before pictures")


if __name__ == "__main__":
    main()
