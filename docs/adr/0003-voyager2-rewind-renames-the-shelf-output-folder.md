# 0003: A Voyager 2 rewind renames the Shelf output folder, per transect; a Voyager 2 spot redo asks for a forced rerun

Status: accepted, 2026-09-27 (LO)
Source: program D spec Amendment 1, A1.12 items 20 and 21 and milestone D1i, `/mnt/rip/vicarius_drive/docs/superpowers/specs/2026-09-26-chain-d-voyager2-and-space-guard-design.md`; decision 11 and ruling O13 of `/mnt/rip/vicarius_drive/docs/superpowers/specs/2026-09-26-manual-edit-chain-decisions.md`; the build plan `/mnt/rip/vicarius_drive/docs/superpowers/plans/2026-09-27-chain-d1i.md`.

## Context

The 2026-09-04 Voyager 2 spec had the rewind move one row's products into `_3doutput/_rewind/<stamp>/` and the spot redo clear files and cells before a forced rerun. Since then Voyager 2 builds one output folder and one psx per transect on the Shelf (spec D choice C14 and A1.3), the driver deposits in archive mode by renaming the old Shelf folder to `<name>_voy2_archive_<stamp>` (A1.4, C7 amended), and the registry's readiness rule already reads a `voyager2.redo_requested` fact newer than step 2's finish as ready again.

## Decision

- The rewind before Voyager 2 acts on the whole transect: every row of the site and transect that carries a step 2 record or a voyager2 fact. It renames the Shelf output folder through the driver's own `phase2.archive_output` (the same rename and name the archive deposit uses), then clears the step 2 cells and the output location cells, points the console log into the renamed folder, blanks the voyager2 facts and appends a note, all through the registry writer. It renames in either Shelf copy mode, because a rename deletes nothing and the overwrite setting governs uploads only. A test run's output folder, recorded only in the voyager2 fact `output_shelf` and named like a real one, is renamed the same way, so a later real turn cannot pull it and reuse the test run's products.
- The Voyager 2 spot redo writes `redo_requested` on every complete row of the transect and moves nothing. The Voyager 2 setup window sends the next turn of that transect with `force` (the registry's `voyager2_redo_pending`: a readable request that no complete step 2 has answered, so a failed redo run keeps it), and the driver passes `--force` to Voyager 2. Without it the turn would pull the old output folder back with its resume record `logs/step2_results/_run_state.json`, and Voyager 2 would reuse every verified product. The turn's archive deposit renames the old folder.
- The Clean workbench tool never scans or deletes inside the edit benches, the Voyager 2 and VIGIL stages or the transfer ledger, and it lists the Voyager 2 stage on its own: a working copy goes once its deposit verified and no turn reserves the transect, an output folder only when it matches the Shelf.

## Alternatives

- Move one timepoint's products into `_rewind/` on the Shelf. Rejected: it needs several remote moves in a folder another timepoint's manifest and psx still describe, and a row rewound alone leaves a transect Voyager 2 would never offer (C14).
- Delete the Shelf output folder in overwrite mode. Rejected: a panel button would delete up to 180 GB of products on the NAS with no copy left; an archive folder can be deleted by hand once nobody needs it.
- Clear cells and files for the spot redo. Rejected: the readiness rule already honours the request, and clearing first would leave the atlas without products until the rerun finishes.

## Consequences

A transect rewound twice leaves two `_voy2_archive_` folders on the Shelf until someone removes them by hand. VIGIL records (`vigil.staged_manifest`, `vigil.project_folder`) point at VIGIL's own copies on the VIGIL stage, not at the Shelf folder, so the rename leaves them alone; VIGIL keeps working from copies of the products the rewind retires, and the rewind asks for an explicit confirmation when any exist. The registry's `redo_requested` rule is no longer inert.

Cost if wrong: switching to per-timepoint moves means a remote move helper, a per-timepoint manifest rewrite in the Voyager 2 output folder, and new tests; the renamed archive folders already on the Shelf stay valid either way.
