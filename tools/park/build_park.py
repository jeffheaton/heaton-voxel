#!/usr/bin/env python3
"""Turn the flattened rectangle at x -39..3, z 172..323 into a park.

A lawn-and-lake park down the middle of JeffWorld: a stone promenade from
the north road to a south gate, the player-planted pale oak grove kept as
its north-west wood, a lake with a bridge and a cascade, a fountain circle,
a bandstand, a lakeside pavilion, a pergola and a formal parterre -- lit
well enough that nothing spawns anywhere inside it.

The site is snowy taiga, where "lush" is not a matter of taste but of
climate: water freezes and snow settles on anything under light 10. The
build repaints the park's biome to forest, then strips the snow and the ice
that were already lying there -- a biome change does not retroactively melt
placed blocks.

Usage:  build_park.py <world> [--dry-run] [--map]
"""

import argparse
import os
import random
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import park_plan as P                                        # noqa: E402
from heaton_voxel.craft import Builder, Worksite              # noqa: E402
from heaton_voxel.craft.build import LIGHT_SOURCES            # noqa: E402

BIOME = "minecraft:forest"
SEED = 11

#: Everything a walker's light can pass through. Deliberately mean: a block
#: not listed counts as opaque, so the light figure this reports is a floor
#: and never a flattering one.
_CLEAR_EXACT = {
    "minecraft:air", "minecraft:cave_air", "minecraft:void_air",
    "minecraft:water", "minecraft:lily_pad", "minecraft:vine",
    "minecraft:chain", "minecraft:ladder", "minecraft:snow",
    "minecraft:sugar_cane", "minecraft:short_grass", "minecraft:tall_grass",
    "minecraft:fern", "minecraft:large_fern", "minecraft:pink_petals",
    "minecraft:azalea", "minecraft:flowering_azalea", "minecraft:torch",
    "minecraft:lantern", "minecraft:soul_lantern", "minecraft:end_rod",
    "minecraft:glass", "minecraft:iron_bars",
}
_CLEAR_SUFFIX = ("_leaves", "_carpet", "_fence", "_wall", "_pane", "_sign",
                 "_gate", "_button", "_torch", "_sapling", "_slab",
                 "_stairs", "_trapdoor", "_bush", "_tulip", "_orchid")
_CLEAR_CONTAINS = ("flower", "poppy", "dandelion", "daisy", "allium",
                   "cornflower", "bluet", "lilac", "peony", "sunflower",
                   "rose_bush", "lily_of_the_valley", "resin_clump",
                   "hanging_moss", "moss_carpet")

_GAPS = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air",
         "minecraft:water", "minecraft:flowing_water", "minecraft:lava",
         "minecraft:ice", "minecraft:snow", "minecraft:powder_snow"}

#: What a flower, a fern or a sapling will actually stand on. Sand and
#: gravel are in the shore mix and in neither of these -- a flower set on
#: sand looks placed until the game's next block update deletes it, and a
#: render taken before that update shows a garden that is not there.
_SOIL = {"minecraft:grass_block", "minecraft:dirt", "minecraft:coarse_dirt",
         "minecraft:podzol", "minecraft:rooted_dirt", "minecraft:moss_block",
         "minecraft:mud", "minecraft:mycelium", "minecraft:farmland"}


def clear_for_light(name: str) -> bool:
    return (name in _CLEAR_EXACT
            or name.endswith(_CLEAR_SUFFIX)
            or any(t in name for t in _CLEAR_CONTAINS))


def weighted(mix):
    return [blk for blk, w in mix for _ in range(w)]


# =============================================================================
# classification
# =============================================================================

#: Highest wins. A cell is one thing, and the order says which.
ORDER = ("locked", "bridge", "path", "plaza", "water", "shore", "walk",
         "bed", "wood", "grove", "lawn")


def classify():
    """One kind per column, plus the sets the later steps need."""
    kind = {}

    def paint(cells, name):
        rank = ORDER.index(name)
        for c in cells:
            if not P.in_park(*c):
                continue
            if c not in kind or rank < ORDER.index(kind[c]):
                kind[c] = name

    lake = P.lake_cells()
    island = set(P.disc_cells(-30, 256, 3))
    lake -= island
    prom = P.promenade_cells()
    # The bridge decks a fixed run; everywhere else the promenade is solid
    # ground, so a lobe of the lake that laps over it gets filled instead of
    # leaving a wet gap in the walk.
    bridge = {c for c in prom if P.BRIDGE_Z[0] <= c[1] <= P.BRIDGE_Z[1]}
    lake -= (prom - bridge)

    paint(P.rect_cells(P.X0, P.Z0, P.X1, P.Z1), "lawn")
    paint(P.rect_cells(-39, 172, -14, 218), "grove")
    paint(P.rect_cells(-39, 272, -24, 318), "wood")
    paint(P.rect_cells(*P.PARTERRE), "bed")

    walks = (P.polyline_cells(P.GROVE_WALK, 1)
             | P.polyline_cells(P.WOOD_WALK, 1)
             | P.polyline_cells(P.EAST_WALK, 1))
    for x0, z0, x1, z1 in P.CROSS:
        walks |= set(P.rect_cells(x0, z0, x1, z1))
    walks |= (P.ring(lake, 4) - P.ring(lake, 2))
    # The lakeside ring is a distance transform, so it also circles the
    # island and walks straight into the rockery. Neither is a path.
    walks -= island
    rx, rz = P.ROCKERY
    walks -= set(P.rect_cells(rx - 6, rz - 6, rx + 6, rz + 6))
    paint(walks, "walk")

    paint(P.ring(lake, 1), "shore")
    paint(lake, "water")

    fx, fz, fr = P.FOUNTAIN
    plazas = set(P.disc_cells(fx, fz, fr))
    plazas |= set(P.rect_cells(*P.GATE_COURT))
    plazas |= set(P.rect_cells(*P.SOUTH_COURT))
    paint(plazas, "plaza")
    paint(prom, "path")
    paint(bridge, "bridge")

    locked = {c for c in kind if P.column_locked(*c)}
    paint(locked, "locked")

    return kind, lake, bridge


# =============================================================================
# the steps
# =============================================================================

class Park:
    def __init__(self, site, seed=SEED):
        self.site = site
        self.save = site.save
        self.b = Builder(site.save, seed=seed)
        self.rng = random.Random(seed)
        self.kind, self.lake, self.bridge = classify()
        self.depth = P.depth_map(self.lake)
        self.occupied = set()      # (x, z) a structure stands on
        self.paved = {}            # (x, z) -> y, everything light_cells lights

    # -- primitives ---------------------------------------------------------

    def set(self, x, y, z, block):
        if P.keep_out(x, y, z):
            return False
        self.b.set(x, y, z, block)
        return True

    def block(self, x, y, z):
        b = self.save.get_block(x, y, z)
        return "" if b is None else b.name

    def is_gap(self, x, y, z):
        return self.block(x, y, z) in _GAPS

    def is_keeper(self, x, y, z):
        return self.block(x, y, z).startswith(P.KEEPERS)

    def cells(self, *kinds):
        want = set(kinds)
        return sorted(c for c, k in self.kind.items() if k in want)

    # -- 1. strip the winter off ------------------------------------------

    def strip(self):
        """Snow, ice and stray litter off the whole site, trees untouched."""
        removed = 0
        for x, z in sorted(self.kind):
            # No column-level skip: a protected box has a top, and the snow
            # lying above one is still the park's snow.
            for y in range(P.WALK, 79):
                if self.is_keeper(x, y, z):
                    continue
                if self.block(x, y, z) not in ("", "minecraft:air"):
                    if self.set(x, y, z, P.AIR):
                        removed += 1
        print(f"  stripped {removed:,} blocks of snow and litter")

    # -- 2. climate --------------------------------------------------------

    def reclimate(self):
        r = self.save.fill_biome((P.X0 - 1, 60, P.Z0), (P.X1, 92, P.Z1), BIOME)
        print(f"  biome -> {BIOME}: {r}")

    # -- 3. a crust that holds ---------------------------------------------

    def ground(self):
        """Three solid layers under every column, then the surface on top.

        The site is undermined: caves run to within a block of the lawn over
        most of the south half, and the "river" was an ice sheet over open
        air. Nothing here trusts the ground it finds -- ``y=59`` and ``y=60``
        are made solid wherever they are not, which seals the crust without
        filling the caves underneath it.
        """
        lawn = weighted(P.LAWN_MIX)
        filled = 0
        for x, z in sorted(self.kind):
            k = self.kind[(x, z)]
            if k == "locked":
                continue
            for y in (58, 59, 60):
                if self.is_gap(x, y, z):
                    if self.set(x, y, z, P.DIRT):
                        filled += 1
            if k not in ("water", "shore", "path", "plaza", "walk", "bridge"):
                self.set(x, P.GY, z, self.rng.choice(lawn))
        print(f"  crust: {filled:,} gaps filled under the park")

    # -- 4. the lake -------------------------------------------------------

    def water(self):
        bed = weighted(P.BED_MIX)
        shore = weighted(P.SHORE_MIX)
        for x, z in sorted(self.lake):
            d = self.depth.get((x, z), 1)
            bed_y = P.WATER_TOP - d
            for y in (bed_y - 2, bed_y - 1):
                self.set(x, y, z, P.STONE)
            self.set(x, bed_y, z, self.rng.choice(bed))
            for y in range(bed_y + 1, P.WATER_TOP + 1):
                self.set(x, y, z, P.WATER)
            for y in range(P.WATER_TOP + 1, 64):
                self.set(x, y, z, P.AIR)

        for x, z in self.cells("shore"):
            for y in (57, 58, 59, 60):
                if self.is_gap(x, y, z):
                    self.set(x, y, z, P.DIRT)
            self.set(x, P.GY, z, self.rng.choice(shore))

        # Sea lanterns in the bed: the lake reads lit from the bank at night
        # and the water is not a black hole in the middle of the park.
        lanterns = 0
        for x, z in sorted(self.lake):
            if self.depth.get((x, z), 1) >= 2 and (x + z) % 6 == 0 and (x - z) % 6 == 0:
                self.set(x, P.WATER_TOP - self.depth[(x, z)], z,
                         "minecraft:sea_lantern")
                lanterns += 1

        pads = reeds = 0
        for x, z in sorted(self.lake):
            if self.depth.get((x, z), 1) <= 2 and self.rng.random() < 0.13:
                if self.set(x, P.WATER_TOP + 1, z, "minecraft:lily_pad"):
                    pads += 1
        # Reeds want water beside the block they stand *on*, not one below
        # it, so each stand gets a sand shelf cut down to the waterline. Set
        # on the bank instead they look right until the next block update
        # snaps them off.
        for x, z in self.cells("shore"):
            if self.rng.random() >= 0.24:
                continue
            if not any(self.kind.get((x + dx, z + dz)) == "water"
                       for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                continue
            self.set(x, P.WATER_TOP, z, "minecraft:sand")
            self.set(x, P.GY, z, P.AIR)
            for i in range(self.rng.randint(2, 3)):
                self.set(x, P.GY + i, z, "minecraft:sugar_cane")
            self.occupied.add((x, z))
            reeds += 1
        print(f"  lake: {len(self.lake):,} cells, {lanterns} sea lanterns, "
              f"{pads} lily pads, {reeds} stands of reed")

    # -- 5. paving ---------------------------------------------------------

    def paths(self):
        path_mix = weighted(P.PATH_MIX)
        walk_mix = weighted(P.WALK_MIX)
        for x, z in self.cells("path", "plaza"):
            self.set(x, P.GY, z, self.rng.choice(path_mix))
            self.paved[(x, z)] = P.GY
        for x, z in self.cells("walk"):
            self.set(x, P.GY, z, self.rng.choice(walk_mix))
            self.paved[(x, z)] = P.GY

        # Granite curb where the fountain circle meets the grass, and along
        # the two courts -- the trim is what stops a plaza reading as spill.
        fx, fz, fr = P.FOUNTAIN
        trim = 0
        for x, z in sorted(set(P.disc_cells(fx, fz, fr))):
            if (x - fx) ** 2 + (z - fz) ** 2 > (fr - 1) ** 2:
                if self.kind.get((x, z)) in ("plaza", "path"):
                    self.set(x, P.GY, z, P.PLAZA_TRIM)
                    trim += 1
        for x0, z0, x1, z1 in (P.GATE_COURT, P.SOUTH_COURT):
            for x, z in sorted(set(P.rect_cells(x0, z0, x1, z1))):
                if x in (x0, x1) or z in (z0, z1):
                    if self.kind.get((x, z)) in ("plaza", "path"):
                        self.set(x, P.GY, z, P.PLAZA_TRIM)
                        trim += 1
        print(f"  paving: {len(self.paved):,} cells, {trim} of granite trim")

    # -- 6. the bridge -----------------------------------------------------

    def bridge_span(self):
        """A stone bridge over the neck of the lake, with slab ramps.

        Ramped in half blocks rather than stairs on purpose: a stair's
        facing is one more thing to get backwards, and a slab reads the
        same from six blocks away.
        """
        if not self.bridge:
            return
        zs, ze = P.BRIDGE_Z
        deck = P.BRIDGE_DECK
        brick = "minecraft:stone_bricks"
        slab = "minecraft:stone_brick_slab[type=bottom]"

        def deck_x(z):
            c = P.prom_center(z)
            return range(c - P.PROM_HALF, c + P.PROM_HALF + 1)

        # ramps: 62.0 -> 62.5 -> 63.0 -> 63.5 -> 64.0
        for side, zr in ((-1, range(zs - 4, zs)), (1, range(ze + 1, ze + 5))):
            steps = list(zr) if side < 0 else list(zr)[::-1]
            for i, z in enumerate(steps):
                for x in deck_x(z):
                    self.paved.pop((x, z), None)
                    if i == 0:
                        self.set(x, P.WALK, z, slab)
                    elif i == 1:
                        self.set(x, P.WALK, z, brick)
                        self.paved[(x, z)] = P.WALK
                    elif i == 2:
                        self.set(x, P.WALK, z, brick)
                        self.set(x, deck, z, slab)
                    else:
                        self.set(x, P.WALK, z, brick)
                        self.set(x, deck, z, brick)
                        self.paved[(x, z)] = deck
                    for y in range(deck + 1, deck + 3):
                        self.set(x, y, z, P.AIR)

        for z in range(zs, ze + 1):
            for x in deck_x(z):
                self.set(x, deck, z, brick)
                self.set(x, deck + 1, z, P.AIR)
                self.paved[(x, z)] = deck

        # railings, and a lantern on every third post
        for z in range(zs - 1, ze + 2):
            xs = list(deck_x(z))
            for x in (xs[0], xs[-1]):
                self.set(x, deck + 1, z, "minecraft:stone_brick_wall")
                if z % 3 == 0:
                    self.set(x, deck + 2, z, "minecraft:lantern")

        # two piers, and the abutments they spring from
        span = ze - zs + 1
        for frac in (1 / 3, 2 / 3):
            pz = zs + round(span * frac)
            for z in (pz, pz + 1):
                for x in deck_x(z):
                    for y in range(56, deck):
                        self.set(x, y, z, brick)
        print(f"  bridge: z {zs}..{ze} on {span} spans, deck y={deck}")

    # -- 7. built things ---------------------------------------------------

    def structures(self):
        self.gate((-9, 173), "north")
        self.gate((-18, 321), "south")
        self.approach(range(-11, -6), 172, -1)
        self.approach(range(-20, -15), 323, +1)
        self.jetty()
        self.bandstand()
        self.pavilion()
        self.fountain()
        self.pergola()
        self.rockery()
        self.obsidian_feature()
        self.lamp_posts()
        self.benches()

    def lamp_posts(self):
        """Lamps down the promenade, alternating sides.

        Placed with the structures rather than with the lighting: by the time
        the lighting pass runs, the trees and benches have taken the cells a
        lamp wants, and a promenade lit only from under the paving looks like
        a runway rather than a walk.
        """
        posts = 0
        for z in range(P.Z0 + 4, P.Z1, 6):
            c = P.prom_center(z)
            side = -1 if (z // 6) % 2 else 1
            x = c + side * (P.PROM_HALF + 1)
            if self.kind.get((x, z)) in ("water", "bridge", "locked"):
                continue
            if self.lamp_post(x, z):
                posts += 1
        print(f"  lamp posts on the promenade: {posts}")

    def _hold(self, x0, z0, x1, z1):
        for z in range(z0, z1 + 1):
            for x in range(x0, x1 + 1):
                self.occupied.add((x, z))

    def gate(self, center, name):
        cx, cz = center
        brick = "minecraft:stone_bricks"
        for dx in (-4, -3, 3, 4):
            for dz in (0, 1):
                for y in range(P.WALK, 68):
                    self.set(cx + dx, y, cz + dz, brick)
                self.set(cx + dx, P.GY, cz + dz, P.PLAZA_TRIM)
                self.set(cx + dx, 67, cz + dz, P.PLAZA_TRIM)
        for dx in range(-4, 5):
            for dz in (0, 1):
                self.set(cx + dx, 68, cz + dz, brick)
                self.set(cx + dx, 69, cz + dz,
                         "minecraft:stone_brick_slab[type=bottom]")
        for dx in (-2, 2):
            self.set(cx + dx, 67, cz, "minecraft:lantern[hanging=true]")
        self._hold(cx - 4, cz, cx + 4, cz + 1)
        print(f"  {name} gate at x={cx}, z={cz}")

    #: Blocks that fall off if what is under them is wrong. Checked at the
    #: end, because "it rendered" and "the game will keep it" are different
    #: claims and only the second one survives the next block update.
    _NEEDS_SOIL = ("_tulip", "_orchid", "poppy", "dandelion", "daisy",
                   "allium", "cornflower", "bluet", "lily_of_the_valley",
                   "short_grass", "tall_grass", "fern", "pink_petals",
                   "lilac", "peony", "rose_bush", "sunflower", "azalea")

    def audit(self):
        """What the build must be able to say about itself afterwards."""
        box1, box2 = (P.X0, 55, P.Z0), (P.X1, 90, P.Z1)
        winter = self.save.find_blocks(
            box1, box2, ["snow", "powder_snow", "ice", "packed_ice",
                         "blue_ice", "frosted_ice", "snow_block"],
            match="name")
        print(f"  winter left in the park: {len(winter.matches)} blocks")
        for x, y, z, st in winter.matches[:8]:
            print(f"    ({x}, {y}, {z}) {st.name} "
                  f"[{self.kind.get((x, z))}]")

        rootless = []
        for x, z in sorted(self.kind):
            for y in (P.WALK, P.WALK + 1):
                n = self.block(x, y, z)
                if not n or not n.endswith(self._NEEDS_SOIL):
                    continue
                below = self.block(x, y - 1, z)
                if below in _SOIL or below.endswith(self._NEEDS_SOIL):
                    continue
                rootless.append((x, y, z, n, below))
        print(f"  plants standing on nothing that holds them: {len(rootless)}")
        for r in rootless[:6]:
            print(f"    {r}")

        cane = self.save.find_blocks(box1, box2, "sugar_cane", match="name")
        bad_cane = 0
        for x, y, z, _ in cane.matches:
            if self.block(x, y - 1, z) == "minecraft:sugar_cane":
                continue
            if not any(self.block(x + dx, y - 1, z + dz).startswith(
                    "minecraft:water")
                    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                bad_cane += 1
                if bad_cane <= 4:
                    print(f"    dry reed at ({x}, {y}, {z}) on "
                          f"{self.block(x, y - 1, z)}")
        print(f"  reeds with no water beside their root: {bad_cane}")
        return len(winter.matches) + len(rootless) + bad_cane

    def access_report(self, xs, zs, top=66):
        """The step profile through a gate, as heights you could jump.

        A gate is the one place a park can silently fail: the site was cut
        flat, so its boundary is a face, and every render of it is top-down.
        """
        rows = []
        for x in xs:
            tops = [self.top_solid(x, z, top) for z in zs]
            worst = max((abs((b or 0) - (a or 0))
                         for a, b in zip(tops, tops[1:])), default=0)
            rows.append((x, tops, worst))
        for x, tops, worst in rows:
            print(f"    x={x:>4}  " + " ".join(
                f"{z}:{t}" for z, t in zip(zs, tops)) + f"   worst step {worst}")
        return max(r[2] for r in rows)

    def top_solid(self, x, z, top=78):
        for y in range(top, 50, -1):
            n = self.block(x, y, z)
            if n and n not in _GAPS and not n.endswith(_CLEAR_SUFFIX) \
                    and n not in _CLEAR_EXACT:
                return y
        return None

    def approach(self, xs, z_edge, outward, reach=4):
        """Make the gate opening walkable in both directions.

        The site was cut flat, so its edge is a two-block face -- a drop in
        and a wall out, and a top-down render shows neither. This walks the
        few cells beyond the boundary and lifts whichever one is low until
        no step is more than a block, which is a cutting nobody has to jump.
        The park's own edge is never raised, and neither is anything inside a
        protected zone: the north road stays exactly as it is and the steps
        are built on the verge in front of it.
        """
        brick = "minecraft:stone_bricks"
        built = 0
        for x in xs:
            zs = [z_edge + outward * i for i in range(reach + 1)]
            top = {zs[0]: P.GY}
            for z in zs[1:]:
                top[z] = self.top_solid(x, z)

            def level_to(z, y):
                nonlocal built
                for yy in range(top[z] + 1, y + 1):
                    self.set(x, yy, z, brick)
                for yy in range(y + 1, y + 4):
                    if self.block(x, yy, z) not in ("minecraft:air", ""):
                        self.set(x, yy, z, P.AIR)
                top[z] = y
                built += 1

            for cur, nxt in zip(zs, zs[1:]):
                if top[nxt] is None:
                    break
                if top[nxt] - top[cur] >= 2:
                    if cur != z_edge and not P.keep_out(x, top[cur] + 1, cur):
                        level_to(cur, min(top[nxt] - 1, top[cur] + 1))
                    # The park's own edge cannot rise -- the plaza is at that
                    # height by design -- so the bank outside is cut instead.
                    if top[nxt] - top[cur] >= 2 \
                            and not P.keep_out(x, top[cur] + 1, nxt):
                        level_to(nxt, top[cur] + 1)
                if top[cur] - top[nxt] >= 2 \
                        and not P.keep_out(x, top[cur] - 1, nxt):
                    level_to(nxt, top[cur] - 1)
        print(f"  approach at z={z_edge}: {built} cells stepped")

    def jetty(self):
        """A plank jetty off the promenade into the west basin."""
        sx, sz, length = P.JETTY
        deck = "minecraft:oak_planks"
        n = 0
        for i in range(length):
            x = sx - i
            for dz in (-1, 0, 1):
                if self.kind.get((x, sz + dz)) not in ("water", "shore"):
                    continue
                self.set(x, P.GY, sz + dz, deck)
                for y in range(P.WALK, P.WALK + 3):
                    self.set(x, y, sz + dz, P.AIR)
                self.occupied.add((x, sz + dz))
                n += 1
        end = sx - length + 1
        for dz in (-1, 1):
            self.b.place(end, P.WATER_TOP, sz + dz, "minecraft:oak_fence")
            self.set(end, P.WALK, sz + dz, "minecraft:oak_fence")
            self.set(end, P.WALK + 1, sz + dz, "minecraft:lantern")
        print(f"  jetty from ({sx}, {sz}) running {n} deck cells into the lake")

    def bandstand(self):
        cx, cz = P.BANDSTAND
        floor = "minecraft:polished_andesite"
        for dz in range(-4, 5):
            for dx in range(-4, 5):
                if abs(dx) + abs(dz) > 6:
                    continue
                x, z = cx + dx, cz + dz
                self.set(x, P.GY, z, "minecraft:stone_bricks")
                self.set(x, P.WALK, z, floor)
                for y in range(P.WALK + 1, 67):
                    self.set(x, y, z, P.AIR)
                self.occupied.add((x, z))
        for dx, dz in ((0, -5), (0, 5), (-5, 0), (5, 0)):
            self.set(cx + dx, P.WALK, cz + dz,
                     "minecraft:stone_brick_slab[type=bottom]")
            self.occupied.add((cx + dx, cz + dz))
        posts = [(-3, -3), (3, -3), (-3, 3), (3, 3),
                 (0, -4), (0, 4), (-4, 0), (4, 0)]
        for dx, dz in posts:
            for y in range(63, 67):
                self.set(cx + dx, y, cz + dz, "minecraft:stripped_oak_log[axis=y]")
        for dz in range(-4, 5):
            for dx in range(-4, 5):
                if abs(dx) + abs(dz) <= 6:
                    self.set(cx + dx, 67, cz + dz, "minecraft:dark_oak_planks")
                if abs(dx) + abs(dz) <= 4:
                    self.set(cx + dx, 68, cz + dz, "minecraft:dark_oak_planks")
                if abs(dx) + abs(dz) <= 2:
                    self.set(cx + dx, 69,
                             cz + dz, "minecraft:dark_oak_slab[type=bottom]")
        self.set(cx, 66, cz, "minecraft:lantern[hanging=true]")
        for dx, dz in ((-2, -2), (2, -2), (-2, 2), (2, 2)):
            self.set(cx + dx, 66, cz + dz, "minecraft:lantern[hanging=true]")
        print(f"  bandstand at {P.BANDSTAND}")

    def pavilion(self):
        cx, cz = P.PAVILION
        for dz in range(-3, 4):
            for dx in range(-3, 4):
                x, z = cx + dx, cz + dz
                self.set(x, P.GY, z, "minecraft:stone_bricks")
                self.set(x, P.WALK, z, "minecraft:smooth_stone")
                for y in range(P.WALK + 1, 66):
                    self.set(x, y, z, P.AIR)
                self.occupied.add((x, z))
        for dx, dz in ((-3, -3), (3, -3), (-3, 3), (3, 3)):
            for y in range(63, 66):
                self.set(cx + dx, y, cz + dz,
                         "minecraft:stripped_birch_log[axis=y]")
        for dz in range(-3, 4):
            for dx in range(-3, 4):
                self.set(cx + dx, 66, cz + dz, "minecraft:birch_planks")
                if abs(dx) <= 2 and abs(dz) <= 2:
                    self.set(cx + dx, 67, cz + dz,
                             "minecraft:birch_slab[type=bottom]")
        for dx, dz in ((-2, -2), (2, -2), (-2, 2), (2, 2)):
            self.set(cx + dx, 65, cz + dz, "minecraft:lantern[hanging=true]")
        for dx in (-1, 0, 1):
            self.set(cx + dx, P.WALK + 1, cz - 3,
                     "minecraft:birch_stairs[facing=north,half=bottom]")
        print(f"  lakeside pavilion at {P.PAVILION}")

    def fountain(self):
        cx, cz, _ = P.FOUNTAIN
        r = P.FOUNTAIN_BASIN
        granite = P.PLAZA_TRIM
        for x, z in sorted(set(P.disc_cells(cx, cz, r))):
            d2 = (x - cx) ** 2 + (z - cz) ** 2
            self.occupied.add((x, z))
            if d2 > (r - 1) ** 2:
                self.set(x, P.GY, z, granite)
                self.set(x, P.WALK, z, granite)
            else:
                self.set(x, P.GY, z, "minecraft:prismarine_bricks")
                self.set(x, P.WALK, z, P.WATER)
        # A plain gray disc eleven across is a car park. A second granite
        # ring and four quarter beds give it something to be the middle of.
        flowers = weighted(P.FLOWERS)
        _, _, fr = P.FOUNTAIN
        for x, z in sorted(set(P.disc_cells(cx, cz, fr - 1))):
            dx, dz = x - cx, z - cz
            d2 = dx * dx + dz * dz
            if 36 < d2 <= 49:
                self.set(x, P.GY, z, granite)
            elif 60 <= d2 <= 100 and abs(dx) >= 3 and abs(dz) >= 3:
                self.set(x, P.GY, z, "minecraft:moss_block")
                self.set(x, P.WALK, z, self.rng.choice(flowers))
                self.paved.pop((x, z), None)
                self.occupied.add((x, z))
        for dx, dz in ((-6, -6), (6, -6), (-6, 6), (6, 6)):
            self.lamp_post(cx + dx, cz + dz)
        for x, z in ((cx, cz - 2), (cx, cz + 2), (cx - 2, cz), (cx + 2, cz)):
            self.set(x, P.GY, z, "minecraft:sea_lantern")
        for dz in (-1, 0, 1):
            for dx in (-1, 0, 1):
                self.set(cx + dx, P.WALK, cz + dz, granite)
                self.set(cx + dx, P.WALK + 1, cz + dz, granite)
        self.set(cx, P.WALK + 2, cz, "minecraft:sea_lantern")
        self.set(cx, P.WALK + 3, cz, granite)
        self.set(cx, P.WALK + 4, cz, P.WATER)
        print(f"  fountain at ({cx}, {cz}), basin r={r}")

    def pergola(self):
        z0, z1 = P.PERGOLA_Z
        beams = 0
        for z in range(z0, z1 + 1):
            c = P.prom_center(z)
            for x in (c - 3, c + 3):
                for y in range(P.WALK, 66):
                    self.set(x, y, z, "minecraft:stripped_oak_log[axis=y]")
                self.occupied.add((x, z))
            if (z - z0) % 2 == 0:
                for x in range(c - 3, c + 4):
                    self.set(x, 66, z, "minecraft:oak_fence")
                if self.rng.random() < 0.55:
                    self.set(c, 65, z, "minecraft:lantern[hanging=true]")
                beams += 1
            for x in range(c - 3, c + 4):
                if self.rng.random() < 0.45:
                    self.set(x, 67, z, P.HEDGE_FLOWER)
        print(f"  pergola z {z0}..{z1}: {beams} beams")

    def rockery(self):
        """A rock mound in the north-west corner of the lake, and the stream
        that falls off it. The steps are cut before the water goes in, so the
        cascade is a chain of sources rather than a spill that has to settle.
        """
        cx, cz = P.ROCKERY
        rocks = ["minecraft:stone", "minecraft:andesite",
                 "minecraft:mossy_cobblestone", "minecraft:moss_block",
                 "minecraft:cobblestone"]
        for dz in range(-5, 6):
            for dx in range(-5, 6):
                x, z = cx + dx, cz + dz
                if not P.in_park(x, z) or self.kind.get((x, z)) == "locked":
                    continue
                if self.kind.get((x, z)) in ("water", "path", "bridge"):
                    continue
                d = max(abs(dx), abs(dz))
                top = P.GY + max(0, 5 - d)
                if top <= P.GY:
                    continue
                for y in range(P.GY, top + 1):
                    self.set(x, y, z, self.rng.choice(rocks))
                self.occupied.add((x, z))
        # the fall: one basin per level, north to south into the lake. The
        # head needs a back wall -- the mound falls away north of the top
        # basin, and a source with nothing behind it runs down the far side.
        for x in range(cx - 2, cx + 3):
            for y in range(64, 66):
                self.set(x, y, cz - 2, "minecraft:mossy_cobblestone")
        for i, z in enumerate(range(cz - 1, cz + 5)):
            y = 65 - i
            for x in (cx - 2, cx + 2):
                for yy in range(y, y + 3):
                    self.set(x, yy, z, "minecraft:mossy_cobblestone")
            for x in (cx - 1, cx, cx + 1):
                self.set(x, y - 1, z, "minecraft:stone")
                self.set(x, y, z, P.WATER)
                for yy in range(y + 1, y + 3):
                    self.set(x, yy, z, P.AIR)
        print(f"  rockery and cascade at {P.ROCKERY}")

    def obsidian_feature(self):
        """Curb the buried obsidian box instead of pretending it is lawn.

        Its own blocks are protected and stay exactly as they are; what goes
        in is the ring around them, so a black 3x3 in the middle of a lawn
        reads as a plinth somebody meant rather than a hole somebody left.
        """
        cx, cz = P.OBSIDIAN
        for dz in range(-3, 4):
            for dx in range(-3, 4):
                x, z = cx + dx, cz + dz
                if P.column_locked(x, z) or not P.in_park(x, z):
                    continue
                d = max(abs(dx), abs(dz))
                if d == 2:
                    self.set(x, P.GY, z, P.PLAZA_TRIM)
                elif d == 3 and (x + z) % 2 == 0:
                    self.set(x, P.WALK, z, "minecraft:oak_leaves[persistent=true]")
                self.occupied.add((x, z))
        self.set(cx, P.WALK, cz, "minecraft:lantern")
        for dx, dz in ((-2, -2), (2, -2), (-2, 2), (2, 2)):
            self.lamp_post(cx + dx, cz + dz)
        print(f"  obsidian plinth curbed at {P.OBSIDIAN}")

    SITTABLE = ("lawn", "grove", "wood", "bed", "plaza", "walk", "shore")

    def bench(self, x, z, facing, along="z"):
        cells = [(x, z + d) for d in (-1, 0, 1)] if along == "z" \
            else [(x + d, z) for d in (-1, 0, 1)]
        for cx, cz in cells:
            if (self.kind.get((cx, cz)) not in self.SITTABLE
                    or (cx, cz) in self.occupied
                    or P.column_locked(cx, cz)
                    or self.block(cx, P.WALK, cz) != "minecraft:air"):
                return False
        for cx, cz in cells:
            self.set(cx, P.WALK, cz,
                     f"minecraft:oak_stairs[facing={facing},half=bottom]")
            self.occupied.add((cx, cz))
        return True

    def benches(self):
        """Benches down the promenade, round the fountain, and facing water.

        The stair's ``facing`` is its tall side, which is the back: a bench
        set ``facing=west`` seats you looking east.
        """
        n = 0
        for z in range(P.Z0 + 6, P.Z1 - 4, 7):
            c = P.prom_center(z)
            for side, facing in ((-1, "west"), (1, "east")):
                x = c + side * (P.PROM_HALF + 1)
                n += self.bench(x, z, facing)
        fx, fz, _ = P.FOUNTAIN
        for dx, dz, facing, along in ((0, -8, "north", "x"), (0, 8, "south", "x"),
                                      (-8, 0, "west", "z"), (8, 0, "east", "z")):
            n += self.bench(fx + dx, fz + dz, facing, along)
        # Facing is the bench's back, so it is the way *away* from the water.
        away = {(0, -1): ("south", "x"), (0, 1): ("north", "x"),
                (-1, 0): ("east", "z"), (1, 0): ("west", "z")}
        for x, z in self.cells("shore")[::17]:
            for (dx, dz), (facing, along) in away.items():
                if self.kind.get((x + dx, z + dz)) == "water":
                    n += self.bench(x, z, facing, along)
                    break
        print(f"  benches: {n}")

    # -- 8. planting -------------------------------------------------------

    def planting(self):
        self.avenue()
        self.woodland()
        self.parterre()
        self.gate_beds()
        self.borders()
        self.meadow()
        self.understory()

    def _plantable(self, x, z, kinds):
        return (self.kind.get((x, z)) in kinds
                and (x, z) not in self.occupied
                and (x, z) not in self.paved
                and not P.column_locked(x, z)
                and self.block(x, P.GY, z) in _SOIL
                and self.block(x, P.WALK, z) == "minecraft:air")

    def _tree(self, x, z, kind, taken, gap=4):
        if any((x - tx) ** 2 + (z - tz) ** 2 < gap * gap for tx, tz in taken):
            return False
        self.b.ornamental_tree(x, z, kind=kind)
        taken.add((x, z))
        for dz in range(-2, 3):
            for dx in range(-2, 3):
                self.occupied.add((x + dx, z + dz))
        return True

    def avenue(self):
        """Specimen trees down the promenade, and round the lake and lawns."""
        taken = set()
        n = 0
        for z in range(P.Z0 + 6, P.Z1 - 4, 9):
            c = P.prom_center(z)
            for side, kind in ((-1, P.AVENUE[(z // 9) % len(P.AVENUE)]),
                               (1, P.AVENUE[(z // 9 + 2) % len(P.AVENUE)])):
                x = c + side * (P.PROM_HALF + 3)
                if self._plantable(x, z, ("lawn", "bed", "grove", "wood")):
                    n += self._tree(x, z, kind, taken)
        for x, z in self.cells("lawn"):
            if self.rng.random() < 0.020 and self._plantable(
                    x, z, ("lawn",)):
                n += self._tree(x, z, self.rng.choice(P.AVENUE), taken, gap=6)
        print(f"  avenue and lawn trees: {n}")
        self._taken = taken

    def woodland(self):
        taken = getattr(self, "_taken", set())
        n = 0
        for x, z in self.cells("wood"):
            if self.rng.random() < 0.15 and self._plantable(x, z, ("wood",)):
                n += self._tree(x, z, self.rng.choice(P.WOODLAND), taken, gap=4)
        for x, z in self.cells("grove"):
            if self.rng.random() < 0.035 and self._plantable(x, z, ("grove",)):
                if self.set(x, P.WALK, z, "minecraft:flowering_azalea"):
                    self.occupied.add((x, z))
                    n += 1
        print(f"  woodland trees and azaleas: {n}")

    def parterre(self):
        """The formal garden: hedged beds, and something in flower in each."""
        x0, z0, x1, z1 = P.PARTERRE
        flowers = weighted(P.FLOWERS)
        beds = tall = hedges = 0
        for z in range(z0, z1 + 1):
            for x in range(x0, x1 + 1):
                if not self._plantable(x, z, ("bed",)):
                    continue
                edge = (x - x0) % 7 in (0,) or (z - z0) % 7 in (0,)
                if edge:
                    if self.set(x, P.WALK, z, P.HEDGE):
                        self.set(x, P.WALK + 1, z,
                                 P.HEDGE_FLOWER if (x + z) % 5 == 0 else P.HEDGE)
                        self.occupied.add((x, z))
                        hedges += 1
                elif (x + z) % 9 == 0:
                    self.b.place(x, P.WALK, z, self.rng.choice(P.TALL_FLOWERS))
                    self.occupied.add((x, z))
                    tall += 1
                elif self.rng.random() < 0.75:
                    if self.set(x, P.WALK, z, self.rng.choice(flowers)):
                        beds += 1
        print(f"  parterre: {hedges} hedge, {beds} flowers, {tall} tall")

    def gate_beds(self):
        """Four clipped beds in the entrance court, so the gate reads formal."""
        flowers = weighted(P.FLOWERS)
        n = 0
        for x0, z0, x1, z1 in P.GATE_BEDS:
            for z in range(z0, z1 + 1):
                for x in range(x0, x1 + 1):
                    if P.column_locked(x, z) or (x, z) in self.occupied:
                        continue
                    edge = x in (x0, x1) or z in (z0, z1)
                    self.set(x, P.GY, z,
                             P.PLAZA_TRIM if edge else "minecraft:moss_block")
                    if edge:
                        self.set(x, P.WALK, z, P.AIR)
                    else:
                        self.set(x, P.WALK, z, self.rng.choice(flowers))
                    self.paved.pop((x, z), None)
                    if edge:
                        self.paved[(x, z)] = P.GY
                    self.occupied.add((x, z))
                    n += 1
        print(f"  entrance court beds: {n} cells")

    def borders(self):
        """Flower borders along the promenade, in runs with gaps between.

        A continuous ribbon the whole length reads as wallpaper; broken into
        stretches it reads as planting somebody chose to put there.
        """
        flowers = weighted(P.FLOWERS)
        n = 0
        for z in range(P.Z0, P.Z1 + 1):
            if (z // 9) % 2:
                continue
            c = P.prom_center(z)
            for x in (c - P.PROM_HALF - 1, c + P.PROM_HALF + 1):
                if not self._plantable(x, z, ("lawn", "grove", "wood", "bed")):
                    continue
                self.set(x, P.GY, z, "minecraft:moss_block")
                if self.set(x, P.WALK, z, self.rng.choice(flowers)):
                    n += 1
        print(f"  promenade borders: {n} cells in flower")

    def meadow(self):
        """The south-east corner, left long: tall grass and dense flowers."""
        x0, z0, x1, z1 = P.MEADOW
        flowers = weighted(P.FLOWERS)
        n = t = 0
        for z in range(z0, z1 + 1):
            for x in range(x0, x1 + 1):
                if not self._plantable(x, z, ("lawn",)):
                    continue
                r = self.rng.random()
                if r < 0.30:
                    if self.set(x, P.WALK, z, self.rng.choice(flowers)):
                        n += 1
                elif r < 0.38:
                    self.b.place(x, P.WALK, z, "minecraft:tall_grass")
                    t += 1
                elif r < 0.62:
                    self.set(x, P.WALK, z, "minecraft:short_grass")
        print(f"  wildflower meadow: {n} flowers, {t} clumps of tall grass")

    def understory(self):
        """Ground cover: thick in the woods, sparse and clumped on the lawns.

        Scattering flowers evenly over a lawn at a few percent looks like
        confetti from above and like nothing at all from the ground. The
        lawns get clumps instead, and the woods get a floor.
        """
        cover = weighted(P.UNDERSTORY)
        grass = ["minecraft:short_grass"] * 6 + ["minecraft:fern"] * 2
        flowers = weighted(P.FLOWERS)
        n = f = 0
        for x, z in self.cells("grove", "wood", "shore"):
            if not self._plantable(x, z, ("grove", "wood", "shore")):
                continue
            k = self.kind[(x, z)]
            if self.rng.random() < (0.46 if k != "shore" else 0.12):
                if self.set(x, P.WALK, z, self.rng.choice(cover)):
                    n += 1
        for x, z in self.cells("lawn"):
            if self._plantable(x, z, ("lawn",)) and self.rng.random() < 0.09:
                if self.set(x, P.WALK, z, self.rng.choice(grass)):
                    n += 1
        # clumps: a handful of cells each, which is how a lawn actually flowers
        lawn = self.cells("lawn")
        for _ in range(70):
            cx, cz = self.rng.choice(lawn)
            kind = self.rng.choice(flowers)
            for dz in range(-2, 3):
                for dx in range(-2, 3):
                    if abs(dx) + abs(dz) > 2 or self.rng.random() < 0.35:
                        continue
                    x, z = cx + dx, cz + dz
                    if self._plantable(x, z, ("lawn",)):
                        if self.set(x, P.WALK, z, kind):
                            f += 1
        print(f"  understory: {n:,} of ground cover, {f} in flower clumps")

    # -- 9. light ----------------------------------------------------------

    def lighting(self, floor=7):
        """Paving lit by the library; lawns lit from under the moss.

        ``light_cells`` derives its own spacing and patches what the lattice
        leaves short, which is the right tool for the paved half. A lawn
        cannot take a glowstone in its surface without looking like a runway,
        so out there the lamp goes *under* a patch of moss carpet, and the
        whole park is then measured -- a real flood fill over what was
        actually built, not the lattice's straight-line promise -- and every
        cell under ``floor`` gets one of its own.
        """
        cells = [(x, y, z) for (x, z), y in sorted(self.paved.items())]
        res = self.b.light_cells(cells, light=P.GLOW, light_level=10)
        print(f"  paving: {res}")

        hidden = self.hidden_lamps()
        print(f"  lamps under the moss: {hidden}")

        # Patching one lamp per dark cell would light the park several times
        # over: the first lamp of a dark patch lights most of the rest of it.
        # Place greedily at the lamp's own reach, then measure again -- twice,
        # because a lamp can be refused (a trunk, a bench) and leave a hole.
        reach = 14 - floor
        still = self.verify_light(floor)
        for _ in range(3):
            if not still:
                break
            placed = []
            for x, z in still:
                if any(abs(x - px) + abs(z - pz) <= reach for px, pz in placed):
                    continue
                if self.patch_lamp(x, z):
                    placed.append((x, z))
            print(f"  patched {len(placed)} dark patches "
                  f"({len(still)} dark cells)")
            if not placed:
                break
            still = self.verify_light(floor)
        print(f"  light: {len(still)} cells below {floor} after patching")
        return still

    def lamp_post(self, x, z):
        if P.column_locked(x, z) or self.block(x, P.WALK, z) != "minecraft:air":
            return False
        for y in range(P.WALK, P.WALK + 3):
            self.set(x, y, z, "minecraft:cobblestone_wall")
        self.set(x, P.WALK + 3, z, "minecraft:lantern")
        self.occupied.add((x, z))
        return True

    def patch_lamp(self, x, z):
        """Light one measured-dark cell however that cell takes a lamp.

        ``light_cells`` guarantees its paving against straight-line distance
        over open ground; a bench, a hedge or a canopy is neither, so a paved
        cell can still measure short. There it gets the same inset glowstone
        the lattice would have used; on grass it gets one under the moss.
        (So it did when the park was built. ``light_cells`` now checks every
        cell by the game's flood itself; this script is kept as it ran.)
        """
        if (x, z) in self.paved:
            y = self.paved[(x, z)]
            return self.set(x, y, z, P.GLOW)
        return self.hidden_lamp(x, z, force=True)

    def hidden_lamp(self, x, z, force=False):
        """A glowstone in the surface with moss carpet over it.

        Carpet has no light opacity, so the lamp reads on the grass and not
        in it. Only ever laid where the cell above is genuinely empty --
        under a tree it would light nothing and show as a bald patch.
        """
        if P.column_locked(x, z):
            return False
        k = self.kind.get((x, z))
        if k in (None, "water", "bridge", "locked"):
            return False
        if (x, z) in self.paved or (x, z) in self.occupied:
            return False
        if self.block(x, P.WALK, z) != "minecraft:air" and not force:
            return False
        if self.block(x, P.WALK, z) not in ("minecraft:air",):
            # Clear the upper half too: a two-block plant whose foot is
            # taken leaves a head floating that the game then deletes.
            self.set(x, P.WALK, z, P.AIR)
            self.set(x, P.WALK + 1, z, P.AIR)
        self.set(x, P.GY, z, P.GLOW)
        self.set(x, P.WALK, z, "minecraft:moss_carpet")
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, z + dz)
            if (self.kind.get(n) in ("lawn", "grove", "wood", "bed", "shore")
                    and n not in self.paved and n not in self.occupied
                    and self.block(n[0], P.WALK, n[1]) == "minecraft:air"
                    and self.rng.random() < 0.6):
                self.set(n[0], P.WALK, n[1], "minecraft:moss_carpet")
        self.occupied.add((x, z))
        return True

    def hidden_lamps(self, pitch=12):
        n = 0
        for x, z in self.cells("lawn", "grove", "wood", "bed", "shore"):
            if (x + z) % pitch == 0 and (x - z) % pitch == 0:
                n += self.hidden_lamp(x, z)
        return n

    # -- the measurement ---------------------------------------------------

    def verify_light(self, floor):
        """Flood the built park with light and hand back what stays dark.

        Straight-line spacing is a promise about open ground; a park is
        trees, walls and a bridge. This is the check that makes the number
        mean something -- blocks not known to be transparent are treated as
        opaque, so the answer errs dark.
        """
        x0, x1 = P.X0 - 1, P.X1 + 1
        z0, z1 = P.Z0 - 1, P.Z1 + 1
        y0, y1 = 56, 72
        names = {}
        for z in range(z0, z1 + 1):
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    names[(x, y, z)] = self.block(x, y, z)

        level = {}
        q = deque()
        for pos, name in names.items():
            emit = LIGHT_SOURCES.get(name)
            if emit:
                level[pos] = emit
                q.append(pos)
        while q:
            x, y, z = q.popleft()
            lv = level[(x, y, z)]
            if lv <= 1:
                continue
            for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0),
                               (0, 0, 1), (0, 0, -1)):
                n = (x + dx, y + dy, z + dz)
                if n not in names or not clear_for_light(names[n]):
                    continue
                if level.get(n, 0) < lv - 1:
                    level[n] = lv - 1
                    q.append(n)

        dark = []
        for z in range(P.Z0, P.Z1 + 1):
            for x in range(P.X0, P.X1 + 1):
                k = self.kind.get((x, z))
                if k in (None, "locked", "water"):
                    continue
                # A trunk, a hedge or a roof is not somewhere to stand, and
                # measuring the dark inside one only invents work.
                if (x, z) in self.occupied:
                    continue
                for y in (P.WALK, P.WALK + 1, P.BRIDGE_DECK + 1):
                    if not clear_for_light(names.get((x, y, z), "")):
                        continue
                    if names.get((x, y - 1, z), "") in ("minecraft:air", ""):
                        continue
                    if level.get((x, y, z), 0) < floor:
                        dark.append((x, z))
                    break
        return sorted(set(dark))


# =============================================================================
# driver
# =============================================================================

GLYPH = {"lawn": ".", "grove": "T", "wood": "t", "bed": "f", "walk": ",",
         "path": "#", "plaza": "%", "water": "~", "shore": "-",
         "bridge": "=", "locked": "X"}


def print_map(kind):
    print("    " + "".join(str(abs(x) % 10) if x % 5 == 0 else " "
                           for x in range(P.X0, P.X1 + 1)))
    for z in range(P.Z0, P.Z1 + 1):
        print(f"{z:4d}" + "".join(GLYPH.get(kind.get((x, z)), "?")
                                  for x in range(P.X0, P.X1 + 1)))


#: The views worth having of a park: the whole thing from above, the two
#: halves close enough to see a flower, a floor plan that finds the paths a
#: canopy hides, and cutaways through the lake, the bridge and the fountain.
VIEWS = [
    ("whole", "area", (P.X0 - 2, P.Z0 - 2, P.X1 + 2, P.Z1 + 2), 4),
    ("north", "area", (P.X0 - 1, 172, P.X1 + 1, 248), 8),
    ("south", "area", (P.X0 - 1, 248, P.X1 + 1, 324), 8),
    ("plan62", "plan", (P.X0 - 1, P.Z0, P.X1 + 1, P.Z1), 4),
    ("cut-lake", "slice-x", (-18, 240, 280, 55, 75), 8),
    ("cut-bridge", "slice-z", (262, -39, 3, 55, 72), 8),
    ("cut-fountain", "slice-z", (288, -32, -4, 58, 75), 12),
    ("cut-grove", "slice-z", (190, -39, 3, 58, 78), 8),
    ("cut-cascade", "slice-x", (-35, 232, 252, 55, 72), 12),
    ("cut-bandstand", "slice-z", (226, -38, -22, 58, 74), 14),
    ("cut-pergola", "slice-z", (308, -28, -8, 58, 72), 14),
]


def preview(save, directory):
    from heaton_voxel.craft import render_area, render_plan, render_slice
    os.makedirs(directory, exist_ok=True)
    for name, kind, geom, scale in VIEWS:
        path = os.path.join(directory, f"{name}.png")
        if kind == "area":
            x0, z0, x1, z1 = geom
            render_area(save, (x0, 0, z0), (x1, 0, z1), path, scale=scale)
        elif kind == "plan":
            x0, z0, x1, z1 = geom
            render_plan(save, P.WALK, (x0, 0, z0), (x1, 0, z1), path,
                        scale=scale)
        else:
            axis = kind.split("-")[1]
            at, a0, a1, y0, y1 = geom
            c1 = (at, y0, a0) if axis == "x" else (a0, y0, at)
            c2 = (at, y1, a1) if axis == "x" else (a1, y1, at)
            render_slice(save, axis, at, c1, c2, path, scale=scale)
        print(f"  {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("world")
    ap.add_argument("--dry-run", action="store_true",
                    help="build in memory and validate, but write nothing")
    ap.add_argument("--map", action="store_true",
                    help="print the zone map and stop")
    ap.add_argument("--preview", metavar="DIR",
                    help="write area, plan and cutaway renders of the built "
                         "park; works under --dry-run, so the pictures come "
                         "before the world does")
    args = ap.parse_args()

    if args.map:
        print_map(classify()[0])
        return 0

    site = Worksite.open(args.world, (P.X0 - 4, P.Z0 - 4), (P.X1 + 4, P.Z1 + 4),
                         name="lush-park")
    for name, c1, c2 in P.PROTECT:
        site.protect(c1, c2, name=name)
    print("before:", site.render("before", scale=3))

    park = Park(site)
    trees_before = len(site.save.find_blocks(
        (P.X0, 60, P.Z0), (P.X1, 90, P.Z1),
        ["pale_oak_log", "creaking_heart"], match="name").matches)

    print("strip:");        park.strip()
    print("climate:");      park.reclimate()
    print("ground:");       park.ground()
    print("water:");        park.water()
    print("paving:");       park.paths()
    print("bridge:");       park.bridge_span()
    print("structures:");   park.structures()
    print("planting:");     park.planting()
    print("light:");        dark = park.lighting()

    print("audit:")
    park.audit()
    print("access, north gate:")
    n_step = park.access_report(range(-11, -6), range(175, 169, -1))
    print("access, south gate:")
    s_step = park.access_report(range(-20, -15), range(320, 327))
    if max(n_step, s_step) > 1:
        print(f"note: worst step at a gate is {max(n_step, s_step)} blocks")

    print("aftercare:", park.b.finish())

    trees_after = len(site.save.find_blocks(
        (P.X0, 60, P.Z0), (P.X1, 90, P.Z1),
        ["pale_oak_log", "creaking_heart"], match="name").matches)
    print(f"pale grove: {trees_before} trunk and heart blocks before, "
          f"{trees_after} after")
    if trees_after < trees_before:
        print("THE GROVE LOST BLOCKS; restoring snapshot")
        site.restore()
        return 1

    if args.preview:
        preview(site.save, args.preview)

    bad = site.check()
    if bad:
        print("PROTECTED ZONES VIOLATED:", bad)
        print("restoring snapshot; the build never happened")
        site.restore()
        return 1
    if dark:
        print(f"note: {len(dark)} cells still under the light floor, "
              f"first few {dark[:8]}")
    if args.dry_run:
        print("dry run: validation clean, nothing written")
        return 0
    written = site.save.save()
    print(f"files written: {written}")
    print("after:", site.render("after", scale=3))
    site.accept()
    return 0


if __name__ == "__main__":
    sys.exit(main())
