"""What a block is, as the judged tier reads it: one home for block knowledge.

Two kinds of answer live here, and they are kept apart on purpose.

The game's answers -- whether it has a block at all, which properties the
block takes and which values (:func:`check_state`, :func:`is_block`,
:func:`properties_of`), what is air, what falls, what holds fluid and what
holds water -- are generated from the jar into
:mod:`~heaton_voxel.craft.block_registry` and
:mod:`~heaton_voxel.craft.block_facts` and only gathered here. The game
refuses none of what it does not have: an id it does not know loads as air,
a property or a value it does not take loads as the block's default, and
the only trace is a line in a log nobody reads. :func:`check_state` is the
refusal the game never makes.

The categories -- :func:`is_tree`, :func:`is_vegetation`,
:func:`subsoil_of`, :func:`material_of`, :func:`form_of` -- are a
builder's judgments ("fell this", "clear that", "fill under this with
that"), so they are written by hand. Each is a copy of the game's own
block tags (``logs``, ``leaves``, ``saplings``, ``wart_blocks``,
``replaceable_by_trees``, ``small_flowers``, ``dirt``, ``sand``,
``base_stone_overworld`` and the rest), which the probe vendors into
``fixtures/block_shapes.json`` and ``tests/test_blockinfo.py`` holds the
copies to. Where a category departs from its tags, the departure
is a named choice in a table beside it, with its reason, and the test fails
a choice that stops excusing anything -- so a game update that adds a log
or a flower fails by name instead of being cleared around. The test keeps
its own list of the choices, so a table edit here that changes a verdict
fails by name as well.

Every category reads a name the game has retired as the block it became
(:data:`RENAMED`): a chunk saved before the rename still holds the old id,
and the game's own fixer renames it when it loads that chunk. Only
:func:`is_block` and :func:`check_state` say no to an old name, because a
*write* of one is air.
"""

from __future__ import annotations

import difflib
import re
from functools import lru_cache

from ..blocks import AIR_BLOCKS, BlockState
from ..nbt import NBTError
from ._plan import as_state
from .block_facts import holds_fluid, holds_water
from .block_registry import (
    BLOCK_IDS,
    DATA_VERSION,
    FALLS,
    FLUIDS,
    GAME_VERSION,
    properties_of,
)
from .errors import IllegalBlock

__all__ = [
    "AIR_BLOCKS",
    "FLUIDS",
    "GAME_VERSION",
    "DATA_VERSION",
    "RENAMED",
    "properties_of",
    "holds_fluid",
    "holds_water",
    "is_air",
    "is_block",
    "check_state",
    "falls",
    "is_tree",
    "is_vegetation",
    "subsoil_of",
    "is_natural_ground",
    "material_of",
    "cut_shapes",
    "FORMS",
    "form_of",
]


def _ids(text: str) -> frozenset[str]:
    return frozenset(f"minecraft:{name}" for name in text.split())


def _path(name: str) -> str:
    """``name`` without its namespace."""
    return name.partition(":")[2] or name


# --------------------------------------------------------------------------
# The game's answers
# --------------------------------------------------------------------------

#: Every block the game has renamed since DataVersion 2724 (1.17, the
#: oldest chunk the library reads): old id -> (new id, the DataVersion of
#: the rename). These two are all of them -- the 26.3 jar's ``DataFixers``
#: registers no other ``LegacyBlockRenameFix`` past 2724, and the game's
#: own upgrade of the 1.17 fixture renames no other block
#: (``tests/test_legacy.py``). A chunk saved
#: before a rename holds the old id and the game renames it on load; a
#: chunk saved after loads it as air.
RENAMED = {
    "minecraft:grass": ("minecraft:short_grass", 3692),
    "minecraft:chain": ("minecraft:iron_chain", 4541),
}


def _current(name: str) -> str:
    """``name``, or what the game renamed it to."""
    renamed = RENAMED.get(name)
    return renamed[0] if renamed else name


def is_air(name: str) -> bool:
    """Whether ``name`` is nothing: air, cave air or void air, the game's
    own ``air`` tag (:data:`AIR_BLOCKS`). The carvers wrote cave air until
    1.18, and dungeons, mineshafts and strongholds still do, so an old cave
    or a structure's rooms are full of it, and the game's own ``isAir`` is
    true of all three."""
    return name in AIR_BLOCKS


def is_block(name: str) -> bool:
    """Whether the game has a block called ``name``. An id it does not have
    is air to the game, whatever a table that has never heard of it guesses
    (:func:`~heaton_voxel.craft.block_facts.facts` answers a cube)."""
    return name in BLOCK_IDS


#: What an ``allow=`` entry may be: an id, or a namespace ending in ``:``,
#: in the characters the game's ``Identifier`` takes -- ``a-z0-9_.-`` in
#: a namespace, and ``/`` besides in a path. Anything else names no block
#: the game could ever load, so it would match nothing.
_ALLOWANCE = re.compile(r"[a-z0-9_.-]+:[a-z0-9_./-]*")


def _allowance(allow) -> frozenset[str]:
    """``allow=`` as a set, refusing an entry that can match nothing."""
    entries = (allow,) if isinstance(allow, str) else tuple(allow or ())
    for entry in entries:
        if not isinstance(entry, str) or not _ALLOWANCE.fullmatch(entry):
            raise NBTError(
                f"allow= takes block ids ('create:gearbox') or namespaces "
                f"ending in ':' ('create:'), lowercase and with no state; "
                f"{entry!r} is neither, so it would match nothing")
    return frozenset(entries)


def _allowed(name: str, allowed: frozenset[str]) -> bool:
    namespace, colon, _ = name.partition(":")
    return name in allowed or (bool(colon) and f"{namespace}:" in allowed)


@lru_cache(maxsize=None)
def _cuts() -> dict[str, dict[str, str]]:
    """Each full block's cut shapes the game has: base -> {shape: id}.

    A cut whose own name leads to the block comes first, and one named in
    ``_CUT_FROM`` only fills a shape nothing else does: oak planks' slab is
    ``oak_slab``, never the petrified oak slab, the stone slab left over
    from the old wooden one that only wears the planks' face."""
    found: dict[str, dict[str, str]] = {}
    names = sorted(BLOCK_IDS)
    for overridden in (False, True):
        for name in names:
            if (name in _CUT_FROM) != overridden:
                continue
            base, shape = material_of(name)
            if shape != "block":
                found.setdefault(base, {}).setdefault(shape, name)
    return found


@lru_cache(maxsize=None)
def _ordered_ids() -> tuple[str, ...]:
    return tuple(sorted(BLOCK_IDS))


def _unknown(name: str) -> tuple[str, list[str], bool]:
    """Why ``name`` is not a block, the real ids nearest it, and whether
    the reason is only that the registry has never heard of it (so that a
    modded or newer block is the likely cause)."""
    namespace, colon, path = name.partition(":")
    if not colon or not namespace:
        # The game reads an id with no namespace (or an empty one) as
        # minecraft's -- Identifier.parse -- but every table here keys a
        # block by its whole id, so a bare name would match none of them.
        full = f"minecraft:{path if colon else name}"
        lead = (f"{name} names no namespace, which every id here needs "
                f"(the game would read it as {full})")
        if full in BLOCK_IDS:
            return lead, [full], False
        reason, suggestions, unexplained = _unknown(full)
        return f"{lead}; {reason}", suggestions, unexplained
    renamed = RENAMED.get(name)
    if renamed:
        new, version = renamed
        return (f"{name} is not a block in Minecraft {GAME_VERSION}: it was "
                f"renamed {new} at DataVersion {version}, and the game loads "
                f"the old name as air"), [new], False
    parts = [f"{name} is not a block in Minecraft {GAME_VERSION}, and the "
             f"game loads it as air"]
    suggestions: list[str] = []
    base, shape = material_of(name)
    cut_from_real = shape != "block" and base in BLOCK_IDS
    if cut_from_real:
        shapes = _cuts().get(base, {})
        cuts = sorted(shapes.values())
        if shape in shapes:
            parts.append(f"{base} cut as {shape} is {shapes[shape]}")
            suggestions.append(shapes[shape])
        elif cuts:
            listed = cuts[0] if len(cuts) == 1 else \
                f"{', '.join(cuts[:-1])} and {cuts[-1]}"
            parts.append(f"{base} comes as {listed} but has no {shape}")
        else:
            parts.append(f"{base} comes in no cut shape at all")
    near = difflib.get_close_matches(name, _ordered_ids(), 3, 0.8)
    if near:
        parts.append(f"nearest: {', '.join(near)}")
    suggestions += [found for found in near if found not in suggestions]
    if cut_from_real and base not in suggestions:
        suggestions.append(base)
    return "; ".join(parts), suggestions, True


def check_state(block, *, allow=()) -> BlockState:
    """``block`` as a :class:`~heaton_voxel.blocks.BlockState`, refused
    unless the game has it.

    ``block`` is a name (``"minecraft:oak_stairs[facing=east]"``), a
    ``BlockState`` or a ``(name, properties)`` pair; text that does not
    parse raises ``ValueError``, as it does everywhere. It comes back
    exactly as given -- a ``BlockState`` is the same object -- with no
    defaults filled in: a property left out is legal, because the game
    fills it from the block's defaults (and a 26.3 chunk writes it so).

    Raises :class:`~heaton_voxel.craft.errors.IllegalBlock`, a
    :class:`~heaton_voxel.craft.errors.Refused`, for an id the game does
    not have -- naming what it was renamed to if it was, or else the cut
    shapes its block does come in and the nearest real spellings, in
    ``.suggestions`` -- and for every property the block does not take and
    every value outside a property's own, each one a fault. A value is
    text as the game writes it: ``waterlogged=True`` is not ``true``. A
    name with no namespace is refused too, though the game would read
    ``stone`` as ``minecraft:stone``: every table here keys a block by its
    whole id, so the refusal names that id (and its replacement, if the
    game has since renamed it).

    ``allow`` passes ids the registry cannot know, a modded block or one
    newer than the game it was generated from, unchecked: exact ids
    (``"create:gearbox"``), or whole namespaces ending in ``":"``
    (``"create:"``). An entry that is neither, or that the game could not
    read as an id (uppercase, a space, a state's ``[...]``), would match
    nothing, and raises ``NBTError``.
    """
    state = as_state(block)
    name = state.name
    if _allowed(name, _allowance(allow)):
        return state
    domains = properties_of(name)
    if domains is None:
        fault, suggestions, unexplained = _unknown(name)
        hint = (f". A modded block, or one newer than this {GAME_VERSION} "
                f"registry, goes in allow= (after a game update, rerun "
                f"tools/probe_block_shapes.py)") if unexplained else ""
        raise IllegalBlock(fault + hint, [fault], suggestions=suggestions)
    faults = []
    for key, value in sorted(state.properties.items(), key=lambda kv: str(kv[0])):
        values = domains.get(key)
        if values is None:
            has = ", ".join(domains) if domains else "none at all"
            faults.append(f"{name} has no property {key!r}; it has {has}")
        elif value not in values:
            shown = value if isinstance(value, str) else f"{value!r} (not text)"
            faults.append(f"{name}[{key}={shown}]: {key} is one of "
                          f"{', '.join(values)}")
    if faults:
        more = f", and {len(faults) - 1} more" if len(faults) > 1 else ""
        raise IllegalBlock(
            f"{state} is not a state Minecraft {GAME_VERSION} has: "
            f"{faults[0]}{more}. The game would keep the block's default in "
            f"its place and say so only in its log", faults)
    return state


#: What :data:`~heaton_voxel.craft.block_registry.FALLS` holds that does not
#: fall the way :func:`falls` means, each with its reason. Both are the
#: game's ``SpeleothemBlock``.
_HANGS_FROM_ABOVE = {
    "minecraft:pointed_dripstone":
        "a stalactite falls when the block it hangs from goes and a "
        "stalagmite breaks when the one under it does, so it never drops "
        "through a hole the way sand does",
    "minecraft:sulfur_spike":
        "the same speleothem as pointed dripstone, hanging or standing the "
        "same way",
}


def falls(name: str) -> bool:
    """Whether ``name`` falls when nothing holds it up from below -- sand,
    gravel, concrete powder, an anvil, the dragon egg: the game's
    ``Fallable`` blocks, less the speleothems, which hang
    (``_HANGS_FROM_ABOVE``)."""
    name = _current(name)
    return name in FALLS and name not in _HANGS_FROM_ABOVE


# --------------------------------------------------------------------------
# Categories: a builder's judgments, held to the game's tags
# --------------------------------------------------------------------------

#: The game's ``logs``, ``leaves`` and ``saplings`` tags, by whole suffix,
#: so a modded tree is one too. Matched exactly, never by bare substring.
_TREE_SUFFIXES = ("_log", "_wood", "_hyphae", "_leaves", "_sapling")
#: The rest of those tags and the ``wart_blocks`` tag: what the suffixes
#: miss.
_TREE_NAMES = _ids("""
crimson_stem warped_stem stripped_crimson_stem stripped_warped_stem azalea
flowering_azalea mangrove_propagule nether_wart_block warped_wart_block
""")
#: What the suffixes catch that the tags do not hold, each with its reason.
#: Every potted plant is furniture besides (the ``potted_`` prefix).
_NOT_TREES = {
    "minecraft:bamboo_sapling": "a bamboo shoot, not a tree's sapling",
}
#: Where :func:`is_tree` departs from the tags, each with its reason.
_TREE_EXTRAS = {
    "minecraft:bee_nest":
        "it grows on a trunk, and is left hanging in the air once the tree "
        "around it is felled",
    "minecraft:mushroom_stem":
        "a huge mushroom stands on the ground the way a tree does and is "
        "cleared with the trees",
    "minecraft:brown_mushroom_block": "a huge mushroom's cap",
    "minecraft:red_mushroom_block": "a huge mushroom's cap",
    "minecraft:creaking_heart":
        "the game grows it in a pale oak's trunk, in place of a log with "
        "logs on all six sides, so a trunk felled without it stops "
        "halfway down",
}


def is_tree(name: str) -> bool:
    """Whether ``name`` is part of a tree: a log, wood, a stem or hyphae, a
    leaf, a sapling, a nether wart block -- the game's ``logs``,
    ``leaves``, ``saplings`` and ``wart_blocks`` tags -- or a huge
    mushroom, a bee nest or a pale oak's creaking heart
    (``_TREE_EXTRAS``). Never a potted sapling or a bamboo shoot, and
    never a shroomlight, which a huge fungus grows in its cap but a
    builder hangs as a lamp."""
    name = _current(name)
    if name in _TREE_NAMES or name in _TREE_EXTRAS:
        return True
    if name in _NOT_TREES or _path(name).startswith("potted_"):
        return False
    return name.endswith(_TREE_SUFFIXES)


#: The game's ``replaceable_by_trees`` tag -- what a growing tree writes
#: over -- with every ``small_flowers`` member, less its leaves, which are
#: trees (:func:`is_tree`), so that nothing is both.
_GROUND_COVER = _ids("""
allium azure_bluet blue_orchid bush closed_eyeblossom cornflower
crimson_roots dandelion dead_bush fern firefly_bush glow_lichen
golden_dandelion hanging_roots large_fern leaf_litter lilac
lily_of_the_valley nether_sprouts open_eyeblossom orange_tulip oxeye_daisy
pale_moss_carpet peony pink_tulip pitcher_plant poppy red_tulip rose_bush
seagrass shelf_mushroom short_dry_grass short_grass sunflower tall_dry_grass
tall_grass tall_seagrass torchflower vine warped_roots water white_tulip
wither_rose
""")
#: What those tags hold that :func:`is_vegetation` leaves, each with its
#: reason.
_NOT_VEGETATION = {
    "minecraft:water": "water is not cover, and clearing it to air drains it",
    "minecraft:seagrass":
        "it stands in water, and air in its place is a hole in the sea",
    "minecraft:tall_seagrass":
        "it stands in water, and air in its place is a hole in the sea",
}
#: Where :func:`is_vegetation` departs from the tags the other way, each
#: with its reason.
_VEGETATION_EXTRAS = {
    "minecraft:sweet_berry_bush": "a bush on the ground, and it hurts",
    "minecraft:sugar_cane": "a reed on a bank, not anything built",
    "minecraft:cactus": "a desert plant, not anything built",
    "minecraft:cactus_flower": "what grows on one",
    "minecraft:pumpkin": "a wild patch growing in the grass",
    "minecraft:melon": "a wild patch growing in the grass",
    "minecraft:brown_mushroom": "a small mushroom on the forest floor",
    "minecraft:red_mushroom": "a small mushroom on the forest floor",
    "minecraft:snow":
        "a layer of snow lying on the ground is cover to clear, not ground",
}


def is_vegetation(name: str) -> bool:
    """Whether ``name`` is ground cover to clear from a site: grass, ferns,
    flowers, bushes, vines, roots, leaf litter -- the game's
    ``replaceable_by_trees`` and ``small_flowers`` tags, less the leaves
    (trees, :func:`is_tree`) and the water plants (``_NOT_VEGETATION``),
    plus berry bushes, cane, cactus, wild pumpkins and melons, small
    mushrooms and lying snow (``_VEGETATION_EXTRAS``). Nothing is both this
    and a tree.
    The grass a pre-1.20.3 chunk still calls by its old name is
    vegetation."""
    name = _current(name)
    return ((name in _GROUND_COVER and name not in _NOT_VEGETATION)
            or name in _VEGETATION_EXTRAS)


#: Natural ground: the game's ``dirt``, ``sand``, ``base_stone_overworld``,
#: ``base_stone_nether``, ``substrate_overworld`` and ``terracotta`` tags.
_NATURAL_GROUND = _ids("""
dirt coarse_dirt rooted_dirt sand red_sand suspicious_sand stone granite
diorite andesite deepslate tuff netherrack basalt blackstone grass_block
moss_block pale_moss_block mud muddy_mangrove_roots mycelium podzol
terracotta white_terracotta orange_terracotta magenta_terracotta
light_blue_terracotta yellow_terracotta lime_terracotta pink_terracotta
gray_terracotta light_gray_terracotta cyan_terracotta purple_terracotta
blue_terracotta brown_terracotta green_terracotta red_terracotta
black_terracotta
""")
#: What those tags hold that is not natural ground, each with its reason.
_NOT_GROUND = {
    "minecraft:suspicious_sand":
        "a ruin's buried loot, not ground: a copy would bury the loot again",
}
#: Natural ground the tags do not name, each with its reason.
_GROUND_EXTRAS = {
    "minecraft:gravel":
        "the beach and the riverbed: a column of dirt under it is a scar",
    "minecraft:clay": "the bed of a lake or a river where it meets the shore",
    "minecraft:sandstone": "what a desert's sand lies on",
    "minecraft:red_sandstone": "what the badlands' red sand lies on",
    "minecraft:snow_block":
        "the snowy slopes and the peaks are snow block on snow block, and "
        "dirt under it is a brown seam down a white hillside",
    "minecraft:calcite": "the white bands of the stony peaks",
    "minecraft:dripstone_block": "the floor of a dripstone cave",
    "minecraft:soul_sand": "the floor of a soul sand valley",
    "minecraft:soul_soil": "the floor of a soul sand valley",
    "minecraft:end_stone":
        "the whole of the End's ground, which has no dirt anywhere in it",
}
#: Ground that only lies on top, and what lies under it, each with its
#: reason.
_SKINS = {
    "minecraft:grass_block": ("minecraft:dirt",
                              "grass grows on dirt and dies back to dirt "
                              "under a block"),
    "minecraft:podzol": ("minecraft:dirt", "a forest floor over dirt"),
    "minecraft:mycelium": ("minecraft:dirt",
                           "mycelium spreads over dirt and dies back to it "
                           "under a block"),
}


def subsoil_of(name: str) -> str | None:
    """What fills the ground beneath natural ground of this kind -- the
    block a raised column is built up from -- or None if ``name`` is not
    natural ground: an ore, bedrock, air, a fluid, a ruin's suspicious
    sand, anything built.

    Natural ground is the game's dirt, sand, stone and terracotta tags,
    with gravel, clay, sandstone, snow block, calcite, dripstone, soul
    sand and soil and end stone (``_GROUND_EXTRAS``); each is its own
    subsoil, except grass, podzol and mycelium, which only lie on top of
    dirt (``_SKINS``)."""
    name = _current(name)
    skin = _SKINS.get(name)
    if skin is not None:
        return skin[0]
    if ((name in _NATURAL_GROUND and name not in _NOT_GROUND)
            or name in _GROUND_EXTRAS):
        return name
    return None


def is_natural_ground(name: str) -> bool:
    """Whether ``name`` is ground nobody built: :func:`subsoil_of` has an
    answer for it."""
    return subsoil_of(name) is not None


#: The cut shapes, by whole suffix: exactly the game's ``stairs``,
#: ``slabs`` and ``walls`` tags.
_SHAPES = (("_stairs", "stairs"), ("_slab", "slab"), ("_wall", "wall"))
#: Shapes whose name does not lead to the block they are cut from.
_CUT_FROM = {
    # The stem is the bamboo plant; the slab and stair are its planks'.
    "minecraft:bamboo_slab": "minecraft:bamboo_planks",
    "minecraft:bamboo_stairs": "minecraft:bamboo_planks",
    # A stone slab that looks like oak, left over from the old wooden slab:
    # there is no petrified oak block, and it wears oak planks' face.
    "minecraft:petrified_oak_slab": "minecraft:oak_planks",
}


def material_of(name: str) -> tuple[str, str]:
    """``(base, shape)``: the full block ``name`` is cut from, and the cut
    -- ``"stairs"``, ``"slab"``, ``"wall"``, or ``"block"`` for anything
    that is not a cut shape (which is its own base).

    ``stone_brick_stairs`` is cut from ``stone_bricks``, ``oak_slab`` from
    ``oak_planks`` and ``quartz_stairs`` from ``quartz_block``: the base is
    the first of the stem, its plural, ``<stem>_block`` and
    ``<stem>_planks`` that the game has. A shape whose name does not lead
    to its block is named (``_CUT_FROM``), and one the game does not have
    keeps its stem, so a modded ``marble_stairs`` is cut from ``marble``.
    A retired name is the block it became (:data:`RENAMED`), so a chain
    read out of an old chunk is the same material as one in a new chunk.
    """
    name = _current(name)
    for suffix, shape in _SHAPES:
        if name.endswith(suffix):
            break
    else:
        return name, "block"
    cut = _CUT_FROM.get(name)
    if cut is not None:
        return cut, shape
    return _stem_base(name[:-len(suffix)]), shape


def _stem_base(stem: str) -> str:
    """The block a form's stem leads to: the first of the stem, its plural,
    ``<stem>_block`` and ``<stem>_planks`` the game has, or the stem."""
    for base in (stem, f"{stem}s", f"{stem}_block", f"{stem}_planks"):
        if base in BLOCK_IDS:
            return base
    return stem


def cut_shapes() -> dict[str, str]:
    """Every stair, slab and wall the game has -> the full block it is cut
    from, by :func:`material_of`, in sorted id order. A fresh dict.

    The game's own cuts and no others: a modded ``marble_stairs`` is not
    here, because the registry has never heard of it and its stem is only
    a guess. This is what :class:`~heaton_voxel.craft.render.ColorMap`
    draws a cut in the color of, and what the Unity app's block palette
    generator bakes into the app's palette, so the two read one table."""
    return dict(_cut_shapes())


@lru_cache(maxsize=None)
def _cut_shapes() -> tuple[tuple[str, str], ...]:
    found = []
    for name in _ordered_ids():
        base, shape = material_of(name)
        if shape != "block":
            found.append((name, base))
    return tuple(found)


#: The forms a family of blocks comes in, as a builder names them: the
#: block itself, its three cut shapes, and a pane. The cuts are exactly the
#: game's ``stairs``, ``slabs`` and ``walls`` tags (:func:`material_of`);
#: no tag lists the panes, which are the ids ending ``_pane``.
FORMS = ("block", "stairs", "slab", "wall", "pane")


def form_of(name: str) -> tuple[str, str]:
    """``(base, form)``: the block ``name`` is a form of -- the full block
    a cut or a pane is made from, or ``name`` itself -- and which of
    :data:`FORMS` it is.

    A stair, slab or wall is :func:`material_of`'s answer. A pane, by its
    whole ``_pane`` suffix, is a form of the block its stem leads to by
    the same rule -- ``glass_pane`` of ``glass``,
    ``red_stained_glass_pane`` of ``red_stained_glass``. Anything else is
    ``(name, "block")``, its own base, and a retired name is the block it
    became, as in every category here.
    """
    base, shape = material_of(name)
    if shape != "block":
        return base, shape
    if base.endswith("_pane"):
        return _stem_base(base[:-len("_pane")]), "pane"
    return base, "block"


@lru_cache(maxsize=None)
def _forms() -> dict[str, dict[str, str]]:
    """Each full block's forms the game has, past the block itself: base ->
    {form: id}, in :data:`FORMS` order. The cuts are :func:`_cuts`', so a
    form whose own name leads to the block wins there as it does there."""
    found: dict[str, dict[str, str]] = {}
    for base, shapes in _cuts().items():
        found[base] = {form: shapes[form] for form in FORMS if form in shapes}
    for name in _ordered_ids():
        base, form = form_of(name)
        if form == "pane":
            found.setdefault(base, {}).setdefault("pane", name)
    return found
