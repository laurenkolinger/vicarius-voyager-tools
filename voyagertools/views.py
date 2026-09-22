#!/usr/bin/env python3
"""
Module: voyagertools/views.py
Purpose: The Voyager tools web layer (program spec section 7): one Flask
         blueprint, voyager_tools_bp, mounted at /voyager-tools by the
         VICARIUS desktop (vicarius_ui_os/voyager_tools_views.py) and by
         standalone_app.py. It serves the page, the fragment the desktop
         window embeds, and the JSON API of the eight tools: the processing
         order, clean workbench, clean manual edit run, rewind, spot redo,
         the prompts doc, the parameter defaults and the camera model table.
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
from flask import Blueprint, jsonify, render_template, request, session

from . import camera_tool, clean_manual_edit, clean_workbench, order_tool, rewind
from . import paramsstore as ps

# --- constants ---------------------------------------------------------------

BLUEPRINT_NAME = "voyager_tools"
URL_PREFIX = "/voyager-tools"
MODULE_SLUG = "voyager_tools"
DISPLAY_NAME = "Voyager tools"
UI_STYLE = "voyager_tools"
ASSET_VERSION = "20260906a"
ROOT_SECTION_PREFIX = '<section class="voyager-tools"'
STATIC_JS = "voyager_tools.js"
STATIC_CSS = "voyager_tools.css"

_PKG = Path(__file__).resolve().parent
CLONE_ROOT = _PKG.parent
TEMPLATES_DIR = CLONE_ROOT / "templates"
STATIC_DIR = CLONE_ROOT / "static"
VICARIUS_ROOT = Path(os.environ.get("VICARIUS_ROOT") or "/mnt/rip/vicarius_drive/vicarius")
DRIVE_ROOT = VICARIUS_ROOT.parent
REGISTRY_LIB_DIR = VICARIUS_ROOT / "_METADATA" / "3d"
MANUAL_EDIT_CLONE = VICARIUS_ROOT / "modules" / "manual_edit" / "github_repo"
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
ADMIN_MESSAGE = "Admin mode is required to open Voyager tools. Unlock admin from the ADMIN chip, then open it again."
DEFAULT_PHASE = "voyager1"
INITIALS_PATTERN = re.compile(r"^[A-Za-z0-9]{1,8}$")
ACTOR_PREFIX = "voyager_tools:"
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_PROMPTS_BYTES = 4 * 1024 * 1024
LOG_TAIL = 20
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


def _job_store_default():
    """A manual edit JobStore on the registry's root (so a test's temp root carries both)."""
    path = str(MANUAL_EDIT_CLONE)
    if path not in sys.path:
        sys.path.insert(0, path)
    jobstore = importlib.import_module("manualedit.jobstore")
    return jobstore.JobStore(_registry_module().ROOT)


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


class Deps:
    """Every seam the routes reach through; a test replaces attributes with fakes.

    Attributes are plain callables: is_locked() -> bool; is_admin() -> bool;
    registry() -> the registry module; job_store() -> JobStore;
    params_store() -> ParamsStore; nas_config() -> dict; workbench_roots()
    -> [paths]; local_snapshot(path) -> snapshot; remote_snapshot(path) ->
    snapshot; rmtree(path); append_processing_log(row); run_chunk(psx,
    label, out_dir) -> result document; site_name(code) -> str;
    prompts_doc() -> Path; now_iso() -> str.
    """

    def __init__(self):
        """Wire the production defaults."""
        self.is_locked: Callable[[], bool] = _read_locks_file
        self.is_admin: Callable[[], bool] = lambda: bool(session.get("admin_unlocked"))
        self.registry: Callable = _registry_module
        self.job_store: Callable = _job_store_default
        self.params_store: Callable = ps.ParamsStore
        self.nas_config: Callable[[], dict] = _nas_config_default
        self.workbench_roots: Callable[[], list] = _workbench_roots_default
        self.local_snapshot: Callable = _local_snapshot_default
        self.remote_snapshot: Callable = _remote_snapshot_default
        self.rmtree: Callable = shutil.rmtree
        self.append_processing_log: Callable = lambda row: clean_workbench.append_processing_log(row, str(PROCESSING_LOG))
        self.run_chunk: Callable = rewind.run_chunk_script
        self.site_name: Callable[[str], str] = _site_name_default
        self.prompts_doc: Callable[[], Path] = lambda: PROMPTS_DOC
        self.now_iso: Callable[[], str] = lambda: datetime.now(AST).isoformat(timespec="seconds")


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
    """Route decorator: the module lock, then admin mode, then RequestError to JSON."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if deps.is_locked():
            return _locked_json()
        if not deps.is_admin():
            return _admin_json()
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
    if not isinstance(value, str) or not INITIALS_PATTERN.match(value.strip()):
        raise RequestError("Type your initials (one to eight letters or digits) before saving.")
    return value.strip().upper()


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


def _active_job_ids() -> tuple[list[str], dict | None]:
    """Ids in the active manual edit job and the job record; ([], None) when none or unreadable."""
    try:
        job = deps.job_store().active_job()
    except Exception:  # noqa: BLE001  (a corrupt pointer must not take the other tools down)
        return [], None
    if not job:
        return [], None
    ids = [str(r.get("readable_id") or "") for r in job.get("rows") or [] if isinstance(r, Mapping)]
    return ids, job


def _phase(value) -> str:
    """A phase name from a query or body value."""
    phase = str(value or DEFAULT_PHASE).strip()
    if phase not in ps.PHASES:
        raise RequestError(f"phase must be one of {', '.join(ps.PHASES)}.")
    return phase


# --- page and fragment ---------------------------------------------------------------

@voyager_tools_bp.route("/", methods=["GET"])
def page():
    """The full Voyager tools page (the standalone shape)."""
    if deps.is_locked():
        return lock_page_html(), HTTP_LOCKED
    if not deps.is_admin():
        return _admin_json()
    return render_template("voyager_tools/index.html", base=URL_PREFIX, v=ASSET_VERSION)


def fragment_payload() -> dict:
    """The payload the desktop window embeds (shared with the host's ui.style branch).

    Locked: the desktop's locked payload (ui_style locked, 200). Outside admin
    mode: ui_style locked with the admin body and the 401 keys, so a host
    that returns it as-is shows the admin message. Otherwise the section,
    the script and the stylesheet.
    """
    if deps.is_locked():
        return {"ui_style": "locked", "locked": True, "html": lock_body_html(),
                "module": {"name": MODULE_SLUG, "display_name": DISPLAY_NAME}}
    if not deps.is_admin():
        payload = {"ui_style": "locked", "locked": False, "admin_required": True, "html": admin_body_html(),
                   "module": {"name": MODULE_SLUG, "display_name": DISPLAY_NAME}}
        payload.update(ADMIN_ERROR)
        return payload
    page_html = render_template("voyager_tools/index.html", base=URL_PREFIX, v=ASSET_VERSION)
    section = slice_section(page_html, ROOT_SECTION_PREFIX)
    if section is None:
        return {"error": "could not extract the Voyager tools fragment"}
    return {"ui_style": UI_STYLE, "locked": False, "html": section, "base": URL_PREFIX,
            "scripts": [f"{URL_PREFIX}/static/{STATIC_JS}?v={ASSET_VERSION}"],
            "styles": [f"{URL_PREFIX}/static/{STATIC_CSS}?v={ASSET_VERSION}"],
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
    """The fragment route: the payload with its status."""
    payload = fragment_payload()
    return jsonify(payload), fragment_status(payload)


# --- state ---------------------------------------------------------------------------

@voyager_tools_bp.route("/api/state", methods=["GET"])
@guarded
def api_state():
    """What the left list shows beside each tool: the order lock, the active job, the default versions."""
    registry = deps.registry()
    lock = registry.processing_order_lock()
    ids, job = _active_job_ids()
    defaults = {}
    for phase in ps.PHASES:
        try:
            defaults[phase] = deps.params_store().default_version(phase)
        except (OSError, ValueError):
            defaults[phase] = ""
    return jsonify({
        "admin": True, "order": {"locked": lock is not None, "lock": lock, "count": len(registry.processing_order())},
        "manual_edit": {"job_id": job["job_id"], "phase": job["phase"], "rows": len(ids)} if job else None,
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
        return jsonify({"roots": [], "folders": [], "problems": ["No Workbench root is configured in the Carousel's nas.yaml."]})
    return jsonify(clean_workbench.plan(roots, registry.load(), deps.local_snapshot, deps.remote_snapshot,
                                        registry.run_is_live))


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
    try:
        real = clean_workbench.anchored(path, roots)
    except (TypeError, ValueError) as exc:
        raise RequestError(str(exc))
    folder = clean_workbench.plan_folder(real, registry.load(), deps.local_snapshot, deps.remote_snapshot,
                                         registry.run_is_live)
    if not folder["deletable"]:
        raise RequestError(f"{real} is not deletable: {folder['detail']}", HTTP_CONFLICT, {"folder": folder})
    try:
        result = clean_workbench.delete_folder(real, roots, registry.run_is_live, deps.rmtree)
    except ValueError as exc:
        raise RequestError(str(exc), HTTP_CONFLICT if RUNNING_PHRASE in str(exc) else HTTP_BAD_REQUEST)
    except OSError as exc:
        raise RequestError(str(exc), HTTP_SERVER_ERROR)
    row = clean_workbench.processing_log_row(result["deleted"], folder["shelf_path"], folder["readable_ids"],
                                             initials, result["bytes"], deps.now_iso)
    problems = []
    try:
        deps.append_processing_log(row)
    except OSError as exc:
        problems.append(str(exc))
    return jsonify({"ok": True, "deleted": result["deleted"], "bytes": result["bytes"], "log_row": row,
                    "problems": problems})


# --- tool 3: clean manual edit run ----------------------------------------------------------

def _current_job(store):
    """The job current.json names (any phase), or None; a corrupt pointer is a RequestError."""
    try:
        pointer = store.current()
        return store.load_job(pointer["job_id"]) if pointer else None
    except Exception as exc:  # noqa: BLE001  (the store's own error classes all mean: fix the file)
        raise RequestError(f"The manual edit job files cannot be read: {exc}", HTTP_SERVER_ERROR)


@voyager_tools_bp.route("/api/manual-edit", methods=["GET"])
@guarded
def api_manual_edit():
    """The current manual edit job, summarised, or null."""
    store = deps.job_store()
    job = _current_job(store)
    if job is None:
        return jsonify({"job": None})
    tail = store.tail_log(job["job_id"], LOG_TAIL)
    return jsonify({"job": clean_manual_edit.summarise(job, tail)})


@voyager_tools_bp.route("/api/manual-edit/cancel", methods=["POST"])
@guarded
def api_manual_edit_cancel():
    """Cancel and prune the current job: {initials, confirm: true}."""
    body = _json_body()
    _confirmed(body)
    initials = _initials(body)
    store = deps.job_store()
    job = _current_job(store)
    if job is None:
        raise RequestError("No manual edit job is active; there is nothing to cancel.", HTTP_CONFLICT)
    host = str(deps.nas_config().get("host") or "").strip()
    shelf_prefix = f"{host}:" if host else ""
    try:
        result = clean_manual_edit.cancel_and_prune(store, deps.registry(), job["job_id"], initials,
                                                    rmtree_fn=deps.rmtree, shelf_prefix=shelf_prefix)
    except clean_manual_edit.CancelRefused as exc:
        raise RequestError(str(exc), HTTP_CONFLICT)
    return jsonify({"ok": True, **result})


# --- tools 4 and 5: rewind and spot redo ------------------------------------------------------

def _rewind_rows(registry, active_ids) -> list[dict]:
    """Rows a rewind could apply to: every row Voyager 1 has touched, with the facts the page needs."""
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
            "manual_edit_status": row.get("manual_edit_status", ""), "run_state": registry.run_state(row),
            "processing_location": location, "local": rewind.is_local_folder(location),
            "in_job": row.get("readable_id") in active_ids, "psx_file": row.get("psx_file", ""),
        })
    return out


@voyager_tools_bp.route("/api/rewind/rows", methods=["GET"])
@guarded
def api_rewind_rows():
    """The rows the rewind and spot redo selects list."""
    active_ids, _job = _active_job_ids()
    return jsonify({"rows": _rewind_rows(deps.registry(), set(active_ids)), "targets": [
        {"value": t, "label": rewind.TARGET_WORDS[t]} for t in rewind.TARGETS]})


def _rewind_row(registry, body: Mapping) -> tuple[dict, str]:
    """The row named by the body and the validated target."""
    rid = _readable_id(body)
    target = str(body.get("target") or rewind.TARGET_BEFORE_STEP1)
    if target not in rewind.TARGETS:
        raise RequestError(f"target must be one of {', '.join(rewind.TARGETS)}.")
    row = registry.get(rid)
    if row is None:
        raise RequestError(f"No registry row has the id {rid}.", HTTP_NOT_FOUND)
    return row, target


def _refuse_rewind(registry, row: dict, target: str) -> None:
    """Raise the 409 refusal (with the pull-back command when that is the fix) when the row cannot rewind."""
    active_ids, _job = _active_job_ids()
    why = rewind.refusal(row, target, registry.run_is_live, active_ids)
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
    _refuse_rewind(registry, row, target)
    the_plan = rewind.plan(row, target)
    if the_plan["empty"]:
        raise RequestError(f"{row['readable_id']} has nothing to rewind {the_plan['target_words']}.", HTTP_CONFLICT)
    return jsonify({"ok": True, "plan": the_plan})


def _apply_rewind(body: Mapping, spot: bool) -> dict:
    """Shared apply for the rewind and spot redo routes."""
    _confirmed(body)
    initials = _initials(body)
    registry = deps.registry()
    if spot:
        body = dict(body, target=rewind.TARGET_BEFORE_STEP1)
    row, target = _rewind_row(registry, body)
    _refuse_rewind(registry, row, target)
    active_ids, _job = _active_job_ids()
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


@voyager_tools_bp.route("/api/spot-redo", methods=["POST"])
@guarded
def api_spot_redo():
    """Spot redo: {readable_id, initials, confirm: true} -> the manifest with the spot_redo fact."""
    return jsonify({"ok": True, "manifest": _apply_rewind(_json_body(), spot=True)})


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
    try:
        saved = store.save_branch(phase, base_ref, slug, description, why, values, initials)
    except ps.StoreLocked as exc:
        raise RequestError(str(exc), HTTP_CONFLICT)
    except (TypeError, ValueError, FileNotFoundError) as exc:
        raise RequestError(str(exc))
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
    try:
        level = ps.classify_change(ps.flatten(store.load(phase, "default").values), ps.flatten(values))
        saved = store.save_default(phase, values, description, initials)
    except ps.StoreLocked as exc:
        raise RequestError(str(exc), HTTP_CONFLICT)
    except (TypeError, ValueError, FileNotFoundError) as exc:
        raise RequestError(str(exc))
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
    "slice_section", "render_markdown", "lock_body_html", "lock_page_html", "admin_body_html",
    "URL_PREFIX", "MODULE_SLUG", "DISPLAY_NAME", "UI_STYLE", "ASSET_VERSION", "ROOT_SECTION_PREFIX",
    "LOCK_MESSAGE", "ADMIN_ERROR", "ADMIN_MESSAGE", "PROMPTS_DOC",
]
