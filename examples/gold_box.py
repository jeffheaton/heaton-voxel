"""Place a solid cube of gold near the player, in whichever world you point it at.

    python examples/gold_box.py "~/Library/Application Support/minecraft/saves/my_world"
    python examples/gold_box.py <world> --size 5 --block minecraft:diamond_block
    python examples/gold_box.py <world> --dry-run

Close the world in Minecraft first. The game holds a lock on a world it has
open and keeps its own copy of the chunks in memory, so an edit made
underneath it is either refused (the default here) or thrown away when the
game next writes. Load the world *after* running this, not before.

Lighting is not recalculated, so the inside of an opaque cube keeps whatever
light values it had until Minecraft relights the chunk.
"""

from __future__ import annotations

import argparse
import os
import sys

# Make the modules importable when run from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heaton_voxel.nbt import NBTError, world_is_open  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("world", help="path to the save directory (holds level.dat)")
    parser.add_argument(
        "--block", default="minecraft:gold_block", help="block to build from"
    )
    parser.add_argument(
        "--size", type=int, default=3, help="edge length in blocks (default 3)"
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=3,
        help="how many blocks south of the player to place it (default 3)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would change, write nothing"
    )
    args = parser.parse_args(argv)

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
    print(f"player is at ({x}, {y}, {z}) in {save.player_dimension()}")

    # Center the cube on the player's x, `offset` blocks south, standing at
    # their feet. An odd size centers exactly; an even one leans one block east.
    half = args.size // 2
    corner1 = (x - half, y, z + args.offset)
    corner2 = (x - half + args.size - 1, y + args.size - 1, z + args.offset + args.size - 1)
    print(f"building a {args.size}x{args.size}x{args.size} cube of {args.block}")
    print(f"  from {corner1} to {corner2}")

    result = save.fill(corner1, corner2, args.block)
    print(f"  {result!r}")
    if not result.complete:
        print(
            f"  warning: incomplete -- {result.blocks_out_of_range:,} blocks out of "
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
        written = save.save()
    except NBTError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"  wrote {written} files")
    print(f"\nLoad the world and look {args.offset} blocks south of where you logged out.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
