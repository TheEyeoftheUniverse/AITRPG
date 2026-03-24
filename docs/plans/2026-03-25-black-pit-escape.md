# Black Pit Escape Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make escape ending fully hard-coded around a post-ritual `纯黑地洞` map node.

**Architecture:** Extend runtime reveal conditions to recognize flags, derive ritual-destruction flags from successful destroy actions, then move the `escaped` ending trigger from “AI request after ritual destruction” to “enter revealed black pit after ritual destruction”.

**Tech Stack:** Python, JSON module config, existing session/map/ending runtime.

---

### Task 1: Persist the approved design

**Files:**
- Create: `docs/plans/2026-03-25-black-pit-escape-design.md`
- Create: `docs/plans/2026-03-25-black-pit-escape.md`

**Step 1: Save the design and plan docs**

Record the approved behavior so future module and runtime changes use the same escape chain.

### Task 2: Support flag-driven hidden location reveal

**Files:**
- Modify: `game_state/session_manager.py`

**Step 1: Extend reveal condition checks**

Allow `reveal_conditions.node_visible` and similar checks to succeed on effective runtime flags such as `ritual_destroyed`, not only clues/inventory.

**Step 2: Allow visible hidden nodes to expose their configured name when desired**

Support a location-level opt-in so `纯黑地洞` can appear on the map by name immediately after reveal.

### Task 3: Derive ritual destruction as hard runtime state

**Files:**
- Modify: `main.py`

**Step 1: Add runtime hard-change derivation**

When a successful `destroy` action targets:
- `粗绳`: set `粗绳已切断`
- `符咒地毯`: set `ritual_destroyed`, `carpet_burned`, `符咒地毯已焚毁`, and add clue `已破坏仪式`

**Step 2: Merge these changes before RhythmAI preview**

Keep later layers consistent without relying on LLM-authored flags.

### Task 4: Move escaped ending to a hard location endpoint

**Files:**
- Modify: `modules/default_module.json`

**Step 1: Add `black_pit` location**

Define a hidden location revealed by `ritual_destroyed`, connected from `ritual_chamber`, and marked as `ending_id=escaped`.

**Step 2: Repoint escape validation**

Update `escaped.validation` to require:
- `ritual_destroyed`
- current location `black_pit`

Remove `require_ai_request`.

**Step 3: Update escape hardcoded text**

Make the immediate triggered text match entering the black pit rather than merely burning the ritual.

### Task 5: Validate and sync

**Files:**
- Modify: `modules/default_module.json`
- Modify: `game_state/session_manager.py`
- Modify: `main.py`

**Step 1: Validate JSON with explicit UTF-8 decoding**

Ensure the module still parses and contains no accidental tag escaping regressions.

**Step 2: Sync changed files to the AstrBot plugin copy**

Copy updated runtime and module files into the live plugin directory for testing.
