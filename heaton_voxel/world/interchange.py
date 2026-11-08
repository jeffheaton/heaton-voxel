"""Schematic interchange, delegated to :mod:`heaton_voxel.schem`.

A mixin of :class:`~heaton_voxel.world.MinecraftSave` -- one file per
concern, mirroring the C# port's partial classes (see PORTING.md).
"""

from __future__ import annotations

import os

from ._state import SaveState


class InterchangeOps(SaveState):
    """``.schem`` export and import, as save methods."""

    def export_schem(
        self,
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
    ):
        """Copy the box between two corners into a Sponge ``.schem`` file.

        The corners are inclusive global block coordinates in any order, the
        same box a :meth:`fill` takes. Block entities inside it (chest
        contents, sign text, spawner settings) travel with their blocks;
        ``entities=True`` also copies mobs, item frames and armor stands.
        See :meth:`heaton_voxel.schem.Schematic.from_save` for every option
        and the exact rules; this both builds the schematic and writes it.

        Returns a :class:`heaton_voxel.schem.SchemExportResult`, whose
        ``.schematic`` is the in-memory copy.
        """
        from ..schem import export_schem

        return export_schem(
            self,
            corner1,
            corner2,
            path,
            origin=origin,
            entities=entities,
            biomes=biomes,
            version=version,
            dimension=dimension,
            allow_incomplete=allow_incomplete,
            keep_loaded=keep_loaded,
        )

    def import_schem(
        self,
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
    ):
        """Paste a ``.schem``, a structure ``.nbt`` or a ``.litematic`` file
        (or a loaded ``Schematic``) into the world.

        ``at`` is where the schematic's *origin* lands -- for a WorldEdit
        file, stand at ``at`` and the build appears exactly as it would from
        ``//paste``. Pass ``anchor="min"`` to put the schematic's minimum
        corner at ``at`` instead, ignoring any stored offset. ``rotate``
        (multiples of 90 degrees, clockwise from above) and ``flip``
        (``"x"``, ``"y"`` or ``"z"``; applied before the rotation) transform
        the copy on the way in, block states, block entities and entities
        alike. Nothing reaches disk until :meth:`save`. See
        :meth:`heaton_voxel.schem.Schematic.paste` for every option and the
        exact rules.

        Returns a :class:`heaton_voxel.schem.SchemImportResult`.
        """
        from ..schem import import_schem

        return import_schem(
            self,
            source,
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
