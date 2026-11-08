"""One dimension's files: terrain regions, entity storage, points of
interest, addressed by global coordinates and loaded on demand."""

from __future__ import annotations

import os
import re
from typing import Iterator

from ..blocks import Blocks, ReadOnlyChunk, empty_chunk
from ..nbt import (
    NBT,
    TAG_COMPOUND,
    Chunk,
    NBTError,
    TagCompound,
    TagInt,
    TagIntArray,
    TagList,
)

__all__ = ["Dimension", "OVERWORLD", "NETHER", "END", "ReadOnlySave"]

OVERWORLD = "minecraft:overworld"
NETHER = "minecraft:the_nether"
END = "minecraft:the_end"

#: Each dimension's own directory. Its region, entity and poi directories
#: hang off this, and so does its ``data/minecraft/`` -- which is where the
#: game keeps the settings that belong to one dimension rather than to the
#: world, the border among them.
_DIMENSION_ROOTS = {
    OVERWORLD: "dimensions/minecraft/overworld",
    NETHER: "dimensions/minecraft/the_nether",
    END: "dimensions/minecraft/the_end",
}

_DIMENSION_DIRS = {
    name: f"{root}/region" for name, root in _DIMENSION_ROOTS.items()
}

#: Where each dimension kept its files before the game's
#: ``DimensionStorageFileFix`` moved them under ``dimensions/`` at
#: :data:`DIMENSION_STORAGE_DATA_VERSION`: the overworld's at the save's
#: root, the nether's under ``DIM-1``, the End's under ``DIM1``.
_OLD_DIMENSION_ROOTS = {OVERWORLD: "", NETHER: "DIM-1", END: "DIM1"}

_OLD_DIMENSION_DIRS = {
    name: f"{root}/region" if root else "region"
    for name, root in _OLD_DIMENSION_ROOTS.items()
}

#: The first DataVersion whose saves keep each dimension under
#: ``dimensions/minecraft/<dim>/`` (26.1's ``DimensionStorageFileFix``,
#: beside ``PlayerStorageFileFix`` and ``LevelDatToSavedDataFileFix``).
DIMENSION_STORAGE_DATA_VERSION = 4772


class ReadOnlySave(ReadOnlyChunk):
    """A write to a save this library reads but never writes: one in the
    layout before Minecraft 26.1, which the game moves into the current one
    the first time it opens it. A :class:`ReadOnlyChunk`, so every writer
    that refuses or reports a chunk it will not write does the same with
    every chunk of such a save."""

_REGION_NAME = re.compile(r"^r\.(-?\d+)\.(-?\d+)\.mca$", re.IGNORECASE)

#: The sections a vanilla dimension's chunks span, ``(lowest, highest)``,
#: for a dimension that holds no finished chunk to read its height off.
_VANILLA_SECTIONS = {
    OVERWORLD: (-4, 19),
    NETHER: (0, 15),
    END: (0, 15),
}

SECTION = 16
CHUNK = 16
REGION_CHUNKS = 32


class Dimension:
    """One dimension's region files, addressed by global block coordinates.

    Region files are opened on demand. Indexing a dimension only reads the
    *names* in its region directory, so opening a world costs nothing much
    regardless of its size; a save with a thousand region files does not read
    a thousand files because you asked about one block.
    """

    def __init__(self, name: str, region_dir: str, *, read_only: str | None = None):
        self.name = name
        self.region_dir = region_dir
        #: Why this dimension refuses every write -- the save it belongs to
        #: is in the layout before 26.1 -- or ``None``; see
        #: :meth:`refuse_read_only`.
        self.read_only = read_only
        # Entities live in a sibling directory since 1.17: region/ holds the
        # terrain, entities/ holds one Anvil file per region of mob data.
        # poi/ is the third sibling: cached points of interest (beds,
        # workstations, portals) the game derives from the blocks.
        self.entities_dir = os.path.join(os.path.dirname(region_dir), "entities")
        self.poi_dir = os.path.join(os.path.dirname(region_dir), "poi")
        self.available: dict[tuple[int, int], str] = {}
        self.regions: dict[tuple[int, int], NBT] = {}
        self.entity_available: dict[tuple[int, int], str] = {}
        self.entity_regions: dict[tuple[int, int], NBT] = {}
        self.poi_available: dict[tuple[int, int], str] = {}
        self.poi_regions: dict[tuple[int, int], NBT] = {}
        self._blocks_cache: dict[tuple[int, int], Blocks] = {}

    def index(self) -> int:
        """Note which region files exist, without reading any of them."""
        if not os.path.isdir(self.region_dir):
            return 0
        for name in sorted(os.listdir(self.region_dir)):
            match = _REGION_NAME.match(name)
            if match:
                key = (int(match.group(1)), int(match.group(2)))
                self.available[key] = os.path.join(self.region_dir, name)
        if os.path.isdir(self.entities_dir):
            for name in sorted(os.listdir(self.entities_dir)):
                match = _REGION_NAME.match(name)
                if match:
                    key = (int(match.group(1)), int(match.group(2)))
                    self.entity_available[key] = os.path.join(self.entities_dir, name)
        if os.path.isdir(self.poi_dir):
            for name in sorted(os.listdir(self.poi_dir)):
                match = _REGION_NAME.match(name)
                if match:
                    key = (int(match.group(1)), int(match.group(2)))
                    self.poi_available[key] = os.path.join(self.poi_dir, name)
        return len(self.available)

    def load_all(self) -> int:
        """Open every region file. Returns how many are now loaded."""
        for region_x, region_z in sorted(self.available):
            self.region(region_x, region_z)
        return len(self.regions)

    def refuse_read_only(self, doing: str = "write") -> None:
        """Raise :class:`ReadOnlySave` if this dimension's save is in the
        old layout, saying it will not ``doing`` it; nothing otherwise.
        Every write path asks before it changes anything: :meth:`writable_blocks`,
        and anything that makes a file or a chunk, or drops one."""
        if self.read_only is not None:
            raise ReadOnlySave(f"{self.read_only}; the library reads it but will "
                               f"not {doing} it")

    # -- lookup ------------------------------------------------------------

    def region(
        self, region_x: int, region_z: int, *, create: bool = False
    ) -> NBT | None:
        """The region file covering these region coordinates, loading it now
        if it has not been read yet. ``None`` if the world has no such file;
        ``create=True`` builds an empty one in memory instead, which reaches
        disk on the next :meth:`MinecraftSave.save` once it holds a chunk."""
        key = (region_x, region_z)
        loaded = self.regions.get(key)
        if loaded is not None:
            return loaded
        path = self.available.get(key)
        if path is None:
            if not create:
                return None
            self.refuse_read_only("make a region file in")
            region = NBT()
            region.kind = "region"
            region.region_x, region.region_z = region_x, region_z
            region.path = os.path.join(self.region_dir, f"r.{region_x}.{region_z}.mca")
            self.available[key] = region.path
            self.regions[key] = region
            return region
        self.regions[key] = NBT.load(path)
        return self.regions[key]

    def chunk(self, chunk_x: int, chunk_z: int) -> Chunk | None:
        """The chunk at these chunk coordinates, or ``None`` if not generated.

        A negative coordinate belongs to the region below it, so the shifts
        here must floor rather than truncate -- which Python's ``>>`` does.
        """
        region = self.region(chunk_x >> 5, chunk_z >> 5)
        if region is None:
            return None
        return region.chunks.get((chunk_x & 31, chunk_z & 31))

    def blocks(self, chunk_x: int, chunk_z: int) -> Blocks | None:
        """Block accessor for a chunk, cached so repeated edits stay cheap.

        Returns ``None`` only when the chunk has never been generated. A chunk
        that exists but cannot be read -- one written by a version older than
        this toolkit supports, or one whose NBT is damaged -- raises instead,
        so callers can tell "nothing here" from "something here I cannot see".
        A chunk Minecraft 1.13 to 1.17 saved is read, and refuses every write; a
        writer asks :meth:`writable_blocks` instead, which refuses it first.
        """
        key = (chunk_x, chunk_z)
        cached = self._blocks_cache.get(key)
        if cached is not None:
            return cached
        chunk = self.chunk(chunk_x, chunk_z)
        if chunk is None:
            return None
        if chunk.data is None:
            raise NBTError(
                f"chunk ({chunk_x}, {chunk_z}) exists but could not be decoded: "
                f"{chunk.parse_error}"
            )
        accessor = Blocks(chunk)  # raises for a chunk older than 1.13
        self._blocks_cache[key] = accessor
        return accessor

    def writable_blocks(self, chunk_x: int, chunk_z: int) -> Blocks | None:
        """:meth:`blocks`, for a caller about to write: a chunk Minecraft
        1.13 to 1.17 saved, which reads like any other, raises
        :class:`~heaton_voxel.blocks.ReadOnlyChunk` here instead, and so
        does every chunk of a save in the layout before 26.1
        (:class:`ReadOnlySave`). It is an
        :class:`~heaton_voxel.nbt.NBTError`, so every writer that skips and
        reports, or refuses, a chunk it cannot read does the same with one
        it will not write -- before it writes anything there."""
        self.refuse_read_only()
        accessor = self.blocks(chunk_x, chunk_z)
        if accessor is not None:
            accessor.refuse_legacy()
        return accessor

    def chunk_status(self, chunk_x: int, chunk_z: int) -> str | None:
        """The chunk's generation stage, or ``None`` if never generated.

        ``"minecraft:full"`` is finished terrain; anything else is a chunk
        the generator set aside part-way -- present, holding blocks, but
        without heightmaps or a surface. A chunk that exists but cannot be
        read raises, as everywhere else, so "no chunk" and "a chunk I cannot
        see" stay distinguishable.
        """
        accessor = self.blocks(chunk_x, chunk_z)  # raises if unreadable
        return None if accessor is None else accessor.status

    # -- building --------------------------------------------------------

    def section_range(self) -> tuple[int, int]:
        """The sections this dimension's chunks span, ``(lowest, highest)``.

        Read off the first finished chunk the dimension holds, opened regions
        first, since a data pack can give a dimension any height; a vanilla
        dimension that holds none takes the vanilla height. Refuses where
        there is neither."""
        def regions():
            yield from list(self.regions.values())
            for key in sorted(self.available):
                if key not in self.regions:
                    yield self.region(*key)

        for region in regions():
            for key in sorted(region.chunks):
                chunk = region.chunks[key]
                if chunk.data is None:
                    continue
                try:
                    accessor = Blocks(chunk)
                except NBTError:
                    continue
                if accessor.legacy or not accessor.finished:
                    continue
                return accessor.min_y >> 4, accessor.max_y >> 4
        known = _VANILLA_SECTIONS.get(self.name)
        if known is None:
            raise NBTError(
                f"{self.name} holds no finished chunk to read its height off, "
                f"and has no vanilla height to fall back on")
        return known

    def create_chunk(
        self,
        chunk_x: int,
        chunk_z: int,
        *,
        data_version: int,
        biome: str,
        sections: tuple[int, int] | None = None,
    ) -> Blocks:
        """A finished chunk of air under ``biome`` where the dimension has no
        chunk at all, ready to build on; its block accessor.

        :func:`~heaton_voxel.blocks.empty_chunk` builds it, sections
        ``sections`` or :meth:`section_range`'s, stamped ``data_version``,
        and the region file it lands in is made in memory if the world has
        none. Refuses a chunk the dimension already holds, finished or not:
        building over one would discard what is there, and
        :meth:`MinecraftSave.delete_chunks` is the way to clear it first.
        """
        self.refuse_read_only("build a chunk in")
        if self.chunk(chunk_x, chunk_z) is not None:
            raise NBTError(
                f"chunk ({chunk_x}, {chunk_z}) already exists; only a chunk the "
                f"world has no record of is built, so delete it first to build "
                f"there afresh")
        low, high = sections if sections is not None else self.section_range()
        data = empty_chunk(chunk_x, chunk_z, data_version=data_version,
                           min_section=low, max_section=high, biome=biome)
        region = self.region(chunk_x >> 5, chunk_z >> 5, create=True)
        if region is None:  # never: create=True makes one or refuses
            raise NBTError(f"no region file could be made for chunk ({chunk_x}, {chunk_z})")
        local = (chunk_x & 31, chunk_z & 31)
        chunk = Chunk(*local, data=data)
        region.chunks[local] = chunk
        accessor = Blocks(chunk)
        self._blocks_cache[(chunk_x, chunk_z)] = accessor
        return accessor

    # -- entities ----------------------------------------------------------

    def entity_region(
        self, region_x: int, region_z: int, *, create: bool = False
    ) -> NBT | None:
        """The entity storage file covering these region coordinates.

        Entities are stored apart from terrain -- ``entities/r.X.Z.mca``
        beside ``region/`` -- in the same Anvil container format. ``None``
        means the world has never saved an entity in that region, which is
        distinct from a region whose chunks merely hold no entities right
        now. ``create=True`` builds an empty file in memory instead (it
        reaches disk on the next :meth:`MinecraftSave.save`, like every
        other edit).
        """
        key = (region_x, region_z)
        loaded = self.entity_regions.get(key)
        if loaded is not None:
            return loaded
        path = self.entity_available.get(key)
        if path is None:
            if not create:
                return None
            self.refuse_read_only("make an entity file in")
            region = NBT()
            region.kind = "region"
            region.region_x, region.region_z = region_x, region_z
            region.path = os.path.join(
                self.entities_dir, f"r.{region_x}.{region_z}.mca"
            )
            self.entity_available[key] = region.path
            self.entity_regions[key] = region
            return region
        self.entity_regions[key] = NBT.load(path)
        return self.entity_regions[key]

    def entity_chunk(
        self,
        chunk_x: int,
        chunk_z: int,
        *,
        create: bool = False,
        data_version: int | None = None,
    ) -> Chunk | None:
        """The entity chunk at these chunk coordinates, or ``None``.

        An entity chunk's root is ``{Position, DataVersion, Entities}`` --
        positions inside the entity compounds are absolute world doubles.
        ``None`` means the world stores no entities there; a chunk that is
        present but unreadable reports through ``chunk.parse_error`` like any
        other. ``create=True`` makes an empty chunk (and its region file if
        needed) so entities can be added; that requires ``data_version``,
        because an entity chunk without one would make the game guess at the
        format on load.
        """
        if create:
            self.refuse_read_only("add an entity to")
        region = self.entity_region(chunk_x >> 5, chunk_z >> 5, create=create)
        if region is None:
            return None
        chunk = region.chunks.get((chunk_x & 31, chunk_z & 31))
        if chunk is None and create:
            if data_version is None:
                raise NBTError(
                    "creating an entity chunk needs a data_version to stamp it with"
                )
            chunk = Chunk(chunk_x & 31, chunk_z & 31)
            chunk.data = TagCompound(
                {
                    "Position": TagIntArray([chunk_x, chunk_z]),
                    "DataVersion": TagInt(int(data_version)),
                    "Entities": TagList(element_type=TAG_COMPOUND),
                }
            )
            region.chunks[(chunk_x & 31, chunk_z & 31)] = chunk
        return chunk

    def poi_region(self, region_x: int, region_z: int) -> NBT | None:
        """The points-of-interest file covering these region coordinates.

        POI storage is derived data -- the game's cache of where beds,
        workstations, bells and portals stand. ``None`` when the world has
        never written one there, which the game treats as "scan the chunks
        when they load". Files are loaded on demand, never created.
        """
        key = (region_x, region_z)
        loaded = self.poi_regions.get(key)
        if loaded is not None:
            return loaded
        path = self.poi_available.get(key)
        if path is None:
            return None
        self.poi_regions[key] = NBT.load(path)
        return self.poi_regions[key]

    def surface_y(
        self, x: int, z: int, heightmap: str = "MOTION_BLOCKING"
    ) -> int | None:
        """World y of the topmost surface block at global ``(x, z)``.

        A heightmap lookup, never a block scan; see
        :meth:`heaton_voxel.blocks.Blocks.surface_y` for the four maps and
        what each means. ``None`` folds together "no surface to report":
        ungenerated, unreadable, partly generated (no heightmaps yet), or an
        empty column. A stored map that contradicts the chunk's own height
        still raises -- that is corruption, not absence.
        """
        try:
            accessor = self.blocks(x >> 4, z >> 4)
        except NBTError:  # exists but unreadable; see Dimension.blocks
            return None
        if accessor is None:
            return None
        return accessor.surface_y(x & 15, z & 15, heightmap)

    def release(self, chunk_x: int, chunk_z: int) -> None:
        """Drop a chunk's decoded tree, flushing any edits into its bytes.

        Lets a fill sweep an arbitrarily large volume in flat memory. Also
        drops the cached accessor and its unpacked heightmaps, so the next
        read decodes fresh.
        """
        self._blocks_cache.pop((chunk_x, chunk_z), None)
        chunk = self.chunk(chunk_x, chunk_z)
        if chunk is not None:
            chunk.release()

    def loaded_regions(self) -> Iterator[NBT]:
        """Only the region files opened so far. Reads nothing."""
        return iter(list(self.regions.values()))

    def __iter__(self) -> Iterator[NBT]:
        """Every region file in the dimension, opening each as it is reached.

        Iterating deliberately covers the whole dimension rather than just
        what happens to be loaded: a sweep that silently skipped unread
        regions would look like a clean pass over an empty world. Use
        :meth:`loaded_regions` for the cheap subset.
        """
        for region_x, region_z in sorted(self.available):
            region = self.region(region_x, region_z)
            if region is not None:
                yield region

    def __repr__(self) -> str:
        return (
            f"<Dimension {self.name} {len(self.available)} region files, "
            f"{len(self.regions)} loaded>"
        )
