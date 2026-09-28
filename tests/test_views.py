#!/usr/bin/env python3
"""
Module: tests/test_views.py
Purpose: Route tests for voyagertools.views on a bare Flask app: the lock
         and admin gates on every route, the page and fragment shapes, and
         every tool's read, dry run and apply against a temporary registry
         root, a temporary manual edit job store, a temporary voyagerparams
         checkout with a fake git runner, fake Shelf snapshots and the chunk
         script in fake mode. Also the Markdown renderer, the section slicer
         and the standalone host.
Inputs:  temporary directories only; no NAS, no git, no Metashape, no live file.
Outputs: unittest results; every temporary directory is removed in tearDown.

Run from the clone root:
    python3 -m unittest discover -s tests -q
"""
from __future__ import annotations

import csv
import importlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from flask import Flask

HERE = Path(__file__).resolve().parent
CLONE = HERE.parent
sys.path.insert(0, str(CLONE))
VICARIUS_ROOT = os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius")
REGISTRY_LIB_DIR = os.path.join(VICARIUS_ROOT, "_METADATA", "3d")
MANUAL_EDIT_CLONE = os.path.join(VICARIUS_ROOT, "modules", "manual_edit", "github_repo")
for path in (REGISTRY_LIB_DIR, MANUAL_EDIT_CLONE):
    if path not in sys.path:
        sys.path.insert(0, path)
sys.path.insert(0, str(HERE))
from status_seed import seed_status  # noqa: E402

from manualedit import jobstore  # noqa: E402
from voyagertools import paramsstore as ps  # noqa: E402
from voyagertools import rewind as rw  # noqa: E402
from voyagertools import views  # noqa: E402

# The manual_edit clone (on sys.path above) has a standalone_app.py of its own,
# so this clone's host is loaded by path, never by name.
_spec = importlib.util.spec_from_file_location("voyager_tools_standalone_app", CLONE / "standalone_app.py")
standalone_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(standalone_app)

HOST = "146.226.147.140"
SHELF_PARENT = "/volume6/Archive8_12TB/driver_deposits/run1"
RID_DONE = "MRS_T1_2023ann"
RID_WAIT_A = "MRS_T1_2024_pbl"
RID_WAIT_B = "MRS_T1_2024ann"
RID_LBH = "LBH_T2_2025_pbl"
FOLDER = "MRS_T1_3D"  # the one processing folder of MRS T1, named for site and transect alone

# The grouped pane list of the Atlas control panel (program C spec 3.2 step 3):
# group heading, then the tab keys under it in order. Uploads and Timelines
# lead the Manual edit group (milestones C7 and C8); Review queue joins ahead
# of them in milestone C6.
NAV_GROUPS = [
    ("Records", ["order", "seasons", "prompts"]),
    ("Manual edit", ["review", "uploads", "timelines", "settings", "shelf", "manual"]),
    ("Voyager", ["runs", "params", "rewind", "spot", "workbench"]),
    ("Desktop", ["locks", "modules", "appearance", "floors", "gpu"]),
    ("History", ["history"]),
]
ALL_TABS = [key for _group, keys in NAV_GROUPS for key in keys]
# The panel's scripts in load order: the pane seam first, then one script per
# group of panes (program C spec 5.3).
PANEL_SCRIPT_NAMES = ["voyager_tools.js", "vt_settings.js", "vt_uploads.js", "vt_timelines.js", "vt_review.js"]
PANEL_SCRIPT_URLS = ["/voyager-tools/static/" + name + "?v=" + views.ASSET_VERSION for name in PANEL_SCRIPT_NAMES]

SEED_V1 = """# params_version: 1.0.0
# phase: voyager1
# kind: version
# description: seed for tests
# saved_at: 2026-09-04T00:00:00-04:00
# saved_by: LO
project:
  name: "base run"
  notes: ""
processing:
  tcrmp: true
  frames_per_transect: 1000 # frames
  use_gpu: true
  metashape:
    defaults:
      downscale: 1
  model_processing:
    scale_error_threshold: 0.009
"""
SEED_V2 = """# params_version: 0.1.0
# phase: voyager2
# kind: version
# status: draft
project:
  name: "base run"
"""


class FakeGit:
    """Records every git call and answers like a checkout without a remote."""

    def __init__(self):
        self.calls = []

    def __call__(self, args, cwd):
        self.calls.append(list(args))
        if args[:2] == ["rev-parse", "--short"]:
            return ps.GitResult(0, "abc1234\n", "")
        if args[:1] == ["remote"]:
            return ps.GitResult(0, "", "")
        return ps.GitResult(0, "", "")


def seed_checkout(root: Path) -> None:
    """A compact voyagerparams checkout for both phases."""
    for phase, seed, version in (("voyager1", SEED_V1, "v1.0.0"), ("voyager2", SEED_V2, "v0.1.0")):
        (root / phase / "versions").mkdir(parents=True)
        (root / phase / "branches").mkdir()
        (root / phase / "custom").mkdir()
        (root / phase / "versions" / f"{version}.yaml").write_text(seed, encoding="utf-8")
        (root / phase / "DEFAULT").write_text(version + "\n", encoding="utf-8")


class ToolsCase(unittest.TestCase):
    """A bare app with the blueprint and every seam pointed at temp roots."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="vt-views-"))
        self.reg_root = self.root / "registry"
        self.reg_root.mkdir()
        os.environ["VICARIUS_3D_REGISTRY_ROOT"] = str(self.reg_root)
        import registry
        self.registry = importlib.reload(registry)
        self.workbench = self.root / "workbench"
        self.workbench.mkdir()
        self.folder = self.workbench / "manual" / FOLDER
        (self.folder / "frames" / RID_DONE).mkdir(parents=True)
        (self.folder / "reports").mkdir()
        (self.folder / "reports" / f"{RID_DONE}_step1.pdf").write_bytes(b"%PDF")
        (self.folder / "frames" / RID_DONE / "f.tiff").write_bytes(b"t")
        self.psx = self.folder / "MRS_T1_2023_2023.psx"
        self.psx.write_text(json.dumps({"chunks": [RID_DONE]}))
        with open(self.folder / "status.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["Model ID", "Status", "Step 1 complete"])
            w.writeheader()
            w.writerow({"Model ID": RID_DONE, "Status": "Complete", "Step 1 complete": "True"})
        self.shelf = f"{HOST}:{SHELF_PARENT}/{FOLDER}"
        base = {"site": "MRS", "transect": "T1", "processing_folder": FOLDER}
        self.registry.upsert(RID_DONE, dict(base, year="2023", season_token="ann", step1_status="complete",
                                            stage="done", step="1", psx_file=str(self.psx),
                                            processing_location=str(self.folder), manual_edit_status="awaiting",
                                            faces_delivery="100"), "seed")
        self.registry.upsert(RID_WAIT_A, dict(base, year="2024", season_token="_pbl"), "seed")
        self.registry.upsert(RID_WAIT_B, dict(base, year="2024", season_token="ann"), "seed")
        self.registry.upsert(RID_LBH, {"site": "LBH", "transect": "T2", "year": "2025", "season_token": "_pbl"}, "seed")

        self.checkout = self.root / "voyagerparams"
        seed_checkout(self.checkout)
        self.git = FakeGit()
        self.store = ps.ParamsStore(self.checkout, run_git=self.git, now=lambda: "2026-09-04T01:00:00-04:00")
        self.jobs = jobstore.JobStore(str(self.reg_root))
        self.bench = self.root / "editbench"
        self.bench.mkdir()
        self.prompts = self.root / "prompts.md"
        self.prompts.write_text("# Guide\n\nRun `atlascatalog.py --dry-run` first.\n\n- one\n- two\n", encoding="utf-8")
        self.remote = {}
        self.log_rows = []
        self.locked = False

        self._saved = {k: getattr(views.deps, k) for k in vars(views.deps)}
        d = views.deps
        d.is_locked = lambda: self.locked
        d.job_store = lambda: self.jobs
        d.params_store = lambda: self.store
        d.nas_config = lambda: {"defaults": {"workbench_root": str(self.workbench)}}
        d.workbench_roots = lambda: [str(self.workbench)]
        d.remote_snapshot = lambda path: dict(self.remote.get(path, {}))
        d.append_processing_log = self.log_rows.append
        d.run_chunk = lambda psx, label, out_dir: rw.run_chunk_script(psx, label, out_dir, fake=True)
        d.site_name = lambda code: {"MRS": "Meri Shoal", "LBH": "Lang Bank Red Hind FSA"}.get(code, "")
        d.prompts_doc = lambda: self.prompts
        d.now_iso = lambda: "2026-09-04T01:00:00-04:00"
        self.history_lines = []
        d.record_setting = lambda *line: self.history_lines.append(line) or "2026-09-04T01:00:00-04:00"

        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(views.voyager_tools_bp)
        self.app = app
        self.client = app.test_client()

    def tearDown(self):
        for key, value in self._saved.items():
            setattr(views.deps, key, value)
        os.environ.pop("VICARIUS_3D_REGISTRY_ROOT", None)
        shutil.rmtree(self.root, ignore_errors=True)
        import registry
        importlib.reload(registry)

    def unlock(self):
        with self.client.session_transaction() as sess:
            sess["admin_unlocked"] = True

    def routes(self):
        """(rule, method) for every blueprint route except the static files."""
        out = []
        for rule in self.app.url_map.iter_rules():
            if not rule.endpoint.startswith("voyager_tools.") or rule.endpoint.endswith(".static"):
                continue
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                out.append((rule.rule, method))
        return out

    def call(self, rule, method, **kwargs):
        path = rule.replace("<readable_id>", RID_DONE)
        return getattr(self.client, method.lower())(path, **kwargs)


def _walk(path):
    """A local snapshot shaped like driver.verify.local_snapshot."""
    out = {}
    for root, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(root, name)
            out[os.path.relpath(full, path)] = {"kind": "f", "size": os.path.getsize(full)}
    return out


class Gates(ToolsCase):
    def test_every_route_answers_401_without_admin(self):
        routes = self.routes()
        self.assertGreaterEqual(len(routes), 20)
        for rule, method in routes:
            with self.subTest(rule=rule, method=method):
                r = self.call(rule, method, json={})
                self.assertEqual(r.status_code, 401)
                body = r.get_json()
                self.assertEqual(body["error"], "locked")
                self.assertIs(body["need_unlock"], True)

    def test_every_route_answers_423_when_locked_even_for_admin(self):
        self.unlock()
        self.locked = True
        for rule, method in self.routes():
            with self.subTest(rule=rule, method=method):
                r = self.call(rule, method, json={})
                if rule == "/voyager-tools/":
                    self.assertEqual(r.status_code, 423)
                    body = r.get_data(as_text=True)
                    self.assertIn(views.LOCK_MESSAGE.split(". Email")[0], body)
                    self.assertIn("mailto:lauren.olinger@uvi.edu", body)
                    self.assertIn("Email Lauren", body)
                elif rule == "/voyager-tools/fragment":
                    self.assertEqual(r.status_code, 200)
                    self.assertEqual(r.get_json()["ui_style"], "locked")
                    self.assertTrue(r.get_json()["locked"])
                else:
                    self.assertEqual(r.status_code, 423)
                    self.assertEqual(r.get_json()["error"], "locked")

    def test_page_and_fragment_with_admin(self):
        self.unlock()
        r = self.client.get("/voyager-tools/")
        self.assertEqual(r.status_code, 200)
        page = r.get_data(as_text=True)
        self.assertIn("Lauren Olinger", page)
        self.assertIn('class="voyager-tools"', page)
        self.assertEqual(page.count('role="tab"'), len(ALL_TABS))
        self.assertEqual(page.count('role="tabpanel"'), len(ALL_TABS))
        self.assertNotIn("UVI", page)
        # The cache-bust literal is hand-written in panel.html (data-vt-version)
        # as well as read from views.ASSET_VERSION; the two must move together
        # or the desktop path (which reads data-vt-version, not the Python
        # constant, to version the injected stylesheet) serves a stale asset.
        self.assertIn('data-vt-version="' + views.ASSET_VERSION + '"', page)
        r = self.client.get("/voyager-tools/fragment")
        self.assertEqual(r.status_code, 200)
        payload = r.get_json()
        self.assertEqual(payload["ui_style"], "voyager_tools")
        self.assertTrue(payload["html"].startswith('<section class="voyager-tools"'))
        self.assertTrue(payload["html"].rstrip().endswith("</section>"))
        self.assertIn("Lauren Olinger", payload["html"])
        self.assertEqual(payload["scripts"], PANEL_SCRIPT_URLS)
        self.assertEqual(payload["styles"], ["/voyager-tools/static/voyager_tools.css?v=" + views.ASSET_VERSION])
        self.assertEqual(payload["base"], "/voyager-tools")
        self.assertEqual(payload["mount"], "VoyagerTools.mount")
        self.assertEqual(payload["module"], {"name": "voyager_tools", "display_name": "Atlas control panel"})
        self.assertEqual(views.fragment_status(payload), 200)

    def test_fragment_without_admin_carries_the_admin_body_and_401(self):
        r = self.client.get("/voyager-tools/fragment")
        self.assertEqual(r.status_code, 401)
        payload = r.get_json()
        self.assertTrue(payload["need_unlock"])
        self.assertTrue(payload["admin_required"])
        self.assertIn("Admin mode is off. Unlock admin to use the Atlas control panel.", payload["html"])
        self.assertEqual(views.fragment_status(payload), 401)

    def test_static_files_served(self):
        for name in PANEL_SCRIPT_NAMES + ["voyager_tools.css"]:
            r = self.client.get(f"/voyager-tools/static/{name}")
            self.assertEqual(r.status_code, 200, name)
        js = self.client.get("/voyager-tools/static/voyager_tools.js").get_data(as_text=True)
        self.assertIn("window.VoyagerTools", js)
        self.assertIn("mount", js)

    def test_no_tooltip_opens_with_you(self):
        self.unlock()
        page = self.client.get("/voyager-tools/").get_data(as_text=True)
        import re
        titles = re.findall(r'title="([^"]*)"', page)
        self.assertGreater(len(titles), 25)
        for title in titles:
            self.assertFalse(title.startswith("You "), title)
            self.assertNotIn("above", title.lower().split())
            self.assertNotIn("this tab", title.lower())

    def test_state(self):
        self.unlock()
        r = self.client.get("/voyager-tools/api/state")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertFalse(data["order"]["locked"])
        self.assertEqual(data["manual_edit"], [])
        self.assertEqual(data["params_default"], {"voyager1": "v1.0.0", "voyager2": "v0.1.0"})
        self.assertEqual(data["workbench_roots"], [str(self.workbench)])

    def test_bad_bodies(self):
        self.unlock()
        r = self.client.post("/voyager-tools/api/order", data="not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("JSON object", r.get_json()["error"])
        r = self.client.post("/voyager-tools/api/order", json=["list"])
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/voyager-tools/api/order", json={"transects": [], "initials": "L O"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("initials", r.get_json()["error"])
        r = self.client.post("/voyager-tools/api/order", data="x" * (views.MAX_BODY_BYTES + 10),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)


class OrderRoutes(ToolsCase):
    def test_get_save_lock_unlock(self):
        self.unlock()
        r = self.client.get("/voyager-tools/api/order")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual([b["key"] for b in data["blocks"]], ["LBH_T2", "MRS_T1"])
        self.assertEqual(data["blocks"][1]["site_name"], "Meri Shoal")
        self.assertEqual([s["readable_id"] for s in data["started"]], [RID_DONE])
        self.assertIsNone(data["lock"])
        r = self.client.post("/voyager-tools/api/order", json={"transects": ["MRS_T1", "LBH_T2"], "initials": "lo"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["ordered_ids"], [RID_DONE, RID_WAIT_A, RID_WAIT_B, RID_LBH])
        self.assertEqual(r.get_json()["count"], 4)
        self.assertEqual(self.registry.processing_order()[0]["set_by"], "voyager_tools:LO")
        r = self.client.post("/voyager-tools/api/order/lock", json={"initials": "LO"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/voyager-tools/api/order/lock", json={"initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["lock"]["locked_by"], "voyager_tools:LO")
        self.assertEqual(r.get_json()["first"], RID_DONE)
        self.assertEqual(r.get_json()["last"], RID_LBH)
        r = self.client.post("/voyager-tools/api/order", json={"transects": ["LBH_T2", "MRS_T1"], "initials": "LO"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("locked by voyager_tools:LO", r.get_json()["error"])
        r = self.client.post("/voyager-tools/api/order/lock", json={"initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 409)
        r = self.client.post("/voyager-tools/api/order/unlock", json={"initials": "LO"})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.get_json()["lock"])
        r = self.client.post("/voyager-tools/api/order/unlock", json={"initials": "LO"})
        self.assertEqual(r.status_code, 409)

    def test_bad_transect_lists(self):
        self.unlock()
        for body in ({"transects": ["MRS_T1"], "initials": "LO"}, {"transects": "MRS_T1", "initials": "LO"},
                     {"transects": ["MRS_T1", "LBH_T2", "ZZ_T1"], "initials": "LO"}, {"initials": "LO"}):
            r = self.client.post("/voyager-tools/api/order", json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertTrue(r.get_json()["error"])


class WorkbenchRoutes(ToolsCase):
    def setUp(self):
        super().setUp()
        views.deps.local_snapshot = _walk
        self.registry.upsert(RID_DONE, {"processing_location": self.shelf}, "seed")
        self.shelf_path = f"{SHELF_PARENT}/{FOLDER}"

    def test_plan_and_delete(self):
        self.unlock()
        self.remote[self.shelf_path] = _walk(str(self.folder))
        r = self.client.get("/voyager-tools/api/workbench")
        self.assertEqual(r.status_code, 200)
        folder = r.get_json()["folders"][0]
        self.assertEqual(folder["status"], "identical")
        self.assertTrue(folder["deletable"])
        # The three MRS T1 timepoints share the processing folder, so all three name it.
        self.assertEqual(folder["readable_ids"], [RID_DONE, RID_WAIT_A, RID_WAIT_B])
        r = self.client.post("/voyager-tools/api/workbench/delete", json={"path": str(self.folder), "initials": "LO"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/voyager-tools/api/workbench/delete",
                             json={"path": str(self.folder), "initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertFalse(self.folder.exists())
        self.assertEqual(len(self.log_rows), 1)
        self.assertEqual(self.log_rows[0]["module"], "voyager_tools")
        self.assertIn(RID_DONE, self.log_rows[0]["purpose"])
        self.assertEqual(self.log_rows[0]["destination_uri"], self.shelf_path)

    def test_differs_and_outside_root_refused(self):
        self.unlock()
        self.remote[self.shelf_path] = {"other": {"kind": "f", "size": 1}}
        r = self.client.post("/voyager-tools/api/workbench/delete",
                             json={"path": str(self.folder), "initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 409)
        self.assertTrue(self.folder.exists())
        outside = self.root / "ZZZ_T1_3D"
        outside.mkdir()
        r = self.client.post("/voyager-tools/api/workbench/delete",
                             json={"path": str(outside), "initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(outside.exists())
        r = self.client.post("/voyager-tools/api/workbench/delete",
                             json={"path": "../../etc", "initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 400)

    def test_no_roots_configured(self):
        self.unlock()
        views.deps.workbench_roots = lambda: []
        r = self.client.get("/voyager-tools/api/workbench")
        self.assertEqual(r.status_code, 200)
        self.assertIn("No Workbench root", r.get_json()["problems"][0])


class ManualEditRoutes(ToolsCase):
    """Tool 3 over HTTP: the summary and Cancel and prune, through the manual edit module's one cancel."""

    def setUp(self):
        """Point the panel at a manual edit Deps kit of its own, as the desktop that mounts both would."""
        super().setUp()
        from manualedit import transfers, watcher
        self.transfers = transfers
        self.me_deps = transfers.Deps(
            ssh_factory=None, transfer_fn=None, remote_spec_fn=None, archive_fn=None, launch_fn=None,
            run_async=None, watchers=watcher.WatcherRegistry(poll_s=60), site_names_fn=dict, snapshot_fn=None,
            metashape_executable_fn=None, legs=transfers.LegRegistry(), project_legs=transfers.ProjectLegRegistry())
        views.deps.manual_edit_deps = lambda: self.me_deps

    def make_job(self):
        local = self.bench / FOLDER
        job = self.jobs.create_job(
            [{"readable_id": RID_DONE, "site": "MRS", "transect": "T1", "processing_folder": FOLDER,
              "shelf_location": f"{SHELF_PARENT}/{FOLDER}", "psx_basename": "MRS_T1_2023_2023.psx"}],
            str(self.bench), "LO")
        local.mkdir()
        (local / "MRS_T1_2023_2023.psx").write_bytes(b"p")
        seed_status(self.registry, RID_DONE, "editing", processing_location=str(local))
        return job, local

    def cancel(self, **body):
        """POST the cancel route with a confirmed body plus `body`."""
        return self.client.post("/voyager-tools/api/manual-edit/cancel", json=dict({"initials": "LO", "confirm": True},
                                                                                  **body))

    def second_project(self, first):
        """A second unfinished project on another transect (RID_LBH, its own bench folder); the job store's
        own active_jobs() lists both."""
        folder = "LBH_T2_3D"
        (self.bench / folder).mkdir()
        rows = [{"readable_id": RID_LBH, "site": "LBH", "transect": "T2", "processing_folder": folder,
                 "shelf_location": f"{SHELF_PARENT}/{folder}", "local_path": str(self.bench / folder),
                 "psx_basename": "LBH_T2_2025_2025.psx"}]
        record = dict(first, job_id="ME-20260927-020000-CC", created_by="CC", projects=jobstore.build_projects(rows),
                      rows=rows)
        self.jobs.save_job(record)
        seed_status(self.registry, RID_LBH, "editing")
        views.deps.job_store = lambda: self.jobs
        return record, self.jobs

    def test_no_job(self):
        self.unlock()
        self.assertEqual(self.client.get("/voyager-tools/api/manual-edit").get_json(), {"jobs": []})
        r = self.cancel(job_id="ME-20260101-000000-ZZ")
        self.assertEqual(r.status_code, 409)
        self.assertIn("is not an unfinished manual edit project", r.get_json()["error"])

    def test_cancel_names_the_project(self):
        self.unlock()
        job, local = self.make_job()
        self.jobs.transition(job["job_id"], "pulling", "LO")
        self.jobs.transition(job["job_id"], "editing", "LO")
        for bad in (None, "", "   ", 7, ["x"], "x" * 200, "ME-2026-01-LO", "../" + job["job_id"],
                    "ME-20260101-000000-" + "A" * 40):
            with self.subTest(job_id=bad):
                r = self.cancel(**({} if bad is None else {"job_id": bad}))
                self.assertEqual(r.status_code, 400)
                self.assertTrue(local.exists())
        self.assertEqual(self.registry.get(RID_DONE)["manual_edit_status"], "editing")

    def test_summary_and_cancel(self):
        self.unlock()
        job, local = self.make_job()
        self.jobs.transition(job["job_id"], "pulling", "LO")
        self.transfers.legs_of(self.me_deps).begin(job["job_id"])
        r = self.client.get("/voyager-tools/api/manual-edit")
        summary = r.get_json()["jobs"][0]
        self.assertEqual(summary["phase"], "pulling")
        self.assertFalse(summary["can_cancel"])
        self.assertIn("still copying", summary["refusal"])
        r = self.cancel(job_id=job["job_id"])
        self.assertEqual(r.status_code, 409)
        self.assertTrue(local.exists())
        self.transfers.legs_of(self.me_deps).end(job["job_id"])
        self.jobs.transition(job["job_id"], "editing", "LO", self.transfers.PULL_VERIFIED_REASON)
        self.jobs.append_log(job["job_id"], "pulled")
        summary = self.client.get("/voyager-tools/api/manual-edit").get_json()["jobs"][0]
        self.assertTrue(summary["can_cancel"])
        self.assertEqual(summary["rows"][0]["site_name"], "")
        self.assertTrue(summary["log_tail"][0].endswith("pulled"))
        r = self.client.post("/voyager-tools/api/manual-edit/cancel", json={"initials": "LO", "job_id": job["job_id"]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(summary["prune_targets"], [FOLDER])
        r = self.cancel(job_id=f"  {job['job_id']} ")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["phase"], "cancelled")
        self.assertEqual((r.get_json()["rows_reset"], r.get_json()["pruned"]), ([RID_DONE], [FOLDER]))
        self.assertFalse(local.exists())
        self.assertEqual(self.registry.get(RID_DONE)["manual_edit_status"], "awaiting")
        self.assertEqual(self.registry.get(RID_DONE)["processing_location"],
                         self.transfers.SHELF_PREFIX + f"{SHELF_PARENT}/{FOLDER}")
        self.assertIsNone(self.jobs.current())
        self.assertEqual(self.client.get("/voyager-tools/api/manual-edit").get_json()["jobs"], [])
        r = self.cancel(job_id=job["job_id"])
        self.assertEqual(r.status_code, 409)

    def test_the_summary_carries_the_registry_refusal(self):
        self.unlock()
        job, local = self.make_job()
        self.jobs.transition(job["job_id"], "pulling", "LO")
        self.jobs.transition(job["job_id"], "editing", "LO")
        rows = self.registry._read_rows()
        for row in rows:
            if row["readable_id"] == RID_DONE:
                row["manual_edit_status"] = "review"
        self.registry._write_rows(rows)
        summary = self.client.get("/voyager-tools/api/manual-edit").get_json()["jobs"][0]
        self.assertFalse(summary["can_cancel"])
        self.assertIn("reads marked for admin review in the atlas record", summary["refusal"])
        r = self.cancel(job_id=job["job_id"])
        self.assertEqual(r.status_code, 409)
        self.assertTrue(local.exists())

    def test_every_unfinished_project_is_listed_and_cancelled_on_its_own(self):
        self.unlock()
        first, local = self.make_job()
        second, _jobs = self.second_project(first)
        data = self.client.get("/voyager-tools/api/manual-edit").get_json()
        self.assertEqual([j["job_id"] for j in data["jobs"]], sorted([first["job_id"], second["job_id"]]))
        state = self.client.get("/voyager-tools/api/state").get_json()["manual_edit"]
        self.assertEqual(sorted(j["job_id"] for j in state), sorted([first["job_id"], second["job_id"]]))
        rows = {r["readable_id"]: r for r in self.client.get("/voyager-tools/api/rewind/rows").get_json()["rows"]}
        self.assertTrue(rows[RID_DONE]["in_job"])
        r = self.cancel(job_id=second["job_id"])
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.registry.get(RID_LBH)["manual_edit_status"], "awaiting")
        self.assertEqual(r.get_json()["pruned"], ["LBH_T2_3D"])
        self.assertEqual(self.registry.get(RID_DONE)["manual_edit_status"], "editing")
        self.assertTrue(local.exists())
        left = self.client.get("/voyager-tools/api/manual-edit").get_json()["jobs"]
        self.assertEqual([j["job_id"] for j in left], [first["job_id"]])

    def test_the_default_seam_reads_every_active_project_strictly(self):
        job, _local = self.make_job()
        fresh = views.Deps()
        views.deps.job_store = lambda: self.jobs
        self.assertEqual([j["job_id"] for j in fresh.active_jobs()], [job["job_id"]])
        seen = []

        class Lister:
            def active_jobs(self, strict=False):
                seen.append(strict)
                return [{"job_id": "a"}, {"job_id": "b"}]
        views.deps.job_store = lambda: Lister()
        self.assertEqual([j["job_id"] for j in fresh.active_jobs()], ["a", "b"])
        self.assertEqual(seen, [True], "a guard never reads past an unreadable job file")

    def test_an_unreadable_job_file_is_a_500_line(self):
        self.unlock()
        job, _local = self.make_job()
        self.jobs.job_path(job["job_id"]).write_text("{not json")
        r = self.client.get("/voyager-tools/api/manual-edit")
        self.assertEqual(r.status_code, 500)
        self.assertIn("cannot be read", r.get_json()["error"])
        self.assertIn("not valid JSON", r.get_json()["error"])


class RewindRoutes(ToolsCase):
    def test_rows(self):
        self.unlock()
        r = self.client.get("/voyager-tools/api/rewind/rows")
        rows = r.get_json()["rows"]
        self.assertEqual([x["readable_id"] for x in rows], [RID_DONE])
        self.assertTrue(rows[0]["local"])
        self.assertEqual(rows[0]["site_name"], "Meri Shoal")
        self.assertEqual([t["value"] for t in r.get_json()["targets"]], ["before_step1", "before_step0", "before_step2"])

    def test_plan_refusals(self):
        self.unlock()
        r = self.client.post("/voyager-tools/api/rewind/plan", json={"readable_id": "NOPE_T1_2020ann"})
        self.assertEqual(r.status_code, 404)
        r = self.client.post("/voyager-tools/api/rewind/plan", json={"readable_id": RID_DONE, "target": "sideways"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/voyager-tools/api/rewind/plan", json={"readable_id": RID_WAIT_A})
        self.assertEqual(r.status_code, 409)
        self.registry.upsert(RID_DONE, {"processing_location": self.shelf}, "seed")
        r = self.client.post("/voyager-tools/api/rewind/plan", json={"readable_id": RID_DONE})
        self.assertEqual(r.status_code, 409)
        self.assertIn("pull-back", r.get_json()["error"])
        self.assertIn("/api/pullback", r.get_json()["pullback_command"])

    def test_plan_apply_and_spot_redo(self):
        self.unlock()
        r = self.client.post("/voyager-tools/api/rewind/plan", json={"readable_id": RID_DONE, "target": "before_step0"})
        self.assertEqual(r.status_code, 200, r.get_json())
        plan = r.get_json()["plan"]
        self.assertEqual(sorted(m["kind"] for m in plan["moves"]), ["frames", "report"])
        self.assertTrue(self.psx.exists())
        r = self.client.post("/voyager-tools/api/rewind/apply",
                             json={"readable_id": RID_DONE, "target": "before_step0", "initials": "LO"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/voyager-tools/api/rewind/apply",
                             json={"readable_id": RID_DONE, "target": "before_step0", "initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 200, r.get_json())
        manifest = r.get_json()["manifest"]
        self.assertEqual(manifest["chunk"]["removed"], 1)
        self.assertFalse((self.folder / "frames" / RID_DONE).exists())
        self.assertEqual(self.registry.get(RID_DONE)["step1_status"], "")
        self.assertEqual(json.loads(self.psx.read_text())["chunks"], [])
        # A second apply has nothing left: 409.
        r = self.client.post("/voyager-tools/api/rewind/plan", json={"readable_id": RID_DONE})
        self.assertEqual(r.status_code, 409)
        # Spot redo on a fresh complete row writes the fact.
        self.registry.upsert(RID_DONE, {"step1_status": "complete"}, "seed")
        r = self.client.post("/voyager-tools/api/spot-redo", json={"readable_id": RID_DONE, "initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertIn("spot_redo", r.get_json()["manifest"]["spot_redo"])
        self.assertEqual(self.registry.fact_sections(RID_DONE)["voyager1"]["spot_redo"]["recorded_by"], "voyager_tools:LO")

    def test_row_in_job_refused(self):
        self.unlock()
        self.jobs.create_job(
            [{"readable_id": RID_DONE, "site": "MRS", "transect": "T1", "processing_folder": FOLDER,
              "shelf_location": "/s", "psx_basename": "x.psx"}], str(self.bench), "LO")
        r = self.client.post("/voyager-tools/api/rewind/plan", json={"readable_id": RID_DONE})
        self.assertEqual(r.status_code, 409)
        self.assertIn("unfinished manual edit project", r.get_json()["error"])
        rows = self.client.get("/voyager-tools/api/rewind/rows").get_json()["rows"]
        self.assertTrue(rows[0]["in_job"])

    def test_a_row_after_the_pull_is_refused_even_with_no_project_listed(self):
        self.unlock()
        rows = self.client.get("/voyager-tools/api/rewind/rows").get_json()["rows"]
        self.assertFalse(rows[0]["manual_edit_under_way"])
        for status in ("editing", "review", "upload"):
            with self.subTest(status=status):
                seed_status(self.registry, RID_DONE, status)
                rows = self.client.get("/voyager-tools/api/rewind/rows").get_json()["rows"]
                self.assertTrue(rows[0]["manual_edit_under_way"])
                for route in ("/voyager-tools/api/rewind/plan", "/voyager-tools/api/rewind/apply",
                              "/voyager-tools/api/spot-redo"):
                    r = self.client.post(route, json={"readable_id": RID_DONE, "initials": "LO", "confirm": True})
                    self.assertEqual(r.status_code, 409, route)
                    self.assertIn("on the edit bench in a manual edit project", r.get_json()["error"])
                self.assertEqual(json.loads(self.psx.read_text())["chunks"], [RID_DONE])
                self.assertEqual(self.registry.get(RID_DONE)["step1_status"], "complete")


    def test_an_unreadable_job_store_stops_the_rewind_but_not_the_lists(self):
        """Without the job store the project guard is unknown, so plan, apply and spot redo answer 500;
        the row list and the state still load, with no row marked as in a project."""
        self.unlock()
        (self.reg_root / "manual_edit" / "jobs").mkdir(parents=True)
        (self.reg_root / "manual_edit" / "jobs" / "ME-20260927-010000-LO.json").write_text("{not json")
        for route in ("/voyager-tools/api/rewind/plan", "/voyager-tools/api/rewind/apply",
                      "/voyager-tools/api/spot-redo"):
            r = self.client.post(route, json={"readable_id": RID_DONE, "initials": "LO", "confirm": True})
            self.assertEqual(r.status_code, 500, route)
            self.assertIn("cannot be read", r.get_json()["error"])
        self.assertEqual(json.loads(self.psx.read_text())["chunks"], [RID_DONE])
        self.assertEqual(self.registry.get(RID_DONE)["step1_status"], "complete")
        r = self.client.get("/voyager-tools/api/rewind/rows")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["rows"][0]["in_job"])
        self.assertEqual(self.client.get("/voyager-tools/api/state").status_code, 200)


class PromptsRoutes(ToolsCase):
    def test_rendered(self):
        self.unlock()
        r = self.client.get("/voyager-tools/api/prompts")
        data = r.get_json()
        self.assertTrue(data["exists"])
        self.assertIn("<h1>Guide</h1>", data["html"])
        self.assertIn("<code>atlascatalog.py --dry-run</code>", data["html"])
        self.assertIn("<ul>", data["html"])
        self.assertTrue(data["updated_at"].endswith("AST"))

    def test_missing(self):
        self.unlock()
        views.deps.prompts_doc = lambda: self.root / "gone.md"
        data = self.client.get("/voyager-tools/api/prompts").get_json()
        self.assertFalse(data["exists"])
        self.assertIn("missing", data["error"])

    def test_real_document_exists_beside_the_agent_docs(self):
        self.assertTrue(views.PROMPTS_DOC.is_file(), views.PROMPTS_DOC)
        self.assertEqual(views.PROMPTS_DOC.parent.name, "agents")


class ParamsRoutes(ToolsCase):
    def test_list_load_validate(self):
        self.unlock()
        r = self.client.get("/voyager-tools/api/params?phase=voyager1")
        self.assertEqual(r.status_code, 200, r.get_json())
        data = r.get_json()
        self.assertEqual(data["default_version"], "v1.0.0")
        self.assertTrue(data["entries"][0]["is_default"])
        self.assertTrue(all(not k.get("hidden") for k in data["schema"]["keys"]))
        self.assertNotIn("processing.max_chunks_per_psx", [k["path"] for k in data["schema"]["keys"]])
        self.assertEqual(data["default"]["values"]["processing"]["frames_per_transect"], 1000)
        r = self.client.get("/voyager-tools/api/params?phase=voyager9")
        self.assertEqual(r.status_code, 400)
        r = self.client.get("/voyager-tools/api/params/load?phase=voyager1&ref=v9.9.9")
        self.assertEqual(r.status_code, 404)
        values = dict(data["default"]["values"])
        values["processing"]["frames_per_transect"] = -5
        r = self.client.post("/voyager-tools/api/params/validate", json={"phase": "voyager1", "values": values})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["ok"])
        self.assertTrue(any("frames_per_transect" in p for p in r.get_json()["problems"]))
        values["processing"]["frames_per_transect"] = 800
        r = self.client.post("/voyager-tools/api/params/validate",
                             json={"phase": "voyager1", "values": values, "yaml_text": "project:\n  notes: hi\n"})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(r.get_json()["level"], "minor")
        self.assertEqual(r.get_json()["values"]["project"]["notes"], "hi")
        r = self.client.post("/voyager-tools/api/params/validate", json={"phase": "voyager1", "yaml_text": ": bad: ["})
        self.assertEqual(r.status_code, 400)
        self.assertIn("YAML box", r.get_json()["error"])

    def test_branch_and_default_saves_commit(self):
        self.unlock()
        values = self.store.load("voyager1", "default").values
        values["processing"]["frames_per_transect"] = 1200
        r = self.client.post("/voyager-tools/api/params/branch", json={
            "phase": "voyager1", "values": values, "slug": "fast", "description": "fewer frames", "why": "speed",
            "initials": "lo"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["file"]["ref"], "branches/fast/v1.0.0")
        self.assertTrue((self.checkout / "voyager1" / "branches" / "fast" / "v1.0.0.yaml").exists())
        self.assertTrue(r.get_json()["git"]["committed"])
        self.assertFalse(r.get_json()["git"]["pushed"])
        self.assertTrue(any(c[:1] == ["commit"] for c in self.git.calls))
        self.assertEqual(self.history_lines, [("voyager1", "voyager1/branches/fast", None, "v1.0.0", "LO", "speed")])
        r = self.client.post("/voyager-tools/api/params/branch", json={
            "phase": "voyager1", "values": values, "slug": "Bad Slug", "description": "d", "why": "w", "initials": "LO"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/voyager-tools/api/params/default", json={
            "phase": "voyager1", "values": values, "description": "new default", "initials": "LO"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/voyager-tools/api/params/default", json={
            "phase": "voyager1", "values": values, "description": "new default", "initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["file"]["version"], "v1.1.0")
        self.assertEqual(r.get_json()["level"], "minor")
        self.assertEqual((self.checkout / "voyager1" / "DEFAULT").read_text().strip(), "v1.1.0")
        r = self.client.post("/voyager-tools/api/params/default", json={
            "phase": "voyager1", "values": values, "yaml_text": "processing:\n  brand_new: 1\n",
            "description": "major", "initials": "LO", "confirm": True})
        self.assertEqual(r.get_json()["file"]["version"], "v2.0.0")
        r = self.client.post("/voyager-tools/api/params/default", json={
            "phase": "voyager1", "description": "same", "initials": "LO", "confirm": True})
        self.assertEqual(r.status_code, 400)
        self.assertIn("nothing changed", r.get_json()["error"])

    def test_git_failure_is_reported_not_raised(self):
        self.unlock()

        def broken(args, cwd):
            return ps.GitResult(1, "", "index locked")
        self.store.run_git = broken
        values = self.store.load("voyager1", "default").values
        values["processing"]["use_gpu"] = False
        r = self.client.post("/voyager-tools/api/params/branch", json={
            "phase": "voyager1", "values": values, "slug": "cpu", "description": "d", "why": "w", "initials": "LO"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["git"]["committed"])
        self.assertIn("index locked", r.get_json()["git"]["detail"])

    def test_missing_checkout_is_a_500_line(self):
        self.unlock()
        views.deps.params_store = lambda: ps.ParamsStore(self.root / "nowhere")
        r = self.client.get("/voyager-tools/api/params")
        self.assertEqual(r.status_code, 500)
        self.assertIn("not found", r.get_json()["error"])


class SeasonRoutes(ToolsCase):
    def test_get_and_save(self):
        self.unlock()
        table = self.client.get("/voyager-tools/api/seasons").get_json()["seasons"]
        self.assertEqual([s["season_key"] for s in table], ["2023ann", "2024_pbl", "2024ann", "2025_pbl"])
        self.assertEqual(table[0]["row_count"], 1)
        r = self.client.post("/voyager-tools/api/seasons", json={"season_key": "2025_pbl", "camera_model": "GoPro 12",
                                                                 "initials": "LO"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(r.get_json()["changed"])
        self.assertEqual(r.get_json()["label"], "2025 spring")
        self.assertEqual(r.get_json()["season"]["set_by"], "voyager_tools:LO")
        r = self.client.post("/voyager-tools/api/seasons", json={"season_key": "2025_pbl", "camera_model": "GoPro 12",
                                                                 "initials": "LO"})
        self.assertFalse(r.get_json()["changed"])
        r = self.client.post("/voyager-tools/api/seasons", json={"season_key": "2025-pbl", "camera_model": "x", "initials": "LO"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/voyager-tools/api/seasons", json={"season_key": "2025_pbl", "initials": "LO"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/voyager-tools/api/seasons", json={"season_key": "2025_pbl", "notes": 7, "initials": "LO"})
        self.assertEqual(r.status_code, 400)


class PanelShape(ToolsCase):
    """The Atlas control panel (program C milestone C3): the display name, the
    grouped pane list, the nine settings and desktop panes, the panel fragment
    and the two to four letter initials rule."""

    def page(self) -> str:
        self.unlock()
        r = self.client.get("/voyager-tools/")
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)

    def test_display_name_in_every_visible_place(self):
        page = self.page()
        self.assertEqual(views.DISPLAY_NAME, "Atlas control panel")
        self.assertIn("<title>Atlas control panel - VICARIUS</title>", page)
        self.assertIn('<h1 class="title" id="vt-title">Atlas control panel</h1>', page)
        self.assertIn("Atlas control panel - built by Lauren Olinger", page)
        self.assertNotIn("Voyager tools", page)
        self.assertIn("Atlas control panel", views.lock_body_html())
        self.assertIn("Atlas control panel", views.admin_body_html())
        # The code names stay (decision 9): slug, address, actor prefix, root class.
        self.assertEqual((views.MODULE_SLUG, views.URL_PREFIX, views.ACTOR_PREFIX),
                         ("voyager_tools", "/voyager-tools", "voyager_tools:"))
        self.assertIn('<section class="voyager-tools" id="vt-root" data-vt-base="/voyager-tools"', page)

    def test_the_pane_list_is_grouped_and_holds_every_tab_once(self):
        import re
        page = self.page()
        nav = page[page.index('<nav class="vt-nav"'):page.index("</nav>")]
        items = re.findall(r'<p class="vt-nav-group" role="presentation">([^<]+)</p>|data-vt-tab="([a-z_]+)"', nav)
        groups, current = [], None
        for heading, key in items:
            if heading:
                current = (heading.strip(), [])
                groups.append(current)
            else:
                self.assertIsNotNone(current, "a tab sits before the first group heading")
                current[1].append(key)
        self.assertEqual(groups, [(name, keys) for name, keys in NAV_GROUPS])
        keys = re.findall(r'data-vt-tab="([a-z_]+)"', page)
        self.assertEqual(sorted(keys), sorted(ALL_TABS))
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_tab_has_its_pane_a_heading_and_a_show_tooltip(self):
        import re
        page = self.page()
        for key in ALL_TABS:
            with self.subTest(key=key):
                tab = re.search(r'<button[^>]*data-vt-tab="' + key + r'"[^>]*>', page).group(0)
                self.assertIn(f'aria-controls="vt-{key}"', tab)
                self.assertRegex(tab, r'title="Show ')
                pane = re.search(r'<section class="vt-pane" id="vt-' + key + r'"[^>]*>', page)
                self.assertIsNotNone(pane, f"no pane for {key}")
                self.assertIn(f'data-vt-pane="{key}"', pane.group(0))
                body = views.slice_section(page, pane.group(0))
                self.assertIn("<h2", body)
                self.assertIn('class="tip"', body)

    def test_settings_panes_carry_fields_reason_and_one_primary_button(self):
        page = self.page()
        for key, button in (("settings", "Save manual edit settings"), ("shelf", "Save the Shelf copy setting"),
                            ("runs", "Save Voyager run settings"), ("floors", "Save space floors")):
            with self.subTest(key=key):
                body = views.slice_section(page, f'<section class="vt-pane" id="vt-{key}"')
                self.assertIn(f'data-vt="{key}-fields"', body)
                self.assertIn(f'data-vt="{key}-reason"', body)
                self.assertIn(f'data-vt="{key}-meta"', body)
                self.assertIn(f'data-vt="{key}-drift"', body)
                self.assertIn(f'data-vt="{key}-result"', body)
                self.assertEqual(body.count("btn-primary"), 1)
                self.assertIn(f">{button}</button>", body)
        self.assertNotIn("admin gated", page.lower())
        self.assertNotIn("admin-gated", page.lower())

    def test_uploads_and_timelines_panes_carry_their_parts(self):
        page = self.page()
        uploads = views.slice_section(page, '<section class="vt-pane" id="vt-uploads"')
        for hook in ("uploads-ready", "uploads-tick-all", "uploads-untick-all", "uploads-start", "uploads-running",
                     "uploads-review", "uploads-recent", "uploads-result", "uploads-refresh"):
            with self.subTest(hook=hook):
                self.assertIn(f'data-vt="{hook}"', uploads)
        self.assertEqual(uploads.count("btn-primary"), 1)
        self.assertIn(">Upload ticked</button>", uploads)
        timelines = views.slice_section(page, '<section class="vt-pane" id="vt-timelines"')
        for hook in ("tl-summary", "tl-projects", "tl-job", "tl-state", "tl-editor", "tl-since", "tl-clear",
                     "tl-rows", "tl-csv", "tl-drawer", "tl-result", "tl-refresh"):
            with self.subTest(hook=hook):
                self.assertIn(f'data-vt="{hook}"', timelines)
        self.assertIn('role="dialog"', timelines)
        for label in ("tl-job", "tl-state", "tl-editor", "tl-since"):
            with self.subTest(label=label):
                self.assertIn(f'<label for="vt-{label}">', timelines)

    def test_panel_fragment_mounts_the_panel(self):
        self.unlock()
        r = self.client.get("/voyager-tools/fragment?tool=panel")
        self.assertEqual(r.status_code, 200)
        payload = r.get_json()
        self.assertEqual(payload["mount"], "VoyagerTools.mountPanel")
        self.assertEqual(payload["ui_style"], "voyager_tools")
        self.assertTrue(payload["html"].startswith('<section class="voyager-tools"'))
        self.assertEqual(payload["scripts"], PANEL_SCRIPT_URLS)
        self.assertEqual(payload["styles"], ["/voyager-tools/static/voyager_tools.css?v=" + views.ASSET_VERSION])

    def test_an_unknown_tool_is_refused_in_one_sentence(self):
        self.unlock()
        for tool in ("reviews", "../x", "PANEL", "", "a" * 300):
            with self.subTest(tool=tool[:20]):
                r = self.client.get("/voyager-tools/fragment", query_string={"tool": tool})
                if tool == "":
                    self.assertEqual(r.status_code, 200)  # an empty tool is the plain window
                    continue
                self.assertEqual(r.status_code, 400)
                error = r.get_json()["error"]
                self.assertTrue(error.endswith("."))
                self.assertLess(len(error), 200)

    def test_panel_fragment_outside_admin_and_locked(self):
        r = self.client.get("/voyager-tools/fragment?tool=panel")
        self.assertEqual(r.status_code, 401)
        self.assertTrue(r.get_json()["need_unlock"])
        self.unlock()
        self.locked = True
        r = self.client.get("/voyager-tools/fragment?tool=panel")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["ui_style"], "locked")

    def test_initials_are_two_to_four_letters(self):
        self.unlock()
        for bad in ("", "L", "L0", "ABCDE", "L O", "<b>x</b>", "LO\n1", 7, None):
            with self.subTest(initials=bad):
                r = self.client.post("/voyager-tools/api/order/unlock", json={"initials": bad})
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.get_json()["error"], views.LINE_INITIALS)
        for good in ("lo ", "LO", "abcd"):
            with self.subTest(initials=good):
                r = self.client.post("/voyager-tools/api/order/unlock", json={"initials": good})
                self.assertNotEqual(r.status_code, 400, r.get_json())
        self.assertEqual(views.INITIALS_PATTERN.pattern, self.registry.MANUAL_EDIT_INITIALS_PATTERN.pattern)

    def test_initials_field_takes_four_characters(self):
        page = self.page()
        self.assertIn('id="vt-initials" class="w-initials" maxlength="4"', page)


class Helpers(unittest.TestCase):
    def test_render_markdown(self):
        text = ("# Title\n\nA `code` line with **bold** and [a link](http://x/y).\n\n- one\n- two\n\n1. first\n2. second\n\n"
                "```\nraw <b>not bold</b>\n```\n\n| a | b |\n|---|---|\n| 1 | <x> |\n\n<script>alert(1)</script>\n")
        html = views.render_markdown(text)
        self.assertIn("<h1>Title</h1>", html)
        self.assertIn("<code>code</code>", html)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn('<a href="http://x/y">a link</a>', html)
        self.assertIn("<ul>\n<li>one</li>\n<li>two</li>\n</ul>", html)
        self.assertIn("<ol>\n<li>first</li>", html)
        self.assertIn("<pre><code>raw &lt;b&gt;not bold&lt;/b&gt;</code></pre>", html)
        self.assertIn("<table><thead><tr><th>a</th><th>b</th></tr></thead><tbody><tr><td>1</td><td>&lt;x&gt;</td></tr></tbody></table>", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertEqual(views.render_markdown(""), "")
        self.assertIn("<pre><code>open</code></pre>", views.render_markdown("```\nopen"))

    def test_render_markdown_huge_in_bounded_time(self):
        import time
        text = "- item\n" * 50000
        started = time.monotonic()
        html = views.render_markdown(text)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(html.count("<li>"), 50000)

    def test_slice_section_balances_nested_sections(self):
        page = '<html><section class="voyager-tools"><section class="vt-pane">x</section></section><script></script></html>'
        self.assertEqual(views.slice_section(page, '<section class="voyager-tools"'),
                         '<section class="voyager-tools"><section class="vt-pane">x</section></section>')
        self.assertIsNone(views.slice_section("<div></div>", '<section class="voyager-tools"'))
        self.assertIsNone(views.slice_section('<section class="voyager-tools"><section>', '<section class="voyager-tools"'))

    def test_lock_texts(self):
        self.assertEqual(views.LOCK_MESSAGE, "Under development. Please come back later. Email Lauren for questions.")
        self.assertIn("mailto:lauren.olinger@uvi.edu", views.lock_body_html())
        self.assertIn("vic-lock-page", views.lock_page_html())


class StandaloneHost(unittest.TestCase):
    def setUp(self):
        self._saved = {k: getattr(views.deps, k) for k in vars(views.deps)}
        views.deps.is_locked = lambda: False

    def tearDown(self):
        for key, value in self._saved.items():
            setattr(views.deps, key, value)

    def test_unlock_flow_and_cookie_name(self):
        app = standalone_app.create_app(admin_pin="1234")
        c = app.test_client()
        self.assertEqual(c.get("/").status_code, 302)
        self.assertEqual(c.get("/voyager-tools/").status_code, 401)
        self.assertEqual(c.post("/api/admin/unlock", json={"pin": "0000"}).status_code, 401)
        r = c.post("/api/admin/unlock", json={"pin": "1234"})
        self.assertEqual(r.status_code, 200)
        cookies = [v for k, v in r.headers.items() if k.lower() == "set-cookie"]
        self.assertTrue(any("voyager_tools_session=" in v for v in cookies))
        self.assertTrue(all("vicarius_session=" not in v for v in cookies))
        self.assertEqual(c.get("/voyager-tools/").status_code, 200)
        c.post("/api/admin/lock")
        self.assertEqual(c.get("/voyager-tools/").status_code, 401)

    def test_no_pin_configured_refuses(self):
        app = standalone_app.create_app(admin_pin="")
        r = app.test_client().post("/api/admin/unlock", json={"pin": ""})
        self.assertEqual(r.status_code, 401)
        self.assertIn("not configured", r.get_json()["error"])

    def test_port_validation(self):
        self.assertEqual(standalone_app.validate_port(5098), 5098)
        for bad in (5090, 5093, 80, 70000, "5098", True):
            with self.assertRaises(ValueError):
                standalone_app.validate_port(bad)
        self.assertEqual(standalone_app.main(["--port", "5090"]), 2)



class ReviewFragment(ToolsCase):
    """The review fragment of one transect (program C milestone C6, spec 4.11) and the Review queue pane's parts."""

    PSX = "MRS_T1_2023_2024.psx"

    def make_job(self, folder=FOLDER):
        """One job in the temporary store holding MRS T1 (two timepoints) under PSX; returns its id."""
        rows = [{"readable_id": rid, "site": "MRS", "site_name": "", "transect": "T1", "processing_folder": folder,
                 "shelf_location": f"{SHELF_PARENT}/{FOLDER}", "psx_basename": self.PSX}
                for rid in (RID_WAIT_B, RID_WAIT_A)]
        return self.jobs.create_job(rows, str(self.bench), "LO")["job_id"]

    def get(self, job, psx):
        return self.client.get("/voyager-tools/fragment", query_string={"tool": "review", "job": job, "psx": psx})

    def test_the_payload_names_the_mount_its_assets_and_the_transect(self):
        job = self.make_job()
        self.unlock()
        r = self.get(job, self.PSX)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        payload = r.get_json()
        self.assertEqual(payload["mount"], "VoyagerTools.mountReview")
        self.assertEqual(payload["ui_style"], "voyager_tools_review")
        self.assertEqual(payload["opts"], {"jobId": job, "psx": self.PSX})
        self.assertEqual(payload["scripts"], ["/voyager-tools/static/" + name + "?v=" + views.ASSET_VERSION
                                              for name in ("voyager_tools.js", "vt_uploads.js", "vt_review.js")])
        self.assertEqual(payload["styles"], ["/voyager-tools/static/voyager_tools.css?v=" + views.ASSET_VERSION])
        html = payload["html"]
        self.assertTrue(html.startswith('<section class="voyager-tools vt-review" data-vt-base="/voyager-tools"'))
        self.assertIn(f'data-job="{job}"', html)
        self.assertIn(f'data-psx="{self.PSX}"', html)
        self.assertIn('<span class="vt-site">Meri Shoal</span> <span class="id code">MRS</span>', html)
        self.assertIn(f"{FOLDER}: {RID_WAIT_A}, {RID_WAIT_B}", html)  # registry order
        self.assertIn('data-review-part="body"', html)
        self.assertNotIn('id="vt-root"', html)

    def test_a_site_the_table_cannot_name_stays_a_bare_code_with_a_tooltip(self):
        job = self.make_job()
        views.deps.site_name = lambda code: ""
        self.unlock()
        html = self.get(job, self.PSX).get_json()["html"]
        self.assertIn('<span class="id code" title="The site table has no full name for MRS.">MRS</span>', html)

    def test_outside_admin_mode_and_locked(self):
        job = self.make_job()
        r = self.get(job, self.PSX)
        self.assertEqual(r.status_code, 401)
        body = r.get_json()
        self.assertTrue(body["need_unlock"] and body["admin_required"])
        self.assertNotIn("scripts", body)
        self.unlock()
        self.locked = True
        r = self.get(job, self.PSX)
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.get_json()["ui_style"], r.get_json()["locked"]), ("locked", True))

    def test_malformed_and_unknown_values_are_refused_in_one_sentence(self):
        job = self.make_job()
        self.unlock()
        cases = [("", self.PSX, 400), ("ME-2026", self.PSX, 400), ("../../etc/passwd", self.PSX, 400),
                 (job.lower(), self.PSX, 400), (job + "\n", self.PSX, 400), (job, "", 400), (job, "../x.psx", 400),
                 (job, "x.psx/", 400), (job, "x.PSX", 400), (job, "a\x00b.psx", 400), (job, "a" * 300 + ".psx", 400),
                 (job, "MRS_T9_2023_2024.psx", 404), ("ME-20260101-000000-ZZ", self.PSX, 404)]
        for job_value, psx, code in cases:
            with self.subTest(job=job_value[:30], psx=psx[:30]):
                r = self.get(job_value, psx)
                self.assertEqual(r.status_code, code, r.get_data(as_text=True))
                error = r.get_json()["error"]
                self.assertTrue(error.endswith(".") and "\n" not in error, error)
        r = self.client.get("/voyager-tools/fragment?tool=review")
        self.assertEqual(r.status_code, 400)

    def test_a_job_file_that_cannot_be_read_answers_500_naming_it(self):
        job = self.make_job()
        self.jobs.job_path(job).write_text("{not json", encoding="utf-8")
        self.unlock()
        r = self.get(job, self.PSX)
        self.assertEqual(r.status_code, 500)
        self.assertIn(job, r.get_json()["error"])

    def test_the_head_escapes_hostile_values_from_the_job_file(self):
        job = self.make_job()
        path = self.jobs.job_path(job)
        record = json.loads(path.read_text(encoding="utf-8"))
        for row in record["rows"]:
            row["processing_folder"] = '<img src=x onerror="alert(1)">'
            row["site_name"] = "<script>alert(2)</script>"
        path.write_text(json.dumps(record), encoding="utf-8")
        self.unlock()
        html = self.get(job, self.PSX).get_json()["html"]
        self.assertNotIn("<img", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;img", html)

    def test_the_fragment_is_read_only(self):
        job = self.make_job()
        before = self.jobs.job_path(job).read_bytes()
        events = Path(self.registry.EVENTS_CSV).read_bytes()
        self.unlock()
        self.assertEqual(self.get(job, self.PSX).status_code, 200)
        self.assertEqual(self.jobs.job_path(job).read_bytes(), before)
        self.assertEqual(Path(self.registry.EVENTS_CSV).read_bytes(), events)

    def test_the_manual_edit_module_missing_answers_503(self):
        job = self.make_job()
        self.unlock()

        def missing():
            raise ImportError("no manualedit here")
        views.deps.job_store = missing
        r = self.get(job, self.PSX)
        self.assertEqual(r.status_code, 503)
        self.assertTrue(r.get_json()["not_mounted"])

    def test_fragment_payload_refuses_the_review_tool(self):
        self.unlock()
        with self.app.test_request_context():
            with self.assertRaises(views.RequestError):
                views.fragment_payload("review")

    def test_the_review_queue_pane_carries_its_parts(self):
        self.unlock()
        page = self.client.get("/voyager-tools/").get_data(as_text=True)
        pane = views.slice_section(page, '<section class="vt-pane" id="vt-review"')
        for hook in ("review-meta", "review-items", "review-stopped", "review-stopped-wrap", "review-returnable",
                     "review-returnable-wrap", "review-unmatched", "review-area", "review-result", "review-refresh"):
            with self.subTest(hook=hook):
                self.assertIn(f'data-vt="{hook}"', pane)
        self.assertEqual(pane.count("btn-primary"), 0)
        self.assertIn("vt_review.js", page)


if __name__ == "__main__":
    unittest.main()
