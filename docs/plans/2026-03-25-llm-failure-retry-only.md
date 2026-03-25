# LLM Failure Retry-Only Implementation Plan

**Goal:** Delete the LLM-failure fallback mechanism so failed AI calls surface as errors and are retried by the player instead of producing stitched fallback text.

## Task 1: Fail fast in all AI layers

**Files:**
- `ai_layers/rule_ai.py`
- `ai_layers/rhythm_ai.py`
- `ai_layers/narrative_ai.py`

**Work:**
- Replace LLM failure fallback returns with explicit raised errors.
- Remove NarrativeAI raw-text echo behavior for invalid JSON.
- Remove NarrativeAI internal compact/fresh-provider retry attempts.

## Task 2: Delete runtime fallback plumbing

**Files:**
- `game_state/location_context.py`
- `game_state/session_manager.py`
- `modules/default_module.json`

**Work:**
- Remove `narrative_fallback` from normalized runtime structures and chase context.
- Delete the remaining module config blocks that only served the fallback narrative path.

## Task 3: Keep failure UX clean

**Files:**
- `webui/server.py`

**Work:**
- Return raw backend error strings so the frontend shows a single clear failure message while preserving the retry path and partial results.

## Task 4: Verify

**Files:**
- `tests/test_llm_failure_retry_only.py`

**Verification commands:**
- `D:\AstrBot\backend\python\python.exe -m unittest discover -s tests`
