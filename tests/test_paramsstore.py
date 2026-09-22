#!/usr/bin/env python3
"""
Module: tests/test_paramsstore.py
Purpose: Unit, adversarial and integration tests for voyagertools.paramsstore,
         the reader and writer of the voyagerparams checkout.
Inputs:  a temporary checkout per test, seeded from the compact fixture in
         this file or copied (never written) from the real checkout; a fake
         git runner that records calls and never touches git or the network.
Outputs: unittest results; every temporary directory is removed in tearDown.

Run from the clone root:
    /usr/bin/python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from voyagertools import paramsstore as ps  # noqa: E402

VICARIUS_ROOT = Path(os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius"))
REAL_CHECKOUT = VICARIUS_ROOT / "modules" / "voyagerparams" / "github_repo"
VOYAGER1_TEMPLATE = VICARIUS_ROOT / "modules" / "3D_phase_1" / "github_repo" / "analysis_params.yaml"

FIXED_NOW = "2026-09-04T01:00:00-04:00"

SEED_V1 = """# params_version: 1.0.0
# phase: voyager1
# kind: version
# description: seed for tests
# saved_at: 2026-09-04T00:00:00-04:00
# saved_by: LO
# Compact Voyager 1 template for tests; every key here is in the schema.

project:
  name: "base run" # overridden at launch
  notes: ""

processing:
  tcrmp: true # registry mode
  frames_per_transect: 1000 # frames step0 extracts per source video
  use_gpu: true
  metashape:
    defaults:
      downscale: 1 # photo downscale for matching; 1 = full resolution
      depth_filter_mode: "MildFiltering" # depth map filtering strength
  step1_products:
    texture_pixel_size: 0.0005 # target metres per texel
  model_processing:
    scale_bars:
      - start_marker: "target 1000"
        end_marker: "target 1010"
        distance: 0.75
    scale_error_threshold: 0.009 # maximum acceptable scale error in metres (9 mm)
"""

SEED_V2 = """# params_version: 0.1.0
# phase: voyager2
# kind: version
# status: draft
# description: seed for tests
# saved_at: 2026-09-04T00:00:00-04:00
# saved_by: LO
project:
  name: "base run"
processing:
  model_processing:
    ortho_tile_size: 1
"""


class FakeGit:
    """Records every git call and answers from a small script.

    `remotes` is the stdout of `git remote`; `fail` maps a git subcommand to
    a (returncode, stdout, stderr) triple; `raise_on` names a subcommand whose
    call raises OSError, the way a missing git binary would.
    """

    def __init__(self, remotes: str = "origin\n", fail: dict | None = None,
                 raise_on: str | None = None):
        self.calls: list[tuple[list[str], Path]] = []
        self.remotes = remotes
        self.fail = fail or {}
        self.raise_on = raise_on

    def __call__(self, args: list[str], cwd: Path) -> "ps.GitResult":
        self.calls.append((list(args), Path(cwd)))
        if self.raise_on and args[0] == self.raise_on:
            raise OSError("git: command not found")
        if args[0] in self.fail:
            code, out, err = self.fail[args[0]]
            return ps.GitResult(code, out, err)
        if args[0] == "remote":
            return ps.GitResult(0, self.remotes, "")
        if args[0] == "rev-parse":
            return ps.GitResult(0, "abc1234\n", "")
        return ps.GitResult(0, "", "")

    def subcommands(self) -> list[str]:
        return [call[0][0] for call in self.calls]


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def seed_checkout(root: Path) -> None:
    """Build the minimal two-phase layout the store expects."""
    write(root / "voyager1" / "versions" / "v1.0.0.yaml", SEED_V1)
    write(root / "voyager1" / "DEFAULT", "v1.0.0\n")
    (root / "voyager1" / "branches").mkdir()
    (root / "voyager1" / "custom").mkdir()
    # voyager2 deliberately has no branches/ or custom/ directory.
    write(root / "voyager2" / "versions" / "v0.1.0.yaml", SEED_V2)
    write(root / "voyager2" / "DEFAULT", "v0.1.0\n")


class _StoreCase(unittest.TestCase):
    """Shared fixture: a seeded temporary checkout, a fake git, a fixed clock."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="voyagerparams_test_"))
        seed_checkout(self.tmp)
        self.git = FakeGit()
        self.store = ps.ParamsStore(self.tmp, run_git=self.git, now=lambda: FIXED_NOW)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.assertFalse(self.tmp.exists())

    def values(self, ref: str = "default", phase: str = "voyager1") -> dict:
        return self.store.load(phase, ref).values

    def changed(self, **leaf) -> dict:
        """Default values with one or more dotted leaves replaced."""
        flat = ps.flatten(self.values())
        for path, value in leaf.items():
            flat[path.replace("__", ".")] = value
        return ps.unflatten(flat)


class PureHelperTests(unittest.TestCase):
    def test_flatten_and_unflatten_round_trip(self):
        nested = {"a": {"b": 1, "c": {"d": [1, 2]}}, "e": "x", "f": {}}
        flat = ps.flatten(nested)
        self.assertEqual(flat, {"a.b": 1, "a.c.d": [1, 2], "e": "x", "f": {}})
        self.assertEqual(ps.unflatten(flat), nested)

    def test_classify_change(self):
        base = {"a": 1, "b": "x"}
        self.assertIsNone(ps.classify_change(base, {"a": 1, "b": "x"}))
        self.assertEqual(ps.classify_change(base, {"a": 2, "b": "x"}), "minor")
        self.assertEqual(ps.classify_change(base, {"a": 1, "b": "x", "c": 0}), "major")
        self.assertEqual(ps.classify_change(base, {"a": 1}), "major")
        self.assertEqual(ps.classify_change(base, {"a": 1, "bb": "x"}), "major")

    def test_classify_change_type_change_is_a_value_change(self):
        self.assertEqual(ps.classify_change({"a": 1}, {"a": 1.0}), None)
        self.assertEqual(ps.classify_change({"a": 1}, {"a": True}), "minor")
        self.assertEqual(ps.classify_change({"a": "1"}, {"a": 1}), "minor")

    def test_next_version(self):
        self.assertEqual(ps.next_version("v1.2.3", "minor"), "v1.3.0")
        self.assertEqual(ps.next_version("v1.2.3", "major"), "v2.0.0")
        with self.assertRaises(ValueError):
            ps.next_version("v1.2.3", "patch")
        with self.assertRaises(ValueError):
            ps.next_version("1.2.3", "minor")

    def test_version_key_orders_numerically(self):
        refs = ["v1.9.0", "v1.10.0", "v2.0.0", "v0.1.0"]
        self.assertEqual(sorted(refs, key=ps.version_key), ["v0.1.0", "v1.9.0", "v1.10.0", "v2.0.0"])

    def test_parse_header_reads_known_keys_and_stops_at_prose(self):
        text = ("# params_version: 1.0.0\n# kind: version\n# description: a, b; \"c\" 'd' 3D\n"
                "# 3D_phase_1 template. Every key: read by config.py\n# saved_by: not-a-header\nproject: {}\n")
        header = ps.parse_header(text)
        self.assertEqual(header["params_version"], "1.0.0")
        self.assertEqual(header["kind"], "version")
        self.assertEqual(header["description"], "a, b; \"c\" 'd' 3D")
        self.assertNotIn("saved_by", header)

    def test_parse_header_empty_and_headerless(self):
        self.assertEqual(ps.parse_header(""), {})
        self.assertEqual(ps.parse_header("project: {}\n# kind: version\n"), {})

    def test_parse_ref_forms(self):
        self.assertEqual(ps.parse_ref("v1.0.0"), ("version", None, "v1.0.0"))
        self.assertEqual(ps.parse_ref("branches/hires/v1.2.0"), ("branch", "hires", "v1.2.0"))
        self.assertEqual(ps.parse_ref("branches/hires"), ("branch", "hires", None))
        self.assertEqual(ps.parse_ref("custom/TCRMP_3sep26_LO_MRS3_23ann-25pbl"),
                         ("custom", "TCRMP_3sep26_LO_MRS3_23ann-25pbl", None))
        self.assertEqual(ps.parse_ref("default"), ("default", None, None))

    def test_parse_ref_rejects_hostile_forms(self):
        for bad in ["", "v1", "V1.0.0", "v1.0.0.1", "../x", "versions/v1.0.0", "branches/../v1.0.0",
                    "branches/Hi res/v1.0.0", "branches//v1.0.0", "custom/a/b", "custom/..", "custom/",
                    "custom/a b", "v1.0.0\n", " v1.0.0", "branches/hires/v1", "custom/" + "x" * 200]:
            with self.assertRaises(ValueError, msg=bad):
                ps.parse_ref(bad)
        for wrong_type in [None, 3, b"v1.0.0", ["v1.0.0"]]:
            with self.assertRaises((TypeError, ValueError), msg=repr(wrong_type)):
                ps.parse_ref(wrong_type)

    def test_now_iso_is_atlantic_standard_time(self):
        self.assertRegex(ps.now_iso(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}-04:00$")


class ReadTests(_StoreCase):
    def test_default_version_reads_pointer(self):
        self.assertEqual(self.store.default_version("voyager1"), "v1.0.0")
        self.assertEqual(self.store.default_version("voyager2"), "v0.1.0")

    def test_default_version_tolerates_whitespace_in_pointer(self):
        write(self.tmp / "voyager1" / "DEFAULT", "  v1.0.0 \n\n")
        self.assertEqual(self.store.default_version("voyager1"), "v1.0.0")

    def test_default_version_missing_pointer_names_path(self):
        (self.tmp / "voyager1" / "DEFAULT").unlink()
        with self.assertRaises(FileNotFoundError) as ctx:
            self.store.default_version("voyager1")
        self.assertIn(str(self.tmp / "voyager1" / "DEFAULT"), str(ctx.exception))

    def test_default_version_malformed_pointer_rejected(self):
        for bad in ["banana\n", "", "v1.0.0\nv1.1.0\n", "../v1.0.0\n"]:
            write(self.tmp / "voyager1" / "DEFAULT", bad)
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.store.default_version("voyager1")

    def test_unknown_phase_rejected(self):
        for bad in ["voyager3", "", "../voyager1", "VOYAGER1"]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.store.default_version(bad)
        with self.assertRaises(ValueError):
            self.store.list_versions(None)

    def test_missing_phase_dir_named_in_error(self):
        shutil.rmtree(self.tmp / "voyager2")
        with self.assertRaises(FileNotFoundError) as ctx:
            self.store.list_versions("voyager2")
        self.assertIn("voyager2", str(ctx.exception))

    def test_load_default_and_explicit_ref_agree(self):
        by_default = self.store.load("voyager1", "default")
        by_ref = self.store.load("voyager1", "v1.0.0")
        self.assertEqual(by_default.values, by_ref.values)
        self.assertEqual(by_default.ref, "v1.0.0")
        self.assertEqual(by_default.version, "v1.0.0")
        self.assertEqual(by_default.kind, "version")
        self.assertEqual(by_default.phase, "voyager1")
        self.assertEqual(by_default.rel_path, "versions/v1.0.0.yaml")
        self.assertEqual(by_default.path, self.tmp / "voyager1" / "versions" / "v1.0.0.yaml")
        self.assertEqual(by_default.values["processing"]["frames_per_transect"], 1000)
        self.assertEqual(by_default.description, "seed for tests")
        self.assertEqual(by_default.saved_by, "LO")
        self.assertTrue(by_default.text.startswith("# params_version: 1.0.0\n"))

    def test_load_draft_status(self):
        draft = self.store.load("voyager2", "default")
        self.assertEqual(draft.status, "draft")
        self.assertEqual(self.store.load("voyager1", "default").status, "")

    def test_load_missing_ref_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            self.store.load("voyager1", "v9.9.9")
        self.assertIn("v9.9.9", str(ctx.exception))
        with self.assertRaises(FileNotFoundError):
            self.store.load("voyager1", "branches/nope")
        with self.assertRaises(FileNotFoundError):
            self.store.load("voyager1", "custom/nope")

    def test_load_dangling_default_pointer(self):
        write(self.tmp / "voyager1" / "DEFAULT", "v3.0.0\n")
        with self.assertRaises(FileNotFoundError):
            self.store.load("voyager1", "default")

    def test_load_empty_file_rejected(self):
        write(self.tmp / "voyager1" / "versions" / "v1.1.0.yaml", "")
        with self.assertRaises(ValueError) as ctx:
            self.store.load("voyager1", "v1.1.0")
        self.assertIn("v1.1.0.yaml", str(ctx.exception))

    def test_load_non_mapping_rejected(self):
        write(self.tmp / "voyager1" / "versions" / "v1.1.0.yaml", "- a\n- b\n")
        with self.assertRaises(ValueError):
            self.store.load("voyager1", "v1.1.0")
        write(self.tmp / "voyager1" / "versions" / "v1.1.0.yaml", "just a string\n")
        with self.assertRaises(ValueError):
            self.store.load("voyager1", "v1.1.0")

    def test_load_invalid_yaml_rejected_with_path(self):
        write(self.tmp / "voyager1" / "versions" / "v1.1.0.yaml", "project: [unclosed\n")
        with self.assertRaises(ValueError) as ctx:
            self.store.load("voyager1", "v1.1.0")
        self.assertIn("v1.1.0.yaml", str(ctx.exception))

    def test_load_duplicate_key_rejected(self):
        write(self.tmp / "voyager1" / "versions" / "v1.1.0.yaml",
              "processing:\n  use_gpu: true\n  use_gpu: false\n")
        with self.assertRaises(ValueError) as ctx:
            self.store.load("voyager1", "v1.1.0")
        self.assertIn("use_gpu", str(ctx.exception))

    def test_load_huge_file(self):
        big = SEED_V1.replace('notes: ""', 'notes: "' + "x" * 1_000_000 + '"')
        write(self.tmp / "voyager1" / "versions" / "v1.1.0.yaml", big)
        loaded = self.store.load("voyager1", "v1.1.0")
        self.assertEqual(len(loaded.values["project"]["notes"]), 1_000_000)

    def test_load_alias_bomb_stays_bounded(self):
        """PyYAML resolves an alias to the already built object, so a
        billion-laughs file loads as a small shared structure; the store must
        return in well under a second and see a plain mapping."""
        bomb = "a: &a [x, x, x, x, x, x, x, x, x, x]\n" + "".join(
            f"{chr(98 + i)}: &{chr(98 + i)} [*{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}]\n"
            for i in range(8))
        write(self.tmp / "voyager1" / "versions" / "v1.1.0.yaml", bomb)
        started = time.monotonic()
        loaded = self.store.load("voyager1", "v1.1.0")
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(sorted(loaded.values), list("abcdefghi"))

    def test_load_too_many_nodes_rejected(self):
        many = "bulk:\n" + "".join(f"  - {i}\n" for i in range(ps.MAX_YAML_NODES + 10))
        write(self.tmp / "voyager1" / "versions" / "v1.1.0.yaml", many)
        with self.assertRaises(ValueError) as ctx:
            self.store.load("voyager1", "v1.1.0")
        self.assertIn("nodes", str(ctx.exception))

    def test_load_oversized_file_rejected(self):
        path = self.tmp / "voyager1" / "versions" / "v1.1.0.yaml"
        write(path, "project: {}\n")
        with open(path, "ab") as fh:
            fh.truncate(ps.MAX_FILE_BYTES + 1)
        with self.assertRaises(ValueError) as ctx:
            self.store.load("voyager1", "v1.1.0")
        self.assertIn("bytes", str(ctx.exception))

    def test_load_unicode_and_hostile_strings_survive(self):
        text = SEED_V1.replace('notes: ""', 'notes: "café, \\"quoted\\"; semi; new\\nline 海"')
        write(self.tmp / "voyager1" / "versions" / "v1.1.0.yaml", text)
        loaded = self.store.load("voyager1", "v1.1.0")
        self.assertEqual(loaded.values["project"]["notes"], 'café, "quoted"; semi; new\nline 海')

    def test_list_versions_default_first_then_versions_branches_customs(self):
        write(self.tmp / "voyager1" / "versions" / "v1.2.0.yaml", SEED_V1.replace("1.0.0", "1.2.0"))
        write(self.tmp / "voyager1" / "versions" / "v1.10.0.yaml", SEED_V1.replace("1.0.0", "1.10.0"))
        write(self.tmp / "voyager1" / "branches" / "hires" / "v1.2.0.yaml",
              SEED_V1.replace("kind: version", "kind: branch\n# branch: hires"))
        write(self.tmp / "voyager1" / "branches" / "hires" / "v1.3.0.yaml",
              SEED_V1.replace("kind: version", "kind: branch\n# branch: hires"))
        write(self.tmp / "voyager1" / "branches" / "alpha" / "v1.0.0.yaml",
              SEED_V1.replace("kind: version", "kind: branch\n# branch: alpha"))
        write(self.tmp / "voyager1" / "custom" / "TCRMP_3sep26_LO_MRS3_23ann-25pbl.yaml",
              SEED_V1.replace("kind: version", "kind: custom"))
        write(self.tmp / "voyager1" / "DEFAULT", "v1.2.0\n")
        entries = self.store.list_versions("voyager1")
        refs = [e.ref for e in entries]
        self.assertEqual(refs, ["v1.2.0", "v1.10.0", "v1.0.0", "branches/alpha/v1.0.0",
                                "branches/hires/v1.3.0", "branches/hires/v1.2.0",
                                "custom/TCRMP_3sep26_LO_MRS3_23ann-25pbl"])
        self.assertEqual([e.is_default for e in entries], [True] + [False] * 6)
        self.assertEqual(entries[0].kind, "version")
        self.assertEqual(entries[3].kind, "branch")
        self.assertEqual(entries[3].branch, "alpha")
        self.assertEqual(entries[6].kind, "custom")
        self.assertEqual(entries[6].run_id, "TCRMP_3sep26_LO_MRS3_23ann-25pbl")
        self.assertEqual(entries[0].url,
                         "https://github.com/laurenkolinger/voyagerparams/blob/main/voyager1/versions/v1.2.0.yaml")
        self.assertEqual(entries[0].description, "seed for tests")
        self.assertEqual(entries[0].saved_by, "LO")

    def test_list_versions_tolerates_missing_branch_and_custom_dirs(self):
        entries = self.store.list_versions("voyager2")
        self.assertEqual([e.ref for e in entries], ["v0.1.0"])
        self.assertEqual(entries[0].status, "draft")

    def test_list_versions_ignores_badly_named_files(self):
        write(self.tmp / "voyager1" / "versions" / "notes.txt", "x")
        write(self.tmp / "voyager1" / "versions" / "v1.yaml", "x")
        write(self.tmp / "voyager1" / "versions" / ".gitkeep", "")
        write(self.tmp / "voyager1" / "branches" / ".gitkeep", "")
        write(self.tmp / "voyager1" / "branches" / "Bad Slug" / "v1.0.0.yaml", SEED_V1)
        write(self.tmp / "voyager1" / "custom" / "a b.yaml", SEED_V1)
        write(self.tmp / "voyager1" / "custom" / ".gitkeep", "")
        self.assertEqual([e.ref for e in self.store.list_versions("voyager1")], ["v1.0.0"])

    def test_list_versions_survives_unreadable_header(self):
        write(self.tmp / "voyager1" / "versions" / "v1.1.0.yaml", "\x00\x01 not yaml at all")
        entries = self.store.list_versions("voyager1")
        self.assertEqual([e.ref for e in entries], ["v1.0.0", "v1.1.0"])

    def test_version_url_forms(self):
        write(self.tmp / "voyager1" / "branches" / "hires" / "v1.0.0.yaml", SEED_V1)
        write(self.tmp / "voyager1" / "custom" / "RUN_1.yaml", SEED_V1)
        base = "https://github.com/laurenkolinger/voyagerparams/blob/main/voyager1/"
        self.assertEqual(self.store.version_url("voyager1", "v1.0.0"), base + "versions/v1.0.0.yaml")
        self.assertEqual(self.store.version_url("voyager1", "default"), base + "versions/v1.0.0.yaml")
        self.assertEqual(self.store.version_url("voyager1", "branches/hires/v1.0.0"),
                         base + "branches/hires/v1.0.0.yaml")
        self.assertEqual(self.store.version_url("voyager1", "branches/hires"),
                         base + "branches/hires/v1.0.0.yaml")
        self.assertEqual(self.store.version_url("voyager1", "custom/RUN_1"), base + "custom/RUN_1.yaml")

    def test_version_url_missing_file_rejected(self):
        with self.assertRaises(FileNotFoundError):
            self.store.version_url("voyager1", "v4.0.0")

    def test_version_url_uses_configured_repo(self):
        store = ps.ParamsStore(self.tmp, run_git=self.git, repo_url="https://example.org/r/")
        self.assertEqual(store.version_url("voyager1", "v1.0.0"),
                         "https://example.org/r/blob/main/voyager1/versions/v1.0.0.yaml")


class SaveDefaultTests(_StoreCase):
    def test_no_change_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self.store.save_default("voyager1", self.values(), "same", "LO")
        self.assertIn("nothing changed", str(ctx.exception).lower())
        self.assertEqual(self.store.default_version("voyager1"), "v1.0.0")

    def test_value_change_bumps_minor_and_keeps_comments(self):
        saved = self.store.save_default("voyager1", self.changed(processing__frames_per_transect=900),
                                        "fewer frames", "LO")
        self.assertEqual(saved.version, "v1.1.0")
        self.assertEqual(saved.ref, "v1.1.0")
        self.assertEqual(saved.kind, "version")
        self.assertEqual(self.store.default_version("voyager1"), "v1.1.0")
        text = (self.tmp / "voyager1" / "versions" / "v1.1.0.yaml").read_text(encoding="utf-8")
        self.assertIn("frames_per_transect: 900 # frames step0 extracts per source video", text)
        self.assertIn('name: "base run" # overridden at launch', text)
        self.assertIn("# Compact Voyager 1 template for tests", text)
        self.assertEqual(self.store.load("voyager1", "default").values["processing"]["frames_per_transect"], 900)
        # The old file is untouched.
        self.assertEqual(self.store.load("voyager1", "v1.0.0").values["processing"]["frames_per_transect"], 1000)

    def test_header_carries_actor_time_and_description(self):
        saved = self.store.save_default("voyager1", self.changed(processing__use_gpu=False), "cpu run", "LO")
        header = ps.parse_header(saved.text)
        self.assertEqual(header["params_version"], "1.1.0")
        self.assertEqual(header["phase"], "voyager1")
        self.assertEqual(header["kind"], "version")
        self.assertEqual(header["description"], "cpu run")
        self.assertEqual(header["saved_at"], FIXED_NOW)
        self.assertEqual(header["saved_by"], "LO")
        self.assertEqual(header["base_ref"], "v1.0.0")
        self.assertEqual(saved.text.count("# params_version:"), 1)

    def test_boolean_and_string_and_float_patches_render_as_yaml(self):
        values = self.changed(processing__use_gpu=False,
                              processing__metashape__defaults__depth_filter_mode="NoFiltering",
                              processing__step1_products__texture_pixel_size=0.00001)
        saved = self.store.save_default("voyager1", values, "types", "LO")
        self.assertIn("use_gpu: false", saved.text)
        self.assertIn('depth_filter_mode: "NoFiltering" # depth map filtering strength', saved.text)
        self.assertEqual(saved.values["processing"]["step1_products"]["texture_pixel_size"], 0.00001)
        self.assertIs(saved.values["processing"]["use_gpu"], False)

    def test_added_key_bumps_major(self):
        values = self.values()
        values["processing"]["new_flag"] = True
        saved = self.store.save_default("voyager1", values, "new key", "LO")
        self.assertEqual(saved.version, "v2.0.0")
        self.assertEqual(self.store.load("voyager1", "default").values["processing"]["new_flag"], True)

    def test_removed_key_bumps_major(self):
        values = self.values()
        del values["processing"]["use_gpu"]
        saved = self.store.save_default("voyager1", values, "drop key", "LO")
        self.assertEqual(saved.version, "v2.0.0")
        self.assertNotIn("use_gpu", saved.values["processing"])

    def test_renamed_key_bumps_major(self):
        values = self.values()
        values["processing"]["gpu"] = values["processing"].pop("use_gpu")
        self.assertEqual(self.store.save_default("voyager1", values, "rename", "LO").version, "v2.0.0")

    def test_successive_saves_chain(self):
        self.store.save_default("voyager1", self.changed(processing__frames_per_transect=900), "a", "LO")
        self.store.save_default("voyager1", self.changed(processing__frames_per_transect=800), "b", "LO")
        values = self.values()
        values["extra"] = {"k": 1}
        self.store.save_default("voyager1", values, "c", "LO")
        refs = [e.ref for e in self.store.list_versions("voyager1")]
        self.assertEqual(refs, ["v2.0.0", "v1.2.0", "v1.1.0", "v1.0.0"])

    def test_list_change_falls_back_to_clean_dump(self):
        values = self.values()
        values["processing"]["model_processing"]["scale_bars"].append(
            {"start_marker": "target 1020", "end_marker": "target 1030", "distance": 0.75})
        saved = self.store.save_default("voyager1", values, "two bars", "LO")
        self.assertEqual(saved.version, "v1.1.0")
        self.assertEqual(len(self.store.load("voyager1", "default").values["processing"]["model_processing"]["scale_bars"]), 2)
        self.assertTrue(saved.text.startswith("# params_version: 1.1.0\n"))

    def test_hostile_description_round_trips(self):
        description = 'a, b; "quoted" \'single\' #hash\nsecond line\r\nthird café 海 \t tab'
        saved = self.store.save_default("voyager1", self.changed(processing__use_gpu=False), description, "LO")
        loaded = self.store.load("voyager1", "default")
        self.assertEqual(loaded.description, 'a, b; "quoted" \'single\' #hash second line third café 海 tab')
        self.assertEqual(saved.text.count("\n# "), saved.text.count("\n#"))

    def test_bad_actor_rejected(self):
        for bad in ["", " ", "L O", "LO\n", "x" * 41, None, 7]:
            with self.assertRaises((ValueError, TypeError), msg=repr(bad)):
                self.store.save_default("voyager1", self.changed(processing__use_gpu=False), "d", bad)
        self.assertEqual(self.store.default_version("voyager1"), "v1.0.0")

    def test_bad_description_rejected(self):
        for bad in [None, 7, "x" * 2001]:
            with self.assertRaises((ValueError, TypeError), msg=repr(bad)):
                self.store.save_default("voyager1", self.changed(processing__use_gpu=False), bad, "LO")

    def test_empty_description_allowed(self):
        saved = self.store.save_default("voyager1", self.changed(processing__use_gpu=False), "", "LO")
        self.assertEqual(saved.description, "")

    def test_type_violation_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.store.save_default("voyager1", self.changed(processing__frames_per_transect="many"), "d", "LO")
        self.assertIn("processing.frames_per_transect", str(ctx.exception))
        with self.assertRaises(ValueError):
            self.store.save_default("voyager1", self.changed(processing__use_gpu="yes"), "d", "LO")
        with self.assertRaises(ValueError):
            self.store.save_default("voyager1", self.changed(processing__frames_per_transect=True), "d", "LO")

    def test_range_violation_rejected(self):
        with self.assertRaises(ValueError):
            self.store.save_default("voyager1", self.changed(processing__frames_per_transect=-1), "d", "LO")
        with self.assertRaises(ValueError):
            self.store.save_default("voyager1", self.changed(processing__model_processing__scale_error_threshold=0), "d", "LO")

    def test_choice_violation_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.store.save_default("voyager1", self.changed(processing__metashape__defaults__downscale=3), "d", "LO")
        self.assertIn("downscale", str(ctx.exception))
        with self.assertRaises(ValueError):
            self.store.save_default("voyager1", self.changed(processing__metashape__defaults__depth_filter_mode="Mild"), "d", "LO")

    def test_values_not_mapping_rejected(self):
        for bad in [None, [], "x", 3, {"a"}]:
            with self.assertRaises((TypeError, ValueError), msg=repr(bad)):
                self.store.save_default("voyager1", bad, "d", "LO")

    def test_hostile_key_names_rejected(self):
        for bad_key in ["a b", "a:b", "a\nb", "", "../x", "a.b", "a/b", "kéy", "a#b"]:
            values = self.values()
            values[bad_key] = 1
            with self.assertRaises(ValueError, msg=repr(bad_key)):
                self.store.save_default("voyager1", values, "d", "LO")
        values = self.values()
        values[3] = 1
        with self.assertRaises(ValueError):
            self.store.save_default("voyager1", values, "d", "LO")

    def test_too_deep_rejected(self):
        values = self.values()
        node = values
        for i in range(ps.MAX_DEPTH + 1):
            node["d%d" % i] = {}
            node = node["d%d" % i]
        node["leaf"] = 1
        with self.assertRaises(ValueError):
            self.store.save_default("voyager1", values, "d", "LO")

    def test_too_many_keys_rejected(self):
        values = self.values()
        values["bulk"] = {"k%d" % i: i for i in range(ps.MAX_LEAVES + 1)}
        with self.assertRaises(ValueError):
            self.store.save_default("voyager1", values, "d", "LO")

    def test_too_long_string_rejected(self):
        with self.assertRaises(ValueError):
            self.store.save_default("voyager1", self.changed(project__notes="x" * (ps.MAX_VALUE_CHARS + 1)), "d", "LO")

    def test_unsupported_value_type_rejected(self):
        with self.assertRaises(ValueError):
            self.store.save_default("voyager1", self.changed(project__notes=object()), "d", "LO")
        with self.assertRaises(ValueError):
            self.store.save_default("voyager1", self.changed(project__notes={1, 2}), "d", "LO")

    def test_huge_list_leaf_rejected_quickly(self):
        """A list-typed leaf is one leaf regardless of size, so MAX_LEAVES
        never bounds it; before this fix a multi-million-item list reached
        yaml.safe_dump inside _check_leaf and hung for minutes. The item
        count must be checked, and must be checked before any dump is
        attempted, so a save call returns well under a second."""
        bomb = [{"start_marker": "x", "end_marker": "y", "distance": 0.1} for _ in range(3_000_000)]
        started = time.monotonic()
        with self.assertRaises(ValueError) as ctx:
            self.store.save_default("voyager1", self.changed(processing__model_processing__scale_bars=bomb),
                                    "d", "LO")
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertIn("more than", str(ctx.exception))
        self.assertIn("scale_bars", str(ctx.exception))
        self.assertEqual(self.store.default_version("voyager1"), "v1.0.0")

    def test_list_leaf_over_dump_size_rejected(self):
        """Within the item-count bound, a list whose dumped text is still
        oversized (many items, each with a long field) is rejected on its
        rendered size rather than written to disk."""
        bars = [{"start_marker": "m" * 300, "end_marker": "n" * 300, "distance": 0.1} for _ in range(500)]
        with self.assertRaises(ValueError) as ctx:
            self.store.save_default("voyager1", self.changed(processing__model_processing__scale_bars=bars),
                                    "d", "LO")
        self.assertIn("serialises to more than", str(ctx.exception))

    def test_list_leaf_within_bounds_accepted(self):
        bars = [{"start_marker": "target %d" % i, "end_marker": "target %d" % (i + 1), "distance": 0.75}
                for i in range(20)]
        saved = self.store.save_default("voyager1", self.changed(processing__model_processing__scale_bars=bars),
                                        "d", "LO")
        self.assertEqual(len(saved.values["processing"]["model_processing"]["scale_bars"]), 20)

    def test_no_temp_files_left_after_save(self):
        self.store.save_default("voyager1", self.changed(processing__use_gpu=False), "d", "LO")
        names = sorted(p.name for p in (self.tmp / "voyager1").rglob("*") if p.is_file())
        self.assertEqual(names, ["DEFAULT", "v1.0.0.yaml", "v1.1.0.yaml"])
        root_files = sorted(p.name for p in self.tmp.iterdir() if p.is_file())
        self.assertEqual(root_files, [ps.LOCK_NAME])

    def test_trailing_newline_in_names_rejected(self):
        with self.assertRaises(ValueError):
            self.store.save_custom("voyager1", "RUN_A\n", self.values(), "LO")
        with self.assertRaises(ValueError):
            self.store.save_branch("voyager1", "default", "hires\n", "d", "w", self.values(), "LO")
        with self.assertRaises(ValueError):
            self.store.load("voyager1", "v1.0.0\n")
        values = self.values()
        values["processing\n"] = 1
        with self.assertRaises(ValueError):
            self.store.save_default("voyager1", values, "d", "LO")

    def test_pending_paths_recorded(self):
        self.store.save_default("voyager1", self.changed(processing__use_gpu=False), "d", "LO")
        self.assertEqual(self.store.pending_paths, ["voyager1/versions/v1.1.0.yaml", "voyager1/DEFAULT"])

    def test_refused_while_lock_held(self):
        lock_path = self.tmp / ps.LOCK_NAME
        lock_path.touch()
        holder = open(lock_path, "w")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            store = ps.ParamsStore(self.tmp, run_git=self.git, now=lambda: FIXED_NOW, lock_wait_s=0.2)
            with self.assertRaises(ps.StoreLocked) as ctx:
                store.save_default("voyager1", self.changed(processing__use_gpu=False), "d", "LO")
            self.assertIn(str(lock_path), str(ctx.exception))
        finally:
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()
        self.assertEqual(self.store.default_version("voyager1"), "v1.0.0")

    def test_concurrent_saves_serialise(self):
        errors: list[BaseException] = []

        def worker(frames: int):
            try:
                store = ps.ParamsStore(self.tmp, run_git=FakeGit(), now=lambda: FIXED_NOW)
                flat = ps.flatten(store.load("voyager1", "default").values)
                flat["processing.frames_per_transect"] = frames
                flat["project.notes"] = "worker %d" % frames
                store.save_default("voyager1", ps.unflatten(flat), "worker", "LO")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in (900, 800, 700, 600)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        refs = [e.ref for e in self.store.list_versions("voyager1")]
        self.assertEqual(refs, ["v1.4.0", "v1.3.0", "v1.2.0", "v1.1.0", "v1.0.0"])
        self.assertEqual(self.store.default_version("voyager1"), "v1.4.0")

    def test_save_on_phase_without_schema_only_checks_shape(self):
        values = self.store.load("voyager2", "default").values
        values["processing"]["model_processing"]["ortho_tile_size"] = "any string is fine here"
        saved = self.store.save_default("voyager2", values, "draft edit", "LO")
        self.assertEqual(saved.version, "v0.2.0")
        self.assertEqual(saved.status, "")


class SaveBranchTests(_StoreCase):
    def test_first_save_uses_base_version_and_records_branch(self):
        saved = self.store.save_branch("voyager1", "default", "hires", "high resolution", "why not",
                                       self.changed(processing__frames_per_transect=2000), "LO")
        self.assertEqual(saved.ref, "branches/hires/v1.0.0")
        self.assertEqual(saved.version, "v1.0.0")
        self.assertEqual(saved.kind, "branch")
        self.assertEqual(saved.branch, "hires")
        self.assertEqual(saved.base_ref, "v1.0.0")
        self.assertEqual(saved.why, "why not")
        self.assertEqual(saved.description, "high resolution")
        self.assertEqual(saved.rel_path, "branches/hires/v1.0.0.yaml")
        self.assertEqual(self.store.default_version("voyager1"), "v1.0.0")
        self.assertEqual(self.store.load("voyager1", "branches/hires").values["processing"]["frames_per_transect"], 2000)
        self.assertEqual(self.store.pending_paths, ["voyager1/branches/hires/v1.0.0.yaml"])

    def test_branch_with_same_values_as_base_is_allowed(self):
        saved = self.store.save_branch("voyager1", "v1.0.0", "copy", "same values", "for later", self.values(), "LO")
        self.assertEqual(saved.ref, "branches/copy/v1.0.0")

    def test_second_save_bumps_within_branch(self):
        self.store.save_branch("voyager1", "default", "hires", "d", "w", self.changed(processing__frames_per_transect=2000), "LO")
        second = self.store.save_branch("voyager1", "default", "hires", "d", "w", self.changed(processing__frames_per_transect=3000), "LO")
        self.assertEqual(second.ref, "branches/hires/v1.1.0")
        values = self.values()
        values["processing"]["extra"] = 1
        third = self.store.save_branch("voyager1", "default", "hires", "d", "w", values, "LO")
        self.assertEqual(third.ref, "branches/hires/v2.0.0")
        self.assertEqual(self.store.load("voyager1", "branches/hires").ref, "branches/hires/v2.0.0")

    def test_no_change_on_existing_branch_refused(self):
        self.store.save_branch("voyager1", "default", "hires", "d", "w", self.changed(processing__frames_per_transect=2000), "LO")
        with self.assertRaises(ValueError):
            self.store.save_branch("voyager1", "default", "hires", "d", "w", self.changed(processing__frames_per_transect=2000), "LO")

    def test_bad_slug_rejected(self):
        for bad in ["Hi Res", "../x", "", "a" * 41, "UPPER", "a/b", "-lead", "slüg", None, 5]:
            with self.assertRaises((ValueError, TypeError), msg=repr(bad)):
                self.store.save_branch("voyager1", "default", bad, "d", "w", self.values(), "LO")

    def test_missing_base_ref_rejected(self):
        with self.assertRaises(FileNotFoundError):
            self.store.save_branch("voyager1", "v7.0.0", "hires", "d", "w", self.values(), "LO")
        with self.assertRaises(ValueError):
            self.store.save_branch("voyager1", "../x", "hires", "d", "w", self.values(), "LO")

    def test_branch_from_another_branch(self):
        self.store.save_branch("voyager1", "default", "hires", "d", "w", self.changed(processing__frames_per_transect=2000), "LO")
        saved = self.store.save_branch("voyager1", "branches/hires", "hires2", "d", "w",
                                       self.changed(processing__frames_per_transect=2500), "LO")
        self.assertEqual(saved.base_ref, "branches/hires/v1.0.0")
        self.assertEqual(saved.ref, "branches/hires2/v1.0.0")

    def test_branch_validation_and_why_required_types(self):
        with self.assertRaises((ValueError, TypeError)):
            self.store.save_branch("voyager1", "default", "hires", "d", None, self.values(), "LO")
        with self.assertRaises(ValueError):
            self.store.save_branch("voyager1", "default", "hires", "d", "w", self.changed(processing__frames_per_transect=-5), "LO")


class SaveCustomTests(_StoreCase):
    def test_writes_file_with_run_id(self):
        run_id = "TCRMP_3sep26_LO_MRS3+LBH2_23ann-25pbl"
        saved = self.store.save_custom("voyager1", run_id, self.changed(processing__frames_per_transect=500), "LO")
        self.assertEqual(saved.ref, "custom/" + run_id)
        self.assertEqual(saved.kind, "custom")
        self.assertEqual(saved.run_id, run_id)
        self.assertEqual(saved.version, "v1.0.0")
        self.assertEqual(saved.base_ref, "v1.0.0")
        self.assertEqual(saved.rel_path, "custom/%s.yaml" % run_id)
        self.assertTrue((self.tmp / "voyager1" / "custom" / (run_id + ".yaml")).exists())
        self.assertEqual(self.store.load("voyager1", saved.ref).values["processing"]["frames_per_transect"], 500)
        self.assertEqual(self.store.default_version("voyager1"), "v1.0.0")
        self.assertEqual(self.store.pending_paths, ["voyager1/custom/%s.yaml" % run_id])

    def test_custom_same_as_default_is_allowed(self):
        saved = self.store.save_custom("voyager1", "RUN_A", self.values(), "LO", description="unchanged default")
        self.assertEqual(saved.description, "unchanged default")

    def test_existing_refused_unless_overwrite(self):
        self.store.save_custom("voyager1", "RUN_A", self.values(), "LO")
        with self.assertRaises(FileExistsError):
            self.store.save_custom("voyager1", "RUN_A", self.changed(processing__use_gpu=False), "LO")
        saved = self.store.save_custom("voyager1", "RUN_A", self.changed(processing__use_gpu=False), "LO", overwrite=True)
        self.assertIs(saved.values["processing"]["use_gpu"], False)

    def test_bad_run_id_rejected(self):
        for bad in ["../x", "a/b", "", "x" * 121, "run id", ".hidden", "a\nb", None, 4]:
            with self.assertRaises((ValueError, TypeError), msg=repr(bad)):
                self.store.save_custom("voyager1", bad, self.values(), "LO")

    def test_custom_from_a_branch_records_base(self):
        self.store.save_branch("voyager1", "default", "hires", "d", "w", self.changed(processing__frames_per_transect=2000), "LO")
        saved = self.store.save_custom("voyager1", "RUN_B", self.changed(processing__frames_per_transect=2000), "LO",
                                       base_ref="branches/hires")
        self.assertEqual(saved.base_ref, "branches/hires/v1.0.0")

    def test_custom_validates_values(self):
        with self.assertRaises(ValueError):
            self.store.save_custom("voyager1", "RUN_C", self.changed(processing__metashape__defaults__downscale=7), "LO")


class DiffTests(_StoreCase):
    def test_diff_between_refs(self):
        self.store.save_default("voyager1", self.changed(processing__frames_per_transect=900), "d", "LO")
        rows = self.store.diff("v1.0.0", "v1.1.0", phase="voyager1")
        self.assertEqual(rows, [{"path": "processing.frames_per_transect", "kind": "changed", "a": 1000, "b": 900}])

    def test_diff_ref_vs_mapping_and_kinds_sorted(self):
        values = self.values()
        values["processing"]["zeta"] = 1
        del values["processing"]["use_gpu"]
        values["project"]["name"] = "other"
        rows = self.store.diff("default", values, phase="voyager1")
        self.assertEqual([r["path"] for r in rows], ["processing.use_gpu", "processing.zeta", "project.name"])
        self.assertEqual([r["kind"] for r in rows], ["removed", "added", "changed"])
        self.assertEqual(rows[0]["a"], True)
        self.assertIsNone(rows[0]["b"])
        self.assertIsNone(rows[1]["a"])

    def test_diff_identical_is_empty(self):
        self.assertEqual(self.store.diff("v1.0.0", "default", phase="voyager1"), [])
        self.assertEqual(self.store.diff(self.values(), self.values()), [])

    def test_diff_rejects_bad_inputs(self):
        with self.assertRaises((TypeError, ValueError)):
            self.store.diff(3, self.values())
        with self.assertRaises(FileNotFoundError):
            self.store.diff("v5.0.0", "v1.0.0", phase="voyager1")

    def test_diff_lists_compare_whole(self):
        values = self.values()
        values["processing"]["model_processing"]["scale_bars"][0]["distance"] = 1.0
        rows = self.store.diff("default", values, phase="voyager1")
        self.assertEqual([r["path"] for r in rows], ["processing.model_processing.scale_bars"])


class CommitAndPushTests(_StoreCase):
    def test_nothing_pending(self):
        result = self.store.commit_and_push("nothing")
        self.assertEqual(result, {"committed": False, "pushed": False, "commit": "", "paths": [],
                                  "detail": "nothing to commit"})
        self.assertEqual(self.git.calls, [])

    def test_stages_only_written_paths_commits_and_pushes(self):
        self.store.save_default("voyager1", self.changed(processing__use_gpu=False), "d", "LO")
        result = self.store.commit_and_push("chore: bump voyager1 default to v1.1.0")
        self.assertEqual(result["committed"], True)
        self.assertEqual(result["pushed"], True)
        self.assertEqual(result["commit"], "abc1234")
        self.assertEqual(result["paths"], ["voyager1/versions/v1.1.0.yaml", "voyager1/DEFAULT"])
        subs = self.git.subcommands()
        self.assertEqual(subs, ["add", "commit", "rev-parse", "remote", "push"])
        add_args = self.git.calls[0][0]
        self.assertEqual(add_args, ["add", "--", "voyager1/versions/v1.1.0.yaml", "voyager1/DEFAULT"])
        self.assertNotIn("-A", add_args)
        self.assertEqual(self.git.calls[1][0][:3], ["commit", "-m", "chore: bump voyager1 default to v1.1.0"])
        self.assertEqual(self.git.calls[4][0], ["push", "origin", "HEAD"])
        self.assertTrue(all(cwd == self.tmp for _, cwd in self.git.calls))
        self.assertEqual(self.store.pending_paths, [])

    def test_explicit_paths_override_pending(self):
        self.store.save_default("voyager1", self.changed(processing__use_gpu=False), "d", "LO")
        result = self.store.commit_and_push("msg", paths=["CHANGELOG.md"])
        self.assertEqual(self.git.calls[0][0], ["add", "--", "CHANGELOG.md"])
        self.assertEqual(result["paths"], ["CHANGELOG.md"])
        self.assertEqual(self.store.pending_paths, ["voyager1/versions/v1.1.0.yaml", "voyager1/DEFAULT"])

    def test_explicit_paths_validated(self):
        for bad in [["../x"], ["/etc/passwd"], [""], "CHANGELOG.md", [3]]:
            with self.assertRaises((ValueError, TypeError), msg=repr(bad)):
                self.store.commit_and_push("msg", paths=bad)
        self.assertEqual(self.git.calls, [])

    def test_without_remote_skips_push(self):
        git = FakeGit(remotes="")
        store = ps.ParamsStore(self.tmp, run_git=git, now=lambda: FIXED_NOW)
        store.save_custom("voyager1", "RUN_A", self.values(), "LO")
        result = store.commit_and_push("msg")
        self.assertTrue(result["committed"])
        self.assertFalse(result["pushed"])
        self.assertIn("no remote", result["detail"])
        self.assertNotIn("push", git.subcommands())

    def test_push_failure_reported_not_raised(self):
        git = FakeGit(fail={"push": (128, "", "fatal: unable to access: Could not resolve host")})
        store = ps.ParamsStore(self.tmp, run_git=git, now=lambda: FIXED_NOW)
        store.save_custom("voyager1", "RUN_A", self.values(), "LO")
        result = store.commit_and_push("msg")
        self.assertTrue(result["committed"])
        self.assertFalse(result["pushed"])
        self.assertIn("Could not resolve host", result["detail"])
        self.assertEqual(store.pending_paths, [])

    def test_commit_failure_raises_git_error_and_keeps_pending(self):
        git = FakeGit(fail={"commit": (1, "", "fatal: empty ident name")})
        store = ps.ParamsStore(self.tmp, run_git=git, now=lambda: FIXED_NOW)
        store.save_custom("voyager1", "RUN_A", self.values(), "LO")
        with self.assertRaises(ps.GitError) as ctx:
            store.commit_and_push("msg")
        self.assertIn("empty ident name", str(ctx.exception))
        self.assertEqual(store.pending_paths, ["voyager1/custom/RUN_A.yaml"])

    def test_runner_exception_wrapped(self):
        git = FakeGit(raise_on="add")
        store = ps.ParamsStore(self.tmp, run_git=git, now=lambda: FIXED_NOW)
        store.save_custom("voyager1", "RUN_A", self.values(), "LO")
        with self.assertRaises(ps.GitError):
            store.commit_and_push("msg")

    def test_empty_message_rejected(self):
        self.store.save_custom("voyager1", "RUN_A", self.values(), "LO")
        for bad in ["", "   ", None, 3]:
            with self.assertRaises((ValueError, TypeError), msg=repr(bad)):
                self.store.commit_and_push(bad)
        self.assertEqual(self.git.calls, [])

    def test_subprocess_runner_shape(self):
        result = ps.run_git_subprocess(["--version"], self.tmp)
        self.assertIsInstance(result, ps.GitResult)
        self.assertEqual(result.returncode, 0)
        self.assertIn("git version", result.stdout)


class DefaultRootTests(unittest.TestCase):
    def test_default_root_follows_vicarius_root(self):
        old = os.environ.get("VICARIUS_ROOT")
        os.environ["VICARIUS_ROOT"] = "/tmp/fake_root"
        try:
            self.assertEqual(ps.default_checkout(), Path("/tmp/fake_root/modules/voyagerparams/github_repo"))
        finally:
            if old is None:
                os.environ.pop("VICARIUS_ROOT", None)
            else:
                os.environ["VICARIUS_ROOT"] = old

    def test_store_rejects_missing_root(self):
        with self.assertRaises(FileNotFoundError):
            ps.ParamsStore("/nonexistent/voyagerparams", run_git=FakeGit())


class SchemaTests(unittest.TestCase):
    """The Voyager 1 editor schema against the rules of the task and the
    template it describes."""

    SENTENCE_END = re.compile(r"[.!?](\s|$)")

    @classmethod
    def setUpClass(cls):
        cls.schema = ps.load_schema("voyager1")
        cls.keys = {entry["path"]: entry for entry in cls.schema["keys"]}
        cls.sections = {section["id"]: section for section in cls.schema["sections"]}

    def test_schema_loads_and_has_shape(self):
        self.assertEqual(self.schema["phase"], "voyager1")
        self.assertTrue(self.schema["keys"])
        self.assertTrue(self.schema["sections"])
        self.assertIsNone(ps.load_schema("voyager2"))
        with self.assertRaises(ValueError):
            ps.load_schema("../voyager1")

    def test_every_entry_well_formed(self):
        paths = [entry["path"] for entry in self.schema["keys"]]
        self.assertEqual(len(paths), len(set(paths)), "duplicate paths")
        labels = [entry["label"] for entry in self.schema["keys"]]
        self.assertEqual(len(labels), len(set(labels)), "duplicate labels")
        for entry in self.schema["keys"]:
            for field in ("path", "type", "default", "label", "tooltip", "section"):
                self.assertIn(field, entry, entry.get("path"))
            self.assertIn(entry["type"], ps.SCHEMA_TYPES, entry["path"])
            self.assertIn(entry["section"], self.sections, entry["path"])
            self.assertRegex(entry["path"], r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
            for extra in entry:
                self.assertIn(extra, ps.SCHEMA_ENTRY_FIELDS, "%s: unknown field %s" % (entry["path"], extra))
            self.assertEqual(ps.check_value(entry, entry["default"]), [], entry["path"])
            if "choices" in entry:
                self.assertIn(entry["default"], entry["choices"], entry["path"])
                self.assertEqual(len(entry["choices"]), len(set(map(str, entry["choices"]))))
            if "min" in entry and "max" in entry:
                self.assertLess(entry["min"], entry["max"], entry["path"])

    def test_hidden_only_on_max_chunks_per_psx(self):
        hidden = sorted(path for path, entry in self.keys.items() if entry.get("hidden"))
        self.assertEqual(hidden, ["processing.max_chunks_per_psx"])
        self.assertIs(self.keys["processing.max_chunks_per_psx"]["hidden"], True)

    def test_tooltips_follow_the_sentence_rules(self):
        banned = re.compile(r"\b" + "la" "nd" + r"(s|ed|ing)?\b", re.IGNORECASE)
        for entry in self.schema["keys"] + self.schema["sections"]:
            tip = entry["tooltip"]
            name = entry.get("path") or entry["id"]
            self.assertTrue(tip.strip(), name)
            self.assertTrue(tip.endswith("."), name)
            self.assertFalse(tip.startswith("You"), name)
            self.assertNotIn(chr(0x2014), tip, name)
            self.assertNotIn(chr(0x2013), tip, name)
            self.assertIsNone(banned.search(tip), name)
            self.assertLessEqual(len(self.SENTENCE_END.findall(tip)), 2, "%s: more than two sentences" % name)
            self.assertLessEqual(len(tip), 420, name)
            for screen_word in ("above", "below", "this tab", "this page"):
                self.assertNotIn(" " + screen_word + " ", " " + tip.lower() + " ", "%s: screen reference" % name)
            self.assertNotIn("config.py", tip, name)

    def test_types_match_defaults(self):
        for entry in self.schema["keys"]:
            default = entry["default"]
            kind = entry["type"]
            if kind == "integer":
                self.assertIsInstance(default, int, entry["path"])
                self.assertNotIsInstance(default, bool, entry["path"])
            elif kind == "number":
                self.assertIsInstance(default, (int, float), entry["path"])
                self.assertNotIsInstance(default, bool, entry["path"])
            elif kind == "boolean":
                self.assertIsInstance(default, bool, entry["path"])
            elif kind == "string":
                self.assertIsInstance(default, str, entry["path"])
            elif kind == "list":
                self.assertIsInstance(default, list, entry["path"])

    def test_check_value_rules(self):
        entry = {"path": "x", "type": "integer", "default": 1, "min": 0}
        self.assertEqual(ps.check_value(entry, 5), [])
        self.assertTrue(ps.check_value(entry, -1))
        self.assertTrue(ps.check_value(entry, True))
        self.assertTrue(ps.check_value(entry, 1.5))
        self.assertTrue(ps.check_value(entry, "1"))
        entry = {"path": "x", "type": "number", "default": 0.5, "min": 0, "min_exclusive": True, "max": 1}
        self.assertEqual(ps.check_value(entry, 0.25), [])
        self.assertEqual(ps.check_value(entry, 1), [])
        self.assertTrue(ps.check_value(entry, 0))
        self.assertTrue(ps.check_value(entry, 1.01))
        entry = {"path": "x", "type": "string", "default": "a", "choices": ["a", "b"]}
        self.assertTrue(ps.check_value(entry, "c"))
        self.assertTrue(ps.check_value(entry, 1))
        entry = {"path": "x", "type": "boolean", "default": True}
        self.assertTrue(ps.check_value(entry, "true"))
        self.assertTrue(ps.check_value(entry, 1))
        entry = {"path": "x", "type": "list", "default": []}
        self.assertTrue(ps.check_value(entry, "a,b"))
        self.assertEqual(ps.check_value(entry, [1]), [])

    @unittest.skipUnless(VOYAGER1_TEMPLATE.exists(), "Voyager 1 template not present")
    def test_schema_covers_every_template_key_exactly(self):
        template = yaml.safe_load(VOYAGER1_TEMPLATE.read_text(encoding="utf-8"))
        flat = ps.flatten(template)
        self.assertEqual(sorted(flat), sorted(self.keys))
        for path, value in flat.items():
            self.assertEqual(self.keys[path]["default"], value, path)

    @unittest.skipUnless(VOYAGER1_TEMPLATE.exists(), "Voyager 1 template not present")
    def test_template_validates_clean(self):
        template = yaml.safe_load(VOYAGER1_TEMPLATE.read_text(encoding="utf-8"))
        self.assertEqual(ps.validate_values("voyager1", template), [])


@unittest.skipUnless(REAL_CHECKOUT.exists(), "voyagerparams checkout not present")
class RealCheckoutTests(unittest.TestCase):
    """Read-only checks of the seeded checkout, run against a copy so no test
    can write into the real repository."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="voyagerparams_copy_"))
        shutil.copytree(REAL_CHECKOUT, self.tmp / "checkout", ignore=shutil.ignore_patterns(".git", "__pycache__"))
        self.store = ps.ParamsStore(self.tmp / "checkout", run_git=FakeGit(), now=lambda: FIXED_NOW)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_seed_layout(self):
        root = self.tmp / "checkout"
        self.assertEqual(self.store.default_version("voyager1"), "v1.0.0")
        self.assertEqual(self.store.default_version("voyager2"), "v0.1.0")
        for rel in ("voyager1/branches/.gitkeep", "voyager1/custom/.gitkeep", "voyager2/branches/.gitkeep",
                    "voyager2/custom/.gitkeep", "README.md", "CHANGELOG.md", "module.yaml", "CLAUDE.md", "CONTEXT.md"):
            self.assertTrue((root / rel).exists(), rel)

    def test_list_default_first(self):
        entries = self.store.list_versions("voyager1")
        self.assertEqual(entries[0].ref, "v1.0.0")
        self.assertTrue(entries[0].is_default)
        self.assertEqual(entries[0].saved_by, "LO")
        self.assertTrue(entries[0].description)

    @unittest.skipUnless(VOYAGER1_TEMPLATE.exists(), "Voyager 1 template not present")
    def test_voyager1_default_equals_module_template(self):
        seeded = self.store.load("voyager1", "default")
        template = yaml.safe_load(VOYAGER1_TEMPLATE.read_text(encoding="utf-8"))
        self.assertEqual(seeded.values, template)
        self.assertEqual(seeded.version, "v1.0.0")
        self.assertEqual(ps.parse_header(seeded.text)["params_version"], "1.0.0")
        self.assertIn("# 3D_phase_1 v1.0.0 analysis parameters.", seeded.text)

    def test_voyager2_draft_carries_no_secret(self):
        draft = self.store.load("voyager2", "default")
        self.assertEqual(draft.status, "draft")
        self.assertEqual(draft.values["processing"]["final_exports"]["sketchfab"]["token"], "")
        self.assertNotIn("152b6186", draft.text)
        self.assertEqual(draft.values["processing"]["chunk_management"]["chunk_quality"],
                         {"min_cameras": 0, "min_alignment_percentage": 0})

    def test_saving_a_new_default_on_the_copy_keeps_every_comment(self):
        values = self.store.load("voyager1", "default").values
        values["processing"]["frames_per_transect"] = 800
        saved = self.store.save_default("voyager1", values, "test on a copy", "LO")
        self.assertEqual(saved.version, "v1.1.0")
        self.assertIn("frames_per_transect: 800 # frames step0 extracts per source video", saved.text)
        self.assertIn("scale_error_threshold: 0.009 # maximum acceptable scale error in metres (9 mm)", saved.text)
        self.assertEqual(ps.validate_values("voyager1", saved.values), [])
        # The real checkout is untouched.
        self.assertFalse((REAL_CHECKOUT / "voyager1" / "versions" / "v1.1.0.yaml").exists())


if __name__ == "__main__":
    unittest.main()
