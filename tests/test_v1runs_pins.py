#!/usr/bin/env python3
"""
Module: tests/test_v1runs_pins.py
Purpose: Pins the Voyager 1 batch pane (static/vt_v1runs.js and
         templates/voyager_tools/panes_v1runs.html): the pure helpers under
         node (escaping of hostile text, the initials seed of the page
         address parameter then the remembered value and never a default,
         the move targets, the flush preview sentences, the action buttons a
         row offers), and static guards (an IIFE, no server string reaching
         innerHTML without esc, every data-vt hook the script reads exists
         in the template, a tooltip on every field in the sentence shape the
         platform requires, the pane registered in the tab list and the
         script in the load list).
Inputs:  the clone's static and template files; node.
Outputs: unittest results.

Run from the clone root:
    /usr/bin/python3 -m unittest tests.test_v1runs_pins -v
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

CLONE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CLONE))
SCRIPT = CLONE / "static" / "vt_v1runs.js"
TEMPLATE = CLONE / "templates" / "voyager_tools" / "panes_v1runs.html"
PANEL = CLONE / "templates" / "voyager_tools" / "panel.html"
NODE = shutil.which("node")
HOSTILE = '<img src=x onerror="alert(1)">\'"&<script>'


def run_node(body: str):
    """Run a snippet under node with the helpers as `h`; the JSON it returns."""
    script = ("const h = require(" + json.dumps(str(SCRIPT)) + ");\n"
              "const out = (function () {\n" + body + "\n})();\nprocess.stdout.write(JSON.stringify(out));\n")
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    if done.returncode != 0:
        raise AssertionError("node failed: " + done.stderr[-1500:])
    return json.loads(done.stdout)


def row(**extra) -> dict:
    """A pane row as the model builds it."""
    base = {"id": 7, "transect": "HBE_T1", "site_code": "HBE", "site_name": "Hind Bank East FSA",
            "video_ids": ["HBE_T1_2023ann"], "status": "pending", "role": "role-planned", "current_video": None,
            "current_hours": None, "limit_hours": 11, "over_limit": False, "build_gone": False, "flushed": None,
            "actions": {"flush": False, "move": True}, "pending_index": 1, "pending_count": 3}
    base.update(extra)
    return base


@unittest.skipUnless(NODE, "node is not installed on this box")
class Helpers(unittest.TestCase):
    def test_node_check(self):
        done = subprocess.run([NODE, "--check", str(SCRIPT)], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_hostile_text_is_escaped_in_every_drawn_row(self):
        flushed = {"by": HOSTILE, "at": HOSTILE, "reason": HOSTILE, "requeued_as": 9}
        html_out = run_node("return h.rowHtml(" + json.dumps(row(
            transect=HOSTILE, site_name=HOSTILE, video_ids=[HOSTILE], status=HOSTILE, role=HOSTILE,
            current_video=HOSTILE, current_hours=12, flushed=flushed, over_limit=True, id=HOSTILE,
            actions={"flush": True, "move": True})) + ");")
        self.assertNotIn("<img", html_out)
        self.assertNotIn("<script", html_out)
        self.assertNotRegex(html_out, r'"[^"]*onerror="')
        self.assertIn("&lt;img src=x", html_out)
        batch = run_node("return h.batchHtml(" + json.dumps({"batch_id": HOSTILE, "status": HOSTILE, "role": HOSTILE,
                                                             "shelf_root": HOSTILE}) + ");")
        self.assertNotIn("<img", batch)
        self.assertNotIn("<script", batch)

    def test_esc_covers_the_five_characters_and_non_strings(self):
        self.assertEqual(run_node("return h.esc('<>&\"\\'');"), "&lt;&gt;&amp;&quot;&#39;")
        self.assertEqual(run_node("return [h.esc(null), h.esc(undefined), h.esc(5)];"), ["", "", "5"])

    def test_initials_seed_prefers_the_parameter_then_the_remembered_value_and_never_a_default(self):
        cases = [
            ("?initials=ab", "", "AB"),
            ("?x=1&initials=ab", "cd", "AB"),
            ("?initials=ab#top", "", "AB"),
            ("?initials=bad1", "cd", "CD"),
            ("?initials=a", "cd", "CD"),
            ("?initials=abcde", "", ""),
            ("?initials=%3Cb%3E", "", ""),
            ("?initials=%E0%A4%A", "", ""),
            ("", "cd", "CD"),
            ("", " cd ", "CD"),
            ("", "c", ""),
            ("", "", ""),
            (None, None, ""),
            ("?initials=", "", ""),
        ]
        for search, kept, expected in cases:
            with self.subTest(search=search, kept=kept):
                self.assertEqual(run_node(f"return h.initialsSeed({json.dumps(search)}, {json.dumps(kept)});"), expected)

    def test_move_targets(self):
        cases = [
            (row(pending_index=0, pending_count=3), "up", None), (row(pending_index=0, pending_count=3), "down", 1),
            (row(pending_index=0, pending_count=3), "end", "end"), (row(pending_index=1, pending_count=3), "up", 0),
            (row(pending_index=1, pending_count=3), "down", 2), (row(pending_index=2, pending_count=3), "down", None),
            (row(pending_index=2, pending_count=3), "end", None), (row(pending_index=0, pending_count=1), "end", None),
            (row(status="running"), "up", None), (row(pending_index=None), "up", None), (row(), "sideways", None),
        ]
        for r, direction, expected in cases:
            with self.subTest(index=r["pending_index"], direction=direction):
                self.assertEqual(run_node(f"return h.moveTarget({json.dumps(r)}, {json.dumps(direction)});"), expected)
        self.assertIsNone(run_node("return h.moveTarget(null, 'up');"))

    def test_move_buttons_follow_the_position(self):
        first = run_node("return h.rowHtml(" + json.dumps(row(pending_index=0)) + ");")
        self.assertNotIn('data-vt-v1-dir="up"', first)
        self.assertIn('data-vt-v1-dir="down"', first)
        middle = run_node("return h.rowHtml(" + json.dumps(row(pending_index=1)) + ");")
        self.assertEqual([d for d in ("up", "down", "end") if f'data-vt-v1-dir="{d}"' in middle], ["up", "down", "end"])
        last = run_node("return h.rowHtml(" + json.dumps(row(pending_index=2)) + ");")
        self.assertEqual([d for d in ("up", "down", "end") if f'data-vt-v1-dir="{d}"' in last], ["up"])
        self.assertNotIn("data-vt-v1-flush", middle)

    def test_flush_button_only_where_the_model_allows_it(self):
        running = run_node("return h.rowHtml(" + json.dumps(row(status="running", actions={"flush": True, "move": False})) + ");")
        self.assertIn('data-vt-v1-flush="7"', running)
        self.assertNotIn("data-vt-v1-move", running)
        done = run_node("return h.rowHtml(" + json.dumps(row(status="done", actions={"flush": False, "move": False})) + ");")
        self.assertNotIn("<button", done)

    def test_flags_and_hours_show_on_the_row(self):
        out = run_node("return h.rowHtml(" + json.dumps(row(status="running", over_limit=True, build_gone=True,
                                                            current_video="BIT_T3_2023ann", current_hours=16.6,
                                                            actions={"flush": True, "move": False})) + ");")
        self.assertIn("over the limit", out)
        self.assertIn("build process gone", out)
        self.assertIn("16.6 h of 11 h", out)
        self.assertIn("is-over", out)
        plain = run_node("return h.rowHtml(" + json.dumps(row(status="running", current_video="V", current_hours=3,
                                                              actions={"flush": True, "move": False})) + ");")
        self.assertNotIn("over the limit", plain)
        self.assertNotIn("is-over", plain)

    def test_flushed_row_names_who_when_and_why(self):
        out = run_node("return h.rowHtml(" + json.dumps(row(status="flushed", actions={"flush": False, "move": False},
              flushed={"by": "LO", "at": "2026-10-21 19:02 AST", "reason": "hung", "requeued_as": 10})) + ");")
        self.assertIn("Flushed by LO, 2026-10-21 19:02 AST. hung. Put back as turn 10.", out)

    def test_a_reason_without_a_full_stop_gets_one_before_the_put_back_sentence(self):
        flushed = lambda reason: {"by": "LO", "at": "t", "reason": reason, "requeued_as": 10}
        for reason, expected in (("hung", "hung. Put back"), ("hung.", "hung. Put back"), ("hung?", "hung? Put back"),
                                 ("  hung  ", "hung. Put back")):
            with self.subTest(reason=reason):
                line = run_node("return h.flushedLine(" + json.dumps(flushed(reason)) + ");")
                self.assertIn(expected, line)
                self.assertNotIn("..", line)
        self.assertEqual(run_node("return h.flushedLine(null);"), "")

    def test_the_flushed_note_is_its_own_row_under_the_turn(self):
        out = run_node("return h.rowHtml(" + json.dumps(row(status="flushed", actions={"flush": False, "move": False},
              flushed={"by": "LO", "at": "2026-10-21 19:02 AST", "reason": "hung"})) + ");")
        self.assertEqual(out.count("<tr"), 2)
        self.assertIn('class="vt-v1-note-row"', out)
        self.assertIn('colspan="4"', out)
        plain = run_node("return h.rowHtml(" + json.dumps(row()) + ");")
        self.assertEqual(plain.count("<tr"), 1)

    def test_the_shelf_sentence_lists_the_turns(self):
        cases = [
            ({"shelf_root": "/volume5/x", "applies_to_turns": [7, 8, 9]}, "The Shelf is now /volume5/x for turns 7, 8 and 9."),
            ({"shelf_root": "/volume5/x", "applies_to_turns": [7, 8]}, "The Shelf is now /volume5/x for turns 7 and 8."),
            ({"shelf_root": "/volume5/x", "applies_to_turns": [7]}, "The Shelf is now /volume5/x for turn 7."),
            ({"shelf_root": "/volume5/x", "applies_to_turns": []}, "The Shelf is now /volume5/x for no turn yet; new turns use it."),
            ({"shelf_root": "/volume5/x"}, "The Shelf is now /volume5/x for no turn yet; new turns use it."),
            ({"shelf_root": "/volume5/x", "applies_to_turns": 3}, "The Shelf is now /volume5/x for no turn yet; new turns use it."),
            (None, "The Shelf is now not reported for no turn yet; new turns use it."),
        ]
        for answer, expected in cases:
            with self.subTest(answer=answer):
                self.assertEqual(run_node("return h.shelfWords(" + json.dumps(answer) + ");"), expected)

    def test_a_flush_call_that_got_no_answer_reports_what_the_driver_shows(self):
        model = {"turns": [{"id": 6, "status": "flushed"}, {"id": 7, "status": "running"}]}
        said = lambda turn, m: run_node("return h.timeoutLine(" + json.dumps(turn) + ", " + json.dumps(m) + ");")
        self.assertIn("shows turn 6 as flushed.", said(6, model))
        self.assertIn("shows turn 7 as running; the flush may still be running", said(7, model))
        self.assertIn("no longer lists the turn", said(9, model))
        self.assertIn("no longer lists the turn", said(6, None))

    def test_the_limit_line_is_a_line_not_a_button(self):
        self.assertEqual(run_node("return h.limitLine({time_limit_hours: 11});"), "Videos over 11 hours are flushed automatically.")
        self.assertEqual(run_node("return h.limitLine({time_limit_hours: null});"), "")
        self.assertEqual(run_node("return h.limitLine(null);"), "")

    def test_preview_sentences_cover_what_will_happen(self):
        preview = {"can_flush": True, "folder": "/mnt/rip/driver_staging/b/turn006", "folder_bytes": 381758028614,
                   "completed_ids": ["BIT_T3_2023ann"], "live": {"job_alive": True, "pids": [11, 12]}, "frozen": True,
                   "rows": [{"id": "BIT_T3_2023ann", "video_location_now": "Workbench", "video_location_after": "Archive"},
                            {"id": "BIT_T3_2024_pbl", "video_location_now": "Workbench", "video_location_after": "Archive"}]}
        items = run_node("return h.previewItems(" + json.dumps(preview) + ");")
        text = " ".join(i["text"] for i in items)
        self.assertIn("2 videos go back to their record in the Archive", text)
        self.assertIn("The Workbench folder /mnt/rip/driver_staging/b/turn006 (381.8 GB) is deleted.", text)
        self.assertIn("Finished videos lost with this transect: BIT_T3_2023ann.", text)
        self.assertIn("The running build is ended (2 processes: 11, 12).", text)
        self.assertIn("frozen", text)
        self.assertEqual(items[0]["rows"][0], {"id": "BIT_T3_2023ann", "now": "Workbench", "after": "Archive"})

    def test_preview_of_a_quiet_turn_and_of_a_refusal(self):
        quiet = run_node("return h.previewItems({can_flush: true, rows: [], folder: null, completed_ids: [], live: {job_alive: false, pids: []}});")
        text = " ".join(i["text"] for i in quiet)
        self.assertIn("There is no Workbench folder to delete.", text)
        self.assertIn("No video of this transect had finished.", text)
        self.assertIn("No build process is running.", text)
        self.assertIn("0 videos", text)
        refused = run_node("return h.previewItems({can_flush: false, refusal: 'a deposit has started'});")
        self.assertEqual(refused[0], {"kind": "refusal", "text": "a deposit has started"})

    def test_a_refusal_with_no_rows_shows_only_the_refusal_sentence(self):
        for preview in ("{can_flush: false, allowed: false, refusal: 'Turn 99 is not in batch b.'}",
                        "{can_flush: false, refusal: 'Turn 99 is not in batch b.', rows: []}"):
            with self.subTest(preview=preview):
                items = run_node("return h.previewItems(" + preview + ");")
                self.assertEqual(items, [{"kind": "refusal", "text": "Turn 99 is not in batch b."}])
        refused_with_rows = run_node("return h.previewItems({can_flush: false, refusal: 'r', rows: [{id: 'A'}]});")
        self.assertGreater(len(refused_with_rows), 1)
        html_out = run_node("return h.previewHtml(h.previewItems({can_flush: false, refusal: 'r'}));")
        self.assertEqual(html_out.count("<p"), 1)

    def test_preview_survives_garbage(self):
        for garbage in ("null", "5", "'x'", "[]", "{rows: 'x', live: 3, completed_ids: {}}", "{rows: [null, 5]}"):
            with self.subTest(garbage=garbage):
                items = run_node("return h.previewItems(" + garbage + ");")
                self.assertTrue(items)
        html_out = run_node("return h.previewHtml(h.previewItems({can_flush: true, folder: " + json.dumps(HOSTILE) +
                            ", completed_ids: [" + json.dumps(HOSTILE) + "], rows: [{id: " + json.dumps(HOSTILE) + "}]}));")
        self.assertNotIn("<img", html_out)
        self.assertNotIn("<script", html_out)

    def test_flush_body_carries_confirm_and_the_two_choices(self):
        body = run_node("return h.flushBody({turn: 6, initials: 'LO', reason: 'r', requeue: true, resume: false});")
        self.assertEqual(body, {"turn_id": 6, "initials": "LO", "reason": "r", "requeue": True, "resume": False, "confirm": True})

    def test_hours_and_gigabytes_words(self):
        self.assertEqual(run_node("return [h.hoursWords(16.64), h.hoursWords(null), h.hoursWords('x'), h.hoursWords(NaN)];"),
                         ["16.6 h", "unknown", "unknown", "unknown"])
        self.assertEqual(run_node("return [h.gigabytes(381758028614), h.gigabytes(-1), h.gigabytes(null), h.gigabytes(0)];"),
                         ["381.8 GB", "an unknown size", "an unknown size", "0 GB"])


class StaticGuards(unittest.TestCase):
    def setUp(self):
        self.js = SCRIPT.read_text(encoding="utf-8")
        self.tpl = TEMPLATE.read_text(encoding="utf-8")

    def test_script_is_one_iife_and_registers_the_pane(self):
        self.assertTrue(self.js.lstrip().startswith("/*"))
        self.assertIn("(function () {", self.js)
        self.assertTrue(self.js.rstrip().endswith("})();"))
        self.assertIn('register("v1runs"', self.js)

    def test_no_dash_or_emoji_in_the_script_or_template(self):
        for text in (self.js, self.tpl):
            self.assertNotRegex(text, "[–—\U0001F000-\U0001FAFF☀-➿]")

    def test_server_strings_reach_innerhtml_only_through_helpers_that_escape(self):
        for match in re.finditer(r"innerHTML\s*=\s*([^;]+);", self.js):
            expression = match.group(1)
            self.assertRegex(expression, r"(Html|esc\(|^\s*\"|^\s*'|\"\")", expression)
        self.assertNotIn("document.write", self.js)
        self.assertNotIn("eval(", self.js)

    def test_every_data_vt_hook_the_script_reads_exists_in_the_template(self):
        hooks = set(re.findall(r'ctx\.q\("([a-z0-9-]+)"\)', self.js))
        self.assertGreaterEqual(len(hooks), 18)
        for hook in hooks:
            with self.subTest(hook=hook):
                self.assertIn(f'data-vt="{hook}"', self.tpl)

    def test_every_field_has_a_label_and_a_tooltip_glyph(self):
        fields = re.findall(r'<(?:input|textarea|select)\b[^>]*>', self.tpl)
        self.assertEqual(len(fields), 7)
        glyphs = re.findall(r'<button type="button" class="tip" aria-label="([^"]+)" title="([^"]+)"><span>\?</span></button>', self.tpl)
        self.assertEqual(len(glyphs), 7, "one ? glyph per field")
        for field in fields:
            field_id = re.search(r'id="([^"]+)"', field)
            if field_id:
                self.assertIn(f'for="{field_id.group(1)}"', self.tpl)

    def test_tooltips_follow_the_platform_sentence_rules(self):
        titles = [html.unescape(t) for t in re.findall(r'title="([^"]+)"', self.tpl)]
        self.assertGreaterEqual(len(titles), 11)
        for title in titles:
            with self.subTest(title=title):
                self.assertFalse(title.startswith("You"), "a tooltip never opens with You")
                self.assertTrue(title.endswith("."), "a tooltip is a complete sentence")
                sentences = [s for s in re.split(r"(?<=[.!?])\s+", title) if s]
                self.assertIn(len(sentences), (1, 2, 3))
                self.assertNotRegex(title, r"\b(above|below|this tab|this pane|this page)\b")
        # the generated button tooltips follow the same rules
        for text in re.findall(r'"((?:Open|Move) [^"]*?\.)"', self.js):
            self.assertFalse(text.startswith("You"))

    def test_no_prompt_seepage_or_filler_in_the_template(self):
        for word in ("as asked", "per the plan", "Lauren said", "agent", "reviewer", "TODO", "placeholder"):
            self.assertNotIn(word.lower(), self.tpl.lower() if word != "Lauren said" else self.tpl.lower().replace("lauren olinger", ""))

    def test_status_and_limit_are_not_buttons(self):
        batch_line = re.search(r'<[a-z]+[^>]*data-vt="v1-limit"[^>]*>', self.tpl).group(0)
        self.assertTrue(batch_line.startswith("<p"))
        self.assertNotIn("<button", self.js[self.js.index("function batchHtml"):self.js.index("function limitLine")])

    def test_the_pane_is_in_the_tab_list_and_the_script_in_the_load_list(self):
        panel = PANEL.read_text(encoding="utf-8")
        self.assertIn('data-vt-tab="v1runs"', panel)
        self.assertIn('{% include "voyager_tools/panes_v1runs.html" %}', panel)
        from voyagertools import views
        self.assertIn(views.STATIC_V1RUNS_JS, views.PANEL_SCRIPTS)
        self.assertTrue((CLONE / "static" / views.STATIC_V1RUNS_JS).is_file())

    def test_initials_are_seeded_from_the_address_parameter_and_upper_cased_as_typed(self):
        self.assertIn("initialsSeed(window.location.search", self.js)
        self.assertIn("box.value = box.value.toUpperCase()", self.js)
        self.assertNotRegex(self.js, r"value\s*=\s*['\"]LO['\"]")

    def test_warning_lines_of_the_preview_have_the_warning_colour(self):
        css = (CLONE / "static" / "voyager_tools.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.voyager-tools \.vt-plan p\.count\.is-warn\s*\{[^}]*color:\s*var\(--warning")

    def test_readme_keeps_the_review_queue_heading_after_the_new_section(self):
        readme = (CLONE / "README.md").read_text(encoding="utf-8")
        self.assertIn("### Review queue, Uploads and Timelines", readme)
        self.assertLess(readme.index("### Voyager 1 batch"), readme.index("### Review queue, Uploads and Timelines"))
        self.assertLess(readme.index("### Review queue, Uploads and Timelines"), readme.index("- **Review queue**"))

    def test_section_tooltip_has_at_most_two_sentences_and_cancel_has_none(self):
        section = re.search(r'aria-label="About the Voyager 1 batch" title="([^"]+)"', self.tpl).group(1)
        self.assertLessEqual(len([s for s in re.split(r"(?<=[.!?])\s+", html.unescape(section)) if s]), 2)
        cancel = re.search(r'<button[^>]*data-vt="v1-flush-cancel"[^>]*>', self.tpl).group(0)
        self.assertNotIn("title=", cancel)

    def test_the_tabs_carry_the_new_names_and_no_old_label_remains(self):
        panel = PANEL.read_text(encoding="utf-8")
        self.assertRegex(panel, r'data-vt-tab="v1runs"[^>]*>Voyager 1 batch</button>')
        self.assertRegex(panel, r'data-vt-tab="runs"[^>]*>Voyager run settings</button>')
        self.assertRegex(panel, r'data-vt-tab="floors"[^>]*>Driver settings</button>')
        settings = (CLONE / "templates" / "voyager_tools" / "panes_settings.html").read_text(encoding="utf-8")
        self.assertIn('<h2 id="vt-runs-h">Voyager run settings</h2>', settings)
        self.assertIn('<h2 id="vt-floors-h">Driver settings</h2>', settings)
        self.assertIn("Voyager 1 batch</h2>", self.tpl)
        stale = [">Voyager runs<", ">Voyager 1 runs<", ">Space floors<", '"Voyager runs"', '"Space floors"', "### Voyager 1 runs"]
        scanned = [panel, settings, self.tpl, self.js, (CLONE / "static" / "vt_settings.js").read_text(encoding="utf-8"),
                   (CLONE / "README.md").read_text(encoding="utf-8"), (CLONE / "CONTEXT.md").read_text(encoding="utf-8")]
        for text in scanned:
            for label in stale:
                self.assertNotIn(label, text)
        self.assertNotIn("**Voyager runs**", scanned[-2])
        self.assertNotIn("**Space floors**", scanned[-2])

    def test_flush_has_its_own_long_timeout_and_a_shut_form_while_it_runs(self):
        self.assertRegex(self.js, r"FLUSH_TIMEOUT_MS = 900000")
        self.assertRegex(self.js, r'"/api/v1runs/flush", \{ method: "POST", body: body, timeoutMs: FLUSH_TIMEOUT_MS \}')
        self.assertIn("Flushing turn \" + turn.id + \": ending the build, putting the videos back on their Archive record and deleting the Workbench copy.", self.js)
        self.assertIn("function setFlushing(on)", self.js)
        self.assertIn("if (res.status === 0) { setFlushing(false); readAfterTimeout(turn); return; }", self.js)
        driver = (CLONE / "static" / "voyager_tools.js").read_text(encoding="utf-8")
        self.assertIn("opts.timeoutMs", driver)
        self.assertIn("}, limitMs);", driver)
        self.assertNotIn("}, REQUEST_TIMEOUT_MS);", driver)

    def test_a_dialog_in_flight_disables_its_button_and_the_unused_busy_flag_is_gone(self):
        self.assertNotIn("busy: false", self.js)
        self.assertNotIn("st.busy", self.js)
        self.assertEqual(self.js.count("st.confirming = true;"), 2)
        self.assertIn("el.go.disabled = true;\n      ctx.confirmDialog", self.js)
        self.assertIn("el.shelfGo.disabled = true;\n      ctx.confirmDialog", self.js)

    def test_flush_stays_the_primary_action_and_the_shelf_button_is_secondary(self):
        self.assertRegex(self.tpl, r'class="btn" data-vt="v1-shelf-go"')
        self.assertNotIn("btn-primary", self.tpl)
        self.assertRegex(self.tpl, r'class="btn btn-danger" data-vt="v1-flush-go"')

    def test_with_no_batch_or_no_driver_the_table_and_shelf_form_are_hidden(self):
        self.assertRegex(self.tpl, r'data-vt="v1-table-box"[^>]*hidden')
        self.assertRegex(self.tpl, r'data-vt="v1-shelf-box"[^>]*hidden')
        self.assertIn("el.tableBox.hidden = !st.turns.length;", self.js)
        self.assertIn("el.shelfBox.hidden = !model.batch;", self.js)
        failure = self.js[self.js.index("if (!res.ok) {\n          st.turns = [];"):self.js.index("render(res.data);")]
        self.assertIn("el.tableBox.hidden = true;", failure)
        self.assertIn("el.shelfBox.hidden = true;", failure)
        css = (CLONE / "static" / "voyager_tools.css").read_text(encoding="utf-8")
        self.assertIn(".voyager-tools .vt-v1-tablebox[hidden]", css)

    def test_the_move_buttons_never_wrap(self):
        css = (CLONE / "static" / "voyager_tools.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"td\.vt-v1-actions \{ white-space: nowrap; \}")

    def test_requeue_and_resume_are_ticked_by_default(self):
        self.assertRegex(self.tpl, r'data-vt="v1-requeue" checked')
        self.assertRegex(self.tpl, r'data-vt="v1-resume" checked')
        self.assertIn("Put it back at the end of the queue", self.tpl)
        self.assertIn("Carry on with the next transect", self.tpl)


if __name__ == "__main__":
    unittest.main()
