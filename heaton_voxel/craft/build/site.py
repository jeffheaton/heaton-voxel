"""The ground under a build.

Reading it, clearing it, grading it -- cutting, filling and draining it,
a pad being a grade -- smoothing terrain, laying roads, fencing plots and
planting trees. The ground writers that take water away refuse what the
game would do with it next: a fill that starves the flowing water it fed,
a cut that lets water out, and -- for a road -- a fill that cuts a body of
water in two.

Part of :mod:`heaton_voxel.craft.build`. What is a tree, what is a plant
and what is natural ground is :mod:`~heaton_voxel.craft.blockinfo`'s to
say, held there to the game's own block tags.
"""

from __future__ import annotations

from functools import partial
import heapq
from types import MappingProxyType
from typing import NamedTuple

from ...nbt import NBTError
from .. import geometry
from ..block_facts import facts, holds_fluid, holds_water
from ..block_registry import BLOCK_IDS
from ..blockinfo import is_block, is_tree, is_vegetation, subsoil_of
from ..errors import NoSurface, Refused
from .._plan import refuse_many
# The weighted surface mixes live with the rest of the materials vocabulary
# and are the same objects here, so every old import still finds them.
from ..style import FROM_PALETTE, LANE_MIX, Material, ROAD_MIX  # noqa: F401
from ..survey import _first, _ICE, _read_column, _stops, starved
from ._common import _empty, _finished, _Mix, _Slot, _state, _takes, AIR


GRASS = "minecraft:grass_block[snowy=false]"


DIRT = "minecraft:dirt"


#: The nether woods: a stem for a trunk and a wart block for a canopy.
#: Wart blocks never decay, so they carry no ``persistent`` flag to set.
_NETHER_TREES = {
    "crimson": ("minecraft:crimson_stem[axis=y]", "minecraft:nether_wart_block"),
    "warped": ("minecraft:warped_stem[axis=y]", "minecraft:warped_wart_block"),
}


def _tree_blocks(kind: str, leaves=None) -> tuple:
    """The trunk and the canopy of an ornamental tree of ``kind``, a
    wood: ``(trunk, leaf)``. An overworld wood grows its log, upright, and
    its own leaves, ``persistent`` so they never decay; the two nether
    woods (:data:`_NETHER_TREES`) a stem and a wart block. ``leaves``,
    given, is the canopy in place of the wood's own, as it is given. A
    wood the game has no log of is refused, as is one with no leaves of
    its own when no ``leaves`` are given, naming a leaf of the wood's name
    where the game has one (poplar's come in colors) -- an ``NBTError``,
    before anything is read or drawn."""
    if kind in _NETHER_TREES:
        trunk_block, leaf = _NETHER_TREES[kind]
    elif is_block(f"minecraft:{kind}_log"):
        trunk_block = f"minecraft:{kind}_log[axis=y]"
        leaf = f"minecraft:{kind}_leaves[persistent=true]"
    else:
        raise NBTError(f"there is no {kind}_log in the game; kind= is a "
                       f"wood such as oak, birch, cherry or crimson")
    if leaves is not None:
        leaf = leaves
    elif kind not in _NETHER_TREES and not is_block(leaf.split("[")[0]):
        choices = sorted(name for name in BLOCK_IDS
                         if name.endswith(f"_{kind}_leaves"))
        raise NBTError(f"there is no {kind}_leaves in the game; pass "
                       f"leaves= to choose one" + (
                           f", such as {choices[0]}" if choices else ""))
    return trunk_block, leaf


def _clears(name: str) -> bool:
    """Whether clearing a site takes ``name``: part of a tree, or ground
    cover (:func:`~heaton_voxel.craft.blockinfo.is_tree`,
    :func:`~heaton_voxel.craft.blockinfo.is_vegetation`)."""
    return is_tree(name) or is_vegetation(name)


#: What a cleared waterlogged block leaves: the game's own removal writes
#: the cell's fluid back in (``Level.removeBlock``), a source at level 0.
_WATER = "minecraft:water[level=0]"


#: What the ground writers do where the ground is water: refuse (None),
#: build on the water's surface ("bridge") or fill it from the bed ("fill").
_OVER_WATER = (None, "bridge", "fill")


def _over_water(choice) -> None:
    """Refuse an ``over_water=`` that is none of :data:`_OVER_WATER` -- a
    bad argument, raised before anything is read."""
    if choice not in _OVER_WATER:
        raise ValueError(f"over_water is None (refuse water), 'bridge' or "
                         f"'fill', not {choice!r}")


def _no_surface(dim, x: int, z: int, reason: str,
                below: int | None = None) -> NoSurface:
    """The refusal for a column the reader found no ground in, saying why in
    the words of :attr:`~heaton_voxel.craft.survey.SurveyMap.missing`."""
    chunk_x, chunk_z = x >> 4, z >> 4
    if reason == "ungenerated":
        why = f"chunk ({chunk_x}, {chunk_z}) is not generated"
    elif reason == "partial":
        status = dim.blocks(chunk_x, chunk_z).status
        stage = (f"is at {status}" if status is not None
                 else "records no generation stage")
        why = (f"chunk ({chunk_x}, {chunk_z}) {stage}, not finished terrain, "
               f"so it has no ground yet")
    else:
        why = ("there is nothing in the column to stand on" if below is None
               else f"there is nothing to stand on at or below y={below}")
    return NoSurface(f"no surface at ({x}, {z}): {why}", reason=reason,
                     cell=(x, z))


def _wet_words(probe) -> str:
    """What a wet probe is standing over, for a refusal."""
    if probe.ice and probe.bed_y is None:
        return f"ice at y={probe.y} over air"
    if probe.ice:
        return (f"ice at y={probe.y} over {probe.water} of water, on a bed "
                f"at y={probe.bed_y}")
    return (f"water to y={probe.waterline}, {probe.water} deep over a bed "
            f"at y={probe.y}")


def _water_stand_in(surfacing):
    """What the water check reads a road's surface as
    (:meth:`Builder._contain_plan`): the block itself, or for a list mix,
    drawn as each column is written and so no block yet, the first of its
    entries water can enter (a slab, a carpet), and dirt where it can enter
    none, since then no entry is a way in or a hole to fall through, and
    one whole block stands for them all. Reads and draws nothing."""
    if not isinstance(surfacing, _Mix):
        return surfacing
    for name, _ in surfacing.mix:
        state = _state(name)
        if facts(state.name, state.properties).enters:
            return name
    return DIRT


def _swath(line, half: int) -> dict[tuple[int, int], int]:
    """The columns a square ``2 * half + 1`` across covers, stamped on each
    cell of ``line`` -- a centerline of ``(x, z)`` cells, as
    :func:`~heaton_voxel.craft.geometry.line` walks one -- each once, in
    the order a road paves them: along the centerline, then across it,
    ``dx`` outer and ``dz`` inner. Iterated, it is the swath.

    Each column maps to the index in ``line`` of the first cell whose
    square reaches it: the stretch of road that cuts it, and so the height
    it is cut to and the point in the write it is written at."""
    swath: dict[tuple[int, int], int] = {}
    for i, (cx, cz) in enumerate(line):
        for dx in range(-half, half + 1):
            for dz in range(-half, half + 1):
                swath.setdefault((cx + dx, cz + dz), i)
    return swath


#: What the ground under a stretch of road calls for (:class:`RoadRule`):
#: the station kinds of ``examples/road.py``, whose classifier it is.
GRADE, CUT, TUNNEL, BRIDGE = "grade", "cut", "tunnel", "bridge"


class Stretch(NamedTuple):
    """A run of a road's centerline cells all of one kind
    (:attr:`RoadResult.stretches`)."""

    #: :data:`GRADE`, :data:`CUT`, :data:`TUNNEL` or :data:`BRIDGE`.
    kind: str
    #: The index in :attr:`RoadResult.line` of its first cell.
    start: int
    #: The index of its last cell, inclusive.
    end: int


class BlendResult:
    """What :meth:`SiteMixin.blend` did: ``columns``, how many it was given;
    ``raised``, ``lowered`` and ``left``, what became of those that could
    move; ``held``, those something built stopped; and ``steep``, the columns
    between heights no slope could join."""

    __slots__ = ("columns", "raised", "lowered", "left", "held", "steep")

    def __init__(self, columns: int):
        self.columns = columns
        self.raised = self.lowered = self.left = self.held = self.steep = 0

    def __repr__(self) -> str:
        return (f"<BlendResult {self.raised:,} raised, {self.lowered:,} "
                f"lowered, {self.left:,} left, {self.held:,} held, "
                f"{self.steep:,} steep>")


def _slopes(sources: dict, region, run: int, sign: int) -> dict:
    """``{column: value}`` over ``region``: the least, over every source
    joined to it through ``region``, of ``sign`` times the source's height
    in units of a block over ``run``, plus the king's steps to it. With
    ``sign`` 1 the value over ``run`` is how high the column may stand, and
    with ``sign`` -1 its negative how low."""
    best: dict = {}
    heap: list = []
    for column, y in sorted(sources.items()):
        key = sign * y * run
        best[column] = key
        heap.append((key, column))
    heapq.heapify(heap)
    while heap:
        key, (x, z) = heapq.heappop(heap)
        if key > best[(x, z)]:
            continue
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                side = (x + dx, z + dz)
                if side in region and key + 1 < best.get(side, key + 2):
                    best[side] = key + 1
                    heapq.heappush(heap, (key + 1, side))
    return {column: value for column, value in best.items()
            if column in region}


class RoadRule:
    """What the ground under a road calls for at each cell of its
    centerline: grade, a cut, a tunnel or a bridge.

    It is the classifier ``examples/road.py`` lays its roads by
    (``classify`` and ``smooth_kinds``), with its thresholds named, and
    :meth:`kind` answers as ``classify`` does. Water among a cell's
    columns makes it a **bridge** before anything else is asked; ground
    higher than the road's surface makes it a **cut**, and a **tunnel**
    where that ground stands ``tunnel_from`` or more above the surface;
    more than ``shallow_fill`` blocks of fill between the lowest ground
    and the road's bed (:meth:`deep`) makes it a bridge; the rest is
    **grade**. A hill is asked about before a dip, so a knife edge is cut
    rather than bridged. :meth:`kinds` then closes every run of
    ``skylight`` or fewer cells between two tunnels into the tunnel, so a
    crest dipping briefly under ``tunnel_from`` does not open a skylight
    mid-bore.

    The defaults -- ``shallow_fill=3``, ``tunnel_from=7``,
    ``skylight=3`` -- are that example's choices, made building it, and
    no rule of the game's: an embankment of three blocks it graded and
    one of four it bridged. :meth:`Builder.road` reports the kinds
    (:class:`RoadResult`) and builds none of them but grade -- it cuts a
    tunnel open to the sky and fills what a bridge would span -- and it
    refuses a fill deeper than ``shallow_fill`` unless told
    ``allow_dam=True``.
    """

    __slots__ = ("shallow_fill", "tunnel_from", "skylight")

    def __init__(self, *, shallow_fill: int = 3, tunnel_from: int = 7,
                 skylight: int = 3):
        for word, value, least in (("shallow_fill", shallow_fill, 0),
                                   ("tunnel_from", tunnel_from, 1),
                                   ("skylight", skylight, 0)):
            if isinstance(value, bool) or not isinstance(value, int) \
                    or value < least:
                raise NBTError(f"a RoadRule's {word} is a whole number of "
                               f"blocks, {least} or more, got {value!r}")
        #: The most blocks of fill under a road's bed that are still grade.
        self.shallow_fill = shallow_fill
        #: How far above the road's surface ground makes a cut a tunnel.
        self.tunnel_from = tunnel_from
        #: The longest run between two tunnels closed into the tunnel.
        self.skylight = skylight

    def kind(self, y: int, high: int, low, wet: bool) -> str:
        """What one cell of road calls for: its surface at ``y`` over
        ground whose highest column stands at ``high`` and whose lowest dry
        one at ``low``, ``wet`` when water is among its columns (``low``
        is then never asked, and may be None)."""
        if wet:
            return BRIDGE
        if high > y:
            return TUNNEL if high - y >= self.tunnel_from else CUT
        if self.deep(y, low):
            return BRIDGE
        return GRADE

    def deep(self, y: int, low: int) -> bool:
        """Whether a road's surface at ``y`` over ground at ``low`` stands
        on more than :attr:`shallow_fill` blocks of fill: the blocks from
        ``low + 1`` to the bed at ``y - 1``."""
        return y - 1 - low > self.shallow_fill

    def kinds(self, stations) -> tuple[str, ...]:
        """The kind of every cell of a road, in order: ``stations`` holds
        ``(y, high, low, wet)`` for each cell, as :meth:`kind` takes them,
        or None for a cell with no ground of its own to judge -- one whose
        square reaches no column the cells before it had not, as on a road
        wider than one or one doubling back -- which takes the kind of the
        cell before it, or :data:`GRADE` at the start. Then every run of
        :attr:`skylight` or fewer cells between two tunnels is a tunnel
        too (``smooth_kinds``)."""
        out: list[str] = []
        for station in stations:
            if station is None:
                out.append(out[-1] if out else GRADE)
            else:
                out.append(self.kind(*station))
        i = 0
        while i < len(out):
            if out[i] != TUNNEL:
                i += 1
                continue
            j = i + 1
            while j < len(out) and out[j] == TUNNEL:
                j += 1
            k = j
            while k < len(out) and out[k] != TUNNEL and k - j < self.skylight:
                k += 1
            if k < len(out) and out[k] == TUNNEL and k > j:
                for gap in range(j, k):
                    out[gap] = TUNNEL
            i = j
        return tuple(out)

    @staticmethod
    def stretches(kinds) -> tuple[Stretch, ...]:
        """``kinds`` as runs: a :class:`Stretch` for each run of one kind,
        in order."""
        runs: list[Stretch] = []
        for i, kind in enumerate(kinds):
            if runs and runs[-1].kind == kind:
                runs[-1] = runs[-1]._replace(end=i)
            else:
                runs.append(Stretch(kind, i, i))
        return tuple(runs)

    def __repr__(self) -> str:
        return (f"RoadRule(shallow_fill={self.shallow_fill}, "
                f"tunnel_from={self.tunnel_from}, skylight={self.skylight})")


class RoadResult:
    """What :meth:`Builder.road` laid, for whatever is laid along it next.

    ``points`` are the waypoints, as checked, and ``width`` the width
    asked for: :meth:`Builder.line_lamps` given the two walks the same
    :func:`~heaton_voxel.craft.geometry.line` and swath, so its posts stand
    beside :attr:`swath`. ``line`` is the centerline, a cell per step;
    ``heights`` the profile the road rides, one height per cell of it --
    the ground's, smoothed and anchored
    (:func:`~heaton_voxel.craft.geometry.smoothed_profile`), so a high
    anchor is the step it stands above its neighbors, there to be seen --
    and each the surface of that cell's stretch: the height of every
    column the stretch cut first. It is not the surface laid along the
    centerline. Each column is cut by the first stretch whose square
    reaches it, and on a road wider than 1 the stretch before every
    centerline cell but the first already reaches it, so the surface laid
    at ``line[i]`` is an earlier stretch's -- ``heights`` shifted
    ``width // 2`` cells along a straight road, every change of level and
    an anchor's step among it. The surface laid along the centerline is
    ``[r.top(*cell) for cell in r.line]``, and along a straight road
    running along x or z it is the road's surface all the way across its
    width at each cell: what a hedge or a wall beside the road follows
    (:meth:`Builder.extrude`'s ``follow=``) to stand on the road's edge.
    ``swath`` maps each column paved, in the order paved, to the index in
    ``line`` of the stretch that cut it, read-only; :meth:`top` is the
    height it was cut to. ``kinds`` is what the ground called for at each
    cell of ``line`` (:class:`RoadRule`) and ``stretches`` the same as runs
    (:class:`Stretch`): a cut, a tunnel the road cut open to the sky, or a
    bridge it filled under, allowed (``allow_dam=True``) or over water. A
    deep fill is not always a bridge: on a side-hill, ground over the road
    on one side and falling away on the other, the rule asks about the
    hill first and calls it a cut or a tunnel, though the road refused its
    fill as a dam all the same until allowed.
    """

    __slots__ = ("points", "width", "line", "heights", "swath", "kinds",
                 "stretches")

    def __init__(self, points, width, line, heights, swath, kinds, stretches):
        self.points = tuple(points)
        self.width = width
        self.line = tuple(line)
        self.heights = tuple(heights)
        self.swath = MappingProxyType(dict(swath))
        self.kinds = tuple(kinds)
        self.stretches = tuple(stretches)

    def top(self, x: int, z: int) -> int:
        """The height the road cut column ``(x, z)`` to: its stretch's
        surface. A column off the swath is an ``NBTError`` naming it."""
        i = self.swath.get((x, z))
        if i is None:
            raise NBTError(f"({x}, {z}) is not in the road's swath")
        return self.heights[i]

    def __repr__(self) -> str:
        counts: dict[str, int] = {}
        for kind in self.kinds:
            counts[kind] = counts.get(kind, 0) + 1
        kinds = ", ".join(f"{count} {kind}" for kind, count in counts.items())
        return (f"<RoadResult {len(self.line)} cells, {len(self.swath)} "
                f"columns, width {self.width}: {kinds}>")


class GradeResult:
    """What :meth:`Builder.grade` did, column by column of its outline.

    Every column is counted once: a dry column ``cut`` down to the target,
    ``raised`` up to it, or ``surfaced`` where it already stood there; a
    wet one ``drained`` -- filled up from its bed, by ``drain=True`` or
    ``over_water="fill"``, so no water is left under the surface, or cut
    down to a target under its bed, bed and all, by a drain -- or
    ``bridged``, the surface stood on the water; or ``left`` alone, where
    ``cut=False`` or ``fill=False`` said so. ``columns`` is how many the
    outline holds, the sum of those six. ``retained`` counts the edge
    columns whose fill is the retaining wall, and ``sealed`` the seals
    written where water would otherwise have got out (``seal=``).

    ``beside_drained`` is every cell of water left horizontally beside
    the water the grade took out -- the pond the outline cut across, the
    rest of the lake past a drained bay -- sorted: water still standing
    against what was filled where it stood, which the grade was not asked
    to take and did not. A drain splits a body of water freely, since the
    caller drew the outline; this is what it left.
    """

    __slots__ = ("columns", "cut", "raised", "surfaced", "drained",
                 "bridged", "left", "retained", "sealed", "beside_drained")

    def __init__(self, columns: int):
        self.columns = columns
        self.cut = self.raised = self.surfaced = 0
        self.drained = self.bridged = self.left = 0
        self.retained = self.sealed = 0
        self.beside_drained: tuple = ()

    def __repr__(self) -> str:
        parts = [f"{getattr(self, word)} {word}" for word in
                 ("cut", "raised", "surfaced", "drained", "bridged", "left",
                  "retained", "sealed") if getattr(self, word)]
        if self.beside_drained:
            parts.append(f"{len(self.beside_drained)} cells of water left "
                         f"beside the drain")
        return (f"<GradeResult {self.columns} columns"
                + (f": {', '.join(parts)}" if parts else "") + ">")


def _outline(outline, what: str, work: str = "grade",
             noun: str = "outline"):
    """``(columns, rect)`` for a grade's or a basin's ``outline`` (or a
    scatter's area, ``noun`` the word its refusals use): an
    inclusive rectangle ``(x0, z0, x1, z1)`` of four whole block
    coordinates, its corners in either order -- every column of it, and
    the rectangle sorted -- or any iterable of ``(x, z)`` columns, each
    held to :func:`~heaton_voxel.craft.geometry.line`'s rule for a pair of
    ints, a column given twice taken once, and ``rect`` None. Either way
    the columns come in ``(z, x)`` order, the order a grade plans and
    writes them in. An empty outline, or anything else, is an ``NBTError``
    naming it, ``what`` the call and ``work`` what it has nothing to do."""
    if (isinstance(outline, (tuple, list)) and len(outline) == 4
            and not any(isinstance(v, (tuple, list)) for v in outline)):
        if any(isinstance(v, bool) or not isinstance(v, int) for v in outline):
            raise NBTError(f"{what}'s rectangle is (x0, z0, x1, z1) in whole "
                           f"block coordinates, got {tuple(outline)!r}")
        x0, z0, x1, z1 = outline
        x0, x1 = sorted((x0, x1))
        z0, z1 = sorted((z0, z1))
        return ([(x, z) for z in range(z0, z1 + 1) for x in range(x0, x1 + 1)],
                (x0, z0, x1, z1))
    try:
        columns = {geometry._pair(column, f"a column of {what}'s {noun}")
                   for column in outline}
    except TypeError:
        raise NBTError(f"{what}'s {noun} is a rectangle (x0, z0, x1, z1) or "
                       f"(x, z) columns, got {outline!r}") from None
    if not columns:
        raise NBTError(f"{what}'s {noun} holds no columns, so there is "
                       f"nothing to {work}")
    return sorted(columns, key=lambda c: (c[1], c[0])), None


class SiteMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods:
    the ground under a build.

    Builder inherits them; nothing here is used on its own.
    """

    # -- ground ------------------------------------------------------------

    def probe(self, x: int, z: int, *, below: int | None = None):
        """What one column looks like, read the way
        :func:`~heaton_voxel.craft.survey.walk_surface` reads it: a
        :class:`~heaton_voxel.craft.survey.SurfaceProbe`, with the ground
        you stand on under the snow and the water, and the water, the ice
        and the bed reported apart.

        Cached per column for the session, as the world was when it was
        first read: no write of the Builder's updates it, so a probe read
        first over ground this session already reshaped describes neither
        the old ground nor the new. :meth:`ground` is the truth for a
        column the session has shaped. ``below`` reads the floor at or
        under ``y=below`` instead -- an interior, a cave -- from the blocks
        as they are now, and is never cached.

        Where there is no ground, raises
        :class:`~heaton_voxel.craft.errors.NoSurface` naming the column in
        :attr:`~heaton_voxel.craft.errors.NoSurface.cell` and why in
        :attr:`~heaton_voxel.craft.errors.NoSurface.reason`:
        ``"ungenerated"``, ``"partial"`` (a chunk the generator set aside
        part-way, without the maps the game writes) or ``"empty"``. A chunk
        that cannot be read, or whose heightmap contradicts it, is
        corruption and raises a plain :class:`~heaton_voxel.nbt.NBTError`.
        """
        x, z = int(x), int(z)
        if below is None:
            found = self._probes.get((x, z))
            if found is not None:
                return found
        else:
            below = int(below)
        dim = self.save.dimension(self.dimension)
        found = _read_column(dim, x, z, below)
        if isinstance(found, str):
            raise _no_surface(dim, x, z, found, below)
        if below is None:
            self._probes[(x, z)] = found
        return found

    def ground(self, x: int, z: int) -> int:
        """The terrain surface at a column: where a build stands on it.

        The height this session set, where it shaped the column -- a pad,
        a road, paving, smoothing, a felled tree, :meth:`set_ground` --
        and otherwise the :meth:`probe`'s
        :attr:`~heaton_voxel.craft.survey.SurfaceProbe.waterline`: the top
        of what stops a fall, which is the ground under any snow, never a
        canopy, a torch or a flower standing on it, and the water's surface
        over a lake. That is ``MOTION_BLOCKING_NO_LEAVES`` where the game
        wrote it, and the same read off a finished chunk's blocks where
        :meth:`~heaton_voxel.world.MinecraftSave.rebuild_heightmaps` took
        it away, so ground built in an earlier, never-yet-loaded session
        still reads true. Raises as :meth:`probe` does where there is no
        ground.
        """
        shaped = self._ground.get((x, z))
        if shaped is not None:
            return shaped
        return self.probe(x, z).waterline

    def set_ground(self, x: int, z: int, y: int) -> None:
        """Record that this session shaped a column's ground to ``y``, as a
        script does after building it by hand; :meth:`ground` answers it
        from then on, and the ground writers build on it without probing
        -- water a script filled is ground. A height left on water or an
        ice sheet is still water to them: read under itself, and refused,
        bridged or filled as any wet column is (:meth:`_build_base`)."""
        self._ground[(x, z)] = int(y)

    def forget_ground(self, x: int | None = None, z: int | None = None) -> None:
        """Forget what the session knows of a column's ground -- the height
        it set and the probe it read -- so the next :meth:`ground` reads the
        world again. With no arguments, every column; a column needs both
        its ``x`` and its ``z``."""
        if x is None and z is None:
            self._ground.clear()
            self._probes.clear()
            return
        if x is None or z is None:
            raise TypeError("forget_ground takes a column's x and z, or "
                            "nothing to forget every column")
        self._ground.pop((x, z), None)
        self._probes.pop((int(x), int(z)), None)

    def known_ground(self, x: int, z: int) -> int | None:
        """:meth:`ground` where the session already knows it -- a height it
        set, or the waterline of a probe it read -- and None where it does
        not. Never reads the world."""
        shaped = self._ground.get((x, z))
        if shaped is not None:
            return shaped
        found = self._probes.get((x, z))
        return None if found is None else found.waterline

    def stand_below(self, x: int, z: int, y: int) -> int:
        """Where you stand at or under ``y`` in a column: the waterline of
        the floor there, read from the blocks as they are now and not
        cached -- the floor of a room under a roof, the bed of a cave. Raises
        as :meth:`probe` does where there is none."""
        return self.probe(x, z, below=y).waterline

    def _shaped_wet(self, x: int, z: int, shaped: int):
        """A column this session shaped to ``shaped``, where that height is
        still water or an ice sheet -- the pond under a felled branch, a
        :meth:`basin`, which sets each column's ground at its surface: its
        probe read under the height
        (:meth:`stand_below`), which the ground writers refuse, bridge or
        fill like any wet column's. None where the session's ground is dry,
        which is every height it built itself; a waterlogged stair, or ice
        lying on the ground, is stood on as the probe stands on one."""
        block = self.save.get_block(x, shaped, z, dimension=self.dimension)
        if block is None or not (holds_fluid(block.name, block.properties)
                                 or block.name in _ICE):
            return None
        found = self.probe(x, z, below=shaped)
        return found if found.wet else None

    def _stand(self, cell) -> tuple:
        """Where one ``(x, z)`` column stands, read before anything is
        written: ``(y, wet)``, the height it stands on dry and, where it is
        water or an ice sheet, its probe -- None where it is dry.

        A column this session shaped stands on the height it set
        (:meth:`set_ground`), and is wet only where that height is still
        water or ice (:meth:`_shaped_wet`). Every other column is probed
        (:meth:`probe`), which raises
        :class:`~heaton_voxel.craft.errors.NoSurface` where there is no
        ground. What a wet column stands on is the writer's to say:
        :meth:`_build_base` refuses it, bridges it or fills it."""
        shaped = self._ground.get(cell)
        if shaped is not None:
            return shaped, self._shaped_wet(cell[0], cell[1], shaped)
        found = self.probe(*cell)
        return found.y, (found if found.wet else None)

    def _build_base(self, tops, over_water, call: str, *, filled=None,
                    fills: bool = True, wet=None, drain=None,
                    allow_dam=None) -> dict:
        """Where each column of a ground writer's footprint stands, read
        before anything is written, refusing water unless told what to do
        with it -- and refusing a fill that would starve flowing water
        unless told it may.

        ``tops`` maps each ``(x, z)`` to the height the writer puts its
        surface at. Columns are read in ``(z, x)`` order, each by
        :meth:`_stand`: a column this session shaped stands on the height
        it set (:meth:`set_ground`) and is not probed, unless that height
        is still water or ice (:meth:`_shaped_wet`). Every other column is
        probed (:meth:`probe`), which raises
        :class:`~heaton_voxel.craft.errors.NoSurface` there and then where
        there is no ground: dry ground stands on
        :attr:`~heaton_voxel.craft.survey.SurfaceProbe.y`. Wet ground --
        open water, or an ice sheet over water or air
        (:attr:`~heaton_voxel.craft.survey.SurfaceProbe.wet`) -- stands
        where ``over_water`` says:

        * ``None`` -- nowhere: it is refused.
        * ``"bridge"`` -- the waterline, the water's surface or the ice, so
          the build stands on the water and the water stays under it.
        * ``"fill"`` -- the bed, so the build is filled up from it and no
          water is left under it; refused where there is no bed (an ice
          sheet over air -- a lid) and where the surface asked for lies
          under the waterline, which would leave the water standing over
          the build. The columns stood on their beds are added to the set
          ``filled``, when one is given, for the writer to fill.

        ``drain`` is for a writer that can take the water out
        (:meth:`grade`): ``None`` for one that cannot, ``False`` where it
        was not asked to, so a refusal offers ``drain=True`` besides, and
        ``True`` to drain it, ``over_water`` then None -- every wet column
        stands on its bed, whatever the surface asked for, and is added to
        ``filled`` for the writer to fill up from it and cut down to its
        surface, while an ice sheet over air, a lid, is refused: draining
        it would pour whatever is over it into whatever is below.

        Every refused column is gathered, then one
        :class:`~heaton_voxel.craft.errors.Refused` names the first and
        how many there were, and holds their probes as its
        :attr:`~heaton_voxel.craft.errors.Refused.faults`. ``call`` names
        the writer in it, and ``fills=False`` leaves ``"fill"`` out of
        what it offers, for a writer that surfaces nothing to fill under
        (:meth:`light_cells`). ``wet``, when a dict is given, is handed
        ``{(x, z): probe}`` for every wet column read, bridged, filled or
        refused, so a writer can say what it built over water.

        A column stood on its bed takes out the water over it, every cell
        from the bed up to the waterline, and flowing water beside it may
        have been fed by that water: the game re-reads a flow from what
        feeds it (``getNewLiquid``) and never a source, so a flow whose
        feed is filled weakens, or dries where it stands, and the flows it
        fed after it. :func:`~heaton_voxel.craft.survey.starved` says
        which, and a fill that starves any is refused (:meth:`_starve`)
        unless ``allow_dam`` is True: ``False`` offers ``allow_dam=True``,
        and ``None`` -- a writer with no such argument, :meth:`pave` --
        points at draining the water first with :meth:`grade`. Returns
        ``{(x, z): y}``.
        """
        _over_water(over_water)
        base: dict[tuple[int, int], int] = {}

        refused = []
        # The columns stood on their beds, with their probes: the water
        # each takes out.
        from_bed = []
        for cell in sorted(tops, key=lambda c: (c[1], c[0])):
            dry, found = self._stand(cell)
            if found is None:
                base[cell] = dry
                continue
            if wet is not None:
                wet[cell] = found
            if drain:
                if found.bed_y is None:
                    refused.append(found)
                    continue
                base[cell] = found.bed_y
                from_bed.append(found)
                if filled is not None:
                    filled.add(cell)
            elif over_water == "bridge":
                base[cell] = found.waterline
            elif (over_water == "fill" and found.bed_y is not None
                    and tops[cell] >= found.waterline):
                base[cell] = found.bed_y
                from_bed.append(found)
                if filled is not None:
                    filled.add(cell)
            else:
                refused.append(found)
        if not refused:
            if from_bed and not allow_dam:
                self._starve([(probe.x, y, probe.z) for probe in from_bed
                              for y in range(probe.bed_y + 1,
                                             probe.waterline + 1)],
                             call=call, allow_dam=allow_dam)
            return base

        first = refused[0]
        count = (f"{len(refused)} columns" if len(refused) > 1 else "1 column")
        where = f"({first.x}, {first.z})"
        if drain:
            message = (f"{call} cannot drain {count}, first: {where} is "
                       f"{_wet_words(first)}, with no bed under it to fill "
                       f"from -- a lid over whatever is below; leave it out "
                       f"of the outline, or stand on it with drain=False, "
                       f"over_water='bridge'")
        elif over_water is None:
            fill = (", or over_water='fill' to fill it from the bed"
                    if fills else "")
            if drain is False:
                fill += ", or drain=True to drain it"
            message = (f"{call} would build over water or ice at {where} "
                       f"({count}): {_wet_words(first)}. Pass "
                       f"over_water='bridge' to build on the water's "
                       f"surface{fill}")
        else:
            if first.bed_y is None:
                why = (f"is {_wet_words(first)}, with no bed under it to "
                       f"fill from -- a lid over whatever is below")
            else:
                why = (f"has its waterline at y={first.waterline}, above "
                       f"the y={tops[(first.x, first.z)]} asked for, which "
                       f"would leave the water standing on the build")
                if drain is False:
                    why += (" -- drain=True, with over_water left out, takes "
                            "the water out down to it")
            message = (f"{call} cannot fill {count} from the bed, first: "
                       f"{where} {why}")
        raise Refused(message + ". Nothing was written", faults=refused)

    def _starve(self, removed, *, call: str, allow_dam) -> None:
        """Refuse taking the water out of ``removed`` -- ``(x, y, z)``
        cells a ground writer fills, cuts or seals -- where that starves
        flowing water: every flow and column of falling water beside it,
        and those they feed in turn, re-read by the game's own
        ``getNewLiquid`` (:func:`~heaton_voxel.craft.survey.starved`), that
        would weaken or dry where it stands. One
        :class:`~heaton_voxel.craft.errors.Refused` names the first and
        how many, its :attr:`~heaton_voxel.craft.errors.Refused.faults` the
        starved cells, sorted; ``call`` opens it. ``allow_dam`` is the
        writer's: ``False`` offers ``allow_dam=True``, and ``None`` -- a
        writer that takes no such argument -- points at :meth:`grade`,
        which drains on purpose. A source is never starved: the game never
        re-reads one."""
        cut_off = starved(self.save, removed, dimension=self.dimension)
        if not cut_off:
            return
        first = cut_off[0]
        block = self.save.get_block(*first, dimension=self.dimension)
        count = (f"{len(cut_off)} cells of it" if len(cut_off) > 1
                 else "1 cell of it")
        if allow_dam is None:
            advice = (f"Take that water out on purpose first with "
                      f"grade(..., drain=True, allow_dam=True), or keep "
                      f"{call} off the water that feeds it")
        else:
            advice = (f"Pass allow_dam=True to fill it all the same, or keep "
                      f"{call} off the water that feeds it")
        raise Refused(f"{call} would cut flowing water off from what feeds "
                      f"it -- {count} would weaken or dry where it stands, "
                      f"first {first}, {block}. {advice}. Nothing was "
                      f"written", faults=cut_off)

    # -- site preparation ----------------------------------------------------

    def clear_vegetation(self, corner1, corner2, *,
                         height: int | None = None) -> int:
        """Fell trees and strip plants over a rectangle of ``(x, z)`` pairs.

        A build site is cleared land. Every tree block and every plant over
        each column's ground goes -- logs, wood, stems, leaves, saplings,
        huge mushrooms, a pale oak's creaking heart, grass, flowers, vines,
        roots, lying snow (:func:`~heaton_voxel.craft.blockinfo.is_tree`,
        :func:`~heaton_voxel.craft.blockinfo.is_vegetation`) -- up to the
        top of the column, however tall the tree: its ``WORLD_SURFACE``, or
        its blocks where this session has already built in the chunk, since
        no write of the Builder's moves a map. A waterlogged block leaves
        its water behind, as it does when the game removes one, so a swamp
        keeps its surface, and water left lying on the ground -- the leaf
        at a swamp's waterline the game's map passes over -- is the
        column's ground from then on: still water, which a pad, a road or a
        floor refuses or bridges or fills as it does any other.

        Where the ground is itself a tree -- the top log, which the game's
        map stops at and so :meth:`ground` does -- or a height this session
        set on a plant, a torch, a rail or anything else that stops no
        fall, the column is walked down through it to the block the game's
        map will land on once the tree is gone, clearing every tree block
        and plant on the way, so a felled tree goes to its roots.
        That block is the column's ground from then on -- a pond's surface,
        where the tree overhung one, which stays water to the ground
        writers -- and plain dirt a tree stood on turns to grass. Returns
        blocks removed.

        Only the open column over the ground is cleared, and the sweep up it
        stops at the first block that stops a fall and is not part of a
        tree -- a ledge, a roof -- because what grows on top of that does
        not grow on this ground. Lichen and vines on an open cliff face go,
        and so do roots hanging from a ledge into the column over a ground
        set beneath it (a pad's, say). Nothing below the ground is touched:
        the roots and lichen under an overhang the column's ground is the
        top of, a cave's ceiling, a mushroom in a cellar. ``height`` caps
        how far above the ground the sweep reaches, as it once always did at
        26 blocks. Trees *outside* the rectangle keep their canopy even
        where it overhangs -- clear a margin if that matters. A huge
        fungus's shroomlights are left, since a shroomlight is a builder's
        lamp as well and nothing in one column says which it is, so a cap
        felled from above stops at its first one and the column's ground
        is left on it.
        """
        (x0, z0), (x1, z1) = corner1, corner2
        x0, x1 = sorted((x0, x1))
        z0, z1 = sorted((z0, z1))
        removed = 0
        # Planned whole, then written: every column reads and writes only
        # itself, so each read here is the one the column would have made
        # with the columns before it already cleared. The chunks those
        # columns write in are the one thing that changes how a column is
        # read (_column_top), and are tracked as the plan grows.
        ops: list[tuple] = []
        planned: set[tuple[int, int]] = set()

        def write(x: int, y: int, z: int, block) -> None:
            ops.append(("set", (x, y, z), block))
            planned.add((x >> 4, z >> 4))

        def clear(x: int, y: int, z: int, found) -> bool:
            """Clear one cell, leaving the water ``found`` held, as the
            game's own removal does; whether it held any."""
            wet = holds_fluid(found.name, found.properties)
            write(x, y, z, _WATER if wet else AIR)
            return wet

        # A total order: z, x, then up the column, then down it.
        for z in range(z0, z1 + 1):
            for x in range(x0, x1 + 1):
                g = ground = self.ground(x, z)
                here = self.save.get_block(x, g, z, dimension=self.dimension)
                # The ground was read off a trunk, a plant or a torch, and is
                # walked down from once the column over it is clear.
                felled = here is not None and (_clears(here.name)
                                               or not _stops(here))
                top = self._column_top(x, z, planned)
                limit = None if height is None else g + int(height) - 1
                # Whether a tree stood on the ground: the dirt under a trunk.
                stood = False
                y = g + 1
                while limit is None or y <= limit:
                    b = self.save.get_block(x, y, z, dimension=self.dimension)
                    if b is None:
                        break                     # the top of the world
                    if _clears(b.name):
                        removed += 1
                        if clear(x, y, z, b) and not felled and y == ground + 1:
                            ground, stood = y, False   # a swamp's surface
                        elif y == g + 1:
                            stood = is_tree(b.name)
                    elif _stops(b) or top is None or y > top:
                        break                     # a roof, or past the top
                    y += 1
                if felled:
                    # Walk down to what the ground stands on, clearing trees
                    # and plants and passing air and anything that stops no
                    # fall, as the game's map will once they are gone.
                    floor = None
                    y = g
                    while (b := self.save.get_block(
                            x, y, z, dimension=self.dimension)) is not None:
                        if _clears(b.name):
                            removed += 1
                            if clear(x, y, z, b):
                                floor, stood = y, False   # its water stays
                                break
                            stood = is_tree(b.name)
                        elif _stops(b):
                            floor = y
                            break
                        else:
                            stood = False
                        y -= 1
                    if floor is None:
                        # Nothing under it all the way down: no ground.
                        ops.append(("ground", (x, z), None))
                        continue
                    ground, here = floor, b
                if ground != g:
                    ops.append(("ground", (x, z), ground))
                if stood and here is not None and here.name == DIRT:
                    write(x, ground, z, GRASS)
        self._commit(ops, what="clear_vegetation")
        return removed

    def _column_top(self, x: int, z: int, planned=()) -> int | None:
        """The topmost block of a column that is not air: its
        ``WORLD_SURFACE``, or the blocks themselves where no map records one
        or where this session has written in the chunk -- or plans to, in
        ``planned`` -- which no map has heard of, on a finished chunk only,
        whose blocks are its terrain. None for a column of air, or for an
        unfinished chunk with no map."""
        # Not the probe: that is the world as first read, while this is the
        # column as the session has left it -- the column reader's own walk
        # down the blocks, over the sections that hold nothing but air.
        cx, cz = x >> 4, z >> 4
        mapped = self.save.surface_y(x, z, "WORLD_SURFACE",
                                     dimension=self.dimension)
        if (mapped is not None and not self.journal._wrote_in((cx, cz))
                and (cx, cz) not in planned):
            return mapped
        accessor = self.save.dimension(self.dimension).blocks(cx, cz)
        if accessor is None or not _finished(accessor.status):
            return mapped
        return _first(accessor, x & 15, z & 15, accessor.max_y,
                      lambda state: not _empty(state))

    def level_pad(self, x0, z0, x1, z1, top_y, *, surface=FROM_PALETTE,
                  wall=FROM_PALETTE, clearance: int = 6,
                  name: str | None = None,
                  over_water=None, allow_dam: bool = False,
                  seal=None, through=(),
                  overwrite: bool = False) -> tuple[int, int, int, int]:
        """Level a building site so its surface block sits at ``top_y``.

        :meth:`grade` over the rectangle, both of its flags on, the pad
        registered: cuts high ground away, fills low ground with dirt
        behind a stone-brick retaining ring on the outer edge, lays the
        surface, and clears ``clearance`` blocks of headroom over it (none
        for 0) -- planned whole first, and refused whole, before a block
        is written, where it reaches a chunk that is not generated or not
        finished (:meth:`_commit`), rather than leaving its first columns
        cut. The rectangle is registered under ``name`` (default
        ``pad-N``, skipping any name already taken) and :meth:`road` and
        :meth:`railway` will refuse to carve through it. A name already
        given to a :meth:`reserve` is refused, before anything is written:
        ``through=`` could no longer say which of the two it means. So is
        one another pad already has, unless it is the same rectangle
        leveled again: a second lot under the first one's name would take
        its keep-out with it, and a road would then carve straight through
        the first. Returns the rectangle.

        ``surface`` and ``wall``, the retaining ring, are each a block, a
        :class:`~heaton_voxel.craft.style.Material` (its block) or a
        :class:`~heaton_voxel.craft.style.Mix`, picked at every cell it is
        laid in; every entry is checked by name before the ground is read.
        Left out, each is the Builder's :attr:`palette`'s: ``surface`` its
        ``pad`` (grass, by default) and ``wall`` its ``retaining`` (stone
        bricks). ``wall=None`` is no ring: dirt to the edge.

        The ground under the pad is read first (:meth:`_build_base`), and
        a pad over water or an ice sheet is refused before anything is
        written, naming every wet column, unless ``over_water`` says what
        to do with it: ``"bridge"`` stands the pad on the water's surface
        and leaves the water under it, and ``"fill"`` fills it up from the
        bed -- ring and dirt, where the water was -- refusing a pad whose
        surface would lie under the waterline, or ice with nothing under it
        to fill from, and a fill that starves the flowing water beside it
        unless ``allow_dam=True``. A bridge laid at the waterline or under
        it takes the water its surface stands in, and is held to the same
        starve check. Ground this session shaped
        (:meth:`set_ground`) is built on as it was set. A pad over a column
        with no ground -- not generated, or not finished -- is refused with
        :class:`~heaton_voxel.craft.errors.NoSurface` naming it, also
        before anything is written. And the pad is held to what
        :meth:`grade` holds every grade to: a cut that would let water out
        is refused, naming each leak, unless ``seal=`` walls it off --
        outside every pad and reservation not named in ``through``, which
        binds the seals alone, since the rectangle is the caller's -- and a
        cut that would leave a torch, a ladder or a door hanging on nothing
        is refused outright (:meth:`_audit_plan`). So is a pad that would
        take what somebody built, unless ``overwrite=True``, as
        :meth:`grade` says. ``top_y`` and
        ``clearance`` are whole numbers: a float, even a whole one, or a
        bool is refused, an ``NBTError`` naming it, before anything is read
        -- where a clearance of ``2.0`` once cleared two and ``True`` one.

        Every pad it still builds, it builds byte for byte as it did before
        it was a grade. What it refuses now and once wrote is each of
        those: a fill that starves a flow, a bridge laid into the water a
        flow is fed by, a cut that lets water out, what a cut leaves
        hanging, what somebody built, and a clearance that is no whole
        number.
        """
        rect = (x0, z0, x1, z1)
        rect = self._grade(rect, top_y, what="level_pad", cut=True, fill=True,
                           drain=None, retain=wall, retain_arg="wall",
                           surface=surface, subsoil=DIRT,
                           clearance=clearance, over_water=over_water,
                           allow_dam=allow_dam, seal=seal, through=through,
                           name=name, pad=True, overwrite=overwrite)[1]
        return rect

    def grade(self, outline, target, *, cut: bool = True, fill: bool = True,
              drain: bool = False, retain=FROM_PALETTE, surface=FROM_PALETTE,
              subsoil=DIRT, clearance: int = 6, over_water=None,
              allow_dam: bool = False, seal=None, through=(),
              name: str | None = None,
              overwrite: bool = False) -> GradeResult:
        """Grade the ground over ``outline`` to ``target``: cut it, fill
        it, drain it -- the earthwork :meth:`level_pad` is, generalized.

        ``outline`` is an inclusive rectangle ``(x0, z0, x1, z1)`` or any
        ``(x, z)`` columns -- a :func:`~heaton_voxel.craft.geometry.disc`,
        a town less its harbor -- each a pair of ints, planned and written
        in ``(z, x)`` order. Every column taken has its surface laid at
        ``target`` and ``clearance`` blocks of air cleared over it (none
        for 0), and the session learns the column's ground there
        (:meth:`set_ground`). A dry column above the target is cut down to
        it -- the cell over its ground goes too, a flower or the snow --
        and is taken only with ``cut``; one below it is filled up to it,
        only with ``fill``; one already at it is resurfaced with either.
        The rest are left exactly as they were. A fill under an **edge**
        column -- one with a side outside the outline, which on a
        rectangle is its border -- is the retaining wall ``retain``, and
        ``subsoil`` (dirt) everywhere else: ``retain`` is a block, a
        :class:`~heaton_voxel.craft.style.Material` (its block) or a
        :class:`~heaton_voxel.craft.style.Mix`, the palette's
        ``retaining`` when left out, and ``None`` for none, subsoil to the
        edge; ``surface`` is the palette's ``pad``, as :meth:`level_pad`'s
        is.

        Water is refused, every wet column named, unless the call says
        what to do with it (:meth:`_build_base`): ``over_water="bridge"``
        and ``"fill"`` as :meth:`level_pad` takes them, or ``drain=True``,
        which takes the water out wherever it stands -- every wet column,
        whatever the flags say, filled from its bed up to the target and
        cut to air from there to its waterline, its ice, kelp and lily pads
        with it -- with ``over_water`` left out (a ``ValueError``
        otherwise: one takes the water away, the other says what to do with
        water that stays). An ice sheet over air is a lid and is never
        drained, since whatever is over it would pour into whatever is
        under it. The bed a drain fills from is the one the column reader
        finds, so where the target stands at the bed or above it nothing
        under the bed is rewritten -- the magic floors the two hand-written
        drains in ``tools/`` fell back on wrote dirt into the ground under
        the bed. A target under the bed takes the column down to it, bed
        and all, as a cut takes dry ground, and ``cut=False`` does not stop
        it: a drained column is taken whole, and counted ``drained``.

        Then, before anything is written, what stands there. Every cell the
        grade would cut, fill, surface or clear is read, and one holding
        what somebody built -- anything
        :func:`~heaton_voxel.craft.build._common._takes` does not take, and
        that is not already what the plan leaves there -- is refused, every
        one named, unless ``overwrite=True``. The outline consults no
        keep-out and a keep-out lasts a session, so this is what stands
        between a later session and a tower it was never told of: the
        column reader stands on a roof as it stands on a hill.

        Then three checks of the game's rules:

        * **a fill that starves flowing water** -- a flow the filled water
          fed, which the game re-reads and weakens or dries
          (:func:`~heaton_voxel.craft.survey.starved`) -- is refused,
          naming every cell of it, unless ``allow_dam=True``; so is a
          bridge laid at the waterline or under it, whose surface takes the
          water it stands in. A source is never starved, nor falling water
          fed from above. Cutting a body of water in two is not refused:
          the caller drew the outline.
        * **a cut that lets water out** -- a bank opened beside the water
          the outline left, a drain below the waterline of a pond it
          straddles -- is refused, naming each
          :class:`~heaton_voxel.craft.survey.Leak`, unless ``seal=`` names
          a full block to wall each one's water off with where it stands
          (:meth:`_contain_plan`); the seals are written after the grade,
          refused inside a keep-out not named in ``through=``, and held to
          the same starve check. Lava touching water, and water running
          into a chunk that is not there, no seal mends.
        * **what the cut leaves hanging** -- a torch or a ladder on a block
          the cut takes, sand over a hole it opens -- is refused
          (:meth:`_audit_plan`).

        ``name`` registers the outline as a pad, as :meth:`level_pad`'s
        does, so :meth:`road` and :meth:`railway` keep out of it; only a
        rectangle has one, and a name already a reservation, or another
        rectangle's pad, is refused. ``None`` registers nothing. The
        outline itself is the caller's, and consults no keep-out, as
        :meth:`level_pad`'s never has (:meth:`reserve`); the seals, which
        the grade places, do.

        Returns a :class:`GradeResult`: how many columns were cut, raised,
        resurfaced, drained, bridged or left, the walls and the seals, and
        the water left standing beside what was drained.
        """
        if name is not None and (not isinstance(name, str) or not name):
            raise NBTError(f"a graded pad's name is a non-empty string, got "
                           f"{name!r}; leave it out to register nothing")
        return self._grade(outline, target, what="grade", cut=bool(cut),
                           fill=bool(fill), drain=bool(drain), retain=retain,
                           retain_arg="retain", surface=surface,
                           subsoil=subsoil, clearance=clearance,
                           over_water=over_water, allow_dam=bool(allow_dam),
                           seal=seal, through=through, name=name,
                           pad=False, overwrite=bool(overwrite))[0]

    def _grade(self, outline, target, *, what: str, cut: bool, fill: bool,
               drain, retain, retain_arg: str, surface, subsoil,
               clearance, over_water, allow_dam, seal, through, name,
               pad: bool, overwrite: bool = False) -> tuple:
        """:meth:`grade` and :meth:`level_pad`, which are one writer:
        ``(result, rect)``. ``what`` names the writer in every refusal;
        ``drain`` is None for one that cannot drain (:meth:`level_pad`,
        whose refusals then read as they always have), and ``retain_arg``
        is the wall's argument by name. ``pad`` registers the rectangle
        whatever ``name`` is, under the first free ``pad-N`` when it is
        none, as :meth:`level_pad` always has.

        The order is fixed and nothing is written or drawn before the
        last: the arguments, in :meth:`level_pad`'s order; the materials
        and every block by name; the ground (:meth:`_build_base`: water,
        and the starve check); the plan; the chunks and heights it reaches
        (:meth:`_reach`, :meth:`_commit`'s refusal word for word); what
        stands there (:meth:`_refuse_built`); the
        water (:meth:`_contain_plan`, the seals' keep-outs and their
        starve check); the survival audit (:meth:`_audit_plan`); and the
        write (:meth:`_commit`), the outline's columns in ``(z, x)`` order
        and the seals after them, in cell order."""
        if name is not None and name in self._reserved:
            raise NBTError(f"{name!r} is already a reservation; give the pad "
                           f"a name of its own")
        noun = "the pad" if pad else "the graded surface"
        if isinstance(clearance, bool) or not isinstance(clearance, int):
            raise NBTError(f"clearance is the headroom cleared over {noun}, "
                           f"a whole number of blocks; got {clearance!r}")
        if clearance < 0:
            raise NBTError(f"clearance is the headroom cleared over {noun}, "
                           f"so it cannot be negative; got {clearance}")
        _over_water(over_water)
        if drain and over_water is not None:
            raise ValueError(f"{what}(drain=True) takes the water out, and "
                             f"over_water={over_water!r} says what to do "
                             f"with water that stays: pass one or the other")
        columns, rect = _outline(outline, what)
        if isinstance(target, bool) or not isinstance(target, int):
            raise NBTError(f"{what}'s target is the whole y its surface is "
                           f"laid at, got {target!r}")
        if name and rect is None:
            raise NBTError(f"only a rectangle is registered as a pad, and "
                           f"{name!r} names an outline of columns: give "
                           f"(x0, z0, x1, z1), or leave name= out")
        if name and self._pads.get(name, rect) != rect:
            raise NBTError(f"{name!r} is already the pad at "
                           f"{self._pads[name]}; give the pad at "
                           f"{rect} a name of its own")
        surface, wall = self._materials(
            what, _Slot("surface", surface, "pad", mixes=True),
            _Slot(retain_arg, retain, "retaining", mixes=True, none=True))
        # The fill under the surface is written wherever a column is raised
        # or drained, so it is one block or a Mix, never none -- refused
        # here, before the ground is read, and not only where some column
        # turns out to need it.
        if subsoil is None:
            raise TypeError(f"{what}(subsoil=None) names no block, and {what} "
                            f"fills with one under the surface: name the "
                            f"block, or leave subsoil= out for dirt")
        if isinstance(subsoil, Material):
            subsoil = subsoil.form("block")
        # The blocks first, as the plan's own check takes them (_commit):
        # a surface the game lacks is that refusal, not the ground's. A
        # seal is one exact block, refused as a Mix before it is named.
        refuse_many(seal)
        self._vet_names((surface, wall, subsoil, seal), what=what)
        self._seal_state(seal, what=what)
        filled: set[tuple[int, int]] = set()
        wet: dict = {}
        base = self._build_base(dict.fromkeys(columns, target), over_water,
                                what, filled=filled, wet=wet, drain=drain,
                                allow_dam=allow_dam)

        # Planned whole, then written: each column reads and writes only
        # itself, so the plan reads what the column-by-column write did.
        result = GradeResult(len(columns))
        inside = set(columns)
        t = target
        ops: list[tuple] = []
        # The water the plan takes out: every cell of it under the surface
        # of a column stood on its bed, and under a bridge laid below the
        # waterline, which cuts the water over the surface to air.
        removed: list[tuple[int, int, int]] = []
        bridged_under = False
        for x, z in columns:
            g = base[(x, z)]
            probe = wet.get((x, z))
            if rect is not None:
                edge = x in (rect[0], rect[2]) or z in (rect[1], rect[3])
            else:
                edge = any(side not in inside for side in
                           ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)))
            walled = edge and wall is not None
            filler = wall if walled else subsoil
            if (x, z) in filled:
                # Stood on its bed: filled up from it, and cut to air from
                # the surface to the waterline where the water stood over.
                result.drained += 1
                if t - 1 >= g + 1:
                    ops.append(("fill", (x, g + 1, z), (x, t - 1, z), filler))
                    result.retained += walled
                if probe.waterline > t:
                    ops.append(("fill", (x, t + 1, z),
                                (x, probe.waterline + 1, z), AIR))
                removed.extend((x, y, z)
                               for y in range(g + 1, probe.waterline + 1))
            else:
                if g > t:
                    if not cut:
                        result.left += 1
                        continue
                    ops.append(("fill", (x, t + 1, z), (x, g + 1, z), AIR))
                    kind = "cut"
                elif g < t:
                    if not fill:
                        result.left += 1
                        continue
                    if t - 1 >= g + 1:
                        ops.append(("fill", (x, g + 1, z), (x, t - 1, z),
                                    filler))
                        result.retained += walled
                    kind = "raised"
                else:
                    if not (cut or fill):
                        result.left += 1
                        continue
                    kind = "surfaced"
                if probe is None:
                    setattr(result, kind, getattr(result, kind) + 1)
                else:
                    result.bridged += 1
                    if probe.waterline >= t:
                        bridged_under = True
                        removed.extend((x, y, z) for y in
                                       range(t, probe.waterline + 1))
            ops.append(("set", (x, t, z), surface))
            if clearance:
                # A fill sorts its corners, so headroom of none would clear
                # the surface just laid.
                ops.append(("fill", (x, t + 1, z), (x, t + clearance, z), AIR))
            ops.append(("ground", (x, z), t))

        # The refusal _commit would give, before the water is read over
        # chunks that are not there.
        self._reach(ops, what=what)
        self._refuse_built(ops, what=what, overwrite=overwrite)
        seals = self._contain_plan(ops, what=what, seal=seal)
        if seals:
            self._keep_out([(x, z) for _, (x, _, z), _ in seals],
                           through=through, what=f"{what}'s seals would stand in")
        if (seals or bridged_under) and not allow_dam:
            self._starve(sorted(set(removed) | {cell for _, cell, _ in seals}),
                         call=what, allow_dam=allow_dam)
        self._audit_plan(ops + seals, what=what)
        self._commit(ops + seals, what=what)
        result.sealed = len(seals)

        if pad or name:
            if not name:
                n = len(self._pads) + 1
                while f"pad-{n}" in self._pads or f"pad-{n}" in self._reserved:
                    n += 1
                name = f"pad-{n}"
            self._pads[name] = rect
            self._register(name)

        # What the grade left standing beside the water it took out, read
        # as written: a seal is no water, and a cell the plan took is none.
        taken = set(removed)
        left = set()
        for x, y, z in taken:
            for side in ((x + 1, y, z), (x - 1, y, z), (x, y, z + 1),
                         (x, y, z - 1)):
                if side in taken or side in left:
                    continue
                found = self.save.get_block(*side, dimension=self.dimension)
                if found is not None and holds_water(found.name,
                                                     found.properties):
                    left.add(side)
        result.beside_drained = tuple(sorted(left))
        return result, rect

    def reserve(self, x0, z0, x1, z1, *, name: str) -> tuple[int, int, int, int]:
        """Keep the routed writers out of an inclusive ``(x, z)`` rectangle
        without leveling it: a grove, a garden, a lot for later.

        A reservation is a pad that writes nothing. :meth:`road`,
        :meth:`railway` and :meth:`approach` refuse to cross it, as they
        refuse a pad, unless it is named in their ``through=``, and
        :meth:`smooth_terrain` holds its columns. The writers handed their
        cells outright -- set, fill, :meth:`level_pad`, a :meth:`grade`'s
        outline, :meth:`clear_vegetation`, :meth:`pave`, the shells, the
        stairs -- do not consult it, as they do not consult pads: what a
        caller names, the caller has seen. The cells a writer places itself
        do, a grade's, a road's and an approach's seals among them.
        ``name`` must be one no pad or reservation already has, and the
        corners whole block coordinates: a fraction is refused, not
        rounded, since which column it meant is a guess. Nothing is written
        or journaled, so a reservation alone gives :meth:`finish` nothing
        to settle. Returns the rectangle, sorted.
        """
        if not isinstance(name, str) or not name:
            raise NBTError("a reservation needs a name, which is what "
                           "through= names it by")
        if name in self._pads or name in self._reserved:
            kind = "pad" if name in self._pads else "reservation"
            raise NBTError(f"{name!r} is already a {kind}; give the "
                           f"reservation a name of its own")
        corners = {"x0": x0, "z0": z0, "x1": x1, "z1": z1}
        bad = [f"{key}={value!r}" for key, value in corners.items()
               if not isinstance(value, int) or isinstance(value, bool)]
        if bad:
            raise NBTError(f"reservation {name!r} needs whole block "
                           f"coordinates for its corners, not {', '.join(bad)}")
        x0, x1 = sorted((x0, x1))
        z0, z1 = sorted((z0, z1))
        rect = (x0, z0, x1, z1)
        self._reserved[name] = rect
        self._register(name)
        return rect

    def smooth_terrain(self, corner1, corner2, *, radius: int = 3,
                       max_shift: int = 3, protect=(), clearance: int = 4,
                       margin: int = 0) -> dict:
        """Ease the jaggedness out of natural ground without flattening it.

        Unlike :meth:`level_pad`, which cuts a site to one plane behind a
        retaining wall, this keeps the landform: each column moves toward the
        mean of its neighbors within ``radius``, but never by more than
        ``max_shift``, so a hillside stays a hillside and only the stair-step
        noise goes. Each column keeps its own surface block, so grass stays
        grass and sand stays sand, and a raised column is built up from
        what lies under its surface where that is natural ground
        (:func:`~heaton_voxel.craft.blockinfo.subsoil_of`: sand under sand,
        dirt under grass) and from dirt where it is anything else -- the
        cave air of a hollow, an ore, a fluid.

        Four kinds of column are surveyed but never moved, and never feed the
        average either -- their heights are not terrain:

        * anything inside a ``protect`` rectangle (``(x0, z0, x1, z1)``), which
          is how you keep a build out of it. A castle tower reads as ground
          fifty blocks up; averaged in, it would heave the whole field
        * water and ice sheets -- every column the :meth:`probe` finds wet,
          kelp and seagrass at the waterline and a frozen pond included --
          so lakes keep their level and shores are not dragged under. A
          column this session shaped is wet where the block at its height
          holds a fluid or is ice -- the pond a felled branch hung over
        * trees, which keep their footing rather than being left afloat
        * registered pads and reservations (:meth:`reserve`), on the same
          reasoning as :meth:`road`

        ``margin`` widens the surveyed area beyond the rectangle for averaging
        only, so the smoothing blends into the ground outside instead of
        stepping at the boundary. Returns a report of what moved, with
        ``"absent"`` counting the columns that have no ground to survey by
        why (:attr:`~heaton_voxel.craft.errors.NoSurface.reason`): not
        generated, partly generated, or empty. A chunk that cannot be read
        is not absent ground and raises.
        """
        if clearance < 0:
            raise NBTError(f"clearance is the headroom cleared over each "
                           f"moved column, so it cannot be negative; got "
                           f"{clearance}")
        (x0, z0), (x1, z1) = corner1, corner2
        x0, x1 = sorted((x0, x1))
        z0, z1 = sorted((z0, z1))
        keep_out = ([tuple(r) for r in protect] + list(self._pads.values())
                    + list(self._reserved.values()))

        def held(x: int, z: int) -> bool:
            return any(a <= x <= c and b <= z <= d for a, b, c, d in keep_out)

        # Survey first, move second: smoothing must read the terrain as it
        # was, or each column is averaged against its already-moved neighbors
        # and the pass creeps downhill.
        height: dict[tuple[int, int], int] = {}
        surface: dict[tuple[int, int], str] = {}
        movable: set[tuple[int, int]] = set()
        absent: dict[str, int] = {}
        for z in range(z0 - margin, z1 + margin + 1):
            for x in range(x0 - margin, x1 + margin + 1):
                shaped = self._ground.get((x, z))
                if shaped is None:
                    try:
                        found = self.probe(x, z)
                    except NoSurface as missing:
                        absent[missing.reason] = absent.get(missing.reason, 0) + 1
                        continue
                    g, wet = found.waterline, found.wet
                else:
                    g, wet = shaped, None
                block = self.save.get_block(x, g, z, dimension=self.dimension)
                if block is None:
                    continue
                name = block.name
                if wet is None:
                    wet = holds_fluid(name, block.properties) or name in _ICE
                if wet or is_tree(name) or held(x, z):
                    continue          # surveyed as absent, not as ground
                height[(x, z)] = g
                surface[(x, z)] = str(block)
                if x0 <= x <= x1 and z0 <= z <= z1:
                    movable.add((x, z))

        moved = raised = lowered = 0
        # Planned whole, then written: a column reads only what is under its
        # own surface, which no other column writes.
        ops: list[tuple] = []
        # A sweep that writes needs a total order: the order cells are first
        # written is the order of the section palette, and so the chunk's
        # bytes. Sorting by (z, x) keeps identical input encoding identically.
        for x, z in sorted(movable, key=lambda c: (c[1], c[0])):
            g = height[(x, z)]
            total = count = 0
            for dz in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    n = height.get((x + dx, z + dz))
                    if n is not None:
                        total += n
                        count += 1
            if count < 2:
                continue
            target = total / count
            shift = int(round(target - g))
            if shift == 0:
                continue
            shift = max(-max_shift, min(max_shift, shift))
            ops += self._move_column(x, z, g, g + shift, surface[(x, z)],
                                     clearance)
            if shift > 0:
                raised += 1
            else:
                lowered += 1
            moved += 1
        self._commit(ops, what="smooth_terrain")

        return {"surveyed": len(height), "movable": len(movable),
                "moved": moved, "raised": raised, "lowered": lowered,
                "held": len(keep_out), "absent": absent}

    def blend(self, columns, *, run: int = 1,
              clearance: int = 4) -> "BlendResult":
        """Ease the ground over ``columns``, ``(x, z)``, into what stands
        round them, so a street or a plaza meets the land with no cliff.

        Every column round the set, and every one in it that cannot move, is
        a height to meet: the ground rises or falls from each at most a block
        in ``run`` columns, a king's step counting one, and a column already
        within that stays exactly where it is. So the land keeps its shape,
        and only what is too steep for what was built beside it moves. It
        moves as :meth:`smooth_terrain` moves a column, its surface block on
        top and what lay under it filling, so sand stays sand.

        Water, ice and what somebody built do not move, and are heights to
        meet; a column with no ground is left out; a column beside water is
        not cut under the waterline, which would let it in; and a column with
        something built in the way of its move stays put and is counted
        ``held``, since this is tidying land and nobody asked for that one
        cell. Heights that no slope can join, a street and a plaza too close
        for the gap between them, meet halfway and are counted ``steep``.

        ``clearance`` is rows of air over a moved column. Refused before
        anything is written: a chunk that is not there, what the game will
        not keep, and water a cut would let out. Returns a
        :class:`BlendResult`.
        """
        self._alive()
        if isinstance(run, bool) or not isinstance(run, int) or run < 1:
            raise NBTError(f"run is the columns to a block, one at the least, "
                           f"got {run!r}")
        cells = {(int(x), int(z)) for x, z in columns}
        height: dict = {}
        surface: dict = {}
        meet: dict = {}
        shore: dict = {}

        def read(column):
            """``(y, surface or None)``: None for what does not move, and
            ``None`` for no ground."""
            try:
                y, wet = self._stand(column)
            except NoSurface:
                return None
            if wet is not None:
                return wet.waterline, None
            found = self._peek((column[0], y, column[1]))
            if found is None or not _takes(found):
                return y, None
            return y, str(found)

        for column in sorted(cells, key=lambda c: (c[1], c[0])):
            found = read(column)
            if found is None:
                continue
            y, block = found
            if block is None:
                meet[column] = y
            else:
                height[column] = y
                surface[column] = block
        for x, z in sorted(height, key=lambda c: (c[1], c[0])):
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    side = (x + dx, z + dz)
                    if side in cells or side in meet:
                        continue
                    found = read(side)
                    if found is not None:
                        meet[side] = found[0]
        for (x, z), y in sorted(meet.items()):
            found = self._peek((x, y, z))
            if found is not None and (holds_water(found.name, found.properties)
                                      or found.name in _ICE):
                for side in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
                    if side in height:
                        shore[side] = max(shore.get(side, y), y)

        under = _slopes(meet, height, run, 1)
        over = _slopes(meet, height, run, -1)
        result = BlendResult(len(cells))
        moves: dict = {}
        for column in sorted(height, key=lambda c: (c[1], c[0])):
            g = height[column]
            high = None if column not in under else under[column] // run
            low = None if column not in over else -(over[column] // run)
            new = g
            if high is not None and low is not None and low > high:
                new = (low + high) // 2
                result.steep += 1
            elif high is not None and new > high:
                new = high
            elif low is not None and new < low:
                new = low
            new = max(new, min(g, shore.get(column, new)))
            if new == g:
                result.left += 1
                continue
            moved = self._move_column(column[0], column[1], g, new,
                                      surface[column], clearance)
            try:
                self._refuse_built(moved, what="blend")
            except Refused:
                result.held += 1
                continue
            moves[column] = (moved, new > g)
        ops = self._blend_safe(moves, result)
        result.raised += sum(rises for _, rises in moves.values())
        result.lowered += sum(not rises for _, rises in moves.values())
        self._commit(ops, what="blend")
        return result

    def _blend_safe(self, moves, result) -> list:
        """The ops of ``moves``, less any that would let water out or leave
        something the game will not keep: water under the ground beside a cut,
        or sand over a cave, shows on no surface, so what opens one is found
        by trying, and that column stays put, counted ``held``. What staying
        put does not mend, or a chunk not there, is refused."""
        while True:
            ops = [op for moved, _ in moves.values() for op in moved]
            self._reach(ops, what="blend")
            try:
                self._contain_plan(ops, what="blend", seal=None)
                self._audit_plan(ops, what="blend")
            except Refused as exc:
                opened = set()
                for leak in exc.faults:
                    x, _, z = leak.cell
                    opened |= {(x + dx, z + dz) for dx in (-1, 0, 1)
                               for dz in (-1, 0, 1)
                               if (x + dx, z + dz) in moves}
                if not opened:
                    raise
                for column in opened:
                    del moves[column]
                result.held += len(opened)
                continue
            return ops

    def _move_column(self, x: int, z: int, g: int, new: int, surface,
                     clearance: int) -> list[tuple]:
        """The ops that move the ground of column ``(x, z)`` from ``g`` to
        ``new``, another height, with ``surface`` -- the block its ground
        was, so grass stays grass -- on top: raised, the column is built up
        from ``g`` with what lies under the old ground; lowered, it is cut
        away from ``new`` up to ``g``. Either way ``surface`` is set at
        ``new``, ``clearance`` blocks of air are cleared over it and the
        ground is recorded there, as :meth:`set_ground` records it --
        planned in that order, for :meth:`_commit` to write. Only the block
        under the old ground is read. :meth:`smooth_terrain` moves every
        column it moves by this.
        """
        # Whatever lies under the surface is what the column is made of;
        # filling with dirt under sand leaves a dirt scar on a beach. But
        # only ground is copied: the cave air of a hollow under the
        # surface, an ore, a fluid or anything built would put a void, a
        # vein or a spring into the hill, so the column is raised with
        # dirt instead.
        below = self.save.get_block(x, g - 1, z, dimension=self.dimension)
        filler = (subsoil_of(below.name) if below is not None else None) or DIRT
        if new > g:
            ops = [("fill", (x, g, z), (x, new - 1, z), filler)]
        else:
            ops = [("fill", (x, new + 1, z), (x, g, z), AIR)]
        ops.append(("set", (x, new, z), surface))
        if clearance:
            ops.append(("fill", (x, new + 1, z), (x, new + clearance, z), AIR))
        ops.append(("ground", (x, z), new))
        return ops

    # -- roads ---------------------------------------------------------------

    def road(self, points, *, width: int = 3, mix=FROM_PALETTE,
             lanterns: bool = False,
             anchors: dict | None = None, through=(), over_water=None,
             allow_dam: bool = False, rule=None, seal=None,
             overwrite: bool = False) -> RoadResult:
        """Lay a road along ``(x, z)`` waypoints, smoothed to one-block steps.

        The centerline is :func:`~heaton_voxel.craft.geometry.line` through
        the waypoints, so anything laid along the same waypoints follows
        the road cell for cell. The swath (``width`` across, rounded up to
        odd) is a square that wide stamped on each centerline cell, and
        follows the terrain smoothed by
        :func:`~heaton_voxel.craft.geometry.smoothed_profile` -- the ground
        along the centerline averaged over
        :data:`~heaton_voxel.craft.geometry.ROAD_WINDOW` cells either side
        and clamped to one-block steps -- cut and filled per column,
        surfaced from ``mix``: a list of ``(block, weight)`` pairs, drawn
        from :attr:`rng`, a :class:`~heaton_voxel.craft.style.Mix`, picked
        at each surface cell, or one block (a
        :class:`~heaton_voxel.craft.style.Material`'s, for one), laid all
        along. Left out, or ``None``, it is the Builder's :attr:`palette`'s
        ``road`` -- :data:`ROAD_MIX` by default, the same list -- and an
        empty list is refused, an ``NBTError``, rather than quietly laid as
        that default.
        ``anchors`` pins centerline cells to exact heights where the road
        meets a terrace: ``{(x, z): y}``, pinning every visit the
        centerline makes to that cell, while an anchor off the line pins
        nothing. The smoothing only lowers, so a low anchor pulls the road
        down to it a block per cell, and a high one is left a step above
        its neighbors rather than ramped up to. An anchor pins the
        profile, one height per centerline cell, and each swath column is
        cut to the first stretch whose square reaches it -- so on a road
        wider than 1, where the stretch before every centerline cell but
        the first already reaches it, the anchored cell's own column
        stands at an earlier stretch's height, and the anchor's height is
        laid on the columns its stretch reaches first: ``width // 2``
        cells further along a straight road. A height that is not an int
        (a bool, or a float even when it is whole) is refused, naming its
        cell, before anything is read.

        Each waypoint is a pair of ints, and anything else is refused,
        naming it, before anything is read or written, as
        :func:`~heaton_voxel.craft.geometry.line` refuses it: a bool, and a
        float even when it is whole, since rounding one that is not would
        end the road somewhere it was not asked to go. A road whose
        waypoints are all one cell has no length and is refused too.

        ``lanterns=True`` was removed, and is a ``TypeError`` naming
        :meth:`line_lamps`, raised before anything else about the call is
        checked, read or drawn: light a road with :meth:`line_lamps`, given
        the road's :class:`RoadResult` ``points`` and ``width``, which
        stands the posts the caller names wherever the game's flood needs
        them, beside the swath and out of every pad and reservation. What
        it stood -- a
        cobblestone-wall-and-lantern post every twelve cells -- was one
        build's look placed by a rule that does not survive a network: the
        post stood a block off the swath's +x, +z corner, so the next road
        laid beside it carved it away, and the road's own swath could take
        it: a road heading toward +x and +z at once paved over it, one
        heading toward -x and -z stood it on its paving, and a bend could
        turn the road back across it. Every build turned it off and set its
        own posts once its roads were down, and ``lanterns=False``, the
        default, is still taken, so those calls read as they always did.

        A road whose swath enters a registered pad or a reservation
        (:meth:`reserve`) is refused, naming every one it enters -- a lane
        through a building is a channel under its roof, and top-down
        renders will not show it. List their names in ``through`` for
        deliberate crossings.

        A keep-out lasts a session, so a road also reads every cell it would
        cut, fill, surface or clear, and refuses one holding what somebody
        built (:meth:`_refuse_built`), every one named, unless
        ``overwrite=True``. A road may cross a path this session laid.

        The whole road is planned before a block of it is written, and a
        plan reaching a chunk that is not generated or not finished is
        refused whole (:meth:`_commit`). Each column's surface is drawn
        from a list ``mix`` as it is written, in the order the columns are,
        so a refused road draws nothing from :attr:`rng` -- and an earlier
        call that draws more or less from it than it did last time
        resurfaces this road all the way along. A
        :class:`~heaton_voxel.craft.style.Mix` draws nothing: each surface
        cell is its pick there, whatever was built before.

        A road across water or ice is refused before anything is written,
        as :meth:`level_pad` refuses a pad, naming every wet column of its
        swath: pass ``over_water="bridge"`` for a causeway on the water's
        surface, or ``"fill"`` to fill it up from the bed (see
        :meth:`_build_base`).

        What the ground calls for at each cell of the centerline is judged
        by ``rule``, a :class:`RoadRule` -- ``RoadRule()``, the road
        example's thresholds, when it is None -- and reported, not built: a
        cut where the ground stands over the road, a tunnel where it stands
        ``tunnel_from`` (7) or more over it, which the road cuts open to the
        sky all the same, and a bridge over water or where more than
        ``shallow_fill`` (3) blocks of fill would stand under the road's
        bed. A fill that deep under any dry column the road refuses, before
        anything is written or drawn: an embankment that deep across a
        valley is a dam, and a bridge belongs there -- and on a side-hill,
        where ground over the road beside it makes the rule call it a cut
        or a tunnel, the fill on its low side is a dam all the same, and
        its fault says it is a side-hill. One
        :class:`~heaton_voxel.craft.errors.Refused` names every such
        stretch of the centerline, how deep it would stand and at which
        column; ``allow_dam=True`` builds the embankment, and rerouting or
        ``anchors=`` keeps the road off it. A fill over water, which
        ``over_water`` already decides, is not counted as an embankment,
        but water a road fills is a dam of its own two ways, each refused
        until ``allow_dam=True``. A fill that starves the flowing water it
        fed is refused first, on its own, as the ground is read
        (:meth:`_build_base`) -- and so, once the road is planned, is a
        causeway laid at the waterline or under it, whose surface takes the
        water it stands in. And a fill that cuts a body of water in two,
        the water either side of it joined through it today and nowhere
        else once it is filled -- a causeway across a canal, where a jetty
        into a lake leaves the lake joined round its end -- is refused with
        the embankments, in their one ``Refused``, after them
        (:meth:`_split_faults`; water followed into a chunk that is not
        there, or past the check's bound, is refused as water that could
        not be followed far enough to say).

        A road cut below the water beside it -- its channel down past a
        pond's waterline, a causeway laid under the surface -- would let
        the water in the first time anything near it updates: it is refused,
        before anything is written or drawn, naming every cell the water
        would enter (:meth:`_contain_plan`), unless ``seal=`` names a full
        block to wall each one's water off with where it stands. The seals
        are written after the road, refused in a keep-out not named in
        ``through``, and held to the same starve check; lava touching water
        and water running into a chunk that is not there no seal mends. A
        surface drawn from a list is read for this as dirt, which water
        enters no more than any whole block, or as the first of its entries
        water can enter. The road is not held to the survival audit
        (:meth:`_audit_plan`), since a list surface has no block to audit
        until it is drawn: a causeway laid at the waterline or a block over
        it stands on the water, nothing filled under it, so a surface that
        falls -- the gravel in :data:`ROAD_MIX` and :data:`LANE_MIX`, sand
        -- drops through the water at the game's next update there, which
        :meth:`finish` reports (and ``finish(strict=True)`` refuses) after
        the road is written. Bridge with a mix of blocks that stand, or
        fill.

        Returns a :class:`RoadResult` -- the waypoints and ``width`` to
        follow the road with, its centerline, the profile it rides, one
        height per cell (a high anchor's step among them), the swath it
        paved, the height each column was cut to, and the kind of ground
        under it, cell by cell and as stretches. The swath is the session's
        path once it is written, and :meth:`scatter` plants nothing on it.
        """
        if lanterns:
            raise TypeError(
                "road(lanterns=True) was removed: light a road with "
                "Builder.line_lamps(result.points, width=result.width, "
                "post=..., lamp=..., light_level=...), result being what "
                "road() returned, which stands the posts the light needs "
                "beside the swath")
        _over_water(over_water)
        if rule is None:
            rule = RoadRule()
        elif not isinstance(rule, RoadRule):
            raise NBTError(f"a road's rule= is a RoadRule, got {rule!r}")
        mix = self._surface(mix, "road", what="road")
        # A list is drawn from the rng as each column is written; a Mix is
        # picked at each column's surface cell, and one block is laid as it
        # is.
        surfacing = _Mix(mix) if isinstance(mix, (list, tuple)) else mix
        half = width // 2
        # The centerline is geometry's line, the one rule a line through
        # waypoints is walked by. Every waypoint is held to that rule, in
        # its words, before anything else is asked of it, so a malformed
        # one is named rather than coerced; a road with no length is then
        # refused in the road's own words, before the line is walked.
        try:
            waypoints = [geometry._pair(point, "a waypoint") for point in points]
        except TypeError:
            raise NBTError(f"a road takes a sequence of (x, z) waypoints, "
                           f"got {points!r}") from None
        if len(set(waypoints)) < 2:
            raise NBTError("a road needs at least two distinct waypoints")
        line = geometry.line(waypoints)
        # An anchor pins every visit the centerline makes to its cell, and
        # one off the line pins nothing. A height that is not an int is
        # named by its cell before anything is read.
        anchors = anchors or {}
        pins: dict[int, int] = {}
        for i, cell in enumerate(line):
            if cell in anchors:
                y = anchors[cell]
                if isinstance(y, bool) or not isinstance(y, int):
                    raise NBTError(f"a road's anchor is an int height, got "
                                   f"{y!r} at {cell}")
                pins[i] = y

        # The swath in the order it is paved: along the centerline, then
        # across it, each column once -- so the first column a refusal names
        # is the first the road would have cut.
        swath = _swath(line, half)
        self._keep_out(swath, through=through, what="road would carve through")

        # The blocks before the ground, as the plan's own check takes them
        # (_commit): a mix entry the game lacks is that refusal, not a
        # column's missing ground -- and it is checked by name, drawing
        # nothing.
        refuse_many(seal)
        self._vet_names((surfacing, DIRT, seal), what="road")
        self._seal_state(seal, what="road")
        # The surface follows the ground's mean over ROAD_WINDOW cells either
        # side, clamped to one-block steps: geometry's one profile rule.
        heights = [self.ground(x, z) for x, z in line]
        smooth = geometry.smoothed_profile(
            heights, window=geometry.ROAD_WINDOW, anchors=pins)

        # Each swath column is cut once, to the height of the first stretch
        # of road that reaches it -- and its ground read, water refused or
        # bridged or filled, before any of it is written.
        tops = {cell: smooth[i] for cell, i in swath.items()}
        wet: dict = {}
        base = self._build_base(tops, over_water, "road", wet=wet,
                                allow_dam=allow_dam)
        # The water the road takes out: every cell of it a filled column
        # stood over its bed, and under a causeway laid below the
        # waterline, which cuts the water over its surface to air.
        removed: list[tuple[int, int, int]] = []
        bridged_under = False
        for cell, i in swath.items():
            found = wet.get(cell)
            if found is None:
                continue
            x, z = cell
            if over_water == "fill":
                removed.extend((x, y, z) for y in
                               range(base[cell] + 1, found.waterline + 1))
            elif smooth[i] <= found.waterline:
                bridged_under = True
                removed.extend((x, y, z) for y in
                               range(smooth[i], found.waterline + 1))

        # Planned whole, then written: each swath column is cut once and
        # reads only its own ground, so the plan reads what the
        # column-by-column write did. Its surface is drawn from the mix as
        # it is written, in the same order the draws always came: stretch
        # by stretch along the centerline, each stretch's new columns.
        stretches: dict[int, list[tuple[int, int]]] = {}
        for cell, i in swath.items():
            stretches.setdefault(i, []).append(cell)

        # What the ground calls for under each centerline cell, judged by
        # the columns that cell's stretch cuts: the highest of them, the
        # lowest dry one, and whether any is water.
        stations = []
        for i, y in enumerate(smooth):
            own = stretches.get(i)
            if not own:
                stations.append(None)
                continue
            dry = [base[cell] for cell in own if cell not in wet]
            stations.append((y, max(base[cell] for cell in own),
                             min(dry) if dry else None,
                             len(dry) < len(own)))
        kinds = rule.kinds(stations)
        if not allow_dam:
            self._refuse_dams(line, smooth, stretches, base, wet, rule,
                              kinds, split=self._split_faults(removed))

        # A column bridged stands on the waterline, and the fill under its
        # surface starts a block over it. Laid a block over the water there
        # is nothing to fill: the fill's corners, upside down, would sort
        # (as every fill's do) into the waterline and put dirt in the water
        # the causeway stands on -- taken out unannounced, past the starve
        # and split checks. On dry ground that same box writes dirt into
        # the ground under the surface, as it always has.
        bridged = set(wet) if over_water == "bridge" else set()
        ops: list[tuple] = []
        for i in range(len(line)):
            y = smooth[i]
            for x, z in stretches.get(i, ()):
                g = base[(x, z)]
                if g > y:
                    ops.append(("fill", (x, y + 1, z), (x, g + 1, z), AIR))
                elif g < y and not (g + 1 == y and (x, z) in bridged):
                    ops.append(("fill", (x, g + 1, z), (x, y - 1, z), DIRT))
                ops.append(("set", (x, y, z), surfacing))
                ops.append(("fill", (x, y + 1, z), (x, y + 4, z), AIR))
                ops.append(("ground", (x, z), y))
        # The water: a cut that opens a bank to the air beside it lets the
        # water in, refused or sealed (_contain_plan). A surface drawn from
        # a list as it is written has no block to read yet, and stands in
        # as the first of its entries water can enter, or as dirt, which
        # water enters no more than any whole block -- so a list of whole
        # blocks, every road mix the library has, is read exactly.
        # The refusal _commit would give, before the water is read over
        # chunks that are not there or past the world.
        self._reach(ops, what="road")
        self._refuse_built(ops, what="road", overwrite=overwrite,
                           paved=self._paved)
        stand_in = _water_stand_in(surfacing)
        seals = self._contain_plan(
            [op[:2] + (stand_in,) if op[0] == "set" and isinstance(op[2], _Mix)
             else op for op in ops], what="road", seal=seal)
        if seals:
            self._keep_out([(x, z) for _, (x, _, z), _ in seals],
                           through=through, what="road's seals would stand in")
        if (seals or bridged_under) and not allow_dam:
            self._starve(sorted(set(removed) | {cell for _, cell, _ in seals}),
                         call="road", allow_dam=allow_dam)
        self._commit(ops + seals, what="road")
        self._paved.update(swath)
        return RoadResult(waypoints, width, line, smooth, swath, kinds,
                          rule.stretches(kinds))

    @staticmethod
    def _refuse_dams(line, smooth, stretches, base, wet, rule,
                     kinds, split=()) -> None:
        """Refuse a road whose fill would stand deeper than ``rule``'s
        ``shallow_fill`` under its bed anywhere on dry ground: every
        stretch of the centerline whose cells cut such a column, in order,
        a fault each naming how deep it stands and where it is deepest.
        ``stretches`` maps a centerline index to the columns its stretch
        cuts, ``base`` each column's ground and ``wet`` the columns over
        water, which ``over_water`` has already decided.

        ``kinds`` are what ``rule`` called each cell. A deep fill under a
        bridge is a bridge's to span; under a cut or a tunnel it is a
        side-hill -- ground over the road on one side of it and falling
        away on the other, the rule asking about the hill first -- and its
        fault says so, since the fill on the low side is a dam all the
        same and no bridge was called for.

        ``split`` is the faults of a fill that cuts a body of water in
        two (:meth:`_split_faults`), refused in the same one
        :class:`~heaton_voxel.craft.errors.Refused`, after the stretches."""
        deep: dict[int, tuple[int, tuple[int, int]]] = {}
        for i, y in enumerate(smooth):
            for cell in stretches.get(i, ()):
                if cell in wet or not rule.deep(y, base[cell]):
                    continue
                depth = y - 1 - base[cell]
                if i not in deep or depth > deep[i][0]:
                    deep[i] = (depth, cell)
        if not deep and not split:
            return
        spans: list[list[int]] = []
        for i in sorted(deep):
            if spans and spans[-1][-1] == i - 1:
                spans[-1].append(i)
            else:
                spans.append([i])
        faults = []
        for span in spans:
            depth, cell = max((deep[i] for i in span), key=lambda d: d[0])
            first, last = line[span[0]], line[span[-1]]
            where = (f"at {first}" if first == last
                     else f"from {first} to {last}")
            called = []
            for i in span:
                if kinds[i] != BRIDGE and kinds[i] not in called:
                    called.append(kinds[i])
            if called:
                why = (f"a side-hill, ground over the road beside it making "
                       f"it {' and '.join(f'a {kind}' for kind in called)}, "
                       f"and the fill on its low side a dam all the same")
            else:
                why = "a bridge belongs there"
            faults.append(f"the stretch {where} would stand on an embankment "
                          f"{depth} blocks deep at {cell}, more than "
                          f"shallow_fill={rule.shallow_fill}: {why}")
        faults += split
        noun = "dams" if split else "stretches"
        count = (f"{len(faults)} {noun}, first: {faults[0]}"
                 if len(faults) > 1 else faults[0])
        raise Refused(f"road would dam the ground it crosses -- {count}. Pass "
                      f"allow_dam=True to fill it all the same, or keep the "
                      f"road off it by rerouting or with anchors=. Nothing was "
                      f"written", faults=faults)

    def fence_rect(self, x0, z0, x1, z1, *, wood="oak", gate=None,
                   y: int | None = None) -> None:
        """Fence a rectangle, following the terrain; ``gate=(x, z, facing)``
        swaps one post for a gate. Connections are settled by finish().

        ``y`` is the floor the fence stands on instead -- a deck, a
        terrace, a roof -- with every post and the gate at ``y + 1``, the
        convention :meth:`pave` and :meth:`level_pad` keep."""
        x0, x1 = sorted((x0, x1))
        z0, z1 = sorted((z0, z1))
        # The blocks before the ground, as the plan's own check takes them
        # (_commit): a wood the game has no fence of is that refusal, not
        # a column's missing ground.
        self._vet_names((f"minecraft:{wood}_fence",
                         f"minecraft:{wood}_fence_gate[facing={gate[2]}]"
                         if gate else None), what="fence_rect")

        def post(x: int, z: int) -> int:
            return (self.ground(x, z) if y is None else int(y)) + 1

        # Planned whole, then written: a fence moves no ground.
        ops: list[tuple] = []
        for x in range(x0, x1 + 1):
            for z in (z0, z1):
                ops.append(("set", (x, post(x, z), z),
                            f"minecraft:{wood}_fence"))
        for z in range(z0 + 1, z1):
            for x in (x0, x1):
                ops.append(("set", (x, post(x, z), z),
                            f"minecraft:{wood}_fence"))
        if gate:
            gx, gz, facing = gate
            ops.append(("set", (gx, post(gx, gz), gz),
                        f"minecraft:{wood}_fence_gate[facing={facing}]"))
        self._commit(ops, what="fence_rect")

    def ornamental_tree(self, x, z, *, kind="oak", leaves=None,
                        y: int | None = None) -> None:
        """A small hand-built tree whose leaves are ``persistent=true`` --
        planted ones decay when a builder trims them; these never do.

        ``kind`` is a wood: ``oak``, ``cherry``, ``pale_oak``... The two
        nether woods grow stems and wart blocks rather than logs and
        leaves. ``leaves=`` names the canopy where the wood's own name does
        not (poplar's come in colors). Anything the game has no block for
        is refused rather than written. The canopy fills only the cells
        that hold nothing -- cave air is nothing -- so it grows round
        whatever already stands there. A canopy reaching a chunk that is
        not generated, or past the top of the world, is refused whole
        rather than planted with that side missing. ``y`` is the floor the
        tree stands on, its trunk rising from ``y + 1`` -- a planter on a
        terrace -- in place of the ground.
        """
        trunk_block, leaf = _tree_blocks(kind, leaves)
        # The trunk's height is drawn while the tree is planned, so the
        # blocks are checked first, and a tree the plan's own check refuses
        # -- a canopy reaching missing ground, or a worksite's protected
        # zone -- puts the draw back: refused, the rng is where it was.
        self._vet_names((trunk_block, leaf), what="ornamental_tree")
        y = (self.ground(x, z) if y is None else int(y)) + 1
        drawn = self.rng.getstate()
        trunk = 4 + self.rng.randrange(2)
        ops = self._tree_ops(x, y, z, trunk_block, leaf, trunk,
                             read=partial(self.save.get_block,
                                          dimension=self.dimension))
        try:
            self._commit(ops, what="ornamental_tree")
        except Refused:
            self.rng.setstate(drawn)
            raise

    @staticmethod
    def _tree_ops(x: int, y: int, z: int, trunk_block, leaf, trunk: int, *,
                  read) -> list[tuple]:
        """The plan of an ornamental tree -- its ops, for :meth:`_commit`
        -- whose trunk of ``trunk_block`` rises ``trunk`` blocks from
        ``(x, y, z)``, the cell over its ground, under a canopy of ``leaf``
        (:func:`_tree_blocks` names both). The trunk comes first, from the
        foot up, then the canopy, layer by layer from the bottom: a
        five-by-five with its corners cut round each of the trunk's top two
        blocks, and two three-by-threes over it, written into every cell
        ``read(x, y, z)`` finds empty -- any of the game's airs -- or
        cannot read at all, so the tree grows round whatever already stands
        there. ``read`` is the world, or the world with the plantings
        planned before this one over it. A pure function of its arguments:
        nothing is drawn and nothing written.
        """
        # Planned whole, then written: no canopy cell is a trunk cell or
        # another canopy cell, so each reads as it did before any of the
        # tree was in. A cell with nothing to read is planned all the same,
        # so the check refuses the tree rather than plant it lopsided.
        ops: list[tuple] = []
        for i in range(trunk):
            ops.append(("set", (x, y + i, z), trunk_block))
        for dy in range(trunk - 2, trunk + 2):
            radius = 2 if dy < trunk else 1
            for dx in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    if abs(dx) + abs(dz) <= radius + 1 and (dx or dz or dy >= trunk):
                        cur = read(x + dx, y + dy, z + dz)
                        if cur is None or _empty(cur):
                            ops.append(("set", (x + dx, y + dy, z + dz), leaf))
        return ops
