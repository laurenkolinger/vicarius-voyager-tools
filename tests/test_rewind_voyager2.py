#!/usr/bin/env python3
"""
Module: tests/test_rewind_voyager2.py
Purpose: Unit, adversarial and integration tests for
         voyagertools.rewind_voyager2: the Voyager 2 rewind (the Shelf output
         folder renamed to <name>_voy2_archive_<stamp>, the step 2 cells and
         the voyager2 facts cleared through the registry writer with events,
         a note in the row's notes cell) and the Voyager 2 spot redo (the
         voyager2 fact redo_requested, which the registry's readiness rule
         honours). A local folder stands in for the Shelf.
Inputs:  a scratch registry root and a scratch Shelf folder; no ssh, no NAS,
         no live registry.
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
CLONE = HERE.parent
sys.path.insert(0, str(CLONE))
sys.path.insert(0, str(HERE))
VICARIUS_ROOT = os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius")
REGISTRY_LIB_DIR = os.path.join(VICARIUS_ROOT, "_METADATA", "3d")
if REGISTRY_LIB_DIR not in sys.path:
    sys.path.insert(0, REGISTRY_LIB_DIR)

from status_seed import seed_status  # noqa: E402
from voyagertools import rewind_voyager2 as rv2  # noqa: E402

HOST = "146.226.147.140"
SHELF_ROOT = "/volume6/Archive8_12TB/driver_deposits"
PARENT = f"{SHELF_ROOT}/TCRMP_8sep26_LO_MRS2_23ann-25pbl"
OUT = "MRS_T1_3doutput"
OUT_PATH = f"{PARENT}/{OUT}"
IDS = ("MRS_T1_2023ann", "MRS_T1_2025_pbl")
NEW_ID = "MRS_T1_2026ann"
OTHER_ID = "LBH_T2_2025_pbl"
LOG = "logs/step2_20261002-120000.log"
ACTOR = "voyager_tools:LO"
STAMP = "20261003-091500"


class Case(unittest.TestCase):
    """A scratch registry with MRS T1 through Voyager 2 and a scratch Shelf."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="vt-rv2-"))
        reg_root = self.root / "registry"
        reg_root.mkdir()
        os.environ["VICARIUS_3D_REGISTRY_ROOT"] = str(reg_root)
        import registry
        self.reg = importlib.reload(registry)
        self.shelf = self.root / "shelf"
        self.local_out = Path(str(self.shelf) + OUT_PATH)
        (self.local_out / "logs").mkdir(parents=True)
        (self.local_out / LOG).write_text("log")
        (self.local_out / "vigil_manifest.json").write_text("{}")
        for rid, year, token in ((IDS[0], "2023", "ann"), (IDS[1], "2025", "_pbl")):
            self.seed_complete(rid, "MRS", "T1", year, token)
        seed_status(self.reg, NEW_ID, "awaiting", site="MRS", transect="T1", year="2026", season_token="ann",
                    processing_folder="MRS_T1_3D", processing_location=f"{HOST}:{PARENT}/MRS_T1_3D")
        self.seed_complete(OTHER_ID, "LBH", "T2", "2025", "_pbl", out="LBH_T2_3doutput")
        self.renamed = []

    def tearDown(self):
        os.environ.pop("VICARIUS_3D_REGISTRY_ROOT", None)
        shutil.rmtree(self.root, ignore_errors=True)
        import registry
        importlib.reload(registry)

    def seed_complete(self, rid, site, transect, year, token, out=OUT):
        """One row after a real Voyager 2 turn whose deposit verified, with its voyager2 facts."""
        seed_status(self.reg, rid, "done", "2026-09-30 10:00:00", site=site, transect=transect, year=year,
                    season_token=token, step1_status="complete", processing_folder=f"{site}_{transect}_3D",
                    processing_location=f"{HOST}:{PARENT}/{site}_{transect}_3D", output_folder=out,
                    output_location=f"{HOST}:{PARENT}/{out}", output_location_verified="2026-10-02 14:30 AST",
                    output_size_gb="84.2", step2_status="complete", step2_started="2026-10-02 12:00:00",
                    step2_finished="2026-10-02 14:05:00", step2_seconds="7500", step="2", stage="done",
                    stage_started="2026-10-02 14:04:00", console_log=f"{HOST}:{PARENT}/{out}/{LOG}",
                    notes="resumed after interrupted run")
        self.reg.set_facts(rid, "voyager2", {"run_id": "TCRMP_V2_2oct26_LO_MRS1", "batch_id": "batch_v2_20261002-120000",
                                             "reserved_by": "", "scale_error_mm": 1.742,
                                             "ortho": f"{HOST}:{PARENT}/{out}/ortho/{rid}/{rid}_ortho.tif"},
                           "carousel", links={"ortho": f"{HOST}:{PARENT}/{out}/ortho/{rid}/{rid}_ortho.tif"})

    def archive(self, dest):
        """The Shelf rename, on the local stand-in: <dest>_voy2_archive_<STAMP>, or None when nothing is there."""
        local = Path(str(self.shelf) + dest)
        if not local.exists():
            return None
        target = f"{dest}{rv2.ARCHIVE_INFIX}{STAMP}"
        local.rename(str(self.shelf) + target)
        self.renamed.append((dest, target))
        return target

    def kw(self, **extra):
        out = {"shelf_host": HOST, "shelf_root": SHELF_ROOT}
        out.update(extra)
        return out


class Plans(Case):
    def test_the_plan_covers_every_row_of_the_transect_with_a_result(self):
        plan = rv2.plan(IDS[1], self.reg, **self.kw(stamp=STAMP))
        self.assertEqual(plan["transect"], "MRS_T1")
        self.assertEqual([r["readable_id"] for r in plan["rows"]], list(IDS))
        self.assertEqual(plan["shelf_folder"], OUT_PATH)
        self.assertEqual(plan["archive_to"], f"{OUT_PATH}{rv2.ARCHIVE_INFIX}{STAMP}")
        resets = plan["rows"][0]["registry_resets"]
        for cell in ("step2_status", "step2_started", "step2_finished", "step2_seconds", "output_location",
                     "output_location_verified", "output_size_gb", "step", "stage", "stage_started"):
            self.assertIn(cell, resets)
        self.assertNotIn("step1_status", resets)
        self.assertNotIn("manual_edit_status", resets)
        self.assertEqual(sorted(plan["rows"][0]["facts_cleared"]), ["batch_id", "ortho", "run_id", "scale_error_mm"])
        self.assertEqual(plan["rows"][0]["console_log"]["to"], f"{HOST}:{OUT_PATH}{rv2.ARCHIVE_INFIX}{STAMP}/{LOG}")
        self.assertFalse(plan["empty"])
        self.assertEqual(plan["vigil_references"], [])

    def test_a_transect_with_no_result_is_refused(self):
        with self.assertRaises(rv2.Voyager2Refused) as ctx:
            rv2.apply(NEW_ID, self.reg, ACTOR, "LO", self.archive, **self.kw(stamp=STAMP))
        self.assertIn("rewind", str(ctx.exception))
        for rid in IDS:
            rv2_cells = self.reg.get(rid)
            self.assertEqual(rv2_cells["step2_status"], "complete")
        # Only MRS T1 carries a result there, so a fresh transect with none is refused.
        seed_status(self.reg, "CKR_T3_2025_pbl", "", site="CKR", transect="T3", year="2025", season_token="_pbl")
        with self.assertRaises(rv2.Voyager2Refused):
            rv2.plan_or_refuse("CKR_T3_2025_pbl", self.reg, **self.kw())

    def test_unknown_row(self):
        with self.assertRaises(rv2.Voyager2Refused) as ctx:
            rv2.plan_or_refuse("NOPE_T1_2020ann", self.reg, **self.kw())
        self.assertIn("No registry row", str(ctx.exception))


class Refusals(Case):
    def refused(self, needle, **extra):
        """Apply is refused naming `needle`, and neither the Shelf nor any row or fact changed."""
        before = {rid: (self.reg.get(rid), self.reg.fact_sections(rid)) for rid in IDS}
        with self.assertRaises(rv2.Voyager2Refused) as ctx:
            rv2.apply(IDS[0], self.reg, ACTOR, "LO", self.archive, **self.kw(**extra))
        self.assertIn(needle, str(ctx.exception))
        self.assertEqual(self.renamed, [])
        self.assertTrue(self.local_out.is_dir())
        for rid in IDS:
            self.assertEqual((self.reg.get(rid), self.reg.fact_sections(rid)), before[rid])

    def test_a_reserved_transect_is_refused(self):
        self.reg.set_facts(IDS[1], "voyager2", {"reserved_by": "batch_v2_20261003-090000/turn001"}, "voyager2:LO")
        self.refused("batch_v2_20261003-090000/turn001")

    def test_a_running_step2_is_refused(self):
        self.reg.upsert(IDS[1], {"step2_status": "running"}, "3D_phase2")
        self.refused("running")

    def test_a_live_lock_is_refused(self):
        self.reg.run_is_live = lambda row: row.get("readable_id") == IDS[1]
        self.refused("processing right now")

    def test_an_output_still_on_this_machine_is_refused(self):
        self.reg.upsert(IDS[1], {"output_location": "/mnt/rip/driver_staging/voyager2_stage/b/turn001/" + OUT}, "carousel")
        self.refused("still on this machine")

    def test_rows_that_disagree_on_the_shelf_folder_are_refused(self):
        self.reg.upsert(IDS[1], {"output_location": f"{HOST}:{SHELF_ROOT}/other/{OUT}"}, "carousel")
        self.refused("disagree")

    def test_a_folder_outside_the_shelf_root_is_refused(self):
        for location in (f"{HOST}:/volume4/Archive6_16TB/{OUT}", f"{HOST}:{SHELF_ROOT}/../elsewhere/{OUT}"):
            with self.subTest(location=location):
                for rid in IDS:
                    self.reg.upsert(rid, {"output_location": location}, "carousel")
                self.refused("Shelf")

    def test_a_foreign_host_or_a_bad_name_is_refused(self):
        cases = {f"10.0.0.9:{PARENT}/{OUT}": "host", f"{HOST}:{PARENT}/MRS_T1_3D": "output folder",
                 f"{HOST}:{PARENT}/LBH_T2_3doutput": "output folder"}
        for location, needle in cases.items():
            with self.subTest(location=location):
                for rid in IDS:
                    self.reg.upsert(rid, {"output_location": location}, "carousel")
                self.refused(needle)

    def test_an_unknown_nas_host_is_refused(self):
        self.refused("NAS host", shelf_host="")

    def test_a_vigil_reference_needs_the_explicit_confirmation(self):
        self.reg.set_facts(IDS[0], "vigil", {"staged_manifest": "/mnt/rip/driver_staging/vigil_stage/b/x.json"},
                           "carousel")
        plan = rv2.plan(IDS[0], self.reg, **self.kw())
        self.assertEqual(plan["vigil_references"][0]["value"], "/mnt/rip/driver_staging/vigil_stage/b/x.json")
        with self.assertRaises(rv2.Voyager2Refused) as ctx:
            rv2.apply(IDS[0], self.reg, ACTOR, "LO", self.archive, **self.kw())
        self.assertIn("/mnt/rip/driver_staging/vigil_stage/b/x.json", str(ctx.exception))
        self.assertEqual(self.renamed, [])
        manifest = rv2.apply(IDS[0], self.reg, ACTOR, "LO", self.archive, **self.kw(confirm_vigil=True))
        self.assertTrue(manifest["vigil_confirmed"])

    def test_bad_arguments(self):
        with self.assertRaises(ValueError):
            rv2.apply(IDS[0], self.reg, "", "LO", self.archive, **self.kw())
        with self.assertRaises(ValueError):
            rv2.apply(IDS[0], self.reg, ACTOR, "lo1", self.archive, **self.kw())
        with self.assertRaises(ValueError):
            rv2.plan(IDS[0], self.reg, **self.kw(shelf_root="relative"))


class Apply(Case):
    def test_rewind_renames_the_shelf_folder_and_clears_the_record(self):
        manifest = rv2.apply(IDS[0], self.reg, ACTOR, "LO", self.archive, **self.kw(stamp=STAMP))
        archived = f"{OUT_PATH}{rv2.ARCHIVE_INFIX}{STAMP}"
        self.assertEqual(manifest["archived_to"], archived)
        self.assertEqual(self.renamed, [(OUT_PATH, archived)])
        self.assertFalse(self.local_out.exists())
        self.assertTrue(Path(str(self.shelf) + archived, LOG).is_file())  # nothing deleted
        for rid in IDS:
            row = self.reg.get(rid)
            for cell in ("step2_status", "step2_finished", "output_location", "output_location_verified",
                         "output_size_gb", "step", "stage"):
                self.assertEqual(row[cell], "", (rid, cell))
            self.assertEqual(row["console_log"], f"{HOST}:{archived}/{LOG}")
            self.assertEqual(row["step1_status"], "complete")
            self.assertEqual(row["manual_edit_status"], "done")
            self.assertEqual(row["output_folder"], OUT)
            self.assertIn("Voyager 2 result rewound", row["notes"])
            self.assertIn(archived, row["notes"])
            self.assertTrue(row["notes"].startswith("resumed after interrupted run"))
            section = self.reg.fact_sections(rid)["voyager2"]
            self.assertTrue(all(not v["value"] for v in section.values()), section)
            self.assertEqual(section["run_id"]["recorded_by"], ACTOR)
            self.assertEqual(section["ortho"]["link"], "")
        # The unrelated transect and the timepoint with no result are untouched.
        self.assertEqual(self.reg.get(OTHER_ID)["step2_status"], "complete")
        self.assertEqual(self.reg.get(NEW_ID)["notes"], "")
        events, _ = self.reg.events_for(IDS[0])
        fields = {(e["field"], e["actor"]) for e in events}
        self.assertIn(("step2_status", ACTOR), fields)
        self.assertIn(("row_facts", ACTOR), fields)
        # The row is ready for Voyager 2 again once nothing blocks it.
        facts = self.reg.fact_sections(IDS[0])
        self.assertNotIn("step2_complete", self.reg.voyager2_blockers(self.reg.get(IDS[0]), facts, False,
                                                                      shelf_prefix=f"{HOST}:"))

    def test_a_second_rewind_has_nothing_left(self):
        rv2.apply(IDS[0], self.reg, ACTOR, "LO", self.archive, **self.kw(stamp=STAMP))
        with self.assertRaises(rv2.Voyager2Refused):
            rv2.apply(IDS[0], self.reg, ACTOR, "LO", self.archive, **self.kw(stamp=STAMP))

    def test_a_missing_shelf_folder_is_a_warning_and_the_record_still_clears(self):
        shutil.rmtree(self.local_out)
        manifest = rv2.apply(IDS[0], self.reg, ACTOR, "LO", self.archive, **self.kw(stamp=STAMP))
        self.assertIsNone(manifest["archived_to"])
        self.assertTrue(any("was not on the Shelf" in w for w in manifest["warnings"]))
        self.assertEqual(self.reg.get(IDS[0])["step2_status"], "")
        self.assertEqual(self.reg.get(IDS[0])["console_log"], f"{HOST}:{OUT_PATH}/{LOG}")

    def test_a_rename_failure_changes_nothing(self):
        def refuse(dest):
            raise RuntimeError("the NAS did not answer")
        with self.assertRaises(rv2.Voyager2Refused) as ctx:
            rv2.apply(IDS[0], self.reg, ACTOR, "LO", refuse, **self.kw())
        self.assertIn("the NAS did not answer", str(ctx.exception))
        self.assertEqual(self.reg.get(IDS[0])["step2_status"], "complete")
        self.assertTrue(self.local_out.is_dir())

    def test_a_registry_failure_after_the_rename_says_so_and_a_rerun_finishes(self):
        real_set_facts = self.reg.set_facts
        calls = []

        def failing(*args, **kwargs):
            calls.append(args)
            raise OSError("disk full")
        self.reg.set_facts = failing
        with self.assertRaises(OSError) as ctx:
            rv2.apply(IDS[0], self.reg, ACTOR, "LO", self.archive, **self.kw(stamp=STAMP))
        self.assertIn("already renamed", str(ctx.exception))
        self.assertIn(f"{OUT_PATH}{rv2.ARCHIVE_INFIX}{STAMP}", str(ctx.exception))
        self.reg.set_facts = real_set_facts
        manifest = rv2.apply(IDS[0], self.reg, ACTOR, "LO", self.archive, **self.kw(stamp=STAMP))
        self.assertIsNone(manifest["archived_to"])
        section = self.reg.fact_sections(IDS[0])["voyager2"]
        self.assertTrue(all(not v["value"] for v in section.values()))

    def seed_test_only(self, output_shelf):
        """BWR T1 with only a test run: no step 2 cells, a voyager2 section naming its Shelf output folder."""
        seed_status(self.reg, "BWR_T1_2025_pbl", "editing", site="BWR", transect="T1", year="2025",
                    season_token="_pbl", processing_location="/mnt/rip/driver_staging/editbench/BWR_T1_3D")
        self.reg.set_facts("BWR_T1_2025_pbl", "voyager2", {"run_kind": "test", "output_shelf": output_shelf},
                           "3D_phase2")

    def test_a_test_run_output_folder_is_renamed_like_a_real_one(self):
        test_out = f"{PARENT}/BWR_T1_3doutput"
        Path(str(self.shelf) + test_out).mkdir(parents=True)
        self.seed_test_only(f"{HOST}:{test_out}")
        plan = rv2.plan("BWR_T1_2025_pbl", self.reg, **self.kw(stamp=STAMP))
        self.assertEqual(plan["shelf_folder"], test_out)
        self.assertEqual(plan["warnings"], [])
        manifest = rv2.apply("BWR_T1_2025_pbl", self.reg, ACTOR, "LO", self.archive, **self.kw(stamp=STAMP))
        renamed = f"{test_out}{rv2.ARCHIVE_INFIX}{STAMP}"
        self.assertEqual(manifest["archived_to"], renamed)
        self.assertEqual(self.renamed, [(test_out, renamed)])
        self.assertTrue(Path(str(self.shelf) + renamed).is_dir())
        self.assertIn(renamed, self.reg.get("BWR_T1_2025_pbl")["notes"])
        section = self.reg.fact_sections("BWR_T1_2025_pbl")["voyager2"]
        self.assertTrue(all(not v["value"] for v in section.values()))
        self.assertEqual(self.reg.get("BWR_T1_2025_pbl")["manual_edit_status"], "editing")

    def test_a_test_run_folder_already_gone_is_named_in_the_note(self):
        self.seed_test_only(f"{HOST}:{PARENT}/BWR_T1_3doutput")
        manifest = rv2.apply("BWR_T1_2025_pbl", self.reg, ACTOR, "LO", self.archive, **self.kw())
        self.assertIsNone(manifest["archived_to"])
        self.assertEqual(self.renamed, [])
        self.assertTrue(any("was not on the Shelf" in w for w in manifest["warnings"]))

    def test_a_hostile_test_output_path_is_refused_before_anything_moves(self):
        for bad in (f"{HOST}:/volume1/elsewhere/BWR_T1_3doutput", f"{HOST}:{PARENT}/../BWR_T1_3doutput",
                    f"{HOST}:{PARENT}/MRS_T1_3doutput", f"10.0.0.9:{PARENT}/BWR_T1_3doutput",
                    "/mnt/rip/driver_staging/voyager2_stage/test_20261001-120000/BWR_T1_3doutput"):
            with self.subTest(bad=bad):
                self.seed_test_only(bad)
                with self.assertRaises(rv2.Voyager2Refused):
                    rv2.apply("BWR_T1_2025_pbl", self.reg, ACTOR, "LO", self.archive, **self.kw())
                self.assertEqual(self.renamed, [])

    def test_a_test_output_that_disagrees_with_the_real_one_is_refused(self):
        self.reg.set_facts(IDS[0], "voyager2", {"run_kind": "test",
                                                "output_shelf": f"{HOST}:{SHELF_ROOT}/other_job/{OUT}"}, "3D_phase2")
        with self.assertRaises(rv2.Voyager2Refused) as ctx:
            rv2.apply(IDS[0], self.reg, ACTOR, "LO", self.archive, **self.kw())
        self.assertIn("disagree", str(ctx.exception))
        self.assertEqual(self.renamed, [])

    def test_a_long_notes_cell_keeps_its_last_characters(self):
        self.reg.upsert(IDS[0], {"notes": "x" * 600}, "seed")
        rv2.apply(IDS[0], self.reg, ACTOR, "LO", self.archive, **self.kw(stamp=STAMP))
        notes = self.reg.get(IDS[0])["notes"]
        self.assertLessEqual(len(notes), rv2.NOTES_CAP)
        self.assertIn("Voyager 2 result rewound", notes)


class SpotRedo(Case):
    def test_redo_request_on_every_complete_row_makes_the_transect_ready_again(self):
        plan = rv2.spot_redo_plan(IDS[0], self.reg)
        self.assertEqual([r["readable_id"] for r in plan["rows"]], list(IDS))
        facts = self.reg.fact_sections(IDS[0])
        self.assertIn("step2_complete", self.reg.voyager2_blockers(self.reg.get(IDS[0]), facts, False,
                                                                    shelf_prefix=f"{HOST}:"))
        manifest = rv2.spot_redo(IDS[0], self.reg, ACTOR, "LO", now_iso=lambda: "2026-10-03T09:15:00-04:00")
        self.assertEqual(manifest["redo_requested"], "requested 2026-10-03T09:15:00-04:00 by LO")
        for rid in IDS:
            facts = self.reg.fact_sections(rid)
            self.assertEqual(facts["voyager2"]["redo_requested"]["value"], "requested 2026-10-03T09:15:00-04:00 by LO")
            self.assertEqual(self.reg.voyager2_blockers(self.reg.get(rid), facts, False, shelf_prefix=f"{HOST}:"), [])
            self.assertEqual(self.reg.get(rid)["step2_status"], "complete")  # nothing is moved or cleared
            self.assertIn("Voyager 2 redo requested", self.reg.get(rid)["notes"])
        self.assertTrue(self.local_out.is_dir())
        self.assertNotIn("redo_requested", self.reg.fact_sections(NEW_ID).get("voyager2", {}))

    def test_spot_redo_refusals(self):
        with self.assertRaises(rv2.Voyager2Refused):
            rv2.spot_redo(NEW_ID, self.reg, ACTOR, "LO")
        self.reg.set_facts(IDS[1], "voyager2", {"reserved_by": "batch_v2_20261003-090000/turn001"}, "voyager2:LO")
        with self.assertRaises(rv2.Voyager2Refused) as ctx:
            rv2.spot_redo(IDS[0], self.reg, ACTOR, "LO")
        self.assertIn("batch_v2_20261003-090000/turn001", str(ctx.exception))
        self.reg.set_facts(IDS[1], "voyager2", {"reserved_by": ""}, "carousel")
        self.reg.upsert(IDS[1], {"step2_status": "running"}, "3D_phase2")
        with self.assertRaises(rv2.Voyager2Refused):
            rv2.spot_redo(IDS[0], self.reg, ACTOR, "LO")
        with self.assertRaises(ValueError):
            rv2.spot_redo(IDS[0], self.reg, ACTOR, "L")


if __name__ == "__main__":
    unittest.main()
