"""The save itself: loading, dimension resolution, writing, reporting.

The operational concerns live in sibling mixins (player, editing,
aftercare, interchange), one file each, mirroring how the C# port lays the
same class out in partial-class files (see PORTING.md).
"""

from __future__ import annotations

import os
from typing import Callable, Iterable

from ..nbt import NBT, NBTError, TagCompound
from .aftercare import AftercareOps
from .dimension import (
    _DIMENSION_DIRS,
    _OLD_DIMENSION_DIRS,
    DIMENSION_STORAGE_DATA_VERSION,
    OVERWORLD,
    Dimension,
    ReadOnlySave,
)
from .editing import EditingOps
from .interchange import InterchangeOps
from .player import PlayerOps
from .settings import SettingsOps
from .history import HistoryOps

class MinecraftSave(PlayerOps, EditingOps, AftercareOps, InterchangeOps,
                    SettingsOps, HistoryOps):
    """An entire Minecraft save.

    Opening a save indexes its region files without reading them; each is
    read the first time a chunk inside it is asked for, and the chunks inside
    that stay compressed until their contents are wanted. So the cost of
    touching a world is proportional to what you actually look at.
    """

    def __init__(self, path: str):
        """Bare construction makes an empty shell -- no level.dat, no
        dimensions. Open a world with :meth:`load`; the accessors refuse a
        shell by name rather than failing somewhere deep inside a mixin.
        """
        self.path = os.path.abspath(os.path.expanduser(path))
        self.level: NBT | None = None
        self.dimensions: dict[str, Dimension] = {}
        #: ``"current"``, or ``"old"`` for a save in the layout before
        #: Minecraft 26.1 (:data:`~heaton_voxel.world.dimension.DIMENSION_STORAGE_DATA_VERSION`):
        #: its overworld in ``region/`` beside ``level.dat``, the nether
        #: under ``DIM-1/``, the End under ``DIM1/``, and its settings and
        #: single player in ``level.dat``. Such a save is read where it
        #: stands and every write to it refused (:attr:`read_only`).
        self.layout = "current"
        #: Why every write to this save is refused, or ``None``.
        self.read_only: str | None = None
        self._player: NBT | None = None
        self._player_path: str | None = None
        # The split world-data files (weather, world_clocks, ...) touched so
        # far, keyed by stem; loaded by SettingsOps, written back by save().
        self._settings_files: dict[str, NBT] = {}
        self._active_edit = None

    def _loaded_level(self) -> NBT:
        """The loaded level.dat, or a refusal naming the actual mistake.

        ``MinecraftSave(path)`` called directly -- rather than
        :meth:`load` -- yields a shell holding no data, and the first
        symptom used to be a bare ``AttributeError`` from whichever mixin
        touched ``self.level`` first. Every accessor that needs the level
        comes through here instead, so the mistake fails at the first call
        with the fix in the message.
        """
        if self.level is None:
            raise NBTError(
                "nothing is loaded: this MinecraftSave was constructed "
                "directly, which makes an empty shell. Open a world with "
                "MinecraftSave.load(path)."
            )
        return self.level

    def _level_data(self) -> TagCompound:
        """``level.dat``'s ``Data`` compound, where every setting and the
        world's DataVersion live, or a refusal naming what is missing."""
        root = self._loaded_level().root
        data = root.get("Data") if root is not None else None
        if not isinstance(data, TagCompound):
            raise NBTError("level.dat holds no Data compound")
        return data

    @classmethod
    def load(
        cls,
        path: str | os.PathLike,
        *,
        dimensions: Iterable[str] | None = None,
        eager: bool = False,
    ) -> "MinecraftSave":
        """Open a save.

        Region files are indexed but not read: each one is opened the first
        time something asks for a chunk inside it. Editing a few blocks in a
        large world therefore touches a couple of files rather than all of
        them.

        A save in the layout before Minecraft 26.1 -- no ``dimensions/``
        tree, but ``region/``, ``DIM-1/region`` or ``DIM1/region`` -- opens
        where it stands, read-only (:attr:`layout`, :attr:`read_only`): the
        game moves its files and rewrites its ``level.dat`` the first time
        it opens one, and nothing here can do that for it. One holding
        region directories in both layouts is refused, as is one whose
        ``level.dat`` is newer than the old layout.

        Args:
            path: The save directory, the one containing ``level.dat``.
            dimensions: Which dimensions to use; all present ones by default.
            eager: Read every region file up front instead.
        """
        save = cls(os.fspath(path))
        level_path = os.path.join(save.path, "level.dat")
        if not os.path.exists(level_path):
            raise NBTError(
                f"{save.path!r} does not look like a Minecraft save: no level.dat"
            )
        save.level = NBT.load(level_path)

        def present(table):
            return [name for name, relative in table.items()
                    if os.path.isdir(os.path.join(save.path, *relative.split("/")))]

        current, old = present(_DIMENSION_DIRS), present(_OLD_DIMENSION_DIRS)
        if current and old:
            raise NBTError(
                f"{save.path!r} holds region directories in both layouts, under "
                f"dimensions/ and at the save's root (region/, DIM-1/, DIM1/). The "
                f"game moves the old ones when it opens a world, so a save holding "
                f"both was put together by hand, and which of them is the world "
                f"cannot be told")
        table = _DIMENSION_DIRS
        if old:
            data = save.level.root.get("Data") if save.level.root is not None else None
            stamp = data.get("DataVersion") if isinstance(data, TagCompound) else None
            if stamp is not None and int(stamp) >= DIMENSION_STORAGE_DATA_VERSION:
                raise NBTError(
                    f"{save.path!r} keeps its regions in the layout before 26.1, but "
                    f"its level.dat is DataVersion {int(stamp)}, which keeps them "
                    f"under dimensions/: the two do not belong together")
            save.layout, table = "old", _OLD_DIMENSION_DIRS
            version = "" if stamp is None else f", DataVersion {int(stamp)}"
            save.read_only = (
                f"{save.path!r} is in the save layout before Minecraft 26.1{version}, "
                f"which a current Minecraft moves into the new one the first time "
                f"it opens it")

        wanted = list(dimensions) if dimensions is not None else list(table)
        for name in wanted:
            relative = table.get(name)
            if relative is None:
                known = ", ".join(sorted(table))
                raise NBTError(f"unknown dimension {name!r}; expected one of {known}")
            directory = os.path.join(save.path, *relative.split("/"))
            if not os.path.isdir(directory):
                continue  # dimension never visited
            dimension = Dimension(name, directory, read_only=save.read_only)
            dimension.index()
            if eager:
                dimension.load_all()
            save.dimensions[name] = dimension

        if not save.dimensions:
            raise NBTError(
                f"{save.path!r} has no region directories: none under dimensions/, "
                f"and none of the layout before 26.1 (region/, DIM-1/region, "
                f"DIM1/region) either."
            )
        return save

    def _refuse_read_only(self, doing: str = "write") -> None:
        """Raise :class:`~heaton_voxel.world.ReadOnlySave` if this save is
        in the layout before 26.1, saying it will not ``doing`` it."""
        if self.read_only is not None:
            raise ReadOnlySave(f"{self.read_only}; the library reads it but will "
                               f"not {doing} it")

    def dimension(self, name: str | None = None) -> Dimension:
        """A loaded dimension.

        With no name, prefers the one the player is in, then the overworld,
        then the only one loaded. The player is merely the best guess at
        intent, not a requirement -- a server world has no player data at all,
        and reading its blocks should still work.
        """
        # A shell from a direct MinecraftSave(...) has no dimensions at
        # all; refuse it here by name, before the resolution below turns
        # "nothing is loaded" into a confusing "no overworld".
        self._loaded_level()
        if name is None:
            name = self._default_dimension()
        found = self.dimensions.get(name)
        if found is None:
            known = ", ".join(sorted(self.dimensions)) or "none"
            raise NBTError(f"dimension {name!r} is not loaded (have: {known})")
        return found

    def _default_dimension(self) -> str:
        try:
            where = self.player_dimension()
        except NBTError:
            where = None
        if where is not None and where in self.dimensions:
            return where
        if OVERWORLD in self.dimensions:
            return OVERWORLD
        if len(self.dimensions) == 1:
            return next(iter(self.dimensions))
        raise NBTError(
            "cannot tell which dimension you mean: there is no player data and "
            f"no overworld loaded. Pass dimension= explicitly (have: "
            f"{', '.join(sorted(self.dimensions))})."
        )


    def save(
        self,
        *,
        force: bool = False,
        touch_modified: bool = False,
        force_all: bool = False,
        before_write: Callable[[str], None] | None = None,
    ) -> int:
        """Write back the files that actually changed. Returns how many.

        A region file is a candidate only if it was opened at all, and is
        written only if serializing it produces different bytes from the ones
        it was loaded with -- so editing one chunk rewrites one file, not the
        whole world. The comparison is a hash of the serialized bytes, which
        catches a change however it was made, including reaching straight into
        ``chunk.data``.

        Refuses by default if the world is open in Minecraft; see
        :func:`nbt.world_is_open`. Refuses a save in the layout before 26.1
        always (:attr:`read_only`), whatever it holds.

        ``before_write`` is called with the absolute path of every file the
        save is about to replace, create or remove, before it is touched
        (:meth:`NBT.save <heaton_voxel.nbt.NBT.save>`), and an exception from
        it stops the save there: every file written before it was offered to
        it first, and nothing after it is written.
        """
        self._refuse_read_only("save")
        if self._active_edit is not None:
            raise NBTError("complete or roll back the active edit before saving")
        written = 0
        for dim in self.dimensions.values():
            for region in dim.loaded_regions():
                if not region.from_disk:
                    if not region.chunks:
                        # Made in memory and never given a chunk: writing
                        # it would leave an empty container file behind.
                        continue
                    # create_chunks may build the first region of a
                    # dimension the world has never visited.
                    directory = os.path.dirname(region.path or "")
                    if directory and not os.path.isdir(directory):
                        os.makedirs(directory, exist_ok=True)
                if region.save(
                    force=force,
                    touch_modified=touch_modified,
                    skip_unchanged=not force_all,
                    before_write=before_write,
                ):
                    written += 1
            for region in list(dim.entity_regions.values()):
                if not region.from_disk and not region.chunks:
                    # Created in memory but never given a chunk; writing it
                    # would leave an empty container file behind.
                    continue
                # A region built by entity_region(create=True) may be the
                # first entity ever stored in this dimension, in which case
                # the entities/ directory itself does not exist yet.
                directory = os.path.dirname(region.path or "")
                if directory and not os.path.isdir(directory):
                    os.makedirs(directory, exist_ok=True)
                if region.save(
                    force=force,
                    touch_modified=touch_modified,
                    skip_unchanged=not force_all,
                    before_write=before_write,
                ):
                    written += 1
            for region in list(dim.poi_regions.values()):
                if region.save(
                    force=force,
                    touch_modified=touch_modified,
                    skip_unchanged=not force_all,
                    before_write=before_write,
                ):
                    written += 1
        if self._player is not None and self._player_path is not None:
            if self._player.save(
                self._player_path, force=force, skip_unchanged=not force_all,
                before_write=before_write,
            ):
                written += 1
        # level.dat and the split settings files ride the same rule as
        # everything else: candidates only once loaded, written only when
        # their bytes would differ.
        if self.level is not None:
            if self.level.save(
                os.path.join(self.path, "level.dat"),
                force=force, skip_unchanged=not force_all,
                before_write=before_write,
            ):
                written += 1
        for handle, path in self._settings_files_to_save():
            if handle.save(path, force=force, skip_unchanged=not force_all,
                           before_write=before_write):
                written += 1
        return written


    @property
    def region_count(self) -> int:
        """Region files present in the save, loaded or not."""
        return sum(len(dim.available) for dim in self.dimensions.values())

    @property
    def loaded_region_count(self) -> int:
        return sum(len(dim.regions) for dim in self.dimensions.values())

    @property
    def chunk_count(self) -> int:
        """Chunks in the regions opened so far."""
        return sum(
            len(region.chunks)
            for dim in self.dimensions.values()
            for region in dim.loaded_regions()
        )

    def memory_estimate(self) -> int:
        """Roughly how many bytes of compressed chunk data are held."""
        return sum(
            chunk.compressed_size
            for dim in self.dimensions.values()
            for region in dim.loaded_regions()
            for chunk in region
        )

    def __repr__(self) -> str:
        name = "?"
        root = self.level.root if self.level is not None else None
        data = root.get("Data") if root is not None else None
        if isinstance(data, TagCompound):
            name = str(data.get("LevelName", "?"))
        dims = ", ".join(
            f"{d.name.split(':')[-1]}={len(d.available)}"
            for d in self.dimensions.values()
        )
        return (
            f"<MinecraftSave {name!r} [{dims} region files] "
            f"{self.loaded_region_count} loaded>"
        )
