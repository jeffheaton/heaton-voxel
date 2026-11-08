"""Tests for Builder.grade, the one earthwork writer, and level_pad, which
is a grade.

A grade cuts the ground over its outline down to a target, fills it up to
it, and drains the water it stands over -- and refuses, before anything is
written, what the game would undo or what would undo the game's water: a
fill that starves a flow it fed, a cut that opens a pond's bank, a torch
left hanging on nothing. The two drains written by hand in ``tools/`` are
the evidence: each guessed the bed it filled from with a magic floor and
wrote dirt into the ground under it, and the lab's left water standing
beside the lawn it cut, sealed under the plaza ever since.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest

from heaton_voxel.blocks import BlockState
from heaton_voxel.craft import GradeResult, geometry
from heaton_voxel.craft.block_facts import holds_water
from heaton_voxel.craft.build import Builder
from heaton_voxel.craft.errors import NoSurface, Refused
from heaton_voxel.craft.style import FROM_PALETTE, Mix
from heaton_voxel.craft.survey import Unsupported
from heaton_voxel.nbt import NBTError
from heaton_voxel.world import MinecraftSave
from tests.test_world import make_save

CHUNKS = [(x, z) for x in range(-1, 2) for z in range(-1, 2)]
STONE = "minecraft:stone"
WATER = "minecraft:water[level=0]"
COBBLE = "minecraft:cobblestone"


class GradeBase(unittest.TestCase):
    """A flat world of stone to y=63, three chunks square, and helpers to
    make another like it."""

    def setUp(self):
        self.save = self.world()
        self.b = Builder(self.save, seed=7)

    def world(self, shape=None):
        directory = tempfile.mkdtemp(prefix="grade-tests-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        if shape in _SAVED:
            # Raised once, saved, and copied from: the same world, read
            # back from the bytes it saved to.
            shutil.rmtree(directory)
            shutil.copytree(_saved(shape), directory)
            save = MinecraftSave.load(directory)
        else:
            save = _flat_world(directory, shape)
        save.directory = directory
        return save

    def name(self, x, y, z, save=None):
        return (save or self.save).get_block(x, y, z).name

    def wet(self, x, y, z, save=None):
        block = (save or self.save).get_block(x, y, z)
        return holds_water(block.name, block.properties)

    def column(self, x, z, y0=40, y1=80, save=None):
        save = save or self.save
        return [str(save.get_block(x, y, z)) for y in range(y0, y1 + 1)]


def _flat_world(directory, shape=None):
    """The flat world :class:`GradeBase` builds on, in ``directory``,
    shaped by ``shape``, its heightmaps rebuilt, not saved."""
    make_save(directory, CHUNKS)
    save = MinecraftSave.load(directory)
    save.fill((-16, 40, -16), (31, 63, 31), STONE)
    if shape is not None:
        shape(save)
    save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))
    return save


def _saved(shape):
    """A saved world of ``shape``'s, raised once for the module and copied
    from by every :meth:`GradeBase.world` after: the hills take most of a
    second to raise, and two tests raise four worlds on them. The module's
    cleanup removes it and forgets it, so a run that comes back to this
    module after tearing it down raises it again."""
    if _SAVED[shape] is None:
        directory = tempfile.mkdtemp(prefix="grade-tests-saved-")
        unittest.addModuleCleanup(_forget, shape, directory)
        _flat_world(directory, shape).save()
        _SAVED[shape] = directory
    return _SAVED[shape]


def _forget(shape, directory):
    """Undo :func:`_saved`: remove ``shape``'s saved world and forget it."""
    _SAVED[shape] = None
    shutil.rmtree(directory, ignore_errors=True)


def hills(save):
    """Ground from 58 to 68 that never repeats a row, and a pond: water
    59..61 on a sand bed at 58, at x 20..25, z 20..25."""
    for x in range(-8, 30):
        for z in range(-8, 30):
            h = 58 + (x * 7 + z * 3 + (x * z) % 5) % 11
            if h < 63:
                save.fill((x, h + 1, z), (x, 63, z), "minecraft:air")
            else:
                save.fill((x, 64, z), (x, h, z), STONE)
            save.set_block(x, h, z, "minecraft:grass_block[snowy=false]")
    save.fill((19, 58, 19), (26, 61, 26), STONE)
    save.fill((19, 62, 19), (26, 75, 26), "minecraft:air")
    save.fill((19, 62, 19), (26, 62, 26), "minecraft:grass_block[snowy=false]")
    save.fill((20, 59, 20), (25, 75, 25), "minecraft:air")
    save.fill((20, 58, 20), (25, 58, 25), "minecraft:sand")
    save.fill((20, 59, 20), (25, 61, 25), WATER)


#: The shapes :meth:`GradeBase.world` raises once and copies after, each
#: mapped to its saved world once it has one.
_SAVED = {hills: None}


class GradeTests(GradeBase):

    def digests(self, save):
        save.save()
        found = {}
        for root, _, files in os.walk(save.directory):
            for name in files:
                if name.endswith(".mca"):
                    path = os.path.join(root, name)
                    with open(path, "rb") as handle:
                        found[os.path.relpath(path, save.directory)] = \
                            hashlib.sha256(handle.read()).hexdigest()
        return found

    def test_level_pad_is_a_grade(self):
        """level_pad is grade over its rectangle with both flags on and a
        name: on twin hilly worlds the two write the same bytes, journal
        the same cells, learn the same ground and register the same pads
        -- a Mix surface and ring, no headroom and ten of it, the pond
        bridged and the pond filled from its bed."""
        ring = Mix([("minecraft:stone_bricks", 3),
                    ("minecraft:mossy_stone_bricks", 1)], clump=2)
        lawn = Mix([("minecraft:moss_block", 1),
                    ("minecraft:grass_block[snowy=false]", 3)], seed=5)
        calls = [((0, 0, 8, 6), 63, dict(surface=lawn, wall=ring,
                                         clearance=0, name="yard")),
                 ((10, -6, 17, 3), 61, dict(clearance=10, name="court")),
                 ((18, 18, 23, 22), 63, dict(over_water="bridge",
                                             name="jetty")),
                 ((21, 23, 24, 25), 62, dict(over_water="fill",
                                             wall="minecraft:bricks",
                                             name="pier"))]
        padded = self.world(hills)
        graded = self.world(hills)
        a, g = Builder(padded, seed=3), Builder(graded, seed=3)
        for rect, top, kwargs in calls:
            with self.subTest(rect=rect):
                self.assertEqual(a.level_pad(*rect, top, **kwargs), rect)
                kwargs = dict(kwargs)
                retain = kwargs.pop("wall", FROM_PALETTE)
                result = g.grade(rect, top, retain=retain, **kwargs)
                self.assertIsInstance(result, GradeResult)
        self.assertEqual(list(a.journal.written()), list(g.journal.written()))
        self.assertEqual(a._ground, g._ground)
        self.assertEqual(a._pads, g._pads)
        self.assertEqual(a._registered, g._registered)
        self.assertEqual(self.digests(padded), self.digests(graded))
        # And the pond was built on, not drained: water under the jetty.
        self.assertTrue(self.wet(20, 61, 20, padded))
        self.assertEqual(self.name(21, 59, 24, padded), "minecraft:bricks")
        self.assertEqual(self.name(22, 59, 24, padded), "minecraft:dirt")

    def test_a_pad_clears_its_clearance_and_no_more(self):
        """``clearance`` blocks of air over the surface, and the block one
        past them left standing, whether the column was cut, surfaced or
        raised: here a canopy hangs into the headroom, which the reader
        never takes for the ground. The hills :class:`LevelPadBytesTests`
        pins have nothing over their pads to clear, so its bytes come out
        the same whatever height the clear reaches; this is what holds
        it."""
        leaves = "minecraft:oak_leaves[distance=7,persistent=true,waterlogged=false]"
        cases = [(61, 3), (61, 6), (63, 1), (63, 3), (63, 6), (65, 1),
                 (65, 3), (65, 6)]
        for writer in ("level_pad", "grade"):
            for target, clearance in cases:
                with self.subTest(writer=writer, target=target,
                                  clearance=clearance):
                    save = self.world()
                    save.fill((0, 65, 0), (4, 80, 4), leaves)
                    b = Builder(save, seed=7)
                    if writer == "level_pad":
                        b.level_pad(0, 0, 4, 4, target, clearance=clearance)
                    else:
                        b.grade((0, 0, 4, 4), target, clearance=clearance)
                    top = target + clearance
                    for x, z in ((0, 0), (2, 3), (4, 4)):
                        self.assertEqual(
                            [self.name(x, y, z, save) for y in
                             range(target, top + 2)],
                            ["minecraft:grass_block"]
                            + ["minecraft:air"] * clearance
                            + ["minecraft:oak_leaves"], (x, z))

    def flat_and_hill(self, save):
        """A hill three high at x 0..3 and a ditch three deep at x 8..11,
        z 0..3, on the flat at 63."""
        save.fill((0, 64, 0), (3, 66, 3), STONE)
        save.fill((8, 61, 0), (11, 63, 3), "minecraft:air")

    def test_cut_and_fill_say_which_dry_columns_are_taken(self):
        """``cut`` takes the columns above the target and ``fill`` those
        under it; a column already there is resurfaced by either. What the
        flags leave is left exactly as it was, and the result counts each
        column once."""
        rect = (0, 0, 11, 3)
        cases = {(True, True): (16, 16, 16, 0), (False, True): (0, 16, 16, 16),
                 (True, False): (16, 0, 16, 16), (False, False): (0, 0, 0, 48)}
        for (cut, fill), (cuts, raised, surfaced, left) in cases.items():
            with self.subTest(cut=cut, fill=fill):
                save = self.world(self.flat_and_hill)
                b = Builder(save)
                before = {(x, z): self.column(x, z, save=save)
                          for x in range(0, 12) for z in range(0, 4)}
                r = b.grade(rect, 63, cut=cut, fill=fill)
                self.assertEqual((r.columns, r.cut, r.raised, r.surfaced,
                                  r.drained, r.bridged, r.left),
                                 (48, cuts, raised, surfaced, 0, 0, left))
                for (x, z), column in before.items():
                    taken = (cut if x < 4 else fill if x >= 8 else cut or fill)
                    if taken:
                        self.assertEqual(self.name(x, 63, z, save),
                                         "minecraft:grass_block")
                        self.assertEqual(b.ground(x, z), 63)
                    else:
                        self.assertEqual(self.column(x, z, save=save), column,
                                         (x, z))
                if not (cut or fill):
                    self.assertEqual(b.blocks_set, 0)

    def test_a_non_rectangular_outline_rings_its_own_edge(self):
        """Any columns: a disc's edge -- every column with a side outside
        it -- stands on the retaining wall and its inside on dirt, each
        column taken once however often it is named. Only a rectangle is
        a pad, so a name with a disc is refused; so are an empty outline,
        a column that is no pair of ints, a target that is no whole height
        and no subsoil, before anything is read."""
        disc = geometry.disc(4, center=(8, 8))
        inside = set(disc)
        r = self.b.grade(list(disc) + list(disc[:5]), 66)
        self.assertEqual((r.columns, r.raised, r.retained),
                         (len(disc), len(disc), r.retained))
        edge = {(x, z) for x, z in disc
                if any(side not in inside for side in
                       ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)))}
        self.assertEqual(r.retained, len(edge))
        for x, z in disc:
            self.assertEqual(self.name(x, 64, z), "minecraft:stone_bricks"
                             if (x, z) in edge else "minecraft:dirt", (x, z))
            self.assertEqual(self.name(x, 66, z), "minecraft:grass_block")
        self.assertEqual(self.name(3, 66, 8), "minecraft:air", "outside")
        self.assertEqual(self.b._pads, {}, "no name, no pad")
        for outline, target, kwargs, error in (
                (disc, 66, dict(name="round"), NBTError),
                ((), 66, {}, NBTError),
                ([(1, 2), (1.5, 2)], 66, {}, NBTError),
                ((0, 0, 4, 4.0), 66, {}, NBTError),
                ((0, 0, 4, 4), 66.0, {}, NBTError),
                ((0, 0, 4, 4), True, {}, NBTError),
                ((0, 0, 4, 4), 66, dict(name=""), NBTError),
                ((0, 0, 4, 4), 66, dict(clearance=-1), NBTError),
                ((0, 0, 4, 4), 66, dict(drain=True, over_water="fill"),
                 ValueError),
                # No subsoil, whether or not a column needs one: on the
                # flat at 63 none does, and it is refused all the same.
                ((0, 0, 4, 4), 66, dict(subsoil=None), TypeError),
                ((0, 0, 4, 4), 63, dict(subsoil=None), TypeError)):
            with self.subTest(outline=outline, target=target, **kwargs):
                with self.assertRaises(error):
                    self.b.grade(outline, target, **kwargs)
        self.assertEqual(self.b.blocks_set, len(list(self.b.journal.written())))

    def test_level_pad_takes_whole_numbers(self):
        """A clearance or a height that is no whole number is refused,
        naming it, before anything is read: a clearance of ``2.0`` once
        cleared two and ``True`` one, which is a guess at what was meant."""
        for top, kwargs, words in ((63, dict(clearance=2.0), "got 2.0"),
                                   (63, dict(clearance=True), "got True"),
                                   (63.0, {}, "got 63.0")):
            with self.subTest(top=top, **kwargs):
                with self.assertRaises(NBTError) as caught:
                    self.b.level_pad(0, 0, 5, 5, top, **kwargs)
                self.assertIn(words, str(caught.exception))
                self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(self.b._pads, {})

    def test_wall_none_fills_to_the_edge(self):
        """No ring: ``level_pad(wall=None)`` and ``grade(retain=None)`` fill
        dirt to the edge, and are the same build."""
        other = self.world()
        self.b.level_pad(0, 0, 5, 5, 66, wall=None)
        r = Builder(other, seed=7).grade((0, 0, 5, 5), 66, retain=None)
        self.assertEqual(r.retained, 0)
        for x in range(0, 6):
            for z in range(0, 6):
                self.assertEqual(self.name(x, 64, z), "minecraft:dirt")
                self.assertEqual(self.column(x, z), self.column(x, z, save=other))

    def test_grade_audits_what_it_cuts(self):
        """A torch hung on the side of a hill the grade cuts away would
        drop at the first block update: the grade is refused, naming it,
        and nothing is written -- level_pad's cut included."""
        self.save.fill((0, 64, 0), (3, 66, 3), STONE)
        self.save.set_block(4, 65, 1, "minecraft:wall_torch[facing=east]")
        for call in (lambda: self.b.level_pad(0, 0, 3, 3, 63),
                     lambda: self.b.grade((0, 0, 3, 3), 63)):
            with self.assertRaises(Refused) as caught:
                call()
            fault, = caught.exception.faults
            self.assertIsInstance(fault, Unsupported)
            self.assertEqual(fault.cell, (4, 65, 1))
            self.assertEqual(self.b.blocks_set, 0)
            self.assertEqual(self.name(3, 65, 1), STONE)
        self.assertEqual(self.b._pads, {})


class DrainTests(GradeBase):

    def test_a_pond_inside_the_outline_drains_below_its_waterline(self):
        """A pond wholly inside the outline, its waterline at 62, drained to
        a target of 60: dirt from its bed up, the surface at 60 and air from
        there up through the waterline -- no water left anywhere -- and
        nothing under the bed rewritten. The dry ground round it is cut to
        the same plane."""
        def pond(save):
            save.fill((4, 63, 4), (7, 63, 7), "minecraft:air")
            save.fill((4, 59, 4), (7, 62, 7), WATER)
            save.fill((4, 58, 4), (7, 58, 7), "minecraft:gravel")
        save = self.world(pond)
        b = Builder(save)
        under = {(x, z): self.column(x, z, 40, 58, save)
                 for x in range(4, 8) for z in range(4, 8)}
        r = b.grade((2, 2, 9, 9), 60, drain=True)
        self.assertEqual((r.columns, r.drained, r.cut, r.left), (64, 16, 48, 0))
        self.assertEqual(r.beside_drained, ())
        for (x, z), column in under.items():
            self.assertEqual(self.column(x, z, 40, 58, save), column)
            self.assertEqual(self.name(x, 59, z, save), "minecraft:dirt")
            self.assertEqual(self.name(x, 60, z, save), "minecraft:grass_block")
            for y in range(61, 64):
                self.assertEqual(self.name(x, y, z, save), "minecraft:air")
        self.assertFalse(any(self.wet(x, y, z, save) for x in range(2, 10)
                             for z in range(2, 10) for y in range(55, 66)))
        # level_pad cannot drain: under the waterline it refuses, as ever,
        # and over water it was not told about, in the words it always
        # had -- neither offering a drain= it does not take.
        for kwargs in (dict(over_water="fill"), {}):
            with self.subTest(**kwargs):
                with self.assertRaises(Refused) as caught:
                    Builder(self.world(pond)).level_pad(2, 2, 9, 9, 60,
                                                        **kwargs)
                self.assertNotIn("drain", str(caught.exception))

    def stream(self, save):
        """A stream one wide at y=63, z=4: a source at x=2 and a flow
        falling off a level a block at a time to the east, 1..7; and at z=8
        a source beside falling water fed from above."""
        save.set_block(2, 63, 4, WATER)
        for level in range(1, 8):
            save.set_block(2 + level, 63, 4, f"minecraft:water[level={level}]")
        save.set_block(2, 63, 8, WATER)
        save.set_block(3, 63, 8, "minecraft:water[level=8]")
        save.set_block(3, 64, 8, "minecraft:water[level=8]")
        save.set_block(3, 65, 8, WATER)
        save.fill((4, 64, 7), (4, 65, 9), STONE)
        save.fill((3, 64, 7), (3, 65, 7), STONE)
        save.fill((3, 64, 9), (3, 65, 9), STONE)

    def test_a_fill_that_starves_flowing_water_is_refused(self):
        """Filling the stream's source from its bed leaves the flow beside
        it with nothing to feed it: the game re-reads a flow from what
        feeds it, so the whole stream would dry where it stands. level_pad
        filling it and a grade draining it are refused, naming the first
        cell starved, with nothing written; ``allow_dam=True`` writes both.
        pave has no such argument, and points at grade. A source beside
        falling water fed from above starves nothing."""
        b = Builder(self.world(self.stream))
        for call in (lambda: b.level_pad(0, 2, 2, 6, 63, over_water="fill"),
                     lambda: b.grade((0, 2, 2, 6), 63, drain=True)):
            with self.assertRaises(Refused) as caught:
                call()
            self.assertEqual(caught.exception.faults[0], (3, 63, 4))
            self.assertEqual(caught.exception.faults,
                             [(x, 63, 4) for x in range(3, 10)])
            message = str(caught.exception)
            self.assertIn("allow_dam=True", message)
            self.assertIn("(3, 63, 4)", message)
            self.assertEqual(b.blocks_set, 0)
        with self.assertRaises(Refused) as caught:
            b.pave(0, 2, 2, 6, 63, over_water="fill", light=None)
        message = str(caught.exception)
        self.assertIn("grade(..., drain=True, allow_dam=True)", message)
        self.assertNotIn("Pass allow_dam=True", message)
        self.assertEqual(b.blocks_set, 0)

        save = self.world(self.stream)
        b = Builder(save)
        b.level_pad(0, 2, 2, 6, 63, over_water="fill", allow_dam=True)
        self.assertEqual(self.name(2, 63, 4, save), "minecraft:grass_block")
        r = Builder(self.world(self.stream)).grade((0, 2, 2, 6), 63,
                                                    drain=True, allow_dam=True)
        self.assertEqual(r.drained, 1)
        # Falling water fed from above keeps its feed.
        r = Builder(self.world(self.stream)).grade((0, 7, 2, 9), 63,
                                                    drain=True)
        self.assertEqual(r.drained, 1)
        self.assertIn((3, 63, 8), r.beside_drained)

    def test_a_drain_under_its_bed_takes_the_bed_down(self):
        """A pond up in a hill, its stone bed at 65 and its water 66..67,
        drained to 63: the drain takes the column whole, bed and all, down
        to the target -- whatever ``cut=`` says, since a drained column is
        never left half taken -- and counts it drained, not cut. The dry
        hill round it, which ``cut=False`` leaves, stands as it stood."""
        def pond(save):
            save.fill((0, 64, 0), (7, 67, 7), STONE)
            save.fill((2, 66, 2), (5, 67, 5), WATER)
        save = self.world(pond)
        b = Builder(save)
        hill = {(x, z): self.column(x, z, save=save)
                for x in range(8) for z in range(8)
                if not (2 <= x <= 5 and 2 <= z <= 5)}
        r = b.grade((2, 2, 5, 5), 63, cut=False, fill=False, drain=True,
                    retain=None, clearance=0)
        self.assertEqual((r.columns, r.drained, r.cut, r.left), (16, 16, 0, 0))
        for x in range(2, 6):
            for z in range(2, 6):
                self.assertEqual(self.name(x, 63, z, save),
                                 "minecraft:grass_block")
                for y in range(64, 69):
                    self.assertEqual(self.name(x, y, z, save),
                                     "minecraft:air", (x, y, z))
                self.assertEqual(b.ground(x, z), 63)
        for column, before in hill.items():
            self.assertEqual(self.column(*column, save=save), before, column)

    def test_a_bridge_or_a_seal_in_a_flows_feed_starves_it(self):
        """A surface laid at the waterline stands in the water's top cell
        and takes it, as a fill takes the water it fills, and so does a
        seal: over the stream's source either starves the flow it fed. A
        pad, a grade and a road bridged there are refused, naming every
        cell starved, with nothing written, and ``allow_dam=True`` writes
        them; a floor and its lights, which have no such argument, point
        at grade. A grade cut beside the source lets the water out and is
        refused unless sealed -- and the seal, which takes the source's
        place, is refused for its starve until ``allow_dam=True``. A
        bridge over the waterline takes no water, and is built."""
        flow = [(x, 63, 4) for x in range(3, 10)]
        bridges = {
            "level_pad": lambda b, **k: b.level_pad(
                0, 2, 2, 6, 63, over_water="bridge", **k),
            "grade": lambda b, **k: b.grade(
                (0, 2, 2, 6), 63, over_water="bridge", **k),
            "road": lambda b, **k: b.road(
                [(2, 0), (2, 6)], width=1, over_water="bridge", **k),
            "seal": lambda b, **k: b.grade((-3, 2, 1, 6), 62, seal=COBBLE,
                                           **k),
            "pave": lambda b: b.pave(0, 2, 2, 6, 63, over_water="bridge",
                                     light=None),
            "pave on the water": lambda b: b.pave(
                0, 2, 2, 6, over_water="bridge", light=None),
            "light_cells": lambda b: b.light_cells(
                [(2, 4)], light_level=12, over_water="bridge")}
        for label, call in bridges.items():
            with self.subTest(label):
                save = self.world(self.stream)
                b = Builder(save)
                with self.assertRaises(Refused) as caught:
                    call(b)
                self.assertEqual(caught.exception.faults, flow)
                message = str(caught.exception)
                what = {"seal": "grade", "pave on the water": "pave"}.get(
                    label, label)
                self.assertIn(f"{what} would cut flowing water off", message)
                self.assertIn("first (3, 63, 4)", message)
                if what in ("pave", "light_cells"):
                    self.assertIn("grade(..., drain=True, allow_dam=True)",
                                  message)
                    self.assertNotIn("Pass allow_dam=True", message)
                else:
                    self.assertIn("Pass allow_dam=True", message)
                self.assertEqual(b.blocks_set, 0)
                self.assertEqual(self.name(2, 63, 4, save), "minecraft:water")
                if what in ("pave", "light_cells"):
                    continue
                save = self.world(self.stream)
                call(Builder(save), allow_dam=True)
                self.assertFalse(self.wet(2, 63, 4, save), "the source taken")
                if label == "seal":
                    self.assertEqual(self.name(2, 63, 4, save), COBBLE)
        # Unsealed, the grade beside the source lets the water out.
        b = Builder(self.world(self.stream))
        with self.assertRaises(Refused) as caught:
            b.grade((-3, 2, 1, 6), 62)
        leak, = caught.exception.faults
        self.assertEqual((leak.cell, leak.source), ((1, 63, 4), (2, 63, 4)))
        # Over the waterline the water stays where it stood.
        for call in (lambda b: b.level_pad(0, 2, 2, 6, 64,
                                           over_water="bridge"),
                     lambda b: b.pave(0, 2, 2, 6, 64, over_water="bridge",
                                      light=None)):
            save = self.world(self.stream)
            call(Builder(save))
            self.assertEqual(self.name(2, 63, 4, save), "minecraft:water")
            self.assertEqual(self.name(3, 63, 4, save), "minecraft:water")

    def test_a_lid_is_not_drained(self):
        """Ice over air is a lid over whatever is under it: draining it is
        refused, naming it; bridged, the grade stands on the ice."""
        def lid(save):
            save.fill((2, 55, 2), (6, 63, 6), "minecraft:air")
            save.fill((2, 63, 2), (6, 63, 6), "minecraft:ice")
        save = self.world(lid)
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.grade((3, 3, 5, 5), 64, drain=True)
        faults = caught.exception.faults
        self.assertEqual(len(faults), 9)
        self.assertTrue(all(f.lid and f.bed_y is None for f in faults))
        message = str(caught.exception)
        for words in ("cannot drain 9 columns", "a lid",
                      "over_water='bridge'"):
            self.assertIn(words, message)
        self.assertEqual(b.blocks_set, 0)
        # Refused without a word about water, a grade offers the drain.
        with self.assertRaises(Refused) as caught:
            b.grade((3, 3, 5, 5), 64)
        self.assertIn("drain=True", str(caught.exception))
        r = b.grade((3, 3, 5, 5), 64, over_water="bridge")
        self.assertEqual(r.bridged, 9)
        self.assertEqual(self.name(4, 63, 4, save), "minecraft:ice")

    def swamp(self, save):
        """Ground at 62 -- stone, dirt, grass -- with a sunken corner at
        60, a rise at 66, pond A (water 59..61 on a sand bed at 58) and
        pond B, whose bed is the plane itself: water 63..64 on clay at 62,
        held in by banks at 64. OCEAN_FLOOR stands at 62 over pond B, which
        is where the Roman drain's magic floor took over and filled 58..61
        with dirt."""
        save.fill((0, 60, 0), (15, 61, 15), "minecraft:dirt")
        save.fill((0, 63, 0), (15, 63, 15), "minecraft:air")
        save.fill((0, 62, 0), (15, 62, 15), "minecraft:grass_block[snowy=false]")
        save.fill((10, 61, 0), (13, 62, 3), "minecraft:air")
        save.fill((10, 60, 0), (13, 60, 3), "minecraft:grass_block[snowy=false]")
        save.fill((14, 63, 0), (15, 66, 3), "minecraft:dirt")
        save.fill((2, 58, 2), (5, 62, 5), "minecraft:air")
        save.fill((2, 58, 2), (5, 58, 5), "minecraft:sand")
        save.fill((2, 59, 2), (5, 61, 5), WATER)
        save.fill((7, 62, 8), (12, 64, 13), "minecraft:dirt")
        save.fill((8, 62, 9), (11, 62, 12), "minecraft:clay")
        save.fill((8, 63, 9), (11, 64, 12), WATER)

    def test_the_two_drain_loops_as_grades(self):
        """The Roman drain (cut=False, fill=False, drain=True, retain=None,
        clearance=0 at 62) and the lab's (cut=False, fill=True, drain=True
        at 64) as grades over a swamp: no water from any bed to the target,
        nothing under a bed rewritten -- pond B's bed is the plane, where
        the Roman loop's magic floor wrote dirt 58..61 -- and the dry
        ground as each loop meant: untouched by the Roman, raised to the
        lawn by the lab where it was sunken and left where it stood
        high."""
        beds = {(x, z): 58 for x in range(2, 6) for z in range(2, 6)}
        beds.update({(x, z): 62 for x in range(8, 12) for z in range(9, 13)})
        rect = (0, 0, 15, 15)
        for loop, target, kwargs in (
                ("roman", 62, dict(cut=False, fill=False, drain=True,
                                   retain=None, clearance=0)),
                ("lab", 64, dict(cut=False, fill=True, drain=True))):
            with self.subTest(loop=loop):
                save = self.world(self.swamp)
                b = Builder(save)
                before = {(x, z): self.column(x, z, 40, 70, save)
                          for x in range(16) for z in range(16)}
                r = b.grade(rect, target, **kwargs)
                self.assertEqual(r.drained, 32)
                for (x, z), bed in beds.items():
                    self.assertEqual(self.column(x, z, 40, bed - 1, save),
                                     before[(x, z)][:bed - 40], (x, z))
                    self.assertFalse(any(self.wet(x, y, z, save)
                                         for y in range(bed, target + 3)))
                    self.assertEqual(self.name(x, target, z, save),
                                     "minecraft:grass_block")
                    self.assertEqual(b.ground(x, z), target)
                dry = [(x, z) for x in range(16) for z in range(16)
                       if (x, z) not in beds]
                if loop == "roman":
                    self.assertEqual(r.left, len(dry))
                    for column in dry:
                        self.assertEqual(self.column(*column, 40, 70, save),
                                         before[column], column)
                else:
                    for x, z in dry:
                        high = x >= 14 and z <= 3
                        if high:
                            self.assertEqual(self.column(x, z, 40, 70, save),
                                             before[(x, z)])
                        else:
                            self.assertEqual(b.ground(x, z), 64, (x, z))
                    # Sunken, raised: dirt over its old lawn, and the
                    # retaining wall at the outline's edge.
                    self.assertEqual(before[(11, 1)][60 - 40],
                                     "minecraft:grass_block[snowy=false]")
                    for y in (61, 62, 63):
                        self.assertEqual(self.name(11, y, 1, save),
                                         "minecraft:dirt")
                        self.assertEqual(self.name(11, y, 0, save),
                                         "minecraft:stone_bricks")

    def half_a_pond(self, save):
        """A pond across x 4..11, z 4..7: water 60..63 on the stone at 59."""
        save.fill((4, 60, 4), (11, 63, 7), WATER)

    def test_water_left_beside_the_drain_is_refused_unless_sealed(self):
        """An outline across a pond, drained below the waterline: the air
        cut where the water stood is open to the half the outline left,
        and the water runs in the first time anything near it updates --
        the lab's y=65 case. Refused, naming every leak; ``seal=`` walls
        the water off where it stands, outside a keep-out unless
        ``through=`` names it, and the water left standing against the
        fill below the seals is reported. Drained no lower than the
        waterline, the pond's other half stands against the fill and
        nothing gets out."""
        save = self.world(self.half_a_pond)
        b = Builder(save)
        with self.assertRaises(Refused) as caught:
            b.grade((0, 0, 7, 11), 61, drain=True)
        leaks = caught.exception.faults
        self.assertEqual([leak.cell for leak in leaks],
                         [(7, y, z) for y in (62, 63) for z in range(4, 8)])
        self.assertEqual({leak.source for leak in leaks},
                         {(8, y, z) for y in (62, 63) for z in range(4, 8)})
        self.assertIn("seal='minecraft:cobblestone'", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)

        b.reserve(8, 0, 9, 11, name="garden")
        with self.assertRaises(Refused) as caught:
            b.grade((0, 0, 7, 11), 61, drain=True, seal=COBBLE)
        self.assertIn("reservation 'garden'", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        r = b.grade((0, 0, 7, 11), 61, drain=True, seal=COBBLE,
                    through=("garden",))
        self.assertEqual((r.drained, r.sealed), (16, 8))
        for z in range(4, 8):
            for y in (62, 63):
                self.assertEqual(self.name(8, y, z, save), COBBLE)
                self.assertEqual(self.name(7, y, z, save), "minecraft:air")
        self.assertEqual(r.beside_drained,
                         tuple((8, y, z) for y in (60, 61) for z in range(4, 8)))
        self.assertTrue(self.wet(9, 63, 5, save), "the rest of the pond")
        for seal, error in ((Mix([(COBBLE, 1)]), TypeError),
                            ("minecraft:sand", NBTError),
                            ("minecraft:oak_slab[type=bottom]", NBTError)):
            with self.subTest(seal=seal):
                with self.assertRaises(error):
                    Builder(self.world(self.half_a_pond)).grade(
                        (0, 0, 7, 11), 61, drain=True, seal=seal)

        save = self.world(self.half_a_pond)
        r = Builder(save).grade((0, 0, 7, 11), 63, drain=True)
        self.assertEqual((r.drained, r.sealed), (16, 0))
        self.assertEqual(r.beside_drained,
                         tuple((8, y, z) for y in range(60, 64)
                               for z in range(4, 8)))
        self.assertEqual(self.name(7, 62, 5, save), "minecraft:stone_bricks",
                         "the outline's edge, walled")
        self.assertEqual(self.name(6, 62, 5, save), "minecraft:dirt")


class OccupancyTests(GradeBase):
    """An earthwork writer takes what nobody built, and refuses the rest.

    ``grade`` once cut whatever stood in its outline. A session's keep-outs
    die with it, and the column reader stands on a tower's roof as it stands
    on a hill, so a later session's plaza took about 11,000 blocks out of a
    standing tower on a scratch copy of CastleWorld (2026-11-01) and nothing
    said a word."""

    def tower(self, x0=2, z0=2, x1=6, z1=6, top=90, block="minecraft:bricks"):
        """A solid block of somebody's bricks, standing on the ground."""
        self.save.fill((x0, 64, z0), (x1, top, z1), block)
        self.save.rebuild_heightmaps((x0, 0, z0), (x1, 0, z1))

    def test_a_grade_refuses_a_tower_in_its_cut_and_writes_nothing(self):
        self.tower()
        before = [self.column(x, 4) for x in range(0, 10)]
        with self.assertRaises(Refused) as caught:
            self.b.grade((0, 0, 9, 9), 63, retain=None, surface=COBBLE)
        self.assertIn("somebody built", str(caught.exception))
        self.assertIn("overwrite=True", str(caught.exception))
        self.assertEqual(before, [self.column(x, 4) for x in range(0, 10)])

    def test_a_refusal_names_every_cell_it_would_have_taken(self):
        self.tower(x0=2, z0=2, x1=2, z1=2, top=66)
        with self.assertRaises(Refused) as caught:
            self.b.grade((0, 0, 4, 4), 63, retain=None, surface=COBBLE,
                         clearance=6)
        self.assertEqual(len(caught.exception.faults), 3)
        self.assertIn("(2, 64, 2)", caught.exception.faults[0])

    def test_level_pad_refuses_it_too(self):
        self.tower()
        with self.assertRaises(Refused):
            self.b.level_pad(0, 0, 9, 9, 63, surface=COBBLE, wall=None)
        self.assertEqual(self.name(4, 70, 4), "minecraft:bricks")

    def test_a_clearance_takes_nothing_somebody_built_either(self):
        # A beam over the site, under no column's ground: only the headroom
        # reaches it.
        self.save.fill((4, 66, 4), (4, 66, 4), "minecraft:oak_planks")
        with self.assertRaises(Refused):
            self.b.grade((0, 0, 9, 9), 63, retain=None, surface=COBBLE,
                         clearance=6)

    def test_overwrite_takes_it_all_the_same(self):
        self.tower()
        self.b.grade((0, 0, 9, 9), 63, retain=None, surface=COBBLE,
                     overwrite=True, clearance=30)
        self.assertEqual(self.name(4, 63, 4), COBBLE)
        self.assertEqual(self.name(4, 64, 4), "minecraft:air")
        self.b2 = Builder(self.save, seed=7)
        self.tower(x0=20, z0=20, x1=20, z1=20)
        self.b2.level_pad(19, 19, 21, 21, 63, surface=COBBLE, wall=None,
                          overwrite=True)
        self.assertEqual(self.name(20, 64, 20), "minecraft:air")

    def test_what_nobody_built_is_cut_as_it_always_was(self):
        self.save.fill((2, 64, 2), (6, 70, 6), "minecraft:granite")
        self.save.set_block(3, 71, 3, "minecraft:dandelion")
        self.save.set_block(4, 71, 4, "minecraft:oak_log[axis=y]")
        self.save.rebuild_heightmaps((2, 0, 2), (6, 0, 6))
        result = self.b.grade((0, 0, 9, 9), 63, retain=None, surface=COBBLE)
        self.assertEqual(self.name(4, 66, 4), "minecraft:air")
        self.assertEqual(result.cut, 25)

    def test_a_cactus_is_nobody_s(self):
        self.save.set_block(3, 64, 3, "minecraft:cactus")
        self.save.set_block(3, 65, 3, "minecraft:cactus_flower")
        self.b.grade((0, 0, 9, 9), 63, retain=None, surface=COBBLE)
        self.assertEqual(self.name(3, 64, 3), "minecraft:air")

    def test_a_grade_of_what_is_already_there_is_no_taking(self):
        self.b.grade((0, 0, 4, 4), 63, retain=None, surface=COBBLE)
        Builder(self.save, seed=7).grade((0, 0, 4, 4), 63, retain=None,
                                         surface=COBBLE)
        self.assertEqual(self.name(2, 63, 2), COBBLE)

    def test_a_mix_is_compared_at_the_cell_it_lands_in(self):
        mix = Mix([(COBBLE, 1), ("minecraft:andesite", 1)], seed=3)
        self.b.grade((0, 0, 4, 4), 63, retain=None, surface=mix)
        Builder(self.save, seed=7).grade((0, 0, 4, 4), 63, retain=None,
                                         surface=mix)
        with self.assertRaises(Refused):
            Builder(self.save, seed=7).grade((0, 0, 4, 4), 63, retain=None,
                                             surface=Mix(
                                                 [(COBBLE, 1),
                                                  ("minecraft:granite", 1)],
                                                 seed=9))

    def test_a_chunk_that_is_not_there_is_still_that_refusal(self):
        with self.assertRaises(NoSurface):
            self.b.grade((40, 40, 44, 44), 63, retain=None, surface=COBBLE)

    def test_a_road_keeps_out_of_a_tower_it_was_never_told_of(self):
        self.tower(x0=4, z0=-4, x1=8, z1=4)
        before = self.column(6, 0)
        with self.assertRaises(Refused) as caught:
            self.b.road([(0, 0), (14, 0)], width=3, mix=COBBLE)
        self.assertIn("somebody built", str(caught.exception))
        self.assertEqual(before, self.column(6, 0))

    def test_overwrite_lets_a_road_through(self):
        self.tower(x0=4, z0=-4, x1=8, z1=4, top=66)
        self.b.road([(0, 0), (14, 0)], width=3, mix=COBBLE, overwrite=True,
                    allow_dam=True)
        self.assertEqual(self.name(6, 67, 0), "minecraft:air")

    def test_a_road_crosses_a_road_this_session_laid(self):
        self.b.road([(0, -8), (0, 8)], width=3, mix=COBBLE)
        self.b.road([(-8, 0), (8, 0)], width=3, mix=COBBLE)
        self.assertEqual(self.name(0, 63, 0), COBBLE)

    def test_a_road_refuses_a_road_some_other_session_laid(self):
        self.b.road([(0, -8), (0, 8)], width=3, mix=COBBLE)
        later = Builder(self.save, seed=7)
        with self.assertRaises(Refused):
            later.road([(-8, 0), (8, 0)], width=3,
                       mix="minecraft:andesite")
        later.road([(-8, 0), (8, 0)], width=3, mix="minecraft:andesite",
                   overwrite=True)


class LevelPadBytesTests(GradeBase):
    """level_pad, which is a grade now, writes the bytes it always wrote."""

    #: The sha256 of every chunk payload (``tests.test_roof._payloads``)
    #: after :data:`CALLS` on the hills, computed before level_pad was a
    #: grade -- the planning order, the palette order and the bytes of a
    #: Mix surface and ring, reversed corners, three clearances, a pond
    #: bridged and a pond filled from its bed. The hills leave nothing over
    #: a pad for a clearance to clear, so the height of the clear is held
    #: by ``GradeTests.test_a_pad_clears_its_clearance_and_no_more``.
    PAYLOADS = "c661985b2485396235942683c8c37cd548a735bea295b9516e80ee434ad68b00"

    RING = Mix([("minecraft:stone_bricks", 3),
                ("minecraft:mossy_stone_bricks", 1)], clump=2)
    LAWN = Mix([("minecraft:moss_block", 1),
                ("minecraft:grass_block[snowy=false]", 3)], seed=5)
    CALLS = [((0, 0, 8, 6), 63, dict(surface=LAWN, wall=RING, clearance=0,
                                     name="yard")),
             ((17, 3, 10, -6), 61, dict(clearance=10, name="court")),
             ((-6, -2, -1, -6), 64, dict(clearance=3)),
             ((2, 9, 7, 15), 60, dict(surface="minecraft:stone_bricks",
                                      wall="minecraft:bricks")),
             ((18, 18, 23, 22), 63, dict(over_water="bridge", name="jetty")),
             ((21, 23, 24, 25), 62, dict(over_water="fill",
                                         wall="minecraft:bricks",
                                         name="pier"))]

    def test_level_pad_writes_the_bytes_it_always_wrote(self):
        """Every call, in order, on the hills: ``level_pad`` saves the
        bytes it saved before it was a grade, and ``grade`` over the same
        rectangles, named, saves them too."""
        from tests.test_roof import _payloads

        for writer in ("level_pad", "grade"):
            with self.subTest(writer=writer):
                save = self.world(hills)
                b = Builder(save, seed=3)
                for rect, top, kwargs in self.CALLS:
                    kwargs = dict(kwargs)
                    if writer == "level_pad":
                        b.level_pad(*rect, top, **kwargs)
                        continue
                    retain = kwargs.pop("wall", FROM_PALETTE)
                    kwargs.setdefault("name", f"pad-{len(b._pads) + 1}")
                    b.grade(rect, top, retain=retain, **kwargs)
                save.save()
                self.assertEqual(_payloads(save.directory), self.PAYLOADS)


if __name__ == "__main__":
    unittest.main()
