# Explicit Save Resume Design

**Date:** 2026-03-26

## Summary

The current WebUI restore flow resumes the last saved game automatically on page refresh. This is convenient when the player wants to continue immediately, but it creates a fragile failure mode: after refresh, the player may click a module card too quickly, start a new run, and lose the practical path back to the previous interrupted progress.

The goal is not to redesign save management. The goal is to make restore explicit:

- refreshing the page should not silently resume the game
- the player should see a visible continue entry for the interrupted run
- starting a new run should require an overwrite confirmation when an interrupted save already exists

This keeps the existing single-save-per-browser model and avoids touching AI, rule, or session runtime logic.

## Approaches

### 1. Explicit single-save resume

Keep the current `cookie_id -> one JSON save` model, but stop auto-restoring it on initial page load.

Add:

- a save summary endpoint
- a resume endpoint
- a frontend continue button on the matching module card
- an overwrite confirmation before starting a new run

This is the recommended approach because it solves the accidental-loss problem with the smallest code change.

### 2. Per-module interrupted saves

Store one interrupted save per module for the current browser cookie.

This improves flexibility, but requires changing the current `JsonSaveStore` data model from one file per cookie into either:

- one manifest plus multiple save files
- or one combined file with multiple named entries

This is heavier than needed for the stated problem.

### 3. Full save-slot management

Add multiple slots, manual naming, delete, and selection UI.

This is not aligned with the current objective and would add unnecessary complexity.

## Recommended Design

### 1. Keep the existing storage model

Do not change `JsonSaveStore` structure in V1.

The existing persisted payload already contains:

- `web_session`
- `game_state`

This is enough for explicit resume. V1 does not need true multi-slot saves.

### 2. Split "inspect save" from "restore save"

Today, the backend often restores the save as part of normal state access. V1 should separate these operations:

- **inspect only**
  Returns whether the current browser cookie has an interrupted save and exposes a compact summary.
- **restore**
  Actually recreates the in-memory session and returns the same kind of payload the frontend needs to enter the game UI.

This is the key behavioral change.

### 3. Show continue entry on the module card

When the page loads, the frontend should fetch:

- module list
- save summary

If a save exists, the frontend should mark the matching module card with a compact interrupted-run summary, for example:

- `继续存档`
- `第 8 回合 · 主卧`
- `保存于 14:32`

The player should explicitly click continue to restore.

### 4. Starting a new run should require confirmation

If an interrupted save exists and the player clicks normal start instead of continue, the frontend should ask for confirmation before calling the start endpoint in overwrite mode.

The warning only needs to say that the current interrupted progress will be replaced.

### 5. Do not auto-enter gameplay on refresh

On page load:

- if there is no save summary, show the module list normally
- if there is a save summary, still remain on the module selection page

The WebUI should only enter gameplay after:

- explicit continue
- or confirmed new start

## API Shape

### `GET /trpg/api/save-summary`

Returns only summary data, without restoring runtime session state.

Suggested payload:

```json
{
  "has_save": true,
  "save": {
    "module_index": 0,
    "module_name": "黑森林宅邸",
    "round_count": 8,
    "current_location": "master_bedroom",
    "current_location_name": "主卧",
    "saved_at": "2026-03-26T14:32:10+08:00",
    "game_over": false
  }
}
```

### `POST /trpg/api/resume`

Restores the interrupted save into memory and returns the same kind of UI bootstrap payload currently used by the auto-restore path:

- chat history
- game state
- map data
- workflow cache if available
- ending state

### `POST /trpg/api/start`

Keep the existing endpoint, but support an overwrite confirmation flag:

```json
{
  "module_index": 0,
  "force_new": true
}
```

If a save exists and `force_new` is not true, the backend may either:

- reject with a clear message
- or let the frontend handle the warning purely client-side after reading save summary

For V1, frontend-only confirmation is acceptable and cheaper.

## Data Rules

- V1 only supports one interrupted save per browser cookie.
- Finished games should not be shown as resumable interrupted saves.
- Save summary should be derived from persisted JSON only.
- Resume should restore exactly the stored session and not create a fresh session first.

## Files Expected To Change

- `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\webui\server.py`
- `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\webui\static\js\app.js`
- `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\webui\templates\index.html`
- `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\webui\static\css\style.css`
- optional focused tests for WebUI session restore helpers

## Risks

### 1. In-memory session drift

If the save summary is inspected without restore, the UI must not accidentally treat stale in-memory state as active gameplay. The backend should prefer persisted save data for summary mode.

### 2. Overwrite ambiguity

If the user starts the same module again, the UI still needs to distinguish:

- continue interrupted run
- intentionally overwrite interrupted run

That must be explicit.

### 3. Ended games shown as resumable

If `game_over=true`, the summary should not advertise `继续存档`.

## Verification Targets

- Refreshing the page with an interrupted save should keep the player on module selection.
- The matching module card should show a visible continue entry.
- Clicking continue should restore the exact prior progress.
- Clicking start new should require confirmation before overwriting.
- Completed endings should not appear as interrupted saves.
