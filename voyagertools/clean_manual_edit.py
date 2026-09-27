#!/usr/bin/env python3
"""
Module: voyagertools/clean_manual_edit.py
Purpose: The clean manual edit run tool (program spec section 7, tool 3;
         chain program C section 5.3): summarise every unfinished manual
         edit project and, on a confirmed cancel of one, mark it cancelled,
         remove the edit bench copy when the job never reached the push to
         the Shelf, and put its registry rows back: a row still at editing
         returns to awaiting with its Shelf location through the registry's
         named reset clean_manual_edit; a row at blank or awaiting keeps its
         status and gets its Shelf location back when its location points at
         the edit bench; any other status is left as it is and reported.
Inputs:  a manualedit.jobstore.JobStore, the registry library (its manual
         edit vocabulary: MANUAL_EDIT_STATUSES and the derived tuples), the
         operator's initials, an rmtree function.
Outputs: a plain summary for the page and the cancel result; registry cells
         written through registry.upsert (one event per changed cell) under
         the actor voyager_tools:<initials>; the job record moved to
         cancelled (which rewrites current.json from the remaining active
         jobs, or removes it when none is left).

Refusals: a finished job; a transfer or checks step in flight; a transect
the manual edit job record holds in a certified state (review, upload,
uploading, restarting); a row of the job the registry records at review or
upload, or at a value outside the manual edit statuses (a hand-typed or
padded cell, which the registry's upsert status guard would refuse partway
through the restore). Each refusal comes before anything is written or
removed.
"""
from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Mapping

ACTOR_PREFIX = "voyager_tools:"
# Two to four letters after trimming and upper-casing: the registry's
# MANUAL_EDIT_INITIALS_PATTERN, the one initials rule of the manual edit chain.
INITIALS_PATTERN = re.compile(r"^[A-Z]{2,4}$")
# A processing folder is named {SITE}_{T#}_3D (MRS_T1_3D). Until 2026-09-08 it
# carried a season and ended "_3dprocessing", specific enough that a suffix test
# was a safe guard on its own; "_3D" is not, so this tests the whole shape.
# Mirrors naming3d.PROCESSING_FOLDER_NAME; the suite below pins the two equal.
PROCESSING_FOLDER_NAME = re.compile(r"^[A-Za-z0-9]+_T[0-9]+_3D\Z")

# Job phases (manualedit.jobstore names them; mirrored here so this module
# needs no import of that clone; tests/test_clean_manual_edit.py pins them).
PHASE_PULLING = "pulling"
PHASE_CHECKING = "checking"
PHASE_PUSHING = "pushing"
PHASE_VERIFYING = "verifying"
PHASE_DONE = "done"
PHASE_CANCELLED = "cancelled"
FAILED_PREFIX = "failed_"
IN_FLIGHT_PHASES = (PHASE_PULLING, PHASE_CHECKING, PHASE_PUSHING, PHASE_VERIFYING)
TERMINAL_PHASES = (PHASE_DONE, PHASE_CANCELLED)
# The manual edit job record's per-transect states in which the bench copy
# holds a certified edit the Shelf does not have yet (chain program B section
# 4.1); mirrored like the phases above and pinned to manualedit.jobstore by the suite.
CERTIFIED_PROJECT_STATES = ("review", "upload", "uploading", "restarting")
# Once a job reached any of these, edited projects may already sit on the
# Shelf, so the local copy is kept as the only complete record.
PUSH_PHASES = (PHASE_PUSHING, PHASE_VERIFYING, PHASE_DONE,
               FAILED_PREFIX + PHASE_PUSHING, FAILED_PREFIX + PHASE_VERIFYING)

# The registry's named reset for this tool (a key of registry.MANUAL_EDIT_RESETS:
# editing to awaiting only); the registry's upsert status guard
# (MANUAL_EDIT_UPSERT_GUARD) checks it.
CLEAN_MANUAL_EDIT_RESET = "clean_manual_edit"
# The registry cell the restore puts back to the Shelf path (not a status word).
PROCESSING_LOCATION_CELL = "processing_location"
HISTORY_TAIL = 8
LOG_TAIL = 20
CANCEL_REASON = "cancelled from the Atlas control panel"


class CancelRefused(ValueError):
    """The job cannot be cancelled in its current phase; the message says why."""


def actor(initials) -> str:
    """The registry actor for an Atlas control panel action: voyager_tools:<INITIALS>.

    Raises:
        TypeError: when initials is not a string.
        ValueError: when it is not two to four letters after trimming.
    """
    if not isinstance(initials, str):
        raise TypeError(f"initials must be a string, got {type(initials).__name__}")
    clean = initials.strip().upper()
    if not INITIALS_PATTERN.fullmatch(clean):
        raise ValueError(f"initials must be two to four letters, got {initials!r}")
    return ACTOR_PREFIX + clean


def _require_job(job) -> Mapping:
    """`job` as a mapping with a job_id and a phase."""
    if not isinstance(job, Mapping) or not isinstance(job.get("job_id"), str) or not isinstance(job.get("phase"), str):
        raise TypeError("job must be a manual edit job record (a dict with job_id and phase)")
    return job


def reached_pushing(job) -> bool:
    """True when the job is, was, or failed while pushing edited projects to the Shelf."""
    job = _require_job(job)
    if job["phase"] in PUSH_PHASES:
        return True
    history = job.get("phase_history")
    if not isinstance(history, list):
        return False
    return any(isinstance(h, Mapping) and h.get("phase") in PUSH_PHASES for h in history)


def cancel_refusal(job) -> str:
    """Why the job cannot be cancelled now, going by its own record, or "" when it can.

    A terminal job has nothing to cancel; a job whose transfer or checks are
    in flight is refused until that step finishes or fails, because the
    thread doing the work lives in the manual edit module and would keep
    writing after the record said cancelled; a job holding a transect in a
    CERTIFIED_PROJECT_STATES state is refused, because its bench copy carries
    an edit that an admin decides or an upload carries to the Shelf.
    """
    job = _require_job(job)
    phase = job["phase"]
    if phase in TERMINAL_PHASES:
        return f"Job {job['job_id']} is already {phase}; there is nothing to cancel."
    if phase in IN_FLIGHT_PHASES:
        return (f"Job {job['job_id']} is {phase} right now; wait for that step to finish or fail, "
                f"then cancel it.")
    projects = job.get("projects")
    if isinstance(projects, Mapping):
        for psx, project in projects.items():
            if isinstance(project, Mapping) and project.get("state") in CERTIFIED_PROJECT_STATES:
                return (f"{psx} carries a certified edit; decide it in the Review queue or upload it "
                        f"before cancelling.")
    return ""


def _status_of(row: Mapping, registry_mod) -> str:
    """The row's manual edit status cell exactly as stored, padding included.

    Decisions compare this raw value, as the registry's upsert status guard
    does, so a padded " editing " never counts as editing.
    """
    return str(row.get(registry_mod.MANUAL_EDIT_STATUS_COLUMN) or "")


def _job_ids(job: Mapping) -> list[str]:
    """The distinct non-blank readable ids of the job's rows, in order."""
    ids: list[str] = []
    for row in job.get("rows") or []:
        rid = str(row.get("readable_id") or "").strip() if isinstance(row, Mapping) else ""
        if rid and rid not in ids:
            ids.append(rid)
    return ids


def registry_refusal(job, registry_mod) -> str:
    """Why the registry forbids cancelling the job, or "" when it does not.

    A row the registry records at review or upload (the in-flight statuses
    after editing) holds a certified edit on the bench; cancelling would
    prune that copy. A row whose raw cell is outside MANUAL_EDIT_STATUSES (a
    hand-typed or padded value) needs the atlas repair first: the restore
    could not move it, and the prune would leave it pointing at a removed
    folder. A row with no registry entry refuses nothing.
    """
    job = _require_job(job)
    certified = tuple(s for s in registry_mod.MANUAL_EDIT_IN_FLIGHT if s != registry_mod.ME_EDITING)
    for rid in _job_ids(job):
        row = registry_mod.get(rid)
        raw = _status_of(row, registry_mod) if row is not None else registry_mod.ME_BLANK
        if raw.strip() in certified:
            return (f"{rid} carries a certified edit (manual edit status {raw.strip()}); decide it in the "
                    f"Review queue or upload it before cancelling.")
        if raw not in registry_mod.MANUAL_EDIT_STATUSES:
            return (f"{rid} holds the manual edit status {raw!r}, which VICARIUS does not know; "
                    f"repair it from the atlas in admin mode first.")
    return ""


def _bench_of(job: Mapping) -> str:
    """The real path of the job's edit bench, or "" when the record names no absolute one."""
    bench = str(job.get("edit_bench") or "").strip()
    return os.path.realpath(bench) if bench and os.path.isabs(bench) else ""


def _points_at_bench(location, real_bench: str) -> bool:
    """True when a processing_location cell names a folder inside the edit bench (never the bench itself)."""
    text = str(location or "").strip()
    if not real_bench or not text or not os.path.isabs(text):
        return False
    real = os.path.realpath(text)
    return real != real_bench and real.startswith(real_bench.rstrip("/") + "/")


def prune_targets(job) -> list[str]:
    """The local edit bench copies a cancel removes: every distinct row folder inside the edit bench.

    A path outside the edit bench, or one that does not end with the
    processing suffix, is never returned (the delete stays anchored).
    """
    job = _require_job(job)
    real_bench = _bench_of(job)
    if not real_bench:
        return []
    bench = str(job.get("edit_bench")).strip()
    targets: list[str] = []
    for row in job.get("rows") or []:
        if not isinstance(row, Mapping):
            continue
        local = str(row.get("local_path") or "").strip()
        if not local:
            folder = str(row.get("processing_folder") or "").strip()
            local = os.path.join(bench, folder) if folder else ""
        if not _points_at_bench(local, real_bench):
            continue
        real = os.path.realpath(local)
        if PROCESSING_FOLDER_NAME.match(os.path.basename(real)) and real not in targets:
            targets.append(real)
    return targets


def summarise(job, tail_lines=None, registry_mod=None) -> dict:
    """What the tool page shows for one unfinished job.

    Parameters:
        job: the job record.
        tail_lines: the last lines of the job log.
        registry_mod: the registry library; when given, registry_refusal
            also decides can_cancel (the routes pass it; None reads the job
            record alone).

    Returns:
        {job_id, phase, created_by, created_at, edit_bench, rows: [{readable_id,
         site, site_name, transect, local_path, shelf_location}], history:
         [last HISTORY_TAIL entries], log_tail, reached_pushing, will_prune,
         prune_targets, refusal, can_cancel}.
    """
    job = _require_job(job)
    history = [h for h in (job.get("phase_history") or []) if isinstance(h, Mapping)]
    refusal = cancel_refusal(job) or (registry_refusal(job, registry_mod) if registry_mod is not None else "")
    pushed = reached_pushing(job)
    return {
        "job_id": job["job_id"], "phase": job["phase"],
        "created_by": str(job.get("created_by") or ""), "created_at": str(job.get("created_at") or ""),
        "edit_bench": str(job.get("edit_bench") or ""),
        "rows": [{k: str(r.get(k) or "") for k in ("readable_id", "site", "site_name", "transect",
                                                    "local_path", "shelf_location")}
                 for r in (job.get("rows") or []) if isinstance(r, Mapping)],
        "history": [{k: str(h.get(k) or "") for k in ("phase", "at", "by", "reason")} for h in history[-HISTORY_TAIL:]],
        "log_tail": list(tail_lines or []),
        "reached_pushing": pushed, "will_prune": not pushed and not refusal,
        "prune_targets": prune_targets(job) if not pushed else [],
        "refusal": refusal, "can_cancel": not refusal,
    }


def _has_host_prefix(shelf: str) -> bool:
    """True when `shelf` already names a host before its first slash
    (the picker's SHELF_PREFIX form, "host:/volume...").

    A bare Shelf path ("/volume6/...") starts with "/", so the text before
    its first "/" is empty and carries no colon; a prefixed one's text
    before the first "/" is the host, which does.
    """
    return ":" in shelf.split("/", 1)[0]


def restore_registry(job, registry_mod, actor_name: str, shelf_prefix: str = "") -> list[dict]:
    """Put the job's registry rows back where the job found them, as far as the lifecycle allows.

    By the row's manual edit status (chain program A section 4.1):
    - editing: the status returns to awaiting (manual_edit_done cleared)
      through upsert's named reset CLEAN_MANUAL_EDIT_RESET, and
      processing_location returns to the Shelf path the job recorded;
    - blank or awaiting (the statuses Voyager 1 may select): the status is
      kept; processing_location returns to the Shelf path only when it
      points inside the job's edit bench (a pull that failed partway wrote
      the bench path, and the bench copy is about to go), so a location
      someone else wrote is never overwritten;
    - done, review, upload or a value VICARIUS does not know: nothing is
      written; the outcome names the status in status_kept.
    A location is written only when the row exists and the cell differs.
    Every write goes through registry.upsert, so each changed cell leaves an
    event.

    Parameters:
        shelf_prefix: prepended to a recorded shelf_location that carries no
            host prefix of its own (default "", which keeps the shelf_location
            exactly as the job recorded it). The manual edit picker records
            shelf_location WITHOUT the host prefix (T20260922: a cancel
            through this tool left processing_location reading a bare
            "/volume6/..." path the Carousel and the picker both treat as
            local, not on the Shelf), so a caller that wants the restored
            cell to read as a real Shelf path passes its own SHELF_PREFIX
            ("<host>:") here.

    Returns:
        [{readable_id, location_restored, status_reset, missing, status_kept}]
        per row; status_kept is None when the status moved or the row is
        missing, else the status left in place.
    """
    job = _require_job(job)
    results = []
    for row in job.get("rows") or []:
        if not isinstance(row, Mapping) or not str(row.get("readable_id") or "").strip():
            continue
        rid = str(row["readable_id"])
        current = registry_mod.get(rid)
        outcome = {"readable_id": rid, "location_restored": False, "status_reset": False,
                   "missing": current is None, "status_kept": None}
        if current is None:
            results.append(outcome)
            continue
        fields = _restore_fields(row, current, registry_mod, _bench_of(job), shelf_prefix, outcome)
        if fields:
            registry_mod.upsert(rid, fields, actor_name, manual_edit_reset=CLEAN_MANUAL_EDIT_RESET)
        results.append(outcome)
    return results


def _restore_fields(job_row: Mapping, current: Mapping, registry_mod, real_bench: str, shelf_prefix: str,
                    outcome: dict) -> dict:
    """The cells restore_registry writes for one row, filling `outcome` as it decides.

    Parameters:
        job_row: the job's row entry (its recorded shelf_location).
        current: the row as the registry holds it now.
        registry_mod: the registry library, for the status vocabulary.
        real_bench: the job's edit bench as _bench_of gives it.
        shelf_prefix: see restore_registry.
        outcome: the row's result dict, updated in place.

    Returns:
        {cell: value} to write, possibly empty.
    """
    status = _status_of(current, registry_mod)
    fields: dict[str, str] = {}
    if status == registry_mod.ME_EDITING:
        fields.update(registry_mod.manual_edit_fields(registry_mod.ME_AWAITING))
        outcome["status_reset"] = True
    else:
        outcome["status_kept"] = status
        if status not in registry_mod.MANUAL_EDIT_VOYAGER1_ELIGIBLE:
            return fields
        if not _points_at_bench(current.get(PROCESSING_LOCATION_CELL), real_bench):
            return fields
    shelf = str(job_row.get("shelf_location") or "").strip()
    if shelf and not _has_host_prefix(shelf):
        shelf = shelf_prefix + shelf
    if shelf and current.get(PROCESSING_LOCATION_CELL) != shelf:
        fields[PROCESSING_LOCATION_CELL] = shelf
        outcome["location_restored"] = True
    return fields


def cancel_and_prune(store, registry_mod, job_id: str, initials: str, reason: str = CANCEL_REASON,
                     rmtree_fn: Callable = shutil.rmtree, shelf_prefix: str = "") -> dict:
    """Cancel the job and clean up after it (spec section 7, tool 3).

    Order: the refusal checks (the job record, then the registry); the
    registry restore (so the atlas stops
    pointing at a folder about to go); the prune of the edit bench copies
    when the job never reached pushing (a failure to remove one is reported,
    not raised, and the job is still cancelled); the cleanup record; the
    transition to cancelled, which rewrites current.json from the remaining
    active jobs, or removes it when none is left.

    Parameters:
        store: a manualedit.jobstore.JobStore.
        registry_mod: the registry library module.
        job_id: the job to cancel.
        initials: who cancels (validated by actor()).
        reason: the phase_history reason line.
        rmtree_fn: injectable for tests.
        shelf_prefix: forwarded to restore_registry; "<host>:" when the
            caller wants a bare recorded shelf_location restored as a real
            Shelf path (see restore_registry's docstring).

    Returns:
        {"job_id", "phase", "restored": [...], "pruned": [paths], "kept": [paths],
         "problems": [sentences], "warnings": [sentences]}.

    Raises:
        CancelRefused: when the job is terminal, a step is in flight, a
            transect holds a certified edit (job record or registry), or a
            row's status cell is a value VICARIUS does not know.
        TypeError, ValueError: for bad initials or a malformed record.
    """
    who = actor(initials)
    job = store.load_job(job_id)
    refusal = cancel_refusal(job) or registry_refusal(job, registry_mod)
    if refusal:
        raise CancelRefused(refusal)
    result = {"job_id": job_id, "phase": "", "restored": restore_registry(job, registry_mod, who, shelf_prefix),
              "pruned": [], "kept": [], "problems": [], "warnings": []}
    if reached_pushing(job):
        result["kept"] = prune_targets(job)
        result["warnings"].append("The job reached the push to the Shelf, so the local copies are kept as the "
                                  "complete record; check the Shelf copies before deleting them by hand.")
    else:
        for target in prune_targets(job):
            if not os.path.isdir(target):
                continue
            try:
                rmtree_fn(target)
                result["pruned"].append(target)
            except OSError as exc:
                result["problems"].append(f"Could not remove {target}: {exc}")
                result["kept"].append(target)
    store.record_cleanup(job_id, bool(result["pruned"]) and not result["problems"], who)
    record = store.transition(job_id, PHASE_CANCELLED, who, reason)
    result["phase"] = record["phase"]
    return result


__all__ = [
    "ACTOR_PREFIX", "INITIALS_PATTERN", "PROCESSING_FOLDER_NAME", "IN_FLIGHT_PHASES", "TERMINAL_PHASES", "PUSH_PHASES",
    "CERTIFIED_PROJECT_STATES", "CLEAN_MANUAL_EDIT_RESET", "PROCESSING_LOCATION_CELL", "CANCEL_REASON",
    "CancelRefused", "actor", "reached_pushing", "cancel_refusal", "registry_refusal", "prune_targets", "summarise",
    "restore_registry", "cancel_and_prune",
]
