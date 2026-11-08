"""Tests for heaton_voxel.craft.field: distance fields over a set of columns.

The transform and the blur were promoted out of the lake build
(``tools/lake/field.py``), which stays as that build's record; the lake's
own module, loaded by path, is the oracle they are held to -- their source
node for node, and their output on seeded random grids. The two fields over
a set of columns are held to brute force: every outside column within one
of the set's bounding box, measured directly, which is every column that
could be the nearest.
"""

from __future__ import annotations

import ast
import importlib.util
import math
import os
import random
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from heaton_voxel.craft import field  # noqa: E402
from heaton_voxel.craft.field import (  # noqa: E402
    blur,
    chessboard,
    distance_to_edge,
    edt,
)
from heaton_voxel.nbt import NBTError  # noqa: E402

_LAKE_FIELD = os.path.join(_ROOT, "tools", "lake", "field.py")


def _lake():
    """``tools/lake/field.py``, loaded by path under a name of its own (the
    lake scripts import it as a bare ``field`` from their own directory)."""
    spec = importlib.util.spec_from_file_location("_field_lake_field", _LAKE_FIELD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LAKE = _lake()


def _outside_near(cells):
    """Every column within one of ``cells``' bounding box that is not in
    it: the ring round the box is all outside, so the nearest outside
    column to any cell is among these."""
    xs = [x for x, _ in cells]
    zs = [z for _, z in cells]
    return [(x, z) for x in range(min(xs) - 1, max(xs) + 2)
            for z in range(min(zs) - 1, max(zs) + 2) if (x, z) not in cells]


def _brute_euclid(cells):
    outside = _outside_near(cells)
    return {(x, z): min(math.sqrt((x - ox) ** 2 + (z - oz) ** 2)
                        for ox, oz in outside)
            for x, z in sorted(cells)}


def _brute_king(cells):
    outside = _outside_near(cells)
    return {(x, z): min(max(abs(x - ox), abs(z - oz)) for ox, oz in outside)
            for x, z in sorted(cells)}


def _rect(x0, z0, x1, z1):
    return {(x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)}


def _pieces(cells):
    """``cells``' 4-connected pieces, each a set, found the slow way."""
    left, pieces = set(cells), []
    while left:
        piece, todo = set(), [left.pop()]
        while todo:
            x, z = todo.pop()
            piece.add((x, z))
            for step in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
                if step in left:
                    left.remove(step)
                    todo.append(step)
        pieces.append(piece)
    return pieces


#: The plans the roof footprints are measured on: an L, a T, a U, a cross
#: and a courtyard (a ring round a hole), at negative coordinates too.
PLANS = {
    "L": _rect(0, 0, 6, 2) | _rect(0, 0, 2, 8),
    "T": _rect(-5, 0, 5, 2) | _rect(-1, 0, 1, 9),
    "U": _rect(0, 0, 8, 2) | _rect(0, 0, 2, 7) | _rect(6, 0, 8, 7),
    "cross": _rect(-7, -1, 7, 1) | _rect(-1, -7, 1, 7),
    "courtyard": _rect(-10, -8, 0, 2) - _rect(-7, -5, -3, -1),
}


def _random_sets(count, seed, span=12):
    """Seeded random column sets in ``[-span, span]``², each a union of a
    few rectangles less a few holes -- several pieces, holes, a lone
    column, negative coordinates -- and never empty."""
    rng = random.Random(seed)
    for _ in range(count):
        cells = set()
        for _ in range(rng.randint(1, 4)):
            x0, z0 = rng.randint(-span, span - 1), rng.randint(-span, span - 1)
            cells |= _rect(x0, z0, min(span, x0 + rng.randint(0, 9)),
                           min(span, z0 + rng.randint(0, 9)))
        for _ in range(rng.randint(0, 3)):
            x0, z0 = rng.randint(-span, span), rng.randint(-span, span)
            cells -= _rect(x0, z0, x0 + rng.randint(0, 2), z0 + rng.randint(0, 2))
        if rng.random() < 0.3:
            cells.add((rng.randint(-span, span), rng.randint(-span, span)))
        yield cells or {(0, 0)}


class LeafTests(unittest.TestCase):
    def test_field_stands_on_nbt_alone(self):
        """A leaf: from the package, only the ``NBTError`` a bad cell set is
        refused with; from the standard library, only ``math``."""
        with open(field.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                found.add("." * node.level + (node.module or ""))
            elif isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
        self.assertEqual(found, {"__future__", "math", "..nbt"})


class EdtBlurTests(unittest.TestCase):
    def test_the_lakes_functions_are_promoted_verbatim(self):
        """``_edt_1d``, ``edt`` and ``blur`` are the lake's, node for node,
        docstrings included, and ``INF`` is the lake's infinity."""
        def functions(path):
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            return {node.name: ast.dump(node) for node in tree.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name in ("_edt_1d", "edt", "blur")}

        ours, theirs = functions(field.__file__), functions(_LAKE_FIELD)
        self.assertEqual(sorted(theirs), ["_edt_1d", "blur", "edt"])
        self.assertEqual(ours, theirs)
        self.assertEqual(field.INF, LAKE.INF)

    def test_edt_and_blur_are_the_lakes(self):
        rng = random.Random(20261022)
        for _ in range(200):
            w, h = rng.randint(1, 24), rng.randint(1, 24)
            density = rng.random()
            mask = [rng.random() < density for _ in range(w * h)]
            with self.subTest(w=w, h=h, density=density):
                grid = edt(mask, w, h)
                self.assertEqual(grid, LAKE.edt(mask, w, h))
                depth = [math.sqrt(grid[k // w][k % w]) * rng.choice((0.75, 1.0))
                         for k in range(w * h)]
                passes, radius = rng.randint(0, 3), rng.randint(1, 3)
                self.assertEqual(blur(depth, mask, w, h, passes, radius),
                                 LAKE.blur(depth, mask, w, h, passes, radius))


class DistanceToEdgeTests(unittest.TestCase):
    def test_it_is_the_distance_to_the_nearest_outside_column(self):
        """Exact, against brute force: the plans, and seeded random sets
        with several pieces, holes and negative coordinates, keyed in
        sorted order."""
        sets = list(PLANS.values()) + list(_random_sets(300, 7))
        for cells in sets:
            with self.subTest(cells=sorted(cells)[:4], n=len(cells)):
                got = distance_to_edge(cells)
                self.assertEqual(got, _brute_euclid(cells))
                self.assertEqual(list(got), sorted(cells))

    def test_every_side_has_a_shore(self):
        """A 5x5 square is 1.0 all round its border, the cells on its
        bounding box's edge included -- the padding the lake had to learn
        -- 2.0 one in and 3.0 at the center. A cell whose only outside
        neighbor is a corner away is sqrt(2) from the outside, and a lone
        column is 1.0."""
        got = distance_to_edge(_rect(-12, -12, -8, -8))
        for (x, z), d in got.items():
            ring = min(x + 12, -8 - x, z + 12, -8 - z)
            self.assertEqual(d, (1.0, 2.0, 3.0)[ring], (x, z))
        notched = distance_to_edge(_rect(0, 0, 4, 4) - {(0, 0)})
        self.assertEqual(notched[(1, 1)], math.sqrt(2))
        self.assertEqual(distance_to_edge([(5, -3)]), {(5, -3): 1.0})
        # A hole is a shore: the courtyard's inner rim is 1.0 too.
        court = distance_to_edge(PLANS["courtyard"])
        self.assertEqual(court[(-5, -6)], 1.0)
        self.assertEqual(court[(-5, -7)], 2.0)

    def test_each_piece_is_measured_on_its_own_box(self):
        """Two lakes far apart are two small grids, not the square between
        them: the transform runs once per 4-connected piece, on its
        bounding box padded by one, and the answer is still exact."""
        cells = _rect(0, 0, 2, 2) | _rect(500, 40, 502, 42)
        with mock.patch.object(field, "edt", wraps=field.edt) as spy:
            got = distance_to_edge(cells)
        self.assertEqual([call.args[1:] for call in spy.call_args_list],
                         [(5, 5), (5, 5)])
        self.assertEqual(got, _brute_euclid(cells))
        self.assertEqual(got[(1, 1)], 2.0)
        self.assertEqual(got[(501, 41)], 2.0)

    def test_a_piece_is_the_field_it_would_be_alone(self):
        """Pieces that touch only at a corner, nest (an island in a ring's
        hole) or lie scattered, against brute force -- and each piece's
        field is the one it has measured alone. From a cell's nearest
        outside column a staircase of steps toward the cell runs back to it
        through the set, so that column touches the cell's own piece and
        another piece's column is never nearer: which is why a piece's box
        may hold another piece's columns, marked either way, and still be
        exact. This holds the theorem, not the mask: marking another
        piece's columns outside is the same field."""
        rng = random.Random(41)
        sets = [_rect(0, 0, 3, 3) | _rect(4, 4, 7, 7),
                (_rect(-8, -8, 8, 8) - _rect(-5, -5, 5, 5)) | _rect(-1, -1, 1, 1),
                {(x, z) for x in range(-6, 7) for z in range(-6, 7)
                 if (x + z) % 2 == 0}]
        for _ in range(100):
            ox, oz = rng.randint(-20, 20), rng.randint(-20, 20)
            cells = {(ox + x, oz + z) for x in range(10) for z in range(10)
                     if rng.random() < 0.55}
            sets.append(cells or {(ox, oz)})
        for cells in sets:
            with self.subTest(cells=sorted(cells)[:4], n=len(cells)):
                got = distance_to_edge(cells)
                self.assertEqual(got, _brute_euclid(cells))
                for piece in _pieces(cells):
                    self.assertEqual({cell: got[cell] for cell in sorted(piece)},
                                     distance_to_edge(piece))

    def test_a_shore_profile_is_cliff_free_up_to_a_slope_of_one(self):
        """``round(s * distance)`` steps at most a block between side
        neighbors for any slope up to 1 -- the basin's bound -- and a slope
        just past it finds a two-block cliff."""
        def worst(depths):
            return max((abs(depths[(x, z)] - depths[n])
                        for x, z in depths
                        for n in ((x + 1, z), (x, z + 1)) if n in depths),
                       default=0)

        def outlines(count, seed):
            """Lakes: unions of ragged discs up to 14 across the radius."""
            rng = random.Random(seed)
            for _ in range(count):
                cells = set()
                for _ in range(rng.randint(1, 3)):
                    cx, cz = rng.randint(-30, 30), rng.randint(-30, 30)
                    r = rng.randint(2, 14)
                    cells |= {(cx + dx, cz + dz)
                              for dx in range(-r, r + 1) for dz in range(-r, r + 1)
                              if dx * dx + dz * dz <= r * r + rng.randint(0, r)}
                yield cells

        steepest = 0
        for cells in outlines(200, 11):
            d = distance_to_edge(cells)
            for slope in (0.75, 0.9, 1.0):
                with self.subTest(slope=slope, n=len(cells)):
                    self.assertLessEqual(
                        worst({c: round(slope * v) for c, v in d.items()}), 1)
            steepest = max(steepest, worst({c: round(1.05 * v)
                                            for c, v in d.items()}))
        self.assertEqual(steepest, 2)

    def test_bad_cells_are_refused(self):
        for cells in ([], set(), (), [(1.5, 0)], [(0, 2.0)], [(True, 0)],
                      [(0, 0, 0)], ["ab"], [(0, "1")], [5], 5, None):
            with self.subTest(cells=cells):
                with self.assertRaises(NBTError):
                    distance_to_edge(cells)
        self.assertEqual(distance_to_edge([[0, 0], (0, 0)]), {(0, 0): 1.0})


class ChessboardTests(unittest.TestCase):
    def test_the_distance_is_how_far_a_king_walks_out(self):
        """Exact, against brute force, over the L, T, U, cross and
        courtyard and 200 seeded random 8x8 footprints."""
        rng = random.Random(8)
        footprints = list(PLANS.values())
        for _ in range(200):
            ox, oz = rng.randint(-20, 20), rng.randint(-20, 20)
            cells = {(ox + x, oz + z) for x in range(8) for z in range(8)
                     if rng.random() < 0.7}
            footprints.append(cells or {(ox, oz)})
        footprints += list(_random_sets(100, 9))
        for cells in footprints:
            with self.subTest(cells=sorted(cells)[:4], n=len(cells)):
                got = chessboard(cells)
                self.assertEqual(got, _brute_king(cells))
                self.assertEqual(list(got), sorted(cells))

    def test_a_rectangle_is_measured_to_its_nearest_edge(self):
        """``min(x - x0, x1 - x, z - z0, z1 - z) + 1``: today's hip roof
        ring, plus one."""
        for w in range(1, 13):
            for h in range(1, 13):
                x0, z0 = -6, 3
                x1, z1 = x0 + w - 1, z0 + h - 1
                got = chessboard(_rect(x0, z0, x1, z1))
                self.assertEqual(got, {(x, z): min(x - x0, x1 - x, z - z0, z1 - z) + 1
                                       for x, z in sorted(_rect(x0, z0, x1, z1))},
                                 (w, h))

    def test_the_two_fields_share_one_convention(self):
        """Both measure to the nearest column outside, center to center, 1
        on the edge: a cell with a side neighbor outside is 1 in both, no
        cell is under 1, and since a king's step is at most a diagonal the
        chessboard distance lies between the Euclidean one over sqrt(2) and
        the Euclidean one itself."""
        for cells in _random_sets(150, 12):
            king, euclid = chessboard(cells), distance_to_edge(cells)
            self.assertEqual(list(king), list(euclid))
            for (x, z), k in king.items():
                d = euclid[(x, z)]
                self.assertGreaterEqual(k, 1)
                self.assertLessEqual(k, d + 1e-9)
                self.assertLessEqual(d, k * math.sqrt(2) + 1e-9)
                if any(n not in cells for n in ((x + 1, z), (x - 1, z),
                                                (x, z + 1), (x, z - 1))):
                    self.assertEqual((k, d), (1, 1.0))

    def test_bad_cells_are_refused(self):
        for cells in ([], set(), [(1.5, 0)], [(False, 0)], [(0, 0, 0)],
                      ["ab"], [5], 5, None):
            with self.subTest(cells=cells):
                with self.assertRaises(NBTError):
                    chessboard(cells)
        self.assertEqual(chessboard(iter([(3, 3)])), {(3, 3): 1})


if __name__ == "__main__":
    unittest.main()
