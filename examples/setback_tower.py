"""Build a tower that steps back, in one call.

    python examples/setback_tower.py <world> --at X Z            # a dry run
    python examples/setback_tower.py <world> --at X Z --write
    python -m heaton_voxel worksite rollback <world> setback-tower

Three tiers in the hand of ``examples/elevations.py``'s Palace rules, 21 by 37,
17 by 33 and 13 square, each stepped back from the last with a terrace round
it, a white core with a stair up through all of them, and a crown of glass
rings and corner fins. :data:`LOWER`, :data:`UPPER` and :data:`TOP` say how many
floors each has, and its facade stretches to fit.

Most of this is what it is built from and how it is checked:
:func:`build` is one ``b.tower`` and the lamps, where ``office_tower.py`` lays
each of those things by hand. The tower's plan is walked to every floor and
back before it is written. A dry run keeps even its snapshot out of the world.
"""

from __future__ import annotations

import argparse
import os
import shutil
import statistics
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from elevations import (PALACE_BASE, PALACE_TOP, PALACE_UPPER, PALE,  # noqa: E402
                        SANDSTONE, WHITE)
from heaton_voxel.craft import (Core, Crown, Massing, NO_SPAWNS, Tier,  # noqa: E402
                                Worksite, audit, spawnable)
from heaton_voxel.craft.build import SwitchbackStair  # noqa: E402
from heaton_voxel.nbt import NBTError, world_is_open  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402

LOWER, UPPER, TOP = 18, 7, 2
MARGIN = 4
PITCH = 4
PLANKS = "minecraft:oak_planks"
PLAZA = "minecraft:smooth_stone_slab[type=double]"
LAMP = "minecraft:glowstone"
CROWN = Crown(((2, 2), (2, 3), (2, 4), (2, 5)), skin=PALE, corner=WHITE,
              liner=WHITE, fin=WHITE, reach=-1, inner=4, rise=3, pitch=2,
              lamp=LAMP, lamps=4, cornice=WHITE, brackets=2, deck=WHITE)


class Tower:
    """Where everything stands, from the center and the ground."""

    def __init__(self, cx: int, cz: int, y: int, lower: int = LOWER,
                 upper: int = UPPER, top: int = TOP):
        self.cx, self.cz, self.y = cx, cz, y
        first = y + 8
        floors = [first + PITCH * i for i in range(lower + upper + top)]
        terrace = floors[lower - 1]
        roof = floors[lower + upper - 1]
        self.floors = tuple(floors)
        self.massing = Massing(
            Tier(cx - 10, cz - 18, cx + 10, cz + 18, faces=PALACE_BASE,
                 floor_y=y, top=terrace + 2, stories=floors[:lower],
                 floor=PLANKS, terrace=WHITE),
            Tier(cx - 8, cz - 16, cx + 8, cz + 16, faces=PALACE_UPPER,
                 floor_y=terrace, top=roof + 2, stories=floors[lower:lower + upper],
                 floor=PLANKS, terrace=WHITE),
            Tier(cx - 6, cz - 6, cx + 6, cz + 6, faces=PALACE_TOP,
                 floor_y=roof, top=floors[-1] + PITCH + 1,
                 stories=floors[lower + upper:], floor=PLANKS))
        self.stair = SwitchbackStair(
            (cx - 2, cz - 2), (cx + 1, cz + 1), axis="z", start="southwest",
            start_y=y + 1, landing=1, stair="minecraft:quartz_stairs",
            wood="minecraft:quartz_block")
        self.core = Core(cx - 3, cz - 3, cx + 2, cz + 2, wall=WHITE,
                         stair=self.stair, top=floors[-1] + PITCH, lamp=LAMP)
        self.plaza = (cx - 10 - MARGIN, cz - 18 - MARGIN,
                      cx + 10 + MARGIN, cz + 18 + MARGIN)

    def doorstep(self) -> tuple:
        return (self.cx, self.y, self.cz + 20)

    def box(self) -> tuple:
        x0, z0, x1, z1 = self.plaza
        return ((x0, self.y - 4, z0),
                (x1, self.massing.top + CROWN.height + 2, z1))


def build(b, t: Tower):
    with b.stage("ground"):
        b.grade(t.plaza, t.y, surface=PLAZA, retain=SANDSTONE, name="plaza")
    with b.stage("tower"):
        result = b.tower(t.massing, core=t.core, crown=CROWN,
                         doorstep=t.doorstep())
    with b.stage("lamps"):
        for cells in (*result.floors.values(), *result.terraces.values()):
            b.light_cells(cells, light=LAMP, light_level=NO_SPAWNS,
                          clear_above=False)
        x0, z0, x1, z1 = t.plaza
        tiers = t.massing.tiers[0]
        b.light_cells([(x, t.y, z) for x in range(x0, x1 + 1)
                       for z in range(z0, z1 + 1)
                       if not (tiers.x0 <= x <= tiers.x1
                               and tiers.z0 <= z <= tiers.z1)],
                      light=LAMP, light_level=NO_SPAWNS, clear_above=False)
    return result


def checks(save, t: Tower, site, report, dimension) -> list:
    problems = [f"the game will not keep {fault}" for fault in report.unsupported]
    problems += t.massing.verify(save, dimension=dimension)
    last = t.massing.tiers[-1]
    problems += CROWN.verify(save, *last.rectangle, last.top + 1,
                             dimension=dimension)
    for tier in t.massing.tiers:
        dark = spawnable(save, (tier.x0 + 1, tier.floor_y, tier.z0 + 1),
                         (tier.x1 - 1, tier.top, tier.z1 - 1),
                         dimension=dimension)
        problems += [f"{len(dark)} cells are dark enough to spawn in, first "
                     f"{dark[0]}"] if dark else []
    found = audit(save, *t.box(), dimension=dimension)
    problems += [f"audit: {leak}" for leak in found.leaks]
    problems += [f"audit: stale heightmaps in chunk {chunk}"
                 for chunk in found.heightmaps]
    if site.check():
        problems.append(f"site.check() found {list(site.check())}")
    return problems


def run(world, x: int, z: int, *, y=None, write: bool = False,
        name: str = "setback-tower", lower: int = LOWER, upper: int = UPPER,
        top: int = TOP, dimension=None, out=print) -> dict:
    """One session: worksite, build, finish, checks, and a save only with
    ``write`` and nothing wrong. Returns what it found."""
    world = os.path.abspath(os.path.expanduser(os.fspath(world)))
    if world_is_open(world):
        raise NBTError(f"{world} is open in Minecraft: quit to the title "
                       f"screen first, or the game overwrites this build")
    reach = 10 + MARGIN, 18 + MARGIN
    corners = (x - reach[0], z - reach[1]), (x + reach[0], z + reach[1])
    scratch = None if write else tempfile.mkdtemp(prefix="setback-tower-")
    try:
        directory = None if write else os.path.join(scratch, name)
        site = Worksite.open(world, *corners, name=name, dimension=dimension,
                             directory=directory)
        b = site.builder(seed=7)
        if y is None:
            y = int(statistics.median(
                b.probe(cx, cz).y
                for cx in range(corners[0][0], corners[1][0] + 1, 2)
                for cz in range(corners[0][1], corners[1][1] + 1, 2)))
        t = Tower(x, z, y, lower, upper, top)
        out(t.massing.describe())
        result = build(b, t)
        report = b.finish()
        out(f"  {report!r}\n  {result!r}")
        problems = checks(b.save, t, site, report, dimension)
        diff = site.diff(*t.box())
        out(f"the build changed {len(diff):,} blocks")
        saved = False
        if problems:
            for problem in problems[:12]:
                out(f"FAILED: {problem}")
            out(f"nothing was saved ({len(problems)} problems)")
        elif write:
            site.save.save()
            saved = True
            if site.diff(*t.box(), save=MinecraftSave.load(world)) == diff:
                site.accept()
                out(f"saved and accepted; Worksite.rollback(world, {name!r}) "
                    f"undoes it")
            else:
                problems.append("what reached the disk is not what was built")
                out(f"FAILED: {problems[-1]}")
        else:
            out("dry run: nothing was saved")
        return {"tower": t, "result": result, "problems": problems,
                "saved": saved, "report": report, "diff": diff}
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("world", help="path to the save directory")
    parser.add_argument("--at", nargs=2, type=int, required=True,
                        metavar=("X", "Z"), help="the tower's center")
    parser.add_argument("--y", type=int, default=None,
                        help="the ground; the median of the plaza by default")
    parser.add_argument("--name", default="setback-tower",
                        help="the worksite's name")
    parser.add_argument("--write", action="store_true",
                        help="save it; a dry run saves nothing")
    args = parser.parse_args(argv)
    try:
        found = run(args.world, *args.at, y=args.y, write=args.write,
                    name=args.name)
    except NBTError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1 if found["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
