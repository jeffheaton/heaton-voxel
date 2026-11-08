"""History restores storage, not just the visible block states."""
import hashlib
import os
import shutil
import unittest

from heaton_voxel.nbt import NBTError
from heaton_voxel.schem import Schematic
from heaton_voxel.world import NETHER, MinecraftSave, ReadOnlySave
from tests.test_create_chunks import _world
from tests.test_old_layout import _GAME, assemble
from tests.test_settings import make_world
from tests.test_world import SaveBase


class HistoryTests(SaveBase):
    def test_lazy_edit_undo_redo_and_save(self):
        record = self.save.begin_edit()
        self.assertEqual(self.save.loaded_region_count, 0)
        self.save.fill((0, 64, 0), (2, 64, 2), "minecraft:gold_block")
        record.complete()
        self.assertTrue(record.changed)
        self.assertGreater(record.byte_size, 0)
        dim = self.save.dimension()
        region = dim.region(0, 0)
        with open(region.path, "rb") as source:
            original = hashlib.sha256(source.read()).digest()
        edited = hashlib.sha256(region.to_bytes()).digest()
        self.assertNotEqual(original, edited)
        self.save.save()
        record.undo()
        self.assertEqual(hashlib.sha256(region.to_bytes()).digest(), original)
        self.assertGreater(self.save.save(), 0, "undo after save must write")
        with open(region.path, "rb") as source:
            self.assertEqual(hashlib.sha256(source.read()).digest(), original)
        record.redo()
        self.assertEqual(hashlib.sha256(region.to_bytes()).digest(), edited)
        self.assertEqual(str(self.save.get_block(0, 64, 0)), "minecraft:gold_block")

    def test_failed_settings_operation_rolls_back_level_and_player(self):
        before = self.save.level.to_bytes()
        player = self.save.player_position()
        record = self.save.begin_edit()
        self.save.set_game_type("creative")
        self.save.set_player_position(25, 90, -40)
        record.rollback()
        self.assertEqual(self.save.level.to_bytes(), before)
        self.assertEqual(self.save.player_position(), player)
        self.assertEqual(self.save.save(), 0)

    def test_delete_restores_cached_chunks_and_repeats(self):
        old = self.save.get_block(0, 64, 0)
        record = self.save.begin_edit()
        self.save.delete_chunks((0, 0, 0), (15, 0, 15))
        record.complete()
        self.assertIsNone(self.save.get_block(0, 64, 0))
        for _ in range(3):
            record.undo()
            self.assertEqual(self.save.get_block(0, 64, 0), old)
            record.redo()
            self.assertIsNone(self.save.get_block(0, 64, 0))

    def test_nested_edits_and_incomplete_save_refuse(self):
        record = self.save.begin_edit()
        with self.assertRaisesRegex(NBTError, "active edit"):
            self.save.begin_edit()
        with self.assertRaisesRegex(NBTError, "active edit"):
            self.save.save()
        with self.assertRaisesRegex(NBTError, "complete"):
            record.undo()
        record.complete()
        self.assertFalse(record.changed)
        record.undo()
        self.assertEqual(self.save.save(), 0)

    def test_lazily_created_entity_region(self):
        dim = self.save.dimension()
        record = self.save.begin_edit()
        dim.entity_chunk(0, 0, create=True, data_version=4903)
        record.complete()
        self.assertTrue(record.changed)
        record.undo()
        self.assertIsNone(dim.entity_chunk(0, 0))
        self.assertEqual(self.save.save(), 0)
        self.assertFalse(os.path.exists(os.path.join(dim.entities_dir, "r.0.0.mca")))
        record.redo()
        self.assertIsNotNone(dim.entity_chunk(0, 0))


class LaterWritersTests(unittest.TestCase):
    """The writers that came after history, each taken back whole: a record
    holds files, not calls, so a writer is covered once its every write
    lands in a file the save holds."""

    def setUp(self):
        self.directory = _world(4903)
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.save = MinecraftSave.load(self.directory)

    def region_path(self, *parts):
        return os.path.join(self.directory, "dimensions", "minecraft", *parts)

    def test_built_chunks_come_undone_and_leave_no_file(self):
        path = self.region_path("overworld", "region", "r.1.1.mca")
        record = self.save.begin_edit()
        self.save.create_chunks((640, 0, 640), (671, 0, 655))
        self.save.fill((640, 60, 640), (660, 62, 650), "minecraft:stone")
        record.complete()
        self.assertEqual(record.paths, (os.path.abspath(path),))
        record.undo()
        self.assertIsNone(self.save.dimension().chunk(40, 40))
        self.assertIsNone(self.save.get_block(650, 62, 645))
        self.assertEqual(self.save.save(), 0)
        self.assertFalse(os.path.exists(path))
        record.redo()
        self.assertEqual(str(self.save.get_block(650, 62, 645)), "minecraft:stone")
        self.assertEqual(self.save.save(), 1)
        self.assertEqual(str(MinecraftSave.load(self.directory).get_block(650, 62, 645)),
                         "minecraft:stone")

    def test_a_dimension_built_from_nothing_comes_undone(self):
        record = self.save.begin_edit()
        self.save.create_chunks((0, 0, 0), (15, 0, 15), dimension=NETHER)
        record.complete()
        self.assertTrue(record.changed)
        record.undo()
        self.assertIsNone(self.save.dimension(NETHER).chunk(0, 0))
        self.assertEqual(self.save.save(), 0)
        self.assertFalse(os.path.exists(os.path.join(self.directory, "dimensions",
                                                     "minecraft", "the_nether")))

    def test_a_structure_and_a_litematic_paste_come_undone(self):
        region = self.region_path("overworld", "region", "r.-1.0.mca")
        with open(region, "rb") as handle:
            original = handle.read()
        piece = Schematic(3, 2, 3, 4903)
        for x in range(3):
            for y in range(2):
                for z in range(3):
                    piece.set_block(x, y, z, "minecraft:gold_block")
        for name in ("piece.nbt", "piece.litematic"):
            with self.subTest(name):
                path = os.path.join(self.directory, name)
                piece.save(path)
                record = self.save.begin_edit()
                result = self.save.import_schem(path, (-400, 120, 20))
                record.complete()
                self.assertEqual(result.blocks_pasted, 18)
                self.assertEqual(str(self.save.get_block(-399, 121, 21)),
                                 "minecraft:gold_block")
                self.save.save()
                record.undo()
                self.assertEqual(str(self.save.get_block(-399, 121, 21)), "minecraft:air")
                self.save.save()
                with open(region, "rb") as handle:
                    self.assertEqual(handle.read(), original)

    def test_a_settings_file_first_opened_inside_an_edit_comes_undone(self):
        world = make_world(self.directory)
        save = MinecraftSave.load(world)
        weather = os.path.join(world, "data", "minecraft", "weather.dat")
        with open(weather, "rb") as handle:
            original = handle.read()
        record = save.begin_edit()
        save.set_weather("thunder", duration=6000)
        record.complete()
        self.assertEqual(record.paths, (os.path.abspath(weather),))
        save.save()
        record.undo()
        self.assertEqual(save.save(), 1)
        with open(weather, "rb") as handle:
            self.assertEqual(handle.read(), original)

    def test_a_save_in_the_old_layout_records_nothing_and_still_refuses(self):
        world = assemble(_GAME["cases"][0]["before"], os.path.join(self.directory, "old"))
        save = MinecraftSave.load(world)
        record = save.begin_edit()
        with self.assertRaises(ReadOnlySave):
            save.create_chunks((640, 0, 640), (640, 0, 640))
        record.rollback()
        self.assertFalse(record.changed)
        with self.assertRaises(ReadOnlySave):
            save.save()
