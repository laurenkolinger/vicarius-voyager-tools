# 0002: The Atlas control panel is a display name; every code name stays

Status: accepted, 2026-09-27 (LO)
Source: decision 9 of `/mnt/rip/vicarius_drive/docs/superpowers/specs/2026-09-26-manual-edit-chain-decisions.md`; program C spec section 5.3 and milestone C3, `/mnt/rip/vicarius_drive/docs/superpowers/specs/2026-09-26-chain-c-atlas-control-panel-design.md`.

## Context

The page this module serves grew from the Voyager program's admin tools into the one window that holds every restricted setting and tool of the atlas, the Voyager runs, the manual edit and the desktop. People call it the Atlas control panel. The module's slug `voyager_tools` names its folder, its lock key in `module_locks.json`, its blueprint, its address `/voyager-tools`, its fragment `ui.style`, the actor prefix `voyager_tools:` of 59 lines already in the registry's `events.csv`, the browser key `vt.tab`, the CSS root class and every file name.

## Decision

Only what people read changes: `display_name` in `module.yaml`, the window title, the desktop tile, the headings, the footer, the admin line and the lock page. Every code name above stays.

## Alternatives

- Rename the slug and every code name to `atlas_control_panel`. Rejected: the lock file, the events already written, the atlas's links, the desktop's native pane table, the fragment `ui.style` and every test would need a coordinated migration for no change a person sees, and a missed reader would break silently.
- Keep the old display name. Rejected: the window no longer holds Voyager tools alone, and the brief names it the Atlas control panel.

## Consequences

A reader of the code meets `voyager_tools` where the screen says Atlas control panel; this record, the module's `CONTEXT.md` and `README.md` say so. A registry event signed `voyager_tools:LO` comes from the Atlas control panel.
