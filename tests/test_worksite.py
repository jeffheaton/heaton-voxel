"""Worksite: the snapshot, the guardrails, the rollback.

The promises under test are the ones building sessions bled for: a
restore puts back the exact bytes the snapshot took (not equivalent ones),
a stale snapshot refuses to resume by file name rather than quietly
becoming a time bomb, and a protected zone trips on one flipped block
inside it while ignoring legitimate edits beside it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import heaton_voxel.craft as craft  # noqa: E402
from heaton_voxel.blocks import BlockState  # noqa: E402
from heaton_voxel.craft import Worksite, list_worksites  # noqa: E402
from heaton_voxel.craft.build import (  # noqa: E402
    LANE_MIX, MELT_SNOW, NO_SPAWNS, Builder, Railway)
from heaton_voxel.craft.errors import Refused  # noqa: E402
from heaton_voxel.nbt import NBT, Chunk, NBTError, TagCompound, TagInt  # noqa: E402
from heaton_voxel.world import MinecraftSave  # noqa: E402
from tests.test_settings import make_world  # noqa: E402
from tests.test_world import make_save  # noqa: E402

# Inside the fixture region r.-1.0.mca, on generated ground.
BOX = ((-344, 0), (-320, 24))
INSIDE = (-328, 70, 8)
REGION_REL = "dimensions/minecraft/overworld/region/r.-1.0.mca"
ENTITIES_REL = "dimensions/minecraft/overworld/entities/r.-1.0.mca"


class WorksiteTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="worksite-tests-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.world = make_world(self.tmp)

    def open_site(self, **kwargs) -> Worksite:
        return Worksite.open(self.world, *BOX, name="test-site", **kwargs)

    def world_bytes(self, rel: str) -> bytes:
        with open(os.path.join(self.world, rel), "rb") as handle:
            return handle.read()

    def lock(self, path: str) -> None:
        """Take every permission off ``path`` until the test ends."""
        mode = os.stat(path).st_mode
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, mode)


# Taking a path's permissions away only hides it from a process that obeys
# them: not root, and not Windows, where a directory stays listable.
needs_permissions = unittest.skipIf(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    "this process can read a path whatever its permissions say")


class SnapshotTests(WorksiteTestCase):
    def test_open_takes_the_snapshot(self):
        site = self.open_site()
        copy = os.path.join(site.directory, "files", REGION_REL)
        self.assertTrue(os.path.exists(copy))
        with open(copy, "rb") as a, \
                open(os.path.join(self.world, REGION_REL), "rb") as b:
            self.assertEqual(a.read(), b.read())
        # Files the box covers but the world does not have are recorded
        # as absent, so a build that creates one can be undone.
        self.assertIsNone(site._manifest["files"][ENTITIES_REL])

    def test_restore_is_byte_exact(self):
        site = self.open_site()
        before = self.world_bytes(REGION_REL)

        x, y, z = INSIDE
        self.assertTrue(site.save.set_block(x, y, z, "minecraft:gold_block"))
        site.save.save()
        self.assertNotEqual(before, self.world_bytes(REGION_REL))

        changed = site.restore()
        self.assertIn(REGION_REL, changed.files)
        self.assertEqual(before, self.world_bytes(REGION_REL))
        # The worksite's own save reloads itself and sees the old world.
        state = site.save.get_block(x, y, z)
        self.assertNotEqual(str(state), "minecraft:gold_block")

    def test_restore_deletes_files_the_build_created(self):
        site = self.open_site()
        created = os.path.join(self.world, ENTITIES_REL)
        os.makedirs(os.path.dirname(created), exist_ok=True)
        with open(created, "wb") as handle:
            handle.write(b"pretend entities")
        site.restore()
        self.assertFalse(os.path.exists(created))


class SidecarTests(WorksiteTestCase):
    """A chunk too big for its region lives in a ``.mcc`` beside it, and the
    region's header points there: a restore that put the region back and
    not the sidecar would leave a chunk pointing at nothing."""

    REGION_DIR = os.path.dirname(REGION_REL)
    KEPT = os.path.join(REGION_DIR, "c.-20.5.mcc")       # region -1,0: covered
    MADE = os.path.join(REGION_DIR, "c.-21.6.mcc")       # covered, created later
    AFAR = os.path.join(REGION_DIR, "c.100.100.mcc")     # region 3,3: not covered

    def write(self, rel: str, payload: bytes) -> None:
        with open(os.path.join(self.world, rel), "wb") as handle:
            handle.write(payload)

    def test_restore_puts_sidecars_back_and_takes_new_ones_away(self):
        self.write(self.KEPT, b"oversized chunk")
        self.write(self.AFAR, b"somebody else's chunk")
        site = self.open_site()
        self.assertIn(self.KEPT, site._manifest["files"])
        self.assertNotIn(self.AFAR, site._manifest["files"])

        os.remove(os.path.join(self.world, self.KEPT))
        self.write(self.MADE, b"made by the build")
        result = site.restore()
        self.assertEqual(self.world_bytes(self.KEPT), b"oversized chunk")
        self.assertFalse(os.path.exists(os.path.join(self.world, self.MADE)))
        self.assertIn(self.MADE, result.files)
        self.assertEqual(self.world_bytes(self.AFAR), b"somebody else's chunk")

    def test_a_snapshot_that_never_looked_leaves_every_sidecar_alone(self):
        site = self.open_site()
        del site._manifest["covered"]          # as written before sidecars
        self.write(self.MADE, b"cannot tell new from old")
        site.restore()
        self.assertTrue(os.path.exists(os.path.join(self.world, self.MADE)))


class ResumeTests(WorksiteTestCase):
    def test_clean_resume_keeps_the_snapshot(self):
        first = self.open_site()
        created = first._manifest["created"]
        again = self.open_site()
        self.assertEqual(again._manifest["created"], created)

    def test_drift_refuses_by_file_name(self):
        self.open_site()
        outside = MinecraftSave.load(self.world)
        x, y, z = INSIDE
        outside.set_block(x, y, z, "minecraft:tnt")
        outside.save()
        with self.assertRaises(NBTError) as caught:
            self.open_site()
        self.assertIn("r.-1.0.mca", str(caught.exception))
        self.assertIn("resnapshot=True", str(caught.exception))

    def test_resnapshot_rebases_and_keeps_zones(self):
        first = self.open_site()
        first.protect((-330, 60, 6), (-326, 75, 10), name="shrine")
        outside = MinecraftSave.load(self.world)
        x, y, z = INSIDE
        outside.set_block(x, y, z, "minecraft:tnt")
        outside.save()

        site = self.open_site(resnapshot=True)
        # The zone survives, re-digested against the world as it now
        # stands — so the tnt is the new baseline, not a violation.
        self.assertIn("shrine", site._manifest["zones"])
        self.assertEqual(site.check(), [])

    def test_different_corners_refuse(self):
        self.open_site()
        with self.assertRaises(NBTError) as caught:
            Worksite.open(self.world, (-344, 0), (-320, 48),
                          name="test-site")
        self.assertIn("different corners", str(caught.exception))


class RollbackTests(WorksiteTestCase):
    """Undoing a build from a process that is not the one that built it.

    The drift guard cannot tell a build you saved from a week of play on
    top of it, which is right — and which also means `open` can never be
    the way back. `rollback` is, and everything about it is arranged so it
    cannot be mistaken for a resume.
    """

    def build_and_save(self) -> bytes:
        """Build, save, and hand back the pre-build bytes."""
        site = self.open_site()
        before = self.world_bytes(REGION_REL)
        x, y, z = INSIDE
        site.save.set_block(x, y, z, "minecraft:gold_block")
        site.save.save()
        self.assertNotEqual(before, self.world_bytes(REGION_REL))
        return before

    def test_a_saved_build_rolls_back_byte_exact_from_a_fresh_object(self):
        before = self.build_and_save()
        result = Worksite.rollback(self.world, "test-site")
        self.assertIn(REGION_REL, result.files)
        self.assertEqual(before, self.world_bytes(REGION_REL),
                         "the exact bytes, not equivalent ones")

    def test_open_still_refuses_the_same_situation(self):
        """The guard rollback exists to route around is still standing."""
        self.build_and_save()
        with self.assertRaises(NBTError) as caught:
            self.open_site()
        self.assertIn("resnapshot=True", str(caught.exception))

    def test_the_worksite_resumes_clean_once_it_has_been_rolled_back(self):
        created = self.open_site()._manifest["created"]
        self.build_and_save()
        Worksite.rollback(self.world, "test-site")
        again = self.open_site()
        self.assertEqual(again._manifest["created"], created,
                         "the same snapshot, not a rebased one")

    def test_a_dry_run_names_the_files_and_moves_none_of_them(self):
        self.build_and_save()
        built = self.world_bytes(REGION_REL)
        planned = Worksite.rollback(self.world, "test-site", dry_run=True)
        self.assertIn(REGION_REL, planned.files)
        self.assertTrue(planned.dry_run)
        self.assertIn("would put back", repr(planned))
        self.assertEqual(built, self.world_bytes(REGION_REL),
                         "looking is not leaping")

    def test_it_reports_the_snapshot_it_is_reinstating(self):
        site = self.open_site()
        site.accept()
        self.build_and_save()
        result = Worksite.rollback(self.world, "test-site")
        self.assertEqual(result.taken, site._manifest["created"])
        self.assertIsNotNone(result.accepted)
        self.assertIn("snapshot taken", repr(result))

    def test_files_the_build_created_are_deleted_again(self):
        self.open_site()
        created = os.path.join(self.world, ENTITIES_REL)
        os.makedirs(os.path.dirname(created), exist_ok=True)
        with open(created, "wb") as handle:
            handle.write(b"pretend entities")
        result = Worksite.rollback(self.world, "test-site")
        self.assertFalse(os.path.exists(created))
        self.assertEqual(result.deleted, 1)

    def test_a_world_with_no_such_worksite_is_refused(self):
        with self.assertRaises(NBTError) as caught:
            Worksite.rollback(self.world, "never-existed")
        self.assertIn("no snapshot to go back to", str(caught.exception))

    def test_a_directory_that_is_not_a_worksite_is_refused(self):
        site = self.open_site()
        with open(os.path.join(site.directory, "manifest.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"worksite": "test-site"}, handle)
        with self.assertRaises(NBTError) as caught:
            Worksite.rollback(self.world, "test-site")
        self.assertIn("not a worksite manifest", str(caught.exception))

    def test_a_manifest_that_is_not_json_is_refused_by_name(self):
        """A damaged record is a refusal that says so, not a traceback from
        the JSON parser -- which is all a command line could have shown."""
        site = self.open_site()
        with open(os.path.join(site.directory, "manifest.json"), "w",
                  encoding="utf-8") as handle:
            handle.write("{not json")
        with self.assertRaises(NBTError) as caught:
            Worksite.rollback(self.world, "test-site")
        self.assertIn("manifest.json", str(caught.exception))
        self.assertIn("cannot be read", str(caught.exception))

    def test_a_manifest_that_is_json_but_no_object_is_refused_by_name(self):
        site = self.open_site()
        for payload in ("[]", "null", '"files"', "7"):
            with self.subTest(payload=payload):
                with open(os.path.join(site.directory, "manifest.json"), "w",
                          encoding="utf-8") as handle:
                    handle.write(payload)
                with self.assertRaises(NBTError) as caught:
                    Worksite.rollback(self.world, "test-site", dry_run=True)
                self.assertIn("not a worksite manifest", str(caught.exception))

    @needs_permissions
    def test_a_worksite_nobody_may_open_is_unreadable_not_absent(self):
        """A directory the process may not look into says so: reporting it
        as no worksite would make "could not read" look like "nothing
        there", which is the one thing a refusal must never do."""
        site = self.open_site()
        self.lock(site.directory)
        with self.assertRaises(NBTError) as caught:
            Worksite.rollback(self.world, "test-site", dry_run=True)
        self.assertIn("cannot be read", str(caught.exception))
        self.assertNotIn("no snapshot to go back to", str(caught.exception))

    def test_the_receipt_names_which_files_it_deletes(self):
        """A plan has to say *delete* against *restore* file by file, and a
        count of deletions cannot."""
        site = self.open_site()
        x, y, z = INSIDE
        site.save.set_block(x, y, z, "minecraft:gold_block")
        site.save.save()
        created = os.path.join(self.world, ENTITIES_REL)
        os.makedirs(os.path.dirname(created), exist_ok=True)
        with open(created, "wb") as handle:
            handle.write(b"pretend entities")

        planned = Worksite.rollback(self.world, "test-site", dry_run=True)
        self.assertEqual(planned.removed, [ENTITIES_REL])
        self.assertEqual(planned.deleted, 1)
        self.assertIn(REGION_REL, planned.files)
        self.assertNotIn(REGION_REL, planned.removed)
        result = Worksite.rollback(self.world, "test-site")
        self.assertEqual(result.removed, [ENTITIES_REL])
        self.assertFalse(os.path.exists(created))

    def test_it_hands_back_a_receipt_and_never_a_worksite(self):
        self.build_and_save()
        result = Worksite.rollback(self.world, "test-site")
        self.assertNotIsInstance(result, Worksite,
                                 "nothing may go on to build against a "
                                 "baseline it has just reverted")

    def test_the_receipt_accounts_for_every_file_in_the_snapshot(self):
        site = self.open_site()
        self.build_and_save()
        result = Worksite.rollback(self.world, "test-site")
        self.assertEqual(len(result.files) + result.already,
                         len(site._manifest["files"]),
                         "moved plus already-right is the whole snapshot")


class ListingTests(WorksiteTestCase):
    """Finding a worksite without loading the save, and without mistaking a
    damaged record or a hand-made backup for nothing being there."""

    def manifest_of(self, site: Worksite) -> dict:
        with open(os.path.join(site.directory, "manifest.json"),
                  encoding="utf-8") as handle:
            return json.load(handle)

    def test_every_worksite_is_listed_in_name_order(self):
        second = Worksite.open(self.world, *BOX, name="b-site")
        second.protect((-330, 60, 6), (-326, 75, 10), name="shrine")
        first = Worksite.open(self.world, (-340, 4), (-330, 14), name="a-site",
                              margin=16)
        first.accept()
        listed = list_worksites(self.world)
        self.assertEqual([entry.name for entry in listed], ["a-site", "b-site"])
        for entry, site in zip(listed, (first, second)):
            with self.subTest(site=entry.name):
                manifest = self.manifest_of(site)
                files = manifest["files"]
                self.assertTrue(entry.has_manifest)
                self.assertIsNone(entry.problem)
                self.assertEqual(entry.directory, site.directory)
                self.assertEqual(entry.world, manifest["world"])
                self.assertEqual(entry.dimension, manifest["dimension"])
                self.assertEqual(entry.box, tuple(manifest["box"]))
                self.assertEqual(entry.margin, manifest["margin"])
                self.assertEqual(entry.created, manifest["created"])
                self.assertEqual(entry.accepted, manifest["accepted"])
                self.assertEqual(entry.copies, sum(1 for d in files.values()
                                                   if d is not None))
                self.assertEqual(entry.absent, sum(1 for d in files.values()
                                                   if d is None))
                self.assertEqual(entry.paths, tuple(sorted(files)))
                self.assertEqual(entry.zones, tuple(sorted(manifest["zones"])))
                self.assertTrue(entry.sidecars_tracked)
                self.assertIsNone(entry.note)
        self.assertIsNotNone(listed[0].accepted)
        self.assertEqual(listed[1].zones, ("shrine",))
        self.assertGreater(listed[0].absent, 0, "the entities file is absent")

    def test_a_directory_without_a_manifest_is_listed_not_skipped(self):
        """A backup made by hand sits there looking like a worksite; the
        listing says what it is rather than leaving it out."""
        os.makedirs(os.path.join(self.world, "worksites", "backup", "files"))
        (entry,) = list_worksites(self.world)
        self.assertEqual(entry.name, "backup")
        self.assertFalse(entry.has_manifest)
        self.assertIn("no manifest.json", entry.problem)

    def test_a_manifest_that_is_not_json_is_listed_as_unreadable(self):
        self.open_site()
        broken = Worksite.open(self.world, *BOX, name="broken")
        with open(os.path.join(broken.directory, "manifest.json"), "w",
                  encoding="utf-8") as handle:
            handle.write("{not json")
        listed = {entry.name: entry for entry in list_worksites(self.world)}
        self.assertEqual(sorted(listed), ["broken", "test-site"],
                         "one bad record hides nothing else")
        self.assertTrue(listed["broken"].has_manifest)
        self.assertIn("manifest.json", listed["broken"].problem)
        self.assertIsNone(listed["test-site"].problem)

    def test_no_worksites_lists_nothing_and_a_non_save_is_refused(self):
        self.assertEqual(list_worksites(self.world), [])
        os.makedirs(os.path.join(self.world, "worksites"))
        with open(os.path.join(self.world, "worksites", ".DS_Store"), "wb") as f:
            f.write(b"\0")
        self.assertEqual(list_worksites(self.world), [],
                         "a plain file is not a worksite")
        nowhere = os.path.join(self.tmp, "not-a-save")
        os.makedirs(os.path.join(nowhere, "worksites", "lookalike"))
        with self.assertRaises(NBTError) as caught:
            list_worksites(nowhere)
        self.assertIn("no level.dat", str(caught.exception))

    def test_the_note_a_person_left_is_carried(self):
        site = self.open_site()
        manifest = self.manifest_of(site)
        manifest["note"] = "pruned the region -1 copies by hand"
        del manifest["covered"]                 # as written before sidecars
        with open(os.path.join(site.directory, "manifest.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(manifest, handle)
        (entry,) = list_worksites(self.world)
        self.assertEqual(entry.note, "pruned the region -1 copies by hand")
        self.assertFalse(entry.sidecars_tracked)

    def test_a_box_or_margin_that_does_not_read_is_a_problem(self):
        """A record whose numbers are not numbers is damaged, and listing it
        must neither raise nor pass it off as a worksite that reads."""
        site = self.open_site()
        good = self.manifest_of(site)
        for key, value in (("box", [1, 2, 3]), ("box", ["a", 2, 3, 4]),
                           ("margin", "wide")):
            with self.subTest(**{key: value}):
                manifest = dict(good, **{key: value})
                with open(os.path.join(site.directory, "manifest.json"), "w",
                          encoding="utf-8") as handle:
                    json.dump(manifest, handle)
                (entry,) = list_worksites(self.world)
                self.assertTrue(entry.has_manifest)
                self.assertIsNotNone(entry.problem)
                self.assertIn("manifest.json", entry.problem)

    @needs_permissions
    def test_a_worksite_nobody_may_open_is_listed_as_damaged(self):
        """Unreadable is not absent: a worksite directory the process may
        not look into is a damaged record, never a hand-made backup, and a
        worksites directory it may not list is a refusal, never empty."""
        site = self.open_site()
        self.lock(site.directory)
        (entry,) = list_worksites(self.world)
        self.assertTrue(entry.has_manifest)
        self.assertIn("cannot be read", entry.problem)

        self.lock(os.path.dirname(site.directory))
        with self.assertRaises(NBTError) as caught:
            list_worksites(self.world)
        self.assertIn("cannot be read", str(caught.exception))


class CommandLineTests(WorksiteTestCase):
    """``python -m heaton_voxel worksite``: it looks, and only looks."""

    def run_main(self, *argv):
        import contextlib
        import io

        from heaton_voxel.__main__ import main

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(list(argv))
            except SystemExit as exit_:       # argparse refusing
                code = exit_.code
        return code, out.getvalue(), err.getvalue()

    def manifest(self, site: Worksite) -> dict:
        with open(os.path.join(site.directory, "manifest.json"),
                  encoding="utf-8") as handle:
            return json.load(handle)

    def build_and_save(self, name: str = "test-site",
                       block: str = "minecraft:gold_block") -> Worksite:
        site = Worksite.open(self.world, *BOX, name=name)
        x, y, z = INSIDE
        site.save.set_block(x, y, z, block)
        site.save.save()
        return site

    def test_list_names_every_worksite(self):
        self.open_site()
        code, out, err = self.run_main("worksite", "list", self.world)
        self.assertEqual(code, 0, err)
        self.assertIn("test-site", out)
        self.assertIn("minecraft:overworld", out)
        self.assertIn("(-344, 0)..(-320, 24)", out)

    def test_list_fails_on_a_manifest_it_cannot_read_but_not_on_a_backup_directory(self):
        self.open_site()
        os.makedirs(os.path.join(self.world, "worksites", "backup"))
        code, out, err = self.run_main("worksite", "list", self.world)
        self.assertEqual(code, 0, err)
        self.assertIn("not a worksite", out)

        broken = Worksite.open(self.world, *BOX, name="broken")
        with open(os.path.join(broken.directory, "manifest.json"), "w",
                  encoding="utf-8") as handle:
            handle.write("{not json")
        code, out, err = self.run_main("worksite", "list", self.world)
        self.assertEqual(code, 1, "damage must not exit clean")
        self.assertIn("UNREADABLE", out)
        self.assertIn("test-site", out)

    def test_rollback_looks_and_moves_nothing(self):
        self.build_and_save()
        created = os.path.join(self.world, ENTITIES_REL)
        os.makedirs(os.path.dirname(created), exist_ok=True)
        with open(created, "wb") as handle:
            handle.write(b"pretend entities")
        built = self.world_bytes(REGION_REL)

        code, out, err = self.run_main("worksite", "rollback", self.world,
                                       "test-site")
        self.assertEqual(code, 0, err)
        self.assertRegex(out, r"restore\s+" + re.escape(REGION_REL))
        self.assertRegex(out, r"delete\s+" + re.escape(ENTITIES_REL))
        self.assertIn("nothing moved", out)
        self.assertIn("Worksite.rollback(world, 'test-site')", out)
        self.assertEqual(built, self.world_bytes(REGION_REL))
        self.assertTrue(os.path.exists(created))

    def test_there_is_no_write_and_the_bytes_never_move(self):
        """Overwriting a played world stays a deliberate call from Python:
        the command has no flag that does it, and --dry-run is what it does
        anyway."""
        self.build_and_save()
        built = self.world_bytes(REGION_REL)

        code, out, err = self.run_main("worksite", "rollback", self.world,
                                       "test-site", "--write")
        self.assertEqual(code, 2)
        self.assertIn("unrecognized arguments: --write", err)
        self.assertEqual(out, "")
        self.assertEqual(built, self.world_bytes(REGION_REL))

        code, out, err = self.run_main("worksite", "rollback", self.world,
                                       "test-site", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("nothing moved", out)
        self.assertEqual(built, self.world_bytes(REGION_REL),
                         "looking is not leaping")

    def test_rollback_flags_files_written_after_the_build_was_accepted(self):
        """The mtime is a hint, not proof -- a plain copy resets it -- so
        the flag says *written*, never *played*."""
        site = self.open_site()
        site.accept()
        x, y, z = INSIDE
        site.save.set_block(x, y, z, "minecraft:gold_block")
        site.save.save()
        accepted = datetime.fromisoformat(
            self.manifest(site)["accepted"]).timestamp()
        target = os.path.join(self.world, REGION_REL)

        os.utime(target, (accepted + 60, accepted + 60))
        code, out, err = self.run_main("worksite", "rollback", self.world,
                                       "test-site")
        self.assertEqual(code, 0, err)
        self.assertIn("after the build was accepted", out)

        os.utime(target, (accepted - 60, accepted - 60))
        code, out, err = self.run_main("worksite", "rollback", self.world,
                                       "test-site")
        self.assertEqual(code, 0, err)
        self.assertNotIn("after the build was accepted", out)

    def test_rollback_names_later_worksites_it_would_undo(self):
        self.build_and_save("a-site").accept()
        # Its baseline holds a-site's build; it builds over that again.
        self.build_and_save("b-site", "minecraft:tnt")
        code, out, err = self.run_main("worksite", "rollback", self.world,
                                       "a-site")
        self.assertEqual(code, 0, err)
        self.assertIn("later worksites cover these files", out)
        self.assertIn("b-site (taken ", out)

        code, out, err = self.run_main("worksite", "rollback", self.world,
                                       "b-site")
        self.assertEqual(code, 0, err)
        self.assertNotIn("a-site", out,
                         "a build accepted before this snapshot is in it")

    def test_rollback_names_an_earlier_worksite_open_beside_it(self):
        """Taken first is not the same as saved first. Two sessions open
        over the same region at once: the later snapshot never held the
        earlier build, so undoing it undoes that build too, and the plan
        has to say so."""
        first = Worksite.open(self.world, *BOX, name="a-site")
        second = Worksite.open(self.world, *BOX, name="b-site")
        x, y, z = INSIDE
        first.save.set_block(x, y, z, "minecraft:gold_block")
        first.save.save()
        first.accept()
        second.save.set_block(x + 1, y, z, "minecraft:tnt")
        second.save.save()

        code, out, err = self.run_main("worksite", "rollback", self.world,
                                       "b-site")
        self.assertEqual(code, 0, err)
        self.assertIn("earlier worksites over these files whose builds may "
                      "also be undone", out)
        self.assertIn("a-site (taken ", out)

        # And it is so: the real undo takes a-site's block with it.
        Worksite.rollback(self.world, "b-site")
        self.assertNotEqual(
            str(MinecraftSave.load(self.world).get_block(x, y, z)),
            "minecraft:gold_block")

    def test_an_earlier_worksite_never_accepted_may_go_back_too(self):
        self.build_and_save("a-site")
        self.build_and_save("b-site", "minecraft:tnt")
        code, out, err = self.run_main("worksite", "rollback", self.world,
                                       "b-site")
        self.assertEqual(code, 0, err)
        self.assertIn("a-site (taken ", out)
        self.assertIn("never accepted", out)

    def test_rollback_names_a_worksite_it_could_not_check(self):
        """A later record that does not read cannot be ruled out, so it is
        named and the command does not exit clean -- the plan must not look
        like one with no later build over these files."""
        self.build_and_save("a-site")
        later = self.build_and_save("b-site", "minecraft:tnt")
        os.makedirs(os.path.join(self.world, "worksites", "backup"))
        manifest_path = os.path.join(later.directory, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        code, out, err = self.run_main("worksite", "rollback", self.world,
                                       "a-site")
        self.assertEqual(code, 1, "damage must not exit clean")
        self.assertIn("could not check b-site", out)
        self.assertIn("cannot be read as a worksite manifest", out)
        self.assertNotIn("backup", out, "a hand-made backup is no worksite")
        self.assertIn("nothing moved", out)

        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump({"files": {REGION_REL: None}, "created": "yesterday"},
                      handle)
        code, out, err = self.run_main("worksite", "rollback", self.world,
                                       "a-site")
        self.assertEqual(code, 1)
        self.assertIn("could not check b-site", out)
        self.assertIn("'yesterday' is not a date", out)

    def test_the_listing_and_the_plan_carry_the_caveats(self):
        """A note somebody left, a snapshot older than sidecar tracking, and
        a manifest recorded in another save are each a reason to read the
        plan twice, so each is printed, in the listing and the plan."""
        site = self.build_and_save()
        manifest = self.manifest(site)
        manifest["note"] = "pruned the region -1 copies by hand"
        del manifest["covered"]
        manifest["world"] = os.path.join(self.tmp, "elsewhere", "JeffWorld")
        with open(os.path.join(site.directory, "manifest.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(manifest, handle)

        code, out, err = self.run_main("worksite", "list", self.world)
        self.assertEqual(code, 0, err)
        self.assertIn("(note)", out)
        self.assertIn("(sidecars not tracked)", out)
        self.assertIn("a copied save)", out)

        code, out, err = self.run_main("worksite", "rollback", self.world,
                                       "test-site")
        self.assertEqual(code, 0, err)
        self.assertIn("note: pruned the region -1 copies by hand", out)
        self.assertIn(f"recorded in {manifest['world']}: a copied save", out)
        self.assertIn("predates sidecar tracking", out)

    def test_nothing_to_put_back_and_no_worksites_say_so(self):
        code, out, err = self.run_main("worksite", "list", self.world)
        self.assertEqual(code, 0, err)
        self.assertIn("no worksites under", out)

        self.open_site()
        code, out, err = self.run_main("worksite", "rollback", self.world,
                                       "test-site")
        self.assertEqual(code, 0, err)
        self.assertIn("nothing to put back", out)

    @needs_permissions
    def test_unreadable_paths_are_refusals_not_tracebacks(self):
        site = self.build_and_save()
        self.lock(os.path.join(site.directory, "files", REGION_REL))
        code, out, err = self.run_main("worksite", "rollback", self.world,
                                       "test-site")
        self.assertEqual(code, 2)
        self.assertIn("error:", err)
        self.assertNotIn("Traceback", err)

        self.lock(site.directory)
        code, out, err = self.run_main("worksite", "list", self.world)
        self.assertEqual(code, 1, "damage must not exit clean")
        self.assertIn("UNREADABLE", out)
        self.assertNotIn("not a worksite", out)

        self.lock(os.path.dirname(site.directory))
        code, out, err = self.run_main("worksite", "list", self.world)
        self.assertEqual(code, 2)
        self.assertIn("error:", err)
        self.assertIn("cannot be read", err)

    def test_rollback_refusals_are_clean(self):
        code, out, err = self.run_main("worksite", "rollback", self.world,
                                       "never-existed")
        self.assertEqual(code, 2)
        self.assertIn("no snapshot to go back to", err)
        self.assertNotIn("Traceback", err)

        site = self.open_site()
        with open(os.path.join(site.directory, "manifest.json"), "w",
                  encoding="utf-8") as handle:
            handle.write("{not json")
        code, out, err = self.run_main("worksite", "rollback", self.world,
                                       "test-site")
        self.assertEqual(code, 2)
        self.assertIn("cannot be read as a worksite manifest", err)

        code, out, err = self.run_main("worksite", "list", self.tmp)
        self.assertEqual(code, 2)
        self.assertIn("no level.dat", err)


class RetireTests(WorksiteTestCase):
    """Removing a settled build's snapshot: it removes that and nothing else,
    looks unless told otherwise, and refuses what it cannot judge."""

    def accepted(self, name: str = "test-site") -> Worksite:
        site = Worksite.open(self.world, *BOX, name=name)
        site.accept()
        return site

    def world_files(self) -> dict:
        """Every file of the save but its worksites, with its bytes."""
        found = {}
        for root, dirs, names in os.walk(self.world):
            if root == self.world:
                dirs[:] = [name for name in dirs if name != "worksites"]
            for name in names:
                path = os.path.join(root, name)
                with open(path, "rb") as handle:
                    found[path] = handle.read()
        return found

    def test_by_default_it_only_says_what_it_would_do(self):
        site = self.accepted()
        result = Worksite.retire(self.world, "test-site")
        self.assertTrue(result.dry_run)
        self.assertTrue(os.path.isdir(site.directory))
        self.assertEqual(result.name, "test-site")
        self.assertGreater(result.size, 0)
        self.assertIn(REGION_REL, result.files)
        self.assertIsNotNone(result.accepted)
        self.assertIn("would remove", repr(result))

    def test_it_removes_that_worksite_and_nothing_else(self):
        site = self.accepted()
        other = self.accepted("other-site")
        before = self.world_files()
        result = Worksite.retire(self.world, "test-site", dry_run=False)
        self.assertFalse(result.dry_run)
        self.assertFalse(os.path.exists(site.directory))
        self.assertTrue(os.path.isdir(other.directory))
        self.assertEqual(self.world_files(), before, "no world file moved")
        self.assertEqual([entry.name for entry in list_worksites(self.world)],
                         ["other-site"])
        self.assertEqual(os.listdir(os.path.join(self.world, "worksites")),
                         ["other-site"], "nothing left behind")

    def test_what_is_left_still_rolls_back(self):
        early = self.accepted("early")
        x, y, z = INSIDE
        early.save.set_block(x, y, z, "minecraft:gold_block")
        early.save.save()
        Worksite.open(self.world, *BOX, name="later", resnapshot=True).accept()
        Worksite.retire(self.world, "later", dry_run=False)
        Worksite.rollback(self.world, "early")
        self.assertNotEqual(MinecraftSave.load(self.world).get_block(x, y, z).name,
                            "minecraft:gold_block")

    def test_a_name_that_leaves_the_worksites_directory_is_refused(self):
        self.accepted()
        for name in ("", ".", "..", "../test-site", "a/b", "a\\b",
                     os.path.join(self.world, "worksites", "test-site"), None):
            with self.subTest(name=name):
                with self.assertRaises(NBTError) as caught:
                    Worksite.retire(self.world, name, dry_run=False)
                self.assertIn("not a worksite's name", str(caught.exception))
        self.assertTrue(os.path.isdir(os.path.join(self.world, "worksites",
                                                   "test-site")))

    def test_a_worksite_that_is_not_there_is_refused(self):
        with self.assertRaises(NBTError) as caught:
            Worksite.retire(self.world, "nobody", dry_run=False)
        self.assertIn("no worksite 'nobody'", str(caught.exception))

    def test_a_path_that_is_no_save_is_refused(self):
        with self.assertRaises(NBTError) as caught:
            Worksite.retire(self.tmp, "test-site", dry_run=False)
        self.assertIn("level.dat", str(caught.exception))

    def test_a_build_in_progress_is_refused(self):
        site = self.open_site()
        with self.assertRaises(NBTError) as caught:
            Worksite.retire(self.world, "test-site", dry_run=False)
        self.assertIn("never accepted", str(caught.exception))
        self.assertIn("by hand", str(caught.exception))
        self.assertTrue(os.path.isdir(site.directory))

    def test_a_rollback_does_not_make_a_build_accepted(self):
        self.open_site()
        Worksite.rollback(self.world, "test-site")
        with self.assertRaises(NBTError) as caught:
            Worksite.retire(self.world, "test-site")
        self.assertIn("never accepted", str(caught.exception))

    def test_a_backup_made_by_hand_is_refused(self):
        backup = os.path.join(self.world, "worksites", "backup")
        os.makedirs(os.path.join(backup, "files"))
        with self.assertRaises(NBTError) as caught:
            Worksite.retire(self.world, "backup", dry_run=False)
        self.assertIn("made by hand", str(caught.exception))
        self.assertTrue(os.path.isdir(backup))

    def test_a_manifest_that_does_not_read_is_refused(self):
        site = self.accepted()
        with open(os.path.join(site.directory, "manifest.json"), "w",
                  encoding="utf-8") as handle:
            handle.write("{not json")
        with self.assertRaises(NBTError) as caught:
            Worksite.retire(self.world, "test-site", dry_run=False)
        self.assertIn("cannot be retired", str(caught.exception))
        self.assertTrue(os.path.isdir(site.directory))

    def test_a_link_is_refused_and_what_it_points_at_left_alone(self):
        outside = os.path.join(self.tmp, "elsewhere")
        os.makedirs(outside)
        with open(os.path.join(outside, "keep"), "w", encoding="utf-8") as handle:
            handle.write("keep")
        base = os.path.join(self.world, "worksites")
        os.makedirs(base, exist_ok=True)
        try:
            os.symlink(outside, os.path.join(base, "link"))
        except OSError:
            self.skipTest("this file system has no links")
        with self.assertRaises(NBTError) as caught:
            Worksite.retire(self.world, "link", dry_run=False)
        self.assertIn("is a link", str(caught.exception))
        self.assertTrue(os.path.exists(os.path.join(outside, "keep")))

    def test_a_world_open_in_the_game_is_refused(self):
        site = self.accepted()
        import heaton_voxel.craft.worksite as module
        real = module.world_is_open
        module.world_is_open = lambda path: True
        self.addCleanup(setattr, module, "world_is_open", real)
        with self.assertRaises(NBTError) as caught:
            Worksite.retire(self.world, "test-site", dry_run=False)
        self.assertIn("open in Minecraft", str(caught.exception))
        self.assertTrue(os.path.isdir(site.directory))

    def test_a_delete_cut_short_leaves_no_snapshot_under_its_name(self):
        site = self.accepted()
        import heaton_voxel.craft.worksite as module
        real = module.shutil.rmtree

        def broken(path):
            raise OSError("the disk went away")

        module.shutil.rmtree = broken
        self.addCleanup(setattr, module.shutil, "rmtree", real)
        with self.assertRaises(NBTError) as caught:
            Worksite.retire(self.world, "test-site", dry_run=False)
        self.assertIn("only partly removed", str(caught.exception))
        self.assertIn(".retiring-test-site", str(caught.exception))
        self.assertFalse(os.path.exists(site.directory))
        module.shutil.rmtree = real
        with self.assertRaises(NBTError):
            Worksite.rollback(self.world, "test-site")

    def cut_short(self, name: str = "test-site") -> str:
        """A worksite whose delete got as far as its rename; its directory."""
        site = self.accepted(name)
        tomb = os.path.join(self.world, "worksites", f".retiring-{name}")
        os.replace(site.directory, tomb)
        return tomb

    def test_what_a_delete_cut_short_left_is_no_worksite(self):
        self.cut_short()
        (entry,) = list_worksites(self.world)
        self.assertFalse(entry.has_manifest, "its manifest is still there")
        self.assertIsNotNone(entry.size)
        self.assertEqual(entry.paths, ())

    def test_retiring_what_a_delete_cut_short_left_finishes_it(self):
        for lost in (False, True):
            with self.subTest(manifest_lost=lost):
                tomb = self.cut_short()
                if lost:
                    os.remove(os.path.join(tomb, "manifest.json"))
                Worksite.retire(self.world, ".retiring-test-site")
                self.assertTrue(os.path.isdir(tomb), "a dry run removes nothing")
                Worksite.retire(self.world, ".retiring-test-site", dry_run=False)
                self.assertFalse(os.path.exists(tomb))

    def test_an_earlier_delete_cut_short_does_not_stop_the_next(self):
        tomb = self.cut_short()
        with open(os.path.join(tomb, "old"), "w", encoding="utf-8") as handle:
            handle.write("old")
        site = self.accepted()
        Worksite.retire(self.world, "test-site", dry_run=False)
        self.assertFalse(os.path.exists(site.directory))
        self.assertFalse(os.path.exists(tomb))

    def test_a_directory_that_will_not_move_stands_as_it_was(self):
        site = self.accepted()
        import heaton_voxel.craft.worksite as module
        real = module.os.replace

        def broken(*args):
            raise OSError("the disk went away")

        module.os.replace = broken
        self.addCleanup(setattr, module.os, "replace", real)
        with self.assertRaises(NBTError) as caught:
            Worksite.retire(self.world, "test-site", dry_run=False)
        self.assertIn("stands as it was", str(caught.exception))
        self.assertTrue(os.path.isdir(site.directory))

    def test_a_summary_holds_its_size(self):
        self.accepted()
        os.makedirs(os.path.join(self.world, "worksites", "backup"))
        with open(os.path.join(self.world, "worksites", "backup", "f"), "wb") as handle:
            handle.write(b"x" * 1000)
        listed = {entry.name: entry for entry in list_worksites(self.world)}
        self.assertEqual(listed["backup"].size, 1000)
        self.assertGreater(listed["test-site"].size, 1000)

    @needs_permissions
    def test_a_size_that_cannot_be_counted_is_none_and_not_nothing(self):
        site = self.accepted()
        self.lock(os.path.join(site.directory, "files"))
        (entry,) = list_worksites(self.world)
        self.assertIsNone(entry.size)


class RetireCommandTests(CommandLineTests):
    """``worksite retire``: it looks, unless told ``--delete``."""

    def accepted(self, name: str, block: str = "minecraft:gold_block") -> Worksite:
        site = self.build_and_save(name, block)
        site.accept()
        return site

    def test_it_looks_and_removes_nothing(self):
        site = self.accepted("test-site")
        code, out, err = self.run_main("worksite", "retire", self.world,
                                       "test-site")
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isdir(site.directory))
        self.assertIn("dry run: nothing removed", out)
        self.assertRegex(out, r"worksite test-site: \d+(\.\d)? [KM]B in \d+ files")
        self.assertIn("no earlier snapshot", out)
        self.assertIn("no file of the world is touched", out)

    def test_delete_removes_it(self):
        site = self.accepted("test-site")
        code, out, err = self.run_main("worksite", "retire", self.world,
                                       "test-site", "--delete")
        self.assertEqual(code, 0, err)
        self.assertIn("removed test-site", out)
        self.assertFalse(os.path.exists(site.directory))

    def test_it_names_the_snapshots_over_the_same_files(self):
        self.accepted("first")
        self.accepted("second", "minecraft:iron_block")
        self.accepted("third", "minecraft:diamond_block")
        code, out, err = self.run_main("worksite", "retire", self.world, "second")
        self.assertEqual(code, 0, err)
        self.assertIn("earlier snapshots over these files stay", out)
        self.assertRegex(out, r"undoes this build, with everything since: "
                              r"first \(taken ")
        self.assertRegex(out, r"do not lean on this one: third \(taken ")

    def test_it_says_what_goes_with_it(self):
        site = Worksite.open(self.world, *BOX, name="test-site")
        site.protect((-330, 60, 6), (-326, 75, 10), name="shrine")
        site.accept()
        path = os.path.join(site.directory, "manifest.json")
        with open(path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest["note"] = "the old road"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        code, out, err = self.run_main("worksite", "retire", self.world,
                                       "test-site")
        self.assertEqual(code, 0, err)
        self.assertIn("shrine", out)
        self.assertIn("the old road", out)

    def test_another_manifest_that_does_not_read_fails_the_look(self):
        self.accepted("test-site")
        broken = self.accepted("broken")
        with open(os.path.join(broken.directory, "manifest.json"), "w",
                  encoding="utf-8") as handle:
            handle.write("{not json")
        code, out, err = self.run_main("worksite", "retire", self.world,
                                       "test-site")
        self.assertEqual(code, 1, "damage must not exit clean")
        self.assertIn("could not check broken", out)

    def test_refusals_are_clean(self):
        os.makedirs(os.path.join(self.world, "worksites", "backup"))
        for name, words in (("nobody", "no worksite"), ("backup", "made by hand"),
                            ("../..", "not a worksite's name")):
            with self.subTest(name=name):
                code, out, err = self.run_main("worksite", "retire", self.world,
                                               name, "--delete")
                self.assertEqual(code, 2)
                self.assertIn(words, err)
                self.assertNotIn("Traceback", err)

    def test_what_a_delete_cut_short_left_is_named_and_finished(self):
        site = self.accepted("test-site")
        tomb = os.path.join(self.world, "worksites", ".retiring-test-site")
        os.replace(site.directory, tomb)
        self.accepted("other")
        code, out, err = self.run_main("worksite", "list", self.world)
        self.assertEqual(code, 0, err)
        self.assertRegex(out, r"\.retiring-test-site +a retire cut short")
        code, out, err = self.run_main("worksite", "retire", self.world, "other")
        self.assertEqual(code, 0, err)
        self.assertNotIn("retiring-test-site", out, "no snapshot to lean on")
        code, out, err = self.run_main("worksite", "retire", self.world,
                                       ".retiring-test-site")
        self.assertEqual(code, 0, err)
        self.assertIn("nothing to lose", out)
        self.assertTrue(os.path.isdir(tomb))
        code, out, err = self.run_main("worksite", "retire", self.world,
                                       ".retiring-test-site", "--delete")
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(tomb))

    def test_list_says_how_big_each_one_is_and_the_total(self):
        self.accepted("test-site")
        backup = os.path.join(self.world, "worksites", "backup")
        os.makedirs(backup)
        with open(os.path.join(backup, "f"), "wb") as handle:
            handle.write(b"x" * 4096)
        code, out, err = self.run_main("worksite", "list", self.world)
        self.assertEqual(code, 0, err)
        self.assertRegex(out, r"not a worksite: no manifest\.json  4 KB")
        self.assertRegex(out, r"\d+(\.\d)? [KM]B in all")


class SnapshotIntegrityTests(WorksiteTestCase):
    def test_a_rotted_snapshot_is_refused_rather_than_written_out(self):
        """Restoring corruption over a world is worse than the build it
        was undoing, so the snapshot is checked before a byte moves."""
        site = self.open_site()
        built = self.world_bytes(REGION_REL)
        copy = os.path.join(site.directory, "files", REGION_REL)
        with open(copy, "r+b") as handle:
            handle.seek(4096)
            handle.write(b"\x00" * 64)

        with self.assertRaises(NBTError) as caught:
            site.restore()
        message = str(caught.exception)
        self.assertIn("not what it was when it was taken", message)
        self.assertIn(REGION_REL, message)
        self.assertEqual(built, self.world_bytes(REGION_REL),
                         "and the world is untouched")

    def test_a_missing_snapshot_file_is_refused_too(self):
        site = self.open_site()
        os.remove(os.path.join(site.directory, "files", REGION_REL))
        with self.assertRaises(NBTError) as caught:
            Worksite.rollback(self.world, "test-site")
        self.assertIn("missing from the snapshot", str(caught.exception))

    def test_the_check_runs_before_anything_is_deleted(self):
        """A rotten region must not cost the world its entities file on
        the way past: nothing moves until every file has been vouched for."""
        site = self.open_site()
        created = os.path.join(self.world, ENTITIES_REL)
        os.makedirs(os.path.dirname(created), exist_ok=True)
        with open(created, "wb") as handle:
            handle.write(b"pretend entities")
        with open(os.path.join(site.directory, "files", REGION_REL),
                  "r+b") as handle:
            handle.seek(4096)
            handle.write(b"\x00" * 64)

        with self.assertRaises(NBTError):
            site.restore()
        self.assertTrue(os.path.exists(created))


class GuardrailTests(WorksiteTestCase):
    def test_a_flipped_block_inside_trips_the_zone(self):
        site = self.open_site()
        site.protect((-330, 60, 6), (-326, 75, 10), name="shrine")
        self.assertEqual(site.check(), [])
        # In-memory only: the guard must catch damage before it is saved.
        site.save.set_block(-328, 70, 8, "minecraft:tnt")
        self.assertEqual(site.check(), ["shrine"])

    def test_edits_beside_the_zone_pass(self):
        site = self.open_site()
        site.protect((-330, 60, 6), (-326, 75, 10), name="shrine")
        site.save.set_block(-340, 70, 20, "minecraft:tnt")
        self.assertEqual(site.check(), [])

    def test_an_absurd_zone_is_refused(self):
        site = self.open_site()
        with self.assertRaises(NBTError) as caught:
            site.protect((0, 0, 0), (999, 10, 100), name="everything")
        self.assertIn("too slow", str(caught.exception))


class EndingTests(WorksiteTestCase):
    def test_render_writes_a_png(self):
        site = self.open_site()
        path = site.render("before", scale=1)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(8), b"\x89PNG\r\n\x1a\n")

    def test_accept_stamps_the_manifest(self):
        site = self.open_site()
        self.assertIn("open", repr(site))
        site.accept()
        with open(os.path.join(site.directory, "manifest.json"),
                  encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertIsNotNone(manifest["accepted"])
        self.assertIn("accepted", repr(site))


# -- the build inside it -------------------------------------------------------

STONE = "minecraft:stone"
BRICKS = "minecraft:stone_bricks"
REGION_DIR = "dimensions/minecraft/overworld/region"
ENTITIES_DIR = "dimensions/minecraft/overworld/entities"
POI_DIR = "dimensions/minecraft/overworld/poi"
#: A post somebody set by hand, its north arm reaching for nothing: what a
#: connect pass over it would correct.
FENCE_NORTH = ("minecraft:oak_fence[east=false,north=true,south=false,"
               "waterlogged=false,west=false]")


def _digests(world: str) -> dict[str, str]:
    """Every file of a world but its worksites, world-relative -> sha256."""
    import hashlib

    found = {}
    for root, directories, names in os.walk(world):
        if root == world and "worksites" in directories:
            directories.remove("worksites")
        for name in names:
            path = os.path.join(root, name)
            with open(path, "rb") as handle:
                found[os.path.relpath(path, world)] = hashlib.sha256(
                    handle.read()).hexdigest()
    return found


class SyntheticSiteCase(unittest.TestCase):
    """A save made from scratch, stone to y=63: the nine chunks around the
    origin, which are all of regions (-1..0, -1..0), and three chunks out
    east -- (30, 0) and (31, 0), the last columns of region (0, 0), and
    (40, 0) in region (1, 0). A site over chunk (0, 0) with the default
    margin holds regions (-1..0, -1..0) and nothing of region (1, 0)."""

    CHUNKS = ([(x, z) for x in range(-1, 2) for z in range(-1, 2)]
              + [(30, 0), (31, 0), (40, 0)])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="site-builder-tests-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.world = os.path.join(self.tmp, "world")
        make_save(self.world, self.CHUNKS)
        save = MinecraftSave.load(self.world)
        for corner1, corner2 in (((-16, 60, -16), (31, 63, 31)),
                                 ((480, 60, 0), (511, 63, 15)),
                                 ((640, 60, 0), (655, 63, 15))):
            save.fill(corner1, corner2, STONE)
            save.rebuild_heightmaps(corner1, corner2)
        save.save()

    def build_world(self, *cells) -> None:
        """Write ``(x, y, z, state)`` cells into the world on disk, before
        any worksite is opened over it."""
        save = MinecraftSave.load(self.world)
        for x, y, z, state in cells:
            self.assertTrue(save.set_block(x, y, z, state))
        save.save()

    def open_site(self, name: str = "build", **kwargs) -> Worksite:
        return Worksite.open(self.world, (0, 0), (15, 15), name=name, **kwargs)


class UncoveredCheckTests(SyntheticSiteCase):
    """check() names what a restore would leave behind: every file save()
    would write that the snapshot does not hold, beside the zones."""

    def test_check_names_a_file_the_snapshot_does_not_cover(self):
        site = self.open_site(margin=0)          # region (0, 0) alone
        site.save.set_block(645, 64, 5, BRICKS)  # region (1, 0)
        site.save.set_block(5, 64, 5, BRICKS)    # held
        found = site.check()
        outside = os.path.join(REGION_DIR, "r.1.0.mca")
        self.assertEqual(found.uncovered, [outside])
        self.assertEqual(found.zones, [])
        self.assertTrue(found, "something is wrong, and it says so")
        self.assertEqual(found, [outside])

    def test_an_entity_spawned_outside_the_snapshot_is_named(self):
        site = self.open_site(margin=0)
        self.assertTrue(site.save.spawn_entity("minecraft:cow", 650.5, 64.0, 5.5))
        self.assertEqual(site.check().uncovered,
                         [os.path.join(ENTITIES_DIR, "r.1.0.mca")])

    def test_reading_outside_the_snapshot_is_not_a_change(self):
        site = self.open_site(margin=0)
        self.assertEqual(site.save.get_block(645, 63, 5).name, STONE)
        self.assertIn((1, 0), site.save.dimension().regions, "it was loaded")
        found = site.check()
        self.assertEqual(found.uncovered, [])
        self.assertEqual(found, [])

    def test_a_poi_record_dropped_outside_the_snapshot_is_named(self):
        """invalidate_poi on a chunk the snapshot does not hold rewrites a
        POI file a restore would leave as it is."""
        directory = os.path.join(self.world, POI_DIR)
        os.makedirs(directory)
        region = NBT()
        region.kind = "region"
        region.region_x, region.region_z = 1, 0
        record = Chunk(40 & 31, 0)
        record.data = TagCompound({"DataVersion": TagInt(4903),
                                   "Sections": TagCompound()})
        region.chunks[(40 & 31, 0)] = record
        region.save(os.path.join(directory, "r.1.0.mca"))
        site = self.open_site(margin=0)
        self.assertEqual(site.check(), [], "loading it is not changing it")
        self.assertEqual(site.save.invalidate_poi((640, 0, 0), (655, 0, 15)), 1)
        self.assertEqual(site.check().uncovered,
                         [os.path.join(POI_DIR, "r.1.0.mca")])

    def test_check_lists_exactly_what_save_would_write_outside_the_snapshot(self):
        """A held region, the weather, the player and the difficulty: save()
        writes four files, and the three the snapshot does not hold are
        exactly what check() named before it."""
        world = make_world(os.path.join(self.tmp, "fixture"))
        site = Worksite.open(world, *BOX, name="fixture-site")
        site.save.set_block(*INSIDE, "minecraft:gold_block")
        site.save.set_weather("rain")
        site.save.set_player_position(-330.5, 80.0, 8.5)
        site.save.set_difficulty("hard")
        uncovered = site.check().uncovered

        before = _digests(world)
        site.save.save()
        after = _digests(world)
        written = {rel for rel, digest in after.items()
                   if before.get(rel) != digest}
        self.assertIn(REGION_REL, written)
        self.assertEqual(written - set(site._manifest["files"]), set(uncovered))
        self.assertEqual(len(uncovered), 3, uncovered)


class SiteBuilderTests(SyntheticSiteCase):
    """site.builder(): a Builder that cannot write where the rollback could
    not follow it, or into a protected zone."""

    def test_the_builder_is_bound_to_the_site(self):
        site = self.open_site()
        b = site.builder(seed=3, allow=("create:",))
        self.assertIs(b.save, site.save)
        self.assertEqual(b.dimension, site.dimension)
        self.assertIsNotNone(b.guard)
        self.assertEqual(b.allow, frozenset({"create:"}))
        self.assertEqual(b.rng.random(), Builder(site.save, seed=3).rng.random())
        same = Builder(site.save, guard=b.guard)
        self.assertEqual(same.dimension, site.dimension, "the site's, adopted")
        with self.assertRaises(NBTError):
            Builder(site.save, dimension="minecraft:the_nether", guard=b.guard)
        with self.assertRaises(NBTError):
            Builder(MinecraftSave.load(self.world), guard=b.guard)

    def test_the_builder_carries_the_palette(self):
        """site.builder(palette=) is the Builder's own palette=: the one
        given, read by the legacy writers, and the writers' old defaults
        when none is."""
        from heaton_voxel.craft.style import DEFAULT_PALETTE, Palette

        site = self.open_site()
        palette = Palette(floor=BRICKS)
        b = site.builder(palette=palette)
        self.assertIs(b.palette, palette)
        self.assertIsNotNone(b.guard, "still the site's guarded builder")
        b.floor(2, 2, 4, 4, 64)
        self.assertEqual(site.save.get_block(3, 64, 3).name, BRICKS)
        self.assertIs(site.builder().palette, DEFAULT_PALETTE)
        with self.assertRaises(TypeError):
            site.builder(palette={"floor": BRICKS})

    def test_a_write_inside_a_protected_zone_is_refused_and_writes_nothing(self):
        site = self.open_site()
        b = site.builder()
        # Protected after the builder was made: the zones are read live.
        site.protect((6, 64, 6), (7, 66, 7), name="shrine")
        cases = {
            "set": lambda: b.set(6, 64, 6, "minecraft:gold_block"),
            "fill": lambda: b.fill((2, 64, 2), (6, 64, 6), "minecraft:gold_block"),
            "the state already there": lambda: b.set(7, 65, 7, "minecraft:air"),
        }
        for kind, call in cases.items():
            with self.subTest(kind):
                with self.assertRaises(Refused) as caught:
                    call()
                self.assertTrue(any("'shrine'" in fault
                                    for fault in caught.exception.faults))
                self.assertEqual(b.blocks_set, 0)
                self.assertEqual(site.diff((0, 60, 0), (15, 70, 15)), {})
                self.assertEqual(site.check(), [])

    def test_a_write_outside_the_snapshot_is_refused(self):
        site = self.open_site()
        b = site.builder()
        with self.assertRaises(Refused) as caught:
            b.set(645, 64, 5, BRICKS)
        self.assertIn(os.path.join(REGION_DIR, "r.1.0.mca"),
                      caught.exception.faults[0])
        self.assertEqual(site.save.get_block(645, 64, 5).name, "minecraft:air")
        self.assertEqual(site.check(), [], "nothing for a restore to miss")

    def test_a_write_whose_settling_leaves_the_snapshot_is_refused(self):
        """Chunk (31, 0) is the last column of a held region. finish()
        relights the chunks around a write and settles connections a block
        across the border, into region (1, 0), which no restore could take
        back -- so the write is refused, though its own file is held."""
        site = self.open_site()
        b = site.builder()
        with self.assertRaises(Refused) as caught:
            b.set(500, 64, 5, BRICKS)
        named = [fault for fault in caught.exception.faults
                 if os.path.join(REGION_DIR, "r.1.0.mca") in fault]
        self.assertEqual(len(named), 1, caught.exception.faults)
        self.assertIn("finish() settles the chunks around", named[0])
        self.assertEqual(b.blocks_set, 0)
        b.set(484, 64, 5, BRICKS)        # chunk (30, 0): its ring is held
        self.assertEqual(b.blocks_set, 1)

    def test_a_spawn_outside_the_snapshot_is_refused(self):
        site = self.open_site()
        b = site.builder()
        drawn = b.rng.getstate()
        with self.assertRaises(Refused) as caught:
            b.spawn("minecraft:cow", 645, 64, 5)
        self.assertIn(os.path.join(ENTITIES_DIR, "r.1.0.mca"),
                      str(caught.exception))
        self.assertEqual(b.entities_added, 0)
        self.assertEqual(b.rng.getstate(), drawn, "no yaw was drawn")
        self.assertEqual(site.check(), [])
        b.spawn("minecraft:cow", 5, 64, 5)
        self.assertEqual(b.entities_added, 1)

    def test_a_bed_whose_head_would_land_in_a_zone_is_refused_whole(self):
        site = self.open_site()
        site.protect((4, 64, 5), (4, 64, 5), name="cot")
        b = site.builder()
        with self.assertRaises(Refused) as caught:
            b.place(4, 64, 4, "minecraft:red_bed[facing=south]")
        self.assertIn("'cot'", str(caught.exception))
        self.assertEqual(site.save.get_block(4, 64, 4).name, "minecraft:air",
                         "the foot, outside the zone, is not written either")
        self.assertEqual(b.blocks_set, 0)

    def test_a_placement_that_would_reconnect_a_protected_fence_is_refused(self):
        """place_block settles the fence beside a fence it places, so
        placing one beside a protected post would rewrite the post."""
        self.build_world((6, 64, 6, FENCE_NORTH))
        site = self.open_site()
        site.protect((6, 64, 6), (6, 64, 6), name="post")
        site.protect((9, 63, 9), (9, 63, 9), name="ground")
        b = site.builder()
        with self.assertRaises(Refused) as caught:
            b.place(7, 64, 6, "minecraft:oak_fence")
        self.assertIn("'post'", str(caught.exception))
        self.assertEqual(str(site.save.get_block(6, 64, 6)), FENCE_NORTH)
        self.assertEqual(site.save.get_block(7, 64, 6).name, "minecraft:air")
        self.assertEqual(b.blocks_set, 0)
        # On a protected stone, which no placement re-derives, it is admitted.
        b.place(9, 64, 9, "minecraft:oak_fence")
        self.assertEqual(site.check(), [])

    def last_write(self, run):
        """The cell ``run`` writes last, found on a throwaway load: the
        lower tier's writes recorded in the order they are made."""
        save = MinecraftSave.load(self.world)
        order = []
        set_block, fill, place_block = save.set_block, save.fill, save.place_block

        def record_set(x, y, z, *args, **kwargs):
            order.append((x, y, z))
            return set_block(x, y, z, *args, **kwargs)

        def record_fill(corner1, corner2, *args, **kwargs):
            order.append(tuple(max(a, b) for a, b in zip(corner1, corner2)))
            return fill(corner1, corner2, *args, **kwargs)

        def record_place(x, y, z, *args, **kwargs):
            order.append((x, y, z))
            return place_block(x, y, z, *args, **kwargs)

        save.set_block, save.fill = record_set, record_fill
        save.place_block = record_place
        run(Builder(save, seed=7))
        self.assertGreater(len(order), 1)
        return order[-1]

    def test_every_writer_refuses_before_its_first_block(self):
        """A zone holding only the last cell a writer writes refuses the
        whole call: admitted cell by cell, everything before it would
        already be in the save."""
        writers = {
            "pave": lambda b: b.pave(0, 0, 9, 9, light_level=MELT_SNOW),
            "shaft": lambda b: b.shaft((8, 8), 64, 70, wall=BRICKS),
            "level_pad": lambda b: b.level_pad(2, 2, 8, 8, 65),
            "gable_roof": lambda b: b.gable_roof(2, 2, 8, 6, 64, "z"),
            "railway": lambda b: b.railway(Railway([(0, 64, 8), (9, 64, 8)])),
            "road": lambda b: b.road([(0, 4), (12, 4)], width=3, mix=LANE_MIX),
        }
        for writer, run in writers.items():
            with self.subTest(writer=writer):
                cell = self.last_write(run)
                site = self.open_site(name=f"last-{writer}")
                site.protect(cell, cell, name="last")
                b = site.builder(seed=7)
                drawn = b.rng.getstate()
                with self.assertRaises(Refused) as caught:
                    run(b)
                self.assertIn(f"{cell} is inside protected zone 'last'",
                              caught.exception.faults)
                self.assertEqual(b.blocks_set, 0)
                self.assertEqual(site.diff((-16, 56, -16), (31, 80, 31)), {})
                if writer in ("road", "pave"):
                    # A road's surface is drawn as it is written, so a road
                    # the guard refuses has drawn nothing; a floor's is
                    # drawn as it is planned, and put back when refused.
                    self.assertEqual(b.rng.getstate(), drawn)

    def test_line_lamps_passes_over_a_post_the_guard_would_refuse(self):
        """A zone along a road's south verge, where its right-hand posts
        would stand: line_lamps passes each place in it over, as it passes
        over one in a pad, rather than refusing the call over one post. On
        the right alone that leaves nowhere, and the call is refused,
        naming the zone, with nothing written; with both sides allowed the
        north verge lights the road and the zone is as it was."""
        site = self.open_site()
        site.protect((-16, 60, 7), (31, 80, 7), name="verge")
        b = site.builder(seed=7)
        points = [(-12, 5), (26, 5)]
        lamp = {"post": "minecraft:cobblestone_wall",
                "lamp": "minecraft:lantern", "light_level": NO_SPAWNS}
        with self.assertRaises(Refused) as caught:
            b.line_lamps(points, side="right", **lamp)
        for words in ("nowhere to stand a post", "worksite 'build'",
                      "inside protected zone 'verge'"):
            self.assertIn(words, str(caught.exception))
        self.assertEqual(b.blocks_set, 0)
        self.assertEqual(site.diff((-16, 56, -16), (31, 80, 31)), {})
        result = b.line_lamps(points, **lamp)
        self.assertTrue(result.posts)
        self.assertEqual({z for _, _, z in result.posts}, {3})
        self.assertEqual(result.passed_over, 41, "every place in the zone")
        self.assertEqual(site.check(), [])

    def test_finish_refuses_when_settling_would_change_a_zone(self):
        """A write a block from a protected post is admitted -- it is not in
        the zone -- but finish() re-derives the post from its neighbors.
        That is refused before the heightmaps, POI and light are touched."""
        self.build_world((4, 64, 4, FENCE_NORTH))
        site = self.open_site()
        site.protect((4, 64, 4), (4, 64, 4), name="post")
        b = site.builder()
        b.set(5, 64, 5, BRICKS)
        with self.assertRaises(Refused) as caught:
            b.finish()
        message = str(caught.exception)
        self.assertIn("'post'", message)
        self.assertIn("finish(connect=False)", message)
        self.assertEqual(site.save.surface_y(5, 5, "WORLD_SURFACE"), 63,
                         "the heightmaps were never rebuilt")
        self.assertEqual(str(site.save.get_block(4, 64, 4)), FENCE_NORTH,
                         "the refusal put the post back as it found it")
        self.assertEqual(site.check(), [])

        # Refused once is refused every time it is asked ...
        with self.assertRaises(Refused):
            b.finish()
        self.assertEqual(str(site.save.get_block(4, 64, 4)), FENCE_NORTH)
        # ... and the finish the refusal names, on this same builder,
        # leaves the zone as it is.
        b.finish(connect=False)
        self.assertEqual(site.save.surface_y(5, 5, "WORLD_SURFACE"), 64)
        self.assertEqual(str(site.save.get_block(4, 64, 4)), FENCE_NORTH)
        self.assertEqual(site.check(), [])

    def test_finish_watches_a_zone_across_a_chunk_border(self):
        """The write is in chunk (1, 0); the post a block west of it is in
        chunk (0, 0), which nothing wrote in but connect's margin reaches."""
        self.build_world((15, 64, 4, FENCE_NORTH))
        site = self.open_site()
        site.protect((15, 64, 4), (15, 64, 4), name="post")
        b = site.builder()
        b.set(16, 64, 4, BRICKS)
        self.assertEqual(b.journal.chunks(), ((1, 0),))
        with self.assertRaises(Refused) as caught:
            b.finish()
        self.assertIn("'post'", str(caught.exception))
        self.assertEqual(str(site.save.get_block(15, 64, 4)), FENCE_NORTH)
        self.assertEqual(site.check(), [])

    def test_finish_refuses_to_settle_outside_the_snapshot(self):
        """Every guarded write was admitted with the chunks around it, so
        this fires only for a write that went round the guard -- straight
        through the save, journaled by hand -- and before any pass runs."""
        site = self.open_site()
        b = site.builder()
        site.save.set_block(500, 64, 5, BRICKS)      # chunk (31, 0)
        b.journal.record(500, 64, 5)
        with self.assertRaises(Refused) as caught:
            b.finish()
        self.assertIn("would settle outside", str(caught.exception))
        self.assertEqual(
            [fault.split()[0] for fault in caught.exception.faults],
            [os.path.join(REGION_DIR, "r.1.-1.mca"),
             os.path.join(REGION_DIR, "r.1.0.mca")],
            "the two regions east of it, which the site does not hold")
        self.assertIn("around chunk (31, 0)", caught.exception.faults[0])
        self.assertEqual(site.save.surface_y(500, 5, "WORLD_SURFACE"), 63,
                         "nothing was settled")
        self.assertEqual(site.check().uncovered, [],
                         "and nothing reached the region next door")

    def test_a_restored_site_stops_the_builders_it_made(self):
        site = self.open_site()
        b = site.builder()
        b.set(4, 64, 4, BRICKS)
        b.reserve(2, 2, 8, 8, name="yard")
        site.restore()
        calls = {
            "set": lambda: b.set(5, 64, 5, BRICKS),
            "spawn": lambda: b.spawn("minecraft:cow", 5, 64, 5),
            "finish": b.finish,
            "a new Builder on its guard": lambda: Builder(b.guard.save,
                                                          guard=b.guard),
            # Each of these a live builder would refuse on its own merits,
            # with a Refused a script might catch and carry on past; a dead
            # one is dead first.
            "a state the game does not have": lambda: b.set(
                5, 64, 5, "minecraft:chain"),
            "past the top of the world": lambda: b.set(5, 400, 5, BRICKS),
            "a chunk never generated": lambda: b.set(100, 64, 5, BRICKS),
            "a road through a reservation": lambda: b.road(
                [(0, 4), (12, 4)], width=3),
            "lamps hung outside the room": lambda: b.hang_lamps(
                0, 0, 9, 9, 72, 64, columns=[(20, 20)], light_level=8),
            # And what reads the undone world, or would save it.
            "a read of the ground": lambda: b.ground(3, 3),
            "its save": lambda: b.save,
        }
        for name, call in calls.items():
            with self.subTest(name):
                with self.assertRaises(NBTError) as caught:
                    call()
                self.assertNotIsInstance(caught.exception, Refused,
                                         "a dead builder, not a refusal")
                self.assertIn("restored", str(caught.exception))
        fresh = site.builder()
        fresh.set(5, 64, 5, BRICKS)
        self.assertEqual(site.save.get_block(5, 64, 5).name, BRICKS)
        self.assertEqual(site.save.get_block(4, 64, 4).name, "minecraft:air")

    def test_a_restored_site_stops_a_palette_writer_before_its_materials(self):
        """A family lacking the form a writer needs is an IllegalBlock, a
        Refused a script may catch and carry on past; from a builder whose
        site has been restored it is the dead builder's NBTError first,
        before the materials are resolved, as every other call is."""
        from heaton_voxel.craft.style import Material, Palette

        site = self.open_site()
        b = site.builder(palette=Palette(floor=BRICKS))
        site.restore()
        calls = {
            "a family with no pane": lambda: b.window(
                5, 65, 5, "x", block=Material("minecraft:stone")),
            "a family with no stairs or slab": lambda: b.gable_roof(
                2, 2, 6, 6, 68, "x", stair=Material("minecraft:calcite"),
                slab=Material("minecraft:calcite")),
            "a material left to the palette": lambda: b.floor(2, 2, 4, 4, 64),
            "a surface left to the palette": lambda: b.road(
                [(0, 4), (12, 4)], width=3),
        }
        for name, call in calls.items():
            with self.subTest(name):
                with self.assertRaises(NBTError) as caught:
                    call()
                self.assertNotIsInstance(caught.exception, Refused,
                                         "a dead builder, not a refusal")
                self.assertIn("restored", str(caught.exception))

    def test_a_guarded_build_encodes_exactly_like_an_unguarded_one(self):
        """The guard only reads: the same script through Builder(save) and
        through site.builder(), with nothing refused, saves the same bytes."""
        twin = os.path.join(self.tmp, "twin")
        shutil.copytree(self.world, twin)

        def script(b):
            b.level_pad(2, 2, 8, 8, 65, name="yard")
            b.road([(-10, 12), (12, 12)], width=3, mix=LANE_MIX)
            b.pave(10, -10, 14, -2, light_level=MELT_SNOW)
            b.shaft((-8, -8), 64, 70, wall=BRICKS)
            b.place(4, 66, 4, "minecraft:red_bed[facing=south]")
            b.finish()

        plain = MinecraftSave.load(twin)
        script(Builder(plain, seed=7))
        plain.save()
        site = self.open_site()
        script(site.builder(seed=7))
        self.assertEqual(site.check(), [])
        site.save.save()
        names = sorted(os.listdir(os.path.join(self.world, REGION_DIR)))
        self.assertIn("r.0.0.mca", names)
        for name in names:
            with self.subTest(region=name):
                with open(os.path.join(self.world, REGION_DIR, name), "rb") as a, \
                        open(os.path.join(twin, REGION_DIR, name), "rb") as b:
                    self.assertEqual(a.read(), b.read())


class DiffTests(WorksiteTestCase):
    """site.diff(): what the build changed, block by block, with 'before'
    read from the snapshot's own copies."""

    AROUND = ((-334, 55, 2), (-322, 75, 14))      # one chunk, (-21, 0)
    GLASS = [(-330, 62, 6), (-330, 62, 7), (-329, 62, 6), (-329, 62, 7)]

    def build(self, site) -> None:
        site.save.set_block(*INSIDE, "minecraft:gold_block")
        site.save.fill(self.GLASS[0], self.GLASS[-1], "minecraft:glass")

    def test_diff_reads_before_from_the_snapshot(self):
        original = MinecraftSave.load(self.world)
        site = self.open_site()
        self.build(site)
        found = site.diff(*self.AROUND)
        self.assertIsInstance(found, craft.SiteDiff)
        self.assertEqual(found.box, (-334, 55, 2, -322, 75, 14))
        self.assertEqual(set(found), {INSIDE, *self.GLASS})
        self.assertEqual(list(found), sorted(found))
        self.assertEqual(found[INSIDE], craft.CellChange(
            original.get_block(*INSIDE), BlockState("minecraft:gold_block")))
        for cell in self.GLASS:
            self.assertEqual(found[cell], craft.CellChange(
                original.get_block(*cell), BlockState("minecraft:glass")))
        self.assertIn("4 minecraft:water[level=0] -> minecraft:glass",
                      repr(found))
        self.assertEqual(site._rotten_snapshot(), [], "the snapshot is only read")

    def test_diff_still_knows_before_after_the_build_is_saved(self):
        """A witness read from the world after the save has no 'before'
        left to read; the snapshot still has it."""
        site = self.open_site()
        self.build(site)
        in_memory = site.diff(*self.AROUND)
        site.save.save()
        fresh = MinecraftSave.load(self.world)
        self.assertEqual(site.diff(*self.AROUND, save=fresh), in_memory)
        self.assertEqual(len(in_memory), 5)

    def test_a_state_spelled_short_is_the_state_it_was(self):
        """The fixture is a 26.2 chunk, which keeps a state as it was
        spelled: ``water`` written over ``water[level=0]`` is the same
        block, and not a change -- as a 26.3 chunk, which decodes every
        state whole, would say on its own."""
        site = self.open_site()
        cell = self.GLASS[0]
        self.assertEqual(str(site.save.get_block(*cell)), "minecraft:water[level=0]")
        site.save.set_block(*cell, "minecraft:water")
        self.assertEqual(str(site.save.get_block(*cell)), "minecraft:water",
                         "the legacy codec keeps the spelling")
        self.assertEqual(site.diff(*self.AROUND), {})
        site.save.set_block(*cell, "minecraft:water[level=1]")
        self.assertEqual(set(site.diff(*self.AROUND)), {cell})

    def test_diff_is_empty_after_restore(self):
        site = self.open_site()
        self.build(site)
        site.save.save()
        self.assertEqual(len(site.diff(*self.AROUND)), 5)
        site.restore()
        self.assertEqual(site.diff(*self.AROUND), {})

    def test_diff_refuses_what_it_cannot_answer(self):
        site = self.open_site()
        elsewhere = os.path.join(self.tmp, "elsewhere")
        shutil.copytree(self.world, elsewhere)
        other = MinecraftSave.load(elsewhere)
        refusals = {
            "a corner with no y": (lambda: site.diff((-330, 6), (-326, 10)),
                                   "has no y"),
            "a region the snapshot does not hold": (
                lambda: site.diff((2000, 60, 0), (2001, 61, 1)), "r.3.0.mca"),
            "a save of another world": (
                lambda: site.diff(*self.AROUND, save=other), "another world"),
        }
        for name, (call, said) in refusals.items():
            with self.subTest(name):
                with self.assertRaises(NBTError) as caught:
                    call()
                self.assertIn(said, str(caught.exception))

        copy = os.path.join(site.directory, "files", REGION_REL)
        manifest = os.path.join(site.directory, "manifest.json")
        damage = {
            "a rotted snapshot copy": (copy, "not what it was when it was taken"),
            "a manifest that does not read": (
                manifest, "cannot be read as a worksite manifest"),
        }
        for name, (path, said) in damage.items():
            with self.subTest(name):
                with open(path, "rb") as handle:
                    kept = handle.read()
                with open(path, "r+b") as handle:
                    handle.seek(0 if path == manifest else 4096)
                    handle.write(b"\x00" * 64)
                try:
                    with self.assertRaises(NBTError) as caught:
                        site.diff(*self.AROUND)
                    self.assertIn(said, str(caught.exception))
                finally:
                    with open(path, "wb") as handle:
                        handle.write(kept)
        self.assertEqual(site.diff(*self.AROUND), {}, "and sound again, it answers")

    def test_diff_refuses_a_rotted_sidecar_copy(self):
        """An oversized chunk of a region the box reads lives in a sidecar,
        so a rotted copy of that is a rotted 'before' too."""
        sidecar = SidecarTests.KEPT                      # region (-1, 0)
        with open(os.path.join(self.world, sidecar), "wb") as handle:
            handle.write(b"oversized chunk")
        site = self.open_site()
        self.assertIn(sidecar, site._manifest["files"])
        with open(os.path.join(site.directory, "files", sidecar), "wb") as handle:
            handle.write(b"something else")
        with self.assertRaises(NBTError) as caught:
            site.diff(*self.AROUND)
        self.assertIn(sidecar, str(caught.exception))
        self.assertIn("not what it was when it was taken", str(caught.exception))

    def test_a_fractional_corner_is_the_block_it_is_in(self):
        """-330.5 is in block -331, as every block coordinate is floored;
        truncated, it would name -330, the block beside it."""
        site = self.open_site()
        site.save.set_block(-331, 62, 6, "minecraft:gold_block")
        self.assertEqual(list(site.diff((-330.5, 62, 6), (-330.5, 62.5, 6.5))),
                         [(-331, 62, 6)])
        site.protect((-330.5, 61.5, 6), (-329, 62, 6.5), name="edge")
        self.assertEqual(site._manifest["zones"]["edge"]["box"],
                         [-331, 61, 6, -329, 62, 6])
        self.assertEqual(site.check(), [], "digested with the gold in it")

    def test_an_unreadable_chunk_is_an_error_not_an_empty_diff(self):
        site = self.open_site()
        chunk = site.save.dimension().chunk(-21, 0)
        chunk.data = TagCompound({"DataVersion": TagInt(4903)})   # no sections
        with self.assertRaises(NBTError) as caught:
            site.diff(*self.AROUND)
        self.assertIn("(-21, 0)", str(caught.exception))
        self.assertIn("cannot be read in the save", str(caught.exception))


class DiffWitnessTests(SyntheticSiteCase):
    """The witnesses three finished builds wrote by hand, as diffs: what the
    frozen lake, the lake and the park each checked before they saved."""

    def test_changed_equals_planned_as_the_frozen_lake_checks_it(self):
        save = MinecraftSave.load(self.world)
        save.fill((0, 63, 0), (8, 63, 8), "minecraft:water")
        save.save()
        site = self.open_site()
        b = site.builder()
        planned = {(2, 63, 2), (4, 63, 6), (6, 63, 3)}
        for cell in sorted(planned):
            b.place(*cell, "minecraft:sea_pickle")
        found = site.diff((0, 58, 0), (8, 64, 8))
        self.assertEqual(set(found), planned)
        for change in found.values():
            self.assertEqual(change.before, BlockState("minecraft:water"))
            self.assertEqual(change.after.name, "minecraft:sea_pickle")
            self.assertEqual(change.after.properties["waterlogged"], "true")
        b.set(7, 63, 7, STONE)                   # not in the plan
        self.assertEqual(set(site.diff((0, 58, 0), (8, 64, 8))) - planned,
                         {(7, 63, 7)})

    def test_named_blocks_unchanged_as_the_lake_witness_checks_it(self):
        keep = [(3, 62, 3), (5, 62, 5)]
        self.build_world(*[(*cell, "minecraft:blackstone") for cell in keep])
        site = self.open_site()
        b = site.builder()
        b.fill((2, 63, 2), (6, 63, 6), "minecraft:water")   # over them
        b.set(4, 62, 4, "minecraft:water")                  # between them

        def witness():
            return [cell for cell, change in
                    site.diff((0, 58, 0), (8, 66, 8)).items()
                    if change.before is not None
                    and "blackstone" in change.before.name]

        self.assertEqual(witness(), [])
        b.set(5, 62, 5, "minecraft:water")
        self.assertEqual(witness(), [(5, 62, 5)])

    def test_a_swapped_log_is_caught_where_a_count_is_not(self):
        """The park counted its grove's logs before and after. A log felled
        here and one set there, or one turned on its side, keeps the count;
        the diff names each cell."""
        log = "minecraft:pale_oak_log[axis=y]"
        self.build_world((2, 64, 2, log), (2, 65, 2, log), (6, 64, 6, log))
        site = self.open_site()

        def count():
            return sum(1 for x in range(16) for y in range(60, 71)
                       for z in range(16)
                       if site.save.get_block(x, y, z).name
                       == "minecraft:pale_oak_log")

        before = count()
        b = site.builder()
        b.set(6, 64, 6, "minecraft:air")
        b.set(9, 64, 9, log)
        b.set(2, 65, 2, "minecraft:pale_oak_log[axis=x]")
        self.assertEqual(count(), before, "the count cannot see it")
        lost = [cell for cell, change in
                site.diff((0, 60, 0), (15, 70, 15)).items()
                if change.before is not None
                and change.before.name == "minecraft:pale_oak_log"]
        self.assertEqual(lost, [(2, 65, 2), (6, 64, 6)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
