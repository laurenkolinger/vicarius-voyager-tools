#!/usr/bin/env python3
"""
Module: tests/test_review_queue.py
Purpose: The Review queue's server side (program C milestone C6, spec
         sections 3.6 and 4.10): voyagertools/review_queue.py over fake
         registry rows and job records, and GET /voyager-tools/api/review/queue
         over a temporary registry root with the unfinished projects faked
         (the gates, a store that cannot be read, the recovery of stranded
         start overs, and read only).
Inputs:  temporary directories only; no NAS, no live registry, no live job.
Outputs: unittest results; every temporary directory is removed in tearDown.

Run from the clone root:
    python3 -m unittest tests.test_review_queue -v
"""
from __future__ import annotations

import copy
import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from flask import Flask

HERE = Path(__file__).resolve().parent
CLONE = HERE.parent
sys.path.insert(0, str(CLONE))
VICARIUS_ROOT = os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius")
REGISTRY_LIB_DIR = os.path.join(VICARIUS_ROOT, "_METADATA", "3d")
if REGISTRY_LIB_DIR not in sys.path:
    sys.path.insert(0, REGISTRY_LIB_DIR)

from voyagertools import review_queue as rq  # noqa: E402
from voyagertools import views  # noqa: E402

JOB_A = "ME-20260925-100000-CC"
JOB_B = "ME-20260926-100000-LO"
T1 = ["MRS_T1_2023ann", "MRS_T1_2024ann"]
T2 = ["MRS_T2_2023ann", "MRS_T2_2024_pbl", "MRS_T2_2024ann"]
T3 = ["LBH_T3_2024ann", "LBH_T3_2025_pbl"]
T4 = ["LBH_T4_2024ann"]
PSX = {"T1": "MRS_T1_2023_2024.psx", "T2": "MRS_T2_2023_2024.psx", "T3": "LBH_T3_2024_2025.psx",
       "T4": "LBH_T4_2024_2025.psx"}
STATES = rq.QueueStates(review="review", restarting="restarting", upload="upload")
SITE_NAMES = {"LBH": "Lang Bank Red Hind FSA"}
ERROR = ("VICARIUS could not read the free space of 146.226.147.140:/volume6 (ssh gave no answer in 60 s), "
         "so nothing was copied.")


def job_rows(ids, psx, site_name=""):
    """Job rows for the readable ids of one transect (program B's row shape)."""
    out = []
    for rid in ids:
        site, transect = rid.split("_")[:2]
        out.append({"readable_id": rid, "site": site, "site_name": site_name, "transect": transect,
                    "processing_folder": f"{site}_{transect}_3D", "psx_basename": psx})
    return out


def checks(*levels):
    """A checks record whose items carry the given levels."""
    return {"at": "2026-09-27T09:30:00-04:00", "items": [{"level": level, "rule": "scale"} for level in levels]}


def jobs():
    """Two unfinished projects: A holds T2 (review) and T1 (upload); B holds T3 (a stopped start over) and T4."""
    job_a = {"job_id": JOB_A, "created_by": "CC", "created_at": "2026-09-25T10:00:00-04:00",
             "rows": job_rows(T1, PSX["T1"]) + job_rows(T2, PSX["T2"]),
             "projects": {PSX["T2"]: {"state": "review", "round": 1, "checks": checks("ok", "stop", "warn", "stop"),
                                      "certification": {"by": "AB", "at": "2026-09-27T09:58:10-04:00"}},
                          PSX["T1"]: {"state": "upload", "round": 0, "upload_id": None, "upload_verified": None,
                                      "certification": {"by": "CC", "at": "2026-09-26T08:00:00-04:00"}}}}
    job_b = {"job_id": JOB_B, "created_by": "LO", "created_at": "2026-09-26T10:00:00-04:00",
             "rows": job_rows(T3, PSX["T3"], site_name="") + job_rows(T4, PSX["T4"]),
             "projects": {PSX["T3"]: {"state": "restarting", "round": 2,
                                      "restart": {"at": "2026-09-27T10:15:00-04:00", "by": "LO", "stage": "pulling",
                                                  "aside": "/bench/LBH_T3_3D_restart_20260927-101500",
                                                  "error": ERROR}},
                          PSX["T4"]: {"state": "uploading", "round": 0, "upload_id": "UP-20260927-110000-LO"}}}
    return [job_a, job_b]


def rows(review=(), others=()):
    """Registry rows: the ids in `review` read review, the rest read editing."""
    out = []
    for rid in list(review) + list(others):
        out.append({"readable_id": rid, "manual_edit_status": "review" if rid in review else "editing"})
    return out


def queue(registry_rows, records):
    """queue_items with the test vocabulary and site table."""
    return rq.queue_items(registry_rows, records, lambda code: SITE_NAMES.get(code, ""), sort_key=str,
                          status_review="review", states=STATES)


class QueueItems(unittest.TestCase):
    def test_membership_is_the_registry_status_and_one_item_per_transect(self):
        out = queue(rows(review=T2, others=T1 + T3 + T4), jobs())
        self.assertEqual([(i["job_id"], i["psx"]) for i in out["items"]], [(JOB_A, PSX["T2"])])
        item = out["items"][0]
        self.assertEqual(item["readable_ids"], sorted(T2))
        self.assertEqual((item["site"], item["site_name"], item["transect"]), ("MRS", "", "T2"))
        self.assertEqual((item["round"], item["editor"], item["confirmed_at"]), (1, "AB", "2026-09-27T09:58:10-04:00"))
        self.assertEqual((item["failed_checks"], item["warned_checks"]), (2, 1))
        self.assertEqual((item["processing_folder"], item["state"]), ("MRS_T2_3D", "review"))

    def test_a_transect_with_only_one_row_in_review_is_listed(self):
        out = queue(rows(review=T2[:1], others=T2[1:]), jobs())
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(out["items"][0]["readable_ids"], sorted(T2))

    def test_several_projects_are_listed_oldest_confirmation_first(self):
        records = jobs()
        records[1]["projects"][PSX["T4"]] = {"state": "review", "round": 0,
                                             "certification": {"by": "LO", "at": "2026-09-26T07:00:00-04:00"}}
        out = queue(rows(review=T2 + T4), records)
        self.assertEqual([i["psx"] for i in out["items"]], [PSX["T4"], PSX["T2"]])
        self.assertEqual(out["items"][0]["site_name"], "Lang Bank Red Hind FSA")

    def test_editor_falls_back_to_the_project_creator_and_counts_to_zero(self):
        records = jobs()
        records[0]["projects"][PSX["T2"]] = {"state": "review", "round": 0}
        item = queue(rows(review=T2), records)["items"][0]
        self.assertEqual((item["editor"], item["confirmed_at"], item["failed_checks"], item["warned_checks"]),
                         ("CC", "", 0, 0))

    def test_a_stopped_start_over_is_listed_with_its_stage_and_error(self):
        out = queue(rows(others=T3), jobs())
        self.assertEqual(out["stopped_restarts"], [{
            "job_id": JOB_B, "psx": PSX["T3"], "processing_folder": "LBH_T3_3D", "readable_ids": sorted(T3),
            "site": "LBH", "site_name": "Lang Bank Red Hind FSA", "transect": "T3", "round": 2, "stage": "pulling",
            "error": ERROR, "at": "2026-09-27T10:15:00-04:00"}])

    def test_a_running_start_over_is_not_listed(self):
        records = jobs()
        records[1]["projects"][PSX["T3"]]["restart"]["error"] = "   "
        self.assertEqual(queue(rows(), records)["stopped_restarts"], [])

    def test_marked_for_upload_lists_only_what_can_go_back_to_review(self):
        records = jobs()
        out = queue(rows(), records)
        self.assertEqual([(i["job_id"], i["psx"]) for i in out["marked_for_upload"]], [(JOB_A, PSX["T1"])])
        self.assertEqual(out["marked_for_upload"][0]["editor"], "CC")
        records[0]["projects"][PSX["T1"]]["upload_id"] = "UP-20260927-120000-LO"
        self.assertEqual(queue(rows(), records)["marked_for_upload"], [])
        records[0]["projects"][PSX["T1"]].update(upload_id=None, upload_verified={"at": "x"})
        self.assertEqual(queue(rows(), records)["marked_for_upload"], [])

    def test_a_row_in_review_no_project_holds_is_unmatched(self):
        out = queue(rows(review=["MRS_T3_2023ann", "LBH_T9_2024ann"] + T2), jobs())
        self.assertEqual(out["unmatched"], ["LBH_T9_2024ann", "MRS_T3_2023ann"])
        self.assertEqual(len(out["items"]), 1)

    def test_malformed_records_and_rows_are_skipped(self):
        records = jobs() + [None, "x", {"job_id": "ME-bad"}, {"job_id": JOB_A + "X", "rows": "no", "projects": []}]
        records[0]["rows"].append({"readable_id": "", "psx_basename": PSX["T2"]})
        records[0]["rows"].append("garbage")
        records[0]["projects"][PSX["T2"]]["checks"] = {"items": ["x", None, {"level": "stop"}]}
        records[0]["projects"][PSX["T2"]]["round"] = "two"
        registry_rows = rows(review=T2) + [None, {"readable_id": ""}, {"manual_edit_status": "review"}]
        out = queue(registry_rows, records)
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual((out["items"][0]["round"], out["items"][0]["failed_checks"]), (0, 1))

    def test_a_transect_with_no_project_record_reads_as_not_started(self):
        records = jobs()
        del records[0]["projects"][PSX["T2"]]
        item = queue(rows(review=T2), records)["items"][0]
        self.assertEqual((item["state"], item["round"]), ("", 0))

    def test_empty_inputs(self):
        self.assertEqual(queue([], []), {"items": [], "stopped_restarts": [], "marked_for_upload": [],
                                         "unmatched": []})

    def test_inputs_are_not_changed(self):
        records, registry_rows = jobs(), rows(review=T2)
        before = copy.deepcopy((records, registry_rows))
        queue(registry_rows, records)
        self.assertEqual((records, registry_rows), before)

    def test_wrong_types_are_refused(self):
        with self.assertRaises(TypeError):
            rq.queue_items("rows", [], str, sort_key=str, status_review="review", states=STATES)
        with self.assertRaises(TypeError):
            rq.queue_items([], {"a": 1}, str, sort_key=str, status_review="review", states=STATES)


class TransectOf(unittest.TestCase):
    def test_the_transect_of_a_job_and_psx(self):
        got = rq.transect_of(jobs()[1], PSX["T3"], lambda code: SITE_NAMES.get(code, ""), sort_key=str)
        self.assertEqual(got, {"psx": PSX["T3"], "processing_folder": "LBH_T3_3D", "site": "LBH",
                               "site_name": "Lang Bank Red Hind FSA", "transect": "T3", "readable_ids": sorted(T3)})

    def test_a_psx_the_job_does_not_hold(self):
        self.assertIsNone(rq.transect_of(jobs()[1], PSX["T2"], str, sort_key=str))


class Route(unittest.TestCase):
    """GET /voyager-tools/api/review/queue on a bare app over a temporary registry root."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="vt-review-queue-"))
        os.environ["VICARIUS_3D_REGISTRY_ROOT"] = str(self.root)
        import registry
        self.registry = importlib.reload(registry)
        self.locked = False
        self.records = jobs()
        self._saved = {k: getattr(views.deps, k) for k in vars(views.deps)}
        views.deps.is_locked = lambda: self.locked
        views.deps.active_jobs = lambda: copy.deepcopy(self.records)
        views.deps.site_name = lambda code: SITE_NAMES.get(code, "")
        views.deps.now_iso = lambda: "2026-09-27T10:00:00-04:00"
        self.recovered = []
        views.deps.recover_restarts = lambda records: self.recovered.append(len(records)) or records
        for rid in T1 + T2 + T3 + T4 + ["MRS_T3_2023ann"]:
            site, transect = rid.split("_")[:2]
            status = "review" if rid in T2 + ["MRS_T3_2023ann"] else "editing"
            self.registry.upsert(rid, {"site": site, "transect": transect, "manual_edit_status": "awaiting"}, "seed")
            self.registry.set_manual_edit_status([rid], "editing", "manual_edit:LO",
                                                 locations={rid: f"/bench/{site}_{transect}_3D"})
            if status == "review":
                self.registry.set_manual_edit_status([rid], "review", "manual_edit:LO")
        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(views.voyager_tools_bp)
        self.client = app.test_client()

    def tearDown(self):
        for key, value in self._saved.items():
            setattr(views.deps, key, value)
        os.environ.pop("VICARIUS_3D_REGISTRY_ROOT", None)
        shutil.rmtree(self.root, ignore_errors=True)
        import registry
        importlib.reload(registry)

    def unlock(self):
        with self.client.session_transaction() as sess:
            sess["admin_unlocked"] = True

    def test_gates(self):
        r = self.client.get("/voyager-tools/api/review/queue")
        self.assertEqual(r.status_code, 401)
        self.assertTrue(r.get_json()["need_unlock"])
        self.unlock()
        self.locked = True
        self.assertEqual(self.client.get("/voyager-tools/api/review/queue").status_code, 423)

    def test_the_payload(self):
        self.unlock()
        r = self.client.get("/voyager-tools/api/review/queue")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual([i["psx"] for i in body["items"]], [PSX["T2"]])
        self.assertEqual([s["psx"] for s in body["stopped_restarts"]], [PSX["T3"]])
        self.assertEqual([m["psx"] for m in body["marked_for_upload"]], [PSX["T1"]])
        self.assertEqual(body["unmatched"], ["MRS_T3_2023ann"])
        self.assertEqual(body["read_at"], "2026-09-27T10:00:00-04:00")
        self.assertEqual(self.recovered, [2])

    def test_an_unreadable_store_answers_500_with_its_sentence(self):
        self.unlock()

        def broken():
            raise ValueError("ME-20260925-100000-CC.json does not parse")
        views.deps.active_jobs = broken
        r = self.client.get("/voyager-tools/api/review/queue")
        self.assertEqual(r.status_code, 500)
        self.assertIn("does not parse", r.get_json()["error"])

    def test_the_route_is_read_only(self):
        self.unlock()
        events = Path(self.registry.EVENTS_CSV).read_bytes()
        registry_csv = Path(self.registry.REGISTRY_CSV).read_bytes()
        self.assertEqual(self.client.get("/voyager-tools/api/review/queue").status_code, 200)
        self.assertEqual(Path(self.registry.EVENTS_CSV).read_bytes(), events)
        self.assertEqual(Path(self.registry.REGISTRY_CSV).read_bytes(), registry_csv)

    def test_post_is_not_allowed(self):
        self.unlock()
        self.assertEqual(self.client.post("/voyager-tools/api/review/queue").status_code, 405)


if __name__ == "__main__":
    unittest.main()
