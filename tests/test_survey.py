"""Tests for heaton_voxel.craft.survey: reading the ground before building.

The promises under test are the ones a survey exists to keep. "The
surface" is several different numbers and picking the wrong one is
silent, so a probe must step past snow and water to the block you would
actually stand on and report what it stepped past as its own number --
never folded into the height. A column with nothing to report must come
back ``None`` rather than a plausible invention, and say why: no chunk, a
partly generated one, or nothing there -- while a chunk that cannot be
read raises, because corruption is not empty ground. And the answer must
not depend on which of the game's maps a chunk still carries:
``rebuild_heightmaps`` keeps only ``WORLD_SURFACE``, and a probe read from
the blocks has to be the probe read from the game's maps.

Snow comes two ways. The legacy fixture has none anywhere, so some is
built; the modern fixture's chunk (-3, 19) holds 105 columns of natural
snow under maps the game wrote, which is the case that matters -- no
motion-blocking map counts a snow layer, so they all sit under it. Water
comes from the legacy fixture's open ocean, real game data with a real
OCEAN_FLOOR map under 29 blocks of sea.

The reachability half is checked the same way, on ground built to have
the problem: a step you walk against one you jump, a stack that seals a
room every plan draws finished, and a pit you can fall into and not climb
out of -- the last being the one a forward-only search calls reached and
says nothing more about.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from heaton_voxel.craft.survey import (  # noqa: E402
    _CLIMBABLE,
    KIND_GLYPHS,
    MONSTER_SPAWN_LIGHT,
    SpawnCell,
    StepSpace,
    SurfaceProbe,
    SurveyMap,
    classify,
    reachable,
    spawnable,
    step_kind,
    survey,
    walk_surface,
)
from fixtures import (  # noqa: E402
    MODERN_LEVEL_DAT,
    MODERN_REGION,
    block_shapes,
    dimension_types,
)
from heaton_voxel.nbt import NBTError  # noqa: E402
from heaton_voxel.world import END, NETHER, OVERWORLD, MinecraftSave  # noqa: E402
from tests.test_render import SEA_X0, SEA_Z0, make_world  # noqa: E402
from tests.test_world import make_save  # noqa: E402

# The taiga village chunk: dirt paths, spruce buildings, farmland, a torch.
VILLAGE_X0, VILLAGE_Z0 = -28 * 16, 14 * 16
# Partly generated, and so representative of ground a survey must decline.
PARTIAL_CHUNK = (-32, 29)


class ClassifyTests(unittest.TestCase):
    """The verdicts. Game knowledge and taste, which is why it lives in craft."""

    def test_a_representative_spread(self):
        expected = {
            "minecraft:grass_block": "ground",
            "minecraft:dirt": "ground",
            "minecraft:podzol": "ground",
            "minecraft:dirt_path": "path",
            "minecraft:sand": "sand",
            "minecraft:gravel": "sand",
            "minecraft:stone": "stone",
            "minecraft:deepslate": "stone",
            "minecraft:iron_ore": "stone",
            "minecraft:water": "water",
            "minecraft:bubble_column": "water",
            "minecraft:ice": "ice",
            "minecraft:powder_snow": "ice",
            "minecraft:oak_log": "tree",
            "minecraft:spruce_leaves": "tree",
            "minecraft:short_grass": "plant",
            "minecraft:large_fern": "plant",
            "minecraft:farmland": "farm",
            "minecraft:wheat": "farm",
            "minecraft:cobblestone": "paving",
            "minecraft:stone_bricks": "paving",
            "minecraft:polished_andesite": "paving",
            "minecraft:oak_planks": "wood",
            "minecraft:spruce_door": "wood",
            "minecraft:glowstone": "light",
            "minecraft:torch": "light",
            "minecraft:ochre_froglight": "light",
            "minecraft:glass": "glass",
            "minecraft:tinted_glass": "glass",
            "minecraft:sponge": "other",
        }
        for name, kind in expected.items():
            with self.subTest(name=name):
                self.assertEqual(classify(name), kind)

    def test_snow_block_is_ground_but_the_layer_is_not_a_surface_material(self):
        """A snow block is real ground; the thin layer is a covering, and
        a probe steps past it rather than classifying it."""
        self.assertEqual(classify("minecraft:snow_block"), "ground")

    def test_shape_suffixes_take_their_material_from_the_species(self):
        """The one genuinely ambiguous case: a stair carries no material of
        its own, so the name is all there is to go on."""
        wooden = ["minecraft:oak_stairs", "minecraft:spruce_stairs",
                  "minecraft:dark_oak_stairs", "minecraft:birch_slab",
                  "minecraft:cherry_slab", "minecraft:mangrove_fence"]
        stony = ["minecraft:stone_stairs", "minecraft:cobblestone_wall",
                 "minecraft:stone_brick_stairs", "minecraft:andesite_slab",
                 "minecraft:mud_brick_wall", "minecraft:deepslate_tile_slab"]
        for name in wooden:
            with self.subTest(name=name):
                self.assertEqual(classify(name), "wood")
        for name in stony:
            with self.subTest(name=name):
                self.assertEqual(classify(name), "paving")

    def test_every_verdict_has_a_glyph(self):
        """The character map cannot draw a kind it has no character for."""
        names = ["minecraft:stone", "minecraft:water", "minecraft:oak_log",
                 "minecraft:short_grass", "minecraft:farmland",
                 "minecraft:dirt_path", "minecraft:grass_block",
                 "minecraft:sand", "minecraft:cobblestone",
                 "minecraft:oak_planks", "minecraft:torch", "minecraft:glass",
                 "minecraft:ice", "minecraft:sponge"]
        for name in names:
            with self.subTest(name=name):
                self.assertIn(classify(name), KIND_GLYPHS)


class SyntheticGroundTests(unittest.TestCase):
    """Snow built by hand, then ``rebuild_heightmaps``: the chunk keeps only
    ``WORLD_SURFACE``, which lands on the snow, and is read from its blocks.
    Natural snow under the game's own maps is in :class:`GameMapTests`."""

    CHUNKS = [(x, z) for x in range(-1, 2) for z in range(-1, 2)]

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="survey-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, self.CHUNKS)
        self.save = MinecraftSave.load(self.dir)
        self.save.fill((-16, 60, -16), (31, 63, 31), "minecraft:stone")
        self.save.fill((2, 64, 2), (4, 64, 4), "minecraft:snow[layers=3]")
        self.save.set_block(8, 64, 8, "minecraft:dirt_path")
        self.save.set_block(8, 65, 8, "minecraft:snow[layers=1]")
        self.save.rebuild_heightmaps((-16, 0, -16), (31, 0, 31))

    def test_snow_is_stepped_past_and_reported_as_its_own_number(self):
        probe = walk_surface(self.save, 3, 3)
        self.assertIsNotNone(probe)
        self.assertEqual(probe.y, 63, "the ground is under the snow")
        self.assertEqual(probe.block.name, "minecraft:stone")
        self.assertEqual(probe.kind, "stone")
        self.assertEqual(probe.snow, 3, "three layers, from the layers property")
        self.assertEqual(probe.top_y, 64, "WORLD_SURFACE lands on the snow; "
                                          "no motion-blocking map does")
        self.assertEqual(probe.top.name, "minecraft:snow")
        self.assertTrue(probe.buried, "something is resting on the ground here")

    def test_the_path_under_the_snow_is_what_the_survey_reports(self):
        """The case that misleads a top-down render: the map draws the
        snow, the survey wants the path underneath."""
        probe = walk_surface(self.save, 8, 8)
        self.assertEqual(probe.y, 64)
        self.assertEqual(probe.block.name, "minecraft:dirt_path")
        self.assertEqual(probe.kind, "path")
        self.assertEqual(probe.glyph, KIND_GLYPHS["path"])
        self.assertEqual(probe.snow, 1)
        self.assertTrue(probe.buried)

    def test_bare_ground_reports_no_snow_and_is_not_buried(self):
        probe = walk_surface(self.save, 0, 0)
        self.assertEqual(probe.y, 63)
        self.assertEqual(probe.snow, 0)
        self.assertEqual(probe.water, 0)
        self.assertEqual(probe.top_y, probe.y)
        self.assertFalse(probe.buried)

    def test_ungenerated_ground_has_nothing_to_report(self):
        self.assertIsNone(walk_surface(self.save, 500, 500))

    def test_snow_depth_shows_up_in_the_map_and_the_repr(self):
        found = survey(self.save, (2, 2), (4, 4))
        self.assertEqual({p.snow for p in found.probes.values()}, {3})
        self.assertIn("3 snow", repr(found.probe(3, 3)))
        rows = found.text(mode="snow").splitlines()
        self.assertEqual(rows[-1].split()[-1], "333")


class OceanTests(unittest.TestCase):
    """Real game data: 29 blocks of sea over a sand floor."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="survey-sea-")
        cls.save = MinecraftSave.load(make_world(cls.dir))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_water_is_stepped_past_to_the_seabed(self):
        x, z = SEA_X0 + 4, SEA_Z0 + 4
        dim = self.save.dimension(OVERWORLD)
        floor = dim.surface_y(x, z, "OCEAN_FLOOR")
        surface = dim.surface_y(x, z, "WORLD_SURFACE")
        probe = walk_surface(self.save, x, z)
        self.assertIsNotNone(probe)
        self.assertEqual(probe.y, floor, "the standing height is the seabed")
        self.assertEqual(probe.water, surface - floor, "depth, not height")
        self.assertGreater(probe.water, 0)
        self.assertNotIn(probe.block.name, ("minecraft:water",
                                            "minecraft:bubble_column"))
        self.assertEqual(probe.top_y, surface)
        self.assertTrue(probe.buried)
        self.assertIn("deep", repr(probe))

    def test_a_water_depth_map_reads_as_depths(self):
        found = survey(self.save, (SEA_X0, SEA_Z0), (SEA_X0 + 3, SEA_Z0 + 3))
        self.assertEqual(found.counts.get("water"), None,
                         "the seabed is sand, not water: the sea was stepped past")
        depths = {p.water for p in found.probes.values()}
        self.assertTrue(all(d > 0 for d in depths), depths)
        # Two-digit depths cannot fit one character, and say so with '+'.
        self.assertIn("+", found.text(mode="water"))


#: The modern fixture's chunk with natural snow: 105 columns topped by a
#: snow layer, three of them on birch leaves.
SNOW_CHUNK = (-3, 19)
#: A kelp tip at the waterline of the legacy fixture's sea, over a gravel
#: bed eleven blocks down.
KELP_COLUMN = (-92, 25)


def _modern_world(tmpdir: str) -> str:
    world = os.path.join(tmpdir, "modern")
    region_dir = os.path.join(world, "dimensions/minecraft/overworld/region")
    os.makedirs(region_dir)
    shutil.copyfile(MODERN_REGION, os.path.join(region_dir, "r.-1.0.mca"))
    shutil.copyfile(MODERN_LEVEL_DAT, os.path.join(world, "level.dat"))
    return world


def _finished_chunks(save) -> list[tuple[int, int]]:
    dim = save.dimension(OVERWORLD)
    return sorted((int(c.data["xPos"]), int(c.data["zPos"]))
                  for region in dim for c in region
                  if str(c.data.get("Status")) == "minecraft:full")


def _columns(chunks):
    for chunk_x, chunk_z in chunks:
        for lz in range(16):
            for lx in range(16):
                yield chunk_x * 16 + lx, chunk_z * 16 + lz


def _fields(probe):
    """A probe as a tuple of every field, for comparing two readings."""
    if probe is None:
        return None
    return tuple(getattr(probe, name) for name in SurfaceProbe.__slots__)


def _pack_maps(save, chunk, **tops):
    """Write the game's maps onto a hand-made chunk by hand: each map named
    ``MAP=y`` at that height in every column, the way the game packs it."""
    from heaton_voxel.blocks import heightmap_bits, pack_indices
    from heaton_voxel.nbt import TagCompound

    dim = save.dimension(OVERWORLD)
    accessor = dim.blocks(*chunk)
    bits = heightmap_bits(accessor.min_y, accessor.max_y)
    dim.chunk(*chunk).data["Heightmaps"] = TagCompound({
        name: pack_indices([y - accessor.min_y + 1] * 256, 256, bits)
        for name, y in tops.items()})
    accessor.drop_heightmap_cache()


class GameMapTests(unittest.TestCase):
    """Real game data, read with the game's maps and without them.

    ``rebuild_heightmaps`` writes ``WORLD_SURFACE`` and deletes the other
    three maps, so every chunk an edit touches loses the maps that know
    about water, leaves and snow. The reader then walks the blocks by the
    game's own heightmap tags, and has to give back the probe the game's
    maps gave -- on every column of every finished chunk of both fixtures.
    """

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="survey-maps-")
        cls.legacy = make_world(cls.dir)
        cls.modern = _modern_world(cls.dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def _snow_columns(self, save):
        """The snow-topped columns of SNOW_CHUNK, as ``(x, z, below)``."""
        dim = save.dimension(OVERWORLD)
        found = []
        for x, z in _columns([SNOW_CHUNK]):
            top = dim.surface_y(x, z, "WORLD_SURFACE")
            if save.get_block(x, top, z).name == "minecraft:snow":
                found.append((x, z, save.get_block(x, top - 1, z).name))
        return found

    def test_natural_snow_under_game_written_maps_is_counted(self):
        """No motion-blocking map counts a snow layer, so under the game's
        own maps the snow is never where the map lands: it is the cell
        above. Read the snow from there, or report none on all 105."""
        save = MinecraftSave.load(self.modern)
        columns = self._snow_columns(save)
        self.assertEqual(len(columns), 105, "fixture drifted")
        missed, misplaced = [], []
        for x, z, below in columns:
            probe = walk_surface(save, x, z)
            if probe.snow != 1:
                missed.append((x, z, probe))
            if below != "minecraft:birch_leaves" and probe.y != probe.top_y - 1:
                misplaced.append((x, z, probe))
        self.assertEqual(missed[:3], [], f"{len(missed)} snowed columns read dry")
        self.assertEqual(misplaced[:3], [], f"{len(misplaced)} not under their snow")

    def test_snow_on_a_canopy_is_not_ground_after_a_rebuild(self):
        """Three columns hold snow on birch leaves. A rebuild leaves only
        WORLD_SURFACE, which lands on that snow; the probe must still stand
        on the ground the game's MOTION_BLOCKING_NO_LEAVES named, not on
        the leaves one step under the snow."""
        save = MinecraftSave.load(self.modern)
        dim = save.dimension(OVERWORLD)
        canopy = [(x, z) for x, z, below in self._snow_columns(save)
                  if below == "minecraft:birch_leaves"]
        self.assertEqual(len(canopy), 3, "fixture drifted")
        ground = {cell: dim.surface_y(*cell, "MOTION_BLOCKING_NO_LEAVES")
                  for cell in canopy}
        origin = (SNOW_CHUNK[0] * 16, 0, SNOW_CHUNK[1] * 16)
        save.rebuild_heightmaps(origin, origin)
        for x, z in canopy:
            with self.subTest(column=(x, z)):
                probe = walk_surface(save, x, z)
                self.assertNotEqual(probe.block.name, "minecraft:birch_leaves")
                self.assertEqual(probe.y, ground[(x, z)])

    def test_the_reader_answers_the_same_with_or_without_the_games_maps(self):
        """Every field of every probe, before and after a rebuild, over
        every column of every finished chunk. Without the maps that know
        about fluid, a reader that trusted WORLD_SURFACE would call the
        whole sea dry."""
        for world, (least_wet, least_snowed) in ((self.legacy, (3000, 0)),
                                                 (self.modern, (0, 100))):
            with self.subTest(world=os.path.basename(world)):
                save = MinecraftSave.load(world)
                chunks = _finished_chunks(save)
                before = {cell: walk_surface(save, *cell)
                          for cell in _columns(chunks)}
                self.assertNotIn(None, before.values())
                # Not a vacuous pass: the sea and the snow are in there.
                self.assertGreaterEqual(
                    sum(1 for p in before.values() if p.water), least_wet)
                self.assertGreaterEqual(
                    sum(1 for p in before.values() if p.snow), least_snowed)
                for chunk_x, chunk_z in chunks:
                    origin = (chunk_x * 16, 0, chunk_z * 16)
                    save.rebuild_heightmaps(origin, origin)
                differ = []
                for cell, probe in before.items():
                    after = _fields(walk_surface(save, *cell))
                    if after != _fields(probe):
                        differ.append((cell, probe, after))
                # The count and the first few, never a 4,000-entry diff.
                self.assertEqual(len(differ), 0, f"first: {differ[:2]}")

    def test_the_block_walk_reproduces_every_game_map(self):
        """The walk is the game's own derivation: WORLD_SURFACE (not air),
        MOTION_BLOCKING (the tag, or fluid), MOTION_BLOCKING_NO_LEAVES (the
        no-leaves tag, or fluid) and OCEAN_FLOOR (the tag alone), equal to
        the stored maps on every column of every finished chunk."""
        from heaton_voxel.craft.survey import _game_maps

        names = ("WORLD_SURFACE", "MOTION_BLOCKING",
                 "MOTION_BLOCKING_NO_LEAVES", "OCEAN_FLOOR")
        for world in (self.legacy, self.modern):
            with self.subTest(world=os.path.basename(world)):
                save = MinecraftSave.load(world)
                dim = save.dimension(OVERWORLD)
                wrong, checked = [], 0
                for x, z in _columns(_finished_chunks(save)):
                    accessor = dim.blocks(x >> 4, z >> 4)
                    stored = tuple(accessor.surface_y(x & 15, z & 15, name)
                                   for name in names)
                    walked = _game_maps(accessor, x & 15, z & 15)
                    checked += 1
                    if walked != stored:
                        wrong.append(((x, z), walked, stored))
                self.assertGreaterEqual(checked, 1024)
                self.assertEqual(len(wrong), 0, f"(column, walked, stored): {wrong[:3]}")

    def test_kelp_at_the_waterline_is_water_not_ground(self):
        """A kelp tip reaches the surface, and every motion-blocking map
        stops at it because it holds water. It is not ground: the probe
        steps through it, and the kelp under it, to the bed."""
        save = MinecraftSave.load(self.legacy)
        x, z = KELP_COLUMN
        self.assertEqual(save.get_block(x, 62, z).name, "minecraft:kelp",
                         "fixture drifted")
        probe = walk_surface(save, x, z)
        self.assertEqual(probe.y, 51)
        self.assertEqual(probe.water, 11)
        self.assertEqual(probe.waterline, 62)
        self.assertNotEqual(probe.kind, "plant")
        self.assertTrue(probe.wet)


class ColumnReasonTests(unittest.TestCase):
    """Why a column has no probe, and the chunks with no maps at all."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="survey-reasons-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, [(0, 0), (1, 0), (0, 1), (1, 1)])
        self.save = MinecraftSave.load(self.dir)
        self.dim = self.save.dimension(OVERWORLD)

    def test_absent_columns_say_why(self):
        """No chunk, a partly generated chunk, a column of nothing, and a
        chunk that cannot be read: four kinds of no probe. The first three
        are None; the last is corruption, and raises -- while a survey
        across it records it and keeps going."""
        from heaton_voxel.craft.errors import NoSurface
        from heaton_voxel.nbt import TagInt, TagString

        self.dim.chunk(1, 1).data["DataVersion"] = TagInt(100)
        self.dim.chunk(0, 1).data["Status"] = TagString("minecraft:carvers")
        self.save.fill((0, 50, 0), (15, 63, 15), "minecraft:stone")
        # (1, 0) is air all the way up; x 32 and beyond was never generated.

        found = survey(self.save, (0, 0), (47, 31), step=8)
        reasons = {cell: found.missing.get(cell) for cell in found.probes}
        expect = {}
        for (x, z) in found.probes:
            expect[(x, z)] = {(0, 0): None, (1, 0): "empty", (0, 1): "partial",
                              (1, 1): "unreadable"}.get((x >> 4, z >> 4), "ungenerated")
        self.assertEqual(reasons, expect)
        self.assertEqual(len(found), 4)
        self.assertIn("20 columns with no surface (8 ungenerated, 4 unreadable, "
                      "4 partly generated, 4 empty)", repr(found))

        for cell in ((40, 8), (24, 8), (8, 24)):
            with self.subTest(cell=cell):
                self.assertIsNone(walk_surface(self.save, *cell))
        with self.assertRaises(NBTError) as caught:
            walk_surface(self.save, 24, 24)
        self.assertNotIsInstance(caught.exception, NoSurface,
                                 "corruption must not read as no ground")

    def test_a_damaged_heightmap_is_surveyed_around(self):
        """A map that contradicts its own chunk is corruption: the probe
        raises it, and a survey records the columns as unreadable and goes
        on with the rest -- never as ground that is not there."""
        from heaton_voxel.craft.errors import NoSurface
        from heaton_voxel.nbt import TagLongArray

        self.save.fill((0, 50, 0), (15, 63, 15), "minecraft:stone")
        self.save.rebuild_heightmaps((0, 0, 0), (15, 0, 15))
        maps = self.dim.chunk(0, 0).data["Heightmaps"]
        maps["WORLD_SURFACE"] = TagLongArray(list(maps["WORLD_SURFACE"])[:-1])
        with self.assertRaises(NBTError) as caught:
            walk_surface(self.save, 3, 3)
        self.assertNotIsInstance(caught.exception, NoSurface)
        found = survey(self.save, (0, 0), (31, 15), step=8)
        self.assertEqual({found.missing[(x, z)] for x, z in found.probes if x < 16},
                         {"unreadable"})
        self.assertEqual({found.missing[(x, z)] for x, z in found.probes if x >= 16},
                         {"empty"})

    def test_a_finished_chunk_with_no_maps_is_read_from_its_blocks(self):
        """A chunk the game finished, holding no maps at all -- made by hand,
        or stripped -- is still ground: read it from its blocks."""
        self.save.fill((0, 50, 0), (15, 62, 15), "minecraft:stone")
        self.save.set_block(3, 63, 3, "minecraft:grass_block")
        self.save.set_block(3, 64, 3, "minecraft:torch")
        self.assertIsNone(self.save.surface_y(3, 3, "WORLD_SURFACE"),
                          "the chunk must carry no map")
        probe = walk_surface(self.save, 3, 3)
        self.assertIsNotNone(probe)
        self.assertEqual((probe.y, probe.block.name), (63, "minecraft:grass_block"))
        self.assertEqual((probe.top_y, probe.top.name), (64, "minecraft:torch"))
        self.assertTrue(probe.buried)
        self.assertEqual(probe.status, "minecraft:full")

    def test_a_map_is_not_trusted_past_the_block_it_names(self):
        """A map the game wrote before 26.3 stops at a sign, which nothing
        stands on and today's tag does not count; a map gone stale names
        air. Both are walked down from, to the ground under them."""
        self.save.fill((0, 50, 0), (15, 63, 15), "minecraft:stone")
        self.save.set_block(8, 64, 8, "minecraft:oak_sign")
        _pack_maps(self.save, (0, 0), WORLD_SURFACE=64, MOTION_BLOCKING=64,
                   MOTION_BLOCKING_NO_LEAVES=64, OCEAN_FLOOR=64)
        sign = walk_surface(self.save, 8, 8)
        self.assertEqual((sign.y, sign.block.name), (63, "minecraft:stone"))
        self.assertEqual((sign.top_y, sign.top.name), (64, "minecraft:oak_sign"))
        stale = walk_surface(self.save, 3, 3)
        self.assertEqual((stale.y, stale.top_y), (63, 63))

    def test_a_map_above_the_world_is_stale_not_corrupt(self):
        """A map naming a cell above the world names air, as surely as one
        gone stale a block lower: the toolkit once counted maps from the
        wrong floor and wrote them sixteen high, and in such a chunk most
        columns name air while a few name the sky above the build limit.
        Every column is walked down from alike -- on the game's maps and on
        a rebuilt chunk's lone WORLD_SURFACE -- and reads as a rebuild
        reads it, never as one chunk half unreadable."""
        self.save.fill((0, 50, 0), (15, 63, 15), "minecraft:stone")
        self.save.rebuild_heightmaps((0, 0, 0), (15, 0, 15))
        rebuilt = walk_surface(self.save, 8, 8)
        self.assertEqual((rebuilt.y, rebuilt.top_y), (63, 63))
        truth = _fields(rebuilt)
        high = self.dim.blocks(0, 0).max_y + 15
        for how, maps in (("mapped", dict(WORLD_SURFACE=high, MOTION_BLOCKING=high,
                                          MOTION_BLOCKING_NO_LEAVES=high,
                                          OCEAN_FLOOR=63)),
                          ("rebuilt", dict(WORLD_SURFACE=high))):
            with self.subTest(read=how):
                _pack_maps(self.save, (0, 0), **maps)
                self.assertEqual(self.save.surface_y(8, 8, "WORLD_SURFACE"), high)
                self.assertEqual(_fields(walk_surface(self.save, 8, 8)), truth)
                found = survey(self.save, (0, 0), (15, 15), step=8)
                self.assertEqual((found.missing, len(found)), ({}, 4))

    def test_below_reads_the_floor_under_a_roof(self):
        """``below=`` asks for the floor under a point: inside a building,
        or anywhere a roof stands over the ground."""
        self.save.fill((0, 50, 0), (15, 63, 15), "minecraft:stone")
        self.save.fill((0, 70, 0), (15, 70, 15), "minecraft:stone_bricks")
        roof = walk_surface(self.save, 8, 8)
        self.assertEqual((roof.y, roof.block.name), (70, "minecraft:stone_bricks"))
        self.assertTrue(roof.lid, "a one-block roof is a lid over the room")
        floor = walk_surface(self.save, 8, 8, below=69)
        self.assertEqual((floor.y, floor.top_y), (63, 63))
        self.assertFalse(floor.lid)
        self.assertIsNone(walk_surface(self.save, 8, 8, below=-100),
                          "below the world there is nothing to stand on")


class IceTests(unittest.TestCase):
    """An ice sheet is stood on, and what is under it is reported."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="survey-ice-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, [(0, 0)])
        self.save = MinecraftSave.load(self.dir)
        self.save.fill((0, 50, 0), (15, 59, 15), "minecraft:stone")

    def _readings(self, top: int = 63):
        """The same chunk read twice: from its blocks after a rebuild, and
        from maps packed by hand as the game writes them -- every map on
        the ice at ``top``, OCEAN_FLOOR included, since ice is in the tag."""
        self.save.rebuild_heightmaps((0, 0, 0), (15, 0, 15))
        yield "walked", walk_surface(self.save, 8, 8)
        _pack_maps(self.save, (0, 0), WORLD_SURFACE=top, MOTION_BLOCKING=top,
                   MOTION_BLOCKING_NO_LEAVES=top, OCEAN_FLOOR=top)
        yield "mapped", walk_surface(self.save, 8, 8)

    def test_a_frozen_pond_reports_its_ice_water_and_bed(self):
        self.save.fill((0, 60, 0), (15, 62, 15), "minecraft:water[level=0]")
        self.save.fill((0, 63, 0), (15, 63, 15), "minecraft:ice")
        for how, probe in self._readings():
            with self.subTest(read=how):
                self.assertEqual((probe.y, probe.block.name), (63, "minecraft:ice"))
                self.assertEqual(probe.kind, "ice")
                self.assertEqual((probe.ice, probe.water, probe.bed_y), (1, 3, 59))
                self.assertEqual(probe.waterline, 63)
                self.assertFalse(probe.lid)
                self.assertTrue(probe.wet)
                self.assertIn("1 ice", repr(probe))

    def test_ice_over_a_room_is_a_lid(self):
        """Ice with air under it is a ceiling: light it to melt it and the
        water pours into whatever is below."""
        self.save.fill((0, 63, 0), (15, 63, 15), "minecraft:ice")
        for how, probe in self._readings():
            with self.subTest(read=how):
                self.assertEqual(probe.y, 63)
                self.assertEqual((probe.ice, probe.water, probe.bed_y), (1, 0, None))
                self.assertTrue(probe.lid)
                self.assertIn("lid", repr(probe))

    def test_ice_on_rock_is_ground_like_any_other(self):
        """A sheet is ice over water or air; ice lying on stone is a block
        you stand on, and its own bed."""
        self.save.fill((0, 60, 0), (15, 60, 15), "minecraft:packed_ice")
        for how, probe in self._readings(60):
            with self.subTest(read=how):
                self.assertEqual((probe.y, probe.ice, probe.bed_y), (60, 0, 60))
                self.assertFalse(probe.wet)


class WadingTests(unittest.TestCase):
    """What a probe steps through on its way down to the bed, and what it
    stands on instead -- each read twice, from the blocks after a rebuild
    and from maps packed as the game writes them, since a rule the two
    paths disagree on is no rule."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="survey-wading-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, [(0, 0)])
        self.save = MinecraftSave.load(self.dir)
        self.save.fill((0, 50, 0), (15, 55, 15), "minecraft:stone")

    def _readings(self, cell=(8, 8), **maps):
        self.save.rebuild_heightmaps((0, 0, 0), (15, 0, 15))
        yield "walked", walk_surface(self.save, *cell)
        _pack_maps(self.save, (0, 0), **maps)
        yield "mapped", walk_surface(self.save, *cell)

    def _layer(self, y0, y1, state):
        self.save.fill((0, y0, 0), (15, y1, 15), state)

    def test_lava_is_a_surface(self):
        """Lava stops every motion-blocking map for its fluid, as water
        does, but nobody wades through it: it is what the probe reports."""
        self._layer(56, 58, "minecraft:lava[level=0]")
        for how, probe in self._readings(WORLD_SURFACE=58, MOTION_BLOCKING=58,
                                         MOTION_BLOCKING_NO_LEAVES=58,
                                         OCEAN_FLOOR=55):
            with self.subTest(read=how):
                self.assertEqual((probe.y, probe.block.name), (58, "minecraft:lava"))
                self.assertEqual((probe.water, probe.bed_y, probe.waterline),
                                 (0, 58, 58))
                self.assertFalse(probe.wet)

    def test_a_waterlogged_stair_is_stood_on(self):
        """A stair the water stands in is still a stair: the no-leaves tag
        names it, so it stops a fall, and its water is not a lake to wade."""
        self._layer(56, 57, "minecraft:water[level=0]")
        self._layer(58, 58, "minecraft:oak_stairs[waterlogged=true]")
        for how, probe in self._readings(WORLD_SURFACE=58, MOTION_BLOCKING=58,
                                         MOTION_BLOCKING_NO_LEAVES=58,
                                         OCEAN_FLOOR=58):
            with self.subTest(read=how):
                self.assertEqual((probe.y, probe.block.name),
                                 (58, "minecraft:oak_stairs"))
                self.assertEqual((probe.water, probe.bed_y, probe.waterline),
                                 (0, 58, 58))
                self.assertFalse(probe.wet)

    def test_waterlogged_leaves_are_the_swamps_water(self):
        """A mangrove's leaves dip into the swamp, waterlogged, and the
        game's MOTION_BLOCKING_NO_LEAVES stops at them for their water. They
        are canopy all the same, and canopy is not ground: the probe wades
        through them to the bed, and the swamp reads wet."""
        self._layer(56, 57, "minecraft:water[level=0]")
        self._layer(58, 58, "minecraft:mangrove_leaves[waterlogged=true]")
        for how, probe in self._readings(WORLD_SURFACE=58, MOTION_BLOCKING=58,
                                         MOTION_BLOCKING_NO_LEAVES=58,
                                         OCEAN_FLOOR=58):
            with self.subTest(read=how):
                self.assertEqual((probe.y, probe.block.name), (55, "minecraft:stone"))
                self.assertEqual((probe.water, probe.waterline), (3, 58))
                self.assertTrue(probe.wet)

    def test_only_the_cells_that_hold_water_are_counted(self):
        """``water`` counts water, not the distance down to the bed: a
        pocket of air in a lake is not water. The waterline is still where
        the fall stops, so it stands higher over the bed than the count."""
        self._layer(56, 58, "minecraft:water[level=0]")
        self._layer(61, 62, "minecraft:water[level=0]")      # air at 59..60
        for how, probe in self._readings(WORLD_SURFACE=62, MOTION_BLOCKING=62,
                                         MOTION_BLOCKING_NO_LEAVES=62,
                                         OCEAN_FLOOR=55):
            with self.subTest(read=how):
                self.assertEqual((probe.y, probe.water, probe.waterline), (55, 5, 62))
                self.assertIn("5 deep", repr(probe))

    def test_a_sign_over_a_drop_holds_one_cell_of_water(self):
        """A waterlogged sign on a facade stops the maps for its water, and
        the column under it is a drop to the ground. One cell of water, not
        the height of the drop."""
        self._layer(56, 63, "minecraft:stone")
        self.save.set_block(8, 70, 8, "minecraft:oak_wall_sign[waterlogged=true]")
        for how, probe in self._readings(WORLD_SURFACE=70, MOTION_BLOCKING=70,
                                         MOTION_BLOCKING_NO_LEAVES=70,
                                         OCEAN_FLOOR=63):
            with self.subTest(read=how):
                self.assertEqual((probe.y, probe.block.name), (63, "minecraft:stone"))
                self.assertEqual((probe.water, probe.waterline), (1, 70))
                self.assertEqual((probe.top_y, probe.top.name),
                                 (70, "minecraft:oak_wall_sign"))
                self.assertFalse(probe.lid)

    def test_snow_under_hanging_water_is_reported(self):
        """Water hanging over air, on a snowy bed: the snow is on what you
        would stand on, so it is read above the bed, not above the water."""
        self._layer(56, 56, "minecraft:snow[layers=2]")
        self._layer(62, 62, "minecraft:water[level=0]")
        for how, probe in self._readings(WORLD_SURFACE=62, MOTION_BLOCKING=62,
                                         MOTION_BLOCKING_NO_LEAVES=62,
                                         OCEAN_FLOOR=55):
            with self.subTest(read=how):
                self.assertEqual((probe.y, probe.snow, probe.water), (55, 2, 1))
                self.assertEqual(probe.waterline, 62)


class AbsentSurfaceTests(unittest.TestCase):
    """The "nothing to report" path, which must never invent terrain."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="survey-absent-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.save = MinecraftSave.load(make_world(self.dir))

    def test_a_partly_generated_chunk_is_still_probed_while_it_has_maps(self):
        chunk_x, chunk_z = PARTIAL_CHUNK
        dim = self.save.dimension(OVERWORLD)
        self.assertEqual(dim.chunk_status(chunk_x, chunk_z), "minecraft:carvers")
        probe = walk_surface(self.save, chunk_x * 16 + 4, chunk_z * 16 + 4)
        self.assertIsNotNone(probe, "the fixture's carved chunks carry maps")

    def test_a_partly_generated_chunk_reports_no_surface(self):
        """Every chunk in this fixture happens to carry heightmaps, even the
        partly generated ones, so the real state of a chunk stopped before
        the surface stage is made the way test_world makes it: by removing
        them, before anything has read the chunk and cached its maps.
        Scanning for a surface that is not there would be ninety times
        slower and would report terrain that does not exist."""
        chunk_x, chunk_z = PARTIAL_CHUNK
        dim = self.save.dimension(OVERWORLD)
        x, z = chunk_x * 16 + 4, chunk_z * 16 + 4
        del dim.chunk(chunk_x, chunk_z).data["Heightmaps"]
        self.assertIsNone(walk_surface(self.save, x, z))
        found = survey(self.save, (x, z), (x + 2, z + 2))
        self.assertEqual(len(found), 0)
        self.assertEqual(len(found.probes), 9,
                         "a hole is kept as a hole, not a smaller rectangle")
        self.assertEqual(set(found.probes.values()), {None})

    def test_a_rebuilt_partial_chunk_is_reported_partial(self):
        """An earlier ``rebuild_heightmaps`` wrote WORLD_SURFACE alone onto
        a chunk of any status, and saves still hold those chunks: a partly
        generated one it touched has a map again -- one that counts whatever
        the generator left lying there. Only a finished chunk's blocks are
        ground, so the map is not read as one. Today's rebuild leaves such a
        chunk as it stands, and says so."""
        chunk_x, chunk_z = PARTIAL_CHUNK
        x, z = chunk_x * 16 + 4, chunk_z * 16 + 4
        self.assertEqual(self.save.rebuild_heightmaps(
            (x, 0, z), (x, 0, z)).unfinished_chunks, [PARTIAL_CHUNK])
        maps = self.save.dimension(OVERWORLD).chunk(*PARTIAL_CHUNK).data["Heightmaps"]
        for name in ("OCEAN_FLOOR", "MOTION_BLOCKING_NO_LEAVES", "MOTION_BLOCKING"):
            del maps[name]
        self.assertIsNotNone(self.save.surface_y(x, z, "WORLD_SURFACE"))
        self.assertIsNone(walk_surface(self.save, x, z))
        found = survey(self.save, (x, z), (x + 2, z + 2))
        self.assertEqual(set(found.missing.values()), {"partial"})
        self.assertEqual(len(found.missing), 9)
        self.assertIn("9 partly generated", repr(found))

    def test_a_hole_draws_as_question_marks(self):
        found = survey(self.save, (100_000, 100_000), (100_003, 100_003))
        self.assertEqual(len(found), 0)
        self.assertEqual(found.counts, {})
        self.assertIn("no surface", repr(found))
        for line in found.text().splitlines()[2:]:
            self.assertEqual(line.split()[-1], "????")


class SurveyMapTests(unittest.TestCase):
    """The rectangle and the character maps, over the fixture's village."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="survey-map-")
        cls.save = MinecraftSave.load(make_world(cls.dir))
        cls.map = survey(
            cls.save, (VILLAGE_X0, VILLAGE_Z0),
            (VILLAGE_X0 + 15, VILLAGE_Z0 + 15),
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_the_village_reads_as_a_village(self):
        self.assertEqual(len(self.map), 256, "the whole chunk is generated")
        self.assertEqual(sum(self.map.counts.values()), len(self.map))
        for kind in ("path", "wood", "tree", "ground"):
            with self.subTest(kind=kind):
                self.assertGreater(self.map.counts.get(kind, 0), 0)

    def test_cells_selects_one_kind_in_reading_order(self):
        paths = self.map.cells("path")
        self.assertEqual(len(paths), self.map.counts["path"])
        for x, z in paths:
            self.assertEqual(self.map.probe(x, z).kind, "path")
        self.assertEqual(paths, sorted(paths, key=lambda c: (c[1], c[0])),
                         "z-major, the order the map is printed in")
        self.assertEqual(self.map.cells("no_such_kind"), [])

    def test_probe_outside_the_rectangle_is_simply_absent(self):
        self.assertIsNone(self.map.probe(VILLAGE_X0 - 100, VILLAGE_Z0))

    def test_heights_are_every_standing_height(self):
        heights = self.map.heights()
        self.assertEqual(len(heights), len(self.map))
        self.assertTrue(all(isinstance(y, int) for y in heights))

    def test_corners_may_be_given_either_way_and_with_a_y(self):
        """A survey reads the ground wherever it is, so y is ignored."""
        flipped = survey(
            self.save, (VILLAGE_X0 + 15, 200, VILLAGE_Z0 + 15),
            (VILLAGE_X0, -50, VILLAGE_Z0),
        )
        self.assertEqual(flipped.counts, self.map.counts)
        self.assertEqual(flipped.text(), self.map.text())

    def test_step_samples_a_coarser_grid(self):
        coarse = survey(
            self.save, (VILLAGE_X0, VILLAGE_Z0),
            (VILLAGE_X0 + 15, VILLAGE_Z0 + 15), step=4,
        )
        self.assertEqual(coarse.step, 4)
        self.assertEqual(list(coarse.xs), [VILLAGE_X0 + i for i in (0, 4, 8, 12)])
        self.assertEqual(len(coarse.probes), 16, "a sixteenth of the lookups")
        self.assertIn("every 4", repr(coarse))
        for (x, z), probe in coarse.probes.items():
            self.assertEqual(probe.kind, self.map.probe(x, z).kind)

    def test_step_below_one_is_refused(self):
        with self.assertRaises(ValueError):
            survey(self.save, (0, 0), (4, 4), step=0)

    def _shape(self, mode, **kwargs):
        text = self.map.text(mode, **kwargs)
        return text.splitlines()

    def test_kind_mode_is_one_character_a_column(self):
        lines = self._shape("kind")
        self.assertEqual(len(lines), 2 + 16, "two ruler lines, then the rows")
        label = len(str(VILLAGE_Z0))
        for line in lines:
            self.assertEqual(len(line), label + 1 + 16)
        self.assertTrue(lines[2].startswith(str(VILLAGE_Z0)))
        body = "".join(line[label + 1:] for line in lines[2:])
        self.assertLessEqual(set(body), set(KIND_GLYPHS.values()))

    def test_height_mode_is_two_digits_and_names_its_datum(self):
        lines = self._shape("height")
        label = len(str(VILLAGE_Z0))
        self.assertEqual(len(lines), 2 + 16 + 1, "the datum line is the extra")
        for line in lines[:-1]:
            self.assertEqual(len(line), label + 1 + 32)
        base = min(self.map.heights())
        self.assertIn(f"heights above y={base}", lines[-1])
        # An explicit datum shifts every reading by the same amount.
        shifted = self.map.text("height", base=base - 1).splitlines()
        self.assertIn(f"heights above y={base - 1}", shifted[-1])

    def test_snow_and_water_modes_draw_dots_where_there_is_none(self):
        for mode in ("snow", "water"):
            with self.subTest(mode=mode):
                lines = self._shape(mode)
                self.assertEqual(len(lines), 2 + 16)
                label = len(str(VILLAGE_Z0))
                body = "".join(line[label + 1:] for line in lines[2:])
                self.assertEqual(set(body), {"."},
                                 "the fixture village is dry and unsnowed")

    def test_legend_explains_the_glyphs_actually_used(self):
        lines = self._shape("kind", legend=True)
        self.assertEqual(len(lines), 2 + 16 + 1)
        for kind in self.map.counts:
            self.assertIn(f"{KIND_GLYPHS[kind]}={kind}", lines[-1])

    def test_an_unknown_mode_is_refused_by_name(self):
        with self.assertRaises(ValueError) as ctx:
            self.map.text(mode="elevation")
        self.assertIn("elevation", str(ctx.exception))
        self.assertIn("height", str(ctx.exception), "the refusal lists the modes")

    def test_the_map_types_are_the_documented_ones(self):
        self.assertIsInstance(self.map, SurveyMap)
        self.assertIsInstance(self.map.probe(VILLAGE_X0, VILLAGE_Z0), SurfaceProbe)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)


class StepKindTests(unittest.TestCase):
    """The classifier, and the bug that makes a walk check worthless."""

    def test_a_bare_substring_would_delete_the_floor(self):
        # `grass_block` starts with "grass" and every `light_blue_*` block
        # starts with "light". A substring test called all of them air, so
        # the checker deleted the floor -- and a walk check that deletes
        # the floor reports every room as reachable whatever is in it.
        for name in ("minecraft:grass_block", "minecraft:light_blue_wool",
                     "minecraft:light_blue_concrete", "minecraft:light_gray_wool",
                     "minecraft:lightning_rod_block", "minecraft:bushy_thing",
                     # A rod you bump into: the game gives it collision.
                     "minecraft:lightning_rod"):
            with self.subTest(name=name):
                self.assertEqual(step_kind(name), "full")
        # ...while the blocks those names were shadowing stay passable.
        for name in ("minecraft:short_grass", "minecraft:light",
                     "minecraft:bush", "minecraft:torch"):
            with self.subTest(name=name):
                self.assertEqual(step_kind(name), "air")

    def test_anything_unrecognized_is_solid(self):
        # A wrong `full` raises a false alarm somebody reads; a wrong `air`
        # hides a wall nobody sees.
        self.assertEqual(step_kind("minecraft:some_block_from_next_year"),
                         "full")

    def test_half_blocks_are_the_ones_you_climb_without_jumping(self):
        cases = [
            ("minecraft:oak_slab", {"type": "bottom"}, "half"),
            ("minecraft:oak_slab", {"type": "top"}, "full"),
            ("minecraft:oak_slab", {"type": "double"}, "full"),
            ("minecraft:oak_stairs", {"half": "bottom"}, "half"),
            ("minecraft:oak_stairs", {"half": "top"}, "full"),
            ("minecraft:oak_trapdoor", {"open": "false"}, "half"),
            ("minecraft:oak_trapdoor", {"open": "false", "half": "top"}, "full"),
            ("minecraft:oak_trapdoor", {"open": "true"}, "full"),
            ("minecraft:oak_door", {}, "air"),
            ("minecraft:oak_fence_gate", {}, "air"),
            ("minecraft:oak_fence", {}, "tall"),
            ("minecraft:cobblestone_wall", {}, "tall"),
            ("minecraft:glass_pane", {}, "full"),
            ("minecraft:lantern", {}, "half"),
            ("minecraft:lantern", {"hanging": "true"}, "full"),
            ("minecraft:mangrove_roots", {}, "full"),
            ("minecraft:potted_oak_sapling", {}, "half"),
        ]
        for name, props, want in cases:
            with self.subTest(name=name, props=props):
                self.assertEqual(step_kind(name, props), want)


class ReachTests(unittest.TestCase):
    """Walking a build, forwards and -- separately -- back again."""

    CHUNKS = [(x, z) for x in range(0, 2) for z in range(0, 2)]
    BOX = ((0, 60, 0), (15, 80, 15))

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="reach-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, self.CHUNKS)
        self.save = MinecraftSave.load(self.dir)
        for x in range(0, 16):
            for z in range(0, 16):
                self.save.set_block(x, 63, z, "minecraft:stone")

    def walk(self, **kwargs):
        return reachable(self.save, (0, 63, 0), *self.BOX, **kwargs)

    def test_a_flat_floor_is_walked_whole_and_is_a_round_trip(self):
        walk = self.walk()
        self.assertEqual(len(walk.footing(63)), 16 * 16)
        self.assertFalse(walk.jumped)
        self.assertFalse(walk.stranded)

    def test_a_half_block_is_a_step_and_a_full_block_is_a_jump(self):
        self.save.set_block(8, 64, 4, "minecraft:stone_brick_slab[type=bottom]")
        self.save.set_block(8, 64, 8, "minecraft:stone")
        walk = self.walk()
        self.assertIn((8, 64, 4), walk.walked, "a slab is inside the auto-step")
        self.assertIn((8, 64, 8), walk.jumped, "a whole block is not")
        strict = self.walk(allow_jump=False)
        self.assertIn((8, 64, 4), strict.reached)
        self.assertNotIn((8, 64, 8), strict.reached)

    def test_a_stack_seals_the_cells_behind_it_and_no_plan_shows_it(self):
        # A bookshelf run one cell off the wall: the room is drawn
        # finished, and twelve floor cells are behind it.
        for z in range(0, 16):
            for y in (64, 65):
                self.save.set_block(13, y, z, "minecraft:bookshelf")
        sealed = [(x, z) for x in range(14, 16) for z in range(0, 16)]
        walk = self.walk()
        self.assertEqual(len(walk.missing(sealed, 63)), len(sealed))
        self.assertIn("#", walk.text(63, StepSpace(self.save)),
                      "ground nobody can reach reads as marooned, not void")

    def test_getting_there_and_getting_home_are_two_questions(self):
        # A pit you drop into and cannot climb out of. A forward-only
        # search calls it reached and says nothing more.
        for x in range(4, 7):
            for z in range(4, 7):
                self.save.set_block(x, 63, z, "minecraft:air")
                self.save.set_block(x, 62, z, "minecraft:air")
                self.save.set_block(x, 61, z, "minecraft:stone")
        walk = self.walk()
        self.assertIn((5, 61, 5), walk.reached, "you can certainly fall in")
        self.assertIn((5, 61, 5), walk.stranded, "and not get out again")
        self.assertTrue(all(y == 61 for _, y, _ in walk.stranded))

    def test_an_overlay_walks_a_build_that_has_not_happened(self):
        plan = {(x, 64, 6): "minecraft:stone" for x in range(0, 16)}
        before = self.walk()
        after = reachable(self.save, (0, 63, 0), *self.BOX, overlay=plan)
        self.assertFalse(before.jumped)
        self.assertTrue(after.jumped, "the wall must now be climbed")
        self.assertNotIn((8, 63, 10), reachable(
            self.save, (0, 63, 0), *self.BOX, overlay=plan,
            allow_jump=False).reached)

    def test_a_fence_is_not_a_step(self):
        # A pen. Fences are a block and a half high; the model used to call
        # them whole blocks, and a whole block is a jump, so every pen and
        # walled garden was a door.
        for x in range(4, 9):
            for z in range(4, 9):
                if x in (4, 8) or z in (4, 8):
                    self.save.set_block(x, 64, z, "minecraft:oak_fence")
        walk = self.walk()
        self.assertNotIn((6, 63, 6), walk.reached)
        self.assertFalse(any(y == 64 for _, y, _ in walk.reached),
                         "nobody stands on a fence")

    def test_a_torch_on_the_floor_is_not_a_wall(self):
        # A corridor one block wide, open at its west end, with a torch
        # standing in it. Read as a whole block, the torch was a jump --
        # so the far end was reached, and only by climbing over a torch.
        for x in range(1, 16):
            for z in (1, 3):
                for y in (64, 65):
                    self.save.set_block(x, y, z, "minecraft:stone")
        self.save.set_block(5, 64, 2, "minecraft:torch")
        walk = self.walk()
        self.assertIn((15, 63, 2), walk.walked)

    def wall_with_iron_door(self, wall="minecraft:stone"):
        """A wall three high across z=8 with an iron door at x=8, which the
        walk from (0, 63, 0) meets from its north side."""
        for x in range(0, 16):
            for y in (64, 65, 66):
                self.save.set_block(x, y, 8, wall)
        self.save.set_block(8, 64, 8, "minecraft:iron_door[facing=south,half=lower,hinge=left,open=false,powered=false]")
        self.save.set_block(8, 65, 8, "minecraft:iron_door[facing=south,half=upper,hinge=left,open=false,powered=false]")

    def button(self, x, y, z, facing):
        self.save.set_block(x, y, z, f"minecraft:stone_button[face=wall,facing={facing},powered=false]")

    def test_an_iron_door_nothing_opens_is_a_wall(self):
        # step_kind calls every door air, as it should: its shape is a
        # panel. Whether you get through an iron one is a question of power.
        self.wall_with_iron_door()
        self.assertNotIn((8, 63, 12), self.walk().reached)

    def test_a_button_on_one_side_is_a_door_you_cannot_come_back_through(self):
        self.wall_with_iron_door()
        self.button(7, 65, 7, "north")
        walk = self.walk()
        self.assertIn((8, 63, 12), walk.reached)
        self.assertIn((8, 63, 12), walk.stranded)

    def test_buttons_on_both_sides_make_the_round_trip(self):
        self.wall_with_iron_door()
        self.button(7, 65, 7, "north")
        self.button(9, 65, 9, "south")
        walk = self.walk()
        self.assertIn((8, 63, 12), walk.reached)
        self.assertFalse(walk.stranded)

    def test_a_button_on_glass_opens_nothing(self):
        # Glass holds a button; it does not conduct, so the door stays shut.
        self.wall_with_iron_door(wall="minecraft:glass")
        self.button(7, 65, 7, "north")
        self.button(9, 65, 9, "south")
        self.assertNotIn((8, 63, 12), self.walk().reached)

    def test_the_doorstep_has_to_be_a_block_you_stand_on(self):
        with self.assertRaises(NBTError) as caught:
            reachable(self.save, (0, 70, 0), *self.BOX)
        self.assertIn("nothing to stand on", str(caught.exception))
        with self.assertRaises(NBTError) as caught:
            reachable(self.save, (99, 63, 99), *self.BOX)
        self.assertIn("outside the box", str(caught.exception))


def game_kind(shape) -> str:
    """How the walk model should read a collision shape from the game.

    Nothing standing in the middle of the cell is air. A top no higher
    than 3/16 is a thin floor: standing on it you still fit in two cells,
    so it reads as air too. Up to the 0.6 the game steps up unasked is a
    half block, up to a whole block is full, and anything higher -- a fence
    or a wall, a block and a half -- is tall.
    """
    if shape.top < 0 or not shape.body:
        return "air"
    if shape.top <= 3:
        return "thin"
    if shape.top <= 9:
        return "half"
    if shape.top <= 16:
        return "full"
    return "tall"


#: Heavier is safer: a wrong ``full`` raises a false alarm somebody reads;
#: a wrong ``air`` hides a wall nobody sees.
_RANK = {"air": 0, "thin": 0, "half": 1, "full": 2, "tall": 3}

#: Where step_kind lets a body through and the game has a shape: each must
#: be a choice, with its reason, because the other name for this is a wall
#: nobody sees.
LIGHTER = [
    ("a gate is opened, like a door",
     lambda name, props: name.endswith("_fence_gate")),
    ("a stair is climbed from its low side, half a block at a time",
     lambda name, props: name.endswith("_stairs") and props["half"] == "bottom"),
    ("natural snow is one layer, which has no shape; deeper is laid by "
     "hand, and walk_surface reports snow rather than folding it into the "
     "ground", lambda name, props: name == "snow"),
    ("you climb scaffolding from inside it: its shape depends on where you "
     "stand", lambda name, props: name == "scaffolding"),
    ("a wall hanging sign's bar is the top of its cell, over the head of "
     "anyone walking under it",
     lambda name, props: name.endswith("_wall_hanging_sign")),
    ("what a piston head pushing down reaches past its cell is inside the "
     "piston, which is solid anyway",
     lambda name, props: name == "piston_head" and props["facing"] == "down"),
]

#: Where step_kind calls a block what the game lets a body into: false
#: alarms by design, each with its reason.
HEAVIER = [
    ("an open trapdoor is a panel on one face of its cell, which a model "
     "of whole cells cannot place, so it reads as a wall rather than a gap",
     lambda name, props: name.endswith("_trapdoor") and props["open"] == "true"),
    ("a shut trapdoor is a floor, even over a hole",
     lambda name, props: name.endswith("_trapdoor") and props["half"] == "bottom"),
    ("fire and lava burn, and a walk check should not walk into them",
     lambda name, props: name in ("fire", "soul_fire", "lava")),
    ("a campfire burns whoever stands on it",
     lambda name, props: name in ("campfire", "soul_campfire")),
    ("a portal takes you somewhere else",
     lambda name, props: name in ("nether_portal", "end_portal", "end_gateway")),
    ("a moving piston is a block in the middle of being pushed",
     lambda name, props: name == "moving_piston"),
    ("the last slice of a cake is too narrow to stop a body",
     lambda name, props: name == "cake"),
    ("a young pitcher crop is under a step, and a grown one is not",
     lambda name, props: name == "pitcher_crop"),
    ("a small amethyst bud reads as the bigger ones do",
     lambda name, props: name == "small_amethyst_bud"),
    ("a wall with no post and no sides is a state the game never makes",
     lambda name, props: name.endswith("_wall") and props["up"] == "false"
     and all(props[side] == "none" for side in ("north", "south", "east", "west"))),
]


class StepKindAgainstTheGameTests(unittest.TestCase):
    """Every block state there is, against the game's own collision shapes.

    ``fixtures/block_shapes.json`` is the game's answer, state by state
    (``tools/probe_block_shapes.py`` asks it). Where step_kind disagrees,
    the disagreement has to be one of the choices listed above, and every
    listed choice has to still be one -- so a game update that adds a
    block, or a table edit that changes a verdict, fails here by name.
    """

    def test_every_state_reads_as_the_game_shapes_it_or_says_why_not(self):
        used: set[str] = set()
        unexplained: list[str] = []
        states = 0
        for name, props, shape in block_shapes():
            states += 1
            mine, theirs = step_kind(name, props), game_kind(shape)
            if _RANK[mine] == _RANK[theirs]:
                continue
            table = LIGHTER if _RANK[mine] < _RANK[theirs] else HEAVIER
            bare = name.removeprefix("minecraft:")
            why = next((reason for reason, applies in table
                        if applies(bare, props)), None)
            if why is None:
                state = ",".join(f"{k}={v}" for k, v in sorted(props.items()))
                unexplained.append(
                    f"{name}[{state}]: step_kind says {mine}, the game's "
                    f"shape is {theirs} (top {shape.top}/16)")
            else:
                used.add(why)
        self.assertGreater(states, 35000)
        self.assertEqual(unexplained[:10], [],
                         f"{len(unexplained)} states disagree with the game")
        stale = [reason for reason, _ in LIGHTER + HEAVIER if reason not in used]
        self.assertEqual(stale, [], "a choice that excuses nothing is stale")



class UnsupportedTests(unittest.TestCase):
    """What the game will not keep -- each found the way the game finds it."""

    def setUp(self):
        from heaton_voxel.craft.survey import unsupported

        self.unsupported = unsupported
        self.dir = tempfile.mkdtemp(prefix="unsupported-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, [(0, 0)])
        self.save = MinecraftSave.load(self.dir)
        self.save.fill((0, 60, 0), (15, 63, 15), "minecraft:stone")

    def put(self, x, y, z, block):
        self.save.set_block(x, y, z, block)

    def kinds(self, **kwargs):
        return {(f.cell, f.kind) for f in
                self.unsupported(self.save, (0, 60, 0), (15, 70, 15), **kwargs)}

    def test_a_well_built_corner_reports_nothing(self):
        self.put(2, 64, 2, "minecraft:red_bed[facing=east,occupied=false,part=foot]")
        self.put(3, 64, 2, "minecraft:red_bed[facing=east,occupied=false,part=head]")
        self.put(5, 64, 5, "minecraft:torch")
        self.put(6, 64, 6, "minecraft:stone")
        self.put(6, 64, 7, "minecraft:wall_torch[facing=south]")
        self.put(8, 63, 8, "minecraft:grass_block[snowy=false]")
        self.put(8, 64, 8, "minecraft:poppy")
        self.assertEqual(self.kinds(), set())

    def test_a_bed_missing_its_head_breaks(self):
        # The Roman town's insulae: a bed placed, then an amphora on its head.
        self.put(2, 64, 2, "minecraft:red_bed[facing=east,occupied=false,part=foot]")
        self.put(3, 64, 2, "minecraft:decorated_pot")
        self.assertIn(((2, 64, 2), "lone half"), self.kinds())

    def test_a_torch_on_leaves_and_a_button_on_a_fence_fall_off(self):
        self.put(4, 64, 4, "minecraft:oak_leaves[distance=1,persistent=true,waterlogged=false]")
        self.put(4, 65, 4, "minecraft:torch")
        self.put(6, 64, 6, "minecraft:oak_fence")
        self.put(6, 64, 7, "minecraft:stone_button[face=wall,facing=south,powered=false]")
        found = self.kinds()
        self.assertIn(((4, 65, 4), "no footing"), found)
        self.assertIn(((6, 64, 7), "no footing"), found)

    def test_sand_over_a_hole_and_a_flower_on_stone(self):
        self.put(9, 66, 9, "minecraft:sand")
        self.put(11, 64, 11, "minecraft:poppy")
        found = self.kinds()
        self.assertIn(((9, 66, 9), "falls"), found)
        self.assertIn(((11, 64, 11), "off its soil"), found)

    def test_sand_falls_through_what_the_game_replaces(self):
        """The game drops a falling block through what
        ``FallingBlock.isFree`` answers yes for -- an air, fire, a liquid,
        or a block its properties make replaceable, the ``replaceable``
        tag -- so sand on hanging roots under a cave's crust, on leaf
        litter, on a light block or on nether roots falls; sand on kelp,
        which is neither a liquid nor replaceable, stays. The list is that
        tag's thirty blocks, every one a block the game has."""
        from heaton_voxel.craft.block_registry import properties_of
        from heaton_voxel.craft.survey import _FALLS_THROUGH

        under = {3: "minecraft:hanging_roots[waterlogged=false]",
                 5: "minecraft:leaf_litter[facing=north,segment_amount=1]",
                 7: "minecraft:light[level=15,waterlogged=false]",
                 9: "minecraft:crimson_roots",
                 11: "minecraft:structure_void",
                 13: "minecraft:kelp_plant"}
        for x, block in under.items():
            self.put(x, 65, 3, block)
            self.put(x, 66, 3, "minecraft:sand")
        found = {cell for cell, kind in self.kinds() if kind == "falls"}
        self.assertEqual(found, {(x, 66, 3) for x in under if x != 13})
        self.assertEqual(len(_FALLS_THROUGH), 30)
        for name in sorted(_FALLS_THROUGH):
            self.assertIsNotNone(properties_of(f"minecraft:{name}"), name)
        self.assertNotIn("kelp", _FALLS_THROUGH)
        self.assertNotIn("kelp_plant", _FALLS_THROUGH)

    def test_sand_falls_through_what_the_game_reads_as_air_or_grass(self):
        """The audit reads the block under a falling one as the game reads
        it, as the ground writers' void-under test does -- one predicate,
        ``survey._falls_into``: an id the game does not have is the air it
        loads it as, so sand on a modded block that is not there falls, and
        an old name is the block it became, so sand on a pre-1.20.3
        ``grass`` falls as it does on ``short_grass``."""
        under = {3: "minecraft:modded_thing_xyz", 5: "minecraft:grass",
                 7: "minecraft:short_grass", 9: "minecraft:stone"}
        for x, block in under.items():
            self.put(x, 65, 3, block)
            self.put(x, 66, 3, "minecraft:sand")
        found = {cell for cell, kind in self.kinds() if kind == "falls"}
        self.assertEqual(found, {(3, 66, 3), (5, 66, 3), (7, 66, 3)})

        from heaton_voxel.craft.build._common import _falls_through
        from heaton_voxel.craft.survey import _falls_into

        for x, block in under.items():
            state = BlockState.parse(block)
            self.assertEqual(_falls_into(state), x != 9, block)
            self.assertEqual(_falls_through(state), x != 9, block)
        self.assertTrue(_falls_through(None), "past the world's floor")

    def test_a_potted_plant_keeps_to_its_pot(self):
        """A flower pot asks nothing of what it stands on, so a potted
        sapling or tulip on planks is kept -- 31 of them were reported off
        their soil in JeffWorld -- while the bare sapling beside it is
        not."""
        self.put(3, 63, 3, "minecraft:oak_planks")
        self.put(3, 64, 3, "minecraft:potted_oak_sapling")
        self.put(4, 63, 3, "minecraft:oak_planks")
        self.put(4, 64, 3, "minecraft:potted_red_tulip")
        self.put(5, 63, 3, "minecraft:oak_planks")
        self.put(5, 64, 3, "minecraft:oak_sapling[stage=0]")
        found = self.kinds()
        self.assertNotIn(((3, 64, 3), "off its soil"), found)
        self.assertNotIn(((4, 64, 3), "off its soil"), found)
        self.assertIn(((5, 64, 3), "off its soil"), found)

    def test_sugar_cane_needs_water_beside_its_soil(self):
        self.put(5, 63, 5, "minecraft:sand")
        self.put(5, 64, 5, "minecraft:sugar_cane[age=0]")
        self.assertIn(((5, 64, 5), "dry cane"), self.kinds())
        self.put(6, 63, 5, "minecraft:water[level=0]")
        self.assertNotIn(((5, 64, 5), "dry cane"), self.kinds())

    def test_sugar_cane_is_watered_by_whatever_holds_water(self):
        """``SugarCaneBlock.canSurvive`` asks the *fluid* beside the cane's
        block for ``#water`` -- a flow as much as a source -- and the block
        for frosted ice. Seagrass, kelp and a bubble column are all water
        to it; they were reported ``"dry cane"``, as only blocks named
        water or carrying ``waterlogged=true`` counted. A waterlogged chain
        under its retired name is the ``iron_chain`` the game's fixer makes
        of it out of an old chunk, and wet. Lava, plain ice and a dry stair
        or pickle water nothing."""
        wet = ("minecraft:seagrass", "minecraft:kelp_plant",
               "minecraft:kelp[age=3]", "minecraft:bubble_column[drag=true]",
               "minecraft:tall_seagrass[half=lower]",
               "minecraft:water[level=3]", "minecraft:water[level=9]",
               "minecraft:sea_pickle",
               "minecraft:oak_slab[type=bottom,waterlogged=true]",
               "minecraft:frosted_ice[age=0]",
               "minecraft:chain[axis=y,waterlogged=true]")
        dry = ("minecraft:lava[level=0]", "minecraft:ice",
               "minecraft:packed_ice",
               "minecraft:sea_pickle[pickles=1,waterlogged=false]",
               "minecraft:oak_stairs[facing=north,half=bottom,"
               "shape=straight,waterlogged=false]")
        cells = {}
        for i, block in enumerate(wet + dry):
            x, z = (i % 5) * 3, (i // 5) * 3 + 1
            self.put(x, 63, z, "minecraft:sand")
            self.put(x, 64, z, "minecraft:sugar_cane[age=0]")
            self.put(x + 1, 63, z, block)
            cells[block] = (x, 64, z)
        found = self.kinds()
        for block in wet:
            with self.subTest(beside=block):
                self.assertNotIn((cells[block], "dry cane"), found)
        for block in dry:
            with self.subTest(beside=block):
                self.assertIn((cells[block], "dry cane"), found)

    def test_sugar_cane_is_watered_from_any_side_of_its_block(self):
        """Water on any of the four sides of the block the cane stands in
        waters it -- here seagrass, which the game's ``#water`` counts --
        while water on a diagonal, under that block or beside the cane
        itself waters nothing."""
        watered = {(2, 63, 2): (2, 63, 1),       # north
                   (7, 63, 2): (7, 63, 3),       # south
                   (12, 63, 2): (13, 63, 2),     # east
                   (2, 63, 7): (1, 63, 7)}       # west
        dry = {(7, 63, 7): (8, 63, 8),           # diagonal
               (12, 63, 7): (12, 62, 7),         # under the sand
               (7, 63, 12): (8, 64, 12)}         # beside the cane
        for sand, water in {**watered, **dry}.items():
            x, y, z = sand
            self.put(x, y, z, "minecraft:sand")
            self.put(x, y + 1, z, "minecraft:sugar_cane[age=0]")
            self.put(*water, "minecraft:seagrass")
        found = {(cell, kind) for cell, kind in self.kinds()
                 if kind == "dry cane"}
        self.assertEqual(found, {((x, y + 1, z), "dry cane")
                                 for x, y, z in dry})

    def test_a_stack_of_cane_is_judged_at_its_foot(self):
        """Cane stands on cane, so a stack is judged by its lowest block
        alone: watered by seagrass on sand it reports nothing, on farmland
        it is off its soil at its foot only, and on dry sand it is dry at
        its foot only."""
        stacks = {(2, 63, 2): ("minecraft:sand", "minecraft:seagrass"),
                  (7, 63, 2): ("minecraft:farmland[moisture=7]",
                               "minecraft:water[level=0]"),
                  (12, 63, 2): ("minecraft:sand", None)}
        for (x, y, z), (ground, water) in stacks.items():
            self.put(x, y, z, ground)
            if water is not None:
                self.put(x + 1, y, z, water)
            for dy in (1, 2, 3):
                self.put(x, y + dy, z, "minecraft:sugar_cane[age=0]")
        found = {(cell, kind) for cell, kind in self.kinds()
                 if cell[1] > 63}
        self.assertEqual(found, {((7, 64, 2), "off its soil"),
                                 ((12, 64, 2), "dry cane")})

    def test_survey_reads_water_one_way(self):
        """``classify``, the probe's wading and the cane check all read
        water by :func:`holds_water`: a flow, a bubble column, seagrass
        and kelp are water to each of them, and lava and ice to none.
        ``classify`` names the plants by name before it asks."""
        from heaton_voxel.blocks import BlockState
        from heaton_voxel.craft.block_facts import holds_water
        from heaton_voxel.craft.survey import _wades

        states = {"minecraft:water[level=0]": True,
                  "minecraft:water[level=3]": True,
                  "minecraft:water[level=8]": True,
                  "minecraft:bubble_column[drag=false]": True,
                  "minecraft:seagrass": True,
                  "minecraft:kelp_plant": True,
                  "minecraft:lava[level=0]": False,
                  "minecraft:ice": False}
        cells = {}
        for i, block in enumerate(states):
            x, z = (i % 4) * 4, (i // 4) * 4 + 1
            self.put(x, 63, z, "minecraft:sand")
            self.put(x, 64, z, "minecraft:sugar_cane[age=0]")
            self.put(x + 1, 63, z, block)
            cells[block] = (x, 64, z)
        found = self.kinds()
        for block, wet in states.items():
            name, _, rest = block.partition("[")
            props = dict(pair.split("=") for pair in rest.rstrip("]").split(",")
                         if pair)
            with self.subTest(block=block):
                self.assertEqual(holds_water(name, props), wet)
                self.assertEqual(_wades(BlockState(name, props)), wet)
                if classify(name) != "plant":
                    self.assertEqual(classify(name) == "water", wet)
                self.assertEqual((cells[block], "dry cane") in found, not wet)

    def test_sugar_cane_on_farmland_is_off_its_soil(self):
        """``supports_sugar_cane`` is ``substrate_overworld`` and sand:
        the soil a flower grows on, but not farmland, which was taken for
        soil. Watered all the same, cane on farmland goes; on sand, red
        sand, mud or grass it stays."""
        footings = ("minecraft:farmland[moisture=7]", "minecraft:sand",
                    "minecraft:red_sand", "minecraft:mud",
                    "minecraft:grass_block[snowy=false]")
        for i, block in enumerate(footings):
            x = i * 3
            self.put(x, 63, 4, block)
            self.put(x + 1, 63, 4, "minecraft:water[level=0]")
            self.put(x, 64, 4, "minecraft:sugar_cane[age=0]")
        found = {(cell, kind) for cell, kind in self.kinds() if cell[1] == 64}
        self.assertEqual(found, {((0, 64, 4), "off its soil")})

    def test_a_dead_bush_on_glazed_terracotta_is_off_its_soil(self):
        """``DryVegetationBlock`` stands a dead bush on
        ``supports_dry_vegetation``: sand, the ``terracotta`` tag and the
        soil. The tag is plain and dyed terracotta, never glazed, which a
        test of the name's ending let in."""
        footings = ("minecraft:white_glazed_terracotta[facing=north]",
                    "minecraft:red_terracotta", "minecraft:terracotta",
                    "minecraft:red_sand", "minecraft:coarse_dirt",
                    "minecraft:farmland[moisture=0]")
        for i, block in enumerate(footings):
            self.put(i * 2, 63, 6, block)
            self.put(i * 2, 64, 6, "minecraft:dead_bush")
        found = {(cell, kind) for cell, kind in self.kinds() if cell[1] == 64}
        self.assertEqual(found, {((0, 64, 6), "off its soil")})

    def test_an_azalea_needs_its_soil_or_clay(self):
        """``AzaleaBlock.mayPlaceOn`` is ``supports_azalea``: the soil and
        clay. An azalea on stone or sand goes; on clay, grass, moss or
        farmland it stays. Azaleas were not looked at."""
        footings = (("minecraft:stone", "minecraft:flowering_azalea"),
                    ("minecraft:sand", "minecraft:azalea"),
                    ("minecraft:clay", "minecraft:flowering_azalea"),
                    ("minecraft:grass_block[snowy=false]", "minecraft:azalea"),
                    ("minecraft:moss_block", "minecraft:azalea"),
                    ("minecraft:farmland[moisture=0]", "minecraft:azalea"))
        for i, (ground, azalea) in enumerate(footings):
            self.put(i * 2, 63, 9, ground)
            self.put(i * 2, 64, 9, azalea)
        found = {(cell, kind) for cell, kind in self.kinds() if cell[1] == 64}
        self.assertEqual(found, {((0, 64, 9), "off its soil"),
                                 ((2, 64, 9), "off its soil")})

    def test_a_bamboo_shoot_is_not_held_to_a_saplings_soil(self):
        """A bamboo shoot is named like a sapling and is no
        ``VegetationBlock``: ``BambooSaplingBlock`` stands on
        ``supports_bamboo`` -- sand and gravel among it -- so one on sand
        was reported off its soil the game keeps it on. Survey copies no
        bamboo tag, so it leaves the shoot unjudged; a real sapling on sand
        is still off its soil."""
        self.put(2, 63, 12, "minecraft:sand")
        self.put(2, 64, 12, "minecraft:bamboo_sapling")
        self.put(4, 63, 12, "minecraft:sand")
        self.put(4, 64, 12, "minecraft:oak_sapling[stage=0]")
        found = {(cell, kind) for cell, kind in self.kinds() if cell[1] == 64}
        self.assertEqual(found, {((4, 64, 12), "off its soil")})

    def test_iron_doors_are_sealed_one_way_or_fine(self):
        for x in range(0, 16):
            for y in (64, 65, 66):
                self.put(x, y, 8, "minecraft:stone")
        self.put(8, 64, 8, "minecraft:iron_door[facing=south,half=lower,hinge=left,open=false,powered=false]")
        self.put(8, 65, 8, "minecraft:iron_door[facing=south,half=upper,hinge=left,open=false,powered=false]")
        self.assertIn(((8, 64, 8), "sealed door"), self.kinds())
        self.put(7, 65, 7, "minecraft:stone_button[face=wall,facing=north,powered=false]")
        self.assertIn(((8, 64, 8), "one-way door"), self.kinds())
        self.put(9, 65, 9, "minecraft:stone_button[face=wall,facing=south,powered=false]")
        self.assertEqual({k for c, k in self.kinds() if c == (8, 64, 8)}, set())

    def test_a_plan_is_audited_before_it_is_written(self):
        plan = {(4, 64, 4): "minecraft:torch", (4, 63, 4): "minecraft:air"}
        self.assertIn(((4, 64, 4), "no footing"), self.kinds(overlay=plan))
        self.assertEqual(self.save.get_block(4, 64, 4).name, "minecraft:air")

    def test_a_chest_under_a_conductor_will_not_open(self):
        """``ChestBlock.isBlockedChestByBlock``: the game will not open a
        chest whose block above conducts power -- ``isRedstoneConductor``,
        not opacity -- and the trapped and copper chests inherit it while
        the ender chest asks the same. Every chest the game has, under
        stone; then a chest under blocks that do not conduct, or that the
        game loads as air, and a barrel, which opens under anything."""
        from heaton_voxel.craft.block_registry import BLOCK_IDS

        chests = sorted(name for name in BLOCK_IDS
                        if name == "minecraft:chest" or name.endswith("_chest"))
        self.assertEqual(chests, [f"minecraft:{name}" for name in (
            "chest", "copper_chest", "ender_chest", "exposed_copper_chest",
            "oxidized_copper_chest", "trapped_chest", "waxed_copper_chest",
            "waxed_exposed_copper_chest", "waxed_oxidized_copper_chest",
            "waxed_weathered_copper_chest", "weathered_copper_chest")])
        for x, name in enumerate(chests):
            self.put(x, 64, 2, name)
            self.put(x, 65, 2, "minecraft:stone")
        self.assertEqual(
            {cell for cell, kind in self.kinds() if kind == "blocked chest"},
            {(x, 64, 2) for x in range(len(chests))})

        for x, above in enumerate((
                "minecraft:glass",
                "minecraft:cobblestone_slab[type=bottom,waterlogged=false]",
                "minecraft:white_carpet",
                "minecraft:oak_stairs[facing=north,half=bottom,shape=straight,"
                "waterlogged=false]",
                "modded:gravel")):
            self.put(x, 64, 6, "minecraft:chest")
            self.put(x, 65, 6, above)
        self.put(8, 64, 6, "minecraft:barrel[facing=up,open=false]")
        self.put(8, 65, 6, "minecraft:stone")
        self.assertEqual(
            {cell for cell, kind in self.kinds()
             if kind == "blocked chest" and cell[2] == 6}, set())
        # And a plan that would set a conductor over one is audited as one.
        self.assertIn(((0, 64, 6), "blocked chest"),
                      self.kinds(overlay={(0, 65, 6): "minecraft:gravel"}))

    def test_a_property_left_out_is_the_one_the_game_loads(self):
        """The game fills a property a state leaves out from its block's
        default state (``StateDefinition.appendPropertyCodec``), so a bare
        wall torch faces north and hangs on the block south of it, a bare
        button is on a wall facing north, and a bare iron door is a closed
        lower half facing north. Each is judged so, in the world and in a
        plan alike: once the torch and the door passed, and the button on
        its stone was a false alarm."""
        self.put(2, 64, 2, "minecraft:wall_torch")            # on nothing
        self.put(4, 64, 3, "minecraft:stone")
        self.put(4, 64, 2, "minecraft:wall_torch")            # on the stone
        self.put(5, 64, 3, "minecraft:stone")
        self.put(5, 64, 2, "minecraft:stone_button")          # on the stone
        self.put(8, 64, 8, "minecraft:iron_door")             # nothing opens it
        self.put(8, 65, 8, "minecraft:iron_door[half=upper]")
        self.assertEqual(self.kinds(), {((2, 64, 2), "no footing"),
                                        ((8, 64, 8), "sealed door")})
        self.assertIn(((10, 64, 10), "no footing"), self.kinds(
            overlay={(10, 64, 10): "minecraft:wall_torch"}))


#: Each of survey's footing tables, and the tag of the game's it copies.
FOOTING_TAGS = {
    "_SOIL": "supports_vegetation",
    "_SAND": "sand",
    "_TERRACOTTA": "terracotta",
    "_AZALEA_SOIL": "supports_azalea",
    "_DRY_SOIL": "supports_dry_vegetation",
    "_CANE_SOIL": "supports_sugar_cane",
    "_CANE_WATER": "supports_sugar_cane_adjacently",
}
#: Where a table departs from its tag, by table, each block with its
#: reason. There are none: a departure would be named here, and fail as
#: stale once the tag holds it -- or leaves it out -- after all.
FOOTING_ADDED: dict[str, dict[str, str]] = {}
FOOTING_LEFT: dict[str, dict[str, str]] = {}

#: What each thing that grows is held to, by name, besides the plants on
#: the soil below: the block class's own tag (javap, 26.3).
FOOTING_OF = {
    "azalea": "_AZALEA_SOIL",              # AzaleaBlock.mayPlaceOn
    "flowering_azalea": "_AZALEA_SOIL",
    "dead_bush": "_DRY_SOIL",              # DryVegetationBlock.mayPlaceOn
    "sugar_cane": "_CANE_SOIL",            # SugarCaneBlock.canSurvive
}
#: Every block judged on the soil (``supports_vegetation``): each one a
#: ``VegetationBlock`` that keeps its ``mayPlaceOn`` (javap, 26.3). A block
#: an update adds, or renames, fails here by name.
ON_THE_SOIL = sorted("""
acacia_sapling allium azure_bluet birch_sapling blue_orchid bush
cherry_sapling closed_eyeblossom cornflower dandelion dark_oak_sapling fern
golden_dandelion jungle_sapling large_fern lilac lily_of_the_valley
oak_sapling open_eyeblossom orange_tulip oxeye_daisy pale_oak_sapling peony
pink_petals pink_tulip poplar_sapling poppy red_tulip rose_bush short_grass
spruce_sapling sunflower sweet_berry_bush tall_grass torchflower
white_tulip wildflowers
""".split())
#: Blocks named like a plant on the soil that survey leaves unjudged, each
#: with its reason; one that excuses nothing fails as stale.
UNJUDGED = {
    "bamboo_sapling": "BambooSaplingBlock is no VegetationBlock: it stands "
                      "on supports_bamboo (sand and gravel, not farmland), "
                      "which survey does not copy",
}


class ClimbTests(unittest.TestCase):
    """A ladder is a way up. The Palace Hotel's only way up is ladders, and a
    walk that could not climb stopped at its lobby and called that a round
    trip, 869 cells of a 138-row tower."""

    CHUNKS = [(x, z) for x in range(0, 2) for z in range(0, 2)]
    BOX = ((0, 60, 0), (15, 90, 15))

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="climb-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, self.CHUNKS)
        self.save = MinecraftSave.load(self.dir)
        self.save.fill((0, 63, 0), (15, 63, 15), "minecraft:stone")
        # A wall to hang on, and a floor up over it.
        self.save.fill((8, 64, 0), (8, 74, 15), "minecraft:stone")

    def ladder(self, x, z, y0, y1, block="minecraft:ladder[facing=west]"):
        for y in range(y0, y1 + 1):
            self.save.set_block(x, y, z, block)

    def loft(self, level=74):
        self.save.fill((8, level, 0), (15, level, 15), "minecraft:stone")

    def walk(self, **kwargs):
        return reachable(self.save, (0, 63, 0), *self.BOX, **kwargs)

    def test_the_table_is_the_games_tag(self):
        from fixtures import block_tags

        self.assertEqual(
            sorted(_CLIMBABLE),
            sorted(name.removeprefix("minecraft:")
                   for name in block_tags()["climbable"]))

    def test_a_wall_with_no_ladder_keeps_you_out(self):
        self.loft()
        self.assertNotIn((10, 74, 5), self.walk().reached)

    def test_a_ladder_to_a_floor_is_a_round_trip_with_no_jump(self):
        self.loft()
        self.ladder(7, 5, 64, 74)
        walk = self.walk(allow_jump=False)
        self.assertIn((10, 74, 5), walk.reached)
        self.assertFalse(walk.stranded)
        self.assertFalse(walk.jumped)

    def test_it_stops_a_block_short_and_there_is_no_way_on(self):
        self.loft()
        self.ladder(7, 5, 64, 72)
        self.assertNotIn((10, 74, 5), self.walk().reached)

    def test_it_runs_on_past_a_floor_and_you_step_off_at_it(self):
        self.loft()
        self.ladder(7, 5, 64, 76)
        self.assertIn((10, 74, 5), self.walk(allow_jump=False).reached)

    def test_a_wall_over_its_top_stops_you_climbing_off(self):
        self.loft()
        self.ladder(7, 5, 64, 74)
        self.save.fill((8, 76, 5), (8, 76, 5), "minecraft:stone")
        self.assertNotIn((8, 74, 5), self.walk().reached)

    def test_a_rung_is_a_cell_and_counts_at_its_level(self):
        self.ladder(7, 5, 64, 70)
        walk = self.walk()
        self.assertIn((7, 69, 5), walk.reached)
        self.assertEqual(walk.footing(69), {(7, 5)})

    def test_a_step_into_a_ladder_catches_it_and_does_not_fall(self):
        """Off a ledge beside the shaft, onto the rung at your own height."""
        self.save.fill((1, 64, 7), (6, 69, 7), "minecraft:stone")
        self.save.fill((1, 64, 6), (4, 69, 6), "minecraft:stone")
        self.ladder(5, 6, 64, 70, "minecraft:ladder[facing=north]")
        space = StepSpace(self.save)
        moves = [cell for cell, _ in space.steps((5, 69, 7), self.BOX)]
        self.assertIn((5, 69, 6), moves, "the rung at your own height")
        self.assertNotIn((5, 63, 6), moves, "the foot of the shaft")

    def test_a_vine_climbs_as_a_ladder_does(self):
        self.loft()
        self.ladder(7, 5, 64, 74, "minecraft:vine[east=true]")
        self.assertIn((10, 74, 5), self.walk().reached)

    def test_the_plan_climbs_a_ladder_it_has_not_written(self):
        self.loft()
        overlay = {(7, y, 5): "minecraft:ladder[facing=west]"
                   for y in range(64, 75)}
        walk = reachable(self.save, (0, 63, 0), *self.BOX, overlay=overlay)
        self.assertIn((10, 74, 5), walk.reached)


class FootingTagTests(unittest.TestCase):
    """Every footing survey judges a plant by is a copy of the game's own
    tag for it, and the game's block classes say which tag."""

    @classmethod
    def setUpClass(cls):
        import importlib

        from fixtures import block_tags
        from heaton_voxel.craft.block_registry import BLOCK_IDS

        cls.survey = importlib.import_module("heaton_voxel.craft.survey")
        cls.tags = block_tags()
        cls.bare = sorted(name.removeprefix("minecraft:") for name in BLOCK_IDS)

    def test_each_footing_table_is_its_tag(self):
        for table, tag in FOOTING_TAGS.items():
            with self.subTest(table=table, tag=tag):
                tagged = {name.removeprefix("minecraft:")
                          for name in self.tags[tag]}
                copied = getattr(self.survey, table)
                added = FOOTING_ADDED.get(table, {})
                left = FOOTING_LEFT.get(table, {})
                self.assertEqual(sorted(copied),
                                 sorted((tagged - set(left)) | set(added)),
                                 "a footing table is a copy of its tag, and "
                                 "a departure is a named choice")
                stale = sorted([n for n in added if n in tagged]
                               + [n for n in left if n not in tagged])
                self.assertEqual(stale, [], "a choice that excuses nothing "
                                            "is stale")

    def test_each_thing_that_grows_is_held_to_its_own_tag(self):
        footing = self.survey._footing
        judged = {name: footing(name) for name in self.bare}
        for name, table in FOOTING_OF.items():
            with self.subTest(name=name):
                self.assertIs(judged[name], getattr(self.survey, table))
        self.assertEqual(
            sorted(name for name, soil in judged.items()
                   if soil is self.survey._SOIL), ON_THE_SOIL)
        self.assertEqual(
            sorted(name for name, soil in judged.items() if soil is not None),
            sorted(ON_THE_SOIL + list(FOOTING_OF)),
            "nothing else is held to a footing")
        for name, why in UNJUDGED.items():
            with self.subTest(unjudged=name):
                self.assertTrue(why.strip())
                self.assertIsNone(judged.get(name))
                self.assertTrue(name in self.bare and name.endswith(
                    self.survey._PLANT_SUFFIX),
                    "a choice that excuses nothing is stale")
        # A potted plant asks nothing of what it stands on.
        self.assertFalse([name for name, soil in judged.items()
                          if soil is not None and name.startswith("potted_")])


class SpawnTests(unittest.TestCase):
    """A stone floor at y=63 over chunk (0, 0), open sky above it, and the
    same chunk again as the nether."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="spawn-tests-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, [(0, 0)])
        shutil.copytree(os.path.join(self.dir, "dimensions/minecraft/overworld"),
                        os.path.join(self.dir, "dimensions/minecraft/the_nether"))
        self.save = MinecraftSave.load(self.dir)
        for dimension in (OVERWORLD, NETHER):
            self.save.fill((0, 63, 0), (15, 63, 15), "minecraft:stone",
                           dimension=dimension)

    def cells(self, corner1, corner2, **kwargs) -> set:
        return {(c.x, c.y, c.z) for c in spawnable(self.save, corner1, corner2,
                                                   **kwargs)}

    def test_a_dark_floor_spawns_and_a_lit_one_does_not(self):
        found = spawnable(self.save, (2, 64, 2), (6, 64, 6))
        self.assertEqual(len(found), 25)
        self.assertEqual(found[0], SpawnCell(2, 64, 2, 0))
        self.assertEqual(found, sorted(found, key=lambda c: (c.y, c.z, c.x)))
        self.save.set_block(4, 63, 4, "minecraft:glowstone")
        self.assertEqual(spawnable(self.save, (2, 64, 2), (6, 64, 6)), [])
        self.assertEqual(spawnable(self.save, (2, 64, 2), (2, 66, 2)), [],
                         "the air above a floor is no floor")

    def test_glass_ice_and_bottom_slab_floors_hold_no_spawn_but_a_top_slab_does(self):
        for x, floor in ((3, "minecraft:glass"), (5, "minecraft:ice"),
                         (7, "minecraft:smooth_stone_slab[type=bottom]"),
                         (9, "minecraft:smooth_stone_slab[type=top]")):
            self.save.set_block(x, 63, 3, floor)
        found = self.cells((3, 64, 3), (9, 64, 3))
        self.assertEqual(sorted(found), [(4, 64, 3), (6, 64, 3), (8, 64, 3),
                                         (9, 64, 3)])

    def test_water_and_a_low_ceiling_hold_no_spawn(self):
        self.save.set_block(3, 64, 3, "minecraft:water")
        self.save.set_block(5, 65, 3, "minecraft:stone")
        self.save.set_block(7, 65, 3, "minecraft:smooth_stone_slab[type=bottom]")
        found = self.cells((3, 64, 3), (7, 64, 3))
        self.assertEqual(sorted(found), [(4, 64, 3), (6, 64, 3), (7, 64, 3)],
                         "a spider fits under a slab at head height")

    def test_what_stands_in_the_middle_of_the_cell_leaves_no_room(self):
        # A carpet, a fence and a slab underfoot take the middle of the
        # cell every monster's box stands in; a ladder and a door hang at
        # its edge, and a zombie spawns beside them.
        # Scaffolding, which a walker climbs through, stands there whole:
        # the game asks for a spawn's room with no entity, and scaffolding
        # answers that with its frame.
        for x, block in ((2, "minecraft:white_carpet"), (4, "minecraft:oak_fence"),
                         (6, "minecraft:smooth_stone_slab[type=bottom]"),
                         (8, "minecraft:ladder[facing=north,waterlogged=false]"),
                         (10, "minecraft:oak_trapdoor[facing=north,half=bottom,"
                              "open=true,powered=false,waterlogged=false]"),
                         (12, "minecraft:oak_door[facing=north,half=lower,"
                              "hinge=left,open=false,powered=false]"),
                         (14, "minecraft:scaffolding[bottom=false,distance=0,"
                              "waterlogged=false]")):
            self.save.set_block(x, 64, 3, block)
        self.save.set_block(12, 65, 3, "minecraft:oak_door[facing=north,half=upper,"
                                       "hinge=left,open=false,powered=false]")
        found = self.cells((2, 64, 3), (14, 64, 3))
        self.assertEqual(sorted(x for x, _, _ in found), [3, 5, 7, 8, 9, 10, 11, 12, 13])

    def test_the_room_is_the_games_collision_for_every_state_a_monster_may_stand_in(self):
        from heaton_voxel.craft.survey import _SPAWN_IMMUNE, _in_the_way
        # Each place the room check departs from the game, with its reason.
        # Every one must err toward reporting a spawn: a false alarm is
        # read, a missed spawn is a creeper in the hall. There are none.
        departures: dict[str, str] = {}
        wrong, used = [], set()
        for name, props, shape in block_shapes():
            if not (shape.spawn_space or name in _SPAWN_IMMUNE):
                continue
            ours = _in_the_way(name, props)
            if ours == shape.body:
                continue
            if name in departures and shape.body and not ours:
                used.add(name)
                continue
            wrong.append(f"{name}{props}: the game {'has' if shape.body else 'has no'} "
                         f"collision in the middle of the cell")
        self.assertEqual(wrong[:5], [], f"{len(wrong)} disagreements")
        self.assertEqual(used, set(departures), "a departure that excuses nothing is stale")
        # And nothing below the feet is read for collision, because nothing
        # that reaches up into the cell above it -- a fence, a wall -- is a
        # floor.
        tall = [f"{name}{props}" for name, props, shape in block_shapes()
                if shape.top > 16 and shape.spawn_floor]
        self.assertEqual(tall, [])

    def test_a_monster_immune_to_a_block_spawns_in_it(self):
        # A zombie's per-cell test refuses these as dangerous, and the game
        # asks it per monster: a stray is immune to powder snow, and a
        # wither skeleton to a wither rose and, being fire-immune, to fire.
        # A berry bush hurts every monster on the rule.
        self.save.set_block(3, 63, 3, "minecraft:dirt")
        self.save.set_block(3, 64, 3, "minecraft:wither_rose")
        self.save.set_block(5, 64, 3, "minecraft:powder_snow")
        self.save.set_block(5, 65, 3, "minecraft:powder_snow")
        self.save.set_block(7, 64, 3, "minecraft:fire")
        self.save.set_block(9, 64, 3, "minecraft:sweet_berry_bush[age=3]")
        self.save.set_block(11, 65, 3, "minecraft:powder_snow")
        found = self.cells((2, 64, 3), (12, 64, 3))
        self.assertEqual(sorted(x for x, _, _ in found),
                         [2, 3, 4, 5, 6, 7, 8, 10, 11, 12])

    def test_every_immunity_is_one_a_zombie_lacks(self):
        from heaton_voxel.craft.survey import _SPAWN_IMMUNE
        # An entry the probe's zombie already passes, or a block that holds
        # fluid or stands in the cell, excuses nothing and is stale.
        states: dict[str, list] = {}
        for name, _, shape in block_shapes():
            if name in _SPAWN_IMMUNE:
                states.setdefault(name, []).append(shape)
        self.assertEqual(sorted(states), sorted(_SPAWN_IMMUNE), "not blocks")
        stale = sorted(name for name, shapes in states.items()
                       if any(s.spawn_space or s.fluid or s.body for s in shapes))
        self.assertEqual(stale, [])

    def test_an_id_with_no_namespace_is_minecrafts(self):
        # The game reads a bare id as minecraft's: a bare stone is a floor,
        # and a bare glowstone beside it lights the cell.
        self.save.set_block(7, 63, 3, "minecraft:air")
        self.assertEqual(self.cells((7, 64, 3), (7, 64, 3),
                                    overlay={(7, 63, 3): "stone"}), {(7, 64, 3)})
        self.assertEqual(self.cells((7, 64, 3), (7, 64, 3),
                                    overlay={(7, 63, 3): "stone",
                                             (8, 64, 3): "glowstone"}), set())

    def test_an_unknown_id_is_air(self):
        unknown = "minecraft:some_block_from_next_year"
        self.save.set_block(3, 64, 3, unknown)
        self.save.set_block(5, 63, 3, unknown)
        found = self.cells((3, 64, 3), (5, 64, 3))
        self.assertIn((3, 64, 3), found, "air to stand in")
        self.assertNotIn((5, 64, 3), found, "and air is no floor")

    def test_the_nether_spawns_up_to_block_light_seven(self):
        for dimension in (OVERWORLD, NETHER):
            self.save.set_block(2, 63, 2, "minecraft:glowstone", dimension=dimension)
        # One block up and six or seven along: light 8, then 7.
        self.assertEqual(spawnable(self.save, (8, 64, 2), (9, 64, 2), dimension=NETHER),
                         [SpawnCell(9, 64, 2, 7)])
        self.assertEqual(spawnable(self.save, (8, 64, 2), (9, 64, 2),
                                   dimension=OVERWORLD), [])

    def test_the_spawn_light_is_the_games_per_dimension(self):
        rules = dimension_types()
        self.assertEqual(set(MONSTER_SPAWN_LIGHT), {OVERWORLD, NETHER, END})
        for dimension, light in MONSTER_SPAWN_LIGHT.items():
            rule = rules[dimension]
            limit = rule["block_light_limit"]
            with self.subTest(dimension=dimension):
                self.assertEqual(light, min(limit if limit < 15 else 15,
                                            rule["light_level_max"]))

    def test_a_lit_furnace_does_not_spawnproof_a_room(self):
        self.save.set_block(5, 64, 5, "minecraft:furnace[facing=north,lit=true]")
        self.assertEqual(spawnable(self.save, (6, 64, 5), (6, 64, 5)),
                         [SpawnCell(6, 64, 5, 0)], "it goes out on its first tick")

    def test_a_plan_is_checked_before_it_is_written(self):
        plan = {(4, 63, 4): "minecraft:glowstone"}
        self.assertEqual(spawnable(self.save, (2, 64, 2), (6, 64, 6), overlay=plan), [])
        self.assertEqual(self.save.get_block(4, 63, 4).name, "minecraft:stone")
        self.assertEqual(len(spawnable(self.save, (2, 64, 2), (6, 64, 6))), 25)
        slab = {(3, 63, 3): ("minecraft:smooth_stone_slab", {"type": "bottom"})}
        self.assertNotIn((3, 64, 3), self.cells((2, 64, 2), (6, 64, 6), overlay=slab))

    def test_an_unknown_dimension_is_refused(self):
        from heaton_voxel.world import Dimension
        region_dir = os.path.join(self.dir, "dimensions/minecraft/overworld/region")
        self.save.dimensions["minecraft:the_moon"] = Dimension("minecraft:the_moon",
                                                                region_dir)
        with self.assertRaises(NBTError) as caught:
            spawnable(self.save, (2, 64, 2), (2, 64, 2), dimension="minecraft:the_moon")
        self.assertIn("the_moon", str(caught.exception))

    def test_ground_that_is_not_there_is_refused(self):
        """A refusal, as the craft tier makes them -- a Refused naming
        every chunk it cannot answer for -- so a caller catching refusals
        tells absence from corruption, which is a plain NBTError."""
        from heaton_voxel.craft.errors import Refused
        from heaton_voxel.nbt import TagString
        with self.assertRaises(Refused) as caught:
            spawnable(self.save, (14, 64, 0), (20, 64, -20))
        self.assertIn("never been generated", str(caught.exception))
        self.assertEqual(len(caught.exception.faults), 5, caught.exception.faults)
        self.save.dimension().chunk(0, 0).data["Status"] = TagString("minecraft:carvers")
        with self.assertRaises(Refused) as caught:
            spawnable(self.save, (2, 64, 2), (2, 64, 2))
        self.assertIn("partly generated", str(caught.exception))


# --------------------------------------------------------------------------
# Where is the water, and will it stay?
# --------------------------------------------------------------------------

from heaton_voxel.blocks import BlockState  # noqa: E402
from heaton_voxel.craft.errors import Refused  # noqa: E402
from heaton_voxel.craft.survey import (  # noqa: E402
    Containment,
    Leak,
    WaterBody,
    contained,
    starved,
    water_body,
)

SOURCE = "minecraft:water[level=0]"
#: The legacy fixture's open sea, seeded where the survey reads its depths:
#: chunk (-24, 1), every column of it water at y=62.
SEA_SEED = (-376, 62, 24)
#: The chunks around the sea the fixture never generated: where the sea
#: runs off the edge of the region the fixture was cut from.
SEA_EDGE = {(-26, 1), (-26, 2), (-25, 0), (-25, 3), (-24, 0), (-24, 3),
            (-23, 0), (-23, 2), (-22, 1)}


def _tool_lines(relative: str, first: int, last: int, opens: str, closes: str):
    """Lines ``first..last`` of a ``tools/`` script, dedented: the loop a
    test runs as it stands, without running the script around it, which
    loads JeffWorld. ``opens``/``closes`` pin where they begin and end, so a
    script that moved fails here rather than running something else."""
    import textwrap
    with open(os.path.join(_ROOT, "tools", relative), encoding="utf-8") as handle:
        lines = handle.read().splitlines()[first - 1:last]
    assert lines[0].strip() == opens, lines[0]
    assert lines[-1].strip().endswith(closes), lines[-1]
    return textwrap.dedent("\n".join(lines))


def _water_world(test, chunks=((0, 0), (1, 0), (0, 1), (1, 1))):
    """A flat world over ``chunks`` (32 wide unless told): stone to y=61,
    air above, every chunk finished."""
    directory = tempfile.mkdtemp(prefix="survey-water-")
    test.addCleanup(shutil.rmtree, directory, ignore_errors=True)
    make_save(directory, list(chunks))
    save = MinecraftSave.load(directory)
    xs = [x for x, _ in chunks]
    zs = [z for _, z in chunks]
    save.fill((min(xs) * 16, 50, min(zs) * 16),
              (max(xs) * 16 + 15, 61, max(zs) * 16 + 15), "minecraft:stone")
    return save


class WaterBodyTests(unittest.TestCase):
    """One body of water, read as the game holds it: the fixture's real sea
    -- with its kelp, seagrass and the glow lichen waterlogged on its floor
    -- and ponds built to have the problem, held to the floods the lake,
    frozen-lake and park scripts each wrote by hand."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="survey-body-")
        cls.save = MinecraftSave.load(make_world(cls.dir))
        cls.sea = water_body(cls.save, SEA_SEED)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_the_sea_is_one_body_with_its_plants_in_it(self):
        sea = self.sea
        self.assertIsInstance(sea, WaterBody)
        self.assertEqual(len(sea.cells), 28_948)
        kinds = {}
        for cell in sea.cells:
            state = self.save.get_block(*cell)
            kinds[state.name] = kinds.get(state.name, 0) + 1
            if state.name == "minecraft:glow_lichen":
                self.assertEqual(state.properties["waterlogged"], "true")
            if state.name == "minecraft:water":
                self.assertEqual(state.properties.get("level", "0"), "0", cell)
        self.assertEqual(kinds, {
            "minecraft:water": 27_894, "minecraft:kelp_plant": 751,
            "minecraft:kelp": 141, "minecraft:seagrass": 94,
            "minecraft:tall_seagrass": 64, "minecraft:glow_lichen": 4})
        self.assertTrue(sea.still)
        self.assertEqual(sea.flowing, frozenset())
        self.assertEqual((sea.ice, sea.lids, sea.clipped), (frozenset(),) * 3)
        self.assertEqual((sea.seed, sea.surface), (SEA_SEED, 62))
        self.assertIn("28948 cells", repr(sea))

    def test_it_says_where_it_runs_out_of_generated_chunks(self):
        """Could not read is never dry: the sea runs on into chunks the
        fixture never had, and the body names each one."""
        self.assertEqual(self.sea.missing, dict.fromkeys(sorted(SEA_EDGE),
                                                         "ungenerated"))

    def test_within_bounds_the_flood_and_says_where_it_cut(self):
        chunk = ((-384, 0, 16), (-369, 0, 31))       # a y is ignored
        body = water_body(self.save, SEA_SEED, within=chunk)
        self.assertEqual(len(body.cells), 8_165)
        self.assertTrue(all(-384 <= x <= -369 and 16 <= z <= 31
                            for x, _, z in body.cells))
        self.assertTrue(body.clipped)
        for x, y, z in body.clipped:
            self.assertTrue(x in (-385, -368) or z in (15, 32), (x, y, z))
            self.assertTrue(holds_water_at(self.save, (x, y, z)))

    def test_its_columns_are_the_column_readers(self):
        columns = self.sea.columns
        self.assertIs(self.sea.columns, columns, "read once")
        self.assertEqual(len(columns), 1_280)
        self.assertEqual({probe.waterline for probe in columns.values()}, {62})
        depths = self.sea.depth
        self.assertEqual((min(depths.values()), max(depths.values())), (13, 48))
        beds = {}
        for probe in columns.values():
            beds[probe.kind] = beds.get(probe.kind, 0) + 1
            self.assertFalse(probe.lid)
        self.assertEqual(beds, {"sand": 1_231, "stone": 49})
        for (x, z), probe in list(columns.items())[::97]:
            walked = walk_surface(self.save, x, z)
            self.assertEqual((probe.y, probe.water, probe.block),
                             (walked.y, walked.water, walked.block))
        self.assertEqual(self.sea.shore, frozenset(),
                         "every side of the sea is sea or not there to read")

    def test_it_is_lakelibs_surface_flood_on_the_sea(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_survey_lakelib", os.path.join(_ROOT, "tools", "lake", "lakelib.py"))
        lakelib = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lakelib)
        flooded = lakelib.surface_flood(self.save, self.save.dimension(),
                                        [SEA_SEED[::2]], 62)
        self.assertEqual(len(flooded), 1_280)
        self.assertEqual(flooded, {(x, z) for x, y, z in self.sea.cells
                                   if y == 62})
        self.assertEqual(water_body(self.save, SEA_SEED[::2]).cells,
                         self.sea.cells, "a column seeds at its waterline")

    def test_a_runaway_body_is_refused_and_a_dry_seed_is_an_error(self):
        with self.assertRaises(Refused) as caught:
            water_body(self.save, SEA_SEED, limit=1_000)
        self.assertIn("within=", str(caught.exception))
        for seed in ((-376, 40, 24), (VILLAGE_X0 + 8, VILLAGE_Z0 + 8)):
            with self.subTest(seed=seed):
                with self.assertRaises(NBTError) as caught:
                    water_body(self.save, seed)
                self.assertNotIsInstance(caught.exception, Refused)
                self.assertIn("no water", str(caught.exception))
        with self.assertRaises(NBTError) as caught:
            water_body(self.save, (5_000, 62, 5_000))
        self.assertIn("never generated", str(caught.exception))
        with self.assertRaises(NBTError) as caught:
            water_body(self.save, SEA_SEED, within=((0, 0), (15, 15)))
        self.assertIn("outside within=", str(caught.exception))
        with self.assertRaises(NBTError) as caught:
            water_body(self.save, (SEA_SEED[0], 400, SEA_SEED[2]))
        self.assertIn("past the world's -64..319", str(caught.exception))

    def test_the_limit_counts_its_ice_with_its_water(self):
        save = _water_world(self)
        save.fill((2, 61, 2), (5, 61, 5), SOURCE)              # 16 of water
        save.fill((2, 62, 2), (5, 62, 5), "minecraft:ice")     # 16 of ice
        body = water_body(save, (3, 61, 3), limit=32)
        self.assertEqual((len(body.cells), len(body.ice)), (16, 16))
        with self.assertRaises(Refused):
            water_body(save, (3, 61, 3), limit=31)

    def test_a_roofed_pond_is_read_from_its_water_down(self):
        """A column's probe starts at the body's own top cell, never at the
        sky: under a stone roof the pond is still three deep at 62, on the
        stone at 59."""
        save = _water_world(self)
        save.fill((0, 62, 0), (15, 62, 15), "minecraft:stone")
        save.fill((2, 60, 2), (9, 62, 9), SOURCE)
        save.fill((0, 70, 0), (15, 70, 15), "minecraft:stone")
        probe = water_body(save, (6, 61, 6)).columns[(6, 6)]
        self.assertEqual((probe.waterline, probe.water, probe.y, probe.lid),
                         (62, 3, 59, False))
        self.assertEqual(probe.block, BlockState("minecraft:stone"))

    def test_a_chunk_it_cannot_read_is_named_and_never_dry(self):
        """A pond running into a chunk that cannot be read stops at it, the
        chunk named ``"unreadable"`` -- and the column over the border is no
        dry shore. ``contained`` raises the chunk's own error."""
        from heaton_voxel.nbt import TagInt
        directory = tempfile.mkdtemp(prefix="survey-water-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        make_save(directory, [(0, 0), (1, 0)])
        save = MinecraftSave.load(directory)
        save.dimension(OVERWORLD).chunk(1, 0).data["DataVersion"] = TagInt(100)
        save.fill((0, 50, 0), (15, 61, 15), "minecraft:stone")
        save.fill((8, 60, 4), (15, 61, 8), SOURCE)
        body = water_body(save, (10, 61, 6))
        self.assertEqual(body.missing, {(1, 0): "unreadable"})
        self.assertEqual(len(body.cells), 8 * 2 * 5)
        self.assertEqual(body.shore, frozenset(
            {(7, z) for z in range(4, 9)}
            | {(x, z) for x in range(8, 16) for z in (3, 9)}))
        with self.assertRaises(NBTError) as caught:
            contained(save, {(15, 61, 6): "minecraft:stone"})
        self.assertNotIsInstance(caught.exception, Refused)

    def test_the_shore_stops_where_within_cut_the_body(self):
        save = _water_world(self)
        save.fill((2, 60, 2), (9, 61, 9), SOURCE)
        body = water_body(save, (3, 61, 5), within=((0, 0), (5, 31)))
        self.assertEqual({(x, z) for x, _, z in body.clipped},
                         {(6, z) for z in range(2, 10)})
        self.assertEqual(body.shore, frozenset(
            {(1, z) for z in range(2, 10)}
            | {(x, z) for x in range(2, 6) for z in (1, 10)}),
            "the water past the cut is no dry shore")

    def test_ice_under_its_water_or_stacked_on_its_ice_is_not_on_it(self):
        """Freeze ice joins lying on the body, beside it, or beside joined
        ice at its own y: never in the bed under the water, nor a block of
        ice set on the sheet. A lid is ice over anything the game loads as
        air, an id it does not have included."""
        save = _water_world(self)
        save.fill((2, 59, 2), (9, 61, 9), SOURCE)
        save.fill((2, 62, 2), (9, 62, 9), "minecraft:ice")
        save.set_block(5, 58, 5, "minecraft:ice")          # in the bed
        save.set_block(5, 63, 5, "minecraft:ice")          # on the sheet
        save.set_block(10, 62, 5, "minecraft:ice")         # on the bank
        save.set_block(11, 62, 5, "minecraft:ice")         # over a cellar
        save.set_block(11, 61, 5, "mymod:cellar")
        body = water_body(save, (5, 60, 5))
        sheet = {(x, 62, z) for x in range(2, 10) for z in range(2, 10)}
        self.assertEqual(body.ice, frozenset(sheet | {(10, 62, 5), (11, 62, 5)}))
        self.assertEqual(body.lids, frozenset({(11, 62, 5)}))
        self.assertEqual(len(body.cells), 8 * 8 * 3)

    def test_an_overlay_is_read_over_the_world(self):
        """The removed cells of a fill, as solid ground: the sea still
        joins round a plug, and a plug across a chunk cuts the cells past it
        off within the chunk."""
        x0, z0 = -384, 16
        wall = {(x0 + 4, y, z): "minecraft:stone"
                for y in range(-64, 63) for z in range(z0, z0 + 16)}
        body = water_body(self.save, SEA_SEED, overlay=wall,
                          within=((x0, z0), (x0 + 15, z0 + 15)))
        self.assertTrue(body.cells)
        self.assertTrue(all(x > x0 + 4 for x, _, _ in body.cells),
                        "the plug across the chunk cuts its west end off")
        self.assertEqual(self.save.get_block(x0 + 4, 62, z0 + 4).name,
                         "minecraft:water", "an overlay writes nothing")

    def test_ice_on_it_is_crossed_and_its_lids_named(self):
        save = _water_world(self)
        save.fill((2, 60, 2), (9, 61, 9), SOURCE)
        save.fill((2, 62, 2), (9, 62, 9), "minecraft:ice")
        save.set_block(10, 62, 5, "minecraft:ice")         # on the bank
        save.set_block(11, 62, 5, "minecraft:ice")         # over a room
        save.fill((11, 61, 5), (12, 61, 5), "minecraft:air")
        save.fill((20, 60, 2), (25, 62, 9), SOURCE)         # a second pond
        save.fill((10, 62, 8), (19, 62, 8), "minecraft:packed_ice")
        body = water_body(save, (5, 61, 5))
        sheet = {(x, 62, z) for x in range(2, 10) for z in range(2, 10)}
        self.assertEqual(body.ice, frozenset(sheet | {(10, 62, 5), (11, 62, 5)}))
        self.assertEqual(body.lids, frozenset({(11, 62, 5)}))
        self.assertEqual(body.surface, 62, "frozen: the ice is the waterline")
        self.assertEqual(len(body.cells), 128)
        self.assertFalse(any(x >= 20 for x, _, _ in body.cells),
                         "packed ice is never frozen water, and is not crossed")
        self.assertEqual(water_body(save, (11, 62, 5)).cells, body.cells,
                         "seeded on the lid itself")
        probe = body.columns[(11, 5)]
        self.assertEqual((probe.y, probe.ice, probe.bed_y, probe.lid),
                         (62, 1, None, True))
        self.assertIn((12, 5), body.shore)
        self.assertNotIn((11, 5), body.shore)

    def test_falling_water_under_a_sheet_of_sources_is_not_still(self):
        """Sources laid over an air trench, as the walled city's moat was:
        the water under them is all falling, every cell of it a flow a
        tick moves."""
        save = _water_world(self)
        save.fill((4, 56, 4), (12, 61, 6), "minecraft:air")
        save.fill((4, 62, 4), (12, 62, 6), SOURCE)
        falling = set()
        for y in range(56, 62):
            save.fill((4, y, 4), (12, y, 6), "minecraft:water[level=8]")
            falling |= {(x, y, z) for x in range(4, 13) for z in range(4, 7)}
        body = water_body(save, (8, 62, 5))
        self.assertFalse(body.still)
        self.assertEqual(body.flowing, frozenset(falling))
        self.assertEqual(body.depth[(8, 5)], 7)

    def test_the_frozen_lakes_mask_comes_out_of_it(self):
        """``tools/frozen_lake/plan.py`` built its lake from a box mask at
        the surface, opened so canals fall away, and ``survey.py`` before
        it by a flood at the surface: run as they stand, over the box and
        over the body, they find the same lake, beds and lids."""
        from collections import deque
        save = _water_world(self)
        save.fill((4, 59, 4), (19, 61, 19), SOURCE)
        save.fill((4, 62, 4), (19, 62, 19), "minecraft:ice")
        save.fill((20, 62, 11), (27, 62, 11), SOURCE)        # a canal
        save.fill((24, 62, 24), (26, 62, 26), SOURCE)        # a puddle
        save.fill((6, 59, 6), (7, 61, 7), "minecraft:air")   # a room under
        player = (12, 12)
        surface = 62
        body = water_body(save, player)
        top = {(x, z) for x, y, z in body.cells | body.ice if y == surface}
        self.assertIn((27, 11), top)
        self.assertNotIn((25, 25), top, "the puddle is another body")

        plan = _tool_lines("frozen_lake/plan.py", 64, 89, "mask = set()", "))}")
        water, air = BlockState.parse(SOURCE), BlockState("minecraft:air")
        lakes = []
        for read in (save.get_block,
                     lambda x, y, z: water if (x, z) in top else air):
            space = {"state": read, "SURFACE": surface, "x0": 0, "z0": 0,
                     "x1": 31, "z1": 31, "px": player[0], "pz": player[1],
                     "OPEN_RADIUS": 2, "deque": deque,
                     "LAKE_TOP": {"minecraft:water", "minecraft:ice"}}
            exec(plan, space)
            lakes.append(space["lake"])
        self.assertEqual(lakes[0], lakes[1])
        self.assertEqual(lakes[0], {(x, z) for x in range(4, 20)
                                    for z in range(4, 20)})

        flood = _tool_lines("frozen_lake/survey.py", 39, 60,
                            "seen = {(px, pz)}", "queue.append((nx, nz))")
        space = {"name": lambda x, y, z: save.get_block(x, y, z).name,
                 "SURFACE": surface, "px": player[0], "pz": player[1],
                 "deque": deque, "LIMIT": 40_000,
                 "TOP": {"minecraft:water", "minecraft:ice"},
                 "IN_WATER": {"minecraft:water", "minecraft:sea_pickle",
                              "minecraft:seagrass", "minecraft:tall_seagrass",
                              "minecraft:kelp", "minecraft:kelp_plant"}}
        exec(flood, space)
        self.assertEqual(set(space["columns"]), top)
        for column, found in space["columns"].items():
            probe = body.columns[column]
            if found["bed"] == "minecraft:air":
                self.assertTrue(probe.lid, column)
                self.assertIsNone(probe.bed_y)
                self.assertIn((*column[:1], surface, column[1]), body.lids)
            else:
                self.assertEqual(probe.bed_y, found["bed_y"], column)
        self.assertEqual(body.lids, frozenset(
            (x, surface, z) for x in (6, 7) for z in (6, 7)))

    def test_the_parks_lake_is_as_deep_as_its_plan_says(self):
        """``tools/park/park_plan.py``'s ``depth_map`` read no world: it
        banded the planned footprint 1, 2 and 3 deep, and
        ``build_park.water`` laid each column's bed at ``WATER_TOP - d``.
        Laid that way here, the lake is one still body whose depth, column
        by column, is the plan's."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_survey_park_plan", os.path.join(_ROOT, "tools", "park", "park_plan.py"))
        park = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(park)
        lake = park.lake_cells()
        depth = park.depth_map(lake)
        self.assertEqual(len(lake), 499)
        save = _water_world(self, [(x, z) for x in range(-3, 0)
                                   for z in range(15, 18)])
        for x, z in sorted(lake):                       # build_park.water's
            bed_y = park.WATER_TOP - depth[(x, z)]
            save.set_block(x, bed_y, z, "minecraft:sand")
            for y in range(bed_y + 1, park.WATER_TOP + 1):
                save.set_block(x, y, z, park.WATER)
            for y in range(park.WATER_TOP + 1, 64):
                save.set_block(x, y, z, park.AIR)
        body = water_body(save, min(lake))
        self.assertTrue(body.still)
        self.assertEqual(body.depth, depth)
        self.assertEqual(set(body.columns), lake)
        self.assertEqual({probe.y for probe in body.columns.values()},
                         {park.WATER_TOP - d for d in (1, 2, 3)})

    def test_lakeplans_lake_and_lip(self):
        """``tools/lake/lakeplan.py``'s ``build_mask``, loaded by path and
        run as it stands: its lake is the body's columns open at the
        waterline, and its lip -- the dry columns the lake would spill into
        once its rim were sources -- is what :func:`contained` says such a
        rim lets water into."""
        save = _water_world(self)
        save.fill((0, 62, 0), (31, 62, 31), "minecraft:stone")
        save.fill((8, 60, 8), (20, 62, 20), SOURCE)
        # A rim flowing out of the lake over the ground, settled: it runs
        # out at level 7, one cell short of a hollow it never reaches.
        for level in range(1, 8):
            save.set_block(20 + level, 62, 13, f"minecraft:water[level={level}]")
        save.set_block(28, 62, 13, "minecraft:air")         # a dry hollow
        lakeplan = _lakeplan()
        lakeplan.LEVEL, lakeplan.SEED_XZ = 62, (14, 14)
        lakeplan.BOUNDS, lakeplan.PROTECT = (0, 0, 31, 31), {}
        lake, lip, _ = lakeplan.build_mask(save, save.dimension())

        body = water_body(save, (14, 14))
        surface = {(x, y, z) for x, y, z in body.cells | body.ice if y == 62}
        self.assertEqual(lake, {(x, z) for x, _, z in surface})
        self.assertEqual(lip, {(28, 13)})
        self.assertIn((28, 13), body.shore)
        as_it_is = contained(save, {cell: save.get_block(*cell) for cell in surface})
        self.assertEqual((as_it_is.leaks, as_it_is.standing), ([], []),
                         "settled: the rim as it is spills nowhere")
        rim = contained(save, dict.fromkeys(surface, SOURCE))
        self.assertEqual({(x, z) for (x, y, z), *_ in rim.leaks if y == 62},
                         lip)
        self.assertEqual([(leak.kind, leak.source) for leak in rim.leaks],
                         [("spreads", (27, 62, 13))])
        self.assertEqual(rim.standing, [])


def _lakeplan():
    """``tools/lake/lakeplan.py``, loaded by path. It imports its siblings
    by their bare names -- ``lakelib``, ``noise`` and ``field`` -- and puts
    two folders on ``sys.path``; each name is lent while it loads, and the
    names and the path are given back after."""
    import importlib.util
    folder = os.path.join(_ROOT, "tools", "lake")
    names = ("lakelib", "noise", "field")
    before = {name: sys.modules.get(name) for name in names}
    path = list(sys.path)
    try:
        for name in names:
            spec = importlib.util.spec_from_file_location(
                name, os.path.join(folder, f"{name}.py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        spec = importlib.util.spec_from_file_location(
            "_survey_lakeplan", os.path.join(folder, "lakeplan.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = path
        for name, found in before.items():
            if found is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = found
    return module


def holds_water_at(save, cell) -> bool:
    from heaton_voxel.craft.block_facts import holds_water
    state = save.get_block(*cell)
    return state is not None and holds_water(state.name, state.properties)


class ContainedTests(unittest.TestCase):
    """Where water would go, by the game's own first step of spread: each
    rule read off the 26.3 jar, each built here to show it."""

    def setUp(self):
        self.save = _water_world(self)

    def leaks(self, plan, **kwargs):
        found = contained(self.save, plan, **kwargs)
        self.assertIsInstance(found, Containment)
        self.assertEqual(found.held, not found.leaks)
        return sorted((leak.cell, leak.kind) for leak in found.leaks)

    def pool(self):
        """A stone basin dug at x, z 4..6, y 60..61, and the plan that fills
        it with sources."""
        self.save.fill((4, 60, 4), (6, 61, 6), "minecraft:air")
        return {(x, y, z): SOURCE for x in range(4, 7) for z in range(4, 7)
                for y in (60, 61)}

    def test_a_closed_cup_holds_and_a_wall_taken_away_leaks(self):
        plan = self.pool()
        found = contained(self.save, plan)
        self.assertTrue(found.held)
        self.assertEqual((found.leaks, found.standing), ([], []))
        self.save.fill((4, 60, 4), (6, 61, 6), SOURCE)     # the basin, built
        self.assertTrue(contained(self.save, {(7, 61, 5): "minecraft:stone"}).held,
                        "a wall written as it is changes nothing")
        found = contained(self.save, {(7, 61, 5): "minecraft:air"})
        self.assertFalse(found.held)
        self.assertEqual(len(found.leaks), 1)
        leak = found.leaks[0]
        self.assertIsInstance(leak, Leak)
        self.assertEqual((leak.cell, leak.kind, leak.source),
                         ((7, 61, 5), "spreads", (6, 61, 5)))
        self.assertEqual(leak.block, BlockState("minecraft:air"))
        self.assertIn("(6, 61, 5)", str(leak))

    def test_beside_air_it_spreads_and_over_a_hole_it_falls(self):
        plan = self.pool()
        self.save.set_block(7, 61, 5, "minecraft:air")
        self.assertEqual(self.leaks(plan), [((7, 61, 5), "spreads")])
        self.save.set_block(7, 61, 5, "minecraft:stone")
        self.save.set_block(5, 59, 5, "minecraft:air")
        self.assertEqual(self.leaks(plan), [((5, 59, 5), "falls")])

    def test_falling_water_is_amount_8_at_every_level(self):
        """``LiquidBlock.getFluidState`` reads ``min(level, 8)``: every
        level from 8 to 15 is falling water of amount 8, which reaches the
        sides with 7. A flow reaches them with one less than its amount,
        so level 7 (amount 1) goes nowhere and level 6 does."""
        sides = [((9, 62, 10), "spreads"), ((10, 62, 9), "spreads"),
                 ((10, 62, 11), "spreads"), ((11, 62, 10), "spreads")]
        for level in range(8, 16):
            with self.subTest(level=level):
                self.assertEqual(
                    self.leaks({(10, 62, 10): f"minecraft:water[level={level}]"}),
                    sides)
        self.assertEqual(self.leaks({(10, 62, 10): "minecraft:water[level=7]"}), [])
        self.assertEqual(self.leaks({(10, 62, 10): "minecraft:water[level=6]"}),
                         sides)

    def trench(self, *blocks):
        """A one-wide trench along x at y=62 over the stone, closed at both
        ends, holding ``blocks`` from x=10 east."""
        self.save.fill((9, 62, 9), (11 + len(blocks), 62, 11), "minecraft:stone")
        return {(10 + i, 62, 10): block for i, block in enumerate(blocks)}

    def test_flowing_water_never_waterlogs_and_a_slab_beside_a_source_holds_it(self):
        """``SimpleWaterloggedBlock.canPlaceLiquid`` takes a source only, so
        one source beside a dry slab, a dry stair or a fence -- or any flow
        -- leaves it dry, and the water stops at it."""
        for dry in ("minecraft:oak_slab[type=bottom]", "minecraft:stone_slab",
                    "minecraft:oak_stairs[facing=east]", "minecraft:oak_fence"):
            for water in (SOURCE, "minecraft:water[level=1]",
                          "minecraft:water[level=8]"):
                with self.subTest(dry=dry, water=water):
                    self.assertEqual(self.leaks(self.trench(water, dry)), [])

    def test_two_sources_convert_a_container_and_one_does_not(self):
        """``getNewLiquid``: a cell two sources reach through their walls,
        over a block that is solid or a source, becomes a source -- and a
        container there is waterlogged (``WATER_SOURCE_CONVERSION``, taken
        as on). Over air it does not, and one written wet already holds."""
        stair = "minecraft:oak_stairs[facing=north]"
        plan = self.trench(SOURCE, stair, SOURCE)
        found = contained(self.save, plan)
        self.assertEqual([(leak.cell, leak.kind, leak.source) for leak in found.leaks],
                         [((11, 62, 10), "waterlogs", (10, 62, 10)),
                          ((11, 62, 10), "waterlogs", (12, 62, 10))])
        self.assertIn("two water sources", found.leaks[0].reason)
        self.assertEqual(self.leaks(self.trench(SOURCE, stair)), [])
        back = "minecraft:oak_stairs[facing=east]"
        self.assertEqual(self.leaks(self.trench(SOURCE, back, SOURCE)), [],
                         "a stair's back is a whole face, a wall: only one "
                         "source reaches it through its walls")
        wet = "minecraft:oak_stairs[facing=north,waterlogged=true]"
        self.assertEqual(self.leaks({**plan, (11, 62, 10): wet}), [])
        double = "minecraft:oak_slab[type=double]"
        self.assertEqual(self.leaks(self.trench(SOURCE, double, SOURCE)), [],
                         "a double slab takes no water at all")
        self.save.set_block(11, 61, 10, "minecraft:air")
        self.assertEqual(self.leaks(plan), [],
                         "over air two sources make no third")

    def test_water_meets_the_floor_through_its_wall_too(self):
        """``canPassThroughWall`` is asked downward as well: a waterlogged
        bottom slab over a hole keeps its water out of it -- its underside
        is whole -- and spreads by its open upper half; and a flow over a
        waterlogged top slab is over no water hole (``isWaterHole`` asks the
        wall first), so it spreads, where over a plain source it would
        not."""
        plan = self.trench("minecraft:oak_slab[type=bottom,waterlogged=true]",
                           "minecraft:air")
        self.save.set_block(10, 61, 10, "minecraft:air")
        self.assertEqual(self.leaks(plan), [((11, 62, 10), "spreads")])
        self.save.set_block(10, 61, 10, "minecraft:stone")
        plan = {**self.trench("minecraft:water[level=1]", "minecraft:air"),
                (10, 61, 10): "minecraft:oak_slab[type=top,waterlogged=true]"}
        self.assertEqual(self.leaks(plan), [((11, 62, 10), "spreads")])
        plan[(10, 61, 10)] = SOURCE
        self.assertEqual(self.leaks(plan), [],
                         "over open water a flow goes nowhere")

    def test_a_dry_container_under_water_is_no_hole_and_stays_dry(self):
        """A dry slab under water takes no flowing water, so the water over
        it is over no water hole and a flow there spreads to its sides; and
        a source over a dry slab with a hole under it leaves the slab dry --
        the falling water the game would make takes no container."""
        slab = "minecraft:oak_slab[type=bottom]"
        plan = {**self.trench("minecraft:water[level=1]", "minecraft:air"),
                (10, 61, 10): slab}
        self.assertEqual(self.leaks(plan), [((11, 62, 10), "spreads")])
        self.save.set_block(10, 60, 10, "minecraft:air")
        self.assertEqual(self.leaks({**self.trench(SOURCE), (10, 61, 10): slab}), [])

    def test_after_a_fall_three_sources_still_spread_it(self):
        """``spread`` goes down first, and then to the sides only where
        three sources stand beside the cell: a flow between three sources
        over a drain falls into it and spreads out of its fourth side too,
        and with two it only falls."""
        self.save.fill((8, 62, 8), (12, 62, 12), "minecraft:stone")
        self.save.set_block(10, 61, 10, "minecraft:air")
        plan = {(10, 62, 10): "minecraft:water[level=1]", (9, 62, 10): SOURCE,
                (11, 62, 10): SOURCE, (10, 62, 9): SOURCE,
                (10, 62, 11): "minecraft:air"}
        self.assertEqual(self.leaks(plan), [((10, 61, 10), "falls"),
                                            ((10, 62, 11), "spreads")])
        drain = [(10, 61, 10)]
        self.assertEqual(self.leaks(plan, spill=drain), [((10, 62, 11), "spreads")])
        plan[(10, 62, 9)] = "minecraft:stone"
        self.assertEqual(self.leaks(plan, spill=drain), [])

    def test_a_plan_two_steps_from_the_water_is_read(self):
        """One cell's step reads two cells off: a floor laid under a dry
        stair between two sources makes the stair's cell a third source and
        waterlogs it, though the floor touches no water."""
        self.save.fill((9, 62, 9), (13, 62, 11), "minecraft:stone")
        self.save.set_block(10, 62, 10, SOURCE)
        self.save.set_block(12, 62, 10, SOURCE)
        self.save.set_block(11, 62, 10, "minecraft:oak_stairs[facing=north]")
        self.save.set_block(11, 61, 10, "minecraft:air")
        self.assertTrue(contained(self.save, {(11, 61, 10): "minecraft:air"}).held)
        self.assertEqual(self.leaks({(11, 61, 10): "minecraft:stone"}),
                         [((11, 62, 10), "waterlogs"), ((11, 62, 10), "waterlogs")])

    def test_at_the_worlds_floor_water_falls_out_of_it_and_goes_nowhere(self):
        """Under the world's floor is the game's void air: ``spread`` lets
        water fall into it, the write past the world does nothing, and water
        that fell goes to its sides only beside three sources -- so a source
        on the floor stays put, and nothing is reported. One block up, on
        the stone, it spreads."""
        self.save.fill((8, -64, 8), (12, -63, 12), "minecraft:stone")
        self.save.fill((11, -64, 10), (11, -63, 10), "minecraft:air")
        self.assertEqual(self.leaks({(10, -64, 10): SOURCE}), [])
        self.assertEqual(self.leaks({(10, -63, 10): SOURCE}),
                         [((11, -63, 10), "spreads")])

    def test_water_leaves_a_waterlogged_slab_by_its_open_half_and_not_a_grate(self):
        """Water passes a wall unless the two collision faces between the
        cells cover the face: a bottom slab's sides are half open, and a
        copper grate's are whole."""
        slab = "minecraft:oak_slab[type=bottom,waterlogged=true]"
        self.assertEqual(self.leaks({(20, 62, 20): slab}),
                         [((19, 62, 20), "spreads"), ((20, 62, 19), "spreads"),
                          ((20, 62, 21), "spreads"), ((21, 62, 20), "spreads")])
        grate = "minecraft:copper_grate[waterlogged=true]"
        self.assertEqual(self.leaks({(20, 62, 20): grate}), [])

    def test_lava_touching_water_is_a_leak(self):
        for lava in ((11, 62, 10), (10, 63, 10), (10, 61, 10)):
            with self.subTest(lava=lava):
                plan = self.trench(SOURCE)
                self.save.set_block(*lava, "minecraft:lava")
                found = contained(self.save, plan)
                self.assertIn((lava, "lava", (10, 62, 10)),
                              [(leak.cell, leak.kind, leak.source) for leak in found.leaks])
                self.save.set_block(*lava, "minecraft:stone")

    def test_a_leak_already_running_is_standing_not_the_plans(self):
        """lakeplan's rule: the lake laps against this cell today, so a
        plan that leaves it changes nothing."""
        self.save.fill((4, 62, 5), (10, 62, 5), SOURCE)     # a channel...
        self.save.fill((4, 62, 4), (11, 62, 4), "minecraft:stone")
        self.save.fill((4, 62, 6), (11, 62, 6), "minecraft:stone")
        found = contained(self.save, {(10, 62, 6): "minecraft:stone"})
        self.assertTrue(found.held)
        self.assertEqual(found.leaks, [])
        self.assertEqual([(leak.cell, leak.kind) for leak in found.standing],
                         [((11, 62, 5), "spreads")])      # ...open at its end
        found = contained(self.save, {(10, 62, 4): "minecraft:air"})
        self.assertEqual([(leak.cell, leak.kind) for leak in found.leaks],
                         [((10, 62, 4), "spreads")])
        self.assertEqual([leak.cell for leak in found.standing], [(11, 62, 5)])

    def test_lava_or_a_block_written_where_water_runs_is_the_plans(self):
        """Standing is water entering a cell the same way without the plan,
        into nothing the plan put there: lava written at the open end of a
        channel meets the water, and a torch or grass there is washed away,
        each the plan's leak; air written there, or the torch already
        standing written back as it is, changes nothing."""
        self.save.fill((4, 62, 5), (10, 62, 5), SOURCE)
        self.save.fill((4, 62, 4), (11, 62, 4), "minecraft:stone")
        self.save.fill((4, 62, 6), (11, 62, 6), "minecraft:stone")
        end = (11, 62, 5)
        for block, kind in (("minecraft:lava", "lava"),
                            ("minecraft:torch", "spreads"),
                            ("minecraft:short_grass", "spreads")):
            with self.subTest(block=block):
                found = contained(self.save, {end: block})
                self.assertEqual([(leak.cell, leak.kind, leak.source)
                                  for leak in found.leaks],
                                 [(end, kind, (10, 62, 5))])
                self.assertEqual(found.standing, [])
        for block in ("minecraft:air", "minecraft:cave_air", "mymod:nothing"):
            with self.subTest(block=block):
                found = contained(self.save, {end: block})
                self.assertEqual((found.leaks, [(leak.cell, leak.kind)
                                                for leak in found.standing]),
                                 ([], [(end, "spreads")]))
        self.save.set_block(*end, "minecraft:torch")
        found = contained(self.save, {end: "torch"})
        self.assertEqual((found.leaks, [leak.cell for leak in found.standing]),
                         ([], [end]), "the torch standing, written back")

    def test_a_source_beside_stale_flowing_water_feeds_it_further(self):
        """A flow is re-read by the game's ``getNewLiquid`` once a plan
        feeds it, along the whole chain: the cell that reaches the air
        stands four from the plan's source, past the two steps one cell's
        spread reads."""
        self.save.fill((4, 62, 9), (11, 62, 11), "minecraft:stone")
        for x in range(6, 10):
            self.save.set_block(x, 62, 10, "minecraft:water[level=7]")
        self.save.set_block(9, 62, 11, "minecraft:air")
        self.assertEqual(self.leaks({(5, 62, 10): "minecraft:stone"}), [])
        found = contained(self.save, {(5, 62, 10): SOURCE})
        self.assertEqual([(leak.cell, leak.kind, leak.source) for leak in found.leaks],
                         [((9, 62, 11), "spreads", (9, 62, 10))])
        self.assertEqual(found.standing, [])

    def test_a_plan_feeds_a_stale_flow_across_a_section_boundary(self):
        """A stale flow over a hole, between two sources, on a floor whose
        top is a section's last row: the plan fills the hole at y=63, the
        flow at y=64 -- the next section's first row -- stops falling, is
        re-read as the source its two neighbors make it, and spreads to
        both open sides. The flows a plan may re-read are found section by
        section, and one across the boundary from the plan's cell is still
        one of them."""
        self.save.fill((5, 62, 5), (15, 63, 15), "minecraft:stone")
        self.save.set_block(10, 63, 10, "minecraft:air")
        self.save.set_block(10, 64, 9, SOURCE)
        self.save.set_block(10, 64, 11, SOURCE)
        self.save.set_block(10, 64, 10, "minecraft:water[level=7]")
        found = contained(self.save, {(10, 63, 10): "minecraft:stone"})
        self.assertEqual(sorted((leak.cell, leak.kind, leak.source)
                                for leak in found.leaks),
                         [((9, 64, 10), "spreads", (10, 64, 10)),
                          ((11, 64, 10), "spreads", (10, 64, 10))])

    def test_a_spout_may_run_only_where_it_is_allowed(self):
        """A fountain: a source on a pillar over a basin. ``spill=`` names
        the cells it may run into; it is carried on through them, falls and
        all, and the first cell past them is the leak."""
        self.save.fill((18, 61, 18), (22, 61, 22), "minecraft:air")
        self.save.fill((20, 61, 20), (20, 62, 20), "minecraft:stone")
        self.save.fill((17, 62, 17), (23, 63, 23), "minecraft:stone")
        self.save.fill((18, 62, 18), (22, 63, 22), "minecraft:air")
        self.save.set_block(20, 62, 20, "minecraft:stone")
        plan = {(20, 63, 20): SOURCE}
        self.assertEqual(len(contained(self.save, plan).leaks), 4)
        basin = {(x, y, z) for x in range(18, 23) for z in range(18, 23)
                 for y in (61, 62, 63)} - {(20, 61, 20), (20, 62, 20), (20, 63, 20)}
        self.assertTrue(contained(self.save, plan, spill=basin).held)
        above = {cell for cell in basin if cell[1] >= 62}
        self.assertEqual(self.leaks(plan, spill=above),
                         [((19, 61, 20), "falls"), ((20, 61, 19), "falls"),
                          ((20, 61, 21), "falls"), ((21, 61, 20), "falls")])

    def test_an_unknown_block_or_an_ungenerated_chunk_holds_no_water(self):
        """An id the game does not have is air to it, whatever it says it
        is; and nothing can be said where the world was never generated."""
        found = contained(self.save, {**self.trench(SOURCE), (11, 62, 10): "mymod:tank"})
        self.assertEqual([(leak.cell, leak.kind) for leak in found.leaks],
                         [((11, 62, 10), "spreads")])
        self.assertIn("loads as air", found.leaks[0].reason)
        self.save.fill((30, 62, 9), (31, 62, 11), "minecraft:stone")
        found = contained(self.save, {(31, 62, 10): SOURCE})
        self.assertEqual([(leak.cell, leak.kind) for leak in found.leaks],
                         [((32, 62, 10), "ungenerated")])
        self.assertIsNone(found.leaks[0].block)
        self.assertIn("never generated", found.leaks[0].reason)

    def test_a_plan_in_ground_that_is_not_there_is_refused(self):
        with self.assertRaises(Refused) as caught:
            contained(self.save, {(40, 62, 10): SOURCE, (40, 62, 40): SOURCE})
        self.assertIn("never generated", str(caught.exception))
        self.assertEqual(len(caught.exception.faults), 2)

    def test_a_plan_past_the_world_is_refused(self):
        """Water can be neither written nor followed past the world's top or
        floor, so a plan cell there is refused, never passed over."""
        with self.assertRaises(Refused) as caught:
            contained(self.save, {(5, 330, 5): SOURCE, (5, -70, 5): SOURCE,
                                  (5, 62, 5): SOURCE})
        self.assertIn("past the world's -64..319", str(caught.exception))
        self.assertIn("(5, -70, 5)", str(caught.exception))
        self.assertEqual(len(caught.exception.faults), 1)


class StarvedTests(unittest.TestCase):
    """Water that loses its feed, by the same ``getNewLiquid`` amounts
    :func:`contained` reads: what a fill across a stream leaves to dry."""

    def setUp(self):
        self.save = _water_world(self)
        self.save.fill((0, 62, 0), (31, 63, 31), "minecraft:stone")

    def stream(self):
        """A one-wide stream at y=63: a source at x=2, levels 1..7 east."""
        self.save.set_block(2, 63, 4, SOURCE)
        for level in range(1, 8):
            self.save.set_block(2 + level, 63, 4, f"minecraft:water[level={level}]")

    def test_a_stream_cut_off_from_its_source_starves(self):
        self.stream()
        self.assertEqual(starved(self.save, [(2, 63, 4)]),
                         [(x, 63, 4) for x in range(3, 10)])
        self.assertEqual(starved(self.save, [(9, 63, 4)]), [],
                         "the end of a stream feeds nothing")
        self.assertEqual(starved(self.save, [(5, 63, 4)]),
                         [(x, 63, 4) for x in range(6, 10)])
        self.assertEqual(starved(self.save, [(2, 63, 4)],
                                 overlay={(3, 64, 4): SOURCE}),
                         [], "a source poured over the stream feeds it")

    def test_falling_water_feeds_at_8_whatever_its_level(self):
        """A flow of level 1 (amount 7) beside a column of falling water
        keeps its amount when its source is cut away: the falling water
        counts 8 at every level 8..15, not 8 less its level past 8."""
        self.save.fill((4, 64, 4), (6, 70, 6), "minecraft:stone")
        self.save.set_block(4, 63, 5, SOURCE)
        self.save.set_block(5, 63, 5, "minecraft:water[level=1]")
        self.save.set_block(6, 64, 5, SOURCE)
        for level in range(8, 16):
            with self.subTest(level=level):
                self.save.set_block(6, 63, 5, f"minecraft:water[level={level}]")
                self.assertEqual(starved(self.save, [(4, 63, 5)]), [])
        self.save.set_block(6, 64, 5, "minecraft:stone")
        self.save.set_block(6, 63, 5, "minecraft:water[level=1]")
        self.assertIn((5, 63, 5), starved(self.save, [(4, 63, 5)]),
                      "a flow of 7 beside it feeds it only 6")

    def test_a_feeder_behind_a_wall_feeds_nothing(self):
        """``getNewLiquid`` counts a neighbor only through the wall between
        them: a waterlogged stair turning its whole back to the stream, or a
        waterlogged bottom slab over it, whose underside is whole, feeds it
        nothing, so the stream starves when its source goes. The stair
        turned round, or a top slab over it, does feed it -- a source beside
        and falling water above -- and nothing starves."""
        self.stream()
        cut = [(x, 63, 4) for x in range(3, 10)]
        for cell, block, starves in (
                ((3, 63, 5), "minecraft:oak_stairs[facing=north,waterlogged=true]", cut),
                ((3, 63, 5), "minecraft:oak_stairs[facing=south,waterlogged=true]", []),
                ((3, 64, 4), "minecraft:oak_slab[type=bottom,waterlogged=true]", cut),
                ((3, 64, 4), "minecraft:oak_slab[type=top,waterlogged=true]", [])):
            with self.subTest(block=block):
                self.assertEqual(starved(self.save, [(2, 63, 4)],
                                         overlay={cell: block}), starves)

    def test_a_falling_column_starves_down_to_its_next_feed(self):
        self.save.fill((4, 62, 4), (6, 71, 6), "minecraft:air")
        self.save.fill((4, 61, 4), (6, 61, 6), "minecraft:stone")
        self.save.set_block(5, 71, 5, SOURCE)
        for y in range(62, 71):
            self.save.set_block(5, y, 5, "minecraft:water[level=8]")
        self.save.set_block(6, 65, 5, SOURCE)
        self.assertEqual(starved(self.save, [(6, 65, 5)]), [],
                         "falling water beside a fill is fed from above")
        self.assertEqual(starved(self.save, [(5, 71, 5)]),
                         [(5, y, 5) for y in range(65, 71)])

    def test_a_source_is_never_starved(self):
        self.save.fill((2, 63, 2), (8, 63, 2), SOURCE)
        self.assertEqual(starved(self.save, [(5, 63, 2)]), [])
