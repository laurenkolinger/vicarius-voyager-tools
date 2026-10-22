#!/usr/bin/env python3
"""
Module: tests/test_v1runs.py
Purpose: Tests of voyagertools.v1runs, the pure model of the Voyager 1 batch
         pane: the batch line, each flag (over the limit, build process gone,
         flushed), each action rule (Flush, Move), the pending positions the
         Move controls use, missing keys, a status with no batch, a batch of
         200 turns, a failing site name resolver, and hostile strings (script
         tags, quotes, control characters, huge text, wrong types) in ids,
         reasons and hours.
Inputs:  hand-built driver status dicts; no network and no files.
Outputs: unittest results.

Run from the clone root:
    /usr/bin/python3 -m unittest tests.test_v1runs -v
"""
from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voyagertools import v1runs  # noqa: E402

SITES = {"BIT": "Buck Island STT", "HBE": "Hind Bank East FSA"}


def site_name(code: str) -> str:
    """A fixed site table so no test reads the platform's site_codes.csv."""
    return SITES.get(code, "")


def ids(code: str) -> list[str]:
    """The four video ids of a transect."""
    return [f"{code}_{s}" for s in ("2023ann", "2024_pbl", "2024ann", "2025_pbl")]


def turn(number: int, code: str, status: str, **extra) -> dict:
    """One driver turn."""
    out = {"id": number, "status": status, "type": "tcrmp3d", "phase": 1, "select": {"ids": ids(code)}}
    out.update(extra)
    return out


def status_of(turns: list, **batch) -> dict:
    """A driver status holding one batch."""
    base = {"batch_id": "batch_20261016-225420", "status": "running", "run_id": "RUN",
            "shelf_root": "/volume6/Archive8_12TB/driver_deposits", "turns": turns}
    base.update(batch)
    return {"batch": base, "engine_alive": True}


def model_of(turns: list, top: dict | None = None, **batch) -> dict:
    """The model of a status."""
    status = status_of(turns, **batch)
    status.update(top or {})
    return v1runs.build_model(status, site_name)


class BatchLine(unittest.TestCase):
    def test_batch_fields_and_site_names(self):
        model = model_of([turn(6, "BIT_T3", "running", time_limit_hours=11)])
        batch = model["batch"]
        self.assertEqual(batch["batch_id"], "batch_20261016-225420")
        self.assertEqual(batch["status"], "running")
        self.assertEqual(batch["shelf_root"], "/volume6/Archive8_12TB/driver_deposits")
        self.assertEqual(batch["time_limit_hours"], 11.0)
        row = model["turns"][0]
        self.assertEqual((row["id"], row["transect"], row["site_code"], row["site_name"]),
                         (6, "BIT_T3", "BIT", "Buck Island STT"))
        self.assertEqual(row["video_ids"], ids("BIT_T3"))

    def test_the_limit_is_read_from_the_status_or_the_batch_when_no_turn_carries_it(self):
        self.assertEqual(model_of([turn(1, "BIT_T1", "pending")], top={"time_limit_hours": 9})["batch"]["time_limit_hours"], 9.0)
        self.assertEqual(model_of([turn(1, "BIT_T1", "pending")], time_limit_hours=8)["batch"]["time_limit_hours"], 8.0)
        self.assertIsNone(model_of([turn(1, "BIT_T1", "pending")])["batch"]["time_limit_hours"])

    def test_no_batch(self):
        for status in (None, {}, {"batch": None}, {"batch": {}}, [], "x", 5):
            with self.subTest(status=status):
                model = v1runs.build_model(status, site_name)
                self.assertIsNone(model["batch"])
                self.assertEqual(model["turns"], [])
                self.assertEqual(model["line"], v1runs.NO_BATCH_LINE)

    def test_a_batch_with_no_turns(self):
        for turns in ([], None, "x"):
            with self.subTest(turns=turns):
                model = v1runs.build_model({"batch": {"batch_id": "b", "turns": turns}}, site_name)
                self.assertEqual(model["turns"], [])
                self.assertEqual(model["line"], "The batch has no turns.")

    def test_missing_keys_still_give_a_model(self):
        model = v1runs.build_model({"batch": {"turns": [{}]}}, site_name)
        row = model["turns"][0]
        self.assertIsNone(row["id"])
        self.assertEqual((row["transect"], row["video_ids"], row["status"]), ("", [], "unknown"))
        self.assertEqual(row["actions"], {"flush": False, "move": False})
        self.assertEqual(model["batch"]["status"], "unknown")


class Flags(unittest.TestCase):
    def test_over_the_limit_needs_hours_strictly_above_the_limit(self):
        for hours, expected in ((16.6, True), (11.04, False), (11.0, False), (10.9, False), (11.2, True)):
            with self.subTest(hours=hours):
                row = model_of([turn(6, "BIT_T3", "running", time_limit_hours=11,
                                     video_hours={"BIT_T3_2023ann": hours})])["turns"][0]
                self.assertEqual(row["over_limit"], expected)

    def test_current_video_is_the_one_built_longest(self):
        row = model_of([turn(6, "BIT_T3", "running", time_limit_hours=11,
                             video_hours={"BIT_T3_2023ann": 3.0, "BIT_T3_2024_pbl": 12.34})])["turns"][0]
        self.assertEqual((row["current_video"], row["current_hours"], row["limit_hours"]), ("BIT_T3_2024_pbl", 12.3, 11.0))

    def test_no_hours_means_no_current_video_and_no_flag(self):
        row = model_of([turn(6, "BIT_T3", "running", time_limit_hours=11)])["turns"][0]
        self.assertEqual((row["current_video"], row["current_hours"], row["over_limit"]), (None, None, False))

    def test_no_limit_means_no_over_flag(self):
        row = model_of([turn(6, "BIT_T3", "running", video_hours={"BIT_T3_2023ann": 99})])["turns"][0]
        self.assertEqual((row["current_hours"], row["limit_hours"], row["over_limit"]), (99.0, None, False))

    def test_hours_that_are_not_hours_are_dropped(self):
        bad = {"a": "5", "b": True, "c": -1, "d": float("nan"), "e": float("inf"), "f": None, "g": [1], "BIT_T3_2024ann": 4}
        row = model_of([turn(6, "BIT_T3", "running", time_limit_hours=11, video_hours=bad)])["turns"][0]
        self.assertEqual((row["current_video"], row["current_hours"]), ("BIT_T3_2024ann", 4.0))
        row = model_of([turn(6, "BIT_T3", "running", time_limit_hours=11, video_hours="lots")])["turns"][0]
        self.assertIsNone(row["current_video"])

    def test_a_limit_that_is_zero_negative_or_text_is_ignored(self):
        for bad in (0, -3, "11", True, None, float("nan")):
            with self.subTest(limit=bad):
                row = model_of([turn(6, "BIT_T3", "running", time_limit_hours=bad,
                                     video_hours={"BIT_T3_2023ann": 20})])["turns"][0]
                self.assertIsNone(row["limit_hours"])
                self.assertFalse(row["over_limit"])

    def test_build_process_gone(self):
        cases = [
            (turn(6, "BIT_T3", "running"), {}, False),
            (turn(6, "BIT_T3", "running", job_alive=False), {}, True),
            (turn(6, "BIT_T3", "running", job_alive=True), {"engine_alive": False}, False),
            (turn(6, "BIT_T3", "running"), {"engine_alive": False}, True),
            (turn(6, "BIT_T3", "paused", job_alive=False), {}, False),
            (turn(6, "BIT_T3", "pending", job_alive=False), {}, False),
        ]
        for t, top, expected in cases:
            with self.subTest(turn=t, top=top):
                self.assertEqual(model_of([t], top=top)["turns"][0]["build_gone"], expected)

    def test_flushed_record_and_status(self):
        record = {"at": "2026-10-21 19:02 AST", "by": "LO", "reason": "hung", "trigger": "manual", "requeued_as": 10}
        row = model_of([turn(3, "BIT_T1", "flushed", flushed=record)])["turns"][0]
        self.assertEqual(row["status"], "flushed")
        self.assertEqual(row["flushed"], {"by": "LO", "at": "2026-10-21 19:02 AST", "reason": "hung",
                                          "trigger": "manual", "requeued_as": 10})
        bare = model_of([turn(3, "BIT_T1", "flushed")])["turns"][0]
        self.assertEqual(bare["flushed"]["by"], "")
        self.assertFalse(bare["actions"]["flush"])
        # a flushed record on a turn whose status still reads another word still counts as flushed
        odd = model_of([turn(3, "BIT_T1", "paused", flushed=record)])["turns"][0]
        self.assertEqual(odd["status"], "flushed")
        self.assertEqual(odd["actions"], {"flush": False, "move": False})

    def test_requeued_as_must_be_a_whole_number(self):
        for bad in ("10", True, 1.5, None):
            row = model_of([turn(3, "BIT_T1", "flushed", flushed={"by": "LO", "requeued_as": bad})])["turns"][0]
            self.assertIsNone(row["flushed"]["requeued_as"], bad)


class Actions(unittest.TestCase):
    def test_each_status(self):
        expected = {
            "running": (True, False), "paused": (True, False), "error": (False, False), "stopped": (False, False),
            "done": (False, False), "scrapped": (False, False), "flushed": (False, False),
            "pending": (False, True), "weird": (False, False), "": (False, False),
        }
        for status, (flush, move) in expected.items():
            with self.subTest(status=status):
                row = model_of([turn(1, "BIT_T1", status)])["turns"][0]
                self.assertEqual((row["actions"]["flush"], row["actions"]["move"]), (flush, move))

    def test_status_roles_cover_every_known_status(self):
        for status in v1runs.STATUS_ROLES:
            self.assertTrue(model_of([turn(1, "BIT_T1", status)])["turns"][0]["role"].startswith("role-"))
        self.assertEqual(model_of([turn(1, "BIT_T1", "weird")])["turns"][0]["role"], v1runs.ROLE_UNKNOWN)

    def test_pending_positions_count_only_pending_turns(self):
        turns = [turn(1, "CSE_T1", "done"), turn(2, "BIT_T1", "pending"), turn(3, "BIT_T2", "flushed"),
                 turn(4, "BIT_T3", "pending"), turn(5, "HBE_T1", "pending")]
        rows = model_of(turns)["turns"]
        self.assertEqual([(r["id"], r["pending_index"], r["pending_count"]) for r in rows],
                         [(1, None, 0), (2, 0, 3), (3, None, 0), (4, 1, 3), (5, 2, 3)])


class Sizes(unittest.TestCase):
    def test_a_batch_of_200_turns(self):
        turns = [turn(i, "BIT_T1", "pending") for i in range(1, 201)]
        started = time.time()
        model = model_of(turns)
        self.assertLess(time.time() - started, 2.0)
        self.assertEqual(len(model["turns"]), 200)
        self.assertEqual(model["turns"][199]["pending_index"], 199)
        self.assertFalse(model["truncated"])
        json.dumps(model)

    def test_more_than_the_cap_is_cut_and_said(self):
        turns = [turn(i, "BIT_T1", "pending") for i in range(1, v1runs.MAX_TURNS + 20)]
        model = model_of(turns)
        self.assertEqual(len(model["turns"]), v1runs.MAX_TURNS)
        self.assertTrue(model["truncated"])

    def test_a_turn_with_a_huge_video_list_is_capped(self):
        t = turn(1, "BIT_T1", "pending")
        t["select"]["ids"] = [f"v{i}" for i in range(5000)]
        self.assertEqual(len(model_of([t])["turns"][0]["video_ids"]), v1runs.MAX_VIDEOS_PER_TURN)

    def test_non_dict_turns_are_skipped(self):
        model = model_of([turn(1, "BIT_T1", "pending"), None, "x", 5, []])
        self.assertEqual(len(model["turns"]), 1)


class Hostile(unittest.TestCase):
    EVIL = '<script>alert("x")</script>\x00\x1b[31m\r\n\t"\'& '

    def assert_clean(self, text: str):
        self.assertFalse(v1runs.CONTROL_CHARS.search(text), repr(text))

    def test_text_in_ids_reasons_and_names_has_no_control_characters(self):
        record = {"by": self.EVIL, "at": self.EVIL, "reason": self.EVIL * 50, "trigger": self.EVIL}
        t = turn(3, "BIT_T1", "flushed", flushed=record)
        t["select"]["ids"] = [self.EVIL, "ok_id", 5, None, "", "   "]
        t["transect"] = self.EVIL
        model = model_of([t], batch_id=self.EVIL, run_id=self.EVIL * 100, shelf_root=self.EVIL * 500, status=self.EVIL)
        row = model["turns"][0]
        for text in (model["batch"]["batch_id"], model["batch"]["run_id"], model["batch"]["shelf_root"],
                     model["batch"]["status"], row["transect"], row["status"], *row["video_ids"],
                     *(row["flushed"][k] for k in ("by", "at", "reason", "trigger"))):
            self.assert_clean(text)
        self.assertLessEqual(len(row["flushed"]["reason"]), v1runs.MAX_TEXT_CHARS)
        self.assertLessEqual(len(model["batch"]["shelf_root"]), 1024)
        self.assertIn("<script>", row["flushed"]["by"], "markup stays literal text for the page to escape")
        self.assertEqual(row["video_ids"][1], "ok_id")
        self.assertEqual(len(row["video_ids"]), 2)

    def test_wrong_types_everywhere(self):
        t = {"id": "6", "status": ["running"], "select": "x", "flushed": "yes", "video_hours": 5, "job_alive": "no",
             "transect": {"a": 1}}
        model = v1runs.build_model({"batch": {"turns": [t], "shelf_root": {"x": 1}, "status": 7}, "engine_alive": "no"}, site_name)
        row = model["turns"][0]
        self.assertIsNone(row["id"])
        self.assertEqual(row["status"], "unknown")
        self.assertEqual(row["video_ids"], [])
        self.assertIsNone(row["flushed"])
        self.assertFalse(row["build_gone"])
        self.assertEqual(model["batch"]["shelf_root"], "")
        json.dumps(model)

    def test_a_site_resolver_that_raises_or_returns_junk_does_not_break_the_row(self):
        def broken(code):
            raise OSError("table unreadable")
        row = v1runs.build_model(status_of([turn(1, "BIT_T1", "pending")]), broken)["turns"][0]
        self.assertEqual((row["transect"], row["site_name"]), ("BIT_T1", ""))
        row = v1runs.build_model(status_of([turn(1, "BIT_T1", "pending")]), lambda c: ["x"])["turns"][0]
        self.assertEqual(row["site_name"], "")

    def test_transect_falls_back_to_the_first_two_parts_of_the_first_video_id(self):
        t = turn(1, "BIT_T3", "pending")
        self.assertEqual(model_of([t])["turns"][0]["transect"], "BIT_T3")
        t["select"]["ids"] = ["ONLY"]
        self.assertEqual(model_of([t])["turns"][0]["transect"], "")

    def test_clean_text_unit(self):
        self.assertEqual(v1runs.clean_text(None), "")
        self.assertEqual(v1runs.clean_text({"a": 1}), "")
        self.assertEqual(v1runs.clean_text("a\x00b\n c"), "a b c")
        self.assertEqual(len(v1runs.clean_text("x" * 10000)), v1runs.MAX_TEXT_CHARS)
        self.assertEqual(v1runs.clean_text(12), "12")


if __name__ == "__main__":
    unittest.main()
