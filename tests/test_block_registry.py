"""Tests for heaton_voxel.craft.block_registry: every block the game has.

The game refuses nothing. An id it does not have loads as air, a property
a block does not take is dropped, and a log line nobody reads is the only
trace. The registry is the list the game would have checked against,
generated with the light and support facts by ``tools/probe_block_shapes.py``
from the jar, so it is held here to ``fixtures/block_shapes.json`` block by
block -- and to ``block_defaults``, which another generator writes from the
game's own report: two tables regenerated from two different games would
otherwise disagree in silence.
"""

from __future__ import annotations

import json
import unittest

import fixtures
from fixtures import FIXTURE_BLOCK_SHAPES, block_shapes
from heaton_voxel import block_defaults
from heaton_voxel.blocks import AIR_BLOCKS
from heaton_voxel.craft import block_facts, block_registry
from heaton_voxel.craft.block_registry import (
    BLOCK_IDS,
    FALLS,
    FLUIDS,
    WATER_FLUIDS,
    properties_of,
)


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.domains: dict[str, dict[str, set]] = {}
        cls.falls: dict[str, bool] = {}
        cls.fluid: dict[str, list[bool]] = {}
        cls.water: dict[str, list[bool]] = {}
        for name, props, shape in block_shapes():
            block = cls.domains.setdefault(name, {})
            for key, value in props.items():
                block.setdefault(key, set()).add(value)
            cls.falls[name] = shape.falls
            cls.fluid.setdefault(name, []).append(shape.fluid)
            cls.water.setdefault(name, []).append(shape.water)

    def test_the_registry_is_every_block_the_game_has(self):
        self.assertEqual(BLOCK_IDS, set(self.domains))
        wrong = [name for name, domains in sorted(self.domains.items())
                 if {key: set(values) for key, values in
                     properties_of(name).items()} != domains]
        self.assertEqual(wrong, [], "property domains the game does not give")

    def test_values_ascend_as_numbers_where_they_are_numbers(self):
        wire = properties_of("minecraft:redstone_wire")
        self.assertEqual(wire["power"], tuple(str(n) for n in range(16)))
        self.assertEqual(wire["east"], ("none", "side", "up"))
        self.assertEqual(list(wire), sorted(wire), "properties in name order")

    def test_an_unknown_id_is_none_and_a_bare_block_is_empty(self):
        self.assertIsNone(properties_of("minecraft:quartz_wall"))
        self.assertIsNone(properties_of("minecraft:chain"), "retired: air now")
        self.assertEqual(properties_of("minecraft:stone"), {})
        found = properties_of("minecraft:oak_stairs")
        found["facing"] = ("up",)
        self.assertNotEqual(properties_of("minecraft:oak_stairs")["facing"],
                            ("up",), "a caller's copy is its own")

    def test_the_registry_and_the_defaults_table_are_one_game(self):
        with open(FIXTURE_BLOCK_SHAPES, encoding="utf-8") as handle:
            document = json.load(handle)
        versions = {
            "block_defaults": (block_defaults.GAME_VERSION,
                               block_defaults.DATA_VERSION),
            "block_facts": (block_facts.GAME_VERSION, block_facts.DATA_VERSION),
            "block_registry": (block_registry.GAME_VERSION,
                               block_registry.DATA_VERSION),
            "fixture": (document["game"], document["data_version"]),
        }
        self.assertEqual(len(set(versions.values())), 1, versions)
        defaults = block_defaults.BLOCK_DEFAULTS
        self.assertEqual(set(defaults),
                         {name for name in BLOCK_IDS if properties_of(name)},
                         "the blocks with properties")
        wrong = []
        for name, chosen in sorted(defaults.items()):
            domains = properties_of(name) or {}
            if set(chosen) != set(domains):
                wrong.append(f"{name}: defaults name {sorted(chosen)}")
            wrong += [f"{name}: default {key}={value} is not a value it takes"
                      for key, value in sorted(chosen.items())
                      if value not in domains.get(key, ())]
        self.assertEqual(wrong, [])

    def test_falls_are_the_games_fallable_blocks(self):
        self.assertEqual(FALLS, {name for name, falls in self.falls.items()
                                 if falls})
        self.assertIn("minecraft:red_concrete_powder", FALLS)
        self.assertIn("minecraft:pointed_dripstone", FALLS,
                      "falls when what it hangs from goes")

    def test_fluids_are_the_blocks_every_state_of_which_holds_fluid(self):
        self.assertEqual(FLUIDS, {name for name, held in self.fluid.items()
                                  if all(held)})
        self.assertEqual(FLUIDS, {
            "minecraft:water", "minecraft:lava", "minecraft:bubble_column",
            "minecraft:kelp", "minecraft:kelp_plant", "minecraft:seagrass",
            "minecraft:tall_seagrass"})

    def test_water_fluids_are_the_blocks_every_state_of_which_holds_water(self):
        self.assertEqual(WATER_FLUIDS, {name for name, held in self.water.items()
                                        if all(held)})
        self.assertEqual(WATER_FLUIDS, FLUIDS - {"minecraft:lava"},
                         "every fluid but lava, at every level")

    def test_air_is_the_games_air_tag(self):
        self.assertEqual(AIR_BLOCKS, fixtures.block_tags()["air"])

    def test_tags_are_expanded_to_blocks_the_game_has(self):
        tags = fixtures.block_tags()
        self.assertEqual({name: sorted(members - BLOCK_IDS)
                          for name, members in tags.items()
                          if members - BLOCK_IDS}, {})
        self.assertGreater(len(tags["logs"]), 40, "expanded through #logs_that_burn")
        self.assertIn("minecraft:crimson_stem", tags["logs"])


if __name__ == "__main__":
    unittest.main()
