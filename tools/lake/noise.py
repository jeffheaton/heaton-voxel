"""Deterministic value noise and per-cell dice.

Both are keyed on the block coordinate and a fixed seed, so nothing here
depends on the order cells are visited. That matters twice over: a sweep
that writes must have a total order or the section palette -- and so the
chunk's bytes -- comes out different every run, and a build you cannot
reproduce is a build you cannot check.
"""
from __future__ import annotations

import math

MASK = 0xFFFFFFFF


def _hash(x: int, z: int, seed: int) -> int:
    v = (x * 0x1F1F1F1F) ^ (z * 0x2545F491) ^ (seed * 0x9E3779B1)
    v &= MASK
    v ^= v >> 15
    v = (v * 0x85EBCA6B) & MASK
    v ^= v >> 13
    v = (v * 0xC2B2AE35) & MASK
    v ^= v >> 16
    return v


def unit(x: int, z: int, seed: int) -> float:
    """A stable value in [0, 1) for this cell and seed."""
    return _hash(x, z, seed) / 4294967296.0


def _smooth(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def value_noise(x: float, z: float, scale: float, seed: int) -> float:
    """Smooth value noise in [-1, 1], one feature about ``scale`` blocks."""
    fx, fz = x / scale, z / scale
    ix, iz = math.floor(fx), math.floor(fz)
    tx, tz = _smooth(fx - ix), _smooth(fz - iz)
    c00 = unit(ix, iz, seed)
    c10 = unit(ix + 1, iz, seed)
    c01 = unit(ix, iz + 1, seed)
    c11 = unit(ix + 1, iz + 1, seed)
    a = c00 + (c10 - c00) * tx
    b = c01 + (c11 - c01) * tx
    return (a + (b - a) * tz) * 2.0 - 1.0


def fbm(x: float, z: float, scale: float, seed: int, octaves: int = 3) -> float:
    """Value noise at several scales, still in [-1, 1]."""
    total = 0.0
    amp = 1.0
    norm = 0.0
    for o in range(octaves):
        total += amp * value_noise(x, z, scale / (2 ** o), seed + o * 977)
        norm += amp
        amp *= 0.5
    return total / norm
