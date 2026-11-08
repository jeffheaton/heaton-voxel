"""Recompute neighbor-derived block states: :func:`connect_blocks`.

Fences, walls, glass panes, iron bars, fence gates and stairs store state the
game *derives from their neighbors* -- a fence's ``north=true``, a wall's
post flag, a stair's corner ``shape``. Vanilla recomputes these only when a
block update fires, so blocks written directly into a save render exactly as
stored: bare fences stand as disconnected posts and roof corners stay
straight until something nudges them.

    save.fill(a, b, "minecraft:oak_fence")
    save.connect_blocks(a, b)          # now they join up
    save.reset_lighting(a, b)
    save.save()

:func:`connect_blocks` rewrites, for every such block in a box, exactly the
properties vanilla's neighbor-update would: connection sides (and only
those -- ``waterlogged`` and friends are placement history, not derived
state, and are never touched). The rules are game knowledge, like
:mod:`heaton_voxel.craft.render`'s colors: the connection families below were
validated against vanilla-placed blocks in a real save, and anything unknown
errs toward *not* connecting -- a post where a stub might have been, never a
stub jutting into nothing. What counts as solid to join is not a name at all:
it is the game's own answer, generated from its shapes into
:mod:`heaton_voxel.connect_faces`, as what a wall reads of the block above it
is into :mod:`heaton_voxel.wall_cover`.

Pass a box that covers the whole build plus a one-block margin: the pass
reads neighbors beyond the box but rewrites only inside it, so a bare fence
just outside the edge would keep its stale state.
"""

from __future__ import annotations

from .block_defaults import BLOCK_DEFAULTS
from .blocks import BlockState
from .connect_faces import full_sides, SIDE_BIT
from .nbt import NBTError
from .wall_cover import EAST, NORTH, OVERRIDE, POST, SOUTH, WEST, wall_cover

__all__ = ["connect_blocks", "ConnectResult"]


# --------------------------------------------------------------------------
# The vocabulary
# --------------------------------------------------------------------------

_DIRECTIONS = (("north", 0, -1), ("south", 0, 1), ("west", -1, 0), ("east", 1, 0))
_VECTOR = {name: (dx, dz) for name, dx, dz in _DIRECTIONS}
_OPPOSITE = {"north": "south", "south": "north", "west": "east", "east": "west"}
# Counter-clockwise seen from above, the game's Direction.getCounterClockWise.
_CCW = {"north": "west", "west": "south", "south": "east", "east": "north"}

# The game's ``Block.isExceptionForConnection`` (26.3, read with javap):
# what a fence, a wall, a pane or bars never join as a solid neighbor,
# however full its faces -- leaves, the barrier, the pumpkins, the jack
# o'lantern, the melon and the shulker boxes. Every one of those predicates
# asks it before the face, so these are never sturdy here.
_NO_CONNECTION_EXACT = {
    "minecraft:barrier", "minecraft:pumpkin", "minecraft:carved_pumpkin",
    "minecraft:jack_o_lantern", "minecraft:melon",
}
_NO_CONNECTION_SUFFIXES = ("_leaves", "shulker_box")


def _no_connection(name: str) -> bool:
    return name in _NO_CONNECTION_EXACT or name.endswith(_NO_CONNECTION_SUFFIXES)


# The side strip of a wall's own test each direction names
# (:mod:`heaton_voxel.wall_cover`).
_SIDE_BIT = {"north": NORTH, "east": EAST, "south": SOUTH, "west": WEST}


def _is_wooden_fence(name: str) -> bool:
    return name.endswith("_fence") and "nether_brick" not in name


def _is_nether_fence(name: str) -> bool:
    return name.endswith("_fence") and "nether_brick" in name


def _is_gate(name: str) -> bool:
    return name.endswith("_fence_gate")


def _is_wall(name: str) -> bool:
    return name.endswith("_wall")


def _is_pane(name: str) -> bool:
    return name.endswith("_pane") or name == "minecraft:iron_bars"


def _is_stairs(name: str) -> bool:
    return name.endswith("_stairs")


def _gate_spans(gate: BlockState, direction: str) -> bool:
    """A gate's bar runs perpendicular to its facing."""
    facing = gate.properties.get("facing", "north")
    if facing in ("east", "west"):
        return direction in ("north", "south")
    return direction in ("east", "west")


def _candidate(name: str, stairs: bool) -> bool:
    return (name.endswith("_fence") or _is_wall(name) or _is_pane(name)
            or _is_gate(name) or (stairs and _is_stairs(name)))


# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------


class ConnectResult:
    """What a :func:`connect_blocks` pass actually rewrote.

    The usual honest accounting: chunks the box reaches that the world never
    generated, or that cannot be read, are listed rather than silently
    treated as empty -- their blocks keep whatever states they have.
    """

    __slots__ = (
        "fences_updated",
        "walls_updated",
        "panes_updated",
        "gates_updated",
        "stairs_updated",
        "blocks_scanned",
        "missing_chunks",
        "unreadable_chunks",
    )

    def __init__(self):
        self.fences_updated = 0
        self.walls_updated = 0
        self.panes_updated = 0
        self.gates_updated = 0
        self.stairs_updated = 0
        # Connectable blocks examined (changed or not).
        self.blocks_scanned = 0
        self.missing_chunks: list[tuple[int, int]] = []
        self.unreadable_chunks: list[tuple[tuple[int, int], str]] = []

    @property
    def total_updated(self) -> int:
        return (self.fences_updated + self.walls_updated + self.panes_updated
                + self.gates_updated + self.stairs_updated)

    @property
    def complete(self) -> bool:
        return not (self.missing_chunks or self.unreadable_chunks)

    def __repr__(self) -> str:
        parts = [f"{self.total_updated:,} of {self.blocks_scanned:,} rewritten"]
        for label in ("fences", "walls", "panes", "gates", "stairs"):
            count = getattr(self, f"{label}_updated")
            if count:
                parts.append(f"{count} {label}")
        if self.missing_chunks:
            parts.append(f"{len(self.missing_chunks)} chunks ungenerated")
        if self.unreadable_chunks:
            parts.append(f"{len(self.unreadable_chunks)} chunks unreadable")
        return f"<ConnectResult {', '.join(parts)}>"


# --------------------------------------------------------------------------
# The pass
# --------------------------------------------------------------------------


def _face_full(neighbor: BlockState, direction: str) -> bool:
    """Does the neighbor present a full square face back toward us?

    ``direction`` runs from the connecting block to the neighbor, so the
    face that touches is the neighbor's ``opposite(direction)`` side, and
    whether it is full is the game's own answer, generated from its shapes
    into :mod:`heaton_voxel.connect_faces`. A stair's full face is its
    ``facing`` side (shipwreck fences attach to stair backs), read by that
    alone since its ``shape`` is derived in the same sweep. What the game
    excepts from every connection (:func:`_no_connection`) is never joined
    as solid, and a block the game does not have has no full face, which
    under-connects rather than inventing an attachment.
    """
    name = neighbor.name
    if _no_connection(name):
        return False
    if _is_stairs(name):
        return neighbor.properties.get("facing", "north") == _OPPOSITE[direction]
    return bool(full_sides(name, neighbor.properties)
                & SIDE_BIT[_OPPOSITE[direction]])


def _fence_side(name: str, neighbor: BlockState | None, direction: str) -> bool:
    if neighbor is None:
        return False
    other = neighbor.name
    if _is_wooden_fence(name):
        if _is_wooden_fence(other):
            return True
    elif _is_nether_fence(other):
        return True
    if _is_gate(other):
        return _gate_spans(neighbor, direction)
    return _face_full(neighbor, direction)


def _wall_side(neighbor: BlockState | None, direction: str) -> bool:
    if neighbor is None:
        return False
    other = neighbor.name
    if _is_wall(other) or _is_pane(other):
        return True
    if _is_gate(other):
        return _gate_spans(neighbor, direction)
    return _face_full(neighbor, direction)


def _pane_side(neighbor: BlockState | None, direction: str) -> bool:
    if neighbor is None:
        return False
    other = neighbor.name
    return _is_pane(other) or _is_wall(other) or _face_full(neighbor, direction)


def _stair_shape(state: BlockState, neighbor_at) -> str:
    """The game's StairBlock.getStairShape, from stored states.

    ``neighbor_at(direction)`` returns the horizontally adjacent state at
    the stair's own level. Chirality was validated against vanilla-placed
    corner stairs in a real save.
    """
    props = state.properties
    facing = props.get("facing", "north")
    half = props.get("half", "bottom")

    def flank_differs(direction: str) -> bool:
        n = neighbor_at(direction)
        if n is None or not _is_stairs(n.name):
            return True
        return (n.properties.get("facing", "north") != facing
                or n.properties.get("half", "bottom") != half)

    behind = neighbor_at(facing)
    if behind is not None and _is_stairs(behind.name) \
            and behind.properties.get("half", "bottom") == half:
        other = behind.properties.get("facing", "north")
        if other in (_CCW[facing], _OPPOSITE[_CCW[facing]]) \
                and flank_differs(_OPPOSITE[other]):
            return "outer_left" if other == _CCW[facing] else "outer_right"

    front = neighbor_at(_OPPOSITE[facing])
    if front is not None and _is_stairs(front.name) \
            and front.properties.get("half", "bottom") == half:
        other = front.properties.get("facing", "north")
        if other in (_CCW[facing], _OPPOSITE[_CCW[facing]]) \
                and flank_differs(other):
            return "inner_left" if other == _CCW[facing] else "inner_right"

    return "straight"


def _cover(above: BlockState | None) -> int:
    """What the block over a wall shows it, as a
    :func:`~heaton_voxel.wall_cover.wall_cover` code -- the game's own
    answer, from its collision shapes and its ``wall_post_override`` tag,
    not from a name. Nothing where there is no block to read (an unloaded
    chunk, past the top of the world), which the game reads as air; the
    post's center alone for a block the table does not know, which runs no
    side tall and raises a post over any run: a post where a stub might
    have been, never a wall reaching up to nothing."""
    if above is None:
        return 0
    code = wall_cover(above.name, above.properties)
    return POST if code is None else code


def _raises_post(sides: dict, above: BlockState | None, cover: int) -> bool:
    """The game's ``WallBlock.shouldRaisePost``, in its own order: a wall
    above with its post up raises this one's outright; then any end, lone
    block, T, L or cross that is lopsided in what it joins raises one; a
    run tall on both sides of an axis -- under a solid block, or the wall
    above it -- runs flush; and otherwise the post rises only for what is
    in ``wall_post_override`` or covers the post's center (a lantern, a
    pot), so a straight run under a top slab, a leaf or open sky is
    flush."""
    if (above is not None and _is_wall(above.name)
            and above.properties.get(
                "up", BLOCK_DEFAULTS.get(above.name, {}).get("up")) == "true"):
        return True
    north, east, south, west = (sides[d] == "none"
                                for d in ("north", "east", "south", "west"))
    if (north and east and south and west) or north != south or east != west:
        return True
    if ((sides["north"] == "tall" and sides["south"] == "tall")
            or (sides["east"] == "tall" and sides["west"] == "tall")):
        return False
    return bool(cover & (OVERRIDE | POST))


def _derived_state(
    state: BlockState, block_at, x: int, y: int, z: int, *, stairs: bool = True
) -> BlockState | None:
    """The state vanilla's neighbor update would produce, or ``None`` when
    the block is not one whose state is neighbor-derived.

    ``block_at(x, y, z)`` supplies neighboring states (``None`` for
    unloaded). Shared by :func:`connect_blocks` and the validation tooling,
    so what ships is exactly what was checked against the game's own output.
    """
    name = state.name
    fence = name.endswith("_fence")
    wall = _is_wall(name)
    pane = _is_pane(name)
    gate = _is_gate(name)
    stair = stairs and _is_stairs(name)
    if not (fence or wall or pane or gate or stair):
        return None
    props = dict(state.properties)

    if fence or wall or pane:
        # What a wall reads of the block above it, as WallBlock.updateShape
        # does: the strips and the post center that block's underside
        # covers, and whether it is in wall_post_override.
        above = block_at(x, y + 1, z) if wall else None
        cover = _cover(above) if wall else 0
        for direction, dx, dz in _DIRECTIONS:
            n = block_at(x + dx, y, z + dz)
            if fence:
                linked = _fence_side(name, n, direction)
            elif wall:
                linked = _wall_side(n, direction)
            else:
                linked = _pane_side(n, direction)
            if wall:
                if not linked:
                    props[direction] = "none"
                elif cover & _SIDE_BIT[direction]:
                    # the block above covers this side, so the wall runs tall
                    # up to it: a solid block, a leaf, a bottom slab, the
                    # side of a wall above joined the same way
                    props[direction] = "tall"
                else:
                    props[direction] = "low"
            else:
                props[direction] = "true" if linked else "false"
        if wall:
            props["up"] = "true" if _raises_post(props, above, cover) else "false"
    elif gate:
        beside = ("north", "south") if _gate_spans(state, "north") else ("west", "east")
        in_wall = any(
            (n := block_at(x + _VECTOR[d][0], y, z + _VECTOR[d][1])) is not None
            and _is_wall(n.name)
            for d in beside
        )
        props["in_wall"] = "true" if in_wall else "false"
    else:  # stair
        def neighbor(direction):
            dx, dz = _VECTOR[direction]
            return block_at(x + dx, y, z + dz)

        props["shape"] = _stair_shape(state, neighbor)

    return BlockState(name, props)


def connect_blocks(
    save,
    corner1: tuple[int, int, int],
    corner2: tuple[int, int, int],
    *,
    dimension: str | None = None,
    stairs: bool = True,
    keep_loaded: bool = False,
) -> ConnectResult:
    """Recompute neighbor-derived states in the box between two corners.

    Corners are inclusive global block coordinates in any order, as for
    :meth:`~heaton_voxel.world.MinecraftSave.fill`. For every fence, wall,
    glass pane, iron bars, fence gate and (unless ``stairs=False``) stair in
    the box, the properties vanilla would derive from neighbors are
    recomputed and written back:

    * fences: ``north/south/east/west`` -- to fences of the same family
      (wooden with wooden, nether brick with nether brick), to a gate whose
      bar spans that way, and to full solid faces;
    * walls: the same sides as ``none``/``low``/``tall`` (tall where the
      block above covers the side's strip, by the game's own collision
      shapes in :mod:`heaton_voxel.wall_cover`), plus the ``up`` post flag
      (:func:`_raises_post`, the game's ``shouldRaisePost``: a wall above
      with its post up raises it, a run tall both ways along an axis is
      flush, and otherwise only an end, a T, an L, a lopsided cross or
      what perches on top -- a torch, a lantern -- raises one); walls also
      join panes and bars;
    * panes and iron bars: sides toward panes, bars, walls and solid faces;
    * gates: ``in_wall`` when a wall adjoins along the bar;
    * stairs: the corner ``shape`` (``inner``/``outer`` left/right) from the
      neighboring stairs' facings.

    Nothing else on the state is touched -- ``waterlogged`` in particular is
    placement history the game never derives, so a builder must set it when
    placing into water. Blocks the vocabulary does not recognize count as
    "not solid", which errs toward a free-standing post rather than an arm
    reaching at nothing.

    Give the box a one-block margin around new construction: neighbors are
    read beyond the box, but only blocks inside it are rewritten.

    Ungenerated and unreadable chunks are skipped and reported on the
    result, mirroring ``fill``. Returns a :class:`ConnectResult`.
    """
    dim = save.dimension(dimension)
    x0, x1 = sorted((int(corner1[0]), int(corner2[0])))
    y0, y1 = sorted((int(corner1[1]), int(corner2[1])))
    z0, z1 = sorted((int(corner1[2]), int(corner2[2])))
    result = ConnectResult()

    def block_at(x: int, y: int, z: int) -> BlockState | None:
        try:
            accessor = dim.blocks(x >> 4, z >> 4)
        except NBTError:
            return None
        if accessor is None:
            return None
        try:
            return accessor.get(x & 15, y, z & 15)
        except IndexError:
            return None

    for chunk_x in range(x0 >> 4, (x1 >> 4) + 1):
        for chunk_z in range(z0 >> 4, (z1 >> 4) + 1):
            try:
                accessor = dim.writable_blocks(chunk_x, chunk_z)
            except NBTError as exc:
                result.unreadable_chunks.append(((chunk_x, chunk_z), str(exc)))
                continue
            if accessor is None:
                result.missing_chunks.append((chunk_x, chunk_z))
                continue

            base_x, base_z = chunk_x * 16, chunk_z * 16
            lx0, lx1 = max(x0, base_x) - base_x, min(x1, base_x + 15) - base_x
            lz0, lz1 = max(z0, base_z) - base_z, min(z1, base_z + 15) - base_z

            # Sections whose palettes hold nothing connectable are skipped
            # outright, which is nearly all of them; candidates are visited
            # top-down so a wall reads the already-settled state of the wall
            # above it when deciding tall sides and its post.
            for section_y in sorted(accessor.sections, reverse=True):
                base_y = section_y * 16
                if base_y > y1 or base_y + 15 < y0:
                    continue
                container = accessor.sections[section_y].blocks
                if container is None:
                    continue
                palette = container.palette
                hits = {
                    i for i, entry in enumerate(palette)
                    if _candidate(entry.name, stairs)
                }
                if not hits:
                    continue
                indices = container.indices()
                for ly in range(15, -1, -1):
                    y = base_y + ly
                    if not (y0 <= y <= y1):
                        continue
                    row = ly * 256
                    for lz in range(lz0, lz1 + 1):
                        for lx in range(lx0, lx1 + 1):
                            if indices[row + lz * 16 + lx] not in hits:
                                continue
                            x, z = base_x + lx, base_z + lz
                            state = container.get(row + lz * 16 + lx)
                            derived = _derived_state(
                                state, block_at, x, y, z, stairs=stairs
                            )
                            result.blocks_scanned += 1
                            if derived is None or derived == state:
                                continue
                            accessor.set(lx, y, lz, derived)
                            name = state.name
                            if name.endswith("_fence"):
                                result.fences_updated += 1
                            elif _is_wall(name):
                                result.walls_updated += 1
                            elif _is_pane(name):
                                result.panes_updated += 1
                            elif _is_gate(name):
                                result.gates_updated += 1
                            else:
                                result.stairs_updated += 1
                    indices = container.indices()  # a set() may have repacked
            if not keep_loaded:
                dim.release(chunk_x, chunk_z)
    return result
