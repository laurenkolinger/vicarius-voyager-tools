#!/usr/bin/env python3
"""
Module: tests/test_clean_manual_edit.py
Purpose: Unit, adversarial and integration tests for tool 3, Clean manual
         edit run: voyagertools.clean_manual_edit.summarise, and the Cancel
         and prune it offers, which is the manual edit module's one cancel
         (manualedit.transfers.cancel_job with prune=True, hardening F14)
         run against a real manualedit.jobstore and a temporary registry
         root: every refusal a live pull, leg, Metashape, marked transect,
         atlas value or Mark done note brings, and a bench folder removed
         only when nobody opened or saved it, only through the bench removal.
Inputs:  one temporary registry root per test (VICARIUS_3D_REGISTRY_ROOT plus
         importlib.reload), a JobStore on the same root, an edit bench folder,
         a manual edit Deps kit with its own leg registries.
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
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
VICARIUS_ROOT = os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius")
REGISTRY_LIB_DIR = os.path.join(VICARIUS_ROOT, "_METADATA", "3d")
MANUAL_EDIT_CLONE = os.path.join(VICARIUS_ROOT, "modules", "manual_edit", "github_repo")
for path in (REGISTRY_LIB_DIR, MANUAL_EDIT_CLONE):
    if path not in sys.path:
        sys.path.insert(0, path)

from manualedit import jobstore, transfers, watcher  # noqa: E402
from voyagertools import clean_manual_edit as cme  # noqa: E402

sys.path.insert(0, str(HERE))
from status_seed import seed_status  # noqa: E402

SHELF_PARENT = "/volume6/Archive8_12TB/driver_deposits/run1"
RID = "MRS_T1_2023ann"
FOLDER = "MRS_T1_3D"  # the one processing folder of MRS T1, named for site and transect alone
PSX = "MRS_T1_2023_2023.psx"
JOB = {"job_id": "ME-20260927-010000-LO", "phase": "editing", "created_by": "LO",
       "created_at": "2026-09-27T01:00:00-04:00", "edit_bench": "/bench",
       "rows": [{"readable_id": RID, "site": "MRS", "site_name": "Meri Shoal", "transect": "T1",
                 "local_path": "/bench/MRS_T1_3D", "shelf_location": f"{SHELF_PARENT}/{FOLDER}"}],
       "phase_history": [{"phase": "draft", "at": "a", "by": "LO", "reason": ""}]}


class SummaryTests(unittest.TestCase):
    """summarise: what the page lists for one project, from the one cancel's judgement and plan."""

    def test_the_shape(self):
        summary = cme.summarise(JOB, ["one", "two"], refusal="", plan=[])
        self.assertEqual(set(summary), {"job_id", "phase", "created_by", "created_at", "edit_bench", "rows",
                                        "history", "log_tail", "prune_targets", "prune_kept", "refusal",
                                        "can_cancel"})
        self.assertEqual(summary["rows"][0]["site_name"], "Meri Shoal")
        self.assertEqual(summary["log_tail"], ["one", "two"])
        self.assertTrue(summary["can_cancel"])

    def test_the_plan_splits_into_folders_removed_and_sentences_kept(self):
        plan = [{"folder": FOLDER, "local_path": "/bench/MRS_T1_3D", "keep": ""},
                {"folder": "MRS_T2_3D", "local_path": "/bench/MRS_T2_3D", "keep": "MRS_T2_3D stays: it was saved."}]
        summary = cme.summarise(JOB, [], plan=plan)
        self.assertEqual(summary["prune_targets"], [FOLDER])
        self.assertEqual(summary["prune_kept"], ["MRS_T2_3D stays: it was saved."])

    def test_a_refusal_closes_the_cancel(self):
        summary = cme.summarise(JOB, [], refusal="The pull is still copying.")
        self.assertEqual((summary["refusal"], summary["can_cancel"]), ("The pull is still copying.", False))

    def test_no_plan_is_nothing_to_remove(self):
        summary = cme.summarise(JOB)
        self.assertEqual((summary["prune_targets"], summary["prune_kept"], summary["log_tail"]), ([], [], []))

    def test_the_history_keeps_its_tail(self):
        job = dict(JOB, phase_history=[{"phase": "editing", "at": str(i), "by": "LO", "reason": ""}
                                       for i in range(cme.HISTORY_TAIL + 5)] + ["junk"])
        history = cme.summarise(job)["history"]
        self.assertEqual(len(history), cme.HISTORY_TAIL)
        self.assertEqual(history[-1]["at"], str(cme.HISTORY_TAIL + 4))

    def test_rows_read_as_text_and_junk_rows_are_skipped(self):
        job = dict(JOB, rows=[{"readable_id": RID, "site": None, "transect": 7}, "junk"])
        rows = cme.summarise(job)["rows"]
        self.assertEqual(rows, [{"readable_id": RID, "site": "", "site_name": "", "transect": "7", "local_path": "",
                                 "shelf_location": ""}])

    def test_a_record_that_is_not_a_job_is_refused(self):
        for bad in (None, [], {"phase": "editing"}, {"job_id": 1, "phase": "editing"}, {"job_id": "j"}):
            with self.subTest(job=bad), self.assertRaises(TypeError):
                cme.summarise(bad)

    def test_a_refusal_that_is_not_text_is_refused(self):
        for bad in (None, 7, ["x"]):
            with self.subTest(refusal=bad), self.assertRaises(TypeError):
                cme.summarise(JOB, refusal=bad)

    def test_a_plan_that_is_not_a_list_of_entries_is_refused(self):
        for bad in ("x", {"folder": FOLDER}, ["x"], [None]):
            with self.subTest(plan=bad), self.assertRaises(TypeError):
                cme.summarise(JOB, plan=bad)

    def test_the_old_cancel_is_gone(self):
        for relic in ("cancel_and_prune", "restore_registry", "cancel_refusal", "registry_refusal", "reached_pushing",
                      "prune_targets", "CancelRefused", "CLEAN_MANUAL_EDIT_RESET", "IN_FLIGHT_PHASES",
                      "PUSH_PHASES", "PROCESSING_FOLDER_NAME", "actor"):
            self.assertFalse(hasattr(cme, relic), relic)

    def test_the_cancel_reason_names_this_page(self):
        self.assertEqual(cme.CANCEL_REASON, "cancelled from the Atlas control panel")


class CancelAndPruneTests(unittest.TestCase):
    """Cancel and prune through the one cancel, against a real job store and a scratch registry."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vt-cme-")
        os.environ["VICARIUS_3D_REGISTRY_ROOT"] = self.root
        import registry
        self.registry = importlib.reload(registry)
        self.bench = os.path.join(self.root, "editbench")
        os.makedirs(self.bench)
        self.store = jobstore.JobStore(self.root)
        seed_status(self.registry, RID, "editing", site="MRS", transect="T1", year="2023", season_token="ann",
                    step1_status="complete", processing_location=os.path.join(self.bench, FOLDER),
                    processing_folder=FOLDER)
        self.job = self.store.create_job(
            [{"readable_id": RID, "site": "MRS", "transect": "T1", "processing_folder": FOLDER,
              "shelf_location": f"{SHELF_PARENT}/{FOLDER}", "psx_basename": PSX}], self.bench, "LO")
        self.job_id = self.job["job_id"]
        self.local = os.path.join(self.bench, FOLDER)
        os.makedirs(self.local)
        Path(self.local, PSX).write_bytes(b"psx")
        old = time.time() - 3600
        os.utime(os.path.join(self.local, PSX), (old, old))
        self.deps = transfers.Deps(
            ssh_factory=None, transfer_fn=None, remote_spec_fn=None, archive_fn=None, launch_fn=None,
            run_async=None, watchers=watcher.WatcherRegistry(poll_s=60), site_names_fn=dict, snapshot_fn=None,
            metashape_executable_fn=None, legs=transfers.LegRegistry(), project_legs=transfers.ProjectLegRegistry())

    def tearDown(self):
        self.deps.watchers.stop_all(2)
        os.environ.pop("VICARIUS_3D_REGISTRY_ROOT", None)
        shutil.rmtree(self.root, ignore_errors=True)
        import registry
        importlib.reload(registry)

    def pulled(self):
        """Walk the job to editing the way the pull does."""
        self.store.transition(self.job_id, jobstore.PULLING, "LO")
        self.store.transition(self.job_id, jobstore.EDITING, "LO", transfers.PULL_VERIFIED_REASON)
        self.store.init_projects(self.job_id)

    def cancel(self):
        """Cancel and prune as the page's route does; returns the answer."""
        return transfers.cancel_job(self.deps, self.store, self.job_id, "LO", cme.CANCEL_REASON, prune=True)

    def assert_refused(self, fragment):
        """The cancel is refused with a sentence holding `fragment`; nothing moved."""
        phase = self.store.load_job(self.job_id)["phase"]
        with self.assertRaises((transfers.TransferRefused, jobstore.TransitionError)) as caught:
            self.cancel()
        self.assertIn(fragment, str(caught.exception))
        self.assertEqual(self.store.load_job(self.job_id)["phase"], phase)
        self.assertTrue(os.path.isdir(self.local))
        self.assertEqual(self.registry.get(RID)["manual_edit_status"], "editing")

    def test_an_untouched_copy_is_removed_and_the_row_goes_back_to_the_shelf(self):
        self.pulled()
        answer = self.cancel()
        self.assertEqual(answer["job"]["phase"], "cancelled")
        self.assertEqual((answer["rows_reset"], answer["removed"], answer["kept_on_bench"]), ([RID], [FOLDER], []))
        self.assertFalse(os.path.lexists(self.local))
        row = self.registry.get(RID)
        self.assertEqual(row["manual_edit_status"], "awaiting")
        self.assertEqual(row["processing_location"], transfers.SHELF_PREFIX + f"{SHELF_PARENT}/{FOLDER}")
        self.assertEqual(self.store.load_job(self.job_id)["phase_history"][-1]["reason"], cme.CANCEL_REASON)

    def test_a_copy_opened_in_metashape_stays_with_its_sentence(self):
        self.pulled()
        self.store.append_open_event(self.job_id, PSX, "open", by="AB")
        answer = self.cancel()
        self.assertTrue(os.path.isdir(self.local))
        self.assertIn("was opened in Metashape", answer["kept_reasons"][FOLDER])
        self.assertEqual(answer["kept_on_bench"], [FOLDER])

    def test_a_copy_saved_after_the_pull_stays(self):
        self.pulled()
        later = time.time() + 60
        os.utime(os.path.join(self.local, PSX), (later, later))
        answer = self.cancel()
        self.assertTrue(os.path.isdir(self.local))
        self.assertIn("was saved after the pull", answer["kept_reasons"][FOLDER])

    def test_a_copy_that_reached_editing_without_a_verified_pull_stays(self):
        self.store.transition(self.job_id, jobstore.PULLING, "LO")
        self.store.transition(self.job_id, jobstore.EDITING, "LO", "migration-B2: the phase checking was retired")
        answer = self.cancel()
        self.assertTrue(os.path.isdir(self.local))
        self.assertIn("records no verified pull", answer["kept_reasons"][FOLDER])

    def test_a_pull_still_copying_is_refused(self):
        self.store.transition(self.job_id, jobstore.PULLING, "LO")
        transfers.legs_of(self.deps).begin(self.job_id)
        self.assert_refused("still copying")

    def test_a_pull_a_restart_stranded_is_recovered_then_cancelled(self):
        self.store.transition(self.job_id, jobstore.PULLING, "LO")
        answer = self.cancel()
        phases = [entry["phase"] for entry in answer["job"]["phase_history"]]
        self.assertEqual(phases[-2:], ["failed_pulling", "cancelled"])

    def test_a_running_project_leg_is_refused(self):
        self.pulled()
        transfers.project_legs_of(self.deps).begin(self.job_id, PSX, "Check scales")
        self.assert_refused(f"Check scales is running on {PSX}")

    def test_a_metashape_holding_the_transect_is_refused(self):
        self.pulled()
        with mock.patch.object(transfers.sessions, "psx_held", return_value=True):
            self.assert_refused(f"Metashape holds {PSX}")

    def test_every_marked_transect_state_is_refused(self):
        self.pulled()
        for state, word in (("review", "marked for admin review"), ("upload", "marked for re-upload"),
                            ("uploading", "uploading"), ("restarting", "starting over")):
            with self.subTest(state=state):
                record = self.store.load_job(self.job_id)
                record["projects"][PSX]["state"] = state
                if state == "restarting":
                    record["projects"][PSX]["restart"] = {"stage": "space", "aside": "", "error": ""}
                self.store.save_job(record)
                self.assert_refused(word)

    def test_a_row_the_atlas_reads_as_marked_is_refused(self):
        self.pulled()
        for status in ("review", "upload"):
            with self.subTest(status=status):
                seed_status(self.registry, RID, status)
                with self.assertRaises(transfers.TransferRefused) as caught:
                    self.cancel()
                self.assertIn("in the atlas, so the project cannot be cancelled", str(caught.exception))
                self.assertEqual(self.store.load_job(self.job_id)["phase"], "editing")

    def test_a_value_vicarius_does_not_know_is_refused_until_repaired(self):
        self.pulled()
        for raw in (" editing ", "Editing", "certified"):
            with self.subTest(raw=raw):
                seed_status(self.registry, RID, raw)
                with self.assertRaises(transfers.TransferRefused) as caught:
                    self.cancel()
                self.assertIn("repair it from the atlas", str(caught.exception))
                self.assertTrue(os.path.isdir(self.local))

    def test_a_mark_done_note_of_this_round_is_refused(self):
        self.pulled()
        self.registry.append_notes([RID], self.job_id, PSX, 0, "editor_explanation", "scale", "One board only.", "AB",
                                   actor="manual_edit:AB", source_id="CR-20260927-100000-AB")
        self.assert_refused(f"{PSX} holds an explanation from Mark done")

    def test_a_note_of_an_earlier_round_refuses_nothing(self):
        self.pulled()
        self.registry.append_notes([RID], self.job_id, PSX, 1, "check_note", "", "Earlier round.", "AB",
                                   actor="manual_edit:AB", source_id="CR-20260927-100000-AB")
        self.assertEqual(self.cancel()["rows_reset"], [RID])

    def test_a_migration_note_refuses_nothing(self):
        self.pulled()
        self.registry.append_notes([RID], self.job_id, PSX, 0, "check_note", "", "Sent back for the checks.", "LO",
                                   actor="manual_edit:LO", source_id=f"{self.job_id}:{PSX}:migration-B0c")
        self.assertEqual(self.cancel()["rows_reset"], [RID])

    def test_a_row_with_no_registry_entry_refuses_nothing(self):
        self.pulled()
        rows = [row for row in self.registry._read_rows() if row["readable_id"] != RID]
        self.registry._write_rows(rows)
        answer = self.cancel()
        self.assertEqual((answer["job"]["phase"], answer["rows_reset"]), ("cancelled", []))

    def test_a_folder_a_transfer_holds_stays(self):
        self.pulled()
        with mock.patch.object(transfers, "_try_folder_lock", return_value=None):
            answer = self.cancel()
        self.assertTrue(os.path.isdir(self.local))
        self.assertIn("A transfer holds", answer["kept_reasons"][FOLDER])

    def test_a_folder_that_is_a_symlink_is_never_followed(self):
        self.pulled()
        real = os.path.join(self.root, "elsewhere", FOLDER)
        os.makedirs(os.path.dirname(real))
        shutil.move(self.local, real)
        os.symlink(real, self.local)
        answer = self.cancel()
        self.assertTrue(os.path.isdir(real))
        self.assertIn("symlink", answer["kept_reasons"][FOLDER])

    def test_an_unknown_job_is_not_found(self):
        with self.assertRaises(jobstore.JobNotFound):
            transfers.cancel_job(self.deps, self.store, "ME-20260101-000000-ZZ", "LO", prune=True)

    def test_initials_that_break_the_rule_are_refused_before_anything(self):
        self.pulled()
        for bad in ("", "L O", "L0", "ABCDE", None, 7):
            with self.subTest(initials=bad), self.assertRaises(ValueError):
                transfers.cancel_job(self.deps, self.store, self.job_id, bad, prune=True)
        self.assertEqual(self.store.load_job(self.job_id)["phase"], "editing")

    def test_a_finished_project_is_refused(self):
        self.pulled()
        self.cancel()
        with self.assertRaises(transfers.TransferRefused) as caught:
            self.cancel()
        self.assertIn("nothing to cancel", str(caught.exception))

    def test_the_watcher_is_stopped(self):
        self.pulled()
        self.deps.watchers.start(self.store, self.store.load_job(self.job_id))
        self.assertTrue(self.deps.watchers.running(self.job_id))
        self.cancel()
        self.assertFalse(self.deps.watchers.running(self.job_id))

    def test_the_job_log_names_what_went_and_what_stayed(self):
        self.pulled()
        self.store.append_open_event(self.job_id, PSX, "open", by="AB")
        self.cancel()
        line = self.store.tail_log(self.job_id, 5)[-1]
        self.assertIn("cancelled by LO", line)
        self.assertIn(f"left on the edit bench: {FOLDER}", line)


    def test_an_uploaded_transect_with_confirm_notes_does_not_hold_the_cancel(self):
        """An upload raises no round, so an uploaded transect's Confirm notes stay current and must hold nothing."""
        done_id, editing_id = "MRS_T2_2023ann", "MRS_T3_2023ann"
        done_psx, editing_psx = "MRS_T2_2023_2023.psx", "MRS_T3_2023_2023.psx"
        rows = []
        for rid, transect, psx in ((done_id, "T2", done_psx), (editing_id, "T3", editing_psx)):
            folder = f"MRS_{transect}_3D"
            seed_status(self.registry, rid, "editing", site="MRS", transect=transect, year="2023",
                        season_token="ann", step1_status="complete", processing_folder=folder,
                        processing_location=os.path.join(self.bench, folder))
            rows.append({"readable_id": rid, "site": "MRS", "transect": transect, "processing_folder": folder,
                         "shelf_location": f"{SHELF_PARENT}/{folder}", "psx_basename": psx})
        job_id = self.store.create_job(rows, self.bench, "AB")["job_id"]
        self.store.transition(job_id, jobstore.PULLING, "AB")
        self.store.transition(job_id, jobstore.EDITING, "AB", transfers.PULL_VERIFIED_REASON)
        record = self.store.init_projects(job_id)
        record["projects"][done_psx].update(state="done", removed_at="2026-09-27T21:30:00-04:00")
        self.store.save_job(record)
        seed_status(self.registry, done_id, "done", done_at="2026-09-27 21:25",
                    processing_location=transfers.SHELF_PREFIX + f"{SHELF_PARENT}/MRS_T2_3D")
        self.registry.append_notes([done_id], job_id, done_psx, 0, "check_note", "", "Explained at Confirm.", "AB",
                                   actor="manual_edit:AB", source_id="CR-20260927-210000-AB")
        editing_local = os.path.join(self.bench, "MRS_T3_3D")
        os.makedirs(editing_local)
        Path(editing_local, editing_psx).write_bytes(b"psx")
        old = time.time() - 3600
        os.utime(os.path.join(editing_local, editing_psx), (old, old))
        answer = transfers.cancel_job(self.deps, self.store, job_id, "LO", cme.CANCEL_REASON, prune=True)
        self.assertEqual(answer["job"]["phase"], "cancelled")
        self.assertEqual(answer["rows_reset"], [editing_id])
        self.assertEqual(self.registry.get(done_id)["manual_edit_status"], "done")
        self.assertEqual(self.registry.get(editing_id)["manual_edit_status"], "awaiting")
        self.assertEqual(answer["removed"], ["MRS_T3_3D"])

if __name__ == "__main__":
    unittest.main()
