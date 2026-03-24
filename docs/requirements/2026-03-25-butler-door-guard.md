# Butler Door Guard Requirements

**Date:** 2026-03-25

**Goal:** Fix the butler chase state so door-blocked follow behavior matches the module fiction and no longer causes false dodge checks in adjacent spaces.

## Accepted Behavior

- Entering the butler's current area activates pursuit, but the entry turn does not force a dodge check.
- If the player remains in direct same-room exposure for the next round, the butler capture ending triggers.
- When the butler reaches a destination with `has_door=true`, the frontend should treat the butler as being at that room.
- A door-guarded room is not the same as direct same-room contact. Nearby hallway movement must not be misclassified as dodge-required.
- If the player is inside a door-guarded room and tries to leave, that door opening immediately triggers the bad ending.
- If the player is outside a door-guarded room and tries to enter, movement is blocked rather than treated as same-room dodge.
- When a bait task completes at a door room, the companion returns to `wait`, and the butler remains in a stable guarded-door state instead of reverting to a stale blocked residue.

## Constraints

- Keep the butler visible on the map/UI at the guarded room.
- Preserve existing chase narration semantics for `same_room`, `separate_rooms`, and blocked-by-door cases.
- Do not revert unrelated user edits in module JSON files.

## Non-Goals

- Redesign the entire threat system.
- Change non-door chase behavior outside this bugfix.
