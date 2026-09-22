#!/usr/bin/env python3
"""
Module: voyagertools/order_tool.py
Purpose: The model behind the atlas edit tool (program spec section 7, tool
         1): the processing order table Voyager 1 works through, shown as
         transect blocks of rows that have not started, and the full id
         list a reordering of those blocks expands to. Pure functions over
         registry rows and processing_order.csv lines; the registry library
         (vicarius/_METADATA/3d/registry.py) does the writing and enforces
         the rules (locked order, started rows keep their place, transects
         stay chronological and together).
Inputs:  registry rows (dicts over registry.COLUMNS), order lines (dicts over
         registry.PROCESSING_ORDER_COLUMNS), the lock stamp from
         registry.processing_order_lock(), an optional site-name resolver.
Outputs: plain dicts and lists for the JSON routes in voyagertools/views.py.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping

# Mirror of registry.INACTIVE_STAGES: a row whose stage is one of these has
# nothing in progress. Kept here so this module stays pure (no registry
# import); tests/test_order_tool.py pins the two tuples equal.
INACTIVE_STAGES = ("", "done", "failed")
TRUE_VALUES = ("true", "1", "yes")
SPRING_TOKEN = "_pbl"
TRANSECT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9]+_T\d+$")
TRANSECT_KEY_JOINER = "_"
# Rows without a numbered position sort after every numbered one.
NO_POSITION = 10 ** 9


def process_on(row: Mapping) -> bool:
    """True when the row's process cell says the row is meant to be processed."""
    return str(row.get("process") or "").strip().lower() in TRUE_VALUES


def row_started(row: Mapping) -> bool:
    """True when Voyager 1 has touched the row: step1_status set, or a stage that is not inactive.

    The same rule registry.set_processing_order applies when it decides which
    rows may move.
    """
    if str(row.get("step1_status") or "").strip():
        return True
    return str(row.get("stage") or "").strip() not in INACTIVE_STAGES


def transect_key(row: Mapping) -> str:
    """"MRS_T1" from the row's site and transect cells; "" when either is blank."""
    site = str(row.get("site") or "").strip()
    transect = str(row.get("transect") or "").strip()
    if not site or not transect:
        return ""
    return f"{site}{TRANSECT_KEY_JOINER}{transect}"


def date_key(row: Mapping) -> tuple:
    """(year, 0 for spring, 1 for annual) from the row's year and token cells; junk sorts last."""
    year_text = str(row.get("year") or "").strip()
    year = int(year_text) if year_text.isdecimal() else NO_POSITION
    token = str(row.get("season_token") or "").strip()
    return (year, 0 if token == SPRING_TOKEN else 1)


def order_positions(order_lines: Iterable[Mapping]) -> dict[str, int]:
    """{readable_id: position} over the order lines; a line with a bad position is skipped."""
    positions: dict[str, int] = {}
    for line in order_lines:
        rid = str(line.get("readable_id") or "").strip()
        position = str(line.get("position") or "").strip()
        if rid and position.isdecimal() and rid not in positions:
            positions[rid] = int(position)
    return positions


def _require_rows(rows) -> list[Mapping]:
    """`rows` as a list of mappings; refuses a string, a dict or a non-mapping element."""
    if isinstance(rows, (str, bytes, Mapping)) or not isinstance(rows, Iterable):
        raise TypeError(f"rows must be a list of registry row dicts, got {type(rows).__name__}")
    rows = list(rows)
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError(f"rows must be a list of registry row dicts, got a {type(row).__name__} inside")
    return rows


def _row_summary(row: Mapping, positions: Mapping) -> dict:
    """The cells the order table shows for one row."""
    rid = str(row.get("readable_id") or "")
    return {
        "readable_id": rid,
        "site": str(row.get("site") or ""),
        "transect": str(row.get("transect") or ""),
        "year": str(row.get("year") or ""),
        "season_token": str(row.get("season_token") or ""),
        "step1_status": str(row.get("step1_status") or ""),
        "stage": str(row.get("stage") or ""),
        "position": positions.get(rid),
    }


def started_rows(rows, order_lines) -> list[dict]:
    """The rows Voyager 1 has touched, ordered rows first by position, the rest in date order.

    Parameters:
        rows: registry rows.
        order_lines: the processing order lines.

    Returns:
        Row summaries (see _row_summary), process-off rows included, because a
        started row keeps its place whatever its process cell says.
    """
    rows = _require_rows(rows)
    positions = order_positions(order_lines)
    started = [row for row in rows if row_started(row)]
    started.sort(key=lambda row: (positions.get(str(row.get("readable_id") or ""), NO_POSITION),
                                  transect_key(row), date_key(row)))
    return [_row_summary(row, positions) for row in started]


def build_blocks(rows, order_lines) -> list[dict]:
    """The transect blocks of rows that have not started, in current processing order.

    A block holds every process-on row of one site and transect that Voyager 1
    has not touched, in date order. Blocks with a position in the order come
    first by their earliest position; blocks outside the order follow in
    site and transect order. Rows with a blank site or transect are left out
    (they cannot be ordered) and reported under "skipped".

    Parameters:
        rows: registry rows.
        order_lines: the processing order lines.

    Returns:
        A list of {key, site, transect, position, rows: [row summaries]}.
    """
    rows = _require_rows(rows)
    positions = order_positions(order_lines)
    grouped: dict[str, list] = {}
    for row in rows:
        if row_started(row) or not process_on(row):
            continue
        key = transect_key(row)
        if not key:
            continue
        grouped.setdefault(key, []).append(row)
    blocks = []
    for key, members in grouped.items():
        members.sort(key=date_key)
        ranks = [positions[str(m.get("readable_id") or "")] for m in members
                 if str(m.get("readable_id") or "") in positions]
        blocks.append({
            "key": key, "site": str(members[0].get("site") or ""), "transect": str(members[0].get("transect") or ""),
            "position": min(ranks) if ranks else None,
            "rows": [_row_summary(m, positions) for m in members],
        })
    blocks.sort(key=lambda block: (block["position"] if block["position"] is not None else NO_POSITION,
                                   block["site"], _transect_number(block["transect"])))
    return blocks


def _transect_number(transect: str) -> int:
    """The number of a transect label ("T12" gives 12); junk sorts last."""
    digits = transect[1:] if transect[:1].upper() == "T" else transect
    return int(digits) if digits.isdecimal() else NO_POSITION


def skipped_rows(rows) -> list[str]:
    """Ids of process-on, not-started rows that cannot be ordered because site or transect is blank."""
    rows = _require_rows(rows)
    return [str(row.get("readable_id") or "") for row in rows
            if not row_started(row) and process_on(row) and not transect_key(row)]


def lock_summary(order_lines) -> dict:
    """{count, first, last} over the order lines, for the lock-in confirm and the result line."""
    ids = [str(line.get("readable_id") or "") for line in order_lines if str(line.get("readable_id") or "")]
    return {"count": len(ids), "first": ids[0] if ids else "", "last": ids[-1] if ids else ""}


def order_table(rows, order_lines, lock, site_name_fn: Callable[[str], str] | None = None) -> dict:
    """Everything the atlas edit tool renders.

    Parameters:
        rows: registry rows.
        order_lines: the processing order lines (registry.processing_order()).
        lock: registry.processing_order_lock(): {"locked_at", "locked_by"} or None.
        site_name_fn: code -> full site name; None leaves names blank.

    Returns:
        {"lock": lock or None, "started": [...], "blocks": [...], "skipped": [...],
         "ordered_ids": [...], "count", "first", "last"}; blocks carry site_name.
    """
    if lock is not None and not isinstance(lock, Mapping):
        raise TypeError(f"lock must be a dict or None, got {type(lock).__name__}")
    lines = list(order_lines)
    blocks = build_blocks(rows, lines)
    for block in blocks:
        block["site_name"] = (site_name_fn(block["site"]) if site_name_fn else "") or ""
    summary = lock_summary(lines)
    return {
        "lock": dict(lock) if lock else None,
        "started": started_rows(rows, lines),
        "blocks": blocks,
        "skipped": skipped_rows(rows),
        "ordered_ids": [str(line.get("readable_id") or "") for line in lines],
        "count": summary["count"], "first": summary["first"], "last": summary["last"],
    }


def _require_transect_keys(transect_keys) -> list[str]:
    """`transect_keys` as a list of distinct "SITE_T#" strings."""
    if isinstance(transect_keys, (str, bytes, Mapping)) or not isinstance(transect_keys, Iterable):
        raise TypeError(f"transects must be a list of SITE_T# keys, got {type(transect_keys).__name__}")
    keys, seen = [], set()
    for key in transect_keys:
        if not isinstance(key, str):
            raise TypeError(f"transects must be a list of SITE_T# keys, got a {type(key).__name__} inside")
        if not TRANSECT_KEY_PATTERN.fullmatch(key):
            raise ValueError(f"{key!r} is not a transect key of the form SITE_T#")
        if key in seen:
            raise ValueError(f"transect {key} appears twice in the new order")
        seen.add(key)
        keys.append(key)
    return keys


def ids_for_transects(rows, order_lines, transect_keys) -> list[str]:
    """Expand a transect block order into the full id list registry.set_processing_order takes.

    Started rows come first (in their current relative order, see
    started_rows), then every block's rows in date order, blocks in the order
    given. Every block that has not started must be named exactly once, so a
    reorder can never drop a transect from the queue by accident.

    Parameters:
        rows: registry rows.
        order_lines: the processing order lines.
        transect_keys: block keys ("MRS_T1") in the wanted order.

    Returns:
        The readable ids, first to process first.

    Raises:
        TypeError, ValueError: for malformed keys, a key named twice, a key
            with no waiting rows, or a waiting transect left out (each names it).
    """
    keys = _require_transect_keys(transect_keys)
    blocks = {block["key"]: block for block in build_blocks(rows, order_lines)}
    unknown = [key for key in keys if key not in blocks]
    if unknown:
        raise ValueError(f"no rows of transect {unknown[0].replace(TRANSECT_KEY_JOINER, ' ')} are waiting "
                         f"to be processed, so it cannot be placed in the order")
    missing = [key for key in blocks if key not in keys]
    if missing:
        raise ValueError(f"transect {missing[0].replace(TRANSECT_KEY_JOINER, ' ')} is missing from the new "
                         f"order; every transect that has not started must be placed")
    ids = [row["readable_id"] for row in started_rows(rows, order_lines)]
    for key in keys:
        ids.extend(row["readable_id"] for row in blocks[key]["rows"])
    return ids


def move_block(keys: list[str], key: str, direction: int) -> list[str]:
    """A copy of `keys` with `key` moved one place up (-1) or down (+1); no move at the ends.

    Raises:
        ValueError: when `key` is not in `keys` or `direction` is not -1 or 1.
    """
    if direction not in (-1, 1):
        raise ValueError(f"direction must be -1 (up) or 1 (down), got {direction!r}")
    if key not in keys:
        raise ValueError(f"transect {key} is not in the order")
    out = list(keys)
    index = out.index(key)
    target = index + direction
    if 0 <= target < len(out):
        out[index], out[target] = out[target], out[index]
    return out


__all__ = [
    "INACTIVE_STAGES", "TRANSECT_KEY_PATTERN", "process_on", "row_started", "transect_key", "date_key",
    "order_positions", "started_rows", "build_blocks", "skipped_rows", "lock_summary", "order_table",
    "ids_for_transects", "move_block",
]
