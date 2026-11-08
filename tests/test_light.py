"""Tests for heaton_voxel.craft.light: block light, as the game will compute it.

The simulation answers questions nothing on disk can -- will this lit floor
stay bare, will this lake under its pickles thaw -- so its inputs have to be
the game's. Emission and dampening come from ``craft.block_facts``, which
``tools/probe_block_shapes.py`` generates from the jar, and are held here to
``fixtures/block_shapes.json`` for every one of the game's block states.
The hand table they replaced credited no copper lantern, no candle cake and
no firefly bush, and counted every flower as a wall.

The flood itself is held to the light the game stored in both fixture
regions, cell by cell. It once overshot that light at 66 cells of the
older one -- top slabs, stairs and the air under a stair roof -- because it
let light through every face a shape closes, and the game does not.

:func:`plan_lights` is the guarantee built on the flood, so its tests are
the cases a taxicab sum gets wrong: a wall between a lamp and the floor
behind it, a lamp already in the world, a furnace that will go out, and an
ice lid that must stay frozen.
"""

from __future__ import annotations

import functools
import importlib
import os
import random
import shutil
import tempfile
import unittest

from fixtures import (
    FIXTURE_LEVEL_DAT,
    FIXTURE_REGION,
    MODERN_LEVEL_DAT,
    MODERN_REGION,
    block_shapes,
    dimension_types,
)
from heaton_voxel.blocks import BlockState
from heaton_voxel.craft import light
from heaton_voxel.craft.errors import Refused
from heaton_voxel.craft.build import MELT_LIGHT
from heaton_voxel.craft.light import (
    MELT_ICE,
    MELT_SNOW,
    MONSTER_SPAWN_LIGHT,
    NO_NEW_SNOW_OR_ICE,
    NO_SPAWNS,
    DimensionLightLevel,
    LightLevel,
    LightPlan,
    durable_emission,
    light_dampening,
    light_emission,
    light_occlusion,
    plan_lights,
    simulate_block_light,
)
from heaton_voxel.craft.survey import MONSTER_SPAWN_LIGHT as SURVEY_SPAWN_LIGHT
from heaton_voxel.nbt import NBT, NBTError
from heaton_voxel.world import END, NETHER, OVERWORLD, MinecraftSave
from tests.test_world import make_save

GLOW = "minecraft:glowstone"
STONE = "minecraft:stone"
UNKNOWN = "minecraft:some_block_from_next_year"


@functools.cache
def states() -> tuple:
    """Every state in the fixture as ``(BlockState, shape)``, expanded once."""
    return tuple((BlockState(name, props), shape)
                 for name, props, shape in block_shapes())


class TableTests(unittest.TestCase):
    def test_emission_and_dampening_are_the_games_for_every_state(self):
        wrong = []
        for state, shape in states():
            if light_emission(state) != shape.emission:
                wrong.append(f"{state} emits {shape.emission}, "
                             f"not {light_emission(state)}")
            if light_dampening(state) != max(1, shape.dampening):
                wrong.append(f"{state} dampens {shape.dampening}, "
                             f"not {light_dampening(state)}")
        self.assertEqual(wrong[:5], [], f"{len(wrong)} disagreements")

    def test_the_faces_a_shape_closes_are_the_games_for_every_state(self):
        wrong = [str(state) for state, shape in states()
                 if light_occlusion(state) != shape.faces]
        self.assertEqual(wrong[:5], [], f"{len(wrong)} disagreements")

    def test_omitted_properties_take_their_defaults(self):
        self.assertEqual(light_emission("minecraft:campfire"), 15, "lit by default")
        self.assertEqual(light_emission("minecraft:redstone_lamp"), 0, "unlit")
        self.assertEqual(light_emission("minecraft:sea_pickle[pickles=4]"), 15)
        self.assertEqual(light_emission(
            "minecraft:sea_pickle[pickles=4,waterlogged=false]"), 0)

    def test_an_unknown_block_is_air_as_the_game_loads_it(self):
        # The game loads an id it does not have as air, and writes air
        # back: a wall here would be a wall the game never builds.
        self.assertEqual(light_emission(UNKNOWN), 0)
        self.assertEqual(light_dampening(UNKNOWN), 1)
        self.assertEqual(light_occlusion(UNKNOWN), (0,) * 6)
        self.assertEqual(light_dampening("minecraft:chain"), 1,
                         "a retired name is no block either")

    def test_an_id_with_no_namespace_is_minecrafts(self):
        # The game reads a bare id as minecraft's, so a bare stone is a
        # wall and a bare glowstone a lamp -- not the air of an unknown id.
        self.assertEqual(light_dampening("stone"), 15)
        self.assertEqual(light_dampening(":stone"), 15)
        self.assertEqual(light_emission("glowstone"), 15)
        self.assertEqual(light_occlusion("oak_slab[type=bottom]"),
                         light_occlusion("minecraft:oak_slab[type=bottom]"))
        self.assertEqual(durable_emission("furnace[lit=true]"), 0)
        self.assertEqual(durable_emission("copper_bulb[lit=true]"), 4)

    def test_light_that_does_not_last_is_not_counted(self):
        expected = {
            "minecraft:furnace[lit=true]": 0,
            "minecraft:smoker[lit=true]": 0,
            "minecraft:redstone_ore[lit=true]": 0,
            "minecraft:redstone_lamp[lit=true]": 0,
            "minecraft:copper_bulb[lit=true]": 4,
            "minecraft:exposed_copper_bulb[lit=true]": 4,
            "minecraft:waxed_copper_bulb[lit=true]": 15,
            "minecraft:oxidized_copper_bulb[lit=true]": 4,
            "minecraft:trial_spawner": 0,
            "minecraft:vault[vault_state=active]": 6,
            "minecraft:vault": 6,
            "minecraft:fire": 0,
            "minecraft:soul_fire": 10,
            "minecraft:lava": 15,
            "minecraft:lava[level=3]": 0,
            "minecraft:glowstone": 15,
            "minecraft:campfire": 15,
            "minecraft:redstone_torch": 7,
            "minecraft:candle[candles=4,lit=true]": 12,
            "minecraft:stone": 0,
            UNKNOWN: 0,
        }
        for state, level in expected.items():
            with self.subTest(state=state):
                self.assertEqual(durable_emission(state), level)
        louder = [str(state) for state, shape in states()
                  if shape.emission and durable_emission(state) > shape.emission]
        self.assertEqual(louder, [], "lasting light is never more than light")

    def test_every_emitting_block_is_judged_lasting_or_not(self):
        emitting = {state.name for state, shape in states() if shape.emission}
        settles, lasting = set(light._SETTLES), set(light._LASTING)
        self.assertEqual(sorted(emitting - settles - lasting), [],
                         "light sources neither table has judged")
        self.assertEqual(sorted(settles & lasting), [], "judged both ways")
        self.assertEqual(sorted((settles | lasting) - emitting), [],
                         "judged, and never gives off light: stale")


class FloodTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="light-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, [(0, 0)])
        self.save = MinecraftSave.load(self.dir)

    def box(self, x0, y0, z0, x1, y1, z1, block=STONE):
        self.save.fill((x0, y0, z0), (x1, y1, z1), block)

    def test_a_plan_that_replaces_a_lamp_takes_its_light_away(self):
        """A lamp standing in the world is a source only while the plan
        leaves it there: paved over, its light is gone from the flood, or
        a floor laid over the old lamp would be credited with light nothing
        gives off any more."""
        self.box(0, 60, 0, 15, 63, 15)
        self.save.set_block(8, 63, 8, GLOW)
        lit = simulate_block_light(self.save, (4, 64, 4), (12, 64, 12))
        self.assertEqual(lit.level(8, 64, 8), 14)
        dark = simulate_block_light(self.save, (4, 64, 4), (12, 64, 12),
                                    overlay={(8, 63, 8): STONE})
        self.assertEqual(dark.emitters, [])
        self.assertEqual(max(dark.level(x, 64, z) for x in range(4, 13)
                             for z in range(4, 13)), 0)

    def test_a_flood_aimed_at_a_box_lights_the_box_as_the_whole_flood_does(self):
        """plan_lights floods each candidate toward the targets it could
        still light, not the whole ball round it; through slabs, stairs,
        snow and walls, every cell of the box must come out exactly as the
        whole flood has it, at every floor."""
        rng = random.Random(17)
        mix = [STONE, "minecraft:air", "minecraft:air", "minecraft:air",
               "minecraft:oak_slab[type=bottom]", "minecraft:oak_slab[type=top]",
               "minecraft:oak_stairs[facing=east,half=bottom]",
               "minecraft:snow[layers=3]", "minecraft:glass"]
        for x in range(16):
            for y in range(60, 72):
                for z in range(16):
                    self.save.set_block(x, y, z, rng.choice(mix))
        for _ in range(6):
            self.save.set_block(rng.randrange(16), rng.randrange(60, 72),
                                rng.randrange(16), GLOW)
        field = simulate_block_light(self.save, (0, 60, 0), (15, 71, 15), margin=0)
        seeds = [(field._index(x, y, z), level) for x, y, z, level in field.emitters]
        self.assertEqual(len(seeds), 6)
        size = len(field._levels)
        for floor in (1, 6, 11):
            whole = bytearray(size)
            light._spread(field._w, field._d, field._damp, field._faces, seeds,
                          whole, floor)
            for _ in range(8):
                xs = sorted(rng.randrange(field._w) for _ in range(2))
                ys = sorted(rng.randrange(field._h) for _ in range(2))
                zs = sorted(rng.randrange(field._d) for _ in range(2))
                aimed, touched = bytearray(size), []
                light._spread_toward(field._w, field._d, field._damp, field._faces,
                                     seeds, aimed, floor, touched,
                                     (xs[0], ys[0], zs[0], xs[1], ys[1], zs[1]))
                for x in range(xs[0], xs[1] + 1):
                    for y in range(ys[0], ys[1] + 1):
                        for z in range(zs[0], zs[1] + 1):
                            index = (y * field._d + z) * field._w + x
                            self.assertEqual(aimed[index], whole[index],
                                             (floor, x, y, z))
                self.assertEqual(sorted(touched), [i for i in range(size) if aimed[i]])

    def test_light_goes_into_the_section_past_each_end_of_the_world(self):
        """The game keeps light one section past the top and the bottom of
        the world and reads a block there as air, so a lamp in the top
        layer lights the cell over it -- where a walker on a floor at the
        build limit stands -- and one in the bottom layer the cell under
        it. Past those sections nothing is kept, and nothing gets in."""
        self.save.set_block(2, 319, 2, GLOW)
        self.save.set_block(2, -64, 2, GLOW)
        top = simulate_block_light(self.save, (2, 318, 2), (2, 336, 2), margin=0)
        self.assertEqual(top.level(2, 320, 2), 14)
        self.assertEqual(top.level(2, 333, 2), 1)
        self.assertEqual(top.dampening(2, 335, 2), 1, "air, as the game reads it")
        self.assertEqual(top.dampening(2, 336, 2), 15, "no light is kept there")
        bottom = simulate_block_light(self.save, (2, -81, 2), (2, -63, 2), margin=0)
        self.assertEqual(bottom.level(2, -65, 2), 14)
        self.assertEqual(bottom.level(2, -78, 2), 1)
        self.assertEqual(bottom.dampening(2, -80, 2), 1)
        self.assertEqual(bottom.dampening(2, -81, 2), 15)

    def test_light_falls_one_a_block_and_stops_at_a_wall_not_a_flower(self):
        self.save.set_block(2, 64, 2, "minecraft:glowstone")
        self.save.set_block(6, 64, 2, "minecraft:stone")
        self.save.set_block(2, 64, 6, "minecraft:allium")
        field = simulate_block_light(self.save, (0, 64, 0), (10, 64, 10), margin=4)
        self.assertEqual(field.level(2, 64, 2), 15)
        self.assertEqual(field.level(4, 64, 2), 13)
        self.assertEqual(field.level(6, 64, 2), 0, "into a solid block: none")
        self.assertEqual(field.level(2, 64, 6), 11, "a flower is no wall")

    def test_a_plan_is_lit_before_it_is_written(self):
        plan = {(3, 64, 3): ("minecraft:copper_lantern", {})}
        field = simulate_block_light(self.save, (0, 64, 0), (6, 64, 6),
                                     overlay=plan, margin=2)
        self.assertEqual(field.level(3, 64, 3), 15)
        self.assertEqual(self.save.get_block(3, 64, 3).name, "minecraft:air")

    def test_a_bottom_slab_stops_light_coming_up_through_it_and_not_from_the_side(self):
        self.box(1, 62, 1, 3, 63, 3)
        self.save.set_block(2, 63, 2, GLOW)
        self.save.set_block(2, 64, 2, "minecraft:smooth_stone_slab[type=bottom]")
        field = simulate_block_light(self.save, (2, 64, 2), (2, 65, 2), margin=4)
        self.assertEqual(field.level(2, 64, 2), 0, "its solid half faces down")
        self.assertEqual(field.level(2, 65, 2), 0)
        # Only the closed face stops it: a slab is no wall from the side.
        self.save.set_block(6, 64, 2, GLOW)
        self.save.set_block(7, 64, 2, "minecraft:smooth_stone_slab[type=bottom]")
        field = simulate_block_light(self.save, (7, 64, 2), (7, 64, 2), margin=4)
        self.assertEqual(field.level(7, 64, 2), 14)

    def test_two_half_faces_close_the_gap_between_them(self):
        # A sealed one-wide tube along x at y=64: glowstone at one end, and
        # the only way on crosses from a bottom slab into a top slab.
        self.box(-1, 63, 1, 7, 65, 3)
        self.box(0, 64, 2, 6, 64, 2, "minecraft:air")
        self.save.set_block(0, 64, 2, GLOW)
        self.save.set_block(2, 64, 2, "minecraft:oak_slab[type=bottom]")
        self.save.set_block(3, 64, 2, "minecraft:oak_slab[type=top]")
        field = simulate_block_light(self.save, (0, 64, 2), (6, 64, 2), margin=4)
        self.assertEqual(field.level(2, 64, 2), 13, "into the bottom slab")
        for x in (3, 4, 5, 6):
            self.assertEqual(field.level(x, 64, 2), 0, f"x={x}: past the seam")

    def test_snow_lying_on_a_lamp_is_not_lit_from_below(self):
        self.box(0, 62, 0, 4, 63, 4)
        self.save.set_block(2, 63, 2, GLOW)
        self.save.set_block(2, 64, 2, "minecraft:snow[layers=1]")
        field = simulate_block_light(self.save, (2, 64, 2), (2, 64, 2), margin=4)
        self.assertEqual(field.level(2, 64, 2), 0, "a snow layer's underside is shut")

    def test_an_unknown_block_lets_light_through(self):
        self.save.set_block(2, 64, 2, GLOW)
        self.save.set_block(3, 64, 2, UNKNOWN)
        field = simulate_block_light(self.save, (3, 64, 2), (4, 64, 2), margin=4)
        self.assertEqual(field.level(3, 64, 2), 14)
        self.assertEqual(field.level(4, 64, 2), 13)

    def test_a_plan_with_bare_ids_floods_as_the_game_reads_it(self):
        plan = {(5, 64, 5): "glowstone", (6, 64, 5): "stone"}
        field = simulate_block_light(self.save, (5, 64, 5), (7, 64, 5),
                                     overlay=plan, margin=4)
        self.assertEqual(field.level(6, 64, 5), 0, "inside the stone")
        self.assertEqual(field.level(7, 64, 5), 11, "round it, not through it")

    def test_a_durable_flood_ignores_a_lit_furnace(self):
        self.save.set_block(2, 64, 2, "minecraft:furnace[facing=north,lit=true]")
        now = simulate_block_light(self.save, (2, 64, 2), (3, 64, 2), margin=4)
        lasting = simulate_block_light(self.save, (2, 64, 2), (3, 64, 2), margin=4,
                                       durable=True)
        self.assertEqual(now.level(3, 64, 2), 12)
        self.assertEqual(lasting.level(2, 64, 2), 0)
        self.assertEqual(lasting.level(3, 64, 2), 0)


class GameLightTests(unittest.TestCase):
    """The flood, held to the light the game itself stored.

    Every chunk of both fixtures the game finished and lit: at every cell
    the game stored light for, the flood is no brighter than the game, and
    the game is no brighter than the flood except by light that came from
    outside the sample -- at most ``15 - d``, ``d`` being the taxicab
    distance to the nearest column the sample does not hold.
    """

    def disagreements(self, region: str, level_dat: str) -> list[str]:
        directory = tempfile.mkdtemp(prefix="light-game-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        region_dir = os.path.join(directory, "dimensions/minecraft/overworld/region")
        os.makedirs(region_dir)
        shutil.copyfile(region, os.path.join(region_dir, os.path.basename(region)))
        shutil.copyfile(level_dat, os.path.join(directory, "level.dat"))
        save = MinecraftSave.load(directory)
        dim = save.dimension()
        lit = set()
        for chunk in NBT.load(region).chunks.values():
            data = chunk.data
            if (data.get("isLightOn") == 1
                    and str(data.get("Status")).removeprefix("minecraft:") == "full"):
                lit.add((int(data["xPos"]), int(data["zPos"])))
        self.assertTrue(lit, "the fixture has lit chunks")

        def sources(section) -> bool:
            blocks = section.blocks
            return blocks is not None and any(light_emission(s) for s in blocks.palette)

        wrong = []
        for cx, cz in sorted(lit):
            accessor = dim.blocks(cx, cz)
            # Light reaches at most 14 blocks, so a section either side of
            # every section holding light or a source -- here, or in a
            # neighbor the sample holds -- is all the flood must cover.
            busy = set()
            for sy, section in accessor.sections.items():
                stored = section.block_light()
                if (stored is not None and any(stored)) or sources(section):
                    busy.add(sy)
            for nx in (cx - 1, cx, cx + 1):
                for nz in (cz - 1, cz, cz + 1):
                    if (nx, nz) in lit and (nx, nz) != (cx, cz):
                        busy.update(sy for sy, section in dim.blocks(nx, nz).sections.items()
                                    if sources(section))
            field = None
            if busy:
                y0 = max(accessor.min_y, min(busy) * 16 - 16)
                y1 = min(accessor.max_y, max(busy) * 16 + 31)
                field = simulate_block_light(save, (cx * 16, y0, cz * 16),
                                             (cx * 16 + 15, y1, cz * 16 + 15))

            # How much light may come from outside the sample to each
            # column: 15 less the distance to the nearest column it lacks.
            outside = [0] * 256
            for i in range(256):
                lx, lz = i & 15, i >> 4
                best = 99
                for dx in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        if (dx or dz) and (cx + dx, cz + dz) not in lit:
                            far_x = {-1: lx + 1, 0: 0, 1: 16 - lx}[dx]
                            far_z = {-1: lz + 1, 0: 0, 1: 16 - lz}[dz]
                            best = min(best, far_x + far_z)
                outside[i] = max(0, 15 - best)

            for sy in sorted(accessor.sections):
                # The game writes no array for a section with no light in it.
                stored = accessor.sections[sy].block_light() or bytes(4096)
                flooded = field is not None and y0 <= sy * 16 and sy * 16 + 15 <= y1
                for i, level in enumerate(stored):
                    if not (level or flooded):
                        continue
                    lx, lz, y = i & 15, (i >> 4) & 15, sy * 16 + (i >> 8)
                    x, z = cx * 16 + lx, cz * 16 + lz
                    sim = field.level(x, y, z) if flooded else 0
                    if not sim <= level <= max(sim, outside[i & 255]):
                        wrong.append(f"({x}, {y}, {z}) {save.get_block(x, y, z)}: "
                                     f"the game stored {level}, the flood {sim}")
        return wrong

    def test_the_flood_agrees_with_the_light_the_game_stored(self):
        for name, region, level_dat in (
                ("26.2", FIXTURE_REGION, FIXTURE_LEVEL_DAT),
                ("26.3", MODERN_REGION, MODERN_LEVEL_DAT)):
            with self.subTest(fixture=name):
                wrong = self.disagreements(region, level_dat)
                self.assertEqual(wrong[:5], [], f"{len(wrong)} cells disagree")


class PlanTests(unittest.TestCase):
    """A stone floor at y=63 over x and z -16..31, air above it."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="light-plan-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, [(x, z) for x in (-1, 0, 1) for z in (-1, 0, 1)])
        self.save = MinecraftSave.load(self.dir)
        self.save.fill((-16, 63, -16), (31, 63, 31), STONE)

    @staticmethod
    def floor(x0, z0, x1, z1):
        """Walkers' cells over a rectangle, and a lamp in the paving under each."""
        targets = [(x, 64, z) for z in range(z0, z1 + 1) for x in range(x0, x1 + 1)]
        return targets, {(x, 63, z): GLOW for x, _, z in targets}

    def assertLitByTheFlood(self, plan, targets, level, overlay=None):
        field = simulate_block_light(self.save, min(targets), max(targets),
                                     overlay={**(overlay or {}), **plan.overlay},
                                     durable=True)
        self.assertEqual(field.below(targets, level), [])
        self.assertEqual(plan.field.below(targets, level), [])

    def test_every_target_ends_at_least_the_level_by_the_flood(self):
        targets, candidates = self.floor(0, 0, 11, 11)
        plan = plan_lights(self.save, targets, 12, candidates)
        self.assertIsInstance(plan, LightPlan)
        self.assertTrue(plan.ok)
        self.assertEqual((plan.dark, plan.blocked, plan.over), ([], [], []))
        self.assertGreater(len(plan.lamps), 0)
        self.assertEqual(plan.patched, len(plan.lamps), "no lattice was given")
        self.assertLitByTheFlood(plan, targets, 12)
        self.assertEqual(self.save.get_block(0, 63, 0).name, STONE, "nothing written")

    def test_what_the_plan_calls_dark_is_what_the_flood_finds_dark(self):
        """Lamps that change nothing about the world's shape -- glowstone
        set into stone -- under only a third of a floor: the plan says so,
        and its dark cells are exactly the cells an independent flood of
        the plan finds under the level, not an ok over a dark floor."""
        targets, candidates = self.floor(0, 0, 23, 11)
        within = {cell: block for cell, block in candidates.items() if cell[0] < 8}
        plan = plan_lights(self.save, targets, 12, within)
        field = simulate_block_light(self.save, min(targets), max(targets),
                                     overlay=plan.overlay, durable=True)
        dark = field.below(targets, 12)
        self.assertGreater(len(dark), 0)
        self.assertFalse(plan.ok)
        self.assertEqual(sorted(plan.dark), sorted(dark))

    def test_a_lattice_is_tried_first_and_patched_after(self):
        targets, candidates = self.floor(0, 0, 11, 11)
        plan = plan_lights(self.save, targets, 12, candidates,
                           lattice=lambda key: key[0] % 4 == 0 and key[2] % 4 == 0)
        self.assertGreater(plan.from_lattice, 0)
        self.assertEqual(plan.from_lattice + plan.patched, len(plan.lamps))
        on_lattice = [k for k in plan.lamps if k[0] % 4 == 0 and k[2] % 4 == 0]
        self.assertGreaterEqual(len(on_lattice), plan.from_lattice)
        self.assertLitByTheFlood(plan, targets, 12)

    def test_a_lattice_given_in_order_is_tried_in_that_order(self):
        """A lattice the caller planned in an order -- the Builder's, whose
        later lamps patch what the earlier left -- is tried in that order,
        each placed only for targets the ones before it left dark."""
        targets, candidates = self.floor(0, 0, 4, 0)
        middle, end = (2, 63, 0), (0, 63, 0)
        plan = plan_lights(self.save, targets, 12, candidates,
                           lattice=[middle, end])
        self.assertEqual(list(plan.lamps), [middle], "the end lit nothing left")
        self.assertEqual((plan.from_lattice, plan.patched), (1, 0))
        plan = plan_lights(self.save, targets, 12, candidates,
                           lattice=[end, middle])
        self.assertEqual(list(plan.lamps), [end, middle])
        self.assertEqual(plan.from_lattice, 2)
        by_key = plan_lights(self.save, targets, 12, candidates,
                             lattice=lambda key: key in (middle, end))
        self.assertEqual(list(by_key.lamps), [end, middle], "a predicate: key order")
        self.assertLitByTheFlood(plan, targets, 12)
        with self.assertRaises(NBTError) as caught:
            plan_lights(self.save, targets, 12, candidates, lattice=[(9, 63, 9)])
        self.assertIn("not a candidate", str(caught.exception))

    def test_a_lamp_goes_on_the_far_side_of_a_wall(self):
        self.save.fill((5, 64, -3), (5, 67, 3), STONE)
        targets = [(4, 64, 0), (6, 64, 0)]
        candidates = {(4, 63, 0): GLOW, (6, 63, 0): GLOW}
        plan = plan_lights(self.save, targets, 12, candidates)
        # Three taxicab blocks apart, so a taxicab sum lights both from
        # either lamp; the wall makes it ten.
        self.assertEqual(sorted(plan.lamps), [(4, 63, 0), (6, 63, 0)])
        self.assertTrue(plan.ok)
        self.assertLitByTheFlood(plan, targets, 12)

    def test_lamps_already_in_the_world_are_counted(self):
        self.save.set_block(2, 63, 2, GLOW)
        targets = [(2, 64, 2), (3, 64, 2), (2, 64, 3), (4, 64, 2)]
        plan = plan_lights(self.save, targets, 12, {(10, 63, 10): GLOW})
        self.assertEqual(plan.lamps, {})
        self.assertEqual(plan.lit_before, len(targets))
        self.assertTrue(plan.ok)

    def test_a_lit_furnace_in_the_world_is_not_counted(self):
        self.save.set_block(2, 64, 2, "minecraft:furnace[facing=north,lit=true]")
        targets = [(3, 64, 2)]
        self.assertEqual(simulate_block_light(self.save, (3, 64, 2), (3, 64, 2))
                         .level(3, 64, 2), 12, "lit to 12 as it stands")
        plan = plan_lights(self.save, targets, 12, {(3, 63, 2): GLOW})
        self.assertEqual(plan.lit_before, 0)
        self.assertEqual(list(plan.lamps), [(3, 63, 2)])

    def test_a_ceiling_is_held_by_the_taxicab_bound(self):
        lid = (0, 64, 0)
        self.save.set_block(*lid, "minecraft:ice")
        targets = [(1, 64, 0), (6, 64, 0)]
        candidates = {(x, 63, 0): GLOW for x in range(9)}
        plan = plan_lights(self.save, targets, 12, candidates, at_most={lid: 10})
        # A glowstone within four blocks of the lid would light it past 10.
        self.assertEqual(sorted(plan.excluded), [(x, 63, 0) for x in range(4)])
        self.assertIn("would light (0, 64, 0)", plan.excluded[(0, 63, 0)])
        self.assertLessEqual(plan.field.level(*lid), 10)
        self.assertEqual(plan.over, [])
        self.assertEqual([d[:3] for d in plan.dark], [(1, 64, 0)],
                         "only an excluded lamp could light it")
        self.assertFalse(plan.ok)

    def test_a_fixture_that_lets_light_through_is_held_back_under_a_ceiling(self):
        targets = [(6, 64, 0)]
        # Glass in place of a stretch of floor, far from the lamp it comes
        # with, opens the cell under it to whatever light the world has.
        candidates = {(6, 63, 0): {(6, 63, 0): GLOW,
                                   (24, 63, 0): "minecraft:glass"}}
        plan = plan_lights(self.save, targets, 12, candidates,
                           at_most={(24, 62, 0): 5})
        self.assertIn("would open (24, 63, 0)", plan.excluded[(6, 63, 0)])
        self.assertEqual(plan.lamps, {})
        self.assertEqual(plan_lights(self.save, targets, 12, candidates).lamps,
                         {(6, 63, 0): {(6, 63, 0): BlockState(GLOW),
                                       (24, 63, 0): BlockState("minecraft:glass")}},
                         "with no ceiling to hold, nothing is held back")

    def test_targets_inside_a_block_are_blocked_not_dark(self):
        self.save.set_block(5, 64, 5, STONE)
        targets, candidates = self.floor(3, 3, 7, 7)
        plan = plan_lights(self.save, targets, 12, candidates)
        self.assertEqual(plan.blocked, [(5, 64, 5)])
        self.assertNotIn((5, 64, 5), [d[:3] for d in plan.dark])
        self.assertTrue(plan.ok)

    def test_a_fixture_that_shades_an_earlier_lamp_is_caught_by_the_next_flood(self):
        # The lamp at (6, 63, 0) brings a stone that seals the one at
        # (0, 63, 0), which was chosen first; only a fresh flood sees it.
        targets = [(1, 64, 0), (6, 64, 0)]
        candidates = {
            (0, 63, 0): GLOW,
            (6, 63, 0): {(6, 63, 0): GLOW, (0, 64, 0): STONE},
            (2, 63, 0): GLOW,
        }
        plan = plan_lights(self.save, targets, 12, candidates)
        self.assertEqual(sorted(plan.lamps), [(0, 63, 0), (2, 63, 0), (6, 63, 0)])
        self.assertTrue(plan.ok)
        self.assertLitByTheFlood(plan, targets, 12)

    def test_the_plan_does_not_depend_on_candidate_order(self):
        self.save.fill((5, 64, -3), (5, 67, 3), STONE)
        targets, candidates = self.floor(0, -2, 9, 4)
        items = list(candidates.items())
        plans = []
        for seed in (1, 2):
            random.Random(seed).shuffle(items)
            plans.append(plan_lights(self.save, targets, 11, dict(items)))
        first, second = plans
        self.assertEqual(list(first.lamps.items()), list(second.lamps.items()))
        self.assertEqual(list(first.overlay.items()), list(second.overlay.items()))
        self.assertEqual(list(first.overlay), sorted(first.overlay))

    def test_bad_arguments_are_refused(self):
        targets, candidates = self.floor(0, 0, 2, 2)
        for level in (0, 16):
            with self.subTest(at_least=level), self.assertRaises(NBTError):
                plan_lights(self.save, targets, level, candidates)
        with self.assertRaises(NBTError):
            plan_lights(self.save, [], 12, candidates)
        with self.assertRaises(NBTError) as caught:
            plan_lights(self.save, targets, 12, candidates,
                        at_most={targets[0]: 11})
        self.assertIn("no plan can do both", str(caught.exception))
        for dark, says in (("minecraft:redstone_lamp[lit=true]", "does not last"),
                           (STONE, "no light")):
            with self.subTest(candidate=dark), self.assertRaises(NBTError) as caught:
                plan_lights(self.save, targets, 12, {(0, 63, 0): dark})
            self.assertIn("no lasting light", str(caught.exception))
            self.assertIn(says, str(caught.exception))

    def test_require_raises_refused_with_every_fault(self):
        self.save.set_block(20, 64, 20, GLOW)
        self.save.fill((9, 64, 9), (11, 66, 11), STONE)
        self.save.set_block(10, 65, 10, "minecraft:air")
        plan = plan_lights(self.save, [(10, 65, 10), (0, 64, 0)], 12,
                           {(0, 63, 0): GLOW}, at_most={(21, 64, 20): 10})
        self.assertEqual(plan.dark, [(10, 65, 10, 0)], "sealed in stone")
        self.assertEqual(plan.over, [(21, 64, 20, 14, 10)])
        with self.assertRaises(Refused) as caught:
            plan.require()
        self.assertEqual(caught.exception.faults, [(10, 65, 10, 0), (21, 64, 20, 14, 10)])
        self.assertIn("already", str(caught.exception))
        lit = plan_lights(self.save, [(0, 64, 0)], 12, {(0, 63, 0): GLOW})
        self.assertIs(lit.require(), lit)

    def test_a_ceiling_is_held_to_every_source_as_it_stands(self):
        # A lit furnace goes out on its first tick, so it lights nothing a
        # guarantee may count -- and until then it lights its neighbor to
        # 12, which a ceiling of 5 must hear about.
        self.save.set_block(2, 64, 2, "minecraft:furnace[facing=north,lit=true]")
        plan = plan_lights(self.save, [(20, 64, 20)], 12, {(20, 63, 20): GLOW},
                           at_most={(3, 64, 2): 5})
        self.assertEqual(plan.field.level(3, 64, 2), 0, "by the light that lasts")
        self.assertEqual(plan.over, [(3, 64, 2, 12, 5)])
        with self.assertRaises(Refused) as caught:
            plan.require()
        self.assertIn("as it already was before the plan", str(caught.exception))

    def test_a_fixture_that_disagrees_with_one_chosen_is_passed_over(self):
        # The first two fixtures want different blocks in the same cell, so
        # once the first is chosen the second cannot be, however well it
        # lights; the third lights its target instead.
        shared = (20, 64, 20)
        candidates = {
            (0, 63, 0): {(0, 63, 0): GLOW, shared: "minecraft:oak_planks"},
            (8, 63, 0): {(8, 63, 0): GLOW, shared: STONE},
            (9, 63, 0): GLOW,
        }
        plan = plan_lights(self.save, [(1, 64, 0), (8, 64, 0)], 12, candidates)
        self.assertEqual(sorted(plan.lamps), [(0, 63, 0), (9, 63, 0)])
        self.assertEqual(plan.overlay[shared], BlockState("minecraft:oak_planks"))
        self.assertTrue(plan.ok)

    def test_a_plan_is_made_over_the_overlay_it_is_given(self):
        # A wall the world does not hold yet still stands between a lamp and
        # the floor behind it, and a lamp it does not hold yet still lights.
        wall = {(5, y, z): STONE for y in range(64, 68) for z in range(-3, 4)}
        targets = [(4, 64, 0), (6, 64, 0)]
        candidates = {(4, 63, 0): GLOW, (6, 63, 0): GLOW}
        self.assertEqual(len(plan_lights(self.save, targets, 12, candidates).lamps), 1,
                         "with no wall, one lamp lights both")
        walled = plan_lights(self.save, targets, 12, candidates, overlay=wall)
        self.assertEqual(sorted(walled.lamps), [(4, 63, 0), (6, 63, 0)])
        self.assertLitByTheFlood(walled, targets, 12, overlay=wall)
        self.assertEqual(self.save.get_block(5, 64, 0).name, "minecraft:air")
        lamp = {(2, 63, 2): GLOW}
        lit = plan_lights(self.save, [(2, 64, 2), (3, 64, 2)], 12,
                          {(10, 63, 10): GLOW}, overlay=lamp)
        self.assertEqual((lit.lamps, lit.lit_before), ({}, 2))

    def test_a_plan_is_made_in_the_dimension_it_names(self):
        shutil.copytree(os.path.join(self.dir, "dimensions/minecraft/overworld"),
                        os.path.join(self.dir, "dimensions/minecraft/the_nether"))
        save = MinecraftSave.load(self.dir)
        save.set_block(2, 63, 2, GLOW, dimension=NETHER)
        targets = [(2, 64, 2), (3, 64, 2)]
        candidates = {(3, 63, 2): GLOW}
        nether = plan_lights(save, targets, 12, candidates, dimension=NETHER)
        self.assertEqual((nether.lamps, nether.lit_before), ({}, 2))
        overworld = plan_lights(save, targets, 12, candidates)
        self.assertEqual((list(overworld.lamps), overworld.lit_before),
                         ([(3, 63, 2)], 0))

    def test_no_spawns_is_read_for_the_dimension_the_plan_is_made_in(self):
        """``at_least=NO_SPAWNS`` plans to 1 in the overworld and to 8 in
        the nether, by the flood; a literal 1 is 1 in both. A missing level
        is a TypeError naming the rules, before the world is read."""
        shutil.copytree(os.path.join(self.dir, "dimensions/minecraft/overworld"),
                        os.path.join(self.dir, "dimensions/minecraft/the_nether"))
        save = MinecraftSave.load(self.dir)
        for dimension in (OVERWORLD, NETHER):
            save.fill((-16, 63, -16), (31, 63, 31), STONE, dimension=dimension)
        targets, candidates = self.floor(0, 0, 11, 11)
        for dimension, level in ((OVERWORLD, 1), (NETHER, 8)):
            with self.subTest(dimension=dimension):
                plan = plan_lights(save, targets, NO_SPAWNS, candidates,
                                   dimension=dimension)
                self.assertEqual(plan.at_least, level)
                self.assertIs(type(plan.at_least), int)
                self.assertTrue(plan.ok)
                field = simulate_block_light(save, min(targets), max(targets),
                                             overlay=plan.overlay, durable=True,
                                             dimension=dimension)
                self.assertEqual(field.below(targets, level), [])
        literal = plan_lights(save, targets, 1, candidates, dimension=NETHER)
        self.assertEqual(literal.at_least, 1)
        self.assertLess(len(literal.lamps), len(plan_lights(
            save, targets, NO_SPAWNS, candidates, dimension=NETHER).lamps))
        with self.assertRaises(TypeError) as caught:
            plan_lights(save, targets, None, candidates)
        self.assertIn("plan_lights needs at_least=", str(caught.exception))
        self.assertIn("NO_SPAWNS", str(caught.exception))


class NamedLevelTests(unittest.TestCase):
    """The light levels the game has a rule for, held to what the probe
    vendors from the jar: each dimension type's spawn light rule, and the
    light dampening of ice, which its melting subtracts. The 10 of
    ``Biome.shouldSnow``/``shouldFreeze`` and the 11 of
    ``SnowLayerBlock.randomTick`` are constants in the game's bytecode,
    read off the 26.3 jar with javap and not vendored, so the rule each
    level names is what is held for them."""

    def test_named_levels_are_the_games(self):
        self.assertEqual((NO_NEW_SNOW_OR_ICE, MELT_ICE, MELT_SNOW), (10, 11, 12))
        for level, rule in ((NO_NEW_SNOW_OR_ICE, "Biome.shouldSnow"),
                            (NO_NEW_SNOW_OR_ICE, "Biome.shouldFreeze"),
                            (MELT_ICE, "IceBlock.randomTick"),
                            (MELT_SNOW, "SnowLayerBlock.randomTick")):
            with self.subTest(level=level):
                self.assertIsInstance(level, LightLevel)
                self.assertIsInstance(level, int)
                self.assertIn(rule, level.rule)
                self.assertEqual(str(level), str(int(level)),
                                 "a message prints the number")
                self.assertEqual(repr(level), f"{level.name} ({int(level)})")
        # Ice melts over 11 less its own dampening (IceBlock.randomTick).
        ice = {shape.dampening for name, _, shape in block_shapes()
               if name == "minecraft:ice"}
        self.assertEqual({12 - dampening for dampening in ice}, {MELT_ICE})
        self.assertIs(MELT_LIGHT, MELT_SNOW, "the older name, the same object")

    def test_no_spawns_is_one_past_each_dimensions_spawn_light(self):
        rules = dimension_types()
        expected = {}
        for dimension in (OVERWORLD, NETHER, END):
            rule = rules[dimension]
            limit = rule["block_light_limit"]
            # Monster.isDarkEnoughToSpawn: block light over the limit
            # refuses a spawn, and with no limit (15) a light level over
            # the most monster_spawn_light_level draws does.
            expected[dimension] = min(limit if limit < 15 else 15,
                                      rule["light_level_max"]) + 1
        self.assertEqual(expected, {OVERWORLD: 1, NETHER: 8, END: 1})
        self.assertEqual({dimension: NO_SPAWNS.at(dimension)
                          for dimension in expected}, expected)
        self.assertEqual(NO_SPAWNS.by_dimension,
                         {dimension: limit + 1
                          for dimension, limit in MONSTER_SPAWN_LIGHT.items()})
        self.assertIs(SURVEY_SPAWN_LIGHT, MONSTER_SPAWN_LIGHT,
                      "survey reads the one table")
        with self.assertRaises(NBTError) as caught:
            NO_SPAWNS.at("modded:sky")
        self.assertIn("modded:sky", str(caught.exception))

    def test_no_spawns_is_no_number(self):
        """A literal 1 is 1 everywhere and NO_SPAWNS is 8 in the nether, so
        the one must not pass for the other."""
        self.assertIsInstance(NO_SPAWNS, DimensionLightLevel)
        self.assertNotIsInstance(NO_SPAWNS, int)
        self.assertNotEqual(NO_SPAWNS, 1)
        with self.assertRaises(TypeError) as caught:
            int(NO_SPAWNS)
        self.assertIn("8 in the nether", str(caught.exception))
        self.assertEqual(
            repr(NO_SPAWNS),
            "NO_SPAWNS (1 in the overworld and the end, 8 in the nether)")

    #: The named levels and their types, as API.md imports them.
    EXPORTED = ("NO_SPAWNS", "NO_NEW_SNOW_OR_ICE", "MELT_ICE", "MELT_SNOW",
                "LightLevel", "DimensionLightLevel")

    def test_the_levels_are_exported_where_a_build_imports_them(self):
        """A build script imports its level from ``heaton_voxel.craft``,
        or from ``craft.build`` beside ``MELT_LIGHT``, and each name there
        is the object ``craft.light`` holds, not a copy -- NO_SPAWNS is
        compared by identity. The packages are imported by name, because
        ``heaton_voxel.craft.survey`` is the survey function, not its
        module."""
        craft = importlib.import_module("heaton_voxel.craft")
        build = importlib.import_module("heaton_voxel.craft.build")
        for package in (craft, build):
            for name in self.EXPORTED:
                with self.subTest(package=package.__name__, name=name):
                    self.assertIs(getattr(package, name, None),
                                  getattr(light, name))
                    self.assertIn(name, package.__all__)
        self.assertIs(craft.MONSTER_SPAWN_LIGHT, MONSTER_SPAWN_LIGHT,
                      "the one table, wherever it is imported from")


if __name__ == "__main__":
    unittest.main()
