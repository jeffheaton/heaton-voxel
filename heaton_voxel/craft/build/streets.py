"""Streets: a network laid whole, its crossings first.

:meth:`~heaton_voxel.craft.build.Builder.road` lays one road and knows of no
other, and that is not a street grid. Laid over CastleWorld's terraces
(2026-11-01), a five by five grid disagreed with itself at 6 of its 25 crossings
by a block or two, the road laid second cut a step of up to five blocks into the
one laid first, and each road climbed in whole blocks, every one a jump, so a
walker who would not jump reached 71% of it. A :class:`StreetNetwork` is the
whole grid, drawn once, and :meth:`StreetsMixin.streets` lays it as one plan.

**Crossings first.** The heights of the nodes are settled before any street:
each a whole block, as near the ground under it as the streets joining them can
climb. How far a street can climb is what its rows allow, so that is a
constraint on every pair of nodes the network joins, and the heights are the
midpoint of the highest and lowest functions the ground allows under it, the
least any node has to move from its ground: pins at the ends and the rest
between. Every street then climbs from one flat crossing to another, so no
crossing has two heights.

**A street climbs in half blocks.** A row is level across its width, and each
row is at most half a block from the last, one row in ``run`` at most, a slab on
the row it rises to, so a walker never has to jump. Between its ends the profile
is the cheapest there is, a half block of earth under a row worth two changes of
level, so it follows the ground and does not ripple over every bump in it. It
only ever rises as fast as ``run`` lets it, which is why the nodes come first.

**A section says what is across a street.** :class:`StreetSection` is a roadway
and a pavement either side of it, half a block above the road unless told
otherwise. At a crossing the roadway runs through and the pavements turn the
corners, so a pavement is unbroken but for where a street crosses it.

**A network is drawn as lines.** Every :class:`Street` is a polyline along x and
z, and the network finds its own nodes: the ends, the bends, and wherever two
streets meet or cross. A bend in a street is how it goes round a lake, so a
diagonal is refused: a row level across a street has no meaning on one.

**One plan, and everything any writer is held to.** Nothing is written until the
whole of it has passed: every chunk, every keep-out, what somebody built, water,
what the game would not keep, and a walk along it, both ways, with no jump, over
the street's own columns and never the ground beside them. Laid, every street
and crossing is a keep-out in its own name, so a later road, a lamp post or a
planting keeps out of it: :meth:`StreetsMixin.street_lamps` stands posts on a
street's own pavements and never in the one crossing it.

Part of :mod:`heaton_voxel.craft.build`. No world in a section or a network.
"""

from __future__ import annotations

import heapq
import statistics

from ...blocks import BlockState
from ...nbt import NBTError
from ..errors import Refused
from ..style import FROM_PALETTE, Material
from ..survey import reachable
from ._common import _Slot, _whole, AIR, HEAD_ROOM
from .extrude import _OnTheBand
from .site import DIRT, RoadRule

#: The rows a street takes for each half block it climbs, unless told.
RUN = 2
#: Rows of air cleared over a street.
CLEARANCE = 4


def _top(y: int) -> int:
    """The height of a whole block's top at cell ``y``, in half blocks."""
    return 2 * y + 2


def _laid(top: int) -> tuple:
    """``(cell y, form)`` of a surface whose top is ``top`` half blocks up: a
    whole block for an even one, a bottom slab for an odd."""
    return (top - 1) // 2, ("block" if top % 2 == 0 else "slab")


def _climb(steps: int, wait: int, run: int) -> int:
    """How many half blocks ``steps`` rows can climb, ``wait`` of them before
    the first and ``run`` between each."""
    return 0 if steps <= wait else (steps - wait - 1) // run + 1


def _profile(targets, start: int, end: int, run: int) -> list:
    """The tops of ``targets``' rows between ``start`` and ``end``, in half
    blocks: each row within half a block of the last, ``run`` rows at least
    between changes, the cheapest such, two for each half block a row stands
    off its target and one for each change. ``end`` is the row after the last.

    Solved row by row, each state a height and the rows left to wait, and the
    cheapest way to each kept: exact, and there are few states, since a height
    the rows left cannot bring back to ``end`` is no state. Refused (an
    ``NBTError``): ends too far apart for the rows between them, which a
    network's heights never are."""
    last = len(targets)
    states = {(start, 0): (0, None)}
    trail = []
    for index in range(last + 1):
        goal = targets[index] if index < last else end
        left = last - index
        following: dict = {}
        for (top, wait), (cost, _) in sorted(states.items()):
            for change in (0, -1, 1):
                if change and wait:
                    continue
                height = top + change
                after = run - 1 if change else max(wait - 1, 0)
                if abs(end - height) > _climb(left, after, run):
                    continue
                total = cost + 2 * abs(height - goal) + abs(change)
                key = (height, after)
                if key not in following or total < following[key][0]:
                    following[key] = (total, (top, wait))
        if not following:
            raise NBTError(f"a street of {last} rows cannot climb from "
                           f"{start} to {end} half blocks, one row in {run}")
        trail.append(following)
        states = following
    key = min(states, key=lambda k: (states[k][0], k))
    tops = []
    for following in reversed(trail):
        tops.append(key[0])
        key = following[key][1]
    tops.reverse()
    return tops[:-1]


class StreetSection:
    """What is across a street. No world in it.

    ``road`` is the roadway's columns, an odd number, so a street has a center
    line, and ``walk`` the pavement's either side, none for a lane. ``surface``
    and ``pavement`` are each a block or a
    :class:`~heaton_voxel.craft.style.Material`, whose block and slab a street
    is laid in, so each has to come as a slab. ``raised`` stands the pavement
    half a block over the road, a curb.

    Refused (an ``NBTError``): an even road, a road or a walk under nothing,
    and an :class:`~heaton_voxel.craft.errors.IllegalBlock` for a block with no
    slab, naming the forms it has.
    """

    __slots__ = ("road", "walk", "surface", "pavement", "raised", "_forms")

    def __init__(self, road: int = 7, walk: int = 2, *,
                 surface="minecraft:polished_andesite",
                 pavement="minecraft:smooth_stone", raised: bool = True):
        self.road = _whole(road, "road")
        self.walk = _whole(walk, "walk")
        if self.road < 1 or self.road % 2 == 0:
            raise NBTError(f"a roadway is an odd number of columns, so a "
                           f"street has a center line, got {road}")
        if self.walk < 0:
            raise NBTError(f"a pavement is no columns or more, got {walk}")
        self.surface = (surface if isinstance(surface, Material)
                        else Material(surface))
        self.pavement = (pavement if isinstance(pavement, Material)
                         else Material(pavement))
        self.raised = bool(raised)
        self._forms = {
            role: {"block": BlockState(material.form("block")),
                   "slab": BlockState(material.form("slab"),
                                      {"type": "bottom"})}
            for role, material in (("road", self.surface),
                                   ("walk", self.pavement))}

    @property
    def half(self) -> int:
        """The roadway's columns either side of its center."""
        return self.road // 2

    @property
    def reach(self) -> int:
        """The columns either side of its center, pavement and all."""
        return self.half + self.walk

    @property
    def curb(self) -> int:
        """How far the pavement stands over the road, in half blocks."""
        return 1 if self.raised else 0

    def state(self, role: str, top: int) -> BlockState:
        """What a column of ``role`` holds at a surface ``top`` half blocks
        up."""
        return self._forms[role][_laid(top)[1]]

    def __repr__(self) -> str:
        return f"StreetSection(road={self.road}, walk={self.walk})"


class Street:
    """One line of a network: ``points``, ``(x, z)``, each straight on from the
    last along x or along z, and the section it has where it is not the
    network's own. No world in it."""

    __slots__ = ("points", "section")

    def __init__(self, *points, section=None):
        if len(points) < 2:
            raise NBTError(f"a street joins two points at least, got "
                           f"{points!r}")
        kept = []
        for point in points:
            try:
                x, z = point
            except (TypeError, ValueError):
                raise NBTError(f"a street's point is (x, z), got {point!r}") \
                    from None
            kept.append((_whole(x, "x"), _whole(z, "z")))
        for a, b in zip(kept, kept[1:]):
            if a == b:
                raise NBTError(f"a street's points differ, and {a} follows "
                               f"itself")
            if a[0] != b[0] and a[1] != b[1]:
                raise NBTError(
                    f"a street runs along x or along z, and {a} to {b} is "
                    f"diagonal: a row is level across a street, which needs a "
                    f"street square to it. Go round with a bend")
        if section is not None and not isinstance(section, StreetSection):
            raise NBTError(f"a street's section is a StreetSection, got "
                           f"{type(section).__name__}")
        self.points = tuple(kept)
        self.section = section

    def __repr__(self) -> str:
        return f"Street({', '.join(map(str, self.points))})"


class _Node:
    """Where streets meet, and the square of road and pavement there."""

    __slots__ = ("x", "z", "arms", "half_x", "half_z", "reach_x", "reach_z",
                 "curb", "name", "section")

    def __init__(self, x, z):
        self.x, self.z = x, z
        self.arms: list = []
        self.name = ""

    @property
    def key(self) -> tuple:
        return (self.x, self.z)

    def settle(self) -> None:
        """Its square, from the widest street each way through it."""
        along_x = [section for way, section in self.arms if way[0]]
        along_z = [section for way, section in self.arms if way[1]]
        widest = max((section for _, section in self.arms),
                     key=lambda section: (section.reach, section.half))
        across_z = along_x or along_z
        across_x = along_z or along_x
        self.half_z = max(section.half for section in across_z)
        self.reach_z = max(section.reach for section in across_z)
        self.half_x = max(section.half for section in across_x)
        self.reach_x = max(section.reach for section in across_x)
        self.curb = widest.curb
        self.section = widest

    @property
    def rectangle(self) -> tuple:
        return (self.x - self.reach_x, self.z - self.reach_z,
                self.x + self.reach_x, self.z + self.reach_z)

    def cells(self) -> dict:
        """``{(x, z): role}`` of its square: the road through the middle and
        out along every street that leaves it, the pavement round that."""
        out = {}
        for dx in range(-self.reach_x, self.reach_x + 1):
            for dz in range(-self.reach_z, self.reach_z + 1):
                road = abs(dx) <= self.half_x and abs(dz) <= self.half_z
                for (wx, wz), section in self.arms:
                    road = road or (wx and dx * wx > 0
                                    and abs(dz) <= section.half)
                    road = road or (wz and dz * wz > 0
                                    and abs(dx) <= section.half)
                out[(self.x + dx, self.z + dz)] = "road" if road else "walk"
        return out

    def __repr__(self) -> str:
        return f"<crossing at ({self.x}, {self.z})>"


class _Edge:
    """A street between two nodes, and the rows of it between their squares."""

    __slots__ = ("a", "b", "section", "name", "axis", "rows")

    def __init__(self, a, b, section):
        self.a, self.b = a, b
        self.section = section
        self.axis = "x" if a.z == b.z else "z"
        self.name = ""
        self.rows: list = []

    def settle(self) -> None:
        if self.axis == "x":
            low, high = self.a.x + self.a.reach_x, self.b.x - self.b.reach_x
        else:
            low, high = self.a.z + self.a.reach_z, self.b.z - self.b.reach_z
        if high - low - 1 < 0:
            raise NBTError(f"the crossings at {self.a.key} and {self.b.key} "
                           f"overlap: they stand {high - low - 1} rows apart "
                           f"with this street's width. Draw the streets "
                           f"further apart")
        self.rows = list(range(low + 1, high))

    def row(self, along: int) -> list:
        """``[((x, z), role), ...]`` across the street at ``along``."""
        section = self.section
        out = []
        for across in range(-section.reach, section.reach + 1):
            column = ((along, self.a.z + across) if self.axis == "x"
                      else (self.a.x + across, along))
            out.append((column, "road" if abs(across) <= section.half
                        else "walk"))
        return out

    @property
    def points(self) -> tuple:
        return (self.a.key, self.b.key)

    @property
    def rectangle(self) -> tuple:
        """The columns between its two squares, or None for no rows."""
        if not self.rows:
            return None
        reach = self.section.reach
        if self.axis == "x":
            return (self.rows[0], self.a.z - reach, self.rows[-1],
                    self.a.z + reach)
        return (self.a.x - reach, self.rows[0], self.a.x + reach,
                self.rows[-1])


class StreetNetwork:
    """Every street of a place, drawn once. No world in it.

    ``section`` is the :class:`StreetSection` a street has unless it says
    otherwise, and ``streets`` the :class:`Street` objects, or plain sequences
    of ``(x, z)`` points. The nodes are found: the ends and bends of every
    street, and wherever two meet or cross. ``pins`` is ``{(x, z): y}``, a node
    held to a height: where a street meets a road already there. ``name`` starts
    the name of every keep-out it registers.

    :meth:`grid` draws the common case. Refused (an ``NBTError``): no streets,
    streets that run along one another, crossings too close for their squares,
    two streets standing in one column, and a pin at no node.
    """

    __slots__ = ("section", "streets", "pins", "name", "nodes", "edges",
                 "columns")

    def __init__(self, section, *streets, pins=None, name: str = "street"):
        if not isinstance(section, StreetSection):
            raise NBTError(f"a network's section is a StreetSection, got "
                           f"{type(section).__name__}")
        if not streets:
            raise NBTError("a network has at least one street")
        self.section = section
        self.name = str(name)
        self.streets = tuple(street if isinstance(street, Street)
                             else Street(*street) for street in streets)
        self.nodes: dict = {}
        self.edges: list = []
        self._join()
        self.pins = {}
        for (x, z), y in dict(pins or {}).items():
            if (x, z) not in self.nodes:
                raise NBTError(f"a pin holds a node, and ({x}, {z}) is none: "
                               f"streets meet at {sorted(self.nodes)}")
            self.pins[(x, z)] = _whole(y, "a pin's y")
        self.columns = self._columns()

    @classmethod
    def grid(cls, xs, zs, section, *, pins=None,
             name: str = "street") -> "StreetNetwork":
        """Streets along every ``x`` of ``xs`` and every ``z`` of ``zs``, each
        as far as the last of the other, every crossing a node."""
        xs = sorted(_whole(x, "x") for x in xs)
        zs = sorted(_whole(z, "z") for z in zs)
        if not xs or not zs:
            raise NBTError("a grid has a street each way at least")
        streets = [Street((x, zs[0]), (x, zs[-1])) for x in xs if len(zs) > 1]
        streets += [Street((xs[0], z), (xs[-1], z)) for z in zs if len(xs) > 1]
        return cls(section, *streets, pins=pins, name=name)

    def _join(self) -> None:
        """The streets cut at every point another touches them."""
        pieces = []
        for street in self.streets:
            section = street.section or self.section
            pieces += [(a, b, section)
                       for a, b in zip(street.points, street.points[1:])]
        for index, (a, b, _) in enumerate(pieces):
            for c, d, _ in pieces[index + 1:]:
                self._apart(a, b, c, d)
        stops = {point for a, b, _ in pieces for point in (a, b)}
        for a, b, _ in pieces:
            for c, d, _ in pieces:
                meet = self._meet(a, b, c, d)
                if meet is not None:
                    stops.add(meet)
        for point in sorted(stops):
            self.nodes[point] = _Node(*point)
        for a, b, section in pieces:
            low, high = min(a, b), max(a, b)
            on = sorted(point for point in stops
                        if low[0] <= point[0] <= high[0]
                        and low[1] <= point[1] <= high[1])
            for near, far in zip(on, on[1:]):
                edge = _Edge(self.nodes[near], self.nodes[far], section)
                self.edges.append(edge)
                way = ((1, 0) if edge.axis == "x" else (0, 1))
                edge.a.arms.append((way, section))
                edge.b.arms.append(((-way[0], -way[1]), section))
        for node in self.nodes.values():
            node.settle()
            node.name = f"{self.name}-cross-{node.x}-{node.z}"
        for edge in self.edges:
            edge.settle()
            edge.name = (f"{self.name}-{edge.a.x}-{edge.a.z}-to-"
                         f"{edge.b.x}-{edge.b.z}")

    @staticmethod
    def _apart(a, b, c, d) -> None:
        """Refuse two pieces running along one another."""
        for axis in (0, 1):
            other = 1 - axis
            if a[axis] == b[axis] == c[axis] == d[axis]:
                low = max(min(a[other], b[other]), min(c[other], d[other]))
                high = min(max(a[other], b[other]), max(c[other], d[other]))
                if low < high:
                    raise NBTError(f"two streets run along one another, "
                                   f"{a} to {b} and {c} to {d}: draw it once")

    @staticmethod
    def _meet(a, b, c, d):
        """Where a piece along x and a piece along z cross, or None."""
        if a[1] != b[1] or c[0] != d[0]:
            return None
        x, z = c[0], a[1]
        if (min(a[0], b[0]) <= x <= max(a[0], b[0])
                and min(c[1], d[1]) <= z <= max(c[1], d[1])):
            return (x, z)
        return None

    def _columns(self) -> dict:
        """``{(x, z): (owner, role)}``, every column once."""
        out: dict = {}

        def claim(column, owner, role):
            held = out.get(column)
            if held is not None and held[0] is not owner:
                raise NBTError(f"two streets stand in column {column}: "
                               f"{owner.name} and {held[0].name}. Draw them "
                               f"further apart")
            out[column] = (owner, role)

        for node in self.nodes.values():
            for column, role in node.cells().items():
                claim(column, node, role)
        for edge in self.edges:
            for along in edge.rows:
                for column, role in edge.row(along):
                    claim(column, edge, role)
        return out

    @property
    def names(self) -> tuple:
        """Every keep-out it registers when it is laid."""
        return (*(node.name for node in self.nodes.values()),
                *(edge.name for edge in self.edges))

    def climbs(self, run: int) -> dict:
        """``{(a, b): blocks}``, how far each street can climb, both ways."""
        out: dict = {}
        for edge in self.edges:
            blocks = _climb(len(edge.rows) + 1, 0, run) // 2
            out[(edge.a.key, edge.b.key)] = blocks
            out[(edge.b.key, edge.a.key)] = blocks
        return out

    def _reaches(self, run: int) -> dict:
        """``{node: {node: blocks}}``, the least climb between each pair the
        network joins: how far apart their heights may be."""
        links: dict = {key: [] for key in self.nodes}
        for (a, b), blocks in self.climbs(run).items():
            links[a].append((b, blocks))
        out = {}
        for source in sorted(self.nodes):
            best = {source: 0}
            heap = [(0, source)]
            while heap:
                cost, at = heapq.heappop(heap)
                if cost > best[at]:
                    continue
                for to, blocks in links[at]:
                    if cost + blocks < best.get(to, cost + blocks + 1):
                        best[to] = cost + blocks
                        heapq.heappush(heap, (best[to], to))
            out[source] = best
        return out

    def levels(self, ground: dict, run: int) -> dict:
        """``{node: y}``, each as near ``ground``, ``{node: y}``, as the
        streets joining them can climb and the pins let it: the midpoint of
        the highest and lowest such functions, which moves no node further
        than it has to. Whole blocks stay within what a street can climb, as
        the midpoint of two such functions, floored, is one. Refused (a
        :class:`~heaton_voxel.craft.errors.Refused`): pins further apart than
        the streets between them can climb."""
        reach = self._reaches(run)
        for a, y in sorted(self.pins.items()):
            for b, other in sorted(self.pins.items()):
                if b in reach[a] and abs(y - other) > reach[a][b]:
                    raise Refused(
                        f"the pins at {a}, y={y}, and {b}, y={other}, are "
                        f"further apart than the streets between them can "
                        f"climb, {reach[a][b]} blocks at one row in {run} a "
                        f"half block. Nothing was written",
                        faults=[a, b])
        wanted: dict = {}
        for key in sorted(self.nodes):
            near = [(pin, y) for pin, y in self.pins.items()
                    if pin in reach[key]]
            low = max((y - reach[key][pin] for pin, y in near), default=None)
            high = min((y + reach[key][pin] for pin, y in near), default=None)
            y = ground[key]
            if low is not None:
                y = min(max(y, low), high)
            wanted[key] = self.pins.get(key, y)
        out = {}
        for key in sorted(self.nodes):
            under = min(wanted[other] + blocks
                        for other, blocks in reach[key].items())
            over = max(wanted[other] - blocks
                       for other, blocks in reach[key].items())
            out[key] = (under + over) // 2
        return out

    def __repr__(self) -> str:
        return (f"StreetNetwork({len(self.edges)} streets, "
                f"{len(self.nodes)} crossings)")


class LaidStreet:
    """One street of a network as it was laid: ``name``, its keep-out;
    ``points``, its two ends, what
    :meth:`~heaton_voxel.craft.build.Builder.line_lamps` follows; ``rows``,
    the coordinates along it, and ``tops``, the road's height at each in half
    blocks."""

    __slots__ = ("name", "points", "axis", "rows", "tops", "section",
                 "rectangle")

    def __init__(self, edge, tops):
        self.name = edge.name
        self.points = edge.points
        self.axis = edge.axis
        self.rows = tuple(edge.rows)
        self.tops = tuple(tops)
        self.section = edge.section
        self.rectangle = edge.rectangle

    def __repr__(self) -> str:
        return f"<LaidStreet {self.points[0]} to {self.points[1]}>"


class StreetsResult:
    """What :meth:`StreetsMixin.streets` laid.

    ``heights`` is ``{node: y}``, its crossings; ``streets`` a
    :class:`LaidStreet` for each; ``tops`` ``{(x, z): half blocks}``, the
    surface of every column; ``walk`` the
    :class:`~heaton_voxel.craft.survey.ReachMap` of the first thing walked.
    ``cut`` and ``filled`` count columns, ``slabs`` the columns laid as a
    half block, and ``sealed`` the seals.
    """

    __slots__ = ("network", "heights", "streets", "tops", "walk", "cut",
                 "filled", "slabs", "sealed")

    def __init__(self, network, heights):
        self.network = network
        self.heights = heights
        self.streets: list = []
        self.tops: dict = {}
        self.walk = None
        self.cut = self.filled = self.slabs = self.sealed = 0

    def top(self, x: int, z: int) -> float:
        """The height of the surface at a column, half blocks and all."""
        return self.tops[(x, z)] / 2

    def level(self, x: int, z: int) -> int:
        """The cell the surface of a column is laid in."""
        return _laid(self.tops[(x, z)])[0]

    def __repr__(self) -> str:
        return (f"<StreetsResult {len(self.streets)} streets, "
                f"{len(self.heights)} crossings, {len(self.tops):,} columns>")


class StreetsMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods: street networks.

    Builder inherits them; nothing here is used on its own.
    """

    def streets(self, network, *, run: int = RUN, clearance: int = CLEARANCE,
                retain=FROM_PALETTE, subsoil=DIRT, over_water=None,
                allow_dam: bool = False, seal=None, through=(), rule=None,
                overwrite: bool = False) -> StreetsResult:
        """Lay a :class:`StreetNetwork` as one plan. See the module docstring.

        ``run`` is the rows a street takes to climb half a block, one the
        steepest a body walks. ``clearance`` is rows of air over it, at least
        :data:`~heaton_voxel.craft.build.HEAD_ROOM`, or a walker cannot come
        back down. A fill under a column with a side off the street is
        ``retain``, the palette's ``retaining`` unless named, and ``None`` for
        none, and ``subsoil`` everywhere else.

        Refused before a block is written, and in this order (each a
        :class:`~heaton_voxel.craft.errors.Refused` unless it says): a name the
        session has used (an ``NBTError``); a keep-out not named in ``through``;
        a column with no ground (:class:`~heaton_voxel.craft.errors.NoSurface`);
        pins the streets cannot join; water, unless ``over_water`` says, as
        :meth:`road` takes it; a fill deeper than ``rule``'s ``shallow_fill`` or
        one that cuts water in two, unless ``allow_dam=True``; a chunk that is
        not there; what somebody built, unless ``overwrite=True``, or a road
        may cross a path this session laid; water the cuts let out, unless
        ``seal=`` walls it off; what the game will not keep; and any column of
        it not walked to and back without a jump.

        Returns a :class:`StreetsResult`, and every street and crossing is a
        keep-out from then on, and a path the session paved.
        """
        self._alive()
        if not isinstance(network, StreetNetwork):
            raise NBTError(f"streets takes a StreetNetwork, got "
                           f"{type(network).__name__}")
        run = _whole(run, "run")
        if run < 1:
            raise NBTError(f"run is the rows to a half block, one at the "
                           f"least, got {run}")
        clearance = _whole(clearance, "clearance")
        if clearance < HEAD_ROOM:
            raise NBTError(f"a street needs {HEAD_ROOM} rows over it for a "
                           f"walker to come back down, got {clearance}")
        taken = sorted(name for name in network.names
                       if name in self._pads or name in self._reserved)
        if taken:
            raise NBTError(f"{taken[0]} is already a keep-out: give the "
                           f"network a name of its own")
        if rule is None:
            rule = RoadRule()
        elif not isinstance(rule, RoadRule):
            raise NBTError(f"a network's rule= is a RoadRule, got {rule!r}")
        retain = self._materials("streets", _Slot("retain", retain, "retaining",
                                                  mixes=True, none=True))[0]
        columns = network.columns
        self._keep_out(sorted(columns), through=through,
                       what="streets would carve through")

        stood = {column: self._stand(column) for column in sorted(columns)}

        def ground(column) -> int:
            y, probe = stood[column]
            return y if probe is None else probe.waterline + 1

        heights = network.levels(
            {node.key: int(statistics.median(
                ground(column) for column in node.cells()))
             for node in network.nodes.values()}, run)
        result = StreetsResult(network, heights)
        tops: dict = {}
        for node in network.nodes.values():
            road = _top(heights[node.key])
            for column, role in node.cells().items():
                tops[column] = road + (node.curb if role == "walk" else 0)
        for edge in network.edges:
            wanted = [int(statistics.median(
                _top(ground(column)) for column, _ in edge.row(along)))
                for along in edge.rows]
            profile = _profile(wanted, _top(heights[edge.a.key]),
                               _top(heights[edge.b.key]), run)
            result.streets.append(LaidStreet(edge, profile))
            for along, road in zip(edge.rows, profile):
                for column, role in edge.row(along):
                    tops[column] = road + (edge.section.curb
                                           if role == "walk" else 0)
        result.tops = tops

        wet: dict = {}
        filled: set = set()
        cells = {column: _laid(top)[0] for column, top in tops.items()}
        base = self._build_base(cells, over_water, "streets", filled=filled,
                                wet=wet, allow_dam=allow_dam)
        removed: list = []
        deep: list = []
        bridged_under = False
        for column in sorted(tops, key=lambda c: (c[1], c[0])):
            y, x, z = cells[column], *column
            found = wet.get(column)
            if found is not None:
                if over_water == "fill":
                    removed += [(x, k, z) for k in range(base[column] + 1,
                                                         found.waterline + 1)]
                elif y <= found.waterline:
                    bridged_under = True
                    removed += [(x, k, z) for k in range(y,
                                                         found.waterline + 1)]
            elif rule.deep(y, base[column]):
                deep.append(f"{y - 1 - base[column]} blocks of fill at "
                            f"{column}")
        if not allow_dam:
            split = self._split_faults(removed)
            faults = (sorted(deep, key=lambda word: -int(word.split()[0]))
                      + split)
            if faults:
                raise Refused(
                    f"streets would dam the ground they cross -- "
                    f"{len(faults)} columns, the deepest: {faults[0]}, more "
                    f"than shallow_fill={rule.shallow_fill}. Bend the street "
                    f"round it, or pass allow_dam=True to build the "
                    f"embankment. Nothing was written", faults=faults)

        ops = self._street_ops(network, tops, cells, base, wet, over_water,
                               retain, subsoil, clearance, result)
        self._reach(ops, what="streets")
        self._refuse_built(ops, what="streets", overwrite=overwrite,
                           paved=self._paved)
        seals = self._contain_plan(ops, what="streets", seal=seal)
        if seals:
            self._keep_out([(x, z) for _, (x, _, z), _ in seals],
                           through=through,
                           what="streets' seals would stand in")
        if (seals or bridged_under) and not allow_dam:
            self._starve(sorted(set(removed) | {cell for _, cell, _ in seals}),
                         call="streets", allow_dam=allow_dam)
        self._audit_plan(ops + seals, what="streets")
        result.walk = self._walk_streets(ops + seals, network, cells)
        self._commit(ops + seals, what="streets")
        result.sealed = len(seals)
        self._paved.update(tops)
        for node in network.nodes.values():
            self._reserve_laid(node.name, node.rectangle)
        for edge in network.edges:
            if edge.rectangle is not None:
                self._reserve_laid(edge.name, edge.rectangle)
        return result

    def _reserve_laid(self, name: str, rectangle: tuple) -> None:
        self._reserved[name] = rectangle
        self._register(name)

    def _street_ops(self, network, tops, cells, base, wet, over_water, retain,
                    subsoil, clearance, result) -> list:
        """The plan, column by column in ``(z, x)`` order: cut or fill to the
        surface, the surface, air over it."""
        columns = network.columns
        ops: list = []
        for column in sorted(tops, key=lambda c: (c[1], c[0])):
            x, z = column
            owner, role = columns[column]
            section = owner.section
            top = tops[column]
            y = cells[column]
            g = base[column]
            state = section.state(role, top)
            edge = any(side not in columns for side in
                       ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)))
            bridged = column in wet and over_water == "bridge"
            if g > y:
                ops.append(("fill", (x, y + 1, z), (x, g + 1, z), AIR))
                result.cut += 1
            elif g < y - 1 and not (bridged and g + 1 == y):
                filler = retain if edge and retain is not None else subsoil
                ops.append(("fill", (x, g + 1, z), (x, y - 1, z), filler))
                result.filled += 1
            ops.append(("set", (x, y, z), state))
            ops.append(("fill", (x, y + 1, z), (x, y + clearance, z), AIR))
            ops.append(("ground", (x, z), y))
            result.slabs += top % 2
        return ops

    def _walk_streets(self, ops, network, cells):
        """Refuse a plan that does not walk both ways without a jump: from
        each piece of the network, every one of its columns reached and walked
        home from, over the street's own columns and nothing beside them, or a
        walker turned back by a step would go round it on the ground."""
        overlay = self._plan_cells(ops, what="streets")
        space = _OnTheBand(self.save, cells, overlay=overlay,
                           dimension=self.dimension)
        xs = [x for x, _ in cells]
        zs = [z for _, z in cells]
        ys = list(cells.values())
        lo = (min(xs), min(ys) - 2, min(zs))
        hi = (max(xs), max(ys) + HEAD_ROOM + 2, max(zs))
        left = {(x, y, z) for (x, z), y in cells.items()}
        first = None
        lost: list = []
        for node in sorted(network.nodes.values(), key=lambda n: n.key):
            start = (node.x, cells[node.key], node.z)
            if start not in left:
                continue
            reach = reachable(space, start, lo, hi, allow_jump=False)
            first = first or reach
            here = {cell for cell in left if cell in reach.reached
                    and cell not in reach.stranded}
            if not here:
                lost.append(start)
                left.discard(start)
                continue
            left -= here
        lost += sorted(left)
        if lost:
            raise Refused(
                f"a street nobody can walk: {len(lost)} columns of the plan "
                f"are not walked to and back without a jump, first {lost[0]}. "
                f"Nothing was written", faults=lost)
        return first

    def street_lamps(self, result, *, post, lamp, light_level,
                     over_water=None) -> list:
        """Stand lamp posts along every street of ``result``, what
        :meth:`streets` returned, until its roadways are lit to
        ``light_level``: :meth:`line_lamps` over each street's own rows, its
        pavement open to it, then over each crossing, from its corners. A
        crossing of two wide streets is more than a post on either can reach
        across, and lighting one street to the far side of it would hang on
        what was lit before. Every roadway is closed to a post, and every
        other street and crossing, so a post never stands in a street
        crossing it. ``post``, ``lamp`` and ``light_level`` are as it takes
        them. Returns each :class:`~heaton_voxel.craft.build.lighting.LineLampsResult`.
        """
        if not isinstance(result, StreetsResult):
            raise NBTError(f"street_lamps takes what streets returned, got "
                           f"{type(result).__name__}")
        network = result.network
        roads = frozenset(column for column, (_, role)
                          in network.columns.items() if role == "road")
        kept = dict(post=post, lamp=lamp, light_level=light_level,
                    over_water=over_water, avoid=roads)
        lit = []
        for street in result.streets:
            if len(street.rows) < 2:
                continue
            x, z = street.points[0]
            ends = (((street.rows[0], z), (street.rows[-1], z))
                    if street.axis == "x"
                    else ((x, street.rows[0]), (x, street.rows[-1])))
            lit.append(self.line_lamps(ends, width=street.section.road,
                                       through=(street.name,), **kept))
        for node in network.nodes.values():
            # A strip runs past its line by half its width, and the posts
            # beside it with it: this one stops at the crossing's edge.
            span = max(1, node.reach_x - node.half_z)
            lit.append(self.line_lamps(((node.x - span, node.z),
                                        (node.x + span, node.z)),
                                       width=2 * node.half_z + 1,
                                       through=(node.name,), **kept))
        return lit


__all__ = ["CLEARANCE", "LaidStreet", "RUN", "Street", "StreetNetwork",
           "StreetsMixin", "StreetSection", "StreetsResult"]
