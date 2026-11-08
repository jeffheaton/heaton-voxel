"""Render a top-down map of a Minecraft world to a PNG.

    python examples/world_map.py <world> -o map.png
    python examples/world_map.py <world> --scale 4 --dimension minecraft:the_nether
    python examples/world_map.py <world> --no-shade --heightmap MOTION_BLOCKING

Every block becomes a `--scale` x `--scale` square of pixels (default 1). The
color is the topmost block in that column, looked up through the chunk's
heightmap rather than by scanning downwards -- about 90x faster, and the
heightmap is exactly the thing the game keeps for this purpose.

Colors:
    black     chunk was never generated
    magenta   chunk exists but could not be read (too old for this toolkit)
    anything  the topmost block, shaded by slope so relief is visible

The image is written a row at a time as it is generated, so memory stays flat
no matter how large the world is: a 13,824 x 17,920 map is 743 MB of raw
pixels but never more than one 512-block band is held at once.

Nothing here writes to the world.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from array import array

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heaton_voxel.blocks import Blocks, unpack_indices  # noqa: E402
from heaton_voxel.nbt import NBTError  # noqa: E402
from heaton_voxel.craft.render import (  # noqa: E402
    UNGENERATED,
    UNREADABLE,
    ColorMap,
    PNGWriter,
)
from heaton_voxel.world import OVERWORLD, MinecraftSave  # noqa: E402

REGION_BLOCKS = 512
CHUNK_BLOCKS = 16
HEIGHTMAP_BITS = 9
COLUMNS_PER_CHUNK = 256


# --------------------------------------------------------------------------
# Region grid overlay
# --------------------------------------------------------------------------

# A 5x7 bitmap font, just the glyphs a region file name needs. Drawing text
# without a font library means carrying the pixels, and this is all it takes.
_GLYPHS = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    "r": ("00000", "00000", "10110", "11001", "10000", "10000", "10000"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}
GLYPH_W, GLYPH_H = 5, 7
GRID_LINE = (255, 255, 255)
GRID_TEXT = (255, 255, 255)
GRID_SHADOW = (0, 0, 0)


def _put(band_rgb, width_blocks, x, y, rgb, blend=1.0):
    if not (0 <= x < width_blocks and 0 <= y < REGION_BLOCKS):
        return
    off = (y * width_blocks + x) * 3
    if blend >= 1.0:
        band_rgb[off], band_rgb[off + 1], band_rgb[off + 2] = rgb
    else:
        for c in range(3):
            band_rgb[off + c] = int(band_rgb[off + c] * (1 - blend) + rgb[c] * blend)


def _draw_text(band_rgb, width_blocks, x, y, text, size):
    """Draw `text` with a dark backing box so it reads over any terrain."""
    box_w = len(text) * (GLYPH_W + 1) * size + 2 * size
    box_h = GLYPH_H * size + 2 * size
    for by in range(box_h):
        for bx in range(box_w):
            _put(band_rgb, width_blocks, x + bx, y + by, GRID_SHADOW, 0.65)
    for index, char in enumerate(text):
        glyph = _GLYPHS.get(char, _GLYPHS[" "])
        ox = x + size + index * (GLYPH_W + 1) * size
        oy = y + size
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit != "1":
                    continue
                for py in range(size):
                    for px in range(size):
                        _put(band_rgb, width_blocks,
                             ox + gx * size + px, oy + gy * size + py, GRID_TEXT)


def _draw_grid(band_rgb, width_blocks, x_origin, region_z, present, text_size):
    """Region boundaries and the file name of each region, drawn in block
    space so the lines scale with --scale along with everything else."""
    # Vertical boundaries, and the horizontal one at the top of this band.
    for x in range(width_blocks):
        if (x_origin + x) % REGION_BLOCKS == 0:
            for y in range(REGION_BLOCKS):
                _put(band_rgb, width_blocks, x, y, GRID_LINE, 0.45)
    for x in range(width_blocks):
        _put(band_rgb, width_blocks, x, 0, GRID_LINE, 0.45)

    first_rx = (x_origin) // REGION_BLOCKS
    last_rx = (x_origin + width_blocks - 1) // REGION_BLOCKS
    for region_x in range(first_rx, last_rx + 1):
        if (region_x, region_z) not in present:
            continue  # no file to name
        left = region_x * REGION_BLOCKS - x_origin
        _draw_text(band_rgb, width_blocks, left + 3 * text_size, 3 * text_size,
                   f"r.{region_x}.{region_z}", text_size)


# --------------------------------------------------------------------------
# Which part of the world to draw
# --------------------------------------------------------------------------


def largest_cluster(coords, gap=2):
    """Group region coordinates into connected blobs, return the biggest.

    A world can contain a handful of region files millions of blocks from
    everything else -- someone visited the world border once. Their bounding
    box would be tens of millions of blocks across, nearly all of it empty, so
    the default is to draw the blob with the most regions in it.
    """
    remaining = set(coords)
    best = set()
    while remaining:
        seed = remaining.pop()
        blob = {seed}
        frontier = [seed]
        while frontier:
            cx, cz = frontier.pop()
            for other in list(remaining):
                if abs(other[0] - cx) <= gap and abs(other[1] - cz) <= gap:
                    remaining.discard(other)
                    blob.add(other)
                    frontier.append(other)
        if len(blob) > len(best):
            best = blob
    return best


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render(save, dimension_name, out_path, *, scale=1, shade=True,
           heightmap="WORLD_SURFACE", grid=False, grid_text=4, quiet=False):
    dim = save.dimension(dimension_name)
    if not dim.available:
        raise NBTError(f"{dimension_name} has no region files")

    keep = largest_cluster(set(dim.available))
    dropped = len(dim.available) - len(keep)
    rx0 = min(c[0] for c in keep); rx1 = max(c[0] for c in keep)
    rz0 = min(c[1] for c in keep); rz1 = max(c[1] for c in keep)

    width_blocks = (rx1 - rx0 + 1) * REGION_BLOCKS
    height_blocks = (rz1 - rz0 + 1) * REGION_BLOCKS
    x_origin, z_origin = rx0 * REGION_BLOCKS, rz0 * REGION_BLOCKS

    if not quiet:
        print(f"  dimension     : {dimension_name}")
        print(f"  regions       : {len(keep)} drawn"
              + (f", {dropped} far-flung ones skipped" if dropped else ""))
        print(f"  world bounds  : x {x_origin}..{x_origin + width_blocks - 1}, "
              f"z {z_origin}..{z_origin + height_blocks - 1}")
        print(f"  image         : {width_blocks * scale:,} x {height_blocks * scale:,} px")

    colors = ColorMap()
    stats = {"chunks": 0, "unreadable": 0, "missing": 0, "incomplete": 0}
    started = time.time()

    # One band = one row of region files. Never more than this is in memory.
    band_rgb = bytearray(width_blocks * REGION_BLOCKS * 3)
    band_h = array("h", bytes(width_blocks * REGION_BLOCKS * 2))
    NO_DATA = -32768
    carry = array("h", [NO_DATA]) * width_blocks

    with PNGWriter(out_path, width_blocks * scale, height_blocks * scale) as png:
        for region_z in range(rz0, rz1 + 1):
            _blank(band_rgb, band_h, NO_DATA)
            for region_x in range(rx0, rx1 + 1):
                if (region_x, region_z) not in keep:
                    continue
                _draw_region(save, dim, region_x, region_z, band_rgb, band_h,
                             x_origin, width_blocks, colors, stats, heightmap)
                # Free the region as soon as it has been drawn.
                dim.regions.pop((region_x, region_z), None)
                dim._blocks_cache.clear()

            if shade:
                _shade(band_rgb, band_h, carry, width_blocks, NO_DATA)
            # Grid last, so the labels are not dimmed by relief shading.
            if grid:
                _draw_grid(band_rgb, width_blocks, x_origin, region_z, keep,
                           grid_text)
            _emit(png, band_rgb, band_h, carry, width_blocks, scale, NO_DATA)

            if not quiet:
                done = region_z - rz0 + 1
                print(f"\r  rendering     : row {done}/{rz1 - rz0 + 1}, "
                      f"{stats['chunks']:,} chunks", end="", flush=True)
    if not quiet:
        print()
    stats["seconds"] = time.time() - started
    stats["unknown_blocks"] = colors.unknown
    stats["size"] = (width_blocks * scale, height_blocks * scale)
    return stats


def _blank(band_rgb, band_h, no_data):
    band_rgb[:] = bytes(len(band_rgb))
    for i in range(len(band_h)):
        band_h[i] = no_data


def _draw_region(save, dim, region_x, region_z, band_rgb, band_h,
                 x_origin, width_blocks, colors, stats, heightmap):
    base_cx, base_cz = region_x * 32, region_z * 32
    for local_cz in range(32):
        for local_cx in range(32):
            chunk_x, chunk_z = base_cx + local_cx, base_cz + local_cz
            px0 = chunk_x * CHUNK_BLOCKS - x_origin
            pz0 = (chunk_z * CHUNK_BLOCKS) % REGION_BLOCKS

            try:
                accessor = dim.blocks(chunk_x, chunk_z)
            except NBTError:
                stats["unreadable"] += 1
                _fill_chunk(band_rgb, px0, pz0, width_blocks, UNREADABLE)
                continue
            if accessor is None:
                stats["missing"] += 1
                continue

            data = accessor._data
            found = _column_heights(accessor, data, heightmap, stats)
            if found is None:
                stats["incomplete"] += 1
                chunk = dim.chunk(chunk_x, chunk_z)
                if chunk is not None:
                    chunk.release()
                dim._blocks_cache.pop((chunk_x, chunk_z), None)
                continue
            heights, floor = found
            _draw_chunk(accessor, heights, floor, band_rgb, band_h, px0, pz0,
                        width_blocks, colors)
            stats["chunks"] += 1
            chunk = dim.chunk(chunk_x, chunk_z)
            if chunk is not None:
                chunk.release()
            dim._blocks_cache.pop((chunk_x, chunk_z), None)


def _column_heights(accessor, data, heightmap, stats):
    """Top block y for each of the chunk's 256 columns."""
    maps = data.get("Heightmaps")
    if maps is None or heightmap not in maps:
        # A chunk the game began generating but never finished -- structures
        # planned, biomes assigned, no terrain yet. There is no surface to
        # draw, and scanning for one costs ~90x a heightmap lookup for a
        # result that is not real terrain anyway.
        return None
    raw = unpack_indices(maps[heightmap], COLUMNS_PER_CHUNK, HEIGHTMAP_BITS)
    # The stored value counts blocks from the bottom of the world, so the
    # topmost solid block sits one below it.
    floor = None
    if "OCEAN_FLOOR" in maps:
        floor = [
            accessor.min_y + v - 1
            for v in unpack_indices(maps["OCEAN_FLOOR"], COLUMNS_PER_CHUNK,
                                    HEIGHTMAP_BITS)
        ]
    return [accessor.min_y + v - 1 for v in raw], floor


def _draw_chunk(accessor, heights, floor, band_rgb, band_h, px0, pz0,
                width_blocks, colors):
    for index in range(COLUMNS_PER_CHUNK):
        lz, lx = divmod(index, CHUNK_BLOCKS)
        y = heights[index]
        try:
            state = accessor.get(lx, y, lz)
        except IndexError:
            continue
        name = state.name
        r, g, b = colors(name)
        if floor is not None and name == "minecraft:water":
            # Flat blue everywhere makes oceans unreadable; darken by depth so
            # shelves and trenches show.
            depth = y - floor[index]
            if depth > 0:
                k = 1.0 - min(depth, 32) / 44.0
                r, g, b = int(r * k), int(g * k), int(b * k)
        row = pz0 + lz
        offset = (row * width_blocks + px0 + lx) * 3
        band_rgb[offset] = r
        band_rgb[offset + 1] = g
        band_rgb[offset + 2] = b
        band_h[row * width_blocks + px0 + lx] = y


def _fill_chunk(band_rgb, px0, pz0, width_blocks, color):
    pixel = bytes(color)
    for lz in range(CHUNK_BLOCKS):
        start = ((pz0 + lz) * width_blocks + px0) * 3
        band_rgb[start:start + CHUNK_BLOCKS * 3] = pixel * CHUNK_BLOCKS


def _shade(band_rgb, band_h, carry, width_blocks, no_data):
    """Lighten or darken each pixel against the column to its north.

    This is what turns a flat color field into something you can read terrain
    from; without it a map is just biome blotches.
    """
    for row in range(REGION_BLOCKS):
        base = row * width_blocks
        for x in range(width_blocks):
            here = band_h[base + x]
            if here == no_data:
                continue
            north = carry[x] if row == 0 else band_h[base - width_blocks + x]
            if north == no_data:
                continue
            delta = here - north
            if not delta:
                continue
            factor = 1.16 if delta > 0 else 0.84
            if abs(delta) > 3:
                factor = 1.30 if delta > 0 else 0.72
            offset = (base + x) * 3
            for c in range(3):
                v = int(band_rgb[offset + c] * factor)
                band_rgb[offset + c] = 255 if v > 255 else v


def _emit(png, band_rgb, band_h, carry, width_blocks, scale, no_data):
    stride = width_blocks * 3
    for row in range(REGION_BLOCKS):
        line = bytes(band_rgb[row * stride:(row + 1) * stride])
        if scale > 1:
            line = b"".join(line[i:i + 3] * scale for i in range(0, len(line), 3))
        for _ in range(scale):
            png.write_row(line)
    base = (REGION_BLOCKS - 1) * width_blocks
    for x in range(width_blocks):
        carry[x] = band_h[base + x]


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("world", help="path to the save directory (holds level.dat)")
    parser.add_argument("-o", "--out", default="world_map.png", help="output PNG")
    parser.add_argument("--scale", type=int, default=1,
                        help="pixels per Minecraft block, square (default 1)")
    parser.add_argument("--dimension", default=OVERWORLD)
    parser.add_argument("--heightmap", default="WORLD_SURFACE",
                        choices=["WORLD_SURFACE", "MOTION_BLOCKING",
                                 "MOTION_BLOCKING_NO_LEAVES", "OCEAN_FLOOR"])
    parser.add_argument("--no-shade", action="store_true", help="flat colors")
    parser.add_argument("--grid", action="store_true",
                        help="overlay region boundaries labeled with the .mca file name")
    parser.add_argument("--grid-text", type=int, default=4,
                        help="label size multiplier for --grid (default 4)")
    parser.add_argument("--max-pixels", type=float, default=4e9,
                        help="refuse images larger than this many pixels")
    args = parser.parse_args(argv)

    if args.scale < 1:
        print("error: --scale must be at least 1", file=sys.stderr)
        return 1

    save = MinecraftSave.load(os.path.expanduser(args.world))
    print(save)

    dim = save.dimension(args.dimension)
    keep = largest_cluster(set(dim.available))
    if not keep:
        print(f"error: {args.dimension} has no region files", file=sys.stderr)
        return 1
    w = (max(c[0] for c in keep) - min(c[0] for c in keep) + 1) * REGION_BLOCKS * args.scale
    h = (max(c[1] for c in keep) - min(c[1] for c in keep) + 1) * REGION_BLOCKS * args.scale
    if w * h > args.max_pixels:
        print(f"error: that would be {w:,} x {h:,} = {w * h / 1e9:.1f}G pixels "
              f"({w * h * 3 / 1e9:.1f} GB raw). Lower --scale, or raise "
              f"--max-pixels if you really mean it.", file=sys.stderr)
        return 1

    stats = render(save, args.dimension, args.out, scale=args.scale,
                   shade=not args.no_shade, heightmap=args.heightmap,
                   grid=args.grid, grid_text=args.grid_text)

    print(f"  chunks drawn  : {stats['chunks']:,}")
    print(f"  unreadable    : {stats['unreadable']:,} (magenta)")
    if stats["incomplete"]:
        print(f"  part-generated: {stats['incomplete']:,} chunks skipped (no surface yet)")
    print(f"  elapsed       : {stats['seconds']:.1f}s")
    print(f"  wrote         : {args.out} "
          f"({os.path.getsize(args.out) / 1024 / 1024:.1f} MiB, "
          f"{stats['size'][0]:,} x {stats['size'][1]:,})")
    unknown = stats["unknown_blocks"]
    if unknown:
        top = sorted(unknown.items(), key=lambda kv: -kv[1])[:8]
        print(f"  unmapped block types: {len(unknown)} "
              f"(drawn gray; a stair, slab or wall counted as its block) "
              f"-- most common:")
        for name, count in top:
            print(f"      {count:>9,}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
