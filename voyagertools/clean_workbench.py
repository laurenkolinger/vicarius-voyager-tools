#!/usr/bin/env python3
"""
Module: voyagertools/clean_workbench.py
Purpose: The clean workbench tool (program spec section 7, tool 2): find
         every processing folder left under the Workbench roots, match each
         to the registry rows whose processing location is on the Shelf,
         compare the local copy to the Shelf copy by file name and size, and
         delete a local copy only when the two are identical, anchored inside
         a Workbench root, with one processing_log row per deletion.
Inputs:  the Workbench roots from the Carousel's nas.yaml, registry rows,
         a local snapshot function (driver.verify.local_snapshot shape) and a
         remote snapshot function (driver.remote.snapshot shape), a liveness
         probe (registry.run_is_live), an rmtree function.
Outputs: plans as plain dicts, deletions on disk, processing_log rows.

Snapshot shape (both sides): {relative path: {"kind": "f", "size": bytes} |
{"kind": "l", "target": text} | {"kind": "s"}}. The processing lock file is
left out of the comparison on both sides, as the Carousel does, because it
is a control file and never folder content.
"""
from __future__ import annotations

import csv
import fcntl
import io
import os
import re
import shutil
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone

# A processing folder is named {SITE}_{T#}_3D (MRS_T1_3D). Until 2026-09-08 it
# carried a season and ended "_3dprocessing", specific enough that a suffix test
# was a safe guard on its own; "_3D" is not, so this tests the whole shape.
# Mirrors naming3d.PROCESSING_FOLDER_NAME; the suite below pins the two equal.
PROCESSING_FOLDER_NAME = re.compile(r"^[A-Za-z0-9]+_T[0-9]+_3D\Z")
LOCK_NAME = ".processing.lock"
# How deep under a Workbench root the walk looks for processing folders:
# <root>/<batch>/turnNNN/<season>/<folder> is four levels down.
MAX_WALK_DEPTH = 4
# A walk that meets more entries than this stops and reports it, so a
# Workbench with a runaway tree never hangs the page.
MAX_WALK_ENTRIES = 50000
LOCATION_PATTERN = re.compile(r"^(?P<host>[A-Za-z0-9.\-]+):(?P<path>/.*)$")
AST = timezone(timedelta(hours=-4))

STATUS_IDENTICAL = "identical"
STATUS_DIFFERS = "differs"
STATUS_NO_ROW = "no registry row"
STATUS_NOT_SHELVED = "not on the Shelf"
STATUS_LIVE = "processing right now"
STATUS_ERROR = "error"

PROCESSING_LOG_COLUMNS = ["timestamp", "module", "origin", "purpose", "status", "duration_s",
                          "source_uri", "destination_uri", "notes"]
LOG_MODULE = "voyager_tools"
LOG_ORIGIN = "USER"
LOG_STATUS = "deleted"


def split_location(location) -> tuple[str | None, str]:
    """(host, path) for a "host:/path" location, (None, path) for a plain path, (None, "") for blank."""
    text = str(location or "").strip()
    match = LOCATION_PATTERN.match(text)
    if match:
        return match["host"], match["path"]
    return None, text


def _require_roots(roots) -> list[str]:
    """`roots` as a list of absolute path strings."""
    if isinstance(roots, (str, bytes)) or not isinstance(roots, Iterable):
        raise TypeError(f"workbench roots must be a list of absolute paths, got {type(roots).__name__}")
    out = []
    for root in roots:
        if not isinstance(root, str) or not os.path.isabs(root):
            raise ValueError(f"a Workbench root must be an absolute path, got {root!r}")
        out.append(root.rstrip("/") or "/")
    return out


def find_processing_folders(roots, listdir: Callable = os.listdir, isdir: Callable = os.path.isdir,
                            islink: Callable = os.path.islink) -> dict:
    """Every processing folder under the Workbench roots, at most MAX_WALK_DEPTH levels down.

    A processing folder is a directory whose name matches PROCESSING_FOLDER_NAME;
    the walk never enters one, skips hidden names and symlinked directories,
    and stops after MAX_WALK_ENTRIES entries.

    Parameters:
        roots: absolute Workbench roots (a missing root is reported, not raised).
        listdir, isdir, islink: injectable for tests.

    Returns:
        {"folders": [{"path", "name", "root"}] sorted by path, "problems": [sentences]}.
    """
    roots = _require_roots(roots)
    folders, problems = [], []
    budget = [MAX_WALK_ENTRIES]
    for root in roots:
        if not isdir(root):
            problems.append(f"Workbench root {root} does not exist or is not a directory.")
            continue
        _walk(root, root, 0, folders, problems, budget, listdir, isdir, islink)
    folders.sort(key=lambda f: f["path"])
    return {"folders": folders, "problems": problems}


def _walk(root, folder, depth, found, problems, budget, listdir, isdir, islink) -> None:
    """One level of the bounded walk (see find_processing_folders)."""
    if depth > MAX_WALK_DEPTH or budget[0] <= 0:
        return
    try:
        names = sorted(listdir(folder))
    except OSError as exc:
        problems.append(f"Could not list {folder}: {exc}.")
        return
    for name in names:
        budget[0] -= 1
        if budget[0] <= 0:
            problems.append(f"Stopped after {MAX_WALK_ENTRIES} entries under {root}; the tree is too large to scan.")
            return
        path = os.path.join(folder, name)
        if name.startswith(".") or islink(path) or not isdir(path):
            continue
        if PROCESSING_FOLDER_NAME.match(name):
            found.append({"path": path, "name": name, "root": root})
            continue
        _walk(root, path, depth + 1, found, problems, budget, listdir, isdir, islink)


def rows_for_folder(folder_path: str, rows) -> list[dict]:
    """The registry rows that name this processing folder: by processing_folder, or by the last path segment of processing_location."""
    name = os.path.basename(folder_path.rstrip("/"))
    matched = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        recorded = os.path.basename(str(row.get("processing_folder") or "").rstrip("/"))
        _host, location = split_location(row.get("processing_location"))
        if name and (recorded == name or os.path.basename(location.rstrip("/")) == name):
            matched.append(dict(row))
    return matched


def shelf_path_for(rows) -> str | None:
    """The Shelf path the matched rows record, or None when none of them is on the Shelf.

    Every matched row must agree; a disagreement is treated as not shelved
    (the caller shows the rows and lets a person look).
    """
    paths = set()
    for row in rows:
        host, path = split_location(row.get("processing_location"))
        if host and path:
            paths.add(path)
    if len(paths) == 1:
        return paths.pop()
    return None


def _size_of(entry: Mapping) -> int:
    """The byte size of a snapshot entry (0 for links and specials)."""
    size = entry.get("size", 0) if entry.get("kind") == "f" else 0
    return int(size) if isinstance(size, (int, float)) and size >= 0 else 0


def compare_snapshots(local, remote) -> dict:
    """Name-and-size comparison of two snapshots with the lock file left out of both.

    Returns:
        {"identical": bool, "mismatches": [sentences], "files": n, "bytes": n}.

    Raises:
        TypeError: when either snapshot is not a mapping.
    """
    if not isinstance(local, Mapping) or not isinstance(remote, Mapping):
        raise TypeError("snapshots must be mappings of relative path to entry")
    local = {k: v for k, v in local.items() if k != LOCK_NAME}
    remote = {k: v for k, v in remote.items() if k != LOCK_NAME}
    mismatches = []
    for rel in sorted(local):
        mine, theirs = local[rel], remote.get(rel)
        if theirs is None:
            mismatches.append(f"missing on the Shelf: {rel}")
        elif mine.get("kind") != theirs.get("kind"):
            mismatches.append(f"kind differs for {rel}: local {mine.get('kind')}, Shelf {theirs.get('kind')}")
        elif mine.get("kind") == "f" and _size_of(mine) != _size_of(theirs):
            mismatches.append(f"size differs for {rel}: local {_size_of(mine)}, Shelf {_size_of(theirs)}")
        elif mine.get("kind") == "l" and mine.get("target") != theirs.get("target"):
            mismatches.append(f"link target differs for {rel}")
    for rel in sorted(set(remote) - set(local)):
        mismatches.append(f"only on the Shelf: {rel}")
    files = [v for v in local.values() if v.get("kind") == "f"]
    return {"identical": not mismatches, "mismatches": mismatches, "files": len(files),
            "bytes": sum(_size_of(v) for v in files)}


def plan_folder(path: str, rows, local_snapshot_fn: Callable, remote_snapshot_fn: Callable,
                is_live_fn: Callable) -> dict:
    """What the tool knows about one processing folder and whether it may be deleted.

    Parameters:
        path: the local processing folder.
        rows: every registry row (matched here by folder name).
        local_snapshot_fn(path) -> snapshot; remote_snapshot_fn(shelf_path) -> snapshot.
        is_live_fn(row_like) -> bool: registry.run_is_live over {"processing_location": path}.

    Returns:
        {"path", "name", "readable_ids", "shelf_path", "status", "mismatches",
         "files", "bytes", "deletable", "detail"}; a snapshot failure is
         status "error" with the message in detail, never an exception.
    """
    matched = rows_for_folder(path, rows)
    ids = [str(r.get("readable_id") or "") for r in matched]
    plan = {"path": path, "name": os.path.basename(path.rstrip("/")), "readable_ids": ids, "shelf_path": None,
            "status": "", "mismatches": [], "files": 0, "bytes": 0, "deletable": False, "detail": ""}
    if not matched:
        plan["status"] = STATUS_NO_ROW
        plan["detail"] = "No registry row names this folder; look at it by hand before removing anything."
        return plan
    shelf_path = shelf_path_for(matched)
    plan["shelf_path"] = shelf_path
    if shelf_path is None:
        plan["status"] = STATUS_NOT_SHELVED
        plan["detail"] = "The registry does not place this transect on the Shelf, so there is no copy to compare against."
        return plan
    if is_live_fn({"processing_location": path}):
        plan["status"] = STATUS_LIVE
        plan["detail"] = "A process holds this folder's lock; wait for it to finish."
        return plan
    try:
        result = compare_snapshots(local_snapshot_fn(path), remote_snapshot_fn(shelf_path))
    except Exception as exc:  # noqa: BLE001  (an ssh or disk failure is a line in the table, not a crash)
        plan["status"] = STATUS_ERROR
        plan["detail"] = f"Could not compare the two copies: {exc}"
        return plan
    plan.update({"mismatches": result["mismatches"], "files": result["files"], "bytes": result["bytes"]})
    plan["status"] = STATUS_IDENTICAL if result["identical"] else STATUS_DIFFERS
    plan["deletable"] = result["identical"]
    plan["detail"] = ("The Shelf copy matches file for file; the local copy can go."
                      if result["identical"] else f"{len(result['mismatches'])} difference(s); keep the local copy.")
    return plan


def plan(roots, rows, local_snapshot_fn: Callable, remote_snapshot_fn: Callable, is_live_fn: Callable,
         find_fn: Callable = find_processing_folders) -> dict:
    """The whole table: one plan per processing folder under the Workbench roots.

    Returns:
        {"roots": [...], "folders": [plan_folder dicts], "problems": [sentences]}.
    """
    found = find_fn(roots)
    plans = [plan_folder(f["path"], rows, local_snapshot_fn, remote_snapshot_fn, is_live_fn)
             for f in found["folders"]]
    return {"roots": _require_roots(roots), "folders": plans, "problems": found["problems"]}


def anchored(path, roots) -> str:
    """The real path of a processing folder that sits inside one of the Workbench roots.

    Raises:
        TypeError: when path is not a string.
        ValueError: when the path is relative, is a root itself, sits outside
            every root, does not end with the processing suffix, is a symlink,
            or is not a directory (each names the reason).
    """
    if not isinstance(path, str):
        raise TypeError(f"path must be a string, got {type(path).__name__}")
    if not path.strip() or not os.path.isabs(path):
        raise ValueError(f"the folder to delete must be an absolute path, got {path!r}")
    if os.path.islink(path.rstrip("/")):
        raise ValueError(f"{path} is a symlink; only a real folder inside the Workbench can be deleted")
    real = os.path.realpath(path)
    real_roots = [os.path.realpath(root) for root in _require_roots(roots)]
    inside = [root for root in real_roots if real != root and real.startswith(root.rstrip("/") + "/")]
    if not inside:
        raise ValueError(f"{path} is not inside a Workbench root ({', '.join(real_roots) or 'none configured'})")
    if not PROCESSING_FOLDER_NAME.match(os.path.basename(real)):
        raise ValueError(f"{path} is not a processing folder (its name is not {{SITE}}_{{T#}}_3D)")
    if not os.path.isdir(real):
        raise ValueError(f"{path} is not a directory")
    return real


def delete_folder(path, roots, is_live_fn: Callable, rmtree_fn: Callable = shutil.rmtree) -> dict:
    """Remove one processing folder from the Workbench, anchored and not live.

    Returns:
        {"deleted": real path, "bytes": size counted before the delete}.

    Raises:
        ValueError: from anchored(), or when a process holds the folder's lock.
        OSError: when the removal fails (names the folder).
    """
    real = anchored(path, roots)
    if is_live_fn({"processing_location": real}):
        raise ValueError(f"{real} is processing right now; deleting is blocked until the run stops.")
    size = folder_bytes(real)
    try:
        rmtree_fn(real)
    except OSError as exc:
        raise OSError(f"could not delete {real}: {exc}") from exc
    return {"deleted": real, "bytes": size}


def folder_bytes(path: str) -> int:
    """Bytes of every regular file under `path` (symlinks skipped); 0 when it cannot be read."""
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                full = os.path.join(root, name)
                if not os.path.islink(full):
                    total += os.path.getsize(full)
    except OSError:
        return 0
    return total


def processing_log_row(path: str, shelf_path: str | None, readable_ids, initials: str, size_bytes: int,
                       now: Callable[[], str] | None = None) -> dict:
    """The processing_log row for one deletion, shaped over PROCESSING_LOG_COLUMNS."""
    stamp = now() if now else datetime.now(AST).isoformat(timespec="seconds")
    ids = ", ".join(str(r) for r in readable_ids) or "no registry row"
    return {
        "timestamp": stamp, "module": LOG_MODULE, "origin": LOG_ORIGIN,
        "purpose": f"clean workbench by {initials}: {ids}", "status": LOG_STATUS, "duration_s": "",
        "source_uri": path, "destination_uri": shelf_path or "",
        "notes": f"local copy removed after a name-and-size match with the Shelf copy ({size_bytes} bytes)",
    }


def append_processing_log(row: Mapping, log_path: str) -> None:
    """Append one row to the platform processing log under an exclusive flock (header when the file is new).

    Raises:
        OSError: when the log cannot be opened or written (names the path).
    """
    record = [str(row.get(col, "") or "").replace("\r", " ").replace("\n", " ") for col in PROCESSING_LOG_COLUMNS]
    buf = io.StringIO()
    csv.writer(buf).writerow(record)
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a+", newline="", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                header = io.StringIO()
                csv.writer(header).writerow(PROCESSING_LOG_COLUMNS)
                fh.write(header.getvalue())
            fh.write(buf.getvalue())
    except (OSError, ValueError) as exc:
        # ValueError: a NUL byte in the path, which open() refuses before any OS call.
        raise OSError(f"could not append to the processing log at {log_path!r}: {exc}") from exc


__all__ = [
    "PROCESSING_FOLDER_NAME", "LOCK_NAME", "MAX_WALK_DEPTH", "MAX_WALK_ENTRIES", "STATUS_IDENTICAL", "STATUS_DIFFERS",
    "STATUS_NO_ROW", "STATUS_NOT_SHELVED", "STATUS_LIVE", "STATUS_ERROR", "PROCESSING_LOG_COLUMNS",
    "split_location", "find_processing_folders", "rows_for_folder", "shelf_path_for", "compare_snapshots",
    "plan_folder", "plan", "anchored", "delete_folder", "folder_bytes", "processing_log_row",
    "append_processing_log",
]
