"""Roofs: one rule, rising from the outline.

:class:`Roof`, the rule -- a half-block rise over distance from the
outline -- with :func:`rise_of`, which turns a vault's profile into one;
:class:`Wings`, several Roofs joined where they meet; and the writers
that lay them: :meth:`RoofMixin.roof`, and :meth:`RoofMixin.gable_roof`,
the stepped gable every build has used, which is a Roof at pitch 2 with
plank ends.

**A roof over a rectangle, a disc or a plan is this rule at one
setting.** A gable is a rise from two opposite eaves, a shed from one; a
hip, a pyramid, a mansard, tiers and an eave that turns up are rises from
a square outline, ring by ring; a cone, a dome and a spire are the same
from a round one (:func:`~heaton_voxel.craft.geometry.ring`'s rings).
Of the roofs the builds wrote by hand, the Roman town's hip, low hip,
pediment and cones, JeffWorld's baths dome and macellum cone, the castle
example's spire, the suburban house's rear gable and :meth:`gable_roof`
itself come out of it cell for cell -- ``tests/test_roof.py`` holds each
to the script that wrote it, the dome but for its finial, the spire but
for its mast and the rear gable but for its two verge columns -- and so
does one course of a :class:`Vault`, whose profile turned outside in is
a rise (:func:`rise_of`). The Roman shed is the same cells with its
stairs turned the other way, on purpose: they faced down its slope. The
suburban house's main roof, a cross gable, is three of them joined: its
main roof, its rear gable and its front bay as :class:`Wings`, the shape
that stands in JeffWorld but for the stone Jeff laid in two rows of the
south slope, the rear gable's verges and the inner-corner stairs of its
valleys. One is not this rule at all: the Roman ``common.dome``, whose
height follows the exact radius rather than the ring and stands round
an oculus. A mansard, tiers and an eave turned up are settings the rule
takes, checked as such; no script wrote one.

**A plan that is no rectangle asks two questions.** A hip over it is one
Roof over its *footprint*, a set of columns: a column's ring is how many
steps a king takes to walk out of the plan
(:func:`~heaton_voxel.craft.field.chessboard`), which runs a straight
45-degree valley up from every reentrant corner, where a Euclidean
distance puts a bump in it. A hole in the footprint is a courtyard the
roof slopes down into, and an overhang that would close one over is
refused. A plan of gables is several roofs, since no one ridge serves an
L or a T: :class:`Wings` lays each wing over its own rectangle, every
column taking the highest top any wing gives it, so the valleys fall
where the planes cross and a wing's end is closed only where it faces out
of the plan. Each wing keeps what it is alone where the join would undo
it: its building stands under its walls from its own springing, which no
column reaches below, and its eave, roofed over by a higher wing, still
closes its attic. The two agree on a hip wherever its wings overlap to
each other's far edges, and part where two only touch: the join leaves a
trough there, and the footprint's distance does not.

The heights are in half blocks, as a vault's are: a bottom slab and a
top slab are two heights inside one block, so a rise can be half a block
a course as well as a whole one, and a crown on a half block ends in a
slab. Each column is laid down from its top to the top of the lowest
neighbor beside it -- the vault's gapless rule, by the same loop
(``vault._lay_column``) -- and a top course that falls a block or more
becomes a stair facing up the slope: a stair's full side is the way it
faces, so it shows its low half to the course below, as a gable's do.

**The attic is a choice the caller makes.** ``attic="hollow"`` leaves the
space under the shell as it is, and a block fills it: two blocks of dark
enclosed air under a roof the size of a house is a mob farm with a house
under it, which is why the suburban house's rear attic was filled, and
why no default makes the choice for anybody.

There is no ``Roof.read``. A ring roof is never continued, and a roof
read back off a building reproduces only when its outline is known
exactly and nothing has been cut through it -- which rules out the
hand-built roofs a read would be wanted for; a one-axis odd-span gable is
already readable, and continuable, as a :class:`Vault`. :meth:`Roof.verify`
holds a roof to what stands, which is the half of a read that is sound.

Part of :mod:`heaton_voxel.craft.build`. The writers plan their call
whole and hand it to the Builder's ``_commit``.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet

from ...blocks import BlockState
from ...nbt import NBTError
from ..blockinfo import is_block, material_of
from ..errors import Refused
from ..field import chessboard
from ..geometry import CLOCKWISE, COMPASS, OPPOSITE, disc, heading_of, ring
from ..style import FROM_PALETTE, Material, Mix
from ._common import _empty, _occupancy, _same_crown, _Slot, _state, _whole
from .vault import _close_column, _lay_column


#: What ``attic=`` says for an attic left as it is.
HOLLOW = "hollow"

#: The two words along each axis, the pair a crown is judged by.
_PAIRS = tuple((word, OPPOSITE[word]) for word in CLOCKWISE[:2])


def _ints(values, what: str, *, least: int, none: bool = False) -> tuple:
    """``values`` as a non-empty tuple of whole numbers of at least
    ``least`` (or None, where ``none``), or an NBTError naming ``what``."""
    if isinstance(values, (str, bytes)) or values is None:
        raise NBTError(f"{what} is a sequence of whole numbers, not {values!r}")
    try:
        out = tuple(values)
    except TypeError:
        raise NBTError(f"{what} is a sequence of whole numbers, not "
                       f"{values!r}") from None
    if not out:
        raise NBTError(f"{what} has no entries")
    for value in out:
        if value is None and none:
            continue
        if (isinstance(value, bool) or not isinstance(value, int)
                or value < least):
            raise NBTError(f"{what} takes whole numbers of at least {least}"
                           f"{' or None' if none else ''}, got {value!r} in "
                           f"{out!r}")
    return out


def rise_of(profile, *, eave: int = 2) -> tuple[int, ...]:
    """A :class:`Vault` profile turned outside in: the rise a :class:`Roof`
    takes (``rise=``) for the same section.

    A profile is how far the shell has fallen below the crown at each
    block outward, crown first; a rise is how high the shell stands above
    the floor of ``base_y`` at each ring inward, eave first, in the same
    half blocks. So ``rise[d] = eave + profile[-1] - profile[-1 - d]``:
    the outermost course stands ``eave`` half blocks up -- 2, a whole
    block, by default -- and the crown ``profile[-1]`` above that.
    ``rise_of(arch_profile(r, h))`` is a dome, and
    ``rise_of(pointed_arch_profile(r, h))`` a Gothic spire.

    A crown stands on a half block when ``eave + profile[-1]`` is odd, as
    every vault's does; one on a whole block is lowered to a slab by a
    ``crown="slab"`` roof. Refused (an ``NBTError``): a profile that does
    not start at 0 or that climbs outward, and an eave under one half
    block.
    """
    drops = _ints(profile, "a profile", least=0)
    if drops[0] != 0:
        raise NBTError("a profile is the drop below the crown at each block "
                       "outward, so it starts at 0")
    if any(b < a for a, b in zip(drops, drops[1:])):
        raise NBTError(f"the profile climbs outward at some point ({drops}); "
                       f"turned outside in it would be no roof")
    eave = _whole(eave, "eave")
    if eave < 1:
        raise NBTError(f"an eave stands at least half a block up, got "
                       f"eave={eave}")
    last = drops[-1]
    return tuple(eave + last - drops[-1 - d] for d in range(len(drops)))


def _role(given, arg: str, form: str, *, mixes: bool, none: bool):
    """One material of a :class:`Roof`, as the rule holds it: a
    :class:`~heaton_voxel.blocks.BlockState`, a
    :class:`~heaton_voxel.craft.style.Mix` (a whole-block role only,
    picked at each world cell), or None (where none means none).

    Named at the call, never a palette's (``FROM_PALETTE`` is a
    ``TypeError``, as is None where a block is written and a Mix where the
    rule spells a facing or a type onto it). A
    :class:`~heaton_voxel.craft.style.Material` is its ``form`` -- one
    family roofs the whole course -- and one without it an
    :class:`~heaton_voxel.craft.errors.IllegalBlock`. A stair or slab role
    given a block the game has of another shape is an ``NBTError``; one
    the game does not have is left for the write's own check to refuse,
    as every plan's is."""
    if given is FROM_PALETTE:
        raise TypeError(
            f"Roof({arg}=) is named at the call, not read from a Builder's "
            f"palette: a Roof is made without one. Pass the block, or "
            f"b.palette[role] for a palette's")
    if given is None:
        if none:
            return None
        raise TypeError(f"Roof({arg}=None) names no block, and a roof writes "
                        f"one there")
    if isinstance(given, Mix):
        if mixes:
            return given
        raise TypeError(
            f"Roof({arg}=) is one block, which the roof spells a "
            f"{'facing' if form == 'stairs' else 'type'} onto, and {given!r} "
            f"is a Mix, many blocks picked by position: pick one with "
            f".pick(x, y, z), or name the block")
    if isinstance(given, Material):
        return _state(given.form(form))
    if not isinstance(given, (str, BlockState)):
        raise TypeError(f"Roof({arg}=) is a block -- an id, a BlockState, a "
                        f"Material{' or a Mix' if mixes else ''} -- not "
                        f"{given!r}")
    state = _state(given)
    if form in ("stairs", "slab") and is_block(state.name):
        shape = material_of(state.name)[1]
        if shape != form:
            if form == "stairs":
                raise NBTError(
                    f"Roof({arg}=) is a stair block, whose facing and half "
                    f"the roof sets, and {state.name} is not one; pass "
                    f"stair=None for a roof that steps square")
            raise NBTError(
                f"Roof({arg}=) is a slab, the half course a roof is laid "
                f"in where it stands half a block up, and {state.name} is "
                f"not one; pass slab=None for a roof of whole blocks")
    return state


def _picked(value, cell) -> BlockState:
    """A planned value as it is written at ``cell``: a Mix's pick there,
    as a state; a state as it is."""
    if isinstance(value, Mix):
        return _state(value.pick(*cell))
    return value


class _Layout:
    """A roof laid out -- a :class:`Roof` over one outline, or
    :class:`Wings` over theirs: ``cells``, every
    ``(cell, value)`` in write order, a value a state or a Mix not yet
    picked; how many are ``stairs`` and how many ``closed`` (end walls and
    attic fill); the ``hollow`` cells a hollow attic leaves, in order; and
    the ``springing``."""

    __slots__ = ("cells", "stairs", "closed", "hollow", "springing")

    def __init__(self, cells, stairs, closed, hollow, springing):
        self.cells = cells
        self.stairs = stairs
        self.closed = closed
        self.hollow = hollow
        self.springing = springing

    def picked(self) -> list:
        return [(cell, _picked(value, cell)) for cell, value in self.cells]


class _Wing:
    """One :class:`Roof` over one outline, as :func:`_lay` reads it
    (:meth:`Roof._wing`): the ``roof``; ``columns``, every column of the
    roof -- the walls widened by the overhang -- with its ring distance,
    or ``{eave: distance}`` on an eaves roof; ``walls``, the outline's own
    columns, which the attic fills and the end planes close; ``center2``,
    the outline's center doubled (so an even rectangle's stays whole),
    which a hip corner faces by; ``runs``, a footprint's instead -- how
    far the roof runs unbroken along x and along z through each column --
    and None while there is a center; ``verges``, the two words an eaves
    roof's end planes face (none for rings); ``along``, the axis the
    ridge runs along, which orders the courses; ``ring_of``, each
    column's ring -- the nearest eave's distance on an eaves roof;
    ``raw``, each column's top in half blocks as the rise gives it; and
    ``present``, the rings it has."""

    __slots__ = ("roof", "columns", "walls", "center2", "runs", "verges",
                 "along", "ring_of", "raw", "present")

    def __init__(self, roof):
        self.roof = roof
        self.columns = self.walls = self.center2 = self.runs = None
        self.verges = ()
        self.along = "x"
        self.ring_of = self.raw = self.present = None


class Roof:
    """A roof: one half-block rise over distance from its outline.

    Exactly one of ``eaves`` and ``rings`` says what the distance is from:

    * ``eaves`` -- one compass word, a shed rising away from that edge, or
      two opposite ones, a gable whose ridge runs between them. The other
      two edges are verges: the roof stops there, and the end planes of
      the outline are what ``close_ends`` closes. Two eaves may rise
      differently (``rise`` a dict, one tuple per eave), and then the two
      slopes meet where their planes cross.
    * ``rings`` -- ``"square"``, every edge an eave and the distance the
      ring of the rectangle a cell is on (a hip, a pyramid, a mansard,
      tiers), or, over a footprint, how many steps a king takes to walk
      out of the plan; or ``"round"``, the rings of
      :func:`~heaton_voxel.craft.geometry.ring` about the outline's center
      (a cone, a dome, a spire).

    The rise is in **half** blocks above the floor of ``base_y``, ring by
    ring from the outline inward: 2 is a whole block at ``base_y``, 1 a
    bottom slab there, and more than 2 lifts the eave off it -- a pitch
    always starts at 2, so ``base_y`` is its eave course. ``pitch`` (2
    when neither is given) climbs that many half blocks a block, from 2 at
    the eave; a sequence of pitches is a running sum, and the roof is flat
    past its end. ``rise`` gives the tops outright, each at least 1, flat
    past the last, and may fall and climb again: ``(2, 6, 10)`` is a
    mansard with a flat top, two blocks a ring from an eave course at
    ``base_y``, and ``(4, 2, 4, 6, 8)`` turns its eaves up, the outer ring
    lifted a block over the valley inside it. ``overhang`` widens the
    outline on every side -- a disc's radius -- before the roof is laid.

    ``crown="slab"`` (the default) ends a ridge or an apex standing on a
    whole block in a bottom slab half a block lower -- what
    :meth:`Builder.gable_roof`, a vault, the castle's spire and the Roman
    cones all do -- unless that would leave it no higher than a neighbor
    below it; ``"block"`` takes the rise as it is. A cell is a crown when
    its neighbors both ways along x or along z are lower, or both outside
    the roof past an eave; an eave turned up is no crown, and nor is a
    cell where ridges meet -- an L's corner, a cross's crossing -- which
    has no two opposite neighbors lower, so it stands half a block over
    the slab ridges running into it, as a peaked ring's corners do over
    its sides.

    Each column is laid from its top down to the top of the lowest
    neighbor beside it, so the shell has no gap seen from anywhere; an
    eave is laid one whole block deep, a plateau one piece, and ``depth``
    (half blocks per ring from the outline, None for the rule) says
    otherwise ring by ring. Its materials each have a job, and the first
    three are always named -- a Roof has no taste of its own:

    * ``block``, a whole course;
    * ``slab``, the half course: a crown on a half block, a top slab where
      half a block is left to lay, the doubled slab a closure makes of a
      top slab over a wall. ``None`` for a roof that never needs one --
      a whole-block rise with ``crown="block"`` -- and a roof that does
      is refused, naming the first cell the write would need it in;
    * ``stair``, the top course of a column falling a whole block or more
      to the next, facing up the slope -- its full side uphill, its low
      half to the course below -- ``half=bottom``; ``None`` steps square.
      A corner falling two ways is a hip: ``hip``'s block where one is
      given (rings only), else a stair facing up its slope, away from
      whichever of its two falls lies along the axis the center lies more
      along -- exactly diagonal goes to z
      (``geometry.heading_of(ties="z")``). That is toward the center
      wherever the corner falls outward, as a hip's and a peaked ring's
      do, and a corner of the outline faces the center too, or away from
      it on an eave turned up -- a ring over a lower ring inside it. A
      footprint has no center to face -- an L's lies outside it -- so
      there a corner faces up its slope along whichever axis the roof
      runs longer through it, a tie to z: on a rectangle the same choice,
      since a ring's corner lies more along the axis the ring is longer
      on. Its eave is turned up where the column diagonally inside the
      corner is the next ring in and lower -- the ring a rectangle asks
      after, which on a plan may lie anywhere but there. Its corner
      ``shape`` is ``finish()``'s to derive;
    * ``springer``, the whole courses of a column ``depth`` thickens past
      its reach (``block`` when left out); an eave is never one;
    * ``close_ends`` (eaves only), the block the outline's two end planes
      are closed with, from the springing up to the shell; left out, or
      ``False``, the ends are open.

    Every material is a block -- an id or a
    :class:`~heaton_voxel.blocks.BlockState` -- or a
    :class:`~heaton_voxel.craft.style.Material`, which is its form for the
    role, so one family can roof the whole course
    (``block=m, stair=m, slab=m``). A
    :class:`~heaton_voxel.craft.style.Mix` is taken where the role is a
    whole block -- ``block``, ``springer``, ``hip``, ``close_ends`` and
    the attic -- and picked at each world cell as the roof is planned, so
    what is planned is what is written; where the rule spells a facing or
    a type onto it, a Mix is a ``TypeError``. A stair or slab role given a
    block of another shape is an ``NBTError``; an id the game lacks is the
    write's :class:`~heaton_voxel.craft.errors.IllegalBlock`.

    The rule is held apart from the blocks, as a :class:`Vault`'s is:
    :meth:`plan` lays it over an outline without reading a world, and
    :meth:`verify` holds it to a roof that stands. Refused as the rule is
    made (an ``NBTError``): both or neither of ``eaves`` and ``rings``,
    an unknown or adjacent pair of eaves, ``pitch`` and ``rise`` together,
    a pitch under 0 or a rise under 1, ``hip`` on an eaves roof,
    ``close_ends`` on rings or of air.
    """

    __slots__ = ("eaves", "rings", "overhang", "crown", "block", "slab",
                 "stair", "springer", "hip", "depth", "close_ends", "_rise")

    def __init__(self, *, eaves=None, rings=None, pitch=None, rise=None,
                 overhang: int = 0, crown: str = "slab", block, slab, stair,
                 springer=None, hip=None, depth=None, close_ends=None):
        if (eaves is None) == (rings is None):
            raise NBTError("a roof rises from its eaves or from rings round "
                           "its outline: pass exactly one of eaves= and "
                           "rings=")
        if eaves is not None:
            words = (eaves,) if isinstance(eaves, str) else _words(eaves)
            for word in words:
                if not isinstance(word, str) or word not in COMPASS:
                    raise NBTError(f"an eave is one of {tuple(COMPASS)}, got "
                                   f"{word!r}")
            if len(words) not in (1, 2):
                raise NBTError(f"a roof has one eave (a shed) or two (a "
                               f"gable), got {words!r}")
            if len(words) == 2 and OPPOSITE[words[0]] != words[1]:
                raise NBTError(
                    f"a gable's two eaves face each other across its ridge, "
                    f"and {words[0]} and {words[1]} do not: a roof with eaves "
                    f"on adjacent sides is a hip, rings='square'")
            self.eaves = words
            self.rings = None
        else:
            if rings not in ("square", "round"):
                raise NBTError(f"rings is 'square' or 'round', got {rings!r}")
            self.eaves = None
            self.rings = rings
        if pitch is not None and rise is not None:
            raise NBTError("pitch and rise say the same thing two ways: pass "
                           "one of them")
        keys = self.eaves if self.eaves is not None else (None,)
        if isinstance(rise, dict):
            if self.eaves is None:
                raise NBTError("a rise per eave is for an eaves roof; rings "
                               "rise as one")
            if set(rise) != set(self.eaves) or len(rise) != len(self.eaves):
                raise NBTError(f"a rise per eave names each eave once, "
                               f"{self.eaves!r}, got {tuple(rise)!r}")
            self._rise = {key: (_ints(rise[key], f"rise[{key!r}]", least=1), 0)
                          for key in keys}
        elif rise is not None:
            tops = _ints(rise, "rise", least=1)
            self._rise = dict.fromkeys(keys, (tops, 0))
        else:
            pitch = 2 if pitch is None else pitch
            if isinstance(pitch, bool) or not isinstance(pitch, int):
                steps = _ints(pitch, "pitch", least=0)
                tops = [2]
                for step in steps:
                    tops.append(tops[-1] + step)
                spec = (tuple(tops), 0)
            else:
                if pitch < 0:
                    raise NBTError(f"a roof climbs from its eaves, never "
                                   f"falls: pitch={pitch}; tops that fall "
                                   f"and climb again are rise=")
                spec = ((2,), pitch)
            self._rise = dict.fromkeys(keys, spec)
        self.overhang = _whole(overhang, "overhang")
        if self.overhang < 0:
            raise NBTError(f"an overhang widens the outline, got "
                           f"overhang={overhang}")
        if crown not in ("slab", "block"):
            raise NBTError(f"crown is 'slab' or 'block', got {crown!r}")
        self.crown = crown
        self.depth = (None if depth is None
                      else _ints(depth, "depth", least=1, none=True))
        if hip is not None and self.rings is None:
            raise NBTError("hip= is the block a hip roof's corners take; an "
                           "eaves roof has no hips")
        if close_ends is not None and self.eaves is None:
            raise NBTError("close_ends= closes the two end planes of an "
                           "eaves roof; a ring roof has no ends -- fill under "
                           "it with attic=")
        self.block = _role(block, "block", "block", mixes=True, none=False)
        self.slab = _role(slab, "slab", "slab", mixes=False, none=True)
        self.stair = _role(stair, "stair", "stairs", mixes=False, none=True)
        self.springer = _role(springer, "springer", "block", mixes=True,
                              none=True)
        self.hip = _role(hip, "hip", "block", mixes=True, none=True)
        if close_ends is True:
            raise NBTError("close_ends is the block a roof's end planes are "
                           "closed with, not a switch: name it, or leave it "
                           "out for open ends")
        self.close_ends = _role(None if close_ends is False else close_ends,
                                "close_ends", "block", mixes=True, none=True)
        if isinstance(self.close_ends, BlockState) and _empty(self.close_ends):
            raise NBTError(f"an end wall of {self.close_ends.name} closes "
                           f"nothing; leave close_ends out for open ends")

    # -- the rule --------------------------------------------------------

    def rise_at(self, d: int, eave: str | None = None) -> int:
        """The rise ``d`` rings in from the outline -- from the named
        ``eave``, for an eaves roof -- in half blocks above the floor of
        ``base_y``: the rise given, flat past its end, or 2 plus the
        pitch climbed."""
        d = _whole(d, "d")
        if d < 0:
            raise NBTError(f"a ring is counted inward from the outline, got "
                           f"d={d}")
        if self.eaves is not None:
            if eave is None and len(self.eaves) == 1:
                eave = self.eaves[0]
            if eave not in self._rise:
                raise NBTError(f"name the eave the rise is counted from, one "
                               f"of {self.eaves!r}, got {eave!r}")
        else:
            eave = None
        tops, slope = self._rise[eave]
        if d < len(tops):
            return tops[d]
        return tops[-1] + slope * (d - len(tops) + 1)

    def plan(self, outline, base_y: int, *, attic,
             attic_floor: int | None = None) -> dict[tuple[int, int, int], BlockState]:
        """Every cell the roof writes over ``outline``, with the state it
        writes there, in the order it is written. Pure: it reads no world,
        and a Mix is picked at each cell as the write will pick it.

        ``outline`` is ``(x0, z0, x1, z1)``, inclusive, for an eaves or a
        square roof -- corners in either order -- and ``(cx, cz, radius)``
        for a round one, the disc of
        :func:`~heaton_voxel.craft.geometry.disc` (half rounding), as the
        castle's towers and the Roman city's are drawn. A square roof
        also takes a *footprint*, any plan: a set of ``(x, z)`` columns,
        or a list or a tuple of them (what ``geometry.disc`` returns
        among them) -- the forms cannot be confused, since a footprint's
        members are pairs and the others' are whole numbers, but for one:
        a list or a tuple of two columns reads as a rectangle's two
        corners as plainly, so where the two readings are different roofs
        it is refused, naming both forms -- two lone columns are a set.
        It is the walls' outline, which ``overhang`` widens, a footprint
        as a king walks: every column within ``overhang`` of a wall's both
        ways. A
        hole in a footprint is a courtyard, left open with the roof
        sloping down into it, and an overhang that would roof one over
        entirely is refused, naming its first column: closing an opening
        somebody drew is no roof's to decide. An eaves or a round roof
        refuses a footprint -- a plan of gables or of cones is
        :class:`Wings` -- as it does an empty one or a member that is no
        pair of whole numbers. ``base_y`` is the block the rise is
        counted from: the eave course of a rise starting at 2, as every
        pitch does, and the course under the eave of one starting higher.

        ``attic`` is required, and is ``"hollow"`` -- nothing written under
        the shell -- or a block, a Material or a Mix, filled under every
        column of the outline from ``attic_floor`` (the springing when
        left out: the lowest block any column of the shell reaches down
        into) up to the shell, a top slab over it made whole. The end
        planes ``close_ends`` closes are closed from the springing either
        way.

        The order is total: course by course up from the eave, each course
        along the ridge -- x for rings and for north and south eaves, z for
        west and east ones -- and across it, and the closures under that
        course after it, across and then along. That is
        :meth:`Builder.gable_roof`'s own order, cell for cell.
        """
        return dict(self._layout(outline, base_y, attic, attic_floor).picked())

    def verify(self, save, outline, base_y: int, *, attic,
               attic_floor: int | None = None, dimension: str | None = None,
               missing_ok: bool = False) -> list[str]:
        """Every cell of the roof :meth:`plan` lays over ``outline`` that
        does not hold what the rule says, in plan order: the check to run
        on a roof somebody built before trusting a rule to describe it.
        Complaints name the cell and both states.

        A stair's ``shape`` is not compared -- a block update derives it
        -- and a state is the game's, a property left out its default
        (:meth:`~heaton_voxel.blocks.BlockState.canonical`). A hollow attic
        is not looked in: it is the space a roof leaves, whatever stands
        there. ``missing_ok`` forgives air -- any of the game's airs --
        where the rule wants a block, for a roof not yet finished.
        """
        return _verify(self.plan(outline, base_y, attic=attic,
                                 attic_floor=attic_floor),
                       save, dimension, missing_ok)

    # -- laying it out -----------------------------------------------------

    def _wing(self, outline, base_y) -> _Wing:
        """This roof over ``outline``, its rise counted from ``base_y``, as
        :func:`_lay` reads it: every column with its ring distance -- for
        an eaves roof, ``{eave: distance}`` -- and its top as the rise
        gives it, the walls' columns, the outline's center doubled (so an
        even rectangle's stays whole) or, over a footprint, the runs its
        hip corners face by instead, the words its ends face and the axis
        its ridge runs along. An outline the rule does not take is refused
        here: a footprint's courtyard an overhang would close among
        them."""
        base_y = _whole(base_y, "base_y")
        o = self.overhang
        wing = _Wing(self)
        members = _plan_of(outline)
        if members is not None:
            _not_corners(outline, members)
            if self.eaves is not None:
                raise NBTError(
                    f"a footprint is roofed by a hip, rings='square': an "
                    f"eaves roof rises from one edge of a rectangle or two "
                    f"opposite ones, and a plan that is no rectangle has "
                    f"more edges than that. Make "
                    f"each wing an eaves Roof over its own rectangle and "
                    f"join them with Wings([(roof, (x0, z0, x1, z1), "
                    f"base_y), ...]); got the eaves {'/'.join(self.eaves)} "
                    f"and {len(members)} columns")
            if self.rings == "round":
                raise NBTError(
                    f"a round roof rises over a disc, (cx, cz, radius), and "
                    f"a footprint is no disc: a plan's hip is "
                    f"rings='square' over the footprint, and round roofs "
                    f"over several discs are joined with Wings([(roof, "
                    f"(cx, cz, radius), base_y), ...]); got "
                    f"{len(members)} columns")
            walls = _plan_columns(members)
            roofed = _dilate(walls, o)
            for hole in _holes(walls):
                if hole <= roofed:
                    first = min(hole)
                    raise NBTError(
                        f"the footprint is open at {first} -- a courtyard "
                        f"of {len(hole)} column{'s' if len(hole) > 1 else ''} "
                        f"inside it -- and an overhang of {o} roofs it "
                        f"over: a roof slopes down into an opening the plan "
                        f"leaves, and never closes one. Widen the opening "
                        f"or give a smaller overhang to keep it, or put its "
                        f"columns in the footprint to roof it over")
            wing.columns = {cell: distance - 1
                            for cell, distance in chessboard(roofed).items()}
            wing.walls = walls
            wing.runs = _runs(roofed)
        elif self.rings == "round":
            try:
                cx, cz, r = outline
            except (TypeError, ValueError):
                raise NBTError(f"a round roof's outline is (cx, cz, radius), "
                               f"got {outline!r}") from None
            for value, what in ((cx, "cx"), (cz, "cz"), (r, "the radius")):
                _whole(value, what)
            if r < 0:
                raise NBTError(f"a radius is not negative, got {r}")
            columns: dict = {}
            for k in range(r + o + 1):
                for cell in ring(k, center=(cx, cz)):
                    columns[cell] = r + o - k
            wing.columns = columns
            wing.walls = frozenset(disc(r, center=(cx, cz)))
            wing.center2 = (2 * cx, 2 * cz)
        else:
            try:
                x0, z0, x1, z1 = outline
            except (TypeError, ValueError):
                raise NBTError(f"an outline is (x0, z0, x1, z1), inclusive, "
                               f"got {outline!r}") from None
            for value, what in ((x0, "x0"), (z0, "z0"), (x1, "x1"),
                                (z1, "z1")):
                _whole(value, what)
            x0, x1 = sorted((x0, x1))
            z0, z1 = sorted((z0, z1))
            X0, Z0, X1, Z1 = x0 - o, z0 - o, x1 + o, z1 + o
            columns = {}
            for x in range(X0, X1 + 1):
                for z in range(Z0, Z1 + 1):
                    if self.rings == "square":
                        columns[(x, z)] = min(x - X0, X1 - x, z - Z0, Z1 - z)
                    else:
                        edge = {"north": z - Z0, "south": Z1 - z,
                                "west": x - X0, "east": X1 - x}
                        columns[(x, z)] = {eave: edge[eave]
                                           for eave in self.eaves}
            wing.columns = columns
            wing.walls = frozenset((x, z) for x in range(x0, x1 + 1)
                                   for z in range(z0, z1 + 1))
            wing.center2 = (X0 + X1, Z0 + Z1)
            if self.eaves is not None:
                if set(self.eaves) <= {"west", "east"}:
                    wing.along, wing.verges = "z", ("north", "south")
                else:
                    wing.verges = ("west", "east")

        # Tops, in half blocks, as the rise gives them.
        floor_y = 2 * base_y
        if self.rings is not None:
            wing.ring_of = wing.columns
            wing.raw = {c: floor_y + self.rise_at(d)
                        for c, d in wing.columns.items()}
        else:
            wing.ring_of = {c: min(ds.values())
                            for c, ds in wing.columns.items()}
            wing.raw = {c: floor_y + min(self.rise_at(d, eave)
                                         for eave, d in ds.items())
                        for c, ds in wing.columns.items()}
        wing.present = frozenset(wing.ring_of.values())
        return wing

    def _layout(self, outline, base_y, attic, attic_floor) -> _Layout:
        base_y = _whole(base_y, "base_y")
        attic = _attic(attic, attic_floor)
        return _lay((self._wing(outline, base_y),), attic)

    def __repr__(self) -> str:
        shape = (f"rings={self.rings}" if self.rings is not None
                 else f"eaves={'/'.join(self.eaves)}")
        name = (repr(self.block) if isinstance(self.block, Mix)
                else self.block.name.rsplit(":", 1)[-1])
        extra = f" overhang={self.overhang}" if self.overhang else ""
        return f"<Roof {shape}{extra} crown={self.crown} {name}>"


def _words(eaves) -> tuple:
    try:
        return tuple(eaves)
    except TypeError:
        raise NBTError(f"eaves is a compass word or two, got "
                       f"{eaves!r}") from None


def _key(cell, along: str, *, swap: bool = False) -> tuple:
    """A column's place in a course: ``(along, across)``, or ``(across,
    along)`` with ``swap``, which is how a closure under the course is
    ordered."""
    x, z = cell
    pair = (x, z) if along == "x" else (z, x)
    return (pair[1], pair[0]) if swap else pair


def _hip_facing(cell, center2, falls, *, upturned: bool) -> str | None:
    """Which way a hip cell's stair faces: up its slope, along the axis
    the outline's center lies more along -- exactly diagonal goes to z, as
    the castle breaks it (:func:`~heaton_voxel.craft.geometry.heading_of`,
    ``ties="z"``).

    A corner with ``falls`` -- the two neighbors it falls a block or more
    to -- faces away from the one on that axis: toward the center where it
    falls outward, as every roof climbing inward does and a ring that
    peaks does too, and away from it where it falls inward. Decided by the
    falls and not by the rise alone, which says a ring stands over the
    ring inside it and not which neighbors a corner has. A corner of the
    outline itself falls only off the roof's edge (``falls`` None), and
    touches only its own ring, so its neighbors say nothing about which
    way the ring turns: it faces the center, or away from it where the
    caller found the ring ``upturned`` -- standing over a ring inside it
    that exists, as an eave turned up does. ``center2`` is the center
    doubled, so an even rectangle's stays whole; a cell on it has no
    heading, and None."""
    dx, dz = center2[0] - 2 * cell[0], center2[1] - 2 * cell[1]
    if dx == 0 and dz == 0:
        return None
    inward = heading_of(dx, dz, ties="z")
    if falls is not None:
        return OPPOSITE[inward] if inward in falls else inward
    return OPPOSITE[inward] if upturned else inward


def _attic(attic, attic_floor) -> tuple:
    """``(hollow, filled, attic_floor)``: whether the attic is left hollow,
    the block filling it otherwise, and the block it is filled from (None
    for the springing). Required, and never None: an attic is a choice,
    hollow or filled."""
    if isinstance(attic, str) and attic == HOLLOW:
        hollow, filled = True, None
    elif attic is None:
        raise TypeError(
            "attic=None says nothing about the space under the roof: "
            "pass attic='hollow' to leave it as it is, or a block to "
            "fill it -- dark enclosed air under a roof is somewhere for "
            "monsters to spawn")
    else:
        filled = _role(attic, "attic", "block", mixes=True, none=False)
        if isinstance(filled, BlockState) and _empty(filled):
            raise NBTError(f"an attic of {filled.name} is a hollow one: pass "
                           f"attic='hollow'")
        hollow = False
    if attic_floor is not None:
        attic_floor = _whole(attic_floor, "attic_floor")
    return hollow, filled, attic_floor


def _neighbors(cell):
    x, z = cell
    for word, (dx, dz) in COMPASS.items():
        yield word, (x + dx, z + dz)


def _beside(cell, word) -> tuple:
    """The column beside ``cell`` the compass ``word`` names."""
    dx, dz = COMPASS[word]
    return (cell[0] + dx, cell[1] + dz)


def _shape(wings) -> tuple:
    """The tops ``wings`` give, joined: ``raw``, every column's highest
    top in half blocks as the rises give them; ``owner``, the first wing
    listed that gives it that top; ``outside``, the words it falls off the
    roof by -- its neighbor outside every wing, past an eave of the
    owner's; ``tops``, the tops with the crowns lowered; and ``lowered``,
    the crowns. A crown is decided on the tops as the rises give them,
    and the crowns are lowered together."""
    raw: dict = {}
    owner: dict = {}
    for wing in wings:
        for cell, top in wing.raw.items():
            if cell not in raw or top > raw[cell]:
                raw[cell] = top
                owner[cell] = wing

    outside = {}
    for cell in raw:
        rule = owner[cell].roof
        outside[cell] = frozenset(
            word for word, n in _neighbors(cell)
            if n not in raw
            and (rule.rings is not None or word in rule.eaves))

    tops = dict(raw)
    lowered = set()
    for cell, top in raw.items():
        if owner[cell].roof.crown != "slab":
            continue
        below = {word for word, n in _neighbors(cell)
                 if n in raw and raw[n] < top}
        off = outside[cell]
        crowned = any((a in below and b in below) or
                      (a in off and b in off) for a, b in _PAIRS)
        if (crowned and top % 2 == 0
                and all(top - 1 > raw[n] for word, n in _neighbors(cell)
                        if word in below)):
            lowered.add(cell)
    for cell in lowered:
        tops[cell] -= 1
    return raw, owner, outside, tops, lowered


def _lower(cell, raw, tops) -> dict:
    """``{word: top}``: the neighbors of ``cell`` standing lower than it."""
    top = tops[cell]
    return {word: tops[n] for word, n in _neighbors(cell)
            if n in raw and tops[n] < top}


def _thickness(cell, wing, top, lower, off, lowered) -> tuple:
    """``(given, reach, thickness)`` for a column ``wing`` lays: the depth
    its rule gives its ring (None for the rule), how far it falls to the
    lowest of the neighbors it reaches down to, ``lower`` (None with none
    lower), and how many half blocks deep it is laid -- down to that
    neighbor's top; an eave, falling off the roof by the words ``off``, a
    whole block; a lowered crown a half one and a plateau one piece."""
    rule = wing.roof
    d = wing.ring_of[cell]
    given = (rule.depth[d] if rule.depth is not None
             and d < len(rule.depth) else None)
    reach = top - min(lower.values()) if lower else None
    if given is not None:
        thickness = given
    elif lower:
        thickness = reach
    elif cell in lowered:
        thickness = 1
    elif off:
        thickness = 2
    else:
        thickness = 2 if top % 2 == 0 else 1
    return given, reach, thickness


def _alone(wing) -> tuple:
    """``(springing, floors, outside)``: ``wing`` laid alone -- the block
    level it reaches down to, each column's floor in half blocks, and the
    words each falls off its roof by. In :class:`Wings` the springing is
    where the building under the wing's walls stands from, so the block
    its ends are closed and its attic filled from, and a column on its
    edge keeps its floor."""
    raw, _, outside, tops, lowered = _shape((wing,))
    floors = {cell: tops[cell] - _thickness(cell, wing, tops[cell],
                                            _lower(cell, raw, tops),
                                            outside[cell], lowered)[2]
              for cell in raw}
    return min(floors.values()) // 2, floors, outside


def _turned_up(cell, falls, wing, d) -> bool:
    """Whether a corner of a footprint's edge, falling off the roof by the
    two words ``falls``, stands on an eave turned up: whether the column
    diagonally inside it, past both, is the next ring in and lower. A
    rectangle asks whether that ring exists at all, which is the same
    question there; a plan's next ring may lie anywhere but beside it."""
    rule = wing.roof
    inside = (cell[0] - sum(COMPASS[word][0] for word in falls),
              cell[1] - sum(COMPASS[word][1] for word in falls))
    return (wing.ring_of.get(inside) == d + 1
            and rule.rise_at(d) > rule.rise_at(d + 1))


def _lay(wings, attic) -> _Layout:
    """The roof ``wings`` make, laid out -- one :class:`_Wing` for a
    :class:`Roof`, one per wing for :class:`Wings` -- with ``attic``
    :func:`_attic`'s triple.

    Every column takes the highest top any wing gives it, and the first
    wing listed that gives it that top owns it: the owner's settings lay
    it. Then it is one roof: a column's neighbors are the union's, so each
    column is laid down to the top of its lowest neighbor whichever wing
    that is -- a wing's verge standing over a lower wing reaches down to
    that wing's top -- and where two wings' slopes cross, the valley is a
    column with no lower side neighbor, laid as a plateau is.

    Each wing keeps what it is alone (:func:`_alone`) where that would
    undo it. Under a wing's walls stands its building, from the springing
    the wing has laid alone -- the highest, where several wings' walls
    hold a column: no column over them reaches below it, and the wing's
    ends are closed and its attic filled, or counted hollow, from it.
    Over open ground, under no wing's walls, a column reaches down to its
    own wing's roof alone: past it, another wing's lower roof is the air
    beyond an eave. And a column a wing falls off its own roof from keeps
    the floor it has alone, whichever wing owns it -- the eave course
    that closes the wing's attic -- but inside another wing's walls,
    where that wing's roof closes the attic the two share.

    An end plane is closed only where it faces out of the union of the
    wings' walls -- a wing's end that runs into another is inside the
    roof and stays open -- with the first wing's block that closes it.
    One wing's springing is the whole roof's: the lowest block any column
    reaches down into, which is the springing a Wings reports too. The
    order is the one roof's, course by course along the first wing's
    ridge."""
    hollow, filled, attic_floor = attic
    along = wings[0].along
    raw, owner, outside, tops, lowered = _shape(wings)
    walls = set()
    for wing in wings:
        walls |= wing.walls

    # Where each wing's building stands from, under its walls, and the
    # floors of the eaves round each wing that close its attic: every
    # column it falls off its own roof from keeps the floor it has alone,
    # but where it stands inside another wing's walls, whose roof closes
    # the attic the two share. One wing's are the roof's own.
    under = keep = None
    if len(wings) > 1:
        alone = [_alone(wing) for wing in wings]
        under, keep = {}, {}
        inner = set()
        for wing, (level, _, edges) in zip(wings, alone):
            for cell in wing.walls:
                if cell not in under or level > under[cell]:
                    under[cell] = level
                if not edges[cell]:
                    inner.add(cell)
        for _, floors, edges in alone:
            for cell, words in edges.items():
                if (words and cell not in inner
                        and (cell not in keep or floors[cell] < keep[cell])):
                    keep[cell] = floors[cell]

    # Each column, laid from its top.
    laid: dict = {}
    stairs = 0
    lacking = []
    for cell in raw:
        wing = owner[cell]
        rule = wing.roof
        top = tops[cell]
        lower = _lower(cell, raw, tops)
        d = wing.ring_of[cell]
        reaches, off = lower, outside[cell]
        if under is not None and cell not in under:
            # Over open ground -- no wing's walls under it -- a column
            # reaches down to its own wing's roof alone: another wing's
            # lower roof beside it is the air past an eave, and nothing a
            # wall built down to it would close.
            reaches = {word: below for word, below in lower.items()
                       if _beside(cell, word) in wing.ring_of}
            off = off | frozenset(lower).difference(reaches)
        given, reach, thickness = _thickness(cell, wing, top, reaches, off,
                                             lowered)
        floor = top - thickness
        if keep is not None and cell in keep and floor > keep[cell]:
            # A wing's eave under another's roof still closes its attic.
            floor = keep[cell]
        if under is not None and cell in under and floor < 2 * under[cell]:
            # Over a wing's walls stands its building, from its springing.
            floor = 2 * under[cell]
        if rule.slab is None and (top % 2 or floor % 2):
            lacking.append((cell, top, floor))
            continue
        whole = (rule.springer if rule.springer is not None
                 and given is not None and reaches and given > reach
                 else rule.block)
        column = _lay_column(top, floor, whole, rule.slab)
        peak = max(column)
        if column[peak] is whole:
            if lower:
                falls = [word for word, below in lower.items()
                         if top - below >= 2]
            else:
                falls = [word for word in COMPASS if word in outside[cell]]
            ridge = any(OPPOSITE[word] in falls for word in falls)
            if len(falls) == 1 and rule.stair is not None:
                column[peak] = rule.stair.with_properties(
                    facing=OPPOSITE[falls[0]], half="bottom")
                stairs += 1
            elif len(falls) == 2 and not ridge:
                if wing.runs is None:
                    upturned = (not lower and rule.rings is not None
                                and d + 1 in wing.present
                                and rule.rise_at(d) > rule.rise_at(d + 1))
                    facing = _hip_facing(cell, wing.center2,
                                         falls if lower else None,
                                         upturned=upturned)
                else:
                    upturned = not lower and _turned_up(cell, falls, wing, d)
                    facing = _run_facing(wing.runs[cell], falls,
                                         edge=not lower, upturned=upturned)
                if rule.hip is not None:
                    column[peak] = rule.hip
                elif rule.stair is not None and facing is not None:
                    column[peak] = rule.stair.with_properties(
                        facing=facing, half="bottom")
                    stairs += 1
        laid[cell] = (column, floor)
    if lacking:
        # The first in write order: the lowest piece that needs one,
        # which is the top slab under a column before its crown.
        def needs(item):
            cell, top, floor = item
            if floor % 2:
                return floor // 2, "half a block is left to lay there"
            return top // 2, "its top is a bottom slab's"
        cell, top, floor = min(lacking, key=lambda item: (
            needs(item)[0], _key(item[0], along)))
        y, where = needs((cell, top, floor))
        raise NBTError(
            f"the roof needs a slab at {(cell[0], y, cell[1])} -- {where} "
            f"-- and was given slab=None: pass slab=, or crown='block' "
            f"for a crown taken as it rises, or a rise of whole blocks")

    springing = min(floor for _, floor in laid.values()) // 2

    # What closes under the shell: the end planes, then the attic.
    ends = {}
    for wing in wings:
        block = wing.roof.close_ends
        if block is None:
            continue
        steps = [COMPASS[word] for word in wing.verges]
        for x, z in wing.walls:
            if (x, z) not in ends and any((x + dx, z + dz) not in walls
                                          for dx, dz in steps):
                ends[(x, z)] = block
    closures = []
    hollow_cells = []
    for x, z in walls:
        column, _ = laid[(x, z)]
        low = min(column)
        base = springing if under is None else under[(x, z)]
        attic_start = base if attic_floor is None else attic_floor
        if (x, z) in ends:
            fill, start = ends[(x, z)], base
        elif filled is not None:
            fill, start = filled, attic_start
        else:
            if hollow:
                hollow_cells += [((y, 1) + _key((x, z), along, swap=True),
                                  (x, y, z))
                                 for y in range(attic_start, low)]
            continue
        for y, state in _close_column(low, column[low], start, fill,
                                      owner[(x, z)].roof.slab):
            if y == low:            # the lowest piece, made whole
                column[low] = state
            else:
                closures.append(((y, 1) + _key((x, z), along, swap=True),
                                  (x, y, z), state))

    # Shell cells first in each course, then what closes under it.
    entries = [((y, 0) + _key((x, z), along), (x, y, z), piece)
               for (x, z), (column, _) in laid.items()
               for y, piece in column.items()]
    entries += closures
    entries.sort(key=lambda entry: entry[0])
    hollow_cells.sort(key=lambda entry: entry[0])
    return _Layout([(cell, value) for _, cell, value in entries], stairs,
                   len(closures), [cell for _, cell in hollow_cells],
                   springing)


def _verify(plan: dict, save, dimension, missing_ok: bool) -> list[str]:
    """Every cell of ``plan`` that does not hold what it says, in plan
    order: :meth:`Roof.verify`'s and :meth:`Wings.verify`'s check."""
    wrong = []
    for cell, expect in plan.items():
        found = save.get_block(*cell, dimension=dimension)
        if found is None:
            wrong.append(f"{cell}: nothing to read -- the chunk is not "
                         f"generated, or y is outside the world")
        elif not _same_crown(found, expect):
            if missing_ok and _empty(found):
                continue
            wrong.append(f"{cell}: expected {expect}, found {found}")
    return wrong


def _plan_of(outline):
    """``outline``'s members when it is a footprint -- a set of ``(x, z)``
    columns, or a list or a tuple of them -- and None when it is one of
    the other two forms. A rectangle's members and a disc's are whole
    numbers and a footprint's are pairs, so the three cannot be taken for
    one another: a 4-tuple of pairs is a footprint and a 4-tuple of ints a
    rectangle. A set is a footprint whatever it holds, since a set of
    numbers has no order to be corners in."""
    if isinstance(outline, (str, bytes)):
        return None
    if isinstance(outline, AbstractSet):
        return tuple(outline)
    if isinstance(outline, (list, tuple)) and any(
            isinstance(member, (list, tuple)) for member in outline):
        return tuple(outline)
    return None


def _not_corners(outline, members) -> None:
    """Refuse a list or a tuple of two columns that read as two corners
    as well: ``((x0, z0), (x1, z1))`` is a rectangle's two corners as
    plainly as it is two columns, and the two are one roof only when the
    columns stand side by side (or are one). A set has no order to be
    corners in, so it is always a footprint."""
    if not isinstance(outline, (list, tuple)) or len(members) != 2:
        return
    pairs = [member for member in members
             if isinstance(member, (list, tuple)) and len(member) == 2
             and all(isinstance(v, int) and not isinstance(v, bool)
                     for v in member)]
    if len(pairs) != 2:
        return                      # not two columns: refused as such
    (ax, az), (bx, bz) = pairs
    if abs(ax - bx) + abs(az - bz) > 1:
        raise NBTError(
            f"{outline!r} is two corners or two columns, and the roofs "
            f"differ: a rectangle is (x0, z0, x1, z1), inclusive -- "
            f"({ax}, {az}, {bx}, {bz}) here -- and a footprint of these "
            f"two columns alone is a set, {{{(ax, az)}, {(bx, bz)}}}")


def _plan_columns(members) -> frozenset:
    """A footprint's columns, each an ``(x, z)`` pair of whole numbers (a
    bool is not one), or an NBTError naming the first that is not; an
    empty footprint is refused too, since it is nothing to roof."""
    if not members:
        raise NBTError("a footprint is the columns a roof stands over, and "
                       "this one has none")
    columns = set()
    for member in members:
        if (isinstance(member, (list, tuple)) and len(member) == 2
                and all(isinstance(v, int) and not isinstance(v, bool)
                        for v in member)):
            columns.add((member[0], member[1]))
        else:
            raise NBTError(f"a footprint is (x, z) columns, each a pair of "
                           f"whole numbers, and {member!r} is not one")
    return frozenset(columns)


def _dilate(columns, by: int) -> frozenset:
    """``columns`` widened by ``by`` on every side, as a king walks: every
    column within ``by`` of one of them both ways, x and z -- a
    rectangle's overhang, round any plan."""
    if not by:
        return frozenset(columns)
    reach = range(-by, by + 1)
    return frozenset((x + dx, z + dz) for x, z in columns
                     for dx in reach for dz in reach)


def _holes(columns) -> list:
    """The footprint's holes: each set of columns outside it, joined
    side to side, that cannot reach the outside of its bounding box
    without crossing it -- a courtyard. Two walls meeting only at a corner
    close a courtyard as surely as a wall does: a gap no one could walk
    through is no way out. In order of each hole's first column."""
    x0 = min(x for x, _ in columns) - 1
    x1 = max(x for x, _ in columns) + 1
    z0 = min(z for _, z in columns) - 1
    z1 = max(z for _, z in columns) + 1

    def flood(start, seen):
        found = [start]
        seen.add(start)
        for x, z in found:
            for step in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
                if (x0 <= step[0] <= x1 and z0 <= step[1] <= z1
                        and step not in columns and step not in seen):
                    seen.add(step)
                    found.append(step)
        return found

    seen: set = set()
    flood((x0, z0), seen)           # the padding, and all it reaches
    holes = []
    for x in range(x0, x1 + 1):
        for z in range(z0, z1 + 1):
            if (x, z) not in columns and (x, z) not in seen:
                holes.append(frozenset(flood((x, z), seen)))
    return holes


def _runs(columns) -> dict:
    """``{(x, z): (along x, along z)}``: how far the roof runs unbroken
    through each column, along its row and along its column."""
    rows: dict = {}
    files: dict = {}
    for x, z in columns:
        rows.setdefault(z, []).append(x)
        files.setdefault(x, []).append(z)
    length: dict = {}
    for axis, lines in ((0, rows), (1, files)):
        for fixed, values in lines.items():
            values.sort()
            start = 0
            for i in range(1, len(values) + 1):
                if i == len(values) or values[i] != values[i - 1] + 1:
                    for value in values[start:i]:
                        cell = (value, fixed) if axis == 0 else (fixed, value)
                        length.setdefault(cell, [0, 0])[axis] = i - start
                    start = i
    return {cell: tuple(pair) for cell, pair in length.items()}


def _run_facing(runs, falls, *, edge: bool, upturned: bool) -> str:
    """Which way a hip cell's stair faces on a footprint, which has no
    center to face -- an L's lies outside it: up its slope along whichever
    axis the roof runs longer through the cell (``runs``, along x and
    along z), a tie to z. ``falls`` are its two falls, one on each axis:
    the neighbors it falls a block or more to, which it faces away from,
    or, on the ``edge`` of the plan, the words it falls off the roof by,
    which it faces away from too -- or toward, on an eave turned up.

    On a rectangle it is :func:`_hip_facing` by another road: a hip cell
    sits on a ring's corner, where the center lies more along x exactly
    when the ring is longer along x, which is the roof's run through
    it."""
    word = next(word for word in falls
                if (COMPASS[word][0] != 0) == (runs[0] > runs[1]))
    if edge and upturned:
        return word
    return OPPOSITE[word]


class Wings:
    """Several roofs joined where they meet: each wing a :class:`Roof`
    over its own outline from its own ``base_y``, and every column the
    highest top any wing gives it.

    ``wings`` is a sequence of ``(roof, outline, base_y)`` -- each outline
    one its roof takes: a rectangle, a disc, or a footprint for a hip.
    It roofs a plan one rule cannot: an L, a T or a cross gable, whose
    wings' ridges run different ways or stand at different heights, as
    the suburban house's main roof, rear gable and front bay do. Where
    two wings' slopes cross, the valley falls out of their planes: a
    column with no lower side neighbor, laid as a plateau is, a whole
    block and no stair. A wing's verge standing over a lower wing reaches
    down to that wing's top, as every column reaches down to its lowest
    neighbor, so the shell has no gap. And a wing's end plane is closed
    (``close_ends``) only where it faces out of the walls of every wing:
    an end that runs into another wing is inside the roof and stays open.

    Each wing keeps what it is alone where a join of tops would undo it.
    Under its walls stands its building, from the springing the wing has
    laid alone -- the highest, where several wings' walls hold a column:
    no column over them reaches below it, so an eave on a wall line stays
    one course deep however low the roof beside it, and the wing's ends
    are closed and its attic filled, or counted hollow, from it, never
    down through a taller wing's top stories from a lower one's eave.
    Over open ground a column reaches down to its own wing's roof alone:
    an overhang beside a lower wing is an eave with air under it, not a
    wall in front of the wall under the eave. And a wing's eave that a
    higher wing roofs over keeps the floor it has alone wherever it
    closes its attic -- everywhere but inside another wing's walls, whose
    roof closes the attic the two share -- so an overhang drawn over a
    lower wing's walls reaches down to that wing's eave, a face that
    keeps its attic shut.

    Every column belongs to the first wing listed whose top there is the
    highest, and that wing's settings lay it -- its materials, its crown,
    its depth and how its hips face -- so a tie between two wings goes to
    the one listed first, and listing them the other way round changes
    what the tie cells are made of; where two wings' end planes both
    close a column, the first listed gives the block. The write is the
    one roof's, course by course along the first wing's ridge.

    A hip over a plan that is no rectangle is one :class:`Roof` over its
    footprint, not Wings: a max of rectangles' hips is the same roof
    where they overlap to each other's far edges, and leaves a trough
    where two only touch. Two :meth:`Builder.roof` calls over the same
    columns refuse each other, as any two writers do; Wings is the join.

    Refused as it is made (an ``NBTError``): no wings, a wing that is not
    ``(Roof, outline, base_y)``, a Wings among them -- list its wings in
    the one -- and an outline its roof does not take. :meth:`plan`
    refuses a column needing a half piece from a wing with ``slab=None``
    -- a whole-block wing roofing over a half-block one's walls -- naming
    the first cell in write order, as one roof does.
    """

    __slots__ = ("_wings", "_parts")

    def __init__(self, wings):
        if isinstance(wings, Wings):
            raise NBTError("a Wings among the wings of a Wings: list its "
                           "wings in the one, (roof, outline, base_y) each")
        if isinstance(wings, (str, bytes, Roof)):
            listed = None
        else:
            try:
                listed = tuple(wings)
            except TypeError:
                listed = None
        if listed is None:
            raise NBTError(f"Wings takes a sequence of (roof, outline, "
                           f"base_y), got {wings!r}")
        if not listed:
            raise NBTError("Wings joins one roof or more, and was given none")
        given = []
        parts = []
        for item in listed:
            if isinstance(item, Wings) or (
                    isinstance(item, (list, tuple)) and item
                    and isinstance(item[0], Wings)):
                raise NBTError("a Wings among the wings of a Wings: list its "
                               "wings in the one, (roof, outline, base_y) "
                               "each")
            if (not isinstance(item, (list, tuple)) or len(item) != 3
                    or not isinstance(item[0], Roof)):
                raise NBTError(f"a wing is (roof, outline, base_y), a Roof "
                               f"with the outline and base_y it is laid "
                               f"over, got {item!r}")
            roof, outline, base_y = item
            parts.append(roof._wing(outline, base_y))
            given.append((roof, outline, base_y))
        self._wings = tuple(given)
        self._parts = tuple(parts)

    @property
    def wings(self) -> tuple:
        """The wings, ``(roof, outline, base_y)`` each, as given."""
        return self._wings

    def plan(self, *, attic,
             attic_floor: int | None = None) -> dict[tuple[int, int, int], BlockState]:
        """Every cell the joined roof writes, with the state it writes
        there, in the order it is written: :meth:`Roof.plan`'s, over every
        wing at once. Pure: it reads no world. ``attic`` and
        ``attic_floor`` are :meth:`Roof.plan`'s, under every wing's walls,
        each from that wing's own springing -- laid alone -- when no
        ``attic_floor`` is given."""
        return dict(self._layout(attic, attic_floor).picked())

    def verify(self, save, *, attic, attic_floor: int | None = None,
               dimension: str | None = None,
               missing_ok: bool = False) -> list[str]:
        """Every cell of the joined roof :meth:`plan` lays that does not
        hold what the rule says, in plan order -- :meth:`Roof.verify`'s
        check, over every wing at once."""
        return _verify(self.plan(attic=attic, attic_floor=attic_floor),
                       save, dimension, missing_ok)

    def _layout(self, attic, attic_floor) -> _Layout:
        return _lay(self._parts, _attic(attic, attic_floor))

    def __repr__(self) -> str:
        return f"<Wings {' + '.join(repr(roof) for roof, _, _ in self._wings)}>"


class RoofResult:
    """What a :meth:`Builder.roof` laid: ``blocks``, the cells of the
    plan, and how many of them are ``stairs`` and how many ``closed`` --
    end walls and attic fill; ``already``, the cells that held the right
    block before the call (written again nothing); ``hollow``, the empty
    cells a hollow attic leaves under the shell over the walls' outline,
    a number that is a mob-proofing job when it is large; and
    ``springing``, the block level the shell reaches down to."""

    __slots__ = ("blocks", "stairs", "closed", "hollow", "already",
                 "springing")

    def __init__(self, springing: int):
        self.blocks = 0
        self.stairs = 0
        self.closed = 0
        self.hollow = 0
        self.already = 0
        self.springing = springing

    def __repr__(self) -> str:
        parts = [f"{self.blocks:,} blocks"]
        if self.stairs:
            parts.append(f"{self.stairs} stairs")
        if self.closed:
            parts.append(f"{self.closed} closed")
        if self.hollow:
            parts.append(f"{self.hollow} hollow")
        if self.already:
            parts.append(f"{self.already} already right")
        parts.append(f"springing at y={self.springing}")
        return f"<RoofResult {', '.join(parts)}>"


class RoofMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods: roofs.

    Builder inherits them; nothing here is used on its own.
    """

    def roof(self, roof, outline=None, base_y=None, *, attic,
             attic_floor=None, overwrite: bool = False) -> RoofResult:
        """Lay a :class:`Roof` over ``outline``, its rise counted from
        ``base_y`` -- the eave course, for a rise starting at 2 as every
        pitch does: :meth:`Roof.plan`, checked and written. Or lay a
        :class:`Wings`, which carries each wing's outline and base_y and
        takes neither here: :meth:`Wings.plan`, checked and written as one
        roof. A Roof without both, or a Wings with either, is an
        ``NBTError``; so is anything else in ``roof``. Two calls over the
        same columns refuse each other as any two writers do -- Wings is
        how roofs that meet are joined.

        ``attic`` is required -- ``"hollow"``, or the block that fills the
        space under the shell over the walls' outline from ``attic_floor``
        (the springing when left out; each wing's own, in a Wings) -- and
        the result's ``hollow`` counts the empty cells a hollow one leaves;
        its ``springing`` is the whole roof's, the lowest block any column
        reaches down into. Anything already standing in
        a cell the roof writes that is not what it would write there --
        snow on an old roof included -- stops the call by name, every cell
        in the refusal's faults, before a block is written;
        ``overwrite=True`` cuts through it. A cell already holding what the
        roof writes there (the same game state, apart from a stair's corner
        shape) is left as it stands and counted ``already``. The roof is
        not ground: what a session knows of the ground under it is left as
        it was.

        Refused before anything is read: a block the game does not have
        (:class:`~heaton_voxel.craft.errors.IllegalBlock`, every entry of a
        Mix included), a chunk not generated or not finished, or a height
        past the world (:class:`~heaton_voxel.craft.errors.Refused`); then
        a taken cell and a worksite's guard the same way. Run
        :meth:`finish` afterwards like any other build: it shapes the
        hips' corner stairs.
        """
        self._alive()
        if isinstance(roof, Wings):
            if outline is not None or base_y is not None:
                raise NBTError("a Wings carries each wing's own outline and "
                               "base_y: pass b.roof(wings, attic=...) with "
                               "neither")
            layout = roof._layout(attic, attic_floor)
        elif isinstance(roof, Roof):
            if outline is None or base_y is None:
                raise NBTError("a Roof is laid over an outline from a base_y: "
                               "pass b.roof(roof, outline, base_y, "
                               "attic=...)")
            layout = roof._layout(outline, base_y, attic, attic_floor)
        else:
            raise NBTError(f"roof takes a Roof or a Wings, got "
                           f"{type(roof).__name__}; make one with "
                           f"Roof(eaves=..., block=..., slab=..., stair=...), "
                           f"or join several with Wings([(roof, outline, "
                           f"base_y), ...])")
        ops = [("set", cell, value) for cell, value in layout.cells]
        self._vet_plan(ops, what="roof")
        planned = layout.picked()
        already, blocked, _ = _occupancy(
            self._read, planned, same=_same_crown, empty=_empty,
            overwrite=overwrite, noun="roof")
        if blocked:
            raise Refused(
                f"{len(blocked)} cells the roof needs are already taken, "
                f"first: {blocked[0]}. Clear the roof line first -- snow on "
                f"an old roof counts -- or pass overwrite=True to cut through "
                f"whatever is there. Nothing was written", faults=blocked)
        result = RoofResult(layout.springing)
        result.blocks = len(planned)
        result.stairs = layout.stairs
        result.closed = layout.closed
        result.already = len(already)
        result.hollow = sum(1 for cell in layout.hollow
                            if _empty(self._read(cell)))
        self._commit([op for op in ops if op[1] not in already], what="roof")
        return result

    def gable_roof(self, x0, z0, x1, z1, base_y, axis, *,
                   stair=FROM_PALETTE, plank=FROM_PALETTE,
                   slab=FROM_PALETTE) -> None:
        """A stepped gable roof with one-block eaves; the ridge runs along
        ``axis`` (``"x"`` or ``"z"``). Gable-end triangles are filled with
        ``plank``; an even span meets in a slab ridge. Stair facings are
        written correctly and :meth:`finish` shapes any corners.

        It is a :class:`Roof` -- ``eaves`` the two sides across ``axis``,
        ``pitch=2``, ``overhang=1``, the gable ends closed with ``plank``
        and the attic left hollow -- written as it always was, byte for
        byte, with no check of what stands in its way. Corners in either
        order are sorted.

        Each material left out is the Builder's :attr:`palette`'s: ``stair``
        and ``slab`` the stairs and the slab of its ``roof`` family (dark
        oak, by default), and ``plank`` its ``gable``. A
        :class:`~heaton_voxel.craft.style.Material` given for ``stair`` or
        ``slab`` is its stairs or its slab, so one family roofs the whole
        course, and one that has no such form is refused by name, an
        :class:`~heaton_voxel.craft.errors.IllegalBlock`, before anything
        is planned. ``plank`` may be a
        :class:`~heaton_voxel.craft.style.Mix`, each gable cell its pick.
        ``stair`` and ``slab`` are one block each, since each course spells
        its own facing or type onto them: a Mix there is a ``TypeError``
        before anything is planned, and a block the game has of another
        shape an ``NBTError``."""
        # The courses spell their facing, half and type onto these, so a
        # Mix is refused for what it is, not as text that will not parse
        # once a facing is stuck on the end of it.
        stair, plank, slab = self._materials(
            "gable_roof", _Slot("stair", stair, "roof", "stairs"),
            _Slot("plank", plank, "gable", mixes=True),
            _Slot("slab", slab, "roof", "slab"))
        if axis == "x":
            eaves = ("north", "south")
        elif axis == "z":
            eaves = ("west", "east")
        else:
            raise NBTError(f"gable_roof axis must be 'x' or 'z', got {axis!r}")
        rule = Roof(eaves=eaves, pitch=2, overhang=1, crown="slab",
                    block=plank, slab=slab, stair=stair, close_ends=plank)
        layout = rule._layout((x0, z0, x1, z1), base_y, HOLLOW, None)
        self._commit([("set", cell, value) for cell, value in layout.cells],
                     what="gable_roof")
