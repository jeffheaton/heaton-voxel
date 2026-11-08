"""What goes where: the floor plan, the finishes and the furniture.

Coordinates are the house's, not a room's -- everything is absolute, so
any line here can be checked against a floor plan render without doing
arithmetic first. Walls are given as the cells they *occupy*; rooms as
the cells they enclose. A wall between two rooms therefore belongs to
neither, which is why every room rectangle stops one short of its
neighbor.

    ground floor   floor block y=64, air y=65..69, ceiling y=70
    upper floor    floor block y=70, air y=71..75, ceiling y=76 and up

The upper floor's ceiling is not flat -- it is the underside of the roof,
y=76 at the eaves and y=78 along the ridge -- so partitions up there are
run to whatever is above them rather than to a fixed height. A wall built
to y=75 everywhere would leave every upstairs room open to its neighbor
through the two-block gap under the ridge.
"""

from __future__ import annotations

import furniture as f
import interior
import house

GF = 64          # ground floor block
GC = 70          # ground floor ceiling
UF = 70          # upper floor block
AIR = "minecraft:air"

WALL = "minecraft:white_concrete"
WET = "minecraft:white_terracotta"
TRIM = "minecraft:stripped_oak_wood[axis=y]"
LINTEL = "minecraft:stripped_oak_wood[axis=x]"


# =========================================================================
# Ground floor
# =========================================================================

#: (x0, z0, x1, z1) of every partition on the ground floor.
GROUND_WALLS = [
    (-301, -13, -301, -3),      # utility strip | foyer
    (-295, -13, -295, -3),      # foyer | living room
    (-305,  -9, -301,  -9),     # powder room | laundry
    (-305,  -3, -301,  -3),     # laundry | back of house
    (-294,  -3, -278,  -3),     # living room | great room
    (-301,  -2, -301, 11),      # back hall | great room
    (-309,  -2, -309, 11),      # rec room | back hall
    (-308,   2, -302,   2),     # back hall | home office
]

#: Cased openings: (x0, z0, x1, z1, height). Three tall with a wood head.
GROUND_OPENINGS = [
    (-295, -6, -295, -4, 3),    # foyer -> living room
    (-288, -3, -286, -3, 3),    # living room -> great room
    (-301, -1, -301,  1, 3),    # great room -> back hall
    (-309, -1, -309,  1, 3),    # back hall -> rec room
]

#: Doors: (x, z, facing, hinge). ``facing`` is the way the door opens onto.
GROUND_DOORS = [
    (-301, -11, "east", "left"),    # powder room
    (-301,  -6, "east", "right"),   # laundry
    (-305,   2, "north", "left"),   # home office
]

#: Rooms, for the floor finishes. (name, x0, z0, x1, z1, floor, carpet)
GROUND_ROOMS = [
    ("foyer",       -300, -13, -296,  -3, "minecraft:polished_diorite", None),
    ("powder",      -305, -13, -302, -10, "minecraft:white_glazed_terracotta", None),
    ("laundry",     -305,  -8, -302,  -4, "minecraft:light_gray_concrete", None),
    ("living",      -294, -13, -278,  -3, None, "minecraft:light_gray_carpet"),
    ("kitchen",     -300,  -2, -293,   6, "minecraft:polished_diorite", None),
    ("dining",      -291,  -2, -278,   4, None, None),
    ("family",      -300,   7, -278,  11, None, "minecraft:light_gray_carpet"),
    ("dining-east", -291,   5, -278,   6, None, None),
    ("back-hall",   -308,  -2, -302,   1, "minecraft:polished_diorite", None),
    ("office",      -308,   3, -302,  11, None, "minecraft:brown_carpet"),
    ("rec",         -326,  -2, -310,  11, None, "minecraft:gray_carpet"),
]


# =========================================================================
# Upper floor
# =========================================================================

UPPER_WALLS = [
    (-301, -16, -301, 11),      # hall west
    (-294, -16, -294, 11),      # hall east
    (-313,  -7, -302,  -7),     # bedroom 2 | bedroom 3
    (-313,   0, -302,   0),     # bedroom 3 | primary suite
    (-309,   1, -309, 11),      # primary bedroom | dressing strip
    (-313,   6, -310,   6),     # closet | ensuite
    (-293,  -7, -286,  -7),     # bedroom 4 | hall bath  (open east of -286)
    (-293,  -1, -278,  -1),     # bath / bedroom 4 tail | bonus room
    (-293,   6, -278,   6),     # bonus room | bedroom 5
    (-285,  -6, -285,  -2),     # hall bath | bedroom 4's south tail
]

UPPER_DOORS = [
    (-301, -11, "west",  "left"),    # bedroom 2
    (-301,  -4, "west",  "right"),   # bedroom 3
    (-301,   6, "west",  "left"),    # primary bedroom
    (-309,   3, "west",  "left"),    # primary -> walk-in closet
    (-309,   9, "west",  "right"),   # primary -> ensuite
    (-294, -12, "east",  "left"),    # bedroom 4 (clear of the chimney at z=-10)
    (-294,  -4, "east",  "right"),   # hall bathroom
    (-294,   3, "east",  "left"),    # bonus room
    (-294,   9, "east",  "right"),   # bedroom 5 / study
]

UPPER_ROOMS = [
    ("hall",       -300, -16, -295, 11, None, "minecraft:light_gray_carpet"),
    ("bed2",       -313, -13, -302, -8, None, "minecraft:light_blue_carpet"),
    ("bed2-bay",   -305, -16, -302, -14, None, "minecraft:light_blue_carpet"),
    ("bed3",       -313,  -6, -302, -1, None, "minecraft:light_blue_carpet"),
    ("primary",    -308,   1, -302, 11, None, "minecraft:white_carpet"),
    ("closet",     -313,   1, -310,  5, None, "minecraft:white_carpet"),
    ("ensuite",    -313,   7, -310, 11, "minecraft:white_glazed_terracotta", None),
    ("bed4",       -293, -13, -278, -8, None, "minecraft:brown_carpet"),
    ("bed4-bay",   -293, -16, -289, -14, None, "minecraft:brown_carpet"),
    ("bed4-tail",  -284,  -6, -278, -2, None, "minecraft:brown_carpet"),
    ("bath",       -293,  -6, -286, -2, "minecraft:white_glazed_terracotta", None),
    ("bonus",      -293,   0, -278,  5, None, "minecraft:gray_carpet"),
    ("bed5",       -293,   7, -278, 11, None, "minecraft:light_blue_carpet"),
]


# =========================================================================
# The stair, the chimney, the leftovers
# =========================================================================

STAIR = dict(x0=-297, x1=-296, z_first=-12, direction="south",
             floor_y=GF, upper_y=UF)

#: The setting-out line left standing above the roof at x=-296.
STRAY_CELLS = [(-296, z) for z in range(-8, 3)]
STRAY_Y = (80, 83)
STRAY_EXTRA = [(-296, 79, 0), (-296, 79, 1)]

FIREPLACE = dict(x=-294, z=-10, facing="east")
CHIMNEY = dict(x=-294, z=-10, top_y=81)


def clear_leftovers(b) -> dict:
    removed = interior.clear_strays(b, STRAY_CELLS, *STRAY_Y)
    for x, y, z in STRAY_EXTRA:
        state = b.save.get_block(x, y, z)
        if state is not None and state.name != AIR:
            b.set(x, y, z, AIR)
            removed += 1
    return {"stray_blocks": removed}


def strip_worklights(b) -> dict:
    cells = house.ground_cells() | house.upper_cells()
    return {"lanterns": interior.strip_worklights(
        b, cells, (GF + 1, UF + 1, UF + 2))}


# =========================================================================
# Building
# =========================================================================

def _ceiling_above(b, x, z, floor_y):
    """The first solid block above ``floor_y``: how high a wall must go."""
    for y in range(floor_y + 1, floor_y + 12):
        state = b.save.get_block(x, y, z)
        if state is not None and state.name != AIR:
            return y
    return floor_y + 7


def build_walls(b) -> dict:
    ground = upper = 0
    for x0, z0, x1, z1 in GROUND_WALLS:
        ground += interior_wall(b, x0, z0, x1, z1, GF + 1, GC - 1, WALL)
    for x0, z0, x1, z1 in UPPER_WALLS:
        for x, z in f.run(x0, z0, x1, z1):
            top = _ceiling_above(b, x, z, UF)
            for y in range(UF + 1, top):
                b.set(x, y, z, WALL)
                upper += 1

    holes = 0
    for x0, z0, x1, z1, height in GROUND_OPENINGS:
        f.opening(b, x0, z0, x1, z1, GF + 1, GF + height, head=LINTEL)
        holes += 1
    for x, z, facing, hinge in GROUND_DOORS:
        f.doorway(b, x, z, GF + 1, facing, hinge, head=LINTEL,
                  head_y=GF + 3)
    for x, z, facing, hinge in UPPER_DOORS:
        f.doorway(b, x, z, UF + 1, facing, hinge, head=LINTEL,
                  head_y=UF + 3)
    return {"ground_wall_blocks": ground, "upper_wall_blocks": upper,
            "openings": holes,
            "doors": len(GROUND_DOORS) + len(UPPER_DOORS)}


def interior_wall(b, x0, z0, x1, z1, y0, y1, block) -> int:
    placed = 0
    for x, z in f.run(x0, z0, x1, z1):
        for y in range(y0, y1 + 1):
            b.set(x, y, z, block)
            placed += 1
    return placed


def build_stairs(b) -> dict:
    result = interior.straight_stair(b, **STAIR)
    # A rail round the well, on the three sides that are a drop. The hall
    # is six wide and the well two, so railing the west edge still leaves
    # x -300..-299 to walk past on; the one-wide strip east of the well is
    # railed off and becomes nothing but a balustrade to look over.
    hole = [z for z in result["opened"]
            if b.save.get_block(-297, UF, z).name == AIR]
    rail = 0
    for z in hole:
        rail += interior.stairwell_rail(b, -298, -298, z, z, UF + 1)
        rail += interior.stairwell_rail(b, -295, -295, z, z, UF + 1)
    if hole:
        north = min(hole) - 1
        rail += interior.stairwell_rail(b, -297, -296, north, north, UF + 1)
    return dict(result, hole=hole, rail=rail)


def build_chimney(b) -> dict:
    f.fireplace(b, GF, **FIREPLACE)
    for y in range(GF + 1, UF + 1):
        b.set(FIREPLACE["x"], y, FIREPLACE["z"], "minecraft:bricks")
    b.set(FIREPLACE["x"], GF + 1, FIREPLACE["z"],
          f"minecraft:campfire[facing={FIREPLACE['facing']},lit=true,"
          "signal_fire=false,waterlogged=false]")
    b.set(FIREPLACE["x"], GF + 2, FIREPLACE["z"], AIR)
    interior.chimney(b, CHIMNEY["x"], CHIMNEY["z"], UF, CHIMNEY["top_y"])
    return {"chimney_top": CHIMNEY["top_y"]}


# =========================================================================
# Floors
# =========================================================================

def lay_floors(b) -> dict:
    """Hard floors replace the boards; carpet lies on top of them."""
    hard = soft = 0
    for _, x0, z0, x1, z1, floor, rug in GROUND_ROOMS:
        if floor:
            f.tile(b, GF, x0, z0, x1, z1, floor)
            hard += 1
        if rug:
            f.carpet(b, GF, x0, z0, x1, z1, rug)
            soft += 1
    for _, x0, z0, x1, z1, floor, rug in UPPER_ROOMS:
        if floor:
            f.tile(b, UF, x0, z0, x1, z1, floor)
            hard += 1
        if rug:
            f.carpet(b, UF, x0, z0, x1, z1, rug)
            soft += 1
    return {"hard_floors": hard, "carpeted_rooms": soft}


# =========================================================================
# Furniture
# =========================================================================

def furnish(b) -> dict:
    done = {}
    for name, fn in (("foyer", _foyer), ("powder", _powder),
                     ("laundry", _laundry), ("living", _living),
                     ("kitchen", _kitchen), ("dining", _dining),
                     ("family", _family), ("back_hall", _back_hall),
                     ("office", _office), ("rec", _rec),
                     ("upper_hall", _upper_hall), ("bed2", _bed2),
                     ("bed3", _bed3), ("primary", _primary),
                     ("closet", _closet), ("ensuite", _ensuite),
                     ("bed4", _bed4), ("bath", _bath), ("bonus", _bonus),
                     ("bed5", _bed5)):
        fn(b)
        done[name] = "ok"
    return done


# -- ground floor ---------------------------------------------------------

def _foyer(b):
    f.rug(b, GF, -300, -13, -298, -11, "minecraft:light_blue_carpet")
    # A console table against the west wall, with somewhere to drop keys.
    b.set(-300, GF + 1, -9, "minecraft:stripped_oak_wood[axis=x]")
    b.set(-300, GF + 1, -8, "minecraft:stripped_oak_wood[axis=x]")
    b.set(-300, GF + 2, -9, "minecraft:flower_pot")
    f.plant(b, GF, -300, -5, "minecraft:potted_azalea_bush")
    f.plant(b, GF, -300, -3, "minecraft:potted_fern")
    b.set(-300, GF + 3, -12, "minecraft:oak_wall_sign"
          "[facing=east,waterlogged=false]")


def _powder(b):
    f.toilet(b, GF, -304, -12, "north")
    f.vanity(b, GF, -302, -12, "east")
    f.plant(b, GF, -302, -10, "minecraft:potted_bamboo")


def _laundry(b):
    f.appliance(b, GF, -305, -7, "east")
    f.appliance(b, GF, -305, -6, "east")
    b.set(-305, GF + 1, -5, "minecraft:cauldron")
    for x in range(-304, -301):
        b.set(x, GF + 3, -8, "minecraft:barrel[facing=south,open=false]")
    b.set(-302, GF + 1, -4, "minecraft:composter[level=0]")


def _living(b):
    # A big rug under the seating, so the room reads as one arrangement.
    f.rug(b, GF, -293, -13, -287, -7, "minecraft:white_carpet")
    f.sofa(b, GF, -289, -12, -289, -8, "east")
    f.chair(b, GF, -292, -12, "north")
    f.chair(b, GF, -292, -8, "south")
    f.low_table(b, GF, -292, -11, -291, -9)
    # Bookcases in the pier between the two front windows.
    f.shelf(b, GF, -287, -13, -285, -13, (1, 2, 3))
    # An upright piano at the quiet end.
    for z in (-6, -5, -4):
        b.set(-293, GF + 1, z, "minecraft:black_concrete")
        b.set(-293, GF + 2, z, "minecraft:black_concrete")
    b.set(-293, GF + 3, -5, "minecraft:note_block[instrument=harp,note=0,"
          "powered=false]")
    f.chair(b, GF, -292, -5, "east")
    # A reading corner at the east end.
    f.chair(b, GF, -279, -12, "east")
    f.plant(b, GF, -279, -11, "minecraft:potted_birch_sapling")
    f.shelf(b, GF, -278, -10, -278, -7, (1, 2, 3))
    f.plant(b, GF, -280, -4, "minecraft:potted_azalea_bush")
    # A card table under the second front window, and a console beneath it.
    f.table(b, GF, -283, -10, -282, -9)
    f.chair(b, GF, -284, -10, "west")
    f.chair(b, GF, -284, -9, "west")
    f.chair(b, GF, -281, -10, "east")
    f.chair(b, GF, -281, -9, "east")
    for x in range(-283, -280):
        b.set(x, GF + 1, -13, "minecraft:barrel[facing=south,open=false]")
        b.set(x, GF + 2, -13,
              "minecraft:smooth_quartz_slab[type=top,waterlogged=false]")
    f.rug(b, GF, -285, -12, -279, -7, "minecraft:brown_carpet")


def _kitchen(b):
    # The counter stops at z=2: south of the cased opening into the back
    # hall, which the run would otherwise wall off.
    f.counter(b, GF, -300, 2, -300, 6)
    f.sink(b, GF, -300, 3)
    f.stove(b, GF, -300, 5, "east")
    f.fridge(b, GF, -300, 6, "east")
    f.wall_cabinets(b, GF, -300, 2, -300, 4, "east")
    f.dishwasher(b, GF, -300, 4, "east")
    # The island, with two stools pulled up to it.
    f.counter(b, GF, -297, 1, -296, 4)
    f.stool(b, GF, -295, 2)
    f.stool(b, GF, -295, 3)
    # A pantry against the wall to the living room.
    for x in (-294, -293):
        for y in (GF + 1, GF + 2, GF + 3):
            b.set(x, y, -2, "minecraft:barrel[facing=south,open=false]")


def _dining(b):
    f.table(b, GF, -286, 0, -285, 3, cloth="minecraft:white_carpet")
    for z in range(0, 4):
        f.chair(b, GF, -287, z, "west")
        f.chair(b, GF, -284, z, "east")
    f.chair(b, GF, -286, -1, "north")
    f.chair(b, GF, -285, 4, "south")
    # A sideboard against the wall to the living room.
    for x in range(-282, -279):
        b.set(x, GF + 1, -2, "minecraft:barrel[facing=south,open=false]")
        b.set(x, GF + 2, -2,
              "minecraft:smooth_quartz_slab[type=top,waterlogged=false]")
    f.plant(b, GF, -278, -2, "minecraft:potted_fern")


def _family(b):
    f.rug(b, GF, -293, 8, -287, 11, "minecraft:white_carpet")
    f.sofa(b, GF, -292, 8, -288, 8, "north")
    f.low_table(b, GF, -291, 10, -289, 10)
    f.chair(b, GF, -294, 10, "west")
    f.chair(b, GF, -286, 10, "east")
    # The television is hung on the south wall rather than cut into it:
    # that wall is the outside of the house and stays exactly as built.
    f.television(b, GF, -291, 11, -289, 11, "south", recess=False)
    f.media_unit(b, GF, -291, 11, -289, 11, "north")
    f.plant(b, GF, -295, 11, "minecraft:potted_azalea_bush")
    f.plant(b, GF, -279, 11, "minecraft:potted_bamboo")


def _back_hall(b):
    # Against the north wall, not across the opening into the rec room.
    f.sofa(b, GF, -307, -2, -303, -2, "north", arms=False)
    for x in range(-307, -302):
        b.set(x, GF + 3, -2, "minecraft:barrel[facing=south,open=false]")
    f.rug(b, GF, -307, 0, -303, 1, "minecraft:brown_carpet")


def _office(b):
    f.desk(b, GF, -307, 4, -305, 4, "south")
    f.chair(b, GF, -306, 5, "south")
    f.shelf(b, GF, -308, 6, -308, 10, (1, 2, 3))
    f.chair(b, GF, -303, 9, "east")
    f.plant(b, GF, -303, 11, "minecraft:potted_birch_sapling")
    f.rug(b, GF, -306, 7, -304, 10, "minecraft:gray_carpet")


def _rec(b):
    # Home cinema at the blind west end: a big screen, two rows of seats.
    for z in range(3, 9):
        for y in (GF + 2, GF + 3, GF + 4):
            b.set(-326, y, z, "minecraft:black_concrete")
    for x in (-322, -320):
        for z in range(3, 9):
            f.chair(b, GF, x, z, "east")
    # A pool table where the room is widest.
    for x, z in f.run(-316, -1, -313, 2):
        b.set(x, GF + 1, z, "minecraft:green_concrete")
    for x, z in f.run(-317, -2, -312, 3):
        if not (-316 <= x <= -313 and -1 <= z <= 2):
            b.set(x, GF + 1, z, "minecraft:stripped_oak_wood[axis=x]")
    # And a bar along the south wall.
    f.counter(b, GF, -318, 11, -314, 11)
    for x in range(-318, -313):
        b.set(x, GF + 3, 11, "minecraft:barrel[facing=north,open=false]")
    for x in (-317, -315):
        f.stool(b, GF, x, 10)
    f.rug(b, GF, -324, 3, -319, 8, "minecraft:black_carpet")
    f.low_table(b, GF, -321, 4, -321, 7)
    f.plant(b, GF, -326, 11, "minecraft:potted_bamboo")
    f.plant(b, GF, -326, -2, "minecraft:potted_azalea_bush")
    f.shelf(b, GF, -326, 5, -326, 7, (1,), "minecraft:barrel[facing=east,open=false]")


# -- upper floor ----------------------------------------------------------

def _upper_hall(b):
    # A window seat in the bay, at the head of the stairs.
    for x in range(-298, -294):
        b.set(x, UF + 1, -16, "minecraft:oak_stairs[facing=south,half=bottom,"
                              "shape=straight,waterlogged=false]")
    f.plant(b, UF, -298, -14, "minecraft:potted_fern")
    f.shelf(b, UF, -299, 11, -296, 11, (1, 2), "minecraft:bookshelf")


def _bed2(b):
    f.bed(b, UF, -312, -10, "west", "light_blue")
    f.nightstand(b, UF, -313, -11)
    f.nightstand(b, UF, -313, -9)
    f.wardrobe(b, UF, -309, -13, -307, -13, "south")
    f.desk(b, UF, -304, -15, -302, -15, "south")
    f.chair(b, UF, -303, -14, "south")
    f.rug(b, UF, -309, -11, -306, -9, "minecraft:white_carpet")


def _bed3(b):
    f.bed(b, UF, -312, -4, "west", "green")
    f.nightstand(b, UF, -313, -5)
    f.nightstand(b, UF, -313, -3)
    f.wardrobe(b, UF, -306, -6, -304, -6, "south")
    f.shelf(b, UF, -313, -1, -311, -1, (1, 2, 3))
    f.rug(b, UF, -309, -5, -306, -3, "minecraft:white_carpet")


def _primary(b):
    # Two beds side by side: the only way to build a king in this game.
    f.bed(b, UF, -305, 2, "north", "white")
    f.bed(b, UF, -304, 2, "north", "white")
    f.nightstand(b, UF, -306, 1)
    f.nightstand(b, UF, -303, 1)
    for x in range(-306, -302):
        b.set(x, UF + 1, 4,
              "minecraft:oak_stairs[facing=north,half=bottom,shape=straight,"
              "waterlogged=false]")
    f.television(b, UF, -308, 6, -308, 8, "west", recess=False)
    f.chair(b, UF, -302, 9, "east")
    f.plant(b, UF, -302, 11, "minecraft:potted_azalea_bush")
    f.rug(b, UF, -307, 6, -302, 10, "minecraft:light_gray_carpet")


def _closet(b):
    f.wardrobe(b, UF, -313, 1, -313, 5, "east")
    f.wardrobe(b, UF, -310, 1, -310, 2, "west")
    f.wardrobe(b, UF, -310, 4, -310, 5, "west")


def _ensuite(b):
    f.bathtub(b, UF, -312, 10, -311, 10)
    f.toilet(b, UF, -312, 8, "west")
    f.vanity(b, UF, -310, 8, "east")
    f.plant(b, UF, -310, 7, "minecraft:potted_bamboo")


def _bed4(b):
    f.bed(b, UF, -291, -10, "north", "brown")
    f.nightstand(b, UF, -292, -11)
    f.nightstand(b, UF, -290, -11)
    f.desk(b, UF, -292, -15, -290, -15, "south")
    f.chair(b, UF, -291, -14, "south")
    f.wardrobe(b, UF, -280, -6, -278, -6, "south")
    f.shelf(b, UF, -284, -2, -282, -2, (1, 2))
    f.rug(b, UF, -288, -11, -285, -9, "minecraft:white_carpet")


def _bath(b):
    f.bathtub(b, UF, -292, -3, -291, -3)
    f.toilet(b, UF, -288, -5, "east")
    f.vanity(b, UF, -290, -5, "north")
    f.vanity(b, UF, -289, -5, "north")
    f.plant(b, UF, -286, -2, "minecraft:potted_fern")


def _bonus(b):
    f.sofa(b, UF, -290, 1, -286, 1, "north")
    f.low_table(b, UF, -289, 3, -287, 3)
    f.television(b, UF, -289, 5, -287, 5, "south", recess=False)
    f.table(b, UF, -283, 2, -282, 4)
    for z in range(2, 5):
        f.chair(b, UF, -284, z, "west")
        f.chair(b, UF, -281, z, "east")
    f.shelf(b, UF, -293, 4, -293, 5, (1, 2, 3))
    f.rug(b, UF, -291, 2, -285, 4, "minecraft:white_carpet")


def _bed5(b):
    f.bed(b, UF, -291, 9, "south", "yellow")
    f.nightstand(b, UF, -292, 9)
    f.desk(b, UF, -284, 8, -282, 8, "north")
    f.chair(b, UF, -283, 7, "north")
    f.shelf(b, UF, -278, 8, -278, 11, (1, 2, 3))
    f.wardrobe(b, UF, -293, 11, -291, 11, "north")
    f.rug(b, UF, -289, 8, -286, 10, "minecraft:white_carpet")


# =========================================================================
# Light
# =========================================================================

#: Pendant fittings, by (x, z). The y is worked out from whatever ceiling
#: happens to be over that cell, which upstairs is the roof itself.
GROUND_PENDANTS = [
    (-298, -11), (-298, -5),                       # foyer
    (-303, -11), (-303, -6),                       # powder room, laundry
    (-291, -11), (-284, -11), (-291, -5), (-282, -6),   # living room
    (-297, 2), (-296, 3), (-298, -1), (-295, 5),   # kitchen
    (-286, 1), (-285, 2), (-281, 0),               # dining
    (-295, 9), (-290, 9), (-283, 9), (-279, 8),    # family
    (-305, 0), (-306, 6), (-304, 9),               # back hall, office
    (-324, 0), (-318, 0), (-313, 5), (-324, 8),
    (-321, 5), (-316, 8), (-312, -1), (-325, 4), (-311, 9),   # rec room
]

UPPER_PENDANTS = [
    (-297, -15), (-297, -10), (-296, -4), (-297, 2), (-296, 8),   # hall
    (-310, -11), (-305, -10), (-303, -15),         # bedroom 2
    (-310, -4), (-305, -3),                        # bedroom 3
    (-305, 6), (-303, 9), (-307, 3),               # primary
    (-311, 3), (-311, 9),                          # closet, ensuite
    (-290, -11), (-284, -10), (-291, -15), (-281, -4),
    (-280, -11), (-280, -8),                       # bedroom 4
    (-290, -4), (-288, -3),                        # bathroom
    (-290, 2), (-284, 3), (-280, 4),               # bonus
    (-290, 9), (-284, 9), (-279, 10),              # bedroom 5
]


def _hang(b, x, z, floor_y):
    """A pendant on whatever ceiling is over this cell.

    The search starts five blocks up rather than one: anything lower is
    furniture, and a fitting hung from the top of a wardrobe is not a
    ceiling light.
    """
    top = _ceiling_above(b, x, z, floor_y + 4)
    if top <= floor_y + 5:
        return False
    b.set(x, top - 1, z, "minecraft:lantern[hanging=true,waterlogged=false]")
    return True


def light(b) -> dict:
    hung = 0
    for x, z in GROUND_PENDANTS:
        hung += _hang(b, x, z, GF)
    for x, z in UPPER_PENDANTS:
        hung += _hang(b, x, z, UF)
    return {"pendants": hung}


def patch_dark(b, dark_cells) -> dict:
    """Bury a light under the carpet wherever the sweep found none.

    Glowstone in the floor under a carpet is invisible and lights the
    cell above it, which is the only way to finish a corner that the
    pendants cannot reach without hanging a lantern in the middle of
    somebody's bedroom.
    """
    patched = 0
    for x, y, z in dark_cells:
        floor_y = y - 1
        under = b.save.get_block(x, floor_y, z)
        if under is None:
            continue
        b.set(x, floor_y, z, "minecraft:glowstone")
        patched += 1
    return {"patched": patched}
