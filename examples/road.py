"""Build a level stone road ahead of the player: cuttings, tunnels, bridges and all.

    python examples/road.py "~/Library/Application Support/minecraft/saves/my_world" 200
    python examples/road.py <world> 500          # the longest road allowed
    python examples/road.py <world> 120 --dry-run

The road starts one block in front of where the player logged out, runs the
way they were facing (snapped to north/south/east/west), and stays at the
level of the block they were standing on for its whole length -- three blocks
of stone bricks wide, one down the player's own line and one on each side,
with lamp posts at regular intervals down alternating sides.

The terrain adapts to the road, not the road to the terrain:

* Trees are not terrain. The survey walks down through trunks, leaves and
  other plant matter to the first real block before deciding anything, and
  whatever stood in the corridor is felled to its canopy -- a forest is
  cleared through, never tunneled through.
* Ground **above** road level is cut through: the road keeps two level
  shoulder blocks on each side, and the cut walls taper upward at 45 degrees
  until they meet the hillside.
* Hills more than 6 blocks above the road are **tunneled** instead, three
  blocks of headroom, lined in stone bricks and lit with torches.
* Shallow dips -- three or fewer blocks of air under the road bed -- are
  **embanked**: stone under the deck, dirt shoulders leveling out to the
  ground, reaching at most six blocks each side.
* Deeper drops, and any water at road level, get a **bridge**: railed deck a
  little wider than the road, stone piers down to the floor. Over water the
  deck **arches**: it climbs on stone stairs, crosses with at least three
  blocks of air between the water and its underside -- room for a boat --
  and steps back down to road level on the far side. A dry ravine gets a
  level bridge; there is nothing to sail under it.
* A single span longer than 100 blocks ends the road: the route is
  **truncated at the near shore** during planning, before any block has been
  placed, and the report says how long the impossible span would have been.

The whole route is surveyed first (heightmap lookups, never block scans);
building starts only if every station is on generated ground. Afterwards
heightmaps are refreshed and every touched chunk is handed back to the
game's light engine, so the lamps actually light and mobs do not spawn on
ground that no longer exists.

Close the world in Minecraft first, for the reasons in gold_box.py.
"""

from __future__ import annotations

import argparse
import os
import sys

# Make the modules importable when run from anywhere; castle for its Mason.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from castle import Mason  # noqa: E402  (the site-works crew: tally, grouped fills, heightmap refresh, relight)
from heaton_voxel.nbt import NBTError, world_is_open  # noqa: E402
from heaton_voxel.world import Dimension, MinecraftSave  # noqa: E402

ROAD = "minecraft:stone_bricks"
RAIL = "minecraft:stone_brick_wall"
FILL_STONE = "minecraft:stone"
DIRT = "minecraft:dirt"
GRASS = "minecraft:grass_block"
AIR = "minecraft:air"
POST = "minecraft:spruce_fence"
LANTERN = "minecraft:lantern"

MAX_LENGTH = 500
MIN_LENGTH = 8
MAX_BRIDGE = 100  # longest single span; a route needing more stops short
BOAT_ROOM = 3  # air blocks kept between water and a bridge's underside
TUNNEL_FROM = 7  # ground this far above the road is bored, not cut
SHALLOW_FILL = 3  # air blocks under the bed an embankment may swallow
EMBANKMENT_REACH = 6  # how far sideways the dirt may level out
HEADROOM = 3  # clear blocks above the deck, everywhere
LAMP_EVERY = 10
TUNNEL_LIGHT_EVERY = 5
PIER_EVERY = 8

# Station kinds, in the order a route report lists them.
GRADE, CUT, TUNNEL, BRIDGE = "grade", "cut", "tunnel", "bridge"

_CARDINALS = (
    ("south", 0, 1),
    ("west", -1, 0),
    ("north", 0, -1),
    ("east", 1, 0),
)


def facing_cardinal(yaw: float) -> tuple[str, int, int]:
    """Snap a player yaw to the nearest cardinal direction.

    The game's yaw runs 0 = south, 90 = west, 180 = north, 270 = east, and
    arrives unnormalized -- logged-out players carry values like -437.6.
    """
    return _CARDINALS[int(((yaw + 45) % 360) // 90)]


# Plant matter the survey must not mistake for ground, and the road must
# fell rather than bore through. Snow is here too: a snow-capped surface is
# one block of fluff over the real one.
_VEGETATION_SUFFIXES = ("_log", "_wood", "_stem", "_hyphae", "_leaves")
_VEGETATION = {
    "minecraft:bamboo",
    "minecraft:cactus",
    "minecraft:sugar_cane",
    "minecraft:vine",
    "minecraft:mushroom_stem",
    "minecraft:brown_mushroom_block",
    "minecraft:red_mushroom_block",
    "minecraft:azalea",
    "minecraft:flowering_azalea",
    "minecraft:moss_carpet",
    "minecraft:pale_moss_carpet",
    "minecraft:snow",
}


def is_vegetation(name: str) -> bool:
    return name.endswith(_VEGETATION_SUFFIXES) or name in _VEGETATION


def true_ground(dim: Dimension, x: int, z: int) -> tuple[int | None, int | None]:
    """The ground under any vegetation, and the vegetation's top.

    Every heightmap counts trees: ``OCEAN_FLOOR`` and ``WORLD_SURFACE``
    include leaves, ``MOTION_BLOCKING_NO_LEAVES`` still counts the trunk --
    so a forest reads as ground eight blocks tall, and a road would tunnel
    through a tree. Where the surface block is plant matter, this walks
    down through it to the first real block.

    Returns ``(ground, plant_top)``; ``plant_top`` is ``None`` for a bare
    column, else the height everything planted there reaches (canopy
    included) so the feller knows how high to swing.
    """
    surface = dim.surface_y(x, z, "MOTION_BLOCKING_NO_LEAVES")
    if surface is None:
        return None, None
    try:
        accessor = dim.blocks(x >> 4, z >> 4)
    except NBTError:
        return None, None
    if accessor is None:
        return None, None
    lx, lz = x & 15, z & 15
    y = surface
    floor = max(accessor.min_y, surface - 40)
    while y > floor and is_vegetation(accessor.get(lx, y, lz).name):
        y -= 1
    if y == surface:
        return surface, None
    canopy = dim.surface_y(x, z, "WORLD_SURFACE")
    return y, canopy if canopy is not None else surface


class Station:
    """One block-row of the route: where it is and what the ground does."""

    __slots__ = (
        "index", "x", "z", "kind", "solid_hi", "solid_lo", "water_top",
        "deck_y", "clear_top",
    )

    def __init__(self, index, x, z, kind, solid_hi, solid_lo, water_top=None,
                 clear_top=None):
        self.index = index
        self.x = x
        self.z = z
        self.kind = kind
        self.solid_hi = solid_hi  # highest ground under the three road columns
        self.solid_lo = solid_lo  # lowest
        self.water_top = water_top  # highest fluid surface, or None if dry
        self.clear_top = clear_top  # top of vegetation to fell, or None
        self.deck_y = None  # set by plan_arches; road level except on arches


def classify(road_y: int, solid_hi: int, solid_lo: int, wet: bool) -> str:
    """What one station needs, from the ground under its three road columns.

    ``wet`` means water stands at road level in at least one column. The
    order matters: water forces a bridge before anything else is
    considered, and a hill wins over a dip so a knife-edge gets cut rather
    than bridged.
    """
    if wet:
        return BRIDGE
    if solid_hi > road_y:
        return TUNNEL if solid_hi - road_y >= TUNNEL_FROM else CUT
    if road_y - 1 - solid_lo > SHALLOW_FILL:
        return BRIDGE
    return GRADE


def smooth_kinds(kinds: list[str]) -> list[str]:
    """Close pinprick gaps in tunnel roofs.

    A hill whose crest dips briefly below the tunnel threshold would open a
    one- or two-block skylight mid-bore. Any non-tunnel run of three or
    fewer stations with tunnel on both sides becomes tunnel too.
    """
    out = list(kinds)
    i = 0
    while i < len(out):
        if out[i] != TUNNEL:
            i += 1
            continue
        j = i + 1
        while j < len(out) and out[j] == TUNNEL:
            j += 1
        # A short non-tunnel gap followed by more tunnel: close it.
        k = j
        while k < len(out) and out[k] != TUNNEL and k - j < 3:
            k += 1
        if k < len(out) and out[k] == TUNNEL and k > j:
            for g in range(j, k):
                out[g] = TUNNEL
        i = j
    return out


def longest_run(kinds: list[str], kind: str) -> int:
    """The longest consecutive run of one kind."""
    best = run = 0
    for k in kinds:
        run = run + 1 if k == kind else 0
        best = max(best, run)
    return best


def survey_route(
    dim: Dimension, start_x: int, start_z: int, dx: int, dz: int,
    road_y: int, length: int,
) -> tuple[list[Station], list[int]]:
    """Walk the route on heightmaps alone; nothing is touched.

    Returns the stations and the indices of any whose road columns sit on
    ground the world has not generated (or has no surface for) -- the
    caller refuses on those rather than building blind.
    """
    px, pz = -dz, dx  # the perpendicular, one side of the road
    stations: list[Station] = []
    unknown: list[int] = []
    for i in range(length):
        cx, cz = start_x + dx * i, start_z + dz * i
        solid_hi = solid_lo = water_top = clear_top = None
        wet = False
        missing = False
        for o in (-1, 0, 1):
            x, z = cx + px * o, cz + pz * o
            ground, plant_top = true_ground(dim, x, z)
            if ground is None:
                missing = True
                continue
            fluid_floor = dim.surface_y(x, z, "OCEAN_FLOOR")
            top = dim.surface_y(x, z, "MOTION_BLOCKING")
            solid_hi = ground if solid_hi is None else max(solid_hi, ground)
            solid_lo = ground if solid_lo is None else min(solid_lo, ground)
            if plant_top is not None:
                clear_top = plant_top if clear_top is None else max(clear_top, plant_top)
            if top is not None and fluid_floor is not None and top > fluid_floor:
                water_top = top if water_top is None else max(water_top, top)
                if top >= road_y - 2:
                    wet = True  # standing fluid at road level
        if missing or solid_hi is None:
            unknown.append(i)
            stations.append(Station(i, cx, cz, GRADE, road_y, road_y))
            continue
        kind = classify(road_y, solid_hi, solid_lo, wet)
        stations.append(
            Station(i, cx, cz, kind, solid_hi, solid_lo, water_top, clear_top)
        )

    kinds = smooth_kinds([s.kind for s in stations])
    for station, kind in zip(stations, kinds):
        station.kind = kind
    return stations, unknown


def usable_length(kinds: list[str], max_bridge: int = MAX_BRIDGE) -> int:
    """How far the road may run before an over-long span stops it.

    A bridge run longer than ``max_bridge`` cannot be built, so the road
    ends at that run's near shore: everything before it is usable, nothing
    past it is reachable. A route with no such span is usable in full.
    """
    run_start = None
    for i, kind in enumerate(kinds):
        if kind != BRIDGE:
            run_start = None
            continue
        if run_start is None:
            run_start = i
        if i - run_start + 1 > max_bridge:
            return run_start
    return len(kinds)


def plan_arches(stations: list[Station], road_y: int) -> None:
    """Give every station a deck height; bridge runs over water get arches.

    A boat needs ``BOAT_ROOM`` blocks of air under a bridge, so a deck near
    the water climbs: one block per station from each end of the run, level
    across the top, down again -- stone stairs make the ramps walkable. A
    run over dry ground (or already high enough) stays at road level, and a
    run too short for the full rise gets as close as its length allows.
    """
    for station in stations:
        station.deck_y = road_y
    i = 0
    while i < len(stations):
        if stations[i].kind != BRIDGE:
            i += 1
            continue
        j = i
        while j < len(stations) and stations[j].kind == BRIDGE:
            j += 1
        run = stations[i:j]
        need = max(
            [road_y]
            + [s.water_top + BOAT_ROOM + 1 for s in run if s.water_top is not None]
        )
        peak = min(need, road_y + (len(run) - 1) // 2)
        for k, station in enumerate(run):
            station.deck_y = min(peak, road_y + k, road_y + len(run) - 1 - k)
        i = j


class RoadBuilder:
    """Lays one surveyed route through a Mason."""

    def __init__(self, mason: Mason, dim: Dimension, road_y: int, dx: int, dz: int):
        self.m = mason
        self.dim = dim
        self.road_y = road_y
        self.dx, self.dz = dx, dz
        self.px, self.pz = -dz, dx  # positive offsets go to this side
        self.lamps = 0

    def at(self, station: Station, offset: int) -> tuple[int, int]:
        return station.x + self.px * offset, station.z + self.pz * offset

    def ground(self, x: int, z: int) -> int | None:
        """The real ground at a column: under trees, above the seabed."""
        return true_ground(self.dim, x, z)[0]

    def water_floor(self, x: int, z: int) -> int | None:
        """Where a pier lands: the solid floor, through any water."""
        return self.dim.surface_y(x, z, "OCEAN_FLOOR")

    # -- stations ----------------------------------------------------------

    def build(self, stations: list[Station]) -> None:
        plan_arches(stations, self.road_y)  # idempotent; callers may pre-plan
        previous_deck = self.road_y
        for station in stations:
            if station.kind == BRIDGE:
                self.lay_bridge(station, previous_deck)
            else:
                handler = {
                    GRADE: self.lay_grade,
                    CUT: self.lay_cut,
                    TUNNEL: self.lay_tunnel,
                }[station.kind]
                handler(station)
            previous_deck = station.deck_y if station.deck_y is not None else self.road_y
            if station.kind != TUNNEL and station.index % LAMP_EVERY == 4:
                self.lamp(station, 2 if (station.index // LAMP_EVERY) % 2 else -2)

    def deck(self, station: Station) -> None:
        y = self.road_y
        for o in (-1, 0, 1):
            x, z = self.at(station, o)
            self.m.set(x, y, z, ROAD)

    def clear_above(self, x: int, z: int, from_y: int, to_y: int) -> None:
        if to_y >= from_y:
            self.m.fill((x, from_y, z), (x, to_y, z), AIR)

    def lay_grade(self, station: Station) -> None:
        """Flat-enough ground: deck, stone underpinning, dirt shoulders."""
        y = self.road_y
        self.deck(station)
        # Fell anything standing over the lane, canopy and all.
        top = max(y + 8, (station.clear_top or 0) + 1)
        spans = []
        for o in (-1, 0, 1):
            x, z = self.at(station, o)
            self.clear_above(x, z, y + 1, top)
            solid = self.ground(x, z)
            if solid is not None and solid < y - 1:
                spans.append((x, z, solid + 1, y - 1))  # the shallow fill
        self.m.fill_columns(spans, FILL_STONE)
        self.shoulders(station)

    def lay_cut(self, station: Station) -> None:
        """Through a hill: same as grade, but the shoulders do the digging."""
        self.lay_grade(station)

    def shoulders(self, station: Station) -> None:
        """Level verges beside the road, then 45-degree tapers beyond.

        Offsets 2 and 3 are held level with the road. Past them each step
        outward may rise one block (a cutting's wall) or fall one block (an
        embankment stepping down); dirt fill reaches at most
        ``EMBANKMENT_REACH`` from the road's edge, a cut face as far as it
        must. Columns already inside the band are left alone.
        """
        y = self.road_y
        dirt, grass_tops = [], []
        for side in (-1, 1):
            for k in range(0, TUNNEL_FROM + 3):
                o = (2 + k) * side
                x, z = self.at(station, o)
                solid, plant = true_ground(self.dim, x, z)
                rise = max(0, k - 1)  # offsets 2 and 3 stay level
                if solid is None:
                    continue
                felled = (plant + 1) if plant is not None else 0
                if solid > y + rise:
                    # Cut down to the taper line and green the new face.
                    self.clear_above(x, z, y + rise + 1, max(solid + 5, felled))
                    grass_tops.append((x, z, y + rise, y + rise))
                elif solid < y - rise and k < EMBANKMENT_REACH:
                    # Fill up to the taper line; grass over dirt.
                    target = y - rise
                    if target - 1 >= solid + 1:
                        dirt.append((x, z, solid + 1, target - 1))
                    grass_tops.append((x, z, target, target))
                    self.clear_above(x, z, target + 1, max(y + 6, felled))
                else:
                    # Inside the band, or past the dirt's reach: keep the
                    # verge clear where it matters and stop this side.
                    if k <= 1:
                        self.clear_above(x, z, solid + 1, max(y + 6, felled))
                    break  # the taper has met the ground on this side
        self.m.fill_columns(dirt, DIRT)
        self.m.fill_columns(grass_tops, GRASS)

    def lay_tunnel(self, station: Station) -> None:
        """A bore: 3 wide, 3 high, lined, floored with the road itself."""
        y = self.road_y
        self.deck(station)
        for o in (-1, 0, 1):
            x, z = self.at(station, o)
            self.clear_above(x, z, y + 1, y + HEADROOM)
        for o in (-2, 2):  # walls
            x, z = self.at(station, o)
            self.m.fill((x, y, z), (x, y + HEADROOM + 1, z), ROAD)
        for o in (-1, 0, 1):  # roof
            x, z = self.at(station, o)
            self.m.set(x, y + HEADROOM + 1, z, ROAD)
        if station.index % TUNNEL_LIGHT_EVERY == 2:
            side = 1 if (station.index // TUNNEL_LIGHT_EVERY) % 2 else -1
            x, z = self.at(station, side)
            toward = self.wall_torch_facing(-side)
            self.m.set(x, y + 2, z, f"minecraft:wall_torch[facing={toward}]")

    def wall_torch_facing(self, toward_center: int) -> str:
        """The facing for a torch on a tunnel wall, pointing into the bore."""
        dx, dz = self.px * toward_center, self.pz * toward_center
        if dx:
            return "east" if dx > 0 else "west"
        return "south" if dz > 0 else "north"

    def travel_facing(self, forward: bool) -> str:
        """The stair facing for a ramp you climb traveling with the road
        (or against it, for the way back down)."""
        dx = self.dx if forward else -self.dx
        dz = self.dz if forward else -self.dz
        if dx:
            return "east" if dx > 0 else "west"
        return "south" if dz > 0 else "north"

    def lay_bridge(self, station: Station, previous_deck: int) -> None:
        """A railed deck over water or a drop, with piers at intervals.

        The deck follows the station's planned height: where it climbs or
        falls against its neighbor, the surface is stairs -- facing the
        ascent -- so the arch is walkable in both directions.
        """
        y = station.deck_y if station.deck_y is not None else self.road_y
        if y == previous_deck:
            surface = ROAD
        else:
            facing = self.travel_facing(forward=y > previous_deck)
            surface = f"minecraft:stone_brick_stairs[facing={facing},half=bottom]"
        for o in (-1, 0, 1):
            x, z = self.at(station, o)
            self.m.set(x, y, z, surface)
            self.clear_above(x, z, y + 1, y + HEADROOM + 2)
        for o in (-2, 2):
            x, z = self.at(station, o)
            self.m.set(x, y, z, ROAD)
            self.m.set(x, y + 1, z, RAIL)
            self.clear_above(x, z, y + 2, y + HEADROOM + 2)
        if station.index % PIER_EVERY == 0:
            for o in (-2, 2):
                x, z = self.at(station, o)
                solid = self.water_floor(x, z)
                if solid is not None and solid < y - 1:
                    self.m.fill((x, solid + 1, z), (x, y - 1, z), ROAD)

    def lamp(self, station: Station, offset: int) -> None:
        """A lamp post on the verge (or railing): fence, fence, lantern."""
        x, z = self.at(station, offset)
        y = station.deck_y if station.deck_y is not None else self.road_y
        if station.kind == GRADE or station.kind == CUT:
            # Stand it on the verge; the shoulder pass put ground there.
            self.m.set(x, y, z, ROAD)
        self.m.fill((x, y + 1, z), (x, y + 2, z), POST)
        self.m.set(x, y + 3, z, LANTERN)
        self.lamps += 1


def describe_route(stations: list[Station]) -> str:
    counts: dict[str, int] = {}
    for station in stations:
        counts[station.kind] = counts.get(station.kind, 0) + 1
    parts = [f"{counts.get(k, 0)} {k}" for k in (GRADE, CUT, TUNNEL, BRIDGE) if counts.get(k)]
    return ", ".join(parts) or "nothing"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("world", help="path to the save directory (holds level.dat)")
    parser.add_argument(
        "length", type=int,
        help=f"how far to build, in blocks ({MIN_LENGTH}-{MAX_LENGTH})",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="survey and build in memory, write nothing"
    )
    args = parser.parse_args(argv)

    if not MIN_LENGTH <= args.length <= MAX_LENGTH:
        print(
            f"error: length must be {MIN_LENGTH}-{MAX_LENGTH}, got {args.length}",
            file=sys.stderr,
        )
        return 1

    world_path = os.path.expanduser(args.world)
    if world_is_open(world_path):
        print(
            f"error: {os.path.basename(world_path)} is open in Minecraft. Close it "
            f"(quit to the title screen) and run again, or the game will overwrite "
            f"this edit when it saves.",
            file=sys.stderr,
        )
        return 1

    save = MinecraftSave.load(world_path)
    print(save)
    dim = save.dimension()

    px, py, pz = save.player_block_position()
    yaw, _ = save.player_rotation()
    name, dx, dz = facing_cardinal(yaw)
    road_y = py - 1  # the block the player is standing on
    start_x, start_z = px + dx, pz + dz
    print(
        f"player at ({px}, {py}, {pz}) facing {name}; road level y={road_y}, "
        f"{args.length} blocks {name} from ({start_x}, {start_z})"
    )

    stations, unknown = survey_route(dim, start_x, start_z, dx, dz, road_y, args.length)
    if unknown:
        first = stations[unknown[0]]
        print(
            f"error: {len(unknown)} of {args.length} stations sit on ground the "
            f"world has not generated (first at ({first.x}, {first.z}), "
            f"{unknown[0]} blocks in). Generate the area in game first, or "
            f"build a shorter road.",
            file=sys.stderr,
        )
        return 1

    kinds = [s.kind for s in stations]
    usable = usable_length(kinds)
    if usable == 0:
        where = stations[0]
        print(
            f"error: the road cannot even start -- the first step at "
            f"({where.x}, {where.z}) opens a span longer than {MAX_BRIDGE} "
            f"blocks. Face somewhere narrower.",
            file=sys.stderr,
        )
        return 1
    if usable < len(stations):
        span = 0
        for k in kinds[usable:]:
            if k != BRIDGE:
                break
            span += 1
        shore = stations[usable - 1]
        print(
            f"note: stopping at {usable} of {args.length} blocks -- the next "
            f"span would run {span}+ blocks over water or void, past the "
            f"{MAX_BRIDGE}-block limit. The road ends at ({shore.x}, {shore.z})."
        )
        stations = stations[:usable]
        kinds = kinds[:usable]

    plan_arches(stations, road_y)
    rise = max((s.deck_y or road_y) for s in stations) - road_y
    print(
        f"route: {describe_route(stations)}; longest bridge span "
        f"{longest_run(kinds, BRIDGE)}"
        + (f", arching {rise} over the water" if rise else "")
    )

    mason = Mason(save, dim)
    builder = RoadBuilder(mason, dim, road_y, dx, dz)
    builder.build(stations)
    print(f"  {builder.lamps} lamp posts lit")
    print("  tidying palettes, refreshing heightmaps, resetting light...")
    mason.finish()

    report = mason.report
    print(f"built: {report.summary()}")
    if not report.complete:
        print(
            f"  warning: incomplete -- {len(report.missing_chunks)} ungenerated "
            f"chunks, {len(report.unreadable_chunks)} unreadable chunks, "
            f"{report.blocks_out_of_range:,} blocks out of range.",
            file=sys.stderr,
        )

    if args.dry_run:
        print("  dry run: nothing written")
        return 0

    try:
        written = save.save()
    except NBTError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    end = stations[-1]
    print(f"  wrote {written} files")
    print(f"\nThe road runs {name} and ends at ({end.x}, {road_y + 1}, {end.z}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
