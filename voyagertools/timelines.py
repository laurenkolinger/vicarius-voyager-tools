#!/usr/bin/env python3
"""
Module: voyagertools/timelines.py
Purpose: The figures of the Atlas control panel's Timelines pane (program C
         spec sections 3.9 and 4.12, milestone C8): per manual edit project
         and per transect, the time in each registry status (program A's
         state_intervals, never a stored counter, chain invariant 3), the
         time in Metashape (program B's jobstore.edit_time over the
         transect's open and close events), the job-record times (the pull,
         the Confirm of the checks, the upload, the removal from the edit
         bench) and the progress of anything running (the pull, an open
         checks run, an upload).
Inputs:  job records (manualedit.jobstore's shape), program A's interval and
         note readers, program B's edit-time counter, a site-name lookup and
         upload set records, each passed in by the caller.
Outputs: plain dicts and lists for the JSON payload; nothing is written.

A transect is one psx of one job (projects[<psx>]); its registry figures are
read from its first readable id in registry order, because program B writes
one status for every readable id of a transect at once. Only intervals that
start inside the project's window count: from the job's created_at up to the
created_at of the next job holding the same processing folder, so a
transect edited again in a later project is never counted twice.
"""
from __future__ import annotations

import statistics
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone

# --- constants ---------------------------------------------------------------

AST = timezone(timedelta(hours=-4))
STATUS_FIELD = "manual_edit_status"   # the registry column whose intervals are read
STATUS_EDITING = "editing"
STATUS_REVIEW = "review"
STATUS_UPLOAD = "upload"
STATUS_DONE = "done"
STATE_TODO = "todo"                   # a transect with no project record reads as not opened
STATE_DONE = "done"
STATE_UPLOADING = "uploading"
PHASE_PULLING = "pulling"
PHASE_EDITING = "editing"
OPEN_EVENT = "open"
COMPLETED_WINDOW_DAYS = 30
DAY_CHARS = 10                        # YYYY-MM-DD at the head of an ISO stamp
HALF_SECOND = 0.5
SUMMED_STATUSES = {STATUS_EDITING: "editing_s", STATUS_REVIEW: "review_s", STATUS_UPLOAD: "upload_wait_s"}


# --- small readers ------------------------------------------------------------

def parse_time(value) -> datetime | None:
    """An ISO stamp as an aware datetime (a naive one is AST); None when it does not parse.

    Example:
        parse_time("2026-09-25T12:00:00") -> 2026-09-25 12:00:00-04:00
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=AST)


def whole_seconds(value) -> int:
    """A non-negative number of seconds rounded half up to a whole number; 0 for anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
        return 0
    return int(value + HALF_SECOND)


def _mapping(value) -> Mapping:
    """The value when it is a mapping, else an empty one."""
    return value if isinstance(value, Mapping) else {}


def _text(value) -> str:
    """The value as stripped text; "" for None or a non-string."""
    return value.strip() if isinstance(value, str) else ""


def _count(value) -> int:
    """A whole number from 0, or 0 for anything else."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


# --- job records ---------------------------------------------------------------

def load_jobs(store, error_types: tuple) -> tuple[list[dict], list[dict]]:
    """Every job record the store holds, newest first, and the files it could not read.

    Parameters:
        store: a manualedit JobStore (list_jobs and load_job).
        error_types: the exceptions that mean one file cannot be read (the
            store's own JobStoreError family); anything else is raised.

    Returns:
        (jobs newest created_at first, skipped [{job_id, error}]).
    """
    jobs, skipped = [], []
    for summary in store.list_jobs():
        job_id = summary.get("job_id")
        if summary.get("corrupt"):
            skipped.append({"job_id": job_id, "error": str(summary.get("error") or "")})
            continue
        try:
            jobs.append(store.load_job(job_id))
        except error_types as exc:
            skipped.append({"job_id": job_id, "error": str(exc)})
    jobs.sort(key=lambda job: (_text(job.get("created_at")), _text(job.get("job_id"))), reverse=True)
    return jobs, skipped


def transects_of(job: Mapping, sort_key: Callable, site_name_fn: Callable[[str], str]) -> list[dict]:
    """The transects of one job in the order its rows first name each psx.

    Parameters:
        job: a job record.
        sort_key: the registry's readable id sort key (registry.sort_key).
        site_name_fn: site code to full name, used when the row carries none.

    Returns:
        [{psx, processing_folder, site, site_name, transect, readable_ids}],
        readable ids in registry order; a row missing its psx or id is skipped.
    """
    rows = job.get("rows") if isinstance(job.get("rows"), list) else []
    grouped: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        psx, rid = _text(row.get("psx_basename")), _text(row.get("readable_id"))
        if not psx or not rid:
            continue
        if psx not in grouped:
            site = _text(row.get("site"))
            grouped[psx] = {"psx": psx, "processing_folder": _text(row.get("processing_folder")), "site": site,
                            "site_name": _text(row.get("site_name")) or site_name_fn(site),
                            "transect": _text(row.get("transect")), "readable_ids": []}
        grouped[psx]["readable_ids"].append(rid)
    for transect in grouped.values():
        transect["readable_ids"].sort(key=sort_key)
    return list(grouped.values())


def windows(jobs: Iterable[Mapping]) -> dict[tuple[str, str], datetime | None]:
    """The end of each job's window per processing folder: the next job's created_at that holds it.

    Returns:
        {(job_id, processing_folder): the later job's created_at, or None}.
    """
    holders: dict[str, list[tuple[datetime, str]]] = {}
    for job in jobs:
        created = parse_time(job.get("created_at"))
        rows = job.get("rows") if isinstance(job.get("rows"), list) else []
        folders = {_text(r.get("processing_folder")) for r in rows if isinstance(r, Mapping)} - {""}
        for folder in folders:
            if created is not None:
                holders.setdefault(folder, []).append((created, _text(job.get("job_id"))))
    ends: dict[tuple[str, str], datetime | None] = {}
    for folder, held in holders.items():
        held.sort()
        for index, (_created, job_id) in enumerate(held):
            ends[(job_id, folder)] = held[index + 1][0] if index + 1 < len(held) else None
    return ends


def _in_window(interval: Mapping, since: datetime | None, until: datetime | None) -> bool:
    """Whether an interval starts inside [since, until); an unreadable start is never inside."""
    start = parse_time(interval.get("start"))
    if start is None:
        return False
    return (since is None or start >= since) and (until is None or start < until)


def durations(intervals, since: datetime | None, until: datetime | None) -> dict:
    """The registry status figures of one transect inside its project's window.

    Parameters:
        intervals: program A's intervals ({value, start, end, seconds, ...})
            of the transect's first readable id, oldest first; an open one
            is already measured to `now` by the reader, and one with
            clock_anomaly carries the reader's clamped seconds.
        since, until: the window (None leaves that side open).

    Returns:
        {pulled_at: start of the first editing spell, editing_s, review_s,
        upload_wait_s (summed whole seconds), uploaded_at: start of the done spell}.
    """
    out = {"pulled_at": None, "editing_s": 0, "review_s": 0, "upload_wait_s": 0, "uploaded_at": None}
    for spell in intervals if isinstance(intervals, list) else []:
        if not isinstance(spell, Mapping) or not _in_window(spell, since, until):
            continue
        value = spell.get("value")
        if value in SUMMED_STATUSES:
            out[SUMMED_STATUSES[value]] += whole_seconds(spell.get("seconds"))
        if value == STATUS_EDITING and out["pulled_at"] is None:
            out["pulled_at"] = spell.get("start")
        if value == STATUS_DONE and out["uploaded_at"] is None:
            out["uploaded_at"] = spell.get("start")
    return out


def _editor(project: Mapping, job: Mapping) -> str:
    """The transect's certifier, else the project's creator."""
    return _text(_mapping(project.get("certification")).get("by")) or _text(job.get("created_by"))


def _open_events(opens) -> list:
    """The events of a transect's opens list that are mappings."""
    return [event for event in opens if isinstance(event, Mapping)] if isinstance(opens, list) else []


def _last_note(notes: Iterable[Mapping], job_id: str, psx: str) -> str:
    """The text of the newest note of this project and transect; "" with none."""
    mine = [n for n in notes if n.get("job_id") == job_id and n.get("psx") == psx]
    mine.sort(key=lambda n: _text(n.get("at")))
    return _text(mine[-1].get("text")) if mine else ""


def upload_progress(upload_id: str, record: Mapping | None, job_id: str, psx: str, folder: str) -> dict:
    """What an upload set says of one transect now.

    Returns:
        {upload_id, status, item_state, bytes_done, bytes_total}; bytes_done
        only while the set is copying this transect's folder, bytes_total
        from the copy's own reading, else the item's real bytes.
    """
    record = _mapping(record)
    items = record.get("items") if isinstance(record.get("items"), list) else []
    item = next((i for i in items if isinstance(i, Mapping) and i.get("job_id") == job_id and i.get("psx") == psx), {})
    progress = _mapping(record.get("progress"))
    here = bool(folder) and progress.get("folder") == folder
    item_bytes = item.get("bytes") if isinstance(item.get("bytes"), int) else None
    return {"upload_id": upload_id, "status": _text(record.get("status")), "item_state": _text(item.get("state")),
            "bytes_done": progress.get("folder_bytes_done") if here else None,
            "bytes_total": progress.get("folder_bytes_total") if here else item_bytes}


def _row(job: Mapping, transect: Mapping, project: Mapping, figures: Mapping, metashape: Mapping, now: datetime,
         until: datetime | None, notes: list, upload_fn, state_words: Mapping) -> dict:
    """One transect row of the payload (spec 4.12 plus the job-record times).

    The total runs from the pull to the upload, else to the next project
    holding the transect (`until`), else to now.
    """
    job_id = _text(job.get("job_id"))
    state = _text(project.get("state")) or STATE_TODO
    pulled = parse_time(figures["pulled_at"])
    ended = parse_time(figures["uploaded_at"]) or (min(until, now) if until is not None else now)
    upload_id = _text(project.get("upload_id"))
    upload = None
    if state == STATE_UPLOADING and upload_id:
        record = upload_fn(upload_id) if upload_fn is not None else None
        upload = upload_progress(upload_id, record, job_id, transect["psx"], transect["processing_folder"])
    return {
        "job_id": job_id, "psx": transect["psx"], "processing_folder": transect["processing_folder"],
        "readable_ids": list(transect["readable_ids"]), "site": transect["site"], "site_name": transect["site_name"],
        "transect": transect["transect"], "editor": _editor(project, job), "round": _count(project.get("round")),
        "state": state, "state_word": str(state_words.get(state, state)), "created_at": job.get("created_at"),
        "phase": job.get("phase"),
        **figures,
        "metashape_s": whole_seconds(metashape.get("seconds")), "unknown_opens": _count(metashape.get("unknown_opens")),
        "opens": sum(1 for e in _open_events(project.get("opens")) if e.get("event") == OPEN_EVENT),
        "total_s": whole_seconds((ended - pulled).total_seconds()) if pulled is not None else None,
        "confirmed_at": _mapping(project.get("certification")).get("at"), "removed_at": project.get("removed_at"),
        "checks_open": isinstance(project.get("checks_run"), Mapping),
        "upload": upload, "last_note": _last_note(notes, job_id, transect["psx"]),
    }


def project_rows(jobs: list[Mapping], intervals_fn: Callable, metashape_fn: Callable, site_name_fn: Callable,
                 now: datetime, *, notes=(), upload_fn: Callable | None = None, state_words: Mapping | None = None,
                 sort_key: Callable = str) -> list[dict]:
    """One row per transect of every job, jobs in the order given (newest first), transects in row order.

    Parameters:
        jobs: job records.
        intervals_fn: [readable ids] -> {id: program A's intervals}, one read for all.
        metashape_fn: a transect's opens list -> {seconds, unknown_opens}.
        site_name_fn: site code -> full name.
        now: the time of the reading.
        notes: program A's note dicts (the last note of each transect).
        upload_fn: upload id -> the set record, or None when it cannot be read.
        state_words: transect state -> the word a card shows.
        sort_key: the registry's readable id order.
    """
    words = state_words or {}
    ends = windows(jobs)
    plan = []
    for job in jobs:
        projects = _mapping(job.get("projects"))
        for transect in transects_of(job, sort_key, site_name_fn):
            plan.append((job, transect, _mapping(projects.get(transect["psx"]))))
    readings = intervals_fn([transect["readable_ids"][0] for _job, transect, _p in plan]) if plan else {}
    note_list = [n for n in notes if isinstance(n, Mapping)]
    rows = []
    for job, transect, project in plan:
        since = parse_time(job.get("created_at"))
        until = ends.get((_text(job.get("job_id")), transect["processing_folder"]))
        figures = durations(readings.get(transect["readable_ids"][0], []), since, until)
        metashape = metashape_fn(_open_events(project.get("opens")))
        rows.append(_row(job, transect, project, figures, _mapping(metashape), now, until, note_list, upload_fn,
                         words))
    return rows


def _phase_time(history, phase: str) -> str | None:
    """The time a job first entered `phase`, from its phase history; None when it never did."""
    for entry in history if isinstance(history, list) else []:
        if isinstance(entry, Mapping) and entry.get("phase") == phase:
            return entry.get("at")
    return None


def _pull(job: Mapping) -> dict:
    """The pull's start and end from the phase history, and its bytes while it runs."""
    progress = _mapping(_mapping(job.get("transfer_down")).get("progress"))
    running = job.get("phase") == PHASE_PULLING
    return {"started_at": _phase_time(job.get("phase_history"), PHASE_PULLING),
            "finished_at": _phase_time(job.get("phase_history"), PHASE_EDITING),
            "bytes_done": progress.get("bytes_done") if running else None,
            "bytes_total": progress.get("bytes_total") if running else None}


def project_summaries(jobs: list[Mapping], states: Iterable[str]) -> list[dict]:
    """One summary per job in the order given: who, when, finished, completed, and its transects by state.

    Parameters:
        jobs: job records.
        states: every transect state (jobstore.PROJECT_STATES), for the order of by_state.
    """
    order = list(states)
    out = []
    for job in jobs:
        projects = _mapping(job.get("projects"))
        psxs = [t["psx"] for t in transects_of(job, str, lambda code: "")]
        counts: dict[str, int] = {}
        for psx in psxs:
            state = _text(_mapping(projects.get(psx)).get("state")) or STATE_TODO
            counts[state] = counts.get(state, 0) + 1
        by_state = {state: counts[state] for state in order if state in counts}
        by_state.update({state: n for state, n in counts.items() if state not in by_state})
        out.append({"job_id": job.get("job_id"), "created_by": job.get("created_by"),
                    "created_at": job.get("created_at"), "phase": job.get("phase"),
                    "finished_at": _mapping(job.get("finished")).get("at"), "completed_at": job.get("completed_at"),
                    "transects": len(psxs), "uploaded": counts.get(STATE_DONE, 0), "by_state": by_state,
                    "pull": _pull(job)})
    return out


def summary(rows: list[Mapping], projects: list[Mapping], states: Iterable[str], now: datetime,
            skipped: list[dict]) -> dict:
    """The summary line's figures over every row and project (never the filtered rows).

    Returns:
        {projects, transects, by_state (every state, 0 when absent),
        median_metashape_s (over uploaded transects, None with none),
        completed_last_30_days, skipped}.
    """
    by_state = {state: 0 for state in states}
    for row in rows:
        by_state[row.get("state")] = by_state.get(row.get("state"), 0) + 1
    done = [whole_seconds(r.get("metashape_s")) for r in rows if r.get("state") == STATE_DONE]
    cutoff = now - timedelta(days=COMPLETED_WINDOW_DAYS)
    completed = sum(1 for p in projects if (at := parse_time(p.get("completed_at"))) is not None and cutoff <= at <= now)
    return {"projects": len(projects), "transects": len(rows), "by_state": by_state,
            "median_metashape_s": whole_seconds(statistics.median(done)) if done else None,
            "completed_last_30_days": completed, "skipped": list(skipped)}


def filter_rows(rows: list[Mapping], job: str = "", state: str = "", editor: str = "", since: str = "") -> list:
    """The rows matching every filter given; a blank filter matches everything.

    Parameters:
        job: a job id; state: a transect state; editor: initials;
        since: YYYY-MM-DD, compared with the project's creation day.
    """
    return [row for row in rows
            if (not job or row.get("job_id") == job) and (not state or row.get("state") == state)
            and (not editor or row.get("editor") == editor)
            and (not since or _text(row.get("created_at"))[:DAY_CHARS] >= since)]


def detail(job: Mapping, psx: str, intervals_by_id: Mapping, notes: Iterable[Mapping], since: datetime | None,
           until: datetime | None) -> dict:
    """The drawer of one transect: its status intervals per readable id and its notes, inside its window.

    Parameters:
        job: the job record; psx: the transect.
        intervals_by_id: {readable id: program A's intervals}.
        notes: program A's note dicts; one note written for several readable
            ids (one group_id) shows once.
        since, until: the project's window.

    Returns:
        {job_id, psx, intervals: {readable id: [intervals, whole seconds]},
        notes: [notes of this job and psx, oldest first]}.

    Raises:
        KeyError: when the job holds no transect `psx`.
    """
    transect = next((t for t in transects_of(job, str, lambda code: "") if t["psx"] == psx), None)
    if transect is None:
        raise KeyError(f"{psx} is not a transect of manual edit project {job.get('job_id')}.")
    intervals = {}
    for rid in sorted(transect["readable_ids"]):
        spells = intervals_by_id.get(rid) if isinstance(intervals_by_id.get(rid), list) else []
        intervals[rid] = [dict(s, seconds=whole_seconds(s.get("seconds"))) for s in spells
                          if isinstance(s, Mapping) and _in_window(s, since, until)]
    seen, kept = set(), []
    for note in notes:
        if not isinstance(note, Mapping) or note.get("job_id") != job.get("job_id") or note.get("psx") != psx:
            continue
        group = note.get("group_id") or note.get("note_id")
        if group in seen:
            continue
        seen.add(group)
        kept.append(dict(note))
    kept.sort(key=lambda n: _text(n.get("at")))
    return {"job_id": job.get("job_id"), "psx": psx, "intervals": intervals, "notes": kept}


# --- the only callers of the sibling programs' readers ---------------------------------

def _intervals_many(registry, readable_ids: list[str], now: datetime) -> dict[str, list[dict]]:
    """Program A's intervals of many readable ids from one read of the events file."""
    return registry.state_intervals_many(readable_ids, STATUS_FIELD, now)


def _notes(registry) -> list[dict]:
    """Every note program A's notes sidecar holds, in file order (read once per request)."""
    return registry.notes()


def _metashape_seconds(jobstore_module, opens, now: datetime) -> dict:
    """Program B's edit time over a transect's opens list: {seconds, unknown_opens}, never live."""
    events = _open_events(opens)
    return jobstore_module.edit_time(events, now, live=False)
