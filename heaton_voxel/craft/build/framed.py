"""Building through a frame: a component written in its own coordinates.

A gatehouse, a stall, a window bay is designed once, facing one way, and
built facing every way the plan needs it. :class:`Framed` is a Builder seen
through a :class:`~heaton_voxel.craft.geometry.Frame`: its writes take the
component's own ``(across, up, out)`` cells and the states as designed, and
the frame turns both before the Builder writes them. A view, not a session
-- every write is the Builder's own, planned and checked by its
``_commit``, so the journal, the stages, the ``rng``, the keep-outs, the
guard and ``finish()`` are exactly the Builder's.

Part of :mod:`heaton_voxel.craft.build`.
"""

from __future__ import annotations

from ...block_defaults import default_properties
from ...blocks import BlockState
from ...nbt import NBTError
from .._plan import as_state
from ..geometry import Frame
from ..style import Mix
from ._common import _Mix, _state


def _frame_state(frame: Frame, block, *, inverse: bool = False) -> BlockState:
    """``block`` as ``frame`` turns it -- or, with ``inverse``, as
    :meth:`~heaton_voxel.craft.geometry.Frame.unstate` turns it back --
    the properties it leaves at their defaults included.

    A state says only what it names: a bare ``minecraft:oak_stairs`` is the
    stair facing north, its other properties at their defaults, and the game
    reads it so. :meth:`~heaton_voxel.craft.geometry.Frame.state` turns the
    properties a state carries, as a schematic's turn does, so a default
    stays where it was -- facing north in every frame. Here the block's
    defaults (:func:`~heaton_voxel.block_defaults.default_properties`) are
    turned with it, and the result is the state as the frame turns it plus
    each default the turn moved off its default value: through a frame
    facing west a bare stair is ``oak_stairs[facing=east]``, a mirrored
    ``oak_door[facing=north,half=lower]`` gains ``hinge=right``, and a bare
    fence or log, which no turn moves off its defaults, stays bare. Nothing
    else is spelled out -- a ``waterlogged`` left unsaid stays unsaid, so a
    placement into a water source still waterlogs -- and a state that names
    every property, or whose block has none (one the table does not know among
    them), is ``frame.state(block)`` itself: the same object back when
    nothing turns.
    """
    state = as_state(block)
    turn = frame.unstate if inverse else frame.state
    turned = turn(state)
    defaults = default_properties(state.name)
    given = state.properties
    missing = {key: value for key, value in defaults.items() if key not in given}
    if not missing:
        return turned
    spelled = turn(BlockState(state.name, {**given, **missing})).properties
    have = turned.properties
    moved = {key: value for key, value in spelled.items()
             if key not in have and value != defaults.get(key)}
    if not moved:
        return turned
    return BlockState(state.name, {**have, **moved})


class _Turned(Mix):
    """A :class:`~heaton_voxel.craft.style.Mix` seen through a frame: each
    cell picked at its **world** cell, as the Mix itself picks it there,
    and the pick then turned by the frame.

    So one Mix is one field over the world whatever faces which way: a
    turned component draws the very stones the same cells would get
    unturned, each standing the way the frame turns it. Turning the
    entries into a new Mix would not do: a Mix's key folds in a CRC of its
    entries, so the turned one would be another field, picking differently
    in each orientation. The Builder knows it as a Mix -- it names its
    turned entries for the legality check, picks it per cell, sweeps it in
    a fill and refuses to place it -- and nothing else: what the base
    class keeps for its own picking is the original's, read through it.
    """

    __slots__ = ("_mix", "_frame", "_turn")

    def __init__(self, mix: Mix, frame: Frame):
        # Not Mix.__init__: the field is the original's, never a new one.
        self._mix = mix
        self._frame = frame
        # Each entry's block, as the original's pick hands it back, to its
        # turned state: turned once each, so a refusal (a direction the
        # vocabulary cannot turn) comes before anything is read.
        turn: dict = {}
        for block, _ in mix.entries:
            if block not in turn:
                turn[block] = _frame_state(frame, block)
        self._turn = turn

    @property
    def entries(self) -> tuple:
        """The original's entries, each block turned, its weight kept."""
        return tuple((self._turn[block], weight)
                     for block, weight in self._mix.entries)

    @property
    def seed(self) -> int:
        return self._mix.seed

    @property
    def clump(self) -> int:
        return self._mix.clump

    def pick(self, x: int, y: int, z: int):
        """The original's pick at the world cell ``(x, y, z)``, turned."""
        return self._turn[self._mix.pick(x, y, z)]

    def _field(self) -> tuple:
        return (self._mix._field(), self._frame)

    def __eq__(self, other) -> bool:
        if not isinstance(other, _Turned):
            return NotImplemented
        return self._field() == other._field()

    def __hash__(self) -> int:
        return hash((_Turned, self._field()))

    def __repr__(self) -> str:
        return f"{self._mix!r} through {self._frame!r}"


def _frame_block(frame: Frame, block):
    """``block`` as the frame turns it: a state turned, its defaults with
    it (:func:`_frame_state`); a
    :class:`~heaton_voxel.craft.build._common._Mix`, drawn from the
    Builder's ``rng`` as it is written, turned entry by entry with its
    weights, so it draws the entry it would have drawn and writes it
    turned; and a :class:`~heaton_voxel.craft.style.Mix` picked at the
    world cell and the pick turned (:class:`_Turned`)."""
    if isinstance(block, _Mix):
        return _Mix([(_frame_state(frame, name), weight)
                     for name, weight in block.mix])
    if isinstance(block, Mix):
        return _Turned(block, frame)
    return _frame_state(frame, block)


def _local(cell, what: str = "a cell") -> tuple[int, int, int]:
    try:
        across, up, out = cell
    except (TypeError, ValueError):
        raise NBTError(f"{what} is (across, up, out), got {cell!r}") from None
    return across, up, out


def _frame_op(frame: Frame, op) -> tuple:
    """A plan op in a frame's own terms -- ``set``, ``fill``, ``place`` or
    ``ground``, as :meth:`~heaton_voxel.craft.build.Builder._commit` takes
    them, with local cells -- as the world op the Builder writes: each cell
    through :meth:`~heaton_voxel.craft.geometry.Frame.cell`, a fill's two
    corners to the world box they span, a ground op's ``(across, out)``
    column to its world column and its height up by the frame's origin,
    and every block through :func:`_frame_block`. The op is otherwise as
    given, so a plan keeps its order, and one component through one frame
    always writes in one order.

    A ``paste`` is refused: a schematic is turned as it is stamped
    (:meth:`~heaton_voxel.craft.build.Builder.stamp`), whole, never remapped
    op by op. A coordinate that is not an int is an ``NBTError``, as the
    frame refuses it, and so is any other op kind."""
    kind = op[0]
    if kind in ("set", "place"):
        return (kind, frame.cell(*_local(op[1])), _frame_block(frame, op[2]))
    if kind == "fill":
        low, high = frame.box(op[1], op[2])
        return ("fill", low, high, _frame_block(frame, op[3]))
    if kind == "ground":
        try:
            across, out = op[1]
        except (TypeError, ValueError):
            raise NBTError(f"a column is (across, out), got {op[1]!r}") from None
        dx, dz = frame.step(across, out)
        x, y, z = frame.origin
        height = op[2]
        return ("ground", (x + dx, z + dz),
                None if height is None else height + y)
    if kind == "paste":
        raise NBTError("a paste is turned as it is stamped, by "
                       "Builder.stamp(rotate=, mirror=), never remapped "
                       "through a frame")
    raise NBTError(f"a plan op is a set, a fill, a place or a ground, not "
                   f"{kind!r}")


class Framed:
    """A :class:`~heaton_voxel.craft.build.Builder` seen through a
    :class:`~heaton_voxel.craft.geometry.Frame`
    (:meth:`~heaton_voxel.craft.build.Builder.framed`).

    :meth:`set`, :meth:`place` and :meth:`fill` take a component's own
    ``(across, up, out)`` cells and its states as designed, facing the
    frame's identity (out is +z, south); the frame turns the cells and the
    states together, and the Builder writes the result through its own
    ``_commit``, as one op. So every check a Builder write passes, a framed
    write passes in the same order -- the block as the caller wrote it
    first (the parser's ``ValueError``, a ``TypeError`` for what is not a
    block, one :class:`~heaton_voxel.craft.errors.IllegalBlock` for one the
    game lacks), then the turn (a state the schematic vocabulary cannot
    turn, under ``allow=``, is
    :class:`~heaton_voxel.craft.errors.Refused`, naming the state and the
    frame), then the chunks, heights and guard over the **world** cells --
    each of the Builder's own refusals opened by the local cell and the
    frame. A written cell is journaled at its world cell, as a Builder
    write is, and :meth:`~heaton_voxel.craft.build.Builder.finish` settles
    it like any other. Writing a component through a frame is byte for byte
    writing it by hand at ``frame.cell(...)`` with ``frame.state(...)``,
    except that a property the caller leaves at its default turns too
    (:func:`_frame_state`): a bare ``minecraft:wall_torch`` is the torch
    facing north, hung on the block to its south, and through any frame it
    hangs on the block the frame puts there.

    A :class:`~heaton_voxel.craft.style.Mix` is picked at the **world**
    cell, as the Mix picks it there, and the pick is then turned
    (``frame.state(mix.pick(x, y, z))``, its defaults with it), so a
    component framed four ways lays the same stones, each turned, as the
    same cells would get unframed. A list mix a writer draws from the ``rng`` is turned entry by
    entry and drawn as it would be.

    The view holds no state of its own: it shares the Builder's journal,
    stage, ``rng``, ground, keep-outs and guard, so a frame can be taken
    up and put down at will. Frames compose through
    :meth:`~heaton_voxel.craft.geometry.Frame.within`
    (``b.framed(inner.within(outer))``); a stamp turns by its own
    ``rotate=`` and ``mirror=``.
    """

    __slots__ = ("_builder", "_frame")

    def __init__(self, builder, frame: Frame):
        if not isinstance(frame, Frame):
            raise NBTError(f"a Builder is framed by a craft.geometry.Frame, "
                           f"not {frame!r}")
        self._builder = builder
        self._frame = frame

    @property
    def builder(self):
        """The Builder every write is made by."""
        return self._builder

    @property
    def frame(self) -> Frame:
        """The frame every cell and state is turned by."""
        return self._frame

    def __repr__(self) -> str:
        return f"<Framed {self._frame!r}>"

    def set(self, across: int, up: int, out: int, block) -> None:
        """Write one state at a local cell, as
        :meth:`~heaton_voxel.craft.build.Builder.set` writes it at the
        world cell the frame puts it in, turned."""
        cell = (across, up, out)
        self._write(("set", cell, block), f"set at {cell}")

    def place(self, across: int, up: int, out: int, block) -> None:
        """Place with item semantics at a local cell, as
        :meth:`~heaton_voxel.craft.build.Builder.place` places it: a bed's
        head lands beside its foot where the turned bed points, and a
        door's hinge -- left, if the state does not say -- changes sides in
        a mirrored frame. A Mix is a ``TypeError``, as it is there."""
        state = _state(block)
        cell = (across, up, out)
        self._write(("place", cell, state), f"place at {cell}")

    def fill(self, corner1, corner2, block) -> None:
        """Fill the box two local corners span, as
        :meth:`~heaton_voxel.craft.build.Builder.fill` fills the world box
        the frame turns it into -- one fill, checked once; a Mix swept
        cell by cell over the world box, each cell its turned pick."""
        self._builder._alive()
        corner1, corner2 = _local(corner1, "a corner"), _local(corner2, "a corner")
        self._write(("fill", corner1, corner2, block),
                    f"fill {corner1}..{corner2}")

    def get(self, across: int, up: int, out: int):
        """The block at a local cell in the frame's own terms
        (:meth:`~heaton_voxel.craft.geometry.Frame.unstate`), or None where
        there is none to read."""
        found = self._builder._peek(self._frame.cell(across, up, out))
        return (None if found is None
                else _frame_state(self._frame, found, inverse=True))

    def _write(self, op, doing: str) -> None:
        builder = self._builder
        what = f"{doing} in {self._frame!r}"
        builder._alive()
        # The block as the caller wrote it, before it is turned: a refusal
        # names the state the caller named.
        ops = [op]
        builder._legal(ops, builder._parse(ops), what=what)
        builder._commit([_frame_op(self._frame, op)], what=what)
