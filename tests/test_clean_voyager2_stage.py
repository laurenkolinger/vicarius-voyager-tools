#!/usr/bin/env python3
"""
Module: tests/test_clean_voyager2_stage.py
Purpose: Unit, adversarial and integration tests for
         voyagertools.clean_voyager2_stage: the bounded walk of the Voyager 2
         stage, the working copy rule (disposable only after a verified
         deposit with no turn reserving the transect), the output folder
         rule (deletable only when it matches the Shelf copy), test run
         copies never offered, and the anchored delete.
Inputs:  a temporary Voyager 2 stage, plain row dicts, in-memory facts and
         snapshots; no ssh, no NAS, no live registry.
Outputs: unittest results; every temporary directory is removed in tearDown.

Run from the clone root:
    python3 -m unittest discover -s tests -q
"""
from __future__ import annotations

import fcntl
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from voyagertools import clean_voyager2_stage as v2s  # noqa: E402
from voyagertools import clean_workbench as cw  # noqa: E402

HOST = "146.226.147.140"
SHELF_PARENT = "/volume6/Archive8_12TB/driver_deposits/run1"
PROC, OUT = "MRS_T1_3D", "MRS_T1_3doutput"
IDS = ("MRS_T1_2023ann", "MRS_T1_2025_pbl")


def not_live(_row):
    """The liveness probe of a folder nobody is processing."""
    return False


def walk(path):
    """A local snapshot shaped like driver.verify.local_snapshot."""
    out = {}
    for root, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(root, name)
            out[os.path.relpath(full, path)] = {"kind": "f", "size": os.path.getsize(full)}
    return out


def done_row(rid, **extra):
    """A row after a complete Voyager 2 turn whose deposit verified."""
    row = {"readable_id": rid, "site": "MRS", "transect": "T1", "processing_folder": PROC,
           "processing_location": f"{HOST}:{SHELF_PARENT}/{PROC}", "output_folder": OUT,
           "output_location": f"{HOST}:{SHELF_PARENT}/{OUT}", "output_location_verified": "2026-10-02 14:30 AST",
           "step2_status": "complete"}
    row.update(extra)
    return row


class Stage(unittest.TestCase):
    """A Voyager 2 stage with one finished turn and one test copy."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vt-v2s-")
        self.stage = os.path.join(self.root, "voyager2_stage")
        self.turn = os.path.join(self.stage, "batch_v2_20261002-120000", "turn001")
        self.proc = os.path.join(self.turn, PROC)
        self.out = os.path.join(self.turn, OUT)
        for folder in (os.path.join(self.proc, "frames"), os.path.join(self.out, "ortho", IDS[0])):
            os.makedirs(folder)
        Path(self.proc, "MRS_T1_2023_2025.psx").write_text("psx")
        Path(self.out, "ortho", IDS[0], f"{IDS[0]}_ortho.tif").write_bytes(b"II+\x00tif")
        Path(self.out, "vigil_manifest.json").write_text("{}")
        self.test_copy = os.path.join(self.stage, "test_20260927-080000", PROC)
        os.makedirs(self.test_copy)
        self.rows = [done_row(IDS[0]), done_row(IDS[1]),
                     {"readable_id": "MRS_T1_2026ann", "site": "MRS", "transect": "T1", "processing_folder": PROC,
                      "processing_location": f"{HOST}:{SHELF_PARENT}/{PROC}", "manual_edit_status": "awaiting"}]
        self.facts = {}
        self.remote = {f"{SHELF_PARENT}/{OUT}": walk(self.out)}

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def facts_fn(self, rid):
        return self.facts.get(rid, {})

    def remote_fn(self, path):
        if path not in self.remote:
            raise RuntimeError(f"no such folder on the Shelf: {path}")
        return dict(self.remote[path])

    def plan(self, is_live=not_live):
        return v2s.plan(self.stage, self.rows, self.facts_fn, walk, self.remote_fn, is_live)

    def by_path(self, result):
        return {item["path"]: item for item in result["folders"]}


class Walk(Stage):
    def test_finds_the_working_copy_the_output_folder_and_the_test_copy(self):
        found = v2s.find_stage_folders(self.stage)
        kinds = {f["path"]: (f["kind"], f["test"]) for f in found["folders"]}
        self.assertEqual(kinds, {self.proc: (v2s.KIND_WORKING_COPY, False), self.out: (v2s.KIND_OUTPUT, False),
                                 self.test_copy: (v2s.KIND_WORKING_COPY, True)})

    def test_never_enters_a_found_folder_and_skips_hidden_and_symlinks(self):
        os.makedirs(os.path.join(self.out, "psx", "ZZZ_T9_3D"))
        os.makedirs(os.path.join(self.stage, ".hidden", "LBH_T2_3D"))
        os.symlink(self.turn, os.path.join(self.stage, "linked"))
        paths = [f["path"] for f in v2s.find_stage_folders(self.stage)["folders"]]
        self.assertEqual(sorted(paths), sorted([self.proc, self.out, self.test_copy]))

    def test_depth_bound(self):
        deep = os.path.join(self.stage, "a", "b", "c", "d", "LBH_T2_3D")
        os.makedirs(deep)
        self.assertNotIn(deep, [f["path"] for f in v2s.find_stage_folders(self.stage)["folders"]])

    def test_missing_stage_is_a_line_not_a_crash(self):
        found = v2s.find_stage_folders(os.path.join(self.root, "nowhere"))
        self.assertEqual(found["folders"], [])
        self.assertIn("does not exist", found["problems"][0])

    def test_stage_root_validated(self):
        for bad in ("relative", "", None, 3):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    v2s.find_stage_folders(bad)


class Plans(Stage):
    def test_after_a_verified_deposit_both_copies_are_offered(self):
        items = self.by_path(self.plan())
        self.assertEqual(items[self.proc]["status"], v2s.STATUS_DEPOSITED)
        self.assertTrue(items[self.proc]["deletable"])
        # The new timepoint that never ran Voyager 2 does not hold the copy back.
        self.assertEqual(items[self.proc]["readable_ids"], list(IDS))
        self.assertEqual(items[self.out]["status"], cw.STATUS_IDENTICAL)
        self.assertTrue(items[self.out]["deletable"])
        self.assertEqual(items[self.out]["shelf_path"], f"{SHELF_PARENT}/{OUT}")

    def test_a_test_copy_is_never_offered(self):
        item = self.by_path(self.plan())[self.test_copy]
        self.assertEqual(item["status"], v2s.STATUS_TEST_COPY)
        self.assertFalse(item["deletable"])

    def test_a_reservation_holds_both_copies(self):
        self.facts[IDS[1]] = {"voyager2": {"reserved_by": {"value": "batch_v2_20261003-090000/turn001"}}}
        items = self.by_path(self.plan())
        for path in (self.proc, self.out):
            self.assertEqual(items[path]["status"], v2s.STATUS_RESERVED)
            self.assertFalse(items[path]["deletable"])
            self.assertIn("batch_v2_20261003-090000/turn001", items[path]["detail"])

    def test_a_live_lock_holds_both_copies(self):
        items = self.by_path(self.plan(is_live=lambda row: row["processing_location"] == self.proc))
        self.assertEqual(items[self.proc]["status"], cw.STATUS_LIVE)
        self.assertEqual(items[self.out]["status"], cw.STATUS_LIVE)

    def test_each_undeposited_state_keeps_the_working_copy(self):
        cases = {
            "running": {"step2_status": "running"},
            "failed": {"step2_status": "failed"},
            "output still local": {"output_location": self.out},
            "not verified": {"output_location_verified": ""},
            "processing still on the stage": {"processing_location": self.proc},
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                self.rows[1] = done_row(IDS[1], **change)
                item = self.by_path(self.plan())[self.proc]
                self.assertEqual(item["status"], v2s.STATUS_NOT_DEPOSITED)
                self.assertFalse(item["deletable"])
                self.assertIn(IDS[1], item["detail"])

    def test_a_working_copy_with_no_step2_record_is_not_offered(self):
        self.rows = [dict(r, step2_status="") for r in self.rows]
        item = self.by_path(self.plan())[self.proc]
        self.assertEqual(item["status"], v2s.STATUS_NOT_DEPOSITED)
        self.assertFalse(item["deletable"])

    def test_an_output_folder_that_differs_is_kept_with_the_differences(self):
        Path(self.out, "extra.txt").write_text("new")
        item = self.by_path(self.plan())[self.out]
        self.assertEqual(item["status"], cw.STATUS_DIFFERS)
        self.assertFalse(item["deletable"])
        self.assertIn("missing on the Shelf: extra.txt", item["mismatches"])

    def test_an_output_folder_the_rows_do_not_place_on_the_shelf(self):
        self.rows = [dict(r, output_location=self.out) for r in self.rows]
        item = self.by_path(self.plan())[self.out]
        self.assertEqual(item["status"], cw.STATUS_NOT_SHELVED)

    def test_rows_disagreeing_on_the_shelf_folder_are_not_shelved(self):
        self.rows[1] = done_row(IDS[1], output_location=f"{HOST}:{SHELF_PARENT}/other/{OUT}")
        self.assertEqual(self.by_path(self.plan())[self.out]["status"], cw.STATUS_NOT_SHELVED)

    def test_no_row_names_the_folder(self):
        orphan = os.path.join(self.turn, "LBH_T2_3doutput")
        os.makedirs(orphan)
        self.assertEqual(self.by_path(self.plan())[orphan]["status"], cw.STATUS_NO_ROW)

    def test_a_shelf_listing_failure_is_a_line(self):
        self.remote = {}
        item = self.by_path(self.plan())[self.out]
        self.assertEqual(item["status"], cw.STATUS_ERROR)
        self.assertIn("no such folder", item["detail"])


class Delete(Stage):
    def test_deletes_inside_the_stage(self):
        removed = v2s.delete_folder(self.out, self.stage, not_live)
        self.assertEqual(removed["deleted"], os.path.realpath(self.out))
        self.assertFalse(os.path.exists(self.out))
        self.assertTrue(os.path.isdir(self.proc))

    def test_refusals_leave_everything(self):
        outside = os.path.join(self.root, PROC)
        os.makedirs(outside)
        link = os.path.join(self.turn, "LBH_T2_3D")
        os.symlink(outside, link)
        badly_named = os.path.join(self.turn, "notes")
        os.makedirs(badly_named)
        cases = [outside, link, badly_named, self.stage, self.test_copy, "relative/MRS_T1_3D", "",
                 os.path.join(self.turn, "..", "..", "..", PROC)]
        for path in cases:
            with self.subTest(path=path):
                with self.assertRaises((TypeError, ValueError)):
                    v2s.delete_folder(path, self.stage, not_live, rmtree_fn=lambda p: self.fail("removed " + p))
        for path in (outside, self.test_copy, self.proc):
            self.assertTrue(os.path.isdir(path))

    def test_a_real_lock_blocks_the_delete(self):
        with open(os.path.join(self.proc, cw.LOCK_NAME), "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)

            def live(row):
                """True while this test holds the lock, the way registry.run_is_live reads it."""
                lock = os.path.join(row["processing_location"], cw.LOCK_NAME)
                if not os.path.exists(lock):
                    return False
                probe = open(lock)
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
                finally:
                    probe.close()
                return False
            with self.assertRaises(ValueError) as ctx:
                v2s.delete_folder(self.proc, self.stage, live)
            self.assertIn("processing right now", str(ctx.exception))
            with self.assertRaises(ValueError):
                v2s.delete_folder(self.out, self.stage, live)
        self.assertTrue(os.path.isdir(self.proc))

    def test_removal_failure_named(self):
        def boom(path):
            raise PermissionError("read-only")
        with self.assertRaises(OSError) as ctx:
            v2s.delete_folder(self.out, self.stage, not_live, rmtree_fn=boom)
        self.assertIn(self.out, str(ctx.exception))

    def test_log_row_names_the_stage_and_the_shelf(self):
        row = v2s.processing_log_row(self.out, f"{SHELF_PARENT}/{OUT}", list(IDS), "LO", 42, v2s.KIND_OUTPUT,
                                     now=lambda: "2026-10-02T15:00:00-04:00")
        self.assertEqual(row["module"], "voyager_tools")
        self.assertIn("Voyager 2 stage", row["purpose"])
        self.assertIn(IDS[0], row["purpose"])
        self.assertEqual(row["destination_uri"], f"{SHELF_PARENT}/{OUT}")
        self.assertIn("42 bytes", row["notes"])


if __name__ == "__main__":
    unittest.main()
