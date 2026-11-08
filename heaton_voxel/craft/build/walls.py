"""Walls: the run, the rule its face follows, the holes cut in it, and what
crowns it.

:class:`Wall` is where a wall stands -- a straight run whose *along*
coordinate is the world's own x or z, its rows, its stories and its
thickness, and :meth:`Wall.ring`, the four that close a rectangle.
:class:`Facade` is the rule its face follows -- corners, piers and bays
across, plinth, bands and a head course up, a window in every bay of every
story -- and :class:`Opening` a hole of one shape: a rectangle, an arch
that springs from its jambs, or a round one. :meth:`WallsMixin.wall` lays
one run, :meth:`WallsMixin.shell` a whole building's -- walls, floors, a
deck and its parapet, or a :class:`~heaton_voxel.craft.build.roof.Roof`
-- and :meth:`WallsMixin.cut_opening` cuts a hole in one already standing.
:func:`merlon_offsets`, the castle's rule for where the teeth of a
parapet go, and :meth:`WallsMixin.battlement`, which lays a parapet course
and its merlons along a straight-run path, or around a round tower's ring,
crown it.

**Bays count along the wall's own run, from the world.** A facade's
rhythm is a congruence on the run's own coordinate, ``(along - phase) %
pitch``, :meth:`~heaton_voxel.craft.build.Builder.arcade`'s rule: x along
a wall that runs east and west, z along one that runs north and south. A
test written per cell with the wrong one reads a coordinate the run holds
constant, and every column of the wall comes out the same -- which is how
the secret lab's stables (``tools/secret_lab/m2_buildings.py``) tested
``x % 4`` on the walls of constant x and ``z % 4`` on those of constant z:
the north and south walls, at z = 924 and 936, both multiples of four,
came out all posts, and the east and west walls, at x = 741 and 757,
none. Because ``along`` *is* the
world coordinate, two walls given one integer ``phase`` agree on every
pier wherever they stand -- the lab put every one of its buildings on
``phase=0`` -- while ``phase=None`` counts each wall from its own low end,
which :meth:`Wall.ring` makes the same corner for all four.

**An arch springs from its jambs.** The cells at the foot of an arch's
head are top stairs whose full side -- the way a stair faces -- is toward
the jamb beside them, so the head steps down onto the wall that carries
it, as the castle example's windows and its entrance do
(``examples/castle.py``); faced toward the middle, as the Roman
``arch_portal`` faced them, the head is notched where it should be
carried. :meth:`Opening.arch` takes the profile its head follows,
:func:`~heaton_voxel.craft.build.vault.arch_profile` or
:func:`~heaton_voxel.craft.build.vault.pointed_arch_profile` -- a vault's
section turned upside down, in the same half blocks -- or the castle's
flat head when it is given none.

**A battlement is counted per run, from that run's own corners.** The
crowns the builds wrote by hand walked the whole perimeter as one list --
four sides concatenated -- and put a merlon on every other cell of it,
``i % 2``. That alternates cleanly only while every side has an odd
length; a side of even length shifts the count for every side after it,
and the corners land wherever the shift leaves them. Seven of JeffWorld's
Roman field towers wear that crown (``tools/roman_city/jw_groundwork.py``),
with two bare corners, one doubled merlon and one doubled gap each, and so
do the castle example's gatehouse turrets. Here each straight run takes
its teeth from :func:`merlon_offsets`, counted from its first corner, and
a run's two ends are always merlons, so a corner is one by construction:
a run of even length doubles up beside its far corner, which is the
castle's documented choice. A round tower has no corners, so a ring
alternates in its own order and an odd count -- one place where two teeth
or two gaps would meet -- is refused.

Part of :mod:`heaton_voxel.craft.build`. Each writer plans its call whole,
has the plan audited for what the game would not keep
(``Builder._audit_plan``), and hands it to the Builder's ``_commit``.
"""

from __future__ import annotations

import copy

from ...blocks import BlockState
from ...nbt import NBTError
from ..block_registry import properties_of
from ..errors import Refused
from ..geometry import _pair, axis_of, COMPASS, disc, OPPOSITE
from ..style import FROM_PALETTE, Material, Mix
from ._common import (_empty, _ENDS, _occupancy, _same_crown,
                      _STAIR_STATE, _state, _whole, AIR)
from .roof import Roof
from .vault import _fill, _SLAB_STATE


#: What a gap between merlons is cut to.
_AIR = BlockState(AIR)


def _hung(state: BlockState) -> bool:
    """Whether ``state`` is a door or a gate -- by whole suffix, ``_door``
    or ``_fence_gate``, so a trapdoor is neither -- which is what hangs in
    a hole and is opened to pass it: the one thing an open cell of an
    opening may already hold and still be the hole. Anything else a body
    walks through -- water, a bubble column, powder snow, a cobweb,
    scaffolding, a torch -- is something standing in it."""
    return state.name.endswith(("_door", "_fence_gate"))


def merlon_offsets(length: int) -> list[int]:
    """Offsets along a straight parapet of ``length`` cells that carry a
    merlon, the raised tooth: every other cell from the first, and the
    last cell always.

    Both ends are always merlons -- a battlement that stops on a gap looks
    bitten off -- so an even length doubles up next to the far end rather
    than leaving the corner bare: 7 gives ``[0, 2, 4, 6]`` and 8 gives
    ``[0, 2, 4, 6, 7]``. ``examples/castle.py``'s rule, verbatim; the
    example keeps its own copy so it stays free of the judged tier, and
    ``tests/test_walls.py`` holds the two equal. No length, or a negative
    one, has no teeth.
    """
    if length <= 0:
        return []
    teeth = list(range(0, length, 2))
    if teeth[-1] != length - 1:
        teeth.append(length - 1)
    return teeth


def _step(a, b) -> tuple[int, int]:
    """The unit step from vertex ``a`` toward vertex ``b``, refusing a
    diagonal: a battlement turns its corners at vertices."""
    dx, dz = b[0] - a[0], b[1] - a[1]
    if dx and dz:
        raise NBTError(
            f"a battlement runs straight along x or along z, and {a} to {b} "
            f"is diagonal: turn the corner with a vertex between them, or "
            f"crown a round tower with ring=")
    return (dx > 0) - (dx < 0), (dz > 0) - (dz < 0)


def _path_roles(path) -> list[tuple[int, int, bool]]:
    """``(x, z, merlon)`` for every cell of a battlement path, in path
    order, each cell once.

    The path is ``(x, z)`` vertices, closed when the last equals the
    first. Consecutive repeats are dropped; every other pair of
    consecutive vertices must share an x or a z, and the path may not turn
    back on itself. A vertex where the direction changes is a corner, and
    so are an open path's two ends; a vertex the path runs straight
    through is not, so a run keeps its rhythm through it. A closed path
    is walked from its first corner. Each run, corner to corner inclusive,
    takes its teeth at :func:`merlon_offsets` of its length, counted from
    its first corner in path order, so a corner shared by two runs is a
    merlon on both. A cell met a second time in the other role is a
    crossing, and refused; met again in the same role it is kept where it
    was first met.
    """
    if path is None or isinstance(path, (str, bytes)):
        raise NBTError(f"a battlement path is a sequence of (x, z) "
                       f"vertices, not {path!r}")
    try:
        points = list(path)
    except TypeError:
        raise NBTError(f"a battlement path is a sequence of (x, z) "
                       f"vertices, not {path!r}") from None
    vertices: list[tuple[int, int]] = []
    for point in points:
        vertex = _pair(point, "a battlement vertex")
        if not vertices or vertices[-1] != vertex:
            vertices.append(vertex)
    if len(vertices) < 2:
        raise NBTError(f"a battlement path needs two vertices that differ, "
                       f"got {points!r}")
    closed = len(vertices) > 2 and vertices[0] == vertices[-1]
    if closed:
        vertices.pop()
    count = len(vertices)
    if closed:
        steps = [_step(vertices[i], vertices[(i + 1) % count])
                 for i in range(count)]
        turns = range(count)
    else:
        steps = [_step(vertices[i], vertices[i + 1]) for i in range(count - 1)]
        turns = range(1, count - 1)
    corners = [] if closed else [0]
    for i in turns:
        before, after = steps[i - 1], steps[i]
        if before == (-after[0], -after[1]):
            raise NBTError(
                f"the battlement path turns back on itself at "
                f"{vertices[i]}: a parapet runs one way along a wall")
        if before != after:
            corners.append(i)
    if not closed:
        corners.append(count - 1)
    elif not corners:
        raise NBTError(f"the closed battlement path {points!r} has no "
                       f"corner, so it is no perimeter")
    if closed:
        corners.append(corners[0])           # round to the first corner again

    roles: dict[tuple[int, int], bool] = {}
    for start, end in zip(corners, corners[1:]):
        (ax, az), (bx, bz) = vertices[start], vertices[end]
        dx, dz = _step((ax, az), (bx, bz))
        length = max(abs(bx - ax), abs(bz - az)) + 1
        teeth = set(merlon_offsets(length))
        for k in range(length):
            cell = (ax + k * dx, az + k * dz)
            role = k in teeth
            seen = roles.get(cell)
            if seen is None:
                roles[cell] = role
            elif seen != role:
                raise NBTError(
                    f"the battlement path crosses itself at {cell}, a "
                    f"{'merlon' if seen else 'gap'} the first time round "
                    f"and a {'merlon' if role else 'gap'} the second")
    return [(x, z, role) for (x, z), role in roles.items()]


def _ring_roles(ring) -> list[tuple[int, int, bool]]:
    """``(x, z, merlon)`` for every cell of a round tower's ring, in ring
    order: a merlon on the first cell and on every other one after it, as
    the castle crowns its towers.

    The cells are walked in the order given, which must be the order round
    the tower -- :func:`~heaton_voxel.craft.geometry.ring`'s, by angle --
    so each one touches the next, side or corner, and the last touches the
    first. Alternating then marches the teeth round the rim rather than
    across it. An odd count cannot alternate all the way round -- where it
    closes, two teeth or two gaps would stand side by side -- and is
    refused, as is a ring given out of order or with a cell twice. Every
    ``geometry.ring`` of a radius above zero is even, being symmetric four
    ways with nothing at its center.
    """
    if ring is None or isinstance(ring, (str, bytes)):
        raise NBTError(f"a battlement ring is a sequence of (x, z) cells, "
                       f"not {ring!r}")
    try:
        cells = [_pair(cell, "a ring cell") for cell in ring]
    except TypeError:
        raise NBTError(f"a battlement ring is a sequence of (x, z) cells, "
                       f"not {ring!r}") from None
    if len(cells) < 2:
        raise NBTError(f"a battlement ring needs at least two cells to "
                       f"alternate, got {cells!r}")
    seen: set[tuple[int, int]] = set()
    for cell in cells:
        if cell in seen:
            raise NBTError(f"the ring holds {cell} twice; a ring goes round "
                           f"its tower once")
        seen.add(cell)
    if len(cells) % 2:
        raise NBTError(
            f"a ring of {len(cells)} cells cannot alternate all the way "
            f"round: where it closes, two merlons or two gaps would stand "
            f"side by side. Every geometry.ring above radius 0 is even")
    for i, a in enumerate(cells):
        b = cells[(i + 1) % len(cells)]
        if max(abs(a[0] - b[0]), abs(a[1] - b[1])) != 1:
            raise NBTError(
                f"the ring steps from {a} to {b}, which do not touch: a ring "
                f"is walked in order round its tower, as geometry.ring gives "
                f"it, or every other cell is a merlon across it rather than "
                f"round it")
    return [(x, z, i % 2 == 0) for i, (x, z) in enumerate(cells)]


def _whole_block(given, arg: str, owner: str = "battlement",
                 noun: str = "a battlement", *, none: bool = False):
    """A whole-block material -- a battlement's, a facade's, an opening's
    fill -- as it is planned: a :class:`~heaton_voxel.craft.style.Mix` as
    itself, picked at each world cell; a
    :class:`~heaton_voxel.craft.style.Material` as its block; a name or a
    :class:`~heaton_voxel.blocks.BlockState` as a state. Named, never read
    from a palette, and ``None`` only where ``none`` lets it mean none: a
    ``TypeError`` before anything is planned. Malformed text raises the
    parser's ``ValueError``. ``owner`` and ``noun`` are the rule's own
    words for itself, in the refusal."""
    if given is FROM_PALETTE:
        raise TypeError(
            f"{owner}({arg}=) is named at the call, not read from the "
            f"Builder's palette: pass the block, or b.palette[role] for a "
            f"palette's")
    if given is None:
        if none:
            return None
        raise TypeError(f"{owner}({arg}=None) names no block, and "
                        f"{noun} writes one there")
    if isinstance(given, Mix):
        return given
    if isinstance(given, Material):
        return _state(given.form("block"))
    if not isinstance(given, (str, BlockState)):
        raise TypeError(f"{owner}({arg}=) is a block -- an id, a "
                        f"BlockState, a Material or a Mix -- not {given!r}")
    return _state(given)


# --------------------------------------------------------------------------
# The rule objects: where a wall stands, the rule its face follows, a hole
# --------------------------------------------------------------------------

def _shaped(given, arg: str, owner: str, form: str) -> BlockState:
    """A stair or a slab an opening spells a facing, a half or a type onto:
    a block of that shape, by id or state, or a
    :class:`~heaton_voxel.craft.style.Material`'s ``form``. A Mix or a
    palette's is a ``TypeError``, a block of another shape an
    ``NBTError``."""
    suffix = "_stairs" if form == "stairs" else "_slab"
    if given is FROM_PALETTE:
        raise TypeError(f"{owner}({arg}=) is named at the call, not read "
                        f"from the Builder's palette: pass the block, or "
                        f"b.palette[role] for a palette's")
    if isinstance(given, Mix):
        raise TypeError(
            f"{owner}({arg}=) is one block, which the opening spells a "
            f"{'facing and a half' if form == 'stairs' else 'type'} onto, "
            f"and {given!r} is a Mix, many blocks picked by position: pick "
            f"one with .pick(x, y, z), or name the block")
    if isinstance(given, Material):
        state = _state(given.form(form))
    elif isinstance(given, (str, BlockState)):
        state = _state(given)
    else:
        raise TypeError(f"{owner}({arg}=) is a {form} block -- an id, a "
                        f"BlockState or a Material -- not {given!r}")
    if not state.name.endswith(suffix):
        raise NBTError(f"{owner}({arg}=) is a {form[:-1] if form == 'stairs' else form} "
                       f"block, which the opening sets a "
                       f"{'facing and a half' if form == 'stairs' else 'type'} "
                       f"on, and {state.name} is not one")
    return state


class _Picks:
    """A Mix picked at world cells, each name parsed once: what a planned
    value is when it is written at a cell."""

    __slots__ = ("_parsed",)

    def __init__(self):
        self._parsed: dict = {}

    def __call__(self, value, cell) -> BlockState:
        if isinstance(value, Mix):
            name = value.pick(*cell)
            state = self._parsed.get(name)
            if state is None:
                state = self._parsed[name] = _state(name)
            return state
        return value


class _Recolor:
    """A rule's blocks changed by ``{block: block}``, and which were met.

    A key is a block, by id, a state or a Material, and matches every state
    of that block; a value is a block, a Material or a Mix. A state keeps
    the properties the new block takes, so a stair stays a stair facing the
    way it faced. What is not a key is left as it is. A Mix goes through
    entry by entry, its weights, seed and clump kept. Refused by
    :meth:`check`, an ``NBTError``: a key that met nothing, which is a block
    spelled wrong or a rule that does not hold it, unless the rule composes
    itself for each wall (:class:`~heaton_voxel.craft.build.elevation.Fitted`)
    and so cannot say yet."""

    __slots__ = ("table", "used", "lazy")

    def __init__(self, mapping):
        if not isinstance(mapping, dict) or not mapping:
            raise NBTError(f"recolored takes {{block: block}}, got {mapping!r}")
        self.table = {}
        for key, value in mapping.items():
            old = _whole_block(key, "a key", "recolored", "a recoloring")
            if isinstance(old, Mix):
                raise NBTError("a recoloring's key is one block, and a Mix "
                               "is many: name each")
            self.table[old.name] = _whole_block(value, "a value", "recolored",
                                                "a recoloring")
        self.used: set = set()
        self.lazy = False

    def __call__(self, value):
        """``value``, a block, a Mix or None, as the table leaves it."""
        if value is None:
            return None
        if isinstance(value, Mix):
            entries = [(self(_state(block)), weight)
                       for block, weight in value.entries]
            return Mix(entries, seed=value.seed, clump=value.clump)
        new = self.table.get(value.name)
        if new is None:
            return value
        self.used.add(value.name)
        if isinstance(new, Mix):
            return new
        takes = properties_of(new.name) or {}
        kept = {key: held for key, held in value.properties.items()
                if held in takes.get(key, ())}
        return BlockState(new.name, {**kept, **new.properties})

    def check(self) -> None:
        unused = sorted(set(self.table) - self.used)
        if unused and not self.lazy:
            raise NBTError(f"recolored names {unused}, which nothing in the "
                           f"rule holds")


class Wall:
    """Where a wall stands: a straight run, its rows, its stories and its
    thickness. No world in it, and nothing written.

    ``start`` and ``end`` are ``(x, z)``, in either order, on one axis.
    The run's *along* coordinate is the world's own -- x for a wall running
    east and west, z for one running north and south, never a count from
    the wall's end -- so a :class:`Facade`'s bays lock to the world.
    ``outward`` is the compass word for the outside, across the run, and the
    wall is ``thickness`` layers deep: layer 0 is the run's own line, and
    layer ``k`` lies ``k`` blocks inward of it.

    Rows run from ``floor_y + 1`` up to ``top``, inclusive: ``floor_y`` is
    the floor the wall stands on, as everywhere in craft. ``stories`` are
    the floor blocks of the stories above the ground one, strictly
    increasing and inside the wall, so a row at an upper floor's level is
    that story's, its floor row. Every story has two clear rows at least
    -- a floor laid one block over another is no story -- the last story
    up to ``top``.

    ``ends=True`` makes the run's end columns corners -- the ``thickness``
    columns at each end, so a thick wall's corner is square -- as
    :meth:`ring` makes the two walls that own a rectangle's corners.
    ``origin`` is the coordinate a facade's default phase counts its bays
    from: the run's low end unless it is given.

    Refused (an ``NBTError``): a diagonal run, an ``outward`` that is no
    compass word or points along the run, ``top`` not above ``floor_y``, a
    thickness under 1, stories out of order or outside the wall, and a
    story with fewer than two clear rows. Equal walls are the same wall.
    """

    __slots__ = ("axis", "at", "low", "high", "floor_y", "top", "outward",
                 "thickness", "stories", "ends", "origin")

    def __init__(self, start, end, *, floor_y: int, top: int, outward: str,
                 thickness: int = 1, stories=(), ends: bool = False,
                 origin: int | None = None):
        ax, az = _pair(start, "a wall's start")
        bx, bz = _pair(end, "a wall's end")
        if ax != bx and az != bz:
            raise NBTError(f"a wall runs straight along x or along z, and "
                           f"{(ax, az)} to {(bx, bz)} is diagonal: build it "
                           f"as two walls that meet")
        if not isinstance(outward, str) or outward not in COMPASS:
            raise NBTError(f"outward is one of {tuple(COMPASS)}, the side "
                           f"the outside is on, got {outward!r}")
        axis = "x" if axis_of(outward) == "z" else "z"
        run = "x" if ax != bx else "z" if az != bz else axis
        if run != axis:
            raise NBTError(f"a wall's outside is across it, and {outward} "
                           f"points along the run from {(ax, az)} to "
                           f"{(bx, bz)}: a wall running along {run} faces "
                           f"{' or '.join(w for w in COMPASS if axis_of(w) != run)}")
        floor_y = _whole(floor_y, "floor_y")
        top = _whole(top, "top")
        if top <= floor_y:
            raise NBTError(f"a wall's top row stands above the floor it "
                           f"stands on, got floor_y={floor_y} and top={top}")
        thickness = _whole(thickness, "thickness")
        if thickness < 1:
            raise NBTError(f"a wall is at least one block thick, got "
                           f"thickness={thickness}")
        if isinstance(stories, (str, bytes)) or stories is None:
            raise NBTError(f"stories is a sequence of floor levels, not "
                           f"{stories!r}")
        try:
            levels = tuple(_whole(f, "a story's floor") for f in stories)
        except TypeError:
            raise NBTError(f"stories is a sequence of floor levels, not "
                           f"{stories!r}") from None
        floors = (floor_y, *levels)
        for below, above in zip(floors, floors[1:]):
            if above <= below:
                raise NBTError(f"stories go up, one floor over the last, "
                               f"from floor_y={floor_y}: got {levels!r}")
        if levels and levels[-1] > top:
            raise NBTError(f"the story floored at {levels[-1]} is above the "
                           f"wall's top row, {top}")
        for i, level in enumerate(floors):
            ceiling = floors[i + 1] if i + 1 < len(floors) else top + 1
            if ceiling - level - 1 < 2:
                raise NBTError(
                    f"the story floored at {level} has "
                    f"{max(0, ceiling - level - 1)} clear rows under "
                    f"{'the floor at ' + str(ceiling) if ceiling <= top else 'the top'}; "
                    f"a story is two rows at least, so a body fits in it")
        if not isinstance(ends, bool):
            raise NBTError(f"ends is True or False, got {ends!r}")
        low, high = sorted((ax, bx) if axis == "x" else (az, bz))
        if origin is not None:
            origin = _whole(origin, "origin")
        set_ = object.__setattr__
        set_(self, "axis", axis)
        set_(self, "at", az if axis == "x" else ax)
        set_(self, "low", low)
        set_(self, "high", high)
        set_(self, "floor_y", floor_y)
        set_(self, "top", top)
        set_(self, "outward", outward)
        set_(self, "thickness", thickness)
        set_(self, "stories", levels)
        set_(self, "ends", ends)
        set_(self, "origin", low if origin is None else origin)

    def __setattr__(self, name, value):
        raise AttributeError("a Wall is a value; make another")

    # A value copies as itself, and pickles through its constructor, as a
    # geometry.Frame does: the default slot-by-slot rebuild would go through
    # __setattr__.
    def __copy__(self) -> "Wall":
        return self

    def __deepcopy__(self, memo) -> "Wall":
        return self

    def __reduce__(self):
        ends = ((self.low, self.at), (self.high, self.at)) if self.axis == "x" \
            else ((self.at, self.low), (self.at, self.high))
        return _wall, (*ends, dict(
            floor_y=self.floor_y, top=self.top, outward=self.outward,
            thickness=self.thickness, stories=self.stories, ends=self.ends,
            origin=self.origin))

    @classmethod
    def ring(cls, x0, z0, x1, z1, *, floor_y: int, top: int,
             thickness: int = 1, stories=()) -> dict:
        """The four walls closing a rectangle, ``(x0, z0)`` to ``(x1, z1)``
        inclusive, corners in either order: ``{"north", "east", "south",
        "west": Wall}``, in that order, which is the order a shell writes
        them in.

        The north and south walls own the corners: they span the whole
        width, ``ends=True``. The east and west walls fill between them,
        ``z0 + thickness`` to ``z1 - thickness``. All four count a facade's
        default phase from the same corner -- ``origin=x0`` along x and
        ``z0`` along z -- so their bays agree where they meet. A rectangle
        too small for an inside, less than ``2 * thickness + 1`` along
        either side, is refused (an ``NBTError``)."""
        for value, what in ((x0, "x0"), (z0, "z0"), (x1, "x1"), (z1, "z1")):
            _whole(value, what)
        x0, x1 = sorted((x0, x1))
        z0, z1 = sorted((z0, z1))
        t = _whole(thickness, "thickness")
        if t >= 1 and (x1 - x0 < 2 * t or z1 - z0 < 2 * t):
            raise NBTError(
                f"a ring of walls {t} thick needs at least {2 * t + 1} "
                f"blocks along each side to have an inside; "
                f"{(x0, z0)}..{(x1, z1)} is {x1 - x0 + 1} by {z1 - z0 + 1}")
        common = dict(floor_y=floor_y, top=top, thickness=thickness,
                      stories=stories)
        return {
            "north": cls((x0, z0), (x1, z0), outward="north", ends=True,
                         origin=x0, **common),
            "east": cls((x1, z0 + t), (x1, z1 - t), outward="east",
                        origin=z0, **common),
            "south": cls((x0, z1), (x1, z1), outward="south", ends=True,
                         origin=x0, **common),
            "west": cls((x0, z0 + t), (x0, z1 - t), outward="west",
                        origin=z0, **common),
        }

    @staticmethod
    def perimeter(x0, z0, x1, z1) -> tuple[tuple[int, int], ...]:
        """The closed corner path round a rectangle, clockwise from its
        northwest corner -- ``(x0, z0), (x1, z0), (x1, z1), (x0, z1),
        (x0, z0)`` with the corners sorted -- which is the path
        :meth:`WallsMixin.battlement` takes. :meth:`WallsMixin.shell` lays
        its parapet over exactly the cells of this path, so the battlement
        that crenellates it walks the same perimeter, corner for corner."""
        for value, what in ((x0, "x0"), (z0, "z0"), (x1, "x1"), (z1, "z1")):
            _whole(value, what)
        x0, x1 = sorted((x0, x1))
        z0, z1 = sorted((z0, z1))
        return ((x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0))

    # -- what it is ------------------------------------------------------

    @property
    def along(self) -> range:
        """The run's columns, low end to high, in the world's own x or z."""
        return range(self.low, self.high + 1)

    @property
    def rows(self) -> range:
        """The wall's rows, ``floor_y + 1`` to ``top``."""
        return range(self.floor_y + 1, self.top + 1)

    @property
    def inward(self) -> str:
        """The compass word for the inside: :attr:`outward`'s opposite."""
        return OPPOSITE[self.outward]

    @property
    def floors(self) -> tuple[int, ...]:
        """Every story's floor, the ground one first: ``(floor_y,
        *stories)``."""
        return (self.floor_y, *self.stories)

    def cell(self, along: int, y: int, layer: int = 0) -> tuple[int, int, int]:
        """The world cell at ``along`` on the run, row ``y``, ``layer``
        blocks inward of the run's line."""
        along, y, layer = (_whole(along, "along"), _whole(y, "y"),
                           _whole(layer, "layer"))
        dx, dz = COMPASS[self.inward]
        if self.axis == "x":
            return (along, y, self.at + layer * dz)
        return (self.at + layer * dx, y, along)

    def story(self, y: int) -> int:
        """Which story row ``y`` belongs to: how many upper floors stand at
        or below it, so an upper floor's own row is its story's, the
        ground story is 0, and a row under the ground floor is none of
        them (an ``NBTError``)."""
        y = _whole(y, "y")
        if y <= self.floor_y:
            raise NBTError(f"row {y} is at or under the wall's floor, "
                           f"{self.floor_y}: no story's")
        return sum(1 for level in self.stories if level <= y)

    def floor_of(self, story: int) -> int:
        """The floor block of ``story``, 0 being the ground."""
        story = _whole(story, "story")
        if not 0 <= story < len(self.floors):
            raise NBTError(f"the wall has stories 0..{len(self.floors) - 1}, "
                           f"got story={story}")
        return self.floors[story]

    def door(self, along: int, story: int = 0) -> tuple:
        """``(x, y, z, facing)`` for a door at ``along`` on ``story``'s
        floor, the way :meth:`~heaton_voxel.craft.build.Builder.doorway`
        takes it -- ``b.doorway(*wall.door(a), door=...)`` -- in layer 0,
        walked through inward. A wall two or more thick needs its inner
        layers cut first (an :class:`Opening` there), and an iron door
        finds no seat for a button in one, so doorway refuses it."""
        along = _whole(along, "along")
        if along not in self.along:
            raise NBTError(f"a door at {along} is off the wall, which runs "
                           f"{self.low}..{self.high}")
        x, y, z = self.cell(along, self.floor_of(story) + 1)
        return (x, y, z, self.inward)

    def _end(self, along: int) -> bool:
        """Whether ``along`` is a corner column: within the wall's
        thickness of an end of a run with ``ends``."""
        return self.ends and (along - self.low < self.thickness
                              or self.high - along < self.thickness)

    def _key(self) -> tuple:
        return (self.axis, self.at, self.low, self.high, self.floor_y,
                self.top, self.outward, self.thickness, self.stories,
                self.ends, self.origin)

    def __eq__(self, other) -> bool:
        if not isinstance(other, Wall):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash((Wall, self._key()))

    def __repr__(self) -> str:
        if self.axis == "x":
            run = f"x {self.low}..{self.high} at z={self.at}"
        else:
            run = f"z {self.low}..{self.high} at x={self.at}"
        extra = f", stories {self.stories}" if self.stories else ""
        extra += f", {self.thickness} thick" if self.thickness > 1 else ""
        extra += ", ends" if self.ends else ""
        return (f"<Wall {run} facing {self.outward}, rows "
                f"{self.floor_y + 1}..{self.top}{extra}>")


def _wall(start, end, kwargs) -> Wall:
    """A wall rebuilt through its constructor, which is how one unpickles."""
    return Wall(start, end, **kwargs)


class Footprint:
    """The columns a building stands on, where no one rectangle says them: an
    L, a T, a U, a cross, a block round a courtyard. No world in it.

    ``Footprint((0, 0, 30, 20), less=[(16, 0, 30, 9)])`` is that rectangle with
    its corner taken out, an L. Each argument is an inclusive ``(x0, z0, x1,
    z1)``; several join, and ``less`` takes rectangles out of the result,
    which may leave a hole, a courtyard, with walls of its own.

    Its walls are the runs of its outline, each a
    :class:`Wall`: :meth:`walls` names them by the way they face, the first
    of each ``"south"``, then ``"south-2"`` and so on, in the order
    :meth:`Wall.ring` gives a rectangle's. As there, the north and south walls
    own the corners, an inside corner among them, and the east and west walls
    run between. A rule that fits one wall fits only that length, so a plan
    of many walls is what :class:`~heaton_voxel.craft.build.elevation.Fitted`
    is for.

    A wall here is one block thick. Refused (an ``NBTError``): nothing left,
    columns that do not join, an inside that does not join, and a wing under
    three across, which would be all wall and no room. Equal footprints are
    the same footprint.
    """

    __slots__ = ("columns", "x0", "z0", "x1", "z1", "inner", "rim")

    def __init__(self, *rectangles, less=()):
        if not rectangles:
            raise NBTError("a footprint has at least one rectangle")
        columns: set = set()
        for rectangle in rectangles:
            columns |= self._rectangle(rectangle)
        for rectangle in less:
            columns -= self._rectangle(rectangle)
        if not columns:
            raise NBTError("a footprint with nothing left stands on nothing")
        if not self._joined(columns):
            raise NBTError("a footprint's columns have to join, and these "
                           "come apart: build two")
        rim = {(x, z) for x, z in columns
               if any((x + dx, z + dz) not in columns
                      for dx in (-1, 0, 1) for dz in (-1, 0, 1))}
        inner = columns - rim
        solid = sorted(cell for cell in rim
                       if not any((cell[0] + dx, cell[1] + dz) in inner
                                  for dx in (-1, 0, 1) for dz in (-1, 0, 1)))
        if solid:
            raise NBTError(f"a wing of a footprint is at least three across, "
                           f"or it is all wall: {solid[0]} has no room beside "
                           f"it")
        if not self._joined(inner):
            raise NBTError("a footprint's inside has to join, or a floor of "
                           "it cannot be walked: widen the wing between them")
        self.columns = frozenset(columns)
        self.inner = frozenset(inner)
        self.rim = frozenset(rim)
        self.x0 = min(x for x, _ in columns)
        self.x1 = max(x for x, _ in columns)
        self.z0 = min(z for _, z in columns)
        self.z1 = max(z for _, z in columns)

    @staticmethod
    def _rectangle(rectangle) -> set:
        try:
            x0, z0, x1, z1 = rectangle
        except (TypeError, ValueError):
            raise NBTError(f"a footprint's rectangle is (x0, z0, x1, z1), got "
                           f"{rectangle!r}") from None
        x0, x1 = sorted((_whole(x0, "x0"), _whole(x1, "x1")))
        z0, z1 = sorted((_whole(z0, "z0"), _whole(z1, "z1")))
        return {(x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)}

    @staticmethod
    def _joined(cells) -> bool:
        if not cells:
            return False
        left = set(cells)
        todo = [left.pop()]
        while todo:
            x, z = todo.pop()
            for side in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
                if side in left:
                    left.remove(side)
                    todo.append(side)
        return not left

    @property
    def bounds(self) -> tuple:
        """The rectangle round it, ``(x0, z0, x1, z1)``."""
        return (self.x0, self.z0, self.x1, self.z1)

    @staticmethod
    def boxes(columns) -> list:
        """``columns`` as ``(x0, z0, x1, z1)`` boxes, in sorted order: runs
        along z, joined across x where they match."""
        runs: dict = {}
        by_x: dict = {}
        for x, z in sorted(columns):
            by_x.setdefault(x, []).append(z)
        out: list = []
        for x in sorted(by_x):
            spans = []
            start = last = None
            for z in by_x[x]:
                if start is None:
                    start = last = z
                elif z == last + 1:
                    last = z
                else:
                    spans.append((start, last))
                    start = last = z
            spans.append((start, last))
            joined: dict = {}
            for span in spans:
                box = runs.pop(span, None)
                if box is not None and box[2] == x - 1:
                    box[2] = x
                else:
                    box = [x, span[0], x, span[1]]
                    out.append(box)
                joined[span] = box
            runs = joined
        return [tuple(box) for box in out]

    def eroded(self, by: int) -> "Footprint":
        """The footprint ``by`` columns in from every side, an L an L still,
        for a tier that steps back all round. Refused (an ``NBTError``):
        what the footprint refuses of what is left."""
        by = _whole(by, "by")
        if by < 0:
            raise NBTError(f"a footprint steps in, got by={by}")
        columns = set(self.columns)
        for _ in range(by):
            columns = {(x, z) for x, z in columns
                       if all((x + dx, z + dz) in columns
                              for dx in (-1, 0, 1) for dz in (-1, 0, 1))}
        if not columns:
            raise NBTError(f"{self!r} has nothing left {by} columns in")
        return Footprint(*((x0, z0, x1, z1) for x0, z0, x1, z1
                           in self.boxes(columns)))

    def _runs(self, facing) -> list:
        """The maximal runs of columns whose ``facing`` side is outside,
        ``(at, low, high)``: at the fixed coordinate, low..high along."""
        dx, dz = {"north": (0, -1), "south": (0, 1),
                  "east": (1, 0), "west": (-1, 0)}[facing]
        along_x = dz != 0
        found: dict = {}
        for x, z in self.columns:
            if (x + dx, z + dz) not in self.columns:
                at, along = (z, x) if along_x else (x, z)
                found.setdefault(at, []).append(along)
        runs = []
        for at in sorted(found):
            values = sorted(found[at])
            start = last = values[0]
            for value in values[1:]:
                if value != last + 1:
                    runs.append((at, start, last))
                    start = value
                last = value
            runs.append((at, start, last))
        return runs

    def walls(self, *, floor_y: int, top: int, stories=()) -> dict:
        """Every wall of its outline, ``{name: Wall}``: a :meth:`Wall.ring`
        for a rectangle, wall for wall."""
        common = dict(floor_y=floor_y, top=top, stories=stories)
        out: dict = {}
        for facing in ("north", "east", "south", "west"):
            count = 0
            for at, low, high in self._runs(facing):
                if facing in ("north", "south"):
                    # An inside corner has no side outside: its column is
                    # the run's to carry on to.
                    if (low - 1, at) in self.columns:
                        low -= 1
                    if (high + 1, at) in self.columns:
                        high += 1
                    start, end = (low, at), (high, at)
                    ends, origin = True, self.x0
                else:
                    if (at, low - 1) not in self.columns:
                        low += 1
                    if (at, high + 1) not in self.columns:
                        high -= 1
                    if high < low:
                        continue
                    start, end = (at, low), (at, high)
                    ends, origin = False, self.z0
                count += 1
                name = facing if count == 1 else f"{facing}-{count}"
                out[name] = Wall(start, end, outward=facing, ends=ends,
                                 origin=origin, **common)
        return out

    def __eq__(self, other) -> bool:
        if not isinstance(other, Footprint):
            return NotImplemented
        return self.columns == other.columns

    def __hash__(self) -> int:
        return hash((Footprint, self.columns))

    def __repr__(self) -> str:
        return (f"Footprint({self.x0}, {self.z0}, {self.x1}, {self.z1}, "
                f"{len(self.columns)} columns)")


class Opening:
    """A hole of one shape in a wall: :meth:`rect`, :meth:`arch` or
    :meth:`round`, made by those three and placed by :meth:`cells`. No
    world in it.

    ``at`` is the opening's lowest column along the wall, for every shape,
    and its bottom row stands ``sill`` above the floor of its story --
    ``sill=1`` is the feet row, so a hole of that sill is a passage. A
    ``fill`` (glazing) goes in layer 0 only, the wall's own line, with air
    behind it in a thicker wall, as the castle glazes its outer face; left
    out, the hole is open. Every material is named, never a palette's: a
    block by id or state, a :class:`~heaton_voxel.craft.style.Material`
    (its block, or the form a role needs), or a
    :class:`~heaton_voxel.craft.style.Mix` wherever the block goes in as it
    is -- a fill, a lintel, a keystone -- picked at each world cell. A
    glass Material as a ``fill`` is therefore full glass: the palette's
    ``glass`` is a pane only where :meth:`Builder.window` reads it, so
    glaze with a glass Material's ``.form("pane")`` for panes.
    """

    __slots__ = ("shape", "width", "height", "sill", "fill", "head",
                 "springer", "slab", "keystone", "drops", "radius",
                 "_facing")

    def __init__(self, shape: str, *, width, height: int, sill: int,
                 fill=None, head=None, springer=None, slab=None,
                 keystone=None, drops=None, radius=None):
        # Made by rect, arch and round, which check what they are given.
        self.shape = shape
        self.width = width
        self.height = height
        self.sill = sill
        self.fill = fill
        self.head = head
        self.springer = springer
        self.slab = slab
        self.keystone = keystone
        self.drops = drops
        self.radius = radius
        self._facing = {}
        if springer is not None:
            for word in (*_ENDS["x"], *_ENDS["z"]):
                self._facing[word] = springer.with_properties(facing=word)

    @staticmethod
    def _sill(sill, owner: str) -> int:
        sill = _whole(sill, "sill")
        if sill < 1:
            raise NBTError(f"{owner}: an opening starts at least one row "
                           f"above its floor -- sill=1 is the feet row -- "
                           f"got sill={sill}")
        return sill

    @staticmethod
    def _clear_fill(fill, owner: str):
        fill = _whole_block(fill, "fill", owner, "an opening", none=True)
        if isinstance(fill, BlockState) and _empty(fill):
            return None              # glazed with air is open
        return fill

    @classmethod
    def rect(cls, width, height: int, *, sill: int = 1, fill=None,
             head=None) -> "Opening":
        """A rectangle ``width`` by ``height``, with ``head`` -- a lintel --
        the row over it, through every layer. ``width=None`` fills a
        :class:`Facade`'s bay: every column between its piers, as many as
        the wall holds, which is the stables' band of panes; it is a
        window's only, and cut on its own it is an ``NBTError``."""
        owner = "Opening.rect"
        if width is not None:
            width = _whole(width, "width")
            if width < 1:
                raise NBTError(f"{owner}: width is at least 1, or None to "
                               f"fill a facade's bay, got {width}")
        height = _whole(height, "height")
        if height < 1:
            raise NBTError(f"{owner}: height is at least 1, got {height}")
        head = _whole_block(head, "head", owner, "an opening", none=True)
        if isinstance(head, BlockState) and _empty(head):
            raise NBTError(f"{owner}: a lintel of {head.name} is none; leave "
                           f"head out")
        return cls("rect", width=width, height=height,
                   sill=cls._sill(sill, owner),
                   fill=cls._clear_fill(fill, owner), head=head)

    @classmethod
    def arch(cls, width: int, height: int, *, springer, sill: int = 1,
             fill=None, keystone=None, profile=None, slab=None) -> "Opening":
        """An arch ``width`` wide, ``height`` clear at its crown, its head
        following ``profile``.

        ``profile`` is a vault's, in half blocks: how far the head has
        fallen below the crown at each column outward, crown first --
        :func:`~heaton_voxel.craft.build.vault.arch_profile` for a round
        head, :func:`~heaton_voxel.craft.build.vault.pointed_arch_profile`
        for a pointed one, a lancet -- one entry for each of the
        ``(width + 1) // 2`` columns from the crown to a jamb (an even
        width has two crown columns). It starts at 0, never climbs outward,
        and leaves every column a whole clear row. Each column is clear up
        to its fall below the crown; where that leaves half a block, the
        block over it is a top ``slab``, and where the head falls a whole
        block or more to the column outside it -- the jamb, past the last
        -- the block over it is a top ``springer`` stair facing that way,
        toward the jamb, so its full side carries the head down onto the
        wall. Every layer gets the same head. Left out, the profile is the
        castle's flat head: the crown's height all the way across, and a
        springer in each of the head row's two end cells.

        ``springer`` is a stair block, or a
        :class:`~heaton_voxel.craft.style.Material` (its stairs, and its
        slab where ``slab`` is left out); ``slab`` is needed only where the
        profile leaves a half block. A ``keystone`` sits over the crown, in
        layer 0, and only an odd width has a crown column to put it on.

        Refused (an ``NBTError``): a width or a height under 2, a profile
        of the wrong length, not starting at 0, climbing outward or closing
        a column, a keystone over an even width, a slab needed and not
        given; a stair or a slab of the wrong shape, or a Mix for either,
        as :class:`~heaton_voxel.craft.build.roof.Roof` refuses them. An
        arched passage needs ``height >= 3`` before a body walks its
        springer columns.
        """
        owner = "Opening.arch"
        width, height = _whole(width, "width"), _whole(height, "height")
        if width < 2 or height < 2:
            raise NBTError(f"{owner}: an arch is at least 2 wide and 2 high, "
                           f"got {width} by {height}")
        stair = _shaped(springer, "springer", owner, "stairs")
        stair = _fill(stair, _STAIR_STATE, drop=("facing",)).with_properties(
            half="top")
        if slab is None and isinstance(springer, Material) and springer.has("slab"):
            slab = springer
        if slab is not None:
            slab = _fill(_shaped(slab, "slab", owner, "slab"), _SLAB_STATE,
                         drop=("type",)).with_properties(type="top")
        columns = (width + 1) // 2
        if profile is None:
            drops = (0,) * (columns - 1) + (2,)
        else:
            if isinstance(profile, (str, bytes)):
                raise NBTError(f"{owner}: a profile is a sequence of drops, "
                               f"not {profile!r}")
            try:
                drops = tuple(_whole(d, "a drop") for d in profile)
            except TypeError:
                raise NBTError(f"{owner}: a profile is a sequence of drops, "
                               f"not {profile!r}") from None
            if len(drops) != columns:
                raise NBTError(
                    f"{owner}: an arch {width} wide has {columns} columns "
                    f"from its crown to a jamb, so its profile has {columns} "
                    f"drops, crown first; got {len(drops)}: {drops!r} -- "
                    f"arch_profile({columns - 1}, rise) is one")
            if drops[0] != 0:
                raise NBTError(f"{owner}: a profile is the drop below the "
                               f"crown at each column outward, so it starts "
                               f"at 0, got {drops!r}")
            if any(b < a for a, b in zip(drops, drops[1:])):
                raise NBTError(f"{owner}: the profile climbs outward at some "
                               f"point ({drops!r}); an arch's head falls "
                               f"toward its jambs")
        if drops[-1] > 2 * height - 2:
            raise NBTError(
                f"{owner}: the profile falls {drops[-1]} half blocks at its "
                f"end columns, which leaves them no whole clear row under a "
                f"crown {height} high; make it at least "
                f"{(drops[-1] + 3) // 2} high")
        if slab is None and any(d % 2 for d in drops):
            raise NBTError(
                f"{owner}: the profile {drops!r} leaves half a block over "
                f"some column, where the head is a top slab: pass slab=, or "
                f"a Material springer that has one")
        keystone = _whole_block(keystone, "keystone", owner, "an opening",
                                none=True)
        if keystone is not None and width % 2 == 0:
            raise NBTError(f"{owner}: a keystone sits over the crown column, "
                           f"and an arch {width} wide has two: make it odd, "
                           f"or leave keystone out")
        return cls("arch", width=width, height=height,
                   sill=cls._sill(sill, owner),
                   fill=cls._clear_fill(fill, owner), springer=stair,
                   slab=slab, keystone=keystone, drops=drops)

    @classmethod
    def round(cls, radius: int, *, sill: int = 1, fill=None) -> "Opening":
        """A round hole of ``radius``: the cells of
        :func:`~heaton_voxel.craft.geometry.disc`'s rule stood on its side,
        ``(a - c)² + (y - cy)² <= r² + r`` about the center ``c = at + r``,
        ``cy`` the bottom row plus ``r`` -- the castle's tested rounding,
        so ``r=2`` is 21 cells. ``2r + 1`` wide and high."""
        owner = "Opening.round"
        radius = _whole(radius, "radius")
        if radius < 1:
            raise NBTError(f"{owner}: a round opening has a radius of at "
                           f"least 1, got {radius}")
        side = 2 * radius + 1
        return cls("round", width=side, height=side,
                   sill=cls._sill(sill, owner),
                   fill=cls._clear_fill(fill, owner), radius=radius)

    @property
    def reach(self) -> int:
        """How many rows the opening takes, from its bottom row to its
        highest cell: a lintel or a keystone included."""
        extra = self.head if self.shape == "rect" else self.keystone
        return self.height + (1 if extra is not None else 0)

    def _clear(self, k: int) -> int:
        """How high column ``k`` of an arch is clear, in half blocks; the
        jambs either side, none."""
        if not 0 <= k < self.width:
            return 0
        d = (self.width - 1) // 2 - min(k, self.width - 1 - k)
        return 2 * self.height - self.drops[d]

    def cells(self, wall, at: int, *, story: int = 0) -> dict:
        """Every cell the opening writes in ``wall`` at column ``at`` on
        ``story``, with what it writes there -- air, the fill, a springer,
        a top slab, the lintel, the keystone -- sorted by ``(x, y, z)``. A
        Mix is picked at each cell. Pure: it reads no world.

        Refused (an ``NBTError``): a cell off the wall's columns or rows,
        and a :meth:`rect` of ``width=None``, which is a facade's bay."""
        if not isinstance(wall, Wall):
            raise NBTError(f"an opening is cut in a Wall, got "
                           f"{type(wall).__name__}")
        at = _whole(at, "at")
        if self.width is None:
            raise NBTError("Opening.rect(None, ...) fills a facade's bay; "
                           "cut on its own, an opening has a width")
        bottom = wall.floor_of(story) + self.sill
        columns = list(range(at, at + self.width))
        return dict(sorted(self._placed(wall, columns, bottom).items()))

    def _placed(self, wall, columns, bottom) -> dict:
        """:meth:`_layout` checked inside ``wall`` and picked."""
        top = bottom + self.reach - 1
        for a in (columns[0], columns[-1]):
            if a not in wall.along:
                raise NBTError(
                    f"the opening reaches column {a}, off the wall, which "
                    f"runs {wall.low}..{wall.high}")
        if top > wall.top:
            raise NBTError(
                f"the opening reaches row {top}, over the wall's top, "
                f"{wall.top}")
        pick = _Picks()
        return {cell: pick(value, cell)
                for cell, value in self._layout(wall, columns, bottom)}

    def _layout(self, wall, columns, bottom) -> list:
        """``(cell, value)`` for every cell, column by column -- ``columns``
        the along coordinates it takes, low to high -- each value a state or
        a Mix not yet picked."""
        out: list = []
        layers = range(wall.thickness)

        def through(a, y, value):
            for layer in layers:
                out.append((wall.cell(a, y, layer), value))

        def clear(a, y):
            for layer in layers:
                out.append((wall.cell(a, y, layer),
                            self.fill if layer == 0 and self.fill is not None
                            else _AIR))

        if self.shape == "rect":
            for a in columns:
                for y in range(bottom, bottom + self.height):
                    clear(a, y)
                if self.head is not None:
                    through(a, bottom + self.height, self.head)
        elif self.shape == "round":
            r = self.radius
            for a, y in disc(r, center=(columns[0] + r, bottom + r)):
                clear(a, y)
        else:
            low, high = _ENDS[wall.axis]
            at, width = columns[0], self.width
            for k in range(width):
                a = at + k
                c = self._clear(k)
                for y in range(bottom, bottom + c // 2):
                    clear(a, y)
                if c >= 2 * self.height:
                    continue
                y = bottom + c // 2
                if c % 2:                    # half a block left: a top slab
                    through(a, y, self.slab)
                    continue
                if 2 * k == width - 1:       # the crown has no outside
                    continue
                side = k - 1 if 2 * k < width - 1 else k + 1
                if c - self._clear(side) >= 2:
                    through(a, y, self._facing[low if 2 * k < width - 1
                                               else high])
            if self.keystone is not None:
                out.append((wall.cell(at + (width - 1) // 2,
                                      bottom + self.height), self.keystone))
        return out

    def __repr__(self) -> str:
        if self.shape == "round":
            return f"Opening.round({self.radius}, sill={self.sill})"
        return (f"Opening.{self.shape}({self.width}, {self.height}, "
                f"sill={self.sill})")


class _FaceRule:
    """What the writers ask of a rule for a wall's face: its :meth:`plan`,
    every cell of a :class:`Wall` with the state it puts there, and the
    :meth:`verify` that holds a standing wall to that plan. A :class:`Facade`
    is one and an :class:`~heaton_voxel.craft.build.elevation.Elevation`
    another; ``corner`` and ``_corner_value`` say what it builds at a
    wall's corners, for :meth:`WallsMixin.shell` to hold a ring's rules to
    one another, and a rule that names none takes them in its courses."""

    __slots__ = ()

    _noun = "rule"
    corner = None
    _corner_value = None

    def plan(self, wall) -> dict:
        raise NotImplementedError

    def recolored(self, mapping):
        """The same rule in other blocks: ``{block: block}``, by id, the
        pattern untouched. Refused (an ``NBTError``): a key the rule does
        not hold."""
        table = _Recolor(mapping)
        rule = self._recolored(table)
        table.check()
        return rule

    def _recolored(self, table):
        raise NotImplementedError

    def verify(self, save, wall, *, dimension: str | None = None,
               missing_ok: bool = False, cut=None, skip=()) -> list[str]:
        """Every cell of ``wall`` that does not hold what the rule puts
        there, in plan order: the check to run on a wall somebody built
        before trusting a rule to describe it. The same game state passes,
        apart from what a block update derives -- a pane's or a fence's
        sides, a stair's corner shape -- and ``missing_ok`` forgives any of
        the game's airs, for a wall not finished. ``cut`` is ``{cell:
        state}``, what openings cut in the wall write, held in the rule's
        place, a door hung in one's open cells passing. ``skip`` is cells it
        does not answer for: a floor running through a layer behind the
        wall's line, where the floor is what stands."""
        wrong = []
        cut = cut or {}
        for cell, expect in {**self.plan(wall), **cut}.items():
            if cell in skip:
                continue
            found = save.get_block(*cell, dimension=dimension)
            if found is None:
                wrong.append(f"{cell}: nothing to read -- the chunk is not "
                             f"generated, or y is outside the world")
            elif not _same_crown(found, expect):
                if (missing_ok and _empty(found)) or (cell in cut
                                                      and _hung(found)):
                    continue
                wrong.append(f"{cell} holds {found}, where the {self._noun} "
                             f"has {expect}")
        return wrong


class Facade(_FaceRule):
    """The rule a wall's face follows: columns across, courses up, a window
    in every bay of every story. No world in it.

    Across the run, each column is the first of: a **corner**, at the ends
    of a wall with ``ends``; a **pier**, where ``pitch`` and ``pier`` are
    given and ``(along - phase) % pitch < pier_width``; else a **bay**.
    That congruence is on the run's own world coordinate, so with an
    integer ``phase`` -- a coordinate a pier stands on -- two walls agree on
    every pier wherever they stand, and with ``phase=None`` each counts
    from its wall's :attr:`Wall.origin`, the low end, which
    :meth:`Wall.ring` makes one corner for all four. Piers run through every
    course, and so do corners -- ``corner``, else ``pier`` -- while a
    corner given neither takes a bay's courses, row by row, and no window.

    Up a bay, each row is the first of: the ``head`` course, ``(rows,
    block)``, the top rows of the wall; a band, ``bands={offset: block}``,
    ``offset`` rows from each upper story's floor row (``0`` is the floor
    row itself, ``-1`` the row under it); the ``plinth``, ``(rows,
    block)``, the ground story's bottom rows; else ``field``.

    ``window``, an :class:`Opening`, goes in every bay of every story in
    ``window_stories`` (None for all), its bottom row ``sill`` over the
    story's floor. It starts ``window_offset`` columns into its bay, or
    centered between the piers -- ``pier_width + (pitch - pier_width -
    width) // 2``, the odd column of slack falling on the high-along side --
    and is placed only whole: every column a bay's, inside the wall's end
    columns and ``margin`` columns from each end. ``Opening.rect(None,
    ...)`` fills each bay instead, every one of its columns the wall holds
    at least ``margin`` from an end. A window cuts through courses, never
    through a pier or a corner.

    Every material is named -- a block, a
    :class:`~heaton_voxel.craft.style.Material` (its block) or a
    :class:`~heaton_voxel.craft.style.Mix`, picked at each world cell as
    the facade is planned, since every role here is a whole block -- and
    ``field`` is required; a palette's is ``b.palette[role]``. Refused as
    the rule is made (an ``NBTError``): a pier or a window with no pitch, a
    ``pier_width`` not under it, a window wider than its bay or offset onto
    a pier; and when it is laid over a wall (:meth:`plan`), a window
    reaching the next story's floor row or past the top, and a story the
    wall does not have.
    """

    __slots__ = ("field", "corner", "pier", "pitch", "phase", "pier_width",
                 "plinth", "bands", "head", "window", "window_stories",
                 "window_offset", "margin")

    _noun = "facade"

    def __init__(self, field, *, corner=None, pier=None, pitch=None,
                 phase=None, pier_width: int = 1, plinth=None, bands=None,
                 head=None, window=None, window_stories=None,
                 window_offset=None, margin: int = 0):
        owner, noun = "Facade", "a facade"
        self.field = _whole_block(field, "field", owner, noun)
        self.corner = _whole_block(corner, "corner", owner, noun, none=True)
        self.pier = _whole_block(pier, "pier", owner, noun, none=True)
        if pitch is not None:
            pitch = _whole(pitch, "pitch")
        self.pitch = pitch
        self.phase = None if phase is None else _whole(phase, "phase")
        self.pier_width = _whole(pier_width, "pier_width")
        if self.pier_width < 1:
            raise NBTError(f"a pier is at least one column wide, got "
                           f"pier_width={pier_width}")
        if pitch is None:
            if self.pier is not None or window is not None:
                raise NBTError(
                    f"a facade's {'piers' if self.pier is not None else 'windows'} "
                    f"stand one bay apart, and it has no pitch: pass pitch=, "
                    f"the columns from one pier to the next")
        elif self.pier_width >= pitch:
            raise NBTError(f"pier_width={self.pier_width} leaves no bay "
                           f"between piers {pitch} apart")
        self.plinth = self._course(plinth, "plinth")
        self.head = self._course(head, "head")
        if bands is None:
            self.bands = ()
        else:
            if not isinstance(bands, dict):
                raise NBTError(f"bands is {{offset: block}}, rows from each "
                               f"upper story's floor row, not {bands!r}")
            self.bands = tuple(
                (_whole(offset, "a band's offset"),
                 _whole_block(block, f"bands[{offset}]", owner, noun))
                for offset, block in sorted(bands.items()))
        if window is not None and not isinstance(window, Opening):
            raise NBTError(f"window is an Opening, got {window!r}")
        self.window = window
        if window_stories is not None:
            if isinstance(window_stories, (str, bytes)):
                raise NBTError(f"window_stories is a sequence of story "
                               f"numbers, not {window_stories!r}")
            stories = tuple(sorted(set(_whole(s, "a window story")
                                       for s in window_stories)))
            if stories and stories[0] < 0:
                raise NBTError(f"stories count up from 0, the ground, got "
                               f"{window_stories!r}")
            window_stories = stories
        self.window_stories = window_stories
        if window_offset is not None:
            window_offset = _whole(window_offset, "window_offset")
        self.window_offset = window_offset
        self.margin = _whole(margin, "margin")
        if self.margin < 0:
            raise NBTError(f"margin is a number of columns, got {margin}")
        if window is not None:
            bay = pitch - self.pier_width
            if window.width is None:
                if window_offset is not None:
                    raise NBTError("a window that fills its bay has no "
                                   "offset in it: leave window_offset out")
            else:
                if window.width > bay:
                    raise NBTError(
                        f"a window {window.width} wide does not fit a bay "
                        f"{bay} wide, between piers {pitch} apart")
                if window_offset is not None and (
                        window_offset < self.pier_width
                        or window_offset + window.width > pitch):
                    raise NBTError(
                        f"window_offset={window_offset} puts a window "
                        f"{window.width} wide on a pier: its bay is columns "
                        f"{self.pier_width}..{pitch - 1} of every {pitch}")

    @staticmethod
    def _course(spec, arg: str):
        if spec is None:
            return None
        try:
            rows, block = spec
        except (TypeError, ValueError):
            raise NBTError(f"{arg} is (rows, block), got {spec!r}") from None
        rows = _whole(rows, f"{arg}'s rows")
        if rows < 1:
            raise NBTError(f"{arg} is at least one row, got {rows}")
        return rows, _whole_block(block, arg, "Facade", "a facade")

    @property
    def _corner_value(self):
        """What a corner column is built of, top to bottom: ``corner``,
        else ``pier``, else None -- the courses a bay has, row by row, as
        the Roman insula's corners carry its plinth and its bands."""
        for value in (self.corner, self.pier):
            if value is not None:
                return value
        return None

    def _recolored(self, table):
        rule = copy.copy(self)
        rule.field = table(self.field)
        rule.corner = table(self.corner)
        rule.pier = table(self.pier)
        rule.plinth = (None if self.plinth is None
                       else (self.plinth[0], table(self.plinth[1])))
        rule.head = (None if self.head is None
                     else (self.head[0], table(self.head[1])))
        rule.bands = tuple((offset, table(block))
                           for offset, block in self.bands)
        if self.window is not None:
            window = copy.copy(self.window)
            window.fill = table(self.window.fill)
            rule.window = window
        return rule

    def _phase(self, wall) -> int:
        return wall.origin if self.phase is None else self.phase

    def _kind(self, wall, along: int, phase: int) -> str:
        if wall._end(along):
            return "corner"
        if (self.pitch is not None and self.pier is not None
                and (along - phase) % self.pitch < self.pier_width):
            return "pier"
        return "bay"

    def kind(self, wall, along: int) -> str:
        """What column ``along`` of ``wall`` is: ``"corner"``, ``"pier"`` or
        ``"bay"``. A glazed bay gives an iron door a jamb to carry its
        button only where it meets a pier, so this is the column to ask
        before hanging one."""
        if not isinstance(wall, Wall):
            raise NBTError(f"a facade is laid over a Wall, got "
                           f"{type(wall).__name__}")
        along = _whole(along, "along")
        if along not in wall.along:
            raise NBTError(f"column {along} is off the wall, which runs "
                           f"{wall.low}..{wall.high}")
        return self._kind(wall, along, self._phase(wall))

    def _row(self, wall, y: int):
        """A bay's course at row ``y``: head, then band, then plinth, then
        field. Where two stories' bands reach one row, the lower story's
        wins."""
        if self.head is not None and y > wall.top - self.head[0]:
            return self.head[1]
        if self.bands:
            for level in wall.stories:
                for offset, block in self.bands:
                    if y - level == offset:
                        return block
        if self.plinth is not None and y <= wall.floor_y + self.plinth[0]:
            return self.plinth[1]
        return self.field

    def plan(self, wall) -> dict:
        """Every cell of ``wall`` with the state the facade puts there,
        windows cut, sorted by ``(x, y, z)``: the replay. Pure: it reads no
        world, and a Mix is picked at each cell, so what is planned is what
        is written."""
        if not isinstance(wall, Wall):
            raise NBTError(f"a facade is laid over a Wall, got "
                           f"{type(wall).__name__}")
        phase = self._phase(wall)
        corner = self._corner_value
        rows = {y: self._row(wall, y) for y in wall.rows}
        pick = _Picks()
        cells: dict = {}
        for a in wall.along:
            kind = self._kind(wall, a, phase)
            for layer in range(wall.thickness):
                for y in wall.rows:
                    value = (self.pier if kind == "pier" else
                             corner if kind == "corner" and corner is not None
                             else rows[y])
                    cell = wall.cell(a, y, layer)
                    cells[cell] = pick(value, cell)
        cells.update(self._windows(wall, phase))
        return dict(sorted(cells.items()))

    def _windows(self, wall, phase: int) -> dict:
        """The window's cells, every bay of every story it goes in."""
        window = self.window
        if window is None:
            return {}
        stories = (range(len(wall.floors)) if self.window_stories is None
                   else self.window_stories)
        a0, a1, pitch = wall.low, wall.high, self.pitch
        out: dict = {}
        for story in stories:
            if story >= len(wall.floors):
                raise NBTError(f"the facade glazes story {story}, and the wall "
                               f"has stories 0..{len(wall.floors) - 1}")
            bottom = wall.floors[story] + window.sill
            ceiling = (wall.floors[story + 1] - 1
                       if story + 1 < len(wall.floors) else wall.top)
            if bottom + window.reach - 1 > ceiling:
                raise NBTError(
                    f"the window on story {story} reaches row "
                    f"{bottom + window.reach - 1}, past "
                    f"{'the row under the next floor, ' if ceiling < wall.top else 'the top, '}"
                    f"{ceiling}: lower its sill or make it shorter")
            for j in range((a0 - phase) // pitch, (a1 - phase) // pitch + 1):
                start = phase + j * pitch
                if window.width is None:
                    columns = [a for a in (start + k for k in
                                           range(self.pier_width, pitch))
                               if a0 <= a <= a1
                               and min(a - a0, a1 - a) >= self.margin
                               and self._kind(wall, a, phase) == "bay"]
                else:
                    k0 = (self.pier_width + (pitch - self.pier_width
                                             - window.width) // 2
                          if self.window_offset is None
                          else self.window_offset)
                    columns = [start + k0 + i for i in range(window.width)]
                    edge = max(1, self.margin)
                    if not all(a0 <= a <= a1 and min(a - a0, a1 - a) >= edge
                               and self._kind(wall, a, phase) == "bay"
                               for a in columns):
                        continue
                if columns:
                    out.update(window._placed(wall, columns, bottom))
        return out

    def __repr__(self) -> str:
        parts = [repr(self.field)]
        if self.pitch is not None:
            parts.append(f"pitch={self.pitch}")
        if self.phase is not None:
            parts.append(f"phase={self.phase}")
        if self.window is not None:
            parts.append(f"window={self.window!r}")
        return f"Facade({', '.join(parts)})"


class _ShellPlan:
    """A shell as :meth:`WallsMixin._plan_shell` planned it: its walls, the
    cuts that empty it, and every cell it would write, in write order."""

    __slots__ = ("walls", "outline", "perimeter", "cuts", "fills", "fabric",
                 "opened", "unglazed", "planned", "kept_open")

    def __init__(self, walls, outline, perimeter, cuts, fills, fabric, opened,
                 unglazed, planned, kept_open):
        self.walls = walls
        self.outline = outline
        self.perimeter = perimeter
        self.cuts = cuts
        self.fills = fills
        self.fabric = fabric
        self.opened = opened
        self.unglazed = unglazed
        self.planned = planned
        self.kept_open = kept_open


class WallResult:
    """What a :meth:`~heaton_voxel.craft.build.Builder.wall` or
    :meth:`~heaton_voxel.craft.build.Builder.shell` laid.

    ``walls`` is every :class:`Wall` by its outward word -- a shell's four,
    a wall's one -- ``floors`` their floors, the ground's first, and
    ``top`` their top row. ``blocks`` counts the cells planned, ``already``
    those that held the right block before the call -- or, in an opening,
    a door or a gate hung in it -- and were left as they stand,
    ``openings`` the cells the openings planned, and ``kept_open`` the
    floor cells a flight this session built claims, left open as its
    stairwell. A shell also gives its ``outline``, ``(x0, z0, x1, z1)``
    sorted, and ``perimeter``, the closed corner path its parapet was laid
    on, for :meth:`~heaton_voxel.craft.build.Builder.battlement` to
    crenellate at the parapet's top row, ``top + 1 + parapet_height`` --
    ``top + 2`` for a parapet one block high -- and a roof laid later goes
    over ``outline`` from ``top + 1``; None for both from a wall.
    """

    __slots__ = ("walls", "floors", "top", "outline", "perimeter", "blocks",
                 "already", "openings", "kept_open")

    def __init__(self, walls, *, outline=None, perimeter=None):
        self.walls = dict(walls)
        first = next(iter(self.walls.values()))
        self.floors = first.floors
        self.top = first.top
        self.outline = outline
        self.perimeter = perimeter
        self.blocks = 0
        self.already = 0
        self.openings = 0
        self.kept_open = 0

    def __repr__(self) -> str:
        parts = [f"{self.blocks:,} blocks"]
        if self.already:
            parts.append(f"{self.already} already right")
        if self.openings:
            parts.append(f"{self.openings} in openings")
        if self.kept_open:
            parts.append(f"{self.kept_open} kept open for a flight")
        return (f"<WallResult {len(self.walls)} "
                f"wall{'s' if len(self.walls) != 1 else ''}, "
                f"{', '.join(parts)}>")


class WallsMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods: walls -- one run
    (:meth:`wall`), a building's shell (:meth:`shell`), a hole cut in one
    standing (:meth:`cut_opening`) -- and what crowns them
    (:meth:`battlement`).

    Builder inherits them; nothing here is used on its own.
    """

    def battlement(self, path=None, y=None, *, ring=None, block,
                   height: int = 1, merlon=None, overwrite: bool = False,
                   ) -> tuple[tuple[int, int, int], ...]:
        """A parapet course at ``y`` and merlons standing on it: a crenellated
        wall top, along ``path`` or round a tower's ``ring``.

        ``path`` is ``(x, z)`` vertices along straight runs -- every pair of
        consecutive vertices shares an x or a z -- and is closed when its
        last vertex is its first, as a tower top or a curtain wall's
        circuit is. Each run, corner to corner, takes its teeth by
        :func:`merlon_offsets`, so every corner is a merlon and a run of
        even length doubles up beside its far corner; a vertex the path runs
        straight through is no corner and keeps the rhythm. ``ring`` is a
        round tower's rim instead, in order round it --
        ``geometry.ring(r, center=...)`` -- and alternates from its first
        cell, as the castle's towers are crowned; an odd count is refused.
        Pass one of the two.

        Every cell of the path or ring gets ``block`` at ``y``; a merlon
        cell gets ``merlon`` (``block`` when left out) from ``y + 1`` up
        ``height`` blocks, and the gaps between them must be empty that
        high. Both are named, never a palette's -- pass ``b.palette[role]``
        for one -- and either may be a
        :class:`~heaton_voxel.craft.style.Mix`, each cell its pick there,
        or a :class:`~heaton_voxel.craft.style.Material`, its block.

        A course already standing is taken as it is: a cell already holding
        what the battlement would write there -- the same game state, apart
        from what a block update derives, a fence's or a wall's sides and a
        stair's corner shape -- is no obstacle and is left as it stands, so
        a wall's solid top course, laid first at ``y``, is crenellated by
        laying the battlement over it, and a fence or wall battlement that
        :meth:`finish` has joined up is found again whole by the same call.
        Anything else in a course or merlon cell, and
        anything but air in a gap, stops the call by name, before a block
        is written; ``overwrite=True`` cuts through it, emptying the gaps.
        Then the plan is audited (``Builder._audit_plan``), as a wall's is:
        a gap cut from behind a torch or a button, or a course that would
        leave a block the game will not keep, is refused, faults that stood
        before it not counted.

        Returns the top cell of every merlon, ``(x, y + height, z)``, in
        path or ring order, so a caller can stand a torch on every n-th
        one.

        Refused before anything is read: a path that runs diagonally, turns
        back on itself or crosses itself at a cell it would be both merlon
        and gap, a ring out of order or of odd length, a ``y`` or a
        ``height`` that is not a whole number (an ``NBTError``); a block
        that is none (a ``TypeError``) or one the game does not have
        (:class:`~heaton_voxel.craft.errors.IllegalBlock`); a chunk that is
        not generated or not finished, or a height past the world
        (:class:`~heaton_voxel.craft.errors.Refused`). Then a cell already
        taken, and a worksite's guard, refuse it the same way. A session
        whose worksite was restored raises before any of it.
        """
        self._alive()
        if (path is None) == (ring is None):
            raise NBTError("a battlement goes along a path or round a ring: "
                           "pass exactly one of the two")
        if isinstance(y, bool) or not isinstance(y, int):
            raise NBTError(f"a battlement's course is laid at a whole y, "
                           f"got y={y!r}")
        if (isinstance(height, bool) or not isinstance(height, int)
                or height < 1):
            raise NBTError(f"a merlon stands a whole number of blocks, at "
                           f"least one, above its course; got "
                           f"height={height!r}")
        block = _whole_block(block, "block")
        merlon = block if merlon is None else _whole_block(merlon, "merlon")
        cells = (_ring_roles(ring) if ring is not None
                 else _path_roles(path))

        # Every cell the battlement reads and may write, with what it would
        # write there, in write order: the course in path order, then each
        # level above it in path order -- a merlon's block, or air in a gap.
        planned: list[tuple[tuple[int, int, int], object, bool]] = [
            ((x, y, z), block, False) for x, z, _ in cells]
        for level in range(y + 1, y + height + 1):
            planned += [((x, level, z), merlon if tooth else _AIR, not tooth)
                        for x, z, tooth in cells]
        self._vet_plan([("set", cell, state) for cell, state, _ in planned],
                       what="battlement")

        picked: dict = {}

        def want(cell, state) -> BlockState:
            if isinstance(state, Mix):
                name = state.pick(*cell)
                if name not in picked:
                    picked[name] = _state(name)
                return picked[name]
            return state

        already, blocked, into = _occupancy(
            self._read,
            ((cell, want(cell, state)) for cell, state, _ in planned),
            same=_same_crown, empty=_empty, overwrite=overwrite,
            noun="battlement")
        if blocked:
            raise Refused(
                f"{len(blocked)} cells the battlement needs are already "
                f"taken, first: {blocked[0]}. Clear the wall top first, or "
                f"pass overwrite=True to cut through whatever is there. "
                f"Nothing was written", faults=blocked)
        # A cell already holding what it would get is left as it stands --
        # a joined-up fence keeps its sides -- and a gap is written only
        # where something stands in it to cut away: one already empty, any
        # of the game's airs, is left as it is.
        ops = [("set", cell, state) for cell, state, gap in planned
               if cell not in already
               and (not gap or not _empty(into[cell]))]
        self._audit_plan(ops, what="battlement")
        self._commit(ops, what="battlement")
        return tuple((x, y + height, z) for x, z, tooth in cells if tooth)

    # -- walls, shells and openings ------------------------------------------

    def _settle(self, planned, *, fabric, opened, unglazed, overwrite: bool,
                noun: str, cleared=()):
        """What stands in the cells a wall writer plans, before any of it is
        written: ``(already, faults, writes)``, ``planned`` scanned in write
        order.

        A cell already holding what it would get -- the same game state,
        apart from what a block update derives (:func:`_same_crown`) -- is
        left as it stands, and so is an open cell of an opening (one of
        ``unglazed``) with a door or a gate hung in it (:func:`_hung`),
        whatever ``overwrite`` says: a hole with a door in it is still the
        hole. Nothing else is: water in a doorway, a cobweb or a torch is
        something standing in the hole, a fault unless ``overwrite``, which
        writes air through it. A cell holding any of the game's airs is
        written, and so is one an opening cuts (one of ``opened``) where it
        holds what the wall's facade puts there (``fabric``): that is the
        wall the opening is cut in. So is a cell inside one of the
        ``cleared`` boxes, ``(low, high)`` corners a cut empties before the
        plan is written: what stands there is gone by then. Anything else is
        a fault unless ``overwrite``; and a block planned into a cell a
        flight or an approach this session built claims is a fault whatever
        ``overwrite`` says, since it would stand in its treads or its head
        room."""
        already: set = set()
        faults: list[str] = []
        writes: list = []
        for cell, want in planned:
            if not _empty(want) and cell in self._claimed:
                who = self._claimant(cell)
                advice = ("build the shell before its flights"
                          if who == "a flight" else
                          "keep the shell off the approach and its head room")
                faults.append(f"{cell} is claimed by {who} this session "
                              f"built, where the {noun} would put {want}: "
                              f"{advice}")
                continue
            found = self._read(cell)
            if _same_crown(found, want) or (cell in unglazed
                                            and _hung(found)):
                already.add(cell)
                continue
            if not (_empty(found) or overwrite
                    or any(all(low[i] <= cell[i] <= high[i] for i in range(3))
                           for low, high in cleared)
                    or (cell in opened and cell in fabric
                        and _same_crown(found, fabric[cell]))):
                faults.append(f"{cell} holds {found}, where the {noun} "
                              f"needs {want}")
                continue
            writes.append((cell, want))
        return already, faults, writes

    def _cut(self, wall, openings, fabric) -> tuple[dict, set]:
        """Every cell ``openings`` -- ``(Opening, at, story)`` triples --
        cut in ``wall``, over the facade's ``fabric``: ``(opened,
        unglazed)``, the opened cells with their states and the open ones
        among them."""
        opened: dict = {}
        for opening, at, story in openings:
            opened.update(opening.cells(wall, at, story=story))
        return opened, {cell for cell, state in opened.items()
                        if _empty(state)}

    def _refuse_taken(self, faults, *, what: str, advice: str) -> None:
        if faults:
            raise Refused(
                f"{len(faults)} cells the {what} needs are already taken, "
                f"first: {faults[0]}. {advice}. Nothing was written",
                faults=faults)

    def wall(self, wall, facade, *, openings=(),
             overwrite: bool = False) -> WallResult:
        """Lay one :class:`Wall` by its :class:`Facade` or
        :class:`~heaton_voxel.craft.build.elevation.Elevation`, with
        ``openings`` -- ``(Opening, at)`` or ``(Opening, at, story)`` --
        cut in it: the rule's plan, the openings over it, checked and
        written in ``(x, y, z)`` order. A partition, a party wall, a
        garden wall: ``ends`` is the wall's own to say.

        A cell already right is left as it stands and counted ``already``,
        and an open cell of an opening with a door or a gate hung in it is
        the hole already there, kept whatever ``overwrite`` says. Anything
        else standing in a cell -- water or a cobweb in an opening too --
        stops the call by name, every cell in the refusal's faults, before
        a block is written, but where an opening cuts the wall the facade
        itself builds; ``overwrite=True`` writes through it. A block
        in a cell a flight or an approach this session built claims is
        refused whatever ``overwrite`` says. Then the plan is audited
        (``Builder._audit_plan``): a wall that would leave a block the game
        will not keep -- a torch or a ladder with nothing to hang on, a
        chest under a block that conducts -- or shut a door further than it
        was, is refused, faults that stood before it not counted. Refused
        before anything is read: a block the game does not have
        (:class:`~heaton_voxel.craft.errors.IllegalBlock`), a chunk not
        generated or not finished, or a height past the world
        (:class:`~heaton_voxel.craft.errors.Refused`); last, a worksite's
        guard. Returns a :class:`WallResult`.
        """
        self._alive()
        if not isinstance(wall, Wall):
            raise NBTError(f"wall takes a Wall, got {type(wall).__name__}; "
                           f"make one with Wall(start, end, floor_y=..., "
                           f"top=..., outward=...)")
        if not isinstance(facade, _FaceRule):
            raise NBTError(f"wall takes a Facade or an Elevation, got "
                           f"{type(facade).__name__}")
        cuts = [_opening_at(item, faced=False) for item in openings]
        fabric = facade.plan(wall)
        opened, unglazed = self._cut(wall, cuts, fabric)
        plan = dict(fabric)
        plan.update(opened)
        planned = sorted(plan.items())
        self._vet_plan([("set", cell, state) for cell, state in planned],
                       what="wall")
        already, faults, writes = self._settle(
            planned, fabric=fabric, opened=opened, unglazed=unglazed,
            overwrite=overwrite, noun="wall")
        self._refuse_taken(
            faults, what="wall",
            advice="Clear the line first, or pass overwrite=True to build "
                   "through whatever is there")
        ops = [("set", cell, state) for cell, state in writes]
        self._audit_plan(ops, what="wall")
        self._commit(ops, what="wall")
        result = WallResult({wall.outward: wall})
        result.blocks = len(planned)
        result.already = len(already)
        result.openings = len(opened)
        return result

    def shell(self, x0, z0, x1, z1, facade, *, floor_y: int, top: int,
              stories=(), faces=None, thickness: int = 1, openings=(),
              ground=None, floor=None, deck=None, parapet=None,
              parapet_height: int = 1, roof=None, attic=None,
              hollow: bool = True, voids=(), footprint=None,
              overwrite: bool = False) -> WallResult:
        """A building's shell over the rectangle ``(x0, z0)`` to ``(x1,
        z1)``, inclusive: its four walls by :meth:`Wall.ring`, laid by
        ``facade``, a :class:`Facade` or an
        :class:`~heaton_voxel.craft.build.elevation.Elevation` -- each face
        by ``faces[name]`` where that names one,
        ``"north"``, ``"east"``, ``"south"`` or ``"west"`` -- with
        ``openings`` cut in them, ``(face, Opening, at)`` or ``(face,
        Opening, at, story)``; its stories stacked from ``floor_y``, the
        floor it stands on, at ``stories``, up to ``top``, the walls' top
        row; and whichever of the rest are named, planned whole and written
        in this order:

        1. ``hollow`` (on by default): the inside of every story cleared,
           bottom up, from the floor over it to the row under the next --
           a cut, like :meth:`clear_interior`, which writes air through
           whatever is there, and refused where a flight (or an approach)
           this session built stands inside: build the shell before its
           flights;
        2. ``ground``: the whole footprint at ``floor_y``, a cut too, since
           it replaces the pad it stands on;
        3. the walls, every cell in ``(x, y, z)`` order;
        4. ``floor``: every upper story's floor inside the walls, story by
           story -- leaving open, and counting in ``kept_open``, the cells
           a flight this session built claims there, which are its
           stairwell, and the ``voids``, columns ``(x, z)`` no floor
           crosses: a core or an atrium;
        5. ``deck``: the footprint at ``top + 1``;
        6. ``parapet``: a solid course ``parapet_height`` high on the
           deck's rim, from ``top + 2``, over exactly the cells of
           :meth:`Wall.perimeter` -- which the result gives as
           ``perimeter`` -- so a battlement over that path at the
           parapet's top row, ``battlement(result.perimeter, top + 1 +
           parapet_height, block=...)`` (``top + 2`` at the default
           height), crenellates it: that course is taken as it stands, and
           its corners are merlons by construction. One row lower, its gaps
           would hold the parapet's top row, and it is refused;
        7. ``roof``: a :class:`~heaton_voxel.craft.build.roof.Roof` over the
           walls' outline from ``top + 1``, its ``attic`` required as
           :meth:`roof` requires it -- or no roof here, and
           ``b.roof(rule, result.outline, result.top + 1, attic=...)``
           after.

        Each material is named, never a palette's: a block, a
        :class:`~heaton_voxel.craft.style.Material` (its block), or a
        :class:`~heaton_voxel.craft.style.Mix`, picked at each world cell.
        Every cell but the two cuts is checked before anything is written,
        as :meth:`wall` checks its own -- already right and left, a door or
        a gate hung in an opening kept, anything else taken and refused by
        name unless ``overwrite=True``, never into a flight's claim -- and
        then the whole plan is audited for what the game would not keep, or
        a door shut further than it was. A door is hung after
        the shell (``b.doorway(*result.walls["south"].door(a),
        door=...)``), where :meth:`Facade.kind` says a pier will carry an
        iron door's buttons, and a flight is built after it, through the
        floor it climbs to, which cuts its own stairwell.

        ``footprint``, a :class:`Footprint`, is the plan where it is no
        rectangle: the walls are its own, ``(x0, z0)`` to ``(x1, z1)`` its
        bounds, ``faces`` names them by the way they face, or one by its
        whole name, and there is no :attr:`WallResult.perimeter` to crown,
        since an outline may be several.

        Refused (an ``NBTError``, before anything is planned): an east or
        west facade naming a corner other than the north and south walls',
        which own the corners and would leave it unbuilt; a ``parapet``
        without a ``deck``; a ``deck`` or a ``parapet`` with a ``roof``,
        which sits where they would; and every refusal :class:`Wall`,
        :class:`Facade` and :class:`Opening` make. Then as :meth:`wall`.
        Returns a :class:`WallResult`.
        """
        self._alive()
        plan = self._plan_shell(
            x0, z0, x1, z1, facade, floor_y=floor_y, top=top, stories=stories,
            faces=faces, thickness=thickness, openings=openings, ground=ground,
            floor=floor, deck=deck, parapet=parapet,
            parapet_height=parapet_height, roof=roof, attic=attic,
            hollow=hollow, voids=voids, footprint=footprint)
        self._vet_plan(plan.fills + [("set", cell, state)
                                     for cell, state in plan.planned],
                       what="shell")
        # A cut writes through whatever stands, so a flight standing inside
        # one is a fault before anything is read.
        faults = self._cut_claims(plan.cuts)
        already, taken, writes = self._settle(
            plan.planned, fabric=plan.fabric, opened=plan.opened,
            unglazed=plan.unglazed, overwrite=overwrite, noun="shell",
            cleared=[(c1, c2) for _, c1, c2, _ in plan.cuts])
        self._refuse_taken(
            faults + taken, what="shell",
            advice="Clear the site first, or pass overwrite=True to build "
                   "through whatever is there")
        ops = plan.fills + [("set", cell, state) for cell, state in writes]
        self._audit_plan(ops, what="shell")
        self._commit(ops, what="shell")
        result = WallResult(plan.walls, outline=plan.outline,
                            perimeter=plan.perimeter)
        result.blocks = len(plan.planned) + sum(
            (c2[0] - c1[0] + 1) * (c2[1] - c1[1] + 1) * (c2[2] - c1[2] + 1)
            for _, c1, c2, _ in plan.cuts)
        result.already = len(already)
        result.openings = len(plan.opened)
        result.kept_open = plan.kept_open
        return result

    def _cut_claims(self, cuts) -> list:
        """The cuts of a shell's plan that hold a flight's or an approach's
        claim, as faults: a cut writes through whatever stands there."""
        faults = []
        claimed = sorted(self._claimed)
        for kind, c1, c2, _ in cuts:
            caught = [cell for cell in claimed
                      if all(c1[i] <= cell[i] <= c2[i] for i in range(3))]
            if caught:
                who = self._claimant(caught[0])
                advice = ("build the shell before its flights"
                          if who == "a flight" else
                          "keep the shell off the approach and its head room")
                faults.append(
                    f"the {kind} {c1}..{c2} holds {len(caught)} cells {who} "
                    f"this session built claims, first {caught[0]}: {advice}")
        return faults

    def _plan_shell(self, x0, z0, x1, z1, facade, *, floor_y: int, top: int,
                    stories=(), faces=None, thickness: int = 1, openings=(),
                    ground=None, floor=None, deck=None, parapet=None,
                    parapet_height: int = 1, roof=None, attic=None,
                    hollow: bool = True, voids=(),
                    footprint=None) -> "_ShellPlan":
        """What :meth:`shell` will write, every refusal of its own made and
        nothing read, for it and for a writer that lays several as one call.
        """
        if not isinstance(facade, _FaceRule):
            raise NBTError(f"shell takes a Facade or an Elevation, got "
                           f"{type(facade).__name__}")
        if not isinstance(hollow, bool):
            raise NBTError(f"hollow is True or False, got {hollow!r}")
        if footprint is None:
            walls = Wall.ring(x0, z0, x1, z1, floor_y=floor_y, top=top,
                              thickness=thickness, stories=stories)
        else:
            if not isinstance(footprint, Footprint):
                raise NBTError(f"footprint is a Footprint, got "
                               f"{type(footprint).__name__}")
            if thickness != 1:
                raise NBTError(f"a footprint's walls are one block thick, got "
                               f"thickness={thickness}")
            walls = footprint.walls(floor_y=floor_y, top=top, stories=stories)
        north = next(iter(walls.values()))
        X0, X1 = sorted((x0, x1))
        Z0, Z1 = sorted((z0, z1))
        t = north.thickness
        faced = dict(faces or {})
        unknown = sorted(set(faced) - set(walls)
                         - ({"north", "east", "south", "west"}
                            if footprint is not None else set()))
        if unknown:
            raise NBTError(f"faces names the walls north, east, south and "
                           f"west, got {unknown}")
        for name, rule in faced.items():
            if not isinstance(rule, _FaceRule):
                raise NBTError(f"faces[{name!r}] is a Facade or an Elevation, "
                               f"got {rule!r}")
        rules = {name: faced.get(name, faced.get(name.split("-")[0], facade))
                 for name in walls}
        for side in ("east", "west") if footprint is None else ():
            named = rules[side].corner
            if named is None:
                continue
            for owner in ("north", "south"):
                built = rules[owner]._corner_value
                if named != built:
                    raise NBTError(
                        f"the {side} facade names the corner {named}, and "
                        f"the {owner} wall, which owns that corner, builds "
                        f"it {'of ' + str(built) if built is not None else 'in its courses'}: "
                        f"the {side} wall's corner would never be laid. Give "
                        f"both the same corner, or leave the {side} one out")
        ground = _material(ground, "ground")
        floor = _material(floor, "floor")
        deck = _material(deck, "deck")
        parapet = _material(parapet, "parapet")
        parapet_height = _whole(parapet_height, "parapet_height")
        if parapet_height < 1:
            raise NBTError(f"a parapet is at least one block high, got "
                           f"parapet_height={parapet_height}")
        if roof is not None:
            if not isinstance(roof, Roof):
                raise NBTError(f"roof is a Roof, got {type(roof).__name__}")
            if deck is not None or parapet is not None:
                raise NBTError("a roof is laid from top + 1, where a deck and "
                               "its parapet would stand: pass the roof, or "
                               "the deck")
        elif attic is not None:
            raise NBTError("attic is the space under a roof, and the shell "
                           "has none: pass roof=")
        if parapet is not None and deck is None:
            raise NBTError("a parapet stands on the rim of a deck, and the "
                           "shell has none: pass deck=")
        holes: dict = {name: [] for name in walls}
        for item in openings:
            face, opening, at, story = _opening_at(item, faced=True)
            if face not in walls:
                raise NBTError(f"an opening goes in the north, east, south "
                               f"or west wall, got {face!r}")
            holes[face].append((opening, at, story))

        floors = north.floors
        inside = (X0 + t, Z0 + t, X1 - t, Z1 - t)
        rooms = ([inside] if footprint is None
                 else Footprint.boxes(footprint.inner))
        # The two cuts, each a box: every story's inside, and the ground.
        cuts: list = []
        if hollow:
            for i, level in enumerate(floors):
                ceiling = floors[i + 1] if i + 1 < len(floors) else top + 1
                for a0, b0, a1, b1 in rooms:
                    cuts.append(("hollow", (a0, level + 1, b0),
                                 (a1, ceiling - 1, b1), _AIR))
        if ground is not None:
            whole = ([(X0, Z0, X1, Z1)] if footprint is None
                     else Footprint.boxes(footprint.columns))
            for a0, b0, a1, b1 in whole:
                cuts.append(("ground", (a0, floor_y, b0), (a1, floor_y, b1),
                             ground))
        fills = [("fill", c1, c2, block) for _, c1, c2, block in cuts]

        fabric: dict = {}
        opened: dict = {}
        unglazed: set = set()
        built: dict = {}
        for name, wall in walls.items():
            laid = rules[name].plan(wall)
            cut, open_ = self._cut(wall, holes[name], laid)
            # An elevation may reach past the wall's own line, so two walls
            # can meet in a cell: they meet in one state or not at all.
            for cell, state in laid.items():
                other = fabric.get(cell)
                if other is not None and not _same_crown(other, state):
                    raise NBTError(
                        f"the {name} wall puts {state} at {cell}, where "
                        f"another wall puts {other}: where two walls meet "
                        f"they have to agree")
            fabric.update(laid)
            opened.update(cut)
            unglazed |= open_
            built.update(laid)
            built.update(cut)
        pick = _Picks()
        planned = sorted(built.items())
        kept_open = 0
        open_columns = {(int(x), int(z)) for x, z in voids}
        if floor is not None:
            room = ([(x, z) for x in range(inside[0], inside[2] + 1)
                     for z in range(inside[1], inside[3] + 1)]
                    if footprint is None else sorted(footprint.inner))
            for level in north.stories:
                for x, z in room:
                    cell = (x, level, z)
                    if cell in self._claimed or (x, z) in open_columns:
                        kept_open += 1
                        continue
                    planned.append((cell, pick(floor, cell)))
        columns = ([(x, z) for x in range(X0, X1 + 1) for z in range(Z0, Z1 + 1)]
                   if footprint is None else sorted(footprint.columns))
        if deck is not None:
            planned += [((x, top + 1, z), pick(deck, (x, top + 1, z)))
                        for x, z in columns]
        perimeter = (Wall.perimeter(X0, Z0, X1, Z1) if footprint is None
                     else ())
        if parapet is not None:
            rim = ([(x, z) for x, z, _ in _path_roles(perimeter)]
                   if footprint is None else sorted(footprint.rim))
            for y in range(top + 2, top + 2 + parapet_height):
                planned += [((x, y, z), pick(parapet, (x, y, z)))
                            for x, z in rim]
        if roof is not None:
            planned += roof._layout((X0, Z0, X1, Z1) if footprint is None
                                    else columns, top + 1, attic,
                                    None).picked()

        return _ShellPlan(walls, (X0, Z0, X1, Z1), perimeter, cuts, fills,
                          fabric, opened, unglazed, planned, kept_open)

    def cut_opening(self, wall, opening, at: int, *, story: int = 0,
                    facade=None, overwrite: bool = False) -> list:
        """Cut ``opening`` in ``wall`` at column ``at`` on ``story``, and
        dress it -- its fill, springers, lintel and keystone -- through the
        wall standing there. Returns the cells written, sorted.

        What it cuts must be the wall: a cell holding what ``facade`` -- the
        rule the wall was built by -- puts there, any of the game's airs, or
        what the opening writes there already; an open cell with a door or
        a gate hung in it is the hole already there, and is left whatever
        ``overwrite`` says. Anything else -- water or a cobweb in the hole
        too -- stops the call by name, every cell in the faults, before a
        block is written; ``overwrite=True`` cuts through whatever stands,
        which is the call for a wall no facade built. A block in a
        cell a flight or an approach this session built claims is refused
        whatever ``overwrite`` says. Then the cut is audited
        (``Builder._audit_plan``) and refused if it would leave what the
        game will not keep, or a door shut further than it was: the wall a
        button or a torch hangs on, an iron door's opener cut away, a chest
        put under a block that conducts. A fault that stood before the cut
        is not the cut's. Refused before anything is read, as every writer
        is: a block the game lacks, a chunk not generated or not finished,
        a height past the world; and last, a worksite's guard.
        """
        self._alive()
        if not isinstance(wall, Wall):
            raise NBTError(f"cut_opening takes a Wall, got "
                           f"{type(wall).__name__}")
        if not isinstance(opening, Opening):
            raise NBTError(f"cut_opening takes an Opening, got "
                           f"{type(opening).__name__}")
        if facade is not None and not isinstance(facade, _FaceRule):
            raise NBTError(f"facade is the Facade or Elevation the wall was "
                           f"built by, got {type(facade).__name__}")
        planned = sorted(opening.cells(wall, at, story=story).items())
        self._vet_plan([("set", cell, state) for cell, state in planned],
                       what="cut_opening")
        fabric = facade.plan(wall) if facade is not None else {}
        already, faults, writes = self._settle(
            planned, fabric=fabric, opened=dict(planned),
            unglazed={cell for cell, state in planned if _empty(state)},
            overwrite=overwrite, noun="opening")
        self._refuse_taken(
            faults, what="opening",
            advice="Pass facade= the rule the wall was built by, or "
                   "overwrite=True to cut through whatever is there")
        ops = [("set", cell, state) for cell, state in writes]
        self._audit_plan(ops, what="cut_opening")
        self._commit(ops, what="cut_opening")
        return [cell for cell, _ in writes]


def _material(given, arg: str):
    """One of a shell's own courses -- its ground, floors, deck, parapet --
    as it is planned: named or None (none laid), never air."""
    block = _whole_block(given, arg, "shell", "a shell", none=True)
    if isinstance(block, BlockState) and _empty(block):
        raise NBTError(f"shell({arg}=) of {block.name} lays nothing: leave "
                       f"{arg} out")
    return block


def _opening_at(item, *, faced: bool) -> tuple:
    """One entry of a writer's ``openings``: ``(Opening, at)`` or
    ``(Opening, at, story)``, with a face first for a shell's."""
    shape = ("(face, Opening, at[, story])" if faced
             else "(Opening, at[, story])")
    try:
        parts = tuple(item)
    except TypeError:
        raise NBTError(f"an opening is {shape}, got {item!r}") from None
    lead = parts[:1] if faced else ()
    rest = parts[1:] if faced else parts
    if len(rest) not in (2, 3) or not isinstance(rest[0], Opening):
        raise NBTError(f"an opening is {shape}, got {item!r}")
    story = rest[2] if len(rest) == 3 else 0
    return (*lead, rest[0], _whole(rest[1], "an opening's at"),
            _whole(story, "an opening's story"))
