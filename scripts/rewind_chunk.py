#!/usr/bin/env python3
"""
Module: scripts/rewind_chunk.py
Purpose: Remove one timepoint's chunk from a Metashape project so Voyager 1
         can rebuild it (program spec section 7, tool 4, the rewind). Opens
         the psx writable, drops every chunk whose label is the readable id,
         saves, and reports what it did as one JSON document.
Inputs:  the psx path, --label <readable id>, --out <json>; --fake makes the
         psx path a JSON file {"chunks": [labels]} that stands in for the
         project so any Python can run the tests.
Outputs: the JSON document at --out (also on stdout): psx, label, mode,
         chunks_before, chunks_after, removed, saved, error, at.

Real mode runs only under the Metashape binary:
    metashape -r scripts/rewind_chunk.py <psx> --label MRS_T1_2024ann --out result.json
Metashape is imported inside real mode only. The script refuses to touch a
project whose lock artifact (<stem>.files/lock, the file Metashape writes
while a project is open) is present, so it never edits under a running GUI.

Exit codes: 0 the chunk was removed (or was not there), 1 the project could
not be opened, edited or saved, 2 a usage problem (message on stderr).
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import sys
from datetime import datetime, timedelta, timezone

AST = timezone(timedelta(hours=-4))
MODE_REAL = "real"
MODE_FAKE = "fake"
PSX_SUFFIX = ".psx"
FILES_SUFFIX = ".files"
LOCK_ARTIFACT_NAME = "lock"
FAKE_CHUNKS_KEY = "chunks"
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
RESULT_FIELDS = ("psx", "label", "mode", "chunks_before", "chunks_after", "removed", "saved", "error", "at")


def now_iso() -> str:
    """AST timestamp with its offset."""
    return datetime.now(AST).isoformat(timespec="seconds")


def lock_artifact_path(psx_path: str) -> str:
    """<stem>.files/lock beside the psx: present while Metashape holds the project open."""
    stem = psx_path[:-len(PSX_SUFFIX)] if psx_path.lower().endswith(PSX_SUFFIX) else psx_path
    return os.path.join(stem + FILES_SUFFIX, LOCK_ARTIFACT_NAME)


def result_document(psx: str, label: str, mode: str) -> dict:
    """A fresh result document with every field present."""
    return {"psx": psx, "label": label, "mode": mode, "chunks_before": None, "chunks_after": None,
            "removed": 0, "saved": False, "error": None, "at": now_iso()}


def remove_chunk_fake(psx_path: str, label: str) -> dict:
    """Fake mode: the psx path is a JSON file {"chunks": [labels]}; rewrite it without the label.

    Raises:
        ValueError: when the file is not that shape.
        OSError: when it cannot be read or written.
    """
    document = result_document(psx_path, label, MODE_FAKE)
    with open(psx_path, "r", encoding="utf-8") as fh:
        state = json.load(fh)
    chunks = state.get(FAKE_CHUNKS_KEY) if isinstance(state, dict) else None
    if not isinstance(chunks, list) or not all(isinstance(c, str) for c in chunks):
        raise ValueError(f"{psx_path}: a fake project must be a JSON object with a list of chunk labels")
    kept = [c for c in chunks if c != label]
    document["chunks_before"], document["chunks_after"] = len(chunks), len(kept)
    document["removed"] = len(chunks) - len(kept)
    state[FAKE_CHUNKS_KEY] = kept
    tmp = psx_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, psx_path)
    document["saved"] = True
    return document


def remove_chunk_real(psx_path: str, label: str) -> dict:
    """Real mode: open the project writable, remove the labelled chunks, save.

    Raises:
        RuntimeError: when the project is open elsewhere (lock artifact present),
            cannot be opened, or cannot be saved; the message names the psx.
    """
    import Metashape  # noqa: WPS433  (only under metashape -r)
    document = result_document(psx_path, label, MODE_REAL)
    if os.path.exists(lock_artifact_path(psx_path)):
        raise RuntimeError(f"{psx_path} is open in Metashape (its lock file is present); close it and try again")
    doc = Metashape.Document()
    try:
        doc.open(psx_path, read_only=False, ignore_lock=False)
    except Exception as exc:  # noqa: BLE001  (any Metashape failure is a refusal here)
        raise RuntimeError(f"could not open {psx_path}: {exc}") from exc
    try:
        document["chunks_before"] = len(doc.chunks)
        targets = [chunk for chunk in doc.chunks if chunk.label == label]
        if targets:
            doc.remove(targets)
        document["chunks_after"] = len(doc.chunks)
        document["removed"] = len(targets)
        if targets:
            doc.save()
            document["saved"] = True
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"could not edit or save {psx_path}: {exc}") from exc
    finally:
        doc = None
        gc.collect()
    return document


def _parser() -> argparse.ArgumentParser:
    """The command line: the psx, --label, --out and --fake."""
    parser = argparse.ArgumentParser(
        description="Remove one timepoint's chunk from a Metashape project and report the result as JSON.")
    parser.add_argument("psx", help="the project (.psx), or the JSON stand-in under --fake")
    parser.add_argument("--label", required=True, help="the chunk label to remove (the readable id)")
    parser.add_argument("--out", help="write the JSON result here as well as to stdout")
    parser.add_argument("--fake", action="store_true", help="treat the psx path as a JSON stand-in (tests)")
    return parser


def _usage_error(message: str) -> int:
    """Print a usage problem on stderr and return EXIT_USAGE."""
    print(f"rewind_chunk: {message}", file=sys.stderr)
    return EXIT_USAGE


def _write_result(document: dict, out_path) -> None:
    """Print the JSON document and write it to --out when given."""
    text = json.dumps({k: document.get(k) for k in RESULT_FIELDS}, ensure_ascii=True)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def main(argv=None) -> int:
    """Parse the arguments, remove the chunk, write the result, return the exit code."""
    args = _parser().parse_args(argv)
    if not args.label.strip():
        return _usage_error("--label must not be blank")
    if not args.fake and not args.psx.lower().endswith(PSX_SUFFIX):
        return _usage_error(f"{args.psx} is not a {PSX_SUFFIX} file")
    if not os.path.exists(args.psx):
        return _usage_error(f"{args.psx} does not exist")
    mode = MODE_FAKE if args.fake else MODE_REAL
    try:
        document = remove_chunk_fake(args.psx, args.label) if args.fake else remove_chunk_real(args.psx, args.label)
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
        document = result_document(args.psx, args.label, mode)
        document["error"] = str(exc)
    try:
        _write_result(document, args.out)
    except OSError as exc:
        return _usage_error(f"could not write {args.out}: {exc}")
    print(f"Step 1 complete: {document['removed']} chunk(s) removed from {os.path.basename(args.psx)}"
          if document["error"] is None else f"rewind_chunk: {document['error']}", file=sys.stderr)
    return EXIT_OK if document["error"] is None else EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
