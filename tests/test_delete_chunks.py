"""Tests for delete_chunks: handing ground back to the generator.

A chunk lives in three files and each has to be cleared, so most of these
build the other two -- entity storage and cached points of interest -- and
then check on a freshly loaded save that the deletion actually reached
disk.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from heaton_voxel.nbt import NBT, Chunk, NBTError, TagCompound, TagInt
from heaton_voxel.world import MinecraftSave
from tests.test_world import DATA_VERSION, make_save

CHUNKS = [(0, 0), (1, 0), (0, 1), (1, 1), (5, 5), (-1, -1)]


class DeleteBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="delete-chunks-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_save(self.dir, CHUNKS)
        self.save = MinecraftSave.load(self.dir)

    def region_path(self, kind: str, region_x: int, region_z: int) -> str:
        return os.path.join(
            self.dir,
            "dimensions/minecraft/overworld",
            kind,
            f"r.{region_x}.{region_z}.mca",
        )

    def write_poi(self, chunks):
        """A poi/ file holding one record per chunk, as the game writes."""
        directory = os.path.dirname(self.region_path("poi", 0, 0))
        os.makedirs(directory, exist_ok=True)
        region = NBT()
        region.kind = "region"
        region.region_x = region.region_z = 0
        for chunk_x, chunk_z in chunks:
            chunk = Chunk(chunk_x & 31, chunk_z & 31)
            chunk.data = TagCompound(
                {"DataVersion": TagInt(DATA_VERSION), "Sections": TagCompound()}
            )
            region.chunks[(chunk_x & 31, chunk_z & 31)] = chunk
        region.save(self.region_path("poi", 0, 0))


class TerrainTests(DeleteBase):
    def test_the_chunk_leaves_the_terrain_tree(self):
        result = self.save.delete_chunks((0, 0, 0), (15, 0, 15))
        self.assertEqual(result.chunks_deleted, 1)
        self.assertEqual(result.chunks_absent, 0)
        self.assertEqual(result.regions_touched, 1)
        self.assertIsNone(self.save.dimension().chunk(0, 0))

    def test_a_deleted_chunk_stops_answering_about_itself(self):
        """``Dimension.blocks`` memoizes, and answers from that cache
        without looking at ``region.chunks`` again -- so a delete that
        skipped ``release()`` would leave the chunk reporting a generation
        status and a surface height for ground that is no longer there.
        Exactly the failure where a stale answer reads like a real one."""
        dim = self.save.dimension()
        self.save.fill((0, 60, 0), (15, 70, 15), "minecraft:stone")
        self.save.rebuild_heightmaps((0, 0, 0), (15, 0, 15))
        self.assertEqual(dim.chunk_status(0, 0), "minecraft:full")
        self.assertEqual(dim.surface_y(4, 4, "WORLD_SURFACE"), 70)

        self.save.delete_chunks((0, 0, 0), (15, 0, 15))
        self.assertIsNone(dim.chunk_status(0, 0))
        self.assertIsNone(dim.surface_y(4, 4, "WORLD_SURFACE"))

    def test_the_whole_column_goes_whatever_y_says(self):
        self.save.delete_chunks((0, 70, 0), (15, 70, 15))
        self.assertIsNone(self.save.get_block(4, 200, 4))
        self.assertIsNone(self.save.get_block(4, -60, 4))

    def test_neighboring_chunks_are_untouched(self):
        self.save.delete_chunks((0, 0, 0), (15, 0, 15))
        dim = self.save.dimension()
        for chunk_x, chunk_z in CHUNKS[1:]:
            self.assertIsNotNone(dim.chunk(chunk_x, chunk_z))

    def test_a_chunk_too_old_to_read_is_still_deletable(self):
        """Deleting never decodes a chunk, which is just as well: an
        unreadable one is usually why somebody is deleting."""
        self.save.dimension().chunk(0, 0).data["DataVersion"] = TagInt(100)
        with self.assertRaises(NBTError):
            self.save.dimension().blocks(0, 0)

        result = self.save.delete_chunks((0, 0, 0), (15, 0, 15))
        self.assertEqual(result.chunks_deleted, 1)
        self.assertIsNone(self.save.dimension().chunk(0, 0))


class BoxTests(DeleteBase):
    def test_deleted_box_is_chunk_aligned(self):
        """The smallest deletable thing is a chunk, so a box that starts
        mid-chunk clears the whole chunk -- reported, not left implicit."""
        result = self.save.delete_chunks((5, 0, 5), (20, 0, 20))
        self.assertEqual(result.deleted_box, ((0, 0), (31, 31)))
        self.assertEqual(result.chunks_deleted, 4)

    def test_the_box_floors_below_a_negative_coordinate(self):
        """Truncation would put x=-1 in chunk 0 and delete the wrong one."""
        result = self.save.delete_chunks((-1, 0, -1), (-1, 0, -1))
        self.assertEqual(result.deleted_box, ((-16, -16), (-1, -1)))
        self.assertEqual(result.chunks_deleted, 1)
        self.assertIsNone(self.save.dimension().chunk(-1, -1))
        self.assertIsNotNone(self.save.dimension().chunk(0, 0))

    def test_corners_may_be_given_in_any_order(self):
        result = self.save.delete_chunks((20, 0, 20), (5, 0, 5))
        self.assertEqual(result.deleted_box, ((0, 0), (31, 31)))
        self.assertEqual(result.chunks_deleted, 4)

    def test_a_box_spanning_two_region_files(self):
        result = self.save.delete_chunks((-16, 0, -16), (15, 0, 15))
        self.assertEqual(result.chunks_deleted, 2)  # (-1,-1) and (0,0)
        self.assertEqual(result.chunks_absent, 2)  # (-1,0) and (0,-1)
        self.assertEqual(result.regions_touched, 2)

    def test_a_chunk_that_is_already_gone_counts_absent(self):
        self.save.delete_chunks((0, 0, 0), (15, 0, 15))
        again = self.save.delete_chunks((0, 0, 0), (15, 0, 15))
        self.assertEqual((again.chunks_deleted, again.chunks_absent), (0, 1))
        self.assertEqual(again.regions_touched, 0)

    def test_a_region_the_world_has_no_file_for_is_absent_not_an_error(self):
        result = self.save.delete_chunks((2000, 0, 2000), (2000, 0, 2000))
        self.assertEqual((result.chunks_deleted, result.chunks_absent), (0, 1))
        self.assertEqual(result.regions_touched, 0)


class EntityAndPoiTests(DeleteBase):
    def test_entity_storage_goes_with_the_chunk(self):
        """``EntityStorage`` reads ``entities/`` without consulting the
        terrain chunk, so a villager left behind stands up again in
        whatever the generator builds next.

        This also pins the reason the file has to be *opened*: ``save()``
        writes only the regions it holds open, so an answer computed from
        the directory index alone would count right and write nothing."""
        self.assertTrue(
            self.save.spawn_entity("minecraft:villager", 8.5, 70.0, 8.5)
        )
        self.assertGreaterEqual(self.save.save(), 1)
        self.assertTrue(os.path.exists(self.region_path("entities", 0, 0)))

        reopened = MinecraftSave.load(self.dir)
        result = reopened.delete_chunks((0, 0, 0), (15, 0, 15))
        self.assertEqual(result.entity_chunks_deleted, 1)
        self.assertGreaterEqual(reopened.save(), 1)

        fresh = MinecraftSave.load(self.dir)
        self.assertIsNone(fresh.dimension().entity_chunk(0, 0))

    def test_entities_outside_the_box_stay(self):
        self.save.spawn_entity("minecraft:villager", 8.5, 70.0, 8.5)
        self.save.spawn_entity("minecraft:villager", 24.5, 70.0, 8.5)
        result = self.save.delete_chunks((0, 0, 0), (15, 0, 15))
        self.assertEqual(result.entity_chunks_deleted, 1)
        self.assertEqual(
            len(self.save.entities_in((16, 0, 0), (31, 100, 15))), 1
        )

    def test_poi_records_go_with_the_chunk(self):
        """``PoiSection.refresh`` returns early on a persisted
        ``Valid: true``, so stale records are never rescanned and a
        villager keeps its claim on a bed the regeneration removed."""
        self.write_poi([(0, 0), (5, 5)])
        save = MinecraftSave.load(self.dir)
        result = save.delete_chunks((0, 0, 0), (15, 0, 15))
        self.assertEqual(result.poi_chunks_deleted, 1)
        self.assertGreaterEqual(save.save(), 1)

        fresh = NBT.load(self.region_path("poi", 0, 0))
        self.assertEqual(sorted(fresh.chunks), [(5, 5)])

    def test_storage_the_world_never_wrote_is_not_a_failure(self):
        """Most chunks have no entities and no points of interest; zeros
        there are ordinary, not a partial delete."""
        result = self.save.delete_chunks((0, 0, 0), (15, 0, 15))
        self.assertEqual(result.chunks_deleted, 1)
        self.assertEqual(result.entity_chunks_deleted, 0)
        self.assertEqual(result.poi_chunks_deleted, 0)


class OnDiskTests(DeleteBase):
    def test_the_deletion_survives_a_save_and_reload(self):
        self.save.delete_chunks((0, 0, 0), (15, 0, 15))
        self.assertGreaterEqual(self.save.save(), 1)

        fresh = MinecraftSave.load(self.dir)
        self.assertIsNone(fresh.dimension().chunk(0, 0))
        self.assertIsNotNone(fresh.dimension().chunk(1, 1))

    def test_surviving_chunks_are_written_back_byte_for_byte(self):
        """Deleting a chunk rewrites the region file around its neighbors;
        those neighbors must come out of it unchanged, timestamps and
        compressed payloads alike."""
        region = self.save.dimension().region(0, 0)
        before = {
            key: (chunk.payload(), chunk.timestamp)
            for key, chunk in region.chunks.items()
        }
        self.save.delete_chunks((0, 0, 0), (15, 0, 15))
        self.assertGreaterEqual(self.save.save(), 1)

        fresh = NBT.load(self.region_path("region", 0, 0))
        self.assertNotIn((0, 0), fresh.chunks)
        self.assertEqual(len(fresh.chunks), len(before) - 1)
        for key, chunk in fresh.chunks.items():
            self.assertEqual(
                (chunk.payload(), chunk.timestamp),
                before[key],
                f"chunk {key} changed when its neighbor was deleted",
            )

    def test_an_emptied_region_file_is_left_in_place(self):
        """A region file full of holes is exactly what Minecraft writes for
        ground nobody has visited; unlinking it is a different operation."""
        result = self.save.delete_chunks((-16, 0, -16), (-1, 0, -1))
        self.assertEqual(result.chunks_deleted, 1)
        self.assertGreaterEqual(self.save.save(), 1)

        path = self.region_path("region", -1, -1)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(len(NBT.load(path).chunks), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
