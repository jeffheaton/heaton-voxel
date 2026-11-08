"""Result objects for schematic export and import, with the same honest
accounting as every other whole-save operation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schematic import Schematic

__all__ = ["SchemExportResult", "SchemImportResult"]


class SchemExportResult:
    """What an export actually captured.

    Mirrors :class:`~heaton_voxel.world.FillResult`'s accounting: chunks the
    world never generated and chunks that exist but cannot be read are listed
    separately, because "nothing there" and "something there I cannot see"
    must never look alike. With the default ``allow_incomplete=False`` an
    export refuses instead, so these lists only fill in when the caller opted
    into air-filling the gaps.
    """

    __slots__ = (
        "schematic",
        "path",
        "blocks_exported",
        "blocks_out_of_range",
        "block_entities_exported",
        "entities_exported",
        "biome_cells_absent",
        "chunks_read",
        "missing_chunks",
        "unreadable_chunks",
    )

    def __init__(self, schematic: "Schematic"):
        self.schematic = schematic
        self.path: str | None = None
        # Cells copied from stored sections.
        self.blocks_exported = 0
        # Cells written as air because the world holds nothing there: above
        # or below the stored sections, or (with allow_incomplete) inside a
        # missing or unreadable chunk.
        self.blocks_out_of_range = 0
        self.block_entities_exported = 0
        self.entities_exported = 0
        # Biome cells air-filled with a placeholder for lack of stored data.
        self.biome_cells_absent = 0
        self.chunks_read = 0
        self.missing_chunks: list[tuple[int, int]] = []
        self.unreadable_chunks: list[tuple[tuple[int, int], str]] = []

    @property
    def complete(self) -> bool:
        """True if every cell in the box came from real stored data."""
        return not (
            self.missing_chunks
            or self.unreadable_chunks
            or self.blocks_out_of_range
            or self.biome_cells_absent
        )

    def __repr__(self) -> str:
        parts = [
            f"{self.blocks_exported:,} blocks",
            f"{self.block_entities_exported} block entities",
        ]
        if self.entities_exported:
            parts.append(f"{self.entities_exported} entities")
        if self.blocks_out_of_range:
            parts.append(f"{self.blocks_out_of_range:,} cells air-filled")
        if self.missing_chunks:
            parts.append(f"{len(self.missing_chunks)} chunks ungenerated")
        if self.unreadable_chunks:
            parts.append(f"{len(self.unreadable_chunks)} chunks unreadable")
        return f"<SchemExportResult {', '.join(parts)}>"


class SchemImportResult:
    """What a paste actually changed, and what it could not.

    The same not-all-or-nothing accounting as
    :class:`~heaton_voxel.world.FillResult`: a paste reaching into chunks the
    world never generated skips them and says so, rather than inventing
    terrain or failing wholesale.
    """

    __slots__ = (
        "placed_box",
        "blocks_pasted",
        "air_skipped",
        "voids_skipped",
        "sections_replaced",
        "chunks_modified",
        "heightmaps_rebuilt",
        "missing_chunks",
        "unfinished_chunks",
        "unreadable_chunks",
        "blocks_out_of_range",
        "block_entities_added",
        "block_entities_removed",
        "entities_added",
        "entities_skipped",
        "biome_cells_set",
        "biome_cells_out_of_range",
        "lighting",
        "transform_warnings",
    )

    def __init__(self):
        # The world-coordinate box the schematic occupies, inclusive.
        self.placed_box: tuple[tuple[int, int, int], tuple[int, int, int]] | None = None
        self.blocks_pasted = 0
        # Cells not written because paste_air=False and the schematic holds
        # air there.
        self.air_skipped = 0
        # structure_void cells left as the world has them (paste_voids=False)
        self.voids_skipped = 0
        self.sections_replaced = 0
        self.chunks_modified = 0
        # Chunks whose blocks moved and whose four maps were rewritten.
        self.heightmaps_rebuilt = 0
        # Chunks the world has never generated: skipped, never created.
        self.missing_chunks: list[tuple[int, int]] = []
        # Chunks the game has not finished generating: skipped, because it
        # generates over whatever is written into them.
        self.unfinished_chunks: list[tuple[int, int]] = []
        # Chunks that exist but could not be read; separate from missing for
        # the same reason as in FillResult.
        self.unreadable_chunks: list[tuple[tuple[int, int], str]] = []
        self.blocks_out_of_range = 0
        self.block_entities_added = 0
        # Stale block entities removed from overwritten positions -- a chest
        # replaced by stone must not leave its inventory behind in the chunk.
        self.block_entities_removed = 0
        self.entities_added = 0
        # (entity id, world position) of entities that could not be placed:
        # their chunk is ungenerated or unfinished, or its entity storage is
        # unreadable.
        self.entities_skipped: list[tuple[str, tuple[float, float, float]]] = []
        self.biome_cells_set = 0
        self.biome_cells_out_of_range = 0
        # The LightResetResult from the automatic relight, or None if
        # relight=False or nothing was written.
        self.lighting = None
        # From the schematic that was pasted: states whose properties look
        # directional but sit outside the rotate/flip vocabulary. Empty
        # unless the paste involved a transform (or the schematic carried
        # warnings from an earlier one).
        self.transform_warnings: list[str] = []

    @property
    def complete(self) -> bool:
        """True if everything the schematic holds was placed."""
        return not (
            self.missing_chunks
            or self.unfinished_chunks
            or self.unreadable_chunks
            or self.blocks_out_of_range
            or self.entities_skipped
        )

    def __repr__(self) -> str:
        parts = [
            f"{self.blocks_pasted:,} blocks",
            f"{self.chunks_modified} chunks",
        ]
        if self.sections_replaced:
            parts.append(f"{self.sections_replaced} whole sections")
        if self.air_skipped:
            parts.append(f"{self.air_skipped:,} air skipped")
        if self.voids_skipped:
            parts.append(f"{self.voids_skipped:,} voids left")
        if self.block_entities_added or self.block_entities_removed:
            parts.append(
                f"{self.block_entities_added} block entities in, "
                f"{self.block_entities_removed} stale out"
            )
        if self.entities_added:
            parts.append(f"{self.entities_added} entities")
        if self.entities_skipped:
            parts.append(f"{len(self.entities_skipped)} entities skipped")
        if self.missing_chunks:
            parts.append(f"{len(self.missing_chunks)} chunks ungenerated")
        if self.unfinished_chunks:
            parts.append(f"{len(self.unfinished_chunks)} chunks unfinished")
        if self.unreadable_chunks:
            parts.append(f"{len(self.unreadable_chunks)} chunks unreadable")
        if self.blocks_out_of_range:
            parts.append(f"{self.blocks_out_of_range:,} blocks out of range")
        if self.transform_warnings:
            parts.append(f"{len(self.transform_warnings)} transform warnings")
        return f"<SchemImportResult {', '.join(parts)}>"
