"""``heaton-voxel`` and ``python -m heaton_voxel`` — dispatch to one of the
command lines.

    python -m heaton_voxel nbt    <file.mca> --verify --check
    python -m heaton_voxel world  <world-path> --fill ...
    python -m heaton_voxel render <world-path> --area X0 Z0 X1 Z1 -o out.png
    python -m heaton_voxel schem  export <world-path> out.schem --box ...
    python -m heaton_voxel worksite list <world-path>
    python -m heaton_voxel worksite retire <world-path> <name> [--delete]
    python -m heaton_voxel audit  <world-path> --box X1 Y1 Z1 X2 Y2 Z2
    python -m heaton_voxel elevation <world-path> --box X0 Z0 X1 Z1 --floor Y --top Y
    python -m heaton_voxel massing <world-path> --box X1 Y1 Z1 X2 Y2 Z2

Installing the package puts this dispatcher on PATH as ``heaton-voxel``, so
``heaton-voxel render ...`` is ``python -m heaton_voxel render ...``, and the
``nbt`` command alone as ``heaton-voxel-nbt``. Running the submodules directly
(``python -m heaton_voxel.nbt``) works too, but warns, because importing the
package has already loaded them.
"""

from __future__ import annotations

import sys

USAGE = """usage: heaton-voxel {nbt|world|render|schem|worksite|audit|elevation|massing} [args...]
   or: python -m heaton_voxel {the same}

  nbt    <file.mca|file.dat> [--verify] [--check] [--dump X,Z]
  world  <world-path> [--fill X1 Y1 Z1 X2 Y2 Z2 BLOCK] [--write]
  render <world-path> (--area X0 Z0 X1 Z1 | --plan Y X0 Z0 X1 Z1
                       | --slice AXIS AT A0 A1 Y0 Y1
                       | --elevation SIDE X0 Y0 Z0 X1 Y1 Z1)
                      [-o out.png] [--scale N]
  schem  info <file.schem>
  schem  export <world-path> <out.schem> --box X1 Y1 Z1 X2 Y2 Z2
                      [--entities] [--biomes] [--origin X Y Z] [--version 2|3]
  schem  import <world-path> <file.schem> --at X Y Z
                      [--anchor origin|min] [--rotate DEG] [--flip x|y|z]
                      [--skip-air] [--no-entities] [--write]
  schem  transform <in.schem> <out.schem> [--rotate DEG] [--flip x|y|z]
  worksite list <world-path>
  worksite rollback <world-path> <name> [--dry-run]
                      (looks only: the undo is Worksite.rollback() from Python)
  worksite retire <world-path> <name> [--delete]
                      (looks only, until --delete removes that snapshot)
  audit <world-path> --box X1 Y1 Z1 X2 Y2 Z2 [--from X Y Z] [--no-jump]
                      (reads only: what stands in a box, judged)
  elevation <world-path> --box X0 Z0 X1 Z1 --floor Y --top Y
                      [--stories Y [Y ...]] [--depth N]
                      (reads only: a building's four walls, as rules)
  massing <world-path> --box X1 Y1 Z1 X2 Y2 Z2 [--walls] [--depth N]
                      (reads only: a tower's tiers, and with --walls each
                      tier's walls, as rules)

Coordinate arguments accept the game's relative notation: ~ is the player's
position, ~5 five blocks past it. Quote it ('~', '~-5') so the shell does
not expand it. Pass --help after the subcommand for its full options."""


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if args else 2

    command, rest = args[0], args[1:]
    if command == "nbt":
        from .nbt import _main as run
    elif command == "world":
        from .world import _main as run
    elif command == "render":
        from .craft.render import _main as run
    elif command == "schem":
        from .schem import _main as run
    elif command == "worksite":
        from .craft.cli import _main as run
    elif command == "audit":
        from .craft.audit import _main as run
    elif command == "elevation":
        from .craft.cli import _elevation_main as run
    elif command == "massing":
        from .craft.cli import _massing_main as run
    else:
        print(f"unknown subcommand {command!r}\n\n{USAGE}", file=sys.stderr)
        return 2
    return run(rest)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
