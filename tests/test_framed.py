"""Builder.framed: a component written in its own coordinates, turned by a
Frame, through the Builder's own write path.

A framed write is the Builder's write at the cell and the state the frame
gives: the same bytes as writing it there by hand, the same refusals, the
same journal. And a Mix through a frame is still one field over the world,
picked at the world cell and the pick turned, so a component framed four
ways lays the same stones.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from heaton_voxel.block_defaults import default_properties  # noqa: E402
from heaton_voxel.blocks import BlockState  # noqa: E402
from heaton_voxel.craft import Frame, Framed, Material, Mix  # noqa: E402
from heaton_voxel.craft.build import Builder, _frame_op  # noqa: E402
from heaton_voxel.craft.build._common import _Mix  # noqa: E402
from heaton_voxel.craft.build.journal import Journal  # noqa: E402
from heaton_voxel.craft.errors import IllegalBlock, Refused  # noqa: E402
from heaton_voxel.nbt import NBTError  # noqa: E402
from tests.test_style import MixWriteBase  # noqa: E402
from tests.test_worksite import SyntheticSiteCase  # noqa: E402

STONE = "minecraft:stone"
STAIRS = "minecraft:oak_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]"
CORNER = ("minecraft:oak_stairs[facing=north,half=bottom,shape=inner_left,"
          "waterlogged=false]")
BED = "minecraft:red_bed[facing=south,occupied=false,part=foot]"
DOOR = "minecraft:oak_door[facing=east,half=lower,hinge=left,open=false,powered=false]"

#: A Mix whose entries face, so a turn shows in what it lays.
FACING_MIX = Mix([(STAIRS, 3),
                  ("minecraft:oak_stairs[facing=east,half=top,shape=straight,"
                   "waterlogged=false]", 2),
                  ("minecraft:cobblestone", 2)], seed=3)

#: All eight frames: four facings, each with either hand.
FRAMES = [Frame((18, 64, 20), facing, mirrored=mirrored)
          for facing in ("south", "west", "north", "east")
          for mirrored in (False, True)]


def spelled(block) -> BlockState:
    """``block`` with every property it leaves unsaid at its default: the
    state the game reads it as."""
    state = BlockState.parse(block) if isinstance(block, str) else block
    return BlockState(state.name, {**default_properties(state.name),
                                   **state.properties})


class ByHand:
    """A component written at the world cells a frame gives, as a script
    would write it with no Framed: each cell ``frame.cell``, each state
    ``frame.state``, and a Mix picked at the world cell and turned. The
    component spells out every property a turn moves, so ``frame.state``
    is the whole turn here; a state left at its defaults is held to the
    game's reading of it by
    ``test_a_property_left_at_its_default_turns_with_the_frame``."""

    def __init__(self, builder, frame):
        self.b, self.frame = builder, frame

    def set(self, across, up, out, block):
        cell = self.frame.cell(across, up, out)
        if isinstance(block, Mix):
            block = block.pick(*cell)
        self.b.set(*cell, self.frame.state(block))

    def place(self, across, up, out, block):
        self.b.place(*self.frame.cell(across, up, out), self.frame.state(block))

    def fill(self, corner1, corner2, block):
        low, high = self.frame.box(corner1, corner2)
        if not isinstance(block, Mix):
            self.b.fill(low, high, self.frame.state(block))
            return
        for x, y, z in MixWriteBase.box(low, high):
            self.b.set(x, y, z, self.frame.state(block.pick(x, y, z)))


def component(view) -> None:
    """A small lodge corner with something to turn in every write: a
    floor, a Mix wall of stairs that face, a stair corner, a bed (foot and
    head), a door (both halves, a hinge), a slab run and a chest half."""
    view.fill((0, 0, 0), (4, 0, 3), "minecraft:stone_bricks")
    view.fill((0, 1, 0), (0, 3, 3), FACING_MIX)
    view.set(2, 1, 1, CORNER)
    view.set(3, 1, 1, FACING_MIX)
    view.place(1, 1, 2, BED)
    view.place(3, 1, 3, DOOR)
    view.set(4, 1, 0, "minecraft:oak_fence")
    view.fill((1, 2, 0), (3, 2, 0),
              "minecraft:smooth_stone_slab[type=top,waterlogged=false]")
    view.set(2, 3, 2, "minecraft:chest[facing=west,type=left,waterlogged=false]")


class FramedTests(MixWriteBase):
    def test_framed_writes_are_the_build_written_by_hand(self):
        """In all eight frames, the component through b.framed(frame) and
        the same component written by hand at frame.cell with frame.state
        journal the same cells, count the same writes, and -- settled by
        finish() and saved -- are the same bytes."""
        for frame in FRAMES:
            with self.subTest(frame=frame):
                framed, save = self.world()
                b = Builder(save, seed=5)
                view = b.framed(frame)
                self.assertIsInstance(view, Framed)
                self.assertIs(view.builder, b)
                self.assertIs(view.frame, frame)
                component(view)
                b.finish()
                save.save()
                by_hand, other = self.world()
                hand = Builder(other, seed=5)
                component(ByHand(hand, frame))
                hand.finish()
                other.save()
                self.assertEqual(list(b.journal.written()),
                                 list(hand.journal.written()))
                self.assertEqual(b.blocks_set, hand.blocks_set)
                self.assertSameBytes(framed, by_hand, repr(frame))

    def test_a_framed_component_lands_turned(self):
        """Where each write lands, and how it faces: the stair corner at
        its turned cell with its turned facing and hand, the bed's head
        beside its foot where the turned bed points."""
        for frame in FRAMES:
            with self.subTest(frame=frame):
                _, save = self.world()
                view = Builder(save).framed(frame)
                view.set(2, 1, 1, CORNER)
                view.place(1, 1, 2, BED)
                corner = save.get_block(*frame.cell(2, 1, 1))
                self.assertEqual(corner, frame.state(CORNER))
                self.assertEqual(corner.properties["facing"],
                                 frame.heading("north"))
                hand = "inner_right" if frame.mirrored else "inner_left"
                self.assertEqual(corner.properties["shape"], hand)
                head = save.get_block(*frame.cell(1, 1, 3))
                self.assertEqual(head.name, "minecraft:red_bed")
                self.assertEqual(head.properties["part"], "head")
                self.assertEqual(head.properties["facing"],
                                 frame.heading("south"))

    def test_a_style_mix_through_a_frame_is_picked_at_the_world_cell(self):
        """Each cell a framed Mix writes is the Mix's pick at that world
        cell, turned: so frames that put the same world cells under one
        Mix lay the same entries there however each turns them -- one
        field over the world -- which a pick at the local cell would not."""
        low, high = (16, 64, 16), (22, 66, 22)
        cells = self.box(low, high)
        unturned = {}
        for frame in FRAMES:
            with self.subTest(frame=frame):
                _, save = self.world()
                view = Builder(save).framed(frame)
                view.fill(frame.local(*low), frame.local(*high), FACING_MIX)
                for cell in cells:
                    found = save.get_block(*cell)
                    self.assertEqual(found, frame.state(FACING_MIX.pick(*cell)),
                                     cell)
                    unturned.setdefault(cell, set()).add(str(frame.unstate(found)))
                view.set(*frame.local(30, 64, 30), FACING_MIX)
                self.assertEqual(save.get_block(30, 64, 30),
                                 frame.state(FACING_MIX.pick(30, 64, 30)))
        self.assertTrue(all(len(found) == 1 for found in unturned.values()),
                        "every frame lays the same entry in each world cell")
        frame = FRAMES[2]
        self.assertTrue(any(FACING_MIX.pick(*frame.local(*cell))
                            != FACING_MIX.pick(*cell) for cell in cells),
                        "a local pick would differ: the test can tell")

    def test_a_property_left_at_its_default_turns_with_the_frame(self):
        """A bare or partial state is its block with the rest at the
        defaults -- a bare stair faces north, a bare chest and a bare rail
        lie north to south -- and the frame turns those as well as what
        the state names, in all eight frames: the game reads what lands as
        the spelled-out state, turned. Only a default the turn moves is
        written, so a bare fence or log stays bare; and get reads a bare
        state back the same way."""
        blocks = ["minecraft:oak_stairs", "minecraft:oak_stairs[half=top]",
                  "minecraft:chest", "minecraft:rail",
                  "minecraft:oak_fence[north=true]", "minecraft:wall_torch",
                  "minecraft:oak_fence", "minecraft:oak_log"]
        for frame in FRAMES:
            with self.subTest(frame=frame):
                _, save = self.world()
                view = Builder(save).framed(frame)
                for across, block in enumerate(blocks):
                    view.set(across, 0, 0, block)
                for across, block in enumerate(blocks):
                    found = save.get_block(*frame.cell(across, 0, 0))
                    self.assertEqual(spelled(found),
                                     spelled(frame.state(spelled(block))), block)
                    self.assertEqual(spelled(view.get(across, 0, 0)),
                                     spelled(block), block)
                stair = save.get_block(*frame.cell(0, 0, 0))
                self.assertEqual(spelled(stair).properties["facing"],
                                 frame.heading("north"))
                self.assertEqual(sorted(stair.properties),
                                 [] if frame.heading("north") == "north"
                                 else ["facing"], "only the default it moved")
                for across in (6, 7):
                    self.assertEqual(str(save.get_block(*frame.cell(across, 0, 0))),
                                     blocks[across], "no turn moves it")
                save.set_block(*frame.cell(0, 0, 1), "minecraft:oak_stairs")
                self.assertEqual(spelled(view.get(0, 0, 1)),
                                 spelled(frame.unstate(spelled("minecraft:oak_stairs"))),
                                 "a bare state stored in the world, read back")

    def test_a_bare_wall_torch_hangs_on_the_wall_the_frame_turns(self):
        """A wall torch left facing its default hangs on the block to its
        south; framed, wall and torch turn together, so finish() finds
        nothing the game would drop in any of the eight frames."""
        for frame in FRAMES:
            with self.subTest(frame=frame):
                _, save = self.world()
                b = Builder(save)
                view = b.framed(frame)
                view.set(0, 1, 1, "minecraft:stone_bricks")
                view.set(0, 1, 0, "minecraft:wall_torch")
                self.assertEqual(b.finish().unsupported, [])

    def test_a_door_left_at_its_default_hinge_changes_hands_in_a_mirror(self):
        """oak_door[facing=north,half=lower] says nothing of its hinge, so
        it is hinged left; placed through a mirrored frame both halves are
        hinged right, and unmirrored both stay left."""
        for frame in FRAMES:
            with self.subTest(frame=frame):
                _, save = self.world()
                view = Builder(save).framed(frame)
                view.place(0, 0, 0, "minecraft:oak_door[facing=north,half=lower]")
                hand = "right" if frame.mirrored else "left"
                for up, half in ((0, "lower"), (1, "upper")):
                    door = spelled(save.get_block(*frame.cell(0, up, 0)))
                    self.assertEqual(door.properties["half"], half)
                    self.assertEqual(door.properties["hinge"], hand, half)
                    self.assertEqual(door.properties["facing"],
                                     frame.heading("north"))

    def test_a_framed_placement_into_water_still_waterlogs(self):
        """What the frame spells out is only what it turns: a stair left
        unsaid about water, placed through a frame into a water source, is
        waterlogged there as an unframed placement is, and faces where the
        frame turns it."""
        for frame in FRAMES:
            with self.subTest(frame=frame):
                _, save = self.world()
                save.set_block(*frame.cell(0, 0, 0), "minecraft:water")
                Builder(save).framed(frame).place(0, 0, 0, "minecraft:oak_stairs")
                stair = spelled(save.get_block(*frame.cell(0, 0, 0)))
                self.assertEqual(stair.properties["waterlogged"], "true")
                self.assertEqual(stair.properties["facing"], frame.heading("north"))

    def test_a_framed_fill_is_one_box(self):
        """A framed fill of one block is the lower tier's one fill over the
        world box the frame turns its corners into, the state turned and
        journaled as one box -- never a sweep of single cells -- in all
        eight frames."""
        corner1, corner2 = (0, 0, 0), (3, 2, 1)
        for frame in FRAMES:
            with self.subTest(frame=frame):
                _, save = self.world()
                b = Builder(save)
                with mock.patch.object(save, "fill", wraps=save.fill) as filled, \
                        mock.patch.object(save, "set_block",
                                          wraps=save.set_block) as sets, \
                        mock.patch.object(Journal, "record_box", autospec=True,
                                          side_effect=Journal.record_box) as boxed, \
                        mock.patch.object(Journal, "record", autospec=True,
                                          side_effect=Journal.record) as cells:
                    b.framed(frame).fill(corner1, corner2, STAIRS)
                low, high = frame.box(corner1, corner2)
                (call,) = filled.call_args_list
                self.assertEqual(call.args[:2], (low, high))
                self.assertEqual(call.args[2], frame.state(STAIRS))
                sets.assert_not_called()
                cells.assert_not_called()
                (box,) = boxed.call_args_list
                self.assertEqual(box.args[1:3], (low, high))
                self.assertEqual(b.blocks_set, 24)

    def test_a_drawn_mix_is_turned_entry_by_entry(self):
        """A list mix a writer draws from the rng is turned entry by entry,
        weights kept: it draws the entry it would have drawn unframed, in
        the same order, and writes it turned -- byte for byte the same as
        writing a mix of the turned entries by hand."""
        drawn = _Mix([(STAIRS, 2), (STONE, 1), (CORNER, 1)])
        locals_ = [(a, 0, o) for a in range(3) for o in range(2)]
        identity, save = self.world()
        plain = Builder(save, seed=9)
        for cell in locals_:
            plain.set(*cell, drawn)
        draws = [save.get_block(*cell) for cell in locals_]
        self.assertGreater(len({str(state) for state in draws}), 1)
        for frame in FRAMES:
            with self.subTest(frame=frame):
                framed, save = self.world()
                b = Builder(save, seed=9)
                view = b.framed(frame)
                for cell in locals_:
                    view.set(*cell, drawn)
                for cell, state in zip(locals_, draws):
                    self.assertEqual(save.get_block(*frame.cell(*cell)),
                                     frame.state(state))
                save.save()
                by_hand, other = self.world()
                hand = Builder(other, seed=9)
                turned = _Mix([(frame.state(name), weight)
                               for name, weight in drawn.mix])
                for cell in locals_:
                    hand.set(*frame.cell(*cell), turned)
                other.save()
                self.assertEqual(b.rng.getstate(), hand.rng.getstate())
                self.assertSameBytes(framed, by_hand, repr(frame))

    def test_a_framed_write_is_refused_as_the_builders_are(self):
        """A block the game lacks, a chunk never generated, a height past
        the world: each refused before anything is written or drawn, in
        the Builder's own refusals, opened by the local cell and the
        frame; and what is not one block is the Builder's TypeError."""
        frame = Frame((40, 64, 20), "east", mirrored=True)
        cases = {
            "a renamed block": (lambda v: v.set(0, 0, 0, "minecraft:chain"),
                                IllegalBlock, "minecraft:chain"),
            "an ungenerated chunk": (lambda v: v.set(0, 0, 10, STONE),
                                     Refused, "is not generated"),
            "a fill into one": (lambda v: v.fill((0, 0, 0), (0, 0, 10), STONE),
                                Refused, "is not generated"),
            "past the world": (lambda v: v.set(0, 300, 0, STONE),
                               Refused, "past the world"),
        }
        for name, (call, error, words) in cases.items():
            with self.subTest(name):
                directory, save = self.world()
                b = Builder(save, seed=1)
                before = b.rng.getstate()
                with self.assertRaises(error) as caught:
                    call(b.framed(frame))
                self.assertIn(words, str(caught.exception))
                self.assertIn(repr(frame), str(caught.exception))
                self.assertEqual(b.blocks_set, 0)
                self.assertEqual(b.rng.getstate(), before)
                save.save()
                fresh, _ = self.world()
                self.assertSameBytes(directory, fresh, "nothing was written")
        directory, save = self.world()
        b = Builder(save)
        view = b.framed(frame)
        with self.assertRaises(IllegalBlock) as caught:
            view.set(0, 0, 0, "minecraft:chain[axis=x]")
        self.assertIn("minecraft:iron_chain", caught.exception.suggestions)
        self.assertIn("set at (0, 0, 0)", str(caught.exception))
        with self.assertRaises(IllegalBlock) as caught:
            view.set(0, 0, 0, "minecraft:oak_stairs[facing=north,half=middle]")
        self.assertIn("facing=north,half=middle", str(caught.exception),
                      "the state as the caller named it, not turned west")
        for call in (lambda: view.place(0, 0, 0, FACING_MIX),
                     lambda: view.set(0, 0, 0, Material("minecraft:bricks")),
                     lambda: view.set(0, 0, 0, [(STONE, 1)])):
            with self.assertRaises(TypeError):
                call()
        with self.assertRaises(NBTError):
            view.set(0.5, 0, 0, STONE)
        with self.assertRaises(NBTError):
            view.fill((0, 0), (1, 1, 1), STONE)
        with self.assertRaises(ValueError):
            view.set(0, 0, 0, "minecraft:stone[")
        self.assertEqual(b.blocks_set, 0)

    def test_a_state_the_frame_cannot_turn_is_refused(self):
        """Under allow=, a modded state with a direction the vocabulary
        does not know is refused by any frame that turns, never written
        pointing the old way; the identity frame writes it as it is."""
        _, save = self.world()
        b = Builder(save, allow=("create:",))
        with self.assertRaises(Refused) as caught:
            b.framed(Frame((20, 64, 20), "west")).set(0, 0, 0,
                                                      "create:gearbox[side=north]")
        self.assertNotIsInstance(caught.exception, IllegalBlock)
        self.assertEqual(b.blocks_set, 0)
        b.framed(Frame((20, 64, 20))).set(0, 0, 0, "create:gearbox[side=north]")
        self.assertEqual(str(save.get_block(20, 64, 20)), "create:gearbox[side=north]")

    def test_get_reads_in_the_frames_own_terms(self):
        for frame in FRAMES:
            with self.subTest(frame=frame):
                _, save = self.world()
                view = Builder(save).framed(frame)
                save.set_block(*frame.cell(1, 0, 2), frame.state(CORNER))
                self.assertEqual(view.get(1, 0, 2), BlockState.parse(CORNER))
                self.assertEqual(view.get(0, 0, 0).name, "minecraft:air")
                self.assertEqual(view.get(0, -1, 0).name, STONE)
        far = Builder(save).framed(Frame((200, 64, 200)))
        self.assertIsNone(far.get(0, 0, 0), "an ungenerated chunk")

    def test_a_frame_maps_every_plan_op(self):
        """_frame_op turns each kind of op a plan holds -- a set's and a
        placement's cell, a fill's corners to the world box, a ground op's
        column and height -- and refuses a paste, which a stamp turns, and
        an op of no kind; b.framed() takes a Frame and nothing else."""
        frame = Frame((10, 64, 20), "west")
        self.assertEqual(_frame_op(frame, ("set", (1, 2, 3), STONE)),
                         ("set", frame.cell(1, 2, 3), BlockState.parse(STONE)))
        kind, cell, state = _frame_op(frame, ("place", (0, 0, 0), BED))
        self.assertEqual((kind, cell, state.properties["facing"]),
                         ("place", (10, 64, 20), "west"))
        self.assertEqual(_frame_op(frame, ("fill", (0, 0, 0), (2, 1, 3), STONE))[:3],
                         ("fill", *frame.box((0, 0, 0), (2, 1, 3))))
        self.assertEqual(_frame_op(frame, ("ground", (2, 3), 5)),
                         ("ground", (10 - 3, 20 + 2), 69))
        self.assertEqual(_frame_op(frame, ("ground", (2, 3), None)),
                         ("ground", (7, 22), None))
        for op in (("paste", object()), ("carve", (0, 0, 0), STONE)):
            with self.assertRaises(NBTError):
                _frame_op(frame, op)
        _, save = self.world()
        for bad in ("west", (10, 64, 20), None):
            with self.assertRaises(NBTError):
                Builder(save).framed(bad)


class FramedGuardTests(SyntheticSiteCase):
    def test_a_guard_refuses_a_framed_write_into_a_zone(self):
        """The guard admits a framed write at its world cells: one landing
        in a protected zone is refused, naming it, and nothing moves."""
        site = self.open_site()
        site.protect((6, 64, 6), (6, 66, 6), name="shrine")
        b = site.builder()
        view = b.framed(Frame((8, 64, 6), "north"))
        with self.assertRaises(Refused) as caught:
            view.fill((0, 0, 0), (2, 2, 0), "minecraft:stone_bricks")
        self.assertIn("'shrine'", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(site.diff((0, 60, 0), (15, 70, 15)), {})
        view.set(-1, 0, 0, "minecraft:stone_bricks")      # (9, 64, 6), beside it
        self.assertEqual(b.blocks_set, 1)


if __name__ == "__main__":
    unittest.main()
