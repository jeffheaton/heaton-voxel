"""``python -m heaton_voxel world`` -- inspect a save, optionally fill."""

from __future__ import annotations

from ..nbt import NBTError
from .save import MinecraftSave


def _main(argv: list[str]) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m heaton_voxel world",
        description="Inspect a Minecraft save and optionally fill a box of blocks."
    )
    parser.add_argument("world", help="path to the save directory (holds level.dat)")
    parser.add_argument(
        "--fill",
        nargs=7,
        metavar=("X1", "Y1", "Z1", "X2", "Y2", "Z2", "BLOCK"),
        help="fill the box between two corners, e.g. --fill 0 70 0 8 78 8 "
        "minecraft:glass; '~-5'-style coordinates are relative to the "
        "player (quote them for the shell)",
    )
    parser.add_argument(
        "--dimension", help="which dimension to fill (default: where the player is)"
    )
    parser.add_argument(
        "--write", action="store_true", help="actually save the changes to disk"
    )
    parser.add_argument(
        "--force", action="store_true", help="write even if the world is open"
    )
    args = parser.parse_args(argv)

    save = MinecraftSave.load(args.world)
    print(save)
    print(f"  compressed chunk data : {save.memory_estimate() / 1024 / 1024:.1f} MiB")
    try:
        x, y, z = save.player_position()
        bx, by, bz = save.player_block_position()
        print(f"  player position       : ({x:.2f}, {y:.2f}, {z:.2f})")
        print(f"  player block          : ({bx}, {by}, {bz}) in {save.player_dimension()}")
        print(f"  standing on           : {save.get_block(bx, by - 1, bz)}")
    except NBTError as exc:
        print(f"  player                : unavailable ({exc})")
    print(f"  world spawn           : {save.spawn_position()}")

    if args.fill:
        *coords, block = args.fill
        try:
            x1, y1, z1, x2, y2, z2 = save.resolve_coordinates(coords)
        except NBTError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        result = save.fill(
            (x1, y1, z1), (x2, y2, z2), block, dimension=args.dimension
        )
        print(f"\n  fill {block}: {result!r}")
        if result.missing_chunks:
            print(f"    ungenerated chunks: {result.missing_chunks[:8]}")
        if args.write:
            print(f"    wrote {save.save(force=args.force)} files")
        else:
            print("    (dry run; pass --write to save)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """The ``world`` command on its own (``heaton-voxel world``).

    The ``heaton-voxel`` script is the dispatcher in
    :mod:`heaton_voxel.__main__`, which reaches this as its ``world``
    command; this stays for a caller that wants the one command alone.
    """
    import sys

    return _main(sys.argv[1:] if argv is None else argv)


