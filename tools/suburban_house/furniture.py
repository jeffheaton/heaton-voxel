"""Furniture, as vanilla blocks.

Every piece here takes the y of the *floor block* it stands on, so a sofa
at ``fy=64`` puts its seat in the air at y=65. That is the only convention
worth remembering; everything else is the usual Minecraft vocabulary of
stairs for seats, slabs and full blocks for surfaces, barrels for anything
with a door in it, and trapdoors for the thin vertical bits.

Two rules the pieces obey, because breaking either is invisible until you
are standing in the room:

* a **chest** will not open with an opaque block directly above it, so
  storage that lives under a counter or a shelf is a ``barrel`` instead;
* a stair's full face is its ``facing`` side, so a chair's ``facing`` is
  the direction its *back* points, not the direction the sitter looks.
"""

from __future__ import annotations

#: (dx, dz) per compass direction. +x is east, +z is south.
VECTOR = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
PERPENDICULAR = {"north": ("east", "west"), "south": ("east", "west"),
                 "east": ("north", "south"), "west": ("north", "south")}


def step(x, z, facing, distance=1):
    dx, dz = VECTOR[facing]
    return x + dx * distance, z + dz * distance


def run(x0, z0, x1, z1):
    """Every cell of an inclusive straight or rectangular run."""
    for x in range(min(x0, x1), max(x0, x1) + 1):
        for z in range(min(z0, z1), max(z0, z1) + 1):
            yield x, z


# -- floors ---------------------------------------------------------------

def carpet(b, fy, x0, z0, x1, z1, color, skip=()):
    """Carpet over a rectangle, at the air level above the floor."""
    laid = 0
    for x, z in run(x0, z0, x1, z1):
        if (x, z) in skip:
            continue
        b.set(x, fy + 1, z, color)
        laid += 1
    return laid


def tile(b, fy, x0, z0, x1, z1, block):
    """Hard floor: the floor block itself is replaced, not covered."""
    for x, z in run(x0, z0, x1, z1):
        b.set(x, fy, z, block)


def rug(b, fy, x0, z0, x1, z1, color):
    carpet(b, fy, x0, z0, x1, z1, color)


# -- seating --------------------------------------------------------------

def chair(b, fy, x, z, back, wood="oak"):
    """One dining chair. ``back`` is the way the backrest points."""
    b.set(x, fy + 1, z,
          f"minecraft:{wood}_stairs[facing={back},half=bottom,shape=straight,"
          "waterlogged=false]")


def sofa(b, fy, x0, z0, x1, z1, back, wood="oak", arms=True):
    """A straight sofa. The run must be one cell wide.

    ``back`` is the side the backrest sits on -- a sofa against a north
    wall has ``back="north"`` and is sat on looking south.
    """
    cells = sorted(run(x0, z0, x1, z1))
    for x, z in cells:
        b.set(x, fy + 1, z,
              f"minecraft:{wood}_stairs[facing={back},half=bottom,"
              "shape=straight,waterlogged=false]")
    if arms and len(cells) >= 2:
        first, last = cells[0], cells[-1]
        along = "east" if first[0] != last[0] else "south"
        for cell, outward in ((first, OPPOSITE[along]), (last, along)):
            ax, az = step(cell[0], cell[1], outward)
            b.set(ax, fy + 1, az,
                  f"minecraft:{wood}_trapdoor[facing={OPPOSITE[outward]},"
                  "half=bottom,open=true,powered=false,waterlogged=false]")
    return cells


def stool(b, fy, x, z, wood="oak"):
    """A backless stool for a kitchen island: a fence post with a lid."""
    b.set(x, fy + 1, z, f"minecraft:{wood}_fence"
          "[east=false,north=false,south=false,west=false,waterlogged=false]")
    b.set(x, fy + 2, z, "minecraft:white_carpet")


# -- surfaces -------------------------------------------------------------

def table(b, fy, x0, z0, x1, z1, top="minecraft:stripped_oak_wood[axis=x]",
          cloth=None):
    """A dining table: a slab of top at seat-plus-one, optional cloth."""
    for x, z in run(x0, z0, x1, z1):
        b.set(x, fy + 1, z, top)
        if cloth:
            b.set(x, fy + 2, z, cloth)


def low_table(b, fy, x0, z0, x1, z1, wood="oak"):
    """A coffee table: a bottom slab, so it reads lower than the seats."""
    for x, z in run(x0, z0, x1, z1):
        b.set(x, fy + 1, z,
              f"minecraft:{wood}_slab[type=bottom,waterlogged=false]")


def counter(b, fy, x0, z0, x1, z1, block="minecraft:smooth_quartz"):
    for x, z in run(x0, z0, x1, z1):
        b.set(x, fy + 1, z, block)


def wall_cabinets(b, fy, x0, z0, x1, z1, facing):
    """Upper kitchen cabinets: barrels, which open whatever is around them."""
    for x, z in run(x0, z0, x1, z1):
        for y in (fy + 3, fy + 4):
            b.set(x, y, z, f"minecraft:barrel[facing={facing},open=false]")


def shelf(b, fy, x0, z0, x1, z1, y_offsets=(1, 2, 3),
          block="minecraft:bookshelf"):
    for x, z in run(x0, z0, x1, z1):
        for dy in y_offsets:
            b.set(x, fy + dy, z, block)


# -- appliances and fittings ---------------------------------------------

def fridge(b, fy, x, z, facing):
    """A tall white fridge, its handle a button on the front face."""
    b.set(x, fy + 1, z, "minecraft:white_concrete")
    b.set(x, fy + 2, z, "minecraft:white_concrete")
    hx, hz = step(x, z, facing)
    b.set(hx, fy + 2, hz,
          f"minecraft:stone_button[face=wall,facing={facing},powered=false]")


def stove(b, fy, x, z, facing):
    b.set(x, fy + 1, z, f"minecraft:smoker[facing={facing},lit=false]")
    b.set(x, fy + 3, z, f"minecraft:barrel[facing={facing},open=false]")


def sink(b, fy, x, z):
    b.set(x, fy + 1, z, "minecraft:water_cauldron[level=3]")


def dishwasher(b, fy, x, z, facing):
    b.set(x, fy + 1, z, f"minecraft:barrel[facing={facing},open=false]")


def appliance(b, fy, x, z, facing, top="minecraft:smooth_quartz_slab"
              "[type=top,waterlogged=false]"):
    """A washer or dryer: a white box with a porthole and a worktop over it."""
    b.set(x, fy + 1, z, "minecraft:white_concrete")
    px, pz = step(x, z, facing)
    b.set(px, fy + 1, pz,
          f"minecraft:stone_button[face=wall,facing={facing},powered=false]")
    b.set(x, fy + 2, z, top)


def toilet(b, fy, x, z, back):
    """Bowl in front, cistern behind, flush button on the cistern."""
    b.set(x, fy + 1, z, "minecraft:water_cauldron[level=1]")
    cx, cz = step(x, z, back)
    b.set(cx, fy + 1, cz,
          f"minecraft:quartz_stairs[facing={OPPOSITE[back]},half=bottom,"
          "shape=straight,waterlogged=false]")


def vanity(b, fy, x, z, wall_facing=None):
    """A basin with a mirror hung over it.

    The mirror is a pane in the basin's own column rather than in the
    wall behind: a pane put *into* a one-block wall is a hole through it,
    and the first sign of that is daylight in a bathroom.
    """
    b.set(x, fy + 1, z, "minecraft:water_cauldron[level=1]")
    for y in (fy + 3, fy + 4):
        b.set(x, y, z, "minecraft:light_gray_stained_glass_pane"
              "[east=false,north=false,south=false,west=false,"
              "waterlogged=false]")


def bathtub(b, fy, x0, z0, x1, z1):
    """A tub: water held by full blocks, so nothing can leak into the room."""
    cells = set(run(x0, z0, x1, z1))
    for x, z in cells:
        b.set(x, fy + 1, z, "minecraft:water[level=0]")
    for x, z in cells:
        for facing in VECTOR:
            nx, nz = step(x, z, facing)
            if (nx, nz) not in cells:
                b.set(nx, fy + 1, nz, "minecraft:smooth_quartz")


def shower(b, fy, x0, z0, x1, z1, ceiling_y, door_side):
    """A glazed cubicle with a head under the ceiling and a drain below."""
    cells = set(run(x0, z0, x1, z1))
    for x, z in cells:
        b.set(x, fy, z, "minecraft:light_gray_concrete")
        b.set(x, ceiling_y - 1, z,
              "minecraft:hopper[enabled=true,facing=down]")
    for x, z in cells:
        for facing in VECTOR:
            nx, nz = step(x, z, facing)
            if (nx, nz) in cells or facing == door_side:
                continue
            for y in range(fy + 1, fy + 4):
                b.set(nx, y, nz, "minecraft:glass_pane"
                      "[east=false,north=false,south=false,west=false,"
                      "waterlogged=false]")


# -- bedrooms -------------------------------------------------------------

def bed(b, fy, x, z, facing, color="white"):
    """Both halves at once; ``(x, z)`` is the foot, ``facing`` the head."""
    b.place(x, fy + 1, z, f"minecraft:{color}_bed[facing={facing}]")


def nightstand(b, fy, x, z, lamp=True):
    b.set(x, fy + 1, z, "minecraft:stripped_oak_wood[axis=y]")
    if lamp:
        b.set(x, fy + 2, z, "minecraft:lantern[hanging=false,waterlogged=false]")


def wardrobe(b, fy, x0, z0, x1, z1, facing):
    for x, z in run(x0, z0, x1, z1):
        for y in (fy + 1, fy + 2, fy + 3):
            b.set(x, y, z, f"minecraft:barrel[facing={facing},open=false]")


def desk(b, fy, x0, z0, x1, z1, facing):
    cells = sorted(run(x0, z0, x1, z1))
    for x, z in cells:
        b.set(x, fy + 1, z, "minecraft:smooth_quartz")
    px, pz = cells[0]
    b.set(px, fy + 1, pz, f"minecraft:barrel[facing={facing},open=false]")


# -- living-room props ----------------------------------------------------

def television(b, fy, x0, z0, x1, z1, mount_facing, recess=True):
    """A flat screen. Recessed into an interior wall unless told otherwise."""
    for x, z in run(x0, z0, x1, z1):
        tx, tz = (step(x, z, mount_facing) if recess else (x, z))
        for y in (fy + 3, fy + 4):
            b.set(tx, y, tz, "minecraft:black_concrete")


def media_unit(b, fy, x0, z0, x1, z1, facing):
    for x, z in run(x0, z0, x1, z1):
        b.set(x, fy + 1, z, f"minecraft:barrel[facing={facing},open=false]")
        b.set(x, fy + 2, z,
              "minecraft:smooth_quartz_slab[type=top,waterlogged=false]")


def fireplace(b, fy, x, z, facing, height=5):
    """A brick surround projecting into the room, with a lit hearth.

    The flue is closed off above the firebox: the smoke particles read
    from the room, and nothing dark and enclosed is left over the fire.
    """
    left, right = PERPENDICULAR[facing]
    for side in (left, right):
        sx, sz = step(x, z, side)
        for y in range(fy + 1, fy + 1 + height):
            b.set(sx, y, sz, "minecraft:bricks")
    b.set(x, fy + 1, z,
          f"minecraft:campfire[facing={facing},lit=true,signal_fire=false,"
          "waterlogged=false]")
    b.set(x, fy + 2, z, "minecraft:air")
    for y in range(fy + 3, fy + 1 + height):
        b.set(x, y, z, "minecraft:bricks")
    bx, bz = step(x, z, OPPOSITE[facing])
    for y in range(fy + 1, fy + 1 + height):
        b.set(bx, y, bz, "minecraft:bricks")


def plant(b, fy, x, z, kind="minecraft:potted_fern"):
    b.set(x, fy + 1, z, kind)


def picture(b, fy, x, z, facing, height=3):
    """A framed print: a pane of color set flat against a wall."""
    b.set(x, fy + height, z, "minecraft:blue_stained_glass_pane"
          "[east=false,north=false,south=false,west=false,waterlogged=false]")


# -- fabric of the house --------------------------------------------------

def partition(b, x0, z0, x1, z1, y0, y1, block="minecraft:white_concrete",
              skip=()):
    """An interior wall, floor to ceiling."""
    placed = 0
    for x, z in run(x0, z0, x1, z1):
        if (x, z) in skip:
            continue
        for y in range(y0, y1 + 1):
            b.set(x, y, z, block)
            placed += 1
    return placed


def doorway(b, x, z, y0, facing, hinge="left", door="minecraft:oak_door",
            head="minecraft:stripped_oak_wood[axis=y]", head_y=None):
    """A door in a partition: the two door halves plus a lintel above."""
    b.place(x, y0, z, f"{door}[facing={facing},hinge={hinge},open=false,"
                      "powered=false]")
    if head_y is not None:
        for y in range(y0 + 2, head_y + 1):
            b.set(x, y, z, head)


def opening(b, x0, z0, x1, z1, y0, y1, head=None):
    """A cased opening: a hole in a partition with a trimmed head."""
    for x, z in run(x0, z0, x1, z1):
        for y in range(y0, y1 + 1):
            b.set(x, y, z, "minecraft:air")
        if head is not None:
            b.set(x, y1 + 1, z, head)


def ceiling_light(b, x, z, ceiling_y):
    """A pendant lantern hung from the ceiling."""
    b.set(x, ceiling_y - 1, z,
          "minecraft:lantern[hanging=true,waterlogged=false]")


def hidden_light(b, fy, x, z):
    """Glowstone set into the floor, to be covered by carpet."""
    b.set(x, fy, z, "minecraft:glowstone")
