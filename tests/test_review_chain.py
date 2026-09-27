#!/usr/bin/env python3
"""
Module: tests/test_review_chain.py
Purpose: The review run end to end over program B's real routes (program C
         spec section 9.3, review end to end, and 9.2, the NAS silent), with
         the Atlas control panel's blueprint mounted beside the manual edit
         blueprint so the Review queue runs its real recovery of stranded
         start overs. The host is program B's own test harness (its
         tests/test_views.py ViewCase: a temp registry root, a local stand-in
         Shelf, a synchronous Deps, the fake project report); nothing here
         reaches the NAS, Metashape or a GPU.
           - Confirm with a failed check puts the transect in admin review;
             the queue lists it; a Send back with instructions through the
             review route moves the registry rows to editing, writes the
             admin_review and return_instructions notes signed with the typed
             initials, and empties the queue.
           - A start over whose free space reading is refused stops in
             restarting with the error; the queue lists it under
             stopped_restarts; with the reading answering again, the resume
             through the review route finishes it and it leaves the list.
           - A start over a desktop restart stranded is marked stopped by the
             queue's read and listed with program B's interruption sentence.
Inputs:  program B's clone beside this one ($VICARIUS_ROOT/modules/manual_edit/
         github_repo), temporary directories only.
Outputs: unittest results; every temporary directory is removed by the
         harness's cleanups.

Run from the clone root:
    python3 -m unittest tests.test_review_chain -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
CLONE = HERE.parent
sys.path.insert(0, str(CLONE))
VICARIUS_ROOT = Path(os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius"))
MANUAL_EDIT_CLONE = VICARIUS_ROOT / "modules" / "manual_edit" / "github_repo"
MANUAL_EDIT_TESTS = MANUAL_EDIT_CLONE / "tests"
# Both clones have a top-level standalone_app.py; program B's harness imports and reloads it by that name.
SHARED_TOP_LEVEL = "standalone_app"
HARNESS_FILE = MANUAL_EDIT_TESTS / "test_views.py"
HARNESS_NAME = "manual_edit_view_harness"   # a unique module name: this clone has its own tests/test_views.py
QUEUE = "/voyager-tools/api/review/queue"
REPORT = "Scale is off on the 2023 chunk; one bar is not enough."
INSTRUCTIONS = "Add a second bar between targets 1 and 4, then run the checks again."
SPACE_REFUSAL = "VICARIUS could not read the free space of the Shelf (ssh gave no answer in 60 s), so nothing was copied."


def load_harness():
    """Program B's view test module (its ViewCase and helpers), loaded under a unique name; None when absent."""
    if not HARNESS_FILE.is_file():
        return None
    if HARNESS_NAME in sys.modules:
        return sys.modules[HARNESS_NAME]
    spec = importlib.util.spec_from_file_location(HARNESS_NAME, HARNESS_FILE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[HARNESS_NAME] = module
    spec.loader.exec_module(module)
    return module


harness = load_harness()
BaseCase = harness.ViewCase if harness is not None else unittest.TestCase

from voyagertools import views as vt_views  # noqa: E402


@unittest.skipIf(harness is None, f"program B's view harness is not at {HARNESS_FILE}")
class ReviewChain(BaseCase):
    """Program B's standalone host with the Atlas control panel's blueprint beside it, in admin mode."""

    def setUp(self):
        """Mount the panel's blueprint before any request, pull one transect, and unlock admin mode."""
        self._use_program_b_standalone_app()
        super().setUp()
        saved = {key: getattr(vt_views.deps, key) for key in vars(vt_views.deps)}
        self.addCleanup(lambda: [setattr(vt_views.deps, key, value) for key, value in saved.items()])
        vt_views.deps.is_locked = lambda: False
        vt_views.deps.site_name = lambda code: {"MRS": "Meri Shoal"}.get(code, "")
        self.app.register_blueprint(vt_views.voyager_tools_bp)
        self.assertIs(vt_views.deps.recover_restarts, vt_views._recover_restarts_default)
        self.record = self.started()
        self.job_id = self.record["job_id"]
        self.local = self.record["rows"][0]["local_path"]
        self.base = f"/manual-edit/api/jobs/{self.job_id}/projects/{self.psx_name}"
        self.admin()

    def _use_program_b_standalone_app(self):
        """Make `import standalone_app` (and its reload) find program B's host for this test, then put back ours.

        This clone's own standalone_app is often imported first by its other
        tests; program B's harness reloads the module by name, which searches
        sys.path, so B's clone goes first on the path while the test runs.
        """
        saved_path, saved_module = list(sys.path), sys.modules.pop(SHARED_TOP_LEVEL, None)

        def restore():
            sys.path[:] = saved_path
            sys.modules.pop(SHARED_TOP_LEVEL, None)
            if saved_module is not None:
                sys.modules[SHARED_TOP_LEVEL] = saved_module
        self.addCleanup(restore)
        sys.path.insert(0, str(MANUAL_EDIT_CLONE))

    # -- helpers borrowed from program B's harness (its project and checks window tests) ------

    project = harness.ProjectRouteTests.project if harness else None
    save_project = harness.ProjectRouteTests.save_project if harness else None
    ready = harness.ChecksWindowRouteTests.ready if harness else None
    begin = harness.ChecksWindowRouteTests.begin if harness else None
    statement = harness.ChecksWindowRouteTests.statement if harness else None

    def queue(self) -> dict:
        """The Review queue payload, 200 asserted."""
        r = self.client.get(QUEUE)
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()

    def confirm_to_review(self, initials="AB"):
        """Mark done with no scale bar on the first chunk (a failed check), explain it, and Confirm to admin review."""
        self.ready()
        report = json.loads(json.dumps(self.report_result))
        report["chunks"][0]["bars"] = 0
        self.report_result = report
        _r, run_id = self.begin(initials)
        r = self.post(self.base + "/done/note", {"initials": initials, "run_id": run_id, "rule": "scale",
                                                 "text": "The second target is under sand."})
        self.assertEqual(r.status_code, 202, r.get_json())
        r = self.post(self.base + "/done/commit", {"initials": initials, "run_id": run_id, "confirmed": True,
                                                   "statement": self.statement("review")})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.project()["state"], "review")

    def review(self, decision, report=REPORT, instructions=INSTRUCTIONS, initials="lo"):
        """POST a decision through program B's review route, as the review content posts it."""
        return self.post(self.base + "/review", {"decision": decision, "report": report, "instructions": instructions,
                                                 "initials": initials, "start_upload": False})

    def statuses(self) -> set:
        """The manual_edit_status of every registry row of the transect."""
        return {self.registry.get(rid)["manual_edit_status"] for rid in self.ids}

    def notes_of(self, kind) -> list:
        """The notes of the transect's first row of one kind."""
        return [note for note in self.registry.notes(self.ids[0]) if note["kind"] == kind]

    # -- section 9.3: the review run end to end ------------------------------------------------

    def test_confirm_to_review_then_a_send_back_empties_the_queue(self):
        self.confirm_to_review()
        self.assertEqual(self.statuses(), {"review"})
        listed = self.queue()
        self.assertEqual([(i["job_id"], i["psx"]) for i in listed["items"]], [(self.job_id, self.psx_name)])
        item = listed["items"][0]
        self.assertEqual((item["site_name"], item["editor"], item["failed_checks"], item["round"]),
                         ("Meri Shoal", "AB", 1, 0))
        fragment = self.client.get(f"/voyager-tools/fragment?tool=review&job={self.job_id}&psx={self.psx_name}")
        self.assertEqual(fragment.status_code, 200, fragment.get_json())
        self.assertIn(f'data-shelf-location="{self.record["rows"][0]["shelf_location"]}"', fragment.get_json()["html"])
        dossier = self.client.get(self.base + "/dossier").get_json()["dossier"]
        self.assertEqual(dossier["decisions"], ["upload", "return", "restart"])
        r = self.review("return")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.statuses(), {"editing"})
        reports, instructions = self.notes_of("admin_review"), self.notes_of("return_instructions")
        self.assertEqual([(n["text"], n["by"], n["decision"]) for n in reports], [(REPORT, "LO", "return")])
        self.assertEqual([(n["text"], n["by"]) for n in instructions], [(INSTRUCTIONS, "LO")])
        after = self.queue()
        self.assertEqual((after["items"], after["stopped_restarts"], after["marked_for_upload"], after["unmatched"]),
                         ([], [], [], []))

    # -- section 9.2: the NAS silent -------------------------------------------------------------

    def test_a_refused_space_reading_stops_the_start_over_and_a_resume_clears_it(self):
        self.confirm_to_review()
        refusal = mock.Mock(ok=False, sentence=SPACE_REFUSAL)
        refusal.to_dict.return_value = {"ok": False}
        with mock.patch.object(self.tr.SpaceAdapter, "require_room", return_value=refusal):
            r = self.review("restart")
        self.assertEqual(r.status_code, 202, r.get_json())
        project = self.project()
        self.assertEqual((project["state"], project["restart"]["stage"], project["restart"]["error"]),
                         ("restarting", "space", SPACE_REFUSAL))
        stopped = self.queue()["stopped_restarts"]
        self.assertEqual([(s["psx"], s["stage"], s["error"]) for s in stopped], [(self.psx_name, "space", SPACE_REFUSAL)])
        dossier = self.client.get(self.base + "/dossier").get_json()["dossier"]
        self.assertEqual(dossier["decisions"], ["restart"])
        first = [n for n in dossier["notes"] if n["kind"] == "admin_review" and n["decision"] == "restart"]
        self.assertEqual([n["text"] for n in first], [REPORT], "the resume can post the first texts again")
        r = self.review("restart")
        self.assertEqual(r.status_code, 202, r.get_json())
        self.assertEqual(self.project()["state"], "editing")
        self.assertEqual(self.queue()["stopped_restarts"], [])
        self.assertEqual(len(self.notes_of("admin_review")), 1, "a resume appends no second report")

    def test_a_start_over_a_desktop_restart_stranded_is_listed_stopped(self):
        self.confirm_to_review()
        path = self.store.job_path(self.job_id)
        data = json.loads(path.read_text())
        data["projects"][self.psx_name].update(state="restarting", restart={
            "at": "2026-09-27T10:15:00-04:00", "by": "LO", "aside": "", "stage": "pulling", "error": ""})
        path.write_text(json.dumps(data))
        # The same process still remembers the Confirm leg that just ended (its settle window), so the
        # recovery rightly waits; a desktop restart is a new process whose leg registry is empty.
        self.assertEqual(self.queue()["stopped_restarts"], [], "a leg that just ended is not called stranded")
        self.app.config[self.views.DEPS_KEY] = self.deps._replace(project_legs=self.tr.ProjectLegRegistry())
        stopped = self.queue()["stopped_restarts"]
        self.assertEqual([(s["psx"], s["stage"], s["error"]) for s in stopped],
                         [(self.psx_name, "pulling", self.tr.LINE_RESTART_INTERRUPTED)])
        self.assertEqual(self.project()["restart"]["error"], self.tr.LINE_RESTART_INTERRUPTED)

    def test_back_to_admin_review_is_offered_by_the_queue_exactly_when_program_b_accepts_it(self):
        self.confirm_to_review()
        r = self.review("upload", instructions="")
        self.assertEqual(r.status_code, 200, r.get_json())
        marked = self.queue()["marked_for_upload"]
        self.assertEqual([m["psx"] for m in marked], [self.psx_name])
        self.assertTrue(self.client.get(self.base + "/dossier").get_json()["dossier"]["can_return_to_review"])
        r = self.post(self.base + "/back-to-review", {"initials": "LO"})
        self.assertEqual(r.status_code, 200, r.get_json())
        listed = self.queue()
        self.assertEqual(([i["psx"] for i in listed["items"]], listed["marked_for_upload"]), ([self.psx_name], []))


if __name__ == "__main__":
    unittest.main()
