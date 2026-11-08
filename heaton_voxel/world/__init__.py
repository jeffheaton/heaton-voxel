"""Whole-save access: :class:`MinecraftSave`.

Where :mod:`heaton_voxel.nbt` is the file format and :mod:`heaton_voxel.blocks` is one chunk's contents,
this is the world — every region file of every dimension behind one set of
global block coordinates.

    from heaton_voxel import MinecraftSave

    save = MinecraftSave.load("~/Library/.../saves/test_world")
    print(save.player_position())                    # (39.1, 68.0, -122.1)

    x, y, z = save.player_block_position()
    save.fill((x - 4, y, z - 4), (x + 4, y + 8, z + 4), "minecraft:glass")
    save.save()

Coordinates are global block coordinates throughout, negatives included. A box
handed to :meth:`MinecraftSave.fill` may span any number of chunks, sections
and region files, and its corners may be given in any order.

Nothing is written until :meth:`MinecraftSave.save` is called, and that
refuses by default to touch a world Minecraft currently has open.
"""

from __future__ import annotations

from .cli import _main, main
from .dimension import (
    CHUNK,
    DIMENSION_STORAGE_DATA_VERSION,
    END,
    NETHER,
    OVERWORLD,
    REGION_CHUNKS,
    SECTION,
    Dimension,
    ReadOnlySave,
)
from .results import (
    BiomeFillResult,
    ChunkCreateResult,
    ChunkDeleteResult,
    EntityQueryResult,
    FillResult,
    FindResult,
    HeightmapResult,
    HistogramResult,
    LightResetResult,
    ReplaceResult,
    UnfreezeResult,
    WeatherState,
    WorldBorder,
)
from .save import MinecraftSave
from .settings import DAY_TIMES, DIFFICULTIES, GAME_MODES, WEATHER_KINDS

__all__ = [
    "MinecraftSave",
    "Dimension",
    "ReadOnlySave",
    "DIMENSION_STORAGE_DATA_VERSION",
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
    "GAME_MODES",
    "DIFFICULTIES",
    "WEATHER_KINDS",
    "DAY_TIMES",
    "OVERWORLD",
    "NETHER",
    "END",
]
