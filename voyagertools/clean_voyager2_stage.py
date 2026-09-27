#!/usr/bin/env python3
"""
Module: voyagertools/clean_voyager2_stage.py
Purpose: The Voyager 2 stage part of the clean workbench tool (program D
         Amendment 1, A1.12 item 21): find the copies a Voyager 2 turn left
         on the Voyager 2 stage, decide which may go, and delete one,
         anchored inside the stage, with one processing_log row per deletion.
         A working copy ({SITE}_{T#}_3D, the pulled processing folder that
         Voyager 2 opened and saved) never matches the Shelf, so it may go
         once the turn's deposit verified: every row it served reads step 2
         complete, the Shelf output location with its verified stamp and the
         Shelf processing location, and no turn reserves the transect. An
         output folder ({SITE}_{T#}_3doutput) may go only when it matches
         its Shelf copy file for file by name and size. A test run's copy
         (under test_<YYYYMMDD-HHMMSS>/) is listed and never offered, because
         the test run's own clean step removes it.
Inputs:  the Voyager 2 stage root (nas.yaml defaults.voyager2_stage_root, read
         by the caller), registry rows, a facts reader (registry.fact_sections),
         a local and a remote snapshot function (the clean_workbench shapes), a
         liveness probe (registry.run_is_live), an rmtree function.
Outputs: plans as plain dicts, deletions on disk, processing_log rows.
"""
from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Mapping
from datetime import datetime

from . import clean_workbench as cw

OUTPUT_FOLDER_NAME = re.compile(r"^[A-Za-z0-9]+_T[0-9]+_3doutput\Z")
TEST_FOLDER_NAME = re.compile(r"^test_\d{8}-\d{6}\Z")
PROCESSING_SUFFIX = "_3D"
# <stage>/<batch_v2_stamp>/turnNNN/<folder> is three levels down; a test copy
# sits at <stage>/test_<stamp>/<folder>.
MAX_WALK_DEPTH = 3
KIND_WORKING_COPY = "working copy"
KIND_OUTPUT = "output folder"
STEP2_COMPLETE = "complete"
FACT_SECTION = "voyager2"
RESERVED_KEY = "reserved_by"
STATUS_TEST_COPY = "test run copy"
STATUS_RESERVED = "reserved by a Voyager 2 turn"
STATUS_NOT_DEPOSITED = "not deposited yet"
STATUS_DEPOSITED = "deposited and verified"
PLACE_WORDS = "Voyager 2 stage"


def _require_stage(stage_root) -> str:
    """The stage root as an absolute path without a trailing slash."""
    if not isinstance(stage_root, str):
        raise TypeError(f"the Voyager 2 stage must be a path string, got {type(stage_root).__name__}")
    if not stage_root.strip() or not os.path.isabs(stage_root):
        raise ValueError(f"the Voyager 2 stage must be an absolute path, got {stage_root!r}")
    return stage_root.rstrip("/") or "/"


def _kind_of(name: str) -> str | None:
    """KIND_WORKING_COPY, KIND_OUTPUT or None for a folder name."""
    if cw.PROCESSING_FOLDER_NAME.match(name):
        return KIND_WORKING_COPY
    if OUTPUT_FOLDER_NAME.match(name):
        return KIND_OUTPUT
    return None


def _is_test_path(path: str, stage_root: str) -> bool:
    """True when the first folder under the stage is a test run folder test_<YYYYMMDD-HHMMSS>."""
    rel = os.path.relpath(os.path.normpath(path), stage_root)
    return bool(TEST_FOLDER_NAME.match(rel.split(os.sep, 1)[0]))


def find_stage_folders(stage_root, listdir: Callable = os.listdir, isdir: Callable = os.path.isdir,
                       islink: Callable = os.path.islink) -> dict:
    """Every working copy and output folder on the Voyager 2 stage, at most MAX_WALK_DEPTH levels down.

    The walk never enters a found folder, skips hidden names and symlinked
    directories, and stops after clean_workbench.MAX_WALK_ENTRIES entries.

    Returns:
        {"folders": [{"path", "name", "kind", "test"}] sorted by path, "problems": [sentences]}.
    """
    root = _require_stage(stage_root)
    folders, problems = [], []
    if not isdir(root):
        return {"folders": [], "problems": [f"The Voyager 2 stage {root} does not exist or is not a directory."]}
    budget = [cw.MAX_WALK_ENTRIES]
    pending = [(root, 0)]
    while pending and budget[0] > 0:
        folder, depth = pending.pop()
        try:
            names = sorted(listdir(folder))
        except OSError as exc:
            problems.append(f"Could not list {folder}: {exc}.")
            continue
        for name in names:
            budget[0] -= 1
            if budget[0] <= 0:
                problems.append(f"Stopped after {cw.MAX_WALK_ENTRIES} entries under {root}; the tree is too large.")
                break
            path = os.path.join(folder, name)
            if name.startswith(".") or islink(path) or not isdir(path):
                continue
            kind = _kind_of(name)
            if kind:
                folders.append({"path": path, "name": name, "kind": kind, "test": _is_test_path(path, root)})
            elif depth + 1 < MAX_WALK_DEPTH:
                pending.append((path, depth + 1))
    folders.sort(key=lambda f: f["path"])
    return {"folders": folders, "problems": problems}


def _text(row: Mapping, column: str) -> str:
    """A row cell as stripped text."""
    value = row.get(column)
    return "" if value is None else str(value).strip()


def _reservation(rows, facts_fn: Callable) -> str:
    """The first Voyager 2 reservation owner any row carries, or ""."""
    for row in rows:
        facts = facts_fn(_text(row, "readable_id")) or {}
        value = ((facts.get(FACT_SECTION) or {}).get(RESERVED_KEY) or {}).get("value")
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def rows_for_output(name: str, rows) -> list[dict]:
    """The registry rows that name this output folder: by output_folder, or by the last segment of output_location."""
    matched = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        _host, location = cw.split_location(row.get("output_location"))
        if _text(row, "output_folder") == name or os.path.basename(location.rstrip("/")) == name:
            matched.append(dict(row))
    return matched


def shelf_output_for(rows) -> str | None:
    """The one Shelf output path every row with an output location agrees on, or None."""
    paths, local = set(), False
    for row in rows:
        host, path = cw.split_location(row.get("output_location"))
        if host and path:
            paths.add(path)
        elif path:
            local = True
    return paths.pop() if len(paths) == 1 and not local else None


def deposit_gap(row: Mapping) -> str:
    """Why this row's Voyager 2 turn has not deposited and verified yet, or "" when it has."""
    rid = _text(row, "readable_id")
    if _text(row, "step2_status") != STEP2_COMPLETE:
        return f"{rid} reads step 2 {_text(row, 'step2_status') or 'blank'}, not complete"
    if not cw.split_location(row.get("output_location"))[0]:
        return f"{rid} has no Shelf output location yet"
    if not _text(row, "output_location_verified"):
        return f"{rid} carries no verified stamp for its Shelf output"
    if not cw.split_location(row.get("processing_location"))[0]:
        return f"{rid} still points its processing location at {_text(row, 'processing_location') or 'nothing'}"
    return ""


def _blank_plan(item: Mapping, rows) -> dict:
    """The plan skeleton for one found folder."""
    return {"path": item["path"], "name": item["name"], "kind": item["kind"],
            "readable_ids": [_text(r, "readable_id") for r in rows], "shelf_path": None, "status": "",
            "mismatches": [], "files": 0, "bytes": 0, "deletable": False, "detail": ""}


def _plan_working_copy(plan: dict, rows) -> dict:
    """Finish the plan of a working copy: disposable once every row it served deposited and verified."""
    served = [r for r in rows if _text(r, "step2_status")]
    plan["readable_ids"] = [_text(r, "readable_id") for r in served] or plan["readable_ids"]
    gaps = [deposit_gap(r) for r in served]
    gaps = [g for g in gaps if g] or ([] if served else ["no row of this transect carries a Voyager 2 record"])
    if gaps:
        plan.update(status=STATUS_NOT_DEPOSITED, detail=f"{gaps[0]}; keep this working copy.")
        return plan
    plan["shelf_path"] = shelf_output_for(served)
    plan.update(status=STATUS_DEPOSITED, deletable=True,
                detail="Voyager 2 deposited and verified this transect's products on the Shelf; this working copy can go.")
    return plan


def _plan_output(plan: dict, rows, local_snapshot_fn: Callable, remote_snapshot_fn: Callable) -> dict:
    """Finish the plan of an output folder: deletable only when it matches its Shelf copy."""
    shelf_path = shelf_output_for([r for r in rows if _text(r, "output_location")])
    plan["shelf_path"] = shelf_path
    if shelf_path is None:
        plan.update(status=cw.STATUS_NOT_SHELVED,
                    detail="The registry does not place this output folder on the Shelf, so there is no copy to compare.")
        return plan
    try:
        result = cw.compare_snapshots(local_snapshot_fn(plan["path"]), remote_snapshot_fn(shelf_path))
    except Exception as exc:  # noqa: BLE001  (an ssh or disk failure is a line in the table, not a crash)
        plan.update(status=cw.STATUS_ERROR, detail=f"Could not compare the two copies: {exc}")
        return plan
    plan.update(mismatches=result["mismatches"], files=result["files"], bytes=result["bytes"],
                status=cw.STATUS_IDENTICAL if result["identical"] else cw.STATUS_DIFFERS,
                deletable=result["identical"])
    plan["detail"] = ("The Shelf copy matches file for file; the stage copy can go." if result["identical"]
                      else f"{len(result['mismatches'])} difference(s); keep the stage copy.")
    return plan


def _live_paths(item: Mapping) -> list[str]:
    """The folders whose lock says a run is using this item: the item, and for an output folder its working copy."""
    paths = [item["path"]]
    if item["kind"] == KIND_OUTPUT:
        paths.append(os.path.join(os.path.dirname(item["path"]), item["name"][:-len("_3doutput")] + PROCESSING_SUFFIX))
    return paths


def plan_item(item: Mapping, rows, facts_fn: Callable, local_snapshot_fn: Callable, remote_snapshot_fn: Callable,
              is_live_fn: Callable) -> dict:
    """What the tool knows about one found stage folder and whether it may be deleted.

    Parameters:
        item: a find_stage_folders entry.
        rows: every registry row.
        facts_fn(readable_id) -> {section: {key: {"value": ...}}}.
        local_snapshot_fn, remote_snapshot_fn, is_live_fn: as in clean_workbench.plan_folder.

    Returns:
        {"path", "name", "kind", "readable_ids", "shelf_path", "status",
         "mismatches", "files", "bytes", "deletable", "detail"}.
    """
    matched = (cw.rows_for_folder(item["path"], rows) if item["kind"] == KIND_WORKING_COPY
               else rows_for_output(item["name"], rows))
    plan = _blank_plan(item, matched)
    if item.get("test"):
        plan.update(status=STATUS_TEST_COPY,
                    detail="A Voyager 2 test run made this copy; its own clean step removes it after its deposit verifies.")
        return plan
    if not matched:
        plan.update(status=cw.STATUS_NO_ROW, detail="No registry row names this folder; look at it by hand first.")
        return plan
    owner = _reservation(matched, facts_fn)
    if owner:
        plan.update(status=STATUS_RESERVED,
                    detail=f"Voyager 2 turn {owner} holds this transect; finish or release it from the driver page first.")
        return plan
    if any(is_live_fn({"processing_location": path}) for path in _live_paths(item)):
        plan.update(status=cw.STATUS_LIVE, detail="A process holds this transect's lock; wait for it to finish.")
        return plan
    if item["kind"] == KIND_WORKING_COPY:
        return _plan_working_copy(plan, matched)
    return _plan_output(plan, matched, local_snapshot_fn, remote_snapshot_fn)


def plan(stage_root, rows, facts_fn: Callable, local_snapshot_fn: Callable, remote_snapshot_fn: Callable,
         is_live_fn: Callable, find_fn: Callable = find_stage_folders) -> dict:
    """The Voyager 2 stage table: one plan per working copy and output folder.

    Returns:
        {"root": the stage root, "folders": [plan_item dicts], "problems": [sentences]}.
    """
    root = _require_stage(stage_root)
    found = find_fn(root)
    plans = [plan_item(item, rows, facts_fn, local_snapshot_fn, remote_snapshot_fn, is_live_fn)
             for item in found["folders"]]
    return {"root": root, "folders": plans, "problems": found["problems"]}


def anchored(path, stage_root) -> tuple[str, str]:
    """(real path, kind) of a working copy or output folder strictly inside the stage and outside every test folder.

    Raises:
        TypeError: when path is not a string.
        ValueError: when the path is relative, a symlink, the stage itself or
            outside it, inside a test run folder, not a directory, or not named
            like a working copy or an output folder (each names the reason).
    """
    if not isinstance(path, str):
        raise TypeError(f"path must be a string, got {type(path).__name__}")
    if not path.strip() or not os.path.isabs(path):
        raise ValueError(f"the folder to delete must be an absolute path, got {path!r}")
    if os.path.islink(path.rstrip("/")):
        raise ValueError(f"{path} is a symlink; only a real folder on the Voyager 2 stage can be deleted")
    real, root = os.path.realpath(path), os.path.realpath(_require_stage(stage_root))
    if real == root or not real.startswith(root.rstrip("/") + "/"):
        raise ValueError(f"{path} is not inside the Voyager 2 stage {root}")
    if _is_test_path(real, root):
        raise ValueError(f"{path} belongs to a Voyager 2 test run, whose own clean step removes it")
    kind = _kind_of(os.path.basename(real))
    if kind is None:
        raise ValueError(f"{path} is neither a working copy ({{SITE}}_{{T#}}_3D) nor an output folder "
                         f"({{SITE}}_{{T#}}_3doutput)")
    if not os.path.isdir(real):
        raise ValueError(f"{path} is not a directory")
    return real, kind


def delete_folder(path, stage_root, is_live_fn: Callable, rmtree_fn: Callable = shutil.rmtree) -> dict:
    """Remove one working copy or output folder from the Voyager 2 stage, anchored and not live.

    Returns:
        {"deleted": real path, "bytes": size counted before the delete, "kind"}.

    Raises:
        ValueError: from anchored(), or when a process holds the transect's lock.
        OSError: when the removal fails (names the folder).
    """
    real, kind = anchored(path, stage_root)
    item = {"path": real, "name": os.path.basename(real), "kind": kind}
    if any(is_live_fn({"processing_location": p}) for p in _live_paths(item)):
        raise ValueError(f"{real} is in use by a run that is processing right now; deleting is blocked until it stops.")
    size = cw.folder_bytes(real)
    try:
        rmtree_fn(real)
    except OSError as exc:
        raise OSError(f"could not delete {real}: {exc}") from exc
    return {"deleted": real, "bytes": size, "kind": kind}


def processing_log_row(path: str, shelf_path: str | None, readable_ids, initials: str, size_bytes: int, kind: str,
                       now: Callable[[], str] | None = None) -> dict:
    """The processing_log row for one Voyager 2 stage deletion, shaped over clean_workbench.PROCESSING_LOG_COLUMNS."""
    stamp = now() if now else datetime.now(cw.AST).isoformat(timespec="seconds")
    ids = ", ".join(str(r) for r in readable_ids) or "no registry row"
    why = ("after the Voyager 2 deposit of its products verified" if kind == KIND_WORKING_COPY
           else "after a name-and-size match with the Shelf copy")
    return {
        "timestamp": stamp, "module": cw.LOG_MODULE, "origin": cw.LOG_ORIGIN,
        "purpose": f"clean {PLACE_WORDS} by {initials}: {ids}", "status": cw.LOG_STATUS, "duration_s": "",
        "source_uri": path, "destination_uri": shelf_path or "",
        "notes": f"{kind} removed from the {PLACE_WORDS} {why} ({size_bytes} bytes)",
    }


__all__ = [
    "OUTPUT_FOLDER_NAME", "TEST_FOLDER_NAME", "MAX_WALK_DEPTH", "KIND_WORKING_COPY", "KIND_OUTPUT",
    "STATUS_TEST_COPY", "STATUS_RESERVED", "STATUS_NOT_DEPOSITED", "STATUS_DEPOSITED", "find_stage_folders",
    "rows_for_output", "shelf_output_for", "deposit_gap", "plan_item", "plan", "anchored", "delete_folder",
    "processing_log_row",
]
