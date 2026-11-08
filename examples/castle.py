"""Raise an ornate stone castle -- moat, towers, gatehouse, keep and all -- sized to order.

    python examples/castle.py "~/Library/Application Support/minecraft/saves/my_world"
    python examples/castle.py <world> --width 96 --height 56
    python examples/castle.py <world> --at 120 -340 --y 64
    python examples/castle.py <world> --no-dungeon --dry-run

`--width` is the side of the square footprint the whole castle -- moat
included -- fits inside; `--height` is how far the keep rises above the
ground. Everything else is proportioned from those two numbers: curtain walls
with a crenellated wall-walk you can patrol end to end, round corner towers
with ladders, floors, arrow slits, machicolations and conical spires, a
gatehouse with a raised portcullis, murder holes, drawbridge and pennants, a
moat with a grass verge, and a central keep with a great hall, throne dais,
switchback stairs, arched windows, roof turrets and a flag. Beneath the keep,
unless `--no-dungeon`, a dungeon is cut into the ground: a torch- and
lantern-lit undercroft of pillars, barred cells, an iron-caged treasury, and
a stair (a ladder, in small keeps) leading down from the great hall. A fixed
`--seed` weathers the same stones the same way every run.

By default the castle is centered on where the player logged out: they wake
up on the floor of their own great hall, gate to the south. `--at X Z`
centers it anywhere else instead. The ground level is the median of the
terrain it replaces (`--y` overrides), the site is leveled, and everything
above it inside the footprint -- trees, hills -- is cleared. The foundation
follows the land rather than assuming it: every column is filled down to
solid ground (through water, to the seabed), and where the site overhangs
lower terrain the ground outside the footprint is graded down one block per
ring -- a 45-degree embankment of grass over dirt over stone -- until the
slope meets the hillside, so a castle on a ledge sits on an earthwork
instead of a floating slab. `--dry-run` first if unsure.

Close the world in Minecraft first. The game holds a lock on a world it has
open and keeps its own copy of the chunks in memory, so an edit made
underneath it is either refused (the default here) or thrown away when the
game next writes. Load the world *after* running this, not before.

Lighting is handed back to the game the only way that works: chunk light is
*baked* -- stored per section and trusted while the chunk's `isLightOn` flag
stands -- so a torch placed in a freshly cut dungeon would light nothing but
its own block, the darkness around it being stored data. Every touched chunk
and its neighbors (light crosses chunk borders) therefore has the flag
cleared and the stale arrays stripped, and Minecraft relights them on first
load. Heightmaps are refreshed for every column the build touches -- a
castle moves the surface by dozens of blocks, and a stale map would leave
mobs spawning on ground that no longer exists and `world_map.py` drawing the
meadow the castle replaced.

Chunks the world has never generated are reported and skipped, never
invented; a castle overlapping the edge of the generated world will say so
rather than quietly build half of itself.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
import time

# Make the modules importable when run from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heaton_voxel.blocks import BlockState, pack_indices, unpack_indices  # noqa: E402
from heaton_voxel.nbt import NBTError, world_is_open  # noqa: E402
from heaton_voxel.world import Dimension, FillResult, MinecraftSave  # noqa: E402

# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------

STONE = "minecraft:stone_bricks"
MOSSY = "minecraft:mossy_stone_bricks"
CRACKED = "minecraft:cracked_stone_bricks"
CHISELED = "minecraft:chiseled_stone_bricks"
COBBLE = "minecraft:cobblestone"
STAIR = "minecraft:stone_brick_stairs"
SLAB = "minecraft:stone_brick_slab"
ROOF = "minecraft:deepslate_tiles"
ROOF_SLAB = "minecraft:deepslate_tile_slab"
FLOOR = "minecraft:spruce_planks"
HALL_FLOOR = "minecraft:smooth_stone"
BRIDGE = "minecraft:dark_oak_planks"
BEAM = "minecraft:dark_oak_log"
AIR = "minecraft:air"
WATER = "minecraft:water"
GRASS = "minecraft:grass_block"
DIRT = "minecraft:dirt"
PATH = "minecraft:dirt_path"
FLAG = "minecraft:red_wool"
POLE = "minecraft:spruce_fence"
TORCH = "minecraft:torch"
LANTERN = "minecraft:lantern"

# The most a build reaches above its own ground level, past the keep: the
# roof turrets, then the flagpole and its pennant.
ABOVE_KEEP = 8
FOUNDATION_DEPTH = 8
CLEARANCE = 6  # air kept above the flag after clearing the site
FLOOR_H = 6  # one story of the keep, floor slab to floor slab

# The dungeon: its floor course sits this far below ground level, with this
# much air above the floor; the stair down needs one step per block of the
# drop, which is what ties the depth to the length of the flight.
DUNGEON_FLOOR = 11
DUNGEON_AIR = 6

# How far outside the footprint the ground may be graded down to meet lower
# terrain, at one block of drop per ring: enough for a serious cliff without
# terraforming half the map for a castle built on a floating island.
MAX_SKIRT = 64

# Cardinal directions, Minecraft convention: north is -z, east is +x.
NORTH, SOUTH, WEST, EAST = "north", "south", "west", "east"
_DIR = {NORTH: (0, -1), SOUTH: (0, 1), WEST: (-1, 0), EAST: (1, 0)}


def facing(dx: int, dz: int) -> str:
    """The cardinal direction nearest a vector, for stairs and torches.

    Diagonals resolve to the dominant axis; exact ties resolve to z (north or
    south), which keeps a square ring's corners consistent with its z-edges.
    """
    if abs(dx) > abs(dz):
        return EAST if dx > 0 else WEST
    return SOUTH if dz > 0 else NORTH


# --------------------------------------------------------------------------
# Shapes -- pure geometry, no world access
# --------------------------------------------------------------------------


def disc_offsets(r: int) -> list[tuple[int, int]]:
    """Offsets of a filled circle of radius ``r`` around (0, 0).

    ``dx*dx + dz*dz <= r*r + r`` is the usual Minecraft-style rounding: it
    keeps (±r, 0) and (0, ±r) in, fills the diagonals enough to look round at
    small radii, and is symmetric under all eight reflections.
    """
    limit = r * r + r
    return [
        (dx, dz)
        for dx in range(-r, r + 1)
        for dz in range(-r, r + 1)
        if dx * dx + dz * dz <= limit
    ]


def ring_offsets(r: int) -> list[tuple[int, int]]:
    """The one-block-thick rim of ``disc_offsets(r)``, ordered by angle.

    Ordered so that taking every other cell yields alternating merlons that
    march around the rim rather than jumping across it.
    """
    inner = set(disc_offsets(r - 1)) if r > 0 else set()
    rim = [cell for cell in disc_offsets(r) if cell not in inner]
    rim.sort(key=lambda cell: math.atan2(cell[1], cell[0]))
    return rim


def merlon_offsets(length: int) -> list[int]:
    """Offsets along a parapet that carry a merlon (the raised tooth).

    Both ends are always merlons -- a battlement that stops on a gap looks
    bitten off -- so an even length doubles up next to the far end rather
    than leaving the corner bare.
    """
    if length <= 0:
        return []
    teeth = list(range(0, length, 2))
    if teeth[-1] != length - 1:
        teeth.append(length - 1)
    return teeth


def weathered(rng: random.Random, wear: float = 1.0) -> str:
    """One stone of a weathered wall face. ``wear`` above 1 mosses it faster."""
    roll = rng.random()
    if roll < 0.08 * wear:
        return MOSSY
    if roll < 0.15 * wear:
        return CRACKED
    if roll < 0.18 * wear:
        return COBBLE
    return STONE


# --------------------------------------------------------------------------
# The plan -- every dimension derived from width and height
# --------------------------------------------------------------------------


class CastlePlan:
    """The proportions of one castle, derived from a footprint and a height.

    Pure numbers, no world access, so the geometry can be tested on its own.
    All lengths are in blocks; heights count up from ground level.
    """

    MIN_WIDTH, MAX_WIDTH = 24, 256
    MIN_HEIGHT, MAX_HEIGHT = 20, 160
    MOAT_FROM = 44  # narrowest footprint that digs a moat unasked

    def __init__(
        self,
        width: int,
        height: int,
        *,
        moat: bool | None = None,
        dungeon: bool = True,
    ):
        if not self.MIN_WIDTH <= width <= self.MAX_WIDTH:
            raise ValueError(
                f"--width must be {self.MIN_WIDTH}-{self.MAX_WIDTH}, got {width}: "
                f"below that nothing fits inside the walls, above it is a town"
            )
        if not self.MIN_HEIGHT <= height <= self.MAX_HEIGHT:
            raise ValueError(
                f"--height must be {self.MIN_HEIGHT}-{self.MAX_HEIGHT}, got {height}"
            )
        self.width = width
        self.height = height

        # The moat needs room: below MOAT_FROM it would squeeze the courtyard
        # to nothing, so it is dug only when asked for or when there is space.
        self.moat = moat if moat is not None else width >= self.MOAT_FROM
        if self.moat and width < 36:
            raise ValueError(
                f"--moat needs --width of at least 36, got {width}: the water "
                f"would displace the walls themselves"
            )
        self.moat_w = max(3, min(6, width // 12)) if self.moat else 0
        # Water starts one block in (the verge, which banks it) and a berm
        # separates it from the wall's own strip of ground.
        self.pad = self.moat_w + 2 if self.moat else 0

        self.tower_r = max(3, min(7, 3 + (width - self.MIN_WIDTH) // 16))
        self.wall_t = 2 if width < 48 else 3
        # Walls and towers keep their proportions to the keep: merlon tops at
        # ~2/5 of the height, tower terraces at ~3/5, spires above that.
        self.wall_h = max(8, min(height - 4, round(height * 0.42)))
        self.tower_h = max(self.wall_h + 4, min(height - 2, round(height * 0.62)))
        self.keep_h = height
        self.spires = self.tower_r >= 5

        # Inset from the footprint edge to the wall's outer face, which runs
        # through the tower centers so each tower shoulders both its walls.
        # One extra block keeps the towers' overhanging crowns (radius r + 1)
        # inside the footprint instead of spilling over its edge.
        self.inset = self.pad + self.tower_r + 1
        inner = width - 2 * (self.inset + self.wall_t)
        if inner < 11:
            raise ValueError(
                f"a width of {width} leaves only {inner} blocks inside the "
                f"walls; drop the moat or widen the footprint"
            )

        keep = max(7, min(inner - 4, round(width * 0.4)))
        self.keep_size = keep if keep % 2 else keep - 1  # odd centers the flag
        self.keep_t = 2 if self.keep_size >= 13 else 1
        self.grand_stairs = self.keep_size >= 15  # else a ladder shaft
        self.great_hall = self.keep_h >= 26  # double-height ground floor

        self.dungeon = dungeon
        hall = self.keep_size - 2 * self.keep_t  # the keep's interior span
        # The flight down needs DUNGEON_FLOOR - 1 steps plus a landing; a
        # hall too short for that gets a ladder shaft instead.
        self.dungeon_stairs = dungeon and hall >= DUNGEON_FLOOR + 2
        self.dungeon_rooms = dungeon and hall >= 15  # cells and the treasury

        self.gate_w = 3 if width < 40 else 5
        self.gate_h = max(4, min(7, self.wall_h - 5))
        self.gatehouse = width >= 36
        self.gatehouse_w = 4 if width < 56 else 5
        self.gatehouse_h = min(self.tower_h - 1, self.wall_h + 6)

    def floor_levels(self) -> list[int]:
        """Story floor heights above ground, the ground floor first.

        The great hall takes two stories when there is room; every floor
        keeps at least a parapet's worth of headroom below the roof deck.
        """
        levels = [0]
        y = FLOOR_H * 2 if self.great_hall else FLOOR_H
        while y <= self.keep_h - 5:
            levels.append(y)
            y += FLOOR_H
        return levels

    def describe(self) -> list[str]:
        parts = [
            f"footprint {self.width}x{self.width}, keep {self.keep_size}x"
            f"{self.keep_size} rising {self.keep_h}",
            f"curtain walls {self.wall_h} high and {self.wall_t} thick, "
            f"towers of radius {self.tower_r} rising {self.tower_h}"
            + (" with spires" if self.spires else ""),
            f"{len(self.floor_levels())} stories in the keep"
            + (" around a great hall" if self.great_hall else ""),
        ]
        if self.moat:
            parts.append(f"a moat {self.moat_w} wide")
        if self.gatehouse:
            parts.append("a gatehouse with drawbridge and portcullis")
        if self.dungeon:
            down = "a stair" if self.dungeon_stairs else "a ladder"
            what = (
                "pillared undercroft, cells and treasury"
                if self.dungeon_rooms
                else "undercroft"
            )
            parts.append(f"a dungeon beneath the keep ({what}, {down} down)")
        return parts


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


class BuildReport:
    """Everything the build did, and everything it could not do.

    Missing and unreadable chunks are kept apart for the usual reason: one
    means "nothing there to build on", the other means "something is there
    that this toolkit cannot see", and a builder must not confuse them.
    """

    def __init__(self):
        self.blocks_placed = 0
        self.blocks_out_of_range = 0
        self.missing_chunks: set[tuple[int, int]] = set()
        self.unreadable_chunks: dict[tuple[int, int], str] = {}
        self.chunks_touched: set[tuple[int, int]] = set()
        self.heightmap_chunks = 0
        self.relit_chunks = 0
        self.connected_blocks = 0
        # Vertical extent of everything placed, for the connect pass.
        self.y_low: int | None = None
        self.y_high: int | None = None

    def touch_y(self, y0: int, y1: int | None = None) -> None:
        if y1 is None:
            y1 = y0
        self.y_low = y0 if self.y_low is None else min(self.y_low, y0)
        self.y_high = y1 if self.y_high is None else max(self.y_high, y1)

    @property
    def complete(self) -> bool:
        return not (
            self.missing_chunks or self.unreadable_chunks or self.blocks_out_of_range
        )

    def absorb(self, result: FillResult) -> None:
        self.blocks_placed += result.blocks_filled
        self.blocks_out_of_range += result.blocks_out_of_range
        self.missing_chunks.update(result.missing_chunks)
        for where, why in result.unreadable_chunks:
            self.unreadable_chunks.setdefault(where, why)

    def summary(self) -> str:
        parts = [f"{self.blocks_placed:,} blocks over {len(self.chunks_touched)} chunks"]
        if self.heightmap_chunks:
            parts.append(f"heightmaps refreshed in {self.heightmap_chunks}")
        if self.connected_blocks:
            parts.append(f"{self.connected_blocks} states connected")
        if self.relit_chunks:
            parts.append(f"{self.relit_chunks} chunks marked for relight")
        if self.missing_chunks:
            parts.append(f"{len(self.missing_chunks)} chunks ungenerated")
        if self.unreadable_chunks:
            parts.append(f"{len(self.unreadable_chunks)} chunks unreadable")
        if self.blocks_out_of_range:
            parts.append(f"{self.blocks_out_of_range:,} blocks out of range")
        return ", ".join(parts)


# --------------------------------------------------------------------------
# The mason -- block placement with a tally
# --------------------------------------------------------------------------


class Mason:
    """Places blocks through a :class:`MinecraftSave`, keeping the score.

    Two jobs beyond forwarding: it records every column touched so the
    heightmaps can be refreshed afterwards, and it folds each operation's
    failures into one :class:`BuildReport` instead of losing them.

    Chunks are kept loaded across the whole build -- a castle revisits the
    same chunk hundreds of times, and releasing between visits would decode
    and re-encode it each round.
    """

    def __init__(self, save: MinecraftSave, dim: Dimension):
        self.save = save
        self.dim = dim
        self.report = BuildReport()
        self.columns: set[tuple[int, int]] = set()

    # -- placement ---------------------------------------------------------

    def set(self, x: int, y: int, z: int, block: str | BlockState) -> bool:
        chunk_x, chunk_z = x >> 4, z >> 4
        try:
            accessor = self.dim.blocks(chunk_x, chunk_z)
        except NBTError as exc:
            self.report.unreadable_chunks.setdefault((chunk_x, chunk_z), str(exc))
            return False
        if accessor is None:
            self.report.missing_chunks.add((chunk_x, chunk_z))
            return False
        try:
            accessor.set(x & 15, y, z & 15, block)
        except (IndexError, NBTError):
            # Outside the world's vertical range, or a section with no block
            # container -- both "no such place", counted rather than raised.
            self.report.blocks_out_of_range += 1
            return False
        self.report.blocks_placed += 1
        self.report.chunks_touched.add((chunk_x, chunk_z))
        self.report.touch_y(y)
        self.columns.add((x, z))
        return True

    def fill(
        self,
        corner1: tuple[int, int, int],
        corner2: tuple[int, int, int],
        block: str,
    ) -> None:
        result = self.save.fill(corner1, corner2, block, keep_loaded=True)
        self.report.absorb(result)
        x0, x1 = sorted((corner1[0], corner2[0]))
        z0, z1 = sorted((corner1[2], corner2[2]))
        self.report.touch_y(*sorted((corner1[1], corner2[1])))
        for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
            for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
                if (chunk_x, chunk_z) not in self.report.missing_chunks:
                    self.report.chunks_touched.add((chunk_x, chunk_z))
        self.columns.update(
            (x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)
        )

    def column(self, x: int, z: int, y0: int, y1: int, block: str) -> None:
        self.fill((x, y0, z), (x, y1, z), block)

    def fill_columns(
        self, spans: list[tuple[int, int, int, int]], block: str | BlockState
    ) -> None:
        """Fill vertical runs ``(x, z, y0, y1)``, one repack per section.

        Terrain-following fills are ragged -- every column has its own
        depth -- and filling them one column at a time would unpack and
        repack each section's whole index array per column. Grouped, a
        cliff face of a thousand columns costs one repack per section.
        """
        if isinstance(block, str):
            block = BlockState.parse(block)
        per_chunk: dict[tuple[int, int], list[tuple[int, int, int, int]]] = {}
        for x, z, y0, y1 in spans:
            if y1 >= y0:
                per_chunk.setdefault((x >> 4, z >> 4), []).append((x, z, y0, y1))
                self.report.touch_y(y0, y1)
        for (chunk_x, chunk_z), runs in sorted(per_chunk.items()):
            try:
                accessor = self.dim.blocks(chunk_x, chunk_z)
            except NBTError as exc:
                self.report.unreadable_chunks.setdefault((chunk_x, chunk_z), str(exc))
                continue
            if accessor is None:
                self.report.missing_chunks.add((chunk_x, chunk_z))
                continue
            by_section: dict[int, list[int]] = {}
            for x, z, y0, y1 in runs:
                lx, lz = x & 15, z & 15
                for section_y in range(y0 >> 4, (y1 >> 4) + 1):
                    base = section_y * 16
                    lo, hi = max(y0, base), min(y1, base + 15)
                    section = accessor.sections.get(section_y)
                    if section is None or section.blocks is None:
                        self.report.blocks_out_of_range += hi - lo + 1
                        continue
                    by_section.setdefault(section_y, []).extend(
                        (y & 15) * 256 + lz * 16 + lx for y in range(lo, hi + 1)
                    )
                self.columns.add((x, z))
            if by_section:
                self.report.chunks_touched.add((chunk_x, chunk_z))
            for section_y, positions in by_section.items():
                self.report.blocks_placed += accessor.sections[
                    section_y
                ].blocks.set_many(positions, block)

    # -- aftercare ---------------------------------------------------------

    def finish(self) -> None:
        """Prune, connect, refresh heightmaps, and hand light to the game."""
        for chunk_x, chunk_z in sorted(self.report.chunks_touched):
            try:
                accessor = self.dim.blocks(chunk_x, chunk_z)
            except NBTError:
                continue
            if accessor is None:
                continue
            accessor.prune()
            if self._refresh_heightmaps(accessor, chunk_x, chunk_z):
                self.report.heightmap_chunks += 1
        self._connect_blocks()
        self._reset_lighting()

    def _connect_blocks(self) -> None:
        """Settle neighbor-derived states over everything the build placed.

        Fences, panes, iron bars and stairs store their connections and
        corner shapes; the game only derives them on a block update, so a
        build that skipped this would show the portcullis as disconnected
        bars until something nudged it.
        """
        if not self.report.chunks_touched or self.report.y_low is None:
            return
        xs = [chunk_x for chunk_x, _ in self.report.chunks_touched]
        zs = [chunk_z for _, chunk_z in self.report.chunks_touched]
        result = self.save.connect_blocks(
            (min(xs) * 16, self.report.y_low - 1, min(zs) * 16),
            (max(xs) * 16 + 15, self.report.y_high + 1, max(zs) * 16 + 15),
            dimension=self.dim.name,
        )
        self.report.connected_blocks = result.total_updated

    def _reset_lighting(self) -> None:
        """Hand lighting back to the game for everything the build changed.

        The heavy thinking lives in :meth:`MinecraftSave.reset_lighting` --
        see its docstring for why baked light must be reset at all. The
        touched chunks form a solid rectangle for a castle, so its bounding
        box is the honest area; the library adds the neighbor ring, since
        light crosses chunk borders.
        """
        if not self.report.chunks_touched:
            return
        xs = [chunk_x for chunk_x, _ in self.report.chunks_touched]
        zs = [chunk_z for _, chunk_z in self.report.chunks_touched]
        result = self.save.reset_lighting(
            (min(xs) * 16, 0, min(zs) * 16),
            (max(xs) * 16 + 15, 0, max(zs) * 16 + 15),
            dimension=self.dim.name,
        )
        self.report.relit_chunks = result.chunks_reset

    # The game's heightmap predicates, approximated for the blocks this build
    # places and the terrain it stands on, and verified against genuine game
    # output in the fixture. WORLD_SURFACE is anything that is not air.
    # MOTION_BLOCKING is "solid or fluid": decorative plants and torches pass,
    # water blocks, and the inherently waterlogged sea plants count as their
    # water. OCEAN_FLOOR is the solid predicate alone -- leaves included,
    # which is the game's actual behavior, surprising as it reads. Only
    # columns this build changed are rewritten, so a mismatch on some exotic
    # block elsewhere cannot leak in.
    _PASSABLE = {
        TORCH,
        "minecraft:wall_torch",
        "minecraft:ladder",
        "minecraft:poppy",
        "minecraft:dandelion",
        "minecraft:cornflower",
        "minecraft:oxeye_daisy",
        "minecraft:short_grass",
        "minecraft:grass",  # pre-1.20.3 name, still common in worlds
        "minecraft:tall_grass",
        "minecraft:fern",
        "minecraft:large_fern",
        "minecraft:dead_bush",
        "minecraft:bush",
        "minecraft:leaf_litter",
        "minecraft:vine",
        "minecraft:sugar_cane",
    }
    _AIRISH = {AIR, "minecraft:cave_air", "minecraft:void_air"}
    _FLUIDS = {WATER, "minecraft:lava", "minecraft:bubble_column"}
    # Sea plants carry their water inside the block state.
    _WET_PLANTS = {
        "minecraft:kelp",
        "minecraft:kelp_plant",
        "minecraft:seagrass",
        "minecraft:tall_seagrass",
    }
    _MAP_NAMES = (
        "WORLD_SURFACE",
        "MOTION_BLOCKING",
        "MOTION_BLOCKING_NO_LEAVES",
        "OCEAN_FLOOR",
    )

    def _refresh_heightmaps(self, accessor, chunk_x: int, chunk_z: int) -> bool:
        """Recompute the four surface maps for the columns this build touched.

        Untouched columns keep the game's own values verbatim -- the merge is
        per entry -- so the approximation above only ever applies to ground
        this build itself rearranged.
        """
        chunk = self.dim.chunk(chunk_x, chunk_z)
        if chunk is None or chunk.data is None:
            return False
        maps = chunk.data.get("Heightmaps")
        if not maps:
            return False  # partly generated; never invent a surface

        base_x, base_z = chunk_x * 16, chunk_z * 16
        locals_touched = [
            (x - base_x, z - base_z)
            for (x, z) in self.columns
            if x >> 4 == chunk_x and z >> 4 == chunk_z
        ]
        if not locals_touched:
            return False

        present = [name for name in self._MAP_NAMES if name in maps]
        if not present:
            return False
        min_y, max_y = accessor.min_y, accessor.max_y
        bits = max(1, (max_y - min_y + 1).bit_length())

        # One pass down each column. Palette entries are classified once per
        # section, so the walk is list lookups rather than NBT decoding.
        surfaces: dict[str, dict[tuple[int, int], int]] = {n: {} for n in present}
        unset = object()
        section_cache: dict[int, object] = {}

        for lx, lz in locals_touched:
            found = dict.fromkeys(present, 0)
            remaining = len(found)
            for y in range(max_y, min_y - 1, -1):
                cached = section_cache.get(y >> 4, unset)
                if cached is unset:
                    cached = self._classified_section(accessor, y >> 4)
                    section_cache[y >> 4] = cached
                if cached is None:
                    continue
                indices, classes = cached
                hits = classes[indices[(y & 15) * 256 + lz * 16 + lx]]
                height = y - min_y + 1
                for name, hit in zip(self._MAP_NAMES, hits):
                    if hit and found.get(name) == 0:
                        found[name] = height
                        remaining -= 1
                if not remaining:
                    break
            for name in present:
                surfaces[name][(lx, lz)] = found[name]

        for name, heights in surfaces.items():
            values = unpack_indices(maps[name], 256, bits)
            for (lx, lz), height in heights.items():
                values[lz * 16 + lx] = height
            maps[name] = pack_indices(values, 256, bits)
        # The accessor may have unpacked the old maps already; drop that so
        # a later surface_y on this same accessor sees what was just written.
        accessor.drop_heightmap_cache()
        return True

    def _classified_section(self, accessor, section_y: int):
        """A section's indices plus, per palette entry, which maps it counts
        for, in ``_MAP_NAMES`` order."""
        section = accessor.sections.get(section_y)
        if section is None or section.blocks is None:
            return None
        container = section.blocks
        classes = []
        for state in container.palette:
            name = state.name
            airish = name in self._AIRISH
            fluid = (
                name in self._FLUIDS
                or name in self._WET_PLANTS
                or state.properties.get("waterlogged") == "true"
            )
            solid = not airish and not (
                name in self._FLUIDS
                or name in self._WET_PLANTS
                or name in self._PASSABLE
            )
            leaves = name.endswith("_leaves")
            classes.append(
                (
                    not airish,  # WORLD_SURFACE
                    solid or fluid,  # MOTION_BLOCKING
                    (solid and not leaves) or fluid,  # ..._NO_LEAVES
                    solid,  # OCEAN_FLOOR: fluids ignored, leaves not
                )
            )
        if not any(entry[0] for entry in classes):
            return None  # all air: skip the whole section in one step
        return container.indices(), classes


# --------------------------------------------------------------------------
# The build itself
# --------------------------------------------------------------------------


class Castle:
    """One castle being raised at a site.

    ``ground`` is the y of the surface block the courtyard ends up at: lawns
    are at ``ground``, feet at ``ground + 1``, and every height in the plan
    counts up from there. The footprint's north-west corner is ``(x0, z0)``;
    the gate is always in the south wall, facing +z.
    """

    def __init__(
        self,
        mason: Mason,
        plan: CastlePlan,
        x0: int,
        z0: int,
        ground: int,
        rng: random.Random,
    ):
        self.m = mason
        self.p = plan
        self.x0, self.z0 = x0, z0
        self.x1 = x0 + plan.width - 1
        self.z1 = z0 + plan.width - 1
        self.ground = ground
        self.rng = rng

        # The walls' outer faces, running through the tower centers.
        self.wx0 = x0 + plan.inset  # west face
        self.wx1 = self.x1 - plan.inset  # east face
        self.wz0 = z0 + plan.inset  # north face
        self.wz1 = self.z1 - plan.inset  # south face (the gate side)
        self.walk = ground + plan.wall_h - 2  # wall-walk surface course

        k = plan.keep_size
        self.kx0 = x0 + (plan.width - k) // 2
        self.kz0 = z0 + (plan.width - k) // 2
        self.kx1 = self.kx0 + k - 1
        self.kz1 = self.kz0 + k - 1
        self.keep_top = ground + plan.keep_h

        self.gate_x0 = x0 + (plan.width - plan.gate_w) // 2
        self.gate_x1 = self.gate_x0 + plan.gate_w - 1

        self.towers = [
            (self.wx0, self.wz0),
            (self.wx1, self.wz0),
            (self.wx0, self.wz1),
            (self.wx1, self.wz1),
        ]

    # -- small helpers -----------------------------------------------------

    def stair(self, x: int, y: int, z: int, toward: str, *, top: bool = False) -> None:
        """A stair leaning against mass in the ``toward`` direction.

        That one rule covers every use here: a plinth leans on its wall, a
        corbel hangs with its stub against its tower, and an arch corner
        leans on its jamb.
        """
        half = "top" if top else "bottom"
        self.m.set(x, y, z, f"{STAIR}[facing={toward},half={half}]")

    def pane(self, x: int, y: int, z: int, along_x: bool, block: str = "minecraft:glass_pane") -> None:
        props = "east=true,west=true" if along_x else "north=true,south=true"
        self.m.set(x, y, z, f"{block}[{props}]")

    def sprinkle(self, x: int, y: int, z: int, wear: float = 1.0) -> None:
        """Weather one face stone. Only writes when the roll says to."""
        block = weathered(self.rng, wear)
        if block != STONE:
            self.m.set(x, y, z, block)

    def weather_face(self, cells, y0: int, y1: int) -> None:
        """Weather a wall face, mossier toward the ground."""
        span = max(1, y1 - y0)
        for x, z in cells:
            for y in range(y0, y1 + 1):
                self.sprinkle(x, y, z, 1.6 - (y - y0) / span)

    def pennant(self, x: int, y: int, z: int, height: int = 3) -> None:
        """A fence-pole with a little red pennant streaming east."""
        self.m.fill((x, y, z), (x, y + height - 1, z), POLE)
        top = y + height - 1
        self.m.set(x + 1, top, z, FLAG)
        self.m.set(x + 2, top, z, FLAG)
        self.m.set(x + 1, top - 1, z, FLAG)

    def in_gate_span(self, x: int) -> bool:
        return self.gate_x0 - 1 <= x <= self.gate_x1 + 1

    def land_below(self, x: int, z: int) -> int | None:
        """The real ground at a column: the seabed under water, else the
        surface. Heightmaps are unpacked once per chunk and cached on the
        accessor, and Mason only rewrites them in ``finish()`` -- so a stage
        can ask about ground an earlier stage already buried and still see
        the original hill.
        """
        return self.m.dim.surface_y(x, z, "OCEAN_FLOOR")

    def in_wall_band(self, x: int, z: int) -> bool:
        """Is this column inside one of the four curtain wall bodies?"""
        t = self.p.wall_t
        along_x = self.wx0 <= x <= self.wx1
        along_z = self.wz0 <= z <= self.wz1
        if along_x and (z - self.wz0 < t or self.wz1 - z < t):
            return True
        return along_z and (x - self.wx0 < t or self.wx1 - x < t)

    # -- stages ------------------------------------------------------------

    def build(self, progress=lambda label: None) -> None:
        stages = [
            ("leveling the site", self.terraform, True),
            ("grading the approaches", self.grade_approaches, True),
            ("digging the moat", self.dig_moat, self.p.moat),
            ("raising the curtain walls", self.raise_walls, True),
            ("raising the corner towers", self.raise_towers, True),
            ("building the gatehouse", self.build_gatehouse, True),
            ("raising the keep", self.raise_keep, True),
            ("furnishing the keep", self.furnish_keep, True),
            ("cutting the dungeon", self.dig_dungeon, self.p.dungeon),
            ("laying out the courtyard", self.lay_courtyard, True),
        ]
        for label, stage, wanted in stages:
            if not wanted:
                continue  # skipped work is not announced
            progress(label)
            stage()

    def terraform(self) -> None:
        """Level the footprint: clear the sky, lay ground down to the land.

        The flat slab is enough where the site is level. Where the ground
        falls away underneath -- a ledge, a lake, the open sea -- each
        column continues past the slab to the real floor, so no part of
        the castle stands on air or water.
        """
        g = self.ground
        top = self.keep_top + ABOVE_KEEP + CLEARANCE
        self.m.fill((self.x0, g + 1, self.z0), (self.x1, top, self.z1), AIR)
        self.m.fill(
            (self.x0, g - FOUNDATION_DEPTH, self.z0),
            (self.x1, g - 4, self.z1),
            "minecraft:stone",
        )
        self.m.fill((self.x0, g - 3, self.z0), (self.x1, g - 1, self.z1), DIRT)
        self.m.fill((self.x0, g, self.z0), (self.x1, g, self.z1), GRASS)

        deep = []
        slab_bottom = g - FOUNDATION_DEPTH
        for x in range(self.x0, self.x1 + 1):
            for z in range(self.z0, self.z1 + 1):
                floor = self.land_below(x, z)
                if floor is not None and floor + 1 < slab_bottom:
                    deep.append((x, z, floor + 1, slab_bottom - 1))
        self.m.fill_columns(deep, "minecraft:stone")

    def grade_approaches(self) -> None:
        """Taper the ground outside the footprint down onto the land.

        A site on a ledge would otherwise end in a sheer artificial face
        hanging over the hillside below. Instead the ground steps down one
        block per ring outward -- a 45-degree embankment -- with every
        column filled from the real floor up, so the slope stands on the
        terrain rather than over it. It stops by itself on each side as
        soon as a whole ring meets ground at its own height, which on the
        uphill side of a ledge is immediately.
        """
        g = self.ground
        grass, dirt, stone = [], [], []
        for ring in range(1, MAX_SKIRT + 1):
            target = g - ring
            needed = False
            for x, z in self.ring_outside(ring):
                floor = self.land_below(x, z)
                if floor is None or floor >= target:
                    continue
                needed = True
                grass.append((x, z, target, target))
                dirt.append((x, z, max(floor + 1, target - 3), target - 1))
                stone.append((x, z, floor + 1, target - 4))
            if not needed:
                break
        self.m.fill_columns(stone, "minecraft:stone")
        self.m.fill_columns(dirt, DIRT)
        self.m.fill_columns(grass, GRASS)

    def ring_outside(self, distance: int):
        """The columns exactly ``distance`` blocks outside the footprint."""
        x0, x1 = self.x0 - distance, self.x1 + distance
        z0, z1 = self.z0 - distance, self.z1 + distance
        for x in range(x0, x1 + 1):
            yield x, z0
            yield x, z1
        for z in range(z0 + 1, z1):
            yield x0, z
            yield x1, z

    def dig_moat(self) -> None:
        """A ring of standing water one block in from the footprint edge.

        The outermost ring stays grass: every water cell then has a bank
        inside the footprint, so nothing pours over a terrain drop just
        outside it when the chunk next ticks.
        """
        if not self.p.moat:
            return
        g = self.ground
        m0, m1 = 1, self.p.moat_w  # insets of the water ring
        boxes = [
            ((self.x0 + m0, self.z0 + m0), (self.x1 - m0, self.z0 + m1)),  # north
            ((self.x0 + m0, self.z1 - m1), (self.x1 - m0, self.z1 - m0)),  # south
            ((self.x0 + m0, self.z0 + m1), (self.x0 + m1, self.z1 - m1)),  # west
            ((self.x1 - m1, self.z0 + m1), (self.x1 - m0, self.z1 - m1)),  # east
        ]
        for (bx0, bz0), (bx1, bz1) in boxes:
            self.m.fill((bx0, g - 2, bz0), (bx1, g - 2, bz1), "minecraft:stone")
            self.m.fill((bx0, g - 1, bz0), (bx1, g, bz1), WATER)

    def raise_walls(self) -> None:
        """Curtain walls with plinth, arrow slits and a crenellated walk."""
        p, g = self.p, self.ground
        t = p.wall_t
        walk = self.walk

        bodies = [
            (self.wx0, self.wz0, self.wx1, self.wz0 + t - 1),  # north
            (self.wx0, self.wz1 - t + 1, self.wx1, self.wz1),  # south
            (self.wx0, self.wz0, self.wx0 + t - 1, self.wz1),  # west
            (self.wx1 - t + 1, self.wz0, self.wx1, self.wz1),  # east
        ]
        for bx0, bz0, bx1, bz1 in bodies:
            self.m.fill((bx0, g, bz0), (bx1, walk, bz1), STONE)

        # Everything between the towers: weathering first (so it cannot fill
        # a carved slit back in), then parapet, merlons, plinth and slits.
        # The x and z spans have the same length (the footprint is square)
        # but different origins, so each wall must count along its own axis.
        span0x = self.wx0 + p.tower_r + 1
        span0z = self.wz0 + p.tower_r + 1
        length = (self.wx1 - p.tower_r - 1) - span0x + 1
        teeth = set(merlon_offsets(length))

        # (outer face cell for step i, inward direction, is the gate wall)
        walls = [
            (lambda i: (span0x + i, self.wz0), SOUTH, False),  # north wall
            (lambda i: (span0x + i, self.wz1), NORTH, True),  # south wall
            (lambda i: (self.wx0, span0z + i), EAST, False),  # west wall
            (lambda i: (self.wx1, span0z + i), WEST, False),  # east wall
        ]
        for cell, inward, has_gate in walls:
            dx, dz = _DIR[inward]
            self.weather_face([cell(i) for i in range(length)], g + 1, walk)
            for i in range(length):
                x, z = cell(i)
                gate_here = has_gate and self.in_gate_span(x)
                self.m.set(x, walk + 1, z, STONE)  # parapet
                if i in teeth:
                    self.m.set(x, walk + 2, z, STONE)
                    if i % 8 == 4:
                        self.m.set(x, walk + 3, z, TORCH)
                if t >= 3:
                    self.m.set(x + dx * (t - 1), walk + 1, z + dz * (t - 1), SLAB)
                if not gate_here:
                    # Plinth stair on the ground outside, leaning on the wall.
                    self.stair(x - dx, g + 1, z - dz, inward)
                    if i % 6 == 3:  # arrow slit: a recess in the outer course
                        self.m.fill((x, walk - 4, z), (x, walk - 3, z), AIR)

    def raise_towers(self) -> None:
        """Four round towers: hollow, laddered, machicolated, crowned."""
        p, g = self.p, self.ground
        r = p.tower_r
        top = g + p.tower_h
        shell = ring_offsets(r)
        hollow = disc_offsets(r - 1)

        for cx, cz in self.towers:
            for dx, dz in disc_offsets(r):
                self.m.column(cx + dx, cz + dz, g - 2, top, STONE)
            for dx, dz in hollow:
                self.m.column(cx + dx, cz + dz, g + 1, top, AIR)
            for dx, dz in shell:
                self.weather_face([(cx + dx, cz + dz)], g + 1, top)
            for dx, dz in ring_offsets(r + 1):  # plinth around the base
                if not self.in_wall_band(cx + dx, cz + dz):
                    self.stair(cx + dx, g + 1, cz + dz, facing(-dx, -dz))

            # Arrow slits at two heights on the faces looking away from the
            # castle -- through the shell, so they are real openings.
            out_x = WEST if cx == self.wx0 else EAST
            out_z = NORTH if cz == self.wz0 else SOUTH
            for direction in (out_x, out_z):
                dx, dz = _DIR[direction]
                for base in (g + p.wall_h // 2, top - 6):
                    self.m.fill(
                        (cx + dx * r, base, cz + dz * r),
                        (cx + dx * r, base + 1, cz + dz * r),
                        AIR,
                    )

            # Floors at ground, wall-walk level and just under the terrace,
            # each with a lantern; the wall-walk floor is what makes the
            # circuit walkable straight through the tower.
            floors = [g, self.walk, top - 4]
            for level in floors:
                for dx, dz in hollow:
                    self.m.set(cx + dx, level, cz + dz, FLOOR)
                self.m.set(cx, level + 1, cz, LANTERN)

            # Machicolations: corbels under an overhanging crown, then the
            # terrace floor, its parapet, and the spire if there is room.
            crown = ring_offsets(r + 1)
            for dx, dz in crown:
                self.stair(cx + dx, top, cz + dz, facing(-dx, -dz), top=True)
            for dx, dz in disc_offsets(r + 1):
                self.m.set(cx + dx, top + 1, cz + dz, STONE)
            for i, (dx, dz) in enumerate(crown):
                self.m.set(cx + dx, top + 2, cz + dz, STONE)
                if i % 2 == 0:
                    self.m.set(cx + dx, top + 3, cz + dz, STONE)
            if p.spires:
                self.spire(cx, cz, top + 2, r - 2)

            # The ladder threads every floor, up through the terrace. It
            # hugs the courtyard-side shell, where no slit can cut its
            # backing, and goes in last so nothing overwrites it.
            ladder_dz = 1 if cz == self.wz0 else -1  # inward
            ladder_z = cz + ladder_dz * (r - 1)
            ladder_face = NORTH if ladder_dz > 0 else SOUTH
            for level in floors:
                self.m.set(cx, level, ladder_z, AIR)
            self.m.set(cx, top + 1, ladder_z, AIR)  # through the terrace
            for y in range(g + 1, top + 2):
                self.m.set(cx, y, ladder_z, f"minecraft:ladder[facing={ladder_face}]")

        # With every shell standing, cut the wall-walk through each tower so
        # the parapet circuit connects: a doorway-height passage from the
        # tower's center out to where the curtain wall meets it.
        t = p.wall_t
        for cx, cz in self.towers:
            along_x = EAST if cx == self.wx0 else WEST
            along_z = SOUTH if cz == self.wz0 else NORTH
            for direction in (along_x, along_z):
                dx, dz = _DIR[direction]
                for step in range(0, r + 1):
                    if direction in (EAST, WEST):
                        px = cx + dx * step
                        cells = [
                            (px, self.wz0 + o if cz == self.wz0 else self.wz1 - o)
                            for o in range(t)
                        ]
                    else:
                        pz = cz + dz * step
                        cells = [
                            (self.wx0 + o if cx == self.wx0 else self.wx1 - o, pz)
                            for o in range(t)
                        ]
                    for qx, qz in cells:
                        self.m.fill((qx, self.walk + 1, qz), (qx, self.walk + 3, qz), AIR)

    def spire(self, cx: int, cz: int, base_y: int, r: int) -> None:
        """A conical slate roof, two courses per step, capped with a slab."""
        y = base_y
        for radius in range(r, 0, -1):
            for dx, dz in ring_offsets(radius):
                self.m.set(cx + dx, y, cz + dz, ROOF)
                self.m.set(cx + dx, y + 1, cz + dz, ROOF)
            y += 2
        self.m.fill((cx, base_y, cz), (cx, y, cz), ROOF)
        self.m.set(cx, y + 1, cz, ROOF_SLAB)

    def build_gatehouse(self) -> None:
        """The south gate: arch, portcullis, murder holes, bridge, turrets."""
        p, g = self.p, self.ground
        t = p.wall_t
        gx0, gx1 = self.gate_x0, self.gate_x1
        z_out = self.wz1
        z_in = z_out - t + 1

        # The passage, floored in cobbles, arched at the top corners.
        self.m.fill((gx0, g, z_in), (gx1, g, z_out), COBBLE)
        self.m.fill((gx0, g + 1, z_in), (gx1, g + p.gate_h, z_out), AIR)
        for z in range(z_in, z_out + 1):
            self.stair(gx0, g + p.gate_h, z, WEST, top=True)
            self.stair(gx1, g + p.gate_h, z, EAST, top=True)
        self.m.set((gx0 + gx1) // 2, g + p.gate_h + 1, z_out, CHISELED)  # keystone

        # Portcullis, raised: bars across the top two courses of the arch.
        for x in range(gx0, gx1 + 1):
            self.pane(x, g + p.gate_h - 1, z_out - 1, True, "minecraft:iron_bars")
            if x not in (gx0, gx1):  # the arch stairs own the top corners
                self.pane(x, g + p.gate_h, z_out - 1, True, "minecraft:iron_bars")

        # Murder holes in the passage ceiling, if there is mass above.
        if p.wall_h - p.gate_h >= 4:
            mid_z = (z_in + z_out) // 2
            for x in (gx0 + 1, gx1 - 1):
                self.m.fill(
                    (x, g + p.gate_h + 1, mid_z), (x, g + p.gate_h + 2, mid_z), AIR
                )

        # An overhanging machicolation box above the gate front.
        for x in range(gx0 - 1, gx1 + 2):
            self.stair(x, self.walk, z_out + 1, NORTH, top=True)
            self.m.set(x, self.walk + 1, z_out + 1, STONE)
            self.m.set(x, self.walk + 2, z_out + 1, STONE)
        for i, x in enumerate(range(gx0 - 1, gx1 + 2)):
            if i % 2 == 0:
                self.m.set(x, self.walk + 3, z_out + 1, STONE)

        if p.gatehouse:
            self.gatehouse_turrets()

        # Drawbridge over the moat, chained to beams beside the arch.
        if p.moat:
            deck_z1 = self.z1
            self.m.fill((gx0 - 1, g, z_out + 1), (gx1 + 1, g, deck_z1), BRIDGE)
            for x in (gx0 - 1, gx1 + 1):
                for z in range(z_out + 2, deck_z1, 2):
                    self.m.set(x, g + 1, z, f"{POLE}[north=true,south=true]")
            beam_y = g + p.gate_h + 3
            chain_z = self.z1 - p.moat_w // 2
            for x in (gx0 - 1, gx1 + 1):
                self.m.fill((x, beam_y, z_out + 1), (x, beam_y, chain_z), f"{BEAM}[axis=z]")
                for y in range(g + 2, beam_y):
                    self.m.set(x, y, chain_z, "minecraft:iron_chain[axis=y]")

    def gatehouse_turrets(self) -> None:
        """Square turrets flanking the gate, crowned and flying pennants."""
        p, g = self.p, self.ground
        t = p.wall_t
        w = p.gatehouse_w
        top = g + p.gatehouse_h
        z_out = self.wz1
        tz0, tz1 = z_out - t - 1, z_out + 2

        for tx0 in (self.gate_x0 - 1 - w, self.gate_x1 + 2):
            tx1 = tx0 + w - 1
            ctx, ctz = (tx0 + tx1) // 2, (tz0 + tz1) // 2
            self.m.fill((tx0, g - 2, tz0), (tx1, top, tz1), STONE)
            self.weather_face([(x, tz1) for x in range(tx0, tx1 + 1)], g + 1, top)
            for base in (g + p.wall_h // 2, top - 4):
                self.m.fill((ctx, base, tz1), (ctx, base + 1, tz1), AIR)

            # The wall-walk tunnels straight through, keeping the circuit.
            self.m.fill(
                (tx0, self.walk + 1, z_out - t + 1),
                (tx1, self.walk + 3, z_out),
                AIR,
            )

            # Crown: corbels, an overhanging cap, merlons, then the pennant.
            rim = (
                [(x, tz0 - 1) for x in range(tx0 - 1, tx1 + 2)]
                + [(x, tz1 + 1) for x in range(tx0 - 1, tx1 + 2)]
                + [(tx0 - 1, z) for z in range(tz0, tz1 + 1)]
                + [(tx1 + 1, z) for z in range(tz0, tz1 + 1)]
            )
            for x, z in rim:
                self.stair(x, top, z, facing(ctx - x, ctz - z), top=True)
            self.m.fill((tx0 - 1, top + 1, tz0 - 1), (tx1 + 1, top + 1, tz1 + 1), STONE)
            for i, (x, z) in enumerate(rim):
                self.m.set(x, top + 2, z, STONE)
                if i % 2 == 0:
                    self.m.set(x, top + 3, z, STONE)
            self.pennant(ctx, top + 2, ctz)

    def raise_keep(self) -> None:
        """The keep's shell: walls, pilasters, cornices, windows, entrance."""
        p, g = self.p, self.ground
        kx0, kz0, kx1, kz1 = self.kx0, self.kz0, self.kx1, self.kz1
        t = p.keep_t
        top = self.keep_top

        self.m.fill((kx0, g - 3, kz0), (kx1, top, kz1), STONE)
        self.m.fill((kx0 + t, g + 1, kz0 + t), (kx1 - t, top - 1, kz1 - t), AIR)
        self.m.fill((kx0 + t, g, kz0 + t), (kx1 - t, g, kz1 - t), HALL_FLOOR)

        for face in (
            [(x, kz0) for x in range(kx0 + 1, kx1)],
            [(x, kz1) for x in range(kx0 + 1, kx1)],
            [(kx0, z) for z in range(kz0 + 1, kz1)],
            [(kx1, z) for z in range(kz0 + 1, kz1)],
        ):
            self.weather_face(face, g + 1, top - 1)

        # Chiseled corner pilasters, and a slab string course per story.
        for x, z in ((kx0, kz0), (kx0, kz1), (kx1, kz0), (kx1, kz1)):
            self.m.column(x, z, g + 1, top, CHISELED)
        for level in p.floor_levels()[1:]:
            y = g + level - 1
            for x in range(kx0, kx1 + 1):
                self.m.set(x, y, kz0 - 1, f"{SLAB}[type=top]")
                self.m.set(x, y, kz1 + 1, f"{SLAB}[type=top]")
            for z in range(kz0, kz1 + 1):
                self.m.set(kx0 - 1, y, z, f"{SLAB}[type=top]")
                self.m.set(kx1 + 1, y, z, f"{SLAB}[type=top]")

        self.keep_windows()
        self.keep_entrance()
        self.keep_roof()

    def window_offsets(self) -> list[int]:
        """Window centers along one keep face, symmetric about the middle."""
        k = self.p.keep_size
        count = max(1, (k - 8) // 6)
        return [round((i + 1) * k / (count + 1)) for i in range(count)]

    def keep_windows(self) -> None:
        """Arrow slits at ground level, arched glazing on every floor above."""
        p, g = self.p, self.ground
        kx0, kz0, kx1, kz1 = self.kx0, self.kz0, self.kx1, self.kz1
        t = p.keep_t

        for offset in self.window_offsets():
            for level in p.floor_levels():
                base = g + level + 2
                if level == 0:
                    # Ground floor: plain slits through every face.
                    self.m.fill((kx0 + offset, base, kz0), (kx0 + offset, base + 1, kz0 + t - 1), AIR)
                    self.m.fill((kx0 + offset, base, kz1 - t + 1), (kx0 + offset, base + 1, kz1), AIR)
                    self.m.fill((kx0, base, kz0 + offset), (kx0 + t - 1, base + 1, kz0 + offset), AIR)
                    self.m.fill((kx1 - t + 1, base, kz0 + offset), (kx1, base + 1, kz0 + offset), AIR)
                    continue
                if base + 3 > self.keep_top - 2:
                    continue
                # Upper floors: two-wide arched windows, glazed, all faces.
                for face_z, glaze_z in ((kz0, kz0), (kz1, kz1)):
                    x = kx0 + offset
                    z_lo = face_z if face_z == kz0 else face_z - t + 1
                    z_hi = face_z + t - 1 if face_z == kz0 else face_z
                    self.m.fill((x - 1, base, z_lo), (x, base + 2, z_hi), AIR)
                    for z in range(z_lo, z_hi + 1):
                        self.stair(x - 1, base + 2, z, WEST, top=True)
                        self.stair(x, base + 2, z, EAST, top=True)
                    for wx in (x - 1, x):
                        for wy in (base, base + 1):
                            self.pane(wx, wy, glaze_z, True)
                for face_x in (kx0, kx1):
                    z = kz0 + offset
                    x_lo = face_x if face_x == kx0 else face_x - t + 1
                    x_hi = face_x + t - 1 if face_x == kx0 else face_x
                    self.m.fill((x_lo, base, z - 1), (x_hi, base + 2, z), AIR)
                    for x in range(x_lo, x_hi + 1):
                        self.stair(x, base + 2, z - 1, NORTH, top=True)
                        self.stair(x, base + 2, z, SOUTH, top=True)
                    for wz in (z - 1, z):
                        for wy in (base, base + 1):
                            self.pane(face_x, wy, wz, False)

    def keep_entrance(self) -> None:
        """A grand arched doorway on the south face, stepped and torch-lit."""
        g = self.ground
        cx = (self.kx0 + self.kx1) // 2
        z1 = self.kz1
        z0 = z1 - self.p.keep_t + 1
        self.m.fill((cx - 1, g + 1, z0), (cx + 1, g + 4, z1), AIR)
        for z in range(z0, z1 + 1):
            self.stair(cx - 1, g + 4, z, WEST, top=True)
            self.stair(cx + 1, g + 4, z, EAST, top=True)
        self.m.set(cx, g + 5, z1, CHISELED)
        for x in (cx - 2, cx + 2):
            self.m.set(x, g + 3, z1 + 1, "minecraft:wall_torch[facing=south]")
        for x in range(cx - 1, cx + 2):
            self.stair(x, g + 1, z1 + 1, NORTH)

    def keep_roof(self) -> None:
        """Cornice, parapet, corner turrets, and the flag over everything."""
        kx0, kz0, kx1, kz1 = self.kx0, self.kz0, self.kx1, self.kz1
        top = self.keep_top

        # Cornice: corbels just under the roofline, all the way round.
        for x in range(kx0, kx1 + 1):
            self.stair(x, top - 1, kz0 - 1, SOUTH, top=True)
            self.stair(x, top - 1, kz1 + 1, NORTH, top=True)
        for z in range(kz0, kz1 + 1):
            self.stair(kx0 - 1, top - 1, z, EAST, top=True)
            self.stair(kx1 + 1, top - 1, z, WEST, top=True)

        # Parapet with merlons around the deck edge.
        edge = (
            [(x, kz0) for x in range(kx0, kx1 + 1)]
            + [(x, kz1) for x in range(kx0, kx1 + 1)]
            + [(kx0, z) for z in range(kz0 + 1, kz1)]
            + [(kx1, z) for z in range(kz0 + 1, kz1)]
        )
        for x, z in edge:
            self.m.set(x, top + 1, z, STONE)
            if (x + z) % 2 == 0:
                self.m.set(x, top + 2, z, STONE)

        # Squat turrets on the roof corners.
        for cx, cz in (
            (kx0 + 1, kz0 + 1),
            (kx0 + 1, kz1 - 1),
            (kx1 - 1, kz0 + 1),
            (kx1 - 1, kz1 - 1),
        ):
            self.m.fill((cx - 1, top + 1, cz - 1), (cx + 1, top + 3, cz + 1), STONE)
            for dx, dz in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
                self.m.set(cx + dx, top + 4, cz + dz, STONE)
            self.m.set(cx, top + 4, cz, SLAB)

        # The flag: a tall pole dead center, pennant streaming east.
        cx = (kx0 + kx1) // 2
        cz = (kz0 + kz1) // 2
        pole_top = top + ABOVE_KEEP - 1
        self.m.fill((cx, top + 1, cz), (cx, pole_top, cz), POLE)
        for i, length in enumerate((3, 2, 1)):
            for dx in range(1, length + 1):
                self.m.set(cx + dx, pole_top - i, cz, FLAG)

    def furnish_keep(self) -> None:
        """Floors, stairs, hall, dais and light inside the shell."""
        p, g = self.p, self.ground
        t = p.keep_t
        ix0, iz0 = self.kx0 + t, self.kz0 + t  # interior corners
        ix1, iz1 = self.kx1 - t, self.kz1 - t
        levels = p.floor_levels()

        for level in levels[1:]:
            self.m.fill((ix0, g + level, iz0), (ix1, g + level, iz1), FLOOR)

        if p.grand_stairs:
            self.keep_stairs(levels)
        else:
            # A small keep gets a ladder shaft in the north-east corner.
            lx, lz = ix1, iz0
            for level in levels[1:]:
                self.m.set(lx, g + level, lz, AIR)
            for y in range(g + 1, g + levels[-1] + 2):
                self.m.set(lx, y, lz, "minecraft:ladder[facing=south]")

        # The great hall: carpet aisle, log columns, a dais with a throne.
        hall_top = g + levels[1] if len(levels) > 1 else self.keep_top
        cx = (self.kx0 + self.kx1) // 2
        for z in range(iz0 + 2, iz1):
            self.m.set(cx, g + 1, z, "minecraft:red_carpet")
        if p.keep_size >= 17:
            for x in (ix0 + 2, ix1 - 2):
                for z in (iz0 + 3, iz1 - 3):
                    self.m.column(x, z, g + 1, hall_top - 1, "minecraft:stripped_spruce_log[axis=y]")
                    self.m.set(x, g, z, CHISELED)

        # Dais against the north wall, throne facing the entrance.
        dais_z = iz0 + 1
        self.m.fill((cx - 2, g + 1, iz0), (cx + 2, g + 1, dais_z), "minecraft:polished_andesite")
        for x in range(cx - 2, cx + 3):
            self.stair(x, g + 1, dais_z + 1, NORTH)
        self.m.set(cx, g + 2, iz0, f"{STAIR}[facing=south,half=bottom]")
        for x in (cx - 1, cx + 1):
            self.m.set(x, g + 2, iz0, "minecraft:cobblestone_wall")

        # Light: sconces along each story's walls (offset from the window
        # columns, whose slits would leave a torch with nothing to hang on),
        # and lanterns on chains down the center of the hall.
        for level in levels:
            y = g + level + 3
            for offset in self.window_offsets():
                x = min(self.kx0 + offset + 2, ix1 - 1)
                self.m.set(x, y, iz0, "minecraft:wall_torch[facing=south]")
                self.m.set(x, y, iz1, "minecraft:wall_torch[facing=north]")
        chain = 2 if p.great_hall else 1
        for z in (iz0 + (iz1 - iz0) // 3, iz1 - (iz1 - iz0) // 3):
            for link in range(1, chain + 1):
                self.m.set(cx, hall_top - link, z, "minecraft:iron_chain[axis=y]")
            self.m.set(cx, hall_top - chain - 1, z, f"{LANTERN}[hanging=true]")

    def keep_stairs(self, levels: list[int]) -> None:
        """Switchback stairs against the east wall, floor to floor.

        Each run rises ``FLOOR_H`` over a zone eight blocks long. Runs
        alternate direction **and columns**: the northbound ones hug the
        wall, the southbound ones stand beside them. Sharing one pair of
        columns put every flight's treads, and the stone under them, into
        the head room of the flight below, so with three floors or more no
        flight but the last could be climbed.

        The floor above keeps its landing cells and is holed only where a
        climber's head passes through it -- three rows, not two. Walking
        *down*, the game moves you sideways before it drops you, and for
        that tick your head is in the third block above the tread you are
        stepping onto; two rows is a stair you climb and cannot come back
        down. ``HEAD_ROOM`` in ``heaton_voxel.craft.build`` is the same
        three.
        """
        g = self.ground
        ix1 = self.kx1 - self.p.keep_t
        iz1 = self.kz1 - self.p.keep_t
        up_xs = (ix1 - 1, ix1)  # northbound runs, hugging the east wall
        down_xs = (ix1 - 3, ix1 - 2)  # southbound runs, beside them
        zB = iz1  # south end of the zone
        zA = zB - FLOOR_H - 1  # north end

        for index in range(len(levels) - 1):
            y_from = g + levels[index]
            rise = levels[index + 1] - levels[index]
            northward = index % 2 == 0  # first flight climbs from the south
            toward = NORTH if northward else SOUTH
            xs = up_xs if northward else down_xs
            for step in range(1, rise):
                z = zB - step if northward else zA + step
                y = y_from + step
                for x in xs:
                    self.m.fill((x, y_from + 1, z), (x, y - 1, z), STONE)
                    self.stair(x, y, z, toward)
            y_above = g + levels[index + 1]
            for step in (rise - 3, rise - 2, rise - 1):  # where heads pass
                z = zB - step if northward else zA + step
                for x in xs:
                    self.m.set(x, y_above, z, AIR)

    def dig_dungeon(self) -> None:
        """An undercroft cut into the ground beneath the keep.

        The room is a sealed masonry box carved back out -- sealed first so
        that whatever the cut runs into (caves, gravel, water pockets) meets
        a finished wall rather than an open room. Floor course at
        ``ground - DUNGEON_FLOOR``, with the keep's own base as the roof.
        At scale it gets a pillared hall, barred cells along the north wall,
        an iron-caged treasury in the south-east corner, and a walled stair
        descending from the great hall; small keeps get a ladder shaft.
        Lanterns and torches go in with it -- the game will relight, but a
        dungeon with no light source stays black forever.
        """
        p, g = self.p, self.ground
        if not p.dungeon:
            return
        t = p.keep_t
        ix0, iz0 = self.kx0 + t, self.kz0 + t
        ix1, iz1 = self.kx1 - t, self.kz1 - t
        slab = g - DUNGEON_FLOOR  # the course you stand on
        air0, air1 = slab + 1, slab + DUNGEON_AIR
        ceiling = air1 + 1

        self.m.fill((ix0 - 1, slab - 1, iz0 - 1), (ix1 + 1, ceiling, iz1 + 1), STONE)
        self.m.fill((ix0, air0, iz0), (ix1, air1, iz1), AIR)
        for x in range(ix0, ix1 + 1):  # a floor worn by centuries of feet
            for z in range(iz0, iz1 + 1):
                if self.rng.random() < 0.25:
                    self.m.set(x, slab, z, self.rng.choice((MOSSY, CRACKED, COBBLE)))

        hall_z0 = iz0 + 5 if p.dungeon_rooms else iz0 + 2
        pillars: set[tuple[int, int]] = set()
        if ix1 - ix0 + 1 >= 11:
            for x in range(ix0 + 4, ix1 - 2, 5):
                for z in range(hall_z0 + 1, iz1 - 2, 5):
                    pillars.add((x, z))
                    self.m.column(x, z, air0, air1, STONE)
                    self.m.set(x, air0, z, CHISELED)
                    self.m.set(x, air1, z, CHISELED)
                    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        self.stair(x + dx, air1, z + dz, facing(-dx, -dz), top=True)

        if p.dungeon_rooms:
            # The stairway owns the north-west corner; the cells start east
            # of its side wall, or the first cell would be buried in the
            # flight's supporting mass.
            cells_x0 = ix0 + 3 if p.dungeon_stairs else ix0
            self.dungeon_cells(cells_x0, ix1, iz0, air0, air1)
            self.dungeon_treasury(ix1, iz1, air0, air1)

        # Light: a chandelier mid-hall (three at scale), torches on the walls.
        cx, cz = (ix0 + ix1) // 2, (iz0 + iz1) // 2
        spots = [(cx, cz)]
        if ix1 - ix0 + 1 >= 17:
            spots += [(cx - 6, cz), (cx + 6, cz)]
        for x, z in spots:
            if (x, z) in pillars:
                continue
            self.m.set(x, air1, z, "minecraft:iron_chain[axis=y]")
            self.m.set(x, air1 - 1, z, f"{LANTERN}[hanging=true]")
        torch_y = air0 + 3
        for z in range(iz0 + DUNGEON_FLOOR + 2, iz1 - 3, 5):
            self.m.set(ix0, torch_y, z, "minecraft:wall_torch[facing=east]")
        for z in range(hall_z0 + 2, iz1 - 3, 5):
            self.m.set(ix1, torch_y, z, "minecraft:wall_torch[facing=west]")
        for x in range(ix0 + 4, ix1 - 3, 5):
            self.m.set(x, torch_y, iz1, "minecraft:wall_torch[facing=north]")

        if p.dungeon_stairs:
            self.dungeon_stairway(ix0, iz0, air0, slab)
        else:
            lx, lz = ix0, iz0  # ladder shaft in the north-west corner
            self.m.fill((lx, air0, lz), (lx, g, lz), AIR)
            for y in range(air0, g + 1):
                self.m.set(lx, y, lz, "minecraft:ladder[facing=south]")
            self.m.set(lx + 1, air0 + 2, lz, "minecraft:wall_torch[facing=south]")

    def dungeon_cells(self, x_start: int, ix1: int, iz0: int, air0: int, air1: int) -> None:
        """Barred cells along the north wall, a doorway gap in each front."""
        front = iz0 + 3
        bars = "minecraft:iron_bars[east=true,west=true]"
        x = x_start
        while x + 2 <= ix1 - 1:
            if x + 3 <= ix1:  # partition east of the cell
                self.m.fill((x + 3, air0, iz0), (x + 3, air1, front), STONE)
            self.m.fill((x, air0 + 3, front), (x + 2, air1, front), STONE)  # lintel
            for bx in (x, x + 2):
                self.m.fill((bx, air0, front), (bx, air0 + 2, front), bars)
            self.m.fill((x + 1, air0, front), (x + 1, air0 + 2, front), AIR)
            self.m.fill((x, air1 - 1, iz0), (x, air1, iz0), "minecraft:iron_chain[axis=y]")
            self.m.set(x + 1, air0 + 2, iz0, "minecraft:wall_torch[facing=south]")
            x += 4

    def dungeon_treasury(self, ix1: int, iz1: int, air0: int, air1: int) -> None:
        """An iron cage over the south-east corner, gold showing through."""
        vx, vz = ix1 - 2, iz1 - 2  # the 3x3 room it encloses
        self.m.column(vx - 1, vz - 1, air0, air1, STONE)  # corner post
        for z in range(vz, iz1 + 1):
            self.m.fill(
                (vx - 1, air0, z), (vx - 1, air0 + 2, z),
                "minecraft:iron_bars[north=true,south=true]",
            )
            self.m.fill((vx - 1, air0 + 3, z), (vx - 1, air1, z), STONE)
        for x in range(vx + 1, ix1 + 1):  # the vx column stays open: the way in
            self.m.fill(
                (x, air0, vz - 1), (x, air0 + 2, vz - 1),
                "minecraft:iron_bars[east=true,west=true]",
            )
        self.m.fill((vx, air0 + 3, vz - 1), (ix1, air1, vz - 1), STONE)
        for x, y, z in (
            (ix1, air0, iz1),
            (ix1 - 1, air0, iz1),
            (ix1, air0, iz1 - 1),
            (ix1, air0 + 1, iz1),
        ):
            self.m.set(x, y, z, "minecraft:gold_block")
        self.m.set(ix1, air0 + 2, iz1, LANTERN)

    def dungeon_stairway(self, ix0: int, iz0: int, air0: int, slab: int) -> None:
        """A walled flight from the great hall down to the dungeon floor.

        Two blocks wide against the keep's west wall, descending south: one
        step per block of drop, headroom carved as it goes, a side wall
        closing it off from the hall it lands in, torch-lit on the way down.
        """
        g = self.ground
        xs = (ix0, ix0 + 1)
        z_top = iz0 + 1
        for x in xs:  # the opening in the great hall floor
            for z in (z_top, z_top + 1, z_top + 2):
                self.m.set(x, g, z, AIR)
        for z in (z_top - 1, z_top + 3):  # lit posts flanking the opening
            self.m.set(ix0 + 1, g + 1, z, "minecraft:cobblestone_wall")
            self.m.set(ix0 + 1, g + 2, z, TORCH)

        for step in range(1, DUNGEON_FLOOR):
            z = z_top + step - 1
            y = g - step
            for x in xs:
                self.m.fill((x, y + 1, z), (x, y + 3, z), AIR)
                if y - 1 >= air0:  # solid mass under the flight
                    self.m.fill((x, air0, z), (x, y - 1, z), STONE)
                self.stair(x, y, z, NORTH)
            self.m.fill((ix0 + 2, y, z), (ix0 + 2, y + 4, z), STONE)  # side wall
            if step % 3 == 1:
                self.m.set(ix0, y + 2, z, "minecraft:wall_torch[facing=east]")
        self.m.set(ix0, air0 + 2, z_top + DUNGEON_FLOOR - 1, "minecraft:wall_torch[facing=east]")

    def lay_courtyard(self) -> None:
        """Paths, lamps, a well and a scrap of garden between keep and walls."""
        p, g = self.p, self.ground
        cx = (self.gate_x0 + self.gate_x1) // 2

        # Path from the gate to the keep's front steps.
        for z in range(self.kz1 + 2, self.wz1 - p.wall_t + 1):
            for x in range(cx - 1, cx + 2):
                self.m.set(x, g, z, PATH)
            if self.rng.random() < 0.3:
                edge = cx - 2 if self.rng.random() < 0.5 else cx + 2
                self.m.set(edge, g, z, COBBLE)

        # Lamp posts flanking the path midway to the gate.
        lamp_z = (self.kz1 + self.wz1) // 2
        for x in (cx - 3, cx + 3):
            self.m.fill((x, g + 1, lamp_z), (x, g + 3, lamp_z), POLE)
            self.m.set(x, g + 4, lamp_z, LANTERN)

        # A well east of the path, roofed in slabs.
        wx = (self.kx1 + self.wx1) // 2
        wz = lamp_z
        if wx - 1 > self.kx1 + 1:
            self.m.fill((wx - 1, g - 2, wz - 1), (wx + 1, g - 1, wz + 1), "minecraft:stone")
            self.m.fill((wx, g - 1, wz), (wx, g, wz), WATER)
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    if dx or dz:
                        self.m.set(wx + dx, g + 1, wz + dz, COBBLE)
            for x in (wx - 1, wx + 1):
                self.m.fill((x, g + 2, wz), (x, g + 3, wz), POLE)
            for x in (wx - 1, wx, wx + 1):
                self.m.set(x, g + 4, wz, SLAB)

        # Garden west of the path: bushes and flowers on the lawn.
        gx = (self.kx0 + self.wx0) // 2
        wz = lamp_z
        if gx - 4 > self.wx0 + p.wall_t and gx + 4 < self.kx0:
            flowers = (
                "minecraft:poppy",
                "minecraft:dandelion",
                "minecraft:cornflower",
                "minecraft:oxeye_daisy",
            )
            for _ in range(max(6, p.width // 4)):
                x = gx + self.rng.randint(-3, 3)
                z = wz + self.rng.randint(-4, 4)
                if not (self.wx0 + p.wall_t < x < self.kx0 - 1):
                    continue
                if self.rng.random() < 0.3:
                    self.m.set(x, g + 1, z, "minecraft:oak_leaves[persistent=true]")
                else:
                    self.m.set(x, g + 1, z, self.rng.choice(flowers))


# --------------------------------------------------------------------------
# Site survey
# --------------------------------------------------------------------------


def survey_ground(
    dim: Dimension, x0: int, z0: int, width: int
) -> tuple[int | None, int, int]:
    """The median surface height across the footprint, from heightmaps.

    Returns ``(median, sampled, missing)``. Columns in chunks that are
    ungenerated, unreadable or too incomplete to carry a heightmap are
    counted, not guessed at -- a castle needs to know its ground, and a
    partly generated chunk genuinely has none yet.
    """
    heights = []
    missing = 0
    step = max(4, width // 12)
    samples = [
        (x0 + dx, z0 + dz)
        for dx in range(0, width, step)
        for dz in range(0, width, step)
    ]
    for x, z in samples:
        y = dim.surface_y(x, z)
        if y is None:
            missing += 1
        else:
            heights.append(y)
    if not heights:
        return None, len(samples), missing
    heights.sort()
    return heights[len(heights) // 2], len(samples), missing




# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("world", help="path to the save directory (holds level.dat)")
    parser.add_argument(
        "--width", type=int, default=64,
        help="side of the square footprint, moat included (default 64)",
    )
    parser.add_argument(
        "--height", type=int, default=44,
        help="how far the keep rises above the ground (default 44)",
    )
    parser.add_argument(
        "--at", type=int, nargs=2, metavar=("X", "Z"),
        help="center the castle here instead of near the player",
    )
    parser.add_argument(
        "--y", type=int, default=None,
        help="ground level to build at (default: median of the site's terrain)",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="weathering seed (default: derived from the site, so re-runs match)",
    )
    parser.add_argument(
        "--moat", dest="moat", action="store_true", default=None,
        help=f"dig a moat even below --width {CastlePlan.MOAT_FROM}",
    )
    parser.add_argument(
        "--no-moat", dest="moat", action="store_false",
        help="keep the ground dry regardless of width",
    )
    parser.add_argument(
        "--no-dungeon", dest="dungeon", action="store_false", default=True,
        help="leave the ground under the keep solid",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="build in memory, report, write nothing"
    )
    args = parser.parse_args(argv)

    try:
        plan = CastlePlan(args.width, args.height, moat=args.moat, dungeon=args.dungeon)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
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

    # Where: centered on --at, or on the player -- who ends up standing in
    # the middle of their own great hall when the world next loads.
    if args.at:
        center_x, center_z = args.at
        x0 = center_x - plan.width // 2
        z0 = center_z - plan.width // 2
    else:
        px, py, pz = save.player_block_position()
        print(f"player is at ({px}, {py}, {pz}) in {save.player_dimension()}")
        print("centering the castle on the player: they will wake in the great hall")
        x0 = px - plan.width // 2
        z0 = pz - plan.width // 2
    x1, z1 = x0 + plan.width - 1, z0 + plan.width - 1

    # How high: the terrain's own median unless told otherwise.
    if args.y is not None:
        ground = args.y
    else:
        ground, sampled, missing = survey_ground(dim, x0, z0, plan.width)
        if ground is None or missing > sampled // 3:
            print(
                f"error: no usable surface under the footprint ({missing} of "
                f"{sampled} sample columns have no heightmap -- ungenerated or "
                f"partly generated terrain). Generate the area in game first, "
                f"move the castle with --at, or force a level with --y.",
                file=sys.stderr,
            )
            return 1
        if missing:
            print(
                f"note: {missing} of {sampled} sample columns had no surface; "
                f"using the rest"
            )

    if args.at:
        # Centering elsewhere can still land on the player by accident.
        try:
            px, _, pz = save.player_block_position()
        except NBTError:
            px = pz = None  # a server world has no player to bury
        if px is not None and x0 <= px <= x1 and z0 <= pz <= z1:
            print(
                "warning: the player is inside the footprint and will wake up "
                "somewhere in the new castle.",
                file=sys.stderr,
            )

    seed = (
        args.seed
        if args.seed is not None
        else (x0 * 73856093 ^ z0 * 19349663) & 0xFFFFFFFF
    )
    rng = random.Random(seed)

    print(f"castle footprint ({x0}, {z0}) to ({x1}, {z1}), ground level y={ground}")
    print(f"  seed {seed}")
    for line in plan.describe():
        print(f"  {line}")

    # The sky above the site has to hold the keep, its flag and some air.
    top_needed = ground + plan.keep_h + ABOVE_KEEP + CLEARANCE
    try:
        probe = dim.blocks((x0 + plan.width // 2) >> 4, (z0 + plan.width // 2) >> 4)
    except NBTError:
        probe = None
    if probe is not None and top_needed > probe.max_y:
        print(
            f"error: the keep would top out at y={top_needed}, above the "
            f"world's ceiling of y={probe.max_y}. Lower --height or the site.",
            file=sys.stderr,
        )
        return 1

    mason = Mason(save, dim)
    castle = Castle(mason, plan, x0, z0, ground, rng)
    started = time.monotonic()
    castle.build(progress=lambda label: print(f"  {label}..."))
    print("  tidying palettes and refreshing heightmaps...")
    mason.finish()
    elapsed = time.monotonic() - started

    report = mason.report
    print(f"built in {elapsed:.1f}s: {report.summary()}")
    if not report.complete:
        print(
            f"  warning: incomplete -- {len(report.missing_chunks)} ungenerated "
            f"chunks, {len(report.unreadable_chunks)} unreadable chunks, "
            f"{report.blocks_out_of_range:,} blocks out of range. The castle "
            f"is missing wherever those fall.",
            file=sys.stderr,
        )
        for where, why in list(report.unreadable_chunks.items())[:3]:
            print(f"    chunk {where}: {why}", file=sys.stderr)

    if args.dry_run:
        print("  dry run: nothing written")
        return 0

    try:
        written = save.save()
    except NBTError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"  wrote {written} files")
    gate_x = (castle.gate_x0 + castle.gate_x1) // 2
    doorstep = (
        "walk in over the drawbridge" if plan.moat else "walk in through the arch"
    )
    print(
        f"\nThe gate is at ({gate_x}, {ground + 1}, {castle.wz1}), facing "
        f"south -- load the world and {doorstep}."
    )
    if plan.dungeon:
        way_down = (
            "a stair against the great hall's west wall"
            if plan.dungeon_stairs
            else "a ladder in the great hall's north-west corner"
        )
        print(f"The dungeon is below the keep; {way_down} leads down.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
