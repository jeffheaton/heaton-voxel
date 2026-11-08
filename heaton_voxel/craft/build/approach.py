"""Approaches: steps or a ramp up the ground, to a pad, a door or a road.

:meth:`ApproachMixin.approach` lays the run between two places a walker
must get between -- a plaza and the bank above it, a road and a doorstep
-- as one straight run along x or z, every row of it level across its
width, cut and filled out of the ground to the profile that moves the
least earth, and walked both ways **without a jump** before anything is
written.

**A whole-block step is a jump.** The park's two gates
(``tools/park/build_park.py``, ``approach``) were made walkable by lifting
or cutting each column on its own until no step was more than a block --
which is a cutting nobody has to climb in a top-down render, and a jump at
every step in the game: a block's top is a whole block up, and a walker
rises :data:`~heaton_voxel.craft.survey.AUTO_STEP`, 0.6 of one, without
jumping. A stair or a slab is half a block, so an approach climbs by
stairs (``kind="steps"``) or by bottom slabs and blocks in turn
(``kind="ramp"``), and its round trip is walked with
``allow_jump=False``. Being cut per column, the park's south gate is
jagged across its width as well; an approach's row is one level from side
to side.

**The profile is a choice made by earthwork.** Each row's level is chosen
together with every other's: never down toward the high end, never up by
more than the kind's step in a row, the last row flush with the high end
-- a tread one below a full-block end is a whole block's rise onto it, a
jump, which is :class:`~heaton_voxel.craft.build.StraightStair`'s "last
tread flush with the exit floor" -- and the first row held level with the
low end when something stands three over it, a door's lintel: a walker
stepping down onto the low end from a row a block higher has their head in
that third cell (``HEAD_ROOM``). Of the profiles left, the one that cuts
and fills the fewest blocks is laid, and of those the one that rises
latest -- the lowest, row by row, a total order -- so the same ground lays
the same approach.

**An overhang is not the ground.** The column reader stands a column on
the top of what stops a fall, and under a ledge, a tunnel's roof or a
cave's ceiling that is the top of the overhang, not where a walker under it
goes. A row cut through a bank is cut open to the sky, as a pad is; a row
with a hollow between it and that top is cut only to its head room, and
the overhang left standing -- a run into a tunnel cuts nothing over the
bore -- and every column's earthwork is counted from the cells the cut and
the fill would really move, so a run under a ledge stays on the ground
under it rather than climbing toward the ledge.

Part of :mod:`heaton_voxel.craft.build`.
"""

from __future__ import annotations

from ...block_defaults import BLOCK_DEFAULTS
from ...blocks import BlockState
from ...nbt import NBTError
from .. import geometry
from .._plan import refuse_many
from ..block_facts import facts, holds_fluid
from ..blockinfo import _current, is_vegetation
from ..errors import Refused
from ..style import Material, Mix
from ..survey import _LAVA, reachable, StepSpace
from ._common import (_empty, _is_whole, _passable, _Slot, _STAIR_STATE,
                      _takes, AIR, HEAD_ROOM)
from .site import _over_water, _wet_words


#: What an approach climbs by: stairs, or bottom slabs and blocks in turn.
KINDS = ("steps", "ramp")

#: The slab a ramp lays where it rises half a block, as one exact state.
_SLAB_STATE = {"type": "bottom", "waterlogged": "false"}

#: The cells lava runs to from a cell: its four sides and the one under
#: it, as offsets from the lava -- so the lava beside a cell or over it.
_LAVA_REACH = ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0))


def _cover(block) -> bool:
    """Whether ``block`` is dry ground cover -- a flower, grass, the snow,
    roots -- which a walker passes and a cut takes with what it stands
    on."""
    return (block is not None and not _empty(block)
            and is_vegetation(_current(block.name))
            and not holds_fluid(block.name, block.properties))


def _hollow(block) -> bool:
    """Whether ``block`` holds nothing a column stands on: any air, or dry
    ground cover. None, past the world, is not."""
    return block is not None and (_empty(block) or _cover(block))


def _lower_half(block) -> bool:
    """Whether ``block`` is the lower half of a tall plant, whose upper
    half goes with it."""
    return block.properties.get(
        "half", BLOCK_DEFAULTS.get(block.name, {}).get("half")) == "lower"


def _is_lava(block) -> bool:
    return block is not None and _current(block.name) == _LAVA


def _lava_enters(block) -> bool:
    """Whether lava runs into a cell holding ``block``: any air, or a block
    the fluids wash away (``block_facts``' ``enters``) that is no
    container -- no container takes lava."""
    if block is None or _is_lava(block):
        return False
    if _empty(block):
        return True
    info = facts(_current(block.name), block.properties)
    return info.enters and not info.container


class ApproachResult:
    """What :meth:`ApproachMixin.approach` laid.

    ``start`` and ``end`` are the cells stood on at each end, ``(x, y,
    z)``, neither written; ``heading`` the way the approach climbs, which
    every stair faces -- from its low end to its high one, and from
    ``start`` to ``end`` where the two stand level; ``kind`` ``"steps"``
    or ``"ramp"``. ``profile`` is one ``(level, form)`` per row, in
    ``start`` to ``end`` order: the y of the row's surface and what it is,
    ``"stairs"``, ``"slab"`` or ``"block"``. ``columns`` is every column
    of the run, row by row from ``start`` and across each in ascending x
    or z, the order it was written in.

    ``treads``, ``slabs`` and ``blocks`` count the surface cells of each
    form; ``filled`` the cells filled under the surface, from the ground
    or the bed up; ``cut`` the cells that held something and were cut to
    air, the head room's among them; and ``sealed`` the seals written
    where water would otherwise have got out (``seal=``).
    """

    __slots__ = ("start", "end", "heading", "kind", "profile", "columns",
                 "treads", "slabs", "blocks", "filled", "cut", "sealed")

    def __init__(self, start, end, heading, kind, profile, columns):
        self.start = start
        self.end = end
        self.heading = heading
        self.kind = kind
        self.profile = profile
        self.columns = columns
        self.treads = self.slabs = self.blocks = 0
        self.filled = self.cut = self.sealed = 0

    def __repr__(self) -> str:
        parts = [f"{len(self.profile)} rows up {self.heading}"]
        for word in ("treads", "slabs", "blocks", "filled", "cut", "sealed"):
            count = getattr(self, word)
            if count:
                parts.append(f"{count} {word}")
        return (f"<ApproachResult {self.kind} {self.start} to {self.end}: "
                f"{', '.join(parts)}>")


def _end(value, word: str):
    """``(x, y, z)`` of an approach's end, ``y`` None for a column to stand
    on the ground of; an ``NBTError`` for anything else."""
    if isinstance(value, (tuple, list)) and len(value) == 3:
        if all(_is_whole(v) for v in value):
            return tuple(value)
    elif isinstance(value, (tuple, list)) and len(value) == 2:
        x, z = geometry._pair(value, f"an approach's {word}")
        return (x, None, z)
    raise NBTError(
        f"an approach's {word} is the block stood on there, (x, y, z), or a "
        f"column (x, z) to stand on its ground -- a pad's edge cell or a "
        f"doorstep, in whole block coordinates -- got {value!r}")


def _solve(costs, low, end, *, ramp: bool, floors, lintel: bool):
    """The profile, as one unit per row from the low end up, or None where
    there is none.

    A unit is a level for steps and a half-block top for a ramp (a block
    at level ``l`` tops out at ``2l + 2``, a bottom slab at ``2l + 1``).
    ``low`` is the low end's and ``end`` the high end's. Each row is the
    one before it or one unit more, never under its ``floors`` entry (a
    waterline it stands on, in units), the first row the low end's own
    where ``lintel``; the last row is flush with the high end, for steps,
    or one unit under it at most, for a ramp. ``costs[i](u)`` is what row
    ``i`` at ``u`` cuts and fills. Of every profile, the cheapest; of
    those, the one lowest at the first row where any differ -- a backward
    pass of least costs to go, then a forward one taking the lowest unit
    at each row that keeps the least."""
    n = len(costs)
    span = end - low + 1
    best: list[list] = [[None] * span for _ in range(n)]
    for i in range(n - 1, -1, -1):
        for k in range(span):
            u = low + k
            if u < floors[i]:
                continue
            if i == n - 1:
                if not (end - u in (0, 1) if ramp else u == end):
                    continue
                rest = 0
            else:
                ahead = [best[i + 1][j] for j in (k, k + 1)
                         if j < span and best[i + 1][j] is not None]
                if not ahead:
                    continue
                rest = min(ahead)
            best[i][k] = costs[i](u) + rest
    profile = []
    prev = low
    for i in range(n):
        options = (prev,) if i == 0 and lintel else (prev, prev + 1)
        pick = None
        for u in options:
            k = u - low
            if 0 <= k < span and best[i][k] is not None and (
                    pick is None or best[i][k] < best[i][pick - low]):
                pick = u
        if pick is None:
            return None
        profile.append(pick)
        prev = pick
    return profile


class ApproachMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods:
    steps and ramps on the ground.

    Builder inherits them; nothing here is used on its own.
    """

    def approach(self, start, end, *, kind: str, material, width: int = 1,
                 fill=None, over_water=None, seal=None, through=(),
                 overwrite: bool = False, walk: bool = True) -> ApproachResult:
        """Lay steps or a ramp on the ground from ``start`` to ``end``,
        walked both ways without a jump before anything is written.

        ``start`` and ``end`` are each the block stood on at that end,
        ``(x, y, z)`` -- for a door, the block under its lower half; for a
        pad, its edge cell -- or a column ``(x, z)``, standing on its
        :meth:`ground`, the height this session gave it or its probe's,
        and refused where that is water or an ice sheet: a waterline is not
        somewhere to stand. Neither end is written, so a road's surface, a
        pad's edge and a doorstep stay as they are. The ends lie on one
        line along x or z, with at least one row between them: the run is
        the cells between, ``width`` columns across each row -- a whole
        number, rounded up to odd and centered, as :meth:`road`'s is.

        ``kind`` is required, a choice: ``"steps"``, every row that rises
        a stair of the material's (``_STAIR_STATE``, facing up the
        approach) and every row that does not its block, or ``"ramp"``,
        half a block a row -- its bottom slab, then its block, and so on.
        ``material`` is required: a
        :class:`~heaton_voxel.craft.style.Material`, or any block of the
        family, as ``Material`` names one; a form it lacks is the
        Material's :class:`~heaton_voxel.craft.errors.IllegalBlock`, and a
        :class:`~heaton_voxel.craft.style.Mix` a ``TypeError``, before
        anything is read. ``fill`` is what goes under the surface where
        the ground is lower -- a block, a Material (its block) or a Mix,
        picked at each cell -- the material's block when left out, as the
        park's gates were built up in stone bricks.

        **The profile** (see the module): each row one level from side to
        side, monotone from the low end up, rising at most a level a row
        as steps and half a block as a ramp, the last row flush with the
        high end -- a ramp's may be its slab, half a block under it -- and
        the first held level with the low end where that end has
        something a body cannot pass three over it with room under it, a
        door's lintel. Of those, the one that cuts and fills least -- the
        cells it changes between holding something and holding nothing,
        which over open ground is ``sum |level - ground|`` -- and of those
        the one that rises latest. Where there is none, it is refused,
        saying why: too steep (``n`` rows climb at most ``n`` blocks as
        steps, and ``(n + 1) // 2`` as a ramp), the lintel, or a waterline
        above what the ends let a row stand at.

        **The ground.** Every column is read (:meth:`_stand`), and a
        column with none raises
        :class:`~heaton_voxel.craft.errors.NoSurface`. Water and ice are
        refused, every wet column named, unless ``over_water`` says what
        to do (:meth:`_build_base`): ``"bridge"`` stands the row on the
        waterline, ``"fill"`` fills it from the bed -- refused where it
        would starve flowing water, pointing at :meth:`grade`'s drain --
        and either keeps every row at its waterline or over it. Each
        column is filled from its ground up to its surface, the surface
        laid, and cut to air over it: a row cut into a bank, solid up to
        its ground, open to the sky, up to the cell over the ground -- a
        flower, the snow, a tall plant's upper half with its lower -- and
        every row to :data:`HEAD_ROOM` over its surface at least. A column
        with a hollow between the row and the ground the column reader
        finds -- a ledge, a tunnel's roof, a cave's ceiling, whose top the
        reader stands on -- is cut only to the head room, with the cover
        standing on the top of that cut, and the overhang over it is left;
        a surface laid into the hollow is filled under, down to what holds
        it.

        **Before anything is written**, in this order, each refusing the
        whole call: the arguments, the materials and every block by name;
        the keep-outs -- a pad or a reservation the run crosses, unless
        named in ``through`` (:meth:`reserve`); the ground; the profile;
        the water and ice under the run (:meth:`_build_base`); the chunks
        and heights the plan reaches (:meth:`_reach`); lava -- a plan cell
        that holds it, or one the plan opens, air or anything the fluids
        wash away where it held what kept out the lava beside it or over
        it, which no flag passes and no seal mends, since the water check
        follows water; what the plan writes over -- air, ground cover,
        natural ground, an ore or a tree it takes, and the water and ice in
        the columns ``over_water`` bridges or fills
        (:func:`~heaton_voxel.craft.build._common._takes`); water or ice in
        a column whose ground is dry, a spring in the bank, which no flag
        speaks for; and anything else, a fence, an eave, a wall, a path,
        refused, each cell named, unless ``overwrite=True``; the cells a
        :meth:`flight` or another approach
        this session built claims -- treads, the wood under them, the head
        room over them -- which no ``overwrite`` passes; the water -- a cut
        that would open a pond's bank to the air is refused, naming each
        :class:`~heaton_voxel.craft.survey.Leak`, unless ``seal=`` names a
        full block to wall the water off with where it stands
        (:meth:`_contain_plan`; the seals refused inside a keep-out not in
        ``through``, and held to the starve check, as a causeway at the
        waterline is); the survival audit -- sand left over the cut, a
        torch on a bank it takes (:meth:`_audit_plan`); and, unless
        ``walk=False``, the round trip: the plan walked from ``start``
        with :func:`~heaton_voxel.craft.survey.reachable` and
        ``allow_jump=False`` over the run's own columns and its ends and
        nothing beside them, refused unless ``end`` and every surface cell
        are reached and walked home from -- so both ends must be there to
        stand on, and the way back down is the approach's own.

        Then the plan is written (:meth:`_commit`), row by row from
        ``start``, across each in ascending x or z, each column bottom up,
        the seals after; the session learns each column's ground at its
        surface; its surface cells and the :data:`HEAD_ROOM` over them are
        claimed, so a later flight or approach, a shell, a piece of
        furniture or a planting is refused there; and its columns are a
        path the session paved (:meth:`scatter` plants nothing on them).
        Nothing is drawn from :attr:`rng`. Returns an
        :class:`ApproachResult`.
        """
        self._alive()
        what = "approach"
        if kind not in KINDS:
            raise NBTError(f"an approach's kind is 'steps' (stairs) or 'ramp' "
                           f"(slabs and blocks, half a block a row), not "
                           f"{kind!r}")
        ramp = kind == "ramp"
        if not _is_whole(width) or width < 1:
            raise NBTError(f"an approach's width is a whole number of "
                           f"columns, 1 or more, got {width!r}")
        half = width // 2
        _over_water(over_water)
        first, last = _end(start, "start"), _end(end, "end")
        dx, dz = last[0] - first[0], last[2] - first[2]
        if dx and dz:
            raise NBTError(
                f"an approach runs straight along x or z, and "
                f"{_where(first)} to {_where(last)} is on neither: lay two "
                f"approaches, to a landing between them")
        if abs(dx) + abs(dz) < 2:
            how = "are one column" if not dx and not dz else "are side by side"
            raise NBTError(
                f"an approach needs at least one row between its ends, and "
                f"{_where(first)} and {_where(last)} {how}")
        if isinstance(material, Mix):
            raise TypeError(
                f"an approach's material is one family, whose stairs, slab "
                f"and block it lays, and {material!r} is a Mix, many blocks "
                f"picked by position: name the family, and pass a Mix as "
                f"fill= for what goes under the surface")
        if not isinstance(material, Material):
            material = Material(material)
        if fill is None:
            fill = material
        elif isinstance(fill, (list, tuple)):
            raise TypeError(
                f"an approach's fill is a block, a Material or a Mix, picked "
                f"at each cell, not {fill!r}: a list drawn from the "
                f"session's rng is road's old way")
        refuse_many(seal)
        surface, block, fill = self._materials(
            what, _Slot("material", material, None,
                        form="slab" if ramp else "stairs"),
            _Slot("material", material, None),
            _Slot("fill", fill, None, mixes=True))
        if ramp:
            rise = BlockState(_state_id(surface), _SLAB_STATE)
        else:
            # Facing is spelled once the heading is known; any of the four
            # is legal where one is.
            rise = BlockState(_state_id(surface),
                              dict(_STAIR_STATE, facing="north"))
        self._vet_names((rise, block, fill, seal), what=what)
        self._seal_state(seal, what=what)

        # The run: the cells between the ends, each row width columns
        # across, ascending in x or z.
        line = geometry.line([(first[0], first[2]), (last[0], last[2])])
        across = (0, 1) if dx else (1, 0)
        rows = [[(cx + w * across[0], cz + w * across[1])
                 for w in range(-half, half + 1)] for cx, cz in line[1:-1]]
        run = [column for row in rows for column in row]
        self._keep_out(run, through=through, what="approach would cross")

        # The ground: the ends', then every column's, before any of it is
        # planned.
        first = self._end_cell(first, "start")
        last = self._end_cell(last, "end")
        stood = {column: self._stand(column) for column in run}
        rising = first[1] <= last[1]
        low, high = (first, last) if rising else (last, first)
        L, H = low[1], high[1]
        travel = geometry.heading_of(dx, dz)
        heading = travel if rising else geometry.OPPOSITE[travel]
        lintel = self._lintel(low)

        # How each dry column is laid at every level a row can take, and
        # what that moves: the earthwork the profile is chosen by.
        dry = {column: self._dry_column(column[0], column[1], y, L, H)
               for column, (y, probe) in stood.items() if probe is None}

        # The profile, solved from the low end up.
        n = len(rows)
        order = list(range(n)) if rising else list(range(n - 1, -1, -1))
        unit = (lambda y: 2 * y + 2) if ramp else (lambda y: y)
        level = (lambda u: (u - 1) // 2) if ramp else (lambda u: u)
        costs, floors, wettest = [], [], []
        for r in order:
            laid, waterlines = [], []
            waterline = None
            for column in rows[r]:
                y, probe = stood[column]
                if probe is None:
                    laid.append(dry[column])
                    continue
                waterlines.append(probe.waterline)
                if over_water is not None and (
                        waterline is None or probe.waterline > waterline[0]):
                    waterline = (probe.waterline, column)
            costs.append(lambda u, ds=tuple(laid), ws=tuple(waterlines):
                         sum(d[level(u)][2] for d in ds)
                         + sum(abs(level(u) - w) for w in ws))
            floors.append(unit(L) if waterline is None else
                          max(unit(L), 2 * waterline[0] + 1 if ramp
                              else waterline[0]))
            wettest.append(waterline)
        units = _solve(costs, unit(L), unit(H), ramp=ramp, floors=floors,
                       lintel=lintel is not None)
        if units is None:
            self._unclimbable(n, L, H, ramp, lintel, low, floors, wettest,
                              unit, level)
        forms = []
        prev = unit(L)
        for u in units:
            forms.append(("block" if u % 2 == 0 else "slab") if ramp
                         else ("stairs" if u > prev else "block"))
            prev = u
        # Back into start-to-end order.
        profile = [None] * n
        for i, r in enumerate(order):
            profile[r] = (level(units[i]), forms[i])
        tops = {column: profile[r][0]
                for r, row in enumerate(rows) for column in row}
        wet: dict = {}
        base = self._build_base(tops, over_water, what, wet=wet)

        states = {"block": block}
        if ramp:
            states["slab"] = rise
        else:
            states["stairs"] = rise.with_properties(facing=heading)
        result = ApproachResult(first, last, heading, kind, tuple(profile),
                                tuple(run))
        counted = {"stairs": "treads", "slab": "slabs", "block": "blocks"}
        ops: list[tuple] = []
        # The water a row bridged at its waterline takes, as a causeway's.
        removed: list[tuple[int, int, int]] = []
        surfaces: list[tuple[int, int, int]] = []
        for r, row in enumerate(rows):
            s, form = profile[r]
            for x, z in row:
                probe = wet.get((x, z))
                if probe is None:
                    under, top, _ = dry[(x, z)][s]
                else:
                    # The bed a fill stands on, or the waterline a bridge
                    # does; over it, the head room.
                    g = base[(x, z)]
                    under = g + 1
                    top = max(g + 1, s + HEAD_ROOM)
                    if over_water == "bridge" and s <= probe.waterline:
                        removed.extend((x, y, z)
                                       for y in range(s, probe.waterline + 1))
                if under is not None and under <= s - 1:
                    ops.append(("fill", (x, under, z), (x, s - 1, z), fill))
                    result.filled += s - under
                ops.append(("set", (x, s, z), states[form]))
                surfaces.append((x, s, z))
                ops.append(("fill", (x, s + 1, z), (x, top, z), AIR))
                ops.append(("ground", (x, z), s))
            setattr(result, counted[form],
                    getattr(result, counted[form]) + len(row))

        # The refusal _commit would give, before anything is read there.
        self._reach(ops, what=what)
        plan = self._plan_cells(ops, what=what)
        world = {cell: self._peek(cell) for cell in plan}
        self._lava_refused(plan, world)
        # Water is taken only in the columns over_water spoke for: those
        # whose ground is water or an ice sheet. Water in the cut of a dry
        # column -- a spring in the bank -- is no flag's.
        water, taken = [], []
        for cell, found in world.items():
            if found is None or found == plan[cell]:
                continue
            if _takes(found, wet=(cell[0], cell[2]) in wet):
                continue
            words = (f"{cell} holds {found}, where the approach writes "
                     f"{plan[cell]}")
            if _takes(found, wet=True):
                water.append(words)
            elif not overwrite:
                taken.append(words)
        if water:
            count = (f"{len(water)} cells, first: {water[0]}"
                     if len(water) > 1 else water[0])
            raise Refused(
                f"approach would cut into water or ice in a column whose "
                f"ground is dry -- {count}. over_water= speaks for the water "
                f"a row stands over, and no flag for water in the bank: keep "
                f"the approach clear of it, or take it out first. Nothing was "
                f"written", faults=water)
        if taken:
            count = (f"{len(taken)} cells, first: {taken[0]}"
                     if len(taken) > 1 else taken[0])
            raise Refused(
                f"approach would write over what somebody built -- "
                f"{count}. Move the approach, or pass overwrite=True to "
                f"take it all the same. Nothing was written",
                faults=taken)
        result.cut = sum(1 for cell, found in world.items()
                         if found is not None and not _empty(found)
                         and _empty(plan[cell]))
        claims = {(x, y, z) for x, s, z in surfaces
                  for y in range(s, s + HEAD_ROOM + 1)}
        clash = sorted((claims | plan.keys()) & self._claimed)
        if clash:
            who = self._claimant(clash[0])
            count = (f"{len(clash)} cells, first {clash[0]}" if len(clash) > 1
                     else f"{clash[0]}")
            raise Refused(
                f"approach would be laid into what {who} this session built "
                f"claims -- {count}: a stair in another's treads or head "
                f"room is one you climb and cannot come back down. Route the "
                f"approach clear of it. Nothing was written",
                faults=clash)

        seals = self._contain_plan(ops, what=what, seal=seal)
        if seals:
            self._keep_out([(x, z) for _, (x, _, z), _ in seals],
                           through=through,
                           what="approach's seals would stand in")
        if seals or removed:
            # A seal takes the water it stands in, as a causeway laid at
            # the waterline does: neither may starve a flow it fed.
            self._starve(sorted(set(removed) | {c for _, c, _ in seals}),
                         call=what, allow_dam=None)
        self._audit_plan(ops + seals, what=what)
        if walk:
            self._walk_approach(ops + seals, first, last, run, surfaces)
        self._commit(ops + seals, what=what)
        result.sealed = len(seals)
        self._claimed |= claims
        self._approached |= claims
        self._paved.update(run)
        return result

    def _end_cell(self, end, word: str) -> tuple[int, int, int]:
        """``end`` as the cell stood on: as given, or for a column, the
        :meth:`ground` it stands on -- refused where that is water or an
        ice sheet."""
        x, y, z = end
        if y is not None:
            return end
        ground, wet = self._stand((x, z))
        if wet is not None:
            raise Refused(
                f"approach's {word} at ({x}, {z}) is {_wet_words(wet)}: a "
                f"waterline is not somewhere to stand. Name the cell stood "
                f"on as (x, y, z), or end the approach on dry ground. "
                f"Nothing was written", faults=[wet])
        return (x, ground, z)

    def _lintel(self, low):
        """The cell three over the low end where something a body cannot
        pass stands with room under it -- a door's lintel -- or None: a
        walker stepping down onto the low end from a row a level higher has
        their head in it, so the first row is held level with the end."""
        x, y, z = low
        cells = [(x, y + k, z) for k in (1, 2, 3)]
        found = [self._peek(cell) for cell in cells]
        if all(block is None or _passable(block) for block in found[:2]) \
                and found[2] is not None and not _passable(found[2]):
            return cells[2], found[2]
        return None

    def _dry_column(self, x: int, z: int, g: int, low: int,
                    high: int) -> dict:
        """How a dry column of the run is laid at each level ``low`` to
        ``high``: ``{s: (under, top, moved)}`` -- the fill under the
        surface from ``under`` up to the cell under it (None for none),
        the cut to air from the cell over it up to ``top``, and how many
        cells the two change between holding something and holding
        nothing, every solid cell cut and every hollow one filled, the
        surface's own among them. Over open ground that is ``|s - g|``.

        ``g`` is the column's ground as :meth:`_stand` finds it: the top of
        what stops a fall, which over an overhang -- a ledge, a tunnel's
        roof, a cave's ceiling -- is the overhang's top, not where a walker
        under it goes. A row at or over ``g`` is filled from the cell over
        it. A row under it with solid ground all the way up -- a bank -- is
        cut open to the sky, up to the cell over the ground, a flower, the
        snow or a tall plant's upper half with its lower. A row with a
        hollow (any air, or dry ground cover) between it and ``g`` is under
        an overhang, and is cut only to its :data:`HEAD_ROOM`, with the
        cover standing on the top of that cut, and what stands over it is
        left; a surface laid into the hollow is filled under, down to what
        holds it."""
        blocks: dict[int, object] = {}

        def at(y: int):
            if y not in blocks:
                blocks[y] = self._peek((x, y, z))
            return blocks[y]

        over = at(g + 1)
        # The open cut's top: the cell over the ground, whatever it holds,
        # and a tall plant's upper half with its lower.
        open_top = g + 2 if _cover(over) and _lower_half(over) else g + 1
        laid = {}
        for s in range(low, high + 1):
            if s >= g:
                laid[s] = (g + 1, max(open_top, s + HEAD_ROOM), s - g)
                continue
            moved = 0
            under = None
            if _hollow(at(s)):
                floor = s - 1
                while _hollow(at(floor)):
                    floor -= 1
                under = floor + 1
                moved += s - floor
            if not any(_hollow(at(y)) for y in range(s + 1, g + 1)):
                laid[s] = (under, max(open_top, s + HEAD_ROOM),
                           moved + g - s)
                continue
            top = s + HEAD_ROOM
            if not _hollow(at(top)) and _cover(at(top + 1)):
                top += 2 if _lower_half(at(top + 1)) else 1
            moved += sum(1 for y in range(s + 1, top + 1)
                         if not _hollow(at(y)))
            laid[s] = (under, top, moved)
        return laid

    def _lava_refused(self, plan, world, *, what: str = "approach",
                      noun: str = "the approach") -> None:
        """Refuse a plan that writes over lava, or opens a cell to it: one
        it leaves as air or anything the fluids wash away, beside lava or
        under it, that held what kept the lava out. Lava runs into such a
        cell as water does, and it is no water: no ``over_water`` speaks
        for it and no seal walls it off,
        which :func:`~heaton_voxel.craft.survey.contained`, following
        water, never asks. A cell lava enters already, beside lava that is
        there already, is the world's, as ``contained`` leaves a leak
        standing. ``what`` opens the refusal and ``noun`` names the writer
        inside it -- an approach's, by default, or :meth:`extrude`'s, whose
        cuts open cells as an approach's do."""
        faults = []
        for cell in sorted(plan):
            found = world[cell]
            if _is_lava(found):
                if not _is_lava(plan[cell]):
                    faults.append(f"{cell} holds {found}, where {noun} "
                                  f"writes {plan[cell]}")
                continue
            if not _lava_enters(plan[cell]):
                continue
            x, y, z = cell
            for dx, dy, dz in _LAVA_REACH:
                near = (x + dx, y + dy, z + dz)
                after = plan[near] if near in plan else self._peek(near)
                if not _is_lava(after):
                    continue
                before = world[near] if near in plan else after
                if _lava_enters(found) and _is_lava(before):
                    continue
                where = "over it" if dy else "beside it"
                faults.append(f"{cell}, which {noun} opens, has "
                              f"{after} {where} at {near}")
                break
        if faults:
            count = (f"{len(faults)} cells, first: {faults[0]}"
                     if len(faults) > 1 else faults[0])
            raise Refused(
                f"{what} would be laid in lava or open onto it -- {count}. "
                f"Lava runs into a cell cut beside it or under it, as water "
                f"does, and no seal walls it off: keep {noun} clear of "
                f"the lava. Nothing was written", faults=faults)

    def _unclimbable(self, n, L, H, ramp, lintel, low, floors, wettest,
                     unit, level) -> None:
        """Refuse an approach with no profile, saying why: too steep, the
        low end's lintel, or a waterline above what the ends allow."""
        rise = H - L
        reach = (n + 1) // 2 if ramp else n
        how = "as a ramp, half a block a row" if ramp else "as steps"
        if rise > reach:
            more = (2 * rise - 1 - n) if ramp else rise - n
            advice = (", or lay steps, which climb a block a row"
                      if ramp and rise <= n else "")
            raise Refused(
                f"an approach of {_count(n, 'row')} climbs at most "
                f"{_count(reach, 'block')} {how}; this one climbs {rise}, "
                f"from y={L} to y={H}. Move an end to give it "
                f"{_count(more, 'more row')}{advice}. Nothing was written",
                faults=[f"{_count(n, 'row')} for a rise of {rise}"])
        held = n // 2 if ramp else n - 1
        if lintel is not None and rise > held:
            cell, block = lintel
            raise Refused(
                f"the approach's low end {low} has {block} at {cell}, three "
                f"over where you stand: a walker stepping down onto it from "
                f"a row a level higher has their head there, so its first "
                f"row is held level with it, and the {_count(n - 1, 'row')} "
                f"after it climb at most {_count(held, 'block')} {how}, not "
                f"{rise}. Move an end to give it one more row, or open the "
                f"head room over the low end. Nothing was written",
                faults=[cell])
        offset = 0 if lintel is None else 1
        for i, floor in enumerate(floors):
            most = min(unit(H), unit(L) + i + 1 - offset)
            if floor > most:
                waterline, column = wettest[i]
                raise Refused(
                    f"the approach would stand on the water at {column}, "
                    f"its waterline at y={waterline}, and the ends let that "
                    f"row stand no higher than y={level(most)} -- under the "
                    f"water. Keep it off the water, or move an end. Nothing "
                    f"was written", faults=[column])
        raise NBTError("the approach found no profile and no reason why")

    def _walk_approach(self, ops, start, end, run, surfaces) -> None:
        """Refuse the plan unless it walks both ways without a jump: from
        ``start``, :func:`~heaton_voxel.craft.survey.reachable` with
        ``allow_jump=False`` over the plan laid on the world, ``end`` and
        every surface cell reached and walked home from. The box is the run
        and its ends and nothing beside them, from two under the lowest
        level to the head room over the highest, so the way back down is
        the approach's own: on open ground a walker turned back by a tread
        could step off the side of the run and drop to the ground beside
        it, and a walk that let them would pass a one-way stair."""
        overlay = self._plan_cells(ops, what="approach")
        space = StepSpace(self.save, overlay=overlay, dimension=self.dimension)
        xs = [c[0] for c in run] + [start[0], end[0]]
        zs = [c[1] for c in run] + [start[2], end[2]]
        ys = [cell[1] for cell in surfaces] + [start[1], end[1]]
        lo = (min(xs), min(ys) - 2, min(zs))
        hi = (max(xs), max(ys) + HEAD_ROOM + 1, max(zs))
        try:
            reach = reachable(space, start, lo, hi, allow_jump=False)
        except NBTError as exc:
            raise Refused(f"the approach cannot be walked from its start: "
                          f"{exc}. Nothing was written",
                          faults=[start]) from None
        lost = [cell for cell in (end, *surfaces)
                if cell not in reach.reached or cell in reach.stranded]
        if lost:
            raise Refused(
                f"the planned approach does not walk both ways between "
                f"{start} and {end} without a jump: {reach!r}, and "
                f"{len(lost)} of its cells are not walked to and back, first "
                f"{lost[0]}. Nothing was written", faults=lost)


def _count(n: int, noun: str) -> str:
    """``n`` of ``noun``, its last word plural unless there is one."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _where(end) -> str:
    """An end as given, for a refusal."""
    x, y, z = end
    return f"({x}, {z})" if y is None else f"({x}, {y}, {z})"


def _state_id(block) -> str:
    """The id of a form, as :meth:`Builder._materials` resolved it."""
    return block.name if isinstance(block, BlockState) else str(block)
