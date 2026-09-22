# voyager_tools: glossary

Last updated: 2026-09-04 (LO)

This file is the vocabulary of the `voyager_tools` module: the words its code, UI, docs and log rows use for the things it handles, one meaning each. Agents read it before naming anything in this clone. The platform glossary at `/mnt/rip/vicarius_drive/CONTEXT.md` covers the terms every module shares, the TCRMP 3D registry glossary at `/mnt/rip/vicarius_drive/vicarius/_METADATA/3d/CONTEXT.md` covers the registry's own words (timepoint, readable id, stage, run state, manual edit status, processing location), and `/mnt/rip/vicarius_drive/CONTEXT-MAP.md` indexes every glossary on the drive. Format rules for all of them are in `/mnt/rip/vicarius_drive/docs/agents/domain.md`.

## The page

**Tool**:
One of the eight sections of the Voyager tools page, each behind one entry in the left list: Atlas edit, Clean workbench, Clean manual edit run, Rewind, Spot redo, Prompts doc, Parameter defaults, Camera model. Every tool reads through the library that owns its file and writes with the actor `voyager_tools:<INITIALS>`.
_Avoid_: tab (the left list is a tab list in markup, but a section is a tool), panel

**Admin mode**:
The desktop session state (the ADMIN chip on port 5090, `session["admin_unlocked"]`) that every route of this module requires; outside it a route answers 401 and the desktop hides the item. Distinct from the module lock, which blocks everyone including admin.
_Avoid_: dev mode

## The processing order

**Processing order**:
The queue Voyager 1 works through, kept in `processing_order.csv` beside the registry: positions 1 to N over rows, one line each, written only through `registry.set_processing_order`. The atlas sorts by it when it exists.
_Avoid_: priority, rank, queue (the Carousel's queue is a batch of turns)

**Transect block**:
The rows of one site and transect that Voyager 1 has not started, shown as one draggable row of the order table and moved together, because the shared Metashape project of a transect appends forward only and its timepoints must stay chronological and contiguous.
_Avoid_: group, bundle

**Started row**:
A row Voyager 1 has touched (`step1_status` set, or a stage that is not blank, done or failed). It keeps its place among the started rows and cannot leave the order; the tool lists it under "Rows already started".

**Lock-in**:
The state of a processing order every line of which carries `locked_at` and `locked_by`; `set_processing_order` refuses until Unlock clears the stamps. The confirm names the count and the first and last ids.
_Avoid_: freeze, lock (the module lock and the processing lock are different things)

## The Workbench and the edit bench

**Workbench candidate**:
A folder whose name ends in `_3dprocessing` found at most four levels under a Workbench root (`defaults.workbench_root` in the Carousel's `config/nas.yaml`). It is deletable only when its registry rows place the transect on the Shelf and the Shelf copy matches it file for file by name and size.

**Shelf comparison**:
The name-and-size diff between a local snapshot (`driver.verify.local_snapshot`) and a remote snapshot (`driver.remote.snapshot`) of one processing folder, with the `.processing.lock` control file left out of both sides. Its outcome is one of identical, differs, no registry row, not on the Shelf, processing right now, or error.

**Edit bench copy**:
The local folder of one transect under the edit bench (the folder VICARIUS owns for a manual edit job). Cancel and prune removes it only when the job never reached the push to the Shelf; after a push it is kept as the complete record.

## Rewind

**Rewind**:
Taking one timepoint back to before step 1 (the Metashape reconstruction) or before step 0 (the frame extraction): the report and frames move into the rewind folder, the chunk leaves the shared project, the `status.csv` cells clear and the registry cells reset with one event each. Nothing is deleted.
_Avoid_: reset, rollback, undo

**Rewind folder**:
`<processing folder>/_rewind/<YYYYMMDD-HHMMSS>/`, holding the moved artifacts, the chunk script's result and the manifest `rewind.json` of one rewind.

**Chunk script**:
`scripts/rewind_chunk.py`, run under `metashape -r` to remove the chunks labelled with the readable id from the project and save; `--fake` treats the psx path as a JSON stand-in so tests need no Metashape.

**Spot redo**:
A rewind before step 1 plus the voyager1 fact `spot_redo` on the row, so the Voyager 1 setup window offers the timepoint again and passes `--force` for it.
_Avoid_: rerun, redo (alone)

## Parameters

**Parameter version**:
A numbered file `<phase>/versions/vX.Y.Z.yaml` in the voyagerparams checkout; the default is the one the `DEFAULT` pointer names. A default save bumps MINOR for changed values and MAJOR for a key added, removed or renamed.

**Branch**:
A named variant `<phase>/branches/<slug>/vX.Y.Z.yaml` that starts at its base version and never moves the default.

**Custom set**:
`<phase>/custom/<run_id>.yaml`, the exact values one run used, written by the Voyager 1 setup window and never a default by itself.

**YAML box**:
The text area of the Parameter defaults tool whose YAML is merged over the editor values before validation; the way to add a key the schema does not know.

## Seasons

**Season record**:
One line of `seasons.csv` per season key (`YYYY_pbl` spring, `YYYYann` annual): camera model, preprocessing and notes for every video of that season, shown in the atlas tooltips.
_Avoid_: timepoint (one transect on one date), year
