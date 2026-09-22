#!/usr/bin/env python3
"""
Module: tests/test_clean_workbench.py
Purpose: Unit, adversarial and integration tests for
         voyagertools.clean_workbench: the bounded walk, the row match, the
         name-and-size comparison, the anchored delete and the log row.
Inputs:  temporary Workbench roots and in-memory snapshots; no ssh, no NAS.
Outputs: unittest results; every temporary directory is removed in tearDown.

Run from the clone root:
    python3 -m unittest discover -s tests -q
"""
from __future__ import annotations

import csv
import fcntl
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from voyagertools import clean_workbench as cw  # noqa: E402

HOST = "146.226.147.140"
SHELF = "/volume6/Archive8_12TB/driver_deposits/run1/MRS_T1_3D"


def snap(**entries):
    """A snapshot from name=size pairs (a str value is a link target)."""
    out = {}
    for name, value in entries.items():
        rel = name.replace("__", "/")
        out[rel] = {"kind": "l", "target": value} if isinstance(value, str) else {"kind": "f", "size": value}
    return out


def not_live(_row):
    return False


class Walk(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vt-wb-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def mk(self, *parts):
        path = os.path.join(self.root, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    def test_finds_processing_folders_at_every_depth_and_never_inside_one(self):
        top = self.mk("MRS_T1_3D")
        self.mk("MRS_T1_3D", "frames", "ZZZ_T7_3D")
        deep = self.mk("batch_1", "turn001", "season", "LBH_T2_3D")
        self.mk("batch_1", "turn001", "season", "videos")
        self.mk(".hidden", "ZZZ_T6_3D")
        found = cw.find_processing_folders([self.root])
        self.assertEqual([f["path"] for f in found["folders"]], sorted([top, deep]))
        self.assertEqual(found["problems"], [])

    def test_symlinked_directory_is_skipped(self):
        real = self.mk("elsewhere")
        os.symlink(real, os.path.join(self.root, "ZZZ_T5_3D"))
        found = cw.find_processing_folders([self.root])
        self.assertEqual(found["folders"], [])

    def test_depth_bound(self):
        self.mk("a", "b", "c", "d", "e", "ZZZ_T4_3D")
        found = cw.find_processing_folders([self.root])
        self.assertEqual(found["folders"], [])

    def test_missing_root_reported_not_raised(self):
        found = cw.find_processing_folders([os.path.join(self.root, "nope")])
        self.assertEqual(found["folders"], [])
        self.assertIn("does not exist", found["problems"][0])

    def test_roots_validated(self):
        with self.assertRaises(ValueError):
            cw.find_processing_folders(["relative/path"])
        with self.assertRaises(TypeError):
            cw.find_processing_folders(self.root)

    def test_entry_budget_stops_the_walk(self):
        names = [f"d{n}" for n in range(60)]
        listing = {self.root: names}
        for name in names:
            listing[os.path.join(self.root, name)] = [f"x{n}" for n in range(1000)]
        original = cw.MAX_WALK_ENTRIES
        cw.MAX_WALK_ENTRIES = 500
        try:
            found = cw.find_processing_folders(
                [self.root], listdir=lambda p: listing.get(p, []), isdir=lambda p: True, islink=lambda p: False)
        finally:
            cw.MAX_WALK_ENTRIES = original
        self.assertTrue(any("Stopped after" in p for p in found["problems"]))

    def test_unreadable_folder_reported(self):
        def listdir(path):
            raise PermissionError("denied")
        found = cw.find_processing_folders([self.root], listdir=listdir)
        self.assertIn("Could not list", found["problems"][0])


class Matching(unittest.TestCase):
    ROWS = [
        {"readable_id": "MRS_T1_2023ann", "processing_folder": "MRS_T1_3D",
         "processing_location": f"{HOST}:{SHELF}"},
        {"readable_id": "MRS_T1_2024ann", "processing_folder": "",
         "processing_location": f"{HOST}:{SHELF}"},
        {"readable_id": "LBH_T2_2025_pbl", "processing_folder": "LBH_T2_3D",
         "processing_location": "/mnt/rip/driver_staging/manual/LBH_T2_3D"},
    ]

    def test_split_location(self):
        self.assertEqual(cw.split_location(f"{HOST}:/a/b"), (HOST, "/a/b"))
        self.assertEqual(cw.split_location("/a/b"), (None, "/a/b"))
        self.assertEqual(cw.split_location(""), (None, ""))
        self.assertEqual(cw.split_location(None), (None, ""))
        self.assertEqual(cw.split_location("C:relative"), (None, "C:relative"))

    def test_rows_for_folder_by_name_or_location(self):
        ids = [r["readable_id"] for r in cw.rows_for_folder("/x/MRS_T1_3D", self.ROWS)]
        self.assertEqual(ids, ["MRS_T1_2023ann", "MRS_T1_2024ann"])
        self.assertEqual(cw.rows_for_folder("/x/ZZZ_T3_3D", self.ROWS), [])
        self.assertEqual(cw.rows_for_folder("/x/MRS_T1_3D", ["junk", 1]), [])

    def test_shelf_path_requires_agreement(self):
        self.assertEqual(cw.shelf_path_for(self.ROWS[:2]), SHELF)
        self.assertIsNone(cw.shelf_path_for([self.ROWS[2]]))
        self.assertIsNone(cw.shelf_path_for([self.ROWS[0], {"processing_location": f"{HOST}:/other"}]))
        self.assertIsNone(cw.shelf_path_for([]))


class Compare(unittest.TestCase):
    def test_identical_ignores_the_lock_file(self):
        local = snap(a=1, sub__b=2)
        local[cw.LOCK_NAME] = {"kind": "f", "size": 30}
        remote = snap(a=1, sub__b=2)
        remote[cw.LOCK_NAME] = {"kind": "f", "size": 0}
        result = cw.compare_snapshots(local, remote)
        self.assertTrue(result["identical"])
        self.assertEqual(result["files"], 2)
        self.assertEqual(result["bytes"], 3)

    def test_differences_named(self):
        result = cw.compare_snapshots(snap(a=1, b=2, c="t1", d=1), snap(a=1, b=3, c="t2", e=1))
        self.assertFalse(result["identical"])
        self.assertEqual(result["mismatches"], [
            "size differs for b: local 2, Shelf 3", "link target differs for c",
            "missing on the Shelf: d", "only on the Shelf: e"])

    def test_kind_mismatch_and_junk_sizes(self):
        result = cw.compare_snapshots({"a": {"kind": "f", "size": "big"}}, {"a": {"kind": "l", "target": "x"}})
        self.assertIn("kind differs for a", result["mismatches"][0])
        self.assertEqual(cw.compare_snapshots({"a": {"kind": "f", "size": -5}}, {"a": {"kind": "f", "size": 0}})["identical"], True)

    def test_type_refused(self):
        with self.assertRaises(TypeError):
            cw.compare_snapshots([], {})

    def test_huge_snapshots_in_bounded_time(self):
        import time
        big = {f"f{n}": {"kind": "f", "size": n} for n in range(200000)}
        started = time.monotonic()
        self.assertTrue(cw.compare_snapshots(big, dict(big))["identical"])
        self.assertLess(time.monotonic() - started, 5.0)


class Plans(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vt-wbplan-")
        self.folder = os.path.join(self.root, "MRS_T1_3D")
        os.makedirs(self.folder)
        Path(self.folder, "a.txt").write_bytes(b"12345")
        self.rows = [{"readable_id": "MRS_T1_2023ann", "processing_folder": "MRS_T1_3D",
                      "processing_location": f"{HOST}:{SHELF}"}]

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_identical_is_deletable(self):
        plan = cw.plan([self.root], self.rows, cw_local,
                       lambda p: {"a.txt": {"kind": "f", "size": 5}} if p == SHELF else {}, not_live)
        folder = plan["folders"][0]
        self.assertEqual(folder["status"], cw.STATUS_IDENTICAL)
        self.assertTrue(folder["deletable"])
        self.assertEqual(folder["shelf_path"], SHELF)
        self.assertEqual(folder["readable_ids"], ["MRS_T1_2023ann"])
        self.assertEqual(folder["bytes"], 5)

    def test_differs_is_not_deletable(self):
        folder = cw.plan_folder(self.folder, self.rows, cw_local, lambda p: {"a.txt": {"kind": "f", "size": 6}}, not_live)
        self.assertEqual(folder["status"], cw.STATUS_DIFFERS)
        self.assertFalse(folder["deletable"])
        self.assertEqual(len(folder["mismatches"]), 1)

    def test_no_row_and_not_shelved_and_live(self):
        folder = cw.plan_folder(self.folder, [], cw_local, lambda p: {}, not_live)
        self.assertEqual(folder["status"], cw.STATUS_NO_ROW)
        local_rows = [dict(self.rows[0], processing_location=self.folder)]
        folder = cw.plan_folder(self.folder, local_rows, cw_local, lambda p: {}, not_live)
        self.assertEqual(folder["status"], cw.STATUS_NOT_SHELVED)
        folder = cw.plan_folder(self.folder, self.rows, cw_local, lambda p: {}, lambda r: True)
        self.assertEqual(folder["status"], cw.STATUS_LIVE)
        self.assertFalse(folder["deletable"])

    def test_snapshot_failure_is_a_line_not_a_crash(self):
        def boom(_path):
            raise RuntimeError("ssh failed rc=255")
        folder = cw.plan_folder(self.folder, self.rows, cw_local, boom, not_live)
        self.assertEqual(folder["status"], cw.STATUS_ERROR)
        self.assertIn("ssh failed", folder["detail"])
        self.assertFalse(folder["deletable"])


def cw_local(path):
    """Snapshot a local folder the way driver.verify.local_snapshot does (files by size)."""
    out = {}
    for root, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(root, name)
            out[os.path.relpath(full, path)] = {"kind": "f", "size": os.path.getsize(full)}
    return out


class Delete(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vt-wbdel-")
        self.other = tempfile.mkdtemp(prefix="vt-wbother-")
        self.folder = os.path.join(self.root, "batch", "MRS_T1_3D")
        os.makedirs(self.folder)
        Path(self.folder, "a.bin").write_bytes(b"x" * 100)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.other, ignore_errors=True)

    def test_delete_inside_root(self):
        result = cw.delete_folder(self.folder, [self.root], not_live)
        self.assertEqual(result["deleted"], os.path.realpath(self.folder))
        self.assertEqual(result["bytes"], 100)
        self.assertFalse(os.path.exists(self.folder))

    def test_refusals(self):
        with self.assertRaises(ValueError):
            cw.delete_folder(self.folder, [self.other], not_live)
        with self.assertRaises(ValueError):
            cw.delete_folder(self.root, [self.root], not_live)
        with self.assertRaises(ValueError):
            cw.delete_folder(os.path.join(self.root, "batch"), [self.root], not_live)
        with self.assertRaises(ValueError):
            cw.delete_folder("ZZZ_T3_3D", [self.root], not_live)
        with self.assertRaises(ValueError):
            cw.delete_folder(os.path.join(self.root, "batch", "..", "..", "ZZZ_T4_3D"), [self.root], not_live)
        with self.assertRaises(ValueError):
            cw.delete_folder(self.folder, [self.root], lambda r: True)
        with self.assertRaises(TypeError):
            cw.delete_folder(None, [self.root], not_live)
        self.assertTrue(os.path.exists(self.folder))

    def test_symlink_pointing_inside_refused(self):
        link = os.path.join(self.root, "ZZZ_T5_3D")
        os.symlink(self.folder, link)
        with self.assertRaises(ValueError):
            cw.delete_folder(link, [self.root], not_live)
        self.assertTrue(os.path.exists(self.folder))

    def test_symlink_escaping_the_root_refused(self):
        outside = os.path.join(self.other, "ZZZ_T5_3D")
        os.makedirs(outside)
        link_dir = os.path.join(self.root, "batch", "sneaky")
        os.symlink(self.other, link_dir)
        with self.assertRaises(ValueError):
            cw.delete_folder(os.path.join(link_dir, "ZZZ_T5_3D"), [self.root], not_live)
        self.assertTrue(os.path.exists(outside))

    def test_real_lock_counts_as_live(self):
        lock_path = os.path.join(self.folder, ".processing.lock")
        fh = open(lock_path, "w")
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            sys.path.insert(0, os.path.join(os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius"),
                                            "_METADATA", "3d"))
            import registry
            with self.assertRaises(ValueError) as caught:
                cw.delete_folder(self.folder, [self.root], registry.run_is_live)
            self.assertIn("processing right now", str(caught.exception))
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

    def test_rmtree_failure_named(self):
        def boom(path):
            raise OSError("busy")
        with self.assertRaises(OSError) as caught:
            cw.delete_folder(self.folder, [self.root], not_live, rmtree_fn=boom)
        self.assertIn(self.folder, str(caught.exception))


class Log(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vt-wblog-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_row_shape_and_append(self):
        row = cw.processing_log_row("/w/ZZZ_T6_3D", SHELF, ["MRS_T1_2023ann"], "LO", 5,
                                    now=lambda: "2026-09-04T01:00:00-04:00")
        self.assertEqual(list(row), cw.PROCESSING_LOG_COLUMNS)
        self.assertEqual(row["module"], "voyager_tools")
        self.assertEqual(row["origin"], "USER")
        self.assertIn("MRS_T1_2023ann", row["purpose"])
        log = os.path.join(self.root, "logs", "processing_log.csv")
        cw.append_processing_log(row, log)
        cw.append_processing_log(dict(row, notes="line\nbreak"), log)
        with open(log, newline="") as fh:
            lines = list(csv.DictReader(fh))
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1]["notes"], "line break")
        self.assertEqual(lines[0]["timestamp"], "2026-09-04T01:00:00-04:00")

    def test_append_failure_named(self):
        with self.assertRaises(OSError):
            cw.append_processing_log({"module": "x"}, os.path.join(self.root, "nope", "\x00bad.csv"))

    def test_row_without_ids(self):
        self.assertIn("no registry row", cw.processing_log_row("/w", None, [], "LO", 0)["purpose"])


if __name__ == "__main__":
    unittest.main()
