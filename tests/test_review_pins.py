#!/usr/bin/env python3
"""
Module: tests/test_review_pins.py
Purpose: Pins the pure functions of the review content and the Review queue
         pane (static/vt_review.js, program C milestone C6) under node: the
         decision body rules of program B's review route (readyToRecord), one
         round numbering, times and results in Atlantic Standard Time, the
         limits line, the Start over confirmation, the time in editing and in
         review from program A's intervals, the notes grouped by round with
         each earlier round's outcome, and the queue's count line. Also static
         source guards: the routes the script posts, no markup built from a
         server value without esc(), no eval, and the prose rules on every
         line and tooltip.
Inputs:  the clone's static/vt_review.js; node.
Outputs: unittest results; nothing is written.

Run from the clone root:
    python3 -m unittest tests.test_review_pins -v
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

CLONE = Path(__file__).resolve().parent.parent
REVIEW_JS = CLONE / "static" / "vt_review.js"
TIMELINES_JS = CLONE / "static" / "vt_timelines.js"
PANEL_CSS = CLONE / "static" / "voyager_tools.css"
ROLES_CSS = CLONE / "static" / "vicarius_roles.css"
NODE = shutil.which("node")
DASHES = ("–", "—")
BANNED_WORD = re.compile(r"\bland(s|ed|ing)?\b", re.IGNORECASE)


def run_node(body: str) -> object:
    """Run a snippet under node with the script's helpers loaded as `h`; return the JSON it prints."""
    script = ("const h = require(" + json.dumps(str(REVIEW_JS)) + ");\n"
              "const out = (function () {\n" + body + "\n})();\n"
              "process.stdout.write(JSON.stringify(out));\n")
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    if done.returncode != 0:
        raise AssertionError("node failed: " + done.stderr[-1500:])
    return json.loads(done.stdout)


@unittest.skipUnless(NODE, "node is not installed on this box")
class Helpers(unittest.TestCase):
    def test_node_check(self):
        done = subprocess.run([NODE, "--check", str(REVIEW_JS)], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_ready_to_record_applies_program_b_body_rules(self):
        cases = run_node("""return [
          h.readyToRecord({decision: "upload", report: "  fine  ", instructions: "", initials: " lo ", startUpload: false}),
          h.readyToRecord({decision: "upload", report: "", instructions: "", initials: "LO"}),
          h.readyToRecord({decision: "upload", report: "ok", instructions: "do this", initials: "LO"}),
          h.readyToRecord({decision: "return", report: "ok", instructions: "   ", initials: "LO"}),
          h.readyToRecord({decision: "restart", report: "ok", instructions: "again", initials: "LO", startUpload: true}),
          h.readyToRecord({decision: "return", report: "ok", instructions: "again", initials: "L0"}),
          h.readyToRecord({decision: "return", report: "ok", instructions: "again", initials: "ABCDE"}),
          h.readyToRecord({decision: "upload", report: new Array(4002).join("x"), instructions: "", initials: "LO"}),
          h.readyToRecord({decision: "return", report: "ok", instructions: new Array(2002).join("x"), initials: "LO"}),
          h.readyToRecord({decision: "upload", report: "a\\u0000b", instructions: "", initials: "LO"}),
          h.readyToRecord({decision: "burn", report: "ok", instructions: "", initials: "LO"}),
          h.readyToRecord({decision: "return", report: "ok", instructions: "again", initials: "AB"}),
          h.readyToRecord({decision: "restart", report: "<img src=x onerror=alert(1)>", instructions: "x", initials: "AGENT"}),
          h.readyToRecord(null)
        ];""")
        self.assertEqual([c["ok"] for c in cases],
                         [True, False, False, False, False, False, False, False, False, False, False, True, False,
                          False])
        self.assertEqual(cases[0]["body"], {"decision": "upload", "report": "fine", "instructions": "", "initials": "LO",
                                            "start_upload": False})
        self.assertIs(cases[11]["body"]["start_upload"], False)
        self.assertEqual(cases[5]["line"], "Type your initials, two to four letters, for example LO.")
        panel_line = run_node("return h.LINES.initialsPanel;")
        self.assertEqual(panel_line, "Type your initials, two to four letters, at the top of the panel.")
        self.assertEqual(cases[1]["line"], "Write the admin report before recording a decision.")
        self.assertEqual(cases[3]["line"], "Write the instructions to the editor for a send back or a start over.")
        for case in cases[1:11] + cases[12:]:
            self.assertTrue(case["line"].endswith(".") and not case["line"].startswith("You"), case)

    def test_round_label_counts_rounds_from_one(self):
        out = run_node("""return [h.roundLabel(0), h.roundLabel(1), h.roundLabel("2"), h.roundLabel(-1),
                                  h.roundLabel(1.5), h.roundLabel(null), h.roundLabel(""), h.roundLabel("x")];""")
        self.assertEqual(out, ["Edit round 1", "Edit round 2", "Edit round 3", "", "", "", "", ""])

    def test_words_for_levels_times_and_results(self):
        out = run_node("""return {
          levels: [h.levelWord("ok"), h.levelWord("warn"), h.levelWord("stop"), h.levelWord("odd")],
          stamp: h.astText("2026-09-27T10:31:05-04:00"), utc: h.astText("2026-09-27T14:31:05Z"), blank: h.astText(""),
          span: [h.spanText(5400), h.spanText(59), h.spanText(0), h.spanText(null), h.spanText(190000), h.spanText(-3)],
          result: [h.resultLine({decision: "upload", by: "LO", at: "2026-09-27T10:31:05-04:00"}),
                   h.resultLine({decision: "return", by: "AB", at: "2026-09-27T10:31:05-04:00"}),
                   h.resultLine({decision: "restart", by: "LO", at: "2026-09-27T10:31:05-04:00"}),
                   h.resultLine({decision: "back_to_review", by: "LO", at: "2026-09-27T10:31:05-04:00"})],
          esc: h.esc('<script>"x" & \\'y\\'</script>'),
          url: h.projectUrl("ME-20260927-101500-LO", "a b#?.psx", "/review")
        };""")
        self.assertEqual(out["levels"], ["passed", "warned", "failed", "odd"])
        self.assertEqual((out["stamp"], out["utc"], out["blank"]), ("2026-09-27 10:31 AST", "2026-09-27 10:31 AST", "-"))
        self.assertEqual(out["span"], ["1 h 30 min", "under a minute", "under a minute", "-", "2 d 4 h", "-"])
        self.assertEqual(out["result"], ["Marked for upload by LO at 2026-09-27 10:31 AST.",
                                         "Sent back to the editor by AB at 2026-09-27 10:31 AST.",
                                         "Start over recorded by LO at 2026-09-27 10:31 AST.",
                                         "Moved back to admin review by LO at 2026-09-27 10:31 AST."])
        self.assertEqual(out["esc"], "&lt;script&gt;&quot;x&quot; &amp; &#39;y&#39;&lt;/script&gt;")
        self.assertEqual(out["url"], "/manual-edit/api/jobs/ME-20260927-101500-LO/projects/a%20b%23%3F.psx/review")

    def test_the_limits_line(self):
        out = run_node("""return [
          h.limitsLine({scale_error_max_mm: 4.94, scale_error_max_percent: 0.66, align_spread_max_mm: 8.65,
                        saved_at: "2026-09-26T21:54:25-04:00", saved_by: "LO"}),
          h.limitsLine({scale_error_max_mm: 4.94, scale_error_max_percent: 0.66, align_spread_max_mm: 8.65,
                        saved_at: "", saved_by: "shipped defaults"}),
          h.limitsLine({}), h.limitsLine(null)];""")
        self.assertEqual(out[0], "Judged under 4.94 mm or 0.66 % scale error and 8.65 mm marker spread, saved "
                                 "2026-09-26 21:54 AST by LO.")
        self.assertEqual(out[1], "Judged under 4.94 mm or 0.66 % scale error and 8.65 mm marker spread, the shipped "
                                 "defaults (no settings file).")
        self.assertEqual(out[2:], ["", ""])

    def test_the_start_over_confirmation_names_the_bench_and_the_shelf_folder(self):
        out = run_node("""var shelf = "/volume6/Archive8_12TB/driver_deposits/TCRMP_run/MRS_T2_3D";
          return [h.restartConfirmText({processing_folder: "MRS_T2_3D", on_bench: {path: "/bench/MRS_T2_3D"}}, false, shelf),
                  h.restartConfirmText({processing_folder: "MRS_T2_3D", on_bench: {path: "/bench/MRS_T2_3D"}}, false, "  "),
                  h.restartConfirmText({processing_folder: "MRS_T2_3D", restart: {stage: "aside", aside: ""}}, true, shelf),
                  h.restartConfirmText({processing_folder: "MRS_T2_3D", restart: "bad"}, true)];""")
        self.assertEqual(out[0], "VICARIUS moves /bench/MRS_T2_3D aside as MRS_T2_3D_restart_ followed by the date "
                                 "and time of the move, and pulls a fresh copy from "
                                 "/volume6/Archive8_12TB/driver_deposits/TCRMP_run/MRS_T2_3D.")
        self.assertTrue(out[1].endswith("and pulls a fresh copy from the Shelf."), out[1])
        self.assertEqual(out[2], "VICARIUS resumes the start over of MRS_T2_3D from the move aside.")
        self.assertEqual(out[3], "VICARIUS resumes the start over of MRS_T2_3D from the stage it reached.")

    def test_the_resume_line_and_the_texts_a_resume_posts_again(self):
        out = run_node("""return {
          line: [h.resumeLine({restart: {stage: "pulling", error: "The copy from the Shelf stopped: No space left on device."}}),
                 h.resumeLine({restart: {stage: "space", error: "ssh gave no answer in 60 s"}}),
                 h.resumeLine({restart: {stage: "odd", error: ""}}), h.resumeLine({restart: null})],
          kept: h.resumeTexts([
              {kind: "admin_review", decision: "return", source_id: "J:P:review:r0", text: "old report"},
              {kind: "admin_review", decision: "restart", source_id: "J:P:review:r1", text: "Start again."},
              {kind: "return_instructions", decision: "restart", source_id: "J:P:review:r1", text: "Re-place markers."},
              {kind: "return_instructions", decision: "return", source_id: "J:P:review:r0", text: "old"}]),
          noInstructions: h.resumeTexts([{kind: "admin_review", decision: "restart", source_id: "S", text: "x"}]),
          wrongSource: h.resumeTexts([{kind: "admin_review", decision: "restart", source_id: "S", text: "x"},
                                      {kind: "return_instructions", decision: "restart", source_id: "T", text: "y"}]),
          blank: h.resumeTexts([{kind: "admin_review", decision: "restart", source_id: "S", text: "  "},
                                {kind: "return_instructions", decision: "restart", source_id: "S", text: "y"}]),
          none: [h.resumeTexts([]), h.resumeTexts(null), h.resumeTexts("x")]
        };""")
        self.assertEqual(out["line"], [
            "The start over stopped at the pull of the fresh copy. The copy from the Shelf stopped: No space left on device.",
            "The start over stopped at the free space check. Ssh gave no answer in 60 s.",
            "The start over stopped at a stage not recorded.",
            "The start over stopped at a stage not recorded."])
        for line in out["line"]:
            self.assertNotIn(": The", line.split(". ", 1)[0])
        self.assertEqual(out["kept"], {"report": "Start again.", "instructions": "Re-place markers."})
        self.assertEqual([out["noInstructions"], out["wrongSource"], out["blank"]], [None, None, None])
        self.assertEqual(out["none"], [None, None, None])

    def test_the_head_reads_a_stopped_start_over_as_stopped(self):
        out = run_node("""return [
          h.stateBadge({state: "restarting", restart: {stage: "pulling", error: "No space left on device."}}),
          h.stateBadge({state: "restarting", restart: {stage: "pulling", error: ""}}),
          h.stateBadge({state: "review"}), h.stateBadge({state: "upload"}), h.stateBadge({state: "done"}),
          h.stateBadge({state: "odd"}), h.stateBadge(null)];""")
        self.assertEqual(out[0], {"role": "role-failed", "words": "start over stopped at the pull of the fresh copy"})
        self.assertEqual(out[1], {"role": "stage-bench", "words": "starting over"})
        self.assertEqual(out[2], {"role": "stage-review", "words": "admin review"})
        self.assertEqual(out[3], {"role": "stage-upload", "words": "marked for upload"})
        self.assertEqual(out[4], {"role": "stage-edited", "words": "uploaded"})
        self.assertEqual(out[5], {"role": "role-empty", "words": "odd"})
        self.assertEqual(out[6], {"role": "role-empty", "words": "state not recorded"})

    def test_state_chips_match_the_atlas_and_the_timelines_pane(self):
        review = run_node("return h.STATE_ROLES;")
        timelines = json.loads(subprocess.run(
            [NODE, "-e", "const t = require(" + json.dumps(str(TIMELINES_JS)) + ");"
             "const s = ['todo','editing','review','upload','uploading','restarting','done'];"
             "const o = {}; s.forEach(function (k) { o[k] = t.stateRole(k); });"
             "process.stdout.write(JSON.stringify(o));"], capture_output=True, text=True, timeout=30).stdout)
        self.assertEqual(review, timelines)
        self.assertEqual(review, {"todo": "stage-bench", "editing": "stage-bench", "review": "stage-review",
                                  "upload": "stage-upload", "uploading": "stage-upload", "restarting": "stage-bench",
                                  "done": "stage-edited"})
        self.assertEqual(run_node("return h.LEVEL_ROLES;"),
                         {"ok": "check-passed", "warn": "role-attention", "stop": "role-failed"})
        roles = ROLES_CSS.read_text(encoding="utf-8")
        for cls in ("stage-bench", "stage-review", "stage-upload", "stage-edited"):
            # the swatch leaves a legend's chips alone: the atlas legend draws its own bars
            self.assertIn(".chip." + cls + ":not(.role-legend .chip) {", roles, cls)
        self.assertIn("--stage-review: #A56BFF;", roles)
        self.assertIn(".chip.check-passed {", PANEL_CSS.read_text(encoding="utf-8"))

    def test_a_server_error_with_no_sentence_reads_as_spec_section_8(self):
        out = run_node("""return [
          h.refusalText({status: 502, body: {}}, "VICARIUS could not record the decision: "),
          h.refusalText({status: 500, body: {error: "The registry could not be read."}}, "x: "),
          h.refusalText({status: 409, body: {message: "MRS_T2 is starting over."}}, "x: "),
          h.refusalText({status: 404, body: {}}, "VICARIUS could not open Metashape: "),
          h.refusalText({status: 0, body: {}}, "x: "), h.refusalText({status: 0, timedOut: true}, "x: ")];""")
        self.assertEqual(out[0], "The desktop answered 502; nothing on this pane changed.")
        self.assertEqual(out[1:4], ["The registry could not be read.", "MRS_T2 is starting over.",
                                    "VICARIUS could not open Metashape: the desktop answered 404."])
        self.assertEqual(out[4], "The request did not reach the desktop; nothing was changed.")
        self.assertTrue(out[5].startswith("No answer from the desktop after 30 s"))

    def test_state_times_from_program_a_intervals(self):
        out = run_node("""
          var ivs = {"MRS_T2_2023ann": [
              {value: "editing", seconds: 3600, open: false}, {value: "review", seconds: 600, open: false},
              {value: "editing", seconds: 1800, open: false}, {value: "review", seconds: 7200, open: true}],
            "MRS_T2_2024ann": [{value: "editing", seconds: 99, open: false}]};
          return [h.stateTimes(ivs, ["MRS_T2_2023ann", "MRS_T2_2024ann"]),
                  h.stateTimes(ivs, ["MRS_T9_2023ann", "MRS_T2_2024ann"]),
                  h.stateTimes({"A": [{value: "upload", seconds: 5, open: true}]}, ["A"]),
                  h.stateTimes(null, ["A"]), h.stateTimes({"A": "bad"}, ["A"]),
                  h.stateTimes({"A": [null, {value: "editing", seconds: "x"}]}, ["A"]),
                  h.stateTimes({"A": [], "B": [{value: "editing", seconds: 7200}]}, ["A", "B"]),
                  h.stateTimes({"A": [null], "B": [{value: "review", seconds: 60, open: true}]}, ["A", "B"]),
                  h.stateTimes({"A": [], "B": []}, ["A", "B"])];""")
        self.assertEqual(out[0], {"editing_s": 5400, "review_open_s": 7200})
        self.assertEqual(out[1], {"editing_s": 99, "review_open_s": None})
        self.assertEqual(out[2], {"editing_s": None, "review_open_s": None}, "no editing interval reads unknown")
        self.assertEqual(out[3], {"editing_s": None, "review_open_s": None})
        self.assertEqual(out[4], {"editing_s": None, "review_open_s": None})
        self.assertEqual(out[5], {"editing_s": 0, "review_open_s": None})
        self.assertEqual(out[6], {"editing_s": 7200, "review_open_s": None}, "an empty first list never hides the next")
        self.assertEqual(out[7], {"editing_s": None, "review_open_s": 60})
        self.assertEqual(out[8], {"editing_s": None, "review_open_s": None})

    def test_notes_are_grouped_by_round_with_each_earlier_outcome(self):
        out = run_node("""return [h.roundGroups([
            {note_id: "1", round: 0, kind: "editor_explanation", text: "a"},
            {note_id: "2", round: 0, kind: "admin_review", decision: "return", text: "b"},
            {note_id: "3", round: 1, kind: "check_note", text: "c"},
            {note_id: "4", round: 1, kind: "admin_review", decision: "restart", text: "d"},
            {note_id: "5", round: 2, kind: "editor_explanation", text: "e"},
            null, 7, {note_id: "6", round: "x", kind: "rescale", text: "f"}], 2),
          h.roundGroups("bad", 0)];""")
        grouped, empty = out
        self.assertEqual([n["note_id"] for n in grouped["current"]], ["5"])
        self.assertEqual([(g["round"], g["outcome"], [n["note_id"] for n in g["notes"]]) for g in grouped["earlier"]],
                         [(1, "Start over", ["3", "4"]), (0, "Sent back to the editor", ["1", "2", "6"])])
        self.assertEqual(empty, {"current": [], "earlier": []})

    def test_the_queue_count_line(self):
        out = run_node("""return [
          h.queueMeta({items: [{}, {}], stopped_restarts: [{}], marked_for_upload: [{}], unmatched: ["x"]}),
          h.queueMeta({items: [{}], stopped_restarts: [], marked_for_upload: [], unmatched: []}),
          h.queueMeta({}), h.queueMeta(null)];""")
        self.assertEqual(out, ["2 transects in admin review, 1 start over stopped, 1 marked for upload.",
                               "1 transect in admin review, 0 start overs stopped, 0 marked for upload.",
                               "0 transects in admin review, 0 start overs stopped, 0 marked for upload.",
                               "0 transects in admin review, 0 start overs stopped, 0 marked for upload."])

    def test_transect_words(self):
        out = run_node("""return [h.transectWords({site: "LBH", site_name: "Lang Bank Red Hind FSA", transect: "T3"}),
                                  h.transectWords({site: "MRS", site_name: "", transect: "T2"})];""")
        self.assertEqual(out, ["Lang Bank Red Hind FSA (LBH) T3", "MRS T2"])


class SourceGuards(unittest.TestCase):
    def source(self) -> str:
        return REVIEW_JS.read_text(encoding="utf-8")

    def test_the_routes_it_calls(self):
        js = self.source()
        self.assertIn('"/manual-edit/api/jobs/"', js)
        for leaf in ('"/dossier"', '"/review"', '"/open"', '"/back-to-review"'):
            self.assertIn(leaf, js, leaf)
        self.assertIn('"/api/timelines/detail?job="', js)
        self.assertIn('"/api/review/queue"', js)
        self.assertIn('"/fragment?tool=review&job="', js)
        self.assertIn("mountReview: mountReview", js)
        self.assertIn("o.onDecision(", js)
        self.assertIn("o.onClose()", js)
        self.assertNotIn("eval(", js)
        self.assertNotIn("new Function", js)

    def test_no_markup_from_server_text_without_esc(self):
        js = self.source()
        for match in re.finditer(r"\.innerHTML\s*=\s*([^;]+);", js):
            rhs = match.group(1)
            self.assertNotRegex(rhs, r"\b(note|n|d|item|it)\.(text|error|line|report)\b(?![^()]*\))",
                                "innerHTML of server text without esc(): " + rhs[:120])

    def test_prose_rules_on_lines_and_tips(self):
        js = self.source()
        self.assertFalse(BANNED_WORD.search(js))
        self.assertFalse(any(d in js for d in DASHES))
        tips = js[js.index("var TIPS = {"):js.index("};", js.index("var TIPS = {"))]
        texts = ["".join(parts) for parts in re.findall(r'^\s+[a-zA-Z]+: "([^"]+)"(?: \+\n\s+"([^"]+)")?', tips, re.M)]
        self.assertGreaterEqual(len(texts), 12)
        for text in texts:
            with self.subTest(tip=text[:40]):
                self.assertFalse(text.startswith("You"), text)
                self.assertTrue(text.endswith("."), text)
                self.assertLessEqual(text.count(". ") + 1, 2, text)
                for banned in ("above", "below", "under the list", "this tab", "this window", "this pane"):
                    self.assertNotIn(banned, text.lower())
        lines = js[js.index("var LINES = {"):js.index("};", js.index("var LINES = {"))]
        for text in re.findall(r'"([^"]{12,})"', lines):
            with self.subTest(line=text[:40]):
                self.assertTrue(text.endswith(".") or text.endswith("..."), text)
                self.assertFalse(text.startswith("You"), text)


if __name__ == "__main__":
    unittest.main()
