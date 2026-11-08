"""The Builder itself.

One construction session over one save: its write path -- the primitives,
and :meth:`Builder._commit`, which every writer hands its planned call to
-- the reader its rule writers plan with, the journal of what it wrote
(:mod:`~heaton_voxel.craft.build.journal`), and :meth:`Builder.finish`,
which settles what the journal holds. Two ways of writing a component made
elsewhere ride the same write path: :meth:`Builder.framed`, a view
(:mod:`~heaton_voxel.craft.build.framed`) whose writes take the component's
own cells and states, and :meth:`Builder.stamp`, a schematic pasted
whole. Its construction methods live beside the rules they write, in the
mixins it is built from.

Part of :mod:`heaton_voxel.craft.build`.
"""

from __future__ import annotations

import contextlib
import math
import os
import random

from ...blocks import AIR_BLOCKS, BlockState, ReadOnlyChunk
from ...connect import ConnectResult, _candidate
from ...nbt import NBTError, TagCompound, TagInt, TagString
from ...place import _cells_for, _with_water
from ...schem import Schematic, SchemImportResult
from ...schem.transforms import directional_leftovers
from ...world.results import HeightmapResult, LightResetResult
from ..biomes import VILLAGER_KINDS, villager_type
from ..blockinfo import _allowance, check_state
from .._plan import refuse_many
from ..block_facts import holds_water
from ..block_registry import WATER_FLUIDS
from ..errors import IllegalBlock, Refused
from ..style import DEFAULT_PALETTE, FROM_PALETTE, Material, Mix, Palette
from ..survey import contained, unsupported, water_body
from ._common import (_finished, _Mix, _same_crown,  # noqa: F401
                      _seal_gives_way, _state, _takes, _walls_off_water)
from .approach import ApproachMixin
from .city import CityMixin
from .crown import CrownMixin
from .extrude import ExtrudeMixin
from .framed import Framed
from .furniture import FurnitureMixin
from .journal import Journal
from .lighting import LightingMixin
from .massing import MassingMixin
from .planting import PlantingMixin
from .railway import RailwayMixin
from .roof import RoofMixin
from .shells import ShellsMixin
from .site import SiteMixin
from .stairs import StairsMixin
from .streets import StreetsMixin
from .vault import VaultMixin
from .walls import WallsMixin
from .water import WaterMixin


def _describe(op) -> str:
    """A primitive's write, in the words a refusal opens with."""
    if op[0] == "fill":
        return f"fill {tuple(op[1])}..{tuple(op[2])}"
    if op[0] == "paste":
        box = op[1].box
        return f"paste {box[:3]}..{box[3:]}"
    return f"{op[0]} at {tuple(op[1])}"


class _Paste:
    """A schematic standing in a plan, as :meth:`Builder.stamp` makes one
    and :meth:`Builder._commit` writes it: ``schematic`` already flipped
    and turned, its minimum corner at the world cell ``corner``, filling
    the inclusive ``box``; ``states``, the distinct states the paste
    writes (air left out where ``paste_air`` is off), in the order the
    schematic's cells first hold them; and ``entity_cells``, the world
    cell each of its entities lands in, in its order -- none when
    ``entities`` is off, since none are written."""

    __slots__ = ("schematic", "corner", "box", "states", "paste_air",
                 "entities", "entity_cells")

    def __init__(self, schematic, corner, box, states, paste_air, entities,
                 entity_cells):
        self.schematic = schematic
        self.corner = corner
        self.box = box
        self.states = states
        self.paste_air = paste_air
        self.entities = entities
        self.entity_cells = entity_cells

    def __repr__(self) -> str:
        width, height, length = self.schematic.size
        return f"<_Paste {width}x{height}x{length} at {self.corner}>"


def _named(block) -> tuple:
    """Every block ``block`` names, as a plan's checks read them, picking
    and drawing nothing: a :class:`~heaton_voxel.craft.build._common._Mix`'s
    entries and a :class:`~heaton_voxel.craft.style.Mix`'s, in order, a
    paste's states (:class:`_Paste`), or the block itself."""
    if isinstance(block, _Mix):
        return tuple(name for name, _ in block.mix)
    if isinstance(block, Mix):
        return tuple(name for name, _ in block.entries)
    if isinstance(block, _Paste):
        return block.states
    return (block,)


def _one_placement(block) -> None:
    """Refuse a placement planned from a mix. Which cells a placement
    writes -- a bed's head beside its foot, a door's top half -- and which
    neighbors it re-derives depend on the block placed, so it is planned
    from one: a mix drawn as it is written has none yet, and a
    :class:`~heaton_voxel.craft.style.Mix` is a different block from cell
    to cell."""
    if isinstance(block, _Mix):
        raise NBTError("a placement is planned from one block, since its "
                       "cells depend on which; draw the mix first")
    if isinstance(block, Mix):
        raise TypeError(f"a placement is planned from one block, since its "
                        f"cells depend on which, and {block!r} is a Mix, a "
                        f"block picked at each cell: place its "
                        f".pick(x, y, z), or write the mix with set or fill")


def _palette(palette) -> Palette:
    """``palette`` as a Builder holds it: ``None`` is
    :data:`~heaton_voxel.craft.style.DEFAULT_PALETTE`, a
    :class:`~heaton_voxel.craft.style.Palette` itself, and anything else a
    ``TypeError`` -- a dict of roles would otherwise be read role by role
    with none of a Palette's checks."""
    if palette is None:
        return DEFAULT_PALETTE
    if not isinstance(palette, Palette):
        raise TypeError(f"a Builder's palette is a craft.style.Palette, not "
                        f"{type(palette).__name__}: {palette!r}; make one "
                        f"with Palette(**roles)")
    return palette


def _villager_kind(kind: str) -> str:
    """``kind`` as the type id a villager's ``VillagerData`` holds --
    ``"snow"`` and ``"minecraft:snow"`` alike -- refusing one the game does
    not have (:data:`~heaton_voxel.craft.biomes.VILLAGER_KINDS`), which it
    would load as plains."""
    name = kind if ":" in kind else f"minecraft:{kind}"
    if name not in VILLAGER_KINDS:
        legal = ", ".join(sorted(k.removeprefix("minecraft:")
                                 for k in VILLAGER_KINDS))
        raise NBTError(f"villager kind {kind!r} is not a villager type the "
                       f"game has, and it would load as plains; the types "
                       f"are {legal}")
    return name


#: How far a write reaches the states :meth:`Builder.finish` re-derives.
#: ``connect._derived_state`` reads only the blocks across a face: those
#: beside a fence, a wall, a pane, a gate or a stair, and the one over a
#: wall. What it reads of the wall above -- whether a side is ``none`` --
#: that wall derives from the blocks beside it, which are within one of
#: this wall too. So no derived state hangs on a block farther than one
#: away in any direction, diagonals included, and no write can change one
#: farther away than that.
_CONNECT_REACH = 1

#: How far the support audit reads from a block it checks, so how far
#: from a write a fault can appear: an iron door's lower half reads the
#: button beside the block over its upper half, two levels up
#: (:func:`~heaton_voxel.craft.survey.iron_door_opens`).
_AUDIT_REACH = 2

#: How badly the audit says a door is shut, for :meth:`Builder._audit_plan`:
#: a plan's door fault is its own only where it shuts the door further.
#: A door that opens from neither side is worse than one that opens from
#: one, and one that opens from both is not listed at all (rank 0).
_DOOR_RANK = {"one-way door": 1, "sealed door": 2}

#: The checks that read a plan's cells (:meth:`Builder._plan_cells`), by
#: the name a writer passes: what each is called in a refusal, and what a
#: paste it cannot read is left to.
_PLAN_READERS = {
    "audit": ("the survival audit", "; finish() audits it once it is written"),
    "water": ("the water check", ""),
}

#: The leaks :meth:`Builder._contain_plan` never seals: lava touching the
#: water, which turns to stone where they meet whatever walls the water,
#: and water running into a chunk that is not there to read.
_UNSEALABLE = frozenset(("lava", "ungenerated"))

#: How far :meth:`Builder._split_faults` looks past a fill first, in
#: columns, for the water beside it to join round it -- a jetty's end, a
#: bay's mouth -- before it follows the water further.
_SPLIT_MARGIN = 16

#: How many cells of water and ice :meth:`Builder._split_faults` follows
#: before it gives up: an implementation bound, not taste. A body larger
#: than this is a sea, whose two sides a fill would meet only far off, and
#: is refused as water that could not be followed far enough to see.
_SPLIT_LIMIT = 2 ** 16


def _leaks_refused(faults, plan, *, what: str, seal=None,
                   again: bool = False, held=None, sealing: bool = True,
                   spilling: str | None = None) -> None:
    """Refuse a plan for ``faults``, the
    :class:`~heaton_voxel.craft.survey.Leak` list
    :meth:`Builder._contain_plan` found with ``plan`` laid over the world:
    one :class:`~heaton_voxel.craft.errors.Refused` naming the first and
    how many, every one in its ``faults``, and what may be done about
    them. ``seal`` is the seal the plan was given, if any; ``again`` says
    the leaks are the ones left once it was laid. ``held`` maps each
    source whose water something stands in -- a waterlogged chest, stair
    or lichen, which a seal never replaces -- to that block, so a leak
    from one is never told to pass ``seal=``. ``sealing=False`` is a
    writer with no ``seal=`` to pass (:meth:`Builder.furnish`), told to
    keep clear of the water instead; ``spilling`` names where a writer
    takes the cells its own water may run into -- ``"spill="`` -- which a
    leak of the plan's own water is pointed at."""
    held = held or {}
    count = (f"{len(faults)} leaks, first: {faults[0]}" if len(faults) > 1
             else str(faults[0]))
    advice = []
    if again:
        advice.append(f"walled off with {seal}, the water would still get "
                      f"out -- a seal can open a way of its own, as falling "
                      f"water over a sealed cell turns to its sides -- so "
                      f"keep the plan clear of the water")
    else:
        lost = any(leak.kind in _UNSEALABLE for leak in faults)
        planned = any(leak.kind not in _UNSEALABLE and leak.source in plan
                      for leak in faults)
        sealable = any(leak.kind not in _UNSEALABLE
                       and leak.source not in plan
                       and leak.source not in held for leak in faults)
        standing = sorted({leak.source for leak in faults
                           if leak.kind not in _UNSEALABLE
                           and leak.source not in plan
                           and leak.source in held})
        if sealable and seal is None:
            advice.append("pass seal='minecraft:cobblestone' to wall the water "
                          "off where it stands, or keep the plan clear of it"
                          if sealing else
                          "keep the plan clear of the water beside it")
        if standing:
            first = standing[0]
            more = (f" (and {len(standing) - 1} more cells like it)"
                    if len(standing) > 1 else "")
            advice.append(f"the water at {first} is held by {held[first]}"
                          f"{more}, and a seal replaces water alone, never "
                          f"what stands in it: keep the plan clear of that "
                          f"water" if sealing else
                          f"the water at {first} is held by {held[first]}"
                          f"{more}: keep the plan clear of that water")
        if planned:
            own = ("the water is the plan's own, and a seal goes where the "
                   "water is, over the plan: close the plan's own way out"
                   if sealing else
                   "the water is the plan's own: close its way out")
            if spilling is not None:
                own += (f", or name the cells it may run into with "
                        f"{spilling}")
            advice.append(own)
        if lost:
            advice.append("no seal mends lava touching water, or water "
                          "running into a chunk that is not there: keep the "
                          "plan clear of them")
    advice = "; ".join(advice)
    raise Refused(f"{what} would let water out: {count}. "
                  f"{advice[0].upper()}{advice[1:]}. Nothing was written",
                  faults=faults)


def _margin_boxes(boxes, margin: int) -> dict:
    """``(cx, cz) -> (corner1, corner2)``: the bounding box of every write
    box in ``boxes`` (``(cx, cz) -> (x0, y0, z0, x1, y1, z1)``, as
    :meth:`Journal._chunk_boxes` gives them), grown ``margin`` blocks each
    way and clipped to each chunk it reaches, the chunks sorted. A margin
    crossing a chunk edge reaches a chunk nothing was written in."""
    parts: dict[tuple[int, int], tuple[int, ...]] = {}
    for x0, y0, z0, x1, y1, z1 in boxes.values():
        x0, y0, z0 = x0 - margin, y0 - margin, z0 - margin
        x1, y1, z1 = x1 + margin, y1 + margin, z1 + margin
        for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
            cx0, cx1 = max(x0, chunk_x * 16), min(x1, chunk_x * 16 + 15)
            for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
                cz0, cz1 = max(z0, chunk_z * 16), min(z1, chunk_z * 16 + 15)
                key = (chunk_x, chunk_z)
                part = parts.get(key)
                parts[key] = ((cx0, y0, cz0, cx1, y1, cz1) if part is None else
                              (min(part[0], cx0), min(part[1], y0),
                               min(part[2], cz0), max(part[3], cx1),
                               max(part[4], y1), max(part[5], cz1)))
    return {key: (part[:3], part[3:]) for key, part in sorted(parts.items())}


def _merge(into, part):
    """Add one chunk's aftercare result ``part`` into ``into``, a result of
    the same class: every int slot summed, every list slot extended.

    The lower tier's result classes are slotted counts and lists, and this
    is how :meth:`Builder.finish` reports its per-chunk passes as one. A
    slot holding anything else -- or a result that is not slotted -- is
    refused with an ``NBTError`` rather than dropped, so a field the lower
    tier adds to one of them fails here, loudly, instead of reading zero.
    """
    kind = type(into)
    if type(part) is not kind:
        raise NBTError(f"cannot merge a {type(part).__name__} into a "
                       f"{kind.__name__}")
    if hasattr(into, "__dict__"):
        raise NBTError(f"cannot merge a {kind.__name__}: finish() merges "
                       f"slotted results, and this one has fields it cannot "
                       f"list")
    for cls in kind.__mro__:
        slots = cls.__dict__.get("__slots__", ())
        for slot in (slots,) if isinstance(slots, str) else slots:
            mine, theirs = getattr(into, slot), getattr(part, slot)
            if type(mine) is int and type(theirs) is int:
                setattr(into, slot, mine + theirs)
            elif type(mine) is list and type(theirs) is list:
                mine.extend(theirs)
            else:
                raise NBTError(
                    f"cannot merge {kind.__name__}.{slot}, a "
                    f"{type(mine).__name__}: finish() sums counts and joins "
                    f"lists, and a result holding anything else has changed "
                    f"under it")
    return into


class BuildFinish:
    """Everything :meth:`Builder.finish` did, one report per aftercare
    pass, each the sum of that pass over every chunk it ran on."""

    __slots__ = ("connections", "heightmaps", "lighting", "poi_records",
                 "unsupported")

    def __init__(self, connections, heightmaps, lighting, poi_records,
                 unsupported=()):
        self.connections = connections    # ConnectResult
        self.heightmaps = heightmaps      # HeightmapResult
        self.lighting = lighting          # LightResetResult
        self.poi_records = poi_records    # chunk records dropped for rescan
        #: What the game will not keep, from :func:`~heaton_voxel.craft.survey.unsupported`.
        self.unsupported = list(unsupported)

    def __repr__(self) -> str:
        connections = ("connections skipped" if self.connections is None
                       else repr(self.connections))
        audit = (f" {len(self.unsupported)} unsupported" if self.unsupported
                 else "")
        return (f"<BuildFinish {connections} {self.heightmaps!r} "
                f"{self.lighting!r} poi_dropped={self.poi_records}{audit}>")


class Builder(LightingMixin, SiteMixin, WaterMixin, ShellsMixin, WallsMixin,
              RoofMixin, FurnitureMixin, VaultMixin, StairsMixin,
              RailwayMixin, ExtrudeMixin, ApproachMixin, PlantingMixin,
              MassingMixin, CrownMixin, StreetsMixin, CityMixin):
    """A construction session over one save.

    Journals every write it makes (:attr:`journal`, counted under the
    :meth:`stage` that made it) so :meth:`finish` knows exactly what to
    settle, and keeps the ground per
    column -- what it read (:meth:`probe`) and what it shaped
    (:meth:`set_ground`) -- so terrain-following work reads each column
    once.
    ``x``/``z`` everywhere are global block coordinates; pad and shell
    rectangles are inclusive.

    A call writes all of itself or nothing. Every writer plans its whole
    call and hands the plan to :meth:`_commit`, which checks all of it --
    every state it names parsed and one the game has, every chunk it
    reaches generated and finished, every height inside the world -- before
    the first write, and refuses it whole, naming every fault, rather than
    leave the first columns of a pad in the save. The primitives check
    their own cells the same way.

    Every write is **strict**: a block the game does not have, or a
    property or value it does not take, is refused
    (:class:`~heaton_voxel.craft.errors.IllegalBlock`), since the game
    would load the id as air and the value as the block's default and say
    so only in its log. ``allow`` passes what the registry cannot know --
    a modded block, or one newer than the game it was generated from --
    as exact ids (``"create:gearbox"``) or whole namespaces ending in
    ``":"`` (``"create:"``), held for the session as the frozenset
    :attr:`allow`; an entry that could match no id raises ``NBTError``
    here, as :func:`~heaton_voxel.craft.blockinfo.check_state` raises it.

    A Builder from :meth:`~heaton_voxel.craft.worksite.Worksite.builder`
    carries that worksite's :attr:`guard`, and is admitted by it before
    every write, after every other check: a write the worksite's restore
    could not take back, or one touching a protected zone, is refused whole
    before its first block moves (see :meth:`_admit`). ``guard=`` is that
    worksite's own, bound to its save and its dimension, so a guard with
    another save, or a ``dimension`` other than the worksite's, raises
    ``NBTError``; ``dimension=None`` takes the worksite's.

    A component designed facing one way is built facing any other through
    :meth:`framed` (local cells and states, turned by a
    :class:`~heaton_voxel.craft.geometry.Frame`), and a schematic is pasted
    by :meth:`stamp`; both are checked, journaled and settled as every
    other write is. A room is furnished by :meth:`furnish`, its
    :class:`~heaton_voxel.craft.build.furniture.Piece` values placed as
    items are and the room walked before and after.

    ``palette`` is the :class:`~heaton_voxel.craft.style.Palette` the
    eight legacy writers read a material from wherever their caller leaves
    it out (:attr:`palette`): ``level_pad``, ``road``, ``pave``,
    ``light_cells``, ``frame_walls``, ``floor``, ``gable_roof`` and
    ``window``. ``None`` is
    :data:`~heaton_voxel.craft.style.DEFAULT_PALETTE`, which holds each
    writer's own old default, so a Builder given none writes what it
    always wrote, byte for byte; anything that is not a Palette is a
    ``TypeError``.
    """

    def __init__(self, save, *, dimension: str | None = None, seed: int = 0,
                 allow=(), guard=None, palette=None):
        palette = _palette(palette)
        if guard is not None:
            if guard.save is not save:
                raise NBTError(
                    f"this guard was made for another save than the one this "
                    f"Builder writes, so what it admits would not be what is "
                    f"written; build with worksite {guard.name!r}'s own "
                    f"site.builder()")
            if dimension is None:
                dimension = guard.dimension
            elif dimension != guard.dimension:
                raise NBTError(
                    f"worksite {guard.name!r} snapshots {guard.dimension}, so "
                    f"its guard cannot admit writes in {dimension}")
            guard.live()
        self._save = save
        self.dimension = dimension
        self._guard = guard
        self._allow = _allowance(allow)
        self._palette = palette
        self.rng = random.Random(seed)
        #: Every write this session made, where, and under which stage:
        #: what :meth:`finish` settles.
        self.journal = Journal()
        self.entities_added = 0
        # The ground this session shaped, column by column -- pads, roads,
        # paving, smoothing, felled trees, set_ground -- and the world as
        # it was first read under every other column (SiteMixin.probe),
        # which no write of the session's updates.
        self._ground: dict[tuple[int, int], int] = {}
        self._probes: dict = {}
        self._pads: dict[str, tuple[int, int, int, int]] = {}
        # Column rectangles kept out of the routed writers' way without
        # leveling them (:meth:`reserve`), and the order every pad and
        # reservation name was registered in, which is the order a
        # keep-out refusal names them.
        self._reserved: dict[str, tuple[int, int, int, int]] = {}
        self._registered: dict[str, int] = {}
        # Cells a flight or an approach owns -- its treads or its surface,
        # a flight's wood, the head room over them -- so a later flight or
        # approach cannot be stacked into them, nor a shell, a piece of
        # furniture or a planting set in them.
        self._claimed: set[tuple[int, int, int]] = set()
        # Those of them an approach claims, so a refusal can say whose
        # (:meth:`_claimant`).
        self._approached: set[tuple[int, int, int]] = set()
        # The columns this session paved -- a road's swath, a pave's
        # rectangle -- recorded once they are written and read by
        # PlantingMixin.scatter alone, which plants nothing on them: a path
        # is no place to plant, whatever its blocks (a lane's coarse dirt
        # grows flowers as well as any lawn).
        self._paved: set[tuple[int, int]] = set()
        # The columns whose ground this session's extrusions raised to their
        # own top, with that height, read by ExtrudeMixin.extrude alone: a
        # wall following the terrain is not to follow a wall it laid.
        self._extruded: dict[tuple[int, int], int] = {}
        # And the columns whose ground an extrusion's cut lowered -- a
        # ditch's floor -- with that height: ground a wall following the
        # terrain may stand on, and a cut following it is not to dig again.
        self._trenched: dict[tuple[int, int], int] = {}
        # The y range of every finished chunk the write check has looked
        # at, so a long run of writes reads each chunk's status once. Only
        # chunks that passed are kept: a refusal is not worth remembering.
        self._spans: dict[tuple[int, int], tuple[int, int]] = {}
        # Every state the legality check has passed, so a session checks
        # each one once. Only legal ones: a refusal is not worth
        # remembering, and allow= never changes under it (:attr:`allow`).
        self._legal_seen: set[BlockState] = set()

    @property
    def allow(self) -> frozenset[str]:
        """The ids and ``"namespace:"`` entries this session writes
        unchecked, fixed as the Builder is made: a state that passed under
        them is not checked again, so an allowance narrowed afterwards
        would go on passing what it no longer names. A session wanting a
        different one is a new Builder."""
        return self._allow

    @property
    def palette(self) -> Palette:
        """The :class:`~heaton_voxel.craft.style.Palette` a legacy writer
        reads each material its caller leaves out from
        (:meth:`_materials`, :meth:`_surface`); an argument given always
        wins. Settable, unlike :attr:`allow` and :attr:`guard`, since
        nothing is cached on it and one session may build in more than one
        style -- the Roman town built in three families. Setting ``None``
        is :data:`~heaton_voxel.craft.style.DEFAULT_PALETTE`, and anything
        that is not a Palette is a ``TypeError``."""
        return self._palette

    @palette.setter
    def palette(self, palette) -> None:
        self._palette = _palette(palette)

    @property
    def guard(self):
        """The :class:`~heaton_voxel.craft.worksite.SiteGuard` admitting this
        session's writes, or None for a Builder no worksite made. Fixed as
        the Builder is made: a guard attached afterwards would admit writes
        while the ones before it went unchecked."""
        return self._guard

    @property
    def save(self):
        """The save this session builds in. A Builder a worksite handed out
        has none once that worksite is restored: what it held is the world
        the restore undid, and anything saved from it would be written over
        the restored files, so asking for it -- to read, to write, or to
        save -- raises a plain ``NBTError`` (:meth:`_alive`)."""
        self._alive()
        return self._save

    def _alive(self) -> None:
        """Raise a plain ``NBTError`` when this session's worksite has been
        restored since it was made; nothing without a :attr:`guard`.

        A dead session is not a judgment of the call, so this comes before
        every one -- before a state is checked, a keep-out consulted or the
        save read (:attr:`save`) -- and a script that catches
        :class:`~heaton_voxel.craft.errors.Refused` to carry on past one
        call cannot carry on past it."""
        if self._guard is not None:
            self._guard.live()

    @property
    def blocks_set(self) -> int:
        """Every write this session made, as its :attr:`journal` counts
        them: a set, or a cell of a placement, is one; a fill is the blocks
        it filled; a cell written twice counts twice."""
        return self.journal.total

    def stage(self, name: str):
        """Count the writes made inside a ``with`` block under ``name``::

            with b.stage("pad"):
                b.level_pad(x0, z0, x1, z1, 64)
            with b.stage("roof"):
                b.gable_roof(x0 + 1, z0 + 1, x1 - 1, z1 - 1, 68, "x")
            b.journal.tally()     # {"pad": ..., "roof": ...}

        Stages nest -- the innermost name counts, and leaving it restores
        the one around it -- and the block yields the builder. A stage is
        a label on the :attr:`journal`'s counts, nothing more: it neither
        scopes :meth:`finish` nor undoes anything. ``name`` must be a
        non-empty string; anything else is refused before the block runs.
        """
        if not isinstance(name, str) or not name:
            raise NBTError(f"a stage is named by a non-empty string, not "
                           f"{name!r}")
        return self._staged(name)

    @contextlib.contextmanager
    def _staged(self, name: str):
        outer = self.journal.stage
        self.journal.stage = name
        try:
            yield self
        finally:
            self.journal.stage = outer

    # -- primitives --------------------------------------------------------
    #
    # Each primitive is the write check (:meth:`_vet`) and then the write
    # itself (``_write_*``), which journals it once it is in the save, and
    # :meth:`_commit` is the same two halves over a whole plan: every check
    # over every op first, then the writes. A refused write journals
    # nothing.

    def set(self, x: int, y: int, z: int, block) -> None:
        """Write one exact state (see ``MinecraftSave.set_block``), journaled.

        ``block`` may be a :class:`~heaton_voxel.craft.style.Mix`, and the
        cell gets its pick there, ``block.pick(x, y, z)``: the same block
        whatever this session or any other wrote before.

        Refused, with nothing written, for a state the game does not have
        (:class:`~heaton_voxel.craft.errors.IllegalBlock`) -- any entry of
        a Mix, named, picked or not -- in a chunk that
        is not generated or not finished (see :meth:`_commit`), past the
        top or bottom of the world, or where a worksite's :attr:`guard`
        does not admit it.
        """
        parsed = self._vet((("set", (x, y, z), block),))
        self._write_set(x, y, z, self._drawn(block, parsed, (x, y, z)))

    def place(self, x: int, y: int, z: int, block) -> None:
        """Place with item semantics (see ``MinecraftSave.place_block``).

        Every cell the placement writes is checked as :meth:`set` checks
        its one, before any of them is written, and journaled -- a bed's head
        lies beside its foot, not above it, and may be in the next chunk
        over, which :meth:`finish` must settle too. Every neighbor the
        placement may re-derive is checked as well
        (:meth:`_placement_footprint`): a fence placed beside an unfinished
        chunk would otherwise rewrite the fence standing in it.

        A :class:`~heaton_voxel.craft.style.Mix` is a ``TypeError``: which
        cells a placement writes depends on the block, so it places one --
        a Mix's pick, if it is to be one of those.
        """
        state = _state(block)
        self._vet((("place", (x, y, z), state),))
        self._write_place(x, y, z, state)

    def fill(self, corner1, corner2, block) -> None:
        """Fill a box with one state (see ``MinecraftSave.fill``), journaled.

        Refuses before writing anything a state the game does not have, a
        box reaching a chunk that is missing, unfinished or unreadable, or
        past the top or bottom of the world, and one a worksite's
        :attr:`guard` does not admit: a fill that raised after
        writing would leave work in the save that nothing journaled, so
        :meth:`finish` would never settle it.

        ``block`` may be a :class:`~heaton_voxel.craft.style.Mix`: every
        cell of the box gets its own pick, written cell by cell in ``y``,
        ``z``, ``x`` order and journaled so (:meth:`_sweep`), never as
        ``MinecraftSave.fill`` writes a section of one state at once -- so a
        Mix fill costs per cell, and is checked once, over the box, as any
        fill is.
        """
        parsed = self._vet((("fill", corner1, corner2, block),))
        if isinstance(block, Mix):
            self._sweep(corner1, corner2, block, parsed)
        else:
            self._write_fill(corner1, corner2, self._drawn(block, parsed))

    # -- frames and schematics -----------------------------------------------

    def framed(self, frame) -> Framed:
        """This session seen through ``frame``, a
        :class:`~heaton_voxel.craft.geometry.Frame`: a
        :class:`~heaton_voxel.craft.build.framed.Framed` whose ``set``,
        ``place`` and ``fill`` take a component's own ``(across, up,
        out)`` cells and states, turn them by the frame, and write them as
        this Builder writes -- the same checks, journal, ``rng``, guard and
        :meth:`finish`::

            gate = b.framed(Frame((120, 64, -40), "west"))
            gate.fill((0, 0, 0), (4, 3, 0), "minecraft:stone_bricks")
            gate.set(2, 0, 1, "minecraft:oak_stairs[facing=north]")   # faces east

        Anything but a Frame is an ``NBTError``."""
        return Framed(self, frame)

    def stamp(self, schematic, at, *, rotate: int = 0,
              mirror: str | None = None, paste_air: bool = True,
              entities: bool = True) -> SchemImportResult:
        """Paste ``schematic`` -- a :class:`~heaton_voxel.schem.Schematic`
        or a ``.schem`` path -- into the build, checked and journaled as
        every Builder write is.

        ``at`` is where the schematic's **origin** lands, its stored offset
        honored, as ``//paste`` and ``import_schem(anchor="origin")`` place
        it: a file exported without ``origin=`` has its origin at its
        minimum corner. ``mirror`` (``"x"`` swaps east and west, ``"z"``
        north and south; ``None``, the default, neither) and then
        ``rotate`` (a multiple of 90 degrees, clockwise seen from above)
        turn it about that origin, exactly as ``import_schem``'s ``flip=``
        and ``rotate=`` do, block states, block entities and entities
        alike. So the schematic's own cells, counted from its origin, land
        where ``Frame(at, turn("south", rotate // 90), mirrored=True)``
        puts them with ``mirror="x"`` -- a half turn further with ``"z"``,
        and unmirrored with ``None`` -- turned as that frame's
        :meth:`~heaton_voxel.craft.geometry.Frame.state` turns them: the
        properties each state carries, as ``import_schem`` turns them, so a
        state the schematic stores bare keeps its defaults' facing (a
        schematic the game writes, or one exported from a 26.3 chunk,
        carries every property; :meth:`framed` turns the defaults too).
        There is no upside-down stamp: flip the Schematic itself
        (``flipped("y")``) for that. Biomes are never written.

        It is written by the lower tier's paste, so a stamp and
        ``save.import_schem(schematic, at, rotate=rotate, flip=mirror,
        relight=False, keep_loaded=True)`` encode the same terrain, byte for
        byte: a block entity comes with its block, the stale block entities
        in the box are removed -- stone stamped over a chest takes its
        inventory with it, which ``set``, ``fill`` and ``place`` do not --
        and every entity gets a fresh identity, remapped so the pasted
        copies still name each other. Nothing is relit here;
        :meth:`finish` settles the stamp as it settles any write --
        connections inside it and a block round it (a vanilla-placed state
        is what connect derives anyway; a debug-stick one is not, and
        ``finish(connect=False)`` keeps it), heightmaps, POI, light and the
        audit.

        Before anything is written, every check comes in the order a
        Builder write's do, and a refusal names every fault:

        * the arguments -- ``rotate`` not a multiple of 90, a ``mirror``
          other than ``"x"`` or ``"z"``, a flag that is not a bool, an
          ``at`` that is not three ints -- an ``NBTError`` before the
          schematic is even read, and a schematic made by a newer
          Minecraft than the world's ``level.dat``, which the lower tier
          refuses too;
        * every state the paste writes is one the game has, an
          :class:`~heaton_voxel.craft.errors.IllegalBlock` naming each
          (a 1.20 file's chain, renamed since, is refused with the iron
          chain as the suggestion);
        * in a stamp that turns, each of those states turns: one with a
          direction-looking property the schematic vocabulary does not
          cover (a modded one, under ``allow=``) is
          :class:`~heaton_voxel.craft.errors.Refused`, never left pointing
          the old way -- stamp it unturned, or turn the Schematic yourself
          and read its ``transform_warnings``;
        * every chunk the box reaches, and the cell each entity lands in,
          is generated and finished, and every height inside the world --
          the whole box, air included, even with ``paste_air=False``;
        * **the DataVersion**: the game reads a stored block entity and
          a stored entity by the DataVersion of the chunk holding it, and
          the schematic's carry the schematic's own, so nothing here can
          make one right in a chunk of another version. Refused, each
          named: every block entity the paste would write into a terrain
          chunk whose DataVersion is not the schematic's, whatever
          ``entities=`` says; and, unless ``entities=False``, every entity
          whose entity chunk -- the one already stored there, or the one
          the paste would make at ``level.dat``'s version -- is not at the
          schematic's. ``entities=False`` leaves the entities out, written
          and checked not at all. Blocks are never refused for it: each
          chunk keeps its own palette codec, and the legality check covers
          the states. Entity storage that cannot be read is corruption, a
          plain ``NBTError``;
        * and a worksite's :attr:`guard` admits the box as a block write
          and each entity's cell as a spawn.

        Journaled as written: the whole box with ``paste_air`` on, as a
        fill is, counted as the blocks pasted, and otherwise each cell it
        wrote, air skipped. :attr:`entities_added` counts the entities.
        Returns the lower tier's
        :class:`~heaton_voxel.schem.SchemImportResult`. Draws nothing from
        :attr:`rng`.
        """
        self._alive()
        if isinstance(rotate, bool) or not isinstance(rotate, int) or rotate % 90:
            raise NBTError(f"a stamp turns by a multiple of 90 degrees, "
                           f"clockwise seen from above, not {rotate!r}")
        if mirror is not None and (not isinstance(mirror, str)
                                   or mirror not in ("x", "z")):
            hint = (": turn the build upside down with the Schematic's own "
                    "flipped('y')" if mirror == "y" else
                    "" if isinstance(mirror, str) else
                    ": name the axis it runs along")
            raise NBTError(f"a stamp mirrors along \"x\" (east and west "
                           f"swapped) or \"z\" (north and south), or not at "
                           f"all (None), not {mirror!r}{hint}")
        for name, flag in (("paste_air", paste_air), ("entities", entities)):
            if not isinstance(flag, bool):
                raise NBTError(f"{name} must be True or False, got {flag!r}")
        try:
            ax, ay, az = at
        except (TypeError, ValueError):
            raise NBTError(f"a stamp lands at (x, y, z), got {at!r}") from None
        for value in (ax, ay, az):
            if isinstance(value, bool) or not isinstance(value, int):
                raise NBTError(f"a stamp lands at three ints, got {at!r}")
        if isinstance(schematic, (str, os.PathLike)):
            schematic = Schematic.load(schematic)
        elif not isinstance(schematic, Schematic):
            raise TypeError(f"a stamp is of a Schematic or a .schem path, "
                            f"not {type(schematic).__name__}")
        world = self._world_version()
        if world is not None and schematic.data_version > world:
            raise NBTError(
                f"this schematic was made by a newer Minecraft (DataVersion "
                f"{schematic.data_version}) than the world (DataVersion "
                f"{world}), so its blocks may not exist there; nothing was "
                f"written")

        # The schematic as it lands: flipped, then turned, as the lower
        # tier turns it -- copies, so the caller's is never altered.
        placed = schematic
        if mirror is not None:
            placed = placed.flipped(mirror)
        if rotate % 360:
            placed = placed.rotated(rotate)
        offset = placed.offset
        corner = (ax + offset[0], ay + offset[1], az + offset[2])
        width, height, length = placed.size
        box = (*corner, corner[0] + width - 1, corner[1] + height - 1,
               corner[2] + length - 1)
        states = tuple(state for state in placed.histogram()
                       if paste_air or state.name not in AIR_BLOCKS)
        cells = []
        if entities:
            for entity in placed.entities:
                pos = entity.get("Pos")
                if pos is None or len(pos) != 3:
                    raise NBTError(
                        f"an entity in this schematic "
                        f"({entity.get('id', '?')}) has no position to stand "
                        f"at; stamp it with entities=False, or mend the "
                        f"schematic")
                cells.append(tuple(math.floor(float(pos[i]) + corner[i])
                                   for i in range(3)))
        size = "x".join(str(n) for n in schematic.size)
        turned = [f"mirrored along {mirror}"] if mirror is not None else []
        if rotate % 360:
            turned.append(f"turned {rotate}")
        what = (f"stamp of a {size} schematic at {(ax, ay, az)}"
                + (f", {' and '.join(turned)}" if turned else ""))
        paste = _Paste(placed, corner, box, states, paste_air, entities,
                       tuple(cells))
        op = ("paste", paste)
        self._legal([op], {}, what=what)
        if rotate % 360 or mirror is not None:
            leftovers = directional_leftovers(states)
            if leftovers:
                count = (f"{len(leftovers)} properties, first: {leftovers[0]}"
                         if len(leftovers) > 1 else leftovers[0])
                raise Refused(
                    f"{what} cannot be written: the turn would leave a "
                    f"direction the schematic vocabulary cannot turn ("
                    f"{count}). Stamp it unturned, or turn the Schematic "
                    f"yourself and read its transform_warnings. Nothing was "
                    f"written", faults=leftovers)
        self._reach([op], what=what)
        self._stamp_versions(paste, what=what)
        return self._commit([op], what=what)[0]

    def _world_version(self) -> int | None:
        """The DataVersion ``level.dat`` records, or None without one."""
        level = getattr(self.save, "level", None)
        if level is None:
            return None
        stamp = level.root["Data"].get("DataVersion")
        return None if stamp is None else int(stamp)

    def _stamp_versions(self, paste: _Paste, *, what: str) -> None:
        """Refuse a stamp that would store a block entity or an entity in a
        chunk of another DataVersion than the schematic's
        (:meth:`stamp`): every block entity the paste writes, by the
        terrain chunk it lands in, then -- with its entities on -- every
        entity, by its entity chunk or, where there is none yet, by the
        version the paste would make one at, ``level.dat``'s. One
        :class:`~heaton_voxel.craft.errors.Refused` names them all, block
        entities in cell order and then entities in the schematic's.
        Reads; an entity chunk that cannot be read raises its
        ``NBTError``."""
        schematic = paste.schematic
        version = schematic.data_version
        dim = self.save.dimension(self.dimension)
        width, _, length = schematic.size
        palette = schematic.palette
        cells = schematic.indices()
        x0, y0, z0 = paste.corner
        faults = []
        found: dict[tuple[int, int], int] = {}
        for x, y, z in sorted(schematic.block_entities):
            if not paste.paste_air and palette[
                    cells[(y * length + z) * width + x]].name in AIR_BLOCKS:
                continue        # its cell is not written, so neither is it
            cell = (x0 + x, y0 + y, z0 + z)
            chunk = (cell[0] >> 4, cell[2] >> 4)
            stored = found.get(chunk)
            if stored is None:
                stored = found[chunk] = dim.blocks(*chunk).data_version
            if stored != version:
                identity = schematic.block_entities[(x, y, z)].get("id", "?")
                faults.append(
                    f"the {identity} block entity at {cell}, saved at "
                    f"DataVersion {version}, would be stored in chunk "
                    f"{chunk}, at DataVersion {stored}")
        blocks = len(faults)
        if paste.entities:
            world = self._world_version()
            made = version if world is None else world
            for entity, cell in zip(schematic.entities, paste.entity_cells):
                chunk = (cell[0] >> 4, cell[2] >> 4)
                storage = dim.entity_chunk(*chunk)
                if storage is None:
                    stored, where = made, "a new entity chunk"
                elif storage.data is None:
                    raise NBTError(
                        f"entity storage for chunk {chunk} exists but cannot "
                        f"be read: {storage.parse_error}")
                else:
                    tag = storage.data.get("DataVersion")
                    stored = None if tag is None else int(tag)
                    where = f"the entity chunk {chunk}"
                if stored != version:
                    stamped = ("records no DataVersion" if stored is None
                               else f"at DataVersion {stored}")
                    faults.append(
                        f"the {entity.get('id', '?')} at {cell}, saved at "
                        f"DataVersion {version}, would be stored in {where}, "
                        f"{stamped}")
        if not faults:
            return
        count = (f"{len(faults)} faults, first: {faults[0]}"
                 if len(faults) > 1 else faults[0])
        advice = []
        if blocks:
            advice.append("nothing here can convert a block entity, so stamp "
                          "one into chunks of its own version")
        if len(faults) > blocks:
            advice.append("pass entities=False to leave the entities out")
        raise Refused(
            f"{what} cannot be written: {count}. The game reads a block "
            f"entity or an entity by the DataVersion of the chunk it is "
            f"stored in, so one saved by another version is misread; "
            f"{', and '.join(advice)}. Nothing was written", faults=faults)

    # -- the write path ------------------------------------------------------

    def _commit(self, ops, *, what: str) -> list:
        """Write a plan: every check over all of it, then every write.

        ``ops`` is the plan, in the order it is written -- which is the
        order states enter a section's palette, and so the chunk's bytes:

        * ``("set", (x, y, z), block)`` -- one exact state, as :meth:`set`;
        * ``("fill", corner1, corner2, block)`` -- a box, as :meth:`fill`,
          corners in either order;
        * ``("place", (x, y, z), block)`` -- with item semantics, as
          :meth:`place`, every cell of it (a bed's head, a door's top);
        * ``("paste", paste)`` -- a schematic, a :class:`_Paste` as
          :meth:`stamp` plans one, written by the lower tier's paste
          (``save.import_schem``), so its block entities and entities come
          with it: its states checked as any op's, its whole box reached
          and admitted as a block write, and the cell each of its entities
          lands in reached as a set's cell and admitted as a spawn
          (:meth:`_entity_boxes`). It writes a section as the lower tier
          does -- whole, or slot by slot, then pruned -- which is not the
          order the same blocks set one by one would give the palette;
        * ``("ground", (x, z), y)`` -- no write: the session learns the
          column's new ground (:meth:`set_ground`), or for ``y=None``
          forgets what it knew of it, the height it set and the probe it
          read alike (:meth:`forget_ground`), when the writes before it in
          the plan have been made.

        A block may be a :class:`~heaton_voxel.craft.build._common._Mix`,
        drawn from :attr:`rng` as its op is written, so the draws come in
        write order and a refused plan draws nothing. Or it may be a
        :class:`~heaton_voxel.craft.style.Mix`, picked at its cell -- a
        set's own, and each of a fill's, the fill swept cell by cell
        (:meth:`_sweep`) -- which reads no rng and nothing written before:
        the same plan writes the same blocks in any session. A placement
        of either is refused, a ``_Mix`` with an ``NBTError`` and a ``Mix``
        with a ``TypeError``, since which cells it writes depends on which
        block it is.

        Before the first write the whole plan is checked (:meth:`_vet`).
        Every state it names must parse, a malformed one raising the
        parser's own ``ValueError``, and be one the game has
        (:meth:`_legal`): an id it lacks, or a property or a value it does
        not take, is an :class:`~heaton_voxel.craft.errors.IllegalBlock`
        naming every such state the plan holds -- a mix's entries by name,
        never by drawing one. Every chunk an op reaches -- a
        placement's re-derived neighbors included -- must be generated and
        **finished**, ``Status`` full, since the game generates over
        whatever is written into a chunk it has not finished, and a
        proto-chunk's blocks are not yet the ground they will be. And every
        y must be inside the world that chunk holds. A plan failing any of
        it is refused whole, a
        :class:`~heaton_voxel.craft.errors.Refused` naming every fault (one
        per bad chunk, and the y span past each world range), opened by
        ``what``; nothing is written and nothing is drawn. A chunk that
        cannot be read is corruption, not a refusal, and its own
        :class:`~heaton_voxel.nbt.NBTError` comes through; so does water a
        placement would take, at a level or with a ``waterlogged`` the
        game does not have, read before the first write
        (:meth:`_displaced`) as ``place_block`` reads it. Last, a
        worksite's :attr:`guard` admits the plan (:meth:`_admit`), or
        refuses it the same way.

        Then the ops are written in list order, through the same writes
        the primitives make, so a plan and the primitive calls it lists
        encode byte for byte alike. Returns what each paste wrote, the
        lower tier's :class:`~heaton_voxel.schem.SchemImportResult`, one
        per paste op in plan order: empty for a plan with none.
        """
        ops = list(ops)
        parsed = self._vet(ops, what=what)
        pasted = []
        for op in ops:
            kind = op[0]
            if kind == "set":
                self._write_set(*op[1], self._drawn(op[2], parsed, op[1]))
            elif kind == "fill":
                if isinstance(op[3], Mix):
                    self._sweep(op[1], op[2], op[3], parsed)
                else:
                    self._write_fill(op[1], op[2], self._drawn(op[3], parsed))
            elif kind == "place":
                self._write_place(*op[1], _state(self._drawn(op[2], parsed)))
            elif kind == "paste":
                pasted.append(self._write_paste(op[1]))
            else:                       # "ground", the one other kind _vet allows
                column, height = op[1], op[2]
                if height is None:
                    self._ground.pop(column, None)
                    self._probes.pop(column, None)
                else:
                    self._ground[column] = height
        return pasted

    def _vet(self, ops, *, what: str | None = None) -> dict:
        """Every check a write must pass, over every op of ``ops``, in one
        fixed order and before anything is written: whether its states
        parse (:meth:`_parse`), whether they are legal (:meth:`_legal`),
        whether the chunks and heights it reaches can be built in
        (:meth:`_reach`), whether the water its placements displace is
        water the game has (:meth:`_displaced`), and whether it is admitted
        where it writes (:meth:`_admit`). ``what`` opens a refusal; without
        one, the op itself is described. Returns what :meth:`_parse` made
        of the strings, so the writes need not parse them again. A session
        whose worksite was restored raises a plain ``NBTError`` before any
        of them (:meth:`_alive`)."""
        self._alive()
        parsed = self._parse(ops)
        self._legal(ops, parsed, what=what)
        self._reach(ops, what=what)
        self._displaced(ops, parsed)
        self._admit(ops, what=what)
        return parsed

    def _displaced(self, ops, parsed) -> None:
        """Read the water each placement of ``ops`` takes, as
        ``place_block`` reads it when the placement is written
        (``place._with_water``), so that a placement displacing water at a
        level, or a ``waterlogged``, the game does not have raises that
        ``NBTError`` before the plan's first write rather than after the
        ops ahead of it are in the save. Only a block that can take water,
        placed with ``waterlogged`` unsaid, reads it. A cell an op earlier
        in the plan writes is not read, since the placement will find that
        op's state there, which :meth:`_legal` has passed: a set's cell, a
        fill's box, a placement's own cells, and a paste's box when it
        writes its air. The neighbors a placement re-derives keep their
        water, and a paste leaving its air unwritten keeps the world's,
        so those are read as the world holds them."""
        points: set = set()
        boxes: list = []

        def world(x: int, y: int, z: int):
            if (x, y, z) in points or any(
                    x0 <= x <= x1 and y0 <= y <= y1 and z0 <= z <= z1
                    for x0, y0, z0, x1, y1, z1 in boxes):
                return None             # an earlier op's state: not read
            return self.save.get_block(x, y, z, dimension=self.dimension)

        for op in ops:
            kind = op[0]
            if kind == "set":
                points.add(tuple(int(v) for v in op[1]))
            elif kind == "fill" or (kind == "paste" and op[1].paste_air):
                boxes.extend(self._boxes(op))
            elif kind == "place":
                state = _state(self._drawn(op[2], parsed))
                x, y, z = (int(v) for v in op[1])
                parts = _cells_for(state, x, y, z)
                _with_water(parts, state, world)
                points.update(cell for cell, _ in parts)

    def _reach(self, ops, *, what: str | None = None) -> None:
        """Refuse ``ops`` where a chunk they reach is not generated or not
        finished, or a height they write is past the world
        (:meth:`_write_faults`): the one of :meth:`_vet`'s checks a writer
        can run before it has decided its whole plan, over every cell the
        plan could write -- a floor whose lamps the light plan has still to
        place, before its surface is drawn -- and the same refusal, word
        for word, that :meth:`_commit` would give the plan."""
        faults = self._write_faults(ops)
        if faults:
            if what is None:
                what = _describe(ops[0])
            count = (f"{len(faults)} faults, first: {faults[0]}"
                     if len(faults) > 1 else faults[0])
            raise Refused(f"{what} cannot be written: {count}. Nothing was "
                          f"written", faults=faults)

    def _vet_plan(self, ops, *, what: str, names=()) -> None:
        """:meth:`_vet`'s checks of ``ops`` that need no reading of the
        world -- states parsed and legal, chunks and heights reachable --
        for a rule writer that must read the cells its rule needs before it
        knows what to write (:meth:`spiral_stair`, :meth:`vault`,
        :meth:`railway`): the call is refused as :meth:`_commit` would
        refuse it, every fault named, rather than by the first read that
        finds nothing there. ``ops`` is every cell the writer reads and may
        write, each with the state it would write there; ``names`` are
        further blocks it may write in cells it cannot name yet -- a
        railway's seal -- checked for legality alone. The guard is left to
        :meth:`_commit`, since a cell read and not written is not its to
        admit."""
        self._alive()
        named = list(ops) + [("set", None, block) for block in names
                             if block is not None]
        self._legal(named, self._parse(named), what=what)
        self._reach(ops, what=what)

    def _audit_plan(self, ops, *, what: str) -> None:
        """Refuse ``ops`` -- a plan, as :meth:`_commit` takes one -- when
        writing it would leave a block the game will not keep, or a door or
        a chest nobody can open, that was not so before. The one survival
        audit a writer runs before it writes; it reads, and writes and
        draws nothing.

        :func:`~heaton_voxel.craft.survey.unsupported` runs twice over the
        same boxes -- each chunk's share of the cells the plan writes, two
        blocks round them (``_AUDIT_REACH``), clipped to the chunk, as
        :meth:`finish` audits what a session wrote -- once over the world
        as it stands and once with the plan laid over it: a ``set``'s
        cell, a ``fill``'s box, and every cell of a ``place`` (a bed's
        head, a door's top), a later op over an earlier one as the writes
        would leave it. Both read every block as the game loads it, a
        property a state leaves out at its default (a bare ``wall_torch``
        faces north). A fault in the second is the plan's unless the same
        block stood in its cell with a fault of that kind in the first, so
        a torch somebody left hanging on nothing beside the build is not
        the build's to answer for, while a wall cut from behind one is, and
        so is a lantern the plan sets where that torch was. A door's two
        faults rank, ``"sealed door"`` over ``"one-way door"``
        (``_DOOR_RANK``): a door is the plan's fault where the plan shuts
        it further -- a two-way door made one-way, a one-way door sealed,
        a door hung that opens from one side or none -- and not where it
        leaves it as it was or opens it more. That is stricter than
        :meth:`finish`, which never refuses a one-way door.

        Every such fault is named in one
        :class:`~heaton_voxel.craft.errors.Refused`, opened by ``what``,
        its :attr:`~heaton_voxel.craft.errors.Refused.faults` the
        :class:`~heaton_voxel.craft.survey.Unsupported` list in cell
        order. There is no bypass: the caller moves the torch or the
        opening. A chunk beside the plan, written by none of it, that
        cannot be read is not audited, as :meth:`finish` leaves it; one
        the plan writes in raises its ``NBTError``. The plan is read as
        :meth:`_plan_cells` reads it: a
        :class:`~heaton_voxel.craft.build._common._Mix`, drawn as it is
        written, has no block to audit yet, and is an ``NBTError``; a
        :class:`~heaton_voxel.craft.style.Mix` is audited as each cell's
        pick, which is the block that cell will be written with; and a
        placement as ``place_block`` will write it, its water and all.
        """
        self._alive()
        plan = self._plan_cells(ops, what=what)
        if not plan:
            return
        # The plan by chunk, and the bounding box of its cells in each: the
        # boxes the journal would hold once it was written.
        by_chunk: dict[tuple[int, int], dict] = {}
        written: dict[tuple[int, int], list[int]] = {}
        for cell in plan:
            key = (cell[0] >> 4, cell[2] >> 4)
            by_chunk.setdefault(key, {})[cell] = plan[cell]
            box = written.get(key)
            if box is None:
                written[key] = [*cell, *cell]
            else:
                for i in range(3):
                    box[i] = min(box[i], cell[i])
                    box[i + 3] = max(box[i + 3], cell[i])
        dim = self.save.dimension(self.dimension)
        before: list = []
        after: list = []
        for chunk, (c1, c2) in _margin_boxes(written, _AUDIT_REACH).items():
            if chunk not in written:
                try:
                    dim.blocks(*chunk)
                except NBTError:
                    continue
            # The plan's cells the audit of this box can read: none of its
            # checks looks farther than _AUDIT_REACH from the block it
            # checks, so this chunk and the eight round it hold them all.
            lo = [v - _AUDIT_REACH for v in c1]
            hi = [v + _AUDIT_REACH for v in c2]
            overlay = {cell: state
                       for dx in (-1, 0, 1) for dz in (-1, 0, 1)
                       for cell, state in by_chunk.get(
                           (chunk[0] + dx, chunk[1] + dz), {}).items()
                       if lo[0] <= cell[0] <= hi[0] and lo[1] <= cell[1] <= hi[1]
                       and lo[2] <= cell[2] <= hi[2]}
            before.extend(unsupported(self.save, c1, c2,
                                      dimension=self.dimension))
            after.extend(unsupported(self.save, c1, c2, overlay=overlay,
                                     dimension=self.dimension))
        # A fault is keyed by its block as well as its cell, so one the plan
        # writes over with something else -- a lantern for a torch already
        # on nothing -- is the plan's, while the same block written back is
        # not.
        seen = {(fault.cell, fault.kind, fault.block) for fault in before}
        rank: dict = {}
        for fault in before:
            if fault.kind in _DOOR_RANK:
                key = (fault.cell, fault.block)
                rank[key] = max(rank.get(key, 0), _DOOR_RANK[fault.kind])
        new = [fault for fault in after
               if (_DOOR_RANK[fault.kind] > rank.get((fault.cell, fault.block), 0)
                   if fault.kind in _DOOR_RANK
                   else (fault.cell, fault.kind, fault.block) not in seen)]
        if not new:
            return
        new.sort(key=lambda fault: fault.cell)
        body = (f"{len(new)} blocks the game will not keep or nobody can "
                f"open, first: {new[0]}" if len(new) > 1 else
                f"a block the game will not keep or nobody can open: {new[0]}")
        raise Refused(f"{what} would leave {body}. Nothing was written",
                      faults=new)

    def _refuse_built(self, ops, *, what: str, overwrite: bool = False,
                      paved=frozenset()) -> None:
        """Refuse earthwork that would take what somebody built: every cell
        ``ops`` write that holds something :func:`_takes` does not take,
        unless it is already what the plan leaves there. Water and what
        goes with it are the water check's, so it is taken here. ``paved``
        is columns this session laid a path in, a road's own to cross. A
        keep-out lasts a session, and the column reader stands on a roof as
        it stands on a hill, so this reads the cells themselves and is what
        keeps a later session out of a tower it was never told of.

        Plans whose ``surface`` is drawn as it is written have no block to
        compare, so a cell of one holding something built is taken for
        somebody's. Reads only; ``overwrite=True`` skips it."""
        if overwrite:
            return
        taken: list = []
        seen: set = set()
        for op in ops:
            kind = op[0]
            if kind not in ("set", "fill"):
                continue
            block = op[3] if kind == "fill" else op[2]
            mix = block if isinstance(block, Mix) else None
            drawn = isinstance(block, _Mix)
            state = None if mix is not None or drawn else _state(block)
            for x0, y0, z0, x1, y1, z1 in self._boxes(op):
                for x in range(x0, x1 + 1):
                    for z in range(z0, z1 + 1):
                        if (x, z) in paved:
                            continue
                        for y in range(y0, y1 + 1):
                            cell = (x, y, z)
                            if cell in seen:
                                continue
                            seen.add(cell)
                            found = self._peek(cell)
                            if found is None or _takes(found, wet=True):
                                continue
                            wanted = (_state(mix.pick(x, y, z))
                                      if mix is not None else state)
                            if wanted is not None and _same_crown(found,
                                                                  wanted):
                                continue
                            taken.append(f"{cell} holds {found}")
        if taken:
            count = (f"{len(taken)} cells, first: {taken[0]}"
                     if len(taken) > 1 else taken[0])
            raise Refused(
                f"{what} would take what somebody built -- {count}. Move it, "
                f"or pass overwrite=True to take it all the same. Nothing "
                f"was written", faults=taken)

    def _plan_cells(self, ops, *, what: str, reader: str = "audit") -> dict:
        """``{(x, y, z): BlockState}``: every cell ``ops`` -- a plan, as
        :meth:`_commit` takes one -- writes, and the state the writes leave
        it in, a later op over an earlier one: a ``set``'s cell, every cell
        of a ``fill``'s box, and every cell of a ``place`` (a bed's head, a
        door's top). What :meth:`_audit_plan` audits and
        :meth:`_contain_plan` checks the water of; it reads only what a
        placement takes its water from, and writes and draws nothing.

        A placement is planned as ``place_block`` will write it
        (``place._with_water``), over whatever the plan before it, or else
        the world, holds in its cells when it is written: a block that can
        take water, its ``waterlogged`` left unsaid, takes the water there
        that its own rule takes -- the source alone for nearly every block,
        and for a coral or the conduit falling water too, for the small
        dripleaf any water -- and a block wet by default is planned dry
        where it takes none. A campfire placed into water it takes is out.
        Water at a level, or a ``waterlogged``, the game does not have is
        the ``NBTError`` it would be when the placement is written. What
        ``place_block`` then re-derives from the neighbors -- a fence's
        arms, a stair's shape, on the placed cells and beside them -- is
        planned as the ops give it, not derived.

        A :class:`~heaton_voxel.craft.style.Mix` is each cell's pick, the
        block the write will pick there. A
        :class:`~heaton_voxel.craft.build._common._Mix`, drawn as it is
        written, has no block yet, and a paste's blocks are a
        schematic's: either is an ``NBTError`` opened by ``what``, naming
        ``reader`` -- ``"audit"`` (the survival audit) or ``"water"``
        (the water check) -- as the check that cannot read it.
        """
        name, later = _PLAN_READERS[reader]
        ops = list(ops)
        parsed = self._parse(ops)
        plan: dict[tuple[int, int, int], BlockState] = {}

        def holds(x: int, y: int, z: int):
            # What a placement finds in its cell as it is written: the
            # plan's state there, ahead of it, else the world's.
            found = plan.get((x, y, z))
            if found is not None:
                return found
            return self.save.get_block(x, y, z, dimension=self.dimension)

        for op in ops:
            kind = op[0]
            if kind == "ground":
                continue
            if kind == "paste":
                raise NBTError(f"{what}: {name} reads the blocks a writer "
                               f"plans, and a paste's are a schematic's"
                               f"{later}")
            if kind not in ("set", "fill", "place"):
                self._boxes(op)        # no other kind writes: its refusal
            block = op[3] if kind == "fill" else op[2]
            if isinstance(block, _Mix):
                raise NBTError(f"{what}: {name} reads the blocks a plan "
                               f"writes, and a mix drawn as it is written has "
                               f"none to read yet")
            if isinstance(block, Mix):
                # Picked at each cell, as the write will pick it.
                for x0, y0, z0, x1, y1, z1 in self._boxes(op):
                    for y in range(y0, y1 + 1):
                        for z in range(z0, z1 + 1):
                            for x in range(x0, x1 + 1):
                                plan[(x, y, z)] = _state(self._drawn(
                                    block, parsed, (x, y, z)))
                continue
            state = _state(parsed[block] if isinstance(block, str) else block)
            if kind == "place":
                x, y, z = (int(v) for v in op[1])
                # Every cell's water read before any of them is planned, as
                # place_block reads it before it writes.
                for cell, part in _with_water(_cells_for(state, x, y, z),
                                              state, holds):
                    plan[cell] = part
                continue
            for x0, y0, z0, x1, y1, z1 in self._boxes(op):
                for y in range(y0, y1 + 1):
                    for z in range(z0, z1 + 1):
                        for x in range(x0, x1 + 1):
                            plan[(x, y, z)] = state
        return plan

    def _seal_state(self, seal, *, what: str):
        """``seal`` -- a block to wall water off with, or None -- as the
        one exact state it writes, checked before anything is read, as
        :meth:`_contain_plan` checks it and a writer that reads the
        ground before it plans checks it first: not a
        :class:`~heaton_voxel.craft.style.Mix` or a
        :class:`~heaton_voxel.craft.style.Material` (a ``TypeError``),
        one the game has (:class:`~heaton_voxel.craft.errors.IllegalBlock`
        opened by ``what``), whole on every face to water and nothing water
        can enter, and one that stays a wall (:func:`_seal_gives_way`); an
        ``NBTError`` naming why otherwise. None for no seal. The one seal
        rule: grade's, road's and a railway's seals are all held to it."""
        if seal is None:
            return None
        refuse_many(seal)
        self._vet_names((seal,), what=what)
        state = _state(seal)
        if not _walls_off_water(state):
            raise NBTError(f"seal={state} would not hold water back: a seal "
                           f"is a full block water can neither pass, enter "
                           f"nor waterlog, such as minecraft:cobblestone")
        gives = _seal_gives_way(state)
        if gives is not None:
            raise NBTError(f"seal={state} would not stay a wall: {gives}; a "
                           f"seal is a full block that stays put, such as "
                           f"minecraft:cobblestone")
        return state

    def _contain_plan(self, ops, *, what: str, seal=None, spill=(),
                      sealing: bool = True,
                      spilling: str | None = None) -> list:
        """Refuse ``ops`` -- a plan, as :meth:`_commit` takes one -- where
        writing it would let water out, or wall the water off where it
        stands with ``seal``. The one water check a writer runs over its
        plan before it writes: it reads, and writes and draws nothing.

        The plan's cells (:meth:`_plan_cells`) are laid over the world and
        :func:`~heaton_voxel.craft.survey.contained` follows the game's
        first step of spread from every cell of water near them: each
        :class:`~heaton_voxel.craft.survey.Leak` is a cell the water would
        run, fall or waterlog into that it does not today -- a pond's bank
        cut open, a tub with a gap in it, a source set beside a stale rim.
        ``spill`` is world cells it may run into, as ``contained`` takes
        them (a fountain's basin, the fall from an aqueduct's open end):
        world cells, from every writer that takes one -- an extrusion's
        ``spill=``, and a furnishing's, each piece's own cells placed with
        it. ``sealing=False`` is a writer with no ``seal=`` (furnish), whose
        refusal says to keep clear of the water rather than to seal it, and
        ``spilling`` the argument a writer takes its spill by, which a leak
        of the plan's own water is pointed at.

        With no ``seal``, any leak refuses the plan: one
        :class:`~heaton_voxel.craft.errors.Refused`, opened by ``what``,
        whose :attr:`~heaton_voxel.craft.errors.Refused.faults` are every
        ``Leak``, in cell order. With one -- a block, the wall -- every
        leak's water is walled off where it stands: a ``("set", source,
        seal)`` for each :attr:`~heaton_voxel.craft.survey.Leak.source`,
        in cell order, which is railway's pattern. They are returned for
        the caller to write after its plan, run its keep-outs and its
        audit over, and count; a plan that keeps its water returns none.
        The plan with them is checked again, since a seal can open a way
        of its own -- falling water over a sealed cell turns to its sides
        -- and anything still getting out is the same refusal. Two leaks
        are never sealed: **lava** touching the water (it turns to stone
        where they meet, and a seal in the water leaves the lava where it
        is) and water running into a chunk that is not there
        (``"ungenerated"``: nothing can be said of where it goes); either
        refuses the plan, ``seal`` or not, naming them. Nor is water the
        plan writes itself sealed: the seal would write over the plan. And
        a seal replaces water alone -- the water itself, or the blocks
        that are water at every state, kelp, seagrass and a bubble column
        (``WATER_FLUIDS``) -- so a leak whose water is held by anything
        else standing in it, a waterlogged chest, stair or lichen, refuses
        the plan, one :class:`~heaton_voxel.craft.errors.Refused` naming
        every such source and what stands there, rather than writing the
        seal over it.

        ``seal`` is checked before anything is read: one exact state (a
        :class:`~heaton_voxel.craft.style.Mix` or a
        :class:`~heaton_voxel.craft.style.Material` is a ``TypeError``),
        one the game has (:class:`~heaton_voxel.craft.errors.IllegalBlock`),
        one water cannot pass or enter -- whole on every face to water
        (``block_facts``' ``collision``) and no block water can waterlog or
        wash away, which leaves none holding a fluid -- and one that stays
        a wall: nothing that falls (sand, gravel, concrete powder: a seal
        stands where water stood) and none of ``_SEAL_GIVES_WAY``, ice and
        frosted ice, which melt, and a sponge, which soaks the water up; an
        ``NBTError`` naming why otherwise. What the seals then stand beside,
        and whether they stand in a keep-out, is the caller's to check, with
        :meth:`_audit_plan` and :meth:`_keep_out`. The game rule
        ``WATER_SOURCE_CONVERSION`` is taken as on, its default, as
        ``contained`` takes it.
        """
        self._alive()
        seal_state = self._seal_state(seal, what=what)
        plan = self._plan_cells(ops, what=what, reader="water")
        if not plan:
            return []
        leaks = contained(self.save, plan, spill=spill,
                          dimension=self.dimension).leaks
        if not leaks:
            return []
        # A seal replaces water alone: a block standing in the water is
        # somebody's, and the seal would be written over it. Read for
        # every leak a seal could mend, so a refusal never offers a seal
        # where it would be refused.
        held = {}
        for leak in leaks:
            source = leak.source
            if (leak.kind in _UNSEALABLE or source in plan
                    or source in held):
                continue
            found = self.save.get_block(*source, dimension=self.dimension)
            if found is not None and found.name not in WATER_FLUIDS:
                held[source] = found
        if seal is None:
            _leaks_refused(leaks, plan, what=what, held=held,
                           sealing=sealing, spilling=spilling)
        # What no seal mends, in the order contained gives it: lava, a chunk
        # not there, and water the plan writes itself.
        unsealed = [leak for leak in leaks
                    if leak.kind in _UNSEALABLE or leak.source in plan]
        if unsealed:
            _leaks_refused(unsealed, plan, what=what, seal=seal_state,
                           spilling=spilling)
        sources = sorted({leak.source for leak in leaks})
        if held:
            buried = [f"{source} holds {held[source]}, which the seal would "
                      f"write over" for source in sorted(held)]
            count = (f"{len(buried)} cells, first: {buried[0]}"
                     if len(buried) > 1 else buried[0])
            raise Refused(f"{what} would seal water off over what stands in "
                          f"it -- {count}. A seal replaces the water alone, "
                          f"so keep the plan clear of that water. Nothing "
                          f"was written", faults=buried)
        seals = [("set", source, seal) for source in sources]
        sealed = dict(plan)
        for _, cell, _ in seals:
            sealed[cell] = seal_state
        left = contained(self.save, sealed, spill=spill,
                         dimension=self.dimension).leaks
        if left:
            _leaks_refused(left, sealed, what=what, seal=seal_state,
                           again=True, spilling=spilling)
        return seals

    def _split_faults(self, removed) -> list[str]:
        """Whether taking the water out of ``removed`` -- ``(x, y, z)``
        cells a writer fills where water stood -- cuts a body of water in
        two: a fault for every piece of the fill that does, or whose water
        could not be followed far enough to say, and none when every piece
        leaves its water whole. The one split check, for the writers that
        fill across water on a route the Builder lays (:meth:`road`, and
        :meth:`extrude`'s band, filled from the bed or bridged into the
        water), and refused only by them, each unless ``allow_dam=True``:
        a grade's outline is the caller's, and may cut a pond as it likes,
        as a basin's may. It reads, and writes and draws nothing.

        The water beside each piece of the fill -- the cells joined to one
        another through it -- is the water it joins today. With the fill
        laid over the world as solid ground, that water is followed
        (:func:`~heaton_voxel.craft.survey.water_body`) from its first
        cell, first within :data:`_SPLIT_MARGIN` columns of the fill --
        the box round all of it, every piece together -- and, where that
        does not reach it all, as far as :data:`_SPLIT_LIMIT` cells. Every
        cell of it reached, the water is whole -- a jetty into a lake,
        round whose end the lake still joins. One not reached once the
        water has been followed to its end is a split: a dam across a
        canal, and the water on the far side of it. And water
        followed into a chunk that is not there to read, or past the
        limit, is a fault of its own, since nothing can be said of how it
        joins there -- a refusal it may not need, never a split missed."""
        removed = {tuple(int(v) for v in cell) for cell in removed}
        if not removed:
            return []
        solid = BlockState("minecraft:stone")
        overlay = dict.fromkeys(removed, solid)
        faults = []
        # The fill's pieces, each the cells joined through one another:
        # water beside one piece was joined through it.
        seen: set = set()
        for start in sorted(removed):
            if start in seen:
                continue
            piece, work = [], [start]
            seen.add(start)
            while work:
                cell = work.pop()
                piece.append(cell)
                x, y, z = cell
                for near in ((x + 1, y, z), (x - 1, y, z), (x, y + 1, z),
                             (x, y - 1, z), (x, y, z + 1), (x, y, z - 1)):
                    if near in removed and near not in seen:
                        seen.add(near)
                        work.append(near)
            shore = set()
            for x, y, z in piece:
                for near in ((x + 1, y, z), (x - 1, y, z), (x, y, z + 1),
                             (x, y, z - 1)):
                    if near in removed or near in shore:
                        continue
                    found = self.save.get_block(*near, dimension=self.dimension)
                    if found is not None and holds_water(found.name,
                                                         found.properties):
                        shore.add(near)
            if len(shore) < 2:
                continue
            shore = sorted(shore)
            fault = self._split_fault(min(piece), shore, overlay)
            if fault is not None:
                faults.append(fault)
        return faults

    def _split_fault(self, first, shore, overlay):
        """The fault, or None, for one piece of a fill
        (:meth:`_split_faults`): ``first`` its first cell, ``shore`` the
        water beside it, sorted, and ``overlay`` the whole fill as solid
        ground."""
        xs = [cell[0] for cell in overlay]
        zs = [cell[2] for cell in overlay]
        box = ((min(xs) - _SPLIT_MARGIN, min(zs) - _SPLIT_MARGIN),
               (max(xs) + _SPLIT_MARGIN, max(zs) + _SPLIT_MARGIN))
        lost = (f"the water beside the fill at {first} could not be followed "
                f"far enough to see whether the fill cuts it in two")
        for within in (box, None):
            try:
                body = water_body(self.save, shore[0], within=within,
                                  overlay=overlay, dimension=self.dimension,
                                  limit=_SPLIT_LIMIT)
            except Refused:
                return (f"{lost}: it runs on past {_SPLIT_LIMIT:,} cells of "
                        f"water and ice")
            apart = [cell for cell in shore if cell not in body.cells]
            if not apart:
                return None
            if within is not None and not (body.clipped or body.missing):
                break                   # followed to its end inside the box
        if body.missing:
            chunks = ", ".join(str(chunk) for chunk in body.missing)
            return (f"{lost}: it reaches chunks that are not there to read, "
                    f"{chunks}")
        return (f"the fill at {first} cuts the water in two: {shore[0]} and "
                f"{apart[0]}, joined through it today, are joined nowhere "
                f"else, so the water on one side is a pond the other side no "
                f"longer feeds")

    @staticmethod
    def _parse(ops) -> dict:
        """Parse every state ``ops`` name -- each entry of a mix too, by
        name (:func:`_named`), picking and drawing nothing -- before
        anything is written, so a malformed one late in a plan raises the
        parser's ``ValueError`` while the save is untouched, not after the
        plan's first writes are in it. Each distinct string is parsed once;
        returns ``{string: BlockState}``.

        A block that is none of a name, a
        :class:`~heaton_voxel.blocks.BlockState` and a mix is a
        ``TypeError`` (a :class:`~heaton_voxel.craft.style.Material` with
        the hint to pass one of its forms), and so is a placement of a
        :class:`~heaton_voxel.craft.style.Mix` (:func:`_one_placement`):
        the first of the checks, so nothing has been read when either is
        raised."""
        parsed: dict[str, BlockState] = {}
        for op in ops:
            kind = op[0]
            if kind == "set":
                block = op[2]
            elif kind == "place":
                block = op[2]
                _one_placement(block)
            elif kind == "fill":
                block = op[3]
            else:
                continue
            for name in _named(block):
                if isinstance(name, BlockState):
                    continue
                if not isinstance(name, str):
                    refuse_many(name)
                    hint = (": a list of (block, weight) pairs is a mix only "
                            "where a writer draws one (road's and pave's "
                            "mix=); elsewhere make it a Mix, which is picked "
                            "by position"
                            if isinstance(name, (list, tuple)) else "")
                    raise TypeError(f"a block is a BlockState, a string or a "
                                    f"Mix, not {type(name).__name__}: "
                                    f"{name!r}{hint}")
                if name not in parsed:
                    parsed[name] = BlockState.parse(name)
        return parsed

    def _legal(self, ops, parsed: dict, *, what: str | None) -> None:
        """Refuse ``ops`` unless every state they write is one the game has
        (:func:`~heaton_voxel.craft.blockinfo.check_state`, under
        :attr:`allow`): one :class:`~heaton_voxel.craft.errors.IllegalBlock`
        naming **every** state that is not, in the order the plan first
        names them, with each one's faults as ``check_state`` words them --
        a renamed id's replacement, the nearest real spellings, the values
        a property takes -- and all their ``.suggestions`` gathered, best
        first. ``what`` opens it; without one, the first op is described.

        Pure: it reads nothing and draws nothing, so a refusal leaves the
        save and :attr:`rng` exactly as they were. A mix -- drawn or
        picked -- is checked entry by entry, by name (:func:`_named`), so an
        entry no cell happens to pick is refused all the same. ``parsed`` is
        :meth:`_parse`'s, so the check is on the parsed
        :class:`~heaton_voxel.blocks.BlockState`: each distinct one is
        checked once a call, and one that passes is not checked again this
        session (:meth:`_illegal`). A paste is checked over every state it
        writes (:class:`_Paste`), each by name as a set's block is."""
        refused: dict[BlockState, IllegalBlock] = {}
        seen = set()
        for op in ops:
            kind = op[0]
            if kind in ("set", "place", "paste"):
                block = op[1] if kind == "paste" else op[2]
            elif kind == "fill":
                block = op[3]
            else:
                continue
            for name in _named(block):
                if name in seen:
                    continue
                seen.add(name)
                state = parsed[name] if isinstance(name, str) else name
                if state in refused:
                    continue
                exc = self._illegal(state)
                if exc is not None:
                    refused[state] = exc
        if not refused:
            return
        if what is None:
            what = _describe(ops[0])
        found = list(refused.values())
        body = (str(found[0]) if len(found) == 1 else
                f"{len(found)} states the game does not have "
                f"({', '.join(str(state) for state in refused)}), first: "
                f"{found[0]}")
        raise IllegalBlock(
            f"{what} cannot be written: {body}. Nothing was written",
            [fault for exc in found for fault in exc.faults],
            suggestions=dict.fromkeys(
                suggestion for exc in found for suggestion in exc.suggestions))

    def _vet_names(self, blocks, *, what: str) -> None:
        """Parse ``blocks`` -- names, states, :class:`_Mix` or
        :class:`~heaton_voxel.craft.style.Mix` -- and check
        them as :meth:`_commit` will (:meth:`_legal`), before there is a
        plan to commit. For a writer that draws from :attr:`rng` while it
        plans, before it draws: a refusal must leave the rng where it was,
        so that a session which catches it and carries on builds what it
        would have built without the call. And for one that has to say
        why a block is unusable before it plans at all -- a lamp dark
        because the game has no such block -- naming with it every other
        block of the call the game lacks, in the writer's words, as the
        plan's own check would. Pure, as the check is; a None is
        skipped. A dead session raises first (:meth:`_alive`)."""
        self._alive()
        ops = [("set", None, block) for block in blocks if block is not None]
        self._legal(ops, self._parse(ops), what=what)

    def _illegal(self, state: BlockState) -> IllegalBlock | None:
        """Why the game does not have ``state``, as
        :func:`~heaton_voxel.craft.blockinfo.check_state` refuses it under
        :attr:`allow`; None when it does. A state that passes is remembered
        for the session, so each is checked once."""
        if state in self._legal_seen:
            return None
        try:
            check_state(state, allow=self._allow)
        except IllegalBlock as exc:
            return exc
        self._legal_seen.add(state)
        return None

    def _admit(self, ops, *, what: str | None) -> None:
        """Whether ``ops`` may write where they write, by this session's
        :attr:`guard`; a Builder without one writes wherever the checks
        before this allow. The last check a write passes, after its states,
        chunks and heights (:meth:`_vet`).

        The guard is given every box the plan writes -- a set's cell, a
        fill's box, a placement's cells and the neighbors it may re-derive
        (:meth:`_placement_footprint`) -- and a plan any of them is refused
        for is refused whole, a :class:`~heaton_voxel.craft.errors.Refused`
        naming every fault (one per file the worksite's snapshot does not
        hold, one per protected zone), opened by ``what``. Nothing is
        written, and a :class:`~heaton_voxel.craft.build._common._Mix` the
        plan draws as it writes is not drawn -- and a writer that drew while
        it planned (:meth:`pave`'s surface, :meth:`ornamental_tree`'s trunk)
        puts :attr:`rng` back when refused. A guard whose worksite has been
        restored raises a plain ``NBTError`` instead: that session is
        over.

        A paste's entities are admitted as well, each cell as a spawn
        (:meth:`_entity_boxes`): its entities file must be one the snapshot
        holds. Their faults follow the blocks' in the one refusal."""
        if self._guard is None:
            return
        boxes = [box for op in ops for box in self._boxes(op)]
        faults = self._guard.refusals(boxes, kind="blocks")
        spawns = [box for op in ops for box in self._entity_boxes(op)]
        if spawns:
            faults += self._guard.refusals(spawns, kind="entities")
        self._unadmitted(faults, what=what, first=ops[0] if ops else None)

    def _admit_boxes(self, boxes, *, what: str | None, first=None,
                     kind: str = "blocks") -> None:
        """:meth:`_admit` over inclusive boxes; ``first``, the plan's first
        op, describes the write when there is no ``what``."""
        self._unadmitted(self._guard.refusals(boxes, kind=kind), what=what,
                         first=first)

    def _unadmitted(self, faults, *, what: str | None, first) -> None:
        """Raise the guard's refusal over ``faults``; nothing when there
        are none."""
        if not faults:
            return
        if what is None:
            what = _describe(first)
        count = (f"{len(faults)} faults, first: {faults[0]}"
                 if len(faults) > 1 else faults[0])
        raise Refused(f"{what} is refused by worksite {self._guard.name!r}: "
                      f"{count}. Nothing was written", faults=faults)

    def _write_faults(self, ops) -> list[str]:
        """Why ``ops`` cannot be written where they write: one fault for each
        chunk an op reaches that is not generated or not finished, in the
        order the ops first reach them, then one for each end of a world's
        height the ops pass, naming the span of y they would write past it.
        Empty when they can. A chunk that cannot be read raises its own
        ``NBTError``. A paste's entities are checked as set cells, where
        each lands (:meth:`_entity_boxes`)."""
        bad: dict[tuple[int, int], str] = {}
        # (lo, hi, end) -> [first, last]: the y past that end of that range.
        past: dict[tuple[int, int, str], list[int]] = {}
        spans = self._spans
        for op in ops:
            if op[0] == "set":
                # The common case, and the one a long run of writes makes:
                # one cell, in a chunk already found sound.
                x, y, z = op[1]
                span = spans.get((x >> 4, z >> 4))
                if span is not None and span[0] <= y <= span[1]:
                    continue
            for x0, y0, z0, x1, y1, z1 in (*self._boxes(op),
                                           *self._entity_boxes(op)):
                for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
                    for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
                        key = (chunk_x, chunk_z)
                        span = spans.get(key)
                        if span is None:
                            if key in bad:
                                continue
                            span = self._chunk_span(chunk_x, chunk_z)
                            if isinstance(span, str):
                                bad[key] = span
                                continue
                        lo, hi = span
                        if lo <= y0 and y1 <= hi:
                            continue
                        for end, a, b in (("under", y0, min(y1, lo - 1)),
                                          ("over", max(y0, hi + 1), y1)):
                            if a <= b:
                                reach = past.setdefault((lo, hi, end), [a, b])
                                reach[0] = min(reach[0], a)
                                reach[1] = max(reach[1], b)
        return list(bad.values()) + [
            f"y={a}..{b} is past the world's {lo}..{hi}"
            for (lo, hi, _), (a, b) in past.items()]

    def _boxes(self, op):
        """The inclusive ``(x0, y0, z0, x1, y1, z1)`` boxes a plan op writes:
        one cell for a set, the sorted box for a fill (as ``MinecraftSave.fill``
        sorts it), a placement's :meth:`_placement_footprint`, a paste's
        whole box -- air and all, even where it skips the air -- and
        nothing for a ground-cache op. An op of any other kind is a bug in
        its writer, raised before anything is written."""
        kind = op[0]
        if kind == "set":
            x, y, z = (int(v) for v in op[1])
            return ((x, y, z, x, y, z),)
        if kind == "fill":
            (ax, ay, az), (bx, by, bz) = op[1], op[2]
            x0, x1 = sorted((int(ax), int(bx)))
            y0, y1 = sorted((int(ay), int(by)))
            z0, z1 = sorted((int(az), int(bz)))
            return ((x0, y0, z0, x1, y1, z1),)
        if kind == "place":
            _one_placement(op[2])
            x, y, z = (int(v) for v in op[1])
            return self._placement_footprint(x, y, z, _state(op[2]))
        if kind == "paste":
            return (op[1].box,)
        if kind == "ground":
            return ()
        raise NBTError(f"a plan op is a set, a fill, a place, a paste or a "
                       f"ground, not {kind!r}")

    @staticmethod
    def _entity_boxes(op) -> tuple:
        """The one-cell boxes of the entities a plan op writes: the cell
        each of a paste's entities lands in, when it writes them; none for
        any other op."""
        if op[0] != "paste":
            return ()
        return tuple(cell + cell for cell in op[1].entity_cells)

    def _placement_footprint(self, x: int, y: int, z: int, state) -> list:
        """Every cell a placement of ``state`` at ``(x, y, z)`` may write, as
        inclusive boxes: its own cells (a bed's head, a door's top), then
        each neighbor ``place_block`` re-derives -- the four beside and the
        one below every cell -- where what stands there now is a block
        whose state its neighbors decide (a fence, a wall, a pane, a bar, a
        gate, a stair). Conservative: such a neighbor counts whether or not
        its state would change."""
        cells = [cell for cell, _ in _cells_for(state, x, y, z)]
        boxes = [(cx, cy, cz, cx, cy, cz) for cx, cy, cz in cells]
        seen = set(cells)
        for cx, cy, cz in cells:
            for near in ((cx, cy, cz - 1), (cx, cy, cz + 1), (cx - 1, cy, cz),
                         (cx + 1, cy, cz), (cx, cy - 1, cz)):
                if near in seen:
                    continue
                seen.add(near)
                found = self._peek(near)
                if found is not None and _candidate(found.name, True):
                    boxes.append(near + near)
        return boxes

    def _chunk_span(self, chunk_x: int, chunk_z: int):
        """A chunk's ``(min_y, max_y)`` when it can be built in -- generated,
        finished, and not one Minecraft 1.13 to 1.17 saved, which the library reads
        and never writes -- remembered from then on; otherwise why not, as
        a fault. An unreadable chunk raises."""
        accessor = self.save.dimension(self.dimension).blocks(chunk_x, chunk_z)
        if accessor is None:
            return f"chunk ({chunk_x}, {chunk_z}) is not generated"
        try:
            accessor.refuse_legacy("build in")
        except ReadOnlyChunk as exc:
            return str(exc)
        status = accessor.status
        if not _finished(status):
            stage = (f"is at {status}" if status is not None
                     else "records no generation stage")
            return (f"chunk ({chunk_x}, {chunk_z}) {stage}, not finished "
                    f"terrain -- the game generates over whatever is written "
                    f"into it")
        span = (accessor.min_y, accessor.max_y)
        self._spans[(chunk_x, chunk_z)] = span
        return span

    def _drawn(self, block, parsed, cell=None):
        """``block`` as it is written at ``cell``: the surface a
        :class:`_Mix` draws from :attr:`rng`, now; a
        :class:`~heaton_voxel.craft.style.Mix`'s pick at ``cell``, which no
        draw and no earlier write moves; and a string as the state
        :meth:`_parse` made of it."""
        if isinstance(block, _Mix):
            block = self._mix(block.mix)
        elif isinstance(block, Mix):
            block = block.pick(*cell)
        return parsed.get(block, block) if isinstance(block, str) else block

    def _sweep(self, corner1, corner2, mix, parsed) -> None:
        """Fill the box ``corner1``..``corner2`` (corners in either order)
        with a :class:`~heaton_voxel.craft.style.Mix`: each cell its own
        pick, written by :meth:`_write_set` and journaled one by one, in
        ``y``, then ``z``, then ``x`` order, ascending -- the order a
        section holds its cells, so each block enters a section's palette
        at its first pick in that order, and what a sweep writes, in what
        order, is the box's and the Mix's alone. Never
        ``MinecraftSave.fill``, which writes one state. The box has passed
        every check :meth:`_vet` makes, once, as a fill's would."""
        ((x0, y0, z0, x1, y1, z1),) = self._boxes(("fill", corner1, corner2,
                                                    mix))
        for y in range(y0, y1 + 1):
            for z in range(z0, z1 + 1):
                for x in range(x0, x1 + 1):
                    self._write_set(x, y, z,
                                    self._drawn(mix, parsed, (x, y, z)))

    def _write_set(self, x: int, y: int, z: int, block) -> None:
        if not self.save.set_block(x, y, z, block, dimension=self.dimension):
            raise NBTError(f"could not set block at ({x}, {y}, {z}); is the "
                           f"chunk generated?")
        self.journal.record(x, y, z)

    def _write_place(self, x: int, y: int, z: int, state) -> None:
        if not self.save.place_block(x, y, z, state, dimension=self.dimension):
            raise NBTError(f"could not place {state} at ({x}, {y}, {z})")
        for (cx, cy, cz), _ in _cells_for(state, x, y, z):
            self.journal.record(cx, cy, cz)

    def _write_fill(self, corner1, corner2, block) -> None:
        result = self.save.fill(
            corner1, corner2, block, dimension=self.dimension, keep_loaded=True
        )
        if result.missing_chunks or result.unreadable_chunks:
            raise NBTError(f"fill entered missing/unreadable chunks: {result!r}")
        self.journal.record_box(corner1, corner2, result.blocks_filled)

    def _write_paste(self, paste: _Paste) -> SchemImportResult:
        """Write a paste :meth:`stamp` planned and :meth:`_commit` checked,
        by the lower tier's paste, minimum corner first and the schematic
        already turned, unrelit: :meth:`finish` relights. Journals the box,
        or with ``paste_air`` off each cell it wrote, in ``y``, ``z``,
        ``x`` order, and counts the entities. A paste that did not land
        whole -- a section missing inside a chunk's own height, entity
        storage that could not take an entity -- is corruption the checks
        could not see, an ``NBTError``."""
        result = self.save.import_schem(
            paste.schematic, paste.corner, anchor="min",
            entities=paste.entities, biomes=False, paste_air=paste.paste_air,
            relight=False, dimension=self.dimension, prune=True,
            keep_loaded=True)
        if not result.complete:
            raise NBTError(f"the stamp at {paste.corner} did not land whole: "
                           f"{result!r}")
        x0, y0, z0, x1, y1, z1 = paste.box
        if paste.paste_air:
            self.journal.record_box((x0, y0, z0), (x1, y1, z1),
                                    result.blocks_pasted)
        else:
            palette = paste.schematic.palette
            cells = iter(paste.schematic.indices())
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    for x in range(x0, x1 + 1):
                        if palette[next(cells)].name not in AIR_BLOCKS:
                            self.journal.record(x, y, z)
        self.entities_added += result.entities_added
        return result

    def spawn(self, identity: str, x: float, y: float, z: float, *,
              yaw: float | None = None, extra: dict | None = None) -> None:
        """Spawn an entity (see ``MinecraftSave.spawn_entity``); integer
        coordinates are centered on their block, which is almost always what
        a builder means.

        Refused, as :meth:`set` is, where the block the entity stands in is
        in a chunk that is not generated or not finished, or past the top
        or bottom of the world -- a mob spawned into ground the game has
        still to generate, or under the world, is not one anybody meant.
        Under a worksite's :attr:`guard` the entities file the spawn lands
        in must also be one its snapshot holds. Either refusal comes before
        anything is written or drawn -- a random ``yaw`` included."""
        x, z = self._spawn_site(identity, x, y, z)
        self._spawn(identity, x, y, z, yaw, extra)

    def _spawn_site(self, identity: str, x: float, y: float,
                    z: float) -> tuple[float, float]:
        """:meth:`spawn`'s refusals, before anything is read, drawn or
        written: ``(x, z)``, centered on their block where they are
        integers."""
        self._alive()
        if isinstance(x, int):
            x += 0.5
        if isinstance(z, int):
            z += 0.5
        cell = (math.floor(x), math.floor(y), math.floor(z))
        what = f"spawn {identity} at ({x}, {y}, {z})"
        self._reach((("set", cell, None),), what=what)
        if self._guard is not None:
            self._admit_boxes([cell + cell], kind="entities", what=what)
        return x, z

    def _spawn(self, identity: str, x: float, y: float, z: float,
               yaw: float | None, extra: dict | None) -> None:
        """Write a spawn :meth:`_spawn_site` has admitted."""
        if yaw is None:
            yaw = float(self.rng.randrange(360) - 180)
        if not self.save.spawn_entity(
            identity, x, float(y), z, yaw=yaw, extra=extra,
            dimension=self.dimension,
        ):
            raise NBTError(f"could not spawn {identity} at ({x}, {y}, {z})")
        self.entities_added += 1

    def villager(self, profession: str, x: int, z: int, *, yaw: float = 0.0,
                 kind: str | None = None, y: int | None = None) -> None:
        """A villager with a profession, standing on the ground at ``x, z``
        -- or on the floor at ``y``, its feet at ``y + 1``: an upper story,
        a market deck.

        Its type is the one the game would give it there. The game types a
        villager by the biome at its feet when it first spawns, and takes
        the type a save hands it as final, so ``kind=None`` reads the biome
        the feet stand in and takes
        :func:`~heaton_voxel.craft.biomes.villager_type` of it: desert in
        the badlands, snow on snowy plains, plains wherever the game's
        table names nothing. The spot is refused first, as :meth:`spawn`
        refuses one, so nothing is read over the world. The game picks its
        biome through a seeded zoom (``BiomeManager``), which within about
        two blocks of a biome border can land in the neighbor's; this reads
        the cell the feet stand in, and a cell that records no biome is
        refused rather than guessed at.

        An explicit ``kind`` -- ``"snow"`` or ``"minecraft:snow"`` -- is
        taken as given, once it is one of
        :data:`~heaton_voxel.craft.biomes.VILLAGER_KINDS`; any other is
        refused before anything is read, since the game would load it as a
        plains villager.

        Give the building a matching workstation and a bed, and
        :meth:`finish`'s POI invalidation makes the game link them on load.
        """
        self._alive()
        if kind is not None:
            kind = _villager_kind(kind)
        y = (self.ground(x, z) if y is None else int(y)) + 1
        x, z = self._spawn_site("minecraft:villager", x, y, z)
        if kind is None:
            feet = (math.floor(x), y, math.floor(z))
            biome = self.save.get_biome(*feet, dimension=self.dimension)
            if biome is None:
                raise NBTError(f"no biome is recorded at {feet}, so the "
                               f"villager's type cannot be the game's; pass "
                               f"kind=")
            kind = villager_type(biome)
        self._spawn(
            "minecraft:villager", x, y, z, yaw,
            {
                "VillagerData": TagCompound(
                    {
                        "type": TagString(kind),
                        "profession": TagString(f"minecraft:{profession}"),
                        "level": TagInt(1),
                    }
                )
            },
        )

    def _read(self, cell):
        """The block at ``cell``, refusing where there is nothing to build
        into -- the reader every rule writer plans with.

        The writers check their plan's reach first (:meth:`_vet_plan`), so
        this refusal is the backstop for a cell outside it: a
        :class:`~heaton_voxel.craft.errors.Refused` in the words
        :meth:`_commit` uses, since a chunk the world never generated, or a
        height past it, is absence and not corruption."""
        found = self.save.get_block(*cell, dimension=self.dimension)
        if found is None:
            faults = self._write_faults((("set", tuple(cell), None),))
            if not faults:
                raise NBTError(f"nothing to read at {cell}, in a chunk "
                               f"that is generated and finished, at a height "
                               f"inside the world")
            raise Refused(f"nothing to build into at {cell}: {faults[0]}. "
                          f"Nothing was written", faults=faults)
        return found

    def _peek(self, cell):
        """The block at ``cell``, or None where there is none to read."""
        return self.save.get_block(*cell, dimension=self.dimension)

    def _claimant(self, cell) -> str:
        """Who claims ``cell``, one of the cells this session's flights and
        approaches own (``_claimed``), in a refusal's words: ``"an
        approach"`` where an :meth:`approach` claims it, ``"a flight"``
        otherwise."""
        return "an approach" if cell in self._approached else "a flight"

    # -- keep-outs -----------------------------------------------------------

    def _register(self, name: str) -> None:
        """Note when a pad or reservation name was first registered."""
        self._registered.setdefault(name, len(self._registered))

    def _keep_outs(self):
        """Every pad and reservation as ``(kind, name, rect)``, in the order
        they were registered; a pad written straight into ``_pads``, which
        nothing registered, comes after them in the order it was written."""
        found = [("pad", name, rect) for name, rect in self._pads.items()]
        found += [("reservation", name, rect)
                  for name, rect in self._reserved.items()]
        last = len(self._registered)
        return sorted(found, key=lambda k: self._registered.get(k[1], last))

    def _keep_out_faults(self, columns, *, through=(), what: str,
                         noun: str = "column") -> list[tuple[str, str, list]]:
        """``(keep-out, fault, hits)`` for each pad or reservation not named
        in ``through`` that an ``(x, z)`` of ``columns`` falls in, in the
        order they were registered; ``hits`` is every distinct column of
        ``columns`` in it, in the caller's order. The fault names the first
        of them and how many there are, each counted as a ``noun`` -- a
        column, or one of :meth:`line_lamps`' posts; ``what`` opens it, up
        to the keep-out's kind -- ``"road would carve through"``. A dead
        session raises first (:meth:`_alive`): the keep-outs are a
        judgment."""
        self._alive()
        allowed = set(through)
        columns = list(dict.fromkeys((c[0], c[1]) for c in columns))
        found = []
        for kind, name, (x0, z0, x1, z1) in self._keep_outs():
            if name in allowed:
                continue
            hits = [c for c in columns if x0 <= c[0] <= x1 and z0 <= c[1] <= z1]
            if hits:
                count = (f"{len(hits)} {noun}s" if len(hits) > 1
                         else f"1 {noun}")
                found.append((
                    f"{kind} {name!r}",
                    f"{what} {kind} {name!r} at {hits[0]} ({count}); route "
                    f"around it, or pass through=({name!r},) if the crossing "
                    f"is deliberate",
                    hits))
        return found

    @staticmethod
    def _keep_out_message(found) -> str:
        """What a refusal over :meth:`_keep_out_faults`' ``found`` says: the
        one fault, or how many keep-outs and which, then the first."""
        if len(found) == 1:
            return found[0][1]
        named = ", ".join(label for label, _, _ in found)
        return (f"{len(found)} keep-outs are in the way ({named}), first: "
                f"{found[0][1]}")

    def _keep_out(self, columns, *, through=(), what: str) -> None:
        """Refuse a footprint that enters a registered pad or reservation
        not named in ``through``, before anything is written: a
        :class:`~heaton_voxel.craft.errors.Refused` with one fault per
        keep-out entered, as :meth:`_keep_out_faults` words them.

        The keep-outs bind the writers whose footprint the Builder derives
        rather than the caller names -- :meth:`road`, :meth:`railway` and
        :meth:`approach`, whose run is the cells between its ends -- since a
        routed footprint is the one nobody looked at column by column before
        it was written.
        """
        found = self._keep_out_faults(columns, through=through, what=what)
        if found:
            raise Refused(self._keep_out_message(found),
                          faults=[fault for _, fault, _ in found])

    # -- materials -----------------------------------------------------------

    def _materials(self, what: str, *slots) -> list:
        """The materials a palette-reading writer, ``what``, writes: one per
        :class:`~heaton_voxel.craft.build._common._Slot`, in order, each
        resolved as it will be written. A dead session raises first
        (:meth:`_alive`).

        A slot left to the palette (``FROM_PALETTE``) reads its role from
        :attr:`palette`, and from there on it is resolved as an argument
        given would be: an id or a
        :class:`~heaton_voxel.blocks.BlockState` is kept as given, so an
        explicit argument always wins and passes through untouched (a
        window then sets the two connections along its wall itself,
        :meth:`window`); a
        :class:`~heaton_voxel.craft.style.Material` becomes the form the
        writer writes -- its block, stairs, slab or pane; a
        :class:`~heaton_voxel.craft.style.Mix` is kept where the slot
        writes the block as it is, and is a ``TypeError`` where the writer
        spells a facing, a half or a connection onto it or reads its light
        off it; and ``None`` is kept where it means none (a floor's trim
        and light) and is a ``TypeError`` anywhere else. Whatever else is
        given is kept, for the plan's own checks to refuse in their words.

        Every form a Material asked for lacks, across all the slots, is
        one :class:`~heaton_voxel.craft.errors.IllegalBlock` naming them
        all, in the plan's words, before anything is read: the game has no
        such block, and would load it as air. Nothing here reads the world
        or draws from :attr:`rng`; whether the game has a plain block is
        the plan's check (:meth:`_legal`), under :attr:`allow`."""
        self._alive()
        resolved = []
        missing = []
        for slot in slots:
            given = slot.given
            if given is FROM_PALETTE:
                given = self._palette[slot.role]
            if given is None:
                if not slot.none:
                    raise TypeError(
                        f"{what}({slot.arg}=None) names no block, and "
                        f"{what} writes one there: name the block, or leave "
                        f"{slot.arg}= out for the palette's {slot.role}")
            elif isinstance(given, Mix):
                if not slot.mixes:
                    doing = {"stairs": "spells a facing onto",
                             "slab": "spells a type onto",
                             "pane": "sets the connections of"}.get(
                                 slot.form, "reads its light off"
                                 if slot.role == "light"
                                 else "derives the state it writes from")
                    raise TypeError(
                        f"{what}({slot.arg}=) is one block, which {what} "
                        f"{doing}, and {given!r} is a Mix, many blocks "
                        f"picked by position: pick one with .pick(x, y, z), "
                        f"or name the block")
            elif isinstance(given, Material):
                if given.has(slot.form):
                    given = given.form(slot.form)
                else:
                    missing.append((f"{slot.form} of {given.base}",
                                    given._missing(slot.form)))
            resolved.append(given)
        if missing:
            refusals = [refusal for _, refusal in missing]
            body = (str(refusals[0]) if len(missing) == 1 else
                    f"{len(missing)} forms the game does not have "
                    f"({', '.join(form for form, _ in missing)}), first: "
                    f"{refusals[0]}")
            raise IllegalBlock(
                f"{what} cannot be written: {body}. Nothing was written",
                [fault for refusal in refusals for fault in refusal.faults],
                suggestions=dict.fromkeys(
                    name for refusal in refusals
                    for name in refusal.suggestions))
        return resolved

    def _surface(self, given, role: str, *, what: str):
        """The surface a road or a floor is laid from: ``given``, or the
        :attr:`palette`'s ``role`` when it is ``FROM_PALETTE`` or ``None``
        (which has always meant the default here). A list or tuple of
        ``(block, weight)`` pairs is returned as it is, the same object, to
        be drawn from :attr:`rng` as it always was; an empty one is an
        ``NBTError``, since a surface drawn from nothing has nothing to lay
        and quietly laying the default instead would be a guess. A
        :class:`~heaton_voxel.craft.style.Mix` is returned as it is, to be
        picked at each cell, and so is one block, laid everywhere; a
        :class:`~heaton_voxel.craft.style.Material` is its block. Reads
        nothing and draws nothing."""
        if given is FROM_PALETTE or given is None:
            given = self._palette[role]
        if isinstance(given, (list, tuple)) and not given:
            raise NBTError(f"{what}'s mix= has no entries, so there is "
                           f"nothing to lay; leave it out for the palette's "
                           f"{role}, or give it a block, a Mix or (block, "
                           f"weight) pairs")
        if isinstance(given, Material):
            return given.form("block")
        return given

    def _mix(self, mix) -> str:
        total = sum(weight for _, weight in mix)
        pick = self.rng.randrange(total)
        for name, weight in mix:
            pick -= weight
            if pick < 0:
                return name
        return mix[0][0]

    # -- aftercare -----------------------------------------------------------

    def finish(self, *, connect: bool = True, strict: bool = False) -> BuildFinish:
        """Settle what the session wrote, chunk by chunk; call once, before
        save().

        The :attr:`journal` says what was written where, and each pass of
        the aftercare runs over just that, one chunk at a time:

        * **connection states**
          (:meth:`~heaton_voxel.world.MinecraftSave.connect_blocks`) over
          each chunk's writes and one block around them, clipped to the
          chunk. A fence, wall, pane, gate or stair takes its state from
          the blocks across its faces, so nothing farther from a write can
          change (``_CONNECT_REACH``), and a margin crossing into the next
          chunk settles the fence standing there as the game's block update
          would. A chunk the margin reaches that the world never generated
          has nothing to settle and is not reported. One that is generated
          and **not finished** is left out, since the game generates over
          it and a Builder never writes into one -- and when a fence, wall,
          pane, gate or stair stands in the margin there, the chunk is
          listed on ``connections.unreadable_chunks`` with why, so
          ``connections.complete`` does not call that border settled. One
          that cannot be read is listed there too.
        * **heightmaps** and **POI records** on the chunks written in: a
          write changes only its own column.
        * **lighting** on the chunks written in and the eight around each,
          since light crosses a chunk border.
        * **the audit** (:func:`~heaton_voxel.craft.survey.unsupported`)
          over each chunk's writes and two blocks around them
          (``_AUDIT_REACH``) -- an iron door's lower half reads the button
          beside the block over its upper half -- clipped to each chunk it
          reaches, the faults in cell order, as one call over them lists
          them. A chunk beside the build that cannot be read is not
          audited, and ``lighting`` reports it.

        Within a chunk the box is one bounding box: of the writes there,
        with their margin, and of every margin reaching in from the chunks
        next door. So a chunk written at y=64 and y=200 is settled through
        everything between, and so is the box spanning a write in one
        corner of a chunk and a margin reaching in across its far edge from
        a write next door. Each pass's report is the sum of its per-chunk
        passes, so the counts are what was looked at, not a rectangle
        around it.

        Earlier work beside a write is not spared.
        :meth:`~heaton_voxel.world.MinecraftSave.connect_blocks`
        re-derives every fence, wall, pane, bar and stair in its boxes from
        their neighbors, which *corrects* decorative states an earlier build
        set by hand -- a line of fence posts spaced two apart becomes
        unconnected posts, because that is what a block update would do.
        Pass ``connect=False`` when the pass placed no connecting blocks of
        its own (:meth:`smooth_terrain` moves ground, which carries no such
        state) and its writes run beside existing work you would rather
        leave alone. Work outside those boxes is left as it is: run
        ``save.connect_blocks`` yourself over anything you built by hand.

        The audit lists what it found -- lone bed and door halves, torches
        and buttons with nothing to hang on, sand over a hole, chests under
        a block that conducts power, iron doors nothing opens -- in the
        result's ``unsupported``, earlier work within its reach included.
        Read it before saving; ``strict=True`` refuses instead, for
        anything but a one-way door, which is often meant. A writer that
        must not leave such a thing behind refuses its plan before it
        writes (:meth:`_audit_plan`), counting only what the plan itself
        would leave.

        Under a worksite's :attr:`guard`, every chunk the passes write in --
        those written and the ring around them -- must be one the snapshot
        holds, which every admitted write already ensured; and the
        connection pass must leave every protected zone as it found it. A
        fence, wall, pane, gate or stair inside a zone, a block from a
        write, whose state the pass re-derives refuses the finish before
        heightmaps, POI and lighting, a
        :class:`~heaton_voxel.craft.errors.Refused` naming every zone it
        would change, since what connect changes cannot be known without
        running it. It is the one refusal that comes after a write, so it
        puts back every zone cell the pass rewrote before it raises: the
        zones are as the finish found them, the connections settled
        outside them stay, and the build is in memory and unsaved. Asking
        again refuses again, and ``finish(connect=False)`` -- on the same
        session -- is the finish that leaves zones alone. A guard whose
        worksite was restored raises a plain ``NBTError`` before anything
        runs.
        """
        self._alive()
        written = self.journal._chunk_boxes()
        if not written:
            raise NBTError("nothing was built; finish() has nothing to settle")
        dimension = self.dimension
        guard = self._guard
        if guard is not None:
            # Each write was admitted with the chunks around it, so this
            # fails only for one that never came through the guard.
            faults = guard.refusals(
                [(chunk_x * 16, 0, chunk_z * 16, chunk_x * 16 + 15, 0,
                  chunk_z * 16 + 15) for chunk_x, chunk_z in written],
                kind="settle")
            if faults:
                count = (f"{len(faults)} faults, first: {faults[0]}"
                         if len(faults) > 1 else faults[0])
                raise Refused(f"finish() would settle outside worksite "
                              f"{guard.name!r}'s snapshot: {count}. Nothing "
                              f"was settled", faults=faults)
        connections = None
        if connect:
            connections = ConnectResult()
            # zone -> [(cell, before, after)], what settling changed in it.
            changed: dict[str, list] = {}
            margins = _margin_boxes(written, _CONNECT_REACH)
            for chunk, (c1, c2) in margins.items():
                if chunk not in written:
                    # The margin alone reaches it: settled only where the
                    # world finished it, as the Builder writes only there.
                    try:
                        span = self._chunk_span(*chunk)
                    except NBTError as exc:
                        connections.unreadable_chunks.append((chunk, str(exc)))
                        continue
                    if isinstance(span, str):
                        # Never generated, nothing stands there to settle.
                        # Still generating, what stands there is left as
                        # it is -- and said, since that border is unsettled.
                        left = self._connecting_in(chunk, c1, c2)
                        if left:
                            kept = ("the connecting block in reach of the "
                                    "build there keeps its state" if left == 1
                                    else f"the {left} connecting blocks in "
                                    f"reach of the build there keep their "
                                    f"states")
                            connections.unreadable_chunks.append((chunk, (
                                f"{span}; finish() writes nothing into it, "
                                f"so {kept}")))
                        continue
                # Connect writes only inside its box, and only the blocks
                # whose state their neighbors decide: those, in the zones
                # the box meets, are everything it can change of them.
                watched = ([] if guard is None else
                           [(name, self._connecting_states(chunk, *part))
                            for name, part in guard.zones_meeting(c1, c2)])
                _merge(connections, self.save.connect_blocks(
                    c1, c2, dimension=dimension))
                for name, cells in watched:
                    for cell, before in cells.items():
                        after = self._peek(cell)
                        if after != before:
                            changed.setdefault(name, []).append(
                                (cell, before, after))
            if changed:
                # Put back what settling changed in the zones, cell by cell
                # in (x, y, z) order, so the refusal leaves every zone as
                # finish() found it: a retry is refused again, and
                # finish(connect=False) on this same session has no damage
                # to save.
                found = {cell: before for cells in changed.values()
                         for cell, before, _ in cells}
                for cell in sorted(found):
                    if not self.save.set_block(*cell, found[cell],
                                               dimension=dimension):
                        raise NBTError(
                            f"finish() could not put back {found[cell]} at "
                            f"{cell} in a protected zone of worksite "
                            f"{guard.name!r}; the zone is not as it was, and "
                            f"check() names it")
                faults = [
                    f"{cells[0][0]} in protected zone {name!r} would have "
                    f"become {cells[0][2]}, from {cells[0][1]}"
                    + (f" (and {len(cells) - 1} more there)" if len(cells) > 1
                       else "")
                    for name, cells in changed.items()]
                named = ", ".join(repr(name) for name in changed)
                raise Refused(
                    f"finish() would change protected zone"
                    f"{'s' if len(changed) > 1 else ''} {named} of worksite "
                    f"{guard.name!r}, settling the connections beside the "
                    f"build: {faults[0]}. It put the zone back as it found "
                    f"it and settled nothing else -- no heightmaps, POI or "
                    f"light -- though the connections it settled outside the "
                    f"zone stay; the build is in memory and unsaved. Finish "
                    f"with finish(connect=False), which leaves zones alone, "
                    f"or restore() and build again with every write more "
                    f"than a block from the fences, walls, panes, gates and "
                    f"stairs in it", faults=faults)
        heightmaps = HeightmapResult()
        for chunk_x, chunk_z in written:
            corner = (chunk_x * 16, 0, chunk_z * 16)
            _merge(heightmaps, self.save.rebuild_heightmaps(
                corner, corner, dimension=dimension))
        poi = 0
        for chunk_x, chunk_z in written:
            corner = (chunk_x * 16, 0, chunk_z * 16)
            poi += self.save.invalidate_poi(corner, corner,
                                            dimension=dimension)
        lighting = LightResetResult()
        around = sorted({(chunk_x + dx, chunk_z + dz)
                         for chunk_x, chunk_z in written
                         for dx in (-1, 0, 1) for dz in (-1, 0, 1)})
        for chunk_x, chunk_z in around:
            corner = (chunk_x * 16, 0, chunk_z * 16)
            _merge(lighting, self.save.reset_lighting(
                corner, corner, dimension=dimension, neighbors=False))
        audit = []
        dim = self.save.dimension(dimension)
        for chunk, (c1, c2) in _margin_boxes(written, _AUDIT_REACH).items():
            if chunk not in written:
                try:
                    dim.blocks(*chunk)
                except NBTError:
                    # Beside the build and unreadable -- an old chunk the
                    # game has not converted, say. Nothing of the build is
                    # in it, and the lighting pass reports it.
                    continue
            audit.extend(unsupported(self.save, c1, c2, dimension=dimension))
        # Every cell lies in one chunk, so this is the order one call over
        # all of them gives: by cell, and a cell's faults as it found them.
        audit.sort(key=lambda fault: fault.cell)
        if strict:
            faults = [fault for fault in audit if fault.kind != "one-way door"]
            if faults:
                raise Refused(
                    f"{len(faults)} blocks the game will not keep, first: "
                    f"{faults[0]}. The build is in memory and unsaved; fix "
                    f"them, or finish without strict=True to save it as it "
                    f"is", faults=faults)
        return BuildFinish(connections, heightmaps, lighting, poi, audit)

    def _connecting_in(self, chunk, corner1, corner2) -> int:
        """How many fences, walls, panes, bars, gates and stairs stand in
        the box ``corner1``..``corner2`` of ``chunk`` -- what a connect pass
        over it would examine; none in a chunk never generated. A read, so
        :meth:`finish` can say what it left in a chunk it does not write."""
        return len(self._connecting_states(chunk, corner1, corner2))

    def _connecting_states(self, chunk, corner1, corner2) -> dict:
        """``{(x, y, z): state}`` of every fence, wall, pane, bar, gate and
        stair in the box ``corner1``..``corner2`` of ``chunk``, by section,
        then ``y``, ``z``, ``x``: the blocks a connect pass over it could
        rewrite. Empty for a chunk never generated."""
        accessor = self.save.dimension(self.dimension).blocks(*chunk)
        if accessor is None:
            return {}
        (x0, y0, z0), (x1, y1, z1) = corner1, corner2
        y0, y1 = max(y0, accessor.min_y), min(y1, accessor.max_y)
        found = {}
        for section_y, section in accessor.sections.items():
            low, high = max(y0, section_y * 16), min(y1, section_y * 16 + 15)
            container = section.blocks
            if low > high or container is None or not any(
                    _candidate(entry.name, True) for entry in container.palette):
                continue
            for y in range(low, high + 1):
                for z in range(z0, z1 + 1):
                    for x in range(x0, x1 + 1):
                        state = accessor.get(x & 15, y, z & 15)
                        if _candidate(state.name, True):
                            found[(x, y, z)] = state
        return found
