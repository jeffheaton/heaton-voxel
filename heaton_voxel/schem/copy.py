"""Moving boxes between a save and a :class:`Schematic`: export and paste,
with the same not-all-or-nothing accounting as ``fill``."""

from __future__ import annotations

import math
import os

from ..blocks import MIN_DATA_VERSION, BlockState
from ..nbt import (
    TAG_COMPOUND,
    NBTError,
    TagByte,
    TagCompound,
    TagInt,
    TagList,
)
from .results import SchemExportResult, SchemImportResult
from .schematic import Schematic
from .structure import STRUCTURE_VOID
from .support import (
    AIR_BLOCKS,
    _collect_uuids,
    _copy_tag,
    _fresh_uuid,
    _remap_uuids,
    _shift_entity,
)

def _copy_out(
    save,
    corner1,
    corner2,
    *,
    origin,
    entities,
    biomes,
    dimension,
    allow_incomplete,
    keep_loaded,
) -> tuple[Schematic, SchemExportResult]:
    dim = save.dimension(dimension)
    x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
    y0, y1 = sorted((int(corner1[1]), int(corner2[1])))
    z0, z1 = sorted((int(corner1[2]), int(corner2[2])))
    width, height, length = x1 - x0 + 1, y1 - y0 + 1, z1 - z0 + 1

    # The data version is stamped from the chunks themselves once one is
    # seen; MIN_DATA_VERSION is only a floor to get the object built.
    schematic = Schematic(width, height, length, MIN_DATA_VERSION)
    if origin is not None:
        schematic.offset = (
            x0 - int(origin[0]),
            y0 - int(origin[1]),
            z0 - int(origin[2]),
        )
    result = SchemExportResult(schematic)
    seen_data_version = 0
    blocks = schematic._blocks
    biome_values: list[int] | None = None
    if biomes:
        schematic.fill_biomes("minecraft:plains")  # placeholder for absences
        biome_values = schematic._biomes

    for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
        for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
            base_x, base_z = chunk_x * 16, chunk_z * 16
            lx0, lx1 = max(x0, base_x) - base_x, min(x1, base_x + 15) - base_x
            lz0, lz1 = max(z0, base_z) - base_z, min(z1, base_z + 15) - base_z
            column = (lx1 - lx0 + 1) * (lz1 - lz0 + 1)

            try:
                accessor = dim.blocks(chunk_x, chunk_z)
                if accessor is not None:
                    # A schematic holds one DataVersion for all its blocks,
                    # and an old chunk's states and block entities are not
                    # the current game's, so it is not exported as if they
                    # were: it is refused, or exported as air, like a chunk
                    # that cannot be read.
                    accessor.refuse_legacy("export to a schematic")
            except NBTError as exc:
                if not allow_incomplete:
                    raise NBTError(
                        f"chunk ({chunk_x}, {chunk_z}) in the export box exists "
                        f"but cannot be read ({exc}); something may be standing "
                        f"there that this export would misrepresent as air. "
                        f"Pass allow_incomplete=True to export air there anyway."
                    ) from None
                result.unreadable_chunks.append(((chunk_x, chunk_z), str(exc)))
                result.blocks_out_of_range += column * height
                continue
            if accessor is None:
                if not allow_incomplete:
                    raise NBTError(
                        f"chunk ({chunk_x}, {chunk_z}) in the export box has "
                        f"never been generated; there are no blocks there to "
                        f"copy. Pass allow_incomplete=True to export air there "
                        f"instead."
                    )
                result.missing_chunks.append((chunk_x, chunk_z))
                result.blocks_out_of_range += column * height
                continue

            seen_data_version = max(seen_data_version, accessor.data_version)
            for section_y in range(y0 >> 4, (y1 >> 4) + 1):
                base_y = section_y * 16
                ly0 = max(y0, base_y) - base_y
                ly1 = min(y1, base_y + 15) - base_y
                section = accessor.sections.get(section_y)
                container = section.blocks if section is not None else None
                if container is None:
                    # Above or below what the chunk stores: air, and counted.
                    result.blocks_out_of_range += column * (ly1 - ly0 + 1)
                else:
                    palette = container.palette
                    indices = container.indices()
                    local_map = [schematic._intern(state) for state in palette]
                    for ly in range(ly0, ly1 + 1):
                        sy = base_y + ly - y0
                        row_y = ly * 256
                        for lz in range(lz0, lz1 + 1):
                            src = row_y + lz * 16 + lx0
                            dst = (sy * length + (base_z + lz - z0)) * width + (
                                base_x + lx0 - x0
                            )
                            for _ in range(lx0, lx1 + 1):
                                blocks[dst] = local_map[indices[src]]
                                src += 1
                                dst += 1
                    result.blocks_exported += column * (ly1 - ly0 + 1)

                if biome_values is not None:
                    bio = section.biomes if section is not None else None
                    if bio is None:
                        result.biome_cells_absent += column * (ly1 - ly0 + 1)
                        if not allow_incomplete:
                            raise NBTError(
                                f"chunk ({chunk_x}, {chunk_z}) stores no biome "
                                f"data for section y={section_y}; the export "
                                f"cannot say what the climate there is. Pass "
                                f"allow_incomplete=True to write "
                                f"minecraft:plains there instead."
                            )
                    else:
                        bio_palette = bio.palette
                        bio_indices = bio.indices()
                        bio_map = [
                            schematic._intern_biome(str(name))
                            for name in bio_palette
                        ]
                        for ly in range(ly0, ly1 + 1):
                            sy = base_y + ly - y0
                            cell_y = (ly >> 2) * 16
                            for lz in range(lz0, lz1 + 1):
                                cell_yz = cell_y + (lz >> 2) * 4
                                dst = (sy * length + (base_z + lz - z0)) * width + (
                                    base_x + lx0 - x0
                                )
                                for lx in range(lx0, lx1 + 1):
                                    biome_values[dst] = bio_map[
                                        bio_indices[cell_yz + (lx >> 2)]
                                    ]
                                    dst += 1

            # Block entities ride with their blocks; position becomes
            # relative, keepPacked is the chunk's business, not the file's.
            chunk = dim.chunk(chunk_x, chunk_z)
            for entry in (chunk.data.get("block_entities") or ()) if chunk else ():
                ex, ey, ez = int(entry["x"]), int(entry["y"]), int(entry["z"])
                if x0 <= ex <= x1 and y0 <= ey <= y1 and z0 <= ez <= z1:
                    data = TagCompound(
                        {
                            key: _copy_tag(value)
                            for key, value in entry.items()
                            if key not in ("x", "y", "z", "keepPacked")
                        }
                    )
                    if "id" not in data:
                        raise NBTError(
                            f"block entity at ({ex}, {ey}, {ez}) has no 'id'; "
                            f"the chunk is damaged"
                        )
                    schematic.block_entities[(ex - x0, ey - y0, ez - z0)] = data
                    result.block_entities_exported += 1

            result.chunks_read += 1
            if not keep_loaded:
                dim.release(chunk_x, chunk_z)

    if entities:
        for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
            for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
                chunk = dim.entity_chunk(chunk_x, chunk_z)
                if chunk is None:
                    continue
                if chunk.data is None:
                    reason = f"entity storage: {chunk.parse_error}"
                    if not allow_incomplete:
                        raise NBTError(
                            f"entity storage for chunk ({chunk_x}, {chunk_z}) "
                            f"cannot be read ({chunk.parse_error}); entities "
                            f"may be standing there. Pass allow_incomplete="
                            f"True to export without them."
                        )
                    result.unreadable_chunks.append(((chunk_x, chunk_z), reason))
                    continue
                for entity in chunk.data.get("Entities") or ():
                    pos = entity.get("Pos")
                    if pos is None or len(pos) != 3:
                        continue
                    ex, ey, ez = (math.floor(float(v)) for v in pos)
                    if x0 <= ex <= x1 and y0 <= ey <= y1 and z0 <= ez <= z1:
                        copy = _copy_tag(entity)
                        _shift_entity(copy, -x0, -y0, -z0)
                        schematic.entities.append(copy)
                        result.entities_exported += 1
                if not keep_loaded:
                    chunk.release()

    if seen_data_version:
        schematic.data_version = seen_data_version
    else:
        # Nothing readable in the whole box (allow_incomplete with only
        # gaps); fall back to the save's own stamp before giving up.
        level = getattr(save, "level", None)
        stamp = level.root["Data"].get("DataVersion") if level is not None else None
        if stamp is None:
            raise NBTError(
                "the export box contained no readable chunk and level.dat has "
                "no DataVersion; cannot tell which Minecraft this world is"
            )
        schematic.data_version = int(stamp)
    return schematic, result


def export_schem(
    save,
    corner1: tuple[int, int, int],
    corner2: tuple[int, int, int],
    path: str | os.PathLike,
    *,
    origin: tuple[int, int, int] | None = None,
    entities: bool = False,
    biomes: bool = False,
    version: int = 3,
    dimension: str | None = None,
    allow_incomplete: bool = False,
    keep_loaded: bool = False,
) -> SchemExportResult:
    """Copy the inclusive box between two corners into a ``.schem`` file.

    Corners are global block coordinates in any order, exactly as
    :meth:`~heaton_voxel.world.MinecraftSave.fill` takes them. Block entities
    inside the box always travel with their blocks. The rest is opt-in:

    Args:
        save: An open :class:`~heaton_voxel.world.MinecraftSave`.
        corner1, corner2: Opposite corners, inclusive.
        path: Where to write the ``.schem``.
        origin: World point the schematic is anchored to -- where the author
            "stood". Pasting with ``anchor="origin"`` at the same point puts
            every block back at its original coordinates. Default: the box's
            minimum corner (a stored offset of zero).
        entities: Also copy entities (mobs, item frames, armor stands...)
            whose position floors into the box. Off by default, like
            WorldEdit's ``//copy`` without ``-e``. Positions become relative;
            identity (``UUID``) is preserved in the file and re-rolled on
            paste.
        biomes: Also record the biome of every block. Off by default; the
            result can only be written as ``version=3``.
        version: 3 (current spec, the default) or 2 (older tooling).
        dimension: Defaults to the dimension the player is in.
        allow_incomplete: A box reaching into chunks the world has never
            generated -- or has, but this toolkit cannot read -- is refused
            by default, because a schematic has no way to say "unknown" and
            would silently claim air. Pass True to write air there and get
            the gaps itemized on the result instead.
        keep_loaded: Keep visited chunks decoded in memory.

    Returns:
        A :class:`SchemExportResult`; its ``.schematic`` is the in-memory
        copy, already written to ``path``.
    """
    schematic, result = _copy_out(
        save,
        corner1,
        corner2,
        origin=origin,
        entities=entities,
        biomes=biomes,
        dimension=dimension,
        allow_incomplete=allow_incomplete,
        keep_loaded=keep_loaded,
    )
    schematic.save(path, version=version)
    result.path = os.fspath(path)
    return result


def _paste_in(
    save,
    schematic: Schematic,
    at,
    *,
    anchor,
    entities,
    biomes,
    paste_air,
    relight,
    heightmaps,
    dimension,
    prune,
    keep_loaded,
    rotate=0,
    flip=None,
    paste_voids=False,
) -> SchemImportResult:
    if anchor not in ("origin", "min"):
        raise ValueError(f"anchor must be 'origin' or 'min', got {anchor!r}")

    # Transforms come first, flip then rotate, so the composition is fixed
    # and documented rather than an accident of parameter order. Both return
    # copies; the caller's schematic is never altered by pasting it.
    if flip is not None:
        schematic = schematic.flipped(flip)
    if rotate % 360:
        schematic = schematic.rotated(rotate)

    # Content stamped by a newer Minecraft than the world's cannot be right:
    # the world's version does not know those blocks, and the game will not
    # data-fix them because the chunks claim to be current already.
    level = getattr(save, "level", None)
    world_version = None
    if level is not None:
        stamp = level.root["Data"].get("DataVersion")
        world_version = int(stamp) if stamp is not None else None
    if world_version is not None and schematic.data_version > world_version:
        raise NBTError(
            f"this schematic was made by a newer Minecraft (DataVersion "
            f"{schematic.data_version}) than the world (DataVersion "
            f"{world_version}); its blocks may not exist there. Update the "
            f"world first, or -- knowing the risk -- assign "
            f"schematic.data_version = {world_version} and paste again."
        )

    dim = save.dimension(dimension)
    ox, oy, oz = schematic.offset if anchor == "origin" else (0, 0, 0)
    px = int(at[0]) + ox
    py = int(at[1]) + oy
    pz = int(at[2]) + oz
    width, height, length = schematic.size
    x1, y1, z1 = px + width - 1, py + height - 1, pz + length - 1

    result = SchemImportResult()
    result.placed_box = ((px, py, pz), (x1, y1, z1))
    result.transform_warnings = list(schematic.transform_warnings)
    air_ids = frozenset(
        i for i, state in enumerate(schematic._palette) if state.name in AIR_BLOCKS
    )
    void_ids = frozenset(
        i for i, state in enumerate(schematic._palette) if state.name == STRUCTURE_VOID
    )
    # The cells a paste leaves as they stand: air when asked to, and every
    # structure_void unless asked not to.
    skipped_ids = ((frozenset() if paste_air else air_ids)
                   | (frozenset() if paste_voids else void_ids))
    biome_cells = schematic._biomes if biomes and schematic.has_biomes else None
    if biomes and biome_cells is None:
        if schematic.had_2d_biomes:
            raise NBTError(
                "this version-2 schematic carries only the old one-biome-per-"
                "column data, which cannot be applied to a world that stores "
                "biomes per 4x4x4 cell without inventing the vertical "
                "dimension. Re-export it as version 3."
            )
        raise NBTError("biomes=True, but this schematic carries no biome data")

    for chunk_x in range(px >> 4, (x1 >> 4) + 1):
        for chunk_z in range(pz >> 4, (z1 >> 4) + 1):
            base_x, base_z = chunk_x * 16, chunk_z * 16
            lx0, lx1 = max(px, base_x) - base_x, min(x1, base_x + 15) - base_x
            lz0, lz1 = max(pz, base_z) - base_z, min(z1, base_z + 15) - base_z
            column = (lx1 - lx0 + 1) * (lz1 - lz0 + 1)

            try:
                accessor = dim.writable_blocks(chunk_x, chunk_z)
            except NBTError as exc:
                result.unreadable_chunks.append(((chunk_x, chunk_z), str(exc)))
                result.blocks_out_of_range += column * height
                continue
            if accessor is None:
                result.missing_chunks.append((chunk_x, chunk_z))
                result.blocks_out_of_range += column * height
                continue
            if not accessor.finished:
                result.unfinished_chunks.append((chunk_x, chunk_z))
                result.blocks_out_of_range += column * height
                if not keep_loaded:
                    dim.release(chunk_x, chunk_z)
                continue

            written = _paste_chunk_blocks(
                schematic,
                accessor,
                (px, py, pz),
                (base_x, base_z),
                (lx0, lx1, lz0, lz1),
                air_ids,
                skipped_ids,
                prune,
                result,
            )
            if biome_cells is not None:
                _paste_chunk_biomes(
                    schematic,
                    accessor,
                    (px, py, pz),
                    (y1,),
                    (base_x, base_z),
                    (lx0, lx1, lz0, lz1),
                    result,
                )
            removed, added = _paste_chunk_block_entities(
                schematic,
                accessor,
                dim.chunk(chunk_x, chunk_z).data,
                (px, py, pz),
                (base_x, base_z),
                (lx0, lx1, lz0, lz1),
                skipped_ids,
            )
            result.block_entities_removed += removed
            result.block_entities_added += added
            if written or removed or added:
                result.chunks_modified += 1
            if heightmaps and written:
                accessor.rebuild_heightmaps()
                result.heightmaps_rebuilt += 1
            if not keep_loaded:
                dim.release(chunk_x, chunk_z)

    if entities and schematic.entities:
        _paste_entities(save, dim, schematic, (px, py, pz), result)

    if relight and result.chunks_modified:
        result.lighting = save.reset_lighting(
            (px, py, pz), (x1, y1, z1), dimension=dim.name
        )
    return result


def _paste_chunk_blocks(
    schematic, accessor, place, base, local, air_ids, skipped_ids, prune, result
) -> int:
    px, py, pz = place
    base_x, base_z = base
    lx0, lx1, lz0, lz1 = local
    width, height, length = schematic.size
    y1 = py + height - 1
    palette = schematic._palette
    cells = schematic._blocks
    column = (lx1 - lx0 + 1) * (lz1 - lz0 + 1)
    written = 0

    for section_y in range(py >> 4, (y1 >> 4) + 1):
        base_y = section_y * 16
        ly0 = max(py, base_y) - base_y
        ly1 = min(y1, base_y + 15) - base_y
        section = accessor.sections.get(section_y)
        container = section.blocks if section is not None else None
        if container is None:
            # Above or below what this chunk stores; nothing to write into.
            result.blocks_out_of_range += column * (ly1 - ly0 + 1)
            continue

        whole = (lx0, lx1, ly0, ly1, lz0, lz1) == (0, 15, 0, 15, 0, 15)
        if whole and not skipped_ids:
            # The schematic owns the entire section: rebuild it in one go.
            values: list[BlockState] = []
            for ly in range(16):
                sy = base_y + ly - py
                for lz in range(16):
                    src = (sy * length + (base_z + lz - pz)) * width + (base_x - px)
                    values.extend(palette[cells[src + lx]] for lx in range(16))
            container.set_all(values)
            result.sections_replaced += 1
            written += 4096
            continue

        groups: dict[int, list[int]] = {}
        for ly in range(ly0, ly1 + 1):
            sy = base_y + ly - py
            row = ly * 256
            for lz in range(lz0, lz1 + 1):
                src = (sy * length + (base_z + lz - pz)) * width + (base_x - px)
                row_z = row + lz * 16
                for lx in range(lx0, lx1 + 1):
                    slot = cells[src + lx]
                    if slot in skipped_ids:
                        if slot in air_ids:
                            result.air_skipped += 1
                        else:
                            result.voids_skipped += 1
                        continue
                    groups.setdefault(slot, []).append(row_z + lx)
        for slot, positions in groups.items():
            written += container.set_many(positions, palette[slot])
        if groups and prune:
            container.prune()

    result.blocks_pasted += written
    return written


def _paste_chunk_biomes(
    schematic, accessor, place, top, base, local, result
) -> None:
    """Apply the schematic's per-block biomes at the world's 4x4x4 grain.

    Every world cell the pasted box touches is set from the schematic's
    entry at the overlap's minimum corner. A box that starts mid-cell
    therefore recolors the whole cell -- the format has no finer
    granularity, the same trade :meth:`MinecraftSave.fill_biome` documents.
    """
    px, py, pz = place
    (y1,) = top
    base_x, base_z = base
    lx0, lx1, lz0, lz1 = local
    width, _, length = schematic.size
    biome_palette = schematic._biome_palette
    biome_cells = schematic._biomes

    for cell_x in range((base_x + lx0) >> 2, ((base_x + lx1) >> 2) + 1):
        sx = max(cell_x * 4, base_x + lx0)
        for cell_z in range((base_z + lz0) >> 2, ((base_z + lz1) >> 2) + 1):
            sz = max(cell_z * 4, base_z + lz0)
            for cell_y in range(py >> 2, (y1 >> 2) + 1):
                sy = max(cell_y * 4, py)
                name = biome_palette[
                    biome_cells[((sy - py) * length + (sz - pz)) * width + (sx - px)]
                ]
                try:
                    accessor.set_biome(sx - base_x, sy, sz - base_z, name)
                except (IndexError, NBTError):
                    result.biome_cells_out_of_range += 1
                else:
                    result.biome_cells_set += 1


def _paste_chunk_block_entities(
    schematic, accessor, chunk_data, place, base, local, skipped_ids
) -> tuple[int, int]:
    px, py, pz = place
    base_x, base_z = base
    lx0, lx1, lz0, lz1 = local
    width, height, length = schematic.size
    y1 = py + height - 1
    cells = schematic._blocks

    def overwritten(ex: int, ey: int, ez: int) -> bool:
        """Was the block at this world position written by the paste?"""
        if not (px <= ex <= px + width - 1 and py <= ey <= y1 and pz <= ez <= pz + length - 1):
            return False
        section = accessor.sections.get(ey >> 4)
        if section is None or section.blocks is None:
            return False  # out of the chunk's range; nothing was written
        if not skipped_ids:
            return True
        slot = cells[((ey - py) * length + (ez - pz)) * width + (ex - px)]
        return slot not in skipped_ids

    removed = 0
    stored = chunk_data.get("block_entities")
    if stored:
        kept = []
        for entry in stored:
            if overwritten(int(entry["x"]), int(entry["y"]), int(entry["z"])):
                removed += 1
            else:
                kept.append(entry)
        if removed:
            stored[:] = kept

    added = 0
    for (rx, ry, rz), data in schematic.block_entities.items():
        ex, ey, ez = px + rx, py + ry, pz + rz
        if not (base_x + lx0 <= ex <= base_x + lx1 and base_z + lz0 <= ez <= base_z + lz1):
            continue  # a different chunk's business
        section = accessor.sections.get(ey >> 4)
        if section is None or section.blocks is None:
            continue  # its block was not written; an orphan would linger
        if cells[((ry) * length + rz) * width + rx] in skipped_ids:
            continue
        if stored is None:
            stored = TagList(element_type=TAG_COMPOUND)
            chunk_data["block_entities"] = stored
        entry = _copy_tag(data)
        entry["x"] = TagInt(ex)
        entry["y"] = TagInt(ey)
        entry["z"] = TagInt(ez)
        if "keepPacked" not in entry:
            entry["keepPacked"] = TagByte(0)
        stored.append(entry)
        added += 1
    return removed, added


def _paste_entities(save, dim, schematic, place, result) -> None:
    px, py, pz = place

    # Fresh identities for everything pasted, remapped consistently so the
    # pasted copies still reference each other (leads, riders, owners). The
    # originals keep their UUIDs; a world cannot hold two entities with one
    # identity, and pasting the same file twice must not try.
    mapping: dict = {}
    for entity in schematic.entities:
        _collect_uuids(entity, mapping)
    for old in mapping:
        mapping[old] = list(_fresh_uuid())

    level = getattr(save, "level", None)
    stamp = level.root["Data"].get("DataVersion") if level is not None else None
    fallback_version = int(stamp) if stamp is not None else schematic.data_version

    for entity in schematic.entities:
        pos = entity.get("Pos")
        identity = str(entity.get("id", "?"))
        if pos is None or len(pos) != 3:
            result.entities_skipped.append((identity, (0.0, 0.0, 0.0)))
            continue
        ex, ey, ez = (float(pos[i]) + d for i, d in ((0, px), (1, py), (2, pz)))
        chunk_x, chunk_z = math.floor(ex) >> 4, math.floor(ez) >> 4

        # An entity needs ground under it: no terrain chunk, no paste; and
        # one over an unfinished chunk, or an old one the library never
        # writes, is not pasted either -- the blocks under it were refused
        # the same way.
        if dim.chunk(chunk_x, chunk_z) is None or _closed_terrain(dim, chunk_x, chunk_z):
            result.entities_skipped.append((identity, (ex, ey, ez)))
            continue
        chunk = dim.entity_chunk(
            chunk_x, chunk_z, create=True, data_version=fallback_version
        )
        if chunk.data is None:
            result.entities_skipped.append((identity, (ex, ey, ez)))
            continue
        stored = chunk.data.get("Entities")
        if stored is None:
            stored = TagList(element_type=TAG_COMPOUND)
            chunk.data["Entities"] = stored

        copy = _copy_tag(entity)
        _shift_entity(copy, px, py, pz)
        _remap_uuids(copy, mapping)
        stored.append(copy)
        result.entities_added += 1


def _closed_terrain(dim, chunk_x: int, chunk_z: int) -> bool:
    """Whether the terrain chunk here is one a paste leaves alone: one
    Minecraft 1.13 to 1.17 saved, or one the game has not finished. A chunk that
    cannot be read is not: its entities keep their own file, as they always
    have."""
    try:
        accessor = dim.blocks(chunk_x, chunk_z)
    except NBTError:
        return False
    return accessor is not None and (accessor.legacy or not accessor.finished)


def import_schem(
    save,
    source,
    at: tuple[int, int, int],
    *,
    anchor: str = "origin",
    entities: bool = True,
    biomes: bool = False,
    paste_air: bool = True,
    relight: bool = True,
    heightmaps: bool = True,
    dimension: str | None = None,
    prune: bool = True,
    keep_loaded: bool = False,
    rotate: int = 0,
    flip: str | None = None,
    paste_voids: bool = False,
) -> SchemImportResult:
    """Paste a schematic into a save, in memory; ``save.save()`` commits it.

    Args:
        save: An open :class:`~heaton_voxel.world.MinecraftSave`.
        source: A path to a ``.schem``, a structure ``.nbt`` or a
            ``.litematic`` (see :meth:`Schematic.load`), or a loaded
            :class:`Schematic`.
        at: Where the schematic's *origin* lands (see ``anchor``).
        anchor: ``"origin"`` (default) applies the file's stored offset, so
            standing at ``at`` gives exactly what WorldEdit's ``//paste``
            would build; a file whose offset is zero -- including everything
            this module exports without an ``origin=`` -- places its minimum
            corner at ``at``. ``"min"`` places the minimum corner at ``at``
            unconditionally, ignoring the stored offset.
        entities: Paste the entities the file carries, if any. On by default
            -- they are only in the file because the exporter asked for them.
            Every pasted entity gets a fresh UUID (consistently remapped, so
            leashes and riders inside the paste stay attached), and one whose
            chunk was never generated is skipped and reported.
        biomes: Apply the file's biome data. Off by default, because biomes
            live on a 4x4x4 grid: cells the box only partly covers are
            recolored whole. Version-2 column biomes are refused.
        paste_air: True (default) reproduces the box exactly, air included.
            False masks air out, so the schematic sits *on* the terrain
            instead of punching its bounding box through it.
        paste_voids: False (default) leaves every ``structure_void`` cell as
            the world has it, which is what the block means: the game never
            saves one into a structure, and places nothing where a structure
            leaves a cell out -- so a structure file, and the cells no region
            of a litematic covers, read as ``structure_void``. True writes
            them as the block itself.
        relight: Hand lighting in the pasted box back to the game afterwards
            (see :meth:`~heaton_voxel.world.MinecraftSave.reset_lighting`);
            the result's ``lighting`` holds that accounting.
        heightmaps: Rewrite the stored heightmaps of every chunk whose blocks
            the paste moved; the result's ``heightmaps_rebuilt`` counts them.
            Without this the game and every map go on reading the ground
            under the paste.
        dimension: Defaults to the dimension the player is in.
        prune: Drop palette entries the paste orphaned.
        keep_loaded: Keep every touched chunk decoded in memory.
        rotate: Degrees to rotate before placing, a multiple of 90; positive
            is clockwise seen from above (north faces become east), like
            WorldEdit's ``//rotate``. Applied about the schematic's origin.
        flip: Mirror before placing, along ``"x"`` (east<->west), ``"z"``
            (north<->south) or ``"y"`` (upside down). When both are given
            the flip happens first, then the rotation. See
            :meth:`Schematic.rotated` and :meth:`Schematic.flipped` for what
            gets rewritten; states the direction vocabulary cannot cover are
            reported on ``result.transform_warnings``, never guessed at.

    A schematic stamped by a newer Minecraft than the world is refused --
    the world's version does not know those blocks, and the game would not
    fix them up because the chunks already claim to be current. Stale block
    entities at overwritten positions are removed, so pasting stone over a
    chest does not leave the chest's inventory haunting the chunk. A chunk
    the game has not finished generating is skipped and reported like one it
    never generated, since it generates over whatever is written there.

    Returns:
        A :class:`SchemImportResult`. Check ``complete``, and report
        ``unreadable_chunks`` separately from ``missing_chunks`` and
        ``unfinished_chunks``.
    """
    schematic = source if isinstance(source, Schematic) else Schematic.load(source)
    return _paste_in(
        save,
        schematic,
        at,
        anchor=anchor,
        entities=entities,
        biomes=biomes,
        paste_air=paste_air,
        relight=relight,
        heightmaps=heightmaps,
        dimension=dimension,
        prune=prune,
        keep_loaded=keep_loaded,
        rotate=rotate,
        flip=flip,
        paste_voids=paste_voids,
    )
