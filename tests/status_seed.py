"""Module: status_seed. Purpose: put scratch registry rows at any manual edit status for a test.

Since milestone A1g the registry's upsert refuses every manual_edit_status
move but blank to awaiting unless a named reset makes it, so a fixture that
needs a row mid-lifecycle (editing, review, upload, done, or a hand-typed
value) writes the two status cells straight into the scratch CSV under the
registry lock. No event is written for the seed, exactly as a row that
already held that status before the test began.

Inputs: a registry module already pointed at a scratch root.
Outputs: the scratch tcrmp_3d_registry.csv, never the live one.
"""
from __future__ import annotations

import os

LIVE_REGISTRY_ROOT = "/mnt/rip/vicarius_drive/vicarius/_METADATA/3d"
SEED_ACTOR = "seed"


def seed_status(reg, readable_id: str, status: str, done_at: str = "", **fields) -> dict:
    """Create or update one scratch row and set its manual edit status cells directly.

    Parameters:
        reg: the registry module, loaded with VICARIUS_3D_REGISTRY_ROOT at a scratch folder.
        readable_id: the row to seed.
        status: the manual_edit_status cell to store, as given (a hand value is allowed).
        done_at: the manual_edit_done cell to store.
        **fields: any other registry cells, written first through upsert.

    Returns:
        The seeded row as the CSV now holds it.

    Raises:
        RuntimeError: when `reg` points at the live registry folder.
    """
    if os.path.realpath(reg.ROOT) == os.path.realpath(LIVE_REGISTRY_ROOT):
        raise RuntimeError(f"seed_status refuses the live registry at {reg.ROOT}; point the test at a scratch root.")
    reg.upsert(readable_id, fields, SEED_ACTOR)
    with reg._Locked():
        rows = reg._read_rows()
        row = next(r for r in rows if r["readable_id"] == readable_id)
        row[reg.MANUAL_EDIT_STATUS_COLUMN] = status
        row[reg.MANUAL_EDIT_DONE_COLUMN] = done_at
        reg._write_rows(rows)
        return dict(row)
