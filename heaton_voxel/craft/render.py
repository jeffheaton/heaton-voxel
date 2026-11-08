"""Quick pictures of a world, for eyes rather than pipelines.

Three renderers cover most "what is actually there?" questions, one per
axis you can hold still:

    from heaton_voxel.craft import render_area, render_slice, render_plan

    render_area(save, (x0, 0, z0), (x1, 0, z1), "site.png", scale=4)
    render_slice(save, "x", 169, (0, 55, 20), (0, 150, 164), "profile.png")
    render_plan(save, 66, (x0, 0, z0), (x1, 0, z1), "floor.png", scale=4)

:func:`render_area` is a top-down map of a box -- each column colored by its
surface block, shaded by slope, water darkened by depth. :func:`render_slice`
is a vertical cutaway along one plane -- the castle-and-dungeon cross-section
view -- with open sky and covered interior shaded differently so caves read
as caves. :func:`render_plan` is the third: a horizontal cut at one ``y``,
the architect's floor plan, which answers what an area map cannot -- where
the doors are, whether a wall is solid or has a gap in it, whether that room
is actually enclosed at head height. An area map draws the roof over all of
it; a slice shows one line through it; a plan shows the whole story.

Both build on two pieces that are useful on their own and shared with
``examples/world_map.py``: :class:`PNGWriter`, a streaming stdlib-only PNG
encoder, and :class:`ColorMap`, a best-effort block-to-RGB resolver over
the tables in :mod:`heaton_voxel.craft.colors`. The colors are game
knowledge, not format knowledge, and drift as Minecraft adds blocks:
unmatched names draw gray and are counted on the map object
(``colors.unknown``), most frequent first, so the table is easy to extend.
A stair, slab or wall the game has is drawn as the block it is cut from,
and counted as that block when it has no color.

There is a CLI: ``python -m heaton_voxel render <world> --area X0 Z0 X1 Z1``.
Nothing here writes to the world.
"""

from __future__ import annotations

import collections
import struct
import zlib

# Every name that means "nothing here": the game's own air tag. The
# renderers shade those by what stands above them rather than coloring
# them, so a room reads as a room.
from ..blocks import AIR_BLOCKS
from ..nbt import NBTError
from .block_facts import holds_water
from .blockinfo import cut_shapes
from .colors import (
    BLOCK_UNKNOWN,
    WOOD_FALLBACK,
    WOOD_SPECIES,
    block_heuristics,
    block_palette,
)
from .survey import walk_surface

__all__ = [
    "PNGWriter",
    "ColorMap",
    "UNGENERATED",
    "UNREADABLE",
    "render_area",
    "render_slice",
    "render_plan",
]

UNGENERATED = (0, 0, 0)
UNREADABLE = (255, 0, 255)  # magenta: there is something here we cannot read
SKY = (150, 185, 225)
CAVE = (28, 26, 32)  # air below cover: the inside of the ground


# --------------------------------------------------------------------------
# Streaming PNG writer (stdlib only -- a PNG is just zlib plus four chunks)
# --------------------------------------------------------------------------


class PNGWriter:
    """Writes a truecolor PNG one row at a time.

    Buffering the whole image would cost width*height*3 bytes; feeding rows
    through a zlib compressor as they are produced keeps that flat.
    """

    def __init__(self, path: str, width: int, height: int, level: int = 6):
        self.width, self.height = width, height
        self._handle = open(path, "wb")
        self._rows = 0
        self._handle.write(b"\x89PNG\r\n\x1a\n")
        self._chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),  # 8-bit RGB
        )
        self._deflate = zlib.compressobj(level)
        self._pending = bytearray()

    def _chunk(self, tag: bytes, data: bytes) -> None:
        self._handle.write(struct.pack(">I", len(data)))
        self._handle.write(tag)
        self._handle.write(data)
        self._handle.write(struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    def write_row(self, row: bytes) -> None:
        if len(row) != self.width * 3:
            raise ValueError(f"row is {len(row)} bytes, expected {self.width * 3}")
        # 0 = no per-scanline filter. Filtering would compress better but costs
        # far more than it saves on imagery this flat.
        self._pending += self._deflate.compress(b"\x00" + row)
        self._rows += 1
        if len(self._pending) >= 1 << 20:
            self._chunk(b"IDAT", bytes(self._pending))
            self._pending.clear()

    def close(self) -> None:
        self._pending += self._deflate.flush()
        if self._pending:
            self._chunk(b"IDAT", bytes(self._pending))
        self._chunk(b"IEND", b"")
        self._handle.close()
        if self._rows != self.height:
            raise ValueError(f"wrote {self._rows} rows, declared {self.height}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if exc[0] is None:
            self.close()
        else:
            self._handle.close()


# --------------------------------------------------------------------------
# Block colors
# --------------------------------------------------------------------------

# The tables live in craft.colors, the one place a color changes; the Unity
# generators read them there too. These two aliases keep ColorMap's
# resolution below reading the way it always has.
_HEURISTICS = block_heuristics()
_WOODS = WOOD_SPECIES


def build_palette() -> dict:
    """Name -> RGB as :class:`ColorMap` draws it: the table from
    :func:`heaton_voxel.craft.colors.block_palette`, with air mapped to
    :data:`UNGENERATED`, the background the renderers shade air against."""
    table = block_palette()
    table.update(dict.fromkeys(sorted(AIR_BLOCKS), UNGENERATED))
    return table


class ColorMap:
    """Block name to RGB, with a record of what it could not place.

    Resolves against :mod:`heaton_voxel.craft.colors` in the order the
    generated C# does: the exact table; then, for a stair, slab or wall the
    game has and the table does not name, the block it is cut from
    (:func:`heaton_voxel.craft.blockinfo.cut_shapes`), resolved by the
    rungs below; then the ordered substring rules, then the wood fallback,
    then ``unknown``. So an oak stair is drawn as oak planks and a quartz
    slab as quartz, where a single stair-and-slab gray used to draw every
    one; a modded stair, which the game's registry does not name, still
    falls to that gray rule.

    ``unknown`` counts every call the ladder could not place, keyed by the
    name it could not place: for a cut shape that is its block, so one
    missing color is one entry however many of its cuts a world holds.
    ``stale_maps`` is what :func:`render_area` could not trust: the columns
    of the map it drew from that disagree with the blocks, by chunk.
    """

    def __init__(self, unknown=BLOCK_UNKNOWN):
        self.table = build_palette()
        self.unknown_color = unknown
        self.unknown = {}
        self.stale_maps = {}
        self._unmapped = set()
        self._cache = {}
        self._cut = cut_shapes()

    def __call__(self, name: str):
        # Before the cache, so that a cut is cached and counted as its
        # block on every call (see below).
        if name not in self.table:
            name = self._cut.get(name, name)
        found = self._cache.get(name)
        if found is None:
            found = self.table.get(name)
            if found is None:
                for needle, rgb in _HEURISTICS:
                    if needle in name:
                        found = rgb
                        break
            if found is None and any(w in name for w in _WOODS):
                found = WOOD_FALLBACK
            if found is None:
                found = self.unknown_color
                self._unmapped.add(name)
            self._cache[name] = found
        # Counted on every call, not just the first -- the cache above would
        # otherwise make every unmapped block look like it occurred once.
        if name in self._unmapped:
            self.unknown[name] = self.unknown.get(name, 0) + 1
        return found


# --------------------------------------------------------------------------
# Area render -- top-down
# --------------------------------------------------------------------------


def _scaled_rows(png: PNGWriter, row: bytes, scale: int) -> None:
    if scale > 1:
        row = b"".join(row[i:i + 3] * scale for i in range(0, len(row), 3))
    for _ in range(scale):
        png.write_row(row)


def render_area(
    save,
    corner1,
    corner2,
    path: str,
    *,
    dimension: str | None = None,
    heightmap: str = "WORLD_SURFACE",
    scale: int = 1,
    shade: bool = True,
    colors: ColorMap | None = None,
) -> ColorMap:
    """Top-down PNG of the box between two corners. Read-only.

    Each column is the color of its surface block (per ``heightmap``),
    shaded by slope against the column to its north, water darkened by its
    depth as :func:`~heaton_voxel.craft.survey.walk_surface` reads it --
    walked down to the bed, so a lake keeps its depth in a chunk an earlier
    ``rebuild_heightmaps`` left without ``OCEAN_FLOOR``. Kelp, seagrass and
    anything waterlogged drawn above the bed are darkened with the water
    around them. Black is ungenerated or not-yet-surfaced
    ground; magenta is a chunk that exists but cannot be read. ``y`` in the
    corners is ignored. ``scale`` repeats pixels, nearest-neighbor.

    The picture is what the stored map says, and a map can be wrong: a
    build pasted without a rebuild draws as the ground under it. So every
    finished chunk the picture touches is read against its blocks, and the
    disagreement goes in ``stale_maps``.

    Returns the :class:`ColorMap` used, whose ``unknown`` dict names any
    block the palette could not place -- report it, most frequent first --
    and whose ``stale_maps`` names the chunks this picture misdraws.
    """
    dim = save.dimension(dimension)
    x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
    z0, z1 = sorted((int(corner1[2]), int(corner2[2])))
    colors = colors if colors is not None else ColorMap()

    with PNGWriter(path, (x1 - x0 + 1) * scale, (z1 - z0 + 1) * scale) as png:
        previous: list[int | None] = [None] * (x1 - x0 + 1)
        for z in range(z0, z1 + 1):
            row = bytearray()
            for i, x in enumerate(range(x0, x1 + 1)):
                try:
                    top = dim.surface_y(x, z, heightmap)
                except NBTError:
                    row += bytes(UNREADABLE)
                    previous[i] = None
                    continue
                if top is None:
                    unreadable = False
                    try:
                        unreadable = (
                            dim.chunk(x >> 4, z >> 4) is not None
                            and dim.chunk(x >> 4, z >> 4).data is None
                        )
                    except NBTError:
                        unreadable = True
                    row += bytes(UNREADABLE if unreadable else UNGENERATED)
                    previous[i] = None
                    continue
                state = save.get_block(x, top, z, dimension=dimension)
                r, g, b = colors(state.name)
                if holds_water(state.name, state.properties):
                    # The column reader's depth, walked down through the water
                    # to its bed whatever maps a rebuild left -- so an edited
                    # lake keeps its depth. Only over the bed: a waterlogged
                    # stair on the seabed, drawn by OCEAN_FLOOR, is the bed
                    # and not the sea.
                    probe = walk_surface(save, x, z, dimension=dimension)
                    depth = (probe.water if probe is not None and top > probe.y
                             else 0)
                    if depth > 0:
                        k = 1.0 - min(depth, 32) / 44.0
                        r, g, b = int(r * k), int(g * k), int(b * k)
                if shade and previous[i] is not None:
                    delta = top - previous[i]
                    if delta:
                        factor = 1.16 if delta > 0 else 0.84
                        if abs(delta) > 3:
                            factor = 1.30 if delta > 0 else 0.72
                        r = min(255, int(r * factor))
                        g = min(255, int(g * factor))
                        b = min(255, int(b * factor))
                previous[i] = top
                row += bytes((r, g, b))
            _scaled_rows(png, bytes(row), scale)
    for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
        for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
            try:
                accessor = dim.blocks(chunk_x, chunk_z)
            except NBTError:
                continue                    # already drawn magenta
            if accessor is None or accessor.legacy or not accessor.finished:
                continue
            wrong = accessor.stale_heightmaps().get(heightmap)
            if wrong:
                colors.stale_maps[(chunk_x, chunk_z)] = wrong
    return colors


# --------------------------------------------------------------------------
# Plan render -- a horizontal cutaway at one height
# --------------------------------------------------------------------------


def render_plan(
    save,
    y: int,
    corner1,
    corner2,
    path: str,
    *,
    dimension: str | None = None,
    scale: int = 1,
    colors: ColorMap | None = None,
) -> ColorMap:
    """Floor plan of one story: a horizontal cut at ``y``. Read-only.

    Every cell is the block standing at exactly that height, so walls read
    as walls and their doorways read as gaps -- the picture an area map
    cannot give you, because an area map draws the roof over the whole
    building. Cut at a floor's head height to find the way in.

    Air is shaded two ways, on the same reasoning :func:`render_slice`
    uses: air with sky above it is open ground and draws light, air with
    something above it is *inside* and draws cave-dark. That single
    distinction is what makes a room read as a room and a courtyard read
    as a courtyard. Black is ungenerated, magenta a chunk that cannot be
    read; ``y`` in the corners is ignored in favor of the ``y``
    argument.

    Returns the :class:`ColorMap` used, whose ``unknown`` dict names any
    block the palette could not place.
    """
    dim = save.dimension(dimension)
    y = int(y)
    x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
    z0, z1 = sorted((int(corner1[2]), int(corner2[2])))
    colors = colors if colors is not None else ColorMap()

    with PNGWriter(path, (x1 - x0 + 1) * scale, (z1 - z0 + 1) * scale) as png:
        for z in range(z0, z1 + 1):
            row = bytearray()
            for x in range(x0, x1 + 1):
                try:
                    state = save.get_block(x, y, z, dimension=dimension)
                except NBTError:
                    row += bytes(UNREADABLE)
                    continue
                if state is None:
                    # No block here: either nothing generated, or a chunk
                    # that exists and cannot be read -- never the same thing.
                    unreadable = False
                    try:
                        chunk = dim.chunk(x >> 4, z >> 4)
                        unreadable = chunk is not None and chunk.data is None
                    except NBTError:
                        unreadable = True
                    row += bytes(UNREADABLE if unreadable else UNGENERATED)
                    continue
                if state.name in AIR_BLOCKS:
                    try:
                        top = dim.surface_y(x, z, "WORLD_SURFACE")
                    except NBTError:
                        top = None
                    row += bytes(CAVE if top is not None and top > y else SKY)
                    continue
                row += bytes(colors(state.name))
            _scaled_rows(png, bytes(row), scale)
    return colors


# --------------------------------------------------------------------------
# Slice render -- a vertical cutaway
# --------------------------------------------------------------------------


def render_slice(
    save,
    axis: str,
    at: int,
    corner1,
    corner2,
    path: str,
    *,
    dimension: str | None = None,
    scale: int = 1,
    colors: ColorMap | None = None,
) -> ColorMap:
    """Vertical cutaway PNG along one plane. Read-only.

    ``axis`` is ``"x"`` or ``"z"``: the coordinate held fixed at ``at``.
    The image spans the *other* horizontal axis and the y range of the
    corners, top of the world at the top of the picture. Air is sky-blue
    where it is open to the sky within the rendered span, and cave-dark
    where something solid sits above it -- which is what makes dungeons,
    caves and interiors legible. Ungenerated ground is near-black.

    This is the picture that answers "did the stairway actually reach the
    dungeon": geometry that a top-down map cannot show.
    """
    if axis not in ("x", "z"):
        raise ValueError(f"axis must be 'x' or 'z', got {axis!r}")
    y0, y1 = sorted((int(corner1[1]), int(corner2[1])))
    if axis == "x":
        a0, a1 = sorted((int(corner1[2]), int(corner2[2])))
    else:
        a0, a1 = sorted((int(corner1[0]), int(corner2[0])))
    colors = colors if colors is not None else ColorMap()
    span = a1 - a0 + 1

    # One pass per column: block names down the plane, and the highest
    # solid block, which decides sky against cave for the air below it.
    names: list[list[str | None]] = []
    tops: list[int | None] = []
    for a in range(a0, a1 + 1):
        x, z = (at, a) if axis == "x" else (a, at)
        column: list[str | None] = []
        top: int | None = None
        for y in range(y1, y0 - 1, -1):
            state = save.get_block(x, y, z, dimension=dimension)
            name = state.name if state is not None else None
            column.append(name)
            if top is None and name is not None and name not in AIR_BLOCKS:
                top = y
        names.append(column)
        tops.append(top)

    with PNGWriter(path, span * scale, (y1 - y0 + 1) * scale) as png:
        for row_index in range(y1 - y0 + 1):
            y = y1 - row_index
            row = bytearray()
            for i in range(span):
                name = names[i][row_index]
                if name is None:
                    row += bytes((18, 18, 22))  # ungenerated
                elif name in AIR_BLOCKS:
                    covered = tops[i] is not None and y < tops[i]
                    row += bytes(CAVE if covered else SKY)
                else:
                    row += bytes(colors(name))
            _scaled_rows(png, bytes(row), scale)
    return colors


# --------------------------------------------------------------------------
# Elevation render -- a building from outside
# --------------------------------------------------------------------------

#: How far each block of depth darkens what stands there, and the most it
#: darkens anything.
_DEPTH_STEP = 0.12
_DEPTH_FLOOR = 0.5


def render_elevation(
    save,
    side: str,
    corner1,
    corner2,
    path: str,
    *,
    dimension: str | None = None,
    scale: int = 1,
    shade: bool = True,
    colors: ColorMap | None = None,
) -> ColorMap:
    """A box as you see it from one side, in PNG. Read-only.

    ``side`` is the compass word you stand on: ``"south"`` looks north at
    the box's south face. Each pixel is the first thing a look straight in
    from that face meets inside the box, which is what
    :func:`render_slice` cannot show: a slice is one plane, so glass set back
    behind its piers and a cornice proud of its wall are in different
    pictures. The picture is as you would see it, so west is on your left
    looking north and on your right looking south, and ``shade`` darkens
    what stands further back than the plane most of the picture stands in,
    so a recess reads as one and a plaza in front of a wall does not put
    the whole wall in shadow. Glass stops a look, as a block.

    Sky is what a look through the whole box meets nothing in. Black is
    ungenerated, magenta a chunk that cannot be read.

    Returns the :class:`ColorMap` used, whose ``unknown`` dict names any
    block the palette could not place.
    """
    if side not in ("north", "south", "east", "west"):
        raise ValueError(f"side must be a compass word, got {side!r}")
    dim = save.dimension(dimension)
    x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
    y0, y1 = sorted((int(corner1[1]), int(corner2[1])))
    z0, z1 = sorted((int(corner1[2]), int(corner2[2])))
    colors = colors if colors is not None else ColorMap()
    xs, zs = range(x0, x1 + 1), range(z0, z1 + 1)
    across, ahead, cell = {
        "south": (xs, range(z1, z0 - 1, -1), lambda a, d, y: (a, y, d)),
        "north": (xs[::-1], zs, lambda a, d, y: (a, y, d)),
        "east": (zs[::-1], range(x1, x0 - 1, -1), lambda a, d, y: (d, y, a)),
        "west": (zs, xs, lambda a, d, y: (d, y, a)),
    }[side]

    def look(a, y):
        """The first thing met, ``(name, depth)``: None for ground not
        generated, or nothing met at all."""
        for depth, d in enumerate(ahead):
            spot = cell(a, d, y)
            state = save.get_block(*spot, dimension=dimension)
            if state is None:
                return None, depth, spot
            if state.name not in AIR_BLOCKS:
                return state.name, depth, spot
        return AIR_BLOCKS, None, None

    rows = [[look(a, y) for a in across] for y in range(y1, y0 - 1, -1)]
    depths = collections.Counter(depth for row in rows for _, depth, _ in row
                                 if depth is not None)
    plane = min(depths, key=lambda depth: (-depths[depth], depth), default=0)

    def pixel(name, depth, spot):
        if name is None:
            try:
                chunk = dim.chunk(spot[0] >> 4, spot[2] >> 4)
                unreadable = chunk is not None and chunk.data is None
            except NBTError:
                unreadable = True
            return UNREADABLE if unreadable else UNGENERATED
        if name is AIR_BLOCKS:
            return SKY
        r, g, b = colors(name)
        if shade and depth > plane:
            k = max(_DEPTH_FLOOR, 1.0 - _DEPTH_STEP * (depth - plane))
            r, g, b = int(r * k), int(g * k), int(b * k)
        return (r, g, b)

    with PNGWriter(path, len(across) * scale, (y1 - y0 + 1) * scale) as png:
        for row in rows:
            line = bytearray()
            for name, depth, spot in row:
                line += bytes(pixel(name, depth, spot))
            _scaled_rows(png, bytes(line), scale)
    return colors


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def _main(argv: list[str]) -> int:
    import argparse
    import os
    import sys

    from ..world import MinecraftSave

    parser = argparse.ArgumentParser(
        prog="python -m heaton_voxel render",
        description="Render a top-down area, a floor plan, or a vertical "
                    "cutaway of a save."
    )
    parser.add_argument("world", help="path to the save directory (holds level.dat)")
    parser.add_argument("-o", "--out", default="render.png", help="output PNG")
    parser.add_argument(
        "--area", nargs=4, metavar=("X0", "Z0", "X1", "Z1"),
        help="top-down render of this block rectangle; '~-40'-style values "
        "are relative to the player (quote them for the shell)",
    )
    parser.add_argument(
        "--slice", nargs=6, metavar=("AXIS", "AT", "A0", "A1", "Y0", "Y1"),
        help="vertical cutaway: hold AXIS (x or z) at AT, span A0..A1, "
        "heights Y0..Y1; coordinates accept '~' notation",
    )
    parser.add_argument(
        "--plan", nargs=5, metavar=("Y", "X0", "Z0", "X1", "Z1"),
        help="floor plan: a horizontal cut at height Y across this block "
        "rectangle; coordinates accept '~' notation",
    )
    parser.add_argument(
        "--elevation", nargs=7,
        metavar=("SIDE", "X0", "Y0", "Z0", "X1", "Y1", "Z1"),
        help="a box as you see it from outside: SIDE is the compass word you "
        "stand on, and coordinates accept '~' notation",
    )
    parser.add_argument("--scale", type=int, default=1, help="pixels per block")
    parser.add_argument("--dimension", default=None)
    parser.add_argument(
        "--heightmap", default="WORLD_SURFACE",
        help="surface map for --area (default WORLD_SURFACE)",
    )
    args = parser.parse_args(argv)

    if sum(1 for form in (args.area, args.slice, args.plan, args.elevation)
           if form) != 1:
        print("error: pass exactly one of --area, --plan, --slice or "
              "--elevation", file=sys.stderr)
        return 1
    if args.scale < 1:
        print("error: --scale must be at least 1", file=sys.stderr)
        return 1

    from ..nbt import NBTError

    save = MinecraftSave.load(os.path.expanduser(args.world))
    if args.area:
        try:
            ax0, az0, ax1, az1 = save.resolve_coordinates(args.area, axes="xzxz")
        except NBTError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        colors = render_area(
            save, (ax0, 0, az0), (ax1, 0, az1), args.out,
            dimension=args.dimension, heightmap=args.heightmap, scale=args.scale,
        )
    elif args.plan:
        try:
            py, px0, pz0, px1, pz1 = save.resolve_coordinates(
                args.plan, axes="yxzxz"
            )
        except NBTError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        colors = render_plan(
            save, py, (px0, 0, pz0), (px1, 0, pz1), args.out,
            dimension=args.dimension, scale=args.scale,
        )
    elif args.elevation:
        side, *coords = args.elevation
        if side not in ("north", "south", "east", "west"):
            print(f"error: an elevation is seen from north, south, east or "
                  f"west, got {side!r}", file=sys.stderr)
            return 2
        try:
            box = save.resolve_coordinates(coords)
        except NBTError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        colors = render_elevation(
            save, side, box[:3], box[3:], args.out,
            dimension=args.dimension, scale=args.scale,
        )
    else:
        axis, *coords = args.slice
        if axis not in ("x", "z"):
            print(f"error: slice axis must be x or z, got {axis!r}", file=sys.stderr)
            return 2
        cross = "z" if axis == "x" else "x"
        try:
            at, a0, a1, sy0, sy1 = save.resolve_coordinates(
                coords, axes=axis + cross * 2 + "yy"
            )
        except NBTError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        low = (a0, sy0, a0)
        high = (a1, sy1, a1)
        colors = render_slice(
            save, axis, at, low, high, args.out,
            dimension=args.dimension, scale=args.scale,
        )
    print(f"wrote {args.out} ({os.path.getsize(args.out) / 1024:.0f} KiB)")
    if colors.stale_maps:
        print(f"warning: {args.heightmap} is stale in "
              f"{sum(colors.stale_maps.values()):,} columns of "
              f"{len(colors.stale_maps):,} chunks touching this picture, so it "
              f"draws what the map says stands there, not what does "
              f"(save.rebuild_heightmaps mends it)")
    if colors.unknown:
        top = sorted(colors.unknown.items(), key=lambda kv: -kv[1])[:5]
        print("unmapped block types (drawn gray; a stair, slab or wall "
              "counted as its block):")
        for name, count in top:
            print(f"  {count:>9,}  {name}")
    return 0
