"""A build already standing, judged in one call.

A Builder checks what it writes before it writes it, and a paste from the
app or a schematic checks none of that: it lands whole, and every picture
draws it finished. :func:`audit` puts the questions to what stands in a box,
whoever built it, with the checks the writers answer to:

* :func:`~heaton_voxel.craft.survey.unsupported` -- what the game will not
  keep, and the doors nothing opens;
* :func:`~heaton_voxel.craft.survey.water_body` -- water that is still
  moving, or was left to run. A source in a build shows up as the flow it
  feeds, which ``contained`` cannot see once it has settled: settled water
  has nowhere left to go;
* :func:`~heaton_voxel.craft.survey.contained` -- water that stands where it
  was written, and runs the first time a block beside it changes. The model
  reports every side the game's rules let water into, so it can name a side
  the game would not take first, and never misses one;
* the stored heightmaps against the blocks
  (:meth:`~heaton_voxel.blocks.Blocks.stale_heightmaps`);
* :func:`~heaton_voxel.craft.survey.reachable`, from a doorstep the caller
  names, since nothing else can say where a build is entered.

Read-only, and nothing here refuses: a finding is a thing to look at, not a
fault to fix, and a one-way door or a fountain may be exactly what was
meant. A chunk that cannot be read raises, because could-not-read must never
pass for clean; one the game has not finished is named in
:attr:`Audit.skipped`.

    python -m heaton_voxel audit <world> --box X1 Y1 Z1 X2 Y2 Z2 [--from X Y Z]

Exit codes: 0 when nothing was found; 1 when something was; 2 for a refusal,
with the reason on stderr.
"""

from __future__ import annotations

import argparse
import os
import sys

from ..nbt import NBTError, world_is_open
from ..world import MinecraftSave
from .survey import contained, reachable, unsupported, water_body

__all__ = ["Audit", "audit"]

#: How many of each finding :meth:`Audit.report` prints.
LISTED = 12


class Audit:
    """What :func:`audit` found in a box."""

    __slots__ = ("box", "unsupported", "flowing", "leaks", "heightmaps", "walk",
                 "skipped")

    def __init__(self, box, unsupported_found, flowing, leaks, heightmaps, walk,
                 skipped):
        #: The box, ``((x0, y0, z0), (x1, y1, z1))``, inclusive.
        self.box = box
        #: Every :class:`~heaton_voxel.craft.survey.Unsupported` in it.
        self.unsupported = unsupported_found
        #: Every :class:`~heaton_voxel.craft.survey.WaterBody` with moving
        #: water in the box, largest first, read within its rectangle. Its
        #: ``flowing`` names the cells, and the seed one of them.
        self.flowing = flowing
        #: Every :class:`~heaton_voxel.craft.survey.Leak` the box's water
        #: would run through the next time a block beside it changes.
        self.leaks = leaks
        #: ``{(chunk_x, chunk_z): {map name: columns wrong}}``.
        self.heightmaps = heightmaps
        #: The :class:`~heaton_voxel.craft.survey.ReachMap` from the
        #: doorstep, or None where the caller named none.
        self.walk = walk
        #: ``{(chunk_x, chunk_z): why}`` for the chunks not judged: never
        #: generated, unfinished, or saved by Minecraft 1.17.
        self.skipped = skipped

    @property
    def stranded(self) -> frozenset:
        """Cells the walk reached and could not get back from."""
        return self.walk.stranded if self.walk is not None else frozenset()

    @property
    def clean(self) -> bool:
        """True when nothing was found. A box partly :attr:`skipped`, or
        whose walk was not asked for, is clean only as far as it was read."""
        return not (self.unsupported or self.flowing or self.leaks
                    or self.heightmaps or self.stranded)

    def report(self) -> str:
        """The findings in words, each kind cut off at :data:`LISTED`."""
        low, high = self.box
        lines = [f"audit of {low}..{high}: "
                 + ("nothing found" if self.clean else "findings below")]

        def listing(title, found):
            lines.append(f"{title}: {len(found):,}")
            lines.extend(f"  {item}" for item in list(found)[:LISTED])
            if len(found) > LISTED:
                lines.append(f"  ... {len(found) - LISTED:,} more")

        if self.unsupported:
            listing("blocks the game will not keep, and doors", self.unsupported)
        if self.flowing:
            listing("bodies of moving water", [
                f"{len(body.flowing):,} of {len(body.cells):,} cells moving, "
                f"seen from {body.seed}" for body in self.flowing])
        if self.leaks:
            listing("water that runs at the next update", self.leaks)
        if self.heightmaps:
            listing("chunks whose stored heightmaps are stale", [
                f"chunk {chunk}: " + ", ".join(
                    f"{name} {columns:,}" for name, columns in maps.items())
                for chunk, maps in sorted(self.heightmaps.items())])
        if self.walk is None:
            lines.append("walk: not asked for (it needs a doorstep)")
        elif self.stranded:
            listing("cells you can reach and not leave", sorted(self.stranded))
        else:
            lines.append(f"walk: {len(self.walk.reached):,} cells, "
                         f"round trip everywhere")
        if self.skipped:
            listing("chunks not judged", [
                f"chunk {chunk}: {why}"
                for chunk, why in sorted(self.skipped.items())])
        return "\n".join(lines)

    def __repr__(self) -> str:
        parts = [f"{label} {len(found):,}"
                 for label, found in (("unsupported", self.unsupported),
                                      ("moving water", self.flowing),
                                      ("leaks", self.leaks),
                                      ("stale chunks", self.heightmaps),
                                      ("stranded", self.stranded),
                                      ("skipped", self.skipped))
                 if found]
        return f"<Audit {', '.join(parts) if parts else 'nothing found'}>"


def _chunks(dim, box):
    """The stale maps and the unjudged chunks of a box, by chunk."""
    (x0, _, z0), (x1, _, z1) = box
    stale, skipped = {}, {}
    for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
        for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
            accessor = dim.blocks(chunk_x, chunk_z)    # raises if unreadable
            key = (chunk_x, chunk_z)
            if accessor is None:
                skipped[key] = "never generated"
            elif accessor.legacy:
                skipped[key] = "saved by Minecraft 1.17"
            elif not accessor.finished:
                skipped[key] = f"unfinished ({accessor.status})"
            else:
                wrong = accessor.stale_heightmaps()
                if wrong:
                    stale[key] = wrong
    return stale, skipped


def _water(save, box, dimension):
    """The bodies of moving water in a box, and the leaks of all its water.
    Every body's flood stays in the box's rectangle, so a box on a sea's
    shore reads the shore, and it is never told a body is too big, since
    the rectangle bounds it."""
    low, high = box
    (x0, y0, z0), (x1, y1, z1) = low, high
    within = ((x0, z0), (x1, z1))
    limit = (x1 - x0 + 1) * (z1 - z0 + 1) * (y1 - y0 + 1 + 512)
    bodies, seen = [], set()
    for cell in save.find_blocks(low, high, "water",
                                 dimension=dimension).positions():
        if cell not in seen:
            body = water_body(save, cell, within=within, limit=limit,
                              dimension=dimension)
            seen |= body.cells
            bodies.append(body)
    plan = {cell: save.get_block(*cell, dimension=dimension) for cell in sorted(seen)
            if y0 <= cell[1] <= y1}
    leaks = contained(save, plan, dimension=dimension).standing if plan else []
    moving = sorted((body for body in bodies if body.flowing),
                    key=lambda body: (-len(body.flowing), body.seed))
    return moving, leaks


def audit(save, corner1, corner2, *, start=None, allow_jump: bool = True,
          dimension: str | None = None) -> Audit:
    """Everything the checks can say about a box as it stands. Read-only.

    The corners are ``(x, y, z)``, inclusive. ``start``, the block you stand
    on to begin, adds a walk of the box both ways; ``allow_jump=False`` walks
    it without jumping. A chunk that exists but cannot be read raises a
    plain :class:`~heaton_voxel.nbt.NBTError`.
    """
    low = tuple(min(int(a), int(b)) for a, b in zip(corner1, corner2))
    high = tuple(max(int(a), int(b)) for a, b in zip(corner1, corner2))
    dim = save.dimension(dimension)
    stale, skipped = _chunks(dim, (low, high))
    moving, leaks = _water(save, (low, high), dimension)
    walk = None
    if start is not None:
        walk = reachable(save, tuple(int(c) for c in start), low, high,
                         allow_jump=allow_jump, dimension=dimension)
    return Audit((low, high), unsupported(save, low, high, dimension=dimension),
                 moving, leaks, stale, walk, skipped)


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m heaton_voxel audit",
        description="Judge what stands in a box. Reads only.")
    parser.add_argument("world", help="path to the save directory")
    parser.add_argument(
        "--box", nargs=6, required=True,
        metavar=("X1", "Y1", "Z1", "X2", "Y2", "Z2"),
        help="the box, inclusive; coordinates accept '~' notation")
    parser.add_argument(
        "--from", dest="start", nargs=3, metavar=("X", "Y", "Z"),
        help="walk the box both ways from the block you stand on here")
    parser.add_argument("--no-jump", action="store_true",
                        help="walk without jumping")
    parser.add_argument("--dimension", default=None)
    args = parser.parse_args(argv)

    world = os.path.expanduser(args.world)
    try:
        save = MinecraftSave.load(world)
        box = save.resolve_coordinates(args.box)
        start = save.resolve_coordinates(args.start) if args.start else None
        if world_is_open(world):
            print("note: the world is open, so this reads what was last saved",
                  file=sys.stderr)
        found = audit(save, box[:3], box[3:], start=start,
                      allow_jump=not args.no_jump, dimension=args.dimension)
    except NBTError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(found.report())
    return 0 if found.clean else 1
