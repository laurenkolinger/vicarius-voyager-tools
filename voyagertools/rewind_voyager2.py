#!/usr/bin/env python3
"""
Module: voyagertools/rewind_voyager2.py
Purpose: The rewind and spot redo tools for Voyager 2 results (program D
         Amendment 1, A1.12 item 20; the 2026-09-04 Voyager 2 spec section 5).
         Both act on a whole transect, because Voyager 2 builds one output
         folder and one psx per transect (spec D choice C14).
         Rewind before Voyager 2: the Shelf output folder <SITE>_<T#>_3doutput
         is renamed to <name>_voy2_archive_<YYYYMMDD-HHMMSS> (nothing on the
         Shelf is deleted, decision 11), then for every row of the transect
         that carries a Voyager 2 result the step 2 cells and the output
         location cells are cleared, the console log is pointed into the
         renamed folder, the voyager2 facts are blanked, and a note goes into
         the row's notes cell; every change goes through the registry writer
         and leaves an event.
         Spot redo for Voyager 2: every complete row of the transect gets the
         voyager2 fact redo_requested ("requested <ISO> by <INITIALS>"), which
         the registry's readiness rule reads as newer than step 2's finish, so
         the Voyager 2 setup window offers the transect again; nothing moves,
         and the next turn's archive mode deposit renames the old Shelf folder.
Inputs:  a readable id, the registry library (load, get, upsert, set_facts,
         fact_sections, run_is_live, MANUAL_EDIT_INITIALS_PATTERN), the NAS
         host and Shelf root from nas.yaml, an actor and initials, and for the
         rewind a rename function (the driver's phase2.archive_output in
         production, a local folder standing in for the Shelf in tests).
Outputs: plan and manifest dicts; the renamed Shelf folder; registry cells,
         facts and notes with their events.

Refusals: no such row; the chosen row carries no Voyager 2 result; a run
holds the transect's lock; a Voyager 2 turn reserves the transect; step 2 is
running; the output folder is still on this machine or the rows disagree on
it; the folder is outside the Shelf root, on another host, or not named
<SITE>_<T#>_3doutput for this transect; the NAS host is unknown; for the
rewind, a VIGIL reference without the explicit confirmation.
"""
from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone

AST = timezone(timedelta(hours=-4))
TARGET_BEFORE_STEP2 = "before_step2"
TARGET_WORDS = "before Voyager 2"
# The Shelf rename's infix, the same one the driver's archive mode deposit uses (driver/phase2.py ARCHIVE_INFIX).
ARCHIVE_INFIX = "_voy2_archive_"
OUTPUT_SUFFIX = "_3doutput"
OUTPUT_FOLDER_NAME = re.compile(r"^[A-Za-z0-9]+_T[0-9]+_3doutput\Z")
LOCATION_PATTERN = re.compile(r"^(?P<host>[A-Za-z0-9.\-]+):(?P<path>/.*)$")
# The step 2 record of a row: what shows a Voyager 2 result is there.
STEP2_RECORD_CELLS = ("step2_status", "step2_started", "step2_finished", "step2_seconds")
# What a rewind before Voyager 2 empties: the step 2 record and the cells that
# described the renamed output folder. output_folder stays, since it is the
# name every later run delivers under.
STEP2_RESET_CELLS = STEP2_RECORD_CELLS + ("output_location", "output_location_verified", "output_size_gb")
# The stage cells Voyager 2 wrote last; emptied only while step reads 2.
STAGE_CELLS = ("step", "stage", "stage_started")
STEP2_STEP = "2"
STEP2_RUNNING, STEP2_COMPLETE = "running", "complete"
CONSOLE_LOG_CELL = "console_log"
NOTES_CELL = "notes"
NOTES_CAP = 500            # the cap every writer of the notes cell keeps (3D_phase2 registry_client.NOTES_CAP)
NOTE_SEPARATOR = " | "     # the separator the same writers use
FACT_SECTION = "voyager2"
RESERVED_KEY = "reserved_by"
REDO_KEY = "redo_requested"
RUN_KIND_KEY, RUN_KIND_TEST, TEST_OUTPUT_KEY = "run_kind", "test", "output_shelf"
VIGIL_SECTION = "vigil"
# The vigil facts that say VIGIL holds this timepoint's orthomosaic: the
# driver's pull to the VIGIL stage and the VIGIL project write-back.
VIGIL_REFERENCE_KEYS = ("staged_manifest", "project_folder")
LOG_STATUS = "archived"
COMPACT_FORMAT = "%Y%m%d-%H%M%S"
NOTE_TIME_FORMAT = "%Y-%m-%d %H:%M AST"


class Voyager2Refused(ValueError):
    """The Voyager 2 rewind or spot redo cannot run; the message says why in one sentence."""


def _now() -> datetime:
    """Now in AST."""
    return datetime.now(AST)


def _text(row: Mapping, column: str) -> str:
    """A row cell as stripped text."""
    value = row.get(column) if isinstance(row, Mapping) else None
    return "" if value is None else str(value).strip()


def _check_initials(initials, registry_mod) -> str:
    """Initials as the registry accepts them (two to four capital letters), else ValueError."""
    if not isinstance(initials, str) or not registry_mod.MANUAL_EDIT_INITIALS_PATTERN.fullmatch(initials):
        raise ValueError(f"initials must be two to four capital letters, got {initials!r}")
    return initials


def _check_actor(actor) -> str:
    """A non-blank actor string, else ValueError."""
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor must be a non-blank string")
    return actor


def _check_shelf_root(shelf_root) -> str:
    """The Shelf root as an absolute POSIX path without a trailing slash."""
    if not isinstance(shelf_root, str) or not shelf_root.startswith("/"):
        raise ValueError(f"the Shelf root must be an absolute path, got {shelf_root!r}")
    return posixpath.normpath(shelf_root)


def _row(readable_id, registry_mod) -> dict:
    """The registry row, or Voyager2Refused when there is none."""
    if not isinstance(readable_id, str) or not readable_id.strip():
        raise Voyager2Refused("Name the timepoint by its readable id.")
    row = registry_mod.get(readable_id)
    if row is None:
        raise Voyager2Refused(f"No registry row has the id {readable_id}.")
    return row


def transect_key(row: Mapping) -> str:
    """<SITE>_<T#> of a row."""
    return f"{_text(row, 'site')}_{_text(row, 'transect')}"


def transect_rows(row: Mapping, registry_mod) -> list[dict]:
    """Every registry row of the same site and transect, in registry order."""
    key = (_text(row, "site"), _text(row, "transect"))
    return [r for r in registry_mod.load() if (_text(r, "site"), _text(r, "transect")) == key]


def _section(registry_mod, readable_id: str, section: str) -> dict[str, str]:
    """{key: value} of one fact section, values as stripped text."""
    grouped = registry_mod.fact_sections(readable_id).get(section) or {}
    return {key: str((fact or {}).get("value") or "").strip() for key, fact in grouped.items()}


def has_result(row: Mapping, facts: Mapping[str, str]) -> bool:
    """True when the row carries a step 2 record or any non-blank voyager2 fact."""
    return any(_text(row, c) for c in STEP2_RECORD_CELLS) or any(v for v in facts.values())


def _blocker(rows: list[dict], registry_mod) -> str:
    """Why the transect cannot be touched now (a live lock, a reservation, step 2 running), or ""."""
    for row in rows:
        rid = _text(row, "readable_id")
        if registry_mod.run_is_live(row):
            return f"{rid} is processing right now; wait for the run to stop."
        owner = _section(registry_mod, rid, FACT_SECTION).get(RESERVED_KEY, "")
        if owner:
            return (f"Voyager 2 turn {owner} holds {transect_key(row)}; finish or release it from the driver "
                    f"page first.")
        if _text(row, "step2_status") == STEP2_RUNNING:
            return f"Step 2 is running on {rid}; wait for it to finish or release the turn from the driver page."
    return ""


def _shelf_folder(rows: list[dict], transect: str, shelf_host: str, shelf_root: str) -> str | None:
    """The one Shelf output folder the rows record, validated; None when no row records one.

    Raises:
        Voyager2Refused: a local path, rows that disagree, an unknown or
            foreign host, a path outside the Shelf root, or a folder not
            named <SITE>_<T#>_3doutput for this transect.
    """
    found = {}
    for row in rows:
        location = _text(row, "output_location")
        if not location:
            continue
        match = LOCATION_PATTERN.match(location)
        if not match:
            raise Voyager2Refused(f"The Voyager 2 output of {_text(row, 'readable_id')} is still on this machine at "
                                  f"{location}; the turn has not deposited it, so finish or release it from the "
                                  f"driver page first.")
        found[(match["host"], match["path"].rstrip("/"))] = row
    if not found:
        return None
    if len(found) > 1:
        raise Voyager2Refused(f"The rows of {transect} disagree on their Shelf output folder "
                              f"({', '.join(sorted(p for _h, p in found))}); correct them in the atlas first.")
    (host, path), _row_of = found.popitem()
    if not shelf_host:
        raise Voyager2Refused("The NAS host is unknown because nas.yaml could not be read.")
    if host != shelf_host:
        raise Voyager2Refused(f"The output folder {host}:{path} is on host {host}, not the NAS {shelf_host}.")
    norm = posixpath.normpath(path)
    if norm != path or not norm.startswith(shelf_root.rstrip("/") + "/"):
        raise Voyager2Refused(f"The output folder {path} is outside the Shelf {shelf_root}, where only the "
                              f"driver's deposits may be renamed.")
    name = posixpath.basename(norm)
    if not OUTPUT_FOLDER_NAME.match(name) or name != transect + OUTPUT_SUFFIX:
        raise Voyager2Refused(f"{path} is not the output folder {transect}{OUTPUT_SUFFIX} of this transect.")
    return norm


def _vigil_references(rows: list[dict], registry_mod) -> list[dict]:
    """[{readable_id, key, value}] for every VIGIL fact that says VIGIL holds one of these timepoints."""
    refs = []
    for row in rows:
        rid = _text(row, "readable_id")
        section = _section(registry_mod, rid, VIGIL_SECTION)
        refs += [{"readable_id": rid, "key": k, "value": section[k]} for k in VIGIL_REFERENCE_KEYS if section.get(k)]
    return refs


def _row_plan(row: Mapping, facts: Mapping[str, str], shelf_host: str, old: str | None, new: str | None) -> dict:
    """What the rewind changes on one row: {readable_id, registry_resets, facts_cleared, console_log}."""
    resets = {c: _text(row, c) for c in STEP2_RESET_CELLS if _text(row, c)}
    if _text(row, "step") == STEP2_STEP:
        resets.update({c: _text(row, c) for c in STAGE_CELLS if _text(row, c)})
    console = None
    log = _text(row, CONSOLE_LOG_CELL)
    if old and new and log.startswith(f"{shelf_host}:{old}/"):
        console = {"from": log, "to": f"{shelf_host}:{new}/" + log[len(f"{shelf_host}:{old}/"):]}
    return {"readable_id": _text(row, "readable_id"), "registry_resets": resets,
            "facts_cleared": sorted(k for k, v in facts.items() if v), "console_log": console}


def plan(readable_id, registry_mod, *, shelf_host, shelf_root, stamp=None) -> dict:
    """What a rewind before Voyager 2 would do, without doing it (the dry run).

    Parameters:
        readable_id: any timepoint of the transect that carries a Voyager 2 result.
        registry_mod: the registry library.
        shelf_host, shelf_root: nas.yaml's host and defaults.shelf_root.
        stamp: the compact stamp the predicted archive name carries (now when None).

    Returns:
        {"readable_id", "target", "target_words", "transect", "rows": [_row_plan],
         "shelf_folder", "archive_to", "vigil_references", "warnings", "empty"}.

    Raises:
        Voyager2Refused: no row, or a recorded output folder that fails _shelf_folder.
        ValueError: a Shelf root that is not absolute.
    """
    shelf_root = _check_shelf_root(shelf_root)
    row = _row(readable_id, registry_mod)
    transect = transect_key(row)
    facts_of = {_text(r, "readable_id"): _section(registry_mod, _text(r, "readable_id"), FACT_SECTION)
                for r in transect_rows(row, registry_mod)}
    affected = [r for r in transect_rows(row, registry_mod) if has_result(r, facts_of[_text(r, "readable_id")])]
    folder = _shelf_folder(affected, transect, shelf_host, shelf_root)
    archive_to = f"{folder}{ARCHIVE_INFIX}{stamp or _now().strftime(COMPACT_FORMAT)}" if folder else None
    warnings = []
    for r in affected:
        facts = facts_of[_text(r, "readable_id")]
        if facts.get(RUN_KIND_KEY) == RUN_KIND_TEST and facts.get(TEST_OUTPUT_KEY):
            warnings.append(f"The test run's Shelf folder {facts[TEST_OUTPUT_KEY]} of {_text(r, 'readable_id')} "
                            f"stays as it is; the next real turn renames it in archive mode.")
    rows = [_row_plan(r, facts_of[_text(r, "readable_id")], shelf_host, folder, archive_to) for r in affected]
    chosen = has_result(row, facts_of[_text(row, "readable_id")])
    return {"readable_id": readable_id, "target": TARGET_BEFORE_STEP2, "target_words": TARGET_WORDS,
            "transect": transect, "rows": rows if chosen else [], "shelf_folder": folder if chosen else None,
            "archive_to": archive_to if chosen else None, "vigil_references": _vigil_references(affected, registry_mod),
            "warnings": warnings, "empty": not chosen}


def plan_or_refuse(readable_id, registry_mod, *, shelf_host, shelf_root, stamp=None) -> dict:
    """The dry run after every refusal: the plan, or Voyager2Refused naming why nothing can run."""
    row = _row(readable_id, registry_mod)
    why = _blocker(transect_rows(row, registry_mod), registry_mod)
    if why:
        raise Voyager2Refused(why)
    the_plan = plan(readable_id, registry_mod, shelf_host=shelf_host, shelf_root=shelf_root, stamp=stamp)
    if the_plan["empty"]:
        raise Voyager2Refused(f"{readable_id} has no Voyager 2 result to rewind.")
    return the_plan


def _with_note(row: Mapping, text: str) -> str:
    """The notes cell with `text` appended, keeping the last NOTES_CAP characters."""
    current = _text(row, NOTES_CELL)
    joined = f"{current}{NOTE_SEPARATOR}{text}" if current else text
    return joined[-NOTES_CAP:]


def _rename(archive_fn: Callable, folder: str | None) -> str | None:
    """The renamed Shelf folder, or None when no folder is recorded or none is there; refusal on failure."""
    if not folder:
        return None
    try:
        return archive_fn(folder)
    except (RuntimeError, ValueError, OSError) as exc:
        raise Voyager2Refused(f"The Shelf output folder {folder} was not renamed, so nothing changed: {exc}") from exc


def apply(readable_id, registry_mod, actor, initials, archive_fn: Callable, *, shelf_host, shelf_root,
          confirm_vigil=False, stamp=None) -> dict:
    """Carry out a rewind before Voyager 2 on the whole transect.

    Order: the refusals; the plan (an empty plan is refused); the VIGIL guard;
    the Shelf rename through archive_fn(folder) -> renamed path or None (a
    failure stops everything before any registry write); per row one upsert
    (the resets, the console log into the renamed folder, the note) and one
    set_facts blanking the voyager2 facts.

    Parameters:
        readable_id, registry_mod, shelf_host, shelf_root, stamp: as in plan().
        actor: "voyager_tools:<INITIALS>".
        initials: the operator's initials, named in the note.
        archive_fn: the Shelf rename (driver phase2.archive_output bound to its ssh and Shelf root).
        confirm_vigil: True once the operator confirmed a rewind VIGIL already references.

    Returns:
        The manifest: the plan plus {"archived_to", "actor", "at", "note", "vigil_confirmed"}.

    Raises:
        Voyager2Refused: every refusal, including a rename failure.
        ValueError: a blank actor, bad initials, a relative Shelf root.
        OSError: a registry write failed after the rename (the message names the renamed folder).
    """
    _check_actor(actor)
    _check_initials(initials, registry_mod)
    the_plan = plan_or_refuse(readable_id, registry_mod, shelf_host=shelf_host, shelf_root=shelf_root, stamp=stamp)
    refs = the_plan["vigil_references"]
    if refs and confirm_vigil is not True:
        named = "; ".join(f"{r['readable_id']} {r['key']} {r['value']}" for r in refs)
        raise Voyager2Refused(f"VIGIL already holds these timepoints ({named}); confirm the VIGIL line in the dialog "
                              f"to rewind anyway.")
    archived = _rename(archive_fn, the_plan["shelf_folder"])
    at = _now()
    what = f"Shelf output folder renamed to {archived}" if archived else "no Shelf output folder was renamed"
    note = f"Voyager 2 result rewound {at.strftime(NOTE_TIME_FORMAT)} by {initials}: {what}"
    warnings = list(the_plan["warnings"])
    if the_plan["shelf_folder"] and not archived:
        warnings.append(f"The output folder {the_plan['shelf_folder']} was not on the Shelf; the record is cleared.")
    rows = [_row_plan(registry_mod.get(r["readable_id"]), _section(registry_mod, r["readable_id"], FACT_SECTION),
                      shelf_host, the_plan["shelf_folder"], archived) for r in the_plan["rows"]]
    try:
        for row_plan in rows:
            _write_row(registry_mod, row_plan, note, actor)
    except (OSError, ValueError, KeyError) as exc:
        where = f"the Shelf output folder was already renamed to {archived}" if archived else "no Shelf folder moved"
        raise OSError(f"The rewind of {the_plan['transect']} stopped part way ({exc}); {where}, and running the "
                      f"rewind again finishes the registry record.") from exc
    the_plan.update(rows=rows, archived_to=archived, actor=actor, at=at.isoformat(timespec="seconds"), note=note,
                    warnings=warnings, vigil_confirmed=bool(refs))
    return the_plan


def _write_row(registry_mod, row_plan: Mapping, note: str, actor: str) -> None:
    """One row's writes: the resets, the console log and the note in one upsert, then the facts blanked."""
    rid = row_plan["readable_id"]
    fields = {cell: "" for cell in row_plan["registry_resets"]}
    if row_plan["console_log"]:
        fields[CONSOLE_LOG_CELL] = row_plan["console_log"]["to"]
    fields[NOTES_CELL] = _with_note(registry_mod.get(rid), note)
    registry_mod.upsert(rid, fields, actor)
    if row_plan["facts_cleared"]:
        registry_mod.set_facts(rid, FACT_SECTION, {key: None for key in row_plan["facts_cleared"]}, actor)


def spot_redo_plan(readable_id, registry_mod) -> dict:
    """What a Voyager 2 spot redo would write: {"readable_id", "transect", "rows": [{readable_id, step2_finished}]}.

    Raises:
        Voyager2Refused: no row, a blocked transect, or a chosen row whose step 2 is not complete.
    """
    row = _row(readable_id, registry_mod)
    rows = transect_rows(row, registry_mod)
    why = _blocker(rows, registry_mod)
    if why:
        raise Voyager2Refused(why)
    if _text(row, "step2_status") != STEP2_COMPLETE:
        raise Voyager2Refused(f"{readable_id} has no complete Voyager 2 result to redo.")
    complete = [{"readable_id": _text(r, "readable_id"), "step2_finished": _text(r, "step2_finished")}
                for r in rows if _text(r, "step2_status") == STEP2_COMPLETE]
    return {"readable_id": readable_id, "transect": transect_key(row), "rows": complete}


def spot_redo(readable_id, registry_mod, actor, initials, now_iso: Callable[[], str] | None = None) -> dict:
    """Record a Voyager 2 redo request on every complete row of the transect.

    Returns:
        The spot redo plan plus {"redo_requested": the fact value, "actor", "note"}.

    Raises:
        Voyager2Refused: from spot_redo_plan. ValueError: a blank actor or bad initials.
    """
    _check_actor(actor)
    _check_initials(initials, registry_mod)
    the_plan = spot_redo_plan(readable_id, registry_mod)
    asked = now_iso() if now_iso else _now().isoformat(timespec="seconds")
    value = f"requested {asked} by {initials}"
    note = f"Voyager 2 redo requested {_now().strftime(NOTE_TIME_FORMAT)} by {initials}"
    for entry in the_plan["rows"]:
        rid = entry["readable_id"]
        registry_mod.set_facts(rid, FACT_SECTION, {REDO_KEY: value}, actor)
        registry_mod.upsert(rid, {NOTES_CELL: _with_note(registry_mod.get(rid), note)}, actor)
    the_plan.update(redo_requested=value, actor=actor, note=note)
    return the_plan


def processing_log_row(manifest: Mapping, shelf_host: str, initials: str, now: Callable[[], str]) -> dict:
    """The processing_log row for one Shelf rename, over clean_workbench.PROCESSING_LOG_COLUMNS' keys."""
    ids = ", ".join(r["readable_id"] for r in manifest["rows"]) or manifest["readable_id"]
    return {"timestamp": now(), "module": "voyager_tools", "origin": "USER",
            "purpose": f"rewind before Voyager 2 by {initials}: {ids}", "status": LOG_STATUS, "duration_s": "",
            "source_uri": f"{shelf_host}:{manifest['shelf_folder']}",
            "destination_uri": f"{shelf_host}:{manifest['archived_to']}",
            "notes": "Shelf output folder renamed, nothing deleted; step 2 cells and voyager2 facts cleared"}


__all__ = [
    "TARGET_BEFORE_STEP2", "TARGET_WORDS", "ARCHIVE_INFIX", "STEP2_RESET_CELLS", "VIGIL_REFERENCE_KEYS", "NOTES_CAP",
    "Voyager2Refused", "transect_key", "transect_rows", "has_result", "plan", "plan_or_refuse", "apply",
    "spot_redo_plan", "spot_redo", "processing_log_row",
]
