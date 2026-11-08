"""``craft.audit``: a build already standing, judged in one call.

Each finding is one the writers would have refused and a paste never asks
about, so each test stands the fault in the world with the lower tier, as a
paste would, and asks the audit to find it. A render warns about the one
thing a picture cannot show: that the map it was drawn from is wrong.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import tempfile
import unittest

from heaton_voxel.__main__ import main
from heaton_voxel.craft import Audit, audit, render_area
from heaton_voxel.nbt import NBTError, TagInt, TagString
from heaton_voxel.world import OVERWORLD, MinecraftSave
from tests.test_world import make_save


class Base(unittest.TestCase):
    """Four finished chunks of stone with a floor at y=60, its maps right."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="audit-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.world = os.path.join(self.dir, "world")
        os.makedirs(self.world)
        make_save(self.world, [(0, 0), (1, 0), (0, 1), (1, 1)])
        self.save = MinecraftSave.load(self.world)
        self.dim = self.save.dimension(OVERWORLD)
        self.save.fill((0, 50, 0), (31, 60, 31), "minecraft:stone")
        self.settle()

    def settle(self):
        self.save.rebuild_heightmaps((0, 0, 0), (31, 0, 31))

    def audit(self, **options):
        return audit(self.save, (0, 50, 0), (31, 80, 31), **options)


class FindingTests(Base):
    def test_nothing_is_found_in_nothing_wrong(self):
        found = self.audit()
        self.assertIsInstance(found, Audit)
        self.assertTrue(found.clean)
        self.assertIn("nothing found", found.report())
        self.assertIn("nothing found", repr(found))

    def test_a_block_the_game_will_not_keep(self):
        self.save.set_block(4, 70, 4, "minecraft:torch")
        self.settle()
        found = self.audit()
        self.assertEqual([fault.cell for fault in found.unsupported], [(4, 70, 4)])
        self.assertFalse(found.clean)
        self.assertIn("(4, 70, 4)", found.report())

    def test_water_that_has_run_is_found_though_it_has_settled(self):
        """A flood across a floor has nowhere left to go, so a containment
        check passes it. It is the flow it left that gives it away."""
        self.save.fill((2, 61, 2), (12, 62, 12), "minecraft:stone")
        self.save.fill((3, 62, 3), (11, 62, 11), "minecraft:air")
        self.save.set_block(4, 62, 4, "minecraft:water[level=0]")
        self.save.set_block(5, 62, 4, "minecraft:water[level=1]")
        self.settle()
        found = self.audit()
        self.assertEqual(len(found.flowing), 1)
        self.assertEqual(found.flowing[0].flowing, frozenset({(5, 62, 4)}))
        self.assertIn("1 of 2 cells moving", found.report())

    def test_still_water_in_a_tub_is_no_finding(self):
        self.save.fill((2, 61, 2), (6, 62, 6), "minecraft:stone")
        self.save.fill((3, 62, 3), (5, 62, 5), "minecraft:water[level=0]")
        self.settle()
        self.assertTrue(self.audit().clean)

    def test_water_that_will_run_at_the_next_update(self):
        """Written beside air, it stands until a block beside it changes."""
        self.save.set_block(8, 61, 8, "minecraft:water[level=0]")
        self.settle()
        found = self.audit()
        self.assertEqual(found.flowing, [])
        self.assertTrue(found.leaks)
        self.assertEqual({leak.source for leak in found.leaks}, {(8, 61, 8)})
        self.assertIn("water that runs at the next update", found.report())

    def test_stale_maps_by_chunk_until_they_are_rebuilt(self):
        self.save.fill((16, 61, 0), (17, 61, 0), "minecraft:stone")
        found = self.audit()
        self.assertEqual(sorted(found.heightmaps), [(1, 0)])
        self.assertEqual(found.heightmaps[(1, 0)]["WORLD_SURFACE"], 2)
        self.assertIn("chunk (1, 0): WORLD_SURFACE 2", found.report())
        self.settle()
        self.assertTrue(self.audit().clean)

    def test_a_long_list_is_cut_off_and_counted(self):
        for x in range(0, 30):
            self.save.set_block(x, 70, 4, "minecraft:torch")
        self.settle()
        report = self.audit().report()
        self.assertIn("blocks the game will not keep, and doors: 30", report)
        self.assertIn("... 18 more", report)


class WalkTests(Base):
    def test_no_doorstep_is_no_walk_and_says_so(self):
        found = self.audit()
        self.assertIsNone(found.walk)
        self.assertEqual(found.stranded, frozenset())
        self.assertIn("walk: not asked for", found.report())

    def test_a_round_trip_is_clean(self):
        found = self.audit(start=(4, 60, 4))
        self.assertGreater(len(found.walk.reached), 900)
        self.assertTrue(found.clean)
        self.assertIn("round trip everywhere", found.report())

    def test_a_pit_you_cannot_climb_out_of(self):
        self.save.fill((8, 57, 8), (10, 60, 10), "minecraft:air")
        self.settle()
        found = self.audit(start=(4, 60, 4))
        self.assertIn((9, 56, 9), found.stranded)
        self.assertFalse(found.clean)
        self.assertIn("cells you can reach and not leave", found.report())


class ChunkTests(Base):
    def test_what_it_could_not_judge_it_names(self):
        self.dim.chunk(1, 0).data["Status"] = TagString("minecraft:features")
        found = audit(self.save, (0, 50, 0), (47, 80, 15))
        self.assertEqual(found.skipped, {
            (1, 0): "unfinished (minecraft:features)",
            (2, 0): "never generated"})
        self.assertTrue(found.clean, "clean, as far as it was read")
        self.assertIn("chunks not judged: 2", found.report())

    def test_an_unreadable_chunk_is_never_clean(self):
        chunk = self.dim.chunk(1, 0)
        chunk.data["DataVersion"] = TagInt(100)
        self.dim.release(1, 0)
        with self.assertRaises(NBTError):
            self.audit()


class CommandLineTests(Base):
    def run_audit(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["audit", self.world, *arguments])
        return code, out.getvalue(), err.getvalue()

    BOX = ("--box", "0", "50", "0", "31", "80", "31")

    def test_nothing_found_exits_0(self):
        self.save.save()
        code, out, _ = self.run_audit(*self.BOX)
        self.assertEqual(code, 0)
        self.assertIn("nothing found", out)

    def test_a_finding_exits_1(self):
        self.save.set_block(4, 70, 4, "minecraft:torch")
        self.settle()
        self.save.save()
        code, out, _ = self.run_audit(*self.BOX)
        self.assertEqual(code, 1)
        self.assertIn("(4, 70, 4)", out)

    def test_a_walk_from_the_command_line(self):
        self.save.save()
        code, out, _ = self.run_audit(*self.BOX, "--from", "4", "60", "4")
        self.assertEqual(code, 0)
        self.assertIn("round trip everywhere", out)

    def test_a_refusal_exits_2_and_says_why(self):
        self.save.save()
        code, _, err = self.run_audit(*self.BOX, "--from", "4", "70", "4")
        self.assertEqual(code, 2)
        self.assertIn("nothing to stand on", err)


class RenderTests(Base):
    def picture(self, **options):
        path = os.path.join(self.dir, "map.png")
        return render_area(self.save, (0, 0, 0), (31, 0, 31), path,
                           dimension=OVERWORLD, **options)

    def test_a_right_map_draws_without_a_word(self):
        self.assertEqual(self.picture().stale_maps, {})

    def test_a_stale_map_is_named_by_chunk(self):
        """The columns of the map it drew from, and no other."""
        self.save.fill((16, 61, 0), (17, 61, 0), "minecraft:stone")
        self.assertEqual(self.picture().stale_maps, {(1, 0): 2})
        maps = self.dim.chunk(1, 0).data["Heightmaps"]
        del maps["OCEAN_FLOOR"]
        self.dim.blocks(1, 0).drop_heightmap_cache()
        self.assertEqual(self.picture(heightmap="OCEAN_FLOOR").stale_maps, {})

    def test_a_chunk_it_cannot_judge_is_passed_over(self):
        self.save.fill((16, 61, 0), (17, 61, 0), "minecraft:stone")
        self.dim.chunk(1, 0).data["Status"] = TagString("minecraft:terrain")
        self.assertEqual(self.picture().stale_maps, {})

    def test_the_command_line_warns(self):
        self.save.fill((16, 61, 0), (17, 61, 0), "minecraft:stone")
        self.save.save()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["render", self.world, "--area", "0", "0", "31", "31",
                         "-o", os.path.join(self.dir, "map.png")])
        self.assertEqual(code, 0)
        self.assertIn("warning: WORLD_SURFACE is stale in 2 columns of 1 chunks",
                      out.getvalue())


if __name__ == "__main__":
    unittest.main()
