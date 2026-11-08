"""Reading and editing the world: blocks, biomes, fills, entities.

A mixin of :class:`~heaton_voxel.world.MinecraftSave` -- one file per
concern, mirroring the C# port's partial classes (see PORTING.md).
"""

from __future__ import annotations

import functools
import math
from collections import Counter

import os
from collections.abc import Mapping
from typing import Callable

from ..blocks import (
    AIR_BLOCKS,
    BIOMES_PER_SECTION,
    EMPTY_CHUNK_MAX_DATA_VERSION,
    EMPTY_CHUNK_MIN_DATA_VERSION,
    LEGACY_ENTITY_STORAGE_DATA_VERSION,
    BlockState,
    Blocks,
)
from ..nbt import (
    TAG_COMPOUND,
    TAG_DOUBLE,
    TAG_FLOAT,
    NBTError,
    TagByte,
    TagCompound,
    TagDouble,
    TagFloat,
    TagInt,
    TagList,
    TagLong,
    TagShort,
    TagString,
    fresh_uuid,
)
from ._state import SaveState
from .dimension import _DIMENSION_DIRS, CHUNK, END, NETHER, OVERWORLD, SECTION, Dimension
from .results import (
    BiomeFillResult,
    ChunkCreateResult,
    ChunkDeleteResult,
    EntityQueryResult,
    FillResult,
    FindResult,
    HistogramResult,
    ReplaceResult,
    UnfreezeResult,
)

#: How :meth:`EditingOps.find_blocks` may compare a state to what you asked for.
MATCH_MODES = ("name", "state", "suffix", "substring")

# --------------------------------------------------------------------------
# What freezing writes, and nothing else
# --------------------------------------------------------------------------
#
# Freezing writes exactly three things, so `unfreeze` rewrites exactly three:
# a `snow[layers=1]` covering, `ice` where a water **source** stood (flowing
# water is refused), and `snowy=true` on the grass_block, podzol or mycelium
# underneath -- which no writer sets directly. The layer goes down with
# `ServerLevel.setBlockAndUpdate`, and the block update runs
# `SnowyBlock.updateShape`, which derives `snowy` from whatever is above.
#
# **Two writers, not one, and a save cannot tell them apart.** 26.2's
# `ServerLevel.tickPrecipitation` writes those three per cold chunk tick;
# worldgen's `SnowAndFreezeFeature` (`Feature.FREEZE_TOP_LAYER`, shipped as
# the `minecraft:freeze_top_layer` placed feature and listed by snowy_taiga
# and friends) writes the same `Blocks.SNOW.defaultBlockState()`, the same
# `Blocks.ICE.defaultBlockState()` and the same `SNOWY`, through the same
# `Biome.shouldFreeze` / `Biome.shouldSnow` gates. Nothing in the bytes
# distinguishes the snow a storm laid from the snow the chunk was born with,
# so over a naturally cold biome this is a de-icing and not an undo: the
# game freezes it all back, and worldgen would too if the chunk were made
# again.
#
# Everything else that looks like winter -- snow_block, powder_snow,
# packed_ice, blue_ice, frosted_ice, powder_snow_cauldron -- is left
# standing. Thawing those would eat snowy hillsides and real builds: one
# live world here holds 1,289,399 snow_blocks and 2,381,441 packed_ice.
# Most of them freezing never writes at all (worldgen, crafting, a frost
# walker's footprints), but `powder_snow_cauldron` is an exclusion by
# *choice*, not by innocence: `tickPrecipitation` calls
# `Block.handlePrecipitation`, and `CauldronBlock.handlePrecipitation` fills
# a cauldron with powder snow on `Precipitation.SNOW`. A cauldron's contents
# are a container's level, not ground covering, and emptying one is not in
# this vocabulary.

_SNOW_LAYER = "minecraft:snow"
_WEATHER_ICE = "minecraft:ice"
#: The tops that carry a `snowy` flag; nothing else in the game has one.
_SNOWY_TOPS = frozenset(
    ("minecraft:grass_block", "minecraft:podzol", "minecraft:mycelium")
)
#: `BlockTags.SNOW` -- what `SnowyBlock.isSnowySetting` accepts above a top.
#: `data/minecraft/tags/block/snow.json`, and it is three blocks, not one.
_SNOW_TAG = frozenset(
    ("minecraft:snow", "minecraft:snow_block", "minecraft:powder_snow")
)
_THAWED_AIR = BlockState("minecraft:air")
#: Ice was a source block before it froze, so it is one again afterwards.
_THAWED_WATER = BlockState("minecraft:water", {"level": "0"})


def _thawed(state: BlockState, deep_snow: bool) -> tuple[BlockState, str] | None:
    """``(what it was before it froze, which kind)``, or ``None``.

    Pure, and matched against the palette rather than the blocks, so a
    section holding no winter at all is dismissed without unpacking a
    single index.
    """
    name = state.name
    if name == _SNOW_LAYER:
        # Freezing lays one layer and stops. `tickPrecipitation` deepens
        # snow only up to the `maxSnowAccumulationHeight` game rule, and
        # 26.2 registers it `registerInteger("max_snow_accumulation_height",
        # UPDATES, 1, 0, 8)` -- default **1** -- while worldgen's
        # `SnowAndFreezeFeature` places `Blocks.SNOW.defaultBlockState()`,
        # also one layer. So on a default world every deeper stack was piled
        # by a player, and taking it would gut the builds that are the whole
        # reason this operation is narrow. `deep_snow=True` is for worlds
        # that raised the rule.
        if deep_snow or state.properties.get("layers", "1") == "1":
            return _THAWED_AIR, "snow"
        return state, "deep_snow"
    if name == _WEATHER_ICE:
        return _THAWED_WATER, "ice"
    if name in _SNOWY_TOPS and state.properties.get("snowy") == "true":
        # Every other property survives, and the unsnowed twin is nearly
        # always already in the section's palette -- rebuilding the state
        # by name would add a duplicate entry beside it. Whether this one is
        # rewritten at all is not the state's own business: see `_snow_above`.
        return state.with_properties(snowy="false"), "snowy"
    return None


def _snow_above(
    accessor: Blocks,
    lx: int,
    y: int,
    lz: int,
    melting: set[tuple[int, int, int]],
) -> bool:
    """Will something in ``BlockTags.SNOW`` still stand over ``y`` afterwards?

    ``snowy`` is positional, not a property of the ground alone:
    ``SnowyBlock.isSnowySetting`` is ``state.is(BlockTags.SNOW)`` on the
    block **above**, and that tag is ``snow``, ``snow_block`` and
    ``powder_snow``. Clearing the flag under a snow_block this thaw
    deliberately leaves standing would write a state vanilla never produces
    and cannot repair: ``updateShape`` only fires on a neighbor change, so
    the ground would render dirt-sided forever.

    Reads the world, box or no box -- a thaw of a ground row alone leaves
    the layer one block above it untouched, and that layer still keeps the
    flag. ``melting`` is what this same call turns to air, so the answer is
    the world as the thaw will leave it rather than as it stands now.
    """
    if (lx, y + 1, lz) in melting:
        return False
    try:
        return accessor.get(lx, y + 1, lz).name in _SNOW_TAG
    except IndexError:
        # Above the top of the world: air, and air is not snow.
        return False


def _air_below(accessor: Blocks, lx: int, y: int, lz: int) -> bool:
    """Is the block under ``y`` air? Reads the world, box or no box.

    Deliberately outside the box being thawed: ice is a *lid*, and what
    hangs under it is the whole question.
    """
    try:
        return accessor.get(lx, y - 1, lz).name in AIR_BLOCKS
    except IndexError:
        # Below the world's floor, where the world simply stops, whatever
        # light the chunk keeps there. That is not air for water to fall
        # through.
        return False


# --------------------------------------------------------------------------
# Replacing blocks: a state-for-state mapping over a box
# --------------------------------------------------------------------------


#: The tags a block entity's x, y and z may be read from: whole numbers.
_INTEGER_TAGS = (TagByte, TagShort, TagInt, TagLong)


def _state_of(value: BlockState | str, role: str) -> BlockState:
    """A mapping's key or value as a state, or a refusal naming which."""
    if isinstance(value, BlockState):
        return value
    if isinstance(value, str):
        try:
            return BlockState.parse(value)
        except ValueError as exc:
            raise NBTError(f"cannot read the {role} {value!r}: {exc}") from exc
    raise NBTError(
        f"a replace {role} is a BlockState or a string, not {type(value).__name__}"
    )


def _replacement_table(
    mapping: Mapping[BlockState | str, BlockState | str],
) -> dict[BlockState, BlockState]:
    """The mapping keyed by canonical state, the form a palette entry is
    compared in, and with canonical targets, the form both codecs store a
    state in: a target spelled two ways would otherwise enter a palette
    twice. A key's unqualified name means the vanilla namespace, the way
    the game's commands read one."""
    table: dict[BlockState, BlockState] = {}
    for key, value in mapping.items():
        source = _state_of(key, "key")
        if ":" not in source.name:
            source = BlockState(f"minecraft:{source.name}", source.properties)
        source = source.canonical()
        target = _state_of(value, "value").canonical()
        if source in table and table[source] != target:
            raise NBTError(
                f"the mapping replaces {source} two ways: with {table[source]} "
                f"and with {target}"
            )
        table[source] = target
    return table


def _replace_target(
    table: dict[BlockState, BlockState], state: BlockState
) -> BlockState | None:
    """What the table writes over this stored state, or None to leave it:
    unnamed, or named with itself."""
    canonical = state.canonical()
    target = table.get(canonical)
    if target is None or target == canonical:
        return None
    return target


def _drop_orphaned_block_entities(
    chunk_data: TagCompound | None,
    accessor: Blocks,
    table: dict[BlockState, BlockState],
    low: tuple[int, int, int],
    high: tuple[int, int, int],
) -> int:
    """Drop each stored block entity in the box whose cell the replace
    gives another block id. Read before anything is written, so a cell
    still holds the block its entity belongs to. Returns how many went."""
    stored = chunk_data.get("block_entities") if chunk_data is not None else None
    if not stored:
        return 0
    x0, y0, z0 = low
    x1, y1, z1 = high
    kept = []
    removed = 0
    for entry in stored:
        where = [entry.get(axis) if isinstance(entry, TagCompound) else None
                 for axis in ("x", "y", "z")]
        numbers = [int(tag) for tag in where if isinstance(tag, _INTEGER_TAGS)]
        if len(numbers) != 3:
            # Not an entry this can place; it is somebody else's to judge.
            kept.append(entry)
            continue
        x, y, z = numbers
        if not (x0 <= x <= x1 and y0 <= y <= y1 and z0 <= z <= z1):
            kept.append(entry)
            continue
        section = accessor.sections.get(y >> 4)
        container = section.blocks if section is not None else None
        if container is None:
            kept.append(entry)
            continue
        state = container.get((y & 15) * 256 + (z & 15) * 16 + (x & 15))
        target = _replace_target(table, state)
        if target is not None and target.name != state.name:
            removed += 1
        else:
            kept.append(entry)
    if removed:
        stored[:] = kept
    return removed


def _replace_chunk(
    accessor: Blocks,
    table: dict[BlockState, BlockState],
    local_x: tuple[int, int],
    world_y: tuple[int, int],
    local_z: tuple[int, int],
    column: int,
    prune: bool,
    result: ReplaceResult,
) -> int:
    """Replace one chunk's share of the box. Returns blocks written.

    The palette answers first: a section none of whose entries the table
    names is passed over without unpacking an index, which is most of
    them. Where one is named, the cells are found in y, z, x order and
    written a target at a time, the targets in the order a cell first
    needed each -- one total order, so the palette and the bytes come out
    the same every time.
    """
    lx0, lx1 = local_x
    lz0, lz1 = local_z
    y0, y1 = world_y
    written = 0

    for section_y in range(y0 >> 4, (y1 >> 4) + 1):
        section = accessor.sections.get(section_y)
        container = section.blocks if section is not None else None
        base_y = section_y * SECTION
        ly0, ly1 = max(y0, base_y) - base_y, min(y1, base_y + 15) - base_y
        if container is None:
            # Above or below what this chunk stores.
            result.blocks_out_of_range += column * (ly1 - ly0 + 1)
            continue

        targets: dict[int, BlockState] = {}
        for slot, state in enumerate(container.palette):
            target = _replace_target(table, state)
            if target is not None:
                targets[slot] = target
        if not targets:
            continue

        whole = (lx0, lx1, ly0, ly1, lz0, lz1) == (0, 15, 0, 15, 0, 15)
        if whole and len(targets) == len(container.palette):
            if len(set(targets.values())) == 1:
                # Every block in the section becomes the one target: collapse
                # it, as a fill does, instead of rewriting 4096 indices.
                container.fill_all(targets[0])
                result.sections_replaced += 1
                written += 4096
                continue

        indices = container.indices()
        groups: dict[BlockState, list[int]] = {}
        for ly in range(ly0, ly1 + 1):
            for lz in range(lz0, lz1 + 1):
                row = ly * 256 + lz * 16
                for lx in range(lx0, lx1 + 1):
                    target = targets.get(indices[row + lx])
                    if target is not None:
                        groups.setdefault(target, []).append(row + lx)
        for target, positions in groups.items():
            written += container.set_many(positions, target)
        if groups and prune:
            container.prune()

    result.blocks_replaced += written
    return written


def _block_matcher(names, match: str):
    """Build the predicate ``find_blocks`` tests each palette entry with.

    Separated out because it is pure: given the search terms it returns a
    function of one :class:`BlockState`, and the scan itself never has to
    think about namespaces or property order again.
    """
    wanted = (names,) if isinstance(names, str) else tuple(names)
    if not wanted:
        raise NBTError("find_blocks needs at least one name to look for")
    if match not in MATCH_MODES:
        raise NBTError(
            f"unknown match mode {match!r}: use one of {', '.join(MATCH_MODES)}"
        )

    if match in ("name", "state"):
        canonical = []
        for text in wanted:
            try:
                state = BlockState.parse(text)
            except ValueError as exc:
                raise NBTError(f"cannot read search term {text!r}: {exc}") from exc
            # An unqualified name means the vanilla namespace, the way the
            # game's own commands read it.
            full = state.name if ":" in state.name else f"minecraft:{state.name}"
            if match == "name":
                if state.properties:
                    raise NBTError(
                        f"match='name' compares names alone, but {text!r} "
                        f"carries properties -- use match='state' for those"
                    )
                canonical.append(full)
            else:
                # Round-trip through BlockState so the properties are in the
                # canonical order str() emits, whatever order they were typed.
                canonical.append(str(BlockState(full, state.properties)))
        lookup = frozenset(canonical)
        if match == "name":
            return lambda state: state.name in lookup
        return lambda state: str(state) in lookup

    if match == "suffix":
        return lambda state: state.name.endswith(wanted)
    return lambda state: any(text in state.name for text in wanted)


class EditingOps(SaveState):
    """Block, biome, fill, census, search and entity operations."""

    def get_block(
        self, x: int, y: int, z: int, *, dimension: str | None = None
    ) -> BlockState | None:
        """The block at global coordinates, or ``None`` if not generated."""
        dim = self.dimension(dimension)
        try:
            accessor = dim.blocks(x >> 4, z >> 4)
        except NBTError:  # exists but unreadable; see Dimension.blocks
            return None
        if accessor is None:
            return None
        try:
            return accessor.get(x & 15, y, z & 15)
        except IndexError:  # outside this chunk's vertical range
            return None

    def set_block(
        self,
        x: int,
        y: int,
        z: int,
        block: BlockState | str,
        *,
        dimension: str | None = None,
    ) -> bool:
        """Set one block. False if that chunk is not generated, or cannot
        be written -- one Minecraft 1.13 to 1.17 saved is read and never written."""
        dim = self.dimension(dimension)
        try:
            accessor = dim.writable_blocks(x >> 4, z >> 4)
            if accessor is None:
                return False
            accessor.set(x & 15, y, z & 15, block)
        except (IndexError, NBTError):
            return False
        return True

    def place_block(
        self,
        x: int,
        y: int,
        z: int,
        block: BlockState | str,
        *,
        dimension: str | None = None,
    ) -> bool:
        """Place a block the way the game places an *item*.

        Where :meth:`set_block` writes exactly the state given, this
        completes what placement implies: a bed, a door or a tall plant gets
        both halves, a fence or stair placed here (and its existing
        neighbors) get their connection states settled, and a block set
        into a water source is waterlogged when it can be -- into flowing
        water it is not, and a block wet by default placed where its rule
        finds no water is written ``waterlogged=false``. Building code
        should use this; ``set_block`` remains the exact-write primitive.
        See :func:`heaton_voxel.place.place_block` for the full rules.

        Returns ``False``, writing nothing at all, if any cell of the
        placement falls outside the generated world -- a bed never ends up
        half-placed. Raises :class:`~heaton_voxel.nbt.NBTError`, again
        writing nothing, when a block that can take water, placed with
        ``waterlogged`` unsaid, displaces water at a level, or a
        ``waterlogged``, the game does not have.
        """
        from ..place import place_block

        return place_block(self, x, y, z, block, dimension=dimension)

    def surface_y(
        self,
        x: int,
        z: int,
        heightmap: str = "MOTION_BLOCKING",
        *,
        dimension: str | None = None,
    ) -> int | None:
        """World y of the topmost surface block at global ``(x, z)``.

        See :meth:`Dimension.surface_y`; this resolves the dimension first.
        """
        return self.dimension(dimension).surface_y(x, z, heightmap)

    def histogram(
        self,
        corner1: tuple[int, int, int],
        corner2: tuple[int, int, int],
        *,
        dimension: str | None = None,
        keep_loaded: bool = False,
    ) -> HistogramResult:
        """Count every block state in the box between two corners, inclusive.

        The census equivalent of :meth:`fill`: corners in any order, the box
        may span anything, ungenerated and unreadable chunks are reported
        rather than counted as empty. A section that lies entirely inside
        the box and holds one uniform state is counted in constant time, so
        sweeping a large volume costs far less than reading it block by
        block.

        Args:
            corner1, corner2: Opposite corners, inclusive, global
                coordinates.
            dimension: Defaults to the dimension the player is in.
            keep_loaded: Keep scanned chunks decoded in memory. Off by
                default so a large census stays flat.

        Returns:
            A :class:`HistogramResult`; ``result.counts`` maps
            :class:`BlockState` to occurrences, and ``result.top(n)`` gives
            the most common states.
        """
        dim = self.dimension(dimension)
        x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
        y0, y1 = sorted((int(corner1[1]), int(corner2[1])))
        z0, z1 = sorted((int(corner1[2]), int(corner2[2])))

        result = HistogramResult()
        counts = result.counts
        for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
            for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
                base_x, base_z = chunk_x * CHUNK, chunk_z * CHUNK
                lx0, lx1 = max(x0, base_x) - base_x, min(x1, base_x + 15) - base_x
                lz0, lz1 = max(z0, base_z) - base_z, min(z1, base_z + 15) - base_z
                column = (lx1 - lx0 + 1) * (lz1 - lz0 + 1)

                try:
                    accessor = dim.blocks(chunk_x, chunk_z)
                except NBTError as exc:
                    result.unreadable_chunks.append(((chunk_x, chunk_z), str(exc)))
                    result.blocks_out_of_range += column * (y1 - y0 + 1)
                    continue
                if accessor is None:
                    result.missing_chunks.append((chunk_x, chunk_z))
                    result.blocks_out_of_range += column * (y1 - y0 + 1)
                    continue

                for section_y in range(y0 >> 4, (y1 >> 4) + 1):
                    section = accessor.sections.get(section_y)
                    container = section.blocks if section is not None else None
                    base_y = section_y * SECTION
                    ly0 = max(y0, base_y) - base_y
                    ly1 = min(y1, base_y + 15) - base_y
                    if container is None:
                        result.blocks_out_of_range += column * (ly1 - ly0 + 1)
                        continue
                    whole = (lx0, lx1, ly0, ly1, lz0, lz1) == (0, 15, 0, 15, 0, 15)
                    palette = container.palette
                    if whole and len(palette) == 1:
                        # Uniform and fully inside: one dictionary bump.
                        counts[palette[0]] = counts.get(palette[0], 0) + 4096
                        result.blocks_counted += 4096
                        continue
                    indices = container.indices()
                    # counted a row at a time by Counter, which keeps the
                    # order each index is first met in, as a loop would
                    if whole:
                        tally = Counter(indices)
                    else:
                        tally = Counter()
                        for ly in range(ly0, ly1 + 1):
                            for lz in range(lz0, lz1 + 1):
                                row = ly * 256 + lz * 16
                                tally.update(indices[row + lx0:row + lx1 + 1])
                    for index, n in tally.items():
                        state = palette[index]
                        counts[state] = counts.get(state, 0) + n
                        result.blocks_counted += n
                result.chunks_scanned += 1
                if not keep_loaded:
                    dim.release(chunk_x, chunk_z)
        return result


    def find_blocks(
        self,
        corner1: tuple[int, int, int],
        corner2: tuple[int, int, int],
        names,
        *,
        match: str = "name",
        dimension: str | None = None,
        limit: int | None = None,
        keep_loaded: bool = False,
    ) -> FindResult:
        """Every block in the box whose state matches ``names``.

        ``names`` is one name or an iterable of them, and ``match`` says
        how to compare:

        * ``"name"`` (the default) -- the block name exactly, ignoring
          properties, so ``"oak_door"`` finds both halves of every oak
          door however it is hinged. An unqualified name is read as
          ``minecraft:``, the way the game's commands read it.
        * ``"state"`` -- the whole state including properties, written
          ``"oak_door[facing=north,half=lower]"``; property order does
          not matter.
        * ``"suffix"`` -- names ending in the term, which is how a whole
          family is asked for: ``"_door"``, ``"_stairs"``, ``"_fence"``.
        * ``"substring"`` -- names containing the term, for the families
          that share no common ending (``"glass"``, ``"glow"``).

        The scan reads each section's palette first, and a section whose
        palette holds nothing wanted is dismissed without unpacking its
        bit-packed indices at all -- so sweeping a region for something
        rare costs a fraction of reading it. Sections the world stores
        nothing for are air, and are dismissed the same way: a search for
        air will not report the implicit air of a section that was never
        written.

        ``limit`` stops the search early and sets ``truncated`` on the
        result, so a capped answer can never be mistaken for an
        exhaustive one.
        """
        dim = self.dimension(dimension)
        wanted = _block_matcher(names, match)
        x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
        y0, y1 = sorted((int(corner1[1]), int(corner2[1])))
        z0, z1 = sorted((int(corner1[2]), int(corner2[2])))

        result = FindResult()
        matches = result.matches
        counts = result.counts
        for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
            for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
                base_x, base_z = chunk_x * CHUNK, chunk_z * CHUNK
                lx0, lx1 = max(x0, base_x) - base_x, min(x1, base_x + 15) - base_x
                lz0, lz1 = max(z0, base_z) - base_z, min(z1, base_z + 15) - base_z

                try:
                    accessor = dim.blocks(chunk_x, chunk_z)
                except NBTError as exc:
                    result.unreadable_chunks.append(((chunk_x, chunk_z), str(exc)))
                    continue
                if accessor is None:
                    result.missing_chunks.append((chunk_x, chunk_z))
                    continue

                for section_y in range(y0 >> 4, (y1 >> 4) + 1):
                    section = accessor.sections.get(section_y)
                    container = section.blocks if section is not None else None
                    if container is None:
                        result.sections_skipped += 1
                        continue
                    palette = container.palette
                    # Membership only -- the set never orders anything that
                    # reaches the result, so the sweep keeps its total order.
                    hits = {i for i, state in enumerate(palette) if wanted(state)}
                    if not hits:
                        result.sections_skipped += 1
                        continue
                    result.sections_scanned += 1
                    base_y = section_y * SECTION
                    ly0 = max(y0, base_y) - base_y
                    ly1 = min(y1, base_y + 15) - base_y
                    indices = container.indices()
                    for ly in range(ly0, ly1 + 1):
                        for lz in range(lz0, lz1 + 1):
                            row = ly * 256 + lz * 16
                            for lx in range(lx0, lx1 + 1):
                                index = indices[row + lx]
                                if index not in hits:
                                    continue
                                if limit is not None and len(matches) >= limit:
                                    result.truncated = True
                                    result.chunks_scanned += 1
                                    if not keep_loaded:
                                        dim.release(chunk_x, chunk_z)
                                    return result
                                state = palette[index]
                                matches.append(
                                    (base_x + lx, base_y + ly, base_z + lz, state)
                                )
                                counts[state] = counts.get(state, 0) + 1
                result.chunks_scanned += 1
                if not keep_loaded:
                    dim.release(chunk_x, chunk_z)
        return result


    def get_biome(
        self, x: int, y: int, z: int, *, dimension: str | None = None
    ) -> str | None:
        """The biome at global coordinates, or ``None`` if not available.

        Takes block coordinates like every other accessor here, but biomes are
        stored per 4x4x4 cell, so the four blocks either side of any coordinate
        share an answer.

        ``None`` means the chunk is not generated, is unreadable, is outside
        the vertical range, or stores no biome data for that section -- the
        same "nothing to report" cases :meth:`get_block` folds together.
        """
        dim = self.dimension(dimension)
        try:
            accessor = dim.blocks(x >> 4, z >> 4)
        except NBTError:  # exists but unreadable; see Dimension.blocks
            return None
        if accessor is None:
            return None
        try:
            return accessor.get_biome(x & 15, y, z & 15)
        except IndexError:  # outside this chunk's vertical range
            return None
        except NBTError:  # section carries no biome container
            return None

    def set_biome(
        self,
        x: int,
        y: int,
        z: int,
        biome: str,
        *,
        dimension: str | None = None,
    ) -> bool:
        """Set the biome cell containing a block. False if it cannot be set.

        Note that this writes a whole 4x4x4 cell, not one block: the three
        neighbors in each direction that share the cell change with it. There
        is no finer granularity in the format. Use :meth:`fill_biome` when the
        area matters, so the return value describes the real extent.

        Changing a biome does not retroactively change the world. Snow layers
        and water are already-placed blocks and stay where they are; grass and
        foliage color is client-side and follows the new biome. Weather and
        mob spawning follow it immediately.
        """
        dim = self.dimension(dimension)
        try:
            accessor = dim.writable_blocks(x >> 4, z >> 4)
            if accessor is None:
                return False
            accessor.set_biome(x & 15, y, z & 15, biome)
        except (IndexError, NBTError):
            return False
        return True

    def fill_biome(
        self,
        corner1: tuple[int, int, int],
        corner2: tuple[int, int, int],
        biome: str,
        *,
        dimension: str | None = None,
        prune: bool = True,
        keep_loaded: bool = False,
    ) -> BiomeFillResult:
        """Set the biome across the box between two corners, inclusive.

        The corners are block coordinates and may be given in any order, and
        the box may span any number of chunks, sections and region files.
        Chunks the world has not generated are skipped and reported rather
        than created.

        Because a biome cell is 4x4x4, the box is grown outward to cell
        boundaries before anything is written -- a box from x=1 to x=6 covers
        cells 0 and 1, which is blocks 0 through 7. The box actually written is
        reported as ``written_box`` on the result rather than left implicit.

        This is a genuine 3D box, not a column operation. Biomes vary with
        height (that is how cave biomes work), so re-climating a surface area
        top to bottom means passing the world's vertical bounds explicitly.

        Sections entirely inside the box are collapsed to a single-entry
        palette, so a large fill costs a constant amount of work per section
        rather than per cell.

        Args:
            corner1, corner2: Opposite corners, inclusive, global block
                coordinates.
            biome: A biome id, like ``"minecraft:snowy_taiga"``. Not
                validated against the game's registry -- an unknown id will
                write cleanly and fail when Minecraft loads the chunk.
            dimension: Defaults to the dimension the player is in.
            prune: Drop biome palette entries the fill has orphaned.
            keep_loaded: Keep every touched chunk decoded in memory. Off by
                default so a huge fill does not accumulate the whole world.

        Returns:
            A :class:`BiomeFillResult` describing what was written.
        """
        dim = self.dimension(dimension)

        # Corners in any order.
        x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
        y0, y1 = sorted((int(corner1[1]), int(corner2[1])))
        z0, z1 = sorted((int(corner1[2]), int(corner2[2])))

        # Work in global cell coordinates from here: one cell is 4 blocks, and
        # >> 2 floors for negatives where int(x / 4) would not.
        cx0, cx1 = x0 >> 2, x1 >> 2
        cy0, cy1 = y0 >> 2, y1 >> 2
        cz0, cz1 = z0 >> 2, z1 >> 2

        result = BiomeFillResult()
        result.written_box = (
            (cx0 * 4, cy0 * 4, cz0 * 4),
            (cx1 * 4 + 3, cy1 * 4 + 3, cz1 * 4 + 3),
        )

        for chunk_x in range(cx0 >> 2, (cx1 >> 2) + 1):
            for chunk_z in range(cz0 >> 2, (cz1 >> 2) + 1):
                # This chunk's slice of the box, in global cell coordinates.
                # A chunk is 16 blocks across, so 4 cells.
                base_cx, base_cz = chunk_x * 4, chunk_z * 4
                slice_cx0, slice_cx1 = max(cx0, base_cx), min(cx1, base_cx + 3)
                slice_cz0, slice_cz1 = max(cz0, base_cz), min(cz1, base_cz + 3)
                column = (slice_cx1 - slice_cx0 + 1) * (slice_cz1 - slice_cz0 + 1)

                try:
                    accessor = dim.writable_blocks(chunk_x, chunk_z)
                except NBTError as exc:
                    # The chunk is there, we just cannot read it. Say so
                    # rather than implying the area is empty.
                    result.unreadable_chunks.append(((chunk_x, chunk_z), str(exc)))
                    result.cells_out_of_range += column * (cy1 - cy0 + 1)
                    continue
                if accessor is None:
                    result.missing_chunks.append((chunk_x, chunk_z))
                    result.cells_out_of_range += column * (cy1 - cy0 + 1)
                    continue

                written = self._fill_biome_chunk(
                    accessor,
                    biome,
                    (slice_cx0 - base_cx, slice_cx1 - base_cx),
                    (cy0, cy1),
                    (slice_cz0 - base_cz, slice_cz1 - base_cz),
                    column,
                    prune,
                    result,
                )
                if written:
                    result.chunks_modified += 1
                if not keep_loaded:
                    dim.release(chunk_x, chunk_z)
        return result

    @staticmethod
    def _fill_biome_chunk(
        accessor: Blocks,
        biome: str,
        local_cx: tuple[int, int],
        world_cy: tuple[int, int],
        local_cz: tuple[int, int],
        column: int,
        prune: bool,
        result: BiomeFillResult,
    ) -> int:
        """Fill one chunk's share of the biome box. Returns cells written."""
        lcx0, lcx1 = local_cx
        lcz0, lcz1 = local_cz
        cy0, cy1 = world_cy
        written = 0

        # A section is 16 blocks tall, so 4 cells: section Y = cell Y >> 2.
        for section_y in range(cy0 >> 2, (cy1 >> 2) + 1):
            section = accessor.sections.get(section_y)
            if section is None or section.biomes is None:
                # Above or below what this chunk stores, or a section that
                # carries blocks but no biome container.
                base = section_y * 4
                overlap = min(cy1, base + 3) - max(cy0, base) + 1
                result.cells_out_of_range += column * overlap
                continue

            base_cy = section_y * 4
            lcy0, lcy1 = max(cy0, base_cy) - base_cy, min(cy1, base_cy + 3) - base_cy
            container = section.biomes

            if (lcx0, lcx1, lcy0, lcy1, lcz0, lcz1) == (0, 3, 0, 3, 0, 3):
                # The whole section is inside the box: collapse it instead of
                # rewriting 64 indices.
                container.fill_all(biome)
                result.sections_replaced += 1
                written += BIOMES_PER_SECTION
                continue

            # Cells are ordered y, then z, then x, same as blocks.
            positions = [
                lcy * 16 + lcz * 4 + lcx
                for lcy in range(lcy0, lcy1 + 1)
                for lcz in range(lcz0, lcz1 + 1)
                for lcx in range(lcx0, lcx1 + 1)
            ]
            written += container.set_many(positions, biome)
            if prune:
                container.prune()

        result.cells_filled += written
        return written

    def fill(
        self,
        corner1: tuple[int, int, int],
        corner2: tuple[int, int, int],
        block: BlockState | str,
        *,
        dimension: str | None = None,
        prune: bool = True,
        keep_loaded: bool = False,
    ) -> FillResult:
        """Fill the axis-aligned box between two corners, inclusive.

        The corners may be given in any order and the box may span any number
        of chunks, sections and region files. Chunks the world has not
        generated are skipped and reported rather than created, since a chunk
        invented here would have no terrain, heightmaps or lighting.

        Sections that end up entirely inside the box are replaced wholesale
        with a single-entry palette, so a large solid fill costs a constant
        amount of work per 16x16x16 section instead of per block.

        Args:
            corner1, corner2: Opposite corners, inclusive, global coordinates.
            block: The block to place, as a :class:`BlockState` or a string
                like ``"minecraft:oak_stairs[facing=north]"``.
            dimension: Defaults to the dimension the player is in.
            prune: Drop palette entries the fill has orphaned.
            keep_loaded: Keep every touched chunk decoded in memory. Off by
                default so a huge fill does not accumulate the whole world.

        Returns:
            A :class:`FillResult` describing what was written.
        """
        if isinstance(block, str):
            block = BlockState.parse(block)
        dim = self.dimension(dimension)

        # Corners in any order.
        x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
        y0, y1 = sorted((int(corner1[1]), int(corner2[1])))
        z0, z1 = sorted((int(corner1[2]), int(corner2[2])))

        result = FillResult()
        for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
            for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
                # This chunk's slice of the box, in global coordinates.
                base_x, base_z = chunk_x * CHUNK, chunk_z * CHUNK
                slice_x0, slice_x1 = max(x0, base_x), min(x1, base_x + 15)
                slice_z0, slice_z1 = max(z0, base_z), min(z1, base_z + 15)
                column = (slice_x1 - slice_x0 + 1) * (slice_z1 - slice_z0 + 1)

                try:
                    accessor = dim.writable_blocks(chunk_x, chunk_z)
                except NBTError as exc:
                    # The chunk is there, we just cannot read it. Say so
                    # rather than implying the area is empty.
                    result.unreadable_chunks.append(((chunk_x, chunk_z), str(exc)))
                    result.blocks_out_of_range += column * (y1 - y0 + 1)
                    continue
                if accessor is None:
                    result.missing_chunks.append((chunk_x, chunk_z))
                    result.blocks_out_of_range += column * (y1 - y0 + 1)
                    continue

                written = self._fill_chunk(
                    accessor,
                    block,
                    (slice_x0 - base_x, slice_x1 - base_x),
                    (y0, y1),
                    (slice_z0 - base_z, slice_z1 - base_z),
                    column,
                    prune,
                    result,
                )
                if written:
                    result.chunks_modified += 1
                if not keep_loaded:
                    dim.release(chunk_x, chunk_z)
        return result

    @staticmethod
    def _fill_chunk(
        accessor: Blocks,
        block: BlockState,
        local_x: tuple[int, int],
        world_y: tuple[int, int],
        local_z: tuple[int, int],
        column: int,
        prune: bool,
        result: FillResult,
    ) -> int:
        """Fill one chunk's share of the box. Returns blocks written."""
        lx0, lx1 = local_x
        lz0, lz1 = local_z
        y0, y1 = world_y
        written = 0

        for section_y in range(y0 >> 4, (y1 >> 4) + 1):
            section = accessor.sections.get(section_y)
            if section is None or section.blocks is None:
                # Above or below what this chunk actually stores.
                base = section_y * SECTION
                overlap = min(y1, base + 15) - max(y0, base) + 1
                result.blocks_out_of_range += column * overlap
                continue

            base_y = section_y * SECTION
            ly0, ly1 = max(y0, base_y) - base_y, min(y1, base_y + 15) - base_y
            container = section.blocks

            if (lx0, lx1, ly0, ly1, lz0, lz1) == (0, 15, 0, 15, 0, 15):
                # The whole section is inside the box: collapse it instead of
                # rewriting 4096 indices.
                container.fill_all(block)
                result.sections_replaced += 1
                written += 4096
                continue

            positions = [
                ly * 256 + lz * 16 + lx
                for ly in range(ly0, ly1 + 1)
                for lz in range(lz0, lz1 + 1)
                for lx in range(lx0, lx1 + 1)
            ]
            written += container.set_many(positions, block)
            if prune:
                container.prune()

        result.blocks_filled += written
        return written

    def replace_blocks(
        self,
        corner1: tuple[int, int, int],
        corner2: tuple[int, int, int],
        mapping: Mapping[BlockState | str, BlockState | str],
        *,
        dimension: str | None = None,
        prune: bool = True,
        keep_loaded: bool = False,
    ) -> ReplaceResult:
        """Rewrite every block in a box that the mapping names, state for
        state, and leave every other block as it stands.

        A key matches a stored state as the 26.3 codec reads one: each with
        its block's defaults filled in (:meth:`BlockState.canonical`), so
        ``minecraft:water`` names ``water[level=0]`` and a key written in
        full matches a chunk of either codec. A value is written with its
        defaults filled in the same way, the form both codecs store a state
        in, so one target spelled two ways never enters a palette twice;
        the game reads a state that leaves a property out at its default,
        so this changes no block it loads. A mapping that names every state
        in a box is a fill, and the editor's fill is built that way: what
        it adds over :meth:`fill` is the next two rules.

        **A chunk the game has not finished is left alone**, and reported in
        ``unfinished_chunks``: the game generates over whatever is written
        into one, so the edit would be lost, or worse, half kept.
        :meth:`fill` writes into it; ``import_schem`` skips it, and so does
        this. A chunk Minecraft 1.13 to 1.17 saved is refused as a write
        and reported among ``unreadable_chunks``, as everywhere.

        **A block entity the rewrite orphans goes with it.** Where a cell's
        block *id* changes, the block entity stored for that cell is
        dropped; where only its properties change -- a chest turned to face
        another way -- it is kept, contents and all. The library cannot
        tell whether a new block takes the same kind of block entity as the
        old (a sign becoming another wood's sign does, a chest becoming a
        barrel does not), so it drops every one whose id changes, the
        direction that never leaves one standing in the wrong block.

        The writing order is total -- chunks by x then z, sections from the
        bottom, cells in y, z, x order, each target appended to a palette
        the first time a cell needs it -- so the same replace lays down the
        same bytes every time. A section wholly inside the box whose every
        palette entry maps to one target collapses to it, as :meth:`fill`
        collapses one. Like :meth:`fill`, this writes states and stops:
        :meth:`connect_blocks`, :meth:`rebuild_heightmaps`,
        :meth:`invalidate_poi` and :meth:`reset_lighting` are the caller's.

        Args:
            corner1, corner2: Opposite corners, inclusive, global
                coordinates, in any order.
            mapping: State to replace, state to write. Either may be a
                :class:`BlockState` or a string such as
                ``"minecraft:oak_stairs[facing=north]"``. Two keys naming one
                state with different targets are refused.
            dimension: Defaults to the dimension the player is in.
            prune: Drop palette entries the rewrite has orphaned.
            keep_loaded: Keep every touched chunk decoded in memory.

        Returns:
            A :class:`ReplaceResult` describing what was written.
        """
        table = _replacement_table(mapping)
        dim = self.dimension(dimension)

        x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
        y0, y1 = sorted((int(corner1[1]), int(corner2[1])))
        z0, z1 = sorted((int(corner1[2]), int(corner2[2])))

        result = ReplaceResult()
        for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
            for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
                base_x, base_z = chunk_x * CHUNK, chunk_z * CHUNK
                lx0, lx1 = max(x0, base_x) - base_x, min(x1, base_x + 15) - base_x
                lz0, lz1 = max(z0, base_z) - base_z, min(z1, base_z + 15) - base_z
                column = (lx1 - lx0 + 1) * (lz1 - lz0 + 1)

                try:
                    accessor = dim.writable_blocks(chunk_x, chunk_z)
                except NBTError as exc:
                    result.unreadable_chunks.append(((chunk_x, chunk_z), str(exc)))
                    result.blocks_out_of_range += column * (y1 - y0 + 1)
                    continue
                if accessor is None:
                    result.missing_chunks.append((chunk_x, chunk_z))
                    result.blocks_out_of_range += column * (y1 - y0 + 1)
                    continue
                if not accessor.finished:
                    result.unfinished_chunks.append((chunk_x, chunk_z))
                    result.blocks_out_of_range += column * (y1 - y0 + 1)
                    if not keep_loaded:
                        dim.release(chunk_x, chunk_z)
                    continue

                chunk = dim.chunk(chunk_x, chunk_z)
                # Before any write, while each block entity's cell still
                # holds the block it belongs to.
                removed = _drop_orphaned_block_entities(
                    chunk.data if chunk is not None else None, accessor, table,
                    (base_x + lx0, y0, base_z + lz0),
                    (base_x + lx1, y1, base_z + lz1),
                )
                written = _replace_chunk(
                    accessor, table, (lx0, lx1), (y0, y1), (lz0, lz1),
                    column, prune, result,
                )
                result.block_entities_removed += removed
                if written or removed:
                    result.chunks_modified += 1
                if not keep_loaded:
                    dim.release(chunk_x, chunk_z)
        return result

    def unfreeze(
        self,
        corner1: tuple[int, int, int],
        corner2: tuple[int, int, int],
        *,
        dimension: str | None = None,
        keep_loaded: bool = False,
        deep_snow: bool = False,
    ) -> UnfreezeResult:
        """Rewrite the three states freezing writes, and nothing else.

        Freezing writes exactly three things, so this rewrites exactly those
        three:

        * ``minecraft:snow[layers=1]`` -> ``minecraft:air``
        * ``minecraft:ice`` -> ``minecraft:water[level=0]``, the source it
          froze from
        * ``grass_block``/``podzol``/``mycelium`` with ``snowy=true`` ->
          the same block with ``snowy=false``, every other property kept --
          but only where nothing in ``BlockTags.SNOW`` (``snow``,
          ``snow_block``, ``powder_snow``) is left standing above it, since
          ``snowy`` is derived from that block and not from the ground

        **This is not an undo, and a save cannot make it one.** Two writers
        produce those states through the same ``Biome.shouldFreeze`` /
        ``Biome.shouldSnow`` gates: ``ServerLevel.tickPrecipitation`` on a
        cold chunk tick, and worldgen's ``SnowAndFreezeFeature``, shipped as
        the ``minecraft:freeze_top_layer`` placed feature and listed by
        snowy_taiga and every other cold biome. The bytes are identical, so
        over a naturally cold biome this is a **de-icing**: what it clears
        the weather will lay down again, and regeneration would too.

        **Nothing else is touched, which is the point.** ``snow_block``,
        ``powder_snow``, ``packed_ice``, ``blue_ice``, ``frosted_ice`` and
        ``powder_snow_cauldron`` are left standing -- eating them would take
        out snowy hillsides and real builds along with the weather. Most of
        them freezing never writes; the cauldron is excluded by choice, as
        ``tickPrecipitation`` does fill one with powder snow through
        ``Block.handlePrecipitation``, and a container's level is not ground
        covering.

        Like :meth:`fill`, this writes states and stops. It runs **no
        aftercare**, and three passes are worth making afterwards:
        :meth:`connect_blocks`, because ice is a sturdy full cube (see
        ``connect._STURDY_SUFFIXES``) and melting it genuinely changes how
        the fences, walls and panes beside it connect;
        :meth:`rebuild_heightmaps`, because a snow layer is part of
        ``WORLD_SURFACE`` (the motion-blocking maps sit under it) and taking
        it off lowers that surface; and :meth:`reset_lighting`, because
        opaque snow and ice let no light through and the stored light still
        says they do.

        Args:
            corner1, corner2: Opposite corners, inclusive, global
                coordinates, in any order.
            dimension: Defaults to the dimension the player is in.
            keep_loaded: Keep every touched chunk decoded in memory. Off by
                default so a large thaw does not accumulate the world.
            deep_snow: Also remove ``snow`` at ``layers=2..8``. Off by
                default: freezing stops at the ``maxSnowAccumulationHeight``
                game rule, which is **1** unless a world raised it, so a
                deeper stack is a player's. What is left comes back as
                ``deep_snow_kept`` rather than going unmentioned.

        Returns:
            An :class:`UnfreezeResult`. Its ``floating_water`` counts melted
            ice standing over air -- a frozen lid on a drained lake -- which
            is reported, never refused.
        """
        dim = self.dimension(dimension)

        # Corners in any order.
        x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
        y0, y1 = sorted((int(corner1[1]), int(corner2[1])))
        z0, z1 = sorted((int(corner1[2]), int(corner2[2])))

        result = UnfreezeResult()
        for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
            for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
                base_x, base_z = chunk_x * CHUNK, chunk_z * CHUNK
                lx0, lx1 = max(x0, base_x) - base_x, min(x1, base_x + 15) - base_x
                lz0, lz1 = max(z0, base_z) - base_z, min(z1, base_z + 15) - base_z

                try:
                    accessor = dim.writable_blocks(chunk_x, chunk_z)
                except NBTError as exc:
                    # There, just not readable. Say so rather than implying
                    # the ground under it thawed.
                    result.unreadable_chunks.append(((chunk_x, chunk_z), str(exc)))
                    continue
                if accessor is None:
                    result.missing_chunks.append((chunk_x, chunk_z))
                    continue

                if self._unfreeze_chunk(
                    accessor, (lx0, lx1), (y0, y1), (lz0, lz1), result, deep_snow
                ):
                    result.chunks_modified += 1
                if not keep_loaded:
                    dim.release(chunk_x, chunk_z)
        return result

    @staticmethod
    def _unfreeze_chunk(
        accessor: Blocks,
        local_x: tuple[int, int],
        world_y: tuple[int, int],
        local_z: tuple[int, int],
        result: UnfreezeResult,
        deep_snow: bool,
    ) -> int:
        """Thaw one chunk's share of the box. Returns blocks rewritten.

        Every rewrite is worked out before any of them is made, and the two
        neighbor questions are why: the floating-water check reads the block
        *below* a melted lid, and the ``snowy`` check reads the block
        *above* the ground. Either may itself be snow this same call
        removes, so deciding against a half-thawed chunk would make the
        answer depend on which section the sweep happened to reach first.

        Both therefore read the blocks as the call found them. ``melting``
        carries the one difference between the questions: floating water
        asks what was holding the lid up, which is a fact about the world
        that was, while ``snowy`` asks what the game will derive next, which
        is a fact about the world this thaw leaves behind.
        """
        lx0, lx1 = local_x
        lz0, lz1 = local_z
        y0, y1 = world_y
        # Section Y -> [(position, state to write, kind, lx, y, lz)], in scan
        # order, so the same thaw lays the same palette down every time.
        found: dict[int, list[tuple[int, BlockState, str, int, int, int]]] = {}
        # Every cell this call turns to air, in the chunk's own coordinates.
        melting: set[tuple[int, int, int]] = set()

        for section_y in range(y0 >> 4, (y1 >> 4) + 1):
            section = accessor.sections.get(section_y)
            container = section.blocks if section is not None else None
            if container is None:
                # A section the chunk does not store is air, and air never
                # froze.
                continue
            targets: dict[int, tuple[BlockState, str]] = {}
            for slot, state in enumerate(container.palette):
                thawed = _thawed(state, deep_snow)
                if thawed is not None:
                    targets[slot] = thawed
            if not targets:
                # Nothing frozen in this section's palette, so none of its
                # 4096 indices need unpacking at all.
                continue

            base_y = section_y * SECTION
            ly0, ly1 = max(y0, base_y) - base_y, min(y1, base_y + 15) - base_y
            indices = container.indices()
            candidates: list[tuple[int, BlockState, str, int, int, int]] = []
            for ly in range(ly0, ly1 + 1):
                for lz in range(lz0, lz1 + 1):
                    row = ly * 256 + lz * 16
                    for lx in range(lx0, lx1 + 1):
                        target = targets.get(indices[row + lx])
                        if target is None:
                            continue
                        state, kind = target
                        candidates.append(
                            (row + lx, state, kind, lx, base_y + ly, lz)
                        )
                        if kind == "snow":
                            melting.add((lx, base_y + ly, lz))
            if candidates:
                found[section_y] = candidates

        # Section Y -> {state to write: positions}, both still in scan order.
        planned: dict[int, dict[BlockState, list[int]]] = {}
        for section_y, candidates in found.items():
            groups: dict[BlockState, list[int]] = {}
            for position, state, kind, lx, y, lz in candidates:
                if kind == "snow":
                    result.snow_removed += 1
                elif kind == "deep_snow":
                    # Deeper than freezing lays it, so it stays -- counted
                    # rather than passed over in silence.
                    result.deep_snow_kept += 1
                    continue
                elif kind == "ice":
                    result.ice_melted += 1
                    if _air_below(accessor, lx, y, lz):
                        result.floating_water += 1
                else:
                    if _snow_above(accessor, lx, y, lz, melting):
                        # Snow this thaw leaves standing, so `snowy=true` is
                        # the state the game itself would derive here.
                        continue
                    result.snowy_cleared += 1
                groups.setdefault(state, []).append(position)
            if groups:
                planned[section_y] = groups

        written = 0
        for section_y, groups in planned.items():
            container = accessor.sections[section_y].blocks
            if container is None:  # never: only a section with blocks is planned
                continue
            for state, positions in groups.items():
                written += container.set_many(positions, state)
            # Melting a lake usually orphans the ice entry outright.
            container.prune()
        return written


    def create_chunks(
        self,
        corner1: tuple[int, int, int],
        corner2: tuple[int, int, int],
        *,
        dimension: str | None = None,
        biome: str | None = None,
    ) -> ChunkCreateResult:
        """Build finished chunks of air wherever a box has none, so a world
        can be built on past the ground the game has generated.

        The corners are global block coordinates in any order, and y is
        ignored: a chunk is a column. Every chunk the box covers that the
        world has no record of is built by
        :meth:`Dimension.create_chunk` -- ``Status`` finished, so the game
        loads it as it stands and never generates over it, all air under
        ``biome`` (the dimension's own when not given: plains, nether wastes,
        the end), as tall as the dimension's other chunks, and its light
        handed to the game, which lights it on first load. Region files are
        made as the chunks need them, and a vanilla dimension the world has
        never visited is made too. A chunk the world holds is left as it
        stands, and named in the result.

        The chunks are stamped with the DataVersion the world was last saved
        by, or 26.3's for a newer world, which the game upgrades on load; a
        world last saved before 26.2 is refused, since its chunks may carry
        fields these do not. Build on them with :meth:`fill`,
        :meth:`set_block` and :meth:`place_block`, then
        :meth:`rebuild_heightmaps` as after any edit, and :meth:`save`.
        """
        self._refuse_read_only("build chunks in")
        name = dimension if dimension is not None else self._default_dimension()
        dim = self.dimensions.get(name)
        if dim is None:
            relative = _DIMENSION_DIRS.get(name)
            if relative is None:
                known = ", ".join(sorted(_DIMENSION_DIRS))
                raise NBTError(f"unknown dimension {name!r}; expected one of {known}")
            dim = Dimension(name, os.path.join(self.path, relative))
            dim.index()
            self.dimensions[name] = dim
        if biome is None:
            biome = {OVERWORLD: "minecraft:plains", NETHER: "minecraft:nether_wastes",
                     END: "minecraft:the_end"}.get(name)
            if biome is None:
                raise NBTError(f"{name} has no biome of its own to default to; name one")

        stamp = self._level_data().get("DataVersion")
        if stamp is None:
            raise NBTError("level.dat records no DataVersion to stamp new chunks with")
        if int(stamp) < EMPTY_CHUNK_MIN_DATA_VERSION:
            raise NBTError(
                f"this world was last saved by DataVersion {int(stamp)}, older "
                f"than the oldest finished chunk this library has read "
                f"({EMPTY_CHUNK_MIN_DATA_VERSION}, 26.2); open it in a current "
                f"Minecraft first")
        version = min(int(stamp), EMPTY_CHUNK_MAX_DATA_VERSION)

        x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
        z0, z1 = sorted((int(corner1[2]), int(corner2[2])))
        result = ChunkCreateResult()
        result.data_version, result.biome = version, biome
        span: tuple[int, int] | None = None
        for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
            for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
                if dim.chunk(chunk_x, chunk_z) is not None:
                    try:
                        found = dim.blocks(chunk_x, chunk_z)
                    except NBTError as exc:
                        result.unreadable_chunks.append(((chunk_x, chunk_z), str(exc)))
                        continue
                    if found is not None and found.finished:
                        result.existing_chunks.append((chunk_x, chunk_z))
                    else:
                        result.unfinished_chunks.append((chunk_x, chunk_z))
                    continue
                if span is None:
                    span = dim.section_range()
                    result.min_y, result.max_y = span[0] * SECTION, (span[1] + 1) * SECTION - 1
                region_key = (chunk_x >> 5, chunk_z >> 5)
                new_region = region_key not in dim.available
                dim.create_chunk(chunk_x, chunk_z, data_version=version, biome=biome,
                                 sections=span)
                result.regions_created += new_region
                result.chunks_created += 1
                result.created_chunks.append((chunk_x, chunk_z))
        return result

    def delete_chunks(
        self,
        corner1: tuple[int, int, int],
        corner2: tuple[int, int, int],
        *,
        dimension: str | None = None,
    ) -> ChunkDeleteResult:
        """Remove whole chunks, so Minecraft generates them again on sight.

        The corners are global **block** coordinates in any order, and y is
        ignored: a chunk is a column and the whole column goes. The smallest
        thing that can be deleted is a chunk, so a box that starts mid-chunk
        clears the whole chunk -- the rectangle actually cleared comes back
        as ``deleted_box`` rather than being left implicit, the way
        :meth:`fill_biome` reports ``written_box``.

        A chunk lives in three files and all three are cleared, because
        nothing rebuilds the other two from the terrain: ``EntityStorage``
        reads ``entities/`` without consulting the chunk, so leftover
        villagers and armor stands would reappear floating in fresh terrain,
        and ``PoiSection.refresh`` returns early on a persisted
        ``Valid: true``, so stale points of interest are never rescanned and
        villagers keep claims on beds that no longer exist.

        Deleting never decodes a chunk, so a chunk too damaged to read is
        deleted like any other -- which is usually why one is being deleted.
        The change reaches disk with :meth:`save`, and an emptied region
        file is left in place: a region with a hole in it is exactly what
        Minecraft writes when a chunk has never been visited.

        **This is not an undo.** The game regenerates from the world seed
        with the *current* version's generator, so what comes back is new
        terrain, not the terrain that was there before. Take a
        :class:`~heaton_voxel.craft.worksite.Worksite` snapshot first.
        """
        dim = self.dimension(dimension)
        dim.refuse_read_only("delete chunks from")

        # Corners in any order; y is not part of the question.
        x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
        z0, z1 = sorted((int(corner1[2]), int(corner2[2])))
        cx0, cx1 = x0 >> 4, x1 >> 4
        cz0, cz1 = z0 >> 4, z1 >> 4

        result = ChunkDeleteResult()
        result.deleted_box = (
            (cx0 * CHUNK, cz0 * CHUNK),
            (cx1 * CHUNK + 15, cz1 * CHUNK + 15),
        )

        touched: set[tuple[int, int]] = set()
        for chunk_x in range(cx0, cx1 + 1):
            for chunk_z in range(cz0, cz1 + 1):
                # First, always: Dimension.blocks memoizes its accessors and
                # answers from that cache without looking at region.chunks
                # again, so a chunk deleted underneath a live accessor would
                # go on reporting minecraft:full and a surface height for
                # ground that is no longer there.
                dim.release(chunk_x, chunk_z)

                region_x, region_z = chunk_x >> 5, chunk_z >> 5
                key = (chunk_x & 31, chunk_z & 31)
                removed = False

                region = dim.region(region_x, region_z)
                if region is not None and region.chunks.pop(key, None) is not None:
                    result.chunks_deleted += 1
                    removed = True
                else:
                    result.chunks_absent += 1

                # Both of these must be *opened* to be emptied: save() writes
                # only the regions it holds open, so deciding from
                # `entity_available` alone would compute the right answer and
                # write nothing. A None return is the honest "the world has
                # never stored any there".
                entities = dim.entity_region(region_x, region_z)
                if entities is not None and entities.chunks.pop(key, None) is not None:
                    result.entity_chunks_deleted += 1
                    removed = True
                poi = dim.poi_region(region_x, region_z)
                if poi is not None and poi.chunks.pop(key, None) is not None:
                    result.poi_chunks_deleted += 1
                    removed = True

                if removed:
                    touched.add((region_x, region_z))
        result.regions_touched = len(touched)
        return result


    def spawn_entity(
        self,
        identity: str,
        x: float,
        y: float,
        z: float,
        *,
        yaw: float = 0.0,
        pitch: float = 0.0,
        extra: dict | None = None,
        dimension: str | None = None,
    ) -> bool:
        """Add an entity to the world, with the structural NBT handled.

        Builds the minimal compound every entity shares -- ``id``, exact
        ``Pos``, ``Rotation``, and a **fresh UUID** (two entities sharing one
        make the game drop a copy, so there is no way to pass a UUID in) --
        and files it in the right entity chunk, creating that chunk and its
        region file in memory if the world has never stored an entity there.
        Everything else about the entity is the game's to default on load;
        pass ``extra`` for the tags a kind needs beyond the shared ones
        (``VillagerData``, a sheep's ``Color``, ...), values as Tag
        instances.

        Coordinates are exact entity coordinates: to stand something in the
        middle of block ``(bx, by, bz)``, pass ``bx + 0.5, by, bz + 0.5``.
        Refuses (``False``, nothing written) when the terrain chunk was
        never generated -- an entity needs ground under it -- and raises
        :class:`~heaton_voxel.blocks.ReadOnlyChunk` over a chunk Minecraft
        1.13 to 1.17 saved, or in a save in the layout before 26.1. As always, nothing reaches disk before :meth:`save`.
        """
        dim = self.dimension(dimension)
        dim.refuse_read_only("add an entity to")
        chunk_x, chunk_z = math.floor(x) >> 4, math.floor(z) >> 4
        if dim.chunk(chunk_x, chunk_z) is None:
            return False
        try:
            terrain = dim.blocks(chunk_x, chunk_z)
        except NBTError:
            terrain = None  # unreadable ground: entities keep their own file
        if terrain is not None:
            # Over an old chunk, which the library reads and never writes.
            terrain.refuse_legacy("add an entity to")

        stamp = self._level_data().get("DataVersion") if self.level is not None else None
        if stamp is None:
            ground = dim.blocks(chunk_x, chunk_z)
            if ground is None:  # never: the chunk was found above
                return False
            stamp = ground.data_version
        chunk = dim.entity_chunk(
            chunk_x, chunk_z, create=True, data_version=int(stamp)
        )
        if chunk is None:  # never: create=True makes one or refuses
            raise NBTError(f"no entity storage could be made for chunk ({chunk_x}, {chunk_z})")
        if chunk.data is None:
            raise NBTError(
                f"entity storage for chunk ({chunk_x}, {chunk_z}) exists but "
                f"cannot be read: {chunk.parse_error}"
            )
        stored = chunk.data.get("Entities")
        if stored is None:
            stored = TagList(element_type=TAG_COMPOUND)
            chunk.data["Entities"] = stored
        tag = TagCompound(
            {
                "id": TagString(identity),
                "Pos": TagList(
                    [TagDouble(float(x)), TagDouble(float(y)), TagDouble(float(z))],
                    element_type=TAG_DOUBLE,
                ),
                "Rotation": TagList(
                    [TagFloat(float(yaw)), TagFloat(float(pitch))],
                    element_type=TAG_FLOAT,
                ),
                "UUID": fresh_uuid(),
            }
        )
        for key, value in (extra or {}).items():
            tag[key] = value
        stored.append(tag)
        return True


    def entities_in(
        self,
        corner1: tuple[int, int, int],
        corner2: tuple[int, int, int],
        *,
        ids=None,
        dimension: str | None = None,
        limit: int | None = None,
        keep_loaded: bool = False,
    ) -> EntityQueryResult:
        """Every entity standing inside the box.

        The read twin of :meth:`spawn_entity`: entities live apart from
        terrain, in ``entities/r.X.Z.mca``, and this walks the chunks the
        box covers and reports what is filed there. ``ids`` filters by
        entity type -- one id or an iterable, unqualified names read as
        ``minecraft:`` -- and ``None`` reports everything, mobs, items,
        armor stands and all.

        Membership is by the block the entity stands in: its stored
        double coordinates are floored, so an entity at ``x = -0.3`` is
        in block ``-1``, not ``0``.

        A chunk with no entity storage is not a failure -- absent storage
        means no entities, which is a complete answer -- so those are
        counted in ``chunks_without_storage`` and nothing else. Storage
        that exists and cannot be read is the case worth knowing about,
        and lands in ``unreadable_chunks``.

        Each entry carries the entity's whole compound, so ``VillagerData``,
        ``CustomName`` and the rest need no second pass. Those tags are the
        live ones while their chunk is loaded; this releases each chunk as
        it finishes, and a tag edited after its chunk was released is
        edited on a tree the world no longer reads. Pass
        ``keep_loaded=True`` when the plan is to modify what comes back.
        """
        dim = self.dimension(dimension)
        x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
        y0, y1 = sorted((int(corner1[1]), int(corner2[1])))
        z0, z1 = sorted((int(corner1[2]), int(corner2[2])))

        wanted = None
        if ids is not None:
            listed = (ids,) if isinstance(ids, str) else tuple(ids)
            if not listed:
                raise NBTError(
                    "entities_in was given an empty id filter; pass ids=None "
                    "to report every entity"
                )
            wanted = frozenset(
                text if ":" in text else f"minecraft:{text}" for text in listed
            )

        result = EntityQueryResult()
        for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
            for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
                chunk = dim.entity_chunk(chunk_x, chunk_z)
                release: Callable[[], None]
                if chunk is None:
                    # Before 20w45a a chunk kept its entities in its own
                    # Level, and a world nobody has stood in since keeps
                    # them there still.
                    terrain = dim.chunk(chunk_x, chunk_z)
                    version = None
                    if terrain is not None and terrain.data is not None:
                        version = terrain.data.get("DataVersion")
                    if version is None or int(version) >= LEGACY_ENTITY_STORAGE_DATA_VERSION:
                        result.chunks_without_storage += 1
                        continue
                    try:
                        terrain_blocks = dim.blocks(chunk_x, chunk_z)
                        stored = (None if terrain_blocks is None
                                  else terrain_blocks.legacy_entities)
                    except NBTError as caught:
                        result.unreadable_chunks.append(
                            ((chunk_x, chunk_z), f"entities in the chunk: {caught}"))
                        continue
                    # released as terrain, so its accessor goes with it
                    release = functools.partial(dim.release, chunk_x, chunk_z)
                elif chunk.data is None:
                    result.unreadable_chunks.append(
                        ((chunk_x, chunk_z),
                         f"entity storage: {chunk.parse_error}")
                    )
                    continue
                else:
                    stored = chunk.data.get("Entities")
                    release = chunk.release
                if stored is None:
                    stored = ()
                elif not isinstance(stored, list):
                    # Storage that exists and makes no sense is unreadable,
                    # not empty. Reporting it as "no entities here" would
                    # let a corrupt chunk look like a quiet one.
                    result.unreadable_chunks.append(
                        ((chunk_x, chunk_z),
                         f"entity storage: Entities is a "
                         f"{type(stored).__name__}, not a list")
                    )
                    if not keep_loaded:
                        release()
                    continue
                result.chunks_scanned += 1
                for entity in stored:
                    position = entity.get("Pos")
                    if position is None or len(position) != 3:
                        continue
                    ex, ey, ez = (float(value) for value in position)
                    if not (math.isfinite(ex) and math.isfinite(ey)
                            and math.isfinite(ez)):
                        # NaN floors to nothing and infinity floors to
                        # everything; either way the entity has no block to
                        # be in, and guessing one would file it somewhere
                        # real. Name it instead.
                        result.unreadable_chunks.append(
                            ((chunk_x, chunk_z),
                             f"entity {str(entity.get('id') or '?')} has "
                             f"non-finite Pos ({ex}, {ey}, {ez})")
                        )
                        continue
                    if not (x0 <= math.floor(ex) <= x1
                            and y0 <= math.floor(ey) <= y1
                            and z0 <= math.floor(ez) <= z1):
                        continue
                    # Explicit rather than ``str(tag or "")``: a tag that is
                    # merely falsy -- an empty string, a zero -- is still a
                    # tag that is there, and only an absent one reads as "".
                    stored_id = entity.get("id")
                    identity = "" if stored_id is None else str(stored_id)
                    if wanted is not None and identity not in wanted:
                        continue
                    if limit is not None and len(result.entities) >= limit:
                        result.truncated = True
                        if not keep_loaded:
                            release()
                        return result
                    result.entities.append((identity, ex, ey, ez, entity))
                    result.counts[identity] = result.counts.get(identity, 0) + 1
                if not keep_loaded:
                    release()
        return result
