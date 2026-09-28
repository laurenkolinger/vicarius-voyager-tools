#!/usr/bin/env python3
"""
Module: voyagertools/clean_manual_edit.py
Purpose: The clean manual edit run tool (program spec section 7, tool 3;
         chain program C section 5.3; hardening finding F14): the summary of
         each unfinished manual edit project the page lists. The cancel
         itself is the manual edit module's one cancel
         (manualedit.transfers.cancel_job with prune=True), which the route
         in views.py calls, so the walkthrough's Cancel project and this
         page's Cancel and prune judge a project by one rule and remove a
         bench folder only through the manual edit bench removal.
Inputs:  a manual edit job record, the tail of its log, the one cancel's
         refusal sentence and its prune plan (manualedit.transfers
         cancel_refusal and prune_plan).
Outputs: a plain summary dict for the page. Nothing here writes.
"""
from __future__ import annotations

from collections.abc import Mapping

HISTORY_TAIL = 8
# The phase history reason the one cancel records for a cancel from this page.
CANCEL_REASON = "cancelled from the Atlas control panel"
ROW_KEYS = ("readable_id", "site", "site_name", "transect", "local_path", "shelf_location")
HISTORY_KEYS = ("phase", "at", "by", "reason")


def _require_job(job) -> Mapping:
    """`job` as a mapping with a job_id and a phase.

    Raises:
        TypeError: when it is not a manual edit job record.
    """
    if not isinstance(job, Mapping) or not isinstance(job.get("job_id"), str) or not isinstance(job.get("phase"), str):
        raise TypeError("job must be a manual edit job record (a dict with job_id and phase)")
    return job


def _require_plan(plan) -> list:
    """The prune plan as a list of {folder, local_path, keep} mappings.

    Raises:
        TypeError: when it is not a list of mappings.
    """
    if not isinstance(plan, list) or any(not isinstance(entry, Mapping) for entry in plan):
        raise TypeError("plan must be a list of {folder, local_path, keep} entries (manualedit.transfers.prune_plan)")
    return plan


def summarise(job, tail_lines=None, refusal: str = "", plan=None) -> dict:
    """What the tool page shows for one unfinished job.

    Parameters:
        job: the job record.
        tail_lines: the last lines of the job log.
        refusal: the one cancel's sentence when it refuses now ("" when the
            project can be cancelled).
        plan: manualedit.transfers.prune_plan(job): each bench folder with
            the sentence that keeps it, or "" when Cancel and prune removes it.

    Returns:
        {job_id, phase, created_by, created_at, edit_bench, rows: [{readable_id,
         site, site_name, transect, local_path, shelf_location}], history:
         [last HISTORY_TAIL entries], log_tail, prune_targets (the folders
         Cancel and prune removes), prune_kept (the sentences of the folders
         it keeps), refusal, can_cancel}.

    Raises:
        TypeError: for a record that is not a job, a refusal that is not
            text, or a plan that is not a list of entries.
    """
    job = _require_job(job)
    if not isinstance(refusal, str):
        raise TypeError(f"refusal must be a string, got {type(refusal).__name__}")
    entries = _require_plan([] if plan is None else plan)
    history = [h for h in (job.get("phase_history") or []) if isinstance(h, Mapping)]
    return {
        "job_id": job["job_id"], "phase": job["phase"],
        "created_by": str(job.get("created_by") or ""), "created_at": str(job.get("created_at") or ""),
        "edit_bench": str(job.get("edit_bench") or ""),
        "rows": [{key: str(row.get(key) or "") for key in ROW_KEYS}
                 for row in (job.get("rows") or []) if isinstance(row, Mapping)],
        "history": [{key: str(h.get(key) or "") for key in HISTORY_KEYS} for h in history[-HISTORY_TAIL:]],
        "log_tail": list(tail_lines or []),
        "prune_targets": [str(entry.get("folder") or "") for entry in entries if not entry.get("keep")],
        "prune_kept": [str(entry["keep"]) for entry in entries if entry.get("keep")],
        "refusal": refusal, "can_cancel": not refusal,
    }


__all__ = ["HISTORY_TAIL", "CANCEL_REASON", "summarise"]
