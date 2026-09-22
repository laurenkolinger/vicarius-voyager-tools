#!/usr/bin/env python3
"""
Module: tests/test_order_reorder.py
Purpose: Proves the four reorder interactions TODO T20260906-213500-1 adds to
         the processing order table (static/voyager_tools.js, the row markup
         built by renderOrder(), and the "Move selected" toolbar added to
         templates/voyager_tools/panel.html): drag and drop, a typed
         position per row, multi-select with a move-to-position control, and
         arrow-key movement on a focusable drag handle. Static guards read
         the source text for the invariants that hold regardless of a
         browser (the one LOCK_MESSAGE, draggable never conditional, the
         Save order request body shape unchanged); the behaviour classes
         drive the real script in headless Chrome over a harness page built
         from the real rendered panel (a Flask test client with an admin
         session, the same pattern tests/test_views.py uses), with a
         stubbed fetch standing in for the four API routes the pane calls.
         The pattern follows vicarius_ui_os/tests/test_atlas_js_guards.py:
         a JSON verdict per step is written into <pre id="results"> and read
         back from --dump-dom, because jsdom is not installed on this box.
Inputs:  the clone's own static/templates files; no NAS, network or process.
Outputs: unittest results; every harness HTML file is written under a
         tempfile.mkdtemp() directory removed in tearDown.

Run from the clone root:
    python3 -m unittest discover -s tests -q
    python3 -m unittest tests.test_order_reorder -v
"""
from __future__ import annotations

import html as html_lib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from flask import Flask

HERE = Path(__file__).resolve().parent
CLONE = HERE.parent
sys.path.insert(0, str(CLONE))

from voyagertools import views  # noqa: E402

JS_PATH = CLONE / "static" / "voyager_tools.js"
CSS_PATH = CLONE / "static" / "voyager_tools.css"
PANEL_PATH = CLONE / "templates" / "voyager_tools" / "panel.html"
CHROME = "/usr/bin/google-chrome"

# Eight blocks, one with two rows (CCC_T3), enough to exercise a drag end to
# end, seven typed-position edge cases, a contiguous and a scattered
# multi-select, and keyboard movement, without a 116-row fixture that would
# only make the harness slower to render.
BLOCKS = [
    {"key": "AAA_T1", "site": "AAA", "site_name": "Alpha Reef", "transect": "T1",
     "rows": [{"readable_id": "AAA_T1_2024ann"}]},
    {"key": "BBB_T2", "site": "BBB", "site_name": "Bravo Flat", "transect": "T2",
     "rows": [{"readable_id": "BBB_T2_2024ann"}]},
    {"key": "CCC_T3", "site": "CCC", "site_name": "Charlie Bank", "transect": "T3",
     "rows": [{"readable_id": "CCC_T3_2023ann"}, {"readable_id": "CCC_T3_2024ann"}]},
    {"key": "DDD_T4", "site": "DDD", "site_name": "Delta Shoal", "transect": "T4",
     "rows": [{"readable_id": "DDD_T4_2024ann"}]},
    {"key": "EEE_T5", "site": "EEE", "site_name": "Echo Reef", "transect": "T5",
     "rows": [{"readable_id": "EEE_T5_2024ann"}]},
    {"key": "FFF_T6", "site": "FFF", "site_name": "Foxtrot Point", "transect": "T6",
     "rows": [{"readable_id": "FFF_T6_2024ann"}]},
    {"key": "GGG_T7", "site": "GGG", "site_name": "Golf Terrace", "transect": "T7",
     "rows": [{"readable_id": "GGG_T7_2024ann"}]},
    {"key": "HHH_T8", "site": "HHH", "site_name": "Hotel Reef", "transect": "T8",
     "rows": [{"readable_id": "HHH_T8_2024ann"}]},
]


def order_payload(locked: bool = False) -> dict:
    """The R4 /api/order shape (docs/superpowers/specs/2026-09-04-voyager-
    -tools-window-inventory.md section 1) for the eight fixture blocks."""
    ordered_ids = [r["readable_id"] for b in BLOCKS for r in b["rows"]]
    return {
        "lock": {"locked_at": "2026-09-06 10:00 AST", "locked_by": "LO"} if locked else None,
        "started": [],
        "blocks": BLOCKS,
        "skipped": [],
        "ordered_ids": ordered_ids,
        "count": len(BLOCKS),
        "first": ordered_ids[0],
        "last": ordered_ids[-1],
    }


STATE_PAYLOAD = {"now": "2026-09-06T10:00:00-04:00"}


# ---------------------------------------------------------------------------
# Static guards: source-text invariants that hold with no browser involved.
# ---------------------------------------------------------------------------

class StaticGuardsCase(unittest.TestCase):
    def setUp(self):
        self.js = JS_PATH.read_text(encoding="utf-8")
        self.css = CSS_PATH.read_text(encoding="utf-8")
        self.panel = PANEL_PATH.read_text(encoding="utf-8")

    def test_node_check_passes(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed on this box")
        done = subprocess.run([node, "--check", str(JS_PATH)], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_draggable_is_never_conditional(self):
        """The row that claimed draggable="true" with no dragstart/dragover/
        drop handler (the defect T20260906-213500-1 opens with) must never
        come back: draggable is unconditional now, and dragstart itself
        refuses a locked order rather than the attribute silently flipping
        to "false" with no explanation."""
        self.assertIn('draggable="true" data-key=', self.js)
        self.assertNotIn('draggable="\' + (locked', self.js)
        self.assertNotIn("draggable='true'", self.js.replace('".row[draggable=\'true\']"', ""),
                          "no stray single-quoted draggable=true outside the dragstart selector")

    def test_one_lock_message_used_by_all_four_new_interactions(self):
        """Drag, the typed position, Move selected and the arrow-key handle
        each refuse a locked order with the same LOCK_MESSAGE text rather
        than four different sentences or silent no-ops."""
        self.assertIn('var LOCK_MESSAGE = "The processing order is locked', self.js)
        # Exactly four call sites write LOCK_MESSAGE into the result line:
        # the drag refusal (with preventDefault), the typed-position commit,
        # Move selected, and the arrow-key handle. Two more mentions of the
        # name are the doc-comment lines explaining the rule, not code.
        self.assertEqual(self.js.count("setResult(orderResult, LOCK_MESSAGE"), 4,
                          "drag, typed position, Move selected and the arrow-key handle each write LOCK_MESSAGE")
        self.assertIn("if (isLocked()) { e.preventDefault(); setResult(orderResult, LOCK_MESSAGE", self.js)
        self.assertIn('commitPosition(key, raw) {\n      if (isLocked()) { setResult(orderResult, LOCK_MESSAGE', self.js)
        self.assertIn('order-move-btn").addEventListener("click", function () {\n      if (isLocked())', self.js)

    def test_ticking_a_box_is_exempt_from_the_lock_check(self):
        """handleSelect (the tick-box handler) must never call isLocked():
        selecting rows does not move anything, so it stays available while
        the order is locked, unlike the other three new interactions."""
        body = self.js[self.js.index("function handleSelect("):self.js.index("orderBody.addEventListener(\"click\"")]
        self.assertNotIn("isLocked", body)

    def test_position_parser_is_the_single_rule(self):
        """One parsePositionInput used by both the per-row field and Move
        selected, so a whole number clamps and anything else - blank,
        letters, a decimal - refuses the same way everywhere."""
        self.assertEqual(self.js.count("parsePositionInput("), 3, "one definition plus two call sites")
        self.assertIn(r'/^[+-]?\d+$/', self.js, "the parser accepts a whole number only, no decimals")

    def test_save_order_body_shape_is_untouched(self):
        """The Save order POST is unchanged in shape: {transects, initials}
        built from state.order.keys, matching R5's request fields exactly
        as the parity contract requires."""
        self.assertIn('body: { transects: state.order.keys, initials: who }', self.js)

    def test_no_route_path_or_json_key_touched(self):
        for path in ("/api/order", "/api/order/lock", "/api/order/unlock", "/api/state"):
            self.assertIn(path, self.js)

    def test_new_toolbar_markup_and_tooltip(self):
        self.assertIn('data-vt="order-toolbar"', self.panel)
        self.assertIn('data-vt="order-select-count"', self.panel)
        self.assertIn('data-vt="order-move-to"', self.panel)
        self.assertIn('data-vt="order-move-btn"', self.panel)
        self.assertIn('data-vt="order-clear-btn"', self.panel)
        # Clear selection's label says everything (design doc section 7,
        # rule 3), matching the Tick all / Untick all exemption; it carries
        # no title, unlike Move selected which does.
        clear_btn = re.search(r'<button[^>]*data-vt="order-clear-btn"[^>]*>', self.panel).group(0)
        self.assertNotIn("title=", clear_btn)
        move_btn = re.search(r'<button[^>]*data-vt="order-move-btn"[^>]*>', self.panel).group(0)
        self.assertIn("title=", move_btn)

    def test_new_row_tooltips_follow_the_house_rules(self):
        """Every title="..." the row template builds (the handle, the
        select checkbox, the position field) is scanned the same way
        tests/test_views.py::test_no_tooltip_opens_with_you scans the
        static page: no "You", no "above", no "this tab"."""
        row_fn = self.js[self.js.index("function renderOrder("):self.js.index("function moveKey(")]
        titles = re.findall(r'title="([^"]*)"', row_fn.replace("\\'", "'"))
        titles += re.findall(r"title=\\'([^\\']*)\\'", row_fn)
        self.assertGreaterEqual(len(titles), 3, "handle, select and position field each carry a title")
        for title in titles:
            self.assertFalse(title.startswith("You "), title)
            self.assertNotIn("above", title.lower().split())
            self.assertNotIn("this tab", title.lower())

    def test_css_has_no_syntax_error_markers(self):
        # A crude but effective guard: braces balance and no stray "{{" from
        # a botched template-string edit.
        self.assertEqual(self.css.count("{"), self.css.count("}"))
        self.assertNotIn("{{", self.css)


# ---------------------------------------------------------------------------
# Behaviour in a real browser
# ---------------------------------------------------------------------------

class ReorderBrowserCase(unittest.TestCase):
    """Renders the real panel through a Flask test client with an admin
    session (views.page() needs only deps.is_locked and the session flag;
    no registry, workbench or params seam is touched by that route), slices
    the <section class="voyager-tools"> the same way views.fragment_payload
    does (views.slice_section), and drives the real voyager_tools.js against
    a stubbed fetch in headless Chrome."""

    def setUp(self):
        if not Path(CHROME).exists():
            self.skipTest("headless Chrome is not installed on this box")
        self.tmp = Path(tempfile.mkdtemp(prefix="vt-order-"))
        self._saved_locked = views.deps.is_locked
        views.deps.is_locked = lambda: False
        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(views.voyager_tools_bp)
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess["admin_unlocked"] = True
        page = self.client.get("/voyager-tools/").get_data(as_text=True)
        section = views.slice_section(page, '<section class="voyager-tools"')
        self.assertIsNotNone(section, "could not extract the voyager-tools section from the rendered page")
        self.section = section

    def tearDown(self):
        views.deps.is_locked = self._saved_locked
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- harness plumbing --------------------------------------------------

    PRELUDE = r"""
function qa(sel){ return Array.prototype.slice.call(document.querySelectorAll(sel)); }
function q(sel){ return document.querySelector(sel); }
function keysInDom(){ return qa('.vt-order-row').map(function(el){ return el.getAttribute('data-key'); }); }
function posVal(key){ var el=q('[data-vt-pos="'+key+'"]'); return el ? el.value : null; }
function resultText(){ var el=q('[data-vt="order-result"]'); return el ? el.textContent : ''; }
function waitFor(pred, ms) {
  var deadline = Date.now() + (ms || 4000);
  return new Promise(function (resolve) {
    (function poll() {
      var v = false;
      try { v = pred(); } catch (e) { v = false; }
      if (v) return resolve(true);
      if (Date.now() > deadline) return resolve(false);
      window.setTimeout(poll, 20);
    })();
  });
}
function dragMove(fromKey, toKey, before) {
  var fromEl = q('.vt-order-row[data-key="' + fromKey + '"]');
  var toEl = q('.vt-order-row[data-key="' + toKey + '"]');
  fromEl.dispatchEvent(new Event('dragstart', {bubbles:true, cancelable:true}));
  var rect = toEl.getBoundingClientRect();
  var y = before ? (rect.top + 1) : (rect.bottom - 1);
  toEl.dispatchEvent(new MouseEvent('dragover', {bubbles:true, cancelable:true, clientY:y}));
  toEl.dispatchEvent(new Event('drop', {bubbles:true, cancelable:true}));
  fromEl.dispatchEvent(new Event('dragend', {bubbles:true, cancelable:true}));
}
function tickBox(key, shift) {
  var el = q('[data-vt-select="' + key + '"]');
  el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, shiftKey: !!shift}));
}
function setPos(key, value) {
  var el = q('[data-vt-pos="' + key + '"]');
  el.focus();
  el.value = value;
  el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true, cancelable:true}));
}
function pressArrow(el, dir) {
  el.dispatchEvent(new KeyboardEvent('keydown', {key: dir, bubbles:true, cancelable:true}));
}
"""

    def _harness(self, name: str, payload: dict, steps_body: str) -> Path:
        stub = ("<script>(function(){"
                "window.fetch = function(url, opts) {"
                "  var u = String(url); var method = (opts && opts.method) || 'GET'; var body = {};"
                "  if (u.indexOf('/api/state') !== -1) body = __STATE__;"
                "  else if (u.indexOf('/api/order') !== -1 && method === 'POST') {"
                "    window.__SAVE_CALLS__ = window.__SAVE_CALLS__ || [];"
                "    window.__SAVE_CALLS__.push(JSON.parse(opts.body));"
                "    body = {ok:true, ordered_ids: (JSON.parse(opts.body).transects || []), count: 0, first:'', last:''};"
                "  } else if (u.indexOf('/api/order') !== -1) { body = __ORDER__; }"
                "  return Promise.resolve({ ok:true, status:200,"
                "    headers:{get:function(){return 'application/json';}},"
                "    json:function(){return Promise.resolve(body);},"
                "    text:function(){return Promise.resolve(JSON.stringify(body));} });"
                "};"
                "})();</script>"
                ).replace("__ORDER__", json.dumps(payload)).replace("__STATE__", json.dumps(STATE_PAYLOAD))
        steps = ("(function(){"
                 "var results = {};"
                 "var pre = document.createElement('pre'); pre.id = 'results'; document.body.appendChild(pre);"
                 "function report(){ pre.textContent = JSON.stringify(results); }"
                 "waitFor(function(){ return qa('.vt-order-row').length === " + str(len(BLOCKS)) + "; }).then(function(ok){"
                 "  if (!ok) { results.rows_rendered = 'rows: ' + qa(\".vt-order-row\").length; results.__done='yes'; report(); return; }"
                 + steps_body +
                 "});"
                 "})();")
        page = ("<!doctype html><html><head><meta charset=\"utf-8\"><title>order harness</title></head><body>"
                + self.section
                + stub
                + f'<script src="file://{JS_PATH}"></script>'
                + "<script>" + self.PRELUDE + steps + "</script></body></html>")
        out = self.tmp / f"harness_{name}.html"
        out.write_text(page, encoding="utf-8")
        return out

    def _run(self, path: Path) -> dict:
        profile = self.tmp / (path.stem + "_profile")
        cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
               "--window-size=1280,1600", f"--user-data-dir={profile}", "--virtual-time-budget=10000",
               "--dump-dom", f"file://{path}"]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        m = re.search(r'<pre id="results">(.*?)</pre>', done.stdout, re.DOTALL)
        self.assertIsNotNone(m, "harness wrote no results; chrome stderr: " + done.stderr[-800:])
        results = json.loads(html_lib.unescape(m.group(1)))
        self.assertEqual(results.get("__done"), "yes", "harness did not finish: " + json.dumps(results))
        return results

    def _assert_all_pass(self, results: dict):
        failures = {k: v for k, v in results.items() if k not in ("__done", "initial") and not (v == "pass" or v is True)}
        self.assertFalse(failures, json.dumps(failures, indent=2))

    # -- 1. drag ------------------------------------------------------------

    def test_drag_last_to_first(self):
        steps = (
            "var before = keysInDom(); results.initial = before.join(',');"
            "dragMove('HHH_T8', 'AAA_T1', true);"
            "var after = keysInDom();"
            "results.drag_last_to_first = (after[0] === 'HHH_T8' && after.slice(1).join(',') === before.slice(0,7).join(',')) ? 'pass' : 'fail: ' + after.join(',');"
            "results.__done='yes'; report();"
        )
        results = self._run(self._harness("drag_first", order_payload(), steps))
        self._assert_all_pass(results)

    def test_drag_first_to_last(self):
        steps = (
            "var before = keysInDom();"
            "dragMove('AAA_T1', 'HHH_T8', false);"
            "var after = keysInDom();"
            "var expected = before.slice(1).concat(['AAA_T1']);"
            "results.drag_first_to_last = (after.join(',') === expected.join(',')) ? 'pass' : 'fail: ' + after.join(',');"
            "results.__done='yes'; report();"
        )
        results = self._run(self._harness("drag_last", order_payload(), steps))
        self._assert_all_pass(results)

    # -- 2. typed position ---------------------------------------------------

    def test_typed_position_edge_cases(self):
        steps = (
            "var expected = keysInDom();"
            "function checkOrder(label){ var now = keysInDom(); results[label] = (now.join(',') === expected.join(',')) ? 'pass' : 'fail: ' + now.join(',') + ' vs ' + expected.join(','); }"
            # position 1 on the last block
            "setPos('HHH_T8', '1'); expected = ['HHH_T8'].concat(expected.filter(function(k){return k!=='HHH_T8';})); checkOrder('pos_to_1');"
            # position N on the current first block
            "var f1 = expected[0]; setPos(f1, String(expected.length)); expected = expected.slice(1).concat([f1]); checkOrder('pos_to_n');"
            # position 0 clamps to 1, applied to the current last block
            "var l1 = expected[expected.length-1]; setPos(l1, '0'); expected = [l1].concat(expected.filter(function(k){return k!==l1;})); checkOrder('pos_zero_clamps_to_1');"
            # position N+1 clamps to N, applied to the current first block
            "var f2 = expected[0]; setPos(f2, String(expected.length+1)); expected = expected.filter(function(k){return k!==f2;}).concat([f2]); checkOrder('pos_over_n_clamps_to_n');"
            # a negative position clamps to 1
            "var l2 = expected[expected.length-1]; setPos(l2, '-5'); expected = [l2].concat(expected.filter(function(k){return k!==l2;})); checkOrder('pos_negative_clamps_to_1');"
            # a non-number refuses: no reorder, the field reverts, a message shows
            "var t1 = expected[3]; var beforeVal = posVal(t1); setPos(t1, 'abc'); checkOrder('pos_non_number_no_change');"
            "results.pos_non_number_message = /whole number/i.test(resultText()) ? 'pass' : 'fail: ' + resultText();"
            "results.pos_non_number_reverts_field = (posVal(t1) === beforeVal) ? 'pass' : 'fail: field shows ' + posVal(t1);"
            # a blank entry refuses the same way
            "setPos(t1, ''); checkOrder('pos_blank_no_change');"
            "results.pos_blank_message = /whole number/i.test(resultText()) ? 'pass' : 'fail: ' + resultText();"
            "results.__done='yes'; report();"
        )
        results = self._run(self._harness("position", order_payload(), steps))
        self._assert_all_pass(results)

    # -- 3. multi-select ------------------------------------------------------

    def test_multiselect_contiguous_and_scattered(self):
        steps = (
            # a contiguous run: click CCC, shift-click EEE => C, D, E ticked
            "tickBox('CCC_T3', false); tickBox('EEE_T5', true);"
            "var checked = qa('[data-vt-select]').filter(function(el){return el.checked;}).map(function(el){return el.getAttribute('data-vt-select');});"
            "results.contiguous_shift_click_selects_the_run = (checked.slice().sort().join(',') === 'CCC_T3,DDD_T4,EEE_T5') ? 'pass' : 'fail: ' + checked.join(',');"
            "q('#vt-order-moveto').value = '1'; q('[data-vt=\"order-move-btn\"]').click();"
            "var afterMove = keysInDom();"
            "results.contiguous_move_to_1 = (afterMove.slice(0,3).join(',') === 'CCC_T3,DDD_T4,EEE_T5') ? 'pass' : 'fail: ' + afterMove.join(',');"
            "q('[data-vt=\"order-clear-btn\"]').click();"
            "results.clear_selection_count = (q('[data-vt=\"order-select-count\"]').textContent === '0 selected') ? 'pass' : 'fail: ' + q('[data-vt=\"order-select-count\"]').textContent;"
            # a scattered set from the order left by the move above
            "var order2 = keysInDom();"
            "tickBox(order2[3], false); tickBox(order2[5], false); tickBox(order2[7], false);"
            "var checked2 = qa('[data-vt-select]').filter(function(el){return el.checked;}).map(function(el){return el.getAttribute('data-vt-select');});"
            "var scatteredKeys = [order2[3], order2[5], order2[7]];"
            "results.scattered_click_selects_exactly_three = (checked2.slice().sort().join(',') === scatteredKeys.slice().sort().join(',')) ? 'pass' : 'fail: ' + checked2.join(',');"
            "q('#vt-order-moveto').value = '2'; q('[data-vt=\"order-move-btn\"]').click();"
            "var afterScatter = keysInDom();"
            "var remaining = order2.filter(function(k){ return scatteredKeys.indexOf(k) === -1; });"
            "var expectedScatter = remaining.slice(0,1).concat(scatteredKeys, remaining.slice(1));"
            "results.scattered_move_preserves_order = (afterScatter.join(',') === expectedScatter.join(',')) ? 'pass' : 'fail: ' + afterScatter.join(',') + ' vs ' + expectedScatter.join(',');"
            "results.__done='yes'; report();"
        )
        results = self._run(self._harness("multiselect", order_payload(), steps))
        self._assert_all_pass(results)

    def test_multiselect_move_requires_a_ticked_block(self):
        steps = (
            "q('#vt-order-moveto').value = '2'; q('[data-vt=\"order-move-btn\"]').click();"
            "results.empty_selection_message = /tick at least one/i.test(resultText()) ? 'pass' : 'fail: ' + resultText();"
            "results.__done='yes'; report();"
        )
        results = self._run(self._harness("multiselect_empty", order_payload(), steps))
        self._assert_all_pass(results)

    # -- 4. keyboard ----------------------------------------------------------

    def test_keyboard_arrow_moves_and_keeps_focus(self):
        steps = (
            "var before = keysInDom(); var lastKey = before[before.length-1];"
            "var handle = q('[data-vt-handle=\"' + lastKey + '\"]'); handle.focus();"
            "pressArrow(handle, 'ArrowUp');"
            "var mid = keysInDom();"
            "results.arrow_up_moves_one = (mid[mid.length-2] === lastKey) ? 'pass' : 'fail: ' + mid.join(',');"
            "var activeAfterFirst = document.activeElement;"
            "results.focus_retained_after_move = (activeAfterFirst && activeAfterFirst.getAttribute && activeAfterFirst.getAttribute('data-vt-handle') === lastKey) ? 'pass' : 'fail';"
            # a second ArrowUp on whatever now has focus - no explicit
            # re-focus here - proves the app itself refocused the moved
            # handle after the first move, not just this test script.
            "if (activeAfterFirst) pressArrow(activeAfterFirst, 'ArrowUp');"
            "var after2 = keysInDom();"
            "results.arrow_up_repeatable_without_refocus = (after2[after2.length-3] === lastKey) ? 'pass' : 'fail: ' + after2.join(',');"
            "results.__done='yes'; report();"
        )
        results = self._run(self._harness("keyboard", order_payload(), steps))
        self._assert_all_pass(results)

    # -- locked refuses all four ----------------------------------------------

    def test_locked_order_refuses_all_four_new_interactions(self):
        steps = (
            "var before = keysInDom(); results.initial = before.join(',');"
            "results.locked_rows_still_draggable_attr = (q('.vt-order-row').getAttribute('draggable') === 'true') ? 'pass' : 'fail';"
            # 1. drag
            "dragMove(before[before.length-1], before[0], true);"
            "var afterDrag = keysInDom();"
            "results.locked_drag_unchanged = (afterDrag.join(',') === before.join(',')) ? 'pass' : 'fail: ' + afterDrag.join(',');"
            "results.locked_drag_message = (resultText().indexOf('locked') !== -1) ? 'pass' : 'fail: ' + resultText();"
            # 2. typed position
            "setPos(before[before.length-1], '1');"
            "var afterPos = keysInDom();"
            "results.locked_position_unchanged = (afterPos.join(',') === before.join(',')) ? 'pass' : 'fail: ' + afterPos.join(',');"
            "results.locked_position_message = (resultText().indexOf('locked') !== -1) ? 'pass' : 'fail: ' + resultText();"
            # 3. multi-select move (ticking itself is allowed; the move refuses)
            "tickBox(before[0], false);"
            "q('#vt-order-moveto').value = '2'; q('[data-vt=\"order-move-btn\"]').click();"
            "var afterMulti = keysInDom();"
            "results.locked_multiselect_unchanged = (afterMulti.join(',') === before.join(',')) ? 'pass' : 'fail: ' + afterMulti.join(',');"
            "results.locked_multiselect_message = (resultText().indexOf('locked') !== -1) ? 'pass' : 'fail: ' + resultText();"
            "results.locked_ticking_itself_allowed = q('[data-vt-select=\"' + before[0] + '\"]').checked ? 'pass' : 'fail';"
            # 4. keyboard arrow
            "var handle = q('[data-vt-handle=\"' + before[0] + '\"]'); handle.focus();"
            "pressArrow(handle, 'ArrowDown');"
            "var afterArrow = keysInDom();"
            "results.locked_arrow_unchanged = (afterArrow.join(',') === before.join(',')) ? 'pass' : 'fail: ' + afterArrow.join(',');"
            "results.locked_arrow_message = (resultText().indexOf('locked') !== -1) ? 'pass' : 'fail: ' + resultText();"
            "results.__done='yes'; report();"
        )
        results = self._run(self._harness("locked", order_payload(locked=True), steps))
        self._assert_all_pass(results)

    # -- save payload ---------------------------------------------------------

    def test_save_order_payload_matches_the_existing_contract(self):
        steps = (
            "dragMove('HHH_T8', 'AAA_T1', true);"
            "q('#vt-initials').value = 'LO'; q('#vt-initials').dispatchEvent(new Event('change', {bubbles:true}));"
            "q('[data-vt=\"order-save\"]').click();"
            "waitFor(function(){ return window.__SAVE_CALLS__ && window.__SAVE_CALLS__.length === 1; }, 4000).then(function(ok){"
            "  if (!ok) { results.save_call_made = 'fail: no call reached the stub'; results.__done='yes'; report(); return; }"
            "  var sent = window.__SAVE_CALLS__[0];"
            "  var keys = Object.keys(sent).sort();"
            "  results.save_call_made = 'pass';"
            "  results.save_body_keys_correct = (keys.join(',') === 'initials,transects') ? 'pass' : 'fail: ' + keys.join(',');"
            "  results.save_transects_is_array_of_strings = (Array.isArray(sent.transects) && sent.transects.length === 8 && sent.transects.every(function(k){return typeof k === 'string';})) ? 'pass' : 'fail: ' + JSON.stringify(sent.transects);"
            "  results.save_transects_matches_dom_order = (sent.transects.join(',') === keysInDom().join(',')) ? 'pass' : 'fail: ' + sent.transects.join(',');"
            "  results.save_initials_value = (sent.initials === 'LO') ? 'pass' : 'fail: ' + sent.initials;"
            "  results.__done = 'yes'; report();"
            "});"
        )
        results = self._run(self._harness("save_payload", order_payload(), steps))
        self._assert_all_pass(results)


if __name__ == "__main__":
    unittest.main()
