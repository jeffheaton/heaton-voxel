"""A sample elevation, read off a real building: the taste lives here.

    from elevations import OFFICE_FACES, OFFICE_SOUTH, office_stories

    b.shell(x0, z0, x0 + 40, z0 + 22, OFFICE_SOUTH, faces=OFFICE_FACES,
            ground="minecraft:smooth_stone", floor="minecraft:oak_planks",
            deck="minecraft:oak_planks", **office_stories(4, y))

An :class:`~heaton_voxel.craft.build.Elevation` is a wall's face as bands
bottom up, each with its own :class:`~heaton_voxel.craft.build.Rhythm` across.
``heaton_voxel.craft`` ships none of its own: bays and glass are a build's
choices, so the one here is named for what it was read off, and every number
in it was read from that building and replayed against it, cell for cell.

* :data:`OFFICE_NORTH`, :data:`OFFICE_SOUTH` and :data:`OFFICE_EAST_WEST` --
  the faces of a five-story office block on a 41 by 23 footprint: a brick
  grid of punched windows between two glass corner pavilions, on a ground
  story whose piers keep their own spacing and stand out of line with those
  above, its glass a block behind them. The north front has the entrance,
  three doors under a window. Its shaft takes whatever height the wall has,
  so the same rule stands any number of stories on the same base under the
  same crown: ``office_stories(n)`` gives ``top`` and ``stories`` for ``n``
  upper ones.
* :data:`TOWER_SOUTH`, :data:`TOWER_NORTH` and :data:`TOWER_EAST_WEST` -- the
  same hand on a slimmer block, 27 by 23: the office's bays and glass, its
  plinth, fins and crown, its entrance on the south front, and a ground
  story of eight rows, since a switchback lands a floor every four and
  ``tower_stories(n)`` puts the first of them on its second turn.
* :data:`PALACE_LOWER`, :data:`PALACE_UPPER` and :data:`PALACE_TOP` -- each a
  face rule by wall, for the three tiers of a white wool and pale glass hotel,
  21 by 37, 17 by 33 and 13 square: a grid of tall lights, floor lines showing
  at its foot and shoulders, and a lattice of alternating lights at the top
  of each tier. The same lattice under each setback is what holds the tiers
  together. Each stretches, so any height will do above its fixed courses.
"""

from __future__ import annotations

from heaton_voxel.craft import Band, Doors, Elevation, Glazing, Layers, Rhythm

BRICK = "minecraft:bricks"
WOOL = "minecraft:light_gray_wool"
SANDSTONE = "minecraft:red_sandstone"
GRAY = "minecraft:gray_stained_glass_pane"
LIGHT = "minecraft:light_gray_stained_glass_pane"
BLACK = "minecraft:black_stained_glass_pane"
AIR = "minecraft:air"

#: The upper stories' glass runs past the floors at the corners and stops at
#: a brick spandrel between the piers.
CURTAIN = Glazing(GRAY, GRAY, LIGHT, LIGHT)
LONG_WINDOW = Glazing(GRAY, LIGHT, GRAY, spandrel=BRICK)
SHORT_WINDOW = Glazing(GRAY, LIGHT, LIGHT, spandrel=BRICK)

#: A pier and the four columns of bay beside it.
_BAY = Rhythm("pier", ("bay", 4))

#: 7 | 4 4 3 4 4 | 7, mirrored about the middle of the wall.
LONG_SHAFT = (Rhythm("post", ("glass", 7)) + _BAY * 2 + "pier"
              ).mirrored(center=("bay", 3))
#: 5 | 4 4 | 5, between the corners the long walls own.
SHORT_SHAFT = Rhythm(("glass", 5)) + _BAY * 2 + "pier" + ("glass", 5)

#: Piers every five from two-wide corners, and a pair of fins at the middle.
_GROUND_BAY = Rhythm(("open", 4), "pier")
LONG_GROUND = (Rhythm(("corner", 2)) + _GROUND_BAY * 3 + "open" + "fin"
               ).mirrored(center=("middle", 3))
SHORT_GROUND = (Rhythm(("corner", 1)) + _GROUND_BAY * 3 + ("open", 4)
                + ("corner", 1))

#: The ground story's five rows, bottom up: its piers, the jambs behind its
#: fins, and its glass.
_GROUND_ROWS = ((SANDSTONE, SANDSTONE, GRAY),
                (SANDSTONE, BRICK, LIGHT),
                (BRICK, BRICK, LIGHT),
                (BRICK, BRICK, LIGHT),
                (BRICK, BRICK, LIGHT))
#: The entrance's middle bay, the same five rows: three doors under a window
#: between two brick courses.
_DOORS = Doors("minecraft:oak_door")
_ENTRANCE = (_DOORS, _DOORS, BRICK, GRAY, BRICK)


def _shaft(rhythm, window, *, rows=None, pier=BRICK, post=WOOL) -> Band:
    roles = {"glass": CURTAIN, "bay": window, "pier": pier}
    if "post" in rhythm.roles:
        roles["post"] = post
    return Band(rows, rhythm, roles)


def _ground(rhythm, *, entrance: bool, rows=_GROUND_ROWS,
            middle=_ENTRANCE) -> list:
    """The ground story, a band a row: its piers two deep, its glass a block
    behind them, and its fins before the jambs at the middle."""
    bands = []
    for i, (pier, jamb, glass) in enumerate(rows):
        roles = {"open": Layers(AIR, glass), "pier": Layers(pier, pier),
                 "corner": Layers(pier, pier)}
        if "fin" in rhythm.roles:
            roles["fin"] = Layers(BLACK, jamb)
            roles["middle"] = Layers(AIR, middle[i] if entrance else glass)
        bands.append(Band(1, rhythm, roles))
    return bands


def _elevation(ground, shaft, window, *, entrance: bool = False,
               rows=_GROUND_ROWS, middle=_ENTRANCE) -> Elevation:
    return Elevation(
        *_ground(ground, entrance=entrance, rows=rows, middle=middle),
        # The first floor's edge, one course round the whole building.
        Band(1, Rhythm(("belt", len(ground))), {"belt": BRICK}),
        _shaft(shaft, window),
        # The crown: the same bays, the piers turned to wool.
        _shaft(shaft, window, rows=3, pier=WOOL),
        # The head course: wool across the middle, the corners still glass.
        _shaft(shaft, WOOL, rows=1, pier=WOOL),
    )


OFFICE_NORTH = _elevation(LONG_GROUND, LONG_SHAFT, LONG_WINDOW, entrance=True)
OFFICE_SOUTH = _elevation(LONG_GROUND, LONG_SHAFT, LONG_WINDOW)
OFFICE_EAST_WEST = _elevation(SHORT_GROUND, SHORT_SHAFT, SHORT_WINDOW)
#: The block's faces, for ``shell(faces=)``.
OFFICE_FACES = {"north": OFFICE_NORTH, "south": OFFICE_SOUTH,
                "east": OFFICE_EAST_WEST, "west": OFFICE_EAST_WEST}


#: 5 | 4 3 4 | 5: the office's bays with one taken off each side, the entrance
#: bay of three still in the middle between its two piers.
TOWER_SHAFT = (Rhythm("post", ("glass", 5)) + _BAY + "pier"
               ).mirrored(center=("bay", 3))
#: Its ground piers stand under the shaft's, and its fins under the two beside
#: the entrance, where the office let them wander.
TOWER_GROUND = (Rhythm(("corner", 2)) + _GROUND_BAY + ("open", 4) + "fin"
                ).mirrored(center=("middle", 3))

#: Eight rows to the first floor, the top one its edge: the office's five
#: with two more of brick.
_TOWER_ROWS = (*_GROUND_ROWS, (BRICK, BRICK, LIGHT), (BRICK, BRICK, LIGHT))
_TOWER_ENTRANCE = (_DOORS, _DOORS, BRICK, GRAY, LIGHT, GRAY, BRICK)

TOWER_SOUTH = _elevation(TOWER_GROUND, TOWER_SHAFT, LONG_WINDOW, entrance=True,
                         rows=_TOWER_ROWS, middle=_TOWER_ENTRANCE)
TOWER_NORTH = _elevation(TOWER_GROUND, TOWER_SHAFT, LONG_WINDOW,
                         rows=_TOWER_ROWS)
TOWER_EAST_WEST = _elevation(SHORT_GROUND, SHORT_SHAFT, SHORT_WINDOW,
                             rows=_TOWER_ROWS)
#: The tower's faces, for ``shell(faces=)``.
TOWER_FACES = {"north": TOWER_NORTH, "south": TOWER_SOUTH,
               "east": TOWER_EAST_WEST, "west": TOWER_EAST_WEST}


def tower_stories(upper: int, floor_y: int = 0) -> dict:
    """``top`` and ``stories`` for a tower of ``upper`` stories over its
    ground one, standing on ``floor_y``: the first floor eight rows up, and
    four rows a story from there, each where a switchback of run four lands
    if its first tread stands on the ground floor."""
    first = floor_y + 8
    return {"floor_y": floor_y,
            "top": first + 4 * upper,
            "stories": tuple(first + 4 * i for i in range(upper))}


def office_stories(upper: int, floor_y: int = 0) -> dict:
    """``top`` and ``stories`` for a block of ``upper`` stories over its
    ground one, standing on ``floor_y``: the ground story five rows under
    the first floor, and four rows a story from there. The original has
    four."""
    first = floor_y + 6
    return {"floor_y": floor_y,
            "top": first + 4 * upper,
            "stories": tuple(first + 4 * i for i in range(upper))}


WHITE = "minecraft:white_wool"
PALE = "minecraft:light_blue_stained_glass"
PALE_PANE = "minecraft:light_blue_stained_glass_pane"


def _drawn(rows, text: str) -> Band:
    """A band drawn as it stands: ``w`` a column of wool, ``#`` of glass."""
    runs, roles = [], {}
    for mark in text:
        role, block = ("pier", WHITE) if mark == "w" else ("glass", PALE)
        roles[role] = block
        if runs and runs[-1][0] == role:
            runs[-1] = (role, runs[-1][1] + 1)
        else:
            runs.append((role, 1))
    return Band(rows, Rhythm(*runs), roles)


def _lower(lights: str, lattice: tuple) -> list:
    """The bands of the lowest shaft over its first floor line, ``lights`` a
    row of it and ``lattice`` the three rows of alternating lights under the
    head course, itself the last. Floor lines show for the first three
    stories and again near the top, with the lights run past the floors
    between, and a double line at the shoulder."""
    belt = "w" * len(lights)
    return [_drawn(1, belt), _drawn(3, lights), _drawn(1, belt),
            _drawn(3, lights), _drawn(1, belt), _drawn(3, lights),
            _drawn(1, belt), _drawn(None, lights), _drawn(1, belt),
            _drawn(3, lights), _drawn(1, belt), _drawn(7, lights),
            _drawn(2, belt), _drawn(3, lights),
            *(_drawn(rows, text) for rows, text in zip((3, 2, 1, 1), lattice))]


def _upper(lights: str, lattice: tuple) -> list:
    """The middle shaft: the same line twice, nearer the top."""
    belt = "w" * len(lights)
    return [_drawn(None, lights), _drawn(1, belt), _drawn(3, lights),
            _drawn(1, belt), _drawn(8, lights),
            *(_drawn(rows, text) for rows, text in zip((3, 2, 1, 1), lattice))]


def _top(lights: str, lattice: tuple) -> list:
    """The top shaft, its lattice over its lights. The building's second
    course from the top is a block out of true on each face; this one is
    drawn as its other rows would have it."""
    return [_drawn(None, lights),
            *(_drawn(rows, text) for rows, text in zip((3, 2, 1, 1), lattice))]


_ACROSS_WIDE = "w###w#####w#####w###w"
_ACROSS_LONG = "###w###w#####w###w###w#####w###w###"
_ACROSS_MIDDLE = "w###w###w###w###w"
_ACROSS_MIDDLE_LONG = "###w###w###w###w###w###w###w###"
_ACROSS_TOP = "w###w###w###w"
_ACROSS_TOP_SHORT = "###w###w###"

#: Each tier's lattice, bottom up, as drawn across its wide and its long wall.
_LOWER_WIDE = ("ww#w#w###w#w###w#w#ww", "w#w###w###w###w###w#w",
               "ww#w#w#w#w#w#w#w#w#ww", "w###w###w###w###w###w")
_LOWER_LONG = ("w#w#w#w#w#w#w#w#w#w#w#w#w#w#w#w#w#w",
               "#w###w###w###w#w###w#w###w###w###w#",
               "w#w#w#w#w#w#w###w#w###w#w#w#w#w#w#w",
               "###w###w###w#####w#####w###w###w###")
_MIDDLE_WIDE = ("ww#w#w#w#w#w#w#ww", "w#w###w###w###w#w",
                "ww#w#w#w#w#w#w#ww", "w###w###w###w###w")
_MIDDLE_LONG = ("w#w#w#w#w#w#w#w#w#w#w#w#w#w#w#w",
                "#w###w###w###w###w###w###w###w#",
                "w#w#w#w#w#w#w#w#w#w#w#w#w#w#w#w",
                "###w###w###w###w###w###w###w###")
_TOP_WIDE = ("ww#w#w#w#w#ww", "w#w###w###w#w", "ww#w#w#w#w#ww", "w###w###w###w")
_TOP_SHORT = ("w#w#w#w#w#w", "#w###w###w#", "w#w#w#w#w#w", "###w###w###")

_LOWER_N = Elevation(*_lower(_ACROSS_WIDE, _LOWER_WIDE))
_LOWER_E = Elevation(*_lower(_ACROSS_LONG, _LOWER_LONG))
_UPPER_N = Elevation(*_upper(_ACROSS_MIDDLE, _MIDDLE_WIDE))
_UPPER_E = Elevation(*_upper(_ACROSS_MIDDLE_LONG, _MIDDLE_LONG))
_TOP_N = Elevation(*_top(_ACROSS_TOP, _TOP_WIDE))
_TOP_E = Elevation(*_top(_ACROSS_TOP_SHORT, _TOP_SHORT))

#: The three tiers' faces, for ``Tier(faces=)``, from each tier's first row
#: over the one it stands on.
PALACE_LOWER = {"north": _LOWER_N, "south": _LOWER_N,
                "east": _LOWER_E, "west": _LOWER_E}
PALACE_UPPER = {"north": _UPPER_N, "south": _UPPER_N,
                "east": _UPPER_E, "west": _UPPER_E}
PALACE_TOP = {"north": _TOP_N, "south": _TOP_N,
              "east": _TOP_E, "west": _TOP_E}


def palace_ground(entrance: bool, lights: str, *, doors: bool = False) -> list:
    """Seven rows of arcade under a lower-tier face: wool piers where the
    building's stand, seven lights and a three-wide middle between them, the
    glass a block back and the middle holding ``doors``."""
    bands = []
    for row in range(7):
        middle = (_DOORS if doors and row < 2 else PALE_PANE
                  if row < 6 else WHITE)
        roles = {"pier": WHITE, "open": Layers(AIR, PALE_PANE if row < 6
                                               else WHITE)}
        if entrance:
            roles["middle"] = Layers(AIR, middle)
        bands.append(Band(1, Rhythm(*lights), roles))
    return bands


#: Piers 8, 4 and 8 apart, as the hotel's arcade has them.
_ARCADE_WIDE = ("pier", ("open", 7), "pier", ("middle", 3), "pier", ("open", 7),
                "pier")
_ARCADE_LONG = (("open", 7), "pier", ("open", 7), "pier", ("middle", 3), "pier",
                ("open", 7), "pier", ("open", 7))

#: The lowest tier's faces with the arcade under them, and the doors in the
#: south one: for a tier standing on the ground.
PALACE_BASE = {
    "north": Elevation(*palace_ground(True, _ARCADE_WIDE),
                       *_lower(_ACROSS_WIDE, _LOWER_WIDE)),
    "south": Elevation(*palace_ground(True, _ARCADE_WIDE, doors=True),
                       *_lower(_ACROSS_WIDE, _LOWER_WIDE)),
    "east": Elevation(*palace_ground(True, _ARCADE_LONG),
                      *_lower(_ACROSS_LONG, _LOWER_LONG)),
    "west": Elevation(*palace_ground(True, _ARCADE_LONG),
                      *_lower(_ACROSS_LONG, _LOWER_LONG)),
}
