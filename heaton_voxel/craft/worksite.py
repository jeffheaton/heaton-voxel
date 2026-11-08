"""The worksite: what every building session built by hand, once each.

Three habits kept every large build safe, and each session re-invented
them as shell scripts: a pristine copy of the region files before the
first edit (the rollback), a content check over things that must not
change (the guardrail that caught a road carved through existing paths),
and pictures before and after (the eyes). A :class:`Worksite` is those
three habits as one object with a manifest, so a build is always one call
away from never having happened.

The snapshot is *files*, not blocks: region, entity and POI files are
copied byte-for-byte, and restore puts those exact bytes back — the same
lossless standard as everything else in this toolkit. The guard is
*content*: a protected zone digests block states, so a neighboring edit
that legitimately rewrites the shared region file does not trip it, and a
single flipped block inside the zone does.

Three refusals carry the scars of real sessions. A worksite will not open
while Minecraft has the world open — a snapshot torn by a live flush is a
corrupt rollback, the worst artifact there is. It will not restore a
snapshot whose own bytes have rotted since it was taken, because writing
corruption over a world is worse than the build it was undoing. And a
worksite that finds its world changed since the snapshot refuses to
resume, naming the files: building on a stale baseline is how one session
overwrote an evening of play, and ``resnapshot=True`` is the deliberate
way past it.

That last refusal has a consequence worth stating, because it looks like
a bug the first time it bites. Once a build is saved, *its own bytes are
drift*: from the digests alone there is nothing to tell a build you meant
from a week of somebody's play, so a later session cannot reopen the
worksite, and ``resnapshot=True`` would rebase the baseline onto the very
build you wanted undone. Undoing from outside the session that built is
therefore its own operation — :meth:`Worksite.rollback`, which reads the
manifest, checks the snapshot against it and puts those bytes back
without going through :meth:`Worksite.open` at all. It hands back a
receipt rather than a worksite, because whatever it just overwrote, it
could not have known whether you wanted it.

A worksite also hands out the Builder that builds inside it
(:meth:`Worksite.builder`), and that Builder cannot write where the
rollback could not follow: every write it makes is admitted first, and one
that would land in a region file the snapshot does not hold -- or reach
one through the chunks :meth:`~heaton_voxel.craft.build.Builder.finish`
settles around it -- or touch a protected zone, is refused before a block
moves. :meth:`Worksite.check` names the files a restore would leave
behind as well as the zones that changed, and :meth:`Worksite.diff` is
what the build actually changed, block by block, with "before" read from
the snapshot's own copies.

Finding a worksite to undo is :func:`list_worksites`, which reads every
manifest a save holds without loading the save, and
``python -m heaton_voxel worksite list|rollback`` is the command line over
both. The command only looks -- its ``rollback`` names every file the undo
would move, the other builds it would take with it and the files written
since acceptance -- and the undo itself stays a deliberate
:meth:`Worksite.rollback` call.

Like the rest of ``craft``, this sits above the deterministic library and
is Python-only; nothing below imports it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import posixpath
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from typing import NamedTuple

from ..blocks import BlockState
from ..nbt import NBTError, atomic_write, world_is_open
from ..world import Dimension, MinecraftSave

#: Chunks the aftercare litany can reach past the build box; the snapshot
#: covers the box plus this many blocks on every side.
DEFAULT_MARGIN = 32


def _rel(path: str, base: str) -> str:
    """``path`` relative to ``base`` as a manifest spells it: with forward
    slashes on every platform, so a worksite taken on one machine reads on
    another and every key a check compares is spelled one way. Windows
    takes the forward slashes back when a key is joined onto the world."""
    return os.path.relpath(path, base).replace(os.sep, "/")


#: A protected zone bigger than this is refused: the digest walks every
#: block, and a guard that takes minutes stops being run.
MAX_ZONE_BLOCKS = 1_000_000

_MANIFEST = "manifest.json"
_RETIRING = ".retiring-"
_FILES = "files"

#: An oversized chunk's payload, kept beside its region as
#: ``c.<chunk x>.<chunk z>.mcc`` in global chunk coordinates.
_SIDECAR = re.compile(r"^c\.(-?\d+)\.(-?\d+)\.mcc$")


def _ground_box(corner1, corner2) -> tuple[int, int, int, int]:
    """Two corners, ``(x, z)`` or ``(x, y, z)``, into a sorted x/z box."""
    x0, z0 = int(corner1[0]), int(corner1[-1])
    x1, z1 = int(corner2[0]), int(corner2[-1])
    return (min(x0, x1), min(z0, z1), max(x0, x1), max(z0, z1))


def _block_of(corner) -> tuple[int, int, int]:
    """An ``(x, y, z)`` corner as the block it is in: floored, never
    truncated, so ``-330.5`` is block ``-331`` -- truncating toward zero
    would name the block beside it on every negative axis."""
    return tuple(math.floor(value) for value in corner[:3])


_AIR = BlockState("minecraft:air")


def _section_view(accessor, section_y: int, low: int, high: int):
    """One section of a chunk's blocks, as :meth:`Worksite.diff` compares
    them: ``(palette, indices, first_y, last_y)``, where ``indices`` is None
    for a section holding no block array (all air, as ``Blocks.get`` reads
    it) and ``first_y..last_y`` is the part of ``low..high`` inside the
    world the chunk holds -- empty for a section it does not store. None
    when there is no chunk at all."""
    if accessor is None:
        return None
    section = accessor.sections.get(section_y)
    first, last = max(low, accessor.min_y), min(high, accessor.max_y)
    if section is None:
        return ((), None, 0, -1)
    container = section.blocks
    if container is None:
        return ((_AIR,), None, first, last)
    return (container.palette, container.indices(), first, last)


def _row(view, y: int):
    """``(palette, indices)`` for level ``y`` of a section view, or None
    where the view holds no block at that height."""
    if view is None or not view[2] <= y <= view[3]:
        return None
    return view[0], view[1]


def _cell(row, index: int) -> BlockState | None:
    """The state at a section index of a :func:`_row`, or None."""
    if row is None:
        return None
    palette, indices = row
    return palette[0] if indices is None else palette[indices[index]]


def _same_state(before: BlockState | None, after: BlockState | None) -> bool:
    """Whether two cells hold the same game state, a property either one
    leaves out read as its block's default
    (:meth:`~heaton_voxel.blocks.BlockState.canonical`): a 26.2 chunk keeps
    a state as it was spelled, so ``grass_block`` written over
    ``grass_block[snowy=false]`` is the block it was, as a 26.3 chunk,
    which decodes every state whole, already says."""
    if before is None or after is None:
        return before is after
    return before.canonical() == after.canonical()


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for piece in iter(lambda: handle.read(1 << 20), b""):
            digest.update(piece)
    return digest.hexdigest()


def _present(path: str, what: str) -> bool:
    """Whether ``path`` exists, telling absent from unreadable.

    ``os.path.exists`` answers False for a path the process may not look
    at, which would make a worksite directory nobody can open read as no
    worksite at all. Here only a path that is not there is False; one that
    cannot be looked at raises an :class:`NBTError` naming ``what`` it was
    meant to be.
    """
    try:
        os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as exc:
        raise NBTError(f"{path!r} cannot be read as {what}: {exc}") from exc
    return True


def _load_manifest(directory: str, name: str) -> dict:
    """A worksite's manifest, or an :class:`NBTError` saying why there is
    none to be had: no file, a file that cannot be read or is not JSON, or
    JSON that records no files. All are refusals by name rather than a
    traceback, because "could not read the snapshot's record" must never
    look like "there was nothing to put back" -- and a manifest the process
    may not open is unreadable, never absent."""
    path = os.path.join(directory, _MANIFEST)
    if not _present(path, "a worksite manifest"):
        raise NBTError(
            f"no worksite {name!r} at {directory!r}: there is no "
            f"{_MANIFEST} there, so there is no snapshot to go back to."
        )
    try:
        with open(path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:     # JSON and UTF-8 errors are ValueErrors
        raise NBTError(
            f"{path!r} cannot be read as a worksite manifest: {exc}"
        ) from exc
    if not (isinstance(manifest, dict)
            and isinstance(manifest.get(_FILES), dict)):
        raise NBTError(
            f"{path!r} is not a worksite manifest: it records "
            "no files, so it cannot say what to put back."
        )
    return manifest


def _disk_size(directory: str) -> int | None:
    """The bytes under ``directory``, by ``stat`` alone, links not followed;
    None where any of it cannot be looked at, which is not the same as
    nothing."""
    def fail(exc):
        raise exc

    total = 0
    try:
        for root, _, names in os.walk(directory, onerror=fail):
            total += sum(os.lstat(os.path.join(root, name)).st_size
                         for name in names)
    except OSError:
        return None
    return total


class RetireResult:
    """What a :meth:`Worksite.retire` removed, or with ``dry_run`` would.

    ``name`` is the worksite and ``size`` the bytes its snapshot holds, None
    if some of it could not be looked at. ``files`` is every world-relative
    file it covered, ``zones`` the protected zones it held and ``note``
    whatever a person left in its manifest. ``taken`` and ``accepted`` are
    its own timestamps.
    """

    __slots__ = ("name", "taken", "accepted", "size", "files", "zones", "note",
                 "dry_run")

    def __init__(self, summary, *, dry_run: bool):
        self.name = summary.name
        self.taken = summary.created
        self.accepted = summary.accepted
        self.size = summary.size
        self.files = summary.paths
        self.zones = summary.zones
        self.note = summary.note
        self.dry_run = bool(dry_run)

    def __repr__(self) -> str:
        verb = "would remove" if self.dry_run else "removed"
        return (f"<RetireResult {verb} {self.name!r}, {self.size} bytes, "
                f"{len(self.files)} files, snapshot taken {self.taken}>")


class RollbackResult:
    """What a :meth:`Worksite.restore` or :meth:`Worksite.rollback` put back.

    ``files`` names every world file that moved, world-relative, because a
    rollback run from outside the session that built needs to say what it
    overwrote and not merely how much. ``removed`` names the ones among
    them that went (or would go) by deletion rather than by having the
    snapshot's bytes written over them -- files the snapshot recorded as
    absent, and sidecars the build made -- and ``deleted`` counts them.
    ``taken`` and ``accepted`` are the snapshot's own timestamps: an
    accepted worksite is the one most likely to have been played in since,
    and this is where that shows.
    """

    __slots__ = ("files", "removed", "already", "taken", "accepted",
                 "dry_run")

    def __init__(self, taken, accepted, *, dry_run: bool = False):
        self.files: list[str] = []
        self.removed: list[str] = []
        self.already = 0
        self.taken = taken
        self.accepted = accepted
        self.dry_run = bool(dry_run)

    @property
    def deleted(self) -> int:
        """How many of :attr:`files` are deletions: ``len(removed)``."""
        return len(self.removed)

    def __repr__(self) -> str:
        count = len(self.files)
        verb = "would put back" if self.dry_run else "put back"
        parts = [f"{verb} {count} file{'' if count == 1 else 's'}"]
        if self.deleted:
            parts.append(f"{self.deleted} of them deleted")
        if self.already:
            parts.append(f"{self.already} already matched")
        parts.append(f"snapshot taken {self.taken}")
        if self.accepted:
            parts.append(f"accepted {self.accepted}")
        return f"<RollbackResult {', '.join(parts)}>"


class CheckResult(list):
    """What :meth:`Worksite.check` found wrong, as one list: the names of
    the protected zones whose content changed, then the world-relative
    paths of the files ``save()`` would write that the snapshot does not
    hold -- so ``if site.check():`` still means "something is wrong", and
    an empty result still equals ``[]``.

    ``zones`` and ``uncovered`` are the two halves apart. ``uncovered`` is
    exactly the files a :meth:`Worksite.restore` would leave as they are,
    so a build is only ever as undoable as it is empty.
    """

    __slots__ = ("zones", "uncovered")

    def __init__(self, zones=(), uncovered=()):
        self.zones: list[str] = list(zones)
        self.uncovered: list[str] = list(uncovered)
        super().__init__(self.zones + self.uncovered)

    def __repr__(self) -> str:
        return (f"CheckResult(zones={self.zones!r}, "
                f"uncovered={self.uncovered!r})")


class CellChange(NamedTuple):
    """One cell of a :class:`SiteDiff`: the state the snapshot holds there
    and the state the save holds now. None on either side is no block at
    all -- the chunk does not exist on that side, or the cell is outside
    the world it holds."""

    before: BlockState | None
    after: BlockState | None


class SiteDiff(dict):
    """``{(x, y, z): CellChange}`` for every cell of a box whose block state
    differs from the snapshot's, the keys in sorted ``(x, y, z)`` order;
    empty when nothing in the box changed. ``box`` is the box compared,
    ``(x0, y0, z0, x1, y1, z1)`` inclusive. Block states only: light,
    heightmaps, biomes and entities are not compared."""

    __slots__ = ("box",)

    def __init__(self, changes=(), box=(0, 0, 0, 0, 0, 0)):
        super().__init__(changes)
        self.box: tuple[int, int, int, int, int, int] = tuple(box)

    def __repr__(self) -> str:
        if not self:
            return f"<SiteDiff no change in {self.box}>"
        moves = Counter((change.before, change.after)
                        for change in self.values())
        common = ", ".join(
            f"{count} {'nothing' if before is None else before} -> "
            f"{'nothing' if after is None else after}"
            for (before, after), count in moves.most_common(3))
        more = ", ..." if len(moves) > 3 else ""
        count = len(self)
        return (f"<SiteDiff {count} cell{'' if count == 1 else 's'} changed "
                f"in {self.box}: {common}{more}>")


class SiteGuard:
    """The write rule a :class:`Worksite` hands the Builder it makes
    (:meth:`Worksite.builder`), asked before every write that Builder
    makes. It only reads; it is not exported, because the way to have one
    is ``site.builder()``.

    A block write is admitted only where :meth:`Worksite.restore` can take
    it back and no protected zone is touched:

    * **every region file it reaches is in the snapshot** -- copied, or
      recorded absent -- and it reaches the file of its own chunk and of
      the eight chunks around it, because
      :meth:`~heaton_voxel.craft.build.Builder.finish` relights those and
      settles connections a block across the border into them. Coverage is
      the manifest's own list of files, so it is region-granular: a write
      anywhere in a held region is restorable, including outside the box
      the worksite renders. The snapshot holds a region's entity and POI
      files with its terrain, so the POI records finish drops are held by
      the same rule.
    * **no write meets a protected zone**, even one writing the state
      already there, and for a placement that includes the neighbors it may
      re-derive (:meth:`~heaton_voxel.craft.build.Builder._placement_footprint`).
      The zones are the manifest's, read at every write, so a zone
      protected after the Builder was made holds it too.

    A spawn needs the entities file of its own region; a zone is block
    content and does not keep entities out.

    A guard is bound to the save it was made with. A :meth:`Worksite.restore`
    discards that save -- what it holds is the world the restore undid --
    so every write and every finish after it raises a plain ``NBTError``,
    and so does every read the Builder makes of that save
    (:attr:`~heaton_voxel.craft.build.Builder.save`): the guard is dead,
    not refusing something a script might catch and go on past.
    """

    __slots__ = ("site", "save", "dimension", "_directories")

    def __init__(self, site: "Worksite", save: MinecraftSave):
        self.site = site
        self.save = save
        self.dimension = site.dimension
        dim = save.dimension(site.dimension)
        # World-relative, as the manifest spells its files.
        self._directories = tuple(
            _rel(path, site.world)
            for path in (dim.region_dir, dim.entities_dir))

    @property
    def name(self) -> str:
        """The worksite's name, which every refusal opens with."""
        return self.site.name

    def live(self) -> None:
        """Raise ``NBTError`` if the worksite has been restored since this
        guard was made."""
        if self.site._save is not self.save:
            raise NBTError(
                f"worksite {self.site.name!r} was restored after this builder "
                f"was made: its save holds the world the restore undid, and "
                f"anything saved from it would be written over the restored "
                f"files. Make a new one with site.builder()")

    def refusals(self, boxes, *, kind: str = "blocks") -> list[str]:
        """Why the inclusive ``(x0, y0, z0, x1, y1, z1)`` ``boxes`` may not be
        written: one fault for each file they reach that the snapshot does
        not hold, then one for each protected zone they meet, each naming
        the first write that reaches it, in the order of ``boxes``. Empty
        when they may.

        ``kind`` is ``"blocks"`` for block writes, ``"entities"`` for a
        spawn (each box one cell: its own region's entities file, and no
        zones), or ``"settle"`` for the chunks ``finish()`` settles
        (coverage alone, as for a block write)."""
        self.live()
        if kind not in ("blocks", "entities", "settle"):
            raise NBTError(f"a guard admits blocks, entities or settle, not "
                           f"{kind!r}")
        files = self.site._manifest[_FILES]
        regions, entities = self._directories
        faults: list[str] = []
        reported: set[str] = set()
        held: set[tuple[int, int]] = set()      # regions found held, this call
        # Chunks whose whole ring was found held: the common case, a long
        # plan of single cells in a handful of chunks, asks once a chunk.
        sound: set[tuple[int, int]] = set()

        def uncovered(directory, rx, rz, cell, how):
            rel = posixpath.join(directory, f"r.{rx}.{rz}.mca")
            if rel in files or rel in reported:
                return
            reported.add(rel)
            faults.append(
                f"{rel} is not in worksite {self.site.name!r}'s snapshot, "
                f"and {how(cell)}: restore() could not take that back")

        for x0, y0, z0, x1, y1, z1 in boxes:
            cell = (x0, y0, z0)
            if kind == "entities":
                uncovered(entities, x0 >> 9, z0 >> 9, cell,
                          lambda at: f"the spawn at {at} lands in it")
                continue
            chunk = (x0 >> 4, z0 >> 4)
            single = chunk == (x1 >> 4, z1 >> 4)
            if single and chunk in sound:
                continue
            before = len(faults)
            own = [(rx, rz) for rx in range(x0 >> 9, (x1 >> 9) + 1)
                   for rz in range(z0 >> 9, (z1 >> 9) + 1)]
            ring = [(rx, rz)
                    for rx in range(((x0 >> 4) - 1) >> 5,
                                    (((x1 >> 4) + 1) >> 5) + 1)
                    for rz in range(((z0 >> 4) - 1) >> 5,
                                    (((z1 >> 4) + 1) >> 5) + 1)]
            # Its own regions first, then the ones only the ring reaches.
            for (rx, rz), inside in ([(region, True) for region in own]
                                     + [(region, False) for region in ring]):
                if (rx, rz) in held:
                    continue
                if posixpath.join(regions, f"r.{rx}.{rz}.mca") in files:
                    held.add((rx, rz))
                    continue
                if kind == "settle":
                    how = ((lambda at: f"finish() settles chunk "
                            f"{(at[0] >> 4, at[2] >> 4)} in it") if inside
                           else (lambda at: f"finish() settles the chunks "
                                 f"around chunk {(at[0] >> 4, at[2] >> 4)}, "
                                 f"which reach into it"))
                else:
                    how = ((lambda at: f"the write at {at} lands in it")
                           if inside else
                           (lambda at: f"finish() settles the chunks around "
                            f"the write at {at}, which reach into it"))
                uncovered(regions, rx, rz, cell, how)
            if single and len(faults) == before and all(
                    region in held for region in ring):
                sound.add(chunk)
        if kind == "blocks":
            zones = [(name, tuple(zone["box"])) for name, zone in
                     (self.site._manifest.get("zones") or {}).items()]
            met: dict[str, tuple[int, int, int]] = {}
            for x0, y0, z0, x1, y1, z1 in boxes:
                if len(met) == len(zones):
                    break
                for name, (zx0, zy0, zz0, zx1, zy1, zz1) in zones:
                    if (name not in met and x0 <= zx1 and zx0 <= x1
                            and y0 <= zy1 and zy0 <= y1
                            and z0 <= zz1 and zz0 <= z1):
                        met[name] = (max(x0, zx0), max(y0, zy0), max(z0, zz0))
            faults.extend(f"{cell} is inside protected zone {name!r}"
                          for name, cell in met.items())
        return faults

    def zones_meeting(self, corner1, corner2) -> list:
        """``(name, (corner1, corner2))`` for each protected zone the box
        between two ``(x, y, z)`` corners meets, with the part of the zone
        inside the box, in the manifest's order."""
        self.live()
        (ax, ay, az), (bx, by, bz) = corner1, corner2
        x0, x1 = sorted((ax, bx))
        y0, y1 = sorted((ay, by))
        z0, z1 = sorted((az, bz))
        found = []
        for name, zone in (self.site._manifest.get("zones") or {}).items():
            zx0, zy0, zz0, zx1, zy1, zz1 = zone["box"]
            low = (max(x0, zx0), max(y0, zy0), max(z0, zz0))
            high = (min(x1, zx1), min(y1, zy1), min(z1, zz1))
            if all(a <= b for a, b in zip(low, high)):
                found.append((name, (low, high)))
        return found

    def __repr__(self) -> str:
        state = "ended" if self.site._save is not self.save else "live"
        return f"<SiteGuard {self.site.name!r} {state}, {self.dimension}>"


class Worksite:
    """A build's snapshot, guardrails and pictures, under one manifest.

    .. code-block:: python

        from heaton_voxel.craft import Worksite

        site = Worksite.open(world, (x0, z0), (x1, z1), name="harbor")
        site.protect((px0, 60, pz0), (px1, 80, pz1), name="old-road")
        site.render("before")

        b = site.builder(seed=7)          # refuses a write restore() can't undo
        ...build...
        b.finish()

        if site.check():                  # violated zones, then uncovered files
            site.restore()                # the build never happened
        else:
            print(site.diff((x0, 60, z0), (x1, 90, z1)))   # what it changed
            site.save.save()
            site.render("after")
            site.accept()

    Open with :meth:`open`, never the constructor.
    """

    def __init__(self):
        self.world: str = ""
        self.directory: str = ""
        self.name: str = ""
        self.dimension: str = ""
        self.box: tuple[int, int, int, int] = (0, 0, 0, 0)
        self.margin: int = DEFAULT_MARGIN
        self._manifest: dict = {}
        self._save: MinecraftSave | None = None
        # Snapshot copies :meth:`diff` has found to be the bytes recorded for
        # them, so each is hashed once a site rather than once a diff.
        self._sound: set[str] = set()

    # -- opening -----------------------------------------------------------

    @classmethod
    def open(cls, world, corner1, corner2, *, name: str,
             dimension: str | None = None, directory: str | None = None,
             margin: int = DEFAULT_MARGIN,
             resnapshot: bool = False) -> "Worksite":
        """Open a worksite over the box between two ground corners.

        First open takes the snapshot; later opens resume against it,
        refusing by file name if the world has changed since (a stale
        snapshot restored over new play is the disaster this class
        exists to prevent). ``resnapshot=True`` is the deliberate way to
        rebase: a fresh snapshot, protected zones kept but re-digested
        against the world as it now stands.

        Args:
            world: The save directory.
            corner1, corner2: Opposite ground corners, ``(x, z)`` or
                ``(x, y, z)`` — y is ignored; snapshots cover whole
                region files.
            name: The worksite's name; also its directory name.
            dimension: Defaults to the player's, then the overworld.
            directory: Where the snapshot lives. Defaults to
                ``<world>/worksites/<name>`` — inside the save, so it
                travels with the world and the game ignores it.
            margin: Blocks of slack around the box, for the aftercare
                passes that reach into neighboring chunks.
            resnapshot: Replace a stale snapshot instead of refusing.
        """
        site = cls()
        site.world = os.path.abspath(os.path.expanduser(os.fspath(world)))
        if world_is_open(site.world):
            raise NBTError(
                f"the world at {site.world!r} is open in Minecraft; a "
                "snapshot taken while the game flushes chunks is a corrupt "
                "rollback. Close the world first."
            )
        site.name = name
        site.directory = (os.path.abspath(directory) if directory is not None
                          else os.path.join(site.world, "worksites", name))
        site.box = _ground_box(corner1, corner2)
        site.margin = int(margin)
        site.dimension = (dimension if dimension is not None
                          else site.save.dimension().name)

        manifest_path = os.path.join(site.directory, _MANIFEST)
        if os.path.exists(manifest_path):
            with open(manifest_path, encoding="utf-8") as handle:
                site._manifest = json.load(handle)
            recorded_box = tuple(site._manifest["box"])
            if recorded_box != site.box:
                raise NBTError(
                    f"worksite {name!r} covers {recorded_box}, not "
                    f"{site.box}: resuming with different corners would "
                    "snapshot one box and restore another. Use the "
                    "original corners, or a new name."
                )
            drifted = site._drifted_files()
            if drifted and not resnapshot:
                listed = ", ".join(drifted[:4])
                more = f" (+{len(drifted) - 4} more)" if len(drifted) > 4 else ""
                raise NBTError(
                    f"worksite {name!r}: the world has changed since the "
                    f"snapshot — {listed}{more}. Building on a stale "
                    "baseline risks restoring over real play; pass "
                    "resnapshot=True to rebase deliberately."
                )
            if drifted:
                zones = site._manifest.get("zones", {})
                site._take_snapshot()
                for zone_name, zone in zones.items():
                    site.protect(zone["box"][:3], zone["box"][3:],
                                 name=zone_name)
            return site

        site._take_snapshot()
        return site

    @property
    def save(self) -> MinecraftSave:
        """The open save. Reloaded lazily after a :meth:`restore`."""
        if self._save is None:
            self._save = MinecraftSave.load(self.world)
        return self._save

    # -- the snapshot ------------------------------------------------------

    def _covered(self) -> tuple[list[str], tuple[int, int, int, int]]:
        """The world-relative region, entity and POI directories the box
        can touch, and the region range ``(r0x, r0z, r1x, r1z)`` over the
        box plus margin -- from the save's own dimension, so its layout is
        whatever the save's is."""
        dim = self.save.dimension(self.dimension)
        directories = [_rel(path, self.world) for path in
                       (dim.region_dir, dim.entities_dir, dim.poi_dir)]
        x0, z0, x1, z1 = self.box
        span = ((x0 - self.margin) >> 9, (z0 - self.margin) >> 9,
                (x1 + self.margin) >> 9, (z1 + self.margin) >> 9)
        return directories, span

    def _sidecars(self, directories, span) -> list[str]:
        """The ``.mcc`` files present now for chunks in the covered regions.

        A chunk too big for its region keeps its payload in one of these,
        and the region's header points at it. Put the region back without
        it and the chunk points at nothing; leave a sidecar the build made
        and it outlives the chunk that needed it.
        """
        r0x, r0z, r1x, r1z = span
        found = []
        for directory in directories:
            full = os.path.join(self.world, directory)
            if not os.path.isdir(full):
                continue
            for name in sorted(os.listdir(full)):
                match = _SIDECAR.match(name)
                if (match and r0x <= int(match[1]) >> 5 <= r1x
                        and r0z <= int(match[2]) >> 5 <= r1z):
                    found.append(posixpath.join(directory, name))
        return found

    def _covered_files(self) -> list[str]:
        """World-relative paths of every file the box can touch: region,
        entity and POI files over the box plus margin, present or not —
        an absent file is recorded so a build that creates it can be
        undone by deleting it — and every sidecar those regions have."""
        directories, (r0x, r0z, r1x, r1z) = self._covered()
        paths = []
        for directory in directories:
            for rx in range(r0x, r1x + 1):
                for rz in range(r0z, r1z + 1):
                    paths.append(posixpath.join(directory, f"r.{rx}.{rz}.mca"))
        return paths + self._sidecars(directories, (r0x, r0z, r1x, r1z))

    def _take_snapshot(self) -> None:
        directories, span = self._covered()
        files: dict[str, str | None] = {}
        for rel in self._covered_files():
            source = os.path.join(self.world, rel)
            if not os.path.exists(source):
                files[rel] = None
                continue
            target = os.path.join(self.directory, _FILES, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(source, target)
            files[rel] = _sha256(target)

        self._manifest = {
            "worksite": self.name,
            "world": self.world,
            "dimension": self.dimension,
            "box": list(self.box),
            "margin": self.margin,
            "created": datetime.now(timezone.utc).isoformat(),
            "accepted": None,
            "files": files,
            # What the snapshot covered, so a restore -- even one from
            # rollback(), with no save loaded -- can find the sidecars a
            # build added and take them away again.
            "covered": {"directories": directories, "regions": list(span)},
            "zones": {},
        }
        self._write_manifest()

    def _write_manifest(self) -> None:
        os.makedirs(self.directory, exist_ok=True)
        payload = json.dumps(self._manifest, indent=1, sort_keys=True)
        atomic_write(os.path.join(self.directory, _MANIFEST),
                     payload.encode("utf-8"))

    def _drifted_files(self) -> list[str]:
        """Snapshotted files whose bytes in the world differ now."""
        drifted = []
        for rel, recorded in sorted(self._manifest["files"].items()):
            current = os.path.join(self.world, rel)
            if recorded is None:
                if os.path.exists(current):
                    drifted.append(rel)
            elif not os.path.exists(current) or _sha256(current) != recorded:
                drifted.append(rel)
        return drifted

    def _rotten_snapshot(self) -> list[str]:
        """Snapshot copies that are no longer the bytes recorded for them.

        The manifest's digest is the digest of the *copy*, so this asks
        whether the rollback is still the rollback. Checked before a
        single byte moves rather than per file as the loop reaches it:
        half a restore made of half a corrupt snapshot is worse than
        either the build or the corruption on its own.
        """
        rotten = []
        for rel, recorded in sorted(self._manifest["files"].items()):
            fault = self._rotten_copy(rel, recorded)
            if fault is not None:
                rotten.append(fault)
        return rotten

    def _rotten_copy(self, rel: str, recorded: str | None) -> str | None:
        """Why the snapshot's copy of ``rel`` is not the bytes recorded for
        it, or None when it is (or when ``rel`` was recorded absent, and
        there is no copy to be)."""
        if recorded is None:
            return None
        copy = os.path.join(self.directory, _FILES, rel)
        if not os.path.exists(copy):
            return f"{rel} (missing from the snapshot)"
        if _sha256(copy) != recorded:
            return f"{rel} (its bytes changed since it was taken)"
        return None

    def _rotten_refusal(self, rotten: list[str], consequence: str) -> NBTError:
        """The refusal a rotten snapshot gets, whatever was about to read it:
        the files, then ``consequence``, what reading them would have done."""
        listed = ", ".join(rotten[:4])
        more = f" (+{len(rotten) - 4} more)" if len(rotten) > 4 else ""
        return NBTError(
            f"worksite {self.name!r}: the snapshot is not what it was when it "
            f"was taken — {listed}{more}. {consequence}")

    # -- the guardrails ----------------------------------------------------

    def _zone_digest(self, box: tuple[int, int, int, int, int, int]) -> str:
        x0, y0, z0, x1, y1, z1 = box
        digest = hashlib.sha256()
        for x in range(x0, x1 + 1):
            for z in range(z0, z1 + 1):
                for y in range(y0, y1 + 1):
                    state = self.save.get_block(
                        x, y, z, dimension=self.dimension)
                    digest.update(
                        f"{x} {y} {z} {state}\n".encode("utf-8"))
        return digest.hexdigest()

    def protect(self, corner1, corner2, *, name: str) -> int:
        """Declare a box that must survive the build untouched.

        Digests every block state inside it now; :meth:`check` digests
        again and names the zones that changed. This is content, not
        files: an edit beside the zone that rewrites the same region file
        passes, one flipped block inside it fails. Returns the volume. A
        corner is floored to the block it is in: ``-330.5`` is ``-331``.
        """
        x0, y0, z0 = _block_of(corner1)
        x1, y1, z1 = _block_of(corner2)
        box = (min(x0, x1), min(y0, y1), min(z0, z1),
               max(x0, x1), max(y0, y1), max(z0, z1))
        volume = ((box[3] - box[0] + 1) * (box[4] - box[1] + 1)
                  * (box[5] - box[2] + 1))
        if volume > MAX_ZONE_BLOCKS:
            raise NBTError(
                f"protected zone {name!r} is {volume:,} blocks; over "
                f"{MAX_ZONE_BLOCKS:,} the digest is too slow to be a "
                "guard anyone runs. Protect the part that matters."
            )
        self._manifest["zones"][name] = {
            "box": list(box),
            "digest": self._zone_digest(box),
        }
        self._write_manifest()
        return volume

    def check(self) -> CheckResult:
        """Everything a save would make that a restore could not take back,
        as a :class:`CheckResult`: the protected zones whose content has
        changed (``.zones``, by name), then the files ``save()`` would write
        that the snapshot does not hold (``.uncovered``, world-relative).
        Empty -- equal to ``[]`` -- means every guardrail held.

        In-memory edits count: run it before :meth:`MinecraftSave.save` to
        catch damage while the disk is still clean. ``uncovered`` is found
        the way ``save()`` finds what to write -- every loaded region,
        entity and POI file of every dimension whose bytes would differ,
        and ``level.dat``, the player file and the split settings files
        likewise -- so it names a write anywhere in the world outside the
        files the snapshot holds, through :attr:`save` directly as well as
        through a Builder: a block set three regions away, a cow spawned in
        a region the snapshot does not hold, the weather. (A write outside
        the box but inside a held region is restorable, and not named.)
        Reading is not writing, so a file loaded and left alone is not
        named. It sees only this site's own save, and only what is unsaved:
        once ``save()`` has run there is nothing left in memory to
        compare.
        """
        violated = []
        for name, zone in sorted(self._manifest.get("zones", {}).items()):
            if self._zone_digest(tuple(zone["box"])) != zone["digest"]:
                violated.append(name)
        return CheckResult(violated, self._uncovered())

    def _uncovered(self) -> list[str]:
        """The world-relative files ``save()`` would write now that the
        manifest does not hold, sorted -- found as ``MinecraftSave.save``
        picks its candidates, so a file kind it learns to write is a file
        kind this sees (``tests/test_worksite.py`` holds the two together)."""
        save = self._save
        if save is None:            # nothing loaded, so nothing modified
            return []
        files = self._manifest.get(_FILES, {})
        found: set[str] = set()

        def note(path):
            if path is None:
                return
            rel = _rel(path, self.world)
            if rel not in files:
                found.add(rel)

        for dim in save.dimensions.values():
            for region in dim.loaded_regions():
                if region.is_modified():
                    note(region.path)
            for region in list(dim.entity_regions.values()):
                if not region.from_disk and not region.chunks:
                    continue        # save() skips an empty file never written
                if region.is_modified():
                    note(region.path)
            for region in list(dim.poi_regions.values()):
                if region.is_modified():
                    note(region.path)
        if (save._player is not None and save._player_path is not None
                and save._player.is_modified()):
            note(save._player_path)
        if save.level is not None and save.level.is_modified():
            note(os.path.join(save.path, "level.dat"))
        for handle, path in save._settings_files_to_save():
            if handle.is_modified():
                note(path)
        return sorted(found)

    # -- building inside it --------------------------------------------------

    def builder(self, *, seed: int = 0, allow=(), palette=None):
        """A :class:`~heaton_voxel.craft.build.Builder` over this site's
        :attr:`save` and dimension that cannot write where :meth:`restore`
        could not follow it.

        Every write it makes -- a primitive's cells, a writer's whole plan,
        a placement's re-derived neighbors, a spawn -- is admitted before
        the first block moves, and refused whole (a
        :class:`~heaton_voxel.craft.errors.Refused` naming every fault) when
        it would land in a region file the snapshot does not hold, or reach
        one through the eight chunks around it that ``finish()`` relights
        and settles, or touch a protected zone, even with the state already
        there. ``finish()`` refuses too, before heightmaps and light, when
        settling the build's connections would change a zone, and puts the
        zone back as it found it.

        ``seed``, ``allow`` and ``palette`` are the Builder's own: ``palette``
        is the :class:`~heaton_voxel.craft.style.Palette` its legacy writers
        read a material left out from, and ``None`` their old defaults.
        :meth:`restore` ends every builder this hands out: its save is the
        world the restore undid, so from then on everything it would do
        there -- a write, a finish, a read of the ground, its ``save``
        itself -- raises a plain ``NBTError``, never a ``Refused``, whatever
        else would have refused the call, and a new one comes from calling
        this again.
        """
        from .build import Builder

        save = self.save
        return Builder(save, dimension=self.dimension, seed=seed, allow=allow,
                       guard=SiteGuard(self, save), palette=palette)

    def diff(self, corner1, corner2, *, save: MinecraftSave | None = None
             ) -> SiteDiff:
        """What changed in the box between two ``(x, y, z)`` corners, block
        by block: a :class:`SiteDiff` of every cell whose state differs from
        the snapshot's, ``before`` read from the snapshot's own copies of
        the region files and ``after`` from ``save``.

        ``save`` defaults to :attr:`save`, unsaved edits included; any save
        of the same world will do, such as one freshly loaded after
        ``save()`` to see what reached the disk -- "before" is still the
        snapshot's. This is the content truth the Builder's journal is not:
        a cell written with the state it held is not in it -- the same game
        state, a property one spelling leaves out read as its default, in
        either codec -- a neighbor
        ``place_block`` re-derived is, and so is anything written straight
        through the save. After :meth:`restore` it is empty. Block states
        only: light, heightmaps, biomes and entities are not compared.

        Refuses (``NBTError``), rather than answer what it cannot know: a
        corner without a ``y``; a box reaching a region file the snapshot
        does not hold, since there is no "before" to read there; a
        snapshot whose copies under the box no longer match the manifest,
        as :meth:`restore` refuses one; a ``save`` of another world; and a
        chunk that differs from the snapshot's and cannot be read on either
        side -- "could not read" never reads as "unchanged". A chunk whose
        bytes are the very bytes the snapshot holds is unchanged whether or
        not anything could decode it (an old chunk nobody converted, say),
        since the same bytes are the same states. The manifest is read
        afresh and refused by name if it cannot be. The snapshot itself is
        only ever read.

        A corner is floored to the block it is in, as :meth:`protect` reads
        one: ``(-330.5, 62, 6)`` is block ``(-331, 62, 6)``, not ``-330``.
        """
        for corner in (corner1, corner2):
            if len(corner) != 3:
                raise NBTError(
                    f"a diff compares blocks, so each corner is (x, y, z); "
                    f"{tuple(corner)!r} has no y")
        (ax, ay, az), (bx, by, bz) = (_block_of(corner1), _block_of(corner2))
        x0, x1 = sorted((ax, bx))
        y0, y1 = sorted((ay, by))
        z0, z1 = sorted((az, bz))
        box = (x0, y0, z0, x1, y1, z1)
        if save is None:
            save = self.save
        elif os.path.realpath(save.path) != os.path.realpath(self.world):
            raise NBTError(
                f"worksite {self.name!r} is a snapshot of {self.world!r}, and "
                f"this save is {save.path!r}: a diff against another world "
                f"would report every difference between the two worlds as "
                f"the build")
        manifest = _load_manifest(self.directory, self.name)
        files = manifest[_FILES]
        dim = save.dimension(self.dimension)
        region_dir = _rel(dim.region_dir, save.path)

        # Every region file the box reads must have a "before".
        needed = []
        for rx in range(x0 >> 9, (x1 >> 9) + 1):
            for rz in range(z0 >> 9, (z1 >> 9) + 1):
                rel = posixpath.join(region_dir, f"r.{rx}.{rz}.mca")
                if rel not in files:
                    raise NBTError(
                        f"worksite {self.name!r} holds no snapshot of {rel}, "
                        f"so there is no 'before' for the box {box} there; a "
                        f"diff answers inside the box and margin it was "
                        f"opened over")
                needed.append((rel, rx, rz))
        # The copies it reads -- each region and the sidecars beside it --
        # must still be the bytes the manifest recorded.
        reading = []
        for rel, rx, rz in needed:
            reading.append(rel)
            for name in files:
                match = _SIDECAR.match(os.path.basename(name))
                if (match and os.path.dirname(name) == region_dir
                        and int(match[1]) >> 5 == rx
                        and int(match[2]) >> 5 == rz):
                    reading.append(name)
        rotten = []
        for rel in reading:
            if rel in self._sound:
                continue
            fault = self._rotten_copy(rel, files[rel])
            if fault is None:
                self._sound.add(rel)
            else:
                rotten.append(fault)
        if rotten:
            raise self._rotten_refusal(
                rotten, "A diff read from it would call whatever the copy has "
                "become the world before the build. Refusing.")

        snapshot = Dimension(self.dimension,
                             os.path.join(self.directory, _FILES, region_dir))
        snapshot.index()
        changes = []
        for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
            for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
                changes.extend(self._chunk_changes(
                    snapshot, dim, chunk_x, chunk_z, box))
        changes.sort(key=lambda change: change[0])
        return SiteDiff(changes, box)

    def _chunk_changes(self, snapshot: Dimension, dim: Dimension,
                       chunk_x: int, chunk_z: int, box) -> list:
        """``[((x, y, z), CellChange)]`` for one chunk's part of ``box``."""
        old_chunk = snapshot.chunk(chunk_x, chunk_z)
        new_chunk = dim.chunk(chunk_x, chunk_z)
        if old_chunk is None and new_chunk is None:
            return []

        def unreadable(where, exc):
            return NBTError(
                f"worksite {self.name!r} cannot compare chunk ({chunk_x}, "
                f"{chunk_z}): it cannot be read in {where} ({exc}), and what "
                f"cannot be read is not unchanged")

        if old_chunk is not None and new_chunk is not None:
            # The very bytes the snapshot holds are the same states, whether
            # or not anything could read them -- the one answer given
            # without decoding. An undecoded chunk hands its bytes back as
            # they are; a decoded one is encoded, as save() would encode it.
            try:
                same = new_chunk.payload() == old_chunk.payload()
            except NBTError as exc:
                raise unreadable("the save or the snapshot", exc) from exc
            if same:
                return []
        sides = []
        for chunk, where, side in ((old_chunk, "the snapshot", snapshot),
                                   (new_chunk, "the save", dim)):
            if chunk is None:
                sides.append(None)
                continue
            try:
                sides.append(side.blocks(chunk_x, chunk_z))
            except NBTError as exc:
                raise unreadable(where, exc) from exc
        old, new = sides
        x0, y0, z0, x1, y1, z1 = box
        cx0, cx1 = max(x0, chunk_x * 16), min(x1, chunk_x * 16 + 15)
        cz0, cz1 = max(z0, chunk_z * 16), min(z1, chunk_z * 16 + 15)
        present = [side for side in sides if side is not None]
        lo = max(y0, min(side.min_y for side in present))
        hi = min(y1, max(side.max_y for side in present))
        found = []
        for section_y in range(lo >> 4, (hi >> 4) + 1):
            low, high = max(lo, section_y * 16), min(hi, section_y * 16 + 15)
            old_view = _section_view(old, section_y, low, high)
            new_view = _section_view(new, section_y, low, high)
            if old_view is not None and old_view == new_view:
                continue        # one palette, one array, one range of y
            for y in range(low, high + 1):
                before_row = _row(old_view, y)
                after_row = _row(new_view, y)
                base = (y & 15) << 8
                for z in range(cz0, cz1 + 1):
                    at = base | ((z & 15) << 4)
                    for x in range(cx0, cx1 + 1):
                        index = at | (x & 15)
                        before = _cell(before_row, index)
                        after = _cell(after_row, index)
                        if before != after and not _same_state(before, after):
                            found.append(((x, y, z), CellChange(before, after)))
        return found

    # -- the eyes ----------------------------------------------------------

    def render(self, label: str, *, scale: int = 2) -> str:
        """A top-down PNG of the worksite into its own directory,
        margin included. ``render("before")`` right after open and
        ``render("after")`` before accept is the habit worth keeping."""
        from .render import render_area

        x0, z0, x1, z1 = self.box
        path = os.path.join(self.directory, f"{label}.png")
        render_area(
            self.save,
            (x0 - self.margin, 0, z0 - self.margin),
            (x1 + self.margin, 0, z1 + self.margin),
            path, dimension=self.dimension, scale=scale)
        return path

    # -- the ending --------------------------------------------------------

    def restore(self, *, dry_run: bool = False) -> RollbackResult:
        """Put the snapshot's exact bytes back: the build never happened.

        Files absent at snapshot time are deleted — a build that created
        an entities region is undone with it. Refuses while Minecraft has
        the world open, and refuses a snapshot whose own bytes no longer
        match the manifest, because restoring those would write
        corruption over the world. Any :class:`MinecraftSave` loaded
        before the restore is stale; this worksite's own :attr:`save`
        reloads itself, others must be reopened. So is every Builder
        :meth:`builder` handed out: they are ended, and the next write,
        finish or read of any of them raises ``NBTError`` rather than build
        into the undone world.

        ``dry_run=True`` runs every check and names every file it would
        move without moving one, which is how you look before you leap
        from :meth:`rollback`; it ends nothing.
        """
        if world_is_open(self.world):
            raise NBTError(
                f"the world at {self.world!r} is open in Minecraft; "
                "restoring under the game would be overwritten or corrupt "
                "the region. Close the world first."
            )
        rotten = self._rotten_snapshot()
        if rotten:
            raise self._rotten_refusal(
                rotten, "Restoring it would write those bytes over the world; "
                "whatever is there now is at least a world. Refusing.")

        result = RollbackResult(self._manifest.get("created"),
                                self._manifest.get("accepted"),
                                dry_run=dry_run)
        for rel, recorded in sorted(self._manifest["files"].items()):
            target = os.path.join(self.world, rel)
            if recorded is None:
                if not os.path.exists(target):
                    result.already += 1      # absent, as it was recorded
                    continue
                if not dry_run:
                    os.remove(target)
                result.files.append(rel)
                result.removed.append(rel)
                continue
            if os.path.exists(target) and _sha256(target) == recorded:
                result.already += 1
                continue
            if not dry_run:
                snapshot = os.path.join(self.directory, _FILES, rel)
                with open(snapshot, "rb") as handle:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    atomic_write(target, handle.read())
            result.files.append(rel)
        # Sidecars the build created: absent from a snapshot that knew to
        # look for them. One taken before snapshots recorded sidecars never
        # listed the ones already there, so it cannot tell new from old and
        # leaves them all alone.
        covered = self._manifest.get("covered")
        if covered is not None:
            recorded = self._manifest["files"]
            for rel in self._sidecars(covered["directories"],
                                      tuple(covered["regions"])):
                if rel not in recorded:
                    if not dry_run:
                        os.remove(os.path.join(self.world, rel))
                    result.files.append(rel)
                    result.removed.append(rel)
        if not dry_run:
            # All it held describes the undone world -- and every builder
            # handed out holds it too, which ends their guards (SiteGuard.live).
            self._save = None
        return result

    @classmethod
    def rollback(cls, world, name: str, *, directory: str | None = None,
                 dry_run: bool = False) -> RollbackResult:
        """Undo a worksite's build from outside the session that made it.

        :meth:`open` cannot do this and should not learn how. Once a build
        is saved its own bytes are drift, so resuming refuses; and
        ``resnapshot=True``, the documented way past that refusal, rebases
        the baseline onto the build you were trying to undo. This reads
        the manifest, checks the snapshot against it, and puts those bytes
        back without :meth:`open` in the path at all.

        It is deliberately terminal: it hands back a
        :class:`RollbackResult`, never a worksite, so nothing can go on to
        build against a baseline it has just reverted. Open the worksite
        again afterwards if you want to rebuild — the world now matches
        the snapshot, so a resume comes back clean.

        **This overwrites whatever is in the world now.** From the digests
        alone there is nothing to distinguish the build you meant to undo
        from a week of somebody's play on top of it, and that judgment is
        yours, not this method's. ``dry_run=True`` names every file it
        would move first, and the result carries the snapshot's own dates
        so you can see how old a baseline you are about to reinstate.
        Refuses while Minecraft has the world open.
        """
        site = cls()
        site.world = os.path.abspath(os.path.expanduser(os.fspath(world)))
        site.name = name
        site.directory = (os.path.abspath(directory) if directory is not None
                          else os.path.join(site.world, "worksites", name))
        site._manifest = _load_manifest(site.directory, name)
        site.box = tuple(site._manifest.get("box", (0, 0, 0, 0)))
        site.margin = int(site._manifest.get("margin", DEFAULT_MARGIN))
        site.dimension = site._manifest.get("dimension", "")
        return site.restore(dry_run=dry_run)

    @classmethod
    def retire(cls, world, name: str, *, dry_run: bool = True) -> RetireResult:
        """Remove an accepted worksite's snapshot, once its build is settled.

        It removes that worksite's own directory and nothing else: no file
        of the world is touched, so what the build made stays built, and all
        that goes is the way to undo it. Every snapshot is a whole copy of
        its files, so no other worksite leans on this one, though one taken
        earlier over the same files still goes back past this build, taking
        everything since with it.

        That cannot be undone, so ``dry_run`` is on unless turned off, and
        it makes every check and removes nothing. The result says what goes.

        Refused (an ``NBTError``), with nothing removed: a name that is not
        one directory's under ``<world>/worksites/``; a path with no
        ``level.dat``; a world open in Minecraft; a worksite that is not
        there; a link, whose target is no snapshot of this save's; a
        directory with no manifest, since a backup somebody made by hand is
        not this method's to judge; a manifest that does not read, since
        nothing then says what would be lost; and a worksite never accepted,
        since nothing then says its build is settled. A session that saved
        without :meth:`accept` leaves one of those for good, because
        :meth:`open` refuses its build as drift: whether that snapshot is
        still wanted is a person's call, and its directory theirs to remove.

        The directory is renamed before it is deleted, so a delete cut short
        leaves ``.retiring-<name>`` and never a part of a snapshot under the
        name that restores from it. That name is no worksite any more, and
        retiring it finishes the job.
        """
        if (not isinstance(name, str) or name in ("", ".", "..")
                or any(mark in name for mark in ("/", "\\", "\0"))):
            raise NBTError(f"{name!r} is not a worksite's name: it is one "
                           f"directory under worksites/, as list shows it")
        root = os.path.abspath(os.path.expanduser(os.fspath(world)))
        if not _present(os.path.join(root, "level.dat"), "a save's level.dat"):
            raise NBTError(f"{root!r} does not look like a Minecraft save: "
                           f"no level.dat")
        if world_is_open(root):
            raise NBTError(f"the world at {root!r} is open in Minecraft. "
                           f"Close the world first.")
        base = os.path.join(root, "worksites")
        directory = os.path.join(base, name)
        if os.path.islink(directory):
            raise NBTError(f"{directory!r} is a link, not a snapshot: remove "
                           f"it by hand if it is yours to remove")
        if not _present(directory, "a worksite directory"):
            raise NBTError(f"no worksite {name!r} under {base!r}")
        summary = _summarize(directory, name)
        unfinished = name.startswith(_RETIRING)
        if not unfinished:
            if not summary.has_manifest:
                raise NBTError(
                    f"{directory!r} has no {_MANIFEST}, so it is no worksite: "
                    f"a backup somebody made by hand is theirs to remove")
            if summary.problem is not None:
                raise NBTError(f"{name!r} cannot be retired, because nothing "
                               f"can say what would be lost: {summary.problem}")
            if not summary.accepted:
                raise NBTError(
                    f"{name!r} was never accepted, so nothing says its build "
                    f"is settled and this snapshot may be the only way to undo "
                    f"it: accept it in the session that built it, or judge it "
                    f"yourself and remove {directory!r} by hand")
        result = RetireResult(summary, dry_run=dry_run)
        if dry_run:
            return result
        retiring = directory if unfinished else os.path.join(base,
                                                             _RETIRING + name)
        try:
            if not unfinished:
                if os.path.isdir(retiring):     # an earlier one of this name
                    shutil.rmtree(retiring)
                os.replace(directory, retiring)
        except OSError as exc:
            raise NBTError(f"{name!r} could not be removed, and stands as it "
                           f"was: {exc}") from exc
        try:
            shutil.rmtree(retiring)
        except OSError as exc:
            raise NBTError(
                f"{name!r} was only partly removed: {exc}. What is left is "
                f"{retiring!r}, which is no worksite any more; retire that "
                f"name to finish") from exc
        return result

    def accept(self) -> None:
        """Stamp the build accepted. The snapshot stays -- disk is cheap and
        a decade-old world deserves its paper trail -- until :meth:`retire`
        is asked to remove it."""
        self._manifest["accepted"] = datetime.now(timezone.utc).isoformat()
        self._write_manifest()

    def __repr__(self) -> str:
        x0, z0, x1, z1 = self.box
        state = "accepted" if self._manifest.get("accepted") else "open"
        return (f"<Worksite {self.name!r} {state}, ({x0}, {z0})..({x1}, {z1})"
                f" in {self.dimension}, {len(self._manifest.get('zones', {}))}"
                f" protected zones>")


# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------


class WorksiteSummary:
    """One directory under ``<world>/worksites/``, read from its manifest
    alone -- no save is loaded and no world file is hashed, so listing is
    cheap and safe while the game has the world open.

    ``name`` is the directory's name, which is what
    :meth:`Worksite.rollback` takes. ``problem`` is None for a manifest that
    reads, and otherwise says why it does not: ``has_manifest`` tells a
    directory that never was a worksite (a hand-made backup) from one whose
    record is damaged. It is False only where there is certainly no
    manifest; a directory the process may not look into is a damaged
    record, never a backup. ``world`` is the path the manifest recorded, which
    differs from the save it sits in when the save was copied. ``copies``
    and ``absent`` count the files snapshotted and the files recorded as
    not existing; ``paths`` is every world-relative file the snapshot
    covers. ``sidecars_tracked`` is False for a snapshot taken before
    ``.mcc`` files were recorded, which a restore therefore leaves alone.
    ``note`` is whatever a person left in the manifest to explain it.
    ``size`` is the bytes the directory holds, by ``stat`` alone, for a
    backup as for a worksite, and None if some of it cannot be looked at. A
    ``.retiring-<name>`` directory is what a :meth:`Worksite.retire` cut short
    left, and reads as no worksite whatever of its manifest is left, so
    nothing counts a half-deleted snapshot as a way back.
    """

    __slots__ = ("name", "directory", "has_manifest", "problem", "world",
                 "dimension", "box", "margin", "created", "accepted",
                 "copies", "absent", "paths", "zones", "sidecars_tracked",
                 "note", "size")

    def __init__(self, name: str, directory: str):
        self.name = name
        self.directory = directory
        self.has_manifest = False
        self.problem: str | None = None
        self.world: str | None = None
        self.dimension: str | None = None
        self.box: tuple[int, int, int, int] | None = None
        self.margin: int | None = None
        self.created: str | None = None
        self.accepted: str | None = None
        self.copies = 0
        self.absent = 0
        self.paths: tuple[str, ...] = ()
        self.zones: tuple[str, ...] = ()
        self.sidecars_tracked = False
        self.note: str | None = None
        self.size: int | None = None

    def __repr__(self) -> str:
        if self.problem is not None:
            return f"<WorksiteSummary {self.name!r}: {self.problem}>"
        state = "accepted" if self.accepted else "not accepted"
        where = ""
        if self.box is not None:
            x0, z0, x1, z1 = self.box
            where = f" ({x0}, {z0})..({x1}, {z1})"
        return (f"<WorksiteSummary {self.name!r} {state}, {self.dimension}"
                f"{where}, {len(self.paths)} files>")


def _summarize(directory: str, name: str) -> WorksiteSummary:
    summary = WorksiteSummary(name, directory)
    summary.size = _disk_size(directory)
    if name.startswith(_RETIRING):
        summary.problem = "what a retire cut short left: not a worksite"
        return summary
    path = os.path.join(directory, _MANIFEST)
    try:
        summary.has_manifest = _present(path, "a worksite manifest")
    except NBTError as exc:
        # Nobody can tell whether a manifest is there, so it is a damaged
        # record, never a backup directory that never was a worksite.
        summary.has_manifest = True
        summary.problem = str(exc)
        return summary
    if not summary.has_manifest:
        summary.problem = f"no {_MANIFEST}: not a worksite"
        return summary
    try:
        manifest = _load_manifest(directory, name)
    except NBTError as exc:
        summary.problem = str(exc)
        return summary
    try:
        files = manifest[_FILES]
        box = manifest.get("box")
        if box is not None:
            box = tuple(int(value) for value in box)
            if len(box) != 4:
                raise ValueError(
                    f"its box {manifest['box']!r} is not four numbers")
        summary.box = box
        summary.margin = int(manifest.get("margin", DEFAULT_MARGIN))
        summary.zones = tuple(sorted(manifest.get("zones") or {}))
    except (TypeError, ValueError) as exc:
        summary.problem = f"{path!r} is not a readable worksite manifest: {exc}"
        return summary
    summary.world = manifest.get("world")
    summary.dimension = manifest.get("dimension")
    summary.created = manifest.get("created")
    summary.accepted = manifest.get("accepted")
    summary.copies = sum(1 for digest in files.values() if digest is not None)
    summary.absent = len(files) - summary.copies
    summary.paths = tuple(sorted(files))
    summary.sidecars_tracked = "covered" in manifest
    summary.note = manifest.get("note")
    return summary


def list_worksites(world) -> list[WorksiteSummary]:
    """Every worksite a save holds, by name, read from manifests alone,
    with each directory's size by ``stat``: no save, and no region data.

    One entry per directory under ``<world>/worksites/``, in name order. A
    directory without a manifest is listed rather than skipped -- a backup
    somebody made by hand sits there looking like a worksite, and saying
    so is the point -- and a manifest that cannot be read is listed with
    the reason, so one damaged record neither hides the others nor reads
    as no worksite at all. Plain files there are not worksites and are not
    listed. Refuses a path with no ``level.dat``: a mistyped world must not
    come back as an empty list. A ``worksites/`` directory that cannot be
    read is refused for the same reason, rather than listed as empty.
    """
    root = os.path.abspath(os.path.expanduser(os.fspath(world)))
    if not _present(os.path.join(root, "level.dat"), "a save's level.dat"):
        raise NBTError(
            f"{root!r} does not look like a Minecraft save: no level.dat"
        )
    base = os.path.join(root, "worksites")
    if not _present(base, "a save's worksites directory"):
        return []
    if not os.path.isdir(base):
        return []
    try:
        # The directory's own entry types, so an entry is told from a
        # plain file without looking inside it -- which is the summary's
        # job, and where an entry nobody may open is reported, not dropped.
        with os.scandir(base) as entries:
            names = sorted(entry.name for entry in entries if entry.is_dir())
    except OSError as exc:
        raise NBTError(
            f"{base!r} cannot be read as a save's worksites directory: {exc}"
        ) from exc
    return [_summarize(os.path.join(base, name), name) for name in names]
