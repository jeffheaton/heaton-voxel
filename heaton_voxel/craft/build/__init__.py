"""Construction helpers: terraces, roads, shells, roofs, furniture — and the aftercare.

This is the toolkit the town and castle builds kept reinventing, distilled.
Like the rest of :mod:`heaton_voxel.craft` it sits *above* the format layers
and is imported explicitly, never re-exported: material palettes, roof
shapes and what counts as vegetation are game knowledge and taste, not
format knowledge.

    from heaton_voxel.craft import Builder, CrossSection, Merlon, Tree, geometry

    b = Builder(save, seed=7)
    b.clear_vegetation((x0, z0), (x1, z1))
    b.level_pad(x0, z0, x1, z1, 64, name="bakery")    # a grade, registered
    b.grade(geometry.disc(6, center=(cx, cz)), 63, drain=True)
    b.basin(geometry.disc(9, center=(px, pz)), 62, floor="minecraft:sand",
            lining="minecraft:stone")              # a pond, shelving
    b.scatter(geometry.disc(20, center=(gx, gz)), Tree("oak"),
              spacing=6)                           # a wood, keyed per column
    b.approach((dx, dz + 6), (dx, 64, dz), kind="steps",
               material="minecraft:stone_bricks")  # up to a doorstep, no jump
    b.extrude([(x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0)],
              CrossSection({(0, 0): "minecraft:stone_bricks",
                            (0, 1): Merlon("minecraft:stone_bricks")},
                           footing="minecraft:cobblestone"))  # a crenellated
                                                  # wall riding the ground
    b.frame_walls(x0 + 1, z0 + 1, x1 - 1, z1 - 1, 64, 4)
    b.gable_roof(x0 + 1, z0 + 1, x1 - 1, z1 - 1, 68, "x")
    b.place(door_x, 65, z0 + 1, "minecraft:oak_door[facing=north]")
    b.road([(ax, az), (bx, bz)], width=3)
    print(b.finish())     # connections, heightmaps, POI, lighting — all of it

Three design points carried over from builds that went wrong:

* **Roads cannot cross pads or reservations.** Every
  :meth:`Builder.level_pad` registers its rectangle, :meth:`Builder.reserve`
  registers one without leveling it, and :meth:`Builder.road` refuses a
  polyline whose swath enters one, naming every one entered —
  a lane once carved a channel through three buildings, invisible from
  above because the roofs stayed on. :meth:`Builder.railway` refuses the
  same wherever its plan writes, :meth:`Builder.approach` across its run,
  :meth:`Builder.extrude` wherever its band lays a cell or a seal, and
  :meth:`Builder.line_lamps` stands no lamp post in one, nor
  :meth:`Builder.scatter` a plant. Name a pad or
  reservation in ``through=`` to sanction a crossing (a street through a
  plaza is a choice; through a bedroom is a bug). A keep-out lasts a
  session, so :meth:`Builder.grade`, :meth:`Builder.level_pad` and
  :meth:`Builder.road` also read what stands in their cells and refuse what
  somebody built unless ``overwrite=True``.
* **Every write is a block the game has.** An id it lacks, or a property
  or a value its block does not take, is refused before the call writes
  anything (:class:`~heaton_voxel.craft.errors.IllegalBlock`, naming every
  one) -- the game would load it as air, as the secret lab's hanging
  charge lost its ``chain`` to the rename, and say so only in its log.
  ``Builder(allow=)`` passes a modded block.
* **finish() is the whole litany.** Neighbor-derived states settled
  (:meth:`~heaton_voxel.world.MinecraftSave.connect_blocks`), heightmaps
  rebuilt, POI records invalidated so beds and workstations register, and
  lighting handed back — over what the builder wrote, chunk by chunk, from
  the :class:`Journal` it keeps of every write. Forgetting one of these is
  the natural failure mode of building by hand; here it is one call, and
  ``save.save()`` remains the only thing that touches disk.

Where the defaults came from
----------------------------

Each default below is one a real build used, or a choice made while
building with one, kept so that a call naming nothing builds what it
always built. None of them is the game's rule, and each is meant to be
replaced; this is where each was taken from, so that nobody mistakes one
for a law. The table is the materials and the worked examples' numbers,
not every default there is: a minimum a writer refuses to go below -- a
railway's ``clearance`` of 2, a rider's head being in the block above,
or :data:`HEAD_ROOM`'s three treads -- is the game's, and its refusal
says why.

+-----------------------------------+-----------------------------------------+
| default                           | where it came from                      |
+===================================+=========================================+
| ``level_pad``'s grass and         | the construction kit the first town and |
| stone-brick ring; ``road``'s      | castle builds were distilled into       |
| :data:`ROAD_MIX`; ``pave``'s      | (e107f54, 2026-08-14): the medieval     |
| :data:`PLAZA_MIX`;                | town's materials, :data:`LANE_MIX`      |
| ``frame_walls``' cobblestone      | among them                              |
| plinth, stripped-spruce corners   |                                         |
| and white terracotta; ``floor``'s |                                         |
| spruce; ``gable_roof``'s dark     |                                         |
| oak; ``window``'s glass pane      |                                         |
+-----------------------------------+-----------------------------------------+
| ``pave``'s and ``light_cells``'   | the plaza floors first lit to stay bare |
| glowstone                         | (48873df, 2026-08-20)                   |
+-----------------------------------+-----------------------------------------+
| ``fence_rect``'s and              | the same construction kit (e107f54)     |
| ``ornamental_tree``'s oak         |                                         |
+-----------------------------------+-----------------------------------------+
| ``level_pad``'s six blocks of     | the same kit's ``level_pad`` (e107f54); |
| headroom, which ``grade`` takes   | ``grade`` is that writer generalized,   |
| as its ``clearance`` too          | and its dirt ``subsoil`` the same kit's |
|                                   | fill                                    |
+-----------------------------------+-----------------------------------------+
| ``road``'s smoothing window,      | the same kit's road (e107f54), which    |
| ``geometry.ROAD_WINDOW``, three   | ran its one-block clamp three times --  |
| cells either side; and            | one pass and two that never moved a     |
| ``geometry.WALL_WINDOW``, five    | cell; the wall's five is the JeffWorld  |
|                                   | Roman wall's ``smoothed_line``          |
|                                   | (``tools/roman_city/jw_groundwork.py``, |
|                                   | 4ed2fcf, 2026-08-17)                    |
+-----------------------------------+-----------------------------------------+
| ``extrude``'s ``window`` and      | road's, ``geometry.ROAD_WINDOW`` and    |
| ``max_step=1``                    | its one-block clamp, so a wall laid     |
|                                   | beside a road rides the ground as the   |
|                                   | road does                               |
+-----------------------------------+-----------------------------------------+
| ``RoadRule(shallow_fill=3,        | ``examples/road.py`` (67647ea,          |
| tunnel_from=7, skylight=3)``:     | 2026-08-11): its ``SHALLOW_FILL``, the  |
| the fill under a road's bed that  | air an embankment may swallow, its      |
| is still grade, the ground over   | ``TUNNEL_FROM``, and the three-cell gap |
| it that makes a cut a tunnel, the | ``smooth_kinds`` closes; ``road``       |
| gap between tunnels closed; and   | refuses a deeper fill unless            |
| so where ``road`` sees a dam      | ``allow_dam=True``                      |
+-----------------------------------+-----------------------------------------+
| ``basin``'s ``slope``,            | the big lake around Jeff's island,      |
| :data:`SHORE_SLOPE`: 0.75 of a    | re-cut 2026-08-27                       |
| block of depth per block from the | (``tools/lake/lakeplan.py``,            |
| shore                             | ``SLOPE``): the slope it chose to leave |
|                                   | room for its ripple. Any slope up to 1  |
|                                   | is cliff-free and taken; past it is     |
|                                   | geometry, not taste, and refused.       |
|                                   | ``basin`` has no material default: its  |
|                                   | ``floor`` is always named               |
+-----------------------------------+-----------------------------------------+
| ``approach``'s ``fill``: the      | the park's two gates                    |
| material's block under the rows   | (``tools/park/build_park.py``,          |
| it raises                         | ``approach``), whose raised cells are   |
|                                   | stone bricks. ``approach`` has no       |
|                                   | material default: its ``kind`` and      |
|                                   | ``material`` are always named           |
+-----------------------------------+-----------------------------------------+
| ``Vault(half_span=7, pitch=1)``   | the vault over JeffWorld's great hall   |
| and its cobblestone slab, block   | (x=-63, z=72..121), read off it by      |
| and stairs                        | ``tools/nave/read_nave.py``: profile    |
|                                   | (0..7), depth (1,1,1,2,1,1,1,4)         |
|                                   | (9d30260, 2026-08-31; re-read           |
|                                   | 2026-10-21)                             |
+-----------------------------------+-----------------------------------------+
| ``arcade``'s                      | the hall's own stone; ``pitch=6`` is a  |
| ``pier=quartz_block`` and         | choice, the demonstration hall of       |
| ``pitch=6``                       | ``tools/nave/build_hall.py``: the real  |
|                                   | hall's arcade lines are solid quartz    |
|                                   | walls and its cross walls stand         |
|                                   | irregularly, so it has no bay pitch to  |
|                                   | measure                                 |
+-----------------------------------+-----------------------------------------+
| ``hang_lamps(drop=3)`` and its    | the hall's lamps are glowstone, hung by |
| glowstone; ``chain=iron_chain``   | the ceiling they hang from: over the    |
|                                   | vault's run (z=72..121, read            |
|                                   | 2026-10-21) mostly three links under    |
|                                   | its y=76 ceilings, one under y=83, two  |
|                                   | from the aisle vault and five from the  |
|                                   | ridge, so ``drop=3`` is a choice among  |
|                                   | them (the nave row of the demonstration |
|                                   | hall in ``tools/nave/build_hall.py``);  |
|                                   | the chain is the library's, since the   |
|                                   | hall hangs every lamp on nether brick   |
|                                   | fences                                  |
+-----------------------------------+-----------------------------------------+
| ``Railway(boost_every=26)``, its  | the Stronghold link, JeffWorld's z=13   |
| cobblestone bed and redstone      | line (laid 2026-10-10, made a rule in   |
| power                             | 829bbf9): boosters on redstone blocks   |
|                                   | every 26 cells from x=-909 to -753. 26  |
|                                   | is that build's choice; no game rule    |
|                                   | picks it                                |
+-----------------------------------+-----------------------------------------+
| ``SpiralStair``'s oak stairs and  | the stair Jeff began by hand up         |
| planks                            | JeffWorld's glass tower at (-171, -107) |
|                                   | (9d30260); ``SwitchbackStair`` and      |
|                                   | ``StraightStair`` took the same oak,    |
|                                   | though the yellow tower the switchback  |
|                                   | was read off is birch                   |
+-----------------------------------+-----------------------------------------+
| ``doorway``'s oak door, stone     | the oak door and left hinge are the     |
| button and left hinge             | suburban house's doorway helper         |
|                                   | (``tools/suburban_house/furniture.py``, |
|                                   | the house finished 2026-08-27, kept in  |
|                                   | 3c71810); the stone button is a choice, |
|                                   | made with the iron door's buttons       |
|                                   | (6037887, 2026-10-20)                   |
+-----------------------------------+-----------------------------------------+
| ``battlement(height=1)``          | every crown built so far: the castle    |
|                                   | example's curtain walls and towers and  |
|                                   | the Roman city's field towers stand     |
|                                   | one-block teeth on their course;        |
|                                   | ``battlement`` has no material default, |
|                                   | its block is always named               |
+-----------------------------------+-----------------------------------------+
| ``Roof(pitch=2, crown="slab")``   | ``gable_roof``'s own pitch and slab     |
|                                   | ridge, from the same construction kit   |
|                                   | (e107f54), which the Roman city's hip   |
|                                   | and tholos cone and the castle          |
|                                   | example's spire share; ``overhang=0``   |
|                                   | because the outline is the walls'       |
|                                   | (``gable_roof`` passes 1). ``Roof`` has |
|                                   | no material default: its ``block``,     |
|                                   | ``slab`` and ``stair`` are always named |
+-----------------------------------+-----------------------------------------+
| ``Opening.arch``'s flat head when | the castle example's arched windows and |
| no ``profile`` is given: a        | keep entrance (``examples/castle.py``,  |
| springer in each end cell of the  | ``keep_windows``, ``keep_entrance``),   |
| head row                          | whose springers face their jambs        |
+-----------------------------------+-----------------------------------------+
| ``Facade(phase=None)`` counting   | a choice, after ``arcade``'s pier on    |
| from each wall's low end, and     | the first column of its run -- though   |
| ``Wall.ring`` giving its corners  | ``arcade`` counts from whichever end is |
| to the north and south walls      | named first; the lab's buildings passed |
|                                   | ``phase=0``. ``Wall``, ``Facade`` and   |
|                                   | ``Opening`` have no material default:   |
|                                   | every block is named                    |
+-----------------------------------+-----------------------------------------+
| ``Piece.lamp``'s one-block        | the suburban house's nightstand, a      |
| ``post``, and ``Piece.pendant``'s | lantern on one block of stripped oak,   |
| lamp hung straight from its       | and its ceiling lights, a lantern hung  |
| ceiling (``links=0``)             | straight from the ceiling               |
|                                   | (``tools/suburban_house/furniture.py``) |
|                                   | -- and the Roman city's candelabrum, a  |
|                                   | torch on one sandstone wall             |
|                                   | (``tools/roman_city/common.py``).       |
|                                   | ``Piece`` has no material default:      |
|                                   | every block is named                    |
+-----------------------------------+-----------------------------------------+

The first two rows are the eight legacy writers' materials, and they are
the roles of :data:`~heaton_voxel.craft.style.DEFAULT_PALETTE`: each of
those writers reads a material its caller leaves out (``FROM_PALETTE``)
from the Builder's ``palette=``, so a style is one
:class:`~heaton_voxel.craft.style.Palette` rather than an argument at every
call, and a Builder with no palette reproduces these defaults exactly,
byte for byte. The rest are literal defaults, no palette's roles,
replaced by passing the argument: a rule object -- a ``Vault``, a
``Railway``, a stair -- is made without a Builder, so it has no palette to
read.
"""

from __future__ import annotations

from ._common import (  # noqa: F401
    _COMPASS,
    _continuation,
    _empty,
    _finished,
    _head_room,
    _HEADINGS,
    _MELTABLE,
    _Mix,
    _occupancy,
    _passable,
    _RING,
    _STAIR_STATE,
    _state,
    _state_matches,
    AIR,
    HEAD_ROOM,
)
from ..light import (  # noqa: F401
    DimensionLightLevel,
    LightLevel,
    MELT_ICE,
    MELT_SNOW,
    NO_NEW_SNOW_OR_ICE,
    NO_SPAWNS,
)
from .lighting import (  # noqa: F401
    _hangs_lamp,
    _sits_in_a_floor,
    LIGHT_SOURCES,
    LineLampsResult,
    MELT_LIGHT,
    PaveResult,
    PLAZA_MIX,
)
from .site import (  # noqa: F401
    _clears,
    _NETHER_TREES,
    BRIDGE,
    CUT,
    DIRT,
    GRADE,
    GradeResult,
    GRASS,
    LANE_MIX,
    ROAD_MIX,
    RoadResult,
    RoadRule,
    Stretch,
    TUNNEL,
)
from .vault import (  # noqa: F401
    _close_column,
    _column_key,
    _family,
    _fill,
    _hangs_from,
    _in_family,
    _lay_column,
    _piece,
    _same_course,
    _SLAB_STATE,
    _TOWARD,
    _vault_column,
    _vault_common,
    _vault_materials,
    _vault_profile,
    _vault_run,
    _VAULT_SAMPLES,
    _vault_section,
    _VAULT_STEP,
    arch_profile,
    pointed_arch_profile,
    Vault,
    VaultResult,
)
from .walls import (  # noqa: F401
    Facade,
    Footprint,
    merlon_offsets,
    Opening,
    Wall,
    WallResult,
)
from .elevation import (  # noqa: F401
    Band,
    Doors,
    Elevation,
    Fitted,
    floors_behind,
    Glazing,
    Layers,
    Rhythm,
)
from .crown import Crown  # noqa: F401
from .massing import Core, Massing, Tier, TowerResult  # noqa: F401
from .design import (design_tower, ROOFS, Scheme, SHAPES,  # noqa: F401
                     TowerDesign, TowerStyle, WINDOWS)
from .streets import (CLEARANCE, LaidStreet, RUN, Street,  # noqa: F401
                      StreetNetwork, StreetSection, StreetsResult)
from .site import BlendResult  # noqa: F401
from .city import (CityBlock, CityLot, CityPlan, CityResult,  # noqa: F401
                   plan_city, SiteMap)
from .furniture import FurnishResult, Piece  # noqa: F401
from .water import BasinResult, SHORE_SLOPE  # noqa: F401
from .planting import ScatterResult, SKIP_REASONS, Tree  # noqa: F401
from .approach import ApproachResult  # noqa: F401
from .extrude import CrossSection, ExtrudeResult, Merlon  # noqa: F401
from .roof import (  # noqa: F401
    _hip_facing,
    HOLLOW,
    rise_of,
    Roof,
    RoofResult,
    Wings,
)
from .stairs import (  # noqa: F401
    _bay_hint,
    _by_level,
    _corner_words,
    _ENDS,
    _longest_run,
    _name,
    _read_landing,
    _read_materials,
    _read_stringer,
    _shaft_reach,
    _TURNING,
    _unread_hint,
    _vote,
    FlightResult,
    Ladder,
    ShaftResult,
    SpiralResult,
    SpiralStair,
    StairRule,
    StraightStair,
    SwitchbackResult,
    SwitchbackStair,
)
from .railway import (  # noqa: F401
    _holds_rail_top,
    _NO_RAIL,
    _NO_RAIL_SUFFIX,
    _OPPOSITE,
    _RAIL_EXITS,
    _rail_meets,
    _RAIL_ON_PASSABLE,
    _rail_shape,
    _RAILS,
    _settled,
    _toward,
    _wet,
    Railway,
    RailwayResult,
)
from .journal import Journal  # noqa: F401
from .framed import _frame_op, _Turned, Framed  # noqa: F401
from .builder import (  # noqa: F401
    _AUDIT_REACH,
    _CONNECT_REACH,
    _merge,
    Builder,
    BuildFinish,
)

__all__ = ["Builder", "BuildFinish", "FlightResult", "Framed", "Journal",
           "LineLampsResult", "PaveResult",
           "Railway",
           "RailwayResult", "StraightStair",
           "ShaftResult",
           "SpiralResult", "SpiralStair", "StairRule", "SwitchbackResult",
           "HEAD_ROOM",
           "SwitchbackStair", "Vault", "VaultResult",
           "Roof", "RoofResult", "Wings", "rise_of",
           "arch_profile", "pointed_arch_profile", "merlon_offsets",
           "Wall", "Facade", "Footprint", "Opening", "WallResult",
           "Elevation", "Fitted", "Band", "Rhythm", "Glazing", "Layers",
           "Doors",
           "floors_behind",
           "Massing", "Tier", "Core", "Crown", "Ladder", "TowerResult",
           "design_tower", "TowerStyle", "TowerDesign", "Scheme", "WINDOWS",
           "ROOFS", "SHAPES",
           "Street", "StreetNetwork", "StreetSection", "StreetsResult",
           "LaidStreet",
           "plan_city", "SiteMap", "CityPlan", "CityBlock", "CityLot",
           "CityResult", "BlendResult",
           "Piece", "FurnishResult",
           "GradeResult", "RoadResult", "RoadRule", "Stretch",
           "BasinResult", "SHORE_SLOPE",
           "Tree", "ScatterResult", "ApproachResult",
           "CrossSection", "Merlon", "ExtrudeResult",
           "GRADE", "CUT", "TUNNEL", "BRIDGE",
           "ROAD_MIX", "LANE_MIX",
           "PLAZA_MIX", "LIGHT_SOURCES", "MELT_LIGHT",
           "LightLevel", "DimensionLightLevel", "NO_SPAWNS",
           "NO_NEW_SNOW_OR_ICE", "MELT_ICE", "MELT_SNOW"]
