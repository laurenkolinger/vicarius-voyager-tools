#!/usr/bin/env python3
"""
Module: voyagertools/camera_tool.py
Purpose: The model behind the camera model tool (program spec section 7,
         tool 8): one line per season with its camera model, preprocessing
         and notes, built from the registry rows (every season that has a
         row) and seasons.csv (every season somebody recorded), and the
         validation of a season edit before registry.set_season writes it.
Inputs:  registry rows, season lines (dicts over registry.SEASON_COLUMNS),
         the JSON body of a season edit.
Outputs: plain dicts for the JSON routes in voyagertools/views.py.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

SEASON_KEY_PATTERN = re.compile(r"^(\d{4})(_pbl|ann)$")
SPRING_TOKEN = "_pbl"
ANNUAL_TOKEN = "ann"
SEASON_WORDS = {SPRING_TOKEN: "spring", ANNUAL_TOKEN: "annual"}
# The cells an operator edits; the same set registry.SEASON_INPUT_COLUMNS names.
SEASON_FIELDS = ("camera_model", "preprocessing", "notes")
MAX_FIELD_CHARS = 2000


def season_key(year, token) -> str:
    """"2025_pbl" or "2024ann" from a row's year and token cells; "" when either is not usable."""
    year_text = str(year or "").strip()
    token_text = str(token or "").strip()
    if not year_text.isdecimal() or len(year_text) != 4 or token_text not in SEASON_WORDS:
        return ""
    return f"{year_text}{token_text}"


def season_label(key: str) -> str:
    """The words for a season key: "2025 spring" for 2025_pbl, "2024 annual" for 2024ann.

    Raises:
        ValueError: when the key is outside the grammar (names the expected form).
    """
    match = SEASON_KEY_PATTERN.fullmatch(str(key or ""))
    if not match:
        raise ValueError(f"season key must be a four-digit year followed by _pbl or ann, got {key!r}")
    return f"{match[1]} {SEASON_WORDS[match[2]]}"


def _sort_key(key: str) -> tuple:
    """Chronological: year, then spring before annual; a malformed key sorts last by text."""
    match = SEASON_KEY_PATTERN.fullmatch(key)
    if not match:
        return (1, 0, 0, key)
    return (0, int(match[1]), 0 if match[2] == SPRING_TOKEN else 1, key)


def _require_list(items, what: str) -> list:
    """`items` as a list of mappings; refuses a string, a dict or a non-mapping element."""
    if isinstance(items, (str, bytes, Mapping)) or not isinstance(items, Iterable):
        raise TypeError(f"{what} must be a list of dicts, got {type(items).__name__}")
    items = list(items)
    for item in items:
        if not isinstance(item, Mapping):
            raise TypeError(f"{what} must be a list of dicts, got a {type(item).__name__} inside")
    return items


def season_table(rows, season_lines) -> list[dict]:
    """One line per season, chronological, for the camera model table.

    A season appears when a registry row belongs to it or seasons.csv holds a
    line for it. The line carries the recorded cells (blank when nothing was
    recorded), the stamp of the last edit, the number of registry rows in
    that season and the season's words.

    Parameters:
        rows: registry rows.
        season_lines: registry.seasons() lines.

    Returns:
        [{season_key, year, season_token, label, camera_model, preprocessing,
          notes, set_at, set_by, row_count}]; a malformed key in seasons.csv is
        listed last with label "".
    """
    rows = _require_list(rows, "rows")
    season_lines = _require_list(season_lines, "season lines")
    counts: dict[str, int] = {}
    for row in rows:
        key = season_key(row.get("year"), row.get("season_token"))
        if key:
            counts[key] = counts.get(key, 0) + 1
    recorded: dict[str, Mapping] = {}
    for line in season_lines:
        key = str(line.get("season_key") or "").strip()
        if key:
            recorded[key] = line
    table = []
    for key in sorted(set(counts) | set(recorded), key=_sort_key):
        line = recorded.get(key, {})
        match = SEASON_KEY_PATTERN.fullmatch(key)
        table.append({
            "season_key": key,
            "year": match[1] if match else str(line.get("year") or ""),
            "season_token": match[2] if match else str(line.get("season_token") or ""),
            "label": season_label(key) if match else "",
            "camera_model": str(line.get("camera_model") or ""),
            "preprocessing": str(line.get("preprocessing") or ""),
            "notes": str(line.get("notes") or ""),
            "set_at": str(line.get("set_at") or ""),
            "set_by": str(line.get("set_by") or ""),
            "row_count": counts.get(key, 0),
        })
    return table


def clean_season_fields(payload) -> dict[str, str]:
    """The three editable cells from a request body, checked as text.

    Parameters:
        payload: the JSON body; keys outside SEASON_FIELDS are ignored, a
            missing key is not written (registry.set_season keeps the cell).

    Returns:
        {field: value} for the fields present, stripped of surrounding blanks.

    Raises:
        TypeError: when the body is not a mapping or a value is not text.
        ValueError: when no editable field is present, a value holds a NUL
            character, or a value is longer than MAX_FIELD_CHARS.
    """
    if not isinstance(payload, Mapping):
        raise TypeError(f"the season edit must be a JSON object, got {type(payload).__name__}")
    fields = {}
    for name in SEASON_FIELDS:
        if name not in payload:
            continue
        value = payload[name]
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise TypeError(f"{name} must be text, got {type(value).__name__}")
        if "\x00" in value:
            raise ValueError(f"{name} holds a NUL character, which a CSV cell cannot carry")
        if len(value) > MAX_FIELD_CHARS:
            raise ValueError(f"{name} is longer than {MAX_FIELD_CHARS} characters")
        fields[name] = value.strip()
    if not fields:
        raise ValueError(f"the season edit names none of {', '.join(SEASON_FIELDS)}")
    return fields


__all__ = ["SEASON_FIELDS", "SEASON_KEY_PATTERN", "MAX_FIELD_CHARS", "season_key", "season_label",
           "season_table", "clean_season_fields"]
