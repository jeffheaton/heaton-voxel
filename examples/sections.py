"""Four sample cross-sections, each read off a real build: the taste lives here.

    from sections import AQUEDUCT, AQUEDUCT_PIERS, HEDGE, LAB_PIERS, LAB_WALL
    from sections import ROMAN_WALL

    b.extrude(rectangle, ROMAN_WALL, window=geometry.WALL_WINDOW)
    b.extrude(rectangle, LAB_WALL, window=0, **LAB_PIERS)
    b.extrude([(ax, az), (ax, bz)], AQUEDUCT, follow=73, through=("path",),
              **AQUEDUCT_PIERS)
    b.extrude(border, HEDGE)              # a parterre's edge, on its lawn

A :class:`~heaton_voxel.craft.build.CrossSection` is what stands across
one cell of a run, drawn once as it stands on a run heading south -- its
``(across, up)`` cells, ``up=0`` the block stood on and ``across`` + to the
right of travel, the inside of a circuit walked clockwise -- and
:meth:`~heaton_voxel.craft.build.Builder.extrude` sweeps it along a line.
``heaton_voxel.craft`` ships no section of its own: a wall's courses, its
bands and its lip are a build's choices, so the four here are named for
the builds they come from, each cell copied from the script that laid it,
and each one's departures from that script said beside it.

* :data:`ROMAN_WALL` -- the JeffWorld Roman city's curtain wall
  (``tools/roman_city/jw_groundwork.py``, ``city_walls``): three thick,
  sandstone foundation, brick body with two relieving bands, a travertine
  walk with a brick parapet outside and a brick slab lip inside.
* :data:`LAB_WALL` and :data:`LAB_PILASTER` -- the secret lab's curtain
  wall (``tools/secret_lab/m1_groundwork.py``, ``_wall_column``): concrete
  skins over a polished deepslate plinth, a tinted-glass band, a gray deck
  walked between a blackstone parapet and a slate lip, and a lit pilaster
  every eighth cell.
* :data:`AQUEDUCT` and :data:`AQUEDUCT_PIER` -- the JeffWorld aqueduct's
  arcade (``jw_groundwork.py``, ``aqueduct``): a channel of water between
  two blocks of travertine on a three-deep deck, carried on sandstone piers
  two cells wide every six.
* :data:`HEDGE` -- the park's parterre hedge (``tools/park/build_park.py``,
  ``parterre``): two courses of oak leaves, flowering azalea in the top
  course. It stands on its centerline: beside a road, draw its cells at
  ``across = r.width // 2 + 1`` and follow the surface the road laid,
  ``[r.top(*cell) for cell in r.line]`` (API.md, "Extrusions").

Every id here is one the game has, which ``tests/test_block_ids.py`` holds
``examples/`` to, and ``tests/test_extrude.py`` holds each section to the
script it is named for and extrudes it over the modern fixture's own
ground.
"""

from __future__ import annotations

import os
import sys

# Make the package importable when run from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heaton_voxel.craft.build import CrossSection, Merlon  # noqa: E402
from heaton_voxel.craft.style import Mix  # noqa: E402

__all__ = ["AQUEDUCT", "AQUEDUCT_PIER", "AQUEDUCT_PIERS", "HEDGE",
           "LAB_PIERS", "LAB_PILASTER", "LAB_WALL", "ROMAN_WALL", "SECTIONS"]


# --------------------------------------------------------------------------
# The Roman city's curtain wall
# --------------------------------------------------------------------------

#: The Roman scripts' materials (``tools/roman_city/common.py``):
#: travertine is smooth sandstone, its rough course plain sandstone.
_TRAV = "minecraft:smooth_sandstone"
_TRAV_RAW = "minecraft:sandstone"
_BRICK = "minecraft:bricks"
_BRICK_SLAB = "minecraft:brick_slab[type=bottom]"


def _roman_wall() -> CrossSection:
    # jw_groundwork.py, city_walls: WALL_T = 3 columns, t=0 the outer face,
    # t=1 the walk's, t=2 the inner -- across -1, 0 and +1 on a circuit
    # walked clockwise -- each over the profile's height ``base``:
    #   sandstone from min(g - 2, base - 2) up to base, the foundation:
    #     rows -2..0 here, and the footing under them buried three blocks
    #     into the ground, which is the same min(g - 2, base - 2);
    #   brick from base + 1 to base + WALL_H (10), with the relieving bands
    #     of sandstone at base + 4 and base + 7, and the walk course of
    #     travertine at base + 10 on the middle column;
    #   on the outer face a brick parapet at base + 11 and a merlon at
    #     base + 12, on the inner a brick slab lip at base + 11 -- the lip a
    #     walker climbs each one-block rise of the walk by, in two halves.
    # The script set the merlon wherever the along coordinate was even
    # (``u % 2 == 0``); a Merlon counts its teeth per run from the run's own
    # corners instead (merlon_offsets, the castle's rule), so every corner
    # is a tooth -- on purpose, and not cell for cell what stands in
    # JeffWorld. The towers and the gatehouses are buildings, not section.
    cells = {}
    for across in (-1, 0, 1):
        for up in (-2, -1, 0, 4, 7):
            cells[(across, up)] = _TRAV_RAW
        for up in (1, 2, 3, 5, 6, 8, 9, 10):
            cells[(across, up)] = _BRICK
    cells[(0, 10)] = _TRAV
    cells[(-1, 11)] = _BRICK
    cells[(-1, 12)] = Merlon(_BRICK)
    cells[(1, 11)] = _BRICK_SLAB
    return CrossSection(cells, walk=[(0, 10)], footing=_TRAV_RAW, bury=3)


#: The JeffWorld Roman city's curtain wall. The script smoothed its ground
#: over five cells either side (``smoothed_line``), which is
#: ``window=geometry.WALL_WINDOW``.
ROMAN_WALL = _roman_wall()


# --------------------------------------------------------------------------
# The secret lab's curtain wall
# --------------------------------------------------------------------------

#: The lab's materials (``tools/secret_lab/lab_common.py``).
_PANEL = "minecraft:white_concrete"
_PANEL2 = "minecraft:light_gray_concrete"
_DECK = "minecraft:gray_concrete"
_SLATE = "minecraft:polished_deepslate"
_SLATE_SLAB = "minecraft:polished_deepslate_slab"
_BLACK_WALL = "minecraft:polished_blackstone_wall"
_TINTED = "minecraft:tinted_glass"
_BULB = "minecraft:waxed_copper_bulb[lit=true]"
_AIR = "minecraft:air"


def _lab_wall(*, pilaster: bool) -> CrossSection:
    # m1_groundwork.py, _wall_column, for the roles outer, core and inner --
    # across -1, 0 and +1 -- over ``top``, the deck, which _wall_tops put
    # seven over the highest ground of the two faces and the plane: so the
    # profile's height, ``up=0``, is top - 7, and
    #   the body, white concrete (light gray on a pilaster's outer face),
    #     from up=1 to up=4 (top - 6 .. top - 3);
    #   at up=5 (top - 2) tinted glass on the outer face, a lit copper bulb
    #     on a pilaster's, the body inside;
    #   polished deepslate at up=6, the gray deck at up=7, walked on the core;
    #   at up=8 the blackstone parapet outside and a slate slab lip inside;
    #   and cleared to air up to top + 8 over all three.
    # Under the body the script laid polished deepslate from two under the
    # lower of the ground and the plane less one up to the plane, 66, and
    # the body from there: an absolute datum this section does not keep. Its
    # footing is that deepslate, three blocks into the ground -- the
    # script's depth wherever the ground lies under the plane -- up to the
    # profile's height, where the body starts above it.
    cells = {}
    for across in (-1, 0, 1):
        body = _PANEL2 if pilaster and across == -1 else _PANEL
        for up in range(1, 5):
            cells[(across, up)] = body
        cells[(across, 5)] = body
        cells[(across, 6)] = _SLATE
        cells[(across, 7)] = _DECK
        for up in range(9, 16):
            cells[(across, up)] = _AIR
    cells[(-1, 5)] = _BULB if pilaster else _TINTED
    cells[(-1, 8)] = _BLACK_WALL
    cells[(0, 8)] = _AIR
    cells[(1, 8)] = _SLATE_SLAB
    return CrossSection(cells, walk=[(0, 7)], footing=_SLATE, bury=3)


#: The secret lab's curtain wall between its pilasters.
LAB_WALL = _lab_wall(pilaster=False)

#: The same wall at a pilaster: light gray up the outer face, the glass
#: band a lit copper bulb.
LAB_PILASTER = _lab_wall(pilaster=True)

#: Where the pilasters stand: ``z % 8 == 2`` on the west and east faces and
#: ``x % 8 == 2`` on the north and south, each the coordinate along the run
#: (perimeter_wall), which is extrude's congruence. The script's profile
#: had no window -- the highest of the two faces and the plane, plus seven,
#: clamped -- so extrude it with ``window=0``.
LAB_PIERS = {"pier": LAB_PILASTER, "pitch": 8, "phase": 2, "pier_width": 1}


# --------------------------------------------------------------------------
# The JeffWorld aqueduct
# --------------------------------------------------------------------------

_WATER = "minecraft:water[level=0]"


def _aqueduct(*, pier: bool) -> CrossSection:
    # jw_groundwork.py, aqueduct, the arcade from the springhouse north to
    # the city wall: three cells across, the channel's water at ``deck``
    # (73) between two of travertine -- the profile's height, so ``up=0``
    # is the water's -- over a deck of travertine three deep (deck - 3 ..
    # deck - 1). A pier row -- ``z % 6 in (0, 1)`` -- is sandstone from the
    # ground up to deck - 2 under travertine at deck - 1: its footing,
    # founded on the ground, under one course of the deck.
    cells = {(-1, 0): _TRAV, (0, 0): _WATER, (1, 0): _TRAV}
    for across in (-1, 0, 1):
        for up in ((-1,) if pier else (-3, -2, -1)):
            cells[(across, up)] = _TRAV
    if pier:
        return CrossSection(cells, footing=_TRAV_RAW)
    return CrossSection(cells)


#: The JeffWorld aqueduct's channel and deck, between its piers: water, so
#: laid level -- the script's ``follow=73``. Its ends are the caller's: the
#: JeffWorld channel's north end runs into the castellum it feeds, and its
#: south end, at z=495, stands open onto air (read 2026-10-22) -- a way out
#: :func:`~heaton_voxel.craft.survey.contained` names, which extrude
#: refuses unless the end is built first or its fall is given as
#: ``spill=``.
AQUEDUCT = _aqueduct(pier=False)

#: The aqueduct at a pier: the deck's top course on sandstone down to the
#: ground.
AQUEDUCT_PIER = _aqueduct(pier=True)

#: Where the piers stand: two cells every six, ``z % 6 in (0, 1)`` along the
#: run. The script skipped the piers over Jeff's path (z 469..476) and
#: carried the deck over it on one arch, which is ``through=`` the path's
#: reservation; the soffit stairs it sprang that arch from are not section.
AQUEDUCT_PIERS = {"pier": AQUEDUCT_PIER, "pitch": 6, "phase": 0,
                  "pier_width": 2}


# --------------------------------------------------------------------------
# The park's hedge
# --------------------------------------------------------------------------

#: The park's parterre hedge (build_park.py, ``parterre``, over
#: park_plan.py's HEDGE and HEDGE_FLOWER): persistent oak leaves in the cell
#: over the lawn and the one over that, the top course flowering azalea
#: wherever ``(x + z) % 5 == 0`` -- one cell in five, which a Mix keeps by
#: weight and picks by the cell, though not on the script's diagonal. A
#: hedge stands on the ground it is planted on, and leaves are no ground,
#: so the column's ground is left where it was.
HEDGE = CrossSection({
    (0, 1): "minecraft:oak_leaves[persistent=true]",
    (0, 2): Mix([("minecraft:oak_leaves[persistent=true]", 4),
                 ("minecraft:flowering_azalea_leaves[persistent=true]", 1)]),
})


#: Every sample, by name.
SECTIONS = {"roman_wall": ROMAN_WALL, "lab_wall": LAB_WALL,
            "aqueduct": AQUEDUCT, "hedge": HEDGE}
