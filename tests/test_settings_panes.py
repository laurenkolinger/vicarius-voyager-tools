#!/usr/bin/env python3
"""
Module: tests/test_settings_panes.py
Purpose: Drives the settings, desktop and history panes of the Atlas control
         panel (static/vt_settings.js over the pane seam of
         static/voyager_tools.js, program C milestone C3) in headless Chrome
         against a stubbed fetch: a save posts only the changed keys with the
         pane's saved_at and the typed initials, once per press; refusals
         (a bad number, bad initials, a 400, a 409) show one line and post
         nothing or keep the values; a drift line and Record as found; the
         Shelf copy confirmation names the consequence and posts confirm;
         the run switches and the floors confirm with their old and new
         values; the history table, its filters and its problem lines; the
         read-only panes; the relock; the standalone host with no settings
         routes; and the seam when the pane script loads first and the atlas
         mounts the panel with mountPanel.
Inputs:  the real panel rendered through a Flask test client with an admin
         session (the pattern of tests/test_order_reorder.py); the clone's
         static scripts; headless Chrome at /usr/bin/google-chrome.
Outputs: unittest results; every harness file sits in a tempfile.mkdtemp()
         directory removed in tearDown.

Run from the clone root:
    python3 -m unittest tests.test_settings_panes -v
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

PANEL_JS = CLONE / "static" / "voyager_tools.js"
SETTINGS_JS = CLONE / "static" / "vt_settings.js"
CHROME = "/usr/bin/google-chrome"
SAVED_AT = "2026-09-26T21:54:25-04:00"
STALE_LINE = "These settings changed since the pane was opened; reload the pane and save again."


def field(key, label, kind, **extra):
    """One field in the settings payload shape (program C spec 4.8)."""
    out = {"key": key, "label": label, "type": kind, "tip": f"Type the {label}.", "confirm": False,
           "reason_required": False}
    out.update(extra)
    return out


MANUAL_EDIT = {
    "store": "manual_edit", "saved_at": SAVED_AT, "saved_by": "LO", "drift": [],
    "fields": [field("scale_error_max_mm", "Scale error limit (mm)", "number", min=0, max=1000, min_exclusive=True,
                     unit="mm"),
               field("markers_per_chunk", "Markers per chunk", "integer", min=1, max=20),
               field("edit_bench_default", "Edit bench default", "path"),
               field("open_close_logging", "Open and close logging", "boolean")],
    "values": {"scale_error_max_mm": 4.94, "markers_per_chunk": 4, "edit_bench_default": "/mnt/rip/driver_staging/editbench",
               "open_close_logging": True},
}
SHELF_OPTIONS = [{"value": True, "label": "Archive the Shelf copy", "disabled": False, "tip": "Pick this to keep it."},
                 {"value": False, "label": "Overwrite the Shelf copy", "disabled": False, "tip": "Pick this to replace it."}]
SHELF = {
    "store": "manual_edit", "saved_at": SAVED_AT, "saved_by": "LO", "drift": [], "overwrite_available": True,
    "shelf_root": "/volume6/Archive8_12TB/driver_deposits",
    "fields": [field("archive_shelf_copy", "Shelf copy", "choice", confirm=True, reason_required=True,
                     options=SHELF_OPTIONS)],
    "values": {"archive_shelf_copy": True},
}
RUNS = {
    "store": "voyager_config", "saved_at": None, "saved_by": None, "drift": [], "has_voyager2": False,
    "fields": [field("voyager1.run_enabled", "Run Voyager 1 batches", "boolean", confirm=True, reason_required=True),
               field("voyager1.delete_from_workbench_default", "Delete from the Workbench by default (Voyager 1)",
                     "boolean")],
    "values": {"voyager1.run_enabled": True, "voyager1.delete_from_workbench_default": False},
}
FLOORS = {
    "store": "driver_nas", "saved_at": None, "saved_by": None, "drift": [], "driver_started_at": None,
    "restart_needed": False,
    "fields": [field("defaults.min_free_gb_local", "Workbench floor (GB)", "number", confirm=True, reason_required=True,
                     unit="GB"),
               field("defaults.min_free_gb_remote", "Shelf floor (GB)", "number", confirm=True, reason_required=True,
                     unit="GB"),
               field("defaults.headroom_factor", "Headroom factor", "number", confirm=True, reason_required=True)],
    "values": {"defaults.min_free_gb_local": 100, "defaults.min_free_gb_remote": 500, "defaults.headroom_factor": 1.1},
}
HISTORY = {
    "rows": [{"at": "2026-09-27T10:12:03-04:00", "by": "LO", "store": "manual_edit", "key": "scale_error_max_mm",
              "old": "4.94", "new": "5", "reason": "<b>tighter</b>", "label": "Scale error limit (mm)"},
             {"at": "2026-09-26T21:54:25-04:00", "by": "LO", "store": "manual_edit", "key": "align_spread_max_mm",
              "old": "null", "new": "8.65", "reason": "baseline when the settings history began"}],
    "total": 5, "skipped": 1,
    "conflict_copies": ["/mnt/rip/vicarius_drive/vicarius/_METADATA/settings_history_dl_Conflict.csv"],
}
LADDER = {"tiers": [{"name": "voyager", "label": "VOYAGER", "kind": "model", "modules": ["3D_phase_1"],
                     "process_hints": ["metashape"], "beside_editor_min_free_mb": 24000,
                     "beside_editor_min_available_gb": 160, "beside_editor_max_pressure_avg10": 5.0},
                    {"name": "vesper", "label": "Vesper.llm", "kind": "service", "modules": [], "process_hints": ["ollama"]}],
          "ignore_processes": [], "path": "/mnt/rip/vicarius_drive/vicarius/_METADATA/gpu_ladder.yaml",
          "updated": "2026-09-27 05:30 AST", "fallback": False, "error": "", "has_editor_tier": False}
CLAIMS = {"claims": {"c1": {"tier": "vega", "module": "reef_point_seg", "run_id": "train_1", "pid": 42, "by": "LO",
                            "cards": [0], "started": "2026-09-27T09:00:00-04:00"}},
          "unclaimed": {}, "last_refusal": None, "ladder": {}}
LOCKS = {"locks": {"tagfab": {"locked": True, "by": "LO", "ts": "2026-09-20T10:00:00-04:00"}}}
MODULES = [{"name": "tagfab", "display_name": "TagFab", "category": "AI", "status": "active", "pipeline_stage": "ingest"},
           {"name": "manual_edit", "display_name": "Start manual edit", "category": "3D", "status": "active"}]
PREFS = {"appearance": {"skin": "fun", "bg_grid": True, "bg_scan": False, "bg_pixels": True, "grid_px": 32,
                        "bg_color": "#0a0a0f"}, "icon_positions": {}}

PRELUDE = r"""
function q(sel){ return document.querySelector(sel); }
function qa(sel){ return Array.prototype.slice.call(document.querySelectorAll(sel)); }
function vt(name){ return document.querySelector('[data-vt="' + name + '"]'); }
function sleep(ms){ return new Promise(function (r) { setTimeout(r, ms); }); }
async function waitFor(pred, ms) {
  var deadline = Date.now() + (ms || 4000);
  while (Date.now() < deadline) {
    try { if (pred()) return true; } catch (e) { /* not yet */ }
    await sleep(20);
  }
  return false;
}
function calls(method, prefix) {
  return (window.__CALLS__ || []).filter(function (c) { return c.method === method && c.url.indexOf(prefix) === 0; });
}
function setInput(el, value) {
  el.value = value;
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
}
function tick(el, on) { if (el.checked !== on) el.click(); }
function showPane(key) { q('[data-vt-tab="' + key + '"]').click(); }
function dialog() { return q('.vt-dialog'); }
function check(results, name, ok, detail) { results[name] = ok ? 'pass' : ('fail: ' + (detail || '')); }
"""

STUB = r"""
<script>
(function () {
  window.__CALLS__ = [];
  function answer(spec) {
    var status = spec.status || 200;
    var body = spec.body === undefined ? {} : spec.body;
    return Promise.resolve({ ok: status >= 200 && status < 300, status: status,
      headers: { get: function () { return 'application/json'; } },
      json: function () { return Promise.resolve(body); },
      text: function () { return Promise.resolve(JSON.stringify(body)); } });
  }
  window.fetch = function (url, opts) {
    var method = (opts && opts.method) || 'GET';
    var u = String(url);
    var body = null;
    try { body = opts && opts.body ? JSON.parse(opts.body) : null; } catch (e) { body = opts.body; }
    window.__CALLS__.push({ method: method, url: u, body: body });
    var routes = window.__ROUTES__ || {};
    var keys = Object.keys(routes).sort(function (a, b) { return b.length - a.length; });
    for (var i = 0; i < keys.length; i++) {
      var parts = keys[i].split(' ');
      if (parts[0] === method && u.indexOf(parts[1]) === 0) {
        var list = routes[keys[i]];
        var spec = list.length > 1 ? list.shift() : list[0];
        return answer(spec);
      }
    }
    return answer({ status: 404, body: { error: 'Not found' } });
  };
})();
</script>
"""


class PanesCase(unittest.TestCase):
    """Renders the real panel once per test and runs one async step script in Chrome."""

    def setUp(self):
        if not Path(CHROME).exists():
            self.skipTest("headless Chrome is not installed on this box")
        self.tmp = Path(tempfile.mkdtemp(prefix="vt-panes-"))
        self._saved_locked = views.deps.is_locked
        views.deps.is_locked = lambda: False
        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(views.voyager_tools_bp)
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["admin_unlocked"] = True
        page = client.get("/voyager-tools/").get_data(as_text=True)
        self.section = views.slice_section(page, '<section class="voyager-tools"')
        self.assertIsNotNone(self.section)

    def tearDown(self):
        views.deps.is_locked = self._saved_locked
        shutil.rmtree(self.tmp, ignore_errors=True)

    def base_routes(self) -> dict:
        """Every route the panes read, answering the fixtures once each (the last answer repeats)."""
        return {
            "GET /voyager-tools/api/state": [{"body": {"now": "2026-09-27T10:00:00-04:00"}}],
            "GET /voyager-tools/api/order": [{"body": {"lock": None, "started": [], "blocks": [], "skipped": [],
                                                       "ordered_ids": [], "count": 0, "first": "", "last": ""}}],
            "GET /api/settings/manual-edit": [{"body": MANUAL_EDIT}],
            "GET /api/settings/shelf": [{"body": SHELF}],
            "GET /api/settings/voyager": [{"body": RUNS}],
            "GET /api/settings/floors": [{"body": FLOORS}],
            "GET /api/settings/gpu-ladder": [{"body": LADDER}],
            "GET /api/settings/history": [{"body": HISTORY}],
            "GET /api/gpu/claims": [{"body": CLAIMS}],
            "GET /api/modules/locks": [{"body": LOCKS}],
            "GET /api/modules/enriched": [{"body": {"modules": MODULES}}],
            "GET /api/desktop/prefs": [{"body": PREFS}],
        }

    def run_steps(self, name: str, steps: str, routes: dict | None = None, settings_first: bool = False,
                  wrap: bool = False, initials: str = "lo") -> dict:
        """Build the harness, run it in Chrome, and return the results the steps reported."""
        routing = "<script>window.__ROUTES__ = " + json.dumps(routes or self.base_routes()) + ";</script>"
        section = f'<div class="win-body">{self.section}</div>' if wrap else self.section
        scripts = [SETTINGS_JS, PANEL_JS] if settings_first else [PANEL_JS, SETTINGS_JS]
        tags = "".join(f'<script src="file://{path}"></script>' for path in scripts)
        body = ("(async function(){ var results = {};"
                "var pre = document.createElement('pre'); pre.id = 'results'; document.body.appendChild(pre);"
                f"try {{ var box = q('#vt-initials'); if (box) setInput(box, {json.dumps(initials)});"
                + steps +
                "} catch (e) { results.__error = String(e && e.stack || e); }"
                "results.__done = 'yes'; pre.textContent = JSON.stringify(results); })();")
        page = ("<!doctype html><html><head><meta charset=\"utf-8\"><title>panes</title></head><body>"
                + STUB + routing + section + tags + "<script>" + PRELUDE + "</script>"
                + "<script>window.addEventListener('load', function () { setTimeout(function () {" + body
                + "}, 50); });</script></body></html>")
        path = self.tmp / f"harness_{name}.html"
        path.write_text(page, encoding="utf-8")
        profile = self.tmp / f"{name}_profile"
        cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
               "--window-size=1280,1600", f"--user-data-dir={profile}", "--virtual-time-budget=20000",
               "--dump-dom", f"file://{path}"]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        match = re.search(r'<pre id="results">(.*?)</pre>', done.stdout, re.DOTALL)
        self.assertIsNotNone(match, "harness wrote no results; chrome stderr: " + done.stderr[-800:])
        results = json.loads(html_lib.unescape(match.group(1)))
        self.assertEqual(results.get("__done"), "yes", json.dumps(results))
        self.assertNotIn("__error", results, results.get("__error"))
        failures = {k: v for k, v in results.items() if not k.startswith("__") and v != "pass"}
        self.assertFalse(failures, json.dumps(failures, indent=2))
        return results


class ManualEditPane(PanesCase):
    def test_save_posts_only_the_changed_key_once(self):
        routes = self.base_routes()
        routes["POST /api/settings/manual-edit"] = [{"body": {"store": "manual_edit", "saved_at": "2026-09-27T10:12:03-04:00",
                                                              "saved_by": "LO", "values": {},
                                                              "changes": [{"key": "scale_error_max_mm", "old": 4.94, "new": 5}]}},
                                                    {"body": MANUAL_EDIT}]
        steps = r"""
        showPane('settings');
        check(results, 'fields', await waitFor(function () { return qa('[data-vt="settings-fields"] [data-vt-key]').length === 4; }));
        var save = vt('settings-save');
        check(results, 'disabled_at_rest', save.disabled === true);
        check(results, 'meta', vt('settings-meta').textContent === 'Saved 2026-09-26 21:54 AST by LO', vt('settings-meta').textContent);
        var box = q('[data-vt-key="scale_error_max_mm"]');
        setInput(box, '5');
        check(results, 'enabled_after_change', save.disabled === false);
        setInput(box, '4.94');
        check(results, 'disabled_when_back', save.disabled === true);
        setInput(box, '5');
        save.click(); save.click();
        await waitFor(function () { return /Saved/.test(vt('settings-result').textContent); });
        var posts = calls('POST', '/api/settings/manual-edit');
        check(results, 'one_post', posts.length === 1, String(posts.length));
        var b = posts[0] && posts[0].body;
        check(results, 'body', b && JSON.stringify(b.values) === '{"scale_error_max_mm":5}' && b.initials === 'LO' &&
              b.saved_at === '2026-09-26T21:54:25-04:00' && b.reason === '', JSON.stringify(b));
        check(results, 'result_line', vt('settings-result').textContent === 'Saved 2026-09-27 10:12 AST by LO: 1 setting changed.',
              vt('settings-result').textContent);
        """
        self.run_steps("me_save", steps, routes)

    def test_refusals_post_nothing_or_show_the_server_line(self):
        routes = self.base_routes()
        routes["POST /api/settings/manual-edit"] = [{"status": 409, "body": {"error": STALE_LINE}},
                                                    {"status": 400, "body": {"error": "Markers per chunk must be at least 1 and at most 20."}}]
        steps = r"""
        showPane('settings');
        await waitFor(function () { return qa('[data-vt="settings-fields"] [data-vt-key]').length === 4; });
        var save = vt('settings-save');
        setInput(q('[data-vt-key="scale_error_max_mm"]'), 'abc');
        save.click();
        await sleep(100);
        check(results, 'bad_number_line', vt('settings-result').textContent === 'Scale error limit (mm) must be a number.', vt('settings-result').textContent);
        check(results, 'bad_number_no_post', calls('POST', '/api/settings/manual-edit').length === 0);
        setInput(q('[data-vt-key="scale_error_max_mm"]'), '5');
        setInput(q('#vt-initials'), 'L0');
        save.click();
        await sleep(100);
        check(results, 'bad_initials_line', vt('settings-result').textContent === 'Type your initials, two to four letters, at the top of the panel.', vt('settings-result').textContent);
        check(results, 'bad_initials_no_post', calls('POST', '/api/settings/manual-edit').length === 0);
        setInput(q('#vt-initials'), 'LO');
        save.click();
        await waitFor(function () { return vt('settings-result').textContent.length > 0 && calls('POST', '/api/settings/manual-edit').length === 1; });
        await sleep(50);
        check(results, 'stale_line', vt('settings-result').textContent === '__STALE__', vt('settings-result').textContent);
        check(results, 'stale_is_error', vt('settings-result').classList.contains('result-error'));
        check(results, 'values_kept', q('[data-vt-key="scale_error_max_mm"]').value === '5');
        setInput(q('[data-vt-key="markers_per_chunk"]'), '0');
        save.click();
        await waitFor(function () { return calls('POST', '/api/settings/manual-edit').length === 2; });
        await sleep(50);
        check(results, 'server_400_line', vt('settings-result').textContent === 'Markers per chunk must be at least 1 and at most 20.', vt('settings-result').textContent);
        """.replace("__STALE__", STALE_LINE)
        self.run_steps("me_refusals", steps, routes)

    def test_drift_line_and_record_as_found(self):
        drifted = dict(MANUAL_EDIT, drift=[{"key": "scale_error_max_mm", "disk": "5.0", "history": "4.94"}])
        routes = self.base_routes()
        routes["GET /api/settings/manual-edit"] = [{"body": drifted}, {"body": MANUAL_EDIT}]
        routes["POST /api/settings/drift/manual_edit"] = [{"body": {"store": "manual_edit", "saved_at": "2026-09-27T10:20:00-04:00",
                                                                    "changes": [{"key": "scale_error_max_mm", "old": 4.94, "new": 5.0}]}}]
        steps = r"""
        showPane('settings');
        await waitFor(function () { return !vt('settings-drift').hidden; });
        var line = vt('settings-drift').textContent;
        check(results, 'drift_line', line.indexOf('Scale error limit (mm) is 5.0 on disk and 4.94 in the settings history; that change has no history line.') !== -1, line);
        var record = q('[data-vt="settings-drift"] [data-vt-drift-record]');
        check(results, 'record_button', !!record);
        record.click();
        await sleep(100);
        check(results, 'reason_required', vt('settings-result').textContent === 'Type a reason before recording the change as found.', vt('settings-result').textContent);
        check(results, 'no_post_yet', calls('POST', '/api/settings/drift/').length === 0);
        setInput(vt('settings-reason'), 'edited by hand on the box');
        record.click();
        await waitFor(function () { return calls('POST', '/api/settings/drift/manual_edit').length === 1; });
        var body = calls('POST', '/api/settings/drift/manual_edit')[0].body;
        check(results, 'drift_body', body.initials === 'LO' && body.reason === 'edited by hand on the box', JSON.stringify(body));
        check(results, 'drift_cleared', await waitFor(function () { return vt('settings-drift').hidden; }));
        """
        self.run_steps("me_drift", steps, routes)

    def test_standalone_host_says_where_the_settings_live(self):
        routes = self.base_routes()
        del routes["GET /api/settings/manual-edit"]
        steps = r"""
        showPane('settings');
        check(results, 'line', await waitFor(function () { return /served by the VICARIUS desktop/.test(vt('settings-result').textContent); }), vt('settings-result').textContent);
        check(results, 'no_fields', qa('[data-vt="settings-fields"] [data-vt-key]').length === 0);
        check(results, 'save_disabled', vt('settings-save').disabled === true);
        """
        self.run_steps("me_standalone", steps, routes)

    def test_a_relock_hides_the_panes_behind_the_admin_line(self):
        routes = self.base_routes()
        routes["GET /api/settings/manual-edit"] = [{"status": 401, "body": {"error": "locked", "need_unlock": True}}]
        steps = r"""
        showPane('settings');
        check(results, 'relocked', await waitFor(function () { return q('.vt-stages').hidden === true; }));
        check(results, 'admin_line', vt('admin-line').textContent.indexOf('Admin mode is off. Unlock admin to use the Atlas control panel.') === 0, vt('admin-line').textContent);
        """
        self.run_steps("me_relock", steps, routes)


class ShelfAndRunsAndFloors(PanesCase):
    def test_overwrite_asks_for_a_reason_then_confirms_the_consequence(self):
        routes = self.base_routes()
        routes["POST /api/settings/shelf"] = [{"body": {"store": "manual_edit", "saved_at": "2026-09-27T10:30:00-04:00",
                                                        "saved_by": "LO", "values": {"archive_shelf_copy": False},
                                                        "changes": [{"key": "archive_shelf_copy", "old": True, "new": False}]}},
                                              {"body": dict(SHELF, values={"archive_shelf_copy": False})}]
        steps = r"""
        showPane('shelf');
        await waitFor(function () { return qa('[data-vt="shelf-fields"] input[type=radio]').length === 2; });
        var over = q('[data-vt="shelf-fields"] input[value="false"]');
        check(results, 'overwrite_enabled', over.disabled === false);
        over.click();
        var save = vt('shelf-save');
        check(results, 'save_enabled', save.disabled === false);
        save.click();
        await sleep(100);
        check(results, 'reason_first', vt('shelf-result').textContent === 'Type a reason before saving a change to Shelf copy.', vt('shelf-result').textContent);
        check(results, 'no_dialog_without_reason', !dialog());
        setInput(vt('shelf-reason'), 'mirror_delete shipped');
        save.click();
        check(results, 'dialog', await waitFor(function () { return !!dialog(); }));
        var text = dialog().textContent;
        check(results, 'consequence', text.indexOf('Overwrite the Shelf copy: each uploaded folder replaces its Shelf folder under /volume6/Archive8_12TB/driver_deposits, files the edit removed are deleted there, and once the bench copy is removed after the verify no pre-edit copy is kept anywhere.') !== -1, text);
        check(results, 'old_new', text.indexOf('Archive the Shelf copy') !== -1 && text.indexOf('Overwrite the Shelf copy') !== -1, text);
        check(results, 'no_post_before_ok', calls('POST', '/api/settings/shelf').length === 0);
        q('[data-vt-dialog="ok"]').click();
        await waitFor(function () { return calls('POST', '/api/settings/shelf').length === 1; });
        var b = calls('POST', '/api/settings/shelf')[0].body;
        check(results, 'body', b.values.archive_shelf_copy === false && b.confirm === true && b.reason === 'mirror_delete shipped' &&
              b.initials === 'LO' && b.saved_at === '2026-09-26T21:54:25-04:00', JSON.stringify(b));
        """
        self.run_steps("shelf_overwrite", steps, routes)

    def test_overwrite_disabled_shows_no_pointer_and_its_reason(self):
        options = [dict(SHELF_OPTIONS[0]), dict(SHELF_OPTIONS[1], disabled=True,
                                                   disabled_reason="Overwrite the Shelf copy cannot be chosen until the driver's upload removes the files an edit deleted.")]
        shelf = dict(SHELF, overwrite_available=False,
                     fields=[dict(SHELF["fields"][0], options=options)])
        routes = self.base_routes()
        routes["GET /api/settings/shelf"] = [{"body": shelf}]
        steps = r"""
        showPane('shelf');
        await waitFor(function () { return qa('[data-vt="shelf-fields"] input[type=radio]').length === 2; });
        var over = q('[data-vt="shelf-fields"] input[value="false"]');
        check(results, 'disabled', over.disabled === true);
        var label = over.closest('label');
        check(results, 'no_pointer', getComputedStyle(label).cursor !== 'pointer', getComputedStyle(label).cursor);
        check(results, 'reason_in_tip', (label.getAttribute('title') || '').indexOf('cannot be chosen until') !== -1, label.getAttribute('title'));
        """
        self.run_steps("shelf_disabled", steps, routes)

    def test_run_switch_confirms_old_and_new_and_a_default_does_not(self):
        routes = self.base_routes()
        ok = {"body": {"store": "voyager_config", "saved_at": "2026-09-27T10:40:00-04:00", "saved_by": "LO", "values": {},
                       "changes": [{"key": "x", "old": 1, "new": 2}]}}
        routes["POST /api/settings/voyager"] = [ok, {"body": RUNS}, ok, {"body": RUNS}]
        steps = r"""
        showPane('runs');
        await waitFor(function () { return qa('[data-vt="runs-fields"] [data-vt-key]').length === 2; });
        check(results, 'v2_chip', vt('runs-v2').hidden === false);
        tick(q('[data-vt-key="voyager1.delete_from_workbench_default"]'), true);
        vt('runs-save').click();
        await waitFor(function () { return calls('POST', '/api/settings/voyager').length === 1; });
        check(results, 'no_dialog_for_default', !dialog());
        var first = calls('POST', '/api/settings/voyager')[0].body;
        check(results, 'default_body', first.confirm === false && first.values['voyager1.delete_from_workbench_default'] === true, JSON.stringify(first));
        await waitFor(function () { return vt('runs-save').disabled === true; });
        await waitFor(function () { return calls('GET', '/api/settings/voyager').length >= 2; });
        await sleep(50);
        tick(q('[data-vt-key="voyager1.run_enabled"]'), false);
        setInput(vt('runs-reason'), 'NAS maintenance');
        vt('runs-save').click();
        check(results, 'dialog', await waitFor(function () { return !!dialog(); }));
        var text = dialog().textContent;
        check(results, 'names_old_and_new', text.indexOf('Run Voyager 1 batches: on to off') !== -1, text);
        q('[data-vt-dialog="ok"]').click();
        await waitFor(function () { return calls('POST', '/api/settings/voyager').length === 2; });
        var second = calls('POST', '/api/settings/voyager')[1].body;
        check(results, 'switch_body', second.confirm === true && second.values['voyager1.run_enabled'] === false &&
              second.reason === 'NAS maintenance', JSON.stringify(second));
        """
        self.run_steps("runs", steps, routes)

    def test_floors_name_the_driver_and_confirm(self):
        routes = self.base_routes()
        routes["POST /api/settings/floors"] = [{"body": {"store": "driver_nas", "saved_at": "2026-09-27T10:50:00-04:00",
                                                         "saved_by": "LO", "values": {}, "note": "The driver reads the new floors at its next start; manual edit pulls, uploads and start overs read them at each copy.",
                                                         "changes": [{"key": "defaults.headroom_factor", "old": 1.1, "new": 1.2}]}},
                                               {"body": FLOORS}]
        steps = r"""
        showPane('floors');
        await waitFor(function () { return qa('[data-vt="floors-fields"] [data-vt-key]').length === 3; });
        check(results, 'driver_line', vt('floors-driver').textContent === 'The driver is not running; its batches read the new floors when it starts.', vt('floors-driver').textContent);
        setInput(q('[data-vt-key="defaults.headroom_factor"]'), '1.2');
        setInput(vt('floors-reason'), 'bigger transects');
        vt('floors-save').click();
        check(results, 'dialog', await waitFor(function () { return !!dialog(); }));
        check(results, 'old_new', dialog().textContent.indexOf('Headroom factor: 1.1 to 1.2') !== -1, dialog().textContent);
        q('[data-vt-dialog="ok"]').click();
        await waitFor(function () { return calls('POST', '/api/settings/floors').length === 1; });
        var b = calls('POST', '/api/settings/floors')[0].body;
        check(results, 'body', b.confirm === true && b.values['defaults.headroom_factor'] === 1.2, JSON.stringify(b));
        await waitFor(function () { return /Saved/.test(vt('floors-result').textContent); });
        check(results, 'note', vt('floors-result').textContent.indexOf('The driver reads the new floors at its next start') !== -1, vt('floors-result').textContent);
        """
        self.run_steps("floors", steps, routes)

    def test_cancel_in_the_dialog_posts_nothing(self):
        steps = r"""
        showPane('floors');
        await waitFor(function () { return qa('[data-vt="floors-fields"] [data-vt-key]').length === 3; });
        setInput(q('[data-vt-key="defaults.min_free_gb_local"]'), '150');
        setInput(vt('floors-reason'), 'r');
        vt('floors-save').click();
        await waitFor(function () { return !!dialog(); });
        q('[data-vt-dialog="cancel"]').click();
        await sleep(100);
        check(results, 'closed', !dialog());
        check(results, 'no_post', calls('POST', '/api/settings/floors').length === 0);
        check(results, 'button_back', vt('floors-save').disabled === false);
        """
        self.run_steps("floors_cancel", steps)


class ReadOnlyAndHistoryPanes(PanesCase):
    def test_history_table_filters_and_problem_lines(self):
        steps = r"""
        showPane('history');
        await waitFor(function () { return qa('[data-vt="history-body"] tr').length === 2; });
        var rows = qa('[data-vt="history-body"] tr');
        check(results, 'newest_first', rows[0].textContent.indexOf('2026-09-27') !== -1 && rows[0].textContent.indexOf('10:12 AST') !== -1 &&
              rows[0].textContent.indexOf('by LO') !== -1, rows[0].textContent);
        check(results, 'reason_as_text', rows[0].innerHTML.indexOf('<b>') === -1 && rows[0].textContent.indexOf('<b>tighter</b>') !== -1);
        check(results, 'label_for_key', rows[0].textContent.indexOf('Scale error limit (mm)') !== -1 && rows[0].textContent.indexOf('scale_error_max_mm') !== -1, rows[0].textContent);
        check(results, 'store_words', rows[0].textContent.indexOf('Manual edit settings') !== -1, rows[0].textContent);
        check(results, 'meta', vt('history-meta').textContent === '2 of 5 lines shown, newest first; 3 more lines match.', vt('history-meta').textContent);
        var problems = vt('history-problems').textContent;
        check(results, 'skipped', problems.indexOf('1 line of the settings history could not be read.') !== -1, problems);
        check(results, 'conflict', problems.indexOf('A conflict copy of the settings history exists at /mnt/rip/vicarius_drive/vicarius/_METADATA/settings_history_dl_Conflict.csv; it is not merged. Compare it by hand.') !== -1, problems);
        check(results, 'clear_hidden', vt('history-clear').hidden === true);
        var store = vt('history-store');
        store.value = 'manual_edit';
        store.dispatchEvent(new Event('change', {bubbles: true}));
        await waitFor(function () { return calls('GET', '/api/settings/history').length >= 2; });
        var last = calls('GET', '/api/settings/history').slice(-1)[0].url;
        check(results, 'filter_query', last.indexOf('store=manual_edit') !== -1, last);
        check(results, 'clear_shown', vt('history-clear').hidden === false);
        vt('history-clear').click();
        await waitFor(function () { return calls('GET', '/api/settings/history').length >= 3; });
        check(results, 'cleared', vt('history-store').value === '' && calls('GET', '/api/settings/history').slice(-1)[0].url.indexOf('store=') === -1);
        """
        self.run_steps("history", steps)

    def test_gpu_locks_modules_and_appearance_read_only(self):
        steps = r"""
        showPane('gpu');
        await waitFor(function () { return qa('[data-vt="gpu-tiers"] .row').length === 2; });
        check(results, 'gpu_meta', vt('gpu-meta').textContent.indexOf('/mnt/rip/vicarius_drive/vicarius/_METADATA/gpu_ladder.yaml') !== -1, vt('gpu-meta').textContent);
        check(results, 'editor_line', vt('gpu-editor-line').hidden === false);
        check(results, 'memory_words', vt('gpu-tiers').textContent.indexOf('starts beside a manual edit window with 24000 MB free on every card and 160 GB of memory available, memory pressure at most 5 percent') !== -1, vt('gpu-tiers').textContent);
        check(results, 'claims', await waitFor(function () { return qa('[data-vt="gpu-claims"] .row').length === 1; }));
        showPane('locks');
        await waitFor(function () { return qa('[data-vt="locks-body"] .row').length === 2; });
        var locked = qa('[data-vt="locks-body"] .row').filter(function (r) { return r.textContent.indexOf('TagFab') !== -1; })[0];
        check(results, 'locked_chip', !!locked && locked.textContent.indexOf('locked') !== -1, locked && locked.textContent);
        check(results, 'no_lock_buttons', qa('[data-vt="locks-body"] button').length === 0);
        showPane('modules');
        check(results, 'modules', await waitFor(function () { return qa('[data-vt="modules-body"] .row').length === 2; }));
        showPane('appearance');
        await waitFor(function () { return qa('[data-vt="appearance-body"] div').length >= 6; });
        var text = vt('appearance-body').textContent;
        check(results, 'appearance', text.indexOf('Pink') !== -1 && text.indexOf('#0a0a0f') !== -1 && text.indexOf('32 px') !== -1, text);
        check(results, 'no_inputs', qa('[data-vt="appearance-body"] input').length === 0);
        """
        self.run_steps("readonly", steps)


class Seam(PanesCase):
    def test_pane_script_first_and_mount_panel_handle(self):
        steps = r"""
        check(results, 'api', typeof window.VoyagerTools.mountPanel === 'function' && typeof window.VoyagerTools.registerPane === 'function');
        var host = q('.win-body');
        var closed = 0;
        var handle = window.VoyagerTools.mountPanel(host, {pane: 'history', onClose: function () { closed += 1; }});
        check(results, 'handle', !!handle && typeof handle.unmount === 'function');
        check(results, 'history_shown', await waitFor(function () { return q('#vt-history').hidden === false; }));
        check(results, 'history_loaded', await waitFor(function () { return qa('[data-vt="history-body"] tr').length === 2; }));
        handle.unmount();
        check(results, 'unmounted', q('.voyager-tools').getAttribute('data-vt-mounted') !== '1');
        var again = window.VoyagerTools.mountPanel(host, {pane: 'gpu', onClose: function () { closed += 1; }});
        check(results, 'remount', await waitFor(function () { return q('#vt-gpu').hidden === false; }));
        again.unmount();
        """
        self.run_steps("seam", steps, settings_first=True, wrap=True)


if __name__ == "__main__":
    unittest.main()
