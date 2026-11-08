"""Materials as a builder names them: mixes, families and palettes.

Three things every build session reached for and wrote again in its own
script: a surface mixed from several blocks so it does not read as one
flat texture, a family of blocks cut from one stone (the bricks, their
stairs, their slab), and the set of materials one style builds with.

    from heaton_voxel.craft.style import Material, Mix, Palette

    cobbles = Mix([("minecraft:cobblestone", 8), ("minecraft:andesite", 5),
                   ("minecraft:mossy_cobblestone", 1)], clump=3)
    cobbles.pick(10, 64, -3)                  # the same block every time

    brick = Material("minecraft:brick_stairs")  # named by any of its blocks
    brick.base, brick.form("slab")            # minecraft:bricks, minecraft:brick_slab

    roman = Palette(roof="minecraft:granite", gable="minecraft:smooth_sandstone")
    roman["roof"].form("stairs")              # minecraft:granite_stairs

**A Mix is picked by position** (:class:`Mix`). Its pick is a pure function
of the Mix and the cell -- :func:`~heaton_voxel.craft.noise.cell_hash`
keyed on the entries and the seed -- so no session, call order or random
stream touches it: a cell picks the same block however often it is
asked, in whatever order, whatever was rolled before. A plain list of
``(block, weight)`` pairs, as :data:`ROAD_MIX` is, is still a list: a
Builder draws it from its ``rng`` in the order its writes come, so one
changed call re-rolls every surface after it. A Builder's ``set``,
``fill``, ``level_pad``, ``road`` and ``pave`` take a Mix and write each
cell its pick. ``clump=`` picks whole patches
(:func:`~heaton_voxel.craft.noise.patch`), which keeps the weights and
loses the static a die per cell gives.

**A Material is a family** (:class:`Material`): a block and the forms of
it the game has -- its stairs, slab and wall
(:func:`~heaton_voxel.craft.blockinfo.material_of`, held to the game's
tags) and its pane -- each checked against the game's block list, so a
form the game does not have is refused by name when it is asked for, not
written and loaded as air. ``Material("minecraft:smooth_quartz")`` has no
wall: that is the Roman town's ``quartz_wall``.

**A Palette is a closed vocabulary** (:class:`Palette`, :data:`ROLES`): the
thirteen materials the eight legacy writers read -- ``level_pad``,
``road``, ``pave``, ``light_cells``, ``frame_walls``, ``floor``,
``gable_roof`` and ``window`` -- each role named for what it supplies,
and each default the material that writer used before it read a palette,
so ``Palette()`` is those defaults and not a style. A role it does not
have is a ``TypeError``, never a quiet fall back to a default. A style is
the caller's to define, never this module's: ``examples/palettes.py``
holds two, read off the Roman town and the secret lab, and
``examples/craft_session.py`` builds a whole session in either. A Builder
reads its ``palette=`` wherever a writer's caller leaves a material out
(``FROM_PALETTE``), and one given none writes what it always wrote.

Every class here is a value with no world in it, and nothing here reads
or writes one.
"""

from __future__ import annotations

import bisect
import difflib
import zlib
from types import MappingProxyType
from typing import NamedTuple

from ..blocks import BlockState
from ..nbt import NBTError
from .block_registry import BLOCK_IDS, GAME_VERSION
from .blockinfo import (
    FORMS,
    RENAMED,
    _forms,
    _ordered_ids,
    _path,
    _stem_base,
    check_state,
    form_of,
    is_block,
)
from .errors import IllegalBlock
from .noise import MASK, MAX_PATCH, cell_hash, patch

__all__ = [
    "FROM_PALETTE",
    "Mix",
    "Material",
    "Role",
    "ROLES",
    "Palette",
    "DEFAULT_PALETTE",
    "ROAD_MIX",
    "LANE_MIX",
    "PLAZA_MIX",
]


# Weighted surface palettes; pass any [(block, weight), ...] of your own.
ROAD_MIX = [("minecraft:cobblestone", 8), ("minecraft:andesite", 5),
            ("minecraft:gravel", 4), ("minecraft:stone", 2),
            ("minecraft:mossy_cobblestone", 1)]


LANE_MIX = [("minecraft:dirt_path", 10), ("minecraft:gravel", 5),
            ("minecraft:coarse_dirt", 3)]


PLAZA_MIX = [("minecraft:stone_bricks", 6), ("minecraft:andesite", 4),
             ("minecraft:cracked_stone_bricks", 2),
             ("minecraft:polished_andesite", 2)]


class _FromPalette:
    """The type of :data:`FROM_PALETTE`; there is only the one."""

    __slots__ = ()
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "FROM_PALETTE"

    def __reduce__(self) -> str:
        # Copied or pickled, it is still the one sentinel.
        return "FROM_PALETTE"


#: A writer argument left to the palette: "this role's material, whatever
#: the palette in use says it is". A sentinel, not ``None``, because
#: ``None`` already means "none" to some of the arguments it stands in for
#: (``pave(light=None)`` lays an unlit floor).
FROM_PALETTE = _FromPalette()


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _one_block(block, what: str) -> BlockState:
    """``block``, a name or a :class:`BlockState`, as a state -- refusing
    anything else with a ``TypeError`` that says what it is. Malformed
    text raises the parser's own ``ValueError``."""
    if isinstance(block, BlockState):
        return block
    if isinstance(block, str):
        return BlockState.parse(block)
    if isinstance(block, Material):
        raise TypeError(f"{what} is a block, and {block!r} is a family of "
                        f"them: pass {block!r}.form('block'), or the form "
                        f"wanted")
    if isinstance(block, Mix):
        raise TypeError(f"{what} is one block, and {block!r} is a Mix: a "
                        f"Mix's entries are blocks, not other mixes")
    raise TypeError(f"{what} is a block, named by its id or a BlockState, "
                    f"not {block!r}")


def _pairs(entries, what: str) -> list[tuple[object, int, BlockState]]:
    """``entries`` as ``(block, weight, state)`` triples, every one checked:
    a pair of a block and a whole weight of at least 1, and at least one
    of them."""
    if isinstance(entries, (str, BlockState, Mix, Material)) or entries is None:
        raise TypeError(f"{what} is a list of (block, weight) pairs, not "
                        f"{entries!r}")
    try:
        entries = tuple(entries)
    except TypeError:
        raise TypeError(f"{what} is a list of (block, weight) pairs, not "
                        f"{entries!r}") from None
    if not entries:
        raise NBTError(f"{what} has no entries, so there is nothing to pick")
    checked = []
    for entry in entries:
        if isinstance(entry, (str, BlockState)) or not hasattr(entry, "__len__") \
                or len(entry) != 2:
            raise TypeError(f"each entry of {what} is a (block, weight) "
                            f"pair, not {entry!r}")
        block, weight = entry
        state = _one_block(block, f"an entry of {what}")
        if not _whole(weight) or weight < 1:
            raise NBTError(f"{what}: {block} has weight {weight!r}, and a "
                           f"weight is a whole number, 1 or more")
        checked.append((block, weight, state))
    return checked


# --------------------------------------------------------------------------
# Mix
# --------------------------------------------------------------------------

#: Folded into a clumped Mix's key for where its patches fall, so the
#: jitter of a patch's center is not the roll that picks its block.
_JITTER = 0x6A09E667


def _weighted(bounds, total: int, roll: int) -> int:
    """The index of the entry a 32-bit ``roll`` picks from weights whose
    running sums are ``bounds``, ``total`` being the last of them: the
    entry whose half-open interval of the running weights holds
    ``(roll * total) >> 32``. That is an exact whole number in
    ``[0, total)``, so the intervals leave no tie to break, and each entry
    is picked in proportion to its weight. :meth:`Mix.pick` is this over a
    roll keyed on its cell, and any other keyed pick that should keep a
    Mix's proportions is too."""
    return bisect.bisect_right(bounds, (roll * total) >> 32)


class Mix:
    """A weighted mix of blocks, picked by position.

    ``entries`` are ``(block, weight)`` pairs -- a block is a name or a
    :class:`~heaton_voxel.blocks.BlockState`, a weight a whole number, 1 or
    more -- kept as given, in order. :meth:`pick` gives each cell one of
    them, in proportion to the weights, as a pure function of the Mix and
    the cell: the same cell picks the same block on every call, in any
    order, from any session.

    The pick is keyed on the cell, ``seed``, and a CRC of the entries
    (each written as its state, properties sorted, and its weight), so two
    different mixes on one seed are two independent fields: a floor and a
    wall mixed with the default seed do not repeat one pattern in two
    materials. The same states spelled two ways pick alike.

    ``clump`` (1 to :data:`~heaton_voxel.craft.noise.MAX_PATCH`) picks
    whole patches about that many blocks across instead of single cells
    (:func:`~heaton_voxel.craft.noise.patch`), in three dimensions, so a
    wall clumps as a floor does. The proportions hold at any clump; what
    changes is the picture: at 1 a neighbor matches about as often as two
    independent dice would, and at 3 about two in three do.

    A Mix checks its entries' syntax, never whether the game has them:
    that is the writer's to check, under its own ``allow=``. Refused here:
    no entries, or a weight that is not a whole number of at least 1, or
    a clump out of range (``NBTError``); an entry that is not a block --
    a :class:`Material` included -- or a seed that is not a whole number
    (``TypeError``); and text that does not parse (the parser's
    ``ValueError``).

    Immutable and hashable. Two Mixes are equal when they pick alike: the
    same states in the same order, however spelled, with the same
    weights, the same clump, and seeds equal in the 32 bits the key is
    made from -- so a mix of ``"minecraft:stone"`` equals one of
    ``BlockState("minecraft:stone")``, and equal Mixes hash alike.
    """

    __slots__ = ("_entries", "_seed", "_clump", "_canonical", "_key",
                 "_bounds", "_blocks", "_total")

    #: What :func:`~heaton_voxel.craft._plan.refuse_many` knows it by.
    _craft_style = "Mix"

    def __init__(self, entries, *, seed: int = 0, clump: int = 1):
        checked = _pairs(entries, "a Mix")
        if not _whole(seed):
            raise TypeError(f"a Mix's seed is a whole number, not {seed!r}")
        if not _whole(clump) or not 1 <= clump <= MAX_PATCH:
            raise NBTError(f"a Mix's clump is a whole number of blocks, 1 "
                           f"to {MAX_PATCH}, not {clump!r}")
        self._entries = tuple((block, weight) for block, weight, _ in checked)
        self._seed = seed
        self._clump = clump
        # Each entry as its state's own text, properties sorted, and its
        # weight: what the key is made from, and so what equality reads.
        self._canonical = tuple((str(state), weight)
                                for _, weight, state in checked)
        canonical = "\n".join(f"{state}\t{weight}"
                              for state, weight in self._canonical)
        self._key = (seed ^ zlib.crc32(canonical.encode("utf-8"))) & MASK
        bounds, total = [], 0
        for _, weight in self._entries:
            total += weight
            bounds.append(total)
        self._bounds = tuple(bounds)
        self._blocks = tuple(block for block, _ in self._entries)
        self._total = total

    @property
    def entries(self) -> tuple[tuple[object, int], ...]:
        """The ``(block, weight)`` pairs, as given."""
        return self._entries

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def clump(self) -> int:
        return self._clump

    def pick(self, x: int, y: int, z: int):
        """The block the cell ``(x, y, z)`` gets: one of :attr:`entries`'
        blocks, as given. Whole-number coordinates only; a float is a
        ``TypeError``, since which way it rounds would be a guess."""
        for value in (x, y, z):
            if not _whole(value):
                raise TypeError(f"a Mix is picked at a whole cell, not "
                                f"({x!r}, {y!r}, {z!r})")
        if self._clump == 1:
            roll = cell_hash(x, y, z, self._key)
        else:
            roll = cell_hash(*patch(x, y, z, self._clump, self._key ^ _JITTER),
                             self._key)
        return self._blocks[_weighted(self._bounds, self._total, roll)]

    def _field(self) -> tuple:
        """What a pick reads: the entries as states, the seed's 32 bits and
        the clump. Equality and the hash are both this, so they agree."""
        return (self._canonical, self._seed & MASK, self._clump)

    def __eq__(self, other) -> bool:
        if not isinstance(other, Mix):
            return NotImplemented
        return self._field() == other._field()

    def __hash__(self) -> int:
        return hash((Mix, self._field()))

    def __repr__(self) -> str:
        extra = "".join(f", {name}={value!r}" for name, value, default in
                        (("seed", self._seed, 0), ("clump", self._clump, 1))
                        if value != default)
        return f"Mix({list(self._entries)!r}{extra})"


# --------------------------------------------------------------------------
# Material
# --------------------------------------------------------------------------

def _listed(names) -> str:
    names = list(names)
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


class Material:
    """A family of blocks: a block and the forms of it the game has.

    Named by any block of the family or by its stem:
    ``Material("minecraft:brick_stairs")``, ``Material("minecraft:bricks")``
    and the stem ``brick`` all give the bricks, ``quartz_stairs`` and the
    stem ``quartz`` the quartz block, ``glass_pane`` the glass, and the oak
    stem the oak planks. A stem must lead to a family's block: one that
    leads to a form instead -- ``oak_stair``, a letter short of
    ``oak_stairs`` -- is a misspelling, refused with the id it nearly is.
    :attr:`base` is the block the family is named for: the full block its
    forms are cut from, or, for a block that is no form of another -- a
    fence, a door, a torch, calcite -- that block itself, a family of one.
    :attr:`forms` is its forms among
    :data:`~heaton_voxel.craft.blockinfo.FORMS` -- the
    block, then its stairs, slab, wall and pane, only those the game has:
    a cut is the one the game's tags hold
    (:func:`~heaton_voxel.craft.blockinfo.material_of`, with its named
    exceptions -- the bamboo stairs and slab are the bamboo planks', and
    oak planks' slab is the oak slab, never the petrified one), and a pane
    the ``_pane`` id its stem leads to.

    :meth:`form` refuses a form the family does not have with an
    :class:`~heaton_voxel.craft.errors.IllegalBlock` naming the forms it
    does have and, in ``.suggestions``, the nearest real ids of that form:
    smooth quartz has no wall (the Roman town's ``quartz_wall``), and the
    nearest wall to smooth sandstone's is the sandstone wall.

    A name with a state (``[...]``) is an ``NBTError``: a Material is named
    by an id. A name the game has retired, or one it never had, raises
    :func:`~heaton_voxel.craft.blockinfo.check_state`'s
    ``IllegalBlock`` -- its replacement, or the nearest spellings. A
    modded block has no family here: it goes in a block role as itself,
    where a Builder's ``allow=`` passes it.

    Immutable, hashable, and equal to any Material with the same base.
    """

    __slots__ = ("_base",)

    #: What :func:`~heaton_voxel.craft._plan.refuse_many` knows it by.
    _craft_style = "Material"

    def __init__(self, name):
        if isinstance(name, Material):
            self._base = name._base
            return
        if isinstance(name, BlockState):
            name = str(name)
        if not isinstance(name, str):
            raise TypeError(f"a Material is named by a block id, not "
                            f"{name!r}")
        if "[" in name:
            raise NBTError(f"a Material is a family, named by an id, not by "
                           f"one state of it: {name!r}")
        if name in RENAMED or is_block(name):
            # A retired name is refused, as any write of it would be air.
            self._base = form_of(check_state(name).name)[0]
            return
        base = _stem_base(name)
        if base in BLOCK_IDS:
            owner, form = form_of(base)
            if form == "block":
                self._base = base
                return
            # The stem rule led to a form, not a family's block: oak_stair
            # is oak_stairs a letter short, a misspelling and not a stem.
            fault = f"{name} is not a block in Minecraft {GAME_VERSION}"
            raise IllegalBlock(
                f"{fault}, nor the stem of a family: it leads to {base}, "
                f"which is {owner}'s {form}, not a family's block",
                [fault], suggestions=[base, owner])
        check_state(name)
        raise IllegalBlock(f"{name} names no block the game has", [name])

    @property
    def base(self) -> str:
        """The family's ``"block"`` form: the full block its other forms
        are cut from, or, for a block that is no form of another (a fence,
        a door, calcite), that block itself."""
        return self._base

    @property
    def forms(self) -> dict[str, str]:
        """``{form: id}`` for every form the game has: ``"block"`` first,
        then the rest in :data:`~heaton_voxel.craft.blockinfo.FORMS`
        order."""
        found = {"block": self._base}
        found.update(_forms().get(self._base, {}))
        return found

    def has(self, form: str) -> bool:
        """Whether the game has this family's ``form``. A form that is not
        one of :data:`~heaton_voxel.craft.blockinfo.FORMS` is an
        ``NBTError``, not a no: a misspelled form would otherwise read as
        one the family lacks."""
        _known_form(form)
        return form in self.forms

    def form(self, form: str) -> str:
        """The id of this family's ``form`` -- ``"block"``, ``"stairs"``,
        ``"slab"``, ``"wall"`` or ``"pane"``. A form the family does not
        have is an :class:`~heaton_voxel.craft.errors.IllegalBlock`; a
        form not in that list, an ``NBTError``."""
        _known_form(form)
        found = self.forms.get(form)
        if found is None:
            raise self._missing(form)
        return found

    def _comes_as(self) -> str:
        others = sorted(name for shape, name in self.forms.items()
                        if shape != "block")
        return (f"it comes as {_listed(others)}" if others
                else "it comes in no other form")

    def _missing(self, form: str) -> IllegalBlock:
        """The refusal for a form this family does not have."""
        fault = f"{self._base} has no {form} in Minecraft {GAME_VERSION}"
        parts = [f"{fault}: {self._comes_as()}"]
        suggestions: list[str] = []
        # The id the form would be named by, if another family has it --
        # the bamboo plant's stairs are its planks'.
        stem = self._base
        for suffix in ("_planks", "_block", "s"):
            if stem.endswith(suffix) and stem[:-len(suffix)].partition(":")[2]:
                stem = stem[:-len(suffix)]
                break
        for named in dict.fromkeys((f"{self._base}_{form}", f"{stem}_{form}")):
            if named in BLOCK_IDS:
                owner, shape = form_of(named)
                if shape == form and owner != self._base:
                    parts.append(f"{named} is {owner}'s")
                    suggestions.append(named)
        # Spellings compared without the namespace every id shares, which
        # would make any two stairs look alike.
        same_form = {_path(name): name for name in _ordered_ids()
                     if form_of(name)[1] == form}
        near = list(dict.fromkeys(
            same_form[found]
            for named in (f"{stem}_{form}", f"{self._base}_{form}")
            for found in difflib.get_close_matches(_path(named), same_form,
                                                   3, 0.8)
            if same_form[found] not in suggestions))[:3]
        suggestions += near
        if near:
            parts.append(f"nearest: {', '.join(near)}")
        return IllegalBlock("; ".join(parts), [fault], suggestions=suggestions)

    def __eq__(self, other) -> bool:
        if not isinstance(other, Material):
            return NotImplemented
        return self._base == other._base

    def __hash__(self) -> int:
        return hash((Material, self._base))

    def __repr__(self) -> str:
        return f"Material({self._base!r})"


def _known_form(form) -> None:
    if form not in FORMS:
        raise NBTError(f"{form!r} is not a form a Material names; the forms "
                       f"are {', '.join(FORMS)}")


# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------

class Role(NamedTuple):
    """One role of :data:`ROLES`: what it holds, its default and who reads
    it.

    ``kind`` is ``"block"`` (one block: an id, a
    :class:`~heaton_voxel.blocks.BlockState`, or a :class:`Material`, read
    as its block), ``"surface"`` (that, or a list of ``(block, weight)``
    pairs drawn from a Builder's ``rng``), ``"family"`` (a
    :class:`Material` with every form in ``forms``; an id is made one) or
    ``"pane"`` (one block, or a :class:`Material` read as its pane).
    ``mixes`` says whether a :class:`Mix` is accepted -- never where the
    reader derives a state from the block, adding a facing or a half to
    it, and never for a light, whose level is read off the one state it
    writes -- and ``none`` whether ``None``, meaning none, is.
    """

    kind: str
    default: object
    #: The writer, and its argument, that reads this role's material.
    read_by: str
    mixes: bool = False
    none: bool = False
    forms: tuple[str, ...] = ()


#: The roles a :class:`Palette` has, in order, and nothing else: each the
#: material one of the eight legacy writers reads (``read_by``), and each
#: default the one that writer wrote before it read a palette -- ``"glass"``
#: is the pane ``window`` always wrote. ``"wall"`` is a building's infill
#: (``frame_walls``), ``"retaining"`` the ring a pad's fill stands behind
#: (``level_pad``), ``"gable"`` the triangles a roof's ends are filled with
#: and ``"frame"`` the corner posts. ``grade``, which ``level_pad`` is a
#: call to, reads ``"pad"`` and ``"retaining"`` too, as its ``surface=``
#: and ``retain=``: one writer under two names, so not a ninth.
ROLES = MappingProxyType({
    "pad": Role("block", "minecraft:grass_block[snowy=false]",
                "level_pad(surface=)", mixes=True),
    "retaining": Role("block", "minecraft:stone_bricks", "level_pad(wall=)",
                      mixes=True),
    "road": Role("surface", ROAD_MIX, "road(mix=)", mixes=True),
    "paving": Role("surface", PLAZA_MIX, "pave(mix=)", mixes=True),
    "trim": Role("block", None, "pave(trim=)", mixes=True, none=True),
    "light": Role("block", "minecraft:glowstone",
                  "pave(light=) and light_cells(light=)"),
    "plinth": Role("block", "minecraft:cobblestone", "frame_walls(plinth=)",
                   mixes=True),
    "frame": Role("block", "minecraft:stripped_spruce_log[axis=y]",
                  "frame_walls(log=)", mixes=True),
    "wall": Role("block", "minecraft:white_terracotta",
                 "frame_walls(infill=)", mixes=True),
    "floor": Role("block", "minecraft:spruce_planks", "floor(block=)",
                  mixes=True),
    "roof": Role("family", Material("minecraft:dark_oak_planks"),
                 "gable_roof(stair=, slab=)", forms=("stairs", "slab")),
    "gable": Role("block", "minecraft:dark_oak_planks", "gable_roof(plank=)",
                  mixes=True),
    "glass": Role("pane", "minecraft:glass_pane", "window(block=)"),
})


def _role_value(role: str, spec: Role, value):
    """``value`` checked for ``role``, as the palette will hold it."""
    what = f"the {role} role"
    if value is None:
        if spec.none:
            return None
        raise TypeError(f"{what} cannot be None: it is read by "
                        f"{spec.read_by} for a material")
    if isinstance(value, Mix):
        if spec.mixes:
            return value
        if spec.kind == "family":
            reason = (f"it is a family, and {spec.read_by} cuts its "
                      f"{_listed(spec.forms)} from it")
        elif role == "light":
            reason = "a light's level is read off the one state it writes"
        else:
            reason = f"{spec.read_by} derives the state it writes from it"
        raise TypeError(f"{what} takes no Mix: {reason}")
    if spec.kind == "family":
        material = value if isinstance(value, Material) else Material(value)
        missing = [form for form in spec.forms if not material.has(form)]
        if missing:
            refusals = [material._missing(form) for form in missing]
            suggestions = list(dict.fromkeys(
                name for refusal in refusals for name in refusal.suggestions))
            raise IllegalBlock(
                f"{what} is read for {_listed(spec.forms)} by "
                f"{spec.read_by}, and {material.base} has no "
                f"{' or '.join(missing)} in Minecraft {GAME_VERSION}: "
                f"{material._comes_as()}",
                [fault for refusal in refusals for fault in refusal.faults],
                suggestions=suggestions)
        return material
    if isinstance(value, Material):
        if spec.kind == "pane" and not value.has("pane"):
            refusal = value._missing("pane")
            raise IllegalBlock(f"{what} is read for a pane by "
                               f"{spec.read_by}, and {refusal}",
                               refusal.faults,
                               suggestions=refusal.suggestions)
        return value
    if isinstance(value, (list, tuple)):
        if spec.kind != "surface":
            raise TypeError(
                f"{what} takes one block, not a list: a list of (block, "
                f"weight) pairs is drawn from a Builder's rng, and only the "
                f"road and paving roles are drawn; a Mix picks by position")
        _pairs(value, what)
        return value
    _one_block(value, what)
    return value


class Palette:
    """The materials a style builds with, one per role of :data:`ROLES`.

    ``Palette(**roles)`` takes any of the roles by name and holds each
    one's :attr:`Role.default` for the rest, so ``Palette()`` is the
    writers' own defaults (:data:`DEFAULT_PALETTE`) -- a name for them,
    not a style. Each value is checked as it is given, so a palette is
    refused when it is made, not halfway through a build: a role
    :data:`ROLES` does not have is a ``TypeError`` naming them (a typo
    must not fall back to a default), a family role whose family lacks a
    form its reader needs is one
    :class:`~heaton_voxel.craft.errors.IllegalBlock` naming every missing
    form, and a value of the wrong kind for its role is a ``TypeError``
    -- a Mix where one state is derived from the block, a list of pairs
    anywhere but a surface, ``None`` anywhere but ``trim``. Whether the
    game has a plain block is left to the Builder that writes it, under
    its own ``allow=``, so a palette never needs one.

    ``palette[role]`` is the value as held: a family role's id is made a
    :class:`Material`, and everything else is kept as given -- a surface
    list is the same object, so ``Palette()["road"] is ROAD_MIX``.
    :meth:`replace` returns a copy with some roles changed. Palettes are
    equal when every role is; a palette may hold a list, so it is not
    hashable.
    """

    __slots__ = ("_roles",)
    __hash__ = None

    def __init__(self, **roles):
        unknown = [role for role in roles if role not in ROLES]
        if unknown:
            hints = [match for role in unknown
                     for match in difflib.get_close_matches(role, ROLES, 1, 0.6)]
            hint = f" (did you mean {_listed(repr(h) for h in hints)}?)" \
                if hints else ""
            named = _listed(repr(role) for role in unknown)
            raise TypeError(
                f"a Palette has no role{'s' if len(unknown) > 1 else ''} "
                f"{named}{hint}; its roles are {', '.join(ROLES)}")
        self._roles = {role: _role_value(role, spec, roles.get(role, spec.default))
                       for role, spec in ROLES.items()}

    @property
    def roles(self) -> tuple[str, ...]:
        """Every role, in :data:`ROLES` order."""
        return tuple(self._roles)

    def __getitem__(self, role: str):
        try:
            return self._roles[role]
        except (KeyError, TypeError):
            near = difflib.get_close_matches(str(role), ROLES, 1, 0.6)
            hint = f" (did you mean {near[0]!r}?)" if near else ""
            raise KeyError(f"a Palette has no role {role!r}{hint}; its roles "
                           f"are {', '.join(ROLES)}") from None

    def replace(self, **roles) -> "Palette":
        """A copy with ``roles`` changed, checked as the constructor checks
        them."""
        return Palette(**{**self._roles, **roles})

    def __eq__(self, other) -> bool:
        if not isinstance(other, Palette):
            return NotImplemented
        return self._roles == other._roles

    def __repr__(self) -> str:
        changed = ", ".join(f"{role}={value!r}"
                            for role, value in self._roles.items()
                            if value != _DEFAULTS[role])
        return f"Palette({changed})"


#: Every role's default, as a palette holds it.
_DEFAULTS = {role: spec.default for role, spec in ROLES.items()}

#: ``Palette()``: the writers' own defaults.
DEFAULT_PALETTE = Palette()
