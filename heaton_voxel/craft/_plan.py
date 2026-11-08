"""A plan overlay, read the same way by everything that reads one.

A plan is ``{(x, y, z): block}`` -- the blocks a build is about to write --
and the walk check (:class:`~heaton_voxel.craft.survey.StepSpace`), the
light simulation (:func:`~heaton_voxel.craft.light.simulate_block_light`),
the water's checks (:func:`~heaton_voxel.craft.survey.contained`,
:func:`~heaton_voxel.craft.survey.water_body`,
:func:`~heaton_voxel.craft.survey.starved`) and
:meth:`~heaton_voxel.craft.build.Railway.verify` each take one, so a
plan can be checked before a block of it exists. A block may be a name
(``"minecraft:oak_slab[type=top]"``), a :class:`BlockState`, or a
``(name, properties)`` pair. They used to accept different subsets of
those, so one plan could pass one check and crash another.
"""

from __future__ import annotations

from ..blocks import BlockState

__all__ = ["as_state", "read_overlay", "refuse_many"]


def as_state(block) -> BlockState:
    """A plan entry as a :class:`BlockState`. A
    :class:`~heaton_voxel.craft.style.Mix` or a
    :class:`~heaton_voxel.craft.style.Material` is many blocks, not one,
    and is a ``TypeError`` saying which (:func:`refuse_many`)."""
    if isinstance(block, BlockState):
        return block
    if isinstance(block, str):
        return BlockState.parse(block)
    refuse_many(block)
    name, properties = block
    return BlockState(name, dict(properties or {}))


def refuse_many(block) -> None:
    """Raise ``TypeError`` if ``block`` is a
    :class:`~heaton_voxel.craft.style.Mix` or a
    :class:`~heaton_voxel.craft.style.Material`: where one exact state is
    wanted, either would otherwise fail somewhere deep with a message about
    unpacking or parsing that names neither.

    Known by the ``_craft_style`` each class carries, not by its type:
    ``craft.style`` reads :mod:`~heaton_voxel.craft.blockinfo`, which reads
    this module, so importing it here would be a cycle.
    """
    kind = getattr(type(block), "_craft_style", None)
    if kind == "Mix":
        raise TypeError(
            f"{block!r} is a Mix, many blocks picked by position, not one "
            f"state: where one exact state is wanted, pick one with "
            f".pick(x, y, z) or name the block. A Builder's set, fill, "
            f"level_pad, road and pave take a Mix whole, and pick it at "
            f"every cell they write")
    if kind == "Material":
        raise TypeError(
            f"{block!r} is a Material, a family of blocks, not one state: "
            f"pass one of its forms, .form(...) with one of "
            f"{', '.join(block.forms)}")


def read_overlay(overlay) -> dict[tuple[int, int, int], BlockState]:
    """``overlay`` with integer cells and every block a :class:`BlockState`."""
    return {(int(cell[0]), int(cell[1]), int(cell[2])): as_state(block)
            for cell, block in (overlay or {}).items()}
