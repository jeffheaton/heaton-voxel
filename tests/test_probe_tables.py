"""Tests for the pure-Python half of tools/probe_block_shapes.py.

The probe's Java half needs the launcher's JDK and the installed jar, so
it runs on Jeff's machine after a game update and nowhere else. Its other
half -- reading the Java run's state table back, expanding tags out of the
jar's data files, reading the dimension types and the villager table,
sorting blocks into heightmaps, deciding which blocks are always fluid and
always water, and indexing the faces water's wall test sees -- is plain
Python over a zip and a list of states, and that half is where a refusal
could quietly become a silent drop: a tag that lost a member, a spawn
light rule read as some other number, a biome typing villagers as nothing
the game has, a fluid table that no longer says which states are wet, a
state table line short of a water fact or contradicting itself. Each
refusal is driven here with a jar or a table built in memory, so none of
this needs the game.
"""

from __future__ import annotations

import importlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

from fixtures import BlockShape

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_ROOT, "tools")


def _load_probe():
    # The probe imports its sibling generate_block_defaults by bare name,
    # as it does when run as a script; tools/ is on the path only for that.
    sys.path.insert(0, _TOOLS)
    try:
        return importlib.import_module("probe_block_shapes")
    finally:
        sys.path.remove(_TOOLS)


probe = _load_probe()


def jar(files: dict) -> zipfile.ZipFile:
    """A jar holding ``{path: JSON document}``, opened for reading."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, document in files.items():
            archive.writestr(path, json.dumps(document))
    return zipfile.ZipFile(io.BytesIO(buffer.getvalue()))


def tag(name: str, *values) -> dict:
    return {f"data/minecraft/tags/block/{name}.json": {"values": list(values)}}


def shape(*, fluid: bool = False, water: bool | None = None,
          collision: tuple = (0,) * 6) -> BlockShape:
    return BlockShape(top=16, body=True, rigid=True, falls=False, fluid=fluid,
                      water=fluid if water is None else water,
                      collision=collision)


BLOCKS = {"minecraft:oak_log", "minecraft:stripped_oak_log",
          "minecraft:crimson_stem", "minecraft:stone"}


class ReadTagsTests(unittest.TestCase):
    def refused(self, files: dict, names=("logs",)) -> str:
        with self.assertRaises(SystemExit) as caught:
            probe.read_tags(jar(files), names, BLOCKS)
        return str(caught.exception)

    def test_a_tag_is_expanded_through_the_tags_it_names(self):
        files = {**tag("logs", "#minecraft:oak_logs", "minecraft:crimson_stem"),
                 **tag("oak_logs", "minecraft:oak_log",
                       "minecraft:stripped_oak_log")}
        self.assertEqual(probe.read_tags(jar(files), ("logs", "oak_logs"),
                                         BLOCKS),
                         {"logs": {"minecraft:oak_log",
                                   "minecraft:stripped_oak_log",
                                   "minecraft:crimson_stem"},
                          "oak_logs": {"minecraft:oak_log",
                                       "minecraft:stripped_oak_log"}})

    def test_an_optional_member_the_game_lacks_is_skipped_as_the_game_does(self):
        files = tag("logs", {"id": "minecraft:mod_log", "required": False},
                    {"id": "#minecraft:mod_logs", "required": False},
                    "minecraft:oak_log")
        self.assertEqual(probe.read_tags(jar(files), ("logs",), BLOCKS),
                         {"logs": {"minecraft:oak_log"}})

    def test_a_tag_the_jar_lacks_is_refused(self):
        self.assertIn("the jar has no data/minecraft/tags/block/logs.json",
                      self.refused({}))

    def test_a_required_tag_it_names_and_the_jar_lacks_is_refused(self):
        found = self.refused(tag("logs", "#minecraft:oak_logs"))
        self.assertIn("oak_logs.json", found)

    def test_a_member_the_probe_never_saw_is_refused(self):
        found = self.refused(tag("logs", "minecraft:oak_log",
                                 "minecraft:quartz_wall"))
        self.assertIn("minecraft:quartz_wall", found)
        self.assertIn("never saw", found)

    def test_a_tag_that_includes_itself_is_refused(self):
        found = self.refused({**tag("logs", "#minecraft:oak_logs"),
                              **tag("oak_logs", "#minecraft:logs")})
        self.assertIn("includes itself", found)


def dimension(limit, level) -> dict:
    return {"monster_spawn_block_light_limit": limit,
            "monster_spawn_light_level": level, "has_skylight": True}


class DimensionTypeTests(unittest.TestCase):
    PATH = "data/minecraft/dimension_type/{}.json"
    UNIFORM = {"type": "minecraft:uniform", "max_inclusive": 7,
               "min_inclusive": 0}

    def test_a_number_and_a_uniform_draw_are_both_read(self):
        files = {self.PATH.format("overworld"): dimension(0, self.UNIFORM),
                 self.PATH.format("the_nether"): dimension(15, 7)}
        self.assertEqual(probe.read_dimension_types(jar(files)), {
            "minecraft:overworld": {"block_light_limit": 0,
                                    "light_level_max": 7},
            "minecraft:the_nether": {"block_light_limit": 15,
                                     "light_level_max": 7}})

    def test_a_light_rule_it_cannot_read_is_refused(self):
        weighted = {"type": "minecraft:weighted_list", "distribution": []}
        files = {self.PATH.format("overworld"): dimension(0, weighted)}
        with self.assertRaises(SystemExit) as caught:
            probe.read_dimension_types(jar(files))
        self.assertIn("cannot read", str(caught.exception))

    def test_a_jar_with_no_overworld_is_refused(self):
        files = {self.PATH.format("the_nether"): dimension(15, 7)}
        with self.assertRaises(SystemExit) as caught:
            probe.read_dimension_types(jar(files))
        self.assertIn("no overworld", str(caught.exception))


class VillagerTableTests(unittest.TestCase):
    """The Java run lists the villager types as ``default``, ``kind`` and
    ``biome`` lines; the fixture keeps them sorted, and anything a reader
    could only half understand is refused rather than vendored."""

    def table(self, *lines: str) -> str:
        directory = tempfile.mkdtemp(prefix="probe-villagers-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = os.path.join(directory, "villagers.tsv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("".join(f"{line}\n" for line in lines))
        return path

    def refused(self, *lines: str) -> str:
        with self.assertRaises(SystemExit) as caught:
            probe.read_villagers(self.table(*lines))
        return str(caught.exception)

    def test_the_villager_table_is_read_as_the_game_lists_it(self):
        path = self.table(
            "kind\tminecraft:snow", "default\tminecraft:plains",
            "biome\tminecraft:snowy_plains\tminecraft:snow",
            "kind\tminecraft:plains", "kind\tminecraft:desert",
            "biome\tminecraft:desert\tminecraft:desert",
            "biome\tminecraft:ice_spikes\tminecraft:snow")
        found = probe.read_villagers(path)
        self.assertEqual(found, {
            "by_biome": {"minecraft:desert": "minecraft:desert",
                         "minecraft:ice_spikes": "minecraft:snow",
                         "minecraft:snowy_plains": "minecraft:snow"},
            "default": "minecraft:plains",
            "kinds": ["minecraft:desert", "minecraft:plains",
                      "minecraft:snow"]})
        self.assertEqual(list(found["by_biome"]), sorted(found["by_biome"]),
                         "biomes in order, so the fixture never churns")

    def test_a_villager_table_it_cannot_read_whole_is_refused(self):
        kinds = ("kind\tminecraft:plains", "kind\tminecraft:snow")
        cases = {
            "not a default, a kind or a biome": (
                "default\tminecraft:plains", *kinds, "type\tminecraft:snow"),
            "is not a default": (
                "default\tminecraft:plains", *kinds,
                "biome\tminecraft:grove"),
            "a second default": (
                "default\tminecraft:plains", "default\tminecraft:snow", *kinds),
            "names no default": kinds,
            "listed twice": (
                "default\tminecraft:plains", *kinds,
                "biome\tminecraft:grove\tminecraft:snow",
                "biome\tminecraft:grove\tminecraft:plains"),
            "minecraft:taiga": (
                "default\tminecraft:plains", *kinds,
                "biome\tminecraft:taiga\tminecraft:taiga"),
            "minecraft:savanna": ("default\tminecraft:savanna", *kinds),
        }
        for words, lines in cases.items():
            with self.subTest(words):
                self.assertIn(words, self.refused(*lines))

    def test_a_biome_the_jar_does_not_ship_is_refused(self):
        typed = {"by_biome": {"minecraft:grove": "minecraft:snow"},
                 "default": "minecraft:plains",
                 "kinds": ["minecraft:plains", "minecraft:snow"]}
        shipped = {"data/minecraft/worldgen/biome/grove.json": {}}
        probe.check_villager_biomes(jar(shipped), typed)
        with self.assertRaises(SystemExit) as caught:
            probe.check_villager_biomes(jar({}), typed)
        self.assertIn("the jar has no data/minecraft/worldgen/biome/grove.json",
                      str(caught.exception))


class HeightmapMembershipTests(unittest.TestCase):
    def test_each_block_counts_both_ways_one_way_or_not_at_all(self):
        tags = {"blocks_motion_in_heightmap": {"minecraft:stone",
                                               "minecraft:oak_leaves"},
                "blocks_motion_in_heightmap_no_leaves": {"minecraft:stone"}}
        self.assertEqual(probe.heightmap_membership(
            ["minecraft:air", "minecraft:oak_leaves", "minecraft:stone"], tags),
            {"minecraft:air": "none", "minecraft:oak_leaves": "leaves",
             "minecraft:stone": "both"})

    def test_a_block_counted_only_without_leaves_is_refused(self):
        tags = {"blocks_motion_in_heightmap": set(),
                "blocks_motion_in_heightmap_no_leaves": {"minecraft:stone"}}
        with self.assertRaises(SystemExit) as caught:
            probe.heightmap_membership(["minecraft:stone"], tags)
        self.assertIn("minecraft:stone", str(caught.exception))


class FluidBlocksTests(unittest.TestCase):
    def test_always_fluid_and_wet_when_waterlogged_are_the_only_two_kinds(self):
        states = [
            ("minecraft:water", {"level": "0"}, shape(fluid=True)),
            ("minecraft:water", {"level": "7"}, shape(fluid=True)),
            ("minecraft:oak_slab", {"waterlogged": "true"}, shape(fluid=True)),
            ("minecraft:oak_slab", {"waterlogged": "false"}, shape()),
            ("minecraft:stone", {}, shape()),
        ]
        self.assertEqual(probe.fluid_blocks(states), {"minecraft:water"})

    def test_fluid_in_a_state_that_is_not_waterlogged_is_refused(self):
        states = [("minecraft:cauldron_like", {"level": "1"}, shape(fluid=True)),
                  ("minecraft:cauldron_like", {"level": "0"}, shape())]
        with self.assertRaises(SystemExit) as caught:
            probe.fluid_blocks(states)
        self.assertIn("minecraft:cauldron_like", str(caught.exception))

    def test_a_waterlogged_state_with_no_fluid_is_refused(self):
        # The check that summarize() made covered only blocks with some
        # fluid somewhere; this one covers every state of every block.
        states = [("minecraft:dry_slab", {"waterlogged": "true"}, shape()),
                  ("minecraft:dry_slab", {"waterlogged": "false"}, shape())]
        with self.assertRaises(SystemExit) as caught:
            probe.fluid_blocks(states)
        self.assertIn("holds no fluid", str(caught.exception))


class WaterBlocksTests(unittest.TestCase):
    def test_lava_is_fluid_and_neither_kind_of_water(self):
        states = [
            ("minecraft:water", {"level": "0"}, shape(fluid=True)),
            ("minecraft:water", {"level": "9"}, shape(fluid=True)),
            ("minecraft:lava", {"level": "0"}, shape(fluid=True, water=False)),
            ("minecraft:oak_slab", {"waterlogged": "true"}, shape(fluid=True)),
            ("minecraft:oak_slab", {"waterlogged": "false"}, shape()),
        ]
        self.assertEqual(probe.water_blocks(states), {"minecraft:water"})
        self.assertEqual(probe.fluid_blocks(states),
                         {"minecraft:water", "minecraft:lava"})

    def test_water_in_a_state_that_is_not_waterlogged_is_refused(self):
        states = [("minecraft:basin_like", {"level": "1"}, shape(fluid=True)),
                  ("minecraft:basin_like", {"level": "0"}, shape())]
        with self.assertRaises(SystemExit) as caught:
            probe.water_blocks(states)
        self.assertIn("minecraft:basin_like", str(caught.exception))
        self.assertIn("holds water", str(caught.exception))

    def test_a_waterlogged_state_holding_no_water_is_refused(self):
        states = [("minecraft:lava_slab", {"waterlogged": "true"},
                   shape(fluid=True, water=False)),
                  ("minecraft:lava_slab", {"waterlogged": "false"}, shape())]
        self.assertEqual(probe.fluid_blocks(states), frozenset())
        with self.assertRaises(SystemExit) as caught:
            probe.water_blocks(states)
        self.assertIn("holds no water", str(caught.exception))


FULL = "f" * 64
EMPTY = "0" * 64


def state_line(name="minecraft:oak_slab", props="type=bottom,waterlogged=true",
               *, fluid="1", water="1", enters="1", container="1",
               takes_source="1", collision=(FULL, EMPTY, EMPTY, EMPTY, EMPTY,
                                            EMPTY), drop=0) -> str:
    """One line of the Java run's state table, as it writes them: the shape
    facts, six light faces, the spawn facts, then how water moves through
    the state."""
    fields = [name, props, "8", "1", "0", "1", "1", "0", "0", "0", "0", "0",
              fluid, *[EMPTY] * 6, "0", "1", water, enters, container,
              takes_source, *collision]
    return "\t".join(fields[:len(fields) - drop])


class ReadStatesTests(unittest.TestCase):
    """The Java run's state table, read back. A table the reader could
    only half understand would hand the flow model facts the game never
    gave, so a short line and a contradiction are both refused by line."""

    def table(self, *lines: str) -> str:
        directory = tempfile.mkdtemp(prefix="probe-states-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = os.path.join(directory, "shapes.tsv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("".join(f"{line}\n" for line in lines))
        return path

    def refused(self, *lines: str) -> str:
        with self.assertRaises(SystemExit) as caught:
            probe.read_states(self.table(*lines))
        return str(caught.exception)

    def test_the_state_table_is_read_as_the_java_run_writes_it(self):
        half = "0" * 32 + "f" * 32
        (name, props, found), = probe.read_states(self.table(state_line(
            collision=(FULL, EMPTY, half, half, half, half))))
        self.assertEqual((name, props),
                         ("minecraft:oak_slab",
                          {"type": "bottom", "waterlogged": "true"}))
        self.assertEqual(found, BlockShape(
            top=8, body=True, rigid=False, falls=False, fluid=True, sturdy=1,
            center=1, spawn_space=True, water=True, enters=True,
            container=True, takes_source=True,
            collision=((1 << 256) - 1, 0, *[(1 << 128) - 1] * 4)))

    def test_a_record_missing_a_flow_fact_is_refused(self):
        found = self.refused(state_line(), state_line(drop=1))
        self.assertIn("line 2", found)
        self.assertIn(f"not {probe.STATE_FIELDS}", found)
        self.assertIn("line 1", self.refused(state_line(drop=7)))

    def test_a_flag_that_is_neither_0_nor_1_is_refused(self):
        found = self.refused(state_line(enters="yes"))
        self.assertIn("enters", found)
        self.assertIn("neither 0 nor 1", found)

    def test_flow_facts_that_contradict_each_other_are_refused(self):
        cases = {
            "takes a water source and is no container":
                state_line(container="0", takes_source="1"),
            "is a container water cannot enter":
                state_line(enters="0"),
            "holds water and no fluid":
                state_line(fluid="0", water="1"),
        }
        for words, line in cases.items():
            with self.subTest(words):
                found = self.refused(line)
                self.assertIn(words, found)
                self.assertIn("minecraft:oak_slab[type=bottom", found)


class CollisionTableTests(unittest.TestCase):
    WALL = ((1 << 256) - 1,) * 6

    def test_every_set_of_collision_faces_is_indexed_and_the_empty_one_first(self):
        half = (1 << 128) - 1
        states = [("minecraft:stone", {}, shape(collision=self.WALL)),
                  ("minecraft:air", {}, shape()),
                  ("minecraft:oak_slab", {"type": "bottom"},
                   shape(collision=(self.WALL[0], 0, half, half, half, half)))]
        masks, sets, index = probe.collision_table(states)
        self.assertEqual(masks, [0, half, self.WALL[0]])
        self.assertEqual(sets[0], (0,) * 6)
        self.assertEqual(index[self.WALL], sets.index((2,) * 6))

    def test_a_game_with_no_wall_to_water_is_refused(self):
        # The run-time table assumes a cube of every block it does not
        # list; a game with none would make that a guess.
        states = [("minecraft:air", {}, shape())]
        with self.assertRaises(SystemExit) as caught:
            probe.collision_table(states)
        self.assertIn("no block state is a wall to water", str(caught.exception))

    def test_a_game_where_nothing_lets_water_through_is_refused(self):
        states = [("minecraft:stone", {}, shape(collision=self.WALL))]
        with self.assertRaises(SystemExit) as caught:
            probe.collision_table(states)
        self.assertIn("shows water nothing", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
