#!/usr/bin/env python3
"""
Module: tests/test_manual_edit_panes.py
Purpose: Drives the Uploads and Timelines panes of the Atlas control panel
         (static/vt_uploads.js and static/vt_timelines.js over the pane seam,
         program C milestones C7 and C8) in headless Chrome against a stubbed
         fetch, with the harness of tests/test_settings_panes.py: the ready
         list, Tick all, the initials refusal, the confirmation naming each
         transect and its Shelf copy, one POST per press, the progress window
         on the page body through window.ManualEditUpload.mount, Watch, a
         relock that leaves the window open, Escape, a missing component, the
         standalone host with no manual edit module, a 409 line; the
         timelines summary, both tables, the filters and their memory, Clear
         filters, Copy as CSV, the drawer with Escape and focus return, and
         hostile strings shown as text.
Inputs:  the real panel through a Flask test client; the clone's static
         scripts; headless Chrome at /usr/bin/google-chrome.
Outputs: unittest results; every harness file sits in a temporary directory
         removed in tearDown.

Run from the clone root:
    python3 -m unittest tests.test_manual_edit_panes -v
"""
from __future__ import annotations

import copy
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_settings_panes import PanesCase  # noqa: E402

AST = timezone(timedelta(hours=-4))
# An upload set started an hour ago, so it stays inside the pane's 7 days whatever day the suite runs.
AN_HOUR_AGO = (datetime.now(AST) - timedelta(hours=1)).isoformat(timespec="seconds")

JOB_A = "ME-20260925-100000-CC"
JOB_B = "ME-20260926-100000-LO"
UPLOAD_RUNNING = "UP-20260927-110000-LO"


def transect(psx, state, **extra):
    """One transect of a project card (program B's _card shape)."""
    site, number = psx.split("_")[:2]
    out = {"psx": psx, "processing_folder": f"{site}_{number}_3D", "site": site, "site_name": "", "transect": number,
           "state": state, "state_word": "", "round": 0, "upload_id": None, "upload_error": "", "removal_error": ""}
    out.update(extra)
    return out


CARDS = {"jobs": [
    {"job_id": JOB_A, "created_by": "CC", "created_at": "2026-09-25T10:00:00-04:00", "phase": "editing",
     "transects": [transect("MRS_T2_2023_2024.psx", "upload"), transect("MRS_T3_2023_2024.psx", "review", round=1),
                   transect("MRS_T4_2023_2024.psx", "uploading", upload_id=UPLOAD_RUNNING)]},
    {"job_id": JOB_B, "created_by": "LO", "created_at": "2026-09-26T10:00:00-04:00", "phase": "editing",
     "transects": [transect("LBH_T3_2024_2025.psx", "upload", site_name="Lang Bank Red Hind FSA"),
                   transect("LBH_T5_2024_2025.psx", "upload")]}]}
UPLOADS = {"admin": True, "uploads": [
    {"upload_id": UPLOAD_RUNNING, "created_at": AN_HOUR_AGO, "created_by": "LO", "status": "running",
     "origin": "admin", "items": [{}]}]}
MOUNT_STUB = r"""
window.__MOUNTS__ = [];
window.ManualEditUpload = { mount: function (el, o) {
  window.__MOUNTS__.push({ uploadId: o.uploadId, base: o.base, initials: o.getInitials() });
  el.innerHTML = '<h2 id="me-upload-title-' + o.uploadId + '">Upload</h2><button type="button" class="btn" data-upload-action="close">Close</button>';
  el.querySelector('button').addEventListener('click', function () { o.onClose(); });
  return { unmount: function () { window.__UNMOUNTED__ = (window.__UNMOUNTED__ || 0) + 1; } };
} };
window.__BUBBLED_ESCAPES__ = 0;
document.addEventListener('keydown', function (e) { if (e.key === 'Escape') window.__BUBBLED_ESCAPES__ += 1; });
"""
UPLOADS_PRELUDE = r"""
function layer() { return document.querySelector('.vt-upload-layer'); }
function readyBoxes() { return qa('[data-vt="uploads-ready"] [data-vt-up-tick]'); }
function escape(target) { target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); }
"""


class UploadsPane(PanesCase):
    def routes(self, **overrides) -> dict:
        routes = self.base_routes()
        routes["GET /manual-edit/api/jobs?active=1"] = [{"body": CARDS}]
        routes["GET /manual-edit/api/uploads"] = [{"body": UPLOADS}]
        routes["POST /manual-edit/api/uploads"] = [{"status": 202, "body": {"upload_id": "UP-20260927-120000-LO",
                                                                            "status": "queued"}}]
        routes.update(overrides)
        return routes

    def test_tick_confirm_one_post_and_the_progress_window_on_the_body(self):
        steps = UPLOADS_PRELUDE + r"""
        showPane('uploads');
        check(results, 'rows', await waitFor(function () { return readyBoxes().length === 3; }), String(readyBoxes().length));
        var start = vt('uploads-start');
        check(results, 'disabled_at_rest', start.disabled === true);
        check(results, 'meta', vt('uploads-meta').textContent === '3 marked for re-upload, 1 uploading, 1 in admin review.', vt('uploads-meta').textContent);
        check(results, 'site_name', vt('uploads-ready').textContent.indexOf('Lang Bank Red Hind FSA') !== -1);
        readyBoxes()[0].click();
        readyBoxes()[1].click();
        check(results, 'enabled', start.disabled === false);
        start.click(); start.click();
        check(results, 'dialog', await waitFor(function () { return !!dialog(); }));
        var text = dialog().textContent;
        check(results, 'names_each', text.indexOf('Upload 2 transects to the Shelf') !== -1 &&
              text.indexOf('MRS T2 (MRS_T2_2023_2024.psx): the Shelf folder MRS_T2_3D is renamed to MRS_T2_3D_voy1_archive') !== -1 &&
              text.indexOf('Lang Bank Red Hind FSA (LBH) T3') !== -1, text);
        check(results, 'one_shelf_read', calls('GET', '/api/settings/shelf').length === 1);
        q('[data-vt-dialog="ok"]').click();
        await waitFor(function () { return calls('POST', '/manual-edit/api/uploads').length > 0; });
        await sleep(200);
        var posts = calls('POST', '/manual-edit/api/uploads');
        check(results, 'one_post', posts.length === 1, String(posts.length));
        var b = posts[0] && posts[0].body;
        check(results, 'body', b && b.initials === 'LO' && JSON.stringify(b.items) ===
              JSON.stringify([{job_id: 'ME-20260925-100000-CC', psx: 'MRS_T2_2023_2024.psx'},
                              {job_id: 'ME-20260926-100000-LO', psx: 'LBH_T3_2024_2025.psx'}]), JSON.stringify(b));
        check(results, 'window', await waitFor(function () { return window.__MOUNTS__.length === 1; }));
        var m = window.__MOUNTS__[0];
        check(results, 'mounted', m.uploadId === 'UP-20260927-120000-LO' && m.base === '/manual-edit' && m.initials === 'LO', JSON.stringify(m));
        check(results, 'on_body', layer() && layer().parentNode === document.body && !q('#vt-root').contains(layer()));
        check(results, 'dialog_role', layer().querySelector('[role="dialog"]').getAttribute('aria-labelledby') === 'me-upload-title-UP-20260927-120000-LO');
        check(results, 'result', vt('uploads-result').textContent === 'Upload set UP-20260927-120000-LO started with 2 transects.', vt('uploads-result').textContent);
        layer().querySelector('[data-upload-action="close"]').click();
        check(results, 'closed', !layer() && window.__UNMOUNTED__ === 1);
        """
        self.run_steps("up_start", steps, self.routes(), head_js=MOUNT_STUB)

    def test_initials_are_checked_before_anything_is_read_or_posted(self):
        steps = UPLOADS_PRELUDE + r"""
        showPane('uploads');
        await waitFor(function () { return readyBoxes().length === 3; });
        readyBoxes()[0].click();
        vt('uploads-start').click();
        await sleep(150);
        check(results, 'line', vt('uploads-result').textContent === 'Type your initials, two to four letters, at the top of the panel.', vt('uploads-result').textContent);
        check(results, 'no_read', calls('GET', '/api/settings/shelf').length === 0);
        check(results, 'no_post', calls('POST', '/manual-edit/api/uploads').length === 0);
        check(results, 'no_dialog', !dialog());
        """
        self.run_steps("up_initials", steps, self.routes(), head_js=MOUNT_STUB, initials="L0")

    def test_tick_all_running_review_and_watch(self):
        steps = UPLOADS_PRELUDE + r"""
        showPane('uploads');
        await waitFor(function () { return readyBoxes().length === 3; });
        check(results, 'ticks_shown', vt('uploads-ticks').hidden === false);
        vt('uploads-tick-all').click();
        check(results, 'all', readyBoxes().every(function (b) { return b.checked; }) && vt('uploads-start').disabled === false);
        vt('uploads-untick-all').click();
        check(results, 'none', readyBoxes().every(function (b) { return !b.checked; }) && vt('uploads-start').disabled === true);
        check(results, 'running', vt('uploads-running-wrap').hidden === false && vt('uploads-running').textContent.indexOf('UP-20260927-110000-LO') !== -1);
        check(results, 'review', vt('uploads-review-wrap').hidden === false && vt('uploads-review').textContent.indexOf('edit round 2') !== -1);
        check(results, 'review_no_tick', !vt('uploads-review').querySelector('input'));
        check(results, 'recent', vt('uploads-recent').textContent.indexOf('from the Atlas control panel') !== -1);
        vt('uploads-running').querySelector('[data-vt-watch]').click();
        check(results, 'watch', await waitFor(function () { return window.__MOUNTS__.length === 1; }) && window.__MOUNTS__[0].uploadId === 'UP-20260927-110000-LO');
        escape(layer().querySelector('[role="dialog"]'));
        check(results, 'escape_closes', !layer());
        check(results, 'escape_stays', window.__BUBBLED_ESCAPES__ === 0, String(window.__BUBBLED_ESCAPES__));
        """
        self.run_steps("up_lists", steps, self.routes(), head_js=MOUNT_STUB)

    def test_a_relock_leaves_the_progress_window_open(self):
        routes = self.routes()
        routes["GET /manual-edit/api/jobs?active=1"] = [{"body": CARDS}, {"status": 401, "body": {"error": "locked", "need_unlock": True}}]
        steps = UPLOADS_PRELUDE + r"""
        showPane('uploads');
        await waitFor(function () { return readyBoxes().length === 3; });
        vt('uploads-recent').querySelector('[data-vt-watch]').click();
        await waitFor(function () { return window.__MOUNTS__.length === 1; });
        vt('uploads-refresh').click();
        check(results, 'relocked', await waitFor(function () { return q('.vt-stages').hidden === true; }));
        check(results, 'window_open', !!layer() && !window.__UNMOUNTED__);
        """
        self.run_steps("up_relock", steps, routes, head_js=MOUNT_STUB)

    def test_a_refusal_shows_its_line_and_opens_no_window(self):
        line = "MRS_T2_2023_2024.psx of ME-20260925-100000-CC is already in upload set UP-20260927-100000-LO."
        routes = self.routes(**{"POST /manual-edit/api/uploads": [{"status": 409, "body": {"error": line}}],
                                "GET /api/settings/shelf": [{"status": 500, "body": {"error": "unreadable"}}]})
        steps = UPLOADS_PRELUDE + r"""
        showPane('uploads');
        await waitFor(function () { return readyBoxes().length === 3; });
        readyBoxes()[0].click();
        vt('uploads-start').click();
        await waitFor(function () { return !!dialog(); });
        check(results, 'unknown_mode', dialog().textContent.indexOf('as the Shelf copy setting says when the upload starts') !== -1, dialog().textContent);
        q('[data-vt-dialog="ok"]').click();
        check(results, 'line', await waitFor(function () { return vt('uploads-result').textContent.indexOf('already in upload set') !== -1; }), vt('uploads-result').textContent);
        check(results, 'error_style', vt('uploads-result').classList.contains('result-error'));
        check(results, 'no_window', !layer() && window.__MOUNTS__.length === 0);
        check(results, 'enabled_again', vt('uploads-start').disabled === false);
        """
        self.run_steps("up_409", steps, routes, head_js=MOUNT_STUB)

    def test_the_component_missing_says_the_upload_continues(self):
        steps = UPLOADS_PRELUDE + r"""
        showPane('uploads');
        await waitFor(function () { return readyBoxes().length === 3; });
        vt('uploads-recent').querySelector('[data-vt-watch]').click();
        check(results, 'line', await waitFor(function () { return !!layer() && layer().textContent.indexOf('The upload progress window could not load; the upload continues on the server.') !== -1; }, 6000));
        check(results, 'id_chip', layer().textContent.indexOf('UP-20260927-110000-LO') !== -1);
        layer().querySelector('[data-vt-upload-close]').click();
        check(results, 'closed', !layer());
        """
        self.run_steps("up_missing", steps, self.routes())

    def test_the_standalone_host_says_the_module_is_not_mounted(self):
        routes = self.routes(**{"GET /manual-edit/api/jobs?active=1": [{"status": 404, "body": {"error": "Not found"}}]})
        steps = UPLOADS_PRELUDE + r"""
        showPane('uploads');
        check(results, 'line', await waitFor(function () { return vt('uploads-result').textContent === 'The manual edit module is not mounted here.'; }), vt('uploads-result').textContent);
        check(results, 'no_controls', vt('uploads-start').hidden === true && readyBoxes().length === 0);
        """
        self.run_steps("up_standalone", steps, routes)


def row(job_id, psx, state, editor, created_at, **extra):
    """One transect row of the timelines payload (spec 4.12)."""
    site, number = psx.split("_")[:2]
    out = {"job_id": job_id, "psx": psx, "processing_folder": f"{site}_{number}_3D", "readable_ids": [f"{site}_{number}_2023ann"],
           "site": site, "site_name": "", "transect": number, "editor": editor, "round": 0, "state": state,
           "state_word": state, "created_at": created_at, "pulled_at": "2026-09-25T12:00:00-04:00", "editing_s": 97200,
           "review_s": 0, "upload_wait_s": 0, "uploaded_at": None, "metashape_s": 18720, "unknown_opens": 0, "opens": 3,
           "total_s": 172800, "confirmed_at": None, "removed_at": None, "checks_open": False, "upload": None,
           "last_note": ""}
    out.update(extra)
    return out


WORDS = {"todo": "Not opened", "editing": "Editing", "review": "Marked for admin review", "upload": "Marked for re-upload",
         "uploading": "Uploading", "done": "Uploaded"}
TIMELINES = {
    "generated_at": "2026-09-27T12:00:00-04:00",
    "summary": {"projects": 2, "transects": 3, "by_state": {"todo": 0, "editing": 0, "review": 1, "upload": 0,
                                                             "uploading": 1, "done": 1},
                "median_metashape_s": 18720, "completed_last_30_days": 1, "skipped": []},
    "projects": [{"job_id": JOB_B, "created_by": "LO", "created_at": "2026-09-26T10:00:00-04:00", "phase": "editing",
                  "finished_at": None, "completed_at": None, "transects": 2, "uploaded": 0,
                  "by_state": {"review": 1, "uploading": 1},
                  "pull": {"started_at": None, "finished_at": None, "bytes_done": 5, "bytes_total": 20}},
                 {"job_id": JOB_A, "created_by": "CC", "created_at": "2026-09-20T10:00:00-04:00", "phase": "done",
                  "finished_at": "2026-09-22T10:00:00-04:00", "completed_at": "2026-09-23T10:00:00-04:00",
                  "transects": 1, "uploaded": 1, "by_state": {"done": 1}, "pull": {}}],
    "rows": [row(JOB_B, "MRS_T2_2023_2024.psx", "review", "LO", "2026-09-26T10:00:00-04:00", round=1, unknown_opens=2,
                 review_s=75600, site_name="<img src=x onerror=alert(1)>"),
             row(JOB_B, "LBH_T3_2024_2025.psx", "uploading", "LO", "2026-09-26T10:00:00-04:00",
                 upload={"upload_id": UPLOAD_RUNNING, "status": "running", "item_state": "copying", "bytes_done": 100,
                         "bytes_total": 400}),
             row(JOB_A, "MRS_T1_2023_2025.psx", "done", "CC", "2026-09-20T10:00:00-04:00",
                 uploaded_at="2026-09-22T12:00:00-04:00")],
    "states": ["todo", "editing", "review", "upload", "uploading", "done"], "state_words": WORDS}
DETAIL = {"job_id": JOB_B, "psx": "MRS_T2_2023_2024.psx",
          "intervals": {"MRS_T2_2023ann": [{"value": "editing", "start": "2026-09-25T12:00:00-04:00",
                                            "end": "2026-09-26T15:00:00-04:00", "seconds": 97200,
                                            "actor": "manual_edit:LO", "end_actor": "manual_edit:LO", "open": False,
                                            "ended_by": "change", "clock_anomaly": False},
                                           {"value": "review", "start": "2026-09-26T15:00:00-04:00", "end": None,
                                            "seconds": 75600, "actor": "manual_edit:LO", "end_actor": None,
                                            "open": True, "ended_by": None, "clock_anomaly": False}]},
          "notes": [{"note_id": "N-1", "group_id": "G1", "readable_id": "MRS_T2_2023ann", "job_id": JOB_B,
                     "psx": "MRS_T2_2023_2024.psx", "round": "1", "kind": "admin_review", "rule": "",
                     "decision": "return", "text": "<b>Rescale</b> the 2024 chunk.", "by": "LO",
                     "at": "2026-09-26T18:00:00-04:00"}]}
CLIPBOARD_STUB = ("Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: function (t) {"
                  " window.__CLIP__ = t; return Promise.resolve(); } } });")
TL_PRELUDE = r"""
function bodyRows() { return qa('[data-vt="tl-rows"] tr[data-vt-tl-row]'); }
"""


class TimelinesPane(PanesCase):
    def routes(self, **overrides) -> dict:
        routes = self.base_routes()
        routes["GET /voyager-tools/api/timelines"] = [{"body": TIMELINES}]
        routes["GET /voyager-tools/api/timelines/detail"] = [{"body": DETAIL}]
        routes.update(overrides)
        return routes

    def test_summary_tables_filters_csv_and_the_drawer(self):
        steps = TL_PRELUDE + r"""
        try { localStorage.removeItem('vt.timelines.filters'); } catch (e) {}
        showPane('timelines');
        check(results, 'rows', await waitFor(function () { return bodyRows().length === 3; }));
        var summary = vt('tl-summary').textContent;
        check(results, 'summary', summary.indexOf('2 manual edit projects, 3 transects: 1 in admin review, 1 uploading, 1 uploaded; median time in Metashape 5.2 h') === 0, summary);
        check(results, 'summary_bold', vt('tl-summary').querySelectorAll('b').length >= 5);
        check(results, 'projects', qa('[data-vt="tl-projects"] tr').length === 2);
        check(results, 'pull_line', vt('tl-projects').textContent.indexOf('Pulling to the edit bench, 25 percent') !== -1);
        check(results, 'progress', vt('tl-projects').textContent.indexOf('1 of 1 uploaded') !== -1);
        check(results, 'running', vt('tl-rows').textContent.indexOf('Copying, 25 percent') !== -1);
        check(results, 'unknown_opens', vt('tl-rows').textContent.indexOf('and 2 opens with no recorded close') !== -1);
        check(results, 'hostile_as_text', !vt('tl-rows').querySelector('img') && vt('tl-rows').textContent.indexOf('<img src=x') !== -1);
        var states = qa('[data-vt="tl-state"] option').map(function (o) { return o.value; });
        check(results, 'states_present', JSON.stringify(states) === JSON.stringify(['', 'review', 'uploading', 'done']), JSON.stringify(states));
        var select = vt('tl-state'); select.value = 'review'; select.dispatchEvent(new Event('change', {bubbles: true}));
        check(results, 'filtered', bodyRows().length === 1 && vt('tl-count').textContent === '1 of 3 transects', vt('tl-count').textContent);
        check(results, 'remembered', JSON.parse(localStorage.getItem('vt.timelines.filters')).state === 'review');
        check(results, 'clear_shown', vt('tl-clear').hidden === false);
        vt('tl-clear').click();
        check(results, 'cleared', bodyRows().length === 3 && vt('tl-clear').hidden === true);
        vt('tl-csv').click();
        await waitFor(function () { return !!window.__CLIP__; });
        var lines = String(window.__CLIP__).split('\r\n');
        check(results, 'csv', lines.length === 4 && lines[0].indexOf('"Project","Metashape project"') === 0, lines[0]);
        var opener = bodyRows()[0].querySelector('[data-vt-tl-open]');
        opener.click();
        check(results, 'drawer', await waitFor(function () { return vt('tl-drawer').hidden === false && vt('tl-drawer-body').textContent.indexOf('in admin review') !== -1; }));
        var detail = calls('GET', '/voyager-tools/api/timelines/detail')[0];
        check(results, 'detail_query', detail && detail.url.indexOf('job=ME-20260926-100000-LO&psx=MRS_T2_2023_2024.psx') !== -1, detail && detail.url);
        check(results, 'note_as_text', !vt('tl-drawer-body').querySelector('b') && vt('tl-drawer-body').textContent.indexOf('<b>Rescale</b>') !== -1);
        check(results, 'note_words', vt('tl-drawer-body').textContent.indexOf('Admin review, sent back') !== -1);
        check(results, 'focus_close', document.activeElement === q('[data-vt="tl-drawer-close"]'));
        vt('tl-drawer').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        check(results, 'drawer_closed', vt('tl-drawer').hidden === true);
        check(results, 'focus_back', document.activeElement === opener);
        """
        self.run_steps("tl_all", steps, self.routes(), head_js=CLIPBOARD_STUB)

    def test_remembered_filters_apply_on_load_and_bad_ones_are_dropped(self):
        head = "try { localStorage.setItem('vt.timelines.filters', JSON.stringify({state: 'done', editor: '<b>', since: 'x'})); } catch (e) {}"
        steps = TL_PRELUDE + r"""
        showPane('timelines');
        check(results, 'one_row', await waitFor(function () { return bodyRows().length === 1; }), String(bodyRows().length));
        check(results, 'state_kept', vt('tl-state').value === 'done' && vt('tl-editor').value === '' && vt('tl-since').value === '');
        setInput(vt('tl-since'), 'soon');
        check(results, 'bad_day', vt('tl-result').textContent === 'Type the first day as YYYY-MM-DD, for example 2026-09-27.', vt('tl-result').textContent);
        """
        self.run_steps("tl_memory", steps, self.routes(), head_js=head)

    def test_no_project_and_the_module_missing(self):
        empty = copy.deepcopy(TIMELINES)
        empty.update({"summary": {"projects": 0, "transects": 0, "by_state": {}, "median_metashape_s": None,
                                  "completed_last_30_days": 0, "skipped": [{"job_id": "ME-20260926-080000-XY",
                                                                            "error": "not JSON"}]},
                      "projects": [], "rows": []})
        steps = TL_PRELUDE + r"""
        showPane('timelines');
        check(results, 'empty', await waitFor(function () { return vt('tl-summary').textContent.indexOf('No manual edit project recorded yet.') === 0; }));
        check(results, 'skipped_line', vt('tl-result').textContent.indexOf('ME-20260926-080000-XY could not be read: not JSON') !== -1, vt('tl-result').textContent);
        check(results, 'csv_off', vt('tl-csv').disabled === true);
        """
        self.run_steps("tl_empty", steps, self.routes(**{"GET /voyager-tools/api/timelines": [{"body": empty}]}))
        missing = {"GET /voyager-tools/api/timelines": [{"status": 503, "body": {
            "error": "The manual edit module is not mounted here.", "not_mounted": True}}]}
        steps = r"""
        showPane('timelines');
        check(results, 'line', await waitFor(function () { return vt('tl-result').textContent === 'The manual edit module is not mounted here.'; }), vt('tl-result').textContent);
        """
        self.run_steps("tl_missing", steps, self.routes(**missing))


if __name__ == "__main__":
    unittest.main()
