#!/usr/bin/env python3
"""
Module: tests/test_params_history.py
Purpose: Proves that no parameter default or branch save exists without its
         settings history line (decision 10 of the manual edit chain): the
         Save as default and Save as branch routes call deps.record_setting
         with the phase, the key, the old and new versions, the initials and
         the reason after the files are written and before the commit; when
         record_setting raises (the standalone host's default, or a history
         file that cannot be written) the new files are removed, the DEFAULT
         pointer is restored, nothing is committed, nothing stays pending,
         and the route answers 500 with the sentence.
Inputs:  a temporary voyagerparams checkout under real git (a throwaway
         identity), a bare Flask app with the blueprint and an admin session.
Outputs: unittest results; the temporary folder is removed in tearDown.

Run from the clone root:
    /usr/bin/python3 -m unittest tests.test_params_history -v
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from flask import Flask

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from voyagertools import paramsstore as ps  # noqa: E402
from voyagertools import views  # noqa: E402
from test_paramsstore import seed_checkout  # noqa: E402

HISTORY_FAILURE = "The settings history at /scratch/settings_history.csv cannot be written (disk full); nothing was saved."


def git(root: Path, *args: str) -> str:
    """Run git in the scratch checkout with a throwaway identity; return stdout."""
    done = subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
                          cwd=str(root), check=True, capture_output=True, text=True)
    return done.stdout


class ParamsHistoryCase(unittest.TestCase):
    """Every seam the params routes use, pointed at a real scratch checkout."""

    def setUp(self):
        if not shutil.which("git"):
            self.skipTest("git is not installed on this box")
        self.tmp = Path(tempfile.mkdtemp(prefix="vt-params-history-"))
        seed_checkout(self.tmp)
        git(self.tmp, "init", "-q")
        git(self.tmp, "add", "-A")
        git(self.tmp, "commit", "-q", "-m", "seed")
        self.store = ps.ParamsStore(self.tmp, now=lambda: "2026-09-27T10:00:00-04:00")
        self.recorded = []
        self._saved = {k: getattr(views.deps, k) for k in vars(views.deps)}
        views.deps.is_locked = lambda: False
        views.deps.is_admin = lambda: True
        views.deps.params_store = lambda: self.store
        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(views.voyager_tools_bp)
        self.client = app.test_client()

    def tearDown(self):
        for key, value in self._saved.items():
            setattr(views.deps, key, value)
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.assertFalse(self.tmp.exists())

    def record(self, *args):
        """A recording record_setting that answers the time it recorded."""
        self.recorded.append(args)
        return "2026-09-27T10:00:05-04:00"

    def values(self) -> dict:
        values = self.store.load("voyager1", "default").values
        values["processing"]["use_gpu"] = False
        return values

    def commits(self) -> int:
        return len(git(self.tmp, "log", "--oneline").splitlines())

    def save_default(self):
        return self.client.post("/voyager-tools/api/params/default", json={
            "phase": "voyager1", "values": self.values(), "description": "cpu only", "initials": "lo", "confirm": True})

    def save_branch(self, slug="cpu"):
        return self.client.post("/voyager-tools/api/params/branch", json={
            "phase": "voyager1", "values": self.values(), "slug": slug, "description": "cpu only",
            "why": "the cards are busy", "initials": "lo"})


class RecordedSaves(ParamsHistoryCase):
    def test_a_default_save_records_old_and_new_version_before_the_commit(self):
        order = []
        views.deps.record_setting = lambda *a: (order.append(("record", self.commits())), self.record(*a))[1]
        r = self.save_default()
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.recorded, [("voyager1", "voyager1/DEFAULT", "v1.0.0", "v1.1.0", "LO", "cpu only")])
        self.assertEqual(order, [("record", 1)], "the history line is written before the commit")
        self.assertEqual(self.commits(), 2)

    def test_a_branch_save_records_null_then_the_previous_branch_version(self):
        views.deps.record_setting = self.record
        self.assertEqual(self.save_branch().status_code, 200)
        values = self.values()
        values["processing"]["frames_per_transect"] = 900
        r = self.client.post("/voyager-tools/api/params/branch", json={
            "phase": "voyager1", "values": values, "slug": "cpu", "description": "fewer frames",
            "why": "speed", "initials": "LO"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.recorded, [
            ("voyager1", "voyager1/branches/cpu", None, "v1.0.0", "LO", "the cards are busy"),
            ("voyager1", "voyager1/branches/cpu", "v1.0.0", "v1.1.0", "LO", "speed")])


class RefusedSaves(ParamsHistoryCase):
    def test_the_default_record_setting_refuses_and_leaves_nothing(self):
        views.deps.record_setting = self._saved["record_setting"]
        r = self.save_default()
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.get_json()["error"], views.PARAMS_HISTORY_ONLY_ON_DESKTOP)
        self.assertEqual((self.tmp / "voyager1" / "DEFAULT").read_text().strip(), "v1.0.0")
        self.assertFalse((self.tmp / "voyager1" / "versions" / "v1.1.0.yaml").exists())
        self.assertEqual(self.store.pending_paths, [])
        self.assertEqual(self.commits(), 1)
        changed = [line for line in git(self.tmp, "status", "--porcelain").splitlines()
                   if not line.endswith(ps.LOCK_NAME)]
        self.assertEqual(changed, [], "only the store's own lock file may be left untracked")

    def test_a_history_failure_on_a_branch_removes_the_new_file(self):
        def broken(*args):
            raise RuntimeError(HISTORY_FAILURE)
        views.deps.record_setting = broken
        r = self.save_branch()
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.get_json()["error"], HISTORY_FAILURE)
        self.assertFalse((self.tmp / "voyager1" / "branches" / "cpu" / "v1.0.0.yaml").exists())
        self.assertEqual(self.store.pending_paths, [])
        self.assertEqual(self.commits(), 1)

    def test_an_older_failed_push_stays_pending_through_a_refused_save(self):
        views.deps.record_setting = self.record
        self.store.save_branch("voyager1", "default", "early", "d", "w", self.values(), "LO")
        early = self.store.pending_paths

        def broken(*args):
            raise RuntimeError(HISTORY_FAILURE)
        views.deps.record_setting = broken
        self.assertEqual(self.save_default().status_code, 500)
        self.assertEqual(self.store.pending_paths, early)
        self.assertTrue((self.tmp / early[0]).exists())


if __name__ == "__main__":
    unittest.main()
