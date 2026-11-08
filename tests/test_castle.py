"""Tests for examples/castle.py.

Three layers, matching how the example is built: the plan (pure geometry --
if the proportions are wrong every stone lands wrong), the shapes (circles
and battlements that must be symmetric and connected), and the build itself,
run against the vendored fixture and read back from disk.

The heightmap refresh gets its own scrutiny: the fixture is genuine game
output, so recomputing a chunk's maps from its blocks and comparing against
what the game stored is an oracle test of the whole classifier. A castle
that leaves heightmaps stale -- or rewrites them wrongly -- sends mobs and
maps to a surface that does not exist.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import random
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "examples"))

from castle import (  # noqa: E402
    ABOVE_KEEP,
    AIR,
    DUNGEON_AIR,
    DUNGEON_FLOOR,
    Castle,
    CastlePlan,
    Mason,
    disc_offsets,
    facing,
    main,
    merlon_offsets,
    ring_offsets,
    survey_ground,
    weathered,
)
from fixtures import FIXTURE_LEVEL_DAT, FIXTURE_REGION  # noqa: E402
from heaton_voxel.blocks import Blocks, unpack_indices  # noqa: E402
from heaton_voxel.nbt import NBT  # noqa: E402
from heaton_voxel.world import OVERWORLD, MinecraftSave  # noqa: E402

# The fixture's one 2x2 patch of fully generated chunks: world chunks
# (-25..-24, 1..2), so blocks x -400..-369, z 16..47. A 32-wide castle
# placed exactly there builds entirely on real terrain.
SITE_X0, SITE_Z0 = -400, 16
SITE_CENTER = (-384, 32)
SITE_GROUND = 62  # the fixture is ocean here; the median surface is the sea


def make_world(tmpdir: str) -> str:
    """A throwaway save directory holding a copy of the fixture."""
    world = os.path.join(tmpdir, "world")
    region_dir = os.path.join(world, "dimensions/minecraft/overworld/region")
    os.makedirs(region_dir)
    shutil.copyfile(
        FIXTURE_REGION, os.path.join(region_dir, os.path.basename(FIXTURE_REGION))
    )
    shutil.copyfile(FIXTURE_LEVEL_DAT, os.path.join(world, "level.dat"))
    return world


class PlanTests(unittest.TestCase):
    """The proportions, across the whole supported range of sizes."""

    def all_plans(self):
        for width in (24, 32, 36, 44, 48, 64, 96, 128, 200, 256):
            for height in (20, 24, 44, 80, 160):
                with self.subTest(width=width, height=height):
                    yield CastlePlan(width, height)

    def test_everything_fits_inside_the_footprint(self):
        """The tower crowns overhang their shells by one block, so the wall
        inset must leave room -- a crown outside the footprint once leaked
        into a neighboring, possibly ungenerated, chunk."""
        for plan in self.all_plans():
            self.assertGreaterEqual(plan.inset, plan.pad + plan.tower_r + 1)

    def test_the_keep_leaves_a_courtyard(self):
        for plan in self.all_plans():
            inner = plan.width - 2 * (plan.inset + plan.wall_t)
            self.assertGreaterEqual(
                inner - plan.keep_size, 4, "no room to walk around the keep"
            )

    def test_the_keep_is_odd_so_the_flag_centers(self):
        for plan in self.all_plans():
            self.assertEqual(plan.keep_size % 2, 1)

    def test_heights_are_ordered(self):
        """Walls below towers below keep: the silhouette of a castle."""
        for plan in self.all_plans():
            self.assertLess(plan.wall_h, plan.tower_h)
            self.assertLessEqual(plan.tower_h, plan.keep_h - 2)
            self.assertEqual(plan.keep_h, plan.height)

    def test_the_gate_fits_in_the_south_wall(self):
        for plan in self.all_plans():
            span = plan.width - 2 * plan.inset - 2 * plan.tower_r - 2
            self.assertLess(plan.gate_w + 2, span, "gate wider than the wall")
            self.assertLess(plan.gate_h, plan.wall_h - 2, "gate taller than the wall")

    def test_floor_levels_climb_and_leave_headroom(self):
        for plan in self.all_plans():
            levels = plan.floor_levels()
            self.assertEqual(levels[0], 0)
            self.assertEqual(levels, sorted(set(levels)))
            self.assertLessEqual(levels[-1], plan.keep_h - 5)

    def test_a_great_hall_takes_two_stories(self):
        plan = CastlePlan(64, 44)
        self.assertTrue(plan.great_hall)
        levels = plan.floor_levels()
        self.assertEqual(levels[1], 12, "the hall should span two stories")

    def test_moat_by_width(self):
        self.assertFalse(CastlePlan(43, 30).moat)
        self.assertTrue(CastlePlan(44, 30).moat)
        self.assertTrue(CastlePlan(40, 30, moat=True).moat)
        self.assertFalse(CastlePlan(200, 30, moat=False).moat)

    def test_the_dungeon_scales_with_the_keep(self):
        """On by default; the way down and the rooms follow the hall size."""
        for plan in self.all_plans():
            self.assertTrue(plan.dungeon)
            hall = plan.keep_size - 2 * plan.keep_t
            self.assertEqual(plan.dungeon_stairs, hall >= DUNGEON_FLOOR + 2)
            self.assertEqual(plan.dungeon_rooms, hall >= 15)
        off = CastlePlan(64, 44, dungeon=False)
        self.assertFalse(off.dungeon)
        self.assertFalse(off.dungeon_stairs)
        self.assertFalse(off.dungeon_rooms)
        self.assertNotIn("dungeon", " ".join(off.describe()))
        self.assertIn("dungeon", " ".join(CastlePlan(64, 44).describe()))

    def test_the_stair_flight_fits_inside_the_keep(self):
        """One step per block of drop, plus the opening and a landing."""
        for plan in self.all_plans():
            if plan.dungeon_stairs:
                hall = plan.keep_size - 2 * plan.keep_t
                self.assertGreaterEqual(hall, DUNGEON_FLOOR + 2)

    def test_refusals(self):
        for width, height, moat in (
            (23, 30, None),  # too narrow
            (257, 30, None),  # a town
            (64, 19, None),  # too squat
            (64, 161, None),  # would breach the sky in any world
            (34, 30, True),  # moat with no room for it
        ):
            with self.subTest(width=width, height=height, moat=moat):
                with self.assertRaises(ValueError):
                    CastlePlan(width, height, moat=moat)


class ShapeTests(unittest.TestCase):
    def test_discs_are_symmetric_every_way(self):
        for r in range(1, 8):
            cells = set(disc_offsets(r))
            for dx, dz in cells:
                for sx, sz in ((-dx, dz), (dx, -dz), (dz, dx)):
                    self.assertIn((sx, sz), cells, f"r={r} asymmetric")

    def test_discs_reach_their_radius_and_no_further(self):
        for r in range(1, 8):
            cells = set(disc_offsets(r))
            for point in ((r, 0), (-r, 0), (0, r), (0, -r)):
                self.assertIn(point, cells)
            self.assertNotIn((r + 1, 0), cells)

    def test_rings_are_the_rim_and_nothing_else(self):
        for r in range(1, 8):
            disc, inner = set(disc_offsets(r)), set(disc_offsets(r - 1))
            self.assertEqual(set(ring_offsets(r)), disc - inner)

    def test_rings_are_connected(self):
        """A tower shell with a gap is a hole in the castle wall."""
        for r in range(1, 8):
            ring = ring_offsets(r)
            for dx, dz in ring:
                neighbors = sum(
                    1
                    for ox, oz in ring
                    if (ox, oz) != (dx, dz) and abs(ox - dx) <= 1 and abs(oz - dz) <= 1
                )
                self.assertGreaterEqual(neighbors, 2, f"gap in ring r={r}")

    def test_rings_are_ordered_by_angle(self):
        """Alternating merlons only alternate if neighbors are adjacent."""
        import math

        for r in range(2, 8):
            ring = ring_offsets(r)
            angles = [math.atan2(dz, dx) for dx, dz in ring]
            self.assertEqual(angles, sorted(angles))

    def test_merlons_cap_both_ends_and_never_gap_wide(self):
        for length in range(1, 30):
            teeth = merlon_offsets(length)
            self.assertEqual(teeth[0], 0)
            self.assertEqual(teeth[-1], length - 1)
            for a, b in zip(teeth, teeth[1:]):
                self.assertLessEqual(b - a, 2, f"length={length}")

    def test_facing_picks_the_dominant_axis(self):
        self.assertEqual(facing(0, -1), "north")
        self.assertEqual(facing(0, 1), "south")
        self.assertEqual(facing(-1, 0), "west")
        self.assertEqual(facing(1, 0), "east")
        self.assertEqual(facing(5, 2), "east")
        self.assertEqual(facing(-2, -5), "north")

    def test_facing_breaks_ties_toward_z(self):
        """Pinned: ring corners must agree with their z-edges either way."""
        self.assertEqual(facing(3, 3), "south")
        self.assertEqual(facing(-3, -3), "north")

    def test_weathering_is_deterministic_and_mostly_plain(self):
        rolls = [weathered(random.Random(7)) for _ in range(5)]
        self.assertEqual(len(set(rolls)), 1, "same seed, same stone")
        rng = random.Random(7)
        stones = [weathered(rng) for _ in range(2000)]
        self.assertGreater(
            stones.count("minecraft:stone_bricks") / len(stones),
            0.6,
            "weathering should accent the wall, not consume it",
        )
        self.assertEqual(
            set(stones)
            - {
                "minecraft:stone_bricks",
                "minecraft:mossy_stone_bricks",
                "minecraft:cracked_stone_bricks",
                "minecraft:cobblestone",
            },
            set(),
        )


class HeightmapRefreshTests(unittest.TestCase):
    """The refresh against the game's own answers, chunk by chunk.

    The fixture's heightmaps are genuine game output. Recomputing every
    column of every fully generated chunk and demanding byte-for-byte
    agreement pins the whole classifier: fluids, kelp (which counts as its
    own water), leaf litter, and the fact that OCEAN_FLOOR *does* stop at
    leaves. When a new world surfaces a block this gets wrong, this test is
    where the disagreement shows up.
    """

    @classmethod
    def setUpClass(cls):
        cls.region = NBT.load(FIXTURE_REGION, eager=True)

    def refreshed(self, chunk, accessor, chunk_x, chunk_z):
        class OneChunk:
            def blocks(self, *_):
                return accessor

            def chunk(self, *_):
                return chunk

        mason = Mason.__new__(Mason)
        mason.dim = OneChunk()
        mason.columns = {
            (chunk_x * 16 + lx, chunk_z * 16 + lz)
            for lx in range(16)
            for lz in range(16)
        }
        return mason._refresh_heightmaps(accessor, chunk_x, chunk_z)

    def test_recomputing_full_chunks_changes_nothing(self):
        compared = 0
        for (local_x, local_z), chunk in sorted(self.region.chunks.items()):
            data = chunk.data
            if data is None or str(data.get("Status")) != "minecraft:full":
                continue
            accessor = Blocks(chunk)
            maps = data["Heightmaps"]
            bits = max(1, (accessor.max_y - accessor.min_y + 1).bit_length())
            before = {
                str(name): unpack_indices(maps[name], 256, bits) for name in maps
            }
            self.assertTrue(
                self.refreshed(chunk, accessor, local_x - 32, local_z),
                f"chunk ({local_x - 32}, {local_z}) refused a refresh",
            )
            for name, values in before.items():
                after = unpack_indices(maps[name], 256, bits)
                for i, (theirs, ours) in enumerate(zip(values, after)):
                    if theirs != ours:
                        self.fail(
                            f"chunk ({local_x - 32}, {local_z}) {name} column "
                            f"{i & 15},{i >> 4}: game says {theirs}, we say {ours}"
                        )
                compared += 256
        self.assertGreaterEqual(compared, 10 * 4 * 256, "the oracle went missing")

    def test_a_chunk_without_heightmaps_is_left_alone(self):
        """No heightmaps yet means none are invented.

        Every chunk in the fixture happens to carry maps, even the
        partly-generated ones, so the absent case is made by removing them
        from a private copy -- which is exactly the state of a chunk stopped
        before the surface stage in a live world.
        """
        region = NBT.load(FIXTURE_REGION)  # a fresh copy; do not disturb setUpClass
        (local_x, local_z), chunk = next(iter(sorted(region.chunks.items())))
        chunk.data.pop("Heightmaps")
        accessor = Blocks(chunk)
        self.assertFalse(self.refreshed(chunk, accessor, local_x - 32, local_z))
        self.assertNotIn("Heightmaps", chunk.data)


def build_site(world: str, width=32, height=24, x0=SITE_X0, z0=SITE_Z0, **plan_args):
    """Build the standard test castle; returns (save, castle, report)."""
    save = MinecraftSave.load(world)
    dim = save.dimension(OVERWORLD)
    plan = CastlePlan(width, height, **plan_args)
    mason = Mason(save, dim)
    castle = Castle(mason, plan, x0, z0, SITE_GROUND, random.Random(1))
    castle.build()
    mason.finish()
    return save, castle, mason.report


class BuildTests(unittest.TestCase):
    """One castle, built over the fixture's real terrain and read back.

    Built once for the class: the assertions are all reads, and one build
    exercises every stage. Positions come from the same Castle object, so a
    change to the layout moves the assertions with it.
    """

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="castle-")
        cls.world = make_world(cls.dir)
        save, castle, report = build_site(cls.world)
        cls.report = report
        save.save()
        # Everything is asserted against a *fresh* load: what matters is
        # what landed on disk, not what the builder thinks it did.
        cls.save = MinecraftSave.load(cls.world)
        cls.castle = castle
        cls.plan = castle.p
        cls.g = SITE_GROUND

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def block(self, x, y, z):
        state = self.save.get_block(x, y, z, dimension=OVERWORLD)
        self.assertIsNotNone(state, f"({x}, {y}, {z}) is unexpectedly ungenerated")
        return state.name

    def assertMasonry(self, x, y, z):
        self.assertIn(
            self.block(x, y, z),
            {
                "minecraft:stone_bricks",
                "minecraft:mossy_stone_bricks",
                "minecraft:cracked_stone_bricks",
                "minecraft:cobblestone",
                "minecraft:chiseled_stone_bricks",
            },
            f"({x}, {y}, {z}) is not castle masonry",
        )

    def test_the_castle_itself_is_complete(self):
        """The skirt legitimately runs off this tiny fixture map, and says
        so; the castle proper -- everything inside the footprint -- must be
        whole, with nothing unreadable and nothing out of range."""
        footprint_chunks = {(cx, cz) for cx in (-25, -24) for cz in (1, 2)}
        self.assertFalse(self.report.missing_chunks & footprint_chunks)
        self.assertEqual(self.report.unreadable_chunks, {})
        self.assertEqual(self.report.blocks_out_of_range, 0)
        self.assertGreater(self.report.blocks_placed, 10_000)

    def test_the_foundation_reaches_the_seabed(self):
        """The fixture site is open ocean: without the terrain-following
        fill the castle would stand on an 8-block slab floating in water."""
        x, z = SITE_X0 + 2, SITE_Z0 + 16
        region = NBT.load(FIXTURE_REGION)
        chunk = region.chunks[(x >> 4 & 31, z >> 4)]
        accessor = Blocks(chunk)
        values = unpack_indices(chunk.data["Heightmaps"]["OCEAN_FLOOR"], 256, 9)
        seabed = accessor.min_y + values[(z & 15) * 16 + (x & 15)] - 1
        self.assertLess(seabed, self.g - 8, "the site stopped being ocean")
        for y in range(seabed + 1, self.g + 1):
            self.assertNotIn(
                self.block(x, y, z),
                {AIR, "minecraft:water"},
                f"the foundation floats at y={y}",
            )

    def test_the_skirt_tapers_down_to_the_terrain(self):
        """East of the footprint the ground steps down one block per ring,
        each column solid from the seabed up, the sea lapping above it."""
        z = SITE_Z0 + 8
        for ring in (1, 2, 6):
            x = (SITE_X0 + 31) + ring
            target = self.g - ring
            self.assertEqual(
                self.block(x, target, z), "minecraft:grass_block", f"ring {ring}"
            )
            self.assertEqual(self.block(x, target - 1, z), "minecraft:dirt")
            self.assertEqual(self.block(x, target + 1, z), "minecraft:water")
            for y in range(46, target + 1):
                self.assertNotIn(
                    self.block(x, y, z),
                    {AIR, "minecraft:water"},
                    f"ring {ring} floats at y={y}",
                )

    def test_touched_chunks_are_marked_for_relight(self):
        """The fixture stores isLightOn=1 with baked light arrays; after the
        build every affected chunk, neighbors included, must have the flag
        cleared and the arrays gone, or torches light only themselves."""
        dim = self.save.dimension(OVERWORLD)
        marked = [(-25, 1), (-25, 2), (-24, 1), (-24, 2), (-23, 1)]
        for key in marked:
            chunk = dim.chunk(*key)
            self.assertIsNotNone(chunk, key)
            self.assertEqual(int(chunk.data["isLightOn"]), 0, key)
            for section in chunk.data["sections"]:
                self.assertNotIn("BlockLight", section, key)
                self.assertNotIn("SkyLight", section, key)

    def test_the_gate_is_an_opening_with_a_raised_portcullis(self):
        c = self.castle
        gate_x = (c.gate_x0 + c.gate_x1) // 2
        self.assertEqual(self.block(gate_x, self.g + 1, c.wz1), AIR)
        self.assertEqual(self.block(gate_x, self.g + 1, c.wz1 - 1), AIR)
        self.assertEqual(
            self.block(gate_x, self.g + self.plan.gate_h - 1, c.wz1 - 1),
            "minecraft:iron_bars",
        )
        self.assertEqual(self.block(gate_x, self.g, c.wz1), "minecraft:cobblestone")

    def test_the_curtain_walls_stand_with_battlements(self):
        c = self.castle
        mid_x = (c.wx0 + c.wx1) // 2
        mid_z = (c.wz0 + c.wz1) // 2
        for x, z in ((mid_x, c.wz0), (c.wx0, mid_z), (c.wx1, mid_z)):
            self.assertMasonry(x, self.g + 2, z)
            self.assertMasonry(x, c.walk + 1, z)  # parapet, all four sides

    def test_the_wall_walk_is_clear_to_patrol(self):
        """The walkway behind the parapet, and the passages through the
        towers, must be open air the whole way round."""
        c = self.castle
        t = self.plan.wall_t
        mid_x = (c.wx0 + c.wx1) // 2
        mid_z = (c.wz0 + c.wz1) // 2
        spots = [
            (mid_x, c.wz0 + t - 1),  # north wall, inner walkway course
            (mid_x, c.wz1 - t + 1),  # south
            (c.wx0 + t - 1, mid_z),  # west
            (c.wx1 - t + 1, mid_z),  # east
            (c.wx0, c.wz0 + 2),  # inside the NW tower doorway
        ]
        for x, z in spots:
            for dy in (1, 2):
                self.assertEqual(
                    self.block(x, c.walk + dy, z), AIR, f"walkway blocked at ({x}, {z})"
                )

    def test_the_towers_are_round_hollow_and_laddered(self):
        c = self.castle
        r = self.plan.tower_r
        for cx, cz in c.towers:
            self.assertMasonry(cx + r, self.g + 2, cz)
            self.assertMasonry(cx - r, self.g + 2, cz)
            self.assertEqual(self.block(cx + 1, self.g + 3, cz), AIR)
            inward = 1 if cz == c.wz0 else -1
            self.assertEqual(
                self.block(cx, self.g + 2, cz + inward * (r - 1)),
                "minecraft:ladder",
            )
            self.assertMasonry(cx + r + 1, self.g + self.plan.tower_h + 2, cz)  # crown

    def test_the_keep_holds_floors_windows_and_the_flag(self):
        c = self.castle
        cx = (c.kx0 + c.kx1) // 2
        cz = (c.kz0 + c.kz1) // 2
        self.assertMasonry(c.kx0 + 1, self.g + 2, c.kz0)
        self.assertEqual(self.block(c.kx0, self.g + 2, c.kz0), "minecraft:chiseled_stone_bricks")
        self.assertEqual(self.block(cx, self.g + 3, cz), AIR)
        first_floor = self.g + self.plan.floor_levels()[1]
        self.assertEqual(self.block(cx - 2, first_floor, cz), "minecraft:spruce_planks")
        pole_top = c.keep_top + ABOVE_KEEP - 1
        self.assertEqual(self.block(cx, pole_top, cz), "minecraft:spruce_fence")
        self.assertEqual(self.block(cx + 1, pole_top, cz), "minecraft:red_wool")

    def test_the_courtyard_leads_to_the_keep(self):
        c = self.castle
        gate_x = (c.gate_x0 + c.gate_x1) // 2
        self.assertEqual(self.block(gate_x, self.g, c.kz1 + 3), "minecraft:dirt_path")
        self.assertEqual(
            self.block(gate_x, self.g + 1, c.kz1 + 1), "minecraft:stone_brick_stairs"
        )

    def test_no_moat_at_this_width(self):
        """32 wide has no room for water; the lawn runs to the edge.

        Probed mid-edge: the corners belong to the tower footings.
        """
        self.assertFalse(self.plan.moat)
        self.assertEqual(
            self.block(SITE_X0, self.g, SITE_Z0 + 16), "minecraft:grass_block"
        )

    def test_the_dungeon_is_a_sealed_lit_room_with_a_ladder(self):
        """This keep is too small for the grand stair, so a ladder shaft
        drops from the hall's north-west corner into the undercroft."""
        c = self.castle
        t = self.plan.keep_t
        ix0, iz0 = c.kx0 + t, c.kz0 + t
        cx = (c.kx0 + c.kx1) // 2
        cz = (c.kz0 + c.kz1) // 2
        slab = self.g - DUNGEON_FLOOR
        self.assertFalse(self.plan.dungeon_stairs)
        # Hollow, floored, and roofed.
        self.assertEqual(self.block(cx, slab + 2, cz), AIR)
        self.assertIn(
            self.block(cx, slab, cz),
            {"minecraft:stone_bricks", "minecraft:mossy_stone_bricks",
             "minecraft:cracked_stone_bricks", "minecraft:cobblestone"},
        )
        self.assertEqual(self.block(cx, slab + DUNGEON_AIR + 1, cz), "minecraft:stone_bricks")
        # Sealed at the shell, one block outside the room.
        self.assertMasonry(ix0 - 1, slab + 2, cz)
        # The ladder runs from the hall floor to the dungeon floor.
        for y in (self.g, self.g - 4, slab + 1):
            self.assertEqual(self.block(ix0, y, iz0), "minecraft:ladder")
        # Lit: the chandelier hangs mid-hall.
        self.assertEqual(self.block(cx, slab + DUNGEON_AIR - 1, cz), "minecraft:lantern")
        self.assertEqual(self.block(cx, slab + DUNGEON_AIR, cz), "minecraft:iron_chain")

    def test_heightmaps_follow_the_new_skyline(self):
        """WORLD_SURFACE over the keep is the flagpole; MOTION_BLOCKING over
        the courtyard is the lawn. Stale maps would still show the sea."""
        c = self.castle
        cx = (c.kx0 + c.kx1) // 2
        cz = (c.kz0 + c.kz1) // 2
        dim = self.save.dimension(OVERWORLD)
        chunk = dim.chunk(cx >> 4, cz >> 4)
        accessor = dim.blocks(cx >> 4, cz >> 4)
        maps = chunk.data["Heightmaps"]
        bits = max(1, (accessor.max_y - accessor.min_y + 1).bit_length())

        def surface(name, x, z):
            values = unpack_indices(maps[name], 256, bits)
            return accessor.min_y + values[(z & 15) * 16 + (x & 15)] - 1

        self.assertEqual(surface("WORLD_SURFACE", cx, cz), c.keep_top + ABOVE_KEEP - 1)
        lawn_x, lawn_z = c.kx1 + 2, c.kz0
        if (lawn_x >> 4, lawn_z >> 4) == (cx >> 4, cz >> 4):
            self.assertEqual(surface("MOTION_BLOCKING", lawn_x, lawn_z), self.g)

    def test_the_written_region_still_round_trips(self):
        """The core promise holds for a file this example has rewritten."""
        path = os.path.join(
            self.world, "dimensions/minecraft/overworld/region", "r.-1.0.mca"
        )
        with open(path, "rb") as handle:
            on_disk = handle.read()
        self.assertEqual(NBT.load(path, eager=True).to_bytes(), on_disk)


class PartialSiteTests(unittest.TestCase):
    """A castle overlapping the edge of the generated world must say so."""

    def test_missing_chunks_are_reported_not_invented(self):
        with tempfile.TemporaryDirectory(prefix="castle-") as tmpdir:
            world = make_world(tmpdir)
            save = MinecraftSave.load(world)
            plan = CastlePlan(32, 24)
            mason = Mason(save, save.dimension(OVERWORLD))
            # 16 blocks east of the site: the east half hangs off the map.
            castle = Castle(
                mason, plan, SITE_X0 + 16, SITE_Z0, SITE_GROUND, random.Random(1)
            )
            castle.build()
            mason.finish()
            report = mason.report
            self.assertFalse(report.complete)
            # Chunk (-23, 1) exists in the fixture; (-23, 2) does not. The
            # skirt reaches further chunks still -- also missing, also
            # reported -- so the set is at least this, never exactly it.
            self.assertIn((-23, 2), report.missing_chunks)
            self.assertGreater(report.blocks_placed, 1000, "the rest should stand")
            self.assertEqual(report.unreadable_chunks, {})


class DungeonAtScaleTests(unittest.TestCase):
    """The full dungeon -- stair, cells, treasury -- under a 64-wide keep.

    The fixture cannot hold a whole 64-wide castle, but placed so the keep
    sits over the generated 2x2 patch, the dungeon beneath it is entirely
    real; the walls falling off the map are the incomplete-report case and
    are already covered elsewhere. Asserted by census rather than by exact
    coordinates, so redecorating the dungeon does not break the test.
    """

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="castle-")
        world = make_world(cls.dir)
        # Keep (25 wide) spans x -397..-373, z 19..43: inside the real chunks.
        save, castle, report = build_site(world, width=64, height=44, x0=-416, z0=0)
        save.save()
        cls.save = MinecraftSave.load(world)
        cls.castle = castle
        cls.report = report

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def census(self):
        c = self.castle
        t = c.p.keep_t
        counts: dict[str, int] = {}
        slab = SITE_GROUND - DUNGEON_FLOOR
        for x in range(c.kx0 + t, c.kx1 - t + 1):
            for z in range(c.kz0 + t, c.kz1 - t + 1):
                for y in range(slab, slab + DUNGEON_AIR + 1):
                    state = self.save.get_block(x, y, z, dimension=OVERWORLD)
                    self.assertIsNotNone(state, f"({x}, {y}, {z}) missing")
                    counts[state.name] = counts.get(state.name, 0) + 1
        return counts

    def test_the_dungeon_furniture_is_all_there(self):
        self.assertTrue(self.castle.p.dungeon_stairs)
        self.assertTrue(self.castle.p.dungeon_rooms)
        counts = self.census()
        self.assertEqual(counts.get("minecraft:gold_block"), 4, "the treasury hoard")
        self.assertGreater(counts.get("minecraft:iron_bars", 0), 20, "cells and cage")
        self.assertGreaterEqual(counts.get("minecraft:lantern", 0), 4, "chandeliers")
        self.assertGreater(counts.get("minecraft:wall_torch", 0), 5, "wall light")
        self.assertGreater(
            counts.get("minecraft:stone_brick_stairs", 0), 20,
            "the flight down and the pillar capitals",
        )
        self.assertGreater(
            counts.get("minecraft:chiseled_stone_bricks", 0), 8,
            "pillar bases and capitals",
        )
        self.assertNotIn("minecraft:ladder", counts, "stairs, not a ladder, at scale")
        self.assertGreater(counts.get("minecraft:air", 0), 1500, "room to walk")

    def test_the_flight_actually_connects_hall_to_floor(self):
        """Walk the stair line: every step is a stair with headroom."""
        c = self.castle
        t = c.p.keep_t
        ix0, iz0 = c.kx0 + t, c.kz0 + t
        for step in range(1, DUNGEON_FLOOR):
            x, y, z = ix0, SITE_GROUND - step, iz0 + step
            state = self.save.get_block(x, y, z, dimension=OVERWORLD)
            self.assertEqual(state.name, "minecraft:stone_brick_stairs", f"step {step}")
            for dy in (1, 2, 3):
                above = self.save.get_block(x, y + dy, z, dimension=OVERWORLD)
                self.assertIn(
                    above.name,
                    {AIR, "minecraft:wall_torch"},
                    f"no headroom over step {step}",
                )

    def test_every_floor_of_the_keep_walks_up_and_back_down(self):
        """The keep's flights, walked the way the player walks them.

        With every flight in one pair of columns, each run's treads sat in
        the head room of the run below; with the floor above holed two rows
        deep instead of three, the top of every flight could be climbed and
        not come back down. A census of stair blocks sees neither.
        """
        from heaton_voxel.craft.survey import reachable

        c = self.castle
        t, g = c.p.keep_t, c.ground
        ix0, iz0, ix1, iz1 = c.kx0 + t, c.kz0 + t, c.kx1 - t, c.kz1 - t
        levels = c.p.floor_levels()
        walk = reachable(self.save, (ix1, g, iz1), (ix0, g - 1, iz0),
                         (ix1, g + levels[-1] + 3, iz1), dimension=OVERWORLD)
        for level in levels[1:]:
            on_floor = {cell for cell in walk.reached if cell[1] == g + level}
            self.assertTrue(on_floor, f"nobody reaches the floor at +{level}")
        self.assertEqual(sorted(walk.stranded)[:5], [],
                         "a floor you can reach and not get home from")

    def test_no_dungeon_means_solid_ground(self):
        with tempfile.TemporaryDirectory(prefix="castle-") as tmpdir:
            world = make_world(tmpdir)
            save, castle, _ = build_site(world, dungeon=False)
            cx = (castle.kx0 + castle.kx1) // 2
            cz = (castle.kz0 + castle.kz1) // 2
            for y in range(SITE_GROUND - DUNGEON_FLOOR, SITE_GROUND - 3):
                state = save.get_block(cx, y, cz, dimension=OVERWORLD)
                self.assertNotEqual(state.name, AIR, f"hole at y={y}")


class SurveyTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="castle-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.save = MinecraftSave.load(make_world(self.dir))
        self.dim = self.save.dimension(OVERWORLD)

    def test_finds_the_sea_surface_over_the_fixture_ocean(self):
        ground, sampled, missing = survey_ground(self.dim, SITE_X0, SITE_Z0, 32)
        self.assertEqual(missing, 0)
        self.assertEqual(ground, SITE_GROUND)
        self.assertGreater(sampled, 8)

    def test_reports_missing_ground_instead_of_guessing(self):
        ground, sampled, missing = survey_ground(self.dim, 10_000, 10_000, 32)
        self.assertIsNone(ground)
        self.assertEqual(missing, sampled)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="castle-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.world = make_world(self.dir)
        self.region = os.path.join(
            self.world, "dimensions/minecraft/overworld/region", "r.-1.0.mca"
        )

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def digest(self):
        with open(self.region, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def test_dry_run_touches_nothing(self):
        before = self.digest()
        code, out, err = self.run_main(
            self.world, "--at", *map(str, SITE_CENTER), "--width", "32",
            "--height", "24", "--dry-run",
        )
        self.assertEqual(code, 0, err)
        self.assertIn("dry run", out)
        self.assertEqual(self.digest(), before, "a dry run wrote to the world")

    def test_a_real_run_writes_and_reports_where_the_gate_is(self):
        before = self.digest()
        code, out, err = self.run_main(
            self.world, "--at", *map(str, SITE_CENTER), "--width", "32", "--height", "24",
        )
        self.assertEqual(code, 0, err)
        self.assertNotEqual(self.digest(), before)
        self.assertIn("wrote 1 files", out)
        self.assertIn("The gate is at", out)

    def test_the_survey_is_used_when_no_y_is_given(self):
        code, out, err = self.run_main(
            self.world, "--at", *map(str, SITE_CENTER), "--width", "32",
            "--height", "24", "--dry-run",
        )
        self.assertEqual(code, 0, err)
        self.assertIn(f"ground level y={SITE_GROUND}", out)

    def test_an_unbuildable_site_is_refused_with_advice(self):
        code, out, err = self.run_main(
            self.world, "--at", "10000", "10000", "--width", "32",
            "--height", "24", "--dry-run",
        )
        self.assertEqual(code, 1)
        self.assertIn("--y", err, "the refusal should say how to override")

    def test_no_dungeon_is_respected_and_announced(self):
        code, out, err = self.run_main(
            self.world, "--at", *map(str, SITE_CENTER), "--width", "32",
            "--height", "24", "--no-dungeon", "--dry-run",
        )
        self.assertEqual(code, 0, err)
        self.assertNotIn("dungeon", out)
        code, out, err = self.run_main(
            self.world, "--at", *map(str, SITE_CENTER), "--width", "32",
            "--height", "24", "--dry-run",
        )
        self.assertEqual(code, 0, err)
        self.assertIn("dungeon", out)

    def test_bad_sizes_are_refused_before_touching_the_world(self):
        for args in (("--width", "8"), ("--height", "500"), ("--width", "32", "--moat")):
            with self.subTest(args=args):
                code, _, err = self.run_main(self.world, *args, "--dry-run")
                self.assertEqual(code, 1)
                self.assertIn("error:", err)

    def test_seed_repeats_are_identical(self):
        """The same seed on the same terrain lands the same bytes.

        Two *fresh* worlds: rebuilding over an existing castle is a
        different starting palette and legitimately packs differently.
        """
        digests = []
        for attempt in ("a", "b"):
            world = make_world(os.path.join(self.dir, attempt))
            code, _, err = self.run_main(
                world, "--at", *map(str, SITE_CENTER), "--width", "32",
                "--height", "24", "--seed", "5",
            )
            self.assertEqual(code, 0, err)
            region = os.path.join(
                world, "dimensions/minecraft/overworld/region", "r.-1.0.mca"
            )
            with open(region, "rb") as handle:
                digests.append(hashlib.sha256(handle.read()).hexdigest())
        self.assertEqual(digests[0], digests[1], "same seed, same castle, same bytes")


if __name__ == "__main__":
    unittest.main(verbosity=2)
