"""Build a row of towers, no two alike, in one go.

    python examples/skyline.py <world> --at X Z                # a dry run
    python examples/skyline.py <world> --at X Z --count 8 --write
    python -m heaton_voxel worksite rollback <world> skyline

Each lot in the row gets a size, a height and a family from
``examples/tower_styles.py``, all rolled from its place and ``--seed``, and
``design_tower`` works out the rest: its plan, tiers, roof and colors. The row
runs east from X Z along the south edge of its lots, each entrance facing south.

This is the variety and nothing else: the ground is leveled lot by lot, with no
street between them, and it does not look for flat land. Find that first. A dry
run keeps even its snapshot out of the world.
"""

from __future__ import annotations

import argparse
import os
import shutil
import statistics
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tower_styles import STYLES  # noqa: E402
from heaton_voxel.craft import design_tower, Worksite  # noqa: E402
from heaton_voxel.craft.noise import cell_hash  # noqa: E402
from heaton_voxel.nbt import NBTError, world_is_open  # noqa: E402

PLAZA = "minecraft:smooth_stone_slab[type=double]"
WALL = "minecraft:stone_bricks"
#: Lots this far apart, so the widest lot and its plaza clear the next.
SPACING = 52
SIZES = ((21, 25), (25, 25), (25, 31), (31, 33), (33, 39), (41, 33), (35, 29))
HEIGHTS = (8, 10, 12, 14, 16, 20, 24, 30, 36)


def _roll(x: int, z: int, seed: int, salt: int, options):
    return options[cell_hash(x, salt, z, seed) % len(options)]


def lots(x: int, z: int, count: int, seed: int) -> list:
    """``(lot, stories, style)`` for each building of the row."""
    row = []
    for index in range(count):
        at = x + index * SPACING
        wide, deep = _roll(at, z, seed, 11, SIZES)
        row.append(((at, z, at + wide - 1, z + deep - 1),
                    _roll(at, z, seed, 12, HEIGHTS),
                    STYLES[index % len(STYLES)]))
    return row


def run(world, x: int, z: int, *, count: int = 6, seed: int = 0,
        write: bool = False, name: str = "skyline", dimension=None,
        out=print) -> dict:
    """One session: worksite, towers, finish, checks, and a save only with
    ``write`` and nothing wrong. Returns what it found."""
    world = os.path.abspath(os.path.expanduser(os.fspath(world)))
    if world_is_open(world):
        raise NBTError(f"{world} is open in Minecraft: quit to the title "
                       f"screen first, or the game overwrites this build")
    row = lots(x, z, count, seed)
    corners = ((x - 8, z - 8),
               (x + count * SPACING + 8, z + max(s[1] for s in SIZES) + 8))
    scratch = None if write else tempfile.mkdtemp(prefix="skyline-")
    try:
        directory = None if write else os.path.join(scratch, name)
        site = Worksite.open(world, *corners, name=name, dimension=dimension,
                             directory=directory)
        b = site.builder(seed=7)
        designs = []
        for index, (lot, stories, style) in enumerate(row):
            ground = int(statistics.median(
                b.probe(cx, cz).y for cx in range(lot[0], lot[2] + 1, 3)
                for cz in range(lot[1], lot[3] + 1, 3)))
            design = design_tower(lot, ground, stories, style, seed=seed)
            out(design.describe())
            with b.stage("ground"):
                b.grade(design.plaza, ground, surface=PLAZA, retain=WALL,
                        name=f"lot-{index}")
            with b.stage("tower"):
                design.lay(b)
            designs.append(design)
        report = b.finish()
        out(f"  {report!r}")
        problems = [f"the game will not keep {fault}"
                    for fault in report.unsupported]
        for design in designs:
            problems += design.verify(b.save, dimension=dimension)
        if site.check():
            problems.append(f"site.check() found {list(site.check())}")
        saved = False
        if problems:
            for problem in problems[:12]:
                out(f"FAILED: {problem}")
            out(f"nothing was saved ({len(problems)} problems)")
        elif write:
            site.save.save()
            saved = True
            site.accept()
            out(f"saved and accepted; Worksite.rollback(world, {name!r}) "
                f"undoes it")
        else:
            out("dry run: nothing was saved")
        return {"designs": designs, "problems": problems, "saved": saved,
                "report": report}
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("world", help="path to the save directory")
    parser.add_argument("--at", nargs=2, type=int, required=True,
                        metavar=("X", "Z"), help="the first lot's corner")
    parser.add_argument("--count", type=int, default=6,
                        help="how many towers")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--name", default="skyline",
                        help="the worksite's name")
    parser.add_argument("--write", action="store_true",
                        help="save it; a dry run saves nothing")
    args = parser.parse_args(argv)
    try:
        found = run(args.world, *args.at, count=args.count, seed=args.seed,
                    write=args.write, name=args.name)
    except NBTError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1 if found["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
