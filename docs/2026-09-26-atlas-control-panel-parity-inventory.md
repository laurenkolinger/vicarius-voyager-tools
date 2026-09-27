# Parity inventory: the Atlas control panel

Written 2026-09-27 00:40 AST against the code of 2026-09-26 (the chain build snapshot of 23:40 AST). This is milestone C0 of program C, [2026-09-26-chain-c-atlas-control-panel-design.md](/mnt/rip/vicarius_drive/docs/superpowers/specs/2026-09-26-chain-c-atlas-control-panel-design.md) section 10, and the instance file that section 8b of [the workflow page design](/mnt/rip/vicarius_drive/docs/superpowers/specs/2026-09-02-workflow-page-design.md) requires before the Voyager tools page becomes the Atlas control panel. Milestone C9's parity reviewer checks every row below against the running page.

Plain words:

- **Voyager tools**: the admin page served by this clone at `/voyager-tools` (module `voyager_tools`). Its display name becomes **Atlas control panel** (decision 9 of [2026-09-26-manual-edit-chain-decisions.md](/mnt/rip/vicarius_drive/docs/superpowers/specs/2026-09-26-manual-edit-chain-decisions.md)); every code name stays.
- **Pane**: one tool of the page, chosen from the list at its left.
- **Home that moves**: a restricted control that lives elsewhere on the desktop today and moves into the panel.
- **Preserved**: the item keeps its behaviour and route. **Moved**: same behaviour, new place. **Changed**: behaviour or copy changes; the row cites the decision and the spec section.

Approval: decisions 8 and 9 come from Lauren's brief of 2026-09-26 (items R9, R11, R15 of the mapping synthesis), and she said to build overnight on 2026-09-26 23:55 AST with every overnight choice listed in the morning summary for her veto. A `changed` row is final only after that review.

## 1. Base inventory and what changed since it

The full row by row inventory of the eight tools is [2026-09-04-voyager-tools-window-inventory.md](/mnt/rip/vicarius_drive/docs/superpowers/specs/2026-09-04-voyager-tools-window-inventory.md): 24 routes (R1 to R24), 63 control rows (C1 to C63), 4 browser keys (K1 to K4), 4 URL forms (H1 to H4), 10 server stores (S1 to S10), 7 keyboard rows (B1 to B7), 13 error states, 15 links, 18 pins. Every one of those rows still holds on 2026-09-26 except the deltas below, read from `voyagertools/views.py`, `templates/voyager_tools/panel.html` and `static/voyager_tools.js` in this clone.

| Delta | Rows affected | Code of 2026-09-26 |
|---|---|---|
| D1. Every route kept its path, method, body and answer; only the line numbers moved. | R1 to R23 | page `views.py:379`, fragment `424`, state `433`, order GET `462`, POST `469`, lock `489`, unlock `504`, workbench `520`, delete `532`, manual edit `578`, cancel `590`, rewind rows `632`, plan `662`, apply `697`, spot redo `704`, prompts `820`, params `911`, load `926`, validate `941`, branch `967`, default `990`, seasons GET `1014`, POST `1022` |
| D2. The asset version is `20260906a` (was `20260904a`). | R2, pins | `views.py:54`; `panel.html` `data-vt-version` |
| D3. The page was converted to the design language: the four result tables became row lists (`order-body`, `wb-body`, `pm-body`, `se-body` as `ul.rows`), the chrome uses the shared block (`.head`, `.title`, `.tip`, `.choices`, `.btn`). No route, JSON key or `data-vt-*` name of a control changed. | C7, C18, C46, C57 | `panel.html` |
| D4. The processing order gained four ways to reorder (T20260906-213500-1): drag and drop on the rows, a position field per row, a tick box per row with shift range and **Move selected to** (`order-move-to`, `order-move-btn`, `order-clear-btn`, `order-select-count`), and a focusable drag handle moved by ArrowUp and ArrowDown. Save order posts the same `{transects, initials}` body. | C7, C8, B6; new rows C7a to C7d | `voyager_tools.js` header comment, `panel.html` `order-toolbar` |
| D5. Initials accept one to eight letters or digits in this page (`INITIALS_RE`); programs A and B require two to four letters. Program C's C3 changes the page to the stricter rule. | C2, C63 | `voyager_tools.js:52` |

## 2. Homes that move into the panel

Each is a restricted control that lives outside this page today.

| # | Home today | What it does | Route or store | Source |
|---|---|---|---|---|
| N1 | Admin window, Appearance block: skin radios (pink, business), grid, scanlines and pixels tick boxes, grid size (8 to 120 px), background colour, Save | saves the shared desktop appearance for everyone | `POST /api/desktop/prefs {appearance}` into `vicarius_ui_os/config/desktop_prefs.json` | `vicarius_ui_os/static/desktop_boot.js`, `openAdminWindow` |
| N2 | Admin window, **Lock admin** | ends admin mode for the browser | `POST /api/admin/lock` | same |
| N3 | Desktop title-bar padlocks on every module window | lock or unlock one module for everyone | `POST /api/modules/<slug>/lock {locked}` into `vicarius/_METADATA/module_locks.json` | `vicarius_ui_os/static/desktop_locks.js`, `lockBtnHtml`, `onLockClick` |
| N4 | Locked badges on icons and the locked page | show a locked module to everyone | `GET /api/modules/locks` | `desktop_locks.js`, `refresh`, `applyAll` |
| N5 | Module Menu metadata editor: the password lock button `#mbLockBtn`, the unlock and edit dialogs, the edit affordance on each card (display name, description, tags, category, pipeline stage, status) | edits the shared module metadata | `/api/modules/meta/unlock`, `/lock`, `/status`; `POST /api/modules/<slug>/meta` into `vicarius_ui_os/config/module_meta.json` | `vicarius_ui_os/static/module_browser.js` (`doUnlock`, `openEdit`, `saveEdit`, `setLockBtn`), `templates/modules_list.html` |
| N6 | Module Menu category order (drag in the category rail) | saves the category order for everyone | `POST /api/modules/category-order` | `module_browser.js` |
| N7 | Manual edit settings panel in the Start manual edit page | edits the manual edit limits and switches (`scale_error_max_mm`, `scale_error_max_percent`, `align_spread_max_mm`, `markers_per_chunk`, `edit_bench_default`, `open_close_logging`, `archive_shelf_copy`; the retired `delete_local_after_verify` and `scale_error_threshold_m` are still in the file) | `GET` and `POST /manual-edit/api/settings` into `vicarius/_METADATA/3d/manual_edit/settings.json` | `manual_edit/github_repo/manualedit/views.py` `api_settings`, `static/manual_edit.js` |
| N8 | Atlas admin links (`ADMIN_LINKS`: order, manual, settings) | open this page at a tool, or the manual edit page, after the PIN | `/voyager-tools#vt-order`, `#vt-manual`, `/manual-edit` | `vicarius_ui_os/static/atlas.js` `ADMIN_LINKS` |
| N9 | Atlas toolbar link **Voyager tools** | opens this page | `/voyager-tools` | `vicarius_ui_os/templates/atlas.html`, `data-atlas="voyager-tools"` |
| N10 | Desktop DEV folder tile **VOYAGER TOOLS** (admin only) | opens this page as a desktop window | fragment `/modules/voyager_tools/fragment` | `desktop_boot.js`, DEV folder items |
| N11 | The top-bar admin chip (`#tb-admin`) | shows `ADMIN` or `locked`; opens the Admin window | `GET /api/admin/status` | `desktop_boot.js`, `VICADMIN` |
| N12 | Voyager 1 run defaults (`voyager1.run_enabled`, `voyager1.delete_from_workbench_default`), edited by hand in the file | the switch in front of real Voyager 1 runs and the default of the Workbench removal tick box | `vicarius_ui_os/config/voyager.json` | `vicarius_ui_os/voyager_setup.py` |
| N13 | Driver space floors, edited by hand | the free-space floors every transfer checks | `vicarius/modules/driver/github_repo/config/nas.yaml` `defaults.min_free_gb_local`, `min_free_gb_remote`, `headroom_factor` | the driver clone |
| N14 | GPU ladder, edited by hand | the right-of-way order | `vicarius/_METADATA/gpu_ladder.yaml` | `vicarius_ui_os/gpu_ladder.py` |

## 3. Parity mapping

One row per item of section 1 (grouped where every row of a group has one fate) and of section 2. No row is dropped.

| Item | Where it lives after program C | Status | Decision and spec section |
|---|---|---|---|
| R1 to R23, R24 (every route) | the same blueprint routes; `guarded` also marks a passing admin check for renewal | preserved | spec 5.1 (renewal is server side only) |
| R2 fragment | same; a new `tool=panel` and `tool=review` answer beside it | preserved, extended | spec 4.11, C3 and C6 |
| C1 title "Voyager tools" | title "Atlas control panel" | changed | decision 9; spec 3.2, C3 |
| C2, C63 initials | initials at the top of the panel, two to four letters | changed | build order cross-spec fix on initials; spec 3.3, C3 |
| C3 admin line | shown for any 401 or 403 carrying `need_unlock`, with **Unlock admin**; the panel swaps to it on every relock | changed | decision 8; spec 3.8, 3.10, 5.1, C1 |
| C4 tool list (eight tabs) | the grouped pane list (Records, Manual edit, Voyager, Desktop, History); the eight keys and `vt.tab` stay | moved | decision 9; spec 3.2, C3 |
| C5 to C14, C7a to C7d Atlas edit | pane Atlas edit under Records | moved | spec 3.2 |
| C15 to C20 Clean workbench | pane Clean workbench under Voyager | moved | spec 3.2 |
| C21 to C25 Clean manual edit run | pane Clean manual edit run under Manual edit; the refusals follow every unfinished project | moved, guards changed | spec 5.3, C3a |
| C26 to C32 Rewind | pane Rewind under Voyager; refuses a row whose status is a manual edit status in flight | moved, guard changed | spec 5.3, C3a |
| C33 to C38 Spot redo | pane Spot redo under Voyager | moved | spec 3.2 |
| C39 to C43 Prompts doc | pane Prompts doc under Records | moved | spec 3.2 |
| C44 to C55 Parameter defaults | pane Parameter defaults under Voyager; each save also writes a settings history line | moved, logging added | decision 10; spec 5.2, C2 |
| C56 to C58 Camera model | pane Camera model under Records (audited in `events.csv` only) | moved | spec choice C-i |
| C59 confirm dialog | same | preserved | |
| C60 footer credit | "Atlas control panel - built by Lauren Olinger" | changed | decision 9; spec 3.2 |
| C61 `mount(el)` | kept; `mountPanel(el, {onClose, pane})` beside it | preserved, extended | spec 6.1 |
| C62 request helper | 401 or 403 with `need_unlock` read the same; a 30 s timeout | changed | decision 8; spec 5.1, 8 |
| K1 `vt.tab` | same key; an unknown value falls back to Atlas edit | preserved | spec 7 |
| K2 to K4 | same | preserved | |
| H1 to H4 | same; `?view=control&pane=<key>` in the atlas | preserved, extended | spec 5.4, C4 |
| S1 to S10 | same stores; settings stores gain history lines | preserved | spec 4.2 |
| B1 to B7 | same | preserved | |
| N1 Appearance block | pane Appearance under Desktop | moved | spec choice C-e, 5.6, C5 |
| N2 Lock admin | stays in the Admin window beside **Stay in admin mode** and the countdown | preserved | spec 3.1, C1 |
| N3 title-bar padlocks | pane Module locks under Desktop; the title-bar button goes | moved | spec choice C-e, C5 |
| N4 locked badges and page | stay for everyone | preserved | spec choice C-e |
| N5 metadata editor | pane Module details under Desktop; the save routes move to the admin gate; the three password routes stay until the secrets decision | moved | spec choice C-c, open item 1, C2 and C5 |
| N6 category order | pane Module details | moved | spec 5.6, C5 |
| N7 manual edit settings panel | panes Manual edit settings and Shelf copy; the retired keys are not shown; the module's POST goes when the panes go live | moved | decisions 6 and 10; spec 3.3, 3.4, interface change 10, C3 |
| N8 atlas admin links | open the panel's Control panel tab at their pane | changed | spec choice C-a, C4 |
| N9 atlas toolbar link | removed; the Control panel tab and the summary-line button replace it | changed | spec choice C-a, C4 |
| N10 DEV tile | label ATLAS CONTROL PANEL, same key and admin flag | changed | decision 9; spec 5.1, C3 |
| N11 admin chip | counts down `ADMIN m:ss`, warns at 60 s, relocks every window | changed | decision 8; spec 3.1, C1 |
| N12 Voyager 1 run defaults | pane Voyager runs under Voyager, with confirm and reason on `run_enabled` | moved | spec choice C-b, 4.9, C3 |
| N13 space floors | pane Space floors under Desktop, with confirm and reason | moved | spec 3.3, 4.2, C3 |
| N14 GPU ladder | pane GPU ladder under Desktop, read only | moved (read only) | ruling G1; spec 11, C3 |

## 4. Counts

| Category | Count |
|---|---|
| Base rows carried from the 2026-09-04 inventory | 24 routes, 63 controls, 4 browser keys, 4 URL forms, 10 stores, 7 keyboard rows |
| New control rows from delta D4 | 4 (C7a to C7d) |
| Homes that move (section 2) | 14 |
| Mapping rows (section 3) | 37 grouped rows covering every item |
