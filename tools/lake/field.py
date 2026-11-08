"""The two field operations the bathymetry is built out of.

Kept apart from :mod:`lakeplan` because neither knows anything about
lakes: one measures how far every cell is from the outside, the other
smooths a field while holding the outside at zero. Together they are
the whole reason the new bed provably has no cliff in it.

(This file was called ``profile.py`` until it was noticed that the name
shadows a standard-library module for anything that imports it.)
"""
from __future__ import annotations

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
