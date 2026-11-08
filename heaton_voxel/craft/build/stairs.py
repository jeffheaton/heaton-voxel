"""Stairs as rules.

:class:`StairRule`, :class:`SpiralStair`, :class:`SwitchbackStair` and
:class:`StraightStair`, their readers, and the writers that build them, with
:class:`Ladder`, the other way up a building.

Part of :mod:`heaton_voxel.craft.build`. The spiral, switchback and
flight writers make their refusals -- where a continuation starts, what is
in the way, head room -- with the helpers they share with the vault
writer, in ``_common``; a flight has no continuation to refuse.
"""

from __future__ import annotations

from ...blocks import BlockState
from ...nbt import NBTError
from .._plan import refuse_many
from ..errors import Refused
from ..geometry import CLOCKWISE, COMPASS, OPPOSITE
from ..light import (_transient_reason, durable_emission, light_dampening,
                     light_emission, plan_lights)
from ..style import Mix
from ..survey import reachable, StepSpace
from ._common import (
    _COMPASS,
    _continuation,
    _empty,
    _ENDS,
    _head_room,
    _HEADINGS,
    _occupancy,
    _passable,
    _RING,
    _STAIR_STATE,
    _state,
    _state_matches,
    AIR,
    HEAD_ROOM,
)


#: Compass order of a clockwise ascent seen from above (north up, east
#: right), from east; the other hand reads the same ring backwards. One
#: quarter turn per level is what makes a winding stair's period four.
_TURNING = {"clockwise": tuple(CLOCKWISE[(1 + k) % 4] for k in range(4)),
            "counterclockwise": tuple(CLOCKWISE[(1 - k) % 4] for k in range(4))}


def _by_level(cell) -> tuple[int, int, int]:
    """The order a winding or dogleg stair writes its ``(x, y, z)`` cells:
    level by level, then x, then z. First-write order is the section
    palette, so it is the chunk's bytes, and it never changes."""
    x, y, z = cell
    return (y, x, z)


def _shaft_reach(radius: int) -> int:
    """How far light must carry inside an open shaft: from a lamp in the
    wall to the far interior corner, in taxicab blocks. That is the
    straight-line arithmetic the lamp spacing is proposed from; whatever
    stands inside the shaft -- a newel the light has to go round -- is the
    game's flood's to find (:meth:`StairsMixin.shaft`)."""
    return 3 * int(radius) + 1


class SpiralResult:
    """What a :meth:`Builder.spiral_stair` wound up the shaft.

    ``already`` counts cells that held the right block before the call --
    on a continuation that is the newel post the shaft was built with, and
    a number close to the total means the stair was already there.
    """

    __slots__ = ("bottom", "top", "steps", "treads", "newel", "already",
                 "turn")

    def __init__(self, bottom: int, top: int, turn: str):
        self.bottom = bottom
        self.top = top
        self.turn = turn
        self.steps = 0
        self.treads = 0
        self.newel = 0
        self.already = 0

    @property
    def turns(self) -> float:
        """Full revolutions, at a quarter turn per level."""
        return self.steps / 4

    def __repr__(self) -> str:
        parts = [f"{self.steps} steps y={self.bottom}..{self.top}",
                 f"{self.turns:g} turns {self.turn}",
                 f"{self.treads} treads"]
        if self.newel:
            parts.append(f"{self.newel} newel")
        if self.already:
            parts.append(f"{self.already} already right")
        return f"<SpiralResult {', '.join(parts)}>"


class ShaftResult:
    """What a :meth:`Builder.shaft` sank: cells hollowed, wall, lamps."""

    __slots__ = ("bottom", "top", "cleared", "walls", "lights", "every",
                 "light_level")

    def __init__(self, bottom: int, top: int):
        self.bottom = bottom
        self.top = top
        self.cleared = 0
        self.walls = 0
        self.lights = 0
        self.every = 0
        self.light_level = 0

    def __repr__(self) -> str:
        parts = [f"y={self.bottom}..{self.top}", f"{self.cleared:,} cleared"]
        if self.walls:
            parts.append(f"{self.walls:,} wall")
        if self.lights:
            parts.append(f"{self.lights} lamps every {self.every}"
                         f" (light >= {self.light_level})")
        return f"<ShaftResult {', '.join(parts)}>"


class StairRule:
    """The read-then-verify half of a stair: replay before you write.

    Continuing a stair somebody already built means writing hundreds of
    blocks from a pattern inferred out of a handful, and a misread pattern
    prints itself all the way up the tower before anybody sees it. So a
    stair here is a *rule* rather than a pile of blocks, and every rule --
    :class:`SpiralStair`'s quarter turn per level, :class:`SwitchbackStair`'s
    two runs and a landing -- answers the same two questions. This class is
    those two questions.

    :meth:`level` is the rule at one level, as a map from column to the
    exact state that belongs in it. :meth:`check` replays that over a span
    of the world and reports what it finds in **two lists**, because
    conflating them is a mistake worth avoiding:

    * a *disagreement* is a cell the rule predicts and the world
      contradicts -- the stair itself is not what the rule says, and a
      build about to extend it must stop;
    * an *extra* is a block standing where the rule wants nothing, which
      on a bare shaft is leftovers worth knowing about and in a furnished
      building is the furniture.

    :meth:`verify` is both lists together, which is what a bare shaft
    wants. A subclass's ``read`` derives the rule from a handful of levels
    and then refuses unless the replay reproduces **every** level already
    standing; that refusal is the tool, and the writing is the easy part.

    A subclass supplies :meth:`level` and :attr:`columns` -- every column
    the rule can touch, gaps included, so a stray block *between* the steps
    is noticed too and not only the cells the rule names.
    """

    __slots__ = ("found", "unfinished", "extras")

    #: How this stair's shape says "nowhere in particular", for messages.
    _empty_hint = "in the shaft"

    def __init__(self):
        #: ``(low, high)`` of the run ``read`` lifted this from, else None.
        self.found = None
        #: Levels of that run whose blocks were never finished.
        self.unfinished = ()
        #: Complaints about blocks standing where the rule wants nothing.
        self.extras = ()

    # -- the rule ----------------------------------------------------------

    def level(self, y: int) -> dict[tuple[int, int], BlockState]:
        """Every block of one level of the stair, by column."""
        raise NotImplementedError

    @property
    def columns(self) -> tuple[tuple[int, int], ...]:
        """Every column the rule can touch, the empty ones included."""
        raise NotImplementedError

    # -- reading -----------------------------------------------------------

    def check(self, save, y0: int, y1: int, *, dimension: str | None = None,
              missing_ok: bool = False) -> tuple[list[str], list[str]]:
        """``(disagreements, extras)`` over levels ``y0..y1``.

        Complaints name the level, the column and both states, and compare
        **whole states**, properties included -- a stair that matches
        everywhere but ``shape`` is a different block in the palette and
        worth hearing about.

        ``missing_ok`` forgives air where the rule wants a block: an
        unfinished level is not a different pattern, and the top of a
        hand-built run is nearly always mid-level. Air is any of the
        game's airs, here and in the gaps: a stair wound up through an old
        cave or down a mineshaft has cave air between its steps, and that
        is nothing, not furniture.
        """
        y0, y1 = sorted((int(y0), int(y1)))
        columns = tuple(self.columns)
        wrong: list[str] = []
        extras: list[str] = []
        for y in range(y0, y1 + 1):
            want = self.level(y)
            for column in columns:
                found = save.get_block(column[0], y, column[1],
                                       dimension=dimension)
                if found is None:
                    wrong.append(
                        f"y={y} {column}: nothing to read -- the chunk is "
                        f"not generated, or y is outside the world")
                    continue
                expect = want.get(column)
                if expect is None:
                    if not _empty(found):
                        extras.append(
                            f"y={y} {column}: expected air "
                            f"{self._empty_hint}, found {found}")
                elif not _state_matches(found, expect):
                    if missing_ok and _empty(found):
                        continue
                    wrong.append(f"y={y} {column}: expected {expect}, "
                                 f"found {found}")
        return wrong, extras

    def verify(self, save, y0: int, y1: int, *, dimension: str | None = None,
               missing_ok: bool = False) -> list[str]:
        """Every level in ``y0..y1`` that is not what this rule says.

        The check to run *before* extending a stair somebody else built,
        and the reason a ``read`` can be trusted: a rule that cannot
        reproduce the levels already standing has no business writing the
        next few hundred. Both kinds of complaint at once -- use
        :meth:`check` where a block standing in the gaps is furniture
        rather than a fault.
        """
        wrong, extras = self.check(save, y0, y1, dimension=dimension,
                                   missing_ok=missing_ok)
        return wrong + extras

    # -- what the stair owns -----------------------------------------------

    def landing_cells(self, y: int) -> tuple[tuple[int, int], ...]:
        """The flat cells of a turn at ``y``; a rule with no turns has none."""
        return ()

    def _within(self, y: int) -> bool:
        span = getattr(self, "span", None)
        return span is None or span[0] <= y <= span[1]

    def claimed(self, y: int) -> set[tuple[int, int]]:
        """The columns the stair owns at one absolute level.

        Three things claim a cell: the tread or landing standing in it, the
        wood a tread above will sit on, and the head of whoever is on the
        treads below. Furniture that ignores the third looks right in every
        plan and clips your head on the way up, and a floor slab that
        ignores it seals the stairwell -- which is why a sweep laying a
        story at ``y`` must start at ``y - HEAD_ROOM``.

        **The head room is three treads, not two.** Standing squarely on a
        tread at ``u`` your head reaches ``u + 2.3``, which is two cells --
        but the game resolves horizontal movement before gravity, so for
        one tick as you step off that tread your feet are still at ``u``
        while your box already overlaps the next cell down the run, and
        your head is then in the *third* cell above that neighbor. Two
        cells of clearance is a stair you can climb and cannot descend,
        which no render and no forward-only walk check will tell you --
        :func:`~heaton_voxel.craft.survey.reachable`, which walks every
        move back again and reports what is ``stranded``, is the one that
        does.

        Clipped to :attr:`span` where that is known, so the bottom of a
        stair does not reserve headroom for treads below its first.
        """
        y = int(y)
        out: set[tuple[int, int]] = set()
        if self._within(y):
            out |= set(self.tread_cells(y)) | set(self.landing_cells(y))
        if self._within(y + 1):
            out |= set(self.support_cells(y))
        for below in range(y - HEAD_ROOM, y):
            if self._within(below):
                out |= set(self.tread_cells(below))
        return out

    def clear(self, y: int, columns) -> list[tuple[int, int]]:
        """Those of ``columns`` the stair does not claim at level ``y``.

        Sorted, because the order a sweep writes cells is the order of the
        section palette and so the chunk's bytes.
        """
        taken = self.claimed(y)
        return sorted(c for c in ((int(a), int(b)) for a, b in columns)
                      if c not in taken)

    def _replay(self, save, levels, *, dimension: str | None = None,
                fatal_extras: bool = True) -> None:
        """Refuse unless the rule reproduces every level already standing.

        The last step of every ``read``, and the only reason the rest of
        one is safe. Records the span it covered, what stood in the gaps,
        and which levels were left half-built.
        """
        wrong, extras = self.check(save, levels[0], levels[-1],
                                   dimension=dimension, missing_ok=True)
        if fatal_extras:
            wrong = wrong + extras
        if wrong:
            raise Refused(
                f"the rule read off this stair does not reproduce it -- "
                f"{len(wrong)} disagreements, first: {wrong[0]}. Refusing to "
                f"extend a rule that does not describe what is already there", faults=wrong)
        self.found = (levels[0], levels[-1])
        self.extras = tuple(extras)
        self.unfinished = tuple(
            y for y in levels
            if self.check(save, y, y, dimension=dimension)[0])


class SpiralStair(StairRule):
    """The geometry of a winding stair: one quarter turn per level.

    Every level is a 2x2 quadrant of the 3x3 shaft -- the newel post at the
    center, a stair in the corner you step off, treads on the other two --
    and the quadrant turns 90 degrees as you climb. So a winding stair of
    any height is one rule plus a *phase*: which of the four cells around
    the newel the step at a given ``y`` sits in. Keeping the rule and the
    phase apart from the blocks is what makes a stair continuable --
    :meth:`read` lifts both out of a stair somebody already built, and the
    same object then writes the rest of it, in the same hand, on the same
    phase, out of the same materials.

    The rule settles two things a hand-built spiral gets wrong. Every
    tread you land on coming down -- the diagonal beside each step -- has
    :data:`HEAD_ROOM` clear blocks above it, since its column is not
    filled again until four levels up; the step itself has two, and its
    third is the tread the stair turns onto three levels higher, which is
    no ceiling, because a step is entered level from the tread beside it
    and never from above. And every step lands on a tread the level below
    already put there, because one level's tread is the next one's
    footing -- which is why a run that stops mid-level (a stair placed,
    its treads not yet) is *unfinished* rather than a different pattern,
    and why :meth:`read` says so instead of refusing.

    ``center`` is the newel column, ``(x, z)`` or ``(x, y, z)``; ``turn``
    is ``"clockwise"`` or ``"counterclockwise"`` **as you ascend**; and
    ``start``/``start_y`` fix the phase -- at ``start_y`` the step stands
    in the ``start`` cell. Materials default to oak, and any properties
    given on ``stair`` survive (``facing`` is the rule's to set).
    """

    __slots__ = ("center", "turn", "stair", "tread", "newel", "_order")

    _empty_hint = "between the steps"

    def __init__(self, center, *, turn: str = "clockwise",
                 start: str = "east", start_y: int = 0,
                 stair="minecraft:oak_stairs",
                 tread="minecraft:oak_planks", newel=None):
        self.center = (int(center[0]), int(center[-1]))
        if turn not in _TURNING:
            raise NBTError(f"turn must be 'clockwise' or 'counterclockwise' "
                           f"(as you ascend), got {turn!r}")
        if start not in _RING:
            raise NBTError(f"start must name one of the four cells around "
                           f"the newel {tuple(_RING)}, got {start!r}")
        self.turn = turn
        ring = _TURNING[turn]
        offset = ring.index(start) - int(start_y)
        # _order[y % 4] is the cell the step at that level stands in.
        self._order = tuple(ring[(offset + j) % 4] for j in range(4))

        state = _state(stair)
        if not state.name.endswith("_stairs"):
            raise NBTError(
                f"{state.name} is not a stair block, so it has no facing or "
                f"half for the rule to set -- a winding stair is made of "
                f"stairs. Pass tread= to choose what the flat cells are")
        properties = dict(_STAIR_STATE)
        properties.update(state.properties)
        properties.pop("facing", None)
        self.stair = BlockState(state.name, properties)
        self.tread = _state(tread)
        self.newel = self.tread if newel is None else _state(newel)
        super().__init__()

    # -- geometry ----------------------------------------------------------

    def cell(self, y: int) -> tuple[int, int]:
        """The column the step at ``y`` stands in."""
        dx, dz = _RING[self._order[y % 4]]
        return (self.center[0] + dx, self.center[1] + dz)

    def facing(self, y: int) -> str:
        """Which way the step at ``y`` faces: the way you are heading, which
        is the cell you climb to next."""
        return self._order[(y + 1) % 4]

    def level(self, y: int) -> dict[tuple[int, int], BlockState]:
        """Every block of one level -- step, two treads, newel -- by column.

        The four keys are always distinct: the step and the cell you climb
        to next sit on different axes, their diagonal is neither, and none
        of the three is the newel.
        """
        here = _RING[self._order[y % 4]]
        nxt = _RING[self._order[(y + 1) % 4]]
        cells = {
            here: self.stair.with_properties(facing=self.facing(y)),
            (0, 0): self.newel,
            nxt: self.tread,
            (here[0] + nxt[0], here[1] + nxt[1]): self.tread,
        }
        cx, cz = self.center
        return {(cx + dx, cz + dz): state for (dx, dz), state in cells.items()}

    def walkway(self, y: int) -> tuple[tuple[int, int], tuple[int, int]]:
        """The two columns you actually stand on at ``y``: the step, and the
        diagonal tread beside it.

        The level's other tread is the *footing* of the step above, not a
        place to stand -- your feet would be inside the next stair. Two
        columns is why a winding stair walks as though it were two blocks
        wide however you count the quadrant, and they are the columns that
        need headroom.
        """
        here = _RING[self._order[y % 4]]
        nxt = _RING[self._order[(y + 1) % 4]]
        cx, cz = self.center
        return ((cx + here[0], cz + here[1]),
                (cx + here[0] + nxt[0], cz + here[1] + nxt[1]))

    def tread_cells(self, y: int) -> tuple[tuple[int, int], tuple[int, int]]:
        """The columns stood on at ``y``, which is what
        :meth:`~StairRule.claimed` reserves head room over: the
        :meth:`walkway`."""
        return self.walkway(y)

    def support_cells(self, y: int) -> tuple[tuple[int, int], tuple[int, int]]:
        """What the level above stands on: the tread the next step sits on,
        and the newel post under the next level's own.

        With these :meth:`~StairRule.claimed` is the whole shaft at every
        level -- every gap of the 3x3 is the head room of some tread within
        :data:`HEAD_ROOM` below it -- which is the honest answer: nothing
        but the stair belongs anywhere in a winding stair's shaft.
        """
        cx, cz = self.center
        return (self.cell(y + 1), (cx, cz))

    @property
    def columns(self) -> tuple[tuple[int, int], ...]:
        """The nine columns of shaft this stair needs, newel included."""
        cx, cz = self.center
        return tuple((cx + dx, cz + dz)
                     for dz in (-1, 0, 1) for dx in (-1, 0, 1))

    #: The shaft this stair winds up, under the name the rest of a build
    #: session calls it by.
    interior = columns

    # -- reading -----------------------------------------------------------

    @classmethod
    def read(cls, save, center, *, dimension: str | None = None,
             low: int | None = None, high: int | None = None,
             tread=None, newel=None) -> "SpiralStair":
        """Lift the stair already wound around a newel, so it can be continued.

        Finds the stairs in the ring around ``center``, derives which way
        they turn and on what phase, takes the materials off the blocks
        themselves, and then **replays the derived rule over every level it
        found**, refusing if the two disagree. That last step is the point:
        continuing a stair is writing hundreds of blocks from a rule read
        out of a handful, and a misread rule prints itself all the way up
        the tower before anybody sees it.

        The run must be contiguous -- a gap means a landing, a second stair
        or a mistake, and which segment you meant is yours to say with
        ``low``/``high``. An unfinished top level is expected, not an
        error; it lands in :attr:`unfinished` and the next
        :meth:`Builder.spiral_stair` completes it.

        ``tread``/``newel`` are only needed when the run is too unfinished
        to show them.
        """
        center = (int(center[0]), int(center[-1]))
        cx, cz = center
        dim = save.dimension(dimension)
        accessor = dim.blocks(cx >> 4, cz >> 4)
        if accessor is None:
            raise NBTError(f"no generated chunk at {center}; there is no "
                           f"stair there to read")
        span_low = accessor.min_y if low is None else int(low)
        span_high = accessor.max_y if high is None else int(high)

        found = save.find_blocks((cx - 1, span_low, cz - 1),
                                 (cx + 1, span_high, cz + 1), "_stairs",
                                 match="suffix", dimension=dimension)
        ring = {}
        for x, y, z, state in found.matches:
            offset = (x - cx, z - cz)
            if offset not in _RING.values():
                raise NBTError(
                    f"a stair at ({x}, {y}, {z}) sits in a corner of the "
                    f"shaft, where a winding stair never puts one. That is "
                    f"not this pattern; read a narrower span with low=/high=")
            if y in ring:
                raise NBTError(
                    f"two stairs share level y={y} ({ring[y][0]} and "
                    f"{(x, z)}); a winding stair has exactly one step per "
                    f"level")
            ring[y] = (offset, state)
        if not ring:
            raise NBTError(
                f"no stairs in the shaft around {center} between y={span_low} "
                f"and y={span_high}; there is nothing to continue. Build a "
                f"fresh SpiralStair({center}) instead")

        levels = sorted(ring)
        gaps = [(a, b) for a, b in zip(levels, levels[1:]) if b != a + 1]
        if gaps:
            a, b = gaps[0]
            raise NBTError(
                f"the stair around {center} is not one run: it stops at "
                f"y={a} and picks up again at y={b}. A gap is a landing, a "
                f"second stair or a mistake -- name the segment you mean "
                f"with low=/high=")
        if len(levels) < 2:
            raise NBTError(
                f"a single step at y={levels[0]} does not say which way the "
                f"stair turns. Build a fresh SpiralStair with turn= and "
                f"start= instead of reading one")

        # Which hand turns this ring? At most one can explain a pair --
        # the two hands send a cell to different neighbors -- so a hand
        # that explains every consecutive pair is the hand.
        hand = next(
            (turn for turn, order in _TURNING.items()
             if all(order[(order.index(_name(ring[y][0])) + 1) % 4]
                    == _name(ring[y + 1][0]) for y in levels[:-1])), None)
        if hand is None:
            raise NBTError(
                f"the steps around {center} do not turn a quarter per level "
                f"-- y={levels[0]} is in the {_name(ring[levels[0]][0])} "
                f"cell and y={levels[0] + 1} in the "
                f"{_name(ring[levels[0] + 1][0])} cell. This is some other "
                f"stair; SpiralStair does not describe it")

        states = {str(BlockState(s.name, {k: v for k, v in s.properties.items()
                                          if k != "facing"}))
                  for _, s in ring.values()}
        if len(states) > 1:
            raise NBTError(
                f"the steps are not all the same block: {sorted(states)}. A "
                f"stair read for continuing must be uniform, or the run "
                f"would change material halfway up")

        # The geometry does not depend on the materials, so settle the rule
        # first and then read the blocks off the cells it points at.
        spiral = cls(center, turn=hand, start=_name(ring[levels[0]][0]),
                     start_y=levels[0], stair=ring[levels[0]][1])
        spiral.tread, spiral.newel = _read_materials(
            save, spiral, levels, dimension, tread, newel)

        spiral._replay(save, levels, dimension=dimension)
        return spiral

    def __repr__(self) -> str:
        span = "" if self.found is None else f" y={self.found[0]}..{self.found[1]}"
        unfinished = (f" +{len(self.unfinished)} unfinished"
                      if self.unfinished else "")
        return (f"<SpiralStair {self.center} {self.turn} "
                f"{self.stair.name.rsplit(':', 1)[-1]}{span}{unfinished}>")


def _name(offset: tuple[int, int]) -> str:
    """The compass name of a ring offset."""
    for name, value in _RING.items():
        if value == offset:
            return name
    raise NBTError(f"{offset} is not a cell of the ring")


def _read_materials(save, spiral, levels, dimension, tread, newel):
    """The tread and newel blocks an existing run is made of.

    Read off the cells the geometry says are treads, ignoring air (cave
    air included) -- the top level of a hand-built run is usually
    unfinished, and its missing treads must not be mistaken for a second
    material.
    """
    seen = {"tread": set(), "newel": set()}
    for y in levels:
        step = spiral.cell(y)
        for column in spiral.level(y):
            if column == step:
                continue
            found = save.get_block(column[0], y, column[1], dimension=dimension)
            if found is not None and not _empty(found):
                seen["newel" if column == spiral.center else "tread"].add(
                    str(found))
    out = []
    for role, given in (("tread", tread), ("newel", newel)):
        if given is not None:
            out.append(_state(given))
            continue
        if len(seen[role]) > 1:
            raise NBTError(
                f"the {role} is not one material: {sorted(seen[role])}. Say "
                f"which with {role}=")
        if not seen[role]:
            raise NBTError(
                f"no {role} block anywhere in the run -- it is too "
                f"unfinished to read. Say which with {role}=")
        out.append(BlockState.parse(seen[role].pop()))
    return out[0], out[1]


class SwitchbackResult:
    """What a :meth:`Builder.switchback_stair` wrote: treads, wood, turns.

    ``already`` counts cells that held the right block before the call --
    on a continuation that is the seam where the new run meets the old,
    and a number close to the total means the flight was already there.
    """

    __slots__ = ("first", "last", "treads", "wood", "landings", "already")

    def __init__(self, first: int, last: int):
        self.first = first
        self.last = last
        self.treads = 0
        self.wood = 0
        self.landings = 0
        self.already = 0

    def __repr__(self) -> str:
        parts = [f"y={self.first}..{self.last}", f"{self.treads:,} treads",
                 f"{self.wood:,} wood"]
        if self.landings:
            parts.append(f"{self.landings} landings")
        if self.already:
            parts.append(f"{self.already} already right")
        return f"<SwitchbackResult {', '.join(parts)}>"


class SwitchbackStair(StairRule):
    """A dogleg stair: two runs side by side, walked in opposite directions.

    The rule is a rectangle and a period. The rectangle is what the treads
    sweep -- ``run`` rows long by twice ``width`` columns across -- and the
    two halves of it are the two flights: one walks the length of it, a
    landing turns you around, the other walks back beside it. So the period
    in ``y`` is ``2 * run``, a **turn happens twice a cycle**, and the pitch
    of the stories such a stair can serve is therefore ``run``, not the
    period. That last is the whole reason to keep the stair as a rule: the
    staircase decides where a floor can go, not the arithmetic.

    Every level is one pair (or ``width``-wide row) of stairs plus the flat
    block the *next* level's treads will sit on, and at a turn the flat
    block spreads over the whole landing. A tread with nothing under it is
    a beam, not a step, which is why the two are one rule and not two.

    :meth:`claimed` is the part no picture shows. Three things own a cell
    at a level -- the tread standing in it, the wood a tread above will sit
    on, and **the head of whoever is on the treads below** -- and the third
    is invisible in every floor plan and cutaway there is. It is what
    decides where a floor slab and any furniture may go, so a sweep laying
    a story has to start **three levels below** the slab: the arriving
    flight's last treads put a head into the story above.

    ``corner1``/``corner2`` are opposite corners of the tread rectangle,
    ``(x, z)`` or ``(x, y, z)``; ``axis`` is the one the runs walk along;
    ``start`` is the corner of that rectangle the **first** run sets off
    from, which fixes both which end it starts at and which half it uses;
    and ``start_y`` is the level of that first tread, which fixes the
    phase. ``landing`` is how many rows deep a turn is, counting the row
    the last tread of the run stands in. ``skip`` names columns the stair
    must leave alone -- a pillar standing in the footprint is not the
    stair's business, and pretending it is would have the rule demand wood
    where the building has stone.

    Materials default to oak; any properties given on ``stair`` survive
    (``facing`` is the rule's to set), and ``wood`` is both the stringer
    under a tread and what a landing is paved with.
    """

    __slots__ = ("axis", "run", "width", "landing", "start", "start_y",
                 "period", "stair", "wood", "skip", "span",
                 "_along_lo", "_along_hi", "_cross_lo", "_cross_hi",
                 "_dir", "_start_along", "_first_high")

    _empty_hint = "in the stairwell"

    def __init__(self, corner1, corner2, *, axis: str = "z",
                 start: str = "northeast", start_y: int = 0,
                 landing: int = 3, stair="minecraft:oak_stairs",
                 wood="minecraft:oak_planks", skip=(), span=None):
        super().__init__()
        if axis not in ("x", "z"):
            raise NBTError(f"a switchback stair runs along 'x' or 'z', "
                           f"got {axis!r}")
        self.axis = axis
        xs = sorted((int(corner1[0]), int(corner2[0])))
        zs = sorted((int(corner1[-1]), int(corner2[-1])))
        along, cross = (zs, xs) if axis == "z" else (xs, zs)
        self._along_lo, self._along_hi = along
        self._cross_lo, self._cross_hi = cross

        self.run = self._along_hi - self._along_lo + 1
        if self.run < 2:
            raise NBTError(
                f"a run of {self.run} row along {axis} is a step, not a "
                f"flight -- the rectangle is what the treads sweep, so it "
                f"must be at least two rows long")
        across = self._cross_hi - self._cross_lo + 1
        if across < 2 or across % 2:
            raise NBTError(
                f"the rectangle is {across} columns across, which cannot be "
                f"halved into two runs side by side. A switchback needs an "
                f"even width -- two of them wide is the usual answer")
        self.width = across // 2

        along_word, cross_word = _corner_words(start, axis)
        self.start = start
        self.start_y = int(start_y)
        # The first run sets off *from* that corner, so it walks away from
        # the end it names and sits in the half the other word names.
        self._dir = -_HEADINGS[along_word][1]
        self._start_along = (self._along_lo if _HEADINGS[along_word][1] < 0
                             else self._along_hi)
        self._first_high = _HEADINGS[cross_word][1] > 0
        self.period = 2 * self.run

        self.landing = int(landing)
        if self.landing < 1:
            raise NBTError(
                f"a turn needs at least the row its last tread stands in, "
                f"so landing must be 1 or more, got {landing}")

        state = _state(stair)
        if not state.name.endswith("_stairs"):
            raise NBTError(
                f"{state.name} is not a stair block, so it has no facing or "
                f"half for the rule to set -- the treads of a switchback are "
                f"stairs. Pass wood= to choose what the flat cells are")
        properties = dict(_STAIR_STATE)
        properties.update(state.properties)
        properties.pop("facing", None)
        self.stair = BlockState(state.name, properties)
        self.wood = _state(wood)
        self.skip = frozenset((int(c[0]), int(c[-1])) for c in skip)
        #: ``(low, high)`` the run actually occupies, when that is known.
        #: :meth:`claimed` clips its look-down to it, so the ground floor of
        #: a tower is not told to keep clear of treads that are not there.
        self.span = None if span is None else (int(span[0]), int(span[1]))

    # -- geometry ----------------------------------------------------------

    def _cell(self, along: int, cross: int) -> tuple[int, int]:
        return (cross, along) if self.axis == "z" else (along, cross)

    def phase(self, y: int) -> int:
        """Where ``y`` falls in the ``2 * run`` cycle."""
        return (int(y) - self.start_y) % self.period

    def _leg(self, y: int) -> tuple[int, int]:
        """``(which run, how many treads into it)`` at level ``y``."""
        k = self.phase(y)
        return (0, k) if k < self.run else (1, k - self.run)

    def _row(self, y: int) -> int:
        leg, j = self._leg(y)
        step = j if leg == 0 else self.run - 1 - j
        return self._start_along + step * self._dir

    def _columns_of(self, leg: int) -> range:
        high = self._first_high if leg == 0 else not self._first_high
        if high:
            return range(self._cross_hi - self.width + 1, self._cross_hi + 1)
        return range(self._cross_lo, self._cross_lo + self.width)

    def tread_cells(self, y: int) -> tuple[tuple[int, int], ...]:
        """The columns the treads of level ``y`` stand in."""
        leg, _ = self._leg(y)
        row = self._row(y)
        return tuple(sorted(
            c for c in (self._cell(row, cross)
                        for cross in self._columns_of(leg))
            if c not in self.skip))

    def tread_facing(self, y: int) -> str:
        """Which way the treads of level ``y`` face: the way you are walking."""
        leg, _ = self._leg(y)
        sign = self._dir if leg == 0 else -self._dir
        return _ENDS[self.axis][1] if sign > 0 else _ENDS[self.axis][0]

    def support_cells(self, y: int) -> tuple[tuple[int, int], ...]:
        """The flat block at level ``y`` that the tread above will sit on.

        One level's stringer is the next level's footing, which is why a
        run that stops mid-level -- treads placed, their wood not -- is
        *unfinished* rather than a different pattern.
        """
        return self.tread_cells(y + 1)

    def is_landing(self, y: int) -> bool:
        """Whether level ``y`` is a turn."""
        return self.phase(y) in (self.run - 1, self.period - 1)

    def landing_cells(self, y: int) -> tuple[tuple[int, int], ...]:
        """The flat cells of a turn, or ``()`` on a level that is not one.

        The full width of both runs, ``landing`` rows deep counting the row
        the last tread stands in, and it does *not* include that tread --
        the cells you turn around on, and nothing else.
        """
        k = self.phase(y)
        if k == self.run - 1:
            out = self._dir
        elif k == self.period - 1:
            out = -self._dir
        else:
            return ()
        end = self._row(y)
        treads = set(self.tread_cells(y))
        cells = (self._cell(end + i * out, cross)
                 for i in range(self.landing)
                 for cross in range(self._cross_lo, self._cross_hi + 1))
        return tuple(sorted(c for c in cells
                            if c not in treads and c not in self.skip))

    def landing_levels(self, y0: int, y1: int) -> tuple[int, ...]:
        """Every turn in ``y0..y1`` -- where a floor slab can land flush.

        A turn is a platform you can stand still on, so a story laid level
        with one is reachable without touching the stair at all. They come
        every ``run`` levels, not every period.
        """
        y0, y1 = sorted((int(y0), int(y1)))
        return tuple(y for y in range(y0, y1 + 1) if self.is_landing(y))

    @property
    def pitch(self) -> int:
        """Levels between one turn and the next: the story pitch this
        stair permits. A turn twice a cycle halves the period."""
        return self.run

    def level(self, y: int) -> dict[tuple[int, int], BlockState]:
        """Every block of one level -- treads, stringer, landing -- by column."""
        out: dict[tuple[int, int], BlockState] = {}
        for column in self.landing_cells(y):
            out[column] = self.wood
        for column in self.support_cells(y):
            out.setdefault(column, self.wood)
        facing = self.tread_facing(y)
        state = self.stair.with_properties(facing=facing)
        for column in self.tread_cells(y):
            out[column] = state
        return out

    def walkway(self, y: int) -> tuple[tuple[int, int], ...]:
        """The columns you actually stand on at ``y``, and so the ones that
        need headroom.

        The treads, plus a turn's landing -- minus the cells carrying the
        *next* level's blocks, which are the stair above you and not a
        place to put your feet.
        """
        under = set(self.support_cells(y))
        return tuple(sorted(
            (set(self.tread_cells(y)) | set(self.landing_cells(y))) - under))

    @property
    def columns(self) -> tuple[tuple[int, int], ...]:
        """Every column the stair can touch: the runs and both landings."""
        reach = self.landing - 1
        return tuple(sorted(
            c for c in (self._cell(along, cross)
                        for along in range(self._along_lo - reach,
                                           self._along_hi + reach + 1)
                        for cross in range(self._cross_lo, self._cross_hi + 1))
            if c not in self.skip))

    # -- reading -----------------------------------------------------------

    @classmethod
    def read(cls, save, corner1, corner2, *, dimension: str | None = None,
             low: int | None = None, high: int | None = None,
             axis: str | None = None, landing: int | None = None,
             wood=None, skip=()) -> "SwitchbackStair":
        """Lift the switchback already standing in a box, so it can be continued.

        ``corner1``/``corner2`` bound the search in ``x``/``z`` and may be
        generous -- the whole interior of a tower is a fine answer, since
        the stairs in it are what is being looked for. Finds them, checks
        that every level turns or advances the way a dogleg does, derives
        the run length, the phase, the depth of a landing and the materials
        off the blocks themselves, and then **replays the derived rule over
        every level it found**, refusing if the two disagree.

        Blocks standing in cells the rule leaves empty are *not* a refusal
        -- in a furnished building they are the furniture. They land in
        :attr:`extras`, and only a cell the rule predicts and the world
        contradicts stops the read.

        The run must be contiguous in ``y``: a gap is a landing floor, a
        second stair or a mistake, and which segment you meant is yours to
        say with ``low``/``high``. An unfinished top level is expected, not
        an error; it lands in :attr:`unfinished`.
        """
        x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
        z0, z1 = sorted((int(corner1[-1]), int(corner2[-1])))
        dim = save.dimension(dimension)
        accessor = dim.blocks(x0 >> 4, z0 >> 4)
        if accessor is None:
            raise NBTError(f"no generated chunk at ({x0}, {z0}); there is no "
                           f"stair there to read")
        span_low = accessor.min_y if low is None else int(low)
        span_high = accessor.max_y if high is None else int(high)
        skip = frozenset((int(c[0]), int(c[-1])) for c in skip)

        found = save.find_blocks((x0, span_low, z0), (x1, span_high, z1),
                                 "_stairs", match="suffix", dimension=dimension)
        by_y: dict[int, list[tuple[int, int, BlockState]]] = {}
        for x, y, z, state in found.matches:
            if (x, z) in skip:
                continue
            by_y.setdefault(y, []).append((x, z, state))
        if not by_y:
            raise NBTError(
                f"no stairs between y={span_low} and y={span_high} in "
                f"({x0}, {z0})..({x1}, {z1}); there is nothing to continue. "
                f"Build a fresh SwitchbackStair over that rectangle instead")

        levels = sorted(by_y)
        gaps = [(a, b) for a, b in zip(levels, levels[1:]) if b != a + 1]
        if gaps:
            a, b = gaps[0]
            raise NBTError(
                f"the stair is not one run: it stops at y={a} and picks up "
                f"again at y={b}. A gap is a landing floor, a second stair "
                f"or a mistake -- name the segment you mean with low=/high=")
        if len(levels) < 2:
            raise NBTError(
                f"a single level of treads at y={levels[0]} does not say "
                f"which way the stair runs. Build a fresh SwitchbackStair "
                f"instead of reading one")

        # A stair read out of a finished building competes with the
        # furniture, because a seat is a stair: a pair of them facing each
        # other across a lounge is not a flight, and a greedy read counts
        # the sitting room as more staircase. So the rule is derived only
        # from the levels that offer exactly one flight -- one contiguous
        # row, all of it facing one way -- and the replay judges the rest,
        # where an off-pattern stair is an extra and not a fault.
        clean: dict[int, tuple[str, str, int, tuple[int, ...]]] = {}
        for y in levels:
            ways = {s.properties.get("facing") for _, _, s in by_y[y]}
            if len(ways) != 1:
                continue
            way = ways.pop()
            if way not in _HEADINGS:
                continue
            here = [(x, z) for x, z, _ in by_y[y]]
            index = 1 if _HEADINGS[way][0] == "z" else 0
            rows = {c[index] for c in here}
            if len(rows) != 1:
                continue
            cross = tuple(sorted(c[1 - index] for c in here))
            if cross != tuple(range(cross[0], cross[0] + len(cross))):
                continue
            clean[y] = (_HEADINGS[way][0], way, rows.pop(), cross)
        if not clean:
            raise NBTError(
                f"no level between y={levels[0]} and y={levels[-1]} holds "
                f"one flight of stairs -- every one is a scatter facing "
                f"different ways or standing in different rows. That is "
                f"furniture, or some other stair. "
                f"{_bay_hint(by_y[levels[0]])}")

        # The axis and the width are decided by **vote across the run**,
        # not by unanimity: a level with one tread knocked out is one
        # damaged level, not a stair of a different width, and a rule that
        # let it redefine the flight would go on to describe nothing. The
        # replay below is what refuses -- damage shows up there, named and
        # located, instead of here as a shrug about widths.
        derived_axis = _vote(clean[y][0] for y in clean)
        if axis is not None and axis != derived_axis:
            raise NBTError(
                f"the treads face along {derived_axis}, not the axis={axis!r} "
                f"asked for -- one of the two is about a different stair")
        axis = derived_axis
        clean = {y: v for y, v in clean.items() if v[0] == axis}
        width = _vote(len(clean[y][3]) for y in clean)
        clean = {y: v for y, v in clean.items() if len(v[3]) == width}

        core = _longest_run(sorted(clean))
        unread = _unread_hint(core, levels)
        if len(core) < 2:
            raise NBTError(
                f"only y={core[0]} holds a flight this box can read "
                f"cleanly; one level does not say which way the stair runs "
                f"or how long its flights are.{unread} Widen the span with "
                f"low=/high=, or build a fresh SwitchbackStair instead of "
                f"reading one")
        facing = {y: clean[y][1] for y in core}
        rows = {y: clean[y][2] for y in core}
        columns = {y: clean[y][3] for y in core}

        turns = []
        for y in core[:-1]:
            if facing[y] != facing[y + 1]:
                if rows[y] != rows[y + 1] or columns[y] == columns[y + 1]:
                    raise NBTError(
                        f"the stair turns between y={y} and y={y + 1} "
                        f"without changing flight: a dogleg turns by keeping "
                        f"its row and stepping across to the other run")
                turns.append(y)
            else:
                want = rows[y] + _HEADINGS[facing[y]][1]
                if rows[y + 1] != want or columns[y] != columns[y + 1]:
                    raise NBTError(
                        f"y={y} and y={y + 1} face {facing[y]} but do not "
                        f"advance one row in it (row {rows[y]} then "
                        f"{rows[y + 1]}). This is some other stair; "
                        f"SwitchbackStair does not describe it")
        if not turns:
            raise NBTError(
                f"the stairs between y={core[0]} and y={core[-1]} never "
                f"turn -- that is one straight flight, and a switchback is "
                f"its turn.{unread} Read a taller span with low=/high=, or "
                f"build a fresh SwitchbackStair with the run length you "
                f"want")

        along_lo = min(rows[y] for y in core)
        along_hi = max(rows[y] for y in core)
        extent = along_hi - along_lo + 1
        if len(turns) >= 2:
            spacings = {b - a for a, b in zip(turns, turns[1:])}
            if len(spacings) != 1:
                raise NBTError(
                    f"the runs are not all the same length -- turns at "
                    f"{turns[:6]}. A switchback read for continuing must "
                    f"have one period, or the next flight lands nowhere")
            run = spacings.pop()
            if run != extent:
                raise NBTError(
                    f"the turns are {run} levels apart but the treads sweep "
                    f"{extent} rows; the run and the rectangle disagree")
        else:
            run = extent

        cross_all = sorted({c for y in core for c in columns[y]})
        if (len(cross_all) != 2 * width
                or cross_all != list(range(cross_all[0], cross_all[-1] + 1))):
            raise NBTError(
                f"the two flights do not sit side by side: {width} treads "
                f"wide but columns {cross_all} across. A switchback is two "
                f"runs of one width against each other")

        start_y = turns[0] + 1
        if rows[start_y] not in (along_lo, along_hi):
            raise NBTError(
                f"the flight starting at y={start_y} begins at row "
                f"{rows[start_y]}, which is neither end of "
                f"{along_lo}..{along_hi} -- the run is not the rectangle")
        along_word = (_ENDS[axis][0] if rows[start_y] == along_lo
                      else _ENDS[axis][1])
        cross_axis = "x" if axis == "z" else "z"
        high_half = columns[start_y][0] > cross_all[0]
        cross_word = _ENDS[cross_axis][1 if high_half else 0]
        north_south = along_word if axis == "z" else cross_word
        east_west = cross_word if axis == "z" else along_word
        start = north_south + east_west

        states = {str(BlockState(s.name, {k: v for k, v in s.properties.items()
                                          if k != "facing"}))
                  for y in core for _, _, s in by_y[y]}
        if len(states) > 1:
            raise NBTError(
                f"the treads are not all the same block: {sorted(states)}. A "
                f"stair read for continuing must be uniform, or the run "
                f"would change material halfway up")

        corner1 = (cross_all[0], along_lo) if axis == "z" else (along_lo, cross_all[0])
        corner2 = (cross_all[-1], along_hi) if axis == "z" else (along_hi, cross_all[-1])
        sample = by_y[core[0]][0][2]

        # The geometry does not depend on the materials, so settle the rule
        # first and then read the blocks off the cells it points at. The
        # landing needs the wood to measure itself against, so it comes last
        # and the rule is rebuilt around it.
        draft = cls(corner1, corner2, axis=axis, start=start, start_y=start_y,
                    landing=1, stair=sample, skip=skip)
        wood_state = _read_stringer(save, draft, core, dimension, wood,
                                    (x0, z0, x1, z1))
        if landing is None:
            landing = _read_landing(save, draft, core, dimension, wood_state,
                                    (x0, z0, x1, z1))

        stair = cls(corner1, corner2, axis=axis, start=start, start_y=start_y,
                    landing=int(landing), stair=sample, wood=wood_state,
                    skip=skip)
        stair._replay(save, levels, dimension=dimension, fatal_extras=False)
        stair.span = stair.found
        return stair

    def __repr__(self) -> str:
        span = "" if self.found is None else f" y={self.found[0]}..{self.found[1]}"
        unfinished = (f" +{len(self.unfinished)} unfinished"
                      if self.unfinished else "")
        return (f"<SwitchbackStair {self.axis} run={self.run} "
                f"{self.width} wide {self.stair.name.rsplit(':', 1)[-1]}"
                f"{span}{unfinished}>")


class FlightResult:
    """What a :meth:`Builder.flight` wrote: treads, the wood under them, the
    floor it opened, and the railing round the opening."""

    __slots__ = ("first", "last", "treads", "wood", "cut", "rails", "already")

    def __init__(self, first: int, last: int):
        self.first = first
        self.last = last
        self.treads = 0
        self.wood = 0
        self.cut = 0
        self.rails = 0
        self.already = 0

    def __repr__(self) -> str:
        parts = [f"y={self.first}..{self.last}", f"{self.treads} treads",
                 f"{self.wood} wood"]
        for label, count in (("cut from the floor", self.cut),
                             ("rails", self.rails),
                             ("already right", self.already)):
            if count:
                parts.append(f"{count} {label}")
        return f"<FlightResult {', '.join(parts)}>"


class StraightStair(StairRule):
    """A straight flight: one run of treads up a heading.

    The run geometry of :class:`SwitchbackStair`, without the turn: tread
    ``k`` stands ``k`` rows along ``heading`` from ``start`` and ``k`` levels
    up, every tread but the first sits on the ``wood`` the level below laid
    for it, and :meth:`~StairRule.claimed` gives the head room the treads
    below put into every level above them -- three, for the reason a
    switchback's are.

    ``start`` is ``(x, y, z)`` of the first tread; you stand on the floor
    under it, one level down, and climb toward ``heading``. ``rise`` is how
    many treads there are, so the flight joins the floor block at
    ``y - 1`` to the one at ``y + rise - 1``, which the last tread is flush
    with. ``width`` columns run side by side, the first at ``start`` and
    the rest to its right as you climb.
    """

    __slots__ = ("start", "heading", "rise", "width", "stair", "wood", "span",
                 "_ahead", "_right")

    _empty_hint = "in the flight"

    def __init__(self, start, heading: str, rise: int, *, width: int = 1,
                 stair="minecraft:oak_stairs", wood="minecraft:oak_planks"):
        super().__init__()
        if heading not in _HEADINGS:
            raise NBTError(f"heading must be one of {tuple(_HEADINGS)}, "
                           f"got {heading!r}")
        if int(rise) < 1 or int(width) < 1:
            raise NBTError("a flight needs at least one tread, one wide")
        state = _state(stair)
        if not state.name.endswith("_stairs"):
            raise NBTError(f"{state.name} is not a stair block; the treads of "
                           f"a flight are stairs, and wood= chooses what is "
                           f"under them")
        properties = dict(_STAIR_STATE)
        properties.update(state.properties)
        properties.pop("facing", None)
        self.start = (int(start[0]), int(start[1]), int(start[2]))
        self.heading = heading
        self.rise = int(rise)
        self.width = int(width)
        self.stair = BlockState(state.name, properties)
        self.wood = _state(wood)
        self._ahead = _COMPASS[heading]
        self._right = (-self._ahead[1], self._ahead[0])
        #: The levels the treads occupy, which clips what the stair claims.
        self.span = (self.start[1], self.start[1] + self.rise - 1)

    def _row(self, k: int) -> tuple[tuple[int, int], ...]:
        x, _, z = self.start
        (ax, az), (rx, rz) = self._ahead, self._right
        return tuple((x + k * ax + w * rx, z + k * az + w * rz)
                     for w in range(self.width))

    def tread_cells(self, y: int) -> tuple[tuple[int, int], ...]:
        """The columns the treads of level ``y`` stand in."""
        k = int(y) - self.start[1]
        return self._row(k) if 0 <= k < self.rise else ()

    def tread_facing(self, y: int) -> str:
        """The way you walk up: every tread faces ``heading``."""
        return self.heading

    def support_cells(self, y: int) -> tuple[tuple[int, int], ...]:
        """The wood at level ``y`` that the tread above will sit on."""
        k = int(y) - self.start[1]
        return self._row(k + 1) if 0 <= k < self.rise - 1 else ()

    def level(self, y: int) -> dict[tuple[int, int], BlockState]:
        out = {column: self.wood for column in self.support_cells(y)}
        tread = self.stair.with_properties(facing=self.heading)
        for column in self.tread_cells(y):
            out[column] = tread
        return out

    def walkway(self, y: int) -> tuple[tuple[int, int], ...]:
        """The columns you stand on at ``y``: its treads."""
        return self.tread_cells(y)

    @property
    def columns(self) -> tuple[tuple[int, int], ...]:
        return tuple(sorted(c for k in range(self.rise) for c in self._row(k)))

    def story_levels(self) -> tuple[int, int]:
        """The floor blocks the flight joins: under its first tread, and the
        one its last tread is flush with."""
        return self.start[1] - 1, self.start[1] + self.rise - 1

    def entry(self) -> tuple[tuple[int, int, int], ...]:
        """The floor cells you climb from, one row before the first tread."""
        low, _ = self.story_levels()
        return tuple((x, low, z) for x, z in self._row(-1))

    def exit(self) -> tuple[tuple[int, int, int], ...]:
        """The floor cells you step off onto, one row past the last tread."""
        _, high = self.story_levels()
        return tuple((x, high, z) for x, z in self._row(self.rise))

    def well_rail(self, floor_y: int) -> tuple[tuple[int, int], ...]:
        """Where a railing goes round the stairwell opening in the floor at
        ``floor_y``: every column beside the opening, except the ones you
        step off onto. Sorted, because write order reaches the bytes."""
        opening = self.claimed(floor_y) - set(self.tread_cells(floor_y))
        if not opening:
            return ()
        arrive = {(x, z) for x, _, z in self.exit()}
        edge = set()
        for x, z in opening:
            for dx, dz in _COMPASS.values():
                column = (x + dx, z + dz)
                if column not in opening and column not in arrive:
                    edge.add(column)
        edge -= set(self.columns)
        return tuple(sorted(edge))

    def __repr__(self) -> str:
        x, y, z = self.start
        return (f"<StraightStair ({x}, {y}, {z}) up {self.heading}, "
                f"{self.rise} treads, {self.width} wide>")


def _vote(values):
    """The commonest value, ties broken by taking the smallest.

    A stair is read off many levels, and a handful of them are always
    damaged, unfinished or furnished. Deciding by weight of evidence and
    then making the replay refuse is what keeps one bad level from
    redefining the whole flight.
    """
    counts: dict = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return min(counts, key=lambda value: (-counts[value], value))


def _longest_run(values) -> tuple[int, ...]:
    """The longest stretch of consecutive integers in ``values``, sorted."""
    best: tuple[int, ...] = ()
    current: list[int] = []
    for value in values:
        if current and value != current[-1] + 1:
            current = []
        current.append(value)
        if len(current) > len(best):
            best = tuple(current)
    return best


def _unread_hint(core: tuple[int, ...], levels) -> str:
    """What a short readable core usually means, when most of a box is not one.

    A box drawn round a whole room is mostly furniture, and furniture is
    mostly stairs. Saying how much of the span could not be read as a
    flight is the difference between "your stair is too short" -- which is
    what a bare complaint about the core sounds like -- and "you drew the
    box too wide", which is the truth nine times in ten.
    """
    skipped = len(levels) - len(core)
    if skipped < 2:
        return ""
    return (f" Only {len(core)} of the {len(levels)} levels holding stairs "
            f"here read as one flight; the other {skipped} hold stairs "
            f"facing different ways or standing in different rows, which "
            f"is what furniture looks like -- narrow the box to the stair "
            f"bay.")


def _bay_hint(here) -> str:
    """Where the stairs of one level actually are, and what that usually means.

    A read box drawn round a whole room catches the furniture: a seat is a
    stair, and a pair of them facing each other across a lounge is not a
    flight. Naming the columns is the difference between "this is not a
    switchback" and "you drew the box too wide".
    """
    cells = sorted((x, z) for x, z, _ in here)
    shown = ", ".join(str(c) for c in cells[:6])
    more = f" and {len(cells) - 6} more" if len(cells) > 6 else ""
    return (f"The stairs at that level are at {shown}{more} -- if some of "
            f"them are furniture, narrow the box to the stair bay")


def _corner_words(name: str, axis: str) -> tuple[str, str]:
    """``(the word along the axis, the word across it)`` of a corner name."""
    for north_south in ("north", "south"):
        if name.startswith(north_south):
            east_west = name[len(north_south):]
            if east_west in ("east", "west"):
                return ((north_south, east_west) if axis == "z"
                        else (east_west, north_south))
    raise NBTError(
        f"start names the corner the first flight sets off from, one of "
        f"('northeast', 'northwest', 'southeast', 'southwest'), got {name!r}")


def _read_stringer(save, draft, levels, dimension, given, box):
    """The flat block an existing switchback's treads sit on.

    Read off the cells the geometry says are stringer, ignoring air (cave
    air included) -- the top level of a hand-built run is usually
    unfinished, and its missing wood must not be mistaken for a second
    material. One block in several orientations is one material: see
    :func:`_state_matches`.
    """
    if given is not None:
        return _state(given)
    x0, z0, x1, z1 = box
    seen = set()
    for y in levels:
        for cx, cz in draft.support_cells(y):
            if not (x0 <= cx <= x1 and z0 <= cz <= z1):
                continue
            block = save.get_block(cx, y, cz, dimension=dimension)
            if block is not None and not _empty(block):
                seen.add(block)
    if not seen:
        raise NBTError(
            "nothing under any tread in the run -- it is too unfinished to "
            "read the stringer off. Say which with wood=")
    names = {state.name for state in seen}
    if len(names) > 1:
        raise NBTError(
            f"the block under the treads is not one material: "
            f"{sorted(names)}. Say which with wood=")
    if len(seen) > 1:
        # One block, several orientations: a hand-built flight lays its
        # logs whichever way the builder was facing, and that is not part
        # of the stair. Drop to the bare name so the rule stops policing a
        # property it never meant -- and writes the game's own default.
        return BlockState(names.pop())
    return seen.pop()


def _read_landing(save, draft, levels, dimension, wood, box):
    """How many rows deep the turns of an existing switchback are.

    Grown outward from the row the last tread of a run stands in, for as
    long as the whole width of the stair is that wood -- and never inward,
    so a story floor laid in the same material at the same level cannot
    swallow the measurement.
    """
    x0, z0, x1, z1 = box
    depths: dict[int, int] = {}
    for y in levels:
        if not draft.is_landing(y):
            continue
        out = draft._dir if draft.phase(y) == draft.run - 1 else -draft._dir
        end = draft._row(y)
        depth = 1
        while True:
            row = end + depth * out
            cells = [c for c in (draft._cell(row, cross) for cross in
                                 range(draft._cross_lo, draft._cross_hi + 1))
                     if c not in draft.skip]
            if not cells or any(not (x0 <= cx <= x1 and z0 <= cz <= z1)
                                for cx, cz in cells):
                break
            blocks = [save.get_block(cx, y, cz, dimension=dimension)
                      for cx, cz in cells]
            if any(b is None or not _state_matches(b, wood) for b in blocks):
                break
            depth += 1
        depths[y] = depth
    if not depths:
        raise NBTError(
            "no turn in the run to measure a landing from -- read a taller "
            "span, or say how deep the turns are with landing=")
    found = sorted(set(depths.values()))
    if len(found) > 1:
        where = {d: sorted(y for y, v in depths.items() if v == d)[:4]
                 for d in found}
        raise NBTError(
            f"the turns are not all the same depth: {where} (rows: the "
            f"levels measuring them). The usual cause is a box that stops "
            f"short of a landing, since the two turns of a switchback are "
            f"at opposite ends of the run -- widen it past both. Otherwise "
            f"one of them is a roof, a floor in the same wood or a mistake: "
            f"say which is meant with landing=")
    return found[0]


class Ladder:
    """A ladder up one column, named by the wall it hangs on. No world in it.

    A ladder hangs on the block opposite its ``facing``, so the state says
    the side you climb from and the sentence you would write says the wall,
    and a ladder written with the one read as the other faces into its wall
    and drops at the next update: all 337 of the Roman town's did. So
    ``on`` is the wall, ``"north"``, ``"east"``, ``"south"`` or ``"west"``,
    and the state follows, as a seat is named by its back.

    ``bottom`` and ``top`` are its first and last rows, inclusive, in either
    order, in the column ``(x, z)``. A body leaves the top by stepping onto
    a floor level with it or level with the row over it, so a ladder up to
    a floor at ``y`` has its top at ``y``, and a walk holds it to that
    (:func:`~heaton_voxel.craft.survey.reachable` climbs).

    Refused (an ``NBTError``): a side that is no compass word.
    """

    __slots__ = ("x", "z", "bottom", "top", "on")

    def __init__(self, x: int, z: int, bottom: int, top: int, *, on: str):
        if on not in COMPASS:
            raise NBTError(f"a ladder hangs on the north, east, south or "
                           f"west wall, got on={on!r}")
        self.x, self.z = int(x), int(z)
        self.bottom, self.top = sorted((int(bottom), int(top)))
        self.on = on

    @property
    def state(self) -> BlockState:
        """The block: ``facing`` is the way out from the wall."""
        return BlockState("minecraft:ladder", {"facing": OPPOSITE[self.on]})

    @property
    def column(self) -> tuple[int, int]:
        return (self.x, self.z)

    @property
    def wall(self) -> tuple[int, int]:
        """The column of the wall it hangs on."""
        dx, dz = COMPASS[self.on]
        return (self.x + dx, self.z + dz)

    def cells(self) -> dict:
        """Every rung, ``{(x, y, z): state}``, bottom up."""
        state = self.state
        return {(self.x, y, self.z): state
                for y in range(self.bottom, self.top + 1)}

    def verify(self, save, *, dimension: str | None = None) -> list[str]:
        """Every rung that is not as this rule says, in words."""
        wrong = []
        for cell, state in self.cells().items():
            found = save.get_block(*cell, dimension=dimension)
            if found is None or not _state_matches(found, state):
                wrong.append(f"{cell} holds {found}, where the ladder has "
                             f"{state}")
        return wrong

    def __repr__(self) -> str:
        return (f"Ladder({self.x}, {self.z}, {self.bottom}, {self.top}, "
                f"on={self.on!r})")


class StairsMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods: stairs as rules.

    Builder inherits them; nothing here is used on its own.
    """

    # -- vertical circulation ------------------------------------------------

    def shaft(self, center, y0, y1, *, radius: int = 1, wall=None,
              hollow: bool = True, light=None, light_side: str = "east",
              light_level=None, every: int | None = None) -> ShaftResult:
        """Sink a vertical shaft: hollow it, line it, light it.

        The room a :meth:`spiral_stair` goes in, and the reason one can be
        built through solid ground at all. ``radius`` is the interior half
        width -- ``1`` gives the 3x3 a winding stair needs -- ``wall``
        lines the ring one block outside it, and ``light`` runs a lamp
        column up the ``light_side`` wall, to ``light_level``: required
        with a light, as :meth:`pave`'s is -- a level 1 to 15, or a named
        one from :mod:`~heaton_voxel.craft.light`, ``NO_SPAWNS`` read for
        the Builder's dimension -- and needed by no unlit shaft.

        Lamp spacing is derived, not guessed, the way :meth:`light_cells`
        derives its lattice. A lamp in the wall is ``3 * radius + 1``
        taxicab blocks from the far interior corner, and light loses one
        level per block, so a source of strength ``L`` can stand
        ``2 * (L - reach - light_level) + 1`` levels from its neighbor and
        still hold the whole shaft at ``light_level``. A shaft too wide or a
        lamp too dim for the level asked even with a lamp at every level is
        a refusal, before a block is written. That spacing is where the
        game's flood (:func:`~heaton_voxel.craft.light.plan_lights`) looks
        first, over the shaft as it will stand -- every cell inside it that
        light can be in -- and it patches any shortfall with another lamp
        up the same wall: the ends of a run are where even spacing leaves a
        dark stretch, and a shaft left solid round a newel
        (``hollow=False``) is dark behind the newel, which light has to go
        round. Light already in the world is counted, so a level that is
        lit already gets no lamp. ``every`` sets the spacing instead, and
        then it is kept: a lamp every ``every`` levels up from the bottom,
        whatever the flood would have spared, and the flood patches only
        what those leave dark. A shaft its wall cannot light anyway is
        refused, naming the cells, before a block is written.

        The light is planned for the shaft this call leaves -- hollow, or
        solid round a newel -- and the level it reports is that shaft's.
        A stair built inside it afterwards stands in the light's way: its
        newel and treads leave cells behind them darker than the plan
        found them, monster-dark on a stair whose shaft was lit to
        ``NO_SPAWNS``.
        Check the finished stairwell with
        :func:`~heaton_voxel.craft.survey.spawnable`, and light what it
        names with :meth:`light_cells`.

        Carve **three levels past the top step** of any stair going inside
        -- :data:`HEAD_ROOM`, because whoever steps down onto the top tread
        has their head in the third cell above it for a tick. A shaft that
        stops level with its last step leaves that step with a ceiling on
        it; one that stops two past is a stair you can climb to a door
        above it and not come back down from; and a top-down render shows
        you neither. (Build it anyway and :meth:`spiral_stair` refuses by
        name, which is the same lesson learned later.)

        This **carves**: everything inside the shaft is replaced. That is
        the point when sinking a stairwell through rock, and the reason to
        run it under a :class:`~heaton_voxel.craft.worksite.Worksite`.

        ``wall`` is one block. A :class:`~heaton_voxel.craft.style.Mix` is a
        ``TypeError``, before anything else is asked: the light plan of a
        lit shaft reads its lining as the one block it is, and a lining
        that a lit shaft refused and an unlit one took would make the light
        decide what the walls are.
        """
        if isinstance(wall, Mix):
            raise TypeError(
                f"shaft(wall=) lines the shaft with one block, and {wall!r} "
                f"is a Mix, many blocks picked by position: the light plan "
                f"of a lit shaft reads its lining as one block, so a Mix "
                f"is refused lit or unlit; name the block")
        cx, cz = int(center[0]), int(center[-1])
        radius = int(radius)
        if radius < 1:
            raise NBTError(f"a shaft needs a radius of at least 1 (a 3x3 "
                           f"interior), got {radius}")
        if light is not None and light_side not in _RING:
            raise NBTError(f"light_side must name a wall {tuple(_RING)}, "
                           f"got {light_side!r}")
        lo, hi = sorted((int(y0), int(y1)))
        result = ShaftResult(lo, hi)

        lamps: list[int] = []
        # The levels a lamp goes in whatever the flood says: every= asks
        # for that spacing, where the derived one is only a proposal.
        fixed: frozenset = frozenset()
        emitted = 0
        if light is not None:
            light_level = self._level(light_level, "shaft")
            state = _state(light)
            name = state.name
            if light_emission(state) == 0:
                # Dark because the game has no such block is that refusal,
                # the call's, as a floor lamp's is (Builder._light_budget).
                if self._illegal(state) is not None:
                    self._vet_names((wall, state), what="shaft")
                raise NBTError(f"{state} gives off no light; use a light source")
            # Only the light that lasts holds a shaft lit.
            emitted = durable_emission(state)
            if emitted == 0:
                raise NBTError(f"{state} gives off light that does not last: "
                               f"{_transient_reason(state)}. Use a light "
                               f"source that keeps its light")
            if int(light_level) < 1:
                raise NBTError(f"light_level={light_level} asks for no light; "
                               f"a light level is 1 to 15")
            reach = _shaft_reach(radius)
            spare = emitted - reach - int(light_level)
            if spare < 0:
                reason = _transient_reason(state)
                kept = f" that lasts ({reason})" if reason else ""
                raise NBTError(
                    f"{name} emits light {emitted}{kept}, and a lamp in the "
                    f"wall of a radius-{radius} shaft is {reach} blocks from "
                    f"the far corner, so it cannot hold the shaft at "
                    f"{light_level} even set solid. Ask for less light, a "
                    f"brighter source, or a narrower shaft")
            step = max(1, 2 * spare + 1) if every is None else max(1, int(every))
            # The lattice, then each level the straight-line arithmetic says
            # falls dark at the top of the run: what the flood tries first.
            lamps = list(range(lo, hi + 1, step))
            if every is not None:
                fixed = frozenset(lamps)
            for y in range(lo, hi + 1):
                if emitted - reach - min(abs(y - lamp) for lamp in lamps) < light_level:
                    lamps.append(y)
            result.every = step
            result.light_level = int(light_level)

        lamp_column = None
        if light is not None:
            dx, dz = _RING[light_side]
            lamp_column = (cx + dx * (radius + 1), cz + dz * (radius + 1))

        def plan(lamp_levels) -> dict:
            """The shaft, ``{(y, x, z): block}``, with a lamp in the lamp
            column at each of ``lamp_levels``."""
            planned: dict[tuple[int, int, int], object] = {}
            for y in range(lo, hi + 1):
                for dz in range(-radius - 1, radius + 2):
                    for dx in range(-radius - 1, radius + 2):
                        x, z = cx + dx, cz + dz
                        edge = max(abs(dx), abs(dz)) == radius + 1
                        if edge and lamp_column == (x, z) and y in lamp_levels:
                            planned[(y, x, z)] = light
                        elif edge and wall is not None:
                            planned[(y, x, z)] = wall
                        elif not edge and hollow:
                            planned[(y, x, z)] = AIR
            return planned

        lamp_levels = fixed
        if light is not None:
            shell = plan(fixed)
            lx, lz = lamp_column
            # Its blocks by name first, as the plan's own check would take
            # them (Builder._commit): a block the game lacks is that refusal
            # even where a chunk is unfinished. Then every chunk and height
            # the shaft and its lamp column reach, before the plan reads the
            # light there.
            self._vet_names((wall, light), what="shaft")
            self._reach([("set", (x, y, z), block)
                         for (y, x, z), block in sorted(shell.items())]
                        + [("set", (lx, y, lz), light) for y in range(lo, hi + 1)],
                        what="shaft")
            # The cells to light: the inside of the shaft as it will stand,
            # wherever light can be in it at all.
            targets = []
            for y in range(lo, hi + 1):
                for dz in range(-radius, radius + 1):
                    for dx in range(-radius, radius + 1):
                        x, z = cx + dx, cz + dz
                        block = shell.get((y, x, z))
                        if block is None:
                            block = self._peek((x, y, z))
                            if block is None:
                                continue
                        if light_dampening(block) < 15:
                            targets.append((x, y, z))
            if targets:
                lit = plan_lights(
                    self.save, targets, light_level,
                    {(lx, y, lz): light for y in range(lo, hi + 1)
                     if y not in fixed},
                    lattice=[(lx, y, lz) for y in lamps if y not in fixed],
                    dimension=self.dimension,
                    overlay={(x, y, z): block
                             for (y, x, z), block in shell.items()})
                if lit.dark:
                    x, y, z, level = lit.dark[0]
                    raise Refused(
                        f"the shaft cannot be lit to {light_level} from its "
                        f"{light_side} wall: {len(lit.dark)} of "
                        f"{len(targets)} cells inside it stay under that "
                        f"wherever the lamps go, first ({x}, {y}, {z}) at "
                        f"{level}. Something in the shaft shades them from "
                        f"that wall; light it from another side, or ask for "
                        f"less light. Nothing was written", faults=lit.dark)
                lamp_levels = fixed | {key[1] for key in lit.lamps}

        # Planned first, written once, in one total order: first-write order
        # becomes the section palette and so the chunk's bytes.
        planned = plan(lamp_levels)
        for y, x, z in planned:
            if max(abs(x - cx), abs(z - cz)) <= radius:
                result.cleared += 1
            elif lamp_column == (x, z) and y in lamp_levels:
                result.lights += 1
            else:
                result.walls += 1

        self._commit([("set", (x, y, z), planned[(y, x, z)])
                      for y, x, z in sorted(planned)], what="shaft")
        return result

    def spiral_stair(self, spiral, y0, y1, *, newel: bool = True,
                     overwrite: bool = False,
                     headroom: bool = True) -> SpiralResult:
        """Wind a stair up a shaft, a quarter turn per level.

        ``spiral`` is a :class:`SpiralStair` -- read one out of the world
        with :meth:`SpiralStair.read` to continue a stair that already
        exists, or make a fresh one -- and a bare ``(x, z)`` is taken as
        the newel column of a default oak stair. ``y0`` and ``y1`` are the
        first and last level, inclusive, in either order; ``y0=None``
        continues a stair that was read, picking up one level past
        whichever end ``y1`` is heading for.

        Two refusals, both before a single block is written, because half a
        staircase is worse than none:

        * **Something is already in the shaft.** Anything that is not air
          and not already the block the rule wants stops the call by name;
          cave air is air, so a shaft through an old cave is a shaft.
          Carve first with :meth:`shaft`, or pass ``overwrite=True`` to cut
          through whatever is there.
        * **A step you cannot come back down.** Every column of
          :meth:`SpiralStair.walkway` needs :data:`HEAD_ROOM` clear blocks
          above it -- three, not the two a standing body occupies, because
          the third is where your head goes for the tick you spend stepping
          down onto a tread. Only empty cells count as clear, any of the
          game's airs: a spiral's gaps are part of its pattern, and
          :meth:`SpiralStair.read` refuses anything standing in them. The
          one block passed is the stair's own, the tread it turns onto
          three levels above each step, since a step is entered from the
          tread beside it and never from above. A stair that runs into its
          own ceiling looks perfectly good from above and in a top-down
          render, which is exactly why it is checked here rather than left
          to be discovered on foot.

        ``newel=False`` leaves the center column alone, for a shaft built
        with its post already in place. Run :meth:`finish` afterwards like
        any other build -- though a winding stair needs no corner shapes
        settled, its steps never touching one another.
        """
        if not isinstance(spiral, SpiralStair):
            spiral = SpiralStair(spiral)
        if y0 is None:
            y0 = _continuation(spiral, y1)
        lo, hi = sorted((int(y0), int(y1)))
        result = SpiralResult(lo, hi, spiral.turn)

        planned: dict[tuple[int, int, int], BlockState] = {}
        for y in range(lo, hi + 1):
            for (x, z), state in spiral.level(y).items():
                if (x, z) == spiral.center and not newel:
                    continue
                planned[(x, y, z)] = state
        order = sorted(planned, key=_by_level)
        # The plan's own checks before the shaft is read: a block the game
        # lacks, a level in a chunk with nothing to build into or past the
        # world, refuses the call as _commit would.
        self._vet_plan([("set", cell, planned[cell]) for cell in order],
                       what="spiral_stair")

        already, blocked, _ = _occupancy(
            self._read, ((cell, planned[cell]) for cell in order),
            empty=_empty, overwrite=overwrite)
        result.already += len(already)
        if blocked:
            raise Refused(
                f"{len(blocked)} cells the stair needs are already taken, "
                f"first: {blocked[0]}. Hollow the shaft first with "
                f"Builder.shaft, or pass overwrite=True to cut through "
                f"whatever is there", faults=blocked)

        if headroom:
            # Three above each walkway cell, strictly empty: the gaps are
            # the pattern. The step's own third is the tread the stair turns
            # onto three up -- planned here, or already standing over a
            # downward continuation -- and passes as the rule's own block.
            low_ceiling = _head_room(self._peek, spiral, range(lo, hi + 1),
                                     planned, passable=_empty, own=True)
            if low_ceiling:
                raise Refused(
                    f"{len(low_ceiling)} places where the stair has less "
                    f"than {HEAD_ROOM} blocks of headroom, first: "
                    f"{low_ceiling[0]}. Two is a stair you can climb and "
                    f"cannot descend, and it looks perfectly good from "
                    f"above; carve the shaft clear to three levels past the "
                    f"top step, or pass headroom=False if the ceiling is "
                    f"meant to be there", faults=low_ceiling)

        stair_name = spiral.stair.name
        ops: list[tuple] = []
        for cell in order:
            x, y, z = cell
            state = planned[cell]
            ops.append(("set", cell, state))
            if state.name == stair_name:
                result.steps += 1
            elif (x, z) == spiral.center:
                result.newel += 1
            else:
                result.treads += 1
        self._commit(ops, what="spiral_stair")
        return result

    def switchback_stair(self, stair, y0, y1, *, overwrite: bool = False,
                         headroom: bool = True) -> SwitchbackResult:
        """Run a dogleg stair up a box: two flights, turning at each end.

        ``stair`` is a :class:`SwitchbackStair` -- read one out of the
        world with :meth:`SwitchbackStair.read` to continue a flight that
        already exists, or make a fresh one over the rectangle its treads
        should sweep. ``y0`` and ``y1`` are the first and last level,
        inclusive, in either order; ``y0=None`` continues a stair that was
        read, picking up one level past whichever end ``y1`` is heading
        for, and backing over an unfinished tail so the join has no hole
        in it.

        Two refusals, both before a single block is written, because half
        a staircase is worse than none:

        * **Something is already in the way.** Anything that is not air
          (any of the game's airs, cave air included) and not already the
          block the rule wants stops the call by name.
          Pass ``overwrite=True`` to cut through it, or name the pillar in
          the rule's ``skip`` if it is meant to be there.
        * **A step you cannot stand up in.** Every column of
          :meth:`SwitchbackStair.walkway` needs :data:`HEAD_ROOM` clear
          blocks above it -- three, not the two a standing body occupies,
          because the third is where your head goes for the tick you spend
          stepping off a tread. Clearance for two is a stair you can climb
          and cannot descend, which looks perfectly good from above and in
          every cutaway. Clear is what a body passes, as the walk judges
          it: a torch or a sign on the stairwell wall is no obstruction.

        Run :meth:`finish` afterwards like any other build.
        """
        if not isinstance(stair, SwitchbackStair):
            raise NBTError(
                "switchback_stair takes a SwitchbackStair -- unlike a winding "
                "stair there is no single column to infer one from, since the "
                "rule is the rectangle its treads sweep")
        if y0 is None:
            y0 = _continuation(stair, y1)
        lo, hi = sorted((int(y0), int(y1)))
        result = SwitchbackResult(lo, hi)

        planned: dict[tuple[int, int, int], BlockState] = {}
        for y in range(lo, hi + 1):
            for (x, z), state in stair.level(y).items():
                planned[(x, y, z)] = state
        order = sorted(planned, key=_by_level)
        self._vet_plan([("set", cell, planned[cell]) for cell in order],
                       what="switchback_stair")

        already, blocked, _ = _occupancy(
            self._read, ((cell, planned[cell]) for cell in order),
            empty=_empty, overwrite=overwrite)
        result.already += len(already)
        if blocked:
            raise Refused(
                f"{len(blocked)} cells the stair needs are already taken, "
                f"first: {blocked[0]}. Clear them first, name a pillar in "
                f"skip= if it is meant to stand there, or pass "
                f"overwrite=True to cut through whatever is there", faults=blocked)

        if headroom:
            # The rule's own next level is not in its way, and a torch or a
            # sign on the wall is no obstruction: head room is what a body
            # can pass, as the walk judges it. The read tolerates extras, so
            # the build can too.
            low_ceiling = _head_room(self._peek, stair, range(lo, hi + 1),
                                     planned, passable=_passable)
            if low_ceiling:
                raise Refused(
                    f"{len(low_ceiling)} places where the stair has less "
                    f"than {HEAD_ROOM} blocks of headroom, first: "
                    f"{low_ceiling[0]}. Two is a stair you can climb and "
                    f"cannot descend, and it looks perfectly good from "
                    f"above; raise the ceiling, or pass headroom=False if "
                    f"it is meant to be there", faults=low_ceiling)

        stair_name = stair.stair.name
        ops: list[tuple] = []
        for cell in order:
            state = planned[cell]
            ops.append(("set", cell, state))
            if state.name == stair_name:
                result.treads += 1
            else:
                result.wood += 1
        self._commit(ops, what="switchback_stair")
        result.landings = sum(1 for y in range(lo, hi + 1) if stair.is_landing(y))
        span = stair.span or (lo, hi)
        stair.span = (min(span[0], lo), max(span[1], hi))
        return result

    def ladder(self, ladder, *, overwrite: bool = False) -> int:
        """Hang a :class:`Ladder`, every rung, and return how many it wrote.

        A rung already right is left as it stands. Anything else in a cell
        of the column stops the call by name, before a block is written,
        unless ``overwrite=True``; water too, since a ladder written into
        water is no ladder the game placed. A ladder with nothing to hang on
        is refused by the survival audit (``Builder._audit_plan``), so build
        its wall first. Refused before anything is read: a chunk not
        generated or not finished, or a height past the world.
        """
        if not isinstance(ladder, Ladder):
            raise NBTError(f"ladder takes a Ladder, got "
                           f"{type(ladder).__name__}")
        planned = sorted(ladder.cells().items(), key=lambda item: item[0][1])
        self._vet_plan([("set", cell, state) for cell, state in planned],
                       what="ladder")
        _, blocked, writes = _occupancy(self._read, planned, empty=_empty,
                                        overwrite=overwrite)
        if blocked:
            raise Refused(
                f"{len(blocked)} cells the ladder needs are already taken, "
                f"first: {blocked[0]}. Clear them first, or pass "
                f"overwrite=True to write through whatever is there",
                faults=blocked)
        ops = [("set", cell, ladder.state) for cell in writes]
        self._audit_plan(ops, what="ladder")
        self._commit(ops, what="ladder")
        return len(ops)

    def flight(self, stair, *, floor_y: int | None = None, rail=None,
               overwrite: bool = False, walk: bool = True) -> FlightResult:
        """Build a :class:`StraightStair`: the treads, the wood under them,
        and the hole in the floor above.

        ``floor_y`` is the level of the floor the flight climbs through --
        usually ``stair.story_levels()[1]`` -- and is the only plane cut:
        every block there that the stair claims is cut, which is the
        stairwell, sized by :meth:`~StairRule.claimed`, head room of three
        included. Anything else in the way is refused before a block is
        written, every fault listed:

        * a cell a tread or its wood must fill that holds something else
          (``overwrite=True`` cuts through it);
        * a step with less than :data:`HEAD_ROOM` clear above it -- two is a
          stair you can climb and not come back down. Clear is what a body
          passes, as the walk judges it, and the sky past the top of the
          world;
        * space an earlier flight from this Builder already claims, which
          is how two flights end up stacked in one column with no head
          room between them -- or an :meth:`approach` does: its surface and
          the head room over it.

        Then, unless ``walk=False``, the plan is walked both ways with
        :func:`~heaton_voxel.craft.survey.reachable` from the floor in
        front of the first tread to the floor past the last, and refused
        unless the round trip works -- so both floors must be there.
        ``rail`` names a block to set round the opening, one level above
        ``floor_y``, wherever the floor beside it stands -- one block, as
        the stair's own are: a :class:`~heaton_voxel.craft.style.Mix` is a
        ``TypeError`` before anything is read, whether or not any cell of
        the well turns out to want a rail.
        """
        if not isinstance(stair, StraightStair):
            raise NBTError("flight takes a StraightStair: StraightStair("
                           "(x, y, z), heading, rise)")
        # The rail is set only where the floor beside the well is found to
        # stand, so it is refused for what it is here, not by the read that
        # finds a cell for it -- which a well with no floor round it never
        # makes.
        refuse_many(rail)
        first, last = stair.span
        result = FlightResult(first, last)
        planned: dict[tuple[int, int, int], BlockState] = {}
        for y in range(first, last + 1):
            for (x, z), state in stair.level(y).items():
                planned[(x, y, z)] = state

        read = self._read
        order = sorted(planned)
        # Every cell the flight reads before it knows its plan -- the
        # treads and their wood, the stairwell, the floor round it and the
        # rail over that -- is checked as _commit will check the plan: a
        # block the game lacks first, then a chunk with nothing to build
        # into, or a height past the world.
        reads = [("set", cell, planned[cell]) for cell in order]
        if floor_y is not None:
            level = int(floor_y)
            reads += [("set", (x, level, z), AIR)
                      for x, z in sorted(stair.claimed(floor_y))]
            if rail is not None:
                for x, z in stair.well_rail(floor_y):
                    reads += [("set", (x, level, z), AIR),
                              ("set", (x, level + 1, z), rail)]
        self._vet_plan(reads, what="flight")
        # Any air is room to build in, and the floor plane is the stairwell
        # this call cuts, whatever stands in it.
        already, faults, into = _occupancy(
            read, ((cell, planned[cell]) for cell in order),
            empty=_empty, overwrite=overwrite,
            cut=lambda cell: cell[1] == floor_y, noun="flight")
        result.already += len(already)
        writes: dict[tuple[int, int, int], BlockState] = {
            cell: planned[cell] for cell in into}
        result.cut += sum(not _empty(found) for found in into.values())

        cuts = {}
        if floor_y is not None:
            for x, z in sorted(stair.claimed(floor_y)):
                cell = (x, int(floor_y), z)
                if cell not in planned and not _empty(read(cell)):
                    cuts[cell] = BlockState(AIR)
        result.cut += len(cuts)

        # A torch or a sign on the wall is no obstruction: head room is what
        # a body can pass, as the walk judges it. Past the top of the world
        # is none either: every tread under it was just read, so a cell
        # with nothing to read over one can only be the sky.
        faults += _head_room(
            self._peek, stair, range(first, last + 1),
            planned.keys() | cuts.keys(),
            passable=_passable,
            say=lambda step, block, cell: (
                f"the tread at {step} has {block} at {cell}: a step needs "
                f"{HEAD_ROOM} clear blocks above it, or you can climb it and "
                f"not come back"))

        claims = {(x, y, z) for y in range(first - 1, last + HEAD_ROOM + 1)
                  for x, z in stair.claimed(y)}
        taken = sorted(claims & self._claimed)
        if taken and taken[0] in self._approached:
            faults.append(f"{len(taken)} cells this flight needs are claimed "
                          f"by an approach this session built, first "
                          f"{taken[0]}: a flight in an approach's treads or "
                          f"head room leaves neither its head room")
        elif taken:
            faults.append(f"{len(taken)} cells this flight needs are claimed by "
                          f"an earlier one, first {taken[0]}: flights stacked "
                          f"in one column leave neither its head room")
        if faults:
            raise Refused(f"the flight cannot go in as planned -- {len(faults)} "
                          f"faults, first: {faults[0]}. Nothing was written",
                          faults=faults)

        overlay = {**writes, **cuts}
        rails: dict[tuple[int, int, int], BlockState] = {}
        if rail is not None and floor_y is not None:
            for x, z in stair.well_rail(floor_y):
                beside = read((x, int(floor_y), z))
                above = read((x, int(floor_y) + 1, z))
                if not _empty(beside) and _empty(above):
                    rails[(x, int(floor_y) + 1, z)] = _state(rail)
            overlay.update(rails)

        if walk:
            entry, exit_ = stair.entry()[0], stair.exit()[0]
            xs = [c[0] for c in (*stair.entry(), *stair.exit())]
            zs = [c[2] for c in (*stair.entry(), *stair.exit())]
            box = ((min(xs) - 1, first - 2, min(zs) - 1),
                   (max(xs) + 1, last + HEAD_ROOM + 1, max(zs) + 1))
            space = StepSpace(self.save, overlay=overlay, dimension=self.dimension)
            if space.stand_top(*entry) is None or space.stand_top(*exit_) is None:
                raise Refused(
                    f"the flight joins {entry} to {exit_}, and there is no "
                    f"floor to stand on at one of them; build both floors "
                    f"first, or pass walk=False", faults=[entry, exit_])
            reach = reachable(space, entry, *box)
            if exit_ not in reach.reached or exit_ in reach.stranded:
                raise Refused(
                    f"the planned flight does not walk both ways between "
                    f"{entry} and {exit_}: {reach!r}. Nothing was written",
                    faults=sorted(reach.stranded))

        ops: list[tuple] = []
        for cell in sorted(overlay):
            ops.append(("set", cell, overlay[cell]))
            if cell in rails:
                result.rails += 1
            elif cell in writes:
                if writes[cell].name == stair.stair.name:
                    result.treads += 1
                else:
                    result.wood += 1
        self._commit(ops, what="flight")
        self._claimed |= claims
        return result
