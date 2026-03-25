# Context Provider Compatibility Implementation Plan

**Goal:** Replace direct reliance on `Context.get_provider(...)` with a small compatibility layer that supports both legacy and current AstrBot provider APIs.

## Task 1: Add a compatibility resolver

**Files:**
- `ai_layers/provider_resolver.py`

**Work:**
- Resolve configured provider IDs via `get_provider(...)` when present.
- Fall back to `get_provider_by_id(...)` on newer AstrBot builds.
- As a final compatibility path, scan `get_all_providers()` by provider ID.

## Task 2: Switch all AI layers to the resolver

**Files:**
- `ai_layers/rule_ai.py`
- `ai_layers/rhythm_ai.py`
- `ai_layers/narrative_ai.py`

**Work:**
- Replace duplicated direct `context.get_provider(...)` calls with the shared resolver.
- Preserve existing strict-error behavior when IDs are missing or unset.

## Task 3: Verify compatibility and non-fallback behavior

**Files:**
- `tests/test_llm_failure_retry_only.py`

**Verification commands:**
- `python -m unittest aitrpg.tests.test_llm_failure_retry_only`
