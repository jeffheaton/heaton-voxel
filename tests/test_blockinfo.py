"""Tests for heaton_voxel.craft.blockinfo: what a block is, and whether the
game has it.

The game refuses nothing: an id it does not know loads as air, and a
property it does not take loads as the block's default, so a write of
either is gone the moment the chunk loads while every render still draws
it. ``check_state`` is the refusal the game never makes, and is held here
to every block state the game has.

The categories are a builder's judgments -- what to fell, what to clear,
what a column is made of underneath, which block a stair is cut from --
and each is held to the game's own block tags, vendored into
``fixtures/block_shapes.json`` by ``tools/probe_block_shapes.py``. Where
one departs from its tags, the departure has to be a choice blockinfo
names, with its reason, and every named choice has to still excuse
something: a game update that adds a log, a flower or a stair fails here
by name instead of being cleared around or drawn gray. The choices are
listed here as well, not read out of blockinfo, so that an edit to one of
its tables that changes a verdict -- water cleared as ground cover, a
dripstone falling -- fails here by name too.
"""

from __future__ import annotations

import ast
import os
import unittest
from unittest import mock

import fixtures
from fixtures import block_shapes
from heaton_voxel import blocks
from heaton_voxel.craft import IllegalBlock, Refused, block_facts, block_registry
from heaton_voxel.craft import blockinfo
from heaton_voxel.craft.block_registry import BLOCK_IDS
from heaton_voxel.craft.blockinfo import (
    RENAMED,
    check_state,
    falls,
    is_air,
    is_block,
    is_natural_ground,
    is_tree,
    is_vegetation,
    material_of,
    subsoil_of,
)
from heaton_voxel.blocks import BlockState
from heaton_voxel.nbt import NBTError


def _bare(names) -> list[str]:
    return sorted(name.removeprefix("minecraft:") for name in names)


def _ids(names) -> set[str]:
    return {f"minecraft:{name}" for name in names}


# Where each category departs from the game's tags: the verdicts, held here
# the way test_survey holds step_kind's LIGHTER and HEAVIER. blockinfo
# gives the reason for each beside its own table.

#: Trees the tags do not name: a huge mushroom, the nest a felled tree
#: leaves hanging in the air, and the heart a pale oak's trunk grows.
TREES_ADDED = {"bee_nest", "mushroom_stem", "brown_mushroom_block",
               "red_mushroom_block", "creaking_heart"}
#: What a growing tree writes over that clearing must leave: the sea.
VEGETATION_LEFT = {"water", "seagrass", "tall_seagrass"}
#: Ground cover no tag names: what today's site clearing already clears.
VEGETATION_ADDED = {"sweet_berry_bush", "sugar_cane", "pumpkin", "melon",
                    "brown_mushroom", "red_mushroom", "snow", "cactus",
                    "cactus_flower"}
#: What the ground tags hold that is a ruin's loot, not ground.
GROUND_LEFT = {"suspicious_sand"}
#: Ground no tag names: the beach, the riverbed, the desert's floor, the
#: snowy peaks, the dripstone caves, the soul sand valleys and the End.
GROUND_ADDED = {"gravel", "clay", "sandstone", "red_sandstone", "snow_block",
                "calcite", "dripstone_block", "soul_sand", "soul_soil",
                "end_stone"}
#: Ground that only lies on top, and what lies under it.
SKINS = {"grass_block": "dirt", "podzol": "dirt", "mycelium": "dirt"}
#: The game's falling blocks that hang instead.
HANGS = {"pointed_dripstone", "sulfur_spike"}
#: Cut shapes whose names do not lead to their blocks.
CUT_FROM = {"bamboo_slab": "bamboo_planks", "bamboo_stairs": "bamboo_planks",
            "petrified_oak_slab": "oak_planks"}

# Where craft's own source may still name an air, by module and by what the
# name sits in: the function, or the module-level table. Held to the source
# below, and a choice that excuses nothing fails as stale.

#: Tables that spell cave air or void air out, bare or whole: lists of
#: what is not air either, in which the airs are three entries.
AIRS_SPELLED = {
    ("survey.py", "_PASSABLE"):
        "what a body walks through, the airs among plants, rails and "
        "fluids, held state by state to the game's shapes by test_survey",
    ("survey.py", "_FALLS_THROUGH"):
        "what a falling block drops through, the game's replaceable tag: "
        "the airs among fluids, fire and plants",
}
#: Comparisons with plain air (``AIR`` or ``"minecraft:air"``). None is
#: left: the last, smooth_terrain's filler, asks subsoil_of now.
PLAIN_AIR_ASKED: dict[tuple[str, str], str] = {}


def _craft_sources():
    """Every hand-written module of craft, parsed, by its path under the
    package. The generated tables are the game's own answers, and name
    every block it has."""
    root = os.path.dirname(blockinfo.__file__)
    generated = {"block_registry.py", "block_facts.py"}
    for folder, _, files in sorted(os.walk(root)):
        for file in sorted(files):
            if not file.endswith(".py") or file in generated:
                continue
            path = os.path.join(folder, file)
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            yield os.path.relpath(path, root).replace(os.sep, "/"), tree


def _owned(node, owner="<module>"):
    """Every node under ``node``, with what it sits in: the innermost
    function or class around it, or the module-level name it is assigned
    to."""
    for child in ast.iter_child_nodes(node):
        mine = owner
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            mine = child.name
        elif owner == "<module>" and isinstance(child, (ast.Assign, ast.AnnAssign)):
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]
            mine = next((t.id for t in targets if isinstance(t, ast.Name)), owner)
        yield mine, child
        yield from _owned(child, mine)


def _plain_air(node) -> bool:
    """Whether ``node`` is plain air, or a literal collection holding it."""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(_plain_air(element) for element in node.elts)
    return ((isinstance(node, ast.Name) and node.id == "AIR")
            or (isinstance(node, ast.Attribute) and node.attr == "AIR")
            or (isinstance(node, ast.Constant) and node.value == "minecraft:air"))


class GameAnswerTests(unittest.TestCase):
    """What blockinfo gathers from the generated tables is theirs, not a copy."""

    def test_the_games_answers_are_the_generated_tables_own(self):
        self.assertIs(blockinfo.AIR_BLOCKS, blocks.AIR_BLOCKS)
        self.assertIs(blockinfo.FLUIDS, block_registry.FLUIDS)
        self.assertIs(blockinfo.holds_fluid, block_facts.holds_fluid,
                      "one holds_fluid, the generated one")
        self.assertIs(blockinfo.properties_of, block_registry.properties_of)
        self.assertEqual((blockinfo.GAME_VERSION, blockinfo.DATA_VERSION),
                         (block_registry.GAME_VERSION,
                          block_registry.DATA_VERSION))

    def test_holds_water_is_the_generated_one_wherever_it_is_imported(self):
        from heaton_voxel import craft
        self.assertIs(blockinfo.holds_water, block_facts.holds_water,
                      "one holds_water, the generated one")
        self.assertIs(craft.holds_water, block_facts.holds_water)
        self.assertIn("holds_water", blockinfo.__all__)
        self.assertIn("holds_water", craft.__all__)

    def test_air_is_the_games_air_and_a_block_is_one_it_has(self):
        for name in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air"):
            self.assertTrue(is_air(name), name)
        self.assertFalse(is_air("minecraft:stone"))
        self.assertTrue(is_block("minecraft:stone"))
        self.assertFalse(is_block("minecraft:quartz_wall"), "never existed")
        self.assertFalse(is_block("minecraft:chain"), "renamed: air now")
        self.assertEqual({name for name in BLOCK_IDS if is_block(name)},
                         {name for name, _, _ in block_shapes()})

    def test_craft_tests_emptiness_with_the_games_one_air_set(self):
        """Builders that asked for plain air refused a shaft through an old
        cave and hung lamps from its cave air, and readers that did counted
        it as furniture. So the writers and the readers share one
        predicate, build._empty, which is AIR_BLOCKS."""
        from heaton_voxel.craft.build import _empty
        for name in sorted(blocks.AIR_BLOCKS):
            self.assertTrue(_empty(BlockState(name)), name)
        for name in ("minecraft:stone", "minecraft:water"):
            self.assertFalse(_empty(BlockState(name)), name)

    def test_craft_asks_no_cell_whether_it_holds_plain_air(self):
        """The form craft's emptiness tests took while they saw a wall in
        every old cave: a name compared with ``AIR``. A *write* of ``AIR``
        is plain air and right; an ``==``, ``!=``, ``in`` or ``not in``
        against it is an emptiness test that names one air of three."""
        asked, used = [], set()
        for path, tree in _craft_sources():
            for owner, node in _owned(tree):
                if not (isinstance(node, ast.Compare)
                        and any(isinstance(op, (ast.Eq, ast.NotEq, ast.In,
                                                ast.NotIn, ast.Is, ast.IsNot))
                                for op in node.ops)
                        and any(map(_plain_air, (node.left, *node.comparators)))):
                    continue
                if (path, owner) in PLAIN_AIR_ASKED:
                    used.add((path, owner))
                else:
                    asked.append(f"{path}:{node.lineno} in {owner}")
        self.assertEqual(asked, [], "test emptiness with build._empty "
                         "(or AIR_BLOCKS), not against plain air")
        stale = sorted(set(PLAIN_AIR_ASKED) - used)
        self.assertEqual(stale, [], "a choice that excuses nothing is stale")

    def test_craft_spells_out_no_air_set_of_its_own(self):
        """craft once kept three sets of the airs beside the game's, and
        each was a second answer to drift from. A string naming cave air
        or void air, bare or whole, alone or in a table of names, is one;
        a docstring saying so is prose."""
        airs = {"cave_air", "void_air", "minecraft:cave_air", "minecraft:void_air"}
        spelled, used = [], set()
        for path, tree in _craft_sources():
            prose = {id(scope.body[0].value) for scope in ast.walk(tree)
                     if isinstance(scope, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                           ast.AsyncFunctionDef))
                     and scope.body and isinstance(scope.body[0], ast.Expr)
                     and isinstance(scope.body[0].value, ast.Constant)}
            for owner, node in _owned(tree):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and id(node) not in prose
                        and airs & set(node.value.split())):
                    continue
                if (path, owner) in AIRS_SPELLED:
                    used.add((path, owner))
                else:
                    spelled.append(f"{path}:{node.lineno} in {owner}")
        self.assertEqual(spelled, [], "test emptiness with AIR_BLOCKS (or "
                         "build._empty), not an air set of your own")
        stale = sorted(set(AIRS_SPELLED) - used)
        self.assertEqual(stale, [], "a choice that excuses nothing is stale")


class CheckStateTests(unittest.TestCase):
    def assertRefused(self, block, **kwargs) -> IllegalBlock:
        with self.assertRaises(IllegalBlock) as caught:
            check_state(block, **kwargs)
        return caught.exception

    def test_every_state_the_game_has_is_legal(self):
        states = 0
        for name, props, _ in block_shapes():
            check_state((name, props))
            states += 1
        self.assertGreater(states, 35000)

    def test_a_retired_name_is_refused_with_its_replacement(self):
        for old, (new, data_version) in sorted(RENAMED.items()):
            with self.subTest(old=old):
                self.assertNotIn(old, BLOCK_IDS, f"{old} is no longer retired")
                self.assertIn(new, BLOCK_IDS, f"{new} must be the current name")
                error = self.assertRefused(old)
                self.assertIsInstance(error, Refused)
                self.assertIsInstance(error, NBTError)
                self.assertIn(new, str(error))
                self.assertIn(str(data_version), str(error))
                self.assertEqual(error.suggestions[0], new)
        error = self.assertRefused("minecraft:chain[axis=y]")
        self.assertEqual(error.suggestions, ("minecraft:iron_chain",))
        self.assertEqual(RENAMED["minecraft:chain"],
                         ("minecraft:iron_chain", 4541))
        self.assertEqual(RENAMED["minecraft:grass"],
                         ("minecraft:short_grass", 3692))

    def test_an_invented_name_is_refused_with_near_names(self):
        error = self.assertRefused("minecraft:quartz_wall")
        self.assertIn("minecraft:quartz_slab", error.suggestions)
        self.assertIn("has no wall", str(error))
        self.assertIn("minecraft:quartz_stairs", str(error),
                      "names the cuts the block does come in")
        self.assertEqual(len(error.faults), 1)
        error = self.assertRefused("minecraft:polished_diorite_wall")
        self.assertEqual(error.suggestions[0], "minecraft:polished_diorite")
        self.assertIn("allow=", str(error), "names the way past it")

    def test_a_shape_named_from_the_wrong_word_is_told_the_right_one(self):
        error = self.assertRefused("minecraft:stone_bricks_slab")
        self.assertIn("minecraft:stone_brick_slab", error.suggestions)
        self.assertIn("is minecraft:stone_brick_slab", str(error))

    def test_a_cut_is_named_by_its_own_block_before_an_odd_one(self):
        # oak_slab and the petrified oak slab are both oak planks' slab; the
        # one named for its block is the one to suggest.
        error = self.assertRefused("minecraft:oak_planks_slab")
        self.assertEqual(error.suggestions[0], "minecraft:oak_slab")
        self.assertIn("is minecraft:oak_slab", str(error))
        error = self.assertRefused("minecraft:oak_wall")
        self.assertIn("comes as minecraft:oak_slab and minecraft:oak_stairs",
                      str(error))
        self.assertNotIn("petrified", str(error))
        # Wherever a shape has a cut named for its block, that is the one.
        cuts = {}
        for name in BLOCK_IDS:
            if material_of(name)[1] != "block":
                cuts.setdefault(material_of(name), set()).add(name)
        for base, shapes in sorted(blockinfo._cuts().items()):
            for shape, chosen in sorted(shapes.items()):
                plain = cuts[(base, shape)] - set(blockinfo._CUT_FROM)
                if plain:
                    self.assertIn(chosen, plain, f"{base} as {shape}")

    def test_a_bare_name_is_told_its_namespace(self):
        error = self.assertRefused("stone")
        self.assertEqual(error.suggestions, ("minecraft:stone",))
        self.assertIn("minecraft:stone", str(error))
        self.assertNotIn("as air", str(error),
                         "the game reads a bare name as minecraft's")
        self.assertNotIn("allow=", str(error), "the cause is known")
        error = self.assertRefused("air")
        self.assertEqual(error.suggestions, ("minecraft:air",))
        self.assertNotIn("as air", str(error))
        error = self.assertRefused(":stone")
        self.assertEqual(error.suggestions, ("minecraft:stone",),
                         "an empty namespace is minecraft's too")
        error = self.assertRefused("chain[axis=y]")
        self.assertEqual(error.suggestions, ("minecraft:iron_chain",),
                         "a bare retired name is told its replacement")
        self.assertIn("4541", str(error))
        error = self.assertRefused("quartz_wall")
        self.assertIn("minecraft:quartz_slab", error.suggestions)
        self.assertIn("allow=", str(error))

    def test_every_bad_property_is_a_fault(self):
        error = self.assertRefused("minecraft:oak_stairs[facing=up,hinge=left]")
        self.assertEqual(len(error.faults), 2)
        facing, hinge = error.faults
        self.assertIn("facing is one of east, north, south, west", facing)
        self.assertIn("'hinge'", hinge)
        self.assertIn("facing, half, shape, waterlogged", hinge)
        self.assertIn("1 more", str(error))
        self.assertEqual(error.suggestions, ())

    def test_a_property_the_block_lacks_is_refused(self):
        error = self.assertRefused("minecraft:stone[snowy=false]")
        self.assertIn("'snowy'", error.faults[0])

    def test_a_value_is_the_games_text(self):
        self.assertRefused(("minecraft:oak_stairs", {"waterlogged": True}))
        self.assertRefused(("minecraft:wheat", {"age": 3}))
        check_state(("minecraft:wheat", {"age": "3"}))

    def test_partial_properties_are_legal_and_left_as_given(self):
        text = "minecraft:oak_stairs[facing=east]"
        self.assertEqual(str(check_state(text)), text, "no defaults filled")
        state = BlockState("minecraft:oak_stairs", {"half": "top"})
        self.assertIs(check_state(state), state)
        self.assertEqual(check_state(("minecraft:stone", None)).name,
                         "minecraft:stone")

    def test_text_that_does_not_parse_is_still_a_value_error(self):
        with self.assertRaises(ValueError):
            check_state("minecraft:oak_stairs[facing")

    def test_allow_names_a_namespace_or_an_id(self):
        self.assertEqual(check_state("create:gearbox", allow=("create:",)).name,
                         "create:gearbox")
        self.assertEqual(check_state("create:gearbox", allow="create:").name,
                         "create:gearbox", "one entry may be given bare")
        new = "minecraft:new_block"
        self.assertEqual(check_state(new, allow=(new,)).name, new)
        self.assertRefused("minecraft:other_block", allow=(new,))
        self.assertRefused("create:gearbox", allow=("create:press",))
        for entry in ("gearbox", ":", ":gearbox", 7, "Create:", "create:Gearbox",
                      "create: gearbox", "create:gearbox[facing=up]"):
            with self.subTest(entry=entry):
                with self.assertRaises(NBTError) as caught:
                    check_state("create:gearbox", allow=(entry,))
                self.assertNotIsInstance(caught.exception, Refused,
                                         "a bad argument, not a refusal")


class CategoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tags = fixtures.block_tags()
        cls.ids = sorted(BLOCK_IDS)
        cls.sturdy = {name: block_facts.facts(name).sturdy for name in cls.ids}

    def assertNamed(self, table, expected):
        """blockinfo's table of choices is the list held here, each with
        its reason."""
        self.assertEqual(_bare(table), sorted(expected),
                         "blockinfo's choices are the ones this test holds")
        for name, why in table.items():
            reason = why[-1] if isinstance(why, tuple) else why
            self.assertTrue(isinstance(reason, str) and reason.strip(),
                            f"{name} needs its reason")

    def test_trees_are_the_games_logs_leaves_saplings_and_wart_blocks(self):
        tags = self.tags
        tagged = tags["logs"] | tags["leaves"] | tags["saplings"] | tags["wart_blocks"]
        found = {n for n in self.ids if is_tree(n)}
        self.assertEqual(_bare(found - tagged), sorted(TREES_ADDED),
                         "a tree the tags do not name must be a named choice")
        self.assertEqual(_bare(tagged - found), [],
                         "every log, leaf, sapling and wart block is a tree")
        stale = _bare(n for n in _ids(TREES_ADDED)
                      if n in tagged or n not in BLOCK_IDS)
        self.assertEqual(stale, [], "a choice that excuses nothing is stale")
        self.assertNamed(blockinfo._TREE_EXTRAS, TREES_ADDED)
        self.assertEqual(
            _bare(blockinfo._TREE_NAMES),
            _bare(n for n in tagged if not n.endswith(blockinfo._TREE_SUFFIXES)),
            "the names beside the suffixes are the tags' own, and no others")
        # The fences around the suffixes: each still keeps something out.
        for name in blockinfo._NOT_TREES:
            self.assertTrue(name.endswith(blockinfo._TREE_SUFFIXES)
                            and name in BLOCK_IDS and name not in tagged, name)
        self.assertTrue(any(n.startswith("minecraft:potted_")
                            and n.endswith(blockinfo._TREE_SUFFIXES)
                            for n in self.ids), "the potted_ fence is stale")
        self.assertFalse(is_tree("minecraft:bamboo_sapling"), "a bamboo shoot")
        self.assertTrue(is_tree("minecraft:creaking_heart"), "in a trunk")
        self.assertFalse(is_tree("minecraft:shroomlight"), "a lamp as well")
        self.assertTrue(is_tree("mymod:maple_log"), "a modded log is a tree")
        self.assertFalse(is_tree("mymod:potted_maple_sapling"))

    def test_vegetation_is_what_a_growing_tree_overwrites(self):
        tags = self.tags
        tagged = (tags["replaceable_by_trees"] | tags["small_flowers"]) - tags["leaves"]
        self.assertEqual(_bare(blockinfo._GROUND_COVER), _bare(tagged),
                         "the copy of the tags is the tags")
        found = {n for n in self.ids if is_vegetation(n)}
        self.assertEqual(_bare(found),
                         _bare((tagged - _ids(VEGETATION_LEFT))
                               | _ids(VEGETATION_ADDED)))
        stale = _bare([n for n in _ids(VEGETATION_LEFT) if n not in tagged]
                      + [n for n in _ids(VEGETATION_ADDED)
                         if n in tagged or n not in BLOCK_IDS])
        self.assertEqual(stale, [], "a choice that excuses nothing is stale")
        self.assertNamed(blockinfo._NOT_VEGETATION, VEGETATION_LEFT)
        self.assertNamed(blockinfo._VEGETATION_EXTRAS, VEGETATION_ADDED)
        for name in sorted(_ids(VEGETATION_LEFT)):
            self.assertFalse(is_vegetation(name),
                             f"{name}: clearing it to air drains the sea")
        self.assertEqual(_bare(n for n in found if is_tree(n)), [],
                         "nothing is both ground cover and a tree")
        for name in ("minecraft:red_tulip", "minecraft:leaf_litter",
                     "minecraft:firefly_bush", "minecraft:sunflower",
                     "minecraft:sugar_cane", "minecraft:snow"):
            self.assertTrue(is_vegetation(name), name)

    def test_subsoil_covers_the_games_soil_and_stone_and_never_an_ore(self):
        tags = self.tags
        tagged = (tags["dirt"] | tags["sand"] | tags["base_stone_overworld"]
                  | tags["base_stone_nether"] | tags["substrate_overworld"]
                  | tags["terracotta"])
        self.assertEqual(_bare(blockinfo._NATURAL_GROUND), _bare(tagged),
                         "the copy of the tags is the tags")
        ground = {n for n in self.ids if is_natural_ground(n)}
        self.assertEqual(_bare(ground),
                         _bare((tagged - _ids(GROUND_LEFT)) | _ids(GROUND_ADDED)))
        self.assertEqual({n.removeprefix("minecraft:"):
                          subsoil_of(n).removeprefix("minecraft:")
                          for n in ground if subsoil_of(n) != n}, SKINS,
                         "natural ground is its own subsoil but for the skins")
        stale = _bare([n for n in _ids(GROUND_LEFT) if n not in tagged]
                      + [n for n in _ids(GROUND_ADDED)
                         if n in tagged or n not in BLOCK_IDS])
        self.assertEqual(stale, [], "a choice that excuses nothing is stale")
        self.assertNamed(blockinfo._NOT_GROUND, GROUND_LEFT)
        self.assertNamed(blockinfo._GROUND_EXTRAS, GROUND_ADDED)
        self.assertNamed(blockinfo._SKINS, SKINS)
        for name in sorted(ground):
            with self.subTest(name=name):
                fill = subsoil_of(name)
                self.assertEqual(subsoil_of(fill), fill, "a subsoil is its own")
                self.assertEqual(self.sturdy[fill], 63, "a full block")
                bare = name.removeprefix("minecraft:")
                self.assertFalse(bare.endswith("_ore") or bare == "bedrock"
                                 or bare.startswith("suspicious_"))
        self.assertEqual(subsoil_of("minecraft:grass_block"), "minecraft:dirt")
        self.assertEqual(subsoil_of("minecraft:sand"), "minecraft:sand",
                         "a beach is sand all the way down")
        self.assertEqual(subsoil_of("minecraft:gravel"), "minecraft:gravel",
                         "a column of dirt under a riverbed is a scar")
        for name in ("minecraft:snow_block", "minecraft:calcite",
                     "minecraft:soul_soil", "minecraft:end_stone"):
            self.assertEqual(subsoil_of(name), name,
                             "dirt under a snowfield, a valley of souls or "
                             "the End is a scar as well")
        for name in ("minecraft:cave_air", "minecraft:diamond_ore",
                     "minecraft:water", "minecraft:bedrock",
                     "minecraft:suspicious_sand", "minecraft:stone_bricks"):
            self.assertIsNone(subsoil_of(name), name)

    def test_every_stair_slab_and_wall_is_cut_from_a_full_block(self):
        tags = self.tags
        for shape, tag in (("stairs", "stairs"), ("slab", "slabs"),
                           ("wall", "walls")):
            self.assertEqual(
                _bare(n for n in self.ids if material_of(n)[1] == shape),
                _bare(tags[tag]), f"the {shape} suffix is the game's {tag} tag")
        wrong = []
        for name in self.ids:
            base, shape = material_of(name)
            if shape == "block":
                self.assertEqual(base, name)
                continue
            if base not in BLOCK_IDS or self.sturdy[base] != 63 \
                    or material_of(base) != (base, "block"):
                wrong.append(f"{name} -> {base}")
        self.assertEqual(wrong, [])
        self.assertEqual({_bare([n])[0]: _bare([base])[0]
                          for n, base in blockinfo._CUT_FROM.items()}, CUT_FROM,
                         "blockinfo's overrides are the ones this test holds")
        for name, base in sorted(CUT_FROM.items()):
            self.assertEqual(material_of(f"minecraft:{name}")[0],
                             f"minecraft:{base}", name)
        # Each override is still needed: the name alone leads astray.
        overridden = sorted(blockinfo._CUT_FROM)
        with mock.patch.dict(blockinfo._CUT_FROM, clear=True):
            astray = sorted(name for name in overridden
                            if material_of(name)[0] not in BLOCK_IDS
                            or self.sturdy[material_of(name)[0]] != 63)
        self.assertEqual(astray, overridden,
                         "an override that excuses nothing is stale")
        self.assertEqual(material_of("minecraft:stone_brick_stairs"),
                         ("minecraft:stone_bricks", "stairs"))
        self.assertEqual(material_of("minecraft:oak_slab"),
                         ("minecraft:oak_planks", "slab"))
        self.assertEqual(material_of("minecraft:quartz_stairs"),
                         ("minecraft:quartz_block", "stairs"))
        self.assertEqual(material_of("mymod:marble_stairs"),
                         ("mymod:marble", "stairs"), "a modded cut keeps its stem")

    def test_cut_shapes_are_every_cut_the_game_has_to_its_block(self):
        """What render draws a cut in the color of, and the app's palette
        bakes: every stair, slab and wall the game's tags list, mapped to
        ``material_of``'s block, in sorted order, and a fresh dict on every
        call. A modded cut is not among them: its stem is only a guess."""
        tags = self.tags
        cuts = blockinfo.cut_shapes()
        self.assertEqual(sorted(cuts),
                         sorted(tags["stairs"] | tags["slabs"] | tags["walls"]))
        self.assertEqual(list(cuts), sorted(cuts), "in sorted id order")
        for shape, block in cuts.items():
            self.assertEqual(block, material_of(shape)[0], shape)
        self.assertEqual(cuts["minecraft:petrified_oak_slab"],
                         "minecraft:oak_planks")
        self.assertEqual(cuts["minecraft:quartz_slab"], "minecraft:quartz_block")
        self.assertNotIn("mymod:marble_stairs", cuts)
        cuts["minecraft:oak_stairs"] = "minecraft:stone"
        self.assertEqual(blockinfo.cut_shapes()["minecraft:oak_stairs"],
                         "minecraft:oak_planks", "a fresh dict every call")

    def test_every_form_is_a_cut_or_a_pane_of_a_full_block(self):
        """``form_of`` is ``material_of`` on every cut, and the panes are
        exactly the ``_pane`` ids -- no tag lists them -- each a form of a
        glass block; everything else is its own block, and ``_forms`` is
        the inverse a Material reads its family from."""
        form_of = blockinfo.form_of
        self.assertEqual(blockinfo.FORMS,
                         ("block", "stairs", "slab", "wall", "pane"))
        panes = []
        for name in self.ids:
            base, form = form_of(name)
            if material_of(name)[1] != "block":
                self.assertEqual((base, form), material_of(name), name)
            elif name.endswith("_pane"):
                panes.append(name)
                self.assertEqual(form, "pane", name)
                self.assertIn(base, BLOCK_IDS, name)
                self.assertTrue(base.endswith("glass"), f"{name} -> {base}")
                self.assertEqual(self.sturdy[base], 63, base)
            else:
                self.assertEqual((base, form), (name, "block"), name)
            if form != "block":
                self.assertIn(form, blockinfo._forms()[base],
                              f"{name}: {base}'s family has no {form}")
        self.assertEqual(len(panes), 17, "glass and the sixteen stained")
        for base, forms in blockinfo._forms().items():
            self.assertEqual(list(forms),
                             [f for f in blockinfo.FORMS if f in forms], base)
            for form, name in forms.items():
                self.assertEqual(form_of(name), (base, form), name)
        self.assertEqual(blockinfo._forms()["minecraft:oak_planks"]["slab"],
                         "minecraft:oak_slab",
                         "a form named for its block wins over an override")
        self.assertEqual(form_of("minecraft:glass_pane"),
                         ("minecraft:glass", "pane"))
        self.assertEqual(form_of("minecraft:lime_stained_glass_pane"),
                         ("minecraft:lime_stained_glass", "pane"))
        self.assertEqual(form_of("minecraft:chain"),
                         ("minecraft:iron_chain", "block"))

    def test_falls_is_the_games_fallable_less_what_hangs(self):
        game = {name for name, _, shape in block_shapes() if shape.falls}
        self.assertEqual(_bare(n for n in self.ids if falls(n)),
                         _bare(game - _ids(HANGS)))
        self.assertEqual(_bare(_ids(HANGS) - game), [],
                         "a choice that excuses nothing is stale")
        self.assertNamed(blockinfo._HANGS_FROM_ABOVE, HANGS)
        for name in ("minecraft:sand", "minecraft:gravel", "minecraft:anvil",
                     "minecraft:red_concrete_powder", "minecraft:dragon_egg"):
            self.assertTrue(falls(name), name)
        for name in sorted(_ids(HANGS)):
            self.assertFalse(falls(name), f"{name} falls when what it hangs "
                                          f"from goes")

    def test_every_category_reads_a_retired_name_as_the_block_it_became(self):
        self.assertTrue(is_vegetation("minecraft:grass"),
                        "a chunk saved before 1.20.3 still calls it that")
        self.assertEqual(material_of("minecraft:chain"),
                         ("minecraft:iron_chain", "block"),
                         "a chain out of an old chunk is the same material")
        # The game's own two renames are of blocks no other category takes,
        # so each category is shown one of its own.
        renames = {
            "minecraft:old_sand": ("minecraft:sand", 9001),
            "minecraft:old_tree": ("minecraft:crimson_stem", 9001),
            "minecraft:old_fern": ("minecraft:fern", 9001),
            "minecraft:old_slab": ("minecraft:oak_slab", 9001),
        }
        with mock.patch.dict(blockinfo.RENAMED, renames):
            self.assertTrue(falls("minecraft:old_sand"))
            self.assertEqual(subsoil_of("minecraft:old_sand"), "minecraft:sand")
            self.assertTrue(is_natural_ground("minecraft:old_sand"))
            self.assertTrue(is_tree("minecraft:old_tree"))
            self.assertTrue(is_vegetation("minecraft:old_fern"))
            self.assertEqual(material_of("minecraft:old_slab"),
                             ("minecraft:oak_planks", "slab"))
            for old in sorted(renames):
                self.assertFalse(is_block(old), old)
                self.assertRaises(IllegalBlock, check_state, old)
            with self.assertRaises(IllegalBlock) as caught:
                check_state("minecraft:old_tree[axis=y]")
            self.assertEqual(caught.exception.suggestions,
                             ("minecraft:crimson_stem",))


if __name__ == "__main__":
    unittest.main()
