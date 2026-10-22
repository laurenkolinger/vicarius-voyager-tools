#!/usr/bin/env python3
"""
Module: voyagertools/v1runs.py
Purpose: Turns the driver's GET /api/status answer into the model of the
         Voyager 1 batch pane: the batch line and one row per turn, with the
         transect and its full site name, the four videos, the status, the
         video being built and its hours against the time limit, the flags
         (over the limit, build process gone, flushed) and the actions that
         apply (Flush, Move). Pure functions: no network, no files; a status
         that is missing, malformed or partly filled still gives a model.
Inputs:  the status dict the driver answered; a callable mapping a site code
         to its full name.
Outputs: a plain dict the route serialises. Every text in it is stripped of
         control characters and cut to a safe length; the page escapes it
         again when it draws it.

Driver fields read (see the plan 2026-10-21-voyager1-flush-time-limit-shelf-move.md):
    status["batch"]: batch_id, run_id, status, shelf_root, initials, turns, time_limit_hours
    status["engine_alive"], status["time_limit_hours"]
    turn: id, status, type, phase, select.ids, transect, flushed, job, job_alive,
          video_hours {readable_id: hours}, time_limit_hours
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping

MAX_TEXT_CHARS = 400
MAX_TURNS = 500
MAX_VIDEOS_PER_TURN = 12
HOURS_DECIMALS = 1
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_ERROR = "error"
STATUS_STOPPED = "stopped"
STATUS_DONE = "done"
STATUS_SCRAPPED = "scrapped"
STATUS_FLUSHED = "flushed"
# A turn the operator may flush: the build started and did not finish, was not given up.
FLUSHABLE_STATUSES = (STATUS_RUNNING, STATUS_PAUSED, STATUS_ERROR, STATUS_STOPPED)

# The chip colour of a status, on the eight-role vocabulary of the matrix window design.
STATUS_ROLES = {
    STATUS_PENDING: "role-planned",
    STATUS_RUNNING: "role-in-progress",
    STATUS_PAUSED: "role-attention",
    STATUS_STOPPED: "role-attention",
    STATUS_ERROR: "role-failed",
    STATUS_DONE: "role-verified",
    STATUS_SCRAPPED: "role-empty",
    STATUS_FLUSHED: "role-empty",
}
ROLE_UNKNOWN = "role-attention"

NO_BATCH_LINE = "The driver has no Voyager 1 batch."


def clean_text(value, limit: int = MAX_TEXT_CHARS) -> str:
    """A display string from any value: control characters removed, spaces collapsed, cut to limit characters.

    Parameters:
        value: anything; None gives "".
        limit: the longest string returned.
    Returns:
        A plain string. Markup characters stay as they are: the page escapes them.
    """
    if value is None or isinstance(value, (dict, list)):
        return ""
    text = " ".join(CONTROL_CHARS.sub(" ", str(value)).split())
    return text[:limit]


def as_hours(value) -> float | None:
    """Hours as a finite non-negative number rounded to one decimal, else None (bool and text refused)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return round(float(value), HOURS_DECIMALS)


def _mapping(value) -> Mapping:
    """The value when it is a mapping, else an empty one."""
    return value if isinstance(value, Mapping) else {}


def video_ids(turn: Mapping) -> list[str]:
    """The readable ids of a turn's videos, from turn.select.ids, cleaned and capped."""
    ids = _mapping(turn.get("select")).get("ids")
    if not isinstance(ids, list):
        return []
    return [clean_text(i, 80) for i in ids[:MAX_VIDEOS_PER_TURN] if isinstance(i, str) and clean_text(i, 80)]


def transect_code(turn: Mapping, ids: list[str]) -> str:
    """The transect code, such as BIT_T3: the turn's own `transect`, else the first two parts of its first video id."""
    named = clean_text(turn.get("transect"), 40)
    if named:
        return named
    first = ids[0].split("_") if ids else []
    return "_".join(first[:2]) if len(first) >= 2 else ""


def _flushed_record(turn: Mapping) -> dict | None:
    """Who flushed the turn, when and why, or None when it was not flushed.

    A turn counts as flushed when it carries a `flushed` record or its status is flushed.
    """
    record = turn.get("flushed")
    if isinstance(record, Mapping) and record:
        return {"by": clean_text(record.get("by"), 40), "at": clean_text(record.get("at"), 40),
                "reason": clean_text(record.get("reason")), "trigger": clean_text(record.get("trigger"), 40),
                "requeued_as": record.get("requeued_as") if isinstance(record.get("requeued_as"), int)
                and not isinstance(record.get("requeued_as"), bool) else None}
    if turn.get("status") == STATUS_FLUSHED:
        return {"by": "", "at": "", "reason": "", "trigger": "", "requeued_as": None}
    return None


def _hours_by_video(turn: Mapping) -> dict[str, float]:
    """The hours each video has been built for, from turn.video_hours; entries that are not hours are dropped."""
    raw = _mapping(turn.get("video_hours"))
    out: dict[str, float] = {}
    for rid, value in list(raw.items())[:MAX_VIDEOS_PER_TURN]:
        hours = as_hours(value)
        name = clean_text(rid, 80)
        if hours is not None and name:
            out[name] = hours
    return out


def _limit_for(turn: Mapping, status: Mapping, batch: Mapping) -> float | None:
    """The time limit in hours: the turn's, else the status's, else the batch's; None when none is reported."""
    for source in (turn, status, batch):
        hours = as_hours(source.get("time_limit_hours"))
        if hours is not None and hours > 0:
            return hours
    return None


def _build_gone(turn: Mapping, status: Mapping) -> bool:
    """Whether a running turn has no live build process: its job_alive is false, or, when the driver gives none, the engine is not alive."""
    if turn.get("status") != STATUS_RUNNING:
        return False
    if isinstance(turn.get("job_alive"), bool):
        return not turn["job_alive"]
    return status.get("engine_alive") is False


def _actions(state: str, flushed: bool) -> dict:
    """Which buttons apply: Flush for a started, unfinished, not given up turn; Move for a pending turn."""
    return {"flush": state in FLUSHABLE_STATUSES and not flushed,
            "move": state == STATUS_PENDING and not flushed}


def turn_row(turn: Mapping, status: Mapping, batch: Mapping, site_name: Callable[[str], str]) -> dict:
    """The pane row of one turn.

    Parameters:
        turn: one entry of batch.turns.
        status: the whole driver status (for engine_alive and a top-level limit).
        batch: status["batch"].
        site_name: maps a site code to its full name; "" when unknown.
    Returns:
        id, number, transect, site_code, site_name, video_ids, status, role, current_video, current_hours,
        limit_hours, over_limit, build_gone, flushed, actions (flush, move); pending_index and pending_count
        are added by build_model.
    """
    ids = video_ids(turn)
    code = transect_code(turn, ids)
    site = code.split("_")[0] if code else ""
    try:
        full_name = clean_text(site_name(site), 120) if site else ""
    except Exception:  # noqa: BLE001  (a name table that fails must not blank the pane; the code still shows)
        full_name = ""
    flushed = _flushed_record(turn)
    state = clean_text(turn.get("status"), 40) or "unknown"
    if flushed is not None:
        state = STATUS_FLUSHED
    hours = _hours_by_video(turn)
    current = max(hours, key=hours.get) if hours else None
    limit = _limit_for(turn, status, batch)
    current_hours = hours[current] if current else None
    turn_id = turn.get("id")
    return {
        "id": turn_id if isinstance(turn_id, int) and not isinstance(turn_id, bool) else None,
        "transect": code, "site_code": site, "site_name": full_name, "video_ids": ids,
        "status": state, "role": STATUS_ROLES.get(state, ROLE_UNKNOWN),
        "current_video": current, "current_hours": current_hours, "limit_hours": limit,
        "over_limit": bool(current_hours is not None and limit is not None and current_hours > limit),
        "build_gone": _build_gone(turn, status), "flushed": flushed,
        "actions": _actions(state, flushed is not None),
        "pending_index": None, "pending_count": 0,
    }


def _batch_line(status: Mapping, batch: Mapping, rows: list[dict]) -> dict:
    """The batch part of the model: ids, status, Shelf folder and the time limit."""
    limit = next((r["limit_hours"] for r in rows if r["limit_hours"] is not None), None)
    if limit is None:
        limit = _limit_for({}, status, batch)
    return {"batch_id": clean_text(batch.get("batch_id"), 80), "run_id": clean_text(batch.get("run_id"), 200),
            "status": clean_text(batch.get("status"), 40) or "unknown",
            "role": STATUS_ROLES.get(clean_text(batch.get("status"), 40), ROLE_UNKNOWN),
            "shelf_root": clean_text(batch.get("shelf_root"), 1024), "time_limit_hours": limit}


def build_model(status, site_name: Callable[[str], str]) -> dict:
    """The model of the Voyager 1 batch pane from the driver status.

    Parameters:
        status: the dict GET /api/status answered; anything else gives the no-batch model.
        site_name: maps a site code to its full name.
    Returns:
        {"batch": dict or None, "turns": [row], "line": a sentence when there is no batch or no turns,
        "truncated": True when more than MAX_TURNS turns were cut}. Pending turns carry pending_index
        (0 for the next to run) and pending_count, which the Move controls use.
    """
    status = _mapping(status)
    batch = _mapping(status.get("batch"))
    if not batch:
        return {"batch": None, "turns": [], "line": NO_BATCH_LINE, "truncated": False}
    raw_turns = batch.get("turns") if isinstance(batch.get("turns"), list) else []
    rows = [turn_row(t, status, batch, site_name) for t in raw_turns[:MAX_TURNS] if isinstance(t, Mapping)]
    pending = [r for r in rows if r["status"] == STATUS_PENDING]
    for index, row in enumerate(pending):
        row["pending_index"], row["pending_count"] = index, len(pending)
    return {"batch": _batch_line(status, batch, rows), "turns": rows,
            "line": "" if rows else "The batch has no turns.", "truncated": len(raw_turns) > MAX_TURNS}
