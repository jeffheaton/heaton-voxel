"""Hold the Litematica reader and writer to a second implementation.

    python -m venv /tmp/lit && /tmp/lit/bin/pip install litemapy
    /tmp/lit/bin/python tools/check_litematic.py

The game cannot be asked about ``.litematic`` files -- the format is the
Litematica mod's -- so ``heaton_voxel.schem.litematic`` was written from the
mod's own source and is held here to litemapy, a reading and writing of the
format that owes this library nothing. Two checks, both ways:

* litemapy reads schematics written here, cell for cell, at every palette
  width up to six bits -- three, five and six bits are where a cell
  straddles two longs, which the mod's packing allows and a chunk's never
  does;
* litemapy writes a region of negative width and length, the way the mod
  stores a selection made from its far corner, and
  ``fixtures/litematic/litemapy.litematic`` keeps it, with the cells
  litemapy set in ``fixtures/litematic/litemapy.json``, for
  ``tests/test_litematic.py`` to read without litemapy installed.

litemapy is a development tool here, never a dependency of the library:
the package stays standard library only. Rerun after changing the format
code, and commit what it writes.
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

import litemapy  # noqa: E402  (a development tool; see the docstring)

from heaton_voxel.schem import Schematic  # noqa: E402
from heaton_voxel.schem.litematic import _bits  # noqa: E402

FIXTURE = os.path.join(REPO, "fixtures", "litematic")
COLORS = ("white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray",
          "light_gray", "cyan", "purple", "blue", "brown", "green", "red", "black")
BLOCKS = ([f"minecraft:{c}_wool" for c in COLORS] + [f"minecraft:{c}_concrete" for c in COLORS]
          + ["minecraft:oak_stairs[facing=east,half=top]", "minecraft:glass"])


def read_back_by_litemapy() -> None:
    rng = random.Random(11)
    with tempfile.TemporaryDirectory() as work:
        for kinds in (1, 3, 5, 7, 12, 20, 33):
            schematic = Schematic(11, 7, 13, 5023, offset=(kinds, -kinds, 2))
            for x in range(11):
                for y in range(7):
                    for z in range(13):
                        schematic.set_block(x, y, z, BLOCKS[rng.randrange(kinds)])
            path = os.path.join(work, f"ours{kinds}.litematic")
            schematic.save(path)
            (name, region), = litemapy.Schematic.load(path).regions.items()
            wrong = [(x, y, z) for x in range(11) for y in range(7) for z in range(13)
                     if region[x, y, z].id != schematic.get_block(x, y, z).name]
            if wrong:
                raise SystemExit(f"litemapy reads {len(wrong)} cells of a {kinds}-kind "
                                 f"schematic differently, first {wrong[0]}")
            print(f"  {kinds + 1:>2} palette entries, {_bits(kinds + 1)} bits: litemapy "
                  f"reads all {11 * 7 * 13} cells as written", file=sys.stderr)


def write_by_litemapy() -> None:
    region = litemapy.Region(5, 1, -2, -4, 3, -3)
    ids = ["minecraft:stone", "minecraft:dirt", "minecraft:gold_block",
           "minecraft:oak_planks", "minecraft:glass"]
    cells = []
    for x in region.range_x():
        for y in region.range_y():
            for z in region.range_z():
                block = ids[(x * 7 + y * 3 + z * 5) % len(ids)]
                region[x, y, z] = litemapy.BlockState(block)
                # litemapy counts from the region's Position, either way
                cells.append([5 + x, 1 + y, -2 + z, block])
    schematic = region.as_schematic(name="litemapy", author="litemapy",
                                    description="a region selected from its far corner")
    os.makedirs(FIXTURE, exist_ok=True)
    schematic.save(os.path.join(FIXTURE, "litemapy.litematic"))
    with open(os.path.join(FIXTURE, "litemapy.json"), "w", encoding="ascii") as handle:
        json.dump({"litemapy": litemapy.__version__ if hasattr(litemapy, "__version__") else None,
                   "position": [5, 1, -2], "size": [-4, 3, -3],
                   "cells": cells}, handle, indent=1)
        handle.write("\n")
    print(f"  wrote {os.path.relpath(FIXTURE, REPO)}/litemapy.litematic and its "
          f"{len(cells)} cells", file=sys.stderr)


def main() -> int:
    read_back_by_litemapy()
    write_by_litemapy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
