#!/usr/bin/env python3
"""
Module: tests/test_clean_manual_edit_pane.py
Purpose: Drives the Clean manual edit run pane of the Atlas control panel
         (static/voyager_tools.js, tool 3) in headless Chrome against a
         stubbed fetch, with the harness of tests/test_settings_panes.py:
         the On cancel value opens with a capital, puts each kept folder on
         its own short line and says the removal once; a refusal shows in
         warning ink; the confirmation names the folders removed and kept
         and its dismiss reads Keep the project; one POST per confirmed
         press; the result counts edit bench copies and links the cancelled
         project's walkthrough when a folder stayed.
Inputs:  the real panel through a Flask test client; the clone's static
         scripts; headless Chrome at /usr/bin/google-chrome.
Outputs: unittest results; every harness file sits in a temporary directory
         removed in tearDown.

Run from the clone root:
    python3 -m unittest tests.test_clean_manual_edit_pane -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_settings_panes import PanesCase  # noqa: E402

JOB_ID = "ME-20260922-155142-LO"
KEPT = "MRS_T1_3D stays: MRS_T1_2023_2025.psx was saved after the pull."
REFUSED = "MRS_T9_2023_2024.psx is marked for re-upload, so this project cannot be cancelled until it is uploaded or decided."


def summary(job_id, refusal="", targets=("MRS_T7_3D",), kept=(KEPT,)):
    """One project as GET /api/manual-edit lists it."""
    return {"job_id": job_id, "phase": "editing", "created_by": "LO", "created_at": "2026-09-22T15:51:42-04:00",
            "edit_bench": "/mnt/rip/driver_staging/editbench",
            "rows": [{"readable_id": "MRS_T1_2023ann", "site": "MRS", "site_name": "Meri Shoal", "transect": "T1",
                      "local_path": "/mnt/rip/driver_staging/editbench/MRS_T1_3D",
                      "shelf_location": "/volume6/Archive8_12TB/driver_deposits/TCRMP_8sep26_LO_MRS/MRS_T1_3D"}],
            "history": [], "log_tail": [], "prune_targets": list(targets), "prune_kept": list(kept),
            "refusal": refusal, "can_cancel": not refusal}


class CleanManualEditPane(PanesCase):
    """Tool 3 in the panel."""

    def routes(self, jobs, answer=None):
        """The base routes plus the manual edit list and, when given, the cancel's answer."""
        routes = self.base_routes()
        routes["GET /voyager-tools/api/manual-edit"] = [{"body": {"jobs": jobs}}]
        if answer is not None:
            routes["POST /voyager-tools/api/manual-edit/cancel"] = [answer]
        return routes

    def test_the_on_cancel_value_lists_each_kept_folder_and_the_removal_once(self):
        jobs = [summary(JOB_ID), summary("ME-20260927-100000-AB", refusal=REFUSED)]
        steps = r"""
        showPane('manual');
        await waitFor(function () { return qa('.vt-me-project').length === 2; });
        var blocks = qa('.vt-me-project');
        var lines = Array.prototype.slice.call(blocks[0].querySelectorAll('.vt-me-line')).map(function (l) { return l.textContent; });
        check(results, 'capital', lines[0] === 'Removes MRS_T7_3D.', lines[0]);
        check(results, 'kept_line', lines[1] === '__KEPT__', lines[1]);
        check(results, 'once', lines.length === 3 && /in the cancelled project's walkthrough/.test(lines[2]), JSON.stringify(lines));
        var block = blocks[0].textContent;
        check(results, 'no_repeat', block.split('Remove from the edit bench').length === 2, block);
        var refusal = blocks[1].querySelector('.vt-me-refusal');
        check(results, 'refusal_span', !!refusal && refusal.textContent === '__REFUSED__', refusal && refusal.textContent);
        var ink = refusal ? getComputedStyle(refusal).color : '';
        var body = getComputedStyle(blocks[1].querySelector('dt')).color;
        check(results, 'refusal_ink', ink && ink !== body, ink + ' vs ' + body);
        check(results, 'button_off', blocks[1].querySelector('[data-vt-cancel]').disabled === true);
        """.replace("__KEPT__", KEPT).replace("__REFUSED__", REFUSED)
        self.run_steps("me_on_cancel", steps, self.routes(jobs))

    def test_the_dialog_names_the_folders_and_keeps_the_project_on_dismiss(self):
        jobs = [summary(JOB_ID)]
        steps = r"""
        showPane('manual');
        await waitFor(function () { return qa('.vt-me-project').length === 1; });
        q('[data-vt-cancel]').click();
        check(results, 'dialog', await waitFor(function () { return !!dialog(); }));
        var text = dialog().textContent;
        check(results, 'removes', text.indexOf('Removes MRS_T7_3D.') !== -1, text);
        check(results, 'kept', text.indexOf('__KEPT__') !== -1, text);
        var dismiss = q('[data-vt-dialog="cancel"]');
        check(results, 'dismiss_label', dismiss.textContent === 'Keep the project', dismiss.textContent);
        dismiss.click();
        await sleep(100);
        check(results, 'no_post', calls('POST', '/voyager-tools/api/manual-edit/cancel').length === 0);
        """.replace("__KEPT__", KEPT)
        self.run_steps("me_dialog", steps, self.routes(jobs))

    def test_the_result_counts_edit_bench_copies_and_links_the_walkthrough(self):
        jobs = [summary(JOB_ID)]
        answer = {"body": {"ok": True, "job_id": JOB_ID, "phase": "cancelled", "rows_reset": ["MRS_T7_2024ann"],
                           "pruned": ["MRS_T7_3D"], "kept": ["MRS_T1_3D"], "problems": [KEPT], "warnings": [],
                           "walkthrough_url": f"/popout/manual-edit?job={JOB_ID}"}}
        steps = r"""
        showPane('manual');
        await waitFor(function () { return qa('.vt-me-project').length === 1; });
        q('[data-vt-cancel]').click();
        await waitFor(function () { return !!dialog(); });
        q('[data-vt-dialog="ok"]').click();
        await waitFor(function () { return calls('POST', '/voyager-tools/api/manual-edit/cancel').length === 1; });
        await waitFor(function () { return /cancelled/.test(vt('me-result').textContent); });
        var text = vt('me-result').textContent;
        check(results, 'copies', text.indexOf('1 edit bench copy removed, 1 kept.') !== -1, text);
        check(results, 'no_local', text.indexOf('local cop') === -1, text);
        var link = vt('me-result').querySelector('a.vt-link');
        check(results, 'link', !!link && link.getAttribute('href') === '/popout/manual-edit?job=__JOB__', link && link.outerHTML);
        check(results, 'one_post', calls('POST', '/voyager-tools/api/manual-edit/cancel').length === 1);
        """.replace("__JOB__", JOB_ID)
        self.run_steps("me_result", steps, self.routes(jobs, answer))


if __name__ == "__main__":
    unittest.main()
