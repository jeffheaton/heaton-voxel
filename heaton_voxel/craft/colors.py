"""Block and biome colors: what the pictures draw, in one public place.

The judged tier, and the plainest part of it: every value here is a choice,
not a fact. :class:`heaton_voxel.craft.render.ColorMap` draws blocks from
these tables, :mod:`heaton_voxel.craft.biomes` answers for the biome ones
under its older names, and two generators in the Unity app's own repo bake
both into its source as ``BlockPalette.generated.cs`` and
``BiomePalette.generated.cs``. They read only the public names below (and the
block one :func:`heaton_voxel.craft.blockinfo.cut_shapes`), never a private
table and never a retyped constant, so a color changes in exactly one place
and the app follows on the next run. The app's tests hold its generators to
that, and ``tests/test_architecture.py`` holds the names to existing.

This module imports nothing from the package, on purpose: it is a leaf of
literals, which render can stand on without the reverse ever being possible,
and which the repo split can vendor whole or export as data.

**Blocks.** A best-effort table, drifting as Minecraft adds blocks: 125
literal entries, which the sixteen dye families expand to 302 names, then an
ordered substring chain (:func:`block_heuristics`, first match wins), then
the wood fallback for anything named after a tree species no earlier rule
caught, then :data:`BLOCK_UNKNOWN`. Between the table and the chain, a stair,
slab or wall the game has is drawn as the block it is cut from, resolved down
the same ladder: :func:`heaton_voxel.craft.blockinfo.cut_shapes`, which needs
the game's block registry and so lives there, keeping this file literals.
That order is the resolution, in Python and in the generated C# alike;
reordering any of it recolors worlds. Air is
not in the table, because air is not a color: render draws it as the
background, and the app's mesher skips it before any color is asked for.

**Biomes.** These are deliberately not the game's own colors. Minecraft
ships a ``grass_color`` for only 6 of its 66 biomes; every other one derives
its grass from a colormap sampled by temperature and rainfall, and the
result is a set of near-identical greens across plains, forest, meadow,
jungle and savanna -- precisely the biomes a map most needs to separate. Sky
colors are worse: 17 distinct values across 66 biomes, all but one within a
few units of #78a7ff. Faithful colors would make a beautiful picture and a
useless legend.

So the biome palette is built for discrimination instead. Families take a
hue (ocean blue, forest green, dry sand, snow white) and members are
separated *within* the family by lightness, so the map survives
desaturation and color-vision differences rather than relying on hue alone.
The families are the game's own, lifted from its ``is_ocean`` /
``is_forest`` / ``is_taiga`` tags rather than invented here. The id list is
checked against the game rather than trusted: the biome generator's
``--jar`` mode reads ``data/minecraft/worldgen/biome/*.json`` out of an
installed client and fails on drift in either direction. It never reads a
color from there -- the jar is an auditor, not a source.

Every accessor hands out a fresh copy, so neither a :class:`ColorMap` nor a
generator can change the shared table under anyone else.
"""

from __future__ import annotations

__all__ = [
    "RGB",
    "BLOCK_UNKNOWN",
    "WOOD_FALLBACK",
    "WOOD_SPECIES",
    "block_palette",
    "block_heuristics",
    "BIOME_UNKNOWN",
    "BIOME_COLORS",
    "biome_palette",
    "biome_heuristics",
    "biome_color",
]

RGB = tuple[int, int, int]

# --------------------------------------------------------------------------
# Blocks
# --------------------------------------------------------------------------

#: A block neither the table nor any rule places: gray, so it reads as "no
#: answer" -- and :class:`ColorMap` counts it, so the table is easy to extend.
BLOCK_UNKNOWN: RGB = (155, 155, 155)

#: A wooden thing no earlier rule names: a fence, a trapdoor, a sign.
WOOD_FALLBACK: RGB = (150, 118, 70)

#: The tree species whose names catch fences, trapdoors, doors, signs and
#: anything else built from a given tree that no earlier rule matched. The
#: order is kept: the generated C# ``Woods`` array is emitted in it.
WOOD_SPECIES: tuple[str, ...] = (
    "oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove",
    "cherry", "pale_oak", "bamboo", "crimson", "warped")

# The 16 dye colors, reused by wool, concrete, terracotta, glass, beds...
_DYES = {
    "white": (233, 236, 236), "orange": (240, 118, 19), "magenta": (189, 68, 179),
    "light_blue": (58, 175, 217), "yellow": (248, 198, 39), "lime": (112, 185, 25),
    "pink": (237, 141, 172), "gray": (62, 68, 71), "light_gray": (142, 142, 134),
    "cyan": (21, 137, 145), "purple": (121, 42, 172), "blue": (53, 57, 157),
    "brown": (114, 71, 40), "green": (84, 109, 27), "red": (161, 39, 34),
    "black": (20, 21, 25),
}

_EXACT = {
    "minecraft:grass_block": (95, 159, 53), "minecraft:short_grass": (95, 159, 53),
    "minecraft:tall_grass": (95, 159, 53), "minecraft:fern": (88, 145, 50),
    "minecraft:dirt": (134, 96, 67), "minecraft:coarse_dirt": (119, 85, 59),
    "minecraft:rooted_dirt": (144, 103, 76), "minecraft:podzol": (91, 63, 26),
    "minecraft:mycelium": (111, 98, 100), "minecraft:farmland": (110, 76, 50),
    "minecraft:dirt_path": (148, 122, 66), "minecraft:mud": (60, 57, 61),
    "minecraft:stone": (125, 125, 125), "minecraft:cobblestone": (122, 122, 122),
    "minecraft:granite": (149, 103, 85), "minecraft:diorite": (188, 188, 190),
    "minecraft:andesite": (136, 136, 136), "minecraft:tuff": (108, 112, 103),
    "minecraft:calcite": (223, 224, 219), "minecraft:gravel": (131, 127, 126),
    "minecraft:deepslate": (77, 77, 82), "minecraft:bedrock": (85, 85, 85),
    "minecraft:sand": (219, 207, 163), "minecraft:red_sand": (190, 102, 33),
    "minecraft:sandstone": (216, 203, 155), "minecraft:clay": (160, 166, 179),
    "minecraft:water": (63, 118, 228), "minecraft:lava": (215, 100, 30),
    "minecraft:ice": (145, 183, 253), "minecraft:packed_ice": (141, 180, 250),
    "minecraft:blue_ice": (116, 167, 253), "minecraft:frosted_ice": (145, 183, 253),
    "minecraft:snow": (248, 252, 252), "minecraft:snow_block": (248, 252, 252),
    "minecraft:powder_snow": (245, 250, 250),
    "minecraft:netherrack": (111, 54, 52), "minecraft:soul_sand": (81, 62, 50),
    "minecraft:soul_soil": (75, 57, 46), "minecraft:basalt": (73, 72, 77),
    "minecraft:blackstone": (42, 35, 40), "minecraft:end_stone": (219, 222, 158),
    "minecraft:obsidian": (24, 20, 34), "minecraft:crying_obsidian": (35, 15, 61),
    "minecraft:moss_block": (89, 109, 45), "minecraft:moss_carpet": (89, 109, 45),
    "minecraft:seagrass": (55, 128, 40), "minecraft:kelp_plant": (44, 110, 35),
    "minecraft:lily_pad": (32, 128, 48), "minecraft:vine": (60, 110, 40),
    "minecraft:cactus": (85, 127, 43), "minecraft:bamboo": (128, 168, 51),
    "minecraft:sugar_cane": (148, 192, 101), "minecraft:pumpkin": (198, 118, 24),
    "minecraft:melon": (112, 148, 42), "minecraft:hay_block": (166, 136, 15),
    "minecraft:bricks": (150, 97, 83), "minecraft:glowstone": (248, 215, 115),
    "minecraft:sea_lantern": (172, 199, 190), "minecraft:magma_block": (142, 61, 30),
    "minecraft:gold_block": (246, 208, 61), "minecraft:iron_block": (220, 220, 220),
    "minecraft:iron_door": (194, 193, 193), "minecraft:ladder": (124, 96, 52),
    "minecraft:sponge": (195, 192, 74), "minecraft:wet_sponge": (171, 181, 70),
    "minecraft:rail": (128, 118, 100), "minecraft:powered_rail": (152, 124, 72),
    "minecraft:detector_rail": (130, 110, 100),
    "minecraft:activator_rail": (140, 100, 90),
    "minecraft:diamond_block": (98, 219, 214), "minecraft:emerald_block": (42, 203, 87),
    "minecraft:netherite_block": (66, 60, 63), "minecraft:copper_block": (192, 107, 79),
    "minecraft:redstone_block": (175, 24, 5), "minecraft:lapis_block": (30, 67, 140),
    "minecraft:coal_block": (25, 25, 25), "minecraft:quartz_block": (236, 233, 226),
    "minecraft:glass": (200, 224, 233), "minecraft:barrier": (255, 0, 255),
    "minecraft:dandelion": (221, 213, 60), "minecraft:poppy": (170, 45, 40),
    "minecraft:cornflower": (70, 106, 200), "minecraft:oxeye_daisy": (225, 228, 210),
    "minecraft:azure_bluet": (222, 232, 214), "minecraft:allium": (170, 145, 210),
    "minecraft:blue_orchid": (58, 168, 208), "minecraft:lily_of_the_valley": (224, 232, 220),
    "minecraft:bush": (86, 130, 48), "minecraft:firefly_bush": (92, 128, 55),
    "minecraft:dead_bush": (148, 116, 58), "minecraft:sweet_berry_bush": (72, 110, 52),
    "minecraft:bubble_column": (63, 118, 228), "minecraft:sea_pickle": (94, 120, 47),
    "minecraft:torchflower": (222, 160, 60), "minecraft:pitcher_plant": (150, 90, 180),
    "minecraft:pink_petals": (233, 170, 200), "minecraft:leaf_litter": (140, 110, 60),
    "minecraft:wildflowers": (150, 175, 70), "minecraft:lilac": (183, 155, 200),
    "minecraft:rose_bush": (150, 55, 50), "minecraft:peony": (200, 170, 200),
    "minecraft:large_fern": (88, 145, 50), "minecraft:kelp": (44, 110, 35),
    "minecraft:cave_vines": (110, 140, 50), "minecraft:cave_vines_plant": (110, 140, 50),
    "minecraft:glow_lichen": (112, 132, 116), "minecraft:hanging_roots": (150, 110, 80),
    "minecraft:big_dripleaf": (110, 150, 60), "minecraft:small_dripleaf": (110, 150, 60),
    "minecraft:spore_blossom": (210, 120, 180), "minecraft:cobweb": (220, 220, 220),
    "minecraft:torch": (240, 200, 100), "minecraft:lantern": (240, 200, 120),
    "minecraft:chest": (162, 130, 78), "minecraft:barrel": (137, 105, 60),
    "minecraft:crafting_table": (140, 105, 65), "minecraft:furnace": (110, 110, 110),
    "minecraft:sunflower": (238, 208, 60), "minecraft:wheat": (190, 178, 84),
    "minecraft:carrots": (110, 160, 60), "minecraft:potatoes": (110, 160, 60),
    "minecraft:beetroots": (130, 150, 70), "minecraft:jack_o_lantern": (214, 138, 40),
    "minecraft:campfire": (150, 100, 60), "minecraft:iron_bars": (150, 150, 150),
    "minecraft:composter": (130, 100, 60), "minecraft:wall_torch": (240, 200, 100),
    "minecraft:polished_granite": (154, 108, 90), "minecraft:polished_diorite": (192, 192, 194),
    "minecraft:polished_andesite": (140, 141, 138), "minecraft:smooth_stone": (158, 158, 158),
    "minecraft:stone_bricks": (122, 122, 122), "minecraft:mossy_cobblestone": (110, 122, 100),
}

# Checked in order; first substring that appears in the block name wins.
# A game stair or slab reaches these rules only as the block it is cut from,
# so ``_slab`` and ``_stairs`` catch a modded one, whose block is a guess.
_BLOCK_HEURISTICS = [
    ("_leaves", (58, 130, 41)), ("azalea", (98, 140, 51)),
    ("_log", (102, 81, 50)), ("_wood", (102, 81, 50)), ("_stem", (102, 81, 50)),
    ("_hyphae", (102, 81, 50)), ("_planks", (162, 130, 78)),
    ("_slab", (150, 150, 150)), ("_stairs", (150, 150, 150)),
    ("granite", (149, 103, 85)), ("diorite", (188, 188, 190)),
    ("andesite", (136, 136, 136)), ("_tulip", (200, 90, 90)),
    ("calcite", (223, 224, 219)), ("basalt", (73, 72, 77)),
    ("deepslate", (77, 77, 82)), ("blackstone", (42, 35, 40)),
    ("cobblestone", (122, 122, 122)), ("sandstone", (216, 203, 155)),
    ("_sand", (219, 207, 163)), ("gravel", (131, 127, 126)),
    ("prismarine", (99, 156, 151)), ("purpur", (169, 125, 169)),
    ("quartz", (236, 233, 226)), ("nether_brick", (44, 22, 26)),
    ("nether", (111, 54, 52)), ("mushroom", (203, 196, 186)),
    ("coral", (196, 84, 152)), ("amethyst", (133, 103, 191)),
    ("oxidized", (82, 162, 132)), ("weathered", (108, 153, 110)),
    ("exposed", (161, 125, 103)), ("copper", (192, 107, 79)),
    ("_ore", (125, 125, 125)),
    ("water", (63, 118, 228)), ("lava", (215, 100, 30)),
    ("ice", (145, 183, 253)), ("snow", (248, 252, 252)),
    ("glass", (200, 224, 233)), ("stone", (125, 125, 125)),
    ("brick", (150, 97, 83)), ("_flower", (200, 90, 120)),
    ("crop", (140, 170, 60)), ("grass", (95, 159, 53)),
    ("dirt", (134, 96, 67)), ("wool", (233, 236, 236)),
]

_DYE_FAMILIES = ("wool", "concrete", "concrete_powder", "terracotta", "glazed_terracotta",
                 "stained_glass", "stained_glass_pane", "carpet", "bed", "banner",
                 "wall_banner", "shulker_box", "candle")


def block_palette() -> dict[str, RGB]:
    """Name -> RGB for every block the table names exactly, with the dye
    families expanded and terracotta blended toward its clay. Air is not
    among them. A fresh dict on every call."""
    table = dict(_EXACT)
    for dye, rgb in _DYES.items():
        table[f"minecraft:{dye}_terracotta"] = _blend(rgb, (150, 92, 66), 0.55)
        for family in _DYE_FAMILIES:
            table.setdefault(f"minecraft:{dye}_{family}", rgb)
    table["minecraft:terracotta"] = (152, 94, 67)
    return table


def _blend(a, b, t):
    return tuple(int(round(a[i] * (1 - t) + b[i] * t)) for i in range(3))


def block_heuristics() -> list[tuple[str, RGB]]:
    """The ordered substring fallbacks, ``(needle, rgb)``: the first needle
    that appears in a name the table does not hold decides its color. A
    fresh list on every call."""
    return list(_BLOCK_HEURISTICS)


# --------------------------------------------------------------------------
# Biomes
# --------------------------------------------------------------------------

#: Anything the table and the heuristics both miss. Gray, so an unmapped biome
#: reads as "no answer" rather than as a plausible wrong one.
BIOME_UNKNOWN: RGB = (150, 150, 150)

#: The 66 vanilla biomes of Minecraft 26.2, keyed as the save stores them.
#: This table trails 26.3 by one: ``dappled_forest`` is new there and has
#: no color of its own yet -- the heuristics give it the plain forest's --
#: so the Unity app's biome palette generator refuses its own audit against
#: the 26.3 jar until somebody chooses one.
BIOME_COLORS: dict[str, RGB] = {
    # -- ocean: blue, darker with depth, cyan-ward with warmth ------------
    "minecraft:ocean": (59, 110, 165),
    "minecraft:deep_ocean": (39, 80, 127),
    "minecraft:cold_ocean": (74, 118, 158),
    "minecraft:deep_cold_ocean": (48, 88, 124),
    "minecraft:lukewarm_ocean": (52, 140, 178),
    "minecraft:deep_lukewarm_ocean": (36, 106, 140),
    "minecraft:warm_ocean": (47, 165, 184),
    "minecraft:frozen_ocean": (156, 196, 222),
    "minecraft:deep_frozen_ocean": (108, 152, 188),

    # -- river ------------------------------------------------------------
    "minecraft:river": (74, 134, 200),
    "minecraft:frozen_river": (169, 203, 230),

    # -- plains and savanna: yellow-greens to straw -----------------------
    "minecraft:plains": (127, 176, 60),
    "minecraft:sunflower_plains": (179, 195, 58),
    "minecraft:meadow": (140, 190, 104),
    "minecraft:savanna": (192, 180, 74),
    "minecraft:savanna_plateau": (170, 158, 66),
    "minecraft:windswept_savanna": (150, 148, 78),

    # -- forest: true greens, separated by lightness -----------------------
    "minecraft:forest": (62, 122, 52),
    "minecraft:flower_forest": (126, 196, 107),
    "minecraft:birch_forest": (108, 156, 92),
    "minecraft:old_growth_birch_forest": (88, 134, 76),
    "minecraft:dark_forest": (35, 70, 31),
    "minecraft:windswept_forest": (80, 116, 74),
    "minecraft:pale_garden": (140, 146, 133),

    # -- taiga: blue-greens ------------------------------------------------
    "minecraft:taiga": (46, 107, 82),
    "minecraft:snowy_taiga": (121, 167, 154),
    "minecraft:old_growth_pine_taiga": (54, 96, 72),
    "minecraft:old_growth_spruce_taiga": (44, 84, 64),
    "minecraft:grove": (150, 176, 168),

    # -- jungle: vivid greens ---------------------------------------------
    "minecraft:jungle": (46, 139, 58),
    "minecraft:bamboo_jungle": (111, 176, 58),
    "minecraft:sparse_jungle": (86, 152, 66),

    # -- dry: sand and rust ------------------------------------------------
    "minecraft:desert": (224, 198, 122),
    "minecraft:beach": (235, 219, 166),
    "minecraft:snowy_beach": (226, 230, 216),
    "minecraft:stony_shore": (146, 142, 132),
    "minecraft:badlands": (176, 106, 56),
    "minecraft:eroded_badlands": (198, 128, 70),
    "minecraft:wooded_badlands": (150, 108, 62),

    # -- snow and peaks: near-whites and pale blues ------------------------
    "minecraft:snowy_plains": (228, 238, 245),
    "minecraft:ice_spikes": (191, 227, 242),
    "minecraft:snowy_slopes": (206, 220, 232),
    "minecraft:frozen_peaks": (176, 204, 226),
    "minecraft:jagged_peaks": (200, 208, 216),
    "minecraft:stony_peaks": (154, 160, 166),
    "minecraft:windswept_hills": (122, 138, 128),
    "minecraft:windswept_gravelly_hills": (140, 140, 132),

    # -- swamp: murky olive ------------------------------------------------
    "minecraft:swamp": (76, 96, 70),
    "minecraft:mangrove_swamp": (62, 107, 88),

    # -- caves: earth and dark, so they never read as surface --------------
    "minecraft:dripstone_caves": (138, 107, 82),
    "minecraft:lush_caves": (79, 154, 74),
    "minecraft:deep_dark": (30, 36, 48),
    "minecraft:sulfur_caves": (186, 172, 70),

    # -- reserved hues nothing else uses -----------------------------------
    "minecraft:mushroom_fields": (184, 124, 176),
    "minecraft:cherry_grove": (232, 160, 192),

    # -- nether: reds, with teal for the warped --------------------------
    "minecraft:nether_wastes": (122, 42, 36),
    "minecraft:crimson_forest": (150, 44, 42),
    "minecraft:warped_forest": (30, 107, 107),
    "minecraft:soul_sand_valley": (92, 76, 66),
    "minecraft:basalt_deltas": (66, 60, 62),

    # -- end: pale ochres ---------------------------------------------------
    "minecraft:the_end": (200, 194, 154),
    "minecraft:end_highlands": (186, 178, 138),
    "minecraft:end_midlands": (172, 164, 126),
    "minecraft:end_barrens": (150, 144, 112),
    "minecraft:small_end_islands": (130, 126, 100),

    # -- and the absence of a world ----------------------------------------
    "minecraft:the_void": (20, 22, 26),
}

# Ordered substring fallbacks for anything not in the table -- a datapack or
# mod biome, most likely. Order matters and is not alphabetical: ``deep_ocean``
# must beat ``ocean``, ``dark_forest`` must beat ``forest``, and ``end`` has to
# come late or it swallows ``windswept`` and ``sparse_jungle``.
_BIOME_HEURISTICS = [
    ("deep_ocean", (39, 80, 127)), ("ocean", (59, 110, 165)),
    ("river", (74, 134, 200)),
    ("mangrove", (62, 107, 88)), ("swamp", (76, 96, 70)),
    ("badlands", (176, 106, 56)), ("desert", (224, 198, 122)),
    ("beach", (235, 219, 166)), ("shore", (146, 142, 132)),
    ("mushroom", (184, 124, 176)), ("cherry", (232, 160, 192)),
    ("bamboo", (111, 176, 58)), ("jungle", (46, 139, 58)),
    ("savanna", (192, 180, 74)),
    ("dark_forest", (35, 70, 31)),
    ("pine", (54, 96, 72)), ("spruce", (44, 84, 64)),
    ("taiga", (46, 107, 82)), ("birch", (108, 156, 92)),
    ("forest", (62, 122, 52)),
    ("peaks", (200, 208, 216)), ("slopes", (206, 220, 232)),
    ("grove", (150, 176, 168)), ("meadow", (140, 190, 104)),
    ("snowy", (228, 238, 245)), ("frozen", (176, 204, 226)),
    ("ice", (191, 227, 242)),
    ("windswept", (122, 138, 128)), ("hills", (122, 138, 128)),
    ("plateau", (170, 158, 66)),
    ("caves", (138, 107, 82)), ("cave", (138, 107, 82)),
    ("nether", (122, 42, 36)), ("crimson", (150, 44, 42)),
    ("warped", (30, 107, 107)), ("soul", (92, 76, 66)),
    ("basalt", (66, 60, 62)),
    ("end", (200, 194, 154)), ("void", (20, 22, 26)),
    ("plains", (127, 176, 60)), ("garden", (140, 146, 133)),
]


def biome_palette() -> dict[str, RGB]:
    """The exact biome table, as the generator wants it. A fresh dict."""
    return dict(BIOME_COLORS)


def biome_heuristics() -> list[tuple[str, RGB]]:
    """The ordered biome fallbacks, as the generator wants them. A fresh
    list."""
    return list(_BIOME_HEURISTICS)


def biome_color(name: str) -> RGB:
    """Resolve one biome id the way the generated C# will.

    Exact table, then ordered substring, then unknown -- the same order as
    :meth:`heaton_voxel.craft.render.ColorMap.__call__`, because a different
    order gives different colors for the same world.
    """
    found = BIOME_COLORS.get(name)
    if found is not None:
        return found
    for needle, rgb in _BIOME_HEURISTICS:
        if needle in name:
            return rgb
    return BIOME_UNKNOWN
