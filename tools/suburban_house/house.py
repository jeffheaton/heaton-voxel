"""The unfinished suburban house in JeffWorld: geometry, palette, helpers.

The shell was built by hand and surveyed before anything here was written;
every number below came out of that survey rather than out of a guess.
The house faces north -- the front door and the street are at low z.

    ground floor   floor y=64, ceiling y=70, air y=65..69
    upper floor    floor y=70, ceiling y=76+, air y=71..75
    roof           side gable, ridge east-west at z=-5, y=79

Two things were missing when the survey ran and both are built by the
scripts beside this one: the roof over the southern third of the upper
floor (z 3..11 east of x=-296 was open to the sky), and any interior at
all -- no partitions, no stairs between the floors, no furniture.
"""

from __future__ import annotations

import os
import sys

# The package lives one directory up from tools/.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from heaton_voxel.blocks import BlockState                      # noqa: E402
from heaton_voxel.craft import Builder, Worksite                # noqa: E402
from heaton_voxel.craft.render import render_area, render_plan, render_slice  # noqa: E402

WORLD = os.path.expanduser(
    "~/Library/Application Support/minecraft/saves/JeffWorld")

WORKSITE = "suburban-house"

# -- the shell ------------------------------------------------------------

GROUND_FLOOR_Y = 64          # the stripped_oak_wood the ground floor stands on
GROUND_CEIL_Y = 70           # also the upper floor's own floor
UPPER_FLOOR_Y = 70
UPPER_CEIL_Y = 76            # over the south third; higher under the ridge

#: Ground floor: two rectangles joined along z=-3/-2, with the sealed
#: garage occupying the north-west corner.
GROUND_BACK = (-326, -2, -278, 11)      # x0, z0, x1, z1  inclusive
GROUND_FRONT = (-305, -13, -278, -3)

#: Upper floor: one rectangle plus the projecting front bay.
UPPER_MAIN = (-313, -13, -278, 11)
UPPER_BAY = (-305, -16, -289, -14)

#: Walls of the shell. Nothing here may be cut.
WALL_WEST_GROUND = -327
WALL_WEST_UPPER = -314
WALL_EAST = -277
WALL_SOUTH = 12
WALL_NORTH = -14
GARAGE_WALL_X = -306         # z -13..-3
GARAGE_WALL_Z = -3           # x -326..-306

FRONT_DOOR = (-299, -298)    # x of each leaf, in the wall at z=-14

#: Exterior windows, as (x, z, y0, y1) of the glass itself.
WINDOWS_GROUND = [(-290, -14, 66, 68), (-289, -14, 66, 68),
                  (-281, -14, 66, 68), (-280, -14, 66, 68)]
WINDOWS_UPPER = [(x, -17, 72, 74) for x in range(-298, -294)] + \
                [(-311, -14, 73, 74), (-310, -14, 73, 74),
                 (-281, -14, 72, 74), (-280, -14, 72, 74),
                 (-311, 12, 72, 74), (-310, 12, 72, 74)]

#: The whole build box, for the worksite snapshot.
BOX = ((-330, -28), (-274, 16))

# -- the palette ----------------------------------------------------------
#
# The shell is smooth quartz, brick, light gray concrete and stripped oak
# with light blue window trim. The interior stays inside that register:
# white walls, oak trim, gray and blue soft furnishing.

AIR = "minecraft:air"
FLOOR_WOOD = "minecraft:stripped_oak_wood[axis=x]"

WALL = "minecraft:white_concrete"          # interior partitions
WALL_TRIM = "minecraft:stripped_oak_wood[axis=y]"  # corner posts / door jambs
WET_WALL = "minecraft:white_terracotta"    # bathrooms, laundry
KITCHEN_WALL = "minecraft:white_concrete"

CEILING = FLOOR_WOOD
ROOF_STONE = "minecraft:stone"
ROOF_STAIR = "minecraft:stone_brick_stairs"

CARPET_LIVING = "minecraft:light_gray_carpet"
CARPET_BLUE = "minecraft:light_blue_carpet"
CARPET_WARM = "minecraft:brown_carpet"
CARPET_WHITE = "minecraft:white_carpet"
TILE_KITCHEN = "minecraft:polished_diorite"
TILE_BATH = "minecraft:white_glazed_terracotta"
TILE_UTILITY = "minecraft:light_gray_concrete"

DOOR_OAK = "minecraft:oak_door"
LAMP = "minecraft:lantern[hanging=true]"
FLOOR_LAMP = "minecraft:lantern"


def block(text: str) -> BlockState:
    return BlockState.parse(text)


def open_site(*, resnapshot: bool = False) -> Worksite:
    """Open (or resume) the house worksite over the whole build box."""
    return Worksite.open(WORLD, BOX[0], BOX[1], name=WORKSITE,
                         resnapshot=resnapshot)


def cells(rect):
    """Every (x, z) in an inclusive (x0, z0, x1, z1) rectangle."""
    x0, z0, x1, z1 = rect
    for x in range(min(x0, x1), max(x0, x1) + 1):
        for z in range(min(z0, z1), max(z0, z1) + 1):
            yield x, z


def ground_cells():
    """Every walkable (x, z) on the ground floor."""
    return set(cells(GROUND_BACK)) | set(cells(GROUND_FRONT))


def upper_cells():
    """Every walkable (x, z) on the upper floor, bay included."""
    return set(cells(UPPER_MAIN)) | set(cells(UPPER_BAY))


def render_set(save, tag: str, directory: str) -> list[str]:
    """The standard picture set: both floors in plan, three cutaways, the roof."""
    os.makedirs(directory, exist_ok=True)
    out = []

    def name(part):
        return os.path.join(directory, f"{tag}-{part}.png")

    render_plan(save, 66, (-330, 66, -20), (-274, 66, 15),
                name("plan-ground"), scale=8)
    render_plan(save, 72, (-330, 72, -20), (-274, 72, 15),
                name("plan-upper"), scale=8)
    render_area(save, (-334, 64, -30), (-270, 90, 18), name("area"), scale=8)
    render_slice(save, "z", 6, (-332, 60, 6), (-272, 84, 6),
                 name("slice-z6"), scale=8)
    render_slice(save, "z", -8, (-332, 60, -8), (-272, 84, -8),
                 name("slice-z-8"), scale=8)
    render_slice(save, "x", -290, (-290, 60, -20), (-290, 84, 16),
                 name("slice-x-290"), scale=8)
    for part in ("plan-ground", "plan-upper", "area",
                 "slice-z6", "slice-z-8", "slice-x-290"):
        out.append(name(part))
    return out
