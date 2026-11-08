"""Tests for craft.build's roofs: Roof, rise_of, Builder.roof and gable_roof.

One rule -- a half-block rise over distance from the outline -- is the
roofs the builds wrote by hand over a rectangle or a disc, each at one
setting, so the oracles here are those scripts themselves, run against a
recording stand-in and compared cell for cell: the Roman town's hip, low
hip, pediment, shed and cones (``tools/roman_city``), the JeffWorld baths
dome and macellum cone, the castle example's spire, the suburban house's
rear gable (``tools/suburban_house/roof.py``), ``gable_roof`` as it stood
before it was laid on the rule, and one course of a ``Vault``. A stair's
``shape`` is never compared: ``finish()`` derives it.

Two of them differ on purpose, and the tests say how: the Roman shed's
stairs face down its slope (a roof's face up it, as every gable's do),
and the suburban rear gable laid stone in its two verge columns where the
rule lays stairs. The Roman ``common.dome`` is not here at all: its height
is a function of the exact squared radius, not of the ring a cell is on,
and it has an oculus, so it is another shape -- the round roofs that are
ring by ring (the cones, the baths dome, the castle's spire) all are. The
mansards, tiers and upturned eaves here are settings of the rule, held to
what any roof must be -- no gap in a column, no stair facing down its
slope -- since no script wrote one.

A plan that is no rectangle is the last two classes. A hip over one is a
``Roof`` over its footprint (``FootprintTests``): a rectangle given as its
cells is the rectangle's roof, byte for byte, and an L, a T and a cross
are their rectangles' hips joined, state for state. A plan of gables is
``Wings`` (``WingsTests``): one wing is its roof, and the suburban house's
main roof -- a cross gable no script wrote, built by hand -- is three
wings held to a grid of what stands in JeffWorld, in shape: every column's
height but six Jeff left a block short, and every top block but the stone
he laid in two rows of the south slope, the rear gable's verges and the
inner-corner stairs of the valleys, where the rule lays plain blocks. A
join keeps each wing what it is alone -- its building under its walls
from its own springing, its eave closing its attic when another wing
roofs it over -- and random joins are held to that: nothing written into
a building, no attic left open.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
import os
import random
import shutil
import struct
import sys
import tempfile
import unittest
import zlib

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "examples"))

import castle  # noqa: E402
from heaton_voxel.blocks import BlockState  # noqa: E402
from heaton_voxel.craft import Roof, RoofResult, Wings, rise_of  # noqa: E402
from heaton_voxel.craft.build import (  # noqa: E402
    Builder,
    Vault,
    arch_profile,
    pointed_arch_profile,
)
from heaton_voxel.craft.build.roof import RoofMixin  # noqa: E402
from heaton_voxel.craft.errors import IllegalBlock, Refused  # noqa: E402
from heaton_voxel.craft.field import chessboard, distance_to_edge  # noqa: E402
from heaton_voxel.craft.geometry import COMPASS, OPPOSITE, disc  # noqa: E402
from heaton_voxel.craft.style import (  # noqa: E402
    DEFAULT_PALETTE,
    FROM_PALETTE,
    Material,
    Mix,
)
from heaton_voxel.nbt import NBTError  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402
from tests.test_world import make_save  # noqa: E402


def _tool(relative: str, name: str):
    """A ``tools/`` module loaded by path, under a name of its own."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(_ROOT, "tools", relative))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROMAN = _tool(os.path.join("roman_city", "common.py"), "_roof_roman_common")


def _suburban():
    """``tools/suburban_house/house.py`` and ``roof.py``, loaded by path.
    The roof script imports its sibling as ``house``, so the sibling is
    lent that name while it loads and the name is given back after.
    (``check.py`` beside them loads JeffWorld on import, and is left
    alone.)"""
    folder = os.path.join("suburban_house")
    spec = importlib.util.spec_from_file_location(
        "house", os.path.join(_ROOT, "tools", folder, "house.py"))
    house = importlib.util.module_from_spec(spec)
    before = sys.modules.get("house")
    sys.modules["house"] = house
    try:
        spec.loader.exec_module(house)
        roof = _tool(os.path.join(folder, "roof.py"), "_roof_suburban_roof")
    finally:
        if before is None:
            sys.modules.pop("house", None)
        else:
            sys.modules["house"] = before
    return house, roof


class _Recorder:
    """Just enough Builder for a hand-written roof: every cell it sets,
    the last write winning, as the world would keep it."""

    def __init__(self):
        self.cells: dict[tuple[int, int, int], BlockState] = {}

    def set(self, x, y, z, block):
        self.cells[(x, y, z)] = (BlockState.parse(block)
                                 if isinstance(block, str) else block)

    def fill(self, corner1, corner2, block):
        (x0, x1), (y0, y1), (z0, z1) = (sorted(pair) for pair in
                                        zip(corner1, corner2))
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    self.set(x, y, z, block)


def _same(found: BlockState, want: BlockState) -> bool:
    """One game state, a property left out its default, a stair's corner
    shape aside: finish() derives it."""
    if found.name != want.name:
        return False
    a = {k: v for k, v in found.canonical().properties.items() if k != "shape"}
    b = {k: v for k, v in want.canonical().properties.items() if k != "shape"}
    return a == b


def _piece(state: BlockState, y: int) -> tuple[int, int]:
    """What a block occupies of its column, in half blocks."""
    kind = state.properties.get("type")
    if state.name.endswith("_slab") and kind == "bottom":
        return 2 * y, 2 * y + 1
    if state.name.endswith("_slab") and kind == "top":
        return 2 * y + 1, 2 * y + 2
    return 2 * y, 2 * y + 2


def _columns(plan) -> dict:
    """``{(x, z): (bottom, top)}`` of a plan, in half blocks, refusing a
    column with a gap inside it."""
    spans: dict = {}
    for (x, y, z), state in plan.items():
        spans.setdefault((x, z), []).append(_piece(state, y))
    out = {}
    for column, pieces in spans.items():
        pieces.sort()
        for (_, high), (low, _) in zip(pieces, pieces[1:]):
            assert high == low, f"a gap inside the column {column}: {pieces}"
        out[column] = (pieces[0][0], pieces[-1][1])
    return out


#: The two-branch loop ``Builder.gable_roof`` wrote before it was laid on
#: the rule (craft/build/shells.py at the phase-3 step-11 snapshot), as it
#: stood: its ops, in order, spelled as it spelled them.
def _old_gable(x0, z0, x1, z1, base_y, axis, *, stair, plank, slab):
    ops = []
    if axis == "x":
        lo, hi = z0 - 1, z1 + 1
        step = 0
        while lo <= hi:
            for x in range(x0 - 1, x1 + 2):
                y = base_y + step
                if lo == hi:
                    ops.append(("set", (x, y, lo), f"{slab}[type=bottom]"))
                else:
                    ops.append(("set", (x, y, lo),
                                f"{stair}[facing=south,half=bottom]"))
                    ops.append(("set", (x, y, hi),
                                f"{stair}[facing=north,half=bottom]"))
            if lo < hi:
                for z in range(lo + 1, hi):
                    for gx in (x0, x1):
                        ops.append(("set", (gx, base_y + step, z), plank))
            lo += 1
            hi -= 1
            step += 1
    else:
        lo, hi = x0 - 1, x1 + 1
        step = 0
        while lo <= hi:
            for z in range(z0 - 1, z1 + 2):
                y = base_y + step
                if lo == hi:
                    ops.append(("set", (lo, y, z), f"{slab}[type=bottom]"))
                else:
                    ops.append(("set", (lo, y, z),
                                f"{stair}[facing=east,half=bottom]"))
                    ops.append(("set", (hi, y, z),
                                f"{stair}[facing=west,half=bottom]"))
            if lo < hi:
                for x in range(lo + 1, hi):
                    for gz in (z0, z1):
                        ops.append(("set", (x, base_y + step, gz), plank))
            lo += 1
            hi -= 1
            step += 1
    return ops


class _GableCapture(RoofMixin):
    """Just enough Builder for ``gable_roof``: its planned ops, uncommitted,
    its materials resolved as a Builder with no palette resolves them."""

    _palette = DEFAULT_PALETTE
    _materials = Builder._materials

    def __init__(self):
        self.ops = None

    def _alive(self):
        pass

    def _commit(self, ops, *, what=None):
        self.ops = list(ops)


STAIR = "minecraft:dark_oak_stairs"
PLANK = "minecraft:dark_oak_planks"
SLAB = "minecraft:dark_oak_slab"
STONE = "minecraft:stone_bricks"
AIR = "minecraft:air"


def _gable_rule(axis, *, stair=STAIR, plank=PLANK, slab=SLAB):
    eaves = ("north", "south") if axis == "x" else ("west", "east")
    return Roof(eaves=eaves, pitch=2, overhang=1, block=plank, slab=slab,
                stair=stair, close_ends=plank)


class RiseTests(unittest.TestCase):
    def test_rise_of_turns_a_vault_profile_inside_out(self):
        profile = arch_profile(6, 6)
        self.assertEqual(profile, (0, 0, 1, 2, 3, 5, 12))
        self.assertEqual(rise_of(profile), (2, 9, 11, 12, 13, 14, 14))
        self.assertEqual(rise_of(profile, eave=1), (1, 8, 10, 11, 12, 13, 13))
        self.assertEqual(rise_of((0,)), (2,))
        for bad in ((1, 2), (0, 2, 1), (), "abc"):
            with self.subTest(profile=bad):
                with self.assertRaises(NBTError):
                    rise_of(bad)
        with self.assertRaises(NBTError):
            rise_of((0, 1), eave=0)

    def test_past_its_rise_a_roof_is_flat(self):
        roof = Roof(rings="square", rise=(4, 8, 12), block=STONE,
                    slab=None, stair=None, crown="block")
        self.assertEqual([roof.rise_at(d) for d in range(6)],
                         [4, 8, 12, 12, 12, 12])
        tops = {column: top for column, (_, top) in _columns(
            roof.plan((0, 0, 10, 8), 70, attic="hollow")).items()}
        for (x, z), top in tops.items():
            ring = min(x, 10 - x, z, 8 - z)
            self.assertEqual(top, 140 + (4, 8, 12)[min(ring, 2)], (x, z))
        # A pitch is a rise climbing from 2, and a run of pitches flattens
        # past its end too.
        self.assertEqual([Roof(rings="round", pitch=3, block=STONE,
                               slab=None, stair=None).rise_at(d)
                          for d in range(4)], [2, 5, 8, 11])
        self.assertEqual([Roof(rings="round", pitch=(1, 2, 4), block=STONE,
                               slab=None, stair=None).rise_at(d)
                          for d in range(6)], [2, 3, 5, 9, 9, 9])


class HandWrittenRoofTests(unittest.TestCase):
    """Each roof a script wrote by hand, and the rule at its setting."""

    def assertSamePlan(self, hand: dict, plan: dict, label, *, skip=()):
        self.assertEqual(set(hand) - set(skip), set(plan) - set(skip),
                         f"{label}: the cells differ")
        wrong = [(cell, str(hand[cell]), str(plan[cell]))
                 for cell in sorted(hand) if cell not in skip
                 and not _same(hand[cell], plan[cell])]
        self.assertEqual(wrong, [], f"{label}: states differ")

    def test_gable_roof_is_a_roof(self):
        """``gable_roof`` as it stood, both branches, over every span and
        length: the rule plans the same cells in the same order with the
        same property order, where a one-block-long roof wrote each
        gable-end plank twice and the rule writes it once; and
        ``gable_roof`` now hands ``_commit`` exactly the rule's plan."""
        for axis in ("x", "z"):
            for span in range(1, 12):
                for length in range(1, 10):
                    if axis == "x":
                        x1, z1 = length - 1, span - 1
                    else:
                        x1, z1 = span - 1, length - 1
                    with self.subTest(axis=axis, span=span, length=length):
                        old = _old_gable(0, 0, x1, z1, 70, axis, stair=STAIR,
                                         plank=PLANK, slab=SLAB)
                        seen, want = set(), []
                        for _, cell, block in old:
                            if cell in seen:
                                self.assertEqual(length, 1, cell)
                                continue
                            seen.add(cell)
                            want.append((cell, BlockState.parse(block)))
                        plan = list(_gable_rule(axis).plan(
                            (0, 0, x1, z1), 70, attic="hollow").items())
                        self.assertEqual([cell for cell, _ in plan],
                                         [cell for cell, _ in want])
                        for (cell, got), (_, expect) in zip(plan, want):
                            self.assertEqual(got, expect, cell)
                            self.assertEqual(list(got.properties.items()),
                                             list(expect.properties.items()),
                                             cell)
                        capture = _GableCapture()
                        capture.gable_roof(0, 0, x1, z1, 70, axis)
                        self.assertEqual([(kind, cell) for kind, cell, _
                                          in capture.ops],
                                         [("set", cell) for cell, _ in plan])
                        self.assertEqual([block for _, _, block in capture.ops],
                                         [state for _, state in plan])

    def test_the_roman_hip_is_square_rings(self):
        """``common.hipped_roof``: stair rings stepping in from every edge,
        a block at every corner, a slab ridge or apex."""
        for overhang in (0, 1):
            for width in range(1, 12):
                for length in range(1, 12):
                    hand = _Recorder()
                    ROMAN.hipped_roof(hand, 0, 0, width - 1, length - 1, 70,
                                      overhang=overhang)
                    plan = Roof(rings="square", pitch=2, overhang=overhang,
                                hip=ROMAN.TILE, block=ROMAN.TILE,
                                slab=ROMAN.TILE_SLAB,
                                stair=ROMAN.TILE_STAIR).plan(
                        (0, 0, width - 1, length - 1), 70, attic="hollow")
                    self.assertSamePlan(hand.cells, plan,
                                        (overhang, width, length))

    def test_the_roman_low_hip_climbs_every_few_rings(self):
        """``common.low_hip``: whole-block rings stepping up every
        ``rise_per`` rings -- a rise, taken as it is."""
        for rise_per in (1, 2, 3):
            rise = tuple(2 + 2 * (d // rise_per) for d in range(8))
            for width in range(1, 14):
                for length in range(1, 14):
                    hand = _Recorder()
                    ROMAN.low_hip(hand, 0, 0, width - 1, length - 1, 70,
                                  rise_per=rise_per)
                    plan = Roof(rings="square", rise=rise, crown="block",
                                block=ROMAN.TILE, slab=None, stair=None).plan(
                        (0, 0, width - 1, length - 1), 70, attic="hollow")
                    self.assertSamePlan(hand.cells, plan,
                                        (rise_per, width, length))

    def test_the_roman_pediment_is_a_one_long_gable_closed_in_marble(self):
        """``common.pediment``: a gable one block long, no stairs, its one
        end plane closed with the marble it is made of."""
        for pitch in (1, 2, 3):
            rise = tuple(2 + 2 * (d // pitch) for d in range(12))
            for width in range(1, 20):
                hand = _Recorder()
                ROMAN.pediment(hand, 0, width - 1, 5, 70, pitch=pitch)
                plan = Roof(eaves=("west", "east"), rise=rise, crown="block",
                            block=ROMAN.MARBLE, slab=None, stair=None,
                            close_ends=ROMAN.MARBLE).plan(
                    (0, 5, width - 1, 5), 70, attic="hollow")
                self.assertSamePlan(hand.cells, plan, (pitch, width))

    def test_the_roman_cones_are_round_rings_on_the_castle_disc(self):
        """The tholos and macellum cones (``m4_districts.py``), a ring of
        tile a block higher for each ring in, the tholos capped with a
        slab; and JeffWorld's macellum cone (``jw_districts.py``), rising
        a block every two rings to a block apex."""
        for radius in range(1, 12):
            for crown in ("slab", "block"):
                hand = _Recorder()
                y = 70
                for r in range(radius, -1, -1):
                    for x, z in ROMAN.ring(0, 0, r):
                        hand.set(x, y, z, ROMAN.TILE)
                    y += 1
                if crown == "slab":
                    hand.set(0, y - 1, 0, f"{ROMAN.TILE_SLAB}[type=bottom]")
                plan = Roof(rings="round", pitch=2, crown=crown,
                            block=ROMAN.TILE, slab=ROMAN.TILE_SLAB,
                            stair=None).plan((0, 0, radius), 70,
                                             attic="hollow")
                self.assertSamePlan(hand.cells, plan, (radius, crown))
        hand = _Recorder()
        for r, dy in ((6, 7), (5, 7), (4, 8), (3, 8), (2, 9), (1, 9), (0, 10)):
            for x, z in ROMAN.ring(0, 0, r):
                hand.set(x, 70 + dy, z, ROMAN.TILE)
        plan = Roof(rings="round", rise=tuple(2 + 2 * (d // 2) for d in range(7)),
                    crown="block", block=ROMAN.TILE, slab=None,
                    stair=None).plan((0, 0, 6), 77, attic="hollow")
        self.assertSamePlan(hand.cells, plan, "JeffWorld macellum")
        self.assertEqual(len(plan), 137, "the cone checked against the world")

    def test_the_baths_dome_is_round_rings_over_a_copper_attic(self):
        """JeffWorld's baths dome (``jw_monuments.py``): six discs of
        copper stacked, smaller each course -- round rings at pitch 2, its
        attic filled with the copper it is made of. Only its finial, a
        different copper set on the apex, is the caller's."""
        copper = "minecraft:waxed_exposed_copper"
        hand = _Recorder()
        for i, r in enumerate((6, 5, 4, 3, 2, 1)):
            for x, z in ROMAN.disc(0, 0, r):
                hand.set(x, 80 + i, z, copper)
        hand.set(0, 86, 0, "minecraft:waxed_copper_block")
        plan = Roof(rings="round", pitch=2, crown="block", block=copper,
                    slab=None, stair=None).plan((0, 0, 6), 80, attic=copper)
        self.assertEqual(len(plan), 371)
        self.assertSamePlan(hand.cells, plan, "baths dome", skip={(0, 86, 0)})
        self.assertEqual(plan[(0, 86, 0)].name, copper)

    def test_the_castle_spire_is_round_rings_at_pitch_four(self):
        """``examples/castle.py``'s spire: two courses a ring, a slab on
        top. The rule lays the shell; the mast the castle fills down its
        middle is the caller's."""
        class Tower:
            pass
        for radius in range(1, 8):
            tower = Tower()
            tower.m = _Recorder()
            castle.Castle.spire(tower, 0, 0, 80, radius)
            plan = Roof(rings="round", pitch=4, depth=(4,) * radius,
                        block=castle.ROOF, slab=castle.ROOF_SLAB,
                        stair=None).plan((0, 0, radius), 81, attic="hollow")
            mast = {(0, y, 0) for y in range(80, 80 + 2 * radius)}
            self.assertEqual(set(tower.m.cells) - set(plan), mast, radius)
            self.assertSamePlan(tower.m.cells, plan, radius, skip=mast)

    def test_two_slopes_meet_where_their_planes_cross(self):
        """The suburban house's rear gable (``tools/suburban_house/roof.py``,
        run as written): its north slope climbs a block every two rows
        from z=3 and its south one from z=12 a row sooner, so they meet in
        a three-row ridge at y=79 -- one rise per eave -- with its stair
        rows, and the attic filled with stone from y=77, cell for cell.
        Only its two verge columns differ: the script laid them in stone,
        the rule in stairs."""
        house, script = _suburban()
        hand = _Recorder()
        script.raise_gable(hand)
        rise = {"north": tuple(2 + 2 * (d // 2) for d in range(10)),
                "south": tuple(2 + 2 * ((d + 1) // 2) for d in range(10))}
        plan = Roof(eaves=("north", "south"), rise=rise,
                    block=house.ROOF_STONE, stair=house.ROOF_STAIR,
                    slab=None).plan((script.ROOF_X0, 3, script.ROOF_X1, 12),
                                    77, attic=house.ROOF_STONE, attic_floor=77)
        self.assertEqual(len(plan), 760)
        verges = {(x, top, z) for z, top in script.REAR_PROFILE.items()
                  for x in (script.ROOF_X0, script.ROOF_X1)
                  if z in script.REAR_STAIRS}
        self.assertEqual(len(verges), 12)
        self.assertSamePlan(hand.cells, plan, "rear gable", skip=verges)
        for x, y, z in verges:
            self.assertEqual(hand.cells[(x, y, z)].name, house.ROOF_STONE)
            self.assertEqual(plan[(x, y, z)].properties["facing"],
                             script.REAR_STAIRS[z])
        # Its tables, read off the plan.
        for z, top in script.REAR_PROFILE.items():
            column = [y for (x, y, cz) in plan if (x, cz) == (-300, z)]
            self.assertEqual(max(column), top, z)
            state = plan[(-300, top, z)]
            self.assertEqual(state.properties.get("facing"),
                             script.REAR_STAIRS.get(z), z)

    def test_a_shed_climbs_away_from_its_eave_facing_up_the_slope(self):
        """``common.shed_roof`` is the same geometry, every cell -- but its
        stairs face down the slope, as TestWorld (2)'s stables (x -213..
        -191, z 218..226) climb south from y=-55 to -47 with all 207 of
        theirs facing north, a sawtooth. The rule faces each
        stair up the slope, its low half to the course below, as a
        gable's do: a stair's full side is the way it faces."""
        for toward in COMPASS:
            for width in range(1, 9):
                for length in range(1, 9):
                    hand = _Recorder()
                    ROMAN.shed_roof(hand, 0, 0, width - 1, length - 1, 70,
                                    toward)
                    plan = Roof(eaves=toward, pitch=2, block=ROMAN.TILE,
                                slab=None, stair=ROMAN.TILE_STAIR).plan(
                        (0, 0, width - 1, length - 1), 70, attic="hollow")
                    label = (toward, width, length)
                    self.assertEqual(set(hand.cells), set(plan), label)
                    for cell, state in plan.items():
                        self.assertEqual(state.properties["facing"],
                                         OPPOSITE[toward], (label, cell))
                        self.assertEqual(hand.cells[cell].properties["facing"],
                                         toward, (label, cell))
        # Up the slope: a shed off its north eave climbs south.
        plan = Roof(eaves="north", pitch=2, block=STONE, slab=None,
                    stair="minecraft:stone_brick_stairs").plan(
            (0, 0, 2, 4), 70, attic="hollow")
        self.assertEqual([max(y for (x, y, cz) in plan if cz == z)
                          for z in range(5)], [70, 71, 72, 73, 74])

    def test_a_one_axis_roof_on_a_vault_profile_is_the_vault(self):
        """One course of a ``Vault`` is a one-block-long eaves roof whose
        rise is the profile turned outside in and whose depth is the
        vault's, outermost first: the same pieces, laid by the same loop,
        with a stair wherever a course falls a block, facing the crown,
        and a springer wherever a course is thickened past its reach. The
        eave picks the crown's half block: odd for every vault."""
        nave = dict(half_span=7, pitch=1, depth=(1, 1, 1, 2, 1, 1, 1, 4),
                    slab="minecraft:cobblestone_slab",
                    block="minecraft:cobblestone",
                    stair="minecraft:cobblestone_stairs",
                    springer="minecraft:cobblestone_slab[type=double]")
        vaults = {
            "nave z": Vault((20, 84, 10), **nave),
            "nave x": Vault((20, 84, 10), axis="x", **nave),
            "mixed": Vault((20, 84, 10), half_span=6, pitch=(1, 2, 1, 3, 2, 4)),
            "arch": Vault((20, 90, 10), profile=arch_profile(9, 9, eaves=2)),
            "pointed": Vault((20, 90, 10), profile=pointed_arch_profile(7, 12)),
            "depth": Vault((20, 84, 10), half_span=3, pitch=2,
                           depth=(1, 3, 2, 5), springer="minecraft:stone"),
        }
        for label, vault in vaults.items():
            with self.subTest(label):
                eave = 2 if vault.profile[-1] % 2 else 1
                base_y = (2 * vault.crown_y + 1 - eave - vault.profile[-1]) // 2
                half = vault.half_span
                if vault.axis == "z":
                    outline = (vault.across - half, 5, vault.across + half, 5)
                    eaves = ("west", "east")
                else:
                    outline = (5, vault.across - half, 5, vault.across + half)
                    eaves = ("north", "south")
                roof = Roof(eaves=eaves, rise=rise_of(vault.profile, eave=eave),
                            depth=(None if vault.depth is None
                                   else tuple(reversed(vault.depth))),
                            block=vault.block, slab=vault.slab,
                            stair=vault.stair, springer=vault.springer)
                plan = roof.plan(outline, base_y, attic="hollow")
                section = {vault.cell(5, offset, y): state
                           for (offset, y), state in vault.section().items()}
                self.assertSamePlan(section, plan, label)


class RuleTests(unittest.TestCase):
    def test_a_roof_below_zero_is_the_same_roof(self):
        """The superflat worlds build below y=0 -- the Roman stables'
        roof climbs from y=-55 -- and a roof there is the roof at y=70
        moved down, cell for cell: a rise starting on a half block, a
        filled attic and closed ends included, whose springing is the
        floor of a half-block height. Halving it toward zero instead lifts
        the springing a block below y=0 and drops the attic's lowest
        course."""
        shapes = (("square", dict(rings="square"), (0, 0, 8, 8)),
                  ("gable", dict(eaves=("north", "south"),
                                 close_ends=STONE), (0, 0, 8, 6)),
                  ("round", dict(rings="round"), (4, 4, 4)))
        for name, kind, outline in shapes:
            roof = Roof(**kind, rise=(3, 4, 5, 6), block=STONE,
                        slab="minecraft:stone_brick_slab",
                        stair="minecraft:stone_brick_stairs")
            for attic in ("hollow", STONE):
                with self.subTest(shape=name, attic=attic):
                    high = roof.plan(outline, 70, attic=attic)
                    low = roof.plan(outline, -55, attic=attic)
                    self.assertEqual(
                        list(low.items()),
                        [((x, y - 125, z), state)
                         for (x, y, z), state in high.items()])

    def test_no_column_stands_over_a_gap(self):
        """Every column reaches down to the top of each neighbor lower
        than it, for upturns, mansards, tiers, a half-block ramp, a steep
        pitch and a dome, on square and round rings and on eaves; and no
        column has a hole in it."""
        rises = {"upturn": dict(rise=(4, 2, 4, 6, 8)),
                 "mansard": dict(rise=(4, 8, 12)),
                 "tiers": dict(rise=(2, 4, 6, 14, 16, 18)),
                 "ramp": dict(pitch=1), "steep": dict(pitch=4),
                 "dome": dict(rise=rise_of(arch_profile(6, 5)))}
        shapes = {"square": (dict(rings="square"), (0, 0, 12, 9)),
                  "round": (dict(rings="round"), (0, 0, 6)),
                  "gable": (dict(eaves=("north", "south")), (0, 0, 8, 11)),
                  "shed": (dict(eaves="west"), (0, 0, 9, 4))}
        for name, rise in rises.items():
            for shape, (kind, outline) in shapes.items():
                for crown in ("slab", "block"):
                    with self.subTest(rise=name, shape=shape, crown=crown):
                        plan = Roof(**kind, **rise, overhang=1, crown=crown,
                                    block=STONE,
                                    slab="minecraft:stone_brick_slab",
                                    stair="minecraft:stone_brick_stairs").plan(
                            outline, 70, attic="hollow")
                        spans = _columns(plan)
                        for (x, z), (bottom, top) in spans.items():
                            for dx, dz in COMPASS.values():
                                near = spans.get((x + dx, z + dz))
                                if near is not None and near[1] < top:
                                    self.assertLessEqual(bottom, near[1],
                                                         (x, z))

    def test_a_crown_ends_on_a_slab_unless_told_otherwise(self):
        def gable(z1, crown="slab", pitch=2):
            return Roof(eaves=("north", "south"), pitch=pitch, overhang=1,
                        crown=crown, block=PLANK, slab=SLAB,
                        stair=STAIR).plan((0, 0, 3, z1), 70, attic="hollow")
        # An odd span: the ridge is a bottom slab half a block over the
        # stairs beside it, or a whole block when told so.
        plan = gable(2)
        ridge = [state for (x, y, z), state in plan.items() if z == 1]
        self.assertEqual({str(s) for s in ridge},
                         {f"{SLAB}[type=bottom]"})
        self.assertEqual({y for (x, y, z) in plan if z == 1}, {72})
        plan = gable(2, crown="block")
        self.assertEqual({str(s) for (x, y, z), s in plan.items() if z == 1},
                         {PLANK})
        # An even span meets in two stairs facing each other.
        plan = gable(1)
        top = {z: plan[(2, 71, z)].properties["facing"] for z in (0, 1)}
        self.assertEqual(top, {0: "south", 1: "north"})
        # A half-block ramp's ridge would sink to its neighbors' height, so
        # it keeps its whole-block top: a top slab, half a block over them.
        plan = gable(2, pitch=1)
        self.assertEqual(str(plan[(2, 71, 1)]), f"{SLAB}[type=top]")
        self.assertEqual(str(plan[(2, 71, 0)]), f"{SLAB}[type=bottom]")
        # A cone's apex, the same way.
        for crown, want in (("slab", f"{SLAB}[type=bottom]"), ("block", PLANK)):
            plan = Roof(rings="round", pitch=2, crown=crown, block=PLANK,
                        slab=SLAB, stair=None).plan((0, 0, 3), 70,
                                                    attic="hollow")
            self.assertEqual(str(plan[(0, 73, 0)]), want)

    def test_a_hip_stair_faces_the_center_and_a_diagonal_goes_to_z(self):
        """With no ``hip`` block a corner takes a stair facing the
        outline's center along the axis it lies more along: east on a roof
        wider than it is deep, south on a deeper one, and south -- toward
        z, as the castle breaks a diagonal -- on a square one."""
        def corner(outline):
            plan = Roof(rings="square", pitch=2, block=STONE, slab=None,
                        stair="minecraft:stone_brick_stairs",
                        crown="block").plan(outline, 70, attic="hollow")
            x0, z0, x1, z1 = outline
            return {(x, z): plan[(x, 70, z)].properties["facing"]
                    for x in (x0, x1) for z in (z0, z1)}
        self.assertEqual(corner((0, 0, 6, 4)),
                         {(0, 0): "east", (0, 4): "east",
                          (6, 0): "west", (6, 4): "west"})
        self.assertEqual(corner((0, 0, 4, 6)),
                         {(0, 0): "south", (4, 0): "south",
                          (0, 6): "north", (4, 6): "north"})
        self.assertEqual(corner((0, 0, 4, 4)),
                         {(0, 0): "south", (4, 0): "south",
                          (0, 4): "north", (4, 4): "north"})
        # hip= puts its block there instead.
        plan = Roof(rings="square", pitch=2, block=STONE, slab=None,
                    stair="minecraft:stone_brick_stairs", crown="block",
                    hip="minecraft:chiseled_stone_bricks").plan(
            (0, 0, 4, 4), 70, attic="hollow")
        self.assertEqual(str(plan[(0, 70, 0)]), "minecraft:chiseled_stone_bricks")
        self.assertEqual(plan[(1, 70, 0)].properties["facing"], "south")

    def test_an_upturned_eave_turns_its_tips_and_corners_out(self):
        """``rise=(4, 2, 4, 6, 8)``: the outer ring stands a block over the
        ring inside it, so its stairs face out, and its corners face away
        from the center; the valley ring is a whole block, falling
        nowhere."""
        plan = Roof(rings="square", rise=(4, 2, 4, 6, 8), block=STONE,
                    slab="minecraft:stone_brick_slab",
                    stair="minecraft:stone_brick_stairs").plan(
            (0, 0, 8, 8), 70, attic="hollow")
        facing = {(x, z): plan[(x, 71, z)].properties.get("facing")
                  for x in range(9) for z in range(9)
                  if min(x, 8 - x, z, 8 - z) == 0}
        for (x, z), word in facing.items():
            corner = x in (0, 8) and z in (0, 8)
            if corner:
                self.assertEqual(word, "north" if z == 0 else "south", (x, z))
            elif z == 0:
                self.assertEqual(word, "north", (x, z))
            elif z == 8:
                self.assertEqual(word, "south", (x, z))
            else:
                self.assertEqual(word, "west" if x == 0 else "east", (x, z))
        valley = {str(plan[(x, 70, z)]) for x in range(1, 8) for z in range(1, 8)
                  if min(x - 1, 7 - x, z - 1, 7 - z) == 0}
        self.assertEqual(valley, {STONE})
        self.assertNotIn((1, 71, 1), plan)
        # Ring 2 climbs from the valley again, facing in.
        self.assertEqual(plan[(4, 71, 2)].properties["facing"], "south")

    def test_no_stair_faces_down_its_slope(self):
        """Whatever the rise does -- climb, turn up, plateau, or peak on a
        ring and fall again inside it -- no stair faces a neighbor it falls
        a block or more to. A ring that peaks falls outward from its
        corners, as every ring of a plain hip does, so they face the
        center; the rise says the ring inside is lower too, which a corner
        touches only across its diagonal."""
        rises = {"pitch": dict(pitch=2), "peak": dict(rise=(2, 4, 6, 4, 2)),
                 "spike": dict(rise=(2, 6, 4)), "wave": dict(rise=(2, 6, 2, 6, 2, 6)),
                 "upturn": dict(rise=(4, 2, 4, 6, 8)),
                 "mansard": dict(rise=(2, 6, 10)), "plateau": dict(rise=(2, 2, 4, 6)),
                 "edge up": dict(rise=(6, 4, 2)),
                 "dome": dict(rise=rise_of(arch_profile(6, 5)))}
        shapes = {"square": (dict(rings="square"),
                             ((0, 0, 12, 9), (0, 0, 5, 5), (0, 0, 6, 2))),
                  "round": (dict(rings="round"), ((0, 0, 6), (0, 0, 2))),
                  "gable": (dict(eaves=("north", "south")), ((0, 0, 8, 11),)),
                  "shed": (dict(eaves="west"), ((0, 0, 9, 4),))}
        seen = 0
        for name, rise in rises.items():
            for shape, (kind, outlines) in shapes.items():
                for outline in outlines:
                    for overhang in (0, 1):
                        plan = Roof(**kind, **rise, overhang=overhang,
                                    block=STONE,
                                    slab="minecraft:stone_brick_slab",
                                    stair="minecraft:stone_brick_stairs").plan(
                            outline, 70, attic="hollow")
                        tops = {column: top for column, (_, top)
                                in _columns(plan).items()}
                        for (x, y, z), state in plan.items():
                            if (not state.name.endswith("_stairs")
                                    or 2 * y + 2 != tops[(x, z)]):
                                continue
                            seen += 1
                            dx, dz = COMPASS[state.properties["facing"]]
                            front = tops.get((x + dx, z + dz))
                            with self.subTest(rise=name, outline=outline,
                                              overhang=overhang, cell=(x, y, z)):
                                self.assertFalse(
                                    front is not None and front <= tops[(x, z)] - 2,
                                    f"faces {state.properties['facing']} down "
                                    f"to {front} from {tops[(x, z)]}")
        self.assertGreater(seen, 5000)
        # The innermost ring of a peaked rise: its corners fall north and
        # west, or west and south, and face up the slope along z.
        plan = Roof(rings="square", rise=(2, 4, 6, 4, 2), block=STONE,
                    slab="minecraft:stone_brick_slab",
                    stair="minecraft:stone_brick_stairs").plan(
            (0, 0, 5, 5), 70, attic="hollow")
        self.assertEqual({(x, z): plan[(x, 72, z)].properties["facing"]
                          for x in (2, 3) for z in (2, 3)},
                         {(2, 2): "south", (3, 2): "south",
                          (2, 3): "north", (3, 3): "north"})

    def test_a_corner_on_a_plateau_faces_the_center(self):
        """A ring no higher than the ring inside it is no eave turned up,
        so its corners face the center, as a hip's do: on the outline,
        where a flat border runs round before the roof climbs, and on a
        mansard's top ring, where the roof goes flat. Nor is a ring with no
        ring inside it, however its rise would fall past it."""
        def corners(rise, outline, y, ring):
            plan = Roof(rings="square", rise=rise, block=STONE,
                        slab="minecraft:stone_brick_slab",
                        stair="minecraft:stone_brick_stairs").plan(
                outline, 70, attic="hollow")
            x0, z0, x1, z1 = outline
            return {(x, z): plan[(x, y, z)].properties.get("facing")
                    for x in (x0 + ring, x1 - ring)
                    for z in (z0 + ring, z1 - ring)}
        # On a wide roof the center lies more along x; on a square one an
        # exact diagonal goes to z.
        self.assertEqual(corners((2, 2, 4, 6), (0, 0, 10, 6), 70, 0),
                         {(0, 0): "east", (0, 6): "east",
                          (10, 0): "west", (10, 6): "west"})
        self.assertEqual(corners((2, 2, 4, 6), (0, 0, 6, 6), 70, 0),
                         {(0, 0): "south", (6, 0): "south",
                          (0, 6): "north", (6, 6): "north"})
        for rise in ((2, 6, 10), (4, 8, 12)):
            with self.subTest(rise=rise):
                self.assertEqual(corners(rise, (0, 0, 10, 8), 70 + rise[2] // 2 - 1,
                                         2),
                                 {(2, 2): "east", (2, 6): "east",
                                  (8, 2): "west", (8, 6): "west"})
        for outline, want in (((0, 0, 5, 1), {(0, 0): "east", (0, 1): "east",
                                              (5, 0): "west", (5, 1): "west"}),
                              ((0, 0, 1, 1), {(0, 0): "south", (1, 0): "south",
                                              (0, 1): "north", (1, 1): "north"})):
            with self.subTest(outline=outline):
                self.assertEqual(corners((4, 2), outline, 71, 0), want)

    def test_a_missing_slab_is_named_where_the_write_first_needs_it(self):
        """``slab=None`` on a roof that needs one names the first cell in
        write order that does -- the lowest course's, first along it --
        and where a column's lowest piece is the half block, that piece
        is the one named, not the crown above it."""
        def named(roof, outline):
            with self.assertRaises(NBTError) as caught:
                roof.plan(outline, 70, attic="hollow")
            return str(caught.exception)
        def first(kind, rise, outline):
            plan = Roof(**kind, **rise, block=STONE,
                        slab="minecraft:stone_brick_slab", stair=None).plan(
                outline, 70, attic="hollow")
            return next(cell for cell, state in plan.items()
                        if state.name.endswith("_slab"))
        cases = ((dict(rings="square"), dict(pitch=1), (0, 0, 9, 7)),
                 (dict(rings="round"), dict(pitch=1), (0, 0, 4)),
                 (dict(eaves="west"), dict(rise=(2, 7, 6, 3)), (0, 0, 9, 7)))
        for kind, rise, outline in cases:
            with self.subTest(**kind, **rise):
                cell = first(kind, rise, outline)
                self.assertIn(f"slab at {cell}", named(
                    Roof(**kind, **rise, block=STONE, slab=None, stair=None),
                    outline))
        # Every ring of the pitch-1 hip lacks one; the first is ring 1's
        # northwest corner, a bottom slab on the course over the eave.
        self.assertEqual(first(dict(rings="square"), dict(pitch=1),
                               (0, 0, 9, 7)), (1, 71, 1))
        # The shed's second column ends on a bottom slab at y=73, its third
        # stands on a top slab at y=71: the third is written first.
        message = named(Roof(eaves="west", rise=(2, 7, 6, 3), block=STONE,
                             slab=None, stair=None), (0, 0, 9, 7))
        self.assertIn("slab at (2, 71, 0) -- half a block is left to lay",
                      message)

    def test_a_material_roofs_the_whole_course(self):
        brick = Material("minecraft:bricks")
        roof = Roof(rings="square", pitch=2, block=brick, slab=brick,
                    stair=brick)
        self.assertEqual((roof.block.name, roof.slab.name, roof.stair.name),
                         ("minecraft:bricks", "minecraft:brick_slab",
                          "minecraft:brick_stairs"))
        with self.assertRaises(IllegalBlock):
            Roof(rings="square", block=STONE, slab=None,
                 stair=Material("minecraft:calcite"))

    def test_a_mix_is_picked_at_the_world_cell(self):
        """A whole-block role may be a Mix, picked at each world cell as
        the plan is made -- so what is planned is what is written, and the
        same roof planned twice is the same roof. A role the rule spells a
        facing or a type onto may not."""
        tiles = Mix([("minecraft:granite", 3), ("minecraft:polished_granite", 2),
                     ("minecraft:terracotta", 1)], seed=4)
        roof = Roof(rings="square", rise=(2, 4, 6, 8), crown="block",
                    block=tiles, slab=None, stair=None, hip=tiles)
        plan = roof.plan((0, 0, 9, 9), 70, attic=tiles)
        self.assertEqual(plan, roof.plan((0, 0, 9, 9), 70, attic=tiles))
        for cell, state in plan.items():
            self.assertEqual(state, BlockState.parse(tiles.pick(*cell)), cell)
        self.assertGreater(len({str(state) for state in plan.values()}), 1)
        for arg in ("stair", "slab"):
            with self.subTest(arg):
                with self.assertRaises(TypeError):
                    Roof(rings="square", block=STONE,
                         **{"slab": None, "stair": None, arg: tiles})

    def test_eaves_and_rings_are_checked(self):
        base = dict(block=STONE, slab=None, stair=None)
        bad = {
            "both": dict(eaves="north", rings="square"),
            "neither": dict(),
            "adjacent": dict(eaves=("north", "east")),
            "twice": dict(eaves=("north", "north")),
            "unknown": dict(eaves="up"),
            "three": dict(eaves=("north", "south", "east")),
            "rings": dict(rings="hexagon"),
            "hip on eaves": dict(eaves="north", hip=STONE),
            "ends on rings": dict(rings="round", close_ends=STONE),
            "ends of air": dict(eaves="north", close_ends=AIR),
            "ends a switch": dict(eaves="north", close_ends=True),
            "pitch and rise": dict(rings="square", pitch=2, rise=(2, 4)),
            "pitch falls": dict(rings="square", pitch=-1),
            "rise of 0": dict(rings="square", rise=(2, 0)),
            "rise per eave on rings": dict(rings="square", rise={"north": (2,)}),
            "rise for the wrong eave": dict(eaves=("north", "south"),
                                            rise={"north": (2,), "west": (2,)}),
            "overhang": dict(rings="square", overhang=-1),
            "crown": dict(rings="square", crown="dome"),
            "depth of 0": dict(rings="square", depth=(1, 0)),
        }
        for label, args in bad.items():
            with self.subTest(label):
                with self.assertRaises(NBTError):
                    Roof(**{**base, **args})
        roof = Roof(rings="round", **base)
        for outline in ((0, 0), (0, 0, -1), (0, 0, 2.5), "abc"):
            with self.subTest(outline=outline):
                with self.assertRaises(NBTError):
                    roof.plan(outline, 70, attic="hollow")
        with self.assertRaises(NBTError):
            Roof(rings="square", **base).plan((0, 0, 2), 70, attic="hollow")

    def test_a_roof_says_which_role_it_lacks(self):
        """``block``, ``slab`` and ``stair`` are always named -- None for
        no slab or no stairs -- and each is held to its shape where the
        game has the block: a slab is a slab, a stair a stair."""
        with self.assertRaises(TypeError):
            Roof(rings="square", block=STONE)           # slab and stair unsaid
        with self.assertRaises(TypeError):
            Roof(rings="square", block=None, slab=None, stair=None)
        with self.assertRaises(TypeError):
            Roof(rings="square", block=FROM_PALETTE, slab=None, stair=None)
        with self.assertRaises(NBTError) as caught:
            Roof(rings="round", pitch=2, block=STONE, slab=None,
                 stair=None).plan((0, 0, 3), 70, attic="hollow")
        self.assertIn("slab", str(caught.exception))
        self.assertIn("(0, 73, 0)", str(caught.exception))
        for args in (dict(slab="minecraft:oak_stairs", stair=None),
                     dict(slab=None, stair="minecraft:oak_planks"),
                     dict(slab=None, stair="minecraft:oak_trapdoor")):
            with self.subTest(**args):
                with self.assertRaises(NBTError) as caught:
                    Roof(rings="square", block=STONE, **args)
                self.assertNotIsInstance(caught.exception, Refused)


TILE_SLAB = "minecraft:stone_brick_slab"
TILE_STAIR = "minecraft:stone_brick_stairs"


def _rect(x0, z0, x1, z1) -> frozenset:
    """A rectangle's columns, inclusive: a footprint."""
    return frozenset((x, z) for x in range(x0, x1 + 1)
                     for z in range(z0, z1 + 1))


def _hip(**settings) -> Roof:
    """A stone-brick hip, pitch 2 unless told otherwise."""
    return Roof(**{"rings": "square", "pitch": 2, "block": STONE,
                   "slab": TILE_SLAB, "stair": TILE_STAIR, **settings})


def _tops(plan) -> dict:
    """``{(x, z): (y, state)}``: each column's top block in a plan."""
    tops: dict = {}
    for (x, y, z), state in plan.items():
        if (x, z) not in tops or y > tops[(x, z)][0]:
            tops[(x, z)] = (y, state)
    return tops


def _gaps(plan) -> list:
    """Every column standing over a gap: its lowest piece above the top
    of a lower neighbor beside it."""
    spans = _columns(plan)
    found = []
    for (x, z), (bottom, top) in sorted(spans.items()):
        for dx, dz in COMPASS.values():
            near = spans.get((x + dx, z + dz))
            if near is not None and near[1] < top and bottom > near[1]:
                found.append((x, z))
    return found


def _troughs(plan) -> list:
    """Every column both of whose neighbors along x, or along z, stand
    higher than it: a trough, which no hip has."""
    spans = _columns(plan)
    found = set()
    for (x, z), (_, top) in spans.items():
        for dx, dz in ((1, 0), (0, 1)):
            a = spans.get((x - dx, z - dz))
            b = spans.get((x + dx, z + dz))
            if a is not None and b is not None and a[1] > top and b[1] > top:
                found.add((x, z))
    return sorted(found)


def _ordered(plan) -> list:
    """A plan as the bytes see it: each cell in write order, with its
    state's name and its properties in the order they are spelled."""
    return [(cell, state.name, list(state.properties.items()))
            for cell, state in plan.items()]


class FootprintTests(unittest.TestCase):
    """A hip over any plan: ``rings="square"`` over a footprint, a set of
    columns, each column's ring how many steps a king takes to walk out
    of the plan, less one."""

    def test_a_rectangle_given_as_its_cells_is_the_same_roof(self):
        """Every rectangle 1..7 by 1..7, overhang 0 and 1, six rises, hip
        block or hip stairs: the footprint plans the same cells in the
        same order with the same states, their properties spelled in the
        same order -- the bytes -- and Builder.roof counts and saves the
        same. (A sweep of 21,168 such plans, overhang 0..2 and sizes to
        14, found no difference either.)"""
        rises = {"pitch 2": dict(pitch=2), "pitch 1": dict(pitch=1),
                 "peak": dict(rise=(2, 4, 6, 4, 2)),
                 "upturn": dict(rise=(4, 2, 4, 6, 8)),
                 "wave": dict(rise=(2, 6, 2, 6, 2, 6)),
                 "plateau": dict(rise=(2, 2, 4, 6))}
        for name, rise in rises.items():
            for hip in (None, "minecraft:chiseled_stone_bricks"):
                for overhang in (0, 1):
                    roof = _hip(**{"pitch": None, **rise},
                                overhang=overhang, hip=hip)
                    for width in range(1, 8):
                        for length in range(1, 8):
                            outline = (3, -2, 3 + width - 1, -2 + length - 1)
                            with self.subTest(rise=name, hip=hip,
                                              overhang=overhang,
                                              outline=outline):
                                self.assertEqual(
                                    _ordered(roof.plan(_rect(*outline), 70,
                                                       attic="hollow")),
                                    _ordered(roof.plan(outline, 70,
                                                       attic="hollow")))
        # A filled attic and its floor, through Builder.roof into two
        # worlds: the same result, and the same bytes saved.
        for roof, outline, attic, floor in (
                (_hip(overhang=1), (2, 2, 12, 9), STONE, None),
                (_hip(rise=(4, 2, 4, 6, 8), pitch=None), (3, 3, 10, 10),
                 "minecraft:dirt", 68),
                (_hip(pitch=1, hip=STONE), (5, 2, 9, 13), "hollow", None)):
            results, digests = [], []
            for given in (outline, _rect(*outline)):
                directory = tempfile.mkdtemp(prefix="roof-footprint-")
                self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
                make_save(directory, [(0, 0)])
                save = MinecraftSave.load(directory)
                result = Builder(save).roof(roof, given, 70, attic=attic,
                                            attic_floor=floor)
                results.append(repr(result))
                save.save()
                digests.append(_payloads(directory))
            with self.subTest(outline=outline, attic=attic):
                self.assertEqual(results[0], results[1])
                self.assertEqual(digests[0], digests[1])

    def test_an_overhang_widens_a_plan_as_a_king_walks(self):
        """An L's walls widened by the overhang: every column within it of
        a wall both ways -- the diagonal outside the reentrant corner
        included -- each column's ring the king's walk out of the widened
        plan, less one, and the attic filled under the walls alone."""
        walls = _rect(0, 0, 20, 8) | _rect(0, 0, 8, 20)
        for overhang in (0, 1, 2):
            with self.subTest(overhang=overhang):
                roof = Roof(rings="square", pitch=2, overhang=overhang,
                            crown="block", block=STONE, slab=None,
                            stair=None)
                plan = roof.plan(walls, 70, attic="minecraft:dirt")
                reach = range(-overhang, overhang + 1)
                roofed = {(x + dx, z + dz) for x, z in walls
                          for dx in reach for dz in reach}
                self.assertEqual({(x, z) for x, _, z in plan}, roofed)
                self.assertEqual((9, 9) in roofed, overhang > 0)
                rings = chessboard(roofed)
                tops = {column: top for column, (_, top)
                        in _columns(plan).items()}
                self.assertEqual(tops, {column: 142 + 2 * (distance - 1)
                                        for column, distance in rings.items()})
                filled = {(x, z) for (x, _, z), state in plan.items()
                          if state.name == "minecraft:dirt"}
                # Every wall stands at least a ring in from an overhang's
                # edge, so has attic under its shell; with none, the walls
                # on the edge are eaves at the springing, with none.
                self.assertEqual(filled, {column for column in walls
                                          if rings[column] > 1})
                self.assertEqual(filled == set(walls), overhang > 0)

    def test_a_valley_runs_straight_from_its_corner(self):
        """An L with arms nine wide: from the reentrant corner (8, 8) the
        diagonal climbs a ring a column, a straight 45-degree valley of
        whole blocks with no lower side neighbor and no stair, to ring 4,
        where it meets the two ridges and the hip from the outer corner --
        a hip stair, as that hip's are. The stairs beside the valley face
        up their slopes, away from it. A Euclidean distance would put a
        bump in it: its floor at (6, 6) is a ring higher than the king's
        walk, a column standing over the valley on either side."""
        walls = _rect(0, 0, 20, 8) | _rect(0, 0, 8, 20)
        plan = _hip().plan(walls, 70, attic="hollow")
        tops = _tops(plan)
        heights = {column: top for column, (_, top)
                   in _columns(plan).items()}
        for k in range(5):
            self.assertEqual(chessboard(walls)[(8 - k, 8 - k)] - 1, k)
            self.assertEqual(tops[(8 - k, 8 - k)][0], 70 + k, k)
        for k in range(4):
            cell = (8 - k, 8 - k)
            with self.subTest(k=k):
                self.assertEqual(str(tops[cell][1]), STONE)
                self.assertTrue(all(
                    heights[(cell[0] + dx, cell[1] + dz)] >= heights[cell]
                    for dx, dz in COMPASS.values()))
        self.assertEqual(str(tops[(4, 4)][1]),
                         f"{TILE_STAIR}[facing=south,half=bottom]")
        self.assertEqual(str(tops[(3, 3)][1]),
                         f"{TILE_STAIR}[facing=south,half=bottom]")
        for k in range(5, 8):
            with self.subTest(k=k):
                self.assertEqual(tops[(k + 1, k)][1].properties["facing"],
                                 "north")
                self.assertEqual(tops[(k, k + 1)][1].properties["facing"],
                                 "west")
        # The lake's metric, which this is not: its floor at k=2 is ring 3.
        self.assertEqual(math.floor(distance_to_edge(walls)[(6, 6)]) - 1, 3)
        self.assertEqual(_troughs(plan), [])

    def test_a_hip_over_a_plan_is_one_roof_however_it_is_cut(self):
        """The footprint's hip over an L, a T and a cross is the hips of
        its rectangles joined, state for state: where wings overlap to
        each other's far edges the distance field and the join are one
        roof. Where two only touch -- a bar against a square's side --
        they part: the join leaves a trough along the seam, three columns
        lower than both their neighbors across it, and the footprint
        none."""
        roof = _hip()
        plans = {"L": [(0, 0, 20, 6), (0, 0, 6, 18)],
                 "T": [(0, 0, 24, 6), (9, 0, 15, 18)],
                 "cross": [(0, 8, 26, 14), (10, 0, 16, 24)]}
        for name, rects in plans.items():
            with self.subTest(plan=name):
                footprint = frozenset().union(*(_rect(*r) for r in rects))
                one = roof.plan(footprint, 70, attic="hollow")
                joined = Wings([(roof, r, 70) for r in rects]).plan(
                    attic="hollow")
                self.assertEqual(one, joined)
                self.assertEqual(_gaps(one), [])
                self.assertEqual(_troughs(one), [])
        rects = [(0, 0, 10, 10), (10, 3, 20, 7)]
        footprint = _rect(*rects[0]) | _rect(*rects[1])
        self.assertEqual(_troughs(roof.plan(footprint, 70, attic="hollow")),
                         [])
        self.assertEqual(_troughs(Wings([(roof, r, 70) for r in rects]).plan(
            attic="hollow")), [(10, 4), (10, 5), (10, 6)])

    def test_a_courtyard_slopes_into_its_opening(self):
        """A plan with a 5 by 5 hole: the hole is left open, every column
        round it an eave at y=70 with its stair facing up the slope, away
        from the opening, the shell gapless, and the attic under the
        walls and never in the hole. An overhang of 1 narrows the opening
        to 3 by 3. One that would roof a courtyard over entirely is
        refused, naming its first column."""
        hole = _rect(8, 8, 12, 12)
        walls = _rect(0, 0, 20, 30) - hole
        plan = _hip().plan(walls, 70, attic="minecraft:dirt")
        tops = _tops(plan)
        self.assertEqual(set(tops), set(walls))
        for x in range(8, 13):
            self.assertEqual((tops[(x, 7)][0], tops[(x, 7)][1].properties[
                "facing"]), (70, "north"))
            self.assertEqual((tops[(x, 13)][0], tops[(x, 13)][1].properties[
                "facing"]), (70, "south"))
        for z in range(8, 13):
            self.assertEqual(tops[(7, z)][1].properties["facing"], "west")
            self.assertEqual(tops[(13, z)][1].properties["facing"], "east")
        self.assertEqual(_gaps(plan), [])
        filled = {(x, z) for (x, _, z), state in plan.items()
                  if state.name == "minecraft:dirt"}
        rings = chessboard(walls)
        self.assertEqual(filled, {c for c in walls if rings[c] > 1})
        self.assertFalse(filled & hole)
        wider = _hip(overhang=1).plan(walls, 70, attic="hollow")
        self.assertEqual(hole - {(x, z) for x, _, z in wider},
                         _rect(9, 9, 11, 11))
        pierced = _rect(0, 0, 8, 8) - {(4, 4)}
        self.assertNotIn((4, 70, 4), _hip().plan(pierced, 70,
                                                 attic="hollow"))
        with self.assertRaises(NBTError) as caught:
            _hip(overhang=1).plan(pierced, 70, attic="hollow")
        self.assertIn("(4, 4)", str(caught.exception))
        self.assertIn("courtyard", str(caught.exception))
        # Two walls meeting only at a corner close one as surely: a gap
        # nobody could walk through is no way out.
        ring = _rect(0, 0, 6, 6) - _rect(1, 1, 5, 5) - {(6, 6)}
        self.assertNotIn((3, 70, 3), _hip().plan(ring, 70, attic="hollow"))
        with self.assertRaises(NBTError) as caught:
            _hip(overhang=3).plan(ring, 70, attic="hollow")
        self.assertIn("(1, 1)", str(caught.exception))

    def test_a_corner_of_a_plan_faces_along_its_longer_run(self):
        """A footprint has no center to face -- a U's lies in its gap --
        so a hip corner faces up its slope along the axis the roof runs
        longer through it: the U's corner (0, 16), whose row runs 23 and
        whose column 17, faces east, and (0, 0), whose row runs 7, faces
        south. A rectangle's corners are where they were: the longer way
        is the way its center lies more."""
        u = _rect(0, 0, 6, 16) | _rect(0, 10, 22, 16) | _rect(16, 0, 22, 16)
        plan = _hip().plan(u, 70, attic="hollow")
        self.assertEqual(plan[(0, 70, 16)].properties["facing"], "east")
        self.assertEqual(plan[(0, 70, 0)].properties["facing"], "south")
        self.assertEqual(plan[(22, 70, 16)].properties["facing"], "west")
        self.assertEqual(plan[(22, 70, 0)].properties["facing"], "south")
        for outline, want in (
                ((0, 0, 6, 4), {(0, 0): "east", (0, 4): "east",
                                (6, 0): "west", (6, 4): "west"}),
                ((0, 0, 4, 6), {(0, 0): "south", (4, 0): "south",
                                (0, 6): "north", (4, 6): "north"}),
                ((0, 0, 4, 4), {(0, 0): "south", (4, 0): "south",
                                (0, 4): "north", (4, 4): "north"})):
            plan = _hip(crown="block").plan(_rect(*outline), 70,
                                            attic="hollow")
            with self.subTest(outline=outline):
                self.assertEqual({(x, z): plan[(x, 70, z)].properties["facing"]
                                  for x, z in want}, want)
        # Where the join's corner faces by the center of the rectangle it
        # falls in, the footprint's faces by its runs: a U's two corners
        # and the two hip cells inside them.
        rects = [(0, 0, 6, 16), (0, 10, 22, 16), (16, 0, 22, 16)]
        joined = Wings([(_hip(), r, 70) for r in rects]).plan(attic="hollow")
        plan = _hip().plan(u, 70, attic="hollow")
        self.assertEqual(sorted(cell for cell in plan
                                if plan[cell] != joined[cell]),
                         [(0, 70, 16), (1, 71, 15), (2, 72, 14), (3, 73, 13)])

    def test_only_square_rings_take_a_plan(self):
        """A footprint is a hip's: an eaves roof or a round one given one
        is refused, naming Wings, which joins several; so is an empty
        footprint and a member that is not a pair of whole numbers. A list
        of lists is a plan too, and so is what ``geometry.disc`` returns,
        while four or three whole numbers are a rectangle or a disc as
        they always were."""
        for kind in (dict(eaves=("north", "south")), dict(eaves="west"),
                     dict(rings="round")):
            with self.subTest(**kind):
                with self.assertRaises(NBTError) as caught:
                    Roof(**kind, block=STONE, slab=TILE_SLAB,
                         stair=TILE_STAIR).plan(_rect(0, 0, 4, 4), 70,
                                                attic="hollow")
                self.assertIn("Wings", str(caught.exception))
        for bad in (frozenset(), set(), [(0, 0), 5], [(0, 0), (1, 2, 3)],
                    {(0, 0), (True, 1)}, [(0, 0), ("a", 1)],
                    ((0, 0), (0.5, 1)), [(0, 0), "ab"]):
            with self.subTest(footprint=bad):
                with self.assertRaises(NBTError):
                    _hip().plan(bad, 70, attic="hollow")
        square = _hip().plan((0, 0, 1, 1), 70, attic="hollow")
        self.assertEqual(_hip().plan([[0, 0], [0, 1], [1, 0], [1, 1]], 70,
                                     attic="hollow"), square)
        self.assertEqual(_hip().plan(((0, 0), (1, 1), (0, 1), (1, 0)), 70,
                                     attic="hollow"), square)
        self.assertEqual(_hip().plan(disc(3), 70, attic="hollow"),
                         _hip().plan(frozenset(disc(3)), 70, attic="hollow"))
        self.assertEqual(_hip().plan([0, 0, 1, 1], 70, attic="hollow"),
                         square)

    def test_two_corners_are_not_two_columns(self):
        """A list or a tuple of two columns reads as a rectangle's two
        corners just as plainly, so where the two readings are different
        roofs it is refused, by any roof, naming the rectangle's form and
        the set's: never silently two lone columns. Side by side the two
        readings are one roof, and a set has no order to be corners in."""
        gable = Roof(eaves=("north", "south"), block=STONE, slab=TILE_SLAB,
                     stair=TILE_STAIR)
        for outline in (((0, 0), (10, 8)), [(0, 0), (1, 1)],
                        [[3, 4], [0, 9]]):
            for roof in (_hip(), gable):
                with self.subTest(outline=outline, roof=roof):
                    with self.assertRaises(NBTError) as caught:
                        roof.plan(outline, 70, attic="hollow")
                    self.assertIn("two corners", str(caught.exception))
                    self.assertIn("(x0, z0, x1, z1)", str(caught.exception))
        with self.assertRaises(NBTError) as caught:
            Wings([(_hip(), [[3, 4], [0, 9]], 70)])
        self.assertIn("(3, 4, 0, 9)", str(caught.exception))
        self.assertEqual(_hip().plan([(0, 0), (0, 1)], 70, attic="hollow"),
                         _hip().plan((0, 0, 0, 1), 70, attic="hollow"))
        self.assertEqual({(x, z) for x, _, z in _hip().plan(
            {(0, 0), (10, 8)}, 70, attic="hollow")}, {(0, 0), (10, 8)})

    def test_an_eave_turns_up_only_over_the_ring_inside_it(self):
        """With rise (4, 2, 4, 6, 8) an eave stands a block over the ring
        inside it and turns up: its stairs face out. A two-wide arm off a
        deep block has no ring inside its own eave, so the corners at its
        tip face in, as its sides do and as the arm alone does -- the
        block's ring 1 lies nowhere near them. A one-column strip down the
        block's side does stand over the block's ring 1 beside it, an eave
        turned up, and its corners face out with its sides."""
        roof = _hip(rise=(4, 2, 4, 6, 8), pitch=None)
        body = _rect(0, 0, 20, 12)
        arm = _rect(21, 0, 30, 1)
        tops = _tops(roof.plan(body | arm, 70, attic="hollow"))
        alone = _tops(roof.plan(arm, 70, attic="hollow"))
        for cell, facing in (((30, 0), "west"), ((30, 1), "west"),
                             ((25, 0), "south"), ((25, 1), "north")):
            with self.subTest(cell=cell):
                self.assertEqual(tops[cell][1].properties["facing"], facing)
                self.assertEqual(tops[cell][1], alone[cell][1])
        tops = _tops(roof.plan(body | _rect(20, 0, 21, 6), 70,
                               attic="hollow"))
        for cell in ((21, 0), (21, 3), (21, 6)):
            with self.subTest(strip=cell):
                self.assertEqual(tops[cell][1].properties["facing"], "east")

    def test_where_ridges_meet_the_crown_is_not_lowered(self):
        """``crown="slab"`` lowers a cell whose neighbors both ways along x
        or along z are lower, and where ridges meet -- an L's corner, a
        cross's crossing -- no two opposite neighbors are: the junction
        stands at its full height, half a block over the slab ridges that
        run into it. It is the rule as it stands on a rectangle too, where
        a ring that peaks keeps its corners over its lowered sides."""
        plan = _hip().plan(_rect(0, 0, 20, 8) | _rect(0, 0, 8, 20), 70,
                           attic="hollow")
        tops, heights = _tops(plan), {c: top for c, (_, top)
                                      in _columns(plan).items()}
        self.assertEqual((heights[(4, 4)], str(tops[(4, 4)][1])),
                         (150, f"{TILE_STAIR}[facing=south,half=bottom]"))
        for cell in ((5, 4), (4, 5), (10, 4), (4, 10)):
            self.assertEqual((heights[cell], str(tops[cell][1])),
                             (149, f"{TILE_SLAB}[type=bottom]"), cell)
        heights = {c: top for c, (_, top) in _columns(_hip().plan(
            _rect(0, 8, 26, 14) | _rect(10, 0, 16, 24), 70,
            attic="hollow")).items()}
        self.assertEqual([heights[c] for c in ((13, 11), (12, 11), (13, 10),
                                               (5, 11))],
                         [148, 147, 147, 147])
        heights = {c: top for c, (_, top) in _columns(_hip(
            rise=(2, 4, 6, 4, 2), pitch=None).plan(
                (0, 0, 10, 10), 70, attic="hollow")).items()}
        self.assertEqual([heights[c] for c in ((2, 2), (3, 2), (5, 2))],
                         [146, 145, 145])


class RoofBase(unittest.TestCase):
    CHUNKS = [(x, z) for x in range(0, 3) for z in range(0, 3)]

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="roof-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, self.CHUNKS)
        self.save = MinecraftSave.load(self.dir)
        self.b = Builder(self.save)

    def name(self, x, y, z):
        return self.save.get_block(x, y, z).name


def _payloads(directory) -> str:
    """One sha256 over every chunk of the overworld's region files as the
    game reads it -- region, index, length and the decompressed NBT."""
    region = os.path.join(directory, "dimensions/minecraft/overworld/region")
    digest = hashlib.sha256()
    for name in sorted(os.listdir(region)):
        with open(os.path.join(region, name), "rb") as handle:
            data = handle.read()
        for index in range(1024):
            entry, = struct.unpack_from(">I", data, 4 * index)
            offset = (entry >> 8) * 4096
            if not offset:
                continue
            length, = struct.unpack_from(">I", data, offset)
            body = data[offset + 5:offset + 4 + length]
            raw = zlib.decompress(body) if data[offset + 4] == 2 else body
            digest.update(f"{name}:{index}:{len(raw)}:".encode())
            digest.update(raw)
    return digest.hexdigest()


class BuildTests(RoofBase):
    def test_the_attic_must_be_chosen(self):
        roof = Roof(rings="square", pitch=1, block=STONE,
                    slab="minecraft:stone_brick_slab", stair=None)
        with self.assertRaises(TypeError):
            self.b.roof(roof, (2, 2, 9, 7), 70)
        with self.assertRaises(TypeError):
            self.b.roof(roof, (2, 2, 9, 7), 70, attic=None)
        with self.assertRaises(NBTError):
            self.b.roof(roof, (2, 2, 9, 7), 70, attic=AIR)
        self.assertEqual(self.b.blocks_set, 0)

        # Hollow: nothing under the shell, and the dark counted.
        hollow = self.b.roof(roof, (2, 2, 9, 7), 70, attic="hollow")
        spans = _columns(roof.plan((2, 2, 9, 7), 70, attic="hollow"))
        under = sum(bottom // 2 - hollow.springing
                    for (x, z), (bottom, _) in spans.items()
                    if 2 <= x <= 9 and 2 <= z <= 7)
        self.assertGreater(under, 0)
        self.assertEqual(hollow.hollow, under)
        self.assertEqual(hollow.closed, 0)
        self.assertEqual(self.name(5, hollow.springing, 4), AIR)

        # Filled: every column of the outline solid from the springing to
        # the shell, and every top slab over it made whole.
        filled = self.b.roof(roof, (22, 2, 29, 7), 70, attic="minecraft:dirt")
        self.assertEqual((filled.hollow, filled.springing), (0, 70))
        self.assertGreater(filled.closed, 0)
        for x in range(22, 30):
            for z in range(2, 8):
                column = [self.save.get_block(x, y, z) for y in range(70, 76)]
                names = [state.name for state in column]
                top = max(i for i, name in enumerate(names) if name != AIR)
                self.assertNotIn(AIR, names[:top + 1], (x, z, names))
                self.assertNotIn("top", [state.properties.get("type")
                                         for state in column], (x, z))
        # The rings stand a half block apart, so the third is a top slab
        # over the second's height, made whole over the fill.
        self.assertEqual(str(self.save.get_block(25, 71, 4)),
                         "minecraft:stone_brick_slab[type=double]")
        self.assertEqual(self.name(25, 70, 4), "minecraft:dirt")

    def test_closed_ends_close_to_the_shell(self):
        """A gable's two end planes, closed from the springing up to the
        shell, a top slab over the wall made whole; the rest of the attic
        left as the caller said."""
        roof = Roof(eaves=("north", "south"), pitch=1, overhang=1,
                    block=STONE, slab="minecraft:stone_brick_slab",
                    stair="minecraft:stone_brick_stairs",
                    close_ends="minecraft:bricks")
        result = self.b.roof(roof, (2, 2, 10, 9), 70, attic="hollow")
        self.assertGreater(result.closed, 0)
        self.assertEqual(result.springing, 70)
        for x in (2, 10):
            for z in range(2, 10):
                column = [self.save.get_block(x, y, z) for y in range(70, 80)]
                names = [s.name for s in column]
                shell = next(i for i, s in enumerate(column)
                             if s.name != "minecraft:bricks")
                self.assertTrue(all(n == "minecraft:bricks"
                                    for n in names[:shell]), (x, z, names))
                self.assertNotEqual(column[shell].properties.get("type"),
                                    "top", (x, z))
        self.assertEqual(self.name(6, 70, 5), AIR, "the attic is hollow")
        self.assertGreater(result.hollow, 0)

    def test_ends_close_from_the_springing_and_the_attic_from_its_floor(self):
        """``close_ends`` and a filled attic together, the attic raised off
        the springing: the end planes are the end wall's block from the
        springing up to the shell, whatever the attic is; every column
        between them is the attic's from ``attic_floor`` up, with the air
        under that left as it was; and a top slab is made whole only where
        a closure reaches it -- one standing under the attic floor is left
        a top slab."""
        roof = Roof(eaves=("north", "south"), pitch=1, overhang=1,
                    block=STONE, slab="minecraft:stone_brick_slab",
                    stair="minecraft:stone_brick_stairs",
                    close_ends="minecraft:bricks")
        result = self.b.roof(roof, (2, 2, 10, 13), 70, attic="minecraft:dirt",
                             attic_floor=72)
        self.assertEqual((result.springing, result.hollow), (70, 0))
        plan = roof.plan((2, 2, 10, 13), 70, attic="minecraft:dirt",
                         attic_floor=72)
        shell = {(x, z): min(y for (px, y, pz), state in plan.items()
                             if (px, pz) == (x, z)
                             and state.name.startswith("minecraft:stone_brick"))
                 for x in range(2, 11) for z in range(2, 14)}
        for (x, z), low in shell.items():
            column = {y: self.save.get_block(x, y, z) for y in range(70, low)}
            with self.subTest(x=x, z=z):
                if x in (2, 10):
                    self.assertEqual({s.name for s in column.values()},
                                     {"minecraft:bricks"} if low > 70 else set())
                else:
                    self.assertEqual({y for y, s in column.items()
                                      if s.name == "minecraft:dirt"},
                                     set(range(72, low)))
                    self.assertEqual({y for y, s in column.items()
                                      if s.name == AIR},
                                     set(range(70, min(72, low))))
        # Two rings in from the overhang the shell is a top slab at y=71:
        # under the end wall it is made whole, and inside, where the attic
        # starts above it, it stays a top slab. Two rings further the top
        # slab at y=72 is over the fill, and whole.
        slab = "minecraft:stone_brick_slab"
        self.assertEqual(str(self.save.get_block(2, 71, 3)), f"{slab}[type=double]")
        self.assertEqual(str(self.save.get_block(2, 70, 3)), "minecraft:bricks")
        self.assertEqual(str(self.save.get_block(6, 71, 3)), f"{slab}[type=top]")
        self.assertEqual(self.name(6, 70, 3), AIR)
        self.assertEqual(str(self.save.get_block(6, 72, 5)), f"{slab}[type=double]")
        self.assertEqual(self.name(6, 71, 5), AIR)
        self.assertEqual(self.name(10, 71, 5), "minecraft:bricks")

    def test_a_round_attic_fills_the_walls_and_not_the_overhang(self):
        """The attic is under the walls' disc: an overhang widens the roof,
        not what it fills."""
        roof = Roof(rings="round", pitch=2, overhang=1, block=STONE,
                    slab="minecraft:stone_brick_slab",
                    stair="minecraft:stone_brick_stairs")
        plan = roof.plan((20, 20, 5), 70, attic="minecraft:dirt",
                         attic_floor=68)
        filled = {(x, z) for (x, y, z), state in plan.items()
                  if state.name == "minecraft:dirt"}
        walls = set(disc(5, center=(20, 20)))
        self.assertEqual(filled, walls)
        roofed = {(x, z) for x, y, z in plan}
        self.assertEqual(roofed, set(disc(6, center=(20, 20))))
        result = self.b.roof(roof, (20, 20, 5), 70, attic="hollow",
                             attic_floor=68)
        self.assertEqual(result.hollow, sum(
            1 for (x, y, z), state in plan.items()
            if state.name == "minecraft:dirt"))

    def test_a_roof_refuses_taken_cells_and_overwrite_cuts_through(self):
        roof = Roof(rings="square", pitch=2, block=STONE,
                    slab="minecraft:stone_brick_slab",
                    stair="minecraft:stone_brick_stairs")
        self.save.set_block(2, 70, 4, "minecraft:snow")
        self.save.set_block(5, 73, 5, "minecraft:oak_log")
        self.save.set_block(5, 72, 5, "minecraft:oak_log")    # the attic's
        with self.assertRaises(Refused) as caught:
            self.b.roof(roof, (2, 2, 9, 9), 70, attic="hollow")
        faults = caught.exception.faults
        self.assertEqual(len(faults), 2)
        self.assertIn("(2, 70, 4) holds minecraft:snow", faults[0])
        self.assertIn("(5, 73, 5) holds minecraft:oak_log", faults[1])
        self.assertEqual(self.b.blocks_set, 0)
        self.assertEqual(self.name(3, 70, 2), AIR)
        result = self.b.roof(roof, (2, 2, 9, 9), 70, attic="hollow",
                             overwrite=True)
        self.assertEqual(self.name(2, 70, 4), "minecraft:stone_brick_stairs")
        self.assertEqual(self.name(5, 73, 5), "minecraft:stone_brick_stairs")
        self.assertEqual(self.name(5, 72, 5), "minecraft:oak_log",
                         "a hollow attic is left as it stands")
        self.assertEqual(result.already, 0)
        # The hollow counts the empty cells under the shell over the walls,
        # and the log is not one of them.
        spans = _columns(roof.plan((2, 2, 9, 9), 70, attic="hollow"))
        under = sum(bottom // 2 - result.springing
                    for (x, z), (bottom, _) in spans.items()
                    if 2 <= x <= 9 and 2 <= z <= 9)
        self.assertEqual(result.hollow, under - 1)

    def test_a_roof_out_of_reach_is_refused_before_anything_is_read(self):
        """A roof into a chunk not generated, or past the top of the world,
        is refused as the plan's check refuses it -- every fault, the whole
        span past the world -- before the occupancy check reads a cell, and
        nothing is written."""
        roof = Roof(rings="square", pitch=2, block=STONE,
                    slab="minecraft:stone_brick_slab",
                    stair="minecraft:stone_brick_stairs")
        cases = (((40, 2, 52, 8), 70, ["chunk (3, 0) is not generated"]),
                 ((2, 2, 9, 9), 318,
                  ["y=320..321 is past the world's -64..319"]))
        for outline, base_y, faults in cases:
            with self.subTest(outline=outline, base_y=base_y):
                with self.assertRaises(Refused) as caught:
                    self.b.roof(roof, outline, base_y, attic="hollow")
                self.assertEqual(str(caught.exception),
                                 f"roof cannot be written: {faults[0]}. "
                                 f"Nothing was written")
                self.assertEqual(caught.exception.faults, faults)
                self.assertEqual(self.b.blocks_set, 0)

    def test_a_roof_built_twice_writes_nothing_the_second_time(self):
        roof = Roof(rings="round", pitch=2, block=STONE,
                    slab="minecraft:stone_brick_slab",
                    stair="minecraft:stone_brick_stairs")
        first = self.b.roof(roof, (16, 16, 6), 70, attic="hollow")
        self.b.finish()                       # hips take their corner shapes
        written = self.b.blocks_set
        again = self.b.roof(roof, (16, 16, 6), 70, attic="hollow")
        self.assertEqual(self.b.blocks_set, written)
        self.assertEqual((again.already, again.blocks),
                         (first.blocks, first.blocks))
        self.assertIn("already right", repr(again))

    def test_a_wall_crown_joined_up_by_finish_is_the_crown_that_was_laid(self):
        """A wall in a whole-block role is joined to its neighbors by
        finish(), as a battlement's or a shell's course is; the roof asks
        the same question they do of a cell -- the same state apart from
        what a block update derives -- so verify holds it right and the
        same call again finds it whole, rather than every joined wall
        taken and a fault."""
        roof = Roof(eaves=("north", "south"), crown="block",
                    block="minecraft:cobblestone_wall",
                    slab="minecraft:cobblestone_slab",
                    stair="minecraft:cobblestone_stairs")
        first = self.b.roof(roof, (2, 2, 10, 8), 70, attic="hollow")
        self.b.finish()
        joined = self.save.get_block(3, 73, 5)
        self.assertEqual(joined.name, "minecraft:cobblestone_wall")
        self.assertEqual(joined.properties.get("west"), "low", "joined up")
        self.assertEqual(roof.verify(self.save, (2, 2, 10, 8), 70,
                                     attic="hollow"), [])
        written = self.b.blocks_set
        again = self.b.roof(roof, (2, 2, 10, 8), 70, attic="hollow")
        self.assertEqual(self.b.blocks_set, written)
        self.assertEqual((again.already, again.blocks),
                         (first.blocks, first.blocks))
        self.assertEqual(self.save.get_block(3, 73, 5), joined)

    def test_a_block_the_game_lacks_writes_nothing(self):
        """An id the game lacks is the write's refusal, every one named --
        a Mix's entries included, picked or not -- and nothing is
        written."""
        roof = Roof(rings="square", pitch=2, block=STONE,
                    slab="minecraft:quartz_wall",
                    stair="minecraft:stone_brick_stairs")
        with self.assertRaises(IllegalBlock) as caught:
            self.b.roof(roof, (2, 2, 8, 8), 70, attic="hollow")
        self.assertIn("roof", str(caught.exception))
        self.assertEqual(self.b.blocks_set, 0)
        tiles = Mix([(STONE, 1000), ("minecraft:marble", 1)])
        roof = Roof(rings="square", pitch=2, block=STONE, slab=None,
                    stair=None, crown="block")
        with self.assertRaises(IllegalBlock) as caught:
            self.b.roof(roof, (2, 2, 4, 4), 70, attic=tiles)
        self.assertIn("minecraft:marble", str(caught.exception))
        self.assertEqual(self.b.blocks_set, 0)
        # A stair of another shape is refused as the rule is made, by
        # gable_roof too -- where it once wrote a roof of trapdoors.
        with self.assertRaises(NBTError):
            self.b.gable_roof(2, 2, 8, 6, 70, "x",
                              stair="minecraft:oak_trapdoor")
        self.assertEqual(self.b.blocks_set, 0)

    def test_two_roofs_built_alike_encode_alike(self):
        tiles = Mix([("minecraft:granite", 2), ("minecraft:polished_granite", 1)],
                    seed=7)
        roof = Roof(rings="square", pitch=2, overhang=1, block=tiles,
                    slab="minecraft:granite_slab",
                    stair="minecraft:granite_stairs", hip=tiles)
        digests = []
        for _ in range(2):
            directory = tempfile.mkdtemp(prefix="roof-alike-")
            self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
            make_save(directory, self.CHUNKS)
            save = MinecraftSave.load(directory)
            b = Builder(save)
            b.roof(roof, (3, 3, 12, 9), 70, attic=tiles)
            b.finish()
            save.save()
            digests.append(_payloads(directory))
        self.assertEqual(digests[0], digests[1])

    def test_verify_holds_a_roof_to_what_stands(self):
        roof = Roof(rings="square", pitch=2, block=STONE,
                    slab="minecraft:stone_brick_slab",
                    stair="minecraft:stone_brick_stairs")
        outline = (2, 2, 11, 8)
        self.assertEqual(len(roof.verify(self.save, outline, 70,
                                         attic="hollow")),
                         len(roof.plan(outline, 70, attic="hollow")))
        self.b.roof(roof, outline, 70, attic="hollow")
        self.b.finish()
        self.assertNotEqual(self.save.get_block(2, 70, 2).properties["shape"],
                            "straight", "finish shaped the corner's stair")
        self.assertEqual(roof.verify(self.save, outline, 70, attic="hollow"), [])
        self.save.set_block(5, 71, 3, AIR)
        self.save.set_block(6, 70, 2, "minecraft:oak_planks")
        wrong = roof.verify(self.save, outline, 70, attic="hollow")
        self.assertEqual(len(wrong), 2)
        self.assertIn("(6, 70, 2): expected minecraft:stone_brick_stairs", wrong[0])
        self.assertIn("(5, 71, 3)", wrong[1])
        self.assertEqual(len(roof.verify(self.save, outline, 70, attic="hollow",
                                         missing_ok=True)), 1)

    def test_a_roof_is_counted_and_reported(self):
        roof = Roof(rings="square", pitch=2, block=STONE,
                    slab="minecraft:stone_brick_slab",
                    stair="minecraft:stone_brick_stairs")
        result = self.b.roof(roof, (2, 2, 6, 6), 70, attic=STONE)
        self.assertIsInstance(result, RoofResult)
        plan = roof.plan((2, 2, 6, 6), 70, attic=STONE)
        self.assertEqual(result.blocks, len(plan))
        self.assertEqual(result.stairs, sum(1 for s in plan.values()
                                            if s.name.endswith("_stairs")))
        self.assertEqual(self.b.blocks_set, len(plan))
        self.assertEqual(
            repr(result), f"<RoofResult {len(plan)} blocks, {result.stairs} "
                          f"stairs, {result.closed} closed, springing at y=70>")
        with self.assertRaises(NBTError):
            self.b.roof("a roof", (2, 2, 6, 6), 70, attic="hollow")


    def test_wings_build_as_one_roof(self):
        """A hip with a gabled wing running into it, laid as Wings: two
        Builder.roof calls over the same columns refuse each other as they
        always did, and one Wings is written as its plan, every cell,
        after the same refusals as a roof -- a taken cell, every one in
        the faults -- with the wing's far end closed and the end inside
        the hip open, its hips' corners shaped by finish(), held to what
        stands by verify, counted already the second time, and encoded
        alike in two saves."""
        def wings(ends):
            hip = Roof(rings="square", pitch=2, overhang=1, block=PLANK,
                       slab=SLAB, stair=STAIR)
            wing = Roof(eaves=("west", "east"), pitch=2, overhang=1,
                        block=PLANK, slab=SLAB, stair=STAIR, close_ends=ends)
            return hip, wing, Wings([(hip, (2, 2, 14, 8), 70),
                                     (wing, (6, 5, 10, 16), 70)])
        hip, wing, tee = wings(PLANK)
        self.b.roof(hip, (2, 26, 14, 32), 70, attic="hollow")
        written = self.b.blocks_set
        with self.assertRaises(Refused):
            self.b.roof(wing, (6, 29, 10, 40), 70, attic="hollow")
        self.assertEqual(self.b.blocks_set, written)

        plan = tee.plan(attic="hollow")
        self.save.set_block(2, 70, 1, "minecraft:snow")
        self.save.set_block(8, 73, 14, "minecraft:oak_log")
        with self.assertRaises(Refused) as caught:
            self.b.roof(tee, attic="hollow")
        self.assertEqual(len(caught.exception.faults), 2)
        self.assertEqual(self.b.blocks_set, written)
        self.save.set_block(2, 70, 1, AIR)
        self.save.set_block(8, 73, 14, AIR)
        result = self.b.roof(tee, attic="hollow")
        self.assertEqual((result.blocks, result.already), (len(plan), 0))
        self.assertEqual(self.b.blocks_set - written, len(plan))
        for cell, state in plan.items():
            self.assertTrue(_same(self.save.get_block(*cell), state), cell)
        ends = {(x, z) for x, _, z in set(plan) - set(wings(None)[2].plan(
            attic="hollow"))}
        self.assertEqual(ends, {(x, 16) for x in range(6, 11)})
        self.b.finish()
        corners = [cell for cell, state in plan.items()
                   if state.name == STAIR
                   and self.save.get_block(*cell).properties["shape"]
                   != "straight"]
        self.assertGreater(len(corners), 4)
        self.assertEqual(tee.verify(self.save, attic="hollow"), [])
        again = self.b.roof(tee, attic="hollow")
        self.assertEqual((again.already, again.blocks), (len(plan), len(plan)))
        self.assertEqual(self.b.blocks_set - written, len(plan))
        first, last = next(iter(plan)), list(plan)[-1]
        self.save.set_block(*first, AIR)
        self.save.set_block(*last, "minecraft:oak_planks")
        wrong = tee.verify(self.save, attic="hollow")
        self.assertEqual(len(wrong), 2)
        self.assertIn(f"{first}: expected", wrong[0])
        self.assertEqual(len(tee.verify(self.save, attic="hollow",
                                        missing_ok=True)), 1)
        digests = []
        for _ in range(2):
            directory = tempfile.mkdtemp(prefix="roof-wings-")
            self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
            make_save(directory, self.CHUNKS)
            save = MinecraftSave.load(directory)
            b = Builder(save)
            b.roof(tee, attic="minecraft:dirt")
            b.finish()
            save.save()
            digests.append(_payloads(directory))
        self.assertEqual(digests[0], digests[1])


#: The suburban house's roof as it stands in JeffWorld, x -314..-277 by
#: z -17..12, read from the world (closed, never saved) on 2026-10-21 and
#: again, unchanged, on 2026-10-22. A row a z, from -17: the height above
#: y=70 of each column's top block at y=76 or over, as a hex digit ("."
#: for none), then the block -- "#" stone, "n", "s", "e" or "w" a
#: stone-brick stair facing that way (every one a bottom half), and "x"
#: something else standing on the roof: three lanterns, a brick chimney
#: five columns wide, a quartz block and a stripped oak block in the z=2
#: valley. An oracle of what stood on those days, which later play does
#: not change: never refresh it from a world being played.
SUBURBAN_ROOF = (
    "........6667778899988777666........... ........e##e##e#e#w#w##w##w...........",
    "........6667778899988777666........... ........e##e##e#e#w#w##w##w...........",
    "........6667778899988777666........... ........e##e##e#e#w#w##w##w...........",
    "66666666666777889998877766666666666666 sssssssss##e##e#e#w#w##w##wsssssssssss",
    "66666666666777889998877766666666666666 ###########e##e#e#w#w##w##############",
    "66666666666777889998877766666666666666 ###########e##e#e#w#w##w##############",
    "77777777777777889998b77777777777777777 ssssssssssss##e#e#w#x##sssssssssssssss",
    "7777777777777788999bcb7777777777777777 ##############e#e#wxxx################",
    "77777777777777889998b77777777777777777 ##############e#e#w#x#################",
    "88888888888888889998888888888888888888 sssssssssssssse#e#w#ssssssssssssssssss",
    "88888888888888889998888888888888888888 ################e#w###################",
    "99999999999999999998999999999999988888 sssssssssssssssse#w#sssssssssssss#####",
    "9999999999999999a9999999999999a999999a ################x#############x######x",
    "99999999999999999999999999999999999999 nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn",
    "88888888888888888888888888888888888888 ######################################",
    "88888888888888888888888888888888888888 ######################################",
    "77777777777777777777777777777777777777 ######################################",
    "77777777777777777777777777777777777777 ######################################",
    "77777777777777777777777777777777777777 ######################################",
    "66666666666666666666666666666666666666 x################x####################",
    "77777777777777777777777777777777777777 #ssssssssssssssssssssssssssssssssssss#",
    "77777777777777777777777777777777777777 ######################################",
    "88888888888888888888888888888888888888 #ssssssssssssssssssssssssssssssssssss#",
    "88888888888888888888888888888888888888 ######################################",
    "99999999999999999999999999999999999999 #ssssssssssssssssssssssssssssssssssss#",
    "99999999999999999999999999999999999999 ######################################",
    "99999999999999999999999999999999999999 #nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn#",
    "88888888888888888888888888888888888888 ######################################",
    "88888888888888888888888888888888888888 #nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn#",
    "77777777777777777777777777777777777777 #nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn#",
)

#: The front bay's north gable end as it stands, z=-17, x -306..-288,
#: from y=79 down to the wall head at y=76 (read the same days; the
#: house's quartz wall is under it): "#" stone, "e" and "w" its slopes'
#: top stairs, "." air.
BAY_END = ("........e#w........",
           "......e#####w......",
           "...e###########w...",
           "e#################w")


def _suburban_wings(house, script, *, bay_to=-6, bay_ends=True) -> Wings:
    """The suburban house's cross gable as three wings, each rise read off
    what stands: the main roof (x -314..-277, z -14..2, eaves at y=76) a
    block every three rows up its north slope to a ridge three rows wide
    at y=79, and its south slope a row sooner; the rear gable as
    ``tools/suburban_house/roof.py`` built it; and the front bay (x
    -306..-288) from its north gable end at z=-17 to ``bay_to``, its
    ridge running north and south, three wide at y=79. Stone, stone-brick
    stairs, no slabs; the main roof's and the bay's ends closed in stone
    (``bay_ends``)."""
    stone, stair = house.ROOF_STONE, house.ROOF_STAIR
    main = Roof(eaves=("north", "south"),
                rise={"north": (2, 2, 2, 4, 4, 4, 6, 6, 8),
                      "south": (2, 4, 4, 4, 6, 6, 8)},
                block=stone, stair=stair, slab=None, close_ends=stone)
    rear = Roof(eaves=("north", "south"),
                rise={"north": tuple(2 + 2 * (d // 2) for d in range(10)),
                      "south": tuple(2 + 2 * ((d + 1) // 2) for d in range(10))},
                block=stone, stair=stair, slab=None)
    bay = Roof(eaves=("west", "east"), rise=(2, 2, 2, 4, 4, 4, 6, 6, 8),
               block=stone, stair=stair, slab=None,
               close_ends=stone if bay_ends else None)
    return Wings([(main, (script.ROOF_X0, -14, script.ROOF_X1, 2), 76),
                  (rear, (script.ROOF_X0, 3, script.ROOF_X1, 12), 77),
                  (bay, (-306, -17, -288, bay_to), 76)])


def _open_attic(wings: Wings) -> list:
    """Every attic cell of ``wings`` -- what a filled attic fills -- with
    a side or its top open: the cell beside it or over it neither roof
    nor attic, nor the building under a wing's walls, which stands up to
    the springing that wing has laid alone (the highest, where several
    wings' walls hold a column). A roof closes its attic; a join that
    lost the eave course closing a wing roofed over by another leaves it
    open to the sky."""
    filler = "minecraft:sponge"
    shell = wings.plan(attic="hollow")
    attic = {cell for cell, state in wings.plan(attic=filler).items()
             if state.name == filler}
    building: dict = {}
    for roof, outline, base_y in wings.wings:
        springing = min(y for _, y, _ in roof.plan(outline, base_y,
                                                   attic="hollow"))
        for column in _rect(*outline):
            building[column] = max(building.get(column, springing),
                                   springing)
    found = []
    for x, y, z in sorted(attic):
        for near in ((x + 1, y, z), (x - 1, y, z), (x, y, z + 1),
                     (x, y, z - 1), (x, y + 1, z)):
            column = (near[0], near[2])
            if (near not in shell and near not in attic
                    and not (column in building
                             and near[1] < building[column])):
                found.append(((x, y, z), near))
    return found


class WingsTests(RoofBase):
    """Several roofs joined where they meet: a plan of gables."""

    def test_one_wing_is_the_roof(self):
        """A Wings of one is its roof, over every kind of roof: the same
        cells in the same order, the same states spelled the same way,
        and Builder.roof counts and saves the same."""
        rises = (dict(pitch=2), dict(pitch=1), dict(rise=(2, 4, 6, 4, 2)),
                 dict(rise=(4, 2, 4, 6, 8)), dict(rise=(2, 6, 2, 6, 2, 6)))
        kinds = {"square": (dict(rings="square"), (0, 0, 9, 6)),
                 "round": (dict(rings="round"), (0, 0, 4)),
                 "gable": (dict(eaves=("north", "south")), (0, 0, 8, 5)),
                 "gable z": (dict(eaves=("west", "east")), (0, 0, 8, 5)),
                 "shed": (dict(eaves="west"), (0, 0, 6, 3))}
        for name, (kind, outline) in kinds.items():
            for rise in rises:
                for overhang in (0, 1):
                    for ends in ((None, STONE) if "eaves" in kind else (None,)):
                        roof = Roof(**kind, **rise, overhang=overhang,
                                    block=STONE, slab=TILE_SLAB,
                                    stair=TILE_STAIR, close_ends=ends)
                        for attic, floor in (("hollow", None), (PLANK, 68)):
                            with self.subTest(kind=name, rise=rise,
                                              overhang=overhang, ends=ends,
                                              attic=attic):
                                one = Wings([(roof, outline, 70)])
                                self.assertEqual(
                                    _ordered(one.plan(attic=attic,
                                                      attic_floor=floor)),
                                    _ordered(roof.plan(outline, 70,
                                                       attic=attic,
                                                       attic_floor=floor)))
        roof = Roof(eaves=("north", "south"), pitch=1, overhang=1,
                    block=STONE, slab=TILE_SLAB, stair=TILE_STAIR,
                    close_ends="minecraft:bricks")
        results, digests = [], []
        for one in (False, True):
            directory = tempfile.mkdtemp(prefix="roof-one-wing-")
            self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
            make_save(directory, self.CHUNKS)
            save = MinecraftSave.load(directory)
            b = Builder(save)
            if one:
                result = b.roof(Wings([(roof, (2, 2, 10, 13), 70)]),
                                attic="minecraft:dirt", attic_floor=72)
            else:
                result = b.roof(roof, (2, 2, 10, 13), 70,
                                attic="minecraft:dirt", attic_floor=72)
            results.append((repr(result), b.blocks_set))
            save.save()
            digests.append(_payloads(directory))
        self.assertEqual(results[0], results[1])
        self.assertEqual(digests[0], digests[1])

    def test_the_suburban_cross_gable(self):
        """The suburban house's main roof, rear gable and front bay, as
        three wings, against what stands (``SUBURBAN_ROOF``). Of the 1,083
        columns they roof, 10 hold something else on top; of the other
        1,073, all but 6 stand at the rule's height -- the ridge's north
        row Jeff stopped five columns short of the east verge, and the
        block missing at (-295, 79, -6) -- and their top blocks are the
        rule's but for 96: two rows of the main roof's south slope (z=-2
        and z=1), where Jeff laid stone and the rule stairs; the rear
        gable's 12 verge cells, as the rear gable's own test says; and the
        8 valley cells where the bay's slopes cross the main's north one,
        which the rule lays as plain blocks and Jeff as stairs -- inner
        corners at 7, one straight beside the missing block. The bay's
        north gable end is closed in stone from the wall head, as it
        stands."""
        house, script = _suburban()
        plan = _suburban_wings(house, script).plan(attic="hollow")
        world = {}
        for i, row in enumerate(SUBURBAN_ROOF):
            heights, kinds = row.split()
            for j, (height, kind) in enumerate(zip(heights, kinds)):
                if height != ".":
                    world[(script.ROOF_X0 + j, -17 + i)] = (70 + int(height, 16),
                                                           kind)
        tops = _tops(plan)
        self.assertEqual(len(tops), 1083)
        self.assertLessEqual(set(tops), set(world))
        other = {column for column in tops if world[column][1] == "x"}
        self.assertEqual(len(other), 10)
        shell = set(tops) - other
        lower = {column: (tops[column][0], world[column][0])
                 for column in shell if world[column][0] != tops[column][0]}
        self.assertEqual(lower, {(x, -6): (79, 78)
                                 for x in (-295, -281, -280, -279, -278, -277)})
        self.assertEqual(len(shell) - len(lower), 1067)

        def kind(state):
            if state.name == house.ROOF_STAIR:
                return state.properties["facing"][0]
            return "#" if state.name == house.ROOF_STONE else "?"
        differ = {column for column in shell - set(lower)
                  if kind(tops[column][1]) != world[column][1]}
        slope = {(x, z) for x in range(script.ROOF_X0, script.ROOF_X1 + 1)
                 for z in (-2, 1)}
        verges = {(x, z) for x in (script.ROOF_X0, script.ROOF_X1)
                  for z in script.REAR_STAIRS}
        valley = {(-306, -14), (-288, -14), (-303, -11), (-291, -11),
                  (-300, -8), (-294, -8), (-298, -6), (-296, -6)}
        self.assertEqual((len(slope), len(verges), len(valley)), (76, 12, 8))
        self.assertEqual(differ, slope | verges | valley)
        for column in slope:
            self.assertEqual((kind(tops[column][1]), world[column][1]),
                             ("n", "#"), column)
        heights = {column: top for column, (_, top) in _columns(plan).items()}
        for x, z in valley:
            with self.subTest(valley=(x, z)):
                self.assertEqual(kind(tops[(x, z)][1]), "#")
                self.assertIn(world[(x, z)][1], "nsew")
                self.assertTrue(all(heights[(x + dx, z + dz)] >= heights[(x, z)]
                                    for dx, dz in COMPASS.values()))
        for i, row in enumerate(BAY_END):
            for j, want in enumerate(row):
                state = plan.get((-306 + j, 79 - i, -17))
                self.assertEqual("." if state is None else kind(state), want,
                                 (-306 + j, 79 - i))
        self.assertFalse([cell for cell in plan if cell[2] == -17
                          and cell[1] < 76])

    def test_a_wing_drawn_through_another_meets_its_ridge(self):
        """The bay drawn through the main roof to its ridge meets it in
        valleys with no gap anywhere, and only its north end is closed:
        its south end is inside the main roof. Drawn short, over its own
        walls (to z=-15), it stands in a gable face laid down to the main
        roof's eave -- still gapless -- and again only its north end is
        closed."""
        house, script = _suburban()
        for bay_to in (-6, -15):
            with self.subTest(bay_to=bay_to):
                plan = _suburban_wings(house, script, bay_to=bay_to).plan(
                    attic="hollow")
                self.assertEqual(_gaps(plan), [])
                bare = _suburban_wings(house, script, bay_to=bay_to,
                                       bay_ends=False).plan(attic="hollow")
                closed = {cell for cell in set(plan) | set(bare)
                          if plan.get(cell) != bare.get(cell)}
                # The end wall, from the wall head at y=76 up to the shell
                # of every column standing over it: all but the three
                # rings at each side, which are the eave course itself.
                self.assertEqual({z for _, _, z in closed}, {-17})
                self.assertEqual({x for x, _, _ in closed},
                                 set(range(-303, -290)))
                self.assertEqual(min(y for _, y, _ in closed), 76)
        short = _suburban_wings(house, script, bay_to=-15).plan(attic="hollow")
        spans = _columns(short)
        for x in range(-298, -295):
            # The bay's ridge at y=79, laid down to the top of the main
            # roof's eave at y=76 beside it: a face three blocks deep.
            self.assertEqual(spans[(x, -15)], (2 * 77, 2 * 80))
            self.assertEqual(spans[(x, -14)][1], 2 * 77)

    def test_a_t_of_gables_is_one_roof(self):
        """A T: a gable along x and a wing's gable along z drawn to its
        ridge. Gapless, no stair facing down its slope, the valleys plain
        blocks with no lower side neighbor, and of the four end planes
        only the three facing out of the plan closed -- the main roof's
        two and the wing's far one. Built, verify holds it to what stands
        and names a cell taken out of it."""
        def tee(ends):
            main = Roof(eaves=("north", "south"), pitch=2, block=STONE,
                        slab=TILE_SLAB, stair=TILE_STAIR, close_ends=ends)
            wing = Roof(eaves=("west", "east"), pitch=2, block=STONE,
                        slab=TILE_SLAB, stair=TILE_STAIR, close_ends=ends)
            return Wings([(main, (2, 2, 26, 8), 70), (wing, (11, 5, 17, 20), 70)])
        roof = tee("minecraft:bricks")
        plan = roof.plan(attic="hollow")
        self.assertEqual(_gaps(plan), [])
        heights = {column: top for column, (_, top) in _columns(plan).items()}
        for (x, y, z), state in plan.items():
            if state.name == TILE_STAIR and 2 * y + 2 == heights[(x, z)]:
                dx, dz = COMPASS[state.properties["facing"]]
                front = heights.get((x + dx, z + dz))
                self.assertFalse(front is not None
                                 and front <= heights[(x, z)] - 2, (x, y, z))
        # The valleys run up from the wing's eaves at the main roof's to
        # the ridge, where the wing's own ridge meets it.
        tops = _tops(plan)
        for x, z in ((11, 8), (12, 7), (13, 6), (17, 8), (16, 7), (15, 6)):
            with self.subTest(valley=(x, z)):
                self.assertEqual(str(tops[(x, z)][1]), STONE)
                self.assertTrue(all(heights[(x + dx, z + dz)] >= heights[(x, z)]
                                    for dx, dz in COMPASS.values()))
        closed = {(x, z) for x, _, z in set(plan) - set(tee(None).plan(
            attic="hollow"))}
        self.assertEqual({x for x, z in closed if z <= 8}, {2, 26})
        self.assertEqual({z for x, z in closed if 11 <= x <= 17}, {20})
        self.b.roof(roof, attic="hollow")
        self.b.finish()
        self.assertEqual(roof.verify(self.save, attic="hollow"), [])
        taken = (13, 72, 15)
        self.assertEqual(str(plan[taken]),
                         f"{TILE_STAIR}[facing=east,half=bottom]")
        self.save.set_block(*taken, AIR)
        self.assertEqual(roof.verify(self.save, attic="hollow"),
                         [f"{taken}: expected {plan[taken]}, found {AIR}"])

    def test_a_tie_goes_to_the_first_wing(self):
        """Two gables of different stone over overlapping rectangles: where
        their tops are equal the first wing listed owns the column, so the
        overlap is its stone, and listing them the other way round makes
        it the other's. The cells are the same either way."""
        granite = Roof(eaves=("north", "south"), pitch=2, crown="block",
                       block="minecraft:granite", slab=None, stair=None)
        andesite = Roof(eaves=("north", "south"), pitch=2, crown="block",
                        block="minecraft:andesite", slab=None, stair=None)
        first = Wings([(granite, (0, 0, 10, 6), 70),
                       (andesite, (6, 0, 16, 6), 70)]).plan(attic="hollow")
        swapped = Wings([(andesite, (6, 0, 16, 6), 70),
                         (granite, (0, 0, 10, 6), 70)]).plan(attic="hollow")
        self.assertEqual(set(first), set(swapped))
        for (x, y, z), state in first.items():
            self.assertEqual(state.name, "minecraft:granite" if x <= 10
                             else "minecraft:andesite", (x, y, z))
        for (x, y, z), state in swapped.items():
            self.assertEqual(state.name, "minecraft:andesite" if x >= 6
                             else "minecraft:granite", (x, y, z))

    def test_wings_are_checked(self):
        """No wings, a wing that is not (Roof, outline, base_y), a Wings
        among them, and an outline its roof does not take are each
        refused as the Wings is made; Builder.roof refuses a Wings given
        an outline or a base_y, and a Roof given neither. Nothing is
        written."""
        hip = _hip()
        good = (hip, (2, 2, 8, 8), 70)
        gable = Roof(eaves="north", block=STONE, slab=TILE_SLAB,
                     stair=TILE_STAIR)
        for label, wings in (
                ("none", []), ("empty", ()), ("not a sequence", None),
                ("a string", "roof"), ("a roof", hip), ("a bare roof", [hip]),
                ("short", [(hip, (2, 2, 8, 8))]),
                ("long", [(hip, (2, 2, 8, 8), 70, 1)]),
                ("not a roof", [("roof", (2, 2, 8, 8), 70)]),
                ("nested", [good, (Wings([good]), None, None)]),
                ("a wings", Wings([good])),
                ("bad outline", [good, (hip, (2, 2, 8), 70)]),
                ("bad base", [(hip, (2, 2, 8, 8), 70.5)]),
                ("a plan of gables", [(gable, _rect(2, 2, 8, 8), 70)])):
            with self.subTest(label):
                with self.assertRaises(NBTError) as caught:
                    Wings(wings)
                if label in ("nested", "a wings"):
                    self.assertIn("a Wings among the wings of a Wings",
                                  str(caught.exception))
        wings = Wings([good])
        self.assertEqual(wings.wings, (good,))
        for call in (lambda: self.b.roof(wings, (2, 2, 8, 8), attic="hollow"),
                     lambda: self.b.roof(wings, base_y=70, attic="hollow"),
                     lambda: self.b.roof(hip, attic="hollow"),
                     lambda: self.b.roof(hip, (2, 2, 8, 8), attic="hollow"),
                     lambda: self.b.roof([good], attic="hollow")):
            with self.assertRaises(NBTError):
                call()
        self.assertEqual(self.b.blocks_set, 0)

    def test_a_wing_that_needs_a_slab_it_lacks_is_named(self):
        """A whole-block hip with no slab over the walls of a wing
        standing half a block lower: where it roofs over them it reaches
        down to their half-block tops, which needs a slab it was not
        given, and the refusal names the first cell in write order that
        does -- the first that is one of the hip's slabs when it is given
        some. Each wing alone needs none."""
        whole = dict(rings="square", pitch=2, crown="block", block=STONE,
                     stair=None)
        half = Roof(rings="square", rise=(3, 4, 5, 6, 7), block=STONE,
                    slab=TILE_SLAB, stair=None)
        Roof(**whole, slab=None).plan((0, 0, 8, 8), 70, attic="hollow")
        half.plan((6, 0, 20, 8), 69, attic="hollow")
        with self.assertRaises(NBTError) as caught:
            Wings([(Roof(**whole, slab=None), (0, 0, 8, 8), 70),
                   (half, (6, 0, 20, 8), 69)]).plan(attic="hollow")
        plan = Wings([(Roof(**whole, slab="minecraft:granite_slab"),
                       (0, 0, 8, 8), 70),
                      (half, (6, 0, 20, 8), 69)]).plan(attic="hollow")
        first = next(cell for cell, state in plan.items()
                     if state.name == "minecraft:granite_slab")
        self.assertIn(f"slab at {first}", str(caught.exception))
        self.assertEqual(first, (7, 71, 2))

    def test_each_wing_closes_and_fills_from_its_own_springing(self):
        """A gable over a tall house, eaves at y=90 and its ends closed,
        with a lean-to below it whose eave is at y=72: joined, each is laid
        as it is alone -- the gable's attic filled and its ends closed
        from y=90, never down through the house's top stories from the
        lean-to's springing, and its eave over the house's wall line one
        course deep, never down that wall to the lean-to's roof. The
        suburban house's rear attic, filled, starts on its ceiling at
        y=77, as ``tools/suburban_house/roof.py`` filled it, and not at
        the main roof's eave course a block lower."""
        house = Roof(eaves=("north", "south"), pitch=2, block=STONE,
                     slab=TILE_SLAB, stair=TILE_STAIR,
                     close_ends="minecraft:bricks")
        lean_to = Roof(eaves="south", pitch=1, block=STONE, slab=TILE_SLAB,
                       stair=TILE_STAIR)
        for attic in ("minecraft:dirt", "hollow"):
            with self.subTest(attic=attic):
                joined = Wings([(house, (0, 0, 10, 8), 90),
                                (lean_to, (0, 9, 10, 13), 72)]).plan(
                                    attic=attic)
                self.assertEqual(joined, {
                    **house.plan((0, 0, 10, 8), 90, attic=attic),
                    **lean_to.plan((0, 9, 10, 13), 72, attic=attic)})
        # Built, the hollow count is the two wings' own, and the springing
        # the whole roof's: the lean-to's eave course.
        result = self.b.roof(Wings([(house, (0, 0, 10, 8), 90),
                                    (lean_to, (0, 9, 10, 13), 72)]),
                             attic="hollow")
        apart = [self.b.roof(house, (20, 0, 30, 8), 90, attic="hollow"),
                 self.b.roof(lean_to, (20, 9, 30, 13), 72, attic="hollow")]
        self.assertEqual((result.hollow, result.springing),
                         (apart[0].hollow + apart[1].hollow, 72))
        suburb, script = _suburban()
        plan = _suburban_wings(suburb, script).plan(attic=suburb.ROOF_STONE)
        rear = [y for x, y, z in plan if 3 <= z <= 12]
        self.assertEqual(min(rear), script.CEIL_Y + 1)
        self.assertEqual(min(y for x, y, z in plan if z <= 2), 76)

    def test_an_eave_over_a_lower_wing_stays_an_eave(self):
        """A hip over a house, eaves at y=80, with a low gable beside it,
        eaves at y=72. With no overhang the hip's eave on the house's wall
        line is one whole block, as alone: under it stands the house's
        wall, which no roof is built down into. With an overhang over
        open ground -- the low gable's walls drawn clear of it -- the
        overhang is the hip's eave course alone, with air under it down
        to the low roof. Drawn under the overhang, the low gable's walls
        are closed by it instead: its eave there is under the hip's
        overhang, and the overhang reaches down to it, a face from y=72
        to y=80 that keeps the low attic shut. Nothing is written into
        either building, and no attic is left open."""
        low = Roof(eaves=("north", "south"), pitch=1, block=STONE,
                   slab=TILE_SLAB, stair=TILE_STAIR,
                   close_ends="minecraft:bricks")
        for overhang, z0, column, span in ((0, 9, (5, 8), (160, 162)),
                                           (1, 10, (5, 9), (160, 162)),
                                           (1, 9, (5, 9), (144, 162))):
            with self.subTest(overhang=overhang, low_from=z0):
                wings = Wings([(_hip(overhang=overhang), (0, 0, 10, 8), 80),
                               (low, (0, z0, 10, z0 + 4), 72)])
                plan = wings.plan(attic="hollow")
                self.assertEqual(_columns(plan)[column], span)
                self.assertFalse([(x, y, z) for x, y, z in plan
                                  if (x, z) in _rect(0, 0, 10, 8) and y < 80])
                self.assertFalse([(x, y, z) for x, y, z in plan
                                  if (x, z) in _rect(0, z0, 10, z0 + 4)
                                  and y < 72])
                self.assertEqual(_open_attic(wings), [])

    def test_a_wing_roofed_over_keeps_the_eave_that_closes_it(self):
        """Where a higher wing roofs over a lower one's eave, the eave
        course that closes the lower wing's attic is kept: the column
        reaches down to it from the higher roof, so no attic is left open
        to the sky -- here a gable's eave under another's overhang, a
        gable's overhang eave under another's, and three hips over each
        other's walls, each of which a join of tops alone left open; and
        in 120 joins of two or three gables and hips drawn at random, at
        bases four blocks apart, none."""
        def gable(axis, **settings):
            return Roof(eaves=("north", "south") if axis == "x"
                        else ("west", "east"), block=STONE, slab=TILE_SLAB,
                        stair=TILE_STAIR, close_ends="minecraft:bricks",
                        **settings)
        cases = (
            [(gable("x", pitch=2), (2, 7, 4, 11), 67),
             (gable("x", pitch=1, overhang=1), (4, 6, 6, 8), 69)],
            [(gable("x", pitch=2, overhang=1), (6, 3, 8, 5), 72),
             (gable("x", pitch=2, overhang=1), (3, 6, 7, 8), 68)],
            [(_hip(pitch=2), (12, 2, 17, 7), 67),
             (_hip(pitch=1), (12, 5, 16, 8), 69),
             (_hip(pitch=1, overhang=1), (11, 4, 18, 6), 73)])
        for i, wings in enumerate(cases):
            with self.subTest(case=i):
                self.assertEqual(_open_attic(Wings(wings)), [])
        rng = random.Random(4)
        for trial in range(120):
            wings = []
            for _ in range(rng.choice((2, 3))):
                kind = rng.choice(("hip", "x", "z"))
                settings = dict(pitch=rng.choice((1, 2)),
                                overhang=rng.choice((0, 0, 1)))
                roof = (_hip(**settings) if kind == "hip"
                        else gable(kind, **settings))
                x0, z0 = rng.randrange(0, 12), rng.randrange(0, 12)
                outline = (x0, z0, x0 + rng.randrange(2, 10),
                           z0 + rng.randrange(2, 10))
                wings.append((roof, outline, 70 + rng.randrange(-4, 5)))
            with self.subTest(trial=trial, wings=wings):
                self.assertEqual(_open_attic(Wings(wings)), [])

    def test_each_column_is_laid_by_the_wing_that_owns_it(self):
        """Every setting of the wing that owns a column lays it: a gable of
        tiles beside one of granite -- different crowns, stairs, depths,
        slabs and end walls -- is, away from the granite, exactly the
        tiled gable laid alone, its attic's doubled slabs its own. A
        column two wings' end planes both close takes the block of the
        first listed, though the other owns it; and the write runs course
        by course along the first wing's ridge, x or z as it is listed."""
        granite = Roof(eaves=("north", "south"), pitch=2, crown="block",
                       block="minecraft:granite",
                       slab="minecraft:granite_slab", stair=None,
                       close_ends="minecraft:bricks")
        tiles = Roof(eaves=("west", "east"), rise=(2, 3, 4, 5, 8),
                     crown="slab", depth=(2, 3, 3), block=STONE,
                     slab=TILE_SLAB, stair=TILE_STAIR,
                     close_ends="minecraft:andesite")
        joined = Wings([(granite, (0, 0, 12, 6), 70),
                        (tiles, (8, 4, 16, 20), 70)]).plan(attic=PLANK)
        alone = tiles.plan((8, 4, 16, 20), 70, attic=PLANK)
        far = _rect(8, 9, 16, 20)

        def away(plan):
            return {cell: state for cell, state in plan.items()
                    if (cell[0], cell[2]) in far}
        self.assertEqual(away(joined), away(alone))
        names = {state.name for state in away(joined).values()}
        self.assertTrue({TILE_STAIR, TILE_SLAB, "minecraft:andesite"}
                        <= names)
        states = {str(state) for state in away(joined).values()}
        self.assertTrue({f"{TILE_SLAB}[type=double]",
                         f"{TILE_SLAB}[type=bottom]"} <= states)
        # The ridge, a whole block as the rise gives it, lowered to a slab.
        self.assertEqual(str(joined[(12, 73, 15)]),
                         f"{TILE_SLAB}[type=bottom]")

        low = Roof(eaves=("north", "south"), pitch=2, block=STONE,
                   slab=TILE_SLAB, stair=TILE_STAIR,
                   close_ends="minecraft:bricks")
        steep = Roof(eaves=("north", "south"), pitch=4, block=STONE,
                     slab=TILE_SLAB, stair=TILE_STAIR,
                     close_ends="minecraft:andesite")
        for wings, shared in (
                ([(low, (0, 0, 10, 6), 70), (steep, (0, 4, 10, 12), 70)],
                 "minecraft:bricks"),
                ([(steep, (0, 4, 10, 12), 70), (low, (0, 0, 10, 6), 70)],
                 "minecraft:andesite")):
            with self.subTest(first=wings[0][0]):
                plan = Wings(wings).plan(attic="hollow")
                ends = {}
                for (x, y, z), state in plan.items():
                    if x == 0 and state.name in ("minecraft:bricks",
                                                 "minecraft:andesite"):
                        ends.setdefault(z, set()).add(state.name)
                # The corners are eaves, laid from the springing.
                self.assertEqual(ends, {
                    z: {"minecraft:bricks" if z < 4 else
                        "minecraft:andesite" if z > 6 else shared}
                    for z in range(1, 12)})
                # The steep wing stands over the low one at z=5 and 6.
                self.assertEqual([_columns(plan)[(0, z)][1] for z in (5, 6)],
                                 [2 * 70 + 6, 2 * 70 + 10])

        x_first = [(Roof(eaves=("north", "south"), pitch=2, block=STONE,
                         slab=TILE_SLAB, stair=TILE_STAIR), (0, 0, 12, 6), 70),
                   (Roof(eaves=("west", "east"), pitch=2, block=STONE,
                         slab=TILE_SLAB, stair=TILE_STAIR), (8, 4, 14, 20),
                    70)]
        for wings, key in ((x_first, lambda c: (c[1], c[0], c[2])),
                           (x_first[::-1], lambda c: (c[1], c[2], c[0]))):
            with self.subTest(along=wings[0][0].eaves):
                plan = Wings(wings).plan(attic="hollow")
                self.assertEqual(list(plan), sorted(plan, key=key))


class GableBytesTests(unittest.TestCase):
    """``gable_roof`` is laid on the rule and writes what it always wrote."""

    #: The sha256 of every chunk payload ``gable_roof`` saves over every
    #: footprint 1..12 by 1..12, each roof alone in its own chunk, along
    #: each axis -- computed before it was laid on the rule.
    PAYLOADS = {
        "x": "9c83c26f9bddc844f726bb9acfebf255d9de65248747e505664d4e53cc8893fc",
        "z": "a85393309cc3e90a1e75bbc875f1a3fd6201f8d2420f581989135ff6512f1c97",
    }

    def build(self, axis, write) -> str:
        directory = tempfile.mkdtemp(prefix="roof-gables-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        make_save(directory, [(cx, cz) for cx in range(12) for cz in range(12)])
        save = MinecraftSave.load(directory)
        b = Builder(save)
        for width in range(1, 13):
            for length in range(1, 13):
                x0, z0 = 16 * (width - 1) + 1, 16 * (length - 1) + 1
                write(b, (x0, z0, x0 + width - 1, z0 + length - 1))
        save.save()
        return _payloads(directory)

    def test_gable_roof_writes_the_bytes_it_always_wrote(self):
        """Every footprint, both axes: ``gable_roof`` saves the bytes it
        saved before it was a Roof, and ``Builder.roof`` with the same rule
        saves them too."""
        for axis in ("x", "z"):
            with self.subTest(axis=axis):
                self.assertEqual(self.build(axis, lambda b, o: b.gable_roof(
                    *o, 70, axis)), self.PAYLOADS[axis])
                rule = _gable_rule(axis)
                self.assertEqual(self.build(axis, lambda b, o: b.roof(
                    rule, o, 70, attic="hollow")), self.PAYLOADS[axis])


if __name__ == "__main__":
    unittest.main()
