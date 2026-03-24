# Follow Arrival Reactions Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add module-declared follow-arrival reaction hooks so a follow NPC can trigger one first-visit reaction per NPC+location for RhythmAI and NarrativeAI.

**Architecture:** Add a generic runtime hook in the move-arrival path, persist a `follow_arrival_seen` map in session state, and inject normalized `npc_reactions` data into location/rhythm/narrative context. Keep the feature soft-only: no rule adjudication changes, no hard state requirements, and no per-move repeated triggering.

**Tech Stack:** Python, JSON module schema, existing SessionManager/Main/RhythmAI/NarrativeAI pipeline

---

### Task 1: Add runtime state and helper APIs

**Files:**
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\game_state\session_manager.py`

**Step 1: Add default state**

- Initialize `world_state.follow_arrival_seen` as `{}` for new sessions and existing-session normalization.

**Step 2: Add helper methods**

- Add methods to:
  - read seen locations for one NPC
  - check whether `npc + location` is unseen
  - mark `npc + location` as seen
  - collect follow NPCs arriving with player at a target location

**Step 3: Keep helpers generic**

- No hardcoded NPC names
- No hardcoded location names

### Task 2: Normalize module follow-arrival context

**Files:**
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\game_state\location_context.py`

**Step 1: Add extraction helpers**

- Read `locations.<key>.npc_reactions`
- Read each current-scene `objects.<key>.npc_reactions`

**Step 2: Add runtime context fields**

- Build:
  - `follow_arrival_reactions`
  - `follow_arrival_objects`

**Step 3: Keep it soft-only**

- Do not alter `runtime_description`
- Do not merge these fields into hard rule fields

### Task 3: Trigger first-visit rhythm/narrative on pure move

**Files:**
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\main.py`

**Step 1: Add move-time decision**

- In the move arrival path, compute whether this move should force follow-arrival judgement.

**Step 2: Build reaction payload**

- Build `follow_arrival_reaction_context` containing:
  - target location key
  - newly triggered NPC names
  - matching location reactions
  - matching object reactions

**Step 3: Force one arrival pass**

- If payload is non-empty, force the move flow to call RhythmAI/NarrativeAI once even if the normal arrival gate would have skipped.

**Step 4: Mark seen**

- After successful payload construction, record all triggered `NPC + location`.

### Task 4: Feed the new context into RhythmAI and NarrativeAI

**Files:**
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\ai_layers\rhythm_ai.py`
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\ai_layers\narrative_ai.py`
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\ai_prompts.json`

**Step 1: RhythmAI scene context**

- Include `follow_arrival_reaction_context` in scene context when present.

**Step 2: NarrativeAI prompt context**

- Include the same payload in the narrative prompt/fallback path.

**Step 3: Prompt contract**

- Tell the model these fields are soft guidance for follow-NPC reactions on first arrival only.

### Task 5: Update module examples

**Files:**
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\modules\default_module.json`
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\modules\default_module_npc_v1.json`

**Step 1: Add example fields**

- Add `npc_reactions` to several locations and objects for Emily.

**Step 2: Keep examples representative**

- Cover at least:
  - one room
  - one investigation-heavy room
  - one ritual-related object

### Task 6: Verify and sync

**Files:**
- Modify as needed after validation

**Step 1: Validate JSON**

- Run `ConvertFrom-Json` for both module files.

**Step 2: Sanity-check references**

- Grep for new field names across runtime and AI layers.

**Step 3: Sync plugin copy**

- Copy updated plugin files to `C:\Users\26459\.astrbot\data\plugins\aitrpg`.

