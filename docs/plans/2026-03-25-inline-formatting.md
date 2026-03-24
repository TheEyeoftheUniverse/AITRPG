# Inline Formatting Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add safe inline emphasis formatting to narrative messages while keeping arbitrary HTML blocked.

**Architecture:** Introduce a whitelist HTML renderer in the WebUI that escapes everything first and only restores exact `b/strong/i/em/s/del` tags. Reinforce the same whitelist in the final narrative prompt so the model only emits supported tags.

**Tech Stack:** Vanilla JS, existing WebUI chat renderer, NarrativeAI prompt assembly, JSON prompt config.

---

### Task 1: Add safe whitelist rendering

**Files:**
- Modify: `webui/static/js/app.js`

**Steps:**
1. Add a helper that escapes full text and restores exact whitelist inline tags.
2. Use it for assistant chat bubbles.
3. Use it for `system-echo` rendering.

### Task 2: Update narrative prompt constraints

**Files:**
- Modify: `ai_prompts.json`

**Steps:**
1. Add whitelist wording to the persisted narrative prompt template.
2. Keep the allowed tags list aligned with the WebUI renderer.

### Task 3: Verify the change

**Files:**
- Modify: none

**Steps:**
1. Check the prompt files for the new whitelist wording.
2. Check `app.js` for the whitelist renderer call sites.
3. Run a small Node verification snippet against sample strings.
