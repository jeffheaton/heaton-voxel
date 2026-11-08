"""Two sample palettes, each read off a real build: the taste lives here.

    from palettes import LAB, PALETTES, ROMAN

    b = site.builder(palette=ROMAN)      # or Builder(save, palette=LAB)
    b.gable_roof(x0, z0, x1, z1, y, "x")  # granite tiles, sandstone gables

A :class:`~heaton_voxel.craft.style.Palette` is the materials one style
builds with, one for each role in
:data:`~heaton_voxel.craft.style.ROLES` -- the thirteen materials the
legacy writers (``level_pad``, ``road``, ``pave``, ``light_cells``,
``frame_walls``, ``floor``, ``gable_roof``, ``window``) read wherever their
caller leaves one out. ``heaton_voxel.craft`` ships no style of its own:
``Palette()`` is those writers' old defaults and nothing more. A style is a
choice, and choices belong to the build that made them, so the two here
are named for the builds they come from, each role copied from the script
that laid it. Where a build never used a role -- the Roman town lit no
floor, and glazed no window -- the role keeps the writers' default, and
says so.

The newer writers -- ``shell``'s :class:`~heaton_voxel.craft.build.Facade`,
:class:`~heaton_voxel.craft.build.Roof`, ``battlement``, a furniture
:class:`~heaton_voxel.craft.build.Piece` -- name their materials at the
call and never read a palette; pass them ``b.palette[role]``, as
``examples/craft_session.py`` does.

Every id here is one the game has, which ``tests/test_block_ids.py`` holds
``examples/`` to.
"""

from __future__ import annotations

import os
import sys

# Make the package importable when run from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heaton_voxel.craft.style import Material, Mix, Palette  # noqa: E402

__all__ = ["LAB", "PALETTES", "ROMAN"]


#: The Roman town (``tools/roman_city/``): brick and stucco under granite
#: tiles, travertine trim, basalt streets, a marble forum.
ROMAN = Palette(
    # The pads leveled for the districts kept the default grass, behind a
    # travertine retaining ring (jw_groundwork.py, ``pads``).
    retaining="minecraft:cut_sandstone",
    # The viae: big dark basalt pavers (common.py, VIA_MIX), one to a cell
    # as the streets laid them; and the forum floor (PLAZA_MIX) in patches,
    # since one to a cell left the real one static. Both are picked by
    # position, not drawn from a session's dice as the scripts drew them.
    road=Mix([("minecraft:smooth_basalt", 9), ("minecraft:basalt[axis=y]", 2),
              ("minecraft:deepslate_tiles", 2),
              ("minecraft:cracked_deepslate_tiles", 1),
              ("minecraft:cobbled_deepslate", 1)]),
    paving=Mix([("minecraft:polished_diorite", 6), ("minecraft:diorite", 3),
                ("minecraft:calcite", 3), ("minecraft:smooth_quartz", 2)],
               clump=2),
    # The curb course the streets laid between carriageway and walk.
    trim="minecraft:cut_sandstone",
    # An insula: a brick ground story, stucco over it, travertine quoins
    # up the corners (m4_districts.py) and spruce floors (jw_districts.py).
    plinth="minecraft:bricks",
    frame="minecraft:cut_sandstone",
    wall="minecraft:white_terracotta",
    floor="minecraft:spruce_planks",
    # Terracotta tiles, which granite reads as from above, with travertine
    # gables: common.py's gable_roof wrapper, three ids by hand, made one
    # palette.
    roof=Material("minecraft:granite"),
    gable="minecraft:smooth_sandstone",
    # No floor light and no glass in the town: the writers' glowstone and
    # plain pane stand.
)

#: The secret lab (``tools/secret_lab/``): white and light-gray concrete
#: over polished-deepslate plinths, deepslate floors, sea-lantern light,
#: light-blue glass.
LAB = Palette(
    # The campus pads: grass behind polished deepslate (m1_groundwork.py,
    # ``pads``).
    retaining="minecraft:polished_deepslate",
    # LAB_ROAD_MIX and the plaza's quartz floor (lab_common.py).
    road=Mix([("minecraft:polished_deepslate", 7),
              ("minecraft:deepslate_tiles", 4),
              ("minecraft:gray_concrete", 2)]),
    paving=Mix([("minecraft:smooth_quartz", 9),
                ("minecraft:white_concrete", 3),
                ("minecraft:chiseled_quartz_block", 1)], clump=2),
    # Polished deepslate curbs and plinths; the sea lanterns the avenue's
    # edges were lit with, flush in the paving.
    trim="minecraft:polished_deepslate",
    light="minecraft:sea_lantern",
    plinth="minecraft:polished_deepslate",
    # The skins: white concrete, light gray at the corners and in the bands
    # (the gatehouse towers), deepslate tile floors.
    frame="minecraft:light_gray_concrete",
    wall="minecraft:white_concrete",
    floor="minecraft:deepslate_tiles",
    # Polished deepslate stairs and slabs, the lab's own stair and roof
    # trim, with light-gray gable ends.
    roof=Material("minecraft:polished_deepslate"),
    gable="minecraft:light_gray_concrete",
    # The HQ's light-blue glass, as a pane.
    glass="minecraft:light_blue_stained_glass_pane",
)

#: Both, by the name ``examples/craft_session.py --palette`` takes.
PALETTES = {"roman": ROMAN, "lab": LAB}
