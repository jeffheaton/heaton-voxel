"""The :class:`Schematic` itself: a box of blocks with no world attached,
its ``.schem`` reader and writer, and its rotate/flip transforms."""

from __future__ import annotations

import gzip
import os
import zlib
from typing import TYPE_CHECKING

from ..blocks import MIN_DATA_VERSION, BlockState
from ..nbt import (
    TAG_COMPOUND,
    TAG_DOUBLE,
    NBTError,
    TagByteArray,
    TagCompound,
    TagDouble,
    TagInt,
    TagIntArray,
    TagList,
    TagShort,
    TagString,
    atomic_write,
    decode,
    encode,
)
from .support import (
    _MAX_EDGE,
    _copy_tag,
    _read_varints,
    _unsigned_short,
    _write_varints,
)
from .litematic import is_litematic, litematic_root, read_litematic
from .structure import is_structure, read_structure, structure_root
if TYPE_CHECKING:
    from .results import SchemImportResult
from .transforms import (
    _FACING2_CW,
    _FACING3_CW,
    _FACING2_MIRROR,
    _FACING3_MIRROR,
    _compose_byte_table,
    _directional_leftovers,
    _map_state,
    _properties_mirrored,
    _properties_rotated_cw,
    _transform_entity,
)

class Schematic:
    """A box of blocks with no world attached.

    Coordinates are relative to the minimum corner: ``x`` runs 0..width-1
    (east), ``y`` 0..height-1 (up), ``z`` 0..length-1 (south). Storage is a
    palette of :class:`BlockState` plus one index per cell, which is also how
    the file encodes it -- memory is roughly 8 bytes per cell, so a
    400x100x400 box costs about 128 MiB.

    Attributes:
        data_version: The Minecraft data version the block states belong to.
            Assignable, for the caller who knows better than the check.
        offset: ``(x, y, z)`` of the minimum corner relative to the author's
            copy origin -- the file's ``Offset``. ``(0, 0, 0)`` unless the
            exporter passed an ``origin``.
        block_entities: ``{(x, y, z): TagCompound}`` -- the block entity's
            NBT as the chunk stores it, minus its position and ``keepPacked``
            (both re-derived on paste); ``"id"`` stays inside.
        entities: List of entity compounds exactly as entity storage holds
            them -- ``id``, ``UUID`` and all -- except that ``Pos`` (and the
            other stored positions, see :func:`_shift_entity`) are relative
            to the minimum corner.
        metadata: The file's ``Metadata`` compound, carried through, or
            ``None``. WorldEdit's ``WEOffsetX/Y/Z`` live in :attr:`offset`
            instead.
        source_version: Container version the file was loaded from (2 or 3
            for a ``.schem``, Litematica's own for a ``.litematic``), or
            ``None`` for one built in memory or a structure file.
        source_format: ``"sponge"``, ``"structure"`` or ``"litematic"``: the
            format the file was loaded from, or ``None`` for one built in
            memory.
        source_palettes: How many palettes a structure file held; the
            first is the one read (see :mod:`heaton_voxel.schem.structure`).
            ``None`` for every other source.
        had_2d_biomes: True if a loaded version-2 file carried the old
            column-based biome data, which this module does not apply; see
            :meth:`load`.
    """

    def __init__(
        self,
        width: int,
        height: int,
        length: int,
        data_version: int,
        *,
        offset: tuple[int, int, int] = (0, 0, 0),
    ):
        for name, value in (("width", width), ("height", height), ("length", length)):
            if not (1 <= int(value) <= _MAX_EDGE):
                raise NBTError(
                    f"schematic {name} must be 1..{_MAX_EDGE}, got {value}"
                )
        if int(data_version) < MIN_DATA_VERSION:
            raise NBTError(
                f"schematic DataVersion {data_version} predates the current "
                f"format (needs {MIN_DATA_VERSION}, Minecraft 1.18); its block "
                f"names would not mean the same thing in a current world."
            )
        self._width = int(width)
        self._height = int(height)
        self._length = int(length)
        self.data_version = int(data_version)
        self.offset = (int(offset[0]), int(offset[1]), int(offset[2]))

        air = BlockState("minecraft:air")
        self._palette: list[BlockState] = [air]
        self._lookup: dict[BlockState, int] = {air: 0}
        self._blocks: list[int] = [0] * (self._width * self._height * self._length)

        self.block_entities: dict[tuple[int, int, int], TagCompound] = {}
        self.entities: list[TagCompound] = []
        # empty while the schematic carries no biomes, which _biomes says
        self._biome_palette: list[str] = []
        self._biome_lookup: dict[str, int] = {}
        self._biomes: list[int] | None = None
        self.metadata: TagCompound | None = None
        self.source_version: int | None = None
        self.source_format: str | None = None
        self.source_palettes: int | None = None
        self.had_2d_biomes = False
        # Set by rotated()/flipped() on their result: states whose properties
        # look directional but sit outside the transform vocabulary.
        self.transform_warnings: list[str] = []

    # -- geometry ----------------------------------------------------------

    @property
    def width(self) -> int:
        """Size along x (east)."""
        return self._width

    @property
    def height(self) -> int:
        """Size along y (up)."""
        return self._height

    @property
    def length(self) -> int:
        """Size along z (south)."""
        return self._length

    @property
    def size(self) -> tuple[int, int, int]:
        return (self._width, self._height, self._length)

    @property
    def volume(self) -> int:
        return self._width * self._height * self._length

    def _index(self, x: int, y: int, z: int) -> int:
        if not (0 <= x < self._width and 0 <= y < self._height and 0 <= z < self._length):
            raise IndexError(
                f"({x}, {y}, {z}) is outside this "
                f"{self._width}x{self._height}x{self._length} schematic"
            )
        # The format's order: y, then z, then x.
        return (y * self._length + z) * self._width + x

    # -- blocks ------------------------------------------------------------

    @property
    def palette(self) -> list[BlockState]:
        """The distinct states, in index order. Entry 0 need not be air."""
        return list(self._palette)

    def indices(self) -> list[int]:
        """Every cell's palette index, in the format's y, z, x order.

        The bulk-read path for renderers and previews, mirroring
        :meth:`PalettedContainer.indices`: cell ``(x, y, z)`` lives at
        ``(y * length + z) * width + x``, and the values index
        :attr:`palette`. The list is the live storage -- treat it as
        read-only, since writing to it would bypass palette interning; go
        through :meth:`set_block` to change cells.
        """
        return self._blocks

    def _intern(self, state: BlockState) -> int:
        slot = self._lookup.get(state)
        if slot is None:
            slot = len(self._palette)
            self._palette.append(state)
            self._lookup[state] = slot
        return slot

    def get_block(self, x: int, y: int, z: int) -> BlockState:
        return self._palette[self._blocks[self._index(x, y, z)]]

    def set_block(self, x: int, y: int, z: int, state: BlockState | str) -> None:
        if isinstance(state, str):
            state = BlockState.parse(state)
        self._blocks[self._index(x, y, z)] = self._intern(state)

    def __getitem__(self, position: tuple[int, int, int]) -> BlockState:
        return self.get_block(*position)

    def __setitem__(self, position: tuple[int, int, int], state: BlockState | str) -> None:
        self.set_block(*position, state)

    def histogram(self) -> dict[BlockState, int]:
        """Count of every state in the box, air included."""
        tally: dict[int, int] = {}
        for index in self._blocks:
            tally[index] = tally.get(index, 0) + 1
        return {self._palette[i]: n for i, n in tally.items()}

    # -- block entities ----------------------------------------------------

    def get_block_entity(self, x: int, y: int, z: int) -> TagCompound | None:
        self._index(x, y, z)  # bounds check
        return self.block_entities.get((x, y, z))

    def set_block_entity(
        self, x: int, y: int, z: int, data: TagCompound | None
    ) -> None:
        """Attach block entity NBT to a cell, or remove it with ``None``.

        ``data`` is the chunk-storage shape minus position: it must carry its
        ``"id"`` (``minecraft:chest``, ...), and must not carry ``x``/``y``/
        ``z`` -- those belong to wherever the schematic gets pasted.
        """
        self._index(x, y, z)  # bounds check
        if data is None:
            self.block_entities.pop((x, y, z), None)
            return
        if "id" not in data:
            raise NBTError(
                f"block entity at ({x}, {y}, {z}) has no 'id'; the game could "
                f"not tell what it is"
            )
        self.block_entities[(x, y, z)] = data

    # -- biomes ------------------------------------------------------------

    @property
    def has_biomes(self) -> bool:
        return self._biomes is not None

    def get_biome(self, x: int, y: int, z: int) -> str | None:
        """The stored biome for a cell, or ``None`` if the schematic carries
        no biome data. One entry per block, as version 3 stores it."""
        index = self._index(x, y, z)
        biomes = self._biomes
        if biomes is None:
            return None
        return self._biome_palette[biomes[index]]

    def _intern_biome(self, name: str) -> int:
        slot = self._biome_lookup.get(name)
        if slot is None:
            slot = len(self._biome_palette)
            self._biome_palette.append(name)
            self._biome_lookup[name] = slot
        return slot

    def fill_biomes(self, biome: str) -> None:
        """Give every cell this biome, creating the biome data if absent."""
        self._biome_palette = [str(biome)]
        self._biome_lookup = {str(biome): 0}
        self._biomes = [0] * self.volume

    def set_biome(self, x: int, y: int, z: int, biome: str) -> None:
        """Set one cell's biome.

        A schematic either carries a biome for every cell or none at all --
        the format has no "unset" -- so the first ever ``set_biome`` fills
        the whole box with the given biome and goes from there.
        """
        index = self._index(x, y, z)
        if self._biomes is None:
            self.fill_biomes(biome)
            return
        self._biomes[index] = self._intern_biome(str(biome))

    # -- construction --------------------------------------------------------

    @classmethod
    def new(
        cls,
        width: int,
        height: int,
        length: int,
        data_version: int,
        *,
        fill: BlockState | str = "minecraft:air",
    ) -> "Schematic":
        """An empty schematic to build in memory.

        ``data_version`` is required and deliberately has no default: it
        stamps which Minecraft the block names belong to, and this toolkit
        does not hardwire a game version. Read it off a world
        (``level.root["Data"]["DataVersion"]``) or a chunk.
        """
        schematic = cls(width, height, length, data_version)
        if not (isinstance(fill, str) and fill == "minecraft:air"):
            state = BlockState.parse(fill) if isinstance(fill, str) else fill
            slot = schematic._intern(state)
            schematic._blocks = [slot] * schematic.volume
        return schematic

    @classmethod
    def from_save(
        cls,
        save,
        corner1: tuple[int, int, int],
        corner2: tuple[int, int, int],
        *,
        origin: tuple[int, int, int] | None = None,
        entities: bool = False,
        biomes: bool = False,
        dimension: str | None = None,
        allow_incomplete: bool = False,
        keep_loaded: bool = False,
    ) -> "Schematic":
        """Copy a box out of a save. See :func:`export_schem`, which also
        writes the file and returns the full accounting; this returns just
        the in-memory schematic."""
        from .copy import _copy_out

        schematic, _ = _copy_out(
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
        return schematic

    def paste(
        self,
        save,
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
        """Paste into a save; see :func:`import_schem` for the full rules."""
        from .copy import _paste_in

        return _paste_in(
            save,
            self,
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

    # -- rotation and mirroring --------------------------------------------

    def rotated(self, degrees: int) -> "Schematic":
        """A copy rotated about the vertical axis. The original is untouched.

        ``degrees`` must be a multiple of 90; positive is **clockwise seen
        from above** -- what faced north faces east -- matching WorldEdit's
        ``//rotate``. The rotation is about the schematic's *origin* (the
        stored offset turns with the box), so exporting with ``origin=p``,
        rotating, and pasting at ``p`` swings the build around that point
        exactly as ``//rotate`` + ``//paste`` would.

        Everything turns together: block positions and their direction-bearing
        properties (``facing``, ``axis``, sign ``rotation``, rail and stair
        shapes, per-face booleans like a wall's ``north=tall``), block
        entities, biomes, and entities (position, yaw, ``Motion``, hanging
        anchors, frame and painting facing bytes). Properties the vocabulary
        does not cover pass through unchanged and, if they look directional,
        are listed on the result's ``transform_warnings``.
        """
        if degrees % 90:
            raise NBTError(
                f"rotation must be a multiple of 90 degrees, got {degrees}; "
                f"block states have no in-between orientations"
            )
        quarters = (degrees // 90) % 4
        width, height, length = self.size
        ox, oy, oz = self.offset
        if quarters == 0:
            size = (width, height, length)
            offset = (ox, oy, oz)
            cell = lambda x, y, z: (x, y, z)  # noqa: E731 - tiny local maps
            point = cell
            vector = cell
        elif quarters == 1:
            size = (length, height, width)
            offset = (-oz - length + 1, oy, ox)
            cell = lambda x, y, z: (length - 1 - z, y, x)  # noqa: E731
            point = lambda x, y, z: (length - z, y, x)  # noqa: E731
            vector = lambda x, y, z: (-z, y, x)  # noqa: E731
        elif quarters == 2:
            size = (width, height, length)
            offset = (-ox - width + 1, oy, -oz - length + 1)
            cell = lambda x, y, z: (width - 1 - x, y, length - 1 - z)  # noqa: E731
            point = lambda x, y, z: (width - x, y, length - z)  # noqa: E731
            vector = lambda x, y, z: (-x, y, -z)  # noqa: E731
        else:
            size = (length, height, width)
            offset = (oz, oy, -ox - width + 1)
            cell = lambda x, y, z: (z, y, width - 1 - x)  # noqa: E731
            point = lambda x, y, z: (z, y, width - x)  # noqa: E731
            vector = lambda x, y, z: (z, y, -x)  # noqa: E731

        def mapper(properties: dict) -> dict:
            for _ in range(quarters):
                properties = _properties_rotated_cw(properties)
            return properties

        yaw_delta = 90.0 * quarters
        new = self._transformed(
            size,
            offset,
            cell,
            lambda state: _map_state(state, mapper),
            dict(
                point=point,
                cell=cell,
                yaw=lambda value: value + yaw_delta,
                pitch_sign=1.0,
                facing3=_compose_byte_table(_FACING3_CW, quarters),
                facing2=_compose_byte_table(_FACING2_CW, quarters),
                vector=vector,
            ),
        )
        if quarters:
            new.transform_warnings = _directional_leftovers(self._palette)
        return new

    def flipped(self, axis: str) -> "Schematic":
        """A mirrored copy. The original is untouched.

        ``axis`` names the axis the flip runs along: ``"x"`` swaps east and
        west, ``"z"`` swaps north and south, ``"y"`` turns the build upside
        down (stair and slab halves, floor/ceiling attachments and hanging
        things follow). Chirality flips with either horizontal mirror, so
        stair corners swap left for right, chest pairs swap halves, and door
        hinges change sides. The same vocabulary limits and
        ``transform_warnings`` reporting as :meth:`rotated` apply.
        """
        if axis not in ("x", "y", "z"):
            raise NBTError(f"flip axis must be 'x', 'y' or 'z', got {axis!r}")
        width, height, length = self.size
        ox, oy, oz = self.offset
        if axis == "x":
            offset = (-ox - width + 1, oy, oz)
            cell = lambda x, y, z: (width - 1 - x, y, z)  # noqa: E731
            point = lambda x, y, z: (width - x, y, z)  # noqa: E731
            vector = lambda x, y, z: (-x, y, z)  # noqa: E731
            yaw = lambda value: -value  # noqa: E731
            pitch_sign = 1.0
        elif axis == "z":
            offset = (ox, oy, -oz - length + 1)
            cell = lambda x, y, z: (x, y, length - 1 - z)  # noqa: E731
            point = lambda x, y, z: (x, y, length - z)  # noqa: E731
            vector = lambda x, y, z: (x, y, -z)  # noqa: E731
            yaw = lambda value: 180.0 - value  # noqa: E731
            pitch_sign = 1.0
        else:
            offset = (ox, -oy - height + 1, oz)
            cell = lambda x, y, z: (x, height - 1 - y, z)  # noqa: E731
            point = lambda x, y, z: (x, height - y, z)  # noqa: E731
            vector = lambda x, y, z: (x, -y, z)  # noqa: E731
            yaw = lambda value: value  # noqa: E731
            pitch_sign = -1.0

        new = self._transformed(
            (width, height, length),
            offset,
            cell,
            lambda state: _map_state(
                state, lambda properties: _properties_mirrored(properties, axis)
            ),
            dict(
                point=point,
                cell=cell,
                yaw=yaw,
                pitch_sign=pitch_sign,
                facing3=_FACING3_MIRROR[axis],
                facing2=_FACING2_MIRROR[axis],
                vector=vector,
            ),
        )
        new.transform_warnings = _directional_leftovers(self._palette)
        return new

    def _transformed(self, size, offset, cell, map_state, entity_op) -> "Schematic":
        """Rebuild every store of this schematic through a coordinate map."""
        new = Schematic(size[0], size[1], size[2], self.data_version, offset=offset)
        new.source_version = self.source_version
        new.had_2d_biomes = self.had_2d_biomes
        if self.metadata:
            new.metadata = _copy_tag(self.metadata)

        new._palette = []
        new._lookup = {}
        index_map = [new._intern(map_state(state)) for state in self._palette]

        width, height, length = self.size
        new_width, _, new_length = size
        blocks = self._blocks
        out = new._blocks
        biomes = self._biomes
        biomes_out = None
        if biomes is not None:
            new._biome_palette = list(self._biome_palette)
            new._biome_lookup = dict(self._biome_lookup)
            new._biomes = [0] * len(biomes)
            biomes_out = new._biomes

        source = 0
        for y in range(height):
            for z in range(length):
                for x in range(width):
                    nx, ny, nz = cell(x, y, z)
                    target = (ny * new_length + nz) * new_width + nx
                    out[target] = index_map[blocks[source]]
                    if biomes_out is not None and biomes is not None:
                        biomes_out[target] = biomes[source]
                    source += 1

        for (x, y, z), data in self.block_entities.items():
            new.block_entities[cell(x, y, z)] = _copy_tag(data)
        for entity in self.entities:
            copy = _copy_tag(entity)
            _transform_entity(copy, **entity_op)
            new.entities.append(copy)
        return new

    # -- files -------------------------------------------------------------

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Schematic":
        """Read a schematic: a Sponge ``.schem`` (container version 2 or 3),
        a vanilla structure ``.nbt``
        (:mod:`~heaton_voxel.schem.structure`), or a Litematica
        ``.litematic`` (:mod:`~heaton_voxel.schem.litematic`), told apart by
        what the file holds rather than by its name. The rest of this
        describes the ``.schem``.

        Refusals, each with its reason: the pre-1.13 ``.schematic`` layout
        (numeric block ids -- a different format, not an older version of
        this one), Sponge version 1 (predates the block states this toolkit
        supports), any file without a ``DataVersion``, and any
        ``DataVersion`` older than 1.18.

        A version-2 file's column biome data (``BiomeData``) is *noted* --
        :attr:`had_2d_biomes` -- but not carried: it stores one biome per
        column where the game now wants one per 4x4x4 cell, and inventing
        the vertical dimension would be a guess. Version 3 biome data is
        carried in full.
        """
        path = os.fspath(path)
        with open(path, "rb") as handle:
            blob = handle.read()
        if blob[:2] == b"\x1f\x8b":
            payload = gzip.decompress(blob)
        elif blob[:1] == b"\x0a":
            payload = blob
        else:
            try:
                payload = zlib.decompress(blob)
            except zlib.error:
                raise NBTError(
                    f"{path!r} is not gzip, zlib or bare NBT; not a .schem file"
                ) from None
        root_name, root = decode(payload)
        if is_structure(root):
            return read_structure(cls, root, path)
        if is_litematic(root):
            return read_litematic(cls, root, path)

        body = root.get("Schematic")
        if not isinstance(body, TagCompound):
            body = root
        if "Version" not in body:
            if isinstance(body.get("Blocks"), TagByteArray) and "Materials" in body:
                raise NBTError(
                    f"{path!r} is a legacy pre-1.13 .schematic (numeric block "
                    f"ids), which is a different format, not an old version of "
                    f".schem. Convert it with WorldEdit first."
                )
            raise NBTError(f"{path!r} has no Version tag; not a Sponge schematic")
        version = int(body["Version"])
        if version == 1:
            raise NBTError(
                f"{path!r} is Sponge schematic version 1, which predates the "
                f"block content this toolkit supports (Minecraft 1.18)."
            )
        if version not in (2, 3):
            raise NBTError(f"{path!r} is Sponge schematic version {version}; only 2 and 3 are known here")

        if "DataVersion" not in body:
            raise NBTError(
                f"{path!r} has no DataVersion, so there is no telling which "
                f"Minecraft its block names belong to; refusing to guess."
            )

        width = int(body.get("Width", TagShort(0))) & 0xFFFF
        height = int(body.get("Height", TagShort(0))) & 0xFFFF
        length = int(body.get("Length", TagShort(0))) & 0xFFFF

        schematic = cls(width, height, length, int(body["DataVersion"]))
        schematic.source_version = version
        schematic.source_format = "sponge"

        if version == 2:
            schematic._load_v2(body, path)
        else:
            schematic._load_v3(body, path)
        return schematic

    def _load_palette(self, palette_tag, volume_tag, what: str) -> None:
        if palette_tag is None or volume_tag is None:
            raise NBTError(f"{what}: no palette or block data")
        entries: list[BlockState | None] = [None] * len(palette_tag)
        for text, index in palette_tag.items():
            index = int(index)
            if not (0 <= index < len(entries)) or entries[index] is not None:
                raise NBTError(
                    f"{what}: palette index {index} for {text!r} is out of "
                    f"range or duplicated"
                )
            try:
                entries[index] = BlockState.parse(str(text))
            except ValueError as exc:
                raise NBTError(f"{what}: bad palette entry {text!r}: {exc}") from None
        self._palette = entries  # type: ignore[assignment]  # dense by the check above
        self._lookup = {state: i for i, state in enumerate(self._palette)}
        values = _read_varints(volume_tag, self.volume, f"{what}: block data")
        top = len(self._palette)
        for value in values:
            if value >= top:
                raise NBTError(
                    f"{what}: block data refers to palette entry {value} of {top}"
                )
        self._blocks = values

    def _load_block_entity(self, entry: TagCompound, what: str) -> None:
        pos = entry.get("Pos")
        if pos is None or len(pos) != 3:
            raise NBTError(f"{what}: block entity without a 3-int Pos")
        x, y, z = (int(v) for v in pos)
        self._index(x, y, z)  # bounds check: outside the box is corruption
        nested = entry.get("Data")
        if isinstance(nested, TagCompound):  # version 3 nests the payload
            data = _copy_tag(nested)
        else:
            data = TagCompound(
                {
                    key: _copy_tag(value)
                    for key, value in entry.items()
                    if key not in ("Pos", "Id", "Data")
                }
            )
        identity = entry.get("Id") or data.get("id")
        if identity is None:
            raise NBTError(f"{what}: block entity at ({x}, {y}, {z}) has no Id")
        data["id"] = TagString(str(identity))
        self.block_entities[(x, y, z)] = data

    def _load_entity(self, entry: TagCompound, what: str) -> None:
        pos = entry.get("Pos")
        if pos is None or len(pos) != 3:
            raise NBTError(f"{what}: entity without a 3-double Pos")
        nested = entry.get("Data")
        if isinstance(nested, TagCompound):  # version 3 nests the payload
            data = _copy_tag(nested)
        else:
            data = TagCompound(
                {
                    key: _copy_tag(value)
                    for key, value in entry.items()
                    if key not in ("Id", "Data")
                }
            )
        identity = entry.get("Id") or data.get("id")
        if identity is None:
            raise NBTError(f"{what}: entity at {tuple(pos)} has no Id")
        data["id"] = TagString(str(identity))
        data["Pos"] = TagList(
            [TagDouble(float(v)) for v in pos], element_type=TAG_DOUBLE
        )
        self.entities.append(data)

    def _load_v2(self, body: TagCompound, path: str) -> None:
        what = os.path.basename(path)
        self._load_palette(body.get("Palette"), body.get("BlockData"), what)
        for entry in body.get("BlockEntities") or ():
            self._load_block_entity(entry, what)
        for entry in body.get("Entities") or ():
            self._load_entity(entry, what)
        metadata = body.get("Metadata")
        if isinstance(metadata, TagCompound):
            kept = _copy_tag(metadata)
            # WorldEdit keeps the copy origin here in version 2; it is this
            # module's `offset`, not opaque metadata.
            offsets = [kept.pop(f"WEOffset{axis}", None) for axis in "XYZ"]
            if all(value is not None for value in offsets):
                self.offset = tuple(int(v) for v in offsets)  # type: ignore[assignment]
            if kept:
                self.metadata = kept
        # Version 2's `Offset` is written as absolute world coordinates by
        # WorldEdit, against the spec's wording; nothing sane can be done
        # with it on a machine that was not there, so it is ignored -- as
        # WorldEdit's own reader ignores it.
        if body.get("BiomeData") is not None:
            self.had_2d_biomes = True

    def _load_v3(self, body: TagCompound, path: str) -> None:
        what = os.path.basename(path)
        blocks = body.get("Blocks")
        if not isinstance(blocks, TagCompound):
            raise NBTError(f"{what}: version 3 file has no Blocks container")
        self._load_palette(blocks.get("Palette"), blocks.get("Data"), what)
        for entry in blocks.get("BlockEntities") or ():
            self._load_block_entity(entry, what)
        for entry in body.get("Entities") or ():
            self._load_entity(entry, what)
        offset = body.get("Offset")
        if offset is not None and len(offset) == 3:
            self.offset = (int(offset[0]), int(offset[1]), int(offset[2]))
        metadata = body.get("Metadata")
        if isinstance(metadata, TagCompound) and metadata:
            self.metadata = _copy_tag(metadata)
        biomes = body.get("Biomes")
        if isinstance(biomes, TagCompound):
            palette_tag = biomes.get("Palette")
            data_tag = biomes.get("Data")
            if palette_tag is None or data_tag is None:
                raise NBTError(f"{what}: Biomes container missing Palette or Data")
            entries: list[str | None] = [None] * len(palette_tag)
            for text, index in palette_tag.items():
                index = int(index)
                if not (0 <= index < len(entries)) or entries[index] is not None:
                    raise NBTError(
                        f"{what}: biome palette index {index} for {text!r} is "
                        f"out of range or duplicated"
                    )
                entries[index] = str(text)
            values = _read_varints(data_tag, self.volume, f"{what}: biome data")
            top = len(entries)
            for value in values:
                if value >= top:
                    raise NBTError(
                        f"{what}: biome data refers to palette entry {value} of {top}"
                    )
            self._biome_palette = entries  # type: ignore[assignment]
            self._biome_lookup = {name: i for i, name in enumerate(entries)}  # type: ignore[misc]
            self._biomes = values

    def save(
        self, path: str | os.PathLike, *, version: int = 3, format: str | None = None
    ) -> None:
        """Write a schematic file, gzip-compressed, atomically.

        ``format`` is ``"sponge"``, ``"structure"`` or ``"litematic"``, and
        unless given is taken from the file's extension: ``.nbt`` a vanilla
        structure, ``.litematic`` a Litematica schematic, anything else a
        Sponge ``.schem``. A structure and a litematic carry no biomes, so
        saving one from a schematic that holds them refuses rather than drop
        them.

        For a ``.schem``, ``version=3`` (the default) is the current Sponge
        spec, what WorldEdit 7.3+ writes; ``version=2`` is readable by older
        tooling but cannot hold biome data either.
        """
        if format is None:
            suffix = os.path.splitext(os.fspath(path))[1].lower()
            format = {".nbt": "structure", ".litematic": "litematic"}.get(suffix, "sponge")
        if format in ("structure", "litematic"):
            if self.has_biomes:
                raise NBTError(
                    f"a {format} file has nowhere to keep biomes; save as a "
                    f".schem (version 3), or drop the biomes")
            root = structure_root(self) if format == "structure" else litematic_root(self)
            atomic_write(os.fspath(path), gzip.compress(encode(root, ""), mtime=0))
            return
        if format != "sponge":
            raise NBTError(f"unknown schematic format {format!r}; use sponge, "
                           f"structure or litematic")
        if version == 3:
            name, root = "", TagCompound({"Schematic": self._build_v3()})
        elif version == 2:
            name, root = "Schematic", self._build_v2()
        else:
            raise NBTError(f"unknown .schem container version {version}; use 2 or 3")
        atomic_write(os.fspath(path), gzip.compress(encode(root, name), mtime=0))

    def _palette_tag(self) -> TagCompound:
        return TagCompound(
            {str(state): TagInt(i) for i, state in enumerate(self._palette)}
        )

    def _common_header(self, version: int) -> TagCompound:
        body = TagCompound()
        body["Version"] = TagInt(version)
        body["DataVersion"] = TagInt(self.data_version)
        body["Width"] = _unsigned_short(self._width, "Width")
        body["Height"] = _unsigned_short(self._height, "Height")
        body["Length"] = _unsigned_short(self._length, "Length")
        return body

    def _block_entity_entries(self, version: int) -> TagList:
        entries = TagList(element_type=TAG_COMPOUND)
        for (x, y, z), data in sorted(self.block_entities.items()):
            identity = TagString(str(data["id"]))
            pos = TagIntArray([x, y, z])
            if version == 3:
                payload = TagCompound(
                    {k: _copy_tag(v) for k, v in data.items() if k != "id"}
                )
                entries.append(
                    TagCompound({"Id": identity, "Pos": pos, "Data": payload})
                )
            else:
                entry = TagCompound(
                    {k: _copy_tag(v) for k, v in data.items() if k != "id"}
                )
                entry["Id"] = identity
                entry["Pos"] = pos
                entries.append(entry)
        return entries

    def _entity_entries(self, version: int) -> TagList:
        entries = TagList(element_type=TAG_COMPOUND)
        for data in self.entities:
            identity = TagString(str(data.get("id", "")))
            if not identity:
                raise NBTError("an entity in this schematic has no 'id'")
            pos = TagList(
                [TagDouble(float(v)) for v in data["Pos"]], element_type=TAG_DOUBLE
            )
            if version == 3:
                payload = TagCompound(
                    {
                        k: _copy_tag(v)
                        for k, v in data.items()
                        if k not in ("id", "Pos")
                    }
                )
                entries.append(
                    TagCompound({"Id": identity, "Pos": pos, "Data": payload})
                )
            else:
                entry = TagCompound(
                    {k: _copy_tag(v) for k, v in data.items() if k != "id"}
                )
                entry["Id"] = identity
                entry["Pos"] = pos
                entries.append(entry)
        return entries

    def _build_v2(self) -> TagCompound:
        if self.has_biomes:
            raise NBTError(
                "version 2 stores one biome per column, not per cell; saving "
                "this schematic's 3D biome data there would flatten it. Save "
                "as version=3, or drop the biomes."
            )
        body = self._common_header(2)
        metadata = _copy_tag(self.metadata) if self.metadata else TagCompound()
        metadata["WEOffsetX"] = TagInt(self.offset[0])
        metadata["WEOffsetY"] = TagInt(self.offset[1])
        metadata["WEOffsetZ"] = TagInt(self.offset[2])
        body["Metadata"] = metadata
        body["Palette"] = self._palette_tag()
        body["PaletteMax"] = TagInt(len(self._palette))
        body["BlockData"] = _write_varints(self._blocks)
        if self.block_entities:
            body["BlockEntities"] = self._block_entity_entries(2)
        if self.entities:
            body["Entities"] = self._entity_entries(2)
        return body

    def _build_v3(self) -> TagCompound:
        body = self._common_header(3)
        body["Offset"] = TagIntArray(list(self.offset))
        if self.metadata:
            body["Metadata"] = _copy_tag(self.metadata)
        blocks = TagCompound()
        blocks["Palette"] = self._palette_tag()
        blocks["Data"] = _write_varints(self._blocks)
        if self.block_entities:
            blocks["BlockEntities"] = self._block_entity_entries(3)
        body["Blocks"] = blocks
        biome_cells = self._biomes
        if biome_cells is not None:
            body["Biomes"] = TagCompound(
                {
                    "Palette": TagCompound(
                        {
                            name: TagInt(i)
                            for i, name in enumerate(self._biome_palette)
                        }
                    ),
                    "Data": _write_varints(biome_cells),
                }
            )
        if self.entities:
            body["Entities"] = self._entity_entries(3)
        return body

    def __repr__(self) -> str:
        parts = [
            f"{self._width}x{self._height}x{self._length}",
            f"{len(self._palette)} states",
        ]
        if self.block_entities:
            parts.append(f"{len(self.block_entities)} block entities")
        if self.entities:
            parts.append(f"{len(self.entities)} entities")
        if self.has_biomes:
            parts.append("biomes")
        return f"<Schematic {' '.join(parts)} DataVersion {self.data_version}>"
