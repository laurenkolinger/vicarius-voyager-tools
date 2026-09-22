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
         vicarius_session cookie on the same host.

Usage:
    VOYAGER_TOOLS_ADMIN_PIN=... python3 standalone_app.py --port 5098
"""
from __future__ import annotations

import argparse
import hmac
import os
import secrets
import sys
from pathlib import Path

from flask import Flask, jsonify, redirect, request, session

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

    @app.route("/")
    def root():
        """The page lives under the blueprint prefix."""
        return redirect(views.URL_PREFIX, code=302)

    @app.route("/api/admin/status")
    def admin_status():
        """Whether this session is in admin mode."""
        return jsonify({"unlocked": bool(session.get("admin_unlocked"))})

    @app.route("/api/admin/unlock", methods=["POST"])
    def admin_unlock():
        """Unlock admin mode for this session with the PIN from the environment."""
        data = request.get_json(silent=True) or {}
        given = str(data.get("pin") or "")[:MAX_PIN_CHARS]
        if not pin:
            return jsonify({"ok": False, "error": f"admin unlock is not configured; set {ADMIN_PIN_ENV}"}), 401
        if not hmac.compare_digest(given, pin):
            return jsonify({"ok": False, "error": "Wrong PIN"}), 401
        session["admin_unlocked"] = True
        return jsonify({"ok": True, "unlocked": True})

    @app.route("/api/admin/lock", methods=["POST"])
    def admin_lock():
        """Leave admin mode."""
        session.pop("admin_unlocked", None)
        return jsonify({"ok": True, "unlocked": False})

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
