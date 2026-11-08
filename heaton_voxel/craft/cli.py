"""``python -m heaton_voxel worksite``: look at a save's worksites.

    python -m heaton_voxel worksite list <world-path>
    python -m heaton_voxel worksite rollback <world-path> <name> [--dry-run]
    python -m heaton_voxel worksite retire <world-path> <name> [--delete]

``list`` and ``rollback`` only look. ``list`` reads every manifest under
``<world>/worksites/`` and each directory's size, and loads nothing else.
``rollback`` runs
:meth:`~heaton_voxel.craft.worksite.Worksite.rollback` as a dry run -- every
check the real one makes, and not a byte moved -- and prints what the undo
would do: each file it would put back or delete, when that file was last
written and whether that was after the build was accepted, and the other
worksites over the same files whose builds would go back with it -- every
later one, and every earlier one not accepted before this snapshot was
taken, since a session open beside this one may have saved its build after
it. A worksite whose manifest does not read is named as unchecked, never
left out.

**There is deliberately no ``--write``.** A rollback overwrites whatever is
in its files now, and from the bytes alone nothing tells the build you meant
to undo from weeks of play on top of it. In a world that is played, one
region file is commonly covered by several worksites and written to long
after each was accepted, so the overwrite is a judgment, and it stays a
deliberate ``Worksite.rollback(world, name)`` call from Python, made after
reading what this prints, never a command line that can be recalled from
history and run again.

``retire`` looks too, unless told ``--delete``: it says what removing an
accepted worksite's snapshot would free and what would be lost, every other
snapshot over the same files among it, by
:meth:`~heaton_voxel.craft.worksite.Worksite.retire`'s every check. It can have
a flag where ``rollback`` cannot because it touches no file of the world: what
it costs is an undo, never a build.

Exit codes: 0 when everything read; 1 when a manifest could not be read --
any, for ``list``; another worksite's, for ``rollback``, which then cannot
say whether that build goes back too, and for a ``retire`` that only looked
(damage must not exit clean); 2 for a refusal -- a path that is not a save, a
worksite that does not exist or cannot be read, a world open in Minecraft, a
snapshot whose bytes have rotted, one a ``retire`` will not take -- with the
reason on stderr.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from ..nbt import NBTError
from .worksite import (
    _RETIRING,
    Worksite,
    WorksiteSummary,
    _summarize,
    list_worksites,
)


def _moment(stamp: str | None) -> datetime | None:
    """A manifest timestamp as an aware datetime, or None if it is not one."""
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _day(stamp: str | None) -> str:
    moment = _moment(stamp)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d") if moment else "?"


def _minute(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _stamp(stamp: str | None) -> str:
    moment = _moment(stamp)
    return _minute(moment) if moment else repr(stamp)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _size(size: int | None) -> str:
    """Bytes as a person reads them, and says so if they could not be counted."""
    if size is None:
        return "size unknown"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _box(summary: WorksiteSummary) -> str:
    if summary.box is None:
        return "(no box recorded)"
    x0, z0, x1, z1 = summary.box
    return f"({x0}, {z0})..({x1}, {z1})"


def _copied(summary: WorksiteSummary, world: str) -> bool:
    return (summary.world is not None
            and os.path.realpath(summary.world) != os.path.realpath(world))


def _list(world: str) -> int:
    try:
        summaries = list_worksites(world)
    except (NBTError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not summaries:
        print(f"no worksites under {os.path.join(world, 'worksites')}")
        return 0

    width = max(len(summary.name) for summary in summaries)
    damaged = 0
    for summary in summaries:
        name = summary.name.ljust(width)
        if summary.name.startswith(_RETIRING):
            print(f"{name}  a retire cut short: 'retire --delete' finishes it"
                  f"  {_size(summary.size)}")
            continue
        if not summary.has_manifest:
            print(f"{name}  not a worksite: no manifest.json"
                  f"  {_size(summary.size)}")
            continue
        if summary.problem is not None:
            damaged += 1
            print(f"{name}  UNREADABLE: {summary.problem}  {_size(summary.size)}")
            continue
        state = "accepted" if summary.accepted else "not accepted"
        line = (f"{name}  {state:<12}  {summary.dimension} {_box(summary)}"
                f"  taken {_day(summary.created)}"
                f"  {_plural(len(summary.paths), 'file')}"
                f"  {_plural(len(summary.zones), 'zone')}"
                f"  {_size(summary.size)}")
        if summary.note:
            line += "  (note)"
        if not summary.sidecars_tracked:
            line += "  (sidecars not tracked)"
        if _copied(summary, world):
            line += f"  (recorded in {summary.world}: a copied save)"
        print(line)
    sizes = [summary.size for summary in summaries]
    total = sum(size for size in sizes if size is not None)
    print(f"{_size(total)} in all"
          + ("" if None not in sizes else ", not counting what could not be "
                                          "looked at"))
    return 1 if damaged else 0


def _rollback(world: str, name: str) -> int:
    try:
        summaries = list_worksites(world)
        # The library's own dry run: every refusal the real undo makes --
        # no such worksite, a damaged manifest, the world open in the game,
        # a rotten snapshot -- happens here, and nothing moves.
        planned = Worksite.rollback(world, name, dry_run=True)
    except (NBTError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    summary = _summarize(os.path.join(world, "worksites", name), name)

    print(f"worksite {name}: {summary.dimension} {_box(summary)}, "
          f"margin {summary.margin}")
    print(f"  taken     {_stamp(planned.taken)}")
    if planned.accepted:
        print(f"  accepted  {_stamp(planned.accepted)}")
    else:
        print("  never accepted")
    if summary.problem is not None:
        print(f"  {summary.problem}")
    if summary.note:
        print(f"  note: {summary.note}")
    if _copied(summary, world):
        print(f"  recorded in {summary.world}: a copied save")
    if not summary.sidecars_tracked:
        print("  this snapshot predates sidecar tracking; any .mcc files "
              "beside these regions are left as they are")

    if not planned.files:
        print("nothing to put back: the world already matches the snapshot")
        return 0

    accepted = _moment(planned.accepted)
    removed = set(planned.removed)
    print(f"the undo would move {_plural(len(planned.files), 'file')}:")
    for rel in sorted(planned.files):
        verb = "delete " if rel in removed else "restore"
        target = os.path.join(world, rel)
        if os.path.exists(target):
            written = datetime.fromtimestamp(os.path.getmtime(target),
                                             timezone.utc)
            when = f"written {_minute(written)}"
            if accepted is not None and written > accepted:
                when += "  -- written after the build was accepted"
        else:
            when = "absent now"
        print(f"  {verb}  {rel}  {when}")
    if planned.already:
        agrees = "matches" if planned.already == 1 else "match"
        print(f"  ({_plural(planned.already, 'more file')} already "
              f"{agrees} the snapshot)")

    later, earlier, unchecked = _others(summaries, name, planned)
    if later:
        named = ", ".join(f"{other.name} (taken {_day(other.created)})"
                          for other in later)
        print("later worksites cover these files too, and what they built "
              f"in them goes back with this: {named}")
    if earlier:
        named = ", ".join(
            f"{other.name} (taken {_day(other.created)}, "
            + (f"accepted {_day(other.accepted)})" if other.accepted
               else "never accepted)")
            for other in earlier)
        print("earlier worksites over these files whose builds may also be "
              "undone, since none was accepted before this snapshot was "
              f"taken: {named}")
    for other, why in unchecked:
        print(f"could not check {other.name} for a build over these files: "
              f"{why}")

    print("dry run: nothing moved, and this command never moves anything.")
    print(f"The undo is Worksite.rollback(world, {name!r}) from Python -- "
          "deliberately not a command, because it overwrites whatever is in "
          "these files now.")
    return 1 if unchecked else 0


def _sharing(summaries: list[WorksiteSummary], name: str, planned):
    """The other snapshots over the files of ``planned``, a retire's plan:
    those taken before it, those taken after, and those that cannot be placed.
    What a retire loses turns on the first, which still go back past this
    build where they cover its files. A manifest or a date that does not read
    is reported rather than left out, as :func:`_others` reports one."""
    taken = _moment(planned.taken)
    files = set(planned.files)
    earlier, later, unchecked = [], [], []
    for other in summaries:
        if other.name == name or not other.has_manifest:
            continue                    # itself, or a hand-made backup
        if other.problem is not None:
            unchecked.append((other, other.problem))
            continue
        if not files & set(other.paths):
            continue
        created = _moment(other.created)
        if taken is None or created is None:
            unchecked.append((other, "its snapshot cannot be placed before or "
                                     "after this one: a date does not read"))
        elif created > taken:
            later.append(other)
        else:
            earlier.append(other)
    order = (lambda other: (_moment(other.created), other.name))
    return sorted(earlier, key=order), sorted(later, key=order), unchecked


def _losses(summaries: list[WorksiteSummary], name: str, planned) -> list:
    """Print what a retire would lose, and return the snapshots it could not
    check against this one."""
    print(f"worksite {name}: {_size(planned.size)} in "
          f"{_plural(len(planned.files), 'file')}")
    print(f"  taken     {_stamp(planned.taken)}")
    print(f"  accepted  {_stamp(planned.accepted)}")
    if planned.note:
        print(f"  note: {planned.note}")
    if planned.zones:
        print(f"  its protected zones go with it: {', '.join(planned.zones)}")
    print("the way back to just before this build goes with it; what it built "
          "stays, and no file of the world is touched.")

    earlier, later, unchecked = _sharing(summaries, name, planned)
    files = set(planned.files)

    def named(others):
        return ", ".join(
            f"{other.name} (taken {_day(other.created)}, "
            f"{len(files & set(other.paths))} of {len(files)} files)"
            for other in others)

    if earlier:
        print("earlier snapshots over these files stay, and rolling one back "
              f"still undoes this build, with everything since: "
              f"{named(earlier)}")
    else:
        print("no earlier snapshot covers these files, so after this nothing "
              "goes back past this build.")
    if later:
        print("later snapshots over these files stay, and do not lean on this "
              f"one: {named(later)}")
    for other, why in unchecked:
        print(f"could not check {other.name} for a snapshot over these files: "
              f"{why}")
    return unchecked


def _retire(world: str, name: str, delete: bool) -> int:
    try:
        summaries = list_worksites(world)
        # Every refusal the real one makes, and nothing removed.
        planned = Worksite.retire(world, name, dry_run=True)
    except (NBTError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if name.startswith(_RETIRING):
        print(f"{name} is what a retire cut short left, {_size(planned.size)} "
              f"of a snapshot that is gone: no worksite, and nothing to lose.")
        unchecked = []
    else:
        unchecked = _losses(summaries, name, planned)

    if not delete:
        print(f"dry run: nothing removed. --delete removes {_size(planned.size)}"
              f", and that cannot be undone.")
        return 1 if unchecked else 0
    try:
        Worksite.retire(world, name, dry_run=False)
    except (NBTError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"removed {name}: {_size(planned.size)} freed.")
    return 0


def _others(summaries: list[WorksiteSummary], name: str, planned):
    """The other worksites whose builds a rollback of ``name`` touches.

    Snapshots copy whole region files, so putting this one back reinstates
    every byte those files held when it was taken. A worksite taken
    *later* therefore loses whatever it built in them. An *earlier* one is
    safe only if its build was already in those files when this snapshot
    was taken, and the manifests record no more than that it was accepted:
    one accepted before this was taken is passed over, and any other --
    never accepted, or accepted after, as when two sessions were open over
    the same files at once -- may be undone too and is named as such. A
    manifest that does not read, or whose dates do not, cannot be ordered
    against this one at all, and is reported rather than left out: a
    damaged record must not look like no later build.
    """
    taken = _moment(planned.taken)
    moving = set(planned.files)
    later, earlier, unchecked = [], [], []
    for other in summaries:
        if other.name == name or not other.has_manifest:
            continue                    # itself, or a hand-made backup
        if other.problem is not None:
            unchecked.append((other, other.problem))
            continue
        if not moving & set(other.paths):
            continue
        created = _moment(other.created)
        if taken is None:
            unchecked.append((other, "this worksite's own snapshot time "
                                     f"{planned.taken!r} is not a date"))
        elif created is None:
            unchecked.append((other, "its snapshot time "
                                     f"{other.created!r} is not a date"))
        elif created > taken:
            later.append(other)
        else:
            accepted = _moment(other.accepted)
            if other.accepted and accepted is None:
                unchecked.append((other, "its acceptance time "
                                         f"{other.accepted!r} is not a date"))
            elif accepted is None or accepted >= taken:
                earlier.append(other)
    order = (lambda other: (_moment(other.created), other.name))
    return sorted(later, key=order), sorted(earlier, key=order), unchecked


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m heaton_voxel worksite",
        description="Look at a save's worksites: list them, see what rolling "
                    "one back would move, or retire a settled one's snapshot. "
                    "Nothing writes to the world; the undo itself is "
                    "Worksite.rollback(world, name) from Python, and only "
                    "'retire --delete' removes anything, a snapshot.",
    )
    actions = parser.add_subparsers(dest="action", required=True,
                                    metavar="{list,rollback,retire}")
    listing = actions.add_parser(
        "list", help="every worksite a save holds, read from its manifests")
    listing.add_argument("world", help="path to the save directory (holds level.dat)")
    rollback = actions.add_parser(
        "rollback",
        help="what rolling a worksite back would move; moves nothing",
        description="Run the rollback's every check as a dry run and print "
                    "what it would move. There is no --write: the undo is "
                    "Worksite.rollback(world, name) from Python.",
    )
    rollback.add_argument("world", help="path to the save directory (holds level.dat)")
    rollback.add_argument("name", help="the worksite's name, as 'list' shows it")
    rollback.add_argument(
        "--dry-run", action="store_true",
        help="the only thing this command does; accepted so the command "
             "line says so")
    retire = actions.add_parser(
        "retire",
        help="what removing an accepted worksite's snapshot would lose; "
             "removes nothing without --delete",
        description="Say what removing a settled build's snapshot would free "
                    "and lose. With --delete, remove it: the way back to "
                    "just before that build goes, and no file of the world "
                    "is touched.",
    )
    retire.add_argument("world", help="path to the save directory (holds level.dat)")
    retire.add_argument("name", help="the worksite's name, as 'list' shows it")
    retire.add_argument(
        "--delete", action="store_true",
        help="remove the snapshot; this cannot be undone")
    args = parser.parse_args(argv)

    world = os.path.abspath(os.path.expanduser(args.world))
    if args.action == "list":
        return _list(world)
    if args.action == "retire":
        return _retire(world, args.name, args.delete)
    return _rollback(world, args.name)


def _elevation_main(argv: list[str]) -> int:
    """``python -m heaton_voxel elevation``: a building's four walls as
    rules. Reads only.

    Each wall of the rectangle ``--box`` is read by
    :meth:`~heaton_voxel.craft.build.elevation.Elevation.read` and said in
    words, which replays it first, so what prints is a rule that reproduces
    the wall. The stories are ``--stories``, or read by
    :func:`~heaton_voxel.craft.build.elevation.floors_behind` from the floors
    behind the north wall. Exit codes: 0 when all four read; 2 for a
    refusal, with the reason on stderr.
    """
    from ..nbt import world_is_open
    from ..world import MinecraftSave
    from .build import Elevation, Wall, floors_behind

    parser = argparse.ArgumentParser(
        prog="python -m heaton_voxel elevation",
        description="Read a building's four walls as rules. Reads only.")
    parser.add_argument("world", help="path to the save directory")
    parser.add_argument("--box", nargs=4, required=True,
                        metavar=("X0", "Z0", "X1", "Z1"),
                        help="the building's outer corners; coordinates "
                        "accept '~' notation")
    parser.add_argument("--floor", required=True, metavar="Y",
                        help="the floor the walls stand on")
    parser.add_argument("--top", required=True, metavar="Y",
                        help="the walls' top row")
    parser.add_argument("--stories", nargs="*", metavar="Y",
                        help="the upper floors; read from the building if "
                        "not given")
    parser.add_argument("--depth", type=int, default=2,
                        help="how many layers to read where a front stands "
                        "open (default 2)")
    parser.add_argument("--dimension", default=None)
    args = parser.parse_args(argv)

    world = os.path.expanduser(args.world)
    try:
        save = MinecraftSave.load(world)
        x0, z0, x1, z1 = save.resolve_coordinates(args.box, axes="xzxz")
        floor_y, top = save.resolve_coordinates([args.floor, args.top],
                                                axes="yy")
        if world_is_open(world):
            print("note: the world is open, so this reads what was last "
                  "saved, and a file the game is writing can read as torn",
                  file=sys.stderr)
        ring = dict(floor_y=floor_y, top=top)
        if args.stories is None:
            stories = floors_behind(save, Wall.ring(x0, z0, x1, z1, **ring)["north"],
                                    dimension=args.dimension)
            how = "read from the floors behind the north wall"
        else:
            stories = save.resolve_coordinates(args.stories,
                                               axes="y" * len(args.stories))
            how = "as given"
        walls = Wall.ring(x0, z0, x1, z1, stories=stories, **ring)
        print(f"stories at {', '.join(map(str, stories)) or 'none'} ({how})")
        for face, wall in walls.items():
            rule = Elevation.read(save, wall, depth=args.depth,
                                  dimension=args.dimension)
            print(f"\n{face} wall, {wall.low}..{wall.high}, "
                  f"{len(rule.plan(wall)):,} cells, reproduced:")
            print(rule.describe())
    except NBTError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _massing_main(argv: list[str]) -> int:
    """``python -m heaton_voxel massing``: a tower's tiers. Reads only.

    :meth:`~heaton_voxel.craft.build.massing.Massing.read` over ``--box``,
    said in words, one line a tier, the tier's walls each replayed where it
    says it read them. ``--walls`` says those walls as rules too. Exit codes:
    0 when it read; 2 for a refusal, with the reason on stderr.
    """
    from ..nbt import world_is_open
    from ..world import MinecraftSave
    from .build import Massing

    parser = argparse.ArgumentParser(
        prog="python -m heaton_voxel massing",
        description="Read a tower's tiers. Reads only.")
    parser.add_argument("world", help="path to the save directory")
    parser.add_argument("--box", nargs=6, required=True,
                        metavar=("X1", "Y1", "Z1", "X2", "Y2", "Z2"),
                        help="a box round the building; coordinates accept "
                        "'~' notation")
    parser.add_argument("--walls", action="store_true",
                        help="say each tier's walls as rules")
    parser.add_argument("--depth", type=int, default=2,
                        help="how many layers to read where a front stands "
                        "open (default 2)")
    parser.add_argument("--dimension", default=None)
    args = parser.parse_args(argv)

    world = os.path.expanduser(args.world)
    try:
        save = MinecraftSave.load(world)
        box = save.resolve_coordinates(args.box, axes="xyzxyz")
        if world_is_open(world):
            print("note: the world is open, so this reads what was last "
                  "saved, and a file the game is writing can read as torn",
                  file=sys.stderr)
        massing = Massing.read(save, box[:3], box[3:], depth=args.depth,
                               dimension=args.dimension)
        print(massing.describe())
        if args.walls:
            for index, tier in enumerate(massing.tiers):
                if tier.rule is None:
                    continue
                for face, wall in tier.walls().items():
                    print(f"\ntier {index}, {face} wall, "
                          f"{len(tier.rule_of(face).plan(wall)):,} cells, "
                          f"reproduced:")
                    print(tier.rule_of(face).describe())
    except NBTError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0
