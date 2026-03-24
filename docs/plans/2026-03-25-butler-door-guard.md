# Butler Door Guard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Align butler follow/door interactions with the module's intended fiction and remove false dodge states around guarded rooms.

**Architecture:** Keep the butler runtime `location` as the UI-facing display room, but split movement logic into direct-contact checks and guarded-door checks. Resolve bait completion into either a true idle wait or an active blocked-door guard depending on the destination room.

**Tech Stack:** Python, existing `SessionManager` runtime state, unittest regression coverage.

---

### Task 1: Freeze the chase-state semantics

**Files:**
- Modify: `game_state/session_manager.py`
- Modify: `game_state/location_context.py`

**Steps:**
1. Extend the default chase state with `blocked_at`.
2. Add helpers for butler contact location and guarded room classification.
3. Update chase context builders so UI/narrative can distinguish `same_room` from `blocked_outside_current_room`.

### Task 2: Fix bait completion and blocked-door persistence

**Files:**
- Modify: `game_state/session_manager.py`

**Steps:**
1. When the butler reaches a `has_door` bait destination, keep the threat in an active blocked-door state.
2. When the destination has no door, clear blocked-door residue and let the butler fall back to waiting.
3. Ensure door-blocking actions sync the butler display location to the guarded room.

### Task 3: Fix movement and exposure rules

**Files:**
- Modify: `game_state/session_manager.py`

**Steps:**
1. Suppress dodge checks for blocked-door states by using contact-location logic.
2. Trigger capture immediately when the player tries to leave a guarded room.
3. Block entry into a guarded room without treating it as same-room dodge.
4. Reduce same-room delayed capture to one extra round after entry.

### Task 4: Add regression coverage

**Files:**
- Create: `tests/test_butler_door_guard.py`

**Steps:**
1. Cover delayed same-room capture after activation.
2. Cover hallway movement near a guarded room without false dodge.
3. Cover blocked entry into a guarded room.
4. Cover immediate ending when opening a guarded room door from inside.
