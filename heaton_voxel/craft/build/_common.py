"""What every family of construction shares.

The compass tables (each derived from the one compass,
:data:`~heaton_voxel.craft.geometry.COMPASS`, in the order it has always
had), ``HEAD_ROOM``, what counts as an empty cell (:func:`_empty`),
whether a chunk is finished terrain (:func:`_finished`,
the column reader's own test from :mod:`~heaton_voxel.craft.survey`), the
mix a planned surface is drawn from (:class:`_Mix`), the material
argument a palette-reading writer resolves (:class:`_Slot`), what no
seal walling water off may be (:func:`_seal_gives_way`), what a falling
block falls through (:func:`_falls_through`, the ground writers' one
void-under test, on the survival audit's own list), what an earthwork
writer takes without being told to overwrite it (:func:`_takes`), the
fluids a plan holding water never writes (:func:`_fluid_unlaid`: lava,
and water flowing) and the world cells it may spill into (:func:`_spill`),
and the few small helpers more than one family reads --
among them the refusals the stair and vault writers share: where a
continuation starts (:func:`_continuation`), what already stands in the
cells a rule needs (:func:`_occupancy`), and whether there is head room
over a walkway (:func:`_head_room`). Each writer takes the ones its rule
has; a vault has no walkway, a flight nothing to continue.

Part of :mod:`heaton_voxel.craft.build`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import NamedTuple

from ...blocks import AIR_BLOCKS, BlockState
from ...connect import _derived_state
from ...nbt import NBTError
from .._plan import refuse_many
from ..block_facts import FULL_FACES, facts, holds_fluid, holds_water, known
from ..block_registry import FLUIDS
from ..blockinfo import (_current, falls, is_natural_ground, is_tree,
                         is_vegetation)
from ..geometry import COMPASS, axis_of, heading_of
# _finished is the column reader's own gate, kept in survey so the reader
# can ask it without importing the builders; it is one predicate.
from ..survey import (_falls_into, _finished,  # noqa: F401
                      _FREEZE_ICE, step_kind)


AIR = "minecraft:air"


#: What a light melts off the floor above it. Snow is the whole point;
#: ice is here because a lit plaza should not stay frozen either.
_MELTABLE = frozenset((
    "minecraft:snow", "minecraft:powder_snow", "minecraft:ice",
    "minecraft:frosted_ice",
))


#: The four cells a winding stair turns through, as offsets from its newel,
#: in the order its refusals name them: east first, then clockwise.
_RING = {word: COMPASS[word] for word in ("east", "south", "west", "north")}


#: Which axis each compass word runs along, and its sign on that axis. The
#: game's own convention: +z is south and +x is east.
_HEADINGS = {word: (axis_of(word), dx + dz) for word, (dx, dz) in COMPASS.items()}


#: The low and the high end of a run along each axis, by name: the way it
#: heads toward its lower and its higher coordinate -- where a dogleg's
#: flight runs, and the way an arch's springer at each end faces, toward
#: its jamb.
_ENDS = {"z": (heading_of(0, -1), heading_of(0, 1)),
         "x": (heading_of(-1, 0), heading_of(1, 0))}


def _is_whole(value) -> bool:
    """Whether ``value`` is a whole number: an int, and a bool is not one."""
    return isinstance(value, int) and not isinstance(value, bool)


def _whole(value, what: str) -> int:
    """``value`` if it is a whole number (a bool is not), else an NBTError
    naming ``what``: the check every rule object's counts and heights
    pass, in one wording."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise NBTError(f"{what} must be a whole number, got {value!r}")
    return value


@lru_cache(maxsize=None)
def _derived(name: str) -> frozenset:
    """The properties of block ``name`` that a block update derives from
    its neighbors -- a fence's, pane's or wall's sides and a wall's post,
    a gate's ``in_wall``, a stair's ``shape`` -- as the lower tier's own
    :func:`~heaton_voxel.connect.connect_blocks` rewrites them, so the
    two never disagree about which they are; none for any other block."""
    derived = _derived_state(BlockState(name), lambda x, y, z: None, 0, 0, 0)
    return frozenset() if derived is None else frozenset(derived.properties)


#: A door's state that says how it stands in play, not what was hung.
_DOOR_PLAY = frozenset(("open", "powered"))


def _same_crown(found: BlockState, expect: BlockState) -> bool:
    """Whether a cell already holds what a writer would write there: the
    same game state (a property either side leaves out is its block's
    default, :meth:`~heaton_voxel.blocks.BlockState.canonical`), apart
    from the properties a block update derives (:func:`_derived`). A fence
    or wall course that ``finish`` has joined up is the course that was
    laid, and laying it again must find it so; a stair whose corner shape
    a neighbor turned is the stair that was laid; and a door somebody has
    opened is the door that was hung, whatever its ``open`` and ``powered``
    say now. The one such test every writer and reader of a rule asks -- a
    battlement's, a wall's, a shell's, a piece of furniture's, a roof's and
    a vault's."""
    if found.name != expect.name:
        return False
    skip = _derived(found.name)
    if found.name.endswith("_door"):
        skip = skip | _DOOR_PLAY
    a = {k: v for k, v in found.canonical().properties.items() if k not in skip}
    b = {k: v for k, v in expect.canonical().properties.items() if k not in skip}
    return a == b


def _state_matches(found, want) -> bool:
    """Whether ``found`` satisfies the state a rule asks for.

    The name must match, and so must **every property the rule names** --
    a stair that agrees everywhere but ``shape`` is a different entry in
    the section palette and worth hearing about. Properties the rule does
    *not* name are free, which is how a rule says that something is not
    part of the pattern: the treads of a flight carry a ``facing`` the
    rule sets, but the ``axis`` of the log under them is the aim of
    whoever placed it, and a hand-built stair has logs lying every which
    way. A rule read off the world names every property it found, so this
    is exact wherever the rule came from the blocks themselves.
    """
    return (found.name == want.name
            and all(found.properties.get(key) == value
                    for key, value in want.properties.items()))


#: Levels of a stair below a cell whose walkers have their head in it.
#: Three, not the two a standing body needs: the game resolves horizontal
#: movement before gravity, so for one tick as you step off a tread your
#: feet are still at its height while your box already overlaps the next
#: cell down the run. Clearance for two is a stair you can climb and
#: cannot descend -- see :meth:`SwitchbackStair.claimed`.
HEAD_ROOM = 3


#: What a stair carries besides ``facing``. Filled in unless the caller
#: names them, so a continued run lands on the palette entry the existing
#: stair already uses instead of adding a near-identical twin beside it --
#: two entries for one block is how identical builds stop encoding
#: identically.
_STAIR_STATE = {"half": "bottom", "shape": "straight", "waterlogged": "false"}


def _state(block) -> BlockState:
    if isinstance(block, BlockState):
        return block
    refuse_many(block)
    return BlockState.parse(block)


#: Blocks whole on every face to water, and with nothing in them water can
#: enter, that do not stay a wall, so are no seal for
#: :meth:`~heaton_voxel.craft.build.Builder._contain_plan` or a
#: :meth:`~heaton_voxel.craft.build.Builder.railway`: each with what
#: becomes of it (javap, 26.3). A block that falls
#: (:func:`~heaton_voxel.craft.blockinfo.falls`: sand, gravel, concrete
#: powder) is no seal either, since a seal stands where water stood, over
#: whatever that water stood on (:func:`_seal_gives_way`).
_SEAL_GIVES_WAY = {
    "minecraft:ice": "ice melts back into water at block light 11 and over "
                     "(IceBlock.randomTick)",
    "minecraft:frosted_ice": "frosted ice melts back into water in daylight "
                             "or lamplight (FrostedIceBlock.tick)",
    "minecraft:sponge": "a sponge soaks up the water round it the first time "
                        "a neighbor updates (SpongeBlock.neighborChanged)",
}


def _walls_off_water(state: BlockState) -> bool:
    """Whether ``state`` stands whole against water: whole on every face
    water's wall test sees (``block_facts``' ``collision``) and nothing
    water can enter -- a container, a fluid, or a block water washes away.
    A state holding fluid is one water enters, so every wet state is
    refused: of the game's states none whole on every face that water
    cannot enter holds any. A block the game has never heard of, a modded
    one passed by ``allow=``, the tables answer as a cube, so it is taken
    at its word about its water: a wall unless it says it is waterlogged,
    as a railway's water check takes it. Half of the one seal rule
    (:meth:`~heaton_voxel.craft.build.Builder._seal_state`), and the whole
    of a basin's rim test, which leaves what falls to the survival audit
    where it stands."""
    info = facts(state.name, state.properties)
    return not (info.collision != FULL_FACES or info.enters
                or (not known(state.name)
                    and state.properties.get("waterlogged") == "true"))


def _seal_gives_way(state: BlockState) -> str | None:
    """Why a seal of ``state`` -- a block walling water off where it
    stands -- would not stay a wall, in words: it melts or soaks the water
    up (:data:`_SEAL_GIVES_WAY`), or it falls wherever nothing holds it up
    (:func:`~heaton_voxel.craft.blockinfo.falls`). None for one that stays.
    The one rule every writer's seal is held to, ``_contain_plan``'s and a
    railway's alike."""
    gives = _SEAL_GIVES_WAY.get(state.name)
    if gives is None and falls(state.name):
        gives = (f"{state.name} falls wherever nothing holds it up, and a "
                 f"seal stands where water stood")
    return gives


def _falls_through(state) -> bool:
    """Whether a falling block falls through ``state``: the cell holds
    nothing up, as the game's ``FallingBlock.isFree`` answers it. That is
    :func:`~heaton_voxel.craft.survey._falls_into`, the very test the
    survival audit's falls check asks: ``survey._FALLS_THROUGH``, a copy of
    the game's ``replaceable`` tag -- every air, water, lava and a bubble
    column, fire, a snow layer, grass and ferns, a vine, lichen, roots,
    leaf litter, a light block -- with an old name read as the block it
    became (``RENAMED``) and an id the game does not have as air, as the
    game loads it. Kelp and a waterlogged block hold water and hold a
    falling block up all the same. None -- past the world's floor, where
    the game answers void air -- holds nothing up either; the audit, which
    reads None for a chunk it cannot read as well, faults nothing there.

    The ground writers' one void-under test: :meth:`Builder.basin` lines
    the cell under a floor that answers yes, a lid over a hole -- "enough
    solid to hold a gravity block up; that is *one* block", as the lake's
    builder put it -- and the cell under it where that is sand or gravel
    standing on such a hole. :meth:`Builder.grade` lays its surface on its
    own fill or on the ground it cut down to, and leaves sand over a hole
    it opens to the survival audit (``Builder._audit_plan``), which asks
    the same list."""
    return state is None or _falls_into(state)


#: What lies on open water and goes with it: the blocks the game places
#: on water (``PlaceOnWaterBlockItem``), which break once nothing under
#: them is water or ice.
_ON_THE_WATER = frozenset(("minecraft:lily_pad", "minecraft:frogspawn"))


def _takes(state, *, wet: bool = False) -> bool:
    """Whether an earthwork writer takes ``state`` -- cuts it away, or
    writes over it -- without being told to overwrite it: what nobody
    built. Any of the game's airs, ground cover
    (:func:`~heaton_voxel.craft.blockinfo.is_vegetation`, a snow layer
    among it), natural ground
    (:func:`~heaton_voxel.craft.blockinfo.is_natural_ground`), an ore (by
    whole suffix, ``_ore``) and a tree's parts
    (:func:`~heaton_voxel.craft.blockinfo.is_tree`). With ``wet``, for a
    writer that has been told what to do with water, or whose work is
    water, also the fluids themselves (``FLUIDS``: water, lava, kelp,
    seagrass, a bubble column), freeze ice (``ice`` and frosted ice, which
    the game freezes water into) and what floats on the water
    (``_ON_THE_WATER``). Everything else is somebody's -- a wall, a torch,
    a rail, a waterlogged stair, a sea pickle somebody set as a lamp, a
    modded block -- and a writer refuses it unless ``overwrite=True``: a
    wrong "built" is a false alarm somebody reads, and a wrong "natural"
    is a wall nobody sees go. The one occupancy rule of the ground
    writers: :meth:`Builder.basin`; :meth:`Builder.approach`, which asks
    for ``wet`` only in the columns whose ground is water or an ice sheet
    that its ``over_water`` bridges or fills, and refuses lava before it
    asks; :meth:`Builder.extrude`, which asks for ``wet`` in the columns
    its ``over_water`` speaks for, the same way; and
    :meth:`Builder.grade`, :meth:`Builder.level_pad` and
    :meth:`Builder.road`, through :meth:`Builder._refuse_built`, which
    always ask for ``wet`` and leave water to the water check."""
    name = _current(state.name)
    if (name in AIR_BLOCKS or is_vegetation(name) or is_natural_ground(name)
            or is_tree(name) or name.endswith("_ore")):
        return True
    return wet and (name in FLUIDS or name in _FREEZE_ICE
                    or name in _ON_THE_WATER)


def _fluid_unlaid(state) -> str | None:
    """Why a writer whose plan may hold water -- an extrusion's channel
    (:class:`~heaton_voxel.craft.build.CrossSection`), a piece of
    furniture's tub or spout (:class:`~heaton_voxel.craft.build.Piece`) --
    does not write ``state``, in words, or None where it may.

    Two fluids are refused. **Lava**, which no water check follows: the
    one those writers run (:func:`~heaton_voxel.craft.survey.contained`)
    follows water, and names lava only where the two touch. And **flowing
    water**, ``minecraft:water`` at a level from 1 to 15: the game re-reads
    a flow from the water that feeds it the first time anything updates
    beside it (``getNewLiquid``), and never a source, so a flow written by
    hand is one the game takes away, or makes again from its sources --
    write the sources, and let the game make the flow. A water source, a
    waterlogged block, and kelp, seagrass and a bubble column, water at
    every state, are water a plan holds, and the water check asks where
    each goes. An old name is read as the block it became."""
    name = _current(state.name)
    if (holds_fluid(name, state.properties)
            and not holds_water(name, state.properties)):
        return (f"{state} is lava, and the water check follows water alone: "
                f"nothing here holds lava in")
    if name == "minecraft:water":
        level = state.properties.get("level", "0")
        if level != "0" and level.isdigit() and 0 < int(level) <= 15:
            return (f"{state} is flowing water, which the game re-reads from "
                    f"the sources feeding it the first time anything updates "
                    f"beside it: write the sources (minecraft:water[level=0]) "
                    f"and let the game make the flow")
    return None


def _spill(spill, what: str, *, cells: str = "the world cells (x, y, z)"
           ) -> tuple:
    """``spill`` -- the cells a plan's water may run into, as
    :func:`~heaton_voxel.craft.survey.contained` takes them: a fountain's
    basin, the fall from an aqueduct's open end into the pool under it --
    as a sorted tuple of ``(x, y, z)``, checked before anything is read.
    Each cell is three whole numbers; a bool (a yes or a no names no
    cell), a string, or anything else that is no sequence of cells, is an
    ``NBTError`` naming ``what``, and ``cells`` what they are -- world
    cells, or a :class:`~heaton_voxel.craft.build.Piece`'s own. Empty, it
    is none: every way the water gets out is a leak."""
    if spill is None or isinstance(spill, (bool, str, bytes)):
        raise NBTError(f"{what} is {cells} the water may run into, not "
                       f"{spill!r}")
    try:
        given = list(spill)
    except TypeError:
        raise NBTError(f"{what} is {cells} the water may run into, not "
                       f"{spill!r}") from None
    cells = set()
    for cell in given:
        try:
            x, y, z = cell
        except (TypeError, ValueError):
            raise NBTError(f"a cell of {what} is three whole numbers, got "
                           f"{cell!r}") from None
        for part in (x, y, z):
            if isinstance(part, bool) or not isinstance(part, int):
                raise NBTError(f"a cell of {what} is three whole numbers, "
                               f"got {cell!r}")
        cells.add((x, y, z))
    return tuple(sorted(cells))


class _Mix:
    """A block still to be drawn from a weighted ``[(block, weight), ...]``
    mix, standing in a plan for the one a road's column will be surfaced
    with. :meth:`Builder._commit` draws it from the Builder's ``rng`` as it
    writes the op, so the draws come in the order the writes do, as they
    did when each column was drawn and written in turn -- and a plan that
    is refused draws nothing. That is a list's way, kept so that every
    build made with one encodes as it always has; a
    :class:`~heaton_voxel.craft.style.Mix` stands in a plan as itself, and
    is picked at each cell instead."""

    __slots__ = ("mix",)

    def __init__(self, mix):
        self.mix = mix

    def __repr__(self) -> str:
        return f"<_Mix {self.mix!r}>"


class _Slot(NamedTuple):
    """One material argument of a palette-reading writer, as it hands it
    to :meth:`~heaton_voxel.craft.build.Builder._materials`: the argument's
    name, what the caller gave (``FROM_PALETTE`` when left out), the
    :data:`~heaton_voxel.craft.style.ROLES` role it defaults to, the form
    of a :class:`~heaton_voxel.craft.style.Material` the writer writes
    (``"block"``, ``"stairs"``, ``"slab"`` or ``"pane"``), whether a
    :class:`~heaton_voxel.craft.style.Mix` may stand there -- only where
    the block is written as it is, never where the writer spells a facing,
    a half or a connection onto it or reads its light off it -- and
    whether ``None`` may, meaning none (a floor's trim and its light)."""

    arg: str
    given: object
    role: str
    form: str = "block"
    mixes: bool = False
    none: bool = False


#: :data:`~heaton_voxel.craft.geometry.COMPASS`, under the name the
#: builders have always read it by.
_COMPASS = COMPASS


# --------------------------------------------------------------------------
# What the stair and vault writers refuse, and how
# --------------------------------------------------------------------------

def _empty(state) -> bool:
    """Whether a cell holding ``state`` holds nothing: any of the game's
    airs, :data:`~heaton_voxel.blocks.AIR_BLOCKS` -- its own ``air`` tag.

    Cave air fills every cave cut before 1.18, when the carvers still
    wrote it, and every dungeon, mineshaft and stronghold; void air
    is what the game answers past the world's edge. Its ``isAir`` is true
    of all three, and that is what its block rules ask, so the builders
    ask it too, on **both** sides of a rule at once: a writer builds
    into a cell this calls empty without being told to cut through it, and
    a reader takes it for nothing -- not an extra in a stair's gaps, not a
    second tread material, not a ceiling a chain hangs from. A writer that
    built into cave air beside a reader that counted it as furniture would
    lay stairs nobody could continue. Writing is the other way round: a
    cell a builder clears gets plain :data:`AIR`.
    """
    return state.name in AIR_BLOCKS


def _passable(state) -> bool:
    """Whether a body passes through ``state``, as the walk judges it: any
    air, and anything :func:`~heaton_voxel.craft.survey.step_kind` walks
    through -- a torch or a sign on the wall is no obstruction."""
    return _empty(state) or step_kind(state.name, state.properties) == "air"


def _continuation(rule, end, *, first: str = "y0", last: str = "y1",
                  noun: str = "stair", unit: str = "level",
                  ends: str = "levels") -> int:
    """Where a continuation of ``rule`` starts: one past whichever end of
    the run it was read from ``end`` is heading for.

    A level the run left unfinished -- its stair placed, its treads not --
    is where the work actually stopped, so the start backs over the
    rule's ``unfinished`` levels at that end rather than stepping past
    them and leaving the join with a hole in it. A rule that keeps no such
    record (a :class:`~heaton_voxel.craft.build.Vault`) starts one past
    the end.

    Raises :class:`NBTError` -- a bad argument, not a refusal -- when the
    rule was made from scratch and has nothing to continue, or when ``end``
    lies inside the run it already covers, which leaves which end was meant
    for the caller to say. ``first``, ``last``, ``noun``, ``unit`` and
    ``ends`` are the writer's own words for its arguments and its run.
    """
    if rule.found is None:
        raise NBTError(
            f"{first}=None continues a {noun} read out of the world, but "
            f"this {type(rule).__name__} was made from scratch and has "
            f"nothing to continue -- name the first {unit}")
    low, high = rule.found
    if low <= int(end) <= high:
        raise NBTError(
            f"{first}=None continues past one end of the {noun}, but "
            f"{last}={end} is inside the run it already covers "
            f"({low}..{high}) -- name both {ends} to rebuild part of it")
    unfinished = set(getattr(rule, "unfinished", ()))
    if int(end) > high:
        start = high + 1
        while start - 1 in unfinished:
            start -= 1
    else:
        start = low - 1
        while start + 1 in unfinished:
            start += 1
    return start


def _occupancy(read, cells, *, same=_state_matches, empty=_empty,
               overwrite: bool = False, cut=None, noun: str | None = None):
    """What stands in the cells a rule needs, before any of it is written.

    ``cells`` is ``(cell, want)`` pairs, scanned in the order given: the
    writer's own write order, so the first fault named is the first cell
    the write would have reached. ``read`` returns the block at a cell and
    refuses where there is nothing to build into -- the Builder's
    :meth:`~heaton_voxel.craft.build.Builder._read`, whose writers check
    their plan's reach before they read, so that refusal is a backstop.

    Returns ``(already, blocked, into)``: the cells ``same`` says already
    hold what the rule wants; a fault for each cell holding anything else
    that is not ``empty``, not forgiven by ``overwrite``, and not one
    ``cut(cell)`` says the writer cuts anyway; and the rest -- the cells
    the write fills -- with the block each holds now, in scan order. Every
    cell lands in exactly one of the three, so ``into`` is what would be
    written even when ``blocked`` refuses it all. A fault reads
    ``"<cell> holds <found>"``, and goes on to say what the ``noun`` needs
    there when one is named.
    """
    already: set[tuple[int, int, int]] = set()
    blocked: list[str] = []
    into: dict[tuple[int, int, int], BlockState] = {}
    for cell, want in cells:
        found = read(cell)
        if same(found, want):
            already.add(cell)
        elif empty(found) or overwrite or (cut is not None and cut(cell)):
            into[cell] = found
        else:
            fault = f"{cell} holds {found}"
            if noun is not None:
                fault += f", where the {noun} needs {want}"
            blocked.append(fault)
    return already, blocked, into


def _head_room(peek, rule, levels, skip=(), *, passable,
               need: int = HEAD_ROOM, own: bool = False,
               say=None) -> list[str]:
    """Every block within ``need`` above a walkway cell that a body cannot
    pass, level by level in the order of ``levels``.

    ``rule.walkway(y)`` names the columns stood on at each level. ``peek``
    returns the block at a cell, or None past the top of the world, which
    is no obstruction -- the walkway under it was just read, so None
    there can only be the sky. Cells in ``skip`` (the rule's own planned
    cells, a stairwell being cut) are not looked at.

    ``own=True`` also passes a cell already holding **the rule's own
    block** for that level -- ``rule.level(above)``, wherever the rule
    has that level -- for a stair whose geometry puts one of its own
    blocks in the head room of a cell nobody comes down into. That is a
    winding stair's step: its third cell up is the tread the stair turns
    onto three levels higher, and the step is entered level from the
    tread beside it, never from above. Without it a stair already
    standing above the levels being written -- a downward continuation,
    or the whole of one read back -- would fault on itself at every
    level.

    ``say(step, found, cell)`` words each fault; by default it is
    ``"the step at <(x, y, z)> has <found> at <cell>"``, for a writer
    whose summary carries the reason.
    """
    faults: list[str] = []
    for y in levels:
        for x, z in rule.walkway(y):
            for above in range(y + 1, y + 1 + need):
                cell = (x, above, z)
                if cell in skip:
                    continue
                found = peek(cell)
                if found is None or passable(found):
                    continue
                if own and rule._within(above):
                    want = rule.level(above).get((x, z))
                    if want is not None and _state_matches(found, want):
                        continue
                faults.append(say((x, y, z), found, cell) if say is not None
                              else f"the step at {(x, y, z)} has {found} "
                                   f"at {cell}")
    return faults
