#!/usr/bin/env python3
"""
Module: tests/test_clean_manual_edit.py
Purpose: Unit, adversarial and integration tests for
         voyagertools.clean_manual_edit against a real manualedit.jobstore
         and a temporary registry root.
Inputs:  one temporary registry root per test (VICARIUS_3D_REGISTRY_ROOT plus
         importlib.reload), a JobStore on the same root, an edit bench folder.
Outputs: unittest results; every temporary directory is removed in tearDown.

Run from the clone root:
    python3 -m unittest discover -s tests -q
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
VICARIUS_ROOT = os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius")
REGISTRY_LIB_DIR = os.path.join(VICARIUS_ROOT, "_METADATA", "3d")
MANUAL_EDIT_CLONE = os.path.join(VICARIUS_ROOT, "modules", "manual_edit", "github_repo")
for path in (REGISTRY_LIB_DIR, MANUAL_EDIT_CLONE):
    if path not in sys.path:
        sys.path.insert(0, path)

from manualedit import jobstore  # noqa: E402
from manualedit import registry_contract  # noqa: E402
from voyagertools import clean_manual_edit as cme  # noqa: E402

HOST = "146.226.147.140"
SHELF_PARENT = "/volume6/Archive8_12TB/driver_deposits/run1"
RID = "MRS_T1_2023ann"
FOLDER = "MRS_T1_3D"  # the one processing folder of MRS T1, named for site and transect alone


class Pure(unittest.TestCase):
    def test_has_host_prefix(self):
        self.assertFalse(cme._has_host_prefix("/volume6/Archive8_12TB/x"))
        self.assertTrue(cme._has_host_prefix(f"{HOST}:/volume6/Archive8_12TB/x"))
        self.assertFalse(cme._has_host_prefix(""))

    def test_constants_match_the_manual_edit_clone(self):
        self.assertEqual(cme.PHASE_PULLING, jobstore.PULLING)
        self.assertEqual(cme.PHASE_PUSHING, jobstore.PUSHING)
        self.assertEqual(cme.PHASE_VERIFYING, jobstore.VERIFYING)
        self.assertEqual(cme.PHASE_CANCELLED, jobstore.CANCELLED)
        self.assertEqual(cme.FAILED_PREFIX, jobstore.FAILED_PREFIX)
        self.assertEqual(cme.STATUS_COLUMN, registry_contract.STATUS_COLUMN)
        self.assertEqual(cme.DONE_COLUMN, registry_contract.DONE_COLUMN)
        self.assertEqual(cme.STATUS_AWAITING, registry_contract.STATUS_AWAITING)

    def test_actor(self):
        self.assertEqual(cme.actor("lo"), "voyager_tools:LO")
        for bad in ("", "L O", "LO;x", "a" * 9):
            with self.assertRaises(ValueError):
                cme.actor(bad)
        with self.assertRaises(TypeError):
            cme.actor(None)

    def test_reached_pushing(self):
        self.assertFalse(cme.reached_pushing({"job_id": "j", "phase": "editing", "phase_history": []}))
        self.assertTrue(cme.reached_pushing({"job_id": "j", "phase": "failed_pushing"}))
        self.assertTrue(cme.reached_pushing({"job_id": "j", "phase": "editing",
                                             "phase_history": [{"phase": "pushing"}]}))
        self.assertFalse(cme.reached_pushing({"job_id": "j", "phase": "editing", "phase_history": "junk"}))
        with self.assertRaises(TypeError):
            cme.reached_pushing({"phase": "editing"})

    def test_refusals(self):
        self.assertIn("already done", cme.cancel_refusal({"job_id": "j", "phase": "done"}))
        self.assertIn("pushing right now", cme.cancel_refusal({"job_id": "j", "phase": "pushing"}))
        self.assertIn("pulling right now", cme.cancel_refusal({"job_id": "j", "phase": "pulling"}))
        self.assertEqual(cme.cancel_refusal({"job_id": "j", "phase": "editing"}), "")
        self.assertEqual(cme.cancel_refusal({"job_id": "j", "phase": "failed_pushing"}), "")

    def test_prune_targets_are_anchored(self):
        bench = tempfile.mkdtemp(prefix="vt-bench-")
        try:
            inside = os.path.join(bench, FOLDER)
            os.makedirs(inside)
            job = {"job_id": "j", "phase": "editing", "edit_bench": bench, "rows": [
                {"readable_id": RID, "local_path": inside, "processing_folder": FOLDER},
                {"readable_id": "X", "local_path": inside, "processing_folder": FOLDER},
                {"readable_id": "Y", "local_path": "/tmp/ZZZ_T9_3D"},
                {"readable_id": "Z", "local_path": os.path.join(bench, "notes")},
                {"readable_id": "W", "local_path": "", "processing_folder": "LBH_T2_3D"},
                {"readable_id": "V", "local_path": os.path.join(bench, "..", "ZZZ_T8_3D")},
                "junk",
            ]}
            self.assertEqual(cme.prune_targets(job),
                             [os.path.realpath(inside), os.path.realpath(os.path.join(bench, "LBH_T2_3D"))])
            self.assertEqual(cme.prune_targets({"job_id": "j", "phase": "editing", "edit_bench": "relative"}), [])
        finally:
            shutil.rmtree(bench, ignore_errors=True)

    def test_summarise_shape(self):
        job = {"job_id": "ME-1", "phase": "editing", "created_by": "LO", "created_at": "t", "edit_bench": "/b",
               "rows": [{"readable_id": RID, "site": "MRS", "transect": "T1", "shelf_location": "s"}],
               "phase_history": [{"phase": "draft", "at": "t", "by": "LO", "reason": ""}] * 12}
        summary = cme.summarise(job, tail_lines=["a", "b"])
        self.assertTrue(summary["can_cancel"])
        self.assertTrue(summary["will_prune"])
        self.assertEqual(len(summary["history"]), cme.HISTORY_TAIL)
        self.assertEqual(summary["log_tail"], ["a", "b"])
        self.assertEqual(summary["rows"][0]["site"], "MRS")
        pushed = cme.summarise(dict(job, phase="failed_pushing"))
        self.assertFalse(pushed["will_prune"])
        self.assertTrue(pushed["reached_pushing"])


class WithStores(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vt-cme-")
        os.environ["VICARIUS_3D_REGISTRY_ROOT"] = self.root
        import registry
        self.registry = importlib.reload(registry)
        self.bench = os.path.join(self.root, "editbench")
        os.makedirs(self.bench)
        self.store = jobstore.JobStore(self.root)
        self.shelf = f"{HOST}:{SHELF_PARENT}/{FOLDER}"
        self.registry.upsert(RID, {"site": "MRS", "transect": "T1", "year": "2023", "season_token": "ann",
                                   "step1_status": "complete", "manual_edit_status": "editing",
                                   "processing_location": os.path.join(self.bench, FOLDER),
                                   "processing_folder": FOLDER}, "seed")
        self.job = self.store.create_job(
            [{"readable_id": RID, "site": "MRS", "transect": "T1", "processing_folder": FOLDER,
              "shelf_location": f"{SHELF_PARENT}/{FOLDER}", "psx_basename": "MRS_T1_2023_2023.psx"}],
            self.bench, "LO", 1000, 5000)
        self.local = os.path.join(self.bench, FOLDER)
        os.makedirs(self.local)
        Path(self.local, "MRS_T1_2023_2023.psx").write_bytes(b"psx")

    def tearDown(self):
        os.environ.pop("VICARIUS_3D_REGISTRY_ROOT", None)
        shutil.rmtree(self.root, ignore_errors=True)
        import registry
        importlib.reload(registry)

    def advance(self, *phases):
        """Walk the job through phases; reaching certified passes the checks gate and calls certify()
        (jobstore.transition refuses "certified" directly: only certify() may record the statement)."""
        for phase in phases:
            if phase == "certified":
                self.store.transition(self.job["job_id"], "checking", "LO")
                self.store.finish_checks(self.job["job_id"], {"verdict": "pass", "items": [], "by_psx": {}}, "LO")
                self.store.certify(self.job["job_id"], "LO", "reviewed and certified for testing")
                continue
            self.store.transition(self.job["job_id"], phase, "LO", reason="test" if phase.startswith("failed_") else "")

    def test_cancel_before_pushing_prunes_and_restores(self):
        self.advance("pulling", "editing")
        result = cme.cancel_and_prune(self.store, self.registry, self.job["job_id"], "lo")
        self.assertEqual(result["phase"], "cancelled")
        self.assertEqual(result["pruned"], [os.path.realpath(self.local)])
        self.assertFalse(os.path.exists(self.local))
        row = self.registry.get(RID)
        self.assertEqual(row["processing_location"], f"{SHELF_PARENT}/{FOLDER}")
        self.assertEqual(row["manual_edit_status"], "awaiting")
        self.assertEqual(row["manual_edit_done"], "")
        self.assertIsNone(self.store.current())
        record = self.store.load_job(self.job["job_id"])
        self.assertEqual(record["phase"], "cancelled")
        self.assertTrue(record["cleanup"]["local_deleted"])
        self.assertEqual(record["cleanup"]["by"], "voyager_tools:LO")
        self.assertEqual(record["phase_history"][-1]["by"], "voyager_tools:LO")
        events = Path(self.root, "events.csv").read_text()
        self.assertIn("voyager_tools:LO", events)
        self.assertEqual(result["restored"][0], {"readable_id": RID, "location_restored": True,
                                                 "status_reset": True, "missing": False})

    def test_cancel_after_a_failed_push_keeps_the_local_copy(self):
        self.advance("pulling", "editing", "certified", "pushing", "failed_pushing")
        result = cme.cancel_and_prune(self.store, self.registry, self.job["job_id"], "LO")
        self.assertEqual(result["pruned"], [])
        self.assertEqual(result["kept"], [os.path.realpath(self.local)])
        self.assertTrue(os.path.exists(self.local))
        self.assertTrue(result["warnings"])
        self.assertEqual(self.store.load_job(self.job["job_id"])["phase"], "cancelled")

    def test_in_flight_and_terminal_refused(self):
        self.advance("pulling")
        with self.assertRaises(cme.CancelRefused):
            cme.cancel_and_prune(self.store, self.registry, self.job["job_id"], "LO")
        self.assertTrue(os.path.exists(self.local))
        self.assertEqual(self.registry.get(RID)["manual_edit_status"], "editing")
        self.advance("editing", "certified", "pushing")
        with self.assertRaises(cme.CancelRefused):
            cme.cancel_and_prune(self.store, self.registry, self.job["job_id"], "LO")
        self.advance("verifying", "done")
        with self.assertRaises(cme.CancelRefused):
            cme.cancel_and_prune(self.store, self.registry, self.job["job_id"], "LO")

    def test_unknown_job_and_bad_initials(self):
        with self.assertRaises(jobstore.JobNotFound):
            cme.cancel_and_prune(self.store, self.registry, "ME-20260101-000000-ZZ", "LO")
        with self.assertRaises(ValueError):
            cme.cancel_and_prune(self.store, self.registry, self.job["job_id"], "L O")
        self.assertIsNotNone(self.store.current())

    def test_prune_failure_reported_and_job_still_cancelled(self):
        self.advance("pulling", "editing")

        def boom(path):
            raise OSError("busy")
        result = cme.cancel_and_prune(self.store, self.registry, self.job["job_id"], "LO", rmtree_fn=boom)
        self.assertEqual(result["phase"], "cancelled")
        self.assertIn("Could not remove", result["problems"][0])
        self.assertEqual(result["kept"], [os.path.realpath(self.local)])
        self.assertFalse(self.store.load_job(self.job["job_id"])["cleanup"]["local_deleted"])

    def test_missing_registry_row_is_reported_not_fatal(self):
        self.registry.delete_row(RID, "seed")
        self.advance("pulling", "editing")
        result = cme.cancel_and_prune(self.store, self.registry, self.job["job_id"], "LO")
        self.assertTrue(result["restored"][0]["missing"])
        self.assertEqual(result["phase"], "cancelled")

    def test_restore_is_idempotent(self):
        self.registry.upsert(RID, {"processing_location": f"{SHELF_PARENT}/{FOLDER}",
                                   "manual_edit_status": "awaiting"}, "seed")
        before = Path(self.root, "events.csv").read_text()
        cme.restore_registry(self.job, self.registry, "voyager_tools:LO")
        self.assertEqual(Path(self.root, "events.csv").read_text(), before)

    def test_restore_prepends_shelf_prefix_to_a_bare_shelf_location(self):
        # the job's own recorded shelf_location has no host prefix (the
        # picker strips it): restore_registry must add one back so the
        # restored cell reads as a real Shelf path, not a local one.
        self.assertEqual(self.job["rows"][0]["shelf_location"], f"{SHELF_PARENT}/{FOLDER}")
        cme.restore_registry(self.job, self.registry, "voyager_tools:LO", shelf_prefix=f"{HOST}:")
        self.assertEqual(self.registry.get(RID)["processing_location"], f"{HOST}:{SHELF_PARENT}/{FOLDER}")

    def test_restore_leaves_an_already_prefixed_shelf_location_alone(self):
        prefixed_job = dict(self.job)
        prefixed_job["rows"] = [dict(self.job["rows"][0])]
        prefixed_job["rows"][0]["shelf_location"] = f"{HOST}:{SHELF_PARENT}/{FOLDER}"
        cme.restore_registry(prefixed_job, self.registry, "voyager_tools:LO", shelf_prefix=f"{HOST}:")
        self.assertEqual(self.registry.get(RID)["processing_location"], f"{HOST}:{SHELF_PARENT}/{FOLDER}")

    def test_restore_with_an_empty_prefix_keeps_todays_bare_behaviour(self):
        cme.restore_registry(self.job, self.registry, "voyager_tools:LO")
        self.assertEqual(self.registry.get(RID)["processing_location"], f"{SHELF_PARENT}/{FOLDER}")

    def test_cancel_and_prune_forwards_shelf_prefix(self):
        self.advance("pulling", "editing")
        result = cme.cancel_and_prune(self.store, self.registry, self.job["job_id"], "LO",
                                      shelf_prefix=f"{HOST}:")
        self.assertTrue(result["restored"][0]["location_restored"])
        self.assertEqual(self.registry.get(RID)["processing_location"], f"{HOST}:{SHELF_PARENT}/{FOLDER}")


if __name__ == "__main__":
    unittest.main()
