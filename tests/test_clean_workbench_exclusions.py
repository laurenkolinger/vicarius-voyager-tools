#!/usr/bin/env python3
"""
Module: tests/test_clean_workbench_exclusions.py
Purpose: The walk exclusions of program D Amendment 1 (A1.1, A1.14) in
         voyagertools.clean_workbench: the Voyager 1 Workbench walk, the
         anchor and the delete never reach the Edit bench, the Voyager 2
         stage, the VIGIL stage or the in-flight transfer ledger, even when
         a folder there carries a Voyager 1 processing folder name.
Inputs:  temporary folders laid out like /mnt/rip/driver_staging; no ssh, no NAS.
Outputs: unittest results; every temporary directory is removed in tearDown.

Run from the clone root:
    python3 -m unittest discover -s tests -q
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from voyagertools import clean_workbench as cw  # noqa: E402

FOLDER = "MRS_T1_3D"


def not_live(_row):
    """The liveness probe of a folder nobody is processing."""
    return False


class Layout(unittest.TestCase):
    """A Workbench with the three other stages and the ledger inside it, as the defaults place them."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vt-wbx-")
        self.workbench = self.mk()
        self.v1 = self.mk("batch_20260927-010000", "turn001", "2025_pbl", FOLDER)
        self.bench = self.mk("editbench")
        self.v2 = self.mk("voyager2_stage")
        self.vigil = self.mk("vigil_stage")
        self.ledger = self.mk(".transfers_in_flight")
        self.excludes = {"Edit bench": self.bench, "Voyager 2 stage": self.v2, "VIGIL stage": self.vigil,
                         "transfer ledger": self.ledger}

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def mk(self, *parts):
        path = os.path.join(self.root, *parts)
        os.makedirs(path, exist_ok=True)
        return path


class Walk(Layout):
    def test_a_processing_folder_under_each_excluded_root_is_never_listed(self):
        planted = [self.mk("editbench", FOLDER),
                   self.mk("voyager2_stage", "batch_v2_20260927-020000", "turn001", FOLDER),
                   self.mk("vigil_stage", "batch_vg_20260927-030000", FOLDER),
                   self.mk(".transfers_in_flight", FOLDER)]
        found = cw.find_processing_folders([self.workbench], exclude_roots=self.excludes)
        paths = [f["path"] for f in found["folders"]]
        self.assertEqual(paths, [self.v1])
        for path in planted:
            self.assertNotIn(path, paths)

    def test_without_exclusions_the_old_walk_would_list_the_bench_copy(self):
        """The hazard the exclusions close: the bench copy matches the Voyager 1 name at depth one."""
        bench_copy = self.mk("editbench", FOLDER)
        paths = [f["path"] for f in cw.find_processing_folders([self.workbench])["folders"]]
        self.assertIn(bench_copy, paths)

    def test_hostile_voyager1_named_folder_inside_the_edit_bench(self):
        """A bench folder laid out exactly like a Voyager 1 turn is still skipped."""
        hostile = self.mk("editbench", "batch_20260927-010000", "turn001", "2025_pbl", "LBH_T2_3D")
        paths = [f["path"] for f in cw.find_processing_folders([self.workbench], exclude_roots=self.excludes)["folders"]]
        self.assertNotIn(hostile, paths)

    def test_an_exclusion_reached_through_a_symlink_is_still_excluded(self):
        """The bench named in the settings is a symlink to the real folder the walk meets."""
        link = os.path.join(self.root, "bench_link")
        os.symlink(self.bench, link)
        self.mk("editbench", FOLDER)
        found = cw.find_processing_folders([self.workbench], exclude_roots={"Edit bench": link})
        self.assertEqual([f["path"] for f in found["folders"]], [self.v1])

    def test_a_workbench_root_inside_an_exclusion_is_reported_and_not_scanned(self):
        inner = self.mk("editbench", "old")
        self.mk("editbench", "old", FOLDER)
        found = cw.find_processing_folders([inner], exclude_roots=self.excludes)
        self.assertEqual(found["folders"], [])
        self.assertIn("Edit bench", found["problems"][0])

    def test_exclusions_are_validated(self):
        for bad in ("relative/path", "", 7, None):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    cw.find_processing_folders([self.workbench], exclude_roots=[bad])
        with self.assertRaises(TypeError):
            cw.find_processing_folders([self.workbench], exclude_roots="/one/string")

    def test_exclusions_as_a_plain_list_carry_a_generic_label(self):
        self.mk("editbench", FOLDER)
        normalised = cw.normalise_excludes([self.bench])
        self.assertEqual(normalised, [(cw.EXCLUDED_LABEL, self.bench)])
        found = cw.find_processing_folders([self.workbench], exclude_roots=[self.bench])
        self.assertNotIn(os.path.join(self.bench, FOLDER), [f["path"] for f in found["folders"]])

    def test_plan_reports_what_it_skipped(self):
        result = cw.plan([self.workbench], [], lambda p: {}, lambda p: {}, not_live, exclude_roots=self.excludes)
        self.assertEqual(sorted(e["label"] for e in result["excluded"]),
                         ["Edit bench", "VIGIL stage", "Voyager 2 stage", "transfer ledger"])
        self.assertEqual([f["path"] for f in result["folders"]], [self.v1])


class AnchorAndDelete(Layout):
    def test_a_delete_aimed_into_an_excluded_root_is_refused_and_nothing_moves(self):
        copy = self.mk("voyager2_stage", "batch_v2_20260927-020000", "turn001", FOLDER)
        bench_copy = self.mk("editbench", FOLDER)
        removed = []
        for path, label in ((copy, "Voyager 2 stage"), (bench_copy, "Edit bench")):
            with self.subTest(path=path):
                with self.assertRaises(ValueError) as ctx:
                    cw.delete_folder(path, [self.workbench], not_live, removed.append, exclude_roots=self.excludes)
                self.assertIn(label, str(ctx.exception))
                self.assertTrue(os.path.isdir(path))
        self.assertEqual(removed, [])

    def test_a_dotted_path_into_the_bench_is_refused(self):
        bench_copy = self.mk("editbench", FOLDER)
        dotted = os.path.join(self.workbench, "batch_20260927-010000", "..", "editbench", FOLDER)
        with self.assertRaises(ValueError):
            cw.anchored(dotted, [self.workbench], exclude_roots=self.excludes)
        self.assertTrue(os.path.isdir(bench_copy))

    def test_a_voyager1_folder_still_deletes(self):
        result = cw.delete_folder(self.v1, [self.workbench], not_live, shutil.rmtree, exclude_roots=self.excludes)
        self.assertEqual(result["deleted"], os.path.realpath(self.v1))
        self.assertFalse(os.path.exists(self.v1))

    def test_the_exclusion_is_optional_for_the_existing_callers(self):
        """The progress monitor calls delete_folder without exclusions; its turn folders still anchor."""
        self.assertEqual(cw.anchored(self.v1, [self.workbench]), os.path.realpath(self.v1))


if __name__ == "__main__":
    unittest.main()
