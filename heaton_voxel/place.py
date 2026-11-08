"""Placing blocks the way the game places items: :func:`place_block`.

:meth:`~heaton_voxel.world.MinecraftSave.set_block` writes exactly the state
it is given, which is the right primitive for a format library -- and the
wrong tool for building, because vanilla *items* place more than one block
and derive state from the surroundings. A bed is two blocks; a door is two
blocks; writing one half leaves an orphan the game breaks on its next
update. A fence written bare stands disconnected until something nudges it.
A lantern dropped into a water source should be waterlogged.

``place_block`` is the building-level setter that handles all of that in one
call, so higher-level code never deals with halves or connections:

    save.place_block(x, y, z, "minecraft:red_bed[facing=south]")   # both halves
    save.place_block(x, y, z, "minecraft:oak_door[facing=west]")   # both halves
    save.place_block(x, y, z, "minecraft:oak_fence")               # connected,
                                                                   # neighbors updated

What it does beyond a raw write:

* **Completes multi-block things.** Beds get their second half laid toward
  ``facing`` (the given cell is the foot unless the state says
  ``part=head``); doors and tall plants (sunflower, lilac, rose bush, peony,
  tall grass, large fern, pitcher plant, small dripleaf, tall seagrass) get
  their upper half placed above. Both cells are
  overwritten -- this is an editor, not survival mode -- and if either
  cell's chunk is missing the whole placement is refused, never half done.
* **Settles neighbor-derived state**, using the same validated rules as
  :func:`heaton_voxel.connect.connect_blocks`: the placed blocks *and* their
  orthogonal neighbors (and the block below, whose wall post may change)
  are re-derived, exactly as a player placement's block updates would.
* **Waterlogs into a water source.** When the placed block carries
  ``waterlogged`` and the caller left it unsaid, it takes the water it
  displaces by the game's rule for it (:data:`_TAKES_WATER`): nearly every
  such block waterlogs only where the cell holds a water *source* --
  ``water[level=0]`` (an omitted level is that default), seagrass, tall
  seagrass, kelp, kelp_plant, a bubble column, or a block that is itself
  waterlogged -- and never into flowing or falling water, whose levels 1-15
  are another fluid. Corals and the conduit also take falling water, the
  small dripleaf takes any water, and the light block and a double slab
  none. A campfire that takes water is placed out (``lit=false``) unless
  the caller said ``lit``. Where the answer is dry and the block is wet by
  default (a sea pickle, a coral, a conduit), ``waterlogged=false`` is
  written, as the game writes it. An explicit ``waterlogged`` is
  respected. Blocks that cannot hold water simply displace it, as in the
  game.

Not simulated: survival placement *rules*. Nothing checks support or
reachability -- a door placed on air is written faithfully, like every other
editor write in this toolkit.
"""

from __future__ import annotations

from .block_defaults import BLOCK_DEFAULTS
from .blocks import BlockState
from .connect import _derived_state
from .nbt import NBTError

__all__ = ["place_block"]

_VECTOR = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}

# Two-block plants: lower half at the placed cell, upper half above. Every
# DoublePlantBlock whose setPlacedBy sets the upper half (26.3, javap): the
# small dripleaf's carries the lower half's facing, and takes its own water
# by copyWaterloggedFrom, which is the cell's own rule here. The pitcher
# crop's setPlacedBy sets nothing -- the pod plants one block, and the crop
# grows its upper half -- so it is not one of them.
_TALL_PLANTS = {
    "minecraft:sunflower", "minecraft:lilac", "minecraft:rose_bush",
    "minecraft:peony", "minecraft:tall_grass", "minecraft:large_fern",
    "minecraft:pitcher_plant", "minecraft:small_dripleaf",
    "minecraft:tall_seagrass",
}

def _waterloggable(name: str) -> bool:
    """Whether the block carries a ``waterlogged`` property at all.

    Read from the game's own block report (:mod:`heaton_voxel.block_defaults`),
    not from a list of families: the hand-kept list this replaced missed 98
    blocks -- every chain, copper lantern, chest and lightning rod -- and
    named the bell, which has no such property, so a bell dropped into water
    was written in a state the game does not have. A block the table has
    never heard of displaces the water, the harmless direction to be wrong
    in.
    """
    return "waterlogged" in BLOCK_DEFAULTS.get(name, ())


# The water a cell holds, as the game's ``getFluidState`` reads it (26.3,
# javap). ``LiquidBlock`` maps ``level`` 0 to the source, 1-7 to flowing
# water of amount ``8 - level``, and every level from 8 up to falling water
# of amount 8. Flowing and falling water are both ``Fluids.FLOWING_WATER``,
# another Fluid from the source ``Fluids.WATER``.
_FLOWING_LEVELS = frozenset(str(level) for level in range(1, 8))
_FALLING_LEVELS = frozenset(str(level) for level in range(8, 16))

#: Blocks the game reads as a water source whatever their state: each one's
#: ``getFluidState`` returns ``Fluids.WATER.getSource(false)`` outright.
#: Every other block holds water only when it is waterlogged.
_WATER_SOURCE_BLOCKS = frozenset({
    "minecraft:bubble_column", "minecraft:kelp", "minecraft:kelp_plant",
    "minecraft:seagrass", "minecraft:tall_seagrass",
})

_SOURCE_ONLY = frozenset({"source"})

#: The corals that carry ``waterlogged``: plants, fans and wall fans, live
#: and dead (``BaseCoralPlantTypeBlock``). The coral *blocks* carry none.
_CORALS = frozenset(
    f"minecraft:{dead}{color}_coral{form}"
    for dead in ("", "dead_")
    for color in ("tube", "brain", "bubble", "fire", "horn")
    for form in ("", "_fan", "_wall_fan"))

#: Which water a placed block takes, where the game's rule is not the usual
#: one. Nearly every waterloggable block's ``getStateForPlacement`` sets
#: ``waterlogged`` to ``fluidState.is(Fluids.WATER)`` (or
#: ``isSourceOfType``, the same test): the source alone, which is
#: :data:`_SOURCE_ONLY` and the default here. The 26.3 jar's exceptions:
#:
#: * corals and the conduit test ``is(FluidTags.WATER) && isFull()``, an
#:   amount of 8, which falling water has as well as a source;
#: * the small dripleaf copies ``isWaterAt`` (``DoublePlantBlock.
#:   copyWaterloggedFrom``), which is any water, flowing included;
#: * the light block has no ``getStateForPlacement`` of its own, so it is
#:   placed in its default state, which is dry, wherever it goes.
#:
#: A slab of ``type=double`` takes none either, whatever its name: it is a
#: whole block, and ``SlabBlock`` makes one with ``waterlogged`` false
#: without asking the water (:func:`_takes`).
_TAKES_WATER: dict[str, frozenset[str]] = {
    **dict.fromkeys(sorted(_CORALS | {"minecraft:conduit"}),
                    frozenset({"source", "falling"})),
    "minecraft:small_dripleaf": frozenset({"source", "flowing", "falling"}),
    "minecraft:light": frozenset(),
}

#: The campfires (``CampfireBlock``), whose placement sets ``lit`` to the
#: opposite of the ``waterlogged`` it sets: one placed into water is out.
_CAMPFIRES = frozenset({"minecraft:campfire", "minecraft:soul_campfire"})


def _takes(state: BlockState) -> frozenset[str]:
    """The water ``state``'s placement takes (:data:`_TAKES_WATER`): none
    for a double slab, the only blocks whose ``type`` is ``double``."""
    if state.properties.get("type") == "double":
        return frozenset()
    return _TAKES_WATER.get(state.name, _SOURCE_ONLY)


def _water_in(state: BlockState, cell: tuple[int, int, int]) -> str | None:
    """The water ``state`` holds, as the game reads its fluid: ``"source"``,
    ``"flowing"``, ``"falling"``, or None for none. An omitted property is
    its default, so a bare ``minecraft:water`` (as a legacy chunk may store
    one) is the source and a bare sea pickle is waterlogged. A level or a
    ``waterlogged`` the game does not have is refused with an
    :class:`NBTError` naming the cell, never read as dry."""
    name = state.name
    defaults = BLOCK_DEFAULTS.get(name, {})
    if name == "minecraft:water":
        level = state.properties.get("level", defaults.get("level"))
        if level == "0":
            return "source"
        if level in _FLOWING_LEVELS:
            return "flowing"
        if level in _FALLING_LEVELS:
            return "falling"
        raise NBTError(f"water at {cell} has level={level}, which the game "
                       f"does not have (0-15)")
    if name in _WATER_SOURCE_BLOCKS:
        return "source"
    waterlogged = state.properties.get("waterlogged", defaults.get("waterlogged"))
    if waterlogged == "true":
        return "source"
    if waterlogged is None or waterlogged == "false":
        return None
    raise NBTError(f"{name} at {cell} has waterlogged={waterlogged}, which "
                   f"is neither true nor false")


def _placed_wet(state: BlockState, displaced: BlockState | None,
                cell: tuple[int, int, int]) -> BlockState:
    """``state``, a waterloggable block whose ``waterlogged`` the caller
    left unsaid, as the game places it over ``displaced``: waterlogged when
    the water there is one its rule takes -- and a campfire put out, unless
    the caller said ``lit`` -- written dry when it is not and the block is
    wet by default, and otherwise as given. A block whose rule takes no
    water never reads ``displaced``."""
    takes = _takes(state)
    water = None if displaced is None or not takes else _water_in(displaced, cell)
    if water in takes:
        properties = dict(state.properties, waterlogged="true")
        if state.name in _CAMPFIRES:
            properties.setdefault("lit", "false")
        return BlockState(state.name, properties)
    if BLOCK_DEFAULTS[state.name]["waterlogged"] == "true":
        return BlockState(state.name, dict(state.properties, waterlogged="false"))
    return state


def _with_water(cells, block: BlockState, block_at) -> list:
    """A placement's ``(cell, state)`` pairs (:func:`_cells_for`) with the
    water each takes: when ``block``, the state placed, leaves
    ``waterlogged`` unsaid, every state that can hold water put through
    :func:`_placed_wet` over what ``block_at(x, y, z)`` holds there (None
    for nothing to read). Every cell is read before the pairs are
    returned, so water the game does not have raises its
    :class:`NBTError` before anything is written."""
    if "waterlogged" in block.properties:
        return list(cells)
    return [(cell, _placed_wet(state, block_at(*cell), cell)
             if _waterloggable(state.name) else state)
            for cell, state in cells]


def _cells_for(state: BlockState, x: int, y: int, z: int):
    """The full set of ``(position, state)`` a placement writes."""
    name = state.name
    props = state.properties

    if name.endswith("_bed"):
        facing = props.get("facing", "north")
        dx, dz = _VECTOR[facing]
        if props.get("part") == "head":
            head, foot = (x, y, z), (x - dx, y, z - dz)
        else:
            foot, head = (x, y, z), (x + dx, y, z + dz)
        base = {key: value for key, value in props.items() if key != "part"}
        base.setdefault("facing", facing)
        return [
            (foot, BlockState(name, dict(base, part="foot"))),
            (head, BlockState(name, dict(base, part="head"))),
        ]

    if name.endswith("_door"):
        base = {key: value for key, value in props.items() if key != "half"}
        return [
            ((x, y, z), BlockState(name, dict(base, half="lower"))),
            ((x, y + 1, z), BlockState(name, dict(base, half="upper"))),
        ]

    if name in _TALL_PLANTS:
        base = {key: value for key, value in props.items() if key != "half"}
        return [
            ((x, y, z), BlockState(name, dict(base, half="lower"))),
            ((x, y + 1, z), BlockState(name, dict(base, half="upper"))),
        ]

    return [((x, y, z), state)]


def place_block(
    save,
    x: int,
    y: int,
    z: int,
    block: BlockState | str,
    *,
    dimension: str | None = None,
) -> bool:
    """Place a block with the game's placement semantics. See the module
    docstring for exactly what that adds over ``set_block``.

    Returns ``True`` when everything was written. ``False`` -- with nothing
    written at all -- when any cell of the placement falls in a chunk the
    world has never generated or cannot read, so a bed can never end up
    half-placed across a chunk border. Raises :class:`NBTError`, again with
    nothing written, when a block whose water it must read -- one that can
    take water, placed with ``waterlogged`` unsaid -- is displacing water at
    a level, or a ``waterlogged``, the game does not have.
    """
    if isinstance(block, str):
        block = BlockState.parse(block)
    dim = save.dimension(dimension)

    cells = _cells_for(block, x, y, z)

    # Refuse atomically before writing anything.
    for (cx, cy, cz), _ in cells:
        try:
            accessor = dim.writable_blocks(cx >> 4, cz >> 4)
        except NBTError:
            return False
        if accessor is None:
            return False
        if not (accessor.min_y <= cy <= accessor.max_y):
            return False

    def block_at(bx: int, by: int, bz: int) -> BlockState | None:
        return save.get_block(bx, by, bz, dimension=dimension)

    # Every cell's water is read before any cell is written, so a displaced
    # state the game does not have refuses the placement whole.
    cells = _with_water(cells, block, block_at)

    written = []
    for (cx, cy, cz), state in cells:
        if not save.set_block(cx, cy, cz, state, dimension=dimension):
            return False  # cannot happen after the checks above, but honest
        written.append((cx, cy, cz))

    # Settle neighbor-derived state on the placed cells, their orthogonal
    # neighbors, and the cells below (a wall's post can change when
    # something lands on it) -- the block updates a player placement fires.
    touched = set(written)
    for cx, cy, cz in written:
        for dx, dz in _VECTOR.values():
            touched.add((cx + dx, cy, cz + dz))
        touched.add((cx, cy - 1, cz))
    # Top-down, then x, then z. The y ordering is what matters (a wall reads
    # the settled wall above it); x and z only break ties, but they must
    # break them *somehow* -- ordering by y alone leaves cells at the same
    # height in set-iteration order, and the order states are first written
    # is the order they enter the section's palette. That would make the
    # bytes of an identical build depend on set hashing.
    for cx, cy, cz in sorted(touched, key=lambda cell: (-cell[1], cell[0], cell[2])):
        state = block_at(cx, cy, cz)
        if state is None:
            continue
        derived = _derived_state(state, block_at, cx, cy, cz)
        if derived is not None and derived != state:
            save.set_block(cx, cy, cz, derived, dimension=dimension)
    return True
