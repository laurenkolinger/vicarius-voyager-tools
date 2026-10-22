#!/usr/bin/env python3
"""
Module: tests/test_driver_client.py
Purpose: Tests of voyagertools.driver_client against a fake driver served on
         127.0.0.1 port 0: the happy path of every method with the request
         the driver would see (method, path, lane, body), a 400 and a 409
         whose sentence passes through verbatim, a 404, a 500, a non-JSON
         answer, a JSON answer that is not an object, a connection refused,
         a timeout, an oversized answer, and arguments the client refuses
         before any call. The live driver on port 5093 is never called.
Inputs:  an in-process fake HTTP server; environment variable VOX_DRIVER_URL set and restored.
Outputs: unittest results.

Run from the clone root:
    /usr/bin/python3 -m unittest tests.test_driver_client -v
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voyagertools import driver_client as dc  # noqa: E402


class FakeDriver:
    """A driver on a free local port that records requests and answers from a script.

    Attributes:
        calls: list of dicts (method, path, query, body) in arrival order.
        answer: (status, body bytes, delay seconds) the next requests get.
    """

    def __init__(self):
        self.calls: list[dict] = []
        self.answer = (200, b"{}", 0.0)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence the test run
                pass

            def _handle(self):
                size = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(size) if size else b""
                parts = urlsplit(self.path)
                outer.calls.append({"method": self.command, "path": parts.path, "query": parse_qs(parts.query),
                                    "body": json.loads(raw) if raw else None})
                status, payload, delay = outer.answer
                time.sleep(delay)
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = do_POST = do_PATCH = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def reply(self, status: int, obj=None, raw: bytes | None = None, delay: float = 0.0):
        """Set the next answer: a JSON object, or raw bytes."""
        self.answer = (status, raw if raw is not None else json.dumps(obj).encode(), delay)


class ClientCalls(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDriver().__enter__()
        self.addCleanup(self.fake.__exit__)
        self.client = dc.DriverClient(self.fake.url)

    def test_status_reads_the_voyager1_lane(self):
        self.fake.reply(200, {"batch": {"batch_id": "b1"}})
        self.assertEqual(self.client.status(), {"batch": {"batch_id": "b1"}})
        call = self.fake.calls[0]
        self.assertEqual((call["method"], call["path"], call["query"]), ("GET", "/api/status", {"lane": ["voyager1"]}))

    def test_flush_preview_sends_the_turn(self):
        self.fake.reply(200, {"can_flush": True})
        self.assertEqual(self.client.flush_preview(6), {"can_flush": True})
        call = self.fake.calls[0]
        self.assertEqual((call["method"], call["path"]), ("GET", "/api/flush"))
        self.assertEqual(call["query"], {"lane": ["voyager1"], "turn": ["6"]})

    def test_flush_posts_the_contract_body(self):
        self.fake.reply(200, {"flushed": True, "requeued_as": 10})
        out = self.client.flush(6, "LO", "hung on gap fill", True, False)
        self.assertEqual(out["requeued_as"], 10)
        call = self.fake.calls[0]
        self.assertEqual((call["method"], call["path"], call["query"]), ("POST", "/api/flush", {"lane": ["voyager1"]}))
        self.assertEqual(call["body"], {"turn_id": 6, "initials": "LO", "reason": "hung on gap fill",
                                        "requeue": True, "resume": False})

    def test_only_the_flush_has_the_long_timeout(self):
        self.assertEqual(dc.FLUSH_TIMEOUT_S, 900)
        seen = []
        client = dc.DriverClient(self.fake.url)
        real = client._open
        client._open = lambda request, timeout=None: (seen.append((request.get_method() + " " + request.full_url.split("?")[0].rsplit("/", 1)[1], timeout)),
                                                      real(request, timeout=timeout))[1]
        self.fake.reply(200, {"ok": True})
        client.flush(6, "LO", "hung", True, True)
        client.set_batch_shelf("/volume5/x", "LO", "why")
        client.status()
        client.flush_preview(6)
        timeouts = dict(seen)
        self.assertEqual(timeouts["POST flush"], dc.FLUSH_TIMEOUT_S)
        self.assertEqual(timeouts["PATCH batch"], dc.WRITE_TIMEOUT_S)
        self.assertEqual(timeouts["GET status"], dc.STATUS_TIMEOUT_S)
        self.assertEqual(timeouts["GET flush"], dc.PREVIEW_TIMEOUT_S)
        self.assertLess(dc.WRITE_TIMEOUT_S, dc.FLUSH_TIMEOUT_S)

    def test_move_turn_accepts_end_start_and_a_position(self):
        self.fake.reply(200, {"order": [7, 8]})
        for target in ("end", "start", 0, 3):
            self.client.move_turn(8, target)
        bodies = [c["body"] for c in self.fake.calls]
        self.assertEqual(bodies, [{"to": "end"}, {"to": "start"}, {"to": 0}, {"to": 3}])
        self.assertTrue(all(c["path"] == "/api/turns/8/move" and c["method"] == "POST" for c in self.fake.calls))

    def test_set_batch_shelf_patches_the_batch(self):
        self.fake.reply(200, {"shelf_root": "/volume5/Archive10_20TB/driver_deposits", "applies_to_turns": [7, 8, 9]})
        out = self.client.set_batch_shelf("/volume5/Archive10_20TB/driver_deposits", "LO", "Archive8 is full")
        self.assertEqual(out["applies_to_turns"], [7, 8, 9])
        call = self.fake.calls[0]
        self.assertEqual((call["method"], call["path"]), ("PATCH", "/api/batch"))
        self.assertEqual(call["body"], {"shelf_root": "/volume5/Archive10_20TB/driver_deposits", "initials": "LO",
                                        "reason": "Archive8 is full"})


class ClientFailures(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDriver().__enter__()
        self.addCleanup(self.fake.__exit__)
        self.client = dc.DriverClient(self.fake.url)

    def assert_error(self, call, reason=None, status=None, unreachable=False):
        with self.assertRaises(dc.DriverError) as ctx:
            call()
        if reason is not None:
            self.assertEqual(ctx.exception.reason, reason)
        self.assertEqual(ctx.exception.status, status)
        self.assertEqual(ctx.exception.unreachable, unreachable)
        self.assertEqual(str(ctx.exception), ctx.exception.reason)
        return ctx.exception

    def test_409_sentence_passes_through_verbatim(self):
        sentence = "a deposit stage of this turn has started, so it cannot be flushed"
        self.fake.reply(409, {"error": sentence})
        exc = self.assert_error(lambda: self.client.flush(6, "LO", "why", True, True), sentence, 409)
        self.assertEqual(exc.http_status, 409)

    def test_400_sentence_passes_through_verbatim(self):
        self.fake.reply(400, {"error": "turn_id is required"})
        exc = self.assert_error(lambda: self.client.move_turn(3, "end"), "turn_id is required", 400)
        self.assertEqual(exc.http_status, 400)

    def test_404_is_relayed(self):
        self.fake.reply(404, {"error": "no turn 99"})
        exc = self.assert_error(lambda: self.client.flush_preview(99), "no turn 99", 404)
        self.assertEqual(exc.http_status, 404)

    def test_500_becomes_a_502_sentence_naming_the_driver(self):
        self.fake.reply(500, {"error": "boom"})
        exc = self.assert_error(lambda: self.client.status(), None, 500)
        self.assertEqual(exc.http_status, 502)
        self.assertRegex(exc.reason, r"^The driver on port \d+ answered with error 500: boom\.$")

    def test_500_without_a_json_body_still_gives_one_sentence(self):
        self.fake.reply(500, raw=b"<html>Internal Server Error</html>")
        exc = self.assert_error(lambda: self.client.status(), None, 500)
        self.assertRegex(exc.reason, r"^The driver on port \d+ answered with error 500\.$")

    def test_a_409_without_an_error_sentence_is_not_passed_through_blank(self):
        self.fake.reply(409, {"detail": "x"})
        exc = self.assert_error(lambda: self.client.status(), None, 409)
        self.assertIn("error 409", exc.reason)

    def test_non_json_answer(self):
        self.fake.reply(200, raw=b"not json at all")
        exc = self.assert_error(lambda: self.client.status())
        self.assertIn("not JSON", exc.reason)
        self.assertEqual(exc.http_status, 502)

    def test_json_that_is_not_an_object(self):
        self.fake.reply(200, raw=b"[1, 2, 3]")
        exc = self.assert_error(lambda: self.client.status())
        self.assertIn("not an object", exc.reason)

    def test_invalid_utf8_answer(self):
        self.fake.reply(200, raw=b"\xff\xfe{")
        self.assert_error(lambda: self.client.status())

    def test_an_oversized_answer_is_refused(self):
        original = dc.MAX_RESPONSE_BYTES
        dc.MAX_RESPONSE_BYTES = 1024
        self.addCleanup(setattr, dc, "MAX_RESPONSE_BYTES", original)
        self.fake.reply(200, raw=b'{"x": "' + b"a" * 4096 + b'"}')
        exc = self.assert_error(lambda: self.client.status())
        self.assertIn("more than", exc.reason)

    def test_connection_refused_is_unreachable_with_the_port_sentence(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        client = dc.DriverClient(f"http://127.0.0.1:{port}")
        exc = self.assert_error(lambda: client.status(), None, None, unreachable=True)
        self.assertEqual(exc.reason, f"The driver on port {port} did not answer: Connection refused.")
        self.assertEqual(exc.http_status, 502)

    def test_timeout_is_unreachable(self):
        original = dc.STATUS_TIMEOUT_S
        dc.STATUS_TIMEOUT_S = 0.2
        self.addCleanup(setattr, dc, "STATUS_TIMEOUT_S", original)
        self.fake.reply(200, {"ok": True}, delay=1.5)
        client = dc.DriverClient(self.fake.url)
        exc = self.assert_error(lambda: client._request("GET", "/api/status", timeout=0.2), None, None, unreachable=True)
        self.assertIn("did not answer", exc.reason)
        self.assertIn("no answer within", exc.reason)

    def test_arguments_are_refused_before_any_call(self):
        bad_calls = [
            lambda: self.client.flush_preview(0),
            lambda: self.client.flush_preview("6"),
            lambda: self.client.flush_preview(True),
            lambda: self.client.flush(6, "", "why", True, True),
            lambda: self.client.flush(6, "LO", "   ", True, True),
            lambda: self.client.flush(6, "LO", "why", "yes", True),
            lambda: self.client.flush(6, "LO", "x" * (dc.MAX_REASON_CHARS + 1), True, True),
            lambda: self.client.flush(6, None, "why", True, True),
            lambda: self.client.move_turn(3, -1),
            lambda: self.client.move_turn(3, "middle"),
            lambda: self.client.move_turn(3, True),
            lambda: self.client.move_turn("3", "end"),
            lambda: self.client.set_batch_shelf("", "LO", "why"),
            lambda: self.client.set_batch_shelf("/x" * 600, "LO", "why"),
            lambda: self.client.set_batch_shelf(["/x"], "LO", "why"),
        ]
        for call in bad_calls:
            with self.subTest(call=call):
                exc = self.assert_error(call, None, 400)
                self.assertEqual(exc.http_status, 400)
        self.assertEqual(self.fake.calls, [], "a refused argument never reaches the driver")


class DriverUrl(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get(dc.DRIVER_URL_ENV)
        self.addCleanup(self.restore)

    def restore(self):
        if self.saved is None:
            os.environ.pop(dc.DRIVER_URL_ENV, None)
        else:
            os.environ[dc.DRIVER_URL_ENV] = self.saved

    def test_default_and_environment(self):
        os.environ.pop(dc.DRIVER_URL_ENV, None)
        self.assertEqual(dc.driver_url(), "http://127.0.0.1:5093")
        os.environ[dc.DRIVER_URL_ENV] = "http://127.0.0.1:5198/"
        self.assertEqual(dc.driver_url(), "http://127.0.0.1:5198")
        self.assertEqual(dc.DriverClient().base_url, "http://127.0.0.1:5198")

    def test_a_value_that_is_not_an_http_address_is_refused(self):
        for bad in ("file:///etc/passwd", "ftp://x", "127.0.0.1:5093", "http://", "http://h:1/path", "http://h:1?x=1"):
            os.environ[dc.DRIVER_URL_ENV] = bad
            with self.subTest(bad=bad):
                with self.assertRaises(dc.DriverError):
                    dc.driver_url()


if __name__ == "__main__":
    unittest.main()
