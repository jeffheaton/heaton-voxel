"""Tests for craft.build's furniture: Piece and Builder.furnish.

A piece of furniture is a small rule -- cells to states, in the piece's
own coordinates -- stood on a floor by one convention for every piece:
``dy=0`` is the cell your feet stand in, directly on the floor block a
frame's origin names. ``furnish`` places pieces as a player places items --
a bed whole, a fence leg joined to what stands beside it -- and refuses two
pieces in one cell, a cell already taken, what the game would not keep, and
a furnishing that cuts floor off the walk from the door: a shelf one cell
from the wall seals the floor behind it, and every picture draws a finished
room.

Three furniture vocabularies came before this one, and a representative
room from each is reproduced here cell for cell, the scripts' own code the
oracle wherever it can run: the suburban house's dining room
(``tools/suburban_house/plan.py``'s ``_dining``, over its ``furniture.py``),
a yellow tower flat (``tools/yellow_tower/layouts.json``, as
``build_tower.plan_furniture`` stamps it), and the Roman domus's triclinium
and cubicula (``tools/roman_city/jw_districts.py``'s lines, over
``common.py``'s couch, table, candelabrum, amphora and brazier). What a
block update derives -- a stair's corner shape, a fence's sides, a wall's
post -- is never compared: the pieces are placed, and the scripts wrote
states.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from heaton_voxel.blocks import AIR_BLOCKS, BlockState  # noqa: E402
from heaton_voxel.craft import (  # noqa: E402
    FROM_PALETTE,
    Builder,
    Facade,
    Frame,
    FurnishResult,
    Material,
    Mix,
    Opening,
    Piece,
    StraightStair,
)
from heaton_voxel.craft.block_facts import facts  # noqa: E402
from heaton_voxel.craft.build.walls import _same_crown  # noqa: E402
from heaton_voxel.craft.errors import IllegalBlock, Refused  # noqa: E402
from heaton_voxel.craft.survey import reachable, unsupported  # noqa: E402
from heaton_voxel.nbt import NBTError  # noqa: E402
from heaton_voxel.place import _cells_for  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402
from tests.test_world import make_save  # noqa: E402

CHUNKS = [(x, z) for x in range(3) for z in range(3)]
GROUND = 63
AIR = "minecraft:air"
STONE = "minecraft:stone_bricks"
PLANKS = "minecraft:spruce_planks"
OAK_STAIRS = "minecraft:oak_stairs"
SHELF = "minecraft:bookshelf"


# --------------------------------------------------------------------------
# The oracles: the scripts themselves, run against a recorder
# --------------------------------------------------------------------------

def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _suburban():
    """``tools/suburban_house/furniture.py`` and ``plan.py``, loaded by
    path. The plan imports its siblings by their bare names --
    ``furniture``, ``interior`` and ``house`` -- so each is lent its name
    while they load, and the names are given back after. (``check.py``
    beside them loads JeffWorld on import, and is left alone.)"""
    folder = os.path.join(_ROOT, "tools", "suburban_house")
    names = ("furniture", "house", "interior")
    before = {name: sys.modules.get(name) for name in names}
    try:
        lent = {}
        for name in names:
            spec = importlib.util.spec_from_file_location(
                name, os.path.join(folder, f"{name}.py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            lent[name] = module
        plan = _load("_furniture_suburban_plan", os.path.join(folder, "plan.py"))
    finally:
        for name, module in before.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return lent["furniture"], plan


ROMAN = _load("_furniture_roman_common",
              os.path.join(_ROOT, "tools", "roman_city", "common.py"))


class _Recorder:
    """Just enough Builder for a hand-furnished room: every cell it sets,
    or every cell of what it places (a bed's head), moved by ``offset``,
    the last write winning, as the world would keep it."""

    def __init__(self, offset=(0, 0, 0)):
        self.cells: dict[tuple[int, int, int], BlockState] = {}
        self.offset = offset

    def _put(self, x, y, z, state):
        dx, dy, dz = self.offset
        self.cells[(x + dx, y + dy, z + dz)] = state

    def set(self, x, y, z, block):
        self._put(x, y, z, BlockState.parse(block) if isinstance(block, str)
                  else block)

    def place(self, x, y, z, block):
        state = BlockState.parse(block) if isinstance(block, str) else block
        for cell, part in _cells_for(state, x, y, z):
            self._put(*cell, part)

    def fill(self, corner1, corner2, block):
        (x0, x1), (y0, y1), (z0, z1) = (sorted(pair) for pair in
                                        zip(corner1, corner2))
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    self.set(x, y, z, block)


def _region_digests(directory):
    region = os.path.join(directory, "dimensions/minecraft/overworld/region")
    files = {}
    for name in sorted(os.listdir(region)):
        with open(os.path.join(region, name), "rb") as handle:
            files[name] = hashlib.sha256(handle.read()).hexdigest()
    return files


def _furnished_room(directory):
    """A walled room furnished, finished and saved into ``directory``: the
    build ``test_identical_rooms_encode_identically`` runs here and in
    fresh interpreters, so it is a function of the module and not of a
    test."""
    make_save(directory, CHUNKS)
    save = MinecraftSave.load(directory)
    save.fill((0, 50, 0), (47, GROUND, 47), "minecraft:stone")
    b = Builder(save)
    house = b.shell(4, 4, 12, 10, Facade(STONE), floor_y=GROUND,
                    top=GROUND + 5, deck=PLANKS,
                    openings=[("south", Opening.rect(1, 2), 8)])
    b.doorway(*house.walls["south"].door(8))
    b.furnish([
        Piece.bed((0, 0), (0, 1), bed="minecraft:red_bed").at(10, GROUND, 5),
        Piece.table((0, 0), (1, 0), leg="minecraft:oak_fence",
                    top="minecraft:oak_pressure_plate").at(6, GROUND, 7),
        Piece.seat((0, 0), (1, 0), back="north",
                   stair=OAK_STAIRS).at(6, GROUND, 6),
        Piece.stack((0, 0), (0, 1), block=SHELF, height=3).at(5, GROUND, 8),
        Piece.pendant(lamp="minecraft:lantern", ceiling=5,
                      chain="minecraft:iron_chain", links=1).at(8, GROUND, 7),
    ], room=(5, 5, 11, 9), doorstep=(8, GROUND, 11))
    b.finish()
    save.save()


class FurnishBase(unittest.TestCase):
    """A save of nine chunks with stone up to y=63, the floor every piece
    here stands on."""

    def setUp(self):
        self.save, self.dir = self.fresh()
        self.b = Builder(self.save)

    def fresh(self):
        directory = tempfile.mkdtemp(prefix="furniture-tests-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        make_save(directory, CHUNKS)
        save = MinecraftSave.load(directory)
        save.fill((0, 50, 0), (47, GROUND, 47), "minecraft:stone")
        return save, directory

    def walled(self, x0=4, z0=4, x1=12, z1=10, *, door=8, b=None):
        """A one-room house: stone-brick walls round ``x0..x1``,
        ``z0..z1``, a plank ceiling at y=69 and an oak door in the south
        wall at ``x=door``. Returns ``(room, doorstep)`` as furnish takes
        them."""
        b = b or self.b
        house = b.shell(x0, z0, x1, z1, Facade(STONE), floor_y=GROUND,
                        top=GROUND + 5, deck=PLANKS,
                        openings=[("south", Opening.rect(1, 2), door)])
        b.doorway(*house.walls["south"].door(door))
        return (x0 + 1, z0 + 1, x1 - 1, z1 - 1), (door, GROUND, z1 + 1)

    def name(self, x, y, z, save=None):
        return (save or self.save).get_block(x, y, z).name

    def assertRoom(self, save, reference, cells):
        """Every cell of ``reference`` holds its state in ``save``, apart
        from what a block update derives, and every other one of ``cells``
        is empty: the room, and nothing else in it."""
        for cell in cells:
            found = save.get_block(*cell)
            want = reference.get(cell)
            if want is None:
                self.assertIn(found.name, AIR_BLOCKS,
                              f"{cell} holds {found}, which the room has not")
            else:
                self.assertTrue(_same_crown(found, want),
                                f"{cell} holds {found}, where the room has "
                                f"{want}")


def _box(corner1, corner2, skip=()):
    (x0, y0, z0), (x1, y1, z1) = corner1, corner2
    return [(x, y, z) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)
            for z in range(z0, z1 + 1) if (x, z) not in skip]


# --------------------------------------------------------------------------
# Piece: the rule, with no world in it
# --------------------------------------------------------------------------

class PieceTests(unittest.TestCase):
    def test_dy_zero_is_the_cell_on_the_floor_block_for_every_piece(self):
        """One height convention: every piece stood on the floor block at
        y=63 starts at y=64, the cell your feet stand in -- a seat, a
        counter, a table's legs, a bed, the bottom of a shelf, a lamp --
        and counts up from there."""
        x, z = 10, 20
        pieces = {
            "chair": (Piece.seat((0, 0), back="north", stair=OAK_STAIRS),
                      {64}),
            "counter": (Piece.table((0, 0), top="minecraft:smooth_quartz"),
                        {64}),
            "table": (Piece.table((0, 0), leg="minecraft:oak_fence",
                                  top="minecraft:oak_pressure_plate"),
                      {64, 65}),
            "bed": (Piece.bed((0, 0), (1, 0), bed="minecraft:red_bed"), {64}),
            "shelf": (Piece.stack((0, 0), block=SHELF, height=3),
                      {64, 65, 66}),
            "cupboard": (Piece.stack((0, 0), block="minecraft:barrel",
                                     height=2, dy=2), {66, 67}),
            "lamp": (Piece.lamp(lamp="minecraft:lantern"), {64}),
            "nightstand": (Piece.lamp(lamp="minecraft:lantern",
                                      post="minecraft:stripped_oak_wood"),
                           {64, 65}),
            # Under a ceiling block at y=69, five over the feet row.
            "pendant": (Piece.pendant(lamp="minecraft:lantern", ceiling=5,
                                      chain="minecraft:iron_chain", links=2),
                        {66, 67, 68}),
            "anything": (Piece({(0, 0, 0): "minecraft:flower_pot"}), {64}),
        }
        for name, (piece, heights) in pieces.items():
            with self.subTest(piece=name):
                cells = piece.at(x, GROUND, z).cells()
                self.assertEqual({y for _, y, _ in cells}, heights)
                self.assertEqual({y for _, y, _ in piece.blocks},
                                 {y - GROUND - 1 for y in heights},
                                 "its own dy counts from 0 on the floor")
        # The same piece on another floor is the same distance off it.
        upstairs = Piece.seat((0, 0), back="north", stair=OAK_STAIRS)
        self.assertEqual(list(upstairs.at(x, 70, z).cells()), [(x, 71, z)])
        with self.assertRaises(NBTError) as caught:
            Piece({(0, -1, 0): "minecraft:oak_planks"})
        self.assertIn("dy=0 is the cell on the floor block", str(caught.exception))

    def test_the_suburban_houses_fy_is_the_floor_block_a_piece_stands_on(self):
        """``tools/suburban_house/furniture.py`` takes the y of the floor
        block, ``fy``, and puts a chair's seat at ``fy + 1``: a piece stood
        at that floor block puts it in the same cell."""
        f, _ = _suburban()
        recorder = _Recorder()
        f.chair(recorder, GROUND, 10, 20, "west")
        f.shelf(recorder, GROUND, 12, 20, 12, 21, (1, 2, 3))
        f.nightstand(recorder, GROUND, 14, 20)
        mine = {}
        for piece in (
                Piece.seat((0, 0), back="west", stair=OAK_STAIRS).at(10, GROUND, 20),
                Piece.stack((0, 0), (0, 1), block=SHELF, height=3).at(12, GROUND, 20),
                Piece.lamp(lamp="minecraft:lantern[hanging=false,waterlogged=false]",
                           post="minecraft:stripped_oak_wood[axis=y]"
                           ).at(14, GROUND, 20)):
            mine.update(piece.cells())
        self.assertEqual(sorted(mine), sorted(recorder.cells))
        for cell, want in recorder.cells.items():
            self.assertTrue(_same_crown(mine[cell], want), cell)

    def test_a_seat_is_named_by_its_back(self):
        """A stair's full side is its ``facing`` side, so a seat's back is
        the way the stair faces: ``back="west"`` is a stair facing west,
        held on its west face and open to the east -- a bench against a west
        wall, sat on facing the room."""
        bench = Piece.seat((0, 0), (0, 4), back="west",
                           stair="minecraft:spruce_stairs")
        states = set(bench.blocks.values())
        self.assertEqual(states, {BlockState(
            "minecraft:spruce_stairs", {"facing": "west", "half": "bottom"})})
        (state,) = states
        held = facts(state.name, state.canonical().properties)
        self.assertTrue(held.holds("west"), "the back is the full side")
        self.assertFalse(held.holds("east"), "the seat opens to the east")
        # The curia's benches were written by the way the sitter looks:
        # common.bench(..., "east") against the west wall at x=414. Named by
        # its back, that is a seat whose back is to the room.
        recorder = _Recorder()
        ROMAN.bench(recorder, 415, 380, 415, 388, 65, "east")
        backwards = Piece.seat((0, 0), (0, 8), back="east",
                               stair="minecraft:spruce_stairs").at(415, 64, 380)
        self.assertEqual(backwards.cells(), recorder.cells)
        self.assertTrue(facts(state.name, {**state.canonical().properties,
                                            "facing": "east"}).holds("east"),
                        "the bench as written holds its back to the room")
        # A facing is never the seat's to be told.
        for given in ("minecraft:spruce_stairs[facing=east]",
                      "minecraft:spruce_stairs[half=top]"):
            with self.subTest(stair=given):
                with self.assertRaises(NBTError) as caught:
                    Piece.seat((0, 0), back="west", stair=given)
                self.assertIn("which the piece sets", str(caught.exception))
        with self.assertRaises(NBTError):
            Piece.seat((0, 0), back="west", stair="minecraft:oak_slab")
        with self.assertRaises(NBTError):
            Piece.seat((0, 0), back="up", stair=OAK_STAIRS)

    def test_a_sofa_and_its_arms_are_the_suburban_houses(self):
        """``furniture.sofa`` and ``chair``: a run of stairs facing the way
        the back points, and open trapdoor arms past each end facing the
        seat."""
        f, _ = _suburban()
        arm = ("minecraft:oak_trapdoor[half=bottom,open=true,powered=false,"
               "waterlogged=false]")
        for back, corner2, call in (
                ("north", (4, 0), (10, 20, 14, 20)),
                ("east", (0, 3), (10, 20, 10, 23))):
            with self.subTest(back=back):
                recorder = _Recorder()
                f.sofa(recorder, GROUND, *call, back)
                sofa = Piece.seat((0, 0), corner2, back=back, stair=OAK_STAIRS,
                                  ends=arm).at(10, GROUND, 20)
                cells = sofa.cells()
                self.assertEqual(sorted(cells), sorted(recorder.cells))
                for cell, want in recorder.cells.items():
                    self.assertEqual(cells[cell].canonical(), want.canonical(),
                                     cell)
        with self.assertRaises(NBTError) as caught:
            Piece.seat((0, 0), (1, 1), back="north", stair=OAK_STAIRS, ends=arm)
        self.assertIn("single row", str(caught.exception))
        with self.assertRaises(NBTError):
            Piece.seat((0, 0), (2, 0), back="north", stair=OAK_STAIRS,
                       ends="minecraft:oak_trapdoor[facing=north]")

    def test_a_bed_is_one_placement_whichever_half_is_given(self):
        """By its foot, by its head, by both as the yellow tower's layouts
        list a bed, or by the rule: one placement from the foot, both
        halves written. Halves that do not make one bed are refused."""
        foot = "minecraft:red_bed[facing=south,part=foot]"
        head = "minecraft:red_bed[facing=south,part=head]"
        by_foot = Piece({(0, 0, 0): "minecraft:red_bed[facing=south]"})
        for other in (Piece({(0, 0, 1): head}),
                      Piece({(0, 0, 0): foot, (0, 0, 1): head}),
                      Piece({(0, 0, 1): head, (0, 0, 0): foot}),
                      Piece.bed((0, 0), (0, 1), bed="minecraft:red_bed")):
            self.assertEqual(other.at(5, GROUND, 5).placements(),
                             by_foot.at(5, GROUND, 5).placements())
            self.assertEqual(dict(other.blocks), {
                (0, 0, 0): BlockState.parse(foot),
                (0, 0, 1): BlockState.parse(head)})
        for blocks in ({(0, 0, 0): foot,
                        (0, 0, 1): "minecraft:red_bed[facing=north,part=head]"},
                       {(0, 0, 0): "minecraft:red_bed[facing=south]",
                        (0, 0, 1): "minecraft:decorated_pot"}):
            with self.assertRaises(NBTError) as caught:
                Piece(blocks)
            self.assertIn("must agree", str(caught.exception))
        with self.assertRaises(NBTError) as caught:
            Piece({(0, 0, 0): "minecraft:red_bed[facing=east]",
                   (2, 0, 0): "minecraft:red_bed[facing=west]"})
        self.assertIn("puts two things in (1, 0, 0)", str(caught.exception))
        self.assertNotIn("None", str(caught.exception))
        with self.assertRaises(NBTError):
            Piece.bed((0, 0), (1, 1), bed="minecraft:red_bed")
        with self.assertRaises(NBTError):
            Piece.bed((0, 0), (0, 1), bed="minecraft:red_bed[facing=north]")
        # A door or a tall plant given by its upper half is placed from the
        # cell under it -- never under the floor row.
        door = Piece({(0, 1, 0): "minecraft:oak_door[facing=east,half=upper]"})
        self.assertEqual([cell for cell, _ in door.at(0, GROUND, 0).placements()],
                         [(0, 64, 0)])
        self.assertEqual(len(Piece({(0, 1, 0): "minecraft:peony[half=upper]"}).blocks), 2)
        with self.assertRaises(NBTError) as caught:
            Piece({(0, 0, 0): "minecraft:oak_door[half=upper]"})
        self.assertIn("under the floor row", str(caught.exception))

    def test_lava_and_flowing_water_are_not_furniture(self):
        """A piece may hold water -- a tub's or a fountain's sources, a
        waterlogged lantern, a bare sea pickle (waterlogged by default),
        kelp -- which the water check holds it to when it is furnished.
        Lava, which nothing follows, and flowing water, which the game
        makes from its sources and re-reads the first time anything
        updates beside it, are no furniture."""
        for block, words in (("minecraft:lava", "is lava"),
                             ("minecraft:lava[level=0]", "is lava"),
                             ("minecraft:water[level=1]", "flowing water"),
                             ("minecraft:water[level=8]", "flowing water")):
            with self.subTest(block=block):
                with self.assertRaises(NBTError) as caught:
                    Piece({(0, 0, 0): block})
                self.assertIn("not furniture", str(caught.exception))
                self.assertIn(words, str(caught.exception))
        for block in ("minecraft:water", "minecraft:water[level=0]",
                      "minecraft:sea_pickle",
                      "minecraft:lantern[waterlogged=true]",
                      "minecraft:kelp",
                      "minecraft:sea_pickle[waterlogged=false]",
                      "minecraft:water_cauldron[level=3]"):
            with self.subTest(block=block):
                Piece({(0, 0, 0): block})

    def test_a_spill_is_the_pieces_own_cells_placed_with_it(self):
        """``spill`` is the piece's own (dx, dy, dz) cells its water may
        run into, placed and turned with its blocks into the world cells
        the water check takes; and it is part of what the piece is. A
        spill under the floor row, on one of the piece's own blocks, given
        to a piece with no water, or that is no cells at all, is refused
        as the piece is made."""
        spout = Piece({(0, 0, 0): STONE, (0, 1, 0): "minecraft:water"},
                      spill=[(0, 1, 1), (0, 0, 1)], name="spout")
        self.assertEqual(spout.spill, ((0, 0, 1), (0, 1, 1)))
        frame = Frame((30, GROUND, 10), "west")
        turned = spout.turned(frame)
        by_hand = tuple(sorted((x, y + 1, z) for x, y, z in
                               (frame.cell(0, 0, 1), frame.cell(0, 1, 1))))
        self.assertEqual(turned.spill_cells(), by_hand)
        # Out of the piece is +z; facing west, out is -x.
        self.assertEqual(turned.spill_cells(), ((29, 64, 10), (29, 65, 10)))
        self.assertEqual(spout.at(5, GROUND, 5).spill_cells(),
                         ((5, 64, 6), (5, 65, 6)))
        self.assertNotEqual(spout, Piece(dict(spout.blocks), name="spout"))
        self.assertEqual(pickle.loads(pickle.dumps(turned)).spill_cells(),
                         by_hand)
        self.assertEqual(Piece({(0, 0, 0): STONE}).spill, ())
        with self.assertRaises(NBTError) as caught:
            spout.spill_cells()
        self.assertIn("not placed yet", str(caught.exception))
        water = {(0, 0, 0): "minecraft:water"}
        for spill, words in ([[(0, -1, 0)], "under the floor row"],
                             [[(0, 0, 0)], "where it puts"],
                             [[(0, 0.5, 1)], "three whole numbers"],
                             [True, "its own cells"],
                             ["up", "its own cells"]):
            with self.subTest(spill=spill):
                with self.assertRaises(NBTError) as caught:
                    Piece(water, spill=spill)
                self.assertIn(words, str(caught.exception))
        with self.assertRaises(NBTError) as caught:
            Piece({(0, 0, 0): STONE}, spill=[(0, 1, 0)])
        self.assertIn("holds no water", str(caught.exception))

    def test_materials_are_named_never_a_palettes(self):
        """Every block is named at the call. A Material is a family's form
        where a piece writes one shape of it -- a seat's stairs, a stack's
        block -- and a TypeError where it does not; a Mix, a palette's
        role and None are TypeErrors anywhere, since a piece is placed and
        a placement is one block."""
        oak = Material("minecraft:oak_planks")
        self.assertEqual(Piece.seat((0, 0), back="north", stair=oak
                                    ).blocks[(0, 0, 0)].name, OAK_STAIRS)
        self.assertEqual(Piece.stack((0, 0), block=oak).blocks[(0, 0, 0)].name,
                         "minecraft:oak_planks")
        mix = Mix([("minecraft:oak_planks", 1), ("minecraft:spruce_planks", 1)])
        for call in (
                lambda: Piece.table((0, 0), top=oak),
                lambda: Piece.bed((0, 0), (0, 1), bed=oak),
                lambda: Piece({(0, 0, 0): oak}),
                lambda: Piece({(0, 0, 0): mix}),
                lambda: Piece.stack((0, 0), block=mix),
                lambda: Piece.seat((0, 0), back="north", stair=FROM_PALETTE),
                lambda: Piece.table((0, 0), top=None),
                lambda: Piece({(0, 0, 0): 7})):
            with self.assertRaises(TypeError):
                call()

    def test_bad_pieces_are_refused_by_name(self):
        for call, words in (
                (lambda: Piece({}), "has no blocks"),
                (lambda: Piece([((0, 0, 0), SHELF)]), "a mapping"),
                (lambda: Piece({(0, 0): SHELF}), "three whole numbers"),
                (lambda: Piece({(0, 0.5, 0): SHELF}), "three whole numbers"),
                (lambda: Piece({(0, 0, 0): AIR}), "clearing is a shell's"),
                (lambda: Piece({(0, 0, 0): "minecraft:cave_air"}), "clearing"),
                (lambda: Piece({(0, 0, 0): SHELF}, name=""), "non-empty string"),
                (lambda: Piece.stack((0, 0), block=SHELF, height=0), "at least one"),
                (lambda: Piece.stack((0, 0), block=SHELF, dy=-1), "below it"),
                (lambda: Piece.lamp(lamp="minecraft:lantern[hanging=true]"),
                 "Piece.pendant"),
                (lambda: Piece.lamp(lamp="minecraft:lantern", height=2),
                 "has no post"),
                (lambda: Piece.lamp(lamp="minecraft:lantern", post=SHELF,
                                    height=0), "at least one"),
                (lambda: Piece.pendant(lamp="minecraft:lantern", ceiling=3,
                                       links=1), "name the chain"),
                (lambda: Piece.pendant(lamp="minecraft:lantern", ceiling=1,
                                       chain="minecraft:iron_chain", links=2),
                 "below the floor row"),
                (lambda: Piece.pendant(lamp="minecraft:lantern[hanging=false]",
                                       ceiling=3), "Piece.lamp"),
                (lambda: Piece.bed((0, 0), (0, 1), bed="minecraft:oak_planks"),
                 "is not one"),
                (lambda: Piece.seat("north", back="north", stair=OAK_STAIRS),
                 "pair of ints")):
            with self.subTest(words=words):
                with self.assertRaises(NBTError) as caught:
                    call()
                self.assertIn(words, str(caught.exception))

    def test_a_pendant_hangs_its_lamp(self):
        """A lantern hung from a ceiling hangs: ``hanging=true`` is set
        unless the lamp names it, on links of chain down from the
        ceiling."""
        pendant = Piece.pendant((1, 2), lamp="minecraft:lantern", ceiling=5,
                                chain="minecraft:iron_chain[axis=y]", links=1)
        self.assertEqual(dict(pendant.blocks), {
            (1, 3, 2): BlockState.parse("minecraft:lantern[hanging=true]"),
            (1, 4, 2): BlockState.parse("minecraft:iron_chain[axis=y]")})
        glow = Piece.pendant(lamp="minecraft:glowstone", ceiling=4)
        self.assertEqual(dict(glow.blocks),
                         {(0, 3, 0): BlockState("minecraft:glowstone")})

    def test_a_turned_piece_turns_its_cells_and_its_states(self):
        """Through a frame the cells go where the frame puts them, one row
        over the floor block at its origin, and so do the states -- a
        property left at its default included: a bare stair faces north,
        so a frame facing west turns it east."""
        piece = Piece({(0, 0, 0): OAK_STAIRS,
                       (0, 0, 2): "minecraft:blue_bed[facing=east]"})
        turned = piece.turned(Frame((30, GROUND, 10), "west"))
        self.assertEqual(turned.cells(), {
            (28, 64, 10): BlockState.parse("minecraft:blue_bed[facing=south,part=foot]"),
            (28, 64, 11): BlockState.parse("minecraft:blue_bed[facing=south,part=head]"),
            (30, 64, 10): BlockState.parse("minecraft:oak_stairs[facing=east]"),
        })
        # Laid out on a room's floor at y=0 and turned with the room.
        inner = piece.turned(Frame((2, 0, 0), "east"))
        outer = Frame((30, GROUND, 10), "south")
        self.assertEqual(inner.turned(outer).cells(),
                         piece.turned(Frame((2, 0, 0), "east").within(outer)).cells())
        self.assertEqual(inner.turned(outer).frame,
                         Frame((32, GROUND, 10), "east"))
        with self.assertRaises(NBTError):
            piece.turned((30, GROUND, 10))
        with self.assertRaises(NBTError) as caught:
            piece.cells()
        self.assertIn("not placed yet", str(caught.exception))

    def test_a_piece_is_a_value(self):
        piece = Piece.seat((0, 0), back="north", stair=OAK_STAIRS, name="chair")
        same = Piece({(0, 0, 0): "minecraft:oak_stairs[facing=north,half=bottom]"},
                     name="chair")
        self.assertEqual(piece, same)
        self.assertEqual(hash(piece), hash(same))
        self.assertNotEqual(piece, piece.at(1, 2, 3))
        self.assertEqual(piece.at(1, 2, 3), same.at(1, 2, 3))
        self.assertNotEqual(piece, Piece(dict(piece.blocks), name="stool"))
        placed = piece.turned(Frame((1, 2, 3), "east", mirrored=True))
        for value in (piece, placed):
            self.assertIs(copy.copy(value), value)
            self.assertIs(copy.deepcopy(value), value)
            self.assertEqual(pickle.loads(pickle.dumps(value)), value)
        self.assertEqual(pickle.loads(pickle.dumps(placed)).cells(), placed.cells())
        with self.assertRaises(AttributeError):
            piece.name = "stool"
        with self.assertRaises(AttributeError):
            del piece._frame
        self.assertIn("'chair'", repr(placed))
        self.assertIn("Frame", repr(placed))


# --------------------------------------------------------------------------
# furnish: the pieces in a world
# --------------------------------------------------------------------------

#: A corner of a room, designed facing south (the frame's identity): a bare
#: stair, a fence-leg table with a pressure plate on it, a barrel, a bed
#: whose head lies east of its foot, and a door.
NOOK = {
    (0, 0, 0): OAK_STAIRS,
    (1, 0, 0): "minecraft:oak_fence",
    (1, 1, 0): "minecraft:oak_pressure_plate",
    (2, 0, 0): "minecraft:barrel[facing=south]",
    (0, 0, 2): "minecraft:blue_bed[facing=east]",
    (3, 0, 2): "minecraft:oak_door[facing=south]",
}

#: The same nook placed by hand in each frame: where each block lands and
#: how it faces, worked out on paper, one row over the floor at y=63.
NOOK_BY_HAND = {
    Frame((10, GROUND, 10), "south"): [
        ((10, 64, 10), "minecraft:oak_stairs[facing=north]"),
        ((11, 64, 10), "minecraft:oak_fence"),
        ((11, 65, 10), "minecraft:oak_pressure_plate"),
        ((12, 64, 10), "minecraft:barrel[facing=south]"),
        ((10, 64, 12), "minecraft:blue_bed[facing=east]"),
        ((13, 64, 12), "minecraft:oak_door[facing=south]")],
    Frame((30, GROUND, 10), "west"): [
        ((30, 64, 10), "minecraft:oak_stairs[facing=east]"),
        ((30, 64, 11), "minecraft:oak_fence"),
        ((30, 65, 11), "minecraft:oak_pressure_plate"),
        ((30, 64, 12), "minecraft:barrel[facing=west]"),
        ((28, 64, 10), "minecraft:blue_bed[facing=south]"),
        ((28, 64, 13), "minecraft:oak_door[facing=west]")],
    Frame((10, GROUND, 30), "north"): [
        ((10, 64, 30), "minecraft:oak_stairs[facing=south]"),
        ((9, 64, 30), "minecraft:oak_fence"),
        ((9, 65, 30), "minecraft:oak_pressure_plate"),
        ((8, 64, 30), "minecraft:barrel[facing=north]"),
        ((10, 64, 28), "minecraft:blue_bed[facing=west]"),
        ((7, 64, 28), "minecraft:oak_door[facing=north]")],
    Frame((30, GROUND, 30), "east"): [
        ((30, 64, 30), "minecraft:oak_stairs[facing=west]"),
        ((30, 64, 29), "minecraft:oak_fence"),
        ((30, 65, 29), "minecraft:oak_pressure_plate"),
        ((30, 64, 28), "minecraft:barrel[facing=east]"),
        ((32, 64, 30), "minecraft:blue_bed[facing=north]"),
        ((32, 64, 27), "minecraft:oak_door[facing=east]")],
    Frame((20, GROUND, 40), "south", mirrored=True): [
        ((20, 64, 40), "minecraft:oak_stairs[facing=north]"),
        ((19, 64, 40), "minecraft:oak_fence"),
        ((19, 65, 40), "minecraft:oak_pressure_plate"),
        ((18, 64, 40), "minecraft:barrel[facing=south]"),
        ((20, 64, 42), "minecraft:blue_bed[facing=west]"),
        ((17, 64, 42), "minecraft:oak_door[facing=south,hinge=right]")],
}


class FurnishTests(FurnishBase):
    def test_furniture_stands_on_the_floor_it_is_given(self):
        result = self.b.furnish([
            Piece.table((0, 0), top="minecraft:smooth_quartz").at(5, GROUND, 5),
            Piece.table((0, 0), leg="minecraft:oak_fence",
                        top="minecraft:oak_pressure_plate").at(7, GROUND, 5),
            Piece.bed((0, 0), (0, 1), bed="minecraft:red_bed").at(9, GROUND, 5),
            Piece.stack((0, 0), block=SHELF, height=2, dy=2).at(11, GROUND, 5),
        ], walk=False)
        self.assertIsInstance(result, FurnishResult)
        self.assertEqual(self.name(5, 64, 5), "minecraft:smooth_quartz")
        self.assertEqual(self.name(7, 64, 5), "minecraft:oak_fence")
        self.assertEqual(self.name(7, 65, 5), "minecraft:oak_pressure_plate")
        self.assertEqual(self.name(9, 64, 5), "minecraft:red_bed")
        self.assertEqual(self.name(9, 64, 6), "minecraft:red_bed")
        self.assertEqual([self.name(11, y, 5) for y in (64, 65, 66, 67, 68)],
                         [AIR, AIR, SHELF, SHELF, AIR])
        self.assertEqual(self.name(5, GROUND, 5), "minecraft:stone",
                         "nothing reaches into the floor")
        self.assertEqual((result.pieces, result.placements, result.blocks,
                          result.written, result.already), (4, 6, 7, 7, 0))
        self.assertIsNone(result.reach)

    def test_a_turned_piece_in_all_four_facings_is_the_piece_placed_by_hand(self):
        """The nook framed four ways and mirrored, furnished, holds exactly
        the blocks the same nook placed by hand does in every cell -- a bed
        whose head turns with it, a stair, a barrel and a door facing where
        the frame faces them, a bare stair turned off its default facing,
        and a mirrored door hung from its other side."""
        self.b.furnish([Piece(NOOK, name="nook").turned(frame)
                        for frame in NOOK_BY_HAND], walk=False)
        other, _ = self.fresh()
        by_hand = Builder(other)
        for placements in NOOK_BY_HAND.values():
            for cell, block in placements:
                by_hand.place(*cell, block)
        placed = 0
        for cell in _box((0, 64, 0), (47, 66, 47)):
            mine, theirs = self.save.get_block(*cell), other.get_block(*cell)
            self.assertEqual(mine.canonical(), theirs.canonical(), cell)
            placed += mine.name not in AIR_BLOCKS
        self.assertEqual(placed, 5 * 8, "six placements, two of them two blocks")

    def test_a_bed_comes_whole(self):
        room, doorstep = self.walled()
        result = self.b.furnish(
            Piece.bed((0, 0), (1, 0), bed="minecraft:white_bed").at(6, GROUND, 6),
            room=room, doorstep=doorstep)
        self.assertEqual(str(self.save.get_block(6, 64, 6)),
                         "minecraft:white_bed[facing=east,part=foot]")
        self.assertEqual(str(self.save.get_block(7, 64, 6)),
                         "minecraft:white_bed[facing=east,part=head]")
        written = set(self.b.journal.written())
        self.assertLessEqual({(6, 64, 6), (7, 64, 6)}, written)
        self.assertEqual((result.placements, result.blocks), (1, 2))
        self.assertEqual(unsupported(self.save, (5, 63, 5), (11, 66, 9)), [])

    def test_two_pieces_in_one_cell_are_refused(self):
        """A bed and an amphora on its head -- the Roman insula's units
        that face south (``tools/roman_city/jw_districts.py``) set the pot
        over the head and left the foot alone, which the game breaks at
        its next update. Refused, both pieces named, nothing written."""
        before = self.b.blocks_set
        bed = Piece.bed((0, 0), (-1, 0), bed="minecraft:red_bed", name="bed")
        pot = Piece.stack((0, 0), block="minecraft:decorated_pot", name="amphora")
        with self.assertRaises(Refused) as caught:
            self.b.furnish([bed.at(20, GROUND, 6), pot.at(19, GROUND, 6)],
                           walk=False)
        self.assertEqual(caught.exception.faults, [
            "(19, 64, 6): piece 0 ('bed') and piece 1 ('amphora') both stand there"])
        self.assertEqual(self.b.blocks_set, before)
        self.assertEqual(self.name(20, 64, 6), AIR)

    def test_shelves_that_seal_the_floor_are_refused(self):
        """A bookshelf stack three high one cell from the north wall draws
        as a furnished room and seals the row behind it; against the wall
        it is fine. A counter across the only way through leaves the far
        side to whoever jumps."""
        room, doorstep = self.walled()
        before = self.b.blocks_set
        shelf = Piece.stack((0, 0), (6, 0), block=SHELF, height=3, name="shelf")
        with self.assertRaises(Refused) as caught:
            self.b.furnish(shelf.at(5, GROUND, 6), room=room, doorstep=doorstep)
        self.assertEqual(caught.exception.faults,
                         [f"({x}, 63, 5) is sealed" for x in range(5, 12)])
        self.assertIn("off the walk from (8, 63, 11)", str(caught.exception))
        self.assertEqual(self.b.blocks_set, before)
        self.assertEqual(self.name(5, 64, 6), AIR)
        counter = Piece.stack((0, 0), (6, 0), block="minecraft:smooth_quartz")
        with self.assertRaises(Refused) as caught:
            self.b.furnish(counter.at(5, GROUND, 7), room=room, doorstep=doorstep)
        self.assertEqual(caught.exception.faults,
                         [f"({x}, 63, {z}) now needs a jump"
                          for x in range(5, 12) for z in (5, 6)])
        result = self.b.furnish(shelf.at(5, GROUND, 5), room=room,
                                doorstep=doorstep)
        self.assertFalse(result.reach.stranded)
        self.assertEqual(self.name(8, 66, 5), SHELF)

    def test_a_dais_you_hop_onto_is_floor_too(self):
        """Floor you could only jump onto before is still floor you could
        get to: a stone dais in the corner, a block up, walled off by two
        shelves, is sealed, and that is refused."""
        room, doorstep = self.walled()
        self.b.fill((5, 64, 5), (6, 64, 6), "minecraft:stone")
        walk = reachable(self.save, doorstep, (4, 62, 4), (12, 70, 11))
        self.assertIn((5, 64, 5), walk.jumped)
        self.assertNotIn((5, 64, 5), walk.stranded)
        before = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.furnish(
                [Piece.stack((0, 0), (0, 1), block=SHELF, height=3).at(7, GROUND, 5),
                 Piece.stack((0, 0), (1, 0), block=SHELF, height=3).at(5, GROUND, 7)],
                room=room, doorstep=doorstep)
        self.assertEqual(caught.exception.faults,
                         [f"({x}, 64, {z}) is sealed" for x in (5, 6) for z in (5, 6)])
        self.assertEqual(self.b.blocks_set, before)
        self.assertEqual(self.name(7, 64, 5), AIR)

    def test_floor_made_one_way_is_refused(self):
        """A dais of bottom slabs you step up onto, and cupboards hung over
        the floor beside it at ``dy=2``: you still step up, since the slab
        is half a block, but stepping off it puts your head into the
        cupboard. Every cell of the dais is one-way, and refused."""
        room, doorstep = self.walled()
        self.b.fill((5, 64, 5), (6, 64, 9), "minecraft:stone_slab[type=bottom]")
        walk = reachable(self.save, doorstep, (4, 62, 4), (12, 70, 11))
        self.assertIn((5, 64, 5), walk.walked)
        self.assertFalse(walk.stranded)
        before = self.b.blocks_set
        cupboards = Piece.stack((0, 0), (0, 4), block="minecraft:barrel", dy=2)
        with self.assertRaises(Refused) as caught:
            self.b.furnish(cupboards.at(7, GROUND, 5), room=room, doorstep=doorstep)
        self.assertEqual(caught.exception.faults,
                         [f"({x}, 64, {z}) is one-way: you get there and not back"
                          for x in (5, 6) for z in range(5, 10)])
        self.assertIn("off the walk from (8, 63, 11)", str(caught.exception))
        self.assertEqual(self.b.blocks_set, before)
        self.assertEqual(self.name(7, 66, 5), AIR)

    def test_floor_one_way_before_is_not_the_furnishings(self):
        """The walk refuses only what the plan takes away, as the audit
        does: the same dais made one-way by hand beforehand is no reason to
        refuse a chair across the room, and stays as one-way as it was."""
        room, doorstep = self.walled()
        self.b.fill((5, 64, 5), (6, 64, 9), "minecraft:stone_slab[type=bottom]")
        self.b.fill((7, 66, 5), (7, 66, 9), "minecraft:barrel")
        dais = {(x, 64, z) for x in (5, 6) for z in range(5, 10)}
        walk = reachable(self.save, doorstep, (4, 62, 4), (12, 70, 11))
        self.assertEqual(walk.stranded, dais)
        result = self.b.furnish(
            Piece.seat((0, 0), back="east", stair=OAK_STAIRS).at(11, GROUND, 7),
            room=room, doorstep=doorstep)
        self.assertEqual(self.name(11, 64, 7), OAK_STAIRS)
        self.assertEqual(result.reach.stranded, dais)

    def test_a_piece_past_the_room_walked_is_refused(self):
        """The walk vouches only for the columns it walks, so every piece
        stands in the room named or the doorstep's column. A shelf in the
        next room, across the gap that is its only way in, would seal it
        unseen: refused before anything is read. Named with a room that
        holds both, the walk sees the far room sealed."""
        room, doorstep = self.walled()
        self.b.set(12, 64, 7, AIR)                  # a gap into a second room
        self.b.set(12, 65, 7, AIR)
        self.b.fill((13, 64, 4), (18, 68, 4), STONE)
        self.b.fill((13, 64, 10), (18, 68, 10), STONE)
        self.b.fill((19, 64, 4), (19, 68, 10), STONE)
        self.b.fill((13, 69, 4), (19, 69, 10), PLANKS)
        self.assertIn((18, GROUND, 9), reachable(
            self.save, doorstep, (4, 62, 4), (19, 70, 11)).reached)
        shelf = Piece.stack((0, 0), (0, 4), block=SHELF, height=3,
                            name="shelf").at(13, GROUND, 5)
        reads = []
        read = self.b._read
        self.b._read = lambda cell: reads.append(cell) or read(cell)
        before = self.b.blocks_set
        with self.assertRaises(NBTError) as caught:
            self.b.furnish(shelf, room=room, doorstep=doorstep)
        self.assertNotIsInstance(caught.exception, Refused)
        self.assertIn("piece 0 ('shelf') stands at (13, 64, 5), outside the "
                      "room (5, 5, 11, 9)", str(caught.exception))
        self.assertEqual(reads, [], "nothing was read")
        self.assertEqual(self.b.blocks_set, before)
        with self.assertRaises(Refused) as caught:
            self.b.furnish(shelf, room=(5, 5, 18, 9), doorstep=doorstep)
        self.assertEqual(caught.exception.faults,
                         [f"({x}, 63, {z}) is sealed"
                          for x in range(14, 19) for z in range(5, 10)])
        self.assertEqual(self.b.blocks_set, before)
        # The doorstep's own column is walked: a lamp hung over it is fine.
        self.b.set(8, 69, 11, PLANKS)
        self.b.furnish(Piece.pendant(lamp="minecraft:lantern", ceiling=5
                                     ).at(*doorstep), room=room, doorstep=doorstep)
        self.assertEqual(self.name(8, 68, 11), "minecraft:lantern")

    def test_furniture_never_writes_into_a_wall(self):
        """The suburban vanity's lesson: a piece put into a wall cell is a
        hole through it. A cell holding anything but air is refused, every
        one named; ``overwrite=True`` places through it."""
        self.b.set(6, 64, 6, "minecraft:stone")
        self.b.set(6, 65, 6, "minecraft:glass")
        shelf = Piece.stack((0, 0), block=SHELF, height=3).at(6, GROUND, 6)
        with self.assertRaises(Refused) as caught:
            self.b.furnish(shelf, walk=False)
        self.assertEqual(len(caught.exception.faults), 2)
        self.assertIn("(6, 64, 6) holds minecraft:stone", caught.exception.faults[0])
        self.assertEqual(self.name(6, 66, 6), AIR)
        self.b.furnish(shelf, walk=False, overwrite=True)
        self.assertEqual([self.name(6, y, 6) for y in (64, 65, 66)], [SHELF] * 3)

    def test_a_chest_under_a_counter_is_refused_and_a_barrel_is_not(self):
        """A chest will not open under a block that conducts power, which a
        smooth quartz worktop does; a barrel opens whatever is over it, and
        so does a chest under a slab."""
        top = Piece.stack((0, 0), block="minecraft:smooth_quartz", dy=1)
        before = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.furnish([Piece.stack((0, 0), block="minecraft:chest").at(6, GROUND, 6),
                            top.at(6, GROUND, 6)], walk=False)
        self.assertEqual([fault.kind for fault in caught.exception.faults],
                         ["blocked chest"])
        self.assertEqual(self.b.blocks_set, before)
        self.b.furnish([Piece.stack((0, 0), block="minecraft:barrel").at(6, GROUND, 6),
                        top.at(6, GROUND, 6)], walk=False)
        self.b.furnish([Piece.stack((0, 0), block="minecraft:chest").at(8, GROUND, 6),
                        Piece.stack((0, 0), block="minecraft:oak_slab", dy=1
                                    ).at(8, GROUND, 6)], walk=False)
        self.assertEqual(self.name(8, 64, 6), "minecraft:chest")

    def test_a_lantern_with_nothing_to_hang_from_is_refused(self):
        lamp = Piece.pendant(lamp="minecraft:lantern", ceiling=5)
        with self.assertRaises(Refused) as caught:
            self.b.furnish(lamp.at(20, GROUND, 20), walk=False)
        self.assertEqual([(fault.cell, fault.kind) for fault in caught.exception.faults],
                         [((20, 68, 20), "no footing")])
        self.b.set(20, 69, 20, PLANKS)              # a ceiling to hang from
        self.b.furnish(lamp.at(20, GROUND, 20), walk=False)
        self.assertEqual(str(self.save.get_block(20, 68, 20)),
                         "minecraft:lantern[hanging=true]")

    def test_a_fault_already_standing_is_not_the_furnishings(self):
        """A torch somebody left hanging on nothing, inside the reach of
        the audit, is not the furnishing's to answer for: the chair beside
        it is placed, and the torch is still the fault it was."""
        room, doorstep = self.walled()
        self.save.set_block(7, 65, 7, BlockState.parse(
            "minecraft:wall_torch[facing=south]"))
        torch = [(fault.cell, fault.kind)
                 for fault in unsupported(self.save, (5, 63, 5), (11, 67, 9))]
        self.assertEqual(torch, [((7, 65, 7), "no footing")])
        self.b.furnish(Piece.seat((0, 0), back="east", stair=OAK_STAIRS
                                  ).at(8, GROUND, 7),
                       room=room, doorstep=doorstep)
        self.assertEqual(self.name(8, 64, 7), OAK_STAIRS)
        self.assertEqual([(fault.cell, fault.kind) for fault in
                          unsupported(self.save, (5, 63, 5), (11, 67, 9))], torch)
        # A fault the plan makes is still refused beside it.
        with self.assertRaises(Refused):
            self.b.furnish(Piece({(0, 1, 0): "minecraft:wall_torch[facing=south]"}
                                 ).at(9, GROUND, 7), room=room, doorstep=doorstep)

    def test_a_covered_doorstep_is_refused_and_a_missing_one_is_an_error(self):
        room, _ = self.walled()
        inside = (8, GROUND, 8)
        before = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.furnish([Piece.stack((0, 0), block=SHELF, name="shelf"
                                        ).at(8, GROUND, 8)],
                           room=room, doorstep=inside)
        self.assertIn("would be covered by piece 0 ('shelf')", str(caught.exception))
        self.assertEqual(self.b.blocks_set, before)
        with self.assertRaises(NBTError) as caught:
            self.b.furnish(Piece.stack((0, 0), block=SHELF).at(6, GROUND, 6),
                           room=room, doorstep=(8, 64, 8))
        self.assertIn("nothing to stand on", str(caught.exception))

    def test_furnish_walks_unless_told_not_to(self):
        """The walk is the point, so a room and a doorstep are required:
        leaving them out is an error naming ``walk=False``."""
        chair = Piece.seat((0, 0), back="north", stair=OAK_STAIRS)
        for call, words in (
                (dict(), "walk=False"),
                (dict(room=(5, 5, 11, 9)), "walk=False"),
                (dict(doorstep=(8, GROUND, 11)), "walk=False"),
                (dict(room=(5, 5, 11), doorstep=(8, GROUND, 11)),
                 "room is (x0, z0, x1, z1)"),
                (dict(room=(5, 5, 11, 9), doorstep=(8, GROUND)),
                 "three whole numbers"),
                (dict(walk="yes"), "True or False"),
                (dict(walk=False, overwrite=1), "True or False")):
            with self.subTest(call=call):
                with self.assertRaises(NBTError) as caught:
                    self.b.furnish(chair.at(6, GROUND, 6), **call)
                self.assertIn(words, str(caught.exception))
        for pieces, words in (([], "at least one"), ([chair], "not placed yet"),
                              (["minecraft:oak_stairs"], "takes Pieces"),
                              (7, "takes Pieces")):
            with self.subTest(pieces=pieces):
                with self.assertRaises(NBTError) as caught:
                    self.b.furnish(pieces, walk=False)
                self.assertIn(words, str(caught.exception))
        self.assertEqual(self.b.blocks_set, 0)
        self.b.furnish(chair.at(6, GROUND, 6), walk=False)
        self.assertEqual(self.name(6, 64, 6), OAK_STAIRS)

    def test_a_flights_claim_is_refused_whatever_overwrite_says(self):
        """A piece in a flight's head room is a stair you climb and cannot
        come back down, whatever ``overwrite`` says."""
        self.b.shell(4, 4, 14, 12, Facade(STONE), floor_y=GROUND, top=73,
                     stories=(68,), floor=PLANKS)
        self.b.flight(StraightStair((8, 64, 10), "north", 5), floor_y=68,
                      walk=False)
        self.assertIn((8, 65, 10), self.b._claimed)
        cupboard = Piece.stack((0, 0), block="minecraft:barrel", dy=1)
        before = self.b.blocks_set
        for overwrite in (False, True):
            with self.assertRaises(Refused) as caught:
                self.b.furnish(cupboard.at(8, GROUND, 10), walk=False,
                               overwrite=overwrite)
            self.assertIn("claimed by a flight", caught.exception.faults[0])
        self.assertEqual(self.b.blocks_set, before)

    def test_both_halves_of_a_placement_answer_to_a_claim(self):
        """A door whose lower half stands clear of a flight and whose upper
        half lands in its head room is refused whatever ``overwrite`` says,
        and the fault names the half that lands there."""
        self.b.shell(4, 4, 14, 12, Facade(STONE), floor_y=GROUND, top=73,
                     stories=(68,), floor=PLANKS)
        self.b.flight(StraightStair((8, 64, 10), "north", 5), floor_y=68,
                      walk=False)
        self.assertNotIn((8, 64, 8), self.b._claimed)
        self.assertIn((8, 65, 8), self.b._claimed)
        standing = str(self.save.get_block(8, 65, 8))
        door = Piece({(0, 0, 0): "minecraft:oak_door[facing=east]"}, name="door")
        before = self.b.blocks_set
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                with self.assertRaises(Refused) as caught:
                    self.b.furnish(door.at(8, GROUND, 8), walk=False,
                                   overwrite=overwrite)
                (fault,) = caught.exception.faults
                self.assertTrue(fault.startswith(
                    "(8, 65, 8) is claimed by a flight this session built, "
                    "where piece 0 ('door') would put "
                    "minecraft:oak_door[facing=east,half=upper]"), fault)
                self.assertNotIn("overwrite=True", str(caught.exception),
                                 "no overwrite passes a claim")
        self.assertEqual(self.b.blocks_set, before)
        self.assertEqual(str(self.save.get_block(8, 65, 8)), standing)
        self.assertEqual(self.name(8, 64, 8), AIR)

    def test_a_lone_half_is_made_whole_not_left(self):
        """A placement is already standing only when every cell of it is: a
        bed laid over a lone foot, or a door over a lone lower half, writes
        the half that was missing, and the game's lone half is gone."""
        for cell, block, other in (
                ((10, 64, 10), "minecraft:red_bed[facing=south,part=foot]",
                 (10, 64, 11)),
                ((14, 64, 10), "minecraft:oak_door[facing=south,half=lower]",
                 (14, 65, 10))):
            self.save.set_block(*cell, BlockState.parse(block))
        self.assertEqual([(fault.cell, fault.kind) for fault in
                          unsupported(self.save, (8, 62, 8), (16, 67, 13))],
                         [((10, 64, 10), "lone half"), ((14, 64, 10), "lone half")])
        result = self.b.furnish(
            [Piece.bed((0, 0), (0, 1), bed="minecraft:red_bed").at(10, GROUND, 10),
             Piece({(0, 0, 0): "minecraft:oak_door[facing=south]"}).at(14, GROUND, 10)],
            walk=False)
        self.assertEqual((result.written, result.already), (4, 0))
        self.assertEqual(str(self.save.get_block(10, 64, 11)),
                         "minecraft:red_bed[facing=south,part=head]")
        self.assertEqual(self.save.get_block(14, 65, 10).properties["half"], "upper")
        self.assertEqual(unsupported(self.save, (8, 62, 8), (16, 67, 13)), [])

    def test_furniture_already_standing_is_left_as_it_stands(self):
        """Furnished again, a room writes nothing: each placement already
        holds what it would put there, apart from what the block updates
        derived -- the joined fence legs, the stair's shape."""
        room, doorstep = self.walled()
        pieces = [Piece.table((0, 0), (1, 0), leg="minecraft:oak_fence",
                              top="minecraft:oak_pressure_plate").at(7, GROUND, 7),
                  Piece.seat((0, 0), back="south", stair=OAK_STAIRS).at(7, GROUND, 6),
                  Piece.bed((0, 0), (0, 1), bed="minecraft:red_bed").at(10, GROUND, 6)]
        first = self.b.furnish(pieces, room=room, doorstep=doorstep)
        self.assertEqual((first.written, first.already), (7, 0))
        self.b.finish()
        count = self.b.blocks_set
        again = self.b.furnish(pieces, room=room, doorstep=doorstep)
        self.assertEqual((again.written, again.already), (0, 7))
        self.assertEqual(self.b.blocks_set, count)

    def test_legs_and_seats_take_what_their_neighbors_give(self):
        """Every cell is placed, as an item is: two fence legs side by side
        join, and a leg beside a full block joins it -- what the scripts
        that set states wrote bare until finish() came round."""
        self.b.set(9, 64, 6, "minecraft:oak_planks")
        self.b.furnish(Piece.table((0, 0), (1, 0), leg="minecraft:oak_fence",
                                   top="minecraft:oak_pressure_plate"
                                   ).at(7, GROUND, 6), walk=False)
        west, east = self.save.get_block(7, 64, 6), self.save.get_block(8, 64, 6)
        self.assertEqual((west.properties["east"], west.properties["west"]),
                         ("true", "false"))
        self.assertEqual((east.properties["east"], east.properties["west"]),
                         ("true", "true"))

    def test_a_block_the_game_lacks_is_named_before_anything_is_read(self):
        reads = []
        read = self.b._read
        self.b._read = lambda cell: reads.append(cell) or read(cell)
        lacking = Piece({(0, 0, 0): "minecraft:quartz_wall",
                         (1, 0, 0): "minecraft:oak_bench"}).at(6, GROUND, 6)
        with self.assertRaises(IllegalBlock) as caught:
            self.b.furnish(lacking, walk=False)
        self.assertEqual(len(caught.exception.faults), 2)
        self.assertEqual(reads, [], "nothing was read")
        self.assertEqual(self.b.blocks_set, 0)
        modded = Builder(self.save, allow=("minecraft:oak_bench",))
        modded.furnish(Piece({(0, 0, 0): "minecraft:oak_bench"}).at(6, GROUND, 6),
                       walk=False)
        self.assertEqual(self.name(6, 64, 6), "minecraft:oak_bench")

    def test_an_ungenerated_chunk_refuses_it_whole(self):
        """A bed across the edge of the world that was generated: its head
        has nothing to be written into, so neither half is."""
        bed = Piece.bed((0, 0), (1, 0), bed="minecraft:red_bed").at(47, GROUND, 6)
        with self.assertRaises(Refused) as caught:
            self.b.furnish([Piece.stack((0, 0), block=SHELF).at(40, GROUND, 6),
                            bed], walk=False)
        self.assertIn("chunk (3, 0) is not generated", caught.exception.faults)
        self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(self.name(40, 64, 6), AIR)
        self.assertEqual(self.name(47, 64, 6), AIR)

    def test_identical_rooms_encode_identically(self):
        """The order a sweep writes is its palette's, and so the chunk's
        bytes: the same room furnished here and in two fresh interpreters
        with other string-hash seeds is the same bytes, so nothing between
        the pieces and the write goes through set order."""
        directory = tempfile.mkdtemp(prefix="furniture-tests-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        _furnished_room(directory)
        digests = [_region_digests(directory)]
        code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                "from tests.test_furniture import _furnished_room; "
                "_furnished_room(sys.argv[2])")
        for seed in ("0", "12345"):
            elsewhere = tempfile.mkdtemp(prefix="furniture-tests-")
            self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
            subprocess.run([sys.executable, "-c", code, _ROOT, elsewhere],
                           cwd=_ROOT, env=dict(os.environ, PYTHONHASHSEED=seed),
                           capture_output=True, text=True, check=True)
            digests.append(_region_digests(elsewhere))
        self.assertEqual(digests[1], digests[0], "PYTHONHASHSEED=0")
        self.assertEqual(digests[2], digests[0], "PYTHONHASHSEED=12345")

    def test_furnish_inside_a_shell_walks_both_ways(self):
        """A shell with an upper story, its door and its flight, furnished
        on both floors: in at the door, round the ground floor, up the
        flight past the furniture, round the bedroom, and back out -- the
        round trip, from the doorstep outside, with nothing stranded."""
        house = self.b.shell(4, 4, 14, 12, Facade(STONE), floor_y=GROUND,
                             top=73, stories=(68,), floor=PLANKS,
                             deck="minecraft:granite")
        self.b.doorway(*house.walls["south"].door(9))
        self.b.flight(StraightStair((8, 64, 10), "north", 5), floor_y=68)
        doorstep = (9, GROUND, 13)
        downstairs = [
            Piece.table((0, 0), (1, 1), top="minecraft:stripped_oak_wood[axis=x]",
                        cloth="minecraft:white_carpet").at(11, GROUND, 6),
            Piece.seat((0, 0), (0, 1), back="west", stair=OAK_STAIRS).at(10, GROUND, 6),
            Piece.seat((0, 0), (0, 1), back="east", stair=OAK_STAIRS).at(13, GROUND, 6),
            Piece.stack((0, 0), (0, 2), block=SHELF, height=3).at(5, GROUND, 5),
        ]
        upstairs = [
            Piece.bed((0, 0), (0, -1), bed="minecraft:blue_bed").at(12, 68, 6),
            Piece.lamp(lamp="minecraft:lantern",
                       post="minecraft:stripped_oak_wood").at(13, 68, 5),
            Piece.stack((0, 0), (0, 1), block="minecraft:barrel", height=2
                        ).at(5, 68, 10),
            Piece.stack((0, 0), (2, 2), block="minecraft:blue_carpet").at(10, 68, 9),
        ]
        result = self.b.furnish(downstairs + upstairs, room=(5, 5, 13, 11),
                                doorstep=doorstep)
        self.assertFalse(result.reach.stranded)
        walk = reachable(self.save, doorstep, (3, 60, 3), (15, 76, 15))
        self.assertFalse(walk.stranded)
        upstairs_reached = {(x, z) for x, y, z in walk.reached if y == 68}
        self.assertIn((5, 5), upstairs_reached, "the bedroom's far corner")
        self.assertIn((13, 11), upstairs_reached)
        self.assertIn((5, 11), {(x, z) for x, y, z in walk.reached if y == GROUND})
        # Cupboards round the head of the flight leave the bedroom to
        # whoever jumps up out of the stairwell's side: every cell of the
        # upper floor but the top tread you arrive on, and what stands on
        # its furniture, now needs a jump. Nothing downstairs is lost.
        barrel = "minecraft:barrel"
        cupboards = Piece({(dx, dy, dz): barrel for dx, dz in ((1, 0), (0, 1), (2, 1))
                           for dy in (0, 1)}, name="cupboards").at(7, 68, 5)
        before = self.b.blocks_set
        with self.assertRaises(Refused) as caught:
            self.b.furnish(cupboards, room=(5, 5, 13, 11), doorstep=doorstep)
        lost = caught.exception.faults
        self.assertIn("(5, 68, 5) now needs a jump", lost)
        self.assertIn("(13, 68, 11) now needs a jump", lost)
        self.assertNotIn("(8, 68, 6) now needs a jump", lost, "the top tread")
        self.assertTrue(all(fault.endswith("now needs a jump")
                            and int(fault.split(", ")[1]) >= 68
                            for fault in lost), lost)
        self.assertEqual(self.b.blocks_set, before)


# --------------------------------------------------------------------------
# A room from each vocabulary that came before
# --------------------------------------------------------------------------

#: Where a Roman fountain's spout runs: the four cells beside the source on
#: its pillar, and the four under them it falls through into the basin.
#: JeffWorld's fountain at (350, 350), round_fountain(b, 350, 350, 3, y=63),
#: holds its water exactly with these as its spill (read 2026-10-22).
SPOUT = [(dx, dy, dz) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
         for dy in (1, 2)]


def _recorded(build) -> dict:
    """The cells ``build`` writes into a recorder, as a piece's blocks."""
    recorder = _Recorder()
    build(recorder)
    return dict(recorder.cells)


class FurnishWaterTests(FurnishBase):
    """Water in furniture: a tub's, a fountain's -- held by the one water
    check, the piece's own spill the only way out it may take."""

    def tub(self, *, gap=None):
        """The suburban house's bathtub two long
        (``tools/suburban_house/furniture.py``, ``bathtub``), recorded off
        its own function on a floor at y=-1, so its water and rim stand at
        the piece's dy=0; ``gap`` a rim cell left out."""
        f, _ = _suburban()
        cells = _recorded(lambda r: f.bathtub(r, -1, 0, 0, 1, 0))
        if gap is not None:
            del cells[gap]
        return Piece(cells, name="tub")

    def fountain(self, *, spill=SPOUT, gap=None):
        """The Roman town's round fountain, three across its basin
        (``tools/roman_city/common.py``, ``round_fountain``), recorded off
        its own function with its basin course at dy=0 -- the course the
        script lays flush with the ground, here on the floor; ``gap`` a
        cell of its marble curb left out."""
        cells = _recorded(lambda r: ROMAN.round_fountain(r, 0, 0, 3, y=0))
        if gap is not None:
            del cells[gap]
        return Piece(cells, name="fountain", spill=spill)

    def water_cells(self, piece) -> dict:
        return {cell: self.save.get_block(*cell)
                for cell, state in piece.cells().items()
                if state.name == "minecraft:water"}

    def test_a_tub_keeps_its_water(self):
        """Water walled by smooth quartz on the room's stone floor is
        written and held: the water check over its own cells finds no way
        out, as it finds none for the suburban house's two tubs in
        JeffWorld (read 2026-10-22). The walk takes the floor under its
        water for furnished, as it takes the floor under a piece."""
        room, doorstep = self.walled()
        tub = self.tub().at(7, GROUND, 7)
        result = self.b.furnish(tub, room=room, doorstep=doorstep)
        self.assertEqual(result.written, len(tub.cells()))
        water = self.water_cells(tub)
        self.assertEqual(sorted(water), [(7, 64, 7), (8, 64, 7)])
        self.assertEqual({str(state) for state in water.values()},
                         {"minecraft:water[level=0]"},
                         "the tub's water is in the world")
        from heaton_voxel.craft.survey import contained
        held = contained(self.save, water)
        self.assertEqual((held.leaks, held.standing), ([], []))

    def test_a_tub_with_a_gap_is_refused(self):
        """A rim cell left out lets the tub's water into the room: refused,
        the cell it would spread into named as a Leak from the water beside
        it, nothing written -- and the refusal offers no seal, since
        furniture keeps its own water in."""
        from heaton_voxel.craft.survey import Leak
        room, doorstep = self.walled()
        before = self.b.blocks_set
        tub = self.tub(gap=(2, 0, 0)).at(7, GROUND, 7)
        with self.assertRaises(Refused) as caught:
            self.b.furnish(tub, room=room, doorstep=doorstep)
        (leak,) = caught.exception.faults
        self.assertIsInstance(leak, Leak)
        self.assertEqual((leak.cell, leak.source, leak.kind),
                         ((9, 64, 7), (8, 64, 7), "spreads"))
        message = str(caught.exception)
        self.assertIn("furnish would let water out", message)
        self.assertIn("the piece's spill=", message)
        self.assertNotIn("seal=", message)
        self.assertEqual(self.b.blocks_set, before)
        self.assertEqual(self.name(8, 64, 7), AIR)

    def test_a_fountain_spout_runs_only_where_its_piece_allows(self):
        """The Roman fountain's spout stands a source on a pillar over its
        basin: with no spill its water spreads off the pillar's top, with
        only the cells beside the spout it falls from them into the air
        over the basin, and a cell of its curb left out lets the basin into
        the room; whole, with the eight cells the JeffWorld fountain's
        water takes, it is held, furnished and walked round."""
        room, doorstep = self.walled(2, 2, 14, 14)
        for spill, kind, ys in (((), "spreads", {66}),
                                ([c for c in SPOUT if c[1] == 2], "falls",
                                 {65})):
            with self.subTest(spill=spill):
                piece = self.fountain(spill=spill).at(8, GROUND, 8)
                with self.assertRaises(Refused) as caught:
                    self.b.furnish(piece, room=room, doorstep=doorstep)
                faults = caught.exception.faults
                self.assertEqual(len(faults), 4)
                self.assertEqual({leak.kind for leak in faults}, {kind})
                self.assertEqual({leak.cell[1] for leak in faults}, ys)
        self.assertEqual(self.name(8, 66, 8), AIR, "nothing was written")
        curb = (3, 0, 0)
        self.assertEqual(self.fountain().blocks[curb].name,
                         "minecraft:smooth_quartz")
        with self.assertRaises(Refused) as caught:
            self.b.furnish(self.fountain(gap=curb).at(8, GROUND, 8),
                           room=room, doorstep=doorstep)
        self.assertEqual([(leak.cell, leak.source) for leak
                          in caught.exception.faults],
                         [((11, 64, 8), (10, 64, 8))])
        piece = self.fountain().at(8, GROUND, 8)
        self.b.furnish(piece, room=room, doorstep=doorstep)
        self.assertEqual(self.name(8, 66, 8), "minecraft:water")
        from heaton_voxel.craft.survey import contained
        water = self.water_cells(piece)
        held = contained(self.save, water, spill=piece.spill_cells())
        self.assertEqual((held.leaks, held.standing), ([], []))
        self.assertEqual(len(contained(self.save, water).standing), 4,
                         "the spout runs, where its piece lets it")

    def test_furniture_keeps_clear_of_water_it_did_not_bring(self):
        """A piece placed over a block that walls somebody's water in --
        a carpet over the quartz holding a pool, overwrite=True -- opens
        the way out, and is refused, told to keep clear of that water."""
        room, doorstep = self.walled()
        self.b.set(7, 64, 7, "minecraft:water[level=0]")
        for x, z in ((6, 7), (8, 7), (7, 6), (7, 8)):
            self.b.set(x, 64, z, "minecraft:smooth_quartz")
        rug = Piece.stack((0, 0), block="minecraft:red_carpet").at(8, GROUND, 7)
        with self.assertRaises(Refused) as caught:
            self.b.furnish(rug, room=room, doorstep=doorstep, overwrite=True)
        message = str(caught.exception)
        self.assertIn("Keep the plan clear of the water beside it", message)
        self.assertNotIn("seal=", message)
        self.assertEqual(self.name(8, 64, 7), "minecraft:smooth_quartz")


class RoomTests(FurnishBase):
    def test_the_suburban_dining_room(self):
        """``plan._dining`` over ``furniture.py``, run as the oracle: a
        table with a cloth, a chair at every place with its back to the
        room's walls, a sideboard of barrels under a quartz top, and a
        fern. The house stands at x=-291..-278, z=-2..4 on a floor block at
        y=64; here it is moved by (+300, -1, +10), inside walls of its own,
        and walked from its door."""
        _, plan = _suburban()
        recorder = _Recorder((300, -1, 10))
        plan._dining(recorder)
        house = self.b.shell(8, 7, 23, 15, Facade("minecraft:white_concrete"),
                             floor_y=GROUND, top=68,
                             deck="minecraft:stripped_oak_wood[axis=x]",
                             openings=[("south", Opening.rect(1, 2), 20)])
        self.b.doorway(*house.walls["south"].door(20))

        def at(x, z):              # the house's own floor block, moved
            return x + 300, GROUND, z + 10

        chair = {back: Piece.seat((0, 0), back=back, stair=OAK_STAIRS, name="chair")
                 for back in ("north", "south", "east", "west")}
        pieces = [Piece.table((0, 0), (1, 3),
                              top="minecraft:stripped_oak_wood[axis=x]",
                              cloth="minecraft:white_carpet").at(*at(-286, 0))]
        for z in range(4):
            pieces.append(chair["west"].at(*at(-287, z)))
            pieces.append(chair["east"].at(*at(-284, z)))
        pieces.append(chair["north"].at(*at(-286, -1)))
        pieces.append(chair["south"].at(*at(-285, 4)))
        pieces.append(Piece.table(
            (0, 0), (2, 0), leg="minecraft:barrel[facing=south,open=false]",
            top="minecraft:smooth_quartz_slab[type=top,waterlogged=false]",
            name="sideboard").at(*at(-282, -2)))
        pieces.append(Piece.stack((0, 0), block="minecraft:potted_fern").at(*at(-278, -2)))
        result = self.b.furnish(pieces, room=(9, 8, 22, 14), doorstep=(20, GROUND, 16))
        self.assertEqual(result.blocks, len(recorder.cells))
        self.assertFalse(result.reach.stranded)
        self.assertRoom(self.save, recorder.cells, _box((9, 64, 8), (22, 68, 14)))

    def test_a_yellow_tower_flat(self):
        """``apartment_s1`` from ``tools/yellow_tower/layouts.json`` as
        ``build_tower.plan_furniture`` stamps it -- each block at the floor
        slab plus its ``dy``, a bed by both its halves -- laid as one
        :class:`Piece`, ``dy - 1`` here since the layout's cell on the floor
        is ``dy=1``. The tower's ring of glass with its gold corners and
        glowstone columns is moved by (+160, 63 - floor, -270), the next
        floor's planks over it, entered by the tower's own west door."""
        with open(os.path.join(_ROOT, "tools", "yellow_tower", "layouts.json")) as handle:
            layout = json.load(handle)["apartment_s1"]
        house = self.b.shell(8, 9, 22, 21, Facade("minecraft:yellow_stained_glass",
                                                  corner="minecraft:raw_gold_block"),
                             floor_y=GROUND, top=69, deck="minecraft:birch_planks",
                             openings=[("west", Opening.rect(1, 2), 11)])
        self.assertEqual(house.walls["west"].at, 8)
        columns = {(9, 10), (21, 10), (9, 20), (21, 20)}
        for x, z in sorted(columns):
            self.b.fill((x, 64, z), (x, 69, z), "minecraft:glowstone")
        reference, blocks = {}, {}
        for item in layout["blocks"]:
            state = BlockState("minecraft:" + item["block"].removeprefix("minecraft:"),
                               {key: str(value) for key, value
                                in (item.get("props") or {}).items()})
            reference[(item["x"] + 160, GROUND + item["dy"], item["z"] - 270)] = state
            blocks[(item["x"] + 151, item["dy"] - 1, item["z"] - 280)] = state
        flat = Piece(blocks, name="apartment_s1").at(9, GROUND, 10)
        self.assertEqual(len(flat.placements()), len(blocks) - 1,
                         "the bed's two halves are one placement")
        result = self.b.furnish(flat, room=(9, 10, 21, 20), doorstep=(7, GROUND, 11))
        self.assertFalse(result.reach.stranded)
        self.assertEqual(result.blocks, len(reference))
        self.assertRoom(self.save, reference,
                        _box((9, 64, 10), (21, 69, 20), skip=columns))

    def test_the_roman_triclinium_and_cubicula(self):
        """``jw_districts.domus``'s furnishings over ``common.py``: two
        dining couches -- a stair headrest and two cushions, the second
        turned to run east, here by a frame -- the fence-and-slab table
        between them, two beds, two candelabra, an amphora and a brazier.
        The domus's ``y`` is the feet cell, the floor block's here plus
        one."""
        C = ROMAN
        x0, z0, x1, z1, base = 6, 4, 26, 30, GROUND
        az, midx = z1 - 8, (x0 + x1) // 2
        recorder = _Recorder()
        # tools/roman_city/jw_districts.py, domus(): its furnishings.
        C.couch(recorder, x0 + 2, az - 2, base + 1, "south")
        C.couch(recorder, x0 + 4, az + 2, base + 1, "east")
        C.table(recorder, x0 + 3, az, base + 1)
        recorder.place(x1 - 2, base + 1, az - 2, "minecraft:red_bed[facing=north]")
        recorder.place(x1 - 2, base + 1, az + 2, "minecraft:white_bed[facing=north]")
        for lx in (x0 + 2, x1 - 2):
            C.candelabrum(recorder, lx, z1 - 2, base + 1)
        C.amphora(recorder, midx - 2, z1 - 2, base + 1)
        C.brazier(recorder, midx + 3, az, base + 1)

        house = self.b.shell(x0, z0, x1, z1, Facade("minecraft:bricks"),
                             floor_y=base, top=base + 5,
                             openings=[("south", Opening.rect(1, 2), midx)])
        self.b.doorway(*house.walls["south"].door(midx), door="minecraft:spruce_door")
        couch = Piece({(0, 0, 0): "minecraft:spruce_stairs[facing=north,half=bottom]",
                       (0, 0, 1): "minecraft:red_wool",
                       (0, 0, 2): "minecraft:red_wool"}, name="couch")
        candelabrum = Piece.lamp(lamp="minecraft:torch", post="minecraft:sandstone_wall",
                                 name="candelabrum")
        pieces = [
            couch.at(x0 + 2, base, az - 2),
            couch.turned(Frame((x0 + 4, base, az + 2), "east")),
            Piece.table((0, 0), leg="minecraft:spruce_fence",
                        top="minecraft:spruce_slab[type=bottom]").at(x0 + 3, base, az),
            Piece.bed((0, 0), (0, -1), bed="minecraft:red_bed").at(x1 - 2, base, az - 2),
            Piece.bed((0, 0), (0, -1), bed="minecraft:white_bed").at(x1 - 2, base, az + 2),
            candelabrum.at(x0 + 2, base, z1 - 2),
            candelabrum.at(x1 - 2, base, z1 - 2),
            Piece.stack((0, 0), block="minecraft:decorated_pot[facing=north]"
                        ).at(midx - 2, base, z1 - 2),
            Piece.stack((0, 0), block="minecraft:campfire[lit=true,facing=north]"
                        ).at(midx + 3, base, az),
        ]
        result = self.b.furnish(pieces, room=(x0 + 1, z0 + 1, x1 - 1, z1 - 1),
                                doorstep=(midx, base, z1 + 1))
        self.assertFalse(result.reach.stranded)
        self.assertEqual(result.blocks, len(recorder.cells))
        self.assertRoom(self.save, recorder.cells,
                        _box((x0 + 1, 64, z0 + 1), (x1 - 1, 68, z1 - 1)))
        # Placed, the table's fence joins the cushion beside it, as the
        # game would join it; the script's bare fence waited for finish().
        self.assertEqual(self.save.get_block(x0 + 3, 64, az).properties["west"], "true")


if __name__ == "__main__":
    unittest.main()
