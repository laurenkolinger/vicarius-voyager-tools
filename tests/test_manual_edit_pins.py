#!/usr/bin/env python3
"""
Module: tests/test_manual_edit_pins.py
Purpose: Pins the pure functions of the Uploads and Timelines panes of the
         Atlas control panel (static/vt_uploads.js and static/vt_timelines.js,
         program C milestones C7 and C8) under node: which transects are
         ready to upload, uploading or in admin review; the upload sets of
         the last 7 days; the confirmation's lines naming each transect and
         what happens to its Shelf copy; the timelines filters, CSV, durations,
         progress, summary and running lines. Also static source guards: the
         pane seam, the initials rule, no innerHTML of a server value without
         esc(), no desktop initials fallback, the dialog outside the panel.
Inputs:  the clone's static/vt_uploads.js and static/vt_timelines.js; node.
Outputs: unittest results; nothing is written.

Run from the clone root:
    python3 -m unittest tests.test_manual_edit_pins -v
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

CLONE = Path(__file__).resolve().parent.parent
UPLOADS_JS = CLONE / "static" / "vt_uploads.js"
TIMELINES_JS = CLONE / "static" / "vt_timelines.js"
NODE = shutil.which("node")
NOW_MS = 1790524800000  # 2026-09-27 12:00 AST


def run_node(path: Path, body: str) -> object:
    """Run a snippet under node with the script's helpers loaded as `h`; return the JSON it prints."""
    script = ("const h = require(" + json.dumps(str(path)) + ");\n"
              "const out = (function () {\n" + body + "\n})();\n"
              "process.stdout.write(JSON.stringify(out));\n")
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    if done.returncode != 0:
        raise AssertionError("node failed: " + done.stderr[-1500:])
    return json.loads(done.stdout)


def card(job_id, created_by, transects):
    """One active project card in program B's /manual-edit/api/jobs?active=1 shape."""
    return {"job_id": job_id, "created_by": created_by, "created_at": "2026-09-25T10:00:00-04:00", "phase": "editing",
            "transects": [dict({"processing_folder": t["psx"].split("_20")[0] + "_3D", "site": t["psx"][:3],
                                "site_name": "", "transect": t["psx"].split("_")[1], "state_word": "",
                                "round": 0, "upload_id": None, "upload_error": "", "removal_error": ""}, **t)
                            for t in transects]}


CARDS = [card("ME-20260925-100000-CC", "CC", [{"psx": "MRS_T2_2023_2024.psx", "state": "upload"},
                                              {"psx": "MRS_T3_2023_2024.psx", "state": "review", "round": 1},
                                              {"psx": "MRS_T4_2023_2024.psx", "state": "uploading",
                                               "upload_id": "UP-20260927-110000-LO"}]),
         card("ME-20260926-100000-LO", "LO", [{"psx": "LBH_T3_2024_2025.psx", "state": "upload",
                                               "site_name": "Lang Bank Red Hind FSA"},
                                              {"psx": "LBH_T4_2024_2025.psx", "state": "editing"},
                                              {"psx": "LBH_T5_2024_2025.psx", "state": "upload",
                                               "upload_id": "UP-20260927-100000-LO"}])]


@unittest.skipUnless(NODE, "node is not installed on this box")
class UploadsHelpers(unittest.TestCase):
    def test_node_check(self):
        for path in (UPLOADS_JS, TIMELINES_JS):
            with self.subTest(path=path.name):
                done = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=30)
                self.assertEqual(done.returncode, 0, done.stderr)

    def test_ready_running_and_review_lists(self):
        out = run_node(UPLOADS_JS, "var c = " + json.dumps(CARDS) + ";"
                       "return [h.readyItems(c), h.runningItems(c), h.reviewItems(c)];")
        ready, running, review = out
        self.assertEqual([(i["job_id"], i["psx"]) for i in ready],
                         [("ME-20260925-100000-CC", "MRS_T2_2023_2024.psx"),
                          ("ME-20260926-100000-LO", "LBH_T3_2024_2025.psx")],
                         "a transect already held by a set is not ready")
        self.assertEqual(ready[0]["editor"], "CC")
        self.assertEqual(ready[1]["site_name"], "Lang Bank Red Hind FSA")
        self.assertEqual([(i["psx"], i["upload_id"]) for i in running],
                         [("MRS_T4_2023_2024.psx", "UP-20260927-110000-LO")])
        self.assertEqual([(i["psx"], i["round"]) for i in review], [("MRS_T3_2023_2024.psx", 1)])

    def test_hostile_cards(self):
        out = run_node(UPLOADS_JS, "return [h.readyItems(null), h.readyItems([null, {transects: 'x'}, {job_id: 'J',"
                                   " transects: [null, {state: 'upload'}]}]), h.runningItems(undefined)];")
        self.assertEqual(out, [[], [], []], "a transect with no psx is never offered")

    def test_recent_uploads_keep_seven_days_newest_first_and_the_unreadable(self):
        uploads = [{"upload_id": "UP-20260927-110000-LO", "created_at": "2026-09-27T11:00:00-04:00", "status": "running"},
                   {"upload_id": "UP-20260919-110000-LO", "created_at": "2026-09-19T11:00:00-04:00", "status": "done"},
                   {"upload_id": "UP-20260921-110000-LO", "created_at": "2026-09-21T11:00:00-04:00", "status": "failed"},
                   {"upload_id": "UP-20260926-080000-LO", "corrupt": True, "error": "the upload set is not JSON"},
                   {"upload_id": "UP-20260925-080000-LO", "created_at": "garbage", "status": "done"}]
        out = run_node(UPLOADS_JS, f"return h.recentUploads({json.dumps(uploads)}, {NOW_MS}, 7);")
        self.assertEqual([u["upload_id"] for u in out],
                         ["UP-20260927-110000-LO", "UP-20260926-080000-LO", "UP-20260921-110000-LO"])

    def test_plan_lines_name_each_transect_and_its_shelf_copy(self):
        items = [{"psx": "MRS_T2_2023_2024.psx", "processing_folder": "MRS_T2_3D", "site": "MRS", "site_name": "",
                  "transect": "T2"},
                 {"psx": "LBH_T3_2024_2025.psx", "processing_folder": "LBH_T3_3D", "site": "LBH",
                  "site_name": "Lang Bank Red Hind FSA", "transect": "T3"}]
        out = run_node(UPLOADS_JS, "var i = " + json.dumps(items) + ";"
                       "return [h.uploadPlanLines(i, true), h.uploadPlanLines(i.slice(0, 1), false),"
                       " h.uploadPlanLines(i.slice(0, 1), null)];")
        archive, overwrite, unknown = out
        self.assertEqual(archive[0], "Upload 2 transects to the Shelf, one after another:")
        self.assertEqual(archive[1], "MRS T2 (MRS_T2_2023_2024.psx): the Shelf folder MRS_T2_3D is renamed to "
                                     "MRS_T2_3D_voy1_archive and kept, then the edit goes up as MRS_T2_3D.")
        self.assertIn("Lang Bank Red Hind FSA (LBH) T3 (LBH_T3_2024_2025.psx)", archive[2])
        self.assertEqual(overwrite[0], "Upload 1 transect to the Shelf, one after another:")
        self.assertEqual(overwrite[1], "MRS T2 (MRS_T2_2023_2024.psx): the edit replaces the Shelf folder MRS_T2_3D, "
                                       "and files the edit removed are deleted there.")
        self.assertEqual(unknown[1], "MRS T2 (MRS_T2_2023_2024.psx): the Shelf copy is archived or overwritten as "
                                     "the Shelf copy setting says when the upload starts.")

    def test_status_roles(self):
        out = run_node(UPLOADS_JS, "return ['queued','running','done','failed','stopped','interrupted','odd']"
                                   ".map(h.statusRole);")
        self.assertEqual(out, ["role-planned", "role-in-progress", "role-machine", "role-failed", "role-attention",
                               "role-attention", "role-empty"])


ROWS = [{"job_id": "ME-20260925-100000-CC", "psx": "MRS_T2_2023_2024.psx", "state": "review", "editor": "CC",
         "created_at": "2026-09-25T10:00:00-04:00", "site": "MRS", "site_name": "", "transect": "T2", "round": 1,
         "pulled_at": "2026-09-25T12:00:00-04:00", "editing_s": 97200, "metashape_s": 18720, "unknown_opens": 2,
         "review_s": 75600, "upload_wait_s": 0, "uploaded_at": None, "total_s": 172800, "last_note": '=cmd|"x"'},
        {"job_id": "ME-20260920-090000-LO", "psx": "MRS_T1_2023_2025.psx", "state": "done", "editor": "LO",
         "created_at": "2026-09-20T09:00:00-04:00", "site": "MRS", "site_name": "", "transect": "T1", "round": 0,
         "pulled_at": "2026-09-20T11:00:00-04:00", "editing_s": 165600, "metashape_s": 120, "unknown_opens": 0,
         "review_s": 0, "upload_wait_s": 10800, "uploaded_at": "2026-09-22T12:00:00-04:00", "total_s": 176400,
         "last_note": 'a, "b"'}]


@unittest.skipUnless(NODE, "node is not installed on this box")
class TimelinesHelpers(unittest.TestCase):
    def test_filter_rows(self):
        out = run_node(TIMELINES_JS, "var r = " + json.dumps(ROWS) + ";"
                       "return [h.filterRows(r, {}), h.filterRows(r, {job: 'ME-20260920-090000-LO'}),"
                       " h.filterRows(r, {state: 'review'}), h.filterRows(r, {editor: 'LO'}),"
                       " h.filterRows(r, {since: '2026-09-21'}), h.filterRows(r, {state: 'done', since: '2026-09-21'}),"
                       " h.filterRows(null, {})].map(function (l) { return l.map(function (x) { return x.psx; }); });")
        self.assertEqual(out, [["MRS_T2_2023_2024.psx", "MRS_T1_2023_2025.psx"], ["MRS_T1_2023_2025.psx"],
                               ["MRS_T2_2023_2024.psx"], ["MRS_T1_2023_2025.psx"], ["MRS_T2_2023_2024.psx"], [], []])

    def test_rows_to_csv_quotes_every_cell_and_guards_formulas(self):
        text = run_node(TIMELINES_JS, "return h.rowsToCsv(" + json.dumps(ROWS) + ", [{key: 'psx', label: 'Transect'},"
                                      " {key: 'editing_s', label: 'Editing (s)'}, {key: 'last_note', label: 'Last note'},"
                                      " {key: 'uploaded_at', label: 'Uploaded'}]);")
        lines = text.split("\r\n")
        self.assertEqual(lines[0], '"Transect","Editing (s)","Last note","Uploaded"')
        self.assertEqual(lines[1], "\"MRS_T2_2023_2024.psx\",\"97200\",\"'=cmd|\"\"x\"\"\",\"\"")
        self.assertEqual(lines[2], '"MRS_T1_2023_2025.psx","165600","a, ""b""","2026-09-22T12:00:00-04:00"')

    def test_format_duration(self):
        out = run_node(TIMELINES_JS, "return [null, undefined, NaN, -5, 0, 45, 59.6, 600, 3599, 18720, 172799, 336060,"
                                     " '12'].map(h.formatDuration);")
        self.assertEqual(out, ["", "", "", "", "0 s", "45 s", "1 min", "10 min", "60 min", "5.2 h", "48.0 h",
                               "3.9 d", ""])

    def test_progress_line(self):
        out = run_node(TIMELINES_JS, "return [h.progressLine({transects: 4, uploaded: 3}),"
                                     " h.progressLine({transects: 1, uploaded: 1}), h.progressLine({transects: 0}),"
                                     " h.progressLine(null)];")
        self.assertEqual(out, ["3 of 4 uploaded", "1 of 1 uploaded", "No transects", "No transects"])

    def test_summary_line_puts_admin_review_first(self):
        summary = {"projects": 5, "transects": 14, "median_metashape_s": 18720,
                   "by_state": {"todo": 0, "editing": 0, "review": 2, "upload": 3, "uploading": 0, "done": 9}}
        out = run_node(TIMELINES_JS, "return [h.summaryLine(" + json.dumps(summary) + "),"
                                     " h.summaryLine({projects: 1, transects: 1, by_state: {editing: 1},"
                                     " median_metashape_s: null}), h.summaryLine({projects: 0, transects: 0,"
                                     " by_state: {}})];")
        self.assertEqual(out[0], "5 manual edit projects, 14 transects: 2 in admin review, 3 marked for re-upload, "
                                 "9 uploaded; median time in Metashape 5.2 h")
        self.assertEqual(out[1], "1 manual edit project, 1 transect: 1 editing")
        self.assertEqual(out[2], "No manual edit project recorded yet.")

    def test_running_lines(self):
        out = run_node(TIMELINES_JS, "return [h.runningLine({upload: {item_state: 'copying', bytes_done: 100,"
                                     " bytes_total: 400}}), h.runningLine({upload: {item_state: 'verifying',"
                                     " bytes_done: null, bytes_total: 400}}), h.runningLine({upload: {item_state: ''}}),"
                                     " h.runningLine({checks_open: true}), h.runningLine({}),"
                                     " h.pullLine({bytes_done: 5, bytes_total: 20}), h.pullLine({bytes_done: null}),"
                                     " h.pullLine(null)];")
        self.assertEqual(out, ["Copying, 25 percent", "Verifying", "Waiting to upload", "Checks window open", "",
                               "Pulling to the edit bench, 25 percent", "", ""])

    def test_state_roles(self):
        out = run_node(TIMELINES_JS, "return ['todo','editing','review','upload','uploading','restarting','done','odd']"
                                     ".map(h.stateRole);")
        self.assertEqual(out, ["role-empty", "role-in-progress", "role-attention", "role-planned", "role-in-progress",
                               "role-in-progress", "role-verified", "role-empty"])

    def test_filters_from_storage_are_validated(self):
        out = run_node(TIMELINES_JS, "return [h.cleanFilters({job: 'ME-20260925-100000-CC', state: 'review',"
                                     " editor: 'cc', since: '2026-09-25'}), h.cleanFilters({job: '<b>', state: 7,"
                                     " editor: 'ABCDE', since: 'soon'}), h.cleanFilters(null)];")
        self.assertEqual(out, [{"job": "ME-20260925-100000-CC", "state": "review", "editor": "CC", "since": "2026-09-25"},
                               {"job": "", "state": "", "editor": "", "since": ""},
                               {"job": "", "state": "", "editor": "", "since": ""}])


class SourceGuards(unittest.TestCase):
    def setUp(self):
        self.uploads = UPLOADS_JS.read_text(encoding="utf-8")
        self.timelines = TIMELINES_JS.read_text(encoding="utf-8")

    def test_the_pane_seam_and_the_initials_rule(self):
        self.assertRegex(self.uploads, r'register\("uploads"')
        self.assertRegex(self.timelines, r'register\("timelines"')
        self.assertIn("/^[A-Z]{2,4}$/", self.uploads)
        self.assertNotIn("VICBOOT", self.uploads + self.timelines)

    def test_every_innerhtml_goes_through_esc_or_an_html_helper(self):
        for name, text in (("vt_uploads.js", self.uploads), ("vt_timelines.js", self.timelines)):
            for line in text.splitlines():
                if ".innerHTML =" in line and ("data." in line or "row." in line or "item." in line):
                    with self.subTest(name=name, line=line.strip()[:60]):
                        self.assertTrue("esc(" in line or re.search(r"[a-z]Html\b", line), line.strip())

    def test_the_upload_dialog_sits_on_the_body_and_uses_the_editor_s_component(self):
        self.assertIn("document.body.appendChild", self.uploads)
        self.assertIn("ManualEditUpload.mount", self.uploads)
        self.assertIn('var MANUAL_EDIT_BASE = "/manual-edit";', self.uploads)
        self.assertIn('MANUAL_EDIT_BASE + "/static/manual_edit_upload.js"', self.uploads)
        self.assertIn('"voyager-tools vt-upload-layer"', self.uploads)

    def test_no_voyager_tools_or_banned_copy(self):
        for name, text in (("vt_uploads.js", self.uploads), ("vt_timelines.js", self.timelines)):
            strings = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', text)
            with self.subTest(name=name):
                self.assertFalse([s for s in strings if "Voyager tools" in s])
                self.assertFalse([s for s in strings if re.search(r"\bland(s|ed|ing)?\b", s, re.IGNORECASE)])
                self.assertFalse([s for s in strings if "—" in s or "–" in s])


if __name__ == "__main__":
    unittest.main()
