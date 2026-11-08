"""Crowns: what stands on a tower's top tier.

A :class:`~heaton_voxel.craft.build.roof.Roof` is a rise over distance, one
surface from the eaves to the ridge, and that is a roof. A skyscraper's top
is not one surface. The Palace Hotel in Greenfield (read 2026-11-01, x 106..128,
z -523..-485, ground y=68, 138 rows) ends in glass rings standing one inside
another, each two courses high, a liner of wool inside each, and four
fins climbing the diagonals from the cornice's corners to a point, a lamp at
each of the last steps. From outside the rings read as a spire and the fins
as four ribs; from above, both are drawn as the same few cells.

So a :class:`Crown` is that rule. Everything is counted in from the rectangle
it stands on, which it takes as an **inset**, the columns in from the
rectangle's edge, so any rectangle takes it: a square's rings close to a point
and a long plan's to a ridge.

    Crown(((2, 2), (2, 3), (2, 4), (2, 5)),      # (rows, inset), bottom up
          skin=GLASS, corner=WOOL, liner=WOOL,
          fin=WOOL, reach=-1, inner=4, rise=3, pitch=2,
          lamp=GLOWSTONE, lamps=4,
          cornice=WOOL, brackets=2, deck=WOOL)

**A ring** is the perimeter of the rectangle at its inset: ``corner`` at its
four corners, ``skin`` between, and ``liner`` round the perimeter one further
in, which for a ring three wide is its center column. A ring that narrow has
no side left to frame, so its corners are skin too.

**A fin** climbs a diagonal. Its cell at inset ``i`` stands from the crown's
first row up to ``rise - 1 + pitch * (i - reach)`` over it, for every ``i``
from ``reach``, which is outside the rectangle where it is negative, to
``inner``; so the fin's top climbs ``pitch`` rows a column inward, and it
starts ``rise`` rows high. A ring takes over where it stands: the fin stops
short of the ring's corner, and runs on to ``inner`` over the last ring.
``lamp`` tops each of the last ``lamps`` steps, the point among them.

**Under it** ``cornice`` is the course round the rectangle one column out, at
the row under the crown's first; ``brackets`` rows under that hold it up, a
block either side of each corner; and ``deck`` closes the rectangle inside its
walls at the cornice's row, so nothing falls into the tower from the crown.

The rule says nothing about how you get up it, and a ladder through it is the
caller's, cut after. The writer is :meth:`CrownMixin.crown`, and
:meth:`~heaton_voxel.craft.build.massing.MassingMixin.tower` lays one on its
top tier. Part of :mod:`heaton_voxel.craft.build`. No world in the rule.
"""

from __future__ import annotations

import copy

from ...nbt import NBTError
from ._common import _same_crown, _whole
from .walls import _Picks, _Recolor, _whole_block


def _perimeter(x0: int, z0: int, x1: int, z1: int) -> list:
    """The columns round a rectangle, once each, in ``(x, z)`` order."""
    return sorted({(x, z) for x in range(x0, x1 + 1) for z in (z0, z1)}
                  | {(x, z) for z in range(z0, z1 + 1) for x in (x0, x1)})


class Crown:
    """Rings one inside another, and fins up the diagonals. See the module
    docstring. Every block is a block, a
    :class:`~heaton_voxel.craft.style.Material` (its block) or a
    :class:`~heaton_voxel.craft.style.Mix`, picked at the world cell.

    ``rings`` is ``(rows, inset)`` pairs, bottom up, each at least as far in
    as the last. ``skin`` is required, and the rest are optional: with no
    ``corner`` a ring's corners are skin, with no ``liner`` it has none, with
    no ``fin`` there are no fins.

    Refused (an ``NBTError``): no rings, a ring of no rows or that steps
    outward, a fin without its ``reach``, an ``inner`` short of the reach, a
    ``rise`` or ``pitch`` under one, more ``lamps`` than steps, lamps without
    a ``lamp``. A ring too far in for the rectangle is refused by :meth:`plan`.
    """

    __slots__ = ("rings", "skin", "corner", "liner", "fin", "reach", "inner",
                 "rise", "pitch", "lamp", "lamps", "cornice", "brackets",
                 "deck")

    def __init__(self, rings, *, skin, corner=None, liner=None, fin=None,
                 reach=None, inner=None, rise: int = 1, pitch: int = 1,
                 lamp=None, lamps: int = 0, cornice=None, brackets: int = 0,
                 deck=None):
        owner, noun = "Crown", "a crown"
        steps = []
        for item in rings:
            try:
                rows, inset = item
            except (TypeError, ValueError):
                raise NBTError(f"a crown's ring is (rows, inset), got "
                               f"{item!r}") from None
            steps.append((_whole(rows, "a ring's rows"),
                          _whole(inset, "a ring's inset")))
        if not steps:
            raise NBTError("a crown has at least one ring")
        if any(rows < 1 for rows, _ in steps):
            raise NBTError(f"a ring is at least one row high, got {steps}")
        insets = [inset for _, inset in steps]
        if insets != sorted(insets):
            raise NBTError(f"a crown's rings step inward as they rise, got "
                           f"insets {insets}")
        self.rings = tuple(steps)
        self.skin = _whole_block(skin, "skin", owner, noun)
        self.corner = _whole_block(corner, "corner", owner, noun, none=True)
        self.liner = _whole_block(liner, "liner", owner, noun, none=True)
        self.fin = _whole_block(fin, "fin", owner, noun, none=True)
        self.lamp = _whole_block(lamp, "lamp", owner, noun, none=True)
        self.cornice = _whole_block(cornice, "cornice", owner, noun, none=True)
        self.deck = _whole_block(deck, "deck", owner, noun, none=True)
        self.rise = _whole(rise, "rise")
        self.pitch = _whole(pitch, "pitch")
        self.lamps = _whole(lamps, "lamps")
        self.brackets = _whole(brackets, "brackets")
        if self.rise < 1 or self.pitch < 1:
            raise NBTError(f"a fin rises at least a row at its foot and a row "
                           f"a column, got rise={rise} and pitch={pitch}")
        if self.brackets < 0:
            raise NBTError(f"brackets is rows, got {brackets}")
        if self.fin is None:
            if reach is not None or inner is not None or self.lamps:
                raise NBTError("reach, inner and lamps say how a fin stands, "
                               "and this crown has no fin=")
            self.reach = self.inner = None
        else:
            if reach is None:
                raise NBTError("a fin starts somewhere: pass reach=, its "
                               "inset at the foot, negative outside the "
                               "rectangle")
            self.reach = _whole(reach, "reach")
            self.inner = (insets[-1] - 1 if inner is None
                          else _whole(inner, "inner"))
            if self.inner < self.reach:
                raise NBTError(f"a fin runs inward from its reach, "
                               f"{self.reach}, and inner={self.inner} is "
                               f"short of it")
            if self.lamps < 0 or self.lamps > self.inner - self.reach + 1:
                raise NBTError(f"a fin of {self.inner - self.reach + 1} steps "
                               f"has no room for lamps={lamps}")
        if self.lamps and self.lamp is None:
            raise NBTError(f"lamps={lamps} names steps to light, and this "
                           f"crown has no lamp=")

    @property
    def height(self) -> int:
        """Rows from the crown's first row to its highest."""
        rings = sum(rows for rows, _ in self.rings)
        if self.fin is None:
            return rings
        return max(rings, self._fin_top(self.inner) + 1)

    def _fin_top(self, inset: int) -> int:
        """The fin's top row at ``inset``, over the crown's first."""
        return self.rise - 1 + self.pitch * (inset - self.reach)

    def _ring_at(self) -> dict:
        """The inset of the ring standing at each row over the first."""
        at: dict = {}
        row = 0
        for rows, inset in self.rings:
            for k in range(rows):
                at[row + k] = inset
            row += rows
        return at

    def plan(self, x0, z0, x1, z1, y: int) -> dict:
        """Every cell the crown writes, ``{(x, y, z): state}``, on the
        rectangle ``(x0, z0)`` to ``(x1, z1)`` with its first row at ``y``.
        A Mix is picked at each cell. Pure: it reads no world.

        Refused (an ``NBTError``): a ring or a fin that does not fit."""
        x0, x1 = sorted((int(x0), int(x1)))
        z0, z1 = sorted((int(z0), int(z1)))
        y = int(y)
        room = (min(x1 - x0, z1 - z0) + 1) // 2
        deepest = self.rings[-1][1]
        if self.fin is not None:
            deepest = max(deepest, self.inner)
        if deepest >= room:
            raise NBTError(f"a crown reaching {deepest} columns in does not "
                           f"fit a rectangle {x1 - x0 + 1} by {z1 - z0 + 1}")
        pick = _Picks()
        out: dict = {}

        def put(cell, value):
            out[cell] = pick(value, cell)

        def inset(i):
            return (x0 + i, z0 + i, x1 - i, z1 - i)

        below = y - 1
        if self.deck is not None:
            for x in range(x0 + 1, x1):
                for z in range(z0 + 1, z1):
                    put((x, below, z), self.deck)
        if self.cornice is not None:
            for x, z in _perimeter(*inset(-1)):
                put((x, below, z), self.cornice)
            for k in range(1, self.brackets + 1):
                for x, z in ((x0 - 1, z0), (x0, z0 - 1), (x1 + 1, z0),
                             (x1, z0 - 1), (x0 - 1, z1), (x0, z1 + 1),
                             (x1 + 1, z1), (x1, z1 + 1)):
                    put((x, below - k, z), self.cornice)

        at = self._ring_at()
        for row, i in sorted(at.items()):
            level = y + row
            a0, b0, a1, b1 = inset(i)
            narrow = min(a1 - a0, b1 - b0) < 3
            ends = {(a, b) for a in (a0, a1) for b in (b0, b1)}
            for x, z in _perimeter(a0, b0, a1, b1):
                value = (self.corner if (x, z) in ends
                         and self.corner is not None and not narrow
                         else self.skin)
                put((x, level, z), value)
            if self.liner is not None and a0 + 1 <= a1 - 1 and b0 + 1 <= b1 - 1:
                for x, z in _perimeter(a0 + 1, b0 + 1, a1 - 1, b1 - 1):
                    put((x, level, z), self.liner)

        if self.fin is not None:
            first_lamp = self.inner - self.lamps + 1
            for i in range(self.reach, self.inner + 1):
                top = self._fin_top(i)
                for row in range(top + 1):
                    if row in at and i >= at[row]:
                        continue            # the ring has it from here in
                    value = (self.lamp if row == top and self.lamps
                             and i >= first_lamp else self.fin)
                    a0, b0, a1, b1 = inset(i)
                    for x, z in ((a0, b0), (a1, b0), (a0, b1), (a1, b1)):
                        put((x, y + row, z), value)
        return out

    def recolored(self, mapping) -> "Crown":
        """The same crown in other blocks: ``{block: block}``, by id.
        Refused (an ``NBTError``): a key the crown does not hold."""
        table = _Recolor(mapping)
        rule = self._recolored(table)
        table.check()
        return rule

    def _recolored(self, table) -> "Crown":
        rule = copy.copy(self)
        for name in ("skin", "corner", "liner", "fin", "lamp", "cornice",
                     "deck"):
            setattr(rule, name, table(getattr(self, name)))
        return rule

    def verify(self, save, x0, z0, x1, z1, y: int, *,
               dimension: str | None = None) -> list:
        """Every cell of the crown not as the rule says, in words: what a
        crown has to answer to, since nothing here reads one."""
        wrong = []
        for cell, want in sorted(self.plan(x0, z0, x1, z1, y).items()):
            found = save.get_block(*cell, dimension=dimension)
            if found is None or not _same_crown(found, want):
                wrong.append(f"{cell} holds {found}, where the crown has "
                             f"{want}")
        return wrong

    def __repr__(self) -> str:
        return f"Crown({list(self.rings)!r}, skin={self.skin!r})"


class CrownMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods: crowns.

    Builder inherits them; nothing here is used on its own.
    """

    def crown(self, rule, x0, z0, x1, z1, y: int, *,
              overwrite: bool = False) -> int:
        """Lay a :class:`Crown` on the rectangle ``(x0, z0)`` to ``(x1,
        z1)``, its first row at ``y``, and return how many cells it wrote.

        A cell already right is left as it stands; anything else in the way
        is refused by name, every cell in the refusal's faults, before a
        block is written, unless ``overwrite=True``, and a cell a flight or
        an approach this session built claims is refused whatever it says.
        Then the plan is audited for what the game will not keep. Refused
        before anything is read: a block the game lacks, a chunk not
        generated or not finished, a height past the world.
        """
        if not isinstance(rule, Crown):
            raise NBTError(f"crown takes a Crown, got {type(rule).__name__}")
        planned = sorted(rule.plan(x0, z0, x1, z1, y).items())
        self._vet_plan([("set", cell, state) for cell, state in planned],
                       what="crown")
        _, faults, writes = self._settle(
            planned, fabric={}, opened={}, unglazed=set(),
            overwrite=overwrite, noun="crown")
        self._refuse_taken(
            faults, what="crown",
            advice="Clear the top first, or pass overwrite=True to build "
                   "through whatever is there")
        ops = [("set", cell, state) for cell, state in writes]
        self._audit_plan(ops, what="crown")
        self._commit(ops, what="crown")
        return len(ops)


__all__ = ["Crown", "CrownMixin"]
