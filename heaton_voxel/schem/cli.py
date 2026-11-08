"""``python -m heaton_voxel schem`` -- info, export, import, transform."""

from __future__ import annotations

from ..nbt import NBTError
from .copy import export_schem, import_schem
from .schematic import Schematic
from .support import AIR_BLOCKS



def _main(argv: list[str]) -> int:
    import argparse
    import os
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m heaton_voxel schem",
        description="Inspect .schem files, export boxes of a world to them, "
        "and paste them back in. Coordinate arguments accept the game's "
        "relative notation -- ~ is the player's position, ~5 five blocks "
        "past it -- but quote it ('~', '~-5') or the shell eats it.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    info = commands.add_parser(
        "info", help="describe a .schem, a structure .nbt or a .litematic")
    info.add_argument("file", help="a .schem, a structure .nbt or a .litematic")

    exporter = commands.add_parser("export", help="copy a box of a world to a .schem")
    exporter.add_argument("world", help="path to the save directory (holds level.dat)")
    exporter.add_argument(
        "out", help="the file to write: .nbt a vanilla structure, .litematic a "
                    "Litematica schematic, anything else a .schem")
    exporter.add_argument(
        "--box",
        nargs=6,
        required=True,
        metavar=("X1", "Y1", "Z1", "X2", "Y2", "Z2"),
        help="opposite corners, inclusive, any order; '~-5'-style values "
        "are relative to the player",
    )
    exporter.add_argument(
        "--origin",
        nargs=3,
        metavar=("X", "Y", "Z"),
        help="anchor point recorded in the file (default: the box's min "
        "corner); accepts '~' notation",
    )
    exporter.add_argument("--entities", action="store_true", help="also copy entities")
    exporter.add_argument("--biomes", action="store_true", help="also copy biomes")
    exporter.add_argument(
        "--version", type=int, choices=(2, 3), default=3, help="container version"
    )
    exporter.add_argument("--dimension", help="default: where the player is")
    exporter.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="export air for ungenerated/unreadable chunks instead of refusing",
    )

    importer = commands.add_parser("import", help="paste a .schem into a world")
    importer.add_argument("world", help="path to the save directory (holds level.dat)")
    importer.add_argument("file", help="the .schem, structure .nbt or .litematic to paste")
    importer.add_argument(
        "--at",
        nargs=3,
        required=True,
        metavar=("X", "Y", "Z"),
        help="where the schematic's origin lands; '~' notation pastes "
        "relative to the player, so --at '~' '~' '~' is //paste",
    )
    importer.add_argument(
        "--anchor",
        choices=("origin", "min"),
        default="origin",
        help="'origin' honors the file's offset (WorldEdit //paste); "
        "'min' puts the minimum corner exactly at --at",
    )
    importer.add_argument(
        "--rotate",
        type=int,
        default=0,
        metavar="DEG",
        help="rotate before placing: a multiple of 90, positive = clockwise "
        "from above (north faces become east)",
    )
    importer.add_argument(
        "--flip",
        choices=("x", "y", "z"),
        help="mirror before placing (applied before --rotate): x swaps "
        "east/west, z swaps north/south, y turns it upside down",
    )
    importer.add_argument(
        "--no-entities", action="store_true", help="do not paste stored entities"
    )
    importer.add_argument(
        "--biomes", action="store_true", help="also apply stored biome data"
    )
    importer.add_argument(
        "--skip-air",
        action="store_true",
        help="do not paste air, so terrain shows through the gaps",
    )
    importer.add_argument(
        "--paste-voids",
        action="store_true",
        help="write structure_void cells as blocks, rather than leave the world "
             "as it is there (cells a structure or a litematic leaves out read "
             "as structure_void)",
    )
    importer.add_argument(
        "--no-relight", action="store_true", help="keep the stale baked lighting"
    )
    importer.add_argument(
        "--no-heightmaps", action="store_true",
        help="keep the stale stored heightmaps"
    )
    importer.add_argument("--dimension", help="default: where the player is")
    importer.add_argument(
        "--write", action="store_true", help="actually save the changes to disk"
    )
    importer.add_argument(
        "--force", action="store_true", help="write even if the world is open"
    )

    transformer = commands.add_parser(
        "transform", help="rotate or mirror a .schem into a new file"
    )
    transformer.add_argument("file", help="the .schem, structure .nbt or .litematic to read")
    transformer.add_argument("out", help="the file to write, in the format its extension names")
    transformer.add_argument(
        "--rotate",
        type=int,
        default=0,
        metavar="DEG",
        help="a multiple of 90; positive = clockwise from above",
    )
    transformer.add_argument(
        "--flip",
        choices=("x", "y", "z"),
        help="mirror (applied before --rotate)",
    )
    transformer.add_argument(
        "--version",
        type=int,
        choices=(2, 3),
        help="container version to write (default: same as the input)",
    )

    args = parser.parse_args(argv)

    if args.command == "info":
        schematic = Schematic.load(args.file)
        print(schematic)
        print(f"  format            : {schematic.source_format}")
        print(f"  container version : {schematic.source_version}")
        if schematic.source_palettes and schematic.source_palettes > 1:
            print(f"  palettes          : {schematic.source_palettes}, the first read")
        print(f"  data version      : {schematic.data_version}")
        w, h, l = schematic.size
        print(f"  size              : {w} x {h} x {l} ({schematic.volume:,} blocks)")
        print(f"  offset            : {schematic.offset}")
        counts = schematic.histogram()
        solid = sum(n for s, n in counts.items() if s.name not in AIR_BLOCKS)
        print(f"  non-air blocks    : {solid:,}")
        print(f"  palette           : {len(schematic.palette)} states")
        for state, count in sorted(counts.items(), key=lambda kv: -kv[1])[:8]:
            print(f"    {count:>10,}  {state}")
        print(f"  block entities    : {len(schematic.block_entities)}")
        print(f"  entities          : {len(schematic.entities)}")
        biome_note = "yes" if schematic.has_biomes else "no"
        if schematic.had_2d_biomes:
            biome_note = "2D column data present but not usable; re-export as v3"
        print(f"  biomes            : {biome_note}")
        if schematic.metadata:
            print(f"  metadata keys     : {', '.join(schematic.metadata)}")
        return 0

    if args.command == "transform":
        schematic = Schematic.load(args.file)
        own = schematic.source_version if schematic.source_format == "sponge" else None
        version = args.version or own or 3
        if args.flip:
            schematic = schematic.flipped(args.flip)
        if args.rotate:
            schematic = schematic.rotated(args.rotate)
        schematic.save(args.out, version=version)
        print(schematic)
        suffix = os.path.splitext(args.out)[1].lower()
        written = {".nbt": "a structure", ".litematic": "a litematic"}.get(
            suffix, f"container version {version}")
        print(f"  wrote {args.out} ({written})")
        for warning in schematic.transform_warnings[:8]:
            print(f"  warning: {warning}")
        if len(schematic.transform_warnings) > 8:
            print(f"  ... {len(schematic.transform_warnings) - 8} more warnings")
        return 0

    from ..world import MinecraftSave  # local import: format layer stays world-free

    save = MinecraftSave.load(args.world)
    if args.command == "export":
        try:
            x1, y1, z1, x2, y2, z2 = save.resolve_coordinates(args.box)
            origin = None
            if args.origin:
                ox, oy, oz = save.resolve_coordinates(args.origin)
                origin = (ox, oy, oz)
        except NBTError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        result = export_schem(
            save,
            (x1, y1, z1),
            (x2, y2, z2),
            args.out,
            origin=origin,
            entities=args.entities,
            biomes=args.biomes,
            version=args.version,
            dimension=args.dimension,
            allow_incomplete=args.allow_incomplete,
        )
        print(result)
        print(f"  wrote {args.out}")
        if not result.complete:
            print(
                f"  warning: incomplete -- {len(result.missing_chunks)} chunks "
                f"ungenerated, {len(result.unreadable_chunks)} unreadable, "
                f"{result.blocks_out_of_range:,} cells air-filled"
            )
        return 0

    try:
        ax, ay, az = save.resolve_coordinates(args.at)
    except NBTError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    placed = import_schem(
        save,
        args.file,
        (ax, ay, az),
        anchor=args.anchor,
        entities=not args.no_entities,
        biomes=args.biomes,
        paste_air=not args.skip_air,
        paste_voids=args.paste_voids,
        relight=not args.no_relight,
        heightmaps=not args.no_heightmaps,
        dimension=args.dimension,
        rotate=args.rotate,
        flip=args.flip,
    )
    print(placed)
    if placed.placed_box is not None:
        print(f"  placed box: {placed.placed_box[0]} to {placed.placed_box[1]}")
    if placed.entities_skipped:
        print(f"  entities skipped: {placed.entities_skipped[:8]}")
    for warning in placed.transform_warnings[:8]:
        print(f"  warning: {warning}")
    if len(placed.transform_warnings) > 8:
        print(f"  ... {len(placed.transform_warnings) - 8} more warnings")
    if args.write:
        print(f"  wrote {save.save(force=args.force)} files")
    else:
        print("  (dry run; pass --write to save)")
    return 0
