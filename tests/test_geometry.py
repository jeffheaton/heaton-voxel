"""Tests for heaton_voxel.craft.geometry: one compass, round shapes, lines
and frames.

Every function here was promoted out of a build that had its own copy, and
the copies are the oracle: ``examples/castle.py`` keeps its disc, ring and
facing (the example stays free of the judged tier), ``tools/`` keeps the
park's, the secret lab's and the Roman city's (historical, never moved onto
new APIs), and road's rasterization is copied here as it stood. Each test
pins the promotion to its source, cell for cell and in order, so the one
rule cannot drift from the builds it came from without a test saying so.

The frame is held to the same vocabulary a schematic turns by, over every
state the game has, and to the one writer that hand-wrote both of its
orientations: ``gable_roof``'s x branch, turned, is its z branch.
"""

from __future__ import annotations

import __future__
import ast
import copy
import functools
import importlib
import importlib.util
import itertools
import os
import pickle
import random
import sys
import types
import unittest
from fractions import Fraction

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "examples"))

from castle import disc_offsets, facing, ring_offsets  # noqa: E402
from heaton_voxel.blocks import BlockState  # noqa: E402
from heaton_voxel.craft import block_registry  # noqa: E402
from heaton_voxel.craft.blockinfo import check_state  # noqa: E402
from heaton_voxel.craft.build import Builder  # noqa: E402
from heaton_voxel.craft.build.roof import RoofMixin  # noqa: E402
from heaton_voxel.craft.errors import Refused  # noqa: E402
from heaton_voxel.craft.style import DEFAULT_PALETTE  # noqa: E402
from heaton_voxel.craft import geometry  # noqa: E402
from heaton_voxel.craft.geometry import (  # noqa: E402
    CLOCKWISE,
    COMPASS,
    OPPOSITE,
    ROAD_WINDOW,
    ROUNDINGS,
    WALL_WINDOW,
    Frame,
    axis_of,
    disc,
    heading_of,
    line,
    ring,
    smoothed_profile,
    turn,
)
from heaton_voxel.nbt import NBTError  # noqa: E402
from heaton_voxel.schem import (  # noqa: E402
    Schematic,
    properties_mirrored,
    properties_rotated_cw,
)


def _tool(relative: str, name: str):
    """A ``tools/`` module loaded by path, under a name of its own: the
    Roman city's and the secret lab's helpers are both bare modules that
    their scripts import from their own directory."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(_ROOT, "tools", relative))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PARK = _tool(os.path.join("park", "park_plan.py"), "_geometry_park_plan")
LAB = _tool(os.path.join("secret_lab", "lab_common.py"), "_geometry_lab_common")
ROMAN = _tool(os.path.join("roman_city", "common.py"), "_geometry_roman_common")

#: Every orientation a frame can have.
ORIENTATIONS = [(word, mirrored) for word in COMPASS for mirrored in (False, True)]


def _road_line(points):
    """``Builder.road``'s centerline, as site.py wrote it before geometry
    held it: the copy :func:`line` is pinned to."""
    line: list[tuple[int, int]] = []
    for (ax, az), (bx, bz) in zip(points, points[1:]):
        steps = max(abs(bx - ax), abs(bz - az))
        if steps == 0:
            continue
        for i in range(steps + 1):
            x = ax + round((bx - ax) * i / steps)
            z = az + round((bz - az) * i / steps)
            if not line or line[-1] != (x, z):
                line.append((x, z))
    if not line:
        raise NBTError("a road needs at least two distinct waypoints")
    return line


def _road_smoothing(heights, line, anchors):
    """``Builder.road``'s smoothing, as site.py wrote it before geometry
    held it (the ground read along ``line`` given as ``heights``): the copy
    :func:`smoothed_profile` is pinned to, its three passes and its
    cell-keyed anchors as they were."""
    smooth = []
    for i in range(len(line)):
        lo, hi = max(0, i - 3), min(len(line), i + 4)
        smooth.append(round(sum(heights[lo:hi]) / (hi - lo)))
    anchors = anchors or {}
    for _ in range(3):
        for i, cell in enumerate(line):
            if cell in anchors:
                smooth[i] = anchors[cell]
        for i in range(1, len(smooth)):
            if smooth[i] > smooth[i - 1] + 1:
                smooth[i] = smooth[i - 1] + 1
        for i in range(len(smooth) - 2, -1, -1):
            if smooth[i] > smooth[i + 1] + 1:
                smooth[i] = smooth[i + 1] + 1
    for i, cell in enumerate(line):
        if cell in anchors:
            smooth[i] = anchors[cell]
    return smooth


def _tool_function(relative: str, name: str, **names):
    """One function out of a ``tools/`` script, compiled from the script's
    own source without running the script -- which writes a world when it
    is run -- in a namespace holding only ``names``; its annotations are
    never evaluated. Each call is a fresh function in a fresh namespace;
    only the compiling is done once a script and name."""
    namespace = dict(names)
    exec(_tool_code(relative, name), namespace)
    return namespace[name]


@functools.lru_cache(maxsize=None)
def _tool_code(relative: str, name: str):
    """:func:`_tool_function`'s code: the one definition of ``name`` in the
    script, compiled on its own."""
    path = os.path.join(_ROOT, "tools", relative)
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    (node,) = [node for node in tree.body
               if isinstance(node, ast.FunctionDef) and node.name == name]
    return compile(ast.Module(body=[node], type_ignores=[]), path, "exec",
                   flags=__future__.annotations.compiler_flag, dont_inherit=True)


class _Ground:
    """A builder that answers ``ground`` from a table, for the tools'
    smoothing functions."""

    def __init__(self, table):
        self.table = table

    def ground(self, x, z):
        return self.table[(x, z)]


class _SetAssertions(unittest.TestCase):
    def assertSameCells(self, actual, expected, msg=""):
        """Two cell collections hold the same cells; on failure, name a few
        of the differences rather than diffing thousands of cells."""
        actual, expected = set(actual), set(expected)
        if actual != expected:
            self.fail(f"{msg}: extra {sorted(actual - expected)[:5]}, "
                      f"missing {sorted(expected - actual)[:5]}")


class LeafTests(unittest.TestCase):
    def test_geometry_stands_on_the_layers_below_and_nothing_judged(self):
        """A leaf: the deterministic tier, and from the judged tier only its
        errors and its plan-entry reader, neither of which imports any
        more of it. Everything else in craft can then lean on it."""
        craft = os.path.join(_ROOT, "heaton_voxel", "craft")

        def inward(path):
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            found = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level:
                    found.add("." * node.level + (node.module or ""))
                elif isinstance(node, ast.ImportFrom) and (
                        node.module or "").startswith("heaton_voxel"):
                    found.add(node.module)
                elif isinstance(node, ast.Import):
                    found.update(alias.name for alias in node.names
                                 if alias.name.startswith("heaton_voxel"))
            return found

        self.assertEqual(inward(os.path.join(craft, "geometry.py")),
                         {"..blocks", "..nbt", "..schem.transforms",
                          "._plan", ".errors"})
        self.assertEqual(inward(os.path.join(craft, "_plan.py")), {"..blocks"})
        self.assertEqual(inward(os.path.join(craft, "errors.py")), {"..nbt"})


class CompassTests(unittest.TestCase):
    def test_the_compass_is_the_games_axes(self):
        self.assertEqual(list(COMPASS.items()),
                         [("north", (0, -1)), ("south", (0, 1)),
                          ("east", (1, 0)), ("west", (-1, 0))])
        self.assertEqual(CLOCKWISE, ("north", "east", "south", "west"))
        self.assertEqual(list(OPPOSITE.items()),
                         [("north", "south"), ("south", "north"),
                          ("east", "west"), ("west", "east")])
        self.assertEqual({word: axis_of(word) for word in COMPASS},
                         {"north": "z", "south": "z", "east": "x", "west": "x"})
        for word in COMPASS:
            dx, dz = COMPASS[word]
            self.assertEqual(COMPASS[OPPOSITE[word]], (-dx, -dz))
            # One clockwise quarter seen from above, north up: R(x, z) = (-z, x).
            self.assertEqual(COMPASS[turn(word)], (-dz, dx))
            for quarters in range(-4, 5):
                with self.subTest(word=word, quarters=quarters):
                    turned = turn(word, quarters)
                    self.assertEqual(
                        turned,
                        CLOCKWISE[(CLOCKWISE.index(word) + quarters) % 4])
                    self.assertEqual(turn(turned, -quarters), word)
            self.assertEqual(turn(word, 2), OPPOSITE[word])
            self.assertEqual(turn(word, 0), word)
        # Shared by every writer, so nobody may change it under the others.
        with self.assertRaises(TypeError):
            COMPASS["up"] = (0, 0)
        with self.assertRaises(TypeError):
            OPPOSITE["north"] = "north"

    def test_turning_agrees_with_the_schematic_vocabulary(self):
        """One rotation vocabulary, checked two ways: a word turned here is
        the facing a schematic's quarter turn gives, and an opposite is a
        mirror along the word's own axis."""
        for word in COMPASS:
            with self.subTest(word=word):
                self.assertEqual(
                    turn(word, 1),
                    properties_rotated_cw({"facing": word})["facing"])
                self.assertEqual(
                    OPPOSITE[word],
                    properties_mirrored({"facing": word}, axis_of(word))["facing"])
                self.assertEqual(
                    word,
                    properties_mirrored({"facing": word},
                                        "z" if axis_of(word) == "x" else "x")["facing"])

    def test_bad_words_are_refused(self):
        for call in (lambda: turn("up"), lambda: turn("North"),
                     lambda: turn(["north"]), lambda: turn("north", 1.0),
                     lambda: turn("north", True), lambda: axis_of("down"),
                     lambda: axis_of(None)):
            with self.assertRaises(NBTError):
                call()
        with self.assertRaises(NBTError) as caught:
            turn("northeast")
        self.assertIn("('north', 'south', 'east', 'west')", str(caught.exception))

    def test_the_builders_tables_are_the_compass(self):
        """The builders' private tables are derived from the one compass,
        each in the order it always had: refusals print ``tuple(_RING)``
        and ``tuple(_HEADINGS)``, and survey's tables are read by lookup."""
        common = importlib.import_module("heaton_voxel.craft.build._common")
        survey = importlib.import_module("heaton_voxel.craft.survey")
        railway = importlib.import_module("heaton_voxel.craft.build.railway")
        build = importlib.import_module("heaton_voxel.craft.build")

        self.assertIs(common._COMPASS, COMPASS)
        self.assertIs(build._COMPASS, COMPASS)
        self.assertEqual(list(common._RING.items()),
                         [(word, COMPASS[word])
                          for word in ("east", "south", "west", "north")])
        self.assertEqual(list(common._HEADINGS.items()),
                         [(word, (axis_of(word), sum(COMPASS[word])))
                          for word in COMPASS])
        self.assertEqual(list(survey._UNIT.items()),
                         [(word, COMPASS[word])
                          for word in ("north", "south", "west", "east")])
        self.assertEqual(survey._FACING_OF,
                         {step: word for word, step in COMPASS.items()})
        self.assertIs(survey._OPPOSITE_WORD, OPPOSITE)
        self.assertIs(railway._OPPOSITE, OPPOSITE)
        self.assertIs(build._OPPOSITE, OPPOSITE)
        for word, (dx, dz) in COMPASS.items():
            self.assertEqual(railway._toward((4, 60, 4), (4 + dx, 61, 4 + dz)),
                             (word, 1))
        with self.assertRaises(KeyError):            # cells that do not touch
            railway._toward((4, 60, 4), (6, 60, 4))

    def test_one_table_of_ends_and_the_rest_from_the_compass(self):
        """The low and high end of each axis is one table, which the stairs
        and the walls share rather than each keeping its own, and the
        writers' other word tables -- a winding stair's turn, a vault's
        facing, the four sides a pane joins by, a roof's opposite pairs --
        say what the compass says, in the order each always had."""
        common = importlib.import_module("heaton_voxel.craft.build._common")
        stairs = importlib.import_module("heaton_voxel.craft.build.stairs")
        walls = importlib.import_module("heaton_voxel.craft.build.walls")
        vault = importlib.import_module("heaton_voxel.craft.build.vault")
        shells = importlib.import_module("heaton_voxel.craft.build.shells")
        roof = importlib.import_module("heaton_voxel.craft.build.roof")
        survey = importlib.import_module("heaton_voxel.craft.survey")

        self.assertIs(stairs._ENDS, common._ENDS)
        self.assertIs(walls._ENDS, common._ENDS)
        for axis, (low, high) in common._ENDS.items():
            self.assertEqual(axis_of(low), axis)
            self.assertEqual((sum(COMPASS[low]), sum(COMPASS[high])), (-1, 1))
        self.assertEqual(common._ENDS, {"z": ("north", "south"),
                                        "x": ("west", "east")})
        self.assertEqual(stairs._TURNING["clockwise"],
                         tuple(turn("east", k) for k in range(4)))
        self.assertEqual(stairs._TURNING["counterclockwise"],
                         tuple(turn("east", -k) for k in range(4)))
        for axis, (low, high) in vault._TOWARD.items():
            self.assertNotEqual(axis_of(low), axis, "a course faces across")
            self.assertEqual(OPPOSITE[low], high)
            self.assertEqual(sum(COMPASS[low]), 1, "the low side faces up")
        self.assertEqual(shells._SIDES, tuple(COMPASS))
        self.assertEqual(survey._SIDES, tuple(COMPASS))
        self.assertEqual(roof._PAIRS, (("north", "south"), ("east", "west")))
        for a, b in roof._PAIRS:
            self.assertEqual(OPPOSITE[a], b)


class HeadingTests(unittest.TestCase):
    SPAN = range(-20, 21)

    def steps(self):
        return [(dx, dz) for dx in self.SPAN for dz in self.SPAN if dx or dz]

    def test_ties_go_to_z_as_the_castle_breaks_them(self):
        for dx, dz in self.steps():
            with self.subTest(step=(dx, dz)):
                self.assertEqual(heading_of(dx, dz), facing(dx, dz))
                self.assertEqual(heading_of(dx, dz, ties="z"),
                                 ROMAN.facing_of(dx, dz))
        self.assertEqual([heading_of(d, d) for d in (1, -1)], ["south", "north"])
        self.assertEqual(heading_of(3, -3), "north")

    def test_ties_x_is_the_labs_rule(self):
        for dx, dz in self.steps():
            with self.subTest(step=(dx, dz)):
                self.assertEqual(heading_of(dx, dz, ties="x"),
                                 LAB.facing_of(dx, dz))
        self.assertEqual(heading_of(3, -3, ties="x"), "east")
        # Off the diagonal the two rules agree.
        self.assertEqual(
            [(dx, dz) for dx, dz in self.steps()
             if heading_of(dx, dz) != heading_of(dx, dz, ties="x")],
            [(dx, dz) for dx, dz in self.steps() if abs(dx) == abs(dz)])

    def test_no_step_has_no_heading(self):
        for call in (lambda: heading_of(0, 0), lambda: heading_of(0, 0, ties="x"),
                     lambda: heading_of(1, 0, ties="y"),
                     lambda: heading_of(1.5, 0), lambda: heading_of(True, 0),
                     lambda: heading_of(1, None)):
            with self.assertRaises(NBTError):
                call()


class DiscTests(_SetAssertions):
    def test_the_default_disc_is_the_castles_cell_for_cell(self):
        for r in range(0, 41):
            with self.subTest(r=r):
                self.assertEqual(disc(r), tuple(disc_offsets(r)))
        self.assertEqual(disc(3, center=(10, -7)),
                         tuple((10 + dx, -7 + dz) for dx, dz in disc_offsets(3)))

    def test_the_three_tool_rules_are_one_rule_at_three_radii(self):
        """The park's ``d² <= r²``, the lab's ``d² <= r² + r/2`` and the
        Roman city's ``d² <= r² + r`` are one rule -- the cell's center
        within ``r`` plus a slack of 0, 1/4 or 1/2 of a block -- run by the
        tools' own functions."""
        tools = {"inside": lambda cx, cz, r: PARK.disc_cells(cx, cz, r),
                 "quarter": lambda cx, cz, r: LAB.disc(cx, cz, r),
                 "half": lambda cx, cz, r: ROMAN.disc(cx, cz, r)}
        for rounding, tool in tools.items():
            for r in range(0, 33):
                for center in ((0, 0), (7, -3)):
                    with self.subTest(rounding=rounding, r=r, center=center):
                        self.assertSameCells(
                            disc(r, center=center, rounding=rounding),
                            tool(*center, r), f"{rounding} r={r}")
        # Membership hangs on d² alone, and every rule takes all of d² <= r²
        # and none of d² > r² + r, so checking every d² across that window
        # proves the three expressions equal far past what a disc can be
        # built to in a test.
        expressions = {"inside": lambda d2, r: d2 <= r * r,
                       "quarter": lambda d2, r: d2 <= r * r + r * 0.5,
                       "half": lambda d2, r: d2 <= r * r + r}
        for rounding, slack in ROUNDINGS.items():
            for r in range(0, 200):
                for d2 in range(max(0, r * r - 2), r * r + r + 3):
                    where = (rounding, r, d2)
                    within = Fraction(d2) <= (r + Fraction(slack, 4)) ** 2
                    self.assertEqual(within, expressions[rounding](d2, r), where)
                    self.assertEqual(within, 16 * d2 <= (4 * r + slack) ** 2, where)
        # The window is the whole question: the rule is monotone in d², and
        # at every slack it takes d² = r² and refuses d² = r² + r + 1.
        for slack in ROUNDINGS.values():
            for r in range(0, 200):
                self.assertTrue(16 * r * r <= (4 * r + slack) ** 2)
                self.assertFalse(16 * (r * r + r + 1) <= (4 * r + slack) ** 2)
        self.assertEqual(
            {rounding: [len(disc(r, rounding=rounding)) for r in range(1, 5)]
             for rounding in ROUNDINGS},
            {"inside": [5, 13, 29, 49], "quarter": [5, 21, 37, 61],
             "half": [9, 21, 37, 69]})

    def test_bad_radii_and_roundings_are_refused(self):
        for call in (lambda: disc(-1), lambda: disc(2.5), lambda: disc(True),
                     lambda: disc("3"), lambda: ring(-1),
                     lambda: disc(2, center=(0,)), lambda: disc(2, center=(0.5, 0)),
                     lambda: disc(2, center=(False, 0)), lambda: disc(2, center=None)):
            with self.assertRaises(NBTError):
                call()
        for rounding in ("round", None, "HALF"):
            with self.subTest(rounding=rounding):
                with self.assertRaises(NBTError) as caught:
                    disc(3, rounding=rounding)
                self.assertIn("('inside', 'quarter', 'half')", str(caught.exception))
                with self.assertRaises(NBTError):
                    ring(3, rounding=rounding)
        # Shared by every writer, so nobody may add or change one under the
        # others.
        with self.assertRaises(TypeError):
            ROUNDINGS["x"] = 3
        with self.assertRaises(TypeError):
            ROUNDINGS["half"] = 4


class RingTests(_SetAssertions):
    def test_the_ring_is_the_castles_in_order(self):
        """In order, not only as a set: alternate cells of the ring are
        where a tower's merlons go, so the order is the crown. The castle
        sorts by ``atan2``; the ring's key is exact."""
        for r in range(0, 41):
            with self.subTest(r=r):
                self.assertEqual(ring(r), tuple(ring_offsets(r)))
                self.assertEqual(ring(r, center=(5, -9)),
                                 tuple(ROMAN.ring(5, -9, r)))
        self.assertEqual(ring(1), ((-1, -1), (0, -1), (1, -1), (1, 0),
                                   (1, 1), (0, 1), (-1, 1), (-1, 0)))

    def test_the_labs_ring_is_the_quarter_ring(self):
        for r in range(1, 33):
            with self.subTest(r=r):
                self.assertSameCells(ring(r, center=(3, 4), rounding="quarter"),
                                     LAB.ring(3, 4, r), f"r={r}")

    def test_rings_enclose_tile_and_connect(self):
        for rounding in ROUNDINGS:
            for r in range(1, 41):
                with self.subTest(rounding=rounding, r=r):
                    rim = ring(r, rounding=rounding)
                    cells = set(rim)
                    whole = set(disc(r, rounding=rounding))
                    inner = set(disc(r - 1, rounding=rounding))
                    self.assertEqual(len(cells), len(rim))
                    self.assertFalse(cells & inner)
                    self.assertEqual(cells | inner, whole)
                    edge = {(x, z) for x, z in whole
                            if any((x + dx, z + dz) not in whole
                                   for dx, dz in COMPASS.values())}
                    self.assertLessEqual(edge, cells)
                    for x, z in rim:
                        touching = sum((x + dx, z + dz) in cells
                                       for dx in (-1, 0, 1) for dz in (-1, 0, 1)
                                       if dx or dz)
                        self.assertGreaterEqual(touching, 2, (x, z))
        self.assertEqual(ring(0), ((0, 0),))
        self.assertEqual(ring(0, center=(4, 4), rounding="inside"), ((4, 4),))


class LineTests(unittest.TestCase):
    def polylines(self, count=500):
        rng = random.Random(20261021)
        for _ in range(count):
            points = [(rng.randint(-40, 40), rng.randint(-40, 40))
                      for _ in range(rng.randint(2, 6))]
            for _ in range(rng.randint(0, 2)):      # zero-length segments
                at = rng.randrange(len(points))
                points.insert(at, points[at])
            if rng.random() < 0.3:                  # exact diagonals and ties
                ax, az = points[-1]
                d = rng.randint(1, 9)
                points.append((ax + d, az + rng.choice((d, -d, 2 * d, d // 2 or 1))))
            yield points

    def test_the_line_is_the_roads_rule(self):
        for points in self.polylines():
            with self.subTest(points=points):
                self.assertEqual(line(points), tuple(_road_line(points)))
                self.assertEqual(line(iter(points)), line(points))
                self.assertEqual(line([list(p) for p in points]), line(points))

    def test_the_tools_lines_step_as_road_does(self):
        """The secret lab's and the Roman city's copies of road's loop sit
        inside scripts that write a world when run, so they cannot be run
        here; their source is held to :func:`line`'s instead -- a segment's
        step count and the two per-step expressions, node for node, inside a
        loop over the same consecutive waypoint pairs. (The lab's copy has
        no zero-length skip; its waypoints never repeat.)"""
        def parsed(*parts):
            with open(os.path.join(_ROOT, *parts), encoding="utf-8") as handle:
                return ast.parse(handle.read())

        def walks(tree):
            """Each loop over ``(ax, az), (bx, bz)`` pairs: its step count
            and the expressions landing a step, as dumped nodes."""
            found = []
            for loop in ast.walk(tree):
                if not (isinstance(loop, ast.For)
                        and ast.unparse(loop.target) == "((ax, az), (bx, bz))"):
                    continue
                dumps = set()
                for node in ast.walk(loop):
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                            and node.func.id == "max"):
                        dumps.add(ast.dump(node))
                    elif (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
                          and isinstance(node.right, ast.Call)
                          and isinstance(node.right.func, ast.Name)
                          and node.right.func.id == "round"):
                        dumps.add(ast.dump(node))
                found.append(dumps)
            return found

        (ours,) = walks(parsed("heaton_voxel", "craft", "geometry.py"))
        self.assertEqual(len(ours), 3)      # max(...) and the x and z landings
        for parts in (("tools", "secret_lab", "m1_groundwork.py"),
                      ("tools", "roman_city", "jw_groundwork.py")):
            with self.subTest(tool=os.path.join(*parts)):
                theirs = walks(parsed(*parts))
                self.assertTrue(theirs, "the copy of road's loop is gone")
                self.assertTrue(all(ours <= dumps for dumps in theirs), theirs)

    def test_the_package_walks_a_line_in_one_place(self):
        """No module of the package but geometry steps from one waypoint
        toward the next: a road walks :func:`line`, and so must anything
        laid along one, or its lamps and edges drift off the road wherever
        two roundings part.

        Two shapes are looked for, in every module. One is the step
        itself, ``(b - a) * i / n`` divided or floor-divided, either factor
        first, which is there however the loop around it is written --
        ``zip``, ``pairwise``, an index or a ``while``. The other is a loop
        or a comprehension over consecutive waypoints, ``zip(p, p[1:])`` or
        ``pairwise(p)``, with a ``round`` in it, whatever it rounds. A
        rasterizer written some third way -- an error term, say -- is not
        found by either."""
        def step(node):
            """``(b - a) * i / n`` or ``... // n``."""
            return (isinstance(node, ast.BinOp)
                    and isinstance(node.op, (ast.Div, ast.FloorDiv))
                    and isinstance(node.left, ast.BinOp)
                    and isinstance(node.left.op, ast.Mult)
                    and any(isinstance(side, ast.BinOp) and isinstance(side.op, ast.Sub)
                            for side in (node.left.left, node.left.right)))

        def consecutive(node):
            """``zip(p, p[1:])`` or ``pairwise(p)``, bare or as an attribute."""
            if not isinstance(node, ast.Call):
                return False
            name = (node.func.id if isinstance(node.func, ast.Name) else
                    node.func.attr if isinstance(node.func, ast.Attribute) else None)
            if name == "pairwise":
                return len(node.args) == 1
            return (name == "zip" and len(node.args) == 2
                    and isinstance(node.args[1], ast.Subscript)
                    and ast.unparse(node.args[1].value) == ast.unparse(node.args[0])
                    and ast.unparse(node.args[1].slice) == "1:")

        def walks_pairs(node):
            """A loop or comprehension over consecutive waypoints that rounds."""
            if isinstance(node, ast.For):
                iters = [node.iter]
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp,
                                   ast.DictComp)):
                iters = [generator.iter for generator in node.generators]
            else:
                return False
            return any(consecutive(it) for it in iters) and any(
                isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                and inner.func.id == "round" for inner in ast.walk(node))

        walkers = {}
        for root, dirs, files in os.walk(os.path.join(_ROOT, "heaton_voxel")):
            dirs.sort()
            for name in sorted(files):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read())
                for node in ast.walk(tree):
                    if step(node) or walks_pairs(node):
                        walkers.setdefault(os.path.relpath(path, _ROOT), node.lineno)
        self.assertEqual(sorted(walkers),
                         [os.path.join("heaton_voxel", "craft", "geometry.py")], walkers)

    def test_the_float_step_is_exact_half_even_rounding(self):
        """Road's ``round(d * i / n)`` in floats is the exact fraction
        rounded half to even, for any segment the world can hold: ties at
        every scale, and steps that come within a hair of one."""
        rng = random.Random(26)
        limit = 2 ** 26 - 1
        for _ in range(20000):
            n = rng.choice((rng.randint(1, 300), rng.randint(1, limit)))
            d = rng.randint(-n, n)
            i = rng.randint(0, n)
            self.assertEqual(round(d * i / n), round(Fraction(d * i, n)), (d, i, n))
        for n in (2, 6, 2 ** 25, limit - 1):
            for d in (1, -1, 3, -(n - 1)):
                i = n // 2
                self.assertEqual(round(d * i / n), round(Fraction(d * i, n)), (d, i, n))

    def test_ties_go_to_even_so_a_line_is_not_its_reverse(self):
        self.assertEqual(line([(0, 0), (2, 1)]), ((0, 0), (1, 0), (2, 1)))
        self.assertEqual(line([(2, 1), (0, 0)]), ((2, 1), (1, 1), (0, 0)))
        self.assertEqual(line([(0, 0), (6, 3)]),
                         ((0, 0), (1, 0), (2, 1), (3, 2), (4, 2), (5, 2), (6, 3)))
        # A bend keeps its corner once, and a repeated waypoint adds nothing.
        self.assertEqual(line([(0, 0), (2, 0), (2, 0), (2, 2)]),
                         ((0, 0), (1, 0), (2, 0), (2, 1), (2, 2)))

    def test_consecutive_cells_touch(self):
        for points in self.polylines(200):
            cells = line(points)
            for (ax, az), (bx, bz) in zip(cells, cells[1:]):
                self.assertEqual(max(abs(bx - ax), abs(bz - az)), 1,
                                 (points, (ax, az), (bx, bz)))
            self.assertEqual(cells[0], next(p for p, q in zip(points, points[1:])
                                            if p != q))

    def test_a_line_needs_two_distinct_whole_waypoints(self):
        for points in ([(1, 1)], [(1, 1), (1, 1)], [(1, 1), (1, 1), (1, 1)], [],
                       [(0, 0), (1.5, 0)], [(0, 0), (True, 0)], [(0, 0), (1, 2, 3)],
                       [(0, 0), "ab"], 5, None):
            with self.subTest(points=points):
                with self.assertRaises(NBTError):
                    line(points)


def _grounds(rng, n):
    """``n`` ground heights along a path: mostly gentle, with cliffs."""
    y = rng.randint(40, 90)
    heights = []
    for _ in range(n):
        y += rng.choice((0, 0, 1, -1, 2, -2, rng.randint(-14, 14)))
        heights.append(y)
    return heights


def _envelope(heights, *, window, max_step, anchors, closed):
    """:func:`smoothed_profile` by its definition, the slow way: each mean
    summed over its own window (wrapped round a loop), anchors pinned, the
    lower envelope taken as a minimum over every pair of cells, anchors
    pinned again."""
    n = len(heights)
    means = []
    for i in range(n):
        if closed:
            window_cells = [heights[j] for j in range(n)
                            if min(abs(i - j), n - abs(i - j)) <= window]
        else:
            window_cells = heights[max(0, i - window):min(n, i + window + 1)]
        means.append(round(sum(window_cells) / len(window_cells)))
    pinned = [anchors.get(i, y) for i, y in enumerate(means)]
    if max_step is None:
        return tuple(pinned)

    def apart(i, j):
        return min(abs(i - j), n - abs(i - j)) if closed else abs(i - j)

    low = [min(pinned[j] + max_step * apart(i, j) for j in range(n))
           for i in range(n)]
    return tuple(anchors.get(i, y) for i, y in enumerate(low))


class SmoothedProfileTests(unittest.TestCase):
    def test_it_is_the_smoothing_road_and_the_two_walls_wrote(self):
        """Road's three passes with its cell-keyed anchors -- over lines that
        double back and cross themselves, so a cell is anchored on every
        visit -- the Roman wall's four at ``WALL_WINDOW`` and the lab
        wall's three over the higher face (compiled from the tools' own
        source) are each one call."""
        rng = random.Random(20261022)
        smoothed_line = _tool_function(
            os.path.join("roman_city", "jw_groundwork.py"), "smoothed_line")
        steps = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1))
        for trial in range(2000):
            n = rng.randint(1, 60)
            heights = _grounds(rng, n)
            cells = [(0, 0)]
            while len(cells) < n:
                if len(cells) > 1 and rng.random() < 0.2:
                    cells.append(cells[-2])                 # doubling back
                else:
                    x, z = cells[-1]
                    dx, dz = rng.choice(steps)
                    cells.append((x + dx, z + dz))
            ground = {}
            heights = [ground.setdefault(cell, y) for cell, y in zip(cells, heights)]
            visited = sorted(set(cells))
            anchors = {cell: rng.randint(min(heights) - 10, max(heights) + 10)
                       for cell in rng.sample(visited, rng.randint(0, min(4, len(visited))))}
            if rng.random() < 0.3:
                anchors[(99, 99)] = 0                       # off the line
            pins = {i: anchors[cell] for i, cell in enumerate(cells) if cell in anchors}
            with self.subTest(trial=trial):
                self.assertEqual(
                    list(smoothed_profile(heights, window=ROAD_WINDOW, anchors=pins)),
                    _road_smoothing(heights, cells, anchors))
                walk = [(i, 0) for i in range(n)]
                self.assertEqual(
                    smoothed_line(_Ground(dict(zip(walk, heights))), walk),
                    list(smoothed_profile(heights, window=WALL_WINDOW)))
        for trial in range(500):
            n = rng.randint(1, 60)
            plane = rng.randint(50, 80)
            outer, inner = _grounds(rng, n), _grounds(rng, n)
            table = {(i, side): y for side, faces in enumerate((outer, inner))
                     for i, y in enumerate(faces) if rng.random() < 0.9}
            wall_tops = _tool_function(
                os.path.join("secret_lab", "m1_groundwork.py"), "_wall_tops",
                P=types.SimpleNamespace(PLANE=plane))
            tops = wall_tops(_Ground(table), [(i, 0) for i in range(n)],
                             [(i, 1) for i in range(n)])
            faces = [max(table.get((i, 0), plane), table.get((i, 1), plane), plane) + 7
                     for i in range(n)]
            with self.subTest(trial=trial):
                self.assertEqual(tops, list(smoothed_profile(faces, window=0)))

    def test_one_sweep_each_way_is_the_lower_envelope(self):
        """The result is ``min_j(p[j] + max_step·|i - j|)`` over the pinned
        means -- round the loop when closed -- with the anchors pinned again;
        nothing ends above its mean or its anchor; every step is within
        ``max_step`` but beside an anchor standing above it; and a second
        pass, the passes road and the walls ran, moves nothing."""
        rng = random.Random(6)
        for trial in range(3000):
            n = rng.randint(1, 40)
            heights = _grounds(rng, n)
            window = rng.randint(0, 7)
            max_step = rng.choice((None, 0, 1, 1, 1, 2, 3))
            closed = rng.random() < 0.4
            pins = {i: rng.randint(min(heights) - 8, max(heights) + 8)
                    for i in rng.sample(range(n), rng.randint(0, min(3, n)))}
            with self.subTest(trial=trial, n=n, window=window,
                              max_step=max_step, closed=closed):
                got = smoothed_profile(heights, window=window, max_step=max_step,
                                       anchors=pins, closed=closed)
                self.assertIsInstance(got, tuple)
                self.assertTrue(all(type(y) is int for y in got))
                self.assertEqual(got, _envelope(heights, window=window,
                                                max_step=max_step, anchors=pins,
                                                closed=closed))
                means = smoothed_profile(heights, window=window, max_step=None,
                                         closed=closed)
                for i, y in enumerate(got):
                    self.assertLessEqual(y, pins.get(i, means[i]))
                if max_step is None:
                    continue
                pairs = list(zip(range(n), range(1, n)))
                if closed and n > 1:
                    pairs.append((n - 1, 0))
                for i, j in pairs:
                    if abs(got[i] - got[j]) > max_step:
                        high = i if got[i] > got[j] else j
                        self.assertIn(high, pins, (i, j, got))
                self.assertEqual(smoothed_profile(got, window=0, max_step=max_step,
                                                  anchors=pins, closed=closed), got)

    def test_a_closed_profile_wraps(self):
        """A loop's last cell lies beside its first: the clamp is measured
        round it, and so is the mean."""
        hill = [0, 0, 0, 0, 10, 10, 10, 10]
        self.assertEqual(smoothed_profile(hill, window=0),
                         (0, 0, 0, 0, 1, 2, 3, 4))
        self.assertEqual(smoothed_profile(hill, window=0, closed=True),
                         (0, 0, 0, 0, 1, 2, 2, 1))
        spike = [0, 0, 0, 0, 0, 0, 0, 7]
        self.assertEqual(smoothed_profile(spike, window=1, max_step=None),
                         (0, 0, 0, 0, 0, 0, 2, 4))
        self.assertEqual(smoothed_profile(spike, window=1, max_step=None,
                                          closed=True),
                         (2, 0, 0, 0, 0, 0, 2, 2))
        # A window as long as the loop counts each cell once: the whole
        # loop's mean.
        self.assertEqual(smoothed_profile([0, 0, 9], window=2, max_step=None,
                                          closed=True), (3, 3, 3))
        self.assertEqual(smoothed_profile([0, 0, 0, 9], window=1, max_step=None,
                                          closed=True), (3, 0, 3, 3))

    def test_the_mean_is_roads_float_rounding(self):
        """Half to even, as ``round(sum / count)`` always rounded road's
        means; ``window=0`` with no clamp is the profile itself; an empty
        profile is empty; a high anchor stays a step; and ``max_step=0``
        levels the whole profile, not each run between anchors."""
        self.assertEqual(smoothed_profile([0, 1], window=1, max_step=None), (0, 0))
        self.assertEqual(smoothed_profile([2, 3], window=1, max_step=None), (2, 2))
        self.assertEqual(smoothed_profile([1, 2, 3, 4], window=1, max_step=None),
                         (2, 2, 3, 4))
        ground = [64, 70, 58, 58, 90, 61]
        self.assertEqual(smoothed_profile(ground, window=0, max_step=None),
                         tuple(ground))
        self.assertEqual(smoothed_profile(iter(ground), window=0, max_step=None),
                         tuple(ground))
        self.assertEqual(smoothed_profile([], window=3), ())
        self.assertEqual(smoothed_profile([62] * 7, window=ROAD_WINDOW,
                                          anchors={3: 66}),
                         (62, 62, 62, 66, 62, 62, 62))
        self.assertEqual(smoothed_profile([62] * 7, window=ROAD_WINDOW,
                                          anchors={3: 58}),
                         (61, 60, 59, 58, 59, 60, 61))
        self.assertEqual(smoothed_profile([62, 66, 70, 74], window=0, max_step=0),
                         (62, 62, 62, 62))
        # At max_step 0 the envelope is the profile's one lowest pinned
        # mean, across every anchor: a run before a high anchor comes down
        # to a lower run after it, and a low anchor takes everything down.
        self.assertEqual(smoothed_profile([9, 9, 30, 9, 5, 5], window=0,
                                          max_step=0, anchors={2: 30}),
                         (5, 5, 30, 5, 5, 5))
        self.assertEqual(smoothed_profile([20, 20, 20, 20, 9, 9], window=0,
                                          max_step=0, anchors={3: 3}),
                         (3, 3, 3, 3, 3, 3))

    def test_it_refuses_what_it_cannot_smooth(self):
        """``NBTError``, naming the value: a height that is not an int (a
        bool is not one), no sequence at all, a bad window, max_step or
        closed, anchors that are not a mapping, and an anchor outside the
        profile or not a whole height."""
        cases = [
            (dict(heights=[64, 64.0]), "64.0"),
            (dict(heights=[64, True]), "True"),
            (dict(heights=[64, None]), "None"),
            (dict(heights="abc"), "'a'"),
            (dict(heights=5), "5"),
            (dict(heights=[64], window=-1), "-1"),
            (dict(heights=[64], window=1.0), "1.0"),
            (dict(heights=[64], window=True), "True"),
            (dict(heights=[64], window=None), "None"),
            (dict(heights=[64], max_step=-1), "-1"),
            (dict(heights=[64], max_step=1.5), "1.5"),
            (dict(heights=[64], max_step=False), "False"),
            (dict(heights=[64], max_step="1"), "'1'"),
            (dict(heights=[64], closed=1), "1"),
            (dict(heights=[64], closed=None), "None"),
            (dict(heights=[64], anchors=[(0, 64)]), "[(0, 64)]"),
            (dict(heights=[64, 65, 66], anchors={3: 64}), "3"),
            (dict(heights=[64, 65, 66], anchors={-1: 64}), "-1"),
            (dict(heights=[64, 65, 66], anchors={True: 64}), "True"),
            (dict(heights=[64, 65, 66], anchors={(0, 0): 64}), "(0, 0)"),
            (dict(heights=[64, 65, 66], anchors={0: 64.5}), "64.5"),
            (dict(heights=[64, 65, 66], anchors={0: True}), "True"),
            (dict(heights=[], anchors={0: 64}), "0"),
        ]
        for given, named in cases:
            arguments = {"window": 3, **given}
            heights = arguments.pop("heights")
            with self.subTest(given=given):
                with self.assertRaises(NBTError) as caught:
                    smoothed_profile(heights, **arguments)
                self.assertIn(named, str(caught.exception))

    def test_the_named_windows(self):
        """Road's mean is three cells either side, the Roman wall's five:
        choices, named once, and exported with the rule."""
        self.assertEqual((ROAD_WINDOW, WALL_WINDOW), (3, 5))
        for name in ("ROAD_WINDOW", "WALL_WINDOW", "smoothed_profile"):
            self.assertIn(name, geometry.__all__)
        with self.assertRaises(TypeError):
            smoothed_profile([64], 3)               # the window is named


class _GableCapture(RoofMixin):
    """Just enough Builder for ``gable_roof``: its planned ops, uncommitted,
    its materials resolved as a Builder with no palette resolves them. The
    roof plans states, as its rule lays them, where it once planned text."""

    _palette = DEFAULT_PALETTE
    _materials = Builder._materials

    def __init__(self):
        self.ops = None

    def _alive(self):
        pass

    def _commit(self, ops, *, what=None):
        self.ops = list(ops)


def _gable(x0, z0, x1, z1, axis):
    capture = _GableCapture()
    capture.gable_roof(x0, z0, x1, z1, 70, axis)
    return capture.ops


class FrameTests(unittest.TestCase):
    ORIGIN = (3, 64, -9)

    def frames(self, origin=ORIGIN):
        return [Frame(origin, word, mirrored=mirrored)
                for word, mirrored in ORIENTATIONS]

    def test_the_identity_frame_is_the_world(self):
        frame = Frame((5, -3, 7))
        self.assertEqual((frame.origin, frame.facing, frame.mirrored, frame.quarters),
                         ((5, -3, 7), "south", False, 0))
        self.assertEqual(frame.cell(2, 1, -4), (7, -2, 3))
        self.assertEqual(frame.step(2, -4), (2, -4))
        stair = BlockState("minecraft:oak_stairs", {"facing": "north", "half": "top"})
        self.assertIs(frame.state(stair), stair)
        self.assertEqual(frame.state("minecraft:oak_stairs[facing=north,half=top]"), stair)
        self.assertEqual(frame.state(("minecraft:oak_stairs",
                                      {"facing": "north", "half": "top"})), stair)
        self.assertEqual(repr(frame), "Frame((5, -3, 7), 'south')")
        self.assertEqual(repr(Frame((0, 0, 0), "west", mirrored=True)),
                         "Frame((0, 0, 0), 'west', mirrored=True)")
        self.assertEqual([Frame(self.ORIGIN, word).quarters for word in COMPASS],
                         [2, 0, 3, 1])
        self.assertEqual([Frame(self.ORIGIN, turn("south", q)).quarters
                          for q in range(4)], [0, 1, 2, 3])

    def test_frames_are_values(self):
        frame = Frame(self.ORIGIN, "west", mirrored=True)
        self.assertEqual(frame, Frame(list(self.ORIGIN), "west", mirrored=True))
        self.assertEqual(hash(frame), hash(Frame(self.ORIGIN, "west", mirrored=True)))
        self.assertNotEqual(frame, Frame(self.ORIGIN, "west"))
        self.assertNotEqual(frame, Frame((0, 0, 0), "west", mirrored=True))
        self.assertEqual(len(set(self.frames())), 8)
        with self.assertRaises(AttributeError):
            frame.facing = "east"
        with self.assertRaises(AttributeError):
            frame._quarters = 0
        # A value copies as itself and pickles to an equal one, so a plan or
        # a result holding a frame can be copied whole.
        self.assertIs(copy.copy(frame), frame)
        self.assertIs(copy.deepcopy(frame), frame)
        held = {"frame": frame, "frames": [frame, Frame(self.ORIGIN)]}
        self.assertEqual(copy.deepcopy(held), held)
        for each in self.frames():
            with self.subTest(frame=each):
                back = pickle.loads(pickle.dumps(each))
                self.assertEqual(back, each)
                self.assertEqual((back.origin, back.facing, back.mirrored, back.quarters),
                                 (each.origin, each.facing, each.mirrored, each.quarters))
                stair = "minecraft:oak_stairs[facing=north,shape=inner_left]"
                self.assertEqual(back.state(stair), each.state(stair))
                self.assertEqual(back.cell(2, 1, -3), each.cell(2, 1, -3))

    def test_a_states_property_order_is_its_own(self):
        """Two equal states whose properties arrive in different orders each
        come back in the order a fresh frame turns them to, whichever one a
        frame met first: in a chunk older than 26.3 that order is palette
        bytes, so it must not hang on what a frame happened to turn before."""
        one = BlockState.parse("minecraft:oak_fence[east=true,north=false,"
                               "south=false,west=false,waterlogged=false]")
        two = BlockState.parse("minecraft:oak_fence[north=false,east=true,"
                               "south=false,west=false,waterlogged=false]")
        self.assertEqual(one, two)
        self.assertNotEqual(list(one.properties), list(two.properties))
        for word, mirrored in ORIENTATIONS:
            for inverse in (False, True):
                def turned(frame, state):
                    return frame.unstate(state) if inverse else frame.state(state)

                for order in ((one, two), (two, one)):
                    frame = Frame(self.ORIGIN, word, mirrored=mirrored)
                    for state in order:
                        with self.subTest(frame=frame, inverse=inverse,
                                          first=list(order[0].properties),
                                          state=list(state.properties)):
                            fresh = turned(Frame(self.ORIGIN, word, mirrored=mirrored),
                                           state)
                            self.assertEqual(list(turned(frame, state).properties),
                                             list(fresh.properties))

    def test_local_inverts_cell(self):
        cube = list(itertools.product(range(-3, 4), repeat=3))
        for frame in self.frames():
            with self.subTest(frame=frame):
                for local in cube:
                    self.assertEqual(frame.local(*frame.cell(*local)), local)
                self.assertEqual(len({frame.cell(*local) for local in cube}), len(cube))
                low, high = frame.box((-3, -3, -3), (3, 3, 3))
                self.assertEqual((low[1], high[1]), (61, 67))
                for local in cube:
                    x, y, z = frame.cell(*local)
                    self.assertTrue(low[0] <= x <= high[0] and low[2] <= z <= high[2])

    def test_cells_and_states_turn_together(self):
        for frame in self.frames():
            with self.subTest(frame=frame):
                for word in COMPASS:
                    world = frame.heading(word)
                    turned = frame.state(f"minecraft:oak_stairs[facing={word}]")
                    self.assertEqual(turned.properties["facing"], world)
                    self.assertEqual(frame.step(*COMPASS[word]), COMPASS[world])
                # out is the facing, and +across runs to the right of a
                # viewer outside the face looking at it -- the left in a
                # mirror.
                self.assertEqual(frame.step(0, 1), COMPASS[frame.facing])
                self.assertEqual(frame.step(1, 0),
                                 COMPASS[turn(frame.facing, 1 if frame.mirrored else -1)])
                self.assertEqual((frame.heading("up"), frame.heading("down")),
                                 ("up", "down"))
                self.assertEqual(frame.state("minecraft:oak_log[axis=x]").properties["axis"],
                                 axis_of(frame.heading("east")))

    def test_a_mirror_changes_hands(self):
        frame = Frame((0, 0, 0), "south", mirrored=True)
        self.assertEqual(frame.cell(1, 0, 2), (-1, 0, 2))

        def turned(text):
            return frame.state(text).properties

        self.assertEqual(turned("minecraft:oak_door[facing=north,half=lower,hinge=left]"),
                         {"facing": "north", "half": "lower", "hinge": "right"})
        self.assertEqual(turned("minecraft:oak_stairs[facing=north,shape=inner_left]"),
                         {"facing": "north", "shape": "inner_right"})
        self.assertEqual(turned("minecraft:chest[facing=north,type=left]"),
                         {"facing": "north", "type": "right"})
        self.assertEqual(turned("minecraft:oak_stairs[facing=east]"), {"facing": "west"})
        north = BlockState("minecraft:ladder", {"facing": "north"})
        self.assertIs(frame.state(north), north)
        # The caller's own state, every time, not an equal one met before.
        again = BlockState("minecraft:ladder", {"facing": "north"})
        self.assertIs(frame.state(again), again)

    def test_every_state_the_game_has_turns_and_turns_back(self):
        """Over every property set the game has, every combination of its
        values, in all eight orientations: the turn inverts, the turned
        state is one the game takes, and nothing needs refusing. Each
        set is tried on the first block that has it."""
        first = {}
        for name in sorted(block_registry.BLOCK_IDS):
            properties = block_registry.properties_of(name)
            first.setdefault(tuple(sorted(properties.items())), name)
        states = [BlockState(name, dict(zip((key for key, _ in options), values)))
                  for options, name in first.items()
                  for values in itertools.product(*(vals for _, vals in options))]
        self.assertGreater(len(states), 6000)
        for frame in self.frames():
            with self.subTest(frame=frame):
                for state in states:
                    turned = frame.state(state)
                    self.assertEqual(frame.unstate(turned), state)
                    self.assertIs(check_state(turned), turned)

    def test_a_state_the_vocabulary_cannot_turn_is_refused(self):
        odd = BlockState("mod:thing", {"side": "north"})
        with self.assertRaises(Refused) as caught:
            Frame(self.ORIGIN, "west").state(odd)
        self.assertIn("side=north", str(caught.exception))
        self.assertEqual(len(caught.exception.faults), 1)
        with self.assertRaises(Refused):
            Frame(self.ORIGIN, "south", mirrored=True).unstate(odd)
        # An identity frame turns nothing, so it has nothing to refuse.
        self.assertIs(Frame(self.ORIGIN).state(odd), odd)
        self.assertIs(Frame((9, 9, 9)).unstate(odd), odd)

    def test_frames_compose(self):
        samples = [BlockState.parse(text) for text in (
            "minecraft:oak_stairs[facing=north,half=bottom,shape=inner_left]",
            "minecraft:rail[shape=north_east]",
            "minecraft:powered_rail[shape=ascending_west]",
            "minecraft:oak_sign[rotation=3]",
            "minecraft:oak_fence[east=true,north=false,south=true,west=false]",
            "minecraft:oak_door[facing=east,hinge=left]",
            "minecraft:chest[facing=west,type=right]",
            "minecraft:crafter[orientation=up_north]",
            "minecraft:oak_log[axis=x]",
        )]
        cells = list(itertools.product((-2, 0, 3), (0, 5), (-1, 4)))
        for inner in self.frames((2, 1, -5)):
            for outer in self.frames((-7, 60, 11)):
                with self.subTest(inner=inner, outer=outer):
                    both = inner.within(outer)
                    for cell in cells:
                        self.assertEqual(both.cell(*cell),
                                         outer.cell(*inner.cell(*cell)))
                    for state in samples:
                        # As states: a stage that changes nothing hands its
                        # input back, so the order the properties come out
                        # in follows the stages taken, as a schematic's do.
                        self.assertEqual(both.state(state),
                                         outer.state(inner.state(state)))
                        self.assertEqual(both.unstate(state),
                                         inner.unstate(outer.unstate(state)))
        with self.assertRaises(NBTError):
            Frame(self.ORIGIN).within((0, 0, 0))

    def test_a_frame_turns_as_a_schematic_turns(self):
        """The lower tier's flip-then-rotate, twin-verified, is the same
        map: every cell of a schematic lands where the frame puts it, about
        the schematic's origin, holding the frame's state -- property order
        included, which is palette bytes in a legacy chunk."""
        first = {}
        for name in sorted(block_registry.BLOCK_IDS):
            options = tuple(sorted(block_registry.properties_of(name).items()))
            first.setdefault(options, name)
        rng = random.Random(7)
        states = [BlockState(name, {key: rng.choice(values) for key, values in options})
                  for options, name in first.items() for _ in range(4)]
        # A mirror that changes nothing hands the state back, and the turn
        # after it renames keys from that order: the case a single combined
        # turn would get wrong.
        states += [BlockState.parse(text) for text in (
            "minecraft:oak_fence[east=true,north=false,south=true,west=true]",
            "minecraft:cobblestone_wall[east=low,north=tall,south=none,up=true,west=low]",
            "minecraft:glass_pane[east=false,north=true,south=true,west=false]")]
        width, length = 7, -(-len(states) // 7)
        schematic = Schematic.new(width, 2, length, block_registry.DATA_VERSION)
        schematic.offset = (-3, 1, -5)
        cells = {}
        for i, state in enumerate(states):
            cell = (i % width, i % 2, i // width)
            schematic.set_block(*cell, state)
            cells[cell] = state
        for word, mirrored in ORIENTATIONS:
            frame = Frame((0, 0, 0), word, mirrored=mirrored)
            with self.subTest(frame=frame):
                placed = schematic.flipped("x") if mirrored else schematic
                placed = placed.rotated(90 * frame.quarters)
                for (x, y, z), state in cells.items():
                    ox, oy, oz = schematic.offset
                    wx, wy, wz = frame.cell(ox + x, oy + y, oz + z)
                    px, py, pz = placed.offset
                    found = placed.get_block(wx - px, wy - py, wz - pz)
                    want = frame.state(state)
                    self.assertEqual(found, want, (x, y, z))
                    self.assertEqual(list(found.properties.items()),
                                     list(want.properties.items()), (x, y, z))

    def test_a_frame_reproduces_the_gable_roofs_other_branch(self):
        """``gable_roof`` wrote its two orientations by hand; a frame that
        swaps x and z -- facing east, mirrored -- turns the x branch, run
        over the transposed rectangle, into the z branch exactly: cells,
        states, property order and the order of the writes."""
        frame = Frame((0, 0, 0), "east", mirrored=True)
        for x0, z0, x1, z1 in ((0, 0, 4, 6), (0, 0, 5, 3), (-3, 2, 1, 2),
                               (2, -5, 9, -1), (10, 10, 10, 10)):
            with self.subTest(rectangle=(x0, z0, x1, z1)):
                across = _gable(z0, x0, z1, x1, "x")
                along = _gable(x0, z0, x1, z1, "z")
                self.assertEqual(len(across), len(along))
                for (kind, cell, block), (want_kind, want_cell, want) in zip(across, along):
                    self.assertEqual(kind, want_kind)
                    self.assertEqual(frame.cell(*cell), want_cell)
                    got = frame.state(block)
                    if not isinstance(want, BlockState):
                        want = BlockState.parse(want)
                    self.assertEqual(got, want)
                    self.assertEqual(list(got.properties.items()),
                                     list(want.properties.items()))

    def test_bad_frames_are_refused(self):
        for make in (lambda: Frame((0, 0, 0), "up"), lambda: Frame((0, 0, 0), None),
                     lambda: Frame((0, 0, 0), mirrored="yes"),
                     lambda: Frame((0, 0, 0), mirrored=1),
                     lambda: Frame((0, 0)), lambda: Frame((0, 0, 0.5)),
                     lambda: Frame((0, True, 0)), lambda: Frame(None),
                     lambda: Frame((0, 0, 0)).heading("sideways")):
            with self.assertRaises(NBTError):
                make()
        # A cell is whole: a fraction or a bool in local or world terms is
        # refused, in the identity frame as in any other, never carried
        # through to a "cell" half a block over.
        for frame in (Frame((100, 64, 200)), Frame((100, 64, 200), "west", mirrored=True)):
            for call in (lambda: frame.cell(0.5, 0, 1), lambda: frame.cell(0, 1.0, 0),
                         lambda: frame.cell(0, 0, True), lambda: frame.cell(True, 0, 0),
                         lambda: frame.step(1.25, 0), lambda: frame.step(0, False),
                         lambda: frame.local(99.7, 64, 202), lambda: frame.local(99, True, 202),
                         lambda: frame.local(99, 64, "202"),
                         lambda: frame.box((0, 0, 0), (4, 3, 6.0)),
                         lambda: frame.box((0, 0), (4, 3, 6)),
                         lambda: frame.box(None, (4, 3, 6))):
                with self.subTest(frame=frame):
                    with self.assertRaises(NBTError):
                        call()
        with self.assertRaises(NBTError) as caught:
            Frame((0, 0, 0), "west").cell(0.5, 0, 1)
        self.assertIn("across", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
