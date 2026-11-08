"""Sponge schematics (``.schem``): the interchange format, and moving boxes
of world through it.

A ``.schem`` file is what WorldEdit (``//copy`` + ``//schem save``), Axiom and
friends trade builds in: a gzip-compressed NBT tree holding a paletted,
varint-packed box of block states, the block entities inside it (chest
contents, sign text), and optionally entities and biomes. Two container
versions are current -- **2** (WorldEdit 7.0-7.2) and **3** (the current
Sponge spec, WorldEdit 7.3+) -- and this module reads both and writes either.
The pre-1.13 ``.schematic`` format (numeric block ids) is a different thing
entirely and is refused with a clear error rather than misread.

    from heaton_voxel import MinecraftSave, Schematic

    save = MinecraftSave.load("~/Library/.../saves/my_world")
    save.export_schem((10, 60, 10), (40, 90, 40), "keep.schem", entities=True)

    result = save.import_schem("keep.schem", (200, 60, -80), anchor="min")
    print(result)          # blocks pasted, entities added, chunks touched
    save.save()            # nothing reaches disk before this

Coordinates inside a schematic are relative to its **minimum corner**;
``Offset`` records where that corner sat relative to the author's copy
origin, so a paste can land the build exactly where its author stood. Both
conventions are honored here: ``anchor="origin"`` (the default, what
``//paste`` does) and ``anchor="min"`` (put the minimum corner at the given
coordinates, ignore the stored offset).

The file format layer (:class:`Schematic`) only needs :mod:`heaton_voxel.nbt`
and :mod:`heaton_voxel.blocks`; the copy/paste entry points take a
:class:`~heaton_voxel.world.MinecraftSave` and are also exposed as methods on
it. Same rules as everywhere else in this toolkit: current Minecraft only
(DataVersion >= 2825), refuse rather than guess, and nothing touches the
world on disk until :meth:`MinecraftSave.save`.
"""

from __future__ import annotations

from .cli import _main
from .copy import export_schem, import_schem
from .results import SchemExportResult, SchemImportResult
from .schematic import Schematic
from .support import AIR_BLOCKS
from .transforms import (
    directional_leftovers,
    properties_mirrored,
    properties_rotated_cw,
)

# Re-exported for the conformance generator and transform tooling: the
# validated game-knowledge vocabulary. Regenerate the corpus rather than
# editing these; ports consume them as data via tables.json.
from .support import _fresh_uuid, _write_varints
from .transforms import (
    _AXIS_CW,
    _DIR_CW,
    _DIR_MIRROR,
    _DIRECTION_WORDS,
    _FACE_MIRROR_Y,
    _FACING2_CW,
    _FACING2_MIRROR,
    _FACING3_CW,
    _FACING3_MIRROR,
    _HALF_MIRROR_Y,
    _LEFT_RIGHT,
    _RAIL_CW,
    _RAIL_MIRROR,
    _STAIR_MIRROR,
    _TOP_BOTTOM,
    _TRANSFORM_KEYS,
    _map_state,
    _properties_mirrored,
    _properties_rotated_cw,
    _wrap_degrees,
)

__all__ = [
    "Schematic",
    "SchemExportResult",
    "SchemImportResult",
    "export_schem",
    "import_schem",
    "properties_rotated_cw",
    "properties_mirrored",
    "directional_leftovers",
]
