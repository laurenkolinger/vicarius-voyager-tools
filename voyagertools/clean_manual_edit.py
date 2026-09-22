#!/usr/bin/env python3
"""
Module: voyagertools/clean_manual_edit.py
Purpose: The clean manual edit run tool (program spec section 7, tool 3):
         show the current manual edit job and, on a confirmed cancel, mark it
         cancelled, remove the edit bench copy when the job never reached the
         push to the Shelf, put every row's processing location back to the
         Shelf path the job recorded, set manual_edit_status back to
         awaiting, and clear the current-job pointer.
Inputs:  a manualedit.jobstore.JobStore, the registry library, the
         operator's initials, an rmtree function.
Outputs: a plain summary for the page and the cancel result; registry cells
         written through registry.upsert (one event per changed cell) under
         the actor voyager_tools:<initials>; the job record moved to
         cancelled (which clears current.json).
"""
from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Mapping

ACTOR_PREFIX = "voyager_tools:"
INITIALS_PATTERN = re.compile(r"^[A-Za-z0-9]{1,8}$")
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
# Once a job reached any of these, edited projects may already sit on the
# Shelf, so the local copy is kept as the only complete record.
PUSH_PHASES = (PHASE_PUSHING, PHASE_VERIFYING, PHASE_DONE,
               FAILED_PREFIX + PHASE_PUSHING, FAILED_PREFIX + PHASE_VERIFYING)

# Registry cells (manualedit.registry_contract names the same values).
STATUS_COLUMN = "manual_edit_status"
DONE_COLUMN = "manual_edit_done"
LOCATION_COLUMN = "processing_location"
STATUS_AWAITING = "awaiting"
HISTORY_TAIL = 8
LOG_TAIL = 20
CANCEL_REASON = "cancelled from Voyager tools"


class CancelRefused(ValueError):
    """The job cannot be cancelled in its current phase; the message says why."""


def actor(initials) -> str:
    """The registry actor for a Voyager tools action: voyager_tools:<INITIALS>.

    Raises:
        TypeError: when initials is not a string.
        ValueError: when it is blank or carries characters other than letters and digits.
    """
    if not isinstance(initials, str):
        raise TypeError(f"initials must be a string, got {type(initials).__name__}")
    if not INITIALS_PATTERN.match(initials.strip()):
        raise ValueError(f"initials must be one to eight letters or digits, got {initials!r}")
    return ACTOR_PREFIX + initials.strip().upper()


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
    """Why the job cannot be cancelled now, or "" when it can.

    A terminal job has nothing to cancel; a job whose transfer or checks are
    in flight is refused until that step finishes or fails, because the
    thread doing the work lives in the manual edit module and would keep
    writing after the record said cancelled.
    """
    job = _require_job(job)
    phase = job["phase"]
    if phase in TERMINAL_PHASES:
        return f"Job {job['job_id']} is already {phase}; there is nothing to cancel."
    if phase in IN_FLIGHT_PHASES:
        return (f"Job {job['job_id']} is {phase} right now; wait for that step to finish or fail, "
                f"then cancel it.")
    return ""


def prune_targets(job) -> list[str]:
    """The local edit bench copies a cancel removes: every distinct row folder inside the edit bench.

    A path outside the edit bench, or one that does not end with the
    processing suffix, is never returned (the delete stays anchored).
    """
    job = _require_job(job)
    bench = str(job.get("edit_bench") or "").strip()
    if not bench or not os.path.isabs(bench):
        return []
    real_bench = os.path.realpath(bench)
    targets: list[str] = []
    for row in job.get("rows") or []:
        if not isinstance(row, Mapping):
            continue
        local = str(row.get("local_path") or "").strip()
        if not local:
            folder = str(row.get("processing_folder") or "").strip()
            local = os.path.join(bench, folder) if folder else ""
        if not local or not os.path.isabs(local):
            continue
        real = os.path.realpath(local)
        inside = real != real_bench and real.startswith(real_bench.rstrip("/") + "/")
        if inside and PROCESSING_FOLDER_NAME.match(os.path.basename(real)) and real not in targets:
            targets.append(real)
    return targets


def summarise(job, tail_lines=None) -> dict:
    """What the tool page shows for the current job.

    Returns:
        {job_id, phase, created_by, created_at, edit_bench, rows: [{readable_id,
         site, site_name, transect, local_path, shelf_location}], history:
         [last HISTORY_TAIL entries], log_tail, reached_pushing, will_prune,
         prune_targets, refusal, can_cancel}.
    """
    job = _require_job(job)
    history = [h for h in (job.get("phase_history") or []) if isinstance(h, Mapping)]
    refusal = cancel_refusal(job)
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
    """Put every row of the job back to its pre-job registry state.

    processing_location returns to the Shelf path the job recorded (only
    when the row still exists and the cell differs), manual_edit_status
    returns to awaiting and manual_edit_done is cleared. Every write goes
    through registry.upsert, so each changed cell leaves an event.

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
        [{readable_id, location_restored, status_reset, missing}] per row.
    """
    job = _require_job(job)
    results = []
    for row in job.get("rows") or []:
        if not isinstance(row, Mapping) or not str(row.get("readable_id") or "").strip():
            continue
        rid = str(row["readable_id"])
        current = registry_mod.get(rid)
        outcome = {"readable_id": rid, "location_restored": False, "status_reset": False, "missing": current is None}
        if current is None:
            results.append(outcome)
            continue
        fields = {}
        shelf = str(row.get("shelf_location") or "").strip()
        if shelf and not _has_host_prefix(shelf):
            shelf = shelf_prefix + shelf
        if shelf and current.get(LOCATION_COLUMN) != shelf:
            fields[LOCATION_COLUMN] = shelf
            outcome["location_restored"] = True
        if current.get(STATUS_COLUMN) != STATUS_AWAITING or current.get(DONE_COLUMN):
            fields[STATUS_COLUMN] = STATUS_AWAITING
            fields[DONE_COLUMN] = ""
            outcome["status_reset"] = True
        if fields:
            registry_mod.upsert(rid, fields, actor_name)
        results.append(outcome)
    return results


def cancel_and_prune(store, registry_mod, job_id: str, initials: str, reason: str = CANCEL_REASON,
                     rmtree_fn: Callable = shutil.rmtree, shelf_prefix: str = "") -> dict:
    """Cancel the job and clean up after it (spec section 7, tool 3).

    Order: the refusal check; the registry restore (so the atlas stops
    pointing at a folder about to go); the prune of the edit bench copies
    when the job never reached pushing (a failure to remove one is reported,
    not raised, and the job is still cancelled); the cleanup record; the
    transition to cancelled, which clears current.json.

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
        CancelRefused: when the job is terminal or a step is in flight.
        TypeError, ValueError: for bad initials or a malformed record.
    """
    who = actor(initials)
    job = store.load_job(job_id)
    refusal = cancel_refusal(job)
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
    "STATUS_COLUMN", "DONE_COLUMN", "LOCATION_COLUMN", "STATUS_AWAITING", "CANCEL_REASON", "CancelRefused",
    "actor", "reached_pushing", "cancel_refusal", "prune_targets", "summarise", "restore_registry",
    "cancel_and_prune",
]
