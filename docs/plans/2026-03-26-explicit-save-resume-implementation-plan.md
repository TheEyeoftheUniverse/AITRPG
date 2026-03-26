# Explicit Save Resume Implementation Plan

**Goal:** Stop auto-resuming interrupted WebUI games on refresh and replace it with an explicit continue entry on the matching module card.

**Architecture:** Reuse the current `JsonSaveStore` and persisted `web_session + game_state` payload. Add one read-only save summary path and one explicit resume path. Keep the runtime session model unchanged.

**Tech Stack:** Python, Quart WebUI server, vanilla JavaScript frontend, optional unittest

---

### Task 1: Add read-only save summary helpers

**Files:**
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\webui\server.py`

**Steps:**

1. Add a helper that reads persisted save JSON without restoring runtime session state.
2. Derive a compact summary from persisted data:
   - module index and module name
   - round count
   - current location key and display name
   - save timestamp
   - game over flag
3. Ignore saves that are invalid or already concluded if they should not be resumable.

### Task 2: Split summary and restore APIs

**Files:**
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\webui\server.py`

**Steps:**

1. Add `GET /trpg/api/save-summary`.
2. Add `POST /trpg/api/resume`.
3. Keep `POST /trpg/api/start` for new runs.
4. Make initial page bootstrap stop auto-restoring interrupted sessions on refresh.

### Task 3: Keep overwrite behavior explicit

**Files:**
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\webui\server.py`
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\webui\static\js\app.js`

**Steps:**

1. Allow `start` requests to carry `force_new`.
2. If an interrupted save exists and `force_new` is not set, either:
   - return a conflict-style error
   - or rely on frontend confirmation before sending overwrite
3. Prefer the frontend-confirmation path for V1 to keep the backend thin.

### Task 4: Update module cards with continue entry

**Files:**
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\webui\static\js\app.js`
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\webui\templates\index.html`
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\webui\static\css\style.css`

**Steps:**

1. Load modules and save summary during initial page setup.
2. If a saved interrupted run exists, decorate the matching module card with:
   - continue button
   - round/location summary
   - save time
3. Keep all other module cards as normal start entries.
4. If the player clicks start while a save exists, show overwrite confirmation.

### Task 5: Wire explicit resume into existing game bootstrap

**Files:**
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\webui\server.py`
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\webui\static\js\app.js`

**Steps:**

1. Make `resume` return the same state payload shape the frontend already uses for active gameplay.
2. Reuse existing UI hydration logic for:
   - chat messages
   - player status
   - map data
   - last workflow
   - ending phase
3. Ensure continue enters the game UI only after the explicit resume response succeeds.

### Task 6: Add focused regression coverage

**Files:**
- Create or modify focused tests around WebUI save helpers

**Steps:**

1. Verify save summary does not restore runtime session state.
2. Verify resume restores persisted interrupted state.
3. Verify finished games are not exposed as resumable interrupted saves.
4. Verify start-new overwrite handling stays explicit.

### Task 7: Verify manually

**Files:**
- No code changes expected

**Steps:**

1. Start a game, play several turns, refresh the page.
2. Confirm the page stays on module selection.
3. Confirm the matching module card shows continue summary.
4. Confirm continue restores the exact interrupted progress.
5. Confirm start-new warns before overwrite.
