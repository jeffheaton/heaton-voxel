"""Block light, derived: what the game will compute once it loads the chunks.

Stored light is baked and :meth:`~heaton_voxel.world.MinecraftSave.reset_lighting`
throws it away, so after an edit there is nothing on disk that says how
bright a cell will be. Whether a lit floor stays bare, whether a lake
under sea pickles thaws, whether a hall spawns nothing at night -- each is
a question about block light the game has not computed yet. This computes
it: the same flood the game's light engine runs, every emitter radiating
its strength and losing one level per block it crosses, more through
anything that blocks light, none at all into a solid block.

The rules are the game's (verified against the unobfuscated 26.3 jar):
``LightEngine.getOpacity`` charges ``max(1, dampening)`` per step, water
and ice dampen by 1 like air, a full opaque cube by 15 -- and then
``shapeOccludes`` stops a step outright wherever the two blocks' shapes
close the face between them: the solid half of a slab, the back of a
stair, the underside of a snow layer, and the gap between a bottom slab
and a top slab standing side by side, whose two half faces make one whole
one. Emission, dampening and those faces are the game's own for every
block state, from :mod:`~heaton_voxel.craft.block_facts`, which
``tools/probe_block_shapes.py`` generates from the jar, and
``tests/test_light.py`` holds the flood to the light the game itself
stored in both fixtures, cell by cell. An id the game does not have is
**air**, because air is what the game loads it as (and writes back); an
id with no namespace is minecraft's, as the game reads it, so ``stone``
is ``minecraft:stone``. A cell in a chunk the world has never generated
stays solid and dark: light from where nothing is known is not counted.

Not all light lasts. A lit furnace goes out on its first tick with nothing
in it, lit redstone ore fades, a lit redstone lamp goes dark at the next
block update unless something powers it, and an unwaxed copper bulb
weathers down to 4. :func:`durable_emission` is what a state keeps with
nothing powering it and nobody acting on it, and ``durable=True`` floods
with that: the light a guarantee may count. :func:`plan_lights` is the
guarantee -- where lamps go so that every one of a set of cells reaches a
level by this flood, and so that none of another set passes one.

A level is worth asking for by the rule of the game it meets, so the ones
the game has a rule for are named here, each read off the 26.3 jar:
:data:`NO_SPAWNS` (nothing on the Monster light rule spawns: 1 in the
overworld and the end, 8 in the nether, from the dimension types),
:data:`NO_NEW_SNOW_OR_ICE` (10: no new snow settles and no water freezes),
:data:`MELT_ICE` (11) and :data:`MELT_SNOW` (12). The Builder's light
writers take one of them, or a plain level, and no default.

Judged tier, Python only: a picture of light is a thing a session reads,
never something the twins need to agree on byte for byte.
"""

from __future__ import annotations

import heapq
from collections.abc import Mapping

from ..block_defaults import default_properties
from ..blocks import BlockState
from ..nbt import NBTError
from ..world.dimension import END, NETHER, OVERWORLD
from ._plan import as_state, read_overlay
from .block_facts import FULL_FACE, NO_FACES, facts, known
from .errors import Refused

__all__ = [
    "LightField",
    "LightPlan",
    "simulate_block_light",
    "plan_lights",
    "light_emission",
    "light_dampening",
    "light_occlusion",
    "durable_emission",
    "MONSTER_SPAWN_LIGHT",
    "LightLevel",
    "DimensionLightLevel",
    "NO_SPAWNS",
    "NO_NEW_SNOW_OR_ICE",
    "MELT_ICE",
    "MELT_SNOW",
]

#: What the game loads an id it does not have as.
_AIR_FACTS = facts("minecraft:air")


def _game_reads(state: BlockState) -> BlockState:
    """``state`` under the id the game reads its name as. An id with no
    namespace, or an empty one, is minecraft's (``Identifier.parse``): a
    bare ``stone`` in a palette loads as ``minecraft:stone``, and is a wall
    to light, not the air of an id nobody has."""
    namespace, colon, path = state.name.partition(":")
    if colon and namespace:
        return state
    return BlockState(f"minecraft:{path if colon else state.name}",
                      state.properties)


def _facts(state: BlockState):
    """The game's facts for ``state``; an id the game does not have is air."""
    state = _game_reads(state)
    if not known(state.name):
        return _AIR_FACTS
    return facts(state.name, state.properties)


def light_emission(state: BlockState | str) -> int:
    """How much block light a state gives off, as the game says.

    Read from :mod:`heaton_voxel.craft.block_facts`, generated from the
    game's own ``getLightEmission`` for every block state, with omitted
    properties taking their defaults: a bare ``campfire`` is lit and gives
    15, a bare ``redstone_lamp`` is not and gives 0, a sea pickle gives
    ``3 + 3 * pickles`` only while waterlogged. An id the game does not
    have gives nothing: it is air. Light that will not last is still
    light here; :func:`durable_emission` is the part that stays.
    """
    state = as_state(state)
    return _facts(state).emission


def light_dampening(state: BlockState | str) -> int:
    """What a state costs light to cross: the game's ``getLightDampening``,
    and never less than the 1 every step costs. A full opaque cube is 15 --
    a wall. An id the game does not have is air, and costs 1.

    The game also stops light at the faces two blocks' shapes close between
    them -- the solid half of a slab, the back of a stair -- however little
    either costs to cross: :func:`light_occlusion` is that half of the rule.
    """
    state = as_state(state)
    return max(1, _facts(state).dampening)


def light_occlusion(state: BlockState | str) -> tuple[int, ...]:
    """The faces a state's shape closes to light: six 256-bit masks, in
    :data:`~heaton_voxel.craft.block_facts.FACES` order (down, up, north,
    south, west, east), from the game's ``LightEngine.getOcclusionShape``.

    Two neighbors close the face between them when their two masks
    together cover it (:data:`~heaton_voxel.craft.block_facts.FULL_FACE`),
    which is how a bottom slab and a top slab side by side seal the gap
    neither seals alone. All zero for a block that does not occlude by its
    shape -- air, a flower, and every full cube, which stops light by its
    dampening instead -- and for an id the game does not have.
    """
    state = as_state(state)
    return _facts(state).faces


# --------------------------------------------------------------------------
# Light that lasts
# --------------------------------------------------------------------------


def _weathered_bulb(properties: dict) -> int:
    """What an unwaxed copper bulb gives off once weathered all the way."""
    return facts("minecraft:oxidized_copper_bulb", properties).emission


def _lava_source(properties: dict) -> int:
    """A lava source keeps its light; a flow keeps it only while fed."""
    return 15 if properties.get("level", "0") == "0" else 0


#: Blocks whose light does not last: block id -> (what it settles to, why).
#: What it settles to is a level, or a function of the state's properties
#: (defaults filled in). ``tests/test_light.py`` holds this table and
#: :data:`_LASTING` together to every emitting block of the game, so a game
#: update that adds a light source fails by name until it is judged.
_SETTLES = {
    "minecraft:furnace": (
        0, "a lit furnace goes out on its first tick with nothing burning in it"),
    "minecraft:blast_furnace": (
        0, "a lit blast furnace goes out on its first tick with nothing "
           "burning in it"),
    "minecraft:smoker": (
        0, "a lit smoker goes out on its first tick with nothing burning in it"),
    "minecraft:redstone_ore": (
        0, "lit redstone ore fades at its next random tick"),
    "minecraft:deepslate_redstone_ore": (
        0, "lit redstone ore fades at its next random tick"),
    "minecraft:redstone_lamp": (
        0, "a lit redstone lamp goes dark at its next block update unless "
           "something powers it, and a state cannot show what does"),
    "minecraft:copper_bulb": (
        _weathered_bulb, "an unwaxed copper bulb weathers to oxidized, which "
                         "gives off 4 lit; wax it to keep its light"),
    "minecraft:exposed_copper_bulb": (
        _weathered_bulb, "an unwaxed copper bulb weathers to oxidized, which "
                         "gives off 4 lit; wax it to keep its light"),
    "minecraft:weathered_copper_bulb": (
        _weathered_bulb, "an unwaxed copper bulb weathers to oxidized, which "
                         "gives off 4 lit; wax it to keep its light"),
    "minecraft:trial_spawner": (
        0, "a trial spawner is dark while inactive and through the cooldown "
           "every trial ends in"),
    "minecraft:vault": (
        6, "a vault gives off 12 only while a player is near it, and 6 "
           "otherwise"),
    "minecraft:fire": (
        0, "fire burns out unless it stands on a block that burns forever, "
           "and a state cannot show what it stands on"),
    "minecraft:lava": (
        _lava_source, "flowing lava drains away unless a source feeds it, "
                      "and a state cannot show whether one does"),
}

_DYES = ("white orange magenta light_blue yellow lime pink gray light_gray "
         "cyan purple blue brown green red black").split()

#: Every other block that gives off light, judged to keep it: nothing
#: unpowers, extinguishes, weathers or empties it unless a player or a mob
#: does. A lit redstone torch is here because with nothing powering its
#: base it stays lit; a lit candle or campfire stays lit until something
#: puts it out; a copper lantern weathers and keeps its 15.
_LASTING = frozenset(
    ["minecraft:candle", "minecraft:candle_cake"]
    + [f"minecraft:{dye}_candle" for dye in _DYES]
    + [f"minecraft:{dye}_candle_cake" for dye in _DYES]
    + [f"minecraft:{name}" for name in """
        amethyst_cluster large_amethyst_bud medium_amethyst_bud
        small_amethyst_bud beacon brewing_stand brown_mushroom
        calibrated_sculk_sensor sculk_sensor sculk_catalyst campfire
        soul_campfire cave_vines cave_vines_plant conduit copper_lantern
        exposed_copper_lantern weathered_copper_lantern oxidized_copper_lantern
        waxed_copper_lantern waxed_exposed_copper_lantern
        waxed_weathered_copper_lantern waxed_oxidized_copper_lantern
        oxidized_copper_bulb waxed_copper_bulb waxed_exposed_copper_bulb
        waxed_weathered_copper_bulb waxed_oxidized_copper_bulb torch wall_torch
        copper_torch copper_wall_torch soul_torch soul_wall_torch
        redstone_torch redstone_wall_torch crying_obsidian dragon_egg
        enchanting_table end_gateway end_portal end_portal_frame end_rod
        ender_chest firefly_bush glow_lichen glowstone jack_o_lantern lantern
        soul_lantern lava_cauldron light magma_block nether_portal
        ochre_froglight pearlescent_froglight verdant_froglight
        respawn_anchor sea_lantern sea_pickle shroomlight soul_fire
    """.split()])


def _settled(state: BlockState) -> int | None:
    """What ``state`` settles to, by :data:`_SETTLES`, or None if it lasts."""
    state = _game_reads(state)
    rule = _SETTLES.get(state.name)
    if rule is None:
        return None
    settles = rule[0]
    if callable(settles):
        properties = default_properties(state.name)
        properties.update(state.properties)
        settles = settles(properties)
    return settles


def durable_emission(state: BlockState | str) -> int:
    """The block light a state keeps with nothing powering it and no player
    or mob acting on it: what a light guarantee may count. Never more than
    :func:`light_emission`.

    A lit furnace, lit redstone ore, a lit redstone lamp, a trial spawner
    and fire keep nothing; a vault keeps the 6 it gives off with nobody
    near; an unwaxed copper bulb keeps the 4 it weathers down to, and a
    waxed one its whole light. A flow of lava keeps nothing and a source
    all 15. Judged from the state alone, which is the honest limit: a
    redstone lamp held lit by a lever, or fire on netherrack, counts as
    dark here -- the direction that adds a lamp rather than leaving a floor
    dark.
    """
    state = as_state(state)
    raw = light_emission(state)
    if raw == 0:
        return 0
    settles = _settled(state)
    return raw if settles is None else min(raw, settles)


def _transient_reason(state: BlockState) -> str | None:
    """Why ``state`` keeps less light than it gives off, or None if it keeps
    all of it."""
    if durable_emission(state) >= light_emission(state):
        return None
    return _SETTLES[_game_reads(as_state(state)).name][1]


# --------------------------------------------------------------------------
# The levels the game has a rule for
# --------------------------------------------------------------------------

#: The most block light a monster on the game's Monster light rule can
#: spawn at, per dimension: the dimension type's
#: ``monster_spawn_block_light_limit`` where it is below 15, and otherwise
#: the top of its ``monster_spawn_light_level``. Sky light never makes a
#: night spawn impossible in the overworld, so block light is the whole
#: question. ``tests/test_survey.py`` holds this to the dimension types
#: the probe vendors from the jar.
MONSTER_SPAWN_LIGHT = {OVERWORLD: 0, NETHER: 7, END: 0}

#: How a dimension is named in a sentence.
_DIMENSION_WORDS = {OVERWORLD: "the overworld", NETHER: "the nether",
                    END: "the end"}


class LightLevel(int):
    """A block light level named for the rule of the game it meets.

    An int, so it goes wherever a level goes and compares as its value
    (``MELT_SNOW == 12``); ``str()`` is the number, so a message reads as
    it always did, and ``repr()`` names it. ``rule`` says which of the
    game's checks the level meets, as read off the 26.3 jar. A level the
    game sets per dimension is not one of these:
    :class:`DimensionLightLevel` is no int, on purpose.
    """

    def __new__(cls, value: int, name: str, rule: str) -> "LightLevel":
        level = super().__new__(cls, value)
        if not 1 <= level <= 15:
            raise NBTError(f"a light level is 1 to 15; {name} would be {value}")
        level.name = name
        level.rule = rule
        return level

    def __getnewargs__(self):
        return int(self), self.name, self.rule

    def at(self, dimension: str | None = None) -> int:
        """The level, as a plain int: the same in every dimension."""
        return int(self)

    def __str__(self) -> str:
        return int.__repr__(self)

    def __repr__(self) -> str:
        return f"{self.name} ({int(self)})"


class DimensionLightLevel:
    """A named light level whose number is the dimension's: the game sets
    the rule per dimension type, so the level is read for the dimension a
    plan is made in (:meth:`at`).

    Deliberately **not** an int. A literal ``1`` is 1 in every dimension,
    while :data:`NO_SPAWNS` is 1 in the overworld and 8 in the nether, and
    a type that compared equal to 1 would let the two be confused exactly
    where they differ. ``int()`` of one is a ``TypeError`` saying so; the
    Builder's light writers and :func:`plan_lights` read it for their
    dimension themselves.
    """

    __slots__ = ("name", "rule", "by_dimension")

    def __init__(self, name: str, rule: str, by_dimension: Mapping[str, int]):
        self.name = name
        self.rule = rule
        #: Dimension id -> the level there.
        self.by_dimension = dict(by_dimension)

    def at(self, dimension: str) -> int:
        """The level in ``dimension``, by id: an ``NBTError`` for one whose
        rule is not known, rather than a guess."""
        level = self.by_dimension.get(dimension)
        if level is None:
            raise NBTError(
                f"{self.name} is not known for dimension {dimension!r}: its "
                f"dimension type's light rule is not one this reads (known: "
                f"{', '.join(sorted(self.by_dimension))}). Ask for a level "
                f"1 to 15 instead")
        return level

    def levels(self) -> str:
        """The level in each dimension, in words:
        ``"1 in the overworld and the end, 8 in the nether"``."""
        grouped: dict[int, list[str]] = {}
        for dimension, level in self.by_dimension.items():
            grouped.setdefault(level, []).append(
                _DIMENSION_WORDS.get(dimension, dimension))
        return ", ".join(f"{level} in {' and '.join(names)}"
                         for level, names in grouped.items())

    def __int__(self):
        raise TypeError(
            f"{self.name} is no one number -- {self.levels()} -- so it is "
            f"read for a dimension: {self.name}.at(dimension)")

    __index__ = __int__

    def __repr__(self) -> str:
        return f"{self.name} ({self.levels()})"


#: Nothing on the game's Monster light rule spawns in the cell -- one more
#: than :data:`MONSTER_SPAWN_LIGHT`: 1 in the overworld and the end, where
#: any block light refuses a spawn, and 8 in the nether, whose dimension
#: type sets no block light limit and refuses at a light level over 7.
#: Drowned are on the rule too (``Drowned.checkDrownedSpawnRules`` asks
#: it after its water test), so it keeps them away as well, but they spawn
#: in water, which :func:`~heaton_voxel.craft.survey.spawnable` does not
#: read. Zombified piglins, ghasts, magma cubes, blazes, piglins, slimes
#: and phantoms spawn by rules of their own.
NO_SPAWNS = DimensionLightLevel(
    "NO_SPAWNS",
    "Monster.isDarkEnoughToSpawn: block light over the dimension type's "
    "monster_spawn_block_light_limit refuses a spawn, and where that limit "
    "is 15 a light level over its monster_spawn_light_level does",
    {dimension: limit + 1 for dimension, limit in MONSTER_SPAWN_LIGHT.items()})

#: No new snow settles and no water freezes in the cell: it melts nothing
#: already there. Lit to this, a floor cleared once stays bare.
NO_NEW_SNOW_OR_ICE = LightLevel(
    10, "NO_NEW_SNOW_OR_ICE",
    "Biome.shouldSnow and Biome.shouldFreeze: block light under 10")

#: Ice in the cell melts: over 11 less the ice's own light dampening,
#: read from the game's facts (1). Exactly 10 is a dead band, where no
#: new ice forms and none melts.
MELT_ICE = LightLevel(
    12 - facts("minecraft:ice").dampening, "MELT_ICE",
    "IceBlock.randomTick: block light over 11 less the ice's dampening")

#: A snow layer in the cell melts.
MELT_SNOW = LightLevel(
    12, "MELT_SNOW", "SnowLayerBlock.randomTick: block light over 11")

#: The named levels, in rising order.
_NAMED_LEVELS = (NO_SPAWNS, NO_NEW_SNOW_OR_ICE, MELT_ICE, MELT_SNOW)


def _resolve_level(level, dimension, *, what: str,
                   keyword: str = "light_level") -> int:
    """``level`` as the int a plan aims for: a plain level as it is, a
    named one as its number, and a :class:`DimensionLightLevel` read for
    the dimension -- ``dimension`` is a callable answering its id, asked
    only then. A missing level is a ``TypeError`` naming the named ones,
    raised before anything is read, and so is a bool. The range is the
    caller's to check, in its own words."""
    if level is None:
        named = ", ".join(repr(named) for named in _NAMED_LEVELS)
        raise TypeError(
            f"{what} needs {keyword}=, the rule its light is for: {named} "
            f"-- named in heaton_voxel.craft.light -- or any level 1 to 15")
    if isinstance(level, bool):
        raise TypeError(f"{what} takes {keyword}= as a level 1 to 15 or a "
                        f"named one, not {level!r}")
    if isinstance(level, DimensionLightLevel):
        return level.at(dimension())
    return int(level)


# --------------------------------------------------------------------------
# The flood
# --------------------------------------------------------------------------

#: Every set of six face masks a field has met, interned so a cell holds
#: one byte; index 0 closes nothing.
_FACE_SETS: list[tuple[int, ...]] = [NO_FACES]
_FACE_IDS: dict[tuple[int, ...], int] = {NO_FACES: 0}
#: Whether a step leaving one face set through face ``out`` into another
#: is closed, memoized on ``(leaving, entering, out)``.
_SHUT: dict[tuple[int, int, int], bool] = {}


def _face_id(faces: tuple[int, ...]) -> int:
    found = _FACE_IDS.get(faces)
    if found is None:
        if len(_FACE_SETS) > 255:
            raise NBTError(
                "more than 255 distinct sets of light-occluding faces; a "
                "light field stores one byte per cell")
        found = len(_FACE_SETS)
        _FACE_SETS.append(faces)
        _FACE_IDS[faces] = found
    return found


def _spread(w: int, d: int, damp, faces, seeds, levels, floor: int,
            touched: list | None = None) -> None:
    """Flood light from ``seeds`` -- ``(index, level)`` -- into ``levels``.

    The game's rule, step by step: crossing into a neighbor costs its
    dampening (never less than one), and a step whose two shapes close the
    face between them is stopped outright, tested after the cost as
    ``BlockLightEngine.propagateIncrease`` tests it. Faces are ``out`` on
    the cell left and ``out ^ 1`` on the one entered, in
    :data:`~heaton_voxel.craft.block_facts.FACES` order. Only levels of at
    least ``floor`` are recorded, and the brightest cells are settled
    first, so each is expanded once. ``touched`` collects every index set
    from 0, for a caller that must clear ``levels`` again.
    """
    plane = w * d
    size = len(levels)
    sets, shut = _FACE_SETS, _SHUT
    buckets: list[list[int]] = [[] for _ in range(16)]
    for index, level in seeds:
        if level >= floor and level > levels[index]:
            if touched is not None and not levels[index]:
                touched.append(index)
            levels[index] = level
            buckets[level].append(index)
    for here in range(15, floor, -1):
        for index in buckets[here]:
            if levels[index] != here:
                continue
            x = index % w
            z = (index // w) % d
            mine = faces[index]
            for step, edge, out in ((1, x < w - 1, 5), (-1, x > 0, 4),
                                    (w, z < d - 1, 3), (-w, z > 0, 2),
                                    (plane, index + plane < size, 1),
                                    (-plane, index >= plane, 0)):
                if not edge:
                    continue
                neighbor = index + step
                new = here - damp[neighbor]
                if new < floor or new <= levels[neighbor]:
                    continue
                theirs = faces[neighbor]
                if mine or theirs:
                    key = (mine, theirs, out)
                    closed = shut.get(key)
                    if closed is None:
                        closed = shut[key] = (
                            sets[mine][out] | sets[theirs][out ^ 1]) == FULL_FACE
                    if closed:
                        continue
                if touched is not None and not levels[neighbor]:
                    touched.append(neighbor)
                levels[neighbor] = new
                if new > floor:
                    buckets[new].append(neighbor)


def _spread_toward(w: int, d: int, damp, faces, seeds, levels, floor: int,
                   touched: list, box) -> None:
    """:func:`_spread`, aimed at ``box`` -- ``(x0, y0, z0, x1, y1, z1)`` in
    the field's own cells: a cell is entered only where its level could
    still reach the box at ``floor`` or more by the taxicab bound, every
    step costing at least one. So a lamp's flood toward a floor of targets
    is the column of light under it rather than the ball around it, and
    every cell the box holds is set exactly as :func:`_spread` would set it
    -- a path to one never passes a cell this leaves out. Every index set
    from 0 is collected in ``touched``."""
    plane = w * d
    size = len(levels)
    sets, shut = _FACE_SETS, _SHUT
    bx0, by0, bz0, bx1, by1, bz1 = box
    buckets: list[list[int]] = [[] for _ in range(16)]
    for index, level in seeds:
        if level >= floor and level > levels[index]:
            if not levels[index]:
                touched.append(index)
            levels[index] = level
            buckets[level].append(index)
    for here in range(15, floor, -1):
        for index in buckets[here]:
            if levels[index] != here:
                continue
            x = index % w
            z = (index // w) % d
            y = index // plane
            mine = faces[index]
            for step, edge, out, nx, ny, nz in (
                    (1, x < w - 1, 5, x + 1, y, z), (-1, x > 0, 4, x - 1, y, z),
                    (w, z < d - 1, 3, x, y, z + 1), (-w, z > 0, 2, x, y, z - 1),
                    (plane, index + plane < size, 1, x, y + 1, z),
                    (-plane, index >= plane, 0, x, y - 1, z)):
                if not edge:
                    continue
                neighbor = index + step
                new = here - damp[neighbor]
                if new < floor or new <= levels[neighbor]:
                    continue
                away = ((bx0 - nx if nx < bx0 else nx - bx1 if nx > bx1 else 0)
                        + (by0 - ny if ny < by0 else ny - by1 if ny > by1 else 0)
                        + (bz0 - nz if nz < bz0 else nz - bz1 if nz > bz1 else 0))
                if new - away < floor:
                    continue
                theirs = faces[neighbor]
                if mine or theirs:
                    key = (mine, theirs, out)
                    closed = shut.get(key)
                    if closed is None:
                        closed = shut[key] = (
                            sets[mine][out] | sets[theirs][out ^ 1]) == FULL_FACE
                    if closed:
                        continue
                if not levels[neighbor]:
                    touched.append(neighbor)
                levels[neighbor] = new
                if new > floor:
                    buckets[new].append(neighbor)


class LightField:
    """Derived block light over a box, one level per cell.

    ``level(x, y, z)`` is 0 anywhere outside the field, which is what a
    caller checking a threshold wants: a cell the simulation did not cover
    is not a lit cell.
    """

    __slots__ = ("x0", "y0", "z0", "x1", "y1", "z1", "_w", "_h", "_d",
                 "_levels", "_damp", "_faces", "emitters")

    def __init__(self, x0: int, y0: int, z0: int, x1: int, y1: int, z1: int):
        self.x0, self.y0, self.z0, self.x1, self.y1, self.z1 = x0, y0, z0, x1, y1, z1
        self._w = x1 - x0 + 1
        self._h = y1 - y0 + 1
        self._d = z1 - z0 + 1
        size = self._w * self._h * self._d
        self._levels = bytearray(size)
        self._damp = bytearray(b"\x0f") * size  # unknown is solid
        #: An index into the interned face sets; 0 closes nothing.
        self._faces = bytearray(size)
        #: ``(x, y, z, strength)`` of every source the flood started from.
        self.emitters: list[tuple[int, int, int, int]] = []

    def _index(self, x: int, y: int, z: int) -> int:
        if not (self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1
                and self.z0 <= z <= self.z1):
            return -1
        return ((y - self.y0) * self._d + (z - self.z0)) * self._w + (x - self.x0)

    def _cell(self, index: int) -> tuple[int, int, int]:
        plane = self._w * self._d
        return (self.x0 + index % self._w, self.y0 + index // plane,
                self.z0 + (index // self._w) % self._d)

    def level(self, x: int, y: int, z: int) -> int:
        index = self._index(x, y, z)
        return self._levels[index] if index >= 0 else 0

    def dampening(self, x: int, y: int, z: int) -> int:
        index = self._index(x, y, z)
        return self._damp[index] if index >= 0 else 15

    def below(self, cells, threshold: int) -> list[tuple[int, int, int, int]]:
        """``(x, y, z, level)`` for every cell under ``threshold``."""
        short = []
        for x, y, z in cells:
            level = self.level(x, y, z)
            if level < threshold:
                short.append((x, y, z, level))
        return short

    def _flood(self) -> None:
        _spread(self._w, self._d, self._damp, self._faces,
                [(self._index(x, y, z), strength)
                 for x, y, z, strength in self.emitters],
                self._levels, 1)

    def __repr__(self) -> str:
        return (f"<LightField x {self.x0}..{self.x1} y {self.y0}..{self.y1} "
                f"z {self.z0}..{self.z1}, {len(self.emitters)} emitters>")


def _classify(state: BlockState, cache: dict) -> tuple[int, int, int, int]:
    """``(cost, raw, durable, face set)`` for a state, memoized in ``cache``."""
    found = cache.get(state)
    if found is None:
        info = _facts(state)
        raw = info.emission
        found = (max(1, info.dampening), raw,
                 durable_emission(state) if raw else 0,
                 _face_id(info.faces) if info.faces != NO_FACES else 0)
        cache[state] = found
    return found


def _read(dim, x0: int, y0: int, z0: int, x1: int, y1: int, z1: int, plan,
          cache: dict) -> tuple[LightField, dict[int, tuple[int, int]]]:
    """A field over the box with every cell's cost and faces filled in from
    the world and then ``plan`` (already read), and every source in it:
    ``{index: (raw, durable)}``, in scan order and then plan order."""
    field = LightField(x0, y0, z0, x1, y1, z1)
    damp, faces = field._damp, field._faces
    w, d = field._w, field._d
    sources: dict[int, tuple[int, int]] = {}

    for chunk_x in range(field.x0 >> 4, (field.x1 >> 4) + 1):
        for chunk_z in range(field.z0 >> 4, (field.z1 >> 4) + 1):
            accessor = dim.blocks(chunk_x, chunk_z)
            if accessor is None:
                continue
            base_x, base_z = chunk_x * 16, chunk_z * 16
            lx0, lx1 = max(field.x0, base_x) - base_x, min(field.x1, base_x + 15) - base_x
            lz0, lz1 = max(field.z0, base_z) - base_z, min(field.z1, base_z + 15) - base_z
            # The game keeps light one section past each end of the world
            # (LevelLightEngine's light sections run from the lowest section
            # less one to the highest plus one) and reads a block there as
            # air (LevelChunk.getBlockState), so a lamp in the top layer
            # lights the cell over it. Past those, no light is kept at all.
            low, high = accessor.min_y, accessor.max_y
            if high >= low:
                low, high = low - 16, high + 16
            for section_y in range(field.y0 >> 4, (field.y1 >> 4) + 1):
                section = accessor.sections.get(section_y)
                base_y = section_y * 16
                ly0, ly1 = max(field.y0, base_y) - base_y, min(field.y1, base_y + 15) - base_y
                container = section.blocks if section is not None else None
                if container is None:
                    if section is None and not (low <= base_y <= high):
                        continue  # past where the game keeps light: shut
                    # A stored section with no blocks is air, and so is the
                    # section of light just past the world.
                    for ly in range(ly0, ly1 + 1):
                        row_y = (base_y + ly - field.y0) * d
                        for lz in range(lz0, lz1 + 1):
                            start = (row_y + base_z + lz - field.z0) * w + base_x + lx0 - field.x0
                            damp[start:start + lx1 - lx0 + 1] = b"\x01" * (lx1 - lx0 + 1)
                    continue
                classes = [_classify(state, cache) for state in container.palette]
                plain = not any(face or raw for _, raw, _, face in classes)
                costs = [cost for cost, _, _, _ in classes]
                indices = container.indices()
                for ly in range(ly0, ly1 + 1):
                    row_y = (base_y + ly - field.y0) * d
                    for lz in range(lz0, lz1 + 1):
                        row = ly * 256 + lz * 16
                        out = (row_y + base_z + lz - field.z0) * w - field.x0 + base_x
                        if plain:
                            damp[out + lx0:out + lx1 + 1] = bytes(
                                costs[indices[row + lx]] for lx in range(lx0, lx1 + 1))
                            continue
                        for lx in range(lx0, lx1 + 1):
                            cost, raw, lasting, face = classes[indices[row + lx]]
                            damp[out + lx] = cost
                            if face:
                                faces[out + lx] = face
                            if raw:
                                sources[out + lx] = (raw, lasting)

    for (x, y, z), state in plan.items():
        index = field._index(x, y, z)
        if index < 0:
            continue
        cost, raw, lasting, face = _classify(state, cache)
        damp[index] = cost
        faces[index] = face
        sources.pop(index, None)
        if raw:
            sources[index] = (raw, lasting)
    return field, sources


def simulate_block_light(save, corner1, corner2, *, dimension: str | None = None,
                         overlay=None, margin: int = 15,
                         durable: bool = False) -> LightField:
    """Block light over a box, as the game will compute it.

    ``corner1``/``corner2`` are the cells whose light is wanted; the field
    reaches ``margin`` blocks further on every side, because a lamp fifteen
    blocks outside the box still lights its edge. ``overlay`` maps
    ``(x, y, z)`` to a state the world does not yet hold -- a plan's lamps
    or pickles -- so the check can run *before* anything is written. Cells
    in chunks the world has never generated stay solid and dark. The
    section the game keeps light in just past the top of the world, and
    the one just past the bottom, are air, as the game reads them: a lamp
    set into the top layer lights the cell a walker on it stands in.
    Further out than those nothing holds light, and they stay solid too.

    ``durable=True`` counts only the light that lasts
    (:func:`durable_emission`): a lit furnace is dark, an unwaxed copper
    bulb is the 4 it weathers to. That is the flood to check a guarantee
    against -- a floor that stays bare, a room nothing spawns in. The
    default counts every source as it stands, which is the light the game
    will compute on the next load and the one to hold a ceiling to.
    """
    dim = save.dimension(dimension)
    x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
    y0, y1 = sorted((int(corner1[1]), int(corner2[1])))
    z0, z1 = sorted((int(corner1[2]), int(corner2[2])))
    field, sources = _read(dim, x0 - margin, y0 - margin, z0 - margin,
                           x1 + margin, y1 + margin, z1 + margin,
                           read_overlay(overlay), {})
    pick = 1 if durable else 0
    field.emitters = [(*field._cell(index), strengths[pick])
                      for index, strengths in sources.items() if strengths[pick]]
    field._flood()
    return field


# --------------------------------------------------------------------------
# Planning lamps by the flood
# --------------------------------------------------------------------------

#: The most times :func:`plan_lights` re-floods and patches again. A fixture
#: that changes the shape of the world (an anchor block, glowstone set into
#: a path) shades what an earlier lamp lit, which only a full flood sees.
_ROUNDS = 8


def _cell(cell) -> tuple[int, int, int]:
    return int(cell[0]), int(cell[1]), int(cell[2])


def _near(by_y: dict, x: int, y: int, z: int, radius: int):
    """Every cell of ``by_y`` -- ``{y: {(x, z): cell}}`` -- within taxicab
    ``radius`` of ``(x, y, z)``."""
    for ty, plane in by_y.items():
        rest = radius - abs(ty - y)
        if rest < 0 or not plane:
            continue
        if len(plane) <= 2 * rest * (rest + 1) + 1:
            for (tx, tz), cell in plane.items():
                if abs(tx - x) + abs(tz - z) <= rest:
                    yield cell
        else:
            for dx in range(-rest, rest + 1):
                span = rest - abs(dx)
                for dz in range(-span, span + 1):
                    cell = plane.get((x + dx, z + dz))
                    if cell is not None:
                        yield cell


class LightPlan:
    """Where lamps go so a set of cells reaches a light level, by the flood.

    What :func:`plan_lights` decided. :attr:`lamps` maps each chosen
    candidate's key to its fixture -- ``{cell: BlockState}``, the lamp and
    whatever it needs with it -- in key order, and :attr:`overlay` is all
    of them as one plan, to write or to check a walk or a spawn against.

    Being dark is a result, not an exception, as a stranded cell is for
    :func:`~heaton_voxel.craft.survey.reachable`: :attr:`dark` lists every
    target left under the level, ``(x, y, z, level)``, and :attr:`over`
    every ``at_most`` cell lit past its limit, ``(x, y, z, level, limit)``.
    :meth:`require` turns either into a refusal. :attr:`blocked` lists the
    targets inside an opaque block that gives off nothing -- a pier, a
    wall -- which can never hold light and are not failures;
    :attr:`excluded` says, by key, which candidates were never considered
    and why. :attr:`lit_before` counts the targets the world already lit,
    :attr:`from_lattice` and :attr:`patched` the lamps the lattice placed
    and the ones the patching added, and :attr:`field` is the final
    durable flood with the plan applied -- read as far past the targets,
    the limits and the fixtures as light that could decide them travels
    (``15 - at_least``, or ``14 -`` the lowest limit where that is more),
    so every target at the level and every limit passed reads exactly, and
    a dark target's level, always under the level, counts only that light.
    A plan that lit every target without changing the shape of the world
    -- glowstone set where there was air or paving does not, being too
    bright to dim anything -- is decided without that flood, which runs the
    first time :attr:`field` is asked for.
    """

    __slots__ = ("at_least", "targets", "lamps", "from_lattice", "patched",
                 "lit_before", "dark", "blocked", "over", "excluded", "_field",
                 "_settle", "_already")

    def __init__(self, at_least: int):
        self.at_least = at_least
        self.targets = 0
        self.lamps: dict[tuple[int, int, int], dict[tuple[int, int, int], BlockState]] = {}
        self.from_lattice = 0
        self.patched = 0
        self.lit_before = 0
        self.dark: list[tuple[int, int, int, int]] = []
        self.blocked: list[tuple[int, int, int]] = []
        self.over: list[tuple[int, int, int, int, int]] = []
        self.excluded: dict[tuple[int, int, int], str] = {}
        self._field: LightField | None = None
        #: The final flood, still to run (see :attr:`field`).
        self._settle = None
        self._already: frozenset = frozenset()

    @property
    def field(self) -> LightField | None:
        """The final durable flood with the plan applied."""
        if self._settle is not None:
            self._field, self._settle = self._settle(), None
        return self._field

    @property
    def overlay(self) -> dict[tuple[int, int, int], BlockState]:
        """Every chosen fixture as one plan, in sorted cell order."""
        merged: dict[tuple[int, int, int], BlockState] = {}
        for fixture in self.lamps.values():
            merged.update(fixture)
        return dict(sorted(merged.items()))

    @property
    def ok(self) -> bool:
        """Every target reaches the level and no limit is passed."""
        return not self.dark and not self.over

    def require(self) -> "LightPlan":
        """This plan, or :class:`~heaton_voxel.craft.errors.Refused` naming
        the first dark cell and the first passed limit, with every one of
        both as :attr:`~heaton_voxel.craft.errors.Refused.faults`."""
        if self.ok:
            return self
        parts = []
        if self.dark:
            x, y, z, level = self.dark[0]
            parts.append(f"{len(self.dark)} of {self.targets} cells stay under "
                         f"light {self.at_least}, first ({x}, {y}, {z}) at "
                         f"{level}")
        if self.over:
            x, y, z, level, limit = self.over[0]
            note = (", as it already was before the plan"
                    if (x, y, z) in self._already else "")
            parts.append(f"{len(self.over)} cells are lit past their limit, "
                         f"first ({x}, {y}, {z}) at {level} where at most "
                         f"{limit} may stand{note}")
        raise Refused("; ".join(parts), faults=[*self.dark, *self.over])

    def __repr__(self) -> str:
        parts = [f"{len(self.lamps)} lamps ({self.from_lattice} lattice, "
                 f"{self.patched} patched)",
                 f"{self.targets} targets at >= {self.at_least}",
                 f"{self.lit_before} lit before"]
        if self.dark:
            parts.append(f"{len(self.dark)} dark")
        if self.blocked:
            parts.append(f"{len(self.blocked)} blocked")
        if self.over:
            parts.append(f"{len(self.over)} over")
        if self.excluded:
            parts.append(f"{len(self.excluded)} excluded")
        return f"<LightPlan {', '.join(parts)}>"


class _Fixture:
    """One candidate: its cells, and what each does to light."""

    __slots__ = ("cells", "entries", "emitters")

    def __init__(self, cells, entries, emitters):
        #: ``{cell: BlockState}``, in sorted cell order.
        self.cells = cells
        #: ``(index, cost, face set, raw, durable)`` per cell, same order.
        self.entries = entries
        #: ``(x, y, z, raw, durable)`` of every cell that gives off light.
        self.emitters = emitters


def plan_lights(save, targets, at_least: int, candidates, *, at_most=None,
                lattice=None, dimension: str | None = None,
                overlay=None) -> LightPlan:
    """Choose lamps from ``candidates`` until every target reaches ``at_least``.

    ``targets`` are the cells whose light the game reads -- the cell a
    walker's feet stand in, an ice cell that must thaw -- and ``at_least``
    the level each needs, 1 to 15, or a named one (:data:`MELT_ICE`;
    :data:`NO_SPAWNS` is read for ``dimension``). The level is reached by
    the game's own flood (:func:`simulate_block_light`, with
    ``durable=True``): light
    that goes round a wall rather than through it, is shut out by the
    solid half of a slab, and counts only what lasts. Light the world
    already holds is counted, so a lamp already lighting a floor is not
    doubled, and a lamp somebody since broke is not credited.

    ``candidates`` maps a key cell ``(x, y, z)`` to a block -- a lamp at
    that cell -- or to a whole fixture, ``{cell: block}``: a lantern with
    its chain and the block it hangs from, glowstone with carpet over it.
    A block is a name, a :class:`BlockState` or ``(name, properties)``. A
    candidate with no lasting light (:func:`durable_emission`) is a
    refusal by name. ``lattice`` is an optional predicate on candidate
    keys, or a sequence of them: those it proposes are tried first -- in
    key order for a predicate, in the order given for a sequence, whose
    every key must be a candidate's -- each placed if it lights a target
    still dark; then the rest are patched in greedily, the one that lights
    the most dark targets first, ties to the lowest key. A sequence is for
    a caller whose lattice is itself planned in an order, as the Builder's
    is: a lamp tried later is placed only for the targets the earlier ones
    left dark. A fixture that changes the shape of the world -- an anchor block,
    glowstone set into a path -- can shade what a lamp chosen before it
    lit, which only a fresh flood sees, so the plan re-floods and patches
    again, up to eight times. ``overlay`` is a plan the world does not hold
    yet, such as the surface a pave is about to lay, and is read under the
    candidates. A target inside an opaque block that gives off nothing --
    a pier standing in a hall -- can hold no light, and is
    :attr:`~LightPlan.blocked` rather than dark.

    ``at_most`` maps cells to the most light each may hold, 0 to 14: an
    ice lid over a room that must stay frozen. A candidate whose light
    could pass a limit by the taxicab bound -- every step costs at least
    one and a shape only ever takes light away, so the bound holds
    whatever stands between -- is :attr:`~LightPlan.excluded` with the
    reason, and so is one that makes any cell more transparent, which
    could let light already in the world through. The final check counts
    every source as it stands, transient ones included.

    Everything is decided over sorted keys and cells, so the plan does not
    depend on the order the candidates came in. Nothing is written: the
    plan is :attr:`LightPlan.overlay`, and :meth:`LightPlan.require` is
    the refusal when it is not enough.
    """
    planner = _Planner(save, targets, at_least, candidates, at_most, lattice,
                       dimension, overlay)
    return planner.plan()


def _plan_proposing(save, targets, at_least: int, candidates, propose, *,
                    at_most=None, dimension: str | None = None,
                    overlay=None) -> LightPlan:
    """:func:`plan_lights` with its lattice proposed once the world's own
    light is known: ``propose`` is given the targets no lamp is wanted for
    -- lit to ``at_least`` already, or with a block standing in them -- and
    returns the candidate keys to try first, in order, as a ``lattice``
    sequence is. For a caller whose proposal is arithmetic that should
    leave out what the world already lights, as the Builder's floor and
    hall lattices do: one read of the world and one flood serve both, where
    asking first would read and flood it twice. ``at_most`` is
    :func:`plan_lights`' own -- a candidate that could light a limited cell
    past its limit is excluded before anything is proposed, so a proposal
    naming it is passed over -- and leaving it out limits nothing, the plan
    every caller without it has always had."""
    planner = _Planner(save, targets, at_least, candidates, at_most, None,
                       dimension, overlay)
    planner._propose(propose(planner._settled()))
    return planner.plan()


class _Planner:
    """The working state of one :func:`plan_lights` call."""

    def __init__(self, save, targets, at_least, candidates, at_most, lattice,
                 dimension, overlay):
        at_least = _resolve_level(
            at_least, lambda: save.dimension(dimension).name,
            what="plan_lights", keyword="at_least")
        if not 1 <= at_least <= 15:
            raise NBTError(f"at_least must be a light level from 1 to 15, "
                           f"got {at_least}")
        goal = sorted({_cell(cell) for cell in targets})
        if not goal:
            raise NBTError("plan_lights needs at least one target cell to light")
        limits: dict[tuple[int, int, int], int] = {}
        for cell, limit in (at_most or {}).items():
            limit = int(limit)
            if not 0 <= limit <= 14:
                raise NBTError(f"an at_most limit is a light level from 0 to "
                               f"14, got {limit} at {_cell(cell)}")
            limits[_cell(cell)] = limit
        limits = dict(sorted(limits.items()))
        for cell in goal:
            if cell in limits and limits[cell] < at_least:
                raise NBTError(
                    f"{cell} must reach light {at_least} and hold at most "
                    f"{limits[cell]}; no plan can do both")

        keyed: dict[tuple[int, int, int], object] = {}
        for key, value in candidates.items():
            cell = _cell(key)
            if cell in keyed:
                raise NBTError(f"two candidates share the key {cell}")
            keyed[cell] = value
        self.save, self.dim = save, save.dimension(dimension)
        self.at_least, self.goal, self.limits = at_least, goal, limits
        self.plan_cells = read_overlay(overlay)
        self.cache: dict = {}

        cells_of: dict[tuple[int, int, int], dict] = {}
        # Each state's lasting light, judged once: a floor's candidates are
        # thousands of one lamp.
        keeps: dict[BlockState, int] = {}
        for key in sorted(keyed):
            value = keyed[key]
            if isinstance(value, Mapping):
                cells = dict(sorted(read_overlay(value).items()))
            else:
                cells = {key: as_state(value)}
            if not cells:
                raise NBTError(f"the candidate at {key} is an empty fixture")
            lasting = []
            for state in cells.values():
                kept = keeps.get(state)
                if kept is None:
                    kept = keeps[state] = durable_emission(state)
                if kept:
                    lasting.append(state)
            if not lasting:
                glowing = [state for state in cells.values() if light_emission(state)]
                if glowing:
                    reason = (f"{glowing[0]} gives off light that does not "
                              f"last: {_transient_reason(glowing[0])}")
                else:
                    reason = (f"{next(iter(cells.values()))} gives off no "
                              f"light")
                raise NBTError(f"the candidate at {key} has no lasting light: "
                               f"{reason}")
            cells_of[key] = cells

        everything = [*goal, *limits]
        for cells in cells_of.values():
            everything.extend(cells)
        # As far past them as light that matters travels: a target reaching
        # at_least has it from a source no further than 15 - at_least along
        # a path no longer, and a limit passed by a source no further than
        # 14 - limit, so nothing past that can change what is decided.
        margin = 15 - at_least
        if limits:
            margin = max(margin, 14 - min(limits.values()))
        xs = [c[0] for c in everything]
        ys = [c[1] for c in everything]
        zs = [c[2] for c in everything]
        self.field, self.world = _read(
            self.dim, min(xs) - margin, min(ys) - margin, min(zs) - margin,
            max(xs) + margin, max(ys) + margin, max(zs) + margin,
            self.plan_cells, self.cache)
        field = self.field
        self.size = len(field._levels)
        self.base = (bytes(field._damp), bytes(field._faces)) if limits else None

        #: Every candidate's cells, by key in key order; each is read
        #: against the field the first time it is weighed (:meth:`_fixture`),
        #: since most of a floor's candidates never are.
        self.cells_of = cells_of
        self.fixtures: dict[tuple[int, int, int], _Fixture] = {}

        self.excluded: dict[tuple[int, int, int], str] = {}
        if limits:
            self._exclude()
        self.order = [key for key in cells_of if key not in self.excluded]
        self._propose(lattice)
        self.target_index = {field._index(*cell): cell for cell in goal}
        self.chosen: dict[tuple[int, int, int], _Fixture] = {}
        self.chosen_cells: dict[tuple[int, int, int], BlockState] = {}
        #: Sources the chosen fixtures put in place of the world's.
        self.overrides: dict[int, tuple[int, int]] = {}
        self.scratch = bytearray(self.size)
        #: The world's own flood and the targets that can hold light, once
        #: read (:meth:`_baseline`).
        self.start: tuple[bytearray, list[int]] | None = None

    def _fixture(self, key) -> _Fixture:
        """The candidate at ``key``, its cells read against the field."""
        fixture = self.fixtures.get(key)
        if fixture is None:
            field, cache = self.field, self.cache
            entries, emitters = [], []
            for cell, state in self.cells_of[key].items():
                cost, raw, lasting, face = _classify(state, cache)
                entries.append((field._index(*cell), cost, face, raw, lasting))
                if raw:
                    emitters.append((*cell, raw, lasting))
            fixture = self.fixtures[key] = _Fixture(self.cells_of[key], entries,
                                                    emitters)
        return fixture

    def _propose(self, lattice) -> None:
        """The keys the lattice proposes, in the order they are tried: a
        predicate's in key order, a sequence's in its own."""
        if lattice is None:
            self.proposed = []
        elif callable(lattice):
            self.proposed = [key for key in self.order if lattice(key)]
        else:
            proposed = list(dict.fromkeys(_cell(key) for key in lattice))
            for key in proposed:
                if key not in self.cells_of:
                    raise NBTError(f"the lattice proposes {key}, which is not "
                                   f"a candidate's key")
            self.proposed = [key for key in proposed if key not in self.excluded]

    def _baseline(self) -> tuple[bytearray, list[int]]:
        """The world's own durable flood, before any candidate is chosen,
        and the targets in scan order that can hold light at all: flooded
        once, for :meth:`plan` and for a caller asking first what the world
        already lights (:meth:`_settled`)."""
        if self.start is None:
            levels = self._flood(1)
            sources = self._sources()
            targets = self.target_index
            live = [index for index in sorted(targets, key=targets.__getitem__)
                    if not self._blocked(index, sources)]
            self.start = (levels, live)
        return self.start

    def _settled(self) -> set[tuple[int, int, int]]:
        """The targets no lamp is wanted for: lit to the level by the world
        already, or with a block standing in them that light cannot
        enter."""
        levels, _ = self._baseline()
        damp, floor = self.field._damp, self.at_least
        return {cell for index, cell in self.target_index.items()
                if levels[index] >= floor or damp[index] >= 15}

    # -- the ceiling -------------------------------------------------------

    def _exclude(self) -> None:
        """Hold back every candidate that could light a limited cell past
        its limit, or open the world to light that could."""
        sets, damp, faces = _FACE_SETS, self.field._damp, self.field._faces
        buckets: dict[tuple[int, int, int], list] = {}
        for cell, limit in self.limits.items():
            buckets.setdefault((cell[0] >> 4, cell[1] >> 4, cell[2] >> 4),
                               []).append((cell, limit))
        for key in self.cells_of:
            fixture = self._fixture(key)
            reason = None
            for x, y, z, raw, _ in fixture.emitters:
                for bx in range((x - raw) >> 4, ((x + raw) >> 4) + 1):
                    for by in range((y - raw) >> 4, ((y + raw) >> 4) + 1):
                        for bz in range((z - raw) >> 4, ((z + raw) >> 4) + 1):
                            for (lx, ly, lz), limit in buckets.get((bx, by, bz), ()):
                                reach = raw - abs(lx - x) - abs(ly - y) - abs(lz - z)
                                if reach > limit and (reason is None
                                                      or (lx, ly, lz) < reason[0]):
                                    reason = ((lx, ly, lz), reach)
            if reason is not None:
                self.excluded[key] = f"would light {reason[0]} to {reason[1]}"
                continue
            for (index, cost, face, _, _), cell in zip(fixture.entries, fixture.cells):
                old_cost, old_face = damp[index], faces[index]
                opened = cost < 15 and any(
                    sets[old_face][side] & ~sets[face][side] for side in range(6))
                if cost < old_cost or opened:
                    self.excluded[key] = (f"would open {cell} to light, which "
                                          f"could let the world's own through")
                    break

    # -- floods ------------------------------------------------------------

    def _sources(self) -> dict[int, tuple[int, int]]:
        current = dict(self.world)
        for index, strengths in self.overrides.items():
            if strengths[0]:
                current[index] = strengths
            else:
                current.pop(index, None)
        return current

    def _flood(self, pick: int, damp=None, faces=None, sources=None) -> bytearray:
        field = self.field
        levels = bytearray(self.size)
        sources = self._sources() if sources is None else sources
        _spread(field._w, field._d,
                field._damp if damp is None else damp,
                field._faces if faces is None else faces,
                [(index, strengths[pick]) for index, strengths in sources.items()
                 if strengths[pick]], levels, 1)
        return levels

    def _blocked(self, index: int, sources) -> bool:
        """Whether a target is inside an opaque block that keeps no light of
        its own: a real block, never a cell the world does not hold."""
        if self.field._damp[index] < 15 or sources.get(index, (0, 0))[1]:
            return False
        cell = self.target_index[index]
        state = self.chosen_cells.get(cell)
        if state is None:
            state = self.plan_cells.get(cell)
        if state is None:
            state = self.save.get_block(*cell, dimension=self.dim.name)
        return state is not None

    def _dark(self, levels, live) -> set[int]:
        floor = self.at_least
        return {index for index in live if levels[index] < floor}

    # -- choosing ----------------------------------------------------------

    def _clashes(self, fixture: _Fixture) -> bool:
        held = self.chosen_cells
        return any(held.get(cell, state) != state
                   for cell, state in fixture.cells.items())

    def _bound(self, fixture: _Fixture, by_y: dict) -> int:
        """How many dark targets the fixture could light by the taxicab
        bound: an upper bound on what its flood lights."""
        reached: set = set()
        for x, y, z, _, lasting in fixture.emitters:
            if lasting >= self.at_least:
                reached.update(_near(by_y, x, y, z, lasting - self.at_least))
        return len(reached)

    def _cover(self, fixture: _Fixture, box) -> frozenset:
        """The targets in ``box`` -- field cells holding every dark target --
        the fixture's own flood brings to the level, with it and every
        chosen fixture in place. The flood is aimed at the box
        (:func:`_spread_toward`), since light going elsewhere lights none of
        them."""
        field = self.field
        damp, faces = field._damp, field._faces
        saved = [(index, damp[index], faces[index]) for index, *_ in fixture.entries]
        for index, cost, face, _, _ in fixture.entries:
            damp[index] = cost
            faces[index] = face
        touched: list[int] = []
        _spread_toward(field._w, field._d, damp, faces,
                       [(index, lasting) for index, _, _, _, lasting
                        in fixture.entries if lasting],
                       self.scratch, self.at_least, touched, box)
        targets = self.target_index
        covered = frozenset(index for index in touched if index in targets)
        scratch = self.scratch
        for index in touched:
            scratch[index] = 0
        for index, cost, face in saved:
            damp[index] = cost
            faces[index] = face
        return covered

    def _choose(self, key, fixture: _Fixture) -> bool:
        """Put a fixture in place; True if it changed the shape of the world
        (or took the place of a light), which only a full flood accounts.

        An opaque block closing no face of its own that keeps a light of 14
        or more -- glowstone set where there was air or paving -- does not
        count: light passing through a cell that is no source is 14 at the
        most, and the lamp now there gives every neighbor at least what that
        light did, so it can dim nothing, and every target a cover found lit
        stays lit."""
        field = self.field
        damp, faces = field._damp, field._faces
        reshaped = False
        for (index, cost, face, raw, lasting), (cell, state) in zip(
                fixture.entries, fixture.cells.items()):
            if index in self.world or (
                    (damp[index], faces[index]) != (cost, face)
                    and not (cost >= 15 and lasting >= 14 and not face)):
                reshaped = True
            damp[index] = cost
            faces[index] = face
            self.overrides[index] = (raw, lasting)
            self.chosen_cells[cell] = state
        self.chosen[key] = fixture
        return reshaped

    def _round(self, dark: set[int], first: bool) -> tuple[int, int, bool]:
        """One pass of lattice (the first round only) and patching over the
        dark targets: ``(from lattice, patched, reshaped)``."""
        targets = self.target_index
        by_y: dict[int, dict] = {}
        for index in sorted(dark, key=targets.__getitem__):
            x, y, z = targets[index]
            by_y.setdefault(y, {})[(x, z)] = index
        covers: dict = {}
        # Every dark target lies in this box of the field's cells, so a flood
        # toward anything else is wasted (see _spread_toward); dark only
        # shrinks through the round, so it holds for the whole of it.
        field = self.field
        cells = [targets[index] for index in dark]
        box = (min(c[0] for c in cells) - field.x0, min(c[1] for c in cells) - field.y0,
               min(c[2] for c in cells) - field.z0, max(c[0] for c in cells) - field.x0,
               max(c[1] for c in cells) - field.y0, max(c[2] for c in cells) - field.z0)

        def cover(key):
            found = covers.get(key)
            if found is None:
                found = covers[key] = self._cover(self._fixture(key), box)
            return found

        def take(key, lit):
            for index in lit:
                x, y, z = targets[index]
                del by_y[y][(x, z)]
            dark.difference_update(lit)
            return self._choose(key, self._fixture(key))

        from_lattice = patched = 0
        reshaped = False
        if first:
            for key in self.proposed:
                if not dark:
                    break
                fixture = self._fixture(key)
                if (key in self.chosen or self._clashes(fixture)
                        or not self._bound(fixture, by_y)):
                    continue
                lit = cover(key) & dark
                if lit:
                    reshaped |= take(key, lit)
                    from_lattice += 1

        # Lazy greedy. Every entry holds an upper bound on what its candidate
        # would light now -- the taxicab count, or its flood's count taken
        # when ``stamp`` picks had been made -- so one popped with an exact
        # count taken since the last pick lights at least as much as any
        # other, and ties go to the lowest key because keys order the heap
        # after the count. A stale bound is refreshed as a bound, which is
        # cheap; only a bound that reaches the top is flooded. A lattice that
        # left nothing dark leaves nothing to patch, and no bound to take.
        heap = []
        for key in (self.order if dark else ()):
            fixture = self._fixture(key)
            if key in self.chosen or self._clashes(fixture):
                continue
            bound = self._bound(fixture, by_y)
            if bound:
                heap.append((-bound, key, -1, False))
        heapq.heapify(heap)
        picks = 0
        while heap and dark:
            _, key, stamp, exact = heapq.heappop(heap)
            fixture = self._fixture(key)
            if key in self.chosen or self._clashes(fixture):
                continue
            if exact and stamp == picks:
                reshaped |= take(key, cover(key) & dark)
                patched += 1
                picks += 1
            elif exact or stamp == picks:
                lit = len(cover(key) & dark)
                if lit:
                    heapq.heappush(heap, (-lit, key, picks, True))
            else:
                bound = self._bound(fixture, by_y)
                if bound:
                    heapq.heappush(heap, (-bound, key, picks, False))
        return from_lattice, patched, reshaped

    # -- the whole -----------------------------------------------------------

    def plan(self) -> LightPlan:
        result = LightPlan(self.at_least)
        result.targets = len(self.goal)
        result.excluded = dict(self.excluded)

        levels, live = self._baseline()
        result.lit_before = sum(1 for index in live
                                if levels[index] >= self.at_least)
        for round_ in range(_ROUNDS):
            dark = self._dark(levels, live)
            if not dark:
                break
            from_lattice, patched, reshaped = self._round(dark, round_ == 0)
            result.from_lattice += from_lattice
            result.patched += patched
            if not (from_lattice or patched):
                break
            if not (reshaped or dark or self.limits):
                # Every target is lit by what the covers saw, and nothing
                # chosen can have dimmed it: decided, and the final flood is
                # left until somebody asks for the field.
                levels = None
                break
            levels = self._flood(1)
            if not reshaped:
                break

        sources = self._sources()
        field = self.field
        field.emitters = [(*field._cell(index), strengths[1])
                          for index, strengths in sources.items() if strengths[1]]
        if levels is None:
            def settle(field=field):
                field._levels = self._flood(1)
                return field
            result._settle = settle
        else:
            field._levels = levels
            result._field = field
        result.lamps = {key: dict(self.chosen[key].cells) for key in sorted(self.chosen)}
        for cell in self.goal:
            index = field._index(*cell)
            if self._blocked(index, sources):
                result.blocked.append(cell)
            elif levels is not None and levels[index] < self.at_least:
                result.dark.append((*cell, levels[index]))

        if self.limits:
            transient = any(raw != lasting for raw, lasting in sources.values())
            raw_levels = self._flood(0, sources=sources) if transient else levels
            for cell, limit in self.limits.items():
                level = raw_levels[field._index(*cell)]
                if level > limit:
                    result.over.append((*cell, level, limit))
            if result.over:
                damp, faces = (bytearray(part) for part in self.base)
                before = self._flood(0, damp=damp, faces=faces, sources=self.world)
                result._already = frozenset(
                    (x, y, z) for x, y, z, _, limit in result.over
                    if before[field._index(x, y, z)] > limit)
        return result
