"""Biomes, for a map that has to tell one region from another.

The colors live in :mod:`heaton_voxel.craft.colors`, beside the block
colors and with the reason they are deliberately not the game's own. This
module answers for them under its older names -- :data:`BIOME_COLORS`,
:data:`UNKNOWN`, :func:`build_palette`, :func:`heuristics` and
:func:`color_for` -- so nothing that reads them had to change. What stays
here is what is not a color: :func:`pretty`, the name a person reads,
:data:`FREEZING_BIOMES` with :func:`freezes`, a game fact that the Unity
app's biome palette generator bakes into the app beside the colors and
audits against the jar, and :data:`VILLAGER_TYPES` with
:func:`villager_type`, the game's own table of which type of villager a
biome gives, held to the jar by the test suite.
"""

from __future__ import annotations

from .colors import (  # noqa: F401 -- the older names, kept answering
    BIOME_COLORS,
    BIOME_UNKNOWN as UNKNOWN,
    biome_color as color_for,
    biome_heuristics as heuristics,
    biome_palette as build_palette,
)


def pretty(name: str) -> str:
    """``minecraft:snowy_taiga`` -> ``Snowy Taiga``.

    Verified exact against the game's own ``en_us.json`` for all 66 vanilla
    biomes -- every ``biome.minecraft.*`` string is byte-identical to
    title-casing the id -- so no name table is carried, and a modded id
    degrades to something readable rather than to nothing.
    """
    short = name.split(":", 1)[-1]
    return " ".join(word.capitalize() for word in short.split("_"))


#: The biomes that freeze, which is what makes a thaw temporary.
#:
#: ``Biome.shouldFreeze`` is ``getTemperature(pos) < 0.15f`` -- read straight
#: out of 26.2's bytecode -- so the set is every biome whose ``temperature``
#: is under that, **plus** the two carrying ``temperature_modifier: frozen``,
#: which is position-dependent and cold regardless of the base value.
#: ``deep_frozen_ocean`` is the one that matters there: its base temperature
#: is 0.5, and a set built from the number alone would leave it out.
#:
#: **Altitude is not in here, and cannot be.** ``getTemperature`` drops with
#: height above y=80, so a mountain top in a temperate biome freezes too.
#: This set answers "does this biome freeze at ordinary ground level", which
#: is the question a warning wants; it is a floor, not a ceiling.
#:
#: Curated here rather than read from the jar at build time, for the reason
#: the colors are: Python is the source and the jar is the auditor.
#: The Unity app's biome palette generator fails on drift either way.
FREEZING_BIOMES = frozenset((
    "minecraft:deep_frozen_ocean",
    "minecraft:frozen_ocean",
    "minecraft:frozen_peaks",
    "minecraft:frozen_river",
    "minecraft:grove",
    "minecraft:ice_spikes",
    "minecraft:jagged_peaks",
    "minecraft:snowy_beach",
    "minecraft:snowy_plains",
    "minecraft:snowy_slopes",
    "minecraft:snowy_taiga",
))


def freezes(name: str) -> bool:
    """Will the game freeze this biome back after a thaw?

    Unknown ids answer False. A datapack biome could freeze and we have no
    way to know, but a warning that fires on everything teaches people to
    ignore it, and the thaw itself is unaffected either way.
    """
    return name in FREEZING_BIOMES


#: The villager type the game gives a villager standing in each biome.
#:
#: The game types a villager when it first spawns (``Villager.finalizeSpawn``
#: -> ``finalizeVillagerType``) by ``VillagerType.byBiome`` of the biome at
#: its feet: this table, which is the game's ``BY_BIOME``, and
#: :data:`VILLAGER_TYPE_DEFAULT` for every biome it does not name. A
#: villager written into a save carries its type in ``VillagerData`` and
#: the game takes that as final on load, so whatever writes one has to ask
#: this question itself -- or every villager in the snow is a plains one.
#:
#: Curated here for the reason :data:`FREEZING_BIOMES` is: Python is the
#: source and the jar is the auditor. ``tools/probe_block_shapes.py`` reads
#: ``BY_BIOME`` out of the game into ``fixtures/block_shapes.json``, and
#: the test suite holds this table to it entry for entry.
VILLAGER_TYPES = {
    "minecraft:badlands": "minecraft:desert",
    "minecraft:desert": "minecraft:desert",
    "minecraft:eroded_badlands": "minecraft:desert",
    "minecraft:wooded_badlands": "minecraft:desert",
    "minecraft:bamboo_jungle": "minecraft:jungle",
    "minecraft:jungle": "minecraft:jungle",
    "minecraft:sparse_jungle": "minecraft:jungle",
    "minecraft:savanna": "minecraft:savanna",
    "minecraft:savanna_plateau": "minecraft:savanna",
    "minecraft:windswept_savanna": "minecraft:savanna",
    "minecraft:deep_frozen_ocean": "minecraft:snow",
    "minecraft:frozen_ocean": "minecraft:snow",
    "minecraft:frozen_peaks": "minecraft:snow",
    "minecraft:frozen_river": "minecraft:snow",
    "minecraft:grove": "minecraft:snow",
    "minecraft:ice_spikes": "minecraft:snow",
    "minecraft:jagged_peaks": "minecraft:snow",
    "minecraft:snowy_beach": "minecraft:snow",
    "minecraft:snowy_plains": "minecraft:snow",
    "minecraft:snowy_slopes": "minecraft:snow",
    "minecraft:snowy_taiga": "minecraft:snow",
    "minecraft:mangrove_swamp": "minecraft:swamp",
    "minecraft:swamp": "minecraft:swamp",
    "minecraft:old_growth_pine_taiga": "minecraft:taiga",
    "minecraft:old_growth_spruce_taiga": "minecraft:taiga",
    "minecraft:taiga": "minecraft:taiga",
    "minecraft:windswept_forest": "minecraft:taiga",
    "minecraft:windswept_gravelly_hills": "minecraft:taiga",
    "minecraft:windswept_hills": "minecraft:taiga",
}

#: The type of a villager in any biome :data:`VILLAGER_TYPES` does not name
#: -- a forest, a river, a datapack's biome: ``VillagerData.DEFAULT_TYPE``,
#: which ``byBiome`` falls back on. It is also what the game loads a
#: ``VillagerData`` whose type it does not have as.
VILLAGER_TYPE_DEFAULT = "minecraft:plains"

#: Every villager type the game has: its ``villager_type`` registry.
VILLAGER_KINDS = frozenset((
    "minecraft:desert",
    "minecraft:jungle",
    "minecraft:plains",
    "minecraft:savanna",
    "minecraft:snow",
    "minecraft:swamp",
    "minecraft:taiga",
))


def villager_type(biome: str) -> str:
    """The villager type the game gives a villager standing in ``biome``:
    its entry in :data:`VILLAGER_TYPES`, else :data:`VILLAGER_TYPE_DEFAULT`.

    The fallback is the game's own (``byBiome``'s ``orElse``), not a guess,
    so an id this table does not know -- a datapack's biome -- is plains,
    exactly as the game would type it.
    """
    return VILLAGER_TYPES.get(biome, VILLAGER_TYPE_DEFAULT)
