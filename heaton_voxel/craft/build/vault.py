"""Half-block stepped vaults and arcades.

The :class:`Vault` rule, its profiles and readers, and the writers that
extrude it.

Part of :mod:`heaton_voxel.craft.build`. The vault writer makes its
continuation and occupancy refusals with the helpers it shares with the
stair writers, in ``_common``; a vault has no walkway to keep head room
over.
"""

from __future__ import annotations

import copy
import math

from ...blocks import BlockState
from ...nbt import NBTError
from ..block_facts import facts, known
from ..blockinfo import material_of
from ..errors import Refused
from ..geometry import heading_of
from ._common import (
    _continuation,
    _empty,
    _MELTABLE,
    _occupancy,
    _same_crown,
    _STAIR_STATE,
    _state,
    AIR,
)


#: Which way a vault's stair courses face: toward the crown. Keyed by the
#: axis the vault runs along, as ``(negative side, positive side)`` -- the
#: same "eaves look at the ridge" convention :meth:`Builder.gable_roof` uses.
_TOWARD = {"z": (heading_of(1, 0), heading_of(-1, 0)),
           "x": (heading_of(0, 1), heading_of(0, -1))}


#: What a vault's slab carries besides ``type``. Filled in unless the caller
#: names it, for the palette reason :data:`_STAIR_STATE` exists.
_SLAB_STATE = {"waterlogged": "false"}


#: How far below the course inside it :meth:`Vault.read` will look for the
#: next one. A window rather than a whole column, because the wall a vault
#: lands on is often the same stone as the vault, and a search that ran to
#: the ground would happily read a building as sixty blocks of roof.
_VAULT_STEP = 6


class VaultResult:
    """What a :meth:`Builder.vault` extruded: courses, blocks, the run.

    ``already`` counts cells that held the right block before the call --
    on a continuation that is the seam where the new run meets the old,
    and a number close to ``blocks`` means the vault was already there.
    ``closed`` is the block the run's two end walls were built of
    (:attr:`Vault.close_ends`), or None for a run left open at both ends.
    """

    __slots__ = ("first", "last", "axis", "courses", "blocks", "stairs",
                 "already", "closed")

    def __init__(self, first: int, last: int, axis: str):
        self.first = first
        self.last = last
        self.axis = axis
        self.courses = 0
        self.blocks = 0
        self.stairs = 0
        self.already = 0
        self.closed = None

    def __repr__(self) -> str:
        parts = [f"{self.courses} courses {self.axis}="
                 f"{self.first}..{self.last}",
                 f"{self.blocks:,} blocks"]
        if self.stairs:
            parts.append(f"{self.stairs} stairs")
        if self.already:
            parts.append(f"{self.already} already right")
        if self.closed is not None:
            parts.append(f"closed with {self.closed}")
        return f"<VaultResult {', '.join(parts)}>"


def arch_profile(half_span: int, rise: int, *, eaves: int = 0) -> tuple[int, ...]:
    """A circular arch, quantized to half blocks -- a profile for :class:`Vault`.

    ``half_span`` is how many blocks the arch reaches from its crown to its
    springing and ``rise`` how many it climbs over that distance; the result
    is how far the top of the shell has fallen below the crown at each of
    those blocks, in **half** blocks, crown outward. ``eaves`` adds one more
    course that far below the last, for the overhang a vault wants where it
    meets its wall.

    A circle is vertical at the springing, so the last block or two fall a
    long way -- which is what makes an arch read as an arch rather than as a
    shallow roof, and what :class:`Vault` turns into stairs. A pointed arch
    is :func:`pointed_arch_profile`.
    """
    half_span, rise = int(half_span), int(rise)
    if half_span < 1:
        raise NBTError(f"an arch needs a half span of at least 1 block, "
                       f"got {half_span}")
    if rise < 0:
        raise NBTError(f"an arch cannot fall from its crown to its "
                       f"springing, got rise={rise}")
    out = []
    for d in range(half_span + 1):
        height = rise * math.sqrt(max(0.0, 1.0 - (d / half_span) ** 2))
        drop = int(round(2 * (rise - height)))
        # Rounding can hand back a course higher than the one inside it,
        # which is a roof that climbs outward. Clamp instead: a flat pair
        # is a real profile, an inverted one is not.
        out.append(max(drop, out[-1]) if out else drop)
    if eaves:
        out.append(out[-1] + int(eaves))
    return tuple(out)


def pointed_arch_profile(half_span: int, rise: int, *,
                         eaves: int = 0) -> tuple[int, ...]:
    """A pointed (two-centered, Gothic) arch, quantized to half blocks -- a
    profile for :class:`Vault`, as :func:`arch_profile` is for a round one.

    Each side is an arc of a circle centered on the springing line, but
    past the crown: radius ``R = (rise² + half_span²) / (2·half_span)``,
    its center ``R - half_span`` beyond the crown on the far side, so the
    two arcs meet at the crown in a point rather than a level top. Read
    the same way as :func:`arch_profile`: how far the top of the shell has
    fallen below the crown at each block out to the springing, in **half**
    blocks, crown outward, with ``eaves`` one more course that far below
    the last; quantized exactly as it is too (``int(round())``, then
    clamped so no course stands above the one inside it), so it is the
    same profile on every machine.

    A pointed arch rises at least its half span: at ``rise == half_span``
    the centers meet at the crown and it is :func:`arch_profile`'s round
    arch, course for course, and anything lower is refused (an
    ``NBTError``). An equilateral arch is ``rise`` about
    ``round(half_span * 1.732)``. A point falls away from its crown faster
    than a circle does -- ``pointed_arch_profile(7, 12)`` is
    ``(0, 1, 3, 5, 7, 10, 14, 24)`` where ``arch_profile(7, 12)`` is
    ``(0, 0, 1, 2, 4, 7, 12, 24)`` -- and it is a pointed vault's first
    step that tells it from a round one.
    """
    half_span, rise = int(half_span), int(rise)
    if half_span < 1:
        raise NBTError(f"an arch needs a half span of at least 1 block, "
                       f"got {half_span}")
    if rise < half_span:
        raise NBTError(
            f"a pointed arch rises at least its half span, got "
            f"half_span={half_span} and rise={rise}; at rise == half_span it "
            f"is the round arch arch_profile draws, and a lower one is "
            f"arch_profile's flattened arc")
    radius = (rise * rise + half_span * half_span) / (2 * half_span)
    out = []
    for d in range(half_span + 1):
        height = math.sqrt(max(0.0, radius * radius
                               - (d - half_span + radius) ** 2))
        drop = int(round(2 * (rise - height)))
        # As arch_profile: a pair of courses rounded level is a real
        # profile, one rounded into a climb is not.
        out.append(max(drop, out[-1]) if out else drop)
    if eaves:
        out.append(out[-1] + int(eaves))
    return tuple(out)


class Vault:
    """A stepped vault: one arch profile, extruded along an axis.

    A stepped gable drops a **whole** block per block outward, which is why
    a roof laid in stairs reads as a staircase. A vault drops **half** a
    block, because a bottom slab and a top slab are two different heights
    inside one block -- so slabs give a roof twice the vertical resolution
    stairs do, and at that resolution a curve stops looking like steps.
    That is the whole trick. Everything else here is bookkeeping on top of
    it: a full course where the profile wants a whole block, a stair where
    it falls faster than one, and each column filled down to the top of the
    column outside it so the shell has no gaps seen from the end.

    The rule is two short tuples of integers. :attr:`profile` is how far the
    top of the shell has fallen below the crown at each block outward, in
    half blocks; :attr:`depth` is how thick the shell is at each of those,
    also in half blocks, and defaults to whatever reaches the next column
    out. ``pitch=1`` is the half-block ramp above (a shallow vault),
    ``pitch=2`` an ordinary stepped gable, and a sequence steepens toward
    the eaves the way :func:`arch_profile` does. ``eaves`` adds one more
    course below the last, for an overhang past the wall head.

    Keeping the rule apart from the blocks is what makes a vault
    *continuable* and *portable*: :meth:`read` lifts both out of one
    somebody already built -- replaying it over every course already
    standing before it hands anything back -- and the same object then
    extrudes the rest of it, in the same hand, out of the same materials.

    ``crown`` is the crown column, ``(x, y, z)``; the coordinate along
    ``axis`` is ignored, a barrel vault being a section and a run. The
    crown course's top surface sits in the middle of block ``y``, which is
    to say the default crown is a bottom slab there.

    Four materials, and each has a job. ``slab`` is the half course the
    ramp is made of; ``block`` a whole course; ``stair`` the top course of
    a column that falls a whole block or more to the next one, facing the
    crown; and ``springer`` a whole course at an offset that has been
    thickened past what the profile needs -- the block a vault lands on
    where it crosses a pier, which is usually a different stone from the
    haunch.

    ``close_ends`` closes the two ends of a run with end walls of that
    block: from the springing (:attr:`springing`) up to the shell, with
    every top slab over the wall made whole (:meth:`section`), which is
    how the walls under JeffWorld's nave meet its vault. Left out, or
    ``False``, the run is open at both ends, as it always was. It is a
    block, and never one of the game's airs, a
    :class:`~heaton_voxel.craft.style.Mix` or a ``True`` (an ``NBTError``,
    or a ``TypeError`` for a Mix); a read vault is open, since
    :meth:`read` does not look for closed ends -- a strict read of a closed
    run is refused on its whole slabs, and :meth:`Builder.vault` will not
    continue past an end closed this way however the rule was read.

    The defaults -- ``half_span=7``, ``pitch=1`` and cobblestone -- are the
    vault over JeffWorld's great hall, a worked example to replace and not
    a rule (see "Where the defaults came from" in
    :mod:`heaton_voxel.craft.build`).
    """

    __slots__ = ("axis", "across", "crown_y", "profile", "depth", "slab",
                 "block", "stair", "springer", "close_ends", "found",
                 "differences")

    def __init__(self, crown, *, axis: str = "z", half_span: int = 7,
                 pitch=1, eaves: int = 0, profile=None, depth=None,
                 slab="minecraft:cobblestone_slab",
                 block="minecraft:cobblestone",
                 stair="minecraft:cobblestone_stairs",
                 springer=None, close_ends=None):
        if axis not in _TOWARD:
            raise NBTError(f"a vault runs along 'x' or 'z', got {axis!r}")
        self.axis = axis
        if len(crown) != 3:
            raise NBTError(f"crown is the crown column (x, y, z), got {crown!r}")
        self.across = int(crown[0] if axis == "z" else crown[2])
        self.crown_y = int(crown[1])
        self.profile = _vault_profile(profile, half_span, pitch, eaves)
        if depth is None:
            self.depth = None
        else:
            self.depth = tuple(int(d) for d in depth)
            if len(self.depth) != len(self.profile):
                raise NBTError(
                    f"depth has {len(self.depth)} entries and the profile "
                    f"{len(self.profile)}; there is one thickness per course")
            if any(d < 1 for d in self.depth):
                raise NBTError("a course of a vault is at least one half "
                               "block thick")

        self.slab = _fill(slab, _SLAB_STATE, drop=("type",))
        if not self.slab.name.endswith("_slab"):
            raise NBTError(
                f"{self.slab.name} is not a slab, so it has no top and "
                f"bottom half for the profile to land in -- half courses "
                f"are what makes this a vault rather than a stepped gable")
        self.block = _state(block)
        self.stair = None if stair is None else _fill(
            stair, _STAIR_STATE, drop=("facing",))
        if self.stair is not None and not self.stair.name.endswith("_stairs"):
            raise NBTError(f"{self.stair.name} is not a stair block; pass "
                           f"stair=None for a vault that steps square")
        self.springer = self.block if springer is None else _state(springer)
        #: The block the end walls of a run are built of, or None for a run
        #: open at both ends.
        self.close_ends = _end_wall(close_ends)
        #: ``(first, last)`` along the axis of the run :meth:`read` lifted
        #: this from, else None.
        self.found = None
        #: Cells of that run the rule does not account for; empty unless
        #: :meth:`read` was asked not to be strict about them.
        self.differences = ()

    # -- geometry ----------------------------------------------------------

    @property
    def half_span(self) -> int:
        """Blocks from the crown column to the outermost course."""
        return len(self.profile) - 1

    @property
    def width(self) -> int:
        """The vault's full span, crown column included."""
        return 2 * len(self.profile) - 1

    @property
    def rise(self) -> float:
        """Blocks from the outermost course's top to the crown's."""
        return self.profile[-1] / 2

    @property
    def springing(self) -> int:
        """The block level the vault springs from: the lowest block any of
        its courses reaches down into, ``min(top(d) - thickness(d)) // 2``
        -- where :meth:`Builder.vault`'s ``clear_under`` starts emptying
        the span under the shell, and where a closed end wall starts
        (:meth:`section`)."""
        return min(self.top(d) - self.thickness(d)
                   for d in range(len(self.profile))) // 2

    def top(self, d: int) -> int:
        """The top of the shell ``d`` blocks out from the crown, in half
        blocks -- twice a block coordinate, so a half course is expressible."""
        return 2 * self.crown_y + 1 - self.profile[abs(int(d))]

    def thickness(self, d: int) -> int:
        """How thick the shell is ``d`` blocks out, in half blocks.

        Whatever was asked for, else whatever reaches the top of the column
        outside this one -- which is what leaves the shell without gaps seen
        from the end, and is why the haunch of a steep profile comes out
        solid without anybody saying so.
        """
        d = abs(int(d))
        if self.depth is not None:
            return self.depth[d]
        return self._reach(d)

    def _reach(self, d: int) -> int:
        """The thickness the profile alone implies at ``d``.

        The outermost course has nothing outside it to reach down to, so it
        is never *thickened* however deep it is -- an eave is as deep as it
        was asked to be, and calling it a springer would put the wrong stone
        all along the edge of the roof.
        """
        if d + 1 < len(self.profile):
            return max(1, self.top(d) - self.top(d + 1))
        return 2 if self.depth is None else self.depth[d]

    def _fall(self, d: int) -> int:
        """How far the top falls from ``d`` to the column outside it."""
        if d + 1 < len(self.profile):
            return self.top(d) - self.top(d + 1)
        return 2

    def column(self, d: int, *, facing: str | None = None) -> dict[int, BlockState]:
        """One column of the section, by block level.

        Filled from the top of the shell downward: a bottom slab wherever
        the run ends on a half block, a top slab wherever half a block is
        all that is left, and whole courses in between. The top course
        becomes a stair facing the crown when the shell falls a whole block
        or more to the column outside -- ``facing`` says which way that is,
        and the crown column, having no outside, never gets one.
        """
        d = abs(int(d))
        whole = (self.springer if self.thickness(d) > self._reach(d)
                 else self.block)
        top, floor = self.top(d), self.top(d) - self.thickness(d)
        out = _lay_column(top, floor, whole, self.slab)
        if self.stair is not None and facing is not None and self._fall(d) >= 2:
            peak = max(out)
            if str(out[peak]) == str(whole):
                out[peak] = self.stair.with_properties(facing=facing)
        return out

    def section(self, *, closed: bool = False) -> dict[tuple[int, int], BlockState]:
        """The whole cross section, keyed by ``(offset, y)``.

        ``offset`` is signed across the vault: negative toward -x for a
        vault running along ``z``, toward -z for one running along ``x``.

        ``closed=True`` is the section an end of the run is closed with,
        for a vault given :attr:`close_ends`: under every column, the end
        wall's block from the :attr:`springing` up to the block under the
        column's lowest piece, and that piece, where it is a top slab, made
        a whole one (``slab[type=double]``) -- since a wall that stopped
        under it would leave the slab's lower half open, half a block of
        sky under every top-slab course. That is the rule the walls under
        JeffWorld's nave follow, quartz from the springing and a double
        slab over each wall. A vault without ``close_ends`` has no closed
        section (an ``NBTError``).
        """
        if closed and self.close_ends is None:
            raise NBTError(
                "section(closed=True) is the section the ends of a run are "
                "closed with, and this vault has no close_ends: name the "
                "block its end walls are built of")
        inward, outward = _TOWARD[self.axis]
        out: dict[tuple[int, int], BlockState] = {}
        for y, state in self.column(0).items():
            out[(0, y)] = state
        for d in range(1, len(self.profile)):
            for sign, facing in ((-1, inward), (1, outward)):
                for y, state in self.column(d, facing=facing).items():
                    out[(sign * d, y)] = state
        if closed:
            springing = self.springing
            lowest: dict[int, int] = {}
            for offset, y in out:
                lowest[offset] = min(y, lowest.get(offset, y))
            for offset in sorted(lowest):
                y = lowest[offset]
                for wall, state in _close_column(y, out[(offset, y)],
                                                 springing, self.close_ends,
                                                 self.slab):
                    out[(offset, wall)] = state
        return out

    def cell(self, along: int, offset: int, y: int) -> tuple[int, int, int]:
        """Where a section cell lands in the world at position ``along``."""
        if self.axis == "z":
            return (self.across + offset, y, int(along))
        return (int(along), y, self.across + offset)

    # -- reading -----------------------------------------------------------

    def verify(self, save, first: int, last: int, *,
               dimension: str | None = None,
               missing_ok: bool = False) -> list[str]:
        """Every cell in ``first..last`` along the axis that is not what this
        rule says.

        The check to run *before* extending a vault somebody else built, and
        the reason :meth:`read` can be trusted: a rule that cannot reproduce
        the courses already standing has no business extruding the next
        fifty. Complaints name the position, the cell and both states.

        A stair's ``shape`` is not compared. Corner shapes are derived from
        neighbors by a block update -- they belong to
        :meth:`~heaton_voxel.world.MinecraftSave.connect_blocks`, not to a
        roof profile, and a vault running past a gable end picks up inner
        corners nobody authored.

        ``missing_ok`` forgives air where the rule wants a block, for a run
        whose far end was never finished -- any of the game's airs, so a
        hall left open to a cave is unfinished and not wrong; in an end
        wall too.

        A vault with :attr:`close_ends` holds the courses at ``first`` and
        ``last`` -- the run's two ends -- to the closed section
        (:meth:`section`), its end walls and their whole slabs, and the
        courses between to the open one.
        """
        first, last = sorted((int(first), int(last)))
        section = self.section()
        ends = (section if self.close_ends is None
                else self.section(closed=True))
        wrong = []
        for along in range(first, last + 1):
            rows = ends if along in (first, last) else section
            for (offset, y), expect in sorted(rows.items()):
                x, cy, z = self.cell(along, offset, y)
                found = save.get_block(x, cy, z, dimension=dimension)
                if found is None:
                    wrong.append(
                        f"{self.axis}={along} ({x}, {cy}, {z}): nothing to "
                        f"read -- the chunk is not generated, or y is "
                        f"outside the world")
                elif not _same_course(found, expect):
                    if missing_ok and _empty(found):
                        continue
                    wrong.append(f"{self.axis}={along} ({x}, {cy}, {z}): "
                                 f"expected {expect}, found {found}")
        return wrong

    @classmethod
    def read(cls, save, crown, *, axis: str = "z",
             dimension: str | None = None, low: int | None = None,
             high: int | None = None, half_span: int | None = None,
             block=None, springer=None, strict: bool = True) -> "Vault":
        """Lift the vault already standing over a crown line.

        Walks the run out from ``crown`` along ``axis`` for as far as the
        crown course continues, samples sections across it -- the top of
        every column, and how far the material goes down before it stops
        being the vault's -- takes each course of the section by majority,
        and then **replays the derived rule over every course it found**,
        refusing if the two disagree. That last step is the point, exactly
        as it is for :meth:`SpiralStair.read`: extruding a vault is writing
        thousands of blocks from a rule read out of one section, and a
        misread section prints itself down the whole hall before anybody
        sees it.

        How wide the vault is settles itself twice over. Columns are read
        outward until the material stops being the crown's -- cobblestone
        slabs, cobblestone stairs and cobblestone are one family, cut from
        one block, while quartz is somebody else's wall and stone bricks
        under a stone vault are the wall it stands on -- and then the rule
        stops where the samples stop agreeing, which is where a wall head
        in the vault's own stone gets counted as more roof. Say
        ``half_span=`` for a vault whose outside is regular too, or one
        that changes stone halfway out.

        ``strict=False`` hands back the rule with the cells it does not
        account for in :attr:`differences` instead of refusing. That is the
        setting for reading a vault built by hand, which has patches and
        springers a profile was never going to predict; look at the list
        before extruding anything from it.
        """
        if axis not in _TOWARD:
            raise NBTError(f"a vault runs along 'x' or 'z', got {axis!r}")
        if len(crown) != 3:
            raise NBTError(f"crown is the crown column (x, y, z), got {crown!r}")
        across = int(crown[0] if axis == "z" else crown[2])
        crown_y = int(crown[1])
        seed = int(crown[2] if axis == "z" else crown[0])

        def at(along: int, offset: int, y: int):
            x, cy, z = ((across + offset, y, along) if axis == "z"
                        else (along, y, across + offset))
            return save.get_block(x, cy, z, dimension=dimension)

        head = at(seed, 0, crown_y)
        if head is None:
            raise NBTError(
                f"nothing to read at the crown {tuple(crown)}: the chunk is "
                f"not generated, or y is outside the world")
        if not head.name.endswith("_slab"):
            raise NBTError(
                f"the crown column at {tuple(crown)} holds {head}, and a "
                f"vault's crown course is a slab -- half courses are what "
                f"makes one. Point at the ridge, not at the roof beside it")
        family = _family(head.name)

        first, last = _vault_run(at, seed, crown_y, head, low, high)
        if last - first < 1:
            raise NBTError(
                f"the crown course at {tuple(crown)} is one block long -- "
                f"{head} with something else either side of it. That is a "
                f"patch, not a ridge: a course thickened to hang a lamp from "
                f"looks exactly like this. Point at a neighboring column, "
                f"or widen the run with low=/high=")
        columns = _vault_common(at, first, last, crown_y, family, half_span,
                                axis)
        materials = _vault_materials(columns, block, springer)

        vault = cls((crown[0], crown_y, crown[2]), axis=axis,
                    profile=materials["profile"], depth=materials["depth"],
                    slab=materials["slab"], block=materials["block"],
                    stair=materials["stair"], springer=materials["springer"])
        wrong = vault.verify(save, first, last, dimension=dimension)
        if wrong and strict:
            raise Refused(
                f"the rule read off this vault does not reproduce it -- "
                f"{len(wrong)} disagreements, first: {wrong[0]}. Refusing to "
                f"extrude a rule that does not describe what is already "
                f"there; pass strict=False to see the whole list instead", faults=wrong)
        vault.found = (first, last)
        vault.differences = tuple(wrong)
        return vault

    def __repr__(self) -> str:
        run = "" if self.found is None else (
            f" {self.axis}={self.found[0]}..{self.found[1]}")
        odd = (f" +{len(self.differences)} unaccounted"
               if self.differences else "")
        ends = ("" if self.close_ends is None else
                f" closed with {self.close_ends.name.rsplit(':', 1)[-1]}")
        return (f"<Vault {self.axis} across={self.across} y={self.crown_y} "
                f"{self.width} wide rise={self.rise:g} "
                f"{self.slab.name.rsplit(':', 1)[-1]}{ends}{run}{odd}>")


def _lay_column(top: int, floor: int, whole, slab) -> dict:
    """One column of a shell, ``{y: piece}``, laid from ``top`` down to
    ``floor`` -- both in half blocks, twice a block coordinate -- the way
    every half-block roof here lays one: a bottom slab wherever the run
    ends on a half block, a top slab wherever half a block is all that is
    left, and ``whole`` -- whatever stands for a whole course, kept as it
    is given -- in between. Top piece first, which is the order the
    column's cells go into the dict and so the order a section holds them.

    :meth:`Vault.column` and :class:`~heaton_voxel.craft.build.roof.Roof`
    both lay their columns with it: one loop, so a roof and a vault laid
    from the same tops lay the same pieces. ``slab`` is a slab state, the
    ``type`` spelled onto it; a column that needs no half piece -- a whole
    number of blocks from a whole-block top -- never reads it."""
    out: dict[int, object] = {}
    cursor = top
    while cursor > floor:
        if cursor % 2:                       # ends on a half block
            out[(cursor - 1) // 2] = slab.with_properties(type="bottom")
            cursor -= 1
        elif cursor - floor == 1:            # half a block left to lay
            out[cursor // 2 - 1] = slab.with_properties(type="top")
            cursor -= 1
        else:
            out[cursor // 2 - 1] = whole
            cursor -= 2
    return out


def _close_column(y: int, piece, start: int, fill, slab) -> list:
    """What closing one column of a shell writes: ``(y, state)`` pairs,
    the column's lowest piece -- ``piece``, at block ``y`` -- made a
    double slab when it is a top slab of ``slab`` and the closure reaches
    it, then ``fill`` from block ``start`` up to the block under it.

    The rule the walls under JeffWorld's nave follow (:meth:`Vault.section`
    with ``closed=True``), and the one :class:`~heaton_voxel.craft.build.roof.Roof`
    closes a gable end or fills an attic by: a wall that stopped under a
    top slab would leave the slab's lower half open, half a block of sky
    under every top-slab course. A closure that starts above the piece
    reaches nothing, and leaves it as it is; ``slab`` None is a shell
    with no slabs to double."""
    out = []
    if (start <= y and slab is not None and isinstance(piece, BlockState)
            and piece.name == slab.name
            and piece.properties.get("type") == "top"):
        out.append((y, slab.with_properties(type="double")))
    out.extend((wall, fill) for wall in range(start, y))
    return out


def _fill(block, defaults: dict, *, drop=()) -> BlockState:
    """``block`` with ``defaults`` filled in where it named nothing, and the
    properties the rule owns taken back off it."""
    state = _state(block)
    properties = dict(defaults)
    properties.update(state.properties)
    for key in drop:
        properties.pop(key, None)
    return BlockState(state.name, properties)


def _end_wall(block):
    """``close_ends`` as a :class:`Vault` holds it: None for a run open at
    both ends (``None`` or ``False``), else the end walls' block, as a
    state -- never ``True``, which names no block, and never any of the
    game's airs, an end wall of which closes nothing."""
    if block is None or block is False:
        return None
    if block is True:
        raise NBTError(
            "close_ends is the block a vault's end walls are built of, not a "
            "switch: name it -- the walls under JeffWorld's nave are "
            "minecraft:quartz_block -- or leave it out for a vault open at "
            "both ends")
    state = _state(block)
    if _empty(state):
        raise NBTError(
            f"an end wall of {state.name} closes nothing; leave close_ends "
            f"out for a vault open at both ends")
    return state


def _under_span(vault: "Vault", lo: int, hi: int):
    """Whether a cell lies in the span :meth:`Builder.vault`'s
    ``clear_under`` empties over ``lo..hi``: under every column of the
    run, from the :attr:`~Vault.springing` up to the block under the
    column's lowest piece -- where a closed run's end walls stand."""
    floors = {d: (vault.top(d) - vault.thickness(d)) // 2
              for d in range(len(vault.profile))}
    springing = vault.springing

    def inside(cell) -> bool:
        x, y, z = cell
        along, offset = ((z, x - vault.across) if vault.axis == "z"
                         else (x, z - vault.across))
        floor = floors.get(abs(offset))
        return (lo <= along <= hi and floor is not None
                and springing <= y < floor)
    return inside


def _closed_end(vault: "Vault", read, along: int):
    """The block the course at ``along`` is walled off with, when it is an
    end closed as :attr:`Vault.close_ends` closes one, else None.

    Closed means every cell of the closed section (:meth:`Vault.section`)
    holds what it would there, for the block standing in the first cell of
    its wall: a wall of one block from the springing to the shell, and its
    top slabs made whole. This is what keeps :meth:`Builder.vault` from
    continuing a closed run, since :meth:`Vault.read` reads no end walls:
    its strict replay trips on the whole slabs, but a run read loose
    (``strict=False``) comes back an open rule, with the slabs among its
    ``differences``, and extending it would leave the old end wall
    standing across the hall. An end walled some other way -- a gable
    filled only to the underside of the shell, as ``build_hall.py`` fills
    its own -- is not this one, and stays the caller's to see. A rule
    whose every column reaches the springing has no wall to stand in the
    way, and is never closed. ``read`` returns the block at a cell.
    """
    probe = copy.copy(vault)
    probe.close_ends = vault.block
    wall = sorted(set(probe.section(closed=True)) - set(vault.section()))
    if not wall:
        return None
    block = read(vault.cell(along, *wall[0]))
    if _empty(block):
        return None
    probe.close_ends = block
    for (offset, y), expect in probe.section(closed=True).items():
        if not _same_course(read(vault.cell(along, offset, y)), expect):
            return None
    return block


#: Whether two vault or roof cells hold the same block, ignoring what a
#: block update derives -- a stair's corner shape, and a fence's or a
#: wall's sides where one is a course: the one test, :func:`_same_crown`,
#: under the name the vault's readers have always used.
_same_course = _same_crown


def _hangs_from(state) -> bool:
    """Whether a chain may hang from under ``state``: whether the game
    would hang a *lantern* there. ``LanternBlock.canSurvive`` keeps a
    hanging lantern only while ``Block.canSupportCenter(level, above,
    DOWN)`` holds for the block above it. That refuses the
    ``unstable_bottom_center`` tag outright (:func:`_unstable_below`, the
    fence gates) and otherwise asks ``isFaceSturdy(DOWN, CENTER)``, the
    ``center`` mask of :mod:`~heaton_voxel.craft.block_facts`: whether the
    block is sturdy at the middle of its underside. A full block, a bottom
    or double slab, a bottom stair, a fence, a wall or a pane is, and a top
    slab, a top stair or leaves is not.

    The game asks nothing of a chain -- ``ChainBlock`` has no
    ``canSurvive``, so it keeps a chain under anything, air included --
    which leaves the chain's ceiling a choice, and the lantern's rule is
    the one taken: a chain the game would keep under a top slab hangs from
    a gap a cutaway shows, and the lamp at its foot is so often a lantern
    that one rule for the two is the rule a hall can be checked by.

    Never any air -- the roof of a cave is the stone above its cave air,
    not the cave air -- and never an id the game does not have, which it
    loads as air, though :func:`~heaton_voxel.craft.block_facts.facts`
    would answer a cube for it."""
    if (state is None or _empty(state) or not known(state.name)
            or _unstable_below(state.name)):
        return False
    return facts(state.name, state.properties).holds("down", center=True)


def _unstable_below(name: str) -> bool:
    """Whether ``name`` is in the game's ``unstable_bottom_center`` block
    tag, which ``Block.canSupportCenter`` refuses below before it looks at
    a shape. In 26.3 the tag is ``#fence_gates``: the thirteen gates, every
    one named ``*_fence_gate``. The probe records the ``center`` mask --
    ``isFaceSturdy`` alone -- and not the tag, and every gate state is 0
    there today, so this answers for an update that gives a gate a sturdy
    middle; ``tests/test_vault.py`` holds the name rule to the registry."""
    return name.endswith("_fence_gate")


def _why_no_ceiling(state) -> str:
    """Why :func:`_hangs_from` refuses ``state``, a block, in the words a
    refusal gives."""
    if _empty(state):
        return "it is air"
    if not known(state.name):
        return "the game has no such block, and loads it as air"
    if _unstable_below(state.name):
        return ("the game hangs nothing from a fence gate (its "
                "unstable_bottom_center tag)")
    return "its underside is not sturdy at the center"


def _family(name: str) -> str:
    """The block a vault block is cut from: ``cobblestone_slab``,
    ``cobblestone_stairs`` and ``cobblestone`` are all cobblestone
    (:func:`~heaton_voxel.craft.blockinfo.material_of`). By the whole
    name, never a prefix of it: stone bricks are not stone, nor is a
    stonecutter, and sandstone is not sand."""
    return material_of(name)[0]


def _in_family(name: str, family: str) -> bool:
    return material_of(name)[0] == family


def _piece(state: BlockState, y: int) -> tuple[int, int]:
    """What a block occupies in its column, in half blocks."""
    kind = state.properties.get("type")
    if state.name.endswith("_slab") and kind == "bottom":
        return (2 * y, 2 * y + 1)
    if state.name.endswith("_slab") and kind == "top":
        return (2 * y + 1, 2 * y + 2)
    return (2 * y, 2 * y + 2)


def _vault_profile(profile, half_span, pitch, eaves) -> tuple[int, ...]:
    """The drops a vault steps through, from ``profile=`` or from
    ``half_span``/``pitch``, with ``eaves`` hung off the end."""
    if profile is not None:
        out = [int(d) for d in profile]
        if not out or out[0] != 0:
            raise NBTError("a profile is the drop below the crown at each "
                           "block outward, so it starts at 0")
        if any(b < a for a, b in zip(out, out[1:])):
            raise NBTError(f"the profile climbs outward at some point "
                           f"({tuple(out)}); a vault does not -- an eave "
                           f"that turns up is a Roof(rise=...), whose tops "
                           f"may fall and climb again")
    else:
        half_span = int(half_span)
        if half_span < 1:
            raise NBTError(f"a vault needs a half span of at least 1 block, "
                           f"got {half_span}")
        steps = ([int(pitch)] * half_span if isinstance(pitch, int)
                 else [int(p) for p in pitch])
        if len(steps) != half_span:
            raise NBTError(
                f"pitch has {len(steps)} steps and the half span is "
                f"{half_span}; a sequence names the drop at each block out")
        if any(step < 0 for step in steps):
            raise NBTError("a vault's courses fall outward, never climb")
        out = [0]
        for step in steps:
            out.append(out[-1] + step)
    if eaves:
        eaves = int(eaves)
        if eaves < 0:
            raise NBTError("eaves hang below the last course, never above")
        out.append(out[-1] + eaves)
    return tuple(out)


def _vault_run(at, seed: int, crown_y: int, head: BlockState,
               low, high) -> tuple[int, int]:
    """How far the crown course runs either side of ``seed``.

    A slab of the crown's stone at the crown's height, not that exact
    state: a ridge may be thickened to a whole course wherever something
    hangs from it -- :meth:`Builder.hang_lamps`' ``anchor=`` sets one over
    every lamp -- and a run that stopped at the first lamp would report a
    fifty-block vault as four.
    """
    floor = None if low is None else int(low)
    ceiling = None if high is None else int(high)
    family = _family(head.name)

    def holds(along: int) -> bool:
        found = at(along, 0, crown_y)
        return (found is not None and found.name.endswith("_slab")
                and _in_family(found.name, family))

    first = last = seed
    while (floor is None or first - 1 >= floor) and holds(first - 1):
        first -= 1
    while (ceiling is None or last + 1 <= ceiling) and holds(last + 1):
        last += 1
    return first, last


#: How many places along a run :meth:`Vault.read` samples before deciding
#: which section describes it. More than one because the ends of a vault are
#: where its hips and half domes are; odd so a course cannot tie for the
#: majority it needs to enter the rule.
_VAULT_SAMPLES = 9


def _vault_common(at, first: int, last: int, crown_y: int, family: str,
                  half_span, axis: str):
    """What most of the run says each course of the section is.

    A hall is not the same all the way along. The outside varies most --
    gable ends, buttresses, the bay beside a cross wall, the wall head the
    eave lands on, often in the vault's own stone so the material alone
    will not say where the roof stops -- and the inside varies too, at
    every course thickened to hang a lamp from. Reading one section and
    calling it the rule makes the answer depend on where the caller
    happened to be standing.

    So sample across the run and take each course by vote, stopping where
    no majority is left: that is where the roof stops being one vault. The
    minority is not discarded, it is the :attr:`Vault.differences` the read
    hands back. Widen the rule deliberately with ``half_span=`` when the
    outside is regular too and you want it in.
    """
    span = last - first
    picks = sorted({first + (span * i) // (_VAULT_SAMPLES - 1)
                    for i in range(_VAULT_SAMPLES)})
    sections, complaints = [], []
    for along in picks:
        try:
            sections.append(_vault_section(at, along, crown_y, family,
                                           half_span))
        except Refused as problem:
            complaints.append(f"{axis}={along}: {problem}")
    if not sections:
        raise Refused(
            f"no section of the vault between {axis}={first} and {last} "
            f"reads as one -- {complaints[0]}", faults=complaints)

    common = []
    for index in range(max(len(section) for section in sections)):
        tally: dict[tuple, list] = {}
        for section in sections:
            if index < len(section):
                tally.setdefault(_column_key(section[index]),
                                 []).append(section[index])
        agreed = max(tally.values(), key=len, default=[])
        if len(agreed) * 2 <= len(sections):
            break
        common.append(agreed[0])
    if len(common) < 2:
        raise NBTError(
            f"no two courses of this vault read the same over most of "
            f"{axis}={first}..{last}, so there is no one section to extrude. "
            f"Name a stretch that is uniform with low=/high=")
    return common


def _column_key(column) -> tuple:
    top, thickness, pieces = column
    return (top, thickness, tuple(str(piece) for piece in pieces))


def _vault_section(at, along: int, crown_y: int, family: str, half_span):
    """One section, read outward from the crown: the top of every column and
    how thick it is, in half blocks, per side."""
    limit = 64 if half_span is None else int(half_span)
    sides: dict[int, list[tuple[int, int, list[BlockState]]]] = {-1: [], 1: []}
    for sign in (-1, 1):
        from_y = crown_y
        for d in range(0, limit + 1):
            read = _vault_column(at, along, sign * d, family, from_y)
            if read is None:
                if half_span is not None:
                    raise Refused(
                        f"half_span={half_span} was asked for, but the "
                        f"column {abs(d)} out from the crown holds no "
                        f"{family} within {_VAULT_STEP} blocks of the course "
                        f"inside it")
                break
            sides[sign].append(read[:3])
            from_y = read[3]
    if len(sides[-1]) != len(sides[1]):
        raise Refused(
            f"the vault is {len(sides[-1]) - 1} blocks wide on one side of "
            f"its crown and {len(sides[1]) - 1} on the other. A barrel vault "
            f"is symmetric; say half_span= for the part you mean")
    if len(sides[1]) < 2:
        raise Refused(
            "there is one column and no second one beside it, so there is "
            "no profile to read. This is a ridge, not a vault")
    for d, (left, right) in enumerate(zip(sides[-1], sides[1])):
        if (left[0], left[1]) != (right[0], right[1]):
            raise Refused(
                f"the two sides disagree {d} blocks out from the crown: the "
                f"shell tops at {left[0] / 2:g} and is {left[1] / 2:g} thick "
                f"one way, tops at {right[0] / 2:g} and is {right[1] / 2:g} "
                f"thick the other. A barrel vault is symmetric")
    return sides[1]


def _vault_column(at, along: int, offset: int, family: str, from_y: int):
    """``(top, thickness, blocks, top_y)`` of one column, or None past the vault.

    Looks for the family no higher than the course inside this one and no
    more than :data:`_VAULT_STEP` below it -- a local window, so a wall of
    the same stone standing under the eaves is not mistaken for more vault
    -- then keeps going down while the material abuts what is above it.
    Snow is stepped over rather than counted: it lies on a roof, it is not
    the roof, and the same mistake in a hand survey reports the snow depth
    as the roof line.
    """
    top = top_y = None
    pieces: list[BlockState] = []
    bottom = 0
    for y in range(from_y, from_y - _VAULT_STEP - 1, -1):
        found = at(along, offset, y)
        if found is None or _empty(found) or found.name in _MELTABLE:
            if top is not None:
                break
            continue
        if not _in_family(found.name, family):
            if top is not None:
                break
            continue
        low, high = _piece(found, y)
        if top is None:
            top, bottom, top_y = high, low, y
            pieces.append(found)
            continue
        if high != bottom:
            break
        bottom = low
        pieces.append(found)
    if top is None:
        return None
    # Past the window the shell may go on; follow it as far as it abuts.
    y = top_y - len(pieces)
    while True:
        found = at(along, offset, y)
        if (found is None or _empty(found)
                or not _in_family(found.name, family)):
            break
        low, high = _piece(found, y)
        if high != bottom:
            break
        bottom = low
        pieces.append(found)
        y -= 1
    return (top, top - bottom, pieces, top_y)


def _vault_materials(columns, block, springer) -> dict:
    """The rule a read section implies: profile, depth and one block per role.

    Roles, not names: a ``type=double`` slab is a whole course and a
    ``type=top`` one is a half course, though both say ``_slab``.
    """
    crown_top = columns[0][0]
    profile = tuple(crown_top - top for top, _, _ in columns)
    depth = tuple(thickness for _, thickness, _ in columns)
    if profile[0] != 0:
        raise NBTError("the crown column is not the highest; point at the "
                       "ridge, not at the roof beside it")

    seen = {"slab": set(), "stair": set(), "whole": set()}
    for _, _, pieces in columns:
        for piece in pieces:
            if piece.name.endswith("_stairs"):
                seen["stair"].add(str(BlockState(
                    piece.name, {k: v for k, v in piece.properties.items()
                                 if k not in ("facing", "shape")})))
            elif (piece.name.endswith("_slab")
                  and piece.properties.get("type") in ("top", "bottom")):
                seen["slab"].add(str(BlockState(
                    piece.name, {k: v for k, v in piece.properties.items()
                                 if k != "type"})))
            else:
                seen["whole"].add(str(piece))
    if len(seen["slab"]) != 1:
        raise NBTError(
            f"the half courses are not one material: "
            f"{sorted(seen['slab']) or 'there are none'}. A vault read for "
            f"extruding must be uniform, or the run would change stone "
            f"halfway along")
    if len(seen["stair"]) > 1:
        raise NBTError(f"the stair courses are not one material: "
                       f"{sorted(seen['stair'])}")

    out = {
        "profile": profile,
        "depth": depth,
        "slab": BlockState.parse(seen["slab"].pop()),
        "stair": (BlockState.parse(seen["stair"].pop())
                  if seen["stair"] else None),
    }
    # A whole course over a thickened offset is a springer -- the block the
    # vault lands on where it crosses a pier -- and elsewhere it is the
    # haunch. Reading them apart is what lets one rule hold both.
    thickened, plain = set(), set()
    for d, (top, thickness, pieces) in enumerate(columns):
        # The outermost course reaches down to nothing, so it is never a
        # springer -- see Vault._reach, which must agree with this.
        reach = (max(1, top - columns[d + 1][0]) if d + 1 < len(columns)
                 else thickness)
        bucket = thickened if thickness > reach else plain
        for piece in pieces:
            if not (piece.name.endswith("_stairs")
                    or (piece.name.endswith("_slab")
                        and piece.properties.get("type") in ("top", "bottom"))):
                bucket.add(str(piece))
    for role, given, found in (("block", block, plain),
                               ("springer", springer, thickened)):
        if given is not None:
            out[role] = _state(given)
        elif len(found) > 1:
            raise NBTError(
                f"the {role} courses are not one material: {sorted(found)}. "
                f"Say which with {role}=")
        elif found:
            out[role] = BlockState.parse(found.pop())
        else:
            out[role] = None
    if out["block"] is None:
        out["block"] = out["springer"] or BlockState.parse(
            f"{out['slab'].name}[type=double]")
    if out["springer"] is None:
        out["springer"] = out["block"]
    return out


class VaultMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods:
    half-block stepped vaults and arcades.

    Builder inherits them; nothing here is used on its own.
    """

    def vault(self, vault, first, last, *, overwrite: bool = False,
              clear_under: bool = False) -> VaultResult:
        """Extrude a :class:`Vault` along its axis, ``first`` to ``last``.

        The roof :meth:`gable_roof` cannot make: courses half a block apart
        instead of one, so the pitch can be shallow enough to read as a
        vault rather than as a staircase. Pass a :class:`Vault` -- read one
        out of the world with :meth:`Vault.read` to continue or to copy a
        vault that already exists, or make a fresh one -- and the two
        positions along its axis, inclusive, in either order.
        ``first=None`` continues a vault that was read, picking up one past
        whichever end ``last`` is heading for.

        Anything already standing in the shell that is not the block the
        rule wants stops the call by name, before a single course is
        written; ``overwrite=True`` cuts through it and ``clear_under=True``
        additionally empties the span between the springing and the crown,
        which is what a vault dropped onto a solid hall needs.

        A vault with :attr:`~Vault.close_ends` closes both ends of the run
        it is given: the courses at ``first`` and ``last`` are its closed
        section (:meth:`Vault.section`), end walls from the springing up to
        the shell and a whole slab over each wall, held to the same
        occupancy check as the shell -- except that with ``clear_under``
        the walls stand in the span it empties, and what stands there is
        cut for them as for the rest of it. Only a fresh run closes:
        continuing a read vault (``first=None``) given ``close_ends`` is an
        ``NBTError``, since the continuation's first course is the seam
        with the run already standing, which a wall would close off. And a
        run closed in the world is not continued: ``Vault.read`` reads no
        end walls, so when the old run's end course is closed as
        ``close_ends`` closes one -- one block from the springing to the
        shell, its top slabs whole -- the continuation is
        :class:`~heaton_voxel.craft.errors.Refused` naming it, before
        anything is written, rather than leave the wall standing across
        the hall.

        Run :meth:`finish` afterwards like any other build. It matters more
        here than usual: the corner shapes where the vault meets a gable end
        are derived from neighbors, and this writes every stair square.
        """
        if not isinstance(vault, Vault):
            raise NBTError(f"vault takes a Vault, got {type(vault).__name__}; "
                           f"build one with Vault(crown) or Vault.read(save, "
                           f"crown)")
        seam = None
        if first is None:
            if vault.close_ends is not None and vault.found is not None:
                raise NBTError(
                    f"first=None continues the vault read out of the world, "
                    f"and this one was given close_ends={vault.close_ends}, "
                    f"which closes a fresh run: the continuation's first "
                    f"course is the seam with the run already standing, "
                    f"which a wall would close off. Continue it with "
                    f"close_ends left None, or name both ends of a fresh "
                    f"run")
            first = _continuation(vault, last, first="first", last="last",
                                  noun="vault", unit="position", ends="ends")
            # The old run's end course the continuation picks up from.
            seam = (vault.found[1] if int(first) > vault.found[1]
                    else vault.found[0])
        lo, hi = sorted((int(first), int(last)))
        result = VaultResult(lo, hi, vault.axis)
        result.closed = vault.close_ends
        section = vault.section()
        # The run's two end courses, closed when the vault closes its ends.
        ends = (section if vault.close_ends is None
                else vault.section(closed=True))

        planned: dict[tuple[int, int, int], BlockState] = {}
        for along in range(lo, hi + 1):
            for (offset, y), state in (ends if along in (lo, hi)
                                       else section).items():
                planned[vault.cell(along, offset, y)] = state
        # One total order over the whole run: first-write order is the
        # section palette, which is the chunk's bytes, and two builds of the
        # same vault must encode identically.
        order = sorted(planned, key=lambda c: (c[2], c[1], c[0]))

        # The span under the vault first, then the courses over it.
        ops: list[tuple] = []
        cut = None
        if clear_under:
            soffit = min(vault.top(d) - vault.thickness(d)
                         for d in range(len(vault.profile)))
            # What the fill empties is no obstacle to the end walls set in
            # it, as it is none to an open run, which sets nothing there.
            cut = _under_span(vault, lo, hi)
            for along in range(lo, hi + 1):
                for d in range(len(vault.profile)):
                    under = (vault.top(d) - vault.thickness(d)) // 2
                    for offset in ((0,) if d == 0 else (-d, d)):
                        base = vault.cell(along, offset, soffit // 2)
                        head = vault.cell(along, offset, under - 1)
                        if head[1] >= base[1]:
                            ops.append(("fill", base, head, AIR))

        for cell in order:
            state = planned[cell]
            ops.append(("set", cell, state))
            result.blocks += 1
            if state.name.endswith("_stairs"):
                result.stairs += 1
        # The plan's own checks before the shell is read: a block the game
        # lacks, a course in a chunk with nothing to build into or past the
        # world, refuses the call as _commit would.
        self._vet_plan(ops, what="vault")

        if seam is not None:
            walled = _closed_end(vault, self._read, seam)
            if walled is not None:
                fault = (f"{vault.axis}={seam}: walled with {walled} from "
                         f"y={vault.springing} up to the shell, its top "
                         f"slabs made whole")
                raise Refused(
                    f"the run being continued ends at {vault.axis}={seam} "
                    f"in a wall, as close_ends closes a run -- {walled} from "
                    f"the springing at y={vault.springing} up to the shell "
                    f"-- and a continuation would leave it standing across "
                    f"the hall. A closed run is not continued: knock the "
                    f"end wall out first, or build the longer run fresh "
                    f"with both its ends named. Nothing was written",
                    faults=[fault])

        already, blocked, _ = _occupancy(
            self._read, ((cell, planned[cell]) for cell in order),
            same=_same_course, empty=_empty, overwrite=overwrite, cut=cut)
        result.already += len(already)
        if blocked:
            raise Refused(
                f"{len(blocked)} cells the vault needs are already taken, "
                f"first: {blocked[0]}. Clear the roof line first, or pass "
                f"overwrite=True to cut through whatever is there", faults=blocked)
        self._commit(ops, what="vault")
        result.courses = hi - lo + 1
        return result

    def arcade(self, start, end, y0, y1, *, pitch: int = 6,
               pier="minecraft:quartz_block", cap=None, base=None,
               phase: int | None = None) -> tuple[tuple[int, int], ...]:
        """A line of piers at a fixed pitch: the bay rhythm a hall is built on.

        ``start`` and ``end`` are ``(x, z)`` on one axis; piers stand every
        ``pitch`` blocks between them, from ``y0`` to ``y1`` inclusive, with
        an optional ``base`` course at the bottom and ``cap`` at the top --
        the impost a :meth:`vault` springs from. Returns the pier columns,
        which are the bay positions everything else in the hall should key
        off: the windows between them, the lamps hung down the middle.

        The rhythm is a congruence on world coordinates, not a count from
        one end, so the two sides of a nave line up on their own as long as
        both runs start level -- and a run extended later picks the bays
        back up instead of restarting the pattern. ``phase`` shifts it for
        runs that do not start level; by default the first pier stands on
        ``start``. The quartz pier is the great hall's stone, and
        ``pitch=6`` a choice, not a measurement of it: its arcades are
        solid walls (see "Where the defaults came from" in
        :mod:`heaton_voxel.craft.build`).
        """
        ax, az = int(start[0]), int(start[-1])
        bx, bz = int(end[0]), int(end[-1])
        if ax != bx and az != bz:
            raise NBTError(
                f"an arcade runs along one axis, and {(ax, az)} to "
                f"{(bx, bz)} is diagonal")
        pitch = int(pitch)
        if pitch < 1:
            raise NBTError(f"piers stand at least one block apart, got "
                           f"pitch={pitch}")
        lo, hi = sorted((int(y0), int(y1)))
        along = "z" if ax == bx else "x"
        a0, a1 = sorted((az, bz) if along == "z" else (ax, bx))
        seed = az if along == "z" else ax
        offset = (seed if phase is None else int(phase)) % pitch

        columns = tuple(
            (ax, a) if along == "z" else (a, az)
            for a in range(a0, a1 + 1) if a % pitch == offset)
        ops: list[tuple] = []
        for x, z in columns:
            for y in range(lo, hi + 1):
                ops.append(("set", (x, y, z), pier))
            if base is not None:
                ops.append(("set", (x, lo, z), base))
            if cap is not None:
                ops.append(("set", (x, hi, z), cap))
        self._commit(ops, what="arcade")
        return columns
