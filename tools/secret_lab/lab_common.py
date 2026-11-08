"""Shared kit for the secret-lab build: palette, mixes, small architecture.

Style: clean-room high tech. White and light-gray concrete panels over
polished-deepslate plinths, smooth quartz where it should gleam, tinted
and light-blue glass, iron and blackstone details, sea-lantern and
copper-bulb lighting, cyan accents, yellow/black hazard striping where
the campus means business.
"""

from __future__ import annotations

from heaton_voxel.nbt import TagByte, TagCompound, TagInt, TagString  # noqa: F401

AIR = "minecraft:air"
WATER = "minecraft:water"
GRASS = "minecraft:grass_block[snowy=false]"
DIRT = "minecraft:dirt"

# -- palette -----------------------------------------------------------------

PANEL = "minecraft:white_concrete"          # primary skin
PANEL2 = "minecraft:light_gray_concrete"    # secondary skin / banding
DECK = "minecraft:gray_concrete"            # roof decks, service floors
CARBON = "minecraft:black_concrete"         # accents, hazard dark
HAZARD = "minecraft:yellow_concrete"        # hazard light
CYAN = "minecraft:cyan_concrete"            # brand accent

QUARTZ = "minecraft:smooth_quartz"
QUARTZ_STAIR = "minecraft:smooth_quartz_stairs"
QUARTZ_SLAB = "minecraft:smooth_quartz_slab"
QPILLAR = "minecraft:quartz_pillar[axis=y]"
QCHISEL = "minecraft:chiseled_quartz_block"

SLATE = "minecraft:polished_deepslate"      # plinths, curbs
SLATE_SLAB = "minecraft:polished_deepslate_slab"
SLATE_STAIR = "minecraft:polished_deepslate_stairs"
SLATE_WALL = "minecraft:polished_deepslate_wall"
TILES = "minecraft:deepslate_tiles"         # dark flooring
DBRICK = "minecraft:deepslate_bricks"

BLACK = "minecraft:polished_blackstone"
BLACK_SLAB = "minecraft:polished_blackstone_slab"
BLACK_STAIR = "minecraft:polished_blackstone_stairs"
BLACK_WALL = "minecraft:polished_blackstone_wall"

STEEL = "minecraft:iron_block"
BARS = "minecraft:iron_bars"
IRON_DOOR = "minecraft:iron_door"
IRON_TRAP = "minecraft:iron_trapdoor"

GLASS = "minecraft:glass"
PANE = "minecraft:glass_pane"
TINTED = "minecraft:tinted_glass"           # one-way lab black glass
SKYGLASS = "minecraft:light_blue_stained_glass"
SKYPANE = "minecraft:light_blue_stained_glass_pane"

GLOW = "minecraft:sea_lantern"
BULB = "minecraft:waxed_copper_bulb[lit=true]"      # glowing stud
ROD_UP = "minecraft:end_rod[facing=up]"
FROG_W = "minecraft:pearlescent_froglight[axis=y]"  # cool white panel light
FROG_Y = "minecraft:ochre_froglight[axis=y]"        # warm panel light
LANTERN = "minecraft:lantern[hanging=false]"
LANTERN_H = "minecraft:lantern[hanging=true]"

SMOOTH = "minecraft:smooth_stone"
SMOOTH_SLAB = "minecraft:smooth_stone_slab"

# -- surface mixes -----------------------------------------------------------

LAB_ROAD_MIX = [(SLATE, 7), (TILES, 4), ("minecraft:gray_concrete", 2)]
WALK_MIX = [(QUARTZ, 8), (PANEL, 3), (PANEL2, 1)]
PLAZA_MIX = [(QUARTZ, 9), (PANEL, 3), (QCHISEL, 1)]
LAWN_MIX = [(GRASS, 1)]
SCORCH_MIX = [("minecraft:blackstone", 6), ("minecraft:cobbled_deepslate", 4),
              ("minecraft:basalt[axis=y]", 2), ("minecraft:coal_block", 1)]
RANGE_MIX = [("minecraft:gravel", 7), ("minecraft:coarse_dirt", 2),
             ("minecraft:cobbled_deepslate", 2)]

# -- geometry helpers --------------------------------------------------------

OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
DIRVEC = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}


def facing_of(dx: int, dz: int) -> str:
    if abs(dx) >= abs(dz):
        return "east" if dx > 0 else "west"
    return "south" if dz > 0 else "north"


def disc(cx, cz, r):
    for z in range(cz - r, cz + r + 1):
        for x in range(cx - r, cx + r + 1):
            if (x - cx) ** 2 + (z - cz) ** 2 <= r * r + r * 0.5:
                yield x, z


def ring(cx, cz, r):
    inner = (r - 1) ** 2 + (r - 1) * 0.5
    for x, z in disc(cx, cz, r):
        if (x - cx) ** 2 + (z - cz) ** 2 > inner:
            yield x, z


def rect_edge(x0, z0, x1, z1):
    for x in range(x0, x1 + 1):
        yield x, z0
        if z1 != z0:
            yield x, z1
    for z in range(z0 + 1, z1):
        yield x0, z
        if x1 != x0:
            yield x1, z


# -- small architecture ------------------------------------------------------

def paved_rect(b, x0, z0, x1, z1, mix, y) -> None:
    for z in range(z0, z1 + 1):
        for x in range(x0, x1 + 1):
            b.set(x, y, z, mixpick(b.rng, mix))
            b._ground[(x, z)] = y


def mixpick(rng, mix) -> str:
    total = sum(w for _, w in mix)
    pick = rng.randrange(total)
    for name, w in mix:
        pick -= w
        if pick < 0:
            return name
    return mix[0][0]


def lamp_post(b, x, z, y=None) -> None:
    """Campus light mast: blackstone stem, sea-lantern head, end-rod tip."""
    y = b.ground(x, z) if y is None else y
    b.set(x, y + 1, z, BLACK_WALL)
    b.set(x, y + 2, z, BLACK_WALL)
    b.set(x, y + 3, z, GLOW)
    b.set(x, y + 4, z, ROD_UP)


def bollard(b, x, z, y) -> None:
    """Low glowing path light."""
    b.set(x, y + 1, z, BLACK_WALL)
    b.set(x, y + 2, z, BULB)


def flight(b, x, z, y, dx, dz, n, *, stair=QUARTZ_STAIR, fill=PANEL2,
           width=1, clear=4) -> int:
    """A straight stair flight: ``n`` steps ascending +1 per advance along
    (dx, dz), ``width`` wide (extending perpendicular, +x or +z side).
    Returns the walk plane at the top. Solid ``fill`` goes under each step;
    headroom is cleared."""
    fx = facing_of(dx, dz)
    px, pz = (0, 1) if dx else (1, 0)
    for i in range(n):
        sx, sz, sy = x + dx * i, z + dz * i, y + 1 + i
        for w in range(width):
            wx, wz = sx + px * w, sz + pz * w
            b.fill((wx, sy - 3, wz), (wx, sy - 1, wz), fill)
            b.set(wx, sy, wz, f"{stair}[facing={fx},half=bottom]")
            b.fill((wx, sy + 1, wz), (wx, sy + clear, wz), AIR)
    return y + n


def rail_run(b, cells, y, rail=BLACK_WALL) -> None:
    for x, z in cells:
        b.set(x, y + 1, z, rail)


def box_shell(b, x0, z0, x1, z1, y0, height, *, wall=PANEL, floor=QUARTZ,
              ceiling=None) -> None:
    """Hollow box: floor plane at ``y0``, walls up ``height`` above it,
    ceiling plane at ``y0 + height + 1`` if given. Cut openings after."""
    b.fill((x0, y0, z0), (x1, y0, z1), floor)
    for y in range(y0 + 1, y0 + height + 1):
        for x, z in rect_edge(x0, z0, x1, z1):
            b.set(x, y, z, wall)
    b.fill((x0 + 1, y0 + 1, z0 + 1), (x1 - 1, y0 + height, z1 - 1), AIR)
    if ceiling is not None:
        b.fill((x0, y0 + height + 1, z0), (x1, y0 + height + 1, z1), ceiling)
    for x, z in ((x0, z0), (x0, z1), (x1, z0), (x1, z1)):
        b._ground[(x, z)] = max(b._ground.get((x, z), y0), y0)


def basement(b, x0, z0, x1, z1, floor_y, plane, *, wall=DECK,
             floor=TILES, light_every=4) -> None:
    """A lit basement under a building: sealed ring wall, floor at
    ``floor_y``, ceiling = the ground-floor slab at ``plane`` (kept).
    Interior air ``floor_y+1 .. plane-1``."""
    b.fill((x0, floor_y - 1, z0), (x1, floor_y - 1, z1), wall)
    b.fill((x0, floor_y, z0), (x1, floor_y, z1), floor)
    for y in range(floor_y + 1, plane):
        for x, z in rect_edge(x0, z0, x1, z1):
            b.set(x, y, z, wall)
    b.fill((x0 + 1, floor_y + 1, z0 + 1), (x1 - 1, plane - 1, z1 - 1), AIR)
    b.fill((x0, plane, z0), (x1, plane, z1), wall)   # ceiling slab
    for z in range(z0 + 2, z1 - 1, light_every):
        for x in range(x0 + 2, x1 - 1, light_every):
            b.set(x, plane - 1, z, GLOW)


def hazard_row(b, cells, y, *, start=0) -> None:
    for i, (x, z) in enumerate(cells):
        b.set(x, y, z, HAZARD if (i + start) % 2 == 0 else CARBON)


def curtain(b, x0, y0, z0, x1, y1, z1, glass=SKYGLASS) -> None:
    b.fill((x0, y0, z0), (x1, y1, z1), glass)


def ceil_lights(b, x0, z0, x1, z1, y, *, every=4, light=GLOW) -> None:
    for z in range(z0 + 1, z1, every):
        for x in range(x0 + 1, x1, every):
            b.set(x, y, z, light)


def tech_column(b, x, z, y0, h, *, shaft=QPILLAR, base=SLATE, cap=True) -> None:
    b.set(x, y0, z, base)
    for y in range(y0 + 1, y0 + h):
        b.set(x, y, z, shaft)
    if cap:
        b.set(x, y0 + h, z, BULB)


def hostile(b, identity, x, y, z, *, yaw=None, extra=None) -> None:
    """A hostile mob that never despawns (a specimen, not a spawn)."""
    tags = {"PersistenceRequired": TagByte(1)}
    if extra:
        tags.update(extra)
    b.spawn(identity, x, y, z, yaw=yaw, extra=tags)
