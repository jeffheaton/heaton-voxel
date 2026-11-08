"""Rail lines as rules.

:class:`Railway`, what holds a rail, and the writer that lays one.

Part of :mod:`heaton_voxel.craft.build`. The writer reads the world it
plans over with the Builder's shared reader.
"""

from __future__ import annotations

from ...blocks import BlockState
from ...nbt import NBTError
from .._plan import read_overlay, refuse_many
from ..block_facts import holds_fluid
from ..blockinfo import _current, falls, FLUIDS, is_block
from ..errors import Refused
from ..geometry import OPPOSITE
from ..survey import _FACING_OF, step_kind, StepSpace
from ._common import _COMPASS, _empty, _state, _state_matches, AIR, HEAD_ROOM


# --------------------------------------------------------------------------
# Railways
# --------------------------------------------------------------------------

#: Every kind of rail, matched exactly. A rail is the one thing a railway
#: will not cut, bury or re-lay without being told: it is somebody's line.
_RAILS = frozenset(("minecraft:rail", "minecraft:powered_rail",
                    "minecraft:detector_rail", "minecraft:activator_rail"))


#: The two ways out of a rail of each shape. An ``ascending_<d>`` rail
#: leaves toward ``d`` one block up and the other way level; the four
#: curves are always level, which is why a corner cannot sit on a slope.
_RAIL_EXITS = {
    "north_south": ("north", "south"), "east_west": ("east", "west"),
    "ascending_north": ("north", "south"), "ascending_south": ("south", "north"),
    "ascending_east": ("east", "west"), "ascending_west": ("west", "east"),
    "north_east": ("north", "east"), "north_west": ("north", "west"),
    "south_east": ("south", "east"), "south_west": ("south", "west"),
}


_OPPOSITE = OPPOSITE


#: Blocks you can stand on that a rail cannot: ``BaseRailBlock.canSurvive``
#: asks for a *rigid* top face -- a full square -- and the game drops a
#: rail from anything else at its next block update. Leaves, fences, walls,
#: panes, path blocks, farmland, chests and a bottom slab all hold a player
#: and none of them holds a rail. Matched exactly or by whole suffix, never
#: by bare substring: ``crimson_stem`` is a log and holds one fine.
_NO_RAIL_SUFFIX = (
    "_fence", "_wall", "_pane", "_bars", "_leaves", "_chest", "_shelf",
    "_cake", "_head", "_skull", "_anvil", "_campfire", "_wall_fan",
    "_lightning_rod", "_golem_statue", "_sensor", "_crop", "_mushroom",
    "_fungus", "_eyeblossom", "_egg", "_dripleaf", "_vines_plant",
    "_portal", "_fire", "_dry_grass", "_chain", "_bud", "_cluster",
    "_copper_lantern",
)


_NO_RAIL = frozenset("""
cactus cactus_flower dirt_path farmland honey_block enchanting_table
end_portal_frame brewing_stand grindstone stonecutter lectern bell conduit
decorated_pot heavy_core frogspawn dried_ghast chorus_plant chorus_flower
cocoa comparator repeater pointed_dripstone sulfur_spike resin_clump
sculk_vein sculk_shrieker spore_blossom leaf_litter pink_petals wildflowers
wheat carrots potatoes beetroots nether_wart melon_stem pumpkin_stem
attached_melon_stem attached_pumpkin_stem golden_dandelion red_shrub
mangrove_propagule big_dripleaf_stem torch fire lava end_gateway
structure_void moving_piston chest cake anvil campfire candle bamboo
end_rod lightning_rod lantern soul_lantern copper_lantern
""".split())


#: Walked through, as :func:`~heaton_voxel.craft.survey.step_kind` sees
#: them, and a full rigid top to a rail all the same.
_RAIL_ON_PASSABLE = frozenset(("scaffolding", "mangrove_roots",
                               "muddy_mangrove_roots"))


def _holds_rail_top(name: str, properties=None) -> bool:
    """Whether a rail can stand on this block -- the game's ``RIGID`` up face.

    Held to the game's own ``isFaceSturdy(UP, RIGID)`` on every one of the
    35,723 block states of 26.3, in both directions, by
    ``tests/test_railway.py`` against ``fixtures/block_shapes.json``. A
    block the tables have never heard of is taken for a full cube, which
    most blocks are -- the same default
    :func:`~heaton_voxel.craft.survey.step_kind` makes. After a game update,
    rerun ``tools/probe_block_shapes.py`` and the test names every block
    that default gets wrong.
    """
    bare = name.removeprefix("minecraft:")
    props = properties or {}
    if bare.endswith("_slab"):
        return props.get("type") in ("top", "double")
    if bare.endswith("_stairs"):
        return props.get("half") == "top"
    if bare.endswith("_trapdoor"):
        return props.get("half") == "top" and props.get("open") != "true"
    if bare == "snow":
        return props.get("layers") == "8"
    if bare in ("piston", "sticky_piston"):
        # An extended piston's top is its arm, except when it pushes down;
        # the arm's own top is a full plate only when it pushes up.
        return props.get("extended") != "true" or props.get("facing") == "down"
    if bare == "piston_head":
        return props.get("facing") == "up"
    if bare in _RAIL_ON_PASSABLE:
        return True
    if bare in _NO_RAIL or bare.endswith(_NO_RAIL_SUFFIX):
        return False
    return step_kind(name, props) == "full"


def _wet(state) -> bool:
    """Whether ``state`` holds water or lava: a block that is fluid
    whatever its state (:data:`~heaton_voxel.craft.blockinfo.FLUIDS`), or
    a waterlogged one, which spills into the air beside it exactly as a
    source does. A ``waterlogged`` left out is the block's default, so a
    bare sea pickle is wet
    (:func:`~heaton_voxel.craft.block_facts.holds_fluid`).

    A name the game has retired is read as the block it became
    (:data:`~heaton_voxel.craft.blockinfo.RENAMED`), as the game reads it
    out of an old chunk, so an old chunk's waterlogged chain is wet. A
    block the game has never heard of, a modded one, is taken at its
    word: a line that missed the water it says it holds would flood."""
    name = _current(state.name)
    if holds_fluid(name, state.properties):
        return True
    return not is_block(name) and state.properties.get("waterlogged") == "true"


def _settled(state, under) -> bool:
    """Whether ``state`` stays where it is: sand over a hole does not.

    What falls is the game's own answer
    (:func:`~heaton_voxel.craft.blockinfo.falls`), and it takes the rail
    with it at the next block update. Of the game's falling blocks only
    sand, gravel and concrete powder ever get this far: no rail can stand
    on an anvil or the dragon egg in the first place."""
    if not falls(state.name):
        return True
    return (under is not None and not _wet(under)
            and step_kind(under.name, under.properties) != "air")


def _toward(a, b) -> tuple[str, int]:
    """The compass direction from cell ``a`` to the cell ``b`` beside it,
    and how many blocks higher ``b`` is."""
    return _FACING_OF[(b[0] - a[0], b[2] - a[2])], b[1] - a[1]


def _rail_meets(shape: str, direction: str, dy: int) -> bool:
    """Whether a rail of ``shape`` runs on toward ``direction`` into a rail
    ``dy`` blocks higher. Climbing toward it is the only way to meet one
    above; a rail that climbs toward a level neighbor runs into its side."""
    exits = _RAIL_EXITS.get(shape)
    if exits is None or direction not in exits:
        return False
    climbs = shape == f"ascending_{direction}"
    return climbs if dy > 0 else not climbs


def _rail_shape(cell, links) -> str:
    """The shape a rail in ``cell`` needs to join the ``(direction, dy)``
    neighbors it has in the line, or a refusal saying why none can."""
    rising = [direction for direction, dy in links if dy > 0]
    ways = [direction for direction, _ in links]
    if len(rising) > 1:
        raise NBTError(
            f"the rail at {cell} would have to climb both ways -- a dip one "
            f"block deep needs a level cell at its bottom; add a waypoint")
    if rising:
        up = rising[0]
        if any(way not in (up, _OPPOSITE[up]) for way in ways):
            raise NBTError(
                f"the rail at {cell} turns and climbs at once, which no rail "
                f"can do -- a curve is always level; move the corner off the "
                f"slope")
        return f"ascending_{up}"
    if len(ways) == 2 and ways[1] != _OPPOSITE[ways[0]]:
        north_south = ways[0] if ways[0] in ("north", "south") else ways[1]
        east_west = ways[1] if north_south == ways[0] else ways[0]
        return f"{north_south}_{east_west}"
    return "north_south" if ways[0] in ("north", "south") else "east_west"


class RailwayResult:
    """What :meth:`Builder.railway` did to the world, cell by cell.

    ``laid`` rails are new, ``boosted`` of them powered. ``already`` held
    exactly the rail the rule wants. ``kept`` held somebody else's rail
    that already runs on into the line -- a detector at a station, a
    booster left unpowered on purpose so the cart stops -- and was left
    alone. ``relaid`` did not, and was laid again under ``overwrite=True``.
    ``removed`` counts, by block, everything the line took out of its way
    that was not air or water: the number to read when a railway has gone
    through somebody's cellar.
    """

    __slots__ = ("cells", "laid", "boosted", "already", "kept", "relaid",
                 "supports", "power", "drained", "sealed", "removed")

    def __init__(self, cells: int):
        self.cells = cells
        self.laid = 0
        self.boosted = 0
        self.already = 0
        self.kept = 0
        self.relaid = 0
        self.supports = 0
        self.power = 0
        self.drained = 0
        self.sealed = 0
        self.removed: dict[str, int] = {}

    def __repr__(self) -> str:
        parts = [f"{self.laid} laid"
                 + (f" ({self.boosted} boosted)" if self.boosted else "")]
        for label, count in (("already right", self.already),
                             ("kept", self.kept), ("relaid", self.relaid),
                             ("supports", self.supports),
                             ("power blocks", self.power),
                             ("drained", self.drained),
                             ("sealed", self.sealed)):
            if count:
                parts.append(f"{count} {label}")
        if self.removed:
            top = sorted(self.removed.items(), key=lambda kv: (-kv[1], kv[0]))
            names = ", ".join(f"{name.removeprefix('minecraft:')} {count}"
                              for name, count in top[:3])
            parts.append(f"removed {sum(self.removed.values())} ({names})")
        return f"<RailwayResult {self.cells} cells: {', '.join(parts)}>"


class Railway:
    """A rail line as a rule: waypoints in, every block of the line out.

    ``points`` are ``(x, y, z)`` waypoints, and ``y`` is the height of the
    **rail** -- the block a cart runs in, not the one under it. Between two
    waypoints the line runs straight along x or along z, and is either level
    or climbs one block per block; anything in between is refused rather
    than guessed, because where a ramp starts is a choice and a rail cannot
    climb by halves. Put a waypoint wherever the gradient changes and at
    every corner. An end waypoint may be a rail that is already there -- the
    end of the line being extended -- and :meth:`Builder.railway` keeps it
    as it is, provided it already runs on into the line.

    From the cells the rule derives everything a vanilla line needs, which
    is more than any picture of one shows:

    * **every shape** -- straight, curved at a corner, ``ascending_<d>``
      toward the higher neighbor. A curve is always level and a rail climbs
      one way only, so a corner on a slope and a one-block dip are refused.
      Shapes are written outright, and stay: a rail re-derives its own
      shape only at a powered junction, so what is written is what a cart
      rides.
    * **boosters** -- a powered rail on a ``power`` block (redstone) on
      every climbing cell, and on the level every ``boost_every`` cells
      (``None`` lays none at all). A powered rail cannot curve, so a booster
      that falls due on a corner waits for the next straight cell. The
      default 26 is the spacing the Stronghold link was laid with, a
      choice and not a rule of the game's, and the ``bed`` and ``power``
      defaults are that line's cobblestone and redstone block (see "Where
      the defaults came from" in :mod:`heaton_voxel.craft.build`).
    * **head room** -- ``clearance`` clear blocks over each support, the
      rail's own cell included, and :data:`HEAD_ROOM` on the low side of
      every change of level. The flat needs two. The low side of a step
      needs three, for the reason a stair tread does: walking down off the
      higher cell, the game moves you sideways before it drops you, and for
      that tick your head is in the third cell. Clearance for two is a line
      you can walk up and cannot walk back down -- the Stronghold link's
      walk check stranded 56 cells that way, and one block cleared at the
      foot of its ramp fixed all of them.

    :meth:`verify` is the half that matters: every reason the line will not
    run, as readable complaints, against a save or against a plan that has
    not been written yet.
    """

    __slots__ = ("points", "cells", "shapes", "boosters", "clearance",
                 "boost_every", "bed", "power", "_boosted")

    def __init__(self, points, *, clearance: int = 2,
                 boost_every: int | None = 26,
                 bed="minecraft:cobblestone",
                 power="minecraft:redstone_block"):
        points = [tuple(int(v) for v in point) for point in points]
        if len(points) < 2:
            raise NBTError("a railway needs at least two waypoints")
        if any(len(point) != 3 for point in points):
            raise NBTError("waypoints are (x, y, z), and the y is the rail's "
                           "own block")
        clearance = int(clearance)
        if clearance < 2:
            raise NBTError(
                f"clearance={clearance}: a rider's head is in the block above "
                f"the rail, so a line needs at least two clear blocks over its "
                f"supports")
        if boost_every is not None and int(boost_every) < 1:
            raise NBTError("boost_every counts level cells between boosters; "
                           "pass None for a line with no boosters at all")
        self.bed = _state(bed)
        self.power = _state(power)
        for role, block in (("bed", self.bed), ("power", self.power)):
            if not _holds_rail_top(block.name, block.properties):
                raise NBTError(f"{role}={block} cannot hold a rail -- its top "
                               f"face is not a full square")

        cells = [points[0]]
        for (ax, ay, az), (bx, by, bz) in zip(points, points[1:]):
            span = f"({ax}, {ay}, {az}) to ({bx}, {by}, {bz})"
            if ax != bx and az != bz:
                raise NBTError(f"{span} is diagonal; a rail runs along x or "
                               f"along z -- put a waypoint at the corner")
            run = abs(bx - ax) + abs(bz - az)
            if run == 0:
                raise NBTError(f"{span} does not move along the ground; a "
                               f"rail cannot climb in place")
            rise = by - ay
            if rise and abs(rise) != run:
                raise NBTError(
                    f"{span} climbs {rise:+d} in {run} blocks; a rail is "
                    f"level or climbs one block per block -- put a waypoint "
                    f"where the ramp starts")
            dx, dz = (bx > ax) - (bx < ax), (bz > az) - (bz < az)
            dy = (rise > 0) - (rise < 0)
            for step in range(1, run + 1):
                cells.append((ax + dx * step, ay + dy * step, az + dz * step))
        seen: dict[tuple[int, int], int] = {}
        for x, y, z in cells:
            if (x, z) in seen:
                raise NBTError(
                    f"the line passes ({x}, {z}) twice, at y={seen[(x, z)]} "
                    f"and y={y}; a line that crosses itself needs a bridge "
                    f"this rule cannot see -- lay it as two railways")
            seen[(x, z)] = y

        shapes = []
        for i, cell in enumerate(cells):
            links = [_toward(cell, cells[j]) for j in (i - 1, i + 1)
                     if 0 <= j < len(cells)]
            shapes.append(_rail_shape(cell, links))

        boosted: set[int] = set()
        if boost_every is not None:
            since = 0
            for i, shape in enumerate(shapes):
                if shape.startswith("ascending_"):
                    boosted.add(i)
                    since = 0
                    continue
                since += 1
                if since >= int(boost_every) and shape in ("north_south",
                                                           "east_west"):
                    boosted.add(i)
                    since = 0

        self.points = tuple(points)
        #: Every rail cell, in order along the line.
        self.cells = tuple(cells)
        #: The ``shape`` of the rail in each cell.
        self.shapes = tuple(shapes)
        #: The cells that carry a powered rail.
        self.boosters = tuple(cells[i] for i in sorted(boosted))
        self.clearance = clearance
        self.boost_every = boost_every
        self._boosted = frozenset(boosted)

    def __len__(self) -> int:
        return len(self.cells)

    # -- the rule ----------------------------------------------------------

    def state(self, i: int) -> BlockState:
        """The rail the rule lays in cell ``i``, properties in name order --
        the order the game writes them, so a line laid here shares palette
        entries with one laid by hand."""
        shape = self.shapes[i]
        if i in self._boosted:
            return BlockState("minecraft:powered_rail", {
                "powered": "true", "shape": shape, "waterlogged": "false"})
        return BlockState("minecraft:rail",
                          {"shape": shape, "waterlogged": "false"})

    def head_room(self, i: int) -> int:
        """Clear blocks cell ``i`` needs over its support, its rail included:
        :data:`HEAD_ROOM` beside a higher neighbor, ``clearance`` elsewhere."""
        y = self.cells[i][1]
        higher = any(self.cells[j][1] > y for j in (i - 1, i + 1)
                     if 0 <= j < len(self.cells))
        return max(self.clearance, HEAD_ROOM) if higher else self.clearance

    def running_space(self, i: int) -> tuple[tuple[int, int, int], ...]:
        """The rail's cell and the clear blocks above it, bottom up."""
        x, y, z = self.cells[i]
        return tuple((x, y + k, z) for k in range(self.head_room(i)))

    def support(self, i: int) -> tuple[int, int, int]:
        """The block under the rail in cell ``i`` -- what you stand on."""
        x, y, z = self.cells[i]
        return (x, y - 1, z)

    def runs_on(self, rail, i: int) -> bool:
        """Whether ``rail``, standing in cell ``i``, already runs on into the
        line toward every neighbor the line has there. An end of the line
        has only one, which is how a rail somebody else laid is kept."""
        shape = rail.properties.get("shape", "")
        return all(_rail_meets(shape, *_toward(self.cells[i], self.cells[j]))
                   for j in (i - 1, i + 1) if 0 <= j < len(self.cells))

    # -- reading -----------------------------------------------------------

    def verify(self, save, *, overlay=None, dimension: str | None = None
               ) -> list[str]:
        """Every reason this line will not run, as readable complaints.

        Read against the save or, with ``overlay`` -- a plan of
        ``{(x, y, z): block}`` -- against that plan laid over it, so a line
        can be checked before a block of it exists. Each check is one a
        render calls finished:

        * every cell holds a rail, and each runs on into the next at the
          right height
        * every rail stands on a rigid top face, and a climbing rail has one
          level with it where it climbs to; the game drops a rail from
          anything else at its next block update -- a bottom slab, a fence,
          leaves, a path block -- and from sand or gravel with nothing under
          it, which falls
        * the running space holds no water, and no water touches it from
          beside or above, where it will run in the first time anything
          near it updates
        * the head room the rule asks for is clear
        * you can walk it both ways, from each support to the next -- the
          check that finds a ramp in a tunnel two blocks high

        The ends are checked against the line only: whatever lies beyond
        them is the line being joined, and its own business.
        """
        plan = read_overlay(overlay)

        def at(cell):
            if cell in plan:
                return plan[cell]
            return save.get_block(cell[0], cell[1], cell[2],
                                  dimension=dimension)

        faults: list[str] = []
        supports = {self.support(i) for i in range(len(self.cells))}
        running: set[tuple[int, int, int]] = set()
        for i, cell in enumerate(self.cells):
            x, y, z = cell
            running.update(self.running_space(i))
            rail = at(cell)
            if rail is None:
                faults.append(f"{cell}: nothing to read -- the chunk is not "
                              f"generated, or y is outside the world")
                continue
            if rail.name not in _RAILS:
                faults.append(f"{cell}: expected a rail, found {rail}")
                continue
            if i + 1 < len(self.cells):
                nxt = self.cells[i + 1]
                other = at(nxt)
                direction, dy = _toward(cell, nxt)
                if (other is not None and other.name in _RAILS and not (
                        _rail_meets(rail.properties.get("shape", ""),
                                    direction, dy)
                        and _rail_meets(other.properties.get("shape", ""),
                                        _OPPOSITE[direction], -dy))):
                    faults.append(f"{cell} -> {nxt}: {rail} and {other} do "
                                  f"not run on into each other")

            under = at((x, y - 1, z))
            if under is None:
                faults.append(f"{(x, y - 1, z)}: nothing to read under the "
                              f"rail")
            elif not _holds_rail_top(under.name, under.properties):
                faults.append(
                    f"{(x, y - 1, z)}: {under} cannot hold the rail above it "
                    f"-- the game drops it at the next block update")
            elif not _settled(under, at((x, y - 2, z))):
                faults.append(
                    f"{(x, y - 1, z)}: {under} has nothing under it, so it "
                    f"falls at the next block update and takes the rail")
            shape = rail.properties.get("shape", "")
            if shape.startswith("ascending_"):
                dx, dz = _COMPASS[shape.removeprefix("ascending_")]
                high = (x + dx, y, z + dz)
                block = at(high)
                # Inside the line this is the next rail's support, which
                # the support check above has already judged.
                if high not in supports and (
                        block is None
                        or not _holds_rail_top(block.name, block.properties)):
                    faults.append(
                        f"{cell}: {rail} climbs toward {high}, which holds "
                        f"{block} -- the game drops a climbing rail with "
                        f"nothing rigid to climb onto")

            for k, space in enumerate(self.running_space(i)):
                block = rail if k == 0 else at(space)
                if block is None:
                    faults.append(f"{space}: nothing to read over the rail")
                elif _wet(block):
                    faults.append(f"{space}: {block} in the running space -- "
                                  f"the line is flooded here")
                elif k and step_kind(block.name, block.properties) != "air":
                    faults.append(
                        f"{space}: {block} is in the way -- the line needs "
                        f"{self.head_room(i)} clear blocks over its support "
                        f"here, the rail's own included")

        for cell in sorted(self._touching(running)):
            block = at(cell)
            if block is not None and _wet(block):
                faults.append(
                    f"{cell}: {block} touches the running space -- drained, "
                    f"the line refills from here the first time anything "
                    f"near it updates")

        faults.extend(self._walk(save, plan, dimension))
        return faults

    @staticmethod
    def _touching(running) -> set[tuple[int, int, int]]:
        """Cells outside the running space that water could run in from:
        beside any of it, and on top of each column of it."""
        out = set()
        for x, y, z in running:
            for dx, dz in _COMPASS.values():
                if (x + dx, y, z + dz) not in running:
                    out.add((x + dx, y, z + dz))
            if (x, y + 1, z) not in running:
                out.add((x, y + 1, z))
        return out

    def _walk(self, save, plan, dimension) -> list[str]:
        """Each support to the next, and back again, as the player walks.

        One pair at a time rather than one search over the line's bounding
        box: a railway is a corridor, and a search that wandered off across
        the countryside would cost a region's worth of lookups to learn
        nothing about the line.
        """
        space = StepSpace(save, overlay=plan, dimension=dimension)
        faults = []
        for i in range(len(self.cells) - 1):
            a, b = self.support(i), self.support(i + 1)
            box = ((min(a[0], b[0]), min(a[1], b[1]) - 2, min(a[2], b[2])),
                   (max(a[0], b[0]), max(a[1], b[1]) + 4, max(a[2], b[2])))
            there = any(cell == b for cell, _ in space.steps(a, box))
            back = any(cell == a for cell, _ in space.steps(b, box))
            if there and back:
                continue
            here, nxt = self.cells[i], self.cells[i + 1]
            way = _toward(here, nxt)[0]
            if not (there or back):
                faults.append(
                    f"{here} <-> {nxt}: you cannot walk between them either "
                    f"way -- something is in the head room, or a support is "
                    f"missing")
            else:
                start, end, going = ((here, nxt, way) if there
                                     else (nxt, here, _OPPOSITE[way]))
                faults.append(
                    f"{start} -> {end}: one way on foot -- you can walk "
                    f"{going} and not back, which is a ceiling one block too "
                    f"low over the lower cell")
        return faults

    def __repr__(self) -> str:
        first, last = self.cells[0], self.cells[-1]
        parts = [f"{len(self.cells)} cells {first} -> {last}"]
        if self.boosters:
            parts.append(f"{len(self.boosters)} boosters")
        parts.append(f"{len(self.points)} waypoints")
        return f"<Railway {', '.join(parts)}>"


class RailwayMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods: rail lines as rules.

    Builder inherits them; nothing here is used on its own.
    """

    # -- railways ------------------------------------------------------------

    def railway(self, line, *, overwrite: bool = False, seal=None,
                through=()) -> RailwayResult:
        """Lay a :class:`Railway`: the rails, the boosters, the bed under
        them and the tunnel they run in.

        A railway is a road in this respect: it goes through the ground
        rather than over it, so whatever stands in its running space is
        cut and whatever under it cannot hold a rail -- air, water, leaves,
        a path block, sand over a hole -- becomes ``line.bed``.
        ``RailwayResult.removed`` lists, by block, everything it took out
        of the way. It refuses, before writing anything, in five places:

        * **a registered pad or reservation** (:meth:`reserve`), as
          :meth:`road` does, anywhere the plan writes -- the seals walling
          water off beside the line included; name it in ``through`` when
          the crossing is meant
        * **a rail it does not own**: a rail in a cell the line needs, that
          does not already run on into the line, is somebody's line --
          ``overwrite=True`` to lay over it. A rail that does run on (the
          end being extended, a detector at a station) is kept as it is
        * **water touching the line**. Draining a tunnel open to a lake
          builds a canal: the water runs back in the first time anything
          near it updates. ``seal=`` names a block to wall each such cell
          off with, and the seal is written along with everything else.
          It is held to the rule every writer's seal is
          (:meth:`~heaton_voxel.craft.build.Builder._seal_state`), before
          anything is read and whether or not any water is found: a block
          whole on every face to water, that water can neither enter nor
          waterlog -- not leaves, a slab, a grate, roots or a chest -- and
          that stays a wall, so not sand, gravel or concrete powder, which
          fall, ice or frosted ice, which melt, nor a sponge, which soaks
          the water up
        * **a plan that does not verify**. The whole plan is checked with
          :meth:`Railway.verify` -- shapes, supports, head room, water,
          and the walk both ways -- before a block of it is written
        * **a plan the world cannot hold**: a cell in a chunk that is not
          generated or not finished terrain, or past the top or bottom of
          the world, refuses the whole plan, as every writer's does
          (:meth:`~heaton_voxel.craft.build.Builder._commit`)

        and then checks the written line again. Run :meth:`finish`
        afterwards like any other build; a railway places nothing
        :meth:`~heaton_voxel.world.MinecraftSave.connect_blocks` derives,
        so ``finish(connect=False)`` leaves older work beside the line
        alone.
        """
        if not isinstance(line, Railway):
            raise NBTError("railway takes a Railway -- make one from its "
                           "waypoints, Railway([(x, y, z), ...])")
        # A seal is one exact block: a Mix or a Material is refused before
        # anything is read, and the rest of the seal rule once its name has
        # been checked with the line's.
        refuse_many(seal)
        # The line first, in line order, so a refusal names the first
        # column of it a keep-out holds; the whole plan, seals and all,
        # once it is known.
        self._keep_out([(x, z) for x, _, z in line.cells], through=through,
                       what="the railway would run through")

        # Every cell the line reads before it knows its plan -- its rails,
        # the bed under them and the space they run in -- is checked as
        # _commit will check the plan, the seal's legality with it: a block
        # the game lacks first, then a chunk with nothing to build into, or
        # a height past the world.
        reads = []
        for i, (x, y, z) in enumerate(line.cells):
            reads.append(("set", (x, y, z), line.state(i)))
            reads.append(("set", (x, y - 1, z),
                          line.power if i in line._boosted else line.bed))
            reads += [("set", space, AIR) for space in line.running_space(i)[1:]]
        self._vet_plan(reads, what="railway", names=(seal,))
        # The one seal rule every writer's seal is held to, before anything
        # is read: whole to water, nothing water enters, and a wall that
        # stays one.
        seal_state = self._seal_state(seal, what="railway")

        read, peek = self._read, self._peek
        result = RailwayResult(len(line))
        plan: dict[tuple[int, int, int], BlockState] = {}
        blocked: list[str] = []
        own = set(line.cells)

        def removed(found) -> None:
            if not _empty(found) and found.name not in FLUIDS:
                result.removed[found.name] = result.removed.get(found.name, 0) + 1

        def take(cell, found, state) -> None:
            """Plan ``state`` over ``found``, unless that costs somebody
            else's rail: the rail itself, or the block it stands on."""
            if not overwrite:
                if found.name in _RAILS:
                    blocked.append(f"{cell} holds {found}, a rail the line "
                                   f"does not own")
                    return
                above = (cell[0], cell[1] + 1, cell[2])
                over = None if above in own else peek(above)
                if (over is not None and over.name in _RAILS
                        and not _holds_rail_top(state.name, state.properties)):
                    blocked.append(f"{cell} carries {over} -- cutting it drops "
                                   f"a rail the line does not own")
                    return
            plan[cell] = state
            if found.name in FLUIDS:
                result.drained += 1
            removed(found)

        air = BlockState(AIR)
        for i, cell in enumerate(line.cells):
            x, y, z = cell
            want = line.state(i)
            found = read(cell)
            ours = True
            if found.name in _RAILS and _state_matches(found, want):
                result.already += 1
            elif found.name in _RAILS and line.runs_on(found, i):
                ours = False
                result.kept += 1
                if _wet(found):
                    plan[cell] = BlockState(found.name, dict(
                        found.properties, waterlogged="false"))
                    result.drained += 1
            elif found.name in _RAILS and overwrite:
                plan[cell] = want
                result.relaid += 1
                if i in line._boosted:
                    result.boosted += 1
            elif found.name in _RAILS:
                blocked.append(f"{cell} holds {found}, which does not run on "
                               f"into the line")
            else:
                take(cell, found, want)
                result.laid += 1
                if i in line._boosted:
                    result.boosted += 1

            under_cell = (x, y - 1, z)
            under = read(under_cell)
            if ours and i in line._boosted:
                if under.name != line.power.name:
                    take(under_cell, under, line.power)
                    result.power += 1
            elif not (_holds_rail_top(under.name, under.properties)
                      and _settled(under, read((x, y - 2, z)))):
                take(under_cell, under, line.bed)
                result.supports += 1

            for space in line.running_space(i)[1:]:
                block = read(space)
                if block.name in FLUIDS:
                    take(space, block, air)
                elif _wet(block) and step_kind(block.name, block.properties) == "air":
                    # A waterlogged sign or lantern: drain it, keep it.
                    plan[space] = BlockState(block.name, dict(
                        block.properties, waterlogged="false"))
                    result.drained += 1
                elif step_kind(block.name, block.properties) != "air":
                    take(space, block, air)
        if blocked:
            raise Refused(
                f"{len(blocked)} cells the railway needs are somebody else's "
                f"rail, first: {blocked[0]}. Route around it, or pass "
                f"overwrite=True to lay over it", faults=blocked)

        running: set[tuple[int, int, int]] = set()
        for i in range(len(line)):
            running.update(line.running_space(i))

        def after(cell):
            if cell in plan:
                return plan[cell]
            return peek(cell)

        leaks = sorted(cell for cell in line._touching(running)
                       if (block := after(cell)) is not None and _wet(block))
        if leaks:
            if seal is None:
                raise Refused(
                    f"{len(leaks)} cells of water or lava touch the line's "
                    f"running space, first {leaks[0]} holding "
                    f"{after(leaks[0])}; drained, the line refills from them "
                    f"the first time anything near it updates. Pass "
                    f"seal='minecraft:cobblestone' to wall them off, or route "
                    f"the line away from the water", faults=leaks)
            for cell in leaks:
                found = after(cell)
                if found.name in _RAILS and not overwrite:
                    blocked.append(f"{cell} holds {found}, a rail the line "
                                   f"does not own")
                    continue
                plan[cell] = seal_state
                result.sealed += 1
                removed(found)
            if blocked:
                raise Refused(
                    f"sealing the line would bury somebody else's rail, "
                    f"first: {blocked[0]}. Pass overwrite=True to seal over it", faults=blocked)

        # Every column the plan writes -- a seal is the one that leaves the
        # line's own -- in the order it would be written.
        self._keep_out([(x, z) for x, _, z in sorted(plan)], through=through,
                       what="the railway would write into")

        faults = line.verify(self.save, overlay=plan, dimension=self.dimension)
        if faults:
            raise Refused(
                f"the planned railway does not hold up -- {len(faults)} "
                f"faults, first: {faults[0]}. Nothing was written", faults=faults)

        # Coordinate order: a sweep that writes needs a total order, since
        # the order states are first written is the order of the palette.
        self._commit([("set", cell, plan[cell]) for cell in sorted(plan)],
                     what="railway")

        faults = line.verify(self.save, dimension=self.dimension)
        if faults:
            raise Refused(
                f"the railway was written but does not verify -- "
                f"{len(faults)} faults, first: {faults[0]}. The plan and the "
                f"world disagree, which is a bug here: do not save", faults=faults)
        return result
