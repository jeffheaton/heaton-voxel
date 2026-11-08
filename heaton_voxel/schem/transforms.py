"""Rotation and mirroring: the direction vocabulary.

The geometry is arithmetic; rewriting block states -- facing=north becoming
facing=east -- takes a vocabulary of the game's direction-bearing
properties, kept here in one place, value-guarded, and validated against
vanilla-placed blocks (see the conformance corpus). Anything it cannot
cover passes through unchanged and is reported, never guessed at.

Three of its functions are public, under their C# twins' names, for
turning a state with no schematic around it: :func:`properties_rotated_cw`,
:func:`properties_mirrored` and :func:`directional_leftovers`.
"""

from __future__ import annotations

from ..blocks import BlockState
from ..nbt import TagCompound, TagDouble, TagInt, TagIntArray, TagList

#
# The geometry is pure arithmetic, but rewriting block states -- facing=north
# becoming facing=east -- takes a vocabulary of the game's direction-bearing
# properties. That vocabulary is game knowledge, kept here in one place, and
# it is *value-guarded*: a rule fires only when both the property name and its
# value match the game's conventions, so unknown states pass through
# untouched. Any property that then still LOOKS directional is reported on
# the result's ``transform_warnings`` rather than silently left behind.
#
# Conventions verified against real saves: sign/banner ``rotation`` runs
# 0=south, 4=west, 8=north, 12=east; hanging entities store ``Facing``
# (item frames: the 3D Direction order down,up,north,south,west,east) or
# ``facing`` (paintings: the 2D order south,west,north,east).

# One quarter turn clockwise seen from above: north faces become east.
_DIR_CW = {"north": "east", "east": "south", "south": "west", "west": "north"}
_AXIS_CW = {"x": "z", "z": "x"}
_RAIL_CW = {
    "north_south": "east_west",
    "east_west": "north_south",
    "ascending_north": "ascending_east",
    "ascending_east": "ascending_south",
    "ascending_south": "ascending_west",
    "ascending_west": "ascending_north",
    "north_east": "south_east",
    "south_east": "south_west",
    "south_west": "north_west",
    "north_west": "north_east",
}

_DIR_MIRROR = {
    "x": {"east": "west", "west": "east"},
    "z": {"north": "south", "south": "north"},
    "y": {"up": "down", "down": "up"},
}
_RAIL_MIRROR = {
    "x": {
        "ascending_east": "ascending_west",
        "ascending_west": "ascending_east",
        "north_east": "north_west",
        "north_west": "north_east",
        "south_east": "south_west",
        "south_west": "south_east",
    },
    "z": {
        "ascending_north": "ascending_south",
        "ascending_south": "ascending_north",
        "north_east": "south_east",
        "south_east": "north_east",
        "north_west": "south_west",
        "south_west": "north_west",
    },
    "y": {},
}
# Any horizontal mirror flips chirality, so a stair corner's left becomes
# its right whichever axis the mirror runs along.
_STAIR_MIRROR = {
    "inner_left": "inner_right",
    "inner_right": "inner_left",
    "outer_left": "outer_right",
    "outer_right": "outer_left",
}
_LEFT_RIGHT = {"left": "right", "right": "left"}
_HALF_MIRROR_Y = {"top": "bottom", "bottom": "top", "upper": "lower", "lower": "upper"}
_TOP_BOTTOM = {"top": "bottom", "bottom": "top"}
_FACE_MIRROR_Y = {"floor": "ceiling", "ceiling": "floor"}
_TRUE_FALSE = {"true": "false", "false": "true"}

# Item frames (and shulker AttachFace): the 3D Direction enum order.
_FACING3_CW = {2: 5, 5: 3, 3: 4, 4: 2}  # down/up fixed
_FACING3_MIRROR = {"x": {4: 5, 5: 4}, "z": {2: 3, 3: 2}, "y": {0: 1, 1: 0}}
# Paintings: the 2D order south, west, north, east -- one step is a quarter
# turn clockwise.
_FACING2_CW = {0: 1, 1: 2, 2: 3, 3: 0}
_FACING2_MIRROR = {"x": {1: 3, 3: 1}, "z": {0: 2, 2: 0}, "y": {}}

# Property names the transforms understand. Anything outside this set whose
# value is a bare direction word gets flagged, not guessed at.
_TRANSFORM_KEYS = frozenset(
    {
        "facing", "axis", "rotation", "shape", "type", "hinge", "half",
        "face", "orientation", "vertical_direction", "attachment", "hanging",
        "north", "south", "east", "west", "up", "down",
    }
)
_DIRECTION_WORDS = frozenset({"north", "south", "east", "west", "up", "down"})


def _wrap_degrees(value: float) -> float:
    return ((value + 180.0) % 360.0) - 180.0


def _rotate_int(value, fn):
    """A sign/banner ``rotation`` step (mod 16), guarded against junk."""
    try:
        return str(fn(int(value)) % 16)
    except (TypeError, ValueError):
        return value


def properties_rotated_cw(properties: dict) -> dict:
    """One clockwise quarter turn, seen from above, of a state's properties.

    ``facing=north`` becomes ``facing=east``, ``axis=x`` becomes ``axis=z``,
    a sign's ``rotation`` steps by 4, and rail shapes and per-face
    properties (a fence's ``north=true``, a wall's ``north=tall``) turn
    with it; anything the vocabulary does not know passes through
    unchanged. Returns a new dict in the same key order (a per-face key
    renamed in place), and leaves ``properties`` alone. The C# twin is
    ``Transforms.PropertiesRotatedCw``.
    """
    out = {}
    for key, value in properties.items():
        key = _DIR_CW.get(key, key)  # per-face booleans: north=true -> east=true
        if key == "facing":
            value = _DIR_CW.get(value, value)
        elif key == "axis":
            value = _AXIS_CW.get(value, value)
        elif key == "rotation":
            value = _rotate_int(value, lambda r: r + 4)
        elif key == "shape":
            value = _RAIL_CW.get(value, value)  # stair shapes rotate with facing
        elif key == "orientation":
            value = "_".join(_DIR_CW.get(part, part) for part in value.split("_"))
        out[key] = value
    return out


def properties_mirrored(properties: dict, axis: str) -> dict:
    """A state's properties mirrored along ``axis``: ``"x"`` swaps east and
    west, ``"z"`` north and south, ``"y"`` up and down.

    A horizontal mirror also flips chirality -- a stair corner's left
    becomes its right, a door's hinge and a chest's half swap sides -- and
    the vertical one turns halves, floor and ceiling attachments and
    hanging things over. Anything the vocabulary does not know passes
    through unchanged. Returns a new dict in the same key order, and
    leaves ``properties`` alone. The C# twin is
    ``Transforms.PropertiesMirrored``.
    """
    pairs = _DIR_MIRROR[axis]
    horizontal = axis in ("x", "z")
    out = {}
    for key, value in properties.items():
        key = pairs.get(key, key)  # per-face booleans swap sides
        if key == "facing":
            value = pairs.get(value, value)
        elif key == "rotation" and horizontal:
            base = 16 if axis == "x" else 8
            value = _rotate_int(value, lambda r, base=base: base - r)
        elif key == "shape" and horizontal:
            value = _RAIL_MIRROR[axis].get(value) or _STAIR_MIRROR.get(value, value)
        elif key == "hinge" and horizontal:
            value = _LEFT_RIGHT.get(value, value)
        elif key == "type":
            swap = _LEFT_RIGHT if horizontal else _TOP_BOTTOM
            value = swap.get(value, value)  # chest halves / slab halves only
        elif key == "orientation":
            value = "_".join(pairs.get(part, part) for part in value.split("_"))
        elif not horizontal:
            if key == "half":
                value = _HALF_MIRROR_Y.get(value, value)
            elif key in ("face", "attachment"):
                value = _FACE_MIRROR_Y.get(value, value)
            elif key == "vertical_direction":
                value = pairs.get(value, value)
            elif key == "hanging":
                value = _TRUE_FALSE.get(value, value)
        out[key] = value
    return out


def _map_state(state: BlockState, mapper) -> BlockState:
    properties = state.properties
    if not properties:
        return state
    mapped = mapper(properties)
    if mapped == properties:
        return state  # untouched states keep their original palette bytes
    return BlockState(state.name, mapped)


def directional_leftovers(palette) -> list[str]:
    """States carrying direction-looking properties the vocabulary misses.

    One message for each distinct block, property and value, in the order
    ``palette`` (any iterable of :class:`~heaton_voxel.blocks.BlockState`)
    first shows it: a property outside the vocabulary whose value is a bare
    direction word, which a turn would leave pointing the old way. Empty
    when every state turns cleanly. The C# twin is
    ``Transforms.DirectionalLeftovers``.
    """
    warnings: list[str] = []
    seen: set[str] = set()
    for state in palette:
        for key, value in state.properties.items():
            if key not in _TRANSFORM_KEYS and value in _DIRECTION_WORDS:
                message = (
                    f"{state.name}: property {key}={value} looks directional "
                    f"but is outside the transform vocabulary; left as-is"
                )
                if message not in seen:
                    seen.add(message)
                    warnings.append(message)
    return warnings


# The names the schem package, its conformance generator and every caller
# before these were public import. Kept so none of them moves.
_properties_rotated_cw = properties_rotated_cw
_properties_mirrored = properties_mirrored
_directional_leftovers = directional_leftovers


def _compose_byte_table(table: dict, times: int) -> dict:
    out = {}
    for source in range(6):
        value = source
        for _ in range(times):
            value = table.get(value, value)
        if value != source:
            out[source] = value
    return out


def _transform_entity(
    tag: TagCompound, *, point, cell, yaw, pitch_sign, facing3, facing2, vector
) -> None:
    """Rotate or mirror one entity compound, in place. Recurses passengers."""
    pos = tag.get("Pos")
    if isinstance(pos, TagList) and len(pos) == 3:
        x, y, z = point(float(pos[0]), float(pos[1]), float(pos[2]))
        pos[0], pos[1], pos[2] = TagDouble(x), TagDouble(y), TagDouble(z)
    rotation = tag.get("Rotation")
    if isinstance(rotation, TagList) and len(rotation) == 2:
        rotation[0] = type(rotation[0])(_wrap_degrees(yaw(float(rotation[0]))))
        rotation[1] = type(rotation[1])(pitch_sign * float(rotation[1]))
    motion = tag.get("Motion")
    if isinstance(motion, TagList) and len(motion) == 3:
        x, y, z = vector(float(motion[0]), float(motion[1]), float(motion[2]))
        motion[0], motion[1], motion[2] = TagDouble(x), TagDouble(y), TagDouble(z)

    block = tag.get("block_pos")
    if isinstance(block, TagIntArray) and len(block) == 3:
        tag["block_pos"] = TagIntArray(
            list(cell(int(block[0]), int(block[1]), int(block[2])))
        )
    if all(name in tag for name in ("TileX", "TileY", "TileZ")):
        tx, ty, tz = cell(int(tag["TileX"]), int(tag["TileY"]), int(tag["TileZ"]))
        tag["TileX"], tag["TileY"], tag["TileZ"] = TagInt(tx), TagInt(ty), TagInt(tz)
    for key in ("leash", "Leash"):
        leash = tag.get(key)
        if isinstance(leash, TagCompound) and all(k in leash for k in "XYZ"):
            lx, ly, lz = cell(int(leash["X"]), int(leash["Y"]), int(leash["Z"]))
            leash["X"], leash["Y"], leash["Z"] = TagInt(lx), TagInt(ly), TagInt(lz)
    sleeping = tag.get("sleeping_pos")
    if isinstance(sleeping, TagIntArray) and len(sleeping) == 3:
        tag["sleeping_pos"] = TagIntArray(
            list(cell(int(sleeping[0]), int(sleeping[1]), int(sleeping[2])))
        )

    for name, table in (("Facing", facing3), ("AttachFace", facing3), ("facing", facing2)):
        stored = tag.get(name)
        if isinstance(stored, int) and not isinstance(stored, bool) and stored in table:
            tag[name] = type(stored)(table[stored])

    passengers = tag.get("Passengers")
    if isinstance(passengers, TagList):
        for passenger in passengers:
            if isinstance(passenger, TagCompound):
                _transform_entity(
                    passenger,
                    point=point,
                    cell=cell,
                    yaw=yaw,
                    pitch_sign=pitch_sign,
                    facing3=facing3,
                    facing2=facing2,
                    vector=vector,
                )
