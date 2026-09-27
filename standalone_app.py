#!/usr/bin/env python3
"""
Module: standalone_app.py
Purpose: A thin host that serves the Voyager tools blueprint on its own port
         for smoke tests away from the desktop. The desktop on port 5090 is
         the real home (vicarius_ui_os/voyager_tools_views.py mounts the same
         blueprint inside the admin session); this host exists so the page
         can be opened alone and is never started by the platform.
Inputs:  --port (default 5098), --host (default 127.0.0.1); the admin PIN
         from the environment variable VOYAGER_TOOLS_ADMIN_PIN (no PIN means
         every route stays locked at 401).
Outputs: an HTTP server; the session cookie is voyager_tools_session, never
         Flask's default name, so it can never overwrite the desktop's
         vicarius_session cookie on the same host. Admin mode here ends
         ADMIN_IDLE_S seconds after the last admin action, the same rule as
         the desktop (decision 8 of the manual edit chain); nothing is
         imported from the desktop.

Usage:
    VOYAGER_TOOLS_ADMIN_PIN=... python3 standalone_app.py --port 5098
"""
from __future__ import annotations

import argparse
import hmac
import math
import os
import secrets
import sys
import time
from pathlib import Path

from flask import Flask, g, jsonify, redirect, request, session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from voyagertools import views  # noqa: E402

DEFAULT_PORT = 5098
DEFAULT_HOST = "127.0.0.1"
SESSION_COOKIE_NAME = "voyager_tools_session"
ADMIN_PIN_ENV = "VOYAGER_TOOLS_ADMIN_PIN"
SECRET_ENV = "VOYAGER_TOOLS_SECRET"
# Ports other VICARIUS services own (CLAUDE.md, Ports); this host refuses them.
FORBIDDEN_PORTS = frozenset({5050, 5055, 5065, 5070, 5075, 5077, 5080, 5081, 5083, 5085, 5090, 5092, 5093, 5096})
MIN_PORT, MAX_PORT = 1024, 65535
MAX_PIN_CHARS = 64
ADMIN_IDLE_S = 600
FLAG_KEY = "admin_unlocked"
SEEN_KEY = "admin_seen"
SESSION_FLAGS = (FLAG_KEY, SEEN_KEY)
HTTP_ERROR_FLOOR = 400

# The clock the idle limit reads; tests replace it.
now_fn = time.time


def admin_seconds_left(sess) -> int | None:
    """Return the whole seconds left in admin mode for a session, or None when not admin.

    Parameters:
        sess: the session mapping.
    Returns:
        None unless the flag is true and admin_seen is a number no more than
        ADMIN_IDLE_S seconds old (a clock stepped back counts as no time).
    """
    seen = sess.get(SEEN_KEY)
    if sess.get(FLAG_KEY) is not True or isinstance(seen, bool) or not isinstance(seen, (int, float)):
        return None
    if not math.isfinite(seen):
        return None
    idle = max(0.0, now_fn() - seen)
    if idle > ADMIN_IDLE_S:
        return None
    return max(0, math.ceil(ADMIN_IDLE_S - idle))


def admin_status(sess) -> dict:
    """The admin status payload, the desktop's shape: unlocked, expires_in_s, idle_limit_s (expires_at is not kept here)."""
    left = admin_seconds_left(sess)
    return {"unlocked": left is not None, "expires_at": None, "expires_in_s": left, "idle_limit_s": ADMIN_IDLE_S}


def create_app(admin_pin: str | None = None) -> Flask:
    """Build the standalone host around the blueprint.

    Parameters:
        admin_pin: the PIN that unlocks admin mode for a session; None reads
            VOYAGER_TOOLS_ADMIN_PIN, and a missing PIN keeps every unlock refused.
    """
    pin = admin_pin if admin_pin is not None else os.environ.get(ADMIN_PIN_ENV, "")
    app = Flask(__name__)
    app.config["SESSION_COOKIE_NAME"] = SESSION_COOKIE_NAME
    app.secret_key = os.environ.get(SECRET_ENV) or secrets.token_hex(32)
    app.json.sort_keys = False
    app.register_blueprint(views.voyager_tools_bp)

    @app.before_request
    def _admin_idle_expiry():
        """Clear the admin flags of a session past the idle limit, before any view reads them."""
        if FLAG_KEY in session and admin_seconds_left(session) is None:
            for key in SESSION_FLAGS:
                session.pop(key, None)

    @app.after_request
    def _admin_renew(response):
        """Restart the idle limit after an admin-gated POST that succeeded (never a GET or a poll)."""
        if (request.method == "POST" and response.status_code < HTTP_ERROR_FLOOR
                and g.get("admin_gated") and admin_seconds_left(session) is not None):
            session[SEEN_KEY] = now_fn()
        return response

    @app.route("/")
    def root():
        """The page lives under the blueprint prefix."""
        return redirect(views.URL_PREFIX, code=302)

    @app.route("/api/admin/status")
    def admin_status_route():
        """Whether this session is in admin mode, and for how long; never renews."""
        return jsonify(admin_status(session))

    @app.route("/api/admin/unlock", methods=["POST"])
    def admin_unlock():
        """Unlock admin mode for this session with the PIN from the environment."""
        data = request.get_json(silent=True) or {}
        given = str(data.get("pin") or "")[:MAX_PIN_CHARS]
        if not pin:
            return jsonify({"ok": False, "error": f"admin unlock is not configured; set {ADMIN_PIN_ENV}"}), 401
        if not hmac.compare_digest(given, pin):
            return jsonify({"ok": False, "error": "Wrong PIN"}), 401
        session[FLAG_KEY] = True
        session[SEEN_KEY] = now_fn()
        return jsonify({"ok": True, **admin_status(session)})

    @app.route("/api/admin/lock", methods=["POST"])
    def admin_lock():
        """Leave admin mode."""
        for key in SESSION_FLAGS:
            session.pop(key, None)
        return jsonify({"ok": True, **admin_status(session)})

    @app.route("/api/admin/renew", methods=["POST"])
    def admin_renew():
        """Stay in admin mode: restart the idle limit, 401 outside admin mode."""
        if admin_seconds_left(session) is None:
            return jsonify(dict(views.ADMIN_ERROR)), views.HTTP_UNAUTHORISED
        session[SEEN_KEY] = now_fn()
        return jsonify(admin_status(session))

    return app


def _parser() -> argparse.ArgumentParser:
    """The command line: --port and --host."""
    parser = argparse.ArgumentParser(description="Serve the Voyager tools page on its own port (smoke tests only).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"TCP port (default {DEFAULT_PORT})")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind address (default {DEFAULT_HOST})")
    return parser


def validate_port(port: int) -> int:
    """A port this host may bind: inside the unprivileged range and not one another VICARIUS service owns."""
    if not isinstance(port, int) or isinstance(port, bool) or not MIN_PORT <= port <= MAX_PORT:
        raise ValueError(f"port must be an integer between {MIN_PORT} and {MAX_PORT}, got {port!r}")
    if port in FORBIDDEN_PORTS:
        raise ValueError(f"port {port} belongs to another VICARIUS service; choose another (default {DEFAULT_PORT})")
    return port


def main(argv=None) -> int:
    """Parse, validate, serve."""
    args = _parser().parse_args(argv)
    try:
        port = validate_port(args.port)
    except ValueError as exc:
        print(f"standalone_app: {exc}", file=sys.stderr)
        return 2
    app = create_app()
    print(f"Voyager tools standalone host on http://{args.host}:{port}{views.URL_PREFIX}")
    app.run(host=args.host, port=port, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
