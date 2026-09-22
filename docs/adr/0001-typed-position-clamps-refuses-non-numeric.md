# A typed block position clamps a whole number and refuses anything else

Date: 2026-09-06 (LO)

The Processing order table's new "#" field (per row) and "Move selected to" field (the toolbar) both accept a typed position for one or more transect blocks. A number outside the valid range and a non-numeric entry needed one rule, not two, so every path through the table behaves the same way. The rule: a whole number is clamped to the nearest valid end (0 or a negative number becomes 1; a number past the last position becomes the last position); a blank entry, letters, or a decimal is not a position at all and refuses with a message in the result line, leaving the block where it was.

Status: accepted

## Considered options

- Refuse every out-of-range number too (only 1 to N is legal): loses the fast path a person actually wants ("move it to the very top" without checking N first) and forces a second attempt for the extremely common last-to-first, first-to-last moves the table exists to make fast.
- Clamp everything, including a non-number (treat "abc" or blank as 1 or as "no change"): quietly guessing when the user typed non-numeric text hides a mistake (a stray key, a copy-paste error) that Lauren would want surfaced with 116 rows in play; a whole-number-only parser gives an unambiguous line between "you gave me a position I can use" and "that was not a position."
- Accept a decimal and truncate it (parseInt-style): adds a silent rounding step for input that was never a valid position in the first place; rejecting it outright is simpler to explain in one tooltip sentence and matches the "whole number" wording used everywhere else on the row.

## Consequences

The row's position tooltip, the toolbar's "Move selected to" tooltip, and the result-line refusal message all state the same "whole number" rule, so a future edit to one must edit all three or the pane's own tests (tests/test_order_reorder.py, StaticGuardsCase.test_position_parser_is_the_single_rule and ReorderBrowserCase.test_typed_position_edge_cases) will fail. The clamp behaviour means a typed "0" and a typed "1" do the same thing, and a typed "999" on an 8-block list does the same thing as a typed "8"; this is intentional and is exactly what makes the field usable without first counting the rows.

Source: T20260906-213500-1 (vicarius/_METADATA/logs/todo.md)
Cost if wrong: switching to a strict refuse-on-out-of-range rule means editing parsePositionInput's two call sites (commitPosition, the Move selected click handler) in static/voyager_tools.js, the three tooltip sentences (panel.html's Move-to tip, and the two title strings renderOrder() builds), and every clamp-path assertion in tests/test_order_reorder.py (pos_to_1 through pos_negative_clamps_to_1 in test_typed_position_edge_cases and the locked-order variants) - a same-day change, not a rewrite, but touching five spots that must stay in step.
