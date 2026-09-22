#!/usr/bin/env python3
"""
Module: voyagertools/rewind.py
Purpose: The rewind and spot redo tools (program spec section 7, tools 4 and
         5): take one registry row back to before step 1 (the reconstruction)
         or before step 0 (the frame extraction). A dry run lists what would
         change; apply moves the step's artifacts into
         <processing folder>/_rewind/<stamp>/, removes the timepoint's chunk
         from the Metashape project through scripts/rewind_chunk.py, clears
         the step's cells in status.csv, and resets the step's registry cells
         through registry.upsert so every cell leaves an event. Spot redo is
         the same rewind before step 1 plus a voyager1 fact "spot_redo" that
         makes the row eligible again in the Voyager 1 setup window.
Inputs:  a registry row, the target, the registry library, an actor, a chunk
         removal function (the real one runs metashape -r; tests inject the
         fake mode), a liveness probe.
Outputs: a plan dict; on apply the moved artifacts, the manifest
         <folder>/_rewind/<stamp>/rewind.json, the registry events.

Refusals: no such row; the row is processing right now (its lock is held);
the processing folder is not on this machine (the Carousel's pull-back
brings it down; the command is shown, never run here); the row sits in the
manual edit job; nothing to rewind.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path

AST = timezone(timedelta(hours=-4))
TARGET_BEFORE_STEP1 = "before_step1"
TARGET_BEFORE_STEP0 = "before_step0"
TARGETS = (TARGET_BEFORE_STEP1, TARGET_BEFORE_STEP0)
TARGET_WORDS = {TARGET_BEFORE_STEP1: "before step 1", TARGET_BEFORE_STEP0: "before step 0"}
REWIND_DIR = "_rewind"
FRAMES_DIR = "frames"
REPORTS_DIR = "reports"
REPORT_GLOB = "{rid}_step1*.pdf"
STATUS_CSV = "status.csv"
MANIFEST_NAME = "rewind.json"
CHUNK_RESULT_NAME = "rewind_chunk.json"
TMP_SUFFIX = ".tmp"
PSX_SUFFIX = ".psx"
LOCATION_HOST_SEPARATOR = ":"
RUNNING_PHRASE = "processing right now"
PULLBACK_URL = "http://127.0.0.1:5093/api/pullback"
# The registry cells step 1 writes (registry_success and the stage calls),
# every one reset to blank by a rewind before step 1. console_log and
# snapshot_dir name step 1 artifacts, so they go too; the snapshot folder
# under the registry root stays on disk as the audit copy.
STEP1_REGISTRY_CELLS = (
    "psx_file", "step1_status", "step1_started", "step1_finished", "step1_seconds", "manual_edit_status",
    "manual_edit_done", "scale_status", "scale_error_mm", "scale_error_ppm", "scale_bars", "tie_points",
    "faces_full", "faces_delivery", "texture_pages", "dem_mm_per_pix", "processing_size_gb", "sizes_verified",
    "snapshot_dir", "params_summary", "console_log", "step", "stage", "stage_started",
)
# Step 0 writes the stage cells (reset above) and fills blank video facts,
# which are ingest facts and stay; nothing more to reset in the registry.
STEP0_REGISTRY_CELLS: tuple = ()
STATUS_ID_COLUMNS = ("Model ID", "readable_id")
STATUS_STEP1_CELLS = {
    "Step 1 complete": "False", "Status": "Rewound before step 1", "Step 1 start time": "", "Step 1 end time": "",
    "Step 1 processing time (s)": "", "Aligned cameras": "", "Total cameras": "", "PSX file": "", "Report file": "",
    "Step 1 error time": "", "Scale": "", "Scale Error (m)": "", "Scale Error (ppm)": "", "Scale Bars": "",
    "Cameras Removed": "",
}
STATUS_STEP0_CELLS = {
    "Step 0 complete": "False", "Status": "Rewound before step 0", "Frames Extracted": "", "Extraction Timestamp": "",
    "Step 0 start time": "", "Step 0 end time": "", "Step 0 processing time (s)": "", "Frames directory": "",
    "Step 0 error time": "",
}
INACTIVE_STAGES = ("", "done", "failed")
SPOT_REDO_SECTION = "voyager1"
SPOT_REDO_KEY = "spot_redo"
METASHAPE_ENV = "METASHAPE_PATH"
METASHAPE_SEARCH_PATHS = ("/home/bizon/applications/metashape-pro_2_2_2_amd64/metashape-pro/metashape",)
METASHAPE_COMMAND = "metashape"
REWIND_CHUNK_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rewind_chunk.py"
CHUNK_TIMEOUT_S = 1800


class RewindRefused(ValueError):
    """The rewind cannot run; the message says why and, when a pull-back is the way, shows the command."""


def now_iso() -> str:
    """AST timestamp with its offset."""
    return datetime.now(AST).isoformat(timespec="seconds")


def now_compact() -> str:
    """Compact AST stamp for the rewind folder name."""
    return datetime.now(AST).strftime("%Y%m%d-%H%M%S")


def target_words(target: str) -> str:
    """"before step 1" for a target; refuses an unknown one."""
    if target not in TARGETS:
        raise ValueError(f"target must be one of {', '.join(TARGETS)}, got {target!r}")
    return TARGET_WORDS[target]


def is_local_folder(location) -> bool:
    """True when the processing location is a plain path that exists as a directory on this machine."""
    text = str(location or "").strip()
    if not text or not os.path.isabs(text):
        return False
    return os.path.isdir(text)


def is_remote_location(location) -> bool:
    """True when the location carries a host prefix ("146.226.147.140:/path"): the folder is on the NAS."""
    text = str(location or "").strip()
    head = text.split(LOCATION_HOST_SEPARATOR, 1)[0] if LOCATION_HOST_SEPARATOR in text else ""
    return bool(head) and not text.startswith("/")


def pullback_command(readable_id: str) -> str:
    """The Carousel call that brings a shelved transect down to the Workbench (shown, never run here)."""
    body = json.dumps({"target": readable_id})
    return f"curl -X POST {PULLBACK_URL} -H 'Content-Type: application/json' -d '{body}'"


def row_started(row: Mapping) -> bool:
    """True when Voyager 1 has touched the row: step1_status set, or a stage that is not inactive."""
    if str(row.get("step1_status") or "").strip():
        return True
    return str(row.get("stage") or "").strip() not in INACTIVE_STAGES


def refusal(row, target: str, is_live_fn: Callable, active_job_ids: Iterable[str] = ()) -> str:
    """Why the row cannot be rewound now, or "" when it can.

    Parameters:
        row: the registry row, or None when the id had none.
        target: one of TARGETS.
        is_live_fn: registry.run_is_live.
        active_job_ids: readable ids in the current manual edit job.
    """
    target_words(target)
    if not isinstance(row, Mapping):
        return "No registry row has this id."
    rid = str(row.get("readable_id") or "")
    if is_live_fn(row):
        return f"{rid} is {RUNNING_PHRASE}; the rewind is blocked until the run stops."
    if rid in set(active_job_ids or ()):
        return f"{rid} is in the manual edit job; cancel the job in Clean manual edit run first."
    location = str(row.get("processing_location") or "").strip()
    if not location:
        return f"{rid} has no processing folder recorded, so there is nothing to rewind."
    if is_remote_location(location) or not is_local_folder(location):
        return (f"The processing folder of {rid} is not on this machine ({location}); bring it down with the "
                f"Carousel pull-back first: {pullback_command(rid)}")
    return ""


def _psx_path(row: Mapping, folder: str) -> str:
    """The absolute psx path the row records, or "" when none is recorded."""
    text = str(row.get("psx_file") or "").strip()
    if not text:
        return ""
    return text if os.path.isabs(text) else os.path.join(folder, text)


def _report_paths(folder: str, rid: str) -> list[str]:
    """Every step 1 report PDF of the row under <folder>/reports/."""
    return sorted(glob.glob(os.path.join(folder, REPORTS_DIR, REPORT_GLOB.format(rid=glob.escape(rid)))))


def registry_resets(row: Mapping, target: str) -> dict[str, str]:
    """{cell: old value} for every step cell of the target that is not already blank."""
    cells = STEP1_REGISTRY_CELLS + (STEP0_REGISTRY_CELLS if target == TARGET_BEFORE_STEP0 else ())
    return {cell: str(row.get(cell) or "") for cell in cells if str(row.get(cell) or "").strip()}


def status_resets(target: str) -> dict[str, str]:
    """The status.csv cells the target clears, in one dict."""
    cells = dict(STATUS_STEP1_CELLS)
    if target == TARGET_BEFORE_STEP0:
        cells.update(STATUS_STEP0_CELLS)
    return cells


def plan(row: Mapping, target: str, stamp: str | None = None) -> dict:
    """What a rewind would do, without doing it (the dry run).

    Parameters:
        row: a registry row whose processing folder is local (see refusal()).
        target: one of TARGETS.
        stamp: the rewind folder stamp; None means now.

    Returns:
        {"readable_id", "target", "target_words", "folder", "stamp", "rewind_dir",
         "moves": [{"from", "to", "kind"}], "chunk": {"psx", "present"} or None,
         "registry_resets": {cell: old}, "status_resets": [cells], "warnings": [...],
         "empty": True when nothing at all would change}.
    """
    if not isinstance(row, Mapping):
        raise TypeError(f"row must be a registry row dict, got {type(row).__name__}")
    words = target_words(target)
    rid = str(row.get("readable_id") or "")
    folder = str(row.get("processing_location") or "").strip()
    stamp = stamp or now_compact()
    rewind_dir = os.path.join(folder, REWIND_DIR, stamp)
    moves, warnings = [], []
    for report in _report_paths(folder, rid):
        moves.append({"from": report, "to": os.path.join(rewind_dir, REPORTS_DIR, os.path.basename(report)),
                      "kind": "report"})
    if target == TARGET_BEFORE_STEP0:
        frames = os.path.join(folder, FRAMES_DIR, rid)
        if os.path.isdir(frames):
            moves.append({"from": frames, "to": os.path.join(rewind_dir, FRAMES_DIR, rid), "kind": "frames"})
        else:
            warnings.append(f"No frames folder at {frames}; nothing to move for step 0.")
    psx = _psx_path(row, folder)
    chunk = None
    if not psx:
        warnings.append("No psx is recorded on this row, so no chunk is removed from a project.")
    else:
        chunk = {"psx": psx, "present": os.path.exists(psx)}
        if not chunk["present"]:
            warnings.append(f"The recorded psx {psx} is not on disk; no chunk is removed.")
    if str(row.get("manual_edit_status") or "") == "done":
        warnings.append("This row was manually edited; the hand edits inside its chunk go with the rewind.")
    resets = registry_resets(row, target)
    empty = not moves and not (chunk and chunk["present"]) and not resets
    return {"readable_id": rid, "target": target, "target_words": words, "folder": folder, "stamp": stamp,
            "rewind_dir": rewind_dir, "moves": moves, "chunk": chunk, "registry_resets": resets,
            "status_resets": sorted(status_resets(target)), "warnings": warnings, "empty": empty}


def reset_status_csv(folder: str, readable_id: str, cells: Mapping[str, str]) -> bool:
    """Clear the row's step cells in <folder>/status.csv, atomically; False when the file or row is absent.

    Only cells present in the header are written (an older header is left as
    it is); the row is found by Model ID or readable_id.

    Raises:
        OSError: when the file cannot be read or rewritten (names the path).
    """
    path = os.path.join(folder, STATUS_CSV)
    if not os.path.exists(path):
        return False
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            header = list(reader.fieldnames or [])
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise OSError(f"could not read {path}: {exc}") from exc
    if not header:
        return False
    hit = False
    for line in rows:
        if any(str(line.get(col) or "") == readable_id for col in STATUS_ID_COLUMNS if col in header):
            for cell, value in cells.items():
                if cell in header:
                    line[cell] = value
            hit = True
    if not hit:
        return False
    tmp = path + TMP_SUFFIX
    try:
        with open(tmp, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore", restval="")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, path)
    except OSError as exc:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise OSError(f"could not rewrite {path}: {exc}") from exc
    return True


def _move_all(moves: list[dict], move_fn: Callable) -> list[dict]:
    """Move every artifact into the rewind folder; stops at the first failure with what moved so far."""
    done = []
    for move in moves:
        try:
            os.makedirs(os.path.dirname(move["to"]), exist_ok=True)
            move_fn(move["from"], move["to"])
        except OSError as exc:
            raise OSError(f"could not move {move['from']} to {move['to']} ({exc}); "
                          f"{len(done)} artifact(s) already moved into {os.path.dirname(move['to'])}") from exc
        done.append(move)
    return done


def apply(row: Mapping, target: str, registry_mod, actor: str, run_chunk_fn: Callable, is_live_fn: Callable,
          active_job_ids: Iterable[str] = (), stamp: str | None = None, move_fn: Callable = shutil.move) -> dict:
    """Carry out a rewind (spec section 7, tool 4).

    Order: the refusals; the plan (an empty plan is refused); the chunk
    removal through run_chunk_fn(psx, label, rewind_dir) -> the
    rewind_chunk.py result document (an error there stops everything before
    any file moves); the artifact moves into the rewind folder; the
    status.csv resets; the registry resets through registry.upsert (one
    event per cell); the manifest.

    Parameters:
        row: the registry row.
        target: one of TARGETS.
        registry_mod: the registry library module.
        actor: "voyager_tools:<INITIALS>".
        run_chunk_fn: see above; called only when a psx is recorded and present.
        is_live_fn: registry.run_is_live.
        active_job_ids: ids in the current manual edit job.
        stamp: the rewind folder stamp (None means now).
        move_fn: injectable for tests.

    Returns:
        The manifest dict, also written to <rewind_dir>/rewind.json.

    Raises:
        RewindRefused: for every refusal, including a chunk removal error.
        OSError: when a move or a status.csv rewrite fails.
    """
    why = refusal(row, target, is_live_fn, active_job_ids)
    if why:
        raise RewindRefused(why)
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor must be a non-blank string")
    the_plan = plan(row, target, stamp)
    if the_plan["empty"]:
        raise RewindRefused(f"{the_plan['readable_id']} has nothing to rewind {the_plan['target_words']}: "
                            f"no artifacts, no chunk and no registry cells to reset.")
    rewind_dir = the_plan["rewind_dir"]
    os.makedirs(rewind_dir, exist_ok=True)
    chunk_result = None
    if the_plan["chunk"] and the_plan["chunk"]["present"]:
        chunk_result = run_chunk_fn(the_plan["chunk"]["psx"], the_plan["readable_id"], rewind_dir)
        if not isinstance(chunk_result, Mapping) or chunk_result.get("error"):
            detail = chunk_result.get("error") if isinstance(chunk_result, Mapping) else "no result"
            raise RewindRefused(f"the chunk could not be removed from {the_plan['chunk']['psx']}: {detail}")
    chunk_removed = bool(chunk_result and chunk_result.get("removed"))
    try:
        moved = _move_all(the_plan["moves"], move_fn)
        status_done = reset_status_csv(the_plan["folder"], the_plan["readable_id"], status_resets(target))
    except OSError as exc:
        if chunk_removed:
            # The chunk removal already saved before this step failed, so the
            # project no longer matches the registry; say so, because the
            # generic move or status.csv message alone would leave an
            # operator believing nothing happened and the row still safe.
            raise OSError(f"{exc}; the chunk was already removed from {the_plan['chunk']['psx']} and saved, so "
                          f"the project and the registry now disagree until this rewind is run again "
                          f"(a repeat run finds the chunk already gone and finishes the rest)") from exc
        raise
    resets = {cell: "" for cell in the_plan["registry_resets"]}
    if resets:
        registry_mod.upsert(the_plan["readable_id"], resets, actor)
    manifest = {
        "readable_id": the_plan["readable_id"], "target": target, "target_words": the_plan["target_words"],
        "folder": the_plan["folder"], "stamp": the_plan["stamp"], "rewind_dir": rewind_dir, "actor": actor,
        "at": now_iso(), "moves": moved, "chunk": dict(chunk_result) if chunk_result else None,
        "registry_resets": the_plan["registry_resets"], "status_csv_reset": status_done,
        "warnings": the_plan["warnings"],
    }
    manifest_path = os.path.join(rewind_dir, MANIFEST_NAME)
    try:
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
    except OSError as exc:
        raise OSError(f"the rewind ran but its manifest could not be written at {manifest_path}: {exc}") from exc
    return manifest


def spot_redo_fact(initials: str, stamp: str | None = None) -> dict[str, str]:
    """The voyager1 fact that marks a row for a spot redo: {"spot_redo": "requested <ISO> by <INITIALS>"}."""
    return {SPOT_REDO_KEY: f"requested {stamp or now_iso()} by {initials}"}


def spot_redo(row: Mapping, registry_mod, actor: str, initials: str, run_chunk_fn: Callable, is_live_fn: Callable,
              active_job_ids: Iterable[str] = (), stamp: str | None = None, move_fn: Callable = shutil.move) -> dict:
    """A rewind before step 1 plus the spot_redo fact (spec section 7, tool 5).

    Returns:
        The rewind manifest with "spot_redo" holding the fact written.
    """
    manifest = apply(row, TARGET_BEFORE_STEP1, registry_mod, actor, run_chunk_fn, is_live_fn, active_job_ids,
                     stamp, move_fn)
    fact = spot_redo_fact(initials)
    registry_mod.set_facts(manifest["readable_id"], SPOT_REDO_SECTION, fact, actor)
    manifest["spot_redo"] = fact
    return manifest


def metashape_binary(environ: Mapping | None = None, which: Callable = shutil.which,
                     exists: Callable = os.path.exists) -> str | None:
    """The Metashape binary: $METASHAPE_PATH, then the known install, then PATH; None when nowhere."""
    env = os.environ if environ is None else environ
    candidate = str(env.get(METASHAPE_ENV) or "").strip()
    if candidate and exists(candidate):
        return candidate
    for path in METASHAPE_SEARCH_PATHS:
        if exists(path):
            return path
    return which(METASHAPE_COMMAND)


def run_chunk_script(psx: str, label: str, out_dir: str, metashape_bin: str | None = None, fake: bool = False,
                     runner: Callable = subprocess.run, python: str = sys.executable,
                     timeout_s: int = CHUNK_TIMEOUT_S) -> dict:
    """Run scripts/rewind_chunk.py and return its result document.

    Real mode: <metashape> -r rewind_chunk.py <psx> --label <label> --out <out_dir>/rewind_chunk.json.
    Fake mode (tests): <python> rewind_chunk.py <psx> ... --fake.

    Returns:
        The JSON document the script wrote; a document with "error" set when
        the binary is missing, the run failed, timed out, or wrote no result.
    """
    out_path = os.path.join(out_dir, CHUNK_RESULT_NAME)
    if fake:
        argv = [python, str(REWIND_CHUNK_SCRIPT), psx, "--label", label, "--out", out_path, "--fake"]
    else:
        binary = metashape_bin or metashape_binary()
        if not binary:
            return {"psx": psx, "label": label, "error": f"Metashape was not found; set {METASHAPE_ENV} to its binary",
                    "removed": 0, "saved": False}
        argv = [binary, "-r", str(REWIND_CHUNK_SCRIPT), psx, "--label", label, "--out", out_path]
    try:
        completed = runner(argv, capture_output=True, text=True, timeout=timeout_s)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"psx": psx, "label": label, "error": f"rewind_chunk.py could not run: {exc}", "removed": 0,
                "saved": False}
    try:
        with open(out_path, encoding="utf-8") as fh:
            document = json.load(fh)
    except (OSError, ValueError):
        stderr = (getattr(completed, "stderr", "") or "").strip()
        return {"psx": psx, "label": label, "removed": 0, "saved": False,
                "error": f"rewind_chunk.py wrote no result (exit {getattr(completed, 'returncode', '?')}): {stderr}"}
    if not isinstance(document, dict):
        return {"psx": psx, "label": label, "removed": 0, "saved": False, "error": "rewind_chunk.py result is not a JSON object"}
    return document


__all__ = [
    "TARGETS", "TARGET_BEFORE_STEP1", "TARGET_BEFORE_STEP0", "TARGET_WORDS", "REWIND_DIR", "STEP1_REGISTRY_CELLS",
    "STATUS_STEP1_CELLS", "STATUS_STEP0_CELLS", "SPOT_REDO_SECTION", "SPOT_REDO_KEY", "REWIND_CHUNK_SCRIPT",
    "RewindRefused", "now_iso", "now_compact", "target_words", "is_local_folder", "is_remote_location",
    "pullback_command", "row_started", "refusal", "registry_resets", "status_resets", "plan", "reset_status_csv",
    "apply", "spot_redo_fact", "spot_redo", "metashape_binary", "run_chunk_script",
]
