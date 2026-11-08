"""Planting at scale: a lawn's flowers, a wood's trees, a field's boulders.

:meth:`PlantingMixin.scatter` plants over any area of columns -- a
rectangle, a :func:`~heaton_voxel.craft.geometry.disc`, a set somebody
drew -- the way the park was planted by hand (``tools/park/build_park.py``:
its woodland, meadow, understory and seventy flower clumps), with the
dice keyed on the cell instead of drawn from a session's random stream,
and every planting held to what the game will keep before anything is
written.

**Keyed, not drawn.** Every roll is :func:`~heaton_voxel.craft.noise.cell_hash`
at the column, ``y = 0``, keyed on ``seed=`` folded with a CRC of what is
planted, the way a :class:`~heaton_voxel.craft.style.Mix` is keyed: the
same area, things and seed roll the same dice in any session and in any
order the area is given in. Nothing is drawn from the Builder's ``rng``, so
a call that draws from it -- a list-mix road, an ornamental tree -- moves
no planting after it, and regrading the ground under a planting never
re-rolls it. What a scatter reads is another matter: the world's blocks,
the ground the session gave a column, its keep-outs, its paths, its
flights' and approaches' claims and its worksite's guard all decide where
the dice land, so an earlier call that writes, paves, reserves or plants
where a scatter plants changes what it plants -- two scatters over one
lawn plant differently in the two orders. Each roll has its own salt (the
SHA-256 initial hash words, the first of them the one a clumped Mix's
jitter uses), so the dice that thin a column are not the dice that pick
its kind.

**Spaced by dart throwing.** The columns that could take a planting are
taken one at a time in an order keyed on the cell (``cell_hash`` under its
own salt, then ``z``, then ``x`` -- a total order), and one is kept when no
planting kept before it, and no column of ``apart_from``, stands closer
than ``spacing``: random sequential adsorption, which leaves the planting
maximal -- no column it passed over had room -- and is the same whatever
order the columns were given in. A grid of ``spacing``-wide cells keeps
each test to the nine around it.

**Only what craft can hold to the game's rule.** A thing is planted only
where :func:`~heaton_voxel.craft.survey.unsupported` would keep it, by the
same tables: a flower, a tuft of grass, a sapling, a berry bush, petals or
one of the six tall plants on the ``supports_vegetation`` tag, an azalea on
``supports_azalea``, a dead bush on ``supports_dry_vegetation``, sugar cane
on ``supports_sugar_cane`` with water beside its block, an ornamental
:class:`Tree` on its sapling's soil, and a block sturdy on every face that
the survival audit asks nothing of and that does not melt -- a boulder --
anywhere. Anything else is refused before a column is read: a cactus,
bamboo, a mushroom, a lily pad or a nether fungus asks something of its
ground that nothing in craft copies, and planting it by guess is how ten
of TestWorld (2)'s Roman garden flowers came to stand on gravel paths.
Then the whole plan goes through the one survival audit
(``Builder._audit_plan``) before it is written.

**A tree is its canopy too.** A trunk stands in one column and its leaves
in the twenty round it, so a tree is passed over wherever any of those is
avoided, paved this session or kept out, and wherever any cell it could
fill is one a flight or an approach this session built claims: a canopy
grown over a road's edge or into a stair's head room is a hedge across the
way, however lawful the column its trunk stands in.

Part of :mod:`heaton_voxel.craft.build`.
"""

from __future__ import annotations

import zlib
from numbers import Real

from ...block_defaults import BLOCK_DEFAULTS
from ...blocks import BlockState
from ...nbt import NBTError
from .. import geometry
from .._plan import refuse_many
from ..block_facts import facts, known
from ..blockinfo import is_natural_ground, is_vegetation
from ..errors import NoSurface, Refused
from ..noise import MASK, MAX_PATCH, cell_hash, patch
from ..style import Mix, _weighted
from ..survey import _bare, _footing, _fragile, _SOIL, _wet_beside
from ._common import _empty, _is_whole, _state, AIR
from .site import _NETHER_TREES, _outline, _tree_blocks, SiteMixin


# The salts each of scatter's rolls folds into its key: the SHA-256
# initial hash words, so no two rolls of one call are one roll. The first
# is the word a clumped Mix's patches are jittered with (style._JITTER).
_JITTER = 0x6A09E667
_COVER = 0xBB67AE85
_DENSITY = 0x3C6EF372
_PICK = 0xA54FF53A
_ORDER = 0x510E527F
_TRUNK = 0x9B05688C

#: A boulder's footing: anything at all.
_ANYWHERE = None

#: Every face sturdy, as a :class:`~heaton_voxel.craft.block_facts.Facts`
#: mask: what a boulder must be.
_ALL_FACES = (1 << 6) - 1

#: Blocks sturdy on every face that the game melts where they stand, so no
#: boulder, each with what becomes of it (javap, 26.3): ``ice`` turns into
#: a water source once block light reaches 11 (``IceBlock.randomTick``:
#: over 11 less its dampening of 1, then ``melt``), a pond on the lawn;
#: ``frosted_ice`` the same by daylight (``FrostedIceBlock.tick`` reads the
#: raw brightness, sky included, outside the End); and a ``snow`` layer,
#: sturdy on every face only at eight layers, is removed at block light 12
#: (``SnowLayerBlock.randomTick``) and asks for a whole top face under it
#: (``canSurvive``), a rule craft does not copy. Packed and blue ice are
#: plain blocks, and keep.
_MELTS = frozenset(("ice", "frosted_ice", "snow"))

#: The game's ``snow`` block tag (``data/minecraft/tags/block/snow.json``,
#: 26.3): what ``SnowyBlock.isSnowySetting`` takes above a grass block,
#: podzol or mycelium for ``snowy=true``. The lower tier keeps its own copy
#: for unfreeze (``world/editing.py``'s ``_SNOW_TAG``), held equal to this by
#: ``tests/test_scatter.py``.
_SNOW_TAG = frozenset(("minecraft:snow", "minecraft:snow_block",
                       "minecraft:powder_snow"))

#: Why a column was passed over, in the order :meth:`PlantingMixin.scatter`
#: asks: the keys of :attr:`ScatterResult.skipped`.
SKIP_REASONS = ("avoided", "paved", "keep-out", "no ground", "water",
                "wrong ground", "off its soil", "dry cane", "occupied",
                "claimed", "cannot be written", "refused by worksite")

_AIR_STATE = BlockState.parse(AIR)


def _tree_cells(trunk: int) -> tuple[tuple[int, int, int], ...]:
    """Every cell an ornamental tree whose trunk is ``trunk`` blocks high
    could fill, from its trunk's foot: :meth:`SiteMixin._tree_ops` over a world
    with nothing to read, which plans a leaf into every cell its canopy
    rule names."""
    ops = SiteMixin._tree_ops(0, 0, 0, AIR, AIR, trunk, read=lambda *c: None)
    return tuple(sorted(op[1] for op in ops))


#: The cells a scattered tree could fill, by trunk height, from its foot.
_TREE_CELLS = {trunk: _tree_cells(trunk) for trunk in (4, 5)}

#: The columns a scattered tree reaches, from its trunk's: its own and the
#: twenty its canopy grows over, a five by five with its corners cut. The
#: same at either trunk height.
_CANOPY = tuple(sorted({(dx, dz) for dx, _, dz in _TREE_CELLS[4]}))


def _fraction(value, name: str) -> float:
    """``value`` as a share over 0 and at most 1, or an ``NBTError``
    naming ``name``."""
    if isinstance(value, bool) or not isinstance(value, Real) \
            or not 0 < value <= 1:
        raise NBTError(f"{name} is the share of columns kept, a number over "
                       f"0 and at most 1, not {value!r}")
    return float(value)


class Tree:
    """An ornamental tree, as a thing :meth:`PlantingMixin.scatter` plants:
    :meth:`~heaton_voxel.craft.build.Builder.ornamental_tree`'s, its trunk
    four or five blocks of the wood's log, upright, under a canopy of its
    own leaves, ``persistent`` so they never decay.

    ``kind`` is a wood, and required -- ``oak``, ``birch``, ``cherry``,
    ``pale_oak`` -- and ``leaves`` the canopy where the wood's own name
    does not give one (poplar's come in colors), as one block. A wood the
    game has no log of, or no leaves of when none are given, is an
    ``NBTError`` here, as ``ornamental_tree`` refuses it; so are the two
    nether woods, ``crimson`` and ``warped``, which ``ornamental_tree``
    still plants anywhere: a scattered tree stands on the ground its
    sapling would grow on, and what a fungus grows on (nylium) is not a
    tag craft copies. A Mix or a Material for ``leaves`` is a
    ``TypeError``: a tree is one block of canopy, picked nowhere.

    Immutable and hashable, and equal to a Tree of the same kind whose
    canopy is the same state, properties in any order -- the wood's own
    leaves named outright are the wood's own, so ``Tree("oak",
    leaves="minecraft:oak_leaves[persistent=true]")`` is ``Tree("oak")``,
    and plants the same grove; ``minecraft:oak_leaves`` with no
    ``persistent`` is another canopy, which writes other bytes. ``str()``
    is the text :attr:`ScatterResult.kinds` counts it under, and what a
    scatter's key is made from: ``"tree:oak"``, or ``"tree:oak with
    minecraft:azalea_leaves[persistent=true]"``.
    """

    __slots__ = ("_kind", "_leaves", "_trunk", "_leaf", "_text")

    def __init__(self, kind: str, *, leaves=None):
        if not isinstance(kind, str) or not kind:
            raise TypeError(f"a Tree's kind is a wood, named as a string "
                            f"(oak, birch, cherry), not {kind!r}")
        if kind in _NETHER_TREES:
            raise NBTError(
                f"a scattered tree stands where its sapling would grow, and "
                f"a {kind} fungus grows on {kind} nylium, a footing craft "
                f"does not hold anything to; ornamental_tree plants a "
                f"{kind} tree where it is told")
        if leaves is not None:
            refuse_many(leaves)
            if not isinstance(leaves, (str, BlockState)):
                raise TypeError(f"a Tree's leaves are one block, named by "
                                f"its id or a BlockState, not {leaves!r}")
            text = str(_state(leaves))
        trunk, leaf = _tree_blocks(kind, leaves)
        own = None
        if leaves is not None:
            try:
                own = str(_state(_tree_blocks(kind)[1]))
            except NBTError:            # a wood with no leaves of its own
                pass
        self._kind = kind
        self._leaves = leaves
        self._trunk = trunk
        self._leaf = leaf
        self._text = (f"tree:{kind}" if leaves is None or text == own
                      else f"tree:{kind} with {text}")

    @property
    def kind(self) -> str:
        """The wood."""
        return self._kind

    @property
    def leaves(self):
        """The canopy given, as given; None for the wood's own."""
        return self._leaves

    def __str__(self) -> str:
        return self._text

    def __repr__(self) -> str:
        if self._leaves is None:
            return f"Tree({self._kind!r})"
        return f"Tree({self._kind!r}, leaves={self._leaves!r})"

    def __eq__(self, other) -> bool:
        if not isinstance(other, Tree):
            return NotImplemented
        return self._text == other._text

    def __hash__(self) -> int:
        return hash((Tree, self._text))


class ScatterResult:
    """What :meth:`PlantingMixin.scatter` planted, and why it planted no
    more.

    ``planted`` is the cell each planting stands in -- a flower's own, a
    tall plant's lower half, a tree's trunk foot: the cell over its ground
    -- in the order written, ``(z, x)``; :attr:`columns` is the same as
    ``(x, z)``, to feed a later call's ``apart_from=``. ``kinds`` counts
    them by what was planted, the block's state as text, its properties
    sorted (``"minecraft:poppy"``), or the :class:`Tree`'s
    (``"tree:oak"``), in the order each was first planted.

    ``skipped`` maps each reason a column was passed over, of
    :data:`SKIP_REASONS` and in their order, to the columns it passed over
    for it, ``(z, x)``: ``"avoided"`` (in ``avoid=``), ``"paved"`` (a road
    or a floor this session laid), ``"keep-out"`` (a pad or a reservation
    not named in ``through=``) -- each of those three for a tree where any
    column its canopy reaches is -- ``"no ground"`` (a chunk not generated or
    not finished, or a column with nothing in it), ``"water"`` (water or an
    ice sheet), ``"wrong ground"`` (a ground block ``on=`` does not take),
    ``"off its soil"`` (one the thing picked there will not stand on),
    ``"dry cane"`` (sugar cane with no water beside its block),
    ``"occupied"`` (the cells it would stand in hold something it may not
    replace), ``"claimed"`` (a cell it could fill, a tree's canopy among
    them, is a flight's or an approach's tread or head room),
    ``"cannot be written"`` (its cells, a tree's whole canopy box,
    reaching an unfinished chunk or past the world) and
    ``"refused by worksite"`` (a write the Builder's guard refuses: a file
    the snapshot does not hold, a protected zone). Only reasons that
    passed a column over are keys. ``thinned`` counts the columns the
    ``density=`` and ``cover=`` dice passed over, and ``crowded`` the
    columns that could have taken a planting but stood within ``spacing``
    of one kept before them, or of a column of ``apart_from``.
    """

    __slots__ = ("planted", "kinds", "skipped", "thinned", "crowded")

    def __init__(self):
        self.planted: tuple = ()
        self.kinds: dict[str, int] = {}
        self.skipped: dict[str, tuple] = {}
        self.thinned = 0
        self.crowded = 0

    @property
    def columns(self) -> tuple[tuple[int, int], ...]:
        """The ``(x, z)`` of every planting, in the order written."""
        return tuple((x, z) for x, _, z in self.planted)

    def __repr__(self) -> str:
        kinds = ", ".join(f"{text} {count}" for text, count in self.kinds.items())
        parts = [f"{len(self.planted)} planted ({kinds})" if kinds
                 else "0 planted"]
        for word in ("thinned", "crowded"):
            if getattr(self, word):
                parts.append(f"{getattr(self, word)} {word}")
        skipped = ", ".join(f"{len(columns)} {reason}"
                            for reason, columns in self.skipped.items())
        if skipped:
            parts.append(f"skipped {skipped}")
        return f"<ScatterResult {', '.join(parts)}>"


class _Thing:
    """One thing a scatter may plant, judged before a column is read: its
    text, the state it writes (None for a Tree), whether it is placed whole
    (a tall plant), and the bare names of the ground it stands on
    (:data:`_ANYWHERE` for a boulder)."""

    __slots__ = ("text", "state", "tree", "tall", "soil", "cane")

    def __init__(self, thing):
        if isinstance(thing, Tree):
            self.text = str(thing)
            self.state = None
            self.tree = thing
            self.tall = False
            self.soil = _SOIL
            self.cane = False
            return
        state = _state(thing)
        self.text = str(state)
        self.state = state
        self.tree = None
        self.soil = _footing_of(state)
        self.cane = _bare(state.name) == "sugar_cane"
        defaults = BLOCK_DEFAULTS.get(state.name, {})
        self.tall = defaults.get("half") == "lower" and self.soil is not _ANYWHERE

    def cells(self, x: int, y: int, z: int, trunk: int) -> list:
        """The cells this thing stands in over ground at ``y``: a plant's
        one or two, a tree's trunk -- its canopy grows round whatever is
        there -- or a boulder's one."""
        if self.tree is not None:
            return [(x, y + 1 + i, z) for i in range(trunk)]
        if self.tall:
            return [(x, y + 1, z), (x, y + 2, z)]
        return [(x, y + 1, z)]

    def reach(self, x: int, y: int, z: int, trunk: int) -> list:
        """Every cell this thing could fill over ground at ``y``: its
        :meth:`cells`, and a tree's whole canopy -- whichever of its cells
        turn out to hold nothing."""
        if self.tree is not None:
            return [(x + dx, y + 1 + dy, z + dz)
                    for dx, dy, dz in _TREE_CELLS[trunk]]
        return self.cells(x, y, z, trunk)

    def columns(self, x: int, z: int) -> list:
        """The columns this thing reaches from ``(x, z)``: its own, and a
        tree's canopy round it."""
        if self.tree is not None:
            return [(x + dx, z + dz) for dx, dz in _CANOPY]
        return [(x, z)]

    def over(self) -> str:
        """The id of the block this thing sets in the cell over its
        ground: a tree's trunk, or its own."""
        if self.tree is not None:
            return _state(self.tree._trunk).name
        return self.state.name


def _footing_of(state: BlockState):
    """The bare names of the ground ``state`` may be planted on -- its
    game tag's copy in :mod:`~heaton_voxel.craft.survey` -- or
    :data:`_ANYWHERE` for a boulder: a block the game has, sturdy on all
    six faces, that :func:`~heaton_voxel.craft.survey.unsupported` asks
    nothing of (nothing it stands on or beside can lose it), and that does
    not melt where it stands (:data:`_MELTS`). That is all a boulder is
    held to: the game may still change one in place -- copper weathers, a
    coral block dies out of water -- and a block that carries a block
    entity, a furnace or a spawner, is written as its state alone, as every
    Builder writer writes one. Anything else is an ``NBTError``, before
    anything is read: a block whose rule of what keeps it craft does not
    copy."""
    name = state.name
    if known(name) and name.startswith("minecraft:"):
        bare = _bare(name)
        soil = _footing(bare)
        if soil is not None:
            return soil
        if bare in _MELTS:
            raise NBTError(
                f"scatter plants a boulder only where it stays, and {state} "
                f"melts where it stands: ice into a water source at block "
                f"light 11, frosted ice by daylight, a snow layer at 12 -- "
                f"packed or blue ice keeps")
        if (not _fragile(name)
                and facts(name, state.properties).sturdy == _ALL_FACES):
            return _ANYWHERE
    raise NBTError(
        f"scatter plants what craft.survey holds to the game's rule of what "
        f"keeps it -- a flower, grass, a fern, a sapling, a berry bush, "
        f"petals, a tall plant, an azalea, a dead bush, sugar cane, a Tree, "
        f"or a block sturdy on every face that nothing can lose and that "
        f"does not melt -- and "
        f"{state} is not one: where the game keeps it is no rule craft "
        f"copies, so it would be planted by guess")


def _snowy_under(ground: BlockState, above: str):
    """``ground`` with its ``snowy`` flag as the block ``above`` sets it,
    or None where it already is, or has no flag: a grass block, podzol or
    mycelium is ``snowy`` exactly when a block of the ``snow`` tag stands on
    it (``SnowyBlock.updateShape``, run by the block update a planting is),
    so taking a snow layer off for a poppy clears it, and a snow block
    boulder sets it."""
    defaults = BLOCK_DEFAULTS.get(ground.name)
    if not defaults or "snowy" not in defaults:
        return None
    want = "true" if above in _SNOW_TAG else "false"
    if ground.properties.get("snowy", defaults["snowy"]) == want:
        return None
    return ground.with_properties(snowy=want)


class _Site:
    """A column that may take a planting: where, on what, and what -- and
    the ground under it as the planting leaves it, where that changes."""

    __slots__ = ("x", "z", "y", "thing", "trunk", "clears", "ground")

    def __init__(self, x, z, y, thing, trunk, clears, ground=None):
        self.x = x
        self.z = z
        self.y = y
        self.thing = thing
        self.trunk = trunk
        self.clears = clears
        self.ground = ground

    def footprint(self) -> list:
        """Every cell the planting could write, as plan ops for the write
        check and the guard: the ground's flag, its own cells and the cover
        it clears, and a tree's whole canopy box, since which of its cells
        get leaves is not known until the plan is."""
        ops = [("set", cell, AIR) for cell in self.clears]
        x, y, z, thing = self.x, self.y, self.z, self.thing
        if self.ground is not None:
            ops.insert(0, ("set", (x, y, z), self.ground))
        if thing.tree is not None:
            ops.append(("fill", (x - 2, y + 1, z - 2),
                        (x + 2, y + self.trunk + 2, z + 2), AIR))
        elif thing.tall:
            ops.append(("place", (x, y + 1, z), thing.state))
        else:
            ops.append(("set", (x, y + 1, z), thing.state))
        return ops


class PlantingMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods:
    planting at scale.

    Builder inherits them; nothing here is used on its own.
    """

    def scatter(self, area, things, *, density: float = 1.0,
                spacing: int = 1, clump: int = 1, cover: float = 1.0,
                seed: int = 0, on=None, avoid=(), apart_from=(),
                through=(), overwrite: bool = False) -> ScatterResult:
        """Plant ``things`` over ``area``, spaced and thinned by dice keyed
        on each column, and only where the game will keep them.

        ``area`` is an inclusive rectangle ``(x0, z0, x1, z1)`` or any
        ``(x, z)`` columns -- a :func:`~heaton_voxel.craft.geometry.disc`,
        an elliptical bed a comprehension drew -- taken once each
        and in ``(z, x)`` order whatever order they come in. ``things`` is
        one block, one :class:`Tree`, a
        :class:`~heaton_voxel.craft.style.Mix` of blocks (its pick at
        ``(x, 0, z)``: the Mix's own seed and clump draw its picture), or a
        sequence of ``(thing, weight)`` pairs, each thing a block or a Tree
        and each weight a whole number of at least 1. What may be planted
        is refused before anything is read (see the module): a block the
        game lacks is an :class:`~heaton_voxel.craft.errors.IllegalBlock`,
        and anything whose footing craft does not copy an ``NBTError``
        naming it -- a modded block ``Builder(allow=)`` passes among them,
        since what keeps a modded plant is no rule craft copies; so is
        a :class:`Tree` with ``spacing`` under 3, since a canopy reaches two
        columns out and no trunk may be planned inside another's.

        Each column is passed over, counted in the result and never
        written, where it is in ``avoid``, on a path this session paved (a
        :meth:`road`'s swath, a :meth:`pave`'s floor; a path from an
        earlier session goes in ``avoid``), in a pad or a reservation not
        named in ``through`` -- for a tree, where any column its canopy
        reaches is, the twenty round its trunk -- or thinned: kept only
        where its ``density`` die and its ``cover`` die both pass, each a
        share of the columns over 0 and at most 1 -- ``density`` rolled per
        column, ``cover``
        per patch of about ``clump`` columns across
        (:func:`~heaton_voxel.craft.noise.patch`, 1 to
        :data:`~heaton_voxel.craft.noise.MAX_PATCH`), so that ``clump=4,
        cover=0.3`` plants three patches in ten whole and leaves the rest
        bare, the park's flower clumps. From a list, one kind is picked per
        patch too, in proportion to the weights, as a Mix keeps them. Then
        its ground is read (:meth:`probe`, or the height this session gave
        it), and it is passed over where there is none, where it is water
        or an ice sheet, where ``on`` -- a predicate on the ground block's
        id, :func:`~heaton_voxel.craft.blockinfo.is_natural_ground` unless
        given -- refuses it, where the thing picked there will not stand on
        it (the soil its game tag names; sugar cane also needs water
        beside its block), where the cells it would stand in -- a
        plant's one or two, a tree's trunk -- hold anything but air, or
        where a cell it could fill, a tree's whole canopy among them, is one
        a :meth:`flight` or an :meth:`approach` this session built claims:
        its treads or its surface, or the head room over them. With
        ``overwrite=True``, ground cover there (a flower, grass, lying
        snow) is replaced, the other half of a two-block plant cleared with
        it, unless something that needs it stands on it. A tree's canopy
        fills only the cells that hold nothing, as
        :meth:`ornamental_tree`'s does. A chunk that cannot be read is
        corruption, and raises its ``NBTError``.

        The columns left are spaced: taken in an order keyed on the cell,
        each kept unless a planting kept before it, or a column of
        ``apart_from`` (a whole number of columns as ``(x, z)``, such as an
        earlier call's :attr:`ScatterResult.columns`), stands closer than
        ``spacing`` -- center to center, ``dx² + dz² < spacing²`` -- and
        unless its cells reach an unfinished chunk or past the world, or a
        worksite's :attr:`guard` refuses them, which pass it over without
        crowding anybody. ``spacing`` is a whole number, 1 or more.

        The plantings are written in ``(z, x)`` order: a block ``set`` in
        the cell over its ground, a tall plant placed whole, a tree as
        :meth:`ornamental_tree` grows one (its trunk four or five blocks by
        its own die), its canopy reading the plantings before it, so a
        leaf two trees could both grow is the first's -- exactly
        ``ornamental_tree`` at each in that order. Where the ground is a
        grass block, podzol or mycelium, its ``snowy`` flag is written as
        the block now standing on it sets it, as the game's block update
        would: cleared under a poppy that took a snow layer's place, set
        under a snow block. The whole plan goes
        through the survival audit before it is written, and a call that
        would plant nothing is refused, naming why each column was passed
        over; either way nothing is written. Nothing is drawn from
        :attr:`rng`: ``seed`` keys the dice, so the same area, things and
        seed over the same world, ground, keep-outs, paths and claims plant
        the same columns in any session. Returns a :class:`ScatterResult`.
        """
        self._alive()
        columns, _ = _outline(area, "scatter", "plant", noun="area")
        density = _fraction(density, "density")
        cover = _fraction(cover, "cover")
        if not _is_whole(spacing) or spacing < 1:
            raise NBTError(f"spacing is the least distance between two "
                           f"plantings, a whole number of blocks, 1 or "
                           f"more, not {spacing!r}")
        if not _is_whole(clump) or not 1 <= clump <= MAX_PATCH:
            raise NBTError(f"clump is the width of a patch, a whole number "
                           f"of blocks, 1 to {MAX_PATCH}, not {clump!r}")
        if not _is_whole(seed):
            raise TypeError(f"scatter's seed is a whole number, not {seed!r}")
        if on is None:
            on = is_natural_ground
        elif not callable(on):
            raise TypeError(f"on is a predicate on the ground block's id, "
                            f"not {on!r}")
        avoid = {_column(c, "avoid") for c in avoid}
        apart = sorted({_column(c, "apart_from") for c in apart_from},
                       key=lambda c: (c[1], c[0]))
        through = tuple(through)

        entries, mix = _entries(things)
        # Every block by name, then what keeps each: an id the game lacks
        # is its IllegalBlock, before a thing it has is judged.
        blocks = []
        for thing, _ in entries:
            if isinstance(thing, Tree):
                blocks.extend((thing._trunk, thing._leaf))
            else:
                blocks.append(thing)
        self._vet_names(blocks, what="scatter")
        judged = {}
        for thing, _ in entries:
            if thing not in judged:
                judged[thing] = _Thing(thing)
        if spacing < 3 and any(isinstance(t, Tree) for t, _ in entries):
            raise NBTError(
                f"a Tree needs spacing of 3 or more, not {spacing}: its "
                f"canopy reaches two columns out, and a trunk planned inside "
                f"another tree's canopy would be planted into its leaves")
        key = (seed ^ zlib.crc32(_canonical(entries, mix).encode("utf-8"))) & MASK
        bounds, total = [], 0
        for _, weight in entries:
            total += weight
            bounds.append(total)

        def pick(x: int, z: int, at) -> _Thing:
            if mix is not None:
                return judged[mix.pick(x, 0, z)]
            if len(entries) == 1:
                return judged[entries[0][0]]
            index = _weighted(bounds, total, cell_hash(*at, key ^ _PICK))
            return judged[entries[index][0]]

        # Passed over without a read: avoided, paved, kept out, thinned --
        # a tree by any column its canopy reaches, the keep-outs read over
        # those too.
        skipped: dict[str, list] = {reason: [] for reason in SKIP_REASONS}
        reached = columns
        if any(isinstance(t, Tree) for t, _ in entries):
            reached = [(x + dx, z + dz) for x, z in columns
                       for dx, dz in _CANOPY]
        held = set()
        for _, _, hits in self._keep_out_faults(
                reached, through=through, what="scatter would plant in"):
            held.update(hits)
        cover_bound = int(cover * 2 ** 32)
        density_bound = int(density * 2 ** 32)
        thinned = 0
        sites: list[_Site] = []

        def block_at(x: int, y: int, z: int):
            return self._peek((x, y, z))

        for x, z in columns:
            at = (x, 0, z) if clump == 1 else patch(x, 0, z, clump,
                                                     key ^ _JITTER)
            thing = pick(x, z, at)
            around = thing.columns(x, z)
            if any(c in avoid for c in around):
                skipped["avoided"].append((x, z))
                continue
            if any(c in self._paved for c in around):
                skipped["paved"].append((x, z))
                continue
            if any(c in held for c in around):
                skipped["keep-out"].append((x, z))
                continue
            if (cell_hash(*at, key ^ _COVER) >= cover_bound
                    or cell_hash(x, 0, z, key ^ _DENSITY) >= density_bound):
                thinned += 1
                continue
            # The ground, and what the thing picked here asks of it.
            try:
                y, wet = self._stand((x, z))
            except NoSurface:
                skipped["no ground"].append((x, z))
                continue
            if wet is not None:
                skipped["water"].append((x, z))
                continue
            ground = self._peek((x, y, z))
            if ground is None or not on(ground.name):
                skipped["wrong ground"].append((x, z))
                continue
            if thing.soil is not _ANYWHERE:
                if _bare(ground.name) not in thing.soil:
                    skipped["off its soil"].append((x, z))
                    continue
                if thing.cane and not _wet_beside(block_at, x, y, z):
                    skipped["dry cane"].append((x, z))
                    continue
            trunk = (4 + (cell_hash(x, 0, z, key ^ _TRUNK) & 1)
                     if thing.tree is not None else 0)
            clears = self._clears_for(x, z, thing.cells(x, y, z, trunk),
                                      overwrite)
            if clears is None:
                skipped["occupied"].append((x, z))
                continue
            # A flight's or an approach's treads and head room: nothing goes
            # in them, a leaf no more than a stone (shell's and furnish's
            # rule).
            if self._claimed and any(cell in self._claimed for cell
                                     in thing.reach(x, y, z, trunk)):
                skipped["claimed"].append((x, z))
                continue
            sites.append(_Site(x, z, y, thing, trunk, clears,
                               _snowy_under(ground, thing.over())))

        # Dart throwing in the keyed order: a site is kept unless one kept
        # before it, or a column of apart_from, is closer than the spacing.
        # Whether a site can be written at all is asked only of one nobody
        # crowds out, and one that cannot crowds out nobody.
        sites.sort(key=lambda s: (cell_hash(s.x, 0, s.z, key ^ _ORDER),
                                  s.z, s.x))
        reach = spacing * spacing
        grid: dict[tuple[int, int], list] = {}
        for ax, az in apart:
            grid.setdefault((ax // spacing, az // spacing), []).append((ax, az))
        kept: list[_Site] = []
        crowded = 0
        for site in sites:
            gx, gz = site.x // spacing, site.z // spacing
            if any((px - site.x) ** 2 + (pz - site.z) ** 2 < reach
                   for nx in (gx - 1, gx, gx + 1)
                   for nz in (gz - 1, gz, gz + 1)
                   for px, pz in grid.get((nx, nz), ())):
                crowded += 1
                continue
            footprint = site.footprint()
            if self._write_faults(footprint):
                skipped["cannot be written"].append((site.x, site.z))
                continue
            if self._guard is not None and self._guard.refusals(
                    [box for op in footprint for box in self._boxes(op)]):
                skipped["refused by worksite"].append((site.x, site.z))
                continue
            kept.append(site)
            grid.setdefault((gx, gz), []).append((site.x, site.z))

        found = {reason: tuple(sorted(cols, key=lambda c: (c[1], c[0])))
                 for reason, cols in skipped.items() if cols}
        if not kept:
            why = [f"{len(cols)} {reason}, first {cols[0]}"
                   for reason, cols in found.items()]
            if thinned:
                why.append(f"{thinned} thinned by the density and cover dice")
            if crowded:
                why.append(f"{crowded} within spacing={spacing} of a column "
                           f"of apart_from")
            raise Refused(
                f"scatter has nothing to plant in its {len(columns)} "
                f"columns: {'; '.join(why)}. Nothing was written",
                faults=why)

        # The plan, in (z, x) order; a tree's canopy reads the plantings
        # planned before it over the world.
        kept.sort(key=lambda s: (s.z, s.x))
        planned: dict[tuple[int, int, int], BlockState] = {}

        def read(x: int, y: int, z: int):
            cell = (x, y, z)
            if cell in planned:
                return planned[cell]
            return self._peek(cell)

        ops: list[tuple] = []
        kinds: dict[str, int] = {}
        for site in kept:
            x, y, z, thing = site.x, site.y, site.z, site.thing
            if site.ground is not None:
                ops.append(("set", (x, y, z), site.ground))
                planned[(x, y, z)] = site.ground
            for cell in site.clears:
                ops.append(("set", cell, AIR))
                planned[cell] = _AIR_STATE
            if thing.tree is not None:
                tree = self._tree_ops(x, y + 1, z, thing.tree._trunk,
                                      thing.tree._leaf, site.trunk, read=read)
                for op in tree:
                    planned[op[1]] = _state(op[2])
                ops.extend(tree)
            elif thing.tall:
                ops.append(("place", (x, y + 1, z), thing.state))
                base = {k: v for k, v in thing.state.properties.items()
                        if k != "half"}
                planned[(x, y + 1, z)] = BlockState(
                    thing.state.name, dict(base, half="lower"))
                planned[(x, y + 2, z)] = BlockState(
                    thing.state.name, dict(base, half="upper"))
            else:
                ops.append(("set", (x, y + 1, z), thing.state))
                planned[(x, y + 1, z)] = thing.state
            kinds[thing.text] = kinds.get(thing.text, 0) + 1
        # The guarantee: what the game will not keep is refused whole.
        self._audit_plan(ops, what="scatter")
        self._commit(ops, what="scatter")

        result = ScatterResult()
        result.planted = tuple((s.x, s.y + 1, s.z) for s in kept)
        result.kinds = kinds
        result.skipped = found
        result.thinned = thinned
        result.crowded = crowded
        return result

    def _clears_for(self, x: int, z: int, cells, overwrite: bool):
        """What must be cleared before a planting takes ``cells`` of its
        column, read before anything is written: ``[]`` where every one
        holds nothing (any of the game's airs, or nothing to read, which
        the write check judges), None where one holds something the
        planting may not take. Only with ``overwrite`` may it take ground
        cover (:func:`~heaton_voxel.craft.blockinfo.is_vegetation`, lying
        snow among it), and then the other half of a two-block plant goes
        with it -- the cells returned -- unless something that needs what
        it takes stands over it (a stack of cane on the cane replaced),
        which is None too: the game would drop it."""
        clears: list = []
        took = False
        for cell in cells:
            found = self._peek(cell)
            if found is None or _empty(found):
                continue
            if not overwrite or not is_vegetation(found.name):
                return None
            took = True
            half = found.properties.get(
                "half", BLOCK_DEFAULTS.get(found.name, {}).get("half"))
            if half not in ("lower", "upper"):
                continue
            other = (x, cell[1] + (1 if half == "lower" else -1), z)
            if other in cells or other in clears:
                continue
            twin = self._peek(other)
            if twin is not None and twin.name == found.name:
                clears.append(other)
        if took:
            top = max(c[1] for c in (*cells, *clears))
            over = self._peek((x, top + 1, z))
            if over is not None and _fragile(over.name):
                return None
        return clears


def _column(value, what: str) -> tuple[int, int]:
    """An ``(x, z)`` of ``avoid=`` or ``apart_from=``, or an ``NBTError``."""
    return geometry._pair(value, f"a column of {what}")


def _entries(things):
    """``([(thing, weight), ...], mix)``: what a scatter plants, each thing a
    block or a :class:`Tree`, and the Mix it is picked from, or None for a
    weighted pick of the scatter's own. Shapes only: a Material, a Mix
    inside a list, or anything that is not a block, a Tree or a pair is a
    ``TypeError``; no entries, or a weight that is not a whole number of
    at least 1, an ``NBTError``."""
    if isinstance(things, Mix):
        return [(block, weight) for block, weight in things.entries], things
    if isinstance(things, (str, BlockState, Tree)):
        return [(things, 1)], None
    refuse_many(things)
    try:
        listed = tuple(things)
    except TypeError:
        raise TypeError(f"scatter plants a block, a Tree, a Mix or a list of "
                        f"(thing, weight) pairs, not {things!r}") from None
    if not listed:
        raise NBTError("scatter's things hold nothing to plant")
    entries = []
    for entry in listed:
        if (isinstance(entry, (str, BlockState, Tree))
                or not isinstance(entry, (tuple, list)) or len(entry) != 2):
            raise TypeError(f"each entry of a list of things is a (thing, "
                            f"weight) pair, not {entry!r}")
        thing, weight = entry
        if not isinstance(thing, (str, BlockState, Tree)):
            refuse_many(thing)
            raise TypeError(f"a thing to plant is a block or a Tree, not "
                            f"{thing!r}")
        if not _is_whole(weight) or weight < 1:
            raise NBTError(f"{thing} has weight {weight!r}, and a weight is "
                           f"a whole number, 1 or more")
        entries.append((thing, weight))
    return entries, None


def _canonical(entries, mix) -> str:
    """What a scatter's key is made from: each thing as its state's text
    (properties sorted) or its Tree's, with its weight, as a Mix writes its
    own entries -- and for a Mix, its seed and clump too, which its picks
    read."""
    text = "\n".join(f"{thing if isinstance(thing, Tree) else _state(thing)}"
                     f"\t{weight}" for thing, weight in entries)
    if mix is not None:
        text = f"mix\n{text}\nseed\t{mix.seed & MASK}\nclump\t{mix.clump}"
    return text
