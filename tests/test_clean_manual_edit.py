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

    def test_certified_project_states_match_the_manual_edit_clone(self):
        # Program B adds these transect states in B0c to B3; each one the job
        # store already defines must carry the word this module mirrors.
        for name, word in (("PROJECT_REVIEW", "review"), ("PROJECT_UPLOAD", "upload"),
                           ("PROJECT_UPLOADING", "uploading"), ("PROJECT_RESTARTING", "restarting")):
            with self.subTest(name=name):
                self.assertIn(word, cme.CERTIFIED_PROJECT_STATES)
                if hasattr(jobstore, name):
                    self.assertEqual(getattr(jobstore, name), word)
        self.assertEqual(len(cme.CERTIFIED_PROJECT_STATES), 4)

    def test_reset_name_is_one_the_registry_knows(self):
        import registry
        self.assertIn(cme.CLEAN_MANUAL_EDIT_RESET, registry.MANUAL_EDIT_RESETS)
        sources, targets = registry.MANUAL_EDIT_RESETS[cme.CLEAN_MANUAL_EDIT_RESET]
        self.assertEqual((sources, targets), ((registry.ME_EDITING,), (registry.ME_AWAITING,)))

    def test_no_status_literal_is_mirrored(self):
        # The status words come from the registry library, never from a copy here.
        for relic in ("STATUS_COLUMN", "DONE_COLUMN", "STATUS_AWAITING", "LOCATION_COLUMN"):
            self.assertFalse(hasattr(cme, relic), relic)

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

    def test_a_certified_transect_refuses_the_cancel(self):
        for state in cme.CERTIFIED_PROJECT_STATES:
            with self.subTest(state=state):
                job = {"job_id": "j", "phase": "editing",
                       "projects": {"MRS_T1_2023_2025.psx": {"state": "done"},
                                    "MRS_T2_2023_2025.psx": {"state": state}}}
                why = cme.cancel_refusal(job)
                self.assertEqual(why, "MRS_T2_2023_2025.psx carries a certified edit; decide it in the Review queue "
                                      "or upload it before cancelling.")
        for state in ("todo", "editing", "done", "failed", "", None):
            with self.subTest(state=state):
                job = {"job_id": "j", "phase": "editing", "projects": {"x.psx": {"state": state}}}
                self.assertEqual(cme.cancel_refusal(job), "")
        # a malformed projects map refuses nothing extra and never raises
        for junk in ("junk", ["x"], {"x.psx": "junk"}, None):
            with self.subTest(projects=junk):
                self.assertEqual(cme.cancel_refusal({"job_id": "j", "phase": "editing", "projects": junk}), "")

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
        """Walk the job through phases; reaching certified marks every project done, passes the checks
        gate and calls certify() (jobstore.transition refuses "certified" directly: only certify() may
        record the statement, and certify() refuses while any project is unfinished)."""
        for phase in phases:
            if phase == "certified":
                job_id = self.job["job_id"]
                for psx in self.store.load_job(job_id)["projects"]:
                    self.store.set_project_state(job_id, psx, jobstore.PROJECT_DONE, "LO")
                self.store.transition(job_id, "checking", "LO")
                self.store.finish_checks(job_id, {"verdict": "pass", "items": [], "by_psx": {}}, "LO")
                self.store.certify(job_id, "LO", "reviewed and certified for testing")
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
                                                 "status_reset": True, "missing": False, "status_kept": None})
        self.assertIn(f",voyager_tools:LO,{RID},manual_edit_status,editing,awaiting\n", events)

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

    def _write_raw_status(self, value):
        """Hand-edit the row's status cell the way a spreadsheet would (upsert refuses unknown values)."""
        rows = self.registry._read_rows()
        for r in rows:
            if r["readable_id"] == RID:
                r["manual_edit_status"] = value
        self.registry._write_rows(rows)

    def test_restore_moves_only_a_row_still_at_editing(self):
        shelf = f"{SHELF_PARENT}/{FOLDER}"
        relocated = self.registry.MANUAL_EDIT_VOYAGER1_ELIGIBLE + (self.registry.ME_EDITING,)
        for status in self.registry.MANUAL_EDIT_STATUSES + ("Reviewed",):
            with self.subTest(status=status):
                self.registry.upsert(RID, {"processing_location": self.local, "manual_edit_done": ""}, "seed")
                self._write_raw_status(status)
                result = cme.restore_registry(self.job, self.registry, "voyager_tools:LO")[0]
                row = self.registry.get(RID)
                if status == self.registry.ME_EDITING:
                    self.assertEqual(row["manual_edit_status"], self.registry.ME_AWAITING)
                    self.assertTrue(result["status_reset"])
                    self.assertIsNone(result["status_kept"])
                else:
                    self.assertEqual(row["manual_edit_status"], status)
                    self.assertFalse(result["status_reset"])
                    self.assertEqual(result["status_kept"], status)
                if status in relocated:
                    self.assertEqual(row["processing_location"], shelf)
                    self.assertTrue(result["location_restored"])
                else:
                    self.assertEqual(row["processing_location"], self.local)
                    self.assertFalse(result["location_restored"])

    def test_restore_keeps_a_done_stamp_it_does_not_own(self):
        self.registry.upsert(RID, {"manual_edit_status": "done", "manual_edit_done": "2026-09-26 21:07:00"}, "seed")
        cme.restore_registry(self.job, self.registry, "voyager_tools:LO")
        row = self.registry.get(RID)
        self.assertEqual((row["manual_edit_status"], row["manual_edit_done"]), ("done", "2026-09-26 21:07:00"))

    def test_restore_writes_through_the_named_reset_under_the_upsert_guard(self):
        self.registry.MANUAL_EDIT_UPSERT_GUARD = True
        cme.restore_registry(self.job, self.registry, "voyager_tools:LO")
        self.assertEqual(self.registry.get(RID)["manual_edit_status"], "awaiting")
        self._write_raw_status("done")
        cme.restore_registry(self.job, self.registry, "voyager_tools:LO")
        self.assertEqual(self.registry.get(RID)["manual_edit_status"], "done")

    def test_cancel_refused_while_the_registry_records_a_certified_edit(self):
        self.advance("pulling", "editing")
        for status in ("review", "upload"):
            with self.subTest(status=status):
                self._write_raw_status(status)
                before = Path(self.root, "events.csv").read_text()
                with self.assertRaises(cme.CancelRefused) as caught:
                    cme.cancel_and_prune(self.store, self.registry, self.job["job_id"], "LO")
                self.assertEqual(str(caught.exception),
                                 f"{RID} carries a certified edit (manual edit status {status}); decide it in the "
                                 f"Review queue or upload it before cancelling.")
                self.assertTrue(os.path.exists(self.local))
                self.assertEqual(self.registry.get(RID)["manual_edit_status"], status)
                self.assertEqual(Path(self.root, "events.csv").read_text(), before)
                self.assertEqual(self.store.load_job(self.job["job_id"])["phase"], "editing")

    def test_a_status_vicarius_does_not_know_refuses_the_cancel_under_the_upsert_guard(self):
        """The upsert guard compares the raw cell, so a padded " editing " would fail the restore write
        partway; the cancel refuses it before anything is written or removed."""
        self.registry.MANUAL_EDIT_UPSERT_GUARD = True
        self.advance("pulling", "editing")
        for value in (" editing ", "Reviewed", " awaiting", " "):
            with self.subTest(value=value):
                self._write_raw_status(value)
                before = Path(self.root, "events.csv").read_text()
                with self.assertRaises(cme.CancelRefused) as caught:
                    cme.cancel_and_prune(self.store, self.registry, self.job["job_id"], "LO")
                self.assertEqual(str(caught.exception),
                                 f"{RID} holds the manual edit status {value!r}, which VICARIUS does not know; "
                                 f"repair it from the atlas in admin mode first.")
                self.assertTrue(os.path.exists(self.local))
                self.assertEqual(self.registry.get(RID)["manual_edit_status"], value)
                self.assertEqual(Path(self.root, "events.csv").read_text(), before)
                self.assertEqual(self.store.load_job(self.job["job_id"])["phase"], "editing")
                summary = cme.summarise(self.store.load_job(self.job["job_id"]), registry_mod=self.registry)
                self.assertFalse(summary["can_cancel"])

    def test_restore_leaves_a_padded_editing_cell_alone_under_the_upsert_guard(self):
        self.registry.MANUAL_EDIT_UPSERT_GUARD = True
        self._write_raw_status(" editing ")
        result = cme.restore_registry(self.job, self.registry, "voyager_tools:LO")[0]
        self.assertFalse(result["status_reset"])
        self.assertFalse(result["location_restored"])
        self.assertEqual(result["status_kept"], " editing ")
        self.assertEqual(self.registry.get(RID)["manual_edit_status"], " editing ")

    def test_restore_moves_the_location_of_an_awaiting_row_only_off_the_bench(self):
        """A blank or awaiting row keeps its status; its location is restored only when it points at the
        edit bench copy the prune removes, never over a value someone else wrote."""
        elsewhere = f"{HOST}:/volume6/Archive8_12TB/driver_deposits/run2/{FOLDER}"
        for status in self.registry.MANUAL_EDIT_VOYAGER1_ELIGIBLE:
            with self.subTest(status=status):
                self.registry.upsert(RID, {"processing_location": elsewhere}, "seed")
                self._write_raw_status(status)
                result = cme.restore_registry(self.job, self.registry, "voyager_tools:LO")[0]
                self.assertFalse(result["location_restored"])
                self.assertEqual(self.registry.get(RID)["processing_location"], elsewhere)
                self.registry.upsert(RID, {"processing_location": self.local + "/"}, "seed")
                result = cme.restore_registry(self.job, self.registry, "voyager_tools:LO")[0]
                self.assertTrue(result["location_restored"])
                self.assertEqual(self.registry.get(RID)["processing_location"], f"{SHELF_PARENT}/{FOLDER}")

    def test_summarise_names_the_registry_refusal_when_given_the_registry(self):
        self._write_raw_status("review")
        summary = cme.summarise(self.store.load_job(self.job["job_id"]), registry_mod=self.registry)
        self.assertFalse(summary["can_cancel"])
        self.assertFalse(summary["will_prune"])
        self.assertIn("certified edit", summary["refusal"])
        self.assertTrue(cme.summarise(self.store.load_job(self.job["job_id"]))["can_cancel"])

    def test_cancel_and_prune_forwards_shelf_prefix(self):
        self.advance("pulling", "editing")
        result = cme.cancel_and_prune(self.store, self.registry, self.job["job_id"], "LO",
                                      shelf_prefix=f"{HOST}:")
        self.assertTrue(result["restored"][0]["location_restored"])
        self.assertEqual(self.registry.get(RID)["processing_location"], f"{HOST}:{SHELF_PARENT}/{FOLDER}")


if __name__ == "__main__":
    unittest.main()
