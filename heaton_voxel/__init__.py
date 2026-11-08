"""Heaton Voxel — a from-scratch Minecraft (Java Edition) save toolkit.

Three layers, each built on the one below:

* :mod:`heaton_voxel.nbt` — the NBT and Anvil region **file formats**,
  and beside it :mod:`heaton_voxel.snbt`, NBT's **text form** as the game's
  commands read and write it.
* :mod:`heaton_voxel.blocks` — one chunk's **contents**, paletted and
  bit-packed.
* :mod:`heaton_voxel.world` — the whole **save**: global coordinates, box fill.

Beside the layers, :mod:`heaton_voxel.schem` moves boxes of world through
Sponge ``.schem`` files — the format WorldEdit trades builds in —
:mod:`heaton_voxel.connect` settles neighbor-derived states
(``save.connect_blocks``: fence/wall/pane connections, stair corners) the
way a vanilla block update would, and :mod:`heaton_voxel.craft.render` draws
**pictures** of any of it — top-down area maps and vertical cutaways.
``schem`` and ``connect`` are re-exported here like everything else;
``render`` is imported explicitly (``from heaton_voxel.craft.render import
render_area``) because its block colors are game knowledge that drifts,
not format knowledge.

The names each layer exports are re-exported here, so most programs need only::

    from heaton_voxel import MinecraftSave

    save = MinecraftSave.load("~/Library/.../saves/my_world")
    x, y, z = save.player_block_position()
    save.fill((x - 4, y, z - 4), (x + 4, y + 8, z + 4), "minecraft:glass")
    save.save()

Pure standard library — the binary formats are parsed directly, with no
Minecraft-specific dependencies.
"""

from __future__ import annotations

from .blocks import (
    HEIGHTMAP_NAMES,
    BlockState,
    Blocks,
    PalettedContainer,
    ReadOnlyChunk,
    Section,
    chunk_finished,
    heightmap_mask,
    bits_for_palette,
    heightmap_bits,
    pack_indices,
    unpack_indices,
    unpack_nibbles,
)
from .nbt import (
    NBT,
    Chunk,
    NBTError,
    Tag,
    TagByte,
    TagByteArray,
    TagCompound,
    TagDouble,
    TagFloat,
    TagInt,
    TagIntArray,
    TagList,
    TagLong,
    TagLongArray,
    TagShort,
    TagString,
    dumps,
    world_is_open,
)
from .snbt import parse_snbt, to_snbt
from .connect import ConnectResult
from .schem import (
    Schematic,
    SchemExportResult,
    SchemImportResult,
    directional_leftovers,
    properties_mirrored,
    properties_rotated_cw,
)
from .world import (
    END,
    NETHER,
    OVERWORLD,
    BiomeFillResult,
    ChunkCreateResult,
    ChunkDeleteResult,
    Dimension,
    EntityQueryResult,
    FillResult,
    FindResult,
    HeightmapResult,
    HistogramResult,
    LightResetResult,
    MinecraftSave,
    ReplaceResult,
    UnfreezeResult,
    WeatherState,
    WorldBorder,
)

__version__ = "0.1.0"

__all__ = [
    # nbt — file formats
    "NBT",
    "Chunk",
    "NBTError",
    "Tag",
    "TagByte",
    "TagShort",
    "TagInt",
    "TagLong",
    "TagFloat",
    "TagDouble",
    "TagByteArray",
    "TagString",
    "TagList",
    "TagCompound",
    "TagIntArray",
    "TagLongArray",
    "dumps",
    "world_is_open",
    # snbt — the text form
    "parse_snbt",
    "to_snbt",
    # blocks — one chunk
    "BlockState",
    "Blocks",
    "ReadOnlyChunk",
    "Section",
    "PalettedContainer",
    "unpack_indices",
    "pack_indices",
    "unpack_nibbles",
    "bits_for_palette",
    "heightmap_bits",
    "heightmap_mask",
    "chunk_finished",
    "HEIGHTMAP_NAMES",
    # world — whole saves
    "MinecraftSave",
    "Dimension",
    "FillResult",
    "ReplaceResult",
    "BiomeFillResult",
    "UnfreezeResult",
    "ChunkDeleteResult",
    "ChunkCreateResult",
    "LightResetResult",
    "HistogramResult",
    "FindResult",
    "EntityQueryResult",
    "HeightmapResult",
    "WeatherState",
    "WorldBorder",
    "OVERWORLD",
    "NETHER",
    "END",
    # schem — Sponge schematic interchange
    "Schematic",
    "SchemExportResult",
    "SchemImportResult",
    "properties_rotated_cw",
    "properties_mirrored",
    "directional_leftovers",
    # connect — neighbor-derived states
    "ConnectResult",
    "__version__",
]
