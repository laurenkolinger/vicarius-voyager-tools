"""The standalone host ends admin mode after ADMIN_IDLE_S without an admin action.

Decision 8 of the manual edit chain and program C spec section 5.1: polls and
status reads never renew; a POST that passes the admin gate and succeeds
renews; the guard marks a passing check so the host can tell. The clock is
standalone_app.now_fn, patched per test.
"""
import sys
import unittest
from pathlib import Path

CLONE = Path(__file__).resolve().parent.parent
if str(CLONE) not in sys.path:
    sys.path.insert(0, str(CLONE))

import standalone_app  # noqa: E402
from voyagertools import views  # noqa: E402

T0 = 1_790_000_000.0


class StandaloneIdle(unittest.TestCase):
    def setUp(self):
        self._saved = {k: getattr(views.deps, k) for k in vars(views.deps)}
        self._now = standalone_app.now_fn
        self.t = T0
        standalone_app.now_fn = lambda: self.t
        views.deps.is_locked = lambda: False
        views.deps.now_iso = lambda: "2026-09-27T01:00:00-04:00"
        self.app = standalone_app.create_app(admin_pin="1234")
        self.seen = {}

        def probe():
            from flask import g
            self.seen["gated"] = g.get("admin_gated")
            return views.jsonify({"ok": True})

        def probe_fail():
            raise views.RequestError("nope", status=409)

        # Routes are added before the first request, as Flask requires.
        self.app.add_url_rule("/probe", "probe", views.guarded(probe), methods=["POST"])
        self.app.add_url_rule("/probe-fail", "probe_fail", views.guarded(probe_fail), methods=["POST"])
        self.c = self.app.test_client()
        self.assertEqual(self.c.post("/api/admin/unlock", json={"pin": "1234"}).status_code, 200)

    def tearDown(self):
        standalone_app.now_fn = self._now
        for key, value in self._saved.items():
            setattr(views.deps, key, value)

    def status(self):
        return self.c.get("/api/admin/status").get_json()

    def test_unlock_answers_the_status_shape(self):
        body = self.status()
        self.assertEqual(body, {"unlocked": True, "expires_at": None, "expires_in_s": 600, "idle_limit_s": 600})

    def test_expires_after_600_seconds(self):
        self.t = T0 + 600
        self.assertTrue(self.status()["unlocked"])
        self.t = T0 + 600.001
        self.assertFalse(self.status()["unlocked"])
        self.assertEqual(self.c.get("/voyager-tools/api/state").status_code, 401)

    def test_polls_never_renew(self):
        for second in range(0, 600, 20):
            self.t = T0 + second
            self.status()
            self.c.get("/voyager-tools/fragment")
        self.t = T0 + 601
        self.assertFalse(self.status()["unlocked"])

    def test_renew_route(self):
        self.t = T0 + 500
        r = self.c.post("/api/admin/renew")
        self.assertEqual(r.status_code, 200)
        self.t = T0 + 1099
        self.assertTrue(self.status()["unlocked"])
        self.t = T0 + 1101
        self.assertEqual(self.c.post("/api/admin/renew").status_code, 401)

    def test_a_guarded_post_that_succeeds_renews(self):
        self.t = T0 + 590
        self.assertEqual(self.c.post("/probe").status_code, 200)
        self.assertTrue(self.seen["gated"])
        self.t = T0 + 1189
        self.assertTrue(self.status()["unlocked"])

    def test_a_refused_post_does_not_renew(self):
        self.t = T0 + 590
        self.assertEqual(self.c.post("/probe-fail").status_code, 409)
        self.t = T0 + 601
        self.assertFalse(self.status()["unlocked"])

    def test_cookie_without_admin_seen_is_cleared(self):
        with self.c.session_transaction() as sess:
            sess.pop("admin_seen")
        self.assertFalse(self.status()["unlocked"])
        with self.c.session_transaction() as sess:
            self.assertNotIn("admin_unlocked", sess)

    def test_lock_clears_both_flags(self):
        self.c.post("/api/admin/lock")
        with self.c.session_transaction() as sess:
            self.assertNotIn("admin_unlocked", sess)
            self.assertNotIn("admin_seen", sess)


if __name__ == "__main__":
    unittest.main()
