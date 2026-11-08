"""How the judged tier says no.

Every error here is a :class:`~heaton_voxel.nbt.NBTError`, so code that
catches the library's one exception keeps working. They exist so a caller
can catch a *refusal* without also catching *corruption*: the format layer
raises ``NBTError`` when a file is damaged, and an ``except NBTError``
wrapped around a judged call quietly turns a broken chunk into "nothing
there" -- exactly what the repo's rule 6 forbids. Catch :class:`NoSurface`,
:class:`IllegalBlock` or :class:`Refused`; let a plain ``NBTError`` through.
"""

from __future__ import annotations

from ..nbt import NBTError

__all__ = ["IllegalBlock", "NoSurface", "Refused"]


class Refused(NBTError):
    """A judged call declined to write, before writing anything.

    The message names the first fault and how many there were;
    :attr:`faults` holds all of them, each as the call found it, so a
    session can fix every one in a single pass instead of rerunning to
    learn the next.
    """

    def __init__(self, message: str, faults=()):
        super().__init__(message)
        #: Every reason, not just the one the message names.
        self.faults = list(faults)


class NoSurface(Refused):
    """No ground to stand on at a column.

    :attr:`reason` says why, where the raiser knows, in the words
    :attr:`~heaton_voxel.craft.survey.SurveyMap.missing` uses:
    ``"ungenerated"`` (no chunk there), ``"partial"`` (a chunk the
    generator set aside part-way, with no surface yet) or ``"empty"`` (a
    column with nothing in it to stand on); :attr:`cell` is the column's
    ``(x, z)``. :meth:`~heaton_voxel.craft.build.Builder.probe`,
    :meth:`~heaton_voxel.craft.build.Builder.ground` and the writers that
    read the ground through them give both. A chunk that cannot be read is
    corruption, not absence: the column reader
    (:func:`~heaton_voxel.craft.survey.walk_surface`, and the Builder over
    it) raises a plain :class:`~heaton_voxel.nbt.NBTError` for one, as it
    does for a heightmap that contradicts its own chunk.
    """

    def __init__(self, message: str, faults=(), *, reason: str | None = None,
                 cell=None):
        super().__init__(message, faults)
        #: Why there is no ground: ``"ungenerated"``, ``"partial"`` or
        #: ``"empty"``; None when the raiser did not tell them apart.
        self.reason = reason
        #: The column, ``(x, z)``, or None.
        self.cell = cell


class IllegalBlock(Refused):
    """A block the game does not have, or a property or a value it does not
    take (:func:`~heaton_voxel.craft.blockinfo.check_state`).

    The game refuses neither. It loads an id it does not know as air, and a
    property or value it does not take as the block's default, and the only
    trace is a line in a log nobody reads -- so a write of one is gone the
    moment the game loads the chunk, while every render still draws it.
    """

    def __init__(self, message: str, faults=(), *, suggestions=()):
        super().__init__(message, faults)
        #: The real ids nearest the one refused, best first: what it was
        #: renamed to, if it was; otherwise the closest spellings.
        self.suggestions = tuple(suggestions)
