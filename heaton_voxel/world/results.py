"""Result objects for whole-save operations.

Every operation that can partially succeed reports exactly what happened:
chunks the world never generated and chunks that exist but cannot be read
are always listed apart, because "nothing there" and "something there I
cannot see" mean opposite things to a user.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..blocks import BlockState

__all__ = [
    "FillResult",
    "BiomeFillResult",
    "UnfreezeResult",
    "ChunkDeleteResult",
    "ChunkCreateResult",
    "HistogramResult",
    "FindResult",
    "EntityQueryResult",
    "LightResetResult",
    "HeightmapResult",
    "WeatherState",
    "WorldBorder",
]


class WorldBorder:
    """The world border: a square wall centered on ``(center_x, center_z)``.

    ``size`` is the full diameter, so the wall stands ``size / 2`` out from
    the center on each axis. A world that has never had its border changed
    keeps no border file at all; that state comes back as the game's
    defaults with :attr:`stored` False, so "default border" and "border
    somebody set" stay distinguishable.
    """

    #: The default diameter: walls at x and z = +/-29,999,984.
    DEFAULT_SIZE = 59_999_968.0

    __slots__ = (
        "center_x",
        "center_z",
        "size",
        "safe_zone",
        "damage_per_block",
        "warning_blocks",
        "warning_time",
        "lerp_target",
        "lerp_time",
        "stored",
    )

    def __init__(self, center_x: float = 0.0, center_z: float = 0.0,
                 size: float = DEFAULT_SIZE, safe_zone: float = 5.0,
                 damage_per_block: float = 0.2, warning_blocks: int = 5,
                 warning_time: int = 15, lerp_target: float | None = None,
                 lerp_time: int = 0, stored: bool = False):
        self.center_x = center_x
        self.center_z = center_z
        self.size = size
        self.safe_zone = safe_zone
        self.damage_per_block = damage_per_block
        self.warning_blocks = warning_blocks
        self.warning_time = warning_time
        # A border at rest lerps to where it already is.
        self.lerp_target = size if lerp_target is None else lerp_target
        self.lerp_time = lerp_time
        self.stored = stored

    @property
    def min_x(self) -> float:
        return self.center_x - self.size / 2

    @property
    def max_x(self) -> float:
        return self.center_x + self.size / 2

    @property
    def min_z(self) -> float:
        return self.center_z - self.size / 2

    @property
    def max_z(self) -> float:
        return self.center_z + self.size / 2

    def contains(self, x: float, z: float) -> bool:
        """Whether ``(x, z)`` is inside the border, walls inclusive."""
        return (self.min_x <= x <= self.max_x
                and self.min_z <= z <= self.max_z)

    def distance_to_edge(self, x: float, z: float) -> float:
        """Blocks from ``(x, z)`` to the nearest wall.

        Positive inside the border, negative outside, zero on a wall --
        signed so "how close am I" and "how far out am I" are one number.
        """
        return min(x - self.min_x, self.max_x - x,
                   z - self.min_z, self.max_z - z)

    def clamp(self, x: float, z: float,
              margin: float = 0.0) -> tuple[float, float]:
        """The nearest point to ``(x, z)`` that is inside the border.

        ``margin`` pulls the allowed square in from the walls, for callers
        that want to stop short rather than dead on the line. A margin
        wider than the border itself collapses to the center.
        """
        lo_x, hi_x = self.min_x + margin, self.max_x - margin
        lo_z, hi_z = self.min_z + margin, self.max_z - margin
        if lo_x > hi_x:
            lo_x = hi_x = self.center_x
        if lo_z > hi_z:
            lo_z = hi_z = self.center_z
        return (min(max(x, lo_x), hi_x), min(max(z, lo_z), hi_z))

    def __repr__(self) -> str:
        where = "stored" if self.stored else "default"
        return (f"<WorldBorder {where}, {self.size:g} across, "
                f"center ({self.center_x:g}, {self.center_z:g})>")


class WeatherState:
    """The world's weather exactly as stored, plus what it adds up to.

    The stored form is five fields because the game runs two timers at
    once; :attr:`kind` collapses them into the one word a person means --
    ``"thunder"`` outranks ``"rain"`` outranks ``"clear"``, the same
    precedence the game's own renderer applies.
    """

    __slots__ = (
        "raining",
        "thundering",
        "rain_time",
        "thunder_time",
        "clear_weather_time",
    )

    def __init__(self, raining: bool, thundering: bool, rain_time: int,
                 thunder_time: int, clear_weather_time: int):
        self.raining = raining
        self.thundering = thundering
        self.rain_time = rain_time
        self.thunder_time = thunder_time
        self.clear_weather_time = clear_weather_time

    @property
    def kind(self) -> str:
        """``"clear"``, ``"rain"`` or ``"thunder"``."""
        if self.raining and self.thundering:
            return "thunder"
        if self.raining:
            return "rain"
        return "clear"

    def __repr__(self) -> str:
        ticks = (self.clear_weather_time if self.kind == "clear"
                 else self.rain_time)
        return f"<WeatherState {self.kind}, {ticks} ticks on the clock>"


class FillResult:
    """What a :meth:`MinecraftSave.fill` actually managed to do.

    A fill is not all-or-nothing: a box can reach into chunks the world has
    never generated, or above and below the buildable range. Rather than
    failing or pretending, the counts say exactly what happened.
    """

    __slots__ = (
        "blocks_filled",
        "sections_replaced",
        "chunks_modified",
        "missing_chunks",
        "unreadable_chunks",
        "blocks_out_of_range",
    )

    def __init__(self):
        self.blocks_filled = 0
        self.sections_replaced = 0
        self.chunks_modified = 0
        # Chunks the world has never generated.
        self.missing_chunks: list[tuple[int, int]] = []
        # Chunks that exist but could not be read -- almost always a chunk
        # written by a version older than this toolkit supports. Kept separate
        # from `missing_chunks` on purpose: a long-lived world can hold a
        # decade-old build in a chunk Minecraft has not re-saved since, and
        # reporting that as "never generated" would suggest the area is empty
        # when something is actually standing there.
        self.unreadable_chunks: list[tuple[tuple[int, int], str]] = []
        self.blocks_out_of_range = 0

    @property
    def complete(self) -> bool:
        """True if every block in the box was written."""
        return not (
            self.missing_chunks or self.unreadable_chunks or self.blocks_out_of_range
        )

    def __repr__(self) -> str:
        parts = [
            f"{self.blocks_filled:,} blocks",
            f"{self.chunks_modified} chunks",
            f"{self.sections_replaced} whole sections",
        ]
        if self.missing_chunks:
            parts.append(f"{len(self.missing_chunks)} chunks ungenerated")
        if self.unreadable_chunks:
            parts.append(f"{len(self.unreadable_chunks)} chunks unreadable")
        if self.blocks_out_of_range:
            parts.append(f"{self.blocks_out_of_range:,} blocks out of range")
        return f"<FillResult {', '.join(parts)}>"


class ReplaceResult:
    """What a :meth:`MinecraftSave.replace_blocks` actually managed to do.

    Kept apart from :class:`FillResult` because a replace stays out of two
    kinds of chunk a fill writes into without asking: one the game has not
    finished generating, which it would generate over the edit, and one it
    cannot write, which it reports among ``unreadable_chunks`` (a chunk
    Minecraft 1.13 to 1.17 saved is read, never written). And a replace
    drops the block entities its rewrites orphan, which a fill leaves.
    """

    __slots__ = (
        "blocks_replaced",
        "sections_replaced",
        "chunks_modified",
        "block_entities_removed",
        "missing_chunks",
        "unfinished_chunks",
        "unreadable_chunks",
        "blocks_out_of_range",
    )

    def __init__(self):
        self.blocks_replaced = 0
        self.sections_replaced = 0
        self.chunks_modified = 0
        self.block_entities_removed = 0
        # Chunks the world has never generated.
        self.missing_chunks: list[tuple[int, int]] = []
        # Chunks the game has not finished generating, left as they are.
        self.unfinished_chunks: list[tuple[int, int]] = []
        # Chunks there but not writable here, with the reason.
        self.unreadable_chunks: list[tuple[tuple[int, int], str]] = []
        self.blocks_out_of_range = 0

    @property
    def complete(self) -> bool:
        """True if every block in the box was read, and so every one the
        mapping names was rewritten."""
        return not (
            self.missing_chunks
            or self.unfinished_chunks
            or self.unreadable_chunks
            or self.blocks_out_of_range
        )

    def __repr__(self) -> str:
        parts = [
            f"{self.blocks_replaced:,} blocks",
            f"{self.chunks_modified} chunks",
            f"{self.sections_replaced} whole sections",
        ]
        if self.block_entities_removed:
            parts.append(f"{self.block_entities_removed} block entities removed")
        if self.missing_chunks:
            parts.append(f"{len(self.missing_chunks)} chunks ungenerated")
        if self.unfinished_chunks:
            parts.append(f"{len(self.unfinished_chunks)} chunks unfinished")
        if self.unreadable_chunks:
            parts.append(f"{len(self.unreadable_chunks)} chunks unreadable")
        if self.blocks_out_of_range:
            parts.append(f"{self.blocks_out_of_range:,} blocks out of range")
        return f"<ReplaceResult {', '.join(parts)}>"


class BiomeFillResult:
    """What a :meth:`MinecraftSave.fill_biome` actually managed to do.

    Deliberately separate from :class:`FillResult` rather than reusing it:
    biomes are stored per 4x4x4 cell, so every count here is in cells, and
    naming them ``blocks_filled`` would understate the affected volume by a
    factor of 64.

    ``written_box`` is the part that matters for trusting a fill. The box asked
    for is in block coordinates, but the smallest thing that can be written is
    a whole cell, so a box that starts mid-cell writes the whole cell. Rather
    than coerce silently, the fill reports the block-coordinate box it actually
    wrote, which is the requested box grown outward to cell boundaries.
    """

    __slots__ = (
        "cells_filled",
        "sections_replaced",
        "chunks_modified",
        "missing_chunks",
        "unreadable_chunks",
        "cells_out_of_range",
        "written_box",
    )

    def __init__(self):
        self.cells_filled = 0
        self.sections_replaced = 0
        self.chunks_modified = 0
        # Chunks the world has never generated.
        self.missing_chunks: list[tuple[int, int]] = []
        # Chunks that exist but could not be read. Kept separate from
        # `missing_chunks` for the same reason as in FillResult.
        self.unreadable_chunks: list[tuple[tuple[int, int], str]] = []
        # Cells in sections this chunk does not store, or that carry no biome
        # container at all.
        self.cells_out_of_range = 0
        # The block-coordinate box actually written, cell-aligned. None if the
        # fill never got as far as writing anything.
        self.written_box: tuple[tuple[int, int, int], tuple[int, int, int]] | None = None

    @property
    def complete(self) -> bool:
        """True if every cell in the box was written."""
        return not (
            self.missing_chunks or self.unreadable_chunks or self.cells_out_of_range
        )

    def __repr__(self) -> str:
        parts = [
            f"{self.cells_filled:,} cells",
            f"{self.chunks_modified} chunks",
            f"{self.sections_replaced} whole sections",
        ]
        if self.missing_chunks:
            parts.append(f"{len(self.missing_chunks)} chunks ungenerated")
        if self.unreadable_chunks:
            parts.append(f"{len(self.unreadable_chunks)} chunks unreadable")
        if self.cells_out_of_range:
            parts.append(f"{self.cells_out_of_range:,} cells out of range")
        return f"<BiomeFillResult {', '.join(parts)}>"


class UnfreezeResult:
    """What a :meth:`MinecraftSave.unfreeze` thawed.

    The three counters are the three states freezing writes, kept apart
    because they answer different questions: ``snow_removed`` is covering
    taken off, ``ice_melted`` is water handed back, and ``snowy_cleared`` is
    the ``snowy=true`` flag on the ground underneath, which is cosmetic and
    outlives both.

    There is no out-of-range count, unlike :class:`FillResult`: a section
    the chunk does not store is air, and air is not frozen, so a box
    reaching past the world's height has nothing to fail at.

    ``floating_water`` is a **report, never a refusal**. It counts melted
    ice whose block below is air -- a frozen lid over a drained lake, which
    thaws into water hanging in the sky and then falls when the game next
    updates it. Nothing about that is corrupt, but it is rarely what the
    caller pictured, and it is invisible from a top-down render.

    ``deep_snow_kept`` is the same idea for what was *not* touched: snow at
    ``layers=2..8``, which freezing does not lay unless a world raised the
    ``maxSnowAccumulationHeight`` game rule off its default of 1, so it is
    almost always a player's. ``unfreeze(deep_snow=True)`` takes it; the
    counter is here so that leaving it is said out loud.
    """

    __slots__ = (
        "snow_removed",
        "ice_melted",
        "snowy_cleared",
        "deep_snow_kept",
        "chunks_modified",
        "floating_water",
        "missing_chunks",
        "unreadable_chunks",
    )

    def __init__(self):
        self.snow_removed = 0
        self.ice_melted = 0
        self.snowy_cleared = 0
        # Snow left standing because it is deeper than freezing lays it; see
        # the class docstring.
        self.deep_snow_kept = 0
        self.chunks_modified = 0
        # Melted ice standing over air; see the class docstring.
        self.floating_water = 0
        # Chunks the world has never generated.
        self.missing_chunks: list[tuple[int, int]] = []
        # Chunks that exist but could not be read; separate from missing for
        # the reason FillResult gives.
        self.unreadable_chunks: list[tuple[tuple[int, int], str]] = []

    @property
    def complete(self) -> bool:
        """True if every chunk in the box was read and thawed."""
        return not (self.missing_chunks or self.unreadable_chunks)

    def __repr__(self) -> str:
        parts = []
        if self.snow_removed:
            parts.append(f"{self.snow_removed:,} snow")
        if self.ice_melted:
            parts.append(f"{self.ice_melted:,} ice")
        if self.snowy_cleared:
            parts.append(f"{self.snowy_cleared:,} unsnowed")
        if not parts and not self.deep_snow_kept:
            # Deep snow left standing is something frozen, so saying
            # "nothing frozen" beside a count of it would contradict itself.
            parts.append("nothing frozen")
        parts.append(f"{self.chunks_modified} chunks")
        if self.deep_snow_kept:
            parts.append(f"{self.deep_snow_kept:,} deep snow kept")
        if self.floating_water:
            parts.append(f"{self.floating_water:,} water over air")
        if self.missing_chunks:
            parts.append(f"{len(self.missing_chunks)} chunks ungenerated")
        if self.unreadable_chunks:
            parts.append(f"{len(self.unreadable_chunks)} chunks unreadable")
        return f"<UnfreezeResult {', '.join(parts)}>"


class ChunkDeleteResult:
    """What a :meth:`MinecraftSave.delete_chunks` removed.

    A chunk lives in three files, and the counters are per file rather than
    summed: terrain in ``region/``, entities in ``entities/``, cached
    points of interest in ``poi/``. A world stores the last two only where
    it has something to store, so zeros there are ordinary.

    ``chunks_absent`` is chunks the box covered that had no terrain record
    to remove -- already regenerating, in other words, which is the state
    this operation asks for. Nothing here is a failure, so there is no
    ``complete``: a chunk cannot be too damaged to delete, and deleting
    never decodes one.

    ``deleted_box`` is the block-coordinate rectangle actually cleared,
    grown outward to chunk boundaries. It has no y: the whole column goes,
    and inventing bounds for a chunk that no longer exists would be a
    guess dressed as a fact.
    """

    __slots__ = (
        "chunks_deleted",
        "chunks_absent",
        "entity_chunks_deleted",
        "poi_chunks_deleted",
        "regions_touched",
        "deleted_box",
    )

    def __init__(self):
        self.chunks_deleted = 0
        self.chunks_absent = 0
        self.entity_chunks_deleted = 0
        self.poi_chunks_deleted = 0
        # Region coordinates something was removed from. One (rx, rz) covers
        # a terrain, an entity and a POI file, so this counts places on the
        # map, not files on disk.
        self.regions_touched = 0
        # The chunk-aligned rectangle cleared, ((x0, z0), (x1, z1)).
        self.deleted_box: tuple[tuple[int, int], tuple[int, int]] | None = None

    def __repr__(self) -> str:
        parts = [f"{self.chunks_deleted} chunks"]
        if self.chunks_absent:
            parts.append(f"{self.chunks_absent} already absent")
        if self.entity_chunks_deleted:
            parts.append(f"{self.entity_chunks_deleted} entity")
        if self.poi_chunks_deleted:
            parts.append(f"{self.poi_chunks_deleted} poi")
        parts.append(f"{self.regions_touched} regions")
        return f"<ChunkDeleteResult {', '.join(parts)}>"


class ChunkCreateResult:
    """What a :meth:`MinecraftSave.create_chunks` built, and what it left.

    Only a chunk with no terrain record at all is built. One the world holds
    is left as it stands and named, in one of three lists: finished
    (``existing_chunks``), set aside part-way by the generator
    (``unfinished_chunks``, which the game finishes when it next loads them
    -- :meth:`MinecraftSave.delete_chunks` first to build there afresh), or
    unreadable (``unreadable_chunks``, with why: something is there).

    ``data_version`` is what the built chunks were stamped with, and
    ``min_y``/``max_y`` the height they span. ``complete`` says every chunk
    the box covers is now finished, built or found so.
    """

    __slots__ = (
        "chunks_created",
        "created_chunks",
        "regions_created",
        "existing_chunks",
        "unfinished_chunks",
        "unreadable_chunks",
        "data_version",
        "min_y",
        "max_y",
        "biome",
    )

    def __init__(self):
        self.chunks_created = 0
        self.created_chunks: list[tuple[int, int]] = []
        # Region files the build made, which the world did not have.
        self.regions_created = 0
        self.existing_chunks: list[tuple[int, int]] = []
        self.unfinished_chunks: list[tuple[int, int]] = []
        self.unreadable_chunks: list[tuple[tuple[int, int], str]] = []
        self.data_version: int | None = None
        self.min_y: int | None = None
        self.max_y: int | None = None
        self.biome: str | None = None

    @property
    def complete(self) -> bool:
        return not self.unfinished_chunks and not self.unreadable_chunks

    def __repr__(self) -> str:
        parts = [f"{self.chunks_created} chunks built"]
        if self.regions_created:
            parts.append(f"{self.regions_created} new regions")
        if self.existing_chunks:
            parts.append(f"{len(self.existing_chunks)} already there")
        if self.unfinished_chunks:
            parts.append(f"{len(self.unfinished_chunks)} unfinished, left")
        if self.unreadable_chunks:
            parts.append(f"{len(self.unreadable_chunks)} unreadable, left")
        return f"<ChunkCreateResult {', '.join(parts)}>"


class HistogramResult:
    """What a :meth:`MinecraftSave.histogram` counted, and what it could not.

    ``counts`` maps :class:`BlockState` to occurrences inside the box. The
    accounting mirrors :class:`FillResult`: a census over ground the world
    has never generated, or cannot read, must say so -- a count that quietly
    treated those as zero would look exactly like "nothing there".
    """

    __slots__ = (
        "counts",
        "blocks_counted",
        "chunks_scanned",
        "missing_chunks",
        "unreadable_chunks",
        "blocks_out_of_range",
    )

    def __init__(self):
        self.counts: dict[BlockState, int] = {}
        self.blocks_counted = 0
        self.chunks_scanned = 0
        # Chunks the world has never generated.
        self.missing_chunks: list[tuple[int, int]] = []
        # Chunks that exist but could not be read; separate from missing for
        # the same reason as in FillResult.
        self.unreadable_chunks: list[tuple[tuple[int, int], str]] = []
        self.blocks_out_of_range = 0

    @property
    def complete(self) -> bool:
        """True if every block in the box was counted."""
        return not (
            self.missing_chunks or self.unreadable_chunks or self.blocks_out_of_range
        )

    def top(self, n: int = 10) -> list[tuple[BlockState, int]]:
        """The ``n`` most common states, most common first."""
        return sorted(self.counts.items(), key=lambda kv: -kv[1])[:n]

    def __repr__(self) -> str:
        parts = [
            f"{self.blocks_counted:,} blocks",
            f"{len(self.counts)} states",
            f"{self.chunks_scanned} chunks",
        ]
        if self.missing_chunks:
            parts.append(f"{len(self.missing_chunks)} chunks ungenerated")
        if self.unreadable_chunks:
            parts.append(f"{len(self.unreadable_chunks)} chunks unreadable")
        if self.blocks_out_of_range:
            parts.append(f"{self.blocks_out_of_range:,} blocks out of range")
        return f"<HistogramResult {', '.join(parts)}>"


class LightResetResult:
    """What a :meth:`MinecraftSave.reset_lighting` actually touched.

    Ungenerated chunks inside the area are *not* failures here -- a chunk is
    lit by generation when it is born, so there is nothing stale to clear --
    but they are counted, so a caller can see the area was bigger than the
    world. Unreadable chunks are the real problem: their stale light cannot
    be cleared, and an edit beside one may keep a dark seam at the border.
    """

    __slots__ = (
        "chunks_reset",
        "arrays_removed",
        "chunks_skipped",
        "unreadable_chunks",
    )

    def __init__(self):
        self.chunks_reset = 0
        self.arrays_removed = 0
        # Chunks the world has never generated: skipped, by design.
        self.chunks_skipped = 0
        self.unreadable_chunks: list[tuple[tuple[int, int], str]] = []

    def __repr__(self) -> str:
        parts = [f"{self.chunks_reset} chunks marked"]
        if self.arrays_removed:
            parts.append(f"{self.arrays_removed} stale arrays dropped")
        if self.chunks_skipped:
            parts.append(f"{self.chunks_skipped} ungenerated skipped")
        if self.unreadable_chunks:
            parts.append(f"{len(self.unreadable_chunks)} chunks unreadable")
        return f"<LightResetResult {', '.join(parts)}>"


class HeightmapResult:
    """What a :meth:`MinecraftSave.rebuild_heightmaps` actually rebuilt.

    Same accounting as everywhere else: chunks that were never generated or
    never finished are skipped and listed, chunks that exist but cannot be
    read are listed apart, because their stale heightmaps *remain stale*.
    """

    __slots__ = ("chunks_rebuilt", "missing_chunks", "unfinished_chunks",
                 "unreadable_chunks")

    def __init__(self):
        self.chunks_rebuilt = 0
        self.missing_chunks: list[tuple[int, int]] = []
        self.unfinished_chunks: list[tuple[int, int]] = []
        self.unreadable_chunks: list[tuple[tuple[int, int], str]] = []

    @property
    def complete(self) -> bool:
        return not (self.missing_chunks or self.unfinished_chunks
                    or self.unreadable_chunks)

    def __repr__(self) -> str:
        parts = [f"{self.chunks_rebuilt} chunks rebuilt"]
        if self.missing_chunks:
            parts.append(f"{len(self.missing_chunks)} ungenerated skipped")
        if self.unfinished_chunks:
            parts.append(f"{len(self.unfinished_chunks)} unfinished skipped")
        if self.unreadable_chunks:
            parts.append(f"{len(self.unreadable_chunks)} unreadable")
        return f"<HeightmapResult {', '.join(parts)}>"


class FindResult:
    """Where a block search found its matches, and what it could not see.

    ``matches`` holds ``(x, y, z, state)`` tuples in a fixed order --
    chunk by chunk, then section, then ``y``, ``z``, ``x`` inside it --
    so the same world always yields the same list and two ports can be
    compared line for line. Sort it if you want world order instead; the
    scan will not pay for an ordering nobody asked for.

    ``sections_skipped`` counts the sections dismissed on their palette
    alone: a section whose palette holds no wanted name cannot hold the
    block, and is never unpacked. That is where a sweep of a whole
    region gets its speed, and a low number next to a high
    ``sections_scanned`` means the search terms were too broad to prune.
    """

    __slots__ = (
        "matches",
        "counts",
        "truncated",
        "chunks_scanned",
        "sections_scanned",
        "sections_skipped",
        "missing_chunks",
        "unreadable_chunks",
    )

    def __init__(self):
        self.matches: list[tuple[int, int, int, BlockState]] = []
        self.counts: dict[BlockState, int] = {}
        self.truncated = False
        self.chunks_scanned = 0
        self.sections_scanned = 0
        self.sections_skipped = 0
        self.missing_chunks: list[tuple[int, int]] = []
        self.unreadable_chunks: list[tuple[tuple[int, int], str]] = []

    @property
    def complete(self) -> bool:
        """True if the whole box was searched and nothing was withheld.

        A search stopped by ``limit`` is not complete -- there may be
        more out there, and a caller that treats a truncated answer as
        exhaustive draws the wrong conclusion from it.
        """
        return not (
            self.missing_chunks or self.unreadable_chunks or self.truncated
        )

    def positions(self) -> list[tuple[int, int, int]]:
        """Just the coordinates, in the same order as :attr:`matches`."""
        return [(x, y, z) for x, y, z, _ in self.matches]

    def top(self, n: int = 10) -> list[tuple[BlockState, int]]:
        """The ``n`` most common matched states, most common first.

        Equal counts are broken by name, so this answers the same way
        twice and the C# twin can answer it identically -- Python's sort
        is stable and .NET's is not, and a summary that depends on which
        is which is a summary you cannot compare across the port.
        """
        return sorted(self.counts.items(),
                      key=lambda kv: (-kv[1], str(kv[0])))[:n]

    def __len__(self) -> int:
        return len(self.matches)

    def __repr__(self) -> str:
        parts = [
            f"{len(self.matches):,} matches",
            f"{len(self.counts)} states",
            f"{self.sections_scanned} sections read",
            f"{self.sections_skipped} skipped by palette",
        ]
        if self.truncated:
            parts.append("truncated at limit")
        if self.missing_chunks:
            parts.append(f"{len(self.missing_chunks)} chunks ungenerated")
        if self.unreadable_chunks:
            parts.append(f"{len(self.unreadable_chunks)} chunks unreadable")
        return f"<FindResult {', '.join(parts)}>"


class EntityQueryResult:
    """Entities standing inside a box, and the storage that failed to read.

    ``entities`` holds ``(identity, x, y, z, tag)`` -- the exact double
    coordinates the game stores, and the whole compound, so callers can
    reach ``VillagerData``, ``CustomName`` or anything else without a
    second pass. Order is chunk by chunk, then the order the chunk
    stores them in, which is the order the game will tick them.

    Chunks holding no entity storage are **not** a partial failure:
    absent storage means no entities, a complete answer. Only storage
    that exists and cannot be read lands in ``unreadable_chunks``, and
    only that makes the result incomplete.
    """

    __slots__ = (
        "entities",
        "counts",
        "truncated",
        "chunks_scanned",
        "chunks_without_storage",
        "unreadable_chunks",
    )

    def __init__(self):
        self.entities: list[tuple[str, float, float, float, object]] = []
        self.counts: dict[str, int] = {}
        self.truncated = False
        self.chunks_scanned = 0
        self.chunks_without_storage = 0
        self.unreadable_chunks: list[tuple[tuple[int, int], str]] = []

    @property
    def complete(self) -> bool:
        return not (self.unreadable_chunks or self.truncated)

    def positions(self) -> list[tuple[float, float, float]]:
        """Just the coordinates, in the same order as :attr:`entities`."""
        return [(x, y, z) for _, x, y, z, _ in self.entities]

    def top(self, n: int = 10) -> list[tuple[str, int]]:
        """The ``n`` most common entity ids, most common first.

        Ties broken by id, for the reason :meth:`FindResult.top` gives.
        """
        return sorted(self.counts.items(),
                      key=lambda kv: (-kv[1], kv[0]))[:n]

    def __len__(self) -> int:
        return len(self.entities)

    def __repr__(self) -> str:
        parts = [
            f"{len(self.entities):,} entities",
            f"{len(self.counts)} kinds",
            f"{self.chunks_scanned} chunks",
        ]
        if self.chunks_without_storage:
            parts.append(f"{self.chunks_without_storage} with no storage")
        if self.truncated:
            parts.append("truncated at limit")
        if self.unreadable_chunks:
            parts.append(f"{len(self.unreadable_chunks)} unreadable")
        return f"<EntityQueryResult {', '.join(parts)}>"
