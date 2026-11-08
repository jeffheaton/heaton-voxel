"""Massing: a tower as the rectangles it steps back through.

An :class:`~heaton_voxel.craft.build.elevation.Elevation` says one wall. A
tower that steps back has several sets of walls, one over another, and the
step is what makes it read as a tower and not a block. The Palace Hotel in
Greenfield (read 2026-11-01) is 138 rows on a 23 by 39 plan: a lobby behind an
arcade, a 21 by 37 shaft to row 79, a 17 by 33 one to row 109, a 13 square to
row 123, and a crown. Each wall of each of those reads as one Elevation, none
wrong; what no Elevation can say is that they are one building.

So a :class:`Massing` is a stack of :class:`Tier` objects, bottom up, each a
rectangle, the rows its walls stand in, its stories and its rule. A tier
stands on a floor of the one under it, at the rows it steps back from it:
that tier's walls run on as a parapet round the terrace, and the terrace is
the part of that floor the tier over it leaves open.

**A tier can be read.** :meth:`Massing.read` takes a box and finds them: each
row's outline is the rectangle whose four edges hold most of the row's solid
blocks, which leaves out an arcade's piers, a sign and a crown's fins, and rows
with one outline are one tier. Then each tier's walls are read as Elevations
and replayed, and a tier that does not read is said to, in ``unread``, rather
than dropped, since a podium or a one-row cornice is part of the massing with
no wall worth a rule.

**A core goes through all of them.** A :class:`Core` is the wall round a
stair or a ladder, from the ground floor up: every floor stops at it, and
each one needs a way out of it. The office tower wrote that by hand, and a
stair settles it: a flight arrives at one end, so given a
:class:`~heaton_voxel.craft.build.stairs.SwitchbackStair` a core opens at that
end on every floor, and refuses a floor that is not where the stair lands.

**:meth:`MassingMixin.tower` lays it all as one call,** planned whole and
checked before its first write, as a shell is: the tiers bottom up, their
floors stopping at the core, the terraces, the core and what climbs it, and
the crown. Given a ``doorstep`` it walks the plan, without a jump, both ways,
to every cell of every floor before anything is written, which is the check
the office tower's script ran after it had built.

**A tier need not be a rectangle, and a stack need not be one stack.**
:meth:`Tier.over` stands a tier on a
:class:`~heaton_voxel.craft.build.walls.Footprint`, an L, a T, a U, and each
tier stands on the nearest tier under it that holds it, so two towers on one
base are three tiers side by side over the first, each with a core of its own.

Part of :mod:`heaton_voxel.craft.build`. Tiers, massings and cores have no
world in them.
"""

from __future__ import annotations

import copy

from ...nbt import NBTError
from ..block_registry import FLUIDS
from ..errors import Refused
from ..survey import reachable, StepSpace
from ._common import _empty, _head_room, _passable, _whole
from .crown import Crown
from .elevation import Elevation, floors_behind
from .stairs import Ladder, SwitchbackStair
from .walls import (_FaceRule, _opening_at, _Picks, _Recolor, _whole_block,
                    Facade, Footprint, Opening, Wall)

#: The share of a side a row's outline has to fill. An arcade's piers come to
#: a seventh of theirs and a lit sign to a half of its own; a wall with its
#: windows and doors fills all of it.
OUTLINE_SHARE = 0.75


def _rect(x0, z0, x1, z1) -> tuple:
    xs = sorted((_whole(x0, "x0"), _whole(x1, "x1")))
    zs = sorted((_whole(z0, "z0"), _whole(z1, "z1")))
    return xs[0], zs[0], xs[1], zs[1]


def _area(box) -> int:
    return (box[2] - box[0] + 1) * (box[3] - box[1] + 1)


def _rule_of(rule, owner: str):
    if rule is not None and not isinstance(rule, _FaceRule):
        raise NBTError(f"{owner} is a Facade or an Elevation, got "
                       f"{type(rule).__name__}")
    return rule


class Tier:
    """One rectangle of a tower: ``(x0, z0)`` to ``(x1, z1)``, inclusive, its
    walls from the row over ``floor_y`` to ``top``. No world in it.

    ``rule`` is a :class:`~heaton_voxel.craft.build.walls.Facade` or an
    :class:`~heaton_voxel.craft.build.elevation.Elevation` for every wall,
    and ``faces`` by name where one differs, as
    :meth:`~heaton_voxel.craft.build.walls.WallsMixin.shell` takes them.
    ``stories`` are its upper floors, ``floor`` what they are laid in. For
    a tier with another over it the last of ``stories`` is what that one
    stands on, and ``terrace`` is what the part of it left open is laid in,
    ``floor`` if none is given. ``ground`` is the whole footprint at
    ``floor_y`` and ``deck`` at ``top + 1``, and either goes on the tier
    that has one: a tower's first and its last. ``parapet`` is a course
    ``parapet_height`` high round a deck, a flat roof's, and ``roof`` a
    :class:`~heaton_voxel.craft.build.roof.Roof` in its place, with its
    ``attic``, as :meth:`shell` takes them. ``openings`` are cut in its
    walls as :meth:`shell` cuts them, ``(face, Opening, at)`` or ``(face,
    Opening, at, story)``: an entrance a rule does not say, or a way out onto
    a terrace. :meth:`over` makes one on a
    :class:`~heaton_voxel.craft.build.walls.Footprint`, whose rectangle is its
    bounds.

    A tier read off a building has no ``floor``, and no ``rule`` where its
    walls would not read.

    Refused (an ``NBTError``): a rule that is no rule, ``faces`` naming no
    wall; and everything :class:`~heaton_voxel.craft.build.walls.Wall` refuses
    of the rows and stories, when it makes its walls.
    """

    __slots__ = ("x0", "z0", "x1", "z1", "rule", "faces", "floor_y", "top",
                 "stories", "floor", "terrace", "ground", "deck", "thickness",
                 "openings", "footprint", "parapet", "parapet_height", "roof",
                 "attic")

    def __init__(self, x0, z0, x1, z1, rule=None, *, floor_y: int, top: int,
                 stories=(), faces=None, floor=None, terrace=None, ground=None,
                 deck=None, thickness: int = 1, openings=(), footprint=None,
                 parapet=None, parapet_height: int = 1, roof=None, attic=None):
        self.x0, self.z0, self.x1, self.z1 = _rect(x0, z0, x1, z1)
        if footprint is not None and not isinstance(footprint, Footprint):
            raise NBTError(f"a tier's footprint is a Footprint, got "
                           f"{type(footprint).__name__}")
        self.footprint = footprint
        self.rule = _rule_of(rule, "a tier's rule")
        named = dict(faces or {})
        unknown = sorted(set(named) - {"north", "east", "south", "west"})
        if unknown:
            raise NBTError(f"faces names the walls north, east, south and "
                           f"west, got {unknown}")
        for name, face in named.items():
            _rule_of(face, f"faces[{name!r}]")
        self.faces = named
        self.floor_y = _whole(floor_y, "floor_y")
        self.top = _whole(top, "top")
        self.stories = tuple(_whole(level, "a story's floor")
                             for level in stories)
        owner, noun = "Tier", "a tier"
        self.floor = _whole_block(floor, "floor", owner, noun, none=True)
        self.terrace = _whole_block(terrace, "terrace", owner, noun, none=True)
        self.ground = _whole_block(ground, "ground", owner, noun, none=True)
        self.deck = _whole_block(deck, "deck", owner, noun, none=True)
        self.parapet = _whole_block(parapet, "parapet", owner, noun, none=True)
        self.parapet_height = _whole(parapet_height, "parapet_height")
        self.roof = roof
        self.attic = attic
        self.thickness = _whole(thickness, "thickness")
        self.openings = tuple(openings)

    @classmethod
    def over(cls, footprint, rule=None, **kept) -> "Tier":
        """A tier on a :class:`~heaton_voxel.craft.build.walls.Footprint`,
        the rest as :class:`Tier` takes it. Its walls are the footprint's, so
        its rule is a
        :class:`~heaton_voxel.craft.build.elevation.Fitted` unless every
        wall of a side is one length, and ``faces`` names them by the way
        they face. A footprint that is a rectangle makes a plain tier."""
        if not isinstance(footprint, Footprint):
            raise NBTError(f"Tier.over takes a Footprint, got "
                           f"{type(footprint).__name__}")
        plain = len(footprint.columns) == _area(footprint.bounds)
        return cls(*footprint.bounds, rule,
                   footprint=None if plain else footprint, **kept)

    @property
    def width(self) -> int:
        """Columns along x."""
        return self.x1 - self.x0 + 1

    @property
    def depth(self) -> int:
        """Columns along z."""
        return self.z1 - self.z0 + 1

    @property
    def rows(self) -> range:
        """The rows its walls stand in."""
        return range(self.floor_y + 1, self.top + 1)

    @property
    def rectangle(self) -> tuple:
        return (self.x0, self.z0, self.x1, self.z1)

    @property
    def inside(self) -> tuple:
        """The rectangle inside its walls."""
        t = self.thickness
        return (self.x0 + t, self.z0 + t, self.x1 - t, self.z1 - t)

    @property
    def columns(self) -> frozenset:
        """Every column it stands on."""
        if self.footprint is not None:
            return self.footprint.columns
        return frozenset((x, z) for x in range(self.x0, self.x1 + 1)
                         for z in range(self.z0, self.z1 + 1))

    def holds(self, other) -> bool:
        """Whether ``other`` stands within this one's columns."""
        if self.footprint is None and other.footprint is None:
            return (self.x0 <= other.x0 and other.x1 <= self.x1
                    and self.z0 <= other.z0 and other.z1 <= self.z1)
        return other.columns <= self.columns

    def encloses(self, x: int, z: int) -> bool:
        """Whether the column is inside its walls."""
        if self.footprint is not None:
            return (x, z) in self.footprint.inner
        a0, b0, a1, b1 = self.inside
        return a0 <= x <= a1 and b0 <= z <= b1

    def fits(self, core) -> bool:
        """Whether ``core`` stands inside its walls."""
        if self.footprint is not None:
            return core.columns <= self.footprint.inner
        a0, b0, a1, b1 = self.inside
        return (a0 <= core.x0 and core.x1 <= a1
                and b0 <= core.z0 and core.z1 <= b1)

    def walls(self) -> dict:
        """Its :class:`~heaton_voxel.craft.build.walls.Wall` objects, four
        for a rectangle, by name."""
        if self.footprint is not None:
            return self.footprint.walls(floor_y=self.floor_y, top=self.top,
                                        stories=self.stories)
        return Wall.ring(self.x0, self.z0, self.x1, self.z1,
                         floor_y=self.floor_y, top=self.top,
                         thickness=self.thickness, stories=self.stories)

    def rule_of(self, side: str):
        """The rule of one wall, ``faces`` by its whole name or the way it
        faces, the tier's own where ``faces`` names neither."""
        return self.faces.get(side, self.faces.get(side.split("-")[0],
                                                   self.rule))

    def floor_cells(self, level: int, *, voids=()) -> list:
        """Every ``(x, level, z)`` inside its walls, in x then z order, but
        the columns ``voids``."""
        skip = set(voids)
        if self.footprint is not None:
            return [(x, level, z) for x, z in sorted(self.footprint.inner)
                    if (x, z) not in skip]
        a0, b0, a1, b1 = self.inside
        return [(x, level, z) for x in range(a0, a1 + 1)
                for z in range(b0, b1 + 1) if (x, z) not in skip]

    def _recolored(self, table) -> "Tier":
        tier = copy.copy(self)
        tier.rule = None if self.rule is None else self.rule._recolored(table)
        tier.faces = {side: rule._recolored(table)
                      for side, rule in self.faces.items()}
        for name in ("floor", "terrace", "ground", "deck", "parapet"):
            setattr(tier, name, table(getattr(self, name)))
        return tier

    def __repr__(self) -> str:
        return (f"Tier({self.x0}, {self.z0}, {self.x1}, {self.z1}, rows "
                f"{self.floor_y + 1}..{self.top}, "
                f"{len(self.stories)} upper floors)")


def _solid(state) -> bool:
    """Whether a row's outline counts a block: anything but air and water,
    since a fountain against a wall is not the wall."""
    return not (state is None or _empty(state) or state.name in FLUIDS)


def _outline(cells: set):
    """The rectangle of a row: its bounding box, the side whose edge holds
    the least of the row trimmed while one holds under
    :data:`OUTLINE_SHARE`. ``None`` if nothing stands."""
    if not cells:
        return None
    x0, x1 = min(c[0] for c in cells), max(c[0] for c in cells)
    z0, z1 = min(c[1] for c in cells), max(c[1] for c in cells)
    while x0 <= x1 and z0 <= z1:
        across = x1 - x0 + 1
        along = z1 - z0 + 1
        sides = (
            (sum((x0, z) in cells for z in range(z0, z1 + 1)) / along, 0),
            (sum((x1, z) in cells for z in range(z0, z1 + 1)) / along, 1),
            (sum((x, z0) in cells for x in range(x0, x1 + 1)) / across, 2),
            (sum((x, z1) in cells for x in range(x0, x1 + 1)) / across, 3),
        )
        share, side = min(sides)
        if share >= OUTLINE_SHARE:
            return (x0, z0, x1, z1)
        if side == 0:
            x0 += 1
        elif side == 1:
            x1 -= 1
        elif side == 2:
            z0 += 1
        else:
            z1 -= 1
    return None


class Massing:
    """A tower's tiers, bottom up. See the module docstring.

    Each tier stands on the nearest tier under it that holds it: its
    ``floor_y`` is one of that tier's ``stories``, or the row over its walls,
    and its columns lie within that one's. So a plain stack is what it looks
    like, and tiers listed side by side over one tier are two towers on one
    base; they may not overlap. Read off a building, tiers are what stands, so
    that is asked of a Massing that is laid and not of one that was read.

    ``unread`` is ``{index: reason}``: the tiers :meth:`read` found and could
    not make a rule for, each with why.

    Refused (an ``NBTError``): no tiers, a tier that is no :class:`Tier`.
    """

    __slots__ = ("tiers", "unread")

    def __init__(self, *tiers, unread=None):
        for tier in tiers:
            if not isinstance(tier, Tier):
                raise NBTError(f"a massing is made of Tiers, got "
                               f"{type(tier).__name__}")
        if not tiers:
            raise NBTError("a massing has at least one tier")
        self.tiers = tuple(tiers)
        self.unread = dict(unread or {})

    @property
    def top(self) -> int:
        """The highest wall row."""
        return max(tier.top for tier in self.tiers)

    @property
    def stories(self) -> tuple:
        """Every upper floor of every tier, lowest first, once each."""
        return tuple(sorted({level for tier in self.tiers
                             for level in tier.stories}))

    @classmethod
    def read(cls, save, corner1, corner2, *, depth: int = 2,
             rules: bool = True, dimension: str | None = None) -> "Massing":
        """The tiers of the building in the box from ``corner1`` to
        ``corner2``, ``(x, y, z)``. Reads only.

        Rows sharing an outline are one tier; see the module docstring. With
        ``rules`` each tier's stories come from
        :func:`~heaton_voxel.craft.build.elevation.floors_behind` and each of
        its walls from :meth:`Elevation.read`, which replays what it reads;
        one that does not reproduce, or a tier too small to have walls,
        leaves the tier without a rule and says why in ``unread``. Refused (an
        ``NBTError``): a box with nothing in it, and a cell of a wall that
        cannot be read, which is not the same as one that does not repeat.
        """
        ax, bx = sorted((int(corner1[0]), int(corner2[0])))
        ay, by = sorted((int(corner1[1]), int(corner2[1])))
        az, bz = sorted((int(corner1[2]), int(corner2[2])))
        runs: list = []
        for y in range(ay, by + 1):
            cells = {(x, z) for x in range(ax, bx + 1)
                     for z in range(az, bz + 1)
                     if _solid(save.get_block(x, y, z, dimension=dimension))}
            outline = _outline(cells)
            if outline is None:
                continue
            if runs and runs[-1][0] == outline and runs[-1][2] == y - 1:
                runs[-1][2] = y
            else:
                runs.append([outline, y, y])
        if not runs:
            raise NBTError(f"nothing stands in the box {corner1}..{corner2}")
        tiers, unread = [], {}
        for index, (outline, low, high) in enumerate(runs):
            kept = dict(floor_y=low - 1, top=high)
            tier = Tier(*outline, **kept)
            if rules and high == low:
                unread[index] = "one row is a course, and no wall to read"
            elif rules and min(tier.width, tier.depth) < 3:
                unread[index] = f"{tier.width} by {tier.depth} has no walls"
            elif rules:
                stories = floors_behind(save, tier.walls()["north"],
                                        dimension=dimension)
                story = Tier(*outline, stories=stories, **kept)
                try:
                    faces = {name: Elevation.read(save, wall, depth=depth,
                                                  dimension=dimension)
                             for name, wall in story.walls().items()}
                except Refused as exc:
                    unread[index] = str(exc)
                else:
                    tier = Tier(*outline, faces["north"], faces=faces,
                                stories=stories, **kept)
            tiers.append(tier)
        return cls(*tiers, unread=unread)

    def verify(self, save, *, dimension: str | None = None) -> list:
        """Every wall cell not as its tier's rule says, in words. A tier with
        no rule has nothing to answer to."""
        wrong = []
        for index, tier in enumerate(self.tiers):
            if tier.rule is None and not tier.faces:
                continue
            for name, wall in tier.walls().items():
                rule = tier.rule_of(name)
                if rule is None:
                    continue
                cut: dict = {}
                for item in tier.openings:
                    face, opening, at, story = _opening_at(item, faced=True)
                    if face == name:
                        cut.update(opening.cells(wall, at, story=story))
                floored = {cell for cell in rule.plan(wall)
                           if cell[1] in tier.stories
                           and tier.encloses(cell[0], cell[2])}
                wrong += [f"tier {index}, {name} wall: {fault}" for fault in
                          rule.verify(save, wall, dimension=dimension, cut=cut,
                                      skip=floored)]
        return wrong

    def recolored(self, mapping) -> "Massing":
        """The same tower in other blocks, ``{block: block}`` by id, every
        tier's rule, floors and terraces with it: a building read off a save
        and repainted, say. Refused (an ``NBTError``): a key nothing in it
        holds."""
        table = _Recolor(mapping)
        massing = Massing(*(tier._recolored(table) for tier in self.tiers),
                          unread=self.unread)
        table.check()
        return massing

    def describe(self) -> str:
        """The tiers in words, bottom up, one line each."""
        lines = []
        below = None
        for index, tier in enumerate(self.tiers):
            rows = (f"row {tier.top}" if len(tier.rows) == 1
                    else f"rows {tier.rows[0]}..{tier.top}")
            size = f"{tier.width} by {tier.depth}"
            parts = [f"{rows}: {size} at ({tier.x0}, {tier.z0})"]
            if below is not None:
                step = [tier.x0 - below.x0, below.x1 - tier.x1,
                        tier.z0 - below.z0, below.z1 - tier.z1]
                if min(step) < 0:
                    parts.append("overhanging the tier below")
                elif max(step) > 0:
                    parts.append("set back "
                                 + (str(step[0]) if len(set(step)) == 1
                                    else ", ".join(map(str, step))))
            if tier.stories:
                parts.append(f"{len(tier.stories)} upper floors")
            if tier.rule is None and index in self.unread:
                parts.append("walls not read")
            lines.append(", ".join(parts))
            below = tier
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"Massing({len(self.tiers)} tiers to row {self.top})"


class Core:
    """The wall round what climbs a tower: the rectangle ``(x0, z0)`` to
    ``(x1, z1)``, that wall included, which no floor crosses. No world in it.

    ``wall`` is a block, a Material or a Mix, or a Facade or an Elevation for
    a core with a face of its own. ``stair`` is a
    :class:`~heaton_voxel.craft.build.stairs.SwitchbackStair` or ``ladder`` a
    :class:`~heaton_voxel.craft.build.stairs.Ladder`, inside the wall, or
    both or neither. ``top`` is the wall's top row, the massing's by default.

    ``doors`` is ``{floor: (face, at, width)}``, a hole in that face of the
    wall over that floor, ``height`` rows high, ``at`` its low column. With a
    stair and no ``doors`` it opens the whole end of the shaft that each
    flight arrives at, and the end the first sets out from on the ground
    floor, so a hallway is what is left over. A floor with no door has none.

    ``lamp`` is a block set in the middle of the two walls beside the run,
    two rows over every floor it serves and every turn of its stair, so a
    shaft is lit from inside; it lights the floor outside as well.

    Refused (an ``NBTError``): a wall that is none, a stair or a ladder that
    is none, either outside the wall, a door in no face.
    """

    __slots__ = ("x0", "z0", "x1", "z1", "wall", "stair", "ladder", "doors",
                 "top", "height", "lamp")

    def __init__(self, x0, z0, x1, z1, *, wall, stair=None, ladder=None,
                 doors=None, top=None, height: int = 3, lamp=None):
        self.x0, self.z0, self.x1, self.z1 = _rect(x0, z0, x1, z1)
        if isinstance(wall, _FaceRule):
            self.wall = wall
        else:
            self.wall = Facade(_whole_block(wall, "wall", "Core", "a core"))
        if stair is not None and not isinstance(stair, SwitchbackStair):
            raise NBTError(f"a core's stair is a SwitchbackStair, got "
                           f"{type(stair).__name__}")
        if ladder is not None and not isinstance(ladder, Ladder):
            raise NBTError(f"a core's ladder is a Ladder, got "
                           f"{type(ladder).__name__}")
        columns = list(stair.columns) if stair is not None else []
        if ladder is not None:
            columns.append(ladder.column)
        for x, z in columns:
            if not (self.x0 < x < self.x1 and self.z0 < z < self.z1):
                raise NBTError(f"column ({x}, {z}) of what climbs the core "
                               f"is outside its wall, {self.rectangle}")
        self.stair = stair
        self.ladder = ladder
        self.lamp = _whole_block(lamp, "lamp", "Core", "a core", none=True)
        self.top = None if top is None else _whole(top, "top")
        self.height = _whole(height, "height")
        cut: dict = {}
        for level, door in dict(doors or {}).items():
            try:
                face, at, width = door
            except (TypeError, ValueError):
                raise NBTError(f"a core's door is (face, at, width), got "
                               f"{door!r}") from None
            if face not in ("north", "east", "south", "west"):
                raise NBTError(f"a core's door goes in the north, east, south "
                               f"or west face, got {face!r}")
            cut[_whole(level, "a door's floor")] = (face, _whole(at, "at"),
                                                    _whole(width, "width"))
        self.doors = cut if doors is not None else None

    @property
    def rectangle(self) -> tuple:
        return (self.x0, self.z0, self.x1, self.z1)

    @property
    def columns(self) -> frozenset:
        """Every column of the shaft and its wall: what no floor crosses."""
        return frozenset((x, z) for x in range(self.x0, self.x1 + 1)
                         for z in range(self.z0, self.z1 + 1))

    @staticmethod
    def _end(stair, along: int) -> str:
        """The face of the core at the end of the stair ``along`` is at."""
        low, high = ("north", "south") if stair.axis == "z" else ("west", "east")
        return low if along < (stair._along_lo + stair._along_hi) / 2 else high

    def recolored(self, mapping) -> "Core":
        """The same core in other blocks, ``{block: block}`` by id. Refused
        (an ``NBTError``): a key nothing in it holds."""
        table = _Recolor(mapping)
        core = copy.copy(self)
        core.wall = self.wall._recolored(table)
        core.lamp = table(self.lamp)
        if self.stair is not None:
            core.stair = copy.copy(self.stair)
            core.stair.stair = table(self.stair.stair)
            core.stair.wood = table(self.stair.wood)
        table.check()
        return core

    def lamp_cells(self, levels) -> list:
        """Where its lamps go, over ``levels`` and the stair's turns."""
        rows = set(levels)
        if self.stair is not None and self.stair.span is not None:
            rows |= set(self.stair.landing_levels(*self.stair.span))
        beside_x = self.stair is None or self.stair.axis == "z"
        cells = []
        for level in sorted(rows):
            if beside_x:
                z = (self.z0 + self.z1) // 2
                cells += [(self.x0, level + 2, z), (self.x1, level + 2, z)]
            else:
                x = (self.x0 + self.x1) // 2
                cells += [(x, level + 2, self.z0), (x, level + 2, self.z1)]
        return cells

    def door_of(self, level: int, ground: int):
        """The door over a floor, ``(face, at, width)`` or None: as given, or
        where the stair says. Refused (an ``NBTError``): a floor the stair
        does not land on."""
        if self.doors is not None or self.stair is None:
            return (self.doors or {}).get(level)
        stair = self.stair
        if level == ground:
            face = self._end(stair, stair._start_along)
        elif stair.is_landing(level):
            x, z = stair.landing_cells(level)[0]
            face = self._end(stair, z if stair.axis == "z" else x)
        else:
            raise NBTError(
                f"the floor at y={level} is not where the stair lands: its "
                f"turns are every {stair.pitch} levels, at "
                f"{stair.landing_levels(ground, level + stair.pitch)}. The "
                f"staircase decides where a floor can go")
        return (face, stair._cross_lo, stair._cross_hi - stair._cross_lo + 1)

    def __repr__(self) -> str:
        climbs = " and ".join(
            word for word, rule in (("a stair", self.stair),
                                    ("a ladder", self.ladder)) if rule)
        return (f"Core({self.x0}, {self.z0}, {self.x1}, {self.z1}"
                f"{', with ' + climbs if climbs else ''})")


class TowerResult:
    """What :meth:`MassingMixin.tower` laid.

    ``walls`` is each tier's walls, by tier; ``floors`` is ``{level: cells}``,
    every cell of every floor inside the walls of the tier it serves, the
    ground floor among them, the core left out, and ``terraces`` the same
    for the open part of each floor a tier stepped back from: what a caller
    lights or furnishes. ``walk`` is the
    :class:`~heaton_voxel.craft.survey.ReachMap` from the doorstep, or None
    without one. ``blocks`` counts every cell planned, ``already`` those left
    as they stood.
    """

    __slots__ = ("walls", "floors", "terraces", "walk", "blocks", "already",
                 "massing")

    def __init__(self, massing):
        self.massing = massing
        self.walls: list = []
        self.floors: dict = {}
        self.terraces: dict = {}
        self.walk = None
        self.blocks = 0
        self.already = 0

    def __repr__(self) -> str:
        return (f"<TowerResult {len(self.massing.tiers)} tiers, "
                f"{len(self.floors)} floors, {self.blocks:,} cells, "
                f"{self.already:,} already there>")


class MassingMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods: towers.

    Builder inherits them; nothing here is used on its own.
    """

    def tower(self, massing, *, core=None, crown=None, doorstep=None,
              overwrite: bool = False) -> TowerResult:
        """A whole tower, as one call: a :class:`Massing`'s tiers bottom up,
        each by :meth:`shell`, its floors stopping at ``core``; each tier's
        ``terrace``; the :class:`Core`, its doors cut, its stair and ladder
        in it, or a sequence of them for tiers side by side; and ``crown``,
        a :class:`~heaton_voxel.craft.build.crown.Crown` on every tier with
        none over it, its first row over that tier's top.

        Every cell is planned and checked before the first is written, as
        :meth:`shell` checks its own: already right and left, anything else
        taken and refused by name unless ``overwrite=True``, never into a
        flight's or an approach's claim, the whole plan audited for what the
        game will not keep. So a tower that cannot stand writes nothing.

        ``doorstep`` is the block you stand on outside, ``(x, y, z)``. Given
        one, the plan is walked before it is written, without a jump, from
        there to every cell of every floor and back, and a floor the walk
        does not reach whole is refused, by level. A floor you furnish can
        cut a room off; one with nothing on it cannot, so what this finds is
        a stair, a door or a ladder that does not connect. It reads the
        walk's map off the plan, so nothing else laid around the tower is
        counted. Check again after furnishing.

        Refused before anything is read (an ``NBTError``): a tier without a
        rule, such as one read off a building that would not read; a tier
        that does not stand on a floor of a tier under it, or reaches past
        its columns; two tiers side by side that overlap; a core that does
        not fit inside every tier it rises through; a floor the core's stair
        does not land on; a crown on a tier with no rectangle to stand on.
        Then what
        :meth:`shell` refuses, tier by tier, and a stair with short head
        room (:data:`~heaton_voxel.craft.build.HEAD_ROOM`). Returns a
        :class:`TowerResult`.
        """
        self._alive()
        if not isinstance(massing, Massing):
            raise NBTError(f"tower takes a Massing, got "
                           f"{type(massing).__name__}")
        cores = (() if core is None else (core,) if isinstance(core, Core)
                 else tuple(core))
        for each in cores:
            if not isinstance(each, Core):
                raise NBTError(f"core is a Core, got {type(each).__name__}")
        if crown is not None and not isinstance(crown, Crown):
            raise NBTError(f"crown is a Crown, got {type(crown).__name__}")
        tiers = massing.tiers
        for index, tier in enumerate(tiers):
            if tier.rule is None and len(tier.faces) < 4:
                raise NBTError(f"tier {index} has no rule for all four walls, "
                               f"so nothing says what they are")
        over = self._stacked(tiers)
        tops = [index for index in range(len(tiers)) if not over[index]]
        if crown is not None:
            for index in tops:
                if tiers[index].footprint is not None:
                    raise NBTError(f"a crown stands on a rectangle, and "
                                   f"{tiers[index]!r} is on a footprint: give "
                                   f"it a tier that is one, or a roof")
        voids = frozenset(column for each in cores for column in each.columns)
        result = TowerResult(massing)

        plans = []
        for tier in tiers:
            plans.append(self._plan_shell(
                *tier.rectangle, tier.rule_of("north"),
                floor_y=tier.floor_y, top=tier.top, stories=tier.stories,
                faces=tier.faces, thickness=tier.thickness, floor=tier.floor,
                ground=tier.ground, deck=tier.deck, openings=tier.openings,
                voids=voids, footprint=tier.footprint, parapet=tier.parapet,
                parapet_height=tier.parapet_height, roof=tier.roof,
                attic=tier.attic))
            result.walls.append(plans[-1].walls)

        # A terrace is a floor's outer part, in a material of its own.
        pick = _Picks()
        for plan, below, on in zip(plans, tiers, over):
            for level in sorted({tiers[index].floor_y for index in on}):
                standing = [tiers[index] for index in on
                            if tiers[index].floor_y == level]
                outer = [cell for cell in below.floor_cells(level, voids=voids)
                         if not any(tier.encloses(cell[0], cell[2])
                                    for tier in standing)]
                result.terraces[level] = outer
                if below.terrace is not None:
                    terrace = {cell: pick(below.terrace, cell) for cell in outer}
                    plan.planned = [(cell, terrace.get(cell, state))
                                    for cell, state in plan.planned]

        ground = tiers[0].floor_y
        levels = (ground, *massing.stories)
        rising: dict = {}
        for each in cores:
            self._plan_core(each, tiers, over, plans, rising)
        if crown is not None:
            for index in tops:
                last = tiers[index]
                rising.update(crown.plan(*last.rectangle, last.top + 1))

        fills = [op for plan in plans for op in plan.fills]
        cuts = [cut for plan in plans for cut in plan.cuts]
        planned = [item for plan in plans for item in plan.planned]
        planned += sorted(rising.items())
        fabric: dict = {}
        opened: dict = {}
        unglazed: set = set()
        for plan in plans:
            fabric.update(plan.fabric)
            opened.update(plan.opened)
            unglazed |= plan.unglazed
        self._vet_plan(fills + [("set", cell, state)
                                for cell, state in planned], what="tower")
        faults = self._cut_claims(cuts)
        already, taken, writes = self._settle(
            planned, fabric=fabric, opened=opened, unglazed=unglazed,
            overwrite=overwrite, noun="tower",
            cleared=[(c1, c2) for _, c1, c2, _ in cuts])
        self._refuse_taken(
            faults + taken, what="tower",
            advice="Clear the site first, or pass overwrite=True to build "
                   "through whatever is there")
        ops = fills + [("set", cell, state) for cell, state in writes]

        laid: dict = {}
        for _, c1, c2, block in cuts:
            for x in range(c1[0], c2[0] + 1):
                for y in range(c1[1], c2[1] + 1):
                    for z in range(c1[2], c2[2] + 1):
                        laid[(x, y, z)] = pick(block, (x, y, z))
        laid.update(planned)

        for each in cores:
            if each.stair is not None:
                self._stair_room(each, laid)
        for level in levels:
            cells: list = []
            for tier, on in zip(tiers, over):
                if (tier.floor_y <= level <= tier.top + 1
                        and not any(tiers[index].floor_y <= level
                                    for index in on)):
                    cells += tier.floor_cells(level, voids=voids)
            result.floors[level] = cells
        if doorstep is not None:
            result.walk = self._walk_tower(massing, result.floors, laid,
                                           doorstep, crown)
        self._audit_plan(ops, what="tower")
        self._commit(ops, what="tower")
        result.blocks = len(planned)
        result.already = len(already)
        return result

    @staticmethod
    def _stacked(tiers) -> list:
        """Which tiers stand on each, by index: each on the nearest tier
        under it with a floor it can stand on, the one listed before it if
        that will do, so a plain stack stays one."""
        over: list = [[] for _ in tiers]
        for index in range(1, len(tiers)):
            tier = tiers[index]
            below = tiers[index - 1]
            found = next((earlier for earlier in range(index - 1, -1, -1)
                          if tiers[earlier].holds(tier)
                          and tier.floor_y in (*tiers[earlier].stories,
                                               tiers[earlier].top + 1)), None)
            if found is None:
                if tier.floor_y not in (*below.stories, below.top + 1):
                    raise NBTError(
                        f"{tier!r} stands on row {tier.floor_y}, and {below!r} "
                        f"has no floor there: a tier stands on one of the "
                        f"stories under it, or on the row over its walls")
                raise NBTError(f"{tier!r} reaches past {below!r}, which it "
                               f"stands on")
            for other in over[found]:
                if tiers[other].columns & tier.columns:
                    raise NBTError(f"{tier!r} and {tiers[other]!r} stand on "
                                   f"one floor and overlap")
            over[found].append(index)
        return over

    def _rises(self, core, tiers, over) -> list:
        """The tiers ``core`` rises through, bottom up: at each, the one over
        it that the core fits. Refused (an ``NBTError``): one it does not."""
        here = 0
        chain = [tiers[0]]
        while True:
            if not chain[-1].fits(core):
                raise NBTError(f"{core!r} does not fit inside {chain[-1]!r}, "
                               f"which it rises through")
            rising = [index for index in over[here]
                      if core.top is None or tiers[index].floor_y < core.top]
            if not rising:
                return chain
            here = next((index for index in rising if tiers[index].fits(core)),
                        rising[0])
            chain.append(tiers[here])

    def _plan_core(self, core, tiers, over, plans, rising) -> None:
        """The core's wall and what climbs it, added to ``plans`` and
        ``rising``: every level it serves gets its door."""
        chain = self._rises(core, tiers, over)
        ground = tiers[0].floor_y
        top = (max(tier.top for tier in chain) if core.top is None
               else core.top)
        levels = sorted({level for tier in chain for level in tier.stories})
        stories = tuple(level for level in levels if level < top - 1)
        served = (ground, *stories)
        openings = []
        for index, level in enumerate(served):
            door = core.door_of(level, ground)
            if door is not None:
                face, at, width = door
                openings.append((face, Opening.rect(width, core.height), at,
                                 index))
        plans.append(self._plan_shell(*core.rectangle, core.wall,
                                      floor_y=ground, top=top, stories=stories,
                                      openings=openings))
        if core.stair is not None:
            first = core.stair.start_y
            last = stories[-1] if stories else first
            for y in range(first, last + 1):
                for (x, z), state in core.stair.level(y).items():
                    rising[(x, y, z)] = state
            core.stair.span = (first, last)
        if core.ladder is not None:
            rising.update(core.ladder.cells())
        if core.lamp is not None:
            pick = _Picks()
            for cell in core.lamp_cells(served):
                if cell not in plans[-1].opened and cell[1] <= top:
                    rising[cell] = pick(core.lamp, cell)

    def _stair_room(self, core, laid) -> None:
        """A flight in a core with short head room, by the walk's own test
        of what a body passes, on the tower as planned."""
        stair = core.stair
        low, high = stair.span

        def peek(cell):
            found = laid.get(cell)
            return found if found is not None else self._peek(cell)

        own = {(x, y, z) for y in range(low, high + 1)
               for x, z in stair.level(y)}
        short = _head_room(peek, stair, range(low, high + 1), own,
                           passable=_passable)
        if short:
            raise Refused(
                f"{len(short)} places in the core with less head room than a "
                f"stair needs, first: {short[0]}. Nothing was written",
                faults=short)

    def _walk_tower(self, massing, floors, laid, doorstep, crown):
        """The plan walked from ``doorstep``, refused unless it reaches
        every cell of every floor a body fits on, and gets home from each."""
        tiers = massing.tiers
        at = [int(v) for v in doorstep]
        box = ((min(at[0], *(tier.x0 for tier in tiers)) - 1,
                min(at[1], tiers[0].floor_y) - 1,
                min(at[2], *(tier.z0 for tier in tiers)) - 1),
               (max(at[0], *(tier.x1 for tier in tiers)) + 1,
                massing.top + (crown.height if crown is not None else 0) + 2,
                max(at[2], *(tier.z1 for tier in tiers)) + 1))
        space = StepSpace(self.save, overlay=laid, dimension=self.dimension)
        walk = reachable(space, doorstep, *box, allow_jump=False)
        faults = []
        for level, cells in floors.items():
            room = [cell for cell in cells
                    if space.stand_top(*cell) is not None]
            gone = [cell for cell in room if cell not in walk.reached]
            if gone:
                faults.append(f"the floor at y={level}: {len(gone)} of "
                              f"{len(room)} cells cannot be walked to, first "
                              f"{gone[0]}")
        if walk.stranded:
            faults.append(f"{len(walk.stranded)} cells can be reached and not "
                          f"left, first {min(walk.stranded)}")
        if faults:
            raise Refused(f"a tower nobody can walk: {faults[0]}. Nothing "
                          f"was written", faults=faults)
        return walk


__all__ = ["Core", "Massing", "MassingMixin", "OUTLINE_SHARE", "Tier",
           "TowerResult"]
