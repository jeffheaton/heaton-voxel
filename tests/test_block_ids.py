"""Every block id the library and its examples name is a block in the game.

The game does not refuse a name it does not know. A chunk saved after the
rename that retired a name loads it as air, logs a line nobody reads, and
writes air back. ``minecraft:chain`` became ``minecraft:iron_chain`` at
DataVersion 4541, and ``Builder.hang_lamps`` kept writing the old name as
its default: every chain it hung in a 26.x chunk was gone the moment the
game loaded it, leaving the lantern under it with nothing to hang from.
Nothing raised, and every render drew the chain.

So every ``minecraft:`` id in ``heaton_voxel/`` and ``examples/`` is held
to ``fixtures/block_shapes.json``, which is the game's own block list. The
renames themselves are ``craft.blockinfo.RENAMED``, the one table of them,
which ``blockinfo.check_state`` refuses an old name by.
"""

from __future__ import annotations

import os
import re
import unittest

from fixtures import block_shapes
from heaton_voxel.craft.biomes import (
    BIOME_COLORS,
    FREEZING_BIOMES,
    VILLAGER_KINDS,
)
from heaton_voxel.craft.blockinfo import RENAMED
from heaton_voxel.world import END, NETHER, OVERWORLD

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ID = re.compile(r"minecraft:([a-z0-9_]+)")

#: Ids in the code that name something other than a block: biomes,
#: villager types (``minecraft:snow`` is one as well as a block) and
#: dimensions.
NOT_BLOCKS = (set(BIOME_COLORS) | set(FREEZING_BIOMES) | set(VILLAGER_KINDS)
              | {OVERWORLD, NETHER, END})
OTHER_IDS = {
    "minecraft:full": "a chunk status",
    "minecraft:empty": "a chunk status",
    "minecraft:biomes": "a chunk status",
    "minecraft:carvers": "a chunk status",
    "minecraft:freeze_top_layer": "a placed feature",
    "minecraft:villager": "an entity",
}

#: Where each retired name (``RENAMED``) may still be named. Chunks saved
#: before the rename still hold it, so a table that *reads* blocks names it
#: beside the new one. Allowed only in the files listed; anywhere else an
#: old name is a write the game drops.
READS_OLD_NAMES = {
    # blockinfo holds the rename table itself, and reads an old name as the
    # block it became.
    "minecraft:chain": {"heaton_voxel/craft/blockinfo.py"},
    # The heightmap table too, since a chunk the game has not rewritten
    # still holds the old name, and no map counts short grass.
    "minecraft:grass": {"heaton_voxel/craft/blockinfo.py",
                        "heaton_voxel/heightmap_blocks.py",
                        "examples/castle.py"},
}


def _sources():
    for top in ("heaton_voxel", "examples"):
        for directory, _, files in os.walk(os.path.join(ROOT, top)):
            for name in sorted(files):
                if name.endswith(".py"):
                    path = os.path.join(directory, name)
                    yield os.path.relpath(path, ROOT), path


def _named_ids():
    """``{id: {file: first line}}`` for every ``minecraft:`` id in the code."""
    found: dict[str, dict[str, int]] = {}
    for relative, path in _sources():
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                for match in _ID.finditer(line):
                    where = found.setdefault(f"minecraft:{match.group(1)}", {})
                    where.setdefault(relative.replace(os.sep, "/"), number)
    return found


class BlockIdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blocks = {name for name, _, _ in block_shapes()}
        cls.named = _named_ids()

    def test_every_id_the_code_names_is_a_block_the_game_has(self):
        unknown = []
        for name, where in sorted(self.named.items()):
            if name in self.blocks or name in NOT_BLOCKS or name in OTHER_IDS:
                continue
            allowed = READS_OLD_NAMES.get(name, set())
            for path, line in sorted(where.items()):
                if path not in allowed:
                    unknown.append(f"{path}:{line} {name}")
        self.assertEqual(unknown, [], "names the game does not have; "
                                      "a retired one loads as air")

    def test_every_rename_has_its_allowances_and_no_more(self):
        self.assertEqual(sorted(READS_OLD_NAMES), sorted(RENAMED),
                         "one allowance list per rename blockinfo knows")

    def test_a_retired_name_is_read_beside_its_replacement(self):
        for old, (new, data_version) in RENAMED.items():
            self.assertIn(new, self.blocks, f"{new} must be the current name")
            self.assertNotIn(old, self.blocks, f"{old} is no longer retired")
            for path in sorted(READS_OLD_NAMES.get(old, ())):
                with self.subTest(old=old, path=path):
                    with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
                        text = handle.read()
                    self.assertTrue(old in text,
                                    "a stale allowance excuses nothing")
                    self.assertTrue(new in text,
                                    f"a table that reads {old} (before "
                                    f"DataVersion {data_version}) must read "
                                    f"{new} too")

    def test_every_other_id_listed_is_still_named(self):
        stale = sorted(name for name in OTHER_IDS if name not in self.named)
        self.assertEqual(stale, [], "an exception that excuses nothing is stale")


if __name__ == "__main__":
    unittest.main()
