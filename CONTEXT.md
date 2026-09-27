# voyager_tools: glossary

Last updated: 2026-09-27 (LO)

This file is the vocabulary of the `voyager_tools` module: the words its code, UI, docs and log rows use for the things it handles, one meaning each. Agents read it before naming anything in this clone. The platform glossary at `/mnt/rip/vicarius_drive/CONTEXT.md` covers the terms every module shares, the TCRMP 3D registry glossary at `/mnt/rip/vicarius_drive/vicarius/_METADATA/3d/CONTEXT.md` covers the registry's own words (timepoint, readable id, stage, run state, manual edit status, processing location), and `/mnt/rip/vicarius_drive/CONTEXT-MAP.md` indexes every glossary on the drive. Format rules for all of them are in `/mnt/rip/vicarius_drive/docs/agents/domain.md`.

## The page

**Atlas control panel**:
The name people see for this module since 2026-09-27: the one window holding the restricted settings and tools of the atlas, the Voyager runs, the manual edit and the desktop. The slug `voyager_tools`, the address `/voyager-tools` and the actor prefix stay.
_Avoid_: Voyager tools (the old display name), admin page, admin gated settings

**Pane**:
One entry of the panel's grouped list (Records, Manual edit, Voyager, Desktop, History) and what it shows. The eight tools are panes; so are Uploads and Timelines, the settings panes, the read-only desktop panes and the Settings history.
_Avoid_: tab, panel (the panel is the whole window)

**Tool**:
One of the eight panes that act on the registry or the disks: Atlas edit, Clean workbench, Clean manual edit run, Rewind, Spot redo, Prompts doc, Parameter defaults, Camera model. Every tool reads through the library that owns its file and writes with the actor `voyager_tools:<INITIALS>`.

**Settings pane**:
A pane that edits one settings file through the desktop's write path for it (`/api/settings/<store>`): Manual edit settings, Shelf copy, Voyager runs, Space floors. Every save appends one settings history line per changed setting.

**Settings history**:
`/mnt/rip/vicarius_drive/vicarius/_METADATA/settings_history.csv`, one line per changed setting: when, who, which store and key, the old and new value as JSON text, and the reason. Written only by the desktop, never rewritten.

**Upload set**:
The manual edit module's list of transects, possibly from several manual edit projects, uploaded to the Shelf one after another under one progress record (`UP-<YYYYMMDD>-<HHMMSS>-<initials>`). The Uploads pane starts one from the transects marked for re-upload and reopens any set of the last 7 days in the editor's own progress window; a set keeps running after admin mode ends, anyone may watch it and only an admin may stop it.
_Avoid_: batch (a batch is a driver's group of Voyager turns), upload job

**Timeline**:
How long one transect of one manual edit project spent at each step, read in the Timelines pane: pulled to the edit bench, editing, open in Metashape, in admin review, waiting for upload, uploaded. The registry status times come from the status changes in `events.csv` (the registry library's `state_intervals`, the time each status lasted) inside the project's window, from its creation to the creation of the next project holding the same transect; the Metashape time comes from the transect's open and close events; nothing is a stored counter.
_Avoid_: history (the settings history is another thing), log

**Drift**:
A setting whose value in its file differs from the last value the settings history holds for it, because the file changed outside the write path. The pane names it and **Record as found** writes the missing line with the typed initials and a reason.
_Avoid_: mismatch, stale

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
A Voyager 1 processing folder (`{SITE}_{T#}_3D`) found at most four levels under a Workbench root (`defaults.workbench_root` in the driver's `config/nas.yaml`) and outside every excluded root. It is deletable only when its registry rows place the transect on the Shelf and the Shelf copy matches it file for file by name and size.

**Excluded root**:
A folder the Workbench walk never enters and the delete never touches although it sits inside the Workbench by default: the Edit bench (the manual edit setting, the fallback `<workbench_root>/editbench` and every unfinished project's bench), the Voyager 2 stage, the VIGIL stage and the transfer ledger (`inflight_dir`). Read from the configuration at every scan; a scan that cannot read them is refused.

**Voyager 2 stage copy**:
A working copy (`{SITE}_{T#}_3D`, the pulled processing folder Voyager 2 opened and saved) or an output folder (`{SITE}_{T#}_3doutput`) a Voyager 2 turn left under `defaults.voyager2_stage_root`. A working copy goes once its deposit verified and no turn reserves the transect; an output folder only when it matches its Shelf copy; a test run copy under `test_<YYYYMMDD-HHMMSS>/` is never offered.

**Shelf comparison**:
The name-and-size diff between a local snapshot (`driver.verify.local_snapshot`) and a remote snapshot (`driver.remote.snapshot`) of one processing folder, with the `.processing.lock` control file left out of both sides. Its outcome is one of identical, differs, no registry row, not on the Shelf, processing right now, or error.

**Edit bench copy**:
The local folder of one transect under the edit bench (the folder VICARIUS owns for manual edit projects). Cancel and prune removes it only when the project never reached the push to the Shelf and no transect holds a certified edit; after a push it is kept as the complete record.

## Rewind

**Rewind**:
Taking one timepoint back to before step 1 (the Metashape reconstruction) or before step 0 (the frame extraction): the report and frames move into the rewind folder, the chunk leaves the shared project, the `status.csv` cells clear and the registry cells reset with one event each, through the registry's named reset `rewind`. A row whose manual edit is under way (`editing`, `review`, `upload`) is refused. Nothing is deleted.
_Avoid_: reset, rollback, undo

**Rewind folder**:
`<processing folder>/_rewind/<YYYYMMDD-HHMMSS>/`, holding the moved artifacts, the chunk script's result and the manifest `rewind.json` of one rewind.

**Chunk script**:
`scripts/rewind_chunk.py`, run under `metashape -r` to remove the chunks labelled with the readable id from the project and save; `--fake` treats the psx path as a JSON stand-in so tests need no Metashape.

**Rewind before Voyager 2**:
Taking a whole transect back to before Voyager 2: its Shelf output folder is renamed to `<name>_voy2_archive_<YYYYMMDD-HHMMSS>` and every row that carries a Voyager 2 result loses its step 2 cells, output location cells and voyager2 facts through the registry writer, with a note. Nothing is deleted.

**Spot redo**:
For Voyager 1, a rewind before step 1 plus the voyager1 fact `spot_redo` on the row, so the Voyager 1 setup window offers the timepoint again and passes `--force` for it. For Voyager 2, the voyager2 fact `redo_requested` on every complete row of the transect, so the Voyager 2 setup window offers the transect again.
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
