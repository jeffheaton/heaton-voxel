"""Build a house, a lane, a square and a tower in one palette, the guarded way.

    python examples/craft_session.py <world> --at X Z            # a dry run
    python examples/craft_session.py <world> --at X Z --palette lab
    python examples/craft_session.py <world> --at '~' '~' --y 64 --write
    python -m heaton_voxel worksite rollback <world> craft-session

A whole building session with ``heaton_voxel.craft``, start to finish, in
the order every real one should run: a worksite first, the build in named
stages, the aftercare, the checks, and only then the save.

The lot is 32 blocks square, centered on ``--at`` (``~`` is the player's
position), and leveled to ``--y`` -- by default the median of what you
would stand on there, the water's surface over a lake or the sea, which the
pad is then filled up to from the bed. Leveling clears the lot: trees are
felled whole, and whatever stands above the pad, a hill or a building, is
cut away -- which is what the worksite is for, and why the default is a dry
run. On it:

* **a house**: a two-story ``shell`` whose walls follow a ``Facade`` -- a
  plinth, corner posts, a band at the upper floor, a window in every bay --
  under a gabled ``Roof``; its door hung by ``doorway`` (an iron one, with
  a button on each side, in the lab's palette); a ``flight`` up through the
  floor it cuts; and both stories furnished in one ``furnish`` call, which
  walks the house from its doorstep before and after;
* **a lane** from the doorstep to the square (``road``), lit by
  ``line_lamps`` to ``NO_SPAWNS``: posts beside the road, as few as the
  game's own light flood says will do;
* **a square** (``pave``), lit flush in its paving to
  ``NO_NEW_SNOW_OR_ICE`` -- no snow settles on it, on about a third of
  the lamps melting it would take on a square this small (13 against 36;
  a quarter on a large one) -- with a villager standing in it, typed by
  the biome at its feet;
* **a watch tower**, solid, decked, its parapet crenellated by
  ``battlement`` over the shell's own ``perimeter``, and its deck lit
  flush to ``NO_SPAWNS`` (``light_cells``): nobody climbs to it, and an
  unlit deck open to the sky is where monsters spawn at night.

The lot is built in one palette (``examples/palettes.py``: ``--palette
roman`` or ``lab``). The legacy writers read it wherever a material is left
out, and the newer rule objects are handed ``b.palette[role]``. What a
palette has no role for -- the lamp post, the door, the stair -- is in
``FITTINGS`` below, as each build had them, beside the scene rather than in
the palette; the furniture is the same in both.

Before the build, the land round the lot is protected (``site.protect``):
four strips two blocks out, past the one block ``finish()`` settles
connections in, from below the ground to over the tower. Then the checks,
each printed: ``finish()``'s audit finds nothing the game will not keep
that was not there before; ``site.check()`` is clean -- the land round the
lot as it was, no file written that the snapshot does not hold; the house
walks from its doorstep to the top story and back (``reachable``, which
walks home as well as out); and nothing can spawn anywhere the scene lit,
by the light that lasts (``spawnable``). Then what the build changed,
block by block against the snapshot (``site.diff``), and the journal's
cells and writes by stage.

**Nothing is written without ``--write``.** A dry run takes its worksite
snapshot in a temporary directory, builds in memory, checks, reports and
throws all of it away: the world's directory is left byte for byte as it
was. ``--write`` takes the snapshot, and a render of the lot, in
``<world>/worksites/<name>/``; then, only if every check passes, it saves
the build, renders the lot again, reads back what reached the disk,
accepts the worksite and prints what ``worksite rollback`` says undoing it
would move. A failed check exits 1 with the build unsaved and nothing
restored: nothing of the session's reached the disk, so a restore could
only put the snapshot back over whatever else wrote the world while it
ran. Once the build is saved nothing raises: a read-back that fails, or
finds what reached the disk is not what was built, exits 1 with the build
saved, not accepted, and the snapshot there to undo it, while an after
render or a rollback report that cannot be made (the game opened the
world meanwhile) is a note. A refusal before the save (the world open in
Minecraft, ground never generated, a writer's) exits 2 with the build
unsaved, though a ``--write`` refused part way leaves its snapshot, never
accepted, where ``worksite list`` shows it. Reusing a ``--name`` resumes
that worksite, which refuses once its build has been saved: undo it with
``Worksite.rollback(world, name)``, or give the next session a name of its
own.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import shutil
import sys
import tempfile
from typing import NamedTuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))   # the package, run from anywhere
sys.path.insert(0, _HERE)                    # palettes

from heaton_voxel.__main__ import main as command_line  # noqa: E402
from heaton_voxel.craft import (  # noqa: E402
    NO_NEW_SNOW_OR_ICE,
    NO_SPAWNS,
    BuildFinish,
    CheckResult,
    Facade,
    Material,
    Opening,
    Piece,
    ReachMap,
    RollbackResult,
    Roof,
    SiteDiff,
    StraightStair,
    Worksite,
    reachable,
    spawnable,
    unsupported,
)
from heaton_voxel.nbt import NBTError, world_is_open  # noqa: E402
from heaton_voxel.world import OVERWORLD, MinecraftSave  # noqa: E402

from palettes import PALETTES  # noqa: E402

#: What the scene takes from each build that a palette has no role for: the
#: lamp post (``post`` from the ground up, ``lamp`` on top), the door and
#: the button that opens it if it is iron, and the stair a flight is built
#: of with the ``wood`` under its treads.
FITTINGS = {
    # The town's lamp_post (common.py), the insulae's spruce doors and
    # spruce stair runs (jw_districts.py), in tools/roman_city/.
    "roman": dict(post=("minecraft:sandstone_wall",
                        "minecraft:sandstone_wall"),
                  lamp="minecraft:lantern",
                  door="minecraft:spruce_door",
                  button="minecraft:stone_button",   # opened by hand: unused
                  stair="minecraft:spruce_stairs",
                  wood="minecraft:spruce_planks"),
    # The campus light mast (lab_common.py), the iron doors with their
    # blackstone buttons and the deepslate flights over white concrete
    # (m2_buildings.py), in tools/secret_lab/.
    "lab": dict(post=("minecraft:polished_blackstone_wall",
                      "minecraft:polished_blackstone_wall"),
                lamp="minecraft:sea_lantern",
                door="minecraft:iron_door",
                button="minecraft:polished_blackstone_button",
                stair="minecraft:polished_deepslate_stairs",
                wood="minecraft:white_concrete"),
}

#: The lot's side, in blocks.
SIZE = 32

#: How far out from the lot the land protected round it stands.
BESIDE = 2

#: Who stands in the square.
PROFESSION = "mason"


class Scene:
    """Where everything stands, from the lot's center ``(x, z)`` and its
    ground level ``y``: a lot ``SIZE`` blocks square, and everything else
    counted from its northwest corner. Arithmetic, with no world in it."""

    def __init__(self, x: int, z: int, y: int):
        self.lot = Scene.lot_of(x, z)
        x0, z0 = self.lot[0], self.lot[1]
        self.y = y
        # The house: two stories of four rows, the upper floor at y + 5 and
        # the walls' top at y + 9, its door in the south wall.
        self.house = (x0 + 2, z0 + 2, x0 + 12, z0 + 10)
        self.upper = y + 5
        self.top = y + 9
        self.door = x0 + 7                                 # along the wall
        self.doorstep = (x0 + 7, y, z0 + 11)               # stood on, outside
        self.room = (x0 + 3, z0 + 3, x0 + 11, z0 + 9)      # inside the walls
        # The flight's first tread, against the west wall, climbing north.
        self.flight = (x0 + 3, y + 1, z0 + 8)
        # The lane from the doorstep to the square, and the square.
        self.lane = [(x0 + 7, z0 + 12), (x0 + 7, z0 + 21), (x0 + 12, z0 + 21)]
        self.lane_width = 3
        self.square = (x0 + 14, z0 + 14, x0 + 29, z0 + 29)
        self.villager = (x0 + 22, z0 + 22)
        # The tower, north of the square's east end: its walls' top, and
        # the deck over them, a parapet round the deck and merlons on that.
        self.tower = (x0 + 25, z0 + 7, x0 + 29, z0 + 11)
        self.tower_top = y + 12
        self.deck = self.tower_top + 1

    @staticmethod
    def lot_of(x: int, z: int) -> tuple[int, int, int, int]:
        """The lot centered on ``(x, z)``, ``(x0, z0, x1, z1)`` inclusive."""
        x0, z0 = x - SIZE // 2, z - SIZE // 2
        return (x0, z0, x0 + SIZE - 1, z0 + SIZE - 1)

    def beside(self) -> dict[str, tuple[tuple[int, int, int],
                                        tuple[int, int, int]]]:
        """The land round the lot, which the build must leave as it is, by
        side: a ring of four strips ``BESIDE`` blocks out -- past the one
        block ``finish()`` settles connections in -- from four below the
        ground to four over the tower's merlons."""
        x0, z0, x1, z1 = self.lot
        x0, z0, x1, z1 = x0 - BESIDE, z0 - BESIDE, x1 + BESIDE, z1 + BESIDE
        low, high = self.y - 4, self.deck + 6
        return {"north": ((x0, low, z0), (x1, high, z0)),
                "south": ((x0, low, z1), (x1, high, z1)),
                "west": ((x0, low, z0 + 1), (x0, high, z1 - 1)),
                "east": ((x1, low, z0 + 1), (x1, high, z1 - 1))}

    def lit(self) -> list[tuple[tuple[int, int, int], tuple[int, int, int]]]:
        """Every box of cells the scene lights, as the feet cells over its
        floors: both stories of the house, the lane's swath -- each of its
        legs runs along x or z, so a leg's swath is the leg widened by half
        the lane on every side -- the square, and the tower's top, from the
        deck inside the parapet to the merlons' tops."""
        half = self.lane_width // 2
        rx0, rz0, rx1, rz1 = self.room
        boxes = [((rx0, floor + 1, rz0), (rx1, floor + 1, rz1))
                 for floor in (self.y, self.upper)]
        for (ax, az), (bx, bz) in zip(self.lane, self.lane[1:]):
            low = (min(ax, bx) - half, self.y + 1, min(az, bz) - half)
            high = (max(ax, bx) + half, self.y + 1, max(az, bz) + half)
            boxes.append((low, high))
        sx0, sz0, sx1, sz1 = self.square
        boxes.append(((sx0, self.y + 1, sz0), (sx1, self.y + 1, sz1)))
        tx0, tz0, tx1, tz1 = self.tower
        boxes.append(((tx0, self.deck + 1, tz0), (tx1, self.deck + 3, tz1)))
        return boxes


class Session(NamedTuple):
    """What one session built and found."""

    palette: str
    scene: Scene
    #: What each writer reported, by name (:func:`build`'s).
    built: dict
    finish: BuildFinish
    #: Faults ``finish()``'s audit found that did not stand before the build.
    new_faults: list
    check: CheckResult
    #: The house walked from its doorstep, out and home again.
    walk: ReachMap
    #: Cells the scene lit that a monster could still spawn in.
    spawns: list
    #: What the build changed, block by block, against the snapshot.
    diff: SiteDiff
    #: Every cell the Builder wrote, from its journal.
    written: frozenset
    #: The journal's writes by stage, a cell written twice counted twice.
    tally: dict
    #: The rollback's dry run: what undoing this session would move. None
    #: when it was refused, which is noted rather than raised.
    rollback: RollbackResult | None
    saved: bool
    #: The checks that failed; empty when the session passed every one.
    problems: list


def ground_level(b, lot) -> int:
    """The median height over ``lot`` of what you would stand on: the
    ground, or the water's surface where there is water. A column with no
    ground -- never generated, or not finished -- is the Builder's
    ``NoSurface``, naming it."""
    x0, z0, x1, z1 = lot
    heights = []
    for x in range(x0, x1 + 1):
        for z in range(z0, z1 + 1):
            found = b.probe(x, z)
            heights.append(found.waterline if found.water else found.y)
    heights.sort()
    return heights[len(heights) // 2]


def build(b, scene: Scene, fittings: dict) -> dict:
    """Build the lot in stages, in the Builder's palette but for the
    fittings and the furniture. Returns what each writer reported, by
    name."""
    palette = b.palette
    y = scene.y
    x0, z0, x1, z1 = scene.lot
    done = {}

    with b.stage("ground"):
        # Cleared land, leveled: trees felled whole, high ground cut away,
        # water filled from its bed behind a retaining ring. The pad keeps
        # roads and lamps out unless they name it.
        b.clear_vegetation((x0, z0), (x1, z1))
        b.level_pad(x0, z0, x1, z1, y, name="lot", over_water="fill")

    with b.stage("lane"):
        b.road(scene.lane, width=scene.lane_width, through=("lot",))

    with b.stage("house"):
        glass = palette["glass"]
        if isinstance(glass, Material):          # a family: its pane
            glass = glass.form("pane")
        walls = Facade(palette["wall"], corner=palette["frame"], pitch=4,
                       plinth=(1, palette["plinth"]),
                       bands={0: palette["frame"]},
                       window=Opening.rect(1, 2, sill=2, fill=glass))
        tile = palette["roof"]        # one family: its block, slab, stairs
        roof = Roof(eaves=("north", "south"), overhang=1,
                    block=tile, slab=tile, stair=tile)
        house = b.shell(*scene.house, walls, floor_y=y, top=scene.top,
                        stories=(scene.upper,), floor=palette["floor"],
                        openings=[("south", Opening.rect(1, 2), scene.door)],
                        roof=roof, attic=palette["gable"])
        done["house"] = house
        b.doorway(*house.walls["south"].door(scene.door),
                  door=fittings["door"], button=fittings["button"])
        done["flight"] = b.flight(
            StraightStair(scene.flight, "north", scene.upper - y,
                          stair=fittings["stair"], wood=fittings["wood"]),
            floor_y=scene.upper)

    with b.stage("furniture"):
        # Both stories in one call, walked from the doorstep before and
        # after: a table and benches, shelves and a lamp downstairs, a bed
        # and a lamp up the flight, each lamp where the game's flood lights
        # its whole story, the corner behind the flight included.
        rx0, rz0, rx1, rz1 = scene.room
        up = scene.upper
        table = Piece.table((0, 0), (1, 0), leg="minecraft:spruce_fence",
                            top="minecraft:spruce_pressure_plate")
        bench = "minecraft:spruce_stairs"
        lamp = Piece.lamp(lamp="minecraft:lantern",
                          post="minecraft:spruce_fence", height=1)
        done["furniture"] = b.furnish([
            table.at(rx0 + 5, y, rz0 + 3),
            Piece.seat((0, 0), (1, 0), back="north",
                       stair=bench).at(rx0 + 5, y, rz0 + 2),
            Piece.seat((0, 0), (1, 0), back="south",
                       stair=bench).at(rx0 + 5, y, rz0 + 4),
            Piece.stack((0, 0), (0, 2), block="minecraft:bookshelf",
                        height=2).at(rx1, y, rz0),
            lamp.at(rx0 + 2, y, rz0 + 3),
            Piece.bed((0, 0), (0, 1),
                      bed="minecraft:red_bed").at(rx1 - 1, up, rz1 - 2),
            lamp.at(rx0 + 4, up, rz0 + 3),
        ], room=scene.room, doorstep=scene.doorstep)

    with b.stage("tower"):
        tx0, tz0, tx1, tz1 = scene.tower
        stone = palette["plinth"]
        # Solid, since a room nobody can get into is a dark one, and a dark
        # room is where monsters spawn.
        b.fill((tx0 + 1, y + 1, tz0 + 1), (tx1 - 1, scene.tower_top, tz1 - 1),
               stone)
        tower = b.shell(*scene.tower, Facade(stone, corner=palette["frame"]),
                        floor_y=y, top=scene.tower_top, deck=stone,
                        parapet=stone, hollow=False)
        done["tower"] = tower
        # The parapet's own row, so every corner is a merlon.
        done["merlons"] = b.battlement(tower.perimeter, tower.top + 2,
                                       block=stone)
        # Nobody climbs to the deck either, and it is open to the sky: unlit,
        # monsters spawn on it at night -- inside the parapet, in its gaps
        # and on every merlon. So it is lit flush, in the palette's light;
        # the spawn check after the build holds the parapet and the
        # merlons' tops to it as well as the deck.
        done["deck"] = b.light_cells(
            [(x, scene.deck, z) for x in range(tx0 + 1, tx1)
             for z in range(tz0 + 1, tz1)], light_level=NO_SPAWNS)

    with b.stage("lamps"):
        # Once the road and what stands beside it are down; the square is
        # paved after, and counts this light as its own.
        done["lamps"] = b.line_lamps(
            scene.lane, post=fittings["post"], lamp=fittings["lamp"],
            light_level=NO_SPAWNS, width=scene.lane_width, through=("lot",))

    with b.stage("square"):
        done["square"] = b.pave(*scene.square, y,
                                light_level=NO_NEW_SNOW_OR_ICE)

    with b.stage("people"):
        b.villager(PROFESSION, *scene.villager)
    return done


def _audit_box(b, lot) -> tuple:
    """The lot and two blocks round it -- as far past a write as
    ``finish()``'s audit looks -- from the bottom of the world to its top."""
    x0, z0, x1, z1 = lot
    x0, z0, x1, z1 = x0 - 2, z0 - 2, x1 + 2, z1 + 2
    dim = b.save.dimension(b.dimension)
    lows, highs = [], []
    for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
        for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
            blocks = dim.blocks(chunk_x, chunk_z)
            if blocks is not None:
                lows.append(blocks.min_y)
                highs.append(blocks.max_y)
    if not lows:
        raise NBTError(f"no chunk under the lot ({x0 + 2}, {z0 + 2})..("
                       f"{x1 - 2}, {z1 - 2}) has been generated")
    return (x0, min(lows), z0), (x1, max(highs), z1)


def fault_key(fault) -> tuple:
    """A fault as the audit's before-and-after comparison takes it: the
    same block in the same cell with the same fault."""
    return (fault.cell, getattr(fault.block, "name", str(fault.block)),
            fault.kind)


def _after_save(site, world: str, box, diff, name: str, out) -> list[str]:
    """Everything the session does once its build is on disk: render the
    lot again, read back what reached the disk against ``diff``, and accept
    the worksite. The build is saved whatever happens here, so nothing
    raises: a render that cannot be written is a note, and a read-back
    that fails, or differs, is the problem returned, the worksite left
    unaccepted with its snapshot there to undo it."""
    try:
        site.render("after")
    except (NBTError, OSError) as exc:
        out(f"note: the after render was not written: {exc}")
    try:
        if site.diff(*box, save=MinecraftSave.load(world)) == diff:
            site.accept()
            out(f"saved and accepted; the snapshot and its renders are in "
                f"{site.directory}")
            return []
        problem = "what reached the disk is not what was built"
    except (NBTError, OSError) as exc:
        problem = f"saved, and then could not be read back and accepted: {exc}"
    out(f"FAILED: {problem}; saved, not accepted -- "
        f"Worksite.rollback(world, {name!r}) undoes it")
    return [problem]


def _rollback_plan(world: str, name: str, directory, write: bool,
                   out) -> RollbackResult | None:
    """What undoing the session would move: the rollback's dry run, and
    after a ``--write`` the command line's own report of it. A refusal --
    the game opened the world meanwhile -- is noted, not raised, since by
    then the session has done all it set out to; None is returned."""
    try:
        rollback = Worksite.rollback(world, name, directory=directory,
                                     dry_run=True)
    except (NBTError, OSError) as exc:
        out(f"worksite rollback (dry run) refused: {exc}")
        return None
    if write:
        # The command line's own report, through ``out`` like the rest.
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            command_line(["worksite", "rollback", world, name, "--dry-run"])
        for line in printed.getvalue().splitlines():
            out(line)
    elif rollback.files:
        # A dry run wrote nothing, so these moved under it, not by it.
        out(f"worksite rollback (dry run): {len(rollback.files)} of the "
            f"world's files changed while the session ran, not by it: "
            + ", ".join(rollback.files))
    else:
        out("worksite rollback (dry run): nothing to move -- the world is "
            "as the snapshot found it")
    return rollback


def _refuse_open(world: str) -> None:
    if world_is_open(world):
        raise NBTError(f"{world} is open in Minecraft: close it (quit to the "
                       f"title screen) and run again, or the game overwrites "
                       f"this build the next time it saves")


def run(world, x: int, z: int, *, palette: str = "roman", y: int | None = None,
        name: str = "craft-session", write: bool = False,
        dimension: str = OVERWORLD, out=print) -> Session:
    """One session over the lot centered on ``(x, z)``: open a worksite,
    build, finish, check, diff -- and save only with ``write``. Returns a
    :class:`Session`; a world it will not build in (open in Minecraft, no
    ground under the lot) and a call the Builder refuses raise
    ``NBTError``, with nothing saved. Once the build is saved nothing
    raises: what goes wrong after is in the session's ``problems``, or a
    note."""
    if palette not in PALETTES:
        raise NBTError(f"no palette {palette!r}; there are "
                       f"{', '.join(sorted(PALETTES))}")
    world = os.path.abspath(os.path.expanduser(os.fspath(world)))
    _refuse_open(world)
    lot = Scene.lot_of(x, z)
    x0, z0, x1, z1 = lot
    # A dry run keeps even its snapshot out of the world.
    scratch = None if write else tempfile.mkdtemp(prefix="craft-session-")
    try:
        directory = None if write else os.path.join(scratch, name)
        site = Worksite.open(world, (x0, z0), (x1, z1), name=name,
                             dimension=dimension, directory=directory)
        b = site.builder(seed=7, palette=PALETTES[palette])
        if y is None:
            y = ground_level(b, lot)
            out(f"ground level y={y}: the median over the lot")
        scene = Scene(x, z, y)
        # The land round the lot is somebody else's: the Builder refuses a
        # write that reaches it, and site.check() names it if anything
        # else changes it.
        guarded = sum(site.protect(corner1, corner2,
                                   name=f"beside the lot, {side}")
                      for side, (corner1, corner2) in scene.beside().items())
        out(f"{site!r}: the land round the lot protected, {BESIDE} blocks "
            f"out ({guarded:,} blocks); its snapshot in {site.directory}"
            + ("" if write else " (a dry run's, removed when it ends)"))
        if write:
            site.render("before")

        # What the game would not keep before anything is built, so the
        # audit after it can say what the build added.
        audited = _audit_box(b, lot)
        before = {fault_key(fault) for fault in
                  unsupported(b.save, *audited, dimension=dimension)}

        out(f"building the {palette} lot over ({x0}, {z0})..({x1}, {z1}) "
            f"at y={y}")
        done = build(b, scene, FITTINGS[palette])
        for label in ("house", "flight", "furniture", "lamps", "square"):
            out(f"  {label}: {done[label]!r}")
        out(f"  tower: {len(done['merlons'])} merlons; its deck "
            f"{done['deck']!r}")
        report = b.finish()
        out(f"  {report!r}")

        # The checks.
        problems = []
        new_faults = [fault for fault in report.unsupported
                      if fault_key(fault) not in before]
        problems += [f"the game will not keep {fault}" for fault in new_faults]
        check = site.check()
        if check:
            problems.append(f"site.check() found {list(check)}")
        hx0, hz0, hx1, hz1 = scene.house
        walk = reachable(b.save, scene.doorstep, (hx0, y - 1, hz0),
                         (hx1, scene.top + 1, scene.doorstep[2]),
                         dimension=dimension)
        upstairs = walk.footing(scene.upper)
        if walk.stranded:
            problems.append(f"{len(walk.stranded)} cells of the house can be "
                            f"reached and not left, first "
                            f"{min(walk.stranded)}")
        if not upstairs:
            problems.append("the top story cannot be reached from the door")
        spawns = [cell for corner1, corner2 in scene.lit()
                  for cell in spawnable(b.save, corner1, corner2,
                                        dimension=dimension)]
        if spawns:
            problems.append(f"{len(spawns)} cells the scene lit are dark "
                            f"enough to spawn in, first {spawns[0]}")
        stood = len(report.unsupported) - len(new_faults)
        out(f"checks: {len(new_faults)} new faults the game would not keep"
            + (f" ({stood} more stood before the build)" if stood else "")
            + f"; site.check() {list(check) if check else 'clean'}; "
            f"{len(walk.reached)} cells walked from the doorstep, "
            f"{len(upstairs)} of them upstairs, {len(walk.stranded)} "
            f"stranded; {len(spawns)} lit cells a monster could spawn in")

        written = frozenset(b.journal.written())
        ys = [cell[1] for cell in written]
        # One block past the lot: finish() settles connections that far.
        box = ((x0 - 1, min(ys), z0 - 1), (x1 + 1, max(ys), z1 + 1))
        diff = site.diff(*box)
        tally = b.journal.tally()
        out(f"the build changed {len(diff):,} blocks; the Builder wrote "
            f"{len(written):,} cells in {b.journal.total:,} writes, by "
            f"stage: " + ", ".join(f"{stage} {count:,}"
                                   for stage, count in tally.items()))

        saved = False
        if problems:
            for problem in problems:
                out(f"FAILED: {problem}")
            # Not site.restore(): nothing of this session's reached the
            # disk, so all a restore could put back is the snapshot over
            # whatever else wrote the world while it ran.
            out("nothing was saved")
        elif write:
            site.save.save()
            saved = True
            problems += _after_save(site, world, box, diff, name, out)
        else:
            out("dry run: nothing was saved")

        rollback = _rollback_plan(world, name, directory, write, out)
        return Session(palette, scene, done, report, new_faults, check, walk,
                       spawns, diff, written, tally, rollback, saved,
                       problems)
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("world",
                        help="path to the save directory (holds level.dat)")
    parser.add_argument("--at", nargs=2, required=True, metavar=("X", "Z"),
                        help="the lot's center; '~' is the player's position")
    parser.add_argument("--y", type=int, default=None,
                        help="the ground level to build at (default: the "
                             "median over the lot)")
    parser.add_argument("--palette", choices=sorted(PALETTES), default="roman",
                        help="whose materials to build in (default roman)")
    parser.add_argument("--name", default="craft-session",
                        help="the worksite's name (default craft-session)")
    parser.add_argument("--dimension", default=OVERWORLD,
                        help=f"the dimension to build in (default "
                             f"{OVERWORLD})")
    parser.add_argument("--write", action="store_true",
                        help="save the build; without it nothing is written")
    args = parser.parse_args(argv)

    world = os.path.abspath(os.path.expanduser(args.world))
    try:
        _refuse_open(world)
        x, z = MinecraftSave.load(world).resolve_coordinates(args.at,
                                                             axes="xz")
        session = run(world, x, z, palette=args.palette, y=args.y,
                      name=args.name, write=args.write,
                      dimension=args.dimension)
    except (NBTError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1 if session.problems else 0


if __name__ == "__main__":
    sys.exit(main())
