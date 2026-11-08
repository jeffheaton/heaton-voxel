"""Geometry for builders: one compass, round shapes, lines, profiles and
frames.

Every build session needed the same five things and wrote them again: which
way a word points, which cells make a round tower, which cells a line
between two waypoints crosses, how high a road or a wall rides along the
ground under it, and how a component designed facing one way lands facing
another. The copies agreed often enough to look like one rule and differed
often enough to matter -- three disc rules, two ways to break a diagonal, a
line rasterized three times, a smoothing written with three windows and
three pass counts -- so this module holds one of each, with the
differences kept as **named choices** rather than retyped.

    from heaton_voxel.craft.geometry import (
        COMPASS, ROAD_WINDOW, Frame, disc, heading_of, line, ring, smoothed_profile)

    disc(5, center=(120, -40))               # a round tower's floor
    ring(5, center=(120, -40))               # its wall, in order around it
    heading_of(3, -3)                        # "north": a diagonal breaks toward z
    line([(0, 0), (6, 3), (6, 9)])           # the cells a road's centerline crosses
    smoothed_profile(ground, window=ROAD_WINDOW)   # the heights a road rides at

    frame = Frame((100, 64, 200), "west")    # a component designed facing south
    frame.cell(2, 0, 1)                      # where its (across, up, out) lands
    frame.state("minecraft:oak_stairs[facing=north]")   # ...facing east

**The compass** is the game's own: +x is east, +z is south, and a turn is
clockwise seen from above with north up. :data:`COMPASS` keeps the order
the builders' tables always had -- the z pair, then the x pair -- because
refusals print it.

**A round shape has one rule** (:func:`disc`, :func:`ring`): a cell is in
when its center lies within the radius, plus a slack, of the shape's
center. The slack is the rounding, in quarter blocks (:data:`ROUNDINGS`),
and the three disc rules the builds wrote are this one rule at three
slacks:

* ``"inside"`` (none) is ``dx² + dz² <= r²``: the park's lake discs
  (``tools/park/park_plan.py``), every cell's center inside the radius, so
  a small one comes out pointed -- a plus at r = 1;
* ``"quarter"`` (a quarter block) is ``dx² + dz² <= r² + r/2``: the secret
  lab's discs and rings (``tools/secret_lab/lab_common.py``);
* ``"half"`` (half a block, the default) is ``dx² + dz² <= r² + r``: the
  castle's towers (``examples/castle.py``) and the Roman city's
  (``tools/roman_city/common.py``) -- (±r, 0) stays in and the diagonals
  fill enough to read as round.

**A diagonal has two headings**, and :func:`heading_of` makes the caller
name the one it means: ``ties="z"`` (the default) breaks an exact
diagonal toward north or south, as the castle and the Roman city do;
``ties="x"`` breaks it toward east or west, as the secret lab did.

**A line is road's rule** (:func:`line`): each segment walked in as many
steps as its longer side, rounding half to even -- the rasterization
:meth:`~heaton_voxel.craft.build.Builder.road` lays its centerline by, and
the two copies in ``tools/secret_lab/m1_groundwork.py`` and
``tools/roman_city/jw_groundwork.py``.

**A profile is road's smoothing** (:func:`smoothed_profile`): the ground
along a path averaged over a window either side, then lowered to the lower
envelope that steps at most ``max_step`` (a block, unless told otherwise),
with anchors pinned. The window is the choice -- :data:`ROAD_WINDOW` road's
three cells, :data:`WALL_WINDOW` the Roman wall's five, 0 the secret lab's
wall, which averaged nothing -- and
the forward-and-back passes the three copies ran three or four times were
one: the envelope is reached by the first.

**A frame turns a component** (:class:`Frame`): a local ``(across, up,
out)`` to a world cell, and a block state with it, through the same
property vocabulary a schematic turns by
(:func:`~heaton_voxel.schem.transforms.properties_rotated_cw`), so a
framed write and a rotated paste agree.

The ``tools/`` copies stay as they are, historical, and
``examples/castle.py`` keeps its own shapes so the example stays free of
the judged tier; ``tests/test_geometry.py`` pins the castle's shapes, in
order, and the tools' disc, ring and facing functions to this module cell
for cell, :func:`line` to road's loop as it stood, and
:func:`smoothed_profile` to road's smoothing as it stood and to the two
walls' smoothing, compiled from the tools' own source. A leaf: this
imports nothing from the judged tier but its errors and its plan-entry
reader. Nothing here reads or writes a world.
"""

from __future__ import annotations

from fractions import Fraction
from types import MappingProxyType

from ..blocks import BlockState
from ..nbt import NBTError
from ..schem.transforms import (
    directional_leftovers,
    properties_mirrored,
    properties_rotated_cw,
)
from ._plan import as_state
from .errors import Refused

__all__ = [
    "COMPASS",
    "CLOCKWISE",
    "OPPOSITE",
    "turn",
    "axis_of",
    "heading_of",
    "ROUNDINGS",
    "disc",
    "ring",
    "line",
    "ROAD_WINDOW",
    "WALL_WINDOW",
    "smoothed_profile",
    "Frame",
]


# --------------------------------------------------------------------------
# The compass
# --------------------------------------------------------------------------

#: The four horizontal words and the step each takes, ``(dx, dz)``: +x is
#: east and +z is south, the game's own axes. The z pair comes first, the
#: order the builders' tables have always had, since their refusals print
#: it. Read-only: every writer shares it.
COMPASS = MappingProxyType({"north": (0, -1), "south": (0, 1),
                            "east": (1, 0), "west": (-1, 0)})

#: The four words in clockwise order, seen from above with north up.
CLOCKWISE: tuple[str, ...] = ("north", "east", "south", "west")

#: Each word's opposite, in :data:`COMPASS` order.
OPPOSITE = MappingProxyType({word: CLOCKWISE[(CLOCKWISE.index(word) + 2) % 4]
                             for word in COMPASS})

#: Each step's word: :data:`COMPASS`, inverted.
_WORD_OF = {step: word for word, step in COMPASS.items()}


def _whole(value, what: str) -> int:
    """``value`` if it is an int (a bool is not), else an NBTError."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise NBTError(f"{what} must be an int, got {value!r}")
    return value


def _compass_word(word, what: str = "a compass word") -> str:
    if not isinstance(word, str) or word not in COMPASS:
        raise NBTError(f"{what} must be one of {tuple(COMPASS)}, got {word!r}")
    return word


def turn(word: str, quarters: int = 1) -> str:
    """``word`` turned ``quarters`` quarter turns clockwise, seen from above;
    a negative count turns counterclockwise. ``turn("north")`` is
    ``"east"``, ``turn("north", -1)`` is ``"west"``.

    Raises :class:`NBTError` for anything but the four words, or a count
    that is not an int.
    """
    _compass_word(word)
    _whole(quarters, "quarters")
    return CLOCKWISE[(CLOCKWISE.index(word) + quarters) % 4]


def axis_of(word: str) -> str:
    """The axis ``word`` runs along: ``"z"`` for north and south, ``"x"``
    for east and west. Raises :class:`NBTError` for any other word."""
    return "x" if COMPASS[_compass_word(word)][0] else "z"


def heading_of(dx: int, dz: int, *, ties: str = "z") -> str:
    """The compass word nearest the horizontal step ``(dx, dz)``: the one
    along whichever axis has the larger component.

    An exact diagonal has two nearest words, and ``ties`` names the axis it
    goes to -- a choice, not a rule, and one that matters wherever a run
    steps (1, 1) at every cell:

    * ``"z"`` (the default) gives north or south, as the castle and the
      Roman city break it (``abs(dx) > abs(dz)`` for east or west);
    * ``"x"`` gives east or west, as the secret lab did
      (``abs(dx) >= abs(dz)``).

    Raises :class:`NBTError` for ``(0, 0)``, which points nowhere -- the
    castle's copy quietly answered ``"north"`` -- for a component that is
    not an int, and for any ``ties`` but ``"x"`` and ``"z"``.
    """
    _whole(dx, "dx")
    _whole(dz, "dz")
    if ties not in ("x", "z"):
        raise NBTError(f"ties must be 'x' or 'z', got {ties!r}")
    if dx == 0 and dz == 0:
        raise NBTError("(0, 0) is no step, so it has no heading")
    if abs(dx) > abs(dz) or (abs(dx) == abs(dz) and ties == "x"):
        return "east" if dx > 0 else "west"
    return "south" if dz > 0 else "north"


# --------------------------------------------------------------------------
# Round shapes
# --------------------------------------------------------------------------

#: How far past its radius a round shape reaches, in quarter blocks: a
#: cell is in when its center lies within ``r + slack/4`` of the shape's
#: center. ``"inside"`` is the park's rule, ``"quarter"`` the secret lab's
#: and ``"half"`` (the default) the castle's and the Roman city's.
ROUNDINGS = MappingProxyType({"inside": 0, "quarter": 1, "half": 2})


def _slack(rounding) -> int:
    if not isinstance(rounding, str) or rounding not in ROUNDINGS:
        raise NBTError(f"rounding must be one of {tuple(ROUNDINGS)}, "
                       f"got {rounding!r}")
    return ROUNDINGS[rounding]


def _radius(r) -> int:
    _whole(r, "a radius")
    if r < 0:
        raise NBTError(f"a radius must not be negative, got {r}")
    return r


def _pair(value, what: str) -> tuple[int, int]:
    """``value`` as ``(int, int)``, or an NBTError naming ``what``."""
    try:
        first, second = value
    except (TypeError, ValueError):
        raise NBTError(f"{what} must be a pair of ints, got {value!r}") from None
    if (isinstance(value, str) or isinstance(first, bool)
            or isinstance(second, bool) or not isinstance(first, int)
            or not isinstance(second, int)):
        raise NBTError(f"{what} must be a pair of ints, got {value!r}")
    return first, second


def _within(dx: int, dz: int, r: int, slack: int) -> bool:
    """Whether a cell ``(dx, dz)`` from the center is inside radius ``r``
    plus ``slack`` quarter blocks: ``16·(dx² + dz²) <= (4r + slack)²``,
    exactly, in integers."""
    return 16 * (dx * dx + dz * dz) <= (4 * r + slack) ** 2


def disc(r: int, *, center=(0, 0), rounding: str = "half") -> tuple[tuple[int, int], ...]:
    """Every cell of a filled circle of radius ``r`` about ``center``, as
    ``(x, z)``, sorted by x and then z.

    A cell is in when its center lies within ``r`` plus the rounding's
    slack of ``center`` (:data:`ROUNDINGS`), boundary included, tested in
    exact integers as ``16·(dx² + dz²) <= (4r + slack)²``. That is
    ``dx² + dz² <= r²`` for ``"inside"``, ``<= r² + r/2`` for
    ``"quarter"`` and ``<= r² + r`` for ``"half"``. For r = 1..4 a disc
    holds 5, 13, 29 and 49 cells inside, 5, 21, 37 and 61 at a quarter,
    and 9, 21, 37 and 69 at a half. ``disc(0)`` is the center alone.

    Raises :class:`NBTError` for a radius that is not a non-negative int
    (a bool is not one), a center that is not a pair of ints, or an
    unknown rounding.
    """
    r = _radius(r)
    cx, cz = _pair(center, "center")
    slack = _slack(rounding)
    return tuple((cx + dx, cz + dz)
                 for dx in range(-r, r + 1)
                 for dz in range(-r, r + 1)
                 if _within(dx, dz, r, slack))


def _bearing(dx: int, dz: int) -> tuple:
    """A sort key that orders steps from the center as ``atan2(dz, dx)``
    ascends, computed exactly: the northern half from just north of west
    round to just north of east, due east, the southern half, then due
    west last. Inside each half the key is a ratio of the two components
    that grows with the angle."""
    if dz < 0:
        return (0, Fraction(dx, -dz))
    if dz > 0:
        return (2, Fraction(-dx, dz))
    return (1, 0) if dx >= 0 else (3, 0)


def ring(r: int, *, center=(0, 0), rounding: str = "half") -> tuple[tuple[int, int], ...]:
    """The one-block rim of ``disc(r)``: its cells not in ``disc(r - 1)``,
    in order around the center.

    The order is by angle about the center, as ``atan2(dz, dx)`` ascends:
    it starts just north of due west, runs clockwise seen from above
    (north, east, south) and ends due west, so taking every other cell
    marches merlons around a tower rather than across it. It is computed
    exactly, with no floats, and cells on one ray keep the disc's order.
    ``ring(0)`` is the center alone.

    For every rounding a ring and the disc inside it make the whole disc
    without overlapping, the ring holds every disc cell with a side
    neighbor outside it -- so it encloses -- and every ring cell touches at
    least two others. Refusals are :func:`disc`'s.
    """
    cells = disc(r, center=center, rounding=rounding)
    if r == 0:
        return cells
    cx, cz = center
    slack = ROUNDINGS[rounding]
    rim = [(x, z) for x, z in cells if not _within(x - cx, z - cz, r - 1, slack)]
    rim.sort(key=lambda cell: _bearing(cell[0] - cx, cell[1] - cz))
    return tuple(rim)


# --------------------------------------------------------------------------
# Lines
# --------------------------------------------------------------------------

def line(points) -> tuple[tuple[int, int], ...]:
    """The cells a polyline through ``(x, z)`` waypoints crosses, in order.

    Road's rule, exactly: each segment is walked in ``n = max(|dx|, |dz|)``
    steps, step ``i`` landing on ``(ax + round(dx·i/n), az + round(dz·i/n))``
    -- Python's ``round``, nearest with ties to even -- so consecutive cells
    always touch, sideways or corner to corner. Each segment starts on the
    last one's end, which is kept once, and a segment of no length is
    skipped. The float expression is the one road has always used, and for
    any segment shorter than 2**26 blocks -- every segment inside the
    world border -- it is exact half-even rounding of the fraction: the
    float is off by less than the distance from a fraction with that
    denominator to the nearest half.

    Ties to even make a line differ from its reverse:
    ``line([(0, 0), (2, 1)])`` is ``((0, 0), (1, 0), (2, 1))``, and
    ``line([(2, 1), (0, 0)])`` is ``((2, 1), (1, 1), (0, 0))``.

    Raises :class:`NBTError` for a waypoint that is not a pair of ints, and
    when no two consecutive waypoints differ -- there is no line to walk.
    """
    try:
        waypoints = [_pair(point, "a waypoint") for point in points]
    except TypeError:
        raise NBTError(f"a line takes a sequence of (x, z) waypoints, "
                       f"got {points!r}") from None
    cells: list[tuple[int, int]] = []
    for (ax, az), (bx, bz) in zip(waypoints, waypoints[1:]):
        steps = max(abs(bx - ax), abs(bz - az))
        if steps == 0:
            continue
        for i in range(steps + 1):
            x = ax + round((bx - ax) * i / steps)
            z = az + round((bz - az) * i / steps)
            if not cells or cells[-1] != (x, z):
                cells.append((x, z))
    if not cells:
        raise NBTError(f"a line needs two consecutive waypoints that differ, "
                       f"got {waypoints!r}")
    return tuple(cells)


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------

#: Road's window: each height along a road the mean of the ground three
#: cells either side of it. The first town's road chose it (e107f54,
#: 2026-08-14) and every road since has kept it; a choice, not a rule of
#: the game.
ROAD_WINDOW = 3

#: The JeffWorld Roman wall's window, five cells either side
#: (``tools/roman_city/jw_groundwork.py``'s ``smoothed_line``, 4ed2fcf,
#: 2026-08-17): a wall rides the ground more loosely than a road. A choice
#: too.
WALL_WINDOW = 5


def _height(value, what: str) -> int:
    """``value`` if it is an int (a bool is not), else an NBTError naming
    ``what``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise NBTError(f"{what} must be an int, got {value!r}")
    return value


def smoothed_profile(heights, *, window: int, max_step: int | None = 1,
                     anchors=None, closed: bool = False) -> tuple[int, ...]:
    """``heights`` -- the ground along a path, one int per cell, in order --
    smoothed into a profile that never steps more than ``max_step`` blocks
    from a cell to the next, as a tuple of ints: the rule
    :meth:`~heaton_voxel.craft.build.Builder.road` lays its surface by
    (``window=ROAD_WINDOW``), and the one the Roman wall's and the secret
    lab's wall's scripts each wrote again.

    **The mean.** Each height becomes the mean of the heights within
    ``window`` cells of it, ``round(sum / count)`` -- Python's ``round``,
    half to even, the float expression road has always used, which is
    exact for any height the world holds. An open profile's window is
    clipped at its ends. A closed one (``closed=True``: a loop, whose last
    cell lies beside its first) wraps round, so every mean is over the
    ``2·window + 1`` cells within ``window`` of it along the loop, or over
    the whole loop when it is no longer than that: a cell is counted once
    however wide the window. ``window=0`` is each height as it is.

    **Anchors** (``{index: y}``) pin those cells to those heights: set over
    the means, and set again once the clamp is done.

    **The clamp** lowers every cell to the lower envelope
    ``min over j of (p[j] + max_step·|i - j|)``, the distance taken round
    the loop when closed: the highest profile at or under the pinned means
    that steps at most ``max_step`` anywhere. One sweep each way computes
    it exactly, and twice round each way when closed. Road ran its
    forward-and-back clamp three times, the Roman wall four and the lab's
    wall three, and the passes after the first never moved a cell: the
    envelope is the sweeps' fixed point, and pinning the anchors again
    between passes only raises an anchor back to where it was, which
    lowers nothing. ``max_step=None`` leaves the means unclamped, and 0
    lowers the whole profile to its lowest pinned mean, a low anchor's
    included -- the envelope does not stop at an anchor -- leaving each
    anchor above that standing alone.

    **It only lowers.** No cell ends above its mean, or above its anchor
    if it has one. So a low anchor pulls its neighbors down to within
    ``max_step`` of it, while a high one stays a step above them -- they
    are never raised to meet it, as road has always left a step where it
    meets a terrace above the ground. Every other step is at most
    ``max_step``, between the last cell and the first too when closed.

    An empty ``heights`` gives ``()``. Raises :class:`NBTError`, naming the
    value, for heights that are not a sequence of ints (a bool is not
    one), a ``window`` that is not a non-negative int, a ``max_step`` that
    is neither ``None`` nor a non-negative int, ``anchors`` that are not a
    mapping, an anchor whose index is not an int inside the profile or
    whose height is not an int, and a ``closed`` that is not a bool.
    """
    try:
        ground = list(heights)
    except TypeError:
        raise NBTError(f"a profile is a sequence of int heights, "
                       f"got {heights!r}") from None
    for i, y in enumerate(ground):
        _height(y, f"height {i} of the profile")
    if isinstance(window, bool) or not isinstance(window, int) or window < 0:
        raise NBTError(f"a window is a non-negative int, the cells either side "
                       f"of each height, got {window!r}")
    if max_step is not None and (isinstance(max_step, bool)
                                 or not isinstance(max_step, int)
                                 or max_step < 0):
        raise NBTError(f"max_step is None or a non-negative int, "
                       f"got {max_step!r}")
    if not isinstance(closed, bool):
        raise NBTError(f"closed must be True or False, got {closed!r}")
    n = len(ground)
    pins: dict[int, int] = {}
    if anchors is not None:
        try:
            items = list(anchors.items())
        except AttributeError:
            raise NBTError(f"anchors are a mapping of index to height, "
                           f"got {anchors!r}") from None
        for index, y in items:
            if (isinstance(index, bool) or not isinstance(index, int)
                    or not 0 <= index < n):
                raise NBTError(f"an anchor's index must be an int from 0 to "
                               f"{n - 1}, got {index!r}" if n else
                               f"an empty profile has no cell to anchor, "
                               f"got index {index!r}")
            pins[index] = _height(y, f"anchor {index}'s height")
    if not n:
        return ()

    prefix = [0]
    for y in ground:
        prefix.append(prefix[-1] + y)
    if closed and 2 * window + 1 >= n:
        profile = [round(prefix[n] / n)] * n
    elif closed:
        def run(k: int) -> int:
            """The sum of the first ``k`` heights of the loop repeated
            both ways (a negative ``k`` counting back from 0)."""
            return (k // n) * prefix[n] + prefix[k % n]
        count = 2 * window + 1
        profile = [round((run(i + window + 1) - run(i - window)) / count)
                   for i in range(n)]
    else:
        profile = []
        for i in range(n):
            lo, hi = max(0, i - window), min(n, i + window + 1)
            profile.append(round((prefix[hi] - prefix[lo]) / (hi - lo)))

    for index, y in pins.items():
        profile[index] = y
    if max_step is not None:
        rounds = 2 if closed else 1
        for _ in range(rounds):
            for i in range(0 if closed else 1, n):
                profile[i] = min(profile[i], profile[i - 1] + max_step)
        for _ in range(rounds):
            for i in range(n - 1 if closed else n - 2, -1, -1):
                profile[i] = min(profile[i], profile[(i + 1) % n] + max_step)
        for index, y in pins.items():
            profile[index] = y
    return tuple(profile)


# --------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------

#: Clockwise quarter turns from south, the identity, for each facing.
_QUARTERS = {"south": 0, "west": 1, "north": 2, "east": 3}
_FACING_BY_QUARTERS = ("south", "west", "north", "east")

#: What a frame remembers of a state its turn leaves alone: the caller's
#: own state goes back, never an equal one met earlier, since a state read
#: out of a chunk carries the very palette entry it was decoded from.
_UNCHANGED = object()


def _remap(state: BlockState, *, quarters: int = 0, mirror: bool = False) -> BlockState:
    """One stage of a schematic's flip-then-rotate: ``state`` mirrored along
    ``"x"``, or turned ``quarters`` times, exactly as
    :meth:`~heaton_voxel.schem.Schematic.flipped` and
    :meth:`~heaton_voxel.schem.Schematic.rotated` each turn a palette entry
    -- the same state back when its properties come out equal, so an
    untouched state keeps its palette bytes, and otherwise the properties
    in the order the vocabulary leaves them."""
    properties = state.properties
    turned = properties_mirrored(properties, "x") if mirror else properties
    for _ in range(quarters):
        turned = properties_rotated_cw(turned)
    return state if turned == properties else BlockState(state.name, turned)


def _turned(x: int, z: int, quarters: int) -> tuple[int, int]:
    """``(x, z)`` turned ``quarters`` clockwise quarter turns about the
    origin, seen from above: one turn takes east ``(1, 0)`` to south
    ``(0, 1)``, ``R(x, z) = (-z, x)``."""
    quarters %= 4
    if quarters == 0:
        return x, z
    if quarters == 1:
        return -z, x
    if quarters == 2:
        return -x, -z
    return z, -x


class Frame:
    """A component's own coordinates, placed in the world facing some way.

    A frame maps a local ``(across, up, out)`` to a world ``(x, y, z)``,
    exactly, with no rounding. The identity is ``facing="south"``: across
    is +x, up is +y and out is +z, so a schematic's own ``(x, y, z)`` are
    already local coordinates. ``facing`` is the world direction ``out``
    points, and seen from outside the face +across runs to the viewer's
    right; four walls framed facing outward, each with its origin at its
    left end, are walked counterclockwise seen from above. ``mirrored=True``
    swaps the hands, negating ``across`` before the turn: a mirror swaps
    east and west in the component's own terms, and a door's hinge, a
    stair corner and a chest half change sides with it.

    Block states turn with the cells (:meth:`state`), through the same
    vocabulary a schematic turns by:
    :func:`~heaton_voxel.schem.transforms.properties_mirrored` along
    ``"x"`` when mirrored, then
    :func:`~heaton_voxel.schem.transforms.properties_rotated_cw` once per
    quarter turn, each stage handing its state back unchanged when the
    properties come out equal, as a schematic's flip and rotation each do.
    So a frame and the lower tier's flip-then-rotate paste put the same
    blocks in the same places, property order included -- which is palette
    bytes in a chunk older than 26.3. A state the vocabulary cannot
    turn -- a property outside it holding a direction word,
    :func:`~heaton_voxel.schem.transforms.directional_leftovers` -- is
    refused in any frame that turns, never guessed at.

    Immutable and hashable; two frames are equal when their origin, facing
    and hand are, and a copy or a pickle of one is an equal frame. Raises
    :class:`NBTError` for an origin that is not three ints, a facing that
    is not a compass word, or a ``mirrored`` that is not a bool -- and every
    cell method for a coordinate that is not an int, since a fractional
    "cell" is no cell at all.
    """

    __slots__ = ("_origin", "_facing", "_mirrored", "_quarters", "_turns")

    def __init__(self, origin, facing: str = "south", *, mirrored: bool = False):
        try:
            x, y, z = origin
        except (TypeError, ValueError):
            raise NBTError(f"a frame's origin must be (x, y, z), got {origin!r}") from None
        for value in (x, y, z):
            if isinstance(value, bool) or not isinstance(value, int):
                raise NBTError(f"a frame's origin must be three ints, got {origin!r}")
        _compass_word(facing, "a frame's facing")
        if not isinstance(mirrored, bool):
            raise NBTError(f"mirrored must be True or False, got {mirrored!r}")
        set_ = object.__setattr__
        set_(self, "_origin", (x, y, z))
        set_(self, "_facing", facing)
        set_(self, "_mirrored", mirrored)
        set_(self, "_quarters", _QUARTERS[facing])
        # Turned states, memoized per direction and keyed by name and
        # properties *in order*, so a result never depends on which of two
        # equal states was turned first.
        set_(self, "_turns", ({}, {}))

    def __setattr__(self, name, value):
        raise AttributeError("a Frame is immutable")

    def __delattr__(self, name):
        raise AttributeError("a Frame is immutable")

    # A value copies as itself, and pickles through its constructor: the
    # default slot-by-slot rebuild would go through __setattr__, and the
    # memo of turned states is a cache, not part of the value.
    def __copy__(self) -> "Frame":
        return self

    def __deepcopy__(self, memo) -> "Frame":
        return self

    def __reduce__(self):
        return _frame, (self._origin, self._facing, self._mirrored)

    # -- what it is ----------------------------------------------------------

    @property
    def origin(self) -> tuple[int, int, int]:
        """The world cell local ``(0, 0, 0)`` lands on."""
        return self._origin

    @property
    def facing(self) -> str:
        """The world direction local ``out`` points."""
        return self._facing

    @property
    def mirrored(self) -> bool:
        """Whether ``across`` is negated before the turn."""
        return self._mirrored

    @property
    def quarters(self) -> int:
        """Clockwise quarter turns from the identity: south 0, west 1,
        north 2, east 3."""
        return self._quarters

    def _identity(self) -> bool:
        return self._quarters == 0 and not self._mirrored

    def __eq__(self, other) -> bool:
        if not isinstance(other, Frame):
            return NotImplemented
        return (self._origin, self._facing, self._mirrored) == (
            other._origin, other._facing, other._mirrored)

    def __hash__(self) -> int:
        return hash((Frame, self._origin, self._facing, self._mirrored))

    def __repr__(self) -> str:
        hand = ", mirrored=True" if self._mirrored else ""
        return f"Frame({self._origin!r}, {self._facing!r}{hand})"

    # -- cells ---------------------------------------------------------------

    def step(self, across: int, out: int) -> tuple[int, int]:
        """A horizontal local offset as a world ``(dx, dz)``. Raises
        :class:`NBTError` for a component that is not an int."""
        _whole(across, "across")
        _whole(out, "out")
        return _turned(-across if self._mirrored else across, out, self._quarters)

    def cell(self, across: int, up: int, out: int) -> tuple[int, int, int]:
        """The world cell of a local ``(across, up, out)``. Raises
        :class:`NBTError` for a component that is not an int."""
        _whole(up, "up")
        dx, dz = self.step(across, out)
        ox, oy, oz = self._origin
        return ox + dx, oy + up, oz + dz

    def local(self, x: int, y: int, z: int) -> tuple[int, int, int]:
        """The local ``(across, up, out)`` of a world cell: :meth:`cell`,
        inverted. Raises :class:`NBTError` for a coordinate that is not an
        int."""
        for value, what in ((x, "x"), (y, "y"), (z, "z")):
            _whole(value, what)
        ox, oy, oz = self._origin
        across, out = _turned(x - ox, z - oz, -self._quarters)
        return (-across if self._mirrored else across), y - oy, out

    def heading(self, word: str) -> str:
        """A direction in the frame's own terms -- south is +out -- as a
        world direction. ``"up"`` and ``"down"`` are unchanged. Raises
        :class:`NBTError` for any other word."""
        if word in ("up", "down"):
            return word
        return _WORD_OF[self.step(*COMPASS[_compass_word(word, "a heading")])]

    def box(self, corner1, corner2) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        """The world box two local corners span, as ``(low, high)``
        corners. Raises :class:`NBTError` for a corner that is not three
        ints."""
        corners = []
        for corner in (corner1, corner2):
            try:
                across, up, out = corner
            except (TypeError, ValueError):
                raise NBTError(f"a box corner must be (across, up, out), "
                               f"got {corner!r}") from None
            corners.append(self.cell(across, up, out))
        a, b = corners
        return (tuple(map(min, a, b)), tuple(map(max, a, b)))

    # -- states --------------------------------------------------------------

    def state(self, block) -> BlockState:
        """``block`` -- a name, a :class:`~heaton_voxel.blocks.BlockState`
        or a ``(name, properties)`` pair -- as it stands once the frame has
        turned it: mirrored along ``"x"`` if the frame is, then turned
        clockwise once per quarter.

        Only the properties ``block`` carries are turned, as a schematic's
        turn turns a palette entry: one it leaves unsaid stays at its
        default, so a bare ``minecraft:oak_stairs`` faces north in every
        frame. A Builder seen through a frame
        (:meth:`~heaton_voxel.craft.build.Builder.framed`) turns the
        defaults as well.

        The same :class:`BlockState` comes back when nothing changes, so an
        untouched state keeps its place in a section palette. In a frame
        that turns, a state carrying a direction-looking property the
        vocabulary does not cover raises
        :class:`~heaton_voxel.craft.errors.Refused`. A malformed name
        raises the parser's ``ValueError``, as a Builder write does.
        """
        return self._turn(as_state(block), inverse=False)

    def unstate(self, block) -> BlockState:
        """:meth:`state`, inverted: a world state in the frame's own terms.
        ``frame.unstate(frame.state(s)) == s`` for every state the game
        has, in every frame."""
        return self._turn(as_state(block), inverse=True)

    def _turn(self, state: BlockState, *, inverse: bool) -> BlockState:
        if self._identity():
            return state
        properties = state.properties
        key = (state.name, tuple(properties.items()))
        memo = self._turns[inverse]
        found = memo.get(key)
        if found is not None:
            return state if found is _UNCHANGED else found
        leftovers = directional_leftovers([state])
        if leftovers:
            raise Refused(
                f"{state} cannot be turned into {self!r}: {leftovers[0]}",
                faults=leftovers)
        result = state
        if inverse:
            result = _remap(result, quarters=(4 - self._quarters) % 4)
            result = _remap(result, mirror=self._mirrored)
        else:
            result = _remap(result, mirror=self._mirrored)
            result = _remap(result, quarters=self._quarters)
        memo[key] = _UNCHANGED if result is state else result
        return result

    # -- composing -----------------------------------------------------------

    def within(self, outer: "Frame") -> "Frame":
        """This frame, placed inside ``outer``: the one frame that maps a
        local cell as this one does and then ``outer`` does with the
        result. Its origin is ``outer.cell(*self.origin)``; it turns by
        ``outer``'s quarters plus this frame's (less them, if ``outer`` is
        mirrored, since a mirror reverses a turn), and is mirrored when
        exactly one of the two is."""
        if not isinstance(outer, Frame):
            raise NBTError(f"a frame goes within a Frame, not {outer!r}")
        quarters = (outer._quarters
                    + (-self._quarters if outer._mirrored else self._quarters)) % 4
        return Frame(outer.cell(*self._origin), _FACING_BY_QUARTERS[quarters],
                     mirrored=outer._mirrored != self._mirrored)


def _frame(origin, facing, mirrored) -> Frame:
    """A frame rebuilt through its constructor, which is how one unpickles."""
    return Frame(origin, facing, mirrored=mirrored)
