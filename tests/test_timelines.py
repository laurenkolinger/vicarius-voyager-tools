#!/usr/bin/env python3
"""
Module: tests/test_timelines.py
Purpose: The Timelines pane's server side (program C milestone C8, spec
         sections 3.9 and 4.12): voyagertools/timelines.py over fake job
         records, a fake registry reader and a fake edit-time counter, and
         the routes GET /voyager-tools/api/timelines and
         /voyager-tools/api/timelines/detail over a temporary registry root
         and a temporary manual edit job store (the gates, every refusal,
         and one run through program A's real interval and notes readers).
Inputs:  temporary directories only; no NAS, no live registry, no live job.
Outputs: unittest results; every temporary directory is removed in tearDown.

Run from the clone root:
    python3 -m unittest tests.test_timelines -v
"""
from __future__ import annotations

import copy
import csv
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, Flask

HERE = Path(__file__).resolve().parent
CLONE = HERE.parent
sys.path.insert(0, str(CLONE))
VICARIUS_ROOT = os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius")
REGISTRY_LIB_DIR = os.path.join(VICARIUS_ROOT, "_METADATA", "3d")
MANUAL_EDIT_CLONE = os.path.join(VICARIUS_ROOT, "modules", "manual_edit", "github_repo")
for path in (REGISTRY_LIB_DIR, MANUAL_EDIT_CLONE):
    if path not in sys.path:
        sys.path.insert(0, path)

from manualedit import jobstore  # noqa: E402
from manualedit import transfers  # noqa: E402
from manualedit import views as manual_views  # noqa: E402
from voyagertools import timelines as tl  # noqa: E402
from voyagertools import views  # noqa: E402

AST = timezone(timedelta(hours=-4))
NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=AST)
STATES = jobstore.PROJECT_STATES
JOB_OLD = "ME-20260920-090000-LO"
JOB_NEW = "ME-20260925-100000-CC"
T1_IDS = ["MRS_T1_2023ann", "MRS_T1_2024_pbl", "MRS_T1_2024ann", "MRS_T1_2025_pbl"]
T2_IDS = ["MRS_T2_2023ann", "MRS_T2_2024ann"]
T3_IDS = ["LBH_T3_2024ann", "LBH_T3_2025_pbl"]
PSX_T1 = "MRS_T1_2023_2025.psx"
PSX_T2 = "MRS_T2_2023_2024.psx"
PSX_T3 = "LBH_T3_2024_2025.psx"
UPLOAD_ID = "UP-20260927-110000-LO"


def iso(day: int, hour: int, minute: int = 0) -> str:
    """A September 2026 AST stamp with offset."""
    return datetime(2026, 9, day, hour, minute, tzinfo=AST).isoformat(timespec="seconds")


def rows_for(ids, psx, site, transect, site_name=""):
    """Job rows of one transect, in the order the picker wrote them."""
    folder = f"{site}_{transect}_3D"
    return [{"readable_id": rid, "site": site, "site_name": site_name, "transect": transect,
             "processing_folder": folder, "shelf_location": f"146.226.147.140:/volume6/x/{folder}",
             "psx_basename": psx, "local_path": f"/tmp/bench/{folder}"} for rid in ids]


def project(state, **extra):
    """A transect record with the keys the timelines read."""
    base = {"state": state, "round": 0, "opens": [], "certification": None, "checks_run": None,
            "upload_id": None, "removed_at": None, "history": []}
    base.update(extra)
    return base


def old_job():
    """A finished project: MRS T1 uploaded, verified and removed."""
    return {"job_id": JOB_OLD, "created_at": iso(20, 9), "created_by": "LO", "phase": "done",
            "phase_history": [{"phase": "draft", "at": iso(20, 9), "by": "LO", "reason": ""},
                              {"phase": "pulling", "at": iso(20, 9, 5), "by": "LO", "reason": ""},
                              {"phase": "editing", "at": iso(20, 11), "by": "LO", "reason": ""},
                              {"phase": "done", "at": iso(23, 10), "by": "LO", "reason": ""}],
            "rows": rows_for(list(reversed(T1_IDS)), PSX_T1, "MRS", "T1"),
            "projects": {PSX_T1: project("done", certification={"by": "LO", "at": iso(22, 9)},
                                         opens=[{"event": "open", "at": iso(21, 9)},
                                                {"event": "close", "at": iso(21, 11)},
                                                {"event": "open", "at": iso(21, 13)}],
                                         removed_at=iso(23, 9))},
            "finished": {"by": "LO", "at": iso(22, 10)}, "completed_at": iso(23, 10),
            "transfer_down": {"status": "done", "progress": None}}


def new_job():
    """An unfinished project: MRS T2 in admin review (round 1), LBH T3 uploading."""
    return {"job_id": JOB_NEW, "created_at": iso(25, 10), "created_by": "CC", "phase": "editing",
            "phase_history": [{"phase": "pulling", "at": iso(25, 10, 1), "by": "CC", "reason": ""},
                              {"phase": "editing", "at": iso(25, 12), "by": "CC", "reason": ""}],
            "rows": rows_for(T2_IDS, PSX_T2, "MRS", "T2") + rows_for(T3_IDS, PSX_T3, "LBH", "T3",
                                                                     "Lang Bank Red Hind FSA"),
            "projects": {PSX_T2: project("review", round=1, certification={"by": "CC", "at": iso(26, 15)},
                                         checks_run={"run_id": "CR-20260927-100000-CC"}),
                         PSX_T3: project("uploading", upload_id=UPLOAD_ID,
                                         certification={"by": "LO", "at": iso(26, 16)})},
            "finished": None, "completed_at": None,
            "transfer_down": {"status": "done", "progress": {"bytes_done": 10, "bytes_total": 20}}}


def interval(value, start, end, seconds, open_=False, anomaly=False, actor="manual_edit:LO"):
    """One interval in program A's shape."""
    return {"value": value, "start": start, "end": end, "seconds": float(seconds), "actor": actor,
            "end_actor": None if open_ else actor, "open": open_, "ended_by": None if open_ else "change",
            "clock_anomaly": anomaly}


INTERVALS = {
    # MRS T1: pulled on the 20th, two editing spells around a review, then upload and done.
    "MRS_T1_2023ann": [interval("awaiting", iso(19, 8), iso(20, 11), 97200),
                       interval("editing", iso(20, 11), iso(22, 9), 165600),
                       interval("upload", iso(22, 9), iso(22, 12), 10800),
                       interval("done", iso(22, 12), None, 432000, open_=True)],
    "MRS_T2_2023ann": [interval("editing", iso(25, 12), iso(26, 15), 97200),
                       interval("review", iso(26, 15), None, 75600.4, open_=True)],
    "LBH_T3_2024ann": [interval("editing", iso(25, 12), iso(26, 16), 100800),
                       interval("upload", iso(26, 16), None, 72000, open_=True)],
}


def fake_intervals(asked):
    """The fake reader: every id it is asked for, from INTERVALS (a copy, so a test may not change them)."""
    def read(ids):
        asked.extend(ids)
        return {rid: copy.deepcopy(INTERVALS.get(rid, [])) for rid in ids}
    return read


def fake_metashape(opens):
    """A fake edit_time: 60 seconds per open event, and one unknown open when the last event is an open."""
    events = [e for e in opens if isinstance(e, dict)]
    count = sum(1 for e in events if e.get("event") == "open")
    unknown = 1 if events and events[-1].get("event") == "open" else 0
    return {"seconds": 60 * count, "unknown_opens": unknown}


UPLOAD_SET = {"upload_id": UPLOAD_ID, "status": "running",
              "items": [{"job_id": JOB_NEW, "psx": PSX_T3, "processing_folder": "LBH_T3_3D", "state": "copying",
                         "bytes": 400}],
              "progress": {"folder": "LBH_T3_3D", "folder_bytes_done": 100, "folder_bytes_total": 400,
                           "bytes_done": 100, "bytes_total": 400}}


def build_rows(jobs, asked=None, notes=(), upload=None, upload_error=""):
    """project_rows over the fakes, sorting readable ids by their text; `upload` is what the set reader answers."""
    reader = (lambda uid: (upload, upload_error)) if upload is not None or upload_error else None
    return tl.project_rows(jobs, fake_intervals(asked if asked is not None else []), fake_metashape,
                           lambda code: {"MRS": ""}.get(code, "?"), NOW, notes=list(notes), upload_fn=reader,
                           live_upload_statuses=jobstore.UPLOAD_LIVE_STATUSES, state_words=jobstore.STATE_WORDS,
                           sort_key=lambda rid: rid)


def stopped_job(phase, stopped_hour):
    """The old project, stopped on the 21st at `stopped_hour` in `phase` (cancelled or failed_pulling), never uploaded.

    Its MRS T1 intervals run editing from the 20th and never reach done, and
    no later project takes the transect, so only the stop can end its total.
    """
    job = old_job()
    job["phase"] = phase
    job["phase_history"] = job["phase_history"][:3] + [{"phase": phase, "at": iso(21, stopped_hour), "by": "LO",
                                                        "reason": ""}]
    job["projects"][PSX_T1]["state"] = "editing"
    job["completed_at"] = None
    return job


def open_editing(ids):
    """A reader whose first id has one editing spell from the 20th at 11:00, still open at NOW."""
    def read(asked):
        return {rid: [interval("editing", iso(20, 11), None, (NOW - datetime.fromisoformat(iso(20, 11))).total_seconds(),
                               open_=True)] for rid in asked}
    return read


class FakeError(Exception):
    """A store error of the fake store."""


class FakeStore:
    """list_jobs and load_job over in-memory records, one of them unreadable."""

    def __init__(self, records, broken=(), corrupt=()):
        self.records = {r["job_id"]: r for r in records}
        self.broken = set(broken)
        self.corrupt = set(corrupt)

    def list_jobs(self):
        out = [{"job_id": jid, "corrupt": False} for jid in self.records]
        out += [{"job_id": jid, "corrupt": True, "error": f"the job record {jid} is not JSON"} for jid in self.corrupt]
        return out

    def load_job(self, job_id):
        if job_id in self.broken:
            raise FakeError(f"the job record {job_id} vanished")
        return copy.deepcopy(self.records[job_id])


class LoadJobs(unittest.TestCase):
    def test_newest_first_and_every_unreadable_file_named(self):
        store = FakeStore([old_job(), new_job(), dict(old_job(), job_id="ME-20260921-090000-AB",
                                                      created_at=iso(21, 9))],
                          broken=["ME-20260921-090000-AB"], corrupt=["ME-20260922-090000-XY"])
        jobs, skipped = tl.load_jobs(store, (FakeError,))
        self.assertEqual([j["job_id"] for j in jobs], [JOB_NEW, JOB_OLD])
        self.assertEqual(sorted(s["job_id"] for s in skipped), ["ME-20260921-090000-AB", "ME-20260922-090000-XY"])
        self.assertTrue(all(s["error"] for s in skipped))

    def test_an_unexpected_error_is_not_swallowed(self):
        store = FakeStore([old_job()], broken=[JOB_OLD])
        with self.assertRaises(FakeError):
            tl.load_jobs(store, (KeyError,))

    def test_no_jobs(self):
        self.assertEqual(tl.load_jobs(FakeStore([]), (FakeError,)), ([], []))


class Transects(unittest.TestCase):
    def test_grouped_by_psx_in_row_order_with_sorted_ids(self):
        out = tl.transects_of(new_job(), sort_key=lambda rid: rid, site_name_fn=lambda code: "Meri")
        self.assertEqual([t["psx"] for t in out], [PSX_T2, PSX_T3])
        self.assertEqual(out[0]["readable_ids"], sorted(T2_IDS))
        self.assertEqual(out[0]["site_name"], "Meri", "a blank row name is filled from the site table")
        self.assertEqual(out[1]["site_name"], "Lang Bank Red Hind FSA", "the row's own name wins")
        self.assertEqual(out[1]["processing_folder"], "LBH_T3_3D")

    def test_hostile_rows_are_skipped(self):
        job = new_job()
        job["rows"] = [None, "x", {"readable_id": "A", "psx_basename": ""}, {"psx_basename": "a.psx"}] + job["rows"]
        out = tl.transects_of(job, sort_key=lambda rid: rid, site_name_fn=lambda code: "")
        self.assertEqual([t["psx"] for t in out], [PSX_T2, PSX_T3])
        job["rows"] = "not a list"
        self.assertEqual(tl.transects_of(job, sort_key=lambda rid: rid, site_name_fn=lambda code: ""), [])


class Durations(unittest.TestCase):
    def test_sums_by_value_inside_the_window(self):
        got = tl.durations(INTERVALS["MRS_T1_2023ann"], since=datetime.fromisoformat(iso(20, 9)), until=None)
        self.assertEqual(got["pulled_at"], iso(20, 11))
        self.assertEqual(got["editing_s"], 165600)
        self.assertEqual(got["upload_wait_s"], 10800)
        self.assertEqual(got["review_s"], 0)
        self.assertEqual(got["uploaded_at"], iso(22, 12))
        self.assertNotIn("awaiting", str(got), "the awaiting spell before the project is not counted")

    def test_intervals_after_the_next_project_are_left_out(self):
        until = datetime.fromisoformat(iso(22, 10))
        got = tl.durations(INTERVALS["MRS_T1_2023ann"], since=datetime.fromisoformat(iso(20, 9)), until=until)
        self.assertEqual(got["upload_wait_s"], 10800, "the upload spell starts before the next project")
        self.assertIsNone(got["uploaded_at"], "the done spell starts after the next project was created")

    def test_open_interval_rounded_and_clock_anomaly_counted_clamped(self):
        spells = [interval("review", iso(26, 15), None, 75600.6, open_=True),
                  interval("editing", iso(26, 14), iso(26, 13), 0, anomaly=True)]
        got = tl.durations(spells, since=datetime.fromisoformat(iso(25, 10)), until=None)
        self.assertEqual(got["review_s"], 75601)
        self.assertEqual(got["editing_s"], 0)
        self.assertIsInstance(got["review_s"], int)

    def test_no_intervals_and_unreadable_starts(self):
        empty = tl.durations([], since=None, until=None)
        self.assertEqual(empty, {"pulled_at": None, "editing_s": 0, "review_s": 0, "upload_wait_s": 0,
                                 "uploaded_at": None})
        junk = [{"value": "editing", "start": "not a time", "seconds": 5}, "x", None,
                {"value": "editing", "start": iso(25, 13), "seconds": "7"}]
        got = tl.durations(junk, since=None, until=None)
        self.assertEqual(got["editing_s"], 0, "a spell with no readable start or seconds counts nothing")

    def test_naive_start_is_read_as_ast(self):
        spells = [interval("editing", "2026-09-25T12:00:00", iso(25, 13), 3600)]
        got = tl.durations(spells, since=datetime.fromisoformat(iso(25, 11)), until=None)
        self.assertEqual(got["editing_s"], 3600)


class ProjectRows(unittest.TestCase):
    def test_every_figure_of_a_row(self):
        asked = []
        notes = [{"job_id": JOB_NEW, "psx": PSX_T2, "readable_id": "MRS_T2_2023ann", "group_id": "G1",
                  "kind": "check_note", "text": "first", "at": iso(26, 15)},
                 {"job_id": JOB_NEW, "psx": PSX_T2, "readable_id": "MRS_T2_2024ann", "group_id": "G1",
                  "kind": "check_note", "text": "first", "at": iso(26, 15)},
                 {"job_id": JOB_NEW, "psx": PSX_T2, "readable_id": "MRS_T2_2023ann", "group_id": "G2",
                  "kind": "admin_review", "text": "last word", "at": iso(26, 18)},
                 {"job_id": JOB_OLD, "psx": PSX_T2, "readable_id": "MRS_T2_2023ann", "group_id": "G3",
                  "kind": "admin_review", "text": "another project", "at": iso(27, 9)}]
        rows = build_rows([new_job(), old_job()], asked, notes=notes, upload=UPLOAD_SET)
        self.assertEqual([(r["job_id"], r["psx"]) for r in rows],
                         [(JOB_NEW, PSX_T2), (JOB_NEW, PSX_T3), (JOB_OLD, PSX_T1)])
        self.assertEqual(sorted(asked), sorted(["MRS_T2_2023ann", "LBH_T3_2024ann", "MRS_T1_2023ann"]),
                         "one first readable id per transect, read in one call")
        t2, t3, t1 = rows
        self.assertEqual((t2["state"], t2["state_word"], t2["round"], t2["editor"]),
                         ("review", "Marked for admin review", 1, "CC"))
        self.assertEqual(t2["review_s"], 75600)
        self.assertEqual(t2["pulled_at"], iso(25, 12))
        self.assertEqual(t2["total_s"], int((NOW - datetime.fromisoformat(iso(25, 12))).total_seconds()))
        self.assertTrue(t2["checks_open"])
        self.assertEqual(t2["last_note"], "last word", "the newest note of this project and transect")
        self.assertEqual(t2["confirmed_at"], iso(26, 15))
        self.assertEqual(t3["editor"], "LO", "the certifier, not the project's creator")
        self.assertEqual(t3["site_name"], "Lang Bank Red Hind FSA")
        self.assertEqual(t3["upload"], {"upload_id": UPLOAD_ID, "status": "running", "item_state": "copying",
                                        "bytes_done": 100, "bytes_total": 400})
        self.assertIsNone(t2["upload"])
        self.assertEqual((t1["opens"], t1["metashape_s"], t1["unknown_opens"]), (2, 120, 1))
        self.assertEqual(t1["uploaded_at"], iso(22, 12))
        self.assertEqual(t1["total_s"], int((datetime.fromisoformat(iso(22, 12))
                                             - datetime.fromisoformat(iso(20, 11))).total_seconds()))
        self.assertEqual(t1["removed_at"], iso(23, 9))
        self.assertEqual(t1["readable_ids"], sorted(T1_IDS))
        self.assertEqual(t1["created_at"], iso(20, 9))
        self.assertEqual((t1["phase"], t2["phase"]), ("done", "editing"), "a cancelled project's rows can say so")

    def test_a_transect_edited_again_later_is_counted_once(self):
        later = dict(new_job(), job_id="ME-20260922-110000-LO", created_at=iso(22, 11),
                     rows=rows_for(T1_IDS, PSX_T1, "MRS", "T1"),
                     projects={PSX_T1: project("editing")})
        rows = build_rows([old_job(), later])
        by_job = {r["job_id"]: r for r in rows}
        self.assertIsNone(by_job[JOB_OLD]["uploaded_at"], "the done spell belongs to the later project")
        self.assertEqual(by_job[JOB_OLD]["upload_wait_s"], 10800)
        self.assertEqual(by_job[JOB_OLD]["total_s"], 48 * 3600, "the total stops when the next project took the transect")
        self.assertEqual(by_job["ME-20260922-110000-LO"]["uploaded_at"], iso(22, 12))
        self.assertEqual(by_job["ME-20260922-110000-LO"]["editing_s"], 0)

    def test_a_transect_with_no_intervals_and_no_project_record(self):
        job = new_job()
        job["projects"] = {}
        job["rows"] = rows_for(["SPR_T4_2024ann"], "SPR_T4_2024_2024.psx", "SPR", "T4")
        row = build_rows([job])[0]
        self.assertEqual(row["state"], jobstore.PROJECT_TODO)
        self.assertIsNone(row["pulled_at"])
        self.assertIsNone(row["total_s"])
        self.assertEqual((row["editing_s"], row["metashape_s"], row["opens"]), (0, 0, 0))
        self.assertEqual(row["editor"], "CC")
        self.assertEqual(row["last_note"], "")

    def test_an_upload_set_that_cannot_be_read_says_so(self):
        reason = f"the upload set {UPLOAD_ID} at /x/{UPLOAD_ID}.json is corrupt: items is not a list"
        rows = tl.project_rows([new_job()], fake_intervals([]), fake_metashape, lambda c: "", NOW,
                               upload_fn=lambda uid: (None, reason), live_upload_statuses=jobstore.UPLOAD_LIVE_STATUSES,
                               state_words={}, sort_key=lambda rid: rid)
        self.assertEqual(rows[1]["upload"], {"upload_id": UPLOAD_ID, "error": reason})
        self.assertEqual(rows[1]["state_word"], "uploading", "a state with no word shows its code")
        blank = build_rows([new_job()], upload=None, upload_error="")
        self.assertIsNone(blank[1]["upload"], "no reader, no upload line")
        odd = tl.project_rows([new_job()], fake_intervals([]), fake_metashape, lambda c: "", NOW,
                              upload_fn=lambda uid: ("not a record", ""),
                              live_upload_statuses=jobstore.UPLOAD_LIVE_STATUSES, sort_key=lambda rid: rid)
        self.assertEqual(odd[1]["upload"], {"upload_id": UPLOAD_ID, "error": tl.UPLOAD_UNREADABLE})

    def test_a_set_that_has_ended_shows_no_progress(self):
        for status in ("interrupted", "failed", "stopped", "done"):
            with self.subTest(status=status):
                ended = dict(copy.deepcopy(UPLOAD_SET), status=status)
                row = build_rows([new_job()], upload=ended)[1]
                self.assertIsNone(row["upload"], "a set a restart stranded never reads as uploading")
        for status in ("queued", "running"):
            with self.subTest(status=status):
                self.assertEqual(build_rows([new_job()], upload=dict(UPLOAD_SET, status=status))[1]["upload"]["status"],
                                 status)

    def test_a_cancelled_project_s_total_stops_at_the_cancel(self):
        job = stopped_job("cancelled", 15)
        rows = tl.project_rows([job], open_editing(T1_IDS), fake_metashape, lambda c: "", NOW,
                               sort_key=lambda rid: rid)
        self.assertEqual(rows[0]["total_s"], 28 * 3600, "from the pull on the 20th at 11:00 to the cancel on the 21st")
        later = tl.project_rows([job], open_editing(T1_IDS), fake_metashape, lambda c: "", NOW + timedelta(days=3),
                                sort_key=lambda rid: rid)
        self.assertEqual(later[0]["total_s"], 28 * 3600, "a later reading does not grow the total")

    def test_a_failed_pull_s_total_stops_at_the_failure(self):
        rows = tl.project_rows([stopped_job("failed_pulling", 13)], open_editing(T1_IDS), fake_metashape,
                               lambda c: "", NOW, sort_key=lambda rid: rid)
        self.assertEqual(rows[0]["total_s"], 26 * 3600)

    def test_stopped_at(self):
        self.assertEqual(tl.stopped_at(stopped_job("cancelled", 15)), datetime.fromisoformat(iso(21, 15)))
        self.assertEqual(tl.stopped_at(old_job()), datetime.fromisoformat(iso(23, 10)), "a complete job stops too")
        self.assertIsNone(tl.stopped_at(new_job()), "a job still editing runs on")
        twice = stopped_job("failed_pulling", 13)
        twice["phase_history"].insert(2, {"phase": "failed_pulling", "at": iso(20, 10), "by": "LO", "reason": ""})
        self.assertEqual(tl.stopped_at(twice), datetime.fromisoformat(iso(21, 13)), "the newest failure")
        broken = stopped_job("cancelled", 15)
        broken["phase_history"] = "junk"
        self.assertIsNone(tl.stopped_at(broken))
        broken["phase_history"] = [{"phase": "cancelled", "at": "not a time"}, None]
        self.assertIsNone(tl.stopped_at(broken))

    def test_the_earliest_of_the_stop_the_next_project_and_now_ends_the_total(self):
        job = stopped_job("cancelled", 15)
        later = dict(new_job(), job_id="ME-20260921-100000-LO", created_at=iso(21, 10),
                     rows=rows_for(T1_IDS, PSX_T1, "MRS", "T1"), projects={PSX_T1: project("editing")})
        rows = tl.project_rows([later, job], open_editing(T1_IDS), fake_metashape, lambda c: "", NOW,
                               sort_key=lambda rid: rid)
        by_job = {r["job_id"]: r for r in rows}
        self.assertEqual(by_job[JOB_OLD]["total_s"], 23 * 3600, "the next project came before the cancel")

    def test_progress_of_another_folder_is_not_this_transect(self):
        other = copy.deepcopy(UPLOAD_SET)
        other["progress"]["folder"] = "MRS_T9_3D"
        row = build_rows([new_job()], upload=other)[1]
        self.assertEqual((row["upload"]["bytes_done"], row["upload"]["bytes_total"]), (None, 400),
                         "the item's real bytes, and no copied figure from another folder")

    def test_hostile_project_fields_do_not_break_the_row(self):
        job = new_job()
        job["projects"][PSX_T2].update({"round": "seven", "opens": "nope", "certification": "x",
                                        "checks_run": None})
        job["created_at"] = "garbage"
        row = build_rows([job])[0]
        self.assertEqual(row["round"], 0)
        self.assertEqual(row["opens"], 0)
        self.assertEqual(row["editor"], "CC")
        self.assertFalse(row["checks_open"])
        self.assertEqual(row["review_s"], 75600, "an unreadable created_at opens the window at the start")

    def test_a_rename_is_read_under_the_job_s_own_id(self):
        # Program A's reader follows a rename; timelines asks for the id the job recorded.
        asked = []
        job = new_job()
        job["rows"] = rows_for(["MRS_T2_2023ann"], PSX_T2, "MRS", "T2")
        build_rows([job], asked)
        self.assertEqual(asked, ["MRS_T2_2023ann"])


class Summaries(unittest.TestCase):
    def test_project_summaries(self):
        projects = tl.project_summaries([new_job(), old_job()], STATES)
        new, old = projects
        self.assertEqual((new["job_id"], new["created_by"], new["phase"], new["transects"], new["uploaded"]),
                         (JOB_NEW, "CC", "editing", 2, 0))
        self.assertEqual(new["by_state"], {"review": 1, "uploading": 1})
        self.assertIsNone(new["finished_at"])
        self.assertEqual(new["pull"], {"started_at": iso(25, 10, 1), "finished_at": iso(25, 12),
                                       "bytes_done": None, "bytes_total": None},
                         "the pull's bytes show only while it runs")
        self.assertEqual((old["finished_at"], old["completed_at"], old["uploaded"]), (iso(22, 10), iso(23, 10), 1))

    def test_pull_progress_while_pulling(self):
        job = new_job()
        job["phase"] = "pulling"
        job["phase_history"] = job["phase_history"][:1]
        pull = tl.project_summaries([job], STATES)[0]["pull"]
        self.assertEqual(pull, {"started_at": iso(25, 10, 1), "finished_at": None, "bytes_done": 10,
                                "bytes_total": 20})

    def test_summary_counts_median_and_completed(self):
        rows = build_rows([new_job(), old_job()])
        projects = tl.project_summaries([new_job(), old_job()], STATES)
        got = tl.summary(rows, projects, STATES, NOW, [{"job_id": "X", "error": "bad"}])
        self.assertEqual((got["projects"], got["transects"]), (2, 3))
        self.assertEqual(set(got["by_state"]), set(STATES))
        self.assertEqual((got["by_state"]["review"], got["by_state"]["uploading"], got["by_state"]["done"]), (1, 1, 1))
        self.assertEqual(got["median_metashape_s"], 120)
        self.assertEqual(got["completed_last_30_days"], 1)
        self.assertEqual(got["skipped"], [{"job_id": "X", "error": "bad"}])

    def test_median_of_an_even_count_and_none_without_done(self):
        rows = [{"state": "done", "metashape_s": 10}, {"state": "done", "metashape_s": 30},
                {"state": "editing", "metashape_s": 999}]
        self.assertEqual(tl.summary(rows, [], STATES, NOW, [])["median_metashape_s"], 20)
        self.assertIsNone(tl.summary(rows[2:], [], STATES, NOW, [])["median_metashape_s"])

    def test_completed_more_than_30_days_ago_is_not_counted(self):
        projects = [{"completed_at": (NOW - timedelta(days=31)).isoformat()}, {"completed_at": "junk"},
                    {"completed_at": None}, {"completed_at": (NOW - timedelta(days=29)).isoformat()}]
        self.assertEqual(tl.summary([], projects, STATES, NOW, [])["completed_last_30_days"], 1)


class Filters(unittest.TestCase):
    def setUp(self):
        self.rows = build_rows([new_job(), old_job()])

    def test_each_filter_and_all_together(self):
        self.assertEqual(len(tl.filter_rows(self.rows)), 3)
        self.assertEqual([r["psx"] for r in tl.filter_rows(self.rows, job=JOB_OLD)], [PSX_T1])
        self.assertEqual([r["psx"] for r in tl.filter_rows(self.rows, state="review")], [PSX_T2])
        self.assertEqual([r["psx"] for r in tl.filter_rows(self.rows, editor="LO")], [PSX_T3, PSX_T1])
        self.assertEqual([r["psx"] for r in tl.filter_rows(self.rows, since="2026-09-25")], [PSX_T2, PSX_T3])
        self.assertEqual(tl.filter_rows(self.rows, job=JOB_NEW, editor="LO", state="uploading")[0]["psx"], PSX_T3)
        self.assertEqual(tl.filter_rows(self.rows, state="done", since="2026-09-25"), [])


class Detail(unittest.TestCase):
    def test_intervals_in_the_window_whole_seconds_and_notes_once(self):
        job = new_job()
        intervals = {rid: copy.deepcopy(INTERVALS.get(rid, [])) for rid in T2_IDS}
        intervals["MRS_T2_2024ann"] = [interval("awaiting", iso(1, 8), iso(25, 12), 10.6),
                                       interval("editing", iso(25, 12), None, 1.5, open_=True)]
        notes = [{"job_id": JOB_NEW, "psx": PSX_T2, "readable_id": rid, "group_id": "G2", "kind": "admin_review",
                  "text": "later", "at": iso(26, 18)} for rid in T2_IDS]
        notes += [{"job_id": JOB_NEW, "psx": PSX_T2, "readable_id": "MRS_T2_2023ann", "group_id": "G1",
                   "kind": "check_note", "text": "earlier", "at": iso(26, 15)},
                  {"job_id": JOB_OLD, "psx": PSX_T2, "readable_id": "MRS_T2_2023ann", "group_id": "G9",
                   "kind": "check_note", "text": "other project", "at": iso(20, 15)}]
        got = tl.detail(job, PSX_T2, intervals, notes, since=datetime.fromisoformat(job["created_at"]), until=None)
        self.assertEqual(got["job_id"], JOB_NEW)
        self.assertEqual(list(got["intervals"]), sorted(T2_IDS))
        self.assertEqual([i["value"] for i in got["intervals"]["MRS_T2_2024ann"]], ["editing"])
        self.assertEqual(got["intervals"]["MRS_T2_2024ann"][0]["seconds"], 2)
        self.assertEqual([n["text"] for n in got["notes"]], ["earlier", "later"])

    def test_unknown_psx_raises(self):
        with self.assertRaises(KeyError):
            tl.detail(new_job(), "NOPE_T1_2023_2023.psx", {}, [], since=None, until=None)


class EditTime(unittest.TestCase):
    def test_metashape_seconds_uses_the_job_store_counter(self):
        opens = [{"event": "open", "at": iso(26, 9)}, {"event": "close", "at": iso(26, 10)},
                 {"event": "open", "at": iso(26, 11)}]
        self.assertEqual(tl._metashape_seconds(jobstore, opens, NOW), {"seconds": 3600, "unknown_opens": 1})
        self.assertEqual(tl._metashape_seconds(jobstore, "junk", NOW), {"seconds": 0, "unknown_opens": 0})


# --- routes ---------------------------------------------------------------------------------


def write_events(path: Path, lines) -> None:
    """Append events.csv lines (naive AST stamps), after whatever the registry already wrote."""
    exists = path.exists() and path.stat().st_size > 0
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if not exists:
            writer.writerow(["timestamp", "actor", "readable_id", "field", "old", "new"])
        writer.writerows(lines)


class Routes(unittest.TestCase):
    """The two routes on a bare app over a temporary registry root and job store."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="vt-timelines-"))
        os.environ["VICARIUS_3D_REGISTRY_ROOT"] = str(self.root)
        import registry
        self.registry = importlib.reload(registry)
        self.store = jobstore.JobStore(str(self.root))
        self.locked = False
        self._saved = {k: getattr(views.deps, k) for k in vars(views.deps)}
        views.deps.is_locked = lambda: self.locked
        views.deps.job_store = lambda: self.store
        views.deps.now_iso = lambda: NOW.isoformat(timespec="seconds")
        views.deps.site_name = lambda code: {"MRS": ""}.get(code, "")
        self.app = self.make_app()
        self.client = self.app.test_client()

    @staticmethod
    def make_app(mount_manual_edit=False):
        """A bare app with the panel's blueprint, and a stand-in blueprint under the manual edit module's name on request."""
        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(views.voyager_tools_bp)
        if mount_manual_edit:
            app.register_blueprint(Blueprint(views.MANUAL_EDIT_BLUEPRINT, __name__))
            app.config[manual_views.DEPS_KEY] = transfers.Deps(
                *([None] * 10), upload_runner=transfers.UploadRunner())
        return app

    def tearDown(self):
        for key, value in self._saved.items():
            setattr(views.deps, key, value)
        os.environ.pop("VICARIUS_3D_REGISTRY_ROOT", None)
        shutil.rmtree(self.root, ignore_errors=True)
        import registry
        importlib.reload(registry)

    def unlock(self):
        with self.client.session_transaction() as sess:
            sess["admin_unlocked"] = True

    def seed(self):
        """Two job records, the registry rows, status events and one note."""
        for job in (old_job(), new_job()):
            record = dict(job, schema=jobstore.JOB_SCHEMA)
            record["projects"] = {psx: dict(p, **{k: v for k, v in jobstore._new_project(
                [r for r in job["rows"] if r["psx_basename"] == psx]).items() if k not in p})
                for psx, p in job["projects"].items()}
            self.store.save_job(record)
        for rid in T1_IDS + T2_IDS + T3_IDS:
            site, transect = rid.split("_")[:2]
            self.registry.upsert(rid, {"site": site, "transect": transect}, "seed")
        write_events(Path(self.registry.EVENTS_CSV), [
            ["2026-09-20 11:00:00", "manual_edit:LO", "MRS_T1_2023ann", "manual_edit_status", "awaiting", "editing"],
            ["2026-09-22 09:00:00", "manual_edit:LO", "MRS_T1_2023ann", "manual_edit_status", "editing", "upload"],
            ["2026-09-22 12:00:00", "manual_edit:LO", "MRS_T1_2023ann", "manual_edit_status", "upload", "done"],
            ["2026-09-25 12:00:00", "manual_edit:CC", "MRS_T2_2023ann", "manual_edit_status", "awaiting", "editing"],
            ["2026-09-26 15:00:00", "manual_edit:CC", "MRS_T2_2023ann", "manual_edit_status", "editing", "review"],
            ["2026-09-25 12:00:00", "manual_edit:CC", "MRS_T2_2024ann", "manual_edit_status", "awaiting", "editing"],
        ])
        self.registry.append_notes(T2_IDS, JOB_NEW, PSX_T2, 1, "admin_review", "", "Send it back.", "LO",
                                   actor="manual_edit:LO", source_id=f"{JOB_NEW}:{PSX_T2}:review:r1",
                                   at=iso(26, 18), decision="return")

    def test_gates(self):
        for path in ("/voyager-tools/api/timelines", f"/voyager-tools/api/timelines/detail?job={JOB_NEW}&psx={PSX_T2}"):
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 401)
                self.assertTrue(r.get_json()["need_unlock"])
                self.unlock()
                self.locked = True
                self.assertEqual(self.client.get(path).status_code, 423)
                self.locked = False
                with self.client.session_transaction() as sess:
                    sess.clear()

    def test_every_figure_through_the_real_readers(self):
        self.seed()
        self.unlock()
        r = self.client.get("/voyager-tools/api/timelines")
        self.assertEqual(r.status_code, 200, r.get_json())
        data = r.get_json()
        self.assertEqual(data["generated_at"], NOW.isoformat(timespec="seconds"))
        self.assertEqual(data["summary"]["projects"], 2)
        self.assertEqual(data["summary"]["transects"], 3)
        self.assertEqual(data["state_words"], jobstore.STATE_WORDS)
        self.assertEqual(data["states"], list(STATES))
        rows = {r["psx"]: r for r in data["rows"]}
        t1, t2 = rows[PSX_T1], rows[PSX_T2]
        self.assertEqual(t1["readable_ids"][0], "MRS_T1_2023ann", "registry order puts the earliest timepoint first")
        self.assertEqual(t1["pulled_at"], iso(20, 11))
        self.assertEqual((t1["editing_s"], t1["upload_wait_s"]), (165600, 10800))
        self.assertEqual(t1["uploaded_at"], iso(22, 12))
        self.assertEqual(t2["review_s"], int((NOW - datetime.fromisoformat(iso(26, 15))).total_seconds()))
        self.assertEqual(t2["last_note"], "Send it back.")
        detail = self.client.get("/voyager-tools/api/timelines/detail", query_string={"job": JOB_NEW, "psx": PSX_T2})
        self.assertEqual(detail.status_code, 200, detail.get_json())
        body = detail.get_json()
        self.assertEqual(sorted(body["intervals"]), sorted(T2_IDS))
        self.assertEqual([i["value"] for i in body["intervals"]["MRS_T2_2023ann"]], ["editing", "review"])
        self.assertTrue(body["intervals"]["MRS_T2_2023ann"][-1]["open"])
        self.assertEqual(len(body["notes"]), 1, "a note written for both timepoints shows once")
        self.assertEqual(body["notes"][0]["decision"], "return")

    def test_filters_on_the_route(self):
        self.seed()
        self.unlock()
        got = self.client.get("/voyager-tools/api/timelines", query_string={"state": "done"}).get_json()
        self.assertEqual([r["psx"] for r in got["rows"]], [PSX_T1])
        self.assertEqual(got["summary"]["transects"], 3, "the summary covers every transect")
        got = self.client.get("/voyager-tools/api/timelines",
                              query_string={"editor": "lo ", "since": "2026-09-25"}).get_json()
        self.assertEqual([r["psx"] for r in got["rows"]], [PSX_T3])

    def test_refusals(self):
        self.unlock()
        bad = [{"since": "2026-13-01"}, {"since": "yesterday"}, {"state": "bogus"}, {"job": "../x"},
               {"editor": "L0"}, {"editor": "ABCDE"}, {"since": "2026-09-27T00:00"}]
        for query in bad:
            with self.subTest(query=query):
                r = self.client.get("/voyager-tools/api/timelines", query_string=query)
                self.assertEqual(r.status_code, 400)
                error = r.get_json()["error"]
                self.assertTrue(error.endswith("."), error)
                self.assertLess(len(error), 200)
        for query in ({}, {"job": JOB_NEW}, {"job": JOB_NEW, "psx": "../a.psx"}, {"job": "x", "psx": PSX_T2},
                      {"job": JOB_NEW, "psx": "a" * 300 + ".psx"}):
            with self.subTest(query=query):
                r = self.client.get("/voyager-tools/api/timelines/detail", query_string=query)
                self.assertEqual(r.status_code, 400)

    def test_unknown_job_or_psx_is_a_404(self):
        self.seed()
        self.unlock()
        r = self.client.get("/voyager-tools/api/timelines/detail",
                            query_string={"job": "ME-20260101-000000-ZZ", "psx": PSX_T2})
        self.assertEqual(r.status_code, 404)
        r = self.client.get("/voyager-tools/api/timelines/detail", query_string={"job": JOB_NEW, "psx": PSX_T1})
        self.assertEqual(r.status_code, 404)
        self.assertIn(PSX_T1, r.get_json()["error"])

    def test_a_corrupt_job_file_is_named_not_fatal(self):
        self.seed()
        (self.store.jobs_dir / "ME-20260926-080000-XY.json").write_text("{not json", encoding="utf-8")
        self.unlock()
        data = self.client.get("/voyager-tools/api/timelines").get_json()
        self.assertEqual(data["summary"]["projects"], 2)
        self.assertEqual([s["job_id"] for s in data["summary"]["skipped"]], ["ME-20260926-080000-XY"])

    def test_no_job_files_at_all(self):
        self.unlock()
        data = self.client.get("/voyager-tools/api/timelines").get_json()
        self.assertEqual((data["summary"]["projects"], data["rows"], data["projects"]), (0, [], []))
        self.assertIsNone(data["summary"]["median_metashape_s"])

    def test_manual_edit_module_missing_is_a_503_line(self):
        def missing():
            raise ModuleNotFoundError("No module named 'manualedit'")
        views.deps.job_store = missing
        self.unlock()
        for path in ("/voyager-tools/api/timelines", f"/voyager-tools/api/timelines/detail?job={JOB_NEW}&psx={PSX_T2}"):
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 503)
                self.assertEqual(r.get_json()["error"], "The manual edit module is not mounted here.")
                self.assertTrue(r.get_json()["not_mounted"])

    def uploading_set(self):
        """Put LBH T3 in a real upload set left running on disk, as a desktop restart strands it; returns its id."""
        path = self.store.job_path(JOB_NEW)
        record = self.store.load_job(JOB_NEW)
        record["projects"][PSX_T3].update({"state": "upload", "upload_id": None})
        path.write_text(json.dumps(record), encoding="utf-8")
        upload = self.store.create_upload_set([{"job_id": JOB_NEW, "psx": PSX_T3}], "LO", "admin", True,
                                              transfers.upload_settings(self.store))
        self.store.set_upload_status(upload["upload_id"], "running")
        return upload["upload_id"]

    def test_the_recovery_runs_before_the_jobs_are_read(self):
        self.seed()
        self.unlock()
        seen = []

        def recover(store):
            seen.append(store)
            record = store.load_job(JOB_NEW)
            record["projects"][PSX_T3].update({"state": "upload", "upload_id": None})
            store.job_path(JOB_NEW).write_text(json.dumps(record), encoding="utf-8")
        views.deps.recover_uploads = recover
        rows = {r["psx"]: r for r in self.client.get("/voyager-tools/api/timelines").get_json()["rows"]}
        self.assertEqual(seen, [self.store])
        self.assertEqual((rows[PSX_T3]["state"], rows[PSX_T3]["upload"]), ("upload", None))

    def test_a_stranded_running_set_reads_as_interrupted_where_manual_edit_is_mounted(self):
        self.seed()
        upload_id = self.uploading_set()
        client = self.make_app(mount_manual_edit=True).test_client()
        with client.session_transaction() as sess:
            sess["admin_unlocked"] = True
        rows = {r["psx"]: r for r in client.get("/voyager-tools/api/timelines").get_json()["rows"]}
        self.assertEqual(self.store.load_upload_set(upload_id)["status"], "interrupted")
        self.assertEqual((rows[PSX_T3]["state"], rows[PSX_T3]["upload"]), ("upload", None),
                         "never shown as uploading")
        summary = client.get("/voyager-tools/api/timelines").get_json()["summary"]
        self.assertEqual(summary["by_state"]["uploading"], 0)

    def test_a_host_without_manual_edit_leaves_the_sets_alone(self):
        self.seed()
        upload_id = self.uploading_set()
        self.unlock()
        row = {r["psx"]: r for r in self.client.get("/voyager-tools/api/timelines").get_json()["rows"]}[PSX_T3]
        self.assertEqual(self.store.load_upload_set(upload_id)["status"], "running",
                         "another process may be running it")
        self.assertEqual((row["upload"]["upload_id"], row["upload"]["status"]), (upload_id, "running"))

    def test_a_missing_upload_set_is_named_on_the_row_and_logged(self):
        self.seed()
        self.unlock()
        with self.assertLogs(self.app.logger, "WARNING") as logged:
            rows = {r["psx"]: r for r in self.client.get("/voyager-tools/api/timelines").get_json()["rows"]}
        upload = rows[PSX_T3]["upload"]
        self.assertEqual(upload["upload_id"], UPLOAD_ID)
        self.assertIn(UPLOAD_ID, upload["error"])
        self.assertIn("no upload set", upload["error"])
        self.assertTrue(any(UPLOAD_ID in line for line in logged.output))

    def test_an_unreadable_events_file_is_a_500_line(self):
        self.seed()
        self.unlock()
        events = Path(self.registry.EVENTS_CSV)
        events.unlink()
        events.mkdir()
        r = self.client.get("/voyager-tools/api/timelines")
        self.assertEqual(r.status_code, 500)
        self.assertIn("events", r.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
