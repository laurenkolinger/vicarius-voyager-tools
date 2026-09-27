#!/usr/bin/env python3
"""
Module: voyagertools/manual_edit_views.py
Purpose: The manual edit routes of the Atlas control panel (program C spec
         section 5.3): the Timelines pane's GET /voyager-tools/api/timelines
         and /voyager-tools/api/timelines/detail (milestone C8, spec 4.12),
         registered on the same blueprint as views.py, which imports this
         module at its end. Both routes read: the job records through
         the manual edit job store, program A's status intervals and notes
         through the registry library, program B's edit time through
         manualedit.jobstore.edit_time. The Uploads pane (milestone C7)
         needs no route here: it reads and starts uploads through program
         B's own /manual-edit/api routes.
Inputs:  HTTP GET requests with optional filters; deps.job_store(),
         deps.registry(), deps.site_name, deps.now_iso, deps.recover_uploads.
Outputs: JSON. The only write is the manual edit module's recovery of
         upload sets a desktop restart stranded (deps.recover_uploads).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime

from flask import current_app, jsonify, request

from . import timelines
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
    job_id = str(request.args.get("job") or "").strip()
    psx = str(request.args.get("psx") or "").strip()
    if not jsm.JOB_ID_PATTERN.fullmatch(job_id):
        raise RequestError(LINE_BAD_JOB)
    if len(psx) > MAX_PSX_CHARS or not jsm.PSX_BASENAME_PATTERN.fullmatch(psx):
        raise RequestError(LINE_BAD_PSX)
    jobs, _skipped = _jobs(store, jsm)
    job = next((j for j in jobs if j.get("job_id") == job_id), None)
    if job is None:
        raise RequestError(LINE_UNKNOWN_JOB.format(job_id=job_id), views.HTTP_NOT_FOUND)
    transect = next((t for t in timelines.transects_of(job, str, views.deps.site_name) if t["psx"] == psx), None)
    if transect is None:
        raise RequestError(f"{psx} is not a transect of manual edit project {job_id}.", views.HTTP_NOT_FOUND)
    registry = views.deps.registry()
    now = _now()
    intervals = _read_registry(lambda: timelines._intervals_many(registry, transect["readable_ids"], now))
    notes = _read_registry(lambda: timelines._notes(registry))
    until = timelines.windows(jobs).get((job_id, transect["processing_folder"]))
    return jsonify(timelines.detail(job, psx, intervals, notes, timelines.parse_time(job.get("created_at")), until))
