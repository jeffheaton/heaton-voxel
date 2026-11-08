"""Water a build holds: a basin, shaped by distance to its shore.

:meth:`WaterMixin.basin` lays a contained body of water over any outline
of columns -- a pond, a moat's arm, a fountain's pool, a lake re-bedded --
with its floor where the outline's distance field puts it, and refuses,
before anything is written, what the game would do with that water next.

**The depth is a distance, not a guess.** A column ``d`` from the nearest
column outside the outline (:func:`~heaton_voxel.craft.field.distance_to_edge`)
is ``round(slope * d)`` deep, and never less than one. The field changes
by at most one between side neighbors, so at a slope of one block per
block or less no two neighboring floors are more than a block apart: the
deepest bed with no cliff in it is that cone, which is how the big lake
around Jeff's island was re-cut on 2026-08-27 (``tools/lake/lakeplan.py``,
``SLOPE = 0.75``, the slope it left room for its ripple at, and
:data:`SHORE_SLOPE` here). Everything deeper than the cone the lake had to
fill, 58,000 blocks of it, because no smooth descent reaches a hole that
close to the shore. A tank one depth all over, the park's three-band pond
(``tools/park/park_plan.py``, ``depth_map``) or any profile of the
distance is the caller's to pass instead.

**A floor over a hole is a lid.** The lake's builder learned it the hard
way: sand laid on a lake bed over a flooded cave falls through, so every
floor over air, water or anything a falling block falls through
(:func:`~heaton_voxel.craft.build._common._falls_through`, the game's own
``FallingBlock.isFree``) gets one course of lining under it -- "enough
solid to hold a gravity block up; that is *one* block" -- and so does a
floor on ice, which the basin's own lamps can melt, and a floor on sand
or gravel that stands on such a hole itself, since that falls first and
takes the floor with it. The frozen lake's rooms under its ice (``Ice
over air is a lid``, CLAUDE.md) are why a basin with lamps also keeps
every ice lid near them at light 10 or under.

Part of :mod:`heaton_voxel.craft.build`.
"""

from __future__ import annotations

from collections.abc import Mapping
from numbers import Real
from typing import NamedTuple

from ...block_defaults import BLOCK_DEFAULTS
from ...blocks import AIR_BLOCKS
from ...nbt import NBTError
from ..block_facts import holds_water, known
from ..blockinfo import _current, falls, is_vegetation
from ..errors import Refused
from ..field import distance_to_edge
from ..light import MELT_ICE, light_emission, simulate_block_light
from ..style import Mix
from ..survey import _LAVA, contained
from ._common import (_empty, _falls_through, _FREEZE_ICE, _is_whole,
                      _ON_THE_WATER, _SEAL_GIVES_WAY, _Slot, _state, _takes,
                      _walls_off_water, AIR)
from .site import _outline


#: The basin's default slope, blocks of depth per block from the shore:
#: the big lake's (``tools/lake/lakeplan.py`` ``SLOPE``, 2026-08-27), a
#: choice and no rule of the game's. Any slope up to 1 is cliff-free, so
#: up to 1 is accepted; past it, two neighboring floors can stand two
#: blocks apart.
SHORE_SLOPE = 0.75

#: What a basin fills with: a water source, every cell of it.
_SOURCE = "minecraft:water[level=0]"

#: The most block light an ice lid near a basin's lamps may hold: ice
#: melts at :data:`~heaton_voxel.craft.light.MELT_ICE` (11), into whatever
#: room it roofs.
_LID_LIMIT = int(MELT_ICE) - 1


class BasinResult:
    """What :meth:`Builder.basin` laid.

    ``floor`` maps every column of the outline, in ``(z, x)`` order, to the
    y of its floor -- the block the water stands on, ``surface - depth``.
    ``water`` counts the cells of water written over the floors, a lamp
    standing in the water among them. ``cut`` counts the columns dug down
    into the ground, the floor at or under it, and ``raised`` the columns
    built up, the floor laid on the ground or over it; together they are
    every column. ``lined`` is every cell of lining laid, sorted: under a
    raised floor the ground built up to it, and under any floor over a
    hole the one course that holds it. ``rim`` is every cell the rim
    walled, in the order laid, ``(z, x, y)``; ``sealed`` counts the seals
    that walled other water off (``seal=``). ``light`` is the
    :class:`~heaton_voxel.craft.light.LightPlan` the lamps were placed by,
    and None for an unlit basin; ``lids`` the ice lids within reach of a
    lamp that it held at light 10 or under, sorted, and ``melting`` those
    the world already lit past 10, and still would with the basin laid --
    melting whatever its lamps do, so neither held nor refused -- sorted.
    """

    __slots__ = ("floor", "water", "cut", "raised", "lined", "rim", "sealed",
                 "light", "lids", "melting")

    def __init__(self):
        self.floor: dict[tuple[int, int], int] = {}
        self.water = self.cut = self.raised = self.sealed = 0
        self.lined: tuple = ()
        self.rim: tuple = ()
        self.light = None
        self.lids: tuple = ()
        self.melting: tuple = ()

    def __repr__(self) -> str:
        parts = [f"{len(self.floor)} columns", f"{self.water} water"]
        for word in ("cut", "raised", "sealed"):
            if getattr(self, word):
                parts.append(f"{getattr(self, word)} {word}")
        if self.lined:
            parts.append(f"{len(self.lined)} lined")
        if self.rim:
            parts.append(f"{len(self.rim)} rim")
        if self.light is not None:
            parts.append(f"{len(self.light.lamps)} lamps")
        return f"<BasinResult {', '.join(parts)}>"


class _Column(NamedTuple):
    """One column of a basin, planned: its floor, the ground it was read
    on, the lining under the floor (``(y0, y1)``, inclusive, or None) and
    the top of what is cleared to air over the water (None for nothing)."""

    x: int
    z: int
    floor: int
    ground: int
    lining: tuple | None
    top: int | None


def _depths(columns, depth, slope, max_depth) -> dict:
    """``{(x, z): depth}`` for every column, by :meth:`Builder.basin`'s
    rule, or an ``NBTError`` naming what is wrong with ``depth``. Pure."""
    if depth is None or callable(depth) and not isinstance(depth, Mapping):
        distance = distance_to_edge(columns)
    if depth is None:
        found = {c: max(1, round(slope * distance[c])) for c in columns}
    elif _is_whole(depth):
        if depth < 1:
            raise NBTError(f"basin's depth is the cells of water over each "
                           f"floor, at least 1; got {depth}")
        found = dict.fromkeys(columns, depth)
    elif isinstance(depth, Mapping):
        found = {}
        bad = []
        for column in columns:
            value = depth.get(column)
            if not _is_whole(value) or value < 1:
                bad.append(f"{column}: {value!r}")
            else:
                found[column] = value
        if bad:
            raise NBTError(
                f"basin's depth= mapping must give every column of the "
                f"outline a whole depth of at least 1, and {len(bad)} have "
                f"none, first {bad[0]}")
    elif callable(depth):
        found = {}
        for column in columns:
            value = depth(distance[column])
            if not _is_whole(value) or value < 1:
                raise NBTError(
                    f"basin's depth= is a profile of the distance from the "
                    f"shore and must give a whole depth of at least 1; at "
                    f"{column}, {distance[column]:.3f} from the shore, it "
                    f"gave {value!r}")
            found[column] = value
    else:
        raise NBTError(f"basin's depth is None (the slope's profile), a whole "
                       f"depth, a mapping of columns to depths or a profile "
                       f"of the distance from the shore; got {depth!r}")
    if max_depth is not None:
        found = {c: min(d, max_depth) for c, d in found.items()}
    return found


def _gives_way(state) -> bool:
    """Whether a floor or a falling block standing on ``state`` loses it:
    what a falling block falls through
    (:func:`~heaton_voxel.craft.build._common._falls_through`, None past
    the world among it), or freeze ice, which the basin's lamps can melt
    into water."""
    return _falls_through(state) or _current(state.name) in _FREEZE_ICE


def _near(heights, x: int, y: int, z: int, reach: int) -> bool:
    """Whether a lamp at one of ``heights`` -- ``{(x, z): y}``, one to a
    column -- stands within ``reach`` of ``(x, y, z)`` by the taxicab
    bound."""
    for dx in range(-reach, reach + 1):
        across = reach - abs(dx)
        for dz in range(-across, across + 1):
            lamp_y = heights.get((x + dx, z + dz))
            if lamp_y is not None and abs(lamp_y - y) <= across - abs(dz):
                return True
    return False


def _wall_faults(block, what: str) -> None:
    """Refuse a rim that would not hold water back: every block it names
    (a :class:`~heaton_voxel.craft.style.Mix`'s entries too) standing
    whole against water
    (:func:`~heaton_voxel.craft.build._common._walls_off_water`, the seal
    rule's own test: whole on every face to water, nothing water can
    enter -- a container, or a block it washes away -- and a block the
    game does not have taken at its word, a wall unless it says it is
    waterlogged), and one that stays a wall: not ice, frosted ice or a
    sponge (``_SEAL_GIVES_WAY``). Unlike a seal, a rim may fall: what
    falls is the survival audit's to judge where it stands, since a sand
    bank on firm ground holds. A block the game does not have is the
    plan's own check's to refuse, unless ``allow=`` passes it."""
    blocks = (tuple(entry for entry, _ in block.entries)
              if isinstance(block, Mix) else (block,))
    for entry in blocks:
        state = _state(entry)
        if not _walls_off_water(state):
            raise NBTError(f"{what}={state} would not hold water back: it "
                           f"is a full block water can neither pass nor "
                           f"enter, such as minecraft:stone_bricks")
        gives = _SEAL_GIVES_WAY.get(state.name)
        if gives is not None:
            raise NBTError(f"{what}={state} would not stay a wall: {gives}")


class WaterMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods:
    water a build holds.

    Builder inherits them; nothing here is used on its own.
    """

    def basin(self, outline, surface, *, floor, depth=None,
              slope=SHORE_SLOPE, max_depth=None, lining=None, rim=None,
              light_level=None, lamp=None, seal=None,
              allow_dam: bool = False, overwrite: bool = False,
              through=()) -> BasinResult:
        """Lay a body of water over ``outline``, its top at ``surface``,
        its floor shelving down from the shore, held where it is put.

        ``outline`` is an inclusive rectangle ``(x0, z0, x1, z1)`` or any
        ``(x, z)`` columns -- a :func:`~heaton_voxel.craft.geometry.disc`,
        two discs and a channel, the park's lake -- planned and written in
        ``(z, x)`` order, as :meth:`grade`'s is; ``surface`` is the y of
        the top cell of water. Each column's ``depth`` is its cells of
        water, the floor at ``surface - depth``:

        * ``None`` (the default) -- ``max(1, round(slope * d))``, where
          ``d`` is the column's distance to the nearest column outside the
          outline (:func:`~heaton_voxel.craft.field.distance_to_edge`): a
          bed that shelves from the shore, and never a cliff -- two side
          neighbors' floors are at most a block apart at any slope up to 1
          (:data:`SHORE_SLOPE`, 0.75, the big lake's, by default); a slope
          that is not a number over 0 and at most 1 is an ``NBTError``;
        * a whole number of at least 1 -- a tank, one depth all over;
        * a mapping of every column to its depth -- the park's bands, read
          off ``park_plan.depth_map``; columns it names outside the outline
          are not read;
        * a callable of the distance, giving each column's depth.

        ``max_depth``, a whole number of at least 1, caps every column
        whatever ``depth`` is. ``floor`` (required), ``lining`` and
        ``rim`` are each a block, a
        :class:`~heaton_voxel.craft.style.Material` (its block) or a
        :class:`~heaton_voxel.craft.style.Mix`, picked at every cell it
        lands in; the floor is set in every column, the water written
        over it as sources up to ``surface``, and what stood over the
        surface -- the ground a cut went through and the cell over it, a
        flower or snow, or the water of a pond the basin lowers, with what
        lies on its surface -- is cleared to air. The session learns each
        column's ground at the surface (:meth:`set_ground`), and since that
        height is water, every ground writer after it reads the column as
        water: refused, bridged or filled (:meth:`_build_base`).

        Each column is read before anything is planned (:meth:`_stand`):
        the ground it stands on, or under water the bed. A floor above the
        ground, more than the cell over it, is built up to with
        ``lining``; a floor on the ground or under it whose cell below
        holds nothing up -- what the game lets a falling block fall
        through (:func:`~heaton_voxel.craft.build._common._falls_through`:
        any air, water, lava, fire, a snow layer, grass, roots, a light
        block) or freeze ice -- or is sand or gravel that stands on such a
        hole, is a lid over a hole, and gets one course of ``lining``
        under it, in that cell. With ``lining=None`` either is refused,
        one :class:`~heaton_voxel.craft.errors.Refused` naming every such
        column, once the chunks and heights the plan reaches are known to
        be there. Every cell the basin writes over must hold what nobody
        built -- air, ground cover, natural ground, an ore, a tree, water,
        lava, freeze ice and what floats on the water
        (:func:`~heaton_voxel.craft.build._common._takes`) -- or the very
        block the basin writes there, or is named in one refusal unless
        ``overwrite=True``.

        Then the water, before anything is written. A cell the new water
        would run, fall or waterlog into
        (:func:`~heaton_voxel.craft.survey.contained`) -- a gap in the
        bank, a cave beside the shore, lava -- is refused, naming each
        :class:`~heaton_voxel.craft.survey.Leak`, unless ``rim`` walls it:
        the rim is set in every cell beside the outline that the water
        would enter and that holds air, ground cover, a snow layer or lava
        -- never a rail, a torch or anything built, which is still a leak
        -- and is held, before anything is read, to being a wall to water
        (whole on every face, nothing water enters, and not ice or a
        sponge). Water the basin lets in from elsewhere -- a river beside
        a cut lower than its waterline -- is refused unless ``seal=``
        walls it off where it stands (:meth:`_contain_plan`). The rim and
        the seals are refused inside a keep-out not named in ``through=``;
        the outline is the caller's, as a grade's is. Water the basin
        takes out -- a pond lowered, or filled under a raised floor --
        that a flow beside it was fed by is refused, naming every cell
        starved (:func:`~heaton_voxel.craft.survey.starved`), unless
        ``allow_dam=True``. And what the plan leaves the game will not
        keep -- sand over a hole, a torch on a block the cut took -- is
        refused (:meth:`_audit_plan`). Cutting a body of water in two is
        not refused: the caller drew the outline.

        ``light_level`` and ``lamp`` go together: one without the other is
        an ``NBTError``. ``light_level`` is a level 1 to 15 or a named rule
        of the game's (:mod:`~heaton_voxel.craft.light`):
        ``NO_NEW_SNOW_OR_ICE`` (10) keeps the water open all winter,
        ``NO_SPAWNS`` keeps the drowned off it. ``lamp`` is one block: a
        full block set flush into the floor (a sea lantern, glowstone,
        a froglight), or one that holds water set in the lowest cell of
        water (four sea pickles, as Jeff lit the frozen lake). Its light
        is checked before the ground is read
        (:meth:`_light_budget`), and the lamps are placed by the game's
        flood (:func:`~heaton_voxel.craft.light.plan_lights`) so every
        cell of the surface reaches the level -- and so no ice lid within
        a lamp's reach, a ``minecraft:ice`` cell over air the plan leaves
        standing no further by the taxicab bound from where a lamp could
        go than the lamp's light less 11, passes light 10, since ice
        melts at 11 into whatever room it roofs. A lid the world already
        lights past 10, and still would with the basin laid, lamps left
        out, is melting whatever the lamps do: it is left out, and named in
        ``melting``; one the basin darkens -- lava it fills with water --
        is held like any other. A basin its lamps
        cannot light so is refused, naming the dark cells; and so is one
        whose own floor, rim or cut would light a lid past 10, which no
        lamp can, since a place a lamp could light one from is held
        back.

        Every check runs before the first write, in this order: the
        arguments; the lamp's light; every block by name; the ground; the
        chunks and heights the plan reaches (:meth:`_reach`, the write's
        own refusal word for word); its lining; what it writes over; the
        water it takes out (the starve check); the rim and its keep-outs;
        the lamps; the water (:meth:`_contain_plan`, then the seals'
        keep-outs and their starve check); the survival audit; and then
        the write (:meth:`_commit`): the columns in
        ``(z, x)`` order, each bottom up -- lining, floor, water, air --
        then the rim, then the seals. A refusal writes and draws nothing.
        ``WATER_SOURCE_CONVERSION`` is taken as on, as the water check
        takes it. Returns a :class:`BasinResult`.
        """
        self._alive()
        what = "basin"
        if not _is_whole(surface):
            raise NBTError(f"basin's surface is the whole y of its top cell "
                           f"of water, got {surface!r}")
        columns, _ = _outline(outline, what, "lay")
        if (isinstance(slope, bool) or not isinstance(slope, Real)
                or not 0 < slope <= 1):
            raise NBTError(
                f"basin's slope is the depth gained per block from the "
                f"shore, over 0 and at most 1 -- a plain distance field is "
                f"cliff-free up to one block per block; got {slope!r}")
        if max_depth is not None and (not _is_whole(max_depth) or max_depth < 1):
            raise NBTError(f"basin's max_depth is a whole number of cells of "
                           f"water, at least 1; got {max_depth!r}")
        if (light_level is None) != (lamp is None):
            given, missing = (("light_level", "lamp") if lamp is None
                              else ("lamp", "light_level"))
            raise NBTError(f"basin's {given}= needs a {missing}= too: a lamp "
                           f"is placed to light the water to a level, and a "
                           f"level is reached with a lamp")
        if floor is None:
            raise TypeError("basin(floor=None) names no block, and a basin "
                            "lays a floor under its water: name the block")
        floor, lining, rim, lamp = self._materials(
            what, _Slot("floor", floor, None, mixes=True),
            _Slot("lining", lining, None, mixes=True, none=True),
            _Slot("rim", rim, None, mixes=True, none=True),
            _Slot("lamp", lamp, "light", none=True))
        if rim is not None:
            _wall_faults(rim, "rim")
        level = budget = None
        if lamp is not None:
            level = self._level(light_level, what)
            lamp = _state(lamp)
            # Before the ground is read: a lamp this water cannot carry is
            # a refusal, and the reading is wasted on it.
            _, _, budget = self._light_budget(
                lamp, level, what=what, beside=(floor, lining, rim),
                floor=not holds_water(lamp.name, lamp.properties))
        self._vet_names((floor, lining, rim, lamp), what=what)
        self._seal_state(seal, what=what)
        depths = _depths(columns, depth, slope, max_depth)

        # The ground, every column before any is planned: a column with
        # none is NoSurface here, before a block is written.
        stood = {column: self._stand(column) for column in columns}
        planned: list[_Column] = []
        unlined: list[str] = []
        for x, z in columns:
            fy = surface - depths[(x, z)]
            y, wet = stood[(x, z)]
            g = wet.bed_y if wet is not None and wet.bed_y is not None else y
            under = None
            if fy - 1 > g:
                under = (g + 1, fy - 1)
                if lining is None:
                    unlined.append(
                        f"({x}, {z}): the floor at y={fy} stands "
                        f"{fy - 1 - g} over the ground at y={g}")
            else:
                hole = self._hole_under(x, fy, z)
                if hole is not None:
                    under = (fy - 1, fy - 1)
                    if lining is None:
                        unlined.append(f"({x}, {z}): the floor at y={fy} "
                                       f"{hole}, a lid over a hole")
            if wet is None:
                # The cell over the ground goes with it -- a flower, snow --
                # and a tall plant's upper half with its lower one.
                top = g + 1
                over = self.save.get_block(x, top, z,
                                           dimension=self.dimension)
                if (over is not None and is_vegetation(_current(over.name))
                        and over.properties.get(
                            "half", BLOCK_DEFAULTS.get(over.name, {}).get(
                                "half")) == "lower"):
                    top += 1
            else:
                # What lies on the water goes with it where the water does:
                # a snow layer on its sheet -- frosted or blue ice, since
                # the game keeps none on ice or packed ice
                # (cannot_support_snow_layer) -- and a lily pad left over
                # air.
                top = wet.waterline
                over = self.save.get_block(x, top + 1, z,
                                           dimension=self.dimension)
                if over is not None and (
                        (over.name == "minecraft:snow" and top >= surface)
                        or (over.name in _ON_THE_WATER and top > surface)):
                    top += 1
            planned.append(_Column(x, z, fy, g, under,
                                   top if top > surface else None))

        def lay(lamps=frozenset(), in_water=False) -> list[tuple]:
            ops: list[tuple] = []
            for col in planned:
                x, z, fy = col.x, col.z, col.floor
                if col.lining is not None:
                    y0, y1 = col.lining
                    ops.append(("set", (x, y0, z), lining) if y0 == y1 else
                               ("fill", (x, y0, z), (x, y1, z), lining))
                lit = (x, z) in lamps
                ops.append(("set", (x, fy, z),
                            lamp if lit and not in_water else floor))
                low = fy + 1
                if lit and in_water:
                    ops.append(("set", (x, low, z), lamp))
                    low += 1
                if low <= surface:
                    ops.append(("fill", (x, low, z), (x, surface, z),
                                _SOURCE))
                if col.top is not None:
                    ops.append(("fill", (x, surface + 1, z), (x, col.top, z),
                                AIR))
                ops.append(("ground", (x, z), surface))
            return ops

        ops = lay()
        # The refusal _commit would give, before anything is read there --
        # and before a lining is asked for under a floor past the world.
        self._reach(ops, what=what)
        if unlined:
            count = (f"{len(unlined)} columns, first {unlined[0]}"
                     if len(unlined) > 1 else unlined[0])
            raise Refused(
                f"basin would lay its floor over nothing that holds it -- "
                f"{count}. Pass lining= (a block such as "
                f"minecraft:stone) to build the low ground up and lay one "
                f"course under a floor over a hole. Nothing was written",
                faults=unlined)
        plan = self._plan_cells(ops, what=what)
        world = {cell: self.save.get_block(*cell, dimension=self.dimension)
                 for cell in plan}
        if not overwrite:
            taken = [f"{cell} holds {found}, where the basin writes "
                     f"{plan[cell]}" for cell, found in world.items()
                     if found is not None and found != plan[cell]
                     and not _takes(found, wet=True)]
            if taken:
                count = (f"{len(taken)} cells, first: {taken[0]}"
                         if len(taken) > 1 else taken[0])
                raise Refused(
                    f"basin would write over what somebody built -- "
                    f"{count}. Move the outline, or pass overwrite=True to "
                    f"take it all the same. Nothing was written",
                    faults=taken)
        # The water the basin takes out -- a pond lowered, or filled under
        # a raised floor -- checked as a grade's fill is, before its
        # leaks: a flow it fed would weaken or dry.
        removed = sorted(cell for cell, found in world.items()
                         if found is not None
                         and holds_water(found.name, found.properties)
                         and not holds_water(plan[cell].name,
                                             plan[cell].properties))
        if removed and not allow_dam:
            self._starve(removed, call=what, allow_dam=allow_dam)

        rimmed: list[tuple] = []
        if rim is not None:
            # Every cell beside the outline its water would run into, as
            # the game's first step of spread finds it, that is open --
            # air, cover, snow, lava -- and nobody's. A side of the basin's
            # own water is always outside the outline, since every cell
            # inside it at the water's height is the plan's; a fall is
            # straight down, and lands on the plan's floor or nowhere.
            cells = set()
            for leak in contained(self.save, plan,
                                  dimension=self.dimension).leaks:
                (lx, ly, lz), (sx, sy, sz) = leak.cell, leak.source
                if (ly != sy or abs(lx - sx) + abs(lz - sz) != 1
                        or leak.source not in plan or leak.block is None):
                    continue
                name = _current(leak.block.name)
                if (_empty(leak.block) or is_vegetation(name)
                        or name == _LAVA):
                    cells.add(leak.cell)
            rimmed = sorted(cells, key=lambda c: (c[2], c[0], c[1]))
            self._keep_out([(x, z) for x, _, z in rimmed], through=through,
                           what="basin's rim would stand in")
        rim_ops = [("set", cell, rim) for cell in rimmed]

        result = BasinResult()
        if lamp is not None:
            in_water = holds_water(lamp.name, lamp.properties)
            floors = {(col.x, col.z): col.floor for col in planned}
            lamp_y = ((lambda c: floors[c] + 1) if in_water
                      else (lambda c: floors[c]))
            overlay = self._plan_cells(ops + rim_ops, what=what)
            lids = self._lids(overlay, [(x, lamp_y((x, z)), z)
                                        for x, z in columns], lamp)
            # A lid the world lights past its limit, and still would with
            # the basin laid, is melting, lamps or none: not the basin's to
            # hold, nor to refuse on.
            melting = self._melting(lids, overlay)
            for cell in melting:
                del lids[cell]
            climb = min(surface - lamp_y(c) for c in columns)
            plan_lit = self._plan_lamps(
                dict.fromkeys(columns, surface - 1), level, budget,
                max(0, budget - climb), lamp_y, dict.fromkeys(columns, lamp),
                overlay=overlay, at_most=lids or None)
            if not plan_lit.ok:
                held = ""
                if lids:
                    n, k = len(lids), len(plan_lit.excluded)
                    held = (f" {n} ice {'lid' if n == 1 else 'lids'} within "
                            f"reach of a lamp must stay at light "
                            f"{_LID_LIMIT} or under, since ice melts at "
                            f"{int(MELT_ICE)} into whatever it roofs, and "
                            f"{k} {'place' if k == 1 else 'places'} a lamp "
                            f"could go {'was' if k == 1 else 'were'} held "
                            f"back for {'it' if n == 1 else 'them'}.")
                parts = []
                if plan_lit.dark:
                    x, y, z, lit = plan_lit.dark[0]
                    parts.append(
                        f"{len(plan_lit.dark)} of its {len(columns)} surface "
                        f"cells stay under light {level}, first ({x}, {y}, "
                        f"{z}) at {lit}")
                if plan_lit.over:
                    # No lamp does this: a place a lamp could light a lid
                    # past its limit from was held back. What lights it is
                    # the basin's own floor, lining or rim, or the world's
                    # light through its cut.
                    x, y, z, lit, limit = plan_lit.over[0]
                    n = len(plan_lit.over)
                    parts.append(
                        f"{n} ice {'lid' if n == 1 else 'lids'} would pass "
                        f"light {limit} by what the basin itself lays or "
                        f"cuts, lamps or none, first ({x}, {y}, {z}) at "
                        f"{lit}")
                if not plan_lit.dark:
                    advice = ("Keep the water further from the lids, or lay "
                              "a floor, lining and rim that give off no "
                              "light")
                elif lids:
                    advice = ("Use a brighter lamp or a shallower basin, ask "
                              "for less light, or keep the water further "
                              "from the lids")
                else:
                    advice = ("Use a brighter lamp or a shallower basin, or "
                              "ask for less light")
                opening = (f"basin cannot light its water with {lamp}"
                           if plan_lit.dark else
                           "basin would melt the ice near its water")
                raise Refused(
                    f"{opening}: {'; and '.join(parts)}.{held} {advice}. "
                    f"Nothing was written",
                    faults=[*plan_lit.dark, *plan_lit.over])
            lamps = {(x, z) for x, _, z in plan_lit.lamps}
            ops = lay(lamps, in_water)
            result.light = plan_lit
            result.lids = tuple(lids)
            result.melting = melting

        ops = ops + rim_ops
        seals = self._contain_plan(ops, what=what, seal=seal)
        if seals:
            self._keep_out([(x, z) for _, (x, _, z), _ in seals],
                           through=through, what="basin's seals would stand in")
            # A seal takes the water it stands in, as a fill does.
            if not allow_dam:
                self._starve(sorted(set(removed) | {c for _, c, _ in seals}),
                             call=what, allow_dam=allow_dam)
        self._audit_plan(ops + seals, what=what)
        self._commit(ops + seals, what=what)

        result.floor = {(col.x, col.z): col.floor for col in planned}
        result.water = sum(surface - col.floor for col in planned)
        result.cut = sum(1 for col in planned if col.floor <= col.ground)
        result.raised = len(planned) - result.cut
        result.lined = tuple(sorted(
            (col.x, y, col.z) for col in planned if col.lining is not None
            for y in range(col.lining[0], col.lining[1] + 1)))
        result.rim = tuple(rimmed)
        result.sealed = len(seals)
        return result

    def _hole_under(self, x: int, fy: int, z: int) -> str | None:
        """Why a floor at ``(x, fy, z)`` would be a lid over a hole, or
        None where what is under it holds it up. The cell under it gives
        way (:func:`_gives_way`); or it falls -- sand, gravel
        (:func:`~heaton_voxel.craft.blockinfo.falls`) -- and so does every
        cell under that down to one that gives way, since the game drops
        the lowest first and the rest after it, the floor last. Read from
        the world: under a floor that is not built up, nothing is the
        plan's."""
        below = self.save.get_block(x, fy - 1, z, dimension=self.dimension)
        y, held = fy - 1, below
        while held is not None and not _gives_way(held) and falls(held.name):
            y -= 1
            held = self.save.get_block(x, y, z, dimension=self.dimension)
        if not _gives_way(held):
            return None
        where = ("nothing, past the world" if held is None
                 else f"{held}")
        if y == fy - 1:
            return f"would lie on {where} at y={y}"
        return (f"would lie on {below} at y={fy - 1}, which falls through "
                f"{where} at y={y}")

    def _lids(self, plan, lamps, lamp) -> dict:
        """``{cell: 10}`` for every ice lid a lamp of ``lamp`` set at any
        of ``lamps`` could light past 10: each ``minecraft:ice`` cell no
        further from one of them than the lamp's light less 11 by the
        taxicab bound (light falls by at least one a step, whatever stands
        between), found with ``find_blocks``, that the plan leaves standing
        over air -- the plan's or the world's below it, an id the game
        does not have being air. An unreadable chunk in the search is its
        ``NBTError``: could not read is never no lid."""
        reach = light_emission(lamp) - int(MELT_ICE)
        if reach < 0 or not lamps:
            return {}
        xs = [c[0] for c in lamps]
        ys = [c[1] for c in lamps]
        zs = [c[2] for c in lamps]
        found = self.save.find_blocks(
            (min(xs) - reach, min(ys) - reach, min(zs) - reach),
            (max(xs) + reach, max(ys) + reach, max(zs) + reach),
            "minecraft:ice", dimension=self.dimension, keep_loaded=True)
        if found.unreadable_chunks:
            chunk, why = found.unreadable_chunks[0]
            raise NBTError(f"basin cannot tell which ice near its lamps is a "
                           f"lid: chunk {chunk} cannot be read ({why})")
        # The lamps by column, one to a column: the box is every lamp's
        # reach at once, and an outline of two ponds holds ice in the
        # middle that neither reaches.
        heights = {(x, z): y for x, y, z in lamps}
        lids = {}
        for x, y, z, _ in found.matches:
            if (x, y, z) in plan or not _near(heights, x, y, z, reach):
                continue
            below = plan.get((x, y - 1, z))
            if below is None:
                below = self.save.get_block(x, y - 1, z,
                                            dimension=self.dimension)
            if below is not None and (
                    _current(below.name) in AIR_BLOCKS
                    or not known(_current(below.name))):
                lids[(x, y, z)] = _LID_LIMIT
        return dict(sorted(lids.items()))

    def _melting(self, lids, plan) -> tuple:
        """The ``lids`` the world lights past their limit as it stands and
        would still with ``plan`` laid over it, its lamps left out, sorted:
        those melt whatever the lamps do. A lid only the plan lights past
        its limit -- its floor, lining or rim, or its cut letting the
        world's light through -- is the plan's, and one only the world
        lights -- by lava the plan fills with water -- is the lamps' to
        keep frozen. Both by the game's flood
        (:func:`~heaton_voxel.craft.light.simulate_block_light`), every
        source counted as it stands, as the lamps' own check counts them,
        over a box as far past the lids as light that could pass 10 there
        travels (15 less 11)."""
        if not lids:
            return ()
        xs = [c[0] for c in lids]
        ys = [c[1] for c in lids]
        zs = [c[2] for c in lids]
        melting = set(lids)
        for overlay in (None, plan):
            field = simulate_block_light(
                self.save, (min(xs), min(ys), min(zs)),
                (max(xs), max(ys), max(zs)), dimension=self.dimension,
                overlay=overlay, margin=15 - int(MELT_ICE))
            melting = {cell for cell in melting
                       if field.level(*cell) > lids[cell]}
            if not melting:
                return ()
        return tuple(sorted(melting))
