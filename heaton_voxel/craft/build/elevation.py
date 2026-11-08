"""Elevations: a wall's face as bands, each with its own rhythm across.

:class:`~heaton_voxel.craft.build.walls.Facade` is one rule for a whole
wall: a pier every ``pitch`` columns, a bay between them, the same from the
plinth to the head. That is most walls, and it is no tower's. A tower is
composed: its bays may run 7, 4, 4, 3, 4, 4, 7 across, which no congruence
says, and the ground story stands on a rhythm of its own that does not line
up with the piers above it. So an :class:`Elevation` stacks :class:`Band`
objects from the bottom up, each some rows, a :class:`Rhythm` across, and
what each role in it holds, and this is all it has: a plinth, a belt course,
a crown and a head course are bands like any other. It is a rule like a
Facade, laid by the same writers, and it replaces nothing.

    half = Rhythm("post", ("glass", 7)) + Rhythm("pier", ("bay", 4)) * 2 + "pier"
    across = half.mirrored(center=("bay", 3))

    Elevation(Band(5, ground, {...}),
              Band(1, ground, {...}),                  # a belt course
              Band(None, across, {"post": WOOL, "pier": BRICK,
                                  "bay": Glazing(GRAY, LIGHT, GRAY,
                                                 spandrel=BRICK),
                                  "glass": Glazing(GRAY, GRAY, LIGHT, LIGHT)}),
              Band(3, across, {...}))                  # the crown

**One band may stretch.** ``Band(None, ...)`` takes whatever rows the others
leave, so one rule builds five stories or fifty, its base and its crown
keeping their heights, and a wall with none to spare stands the crown on the
base. A :class:`Glazing` counts from each story's floor, so the rows it lays
follow the stories the wall has, however many.

**A rhythm covers its whole wall, corners included.** The columns run from
the wall's low end, west to east or north to south, whatever way it faces,
and a rhythm the wrong width is refused rather than cut or repeated, since a
bay that did not fit is a decision, not a remainder. :meth:`Wall.ring` gives
a rectangle's corners to its north and south walls, so those rhythms carry
the corner columns and the east and west ones run between them.

**A rule that fits any wall composes itself.** That refusal stands for a rule
that was drawn or read, and a building nobody has drawn asks another question:
:meth:`Rhythm.spaced` decides the bays for a width, and :class:`Fitted` is a
rule made for each wall it is laid over, so one style stands on any footprint.
:meth:`Elevation.recolored` is the same rule in other blocks.

**Up a bay, rows count from the story's floor.** A :class:`Glazing` is the
rows a bay holds up each story, repeating. With a ``spandrel`` the floor row
is that block and the rows begin over it, a punched window; without one the
rows run through the floor row too, a curtain wall.

**A face has depth, and a role says what stands in it.** :class:`Layers` is a
role's cells front to back, from the wall's line inward, so glass set back
behind its piers is ``Layers(AIR, GLASS)``, a pier two deep ``Layers(BRICK,
BRICK)`` and a fin before a jamb ``Layers(BLACK, BRICK)``. It says exactly
those layers, however thick the wall, so a wall one block thick can stand on
a ground story two deep: a role with no ``Layers`` is what fills the wall's
own thickness. Two walls that reach one cell that way have to agree on it.

**A door is a role.** :class:`Doors` hangs a door in each column of its run,
in the two rows over the floor, and every state of them follows from where
they are: they open inward through the wall, and they hinge at the run's
outer ends, a pair meeting in the middle. So a rule says its entrance whole,
and :meth:`Elevation.verify` holds it like any other cell.

**A rule can be read off a wall, and the replay is the reading.**
:meth:`Elevation.read` proposes the bands, the rhythms and the roles of a wall
that stands, and then replays them over it, refusing unless they reproduce it
cell for cell, as :meth:`~heaton_voxel.craft.build.stairs.SpiralStair.read`
and :meth:`~heaton_voxel.craft.build.vault.Vault.read` do: the writing is the
easy part. It reads a layer past the wall's own only in a row whose front
stands open, because behind a window is somebody's desk, and it lines rows up
by the wall's stories, which :func:`floors_behind` finds from what stands
behind the wall: a row of full cubes.

Part of :mod:`heaton_voxel.craft.build`. The writers are
:meth:`~heaton_voxel.craft.build.Builder.wall`,
:meth:`~heaton_voxel.craft.build.Builder.shell` and
:meth:`~heaton_voxel.craft.build.Builder.cut_opening`, which take an
Elevation wherever they take a Facade. No world in anything here.
"""

from __future__ import annotations

from fractions import Fraction

from ...block_defaults import BLOCK_DEFAULTS
from ...blocks import BlockState
from ...nbt import NBTError
from ..block_facts import facts
from ..errors import Refused
from ..style import Material, Mix
from ..survey import _POWER_DOORS
from ._common import _derived, _empty, _same_crown, _state, _whole
from .walls import _FaceRule, _Picks, _Recolor, _whole_block, Wall


def _run(item) -> tuple:
    """One run of a :class:`Rhythm`: a role name, one column of it, or
    ``(role, width)``."""
    role, width = (item, 1) if isinstance(item, str) else (None, None)
    if role is None:
        if not isinstance(item, tuple) or len(item) != 2:
            raise NBTError(f"a rhythm is made of role names and (role, width) "
                           f"pairs, got {item!r}")
        role, width = item
    if not isinstance(role, str) or not role:
        raise NBTError(f"a rhythm's role is a name, got {role!r}")
    width = _whole(width, "a run's width")
    if width < 1:
        raise NBTError(f"the run of {role!r} is at least one column wide, "
                       f"got {width}")
    return role, width


def _shares(total: int, count: int) -> list:
    """``total`` columns as ``count`` widths, mirrored, within a column of
    one another, the wider ones toward the middle. Even ``count`` with odd
    ``total`` has no such split, and its callers keep clear of it."""
    if not count:
        return []
    base, extra = divmod(total, count)
    widths = [base] * count
    high = count // 2
    low = high - 1 if count % 2 == 0 else high
    if low == high:
        if extra % 2:
            widths[high] += 1
            extra -= 1
        low, high = low - 1, high + 1
    while extra:
        widths[low] += 1
        widths[high] += 1
        extra -= 2
        low, high = low - 1, high + 1
    return widths


class Rhythm:
    """The columns across a wall, each a named role, from its low end. No
    world in it, and it never changes.

    ``Rhythm("post", ("glass", 7), "pier")`` is one column of ``post``, seven
    of ``glass`` and one of ``pier``: a run is a role name or ``(role,
    width)``, and another Rhythm goes in as its runs. Rhythms add, and a
    Rhythm plus a run is the same as a Rhythm of both; ``*`` repeats one,
    :meth:`reversed` turns it end for end and :meth:`mirrored` reflects it.
    Equal rhythms are the same rhythm: the same role in every column.

    :meth:`spaced` works one out from a width, for a wall nobody has drawn.

    Refused (an ``NBTError``): no columns at all, a role that is no name, a
    width under one.
    """

    __slots__ = ("columns",)

    def __init__(self, *runs):
        columns: list = []
        for run in runs:
            if isinstance(run, Rhythm):
                columns.extend(run.columns)
            else:
                role, width = _run(run)
                columns.extend([role] * width)
        if not columns:
            raise NBTError("a rhythm has at least one column")
        object.__setattr__(self, "columns", tuple(columns))

    def __setattr__(self, name, value):
        raise AttributeError("a Rhythm does not change: make another")

    def __len__(self) -> int:
        return len(self.columns)

    @property
    def roles(self) -> frozenset:
        """Every role a column of it has."""
        return frozenset(self.columns)

    @property
    def runs(self) -> tuple:
        """The rhythm as ``(role, width)`` runs, left to right."""
        runs: list = []
        for role in self.columns:
            if runs and runs[-1][0] == role:
                runs[-1][1] += 1
            else:
                runs.append([role, 1])
        return tuple((role, width) for role, width in runs)

    def __add__(self, other) -> "Rhythm":
        return Rhythm(self, other)

    def __mul__(self, times) -> "Rhythm":
        times = _whole(times, "a rhythm's repeat")
        if times < 1:
            raise NBTError(f"a rhythm repeats at least once, got {times}")
        return Rhythm(*[self] * times)

    __rmul__ = __mul__

    @property
    def symmetric(self) -> bool:
        """Whether it reads the same from both ends."""
        return self.columns == self.columns[::-1]

    def reversed(self) -> "Rhythm":
        """The same columns, end for end."""
        return Rhythm(*self.columns[::-1])

    def mirrored(self, center=None) -> "Rhythm":
        """This rhythm, then ``center`` if it is given -- a run or a Rhythm --
        then this one end for end: a wall the same from both ends."""
        return (Rhythm(self, self.reversed()) if center is None
                else Rhythm(self, center, self.reversed()))

    @classmethod
    def spaced(cls, width: int, *, pier: str = "pier", bay: str = "bay",
               target: int = 3, closed: bool = True, pier_width: int = 1,
               ends=None, center=None) -> "Rhythm":
        """Exactly ``width`` columns of bays between piers, the same from
        both ends: the rhythm a wall of that width asks for, decided here
        rather than cut to fit.

        ``target`` is the width a ``bay`` would like. The bays come as near
        it as the width lets, and differ from one another by a column at
        most, the wider ones toward the middle: a wall of 22 at the default
        comes to bays of 3, 3, 4, 3, 3. ``closed`` starts and ends the rhythm
        on a pier, for a wall that owns its corners
        (:attr:`Wall.ends`); open, it starts and ends on a bay, for one
        running between two that do. ``pier_width`` is how wide a pier is.

        ``ends`` is a :class:`Rhythm` at the low end and its reverse at the
        high end, a corner pavilion, say, and the bays share what is left.
        ``center`` makes the middle bay a role of its own, for an entrance:
        a role name, as wide as the bays beside it, or ``(role, width)``.

        Refused (an ``NBTError``): a width with no room for a bay, and a
        fixed ``center`` of the wrong parity for the width, which cannot
        stand mirrored on it."""
        width = _whole(width, "a rhythm's width")
        target = _whole(target, "target")
        pier_width = _whole(pier_width, "pier_width")
        if target < 1 or pier_width < 1:
            raise NBTError(f"target and pier_width are at least one column, "
                           f"got {target} and {pier_width}")
        pier, _ = _run(pier)
        bay, _ = _run(bay)
        if ends is not None and not isinstance(ends, Rhythm):
            raise NBTError(f"ends is a Rhythm, got {ends!r}")
        reach = 0 if ends is None else len(ends)
        role, wide = (None, 0) if center is None else (
            (center, 0) if isinstance(center, str) else _run(center))
        free = width - 2 * reach
        options = []
        for bays in range(1, free + 1):
            if center is not None and bays % 2 == 0:
                continue
            piers = bays + 1 if closed else bays - 1
            share = free - piers * pier_width - wide
            shared = bays - 1 if wide else bays
            if (share < shared or (not shared and share)
                    or (shared % 2 == 0 and share % 2)):
                continue
            miss = abs(Fraction(share, shared or 1) - target)
            options.append((miss, bays, share, shared))
        if not options:
            raise NBTError(
                f"{width} columns have no room for bays between piers "
                f"{pier_width} wide"
                + (f", with ends {reach} wide on each side" if reach else "")
                + (f" and {center!r} mirrored in the middle" if wide else ""))
        _, bays, share, shared = min(options)
        widths = _shares(share, shared)
        if wide:
            middle = len(widths) // 2
            widths[middle:middle] = [wide]
        runs = [] if ends is None else [ends]
        if closed:
            runs.append((pier, pier_width))
        for index, count in enumerate(widths):
            middle = role is not None and index == len(widths) // 2
            runs.append((role if middle else bay, count))
            if index < len(widths) - 1 or closed:
                runs.append((pier, pier_width))
        if ends is not None:
            runs.append(ends.reversed())
        return cls(*runs)

    def __eq__(self, other) -> bool:
        if not isinstance(other, Rhythm):
            return NotImplemented
        return self.columns == other.columns

    def __hash__(self) -> int:
        return hash((Rhythm, self.columns))

    def __copy__(self) -> "Rhythm":
        return self

    def __deepcopy__(self, memo) -> "Rhythm":
        return self

    def __reduce__(self):
        return Rhythm, tuple(self.columns)

    def __repr__(self) -> str:
        runs = (repr(role) if width == 1 else repr((role, width))
                for role, width in self.runs)
        return f"Rhythm({', '.join(runs)})"


class Glazing:
    """What a bay holds row by row, up each story, as a role of a
    :class:`Band`: the rows a window is made of, or anything else that
    repeats story by story. No world in it.

    The rows count from the floor of the story each one stands in
    (:meth:`Wall.story`), so they stay on the floors whatever the wall's
    story heights, and start again at each one. With a ``spandrel`` the
    floor row is that block and ``rows`` begin in the row over it, a window
    between floors; a ground story has no floor row in the wall, so it
    begins there too. Without one they begin in the floor row and run
    through it, a curtain wall hung past the floors. Either way they repeat
    in a story taller than they are. ``start`` is how far over the floor
    the first of them stands, where that is neither of those: a ground
    story's rows, say, read from the bottom with ``start=1``.

    Each row is a block, a :class:`~heaton_voxel.craft.style.Material` (its
    block) or a :class:`~heaton_voxel.craft.style.Mix`, picked at each world
    cell: named, never a palette's, as everywhere in a face. A pane written
    here has no sides until ``finish()`` joins it, as anywhere. Refused as
    the rule is made: no rows, a ``start`` under zero.
    """

    __slots__ = ("rows", "spandrel", "start")

    def __init__(self, *rows, spandrel=None, start=None):
        if not rows:
            raise NBTError("a glazing holds at least one row")
        self.rows = tuple(_whole_block(row, f"rows[{i}]", "Glazing", "a glazing")
                          for i, row in enumerate(rows))
        self.spandrel = _whole_block(spandrel, "spandrel", "Glazing",
                                     "a glazing", none=True)
        if start is None:
            start = 0 if self.spandrel is None else 1
        self.start = _whole(start, "start")
        if self.start < 0:
            raise NBTError(f"start is rows over the floor, got {start}")

    def _at(self, k: int):
        """The value ``k`` rows over a story's floor, 0 being the floor
        row."""
        if k == 0 and self.spandrel is not None:
            return self.spandrel
        return self.rows[(k - self.start) % len(self.rows)]

    def __repr__(self) -> str:
        parts = [repr(row) for row in self.rows]
        if self.spandrel is not None:
            parts.append(f"spandrel={self.spandrel!r}")
        if self.start != (0 if self.spandrel is None else 1):
            parts.append(f"start={self.start}")
        return f"Glazing({', '.join(parts)})"


class Doors:
    """A door hung in every column of a role's run, as a role of a
    :class:`Band`, or a layer of :class:`Layers`. No world in it.

    The door stands in the two rows over its story's floor, the lower half
    in the first, and the bands that hold this role have to be those rows:
    a door written anywhere else is refused when it is laid. Every other
    property is the wall's. It faces inward, as
    :meth:`~heaton_voxel.craft.build.walls.Wall.door` hangs one, and each
    hinges toward the nearer end of its run, the middle one of an odd run
    with the low end's, so a run opens from its center: a pair meets in the
    middle, as the game hangs one placed beside another.

    ``door`` is an id or a state naming nothing the wall decides. Refused
    as the rule is made (an ``NBTError``): what is no door, and an iron
    door, which nothing here can open: hang one with
    :meth:`~heaton_voxel.craft.build.Builder.doorway`, which gives it its
    buttons. A Mix or a Material is a ``TypeError``: a door is one block.
    """

    __slots__ = ("door",)

    def __init__(self, door):
        if isinstance(door, (Mix, Material)):
            raise TypeError(f"Doors(door=) is one door, and {door!r} is not: "
                            f"name the block")
        if not isinstance(door, (str, BlockState)):
            raise TypeError(f"Doors(door=) is a door, by id or state, not "
                            f"{door!r}")
        state = _state(door)
        if not state.name.endswith("_door"):
            raise NBTError(f"{state} is not a door")
        if state.name in _POWER_DOORS:
            raise NBTError(f"{state.name} opens only under power, and a rule "
                           f"gives it none: hang it with Builder.doorway, "
                           f"which gives it a button on each side")
        spoken = sorted(set(state.properties) & {"facing", "half", "hinge"})
        if spoken:
            raise NBTError(f"a door's {', '.join(spoken)} are the wall's to "
                           f"say, and {state} names them")
        self.door = state

    def _at(self, wall, k: int, i: int, n: int) -> BlockState:
        """The door ``k`` rows over a story's floor, in place ``i`` of a run
        of ``n``."""
        if k not in (1, 2):
            raise NBTError(f"a door stands in the two rows over its floor, "
                           f"and a band puts one {k} rows over it")
        low_end = i < (n + 1) // 2
        clockwise_is_low = wall.inward in ("south", "west")
        props = dict(BLOCK_DEFAULTS.get(self.door.name, {}))
        props.update(self.door.properties)
        props.update(facing=wall.inward, half="lower" if k == 1 else "upper",
                     hinge="right" if low_end == clockwise_is_low else "left")
        return BlockState(self.door.name, props)

    def __repr__(self) -> str:
        return f"Doors({str(self.door)!r})"


def _item(value, arg: str, owner: str, noun: str):
    """What a column holds at one depth, as a rule keeps it."""
    if isinstance(value, (Glazing, Doors)):
        return value
    return _whole_block(value, arg, owner, noun)


class Layers:
    """What a role holds at each depth: ``values`` front to back, the first
    in the wall's own line and each next one block inward. No world in it.

    Each is a block, a Material, a Mix, a :class:`Glazing` or :class:`Doors`,
    and air, by name, keeps a layer clear: ``Layers(AIR, GLASS)`` is glass
    set back behind its piers. It says exactly these layers whatever the
    wall's thickness, so it may reach past the wall's own into its floor
    plan, and a :meth:`~heaton_voxel.craft.build.Builder.shell` lays it
    after hollowing, over what the hollow clears; where a shell's floors
    run through such a cell the floor is what stands. Refused as the rule
    is made: no layers (an ``NBTError``), and ``None`` (a ``TypeError``):
    name what a layer holds.
    """

    __slots__ = ("values",)

    def __init__(self, *values):
        if not values:
            raise NBTError("Layers holds at least one layer")
        self.values = tuple(_item(value, f"values[{i}]", "Layers", "a layer")
                            for i, value in enumerate(values))

    def __repr__(self) -> str:
        return f"Layers({', '.join(repr(value) for value in self.values)})"


class Band:
    """Some rows of a wall: how many, the :class:`Rhythm` across them, and
    what each of its roles holds. No world in it.

    ``rows`` is a whole number, or ``None`` for a band that takes whatever
    rows an :class:`Elevation`'s others leave. ``roles`` maps every role of
    the rhythm to a block, a Material, a Mix, a :class:`Glazing`,
    :class:`Doors` or :class:`Layers`; a column with nothing in it holds
    air, by name, and the writers keep it clear.

    Refused as the rule is made: rows under one, a rhythm that is no
    :class:`Rhythm`, and a role the rhythm has and ``roles`` does not, or
    the other way about, either of which is one of them spelled wrong (an
    ``NBTError``); a material that is no block or is ``None`` (a
    ``TypeError``), as a Facade's is.
    """

    __slots__ = ("rows", "rhythm", "roles")

    def __init__(self, rows, rhythm, roles):
        if rows is not None:
            rows = _whole(rows, "a band's rows")
            if rows < 1:
                raise NBTError(f"a band is at least one row high, or None to "
                               f"take what the others leave, got {rows}")
        if not isinstance(rhythm, Rhythm):
            raise NBTError(f"a band's rhythm is a Rhythm, got {rhythm!r}")
        if not isinstance(roles, dict):
            raise NBTError(f"a band's roles is {{role: block}}, got {roles!r}")
        missing = sorted(rhythm.roles - set(roles))
        if missing:
            raise NBTError(f"the band's rhythm has the roles {missing}, and "
                           f"roles does not say what they hold")
        unused = sorted(str(role) for role in set(roles) - rhythm.roles)
        if unused:
            raise NBTError(f"roles names {unused}, which no column of the "
                           f"band's rhythm has")
        self.rows = rows
        self.rhythm = rhythm
        self.roles = {
            role: (value if isinstance(value, Layers) else
                   _item(value, f"roles[{role!r}]", "Band", "a band"))
            for role, value in sorted(roles.items())}

    def __repr__(self) -> str:
        rows = "the rest" if self.rows is None else self.rows
        return f"Band({rows} rows, {len(self.rhythm)} columns)"


class Elevation(_FaceRule):
    """A wall's face as :class:`Band` objects, bottom up: a rule like a
    :class:`~heaton_voxel.craft.build.walls.Facade`, given to the same
    writers, with no world in it.

    Every band is as wide as the wall, and between them they are as high:
    at most one stretches to make up the height. Each role is laid through
    every layer of the wall's thickness, as a Facade's courses are, but for
    a role that says its own with :class:`Layers`. A band that stretches may
    come to no rows at all, where the others take the whole wall. Refused as
    the rule is made: no bands, a thing that is no Band, and two bands that
    stretch. Refused when it is laid over a wall (:meth:`plan`, an
    ``NBTError`` naming both numbers): a band the wrong width, and bands
    that do not make the wall's height or pass it; and a door not in the
    two rows over its floor.
    """

    __slots__ = ("bands",)

    _noun = "elevation"

    def __init__(self, *bands):
        if not bands:
            raise NBTError("an elevation has at least one band")
        for band in bands:
            if not isinstance(band, Band):
                raise NBTError(f"an elevation is made of Bands, got {band!r}")
        if sum(1 for band in bands if band.rows is None) > 1:
            raise NBTError("one band may take what the others leave, and "
                           "two say rows=None: give all but one a height")
        self.bands = bands

    def _bands(self, wall) -> list:
        """``(bottom row, top row, band)``, bottom up, over ``wall``, whose
        columns and rows each band is held to."""
        if not isinstance(wall, Wall):
            raise NBTError(f"an elevation is laid over a Wall, got "
                           f"{type(wall).__name__}")
        columns = len(wall.along)
        for band in self.bands:
            if len(band.rhythm) != columns:
                raise NBTError(
                    f"a band's rhythm is {len(band.rhythm)} columns, and the "
                    f"wall runs {wall.low}..{wall.high}, {columns}: a rhythm "
                    f"covers its whole wall, and is never cut to fit")
        height = len(wall.rows)
        fixed = sum(band.rows or 0 for band in self.bands)
        stretching = any(band.rows is None for band in self.bands)
        if stretching and fixed > height:
            raise NBTError(f"the bands of set height are {fixed} rows, and "
                           f"the wall has {height}: it is too low for them")
        if not stretching and fixed != height:
            raise NBTError(f"the bands are {fixed} rows, and the wall has "
                           f"{height}: make them meet, or give one rows=None "
                           f"to take the rest")
        placed = []
        bottom = wall.floor_y + 1
        for band in self.bands:
            rows = height - fixed if band.rows is None else band.rows
            placed.append((bottom, bottom + rows - 1, band))
            bottom += rows
        return placed

    def role(self, wall, along: int, y: int) -> str:
        """The role of the column at ``along`` in the band at row ``y``, the
        way :meth:`Facade.kind` answers for a Facade: what to ask before
        hanging a door on a jamb."""
        along, y = _whole(along, "along"), _whole(y, "y")
        placed = self._bands(wall)
        if along not in wall.along:
            raise NBTError(f"column {along} is off the wall, which runs "
                           f"{wall.low}..{wall.high}")
        for bottom, top, band in placed:
            if bottom <= y <= top:
                return band.rhythm.columns[along - wall.low]
        raise NBTError(f"row {y} is off the wall, whose rows run "
                       f"{wall.rows[0]}..{wall.rows[-1]}")

    def plan(self, wall) -> dict:
        """Every cell of ``wall`` with the state the elevation puts there,
        sorted by ``(x, y, z)``: the replay. Pure: it reads no world, and a
        Mix is picked at each cell, so what is planned is what is written."""
        placed = self._bands(wall)
        pick = _Picks()
        cells: dict = {}
        for bottom, top, band in placed:
            places = [(i, width) for _, width in band.rhythm.runs
                      for i in range(width)]
            for y in range(bottom, top + 1):
                k = y - wall.floors[wall.story(y)]
                for a, role, (i, n) in zip(wall.along, band.rhythm.columns,
                                           places):
                    value = band.roles[role]
                    depth = (value.values if isinstance(value, Layers)
                             else (value,) * wall.thickness)
                    for layer, item in enumerate(depth):
                        if isinstance(item, Glazing):
                            item = item._at(k)
                        elif isinstance(item, Doors):
                            item = item._at(wall, k, i, n)
                        cell = wall.cell(a, y, layer)
                        cells[cell] = pick(item, cell)
        return dict(sorted(cells.items()))

    @classmethod
    def read(cls, save, wall, *, depth: int = 2, stretch: bool = True,
             dimension: str | None = None) -> "Elevation":
        """The rule of a wall that stands, read off it and replayed over it.

        Rows may share a band while no two at one height in their stories
        disagree, so a band is what repeats, and of the ways to cut the wall
        into such bands it takes the one that takes the fewest words to
        say, which is what stands a belt course apart from the story over
        it. Give ``wall`` its stories for it to find that:
        :func:`floors_behind` reads them, and a wall with none is one tall
        story, every row its own height, and comes back as many short bands.
        Columns holding the same in every row are a role, and the roles
        across are the band's rhythm. A role is a block, or a
        :class:`Glazing` where it changes up a story, with a ``spandrel``
        where its floor row is a block its rows are not. Its name says
        what it is, from the first thing in it: ``glass``, ``doors``,
        ``courses`` for rows of anything else, the block's id, or ``open``.

        ``depth`` is how many layers it may read, at the least the wall's
        thickness, and it reads past the wall's thickness only in a row
        with a column open through it, as far as those columns meet
        something: behind a pier or a window is the building, and a rule
        that took it would have the furniture in it. Every column of such a
        row is read that deep, as :class:`Layers`, and a row that reads to
        another depth is another band.

        A door hangs in a band of the rows that hold doors, as
        :class:`Doors` where they hang as Doors would, and as the states
        that stand where they do not: an iron door, or a door hinged some
        other way.

        ``stretch`` gives the longest band that repeats ``rows=None``, so
        what comes back stands on a wall of another height. The same wall
        replays the same either way.

        Returns the rule, whose :meth:`describe` says it in words. Refused:
        a cell with nothing to read (an ``NBTError``: never generated,
        cannot be read, or past the world), and a rule that does not
        reproduce the wall (:class:`~heaton_voxel.craft.errors.Refused`,
        every cell in its faults). It does not refuse a wall with no rule
        in it: a wall of random blocks comes back as many roles and many
        bands, which says so.
        """
        if not isinstance(wall, Wall):
            raise NBTError(f"read takes a Wall, got {type(wall).__name__}")
        depth = max(_whole(depth, "depth"), wall.thickness)
        reader = _Reader(save, wall, depth, dimension)
        spans = reader.spans()
        longest = None
        if stretch:
            repeats = [ys for ys in spans
                       if len({reader.k(y) for y in ys}) < len(ys)]
            longest = max(repeats, key=len, default=None)
        rule = cls(*(reader.band(ys, stretched=ys is longest) for ys in spans))
        wrong = rule.verify(save, wall, dimension=dimension)
        if wrong:
            raise Refused(f"the rule read off the wall does not reproduce it "
                          f"at {len(wrong)} cells, first: {wrong[0]}",
                          faults=wrong)
        return rule

    def _recolored(self, table):
        return Elevation(*(Band(band.rows, band.rhythm,
                                {role: _changed(value, table)
                                 for role, value in band.roles.items()})
                           for band in self.bands))

    def describe(self) -> str:
        """The rule in words, bottom band first: its rows, its rhythm, and
        what each role holds."""
        lines = []
        for band in self.bands:
            size = ("the rest of the wall" if band.rows is None else
                    "1 row" if band.rows == 1 else f"{band.rows} rows")
            shape = ", mirrored" if band.rhythm.symmetric else ""
            runs = ", ".join(f"{role} {width}"
                             for role, width in band.rhythm.runs)
            lines.append(f"{size}, {len(band.rhythm)} columns{shape}: {runs}")
            lines.extend(f"    {role}: {_said(value)}"
                         for role, value in band.roles.items())
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"Elevation({', '.join(repr(band) for band in self.bands)})"


class Fitted(_FaceRule):
    """A rule that composes itself for each wall it is laid over: ``make`` is
    called with the :class:`~heaton_voxel.craft.build.walls.Wall` and returns
    the :class:`Elevation` or :class:`~heaton_voxel.craft.build.walls.Facade`
    for it, so a style stands on any width, where an Elevation is drawn for
    one. No world in it.

    .. code-block:: python

        def face(wall):
            across = Rhythm.spaced(len(wall.along), closed=wall.ends)
            return Elevation(Band(None, across, {"pier": BRICK, "bay": GLASS}))

        Tier(0, 0, 30, 20, Fitted(face), floor_y=64, top=100)

    ``make`` is held to return a rule, and a wall it cannot fit is its own to
    refuse. Refused (an ``NBTError``): ``make`` not callable, and one that
    returns something other than a rule.
    """

    __slots__ = ("make",)

    _noun = "fitted rule"

    def __init__(self, make):
        if not callable(make):
            raise NBTError(f"Fitted takes a function of a wall, got {make!r}")
        self.make = make

    def rule(self, wall):
        """The rule ``make`` gives for ``wall``."""
        made = self.make(wall)
        if not isinstance(made, _FaceRule):
            raise NBTError(f"a fitted rule makes a Facade or an Elevation for "
                           f"its wall, and got {type(made).__name__}")
        return made

    def plan(self, wall) -> dict:
        return self.rule(wall).plan(wall)

    def role(self, wall, along: int, y: int) -> str:
        """The role of a column in the rule made for ``wall``: an
        Elevation's :meth:`Elevation.role`, or a Facade's kind of column."""
        made = self.rule(wall)
        return (made.role(wall, along, y) if isinstance(made, Elevation)
                else made.kind(wall, along))

    def describe(self, wall) -> str:
        """The rule made for ``wall``, in words."""
        return self.rule(wall).describe()

    def _recolored(self, table):
        # What it holds is not known until it has a wall, so a key that
        # meets nothing cannot be told yet from one that will.
        table.lazy = True
        mapping = dict(table.table)

        def make(wall):
            return self.rule(wall)._recolored(_quiet(mapping))
        return Fitted(make)

    def __repr__(self) -> str:
        return f"Fitted({getattr(self.make, '__name__', self.make)!r})"


def _quiet(mapping) -> _Recolor:
    """A table that says nothing of a key its one wall does not hold."""
    table = _Recolor(mapping)
    table.lazy = True
    return table


def _changed(value, table):
    """A role's value with ``table`` applied, however it is held."""
    if isinstance(value, Layers):
        return Layers(*(_changed(item, table) for item in value.values))
    if isinstance(value, Glazing):
        return Glazing(*(table(row) for row in value.rows),
                       spandrel=table(value.spandrel), start=value.start)
    if isinstance(value, Doors):
        return Doors(table(value.door))
    return table(value)


def _said(value) -> str:
    """A role's value in words, ids without their namespace."""
    if isinstance(value, Layers):
        return ", then ".join(_said(v) for v in value.values)
    if isinstance(value, Doors):
        return f"doors of {_said(value.door)}"
    if isinstance(value, Glazing):
        rows = ", ".join(_said(row) for row in value.rows)
        spandrel = value.spandrel
        over = f" over {_said(spandrel)}" if spandrel is not None else ""
        start = (f" from row {value.start}"
                 if value.start != (0 if spandrel is None else 1) else "")
        return f"rows of {rows}{over}{start}"
    return str(value).removeprefix("minecraft:")


def _plain(state: BlockState) -> BlockState:
    """A state found in a wall as a rule says it: what a block update
    derives left out, and what equals its block's default, so a joined pane
    is its bare id and two of them are one."""
    skip = _derived(state.name)
    defaults = BLOCK_DEFAULTS.get(state.name, {})
    return BlockState(state.name, {
        key: value for key, value in state.canonical().properties.items()
        if key not in skip and defaults.get(key) != value})


#: What a :class:`~heaton_voxel.craft.block_facts.Facts` says of a block
#: sturdy on all six faces.
_ALL_FACES = 63


def floors_behind(save, wall, *, share: float = 0.6,
                  dimension: str | None = None) -> tuple:
    """The floors of the building behind a wall, for ``Wall(stories=)``.

    A floor is a row of full cubes just inside the wall, where ``share`` of
    its columns are, and nothing else is: a pane is no cube and neither is
    a desk, while a run of such rows is a wall or a mass, not a floor. Only
    a row that could be a story's floor counts, two clear rows over the
    last and two under ``top``. It reads the layer behind the wall's own,
    so a recess in front of a slab does not hide it. Read-only.
    """
    if not isinstance(wall, Wall):
        raise NBTError(f"floors_behind takes a Wall, got {type(wall).__name__}")
    columns = list(wall.along)[1:-1] or list(wall.along)
    cubes = []
    for y in wall.rows:
        count = 0
        for a in columns:
            state = save.get_block(*wall.cell(a, y, wall.thickness),
                                   dimension=dimension)
            if (state is not None
                    and facts(state.name, state.properties).sturdy == _ALL_FACES):
                count += 1
        if count >= share * len(columns):
            cubes.append(y)
    floors: list = []
    last = wall.floor_y
    for y in cubes:
        if (y - 1 in cubes or y + 1 in cubes or y - last < 3
                or y > wall.top - 2):
            continue
        floors.append(y)
        last = y
    return tuple(floors)


class _Reader:
    """A wall's cells, read once, and the bands they make."""

    def __init__(self, save, wall, depth, dimension):
        self.save = save
        self.wall = wall
        self.depth = depth
        self.dimension = dimension
        self.found: dict = {}

    def cell(self, a, y, layer) -> BlockState:
        key = (a, y, layer)
        state = self.found.get(key)
        if state is None:
            cell = self.wall.cell(a, y, layer)
            state = self.save.get_block(*cell, dimension=self.dimension)
            if state is None:
                raise NBTError(f"nothing to read at {cell}: the chunk is not "
                               f"generated or cannot be read, or y is outside "
                               f"the world")
            state = self.found[key] = _plain(state)
        return state

    def k(self, y) -> int:
        """How far row ``y`` stands over its story's floor."""
        return y - self.wall.floors[self.wall.story(y)]

    def reach(self, y) -> int:
        """How many layers row ``y`` is read to: the wall's, or further
        where a column stands open through it, until they all meet
        something."""
        wall = self.wall
        opened = [a for a in wall.along
                  if all(_empty(self.cell(a, y, layer))
                         for layer in range(wall.thickness))]
        if not opened:
            return wall.thickness
        for layers in range(wall.thickness + 1, self.depth + 1):
            if all(any(not _empty(self.cell(a, y, layer))
                       for layer in range(layers)) for a in opened):
                return layers
        return self.depth

    def doored(self, y, reach) -> bool:
        return any(self.cell(a, y, layer).name.endswith("_door")
                   for a in self.wall.along for layer in range(reach))

    def spans(self) -> list:
        """The wall's rows as the bands that say them in the fewest words,
        bottom up. A band costs two, its height and its rhythm, and each
        kind of column in it one for each height it spans in a story. Rows
        share a band only if they read to one depth, hold doors alike, and
        agree at each height."""
        wall = self.wall
        rows = list(wall.rows)
        shapes = []
        for y in rows:
            reach = self.reach(y)
            shapes.append((reach, self.doored(y, reach)))
        best = [0] + [None] * len(rows)
        cut = [0] * (len(rows) + 1)
        for start in range(len(rows)):
            heights: dict = {}
            kinds = {a: 0 for a in wall.along}
            depths = set()
            reach = shapes[start][0]
            for end in range(start, len(rows)):
                if shapes[end] != shapes[start]:
                    break
                y, k = rows[end], self.k(rows[end])
                row = {(a, layer, k): self.cell(a, y, layer)
                       for a in wall.along for layer in range(reach)}
                if any(heights.get(key, state) != state
                       for key, state in row.items()):
                    break
                heights.update(row)
                depths.add(k)
                for a in wall.along:
                    kinds[a] = hash((kinds[a], *(row[a, layer, k]
                                                 for layer in range(reach))))
                cost = (best[start] + 2
                        + len(set(kinds.values())) * len(depths))
                if best[end + 1] is None or cost < best[end + 1]:
                    best[end + 1] = cost
                    cut[end + 1] = start
        spans = []
        end = len(rows)
        while end > 0:
            spans.append(rows[cut[end]:end])
            end = cut[end]
        return spans[::-1]

    def band(self, ys, *, stretched: bool) -> Band:
        """The band of rows ``ys``: as :class:`Doors` where those hang as
        Doors would, and as the states that stand where they do not."""
        reach = self.reach(ys[0])
        hung = self._band(ys, reach, stretched, doors=True)
        return hung if hung is not None else self._band(ys, reach, stretched,
                                                        doors=False)

    def _band(self, ys, reach, stretched, *, doors: bool):
        wall = self.wall

        def key(state):
            if doors and state.name.endswith("_door"):
                return ("door", state.name)
            return state

        columns = [tuple(tuple(key(self.cell(a, y, layer)) for y in ys)
                         for layer in range(reach)) for a in wall.along]
        roles: dict = {}
        names: dict = {}
        for column in columns:
            if column in names:
                continue
            value = self._value(column, ys, reach)
            if value is None:
                return None
            name = base = _role_name(value)
            count = 1
            while name in roles:
                count += 1
                name = f"{base}_{count}"
            names[column] = name
            roles[name] = value
        rhythm = Rhythm(*(names[column] for column in columns))
        band = Band(None if stretched else len(ys), rhythm, roles)
        return band if self._hung_true(band, ys, reach) else None

    def _value(self, column, ys, reach):
        """What a column holds, or None where it holds doors a band cannot
        say as :class:`Doors`."""
        layers = []
        for values in column:
            if any(isinstance(value, tuple) for value in values):
                if not all(isinstance(value, tuple) for value in values):
                    return None
                try:
                    layers.append(Doors(values[0][1]))
                except NBTError:
                    return None
            elif len(set(values)) == 1:
                layers.append(values[0])
            else:
                layers.append(_fitted({self.k(y): value
                                       for y, value in zip(ys, values)}))
        wall = self.wall
        if reach == wall.thickness and all(
                _same_value(value, layers[0]) for value in layers):
            return layers[0]
        return Layers(*layers)

    def _hung_true(self, band, ys, reach) -> bool:
        """Whether every door of ``band`` stands as :class:`Doors` hangs
        it."""
        wall = self.wall
        places = [(i, width) for _, width in band.rhythm.runs
                  for i in range(width)]
        for a, role, (i, n) in zip(wall.along, band.rhythm.columns, places):
            value = band.roles[role]
            depth = value.values if isinstance(value, Layers) else (value,)
            for layer, item in enumerate(depth):
                if not isinstance(item, Doors):
                    continue
                for y in ys:
                    if self.k(y) not in (1, 2) or not _same_crown(
                            self.cell(a, y, layer),
                            item._at(wall, self.k(y), i, n)):
                        return False
        return True


def _same_value(one, other) -> bool:
    if isinstance(one, BlockState) and isinstance(other, BlockState):
        return one == other
    return one is other


def _glass(state) -> bool:
    return state.name.endswith(("glass", "glass_pane"))


def _role_name(value) -> str:
    """What a role read off a wall is called, from the first thing in it:
    an open front says nothing of what stands behind it."""
    depth = value.values if isinstance(value, Layers) else (value,)
    first = next((v for v in depth
                  if not (isinstance(v, BlockState) and _empty(v))), None)
    if first is None:
        return "open"
    if isinstance(first, Doors):
        return "doors"
    if isinstance(first, Glazing):
        rows = (*first.rows, *((first.spandrel,) if first.spandrel else ()))
        return "glass" if any(_glass(row) for row in rows) else "courses"
    return "glass" if _glass(first) else first.name.split(":", 1)[-1]


def _fitted(rows: dict) -> Glazing:
    """The :class:`Glazing` of a column that changes up a story, ``{rows
    over the floor: state}``: the shortest that says it, read from its
    lowest row, and over a ``spandrel`` where the floor row holds what no
    other row does."""
    above = {k: state for k, state in rows.items() if k > 0}
    if 0 in rows and above and rows[0] not in above.values():
        return Glazing(*_cycle(above, min(above)), spandrel=rows[0],
                       start=min(above))
    return Glazing(*_cycle(rows, min(rows)), start=min(rows))


def _cycle(rows: dict, start: int) -> list:
    """The shortest repeat from row ``start`` that holds ``rows[k]`` at
    ``(k - start) % n``."""
    for n in range(1, max(rows) - start + 2):
        cycle: dict = {}
        if all(cycle.setdefault((k - start) % n, state) == state
               for k, state in rows.items()):
            first = next(iter(cycle.values()))
            return [cycle.get(i, first) for i in range(n)]
    raise AssertionError("one row a height always fits")
