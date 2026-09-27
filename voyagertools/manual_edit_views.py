#!/usr/bin/env python3
"""
Module: voyagertools/manual_edit_views.py
Purpose: The manual edit routes of the Atlas control panel (program C spec
         section 5.3), registered on the same blueprint as views.py, which
         imports this module at its end: the Timelines pane's
         GET /voyager-tools/api/timelines and /voyager-tools/api/timelines/detail
         (milestone C8, spec 4.12); the Review queue pane's
         GET /voyager-tools/api/review/queue (milestone C6, spec 4.10); and
         the review fragment, review_fragment_payload, which the fragment
         route of views.py answers for ?tool=review (spec 4.11). Every route
         reads: the job records through the manual edit job store, program
         A's status intervals and notes through the registry library,
         program B's edit time through manualedit.jobstore.edit_time. The
         review content itself (static/vt_review.js) reads program B's
         dossier and posts its open, review and back-to-review routes; the
         Uploads pane (milestone C7) reads and starts uploads through
         program B's own /manual-edit/api routes.
Inputs:  HTTP GET requests with optional filters; deps.job_store(),
         deps.active_jobs(), deps.registry(), deps.site_name, deps.now_iso,
         deps.recover_uploads, deps.recover_restarts.
Outputs: JSON. The only writes are the manual edit module's recoveries of
         upload sets and start overs a desktop restart stranded
         (deps.recover_uploads, deps.recover_restarts).
"""
from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from datetime import date, datetime

from flask import current_app, jsonify, render_template, request

from . import review_queue, timelines
from . import views
from .views import RequestError, guarded, voyager_tools_bp

# --- constants ---------------------------------------------------------------

DAY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_PSX_CHARS = 200
HTTP_UNAVAILABLE = 503
LINE_NOT_MOUNTED = "The manual edit module is not mounted here."
LINE_BAD_SINCE = "Give since as a day, YYYY-MM-DD, for example 2026-09-27."
LINE_BAD_JOB = "Name the manual edit project by its id, ME-YYYYMMDD-HHMMSS-initials."
LINE_BAD_EDITOR = "Give the editor as initials of two to four letters."
LINE_BAD_PSX = "Name the transect by its Metashape project file, for example MRS_T2_2023_2025.psx."
LINE_UNKNOWN_JOB = "No manual edit project {job_id} is recorded."
LINE_UNKNOWN_TRANSECT = "{psx} is not a transect of manual edit project {job_id}."
LINE_UNREADABLE_JOB = "The manual edit project {job_id} cannot be read: {error}"
LINE_QUEUE_UNREADABLE = ("The manual edit project {job_ids} cannot be read, so the Review queue lists nothing; "
                         "open Clean manual edit run to see it.")
REVIEW_TEMPLATE = "voyager_tools/review.html"
# A job id as it appears inside a job store error (a file name or a sentence);
# each candidate is then checked whole against the job store's own pattern.
JOB_ID_CANDIDATE = re.compile(r"ME-[0-9A-Za-z-]+")
MANUAL_EDIT_VIEWS_MODULE = "manualedit.views"


# --- helpers -----------------------------------------------------------------

def _manual_edit():
    """The job store and the job store library, or a 503 when the manual edit module cannot be imported.

    Raises:
        RequestError: 503 with not_mounted, in one sentence.
    """
    try:
        return views.deps.job_store(), views._jobstore_module()
    except ImportError as exc:
        raise RequestError(LINE_NOT_MOUNTED, HTTP_UNAVAILABLE, {"not_mounted": True}) from exc


def _now() -> datetime:
    """The time of this reading, from deps.now_iso (AST with offset)."""
    return timelines.parse_time(views.deps.now_iso())


def _filters(args: Mapping, jobstore_module) -> dict:
    """The four optional filters of the timelines route, validated; a blank one matches everything.

    Raises:
        RequestError: 400 naming the filter that does not parse.
    """
    job = str(args.get("job") or "").strip()
    state = str(args.get("state") or "").strip()
    editor = str(args.get("editor") or "").strip().upper()
    since = str(args.get("since") or "").strip()
    if job and not jobstore_module.JOB_ID_PATTERN.fullmatch(job):
        raise RequestError(LINE_BAD_JOB)
    if state and state not in jobstore_module.PROJECT_STATES:
        raise RequestError(f"Give state as one of {', '.join(jobstore_module.PROJECT_STATES)}.")
    if editor and not views.INITIALS_PATTERN.fullmatch(editor):
        raise RequestError(LINE_BAD_EDITOR)
    if since:
        try:
            if not DAY_PATTERN.fullmatch(since):
                raise ValueError(since)
            date.fromisoformat(since)
        except ValueError as exc:
            raise RequestError(LINE_BAD_SINCE) from exc
    return {"job": job, "state": state, "editor": editor, "since": since}


def _job_and_psx(job_value, psx_value, jobstore_module) -> tuple[str, str]:
    """A manual edit project id and a transect's psx from a query, both validated whole, as given.

    Nothing is trimmed: a value with a space or a line end around it is
    malformed, since no job file or psx carries one.

    Raises:
        RequestError: 400 naming the value that does not parse.
    """
    job_id = job_value if isinstance(job_value, str) else ""
    psx = psx_value if isinstance(psx_value, str) else ""
    if not jobstore_module.JOB_ID_PATTERN.fullmatch(job_id):
        raise RequestError(LINE_BAD_JOB)
    if len(psx) > MAX_PSX_CHARS or not jobstore_module.PSX_BASENAME_PATTERN.fullmatch(psx):
        raise RequestError(LINE_BAD_PSX)
    return job_id, psx


def _jobs(store, jobstore_module) -> tuple[list[dict], list[dict]]:
    """Every job record, newest first, and the files that could not be read."""
    errors = (jobstore_module.JobStoreError, ValueError, TypeError)
    try:
        return timelines.load_jobs(store, errors)
    except OSError as exc:
        raise RequestError(f"The manual edit job files cannot be read: {exc}", views.HTTP_SERVER_ERROR) from exc


def _read_registry(read):
    """Run one read of program A's files; an unreadable file is a 500 naming it."""
    try:
        return read()
    except OSError as exc:
        raise RequestError(f"The registry could not be read: {exc}", views.HTTP_SERVER_ERROR) from exc


def _upload_reader(store, jobstore_module):
    """upload id -> (the upload set record, "") or (None, the reason it cannot be read, logged once as a warning)."""
    def read(upload_id: str):
        try:
            return store.load_upload_set(upload_id), ""
        except (jobstore_module.JobStoreError, ValueError, TypeError, OSError) as exc:
            current_app.logger.warning("voyager tools: timelines could not read the upload set %s: %s", upload_id, exc)
            return None, str(exc)
    return read


# --- routes ------------------------------------------------------------------

@voyager_tools_bp.route("/api/timelines", methods=["GET"])
@guarded
def api_timelines():
    """The Timelines payload (spec 4.12): summary, projects and transect rows, newest first.

    Before the jobs are read, the manual edit module's own recovery marks
    every upload set a desktop restart stranded as interrupted (the same
    step its upload routes take), so a stranded set never reads as
    uploading here.

    Query: job, state, editor, since (YYYY-MM-DD); each filters the rows
    only, never the summary or the projects.
    """
    store, jsm = _manual_edit()
    filters = _filters(request.args, jsm)
    registry = views.deps.registry()
    now = _now()
    views.deps.recover_uploads(store)
    jobs, skipped = _jobs(store, jsm)
    notes = _read_registry(lambda: timelines._notes(registry))
    rows = _read_registry(lambda: timelines.project_rows(
        jobs, lambda ids: timelines._intervals_many(registry, ids, now),
        lambda opens: timelines._metashape_seconds(jsm, opens, now), views.deps.site_name, now,
        notes=notes, upload_fn=_upload_reader(store, jsm), live_upload_statuses=jsm.UPLOAD_LIVE_STATUSES,
        state_words=jsm.STATE_WORDS,
        sort_key=registry.sort_key))
    projects = timelines.project_summaries(jobs, jsm.PROJECT_STATES)
    return jsonify({"generated_at": now.isoformat(timespec="seconds"),
                    "summary": timelines.summary(rows, projects, jsm.PROJECT_STATES, now, skipped),
                    "projects": projects, "rows": timelines.filter_rows(rows, **filters),
                    "states": list(jsm.PROJECT_STATES), "state_words": dict(jsm.STATE_WORDS)})


@voyager_tools_bp.route("/api/timelines/detail", methods=["GET"])
@guarded
def api_timelines_detail():
    """One transect's drawer: {job_id, psx, intervals: {readable id: [...]}, notes: [...]}.

    400 for a malformed job or psx, 404 for a job or psx that is not recorded.
    """
    store, jsm = _manual_edit()
    job_id, psx = _job_and_psx(request.args.get("job"), request.args.get("psx"), jsm)
    jobs, _skipped = _jobs(store, jsm)
    job = next((j for j in jobs if j.get("job_id") == job_id), None)
    if job is None:
        raise RequestError(LINE_UNKNOWN_JOB.format(job_id=job_id), views.HTTP_NOT_FOUND)
    transect = next((t for t in timelines.transects_of(job, str, views.deps.site_name) if t["psx"] == psx), None)
    if transect is None:
        raise RequestError(LINE_UNKNOWN_TRANSECT.format(psx=psx, job_id=job_id), views.HTTP_NOT_FOUND)
    registry = views.deps.registry()
    now = _now()
    intervals = _read_registry(lambda: timelines._intervals_many(registry, transect["readable_ids"], now))
    notes = _read_registry(lambda: timelines._notes(registry))
    until = timelines.windows(jobs).get((job_id, transect["processing_folder"]))
    return jsonify(timelines.detail(job, psx, intervals, notes, timelines.parse_time(job.get("created_at")), until))


# --- the Review queue and the review fragment (milestone C6) ------------------------

def _queue_rules(jobstore_module) -> review_queue.QueueRules:
    """The transect rules the queue applies, from the manual edit job store (the ones its dossier uses)."""
    return review_queue.QueueRules(stopped=jobstore_module.restart_stopped,
                                   returnable=jobstore_module.can_return_to_review)


@voyager_tools_bp.route("/api/review/queue", methods=["GET"])
@guarded
def api_review_queue():
    """The Review queue payload (spec 4.10): {items, stopped_restarts, marked_for_upload, unmatched, read_at}.

    Every unfinished project is read strictly, so a job file that cannot be
    read answers 500 with its sentence instead of calling a row it may hold
    unmatched. Before the lists are built the manual edit module's own
    recovery marks every start over a desktop restart stranded as stopped
    (the step its dossier route takes), so it lists under Start over stopped.
    """
    _store, jsm = _manual_edit()
    jobs = views.deps.recover_restarts(_queue_jobs(jsm))
    registry = views.deps.registry()
    rows = _read_registry(registry.load)
    payload = review_queue.queue_items(rows, jobs, views.deps.site_name, sort_key=registry.sort_key,
                                       status_review=registry.ME_REVIEW, rules=_queue_rules(jsm))
    payload["read_at"] = views.deps.now_iso()
    return jsonify(payload)


def _queue_jobs(jobstore_module) -> list[dict]:
    """Every unfinished job record, read strictly; an unreadable one is a 500 naming its project id, not its path.

    The full error (with the file path) goes to the desktop log; the pane
    shows one line naming the project and where to look at it.

    Raises:
        RequestError: 500 in one sentence.
    """
    try:
        return [job for job in views.deps.active_jobs() if isinstance(job, Mapping)]
    except Exception as exc:  # noqa: BLE001  (the store's own error classes all mean: fix the file)
        current_app.logger.warning("voyager tools: the Review queue could not read the job files: %s", exc)
        job_ids = sorted({token for token in JOB_ID_CANDIDATE.findall(str(exc))
                          if jobstore_module.JOB_ID_PATTERN.fullmatch(token)})
        line = (LINE_QUEUE_UNREADABLE.format(job_ids=" and ".join(job_ids)) if job_ids
                else f"The manual edit job files cannot be read: {exc}")
        raise RequestError(line, views.HTTP_SERVER_ERROR) from exc


def _manual_edit_asset_version() -> str:
    """The manual edit module's asset version (the upload window's cache bust), "" when it cannot be read."""
    try:
        return str(getattr(importlib.import_module(MANUAL_EDIT_VIEWS_MODULE), "ASSET_VERSION", "") or "")
    except ImportError:
        return ""


def _review_job(store, jobstore_module, job_id: str) -> dict:
    """The job record of a review fragment; 404 for an unknown project, 500 for a file that cannot be read."""
    try:
        return store.load_job(job_id)
    except jobstore_module.JobNotFound as exc:
        raise RequestError(LINE_UNKNOWN_JOB.format(job_id=job_id), views.HTTP_NOT_FOUND) from exc
    except (jobstore_module.JobStoreError, OSError, ValueError, TypeError) as exc:
        raise RequestError(LINE_UNREADABLE_JOB.format(job_id=job_id, error=exc), views.HTTP_SERVER_ERROR) from exc


def review_fragment_payload(job_value, psx_value) -> dict:
    """The review fragment of one transect (spec 4.11): the atlas's review sidecar and the Review queue pane mount it.

    Parameters:
        job_value, psx_value: the ?job= and ?psx= values as given.

    Returns:
        The lock or admin payload of views.gate_payload when either gate
        stops it; otherwise {ui_style, locked, base, html, scripts, styles,
        mount, opts, module}, the html being templates/voyager_tools/review.html
        with the transect's head (psx, site full name and code, transect,
        readable ids) rendered and escaped, and the transect's Shelf folder
        and the manual edit asset version as data attributes.

    Raises:
        RequestError: 400 a malformed job or psx, 404 an unknown project or
            a psx it does not hold, 500 a job file that cannot be read, 503
            the manual edit module not mounted.
    """
    gate = views.gate_payload()
    if gate is not None:
        return gate
    store, jsm = _manual_edit()
    job_id, psx = _job_and_psx(job_value, psx_value, jsm)
    job = _review_job(store, jsm, job_id)
    transect = review_queue.transect_of(job, psx, views.deps.site_name, views.deps.registry().sort_key)
    if transect is None:
        raise RequestError(LINE_UNKNOWN_TRANSECT.format(psx=psx, job_id=job_id), views.HTTP_NOT_FOUND)
    html = render_template(REVIEW_TEMPLATE, base=views.URL_PREFIX, v=views.ASSET_VERSION,
                           manual_edit_version=_manual_edit_asset_version(), job_id=job_id, **transect).strip()
    return {"ui_style": views.UI_STYLE_REVIEW, "locked": False, "base": views.URL_PREFIX, "html": html,
            "scripts": views.asset_urls(views.REVIEW_SCRIPTS), "styles": views.asset_urls((views.STATIC_CSS,)),
            "mount": views.MOUNT_REVIEW, "opts": {"jobId": job_id, "psx": psx},
            "module": {"name": views.MODULE_SLUG, "display_name": views.DISPLAY_NAME}}
