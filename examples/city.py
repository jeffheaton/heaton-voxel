"""Plan a city around a place, look at the plan, and lay it.

    python examples/city.py <world> --towers 50                 # plans, dry runs
    python examples/city.py <world> --towers 50 --plan-only     # plans only
    python examples/city.py <world> --towers 50 --write
    python -m heaton_voxel worksite rollback <world> city

Reads the land round the player, or ``--at X Z``, and works out a city on what
it finds: streets that step round water and anything already built, blocks and
lots, parks where the ground asks for them, and towers from
``examples/tower_styles.py``, tallest downtown. The plan is drawn to
``--picture`` before anything else happens, and a dry run keeps even its
snapshot out of the world.

Quit to the title screen first: a save open in the game cannot be written.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tower_styles import ZONES  # noqa: E402
from heaton_voxel.craft import (audit, plan_city, SiteMap,  # noqa: E402
                                Worksite)
from heaton_voxel.nbt import NBTError, world_is_open  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402


def run(world, *, towers: int = 50, at=None, radius: int = 420, seed: int = 0,
        write: bool = False, plan_only: bool = False, picture=None,
        name: str = "city", dimension=None, out=print) -> dict:
    """One session: read, plan, draw, lay, check, and a save only with
    ``write`` and nothing wrong. Returns what it found."""
    world = os.path.abspath(os.path.expanduser(os.fspath(world)))
    save = MinecraftSave.load(world)
    center = at if at is not None else save.player_block_position()[::2]
    site = SiteMap.read(save, center, radius, dimension=dimension)
    out(site)
    plan = plan_city(site, towers, ZONES, seed=seed, name=name)
    out(plan.describe())
    if picture is not None:
        plan.picture(picture)
        out(f"plan drawn to {picture}")
    if plan_only:
        return {"plan": plan, "problems": [], "saved": False, "result": None}
    if world_is_open(world):
        raise NBTError(f"{world} is open in Minecraft: quit to the title "
                       f"screen first, or the game overwrites this build")
    x0, z0, x1, z1 = plan.bounds
    scratch = None if write else tempfile.mkdtemp(prefix="city-")
    try:
        directory = None if write else os.path.join(scratch, name)
        # A little over the plan: it is drawn again if a street meets
        # something the site's samples missed.
        site_work = Worksite.open(world, (x0 - 64, z0 - 64), (x1 + 64, z1 + 64),
                                  name=name, dimension=dimension,
                                  directory=directory)
        b = site_work.builder(seed=seed)
        # Only what the city adds: water has always run in the caves under it.
        rows = sorted(site.heights[cell] for cell in site.samples(plan.bounds)
                      if cell in site.heights)
        box = ((x0, rows[0] - 8, z0), (x1, 319, z1))
        before = {str(leak) for leak in audit(b.save, *box,
                                              dimension=dimension).leaks}
        result = b.city(plan)
        plan = result.plan
        report = b.finish()
        out(f"  {result!r}, drawn {result.replanned + 1} times\n"
            f"  {result.streets!r}\n  {result.blend!r}\n  {report!r}")
        for lot, why in result.skipped:
            out(f"  skipped {lot.rectangle}: {why[:120]}")
        problems = [f"a door the streets do not reach: {door}"
                    for door in result.unreached]
        for design in result.designs:
            problems += design.verify(b.save, dimension=dimension)
        found = audit(b.save, *box, dimension=dimension)
        problems += [f"audit: {leak}" for leak in found.leaks
                     if str(leak) not in before]
        problems += [f"audit: stale heightmaps in chunk {chunk}"
                     for chunk in found.heightmaps]
        if site_work.check():
            problems.append(f"site.check() found {list(site_work.check())}")
        saved = False
        if problems:
            for problem in problems[:12]:
                out(f"FAILED: {problem}")
            out(f"nothing was saved ({len(problems)} problems)")
        elif write:
            site_work.save.save()
            saved = True
            site_work.accept()
            out(f"saved and accepted; Worksite.rollback(world, {name!r}) "
                f"undoes it")
        else:
            out("dry run: nothing was saved")
        return {"plan": plan, "problems": problems, "saved": saved,
                "result": result, "report": report}
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("world", help="path to the save directory")
    parser.add_argument("--towers", type=int, default=50)
    parser.add_argument("--at", nargs=2, type=int, default=None,
                        metavar=("X", "Z"), help="the middle of the search, "
                        "the player by default")
    parser.add_argument("--radius", type=int, default=420,
                        help="how far to look for land")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--picture", default="city-plan.png",
                        help="where to draw the plan")
    parser.add_argument("--name", default="city", help="the worksite's name")
    parser.add_argument("--plan-only", action="store_true",
                        help="draw the plan and stop")
    parser.add_argument("--write", action="store_true",
                        help="save it; a dry run saves nothing")
    args = parser.parse_args(argv)
    try:
        found = run(args.world, towers=args.towers, at=args.at,
                    radius=args.radius, seed=args.seed, write=args.write,
                    plan_only=args.plan_only, picture=args.picture,
                    name=args.name)
    except NBTError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1 if found["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
