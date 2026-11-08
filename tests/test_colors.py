"""craft.colors: the one public home of the block and biome colors.

Colors are choices, so nothing here pins a value -- a color is meant to
change when somebody decides it should. What is pinned is that the public
tables are enough: a name resolved from them alone, in the order the Unity
app's generated palettes apply them -- the exact table; a cut it does not
name by the block it is cut from (``blockinfo.cut_shapes()``); then the
rules, the woods and unknown -- comes out as :class:`ColorMap` draws it.
If it ever does not, a palette generated from these tables has silently
stopped being the renderer's twin. The app's generators, and the tests
that hold what they emit to these tables, live with the app.
"""

from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from fixtures import block_shapes  # noqa: E402
from heaton_voxel.craft import biomes, blockinfo, colors  # noqa: E402
from heaton_voxel.craft.render import UNGENERATED, ColorMap  # noqa: E402

_AIR = ("minecraft:air", "minecraft:cave_air", "minecraft:void_air")

#: Names no table holds, each landing on a different rung of the ladder.
_MODDED = ("modded:oak_crate", "modded:thing", "modded:basalt_pillar",
           "modded:cherry_shelf")

_MODDED_BIOMES = ("modded:deep_ocean_trench", "modded:windswept_mesa",
                  "modded:sparse_jungle_edge", "modded:nowhere", "")


def _resolve(name, exact, cut_from, rules, woods, wood_fallback, unknown):
    """The generated ``BlockPalette.Of``, rung for rung, over its tables:
    the exact table; a cut it does not name goes by its block (the
    generator's ``CutFrom``); then the rules, the woods and unknown, all
    asked of that block."""
    drawn = name if name in exact else cut_from.get(name, name)
    if drawn in exact:
        return exact[drawn]
    for needle, rgb in rules:
        if needle in drawn:
            return rgb
    if any(wood in drawn for wood in woods):
        return wood_fallback
    return unknown


def _as_generated(name: str, table=None):
    """A block resolved from the public tables alone (``table`` in place
    of the exact one, if given), in the order the generated
    ``BlockPalette.Of`` applies them."""
    table = colors.block_palette() if table is None else dict(table)
    table.update(dict.fromkeys(_AIR, UNGENERATED))   # render's own background
    return _resolve(name, table, blockinfo.cut_shapes(),
                    colors.block_heuristics(), colors.WOOD_SPECIES,
                    colors.WOOD_FALLBACK, colors.BLOCK_UNKNOWN)


def _biome_as_generated(name: str):
    table = colors.biome_palette()
    if name in table:
        return table[name]
    for needle, rgb in colors.biome_heuristics():
        if needle in name:
            return rgb
    return colors.BIOME_UNKNOWN


class ColorTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.names = sorted({name for name, _, _ in block_shapes()})

    def test_colormap_draws_what_the_public_tables_say(self):
        self.assertEqual(len(self.names), 1286, "every block the game has")
        drawn = ColorMap()
        disagree = [name for name in (*self.names, *_MODDED)
                    if drawn(name) != _as_generated(name)]
        self.assertEqual(disagree, [], "drawn other than the tables say")
        # The two bottom rungs are reached, so the ladder is tested whole.
        self.assertEqual(drawn("modded:oak_crate"), colors.WOOD_FALLBACK)
        self.assertEqual(drawn("modded:thing"), colors.BLOCK_UNKNOWN)

    def test_a_stair_slab_or_wall_is_drawn_as_its_block(self):
        """A cut is the color of what it is cut from, where one gray used
        to draw every stair and slab the table does not name: an oak stair
        is oak planks, a quartz slab quartz."""
        drawn = ColorMap()
        for shape, block in (
                ("minecraft:oak_stairs", "minecraft:oak_planks"),
                ("minecraft:quartz_slab", "minecraft:quartz_block"),
                ("minecraft:white_wool_slab", "minecraft:white_wool"),
                ("minecraft:cobblestone_stairs", "minecraft:cobblestone"),
                ("minecraft:stone_brick_wall", "minecraft:stone_bricks"),
                ("minecraft:petrified_oak_slab", "minecraft:oak_planks")):
            with self.subTest(shape=shape):
                self.assertEqual(drawn(shape), drawn(block))
        self.assertNotEqual(drawn("minecraft:oak_stairs"),
                            drawn("minecraft:quartz_stairs"),
                            "two stairs are no longer one gray")
        self.assertEqual(drawn.unknown, {}, "every one of those has a color")

    def test_every_cut_the_game_has_is_drawn_as_its_block(self):
        """Every one the table does not name, which today is all of them:
        a cut given a color of its own keeps it (the test below)."""
        drawn = ColorMap()
        cuts = {name: blockinfo.material_of(name)[0] for name in self.names
                if blockinfo.material_of(name)[1] != "block"
                and name not in drawn.table}
        self.assertTrue(cuts, "the game's stairs, slabs and walls")
        wrong = [f"{shape} {drawn(shape)} -> {block} {drawn(block)}"
                 for shape, block in cuts.items()
                 if drawn(shape) != drawn(block)]
        self.assertEqual(wrong, [], "cuts drawn other than their blocks")

    def test_a_cut_the_table_names_keeps_its_own_color(self):
        """The table comes first: a stair given a color of its own is drawn
        in it, not as its block, and the rung draws only the cuts the table
        does not name -- in ColorMap and in the generated ``Of`` alike."""
        own = (1, 2, 3)
        table = colors.block_palette()
        table["minecraft:oak_stairs"] = own
        drawn = ColorMap()
        drawn.table["minecraft:oak_stairs"] = own
        self.assertEqual(drawn("minecraft:oak_stairs"), own)
        self.assertEqual(_as_generated("minecraft:oak_stairs", table), own)
        for shape, block in (("minecraft:oak_slab", "minecraft:oak_planks"),
                             ("minecraft:spruce_stairs",
                              "minecraft:spruce_planks")):
            with self.subTest(shape=shape):
                self.assertEqual(drawn(shape), drawn(block))
                self.assertEqual(_as_generated(shape, table), drawn(block))

    def test_a_modded_cut_keeps_the_stair_and_slab_gray(self):
        """The rung is the game's cuts and never a guess from a name: a
        modded quartz stair is not drawn as quartz, though its stem says
        quartz, while the game's own is. A rule still places it, so it is
        not unknown either."""
        drawn = ColorMap()
        rules = dict(colors.block_heuristics())
        self.assertEqual(drawn("minecraft:quartz_stairs"),
                         drawn("minecraft:quartz_block"))
        self.assertEqual(drawn("modded:quartz_stairs"), rules["_stairs"])
        self.assertEqual(drawn("modded:quartz_slab"), rules["_slab"])
        self.assertEqual(drawn.unknown, {})

    def test_an_uncolored_block_is_counted_for_every_call_on_its_cuts(self):
        """A cut whose block has no color is unknown, and counted on every
        call -- the cache does not swallow the repeats -- under that block,
        the one entry that would color all its cuts. It used to be drawn
        in the stair gray and never counted, so the table never heard it
        was short. The block is made uncolored here, by taking it out of a
        map's table, so coloring the ones 26.3 left without one changes
        nothing below: it is one whose name no rule and no wood places."""
        needles = [needle for needle, _ in colors.block_heuristics()]
        cut_from = {name: blockinfo.material_of(name)[0] for name in self.names
                    if blockinfo.material_of(name)[1] != "block"}
        blocks = sorted(block for block in set(cut_from.values())
                        if not any(n in block for n in needles)
                        and not any(w in block for w in colors.WOOD_SPECIES))
        self.assertTrue(blocks, "a cut's block that only the table colors")
        block = blocks[0]
        cuts = sorted(name for name, base in cut_from.items() if base == block)
        drawn = ColorMap()
        drawn.table.pop(block, None)
        for _ in range(3):
            self.assertEqual(drawn(cuts[0]), colors.BLOCK_UNKNOWN, cuts[0])
        self.assertEqual(drawn.unknown, {block: 3},
                         f"{cuts[0]} counted on every call, under {block}")
        for name in (*cuts[1:], block):
            drawn(name)
        self.assertEqual(drawn.unknown, {block: 3 + len(cuts)},
                         "one entry for the block, however it was asked for")

    def test_biome_color_resolves_the_way_the_generated_twin_does(self):
        for name in (*sorted(colors.BIOME_COLORS), *_MODDED_BIOMES):
            with self.subTest(name=name):
                self.assertEqual(colors.biome_color(name),
                                 _biome_as_generated(name))
        self.assertEqual(colors.biome_color("modded:nowhere"),
                         colors.BIOME_UNKNOWN)

    def test_what_a_tower_holds_has_a_color_of_its_own(self):
        """The hotel drew its ladders, rails, iron doors, sponges and wall
        banners gray, all five unmapped."""
        table = colors.block_palette()
        for name in ("ladder", "rail", "powered_rail", "detector_rail",
                     "activator_rail", "iron_door", "sponge", "wet_sponge",
                     "yellow_wall_banner", "cyan_wall_banner"):
            self.assertIn(f"minecraft:{name}", table)
        self.assertEqual(table["minecraft:yellow_wall_banner"],
                         table["minecraft:yellow_banner"])

    def test_a_stained_pane_is_drawn_in_its_dye(self):
        """The rule a facade's glass follows is its colors: gray and light
        gray panes drew as one clear glass until the dye family took the
        pane too, so no picture could show one."""
        draw = ColorMap()
        self.assertEqual(draw("minecraft:gray_stained_glass_pane"),
                         draw("minecraft:gray_wool"))
        self.assertEqual(len({draw(f"minecraft:{dye}_stained_glass_pane")
                              for dye in ("gray", "light_gray", "black")}), 3)
        self.assertEqual(draw("minecraft:glass_pane"), draw("minecraft:glass"))

    def test_the_color_table_carries_no_air(self):
        table = colors.block_palette()
        for name in _AIR:
            with self.subTest(name=name):
                self.assertNotIn(name, table, "air is not a color")
                self.assertEqual(ColorMap()(name), UNGENERATED,
                                 "render still draws it as the background")

    def test_accessors_hand_out_copies(self):
        palette = colors.block_palette()
        palette["minecraft:stone"] = (1, 2, 3)
        palette["modded:intruder"] = (4, 5, 6)
        rules = colors.block_heuristics()
        rules.insert(0, ("", (7, 8, 9)))
        biome_table = colors.biome_palette()
        biome_table["minecraft:plains"] = (1, 1, 1)
        biome_rules = colors.biome_heuristics()
        biome_rules.clear()

        self.assertNotEqual(colors.block_palette()["minecraft:stone"], (1, 2, 3))
        self.assertNotIn("modded:intruder", colors.block_palette())
        self.assertNotEqual(colors.block_heuristics()[0], ("", (7, 8, 9)))
        self.assertNotEqual(colors.biome_palette()["minecraft:plains"], (1, 1, 1))
        self.assertTrue(colors.biome_heuristics())
        self.assertNotEqual(ColorMap()("minecraft:stone"), (1, 2, 3),
                            "and a map made after sees the real table")


class BiomeCompatTests(unittest.TestCase):
    def test_biomes_still_answers_for_its_colors(self):
        """Every name craft.biomes had keeps answering, from the one table."""
        self.assertIs(biomes.BIOME_COLORS, colors.BIOME_COLORS)
        self.assertEqual(biomes.build_palette(), colors.biome_palette())
        self.assertEqual(biomes.heuristics(), colors.biome_heuristics())
        self.assertEqual(biomes.UNKNOWN, colors.BIOME_UNKNOWN)
        for name in (*sorted(colors.BIOME_COLORS), *_MODDED_BIOMES):
            with self.subTest(name=name):
                self.assertEqual(biomes.color_for(name), colors.biome_color(name))
        self.assertEqual(len(colors.BIOME_COLORS), 66)


if __name__ == "__main__":
    unittest.main(verbosity=2)
