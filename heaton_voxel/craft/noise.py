"""Dice keyed on a cell, and the patches they clump into.

A build that rolls its dice from a session's random stream gets a
different floor whenever an earlier call changes, and one that rolls a
fresh die per cell gets the weights right and the picture wrong:
television static, which ``tools/lake/rebed.py`` had to repair on the lake
bed. Both are fixed by keying the roll on the block itself. Everything
here is a pure function of its arguments -- whole-number arithmetic with
no ``hash()``, no ``random`` and no state -- so a cell rolls the same on
every run, in every order, on every platform and every Python.

    from heaton_voxel.craft.noise import cell_hash, patch

    cell_hash(-599, 64, -53, 4211)      # a 32-bit roll for this cell and seed
    patch(-599, 64, -53, 3, 4211)       # the patch of about 3 cells across it falls in

**The roll** (:func:`cell_hash`) is ``tools/lake/noise.py``'s ``_hash``
with a third axis: the coordinates and the seed, each multiplied by its
own odd constant and folded together, then murmur3's 32-bit finalizer. At
``y = 0`` it is the lake's hash bit for bit, and ``tests/test_noise.py``
holds the two equal, so the lake build's own record stays the oracle.

**A patch** (:func:`patch`) is a jittered grid's nearest feature (Worley
F1): space is cut into cubes ``size`` blocks across, each cube gets one
feature point placed in it by its own roll, and a cell belongs to the
cube whose feature is nearest. Rolling once per patch instead of once per
cell clumps the picture and keeps the weights, which thresholding smooth
noise does not: value noise piles up in the middle of its range, so the
ends of a mix vanish.

A leaf: it imports nothing from the judged tier, and from the lower tier
only the ``NBTError`` a bad patch size is refused with.
"""

from __future__ import annotations

from ..nbt import NBTError

__all__ = ["MASK", "MAX_PATCH", "cell_hash", "patch"]

#: Every roll is a 32-bit word.
MASK = 0xFFFFFFFF

#: The widest patch: a feature point is placed by ten bits of its cube's
#: roll per axis, so a cube wider than 1024 cells could not be reached
#: across.
MAX_PATCH = 1024


def cell_hash(x: int, y: int, z: int, seed: int) -> int:
    """A stable 32-bit roll for the cell ``(x, y, z)`` and ``seed``.

    Whole numbers only: negative coordinates and seeds wrap through
    two's complement, and a float is a ``TypeError``. ``cell_hash(x, 0,
    z, seed)`` is ``tools/lake/noise.py``'s ``_hash(x, z, seed)``.
    """
    v = (x * 0x1F1F1F1F) ^ (y * 0x27D4EB2F) ^ (z * 0x2545F491) ^ (seed * 0x9E3779B1)
    v &= MASK
    v ^= v >> 15
    v = (v * 0x85EBCA6B) & MASK
    v ^= v >> 13
    v = (v * 0xC2B2AE35) & MASK
    v ^= v >> 16
    return v


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def patch(x: int, y: int, z: int, size: int, seed: int) -> tuple[int, int, int]:
    """The cube ``(cx, cy, cz)`` whose patch the cell ``(x, y, z)`` is in.

    Space is cut into cubes ``size`` blocks across, by floor division, so
    a cell at -1 is in cube -1 and never in cube 0. Each cube's feature
    point lies inside it, at offsets taken from ten bits each of the
    cube's own :func:`cell_hash`; the cell belongs to the cube, of the
    27 around its own, whose feature is nearest. Ties go to the least
    ``(squared distance, cy, cz, cx)``, so the answer never depends on
    the order anything is visited in. The nearest among those 27 is the
    definition, not an approximation of a truer nearest: it is pure and
    total, and every patch lies within one cube of its cells.

    ``size`` 1 is the cell itself. ``size`` must be a whole number from 1
    to :data:`MAX_PATCH`, or it is an ``NBTError``, as a Mix's ``clump``
    is; the coordinates must be whole numbers (``TypeError``): a float
    would have to be truncated or floored, and choosing which here would
    hide it.
    """
    if not _whole(size) or not 1 <= size <= MAX_PATCH:
        raise NBTError(f"a patch is a whole number of blocks across, 1 to "
                       f"{MAX_PATCH}, not {size!r}")
    for value in (x, y, z):
        if not _whole(value):
            raise TypeError(f"a patch is found for a whole cell, not "
                            f"({x!r}, {y!r}, {z!r})")
    if size == 1:
        return (x, y, z)
    gx, gy, gz = x // size, y // size, z // size
    best = None
    for cy in (gy - 1, gy, gy + 1):
        for cz in (gz - 1, gz, gz + 1):
            for cx in (gx - 1, gx, gx + 1):
                h = cell_hash(cx, cy, cz, seed)
                fx = cx * size + (h & 1023) % size - x
                fy = cy * size + ((h >> 10) & 1023) % size - y
                fz = cz * size + ((h >> 20) & 1023) % size - z
                key = (fx * fx + fy * fy + fz * fz, cy, cz, cx)
                if best is None or key < best:
                    best = key
    return (best[3], best[1], best[2])
