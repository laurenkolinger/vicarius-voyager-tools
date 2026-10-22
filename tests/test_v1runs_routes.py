#!/usr/bin/env python3
"""
Module: tests/test_v1runs_routes.py
Purpose: Route tests of the Voyager 1 batch pane (GET /api/v1runs, GET
         /api/v1runs/flush-preview, POST /api/v1runs/flush, /move and
         /shelf) with the driver client faked: the admin and lock gates,
         the model the pane receives, the exact call each POST makes on the
         client, missing confirm, bad initials, missing reason, bad turn and
         position values, a driver that cannot be reached (502 with the one
         sentence), the driver's 409 and 400 relayed verbatim, a driver 500
         answered as 502, and the fragment carrying the pane and its script.
Inputs:  a bare Flask app with the blueprint and a fake driver client; no network.
Outputs: unittest results.

Run from the clone root:
    /usr/bin/python3 -m unittest tests.test_v1runs_routes -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from flask import Flask

CLONE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CLONE))
VICARIUS_ROOT = os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius")
for extra in (os.path.join(VICARIUS_ROOT, "_METADATA", "3d"),
              os.path.join(VICARIUS_ROOT, "modules", "manual_edit", "github_repo")):
    if extra not in sys.path:
        sys.path.append(extra)

from voyagertools import driver_client as dc  # noqa: E402
from voyagertools import views  # noqa: E402

UNREACHABLE = "The driver on port 5093 did not answer: Connection refused."
STATUS = {
    "engine_alive": True,
    "batch": {"batch_id": "batch_1", "status": "running", "shelf_root": "/volume6/Archive8_12TB/driver_deposits",
              "turns": [
                  {"id": 6, "status": "running", "select": {"ids": ["BIT_T3_2023ann"]}, "time_limit_hours": 11,
                   "video_hours": {"BIT_T3_2023ann": 16.6}},
                  {"id": 7, "status": "pending", "select": {"ids": ["HBE_T1_2023ann"]}},
              ]},
}


class FakeClient:
    """A stand-in for DriverClient: records calls, answers from `answers` or raises from `errors`."""

    def __init__(self, answers=None, errors=None):
        self.calls = []
        self.answers = answers or {}
        self.errors = errors or {}

    def _call(self, name, *args):
        self.calls.append((name,) + args)
        if name in self.errors:
            raise self.errors[name]
        return self.answers.get(name, {"ok": True})

    def status(self):
        return self._call("status")

    def flush_preview(self, turn_id):
        return self._call("flush_preview", turn_id)

    def flush(self, turn_id, initials, reason, requeue, resume):
        return self._call("flush", turn_id, initials, reason, requeue, resume)

    def move_turn(self, turn_id, to):
        return self._call("move_turn", turn_id, to)

    def set_batch_shelf(self, shelf_root, initials, reason):
        return self._call("set_batch_shelf", shelf_root, initials, reason)


class RouteCase(unittest.TestCase):
    def setUp(self):
        self.locked = False
        self.admin = True
        self.fake = FakeClient({"status": STATUS})
        self._saved = {k: getattr(views.deps, k) for k in vars(views.deps)}
        d = views.deps
        d.is_locked = lambda: self.locked
        d.is_admin = lambda: self.admin
        d.driver_client = lambda: self.fake
        d.site_name = lambda code: {"BIT": "Buck Island STT", "HBE": "Hind Bank East FSA"}.get(code, "")
        d.now_iso = lambda: "2026-10-21T19:00:00-04:00"
        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(views.voyager_tools_bp)
        self.client = app.test_client()

    def tearDown(self):
        for key, value in self._saved.items():
            setattr(views.deps, key, value)

    def post(self, path, body):
        return self.client.post("/voyager-tools/api/v1runs" + path, json=body)


class Gates(RouteCase):
    ROUTES = (("get", "/api/v1runs"), ("get", "/api/v1runs/flush-preview?turn=6"), ("post", "/api/v1runs/flush"),
              ("post", "/api/v1runs/move"), ("post", "/api/v1runs/shelf"))

    def test_non_admin_is_refused_and_the_driver_is_not_called(self):
        self.admin = False
        for method, path in self.ROUTES:
            with self.subTest(path=path):
                r = getattr(self.client, method)("/voyager-tools" + path, **({"json": {}} if method == "post" else {}))
                self.assertEqual(r.status_code, 401)
                self.assertEqual(r.get_json()["error"], "locked")
        self.assertEqual(self.fake.calls, [])

    def test_a_locked_module_answers_423_for_admin_too(self):
        self.locked = True
        for method, path in self.ROUTES:
            with self.subTest(path=path):
                r = getattr(self.client, method)("/voyager-tools" + path, **({"json": {}} if method == "post" else {}))
                self.assertEqual(r.status_code, 423)
        self.assertEqual(self.fake.calls, [])


class Reads(RouteCase):
    def test_the_pane_model(self):
        r = self.client.get("/voyager-tools/api/v1runs")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["batch"]["batch_id"], "batch_1")
        self.assertEqual(body["read_at"], "2026-10-21T19:00:00-04:00")
        first, second = body["turns"]
        self.assertTrue(first["over_limit"] and first["actions"]["flush"])
        self.assertEqual(second["site_name"], "Hind Bank East FSA")
        self.assertFalse(second["actions"]["move"])  # the batch runs: the driver would refuse a move
        self.assertEqual(self.fake.calls, [("status",)])

    def test_a_paused_batch_offers_the_move(self):
        paused = {"engine_alive": False, "batch": dict(STATUS["batch"], status="paused")}
        self.fake.answers["status"] = paused
        body = self.client.get("/voyager-tools/api/v1runs").get_json()
        self.assertTrue(body["turns"][1]["actions"]["move"])
        self.assertEqual(body["batch"]["move_note"], "")

    def test_a_status_with_no_batch(self):
        self.fake.answers["status"] = {"batch": None}
        body = self.client.get("/voyager-tools/api/v1runs").get_json()
        self.assertIsNone(body["batch"])
        self.assertEqual(body["line"], "The driver has no Voyager 1 batch.")

    def test_preview_passes_the_driver_answer_unchanged(self):
        self.fake.answers["flush_preview"] = {"turn_id": 6, "can_flush": True, "folder_bytes": 5}
        r = self.client.get("/voyager-tools/api/v1runs/flush-preview?turn=6")
        self.assertEqual(r.get_json(), {"turn_id": 6, "can_flush": True, "folder_bytes": 5})
        self.assertEqual(self.fake.calls, [("flush_preview", 6)])

    def test_a_turn_is_up_to_six_ascii_digits_and_nothing_else_is_a_500(self):
        hostile = ["\u00b2", "\u0663", "9" * 5000, "-1", "1.5", "1e3", "", "   ", "1234567", "0", "\u0661\u0662", "\n", "+5"]
        for value in hostile:
            with self.subTest(turn=value[:12]):
                r = self.client.get("/voyager-tools/api/v1runs/flush-preview", query_string={"turn": value})
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.get_json()["error"], "Turn must be a whole number from 1 to 999999.")
                body = dict(Flush.BODY, turn_id=value)
                r = self.client.post("/voyager-tools/api/v1runs/flush", json=body)
                self.assertEqual(r.status_code, 400)
        huge = '{"turn_id": 1' + "0" * 5000 + ', "initials": "LO", "reason": "r", "confirm": true}'
        r = self.client.post("/voyager-tools/api/v1runs/flush", data=huge, content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.fake.calls, [])

    def test_a_turn_given_as_digits_still_works(self):
        self.fake.answers["flush_preview"] = {"can_flush": True}
        for value in ("6", " 6 ", "000006", "999999"):
            self.assertEqual(self.client.get("/voyager-tools/api/v1runs/flush-preview", query_string={"turn": value}).status_code, 200)

    def test_preview_refuses_a_bad_turn(self):
        for query in ("", "?turn=", "?turn=0", "?turn=-1", "?turn=abc", "?turn=1.5", "?turn=%3Cscript%3E"):
            with self.subTest(query=query):
                r = self.client.get("/voyager-tools/api/v1runs/flush-preview" + query)
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.get_json()["error"], "Turn must be a whole number from 1 to 999999.")
        self.assertEqual(self.fake.calls, [])


class Flush(RouteCase):
    BODY = {"turn_id": 6, "initials": "lo", "reason": "Gap fill ran 11.6 hours.", "requeue": True, "resume": False,
            "confirm": True}

    def test_a_flush_reason_of_exactly_the_cap_goes_through(self):
        self.assertEqual(self.post("/flush", dict(self.BODY, reason="x" * dc.MAX_REASON_CHARS)).status_code, 200)

    def test_a_flush_calls_the_driver_with_upper_cased_initials(self):
        self.fake.answers["flush"] = {"flushed": True, "requeued_as": 10, "reclaimed_bytes": 5, "rows_restored": 4,
                                      "resumed": False}
        r = self.post("/flush", self.BODY)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["requeued_as"], 10)
        self.assertEqual(self.fake.calls, [("flush", 6, "LO", "Gap fill ran 11.6 hours.", True, False)])

    def test_requeue_and_resume_default_to_true(self):
        body = {k: v for k, v in self.BODY.items() if k not in ("requeue", "resume")}
        self.post("/flush", body)
        self.assertEqual(self.fake.calls, [("flush", 6, "LO", "Gap fill ran 11.6 hours.", True, True)])

    def test_refusals_never_reach_the_driver(self):
        cases = [
            ("no confirm", {k: v for k, v in self.BODY.items() if k != "confirm"}, "Confirm the action in the dialog before it runs."),
            ("confirm text", dict(self.BODY, confirm="true"), "Confirm the action in the dialog before it runs."),
            ("no initials", {k: v for k, v in self.BODY.items() if k != "initials"}, views.LINE_INITIALS),
            ("one letter", dict(self.BODY, initials="L"), views.LINE_INITIALS),
            ("five letters", dict(self.BODY, initials="ABCDE"), views.LINE_INITIALS),
            ("digits", dict(self.BODY, initials="L0"), views.LINE_INITIALS),
            ("initials number", dict(self.BODY, initials=12), views.LINE_INITIALS),
            ("no reason", {k: v for k, v in self.BODY.items() if k != "reason"}, "reason is required."),
            ("blank reason", dict(self.BODY, reason="   "), "reason is required."),
            ("reason number", dict(self.BODY, reason=5), "reason must be text."),
            ("long reason", dict(self.BODY, reason="x" * (dc.MAX_REASON_CHARS + 1)), f"reason is longer than {dc.MAX_REASON_CHARS} characters."),
            ("no turn", {k: v for k, v in self.BODY.items() if k != "turn_id"}, "Turn must be a whole number from 1 to 999999."),
            ("turn zero", dict(self.BODY, turn_id=0), "Turn must be a whole number from 1 to 999999."),
            ("turn bool", dict(self.BODY, turn_id=True), "Turn must be a whole number from 1 to 999999."),
            ("turn float", dict(self.BODY, turn_id=6.5), "Turn must be a whole number from 1 to 999999."),
            ("requeue text", dict(self.BODY, requeue="yes"), "requeue must be true or false."),
            ("resume number", dict(self.BODY, resume=1), "resume must be true or false."),
        ]
        for name, body, sentence in cases:
            with self.subTest(name):
                r = self.post("/flush", body)
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.get_json()["error"], sentence)
        self.assertEqual(self.fake.calls, [])

    def test_a_body_that_is_not_an_object(self):
        for payload in ([], "x", 5, None):
            with self.subTest(payload=payload):
                r = self.client.post("/voyager-tools/api/v1runs/flush", json=payload)
                self.assertEqual(r.status_code, 400)
        r = self.client.post("/voyager-tools/api/v1runs/flush", data="not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_hostile_text_is_passed_as_text_and_answered_as_json(self):
        evil = '<script>alert("x")</script>\x00\r\n\'"'
        r = self.post("/flush", dict(self.BODY, reason=evil))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "application/json")
        self.assertEqual(self.fake.calls[0][3], evil.strip())

    def test_the_driver_409_is_relayed_verbatim(self):
        sentence = "a deposit stage of this turn has started, so it cannot be flushed"
        self.fake.errors["flush"] = dc.DriverError(sentence, 409)
        r = self.post("/flush", self.BODY)
        self.assertEqual((r.status_code, r.get_json()), (409, {"error": sentence}))

    def test_the_driver_400_is_relayed_verbatim(self):
        self.fake.errors["flush"] = dc.DriverError("reason is required", 400)
        r = self.post("/flush", self.BODY)
        self.assertEqual((r.status_code, r.get_json()), (400, {"error": "reason is required"}))

    def test_an_unreachable_driver_answers_502_with_one_sentence(self):
        self.fake.errors["flush"] = dc.DriverError(UNREACHABLE, unreachable=True)
        r = self.post("/flush", self.BODY)
        self.assertEqual((r.status_code, r.get_json()), (502, {"error": UNREACHABLE}))

    def test_a_driver_500_answers_502(self):
        sentence = "The driver on port 5093 answered with error 500: boom."
        self.fake.errors["flush"] = dc.DriverError(sentence, 500)
        r = self.post("/flush", self.BODY)
        self.assertEqual((r.status_code, r.get_json()), (502, {"error": sentence}))


class MoveAndShelf(RouteCase):
    def test_move_to_each_kind_of_target(self):
        for target in ("end", "start", 0, 4):
            r = self.post("/move", {"turn_id": 7, "to": target, "initials": "lo"})
            self.assertEqual(r.status_code, 200, target)
        self.assertEqual(self.fake.calls, [("move_turn", 7, "end"), ("move_turn", 7, "start"), ("move_turn", 7, 0),
                                           ("move_turn", 7, 4)])

    def test_move_refusals(self):
        base = {"turn_id": 7, "to": "end", "initials": "LO"}
        cases = [dict(base, initials="1"), dict(base, turn_id=0), dict(base, to=-1), dict(base, to="middle"),
                 dict(base, to=True), dict(base, to=None), {k: v for k, v in base.items() if k != "to"},
                 dict(base, to=1.5), dict(base, to=["end"])]
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(self.post("/move", body).status_code, 400)
        self.assertEqual(self.fake.calls, [])

    def test_move_relays_the_driver_409(self):
        self.fake.errors["move_turn"] = dc.DriverError("turn 6 is running; only pending turns move", 409)
        r = self.post("/move", {"turn_id": 6, "to": "end", "initials": "LO"})
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "turn 6 is running; only pending turns move"))

    def test_move_with_an_unreachable_driver(self):
        self.fake.errors["move_turn"] = dc.DriverError(UNREACHABLE, unreachable=True)
        r = self.post("/move", {"turn_id": 7, "to": "end", "initials": "LO"})
        self.assertEqual((r.status_code, r.get_json()["error"]), (502, UNREACHABLE))

    SHELF = {"shelf_root": "/volume5/Archive10_20TB/driver_deposits", "initials": "lo", "reason": "Archive8 is 96% full",
             "confirm": True}

    def test_shelf_change_calls_the_driver(self):
        self.fake.answers["set_batch_shelf"] = {"shelf_root": self.SHELF["shelf_root"], "applies_to_turns": [7, 8, 9]}
        r = self.post("/shelf", self.SHELF)
        self.assertEqual((r.status_code, r.get_json()["applies_to_turns"]), (200, [7, 8, 9]))
        self.assertEqual(self.fake.calls, [("set_batch_shelf", self.SHELF["shelf_root"], "LO", "Archive8 is 96% full")])

    def test_shelf_refusals_never_reach_the_driver(self):
        cases = [
            ("no confirm", {k: v for k, v in self.SHELF.items() if k != "confirm"}),
            ("bad initials", dict(self.SHELF, initials="X")),
            ("no folder", dict(self.SHELF, shelf_root="")),
            ("folder number", dict(self.SHELF, shelf_root=5)),
            ("folder too long", dict(self.SHELF, shelf_root="/x" * 600)),
            ("no reason", dict(self.SHELF, reason="")),
            ("reason over the driver cap", dict(self.SHELF, reason="x" * (dc.MAX_REASON_CHARS + 1))),
        ]
        for name, body in cases:
            with self.subTest(name):
                self.assertEqual(self.post("/shelf", body).status_code, 400)
        self.assertEqual(self.fake.calls, [])

    def test_a_shelf_reason_of_exactly_the_cap_goes_through(self):
        reason = "x" * dc.MAX_REASON_CHARS
        self.assertEqual(dc.MAX_REASON_CHARS, 300)
        self.assertEqual(self.post("/shelf", dict(self.SHELF, reason=reason)).status_code, 200)

    def test_shelf_relays_the_driver_409(self):
        sentence = "a deposit is in progress, so the Shelf cannot change now"
        self.fake.errors["set_batch_shelf"] = dc.DriverError(sentence, 409)
        r = self.post("/shelf", self.SHELF)
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, sentence))


class RealClientWiring(RouteCase):
    def test_the_default_dependency_builds_a_driver_client(self):
        views.deps.driver_client = self._saved["driver_client"]
        os.environ[dc.DRIVER_URL_ENV] = "http://127.0.0.1:9"
        self.addCleanup(os.environ.pop, dc.DRIVER_URL_ENV, None)
        r = self.client.get("/voyager-tools/api/v1runs")
        self.assertEqual(r.status_code, 502)
        self.assertRegex(r.get_json()["error"], r"^The driver on port 9 did not answer: .+\.$")

    def test_a_bad_driver_address_is_one_sentence_not_a_crash(self):
        views.deps.driver_client = self._saved["driver_client"]
        os.environ[dc.DRIVER_URL_ENV] = "file:///etc/passwd"
        self.addCleanup(os.environ.pop, dc.DRIVER_URL_ENV, None)
        r = self.client.get("/voyager-tools/api/v1runs")
        self.assertEqual(r.status_code, 400)
        self.assertIn(dc.DRIVER_URL_ENV, r.get_json()["error"])


class Fragment(RouteCase):
    def test_the_panel_lists_the_pane_and_loads_its_script(self):
        payload = self.client.get("/voyager-tools/fragment").get_json()
        self.assertIn('data-vt-tab="v1runs"', payload["html"])
        self.assertIn('id="vt-v1runs"', payload["html"])
        self.assertIn("/voyager-tools/static/vt_v1runs.js?v=" + views.ASSET_VERSION, payload["scripts"])
        self.assertEqual(payload["scripts"][0].split("?")[0].rsplit("/", 1)[1], "voyager_tools.js")

    def test_the_standalone_page_loads_the_same_script(self):
        page = self.client.get("/voyager-tools/").get_data(as_text=True)
        self.assertIn("vt_v1runs.js?v=" + views.ASSET_VERSION, page)


if __name__ == "__main__":
    unittest.main()
