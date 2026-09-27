#!/usr/bin/env python3
"""
Module: tests/test_views_d1i.py
Purpose: Route tests for milestone D1i on voyagertools.views: the Clean
         workbench scan and delete with the excluded roots (computed through
         the real driver.space over a scratch nas.yaml, fail closed when they
         cannot be computed), the Voyager 2 stage list and delete, the rewind
         before Voyager 2 (a local folder stands in for the Shelf) and the
         Voyager 2 spot redo.
Inputs:  the temporary roots of test_views.ToolsCase; no NAS, no ssh, no live file.
Outputs: unittest results; every temporary directory is removed in tearDown.

Run from the clone root:
    python3 -m unittest discover -s tests -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from status_seed import seed_status  # noqa: E402
from test_views import FOLDER, HOST, RID_DONE, RID_WAIT_A, SHELF_PARENT, ToolsCase, _walk  # noqa: E402
from voyagertools import rewind_voyager2 as rv2  # noqa: E402
from voyagertools import views  # noqa: E402

SHELF_ROOT = "/volume6/Archive8_12TB/driver_deposits"
OUT = "MRS_T1_3doutput"
OUT_PATH = f"{SHELF_PARENT}/{OUT}"
STAMP = "20261003-091500"


class Layout(ToolsCase):
    """The ToolsCase Workbench with the other stages inside it and a scratch nas.yaml naming them."""

    def setUp(self):
        super().setUp()
        self.v2root = self.workbench / "voyager2_stage"
        self.vigil = self.workbench / "vigil_stage"
        self.ledger = self.workbench / ".transfers_in_flight"
        self.default_bench = self.workbench / "editbench"
        self.cfg = {"host": HOST, "inflight_dir": str(self.ledger),
                    "defaults": {"workbench_root": str(self.workbench), "shelf_root": SHELF_ROOT,
                                 "voyager2_stage_root": str(self.v2root), "vigil_stage_root": str(self.vigil)}}
        views.deps.nas_config = lambda: self.cfg
        views.deps.local_snapshot = _walk
        self.jobs.save_settings({"edit_bench_default": str(self.bench)}, "LO")
        self.unlock()


class Exclusions(Layout):
    def test_the_default_layout_reads_every_stage_the_space_library_names(self):
        layout = views.deps.stage_layout()
        labels = {label: path for label, path in layout["excluded"]}
        self.assertEqual(labels["Voyager 2 stage"], str(self.v2root))
        self.assertEqual(labels["VIGIL stage"], str(self.vigil))
        self.assertEqual(labels[views.LEDGER_LABEL], str(self.ledger))
        benches = [path for label, path in layout["excluded"] if label == "Edit bench"]
        self.assertIn(str(self.bench), benches)
        self.assertIn(str(self.default_bench), benches)
        self.assertNotIn(str(self.workbench), labels.values())
        self.assertEqual(layout["voyager2_stage"], str(self.v2root))

    def test_a_project_bench_is_excluded_too(self):
        other = self.workbench / "bench_cc"
        other.mkdir()
        self.jobs.create_job([{"readable_id": RID_WAIT_A, "site": "MRS", "transect": "T1", "processing_folder": FOLDER,
                               "shelf_location": "/s", "psx_basename": "x.psx"}], str(other), "CC")
        (other / FOLDER).mkdir()
        paths = [f["path"] for f in self.client.get("/voyager-tools/api/workbench").get_json()["folders"]]
        self.assertNotIn(str(other / FOLDER), paths)

    def test_bench_and_stage_copies_are_never_listed_or_deleted(self):
        planted = [self.default_bench / FOLDER, self.bench / FOLDER, self.vigil / "batch_vg_1" / FOLDER,
                   self.ledger / FOLDER]
        for path in planted:
            path.mkdir(parents=True)
        self.remote[f"{SHELF_PARENT}/{FOLDER}"] = {}
        self.registry.upsert(RID_DONE, {"processing_location": f"{HOST}:{SHELF_PARENT}/{FOLDER}"}, "seed")
        body = self.client.get("/voyager-tools/api/workbench").get_json()
        paths = [f["path"] for f in body["folders"]]
        self.assertEqual(paths, [str(self.folder)])
        self.assertIn("Voyager 2 stage", [e["label"] for e in body["excluded"]])
        for path in planted:
            if not str(path).startswith(str(self.workbench)):
                continue
            r = self.client.post("/voyager-tools/api/workbench/delete",
                                 json={"path": str(path), "initials": "LO", "confirm": True})
            self.assertEqual(r.status_code, 400, (path, r.get_json()))
            self.assertIn("never touches", r.get_json()["error"])
            self.assertTrue(path.is_dir())

    def test_an_unreadable_job_store_refuses_the_scan_and_the_delete(self):
        (self.reg_root / "manual_edit" / "jobs").mkdir(parents=True, exist_ok=True)
        (self.reg_root / "manual_edit" / "jobs" / "ME-20260927-010000-LO.json").write_text("{not json")
        r = self.client.get("/voyager-tools/api/workbench")
        self.assertEqual(r.status_code, 500)
        self.assertIn("cannot be read", r.get_json()["error"])
        r = self.client.post("/voyager-tools/api/workbench/delete",
                             json={"path": str(self.folder), "initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 500)
        self.assertTrue(self.folder.exists())

    def test_a_stage_layout_nas_yaml_refuses_is_a_500_sentence(self):
        self.cfg["defaults"]["vigil_stage_root"] = str(self.v2root)
        r = self.client.get("/voyager-tools/api/workbench")
        self.assertEqual(r.status_code, 500)
        self.assertIn("stage folders", r.get_json()["error"])


class Voyager2Stage(Layout):
    def setUp(self):
        super().setUp()
        self.turn = self.v2root / "batch_v2_20261002-120000" / "turn001"
        self.proc, self.out = self.turn / FOLDER, self.turn / OUT
        (self.proc / "frames").mkdir(parents=True)
        (self.out / "logs").mkdir(parents=True)
        (self.out / "logs" / "step2.log").write_text("log")
        for rid in (RID_DONE,):
            self.registry.upsert(rid, {"processing_location": f"{HOST}:{SHELF_PARENT}/{FOLDER}", "output_folder": OUT,
                                       "output_location": f"{HOST}:{OUT_PATH}", "step2_status": "complete",
                                       "output_location_verified": "2026-10-02 14:30 AST"}, "seed")
        self.remote[OUT_PATH] = _walk(str(self.out))

    def test_the_scan_lists_the_stage_and_deletes_after_the_verified_deposit(self):
        body = self.client.get("/voyager-tools/api/workbench").get_json()
        stage = {f["path"]: f for f in body["voyager2_stage"]["folders"]}
        self.assertEqual(body["voyager2_stage"]["root"], str(self.v2root))
        self.assertTrue(stage[str(self.proc)]["deletable"])
        self.assertTrue(stage[str(self.out)]["deletable"])
        self.assertNotIn(str(self.proc), [f["path"] for f in body["folders"]])
        for path in (self.out, self.proc):
            r = self.client.post("/voyager-tools/api/workbench/delete",
                                 json={"path": str(path), "initials": "LO", "confirm": True})
            self.assertEqual(r.status_code, 200, r.get_json())
            self.assertFalse(path.exists())
        self.assertEqual(len(self.log_rows), 2)
        self.assertIn("Voyager 2 stage", self.log_rows[0]["purpose"])

    def test_a_reserved_transect_keeps_its_stage_copies(self):
        self.registry.set_facts(RID_DONE, "voyager2", {"reserved_by": "batch_v2_20261003-090000/turn001"}, "voyager2:LO")
        r = self.client.post("/voyager-tools/api/workbench/delete",
                             json={"path": str(self.proc), "initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 409)
        self.assertIn("batch_v2_20261003-090000/turn001", r.get_json()["error"])
        self.assertTrue(self.proc.is_dir())

    def test_a_test_copy_is_refused(self):
        test_copy = self.v2root / "test_20260927-080000" / FOLDER
        test_copy.mkdir(parents=True)
        r = self.client.post("/voyager-tools/api/workbench/delete",
                             json={"path": str(test_copy), "initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(test_copy.is_dir())


class RewindBeforeVoyager2(Layout):
    def setUp(self):
        super().setUp()
        self.shelf = self.root / "shelf"
        self.local_out = Path(str(self.shelf) + OUT_PATH)
        self.local_out.mkdir(parents=True)
        (self.local_out / "vigil_manifest.json").write_text("{}")
        seed_status(self.registry, RID_DONE, "done", "2026-09-30 10:00:00",
                    processing_location=f"{HOST}:{SHELF_PARENT}/{FOLDER}", output_folder=OUT,
                    output_location=f"{HOST}:{OUT_PATH}", output_location_verified="2026-10-02 14:30 AST",
                    step2_status="complete", step2_finished="2026-10-02 14:05:00", step="2", stage="done")
        self.registry.set_facts(RID_DONE, "voyager2", {"run_id": "TCRMP_V2_2oct26_LO_MRS1"}, "carousel")
        views.deps.archive_shelf_output = self.archive

    def archive(self, dest):
        """The Shelf rename on the local stand-in."""
        local = Path(str(self.shelf) + dest)
        if not local.exists():
            return None
        target = f"{dest}{rv2.ARCHIVE_INFIX}{STAMP}"
        local.rename(str(self.shelf) + target)
        return target

    def test_targets_and_rows_carry_voyager2(self):
        body = self.client.get("/voyager-tools/api/rewind/rows").get_json()
        self.assertIn("before_step2", [t["value"] for t in body["targets"]])
        self.assertEqual(body["rows"][0]["step2_status"], "complete")

    def test_plan_then_apply_renames_and_clears(self):
        r = self.client.post("/voyager-tools/api/rewind/plan", json={"readable_id": RID_DONE, "target": "before_step2"})
        self.assertEqual(r.status_code, 200, r.get_json())
        plan = r.get_json()["plan"]
        self.assertEqual(plan["shelf_folder"], OUT_PATH)
        self.assertEqual([x["readable_id"] for x in plan["rows"]], [RID_DONE])
        r = self.client.post("/voyager-tools/api/rewind/apply",
                             json={"readable_id": RID_DONE, "target": "before_step2", "initials": "LO"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/voyager-tools/api/rewind/apply",
                             json={"readable_id": RID_DONE, "target": "before_step2", "initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 200, r.get_json())
        manifest = r.get_json()["manifest"]
        self.assertEqual(manifest["archived_to"], f"{OUT_PATH}{rv2.ARCHIVE_INFIX}{STAMP}")
        self.assertFalse(self.local_out.exists())
        row = self.registry.get(RID_DONE)
        self.assertEqual(row["step2_status"], "")
        self.assertEqual(row["step1_status"], "complete")
        self.assertIn("Voyager 2 result rewound", row["notes"])
        self.assertEqual(self.log_rows[-1]["status"], "archived")
        self.assertEqual(self.log_rows[-1]["destination_uri"], f"{HOST}:{manifest['archived_to']}")

    def test_the_vigil_confirmation_travels_in_the_body(self):
        self.registry.set_facts(RID_DONE, "vigil", {"staged_manifest": "/mnt/rip/driver_staging/vigil_stage/b/m.json"},
                                "carousel")
        body = {"readable_id": RID_DONE, "target": "before_step2", "initials": "LO", "confirm": True}
        r = self.client.post("/voyager-tools/api/rewind/apply", json=body)
        self.assertEqual(r.status_code, 409)
        self.assertIn("VIGIL", r.get_json()["error"])
        self.assertTrue(self.local_out.exists())
        r = self.client.post("/voyager-tools/api/rewind/apply", json=dict(body, confirm_vigil=True))
        self.assertEqual(r.status_code, 200, r.get_json())

    def test_refusals_answer_409_and_a_missing_shelf_root_500(self):
        self.registry.set_facts(RID_DONE, "voyager2", {"reserved_by": "batch_v2_1/turn001"}, "voyager2:LO")
        r = self.client.post("/voyager-tools/api/rewind/plan", json={"readable_id": RID_DONE, "target": "before_step2"})
        self.assertEqual(r.status_code, 409)
        del self.cfg["defaults"]["shelf_root"]
        r = self.client.post("/voyager-tools/api/rewind/plan", json={"readable_id": RID_DONE, "target": "before_step2"})
        self.assertEqual(r.status_code, 500)
        self.assertIn("shelf_root", r.get_json()["error"])

    def test_a_nas_failure_is_a_409_and_nothing_changes(self):
        def refuse(dest):
            raise RuntimeError("ssh did not answer within 60 s")
        views.deps.archive_shelf_output = refuse
        r = self.client.post("/voyager-tools/api/rewind/apply",
                             json={"readable_id": RID_DONE, "target": "before_step2", "initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 409)
        self.assertIn("ssh did not answer", r.get_json()["error"])
        self.assertEqual(self.registry.get(RID_DONE)["step2_status"], "complete")


class SpotRedoVoyager2(RewindBeforeVoyager2):
    def test_plan_and_apply_write_the_redo_request(self):
        r = self.client.post("/voyager-tools/api/spot-redo/plan", json={"readable_id": RID_DONE, "phase": "voyager2"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual([x["readable_id"] for x in r.get_json()["plan"]["rows"]], [RID_DONE])
        r = self.client.post("/voyager-tools/api/spot-redo",
                             json={"readable_id": RID_DONE, "phase": "voyager2", "initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 200, r.get_json())
        fact = self.registry.fact_sections(RID_DONE)["voyager2"]["redo_requested"]
        self.assertTrue(fact["value"].startswith("requested "))
        self.assertEqual(fact["recorded_by"], "voyager_tools:LO")
        self.assertTrue(self.local_out.exists())

    def test_the_voyager1_plan_route_still_answers_the_rewind_plan(self):
        r = self.client.post("/voyager-tools/api/spot-redo/plan", json={"readable_id": RID_DONE, "phase": "voyager1"})
        self.assertEqual(r.status_code, 409)  # its folder is on the Shelf, so the pull-back is the way
        self.assertIn("pullback_command", r.get_json())

    def test_an_unknown_phase_is_a_400(self):
        for route in ("/voyager-tools/api/spot-redo/plan", "/voyager-tools/api/spot-redo"):
            r = self.client.post(route, json={"readable_id": RID_DONE, "phase": "vigil", "initials": "LO",
                                              "confirm": True})
            self.assertEqual(r.status_code, 400, route)

    def test_a_row_without_a_complete_result_is_a_409(self):
        r = self.client.post("/voyager-tools/api/spot-redo",
                             json={"readable_id": RID_WAIT_A, "phase": "voyager2", "initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 409)


if __name__ == "__main__":
    unittest.main()
