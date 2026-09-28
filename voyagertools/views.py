#!/usr/bin/env python3
"""
Module: voyagertools/views.py
Purpose: The web layer of the Atlas control panel (module voyager_tools;
         the code names keep the slug): one Flask blueprint, voyager_tools_bp, mounted at
         /voyager-tools by the VICARIUS desktop
         (vicarius_ui_os/voyager_tools_views.py) and by standalone_app.py. It
         serves the page, the fragment the desktop window and the atlas embed
         (the plain window, ?tool=panel for the atlas's Control panel
         tab, or ?tool=review for one transect's review content), and the
         JSON API of the eight tools: the processing order,
         clean workbench (the Voyager 1 Workbench with the other stages
         excluded, and the Voyager 2 stage), clean manual edit run, rewind
         and spot redo (Voyager 1, and Voyager 2 per transect), the prompts
         doc, the parameter defaults and the camera model table. The
         Timelines and Review queue routes and the review fragment's payload
         live in manual_edit_views.py on the same blueprint; the Uploads pane reads and starts uploads through the
         manual edit module's own /manual-edit/api routes. The
         settings panes of the panel read and write the desktop's own
         /api/settings routes (vicarius_ui_os/settings_views.py), not routes
         of this blueprint. A parameter default or branch save is written
         to the settings history through deps.record_setting, which the
         desktop wires; without it the save is refused and undone.
Inputs:  HTTP requests; the TCRMP 3D registry library; the manual edit job
         store; the voyagerparams checkout through paramsstore; the
         Carousel's nas.yaml and its snapshot helpers; the prompts document.
Outputs: HTML and JSON responses; every write goes through the library that
         owns the file (registry.py, jobstore.py, paramsstore.py) under the
         actor voyager_tools:<INITIALS>.

Gates, in this order on every route: the module lock (423, or the lock page
for the page route; the fragment answers the desktop's locked payload) and
admin mode (401 {"error": "locked", "need_unlock": true}). Everything the
routes reach outside this clone goes through `deps`, one object of plain
callables a test replaces with fakes: no test touches the live registry, the
NAS, git, Metashape or the processing log.
"""
from __future__ import annotations

import csv
import html as html_lib
import importlib
import json
import os
import re
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import yaml
from flask import Blueprint, current_app, g, jsonify, render_template, request, session

from . import camera_tool, clean_manual_edit, clean_voyager2_stage, clean_workbench, order_tool, rewind
from . import rewind_voyager2
from . import paramsstore as ps

# --- constants ---------------------------------------------------------------

BLUEPRINT_NAME = "voyager_tools"
URL_PREFIX = "/voyager-tools"
MODULE_SLUG = "voyager_tools"
DISPLAY_NAME = "Atlas control panel"
UI_STYLE = "voyager_tools"
ASSET_VERSION = "20260928b"
ROOT_SECTION_PREFIX = '<section class="voyager-tools"'
STATIC_JS = "voyager_tools.js"
STATIC_SETTINGS_JS = "vt_settings.js"  # the settings and desktop panes (program C spec 5.3)
STATIC_UPLOADS_JS = "vt_uploads.js"  # the Uploads pane (milestone C7)
STATIC_TIMELINES_JS = "vt_timelines.js"  # the Timelines pane (milestone C8)
STATIC_REVIEW_JS = "vt_review.js"  # the Review queue pane and VoyagerTools.mountReview (milestone C6)
STATIC_CSS = "voyager_tools.css"
# Load order: the pane seam first, then one script per group of panes.
PANEL_SCRIPTS = (STATIC_JS, STATIC_SETTINGS_JS, STATIC_UPLOADS_JS, STATIC_TIMELINES_JS, STATIC_REVIEW_JS)
# The review fragment (program C spec 4.11) loads the pane seam, the upload
# window its Start the upload now opens (vt_uploads.js), and the review content.
REVIEW_SCRIPTS = (STATIC_JS, STATIC_UPLOADS_JS, STATIC_REVIEW_JS)
TOOL_PARAM = "tool"
TOOL_PANEL = "panel"
TOOL_REVIEW = "review"
MOUNT_WINDOW = "VoyagerTools.mount"
MOUNT_PANEL = "VoyagerTools.mountPanel"
MOUNT_REVIEW = "VoyagerTools.mountReview"
UI_STYLE_REVIEW = "voyager_tools_review"
FRAGMENT_TOOLS = {None: MOUNT_WINDOW, TOOL_PANEL: MOUNT_PANEL}
TOOL_NAMES = (TOOL_PANEL, TOOL_REVIEW)

_PKG = Path(__file__).resolve().parent
CLONE_ROOT = _PKG.parent
TEMPLATES_DIR = CLONE_ROOT / "templates"
STATIC_DIR = CLONE_ROOT / "static"
VICARIUS_ROOT = Path(os.environ.get("VICARIUS_ROOT") or "/mnt/rip/vicarius_drive/vicarius")
DRIVE_ROOT = VICARIUS_ROOT.parent
REGISTRY_LIB_DIR = VICARIUS_ROOT / "_METADATA" / "3d"
MANUAL_EDIT_CLONE = VICARIUS_ROOT / "modules" / "manual_edit" / "github_repo"
# The manual edit module's blueprint name: only an app that mounts it runs
# its upload runner, so only there may a stranded upload set be recovered.
MANUAL_EDIT_BLUEPRINT = "manual_edit"
DRIVER_CLONE = VICARIUS_ROOT / "modules" / "driver" / "github_repo"
NAS_YAML = DRIVER_CLONE / "config" / "nas.yaml"
LOCKS_PATH = VICARIUS_ROOT / "_METADATA" / "module_locks.json"
LOCKS_PATH_ENV = "VICARIUS_LOCKS_PATH"
PROCESSING_LOG = VICARIUS_ROOT / "_METADATA" / "logs" / "processing_log.csv"
PROMPTS_DOC = DRIVE_ROOT / "docs" / "agents" / "voyager-ingest-prompts.md"
SITE_CODES_CSV = VICARIUS_ROOT / "modules" / "reef_point_seg" / "github_repo" / "supporting_data" / "site_codes.csv"
SITE_TABLE_CODE_COLUMN = "site_code"
SITE_TABLE_NAME_COLUMN = "site"

LOCK_MESSAGE = "Under development. Please come back later. Email Lauren for questions."
LOCK_CONTACT_EMAIL = "lauren.olinger@uvi.edu"
ADMIN_ERROR = {"error": "locked", "need_unlock": True}
ADMIN_MESSAGE = "Admin mode is off. Unlock admin to use the Atlas control panel."
DEFAULT_PHASE = "voyager1"
# Two to four letters after trimming and upper-casing: the registry's
# MANUAL_EDIT_INITIALS_PATTERN, so initials this panel accepts are never
# refused by a manual edit review, open or upload route.
INITIALS_PATTERN = re.compile(r"^[A-Z]{2,4}$")
LINE_INITIALS = "Type your initials, two to four letters, at the top of the panel before saving."
PARAMS_HISTORY_ONLY_ON_DESKTOP = ("Parameter defaults are saved only from the desktop on port 5090, which keeps "
                                  "the settings history.")
PARAMS_DEFAULT_KEY = "{phase}/DEFAULT"
PARAMS_BRANCH_KEY = "{phase}/branches/{slug}"
ACTOR_PREFIX = "voyager_tools:"
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_PROMPTS_BYTES = 4 * 1024 * 1024
LOG_TAIL = 20
LINE_CANCEL_ELSEWHERE = ("Cancel manual edit projects from the Atlas control panel on the VICARIUS desktop, where the "
                         "manual edit runs.")
JOB_ID_KEY = "job_id"
CONFIRM_KEY = "confirm"
AST = timezone(timedelta(hours=-4))
LOCKED_PHRASE = "locked by"
RUNNING_PHRASE = "processing right now"
HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORISED = 401
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_LOCKED = 423
HTTP_SERVER_ERROR = 500
# The excluded roots of the Workbench walk (program D Amendment 1, A1.1):
# every stage driver.space names except the Voyager 1 Workbench, the edit
# benches, and the in-flight transfer ledger, labelled for the page.
LEDGER_LABEL = "transfer ledger"
EDIT_BENCH_SETTING = "edit_bench_default"
JOB_BENCH_KEY = "edit_bench"
REWIND_TARGETS = rewind.TARGETS + (rewind_voyager2.TARGET_BEFORE_STEP2,)
REWIND_TARGET_WORDS = dict(rewind.TARGET_WORDS, **{rewind_voyager2.TARGET_BEFORE_STEP2: rewind_voyager2.TARGET_WORDS})
PHASE_VOYAGER1, PHASE_VOYAGER2 = "voyager1", "voyager2"
SPOT_REDO_PHASES = (PHASE_VOYAGER1, PHASE_VOYAGER2)
CONFIRM_VIGIL_KEY = "confirm_vigil"

voyager_tools_bp = Blueprint(BLUEPRINT_NAME, __name__, url_prefix=URL_PREFIX,
                             template_folder=str(TEMPLATES_DIR), static_folder=str(STATIC_DIR),
                             static_url_path="/static")


# --- errors --------------------------------------------------------------------

class RequestError(ValueError):
    """A request the route refuses; carries the HTTP status and a one-sentence message."""

    def __init__(self, message: str, status: int = HTTP_BAD_REQUEST, extra: Mapping | None = None):
        super().__init__(message)
        self.status = status
        self.extra = dict(extra or {})


# --- dependencies ----------------------------------------------------------------

def _registry_module():
    """The registry library, imported from REGISTRY_LIB_DIR; the same module object a test reloads."""
    path = str(REGISTRY_LIB_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)
    return importlib.import_module("registry")


def _jobstore_module():
    """The manual edit job store library, imported from MANUAL_EDIT_CLONE (its JobStore and JOB_ID_PATTERN)."""
    path = str(MANUAL_EDIT_CLONE)
    if path not in sys.path:
        sys.path.insert(0, path)
    return importlib.import_module("manualedit.jobstore")


def _job_store_default():
    """A manual edit JobStore on the registry's root (so a test's temp root carries both)."""
    return _jobstore_module().JobStore(_registry_module().ROOT)


def _recover_uploads_default(store) -> None:
    """Mark every upload set a desktop restart stranded as interrupted, through the manual edit module's recovery.

    Runs only in an app that mounts the manual edit blueprint, whose upload
    runner knows which sets are live in this process; any other host (the
    standalone panel) leaves the sets alone, since another process may be
    running them. A recovery that stops is logged, never raised, as the
    manual edit module's own routes do.

    Parameters:
        store: the manual edit JobStore the caller reads.
    """
    if MANUAL_EDIT_BLUEPRINT not in current_app.blueprints:
        return
    jobstore = _jobstore_module()
    manual_views = importlib.import_module("manualedit.views")
    transfers = importlib.import_module("manualedit.transfers")
    try:
        transfers.recover_stale_uploads(manual_views.get_deps(), store)
    except jobstore.JobStoreError as exc:
        current_app.logger.warning("voyager tools: the stale upload recovery stopped: %s", exc)


def _recover_restarts_default(records: list[dict]) -> list[dict]:
    """Stop every start over a desktop restart stranded, through the manual edit module's recovery.

    Runs only in an app that mounts the manual edit blueprint, whose leg
    registry knows which start overs are live in this process (the same
    rule as _recover_uploads_default); any other host returns the records
    as they are. A recovery that stops (the job store's error, or an OSError,
    ValueError or TypeError from a file it writes) is logged, never raised,
    and that record is returned as read.

    Parameters:
        records: the unfinished job records the caller read.

    Returns:
        The records after recovery, in the same order.
    """
    if MANUAL_EDIT_BLUEPRINT not in current_app.blueprints:
        return records
    jobstore = _jobstore_module()
    manual_views = importlib.import_module("manualedit.views")
    transfers = importlib.import_module("manualedit.transfers")
    store, recovered = deps.job_store(), []
    for record in records:
        try:
            recovered.append(transfers.recover_stale_restarts(manual_views.get_deps(), store, record))
        except (jobstore.JobStoreError, OSError, ValueError, TypeError) as exc:
            current_app.logger.warning("voyager tools: the stranded start over recovery of %s stopped: %s",
                                       record.get(JOB_ID_KEY), exc)
            recovered.append(record)
    return recovered


def _manual_edit_transfers():
    """The manual edit module's transfers library (its one cancel, cancel_refusal and prune_plan)."""
    _jobstore_module()
    return importlib.import_module("manualedit.transfers")


def _manual_edit_deps_default():
    """The Deps kit of the manual edit module mounted in this app, or None when it is not mounted.

    The one cancel judges the pull and the project legs from the leg
    registries of the process they run in, so only an app that mounts the
    manual edit blueprint (the desktop) can cancel; a standalone panel
    refuses (LINE_CANCEL_ELSEWHERE) rather than judge from empty registries.
    """
    if MANUAL_EDIT_BLUEPRINT not in current_app.blueprints:
        return None
    return importlib.import_module("manualedit.views").get_deps()


def _active_jobs_default() -> list[dict]:
    """Every unfinished manual edit job record, oldest first (several projects at once).

    Read strictly: a job file the store cannot read raises, so every guard
    built on this list refuses rather than acting on a transect an unreadable
    project may hold (the display reads answer an empty list instead).
    """
    return list(deps.job_store().active_jobs(strict=True))


def _read_locks_file() -> bool:
    """Whether module_locks.json says this module is locked; fail-open on any read problem."""
    path = os.environ.get(LOCKS_PATH_ENV) or str(LOCKS_PATH)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    locks = data.get("locks") if isinstance(data, dict) else None
    entry = locks.get(MODULE_SLUG) if isinstance(locks, dict) else None
    return isinstance(entry, dict) and entry.get("locked") is True


def _nas_config_default() -> dict:
    """The Carousel's nas.yaml as a dict; {} when missing or unreadable."""
    try:
        with open(NAS_YAML, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _workbench_roots_default() -> list[str]:
    """The Workbench roots to scan: defaults.workbench_root from nas.yaml."""
    defaults = deps.nas_config().get("defaults") or {}
    root = str(defaults.get("workbench_root") or "").strip() if isinstance(defaults, dict) else ""
    return [root] if root and os.path.isabs(root) else []


def _driver_module(name: str):
    """Import driver.<name> from the Carousel clone (lazy: only the workbench tool needs it)."""
    path = str(DRIVER_CLONE)
    if path not in sys.path:
        sys.path.insert(0, path)
    return importlib.import_module(f"driver.{name}")


def _local_snapshot_default(path: str) -> dict:
    """driver.verify.local_snapshot."""
    return _driver_module("verify").local_snapshot(path)


def _remote_snapshot_default(path: str) -> dict:
    """driver.remote.snapshot over an SSH session built from nas.yaml."""
    remote = _driver_module("remote")
    cfg = deps.nas_config()
    ssh = remote.SSH(cfg.get("host"), cfg.get("user"), cfg.get("key"))
    return remote.snapshot(ssh, path)


def _stage_layout_default() -> dict:
    """The roots the Workbench walk skips and the Voyager 2 stage, read from the driver configuration now.

    Every stage driver.space.stage_roots names except the Voyager 1
    Workbench, the fallback bench <workbench_root>/editbench, the manual edit
    setting edit_bench_default, the bench of every unfinished manual edit
    project, and nas.yaml inflight_dir. Fail closed: when any of them cannot
    be read the scan and the delete are refused, never run without them.

    Returns:
        {"excluded": [(label, absolute path)], "voyager2_stage": path or None};
        both empty when nas.yaml names no Workbench root.

    Raises:
        RequestError: 500 when the job store, its settings or the stage layout cannot be read.
    """
    cfg = deps.nas_config()
    defaults = cfg.get("defaults") if isinstance(cfg.get("defaults"), Mapping) else {}
    workbench = str(defaults.get("workbench_root") or "").strip()
    if not workbench:
        return {"excluded": [], "voyager2_stage": None}
    try:
        bench = str(deps.job_store().settings().get(EDIT_BENCH_SETTING) or "").strip() or None
    except Exception as exc:  # noqa: BLE001  (the store's error classes all mean: fix the settings file)
        raise RequestError(f"The manual edit settings cannot be read, so the edit bench is unknown: {exc}",
                           HTTP_SERVER_ERROR)
    job_benches = [str(job.get(JOB_BENCH_KEY) or "").strip() for job in _unfinished_jobs()]
    space = _driver_module("space")
    try:
        roots = space.stage_roots(cfg, bench)
    except (TypeError, ValueError) as exc:
        raise RequestError(f"The Workbench scan is refused because the stage folders cannot be read from "
                           f"nas.yaml: {exc}", HTTP_SERVER_ERROR)
    excluded = [(label, path) for label, path in roots.items() if label != space.WORKBENCH]
    benches = [os.path.join(roots[space.WORKBENCH], space.EDIT_BENCH_SUBFOLDER)] + job_benches
    excluded += [(space.EDIT_BENCH, b) for b in benches if os.path.isabs(b) and (space.EDIT_BENCH, b) not in excluded]
    ledger = cfg.get("inflight_dir")
    if isinstance(ledger, str) and os.path.isabs(ledger):
        excluded.append((LEDGER_LABEL, ledger))
    return {"excluded": excluded, "voyager2_stage": roots.get(space.VOYAGER2_STAGE)}


def _shelf_host_root() -> tuple[str, str]:
    """(NAS host, defaults.shelf_root) from nas.yaml; RequestError 500 when the Shelf root is missing."""
    cfg = deps.nas_config()
    defaults = cfg.get("defaults") if isinstance(cfg.get("defaults"), Mapping) else {}
    root = str(defaults.get("shelf_root") or "").strip()
    if not root.startswith("/"):
        raise RequestError("nas.yaml names no defaults.shelf_root, so no Shelf folder can be renamed.",
                           HTTP_SERVER_ERROR)
    return str(cfg.get("host") or "").strip(), root


def _archive_shelf_output_default(dest: str) -> str | None:
    """Rename a Shelf output folder to <dest>_voy2_archive_<stamp> through the driver's own archive step."""
    cfg = deps.nas_config()
    remote = _driver_module("remote")
    ssh = remote.SSH(cfg.get("host"), cfg.get("user"), cfg.get("key"))
    return _driver_module("phase2").archive_output(ssh, dest, _shelf_host_root()[1])


def _site_name_default(code) -> str:
    """Full site name for a site code from SITE_CODES_CSV; "" when unknown or the table is missing."""
    wanted = code.strip().upper() if isinstance(code, str) else ""
    if not wanted:
        return ""
    try:
        with open(SITE_CODES_CSV, newline="", encoding="utf-8-sig") as fh:
            for line in csv.DictReader(fh):
                if (line.get(SITE_TABLE_CODE_COLUMN) or "").strip().upper() == wanted:
                    return (line.get(SITE_TABLE_NAME_COLUMN) or "").strip()
    except (OSError, csv.Error, UnicodeDecodeError):
        return ""
    return ""


def _record_setting_refused(phase, key, old_version, new_version, by, reason) -> str:
    """The default record_setting: a host with no settings history refuses every parameter save."""
    raise RuntimeError(PARAMS_HISTORY_ONLY_ON_DESKTOP)


class Deps:
    """Every seam the routes reach through; a test replaces attributes with fakes.

    Attributes are plain callables: is_locked() -> bool; is_admin() -> bool;
    registry() -> the registry module; job_store() -> JobStore;
    active_jobs() -> [every unfinished manual edit job record];
    params_store() -> ParamsStore; nas_config() -> dict; workbench_roots()
    -> [paths]; local_snapshot(path) -> snapshot; remote_snapshot(path) ->
    snapshot; rmtree(path); append_processing_log(row); run_chunk(psx,
    label, out_dir) -> result document; stage_layout() -> {"excluded",
    "voyager2_stage"}, the roots the Workbench walk skips and the Voyager 2
    stage; archive_shelf_output(dest) -> the renamed Shelf folder or None;
    site_name(code) -> str;
    prompts_doc() -> Path; now_iso() -> str; record_setting(phase, key,
    old_version, new_version, by, reason) -> the recorded time, the
    settings history line of a parameter save (the desktop wires
    settings_stores.record_params_save; the default refuses);
    recover_uploads(store), the manual edit module's recovery of upload
    sets a desktop restart stranded; recover_restarts(records) -> records,
    its recovery of start overs a desktop restart stranded;
    manual_edit_deps() -> the manual edit module's Deps kit in this app, or
    None when it is not mounted (the one cancel reads its leg registries).
    """

    def __init__(self):
        """Wire the production defaults."""
        self.is_locked: Callable[[], bool] = _read_locks_file
        self.is_admin: Callable[[], bool] = lambda: bool(session.get("admin_unlocked"))
        self.registry: Callable = _registry_module
        self.job_store: Callable = _job_store_default
        self.active_jobs: Callable[[], list] = _active_jobs_default
        self.params_store: Callable = ps.ParamsStore
        self.nas_config: Callable[[], dict] = _nas_config_default
        self.workbench_roots: Callable[[], list] = _workbench_roots_default
        self.local_snapshot: Callable = _local_snapshot_default
        self.remote_snapshot: Callable = _remote_snapshot_default
        self.rmtree: Callable = shutil.rmtree
        self.append_processing_log: Callable = lambda row: clean_workbench.append_processing_log(row, str(PROCESSING_LOG))
        self.run_chunk: Callable = rewind.run_chunk_script
        self.stage_layout: Callable[[], dict] = _stage_layout_default
        self.archive_shelf_output: Callable[[str], str | None] = _archive_shelf_output_default
        self.site_name: Callable[[str], str] = _site_name_default
        self.prompts_doc: Callable[[], Path] = lambda: PROMPTS_DOC
        self.now_iso: Callable[[], str] = lambda: datetime.now(AST).isoformat(timespec="seconds")
        self.record_setting: Callable[..., str] = _record_setting_refused
        self.recover_uploads: Callable = _recover_uploads_default
        self.recover_restarts: Callable[[list], list] = _recover_restarts_default
        self.manual_edit_deps: Callable = _manual_edit_deps_default


deps = Deps()


# --- gates and helpers ------------------------------------------------------------

def lock_body_html() -> str:
    """The locked-module body with the exact platform message and the mailto."""
    return ('<section class="lock-body" role="status">'
            f'<h2 class="lock-name">{html_lib.escape(DISPLAY_NAME)}</h2>'
            '<p class="lock-msg">Under development. Please come back later. '
            f'<a href="mailto:{LOCK_CONTACT_EMAIL}">Email Lauren</a> for questions.</p></section>')


def lock_page_html() -> str:
    """A standalone lock page for the page route."""
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{html_lib.escape(DISPLAY_NAME)} locked</title>"
            "<style>body{margin:0;background:#0a0a0f;color:#F5EDF0;font-family:monospace;display:flex;"
            "align-items:center;justify-content:center;min-height:100vh}.lock-body{text-align:center;"
            "max-width:34rem;padding:2rem}.lock-msg a{color:#FFB3D9}</style></head>"
            f"<body class='vic-lock-page'>{lock_body_html()}</body></html>")


def admin_body_html() -> str:
    """The body shown in place of the tools when the session is not in admin mode."""
    return (f'{ROOT_SECTION_PREFIX} data-admin-required="1"><p class="vt-admin-msg">'
            f'{html_lib.escape(ADMIN_MESSAGE)}</p></section>')


def _locked_json():
    """423 JSON for a locked module."""
    return jsonify({"error": "locked", "message": LOCK_MESSAGE}), HTTP_LOCKED


def _admin_json():
    """401 JSON for a session outside admin mode."""
    return jsonify(dict(ADMIN_ERROR)), HTTP_UNAUTHORISED


def guarded(view):
    """Route decorator: the module lock, then admin mode, then RequestError to JSON.

    A passing admin check sets flask.g.admin_gated, so the host renews admin
    mode when the request is a POST that succeeds (program C spec 4.7).
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        if deps.is_locked():
            return _locked_json()
        if not deps.is_admin():
            return _admin_json()
        g.admin_gated = True
        try:
            return view(*args, **kwargs)
        except RequestError as exc:
            body = {"error": str(exc)}
            body.update(exc.extra)
            return jsonify(body), exc.status
    return wrapper


def slice_section(page: str, open_tag_prefix: str) -> str | None:
    """The <section ...>...</section> block whose open tag starts with the prefix, nested sections balanced."""
    start = page.find(open_tag_prefix)
    if start == -1:
        return None
    tag_re = re.compile(r"<section(?=[\s>])|</section\s*>", re.IGNORECASE)
    depth = 0
    for match in tag_re.finditer(page, start):
        if match.group(0).lower().startswith("</section"):
            depth -= 1
            if depth == 0:
                return page[start:match.end()]
        else:
            depth += 1
    return None


def _json_body() -> dict:
    """The request's JSON object; refuses anything else or an oversized body."""
    if request.content_length and request.content_length > MAX_BODY_BYTES:
        raise RequestError(f"The request body is larger than {MAX_BODY_BYTES} bytes.")
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise RequestError("The request body must be a JSON object.")
    return data


def _initials(body: Mapping) -> str:
    """Validated, upper-cased initials from the body."""
    value = body.get("initials")
    clean = value.strip().upper() if isinstance(value, str) else ""
    if not INITIALS_PATTERN.fullmatch(clean):
        raise RequestError(LINE_INITIALS)
    return clean


def _actor(initials: str) -> str:
    """The registry actor for this page."""
    return ACTOR_PREFIX + initials


def _confirmed(body: Mapping) -> None:
    """A destructive route needs confirm true in its body (the page sends it after its dialog)."""
    if body.get(CONFIRM_KEY) is not True:
        raise RequestError("Confirm the action in the dialog before it runs.")


def _text(body: Mapping, key: str, required: bool = False, max_chars: int = 2000) -> str:
    """A text field from the body, stripped; a missing optional field is ""."""
    value = body.get(key)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise RequestError(f"{key} must be text.")
    value = value.strip()
    if required and not value:
        raise RequestError(f"{key} is required.")
    if len(value) > max_chars:
        raise RequestError(f"{key} is longer than {max_chars} characters.")
    return value


def _readable_id(body: Mapping) -> str:
    """A non-blank readable id from the body."""
    return _text(body, "readable_id", required=True, max_chars=200)


def _rows() -> list[dict]:
    """Every registry row."""
    return deps.registry().load()


def _unfinished_jobs() -> list[dict]:
    """Every unfinished manual edit job record; a store that cannot be read is a RequestError (500)."""
    try:
        return [job for job in deps.active_jobs() if isinstance(job, Mapping)]
    except Exception as exc:  # noqa: BLE001  (the store's own error classes all mean: fix the file)
        raise RequestError(f"The manual edit job files cannot be read: {exc}", HTTP_SERVER_ERROR)


def _job_row_ids(jobs: list[dict]) -> list[str]:
    """The distinct readable ids of every row of the given job records (their union), in order."""
    ids: list[str] = []
    for job in jobs:
        for row in job.get("rows") or []:
            rid = str(row.get("readable_id") or "") if isinstance(row, Mapping) else ""
            if rid and rid not in ids:
                ids.append(rid)
    return ids


def _listed_job_ids() -> tuple[list[str], list[dict]]:
    """The row ids and records of every unfinished manual edit project, for display only.

    An unreadable store answers ([], []) so the state and the row list still
    load; nothing that acts on a row may use this, because a row whose pull
    failed partway sits at awaiting with a bench location and only the job
    record says it belongs to a project. The rewind routes read the store
    through _unfinished_jobs and answer 500 when it cannot be read.
    """
    try:
        jobs = _unfinished_jobs()
    except RequestError:
        return [], []
    return _job_row_ids(jobs), jobs


def _phase(value) -> str:
    """A phase name from a query or body value."""
    phase = str(value or DEFAULT_PHASE).strip()
    if phase not in ps.PHASES:
        raise RequestError(f"phase must be one of {', '.join(ps.PHASES)}.")
    return phase


# --- page and fragment ---------------------------------------------------------------

@voyager_tools_bp.route("/", methods=["GET"])
def page():
    """The full Atlas control panel page (the standalone shape)."""
    if deps.is_locked():
        return lock_page_html(), HTTP_LOCKED
    if not deps.is_admin():
        return _admin_json()
    return render_template("voyager_tools/index.html", base=URL_PREFIX, v=ASSET_VERSION)


def _fragment_tool(value) -> str | None:
    """The fragment variant a ?tool= value names: None (the desktop window), TOOL_PANEL or TOOL_REVIEW.

    Raises:
        RequestError: 400 for any other value, in one sentence.
    """
    tool = value if isinstance(value, str) and value else None
    if tool is not None and tool not in TOOL_NAMES:
        raise RequestError(f"The fragment has no tool named {tool[:40]!r}; ask for tool=panel, tool=review "
                           "or no tool.")
    return tool


def asset_urls(names) -> list[str]:
    """The addresses of this blueprint's static files, each with the asset version as its cache bust."""
    return [f"{URL_PREFIX}/static/{name}?v={ASSET_VERSION}" for name in names]


def gate_payload() -> dict | None:
    """The payload a fragment answers when a gate stops it, or None when the module is open and admin mode is on.

    Returns:
        Locked module: the desktop's locked payload (ui_style locked, 200).
        Outside admin mode: ui_style locked with the admin body and the 401
        keys (need_unlock, admin_required), so a host that shows it as it is
        shows the admin line.
    """
    module = {"name": MODULE_SLUG, "display_name": DISPLAY_NAME}
    if deps.is_locked():
        return {"ui_style": "locked", "locked": True, "html": lock_body_html(), "module": module}
    if not deps.is_admin():
        payload = {"ui_style": "locked", "locked": False, "admin_required": True, "html": admin_body_html(),
                   "module": module}
        payload.update(ADMIN_ERROR)
        return payload
    return None


def fragment_payload(tool: str | None = None) -> dict:
    """The payload the desktop window, or the atlas's Control panel tab, embeds.

    Parameters:
        tool: None for the desktop window (mount VoyagerTools.mount) or
            TOOL_PANEL for the atlas (mount VoyagerTools.mountPanel); the
            same section, scripts and stylesheet either way.
    Returns:
        Locked: the desktop's locked payload (ui_style locked, 200). Outside
        admin mode: ui_style locked with the admin body and the 401 keys, so
        a host that returns it as-is shows the admin line. Otherwise the
        section, the scripts in load order, the stylesheet and the mount.
    Raises:
        RequestError: an unknown tool, or TOOL_REVIEW, whose payload is
            manual_edit_views.review_fragment_payload (400).
    """
    tool = _fragment_tool(tool)
    if tool not in FRAGMENT_TOOLS:
        raise RequestError("The review fragment needs a manual edit project and a transect; ask for "
                           "tool=review with job and psx.")
    mount = FRAGMENT_TOOLS[tool]
    gate = gate_payload()
    if gate is not None:
        return gate
    page_html = render_template("voyager_tools/index.html", base=URL_PREFIX, v=ASSET_VERSION)
    section = slice_section(page_html, ROOT_SECTION_PREFIX)
    if section is None:
        return {"error": "could not extract the Atlas control panel fragment"}
    return {"ui_style": UI_STYLE, "locked": False, "html": section, "base": URL_PREFIX,
            "scripts": asset_urls(PANEL_SCRIPTS), "styles": asset_urls((STATIC_CSS,)), "mount": mount,
            "module": {"name": MODULE_SLUG, "display_name": DISPLAY_NAME}}


def fragment_status(payload: Mapping) -> int:
    """The HTTP status a fragment payload travels with: 401 outside admin mode, 500 on a slice failure, else 200."""
    if payload.get("admin_required"):
        return HTTP_UNAUTHORISED
    if "ui_style" not in payload:
        return HTTP_SERVER_ERROR
    return 200


@voyager_tools_bp.route("/fragment", methods=["GET"])
def fragment():
    """The fragment route: the payload with its status.

    ?tool=panel serves the atlas's Control panel tab; ?tool=review&job=&psx=
    the review content of one transect (the atlas's review sidecar and the
    Review queue pane); no tool the desktop window.
    """
    try:
        tool = _fragment_tool(request.args.get(TOOL_PARAM))
        if tool == TOOL_REVIEW:
            payload = manual_edit_views.review_fragment_payload(request.args.get("job"), request.args.get("psx"))
        else:
            payload = fragment_payload(tool)
    except RequestError as exc:
        body = {"error": str(exc)}
        body.update(exc.extra)
        return jsonify(body), exc.status
    return jsonify(payload), fragment_status(payload)


# --- state ---------------------------------------------------------------------------

@voyager_tools_bp.route("/api/state", methods=["GET"])
@guarded
def api_state():
    """What the left list shows beside each tool: the order lock, the active job, the default versions."""
    registry = deps.registry()
    lock = registry.processing_order_lock()
    _ids, jobs = _listed_job_ids()
    defaults = {}
    for phase in ps.PHASES:
        try:
            defaults[phase] = deps.params_store().default_version(phase)
        except (OSError, ValueError):
            defaults[phase] = ""
    return jsonify({
        "admin": True, "order": {"locked": lock is not None, "lock": lock, "count": len(registry.processing_order())},
        "manual_edit": [{"job_id": job.get("job_id"), "phase": job.get("phase"), "rows": len(job.get("rows") or [])}
                        for job in jobs],
        "params_default": defaults, "workbench_roots": deps.workbench_roots(),
        "prompts_doc": str(deps.prompts_doc()), "now": deps.now_iso(),
    })


# --- tool 1: the processing order ---------------------------------------------------------

def _order_payload(registry) -> dict:
    """The order table plus the summary the lock confirm names."""
    lines = registry.processing_order()
    return order_tool.order_table(registry.load(), lines, registry.processing_order_lock(), deps.site_name)


@voyager_tools_bp.route("/api/order", methods=["GET"])
@guarded
def api_order():
    """The processing order table: started rows, transect blocks, lock, count, first and last ids."""
    return jsonify(_order_payload(deps.registry()))


@voyager_tools_bp.route("/api/order", methods=["POST"])
@guarded
def api_order_save():
    """Save a new block order: {transects: [SITE_T#...], initials}."""
    body = _json_body()
    actor = _actor(_initials(body))
    registry = deps.registry()
    try:
        ids = order_tool.ids_for_transects(registry.load(), registry.processing_order(), body.get("transects"))
        lines = registry.set_processing_order(ids, actor)
    except KeyError as exc:
        raise RequestError(str(exc.args[0]) if exc.args else "unknown row")
    except ValueError as exc:
        raise RequestError(str(exc), HTTP_CONFLICT if LOCKED_PHRASE in str(exc) else HTTP_BAD_REQUEST)
    except TypeError as exc:
        raise RequestError(str(exc))
    summary = order_tool.lock_summary(lines)
    return jsonify({"ok": True, "ordered_ids": [line["readable_id"] for line in lines], **summary})


@voyager_tools_bp.route("/api/order/lock", methods=["POST"])
@guarded
def api_order_lock():
    """Lock the processing order in: {initials, confirm: true}."""
    body = _json_body()
    _confirmed(body)
    actor = _actor(_initials(body))
    registry = deps.registry()
    try:
        lines = registry.lock_processing_order(actor)
    except ValueError as exc:
        raise RequestError(str(exc), HTTP_CONFLICT)
    return jsonify({"ok": True, "lock": registry.processing_order_lock(), **order_tool.lock_summary(lines)})


@voyager_tools_bp.route("/api/order/unlock", methods=["POST"])
@guarded
def api_order_unlock():
    """Unlock the processing order: {initials}."""
    body = _json_body()
    actor = _actor(_initials(body))
    registry = deps.registry()
    try:
        lines = registry.unlock_processing_order(actor)
    except ValueError as exc:
        raise RequestError(str(exc), HTTP_CONFLICT)
    return jsonify({"ok": True, "lock": None, **order_tool.lock_summary(lines)})


# --- tool 2: clean workbench --------------------------------------------------------------

@voyager_tools_bp.route("/api/workbench", methods=["GET"])
@guarded
def api_workbench():
    """Every processing folder under the Workbench roots with its Shelf comparison."""
    registry = deps.registry()
    roots = deps.workbench_roots()
    if not roots:
        return jsonify({"roots": [], "folders": [], "excluded": [], "voyager2_stage": None,
                        "problems": ["No Workbench root is configured in the Carousel's nas.yaml."]})
    layout = deps.stage_layout()
    rows = registry.load()
    result = clean_workbench.plan(roots, rows, deps.local_snapshot, deps.remote_snapshot, registry.run_is_live,
                                  exclude_roots=layout["excluded"])
    stage = layout.get("voyager2_stage")
    result["voyager2_stage"] = (clean_voyager2_stage.plan(stage, rows, registry.fact_sections, deps.local_snapshot,
                                                          deps.remote_snapshot, registry.run_is_live)
                                if stage else None)
    return jsonify(result)


def _inside_real(path: str, root: str | None) -> bool:
    """True when the real path of `path` lies strictly inside the real path of `root`."""
    if not root:
        return False
    real, base = os.path.realpath(path), os.path.realpath(root)
    return real != base and real.startswith(base.rstrip("/") + "/")


def _delete_stage_copy(path: str, stage: str, initials: str, registry) -> dict:
    """Delete one Voyager 2 stage copy after its plan is redone: {deleted, bytes, kind, log_row, problems}."""
    try:
        real, kind = clean_voyager2_stage.anchored(path, stage)
    except (TypeError, ValueError) as exc:
        raise RequestError(str(exc))
    item = {"path": real, "name": os.path.basename(real), "kind": kind, "test": False}
    folder = clean_voyager2_stage.plan_item(item, registry.load(), registry.fact_sections, deps.local_snapshot,
                                            deps.remote_snapshot, registry.run_is_live)
    if not folder["deletable"]:
        raise RequestError(f"{real} is not deletable: {folder['detail']}", HTTP_CONFLICT, {"folder": folder})
    try:
        result = clean_voyager2_stage.delete_folder(real, stage, registry.run_is_live, deps.rmtree)
    except ValueError as exc:
        raise RequestError(str(exc), HTTP_CONFLICT if RUNNING_PHRASE in str(exc) else HTTP_BAD_REQUEST)
    except OSError as exc:
        raise RequestError(str(exc), HTTP_SERVER_ERROR)
    row = clean_voyager2_stage.processing_log_row(result["deleted"], folder["shelf_path"], folder["readable_ids"],
                                                  initials, result["bytes"], kind, deps.now_iso)
    return dict(result, log_row=row, problems=_append_log(row))


def _append_log(row: Mapping) -> list[str]:
    """Append one processing_log row; [] on success, else the failure as one sentence (the action already ran)."""
    try:
        deps.append_processing_log(row)
    except OSError as exc:
        return [str(exc)]
    return []


@voyager_tools_bp.route("/api/workbench/delete", methods=["POST"])
@guarded
def api_workbench_delete():
    """Delete one identical local copy: {path, initials, confirm: true}; the comparison is redone first."""
    body = _json_body()
    _confirmed(body)
    initials = _initials(body)
    path = _text(body, "path", required=True, max_chars=4096)
    registry = deps.registry()
    roots = deps.workbench_roots()
    layout = deps.stage_layout()
    stage = layout.get("voyager2_stage")
    if os.path.isabs(path) and _inside_real(path, stage):
        result = _delete_stage_copy(path, stage, initials, registry)
        return jsonify({"ok": True, **result})
    try:
        real = clean_workbench.anchored(path, roots, exclude_roots=layout["excluded"])
    except (TypeError, ValueError) as exc:
        raise RequestError(str(exc))
    folder = clean_workbench.plan_folder(real, registry.load(), deps.local_snapshot, deps.remote_snapshot,
                                         registry.run_is_live)
    if not folder["deletable"]:
        raise RequestError(f"{real} is not deletable: {folder['detail']}", HTTP_CONFLICT, {"folder": folder})
    try:
        result = clean_workbench.delete_folder(real, roots, registry.run_is_live, deps.rmtree,
                                               exclude_roots=layout["excluded"])
    except ValueError as exc:
        raise RequestError(str(exc), HTTP_CONFLICT if RUNNING_PHRASE in str(exc) else HTTP_BAD_REQUEST)
    except OSError as exc:
        raise RequestError(str(exc), HTTP_SERVER_ERROR)
    row = clean_workbench.processing_log_row(result["deleted"], folder["shelf_path"], folder["readable_ids"],
                                             initials, result["bytes"], deps.now_iso)
    return jsonify({"ok": True, "deleted": result["deleted"], "bytes": result["bytes"], "log_row": row,
                    "problems": _append_log(row)})


# --- tool 3: clean manual edit run ----------------------------------------------------------

def _job_id(body: Mapping) -> str:
    """The job_id the body names, stripped; a value that is not a manual edit job id
    (jobstore.JOB_ID_PATTERN, ME-YYYYMMDD-HHMMSS-initials) is a 400."""
    value = body.get(JOB_ID_KEY)
    text = value.strip() if isinstance(value, str) else ""
    if not _jobstore_module().JOB_ID_PATTERN.fullmatch(text):
        raise RequestError("Name the manual edit project to cancel by its job_id (ME-YYYYMMDD-HHMMSS-initials).")
    return text


def _walkthrough_url(job_id: str) -> str:
    """The address of one manual edit project's walkthrough, where an admin removes the folders a cancel kept."""
    return f"{importlib.import_module('manualedit.views').POPOUT_URL}?job={job_id}"


def _cancel_refusal_of(job: Mapping, manual_deps) -> str:
    """The one cancel's judgement of one project for the page, or LINE_CANCEL_ELSEWHERE without the manual edit module."""
    if manual_deps is None:
        return LINE_CANCEL_ELSEWHERE
    return _manual_edit_transfers().cancel_refusal(manual_deps, job)


def _recovered_for_listing(job: Mapping, store, manual_deps) -> Mapping:
    """The job after the recovery the one cancel runs first (a pull or a checks run a restart stranded).

    Without it the list would judge a checks run a restart killed as still
    running and keep Cancel and prune off, although the cancel itself
    recovers the run and goes ahead. A recovery that fails is logged and the
    job is judged as stored.
    """
    if manual_deps is None:
        return job
    try:
        return _manual_edit_transfers().recover_stale_job(manual_deps, store, job)
    except (_jobstore_module().JobStoreError, OSError, ValueError, TypeError) as exc:
        current_app.logger.warning("voyager tools: the stranded leg recovery of %s stopped: %s",
                                   job.get(JOB_ID_KEY), exc)
        return job


@voyager_tools_bp.route("/api/manual-edit", methods=["GET"])
@guarded
def api_manual_edit():
    """Every unfinished manual edit project, recovered as the one cancel recovers it and summarised with its judgement.

    Answers {"jobs": [summary, ...]}.
    """
    store = deps.job_store()
    manual_deps = deps.manual_edit_deps()
    transfers = _manual_edit_transfers()
    summaries = []
    for job in _unfinished_jobs():
        job = _recovered_for_listing(job, store, manual_deps)
        summaries.append(clean_manual_edit.summarise(job, store.tail_log(job["job_id"], LOG_TAIL),
                                                     refusal=_cancel_refusal_of(job, manual_deps),
                                                     plan=transfers.prune_plan(job)))
    return jsonify({"jobs": summaries})


@voyager_tools_bp.route("/api/manual-edit/cancel", methods=["POST"])
@guarded
def api_manual_edit_cancel():
    """Cancel and prune one unfinished project through the manual edit module's one cancel: {job_id, initials, confirm: true}.

    Answers {ok, job_id, phase, rows_reset, pruned, kept, problems, warnings,
    walkthrough_url}: the folders removed from the edit bench (only through
    the bench removal, and only when untouched), the folders left there, one
    sentence per kept folder, any warning, and the project's walkthrough,
    where an admin removes a kept folder. 409 with the one cancel's sentence when it
    refuses, or when this app does not mount the manual edit module.
    """
    body = _json_body()
    _confirmed(body)
    initials = _initials(body)
    job_id = _job_id(body)
    if job_id not in {job.get("job_id") for job in _unfinished_jobs()}:
        raise RequestError(f"{job_id} is not an unfinished manual edit project; refresh the list.", HTTP_CONFLICT)
    manual_deps = deps.manual_edit_deps()
    if manual_deps is None:
        raise RequestError(LINE_CANCEL_ELSEWHERE, HTTP_CONFLICT)
    transfers = _manual_edit_transfers()
    try:
        answer = transfers.cancel_job(manual_deps, deps.job_store(), job_id, initials,
                                      clean_manual_edit.CANCEL_REASON, prune=True)
    except (transfers.TransferRefused, _jobstore_module().TransitionError) as exc:
        raise RequestError(str(exc), HTTP_CONFLICT)
    return jsonify({"ok": True, "job_id": job_id, "phase": answer["job"]["phase"],
                    "rows_reset": answer["rows_reset"], "pruned": answer["removed"],
                    "kept": answer["kept_on_bench"], "problems": list(answer["kept_reasons"].values()),
                    "warnings": answer["warnings"], "walkthrough_url": _walkthrough_url(job_id)})


# --- tools 4 and 5: rewind and spot redo ------------------------------------------------------

def _rewind_rows(registry, active_ids) -> list[dict]:
    """Rows a rewind could apply to: every row Voyager 1 has touched, with the facts the page needs.

    manual_edit_under_way is True for a row whose manual edit status is one
    after the pull (registry.MANUAL_EDIT_IN_FLIGHT), which the rewind refuses,
    so the page can mark it without copying the status words.
    """
    out = []
    for row in registry.load():
        if not rewind.row_started(row) and not str(row.get("psx_file") or "").strip():
            continue
        location = str(row.get("processing_location") or "")
        out.append({
            "readable_id": row.get("readable_id", ""), "site": row.get("site", ""),
            "site_name": deps.site_name(row.get("site", "")), "transect": row.get("transect", ""),
            "year": row.get("year", ""), "season_token": row.get("season_token", ""),
            "step1_status": row.get("step1_status", ""), "stage": row.get("stage", ""),
            "manual_edit_status": row.get("manual_edit_status", ""),
            "manual_edit_under_way": str(row.get(registry.MANUAL_EDIT_STATUS_COLUMN) or "").strip()
            in registry.MANUAL_EDIT_IN_FLIGHT,
            "run_state": registry.run_state(row),
            "processing_location": location, "local": rewind.is_local_folder(location),
            "in_job": row.get("readable_id") in active_ids, "psx_file": row.get("psx_file", ""),
            "step2_status": row.get("step2_status", ""),
        })
    return out


@voyager_tools_bp.route("/api/rewind/rows", methods=["GET"])
@guarded
def api_rewind_rows():
    """The rows the rewind and spot redo selects list."""
    active_ids, _jobs = _listed_job_ids()
    return jsonify({"rows": _rewind_rows(deps.registry(), set(active_ids)), "targets": [
        {"value": t, "label": REWIND_TARGET_WORDS[t]} for t in REWIND_TARGETS]})


def _rewind_row(registry, body: Mapping) -> tuple[dict, str]:
    """The row named by the body and the validated target."""
    rid = _readable_id(body)
    target = str(body.get("target") or rewind.TARGET_BEFORE_STEP1)
    if target not in REWIND_TARGETS:
        raise RequestError(f"target must be one of {', '.join(REWIND_TARGETS)}.")
    row = registry.get(rid)
    if row is None:
        raise RequestError(f"No registry row has the id {rid}.", HTTP_NOT_FOUND)
    return row, target


def _refuse_rewind(registry, row: dict, target: str, active_ids: list[str]) -> None:
    """Raise the 409 refusal (with the pull-back command when that is the fix) when the row cannot rewind.

    Parameters:
        active_ids: the row ids of every unfinished manual edit project (_job_row_ids).
    """
    why = rewind.refusal(row, target, registry.run_is_live, active_ids, registry_mod=registry)
    if why:
        extra = {"pullback_command": rewind.pullback_command(row["readable_id"])} if "not on this machine" in why else {}
        raise RequestError(why, HTTP_CONFLICT, extra)


@voyager_tools_bp.route("/api/rewind/plan", methods=["POST"])
@guarded
def api_rewind_plan():
    """Dry run: {readable_id, target} -> what would change."""
    body = _json_body()
    registry = deps.registry()
    row, target = _rewind_row(registry, body)
    if target == rewind_voyager2.TARGET_BEFORE_STEP2:
        return jsonify({"ok": True, "plan": _voyager2_call(rewind_voyager2.plan_or_refuse, row["readable_id"],
                                                           registry, **_shelf_kwargs())})
    return jsonify({"ok": True, "plan": _voyager1_plan(registry, row, target)})


def _voyager1_plan(registry, row: dict, target: str) -> dict:
    """The Voyager 1 rewind dry run after its refusals (409 when there is nothing to rewind)."""
    _refuse_rewind(registry, row, target, _job_row_ids(_unfinished_jobs()))
    the_plan = rewind.plan(row, target, registry_mod=registry)
    if the_plan["empty"]:
        raise RequestError(f"{row['readable_id']} has nothing to rewind {the_plan['target_words']}.", HTTP_CONFLICT)
    return the_plan


def _shelf_kwargs() -> dict:
    """The shelf_host and shelf_root keywords of the Voyager 2 rewind, from nas.yaml."""
    host, root = _shelf_host_root()
    return {"shelf_host": host, "shelf_root": root}


def _voyager2_call(fn: Callable, *args, **kwargs):
    """Call a rewind_voyager2 function; a refusal is a 409 and a part-way stop a 500, each in its own words."""
    try:
        return fn(*args, **kwargs)
    except rewind_voyager2.Voyager2Refused as exc:
        raise RequestError(str(exc), HTTP_CONFLICT)
    except OSError as exc:
        raise RequestError(str(exc), HTTP_SERVER_ERROR)


def _apply_voyager2_rewind(body: Mapping, registry, initials: str) -> dict:
    """The rewind before Voyager 2, then its processing_log row when a Shelf folder was renamed."""
    manifest = _voyager2_call(rewind_voyager2.apply, _readable_id(body), registry, _actor(initials), initials,
                              deps.archive_shelf_output, confirm_vigil=body.get(CONFIRM_VIGIL_KEY) is True,
                              **_shelf_kwargs())
    manifest["problems"] = []
    if manifest["archived_to"]:
        row = rewind_voyager2.processing_log_row(manifest, _shelf_host_root()[0], initials, deps.now_iso)
        manifest["problems"] = _append_log(row)
    return manifest


def _apply_rewind(body: Mapping, spot: bool) -> dict:
    """Shared apply for the rewind and spot redo routes."""
    _confirmed(body)
    initials = _initials(body)
    registry = deps.registry()
    if spot:
        body = dict(body, target=rewind.TARGET_BEFORE_STEP1)
    row, target = _rewind_row(registry, body)
    if target == rewind_voyager2.TARGET_BEFORE_STEP2:
        return _apply_voyager2_rewind(body, registry, initials)
    active_ids = _job_row_ids(_unfinished_jobs())
    _refuse_rewind(registry, row, target, active_ids)
    try:
        if spot:
            return rewind.spot_redo(row, registry, _actor(initials), initials, deps.run_chunk, registry.run_is_live,
                                    active_ids)
        return rewind.apply(row, target, registry, _actor(initials), deps.run_chunk, registry.run_is_live, active_ids)
    except rewind.RewindRefused as exc:
        raise RequestError(str(exc), HTTP_CONFLICT)
    except OSError as exc:
        raise RequestError(str(exc), HTTP_SERVER_ERROR)


@voyager_tools_bp.route("/api/rewind/apply", methods=["POST"])
@guarded
def api_rewind_apply():
    """Apply a rewind: {readable_id, target, initials, confirm: true} -> the manifest."""
    return jsonify({"ok": True, "manifest": _apply_rewind(_json_body(), spot=False)})


def _spot_phase(body: Mapping) -> str:
    """The pipeline a spot redo names: voyager1 (the default) or voyager2."""
    phase = str(body.get("phase") or PHASE_VOYAGER1)
    if phase not in SPOT_REDO_PHASES:
        raise RequestError(f"phase must be one of {', '.join(SPOT_REDO_PHASES)}.")
    return phase


@voyager_tools_bp.route("/api/spot-redo/plan", methods=["POST"])
@guarded
def api_spot_redo_plan():
    """Spot redo dry run: {readable_id, phase} -> the rewind plan (voyager1) or the redo request plan (voyager2)."""
    body = _json_body()
    phase = _spot_phase(body)
    registry = deps.registry()
    row, _target = _rewind_row(registry, dict(body, target=rewind.TARGET_BEFORE_STEP1))
    if phase == PHASE_VOYAGER2:
        return jsonify({"ok": True, "plan": _voyager2_call(rewind_voyager2.spot_redo_plan, row["readable_id"], registry)})
    return jsonify({"ok": True, "plan": _voyager1_plan(registry, row, rewind.TARGET_BEFORE_STEP1)})


@voyager_tools_bp.route("/api/spot-redo", methods=["POST"])
@guarded
def api_spot_redo():
    """Spot redo: {readable_id, phase, initials, confirm: true} -> the manifest (voyager1) or the redo request (voyager2)."""
    body = _json_body()
    if _spot_phase(body) == PHASE_VOYAGER1:
        return jsonify({"ok": True, "manifest": _apply_rewind(body, spot=True)})
    _confirmed(body)
    initials = _initials(body)
    registry = deps.registry()
    row, _target = _rewind_row(registry, dict(body, target=rewind.TARGET_BEFORE_STEP1))
    return jsonify({"ok": True, "manifest": _voyager2_call(rewind_voyager2.spot_redo, row["readable_id"], registry,
                                                           _actor(initials), initials, now_iso=deps.now_iso)})


# --- tool 6: the prompts doc -------------------------------------------------------------------

_MD_FENCE = re.compile(r"^```")
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_MD_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_MD_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_MD_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_MD_CODE_SPAN = re.compile(r"`([^`]+)`")
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _inline(text: str) -> str:
    """Escape a line and render code spans, bold and links."""
    parts, last = [], 0
    for match in _MD_CODE_SPAN.finditer(text):
        parts.append(_inline_plain(text[last:match.start()]))
        parts.append(f"<code>{html_lib.escape(match.group(1))}</code>")
        last = match.end()
    parts.append(_inline_plain(text[last:]))
    return "".join(parts)


def _inline_plain(text: str) -> str:
    """Escape text and render bold and links (no code spans inside)."""
    escaped = html_lib.escape(text)
    escaped = _MD_BOLD.sub(r"<strong>\1</strong>", escaped)
    return _MD_LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', escaped)


def _close_lists(state: dict, out: list) -> None:
    """Close an open list or paragraph."""
    if state["list"]:
        out.append(f"</{state['list']}>")
        state["list"] = ""
    if state["para"]:
        out.append("<p>" + " ".join(state["para"]) + "</p>")
        state["para"] = []


def _table_html(rows: list[str]) -> str:
    """A pipe table (header, separator, body rows) as HTML."""
    cells = [[_inline(c.strip()) for c in r.strip().strip("|").split("|")] for r in rows]
    if len(cells) < 2:
        return ""
    head = "".join(f"<th>{c}</th>" for c in cells[0])
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in cells[2:])
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_markdown(text: str) -> str:
    """A small, dependency-free Markdown renderer: headings, paragraphs, bullet
    and numbered lists, fenced code, pipe tables, code spans, bold and links.
    Every character of the document is escaped, so a hostile file renders as text."""
    out: list = []
    state = {"list": "", "para": [], "code": None, "table": []}
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if state["code"] is not None:
            if _MD_FENCE.match(line):
                out.append("<pre><code>" + html_lib.escape("\n".join(state["code"])) + "</code></pre>")
                state["code"] = None
            else:
                state["code"].append(line)
            continue
        if _MD_TABLE_ROW.match(line) and (state["table"] or not state["para"]):
            _close_lists(state, out)
            state["table"].append(line)
            continue
        if state["table"]:
            out.append(_table_html(state["table"]) if len(state["table"]) > 1 and _MD_TABLE_SEP.match(state["table"][1])
                       else "".join(f"<p>{_inline(r)}</p>" for r in state["table"]))
            state["table"] = []
        if _MD_FENCE.match(line):
            _close_lists(state, out)
            state["code"] = []
            continue
        heading = _MD_HEADING.match(line)
        if heading:
            _close_lists(state, out)
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2).strip())}</h{level}>")
            continue
        bullet, numbered = _MD_BULLET.match(line), _MD_NUMBERED.match(line)
        if bullet or numbered:
            kind = "ul" if bullet else "ol"
            if state["list"] != kind:
                _close_lists(state, out)
                out.append(f"<{kind}>")
                state["list"] = kind
            out.append(f"<li>{_inline((bullet or numbered).group(1))}</li>")
            continue
        if not line.strip():
            _close_lists(state, out)
            continue
        if state["list"]:
            out.append(f"</{state['list']}>")
            state["list"] = ""
        state["para"].append(_inline(line.strip()))
    if state["code"] is not None:
        out.append("<pre><code>" + html_lib.escape("\n".join(state["code"])) + "</code></pre>")
    if state["table"]:
        out.append(_table_html(state["table"]) if len(state["table"]) > 1 and _MD_TABLE_SEP.match(state["table"][1])
                   else "".join(f"<p>{_inline(r)}</p>" for r in state["table"]))
    _close_lists(state, out)
    return "\n".join(out)


@voyager_tools_bp.route("/api/prompts", methods=["GET"])
@guarded
def api_prompts():
    """The prompts document rendered to HTML, with its path and modification time."""
    path = Path(deps.prompts_doc())
    if not path.is_file():
        return jsonify({"exists": False, "path": str(path), "html": "", "updated_at": "",
                        "error": f"The prompts document is missing at {path}."})
    if path.stat().st_size > MAX_PROMPTS_BYTES:
        raise RequestError(f"The prompts document at {path} is larger than {MAX_PROMPTS_BYTES} bytes.", HTTP_SERVER_ERROR)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RequestError(f"The prompts document at {path} could not be read: {exc}", HTTP_SERVER_ERROR)
    updated = datetime.fromtimestamp(path.stat().st_mtime, AST).strftime("%Y-%m-%d %H:%M AST")
    return jsonify({"exists": True, "path": str(path), "html": render_markdown(text), "updated_at": updated})


# --- tool 7: the parameter defaults ----------------------------------------------------------------

def _entry_dict(entry: ps.ParamsEntry) -> dict:
    """A listing row as JSON."""
    return asdict(entry)


def _file_dict(loaded: ps.ParamsFile, store: ps.ParamsStore) -> dict:
    """A loaded parameter file as JSON (values, text, header fields, URL)."""
    try:
        url = store.version_url(loaded.phase, loaded.ref)
    except (OSError, ValueError):
        url = ""
    return {"ref": loaded.ref, "kind": loaded.kind, "version": loaded.version, "rel_path": loaded.rel_path,
            "values": loaded.values, "text": loaded.text, "branch": loaded.branch, "run_id": loaded.run_id,
            "base_ref": loaded.base_ref, "description": loaded.description, "why": loaded.why,
            "saved_at": loaded.saved_at, "saved_by": loaded.saved_by, "status": loaded.status, "url": url}


def _visible_schema(phase: str) -> dict | None:
    """The editor schema without hidden keys (the project structure sets those)."""
    schema = ps.load_schema(phase)
    if schema is None:
        return None
    return {"phase": schema.get("phase"), "template_version": schema.get("template_version"),
            "schema_version": schema.get("schema_version"), "sections": schema.get("sections") or [],
            "keys": [k for k in schema.get("keys") or [] if not k.get("hidden")]}


def _deep_merge(base: dict, patch: Mapping) -> dict:
    """A copy of base with patch merged in (nested mappings merge, everything else replaces)."""
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _deep_merge(dict(out[key]), value)
        else:
            out[key] = value
    return out


def _values_from_body(body: Mapping, phase: str, store: ps.ParamsStore) -> dict:
    """The values a save uses: the editor's values (or the base ref's) with the YAML box merged over them."""
    values = body.get("values")
    if values is None:
        base_ref = str(body.get("base_ref") or "default")
        try:
            values = store.load(phase, base_ref).values
        except (FileNotFoundError, ValueError) as exc:
            raise RequestError(str(exc))
    if not isinstance(values, dict):
        raise RequestError("values must be a JSON object of parameter values.")
    text = _text(body, "yaml_text", max_chars=200000)
    if text:
        try:
            patch = ps.load_yaml_mapping(text, "the YAML box")
        except ValueError as exc:
            raise RequestError(f"The YAML box does not parse: {exc}")
        values = _deep_merge(values, patch)
    problems = ps.validate_values(phase, values)
    if problems:
        raise RequestError("The values do not pass the schema: " + "; ".join(problems[:5]),
                           HTTP_BAD_REQUEST, {"problems": problems})
    return values


def _store():
    """The parameter store, or a 500 when the checkout is missing."""
    try:
        return deps.params_store()
    except FileNotFoundError as exc:
        raise RequestError(str(exc), HTTP_SERVER_ERROR)


@voyager_tools_bp.route("/api/params", methods=["GET"])
@guarded
def api_params():
    """Every version, branch and custom set of a phase, the default marked, plus the schema and the default's values."""
    phase = _phase(request.args.get("phase"))
    store = _store()
    try:
        entries = store.list_versions(phase)
        default = store.load(phase, "default")
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise RequestError(str(exc), HTTP_SERVER_ERROR)
    return jsonify({"phase": phase, "default_version": default.version, "entries": [_entry_dict(e) for e in entries],
                    "schema": _visible_schema(phase), "default": _file_dict(default, store), "repo_url": store.repo_url})


@voyager_tools_bp.route("/api/params/load", methods=["GET"])
@guarded
def api_params_load():
    """One parameter file by ref, with its diff against the default."""
    phase = _phase(request.args.get("phase"))
    ref = str(request.args.get("ref") or "default").strip()
    store = _store()
    try:
        loaded = store.load(phase, ref)
        diff = store.diff("default", loaded.values, phase=phase)
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise RequestError(str(exc), HTTP_NOT_FOUND if isinstance(exc, FileNotFoundError) else HTTP_BAD_REQUEST)
    return jsonify({"file": _file_dict(loaded, store), "diff": diff})


@voyager_tools_bp.route("/api/params/validate", methods=["POST"])
@guarded
def api_params_validate():
    """Check a values set (editor values plus the YAML box) against the schema and diff it against the default."""
    body = _json_body()
    phase = _phase(body.get("phase"))
    store = _store()
    try:
        values = _values_from_body(body, phase, store)
    except RequestError as exc:
        if "problems" in exc.extra:
            return jsonify({"ok": False, "problems": exc.extra["problems"], "error": str(exc)})
        raise
    diff = store.diff("default", values, phase=phase)
    level = ps.classify_change(ps.flatten(store.load(phase, "default").values), ps.flatten(values))
    return jsonify({"ok": True, "problems": [], "values": values, "diff": diff, "level": level})


def _branch_version(store: ps.ParamsStore, phase: str, slug: str) -> str | None:
    """The newest version of branch <slug>, or None when the branch has no file yet."""
    for entry in store.list_versions(phase):
        if entry.kind == "branch" and entry.branch == slug:
            return entry.version
    return None


def _record_or_undo(store: ps.ParamsStore, paths_before: list[str], history: tuple) -> None:
    """Write the settings history line of a parameter save, or undo the files that save wrote.

    Parameters:
        store: the store that just wrote the files.
        paths_before: store.pending_paths taken before the save, left alone.
        history: the arguments of deps.record_setting.
    Raises:
        RequestError: 500 with the history's sentence when the line could
            not be written; the new files are removed or restored first.
    """
    try:
        deps.record_setting(*history)
    except (RuntimeError, ValueError, OSError) as exc:
        try:
            store.discard_pending(only=[p for p in store.pending_paths if p not in paths_before])
        except (ps.GitError, OSError, ValueError) as undo_exc:
            raise RequestError(f"{exc} Undoing the parameter files also failed ({undo_exc}); check "
                               f"{store.root} by hand.", HTTP_SERVER_ERROR) from exc
        raise RequestError(str(exc), HTTP_SERVER_ERROR) from exc


def _commit(store: ps.ParamsStore, message: str) -> dict:
    """Commit and push what the store wrote; a git failure is reported, never raised."""
    try:
        return store.commit_and_push(message)
    except ps.GitError as exc:
        return {"committed": False, "pushed": False, "commit": "", "paths": store.pending_paths, "detail": str(exc)}


@voyager_tools_bp.route("/api/params/branch", methods=["POST"])
@guarded
def api_params_branch():
    """Save as branch: {phase, base_ref, slug, description, why, values, yaml_text, initials}."""
    body = _json_body()
    phase = _phase(body.get("phase"))
    initials = _initials(body)
    slug = _text(body, "slug", required=True, max_chars=40)
    description = _text(body, "description", required=True)
    why = _text(body, "why", required=True)
    base_ref = _text(body, "base_ref", max_chars=200) or "default"
    store = _store()
    values = _values_from_body(dict(body, base_ref=base_ref), phase, store)
    paths_before = store.pending_paths
    try:
        old_version = _branch_version(store, phase, slug)
        saved = store.save_branch(phase, base_ref, slug, description, why, values, initials)
    except ps.StoreLocked as exc:
        raise RequestError(str(exc), HTTP_CONFLICT)
    except (TypeError, ValueError, FileNotFoundError) as exc:
        raise RequestError(str(exc))
    _record_or_undo(store, paths_before, (phase, PARAMS_BRANCH_KEY.format(phase=phase, slug=slug), old_version,
                                          saved.version, initials, why))
    git = _commit(store, f"voyagerparams: {phase} branch {slug} {saved.version} by {initials}: {description}")
    return jsonify({"ok": True, "file": _file_dict(saved, store), "git": git})


@voyager_tools_bp.route("/api/params/default", methods=["POST"])
@guarded
def api_params_default():
    """Save as default: {phase, description, values, yaml_text, initials, confirm: true}."""
    body = _json_body()
    _confirmed(body)
    phase = _phase(body.get("phase"))
    initials = _initials(body)
    description = _text(body, "description", required=True)
    store = _store()
    values = _values_from_body(dict(body, base_ref="default"), phase, store)
    paths_before = store.pending_paths
    try:
        current = store.load(phase, "default")
        level = ps.classify_change(ps.flatten(current.values), ps.flatten(values))
        saved = store.save_default(phase, values, description, initials)
    except ps.StoreLocked as exc:
        raise RequestError(str(exc), HTTP_CONFLICT)
    except (TypeError, ValueError, FileNotFoundError) as exc:
        raise RequestError(str(exc))
    _record_or_undo(store, paths_before, (phase, PARAMS_DEFAULT_KEY.format(phase=phase), current.version,
                                          saved.version, initials, description))
    git = _commit(store, f"voyagerparams: {phase} default {saved.version} ({level}) by {initials}: {description}")
    return jsonify({"ok": True, "file": _file_dict(saved, store), "level": level, "git": git})


# --- tool 8: the camera model table ------------------------------------------------------------------

@voyager_tools_bp.route("/api/seasons", methods=["GET"])
@guarded
def api_seasons():
    """One line per season with its camera model, preprocessing and notes."""
    registry = deps.registry()
    return jsonify({"seasons": camera_tool.season_table(registry.load(), registry.seasons())})


@voyager_tools_bp.route("/api/seasons", methods=["POST"])
@guarded
def api_seasons_save():
    """Record a season's cells: {season_key, camera_model, preprocessing, notes, initials}."""
    body = _json_body()
    actor = _actor(_initials(body))
    key = _text(body, "season_key", required=True, max_chars=20)
    try:
        fields = camera_tool.clean_season_fields(body)
        changed = deps.registry().set_season(key, fields, actor)
    except (TypeError, ValueError, KeyError) as exc:
        raise RequestError(str(exc.args[0]) if exc.args else str(exc))
    line = deps.registry().season(key)
    return jsonify({"ok": True, "changed": changed, "season": line, "label": camera_tool.season_label(key)})


__all__ = [
    "voyager_tools_bp", "deps", "Deps", "RequestError", "fragment_payload", "fragment_status", "guarded",
    "slice_section", "render_markdown", "lock_body_html", "lock_page_html", "admin_body_html", "gate_payload",
    "asset_urls",
    "URL_PREFIX", "MODULE_SLUG", "DISPLAY_NAME", "UI_STYLE", "ASSET_VERSION", "ROOT_SECTION_PREFIX",
    "LOCK_MESSAGE", "ADMIN_ERROR", "ADMIN_MESSAGE", "PROMPTS_DOC", "PARAMS_HISTORY_ONLY_ON_DESKTOP",
]

# The manual edit routes (the Timelines and Review queue panes, the review
# fragment) register on the same blueprint; imported last because they use
# the gates and seams defined above.
from . import manual_edit_views  # noqa: E402,F401  (registers routes on voyager_tools_bp)
