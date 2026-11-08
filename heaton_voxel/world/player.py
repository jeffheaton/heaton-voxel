"""The player's side of a save: position, spawn, and the ``~`` notation.

A mixin of :class:`~heaton_voxel.world.MinecraftSave` -- one file per
concern, mirroring the C# port's partial classes (see PORTING.md).
"""

from __future__ import annotations

import os

from ..nbt import (
    NBT,
    NBTError,
    TagCompound,
    TagDouble,
    TagFloat,
    TagIntArray,
    TagList,
    TagString,
)
from ._state import SaveState
from .dimension import END, NETHER, OVERWORLD

#: A dimension as a number, the way a player compound named it before
#: 1.16 -- and still does, in a file the game has moved and not yet loaded
#: -- read as the game's ``LegacyDimensionIdFix`` reads it: any number but
#: -1 and 1 is the overworld.
_LEGACY_DIMENSION_IDS = {-1: NETHER, 1: END}


def _uuid_text(words) -> str:
    digits = "".join(f"{int(word) & 0xFFFFFFFF:08x}" for word in words)
    return (f"{digits[0:8]}-{digits[8:12]}-{digits[12:16]}-"
            f"{digits[16:20]}-{digits[20:32]}")


def _player_uuid(tag) -> str | None:
    """The uuid a player compound carries: four ints from 1.16, two longs
    before (``PlayerUUIDFix``)."""
    words = tag.get("UUID")
    if words is not None and len(words) == 4:
        return _uuid_text(words)
    most, least = tag.get("UUIDMost"), tag.get("UUIDLeast")
    if most is not None and least is not None:
        most, least = int(most) & (2**64 - 1), int(least) & (2**64 - 1)
        return _uuid_text([most >> 32, most, least >> 32, least])
    return None


class PlayerOps(SaveState):
    """Player data, world spawn, and player-relative coordinates."""

    def _load_player(self) -> TagCompound:
        """Find the single-player's data.

        It lives in ``players/data/<uuid>.dat``, and ``level.dat`` names the
        player through ``singleplayer_uuid`` -- four signed 32-bit words, most
        significant first, which is how Minecraft encodes a UUID in NBT.
        """
        if self._player is not None:
            return self._player_root()
        if self.layout == "old":
            return self._load_old_player()

        directory = os.path.join(self.path, "players", "data")
        candidates = []

        raw_uuid = self._level_data().get("singleplayer_uuid")
        if raw_uuid is not None and len(raw_uuid) == 4:
            candidates.append(os.path.join(directory, f"{_uuid_text(raw_uuid)}.dat"))

        # A world with no singleplayer_uuid still has exactly one player file
        # if it has only ever been played locally.
        if os.path.isdir(directory):
            candidates.extend(
                os.path.join(directory, name)
                for name in sorted(os.listdir(directory))
                if name.endswith(".dat")
            )

        for candidate in candidates:
            if os.path.exists(candidate):
                self._player = NBT.load(candidate)
                self._player_path = candidate
                return self._player_root()

        raise NBTError(
            f"no player data in {self.path!r}: expected a .dat under players/data/"
        )

    def _player_root(self) -> TagCompound:
        """The compound of the player :meth:`_load_player` found."""
        root = self._player.root if self._player is not None else None
        if root is None:
            raise NBTError(f"the player data in {self._player_path!r} holds no compound")
        return root

    def _load_old_player(self) -> TagCompound:
        """The player of a save in the layout before 26.1, as the game
        keeps it when it moves the save: ``playerdata/<uuid>.dat`` of the
        player ``level.dat`` holds, which ``PlayerStorageFileFix`` moves to
        ``players/data/`` while ``LevelDatToSavedDataFileFix`` drops
        ``level.dat``'s own copy and keeps only its uuid. Where that file is
        missing the copy in ``level.dat`` is read as it stands -- the game
        would lose it, so there is nothing of the game's to agree with --
        and a save with no player in ``level.dat`` (a server's) reads the
        first of ``playerdata/`` in name order, as a current one reads
        ``players/data/``."""
        directory = os.path.join(self.path, "playerdata")
        embedded = self._level_data().get("Player")
        candidates = []
        if isinstance(embedded, TagCompound):
            uuid = _player_uuid(embedded)
            if uuid is not None:
                candidates.append(os.path.join(directory, f"{uuid}.dat"))
        for candidate in candidates:
            if os.path.exists(candidate):
                self._player, self._player_path = NBT.load(candidate), candidate
                return self._player_root()
        if isinstance(embedded, TagCompound):
            held = NBT()
            held.root = embedded
            self._player, self._player_path = held, None
            return embedded
        if os.path.isdir(directory):
            for name in sorted(os.listdir(directory)):
                if name.endswith(".dat"):
                    path = os.path.join(directory, name)
                    self._player, self._player_path = NBT.load(path), path
                    return self._player_root()
        raise NBTError(
            f"no player data in {self.path!r}: no Player in level.dat and no .dat "
            f"under playerdata/")

    def player_position(self) -> tuple[float, float, float]:
        """The player's exact position in global coordinates.

        This is the entity position: ``y`` is the player's feet, and the
        values are fractional.
        """
        pos = self._load_player()["Pos"]
        if len(pos) != 3:
            raise NBTError(f"player Pos has {len(pos)} entries, expected 3")
        return (float(pos[0]), float(pos[1]), float(pos[2]))

    def player_block_position(self) -> tuple[int, int, int]:
        """The block the player is standing in, as integers.

        Floors rather than truncates, so it stays correct west and north of
        the origin where the coordinates are negative.
        """
        import math

        x, y, z = self.player_position()
        return (math.floor(x), math.floor(y), math.floor(z))

    def set_player_position(
        self, x: float, y: float, z: float, *, keep_motion: bool = False
    ) -> tuple[float, float, float]:
        """Move the player. Nothing reaches disk until :meth:`save`.

        These are entity coordinates, not block coordinates: ``y`` is the
        feet, so standing someone on top of the block at ``by`` means
        ``by + 1``, and the middle of a block column is ``bx + 0.5``.

        Momentum and fall distance are cleared unless ``keep_motion``,
        because a teleport should not arrive still traveling: a player
        moved while falling keeps the fall and takes the damage on load,
        which is a surprising way for an editor to hurt someone.
        """
        self._refuse_read_only("move the player of")
        root = self._load_player()
        pos = root.get("Pos")
        if pos is None or len(pos) != 3:
            raise NBTError("player data has no Pos to move")
        pos[0] = TagDouble(float(x))
        pos[1] = TagDouble(float(y))
        pos[2] = TagDouble(float(z))

        if not keep_motion:
            motion = root.get("Motion")
            if isinstance(motion, TagList) and len(motion) == 3:
                for axis in range(3):
                    motion[axis] = TagDouble(0.0)
            # Both spellings exist across versions; write whichever is there
            # rather than inventing a second one beside it.
            for name in ("fall_distance", "FallDistance"):
                if name in root:
                    root[name] = TagFloat(0.0)
        return (float(x), float(y), float(z))

    def player_rotation(self) -> tuple[float, float]:
        """The player's yaw and pitch, in degrees.

        Yaw follows the game's convention: 0 faces south (+z), 90 west
        (-x), 180 north (-z), 270 east (+x); values may be negative or wrap
        past 360. Pitch is -90 straight up to 90 straight down.
        """
        rotation = self._load_player().get("Rotation")
        if rotation is None or len(rotation) != 2:
            raise NBTError("player data has no Rotation")
        return (float(rotation[0]), float(rotation[1]))

    def player_dimension(self) -> str:
        """Which dimension the player is in. A number, as a player saved
        before 1.16 names it, is read as the game reads it
        (:data:`_LEGACY_DIMENSION_IDS`)."""
        found = self._load_player().get("Dimension")
        if found is None:
            return OVERWORLD
        if isinstance(found, TagString):
            return str(found)
        return _LEGACY_DIMENSION_IDS.get(int(found), OVERWORLD)

    def spawn_position(self) -> tuple[int, int, int]:
        """The world spawn point: ``level.dat``'s ``spawn`` compound, or the
        ``SpawnX``/``SpawnY``/``SpawnZ`` a level.dat before 4548 kept, which
        the game's ``WorldSpawnDataFix`` makes it."""
        data = self._level_data()
        spawn = data.get("spawn")
        if spawn is not None and "pos" in spawn:
            pos = spawn["pos"]
            return (int(pos[0]), int(pos[1]), int(pos[2]))
        if all(key in data for key in ("SpawnX", "SpawnY", "SpawnZ")):
            return (int(data["SpawnX"]), int(data["SpawnY"]), int(data["SpawnZ"]))
        raise NBTError("level.dat has no spawn position")

    def set_spawn_position(
        self,
        x: int,
        y: int,
        z: int,
        *,
        dimension: str | None = None,
        yaw: float | None = None,
        pitch: float | None = None,
    ) -> tuple[int, int, int]:
        """Move the world spawn point. Nothing reaches disk until
        :meth:`save`.

        These are block coordinates. The spawn's dimension and facing are
        kept as they are unless given -- moving the spawn point is usually
        about *where*, and silently resetting the rest would surprise.
        Refuses a save whose level.dat predates the ``spawn`` compound
        (Minecraft 26.x) rather than inventing the old scalar fields.
        """
        self._refuse_read_only("move the spawn of")
        spawn = self._level_data().get("spawn")
        if not isinstance(spawn, TagCompound):
            raise NBTError(
                "level.dat has no spawn compound: this world predates the "
                "current layout (Minecraft 26.x)"
            )
        spawn["pos"] = TagIntArray([int(x), int(y), int(z)])
        if dimension is not None:
            spawn["dimension"] = TagString(dimension)
        if yaw is not None:
            spawn["yaw"] = TagFloat(float(yaw))
        if pitch is not None:
            spawn["pitch"] = TagFloat(float(pitch))
        return (int(x), int(y), int(z))

    def resolve_coordinates(
        self, tokens, axes: str | None = None
    ) -> tuple[int, ...]:
        """Turn coordinate tokens into absolute block coordinates.

        Understands the game's relative notation alongside plain integers:
        ``~`` is the player's block position on that axis, ``~5`` five
        blocks beyond it, ``~-3`` three short of it -- so ``["~-2", "64",
        "~"]`` means "two west of me, at y 64, my z". Which axis each token
        belongs to comes from ``axes``, a string of ``x``/``y``/``z`` with
        one letter per token; left unset, tokens cycle x, y, z, so one call
        resolves a position or a two-corner box alike.

        The player is looked up only when a ``~`` actually appears, so
        absolute coordinates keep working in a world with no player data (a
        server's, say), and a relative one there fails with a message that
        says why instead of a bare lookup error.

        Meant for command lines and other user-typed input; every CLI in
        this package feeds its coordinate arguments through here. Note that
        shells expand a bare ``~`` into the home directory before Python
        ever sees it -- quote it (``'~'``). A token that arrives looking
        like a path is refused with exactly that hint.
        """
        tokens = [str(token) for token in tokens]
        if axes is None:
            if len(tokens) % 3:
                raise NBTError(
                    f"expected coordinates in x/y/z groups, got {len(tokens)} "
                    f"values; pass axes= to name the axis of each"
                )
            axes = ("xyz" * ((len(tokens) + 2) // 3))[: len(tokens)]
        if len(axes) != len(tokens):
            raise NBTError(
                f"{len(tokens)} coordinates but {len(axes)} axis letters ({axes!r})"
            )

        player: tuple[int, int, int] | None = None
        resolved: list[int] = []
        for token, axis in zip(tokens, axes):
            token = token.strip()
            try:
                index = "xyz".index(axis)
            except ValueError:
                raise NBTError(f"axis letters must be x, y or z, got {axis!r}") from None
            if token.startswith("~"):
                if player is None:
                    try:
                        player = self.player_block_position()
                    except NBTError as exc:
                        raise NBTError(
                            f"cannot resolve {token!r}: relative coordinates "
                            f"need a player position, and {exc}"
                        ) from None
                rest = token[1:]
                try:
                    delta = int(rest) if rest else 0
                except ValueError:
                    raise NBTError(
                        f"bad relative coordinate {token!r}: expected ~, ~N or ~-N"
                    ) from None
                resolved.append(player[index] + delta)
                continue
            try:
                resolved.append(int(token))
            except ValueError:
                if os.sep in token:
                    # A shell expanded the user's bare ~ into their home
                    # directory before Python ever saw it.
                    raise NBTError(
                        f"coordinate {token!r} looks like a shell-expanded home "
                        f"directory; quote relative coordinates so the shell "
                        f"passes them through, e.g. '~' or '~-5'"
                    ) from None
                raise NBTError(
                    f"bad coordinate {token!r}: expected an integer, ~, ~N or ~-N"
                ) from None
        return tuple(resolved)
