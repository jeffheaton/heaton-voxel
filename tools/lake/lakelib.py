"""Shared vocabulary for the lake job: what counts as water, floor, plant.

Read-only helpers plus the block sets three scripts kept re-deriving.
"""
from __future__ import annotations

import os
from collections import deque

WORLD = os.path.expanduser("~/Library/Application Support/minecraft/saves/JeffWorld")

WATER = frozenset(("minecraft:water", "minecraft:bubble_column"))
AIR = frozenset(("minecraft:air", "minecraft:cave_air", "minecraft:void_air"))

#: Things standing *in* the water that are not the lake floor. A floor
#: finder that stops on kelp reports the plant's height as the bottom.
FLOATING = frozenset((
    "minecraft:kelp", "minecraft:kelp_plant", "minecraft:seagrass",
    "minecraft:tall_seagrass", "minecraft:lily_pad", "minecraft:sea_pickle",
))

#: Ice sits *at* water level and is motion-blocking, so a surface flood
#: fill that only follows water stops dead at a frozen patch.
ICE = frozenset((
    "minecraft:ice", "minecraft:frosted_ice", "minecraft:blue_ice",
    "minecraft:packed_ice",
))


def names(save, x, y, z):
    b = save.get_block(x, y, z)
    return b.name if b is not None else None


def surface_flood(save, dim, seeds, level, *, limit=500_000, bounds=None):
    """Every column whose block at ``level`` is water, connected to a seed.

    Ice is crossed (it is a lid on the same lake) but not collected.
    """
    lake: set[tuple[int, int]] = set()
    q = deque(seeds)
    seen = set(q)
    while q:
        x, z = q.popleft()
        n = names(save, x, level, z)
        if n is None:
            continue
        if n in WATER:
            lake.add((x, z))
        elif n not in ICE:
            continue
        if len(lake) > limit:
            raise RuntimeError("flood fill ran away -- the lake is not closed")
        for nx, nz in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
            if (nx, nz) in seen:
                continue
            if bounds and not (bounds[0] <= nx <= bounds[2]
                               and bounds[1] <= nz <= bounds[3]):
                continue
            seen.add((nx, nz))
            if dim.chunk(nx >> 4, nz >> 4) is None:
                continue
            q.append((nx, nz))
    return lake


def floor_y(save, x, z, level, *, floor_limit=-64):
    """Walk down from the water surface to the first real floor block.

    Water, air pockets and underwater plants are all stepped through --
    each of them, taken as the bottom, is a different wrong answer.
    """
    y = level
    while y > floor_limit:
        n = names(save, x, y, z)
        if n is None:
            return None
        if n not in WATER and n not in AIR and n not in FLOATING and n not in ICE:
            return y
        y -= 1
    return None
