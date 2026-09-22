#!/usr/bin/env python3
"""
Module: tests/test_rewind.py
Purpose: Unit, adversarial and integration tests for voyagertools.rewind and
         scripts/rewind_chunk.py in fake mode: refusals, the dry run, the
         artifact moves, status.csv and registry resets, the manifest, the
         spot redo fact, and the chunk script's exit codes.
Inputs:  one temporary registry root and processing folder per test; the
         chunk script runs as a subprocess under this interpreter with --fake.
Outputs: unittest results; every temporary directory is removed in tearDown.

Run from the clone root:
    python3 -m unittest discover -s tests -q
"""
from __future__ import annotations

import csv
import fcntl
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
REGISTRY_LIB_DIR = os.path.join(os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius"),
                                "_METADATA", "3d")
if REGISTRY_LIB_DIR not in sys.path:
    sys.path.insert(0, REGISTRY_LIB_DIR)

from voyagertools import rewind as rw  # noqa: E402

RID = "MRS_T1_2024ann"
SIBLING = "MRS_T1_2023ann"
STATUS_HEADER = ["original_videos", "readable_id", "Model ID", "Status", "Step 0 complete", "Frames Extracted",
                 "Step 0 start time", "Step 1 complete", "Step 1 start time", "PSX file", "Report file", "Scale",
                 "Notes"]


def not_live(_row):
    return False


def fake_chunk_runner(psx, label, out_dir):
    """The real seam with the script in fake mode under this interpreter."""
    return rw.run_chunk_script(psx, label, out_dir, fake=True)


class Folder(unittest.TestCase):
    """A processing folder with frames, a report, a fake psx and a status.csv, plus a temp registry."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vt-rewind-")
        os.environ["VICARIUS_3D_REGISTRY_ROOT"] = self.root
        import registry
        self.registry = importlib.reload(registry)
        self.folder = os.path.join(self.root, "MRS_T1_3D")
        os.makedirs(os.path.join(self.folder, "frames", RID))
        Path(self.folder, "frames", RID, "f_00001.tiff").write_bytes(b"tiff")
        os.makedirs(os.path.join(self.folder, "reports"))
        Path(self.folder, "reports", f"{RID}_step1.pdf").write_bytes(b"%PDF")
        Path(self.folder, "reports", f"{SIBLING}_step1.pdf").write_bytes(b"%PDF sibling")
        self.psx = os.path.join(self.folder, "MRS_T1_2023_2024.psx")
        Path(self.psx).write_text(json.dumps({"chunks": [SIBLING, RID]}))
        with open(os.path.join(self.folder, "status.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=STATUS_HEADER)
            w.writeheader()
            w.writerow({"readable_id": RID, "Model ID": RID, "Status": "Complete", "Step 0 complete": "True",
                        "Frames Extracted": "1000", "Step 1 complete": "True", "PSX file": self.psx,
                        "Report file": "r.pdf", "Scale": "PASS", "Notes": "keep me"})
            w.writerow({"readable_id": SIBLING, "Model ID": SIBLING, "Step 1 complete": "True"})
        common = {"site": "MRS", "transect": "T1", "processing_location": self.folder,
                  "processing_folder": os.path.basename(self.folder), "psx_file": self.psx}
        self.registry.upsert(RID, dict(common, year="2024", season_token="ann", step1_status="complete", step="1",
                                       stage="done", step1_seconds="123", faces_delivery="876675", scale_status="PASS",
                                       manual_edit_status="awaiting", console_log=f"{self.folder}/console/x.log",
                                       snapshot_dir=f"snapshots/{RID}"), "seed")
        self.registry.upsert(SIBLING, dict(common, year="2023", season_token="ann", step1_status="complete"), "seed")
        self.row = self.registry.get(RID)

    def tearDown(self):
        os.environ.pop("VICARIUS_3D_REGISTRY_ROOT", None)
        shutil.rmtree(self.root, ignore_errors=True)
        import registry
        importlib.reload(registry)

    def chunks(self):
        return json.loads(Path(self.psx).read_text())["chunks"]

    def status_row(self, rid):
        with open(os.path.join(self.folder, "status.csv"), newline="") as fh:
            return next(r for r in csv.DictReader(fh) if r["Model ID"] == rid)


class Refusals(Folder):
    def test_unknown_target(self):
        with self.assertRaises(ValueError):
            rw.refusal(self.row, "before_step9", not_live)
        with self.assertRaises(ValueError):
            rw.plan(self.row, "sideways")

    def test_no_row(self):
        self.assertIn("No registry row", rw.refusal(None, rw.TARGET_BEFORE_STEP1, not_live))

    def test_live_row_refused_with_the_phrase(self):
        lock_path = os.path.join(self.folder, ".processing.lock")
        fh = open(lock_path, "w")
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            why = rw.refusal(self.row, rw.TARGET_BEFORE_STEP1, self.registry.run_is_live)
            self.assertIn("processing right now", why)
            with self.assertRaises(rw.RewindRefused):
                rw.apply(self.row, rw.TARGET_BEFORE_STEP1, self.registry, "voyager_tools:LO", fake_chunk_runner,
                         self.registry.run_is_live)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()
        self.assertEqual(self.chunks(), [SIBLING, RID])

    def test_remote_folder_shows_the_pullback_command(self):
        row = dict(self.row, processing_location="146.226.147.140:/volume6/x/MRS_T1_3D")
        why = rw.refusal(row, rw.TARGET_BEFORE_STEP1, not_live)
        self.assertIn("not on this machine", why)
        self.assertIn("/api/pullback", why)
        self.assertIn(RID, why)

    def test_missing_local_folder_and_blank_location(self):
        row = dict(self.row, processing_location=os.path.join(self.root, "ZZZ_T2_3D"))
        self.assertIn("not on this machine", rw.refusal(row, rw.TARGET_BEFORE_STEP1, not_live))
        row = dict(self.row, processing_location="")
        self.assertIn("no processing folder", rw.refusal(row, rw.TARGET_BEFORE_STEP1, not_live))

    def test_row_in_the_manual_edit_job(self):
        why = rw.refusal(self.row, rw.TARGET_BEFORE_STEP1, not_live, active_job_ids=[RID])
        self.assertIn("manual edit job", why)

    def test_nothing_to_rewind(self):
        bare = dict(self.row)
        for cell in rw.STEP1_REGISTRY_CELLS:
            bare[cell] = ""
        os.remove(os.path.join(self.folder, "reports", f"{RID}_step1.pdf"))
        self.assertTrue(rw.plan(bare, rw.TARGET_BEFORE_STEP1)["empty"])
        with self.assertRaises(rw.RewindRefused) as caught:
            rw.apply(bare, rw.TARGET_BEFORE_STEP1, self.registry, "voyager_tools:LO", fake_chunk_runner, not_live)
        self.assertIn("nothing to rewind", str(caught.exception))

    def test_blank_actor_refused(self):
        with self.assertRaises(ValueError):
            rw.apply(self.row, rw.TARGET_BEFORE_STEP1, self.registry, " ", fake_chunk_runner, not_live)


class DryRun(Folder):
    def test_plan_before_step1(self):
        plan = rw.plan(self.row, rw.TARGET_BEFORE_STEP1, stamp="20260904-010000")
        self.assertEqual(plan["target_words"], "before step 1")
        self.assertEqual(plan["rewind_dir"], os.path.join(self.folder, "_rewind", "20260904-010000"))
        self.assertEqual([m["kind"] for m in plan["moves"]], ["report"])
        self.assertTrue(plan["moves"][0]["from"].endswith(f"{RID}_step1.pdf"))
        self.assertEqual(plan["chunk"], {"psx": self.psx, "present": True})
        self.assertEqual(plan["registry_resets"]["step1_status"], "complete")
        self.assertEqual(plan["registry_resets"]["faces_delivery"], "876675")
        self.assertNotIn("site", plan["registry_resets"])
        self.assertIn("Step 1 complete", plan["status_resets"])
        self.assertNotIn("Step 0 complete", plan["status_resets"])
        self.assertFalse(plan["empty"])
        self.assertEqual(plan["warnings"], [])
        self.assertTrue(os.path.exists(self.psx))
        self.assertEqual(self.chunks(), [SIBLING, RID])

    def test_plan_before_step0_adds_frames(self):
        plan = rw.plan(self.row, rw.TARGET_BEFORE_STEP0)
        self.assertEqual(sorted(m["kind"] for m in plan["moves"]), ["frames", "report"])
        self.assertIn("Step 0 complete", plan["status_resets"])

    def test_plan_warnings(self):
        row = dict(self.row, psx_file="", manual_edit_status="done")
        shutil.rmtree(os.path.join(self.folder, "frames", RID))
        plan = rw.plan(row, rw.TARGET_BEFORE_STEP0)
        self.assertIsNone(plan["chunk"])
        self.assertTrue(any("No psx" in w for w in plan["warnings"]))
        self.assertTrue(any("manually edited" in w for w in plan["warnings"]))
        self.assertTrue(any("No frames folder" in w for w in plan["warnings"]))
        row = dict(self.row, psx_file="missing.psx")
        plan = rw.plan(row, rw.TARGET_BEFORE_STEP1)
        self.assertFalse(plan["chunk"]["present"])

    def test_plan_refuses_a_non_row(self):
        with self.assertRaises(TypeError):
            rw.plan("MRS_T1_2024ann", rw.TARGET_BEFORE_STEP1)

    def test_hostile_id_never_globs(self):
        row = dict(self.row, readable_id="[a-z]*")
        self.assertEqual(rw.plan(row, rw.TARGET_BEFORE_STEP1)["moves"], [])


class Apply(Folder):
    def test_apply_before_step1(self):
        manifest = rw.apply(self.row, rw.TARGET_BEFORE_STEP1, self.registry, "voyager_tools:LO", fake_chunk_runner,
                            not_live, stamp="20260904-010000")
        rewind_dir = os.path.join(self.folder, "_rewind", "20260904-010000")
        self.assertTrue(os.path.exists(os.path.join(rewind_dir, "reports", f"{RID}_step1.pdf")))
        self.assertFalse(os.path.exists(os.path.join(self.folder, "reports", f"{RID}_step1.pdf")))
        self.assertTrue(os.path.exists(os.path.join(self.folder, "reports", f"{SIBLING}_step1.pdf")))
        self.assertTrue(os.path.isdir(os.path.join(self.folder, "frames", RID)))
        self.assertEqual(self.chunks(), [SIBLING])
        self.assertEqual(manifest["chunk"]["removed"], 1)
        self.assertEqual(manifest["chunk"]["mode"], "fake")
        row = self.registry.get(RID)
        for cell in ("step1_status", "psx_file", "stage", "step", "faces_delivery", "manual_edit_status",
                     "console_log", "snapshot_dir"):
            self.assertEqual(row[cell], "", cell)
        self.assertEqual(row["site"], "MRS")
        self.assertEqual(self.registry.get(SIBLING)["psx_file"], self.psx)
        status = self.status_row(RID)
        self.assertEqual(status["Step 1 complete"], "False")
        self.assertEqual(status["Status"], "Rewound before step 1")
        self.assertEqual(status["PSX file"], "")
        self.assertEqual(status["Step 0 complete"], "True")
        self.assertEqual(status["Notes"], "keep me")
        self.assertEqual(self.status_row(SIBLING)["Step 1 complete"], "True")
        self.assertTrue(manifest["status_csv_reset"])
        written = json.loads(Path(rewind_dir, "rewind.json").read_text())
        self.assertEqual(written["actor"], "voyager_tools:LO")
        self.assertEqual(written["registry_resets"]["step1_status"], "complete")
        events = Path(self.root, "events.csv").read_text()
        self.assertIn("voyager_tools:LO", events)
        self.assertIn("step1_status,complete,", events)

    def test_apply_before_step0_moves_frames(self):
        rw.apply(self.row, rw.TARGET_BEFORE_STEP0, self.registry, "voyager_tools:LO", fake_chunk_runner, not_live,
                 stamp="s0")
        self.assertFalse(os.path.exists(os.path.join(self.folder, "frames", RID)))
        self.assertTrue(os.path.exists(os.path.join(self.folder, "_rewind", "s0", "frames", RID, "f_00001.tiff")))
        status = self.status_row(RID)
        self.assertEqual(status["Step 0 complete"], "False")
        self.assertEqual(status["Frames Extracted"], "")
        self.assertEqual(status["Status"], "Rewound before step 0")

    def test_chunk_error_stops_before_any_change(self):
        def failing(psx, label, out_dir):
            return {"error": "the project is open in Metashape", "removed": 0}
        with self.assertRaises(rw.RewindRefused) as caught:
            rw.apply(self.row, rw.TARGET_BEFORE_STEP1, self.registry, "voyager_tools:LO", failing, not_live)
        self.assertIn("open in Metashape", str(caught.exception))
        self.assertTrue(os.path.exists(os.path.join(self.folder, "reports", f"{RID}_step1.pdf")))
        self.assertEqual(self.registry.get(RID)["step1_status"], "complete")
        self.assertEqual(self.status_row(RID)["Step 1 complete"], "True")

    def test_chunk_runner_returning_junk_is_refused(self):
        with self.assertRaises(rw.RewindRefused):
            rw.apply(self.row, rw.TARGET_BEFORE_STEP1, self.registry, "voyager_tools:LO", lambda *a: None, not_live)

    def test_move_failure_names_the_artifact(self):
        def boom(src, dst):
            raise OSError("disk full")
        with self.assertRaises(OSError) as caught:
            rw.apply(self.row, rw.TARGET_BEFORE_STEP1, self.registry, "voyager_tools:LO", fake_chunk_runner, not_live,
                     move_fn=boom)
        self.assertIn(f"{RID}_step1.pdf", str(caught.exception))

    def test_status_csv_missing_or_without_the_row(self):
        os.remove(os.path.join(self.folder, "status.csv"))
        manifest = rw.apply(self.row, rw.TARGET_BEFORE_STEP1, self.registry, "voyager_tools:LO", fake_chunk_runner,
                            not_live)
        self.assertFalse(manifest["status_csv_reset"])
        self.assertFalse(rw.reset_status_csv(self.folder, RID, {"Status": ""}))
        Path(self.folder, "status.csv").write_text("")
        self.assertFalse(rw.reset_status_csv(self.folder, RID, {"Status": ""}))
        Path(self.folder, "status.csv").write_text("Model ID,Status\nOTHER,x\n")
        self.assertFalse(rw.reset_status_csv(self.folder, RID, {"Status": ""}))

    def test_spot_redo_writes_the_fact(self):
        manifest = rw.spot_redo(self.row, self.registry, "voyager_tools:LO", "LO", fake_chunk_runner, not_live)
        self.assertIn("spot_redo", manifest["spot_redo"])
        facts = self.registry.fact_sections(RID)
        self.assertIn("requested", facts["voyager1"]["spot_redo"]["value"])
        self.assertIn("by LO", facts["voyager1"]["spot_redo"]["value"])
        self.assertEqual(facts["voyager1"]["spot_redo"]["recorded_by"], "voyager_tools:LO")
        self.assertEqual(self.registry.get(RID)["step1_status"], "")
        self.assertEqual(self.chunks(), [SIBLING])

    def test_two_rewinds_get_distinct_folders(self):
        rw.apply(self.row, rw.TARGET_BEFORE_STEP1, self.registry, "voyager_tools:LO", fake_chunk_runner, not_live,
                 stamp="a")
        Path(self.folder, "reports", f"{RID}_step1.pdf").write_bytes(b"again")
        self.registry.upsert(RID, {"step1_status": "complete"}, "seed")
        rw.apply(self.registry.get(RID), rw.TARGET_BEFORE_STEP1, self.registry, "voyager_tools:LO",
                 fake_chunk_runner, not_live, stamp="b")
        self.assertTrue(os.path.isdir(os.path.join(self.folder, "_rewind", "a")))
        self.assertTrue(os.path.isdir(os.path.join(self.folder, "_rewind", "b")))


class ChunkScript(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vt-chunk-")
        self.psx = os.path.join(self.root, "fake.psx")
        Path(self.psx).write_text(json.dumps({"chunks": ["A", "B", "A"]}))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_script(self, *args):
        return subprocess.run([sys.executable, str(rw.REWIND_CHUNK_SCRIPT), *args], capture_output=True, text=True,
                              timeout=60)

    def test_fake_removal_and_out_file(self):
        out = os.path.join(self.root, "r.json")
        completed = self.run_script(self.psx, "--label", "A", "--out", out, "--fake")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        document = json.loads(Path(out).read_text())
        self.assertEqual((document["chunks_before"], document["chunks_after"], document["removed"]), (3, 1, 2))
        self.assertTrue(document["saved"])
        self.assertIsNone(document["error"])
        self.assertEqual(json.loads(Path(self.psx).read_text())["chunks"], ["B"])
        self.assertIn("Step 1 complete", completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["label"], "A")

    def test_label_absent_is_not_an_error(self):
        completed = self.run_script(self.psx, "--label", "Z", "--fake")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout)["removed"], 0)

    def test_usage_errors(self):
        self.assertEqual(self.run_script(self.psx, "--label", " ", "--fake").returncode, 2)
        self.assertEqual(self.run_script(os.path.join(self.root, "nope.psx"), "--label", "A", "--fake").returncode, 2)
        self.assertEqual(self.run_script(os.path.join(self.root, "x.txt"), "--label", "A").returncode, 2)

    def test_bad_fake_file_is_an_error_document(self):
        Path(self.psx).write_text("not json")
        completed = self.run_script(self.psx, "--label", "A", "--fake")
        self.assertEqual(completed.returncode, 1)
        self.assertIsNotNone(json.loads(completed.stdout)["error"])
        Path(self.psx).write_text(json.dumps({"chunks": "A"}))
        completed = self.run_script(self.psx, "--label", "A", "--fake")
        self.assertEqual(completed.returncode, 1)

    def test_run_chunk_script_seam(self):
        document = rw.run_chunk_script(self.psx, "B", self.root, fake=True)
        self.assertEqual(document["removed"], 1)
        self.assertTrue(os.path.exists(os.path.join(self.root, "rewind_chunk.json")))

    def test_run_chunk_script_without_metashape(self):
        document = rw.run_chunk_script(self.psx, "B", self.root, metashape_bin=None, fake=False,
                                       runner=lambda *a, **k: None)
        self.assertIn("Metashape was not found", document["error"]) if rw.metashape_binary() is None else None

    def test_run_chunk_script_runner_failures(self):
        def boom(argv, **kwargs):
            raise OSError("no such binary")
        document = rw.run_chunk_script(self.psx, "B", self.root, metashape_bin="/nope/metashape", runner=boom)
        self.assertIn("could not run", document["error"])

        class Completed:
            returncode, stderr = 1, "crash"
        document = rw.run_chunk_script(self.psx, "B", self.root, metashape_bin="/nope/metashape",
                                       runner=lambda argv, **kwargs: Completed())
        self.assertIn("wrote no result", document["error"])

    def test_metashape_binary_resolution(self):
        exists = {"/env/metashape": True, rw.METASHAPE_SEARCH_PATHS[0]: False}
        self.assertEqual(rw.metashape_binary({"METASHAPE_PATH": "/env/metashape"}, which=lambda n: None,
                                             exists=lambda p: exists.get(p, False)), "/env/metashape")
        self.assertEqual(rw.metashape_binary({}, which=lambda n: "/usr/bin/metashape", exists=lambda p: False),
                         "/usr/bin/metashape")
        self.assertIsNone(rw.metashape_binary({}, which=lambda n: None, exists=lambda p: False))

    def test_lock_artifact_path(self):
        sys.path.insert(0, str(rw.REWIND_CHUNK_SCRIPT.parent))
        import rewind_chunk
        self.assertEqual(rewind_chunk.lock_artifact_path("/x/MRS_T1_2023_2023.psx"), "/x/MRS_T1_2023_2023.files/lock")

    def test_pullback_command_is_a_curl_line(self):
        command = rw.pullback_command("MRS_T1_2024ann")
        self.assertTrue(command.startswith("curl -X POST http://127.0.0.1:5093/api/pullback"))
        self.assertIn('"target": "MRS_T1_2024ann"', command)


if __name__ == "__main__":
    unittest.main()
