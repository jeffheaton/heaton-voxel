"""The shell of a building.

Walls, floors and openings, doors and windows included. Roofs are
:mod:`~heaton_voxel.craft.build.roof`'s. The rule-built shell -- a
:class:`~heaton_voxel.craft.build.walls.Facade` over the walls of a
rectangle, stories, floors, a parapet -- is
:mod:`~heaton_voxel.craft.build.walls`' ``shell``; ``frame_walls`` and
``floor`` here are the first town's, kept writing what they always wrote.

Part of :mod:`heaton_voxel.craft.build`. Each writer here plans its
call whole and hands it to the Builder's ``_commit``.
"""

from __future__ import annotations

from ...block_defaults import BLOCK_DEFAULTS
from ...blocks import BlockState
from ...nbt import NBTError
from .._plan import refuse_many
from ..block_facts import facts
from ..errors import Refused
from ..style import FROM_PALETTE
from ..survey import _FACING_OF, _POWER_DOORS, iron_door_opens
from ._common import _COMPASS, _empty, _HEADINGS, _Slot, _state, AIR
from .roof import RoofMixin


#: The four sides a pane or a bar joins to its neighbors by.
_SIDES = tuple(_COMPASS)


class ShellsMixin:
    """:class:`~heaton_voxel.craft.build.Builder` methods: the shell of a building.

    Builder inherits them; nothing here is used on its own.
    """

    # -- shells --------------------------------------------------------------

    def frame_walls(self, x0, z0, x1, z1, y0, height, *,
                    log=FROM_PALETTE, infill=FROM_PALETTE,
                    plinth=FROM_PALETTE) -> None:
        """A rectangular shell: plinth course, log corners, infill walls.

        ``y0`` is the floor level (the plinth row); walls rise ``height - 1``
        above it. Cut doors and windows afterwards.

        The first town's shell, kept as it always wrote, byte for byte,
        with no check of what stands in its way. A building of more than
        this -- stories, piers and bays, bands, windows in every bay, a
        deck and its parapet, checked before it is written -- is
        :meth:`~heaton_voxel.craft.build.walls.WallsMixin.shell`, which
        counts its plinth from the row over its floor.

        Each material left out is the Builder's :attr:`palette`'s: ``log``
        its ``frame`` (stripped spruce, upright, by default), ``infill`` its
        ``wall`` (white terracotta) and ``plinth`` its ``plinth``
        (cobblestone). Each is a block, a
        :class:`~heaton_voxel.craft.style.Material` (its block) or a
        :class:`~heaton_voxel.craft.style.Mix`, each cell its pick.
        """
        log, infill, plinth = self._materials(
            "frame_walls", _Slot("log", log, "frame", mixes=True),
            _Slot("infill", infill, "wall", mixes=True),
            _Slot("plinth", plinth, "plinth", mixes=True))
        ops: list[tuple] = []
        for x in range(x0, x1 + 1):
            for z in range(z0, z1 + 1):
                if x in (x0, x1) or z in (z0, z1):
                    corner = x in (x0, x1) and z in (z0, z1)
                    ops.append(("set", (x, y0, z), plinth))
                    for y in range(y0 + 1, y0 + height):
                        ops.append(("set", (x, y, z), log if corner else infill))
        self._commit(ops, what="frame_walls")

    def floor(self, x0, z0, x1, z1, y, block=FROM_PALETTE) -> None:
        """A floor of ``block`` over the rectangle at ``y``: the Builder's
        :attr:`palette`'s ``floor`` (spruce planks, by default) when it is
        left out, a :class:`~heaton_voxel.craft.style.Material`'s block, or
        a :class:`~heaton_voxel.craft.style.Mix`, swept cell by cell
        (:meth:`fill`). A shell's upper floors, laid round the stairwell a
        flight claims, are
        :meth:`~heaton_voxel.craft.build.walls.WallsMixin.shell`'s
        ``floor=``."""
        block, = self._materials(
            "floor", _Slot("block", block, "floor", mixes=True))
        self.fill((x0, y, z0), (x1, y, z1), block)

    def clear_interior(self, x0, z0, x1, z1, y0, height) -> None:
        self.fill((x0 + 1, y0 + 1, z0 + 1), (x1 - 1, y0 + height, z1 - 1), AIR)

    #: :meth:`~heaton_voxel.craft.build.roof.RoofMixin.gable_roof`, which
    #: lives beside its :class:`~heaton_voxel.craft.build.roof.Roof` now:
    #: the same function, kept here so that whatever resolved it through
    #: the shells still does.
    gable_roof = RoofMixin.gable_roof

    def doorway(self, x, y, z, facing, *, door="minecraft:oak_door",
                button="minecraft:stone_button", hinge="left") -> list:
        """Cut a doorway and hang a door in it that opens from both sides.

        ``(x, y, z)`` is the door's lower half, standing on the floor; you
        walk through it toward ``facing`` and back. Whatever stands in the
        two cells is cut, and the door is placed whole through
        :meth:`place`. A wooden or copper door opens by hand. An **iron**
        door opens only when power reaches it, so each side gets a
        ``button`` on the wall beside the door, at head height where the
        wall allows -- on a block that conducts, since a button on glass
        sticks and opens nothing. A doorway with no conducting wall to
        carry a button on a side is refused before anything is written, a
        :class:`~heaton_voxel.craft.errors.Refused` naming each such side,
        after the door and the button are checked as every plan is -- a
        block the game lacks, then a chunk with nothing to build into.
        Then the plan is audited (:meth:`_audit_plan`), as a wall's is: a
        door with no floor under it, which the game drops at the next
        block update, or a cut that leaves a torch beside the doorway on
        nothing, is refused, faults that stood before it not counted. The
        finished door is checked from both sides with the same rule
        :func:`~heaton_voxel.craft.survey.reachable` walks by.

        Returns the cells written.
        """
        if facing not in _HEADINGS:
            raise NBTError(f"facing must be one of {tuple(_HEADINGS)}, got {facing!r}")
        dx, dz = _COMPASS[facing]
        state = _state(door)
        if not state.name.endswith("_door"):
            raise NBTError(f"{state} is not a door")
        props = dict(BLOCK_DEFAULTS.get(state.name, {}))
        props.update(state.properties)
        props.update(facing=facing, hinge=hinge, half="lower", open="false")
        state = BlockState(state.name, props)
        wired = state.name in _POWER_DOORS
        if wired:
            # One button, set facing out of each wall: a Mix or a Material
            # is refused before a wall is read for it.
            refuse_many(button)
        # The plan's own checks before the walls are read for a button:
        # a door or a button the game lacks, then a door in a chunk with
        # nothing to build into, refuses as _commit would.
        self._vet_plan([("place", (x, y, z), state)], what="doorway",
                       names=(button,) if wired else ())

        presses = []
        missing = []
        if wired:
            # Beside the door, head height first, then the lower half; the
            # button sits on the side being served, facing out of the wall.
            across = [(dz, dx), (-dz, -dx)]
            for side in ((dx, dz), (-dx, -dz)):
                spot = None
                for level in (y + 1, y):
                    for px, pz in across:
                        wall = (x + px, level, z + pz)
                        found = self.save.get_block(*wall, dimension=self.dimension)
                        seat = (wall[0] + side[0], level, wall[2] + side[1])
                        free = self.save.get_block(*seat, dimension=self.dimension)
                        if (found is not None and facts(found.name, found.properties).conductor
                                and free is not None and _empty(free)):
                            spot = seat
                            break
                    if spot:
                        break
                if spot is None:
                    missing.append(
                        f"no conducting wall beside the doorway at ({x}, {y}, "
                        f"{z}) to carry a button on its {_FACING_OF[side]} "
                        f"side")
                else:
                    presses.append((spot, _FACING_OF[side]))
        if missing:
            both = " (and the other side too)" if len(missing) > 1 else ""
            raise Refused(
                f"{missing[0]}{both}; an iron door with nothing to open it is "
                f"a wall. Build the wall first, or use a door that opens by "
                f"hand. Nothing was written", faults=missing)

        # The cut, the door and its buttons, planned whole and written in
        # that order.
        written = []
        ops: list[tuple] = []
        for level in (y, y + 1):
            here = self.save.get_block(x, level, z, dimension=self.dimension)
            if here is not None and not _empty(here):
                ops.append(("set", (x, level, z), AIR))
        ops.append(("place", (x, y, z), state))
        written += [(x, y, z), (x, y + 1, z)]
        for (bx, by, bz), out in presses:
            press = _state(button)
            pressed = dict(BLOCK_DEFAULTS.get(press.name, {}))
            pressed.update(press.properties)
            pressed.update(face="wall", facing=out, powered="false")
            ops.append(("set", (bx, by, bz), BlockState(press.name, pressed)))
            written.append((bx, by, bz))
        self._audit_plan(ops, what="doorway")
        self._commit(ops, what="doorway")
        if wired:
            def block_at(bx, by, bz):
                return self.save.get_block(bx, by, bz, dimension=self.dimension)
            for side in ((dx, dz), (-dx, -dz)):
                if not iron_door_opens(block_at, (x, y, z), side):
                    raise NBTError(
                        f"the iron door at ({x}, {y}, {z}) was hung but does "
                        f"not open from its {_FACING_OF[side]} side; this is "
                        f"a bug here, do not save")
        return written

    def window(self, x, y, z, axis, *, block=FROM_PALETTE) -> None:
        """A window of glass in a wall running along ``axis`` (``"x"``;
        anything else is a wall along z).

        ``block`` is the glass: the Builder's :attr:`palette`'s ``glass``
        (a glass pane, by default) when it is left out, any block, or a
        :class:`~heaton_voxel.craft.style.Material`, which is its pane --
        refused by name, an :class:`~heaton_voxel.craft.errors.IllegalBlock`,
        for a family that has none. A pane, or any block whose defaults
        carry all four of ``north``, ``south``, ``east`` and ``west`` as
        true or false -- iron bars -- is written joined along the wall,
        ``east`` and ``west`` for a wall along x and ``north`` and
        ``south`` for one along z, its other properties as given; any other
        block -- plain or tinted glass -- is written as it is, since a
        connection its block does not take is a property the game would
        drop, and a strict write refuses it. A
        :class:`~heaton_voxel.craft.style.Mix` is a ``TypeError``: the
        connections are spelled onto one block.

        The pair along the wall is the window's to set, so the block given
        is checked as it was given, before they are: a state the game does
        not have -- ``east=maybe`` -- is an
        :class:`~heaton_voxel.craft.errors.IllegalBlock` in the window's
        name, and a side of that pair given as anything but ``true`` -- a
        pane told not to join the wall it is set in -- is an ``NBTError``,
        rather than quietly joined. Nothing is written either way.
        """
        glass, = self._materials(
            "window", _Slot("block", block, "glass", "pane"))
        if isinstance(glass, (str, BlockState)):
            state = _state(glass)
            # As given, before the window spells anything onto it: what it
            # joins must not hide what was wrong with it.
            self._vet_names((state,), what="window")
            defaults = BLOCK_DEFAULTS.get(state.name, {})
            if all(defaults.get(side) in ("true", "false")
                   for side in _SIDES):
                along = ("east", "west") if axis == "x" else ("north", "south")
                told = [f"{side}={state.properties[side]}" for side in along
                        if state.properties.get(side, "true") != "true"]
                if told:
                    raise NBTError(
                        f"window joins its glass along the wall, setting "
                        f"{along[0]} and {along[1]} true, and {state} says "
                        f"{', '.join(told)}; leave them out, or set() the "
                        f"block for a pane that does not join. Nothing was "
                        f"written")
                joined = dict(state.properties)
                joined.update(dict.fromkeys(along, "true"))
                glass = BlockState(state.name, joined)
        self.set(x, y, z, glass)
