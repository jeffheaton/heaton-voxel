"""Tests for heaton_voxel.craft.style: Mix, Material and Palette.

A Mix is held to being a pure function of position -- the same pick for a
cell however, whenever and by whomever it is asked -- and to keeping its
weights, with and without clumping. A Material is held to the game's own
block list, its families read the way ``blockinfo.material_of`` reads a
cut. A Palette is held to the eight legacy writers' old defaults, and each
role to the writer argument that reads it, so the vocabulary cannot drift
from the writers it names; and a Builder with no palette, with
``Palette()``, or given every old literal outright builds the same bytes.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import os
import pickle
import random
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import heaton_voxel.craft as craft  # noqa: E402
from heaton_voxel.blocks import BlockState  # noqa: E402
from heaton_voxel.craft import build, style  # noqa: E402
from heaton_voxel.craft._plan import as_state, read_overlay  # noqa: E402
from heaton_voxel.craft.block_registry import BLOCK_IDS  # noqa: E402
from heaton_voxel.craft.blockinfo import RENAMED, check_state, form_of  # noqa: E402
from heaton_voxel.craft.build import (  # noqa: E402
    Builder,
    Railway,
    SpiralStair,
    StraightStair,
    SwitchbackStair,
    Vault,
    lighting,
    site,
)
from heaton_voxel.craft.build._common import _state  # noqa: E402
from heaton_voxel.craft.build.lighting import LightingMixin  # noqa: E402
from heaton_voxel.craft.build.shells import ShellsMixin  # noqa: E402
from heaton_voxel.craft.build.site import SiteMixin  # noqa: E402
from heaton_voxel.craft.errors import IllegalBlock, Refused  # noqa: E402
from heaton_voxel.craft.light import (  # noqa: E402
    MELT_SNOW,
    light_emission,
    simulate_block_light,
)
from heaton_voxel.craft.style import (  # noqa: E402
    DEFAULT_PALETTE,
    FROM_PALETTE,
    LANE_MIX,
    PLAZA_MIX,
    ROAD_MIX,
    ROLES,
    Material,
    Mix,
    Palette,
)
from heaton_voxel.nbt import NBTError, _decompress  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402
from heaton_voxel.world.dimension import Dimension  # noqa: E402
from tests.test_blocks import SequenceAssertions  # noqa: E402
from tests.test_world import make_save  # noqa: E402

STONE = "minecraft:stone"
ANDESITE = "minecraft:andesite"


def _grid(mix: Mix, n: int, y: int = 63) -> dict[tuple[int, int], object]:
    return {(x, z): mix.pick(x, y, z) for z in range(n) for x in range(n)}


def _same_neighbors(grid, n: int) -> float:
    same = sum(grid[(x, z)] == grid[(x + 1, z)]
               for z in range(n) for x in range(n - 1))
    same += sum(grid[(x, z)] == grid[(x, z + 1)]
                for z in range(n - 1) for x in range(n))
    return same / (2 * n * (n - 1))


class MixTests(SequenceAssertions):
    """Picks over thousands of cells are compared with
    ``assertSequencesEqual``, which names the first cell that differs:
    ``assertEqual`` on two differing lists that long spends minutes
    building a diff nobody reads."""

    def test_a_mix_is_a_pure_function_of_position(self):
        cells = [(x, y, z) for x in range(-12, 13) for y in (-64, 63, 64)
                 for z in range(-12, 13)]
        for clump in (1, 3):
            mix = Mix(ROAD_MIX, seed=7, clump=clump)
            forward = {cell: mix.pick(*cell) for cell in cells}
            backward = {cell: mix.pick(*cell) for cell in reversed(cells)}
            shuffled = list(cells)
            random.Random(clump).shuffle(shuffled)
            mixed = {cell: mix.pick(*cell) for cell in shuffled}
            twin = Mix(list(ROAD_MIX), seed=7, clump=clump)
            expected = [forward[cell] for cell in cells]
            self.assertSequencesEqual([backward[cell] for cell in cells],
                                      expected, f"backward, clump {clump}")
            self.assertSequencesEqual([mixed[cell] for cell in cells],
                                      expected, f"shuffled, clump {clump}")
            self.assertSequencesEqual([twin.pick(*cell) for cell in cells],
                                      expected, f"an equal Mix, clump {clump}")
            self.assertTrue(any(forward[(x, 63, z)] != forward[(x, 64, z)]
                                for x in range(-12, 13) for z in range(-12, 13)),
                            "y is part of the cell")

    def test_no_session_or_random_stream_moves_a_pick(self):
        """Two Builders with different seeds, drawing from their own rng
        between picks, and the global random stream reseeded: the picks are
        the ones a bare Mix gives."""
        directory = tempfile.mkdtemp(prefix="style-tests-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        make_save(directory, [(0, 0)])
        save = MinecraftSave.load(directory)
        mix = Mix(PLAZA_MIX, clump=2)
        cells = [(x, 64, z) for x in range(16) for z in range(16)]
        bare = [mix.pick(*cell) for cell in cells]
        for seed in (1, 2):
            builder = Builder(save, seed=seed)
            random.seed(seed * 1000)
            picks = []
            for cell in cells:
                builder.rng.random()
                random.random()
                picks.append(mix.pick(*cell))
            self.assertSequencesEqual(picks, bare, f"Builder(seed={seed})")

    def test_the_picks_are_pinned(self):
        # Computed when Mix was written: the key (the seed and the CRC of
        # the entries), the patch and the weighted lookup, end to end.
        names = [name for name, _ in ROAD_MIX]
        row = [(x, 64, -5) for x in range(-16, 16)]
        for mix, picks in (
                (Mix(ROAD_MIX), "23122400101113013211011000211222"),
                (Mix(ROAD_MIX, seed=4211, clump=3),
                 "12222002222000112222001100111111")):
            self.assertEqual("".join(str(names.index(mix.pick(*cell)))
                                     for cell in row), picks, repr(mix))

    def test_a_mix_keeps_its_weights(self):
        total = sum(weight for _, weight in ROAD_MIX)
        for clump, tolerance in ((1, 0.01), (3, 0.02)):
            grid = _grid(Mix(ROAD_MIX, clump=clump), 128)
            for block, weight in ROAD_MIX:
                share = sum(1 for b in grid.values() if b == block) / len(grid)
                self.assertAlmostEqual(share, weight / total, delta=tolerance,
                                       msg=f"{block} at clump {clump}")

    def test_a_pick_is_the_weighted_rule_over_its_roll(self):
        """style._weighted is the arithmetic a keyed pick shares: a 32-bit
        roll scaled to ``[0, total)`` and found among the running weights.
        Each entry's rolls are one unbroken run starting at
        ``ceil(bound * 2**32 / total)`` of the bound before it, so over all
        2**32 rolls each takes its weight's share to within one roll; and
        Mix.pick is that rule over the roll keyed on its cell, or on its
        cell's patch."""
        from heaton_voxel.craft.noise import cell_hash, patch
        from heaton_voxel.craft.style import _JITTER, _weighted

        for weights in ((1,), (3, 2), (8, 5, 1), (1000, 1), (2, 3, 1, 4, 5)):
            bounds, total = [], 0
            for weight in weights:
                total += weight
                bounds.append(total)
            starts = [-(-bound * 2 ** 32 // total) for bound in [0] + bounds]
            for i, weight in enumerate(weights):
                with self.subTest(weights=weights, entry=i):
                    first, after = starts[i], starts[i + 1]
                    self.assertEqual(_weighted(bounds, total, first), i)
                    self.assertEqual(_weighted(bounds, total, after - 1), i)
                    if i:
                        self.assertEqual(_weighted(bounds, total, first - 1),
                                         i - 1)
                    self.assertLessEqual(
                        abs((after - first) - weight * 2 ** 32 / total), 1)
            self.assertEqual(starts[-1], 2 ** 32)

        cells = [(x, y, z) for x in range(-9, 10) for y in (-64, 63)
                 for z in range(-9, 10)]
        for mix in (Mix(ROAD_MIX), Mix(PLAZA_MIX, seed=-3, clump=3)):
            blocks = [block for block, _ in mix.entries]
            for cell in cells:
                if mix.clump == 1:
                    roll = cell_hash(*cell, mix._key)
                else:
                    roll = cell_hash(*patch(*cell, mix.clump,
                                            mix._key ^ _JITTER), mix._key)
                self.assertEqual(
                    mix.pick(*cell),
                    blocks[_weighted(mix._bounds, mix._total, roll)], cell)

    def test_clumping_makes_patches(self):
        n = 128
        total = sum(weight for _, weight in ROAD_MIX)
        independent = sum((weight / total) ** 2 for _, weight in ROAD_MIX)
        static = _grid(Mix(ROAD_MIX), n)
        self.assertAlmostEqual(_same_neighbors(static, n), independent,
                               delta=0.02, msg="a die per cell is static")
        clumped = _grid(Mix(ROAD_MIX, clump=3), n)
        self.assertGreaterEqual(_same_neighbors(clumped, n), 0.6)
        lone = sum(all(clumped[(x + dx, z + dz)] != clumped[(x, z)]
                       for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)))
                   for z in range(1, n - 1) for x in range(1, n - 1))
        self.assertLess(lone / (n - 2) ** 2, 0.02, "lone cells at clump 3")

    def test_two_mixes_with_one_seed_are_two_fields(self):
        """The entries' CRC is in the key: two different mixes on the
        default seed pick independently, while one mix spelled two ways is
        one field."""
        cells = [(x, 64, z) for x in range(64) for z in range(64)]

        def index(mix):
            blocks = [block for block, _ in mix.entries]
            return [blocks.index(mix.pick(*cell)) for cell in cells]

        floor = index(Mix([(STONE, 1), (ANDESITE, 1)]))
        wall = index(Mix([("minecraft:oak_planks", 1),
                          ("minecraft:spruce_planks", 1)]))
        reseeded = index(Mix([(STONE, 1), (ANDESITE, 1)], seed=1))
        for other, what in ((wall, "another mix"), (reseeded, "another seed")):
            agree = sum(a == b for a, b in zip(floor, other)) / len(cells)
            self.assertTrue(0.45 <= agree <= 0.55, f"{what}: {agree}")
        respelled = index(Mix([(BlockState(STONE), 1), (f"{ANDESITE}[]", 1)]))
        self.assertSequencesEqual(respelled, floor, "one mix, spelled two ways")

    def test_a_mix_refuses_what_is_not_a_mix(self):
        for entries in ([], (), None, "minecraft:stone", ROAD_MIX[0]):
            with self.assertRaises((NBTError, TypeError), msg=entries):
                Mix(entries)
        with self.assertRaises(NBTError):
            Mix([])
        for weight in (0, -1, True, 1.5, "2"):
            with self.assertRaises(NBTError, msg=weight):
                Mix([(STONE, weight)])
        with self.assertRaises(TypeError) as caught:
            Mix([(Material("minecraft:bricks"), 1)])
        self.assertIn("form('block')", str(caught.exception))
        for entry in ((STONE,), (STONE, 1, 2), 7, Mix([(STONE, 1)])):
            with self.assertRaises(TypeError, msg=entry):
                Mix([entry])
        with self.assertRaises(ValueError):
            Mix([("minecraft:stone[facing", 1)])
        for clump in (0, 1025, True, 2.5):
            with self.assertRaises(NBTError, msg=clump):
                Mix([(STONE, 1)], clump=clump)
        for seed in (1.5, "7", None):
            with self.assertRaises(TypeError, msg=seed):
                Mix([(STONE, 1)], seed=seed)
        mix = Mix([(STONE, 1)])
        for cell in ((0.5, 64, 0), (0, 64.0, 0), (0, 64, None)):
            with self.assertRaises(TypeError, msg=cell):
                mix.pick(*cell)
        # Whether the game has a block is the writer's to ask, under its
        # own allow=: a Mix of a modded block is a Mix.
        self.assertEqual(Mix([("create:marble", 1)]).pick(0, 0, 0),
                         "create:marble")

    def test_a_mix_is_a_value(self):
        mix = Mix(ROAD_MIX, seed=3, clump=2)
        self.assertEqual(mix.entries, tuple(ROAD_MIX))
        self.assertEqual((mix.seed, mix.clump), (3, 2))
        self.assertEqual(mix, Mix(tuple(ROAD_MIX), seed=3, clump=2))
        self.assertEqual(hash(mix), hash(Mix(tuple(ROAD_MIX), seed=3, clump=2)))
        self.assertNotEqual(mix, Mix(ROAD_MIX, seed=3))
        self.assertNotEqual(mix, ROAD_MIX)
        self.assertEqual(repr(Mix([(STONE, 2)])), "Mix([('minecraft:stone', 2)])")
        self.assertIn("clump=2", repr(mix))
        with self.assertRaises(AttributeError):
            mix.seed = 4
        state = BlockState.parse("minecraft:oak_log[axis=x]")
        self.assertIs(Mix([(state, 1)]).pick(5, 5, 5), state,
                      "a pick is the block as given")

    def test_equal_mixes_are_the_ones_that_pick_alike(self):
        """Equality follows the field a Mix picks, and the hash follows
        equality: one mix spelled two ways is one Mix in a set."""
        cells = [(x, 64, z) for x in range(-8, 8) for z in range(-8, 8)]
        stairs = "minecraft:oak_stairs"

        def picked(mix):
            # A pick is the block as given, so compare it as the state it is.
            return [str(as_state(mix.pick(*cell))) for cell in cells]
        for a, b, what in (
                (Mix([(STONE, 1)]), Mix([(BlockState(STONE), 1)]),
                 "an id and its BlockState"),
                (Mix([(f"{stairs}[facing=east,half=top]", 1), (STONE, 2)]),
                 Mix([(f"{stairs}[half=top,facing=east]", 1), (STONE, 2)]),
                 "properties in another order"),
                (Mix([(STONE, 1), (ANDESITE, 1)], seed=1),
                 Mix([(STONE, 1), (ANDESITE, 1)], seed=2 ** 32 + 1),
                 "seeds equal in the 32 bits keyed on"),
                (Mix(ROAD_MIX, seed=-1, clump=2),
                 Mix(tuple(ROAD_MIX), seed=2 ** 32 - 1, clump=2),
                 "a negative seed and its 32 bits")):
            self.assertSequencesEqual(picked(b), picked(a),
                                      f"{what}: they pick alike")
            self.assertEqual(a, b, what)
            self.assertEqual(hash(a), hash(b), what)
            self.assertEqual(len({a, b}), 1, what)
        for other, what in ((Mix([(STONE, 1), (ANDESITE, 1)], seed=2), "seed"),
                            (Mix([(ANDESITE, 1), (STONE, 1)], seed=1), "order"),
                            (Mix([(STONE, 1), (ANDESITE, 2)], seed=1), "weight"),
                            (Mix([(STONE, 1), (ANDESITE, 1)], seed=1, clump=2),
                             "clump")):
            self.assertNotEqual(Mix([(STONE, 1), (ANDESITE, 1)], seed=1),
                                other, what)


class MaterialTests(unittest.TestCase):

    def test_a_material_is_named_by_any_of_its_blocks_or_its_stem(self):
        families = {
            "minecraft:bricks": ("minecraft:bricks", "minecraft:brick_stairs",
                                 "minecraft:brick_slab", "minecraft:brick_wall",
                                 "minecraft:brick"),
            "minecraft:quartz_block": ("minecraft:quartz_block",
                                       "minecraft:quartz_stairs",
                                       "minecraft:quartz_slab",
                                       "minecraft:quartz"),
            "minecraft:stone_bricks": ("minecraft:stone_brick_stairs",
                                       "minecraft:stone_brick_wall",
                                       "minecraft:stone_brick"),
            "minecraft:oak_planks": ("minecraft:oak_planks", "minecraft:oak",
                                     "minecraft:oak_stairs"),
            "minecraft:glass": ("minecraft:glass", "minecraft:glass_pane"),
        }
        for base, names in families.items():
            for name in names:
                self.assertEqual(Material(name).base, base, name)
        self.assertEqual(Material("minecraft:stone_brick_stairs").forms, {
            "block": "minecraft:stone_bricks",
            "stairs": "minecraft:stone_brick_stairs",
            "slab": "minecraft:stone_brick_slab",
            "wall": "minecraft:stone_brick_wall",
        })
        self.assertEqual(Material("minecraft:quartz").form("stairs"),
                         "minecraft:quartz_stairs")
        self.assertEqual(Material("minecraft:glass").form("pane"),
                         "minecraft:glass_pane")
        self.assertEqual(Material("minecraft:red_stained_glass_pane").forms, {
            "block": "minecraft:red_stained_glass",
            "pane": "minecraft:red_stained_glass_pane",
        })
        # A block that is no form of anything is a family of itself.
        self.assertEqual(Material("minecraft:calcite").forms,
                         {"block": "minecraft:calcite"})
        self.assertEqual(Material(BlockState("minecraft:bricks")).base,
                         "minecraft:bricks")

    def test_the_cut_from_overrides_hold(self):
        bamboo = Material("minecraft:bamboo_stairs")
        self.assertEqual(bamboo.base, "minecraft:bamboo_planks")
        self.assertEqual(Material("minecraft:bamboo_slab"), bamboo)
        self.assertEqual(bamboo.form("slab"), "minecraft:bamboo_slab")
        self.assertEqual(Material("minecraft:petrified_oak_slab").base,
                         "minecraft:oak_planks")
        self.assertEqual(Material("minecraft:oak_planks").form("slab"),
                         "minecraft:oak_slab",
                         "the oak slab, never the petrified one")
        self.assertEqual(Material("minecraft:bamboo").base, "minecraft:bamboo",
                         "the plant is a block of its own")

    def test_a_missing_form_is_refused_by_name(self):
        with self.assertRaises(IllegalBlock) as caught:
            Material("minecraft:smooth_quartz").form("wall")
        message = str(caught.exception)
        self.assertIn("minecraft:smooth_quartz has no wall", message)
        self.assertIn("minecraft:smooth_quartz_slab", message)
        self.assertIn("minecraft:smooth_quartz_stairs", message)
        with self.assertRaises(IllegalBlock) as caught:
            Material("minecraft:smooth_sandstone").form("wall")
        self.assertEqual(caught.exception.suggestions[0],
                         "minecraft:sandstone_wall")
        with self.assertRaises(IllegalBlock) as caught:
            Material("minecraft:bamboo").form("stairs")
        self.assertIn("minecraft:bamboo_stairs is minecraft:bamboo_planks's",
                      str(caught.exception))
        self.assertEqual(caught.exception.suggestions[0],
                         "minecraft:bamboo_stairs")
        with self.assertRaises(IllegalBlock) as caught:
            Material("minecraft:calcite").form("slab")
        self.assertIn("minecraft:calcite has no slab in Minecraft 26.3: it "
                      "comes in no other form", str(caught.exception))
        # A fence is a family of one, and not a full block: the refusal
        # must not call it one.
        with self.assertRaises(IllegalBlock) as caught:
            Material("minecraft:oak_fence").form("stairs")
        self.assertIn("it comes in no other form", str(caught.exception))
        self.assertNotIn("full block", str(caught.exception))
        self.assertFalse(Material("minecraft:stone").has("pane"))
        self.assertTrue(Material("minecraft:stone").has("slab"))
        for form in ("stair", "fence", "door", "", None):
            with self.assertRaises(NBTError, msg=form):
                Material("minecraft:stone").form(form)
            with self.assertRaises(NBTError, msg=form):
                Material("minecraft:stone").has(form)

    def test_a_base_is_always_a_familys_block(self):
        """Whatever names it -- any block the game has, or a stem -- a
        Material's base is a block that is its own ``"block"`` form, never
        a stair or a slab. A name the stem rule leads to a form is a
        misspelling of that form's id (``oak_stair``), refused with it."""
        for name in sorted(BLOCK_IDS):
            base = Material(name).base
            self.assertIn(base, BLOCK_IDS, name)
            self.assertEqual(form_of(base), (base, "block"), name)
        stems = sorted({name[:-len(suffix)] for name in BLOCK_IDS
                        for suffix in ("s", "_block", "_planks")
                        if name.endswith(suffix)} - set(BLOCK_IDS))
        refused = []
        for stem in stems:
            try:
                material = Material(stem)
            except IllegalBlock:
                refused.append(stem)
                continue
            self.assertEqual(form_of(material.base), (material.base, "block"),
                             stem)
            self.assertEqual(material.form("block"), material.base, stem)
        # Refused: a retired name (grass, the stem of grass_block) as any
        # write of it would be, and the stair ids a letter short.
        self.assertIn("minecraft:grass", refused)
        self.assertTrue(any(stem.endswith("_stair") for stem in refused))
        self.assertEqual([stem for stem in refused if stem not in RENAMED
                          and not stem.endswith("_stair")], [])
        for name, base in (("minecraft:oak_stair", "minecraft:oak_planks"),
                           ("minecraft:stone_brick_stair",
                            "minecraft:stone_bricks")):
            with self.assertRaises(IllegalBlock, msg=name) as caught:
                Material(name)
            self.assertEqual(caught.exception.suggestions, (f"{name}s", base))
            self.assertIn(f"it leads to {name}s", str(caught.exception))
        with self.assertRaises(IllegalBlock):
            Palette(roof="minecraft:oak_stair")
        with self.assertRaises(IllegalBlock):
            Palette(floor=Material("minecraft:stone_brick_stair"))

    def test_what_names_no_family_is_refused(self):
        with self.assertRaises(NBTError) as caught:
            Material("minecraft:oak_stairs[facing=east]")
        self.assertNotIsInstance(caught.exception, IllegalBlock)
        with self.assertRaises(IllegalBlock) as caught:
            Material("minecraft:chain")
        self.assertEqual(caught.exception.suggestions, ("minecraft:iron_chain",))
        for name in ("create:marble", "minecraft:brik", "bricks"):
            with self.assertRaises(IllegalBlock, msg=name):
                Material(name)
        for name in (None, 3, [STONE], Mix([(STONE, 1)])):
            with self.assertRaises(TypeError, msg=name):
                Material(name)

    def test_a_material_is_a_value(self):
        self.assertEqual(Material("minecraft:brick_wall"),
                         Material("minecraft:bricks"))
        self.assertEqual(len({Material("minecraft:brick_wall"),
                              Material("minecraft:brick")}), 1)
        self.assertNotEqual(Material("minecraft:bricks"), "minecraft:bricks")
        self.assertEqual(repr(Material("minecraft:brick")),
                         "Material('minecraft:bricks')")
        with self.assertRaises(AttributeError):
            Material("minecraft:bricks").base = STONE


#: Each role, the writer and argument whose default it is.
WRITERS = {
    "pad": (SiteMixin.level_pad, "surface"),
    "retaining": (SiteMixin.level_pad, "wall"),
    "trim": (LightingMixin.pave, "trim"),
    "light": (LightingMixin.pave, "light"),
    "plinth": (ShellsMixin.frame_walls, "plinth"),
    "frame": (ShellsMixin.frame_walls, "log"),
    "wall": (ShellsMixin.frame_walls, "infill"),
    "floor": (ShellsMixin.floor, "block"),
    "gable": (ShellsMixin.gable_roof, "plank"),
}

#: The writers' literal defaults, as they stood when the roles were named.
LITERALS = {
    "pad": "minecraft:grass_block[snowy=false]",
    "retaining": "minecraft:stone_bricks",
    "trim": None,
    "light": "minecraft:glowstone",
    "plinth": "minecraft:cobblestone",
    "frame": "minecraft:stripped_spruce_log[axis=y]",
    "wall": "minecraft:white_terracotta",
    "floor": "minecraft:spruce_planks",
    "gable": "minecraft:dark_oak_planks",
    "glass": "minecraft:glass_pane",
}


def _default(method, argument):
    return inspect.signature(method).parameters[argument].default


class PaletteTests(unittest.TestCase):

    def test_the_roles_are_the_legacy_writers_materials(self):
        self.assertEqual(list(ROLES), [
            "pad", "retaining", "road", "paving", "trim", "light", "plinth",
            "frame", "wall", "floor", "roof", "gable", "glass"])
        palette = Palette()
        for role, literal in LITERALS.items():
            self.assertEqual(palette[role], literal, role)
            self.assertEqual(ROLES[role].default, literal, role)
        # Held to the writers themselves wherever their default is still a
        # literal rather than the palette's.
        for role, (method, argument) in WRITERS.items():
            default = _default(method, argument)
            if default is not FROM_PALETTE:
                self.assertEqual(palette[role], default,
                                 f"{role}: {method.__name__}({argument}=)")
        light_cells = _default(LightingMixin.light_cells, "light")
        if light_cells is not FROM_PALETTE:
            self.assertEqual(palette["light"], light_cells)
        roof = palette["roof"]
        self.assertEqual(roof, Material("minecraft:dark_oak_planks"))
        for form, argument in (("stairs", "stair"), ("slab", "slab")):
            self.assertEqual(roof.form(form), {"stairs": "minecraft:dark_oak_stairs",
                                               "slab": "minecraft:dark_oak_slab"}[form])
            default = _default(ShellsMixin.gable_roof, argument)
            if default is not FROM_PALETTE:
                self.assertEqual(roof.form(form), default, argument)
        self.assertEqual(palette["pad"], site.GRASS)
        self.assertEqual(Material(palette["glass"]).form("pane"),
                         palette["glass"], "the glass role is a real pane")
        for role, mix in (("road", ROAD_MIX), ("paving", PLAZA_MIX)):
            self.assertIs(palette[role], mix, role)
        for method, argument in ((SiteMixin.road, "mix"),
                                 (LightingMixin.pave, "mix")):
            self.assertIn(_default(method, argument), (None, FROM_PALETTE))

    def test_the_default_palette_is_todays_defaults(self):
        self.assertEqual(DEFAULT_PALETTE, Palette())
        self.assertIs(DEFAULT_PALETTE["road"], ROAD_MIX)
        self.assertIs(DEFAULT_PALETTE["paving"], PLAZA_MIX)
        self.assertEqual(repr(DEFAULT_PALETTE), "Palette()")
        self.assertEqual(DEFAULT_PALETTE.roles, tuple(ROLES))

    def test_a_palette_refuses_an_unknown_role(self):
        with self.assertRaises(TypeError) as caught:
            Palette(rooof="minecraft:granite")
        message = str(caught.exception)
        self.assertIn("'rooof'", message)
        self.assertIn("did you mean 'roof'", message)
        for role in ROLES:
            self.assertIn(role, message)
        with self.assertRaises(TypeError):
            Palette(walls="minecraft:bricks", plaza=PLAZA_MIX)
        with self.assertRaises(TypeError):
            DEFAULT_PALETTE.replace(post="minecraft:cobblestone_wall")
        with self.assertRaises(KeyError) as caught:
            DEFAULT_PALETTE["rooof"]
        self.assertIn("roof", str(caught.exception))

    def test_a_palette_refuses_a_value_its_role_cannot_take(self):
        with self.assertRaises(IllegalBlock) as caught:
            Palette(roof="minecraft:calcite")
        self.assertIn("the roof role", str(caught.exception))
        self.assertEqual(caught.exception.faults, [
            "minecraft:calcite has no stairs in Minecraft 26.3",
            "minecraft:calcite has no slab in Minecraft 26.3"])
        with self.assertRaises(IllegalBlock):
            Palette(roof="minecraft:glass")
        with self.assertRaises(IllegalBlock):
            Palette(glass=Material("minecraft:stone"))
        refusals = {
            TypeError: [
                dict(light=Mix([("minecraft:glowstone", 1)])),
                dict(light=None),
                dict(pad=None),
                dict(roof=None),
                dict(road=None),
                dict(pad=[(STONE, 1)]),
                dict(roof=Mix([(STONE, 1)])),
                dict(glass=Mix([("minecraft:glass_pane", 1)])),
                dict(floor=3),
                dict(roof=["minecraft:bricks"]),
            ],
            NBTError: [
                dict(road=[]),
                dict(paving=[(STONE, 0)]),
                dict(road=[(STONE, 1.5)]),
                dict(roof="minecraft:bricks[snowy=true]"),
            ],
            ValueError: [dict(floor="minecraft:stone[facing")],
        }
        for error, cases in refusals.items():
            for given in cases:
                with self.assertRaises(error, msg=given):
                    Palette(**given)

    def test_a_palette_holds_what_it_is_given(self):
        lane = Mix(LANE_MIX, clump=2)
        brick = Material("minecraft:brick")
        palette = Palette(road=lane, paving="minecraft:gravel", trim=lane,
                          roof=brick, gable=Material("minecraft:granite"),
                          glass=Material("minecraft:red_stained_glass"),
                          wall=BlockState("minecraft:bricks"))
        self.assertIs(palette["road"], lane)
        self.assertEqual(palette["paving"], "minecraft:gravel")
        self.assertIs(palette["roof"], brick)
        self.assertEqual(Palette(roof="minecraft:brick_stairs")["roof"], brick,
                         "a family role's id is made a Material")
        self.assertEqual(Palette(glass="minecraft:glass")["glass"],
                         "minecraft:glass", "plain glass in a window is a block")
        self.assertIsNone(Palette(trim=None)["trim"])
        changed = palette.replace(trim=None)
        self.assertIsNone(changed["trim"])
        self.assertIs(palette["trim"], lane, "replace leaves the original")
        self.assertEqual(changed["roof"], brick)
        self.assertNotEqual(changed, palette)
        self.assertEqual(Palette(road=list(ROAD_MIX)), Palette(),
                         "palettes are equal when every role is")
        with self.assertRaises(TypeError):
            hash(palette)
        self.assertEqual(repr(Palette(roof="minecraft:granite")),
                         "Palette(roof=Material('minecraft:granite'))")

    def test_from_palette_is_one_sentinel(self):
        self.assertEqual(repr(FROM_PALETTE), "FROM_PALETTE")
        self.assertIs(copy.copy(FROM_PALETTE), FROM_PALETTE)
        self.assertIs(copy.deepcopy(FROM_PALETTE), FROM_PALETTE)
        self.assertIs(pickle.loads(pickle.dumps(FROM_PALETTE)), FROM_PALETTE)
        self.assertIs(type(FROM_PALETTE)(), FROM_PALETTE)


class MovedMixesTests(unittest.TestCase):

    def test_the_mixes_are_one_object_wherever_they_are_imported(self):
        for name, homes in (("ROAD_MIX", (site, build, craft)),
                            ("LANE_MIX", (site, build, craft)),
                            ("PLAZA_MIX", (lighting, build, craft))):
            mix = getattr(style, name)
            for home in homes:
                self.assertIs(getattr(home, name), mix,
                              f"{home.__name__}.{name}")
        self.assertIs(craft.Mix, Mix)
        self.assertIs(craft.Material, Material)
        self.assertIs(craft.Palette, Palette)


class OneStateTests(unittest.TestCase):
    """A Mix or a Material where one exact state is wanted is a
    ``TypeError`` that says what it is, not an unpacking error deep in a
    parser."""

    def test_a_mix_or_a_material_is_not_one_state(self):
        mix = Mix([(STONE, 1), (ANDESITE, 1)])
        bricks = Material("minecraft:bricks")
        for read in (as_state, _state, check_state, light_emission,
                     lambda block: read_overlay({(0, 64, 0): block})):
            with self.assertRaises(TypeError) as caught:
                read(mix)
            self.assertIn("pick", str(caught.exception))
            with self.assertRaises(TypeError) as caught:
                read(bricks)
            self.assertIn(".form(", str(caught.exception))
            self.assertIn("stairs", str(caught.exception))
        self.assertEqual(as_state(("minecraft:oak_log", {"axis": "x"})),
                         BlockState("minecraft:oak_log", {"axis": "x"}))

    def test_placing_a_mix_writes_nothing(self):
        directory = tempfile.mkdtemp(prefix="style-tests-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        make_save(directory, [(0, 0)])
        save = MinecraftSave.load(directory)
        builder = Builder(save)
        before = save.get_block(3, 64, 3)
        for block in (Mix([(STONE, 1)]), Material("minecraft:bricks")):
            with self.assertRaises(TypeError):
                builder.place(3, 64, 3, block)
        self.assertEqual(save.get_block(3, 64, 3), before)
        self.assertEqual(builder.journal.total, 0)


class MixWriteBase(SequenceAssertions):
    """Builds on copies of one stone world, so two sessions start from the
    same bytes and their saves can be compared byte for byte."""

    CHUNKS = [(x, z) for x in range(-1, 3) for z in range(-1, 3)]

    def world(self):
        """A fresh copy of the stone world -- stone to y=63, air above --
        and its save, loaded."""
        if not hasattr(self, "_origin"):
            scratch = tempfile.mkdtemp(prefix="style-tests-")
            self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
            self._scratch = scratch
            self._origin = os.path.join(scratch, "origin")
            make_save(self._origin, self.CHUNKS)
            save = MinecraftSave.load(self._origin)
            save.fill((-16, 56, -16), (47, 63, 47), STONE)
            save.rebuild_heightmaps((-16, 0, -16), (47, 0, 47))
            save.save()
            self._copies = 0
        self._copies += 1
        directory = os.path.join(self._scratch, f"copy-{self._copies}")
        shutil.copytree(self._origin, directory)
        return directory, MinecraftSave.load(directory)

    @staticmethod
    def regions(directory) -> dict[str, bytes]:
        """Every region file of the overworld, by name, as saved."""
        region = os.path.join(directory, "dimensions/minecraft/overworld/region")
        found = {}
        for name in sorted(os.listdir(region)):
            with open(os.path.join(region, name), "rb") as handle:
                found[name] = handle.read()
        return found

    def assertSameBytes(self, first, second, msg=""):
        mine, theirs = self.regions(first), self.regions(second)
        self.assertEqual(sorted(mine), sorted(theirs), msg)
        for name in mine:
            self.assertTrue(mine[name] == theirs[name],
                            f"{msg}: {name} differs" if msg else
                            f"{name} differs")

    @staticmethod
    def box(corner1, corner2):
        """The cells of a box in y, z, x order."""
        (x0, y0, z0), (x1, y1, z1) = corner1, corner2
        return [(x, y, z) for y in range(min(y0, y1), max(y0, y1) + 1)
                for z in range(min(z0, z1), max(z0, z1) + 1)
                for x in range(min(x0, x1), max(x0, x1) + 1)]


class MixWriteTests(MixWriteBase):
    """A Mix in a Builder's write path is a field over the world: each cell
    it is written in gets its pick there, whatever the session drew or
    wrote before, so the same Mix over the same cells is the same blocks
    -- and, written in the same order, the same bytes -- in any session."""

    MIX = Mix([(STONE, 3), (ANDESITE, 2), ("minecraft:cobblestone", 1)],
              seed=2)

    def picked(self, mix, cell):
        return BlockState.parse(mix.pick(*cell))

    def test_a_mix_does_not_move_when_an_earlier_call_changes(self):
        """The rng drift a list mix suffers: one more cell paved early on
        re-rolls every surface drawn after it. Two sessions differ only in
        the size of an earlier list-mix floor; a fill and a road with a Mix
        lay the same blocks in both, each cell its pick, drawing nothing,
        while the same road with the ROAD_MIX list does not."""
        mix = Mix(ROAD_MIX, seed=2)
        box = ((20, 64, 2), (27, 66, 9))
        laid = []
        for size in (5, 6):
            with self.subTest(size=size):
                _, save = self.world()
                b = Builder(save, seed=7)
                b.pave(0, 0, size, size, 63, light=None)
                drawn = b.rng.getstate()
                b.fill(*box, mix)
                before = set(b._ground)
                b.road([(2, 30), (40, 40)], mix=mix)
                surface = {column: y for column, y in b._ground.items()
                           if column not in before}
                self.assertEqual(b.rng.getstate(), drawn,
                                 "a Mix draws nothing from the rng")
                b.road([(2, 20), (40, 24)])
                for cell in self.box(*box):
                    self.assertEqual(save.get_block(*cell),
                                     self.picked(mix, cell), cell)
                for (x, z), y in surface.items():
                    self.assertEqual(save.get_block(x, y, z),
                                     self.picked(mix, (x, y, z)), (x, y, z))
                laid.append((
                    [save.get_block(*cell) for cell in self.box(*box)],
                    [save.get_block(x, y, z) for (x, z), y in sorted(surface.items())],
                    [save.get_block(*cell)
                     for cell in self.box((2, 60, 18), (40, 66, 26))]))
        (fill5, road5, listed5), (fill6, road6, listed6) = laid
        self.assertSequencesEqual(fill5, fill6, "the fill")
        self.assertSequencesEqual(road5, road6, "the Mix road")
        self.assertNotEqual(listed5, listed6,
                            "the list-mix road is re-rolled by the bigger "
                            "floor before it: why a Mix exists")

    def test_one_mix_over_one_box_writes_the_same_bytes_in_any_session(self):
        """Two Builders, seeded apart and one of them having drawn from its
        rng first, give a fill, a set, a pad, a floor and a road the same
        Mixes, and save the same bytes. And a Mix fill is the sweep in
        y, z, x order that a hand-written loop of set_block calls is, byte
        for byte -- the order a section holds its cells, and so the order
        each block enters its palette -- journaled cell by cell."""
        mix = Mix([(STONE, 3), (ANDESITE, 2), ("minecraft:cobblestone", 1)],
                  clump=2)
        ring = Mix([("minecraft:stone_bricks", 2),
                    (BlockState("minecraft:mossy_stone_bricks"), 1)], seed=5)

        def build(seed, warm):
            directory, save = self.world()
            b = Builder(save, seed=seed)
            for _ in range(warm):
                b.rng.random()
            b.fill((-5, 64, -5), (20, 67, 9), mix)
            b.set(30, 64, 30, mix)
            b.level_pad(24, 12, 31, 20, 66, surface=mix, wall=ring)
            b.pave(0, 20, 9, 29, 64, mix=mix, trim=ring, light=None)
            b.road([(-10, 40), (40, 36)], mix=ring)
            save.save()
            return directory

        self.assertSameBytes(build(0, 0), build(99, 17),
                             "one Mix, two sessions")

        corner1, corner2 = (-3, 64, -2), (18, 66, 4)       # across chunks
        cells = self.box(corner1, corner2)
        swept, save = self.world()
        b = Builder(save, seed=3)
        drawn = b.rng.getstate()
        # The order itself, cell by cell: the bytes below show only where
        # an order happens to put a new block into a section's palette
        # first, which a few-entry Mix can leave the same for another one.
        order = []
        write = Builder._write_set

        def recorded(builder, x, y, z, block):
            order.append((x, y, z))
            return write(builder, x, y, z, block)

        with mock.patch.object(Builder, "_write_set", recorded):
            b.fill(corner2, corner1, mix)       # corners in either order
        self.assertSequencesEqual(order, cells, "the sweep's order")
        self.assertEqual(b.rng.getstate(), drawn)
        self.assertEqual(b.blocks_set, len(cells), "one write per cell")
        self.assertSequencesEqual(sorted(b.journal.written()), sorted(cells))
        save.save()
        by_hand, other = self.world()
        for cell in cells:
            other.set_block(*cell, mix.pick(*cell))
        other.save()
        self.assertSameBytes(swept, by_hand, "the sweep against set_block "
                             "in y, z, x order")

    def test_each_writer_lays_each_cells_pick(self):
        """set, fill, a pad's surface and its retaining ring, a road's
        surface, and a floor's surface and trim: every cell is the Mix's
        pick there. A lit floor plans its lamps over the floor it lays, so
        its lamps are lamps and every cell of it holds its level; the rng
        is not touched."""
        directory, save = self.world()
        b = Builder(save, seed=11)
        drawn = b.rng.getstate()
        mix, trim = self.MIX, Mix([("minecraft:polished_andesite", 1),
                                   ("minecraft:smooth_stone", 1)])
        b.set(40, 64, 40, mix)
        self.assertEqual(save.get_block(40, 64, 40),
                         self.picked(mix, (40, 64, 40)))
        b._commit([("set", (41, 64, 40), mix),
                   ("fill", (42, 64, 40), (44, 65, 41), mix)], what="plan")
        for cell in [(41, 64, 40)] + self.box((42, 64, 40), (44, 65, 41)):
            self.assertEqual(save.get_block(*cell), self.picked(mix, cell))

        b.level_pad(0, 0, 8, 8, 66, surface=mix, wall=trim, clearance=2)
        for z in range(9):
            for x in range(9):
                self.assertEqual(save.get_block(x, 66, z),
                                 self.picked(mix, (x, 66, z)))
                edge = x in (0, 8) or z in (0, 8)
                for y in (64, 65):
                    self.assertEqual(save.get_block(x, y, z),
                                     self.picked(trim, (x, y, z)) if edge
                                     else BlockState("minecraft:dirt"))

        before = set(b._ground)
        b.road([(12, 2), (30, 14)], width=2, mix=mix)
        surface = {c: y for c, y in b._ground.items() if c not in before}
        self.assertTrue(surface)
        for (x, z), y in surface.items():
            self.assertEqual(save.get_block(x, y, z),
                             self.picked(mix, (x, y, z)))

        result = b.pave(0, 14, 11, 25, 63, mix=mix, trim=trim,
                        light_level=MELT_SNOW)
        self.assertEqual(result.trim, 44)
        lamps = []
        for z in range(14, 26):
            for x in range(12):
                found = save.get_block(x, 63, z)
                if found.name == "minecraft:glowstone":
                    lamps.append((x, z))
                    continue
                edge = x in (0, 11) or z in (14, 25)
                self.assertEqual(found, self.picked(trim if edge else mix,
                                                    (x, 63, z)), (x, z))
        self.assertEqual(len(lamps), result.lights)
        self.assertTrue(lamps)
        field = simulate_block_light(save, (0, 64, 14), (11, 64, 25),
                                     durable=True)
        for z in range(14, 26):
            for x in range(12):
                self.assertGreaterEqual(field.level(x, 64, z), MELT_SNOW,
                                        (x, z))
        self.assertEqual(b.rng.getstate(), drawn)

    def test_a_writer_writing_its_block_unchanged_picks_a_mix(self):
        """The writers that write a block they are given into their own
        cells as it is -- a frame's plinth, logs and infill, a floor, a
        gable's planks, an arcade's piers, bases and caps, a tree's
        leaves -- go through the same write path, and lay each cell its
        Mix's pick there."""
        _, save = self.world()
        b = Builder(save, seed=4)

        def mix(*names, seed=0):
            return Mix([(name, 1) for name in names], seed=seed)

        plinth = mix("minecraft:cobblestone", "minecraft:mossy_cobblestone")
        log = mix("minecraft:stripped_spruce_log[axis=y]",
                  "minecraft:stripped_oak_log[axis=y]", seed=1)
        infill = mix("minecraft:white_terracotta", "minecraft:calcite",
                     "minecraft:smooth_quartz", seed=2)
        b.frame_walls(2, 2, 8, 6, 64, 4, log=log, infill=infill,
                      plinth=plinth)
        for z in range(2, 7):
            for x in range(2, 9):
                if x not in (2, 8) and z not in (2, 6):
                    continue
                corner = x in (2, 8) and z in (2, 6)
                for y in range(64, 68):
                    want = (plinth if y == 64 else log if corner
                            else infill)
                    self.assertEqual(save.get_block(x, y, z),
                                     self.picked(want, (x, y, z)),
                                     ("frame_walls", x, y, z))

        boards = mix("minecraft:spruce_planks", "minecraft:oak_planks",
                     "minecraft:birch_planks", seed=3)
        b.floor(3, 3, 7, 5, 64, block=boards)
        for cell in self.box((3, 64, 3), (7, 64, 5)):
            self.assertEqual(save.get_block(*cell),
                             self.picked(boards, cell), ("floor", cell))

        planks = mix("minecraft:dark_oak_planks", "minecraft:spruce_planks",
                     seed=4)
        b.gable_roof(2, 2, 8, 6, 68, "x", plank=planks)
        gable = []
        lo, hi, step = 1, 7, 0
        while lo < hi:
            gable += [(x, 68 + step, z) for z in range(lo + 1, hi)
                      for x in (2, 8)]
            lo, hi, step = lo + 1, hi - 1, step + 1
        self.assertTrue(gable)
        for cell in gable:
            self.assertEqual(save.get_block(*cell),
                             self.picked(planks, cell), ("gable", cell))

        pier = mix("minecraft:quartz_block", "minecraft:smooth_quartz",
                   seed=5)
        base = mix("minecraft:stone_bricks", "minecraft:cracked_stone_bricks",
                   seed=6)
        cap = mix("minecraft:chiseled_stone_bricks", "minecraft:stone_bricks",
                  seed=7)
        columns = b.arcade((14, 2), (14, 20), 64, 69, pitch=6, pier=pier,
                           cap=cap, base=base)
        self.assertEqual(len(columns), 4)
        for x, z in columns:
            for y in range(64, 70):
                want = base if y == 64 else cap if y == 69 else pier
                self.assertEqual(save.get_block(x, y, z),
                                 self.picked(want, (x, y, z)),
                                 ("arcade", x, y, z))

        leaves = mix("minecraft:oak_leaves[persistent=true]",
                     "minecraft:azalea_leaves[persistent=true]",
                     "minecraft:flowering_azalea_leaves[persistent=true]",
                     seed=8)
        b.ornamental_tree(30, 30, kind="oak", leaves=leaves)
        canopy = [cell for cell in self.box((27, 64, 27), (33, 72, 33))
                  if save.get_block(*cell).name.endswith("_leaves")]
        trunk = sum(save.get_block(30, y, 30).name == "minecraft:oak_log"
                    for y in range(64, 72))
        self.assertIn(trunk, (4, 5))
        # Every cell the canopy's rule names, since the air there holds
        # nothing for it to grow round.
        self.assertEqual(len(canopy), sum(
            1 for dy in range(trunk - 2, trunk + 2)
            for dx in range(-(2 if dy < trunk else 1),
                            (2 if dy < trunk else 1) + 1)
            for dz in range(-(2 if dy < trunk else 1),
                            (2 if dy < trunk else 1) + 1)
            if abs(dx) + abs(dz) <= (2 if dy < trunk else 1) + 1
            and (dx or dz or dy >= trunk)))
        for cell in canopy:
            self.assertEqual(save.get_block(*cell),
                             self.picked(leaves, cell), ("leaves", cell))

    def test_the_audit_reads_a_mix_as_its_picks(self):
        """The survival audit a writer runs before it writes reads each
        cell of a Mix as its pick: a layer of sand and stone over air is
        refused for exactly the cells that pick sand."""
        _, save = self.world()
        b = Builder(save)
        mix = Mix([("minecraft:sand", 1), (STONE, 1)], seed=4)
        cells = self.box((2, 70, 2), (9, 70, 9))
        sand = sorted(cell for cell in cells
                      if mix.pick(*cell) == "minecraft:sand")
        self.assertTrue(sand and len(sand) < len(cells))
        with self.assertRaises(Refused) as caught:
            b._audit_plan([("fill", (2, 70, 2), (9, 70, 9), mix)],
                          what="the layer")
        self.assertEqual([fault.cell for fault in caught.exception.faults],
                         sand)
        self.assertEqual({fault.kind for fault in caught.exception.faults},
                         {"falls"})
        self.assertEqual(b.blocks_set, 0)


class MixRefusalTests(MixWriteBase):
    """What a Mix cannot be: an entry the game lacks, a placement, one
    exact state a rule or a derived state needs. Each is refused before
    anything is written, and the rng and the save are as they were."""

    def test_a_mix_entry_the_game_lacks_refuses_the_whole_call(self):
        """An entry no cell happens to pick is refused all the same: a
        Mix's entries are checked by name, as a list's are."""
        bad = Mix([(STONE, 1000), ("minecraft:quartz_wall", 1)])
        self.assertEqual(bad.pick(3, 64, 3), STONE,
                         "the cell set below would pick stone")
        untouched, _ = self.world()
        directory, save = self.world()
        b = Builder(save, seed=3)
        drawn = b.rng.getstate()
        calls = {
            "set": lambda: b.set(3, 64, 3, bad),
            "fill": lambda: b.fill((0, 64, 0), (5, 66, 5), bad),
            "a plan": lambda: b._commit(
                [("set", (3, 64, 3), STONE),
                 ("fill", (4, 64, 4), (6, 64, 6), bad)], what="the plan"),
            "a pad's surface": lambda: b.level_pad(0, 0, 4, 4, 65,
                                                   surface=bad),
            "a pad's ring": lambda: b.level_pad(0, 0, 4, 4, 66, wall=bad),
            "a road": lambda: b.road([(0, 10), (20, 10)], mix=bad),
            "a floor": lambda: b.pave(0, 0, 4, 4, 64, mix=bad, light=None),
            "a lit floor": lambda: b.pave(0, 0, 4, 4, 64, mix=bad,
                                          light_level=MELT_SNOW),
            "a trim": lambda: b.pave(0, 0, 4, 4, 64, trim=bad, light=None),
        }
        for name, call in calls.items():
            with self.subTest(name):
                with self.assertRaises(IllegalBlock) as caught:
                    call()
                self.assertIn("minecraft:quartz_wall", str(caught.exception))
                self.assertIn("Nothing was written", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(b.rng.getstate(), drawn)
        save.save()
        self.assertSameBytes(untouched, directory)

    def test_a_placement_of_a_mix_is_refused(self):
        """Which cells a placement writes -- a door's top half, a bed's
        head -- depends on the block, so a plan placing a Mix is refused
        before its first op is written, and so is the audit of one."""
        _, save = self.world()
        b = Builder(save)
        doors = Mix([("minecraft:oak_door", 1), ("minecraft:birch_door", 1)])
        with self.assertRaises(TypeError) as caught:
            b._commit([("set", (3, 64, 3), "minecraft:oak_planks"),
                       ("place", (5, 64, 5), doors)], what="the plan")
        self.assertIn("placement", str(caught.exception))
        self.assertIn(repr(doors), str(caught.exception))
        with self.assertRaises(TypeError) as caught:
            b._audit_plan([("place", (5, 64, 5), doors)], what="the plan")
        self.assertIn("placement", str(caught.exception))
        with self.assertRaises(TypeError):
            b.place(5, 64, 5, doors)
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(save.get_block(3, 64, 3).name, "minecraft:air")

    def test_rules_and_derived_states_refuse_a_mix(self):
        """A rule class takes one exact state for each of its materials,
        and so does every argument a writer derives a state from -- a
        lamp, its chain and anchor, a door's button, a post, a seal, a
        shaft's lining, a flight's rail, a gable's stairs and ridge slab: a
        Mix is a TypeError naming it, before anything is written, and
        before anything is read but by a stair's ``read`` given one for a
        material it would otherwise read off the blocks. Before anything
        is read is held to by making any chunk read fail, so a guard that
        went missing and left the refusal to some later coercion -- or to
        none, for a flight whose well wants no rail -- is caught, as is one
        left to an unpacking error that merely mentions the Mix."""
        _, save = self.world()
        b = Builder(save)
        stairs = Mix([("minecraft:oak_stairs", 1),
                      ("minecraft:spruce_stairs", 1)])
        planks = Mix([("minecraft:oak_planks", 1),
                      ("minecraft:spruce_planks", 1)])
        slabs = Mix([("minecraft:oak_slab", 1), ("minecraft:spruce_slab", 1)])
        lamps = Mix([("minecraft:glowstone", 1),
                     ("minecraft:sea_lantern", 1)])
        line = [(0, 64, 30), (20, 64, 30)]
        cases = {
            "SpiralStair stair": lambda: SpiralStair((8, 8), stair=stairs),
            "SpiralStair tread": lambda: SpiralStair((8, 8), tread=planks),
            "SpiralStair newel": lambda: SpiralStair((8, 8), newel=planks),
            "SwitchbackStair stair": lambda: SwitchbackStair(
                (2, 10), (5, 16), stair=stairs),
            "SwitchbackStair wood": lambda: SwitchbackStair(
                (2, 10), (5, 16), wood=planks),
            "StraightStair stair": lambda: StraightStair(
                (5, 64, 10), "north", 4, stair=stairs),
            "StraightStair wood": lambda: StraightStair(
                (5, 64, 10), "north", 4, wood=planks),
            "Vault slab": lambda: Vault((8, 80, 8), half_span=4, slab=slabs),
            "Vault block": lambda: Vault((8, 80, 8), half_span=4,
                                         block=planks),
            "Vault stair": lambda: Vault((8, 80, 8), half_span=4,
                                         stair=stairs),
            "Vault springer": lambda: Vault((8, 80, 8), half_span=4,
                                            springer=planks),
            "Railway bed": lambda: Railway(line, bed=planks),
            "Railway power": lambda: Railway(line, power=planks),
            "railway seal": lambda: b.railway(Railway(line), seal=planks),
            "shaft wall": lambda: b.shaft((8, 8), 60, 70, wall=planks),
            "lit shaft wall": lambda: b.shaft(
                (8, 8), 60, 70, wall=planks, light="minecraft:glowstone",
                light_level=8),
            "shaft light": lambda: b.shaft((8, 8), 60, 70, light=lamps,
                                           light_level=8),
            "pave light": lambda: b.pave(0, 0, 4, 4, 63, light=lamps,
                                         light_level=MELT_SNOW),
            "light_cells light": lambda: b.light_cells(
                [(0, 0)], light=lamps, light_level=MELT_SNOW),
            "hang_lamps light": lambda: b.hang_lamps(
                0, 0, 4, 4, 70, 63, light=lamps, light_level=8),
            "hang_lamps chain": lambda: b.hang_lamps(
                0, 0, 4, 4, 70, 63, chain=Mix([("minecraft:iron_chain", 1)]),
                light_level=8),
            "hang_lamps anchor": lambda: b.hang_lamps(
                0, 0, 4, 4, 70, 63, anchor=planks, light_level=8),
            "doorway door": lambda: b.doorway(
                3, 64, 3, "north", door=Mix([("minecraft:oak_door", 1)])),
            "doorway button": lambda: b.doorway(
                3, 64, 3, "north", door="minecraft:iron_door",
                button=Mix([("minecraft:stone_button", 1)])),
            "line_lamps post": lambda: b.line_lamps(
                [(0, 20), (20, 20)], post=planks, lamp="minecraft:lantern",
                light_level=8),
            "line_lamps lamp": lambda: b.line_lamps(
                [(0, 20), (20, 20)], post="minecraft:oak_fence",
                lamp=Mix([("minecraft:lantern", 1)]), light_level=8),
            # Nothing stands at y=67, so no cell of the well wants a rail:
            # only a guard ahead of the reads can refuse this one.
            "flight rail": lambda: b.flight(
                StraightStair((5, 64, 10), "north", 4), floor_y=67,
                rail=planks, walk=False),
            "gable_roof stair": lambda: b.gable_roof(
                2, 2, 10, 8, 68, "x", stair=stairs),
            "gable_roof slab": lambda: b.gable_roof(
                2, 2, 10, 8, 68, "z", slab=slabs),
        }

        def read(*args, **kwargs):
            raise AssertionError("a chunk was read before the Mix was "
                                 "refused")

        for name, call in cases.items():
            with self.subTest(name):
                with mock.patch.object(Dimension, "region", read), \
                        mock.patch.object(Dimension, "chunk", read), \
                        mock.patch.object(Dimension, "blocks", read):
                    with self.assertRaises(TypeError) as caught:
                        call()
                self.assertIn("Mix", str(caught.exception))
                self.assertIn("picked by position", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        self.assertIn("wall=", str(self._raised(cases["shaft wall"])))

        # Reading a stair back with a Mix for a material it would otherwise
        # read off the blocks.
        b.spiral_stair(SpiralStair((8, 8)), 64, 72)
        b.switchback_stair(SwitchbackStair(
            (22, 10), (25, 16), axis="z", start="northeast", start_y=64,
            landing=3), 64, 77)
        for name, call in {
                "SpiralStair.read tread": lambda: SpiralStair.read(
                    save, (8, 8), tread=planks),
                "SpiralStair.read newel": lambda: SpiralStair.read(
                    save, (8, 8), newel=planks),
                "SwitchbackStair.read wood": lambda: SwitchbackStair.read(
                    save, (22, 8), (25, 18), wood=planks)}.items():
            with self.subTest(name):
                with self.assertRaises(TypeError) as caught:
                    call()
                self.assertIn("Mix", str(caught.exception))
        self.assertEqual(SpiralStair.read(save, (8, 8)).found, (64, 72))
        self.assertEqual(SwitchbackStair.read(save, (22, 8), (25, 18)).found,
                         (64, 77))

    def _raised(self, call):
        try:
            call()
        except TypeError as exc:
            return exc
        self.fail("no TypeError")


#: Every material of the eight legacy writers, at the literal each wrote
#: before it read a palette: what a call naming them all passes.
OLD_LITERALS = {
    "level_pad": dict(surface="minecraft:grass_block[snowy=false]",
                      wall="minecraft:stone_bricks"),
    "road": dict(mix=ROAD_MIX),
    "pave": dict(mix=PLAZA_MIX, trim=None, light="minecraft:glowstone"),
    "light_cells": dict(light="minecraft:glowstone"),
    "frame_walls": dict(log="minecraft:stripped_spruce_log[axis=y]",
                        infill="minecraft:white_terracotta",
                        plinth="minecraft:cobblestone"),
    "floor": dict(block="minecraft:spruce_planks"),
    "gable_roof": dict(stair="minecraft:dark_oak_stairs",
                       plank="minecraft:dark_oak_planks",
                       slab="minecraft:dark_oak_slab"),
    "window": dict(block="minecraft:glass_pane"),
}

#: A palette naming every role, each something its writer did not write by
#: default -- a Material, a Mix, one block -- so a cell shows which it read.
SWAPPED = dict(
    pad="minecraft:moss_block",
    retaining=Material("minecraft:bricks"),
    road="minecraft:gravel",
    paving=Mix([("minecraft:polished_andesite", 2),
                ("minecraft:smooth_stone", 1)], seed=3),
    trim="minecraft:chiseled_stone_bricks",
    light="minecraft:sea_lantern",
    plinth="minecraft:mossy_cobblestone",
    frame="minecraft:oak_log[axis=y]",
    wall=Material("minecraft:calcite"),
    floor=Mix([("minecraft:oak_planks", 1), ("minecraft:birch_planks", 1)],
              seed=4),
    roof="minecraft:granite",
    gable="minecraft:smooth_sandstone",
    glass=Material("minecraft:red_stained_glass"),
)


class PaletteWriteTests(MixWriteBase):
    """The eight legacy writers read every material their caller leaves out
    from the Builder's palette, and an argument given always wins: a Builder
    with no palette, one with ``Palette()``, and one under a palette of
    other materials given every old literal outright all build the same
    bytes, while the palette alone changes what is built."""

    def build(self, palette=None, *, explicit=False, seed=5):
        """One script through every legacy writer -- a pad cut and filled, a
        road and a lane drawn from lists, a lit floor, light_cells, a frame,
        a floor, a gable and a window -- on a fresh copy of the stone world,
        saved. Returns the world's directory, the Builder and its save."""
        directory, save = self.world()
        b = Builder(save, seed=seed, palette=palette)

        def old(writer, **given):
            return {**OLD_LITERALS[writer], **given} if explicit else given

        b.level_pad(0, 0, 6, 6, 66, name="pad", **old("level_pad"))
        b.road([(10, 2), (40, 8)], **old("road"))
        b.road([(10, 30), (40, 26)], width=2,
               **old("road", mix=LANE_MIX))
        b.pave(0, 10, 9, 19, 63, light_level=MELT_SNOW, **old("pave"))
        b.light_cells([(x, 22) for x in range(0, 10)],
                      light_level=MELT_SNOW, **old("light_cells"))
        b.frame_walls(12, 10, 18, 16, 64, 4, **old("frame_walls"))
        b.floor(13, 11, 17, 15, 64, **old("floor"))
        b.gable_roof(12, 10, 18, 16, 68, "x", **old("gable_roof"))
        b.gable_roof(22, 10, 27, 16, 68, "z", **old("gable_roof"))
        b.window(14, 66, 10, "x", **old("window"))
        b.window(12, 66, 13, "z", **old("window"))
        b.finish()
        save.save()
        return directory, b, save

    def test_no_palette_builds_todays_bytes(self):
        """No palette, ``Palette()`` and every old literal passed outright
        are one build: the same region bytes, the same rng draws."""
        plain, b, _ = self.build()
        for name, palette, explicit in (("Palette()", Palette(), False),
                                        ("the old literals", None, True)):
            with self.subTest(name):
                other, c, _ = self.build(palette, explicit=explicit)
                self.assertSameBytes(plain, other, name)
                self.assertEqual(c.rng.getstate(), b.rng.getstate(), name)
        self.assertIs(b.palette, DEFAULT_PALETTE)

    #: The sha256 of every chunk payload the build saves with no palette,
    #: uncompressed: what the same calls saved before the eight writers
    #: read a palette at all. A change to it is a change to the bytes every
    #: script calling these writers at their defaults has always built.
    LEGACY_PAYLOADS = (
        "d0b2ec10d2cdfaa3f03d70a129a93220700090acac921b37bb69551a6f704e38")

    @staticmethod
    def payloads(directory) -> str:
        """One sha256 over every chunk of the overworld's region files as
        the game reads it -- region, index, length and the decompressed
        NBT -- so the pin holds whatever zlib compressed it."""
        region = os.path.join(directory, "dimensions/minecraft/overworld/region")
        digest = hashlib.sha256()
        for name in sorted(os.listdir(region)):
            with open(os.path.join(region, name), "rb") as handle:
                data = handle.read()
            for index in range(1024):
                entry, = struct.unpack_from(">I", data, 4 * index)
                offset = (entry >> 8) * 4096
                if not offset:
                    continue
                length, = struct.unpack_from(">I", data, offset)
                raw = _decompress(data[offset + 4],
                                  data[offset + 5:offset + 4 + length])
                digest.update(f"{name}:{index}:{len(raw)}:".encode())
                digest.update(raw)
        return digest.hexdigest()

    def test_no_palette_builds_the_bytes_built_before_palettes(self):
        """The variants above all run today's code, so a drift they share
        passes them; this pins what that code saves to what it saved before
        the writers read a palette, so a later move of one of them -- a
        gable onto a roof rule, a window into a shell -- that changes a byte
        fails here. The window's joins are read back from the saved palette
        too: east before west, north before south, as it always spelled
        them -- the same state the other way round is other bytes."""
        directory, _, _ = self.build()
        self.assertEqual(self.payloads(directory), self.LEGACY_PAYLOADS)
        saved = MinecraftSave.load(directory)
        self.assertEqual(list(saved.get_block(14, 66, 10).properties)[:2],
                         ["east", "west"])
        self.assertEqual(list(saved.get_block(12, 66, 13).properties)[:2],
                         ["north", "south"])

    def test_an_argument_beats_the_palette(self):
        """Under a palette of other materials, a call giving each material
        builds what the same call built with no palette -- the palette's
        trim and light included, given as ``None`` and as glowstone."""
        plain, b, _ = self.build()
        swapped = Palette(**SWAPPED)
        given, c, _ = self.build(swapped, explicit=True)
        self.assertSameBytes(plain, given, "every argument given")
        self.assertEqual(c.rng.getstate(), b.rng.getstate())
        self.assertIs(c.palette, swapped)
        bare, _, _ = self.build(swapped)
        self.assertTrue(self.regions(plain) != self.regions(bare),
                        "left out, the palette's materials are written")

        # None means none, palette or no palette.
        _, save = self.world()
        d = Builder(save, palette=swapped)
        result = d.pave(30, 30, 35, 35, 63, trim=None, light=None)
        self.assertEqual(result.trim, 0)
        self.assertIsNone(result.light_level)
        self.assertEqual(d.pave(30, 40, 35, 45, 63, light=None).trim, 20,
                         "left out, the palette's trim")
        self.assertEqual(save.get_block(30, 63, 40).name,
                         "minecraft:chiseled_stone_bricks")

        # A palette is set as a Palette, or None for the defaults.
        d.palette = DEFAULT_PALETTE
        d.floor(40, 40, 41, 41, 64)
        self.assertEqual(save.get_block(40, 64, 40).name,
                         "minecraft:spruce_planks")
        d.palette = swapped
        d.palette = None
        self.assertIs(d.palette, DEFAULT_PALETTE)
        for wrong in ({"floor": STONE}, "minecraft:stone", ROLES):
            with self.subTest(wrong=wrong):
                with self.assertRaises(TypeError):
                    d.palette = wrong
                with self.assertRaises(TypeError):
                    Builder(save, palette=wrong)
        self.assertIs(d.palette, DEFAULT_PALETTE)

    def test_a_palette_swaps_the_materials_of_each_legacy_writer(self):
        """Every material each writer leaves out is the palette's role, in
        the form the writer writes: a Material as its block, its stairs and
        slab, or its pane; a Mix as each cell's pick; one block as itself."""
        _, b, save = self.build(Palette(**SWAPPED))
        get = save.get_block

        def picked(mix, cell):
            return BlockState.parse(mix.pick(*cell))

        # level_pad: the surface over the pad, the ring behind its fill.
        for z in range(7):
            for x in range(7):
                self.assertEqual(get(x, 66, z).name, "minecraft:moss_block")
                edge = x in (0, 6) or z in (0, 6)
                for y in (64, 65):
                    self.assertEqual(get(x, y, z).name, "minecraft:bricks"
                                     if edge else "minecraft:dirt", (x, y, z))
        # road: the palette's one block all along the road (the lane names
        # its own LANE_MIX).
        road = [(x, z) for z in range(0, 12) for x in range(10, 41)
                if get(x, 63, z).name == "minecraft:gravel"]
        self.assertGreater(len(road), 60)
        # pave: the trim round the edge, the paving Mix's picks inside, and
        # sea lanterns for lamps, holding the floor at its level.
        lamps = 0
        for z in range(10, 20):
            for x in range(10):
                found = get(x, 63, z)
                if found.name == "minecraft:sea_lantern":
                    lamps += 1
                elif x in (0, 9) or z in (10, 19):
                    self.assertEqual(found.name,
                                     "minecraft:chiseled_stone_bricks")
                else:
                    self.assertEqual(found, picked(SWAPPED["paving"],
                                                   (x, 63, z)), (x, z))
        self.assertGreater(lamps, 0)
        field = simulate_block_light(save, (0, 64, 10), (9, 64, 19),
                                     durable=True)
        self.assertTrue(all(field.level(x, 64, z) >= MELT_SNOW
                            for z in range(10, 20) for x in range(10)))
        # light_cells: its lamps are the palette's light.
        row = {get(x, 63, 22).name for x in range(10)}
        self.assertIn("minecraft:sea_lantern", row)
        self.assertNotIn("minecraft:glowstone", row)
        # frame_walls and floor.
        for x, z in ((12, 10), (18, 16)):
            self.assertEqual(get(x, 64, z).name, "minecraft:mossy_cobblestone")
            self.assertEqual(get(x, 65, z),
                             BlockState("minecraft:oak_log", {"axis": "y"}))
        self.assertEqual(get(15, 65, 10).name, "minecraft:calcite")
        for z in range(11, 16):
            for x in range(13, 18):
                self.assertEqual(get(x, 64, z), picked(SWAPPED["floor"],
                                                       (x, 64, z)))
        # gable_roof: the roof family's stairs and slab, the gable's block.
        names = {state.name for state in
                 save.histogram((11, 68, 9), (28, 76, 17)).counts}
        self.assertTrue({"minecraft:granite_stairs", "minecraft:granite_slab",
                         "minecraft:smooth_sandstone"} <= names, names)
        self.assertFalse({"minecraft:dark_oak_stairs", "minecraft:dark_oak_slab",
                          "minecraft:dark_oak_planks"} & names)
        # window: the glass family's pane, joined along its wall.
        for cell, sides in (((14, 66, 10), ("east", "west")),
                            ((12, 66, 13), ("north", "south"))):
            found = get(*cell)
            self.assertEqual(found.name, "minecraft:red_stained_glass_pane")
            self.assertEqual({side for side in ("north", "south", "east", "west")
                              if found.properties.get(side) == "true"},
                             set(sides), cell)

    def test_a_material_roof_is_one_familys_stairs_and_slab(self):
        """A family in the roof role is its stairs and its slab: the Roman
        town's gable wrapper, three ids by hand, is one palette. A Material
        given for ``stair=`` or ``slab=`` is that form; one lacking it is
        refused by name, every missing form at once, before anything."""
        directory, save = self.world()
        b = Builder(save, palette=Palette(roof="minecraft:granite",
                                          gable="minecraft:smooth_sandstone"))
        b.gable_roof(2, 2, 8, 7, 68, "x")
        b.gable_roof(12, 2, 17, 9, 68, "z")
        save.save()
        by_hand, other = self.world()
        c = Builder(other)
        for args in (((2, 2, 8, 7, 68, "x")), ((12, 2, 17, 9, 68, "z"))):
            c.gable_roof(*args, stair="minecraft:granite_stairs",
                         plank="minecraft:smooth_sandstone",
                         slab="minecraft:granite_slab")
        other.save()
        self.assertSameBytes(directory, by_hand, "the Roman wrapper")

        _, save = self.world()
        d = Builder(save)
        d.gable_roof(2, 2, 7, 6, 68, "x",
                     stair=Material("minecraft:brick_stairs"),
                     slab=Material("minecraft:brick"))
        names = {state.name for state in
                 save.histogram((1, 68, 1), (8, 75, 7)).counts}
        self.assertTrue({"minecraft:brick_stairs", "minecraft:brick_slab",
                         "minecraft:dark_oak_planks"} <= names, names)
        written = d.blocks_set

        def read(*args, **kwargs):
            raise AssertionError("a chunk was read before the refusal")

        with mock.patch.object(Dimension, "region", read), \
                mock.patch.object(Dimension, "chunk", read), \
                mock.patch.object(Dimension, "blocks", read):
            with self.assertRaises(IllegalBlock) as caught:
                d.gable_roof(20, 2, 25, 6, 68, "x",
                             stair=Material("minecraft:calcite"),
                             slab=Material("minecraft:calcite"))
        message = str(caught.exception)
        self.assertIn("gable_roof cannot be written: 2 forms the game does "
                      "not have (stairs of minecraft:calcite, slab of "
                      "minecraft:calcite)", message)
        self.assertIn("Nothing was written", message)
        self.assertEqual(caught.exception.faults, [
            "minecraft:calcite has no stairs in Minecraft 26.3",
            "minecraft:calcite has no slab in Minecraft 26.3"])
        self.assertEqual(d.blocks_set, written)

    def test_a_window_of_glass_that_does_not_join_is_written_as_it_is(self):
        """A connection is spelled only onto a block that takes all four, so
        plain and tinted glass go in a window under strict writes, from an
        argument or from the palette; a pane or a bar joins along its wall,
        keeping what else it was given."""
        _, save = self.world()
        b = Builder(save)
        b.window(2, 64, 2, "x", block="minecraft:tinted_glass")
        b.window(3, 64, 2, "z", block=BlockState("minecraft:glass"))
        b.window(4, 64, 2, "x", block="minecraft:iron_bars")
        b.window(5, 64, 2, "z",
                 block="minecraft:glass_pane[waterlogged=true]")
        b.palette = Palette(glass="minecraft:tinted_glass")
        b.window(6, 64, 2, "z")
        self.assertEqual(save.get_block(2, 64, 2),
                         BlockState("minecraft:tinted_glass"))
        self.assertEqual(save.get_block(3, 64, 2),
                         BlockState("minecraft:glass"))
        self.assertEqual(save.get_block(4, 64, 2), BlockState(
            "minecraft:iron_bars", {"east": "true", "west": "true"}))
        self.assertEqual(save.get_block(5, 64, 2), BlockState(
            "minecraft:glass_pane", {"waterlogged": "true", "north": "true",
                                     "south": "true"}))
        self.assertEqual(save.get_block(6, 64, 2),
                         BlockState("minecraft:tinted_glass"))
        self.assertEqual(b.blocks_set, 5)

    def test_a_window_given_a_family_is_its_pane(self):
        """A Material in a window is its pane, given or from the palette's
        glass; one with no pane is refused by name, before anything."""
        _, save = self.world()
        b = Builder(save)
        b.window(2, 64, 2, "x", block=Material("minecraft:red_stained_glass"))
        b.window(3, 64, 2, "z", block=Material("minecraft:red_stained_glass"))
        b.palette = Palette(glass=Material("minecraft:glass"))
        b.window(4, 64, 2, "x")
        self.assertEqual(save.get_block(2, 64, 2), BlockState(
            "minecraft:red_stained_glass_pane",
            {"east": "true", "west": "true"}))
        self.assertEqual(save.get_block(3, 64, 2), BlockState(
            "minecraft:red_stained_glass_pane",
            {"north": "true", "south": "true"}))
        self.assertEqual(save.get_block(4, 64, 2), BlockState(
            "minecraft:glass_pane", {"east": "true", "west": "true"}))
        with self.assertRaises(IllegalBlock) as caught:
            b.window(5, 64, 2, "x", block=Material("minecraft:stone"))
        self.assertIn("window cannot be written: minecraft:stone has no "
                      "pane", str(caught.exception))
        self.assertEqual(save.get_block(5, 64, 2).name, "minecraft:air")
        self.assertEqual(b.blocks_set, 3)

    def test_a_window_refuses_a_mix(self):
        """A window's connections are spelled onto one block: a Mix there is
        a TypeError naming it, before anything is read or written."""
        _, save = self.world()
        b = Builder(save)
        panes = Mix([("minecraft:glass_pane", 1),
                     ("minecraft:white_stained_glass_pane", 1)])

        def read(*args, **kwargs):
            raise AssertionError("a chunk was read before the Mix was refused")

        with mock.patch.object(Dimension, "region", read), \
                mock.patch.object(Dimension, "chunk", read), \
                mock.patch.object(Dimension, "blocks", read):
            with self.assertRaises(TypeError) as caught:
                b.window(2, 64, 2, "x", block=panes)
        message = str(caught.exception)
        self.assertIn("window(block=)", message)
        self.assertIn("picked by position", message)
        self.assertIn(repr(panes), message)
        self.assertEqual(b.blocks_set, 0)

    def test_a_window_checks_its_block_before_joining_it(self):
        """The pair along the wall is the window's to set, so the block is
        checked as it was given first: a value its block does not take is
        an IllegalBlock in the window's name, and a side of that pair given
        as anything but true is an NBTError -- neither quietly joined, and
        nothing read or written. The other two sides pass as given."""
        _, save = self.world()
        b = Builder(save)

        def read(*args, **kwargs):
            raise AssertionError("a chunk was read before the refusal")

        with mock.patch.object(Dimension, "region", read), \
                mock.patch.object(Dimension, "chunk", read), \
                mock.patch.object(Dimension, "blocks", read):
            for block, axis in (("minecraft:glass_pane[east=maybe]", "x"),
                                ("minecraft:iron_bars[west=1]", "x"),
                                ("minecraft:glass_pane[north=yes]", "z")):
                with self.subTest(block):
                    with self.assertRaises(IllegalBlock) as caught:
                        b.window(2, 64, 2, axis, block=block)
                    self.assertIn(f"window cannot be written: {block} is "
                                  f"not a state", str(caught.exception))
            for block, axis, said in (
                    ("minecraft:glass_pane[east=false,west=false]", "x",
                     "says east=false, west=false"),
                    ("minecraft:iron_bars[south=false]", "z",
                     "says south=false")):
                with self.subTest(block):
                    with self.assertRaises(NBTError) as caught:
                        b.window(2, 64, 2, axis, block=block)
                    self.assertNotIsInstance(caught.exception, IllegalBlock)
                    self.assertIn(said, str(caught.exception))
                    self.assertIn("Nothing was written", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        b.window(2, 64, 2, "x",
                 block="minecraft:glass_pane[east=true,north=false]")
        self.assertEqual(save.get_block(2, 64, 2), BlockState(
            "minecraft:glass_pane",
            {"east": "true", "north": "false", "west": "true"}))

    def test_a_surface_given_as_none_is_the_palettes(self):
        """``None`` in a road's or a floor's ``mix=`` has always meant the
        default surface, and under a palette it is the palette's, as
        leaving it out is."""
        _, save = self.world()
        b = Builder(save, palette=Palette(road="minecraft:gravel",
                                          paving="minecraft:smooth_stone"))
        before = set(b._ground)
        b.road([(0, 2), (30, 6)], mix=None)
        laid = {c: y for c, y in b._ground.items() if c not in before}
        self.assertTrue(laid)
        for (x, z), y in laid.items():
            self.assertEqual(save.get_block(x, y, z).name, "minecraft:gravel")
        b.pave(0, 10, 5, 15, 63, mix=None, light=None)
        self.assertEqual(save.histogram((0, 63, 10), (5, 63, 15)).counts,
                         {BlockState("minecraft:smooth_stone"): 36})

    def test_light_cells_takes_no_none_for_its_light(self):
        """``pave(light=None)`` is an unlit floor; light_cells exists to set
        lamps, so its ``light=None`` is a TypeError naming the argument,
        before anything is read, written or drawn."""
        _, save = self.world()
        b = Builder(save, seed=3)
        drawn = b.rng.getstate()

        def read(*args, **kwargs):
            raise AssertionError("a chunk was read before the refusal")

        with mock.patch.object(Dimension, "region", read), \
                mock.patch.object(Dimension, "chunk", read), \
                mock.patch.object(Dimension, "blocks", read):
            with self.assertRaises(TypeError) as caught:
                b.light_cells([(x, 22) for x in range(10)], light=None,
                              light_level=MELT_SNOW)
        self.assertIn("light_cells(light=None)", str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(b.rng.getstate(), drawn)

    def test_a_surface_is_a_list_a_mix_or_one_block(self):
        """A road's or a floor's mix= is drawn from a list, picked from a
        Mix, or one block laid all along -- a Material's block too -- and an
        empty list is refused, with nothing written and nothing drawn,
        rather than laid as the default."""
        _, save = self.world()
        b = Builder(save, seed=2)
        drawn = b.rng.getstate()
        before = set(b._ground)
        b.road([(0, 2), (30, 6)], mix="minecraft:gravel")
        laid = {c: y for c, y in b._ground.items() if c not in before}
        self.assertTrue(laid)
        for (x, z), y in laid.items():
            self.assertEqual(save.get_block(x, y, z).name, "minecraft:gravel")
        b.pave(0, 10, 5, 15, 63, mix=Material("minecraft:stone_brick_stairs"),
               light=None)
        self.assertEqual(save.histogram((0, 63, 10), (5, 63, 15)).counts,
                         {BlockState("minecraft:stone_bricks"): 36})
        self.assertEqual(b.rng.getstate(), drawn, "one block draws nothing")
        written = b.blocks_set
        for call in (lambda: b.road([(0, 20), (30, 20)], mix=[]),
                     lambda: b.pave(0, 30, 5, 35, 63, mix=(), light=None)):
            with self.assertRaises(NBTError) as caught:
                call()
            self.assertIn("no entries", str(caught.exception))
        self.assertEqual(b.blocks_set, written)
        self.assertEqual(b.rng.getstate(), drawn)

    def test_every_role_is_read_by_the_argument_it_names(self):
        """Each role's ``read_by`` names a writer's argument that defaults to
        the palette -- every one of the eight legacy writers, and nothing
        else -- so the vocabulary and the writers cannot drift apart."""
        writers = {}
        for role, spec in ROLES.items():
            for reader in spec.read_by.split(" and "):
                name, _, argument = reader.partition("(")
                argument = argument.rstrip(")").rstrip("=")
                with self.subTest(role=role, reader=reader):
                    for arg in argument.split(", "):
                        arg = arg.rstrip("=")
                        method = getattr(Builder, name)
                        self.assertIs(_default(method, arg), FROM_PALETTE)
                        writers.setdefault(name, set()).add(arg)
        self.assertEqual(sorted(writers), sorted([
            "level_pad", "road", "pave", "light_cells", "frame_walls",
            "floor", "gable_roof", "window"]))
        # And no writer reads the palette for an argument no role names.
        for name, arguments in writers.items():
            method = getattr(Builder, name)
            palette_args = {arg for arg, parameter in
                            inspect.signature(method).parameters.items()
                            if parameter.default is FROM_PALETTE}
            self.assertEqual(palette_args, arguments, name)


if __name__ == "__main__":
    unittest.main()
