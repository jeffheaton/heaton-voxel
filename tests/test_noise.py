"""Tests for heaton_voxel.craft.noise: dice keyed on a cell, and patches.

The roll was promoted out of the lake build (``tools/lake/noise.py``),
which stays as that build's record; the lake's own module, loaded by path,
is the oracle the promoted hash is held to bit for bit at ``y = 0``. Golden
values computed when it was promoted pin it besides, so the two copies
cannot drift together, and so a roll stays the same across runs, Pythons
and platforms -- the whole point of keying a die on the cell.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import random
import subprocess
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from heaton_voxel.craft import noise  # noqa: E402
from heaton_voxel.craft.noise import MAX_PATCH, cell_hash, patch  # noqa: E402
from heaton_voxel.nbt import NBTError  # noqa: E402
from tests.test_blocks import SequenceAssertions  # noqa: E402


def _lake():
    """``tools/lake/noise.py``, loaded by path under a name of its own (the
    lake scripts import it as a bare ``noise`` from their own directory)."""
    spec = importlib.util.spec_from_file_location(
        "_noise_lake_noise", os.path.join(_ROOT, "tools", "lake", "noise.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LAKE = _lake()

#: Seeds the lake used (4211), small ones, a negative one, and one past 32
#: bits, which must wrap the way the lake's does.
SEEDS = (0, 1, 4211, -7, 2 ** 40 + 3)


class CellHashTests(unittest.TestCase):

    def test_the_lake_hash_is_promoted_exactly(self):
        for seed in SEEDS:
            for x in range(-40, 41):
                for z in range(-40, 41):
                    if cell_hash(x, 0, z, seed) != LAKE._hash(x, z, seed):
                        self.fail(f"cell_hash({x}, 0, {z}, {seed}) is "
                                  f"{cell_hash(x, 0, z, seed)}, the lake's "
                                  f"_hash is {LAKE._hash(x, z, seed)}")

    def test_the_hash_is_pinned(self):
        # Computed when the hash was promoted: the lake's values, and the
        # promoted hash off the y = 0 plane, where the lake has no oracle.
        golden = {
            (-599, 0, -53, 4211): 4291811685,
            (17, 0, -3, 1301): 2074092993,
            (3, 64, -7, 0): 2017057157,
            (-1000, -64, 999, 123456789): 2798233802,
        }
        for args, value in golden.items():
            self.assertEqual(cell_hash(*args), value, args)

    def test_every_axis_and_the_seed_move_the_roll(self):
        rolls = {cell_hash(*args) for args in (
            (5, 64, 9, 3), (6, 64, 9, 3), (5, 65, 9, 3), (5, 64, 10, 3),
            (5, 64, 9, 4))}
        self.assertEqual(len(rolls), 5)
        for args in ((5, 64, 9, 3), (-5, -64, -9, -3), (2 ** 33, 0, 0, 0)):
            self.assertTrue(0 <= cell_hash(*args) <= noise.MASK, args)

    def test_a_float_is_refused(self):
        with self.assertRaises(TypeError):
            cell_hash(1.5, 64, 0, 0)

    def test_a_roll_does_not_depend_on_the_run(self):
        """A fresh interpreter with another string-hash seed and another
        random seed rolls the same: nothing here reads ``hash()`` or
        ``random``."""
        code = ("import random; random.seed(99); "
                "from heaton_voxel.craft.noise import cell_hash, patch; "
                "print(cell_hash(3, 64, -7, 0), patch(10, 64, -3, 3, 0))")
        env = dict(os.environ, PYTHONHASHSEED="12345")
        done = subprocess.run([sys.executable, "-c", code], cwd=_ROOT,
                              env=env, capture_output=True, text=True,
                              check=True)
        random.seed(7)
        self.assertEqual(done.stdout.split("\n")[0],
                         f"{cell_hash(3, 64, -7, 0)} {patch(10, 64, -3, 3, 0)}")

    def test_the_module_is_a_leaf_with_no_dice_of_its_own(self):
        """noise imports nothing but the lower tier's ``NBTError`` -- not
        the judged tier, not ``random`` -- and calls no ``hash()``, whose
        strings change every run."""
        path = os.path.join(_ROOT, "heaton_voxel", "craft", "noise.py")
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imports = [node for node in ast.walk(tree)
                   if isinstance(node, (ast.Import, ast.ImportFrom))
                   and not (isinstance(node, ast.ImportFrom)
                            and node.module == "__future__")]
        self.assertEqual([ast.unparse(node) for node in imports],
                         ["from ..nbt import NBTError"])
        calls = [node.lineno for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "hash"]
        self.assertEqual(calls, [])


class PatchTests(SequenceAssertions):

    def test_the_patches_are_pinned(self):
        golden = {
            (10, 64, -3, 3, 0): (3, 21, -1),
            (-1, -1, -1, 4, 77): (-1, 0, 0),
            (100, 70, -250, 5, 4211): (19, 14, -50),
            (-599, 63, -53, 2, 4211): (-299, 31, -27),
        }
        for args, cube in golden.items():
            self.assertEqual(patch(*args), cube, args)

    def test_a_negative_cell_is_floored_into_its_cube(self):
        """Cells where flooring and truncating toward zero find different
        patches -- on x, on y and on z -- pinned when the patch was
        written. A truncating division centers the 27 cubes searched one
        cube toward zero, and the nearest feature it finds is then
        another patch's."""
        golden = {
            (-13, 63, -5, 3, 9): (-5, 21, -3),    # z: truncating gives -2
            (-9, -1, -9, 5, 9): (-3, -1, -2),     # x: truncating gives -2
            (-9, -1, 1, 5, 9): (-3, 0, 0),        # x and y: (-2, -1, 0)
        }
        for args, cube in golden.items():
            self.assertEqual(patch(*args), cube, args)

    def test_size_one_is_the_cell_itself(self):
        for cell in ((0, 0, 0), (-1, 64, 5), (-599, -64, -53)):
            self.assertEqual(patch(*cell, 1, 4211), cell)

    def test_a_patch_is_within_one_cube_and_order_free(self):
        # Negative cells included: a cube is found by flooring, so -1 is in
        # cube -1, and a truncating division would put it in cube 0.
        cells = [(x, y, z) for x in range(-13, 14) for y in (-1, 0, 63)
                 for z in range(-13, 14)]
        for size in (2, 3, 5):
            forward = {cell: patch(*cell, size, 9) for cell in cells}
            shuffled = list(cells)
            random.Random(size).shuffle(shuffled)
            again = {cell: patch(*cell, size, 9) for cell in shuffled}
            self.assertSequencesEqual([again[cell] for cell in cells],
                                      [forward[cell] for cell in cells],
                                      f"size {size}")
            for (x, y, z), cube in forward.items():
                own = (x // size, y // size, z // size)
                self.assertTrue(all(abs(c - o) <= 1 for c, o in zip(cube, own)),
                                f"{(x, y, z)} at size {size}: {cube}")

    def test_a_patch_is_about_size_cubed(self):
        size, span = 3, 30
        counts: dict[tuple[int, int, int], int] = {}
        for x in range(span):
            for y in range(span):
                for z in range(span):
                    cube = patch(x, y, z, size, 11)
                    counts[cube] = counts.get(cube, 0) + 1
        # Only the cubes whose whole neighborhood is inside the box are
        # counted whole.
        inner = [n for cube, n in counts.items()
                 if all(2 <= c <= span // size - 3 for c in cube)]
        mean = sum(inner) / len(inner)
        self.assertAlmostEqual(mean, size ** 3, delta=0.15 * size ** 3)

    def test_a_bad_size_or_coordinate_is_refused(self):
        # A size is refused the way a Mix's clump is, with the repo's own
        # refusal; a coordinate that is not whole is a TypeError.
        for size in (0, -3, MAX_PATCH + 1, 2.0, True, "3", None):
            with self.assertRaises(NBTError, msg=size):
                patch(0, 0, 0, size, 0)
        for cell in ((0.5, 0, 0), (0, 64.0, 0), (0, 0, None)):
            with self.assertRaises(TypeError, msg=cell):
                patch(*cell, 3, 0)
        self.assertEqual(len(patch(0, 0, 0, MAX_PATCH, 0)), 3)


if __name__ == "__main__":
    unittest.main()
