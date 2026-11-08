"""Furniture: a piece stood on a floor, and the room it must not seal.

:class:`Piece` is a small rule -- cells to states, in the piece's own
coordinates -- and :meth:`FurnitureMixin.furnish` places pieces the way a
player places them, refusing the plan whole when two pieces stand in one
cell, when something already stands where a piece goes, when the game would
not keep what it places, or when a cell of the room you could get to and
back from before is cut off after.

The rules the pieces obey were found by building a house and standing in
it. ``tools/suburban_house/furniture.py`` recorded them, and they are kept
here as it wrote them (its lines 1-16, then its vanity and bathtub):

    Furniture, as vanilla blocks.

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

    A basin with a mirror hung over it.

    The mirror is a pane in the basin's own column rather than in the
    wall behind: a pane put *into* a one-block wall is a hole through it,
    and the first sign of that is daylight in a bathroom.

    A tub: water held by full blocks, so nothing can leak into the room.

Three notes on those, in this module's terms:

* ``fy`` is the y of the floor block a :class:`Piece` stands on: the y of
  the origin of the :class:`~heaton_voxel.craft.geometry.Frame` that places
  it (:meth:`Piece.at`, :meth:`Piece.turned`), as craft's ``floor_y`` is
  everywhere. A piece's own ``dy=0`` is the cell over that block, where
  your feet are -- the seat in the air at y=65 -- and every piece counts
  its height from there, so no piece reaches down into the floor it stands
  on. The yellow tower's room layouts (``tools/yellow_tower/layouts.json``)
  count from the same floor block but put the cell on it at ``dy=1``, so
  their ``dy`` is this one plus one; the Roman city's furnishings
  (``tools/roman_city/common.py``) take the feet cell itself as their ``y``.
* The chest's "opaque" is, in the game's words, a block that *conducts*
  power: ``ChestBlock.isBlockedChestByBlock`` asks ``isRedstoneConductor``
  of the block above, so glass, a slab or a carpet over a chest is fine and
  a smooth quartz counter is not. The survival audit knows it as
  ``"blocked chest"`` (:func:`~heaton_voxel.craft.survey.unsupported`),
  and :meth:`~FurnitureMixin.furnish` refuses a plan that makes one.
* "Water held by full blocks" is the water check's to say: a piece may
  hold water -- a tub's, a fountain's, a trough's sources, a waterlogged
  block -- and :meth:`~FurnitureMixin.furnish` refuses one whose water gets
  out (:func:`~heaton_voxel.craft.survey.contained`), naming every cell it
  would run into, unless the piece names it as its ``spill``: the air a
  spout falls through into its basin. JeffWorld's Roman fountains
  (``tools/roman_city/common.py``, ``round_fountain``) stand a source on a
  pillar over their basin, and the one at (350, 350) holds its water
  exactly, read 2026-10-22, with the four cells beside the spout and the
  four under them given as that spill. Lava and flowing water are no
  furniture: nothing follows lava, and the game makes a flow from its
  sources.

**A seat is named by its back.** A stair's full side is its ``facing``
side, so a stair set as a chair faces the way its back points, and a
seat's argument is ``back=``, never a facing. Named the other way -- the
way the sitter looks -- every seat comes out backwards: the Roman city's
curia benches (``tools/roman_city/jw_monuments.py``, "facing in" in
``m2_forum.py``) were written facing the room, and in JeffWorld the benches
at (415, 65, 380..388) and (423, 65, 380..388) are ``spruce_stairs``
facing east and west, their backs to the floor and their seats against the
walls (read 2026-10-22).

Part of :mod:`heaton_voxel.craft.build`.
"""

from __future__ import annotations

from types import MappingProxyType

from ...block_defaults import BLOCK_DEFAULTS
from ...blocks import BlockState
from ...nbt import NBTError
from ...place import _cells_for
from .._plan import as_state, refuse_many
from ..block_facts import holds_water
from ..errors import Refused
from ..geometry import _pair, axis_of, COMPASS, Frame, OPPOSITE
from ..style import FROM_PALETTE, Material
from ..survey import reachable, StepSpace
from ._common import (_empty, _fluid_unlaid, _same_crown, _spill, _whole,
                      HEAD_ROOM)
from .framed import _frame_state


def _cell(value, what: str) -> tuple[int, int, int]:
    """``value`` as three whole numbers, or an NBTError naming ``what``."""
    try:
        a, b, c = value
    except (TypeError, ValueError):
        raise NBTError(f"{what} must be three whole numbers, got "
                       f"{value!r}") from None
    for part in (a, b, c):
        if isinstance(part, bool) or not isinstance(part, int):
            raise NBTError(f"{what} must be three whole numbers, got "
                           f"{value!r}")
    return a, b, c


def _named(given, where: str, *, form: str | None = None) -> BlockState:
    """One exact block a piece is made of: an id, a
    :class:`~heaton_voxel.blocks.BlockState` or a ``(name, properties)``
    pair -- or, where the piece writes one shape of a family (a seat's
    stairs, a stack's block), a
    :class:`~heaton_voxel.craft.style.Material`, as that ``form``. Named at
    the call, never a palette's, and never a
    :class:`~heaton_voxel.craft.style.Mix`: a piece is placed, and which
    cells a placement writes depends on its one block. Each is a
    ``TypeError`` naming ``where`` the block was given; malformed text
    raises the parser's ``ValueError``."""
    if given is FROM_PALETTE:
        raise TypeError(f"{where} is named at the call, not read from a "
                        f"Builder's palette: pass the block, or "
                        f"b.palette[role] for a palette's")
    if given is None:
        raise TypeError(f"{where} is None, which names no block")
    if isinstance(given, Material):
        if form is None:
            raise TypeError(f"{where} is one block, and {given!r} is a family "
                            f"of them: pass one of its forms, .form(...)")
        return as_state(given.form(form))
    refuse_many(given)
    if not isinstance(given, (str, BlockState, tuple)):
        raise TypeError(f"{where} is a block -- an id, a BlockState or "
                        f"(name, properties) -- not {given!r}")
    return as_state(given)


def _unsaid(state: BlockState, keys, where: str, why: str) -> None:
    """Refuse a block naming a property the piece sets itself: a seat's
    ``facing`` is its back, a bed's is where its head lies."""
    named = [key for key in keys if key in state.properties]
    if named:
        raise NBTError(f"{where} {state} names {', '.join(named)}, which the "
                       f"piece sets: {why}")


def _with(state: BlockState, **properties) -> BlockState:
    """``state`` with ``properties`` added after the ones it names."""
    return BlockState(state.name, {**state.properties, **properties})


def _span(corner1, corner2, owner: str) -> tuple[int, int, int, int]:
    """The inclusive ``(x0, z0, x1, z1)`` two ``(dx, dz)`` corners span,
    ``corner2`` defaulting to ``corner1``: one cell."""
    x0, z0 = _pair(corner1, f"Piece.{owner}'s corner1")
    x1, z1 = ((x0, z0) if corner2 is None
              else _pair(corner2, f"Piece.{owner}'s corner2"))
    return min(x0, x1), min(z0, z1), max(x0, x1), max(z0, z1)


def _columns(span) -> list[tuple[int, int]]:
    x0, z0, x1, z1 = span
    return [(x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)]


def _word(word, what: str) -> str:
    if not isinstance(word, str) or word not in COMPASS:
        raise NBTError(f"{what} must be one of {tuple(COMPASS)}, got {word!r}")
    return word


def _anchor(state: BlockState, cell) -> list:
    """The cells one placement writes that put ``state`` in ``cell``, as
    ``place``'s own ``_cells_for`` lists them, its anchor -- the foot of a
    bed, the lower half of a door or a tall plant -- first. ``_cells_for``
    takes a bed by its ``part`` but reads every door and tall plant as its
    lower half, so an upper half given alone is placed from the cell under
    it."""
    parts = _cells_for(state, *cell)
    if len(parts) == 1:
        return parts
    for where, part in parts:
        if where == cell and part.canonical() == state.canonical():
            return parts
    x, y, z = cell
    below = _cells_for(state, x, y - 1, z)
    if below[-1][0] == cell and below[-1][1].canonical() == state.canonical():
        return below
    raise NBTError(f"{state} at {cell} is no half its own placement would "
                   f"write there")


class Piece:
    """A piece of furniture: cells to block states, in its own coordinates,
    stood on a floor.

    ``blocks`` maps ``(dx, dy, dz)`` to a block -- an id, a
    :class:`~heaton_voxel.blocks.BlockState` or ``(name, properties)``.
    **One height convention, for every piece:** ``dy=0`` is the cell your
    feet stand in, directly on the floor block the piece stands on; a seat,
    a table's top or its legs, a bed, a rug, the bottom of a shelf all
    start there, and nothing reaches below it, so a ``dy`` under 0 -- or a
    door or a tall plant given by an upper half at ``dy=0`` -- is an
    ``NBTError``. ``dx`` and ``dz`` are across and out, +x and +z, as a
    :class:`~heaton_voxel.craft.geometry.Frame`'s identity takes them, so a
    piece designed facing south is turned to face any way by
    :meth:`turned`. ``name`` is the piece's own word for itself in a
    refusal.

    A piece is placed by a frame whose **origin is the floor block** under
    its ``(0, 0, 0)``: :meth:`at` puts it there unturned, and
    :meth:`turned` turns it there -- its cells through
    :meth:`~heaton_voxel.craft.geometry.Frame.cell`, one row up, and its
    states through the frame as a
    :meth:`~heaton_voxel.craft.build.Builder.framed` write turns them, a
    property left at its default included, so a bare
    ``minecraft:oak_stairs`` (facing north) faces east in a frame facing
    west. :meth:`~FurnitureMixin.furnish` takes placed pieces.

    **Two-block things are one placement.** A bed, a door and a tall plant
    are placed whole, as an item is (``place``'s rule): a bed given by its
    foot gets its head toward ``facing``, a door or a tall plant given by
    its lower half gets the upper. Given by its head, or its upper half,
    alone, it is placed from the other one; given by both, as the yellow
    tower's room layouts list a bed, the two halves are one placement --
    and two halves that do not make one thing between them (a head facing
    away from its foot, a second block standing in the other half's cell)
    are an ``NBTError``.

    **Water in a piece** -- a source, a waterlogged block, kelp -- is a
    tub's, a fountain's or a trough's, and the water check holds it when
    the piece is furnished: every cell its water would run, fall or
    waterlog into is refused, unless it is one of ``spill``, the piece's
    own ``(dx, dy, dz)`` cells its water may run into -- the air a spout
    pours through into its basin -- placed and turned with its blocks
    (:meth:`spill_cells`), the water carried on through them and the first
    cell it reaches past them the leak. Lava and flowing water
    (``minecraft:water`` at a level from 1 to 15) are no furniture: nothing
    follows lava, and the game makes a flow from the sources feeding it.

    Refused as it is made (``NBTError``): a cell that is not three whole
    numbers, a ``dy`` under 0, air (a piece is what stands in a room;
    clearing is a shell's or a ``set``'s), lava and flowing water, a piece
    with no blocks, and a ``spill`` cell that is not three whole numbers,
    lies under the floor row, is a cell one of the piece's own blocks
    fills -- a spill is where the piece leaves room for its water -- or is
    given to a piece holding no water. A
    :class:`~heaton_voxel.craft.style.Mix`, a
    :class:`~heaton_voxel.craft.style.Material` or ``FROM_PALETTE`` is a
    ``TypeError``: a piece is placed, and a placement is one block. Whether
    the game has each block is asked when the piece is furnished, by the
    Builder that places it (``Builder(allow=)`` passes a modded one).
    Immutable and hashable: two pieces are equal when their names,
    placements, spills and frames are.
    """

    __slots__ = ("_name", "_placements", "_blocks", "_spill", "_frame",
                 "_world", "_spilled")

    def __init__(self, blocks, *, name: str = "piece", spill=()):
        if not isinstance(name, str) or not name:
            raise NBTError(f"a piece is named by a non-empty string, not "
                           f"{name!r}")
        try:
            items = list(blocks.items())
        except AttributeError:
            raise NBTError(f"a piece's blocks are a mapping of (dx, dy, dz) "
                           f"to a block, not {type(blocks).__name__}") from None
        if not items:
            raise NBTError(f"piece {name!r} has no blocks")
        given: dict[tuple[int, int, int], BlockState] = {}
        for key, block in items:
            cell = _cell(key, f"a cell of piece {name!r}")
            state = _named(block, f"a block of piece {name!r}")
            if cell[1] < 0:
                raise NBTError(
                    f"piece {name!r} puts {state} at dy={cell[1]}: dy=0 is "
                    f"the cell on the floor block it stands on, and nothing "
                    f"of a piece goes below it")
            if _empty(state):
                raise NBTError(f"piece {name!r} puts {state.name} at {cell}: "
                               f"a piece holds what stands in a room, and "
                               f"clearing is a shell's or a set's")
            why = _fluid_unlaid(state)
            if why is not None:
                raise NBTError(f"piece {name!r} puts {state} at {cell}, which "
                               f"is not furniture: {why}")
            given[cell] = state
        placements: dict = {}
        # Each cell a placement writes: the anchor it was placed from, and
        # what it puts there.
        owner: dict = {}
        for cell in sorted(given):
            if cell in owner:
                continue
            state = given[cell]
            parts = _anchor(state, cell)
            anchor, first = parts[0]
            for where, part in parts:
                if where[1] < 0:
                    raise NBTError(
                        f"piece {name!r} gives {state} at {cell}, whose "
                        f"{part} would stand at dy={where[1]}, under the "
                        f"floor row: give the {state.name} by its lower half")
                if where in owner:
                    other, put = owner[where]
                    raise NBTError(
                        f"piece {name!r} puts two things in {where}: {part} "
                        f"of the {state.name} placed at {anchor}, and {put} "
                        f"of the one placed at {other}")
                found = given.get(where)
                if found is not None and found.canonical() != part.canonical():
                    raise NBTError(
                        f"piece {name!r} gives {found} at {where}, where the "
                        f"{state.name} placed at {anchor} puts {part}: two "
                        f"halves of one thing must agree, and nothing else "
                        f"stands in its other half")
            for where, part in parts:
                owner[where] = (anchor, part)
            placements[anchor] = first
        ordered = tuple(sorted(placements.items()))
        cells = dict(sorted((where, part) for anchor, state in ordered
                            for where, part in _cells_for(state, *anchor)))
        spill = _spill(spill, f"piece {name!r}'s spill",
                       cells="its own cells (dx, dy, dz)")
        if spill:
            if not any(holds_water(state.name, state.properties)
                       for state in cells.values()):
                raise NBTError(
                    f"piece {name!r} names spill cells and holds no water to "
                    f"spill: spill is where a piece's own water may run")
            for cell in spill:
                if cell[1] < 0:
                    raise NBTError(
                        f"piece {name!r} lets its water spill into {cell}, "
                        f"under the floor row: dy=0 is the cell on the floor "
                        f"block it stands on, and the floor holds its water")
                if cell in cells:
                    raise NBTError(
                        f"piece {name!r} lets its water spill into {cell}, "
                        f"where it puts {cells[cell]}: a spill cell is one "
                        f"the piece leaves open for its water")
        set_ = object.__setattr__
        set_(self, "_name", name)
        set_(self, "_placements", ordered)
        set_(self, "_blocks", MappingProxyType(cells))
        set_(self, "_spill", spill)
        set_(self, "_frame", None)
        set_(self, "_world", None)
        set_(self, "_spilled", None)

    def __setattr__(self, name, value):
        raise AttributeError("a Piece is immutable")

    def __delattr__(self, name):
        raise AttributeError("a Piece is immutable")

    # A value copies as itself, and pickles through its constructor (and
    # its frame): the default slot-by-slot rebuild would go through
    # __setattr__.
    def __copy__(self) -> "Piece":
        return self

    def __deepcopy__(self, memo) -> "Piece":
        return self

    def __reduce__(self):
        return _rebuilt_piece, (dict(self._placements), self._name,
                                self._frame, self._spill)

    # -- what it is ----------------------------------------------------------

    @property
    def name(self) -> str:
        """The piece's word for itself, in a refusal."""
        return self._name

    @property
    def blocks(self):
        """Every cell of the piece in its own ``(dx, dy, dz)``, both halves
        of a two-block thing included, as the placements write them: a
        read-only mapping, in cell order."""
        return self._blocks

    @property
    def spill(self) -> tuple:
        """The piece's own ``(dx, dy, dz)`` cells its water may run into,
        sorted; empty for none."""
        return self._spill

    @property
    def frame(self) -> Frame | None:
        """The :class:`~heaton_voxel.craft.geometry.Frame` placing the
        piece -- its origin the floor block under ``(0, 0, 0)`` -- or None
        for a piece not placed yet."""
        return self._frame

    def cells(self) -> dict:
        """Every world cell the placed piece writes, with its state, both
        halves of a two-block thing included, in cell order. A piece not
        placed yet is an ``NBTError``: it has no world cells."""
        return dict(sorted((where, part) for cell, state in self._placed()
                           for where, part in _cells_for(state, *cell)))

    def placements(self) -> list:
        """The placed piece's placements, ``((x, y, z), state)``, one per
        thing placed -- a bed by its foot, a door by its lower half -- in
        world cell order: what :meth:`~FurnitureMixin.furnish` places."""
        return list(self._placed())

    def spill_cells(self) -> tuple:
        """The world cells the placed piece's water may run into: its
        ``spill`` placed and turned as its blocks are, sorted -- what
        :meth:`~FurnitureMixin.furnish` hands the water check, which takes
        world cells. A piece not placed yet is an ``NBTError``."""
        self._placed()
        return self._spilled

    def _placed(self) -> tuple:
        if self._world is None:
            raise NBTError(f"piece {self._name!r} is not placed yet: put it "
                           f"on a floor with .at(x, y, z) or "
                           f".turned(Frame((x, y, z), facing)), y the floor "
                           f"block it stands on")
        return self._world

    # -- placing it ----------------------------------------------------------

    def at(self, x: int, y: int, z: int) -> "Piece":
        """This piece stood on the floor block ``(x, y, z)``, unturned: its
        ``(dx, dy, dz)`` at ``(x + dx, y + 1 + dy, z + dz)``. That is
        :meth:`turned` by ``Frame((x, y, z))``."""
        return self.turned(Frame((x, y, z)))

    def turned(self, frame) -> "Piece":
        """This piece placed by ``frame``, a
        :class:`~heaton_voxel.craft.geometry.Frame` whose origin is the
        floor block under the piece's ``(0, 0, 0)``: each cell at
        ``frame.cell(dx, dy, dz)``, one row up -- ``dy=0`` over the floor
        -- and each state as the frame turns it, a property it leaves at
        its default turned too (as
        :meth:`~heaton_voxel.craft.build.Builder.framed` turns it), so a
        bed's head, a stair's back and a door's hinge go where the frame
        puts them. A piece placed already is placed again inside
        ``frame``: the two frames compose
        (:meth:`~heaton_voxel.craft.geometry.Frame.within`), so a room's
        pieces laid out once, on a floor at ``y=0`` of the room's own
        frame, are turned with the room.

        Anything but a Frame is an ``NBTError``; a state the schematic
        vocabulary cannot turn is
        :class:`~heaton_voxel.craft.errors.Refused`, in any frame that
        turns."""
        if not isinstance(frame, Frame):
            raise NBTError(f"a piece is placed by a craft.geometry.Frame, "
                           f"not {frame!r}")
        if self._frame is not None:
            frame = self._frame.within(frame)
        world = []
        for (dx, dy, dz), state in self._placements:
            x, y, z = frame.cell(dx, dy, dz)
            world.append(((x, y + 1, z), _frame_state(frame, state)))
        spilled = []
        for dx, dy, dz in self._spill:
            x, y, z = frame.cell(dx, dy, dz)
            spilled.append((x, y + 1, z))
        placed = object.__new__(Piece)
        set_ = object.__setattr__
        set_(placed, "_name", self._name)
        set_(placed, "_placements", self._placements)
        set_(placed, "_blocks", self._blocks)
        set_(placed, "_spill", self._spill)
        set_(placed, "_frame", frame)
        set_(placed, "_world", tuple(sorted(world)))
        set_(placed, "_spilled", tuple(sorted(spilled)))
        return placed

    # -- comparing -----------------------------------------------------------

    def _key(self) -> tuple:
        return (self._name, self._placements, self._spill, self._frame)

    def __eq__(self, other) -> bool:
        if not isinstance(other, Piece):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash((Piece, self._key()))

    def __repr__(self) -> str:
        where = "" if self._frame is None else f" on {self._frame!r}"
        return f"<Piece {self._name!r}: {len(self._blocks)} blocks{where}>"

    # -- the vocabulary ------------------------------------------------------

    @classmethod
    def seat(cls, corner1, corner2=None, *, back: str, stair, ends=None,
             name: str = "seat") -> "Piece":
        """A seat over the ``(dx, dz)`` cells ``corner1`` to ``corner2`` --
        a chair, one cell, when ``corner2`` is left out; a bench or a sofa,
        a run -- each a bottom ``stair`` at ``dy=0`` facing ``back``.

        **A seat is named by its back.** A stair's full side is its
        ``facing`` side -- ``oak_stairs[facing=west]`` holds on its west
        face -- so the backrest is the side the stair faces, and a chair
        whose sitter looks at a table to its south has ``back="north"``; a
        bench against a west wall has ``back="west"``. The game's ``shape``
        is left to the placement, as a stair placed by hand takes the shape
        its neighbors give it. ``stair`` is a block ending in ``_stairs``,
        or a :class:`~heaton_voxel.craft.style.Material`'s stairs; it names
        no ``facing`` or ``half``, which the seat sets.

        ``ends`` is a block for the arms: one cell past each end of the
        seat across its back, at ``dy=0`` -- a sofa's trapdoor arms, an
        armchair's -- with its ``facing``, if the block has one, toward the
        seat, as the suburban house's sofas hold theirs. A seat with ends
        is a single row across its back; a block naming its own ``facing``
        there is an ``NBTError``.
        """
        span = _span(corner1, corner2, "seat")
        back = _word(back, "Piece.seat's back")
        state = _named(stair, "Piece.seat(stair=)", form="stairs")
        if not state.name.endswith("_stairs"):
            raise NBTError(f"Piece.seat(stair=) is a stair, which the seat "
                           f"faces by its back, and {state.name} is not one")
        _unsaid(state, ("facing", "half"), "Piece.seat(stair=)",
                "a seat faces its back, and sits at the bottom of its cell")
        seat = _with(state, facing=back, half="bottom")
        blocks = {(x, 0, z): seat for x, z in _columns(span)}
        if ends is not None:
            arm = _named(ends, "Piece.seat(ends=)")
            _unsaid(arm, ("facing",), "Piece.seat(ends=)",
                    "an arm faces the seat it ends")
            across = "x" if axis_of(back) == "z" else "z"
            x0, z0, x1, z1 = span
            if (across == "x" and z0 != z1) or (across == "z" and x0 != x1):
                raise NBTError(
                    f"Piece.seat(ends=) arms a single row of seats across its "
                    f"back, and {corner1}..{corner2} is more than one deep "
                    f"toward {back}")
            low, high = ("west", "east") if across == "x" else ("north", "south")
            faced = "facing" in BLOCK_DEFAULTS.get(arm.name, {})
            for (x, z), outward in (((x0, z0), low), ((x1, z1), high)):
                sx, sz = COMPASS[outward]
                blocks[(x + sx, 0, z + sz)] = (
                    _with(arm, facing=OPPOSITE[outward]) if faced else arm)
        return cls(blocks, name=name)

    @classmethod
    def table(cls, corner1, corner2=None, *, top, leg=None, cloth=None,
              name: str = "table") -> "Piece":
        """A table over the ``(dx, dz)`` cells ``corner1`` to ``corner2``.

        With no ``leg`` its ``top`` -- a full block, a slab -- stands on the
        floor at ``dy=0``, a counter's way or the suburban house's dining
        table's; with one, a ``leg`` -- a fence, a barrel -- stands under
        every cell of it at ``dy=0`` and the ``top`` -- a pressure plate, a
        slab, a carpet -- lies on the legs at ``dy=1``, the Roman city's
        fence and slab. ``cloth`` lies over the top, one row up. Each is
        one block, named: a Material is a ``TypeError`` here, since which of
        its forms a top is, is the caller's to say.
        """
        span = _span(corner1, corner2, "table")
        top = _named(top, "Piece.table(top=)")
        level = 0
        blocks: dict = {}
        if leg is not None:
            leg = _named(leg, "Piece.table(leg=)")
            for x, z in _columns(span):
                blocks[(x, 0, z)] = leg
            level = 1
        for x, z in _columns(span):
            blocks[(x, level, z)] = top
        if cloth is not None:
            cloth = _named(cloth, "Piece.table(cloth=)")
            for x, z in _columns(span):
                blocks[(x, level + 1, z)] = cloth
        return cls(blocks, name=name)

    @classmethod
    def bed(cls, foot, head, *, bed, name: str = "bed") -> "Piece":
        """A bed with its foot at ``foot`` and its head at ``head``, two
        ``(dx, dz)`` cells side by side: ``bed`` -- a block ending in
        ``_bed`` -- facing from the foot to the head, at ``dy=0``, one
        placement that writes both halves. ``bed`` names no ``facing`` or
        ``part``, which the two cells set."""
        fx, fz = _pair(foot, "Piece.bed's foot")
        hx, hz = _pair(head, "Piece.bed's head")
        step = (hx - fx, hz - fz)
        facing = next((word for word, unit in COMPASS.items() if unit == step),
                      None)
        if facing is None:
            raise NBTError(f"a bed's head lies beside its foot, and {head} is "
                           f"not beside {foot}")
        state = _named(bed, "Piece.bed(bed=)")
        if not state.name.endswith("_bed"):
            raise NBTError(f"Piece.bed(bed=) is a bed, and {state.name} is "
                           f"not one")
        _unsaid(state, ("facing", "part"), "Piece.bed(bed=)",
                "a bed faces from its foot to its head")
        return cls({(fx, 0, fz): _with(state, facing=facing)}, name=name)

    @classmethod
    def stack(cls, corner1, corner2=None, *, block, height: int = 1,
              dy: int = 0, name: str = "stack") -> "Piece":
        """``block`` over the ``(dx, dz)`` cells ``corner1`` to ``corner2``,
        ``height`` rows of it from ``dy`` up: a shelf of bookshelves three
        high, a wardrobe, a counter, a rug (``height=1``), cupboards hung
        over a counter (``dy=2``). A
        :class:`~heaton_voxel.craft.style.Material` is its block."""
        span = _span(corner1, corner2, "stack")
        height = _whole(height, "Piece.stack's height")
        dy = _whole(dy, "Piece.stack's dy")
        if height < 1:
            raise NBTError(f"a stack is at least one block high, got "
                           f"height={height}")
        state = _named(block, "Piece.stack(block=)", form="block")
        return cls({(x, y, z): state for x, z in _columns(span)
                    for y in range(dy, dy + height)}, name=name)

    @classmethod
    def lamp(cls, cell=(0, 0), *, lamp, post=None, height=None,
             name: str = "lamp") -> "Piece":
        """A lamp standing at the ``(dx, dz)`` ``cell``: on the floor at
        ``dy=0``, or on ``height`` blocks of ``post`` (one when left out) --
        a nightstand's lantern, a candelabrum's torch. A lantern stands, so
        a ``lamp`` naming ``hanging=true`` is an ``NBTError``: hang one
        with :meth:`pendant`. A ``height`` without a ``post``, or under 1,
        is an ``NBTError`` too. What it stands on is the survival audit's
        to judge when the piece is furnished: a lantern needs the middle of
        the block under it."""
        dx, dz = _pair(cell, "Piece.lamp's cell")
        light = _named(lamp, "Piece.lamp(lamp=)")
        if light.properties.get("hanging") == "true":
            raise NBTError("Piece.lamp stands a lamp on what is under it; "
                           "hang one from a ceiling with Piece.pendant")
        blocks: dict = {}
        if post is None:
            if height is not None:
                raise NBTError(f"Piece.lamp(height={height!r}) is the post's "
                               f"height, and the lamp has no post")
            rise = 0
        else:
            rise = 1 if height is None else _whole(height, "Piece.lamp's height")
            if rise < 1:
                raise NBTError(f"a lamp's post is at least one block high, "
                               f"got height={rise}")
            stand = _named(post, "Piece.lamp(post=)")
            for y in range(rise):
                blocks[(dx, y, dz)] = stand
        blocks[(dx, rise, dz)] = light
        return cls(blocks, name=name)

    @classmethod
    def pendant(cls, cell=(0, 0), *, lamp, ceiling: int, chain=None,
                links: int = 0, name: str = "pendant") -> "Piece":
        """A lamp hung at the ``(dx, dz)`` ``cell`` from the ceiling at
        ``dy=ceiling`` -- the block it hangs from, which is not the
        piece's: under a ceiling at y=70 over a floor block at y=64,
        ``ceiling=5`` -- on ``links`` blocks of ``chain``, or straight from
        the ceiling with none. A lamp with a ``hanging`` property hangs
        (``hanging=true``, set unless the lamp names it; naming ``false``
        is an ``NBTError``). The game keeps a lantern only under the middle
        of something that holds it there, a chain or a ceiling, which the
        survival audit checks when the piece is furnished. A lamp that would
        hang below the floor row, ``links`` without a ``chain`` or a
        ``chain`` without ``links``, is an ``NBTError``."""
        dx, dz = _pair(cell, "Piece.pendant's cell")
        ceiling = _whole(ceiling, "Piece.pendant's ceiling")
        links = _whole(links, "Piece.pendant's links")
        if links < 0:
            raise NBTError(f"a pendant hangs on no links or more, got "
                           f"links={links}")
        if (chain is None) != (links == 0):
            raise NBTError("Piece.pendant hangs its lamp on links of chain: "
                           "name the chain and how many links, or neither")
        dy = ceiling - 1 - links
        if dy < 0:
            raise NBTError(f"a lamp {links} links under a ceiling at "
                           f"dy={ceiling} hangs at dy={dy}, below the floor "
                           f"row")
        light = _named(lamp, "Piece.pendant(lamp=)")
        if "hanging" in BLOCK_DEFAULTS.get(light.name, {}):
            if light.properties.get("hanging") == "false":
                raise NBTError("Piece.pendant hangs its lamp; stand one with "
                               "Piece.lamp")
            if "hanging" not in light.properties:
                light = _with(light, hanging="true")
        blocks = {(dx, dy, dz): light}
        if links:
            link = _named(chain, "Piece.pendant(chain=)")
            for y in range(dy + 1, ceiling):
                blocks[(dx, y, dz)] = link
        return cls(blocks, name=name)


def _rebuilt_piece(placements, name, frame, spill=()) -> Piece:
    """A piece rebuilt from its placements, which is how one unpickles."""
    piece = Piece(placements, name=name, spill=spill)
    return piece if frame is None else piece.turned(frame)


class FurnishResult:
    """What :meth:`FurnitureMixin.furnish` placed: ``pieces`` and
    ``placements`` counted, ``blocks`` the cells the pieces hold (both
    halves of a bed or a door), ``already`` those of them left as they
    stood because a placement found its every cell already right, and
    ``written`` the rest. ``reach`` is the room walked with the furniture
    in it, a :class:`~heaton_voxel.craft.survey.ReachMap`, or None with
    ``walk=False``."""

    __slots__ = ("pieces", "placements", "blocks", "already", "written",
                 "reach")

    def __init__(self, pieces: int):
        self.pieces = pieces
        self.placements = 0
        self.blocks = 0
        self.already = 0
        self.written = 0
        self.reach = None

    def __repr__(self) -> str:
        walked = "" if self.reach is None else f", {self.reach!r}"
        return (f"<FurnishResult {self.pieces} pieces, {self.blocks} blocks, "
                f"{self.written} written, {self.already} already{walked}>")


class FurnitureMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods: furnishing a
    room, :meth:`furnish`.

    Builder inherits them; nothing here is used on its own.
    """

    def furnish(self, pieces, *, room=None, doorstep=None, walk: bool = True,
                overwrite: bool = False) -> FurnishResult:
        """Place ``pieces`` -- placed :class:`Piece` values, or one -- with
        item semantics, every one or none.

        Every placement goes through ``place`` (``Builder.place``): a bed,
        a door and a tall plant come whole, and a stair seat or a fence leg
        takes the shape and the connections its neighbors give it, as the
        game would. Written in piece order, each piece's placements in
        world cell order. Before the first write, every fault named in
        one refusal at each step:

        1. two pieces putting a block in one cell -- the head of a bed with
           a pot on it, which is how the Roman insulae have lone bed
           feet -- each overlap naming both pieces and the cell;
        2. a block the game does not have
           (:class:`~heaton_voxel.craft.errors.IllegalBlock`), a chunk not
           generated or not finished, a height past the world;
        3. what stands there: a cell holding anything but air is a fault
           unless ``overwrite=True`` -- furniture never writes into a wall
           (the vanity's lesson) -- while a placement whose every cell
           already holds what it would put there, apart from the shape and
           connections a block update derives, is left and counted
           ``already``; and a cell a flight or an approach this session
           built claims, whatever ``overwrite`` says, since a piece in a
           stair's head room is a stair you climb and cannot come down;
        4. the survival audit (``Builder._audit_plan``): a block the game
           will not keep -- a lantern under nothing that holds it, a carpet
           on air, a lone half -- or a chest under a block that conducts
           power, which will not open; faults that stood before the call
           are not the call's;
        5. unless ``walk=False``, the room walked from ``doorstep`` before
           and after the plan
           (:func:`~heaton_voxel.craft.survey.reachable`, which walks back
           home as well as out): every cell you could get to and back from
           before -- walking, or with a jump, as onto a dais -- and can
           still stand on, must still be so. One that cannot be reached is
           **sealed** -- a shelf one cell from the wall seals the floor
           behind it, and draws as a finished room in every plan -- one
           reached and not left is **one-way**, and one you walked to
           before and now reach only by a jump **now needs a jump**. A
           cell a piece now stands on is furnished, not lost, and so is a
           floor a piece's water stands over, a tub's, which nobody walks
           in; a cell that was one-way before is not the plan's, as a
           fault that stood before is not the audit's; the doorstep
           covered by a piece is a refusal of its own;
        6. the water (``Builder._contain_plan``, the one water check): every
           cell water would run, fall or waterlog into with the plan laid
           -- a tub's water through a gap in its rim, a fountain's spout
           past its basin, a bank a piece placed over with
           ``overwrite=True`` -- each a
           :class:`~heaton_voxel.craft.survey.Leak`, unless it is among the
           pieces' :meth:`Piece.spill_cells`, world cells each placed with
           its piece. There is no seal: furniture keeps its own water in,
           and keeps clear of anybody else's.

        ``room`` is ``(x0, z0, x1, z1)``, the columns walked, inclusive,
        and ``doorstep`` is ``(x, y, z)``, the block you stand on to come
        in, as :func:`~heaton_voxel.craft.survey.reachable` takes it; the
        walk's box is the room's and the doorstep's columns, from a block
        under the lowest floor a piece stands on to
        :data:`~heaton_voxel.craft.build.HEAD_ROOM` over the highest cell
        a piece writes. The walk vouches for nothing past those columns, so
        a piece with a cell outside them is an ``NBTError``, before
        anything is read: a shelf next door, across the only way in, would
        seal that room unseen. For the same reason a piece in the room that
        closes the way on into a space beyond it is judged only when
        ``room`` takes that space in too, and a room whose only way round
        loops out of the box reads as sealed: name the room you mean. Both
        are required unless ``walk=False``, and a doorstep with nothing to
        stand on is an ``NBTError``. Anything that is not a placed
        :class:`Piece`, and no pieces at all, are an ``NBTError``. Returns
        a :class:`FurnishResult`.
        """
        self._alive()
        if isinstance(pieces, Piece):
            pieces = [pieces]
        try:
            pieces = list(pieces)
        except TypeError:
            raise NBTError(f"furnish takes Pieces, not {pieces!r}") from None
        if not pieces:
            raise NBTError("furnish places at least one Piece")
        for piece in pieces:
            if not isinstance(piece, Piece):
                raise NBTError(f"furnish takes Pieces, not {piece!r}")
            piece._placed()
        if not isinstance(walk, bool):
            raise NBTError(f"walk is True or False, got {walk!r}")
        if not isinstance(overwrite, bool):
            raise NBTError(f"overwrite is True or False, got {overwrite!r}")
        if walk:
            if room is None or doorstep is None:
                raise NBTError(
                    "furnish walks the room from its doorstep before and "
                    "after, so a furnishing that seals floor behind it is "
                    "refused: pass room=(x0, z0, x1, z1) and doorstep=(x, y, "
                    "z), the block you stand on to come in -- or "
                    "walk=False")
            try:
                rx0, rz0, rx1, rz1 = room
            except (TypeError, ValueError):
                raise NBTError(f"room is (x0, z0, x1, z1), got {room!r}") from None
            for value in (rx0, rz0, rx1, rz1):
                _whole(value, "a room corner")
            doorstep = _cell(doorstep, "the doorstep")

        labels = [f"piece {i} ({piece.name!r})" for i, piece in enumerate(pieces)]
        if walk:
            # The walk vouches only for the columns it walks, so a piece
            # standing past them could seal a room next door unseen.
            (lx, hx), (lz, hz) = sorted((rx0, rx1)), sorted((rz0, rz1))
            outside: list[str] = []
            for i, piece in enumerate(pieces):
                for cell in piece.cells():
                    x, _, z = cell
                    if (not (lx <= x <= hx and lz <= z <= hz)
                            and (x, z) != (doorstep[0], doorstep[2])):
                        outside.append(f"{labels[i]} stands at {cell}")
                        break
            if outside:
                raise NBTError(
                    f"{outside[0]}"
                    f"{'' if len(outside) == 1 else f' (and {len(outside) - 1} more pieces)'}"
                    f", outside the room {(lx, lz, hx, hz)} and the doorstep's "
                    f"column, which are all the walk checks: name a room "
                    f"that holds every piece -- and whatever space a piece "
                    f"could close off -- or pass walk=False")
        # The plan: every placement, in piece order and each piece's world
        # cell order, with the cells it writes.
        plan: list = []
        holder: dict = {}
        overlaps: list[str] = []
        for i, piece in enumerate(pieces):
            for cell, state in piece.placements():
                parts = _cells_for(state, *cell)
                for where, _ in parts:
                    first = holder.get(where)
                    if first is not None:
                        overlaps.append(
                            f"{where}: {labels[first]} and {labels[i]} both "
                            f"stand there")
                    else:
                        holder[where] = i
                plan.append((i, cell, state, parts))
        if overlaps:
            raise Refused(
                f"furnish would put two pieces in one cell"
                f"{'' if len(overlaps) == 1 else f' {len(overlaps)} times'}, "
                f"first {overlaps[0]}. Nothing was written", faults=overlaps)

        result = FurnishResult(len(pieces))
        result.placements = len(plan)
        result.blocks = len(holder)
        self._vet_plan([("place", cell, state) for _, cell, state, _ in plan],
                       what="furnish")
        faults: list[str] = []
        occupied = False                # a fault overwrite=True would pass
        ops: list = []
        overlay: dict = {}
        for i, cell, state, parts in plan:
            # Every cell the placement writes, both halves of a door or a
            # bed, each named with the part that would land in it.
            claimed = [(where, part) for where, part in parts
                       if where in self._claimed]
            if claimed:
                faults += [
                    f"{where} is claimed by {self._claimant(where)} this "
                    f"session built, where {labels[i]} would put {part}: "
                    f"furniture in a stair's head room makes a stair you can "
                    f"climb and not come back down" for where, part in claimed]
                continue
            found = [(where, part, self._read(where)) for where, part in parts]
            if all(_same_crown(have, part) for _, part, have in found):
                result.already += len(parts)
                continue
            taken = [(where, part, have) for where, part, have in found
                     if not (_empty(have) or _same_crown(have, part))]
            if taken and not overwrite:
                faults += [f"{where} holds {have}, where {labels[i]} needs "
                           f"{part}" for where, part, have in taken]
                occupied = True
                continue
            ops.append(("place", cell, state))
            for where, part in parts:
                overlay[where] = part
        if faults:
            raise Refused(
                f"{len(faults)} {'cell' if len(faults) == 1 else 'cells'} the "
                f"furnishing needs {'is' if len(faults) == 1 else 'are'} "
                f"taken, first: {faults[0]}. Move the piece"
                f"{', or pass overwrite=True to place it through whatever is there' if occupied else ''}"
                f". Nothing was written", faults=faults)
        self._audit_plan(ops, what="furnish")
        if walk:
            result.reach = self._furnish_walk(
                pieces, labels, overlay, (rx0, rz0, rx1, rz1), doorstep)
        # The water, its spill the pieces' own cells placed with them.
        spill = sorted({cell for piece in pieces
                        for cell in piece.spill_cells()})
        self._contain_plan(ops, what="furnish", spill=spill, sealing=False,
                           spilling="the piece's spill=")
        self._commit(ops, what="furnish")
        result.written = len(overlay)
        return result

    def _furnish_walk(self, pieces, labels, overlay, room, doorstep):
        """Walk the room before the plan and with it laid over the world,
        and refuse what the plan takes away (see :meth:`furnish`). Returns
        the walk after."""
        x0, z0, x1, z1 = room
        sx, sy, sz = doorstep
        floors = [piece.frame.origin[1] for piece in pieces]
        tops = [cell[1] for piece in pieces for cell in piece.cells()]
        box = ((min(x0, x1, sx), min(sy, *floors) - 1, min(z0, z1, sz)),
               (max(x0, x1, sx), max(sy, *tops) + HEAD_ROOM, max(z0, z1, sz)))
        # Walked before anything else is asked, so a doorstep with nothing
        # to stand on is the caller's NBTError, never a covered doorstep.
        before = reachable(self.save, doorstep, *box, dimension=self.dimension)
        after_space = StepSpace(self.save, overlay=overlay,
                                dimension=self.dimension)
        if after_space.stand_top(*doorstep) is None:
            covering = [labels[i] for i, piece in enumerate(pieces)
                        for cell in piece.cells()
                        if cell[0] == sx and cell[2] == sz
                        and sy <= cell[1] <= sy + 2]
            raise Refused(
                f"the doorstep {doorstep} would be covered"
                f"{' by ' + covering[0] if covering else ''}: nobody could "
                f"stand there to come in. Nothing was written",
                faults=[doorstep])
        after = reachable(after_space, doorstep, *box)
        # What the room offered before: every cell you could get to and
        # back from, a dais you hop onto included, and of those the ones
        # you could walk to. A cell stranded before is somebody else's
        # fault, as the audit leaves one, and stays out of it.
        round_before = before.reached - before.stranded
        walked_before = round_before - before.jumped
        # A tub's water a body would stand in: the walk wades through
        # water, and nobody walks in a tub.
        wet = {cell for cell, state in overlay.items()
               if holds_water(state.name, state.properties)}
        lost: list[str] = []
        for cell in sorted(round_before):
            if cell in after.reached and cell not in after.stranded and (
                    cell not in walked_before or cell not in after.jumped):
                continue                # as good as it was
            if after_space.stand_top(*cell) is None:
                continue                # a piece stands there: furnished
            x, y, z = cell
            if (x, y + 1, z) in wet or (x, y + 2, z) in wet:
                continue                # a piece's water stands there
            if cell not in after.reached:
                lost.append(f"{cell} is sealed")
            elif cell in after.stranded:
                lost.append(f"{cell} is one-way: you get there and not back")
            else:
                lost.append(f"{cell} now needs a jump")
        if lost:
            raise Refused(
                f"the furnishing would cut {len(lost)} floor "
                f"{'cell' if len(lost) == 1 else 'cells'} off the walk from "
                f"{doorstep}, first: {lost[0]}. Nothing was written",
                faults=lost)
        return after
