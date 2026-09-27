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

    def test_the_start_over_confirmation_names_no_placeholder(self):
        out = run_node("""return [h.restartConfirmText({processing_folder: "MRS_T2_3D",
                                                        on_bench: {path: "/bench/MRS_T2_3D"}}, false),
                                  h.restartConfirmText({processing_folder: "MRS_T2_3D", restart: {stage: "aside", aside: ""}}, true),
                                  h.restartConfirmText({processing_folder: "MRS_T2_3D", restart: "bad"}, true)];""")
        self.assertEqual(out[0], "VICARIUS moves /bench/MRS_T2_3D aside as MRS_T2_3D_restart_ followed by the date "
                                 "and time of the move, and pulls a fresh copy from the Shelf.")
        self.assertEqual(out[1], "VICARIUS resumes the start over of MRS_T2_3D from the move aside.")
        self.assertEqual(out[2], "VICARIUS resumes the start over of MRS_T2_3D from the stage it reached.")

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
                  h.stateTimes({"A": [null, {value: "editing", seconds: "x"}]}, ["A"])];""")
        self.assertEqual(out[0], {"editing_s": 5400, "review_open_s": 7200})
        self.assertEqual(out[1], {"editing_s": 99, "review_open_s": None})
        self.assertEqual(out[2], {"editing_s": 0, "review_open_s": None})
        self.assertEqual(out[3], {"editing_s": None, "review_open_s": None})
        self.assertEqual(out[4], {"editing_s": None, "review_open_s": None})
        self.assertEqual(out[5], {"editing_s": 0, "review_open_s": None})

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
                for banned in ("above", "this tab", "this window", "this pane"):
                    self.assertNotIn(banned, text.lower())
        lines = js[js.index("var LINES = {"):js.index("};", js.index("var LINES = {"))]
        for text in re.findall(r'"([^"]{12,})"', lines):
            with self.subTest(line=text[:40]):
                self.assertTrue(text.endswith(".") or text.endswith("..."), text)
                self.assertFalse(text.startswith("You"), text)


if __name__ == "__main__":
    unittest.main()
