"""Re-climate a square around the player, from ground level to the sky or the bedrock.

    python examples/biome_zone.py "~/Library/Application Support/minecraft/saves/my_world"
    python examples/biome_zone.py <world> --width 64 --biome minecraft:snowy_taiga
    python examples/biome_zone.py <world> --width 32 --dry-run

    python examples/biome_zone.py <world> --direction down

The square is centered on the player and extends `--width` blocks on each side.
Which way it extends *vertically* is `--direction`:

  auto (default)  ->  ground level, up or down depending on where the player is
                      above ground  ->  ground level up to the top of the world
                      underground   ->  ground level down to the bottom
  up              ->  the player's own y up to the top of the world
  down            ->  the player's own y down to the bottom of the world

The anchor differs between the two, which is the point. `auto` measures from
the ground; `up` and `down` measure from your feet. Standing on a hilltop and
asking for `down` fills from where you are, not from a ground level that is the
same height you are. An explicit direction also needs no heightmap, so it works
in a partly-generated chunk where `auto` has nothing to measure.

"Ground level" is the surface in the player's own column, read from the chunk's
MOTION_BLOCKING heightmap, and that one height decides the split for the whole
square. A single flat boundary is predictable and explainable; following the
terrain per column would be a different tool.

MOTION_BLOCKING is the topmost block that stops you moving, so it is the block
the player is standing *on*. WORLD_SURFACE would instead be the topmost non-air
block, which on any grassy surface is the flower or grass the player is standing
*in* -- level with their feet, which would read as underground and fill the
wrong way. Water blocks motion, so standing in a boat still counts as above
ground and diving counts as underground; `--heightmap OCEAN_FLOOR` measures to
the seabed instead.

Close the world in Minecraft first, for the reasons in gold_box.py.

Changing a biome does not retroactively change the world. Snow already on the
ground is placed blocks and stays; grass and foliage color is client-side and
follows the new biome immediately; weather and mob spawning follow the new
biome the next time the chunk loads.
"""

from __future__ import annotations

import argparse
import os
import sys

# Make the modules importable when run from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heaton_voxel.blocks import HEIGHTMAP_NAMES, heightmap_bits  # noqa: E402,F401
from heaton_voxel.nbt import NBTError, world_is_open  # noqa: E402
from heaton_voxel.world import Dimension, MinecraftSave  # noqa: E402

# MOTION_BLOCKING, not WORLD_SURFACE: the question here is "what is the player
# standing on", and WORLD_SURFACE counts the flower, torch or snow layer the
# player is standing *in*. On any grassy surface that puts the reported ground
# level at the player's own feet, which reads as underground and fills the
# wrong way. MOTION_BLOCKING reports the block that actually stops you.
HEIGHTMAP = "MOTION_BLOCKING"
HEIGHTMAPS = HEIGHTMAP_NAMES

AUTO, UP, DOWN = "auto", "up", "down"
DIRECTIONS = (AUTO, UP, DOWN)


def surface_y(
    dim: Dimension, x: int, z: int, heightmap: str = HEIGHTMAP
) -> int | None:
    """Y of the topmost surface block at a column, or ``None`` if unavailable.

    Now a thin veneer over :meth:`Dimension.surface_y`, which grew out of
    this function; the signature stays because the tests -- and possibly
    readers -- know this one. ``None`` still means ungenerated, unreadable,
    or stopped before the stage that builds heightmaps; a stored map that
    contradicts the chunk's height still raises rather than unpacking
    nonsense.
    """
    return dim.surface_y(x, z, heightmap)


def vertical_span(
    player_y: int,
    ground: int | None,
    min_y: int,
    max_y: int,
    direction: str = AUTO,
) -> tuple[int, int, str]:
    """The y range to fill, and a phrase describing which way it went.

    The two modes anchor at different heights on purpose:

    ``auto`` anchors at *ground level* and picks the direction from where the
    player is relative to it. Standing on the surface puts the player's feet
    one block above the block they stand on, so ``player_y > ground`` is
    "above ground".

    ``up`` and ``down`` anchor at the *player's own y* and go the way they are
    told. That is the difference that makes them useful: standing on a hill and
    asking for ``down`` fills from your feet, not from a ground level that is
    the same height you are.

    ``ground`` may be None when the direction is explicit, since nothing then
    needs the surface.
    """
    if direction == UP:
        return player_y, max_y, "up to the sky from the player"
    if direction == DOWN:
        return min_y, player_y, "down to the bedrock from the player"
    if direction != AUTO:
        raise ValueError(f"unknown direction {direction!r}")
    if ground is None:
        raise ValueError("auto needs a ground level")
    if player_y > ground:
        return ground, max_y, "up to the sky from ground level"
    return min_y, ground, "down to the bedrock from ground level"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("world", help="path to the save directory (holds level.dat)")
    parser.add_argument(
        "--biome",
        default="minecraft:snowy_taiga",
        help="biome id to write (default minecraft:snowy_taiga)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=32,
        help="edge length of the square in blocks (default 32)",
    )
    parser.add_argument(
        "--direction",
        default=AUTO,
        choices=DIRECTIONS,
        help=(
            "auto (default) anchors at ground level and picks the direction "
            "from whether the player is above or below it; up and down anchor "
            "at the player's own y and go that way regardless"
        ),
    )
    parser.add_argument(
        "--heightmap",
        default=HEIGHTMAP,
        choices=HEIGHTMAPS,
        help=(
            "which surface to measure to (default MOTION_BLOCKING, the block "
            "you stand on; WORLD_SURFACE counts plants and torches, "
            "OCEAN_FLOOR ignores water)"
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would change, write nothing"
    )
    args = parser.parse_args(argv)

    if args.width < 1:
        print(f"error: --width must be at least 1, got {args.width}", file=sys.stderr)
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

    x, y, z = save.player_block_position()
    dimension = save.player_dimension()
    print(f"player is at ({x}, {y}, {z}) in {dimension}")

    dim = save.dimension()
    accessor = dim.blocks(x >> 4, z >> 4)
    if accessor is None:
        print(
            f"error: the chunk at ({x >> 4}, {z >> 4}) holding the player is not "
            f"generated, so there is no surface to work from.",
            file=sys.stderr,
        )
        return 1

    # Only `auto` needs to know where the surface is. Skipping the lookup for
    # an explicit direction is not just a saving: it lets the tool work in a
    # partly-generated chunk, which has no heightmaps at all.
    ground = None
    if args.direction == AUTO:
        try:
            ground = surface_y(dim, x, z, args.heightmap)
        except NBTError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if ground is None:
            print(
                f"error: no {args.heightmap} heightmap for the player's column. The "
                f"chunk is only partly generated, so its surface does not exist yet. "
                f"Pass --direction up or --direction down to fill from the player "
                f"instead of from ground level.",
                file=sys.stderr,
            )
            return 1
        print(f"ground level here is y={ground} ({args.heightmap})")

    y0, y1, described = vertical_span(
        y, ground, accessor.min_y, accessor.max_y, args.direction
    )
    print(f"filling {described} (y {y0}..{y1})")

    # Center the square on the player. An odd width centers exactly; an even
    # one leans one block east and south, same convention as gold_box.
    half = args.width // 2
    corner1 = (x - half, y0, z - half)
    corner2 = (x - half + args.width - 1, y1, z - half + args.width - 1)
    print(f"setting {args.biome} over {args.width}x{args.width} blocks")
    print(f"  from {corner1} to {corner2}")

    result = save.fill_biome(corner1, corner2, args.biome)
    print(f"  {result!r}")

    # Biomes are stored per 4x4x4 cell, so the box written is the box asked for
    # grown outward to cell boundaries. Say so rather than let it surprise.
    written = result.written_box
    if written is not None and written != (corner1, corner2):
        print(f"  note: rounded out to whole 4x4x4 biome cells: {written[0]} to {written[1]}")

    if not result.complete:
        print(
            f"  warning: incomplete -- {result.cells_out_of_range:,} cells out of "
            f"range, {len(result.missing_chunks)} ungenerated chunks, "
            f"{len(result.unreadable_chunks)} unreadable chunks",
            file=sys.stderr,
        )
        for where, why in result.unreadable_chunks[:3]:
            print(f"    chunk {where}: {why}", file=sys.stderr)

    if args.dry_run:
        print("  dry run: nothing written")
        return 0

    try:
        count = save.save()
    except NBTError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"  wrote {count} files")
    print(
        "\nLoad the world and look around. Weather and mob spawning follow the new "
        "biome; blocks already placed do not change."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
