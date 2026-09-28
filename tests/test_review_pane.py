#!/usr/bin/env python3
"""
Module: tests/test_review_pane.py
Purpose: Drives the review content of one transect (window.VoyagerTools.
         mountReview, static/vt_review.js) and the Review queue pane of the
         Atlas control panel (program C milestone C6) in headless Chrome
         against a stubbed fetch, with the harness of
         tests/test_settings_panes.py. The review is mounted the way the
         atlas's review sidecar mounts it (the rendered review fragment in a
         440 px host) and the way the queue pane mounts it (under the list,
         signed with the panel's initials). Covered: the review record shown
         read only with hostile text as text, the three radios and the rows
         each shows, local validation before posting, each decision's body,
         the Start over confirmation, the stale re-read, a 409, a locked
         answer with the typed text kept, Open in Metashape, Back to admin
         review, a stopped start over's resume, one post per press, a
         malformed record, the times in editing and in review, earlier rounds
         collapsed, the upload hand-off, and the queue's three lists, the
         unmatched line, Review, Close and the relock.
Inputs:  the real panel and review fragment rendered through Flask; the
         clone's static scripts; headless Chrome at /usr/bin/google-chrome.
Outputs: unittest results; every harness file sits in a temporary directory
         removed in tearDown.

Run from the clone root:
    python3 -m unittest tests.test_review_pane -v
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

from flask import Flask, render_template

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_settings_panes import PanesCase  # noqa: E402
from voyagertools import views  # noqa: E402

JOB = "ME-20260925-100000-CC"
PSX = "MRS_T2_2023_2024.psx"
PROJECT = f"/manual-edit/api/jobs/{JOB}/projects/{PSX}"
IDS = ["MRS_T2_2023ann", "MRS_T2_2024ann"]
BENCH = "/mnt/rip/driver_staging/editbench/MRS_T2_3D"
SHELF = "/volume6/Archive8_12TB/driver_deposits/TCRMP_run/MRS_T2_3D"
MANUAL_EDIT_VERSION = "me-test-version"
SETTINGS = {"scale_error_max_mm": 4.94, "scale_error_max_percent": 0.66, "align_spread_max_mm": 8.65,
            "saved_at": "2026-09-26T21:54:25-04:00", "saved_by": "LO"}
DOSSIER = {
    "job_id": JOB, "psx": PSX, "processing_folder": "MRS_T2_3D", "readable_ids": IDS, "state": "review", "round": 1,
    "editor": "AB",
    "timings": {"edit_seconds": 5400, "unknown_opens": 2, "opens": 3, "admin_seconds": 600, "admin_opens": 1,
                "first_open_at": "2026-09-26T09:00:00-04:00", "last_close_at": "2026-09-27T09:20:00-04:00",
                "confirmed_at": "2026-09-27T09:30:00-04:00",
                "rounds": [{"round": 0, "edit_seconds": 3600, "unknown_opens": 0},
                           {"round": 1, "edit_seconds": 1800, "unknown_opens": 2}]},
    "checks": {"at": "2026-09-27T09:30:00-04:00", "by": "AB", "verdict": "stop", "outcome": "review",
               "reason": "returned", "settings": SETTINGS,
               "items": [{"level": "ok", "rule": "chunks", "line": "Every timepoint has one chunk.", "fixed": False},
                         {"level": "warn", "rule": "scale", "line": "MRS_T2_2023ann has one scale bar.",
                          "note": {"text": "<b>second bar</b> buried\nunder sand", "by": "AB",
                                   "at": "2026-09-27T09:25:00-04:00"}, "fixed": False},
                         {"level": "ok", "rule": "align", "line": "Aligned under the limit.", "fixed": True}]},
    "certification": {"by": "AB", "at": "2026-09-27T09:30:00-04:00", "statement": "I have done every step."},
    "settings": SETTINGS,
    "scale_check": {"chunks": [{"label": "MRS_T2_2023ann", "bars": 1, "error_mm": 2.1, "error_pct": 0.3},
                               {"label": "MRS_T2_2024ann", "bars": 3, "error_mm": 1.2, "error_pct": 0.2}]},
    "align": {"reference": "MRS_T2_2024ann", "chunks": [{"label": "MRS_T2_2023ann", "spread_mm": 4.2}]},
    "rescale": None,
    "notes": [{"note_id": "N1", "group_id": "G1", "round": 0, "kind": "admin_review", "rule": "", "decision": "return",
               "text": "Scale is off.", "by": "LO", "at": "2026-09-26T12:00:00-04:00"},
              {"note_id": "N2", "group_id": "G2", "round": 0, "kind": "return_instructions", "rule": "",
               "decision": "return", "text": "Add a second bar.", "by": "LO", "at": "2026-09-26T12:00:00-04:00"},
              {"note_id": "N3", "group_id": "G3", "round": 1, "kind": "editor_explanation", "rule": "scale",
               "decision": "", "text": "<script data-hostile=\"1\">alert(1)</script>", "by": "AB",
               "at": "2026-09-27T09:25:00-04:00"}],
    "notes_error": "", "history": [], "admin_opened_after_checks": True,
    "on_bench": {"path": BENCH, "exists": True}, "upload_id": None,
    "decisions": ["upload", "return", "restart"], "can_return_to_review": False,
}
INTERVALS = {"job_id": JOB, "psx": PSX, "notes": [], "intervals": {
    IDS[0]: [{"value": "editing", "seconds": 3600, "open": False}, {"value": "review", "seconds": 600, "open": False},
             {"value": "editing", "seconds": 1800, "open": False}, {"value": "review", "seconds": 7200, "open": True}],
    IDS[1]: []}}
SESSION_STUB = r"""
window.__NOTED__ = []; window.__PROMPTS__ = 0; window.__LISTENERS__ = [];
window.VICADMINSESSION = {
  isLockedResponse: function (s, b) { return (s === 401 || s === 403) && !!b && b.need_unlock === true; },
  noteResponse: function (s) { window.__NOTED__.push(s); },
  promptUnlock: function () { window.__PROMPTS__ += 1; },
  onChange: function (fn) { window.__LISTENERS__.push(fn);
    return function () { window.__LISTENERS__ = window.__LISTENERS__.filter(function (f) { return f !== fn; }); }; }
};
window.__MOUNTS__ = [];
window.ManualEditUpload = { mount: function (el, o) {
  window.__MOUNTS__.push({ uploadId: o.uploadId, initials: o.getInitials() });
  el.innerHTML = '<h2 id="me-upload-title-' + o.uploadId + '">Upload</h2>';
  return { unmount: function () {} };
} };
"""
REVIEW_PRELUDE = r"""
function part(name) { return q('[data-review-part="' + name + '"]'); }
function fld(name) { return q('[data-review-field="' + name + '"]'); }
function act(name) { return q('[data-review-action="' + name + '"]'); }
function choose(value) { var r = q('[data-review-field="decision"][value="' + value + '"]'); r.click(); }
function line() { var l = part('line'); return l ? l.textContent : ''; }
function posts(leaf) { return calls('POST', PROJECT + leaf); }
function unlockAll(state) { window.__LISTENERS__.slice().forEach(function (fn) { fn({ unlocked: state }); }); }
var host = document.createElement('div');
host.style.width = '440px';
document.body.appendChild(host);
var handle = null;
window.__DECISIONS__ = []; window.__CLOSED__ = 0;
async function mountFresh() {
  if (handle) handle.unmount();
  window.__CALLS__ = [];
  host.innerHTML = window.__FRAGMENT__;
  handle = VoyagerTools.mountReview(host.querySelector('.vt-review'), { jobId: JOB, psx: PSX,
    onDecision: function (r) { window.__DECISIONS__.push(r); }, onClose: function () { window.__CLOSED__ += 1; } });
  return waitFor(function () { return part('decision') || part('error') || act('unlock'); });
}
function fill(report, instructions, initials) {
  setInput(fld('report'), report);
  if (fld('instructions')) setInput(fld('instructions'), instructions || '');
  if (initials != null) setInput(fld('initials'), initials);
}
function setDossier(list) { window.__ROUTES__['GET ' + PROJECT + '/dossier'] = list; }
"""


def js(value) -> str:
    """A value as JSON safe inside an inline script: "</" escaped so hostile text cannot close the element."""
    return json.dumps(value).replace("</", "<\\/")


def dossier(**changes) -> dict:
    """A copy of the review record with the given keys replaced."""
    out = copy.deepcopy(DOSSIER)
    out.update(changes)
    return out


class ReviewCase(PanesCase):
    """PanesCase plus the rendered review fragment and the routes the review reads."""

    def setUp(self):
        super().setUp()
        app = Flask(__name__)
        app.register_blueprint(views.voyager_tools_bp)
        with app.test_request_context():
            self.fragment = render_template(
                "voyager_tools/review.html", base=views.URL_PREFIX, v=views.ASSET_VERSION,
                manual_edit_version=MANUAL_EDIT_VERSION, job_id=JOB, psx=PSX, processing_folder="MRS_T2_3D",
                shelf_location=SHELF, site="MRS", site_name="", transect="T2", readable_ids=IDS).strip()

    def routes(self, **overrides) -> dict:
        routes = self.base_routes()
        routes["GET " + PROJECT + "/dossier"] = [{"body": {"dossier": DOSSIER}}]
        routes["GET /voyager-tools/api/timelines/detail"] = [{"body": INTERVALS}]
        routes["POST " + PROJECT + "/open"] = [{"body": {"opened": True, "pid": 4321}}]
        routes["POST " + PROJECT + "/review"] = [{"body": {"job": {}, "upload_id": None, "decision_round": 1}}]
        routes["POST " + PROJECT + "/back-to-review"] = [{"body": {"job": {}}}]
        routes.update(overrides)
        return routes

    def run_review(self, name: str, steps: str, routes: dict | None = None, **kwargs) -> dict:
        """Run steps with the review's stubs; the routes are set in the head, escaped, since the
        hostile note text would otherwise close the harness's inline script."""
        head = (SESSION_STUB + "window.__FRAGMENT__ = " + js(self.fragment) + ";"
                + "window.__ROUTES__ = " + js(routes or self.routes()) + ";"
                + f"var JOB = {json.dumps(JOB)}; var PSX = {json.dumps(PSX)}; var PROJECT = {json.dumps(PROJECT)};")
        return self.run_steps(name, REVIEW_PRELUDE + steps, None, head_js=head, **kwargs)


class SidecarReview(ReviewCase):
    def test_the_record_shows_read_only_with_hostile_text_as_text(self):
        steps = r"""
        check(results, 'rendered', await mountFresh());
        var text = host.textContent;
        var want = ['Edit round 2', 'admin review', 'edited by AB', 'passed', 'warned', 'fixed',
                    'MRS_T2_2023ann has one scale bar.', '4.94 mm', '0.66 %', '8.65 mm', '2026-09-26 21:54 AST',
                    '1 h 30 min', 'and 2 opens with no recorded close', 'Edit round 1: 1 h 00 min; Edit round 2: 30 min',
                    '10 min over 1 open', 'Time in editing', '1 h 30 min', '2 h 00 min', '4.2 mm', 'reference',
                    'Opened in Metashape by an admin after the checks', 'On the edit bench: ' + '__BENCH__',
                    'Certified by AB at 2026-09-27 09:30 AST', 'Earlier rounds (1)', 'Sent back to the editor'];
        var missing = want.filter(function (w) { return text.indexOf(w) === -1; });
        check(results, 'texts', missing.length === 0, missing.join(' | '));
        check(results, 'no_zero_round', !/\b[Rr]ound 0\b/.test(text) && text.indexOf('percent') === -1);
        check(results, 'hostile_as_text', !host.querySelector('script, b') && text.indexOf('<script data-hostile="1">') !== -1);
        check(results, 'earlier_collapsed', q('details.vt-review-earlier') && !q('details.vt-review-earlier').open);
        check(results, 'initials_seeded', fld('initials').value === 'LO', fld('initials').value);
        check(results, 'initials_above_open', !!(fld('initials').compareDocumentPosition(act('open')) & Node.DOCUMENT_POSITION_FOLLOWING));
        var radios = qa('[data-review-field="decision"]').map(function (r) { return r.parentNode.textContent.trim(); });
        check(results, 'radios', JSON.stringify(radios) === '["Mark for upload","Send back with instructions","Start over"]', JSON.stringify(radios));
        check(results, 'record_disabled_until_chosen', act('record').disabled === true);
        check(results, 'rows_hidden_until_chosen', q('[data-review-row="start_upload"]').hidden && q('[data-review-row="instructions"]').hidden);
        choose('upload');
        check(results, 'upload_rows', !q('[data-review-row="start_upload"]').hidden && q('[data-review-row="instructions"]').hidden &&
              act('record').disabled === false);
        choose('return');
        check(results, 'return_rows', q('[data-review-row="start_upload"]').hidden && !q('[data-review-row="instructions"]').hidden);
        var untipped = qa('.vt-review button, .vt-review textarea, .vt-review input').filter(function (el) {
          if (el.tagName === 'BUTTON' && ['Cancel', 'Close'].indexOf(el.textContent) !== -1) return false;
          if (el.type === 'radio') return !q('[data-review-row="decision"] .tip');
          return !el.getAttribute('title') && !(el.id && q('label[for="' + el.id + '"] ~ .tip, dt:has(label[for="' + el.id + '"]) .tip'));
        }).map(function (el) { return el.outerHTML.slice(0, 60); });
        check(results, 'tooltips', untipped.length === 0, untipped.join(' | '));
        check(results, 'two_reads', calls('GET', PROJECT + '/dossier').length === 1 && calls('GET', '/voyager-tools/api/timelines/detail').length === 1);
        check(results, 'head_chip_is_the_matrix_violet', !!part('state').querySelector('.chip.stage-review'), part('state').innerHTML);
        var passed = qa('.vt-review-check-head .chip').filter(function (c) { return c.parentNode.textContent.indexOf('passed') !== -1; });
        check(results, 'passed_is_green', passed.length === 2 && passed.every(function (c) { return c.classList.contains('check-passed'); }) &&
              getComputedStyle(passed[0]).backgroundColor === 'rgb(0, 255, 179)', passed.length ? getComputedStyle(passed[0]).backgroundColor : 'none');
        check(results, 'no_violet_role', !host.querySelector('.role-verified'));
        check(results, 'violet_chip_colour', getComputedStyle(part('state').querySelector('.chip')).backgroundColor === 'rgb(165, 107, 255)',
              getComputedStyle(part('state').querySelector('.chip')).backgroundColor);
        check(results, 'current_round_heading', qa('.vt-review-round-head').length === 2 &&
              qa('.vt-review-round-head')[0].textContent === 'Edit round 2', qa('.vt-review-round-head').map(function (h) { return h.textContent; }).join(' | '));
        var admin = qa('.vt-review-kind.is-admin').map(function (k) { return k.textContent; });
        check(results, 'admin_kinds_tinted', JSON.stringify(admin) === '["Admin review","Instructions to the editor"]', JSON.stringify(admin));
        var glyphs = qa('.vt-review .tip').map(function (g) { return g.getAttribute('aria-label'); });
        check(results, 'notes_glyph_has_no_count', glyphs.indexOf('About notes') !== -1 && glyphs.every(function (g) { return !/\d/.test(g); }), glyphs.join(' | '));
        check(results, 'next_step_label', q('[data-review-row="decision"] dt').textContent.indexOf('Next step') === 0 &&
              glyphs.indexOf('About the next step') !== -1 && glyphs.indexOf('About decision') !== -1);
        check(results, 'shelf_attr', q('.vt-review').getAttribute('data-shelf-location') === '__SHELF__');
        """.replace("__BENCH__", BENCH).replace("__SHELF__", SHELF)
        self.run_review("rv_record", steps)

    def test_initials_belong_to_the_browser_session_and_an_old_copy_goes(self):
        """V-claim-audit-10: the review keeps initials in session storage like the other windows; the last
        reviewer's initials left in browser storage are removed and never fill the box."""
        steps = r"""
        check(results, 'old_copy_removed', localStorage.getItem('vt.initials') === null, String(localStorage.getItem('vt.initials')));
        await mountFresh();
        check(results, 'not_prefilled_from_the_old_copy', fld('initials').value !== 'ZZ', fld('initials').value);
        check(results, 'still_gone', localStorage.getItem('vt.initials') === null);
        """
        head_extra = "try { localStorage.setItem('vt.initials', 'ZZ'); } catch (e) {}"
        routes = self.routes()
        head = (head_extra + SESSION_STUB + "window.__FRAGMENT__ = " + js(self.fragment) + ";"
                + "window.__ROUTES__ = " + js(routes) + ";"
                + f"var JOB = {json.dumps(JOB)}; var PSX = {json.dumps(PSX)}; var PROJECT = {json.dumps(PROJECT)};")
        self.run_steps("rv_session_initials", REVIEW_PRELUDE + steps, None, head_js=head, initials=None)

    def test_a_record_with_no_editor_says_so_and_never_names_the_creator(self):
        """V-claim-audit-3: with no certifier and no live opener the review says no editor is on record."""
        routes = self.routes()
        routes["GET " + PROJECT + "/dossier"] = [{"body": {"dossier": dossier(editor="", certification=None)}}]
        steps = r"""
        await mountFresh();
        check(results, 'no_editor_line', part('state').parentNode.textContent.indexOf('no editor on record') !== -1,
              part('state').parentNode.textContent);
        check(results, 'no_edited_by', host.textContent.indexOf('edited by') === -1);
        """
        self.run_review("rv_no_editor", steps, routes)

    def test_local_validation_posts_nothing(self):
        steps = r"""
        await mountFresh();
        choose('upload'); fill('', '', 'LO'); act('record').click();
        check(results, 'report_required', line() === 'Write the admin report before recording a decision.', line());
        fill('Looks right.', '', 'L0'); act('record').click();
        check(results, 'initials', line() === 'Type your initials, two to four letters, for example LO.', line());
        check(results, 'initials_focused', document.activeElement === fld('initials'));
        choose('return'); fill('Scale is off.', '   ', 'LO'); act('record').click();
        check(results, 'instructions_required', line() === 'Write the instructions to the editor for a send back or a start over.', line());
        fill('<img src=x onerror=alert(1)>', new Array(2002).join('x'), 'LO'); act('record').click();
        check(results, 'instructions_long', line() === 'Keep the instructions to the editor to 2,000 characters.', line());
        check(results, 'nothing_posted', posts('/review').length === 0 && calls('GET', PROJECT + '/dossier').length === 1);
        check(results, 'error_colour', part('line').classList.contains('result-error'));
        """
        self.run_review("rv_validate", steps)

    def test_mark_for_upload_with_start_now_hands_the_upload_on(self):
        routes = self.routes()
        routes["POST " + PROJECT + "/review"] = [{"body": {"job": {}, "upload_id": "UP-20260927-103100-LO"}}]
        steps = r"""
        await mountFresh();
        choose('upload'); fill('Looks right.', '', 'lo');
        fld('start_upload').click();
        var versions = [];
        var realOpen = VoyagerTools.openUploadWindow;
        VoyagerTools.openUploadWindow = function (id, o) { versions.push(o.version); return realOpen(id, o); };
        act('record').click(); act('record').click();
        check(results, 'decided', await waitFor(function () { return window.__DECISIONS__.length === 1; }), line());
        var p = posts('/review');
        check(results, 'one_post', p.length === 1, String(p.length));
        var b = p[0] && p[0].body;
        check(results, 'body', b && b.decision === 'upload' && b.report === 'Looks right.' && b.initials === 'LO' &&
              b.start_upload === true && b.instructions === '', JSON.stringify(b));
        var r = window.__DECISIONS__[0];
        check(results, 'result', r.decision === 'upload' && r.upload_id === 'UP-20260927-103100-LO' && r.job_id === JOB && r.psx === PSX, JSON.stringify(r));
        check(results, 'reread', calls('GET', PROJECT + '/dossier').length === 2);
        check(results, 'window', await waitFor(function () { return window.__MOUNTS__.length === 1; }) &&
              window.__MOUNTS__[0].uploadId === 'UP-20260927-103100-LO' && window.__MOUNTS__[0].initials === 'LO', JSON.stringify(window.__MOUNTS__));
        check(results, 'window_on_body', q('.vt-upload-layer') && q('.vt-upload-layer').parentNode === document.body);
        check(results, 'manual_edit_cache_bust', JSON.stringify(versions) === '["__MEV__"]', JSON.stringify(versions));
        check(results, 'uploading_chip', !!part('state').querySelector('.chip.stage-upload'));
        check(results, 'closed_form', act('record').disabled && act('open').disabled && fld('report').disabled);
        check(results, 'state_chip', part('state').textContent === 'uploading to the Shelf', part('state').textContent);
        check(results, 'line', /^Marked for upload by LO at \d{4}-\d{2}-\d{2} \d{2}:\d{2} AST\.$/.test(line()), line());
        check(results, 'remembered_for_the_session', sessionStorage.getItem('vt.initials') === 'LO' &&
              localStorage.getItem('vt.initials') === null, String(localStorage.getItem('vt.initials')));
        act('close').click();
        check(results, 'close', window.__CLOSED__ === 1);
        """.replace("__MEV__", MANUAL_EDIT_VERSION)
        self.run_review("rv_upload", steps, routes)

    def test_send_back_and_start_over(self):
        routes = self.routes()
        routes["POST " + PROJECT + "/review"] = [{"body": {"job": {}, "upload_id": None}},
                                                 {"status": 202, "body": {"job": {}}}]
        steps = r"""
        await mountFresh();
        choose('upload'); fld('start_upload').click();
        choose('return'); fill('Scale is off.', 'Add a second bar between targets 1 and 4.', 'AB');
        act('record').click();
        check(results, 'decided', await waitFor(function () { return window.__DECISIONS__.length === 1; }), line());
        var b = posts('/review')[0].body;
        check(results, 'return_body', b.decision === 'return' && b.instructions === 'Add a second bar between targets 1 and 4.' &&
              b.start_upload === false && b.initials === 'AB', JSON.stringify(b));
        check(results, 'bench_chip', part('state').textContent === 'on the edit bench', part('state').textContent);
        await mountFresh();
        choose('restart'); fill('Start again.', 'Pull a fresh copy and redo the crop.', 'LO');
        act('record').click();
        var confirm = part('confirm');
        check(results, 'asks_first', posts('/review').length === 0 && confirm && !confirm.hidden);
        check(results, 'names_aside', confirm.textContent.indexOf('__BENCH__') !== -1 && confirm.textContent.indexOf('MRS_T2_3D_restart_') !== -1, confirm.textContent);
        check(results, 'names_shelf', confirm.textContent.indexOf('pulls a fresh copy from __SHELF__.') !== -1, confirm.textContent);
        check(results, 'one_action_while_asking', act('record').disabled === true && act('restart-confirm').disabled === false);
        act('restart-cancel').click();
        check(results, 'cancel', part('confirm').hidden && act('record').disabled === false);
        act('record').click(); act('restart-confirm').click();
        check(results, 'restarted', await waitFor(function () { return window.__DECISIONS__.length === 2; }), line());
        check(results, 'restart_body', posts('/review')[0].body.decision === 'restart');
        check(results, 'restart_chip', part('state').textContent === 'starting over', part('state').textContent);
        """.replace("__BENCH__", BENCH).replace("__SHELF__", SHELF)
        self.run_review("rv_return_restart", steps, routes)

    def test_stale_conflict_and_reread_error(self):
        stale = [{"body": {"dossier": DOSSIER}}, {"body": {"dossier": dossier(round=2)}}, {"body": {"dossier": DOSSIER}}]
        steps = r"""
        setDossier(__STALE__);
        await mountFresh();
        choose('upload'); fill('Looks right.', '', 'LO'); act('record').click();
        check(results, 'stale_reload', await waitFor(function () { return act('reload'); }));
        check(results, 'stale_line', line() === 'This transect changed since the review opened; reload it before deciding.', line());
        check(results, 'stale_no_post', posts('/review').length === 0);
        act('reload').click();
        check(results, 'reloaded', await waitFor(function () { return fld('report') && !act('reload'); }));
        check(results, 'draft_kept', fld('report').value === 'Looks right.' &&
              q('[data-review-field="decision"][value="upload"]').checked, fld('report').value);
        setDossier([{ body: { dossier: __DOSSIER__ } }]);
        window.__ROUTES__['POST ' + PROJECT + '/review'] = [{ status: 409, body: { message: 'MRS_T2_2023_2024.psx is not in admin review any more.' } }];
        act('record').click();
        check(results, 'conflict', await waitFor(function () { return act('reload'); }) &&
              line() === 'MRS_T2_2023_2024.psx is not in admin review any more.', line());
        check(results, 'no_decision_call', window.__DECISIONS__.length === 0);
        check(results, 'buttons_back', act('record').disabled === false);
        setDossier([{ status: 404, body: { error: 'No manual edit project ME-x holds that transect.' } }]);
        act('record').click();
        check(results, 'reread_error', await waitFor(function () { return part('line').classList.contains('result-error') &&
              line().indexOf('ME-x') !== -1; }), line());
        check(results, 'record_works_after', act('record').disabled === false);
        setDossier([{ body: { dossier: __DOSSIER__ } }]);
        window.__ROUTES__['POST ' + PROJECT + '/review'] = [{ status: 502, body: {} }];
        act('record').click();
        check(results, 'bare_5xx', await waitFor(function () { return line() === 'The desktop answered 502; nothing on this pane changed.'; }), line());
        """.replace("__STALE__", js(stale)).replace("__DOSSIER__", js(DOSSIER))
        self.run_review("rv_stale", steps)

    def test_open_in_metashape(self):
        steps = r"""
        await mountFresh();
        setInput(fld('initials'), '');
        act('open').click();
        check(results, 'no_initials', posts('/open').length === 0 && part('open-line').classList.contains('is-error') &&
              document.activeElement === fld('initials'));
        setInput(fld('initials'), 'ab');
        act('open').click(); act('open').click();
        check(results, 'opened', await waitFor(function () { return part('open-line').textContent.indexOf('4321') !== -1; }), part('open-line').textContent);
        check(results, 'one_open', posts('/open').length === 1 && posts('/open')[0].body.initials === 'AB', JSON.stringify(posts('/open')));
        check(results, 'enabled_after', act('open').disabled === false);
        window.__ROUTES__['POST ' + PROJECT + '/open'] = [{ status: 409, body: { message: 'A Voyager 1 run holds the card; Metashape waits.' } }];
        act('open').click();
        check(results, 'refusal', await waitFor(function () { return part('open-line').textContent === 'A Voyager 1 run holds the card; Metashape waits.'; }), part('open-line').textContent);
        """
        self.run_review("rv_open", steps)

    def test_a_locked_answer_keeps_the_typed_text(self):
        steps = r"""
        await mountFresh();
        choose('return'); fill('Half written report.', 'Half written instructions.', 'LO');
        setDossier([{ status: 401, body: { error: 'locked', need_unlock: true } }]);
        act('record').click();
        check(results, 'relock', await waitFor(function () { return act('unlock'); }), host.textContent);
        check(results, 'relock_line', host.textContent.indexOf('Admin mode ended. Unlock it to go on reviewing.') !== -1);
        check(results, 'noted', window.__NOTED__.indexOf(401) !== -1);
        act('unlock').click();
        check(results, 'prompted', window.__PROMPTS__ === 1);
        setDossier([{ body: { dossier: __DOSSIER__ } }]);
        unlockAll(true);
        check(results, 'back', await waitFor(function () { return fld('report'); }));
        check(results, 'text_kept', fld('report').value === 'Half written report.' && fld('instructions').value === 'Half written instructions.' &&
              q('[data-review-field="decision"][value="return"]').checked);
        act('record').click();
        check(results, 'posts_after_unlock', await waitFor(function () { return posts('/review').length === 1 && act('close'); }), line());
        setDossier([{ status: 401, body: { error: 'locked', need_unlock: true } }]);
        await mountFresh();
        await waitFor(function () { return act('unlock'); });
        handle.unmount(); handle = null;
        setDossier([{ body: { dossier: __DOSSIER__ } }]);
        var before = window.__CALLS__.length;
        unlockAll(true);
        await sleep(200);
        check(results, 'unmounted_reads_nothing', window.__CALLS__.length === before);
        """.replace("__DOSSIER__", js(DOSSIER))
        self.run_review("rv_locked", steps)

    def test_states_without_a_decision_and_back_to_review(self):
        marked = dossier(state="upload", decisions=[], can_return_to_review=True)
        steps = r"""
        setDossier([{ status: 404, body: { error: 'No manual edit project ME-x holds that transect.' } }]);
        await mountFresh();
        check(results, 'missing', part('error') && part('error').textContent.indexOf('ME-x') !== -1 && act('reload'));
        setDossier([{ body: { dossier: __MARKED__ } }]);
        await mountFresh();
        check(results, 'no_form', !fld('report') && !act('record') && !q('[data-review-field="decision"]'));
        check(results, 'why_line', part('decision').textContent.indexOf('No decision is open: the transect is marked for upload.') !== -1);
        check(results, 'back_button', act('back') && act('back').textContent === 'Back to admin review' && act('back').title.length > 20);
        setDossier([{ body: { dossier: __REVIEW__ } }]);
        act('back').click(); act('back').click();
        check(results, 'moved', await waitFor(function () { return fld('report') && /^Moved back to admin review by LO at /.test(line()); }), line());
        check(results, 'one_move', posts('/back-to-review').length === 1 && posts('/back-to-review')[0].body.initials === 'LO');
        check(results, 'told_host', window.__DECISIONS__.length === 1 && window.__DECISIONS__[0].decision === 'back_to_review' &&
              window.__DECISIONS__[0].upload_id === null, JSON.stringify(window.__DECISIONS__));
        setDossier([{ body: { dossier: __UPLOADING__ } }]);
        await mountFresh();
        check(results, 'uploading_no_back', !act('back') && part('decision').textContent.indexOf('uploading to the Shelf') !== -1);
        setDossier([{ body: { dossier: __MARKED__ } }]);
        window.__ROUTES__['POST ' + PROJECT + '/back-to-review'] = [{ status: 409, body: { message: 'An upload set holds MRS_T2_2023_2024.psx now.' } }];
        await mountFresh();
        act('back').click();
        check(results, 'back_refused', await waitFor(function () { return line() === 'An upload set holds MRS_T2_2023_2024.psx now.'; }), line());
        """.replace("__MARKED__", js(marked)).replace("__REVIEW__", js(DOSSIER)).replace(
            "__UPLOADING__", js(dossier(state="uploading", decisions=[], upload_id="UP-1")))
        self.run_review("rv_states", steps)

    def test_a_stopped_start_over_offers_only_the_resume(self):
        """With the first Start over's texts missing from the notes (its registry write failed), the admin types them."""
        stopped = dossier(state="restarting", decisions=["restart"],
                          restart={"at": "2026-09-27T10:15:00-04:00", "by": "LO", "stage": "pulling",
                                   "aside": BENCH + "_restart_20260927-101500",
                                   "error": "The pull stopped: the edit bench is full."})
        routes = self.routes()
        routes["GET " + PROJECT + "/dossier"] = [{"body": {"dossier": stopped}}]
        routes["POST " + PROJECT + "/review"] = [{"status": 202, "body": {"job": {}}}]
        steps = r"""
        await mountFresh();
        check(results, 'no_radios', !q('[data-review-field="decision"]'));
        check(results, 'resume_button', act('record').textContent === 'Resume start over' && act('record').disabled === false);
        check(results, 'instructions_shown', !q('[data-review-row="instructions"]').hidden && q('[data-review-row="start_upload"]').hidden);
        var status = part('decision').textContent;
        check(results, 'why', status.indexOf('The start over stopped at the pull of the fresh copy. The pull stopped: the edit bench is full.') !== -1 &&
              status.indexOf('The first Start over did not reach the notes; write the report and instructions again.') !== -1, status);
        check(results, 'head_reads_stopped', part('state').textContent === 'start over stopped at the pull of the fresh copy' &&
              !!part('state').querySelector('.chip.role-failed'), part('state').innerHTML);
        fill('Resume.', 'Redo the crop.', 'LO');
        act('record').click();
        var confirm = part('confirm');
        check(results, 'confirm', !confirm.hidden && confirm.textContent.indexOf('resumes the start over of MRS_T2_3D from the pull of the fresh copy') !== -1 &&
              confirm.textContent.indexOf('MRS_T2_3D_restart_20260927-101500') !== -1 && act('restart-confirm').textContent === 'Resume now', confirm.textContent);
        act('restart-confirm').click();
        check(results, 'posted', await waitFor(function () { return posts('/review').length === 1; }) && posts('/review')[0].body.decision === 'restart');
        check(results, 'typed_texts', posts('/review')[0].body.report === 'Resume.' && posts('/review')[0].body.instructions === 'Redo the crop.');
        """
        self.run_review("rv_resume", steps, routes)

    def test_a_resume_posts_the_first_start_over_texts_from_the_notes(self):
        notes = DOSSIER["notes"] + [
            {"note_id": "N4", "group_id": "G4", "round": 1, "kind": "admin_review", "rule": "", "decision": "restart",
             "source_id": f"{JOB}:{PSX}:review:r1:2", "text": "Start again from the Shelf.", "by": "LO",
             "at": "2026-09-27T10:14:00-04:00"},
            {"note_id": "N5", "group_id": "G5", "round": 1, "kind": "return_instructions", "rule": "",
             "decision": "restart", "source_id": f"{JOB}:{PSX}:review:r1:2", "text": "Redo the crop.", "by": "LO",
             "at": "2026-09-27T10:14:00-04:00"}]
        stopped = dossier(state="restarting", decisions=["restart"], notes=notes,
                          restart={"at": "2026-09-27T10:15:00-04:00", "by": "LO", "stage": "space", "aside": "",
                                   "error": "VICARIUS could not read the free space of the NAS, so nothing was copied."})
        routes = self.routes()
        routes["GET " + PROJECT + "/dossier"] = [{"body": {"dossier": stopped}}]
        routes["POST " + PROJECT + "/review"] = [{"status": 202, "body": {"job": {}}}]
        steps = r"""
        await mountFresh();
        check(results, 'no_text_fields', !fld('report') && !fld('instructions'));
        var status = part('decision').textContent;
        check(results, 'kept_line', status.indexOf('VICARIUS resumes with the report and instructions written at the first Start over.') !== -1 &&
              status.indexOf('The start over stopped at the free space check. VICARIUS could not read the free space') !== -1, status);
        act('record').click();
        check(results, 'asks', !part('confirm').hidden && part('confirm').textContent.indexOf('from the free space check') !== -1);
        act('restart-confirm').click();
        check(results, 'posted', await waitFor(function () { return posts('/review').length === 1; }));
        var b = posts('/review')[0].body;
        check(results, 'first_texts', b.decision === 'restart' && b.report === 'Start again from the Shelf.' &&
              b.instructions === 'Redo the crop.' && b.initials === 'LO', JSON.stringify(b));
        """
        self.run_review("rv_resume_kept", steps, routes)

    def test_a_malformed_record_still_renders_and_an_upload_error_says_why(self):
        bad = {"state": "review", "round": "x", "checks": "garbage", "scale_check": {"chunks": "no"}, "align": [],
               "timings": None, "notes": [None, 7, {"kind": "check_note", "text": 5}], "certification": "no",
               "on_bench": 3, "decisions": "upload"}
        routes = self.routes()
        routes["GET /voyager-tools/api/timelines/detail"] = [{"status": 500, "body": {"error": "The registry could not be read."}}]
        steps = r"""
        setDossier([{ body: { dossier: __BAD__ } }]);
        await mountFresh();
        check(results, 'rendered', !!part('decision'), host.textContent);
        check(results, 'no_checks_line', host.textContent.indexOf('No checks are recorded for this round.') !== -1);
        check(results, 'no_form_from_a_bad_list', !act('record'));
        setDossier([{ body: { dossier: __GOOD__ } }]);
        window.__ROUTES__['POST ' + PROJECT + '/review'] = [{ body: { job: {}, upload_id: null,
          upload_error: 'Metashape still holds MRS_T2_2023_2024.psx; close it and start the upload.' } }];
        await mountFresh();
        check(results, 'times_unknown_on_a_failed_read', host.textContent.indexOf('Time in review so far') !== -1);
        choose('upload'); fld('start_upload').click(); fill('Looks right.', '', 'LO');
        act('record').click();
        check(results, 'recorded', await waitFor(function () { return act('close'); }), line());
        check(results, 'upload_error', line().indexOf('Metashape still holds MRS_T2_2023_2024.psx') !== -1, line());
        check(results, 'chip_marked', part('state').textContent === 'marked for upload', part('state').textContent);
        check(results, 'no_window', window.__MOUNTS__.length === 0);
        """.replace("__BAD__", js(bad)).replace("__GOOD__", js(DOSSIER))
        self.run_review("rv_malformed", steps, routes)

    def test_a_request_with_no_answer_ends_in_the_timeout_line(self):
        routes = self.routes()
        routes["POST " + PROJECT + "/review"] = [{"hang": True}]
        steps = r"""
        await mountFresh();
        choose('upload'); fill('Looks right.', '', 'LO');
        act('record').click();
        check(results, 'timeout', await waitFor(function () { return line().indexOf('No answer from the desktop after 30 s') === 0; }, 45000), line());
        check(results, 'reload_offered', !!act('reload'));
        """
        self.run_review("rv_timeout", steps, routes, budget_ms=60000)


QUEUE = {"items": [{"job_id": JOB, "psx": PSX, "processing_folder": "MRS_T2_3D", "readable_ids": IDS, "site": "MRS",
                    "site_name": "", "transect": "T2", "round": 1, "editor": "AB", "state": "review",
                    "confirmed_at": "2026-09-27T09:58:10-04:00", "failed_checks": 1, "warned_checks": 2}],
         "stopped_restarts": [{"job_id": "ME-20260926-100000-LO", "psx": "LBH_T3_2024_2025.psx",
                               "processing_folder": "LBH_T3_3D", "readable_ids": ["LBH_T3_2024ann"], "site": "LBH",
                               "site_name": "Lang Bank Red Hind FSA", "transect": "T3", "round": 2, "stage": "space",
                               "error": "VICARIUS could not read the free space of <b>the NAS</b>, so nothing was copied.",
                               "at": "2026-09-27T10:15:00-04:00"}],
         "marked_for_upload": [{"job_id": JOB, "psx": "MRS_T1_2023_2024.psx", "processing_folder": "MRS_T1_3D",
                                "readable_ids": ["MRS_T1_2023ann"], "site": "MRS", "site_name": "", "transect": "T1",
                                "round": 0, "editor": "CC"}],
         "unmatched": ["MRS_T3_2023ann"], "read_at": "2026-09-27T10:00:00-04:00"}
QUEUE_PRELUDE = r"""
function area() { return q('[data-vt="review-area"]'); }
function openButtons() { return qa('[data-vt-review-open]'); }
"""


class QueuePane(ReviewCase):
    def queue_routes(self, **overrides) -> dict:
        routes = self.routes()
        routes["GET /voyager-tools/api/review/queue"] = [{"body": QUEUE}]
        routes["GET /voyager-tools/fragment?tool=review"] = [{"body": {"html": self.fragment, "mount": "VoyagerTools.mountReview"}}]
        routes.update(overrides)
        return routes

    def test_lists_review_close_and_a_decision_reloads_the_queue(self):
        steps = QUEUE_PRELUDE + r"""
        showPane('review');
        check(results, 'rows', await waitFor(function () { return openButtons().length === 3; }), String(openButtons().length));
        check(results, 'meta', vt('review-meta').textContent === '1 transect in admin review, 1 start over stopped, 1 marked for upload.', vt('review-meta').textContent);
        var items = vt('review-items').textContent;
        check(results, 'item', items.indexOf('Edit round 2') !== -1 && items.indexOf('1 check failed, 2 checks warned') !== -1 &&
              items.indexOf('edited by AB') !== -1 && items.indexOf('confirmed 2026-09-27 09:58 AST') !== -1, items);
        var stopped = vt('review-stopped').textContent;
        check(results, 'stopped', !vt('review-stopped-wrap').hidden && stopped.indexOf('Stopped at the free space check') !== -1 &&
              stopped.indexOf('Lang Bank Red Hind FSA') !== -1 && stopped.indexOf('<b>the NAS</b>') !== -1 &&
              !vt('review-stopped').querySelector('b'), stopped);
        check(results, 'returnable', !vt('review-returnable-wrap').hidden && vt('review-returnable').textContent.indexOf('Marked for upload') !== -1);
        check(results, 'unmatched', vt('review-unmatched').textContent === 'MRS_T3_2023ann reads review in the registry, but no unfinished manual edit project holds it; correct the cell in the atlas table.', vt('review-unmatched').textContent);
        check(results, 'tips', openButtons().every(function (b) { return (b.getAttribute('title') || '').indexOf("Open this transect's checks") === 0; }));
        check(results, 'row_chips', !!vt('review-items').querySelector('.chip.stage-review') && !!vt('review-returnable').querySelector('.chip.stage-upload') &&
              !!vt('review-stopped').querySelector('.chip.role-failed') && !q('#vt-review .role-planned, #vt-review .role-attention'));
        openButtons()[0].click();
        check(results, 'mounted', await waitFor(function () { return area().querySelector('[data-review-part="decision"]'); }), area().textContent);
        check(results, 'fragment_url', calls('GET', '/voyager-tools/fragment?tool=review&job=' + JOB + '&psx=' + PSX).length === 1);
        check(results, 'no_own_initials', !area().querySelector('[data-review-field="initials"]'));
        check(results, 'selected', q('#vt-review .row[aria-current="true"]') !== null);
        check(results, 'head', area().querySelector('[data-vt-review-of="MRS T2"]').textContent === 'Admin review');
        choose('upload'); setInput(fld('report'), 'Looks right.');
        var panelInitials = q('#vt-initials');
        setInput(panelInitials, '');
        act('record').click();
        check(results, 'panel_initials_line', line() === 'Type your initials, two to four letters, at the top of the panel.' &&
              document.activeElement === panelInitials && posts('/review').length === 0, line());
        setInput(panelInitials, 'LO');
        var before = calls('GET', '/voyager-tools/api/review/queue').length;
        act('record').click();
        check(results, 'recorded', await waitFor(function () { return posts('/review').length === 1 && act('close'); }), line());
        check(results, 'panel_initials', posts('/review')[0].body.initials === 'LO', JSON.stringify(posts('/review')[0].body));
        check(results, 'queue_reloaded', await waitFor(function () { return calls('GET', '/voyager-tools/api/review/queue').length > before; }));
        act('close').click();
        check(results, 'closed', area().hidden && area().innerHTML === '' && !q('#vt-review .row[aria-current="true"]'));
        openButtons()[1].click();
        check(results, 'second', await waitFor(function () { return area().querySelector('.vt-review-body [data-review-part]') && area().querySelector('[data-vt-review-of="Lang Bank Red Hind FSA (LBH) T3"]'); }), area().textContent.slice(0, 200));
        q('[data-vt="review-close"]').click();
        check(results, 'close_button', area().hidden);
        """
        self.run_review("q_main", steps, self.queue_routes())

    def test_a_relock_unmounts_the_review_and_the_unlock_reopens_it_with_the_draft(self):
        steps = QUEUE_PRELUDE + r"""
        showPane('review');
        await waitFor(function () { return openButtons().length === 3; });
        openButtons()[0].click();
        await waitFor(function () { return area().querySelector('[data-review-field="report"]'); });
        choose('return'); setInput(fld('report'), 'Draft report.'); setInput(fld('instructions'), 'Draft instructions.');
        unlockAll(false);
        check(results, 'hidden', q('.vt-stages').hidden === true);
        var before = calls('GET', PROJECT + '/dossier').length;
        unlockAll(true);
        check(results, 'reopened', await waitFor(function () { return calls('GET', PROJECT + '/dossier').length > before && fld('report'); }));
        check(results, 'draft', fld('report').value === 'Draft report.' && fld('instructions').value === 'Draft instructions.', fld('report').value);
        """
        self.run_review("q_relock", steps, self.queue_routes())

    def test_the_module_missing_and_an_empty_queue(self):
        empty = {"items": [], "stopped_restarts": [], "marked_for_upload": [], "unmatched": [], "read_at": ""}
        steps = QUEUE_PRELUDE + r"""
        showPane('review');
        check(results, 'empty', await waitFor(function () { return vt('review-items').textContent === 'No transect is in admin review.'; }), vt('review-items').textContent);
        check(results, 'others_hidden', vt('review-stopped-wrap').hidden && vt('review-returnable-wrap').hidden && vt('review-unmatched').hidden);
        window.__ROUTES__['GET /voyager-tools/api/review/queue'] = [{ status: 503, body: { error: 'The manual edit module is not mounted here.', not_mounted: true } }];
        vt('review-refresh').click();
        check(results, 'not_mounted', await waitFor(function () { return vt('review-result').textContent === 'The manual edit module is not mounted here.'; }), vt('review-result').textContent);
        """
        self.run_review("q_empty", steps, self.queue_routes(**{"GET /voyager-tools/api/review/queue": [{"body": empty}]}))


if __name__ == "__main__":
    unittest.main()
