"""Build a ten-story tower with stairs inside it, the guarded way.

    python examples/office_tower.py <world> --at X Z            # a dry run
    python examples/office_tower.py <world> --at X Z --write
    python -m heaton_voxel worksite rollback <world> office-tower

The tower is the hand of ``examples/elevations.py`` on a slimmer block, 27 by
23 and ten floors: a brick grid of punched windows between glass corner
pavilions, over a tall recessed ground story and under a wool crown and a
three-course cornice, on a stone plaza four blocks wider than it each way,
leveled to ``--y``, by default the median of what you would stand on there.
Its door is in the south front.

Inside, a white core stands in the middle of every floor with a quartz
switchback in it. A switchback's floors are as far apart as its run is long,
so a four-row story pitch wants a run of four, and the core is the stair's
four-square rectangle and one wall round it. Its flights land a floor at each
end in turn, so the wall stands open at the south end of one story and the
north end of the next. Each floor is lit flush to ``NO_SPAWNS``, with a lamp
in each long side of the core for the stair.

The session runs in the order every real one should: a worksite, the land
round the plaza protected, the build in named stages, the aftercare, the
checks, and only then the save. The checks are each printed: what the game
will not keep, every wall against its rule, a walk from the plaza to the top
floor and back, every floor reached, every lit cell dark enough to spawn in,
and the worksite's own. A dry run keeps even its snapshot out of the world.
"""

from __future__ import annotations

import argparse
import os
import shutil
import statistics
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from elevations import (BRICK, SANDSTONE, TOWER_FACES,  # noqa: E402
                        TOWER_SOUTH, tower_stories)
from heaton_voxel.craft import (NO_SPAWNS, Wall, Worksite, audit,  # noqa: E402
                                reachable, spawnable)
from heaton_voxel.craft.build import CrossSection, SwitchbackStair  # noqa: E402
from heaton_voxel.nbt import NBTError, world_is_open  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402

WIDTH, DEPTH = 27, 23
MARGIN = 4
UPPER = 9

PLANKS = "minecraft:oak_planks"
PLAZA = "minecraft:smooth_stone_slab[type=double]"
CORE = "minecraft:white_wool"
LAMP = "minecraft:glowstone"
CARPET = "minecraft:red_carpet"
GRAVEL = "minecraft:gravel"
CORNICE = CrossSection({(0, 1): "minecraft:brick_slab[type=top]",
                        (0, 2): BRICK,
                        (0, 3): "minecraft:red_sandstone_slab[type=bottom]"})


class Tower:
    """Where everything stands, from the tower's center and its ground."""

    def __init__(self, cx: int, cz: int, y: int):
        self.cx, self.cz, self.y = cx, cz, y
        self.x0, self.x1 = cx - WIDTH // 2, cx + WIDTH // 2
        self.z0, self.z1 = cz - DEPTH // 2, cz + DEPTH // 2
        self.plaza = (self.x0 - MARGIN, self.z0 - MARGIN,
                      self.x1 + MARGIN, self.z1 + MARGIN)
        self.levels = tower_stories(UPPER, y)
        self.floors = self.levels["stories"]
        self.top = self.levels["top"]
        # The stair's rectangle, and the wall round it.
        self.sx0, self.sz0 = cx - 2, cz - 2
        self.sx1, self.sz1 = cx + 1, cz + 1
        self.stair = SwitchbackStair(
            (self.sx0, self.sz0), (self.sx1, self.sz1), axis="z",
            start="southwest", start_y=y + 1, landing=1,
            stair="minecraft:quartz_stairs", wood="minecraft:quartz_block")

    def inside(self) -> list:
        """Every column inside the walls."""
        return [(x, z) for x in range(self.x0 + 1, self.x1)
                for z in range(self.z0 + 1, self.z1)]

    def core_ring(self) -> list:
        """The columns of the wall round the stair."""
        xs = range(self.sx0 - 1, self.sx1 + 2)
        zs = range(self.sz0 - 1, self.sz1 + 2)
        return [(x, z) for x in xs for z in zs
                if x in (xs[0], xs[-1]) or z in (zs[0], zs[-1])]

    def core_opening(self, level: int) -> int:
        """The z of the core's end wall that stands open over the floor at
        ``level``: south on the ground floor and every second one, where the
        flights arrive walking south, and north on the others."""
        south = (level - self.y) % 8 == 0
        return self.sz1 + 1 if south else self.sz0 - 1

    def walls(self) -> dict:
        return Wall.ring(self.x0, self.z0, self.x1, self.z1, **self.levels)

    def doorstep(self) -> tuple:
        return (self.cx, self.y, self.z1 + 2)

    def box(self) -> tuple:
        """The plaza, from under its ground to over the cornice."""
        x0, z0, x1, z1 = self.plaza
        return (x0, self.y - 4, z0), (x1, self.top + 4, z1)


def build(b, t: Tower) -> dict:
    """Everything, stage by stage. Each writer checks its own plan."""
    done = {}
    y, top = t.y, t.top
    with b.stage("ground"):
        done["ground"] = b.grade(t.plaza, y, surface=PLAZA, retain=SANDSTONE,
                                 name="plaza")
    with b.stage("shell"):
        done["shell"] = b.shell(t.x0, t.z0, t.x1, t.z1, TOWER_SOUTH,
                                faces=TOWER_FACES, **t.levels)
    with b.stage("lobby floor"):
        b.fill((t.x0 + 2, y, t.z0 + 2), (t.x1 - 2, y, t.z1 - 2), PLANKS)
    with b.stage("core"):
        opened = {(x, row, t.core_opening(level))
                  for level in (y, *t.floors)
                  for x in range(t.sx0, t.sx1 + 1)
                  for row in range(level + 1, level + 4)}
        for x, z in t.core_ring():
            for row in range(y + 1, top):
                if (x, row, z) not in opened:
                    b.set(x, row, z, CORE)
    with b.stage("floors"):
        ring = set(t.core_ring())
        for level in t.floors:
            for x, z in t.stair.clear(level, t.inside()):
                if (x, z) not in ring:
                    b.set(x, level, z, PLANKS)
    with b.stage("stair"):
        done["stair"] = b.switchback_stair(t.stair, y + 1, t.floors[-1])
    with b.stage("roof"):
        b.fill((t.x0 + 1, top, t.z0 + 1), (t.x1 - 1, top, t.z1 - 1), PLANKS)
        b.fill((t.x0, top + 1, t.z0), (t.x1, top + 1, t.z1), GRAVEL)
        x0, z0, x1, z1 = t.x0 - 1, t.z0 - 1, t.x1 + 1, t.z1 + 1
        done["cornice"] = b.extrude(
            [(x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0)], CORNICE,
            follow=top - 1, through=("plaza",))
    with b.stage("carpet"):
        b.fill((t.cx - 1, y + 1, t.sz1 + 1), (t.cx + 1, y + 1, t.z1 - 2), CARPET)
    with b.stage("lamps"):
        # The stair's own, two to a story in the core's long walls, which
        # light the floor outside as well.
        for level in (y, y + 4, *t.floors):
            row = level + 2
            for x in (t.sx0 - 1, t.sx1 + 1):
                b.set(x, row, t.cz, LAMP)
        ring = set(t.core_ring())
        for level in (y, *t.floors):
            cells = [(x, level, z) for x, z in t.stair.clear(level, t.inside())
                     if (x, z) not in ring]
            b.light_cells(cells, light=LAMP, light_level=NO_SPAWNS,
                          clear_above=False)
        deck = [(x, top + 1, z) for x in range(t.x0, t.x1 + 1)
                for z in range(t.z0, t.z1 + 1)]
        b.light_cells(deck, light=LAMP, light_level=NO_SPAWNS, clear_above=False)
        x0, z0, x1, z1 = t.plaza
        plaza = [(x, y, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)
                 if not (t.x0 + 2 <= x <= t.x1 - 2 and t.z0 + 2 <= z <= t.z1 - 2)]
        b.light_cells(plaza, light=LAMP, light_level=NO_SPAWNS, clear_above=False)
    return done


def checks(b, t: Tower, site, report, dimension) -> tuple:
    """Every check: ``(problems, walk)``, each result printed by the caller."""
    problems = []
    save = b.save
    problems += [f"the game will not keep {fault}"
                 for fault in report.unsupported]
    for face, wall in t.walls().items():
        problems += [f"{face} wall: {wrong}" for wrong in
                     TOWER_FACES[face].verify(save, wall, dimension=dimension)]
    x0, z0, x1, z1 = t.plaza
    walk = reachable(save, t.doorstep(), (x0, t.y - 1, z0),
                     (x1, t.top, z1), dimension=dimension)
    problems += [f"{len(walk.stranded)} cells can be reached and not left, "
                 f"first {min(walk.stranded)}"] if walk.stranded else []
    for level in t.floors:
        if not walk.footing(level):
            problems.append(f"the floor at y={level} cannot be reached")
    dark = spawnable(save, (t.x0 + 1, t.y, t.z0 + 1),
                     (t.x1 - 1, t.top + 2, t.z1 - 1), dimension=dimension)
    problems += [f"{len(dark)} cells inside are dark enough to spawn in, "
                 f"first {dark[0]}"] if dark else []
    found = audit(save, *t.box(), dimension=dimension)
    problems += [f"audit: {leak}" for leak in found.leaks]
    problems += [f"audit: moving water {body.seed}" for body in found.flowing]
    problems += [f"audit: stale heightmaps in chunk {chunk}"
                 for chunk in found.heightmaps]
    if site.check():
        problems.append(f"site.check() found {list(site.check())}")
    return problems, walk


def run(world, x: int, z: int, *, y: int | None = None, write: bool = False,
        name: str = "office-tower", dimension: str | None = None,
        out=print) -> dict:
    """One session: worksite, build, finish, checks, and a save only with
    ``write`` and nothing wrong. Returns what it found."""
    world = os.path.abspath(os.path.expanduser(os.fspath(world)))
    if world_is_open(world):
        raise NBTError(f"{world} is open in Minecraft: quit to the title "
                       f"screen first, or the game overwrites this build")
    x0, z0 = x - WIDTH // 2 - MARGIN, z - DEPTH // 2 - MARGIN
    x1, z1 = x + WIDTH // 2 + MARGIN, z + DEPTH // 2 + MARGIN
    scratch = None if write else tempfile.mkdtemp(prefix="office-tower-")
    try:
        directory = None if write else os.path.join(scratch, name)
        site = Worksite.open(world, (x0, z0), (x1, z1), name=name,
                             dimension=dimension, directory=directory)
        b = site.builder(seed=7)
        if y is None:
            y = int(statistics.median(
                b.probe(cx, cz).y for cx in range(x0, x1 + 1, 2)
                for cz in range(z0, z1 + 1, 2)))
        t = Tower(x, z, y)
        ground, height = (y - 8, t.top + 8)
        for side, corners in {
                "north": ((x0 - 3, ground, z0 - 3), (x1 + 3, height, z0 - 2)),
                "south": ((x0 - 3, ground, z1 + 2), (x1 + 3, height, z1 + 3)),
                "west": ((x0 - 3, ground, z0 - 3), (x0 - 2, height, z1 + 3)),
                "east": ((x1 + 2, ground, z0 - 3), (x1 + 3, height, z1 + 3)),
        }.items():
            site.protect(*corners, name=f"beside the plaza, {side}")
        out(f"tower {WIDTH} by {DEPTH} at ({t.x0}, {t.z0})..({t.x1}, {t.z1}), "
            f"ground y={y}, {UPPER + 1} floors, top row y={t.top}; plaza "
            f"{t.plaza}")
        done = build(b, t)
        report = b.finish()
        out(f"  {report!r}")
        problems, walk = checks(b, t, site, report, dimension)
        reached = [level for level in t.floors if walk.footing(level)]
        out(f"walk: {len(walk.reached):,} cells from the plaza, "
            f"{len(reached) + 1} of {UPPER + 1} floors, "
            f"{len(walk.stranded)} stranded")
        out(f"stages: {b.journal.tally()}")
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
        return {"tower": t, "done": done, "problems": problems, "walk": walk,
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
    parser.add_argument("--name", default="office-tower",
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
