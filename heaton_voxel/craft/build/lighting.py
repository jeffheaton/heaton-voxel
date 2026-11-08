"""Lit floors, hanging lamps and lamp posts.

``pave``, ``light_cells``, ``hang_lamps`` and ``line_lamps``, the lattice
that spaces their lamps, and how the light level each is asked for is read.

Part of :mod:`heaton_voxel.craft.build`. A floor and its lamps, a lamp
and its chain, or a post and its lamp, are planned as one call and handed
to the Builder's ``_commit`` whole. Where the lamps go is decided by the
game's own flood (:func:`~heaton_voxel.craft.light.plan_lights`): the
lattice proposes, the flood checks, and patches wherever light does not
go where the lattice's straight-line arithmetic says it does.
"""

from __future__ import annotations

from ...block_defaults import BLOCK_DEFAULTS
from ...blocks import BlockState
from ...nbt import NBTError
from .. import geometry
from .._plan import refuse_many
from ..block_facts import facts
from ..blockinfo import is_vegetation
from ..errors import NoSurface, Refused
from ..light import (_plan_proposing, _resolve_level, _transient_reason,
                     durable_emission, light_emission, MELT_SNOW)
# The same object as craft.style's, so every old import still finds it.
from ..style import FROM_PALETTE, Mix, PLAZA_MIX  # noqa: F401
from ._common import _empty, _MELTABLE, _Mix, _Slot, _state, AIR
from .site import _over_water, _swath, _wet_words, DIRT
from .vault import _hangs_from, _why_no_ceiling


#: The older name of :data:`~heaton_voxel.craft.light.MELT_SNOW` (12),
#: the same object: what :meth:`Builder.pave` and :meth:`Builder.light_cells`
#: lit to before a light writer had to be told which rule its light is for.
MELT_LIGHT = MELT_SNOW


#: What each of the usual light sources gives off in its default state, from
#: the game's own data (:func:`~heaton_voxel.craft.light.light_emission`).
#: Kept for scripts that look a source up by name; the Builder reads the
#: exact state it is about to write, and credits only the light that state
#: keeps (:func:`~heaton_voxel.craft.light.durable_emission`), so a bare,
#: unlit lamp here is 0 and ``redstone_lamp[lit=true]``, which goes dark
#: at the next block update with nothing to power it, is refused.
LIGHT_SOURCES = {name: light_emission(name) for name in (
    "minecraft:glowstone", "minecraft:sea_lantern", "minecraft:shroomlight",
    "minecraft:redstone_lamp", "minecraft:jack_o_lantern", "minecraft:beacon",
    "minecraft:ochre_froglight", "minecraft:verdant_froglight",
    "minecraft:pearlescent_froglight", "minecraft:crying_obsidian",
    "minecraft:magma_block", "minecraft:lantern", "minecraft:campfire",
    "minecraft:soul_lantern", "minecraft:soul_campfire", "minecraft:torch",
    "minecraft:wall_torch", "minecraft:end_rod", "minecraft:candle",
)}


def _sits_in_a_floor(state: BlockState) -> bool:
    """Whether a light can be set flush into a walking surface: its top is a
    whole sturdy face. A lantern, a torch, a campfire or a sea pickle would
    hang in the paving instead -- plausible-looking wrongness a render does
    not show."""
    return facts(state.name, state.properties).holds("up")


def _hangs_lamp(state: BlockState) -> bool:
    """Whether ``state`` is a lantern, which hangs by its own ``hanging``."""
    return "hanging" in BLOCK_DEFAULTS.get(state.name, ())


def _half_block(state) -> bool:
    """Whether a block's top stands half a block up: a bottom slab."""
    return (state is not None and state.name.endswith("_slab")
            and state.properties.get("type", "bottom") == "bottom")


def _column_blocks(dim, columns, y0: int, y1: int) -> dict:
    """``{(x, z): [block at y0, ..., block at y1]}`` over ``columns``, read a
    section at a time: what ``get_block`` answers cell by cell -- None where
    a chunk is not generated or cannot be read, or a height is outside it --
    for a writer about to look at thousands of cells."""
    found: dict[tuple[int, int], list] = {}
    by_chunk: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for x, z in columns:
        by_chunk.setdefault((x >> 4, z >> 4), []).append((x, z))
    for (chunk_x, chunk_z), cells in by_chunk.items():
        try:
            accessor = dim.blocks(chunk_x, chunk_z)
        except NBTError:  # there but unreadable: nothing to read, as get_block
            accessor = None
        rows = {cell: [] for cell in cells}
        for y in range(y0, y1 + 1):
            section = None
            if accessor is not None and accessor.min_y <= y <= accessor.max_y:
                section = accessor.sections.get(y >> 4)
            container = section.blocks if section is not None else None
            if container is None:
                held = None if section is None else BlockState("minecraft:air")
                for cell in cells:
                    rows[cell].append(held)
                continue
            palette, indices = container.palette, container.indices()
            row = (y & 15) * 256
            for cell in cells:
                rows[cell].append(
                    palette[indices[row + (cell[1] & 15) * 16 + (cell[0] & 15)]])
        found.update(rows)
    return found


def _by_row(cell) -> tuple[int, int]:
    """The order a floor's ``(x, z)`` cells are planned and written in:
    row by row, ``z`` then ``x``."""
    return (cell[1], cell[0])


def _diamond(reach: int):
    """The lattice a floor's lamps are laid on, as a predicate on ``(x, z)``:
    points ``2 * reach`` apart along both diagonals, so the diamonds of
    cells within ``reach`` of them tile the plane, and every cell when
    ``reach`` is 0 or less. Phase-locked to world coordinates, so two
    calls that meet continue one pattern instead of restarting it."""
    step = max(1, 2 * reach)
    if reach <= 0:
        return lambda x, z: True
    return lambda x, z: (x + z) % step == 0 and (x - z) % step == 0


def _lattice(wanted, budget: int, reach: int, lamp_y, pool, lit) -> list:
    """Where a floor's lamps go by straight-line arithmetic: the lamps to
    try first, as ``[(x, z)]`` in the order to try them.

    ``wanted`` maps ``(x, z)`` to the floor level; ``lamp_y(cell)`` is the
    level that cell's lamp would sit at -- the floor itself for a light set
    into paving, the far end of a chain for one hung from a ceiling --
    ``pool`` the cells a lamp may go in, and ``lit`` the cells the world
    already lights, which need none. The :func:`_diamond` lattice at
    ``reach``, in row order; then each cell it leaves short, in row order,
    gets of the lamps that reach it the one reaching the most cells still
    short, ties to the furthest in row order. So a thin run -- a road, a
    colonnade, which a lattice crosses rarely or never -- is lit at the
    full reach, each lamp as far along as it can stand, and the margin a
    wide floor's lattice leaves at its edges is lit along the edge.
    (Taking the furthest lamp alone, as this once did, hung a wide floor's
    edge on a row of lamps one per column, each reaching straight back
    up to the one cell it was for: at ``NO_SPAWNS``, four to five times
    the lamps the floor needs, in a stripe.)
    A lamp reaches a cell when ``|dx| + |dz| + |floor + 1 - lamp y|`` is
    at most ``budget``, which is what light does over **open** ground and
    nowhere else: this is only the proposal, and the flood
    (:func:`~heaton_voxel.craft.light.plan_lights`) decides. On open
    ground the two agree and the proposal is the plan; behind a wall or a
    pier, or where a lamp counted on has since been broken, the flood
    finds the cells it leaves dark and patches them.
    """
    on = _diamond(reach)
    # Every (dx, dz) a lamp can reach, in one fixed order.
    offsets = [(dx, dz) for dx in range(-budget, budget + 1)
               for dz in range(abs(dx) - budget, budget - abs(dx) + 1)]
    short = {cell for cell in wanted if cell not in lit}

    def reached(lamp) -> list:
        """The cells still short that a lamp at ``lamp`` reaches."""
        x, z = lamp
        ly = lamp_y(lamp)
        found = []
        for dx, dz in offsets:
            cell = (x + dx, z + dz)
            if (cell in short
                    and abs(dx) + abs(dz) + abs(wanted[cell] + 1 - ly) <= budget):
                found.append(cell)
        return found

    def place(lamp) -> None:
        order.append(lamp)
        short.difference_update(reached(lamp))
        short.discard(lamp)

    order: list[tuple[int, int]] = []
    for cell in sorted(pool, key=_by_row):
        if on(*cell):
            place(cell)
    for cell in sorted(wanted, key=_by_row):
        if cell not in short:
            continue
        best, best_key = None, None
        for dx, dz in offsets:
            candidate = (cell[0] + dx, cell[1] + dz)
            if candidate not in pool:
                continue
            if (abs(dx) + abs(dz) + abs(wanted[cell] + 1 - lamp_y(candidate))
                    > budget):
                continue
            key = (len(reached(candidate)), _by_row(candidate))
            if best_key is None or key > best_key:
                best, best_key = candidate, key
        if best is not None:
            place(best)
    return order


class PaveResult:
    """What a :meth:`Builder.pave`, :meth:`Builder.light_cells` or
    :meth:`Builder.hang_lamps` laid down.

    ``patched`` is the number of lamps off the lattice: the ones its edges,
    an odd shape, or a wall the light has to go round needed. A handful
    means the edges needed help, which is normal. A number close to
    ``lights`` means few lattice points fall on the floor: a thin run -- a
    road, a colonnade, which the lattice crosses rarely or never -- or a
    floor not many times wider than the lattice's spacing, which at
    :data:`~heaton_voxel.craft.light.NO_SPAWNS` is 26, so a 30-block square
    lit to it is nearly all edge. Either way the lamps are still spaced by
    the reach, not packed. ``blocked`` counts the cells with a block
    standing in them -- a pier, a wall, a post -- which hold no light and
    need none. ``passed_over`` counts the columns :meth:`Builder.hang_lamps`
    could not hang a lamp in -- a ceiling a chain does not hang from,
    something already in the chain's way -- and lit the room from the
    rest of.
    """

    __slots__ = ("cells", "lights", "patched", "trim", "snow_cleared",
                 "chains", "anchors", "light_level", "blocked", "passed_over")

    def __init__(self):
        self.cells = 0
        self.lights = 0
        self.patched = 0
        self.trim = 0
        self.snow_cleared = 0
        self.chains = 0
        self.anchors = 0
        self.light_level = 0
        self.blocked = 0
        self.passed_over = 0

    def __repr__(self) -> str:
        parts = [f"{self.cells:,} cells",
                 f"{self.lights} lights ({self.patched} patched)"]
        if self.trim:
            parts.append(f"{self.trim} trim")
        if self.snow_cleared:
            parts.append(f"{self.snow_cleared} melted")
        if self.chains:
            parts.append(f"{self.chains} chain")
        if self.anchors:
            parts.append(f"{self.anchors} anchored")
        if self.blocked:
            parts.append(f"{self.blocked} blocked")
        if self.passed_over:
            parts.append(f"{self.passed_over} passed over")
        parts.append("unlit" if self.light_level is None
                     else f"light >= {self.light_level}")
        return f"<PaveResult {', '.join(parts)}>"


class LineLampsResult:
    """What a :meth:`Builder.line_lamps` stood beside a line.

    ``posts`` is where each post stands, as the cell of its foot
    ``(x, y, z)`` -- its lamp is on top of it -- in the order they were
    written; ``cells`` counts the strip cells the guarantee is about, every
    one lit to ``light_level``. ``patched`` counts the posts the flood
    added where the arithmetic spacing them left a cell dark -- behind a
    wall, round a bend, over a bump. ``blocked`` counts the strip cells
    with a block standing in them, which hold no light and need none, and
    ``passed_over`` the places beside the line a post could not stand: in a
    pad or a reservation, on water, where something already stands, with
    no ground under it.
    """

    __slots__ = ("cells", "posts", "patched", "blocked", "passed_over",
                 "light_level")

    def __init__(self):
        self.cells = 0
        self.posts: list[tuple[int, int, int]] = []
        self.patched = 0
        self.blocked = 0
        self.passed_over = 0
        self.light_level = 0

    def __repr__(self) -> str:
        parts = [f"{self.cells:,} cells",
                 f"{len(self.posts)} posts ({self.patched} patched)"]
        if self.blocked:
            parts.append(f"{self.blocked} blocked")
        if self.passed_over:
            parts.append(f"{self.passed_over} passed over")
        parts.append(f"light >= {self.light_level}")
        return f"<LineLampsResult {', '.join(parts)}>"


#: The sides of a line :meth:`Builder.line_lamps` may stand its posts on,
#: relative to travel from its first waypoint to its last.
_SIDES = ("right", "left", "both")


class LightingMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods:
    lit floors and hanging lamps.

    Builder inherits them; nothing here is used on its own.
    """

    # -- surfaces ------------------------------------------------------------

    def _level(self, light_level, what: str) -> int:
        """The light level a writer was asked for, as the int it plans to.

        ``light_level`` is required: ``None`` is a ``TypeError`` naming the
        levels the game has a rule for, raised before anything is read. A
        plain level is taken as it is and a named one as its number, with
        :data:`~heaton_voxel.craft.light.NO_SPAWNS` read for this Builder's
        dimension: 8 in the nether, 1 in the overworld and the end. The
        range is the writer's to refuse, in its own words."""
        return _resolve_level(
            light_level, lambda: self.save.dimension(self.dimension).name,
            what=what)

    def _light_budget(self, light, light_level: int, *, what: str,
                      beside=(), floor: bool = True) -> tuple[str, int, int]:
        """Validate a light source and return how far it carries.

        Split out so :meth:`pave` can refuse *before* it lays a single
        block: a half-paved square left behind by a rejected lamp is
        exactly the damage the refusal was supposed to prevent. The light
        counted is the light that lasts
        (:func:`~heaton_voxel.craft.light.durable_emission`): a lit
        redstone lamp goes dark at the next block update with nothing to
        power it, and an unwaxed copper bulb weathers down to 4. A
        ``light_level`` under 1 asks for no light at all, and is refused.

        It runs before the plan's legality check (:meth:`_commit`), so a
        block the game has that is dark says so. A source that is dark
        because the game has no such block -- it loads the id as air -- is
        refused as what it is, an
        :class:`~heaton_voxel.craft.errors.IllegalBlock`, unless
        :attr:`allow` passes it, and then it is dark like any other. That
        refusal is the call's, as its plan's check would give it: opened
        by ``what``, the writer, and naming with the lamp every block of
        ``beside`` -- the call's other materials, in the order its plan
        writes them -- that the game lacks too (:meth:`_vet_names`). A dead
        session raises before any of it (:meth:`_alive`).
        """
        self._alive()
        state = _state(light)
        name = state.name
        if floor and not _sits_in_a_floor(state):
            raise NBTError(
                f"{name} is not a full block, so it cannot be set into a "
                f"walking surface -- it would hang in the paving. Use a "
                f"glowstone, sea lantern, shroomlight or froglight, or "
                f"stand lanterns on posts beside the paving instead "
                f"(line_lamps, along its edge)"
            )
        # The exact state about to be written: an unlit lamp is no lamp,
        # and a lit one that will not stay lit is no lamp either. Only the
        # light that lasts is credited.
        if light_emission(state) == 0:
            if self._illegal(state) is not None:
                self._vet_names((*beside, state), what=what)
            raise NBTError(
                f"{state} gives off no light -- a redstone lamp or a copper "
                f"bulb is dark unless lit, and most blocks are dark full "
                f"stop. Use a light source")
        emitted = durable_emission(state)
        reason = _transient_reason(state)
        if emitted == 0:
            raise NBTError(
                f"{state} gives off light that does not last: {reason}. Use "
                f"a light source that keeps its light")
        if int(light_level) < 1:
            raise NBTError(
                f"light_level={light_level} asks for no light; a light "
                f"level is 1 to 15")
        budget = emitted - int(light_level)
        if budget < 1:
            kept = f" that lasts ({reason})" if reason else ""
            raise NBTError(
                f"{name} emits light {emitted}{kept}, which cannot hold a "
                f"walking surface at level {light_level}; ask for less light "
                f"or a brighter source"
            )
        return name, emitted, budget

    def _plan_lamps(self, wanted, light_level: int, budget: int, reach: int,
                    lamp_y, fixtures, *, overlay=None, at_most=None):
        """Where a floor's lamps go, by the game's flood: the
        :class:`~heaton_voxel.craft.light.LightPlan` lighting the cell over
        every floor cell of ``wanted`` -- ``{(x, z): floor y}`` -- to
        ``light_level``.

        ``fixtures`` maps each ``(x, z)`` a lamp may go in to what goes
        there -- the lamp, or ``{cell: block}`` for one hung on a chain --
        keyed by the lamp's cell at ``lamp_y``; ``overlay`` is the rest of
        the plan the world does not hold yet, read under them. Light the
        world already holds is counted -- a lamp this session or anybody
        else set, read off the blocks, so a lamp since broken is not -- and
        the cells it already lights, or that a block stands in, are left out
        of the :func:`_lattice` proposal the flood tries first; the flood
        then patches what the proposal leaves dark. Which cells those are is
        the plan's own first flood, over the world it reads once, so asking
        costs nothing. Nothing is proposed when the world lights the floor
        already: that plan has no lamps. ``at_most`` maps cells to the most
        light each may hold, as :func:`~heaton_voxel.craft.light.plan_lights`
        takes it -- an ice lid over a room that must stay frozen -- and a
        fixture that could pass one is never placed; left out, nothing is
        limited.
        """
        level = int(light_level)
        targets = [(x, y + 1, z) for (x, z), y in wanted.items()]
        keys = {cell: (cell[0], lamp_y(cell), cell[1]) for cell in fixtures}

        def propose(settled) -> list:
            if len(settled) == len(targets):
                return []
            lit = {(x, z) for x, _, z in settled}
            return [keys[cell] for cell in
                    _lattice(wanted, budget, reach, lamp_y, fixtures, lit)]

        return _plan_proposing(
            self.save, targets, level,
            {keys[cell]: fixture for cell, fixture in fixtures.items()},
            propose, at_most=at_most, dimension=self.dimension,
            overlay=overlay)

    def _cover(self, wanted, clear_above: bool) -> list:
        """The cells lying on a floor that its lights would melt -- snow,
        ice -- as ``(x, y, z)`` in row order, when ``clear_above``; none
        otherwise. ``wanted`` maps ``(x, z)`` to the floor level."""
        if not clear_above:
            return []
        found = []
        for cell in sorted(wanted, key=_by_row):
            x, z = cell
            above = self.save.get_block(x, wanted[cell] + 1, z,
                                        dimension=self.dimension)
            if above is not None and above.name in _MELTABLE:
                found.append((x, wanted[cell] + 1, z))
        return found

    def _light_floor(self, wanted, light, light_level: int, budget: int,
                     cover, *, what: str, overlay=None):
        """The lights for a floor, planned: ``(ops, result)`` -- each light
        in row order, then the cover it clears, in the order they are
        written -- and the report.

        Every cell may hold a light, set into the floor with the cell over
        it the one lit, and the lattice is laid at ``budget - 1``: a source
        in the floor loses one level reaching the air a walker occupies. A
        cell that stays dark wherever the lights go -- one something lying
        on the floor shuts off, as snow left on it does -- is refused,
        before anything is written. ``overlay`` is the floor still to be
        laid; the cleared ``cover`` is air to the plan."""
        light = _state(light)
        result = PaveResult()
        result.cells = len(wanted)
        result.light_level = int(light_level)
        result.snow_cleared = len(cover)
        if not wanted:
            return [], result
        overlay = dict(overlay or {})
        for cell in cover:
            overlay[cell] = AIR
        plan = self._plan_lamps(
            wanted, light_level, budget, budget - 1, lambda cell: wanted[cell],
            dict.fromkeys(wanted, light), overlay=overlay)
        if plan.dark:
            x, y, z, level = plan.dark[0]
            raise Refused(
                f"{what} cannot light {len(plan.dark)} of its "
                f"{len(wanted)} cells to {light_level} wherever its lights "
                f"go, first the cell over the floor at ({x}, {y}, {z}), at "
                f"{level}: something lying on the floor shuts out the light "
                f"of a lamp set in it, as snow does. Clear it "
                f"(clear_above=True melts snow and ice), or light those "
                f"cells from beside them. Nothing was written",
                faults=plan.dark)
        on = _diamond(budget - 1)
        lamps = sorted(((x, z) for x, _, z in plan.lamps), key=_by_row)
        ops: list[tuple] = [("set", (x, wanted[(x, z)], z), light)
                            for x, z in lamps]
        ops += [("set", cell, AIR) for cell in cover]
        result.lights = len(lamps)
        result.patched = sum(1 for cell in lamps if not on(*cell))
        result.blocked = len(plan.blocked)
        return ops, result

    def pave(self, x0, z0, x1, z1, y=None, *, mix=FROM_PALETTE,
             trim=FROM_PALETTE, light=FROM_PALETTE, light_level=None,
             clear_above: bool = True, over_water=None) -> "PaveResult":
        """Surface a rectangle, and light it to the level asked for.

        The floor a plaza, a market square or a courtyard wants: a mixed
        surface so it does not read as one flat texture, an optional
        ``trim`` course around the edge to give it a border, and lights
        set flush *into* the paving on a diamond lattice.

        ``light_level`` names the rule the light is for, and has no default,
        because the choice is most of the lamps. Lit to
        :data:`~heaton_voxel.craft.light.MELT_SNOW` (12) a floor melts snow
        lying on it; to :data:`~heaton_voxel.craft.light.NO_NEW_SNOW_OR_ICE`
        (10) no snow settles on it, so one ``clear_above`` has cleared stays
        bare, on a lattice a quarter as dense; and to
        :data:`~heaton_voxel.craft.light.NO_SPAWNS` nothing spawns on it --
        light 1 in the overworld, on one a forty-second as dense, and 8 in
        the nether, read for the Builder's dimension. A floor's edges add
        lamps of their own -- the margin the lattice leaves is lit along
        the edge -- so a real one takes more than that. Any level 1
        to 15 will do as well; leaving it out is a ``TypeError`` naming
        these, before anything is read.

        ``y`` fixes the surface height; ``None`` follows the ground, which
        is what you want over a terrace that already steps. ``light=None``
        lays the floor unlit, needs no level, and the result's
        ``light_level`` says so. The lattice is phase-locked to world
        coordinates, so two calls that meet tile seamlessly instead of
        showing a seam where the pattern restarts -- and each call is
        checked by the game's flood over the floor it is about to lay,
        crediting the light already in the world (an earlier call's lamps,
        anybody's), and patched wherever it falls short. See
        :meth:`light_cells` for what the guarantee is.

        The ground under the floor is read before anything is planned
        (:meth:`_build_base`), and paving over water or an ice sheet is
        refused, naming every wet cell, unless ``over_water`` says what to
        do with it: ``"bridge"`` lays the floor on the water, and ``"fill"``
        fills each wet cell with dirt from its bed up to the floor --
        refusing a floor under the waterline, or ice with nothing under it,
        and a fill that would starve the flowing water it fed, which
        :meth:`grade` takes out on purpose (``drain=True,
        allow_dam=True``); a floor has no ``allow_dam`` of its own. A floor
        bridged at the waterline or under it -- where ``y=None`` lays it,
        on the water's surface -- takes the water it stands in, and is
        refused the same way where that starves flowing water.

        ``mix`` is a list of ``(block, weight)`` pairs, drawn from
        :attr:`rng`, a :class:`~heaton_voxel.craft.style.Mix`, picked at
        each cell, or one block, laid everywhere; a ``trim`` is one block
        or a Mix too, and ``None`` for none. Each material left out is the
        Builder's :attr:`palette`'s: ``mix`` (or ``None``) its ``paving``,
        :data:`PLAZA_MIX` by default, the same list -- an empty list is an
        ``NBTError``, not that default -- ``trim`` its ``trim``, none by
        default, and ``light`` its ``light``, glowstone; a
        :class:`~heaton_voxel.craft.style.Material` in any of them is its
        block. The light is resolved first, so a palette's light is the
        lamp whose level is required. The surface is drawn from a
        list as the floor is planned, so its entries, ``trim`` and
        ``light`` are checked by name first, and every chunk and height the
        floor reaches (:meth:`_reach`), before a die is cast. A refusal
        after the draws -- a floor its lights cannot light, a worksite's
        :attr:`guard` -- puts :attr:`rng` back where it was, so a refused
        floor draws nothing. A Mix draws nothing at all: each cell is its
        pick there, so a floor laid after any other call is the floor it
        would have been without it. The floor is the session's path once it
        is written, and :meth:`scatter` plants nothing on it.
        """
        x0, x1 = sorted((x0, x1))
        z0, z1 = sorted((z0, z1))
        mix = self._surface(mix, "paving", what="pave")
        # A list is drawn from the rng as the floor is planned; a Mix is
        # picked at each cell, the same floor however it is planned; and
        # one block is laid as it is.
        drawn_from = isinstance(mix, (list, tuple))
        paving = _Mix(mix) if drawn_from else mix
        _over_water(over_water)
        # The palette's light before the question of whether there is one:
        # left out, it is a lamp, and its level is required.
        light, trim = self._materials(
            "pave", _Slot("light", light, "light", none=True),
            _Slot("trim", trim, "trim", mixes=True, none=True))
        budget = 0
        if light is not None:
            light_level = self._level(light_level, "pave")
            # Before anything is written: a lamp this floor cannot carry is
            # a refusal, and a refusal must not leave half a square paved.
            budget = self._light_budget(light, light_level, what="pave",
                                        beside=(paving, trim))[2]
        # The surface is drawn as it is planned, so its blocks -- the lamp
        # among them -- are checked by name before a die is cast: a floor
        # naming a block the game does not have is refused with the rng
        # where it was.
        self._vet_names((paving, trim, light), what="pave")

        # The ground first, before a block is drawn: a floor over water is
        # refused before the dice are touched.
        tops = {(x, z): self.ground(x, z) if y is None else int(y)
                for z in range(z0, z1 + 1) for x in range(x0, x1 + 1)}
        # The cells stood on their beds, to be filled up from them.
        filled: set[tuple[int, int]] = set()
        wet: dict = {}
        base = self._build_base(tops, over_water, "pave", filled=filled,
                                wet=wet)
        # A floor bridged at the waterline or under it stands in the water
        # there and takes it, as a fill takes the water it fills: held to
        # the same starve check, which points at grade, since a floor has
        # no allow_dam of its own.
        self._starve_bridged(tops, wet, filled, call="pave")
        cover = self._cover(tops, clear_above) if light is not None else []

        # Planned first, written once, in one total order: first-write
        # order becomes the section palette and so the chunk's bytes. The
        # floor and its lights are one plan, refused or written whole.
        ops: list[tuple] = []
        for (x, z) in sorted(tops, key=_by_row):
            top = tops[(x, z)]
            if (x, z) in filled and base[(x, z)] + 1 <= top - 1:
                ops.append(("fill", (x, base[(x, z)] + 1, z), (x, top - 1, z),
                            DIRT))
            ops.append(("set", (x, top, z), paving))
            ops.append(("ground", (x, z), top))
        # Every chunk and height the floor could reach, checked before a
        # die is cast: its lights go in its own cells, so this is the
        # refusal the whole plan would get, and the rng is where it was.
        self._reach(ops + [("set", cell, AIR) for cell in cover], what="pave")

        drawn = self.rng.getstate()
        try:
            surface: dict[tuple[int, int], str] = {}
            trimmed = 0
            for z in range(z0, z1 + 1):
                for x in range(x0, x1 + 1):
                    edge = x in (x0, x1) or z in (z0, z1)
                    if trim is not None and edge:
                        surface[(x, z)] = (trim.pick(x, tops[(x, z)], z)
                                           if isinstance(trim, Mix) else trim)
                        trimmed += 1
                    elif isinstance(mix, Mix):
                        surface[(x, z)] = mix.pick(x, tops[(x, z)], z)
                    elif drawn_from:
                        surface[(x, z)] = self._mix(mix)
                    else:
                        surface[(x, z)] = mix
            ops = [(op[0], op[1], surface[(op[1][0], op[1][2])])
                   if op[0] == "set" else op for op in ops]

            if light is None:
                # An unlit floor, asked for: report it as one rather than
                # pretending to a light level nothing was placed to reach.
                self._commit(ops, what="pave")
                result = PaveResult()
                result.light_level = None
            else:
                # The floor the lights are planned over: the surface as
                # drawn, the dirt under a filled cell, and the cover the
                # lights will clear off it.
                states: dict = {DIRT: _state(DIRT)}
                overlay = {}
                for op in ops:
                    if op[0] == "fill":
                        (x, ya, z), (_, yb, _) = op[1], op[2]
                        for fy in range(ya, yb + 1):
                            overlay[(x, fy, z)] = states[DIRT]
                    elif op[0] == "set":
                        block = op[2]
                        if block not in states:
                            states[block] = _state(block)
                        overlay[op[1]] = states[block]
                lights, result = self._light_floor(
                    tops, light, light_level, budget, cover, what="pave",
                    overlay=overlay)
                self._commit(ops + lights, what="pave")
        except Refused:
            self.rng.setstate(drawn)
            raise
        self._paved.update(tops)
        result.cells = len(surface)
        result.trim = trimmed
        return result

    def light_cells(self, cells, *, light=FROM_PALETTE,
                    light_level=None,
                    clear_above: bool = True, over_water=None) -> "PaveResult":
        """Inset lights into a surface until every cell clears ``light_level``.

        ``light_level`` is required, as :meth:`pave`'s is: a level 1 to 15,
        or the rule of the game it meets, named in
        :mod:`~heaton_voxel.craft.light` -- ``NO_SPAWNS`` read for the
        Builder's dimension. ``light`` left out is the Builder's
        :attr:`palette`'s ``light``, glowstone by default. ``None`` is a
        ``TypeError``, as it is not for :meth:`pave`: light_cells exists to
        set lamps, so a call with none to set is a mistake, not an unlit
        floor.

        ``cells`` are ``(x, z)`` or ``(x, y, z)``; without a ``y`` the
        current ground height is used, which is how an existing road or
        village street gets lit without being repaved. The ground under a
        bare ``(x, z)`` is read first, and one over water or an ice sheet
        is refused, naming every wet cell, unless ``over_water="bridge"``
        sets its light into the water's surface -- refused, as :meth:`pave`
        refuses a bridged floor, where the water a light takes there fed a
        flow it would starve; ``"fill"`` is pave's, for a floor still to be
        laid, and a ``ValueError`` here. A cell with a ``y`` is lit where
        it says.

        Lights go *into* the floor, replacing the surface block, so
        nothing stands proud to walk into. Spacing is derived, not
        guessed: a source in the floor loses one level reaching the air a
        walker occupies and one more per block traveled, so the furthest
        a cell can sit from a lamp of strength ``L`` across open ground and
        still read ``light_level`` is ``L - light_level - 1``. The lattice
        is laid at that reach, then **every cell is checked by the game's
        flood** (:func:`~heaton_voxel.craft.light.plan_lights`) -- light
        that goes round a wall, not through it, and is shut out by the
        solid half of a slab -- and any shortfall patched: edges and odd
        shapes are where a lattice alone leaves dark cells, and so is the
        far side of a wall, a pillar or a building, where light has to turn
        corners. Light already in the world is counted, read off the
        blocks, so a lamp an earlier call set is not doubled and one broken
        since is not credited. The cell lit is the one over the floor;
        where a block stands in it -- a post, a wall -- it holds no light
        and needs none, and the result counts it as ``blocked``.

        Snow and ice lying on the floor are cleared with ``clear_above``,
        since the light would melt them. Without it they are part of the
        floor the flood reads, and lying snow shuts the light of a lamp
        under it out: a cell no light can then reach is refused, before
        anything is written.

        The lights and the snow they clear are one plan, refused whole
        before either is written (see :meth:`_commit`).
        """
        if over_water not in (None, "bridge"):
            raise ValueError(
                f"light_cells lights a floor that is already there, so "
                f"over_water is None (refuse water) or 'bridge' (set the "
                f"lights in the water's surface), not {over_water!r}; "
                f"pave(over_water='fill') lays a floor over water")
        light, = self._materials("light_cells",
                                 _Slot("light", light, "light"))
        light_level = self._level(light_level, "light_cells")
        _, _, budget = self._light_budget(light, light_level,
                                          what="light_cells")
        # The lamp by name, as the plan's own check would take it (see
        # _commit), before the ground or a chunk is read: a block the game
        # lacks is that refusal wherever the cells are.
        self._vet_names((light,), what="light_cells")
        cells = [tuple(int(v) for v in cell) for cell in cells]
        bare = {(cell[0], cell[1]): None for cell in cells if len(cell) != 3}
        # A bare cell stands on its ground, and one on water only where
        # over_water says so.
        wet: dict = {}
        base = (self._build_base(bare, over_water, "light_cells", fills=False,
                                 wet=wet)
                if bare else {})
        wanted: dict[tuple[int, int], int] = {}
        for cell in cells:
            if len(cell) == 3:
                wanted[(cell[0], cell[2])] = cell[1]
            else:
                wanted[cell] = base[cell]
        cover = self._cover(wanted, clear_above)
        # Every chunk and height the lights could reach -- any cell may take
        # one -- before the plan reads the light there.
        self._reach([("set", (x, y, z), light) for (x, z), y in wanted.items()]
                    + [("set", cell, AIR) for cell in cover],
                    what="light_cells")
        ops, result = self._light_floor(wanted, light, light_level, budget,
                                        cover, what="light_cells")
        # A light set into the water's surface takes the water it stands
        # in: held to pave's starve check, a bridged floor's.
        lit = {(x, z): y for _, (x, y, z), _ in ops[:result.lights]
               if (x, z) in wet}
        self._starve_bridged(lit, wet, (), call="light_cells")
        self._commit(ops, what="light_cells")
        return result

    def _starve_bridged(self, tops, wet, filled, *, call: str) -> None:
        """Refuse a floor -- or its lights -- set into the water it is
        bridged over, where taking that water out starves the flowing water
        it fed (:meth:`_starve`). ``tops`` maps each column to the height
        written in it, ``wet`` each wet column to its probe
        (:meth:`_build_base`) and ``filled`` the columns filled from their
        beds instead, which the fill's own check has judged. Only a cell at
        the waterline or under it, over the bed, holds the water a write
        there takes; neither writer has an ``allow_dam``, so the refusal
        points at :meth:`grade`."""
        taken = []
        for (x, z), probe in wet.items():
            if (x, z) in filled or (x, z) not in tops:
                continue
            y = tops[(x, z)]
            if y <= probe.waterline and (probe.bed_y is None
                                         or y > probe.bed_y):
                taken.append((x, y, z))
        if taken:
            self._starve(sorted(taken), call=call, allow_dam=None)

    def hang_lamps(self, x0, z0, x1, z1, ceiling_y: int, floor_y: int, *,
                   drop: int = 3, light="minecraft:glowstone",
                   chain="minecraft:iron_chain[axis=y]", light_level=None,
                   anchor=None, columns=None,
                   overwrite: bool = False) -> "PaveResult":
        """Hang lamps on chains from a ceiling until the floor is lit.

        The other half of :meth:`light_cells`. A floor lights itself from
        underfoot; a hall lights itself from above, and a lamp hung in the
        middle of the room is the difference between a nave and a warehouse
        with the lights on. ``ceiling_y`` is the ceiling course itself --
        the chain hangs from the block *below* it -- ``drop`` how many links
        it hangs on, and ``floor_y`` the floor being lit, which is what the
        guarantee is about: the cell over every floor cell clears
        ``light_level`` by the game's flood. That level is required, as
        :meth:`pave`'s is -- a level 1 to 15, or a named one from
        :mod:`~heaton_voxel.craft.light`, ``NO_SPAWNS`` read for the
        Builder's dimension.

        Spacing is derived, not guessed, the same way :meth:`light_cells`
        derives its lattice, with one extra term: a lamp near the ceiling
        has already spent most of its light climbing down to the walker's
        head, so the further it hangs from the floor the tighter the grid
        must be. A lamp that arrives at the floor already below
        ``light_level`` is a refusal, before a block is written, naming both
        heights -- which is the failure a hall full of chandeliers has, and
        it is invisible until nightfall. The lattice is then checked by the
        flood and patched where it falls short: a partition or a pier
        shades what a lamp across it would light over open floor, and gets
        a lamp on each side. Light the room already has -- lamps hung
        earlier, by this session or anybody -- is counted, so a room that is
        lit already gets none. A cell with a block standing in it, a pier,
        holds no light and needs none, and the result counts it as
        ``blocked``.

        Two more refusals, for the same reason: a lamp low enough to walk
        into, and a chain with nothing to hang from. A lamp hangs only in a
        column whose ceiling a chain may hang from, which is what the game
        would hang a lantern from: a block sturdy at the middle of its
        underside, and never a fence gate (``LanternBlock.canSurvive``) --
        a full block, a bottom or double slab, a bottom stair, a fence or a
        wall, and not a top slab, a top stair or leaves. The game itself keeps a chain under
        anything, air included, so that rule is a choice, the lantern's
        taken for the chain (:func:`~heaton_voxel.craft.build._hangs_from`):
        a chain under a top slab hangs from a gap only a cutaway shows. An
        id the game does not have is not a ceiling, since the game loads it
        as air.

        ``anchor`` is a block set above every lamp hung -- the boss a hall
        may want over each of its lamps, or the ceiling a column without
        one needs -- in place of whatever the ceiling course holds there.
        It must be a block a chain may hang from, by the same rule, or the
        call is refused before anything is read. (JeffWorld's great hall,
        which ``Vault``'s defaults were read from, sets a boss only where a
        lantern could not hang without one: each of its nine upper aisle
        lamps hangs from a double slab set into a course of top slabs, and
        the eight down its ridge hang on nether brick fences straight from
        the ridge's bottom slabs. A hall like it hangs its ridge without
        ``anchor`` and its aisles with one; ``tools/nave/build_hall.py``
        sets one over every lamp.) Nor does a lamp hang where
        something already stands in its chain or its lamp, unless
        ``overwrite`` cuts through. Such columns are passed over -- the
        result counts them as ``passed_over`` -- and the room is lit from
        the rest. A room the rest cannot light is refused with its dark
        cells as the faults, naming the first column passed over and why;
        when every column is passed over, the reasons are the refusal.
        Cave air is air both ways: a cave is a room a chain hangs through,
        and its cave air is not a ceiling one hangs from.

        Under a vault there is nowhere to hang a lamp except the few
        columns the shell leaves clear, so the room to light and the places
        to light it from are two different shapes. ``columns`` is that
        second one -- ``(x, z)`` pairs inside the rectangle where a chain
        may go, the rows of a nave rather than the whole ceiling. Cells no
        allowed lamp can reach are a refusal naming the first of them,
        which is the honest answer to a hall too wide to light from one row
        down its ridge.
        """
        x0, x1 = sorted((int(x0), int(x1)))
        z0, z1 = sorted((int(z0), int(z1)))
        ceiling_y, floor_y, drop = int(ceiling_y), int(floor_y), int(drop)
        if drop < 0:
            raise NBTError(f"a lamp hangs below its ceiling; got drop={drop}")
        if ceiling_y <= floor_y:
            raise NBTError(f"the ceiling at y={ceiling_y} is not above the "
                           f"floor at y={floor_y}")
        lamp_y = ceiling_y - 1 - drop
        if lamp_y <= floor_y + 1:
            raise NBTError(
                f"hung {drop} from a ceiling at y={ceiling_y}, the lamp sits "
                f"at y={lamp_y}, which is in the two blocks a walker on the "
                f"floor at y={floor_y} occupies. Shorten the chain")
        light_level = self._level(light_level, "hang_lamps")

        # The chain and the anchor are one block each, as the lamp is: a Mix
        # or a Material is refused before anything is read for them.
        for block in (chain, anchor):
            refuse_many(block)
        if _hangs_lamp(_state(light)):
            # A lantern hangs by its own property; spelled out whole, so it
            # shares the palette entry of a lantern somebody hung by hand.
            lantern = _state(light)
            spelled = dict(BLOCK_DEFAULTS[lantern.name])
            spelled.update(lantern.properties)
            spelled["hanging"] = "true"
            light = BlockState(lantern.name, spelled)
        name, emitted, budget = self._light_budget(
            light, light_level, what="hang_lamps", beside=(anchor, chain),
            floor=False)
        climb = lamp_y - (floor_y + 1)
        reach = budget - climb
        if reach < 0:
            raise NBTError(
                f"{name} emits light {emitted} and hangs {climb} blocks above "
                f"a walker's head on the floor at y={floor_y}, so it arrives "
                f"there at {emitted - climb} -- already under {light_level} "
                f"directly below itself. Hang it lower, use a brighter lamp, "
                f"or ask for less light")

        wanted = {(x, z): floor_y
                  for z in range(z0, z1 + 1) for x in range(x0, x1 + 1)}
        pool = set(wanted)
        if columns is not None:
            pool = {(int(c[0]), int(c[-1])) for c in columns}
            outside = sorted(pool - set(wanted))
            if outside:
                raise Refused(
                    f"{len(outside)} of the columns a lamp may hang in are "
                    f"outside the room being lit, first {outside[0]}. The "
                    f"rectangle is what gets the guarantee, so the lamps "
                    f"have to be inside it", faults=outside)
            if not pool:
                raise NBTError("columns= names nowhere to hang a lamp")
        # Its blocks by name, in the order its plan writes them, as the
        # plan's own check would take them (see _commit): a block the game
        # lacks is that refusal even where a chunk is unfinished. Then every
        # chunk and height a lamp could hang in, before the plan reads the
        # light there.
        self._vet_names((anchor, chain, light), what="hang_lamps")
        if anchor is not None and not _hangs_from(_state(anchor)):
            raise NBTError(
                f"anchor={anchor} is set above every lamp for its chain to "
                f"hang from, and the game would not hang a lantern from it: "
                f"{_why_no_ceiling(_state(anchor))}. A full block, a bottom "
                f"or double slab, a fence or a wall will do")
        top = ceiling_y if anchor is not None else ceiling_y - 1
        self._reach([("fill", (x, lamp_y, z), (x, top, z), AIR)
                     for x, z in sorted(pool, key=_by_row)], what="hang_lamps")

        # Where a lamp can hang: a ceiling a chain hangs from, or the anchor
        # set in its place, and nothing in the way of its chain. Checked
        # before the plan rather than after it, since the flood would
        # happily hang a lamp inside a partition -- it lights both sides.
        light, chain = _state(light), _state(chain)
        if anchor is not None:
            anchor = _state(anchor)
        fixtures: dict[tuple[int, int], dict] = {}
        passed: list[str] = []
        order = sorted(pool, key=_by_row)
        # The lamp's cell up to the ceiling, every column read a section at
        # a time; and each state judged once, a ceiling being one block.
        held = _column_blocks(self.save.dimension(self.dimension), order,
                              lamp_y, ceiling_y)
        hangs_from: dict = {}
        empty: dict = {}
        for x, z in order:
            column = held[(x, z)]
            above = column[-1]
            if anchor is None:
                hangs = hangs_from.get(above)
                if hangs is None:
                    hangs = hangs_from[above] = _hangs_from(above)
                if not hangs:
                    passed.append(
                        f"({x}, {ceiling_y}, {z}) is outside the world or "
                        f"ungenerated" if above is None else
                        f"({x}, {ceiling_y}, {z}) holds {above}, which a "
                        f"chain does not hang from -- "
                        f"{_why_no_ceiling(above)}; pass anchor= to set a "
                        f"block there")
                    continue
            if not overwrite:
                taken = None
                for y, found in enumerate(column[:-1], lamp_y):
                    if found is None:
                        continue
                    clear = empty.get(found)
                    if clear is None:
                        clear = empty[found] = _empty(found)
                    if not clear:
                        taken = f"({x}, {y}, {z}) already holds {found}"
                        break
                if taken is not None:
                    passed.append(taken)
                    continue
            fixture = {}
            if anchor is not None:
                fixture[(x, ceiling_y, z)] = anchor
            for y in range(ceiling_y - 1, lamp_y, -1):
                fixture[(x, y, z)] = chain
            fixture[(x, lamp_y, z)] = light
            fixtures[(x, z)] = fixture

        plan = self._plan_lamps(wanted, light_level, budget, reach,
                                lambda cell: lamp_y, fixtures)
        # Everything checked before anything is written: half a room of
        # chandeliers is worse than none, and none of this is a mistake a
        # top-down render will ever show.
        if plan.dark and not fixtures:
            raise Refused(
                f"{len(passed)} places a lamp cannot hang, first: "
                f"{passed[0]}. Roof the room first, move the grid, or pass "
                f"overwrite=True", faults=passed)
        if plan.dark:
            x, _, z, level = plan.dark[0]
            note = ""
            if passed:
                note = (f" {len(passed)} of the columns a chain may hang in "
                        f"were passed over, first: {passed[0]}.")
            raise Refused(
                f"{len(plan.dark)} floor cells no permitted lamp can reach, "
                f"first ({x}, {z}), lit to {level}: the light of every column "
                f"a chain may hang in arrives there under {light_level} -- a "
                f"{name} has {reach} blocks left after climbing {climb} to "
                f"head height, and light goes round a wall, not through "
                f"it.{note} Hang another row, hang them lower, or ask for "
                f"less light", faults=plan.dark)

        hung = sorted(((x, z) for x, _, z in plan.lamps), key=_by_row)
        anchors = hung if anchor is not None else []
        chains = 0
        ops: list[tuple] = []
        for x, z in anchors:
            ops.append(("set", (x, ceiling_y, z), anchor))
        for x, z in hung:
            for y in range(ceiling_y - 1, lamp_y, -1):
                ops.append(("set", (x, y, z), chain))
                chains += 1
            ops.append(("set", (x, lamp_y, z), light))
        self._commit(ops, what="hang_lamps")

        on = _diamond(reach)
        result = PaveResult()
        result.cells = len(wanted)
        result.lights = len(hung)
        result.patched = sum(1 for cell in hung if not on(*cell))
        result.chains = chains
        result.anchors = len(anchors)
        result.blocked = len(plan.blocked)
        result.passed_over = len(passed)
        result.light_level = int(light_level)
        return result

    def line_lamps(self, points, *, post, lamp, light_level=None,
                   side: str = "both", width: int = 3, through=(),
                   over_water=None, avoid=()) -> "LineLampsResult":
        """Stand lamp posts beside a line until the strip along it is lit.

        The lamps a road wants, set beside it rather than in it: ``points``
        are ``(x, z)`` waypoints, walked by
        :func:`~heaton_voxel.craft.geometry.line` as :meth:`road` walks
        them, so lamps given a road's waypoints and ``width`` follow that
        road cell for cell. The **strip** is the road's swath -- a square
        ``width`` across (rounded up to odd) stamped on each cell of the
        line -- and the guarantee is about the cell over each of its
        columns, where a walker's feet are: every one clears
        ``light_level`` by the game's flood. That level is required, as
        :meth:`pave`'s is: a level 1 to 15, or a rule of the game named in
        :mod:`~heaton_voxel.craft.light` -- ``NO_SPAWNS`` read for the
        Builder's dimension, so nothing spawns on the road.

        ``post`` is what the lamp stands on -- a block, or a sequence of
        them from the ground up, a wall or a fence two high -- and ``lamp``
        what stands on top of it. Both are required: a lamp post is one
        build's look, and none is built in. ``lamp`` must give off light
        that lasts, and it must stay where it is put: the plan is audited
        for what the game will not keep before anything is written
        (:meth:`_audit_plan`), so a lantern on the half-height top of a
        bottom slab, or ``lantern[hanging=true]`` on a post with nothing
        over it, is refused -- a standing lantern needs the middle of the
        block under it (``LanternBlock.canSurvive``), which a wall, a fence
        or a full block gives.

        ``side`` is which side of the line the posts stand on, seen
        traveling from the first waypoint to the last: ``"right"``,
        ``"left"`` or ``"both"`` (the default), where they alternate
        wherever either side would serve. Each cell of the line offers a
        post on each of its sides, in the nearest column off the strip
        straight out from it: square to the step the line takes there, by
        the compass word nearest that step. An exact diagonal has two such
        words, and this breaks it toward z
        (:func:`~heaton_voxel.craft.geometry.heading_of` with
        ``ties="z"``, the castle's rule): a line heading south-east heads
        south, so its right side is west and its left side east. Along a
        diagonal run either tie offers the same columns, each from another
        cell, so the tie shows where the run starts and stops. The
        strip runs past each end of the line by half its width, and the
        line is walked on that far, straight on, so the strip's ends offer
        posts beside them too: the middle of a road's end is lit from
        beside the road, never from a post standing across it.

        A place is passed over -- counted in the result's ``passed_over``,
        never written -- where the post would stand in a pad or a
        reservation not named in ``through``, which bind this as they bind
        :meth:`road`; in a column of ``avoid``, ``(x, z)`` columns a keep-out
        is too coarse for, a crossing's roadway among its pavements; on
        water or an ice sheet, unless
        ``over_water="bridge"`` stands it on the water's surface; where
        there is no ground (a chunk not generated, or not finished); where
        its cells hold anything but air, apart from a flower, a tuft of
        grass or lying snow at its foot, which it replaces; past the top of
        the world; or where a worksite's :attr:`guard` would refuse it. No
        post ever stands in the strip. The strip's own ground is read
        first (:meth:`_build_base`), and a strip over water or ice is
        refused, naming every wet column, unless ``over_water="bridge"``
        lights it on the water's surface; ``"fill"`` is :meth:`pave`'s,
        and a ``ValueError`` here.

        Where the posts go is the game's flood's to say
        (:func:`~heaton_voxel.craft.light.plan_lights`), not a fixed
        rhythm. Spacing them by straight-line arithmetic is only the
        proposal -- walking the strip, each cell no post yet reaches gets
        the post that reaches it and the most of the strip after it,
        alternating sides on a tie -- and the flood checks every cell and
        patches where light falls
        short: the inside of a bend, the far side of a wall, a dip in the
        verge. Light already in the world is counted, so a road lit already
        gets no posts. A strip the posts it may have cannot light is
        refused with its dark cells as the faults, naming the first place
        passed over and why; when no post can stand anywhere, the reasons
        are the refusal.

        Everything is decided before a block is written, and the posts are
        written in the order the line offers them, each from its foot up:
        nothing is drawn from :attr:`rng`. Returns a
        :class:`LineLampsResult` naming every post it stood.
        """
        light_level = self._level(light_level, "line_lamps")
        if side not in _SIDES:
            raise NBTError(f"side is 'right', 'left' or 'both', seen "
                           f"traveling along the line, not {side!r}")
        if over_water not in (None, "bridge"):
            raise ValueError(
                f"line_lamps lights a strip that is already there, so "
                f"over_water is None (refuse water) or 'bridge' (light the "
                f"water's surface and stand posts on it), not "
                f"{over_water!r}")
        if isinstance(width, bool) or not isinstance(width, int) or width < 1:
            raise NBTError(f"width is the strip's width in blocks, a "
                           f"positive int, not {width!r}")
        # A post stands one exact block on another: a Mix or a Material is
        # refused for what it is, not as something that will not unpack.
        refuse_many(post)
        posts = ((post,) if isinstance(post, (str, BlockState))
                 else tuple(post))
        if not posts:
            raise NBTError("post= names nothing to stand the lamp on; give a "
                           "block, or blocks from the ground up")
        line = geometry.line(points)
        # The lamp first, as every light writer weighs it: a block the game
        # has that is dark says so, and only one it lacks is the call's
        # IllegalBlock, naming the post's with it. Then the post by name.
        name, emitted, budget = self._light_budget(
            lamp, light_level, what="line_lamps", beside=posts, floor=False)
        self._vet_names((*posts, lamp), what="line_lamps")
        posts = tuple(_state(block) for block in posts)
        lamp = _state(lamp)
        height = len(posts)

        # The strip, in the order a road paves it, and the ground under it:
        # water refused unless bridged, before a post is looked for.
        half = width // 2
        strip = _swath(line, half)
        base = self._build_base(dict.fromkeys(strip), over_water,
                                "line_lamps", fills=False)
        targets = [(x, base[(x, z)] + 1, z) for x, z in strip]

        # The places a post may stand, in the order the line offers them:
        # by cell, right side before left, each the nearest column off the
        # strip straight out from its cell, a column offered twice kept
        # where it was offered first.
        words = ("right", "left") if side == "both" else (side,)
        walk = []
        for i, cell in enumerate(line):
            ax, az = line[i] if i + 1 < len(line) else line[i - 1]
            bx, bz = line[i + 1] if i + 1 < len(line) else line[i]
            # Named here, not left to a default. Along an exact diagonal
            # either tie offers the same columns, each from another cell, so
            # it shows where such a run starts and stops: which columns its
            # first and last cells -- and the strip's ends -- offer.
            walk.append((cell, geometry.heading_of(bx - ax, bz - az,
                                                   ties="z")))
        # The strip is a square stamped on every cell, so it runs ``half``
        # past each end of the line: the line is walked on that far, back
        # from its first cell and on from its last, and those cells offer
        # posts beside them too. Without them a post beside an end cell
        # stands ``half`` short of the strip's end, and the middle of its
        # end is the furthest cell from every post there is.
        (fx, fz), first = walk[0]
        (lx, lz), last = walk[-1]
        bx, bz = geometry.COMPASS[geometry.OPPOSITE[first]]
        ex, ez = geometry.COMPASS[last]
        walk[:0] = [((fx + bx * k, fz + bz * k), first)
                    for k in range(half, 0, -1)]
        walk += [((lx + ex * k, lz + ez * k), last)
                 for k in range(1, half + 1)]
        offered: dict[tuple[int, int], tuple[int, str]] = {}
        for i, ((cx, cz), heading) in enumerate(walk):
            for word in words:
                sx, sz = geometry.COMPASS[geometry.turn(
                    heading, 1 if word == "right" else -1)]
                for out in range(half + 1, 2 * half + 2):
                    column = (cx + sx * out, cz + sz * out)
                    if column not in strip:
                        offered.setdefault(column, (i, word))
                        break

        # Each place judged before the plan: the flood would happily stand
        # a post in a pad or on a pond, since it lights the road from there.
        held = {}
        for label, _, hits in self._keep_out_faults(
                offered, through=through, what="a lamp post would stand in",
                noun="post"):
            for column in hits:
                held.setdefault(column, label)
        fixtures: dict[tuple[int, int, int], dict] = {}
        sites: dict[tuple[int, int, int], tuple] = {}
        passed: list[str] = []
        keep_off = frozenset(avoid)
        for (x, z), (i, word) in offered.items():
            where = f"the post at ({x}, {z})"
            if (x, z) in held:
                passed.append(f"{where} would stand in {held[(x, z)]}, "
                              f"which through= does not name")
                continue
            if (x, z) in keep_off:
                passed.append(f"{where} is in avoid=")
                continue
            try:
                dry, wet = self._stand((x, z))
            except NoSurface as missing:
                passed.append(str(missing))
                continue
            if wet is not None and over_water != "bridge":
                passed.append(f"{where} would stand on {_wet_words(wet)}; "
                              f"over_water='bridge' stands it on the "
                              f"water's surface")
                continue
            g = dry if wet is None else wet.waterline
            # A post on half a block would stand with a gap under it, so it
            # takes the slab's cell and stands on the whole block below.
            sunk = wet is None and _half_block(self._peek((x, g, z)))
            if sunk:
                g -= 1
            column = ("fill", (x, g + 1, z), (x, g + height + 1, z), AIR)
            faults = self._write_faults((column,))
            if faults:
                passed.append(f"{where} cannot be written: {faults[0]}")
                continue
            taken = None
            for y in range(g + 1, g + height + 2):
                found = self.save.get_block(x, y, z, dimension=self.dimension)
                if found is None or _empty(found):
                    continue
                if y == g + 1 and sunk:
                    continue            # the slab it stands in, replaced
                if (y == g + 1 and is_vegetation(found.name)
                        and "half" not in BLOCK_DEFAULTS.get(found.name, ())):
                    continue            # cover at its foot, replaced
                taken = (f"{where} would stand in ({x}, {y}, {z}), which "
                         f"holds {found}")
                break
            if taken is not None:
                passed.append(taken)
                continue
            if self._guard is not None:
                faults = self._guard.refusals(
                    [(x, g + 1, z, x, g + height + 1, z)])
                if faults:
                    passed.append(f"{where} is refused by worksite "
                                  f"{self._guard.name!r}: {faults[0]}")
                    continue
            key = (x, g + height + 1, z)
            fixture = {(x, g + k, z): block
                       for k, block in enumerate(posts, start=1)}
            fixture[key] = lamp
            fixtures[key] = fixture
            sites[key] = (i, word, (x, g + 1, z))

        def reaches(key, target) -> bool:
            """Whether the lamp at ``key`` lights ``target`` to the level
            over open ground: within ``budget``, every block counted."""
            return (abs(key[0] - target[0]) + abs(key[1] - target[1])
                    + abs(key[2] - target[2])) <= budget

        def propose(settled) -> list:
            """The posts to try first, by straight-line arithmetic: walking
            the strip, the first cell neither lit already nor reached by a
            post proposed before it gets the post that reaches it and the
            longest run of the cells after it -- the most road for one post
            -- the side the last one did not take on a tie, then the one
            further along the line. A cell no post reaches is left to the
            flood, which will find it dark."""
            done = [target in settled for target in targets]
            proposed: list[tuple[int, int, int]] = []
            last = None
            for j, target in enumerate(targets):
                if done[j]:
                    continue
                best = rank = None
                for key, (i, word, _) in sites.items():
                    if not reaches(key, target):
                        continue
                    run = 0
                    for k in range(j, len(targets)):
                        if done[k]:
                            continue
                        if not reaches(key, targets[k]):
                            break
                        run += 1
                    mine = (run, word != last, i)
                    if rank is None or mine > rank:
                        best, rank = key, mine
                if best is None:
                    continue
                proposed.append(best)
                last = sites[best][1]
                for k in range(j, len(targets)):
                    if not done[k] and reaches(best, targets[k]):
                        done[k] = True
            return proposed

        plan = _plan_proposing(self.save, targets, light_level, fixtures,
                               propose, dimension=self.dimension)
        # Everything checked before anything is written: a road half lit is
        # a road where things spawn, and no render shows which half.
        if plan.dark and not fixtures:
            raise Refused(
                f"line_lamps has nowhere to stand a post: {len(passed)} "
                f"places beside the line were passed over, first: "
                f"{passed[0]}. Nothing was written" if passed else
                f"line_lamps has nowhere to stand a post: every column "
                f"beside the line is in its strip. Nothing was written",
                faults=passed)
        if plan.dark:
            x, y, z, level = plan.dark[0]
            note = ""
            if passed:
                note = (f" {len(passed)} places a post might stand were "
                        f"passed over, first: {passed[0]}.")
            both = (" stand posts on both sides (side='both'),"
                    if side != "both" else "")
            raise Refused(
                f"line_lamps cannot light {len(plan.dark)} of the "
                f"{len(targets)} cells along the line to {light_level}, "
                f"first ({x}, {y}, {z}), lit to {level}: no post it may "
                f"stand lights it -- a {name} on a post {height} high lights "
                f"{budget} blocks out, the drop to a walker's feet among "
                f"them, before its light falls under {light_level}, and "
                f"light goes round a wall, not through it.{note} Clear the "
                f"verge,{both} use a brighter lamp, or ask for less light. "
                f"Nothing was written", faults=plan.dark)

        chosen = [key for key in sites if key in plan.lamps]
        ops: list[tuple] = []
        for key in chosen:
            for cell, block in fixtures[key].items():     # from the foot up
                ops.append(("set", cell, block))
        # What the game will not keep: the lamp on its post above all.
        self._audit_plan(ops, what="line_lamps")
        self._commit(ops, what="line_lamps")

        result = LineLampsResult()
        result.cells = len(targets)
        result.posts = [sites[key][2] for key in chosen]
        result.patched = plan.patched
        result.blocked = len(plan.blocked)
        result.passed_over = len(passed)
        result.light_level = int(light_level)
        return result
