"""The judged tier: construction and pictures, above the editing library.

``heaton_voxel`` proper is deterministic — for every question it answers
(what bytes encode this chunk, what state a block update derives) there is
one correct answer, the C# twin must agree to the bit, and the conformance
corpus enforces it. Everything in ``heaton_voxel.craft`` sits above that
line: material palettes, road mixes, roof shapes, block colors — game
knowledge and taste, where the answers are choices. Choices get renders
and judgment, not corpus artifacts, which is why this tier is Python-only
by policy and deliberately outside the C# port (see PORTING.md).

The dependency arrow points down and only down: ``craft`` builds on
``place``, ``world`` and ``nbt``; nothing below ever imports from here,
and the top-level ``heaton_voxel`` namespace re-exports none of this —
``tests/test_architecture.py`` fails the build if either rule breaks.
Import the tier explicitly, the way its taste deserves to be visible:

    from heaton_voxel.craft import Builder, ROAD_MIX
    from heaton_voxel.craft import render_area, render_slice

A session runs in three movements, and there is a module for each:
``survey`` reads the ground before you touch it, ``build`` changes it,
``render`` shows you what you did — with ``worksite`` holding the
seatbelt around all three. ``survey.reachable`` is the fourth question a
render cannot answer: can you actually walk what you drew, and can you
get back out again; ``survey.spawnable`` and ``light.plan_lights`` are
the fifth, will anything spawn in it at night, and where do the lamps go
so nothing does -- to a level named for the game's rule it meets
(``NO_SPAWNS``, ``NO_NEW_SNOW_OR_ICE``, ``MELT_ICE``, ``MELT_SNOW``).
``survey.water_body``, ``contained`` and ``starved`` are the water's: where
a body of water is, whether a plan keeps it where it is, and which flows a
plan cuts off -- and ``Builder.basin`` lays water that stays, its floor
shelving down from its shore. ``Builder.approach`` lays the steps or the
ramp a walker climbs to a pad or a door, walked both ways without a jump
before it is written, and ``Builder.extrude`` sweeps a ``CrossSection`` --
a curtain wall and its merlons, a hedge, an aqueduct's channel on its
piers -- along a line, riding the ground with every turn laid level, walks
its wall walk both ways first, and lays a channel level with its water
held in. A furnishing's water -- a tub's, a fountain's -- is held the same
way, each piece's ``spill`` the only way out it may take.
``blockinfo`` is what any of them asks about a single block: whether the
game has it (``check_state`` refuses an id the game would load as air, and
a property or value it would drop for the block's default), and what kind
of thing it is -- a tree, ground cover, natural ground, a stair cut from
which block. ``geometry`` is the shape of things
with no world in it: the one compass, round towers and their walls by a
named rounding, the cells a line crosses, and the :class:`Frame` a
component is turned and placed by. ``style`` is materials as a builder
names them: a :class:`Mix` picked by position (the same block for a cell
whoever asks, whenever, and the one a Builder writes there), a
:class:`Material` family checked against the
game's blocks, and the :class:`Palette`, a closed set of roles naming
the materials the legacy writers read wherever their caller leaves one
out -- from ``Builder(palette=)``, which is their old defaults when it is
not given.

Every write a ``Builder`` makes is strict: ``check_state`` runs on each
state a call plans, before its first write, and one
:class:`~heaton_voxel.craft.errors.IllegalBlock` names every state the
game does not have -- a retired name copied off an old building
included. ``Builder(save, allow=("create:",))`` passes a modded
namespace, or an exact id, unchecked.

Inside a worksite, build with ``site.builder()``: it refuses, before a
block moves, any write the worksite's restore could not take back and any
that would touch a protected zone, and ``site.diff(c1, c2)`` then says
what the build actually changed, block by block against the snapshot.
"""

from .build import (
    HEAD_ROOM,
    LANE_MIX,
    PLAZA_MIX,
    ROAD_MIX,
    SHORE_SLOPE,
    ApproachResult,
    Band,
    BasinResult,
    BlendResult,
    BuildFinish,
    Builder,
    CityBlock,
    CityLot,
    CityPlan,
    CityResult,
    Core,
    CrossSection,
    Crown,
    design_tower,
    Doors,
    Elevation,
    Fitted,
    ExtrudeResult,
    Facade,
    Footprint,
    FlightResult,
    floors_behind,
    Framed,
    FurnishResult,
    Glazing,
    GradeResult,
    Journal,
    Ladder,
    Layers,
    LineLampsResult,
    Massing,
    Merlon,
    Opening,
    PaveResult,
    Piece,
    plan_city,
    Railway,
    RailwayResult,
    Rhythm,
    Scheme,
    RoadResult,
    RoadRule,
    Roof,
    RoofResult,
    ScatterResult,
    ShaftResult,
    SiteMap,
    SpiralResult,
    SpiralStair,
    StairRule,
    Street,
    StreetNetwork,
    StreetSection,
    StreetsResult,
    Stretch,
    StraightStair,
    SwitchbackResult,
    SwitchbackStair,
    Tier,
    TowerDesign,
    TowerResult,
    TowerStyle,
    Tree,
    Vault,
    VaultResult,
    Wall,
    WallResult,
    Wings,
    arch_profile,
    merlon_offsets,
    pointed_arch_profile,
    rise_of,
)
from .audit import Audit, audit
from .blockinfo import (
    check_state,
    falls,
    form_of,
    holds_water,
    is_air,
    is_block,
    is_natural_ground,
    is_tree,
    is_vegetation,
    material_of,
    subsoil_of,
)
from .errors import IllegalBlock, NoSurface, Refused
from .geometry import Frame
from .light import (
    MELT_ICE,
    MELT_SNOW,
    NO_NEW_SNOW_OR_ICE,
    NO_SPAWNS,
    DimensionLightLevel,
    LightField,
    LightLevel,
    LightPlan,
    durable_emission,
    light_dampening,
    light_emission,
    light_occlusion,
    plan_lights,
    simulate_block_light,
)
from .render import (
    ColorMap,
    render_area,
    render_elevation,
    render_plan,
    render_slice,
)
from .style import FROM_PALETTE, Material, Mix, Palette
from .survey import (
    AUTO_STEP,
    JUMP_HEIGHT,
    KIND_GLYPHS,
    MONSTER_SPAWN_LIGHT,
    REACH_GLYPHS,
    Containment,
    Leak,
    ReachMap,
    SpawnCell,
    StepSpace,
    SurfaceProbe,
    SurveyMap,
    Unsupported,
    WaterBody,
    classify,
    contained,
    iron_door_opens,
    reachable,
    spawnable,
    starved,
    step_kind,
    survey,
    unsupported,
    walk_surface,
    water_body,
)
from .worksite import (
    CellChange,
    CheckResult,
    RetireResult,
    RollbackResult,
    SiteDiff,
    Worksite,
    WorksiteSummary,
    list_worksites,
)

__all__ = [
    "Audit",
    "audit",
    "Builder",
    "BuildFinish",
    "Framed",
    "Journal",
    "LineLampsResult",
    "PaveResult",
    "Railway",
    "RailwayResult",
    "SpiralStair",
    "StraightStair",
    "FlightResult",
    "SpiralResult",
    "ShaftResult",
    "StairRule",
    "SwitchbackStair",
    "SwitchbackResult",
    "HEAD_ROOM",
    "Vault",
    "VaultResult",
    "Roof",
    "RoofResult",
    "Wings",
    "arch_profile",
    "pointed_arch_profile",
    "rise_of",
    "merlon_offsets",
    "Wall",
    "Facade",
    "Footprint",
    "Opening",
    "Elevation",
    "Fitted",
    "Massing",
    "Tier",
    "Core",
    "BlendResult",
    "CityBlock",
    "CityLot",
    "CityPlan",
    "CityResult",
    "plan_city",
    "SiteMap",
    "Crown",
    "Street",
    "StreetNetwork",
    "StreetSection",
    "StreetsResult",
    "design_tower",
    "Scheme",
    "TowerDesign",
    "TowerStyle",
    "Ladder",
    "TowerResult",
    "Band",
    "Rhythm",
    "Glazing",
    "Layers",
    "Doors",
    "floors_behind",
    "WallResult",
    "Piece",
    "FurnishResult",
    "GradeResult",
    "BasinResult",
    "SHORE_SLOPE",
    "Tree",
    "ScatterResult",
    "ApproachResult",
    "CrossSection",
    "Merlon",
    "ExtrudeResult",
    "RoadResult",
    "RoadRule",
    "Stretch",
    "ROAD_MIX",
    "LANE_MIX",
    "PLAZA_MIX",
    "Mix",
    "Material",
    "Palette",
    "FROM_PALETTE",
    "ColorMap",
    "render_area",
    "render_plan",
    "render_slice",
    "LightField",
    "LightPlan",
    "simulate_block_light",
    "plan_lights",
    "light_emission",
    "light_dampening",
    "light_occlusion",
    "durable_emission",
    "LightLevel",
    "DimensionLightLevel",
    "NO_SPAWNS",
    "NO_NEW_SNOW_OR_ICE",
    "MELT_ICE",
    "MELT_SNOW",
    "survey",
    "walk_surface",
    "classify",
    "SurveyMap",
    "SurfaceProbe",
    "KIND_GLYPHS",
    "reachable",
    "unsupported",
    "Unsupported",
    "spawnable",
    "SpawnCell",
    "MONSTER_SPAWN_LIGHT",
    "iron_door_opens",
    "step_kind",
    "ReachMap",
    "StepSpace",
    "REACH_GLYPHS",
    "AUTO_STEP",
    "JUMP_HEIGHT",
    "water_body",
    "WaterBody",
    "contained",
    "Containment",
    "Leak",
    "starved",
    "Worksite",
    "CheckResult",
    "SiteDiff",
    "CellChange",
    "RetireResult",
    "RollbackResult",
    "WorksiteSummary",
    "list_worksites",
    "check_state",
    "is_block",
    "is_air",
    "falls",
    "holds_water",
    "is_tree",
    "is_vegetation",
    "subsoil_of",
    "is_natural_ground",
    "material_of",
    "form_of",
    "Refused",
    "NoSurface",
    "IllegalBlock",
    "Frame",
]
