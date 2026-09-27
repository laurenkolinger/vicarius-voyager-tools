#!/usr/bin/env python3
"""
Module: tests/test_params_editor_js.py
Purpose: Proves the parameter editor (static/voyager_tools.js, editorKeys)
         shows only the schema keys a loaded version file holds, so a save
         from the editor never invents a value. The case that matters is
         the Voyager 2 schema against the legacy voyagerparams voyager2
         v0.1.0 file, which holds none of the schema's keys: without the
         filter every boolean field would save as false and every text field
         as blank. The real script runs in node with a stub window and
         document, because jsdom is not installed on this box.
Inputs:  the clone's static/voyager_tools.js and voyagertools/params_schema_*.yaml.
Outputs: unittest results; no file is written.

Run from the clone root:
    python3 -m unittest tests.test_params_editor_js -v
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLONE = HERE.parent
sys.path.insert(0, str(CLONE))

from voyagertools import paramsstore as ps  # noqa: E402

JS_PATH = CLONE / "static" / "voyager_tools.js"
NODE_TIMEOUT_S = 30

# Loads the real script with a stub window and document, then prints what
# editorKeys returns for the schema and values given on stdin.
HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const context = {
  window: {},
  document: { readyState: "complete", getElementById: () => null, querySelector: () => null,
              addEventListener: () => {} },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(input.js, "utf8"), context);
const out = context.window.VoyagerTools.editorKeys(input.schema, input.values);
process.stdout.write(JSON.stringify({ paths: out.keys.map((k) => k.path), missing: out.missing }));
"""


def editor_keys(schema: dict, values: dict) -> dict:
    """What editorKeys returns for `schema` and `values`, run in node."""
    payload = json.dumps({"js": str(JS_PATH), "schema": schema, "values": values})
    done = subprocess.run([shutil.which("node"), "-e", HARNESS], input=payload, capture_output=True, text=True,
                          timeout=NODE_TIMEOUT_S)
    if done.returncode != 0:
        raise AssertionError(f"node failed: {done.stderr}")
    return json.loads(done.stdout)


@unittest.skipUnless(shutil.which("node"), "node is not installed on this box")
class EditorKeysTests(unittest.TestCase):
    """editorKeys against the two real schemas."""

    def test_a_legacy_voyager2_file_shows_no_field(self):
        schema = ps.load_schema("voyager2")
        legacy = {"processing": {"model_processing": {"ortho_tile_size": 256}}}
        out = editor_keys(schema, legacy)
        self.assertEqual(out["paths"], [])
        self.assertEqual(out["missing"], len(schema["keys"]))

    def test_a_file_with_some_keys_shows_exactly_those(self):
        schema = ps.load_schema("voyager2")
        values = {"voyager2": {"psx": {"save": False}, "preview": {"max_side_px": 1024}}}
        out = editor_keys(schema, values)
        self.assertEqual(sorted(out["paths"]), ["voyager2.preview.max_side_px", "voyager2.psx.save"])
        self.assertEqual(out["missing"], len(schema["keys"]) - 2)

    def test_a_false_or_zero_value_still_counts_as_held(self):
        schema = {"keys": [{"path": "a.flag", "type": "boolean"}, {"path": "a.n", "type": "integer"},
                           {"path": "a.s", "type": "string"}]}
        out = editor_keys(schema, {"a": {"flag": False, "n": 0, "s": ""}})
        self.assertEqual(out, {"paths": ["a.flag", "a.n", "a.s"], "missing": 0})

    def test_a_full_voyager1_file_shows_every_key(self):
        schema = ps.load_schema("voyager1")
        values: dict = {}
        for entry in schema["keys"]:
            node = values
            parts = entry["path"].split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = entry["default"]
        out = editor_keys(schema, values)
        self.assertEqual(out["missing"], 0)
        self.assertEqual(len(out["paths"]), len(schema["keys"]))

    def test_no_schema_or_no_values_is_empty(self):
        self.assertEqual(editor_keys(None, {}), {"paths": [], "missing": 0})
        self.assertEqual(editor_keys({"keys": [{"path": "x"}]}, None), {"paths": [], "missing": 1})


if __name__ == "__main__":
    unittest.main()
