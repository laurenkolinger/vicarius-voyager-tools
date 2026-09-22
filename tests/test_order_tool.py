#!/usr/bin/env python3
"""
Module: tests/test_order_tool.py
Purpose: Unit, adversarial and integration tests for voyagertools.order_tool,
         the model behind the atlas edit tool, and for its hand-off to
         registry.set_processing_order on a temporary registry root.
Inputs:  in-memory rows and order lines; one temporary registry root per
         integration test (VICARIUS_3D_REGISTRY_ROOT plus importlib.reload).
Outputs: unittest results; every temporary directory is removed in tearDown.

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

from voyagertools import order_tool as ot  # noqa: E402


def row(rid, site, transect, year, token, **cells):
    """A registry-shaped row with the identity cells filled and process on."""
    base = {"readable_id": rid, "site": site, "transect": transect, "year": year, "season_token": token,
            "process": "true", "step1_status": "", "stage": ""}
    base.update(cells)
    return base


ROWS = [
    row("MRS_T1_2023ann", "MRS", "T1", "2023", "ann", step1_status="complete", stage="done"),
    row("MRS_T1_2024_pbl", "MRS", "T1", "2024", "_pbl"),
    row("MRS_T1_2024ann", "MRS", "T1", "2024", "ann"),
    row("LBH_T2_2025_pbl", "LBH", "T2", "2025", "_pbl"),
    row("LBH_T2_2024ann", "LBH", "T2", "2024", "ann"),
    row("BWR_T3_2025_pbl", "BWR", "T3", "2025", "_pbl", process="false"),
]


def lines(*ids):
    """Order lines at positions 1..N over `ids`, unlocked."""
    return [{"position": str(n), "readable_id": rid, "set_at": "", "set_by": "", "locked_at": "", "locked_by": ""}
            for n, rid in enumerate(ids, start=1)]


class PureRules(unittest.TestCase):
    """The row predicates and keys."""

    def test_inactive_stages_match_the_registry(self):
        import registry
        self.assertEqual(ot.INACTIVE_STAGES, registry.INACTIVE_STAGES)

    def test_started_when_status_or_active_stage(self):
        self.assertTrue(ot.row_started({"step1_status": "running"}))
        self.assertTrue(ot.row_started({"step1_status": "", "stage": "matching"}))
        self.assertFalse(ot.row_started({"step1_status": "", "stage": "done"}))
        self.assertFalse(ot.row_started({"step1_status": " ", "stage": ""}))
        self.assertFalse(ot.row_started({}))

    def test_process_on_accepts_the_true_spellings_only(self):
        for value in ("true", "TRUE", " 1 ", "yes"):
            self.assertTrue(ot.process_on({"process": value}))
        for value in ("false", "", None, "0", "maybe"):
            self.assertFalse(ot.process_on({"process": value}))

    def test_transect_key_and_date_key(self):
        self.assertEqual(ot.transect_key(ROWS[0]), "MRS_T1")
        self.assertEqual(ot.transect_key({"site": "", "transect": "T1"}), "")
        self.assertEqual(ot.date_key(row("x", "S", "T1", "2024", "_pbl")), (2024, 0))
        self.assertEqual(ot.date_key(row("x", "S", "T1", "2024", "ann")), (2024, 1))
        self.assertEqual(ot.date_key({"year": "junk"})[0], ot.NO_POSITION)

    def test_order_positions_skips_bad_lines_and_keeps_first_duplicate(self):
        got = ot.order_positions([{"position": "1", "readable_id": "A"}, {"position": "x", "readable_id": "B"},
                                  {"position": "3", "readable_id": ""}, {"position": "4", "readable_id": "A"}])
        self.assertEqual(got, {"A": 1})


class Blocks(unittest.TestCase):
    """Grouping into transect blocks and the started list."""

    def test_blocks_group_waiting_rows_by_transect_in_date_order(self):
        blocks = ot.build_blocks(ROWS, [])
        self.assertEqual([b["key"] for b in blocks], ["LBH_T2", "MRS_T1"])
        mrs = blocks[1]
        self.assertEqual([r["readable_id"] for r in mrs["rows"]], ["MRS_T1_2024_pbl", "MRS_T1_2024ann"])
        lbh = blocks[0]
        self.assertEqual([r["readable_id"] for r in lbh["rows"]], ["LBH_T2_2024ann", "LBH_T2_2025_pbl"])

    def test_blocks_follow_the_current_order(self):
        order = lines("MRS_T1_2023ann", "MRS_T1_2024_pbl", "MRS_T1_2024ann", "LBH_T2_2024ann", "LBH_T2_2025_pbl")
        blocks = ot.build_blocks(ROWS, order)
        self.assertEqual([b["key"] for b in blocks], ["MRS_T1", "LBH_T2"])
        self.assertEqual(blocks[0]["position"], 2)
        self.assertEqual(blocks[0]["rows"][0]["position"], 2)

    def test_process_off_and_started_rows_stay_out_of_blocks(self):
        keys = {r["readable_id"] for b in ot.build_blocks(ROWS, []) for r in b["rows"]}
        self.assertNotIn("BWR_T3_2025_pbl", keys)
        self.assertNotIn("MRS_T1_2023ann", keys)

    def test_rows_without_identity_are_skipped_and_reported(self):
        rows = ROWS + [row("ODD", "", "", "2025", "ann")]
        self.assertEqual(ot.skipped_rows(rows), ["ODD"])
        self.assertEqual(len(ot.build_blocks(rows, [])), 2)

    def test_started_rows_keep_position_order_then_date_order(self):
        rows = ROWS + [row("LBH_T2_2023ann", "LBH", "T2", "2023", "ann", stage="matching")]
        order = lines("LBH_T2_2023ann", "MRS_T1_2023ann")
        self.assertEqual([r["readable_id"] for r in ot.started_rows(rows, order)],
                         ["LBH_T2_2023ann", "MRS_T1_2023ann"])
        self.assertEqual([r["readable_id"] for r in ot.started_rows(rows, [])],
                         ["LBH_T2_2023ann", "MRS_T1_2023ann"])

    def test_order_table_shape(self):
        order = lines("MRS_T1_2023ann", "MRS_T1_2024_pbl")
        table = ot.order_table(ROWS, order, {"locked_at": "2026-09-04T01:00:00-04:00", "locked_by": "LO"},
                               site_name_fn=lambda code: {"MRS": "Meri Shoal"}.get(code, ""))
        self.assertEqual(table["lock"]["locked_by"], "LO")
        self.assertEqual(table["count"], 2)
        self.assertEqual(table["first"], "MRS_T1_2023ann")
        self.assertEqual(table["last"], "MRS_T1_2024_pbl")
        self.assertEqual(table["ordered_ids"], ["MRS_T1_2023ann", "MRS_T1_2024_pbl"])
        names = {b["key"]: b["site_name"] for b in table["blocks"]}
        self.assertEqual(names, {"MRS_T1": "Meri Shoal", "LBH_T2": ""})
        self.assertIsNone(ot.order_table(ROWS, [], None)["lock"])

    def test_order_table_refuses_a_bad_lock_type(self):
        with self.assertRaises(TypeError):
            ot.order_table(ROWS, [], "locked")

    def test_rows_type_refused(self):
        with self.assertRaises(TypeError):
            ot.build_blocks("MRS_T1_2023ann", [])
        with self.assertRaises(TypeError):
            ot.build_blocks([{"a": 1}, "junk"], [])
        with self.assertRaises(TypeError):
            ot.build_blocks({"readable_id": "x"}, [])


class Expansion(unittest.TestCase):
    """Turning a block order into the id list."""

    def test_ids_follow_started_then_blocks(self):
        ids = ot.ids_for_transects(ROWS, [], ["LBH_T2", "MRS_T1"])
        self.assertEqual(ids, ["MRS_T1_2023ann", "LBH_T2_2024ann", "LBH_T2_2025_pbl",
                               "MRS_T1_2024_pbl", "MRS_T1_2024ann"])

    def test_missing_transect_refused(self):
        with self.assertRaises(ValueError) as caught:
            ot.ids_for_transects(ROWS, [], ["MRS_T1"])
        self.assertIn("LBH T2", str(caught.exception))

    def test_unknown_transect_refused(self):
        with self.assertRaises(ValueError) as caught:
            ot.ids_for_transects(ROWS, [], ["MRS_T1", "LBH_T2", "ZZZ_T9"])
        self.assertIn("ZZZ T9", str(caught.exception))

    def test_duplicate_and_malformed_keys_refused(self):
        with self.assertRaises(ValueError):
            ot.ids_for_transects(ROWS, [], ["MRS_T1", "MRS_T1", "LBH_T2"])
        with self.assertRaises(ValueError):
            ot.ids_for_transects(ROWS, [], ["MRS_T1", "LBH T2"])
        with self.assertRaises(ValueError):
            ot.ids_for_transects(ROWS, [], ["MRS_T1", "../etc"])
        with self.assertRaises(TypeError):
            ot.ids_for_transects(ROWS, [], "MRS_T1")
        with self.assertRaises(TypeError):
            ot.ids_for_transects(ROWS, [], ["MRS_T1", 7])

    def test_hostile_strings_never_crash(self):
        rows = ROWS + [row("X\x00Y", "S<script>", "T\n1", "20x4", "zzz")]
        table = ot.order_table(rows, [], None)
        self.assertTrue(any(b["site"] == "S<script>" for b in table["blocks"]))

    def test_huge_input_in_bounded_time(self):
        import time
        rows = [row(f"S{n:04d}_T1_2025ann", f"S{n:04d}", "T1", "2025", "ann") for n in range(5000)]
        order = lines(*[r["readable_id"] for r in rows])
        keys = [b["key"] for b in ot.build_blocks(rows, order)]
        started = time.monotonic()
        ids = ot.ids_for_transects(rows, order, list(reversed(keys)))
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(ids[0], "S4999_T1_2025ann")

    def test_move_block(self):
        keys = ["A_T1", "B_T1", "C_T1"]
        self.assertEqual(ot.move_block(keys, "B_T1", -1), ["B_T1", "A_T1", "C_T1"])
        self.assertEqual(ot.move_block(keys, "C_T1", 1), keys)
        self.assertEqual(keys, ["A_T1", "B_T1", "C_T1"])
        with self.assertRaises(ValueError):
            ot.move_block(keys, "Z_T1", 1)
        with self.assertRaises(ValueError):
            ot.move_block(keys, "A_T1", 2)

    def test_lock_summary(self):
        self.assertEqual(ot.lock_summary([]), {"count": 0, "first": "", "last": ""})
        self.assertEqual(ot.lock_summary(lines("A", "B", "C")), {"count": 3, "first": "A", "last": "C"})


class WithRegistry(unittest.TestCase):
    """The expansion hands a valid list to the registry library."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vt-order-")
        os.environ["VICARIUS_3D_REGISTRY_ROOT"] = self.root
        import registry
        self.registry = importlib.reload(registry)
        for r in ROWS:
            self.registry.upsert(r["readable_id"], {k: v for k, v in r.items() if k != "readable_id"}, "seed")

    def tearDown(self):
        os.environ.pop("VICARIUS_3D_REGISTRY_ROOT", None)
        shutil.rmtree(self.root, ignore_errors=True)
        import registry
        importlib.reload(registry)

    def test_expanded_order_is_accepted_and_lockable(self):
        rows = self.registry.load()
        ids = ot.ids_for_transects(rows, self.registry.processing_order(), ["LBH_T2", "MRS_T1"])
        written = self.registry.set_processing_order(ids, "voyager_tools:LO")
        self.assertEqual([line["readable_id"] for line in written], ids)
        table = ot.order_table(rows, self.registry.processing_order(), self.registry.processing_order_lock())
        self.assertEqual([b["key"] for b in table["blocks"]], ["LBH_T2", "MRS_T1"])
        self.registry.lock_processing_order("voyager_tools:LO")
        with self.assertRaises(ValueError) as caught:
            self.registry.set_processing_order(list(reversed(ids)), "voyager_tools:LO")
        self.assertIn("locked by voyager_tools:LO", str(caught.exception))
        self.registry.unlock_processing_order("voyager_tools:LO")
        self.assertIsNone(self.registry.processing_order_lock())


if __name__ == "__main__":
    unittest.main()
