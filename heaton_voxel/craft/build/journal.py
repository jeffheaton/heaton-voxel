"""The write journal: what a Builder wrote, where, and under which stage.

Every write the Builder makes -- a set, each cell of a placement, a fill's
box -- is recorded once it is in the save, and nothing else is: a refused
call journals nothing, and what stood in a cell before is never kept. It is
a record, not an undo, and not a diff: what a build changed is
:meth:`~heaton_voxel.craft.worksite.Worksite.diff`'s to say.
:meth:`~heaton_voxel.craft.build.Builder.finish` scopes the aftercare from
it, chunk by chunk, and :meth:`~heaton_voxel.craft.build.Builder.stage`
names what it counts.

Part of :mod:`heaton_voxel.craft.build`.
"""

from __future__ import annotations

from collections.abc import Iterator

#: One bit per cell of a 16x16x16 section.
_SECTION_BYTES = 512

_WHOLE_SECTION = b"\xff" * _SECTION_BYTES


class Journal:
    """What a Builder wrote, where, under which stage -- never what was
    there before.

    The cells are kept per section a write touched, one bit per cell at
    the section's own index (``ly * 256 + lz * 16 + lx``), so a fill of a
    million blocks costs a few bitmaps rather than a million tuples, and
    every cell is listed once however often it was written. Beside them
    sit the bounding box of the writes in each chunk, which is what
    :meth:`~heaton_voxel.craft.build.Builder.finish` settles, and the
    number of writes under each stage.
    """

    __slots__ = ("stage", "_sections", "_boxes", "_tally", "_total")

    def __init__(self) -> None:
        #: The name writes are counted under, set by
        #: :meth:`~heaton_voxel.craft.build.Builder.stage`; None outside one.
        self.stage: str | None = None
        # (chunk_x, chunk_z, section_y) -> the bitmap of its written cells.
        self._sections: dict[tuple[int, int, int], bytearray] = {}
        # (chunk_x, chunk_z) -> [x0, y0, z0, x1, y1, z1], inclusive, of the
        # writes in that chunk.
        self._boxes: dict[tuple[int, int], list[int]] = {}
        self._tally: dict[str | None, int] = {}
        self._total = 0

    # -- recording -----------------------------------------------------------

    def record(self, x: int, y: int, z: int) -> None:
        """One cell written: a set, or one cell of a placement."""
        # The common write, and so spelled out rather than shared with
        # record_box: it runs once per block a build sets.
        chunk = (x >> 4, z >> 4)
        key = (chunk[0], chunk[1], y >> 4)
        bits = self._sections.get(key)
        if bits is None:
            bits = self._sections[key] = bytearray(_SECTION_BYTES)
        index = ((y & 15) << 8) | ((z & 15) << 4) | (x & 15)
        bits[index >> 3] |= 1 << (index & 7)
        box = self._boxes.get(chunk)
        if box is None:
            self._boxes[chunk] = [x, y, z, x, y, z]
        else:
            if x < box[0]:
                box[0] = x
            elif x > box[3]:
                box[3] = x
            if y < box[1]:
                box[1] = y
            elif y > box[4]:
                box[4] = y
            if z < box[2]:
                box[2] = z
            elif z > box[5]:
                box[5] = z
        stage = self.stage
        self._tally[stage] = self._tally.get(stage, 0) + 1
        self._total += 1

    def record_box(self, corner1, corner2, count: int) -> None:
        """A box written, corners inclusive and in either order, counted as
        ``count`` writes -- what the fill reported filling."""
        (ax, ay, az), (bx, by, bz) = corner1, corner2
        x0, x1 = sorted((int(ax), int(bx)))
        y0, y1 = sorted((int(ay), int(by)))
        z0, z1 = sorted((int(az), int(bz)))
        for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
            cx0, cx1 = max(x0, chunk_x * 16), min(x1, chunk_x * 16 + 15)
            lx0, lx1 = cx0 & 15, cx1 & 15
            row = ((1 << (lx1 - lx0 + 1)) - 1) << lx0
            low, high = row & 0xFF, row >> 8
            for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
                cz0, cz1 = max(z0, chunk_z * 16), min(z1, chunk_z * 16 + 15)
                lz0, lz1 = cz0 & 15, cz1 & 15
                self._bound(chunk_x, chunk_z, cx0, y0, cz0, cx1, y1, cz1)
                square = lx0 == 0 and lx1 == 15 and lz0 == 0 and lz1 == 15
                for section_y in range(y0 >> 4, (y1 >> 4) + 1):
                    ly0 = max(y0, section_y * 16) & 15
                    ly1 = min(y1, section_y * 16 + 15) & 15
                    key = (chunk_x, chunk_z, section_y)
                    if square and ly0 == 0 and ly1 == 15:
                        self._sections[key] = bytearray(_WHOLE_SECTION)
                        continue
                    bits = self._sections.get(key)
                    if bits is None:
                        bits = self._sections[key] = bytearray(_SECTION_BYTES)
                    for ly in range(ly0, ly1 + 1):
                        for lz in range(lz0, lz1 + 1):
                            at = (ly << 5) | (lz << 1)   # the byte of lx=0
                            bits[at] |= low
                            bits[at + 1] |= high
        self._count(count)

    def _bound(self, chunk_x, chunk_z, x0, y0, z0, x1, y1, z1) -> None:
        box = self._boxes.get((chunk_x, chunk_z))
        if box is None:
            self._boxes[(chunk_x, chunk_z)] = [x0, y0, z0, x1, y1, z1]
            return
        box[0] = min(box[0], x0)
        box[1] = min(box[1], y0)
        box[2] = min(box[2], z0)
        box[3] = max(box[3], x1)
        box[4] = max(box[4], y1)
        box[5] = max(box[5], z1)

    def _count(self, count: int) -> None:
        self._tally[self.stage] = self._tally.get(self.stage, 0) + count
        self._total += count

    # -- reading -------------------------------------------------------------

    def tally(self) -> dict[str | None, int]:
        """Writes per stage, in the order the stages first wrote; None is
        what was written outside any stage. A write is counted each time it
        is made, as ``Builder.blocks_set`` always counted it -- a cell set
        twice is two -- and a fill as the blocks it filled."""
        return dict(self._tally)

    @property
    def total(self) -> int:
        """Every write, counted as :meth:`tally` counts them."""
        return self._total

    def written(self) -> Iterator[tuple[int, int, int]]:
        """Every cell the Builder asked for, once each, as ``(x, y, z)``, in
        a total order: by chunk ``(cx, cz)``, then ``y``, ``z``, ``x``.

        It lists what the Builder wrote, not what changed: a cell written
        with the state it already held is listed, and so is one a later
        write took back. Nor does it list what the lower tier re-derived on
        its own -- the fences, walls, panes and stairs beside a placement
        that ``place_block`` settles -- which
        :meth:`~heaton_voxel.craft.build.Builder.finish`'s one-block
        connect margin settles again anyway. What a build actually changed
        is a content question, and the answer is
        :meth:`~heaton_voxel.craft.worksite.Worksite.diff`: every cell whose
        state differs from the worksite's snapshot, whoever wrote it, not
        the journal. A fill is listed cell by cell, as the iteration reaches
        it, so a city-scale build listed whole is millions of tuples.
        """
        sections = self._sections
        for key in sorted(sections):
            chunk_x, chunk_z, section_y = key
            bits = sections[key]
            base_x, base_y, base_z = chunk_x * 16, section_y * 16, chunk_z * 16
            for at, byte in enumerate(bits):
                if not byte:
                    continue
                for bit in range(8):
                    if byte >> bit & 1:
                        index = (at << 3) | bit
                        yield (base_x + (index & 15), base_y + (index >> 8),
                               base_z + ((index >> 4) & 15))

    def chunks(self) -> tuple[tuple[int, int], ...]:
        """Every chunk ``(cx, cz)`` written in, sorted."""
        return tuple(sorted(self._boxes))

    def _wrote_in(self, chunk: tuple[int, int]) -> bool:
        """Whether anything was written in ``chunk`` -- what
        ``chunk in chunks()`` answers, without sorting them."""
        return chunk in self._boxes

    def _chunk_boxes(self) -> dict[tuple[int, int], tuple[int, ...]]:
        """``(cx, cz) -> (x0, y0, z0, x1, y1, z1)``, the inclusive bounding
        box of the writes in each chunk, the chunks sorted."""
        return {key: tuple(self._boxes[key]) for key in sorted(self._boxes)}

    def __bool__(self) -> bool:
        return bool(self._boxes)

    def __repr__(self) -> str:
        stages = ", ".join(f"{'unstaged' if name is None else name} {count:,}"
                           for name, count in self._tally.items())
        return (f"<Journal {self._total:,} writes in {len(self._boxes)} "
                f"chunks{f' ({stages})' if stages else ''}>")
