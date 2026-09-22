# Voyager tools

Last updated: 2026-09-04 (LO)

The admin-only tools page of the Voyager program (section 7 of `/mnt/rip/vicarius_drive/docs/superpowers/specs/2026-09-03-voyager-program-design.md`): eight tools on one page, a left list and one section each. It is a native blueprint the VICARIUS desktop mounts at `/voyager-tools` on port 5090 (`/mnt/rip/vicarius_drive/vicarius_ui_os/voyager_tools_views.py`), opened from the DEV folder item VOYAGER TOOLS, which only appears in admin mode, and from the VOYAGER TOOLS button in the atlas row view. Every route answers 401 outside admin mode and 423 while the module lock `voyager_tools` is on.

## The eight tools

1. Atlas edit: the processing order table. Transect blocks of rows Voyager 1 has not started, moved up and down or dragged; Save order writes `processing_order.csv` through `registry.set_processing_order`; Lock order in (the confirm names the count and the first and last ids) and Unlock go through `lock_processing_order` and `unlock_processing_order`.
2. Clean workbench: every `*_3dprocessing` folder under the Workbench roots, matched to its registry rows, compared to the Shelf copy by name and size (`driver.verify.local_snapshot` and `driver.remote.snapshot`), and Delete only when identical: confirmed, anchored inside the Workbench root, one processing_log row per deletion.
3. Clean manual edit run: the current manual edit job with its rows, history and log tail; Cancel and prune marks it cancelled, removes the edit bench copies when the job never reached the push, restores each row's Shelf location, sets `manual_edit_status` back to awaiting and clears `current.json`.
4. Rewind: pick a row and a target (before step 1 or before step 0); Dry run lists the moves, the chunk, the registry cells and the status.csv cells; Apply moves the artifacts into `<processing folder>/_rewind/<stamp>/`, removes the chunk through `scripts/rewind_chunk.py` under `metashape -r`, clears status.csv and resets the registry cells with events. Refused while the row is live, while its folder is on the NAS (the Carousel pull-back command is shown), or while the row sits in the manual edit job.
5. Spot redo: a rewind before step 1 plus the voyager1 fact `spot_redo`, so the Voyager 1 setup window offers the timepoint again.
6. Prompts doc: `/mnt/rip/vicarius_drive/docs/agents/voyager-ingest-prompts.md` rendered from Markdown.
7. Parameter defaults: every version, branch and custom set of a phase in voyagerparams with the default marked, the full editor from the schema, the YAML box, Validate, Save as branch, and Save as default behind a dialog that states the significance; every save commits and pushes through `paramsstore.commit_and_push`.
8. Camera model: a table editor over `seasons.csv` (camera model, preprocessing, notes per season) through `registry.set_season`.

## Admin mode

Every route in this module checks two gates in order: the module lock (`voyager_tools`; 423 while locked, the exact page "Under development. Please come back later. Email Lauren for questions." with a mailto to lauren.olinger@uvi.edu), then the desktop's admin session (401 `{"error": "locked", "need_unlock": true}` outside it; the cookie is `vicarius_session`, unlocked the same way as every other admin surface). Nobody, including the module owner, reaches a tool without both. On the desktop the page opens two ways, both admin-gated: the DEV folder item VOYAGER TOOLS (carries `admin: true` in `desktop_boot.js`, so `desktop_icons.js` hides it, and any folder window holding it, until `VICPREFS.admin` is set, and rebuilds the grid the moment admin mode is unlocked or locked again), and the VOYAGER TOOLS link in the atlas row-view toolbar (also hidden outside admin mode). The standalone host on port 5098 (`standalone_app.py`) gates on its own PIN (`VOYAGER_TOOLS_ADMIN_PIN`) instead of the desktop session, for smoke tests; the platform never starts it.

## The versioning rule (tool 7)

Every version, branch and custom parameter set for a phase (`voyager1` or `voyager2`) lives in the `voyagerparams` checkout (`/mnt/rip/vicarius_drive/vicarius/modules/voyagerparams/github_repo/`), read and written through `voyagertools/paramsstore.py`. Three ways a save reaches it, each with its own rule:

- **Save as default** (behind a dialog that states the significance, since it changes what every future run picks up by default) bumps the phase's version number: MINOR (`v1.2.0` to `v1.3.0`) when only values changed, MAJOR (`v1.2.0` to `v2.0.0`) when a key was added, removed or renamed. Saving a set identical to the current default is refused; there is nothing to version.
- **Save as branch** (name, description, why) starts at the base version's number under the branch's own name (`branches/<slug>/v1.2.0`), so a branch never collides with the default line; a later save to the same branch bumps within that branch.
- **Custom run sets** are written automatically at RUN time by the Voyager 1 and Voyager 2 setup windows, never by this tool: `custom/<run_id>.yaml`, and a custom set never becomes the default by itself.

Every save commits and, when the checkout has a remote, pushes to `laurenkolinger/voyagerparams` (public) through `paramsstore.commit_and_push`; a push failure is reported rather than raised, so a save is never lost to a flaky connection. The full parameter editor is generated from `voyagertools/params_schema_voyager1.yaml` (label, tooltip, type, default, and range or choices per key; `processing.max_chunks_per_psx` is marked hidden because the project structure controls it, not the operator), so a schema change is the only place a new key's tooltip needs to be written.

## Layout

- `voyagertools/views.py`: the blueprint `voyager_tools_bp` (page `/voyager-tools`, fragment `/voyager-tools/fragment`, JSON under `/voyager-tools/api/`), the two gates, and `deps`, one object of plain callables (registry, job store, parameter store, snapshots, rmtree, processing log, chunk runner, site names, prompts doc) that tests replace with fakes.
- `voyagertools/order_tool.py`, `clean_workbench.py`, `clean_manual_edit.py`, `rewind.py`, `camera_tool.py`: the tool logic, pure where it can be, with the registry library, `manualedit.jobstore` and `paramsstore` doing every write.
- `scripts/rewind_chunk.py`: the Metashape script (`metashape -r scripts/rewind_chunk.py <psx> --label <id> --out <json>`; `--fake` for tests).
- `templates/voyager_tools/panel.html` (the fragment, root `<section class="voyager-tools">`) and `index.html` (the standalone page); `static/voyager_tools.css` (scoped under `.voyager-tools`); `static/voyager_tools.js` (an IIFE exporting `window.VoyagerTools = {mount(el)}`).
- `standalone_app.py`: a smoke-test host on port 5098 (`VOYAGER_TOOLS_ADMIN_PIN=... python3 standalone_app.py --port 5098`); the platform never starts it.
- `voyagertools/paramsstore.py` and `params_schema_voyager1.yaml`: the voyagerparams reader and writer and the editor schema (plan task A7).

## API in one table

| Route | Body | Answer |
|---|---|---|
| GET /voyager-tools/api/state | | order lock, active job, default versions, Workbench roots |
| GET, POST /voyager-tools/api/order | {transects: [SITE_T#...], initials} | the order table; saved ids, count, first, last (409 when locked) |
| POST /voyager-tools/api/order/lock, /unlock | {initials, confirm} | lock stamp, count, first, last |
| GET /voyager-tools/api/workbench; POST .../workbench/delete | {path, initials, confirm} | folder plans; deleted path, bytes, log row (409 when not identical) |
| GET /voyager-tools/api/manual-edit; POST .../manual-edit/cancel | {initials, confirm} | the job summary; restored rows, pruned and kept paths (409 in flight or none) |
| GET /voyager-tools/api/rewind/rows; POST .../rewind/plan, .../rewind/apply, .../spot-redo | {readable_id, target, initials, confirm} | rows and targets; the plan; the manifest (409 with the pull-back command when the folder is on the NAS) |
| GET /voyager-tools/api/prompts | | the document as HTML |
| GET /voyager-tools/api/params?phase=, .../params/load?ref=; POST .../params/validate, .../params/branch, .../params/default | {phase, values, yaml_text, slug, description, why, initials, confirm} | entries, schema, values; problems and diff; the saved file and the git result |
| GET, POST /voyager-tools/api/seasons | {season_key, camera_model, preprocessing, notes, initials} | the season table; the saved line |

Every write needs `initials` (one to eight letters or digits) and every destructive one `confirm: true`, which the page sends after its dialog. A refusal is one sentence under `error` with status 400, 404 or 409.

## Tests

```bash
cd /mnt/rip/vicarius_drive/vicarius/modules/voyager_tools/github_repo
python3 -m unittest discover -s tests -q        # 246 tests on 2026-09-04
python3 tests/test_house_rules.py
cd /mnt/rip/vicarius_drive/vicarius_ui_os && .venv/bin/python -m pytest tests/test_voyager_tools_host.py -q
```

No test touches the live registry (every test sets `VICARIUS_3D_REGISTRY_ROOT` to a temporary root and reloads the library), the NAS, git, Metashape or a live process; the chunk script runs in `--fake` mode and the parameter store takes a fake git runner.

## Related documentation

- Rules for agents: `/mnt/rip/vicarius_drive/CLAUDE.md`; this clone's pointer is `CLAUDE.md` beside this file; the glossary is `CONTEXT.md`.
- The program spec and plan: `/mnt/rip/vicarius_drive/docs/superpowers/specs/2026-09-03-voyager-program-design.md`, `/mnt/rip/vicarius_drive/docs/superpowers/plans/2026-09-03-voyager-program-build.md`.
- voyagerparams README (the versioning rule in plain words): `/mnt/rip/vicarius_drive/vicarius/modules/voyagerparams/github_repo/README.md`.
- The registry library and its glossary: `/mnt/rip/vicarius_drive/vicarius/_METADATA/3d/registry.py`, `/mnt/rip/vicarius_drive/vicarius/_METADATA/3d/CONTEXT.md`.
- The manual edit job store: `/mnt/rip/vicarius_drive/vicarius/modules/manual_edit/github_repo/manualedit/jobstore.py`.
