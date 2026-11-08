"""In-memory edit records, including files opened lazily during an edit.

Records retain compressed chunk payloads, not decoded block trees. Completing
an edit captures any newly opened file's original bytes before a save can
replace them. Restoring a record preserves each handle's current disk digest,
so undoing an edit after saving still produces a modified file on the next save.
"""

from __future__ import annotations

import copy
import os

from ..nbt import NBT, NBTError, encode, decode
from ._state import SaveState


def _files(save: SaveState) -> dict[str, NBT]:
    result = {}
    handles = [save.level, save._player, *save._settings_files.values()]
    for dim in save.dimensions.values():
        handles.extend(dim.regions.values())
        handles.extend(dim.entity_regions.values())
        handles.extend(dim.poi_regions.values())
    for handle in handles:
        if handle is not None and handle.path is not None:
            result[os.path.abspath(handle.path)] = handle
    return result


def _clear_accessors(save: SaveState) -> None:
    for dim in save.dimensions.values():
        dim._blocks_cache.clear()


def _snapshot(handle: NBT) -> NBT:
    for chunk in handle.chunks.values():
        chunk.release()
    result = copy.copy(handle)
    result.root = (decode(encode(handle.root))[1]
                   if handle.root is not None else None)
    result.chunks = {key: copy.copy(chunk) for key, chunk in handle.chunks.items()}
    result._timestamps = handle._timestamps.copy()
    result._gap_sectors = handle._gap_sectors.copy()
    result._loaded_sidecars = handle._loaded_sidecars.copy()
    return result


def _signature(handle: NBT) -> tuple:
    if handle.kind != "region":
        return (handle.root_name, handle.compression,
                encode(handle.root, handle.root_name) if handle.root is not None else None)
    return tuple((key, chunk.timestamp, chunk.compression, chunk.payload())
                 for key, chunk in sorted(handle.chunks.items()))


class SaveEdit:
    """A reversible edit of one open save. Call complete before saving.

    Undo/redo change memory only; call save.save() to write the result. Records
    must be used in stack order. Reacquire block accessors after beginning or
    restoring a record. The caller owns its world lock and history budget.
    """

    def __init__(self, save: SaveState):
        self._save = save
        self._complete = False
        _clear_accessors(save)
        self._before = {path: _snapshot(handle) for path, handle in _files(save).items()}
        self._after: dict[str, NBT] = {}
        self._targets: dict[str, NBT] = {}

    @property
    def changed(self) -> bool:
        return bool(self._targets)

    @property
    def paths(self) -> tuple[str, ...]:
        """Changed file paths, in deterministic order."""
        return tuple(sorted(self._targets))

    @property
    def byte_size(self) -> int:
        return sum(sum(chunk.compressed_size for chunk in handle.chunks.values())
                   + (len(encode(handle.root, handle.root_name)) if handle.root is not None else 0)
                   for states in (self._before, self._after) for handle in states.values())

    def complete(self) -> None:
        if self._complete:
            return
        _clear_accessors(self._save)
        for path, handle in sorted(_files(self._save).items()):
            if path not in self._before:
                if os.path.isfile(path):
                    self._before[path] = _snapshot(NBT.load(path))
                elif handle.kind == "region":
                    empty = _snapshot(handle)
                    empty.chunks.clear()
                    self._before[path] = empty
                else:
                    raise NBTError(f"cannot capture original state of missing file '{path}'")
            after = _snapshot(handle)
            if _signature(self._before[path]) != _signature(after):
                self._targets[path] = handle
                self._after[path] = after
        self._before = {path: self._before[path] for path in self._targets}
        self._complete = True
        self._save._active_edit = None

    def _restore(self, states: dict[str, NBT]) -> None:
        if not self._complete:
            raise NBTError("complete the edit before restoring it")
        if self._save._active_edit is not None:
            raise NBTError("finish the active edit before restoring history")
        _clear_accessors(self._save)
        for path, state in sorted(states.items()):
            target = self._targets[path]
            baseline = target._load_digest
            sidecars = target._loaded_sidecars.copy()
            restored = _snapshot(state)
            target.__dict__.update(restored.__dict__)
            target._load_digest = baseline
            target._loaded_sidecars |= sidecars

    def undo(self) -> None:
        self._restore(self._before)

    def redo(self) -> None:
        self._restore(self._after)

    def rollback(self) -> None:
        self.complete()
        self.undo()


class HistoryOps(SaveState):
    def begin_edit(self) -> SaveEdit:
        """Begin a reversible in-memory edit; complete or roll it back.

        Nested edits and saving an incomplete edit are refused. Nothing is
        written by this method or the record it returns.
        """
        if self._active_edit is not None:
            raise NBTError("finish the active edit before beginning another")
        record = SaveEdit(self)
        self._active_edit = record
        return record
