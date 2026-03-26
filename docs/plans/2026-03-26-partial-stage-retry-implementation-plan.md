# Partial Stage Retry Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Cache each completed AI stage for the current turn, expose those partial results through progress polling, and make retry resume automatically from the failed stage.

**Architecture:** Reuse the existing `_last_action_cache` and action progress structures as the single current-turn snapshot. The backend writes stage outputs as soon as each stage succeeds, `/trpg/api/progress` returns both telemetry and partial results, and the frontend updates left workflow panels incrementally while using a backend-provided retry hint.

**Tech Stack:** Python, Quart/Flask-style WebUI server, vanilla JavaScript frontend, unittest

---

### Task 1: Add current-turn partial result snapshot helpers

**Files:**
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\main.py`

**Steps:**

1. Add helper methods that read current-turn cache into a normalized `partial_results` payload and derive `retry_from_hint`.
2. Make the helpers tolerant of missing fields so partial stage results can be returned mid-turn.
3. Keep the cache strictly scoped to the current turn.

### Task 2: Persist stage outputs immediately after each successful stage

**Files:**
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\main.py`

**Steps:**

1. After RuleAI succeeds, write rule outputs and step metrics into `_last_action_cache` before moving to RhythmAI.
2. After RhythmAI succeeds, write `rhythm_result` and step metrics into `_last_action_cache` before moving to NarrativeAI.
3. On failure, write `retry_from_hint` based on the failed step before re-raising.

### Task 3: Extend progress and action error responses

**Files:**
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\webui\server.py`

**Steps:**

1. Extend `/trpg/api/progress` to return `partial_results` and `retry_from_hint` with the existing `progress`.
2. Update action and retry error responses to use the same helper output instead of duplicating field assembly.
3. Keep completed-turn responses backward compatible.

### Task 4: Refresh workflow panels incrementally on the frontend

**Files:**
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\webui\static\js\app.js`

**Steps:**

1. Update progress polling to consume `partial_results`.
2. Refresh the left rule panel when cached rule outputs appear.
3. Refresh the rhythm panel when cached rhythm output appears.
4. Track the latest `retry_from_hint` from the backend and use it for retry.

### Task 5: Add regression tests for staged retry behavior

**Files:**
- Modify: `C:\Users\26459\Desktop\AI驱动跑团项目\aitrpg\tests\test_llm_failure_retry_only.py`
- Create or modify additional focused tests if needed

**Steps:**

1. Add tests for rule-stage partial cache availability.
2. Add tests for rhythm-stage partial cache availability and retry hint selection.
3. Add tests proving narrative retry reuses cached rhythm results instead of rerunning earlier stages.

### Task 6: Verify behavior

**Files:**
- No code changes expected

**Steps:**

1. Run targeted unit tests for retry/cache behavior.
2. If available locally, run an additional focused sanity check for WebUI progress payload shape.
3. Summarize any remaining manual verification gap.
