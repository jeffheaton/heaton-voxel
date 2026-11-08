"""Extrusions: a cross-section swept along a line, following the ground.

:meth:`ExtrudeMixin.extrude` sweeps a :class:`CrossSection` -- the blocks
of a curtain wall, a hedge, a levee or an aqueduct's deck, drawn once as
they stand across one cell of the run -- along a line of ``(x, z)``
waypoints, at a height that follows the ground under it, at one level, or
on a profile the caller gives: a wall beside a road at the road's own
heights, a deck carried level over a valley on piers.

**A section is drawn once, facing south.** Its cells are ``(across, up)``.
``up`` counts from the profile's height, which is the block stood on, so
``up=0`` is the ground course; ``across`` is signed, + to the right of
travel -- the side :meth:`~heaton_voxel.craft.build.Builder.line_lamps`
calls right, and the inside of a circuit walked clockwise seen from
above. Its states are drawn as they stand on a run heading south, and each
run turns them by its own heading through a
:class:`~heaton_voxel.craft.geometry.Frame`, the properties a state leaves
at their defaults included, as :meth:`~heaton_voxel.craft.build.Builder.framed`
turns them: a log drawn ``axis=z`` lies along every run and one drawn
``axis=x`` across it, which is "logs laid by axis", and a stair drawn
facing back up the run faces back up every run. A
:class:`~heaton_voxel.craft.style.Mix` is picked at the world cell and the
pick then turned.

**Each across value is its own line.** The waypoints are moved along their
normals -- mitered exactly at a right-angle turn, so a corner is a square
one -- and each such line is rasterized by
:func:`~heaton_voxel.craft.geometry.line`, road's rule, which rounds
differences only: a line along a road's waypoints, moved across, is that
road's line moved across. A run may bend so long as it keeps its heading
-- every segment of it nearest one compass word -- since then every line
of it is the centerline shifted across, and its cells stack edge to edge.
It turns only at a right angle between runs straight along x and z, and a
closed path is a rectangle. A turn beside a diagonal folds the band over
itself or leaves a hole in it nearly every time (99.7% of the shapes tried
in the design), so it is refused, as are a turn back on itself, a run too
short for the turn inside it, and a band that comes back over itself.

**The profile.** Each cell of the band takes the height of a centerline
cell: the one with the same coordinate along its run, clamped to the run's
ends, so a corner's miter takes the corner's. ``follow="terrain"`` takes
the highest ground under the section at each centerline cell, smooths it
by :func:`~heaton_voxel.craft.geometry.smoothed_profile` (road's window
and its one-block clamp by default), and then levels every turn over the
section's reach either side (:func:`_level_turns`): the inner line of a
corner skips centerline cells, twice its distance from the centerline, and
a profile that climbs a block a cell through the corner would put a cliff
of ``2 * reach + 1`` blocks between two cells side by side on its inner
face -- seven at reach three. Leveled, no two cells side by side differ by
more than ``max_step``.
``follow=y`` lays it level, and a sequence is the caller's, one height per
centerline cell, held level through every turn.

**Steps are whole blocks.** Both terrain-following walls built so far --
the JeffWorld Roman walls (``tools/roman_city/jw_groundwork.py``) and the
secret lab's curtain wall (``tools/secret_lab/m1_groundwork.py``) -- step
a whole block from the same clamp, with a lip beside the walk, the Roman
brick slab and the lab's slate slab; dressing a rise with a slab only
halves it where it stands alone, and on a slope of a block a cell each
slab is still a block over the last. A section with a ``walk`` is walked
both ways (:func:`~heaton_voxel.craft.survey.reachable`) before anything
is written, on the wall itself: a rise it cannot walk is refused, and one
that needs a jump is reported, in the result's ``reach.jumped``.

**The ground is read, never assumed.** The lab's wall took the plane, 66,
for any column it could not read (``except Exception: g = P.PLANE``), and
planned a wall at 73 over ground nobody had read; the Roman wall's
``smoothed_line`` raised at the first missing column, after its script's
earlier stages had already written. Every column is read before anything
is planned, and every one with no ground is named in one
:class:`~heaton_voxel.craft.errors.NoSurface`.

**A battlement that follows the ground is a section.** A
:class:`Merlon` cell is a tooth or a gap, counted per run from that run's
own corners by :func:`~heaton_voxel.craft.build.merlon_offsets` -- the rule
:meth:`~heaton_voxel.craft.build.Builder.battlement` counts by, so on level
ground a section of a course and a merlon lays what ``battlement`` lays,
cell for cell, and on a slope each tooth stands on the course where the
course is.

**A channel is laid level, and its ends are the caller's.** A section may
hold water -- a canal, an aqueduct's trough, both aqueducts the Roman
scripts built a block of water between two of sandstone -- and water lies
level, so a section holding it follows no terrain and no profile that
steps. Its water is the plan's own, and held to the one water check
(:meth:`~heaton_voxel.craft.build.Builder._contain_plan`, over
:func:`~heaton_voxel.craft.survey.contained`) as a cut beside a pond is:
an end left open pours the channel out. JeffWorld's aqueduct holds its
water at ``(477, 73, 451..495)``, its north end closed by the castellum
it feeds and its south end open onto air at ``z=496``, south of which
nothing of the arcade or its springhouse stands (read 2026-10-22): the
water stands only because nothing has updated beside it, and
``contained`` over the channel's own cells names that cell as a way out
already standing. An end is closed by building it first, or named with
``spill=`` -- the world cells its water may fall through into a basin, as
the castellum's spout falls into its own.

Part of :mod:`heaton_voxel.craft.build`.
"""

from __future__ import annotations

from math import gcd
from types import MappingProxyType

from ...blocks import AIR_BLOCKS, BlockState
from ...nbt import NBTError
from .. import geometry
from ..block_facts import holds_fluid, holds_water
from ..errors import NoSurface, Refused
from ..geometry import COMPASS, Frame, heading_of, turn
from ..style import FROM_PALETTE, Material, Mix
from ..survey import _stops, reachable, StepSpace, step_kind
from ._common import (_empty, _fluid_unlaid, _same_crown, _spill, _takes,
                      _whole, AIR)
from .framed import _frame_state
from .site import _over_water
from .walls import merlon_offsets


#: What a cut writes: a cell a builder clears gets plain air, whichever of
#: the game's airs the section named.
_AIR = BlockState(AIR)


class _Default:
    """An argument left out, told apart from every value it could be given
    -- ``max_step=None`` means no clamp, so None cannot mean "left out"."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<default>"


_DEFAULT = _Default()

#: Each compass word's right hand, as a step: the way +across points on a
#: run heading that way.
_RIGHT = {word: COMPASS[turn(word, 1)] for word in COMPASS}


# --------------------------------------------------------------------------
# The section: what stands across one cell of the run
# --------------------------------------------------------------------------

def _block(value, where: str):
    """A section's piece as it is kept: a
    :class:`~heaton_voxel.blocks.BlockState` (any of the game's airs as
    plain air, the cut), or a :class:`~heaton_voxel.craft.style.Mix` as
    itself; a :class:`~heaton_voxel.craft.style.Material` is its block.
    None is an ``NBTError`` -- a cell left out is the cell left as it is --
    and a palette's sentinel, a list of pairs or anything else a
    ``TypeError``. Malformed text raises the parser's ``ValueError``."""
    if value is None:
        raise NBTError(f"{where} is None: leave the cell out of the section "
                       f"to leave what stands there as it is, or give it any "
                       f"air to cut it")
    if value is FROM_PALETTE:
        raise TypeError(f"{where} is named in the section, not read from a "
                        f"Builder's palette: pass the block, or "
                        f"b.palette[role] for a palette's")
    if isinstance(value, Mix):
        return value
    if isinstance(value, Material):
        value = value.form("block")
    if isinstance(value, str):
        value = BlockState.parse(value)
    if isinstance(value, BlockState):
        return _AIR if value.name in AIR_BLOCKS else value
    if isinstance(value, (list, tuple)):
        raise TypeError(
            f"{where} is {value!r}: a list of (block, weight) pairs is a mix "
            f"only where a writer draws one from the session's rng (road's "
            f"and pave's mix=); in a section make it a Mix, which is picked "
            f"by position")
    raise TypeError(f"{where} is a block -- an id, a BlockState, a Material, "
                    f"a Mix, a Merlon, or any air to cut -- not {value!r}")


def _states(piece) -> tuple:
    """Every state ``piece`` -- a block, a Mix or a Merlon -- may write, each
    parsed: a Mix's every entry, a Merlon's block's."""
    if isinstance(piece, Merlon):
        piece = piece.block
    if isinstance(piece, Mix):
        return tuple(entry if isinstance(entry, BlockState)
                     else BlockState.parse(entry)
                     for entry, _ in piece.entries)
    return (piece,)


def _wet(piece) -> bool:
    """Whether ``piece`` may write water or lava -- a fluid, or a block
    waterlogged as written, a bare sea pickle among them
    (:func:`~heaton_voxel.craft.block_facts.holds_fluid`)."""
    return any(holds_fluid(state.name, state.properties)
               for state in _states(piece))


def _cut(piece) -> bool:
    """Whether ``piece`` is a cut: air, written only where something
    stands."""
    return piece is _AIR


class Merlon:
    """A section cell that is a merlon where the run's battlement rhythm
    puts a tooth, and a gap everywhere else.

    ``block`` is what a tooth is built of: an id, a
    :class:`~heaton_voxel.blocks.BlockState`, a
    :class:`~heaton_voxel.craft.style.Material` (its block) or a
    :class:`~heaton_voxel.craft.style.Mix`, picked at each tooth's cell. A
    gap is a cut, written only where something stands in it, as a
    battlement's gap is. ``None`` names no block and is a ``TypeError``, as
    a battlement's ``block=None`` is; air builds no tooth, and a block
    holding water or lava is none, each an ``NBTError``. Merlons stacked
    up one across value stand together:
    every cell of that line is a tooth or a gap all the way up.
    """

    __slots__ = ("block",)

    def __init__(self, block):
        if block is None:
            raise TypeError("Merlon(None) names no block, and a merlon is "
                            "one: name the block its teeth are built of")
        if isinstance(block, Merlon):
            raise TypeError("a Merlon's block is a block, not another Merlon")
        piece = _block(block, "a Merlon's block")
        if _cut(piece):
            raise NBTError("a Merlon of air has no teeth: name the block "
                           "they are built of, or leave the cell out")
        if _wet(piece):
            raise NBTError(f"a Merlon of {piece} is a tooth of water or "
                           f"lava: a tooth is the battlement's block, and a "
                           f"channel's water a cell of the section")
        self.block = piece

    def __eq__(self, other) -> bool:
        if not isinstance(other, Merlon):
            return NotImplemented
        return self.block == other.block

    def __hash__(self) -> int:
        return hash((Merlon, self.block))

    def __repr__(self) -> str:
        return f"Merlon({self.block!r})"


def _standable(piece) -> bool:
    """Whether a body stands on every block ``piece`` may write: a whole
    block, a bottom slab or a stair, never air, a fluid, a fence or wall
    nobody climbs onto, or a merlon, which is a gap as often as not."""
    if isinstance(piece, Merlon) or _cut(piece):
        return False
    return all(state.name not in AIR_BLOCKS
               and step_kind(state.name, state.properties) in ("half", "full")
               and not holds_fluid(state.name, state.properties)
               for state in _states(piece))


def _clear(piece) -> bool:
    """Whether a body passes through every block ``piece`` may write, as the
    walk judges it: air, a torch, a flower. A Merlon is a tooth as often
    as not, and no body passes a tooth."""
    if isinstance(piece, Merlon):
        return False
    return all(state.name in AIR_BLOCKS
               or step_kind(state.name, state.properties) == "air"
               for state in _states(piece))


class CrossSection:
    """What stands across one cell of an extrusion's run: ``(across, up)``
    cells, each a piece. No world in it, and nothing written.

    ``up`` counts from the profile's height, the block stood on, so
    ``up=0`` is the ground course, ``up=1`` the cell your feet are in
    standing on it, and a negative ``up`` is a foundation row. ``across``
    is signed, + to the right of travel: the inside of a circuit walked
    clockwise seen from above. States are drawn as they stand on a run
    heading south; each run turns them by its heading (see the module).

    A piece is a block -- an id, a
    :class:`~heaton_voxel.blocks.BlockState` or a
    :class:`~heaton_voxel.craft.style.Material`'s block -- a
    :class:`~heaton_voxel.craft.style.Mix`, picked at each world cell, a
    :class:`Merlon`, or any of the game's airs, a **cut**: the cell is
    emptied where something stands in it, and left alone where it is
    already empty. A cell left out of the mapping is left as it stands.
    ``None`` is an ``NBTError`` (leave the cell out instead); a palette's
    ``FROM_PALETTE`` and a list of ``(block, weight)`` pairs are a
    ``TypeError``, since a section names its materials and a list is drawn
    from a session's rng, which a section has none of.

    A piece may hold water -- a water source, a waterlogged block, kelp --
    which is a channel: a canal's, an aqueduct's trough, a moat's.
    :meth:`ExtrudeMixin.extrude` lays a section holding water level, and
    holds its water to the water check like any other. Lava and flowing
    water (``minecraft:water`` at a level from 1 to 15) are an
    ``NBTError``, a Mix's every entry asked: nothing follows lava, and the
    game makes a flow from its sources and re-reads one written by hand.

    ``walk`` is the ``(across, up)`` cells a body stands on -- a wall walk
    -- each one of the section's blocks that a body stands on (a whole
    block, a bottom slab, a stair; never air, a fluid or a Merlon), with
    nothing solid in the section at ``(across, up + 1)`` or ``(across, up +
    2)``, where the body goes. The third cell of head room is the walk
    check's to judge, as :meth:`ExtrudeMixin.extrude` walks the plan.

    ``footing`` is a block (or a Material, its block, or a Mix) that fills
    each across column whose lowest piece is a block, from ``bury`` blocks
    into the ground up to the cell under that piece -- a wall's foundation
    down to whatever it stands on. Air, a fluid or a Merlon is no footing,
    an ``NBTError``, and so is a ``bury`` above 0 with no footing to bury.

    :meth:`rows` reads a section drawn as text. Immutable; :attr:`cells` is
    a read-only mapping of the pieces as kept (a Material as its block, any
    air as plain air), sorted by ``(across, up)``.
    """

    __slots__ = ("_cells", "_columns", "_walk", "_footing", "_bury",
                 "_water")

    def __init__(self, cells, *, walk=(), footing=None, bury: int = 0):
        try:
            items = list(cells.items())
        except AttributeError:
            raise NBTError(f"a section is a mapping of (across, up) cells to "
                           f"pieces, not {cells!r}") from None
        if not items:
            raise NBTError("a section with no cells lays nothing: give it "
                           "at least one (across, up) cell")
        kept: dict[tuple[int, int], object] = {}
        for key, value in items:
            across, up = geometry._pair(key, "a section's cell")
            piece = kept[(across, up)] = (
                value if isinstance(value, Merlon)
                else _block(value, f"the section's cell {(across, up)}"))
            for state in _states(piece):
                why = _fluid_unlaid(state)
                if why is not None:
                    raise NBTError(f"the section's cell {(across, up)} holds "
                                   f"{why}")
        kept = dict(sorted(kept.items()))
        columns: dict[int, list] = {}
        for (across, up), piece in kept.items():
            columns.setdefault(across, []).append((up, piece))
        set_ = object.__setattr__
        set_(self, "_cells", MappingProxyType(kept))
        set_(self, "_columns", MappingProxyType(
            {across: tuple(pieces) for across, pieces in columns.items()}))

        if footing is not None:
            if isinstance(footing, Merlon):
                raise NBTError("a footing is the block a section stands "
                               "on, and a Merlon is a tooth or a gap: name "
                               "a block")
            footing = _block(footing, "the section's footing")
            if _cut(footing):
                raise NBTError("a footing of air holds nothing up: name the "
                               "block a section's foundation is built of")
            if _wet(footing):
                raise NBTError(f"a footing of {footing} would write water or "
                               f"lava under the section: a footing is a "
                               f"block it stands on")
        bury = _whole(bury, "bury")
        if bury < 0:
            raise NBTError(f"bury is how many blocks of ground the footing "
                           f"replaces, 0 or more, not {bury}")
        if bury and footing is None:
            raise NBTError(f"bury={bury} replaces ground with the section's "
                           f"footing, and it has none: pass footing=")
        set_(self, "_footing", footing)
        set_(self, "_bury", bury)

        if isinstance(walk, (str, bytes)):
            raise NBTError(f"walk is a sequence of (across, up) cells, not "
                           f"{walk!r}")
        try:
            walked = [geometry._pair(cell, "a walk cell") for cell in walk]
        except TypeError:
            raise NBTError(f"walk is a sequence of (across, up) cells, not "
                           f"{walk!r}") from None
        for across, up in walked:
            piece = kept.get((across, up))
            if piece is None:
                raise NBTError(
                    f"walk cell {(across, up)} is not one of the section's "
                    f"cells: the walk is the section's blocks a body stands "
                    f"on")
            if not _standable(piece):
                raise NBTError(
                    f"walk cell {(across, up)} is {piece}, which nobody "
                    f"stands on: a walk cell is a whole block, a bottom slab "
                    f"or a stair of the section, never air, a fluid, a fence "
                    f"or a Merlon")
            for k in (1, 2):
                above = kept.get((across, up + k))
                if above is not None and not _clear(above):
                    raise NBTError(
                        f"walk cell {(across, up)} has {above} {k} over it: a "
                        f"body standing on it needs the two cells over it "
                        f"clear, so the section holds nothing solid at "
                        f"{(across, up + 1)} or {(across, up + 2)}")
        set_(self, "_walk", tuple(dict.fromkeys(walked)))
        set_(self, "_water", any(_wet(piece) for piece in kept.values()))

    def __setattr__(self, name, value):
        raise AttributeError("a CrossSection is immutable")

    @classmethod
    def rows(cls, lines, key, *, left: int, top: int, walk=(), footing=None,
             bury: int = 0) -> "CrossSection":
        """A section drawn as text, its top row first: character ``j`` of
        row ``r`` is the cell ``(left + j, top - r)``, its piece
        ``key[character]``. ``"."`` and ``" "`` are nothing -- a cell left
        out -- and so cannot be keys; any other character ``key`` does not
        name is an ``NBTError`` naming it, its row and its column.
        ``walk``, ``footing`` and ``bury`` are the constructor's.

        ::

            CrossSection.rows(["m.m",
                               "sss",
                               "sss"], {"s": "minecraft:stone_bricks",
                                        "m": Merlon("minecraft:stone_bricks")},
                              left=-1, top=1, walk=[(0, 0)])
        """
        if isinstance(lines, (str, bytes)):
            raise NBTError(f"rows takes a sequence of lines, top row first, "
                           f"not one string: {lines!r}")
        left = _whole(left, "left")
        top = _whole(top, "top")
        try:
            names = dict(key)
        except (TypeError, ValueError):
            raise NBTError(f"key maps each character to its piece, not "
                           f"{key!r}") from None
        for char in names:
            if not isinstance(char, str) or len(char) != 1:
                raise NBTError(f"a key is one character, not {char!r}")
            if char in ". ":
                raise NBTError(f"{char!r} is nothing in a drawn section -- a "
                               f"cell left out -- and cannot be a key")
        cells = {}
        try:
            drawn = list(lines)
        except TypeError:
            raise NBTError(f"rows takes a sequence of lines, top row first, "
                           f"not {lines!r}") from None
        for r, line in enumerate(drawn):
            if not isinstance(line, str):
                raise NBTError(f"row {r} of the section is {line!r}, not a "
                               f"line of text")
            for j, char in enumerate(line):
                if char in ". ":
                    continue
                if char not in names:
                    raise NBTError(f"row {r} of the section holds {char!r} "
                                   f"at column {j}, which key does not name")
                cells[(left + j, top - r)] = names[char]
        return cls(cells, walk=walk, footing=footing, bury=bury)

    @property
    def cells(self):
        """The pieces, ``{(across, up): piece}``, read-only, sorted."""
        return self._cells

    @property
    def across(self) -> range:
        """The across values the section spans, lowest to highest."""
        return range(min(self._columns), max(self._columns) + 1)

    @property
    def reach(self) -> int:
        """How far the section reaches either side of its line: the largest
        ``|across|``."""
        return max(abs(across) for across in self._columns)

    @property
    def walk(self) -> tuple:
        """The ``(across, up)`` cells a body stands on, in the order given."""
        return self._walk

    @property
    def footing(self):
        """The block filling under the section, or None."""
        return self._footing

    @property
    def bury(self) -> int:
        """How many blocks of ground the footing replaces."""
        return self._bury

    @property
    def water(self) -> bool:
        """Whether any piece may write water: a source, a waterlogged
        block, kelp -- lava and flowing water never being a piece."""
        return self._water

    def lowest(self, across: int) -> int | None:
        """The lowest ``up`` the section holds a piece at in column
        ``across``, or None where it holds none."""
        pieces = self._columns.get(across)
        return None if pieces is None else pieces[0][0]

    def _column(self, across: int) -> tuple:
        """``((up, piece), ...)`` in column ``across``, bottom up."""
        return self._columns.get(across, ())

    def __eq__(self, other) -> bool:
        if not isinstance(other, CrossSection):
            return NotImplemented
        return ((dict(self._cells), self._walk, self._footing, self._bury)
                == (dict(other._cells), other._walk, other._footing,
                    other._bury))

    def __hash__(self) -> int:
        return hash((CrossSection, tuple(self._cells.items()), self._walk,
                     self._footing, self._bury))

    def __repr__(self) -> str:
        ups = [up for _, up in self._cells]
        span = self.across
        parts = [f"{len(self._cells)} cells, across {span.start}.."
                 f"{span.stop - 1}, up {min(ups)}..{max(ups)}"]
        if self._walk:
            parts.append(f"walk {list(self._walk)}")
        if self._footing is not None:
            parts.append(f"footing {self._footing}"
                         + (f", bury {self._bury}" if self._bury else ""))
        return f"<CrossSection {', '.join(parts)}>"


class ExtrudeResult:
    """What :meth:`ExtrudeMixin.extrude` laid.

    ``line`` is the centerline, a cell per step (a closed path's repeated
    last cell dropped), and ``profile`` the height at each of its cells: the
    block stood on, ``up=0``. ``walk`` is every walk cell, the block stood
    on, and ``merlons`` the top cell of every tooth, each in write order;
    ``reach`` the :class:`~heaton_voxel.craft.survey.ReachMap` the walk was
    checked with -- read its ``jumped`` for the rises that need a jump --
    or None for a section with no walk. ``blocks`` counts the cells
    written, and ``already`` the cells the plan names that already held
    what it wanted there, cuts already empty among them, so the call made
    again at the profile it was laid at (``follow=result.profile``) writes
    none and finds them all. Made again following the terrain, it is
    refused: the ground there is now the extrusion's own top, or, for a
    section that cuts into the ground -- a ditch -- the floor its cut left,
    which it would dig as deep again. ``sealed``
    counts the seals that walled water off where it stood (``seal=``),
    written after the extrusion and not among its ``blocks``.
    """

    __slots__ = ("line", "profile", "walk", "merlons", "reach", "blocks",
                 "already", "sealed")

    def __init__(self, line, profile, walk, merlons, reach, blocks, already,
                 sealed=0):
        self.line = tuple(line)
        self.profile = tuple(profile)
        self.walk = tuple(walk)
        self.merlons = tuple(merlons)
        self.reach = reach
        self.blocks = blocks
        self.already = already
        self.sealed = sealed

    def __repr__(self) -> str:
        parts = [f"{len(self.line)} cells along", f"{self.blocks} written"]
        if self.already:
            parts.append(f"{self.already} already there")
        if self.merlons:
            parts.append(f"{len(self.merlons)} merlons")
        if self.walk:
            parts.append(f"a walk of {len(self.walk)}")
        if self.sealed:
            parts.append(f"{self.sealed} sealed")
        return f"<ExtrudeResult {', '.join(parts)}>"


# --------------------------------------------------------------------------
# The band: every across line, rasterized, and where each cell stands
# --------------------------------------------------------------------------

class _Band:
    """The cells an extrusion covers (:func:`_band`). ``line`` is the
    centerline and ``closed`` whether it closes; ``cells`` every cell of
    every across line, ``(i, across, k, x, z, heading, tooth)`` in write
    order -- by the index ``i`` of the centerline cell whose height it
    takes, then ``across``, then ``k``, its place along its own line --
    ``heading`` the run's compass word, which turns its states, and
    ``tooth`` whether a Merlon there is a tooth. ``windows`` are the
    centerline indices round each turn, merged where they overlap, that a
    profile is leveled over; ``turns`` the turning waypoints with their
    centerline index; ``along`` each centerline cell's coordinate along its
    run, x on a run east or west and z north or south, which piers are
    counted on."""

    __slots__ = ("line", "closed", "cells", "windows", "turns", "along")

    def __init__(self, line, closed, cells, windows, turns, along):
        self.line = line
        self.closed = closed
        self.cells = cells
        self.windows = windows
        self.turns = turns
        self.along = along


def _waypoints(points) -> tuple[list, bool]:
    """``points`` as ``(x, z)`` vertices, consecutive repeats dropped, and
    whether the path closes: its last waypoint its first (then dropped)."""
    if points is None or isinstance(points, (str, bytes)):
        raise NBTError(f"an extrusion runs along a sequence of (x, z) "
                       f"waypoints, not {points!r}")
    try:
        given = list(points)
    except TypeError:
        raise NBTError(f"an extrusion runs along a sequence of (x, z) "
                       f"waypoints, not {points!r}") from None
    vertices: list[tuple[int, int]] = []
    for point in given:
        vertex = geometry._pair(point, "a waypoint")
        if not vertices or vertices[-1] != vertex:
            vertices.append(vertex)
    if len(vertices) < 2:
        raise NBTError(f"an extrusion needs two waypoints that differ, got "
                       f"{given!r}")
    closed = len(vertices) > 2 and vertices[0] == vertices[-1]
    if closed:
        vertices.pop()
    return vertices, closed


def _band(points, acrosses, *, reach: int | None = None,
          claims: bool = True) -> _Band:
    """The band an extrusion along ``points`` covers, one line per value of
    ``acrosses``; pure, reading no world.

    Each segment between waypoints heads its nearest compass word
    (:func:`~heaton_voxel.craft.geometry.heading_of`, an exact diagonal to
    north or south). At a waypoint where the word stays the same the run
    goes on, and may bend there: its lines are the centerline shifted
    across, at the word's right hand. Where it changes, both segments must
    run straight along x or z at a right angle, and the lines are mitered
    there, ``right(in) + right(out)``, an exact square corner. Anything
    else is an ``NBTError`` naming the waypoint: a turn back on itself, and
    a turn beside a diagonal, which folds the band or leaves holes in it. A
    closed path -- its last waypoint its first -- is a rectangle: four
    corners turning one way, every side straight along x or z.

    Each across line is its waypoints moved by ``across`` times their
    normals, rasterized segment by segment by
    :func:`~heaton_voxel.craft.geometry.line` with the joints shared once,
    as the line itself does. Refused, naming the run: a line that folds
    back along a run -- the run too short for the turn inside it, the
    section's reach at each end it turns inside of -- and a cell any line
    reaches twice, the band come back over itself (a hairpin, or two runs
    closer than the section is wide), and a centerline that crosses itself.

    Each cell takes the centerline index of the cell on its own segment
    with the same coordinate along the run, clamped to the segment's ends,
    so a corner's miter takes the corner's. The windows are the centerline
    cells within ``reach`` (the largest ``|across|`` by default) of each
    corner, wrapping round a closed path, merged where they overlap.
    ``claims=False`` leaves the cell reached twice to the caller
    (:func:`_claim_once`), for an extrusion whose pier is wider than its
    section, which lays a pier's outer lines only at a pier."""
    vertices, closed = _waypoints(points)
    try:
        acrosses = sorted({geometry._whole(a, "an across value")
                           for a in acrosses})
    except TypeError:
        raise NBTError(f"acrosses is a sequence of ints, not "
                       f"{acrosses!r}") from None
    if not acrosses:
        raise NBTError("a band of no across values covers nothing")
    if reach is None:
        reach = max(abs(a) for a in acrosses)
    n = len(vertices)
    count = n if closed else n - 1
    segments = [(vertices[s], vertices[(s + 1) % n]) for s in range(count)]
    words, straight, steps = [], [], []
    for (ax, az), (bx, bz) in segments:
        dx, dz = bx - ax, bz - az
        words.append(heading_of(dx, dz, ties="z"))
        straight.append(dx == 0 or dz == 0)
        steps.append(max(abs(dx), abs(dz)))

    # What each waypoint is: a run going on, or a corner.
    corners: dict[int, str] = {}       # vertex -> "right" or "left"
    inner = range(n) if closed else range(1, n - 1)
    for v in inner:
        before, after = (v - 1) % count, v % count
        w1, w2 = words[before], words[after]
        (ax, az), (bx, bz) = segments[before]
        (cx, cz), (dx, dz) = segments[after]
        ux, uz, wx, wz = bx - ax, bz - az, dx - cx, dz - cz
        if ux * wz == uz * wx and ux * wx + uz * wz < 0:
            raise NBTError(
                f"the path turns back on itself at {vertices[v]}, from "
                f"{w1} to {w2}: an extrusion runs one way along its line")
        if w1 == w2:
            continue
        if not (straight[before] and straight[after]):
            raise NBTError(
                f"the path turns at {vertices[v]} from a run heading {w1} to "
                f"one heading {w2}, and an extrusion turns only at a right "
                f"angle between runs straight along x and z: beside a "
                f"diagonal the band folds over itself or leaves holes. Put "
                f"a straight run along x or z on each side of the turn, at "
                f"least as long as the section reaches")
        corners[v] = "right" if w2 == turn(w1, 1) else "left"
    if closed:
        bent = [segments[s] for s in range(count) if not straight[s]]
        why = None
        if bent:
            why = (f"runs from {bent[0][0]} to {bent[0][1]}, straight along "
                   f"neither")
        elif len(corners) != 4:
            why = f"turns {len(corners)} times"
        elif len(set(corners.values())) != 1:
            why = "turns both ways"
        if why is not None:
            raise NBTError(
                f"a closed extrusion goes round a rectangle -- four runs "
                f"straight along x and z, turning one way at each corner -- "
                f"and this path {why}")

    normals = []
    for v in range(n):
        if not closed and v == 0:
            normals.append(_RIGHT[words[0]])
        elif not closed and v == n - 1:
            normals.append(_RIGHT[words[-1]])
        elif v in corners:
            (ax, az), (bx, bz) = (_RIGHT[words[(v - 1) % count]],
                                  _RIGHT[words[v % count]])
            normals.append((ax + bx, az + bz))
        else:
            normals.append(_RIGHT[words[v % count]])

    line = list(geometry.line(vertices + [vertices[0]] if closed
                              else vertices))
    if closed:
        line.pop()
    size = len(line)
    seen: dict[tuple[int, int], int] = {}
    for i, cell in enumerate(line):
        if cell in seen:
            raise NBTError(f"the path crosses itself at {cell}: an extrusion "
                           f"comes back over its own line there")
        seen[cell] = i
    starts = [0]
    for s in range(count):
        starts.append(starts[-1] + steps[s])

    def axis_of(word: str) -> int:
        return 0 if word in ("east", "west") else 1

    def sign_of(word: str) -> int:
        return 1 if word in ("east", "south") else -1

    # Each centerline index's segment -- a joint the segment's that reached
    # it first -- and its coordinate along that run.
    along = []
    s = 0
    for i, cell in enumerate(line):
        while i > starts[s + 1]:
            s += 1
        along.append(cell[axis_of(words[s])])

    cells = []
    for a in acrosses:
        moved = [(x + a * mx, z + a * mz)
                 for (x, z), (mx, mz) in zip(vertices, normals)]
        laid: list[tuple[tuple[int, int], int]] = []
        joints: list[tuple[int, tuple[int, int]]] = []
        for s in range(count):
            (px, pz), (qx, qz) = segments[s]
            start, end = moved[s], moved[(s + 1) % n]
            cx, cz = qx - px, qz - pz
            ox, oz = end[0] - start[0], end[1] - start[1]
            dot = cx * ox + cz * oz
            if dot < 0:
                need = steps[s] - dot // steps[s]
                raise NBTError(
                    f"the run from {(px, pz)} to {(qx, qz)} is {steps[s]} "
                    f"long, too short for the section's line {a} across, "
                    f"which turns inside it and folds back on itself: give "
                    f"the run at least {need} blocks, or narrow the section")
            if (ox, oz) == (0, 0):
                continue                # a run the line turns inside, gone
            for cell in geometry.line([start, end]):
                if laid and laid[-1][0] == cell:
                    continue
                laid.append((cell, s))
            g = gcd(ox, oz)
            joints.append((len(laid) - 1, (ox // g, oz // g)))
        if not laid:
            # Every run of the line turned inside its corners down to
            # nothing: the line is the one cell its waypoints all moved to,
            # the corner of the square the band fills.
            laid.append((moved[0], 0))
        if closed and len(laid) > 1 and laid[-1][0] == laid[0][0]:
            laid.pop()
            joints[-1] = (0, joints[-1][1])

        # The line's corners, where it changes direction, and an open
        # line's two ends: a battlement's runs go corner to corner.
        length = len(laid)
        at: list[int] = [] if closed else [0]
        last = len(joints) if closed else len(joints) - 1
        for j in range(last):
            if joints[j][1] != joints[(j + 1) % len(joints)][1]:
                at.append(joints[j][0])
        if not closed:
            at.append(length - 1)
        at = sorted(set(at))
        tooth = [False] * length
        if at:
            spans = [(at[j], at[j + 1]) for j in range(len(at) - 1)]
            if closed:
                spans.append((at[-1], at[0] + length))
            elif len(at) == 1:
                spans = [(0, 0)]        # a line of one cell, a run of one
            for first, final in spans:
                teeth = merlon_offsets(final - first + 1)
                for k in teeth:
                    tooth[(first + k) % length] = True

        for k, (cell, s) in enumerate(laid):
            word = words[s]
            axis = axis_of(word)
            offset = (cell[axis] - segments[s][0][axis]) * sign_of(word)
            i = starts[s] + max(0, min(offset, steps[s]))
            if closed:
                i %= size
            cells.append((i, a, k, cell[0], cell[1], word, tooth[k]))
    if claims:
        _claim_once(cells)
    cells.sort(key=lambda c: (c[0], c[1], c[2]))

    # The windows: the centerline cells within the reach of each corner.
    turns = tuple((vertices[v], starts[v] % size) for v in sorted(corners))
    windows: list[set] = []
    for _, index in turns:
        if closed:
            window = ({(index + d) % size for d in range(-reach, reach + 1)}
                      if 2 * reach + 1 < size else set(range(size)))
        else:
            window = {index + d for d in range(-reach, reach + 1)
                      if 0 <= index + d < size}
        for other in [w for w in windows if w & window]:
            windows.remove(other)
            window |= other
        windows.append(window)
    windows.sort(key=min)
    return _Band(tuple(line), closed, tuple(cells),
                 tuple(tuple(sorted(w)) for w in windows), turns,
                 tuple(along))


def _claim_once(cells) -> None:
    """Refuse a band whose ``cells`` -- :class:`_Band` cells, or those of
    them an extrusion lays -- reach one ``(x, z)`` twice: the band come
    back over itself. Each line is walked in turn, lowest across value
    first and along it in order, and the first cell reached again is
    named."""
    claimed: dict[tuple[int, int], int] = {}
    for _, a, _, x, z, _, _ in sorted(cells, key=lambda c: (c[1], c[2])):
        cell = (x, z)
        if cell in claimed:
            other = claimed[cell]
            which = (f"its line {a} across reaches it twice"
                     if other == a else
                     f"its lines {other} and {a} across both reach it")
            raise NBTError(
                f"the band comes back over itself at {cell}: {which} -- a "
                f"hairpin, or two runs closer than the section is wide. "
                f"Move them apart")
        claimed[cell] = a


# --------------------------------------------------------------------------
# The profile
# --------------------------------------------------------------------------

def _level_turns(profile, windows, *, max_step: int | None = 1, pins=None,
                 closed: bool = False, line=None,
                 turns=()) -> tuple[int, ...]:
    """``profile`` -- one height per centerline cell -- with every window
    of ``windows`` (centerline indices round a turn, as :func:`_band` gives
    them) laid level, and the profile clamped again to ``max_step``
    (:func:`~heaton_voxel.craft.geometry.smoothed_profile`, the lower
    envelope) -- round the loop when ``closed`` -- until neither moves a
    cell; pure.

    A window holding a pin (``pins``, ``{index: y}``) is laid at the pin's
    height and pinned whole; two pins of different heights in one window
    are an ``NBTError``, since a turn cannot be level at both. Every other
    window is laid at its lowest height, then the clamp runs over
    everything with the pins held. Heights only fall, so it ends. Then the
    result is held to it: every window level, every step within
    ``max_step`` and every pin where it was put -- an ``NBTError``
    otherwise, naming the cell (``line[i]`` where ``line`` is given, and a
    turn by its corner, the first of ``turns``, ``(cell, index)`` pairs,
    inside it): a pin above its neighbors is a step the clamp leaves
    standing, since it only lowers."""
    profile = list(profile)
    pins = dict(pins or {})

    def where(i: int):
        return line[i] if line is not None else f"index {i}"

    def corner(window):
        return _corner(window, turns, where)

    for window in windows:
        pinned = {pins[i] for i in window if i in pins}
        if len(pinned) > 1:
            raise NBTError(
                f"the turn round {corner(window)} holds anchors at heights "
                f"{sorted(pinned)}, and a turn is laid level: anchor it at "
                f"one height")
        if pinned:
            y = pinned.pop()
            for i in window:
                profile[i] = y
                pins[i] = y
    while True:
        before = tuple(profile)
        for window in windows:
            if window[0] in pins:
                continue
            low = min(profile[i] for i in window)
            for i in window:
                profile[i] = low
        profile = list(geometry.smoothed_profile(
            profile, window=0, max_step=max_step, anchors=pins,
            closed=closed))
        if tuple(profile) == before:
            break
    for window in windows:
        if len({profile[i] for i in window}) > 1:
            raise NBTError(f"the turn round {corner(window)} could not be "
                           f"laid level")
    for i, y in pins.items():
        if profile[i] != y:
            raise NBTError(f"the anchor at {where(i)} could not be held at "
                           f"y={y}")
    if max_step is not None:
        size = len(profile)
        pairs = range(size if closed and size > 1 else size - 1)
        for i in pairs:
            j = (i + 1) % size
            if abs(profile[i] - profile[j]) > max_step:
                high = i if profile[i] > profile[j] else j
                raise NBTError(
                    f"the anchor at {where(high)} stands "
                    f"{abs(profile[i] - profile[j])} over the cell beside it, "
                    f"more than max_step={max_step}: the profile only lowers "
                    f"to meet an anchor, never rises to one, so a high anchor "
                    f"is a step. Anchor it lower, or let it step with a "
                    f"larger max_step")
    return tuple(profile)


def _corner(window, turns, where):
    """The waypoint a window turns round, for a refusal: the first of
    ``turns`` (``(cell, index)`` pairs) whose index is in it, else
    ``where`` of its first index."""
    inside = set(window)
    for cell, index in turns:
        if index in inside:
            return cell
    return where(window[0])


def _sequence_profile(follow, band: _Band) -> tuple[int, ...]:
    """A caller's profile, ``follow``: one int per centerline cell, level
    through every turn."""
    try:
        heights = list(follow)
    except TypeError:
        raise NBTError(f"follow is 'terrain', a height, or a sequence of "
                       f"heights, not {follow!r}") from None
    if len(heights) != len(band.line):
        raise NBTError(
            f"follow= gives {len(heights)} heights for a centerline of "
            f"{len(band.line)} cells: one per cell of geometry.line through "
            f"the waypoints (a closed path's last cell, its first again, "
            f"left out) -- a road's surface, [r.top(*cell) for cell in "
            f"r.line], only for the road's own waypoints")
    for i, y in enumerate(heights):
        if isinstance(y, bool) or not isinstance(y, int):
            raise NBTError(f"follow= height {i}, at {band.line[i]}, is "
                           f"{y!r}, not an int")
    for window in band.windows:
        found = sorted({heights[i] for i in window})
        if len(found) > 1:
            cell = _corner(window, band.turns, band.line.__getitem__)
            raise NBTError(
                f"follow= is not level through the turn round {cell}, where "
                f"it reads {found}: the cells of a corner's inner and outer "
                f"lines meet there from far along the profile, so it must "
                f"be level round each turn. Level it there, or follow "
                f"'terrain', which levels its turns -- a road's surface "
                f"serves only a road with no turns")
    return tuple(heights)


# --------------------------------------------------------------------------
# States, turned for each run
# --------------------------------------------------------------------------

class _Turns:
    """Each state a section writes, turned for a run's heading, its
    defaults with it (:func:`~heaton_voxel.craft.build.framed._frame_state`),
    each worked out once; a Mix is picked at the world cell and the pick
    turned."""

    __slots__ = ("_frames", "_turned", "_parsed")

    def __init__(self):
        self._frames: dict[str, Frame] = {}
        self._turned: dict = {}
        self._parsed: dict = {}

    def state(self, state: BlockState, word: str) -> BlockState:
        if state is _AIR or word == "south":
            return state
        key = (state, word)
        found = self._turned.get(key)
        if found is None:
            frame = self._frames.get(word)
            if frame is None:
                frame = self._frames[word] = Frame((0, 0, 0), word)
            found = self._turned[key] = _frame_state(frame, state)
        return found

    def piece(self, piece, cell, word: str) -> BlockState:
        """What ``piece`` -- a block or a Mix -- writes at the world cell
        ``cell`` on a run heading ``word``."""
        if isinstance(piece, Mix):
            picked = piece.pick(*cell)
            state = self._parsed.get(picked)
            if state is None:
                state = (picked if isinstance(picked, BlockState)
                         else BlockState.parse(picked))
                state = self._parsed[picked] = (
                    _AIR if state.name in AIR_BLOCKS else state)
            piece = state
        return self.state(piece, word)


def _count(found: list, noun: str) -> str:
    """How many faults, and the first."""
    return (f"{len(found)} {noun}s, first: {found[0]}" if len(found) > 1
            else str(found[0]))


class ExtrudeMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods: a cross-section
    swept along a line.

    Builder inherits them; nothing here is used on its own.
    """

    def extrude(self, points, section, *, follow="terrain", window=_DEFAULT,
                max_step=_DEFAULT, anchors=None, pier=None, pitch=None,
                phase=None, pier_width: int = 1, over_water=None, through=(),
                overwrite: bool = False, allow_dam: bool = False, seal=None,
                spill=()) -> ExtrudeResult:
        """Sweep ``section`` along ``points``, at a height that follows the
        ground, one level, or a profile given.

        ``points`` are ``(x, z)`` waypoints, consecutive repeats dropped,
        closed when the last is the first; ``section`` a
        :class:`CrossSection`, drawn as it stands on a run heading south.
        The band (see the module) is every across line of the section,
        mitered at each corner: a run may bend while it keeps its heading,
        and turns only at a right angle between runs straight along x and
        z; a closed path is a rectangle. A turn beside a diagonal, a turn
        back, a run too short for the turn inside it and a band that comes
        back over itself are each an ``NBTError`` naming where, before
        anything is read -- the last judged on the cells laid, so a pier
        wider than its section reaches past it only at a pier.

        **The profile.** ``follow="terrain"`` (the default) takes the
        highest ground under the section at each centerline cell -- each
        column's :meth:`ground`, the waterline over water -- smooths it by
        :func:`~heaton_voxel.craft.geometry.smoothed_profile` over
        ``window`` cells either side (road's
        :data:`~heaton_voxel.craft.geometry.ROAD_WINDOW` when left out),
        clamped to ``max_step`` blocks a cell (1 when left out; None for no
        clamp) with ``anchors``, ``{(x, z): y}`` on centerline cells,
        pinned -- round the loop on a closed path -- and lays every turn
        level over the section's reach either side, so no two cells of the
        band side by side differ by more than ``max_step``. An anchor off
        the centerline, or one a turn or the clamp cannot hold (a high one
        is a step: the profile only lowers), is an ``NBTError``. ``follow=y``
        lays the section level at ``y``, and a sequence of ints is the
        caller's own profile, one height per centerline cell -- a road's
        surface along its centerline, ``[r.top(*cell) for cell in
        r.line]``, given the road's waypoints -- refused unless it is level
        through every turn, which a road with turns is not. The road's
        :attr:`~heaton_voxel.craft.build.RoadResult.heights` is its
        profile, laid ``width // 2`` cells further along a road wider than
        1, so a hedge following it stands a block off the road's edge at
        every change of level. ``window``, ``max_step`` and
        ``anchors`` are terrain's, an ``NBTError`` with any other
        ``follow``.

        **Each column** of the band is laid from the section's column at
        its across value: the footing, from ``bury`` blocks into the ground
        up to the section's lowest piece, where that piece is a block; then
        every piece, bottom up, at the profile's height plus its ``up``,
        turned for the run's heading -- a Mix picked at the world cell and
        the pick turned, a :class:`Merlon` a tooth where its line's
        battlement rhythm puts one, per run from that run's own corners
        (:func:`~heaton_voxel.craft.build.merlon_offsets`), and a gap
        elsewhere, and a cut emptied where something stands. ``pier`` is a
        second CrossSection laid instead at the centerline cells where
        ``(u - phase) % pitch < pier_width``, ``u`` the cell's coordinate
        along its run -- x on a run east or west, z north or south -- and
        ``phase`` the first cell's when left out, so the first pier stands
        on the start, Facade's and arcade's congruence; ``pitch`` is
        required with a pier, and ``pier_width`` under it. ``through``
        names the pads and reservations (:meth:`reserve`) the band may
        cross, a keep-out it enters otherwise refused: **nothing is founded
        inside one** -- the main section spans it, no footing and no pier.

        **The ground** is read for every column laid outside a span, every
        one before anything is planned. Under ``"terrain"``, and wherever a
        footing founds a column, it is needed: each column with none is
        gathered into one :class:`~heaton_voxel.craft.errors.NoSurface`
        naming the first and how many, its ``reason`` and ``cell`` the
        first's and its ``faults`` every column's own ``NoSurface``, in
        write order -- never a height assumed for a column nobody read. A
        chunk that cannot be read raises its own ``NBTError``. Water and
        ice are refused unless ``over_water`` says what to do
        (:meth:`_build_base`) -- under the terrain everywhere, and laid at
        a height given wherever the section, or its footing, reaches down
        to the waterline (a deck carried over a pond leaves the pond as it
        is): ``"bridge"`` stands the section on the waterline, the footing
        from there up, and ``"fill"`` fills the column from the bed with
        the footing -- refused for a section with none to fill it with, and
        where it would starve flowing water unless ``allow_dam=True``.
        Under ``"terrain"`` without piers every column
        holding a block must be founded -- by its footing, or by its lowest
        block standing on the ground, at most one over it -- or it is
        refused, naming each, with the hint to give the section a footing;
        a deck over a valley is ``follow=y`` and ``pier=``.

        **Water a section holds** -- a source, a waterlogged block, kelp:
        a canal's channel, an aqueduct's trough -- lies level, so a section
        holding it, or a pier holding it, is laid level: ``follow="terrain"``
        is an ``NBTError``, and so is a sequence that steps, naming where it
        steps, both before anything is read. The channel's water is the
        plan's own, and the water check asks where it goes as it asks where
        a cut lets a pond out: a trough's open end, a gap in a bank, a
        channel carried over air with nothing under its end, each refused,
        every cell the water would enter named, unless ``spill`` names it --
        world cells ``(x, y, z)`` the water may run into, as
        :func:`~heaton_voxel.craft.survey.contained` takes them: the fall
        from an aqueduct's end into the basin under it, its water carried on
        down them, and the first cell it reaches outside them the leak. An
        end built first, or ground that closes it, holds it like any bank.

        **Before anything is written**, each refusing the whole call: the
        arguments and the band; the keep-outs, entered by a cell laid; every
        block by name; the ground, an extrusion this session laid under the
        terrain followed, and the profile; the water under the band; the
        chunks and heights the plan reaches; lava, written over or opened
        onto by a cut, which no flag passes (:meth:`_lava_refused`); what
        it writes over -- air, ground cover, natural ground, an ore or a
        tree it takes (:func:`~heaton_voxel.craft.build._common._takes`),
        and the water and ice of the columns ``over_water`` bridges or
        fills -- and anything else, a fence, a wall, a path, refused, each
        cell named, unless ``overwrite=True``, as is water ``over_water``
        never reaches, lying under dry ground or under a span, refused as
        water; a block in a :meth:`flight`'s or an
        :meth:`approach`'s claim, which no ``overwrite`` passes; water the
        plan takes out that starves a flow beside it, or that cuts a body
        of water in two (``Builder._split_faults``, the split check road's
        fills are held to), each unless ``allow_dam=True``; water the plan
        lets out -- a cut opening a pond, a channel's own water at an end
        left open -- every :class:`~heaton_voxel.craft.survey.Leak`
        :func:`~heaton_voxel.craft.survey.contained` finds outside
        ``spill`` named in one refusal (:meth:`_contain_plan`, the one
        water check), unless ``seal=`` names a full block to wall each
        leak's water off where it stands: written after the extrusion,
        refused in a keep-out not named in ``through``, held to the starve
        check, audited and walked with it, and never mending lava touching
        water, a chunk not there or the channel's own water, which no seal
        is laid over; the survival audit
        (:meth:`_audit_plan`), a torch over a cut or sand over a span; and,
        for a section with a ``walk``, the round trip: the plan walked from
        its first walk cell (:func:`~heaton_voxel.craft.survey.reachable`)
        on the band's own columns -- never round a gap over the ground
        beside the wall -- from the lowest walk cell to two over the
        highest, refused unless every walk cell is reached and walked home
        from -- so a rise of two, a step down into head room under three, a
        walk one wide that touches itself only at corners and a first cell
        with no room to stand on are each refused, and the rises that need
        a jump are in the result's ``reach.jumped``. The game rule
        ``WATER_SOURCE_CONVERSION`` is taken as on, its default, as the
        water check takes it.

        Then the plan is written (:meth:`_commit`) cell by cell in one
        total order -- by the centerline cell each column takes its height
        from, then its across value, then its place along its own line,
        each column bottom up -- so the same call on the same save writes
        the same bytes. A cell already holding what it would get, apart from
        what a block update derives, is left as it stands, so the call made
        again at the profile it was laid at (``follow=result.profile``)
        writes nothing. Each column's ground is learned as the column reader
        will find it with the plan laid (:meth:`ground`): the highest block
        that stops a fall -- a wall's top, a ditch's floor, the grass over a
        course laid under it, never a hedge's leaves or a torch -- except
        that a span is not ground: a deck between piers, or across a
        keep-out, leaves the ground under it as it was, and a channel's is
        its water's surface, which every ground writer after it reads as
        water. So following the terrain where the ground is the top of an
        extrusion this session laid is refused, since it would stand a
        second one on the first -- the same call made twice would build
        two -- and so is a section that cuts into the ground where the
        ground is still the floor an extrusion's cut left, which a ditch
        made again would dig as deep again under; a wall across the ditch
        stands on its floor. A later session reads the game's heightmap,
        which lands on the wall and in the ditch too, and is told the
        profile with ``follow=``. Nothing is drawn from :attr:`rng`.
        Returns an :class:`ExtrudeResult`.
        """
        self._alive()
        what = "extrude"
        if not isinstance(section, CrossSection):
            raise TypeError(f"extrude sweeps a CrossSection, not {section!r}")
        if pier is not None and not isinstance(pier, CrossSection):
            raise TypeError(f"pier= is a CrossSection, not {pier!r}")
        sections = (section,) if pier is None else (section, pier)
        _over_water(over_water)
        mode, level = self._follow(follow, window, max_step, anchors)
        # Water lies level: a channel laid down a slope runs out of itself
        # at every step.
        channel = next(((shape is pier and shape is not section, cell)
                        for shape in sections
                        for cell, piece in shape.cells.items()
                        if _wet(piece)), None)
        if channel is not None and mode == "terrain":
            raise NBTError(
                f"the {'pier' if channel[0] else 'section'} holds water at "
                f"{channel[1]}, and water lies level: following the terrain, "
                f"a channel runs out of itself at its first step down. Lay "
                f"it level, with follow=y or a sequence of one height")
        spill = _spill(spill, "extrude's spill")
        if window is _DEFAULT:
            window = geometry.ROAD_WINDOW
        if max_step is _DEFAULT:
            max_step = 1
        if pier is None:
            if pitch is not None or phase is not None or pier_width != 1:
                raise NBTError("pitch=, phase= and pier_width= place a pier= "
                               "section, and none is given")
        else:
            if pitch is None:
                raise NBTError("a pier stands every pitch= cells along the "
                               "run, and none is given")
            pitch = _whole(pitch, "pitch")
            pier_width = _whole(pier_width, "pier_width")
            if pier_width < 1:
                raise NBTError(f"a pier is at least one cell wide, got "
                               f"pier_width={pier_width}")
            if pier_width >= pitch:
                raise NBTError(f"pier_width={pier_width} leaves no span "
                               f"between piers {pitch} apart")
            if phase is not None:
                phase = _whole(phase, "phase")
        acrosses = sorted({across for shape in sections
                           for across, _ in shape.cells})
        reach = max(shape.reach for shape in sections)
        band = _band(points, acrosses, reach=reach, claims=False)
        size = len(band.line)
        if pier is not None and phase is None:
            phase = band.along[0]
        pins = self._pins(anchors, band) if mode == "terrain" else {}
        profile = None
        if mode == "level":
            profile = (level,) * size
        elif mode == "sequence":
            profile = _sequence_profile(follow, band)
            if channel is not None and len(set(profile)) > 1:
                i = next(i for i, y in enumerate(profile) if y != profile[0])
                raise NBTError(
                    f"follow= steps from y={profile[0]} to y={profile[i]} at "
                    f"{band.line[i]}, and the "
                    f"{'pier' if channel[0] else 'section'} holds water at "
                    f"{channel[1]}: a channel is laid level, every cell of "
                    f"it at one height, or its water runs out at the step")

        # The columns of the keep-outs named in through, which the section
        # spans.
        columns = list(dict.fromkeys((c[3], c[4]) for c in band.cells))
        allowed = set(through)
        spanned = set()
        for _, name, (x0, z0, x1, z1) in self._keep_outs():
            if name in allowed:
                spanned.update(c for c in columns
                               if x0 <= c[0] <= x1 and z0 <= c[1] <= z1)

        # Which section each cell lays: a pier's lines past its section's
        # are laid only at a pier, so the band is held to the cells laid --
        # the keep-outs, and the band come back over itself.
        laid = []
        for cell in band.cells:
            i, across, _, x, z, word, _tooth = cell
            column = (x, z)
            shape = section
            if (pier is not None and column not in spanned
                    and (band.along[i] - phase) % pitch < pier_width):
                shape = pier
            stack = shape._column(across)
            if stack:
                laid.append((cell, shape, stack))
        if not laid:
            raise NBTError("the extrusion lays nothing: no cell of the band "
                           "falls under a piece of its section")
        _claim_once([cell for cell, _, _ in laid])
        self._keep_out([(cell[3], cell[4]) for cell, _, _ in laid],
                       through=through, what="extrude would build through")

        # Every block by name, then every state turned for every heading
        # the band runs, before anything is read.
        pieces = [piece.block if isinstance(piece, Merlon) else piece
                  for shape in sections for piece in shape.cells.values()]
        pieces += [shape.footing for shape in sections
                   if shape.footing is not None]
        self._vet_names(pieces, what=what)
        self._seal_state(seal, what=what)
        turns = _Turns()
        for word in sorted({c[5] for c in band.cells}):
            for piece in pieces:
                for state in _states(piece):
                    turns.state(_AIR if state.name in AIR_BLOCKS else state,
                                word)

        def footed(shape, column, stack) -> bool:
            low = stack[0][1]
            return (shape.footing is not None and column not in spanned
                    and not _cut(low) and not isinstance(low, Merlon))

        # Every column laid outside a span is read: the ground terrain
        # follows and a footing founds on is required, and every other
        # column's is what says whether the section meets water there, and
        # what its ground is once it is laid.
        stood: dict = {}
        missing: list[NoSurface] = []
        for cell, shape, stack in laid:
            column = (cell[3], cell[4])
            if column in spanned or column in stood:
                continue
            try:
                stood[column] = self._stand(column)
            except NoSurface as exc:
                if mode == "terrain" or footed(shape, column, stack):
                    missing.append(exc)
        if missing:
            first = missing[0]
            raise NoSurface(
                f"extrude has no ground to follow under "
                f"{_count([str(exc) for exc in missing], 'column')}. "
                f"Nothing was written", faults=missing, reason=first.reason,
                cell=first.cell)
        if mode == "terrain":
            self._unstacked(stood, {
                (cell[3], cell[4]) for cell, _, stack in laid
                if stack[0][0] <= 0 and _cut(stack[0][1])})

        # The profile.
        if mode == "terrain":
            sample: list = [None] * size
            for cell, _, _ in laid:
                found = stood.get((cell[3], cell[4]))
                if found is None:
                    continue
                y, probe = found
                g = y if probe is None else probe.waterline
                i = cell[0]
                if sample[i] is None or g > sample[i]:
                    sample[i] = g
            sample = _carried(sample, closed=band.closed)
            smooth = geometry.smoothed_profile(
                sample, window=window, max_step=max_step, anchors=pins,
                closed=band.closed)
            profile = _level_turns(smooth, band.windows, max_step=max_step,
                                   pins=pins, closed=band.closed,
                                   line=band.line, turns=band.turns)

        # The water under the band, and where each column read stands: a
        # column whose profile is the ground's always meets it, as does one
        # a footing founds; laid at a height given, a section meets the
        # water only where it reaches down to the waterline -- a deck over a
        # pond leaves the pond as it is.
        tops = {}
        for cell, shape, stack in laid:
            column = (cell[3], cell[4])
            if column not in stood:
                continue
            base = profile[cell[0]]
            _, probe = stood[column]
            if (mode != "terrain" and probe is not None
                    and not footed(shape, column, stack)
                    and base + stack[0][0] > probe.waterline):
                continue
            ups = [up for up, piece in stack if not _cut(piece)]
            tops[column] = base + max(ups or [stack[-1][0]])
        wet: dict = {}
        filled: set = set()
        ground = (self._build_base(tops, over_water, what, filled=filled,
                                   wet=wet, allow_dam=allow_dam)
                  if tops else {})
        for cell, shape, stack in laid:
            column = (cell[3], cell[4])
            if column not in filled or footed(shape, column, stack):
                continue
            # A column stood on its bed with no footing is filled only where
            # the section itself reaches down to the bed.
            blocks = [up for up, piece in stack if not _cut(piece)]
            if not blocks or profile[cell[0]] + blocks[0] > ground[column] + 1:
                raise NBTError(
                    f"over_water='fill' fills a wet column from its bed with "
                    f"the section's footing, and the column at {column} gets "
                    f"none, leaving water under the section: give the section "
                    f"a footing, or bridge the water")

        # The plan, column by column in write order.
        planned: list = []                # (cell, state, column)
        groups: list = []                 # (cells, column, founded)
        floating: list = []
        walk: list = []
        merlons: list = []
        bridged = over_water == "bridge"
        for cell, shape, stack in laid:
            i, across, _, x, z, word, tooth = cell
            column = (x, z)
            base = profile[i]
            g = ground.get(column)
            cells = []
            low = stack[0][0]
            is_footed = footed(shape, column, stack)
            if is_footed:
                bury = 0 if bridged and column in wet else shape.bury
                for y in range(g + 1 - bury, base + low):
                    cells.append(((x, y, z), turns.piece(shape.footing,
                                                         (x, y, z), word)))
            top_merlon = None
            for up, piece in stack:
                at = (x, base + up, z)
                if isinstance(piece, Merlon):
                    state = (turns.piece(piece.block, at, word) if tooth
                             else _AIR)
                    if tooth:
                        top_merlon = at
                else:
                    state = turns.piece(piece, at, word)
                cells.append((at, state))
            blocks = [up for up, piece in stack if not _cut(piece)]
            founded = False
            if blocks:
                lowest = base + blocks[0]
                if column in spanned:
                    founded = False
                elif is_footed or (g is not None and lowest <= g + 1):
                    founded = True
                elif mode == "terrain" and pier is None:
                    floating.append(
                        f"{(x, lowest, z)}, {lowest - g} over the "
                        f"ground at y={g}")
            for c, state in cells:
                planned.append((c, state, column))
            groups.append((cells, column, founded))
            for walk_across, up in shape.walk:
                if walk_across == across:
                    walk.append((x, base + up, z))
            if top_merlon is not None:
                merlons.append(top_merlon)
        if floating:
            raise Refused(
                f"extrude would leave {_count(floating, 'column')} standing "
                f"on nothing: under 'terrain' every column is founded, by "
                f"the section's footing or by its lowest block standing on "
                f"the ground. Give the section a footing= to fill under it, "
                f"or carry it level over the dip with follow=y and pier=. "
                f"Nothing was written", faults=floating)

        # Legal and reachable, before a cell is read.
        self._vet_plan([("set", c, state) for c, state, _ in planned],
                       what=what)
        world = {c: self._read(c) for c, _, _ in planned}
        self._lava_refused({c: state for c, state, _ in planned}, world,
                           what=what, noun="the extrusion")

        writes: set = set()
        taken: list = []
        drowned: list = []
        claims: list = []
        for c, state, column in planned:
            found = world[c]
            if not _empty(state) and c in self._claimed:
                claims.append(c)
                continue
            if _same_crown(found, state) or (_empty(state) and _empty(found)):
                continue
            if overwrite or _takes(found, wet=column in wet):
                writes.add(c)
            elif _takes(found, wet=True):
                drowned.append(f"{c} holds {found}")
            else:
                taken.append(f"{c} holds {found}, where the extrusion needs "
                             f"{state}")
        if taken:
            raise Refused(
                f"extrude would write over what somebody built -- "
                f"{_count(taken, 'cell')}. Move the extrusion, or pass "
                f"overwrite=True to take it all the same. Nothing was "
                f"written", faults=taken)
        if drowned:
            # Water over_water never saw: under dry ground, or under a span.
            raise Refused(
                f"extrude would write over water or ice that over_water does "
                f"not reach -- {_count(drowned, 'cell')}: water lying under "
                f"the ground the column stands on, or under a span. Keep the "
                f"extrusion out of it, or pass overwrite=True to take it all "
                f"the same. Nothing was written", faults=drowned)
        if claims:
            who = self._claimant(claims[0])
            raise Refused(
                f"extrude would build into what {who} this session built "
                f"claims -- {_count(claims, 'cell')}: a block in a stair's "
                f"treads or head room is a stair you climb and cannot come "
                f"back down. Route the extrusion clear of it. Nothing was "
                f"written", faults=claims)

        ops: list = []
        tops_laid: dict = {}              # column -> (its ground, own top?)
        for cells, column, founded in groups:
            ops.extend(("set", c, state) for c, state in cells
                       if c in writes)
            settled = self._ground_after(column, cells, founded=founded)
            if settled is not None:
                y, own = settled
                ops.append(("ground", column, y))
                tops_laid[column] = (y, own)

        # The water the plan takes out: what a filled column's footing
        # fills, and any other water it writes over.
        drained = {(probe.x, y, probe.z) for column, probe in wet.items()
                   if column in filled
                   for y in range(probe.bed_y + 1, probe.waterline + 1)}
        removed = sorted(c for c, state, _ in planned if c in writes
                         and holds_water(world[c].name, world[c].properties)
                         and not holds_water(state.name, state.properties))
        rest = [c for c in removed if c not in drained]
        if rest and not allow_dam:
            self._starve(rest, call=what, allow_dam=allow_dam)
        if not allow_dam:
            split = self._split_faults(sorted(drained | set(removed)))
            if split:
                raise Refused(
                    f"extrude would cut a body of water in two -- "
                    f"{_count(split, 'fault')}. Keep the extrusion off the "
                    f"water or bridge it higher, or pass allow_dam=True to "
                    f"fill it all the same. Nothing was written",
                    faults=split)
        # The water: a cut that opens a pond's bank, and a channel's own
        # water, held to the one water check; the world cells it may run
        # into, and the seals walling off water it lets in, the caller's.
        seals = self._contain_plan(ops, what=what, seal=seal, spill=spill,
                                   spilling="spill=")
        if seals:
            self._keep_out([(x, z) for _, (x, _, z), _ in seals],
                           through=through,
                           what="extrude's seals would stand in")
            if not allow_dam:
                # A seal takes the water it stands in, as a fill does.
                self._starve(sorted(drained | set(removed)
                                    | {c for _, c, _ in seals}),
                             call=what, allow_dam=allow_dam)
        self._audit_plan(ops + seals, what=what)
        reach_map = (self._walk_extrusion(
            ops + seals, walk, [(cell[3], cell[4]) for cell, _, _ in laid])
            if walk else None)
        self._commit(ops + seals, what=what)
        for column, (y, own) in tops_laid.items():
            self._extruded.pop(column, None)
            self._trenched.pop(column, None)
            if y is not None:
                (self._extruded if own else self._trenched)[column] = y
        written = sum(1 for op in ops if op[0] == "set")
        return ExtrudeResult(band.line, profile, walk, merlons, reach_map,
                             written, len(planned) - written, len(seals))

    @staticmethod
    def _follow(follow, window, max_step, anchors):
        """What ``follow`` asks for -- ``("terrain", None)``, ``("level",
        y)`` or ``("sequence", None)`` -- with terrain's own arguments held
        to terrain, every one checked before anything is read."""
        if isinstance(follow, str):
            if follow != "terrain":
                raise NBTError(f"follow is 'terrain', a height, or a "
                               f"sequence of heights, not {follow!r}")
            mode, level = "terrain", None
        elif isinstance(follow, bool):
            raise NBTError(f"follow is 'terrain', a height, or a sequence "
                           f"of heights, not {follow!r}")
        elif isinstance(follow, int):
            mode, level = "level", follow
        else:
            mode, level = "sequence", None
        if mode != "terrain":
            given = [name for name, value in (("window", window),
                                              ("max_step", max_step))
                     if value is not _DEFAULT]
            if anchors is not None:
                given.append("anchors")
            if given:
                raise NBTError(f"{', '.join(given)} smooth the ground a "
                               f"terrain-following extrusion rides, and "
                               f"follow={follow!r} follows none: leave "
                               f"{'them' if len(given) > 1 else 'it'} out")
            return mode, level
        if window is not _DEFAULT and (isinstance(window, bool)
                                       or not isinstance(window, int)
                                       or window < 0):
            raise NBTError(f"window is a non-negative int, the cells either "
                           f"side of each height, not {window!r}")
        if max_step is not _DEFAULT and max_step is not None and (
                isinstance(max_step, bool) or not isinstance(max_step, int)
                or max_step < 0):
            raise NBTError(f"max_step is None or a non-negative int, not "
                           f"{max_step!r}")
        return mode, level

    @staticmethod
    def _pins(anchors, band: _Band) -> dict[int, int]:
        """``anchors``, ``{(x, z): y}`` on centerline cells, as ``{index:
        y}``; an anchor off the centerline, or a height that is not an
        int, is an ``NBTError`` -- an anchor meant to pin the line that
        pins nothing is a wall built somewhere it was not meant to be."""
        if anchors is None:
            return {}
        try:
            items = list(anchors.items())
        except AttributeError:
            raise NBTError(f"anchors map centerline cells (x, z) to "
                           f"heights, not {anchors!r}") from None
        index = {cell: i for i, cell in enumerate(band.line)}
        pins = {}
        for cell, y in items:
            column = geometry._pair(cell, "an anchor's cell")
            if column not in index:
                raise NBTError(f"the anchor at {column} is off the "
                               f"extrusion's centerline, so it pins nothing: "
                               f"anchor a cell of geometry.line through the "
                               f"waypoints")
            if isinstance(y, bool) or not isinstance(y, int):
                raise NBTError(f"an anchor's height is an int, got {y!r} at "
                               f"{column}")
            pins[index[column]] = y
        return pins

    def _unstacked(self, stood, cutting=()) -> None:
        """Refuse following the terrain where the ground under the band is
        the top of an extrusion this session laid there: every one of the
        ``stood`` columns whose ground is still the height an extrusion set
        (``_extruded``). Called again, a wall would read its own top as the
        ground and stand a second wall on it -- the same call made twice
        builds two. So, among the ``cutting`` columns, where the plan's
        lowest piece is a cut into the ground, is a column whose ground is
        still the floor an extrusion's cut lowered it to (``_trenched``):
        a ditch made again would read its own floor as the ground and dig
        as deep again under it. A wall following the terrain across a
        ditch stands on its floor, which is ground. A later session reads
        the game's heightmap, which lands on the wall and in the ditch as
        well; that is the profile's to say, not the terrain's."""
        tops = {column for column in stood
                if column in self._extruded
                and self._ground.get(column) == self._extruded[column]}
        floors = {column for column in stood
                  if column in cutting and column in self._trenched
                  and self._ground.get(column) == self._trenched[column]}
        stacked = [column for column in stood
                   if column in tops or column in floors]
        if stacked:
            if not floors:
                what = ("the top of an extrusion this session laid there: "
                        "following it would stand a second one on the first")
            elif not tops:
                what = ("the floor an extrusion this session cut there: "
                        "following it would dig a second one under the first")
            else:
                what = ("the top of an extrusion this session laid there, or "
                        "the floor of one it cut: following it would stand a "
                        "second one on the first, or dig one under it")
            raise Refused(
                f"extrude follows the terrain, and the ground under "
                f"{_count([str(column) for column in stacked], 'column')} is "
                f"{what}. To lay the same "
                f"one again, follow the profile it was laid at "
                f"(follow=result.profile); to join two runs, extrude them as "
                f"one path, mitered at its corner; or give the heights to "
                f"stand on with follow=. Nothing was written",
                faults=stacked)

    def _ground_after(self, column, cells, *, founded: bool):
        """The ground a column reader finds at ``column`` once ``cells`` --
        ``((x, y, z), state)``, what the plan leaves there -- are laid, as
        ``(y, own)``, ``own`` whether ``y`` is one of ``cells``; None where
        that is the ground the session already knows there
        (:meth:`known_ground`) -- unless the plan writes water into that
        cell, or takes the water out of it, so the column is learned again
        and the ground writers after it read it wet or dry as it now is --
        and ``(None, False)`` where nothing is left to stand on down to
        the world's floor.

        It is the highest cell that stops a fall
        (:func:`~heaton_voxel.craft.survey._stops`, the game's
        ``MOTION_BLOCKING_NO_LEAVES``: never leaves, a torch or a flower)
        with the plan laid over the world, read down from the higher of
        the known ground and the plan's top for a ``founded`` column, which
        stands on the ground, and from the known ground for any other --
        a cut lowers it, and a span over it is no ground, as
        :meth:`ground` defines it: a deck is not where a build stands. A
        column the session knows nothing of, and does not found, keeps
        what a read of it would find."""
        x, z = column
        prior = self.known_ground(x, z)
        planned = {cell[1]: state for cell, state in cells}
        if founded:
            start = max(planned) if prior is None else max(prior,
                                                           max(planned))
        elif prior is None:
            return None
        else:
            start = prior
        y = start
        while True:
            state = planned.get(y)
            if state is None:
                state = self._peek((x, y, z))
                if state is None:              # under the world's floor
                    return None if prior is None else (None, False)
            if _stops(state):
                break
            y -= 1
        if y == prior:
            # The height it had: learned again only where the plan turns
            # that cell to water or out of it -- a canal cut at the lawn's
            # own level -- since the ground writers after it read the
            # column's water at the height it is learned at.
            here = planned.get(y)
            found = self._peek((x, y, z))
            if (here is None or found is None
                    or holds_water(here.name, here.properties)
                    == holds_water(found.name, found.properties)):
                return None
        return y, y in planned

    def _walk_extrusion(self, ops, walk, columns):
        """Refuse the plan unless its walk walks both ways: from the first
        walk cell, :func:`~heaton_voxel.craft.survey.reachable` over the
        plan laid on the world, landing only on the band's own columns
        (:class:`_OnTheBand`) from the lowest walk cell to two over the
        highest, every walk cell reached and walked home from. Returns the
        map, whose ``jumped`` names the rises that need a jump."""
        overlay = self._plan_cells(ops, what="extrude")
        space = _OnTheBand(self.save, columns, overlay=overlay,
                           dimension=self.dimension)
        xs = [column[0] for column in columns]
        zs = [column[1] for column in columns]
        ys = [cell[1] for cell in walk]
        lo = (min(xs), min(ys), min(zs))
        hi = (max(xs), max(ys) + 2, max(zs))
        start = walk[0]
        try:
            reach = reachable(space, start, lo, hi)
        except NBTError as exc:
            raise Refused(f"the extrusion's walk cannot be walked from its "
                          f"first cell: {exc}. Nothing was written",
                          faults=[start]) from None
        lost = [cell for cell in walk
                if cell not in reach.reached or cell in reach.stranded]
        if lost:
            raise Refused(
                f"the planned walk does not walk both ways from {start}: "
                f"{reach!r}, and {len(lost)} of its cells are not walked to "
                f"and back, first {lost[0]} -- a rise of two, a step down "
                f"with no head room three over it, or a walk that touches "
                f"itself only at corners. Nothing was written", faults=lost)
        return reach


class _OnTheBand(StepSpace):
    """The plan laid on the world, as a walker on the wall finds it: every
    column off the band is open air to the walk check, so the walk is
    walked along the wall and never round a gap in it over the ground
    beside -- on a low wall or a terrace, the ground inside a circuit lies
    within a block of the walk, and a walk check free to step down onto it
    passes a walk with a rise of two in it. A column of the band reads as
    it is, its head room included."""

    __slots__ = ("_columns",)

    def __init__(self, save, columns, *, overlay, dimension):
        super().__init__(save, overlay=overlay, dimension=dimension)
        self._columns = frozenset(columns)

    def kind(self, x: int, y: int, z: int) -> str:
        if (x, z) not in self._columns:
            return "air"
        return super().kind(x, y, z)


def _carried(sample, *, closed: bool) -> list[int]:
    """``sample`` with each None -- a centerline cell no band cell read
    ground for, inside a turn or across a span -- the height before it,
    round the loop when ``closed``; leading ones on an open line take the
    first height read. All None is an ``NBTError``: nothing to follow."""
    known = [i for i, y in enumerate(sample) if y is not None]
    if not known:
        raise NBTError("a terrain-following extrusion found no ground to "
                       "follow: every column is spanned. Lay it with "
                       "follow=y")
    out = list(sample)
    size = len(out)
    if closed:
        start = known[0]
        for step in range(1, size):
            i = (start + step) % size
            if out[i] is None:
                out[i] = out[(i - 1) % size]
    else:
        for i in range(known[0]):
            out[i] = sample[known[0]]
        for i in range(known[0] + 1, size):
            if out[i] is None:
                out[i] = out[i - 1]
    return out
