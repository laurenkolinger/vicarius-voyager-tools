#!/usr/bin/env python3
"""
Module: tests/test_frontend_pins.py
Purpose: Pins the pure functions of static/vt_settings.js (the settings and
         desktop panes of the Atlas control panel, program C milestone C3)
         under node: the field renderer escapes every label, tooltip and
         value; the parser refuses what is not a number, a whole number or
         a path; changedKeys compares numbers by value; a reason of spaces
         is no reason; the initials rule is two to four letters; the history
         CSV quotes every cell and never opens a formula; the words shown for
         a history value. Also static source guards on both panel scripts:
         the pane seam, the stricter initials, no innerHTML of a server
         string without esc(), and no "admin gated" wording.
Inputs:  the clone's static/vt_settings.js and static/voyager_tools.js; node.
Outputs: unittest results; nothing is written.

Run from the clone root:
    python3 -m unittest tests.test_frontend_pins -v
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

CLONE = Path(__file__).resolve().parent.parent
SETTINGS_JS = CLONE / "static" / "vt_settings.js"
PANEL_JS = CLONE / "static" / "voyager_tools.js"
NODE = shutil.which("node")


def run_node(expression_body: str) -> object:
    """Run a snippet under node with the helpers loaded as `h`; return the JSON it prints."""
    script = ("const h = require(" + json.dumps(str(SETTINGS_JS)) + ");\n"
              "const out = (function () {\n" + expression_body + "\n})();\n"
              "process.stdout.write(JSON.stringify(out));\n")
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    if done.returncode != 0:
        raise AssertionError("node failed: " + done.stderr[-1500:])
    return json.loads(done.stdout)


FIELDS = [
    {"key": "scale_error_max_mm", "label": "Scale error limit (mm)", "type": "number", "min": 0, "max": 1000,
     "min_exclusive": True, "step": 0.01, "unit": "mm", "tip": "Type the largest scale error.",
     "confirm": False, "reason_required": False},
    {"key": "markers_per_chunk", "label": "Markers per chunk", "type": "integer", "min": 1, "max": 20,
     "tip": "Type how many markers.", "confirm": False, "reason_required": False},
    {"key": "edit_bench_default", "label": "Edit bench default", "type": "path", "tip": "Type the folder.",
     "confirm": False, "reason_required": False},
    {"key": "open_close_logging", "label": "Open and close logging", "type": "boolean", "tip": "Tick this.",
     "confirm": False, "reason_required": False},
    {"key": "archive_shelf_copy", "label": "Shelf copy", "type": "choice", "tip": "The Shelf.", "confirm": True,
     "reason_required": True,
     "options": [{"value": True, "label": "Archive the Shelf copy", "disabled": False, "tip": "Pick this to keep."},
                 {"value": False, "label": "Overwrite the Shelf copy", "disabled": True, "tip": "Pick this to replace.",
                  "disabled_reason": "Overwrite cannot be chosen yet."}]},
]


@unittest.skipUnless(NODE, "node is not installed on this box")
class SettingsHelpers(unittest.TestCase):
    def test_node_check_passes_on_both_scripts(self):
        for path in (SETTINGS_JS, PANEL_JS):
            with self.subTest(path=path.name):
                done = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=30)
                self.assertEqual(done.returncode, 0, done.stderr)

    def test_fields_html_renders_one_row_per_field_with_its_glyph(self):
        html = run_node("return h.fieldsHtml('settings', " + json.dumps(FIELDS) + ", "
                        "{scale_error_max_mm: 4.94, markers_per_chunk: 4, edit_bench_default: '/mnt/rip/editbench',"
                        " open_close_logging: true, archive_shelf_copy: true});")
        self.assertEqual(html.count("<div class=\"vt-setting\""), len(FIELDS))
        options = sum(len(f.get("options", [])) for f in FIELDS)
        self.assertEqual(html.count('class="tip"'), len(FIELDS) + options, "one glyph per field and per option")
        self.assertEqual(html.count('<dl class="choices">'), 1)
        self.assertNotIn("vt-field-group", html)
        self.assertIn('value="4.94"', html)
        self.assertIn('value="4"', html)
        self.assertIn('value="/mnt/rip/editbench"', html)
        self.assertRegex(html, r'type="checkbox"[^>]*data-vt-key="open_close_logging"[^>]*checked')
        self.assertEqual(html.count('type="radio"'), 2)
        self.assertRegex(html, r'type="radio"[^>]*value="false"[^>]*disabled')
        self.assertIn("Overwrite cannot be chosen yet.", html)
        self.assertIn("mm</span>", html)

    def test_fields_html_groups_rows_under_their_sub_heading(self):
        grouped = [dict(FIELDS[3], key="a", label="Run Voyager 1 batches", group="Voyager 1"),
                   dict(FIELDS[3], key="b", label="Delete from the Workbench", group="Voyager 1"),
                   dict(FIELDS[3], key="c", label="Run Voyager 2 batches", group="Voyager 2")]
        html = run_node("return h.fieldsHtml('runs', " + json.dumps(grouped) + ", {a: true, b: false, c: true});")
        self.assertEqual(html.count('<h3 class="vt-field-group">'), 2)
        self.assertEqual(html.count('<dl class="choices">'), 2)
        self.assertLess(html.index("Voyager 1</h3>"), html.index('data-vt-key="b"'))
        self.assertLess(html.index('data-vt-key="b"'), html.index("Voyager 2</h3>"))

    def test_a_checkbox_reads_on_or_off_with_its_state(self):
        html = run_node("return h.fieldsHtml('x', " + json.dumps([FIELDS[3]]) + ", {open_close_logging: false});")
        self.assertIn(">Off</span>", html)
        self.assertNotIn(">On</span>", html)

    def test_a_disabled_option_says_why_on_a_focusable_glyph(self):
        html = run_node("return h.fieldsHtml('x', " + json.dumps([FIELDS[4]]) + ", {archive_shelf_copy: true});")
        self.assertRegex(html, r'<button type="button" class="tip"[^>]*title="Overwrite cannot be chosen yet\."')
        self.assertNotRegex(html, r'<label[^>]*title=')

    def test_short_process_names_the_executable_and_the_script(self):
        out = run_node("return [h.shortProcess('/mnt/x/env/bin/python -u /home/b/.config/Ultralytics/train.py --epochs 5'),"
                       " h.shortProcess('/usr/bin/ollama serve'), h.shortProcess('metashape'), h.shortProcess(''),"
                       " h.shortProcess(null)];")
        self.assertEqual(out, ["python train.py", "ollama serve", "metashape", "unknown", "unknown"])

    def test_value_class_keeps_short_values_whole(self):
        out = run_node("return [h.valueClass('false'), h.valueClass('4.94'), h.valueClass('\"/mnt/rip/driver_staging/editbench\"')];")
        self.assertEqual(out, ["is-short", "is-short", "is-long"])

    def test_fields_html_escapes_hostile_labels_tips_and_values(self):
        hostile = [{"key": "k\"><img src=x onerror=alert(1)>", "label": "<script>alert(1)</script>",
                    "type": "path", "tip": "\" onmouseover=\"alert(2)", "confirm": False, "reason_required": False}]
        html = run_node("return h.fieldsHtml('p', " + json.dumps(hostile) + ", {'k\"><img src=x onerror=alert(1)>': "
                        "'\"><b>v</b>'});")
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("<b>", html)
        self.assertNotIn('" onmouseover="', html)
        self.assertIn("&lt;script&gt;", html)

    def test_parse_value_accepts_numbers_and_refuses_the_rest(self):
        out = run_node("var f = " + json.dumps(FIELDS) + ";"
                       "return [h.parseValue(f[0], '4.94'), h.parseValue(f[0], ' 5 '), h.parseValue(f[0], ''),"
                       " h.parseValue(f[0], 'abc'), h.parseValue(f[0], '1e999'), h.parseValue(f[0], '4.9.4'),"
                       " h.parseValue(f[1], '20'), h.parseValue(f[1], '20.5'), h.parseValue(f[1], '-3'),"
                       " h.parseValue(f[2], '  /mnt/rip/x  '), h.parseValue(f[2], ''), h.parseValue(f[3], true),"
                       " h.parseValue(f[4], 'false')];")
        self.assertEqual(out[0], {"ok": True, "value": 4.94})
        self.assertEqual(out[1], {"ok": True, "value": 5})
        for index in (2, 3, 4, 5):
            with self.subTest(index=index):
                self.assertFalse(out[index]["ok"])
                self.assertEqual(out[index]["line"], "Scale error limit (mm) must be a number.")
        self.assertEqual(out[6], {"ok": True, "value": 20})
        self.assertEqual(out[7], {"ok": False, "line": "Markers per chunk must be a whole number."})
        self.assertEqual(out[8], {"ok": True, "value": -3})
        self.assertEqual(out[9], {"ok": True, "value": "/mnt/rip/x"})
        self.assertEqual(out[10], {"ok": False, "line": "Edit bench default must be a path."})
        self.assertEqual(out[11], {"ok": True, "value": True})
        self.assertEqual(out[12], {"ok": True, "value": False})

    def test_changed_keys_compares_by_value(self):
        out = run_node("var f = " + json.dumps(FIELDS) + ";"
                       "var before = {scale_error_max_mm: 5.0, markers_per_chunk: 4, edit_bench_default: '/a',"
                       " open_close_logging: true, archive_shelf_copy: true};"
                       "return [h.changedKeys(f, before, before),"
                       " h.changedKeys(f, before, Object.assign({}, before, {scale_error_max_mm: 5})),"
                       " h.changedKeys(f, before, Object.assign({}, before, {scale_error_max_mm: 5.01, open_close_logging: false})),"
                       " h.changedKeys(f, before, Object.assign({}, before, {archive_shelf_copy: false}))];")
        self.assertEqual(out, [[], [], ["scale_error_max_mm", "open_close_logging"], ["archive_shelf_copy"]])

    def test_reason_ready(self):
        out = run_node("return [h.reasonReady('', false), h.reasonReady('   ', false), h.reasonReady('   ', true),"
                       " h.reasonReady('\\t\\n', true), h.reasonReady(' x ', true), h.reasonReady(null, true)];")
        self.assertEqual(out, [True, True, False, False, True, False])

    def test_initials_rule_is_two_to_four_letters(self):
        cases = ["LO", "lo ", "ABCD", "", "L", "L0", "ABCDE", "L O", "<b>x</b>", "LO\n"]
        out = run_node("return " + json.dumps(cases) + ".map(h.cleanInitials);")
        self.assertEqual(out, ["LO", "LO", "ABCD", None, None, None, None, None, None, "LO"])

    def test_history_csv_quotes_every_cell_and_guards_formulas(self):
        rows = [{"at": "2026-09-27T10:12:03-04:00", "by": "LO", "store": "manual_edit", "key": "scale_error_max_mm",
                 "old": "4.94", "new": "5", "reason": 'a, "quoted"\nline'},
                {"at": "2026-09-27T10:13:00-04:00", "by": "LO", "store": "driver_nas", "key": "defaults.headroom_factor",
                 "old": "1.1", "new": "1.2", "reason": '=HYPERLINK("http://x")'}]
        csv_text = run_node("return h.historyCsv(" + json.dumps(rows) + ");")
        lines = csv_text.split("\r\n")
        self.assertEqual(lines[0], '"at","by","store","key","old","new","reason"')
        self.assertIn('"a, ""quoted""\nline"', csv_text)
        self.assertIn("\"'=HYPERLINK(\"\"http://x\"\")\"", csv_text)

    def test_value_words(self):
        out = run_node("return [h.valueWords('4.94'), h.valueWords('true'), h.valueWords('false'),"
                       " h.valueWords('\"/mnt/rip/editbench\"'), h.valueWords('null'), h.valueWords(null),"
                       " h.valueWords('{\"__digest__\": \"sha256:ab\", \"chars\": 5000}'), h.valueWords('not json')];")
        self.assertEqual(out, ["4.94", "true", "false", "/mnt/rip/editbench", "none", "none",
                               "a value of 5000 characters (sha256:ab)", "not json"])

    def test_meta_line(self):
        out = run_node("return [h.metaLine('2026-09-26T21:54:25-04:00', 'LO'), h.metaLine(null, null)];")
        self.assertEqual(out, ["Saved 2026-09-26 21:54 AST by LO", "No save recorded in the settings history yet."])

    def test_drift_lines_name_the_label(self):
        out = run_node("return h.driftLines(" + json.dumps(FIELDS) + ", [{key: 'scale_error_max_mm', disk: '5.0',"
                       " history: '4.94'}, {key: 'unknown_key', disk: 'true', history: null}]);")
        self.assertEqual(out, ["Scale error limit (mm) is 5.0 on disk and 4.94 in the settings history; that change has no history line.",
                               "unknown_key is true on disk and has no line in the settings history."])


@unittest.skipUnless(NODE, "node is not installed on this box")
class SharedBlockInTheDesktopWindow(unittest.TestCase):
    """The desktop links neither the shared design block nor the state-role sheet, so the panel
    injects its own copies, the shared block scoped under .voyager-tools (the manual edit
    module's rule), and never twice."""

    def scope(self, css: str) -> str:
        script = ("global.window = {}; global.document = {readyState: 'complete', getElementById: function () { return null; },"
                  " addEventListener: function () {}};\n"
                  "require(" + json.dumps(str(PANEL_JS)) + ");\n"
                  "process.stdout.write(JSON.stringify(window.VoyagerTools.scopeCss(" + json.dumps(css) + ", '.voyager-tools')));")
        done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_selectors_are_scoped(self):
        out = self.scope(":root{--a:1}\nhtml,body{margin:0}\n.choices,.row>b{x:1}\n*{y:1}\n.bubble{z:1}\n"
                         "@media (max-width:900px){.btn{w:1}}\n@font-face{font-family:x}")
        self.assertIn(".voyager-tools{--a:1}", out)
        self.assertNotIn("html", out)
        self.assertNotIn("body", out)
        self.assertIn(".voyager-tools .choices,.voyager-tools .row>b{x:1}", out)
        self.assertIn(".voyager-tools *{y:1}", out)
        self.assertIn(".bubble{z:1}", out)
        self.assertIn("@media (max-width:900px){.voyager-tools .btn{w:1}}", out)
        self.assertIn("@font-face{font-family:x}", out)

    def test_the_mount_injects_both_sheets_once(self):
        source = PANEL_JS.read_text(encoding="utf-8")
        self.assertIn('var SHARED_CSS = "vicarius_workflow.css";', source)
        self.assertIn('var ROLES_CSS = "vicarius_roles.css";', source)
        self.assertIn('data-scoped-shared="voyager-tools"', source)
        self.assertIn("hasStylesheet(SHARED_CSS)", source)
        self.assertIn("hasStylesheet(ROLES_CSS)", source)


class SourceGuards(unittest.TestCase):
    def setUp(self):
        self.settings = SETTINGS_JS.read_text(encoding="utf-8")
        self.panel = PANEL_JS.read_text(encoding="utf-8")

    def test_the_pane_seam(self):
        self.assertIn("registerPane", self.panel)
        self.assertIn("mountPanel", self.panel)
        self.assertIn("Object.assign(window.VoyagerTools || {}", self.panel)
        self.assertIn("_pendingPanes", self.panel)
        self.assertIn("_pendingPanes", self.settings)
        for key in ("settings", "shelf", "runs", "floors", "gpu", "locks", "modules", "appearance", "history"):
            with self.subTest(key=key):
                self.assertRegex(self.settings, r'register\("' + key + '"')

    def test_the_initials_are_never_seeded_from_the_desktop_fallback(self):
        # VICBOOT.getInitials answers a fixed pair of letters when nobody typed
        # any; a settings history line signed that way would name the wrong person.
        self.assertNotIn("getInitials", self.panel)
        self.assertNotIn("getInitials", self.settings)

    def test_initials_rule_in_both_scripts(self):
        self.assertIn("/^[A-Z]{2,4}$/", self.panel)
        self.assertIn("/^[A-Z]{2,4}$/", self.settings)
        self.assertNotIn("one to eight", self.panel)

    def test_no_voyager_tools_or_admin_gated_copy(self):
        for name, text in (("voyager_tools.js", self.panel), ("vt_settings.js", self.settings)):
            with self.subTest(name=name):
                strings = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', text)
                self.assertFalse([s for s in strings if "Voyager tools" in s], name)
                self.assertNotIn("admin gated", text.lower())
                self.assertNotIn("admin-gated", text.lower())

    def test_every_innerhtml_of_a_server_value_goes_through_esc(self):
        # Every assignment builds its markup from esc() or from a helper whose
        # name ends in Html (each escapes every value it writes); a bare data
        # field never reaches innerHTML.
        for line in self.settings.splitlines():
            if ".innerHTML =" in line and "data." in line:
                self.assertTrue("esc(" in line or re.search(r"[a-z]Html\b", line), line.strip())


if __name__ == "__main__":
    unittest.main()
