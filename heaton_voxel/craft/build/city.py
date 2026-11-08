"""Cities: a place worked out from the land, then laid whole.

Fifty towers on a grid is a pattern, and it falls apart on real ground. Around
CastleWorld's lake a city-sized box is a quarter water with forty blocks of
relief, so 9 of 10 straight streets across it were refused, and a plaza leveled
for each tower on its own stood behind a cliff of up to 32 blocks, 9 plazas in
11 behind one over three. So a city here is planned from what the land allows.

**Read the land first.** A :class:`SiteMap` samples the ground over an area and
keeps no world: heights, water, what somebody built. :func:`plan_city` then
works from it alone, so a plan is a value, cheap to redraw and to throw away, and
:meth:`CityPlan.picture` draws it before a block is written.

**The grid is cut to fit.** A city's streets start as a loose grid of blocks of
uneven sizes, and each stretch of street between two crossings is judged alone:
it is there if its whole width stands on dry ground nobody has built on, and it
can follow that ground with no deep cut or fill, so a ravine is not crossed. A block
is kept if two of its sides are, so one on a lakefront has streets on three sides
and water on the fourth, and the city grows from the best one near where it was
asked for, one at a time, the cheapest that shares a street with what it has. So
streets step round a lake by themselves, and the city's outline is what the land
gives it. Each try starts the grid somewhere else, and the cheapest wins.

**What a block is, the land says.** Dry and gentle, it splits into one to four
lots of different sizes, each with a street to front, its entrance on the side
nearest downtown. With water in it or too steep to build on, it is a park, so a
pond is the middle of one and a hill stays a hill, and one near the middle of
town goes green besides. With something somebody built in it, it is left exactly
as it is, and the streets go round.

**Height falls away from downtown.** Stories are highest where the city is
thickest and fall with distance from it, with dice over that, so a skyline has a
shape and a street is never one height. A slender lot is kept short. ``styles``
may name ``core``, ``mid`` and ``edge`` families, so glass stands downtown and
brick at the edges.

**A city is laid from the streets out.** :meth:`CityMixin.city` lays the streets
first, since they settle every height. Each lot is then leveled to the pavement
at its door, with no step to speak of, and its tower designed and laid at that
height. A site is sampled, so a lot can hold what the samples missed, a
mineshaft's rails in a cut, say: that lot is refused like any other and left as
it is, and the tower goes on one of the plan's spare lots instead. A street
that meets something the samples missed is refused before anything is written,
so the plan is drawn again with that place marked taken. Then
:meth:`~heaton_voxel.craft.build.site.SiteMixin.blend` eases
everything left into what stands round it: the yards between plazas and
pavements, and a band of the land outside the outer streets, so the city meets
the country with no cliff. Parks are sown on what that leaves, and lit.

Part of :mod:`heaton_voxel.craft.build`. No world in a plan.
"""

from __future__ import annotations

import math
import re

from ...nbt import NBTError
from ..errors import Refused
from ..light import NO_SPAWNS
from ..noise import cell_hash
from ..render import PNGWriter
from ..survey import reachable, walk_surface
from ..worksite import list_worksites
from ._common import _takes, HEAD_ROOM
from .design import design_tower
from .extrude import _OnTheBand
from .planting import Tree
from .streets import RUN, Street, StreetNetwork, StreetSection

#: Blocks a tower may share a street with, for scale: the nominal columns from
#: one street to the next.
BLOCK = 76
#: The columns a lot needs each way, and the most it takes before it splits.
SMALLEST_LOT = 25
LARGEST_LOT = 46
#: Columns of the land outside the outer streets eased into them.
BAND = 16
#: What a plaza is paved in, unless told.
PLAZA = "minecraft:smooth_stone_slab[type=double]"
#: Lots a plan holds back for towers whose own lots turn out to be taken.
SPARE_LOTS = 6
_FLOWERS = ("minecraft:dandelion", "minecraft:poppy", "minecraft:oxeye_daisy",
            "minecraft:cornflower")


def _roll(seed: int, salt: int, a: int, b: int, options):
    """One of ``options``, rolled for a place."""
    return options[cell_hash(a, salt, b, seed) % len(options)]


class SiteMap:
    """What stands over an area, sampled every ``step`` columns. Read once and
    no world in it after.

    ``heights`` is ``{(x, z): y}``, what you would stand on; ``wet`` and
    ``built`` the sampled columns that are water or ice, and what somebody
    built, by what the ground is made of and by every worksite the save
    records. ``absent`` is sampled columns with no ground at all.
    """

    __slots__ = ("x0", "z0", "x1", "z1", "step", "heights", "wet", "built",
                 "absent", "dimension", "center")

    def __init__(self, x0, z0, x1, z1, step, heights, wet, built, absent,
                 center=None, dimension=None):
        self.x0, self.z0, self.x1, self.z1 = x0, z0, x1, z1
        self.step = step
        self.heights = heights
        self.wet = wet
        self.built = built
        self.absent = absent
        self.center = (center if center is not None
                       else ((x0 + x1) // 2, (z0 + z1) // 2))
        self.dimension = dimension

    @classmethod
    def read(cls, save, center, radius: int, *, step: int = 4, dimension=None,
             keep_out=()) -> "SiteMap":
        """Sample a square ``radius`` columns either way of ``center``,
        ``(x, z)``. ``keep_out`` is more ``(x0, z0, x1, z1)`` rectangles to
        read as built. Reads only."""
        cx, cz = (int(center[0]), int(center[1]))
        x0, z0, x1, z1 = cx - radius, cz - radius, cx + radius, cz + radius
        heights: dict = {}
        wet: set = set()
        built: set = set()
        absent: set = set()
        for x in range(x0, x1 + 1, step):
            for z in range(z0, z1 + 1, step):
                probe = walk_surface(save, x, z, dimension=dimension)
                if probe is None:
                    absent.add((x, z))
                    continue
                heights[(x, z)] = probe.y
                if probe.wet or probe.water:
                    wet.add((x, z))
                elif not _takes(probe.block):
                    built.add((x, z))
        held = [tuple(box) for box in keep_out]
        for found in list_worksites(save.path):
            if found.box is not None and (
                    dimension is None or found.dimension == dimension):
                held.append(tuple(found.box))
        for a, b, c, d in held:
            built |= {cell for cell in heights
                      if a - step <= cell[0] <= c + step
                      and b - step <= cell[1] <= d + step}
        return cls(x0, z0, x1, z1, step, heights, wet, built, absent,
                   center=(cx, cz), dimension=dimension)

    def samples(self, rectangle) -> list:
        """The sampled columns in ``(x0, z0, x1, z1)``."""
        a, b, c, d = rectangle
        step = self.step
        first_x = self.x0 + max(0, a - self.x0 + step - 1) // step * step
        first_z = self.z0 + max(0, b - self.z0 + step - 1) // step * step
        return [(x, z) for x in range(first_x, min(c, self.x1) + 1, step)
                for z in range(first_z, min(d, self.z1) + 1, step)]

    def open(self, rectangle) -> bool:
        """Whether a rectangle stands wholly on the map, dry and unbuilt."""
        a, b, c, d = rectangle
        if a < self.x0 or b < self.z0 or c > self.x1 or d > self.z1:
            return False
        return not any(cell in self.wet or cell in self.built
                       or cell in self.absent
                       for cell in self.samples(rectangle))

    def height(self, x: int, z: int):
        """The ground at the sample nearest a column, or None."""
        step = self.step
        return self.heights.get((self.x0 + (x - self.x0 + step // 2) // step
                                 * step,
                                 self.z0 + (z - self.z0 + step // 2) // step
                                 * step))

    def relief(self, rectangle) -> int:
        """How much the ground falls across a rectangle, a tenth of it at
        each end left out, so one rock does not condemn a block."""
        heights = sorted(self.heights[cell] for cell in self.samples(rectangle)
                         if cell in self.heights)
        if not heights:
            return 0
        trim = len(heights) // 10
        return heights[len(heights) - 1 - trim] - heights[trim]

    def __repr__(self) -> str:
        return (f"<SiteMap ({self.x0}, {self.z0})..({self.x1}, {self.z1}), "
                f"{len(self.heights):,} sampled, {len(self.wet):,} wet, "
                f"{len(self.built):,} built>")


class CityLot:
    """One building's plot. ``rectangle`` is the plot, ``building`` the
    footprint on it and ``front`` the way its entrance faces. ``stories``,
    ``style`` and ``seed`` go to
    :func:`~heaton_voxel.craft.build.design.design_tower` when it is laid, at
    a height its pavement decides. ``zone`` is ``core``, ``mid`` or ``edge``."""

    __slots__ = ("rectangle", "building", "front", "stories", "style", "seed",
                 "zone")

    def __init__(self, rectangle, building, front, stories, style, seed, zone):
        self.rectangle = rectangle
        self.building = building
        self.front = front
        self.stories = stories
        self.style = style
        self.seed = seed
        self.zone = zone

    def design(self, y: int):
        """Its tower, on ground at ``y``."""
        return design_tower(self.building, y, self.stories, self.style,
                            seed=self.seed, front=self.front)

    @property
    def door(self) -> tuple:
        """``(x, z)`` of the pavement outside its entrance: one past the
        plot's front edge, at the middle of the building."""
        x0, z0, x1, z1 = self.rectangle
        b0, c0, b1, c1 = self.building
        if self.front == "south":
            return ((b0 + b1 + 1) // 2, z1 + 1)
        if self.front == "north":
            return ((b0 + b1 + 1) // 2, z0 - 1)
        if self.front == "east":
            return (x1 + 1, (c0 + c1 + 1) // 2)
        return (x0 - 1, (c0 + c1 + 1) // 2)

    def __repr__(self) -> str:
        return (f"<CityLot {self.rectangle}, {self.stories} stories, "
                f"{self.style.name}>")


class CityBlock:
    """One block between streets: ``rectangle`` inside its pavements, ``kind``
    and its ``lots``. ``kind`` is ``towers``, ``park``, or ``left`` for one
    with something in it the city goes round."""

    __slots__ = ("rectangle", "lots", "kind")

    def __init__(self, rectangle, lots, kind):
        self.rectangle = rectangle
        self.lots = lots
        self.kind = kind

    def __repr__(self) -> str:
        kind = (f"{len(self.lots)} lots" if self.kind == "towers"
                else self.kind)
        return f"<CityBlock {self.rectangle}, {kind}>"


class CityPlan:
    """A city, worked out and not yet laid. No world in it: :func:`plan_city`
    made it from a :class:`SiteMap`. ``network`` is its streets, ``blocks``
    its blocks and ``lots`` every building, tallest zone first, and ``spare``
    the lots a tower goes on when one of those turns out to be taken.
    ``downtown`` is where it is thickest and ``bounds`` the rectangle it
    fills, the band outside its outer streets among it."""

    __slots__ = ("site", "network", "blocks", "lots", "spare", "downtown",
                 "bounds", "seed", "band", "depth", "asked")

    def __init__(self, site, network, blocks, lots, spare, downtown, seed, band,
                 depth):
        self.site = site
        self.network = network
        self.blocks = blocks
        self.lots = lots
        self.spare = spare
        self.downtown = downtown
        self.seed = seed
        self.band = band
        self.depth = depth
        self.asked: tuple = ((), {})
        xs = [x for x, _ in network.columns]
        zs = [z for _, z in network.columns]
        self.bounds = (min(xs) - band, min(zs) - band, max(xs) + band,
                       max(zs) + band)

    @property
    def parks(self) -> list:
        return [block for block in self.blocks if block.kind == "park"]

    def avoiding(self, cells) -> "CityPlan":
        """The plan drawn again with ``cells``, ``(x, y, z)``, marked on its
        site as built: for what the samples missed."""
        site = self.site
        for x, _, z in cells:
            snap_x = site.x0 + (x - site.x0) // site.step * site.step
            snap_z = site.z0 + (z - site.z0) // site.step * site.step
            for dx in (0, site.step):
                for dz in (0, site.step):
                    site.built.add((snap_x + dx, snap_z + dz))
        args, kept = self.asked
        return plan_city(site, *args, **kept)

    def describe(self) -> str:
        """The plan in words."""
        x0, z0, x1, z1 = self.bounds
        counts: dict = {}
        for lot in self.lots:
            counts[lot.style.name] = counts.get(lot.style.name, 0) + 1
        styles = ", ".join(f"{count} {name}" for name, count
                           in sorted(counts.items(), key=lambda kv: -kv[1]))
        stories = sorted(lot.stories for lot in self.lots)
        return (f"a city of {len(self.lots)} towers at ({x0}, {z0})..({x1}, "
                f"{z1}), {x1 - x0 + 1} by {z1 - z0 + 1}, downtown at "
                f"{self.downtown}: {len(self.network.edges)} streets, "
                f"{len(self.network.nodes)} crossings, "
                f"{sum(block.kind == 'towers' for block in self.blocks)} "
                f"blocks and {len(self.parks)} parks\n{stories[0]} to "
                f"{stories[-1]} "
                f"stories, half of them over {stories[len(stories) // 2]}: "
                f"{styles}")

    def picture(self, path: str, *, scale: int = 2) -> None:
        """Draw the plan over the land from above, to a PNG: ground shaded by
        height, water, what somebody built, streets, parks, and each lot with
        its building shaded by how tall it is. Reads no world."""
        site = self.site
        step = site.step
        lowest = min(site.heights.values(), default=0)
        highest = max(max(site.heights.values(), default=1), lowest + 1)
        tallest = max((lot.stories for lot in self.lots), default=1)
        painted: dict = {}
        for block in self.parks:
            a, b, c, d = block.rectangle
            for x in range(a, c + 1):
                for z in range(b, d + 1):
                    snap = (site.x0 + (x - site.x0) // step * step,
                            site.z0 + (z - site.z0) // step * step)
                    if snap not in site.wet:
                        painted[(x, z)] = (86, 142, 74)
        for lot in self.lots:
            _paint(painted, lot.rectangle, (196, 190, 176))
            shade = int(190 - 150 * lot.stories / tallest)
            _paint(painted, lot.building, (shade, shade, min(255, shade + 26)))
        for column, (_, role) in self.network.columns.items():
            painted[column] = ((92, 92, 96) if role == "road"
                               else (160, 160, 160))
        width = (site.x1 - site.x0 + 1) * scale
        height = (site.z1 - site.z0 + 1) * scale
        with PNGWriter(path, width, height) as png:
            for z in range(site.z0, site.z1 + 1):
                row = bytearray()
                snap_z = site.z0 + (z - site.z0) // step * step
                for x in range(site.x0, site.x1 + 1):
                    color = painted.get((x, z))
                    if color is None:
                        snap = (site.x0 + (x - site.x0) // step * step, snap_z)
                        color = _ground_color(site, snap, lowest, highest)
                    row += bytes(color) * scale
                for _ in range(scale):
                    png.write_row(bytes(row))

    def __repr__(self) -> str:
        return (f"<CityPlan {len(self.lots)} towers, "
                f"{len(self.network.edges)} streets>")


def _paint(painted: dict, rectangle, color) -> None:
    a, b, c, d = rectangle
    for x in range(a, c + 1):
        for z in range(b, d + 1):
            painted[(x, z)] = color


def _ground_color(site, snap, lowest, highest) -> tuple:
    if snap in site.wet:
        return (64, 110, 190)
    if snap in site.built:
        return (176, 70, 60)
    y = site.heights.get(snap)
    if y is None:
        return (24, 24, 28)
    level = (y - lowest) / (highest - lowest)
    return (int(150 + 70 * level), int(130 + 70 * level), int(96 + 60 * level))


def _lines(start: int, stop: int, block: int, seed: int, salt: int) -> list:
    """Street lines from ``start`` past ``stop``, a block apart, give or take,
    so no two blocks need be one size."""
    wide = (block - 12, block - 6, block, block + 6, block + 12)
    lines = [start]
    while lines[-1] < stop:
        lines.append(lines[-1] + _roll(seed, salt, len(lines), start, wide))
    return lines


def _split(low: int, high: int, seed: int, salt: int, a: int, b: int) -> list:
    """A block's columns from ``low`` to ``high`` as one run or two, cut near
    the middle: ``[(low, high), ...]``."""
    size = high - low + 1
    if size < 2 * SMALLEST_LOT:
        return [(low, high)]
    if size <= LARGEST_LOT and _roll(seed, salt, a, b, (0, 1, 1)) == 0:
        return [(low, high)]
    room = (size - 2 * SMALLEST_LOT) // 2
    shift = (_roll(seed, salt + 1, a, b, (-2, -1, 0, 1, 2)) * room) // 2
    middle = low + size // 2 + shift
    return [(low, middle - 1), (middle, high)]


def _way(lot, block, downtown, sides):
    """The side of ``lot`` with a street to front, nearest downtown, of the
    ``sides`` its block has one on, or None."""
    x0, z0, x1, z1 = lot
    a, b, c, d = block
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
    dx, dz = downtown[0] - cx, downtown[1] - cz
    ways = []
    if z1 == d and "south" in sides:
        ways.append((dz, "south"))
    if z0 == b and "north" in sides:
        ways.append((-dz, "north"))
    if x1 == c and "east" in sides:
        ways.append((dx, "east"))
    if x0 == a and "west" in sides:
        ways.append((-dx, "west"))
    return max(ways)[1] if ways else None


def _footing(lot, front: str, seed: int) -> tuple:
    """The building on a plot: three columns from the front, for its plaza,
    and three and a yard from the rest, so the ground has somewhere to come
    down."""
    x0, z0, x1, z1 = lot
    yard = 5 + _roll(seed, 41, x0, z0, (0, 1, 2))
    a, b, c, d = x0 + yard, z0 + yard, x1 - yard, z1 - yard
    if front == "south":
        d = z1 - 3
    elif front == "north":
        b = z0 + 3
    elif front == "east":
        c = x1 - 3
    else:
        a = x0 + 3
    return a, b, c, d


def _zones(styles) -> dict:
    """``styles`` as ``{zone: families}``."""
    if isinstance(styles, dict):
        out = {zone: tuple(styles.get(zone, ())) for zone in ("core", "mid",
                                                              "edge")}
        everything = tuple(family for families in out.values()
                           for family in families)
        if not everything:
            raise NBTError("styles names no family")
        return {zone: families or everything
                for zone, families in out.items()}
    families = tuple(styles)
    if not families:
        raise NBTError("styles names no family")
    return dict.fromkeys(("core", "mid", "edge"), families)


class _Grid:
    """One try at a city: street lines, which stretches of them the land
    allows, and which cells they make: ``edges`` is the dry stretches, as
    ``("x", i, j)`` from line crossing ``(i, j)`` along x, and ``("z", i, j)``
    along z, and ``kinds`` each cell's ``towers``, ``park`` or ``left``."""

    def __init__(self, site, xs, zs, reach, relief, near, block, depth):
        self.site = site
        self.xs = xs
        self.zs = zs
        self.near = near
        self.block = block
        self.reach = reach
        self.depth = depth
        self.edges: set = set()
        self.cost: dict = {}
        self.cells: dict = {}
        self.kinds: dict = {}
        for i, x in enumerate(xs):
            for j, z in enumerate(zs):
                if (i + 1 < len(xs) and site.open(
                        (x - reach, z - reach, xs[i + 1] + reach, z + reach))
                        and self.follows(x, z, xs[i + 1] - x, 0)):
                    self.edges.add(("x", i, j))
                if (j + 1 < len(zs) and site.open(
                        (x - reach, z - reach, x + reach, zs[j + 1] + reach))
                        and self.follows(x, z, 0, zs[j + 1] - z)):
                    self.edges.add(("z", i, j))
        for i in range(len(xs) - 1):
            for j in range(len(zs) - 1):
                if sum(side in self.edges
                       for side in self.sides((i, j)).values()) < 2:
                    continue
                x0, z0, x1, z1 = rectangle = (xs[i], zs[j], xs[i + 1],
                                              zs[j + 1])
                inside = (x0 + reach + 1, z0 + reach + 1, x1 - reach - 1,
                          z1 - reach - 1)
                columns = site.samples(inside)
                steep = site.relief(inside)
                if any(cell in site.built or cell in site.absent
                       for cell in columns):
                    kind, price = "left", 30
                elif steep > relief or any(cell in site.wet
                                           for cell in columns):
                    kind, price = "park", 15
                else:
                    kind, price = "towers", steep
                self.cells[(i, j)] = rectangle
                self.kinds[(i, j)] = kind
                self.cost[(i, j)] = price + 3 * self.apart(rectangle) / block

    def follows(self, x: int, z: int, dx: int, dz: int) -> bool:
        """Whether a street from ``(x, z)`` by ``(dx, dz)`` can follow the
        ground: the midpoint of the highest and lowest heights it could climb
        at, as a network settles its crossings, stands no more than ``depth``
        from the ground anywhere across it."""
        site = self.site
        step = site.step
        length = abs(dx + dz)
        rows = list(range(0, length + 1, step))
        center = []
        across = []
        for along in rows:
            cx = x + (along if dx else 0)
            cz = z + (along if dz else 0)
            sides = [site.height(cx + (0 if dx else k), cz + (k if dx else 0))
                     for k in (-self.reach, 0, self.reach)]
            if None in sides:
                return False
            center.append(sides[1])
            across.append(sides)
        climb = step / (2 * RUN)
        under = list(center)
        over = list(center)
        for i in range(1, len(rows)):
            under[i] = min(under[i], under[i - 1] + climb)
            over[i] = max(over[i], over[i - 1] - climb)
        for i in range(len(rows) - 2, -1, -1):
            under[i] = min(under[i], under[i + 1] + climb)
            over[i] = max(over[i], over[i + 1] - climb)
        return all(abs((under[i] + over[i]) / 2 - y) <= self.depth
                   for i, sides in enumerate(across) for y in sides)

    @staticmethod
    def sides(cell) -> dict:
        """``{way: edge}`` round a cell."""
        i, j = cell
        return {"north": ("x", i, j), "south": ("x", i, j + 1),
                "west": ("z", i, j), "east": ("z", i + 1, j)}

    def across(self, cell) -> list:
        """The cells sharing a dry street with ``cell``."""
        i, j = cell
        near = {"north": (i, j - 1), "south": (i, j + 1), "west": (i - 1, j),
                "east": (i + 1, j)}
        return [near[way] for way, edge in self.sides(cell).items()
                if edge in self.edges and near[way] in self.cells]

    def apart(self, rectangle) -> float:
        return math.hypot((rectangle[0] + rectangle[2]) / 2 - self.near[0],
                          (rectangle[1] + rectangle[3]) / 2 - self.near[1])

    def pieces(self) -> list:
        """The cells a street joins, each piece a list, cheapest piece
        first."""
        left = set(self.cells)
        found = []
        for cell in sorted(self.cells, key=lambda c: (self.cost[c], c)):
            if cell not in left:
                continue
            piece = [cell]
            left.discard(cell)
            for here in piece:
                for side in self.across(here):
                    if side in left:
                        left.discard(side)
                        piece.append(side)
            found.append(piece)
        return found

    def grow(self, wanted) -> list:
        """Cells from the cheapest of the cheapest piece that holds what is
        wanted, each next the cheapest sharing a street with what is kept,
        until ``wanted`` says enough."""
        for piece in self.pieces():
            kept = self._grow(piece[0], wanted)
            if kept:
                return kept
        return []

    def _grow(self, start, wanted) -> list:
        kept = [start]
        while not wanted(kept):
            held = set(kept)
            around = {side for cell in kept for side in self.across(cell)
                      if side not in held}
            if not around:
                return []

            def price(cell):
                touching = sum(side in held for side in self.across(cell))
                return (self.cost[cell] - 6 * touching, cell)

            kept.append(min(around, key=price))
        return kept

    def joined(self, kept) -> set:
        """The dry streets round ``kept`` that join the first cell's: a street
        cut off from the rest of the city is no street of it."""
        edges = {edge for cell in kept for edge in self.sides(cell).values()
                 if edge in self.edges}
        nodes: dict = {}
        for edge in edges:
            kind, i, j = edge
            ends = ((i, j), (i + 1, j) if kind == "x" else (i, j + 1))
            for end in ends:
                nodes.setdefault(end, []).append(edge)
        first = next(edge for edge in self.sides(kept[0]).values()
                     if edge in edges)
        seen = {first}
        todo = [first]
        while todo:
            kind, i, j = todo.pop()
            for end in ((i, j), (i + 1, j) if kind == "x" else (i, j + 1)):
                for edge in nodes[end]:
                    if edge not in seen:
                        seen.add(edge)
                        todo.append(edge)
        return seen


def _streets(xs, zs, edges, avenue_x, avenue_z, section, avenue) -> list:
    """The streets along ``edges``, each line's stretches joined into runs, so
    a node stands only where streets meet."""
    along_x: dict = {}
    along_z: dict = {}
    for kind, i, j in edges:
        if kind == "x":
            along_x.setdefault(j, set()).add(i)
        else:
            along_z.setdefault(i, set()).add(j)
    streets = []

    def runs(spans):
        spans = sorted(spans)
        start = last = spans[0]
        for value in spans[1:]:
            if value != last + 1:
                yield start, last + 1
                start = value
            last = value
        yield start, last + 1

    for j in sorted(along_x):
        kind = avenue if zs[j] == avenue_z else section
        for a, b in runs(along_x[j]):
            streets.append(Street((xs[a], zs[j]), (xs[b], zs[j]), section=kind))
    for i in sorted(along_z):
        kind = avenue if xs[i] == avenue_x else section
        for a, b in runs(along_z[i]):
            streets.append(Street((xs[i], zs[a]), (xs[i], zs[b]), section=kind))
    return streets


def _assemble(grid, kept, towers, zones, section, avenue, parks, stories, seed,
              name, band, tally):
    """The city ``kept`` makes, or None if it holds too few towers, the most
    it has held kept in ``tally``."""
    site = grid.site
    xs, zs = grid.xs, grid.zs
    middles = [grid.cells[cell] for cell in kept]
    cx = sum(r[0] + r[2] for r in middles) // (2 * len(middles))
    cz = sum(r[1] + r[3] for r in middles) // (2 * len(middles))
    edges = grid.joined(kept)
    avenue_x = min((xs[i] for kind, i, _ in edges if kind == "z"),
                   key=lambda x: (abs(x - cx), x), default=xs[0])
    avenue_z = min((zs[j] for kind, _, j in edges if kind == "x"),
                   key=lambda z: (abs(z - cz), z), default=zs[0])
    downtown = (avenue_x, avenue_z)

    def reach(line, lines, chosen) -> int:
        return (avenue if lines[line] == chosen else section).reach

    # What the land did not settle: enough of the steepest go green, and one
    # by the middle.
    kinds = {cell: grid.kinds[cell] for cell in kept}
    free = sorted((cell for cell in kept if kinds[cell] == "towers"),
                  key=lambda cell: (-site.relief(grid.cells[cell]), cell))
    short = round(len(kept) * parks) - sum(kind == "park"
                                           for kind in kinds.values())
    for cell in free[:max(0, short)]:
        kinds[cell] = "park"
    free = [cell for cell in free if kinds[cell] == "towers"]
    if parks and len(free) >= 2:
        kinds[min(free, key=lambda cell: (math.hypot(
            (grid.cells[cell][0] + grid.cells[cell][2]) / 2 - cx,
            (grid.cells[cell][1] + grid.cells[cell][3]) / 2 - cz), cell))] = "park"
    blocks = []
    rough: list = []
    for cell in sorted(kept):
        i, j = cell
        x0, z0, x1, z1 = grid.cells[cell]
        inside = (x0 + reach(i, xs, avenue_x) + 1,
                  z0 + reach(j, zs, avenue_z) + 1,
                  x1 - reach(i + 1, xs, avenue_x) - 1,
                  z1 - reach(j + 1, zs, avenue_z) - 1)
        if kinds[cell] != "towers":
            blocks.append(CityBlock(inside, [], kinds[cell]))
            continue
        pieces = []
        for a, b in _split(inside[0], inside[2], seed, 21, i, j):
            for c, d in _split(inside[1], inside[3], seed, 23, i, j):
                pieces.append((a, c, b, d))
        block = CityBlock(inside, [], "towers")
        blocks.append(block)
        sides = {way for way, edge in grid.sides(cell).items()
                 if edge in edges}
        for piece in pieces:
            front = _way(piece, inside, downtown, sides)
            if front is not None:
                rough.append((block, piece, front))
    tally[0] = max(tally[0], len(rough))
    if len(rough) < towers:
        return None

    def apart(piece) -> float:
        return math.hypot((piece[0] + piece[2]) / 2 - cx,
                          (piece[1] + piece[3]) / 2 - cz)

    rough.sort(key=lambda item: (apart(item[1]), item[1]))
    rough = rough[:towers + SPARE_LOTS]
    low, high = stories
    lots = []
    for index, (block, piece, front) in enumerate(rough):
        # By rank from the middle, so the middle stands as tall as asked
        # wherever the land put it.
        away = min(1, index / max(1, towers - 1))
        zone = "core" if away < 0.2 else "mid" if away < 0.65 else "edge"
        building = _footing(piece, front, seed)
        tall = low + (high - low) * (1 - away) ** 2.2
        tall *= _roll(seed, 31, piece[0], piece[1],
                      (0.7, 0.8, 0.9, 1.0, 1.0, 1.1, 1.25))
        slim = min(building[2] - building[0], building[3] - building[1]) + 1
        count = max(low, min(int(tall), slim * 2))
        style = _roll(seed, 33, piece[0], piece[1], zones[zone])
        lot = CityLot(piece, building, front, count, style,
                      seed + index, zone)
        lot.design(64)                     # refused here, not half laid
        lots.append(lot)
        if index < towers:
            block.lots.append(lot)
    network = StreetNetwork(section, *_streets(xs, zs, edges, avenue_x,
                                               avenue_z, section, avenue),
                            name=name)
    return CityPlan(site, network, blocks, lots[:towers], lots[towers:],
                    downtown, seed, band, grid.depth)


def plan_city(site, towers: int, styles, *, seed: int = 0, block: int = BLOCK,
              stories=(6, 40), parks: float = 0.15, relief: int = 20,
              section=None, avenue=None, name: str = "city",
              band: int = BAND, depth: int = 6, tries: int = 12) -> CityPlan:
    """Work a city out from a :class:`SiteMap`. See the module docstring.

    ``towers`` is how many, and ``styles`` the
    :class:`~heaton_voxel.craft.build.design.TowerStyle` families, a sequence
    or ``{"core": ..., "mid": ..., "edge": ...}``. ``block`` is the nominal
    columns between streets, ``stories`` the ``(fewest, most)`` a tower has,
    ``parks`` the share of blocks left green, and ``relief`` how far the ground
    may fall across a block for it to be built on. ``section`` is what a street
    has and ``avenue`` the two through downtown. ``name`` starts every keep-out
    its streets register. ``band`` is the columns of land outside the city
    eased into it, ``depth`` how far a street may stand off the ground, in a
    cut or on a fill, and ``tries`` how many grids it compares.

    One site, seed and set of arguments is one plan. Refused (a
    :class:`~heaton_voxel.craft.errors.Refused`): land that holds too few
    towers, naming how many it does; and an ``NBTError`` for what is no site
    or no count.
    """
    if not isinstance(site, SiteMap):
        raise NBTError(f"plan_city takes a SiteMap, got {type(site).__name__}")
    if isinstance(towers, bool) or not isinstance(towers, int) or towers < 1:
        raise NBTError(f"towers is how many, at least one, got {towers!r}")
    if not 0 <= parks < 1:
        raise NBTError(f"parks is a share of the blocks, got {parks!r}")
    zones = _zones(styles)
    section = section if section is not None else StreetSection(7, 2)
    avenue = avenue if avenue is not None else StreetSection(9, 3)
    reach = max(section.reach, avenue.reach)
    best = None
    tally = [0]
    span = 2 * block
    for attempt in range(tries):
        ox = cell_hash(attempt, 51, 0, seed) % span
        oz = cell_hash(attempt, 53, 0, seed) % span
        xs = _lines(site.x0 + reach + ox % block, site.x1 - reach, block, seed,
                    55 + attempt)
        zs = _lines(site.z0 + reach + oz % block, site.z1 - reach, block, seed,
                    57 + attempt)
        grid = _Grid(site, xs, zs, reach, relief, site.center, block, depth)
        found = {}

        def enough(kept):
            built = _assemble(grid, kept, towers, zones, section, avenue, parks,
                              stories, seed, name, band, tally)
            found["plan"] = built
            return built is not None

        kept = grid.grow(enough)
        if not kept:
            continue
        price = sum(grid.cost[cell] for cell in kept)
        if best is None or price < best[0]:
            best = (price, attempt, found["plan"])
    if best is not None:
        best[2].asked = ((towers, styles), dict(
            seed=seed, block=block, stories=stories, parks=parks, relief=relief,
            section=section, avenue=avenue, name=name, band=band, depth=depth,
            tries=tries))
    if best is None:
        raise Refused(
            f"the land within {(site.x1 - site.x0) // 2} columns of "
            f"{site.center} has room for {tally[0]} towers at the most, not "
            f"{towers}: it is too wet, too built on or too steep. Read a "
            f"wider site, move it, or raise relief= or depth=",
            faults=[tally[0]])
    return best[2]


class CityResult:
    """What :meth:`CityMixin.city` laid: ``streets``, a
    :class:`~heaton_voxel.craft.build.streets.StreetsResult`; ``designs`` and
    ``towers``, each lot's tower and what it laid; ``skipped``, each lot that
    turned out to be taken, ``(lot, why)``; ``blend``, what the land did; and
    ``unreached``, every doorstep the streets do not walk to with no jump,
    which is empty in a city nobody has to climb to the door of."""

    __slots__ = ("plan", "streets", "designs", "towers", "skipped", "blend",
                 "lamps", "unreached", "replanned")

    def __init__(self, plan):
        self.plan = plan
        self.streets = None
        self.designs: list = []
        self.towers: list = []
        self.skipped: list = []
        self.blend = None
        self.lamps = 0
        self.unreached: list = []
        self.replanned = 0

    def __repr__(self) -> str:
        return (f"<CityResult {len(self.towers)} towers, "
                f"{len(self.skipped)} lots skipped, "
                f"{len(self.unreached)} doors unreached>")


class CityMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods: cities.

    Builder inherits them; nothing here is used on its own.
    """

    def city(self, plan, *, post=("minecraft:stone_brick_wall",) * 2,
             lamp="minecraft:lantern", plaza=PLAZA, run: int = 2,
             park_biome="minecraft:plains", over_water=None,
             allow_dam: bool = True, replans: int = 8) -> CityResult:
        """Lay a :class:`CityPlan`, from the streets out. See the module
        docstring. ``post`` and ``lamp`` are what its streets and parks are
        lit with, ``plaza`` what a tower's forecourt is laid in, ``run`` the
        rows a street takes to climb half a block, and ``park_biome`` what a
        park's ground is repainted as, so grass is green in a desert; ``None``
        leaves it. ``over_water`` and ``allow_dam`` go to
        :meth:`~heaton_voxel.craft.build.streets.StreetsMixin.streets`:
        ``allow_dam`` is on because a plan has kept its streets off the
        ravines it can see, so what is left is a hole too narrow for a site
        to sample, which a street fills. ``replans`` is how many times a plan
        is drawn again round what its streets met, before that refusal stands;
        the result's ``plan`` is the one laid.

        Each stage is planned whole and refused whole, but they run in turn,
        so a refusal late on leaves the earlier ones written: lay it in a
        :class:`~heaton_voxel.craft.worksite.Worksite`, and save only what
        came through. Run :meth:`finish` after it. Returns a
        :class:`CityResult`.
        """
        self._alive()
        if not isinstance(plan, CityPlan):
            raise NBTError(f"city takes a CityPlan, got {type(plan).__name__}")
        result = CityResult(plan)
        with self.stage("streets"):
            while True:
                try:
                    result.streets = self.streets(result.plan.network, run=run,
                                                  over_water=over_water,
                                                  allow_dam=allow_dam)
                    break
                except Refused as exc:
                    cells = _named_cells(exc)
                    if not cells or result.replanned >= replans:
                        raise
                result.plan = result.plan.avoiding(cells)
                result.replanned += 1
        plan = result.plan
        x0, z0, x1, z1 = plan.bounds
        with self.stage("clear"):
            self.clear_vegetation((x0, z0), (x1, z1))
        fixed: set = set(plan.network.columns)
        with self.stage("towers"):
            spare = list(plan.spare)
            for lot in plan.lots:
                while not self._lay_lot(lot, plaza, result, fixed) and spare:
                    lot = spare.pop(0)
        with self.stage("blend"):
            result.blend = self.blend(self._yards(plan, fixed))
        with self.stage("parks"):
            for index, block in enumerate(plan.parks):
                self._park(block, plan.seed + index, post, lamp, park_biome)
        with self.stage("lamps"):
            lit = self.street_lamps(result.streets, post=post, lamp=lamp,
                                    light_level=NO_SPAWNS)
            result.lamps = sum(len(each.posts) for each in lit)
        result.unreached = self._doors_unreached(plan, result)
        return result

    def _lay_lot(self, lot, plaza, result, fixed) -> bool:
        """Level a lot and lay its tower, or say why not: every step of it is
        refused whole, so a lot that is taken is left as it stood but for what
        came before the refusal."""
        design = lot.design(self._door_level(result.streets, lot))
        try:
            self.grade(design.plaza, design.massing.tiers[0].floor_y,
                       surface=plaza, retain=None)
            fixed |= _columns(design.plaza)
            result.towers.append(design.lay(self))
        except Refused as exc:
            result.skipped.append((lot, str(exc)))
            return False
        result.designs.append(design)
        self._light_plaza(design, design.massing.tiers[0].columns)
        return True

    @staticmethod
    def _door_level(streets, lot) -> int:
        """The whole block a lot is leveled to: flush with its pavement, or
        half a block under it where the pavement is a slab."""
        top = streets.tops[lot.door]
        return (top - top % 2) // 2 - 1

    def _light_plaza(self, design, foot) -> None:
        cells = sorted(column for column in _columns(design.plaza)
                       if column not in foot)
        y = design.massing.tiers[0].floor_y
        self.light_cells([(x, y, z) for x, z in cells], light=design.scheme.lamp,
                         light_level=NO_SPAWNS, clear_above=False)

    def _yards(self, plan, fixed) -> set:
        """Everything the blend moves: the blocks round what is built on them,
        and a band of the land outside the streets."""
        columns: set = set()
        left: set = set()
        for block in plan.blocks:
            if block.kind == "left":
                left |= _columns(block.rectangle)
            else:
                columns |= _columns(block.rectangle)
        edge = set(plan.network.columns)
        seen = set(edge)
        for _ in range(plan.band):
            ring = set()
            for x, z in edge:
                for dx in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        side = (x + dx, z + dz)
                        if side not in seen:
                            ring.add(side)
            seen |= ring
            edge = ring
        columns |= seen
        return columns - fixed - left

    def _park(self, block, seed, post, lamp, biome) -> None:
        """A block left green: grass over whatever it was, a path each way
        through the middle, lit, trees and flowers."""
        x0, z0, x1, z1 = block.rectangle
        columns = sorted(_columns(block.rectangle),
                         key=lambda c: (c[1], c[0]))
        cx, cz = (x0 + x1) // 2, (z0 + z1) // 2
        ops: list = []
        paths: list = []
        lawn: list = []
        for x, z in columns:
            y, wet = self._stand((x, z))
            if wet is not None or not _takes(self._peek((x, y, z))):
                continue
            path = abs(x - cx) <= 1 or abs(z - cz) <= 1
            ops.append(("set", (x, y, z), ("minecraft:gravel" if path
                                           else "minecraft:grass_block")))
            ops.append(("set", (x, y - 1, z), "minecraft:dirt"))
            (paths if path else lawn).append((x, z))
        self._commit(ops, what="a park")
        self._paved.update(paths)
        if biome is not None:
            self.save.fill_biome((x0, -64, z0), (x1, 319, z1), biome,
                                 dimension=self.dimension)
        trod = set(paths)
        for a, b in _stretches(z for z in range(z0, z1 + 1)
                               if all((x, z) in trod
                                      for x in range(cx - 1, cx + 2))):
            self.line_lamps(((cx, a), (cx, b)), post=post, lamp=lamp,
                            light_level=NO_SPAWNS, width=3)
        for a, b in _stretches(x for x in range(x0, x1 + 1)
                               if all((x, z) in trod
                                      for z in range(cz - 1, cz + 2))):
            self.line_lamps(((a, cz), (b, cz)), post=post, lamp=lamp,
                            light_level=NO_SPAWNS, width=3)
        trees = self.scatter(lawn, [(Tree("oak"), 2), (Tree("birch"), 1)],
                             spacing=7, seed=seed)
        self.scatter(lawn, [(flower, 1) for flower in _FLOWERS], density=0.06,
                     clump=4, cover=0.5, seed=seed + 1)
        return trees

    def _doors_unreached(self, plan, result) -> list:
        """Every doorstep not walked to from the streets with no jump, over
        the streets and plazas alone."""
        columns = set(plan.network.columns)
        for design in result.designs:
            columns |= _columns(design.plaza)
            columns -= design.massing.tiers[0].columns
        space = _OnTheBand(self.save, columns, overlay={},
                           dimension=self.dimension)
        streets = result.streets
        node = sorted(streets.heights)[0]
        start = (node[0], streets.heights[node], node[1])
        ys = [design.doorstep[1] for design in result.designs]
        ys += list(streets.heights.values())
        xs = [x for x, _ in columns]
        zs = [z for _, z in columns]
        lo = (min(xs), min(ys) - 40, min(zs))
        hi = (max(xs), max(ys) + 40 + HEAD_ROOM, max(zs))
        reach = reachable(space, start, lo, hi, allow_jump=False)
        return [design.doorstep for design in result.designs
                if design.doorstep not in reach.reached
                or design.doorstep in reach.stranded]


def _named_cells(refusal) -> list:
    """The ``(x, y, z)`` cells of a refusal over what somebody built: what is
    standing in a street's way, and none for any other refusal."""
    cells = []
    for fault in refusal.faults:
        found = (re.match(r"\((-?\d+), (-?\d+), (-?\d+)\) holds ", fault)
                 if isinstance(fault, str) else None)
        if found:
            cells.append(tuple(int(value) for value in found.groups()))
    return cells


def _stretches(values) -> list:
    """Ascending ``values`` as ``(first, last)`` runs a column in from each
    end, those long enough to want a lamp: a lit strip runs past its line by
    half its width, and a path broken by a pond is two paths."""
    runs: list = []
    for value in values:
        if runs and runs[-1][1] == value - 1:
            runs[-1][1] = value
        else:
            runs.append([value, value])
    return [(a + 1, b - 1) for a, b in runs if b - a >= 6]


def _columns(rectangle) -> set:
    a, b, c, d = rectangle
    return {(x, z) for x in range(a, c + 1) for z in range(b, d + 1)}


__all__ = ["BAND", "BLOCK", "CityBlock", "CityLot", "CityMixin", "CityPlan",
           "CityResult", "plan_city", "PLAZA", "SiteMap"]
