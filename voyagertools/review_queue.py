#!/usr/bin/env python3
"""
Module: voyagertools/review_queue.py
Purpose: The Review queue of the Atlas control panel (program C spec
         sections 3.6 and 4.10, milestone C6), as pure functions over the
         registry rows and the unfinished manual edit job records, so the
         route and the tests share one rule:
           - items: every transect with a registry row whose
             manual_edit_status is review (program A's rule, the one the
             atlas's own queue uses); the job record supplies the project,
             the round, the editor, the confirmation time and the check counts;
           - stopped_restarts: every transect whose start over stopped (state
             restarting with a recorded error), for Resume start over;
           - marked_for_upload: every transect an admin may still move back
             to admin review (marked for upload, no upload set holds it, its
             Shelf copy not verified yet; ruling O3 of the chain decisions);
           - unmatched: every registry row in review that no unfinished
             project holds.
Inputs:  registry rows (dicts), job records (dicts), a site name lookup, the
         registry's sort key, the registry's review status and the two
         transect rules of the manual edit job store (restart_stopped and
         can_return_to_review), passed in by the route, never copied here.
Outputs: one dict; nothing is written.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import NamedTuple

from . import timelines

# --- constants ---------------------------------------------------------------

LEVEL_STOP = "stop"   # a failed check (manual edit checks window, decision 2)
LEVEL_WARN = "warn"   # a warned check
NO_TIME = "~"         # sorts after every ISO stamp, so an unconfirmed transect comes last


class QueueRules(NamedTuple):
    """The manual edit job store's transect rules the queue applies (project dict -> bool)."""

    stopped: Callable[[Mapping], bool]      # jobstore.restart_stopped: a start over that stopped
    returnable: Callable[[Mapping], bool]   # jobstore.can_return_to_review: may go back to admin review


# --- helpers -----------------------------------------------------------------

def _mapping(value) -> Mapping:
    """The value when it is a mapping, else an empty one."""
    return value if isinstance(value, Mapping) else {}


def _text(value) -> str:
    """The value as stripped text; anything that is not text is ""."""
    return value.strip() if isinstance(value, str) else ""


def _round(value) -> int:
    """A review round: a whole number from 0; anything else reads as 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _check_counts(project: Mapping) -> tuple[int, int]:
    """(failed, warned) checks of the transect's latest pass."""
    items = _mapping(project.get("checks")).get("items")
    levels = [_mapping(item).get("level") for item in items] if isinstance(items, list) else []
    return levels.count(LEVEL_STOP), levels.count(LEVEL_WARN)


def _records(jobs) -> list[Mapping]:
    """The job records that carry a job id, in the given order."""
    if not isinstance(jobs, list):
        raise TypeError("queue_items needs the job records as a list.")
    return [job for job in jobs if isinstance(job, Mapping) and _text(job.get("job_id"))]


def _transect_base(job: Mapping, transect: Mapping, project: Mapping) -> dict:
    """The fields every list of the queue shares for one transect."""
    return {"job_id": job["job_id"], "psx": transect["psx"], "processing_folder": transect["processing_folder"],
            "readable_ids": list(transect["readable_ids"]), "site": transect["site"],
            "site_name": transect["site_name"], "transect": transect["transect"],
            "round": _round(project.get("round"))}


def _item(job: Mapping, transect: Mapping, project: Mapping) -> dict:
    """One In review row."""
    failed, warned = _check_counts(project)
    return dict(_transect_base(job, transect, project), state=_text(project.get("state")),
                editor=timelines.editor_of(project),
                confirmed_at=_text(_mapping(project.get("certification")).get("at")),
                failed_checks=failed, warned_checks=warned)


def _stopped(job: Mapping, transect: Mapping, project: Mapping) -> dict:
    """One Start over stopped row."""
    restart = _mapping(project.get("restart"))
    return dict(_transect_base(job, transect, project), stage=_text(restart.get("stage")),
                error=_text(restart.get("error")), at=_text(restart.get("at")))


# --- public ------------------------------------------------------------------

def _shelf_location(job: Mapping, psx: str) -> str:
    """The Shelf folder of the transect `psx`: the first of its job rows that names one, else ""."""
    rows = job.get("rows") if isinstance(job.get("rows"), list) else []
    return next((_text(row.get("shelf_location")) for row in rows
                 if isinstance(row, Mapping) and _text(row.get("psx_basename")) == psx
                 and _text(row.get("shelf_location"))), "")


def transect_of(job: Mapping, psx: str, site_name_fn: Callable[[str], str], sort_key: Callable = str) -> dict | None:
    """The transect `psx` of one job record, or None when the job holds no such psx.

    Parameters:
        job: a job record.
        psx: a Metashape project file name, for example MRS_T2_2023_2024.psx.
        site_name_fn: site code to full name.
        sort_key: the registry's readable id sort key.

    Returns:
        {psx, processing_folder, site, site_name, transect, readable_ids,
        shelf_location}, shelf_location being the Shelf folder a Start over
        pulls the fresh copy from ("" when no row names one).
    """
    transect = next((t for t in timelines.transects_of(job, sort_key, site_name_fn) if t["psx"] == psx), None)
    return None if transect is None else dict(transect, shelf_location=_shelf_location(job, psx))


def queue_items(rows: Iterable[Mapping], jobs: list, site_name_fn: Callable[[str], str], *,
                sort_key: Callable = str, status_review: str, rules: QueueRules) -> dict:
    """The Review queue (spec 4.10) without its read time.

    Parameters:
        rows: every registry row (manual_edit_status read from each).
        jobs: every unfinished manual edit job record.
        site_name_fn: site code to full name ("" when the site table has none).
        sort_key: the registry's readable id sort key.
        status_review: the registry status of a transect in admin review (registry.ME_REVIEW).
        rules: the job store's transect rules.

    Returns:
        {items, stopped_restarts, marked_for_upload, unmatched}: items oldest
        confirmation first, the two job lists in project order, unmatched
        readable ids in registry order.

    Raises:
        TypeError: rows or jobs of the wrong type.

    Example:
        queue_items(registry.load(), store.active_jobs(strict=True), site_name,
                    sort_key=registry.sort_key, status_review=registry.ME_REVIEW,
                    rules=QueueRules(jobstore.restart_stopped, jobstore.can_return_to_review))
    """
    if not isinstance(rows, (list, tuple)):
        raise TypeError("queue_items needs the registry rows as a list.")
    in_review = {_text(row.get("readable_id")) for row in rows
                 if isinstance(row, Mapping) and row.get("manual_edit_status") == status_review}
    in_review.discard("")
    held: set[str] = set()
    items, stopped, returnable = [], [], []
    for job in _records(jobs):
        projects = _mapping(job.get("projects"))
        for transect in timelines.transects_of(job, sort_key, site_name_fn):
            held.update(transect["readable_ids"])
            project = _mapping(projects.get(transect["psx"]))
            if in_review.intersection(transect["readable_ids"]):
                items.append(_item(job, transect, project))
            if rules.stopped(project):
                stopped.append(_stopped(job, transect, project))
            if rules.returnable(project):
                returnable.append(dict(_transect_base(job, transect, project), editor=timelines.editor_of(project)))
    items.sort(key=lambda item: (item["confirmed_at"] or NO_TIME, item["job_id"], item["psx"]))
    return {"items": items, "stopped_restarts": stopped, "marked_for_upload": returnable,
            "unmatched": sorted(in_review - held, key=sort_key)}
