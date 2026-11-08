"""What the mixins of :class:`~heaton_voxel.world.MinecraftSave` read from
one another, declared once for a type checker.

``save.py`` assembles the save from one mixin per concern -- player,
settings, editing, aftercare, interchange, history -- the way the C# port lays one
class out in partial-class files. A partial class shares its state by being
one class; a mixin reaches attributes and methods that another file
defines, which a type checker cannot see from inside it. Every mixin derives
from :class:`SaveState`, which declares them for the checker alone: at run
time it is an empty class, and it has no twin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..nbt import NBT, TagCompound
    from .dimension import Dimension
    from .history import SaveEdit


class SaveState:
    """The state and the methods of ``MinecraftSave`` its mixins share."""

    if TYPE_CHECKING:
        path: str
        level: NBT | None
        dimensions: dict[str, Dimension]
        layout: str
        read_only: str | None
        _player: NBT | None
        _player_path: str | None
        _settings_files: dict[str, NBT]
        _active_edit: SaveEdit | None

        def _loaded_level(self) -> NBT: ...

        def _level_data(self) -> TagCompound: ...

        def _refuse_read_only(self, doing: str = "write") -> None: ...

        def dimension(self, name: str | None = None) -> Dimension: ...

        def _default_dimension(self) -> str: ...

        def _load_player(self) -> TagCompound: ...
