#!/usr/bin/env python3
"""
Module: tests/test_voyager2_tools_js.py
Purpose: Drives the milestone D1i parts of static/voyager_tools.js in headless
         Chrome against a stubbed fetch: the Rewind pane's Before Voyager 2
         target (dry run shape, the VIGIL line and confirm_vigil in the apply
         body, a stale dry run cleared when the target changes), the Spot
         redo pane's Voyager 2 pipeline (plan and apply carry the phase), and
         the Clean workbench pane's never-scanned line and Voyager 2 stage
         list with its Delete wording.
Inputs:  the real panel (test_settings_panes.PanesCase renders it through a
         Flask test client); headless Chrome at /usr/bin/google-chrome.
Outputs: unittest results; every harness file sits in a temporary folder removed in tearDown.

Run from the clone root:
    python3 -m unittest tests.test_voyager2_tools_js -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_settings_panes import PanesCase  # noqa: E402

RID = "MRS_T1_2023ann"
OUT_PATH = "/volume6/Archive8_12TB/driver_deposits/run1/MRS_T1_3doutput"
ROWS = {"rows": [{"readable_id": RID, "site": "MRS", "site_name": "Meri Shoal", "transect": "T1", "year": "2023",
                  "season_token": "ann", "step1_status": "complete", "stage": "done", "manual_edit_status": "done",
                  "manual_edit_under_way": False, "run_state": "", "processing_location": "146.226.147.140:/x",
                  "local": False, "in_job": False, "psx_file": "", "step2_status": "complete"}],
        "targets": [{"value": "before_step1", "label": "before step 1"}, {"value": "before_step0", "label": "before step 0"},
                    {"value": "before_step2", "label": "before Voyager 2"}]}
V2_PLAN = {"readable_id": RID, "target": "before_step2", "target_words": "before Voyager 2", "transect": "MRS_T1",
           "rows": [{"readable_id": RID, "registry_resets": {"step2_status": "complete", "output_location": "x"},
                     "facts_cleared": ["ortho", "run_id"], "console_log": None}],
           "shelf_folder": OUT_PATH, "archive_to": OUT_PATH + "_voy2_archive_20261003-091500",
           "vigil_references": [{"readable_id": RID, "key": "staged_manifest", "value": "/mnt/rip/driver_staging/vigil_stage/b/m.json"}],
           "warnings": [], "empty": False}
V2_MANIFEST = dict(V2_PLAN, archived_to=V2_PLAN["archive_to"], problems=[])
REDO_PLAN = {"readable_id": RID, "transect": "MRS_T1", "rows": [{"readable_id": RID, "step2_finished": "2026-10-02 14:05:00"}]}
WORKBENCH = {
    "roots": ["/mnt/rip/driver_staging"], "problems": [],
    "excluded": [{"label": "Edit bench", "path": "/mnt/rip/driver_staging/editbench"},
                 {"label": "Voyager 2 stage", "path": "/mnt/rip/driver_staging/voyager2_stage"}],
    "folders": [],
    "voyager2_stage": {"root": "/mnt/rip/driver_staging/voyager2_stage", "problems": [], "folders": [
        {"path": "/mnt/rip/driver_staging/voyager2_stage/batch_v2_1/turn001/MRS_T1_3D", "name": "MRS_T1_3D",
         "kind": "working copy", "readable_ids": [RID], "shelf_path": OUT_PATH, "status": "deposited and verified",
         "mismatches": [], "files": 0, "bytes": 0, "deletable": True, "detail": "can go"},
        {"path": "/mnt/rip/driver_staging/voyager2_stage/test_20260927-080000/MRS_T1_3D", "name": "MRS_T1_3D",
         "kind": "working copy", "readable_ids": [], "shelf_path": None, "status": "test run copy",
         "mismatches": [], "files": 0, "bytes": 0, "deletable": False, "detail": "its own clean step removes it"}]}}


class Voyager2Panes(PanesCase):
    def routes(self) -> dict:
        routes = self.base_routes()
        routes["GET /voyager-tools/api/rewind/rows"] = [{"body": ROWS}]
        routes["POST /voyager-tools/api/rewind/plan"] = [{"body": {"ok": True, "plan": V2_PLAN}}]
        routes["POST /voyager-tools/api/rewind/apply"] = [{"body": {"ok": True, "manifest": V2_MANIFEST}}]
        routes["POST /voyager-tools/api/spot-redo/plan"] = [{"body": {"ok": True, "plan": REDO_PLAN}}]
        routes["POST /voyager-tools/api/spot-redo"] = [{"body": {"ok": True, "manifest": dict(REDO_PLAN, redo_requested="requested x by LO")}}]
        routes["GET /voyager-tools/api/workbench"] = [{"body": WORKBENCH}]
        routes["POST /voyager-tools/api/workbench/delete"] = [{"body": {"ok": True, "deleted": "/x", "bytes": 0, "problems": []}}]
        return routes

    def test_rewind_before_voyager2_sends_the_vigil_confirmation(self):
        steps = r"""
        showPane('rewind');
        check(results, 'rows', await waitFor(function () { return vt('rw-row').options.length === 1; }));
        check(results, 'row_label_names_step2', /step 2 complete/.test(vt('rw-row').options[0].textContent), vt('rw-row').options[0].textContent);
        q('input[name="vt-rw-target"][value="before_step2"]').click();
        vt('rw-dry').click();
        check(results, 'planned', await waitFor(function () { return /MRS_T1 before Voyager 2/.test(vt('rw-plan').textContent); }));
        var plan = vt('rw-plan').textContent;
        check(results, 'rename_line', /MRS_T1_3doutput gets _voy2_archive_ and the time of the rename added to its name; nothing is deleted/.test(plan), plan);
        check(results, 'vigil_line', /VIGIL holds MRS_T1_2023ann/.test(plan), plan);
        check(results, 'dry_body', calls('POST', '/voyager-tools/api/rewind/plan')[0].body.target === 'before_step2');
        check(results, 'dry_result', /1 timepoint would be cleared and the Shelf output folder renamed/.test(vt('rw-result').textContent), vt('rw-result').textContent);
        vt('rw-apply').click();
        check(results, 'dialog', await waitFor(function () { return !!dialog(); }));
        var ok = dialog().querySelector('[data-vt-dialog="ok"]');
        check(results, 'ok_label', ok.textContent === 'Rewind anyway', ok.textContent);
        check(results, 'dialog_vigil', /VIGIL still holds its copies of MRS_T1_2023ann on the VIGIL stage/.test(dialog().textContent), dialog().textContent);
        ok.click();
        check(results, 'applied', await waitFor(function () { return /Rewound MRS_T1 before Voyager 2/.test(vt('rw-result').textContent); }));
        var body = calls('POST', '/voyager-tools/api/rewind/apply')[0].body;
        check(results, 'apply_body', body.target === 'before_step2' && body.confirm_vigil === true && body.confirm === true && body.initials === 'LO', JSON.stringify(body));
        """
        self.run_steps("rewind_v2", steps, routes=self.routes())

    def test_a_changed_target_clears_the_dry_run(self):
        steps = r"""
        showPane('rewind');
        await waitFor(function () { return vt('rw-row').options.length === 1; });
        q('input[name="vt-rw-target"][value="before_step2"]').click();
        vt('rw-dry').click();
        await waitFor(function () { return vt('rw-apply').disabled === false; });
        q('input[name="vt-rw-target"][value="before_step1"]').click();
        check(results, 'apply_off', vt('rw-apply').disabled === true);
        check(results, 'plan_gone', vt('rw-plan').textContent === '', vt('rw-plan').textContent);
        """
        self.run_steps("rewind_stale", steps, routes=self.routes())

    def test_spot_redo_in_voyager2_carries_the_phase(self):
        steps = r"""
        showPane('spot');
        await waitFor(function () { return vt('sr-row').options.length === 1; });
        q('input[name="vt-sr-phase"][value="voyager2"]').click();
        vt('sr-dry').click();
        check(results, 'planned', await waitFor(function () { return /MRS_T1 again in Voyager 2/.test(vt('sr-plan').textContent); }));
        check(results, 'plan_body', calls('POST', '/voyager-tools/api/spot-redo/plan')[0].body.phase === 'voyager2');
        vt('sr-apply').click();
        await waitFor(function () { return !!dialog(); });
        check(results, 'dialog', /redo request/.test(dialog().textContent), dialog().textContent);
        dialog().querySelector('[data-vt-dialog="ok"]').click();
        check(results, 'done', await waitFor(function () { return /Redo requested for MRS_T1/.test(vt('sr-result').textContent); }));
        var body = calls('POST', '/voyager-tools/api/spot-redo').filter(function (c) { return !/\/plan$/.test(c.url); })[0].body;
        check(results, 'apply_body', body.phase === 'voyager2' && body.confirm === true, JSON.stringify(body));
        """
        self.run_steps("spot_v2", steps, routes=self.routes())

    def test_workbench_shows_the_skipped_roots_and_the_stage(self):
        steps = r"""
        showPane('workbench');
        vt('wb-scan').click();
        check(results, 'scanned', await waitFor(function () { return vt('wb-v2-body').querySelectorAll('li').length === 2; }));
        check(results, 'excluded', /Never scanned: Edit bench \/mnt\/rip\/driver_staging\/editbench, Voyager 2 stage/.test(vt('wb-excluded').textContent), vt('wb-excluded').textContent);
        check(results, 'root', vt('wb-v2-root').textContent === 'Voyager 2 stage: /mnt/rip/driver_staging/voyager2_stage', vt('wb-v2-root').textContent);
        var buttons = vt('wb-v2-body').querySelectorAll('[data-vt-delete]');
        check(results, 'one_delete', buttons.length === 1, String(buttons.length));
        buttons[0].click();
        await waitFor(function () { return !!dialog(); });
        check(results, 'dialog', /Voyager 2 deposited and verified/.test(dialog().textContent), dialog().textContent);
        dialog().querySelector('[data-vt-dialog="ok"]').click();
        await waitFor(function () { return calls('POST', '/voyager-tools/api/workbench/delete').length === 1; });
        var body = calls('POST', '/voyager-tools/api/workbench/delete')[0].body;
        check(results, 'delete_body', /voyager2_stage\/batch_v2_1\/turn001\/MRS_T1_3D$/.test(body.path) && body.confirm === true, JSON.stringify(body));
        """
        self.run_steps("workbench_v2", steps, routes=self.routes())


if __name__ == "__main__":
    unittest.main()
