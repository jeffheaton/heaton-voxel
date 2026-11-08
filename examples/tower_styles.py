"""A handful of tower families, for ``design_tower``.

A style is taste, so these live here and not in the package, as the sample
palettes do. Each is a family: its schemes are the colors a building of it may
come in, and the rest is what ``design_tower`` rolls for each lot. The list in
:data:`STYLES` is what ``examples/skyline.py`` draws from.

Each family is set by how its walls are made. ``GLASS`` is a curtain wall on
thin mullions, ``BRICK`` windows punched in masonry with stone trim, ``DECO``
tall piers with the glass set a block behind them, stepping back to a crown,
``RIBBON`` long strips of window between solid courses, and ``FRAME`` a grid of
concrete with every floor showing. ``SETBACK`` is the Palace Hotel's look read
as a family rather than one building: a white frame, pale glass, three tiers
and a glass crown.
"""

from __future__ import annotations

from heaton_voxel.craft import Material, Scheme, TowerStyle


def _pane(color: str) -> str:
    return f"minecraft:{color}_stained_glass_pane"


GLASS = TowerStyle(
    "glass",
    (Scheme("minecraft:black_concrete", _pane("gray"), tint=_pane("black"),
            trim="minecraft:polished_blackstone_bricks",
            lamp="minecraft:sea_lantern"),
     Scheme("minecraft:gray_concrete", _pane("cyan"), tint=_pane("light_blue"),
            trim="minecraft:polished_andesite", lamp="minecraft:sea_lantern"),
     Scheme("minecraft:light_gray_concrete", _pane("light_blue"),
            tint=_pane("cyan"), trim="minecraft:white_concrete"),
     Scheme("minecraft:waxed_oxidized_copper", _pane("green"),
            tint=_pane("cyan"), trim="minecraft:waxed_oxidized_cut_copper",
            lamp="minecraft:sea_lantern")),
    window="curtain", bay=3, lobby=2, tiers=(1, 1, 2), step=3,
    roofs=("flat", "flat", "crown"), shapes=("box", "box", "ell", "twin"))

BRICK = TowerStyle(
    "brick",
    (Scheme("minecraft:bricks", _pane("gray"), tint=_pane("light_gray"),
            trim="minecraft:smooth_stone", spandrel="minecraft:bricks",
            roof=Material("minecraft:deepslate_tiles")),
     Scheme("minecraft:mud_bricks", _pane("brown"), tint=_pane("gray"),
            trim="minecraft:smooth_sandstone",
            roof=Material("minecraft:dark_prismarine")),
     Scheme("minecraft:red_nether_bricks", _pane("light_gray"),
            tint=_pane("gray"), trim="minecraft:polished_diorite",
            roof=Material("minecraft:deepslate_tiles")),
     Scheme("minecraft:terracotta", _pane("gray"), tint=_pane("brown"),
            trim="minecraft:white_terracotta",
            roof=Material("minecraft:bricks"))),
    window="punched", bay=2, pitch=5, tiers=(1, 2), step=2,
    roofs=("flat", "hip"), shapes=("box", "ell", "you", "tee"))

DECO = TowerStyle(
    "deco",
    (Scheme("minecraft:smooth_sandstone", _pane("gray"), tint=_pane("brown"),
            trim="minecraft:cut_sandstone", spandrel="minecraft:gold_block"),
     Scheme("minecraft:polished_diorite", _pane("black"), tint=_pane("gray"),
            trim="minecraft:quartz_bricks",
            spandrel="minecraft:polished_blackstone"),
     Scheme("minecraft:polished_granite", _pane("gray"), tint=_pane("black"),
            trim="minecraft:smooth_red_sandstone",
            spandrel="minecraft:polished_blackstone")),
    window="piers", bay=2, lobby=2, tiers=(3, 4), step=2,
    roofs=("crown", "stepped"), shapes=("box", "box", "tee", "cross"))

RIBBON = TowerStyle(
    "ribbon",
    (Scheme("minecraft:white_concrete", _pane("light_blue"),
            tint=_pane("light_gray"), spandrel="minecraft:white_concrete"),
     Scheme("minecraft:light_gray_concrete", _pane("gray"),
            tint=_pane("black"), spandrel="minecraft:light_gray_concrete",
            trim="minecraft:gray_concrete"),
     Scheme("minecraft:cyan_terracotta", _pane("cyan"),
            spandrel="minecraft:white_terracotta",
            trim="minecraft:white_terracotta")),
    window="ribbon", bay=3, tiers=(1, 2), step=4,
    roofs=("flat",), shapes=("box", "ell", "twin"))

FRAME = TowerStyle(
    "frame",
    (Scheme("minecraft:stone_bricks", _pane("gray"), tint=_pane("light_gray"),
            trim="minecraft:polished_andesite"),
     Scheme("minecraft:polished_andesite", _pane("blue"), tint=_pane("cyan"),
            trim="minecraft:smooth_stone"),
     Scheme("minecraft:deepslate_bricks", _pane("light_gray"),
            tint=_pane("white"), trim="minecraft:polished_deepslate",
            lamp="minecraft:sea_lantern")),
    window="grid", bay=3, tiers=(1, 2, 3), step=3,
    roofs=("flat", "stepped"), shapes=("box", "tee", "you"))

SETBACK = TowerStyle(
    "setback",
    (Scheme("minecraft:white_concrete", _pane("light_blue"),
            tint="minecraft:light_blue_stained_glass",
            trim="minecraft:white_concrete",
            stair="minecraft:quartz_stairs", landing="minecraft:quartz_block"),
     Scheme("minecraft:smooth_quartz", _pane("cyan"), tint=_pane("light_blue"),
            trim="minecraft:quartz_bricks",
            stair="minecraft:quartz_stairs", landing="minecraft:quartz_block")),
    window="grid", bay=3, lobby=2, tiers=(3,), step=2, roofs=("crown",),
    shapes=("box",))

#: Every family, what a district draws from.
STYLES = (GLASS, BRICK, DECO, RIBBON, FRAME, SETBACK)

#: Who stands where in a city: glass and stone downtown, brick at its edges.
ZONES = {"core": (SETBACK, DECO, GLASS),
         "mid": (GLASS, FRAME, RIBBON, DECO),
         "edge": (BRICK, FRAME, RIBBON)}
