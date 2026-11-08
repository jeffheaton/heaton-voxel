"""Distance fields over a set of columns: how far each is from the outside.

Two builds measured the same thing in two metrics. The lake bed
(``tools/lake``) is cut ``slope x distance-to-shore`` deep, by the exact
Euclidean distance, because a chamfer approximation put diamond ridges
down the middle of the lake that showed on the floor as terraces. A hip
roof over a plan that is not a rectangle rises with the chessboard
distance, since a king's walk to the eaves is what puts a straight
45-degree valley at a reentrant corner, where a Euclidean distance puts a
bump. So this module holds both, each over a set of ``(x, z)`` columns:

    from heaton_voxel.craft.field import chessboard, distance_to_edge

    lake = {(x, z) for x in range(-4, 5) for z in range(-4, 5)}
    distance_to_edge(lake)[(0, 0)]      # 5.0: the nearest dry column is five away
    chessboard(lake)[(3, 3)]            # 2: a king walks out in two steps

**One convention, 1 on the edge.** Both return a dict giving every cell
of the set its distance to the nearest column *not* in the set, measured
center to center, so a cell with a side neighbor outside is 1 in both
metrics, and nothing is ever 0. They part wherever the nearest outside
column is off the axes: a cell whose one outside neighbor is a corner away
is ``sqrt(2)`` from the outside by :func:`distance_to_edge` and 1 by
:func:`chessboard`. A hole in
the set is outside, so it is a shore too. The dict is keyed in sorted
``(x, z)`` order, whatever order the cells came in.

**The transform and the blur** (:func:`edt`, :func:`blur`) are the lake
build's own (``tools/lake/field.py``), promoted verbatim with their
docstrings and held to that file, loaded by path, by
``tests/test_field.py``. They work on a padded row-major grid, as the lake
did; :func:`distance_to_edge` is the transform over a set of columns, with
the padding the lake had to learn done for every caller.

A leaf: it imports nothing from the judged tier, and from the lower tier
only the ``NBTError`` a malformed cell set is refused with. Nothing here
reads or writes a world.
"""

from __future__ import annotations

import math

from ..nbt import NBTError

__all__ = ["INF", "edt", "blur", "distance_to_edge", "chessboard"]

INF = float("inf")


# --------------------------------------------------------------------------
# Exact Euclidean distance transform (Felzenszwalb & Huttenlocher), which is
# separable: a 1-D lower envelope of parabolas per row, then per column.
# A chamfer approximation would put diamond ridges down the middle of the
# lake, and those ridges would show on the floor as terraces.
# --------------------------------------------------------------------------

def _edt_1d(f):
    n = len(f)
    d = [0.0] * n
    v = [0] * n
    z = [0.0] * (n + 1)
    k = 0
    v[0] = 0
    z[0] = -INF
    z[1] = INF
    for q in range(1, n):
        while True:
            s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2.0 * q - 2.0 * v[k])
            if s <= z[k]:
                k -= 1
            else:
                break
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = INF
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        d[q] = (q - v[k]) * (q - v[k]) + f[v[k]]
    return d


def edt(mask, w, h):
    """Squared distance from each True cell to the nearest False cell.

    Measured *inside the grid*, so the grid must be padded with False
    cells or a lake column on the edge will believe it has no shore on
    that side.
    """
    big = float(w * w + h * h) * 4.0
    grid = [[0.0 if not mask[z * w + x] else big for x in range(w)]
            for z in range(h)]
    for z in range(h):
        grid[z] = _edt_1d(grid[z])
    for x in range(w):
        col = _edt_1d([grid[z][x] for z in range(h)])
        for z in range(h):
            grid[z][x] = col[z]
    return grid


def blur(field, mask, w, h, passes, radius=1):
    """Box blur over the masked cells, with everything outside held at 0.

    Clamping the outside to zero is what keeps the beach break gentle: a
    blur that ignored the shore would leave a step at the waterline, and
    one that skipped out-of-grid cells entirely would deepen the bed
    against the bank.
    """
    cur = field
    for _ in range(passes):
        nxt = list(cur)
        for j in range(h):
            for i in range(w):
                k = j * w + i
                if not mask[k]:
                    continue
                total = 0.0
                n = 0
                for dj in range(-radius, radius + 1):
                    jj = j + dj
                    for di in range(-radius, radius + 1):
                        ii = i + di
                        n += 1
                        if 0 <= jj < h and 0 <= ii < w:
                            kk = jj * w + ii
                            if mask[kk]:
                                total += cur[kk]
                nxt[k] = total / n
        cur = nxt
    return cur


# --------------------------------------------------------------------------
# Over a set of columns
# --------------------------------------------------------------------------

def _columns(cells, what: str) -> set[tuple[int, int]]:
    """``cells`` as a set of ``(x, z)`` int pairs, or an NBTError naming the
    first that is not one; an empty set is refused too, since nothing has
    no edge to measure from."""
    try:
        listed = list(cells)
    except TypeError:
        raise NBTError(f"{what} takes an iterable of (x, z) cells, "
                       f"got {cells!r}") from None
    found: set[tuple[int, int]] = set()
    for cell in listed:
        try:
            x, z = cell
        except (TypeError, ValueError):
            raise NBTError(f"{what}: a cell must be a pair of ints, "
                           f"got {cell!r}") from None
        if (isinstance(cell, str) or isinstance(x, bool) or isinstance(z, bool)
                or not isinstance(x, int) or not isinstance(z, int)):
            raise NBTError(f"{what}: a cell must be a pair of ints, "
                           f"got {cell!r}")
        found.add((x, z))
    if not found:
        raise NBTError(f"{what} needs at least one cell: an empty set has "
                       f"no edge to measure from")
    return found


def distance_to_edge(cells) -> dict[tuple[int, int], float]:
    """The Euclidean distance from each cell of ``cells`` to the nearest
    ``(x, z)`` column not in it, center to center: 1.0 for a cell with a
    side neighbor outside, ``sqrt(2)`` for one whose only outside
    neighbor is a corner away, and never 0. A hole is outside.

    Exact: it is :func:`edt`, the lake's transform, square-rooted. Each
    4-connected piece of ``cells`` is measured on its own bounding box
    padded by a column all round -- ``lakeplan.build_floor``'s lesson,
    that a column on the box's edge would otherwise have no shore on that
    side -- with the box's columns marked as ``cells`` has them. That is
    the whole field and not an approximation of it: from a cell's nearest
    outside column, a step toward the cell along the longer of the two
    offsets is strictly nearer the cell, so a staircase of such steps runs
    back to it through the set, and the nearest outside column always
    touches the cell's own piece. So another piece's column in the box is
    never nearer than the piece's own shore, and marking it outside would
    give the same field: each piece's is the one it has alone. It also
    means two lakes far apart cost what each costs, not the square
    between them.

    The field changes by at most 1 between side neighbors, so a depth cut
    ``round(s * distance)`` at a slope ``s <= 1`` never steps more than a
    block between them: the two depths differ by at most ``s``, and at
    ``s = 1`` neither can sit on a half, since the square root of a whole
    number never does. The result is keyed in sorted ``(x, z)`` order.

    ``cells`` is any iterable of ``(x, z)`` pairs of ints (duplicates are
    one cell). Raises :class:`NBTError` for an empty set and for a cell
    that is not a pair of ints, a bool included.
    """
    inside = _columns(cells, "distance_to_edge")
    distance: dict[tuple[int, int], float] = {}
    placed: set[tuple[int, int]] = set()
    for start in sorted(inside):
        if start in placed:
            continue
        piece = [start]
        placed.add(start)
        for x, z in piece:
            for step in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
                if step in inside and step not in placed:
                    placed.add(step)
                    piece.append(step)
        x0 = min(x for x, _ in piece) - 1
        x1 = max(x for x, _ in piece) + 1
        z0 = min(z for _, z in piece) - 1
        z1 = max(z for _, z in piece) + 1
        w, h = x1 - x0 + 1, z1 - z0 + 1
        mask = [(x0 + i, z0 + j) in inside for j in range(h) for i in range(w)]
        squared = edt(mask, w, h)
        for x, z in piece:
            distance[(x, z)] = math.sqrt(squared[z - z0][x - x0])
    return {cell: distance[cell] for cell in sorted(distance)}


def chessboard(cells) -> dict[tuple[int, int], int]:
    """The chessboard distance from each cell of ``cells`` to the nearest
    ``(x, z)`` column not in it -- how many steps a king takes to walk out
    -- which is 1 for every cell with any of its eight neighbors outside,
    and never 0. A hole is outside.

    Exact. It is a breadth-first walk by king moves through ``cells``,
    started from every cell at 1. On any shortest king walk from a cell to
    its nearest outside column, every column before the last is nearer
    that column than the cell is, so inside the set: the walk stays inside
    until its last step, and the search finds it. On a rectangle it is
    ``min(x - x0, x1 - x, z - z0, z1 - z) + 1``, a hip roof's ring plus
    one. The result is keyed in sorted ``(x, z)`` order.

    ``cells`` is any iterable of ``(x, z)`` pairs of ints (duplicates are
    one cell). Raises :class:`NBTError` for an empty set and for a cell
    that is not a pair of ints, a bool included.
    """
    inside = _columns(cells, "chessboard")
    king = [(dx, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if dx or dz]
    rings: dict[tuple[int, int], int] = {}
    frontier = [(x, z) for x, z in sorted(inside)
                if any((x + dx, z + dz) not in inside for dx, dz in king)]
    for cell in frontier:
        rings[cell] = 1
    ring = 1
    while frontier:
        ring += 1
        reached = []
        for x, z in frontier:
            for dx, dz in king:
                step = (x + dx, z + dz)
                if step in inside and step not in rings:
                    rings[step] = ring
                    reached.append(step)
        frontier = reached
    return {cell: rings[cell] for cell in sorted(rings)}
