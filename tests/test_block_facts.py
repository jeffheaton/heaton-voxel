"""Tests for heaton_voxel.craft.block_facts: the game's own answers, at run time.

``tools/probe_block_shapes.py`` asks the jar, state by state, how a block
holds, powers, lights and spawns, and writes the answers twice: into
``fixtures/block_shapes.json``, every state spelled out, and into
``craft.block_facts``, compressed for run time. Everything here holds the
second to the first, state by state, so a regeneration that lost a fact,
or a hand edit, fails by name.

The facts with no caller yet are pinned the same way, with the cases their
callers will lean on: the heightmaps step past snow and count leaves only
one way, a bare sea pickle is wet because its default state is, a rail is
no place to spawn because the game's tags were bound when it was asked,
and an id the game has never heard of is unknown rather than a cube. How
water moves through a state is held the same way: lava holds fluid and no
water, water and lava are themselves washed away, a double slab takes no
water, and a fence a block and a half tall shows the cell above it
nothing.

The same run vendors two game facts that are not about blocks, and they
are held here too: each dimension type's monster spawn light, and the
villager type each biome gives, which ``craft.biomes`` curates; and the
four tags a plant's footing is held to, with the one sugar cane asks of
the blocks beside its footing. Last, the fixture's own reader: it expands
the file once per content, and every call must still read the file as it
is now, its states its own.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import tempfile
import unittest

import fixtures
from fixtures import block_shapes
from heaton_voxel.block_defaults import BLOCK_DEFAULTS
from heaton_voxel.craft import biomes, block_facts
from heaton_voxel.craft.block_facts import facts

#: The blocks besides every stair and slab whose classes close faces to
#: light by their shape (``useShapeForLightOcclusion``): SnowLayerBlock,
#: FarmlandBlock, PathBlock, DaylightDetectorBlock, EnchantingTableBlock,
#: EndPortalFrameBlock, LecternBlock, SculkSensorBlock (both kinds),
#: SculkShriekerBlock, StonecutterBlock, PistonBaseBlock (both kinds) and
#: PistonHeadBlock. ShelfBlock's are every ``_shelf``.
SHAPED = {
    "minecraft:snow", "minecraft:farmland", "minecraft:dirt_path",
    "minecraft:daylight_detector", "minecraft:enchanting_table",
    "minecraft:end_portal_frame", "minecraft:lectern",
    "minecraft:sculk_sensor", "minecraft:calibrated_sculk_sensor",
    "minecraft:sculk_shrieker", "minecraft:stonecutter", "minecraft:piston",
    "minecraft:sticky_piston", "minecraft:piston_head",
}

DOWN, UP, NORTH, SOUTH, WEST, EAST = range(6)
UNKNOWN = "minecraft:some_block_from_next_year"


@functools.cache
def states() -> tuple:
    """Every state in the fixture, expanded once for the whole module."""
    return tuple(block_shapes())


class FactsTests(unittest.TestCase):
    def test_every_fact_is_the_games_for_every_state(self):
        wrong = []
        for name, props, shape in states():
            expected = (shape.sturdy, shape.center, shape.solid,
                        shape.conductor, shape.emission, shape.dampening,
                        shape.spawn_floor, shape.spawn_space, shape.faces)
            found = facts(name, props)
            # The facts after these, how water moves through it, are
            # FlowTests'.
            if tuple(found)[:len(expected)] != expected:
                wrong.append(f"{name}{props}: "
                             + ", ".join(field for field, a, b in
                                         zip(found._fields, found, expected)
                                         if a != b))
        self.assertEqual(wrong[:5], [], f"{len(wrong)} disagreements")

    def test_every_block_the_game_has_is_known_and_nothing_else(self):
        names = {name for name, _, _ in states()}
        self.assertEqual([name for name in sorted(names)
                          if not block_facts.known(name)], [])
        self.assertFalse(block_facts.known(UNKNOWN))
        self.assertFalse(block_facts.known("minecraft:chain"), "retired: air now")
        self.assertEqual(facts(UNKNOWN), block_facts.CUBE,
                         "still a cube to facts(): the safe guess for a wall")


class OcclusionTests(unittest.TestCase):
    def test_only_shaped_blocks_close_faces(self):
        tags = fixtures.block_tags()
        closing = {name for name, _, shape in states() if any(shape.faces)}
        shelves = {name for name in closing if name.endswith("_shelf")}
        self.assertEqual(closing - shelves,
                         tags["stairs"] | tags["slabs"] | SHAPED)
        self.assertEqual(len(shelves), 13)

    def test_a_slab_closes_the_face_its_solid_half_lies_on(self):
        full = block_facts.FULL_FACE
        bottom = facts("minecraft:oak_slab", {"type": "bottom"}).faces
        top = facts("minecraft:oak_slab", {"type": "top"}).faces
        self.assertEqual(bottom[DOWN], full)
        self.assertEqual(bottom[UP], 0)
        self.assertEqual(top[UP], full)
        self.assertEqual(top[DOWN], 0)
        for face in (NORTH, SOUTH, WEST, EAST):
            self.assertNotIn(bottom[face], (0, full), "half a side")
        self.assertEqual(facts("minecraft:oak_slab", {"type": "double"}).faces,
                         block_facts.NO_FACES, "a cube: its dampening stops light")
        self.assertEqual(facts("minecraft:stone").faces, block_facts.NO_FACES)

    def test_two_half_faces_close_the_gap_between_them(self):
        full = block_facts.FULL_FACE
        bottom = facts("minecraft:oak_slab", {"type": "bottom"}).faces
        top = facts("minecraft:oak_slab", {"type": "top"}).faces
        self.assertEqual(bottom[EAST] | top[WEST], full)
        self.assertNotEqual(bottom[EAST] | bottom[WEST], full,
                            "two bottom halves leave the top half open")

    def test_a_snow_layer_closes_its_underside(self):
        snow = facts("minecraft:snow", {"layers": "1"}).faces
        self.assertEqual(snow[DOWN], block_facts.FULL_FACE)
        self.assertEqual(snow[UP], 0)


class SpawnTests(unittest.TestCase):
    def assertSpawn(self, name, props=None, *, floor, space):
        found = facts(name, props)
        self.assertEqual((found.spawn_floor, found.spawn_space), (floor, space),
                         f"{name}{props or ''}: (floor, space)")

    def test_a_monster_stands_on_a_sturdy_top_in_open_air(self):
        self.assertSpawn("minecraft:stone", floor=True, space=False)
        self.assertSpawn("minecraft:air", floor=False, space=True)
        self.assertSpawn("minecraft:short_grass", floor=False, space=True)
        self.assertSpawn("minecraft:oak_slab", {"type": "top"}, floor=True,
                         space=True)
        self.assertSpawn("minecraft:oak_slab", {"type": "bottom"}, floor=False,
                         space=True)

    def test_glass_ice_leaves_and_bright_blocks_are_no_floor(self):
        for name in ("minecraft:glass", "minecraft:ice", "minecraft:oak_leaves",
                     "minecraft:glowstone", "minecraft:bedrock"):
            self.assertSpawn(name, floor=False, space=False)
        self.assertSpawn("minecraft:magma_block", floor=True, space=False)

    def test_the_spawn_space_was_read_with_the_games_tags_bound(self):
        # Under a bare Bootstrap every tag is empty, and a rail
        # (prevent_mob_spawning_inside) or a fire would read as room.
        self.assertSpawn("minecraft:rail", floor=False, space=False)
        self.assertSpawn("minecraft:fire", floor=False, space=False)
        self.assertSpawn("minecraft:campfire", floor=False, space=False)
        self.assertSpawn("minecraft:campfire", {"lit": "false"}, floor=False,
                         space=True)
        self.assertSpawn("minecraft:water", floor=False, space=False)

    def test_nothing_that_hurts_is_room_to_spawn(self):
        # isBlockDangerous names these four after the burning test.
        for name in ("minecraft:wither_rose", "minecraft:sweet_berry_bush",
                     "minecraft:cactus", "minecraft:powder_snow"):
            self.assertFalse(facts(name).spawn_space, name)
        self.assertTrue(facts("minecraft:poppy").spawn_space)

    def test_the_space_is_the_games_per_cell_test_and_no_more(self):
        # isValidEmptySpawnBlock asks only "not a full collision cube"; the
        # game then wants the mob's box clear of collision (noCollision on
        # getSpawnAABB), which no one state answers. So these pass here and
        # never spawn a monster in the game: a caller asking "can one spawn
        # in this cell" must decide them itself.
        for name, props in (("minecraft:oak_fence", None),
                            ("minecraft:cobblestone_wall", None),
                            ("minecraft:chest", None),
                            ("minecraft:oak_slab", {"type": "bottom"})):
            self.assertTrue(facts(name, props).spawn_space, name)


class HeightmapTests(unittest.TestCase):
    def test_heightmap_membership_is_the_games_for_every_block(self):
        wrong = []
        for name, _, shape in states():
            expected = (shape.heightmap != "none", shape.heightmap == "both")
            found = (block_facts.in_heightmap(name),
                     block_facts.in_heightmap(name, leaves=False))
            if found != expected:
                wrong.append(f"{name}: {found}, not {expected}")
        self.assertEqual(sorted(set(wrong))[:5], [])

    def test_only_leaves_count_one_way(self):
        tags = fixtures.block_tags()
        one_way = (tags["blocks_motion_in_heightmap"]
                   - tags["blocks_motion_in_heightmap_no_leaves"])
        self.assertEqual(one_way, tags["leaves"])
        self.assertTrue(block_facts.in_heightmap("minecraft:oak_leaves"))
        self.assertFalse(block_facts.in_heightmap("minecraft:oak_leaves",
                                                  leaves=False))

    def test_snow_and_water_are_not_in_the_tags(self):
        for name in ("minecraft:snow", "minecraft:water", "minecraft:torch",
                     "minecraft:short_grass", "minecraft:air"):
            self.assertFalse(block_facts.in_heightmap(name), name)
        for name in ("minecraft:ice", "minecraft:oak_fence", "minecraft:lantern",
                     "minecraft:stone"):
            self.assertTrue(block_facts.in_heightmap(name, leaves=False), name)

    def test_an_unknown_block_stops_a_fall_and_holds_no_water(self):
        self.assertTrue(block_facts.in_heightmap(UNKNOWN))
        self.assertTrue(block_facts.in_heightmap(UNKNOWN, leaves=False))
        self.assertFalse(block_facts.holds_fluid(UNKNOWN))
        self.assertFalse(block_facts.holds_fluid(UNKNOWN, {"waterlogged": "true"}))


class FluidTests(unittest.TestCase):
    def test_holds_fluid_is_the_games_for_every_state(self):
        wrong = [f"{name}{props}" for name, props, shape in states()
                 if block_facts.holds_fluid(name, props) != shape.fluid]
        self.assertEqual(wrong[:5], [], f"{len(wrong)} disagreements")

    def test_a_bare_id_holds_fluid_as_its_default_state_does(self):
        default = {}
        for name, props, shape in states():
            if props == BLOCK_DEFAULTS.get(name, {}):
                default[name] = shape.fluid
        wrong = [name for name, fluid in sorted(default.items())
                 if block_facts.holds_fluid(name) != fluid]
        self.assertEqual(wrong, [])
        self.assertTrue(block_facts.holds_fluid("minecraft:sea_pickle"),
                        "waterlogged by default")
        self.assertFalse(block_facts.holds_fluid(
            "minecraft:sea_pickle", {"pickles": "4", "waterlogged": "false"}))
        self.assertTrue(block_facts.holds_fluid("minecraft:sea_pickle",
                                                {"pickles": "4"}))

    def test_waterlogged_means_nothing_to_a_block_without_it(self):
        self.assertFalse(block_facts.holds_fluid("minecraft:stone",
                                                 {"waterlogged": "true"}))
        self.assertTrue(block_facts.holds_fluid("minecraft:water", {"level": "7"}))


class FlowTests(unittest.TestCase):
    """How water moves through a state, held to the game state by state:
    whether its fluid is water, whether water can enter the cell at all,
    whether the state is a container and takes a water source, and the
    faces of its collision shape that water's wall test sees."""

    def test_holds_water_is_the_games_for_every_state(self):
        wrong = [f"{name}{props}" for name, props, shape in states()
                 if block_facts.holds_water(name, props) != shape.water]
        self.assertEqual(wrong[:5], [], f"{len(wrong)} disagreements")
        held = {name for name, _, shape in states() if shape.water}
        self.assertNotIn("minecraft:lava", held)
        self.assertLessEqual(held, {name for name, _, shape in states()
                                    if shape.fluid}, "water is fluid")

    def test_flow_facts_are_the_games_for_every_state(self):
        wrong = []
        for name, props, shape in states():
            found = facts(name, props)
            expected = (shape.enters, shape.container, shape.takes_source,
                        shape.collision)
            got = (found.enters, found.container, found.takes_source,
                   found.collision)
            if got != expected:
                wrong.append(f"{name}{props}: " + ", ".join(
                    field for field, a, b in zip(
                        ("enters", "container", "takes_source", "collision"),
                        got, expected) if a != b))
        self.assertEqual(wrong[:5], [], f"{len(wrong)} disagreements")

    def test_lava_holds_fluid_and_no_water(self):
        for props in (None, {"level": "0"}, {"level": "7"}, {"level": "8"}):
            with self.subTest(props=props):
                self.assertTrue(block_facts.holds_fluid("minecraft:lava", props))
                self.assertFalse(block_facts.holds_water("minecraft:lava", props))
        for props in ({"level": "0"}, {"level": "7"}, {"level": "8"},
                      {"level": "15"}):
            self.assertTrue(block_facts.holds_water("minecraft:water", props),
                            f"a flow is water as much as a source: {props}")

    def test_a_bare_id_holds_water_as_its_default_state_does(self):
        default = {}
        for name, props, shape in states():
            if props == BLOCK_DEFAULTS.get(name, {}):
                default[name] = shape.water
        wrong = [name for name, water in sorted(default.items())
                 if block_facts.holds_water(name) != water]
        self.assertEqual(wrong, [])
        self.assertTrue(block_facts.holds_water("minecraft:sea_pickle"),
                        "waterlogged by default")
        self.assertFalse(block_facts.holds_water(
            "minecraft:sea_pickle", {"pickles": "4", "waterlogged": "false"}))
        # Each of these is water to the game's #water, which is what sugar
        # cane asks of the cell beside it.
        for name in ("minecraft:seagrass", "minecraft:kelp_plant",
                     "minecraft:bubble_column", "minecraft:tall_seagrass"):
            self.assertTrue(block_facts.holds_water(name), name)
        self.assertFalse(block_facts.holds_water("minecraft:stone",
                                                 {"waterlogged": "true"}))
        self.assertFalse(block_facts.holds_water(UNKNOWN))
        self.assertFalse(block_facts.holds_water("minecraft:frosted_ice"))

    def test_a_bare_id_has_the_flow_facts_its_default_state_does(self):
        # facts() fills an omitted property with its default before it
        # reads either table, so a bare slab is the bottom slab it places
        # as -- a container that takes a source, half a wall -- and never
        # the double one the flow table's commonest record would give.
        default = {}
        for name, props, shape in states():
            if props == BLOCK_DEFAULTS.get(name, {}):
                default[name] = (shape.enters, shape.container,
                                 shape.takes_source, shape.collision)
        wrong = [name for name, flow in sorted(default.items())
                 if tuple(facts(name)[-4:]) != flow]
        self.assertEqual(wrong[:5], [], f"{len(wrong)} disagreements")
        slab = facts("minecraft:oak_slab")
        self.assertEqual((slab.container, slab.takes_source), (True, True))
        self.assertEqual(slab.collision,
                         facts("minecraft:oak_slab", {"type": "bottom"}).collision)

    def test_water_and_lava_themselves_are_washed_away(self):
        # The game's washed_away_by_fluids holds water and lava, so a flow
        # beside a flow is somewhere water enters; sugar cane, cactus and a
        # door are in neither half of canHoldAnyFluid.
        for name, props in (("minecraft:water", {"level": "3"}),
                            ("minecraft:water", {"level": "0"}),
                            ("minecraft:lava", None), ("minecraft:air", None),
                            ("minecraft:cave_air", None),
                            ("minecraft:short_grass", None),
                            ("minecraft:torch", None), ("minecraft:snow", None)):
            self.assertTrue(facts(name, props).enters, name)
        for name in ("minecraft:sugar_cane", "minecraft:cactus",
                     "minecraft:oak_door", "minecraft:stone",
                     "minecraft:bubble_column"):
            self.assertFalse(facts(name).enters, name)

    def test_a_double_slab_takes_no_water_and_a_bottom_one_does(self):
        # SlabBlock.canPlaceLiquid: a source, and never into a double slab.
        for kind, takes in (("bottom", True), ("top", True), ("double", False)):
            found = facts("minecraft:oak_slab", {"type": kind})
            self.assertTrue(found.container, kind)
            self.assertTrue(found.enters, kind)
            self.assertEqual(found.takes_source, takes, kind)
        self.assertTrue(facts("minecraft:oak_stairs",
                              {"waterlogged": "true"}).takes_source,
                        "canPlaceLiquid asks the fluid, not the state")
        kelp = facts("minecraft:kelp")
        self.assertEqual((kelp.container, kelp.takes_source), (True, False))
        self.assertEqual(facts("minecraft:stone")[-4:],
                         (False, False, False, block_facts.FULL_FACES))
        self.assertEqual(facts(UNKNOWN), block_facts.CUBE)

    def test_only_a_container_takes_a_source_and_water_enters_every_one(self):
        wrong = [f"{name}{props}" for name, props, shape in states()
                 if (shape.takes_source and not shape.container)
                 or (shape.container and not shape.enters)]
        self.assertEqual(wrong, [])

    def test_collision_faces_close_to_water_where_the_shape_meets_them(self):
        full = block_facts.FULL_FACE
        self.assertEqual(facts("minecraft:stone").collision,
                         block_facts.FULL_FACES)
        self.assertEqual(facts("minecraft:air").collision, block_facts.NO_FACES)
        self.assertEqual(facts("minecraft:water").collision, block_facts.NO_FACES)
        bottom = facts("minecraft:oak_slab", {"type": "bottom"}).collision
        top = facts("minecraft:oak_slab", {"type": "top"}).collision
        self.assertEqual((bottom[DOWN], bottom[UP]), (full, 0))
        self.assertEqual((top[DOWN], top[UP]), (0, full))
        self.assertEqual(bottom[EAST] | top[WEST], full,
                         "a bottom and a top slab side by side hold water")
        self.assertNotEqual(bottom[EAST] | bottom[WEST], full)
        fence = facts("minecraft:oak_fence").collision
        self.assertEqual(fence[UP], 0, "a block and a half tall: past the face")
        self.assertNotIn(fence[DOWN], (0, full), "the post stands on its floor")
        chain = facts("minecraft:iron_chain", {"axis": "y"}).collision
        self.assertEqual(bin(chain[DOWN]).count("1"), 4,
                         "the sixteenths a chain covers whole")


class SupportTagTests(unittest.TestCase):
    """The four footing tags the same run vendors, each the jar's own
    definition over the tags vendored beside it: ``VegetationBlock``,
    ``AzaleaBlock``, ``DryVegetationBlock`` and ``SugarCaneBlock`` ask
    them of the block under a plant; and the block tag ``SugarCaneBlock``
    asks of the blocks beside the cane's footing, alongside the fluid tag
    of the same name, which is ``#water``."""

    def test_the_footing_tags_are_vendored_as_the_jar_defines_them(self):
        tags = fixtures.block_tags()
        vegetation = tags["supports_vegetation"]
        self.assertEqual(vegetation,
                         tags["substrate_overworld"] | {"minecraft:farmland"})
        self.assertEqual(tags["supports_azalea"],
                         vegetation | {"minecraft:clay"})
        self.assertEqual(tags["supports_dry_vegetation"],
                         tags["sand"] | tags["terracotta"] | vegetation)
        self.assertEqual(tags["supports_sugar_cane"],
                         tags["substrate_overworld"] | tags["sand"])
        self.assertNotIn("minecraft:farmland", tags["supports_sugar_cane"])
        self.assertNotIn("minecraft:white_glazed_terracotta",
                         tags["supports_dry_vegetation"])

    def test_the_probe_vendors_them(self):
        with open(fixtures.FIXTURE_BLOCK_SHAPES, encoding="utf-8") as handle:
            vendored = list(json.load(handle)["tags"])
        self.assertEqual(vendored[-5:], [
            "supports_vegetation", "supports_azalea",
            "supports_dry_vegetation", "supports_sugar_cane",
            "supports_sugar_cane_adjacently"],
            "appended after the tags vendored before them")

    def test_frosted_ice_waters_sugar_cane_and_no_other_block_does(self):
        # Beside the footing, the game asks the fluid for water and the
        # block for this tag: the one block that waters cane without
        # holding water, and none of the blocks that hold it.
        self.assertEqual(fixtures.block_tags()["supports_sugar_cane_adjacently"],
                         {"minecraft:frosted_ice"})
        self.assertFalse(block_facts.holds_water("minecraft:frosted_ice"))


class DimensionTypeTests(unittest.TestCase):
    def test_monster_spawn_light_is_the_games_per_dimension_type(self):
        rules = fixtures.dimension_types()
        self.assertEqual(rules["minecraft:overworld"],
                         {"block_light_limit": 0, "light_level_max": 7})
        self.assertEqual(rules["minecraft:the_nether"],
                         {"block_light_limit": 15, "light_level_max": 7})
        self.assertEqual(rules["minecraft:the_end"],
                         {"block_light_limit": 0, "light_level_max": 15})


class VillagerTypeTests(unittest.TestCase):
    """``craft.biomes``' villager table, entry for entry the game's: the
    probe reads ``VillagerType.BY_BIOME``, ``VillagerData.DEFAULT_TYPE``
    and the ``villager_type`` registry out of the jar into the fixture."""

    def test_villager_types_are_the_games(self):
        game = fixtures.villager_types()
        self.assertEqual(biomes.VILLAGER_TYPES, game.by_biome)
        self.assertEqual(biomes.VILLAGER_TYPE_DEFAULT, game.default)
        self.assertEqual(biomes.VILLAGER_KINDS, game.kinds)

    def test_every_typed_biome_is_one_the_map_knows_and_every_type_is_real(self):
        self.assertEqual(set(biomes.VILLAGER_TYPES) - set(biomes.BIOME_COLORS),
                         set())
        self.assertLessEqual(set(biomes.VILLAGER_TYPES.values()),
                             biomes.VILLAGER_KINDS)
        self.assertIn(biomes.VILLAGER_TYPE_DEFAULT, biomes.VILLAGER_KINDS)

    def test_a_biome_the_table_does_not_name_is_the_games_default(self):
        default = fixtures.villager_types().default
        for biome in ("minecraft:plains", "minecraft:forest",
                      "minecraft:the_void", "example:datapack_glacier"):
            with self.subTest(biome=biome):
                self.assertEqual(biomes.villager_type(biome), default)
        self.assertEqual(biomes.villager_type("minecraft:snowy_plains"),
                         "minecraft:snow")
        self.assertEqual(biomes.villager_type("minecraft:wooded_badlands"),
                         "minecraft:desert")


class FixtureReadingTests(unittest.TestCase):
    """``fixtures.block_shapes`` expands a content of the file once, and
    hands every call its states afresh: what one caller does to a state's
    properties is nothing the next one reads, and a file rewritten at the
    same path -- the probe reading back what it has just written -- is read
    as it now is. Each is held to the expansion uncached, over a few of
    the fixture's blocks, so as not to expand the whole of it again."""

    KEEP = (("minecraft:water", "minecraft:redstone_wire"),
            ("minecraft:water", "minecraft:lava"))

    def written(self, path, names):
        """``path`` rewritten as the fixture holding only ``names``, and the
        states the uncached expansion reads out of it."""
        with open(fixtures.FIXTURE_BLOCK_SHAPES, encoding="utf-8") as handle:
            document = json.load(handle)
        document["blocks"] = {name: document["blocks"][name] for name in names}
        raw = json.dumps(document).encode("utf-8")
        with open(path, "wb") as handle:
            handle.write(raw)
        return list(fixtures._expand(raw))

    def assertStates(self, found, expected):
        self.assertEqual(len(found), len(expected))
        for index, (got, want) in enumerate(zip(found, expected)):
            if got != want:
                self.fail(f"state {index}: {got} != {want}")

    def test_every_call_reads_the_file_as_it_is_now(self):
        directory = tempfile.mkdtemp(prefix="block-shapes-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = os.path.join(directory, "block_shapes.json")
        first_names, second_names = self.KEEP
        expected = self.written(path, first_names)
        self.assertTrue(any(props for _, props, _ in expected))
        first = list(block_shapes(path))
        self.assertStates(first, expected)
        for _, props, _ in first:
            props["meddled"] = "true"
            props.pop("level", None)
        self.assertStates(list(block_shapes(path)), expected)
        rewritten = self.written(path, second_names)
        self.assertNotEqual(rewritten, expected)
        self.assertStates(list(block_shapes(path)), rewritten)


if __name__ == "__main__":
    unittest.main()
