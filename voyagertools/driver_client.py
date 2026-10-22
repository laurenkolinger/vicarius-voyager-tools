#!/usr/bin/env python3
"""
Module: voyagertools/driver_client.py
Purpose: A small HTTP client for the driver (VOX) API, used by the Voyager 1
         runs pane to read the batch, preview and run a flush, move a pending
         turn and change the batch's Shelf. Every call is for lane voyager1.
         Every method returns the parsed JSON object the driver answered or
         raises DriverError, which carries one plain sentence; a 400, 404 or
         409 from the driver passes its own "error" sentence through verbatim.
Inputs:  the driver address from the environment variable VOX_DRIVER_URL,
         else DEFAULT_DRIVER_URL; the arguments of each method.
Outputs: parsed JSON objects (dict); nothing is written to disk.

Example:
    client = DriverClient()
    batch_status = client.status()
    preview = client.flush_preview(6)
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping

DRIVER_URL_ENV = "VOX_DRIVER_URL"
DEFAULT_DRIVER_URL = "http://127.0.0.1:5093"
LANE = "voyager1"
# A status read is a file read; a preview walks the turn's folder for its size;
# a flush ends processes (a grace period before SIGKILL) and deletes the folder.
STATUS_TIMEOUT_S = 20
PREVIEW_TIMEOUT_S = 120
WRITE_TIMEOUT_S = 600
# A flush waits up to 30 s of grace and 30 s for the build to go, then deletes a Workbench folder of several
# hundred GB; it can take minutes, so it has its own limit.
FLUSH_TIMEOUT_S = 900
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
# The driver refuses a flush or Shelf change reason longer than this (driver/flush.py REASON_MAX_CHARS); the
# panel routes and the pane inputs use the same number.
MAX_REASON_CHARS = 300
MAX_PATH_CHARS = 1024
# The driver's own refusals and malformed-body answers, relayed with their status.
RELAYED_STATUSES = (400, 404, 409)
MOVE_WORDS = ("end", "start")
HTTP_BAD_REQUEST = 400
HTTP_BAD_GATEWAY = 502


class DriverError(Exception):
    """A driver call that did not give a JSON object; carries one plain sentence.

    Attributes:
        reason: the sentence, also the exception text.
        status: the HTTP status the driver answered, or None when it did not answer.
        unreachable: True when no answer arrived (refused, timed out, no route).
    """

    def __init__(self, reason: str, status: int | None = None, unreachable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.unreachable = unreachable

    @property
    def http_status(self) -> int:
        """The status this panel answers with: the driver's 400, 404 or 409 as they are, else 502."""
        return self.status if self.status in RELAYED_STATUSES else HTTP_BAD_GATEWAY


def driver_url() -> str:
    """The driver's base URL: VOX_DRIVER_URL, else DEFAULT_DRIVER_URL, without a trailing slash.

    Raises:
        DriverError: the value is not an http address with a host.
    """
    raw = (os.environ.get(DRIVER_URL_ENV) or DEFAULT_DRIVER_URL).strip().rstrip("/")
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme != "http" or not parts.hostname or parts.path or parts.query or parts.fragment:
        raise DriverError(f"{DRIVER_URL_ENV} must be an address like {DEFAULT_DRIVER_URL}, got {raw[:80]!r}.",
                          HTTP_BAD_REQUEST)
    return raw


def _port_words(base: str) -> str:
    """The words naming the driver in a sentence: "The driver on port 5093"."""
    port = urllib.parse.urlsplit(base).port
    return f"The driver on port {port}" if port else "The driver"


def _os_reason(reason) -> str:
    """The words for a failed connection: the operating system's strerror for an OSError, else the text of the reason."""
    if isinstance(reason, OSError) and reason.strerror:
        return str(reason.strerror)
    return str(reason)


def _sentence(text: str) -> str:
    """The text as one sentence: trimmed, ending in a full stop."""
    clean = " ".join(str(text).split())
    return clean if clean.endswith((".", "!", "?")) else clean + "."


def _positive_int(value, name: str) -> int:
    """A whole number of at least 1 (a turn id); a bool or text is refused."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DriverError(f"{name} must be a whole number of 1 or more.", HTTP_BAD_REQUEST)
    return value


def _text(value, name: str, limit: int) -> str:
    """A non-blank string of at most limit characters."""
    if not isinstance(value, str) or not value.strip():
        raise DriverError(f"{name} must be text and cannot be blank.", HTTP_BAD_REQUEST)
    if len(value) > limit:
        raise DriverError(f"{name} is longer than {limit} characters.", HTTP_BAD_REQUEST)
    return value.strip()


def _flag(value, name: str) -> bool:
    """A true or false value; anything else is refused."""
    if not isinstance(value, bool):
        raise DriverError(f"{name} must be true or false.", HTTP_BAD_REQUEST)
    return value


def _move_target(value):
    """The `to` of a move: "end", "start" or an index of 0 or more among the pending turns."""
    if value in MOVE_WORDS:
        return value
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DriverError('to must be "end", "start" or a position of 0 or more.', HTTP_BAD_REQUEST)
    return value


def _error_sentence(body: bytes) -> str | None:
    """The "error" sentence of a driver answer body, or None when the body carries none."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    message = data.get("error") if isinstance(data, dict) else None
    return message.strip() if isinstance(message, str) and message.strip() else None


class DriverClient:
    """Calls the driver for lane voyager1.

    Parameters:
        base_url: the driver address; None reads VOX_DRIVER_URL at construction.
        opener: a urllib opener (a test passes a fake); None uses urllib's default.
    """

    def __init__(self, base_url: str | None = None, opener=None):
        self.base_url = base_url.rstrip("/") if base_url else driver_url()
        self._open = (opener or urllib.request.build_opener()).open

    # -- transport -------------------------------------------------------------

    def _request(self, method: str, path: str, query: Mapping | None = None, body: Mapping | None = None,
                 timeout: float = STATUS_TIMEOUT_S) -> dict:
        """One call to the driver; the parsed JSON object it answered.

        Parameters:
            method: GET, POST or PATCH.
            path: the route, for example /api/status.
            query: extra query parameters; lane=voyager1 is always added.
            body: the JSON body of a POST or PATCH.
            timeout: seconds to wait for the answer.
        Raises:
            DriverError: no answer (unreachable), a refusal (its sentence verbatim for 400, 404 and 409),
                any other error status, or an answer that is not a JSON object.
        """
        params = {"lane": LANE}
        params.update(query or {})
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method,
                                         headers={"Accept": "application/json", "Content-Type": "application/json"})
        who = _port_words(self.base_url)
        try:
            with self._open(request, timeout=timeout) as answer:
                raw = answer.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc, who) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)) or isinstance(exc, (socket.timeout, TimeoutError)):
                why = f"no answer within {int(timeout)} seconds"
            else:
                why = _os_reason(reason) or exc.__class__.__name__
            raise DriverError(f"{who} did not answer: {why.rstrip('.')}.", unreachable=True) from exc
        return self._parse(raw, who)

    @staticmethod
    def _http_error(exc: urllib.error.HTTPError, who: str) -> DriverError:
        """The DriverError for an error status: the driver's own sentence for 400, 404 and 409."""
        try:
            body = exc.read(MAX_RESPONSE_BYTES)
        except OSError:
            body = b""
        sentence = _error_sentence(body)
        if exc.code in RELAYED_STATUSES and sentence:
            return DriverError(sentence, exc.code)
        detail = f": {sentence}" if sentence else ""
        return DriverError(_sentence(f"{who} answered with error {exc.code}{detail}"), exc.code)

    @staticmethod
    def _parse(raw: bytes, who: str) -> dict:
        """The JSON object in an answer body, or a DriverError saying what came back instead."""
        if len(raw) > MAX_RESPONSE_BYTES:
            raise DriverError(f"{who} answered with more than {MAX_RESPONSE_BYTES // (1024 * 1024)} MB.")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise DriverError(f"{who} answered something that is not JSON.") from exc
        if not isinstance(data, dict):
            raise DriverError(f"{who} answered JSON that is not an object.")
        return data

    # -- the calls ---------------------------------------------------------------

    def status(self) -> dict:
        """GET /api/status: the Voyager 1 lane's batch, turns and progress."""
        return self._request("GET", "/api/status")

    def flush_preview(self, turn_id: int) -> dict:
        """GET /api/flush?turn=: what flushing the turn would do, without doing it.

        Parameters:
            turn_id: the turn number (1 or more).
        """
        turn = _positive_int(turn_id, "turn_id")
        return self._request("GET", "/api/flush", {"turn": turn}, timeout=PREVIEW_TIMEOUT_S)

    def flush(self, turn_id: int, initials: str, reason: str, requeue: bool, resume: bool) -> dict:
        """POST /api/flush: end the turn's build, give its rows back to the Archive and delete its Workbench folder.

        Parameters:
            turn_id: the turn number (1 or more).
            initials: who flushes, two to four letters.
            reason: why, in a sentence.
            requeue: put a pending copy of the turn at the end of the queue.
            resume: carry on with the next pending turn afterwards.
        """
        body = {"turn_id": _positive_int(turn_id, "turn_id"), "initials": _text(initials, "initials", 4),
                "reason": _text(reason, "reason", MAX_REASON_CHARS), "requeue": _flag(requeue, "requeue"),
                "resume": _flag(resume, "resume")}
        return self._request("POST", "/api/flush", body=body, timeout=FLUSH_TIMEOUT_S)

    def move_turn(self, turn_id: int, to) -> dict:
        """POST /api/turns/<id>/move: reorder a pending turn.

        Parameters:
            turn_id: the pending turn's number.
            to: "end", "start" or a position among the pending turns, counted from 0.
        """
        turn = _positive_int(turn_id, "turn_id")
        return self._request("POST", f"/api/turns/{turn}/move", body={"to": _move_target(to)})

    def set_batch_shelf(self, shelf_root: str, initials: str, reason: str) -> dict:
        """PATCH /api/batch: change the Shelf folder the running batch deposits into.

        Parameters:
            shelf_root: the absolute Shelf folder.
            initials: who changes it, two to four letters.
            reason: why, in a sentence.
        """
        body = {"shelf_root": _text(shelf_root, "shelf_root", MAX_PATH_CHARS),
                "initials": _text(initials, "initials", 4), "reason": _text(reason, "reason", MAX_REASON_CHARS)}
        return self._request("PATCH", "/api/batch", body=body, timeout=WRITE_TIMEOUT_S)
