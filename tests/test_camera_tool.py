#!/usr/bin/env python3
"""
Module: tests/test_camera_tool.py
Purpose: Unit, adversarial and integration tests for voyagertools.camera_tool
         and its hand-off to registry.set_season on a temporary registry root.
Inputs:  in-memory rows and season lines; one temporary registry root.
Outputs: unittest results; the temporary root is removed in tearDown.

Run from the clone root:
    python3 -m unittest discover -s tests -q
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
REGISTRY_LIB_DIR = os.path.join(os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius"),
                                "_METADATA", "3d")
if REGISTRY_LIB_DIR not in sys.path:
    sys.path.insert(0, REGISTRY_LIB_DIR)

from voyagertools import camera_tool as ct  # noqa: E402

ROWS = [
    {"readable_id": "MRS_T1_2025_pbl", "year": "2025", "season_token": "_pbl"},
    {"readable_id": "LBH_T1_2025_pbl", "year": "2025", "season_token": "_pbl"},
    {"readable_id": "MRS_T1_2024ann", "year": "2024", "season_token": "ann"},
    {"readable_id": "BAD", "year": "20x4", "season_token": "ann"},
]
LINES = [
    {"season_key": "2023ann", "year": "2023", "season_token": "ann", "camera_model": "GoPro 11",
     "preprocessing": "none", "notes": "", "set_at": "2026-09-04T01:00:00-04:00", "set_by": "voyager_tools:LO"},
    {"season_key": "2025_pbl", "year": "2025", "season_token": "_pbl", "camera_model": "GoPro 12",
     "preprocessing": "proxy encode", "notes": "n", "set_at": "", "set_by": ""},
]


class Pure(unittest.TestCase):
    def test_season_key_and_label(self):
        self.assertEqual(ct.season_key("2025", "_pbl"), "2025_pbl")
        self.assertEqual(ct.season_key(2024, "ann"), "2024ann")
        self.assertEqual(ct.season_key("24", "ann"), "")
        self.assertEqual(ct.season_key("2024", "fall"), "")
        self.assertEqual(ct.season_label("2025_pbl"), "2025 spring")
        self.assertEqual(ct.season_label("2024ann"), "2024 annual")
        with self.assertRaises(ValueError):
            ct.season_label("2024-ann")

    def test_fields_match_the_registry_input_columns(self):
        import registry
        self.assertEqual(tuple(registry.SEASON_INPUT_COLUMNS), ct.SEASON_FIELDS)

    def test_table_unions_rows_and_lines_in_date_order(self):
        table = ct.season_table(ROWS, LINES)
        self.assertEqual([t["season_key"] for t in table], ["2023ann", "2024ann", "2025_pbl"])
        by_key = {t["season_key"]: t for t in table}
        self.assertEqual(by_key["2025_pbl"]["row_count"], 2)
        self.assertEqual(by_key["2025_pbl"]["camera_model"], "GoPro 12")
        self.assertEqual(by_key["2025_pbl"]["label"], "2025 spring")
        self.assertEqual(by_key["2024ann"]["camera_model"], "")
        self.assertEqual(by_key["2024ann"]["row_count"], 1)
        self.assertEqual(by_key["2023ann"]["row_count"], 0)
        self.assertEqual(by_key["2023ann"]["set_by"], "voyager_tools:LO")

    def test_malformed_season_line_listed_last_without_label(self):
        table = ct.season_table([], LINES + [{"season_key": "junk", "camera_model": "x"}])
        self.assertEqual(table[-1]["season_key"], "junk")
        self.assertEqual(table[-1]["label"], "")

    def test_table_refuses_bad_types(self):
        with self.assertRaises(TypeError):
            ct.season_table("rows", [])
        with self.assertRaises(TypeError):
            ct.season_table([], [1])
        with self.assertRaises(TypeError):
            ct.season_table({"year": "2025"}, [])

    def test_empty_inputs(self):
        self.assertEqual(ct.season_table([], []), [])

    def test_clean_fields(self):
        self.assertEqual(ct.clean_season_fields({"camera_model": " GoPro 12 ", "junk": "x"}),
                         {"camera_model": "GoPro 12"})
        self.assertEqual(ct.clean_season_fields({"notes": None}), {"notes": ""})
        with self.assertRaises(ValueError):
            ct.clean_season_fields({"junk": "x"})
        with self.assertRaises(TypeError):
            ct.clean_season_fields({"camera_model": 12})
        with self.assertRaises(TypeError):
            ct.clean_season_fields(["camera_model"])
        with self.assertRaises(ValueError):
            ct.clean_season_fields({"notes": "a\x00b"})
        with self.assertRaises(ValueError):
            ct.clean_season_fields({"notes": "x" * (ct.MAX_FIELD_CHARS + 1)})

    def test_hostile_text_survives(self):
        fields = ct.clean_season_fields({"notes": "<script>alert(1)</script>\n, \"quoted\""})
        self.assertIn("<script>", fields["notes"])


class WithRegistry(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vt-camera-")
        os.environ["VICARIUS_3D_REGISTRY_ROOT"] = self.root
        import registry
        self.registry = importlib.reload(registry)
        self.registry.upsert("MRS_T1_2025_pbl", {"site": "MRS", "transect": "T1", "year": "2025",
                                                 "season_token": "_pbl"}, "seed")

    def tearDown(self):
        os.environ.pop("VICARIUS_3D_REGISTRY_ROOT", None)
        shutil.rmtree(self.root, ignore_errors=True)
        import registry
        importlib.reload(registry)

    def test_round_trip_through_set_season(self):
        fields = ct.clean_season_fields({"camera_model": "GoPro 12", "preprocessing": "proxy"})
        self.assertTrue(self.registry.set_season("2025_pbl", fields, "voyager_tools:LO"))
        table = ct.season_table(self.registry.load(), self.registry.seasons())
        self.assertEqual(table[0]["camera_model"], "GoPro 12")
        self.assertEqual(table[0]["set_by"], "voyager_tools:LO")
        self.assertEqual(table[0]["row_count"], 1)
        self.assertFalse(self.registry.set_season("2025_pbl", fields, "voyager_tools:LO"))


if __name__ == "__main__":
    unittest.main()
