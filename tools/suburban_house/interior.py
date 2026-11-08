"""The fabric of the interior: work lights out, walls, stairs, chimney.

Everything here is layout-agnostic -- it takes coordinates and builds the
thing. What goes where lives in :mod:`plan`.
"""

from __future__ import annotations

import furniture as f

AIR = "minecraft:air"

WALL = "minecraft:white_concrete"
TRIM = "minecraft:stripped_oak_wood[axis=y]"
FLOOR_WOOD = "minecraft:stripped_oak_wood[axis=x]"


def strip_worklights(b, cells, levels) -> int:
    """Take out the loose floor-standing lanterns left as work lights.

    They are all ``lantern[hanging=false]`` sitting on a floor; the
    hanging ones belong to the shell and are left alone.
    """
    removed = 0
    for x, z in cells:
        for y in levels:
            state = b.save.get_block(x, y, z)
            if state is None or state.name != "minecraft:lantern":
                continue
            if state.properties.get("hanging") == "true":
                continue
            b.set(x, y, z, AIR)
            removed += 1
    return removed


def clear_room(b, x0, z0, x1, z1, y0, y1) -> None:
    for x, z in f.run(x0, z0, x1, z1):
        for y in range(y0, y1 + 1):
            b.set(x, y, z, AIR)


# -- the staircase --------------------------------------------------------

def straight_stair(b, x0, x1, z_first, direction, floor_y, upper_y,
                   wood="oak", rail="minecraft:oak_fence"):
    """A straight flight from ``floor_y`` up to ``upper_y``, ``x0..x1`` wide.

    ``z_first`` is the first tread and ``direction`` the way it climbs
    (``"north"`` or ``"south"``). Six treads carry the six blocks from a
    ground floor at y=64 to an upper floor at y=70: the last tread *is*
    the upper floor plane, so it replaces a floor block rather than
    sitting under one.

    The well is cut wide enough that nobody hits their head: a tread at
    height h is stood on at h+1, and the head occupies h+1 and h+2, so
    the floor above has to be open from the tread whose h+2 reaches it.
    """
    rise = upper_y - floor_y                      # 6 for this house
    dz = 1 if direction == "south" else -1
    treads = []
    for step_index in range(rise):
        y = floor_y + 1 + step_index              # 65..70
        z = z_first + dz * step_index
        treads.append((y, z))
        for x in range(x0, x1 + 1):
            b.set(x, y, z,
                  f"minecraft:{wood}_stairs[facing={direction},half=bottom,"
                  "shape=straight,waterlogged=false]")
            # Solid under every tread, so the flight reads as a stair and
            # not as six steps floating over the room below.
            for fill in range(floor_y + 1, y):
                b.set(x, fill, z, f"minecraft:{wood}_planks")

    # Head room: open the floor above wherever a walker's head would reach it.
    opened = []
    for y, z in treads:
        if y + 2 >= upper_y:
            for x in range(x0, x1 + 1):
                if b.save.get_block(x, upper_y, z).name != f"minecraft:{wood}_stairs":
                    b.set(x, upper_y, z, AIR)
                b.set(x, upper_y + 1, z, AIR)
            opened.append(z)

    # And the landing in front of the top tread, so you arrive on floor.
    top_y, top_z = treads[-1]
    arrival_z = top_z + dz
    for x in range(x0, x1 + 1):
        b.set(x, upper_y, arrival_z, FLOOR_WOOD)
        for y in range(upper_y + 1, upper_y + 6):
            b.set(x, y, arrival_z, AIR)

    return {"treads": treads, "opened": sorted(set(opened)),
            "arrival_z": arrival_z}


def stairwell_rail(b, x0, x1, z0, z1, y, exclude=(),
                   rail="minecraft:oak_fence"):
    """A fence rail round the open edges of the stairwell on the floor above."""
    placed = 0
    for x, z in f.run(x0, z0, x1, z1):
        if (x, z) in exclude:
            continue
        b.set(x, y, z, rail + "[east=false,north=false,south=false,"
                              "west=false,waterlogged=false]")
        placed += 1
    return placed


def under_stair_cupboard(b, x0, x1, z0, z1, floor_y, facing):
    """The cupboard under the stairs, because every house has one."""
    for x, z in f.run(x0, z0, x1, z1):
        b.set(x, floor_y + 1, z,
              f"minecraft:barrel[facing={facing},open=false]")


# -- the chimney ----------------------------------------------------------

def chimney(b, x, z, floor_y, top_y, *, breast=(), block="minecraft:bricks"):
    """A brick stack from the hearth to the sky.

    ``breast`` is the extra cells the chimney breast occupies on the way
    up -- the shoulder either side of the firebox. Everything from the
    ground floor to ``top_y`` is solid masonry; the flue is not modeled,
    because an open shaft through a house is a two-block-wide dark column
    that mobs would find long before anyone noticed the realism.
    """
    columns = [(x, z), *breast]
    for cx, cz in columns:
        for y in range(floor_y + 1, top_y + 1):
            b.set(cx, y, cz, block)
    # A capped stack: a lip of stairs and a slab, so it does not read as a
    # brick post somebody forgot to finish.
    for facing in ("north", "south", "east", "west"):
        nx, nz = f.step(x, z, facing)
        b.set(nx, top_y, nz,
              f"minecraft:brick_stairs[facing={f.OPPOSITE[facing]},half=top,"
              "shape=straight,waterlogged=false]")
    b.set(x, top_y + 1, z, "minecraft:brick_slab[type=bottom,waterlogged=false]")


def clear_strays(b, cells, y0, y1) -> int:
    """Take out the setting-out line left standing above the roof."""
    removed = 0
    for x, z in cells:
        for y in range(y0, y1 + 1):
            state = b.save.get_block(x, y, z)
            if state is not None and state.name != AIR:
                b.set(x, y, z, AIR)
                removed += 1
    return removed
