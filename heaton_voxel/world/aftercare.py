"""The aftercare litany: lighting, heightmaps, POI, derived states.

Editing a world leaves baked data describing the world as it was; each of
these hands one kind back to the game. A mixin of
:class:`~heaton_voxel.world.MinecraftSave` -- one file per concern,
mirroring the C# port's partial classes (see PORTING.md).
"""

from __future__ import annotations

from ..nbt import NBTError
from ._state import SaveState
from .results import HeightmapResult, LightResetResult


class AftercareOps(SaveState):
    """Lighting reset, heightmap rebuild, POI invalidation, connections."""

    def reset_lighting(
        self,
        corner1: tuple[int, int, int],
        corner2: tuple[int, int, int],
        *,
        dimension: str | None = None,
        neighbors: bool = True,
    ) -> LightResetResult:
        """Hand lighting in an area back to the game. Call after editing.

        Chunk light is baked: the game stores computed light per section and
        trusts it for as long as a chunk's ``isLightOn`` flag stands, so any
        block edit leaves light describing the world as it was -- a torch
        placed in a newly carved room lights only its own block, and a
        removed roof leaves the room below it dark at noon. This clears the
        flag and the stale arrays for every generated chunk the box touches,
        which makes Minecraft recompute light when it next loads them.

        The corners are the same global block coordinates a :meth:`fill`
        takes, in any order -- passing the corners of the boxes you filled
        is the intended use. Light is stored per chunk, so whole chunks are
        reset and the y coordinates are ignored.

        ``neighbors`` widens the reset one chunk outward: light propagates
        up to 15 blocks across chunk borders, and a relit chunk beside an
        unmarked one pulls the neighbor's stale darkness back in. Leave it
        on unless the surrounding chunks are already covered.

        Ungenerated chunks are skipped without complaint -- generation
        lights a chunk when it builds it. Chunks that exist but cannot be
        read are reported on the result: their stale light cannot be
        cleared, and this refuses to guess at their contents.
        """
        dim = self.dimension(dimension)
        x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
        z0, z1 = sorted((int(corner1[2]), int(corner2[2])))
        ring = 1 if neighbors else 0

        result = LightResetResult()
        for chunk_x in range((x0 >> 4) - ring, (x1 >> 4) + ring + 1):
            for chunk_z in range((z0 >> 4) - ring, (z1 >> 4) + ring + 1):
                try:
                    accessor = dim.writable_blocks(chunk_x, chunk_z)
                except NBTError as exc:
                    # There, but not safely editable: an old chunk, or damaged.
                    result.unreadable_chunks.append(((chunk_x, chunk_z), str(exc)))
                    continue
                if accessor is None:
                    result.chunks_skipped += 1
                    continue
                result.arrays_removed += accessor.reset_lighting()
                result.chunks_reset += 1
        return result


    def rebuild_heightmaps(
        self,
        corner1: tuple[int, int, int],
        corner2: tuple[int, int, int],
        *,
        dimension: str | None = None,
        keep_loaded: bool = False,
    ) -> HeightmapResult:
        """Recompute stored heightmaps for every chunk a box touches.

        Heightmaps are cached data the game trusts, and editing blocks
        leaves them describing the old terrain -- which misleads mob
        spawning, this library's own :meth:`surface_y`, and every render
        built on it. This rewrites all four from the blocks, by the game's
        own tags (:func:`~heaton_voxel.blocks.heightmap_mask`), byte for
        byte what the game stores for a map that was right. Call it after
        building, like :meth:`reset_lighting`; the y coordinates are
        ignored, whole chunks are rebuilt.

        Ungenerated chunks are skipped and reported, and so are unfinished
        ones: the game generates over them, and writes their maps when it
        finishes them. Unreadable ones are the real warning: their stale
        maps cannot be replaced.
        """
        dim = self.dimension(dimension)
        x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
        z0, z1 = sorted((int(corner1[2]), int(corner2[2])))
        result = HeightmapResult()

        for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
            for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
                try:
                    accessor = dim.writable_blocks(chunk_x, chunk_z)
                except NBTError as exc:
                    result.unreadable_chunks.append(((chunk_x, chunk_z), str(exc)))
                    continue
                if accessor is None:
                    result.missing_chunks.append((chunk_x, chunk_z))
                    continue
                if accessor.finished:
                    accessor.rebuild_heightmaps()
                    result.chunks_rebuilt += 1
                else:
                    result.unfinished_chunks.append((chunk_x, chunk_z))
                if not keep_loaded:
                    dim.release(chunk_x, chunk_z)
        return result

    def invalidate_poi(
        self,
        corner1: tuple[int, int, int],
        corner2: tuple[int, int, int],
        *,
        dimension: str | None = None,
    ) -> int:
        """Drop stored points-of-interest records for every chunk in a box.

        POI storage is the game's cache of where beds, workstations, bells
        and portals stand. It is *not* updated by editing blocks, so a
        villager can hold a claim on a bed that no longer exists, and a
        freshly built workstation is invisible until something rescans.
        Deleting a chunk's records makes the game rescan that chunk on next
        load and rebuild them from the actual blocks -- the safe direction,
        since the cache is derived data.

        Call it over any area where beds, workstations, bells, portals or
        beehives were placed or removed. The y coordinates are ignored.
        Returns the number of chunk records dropped (an area that never had
        records needs nothing dropped: absent records already mean
        "rescan"). The change reaches disk with :meth:`save`.
        """
        dim = self.dimension(dimension)
        dim.refuse_read_only("drop points of interest from")
        x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
        z0, z1 = sorted((int(corner1[2]), int(corner2[2])))
        removed = 0
        for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
            for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
                region = dim.poi_region(chunk_x >> 5, chunk_z >> 5)
                if region is None:
                    continue
                if region.chunks.pop((chunk_x & 31, chunk_z & 31), None) is not None:
                    removed += 1
        return removed


    def connect_blocks(
        self,
        corner1: tuple[int, int, int],
        corner2: tuple[int, int, int],
        *,
        dimension: str | None = None,
        stairs: bool = True,
        keep_loaded: bool = False,
    ):
        """Recompute neighbor-derived states in a box: fence, wall, pane and
        iron-bar connections, gate ``in_wall`` flags, and stair corner shapes.

        Call it over any area where such blocks were placed, the way
        :meth:`reset_lighting` is called over any area that was edited --
        the game only derives these states on a block update, so blocks
        written directly render disconnected until then. The rules were
        validated against vanilla-placed blocks (0 disagreements in 22,616);
        see :func:`heaton_voxel.connect.connect_blocks` for exactly what is
        rewritten. Returns a :class:`heaton_voxel.connect.ConnectResult`.
        """
        from ..connect import connect_blocks

        return connect_blocks(
            self,
            corner1,
            corner2,
            dimension=dimension,
            stairs=stairs,
            keep_loaded=keep_loaded,
        )
