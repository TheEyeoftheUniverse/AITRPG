# Context Provider Compatibility Requirements

**Date:** 2026-03-25

**Goal:** Restore TRPG turn processing on AstrBot versions where `Context.get_provider(...)` was removed, while keeping the plugin's provider selection strict and ID-based.

## Accepted Behavior

- `RuleAI`, `RhythmAI`, and `NarrativeAI` must resolve configured provider IDs on both old and new AstrBot `Context` APIs.
- The configured `rule_ai_provider`, `rhythm_ai_provider`, and `narrative_ai_provider` values must continue to be treated as exact provider IDs.
- If a configured provider ID does not exist, the AI layer must still fail fast instead of silently falling back to the current global provider.
- WebUI action processing must no longer fail with `AttributeError: 'Context' object has no attribute 'get_provider'`.

## Constraints

- Keep the existing plugin config schema unchanged.
- Keep strict provider selection behavior unchanged for missing or unset IDs.
- Limit the fix to provider lookup compatibility; do not alter prompt logic or turn orchestration.

## Non-Goals

- Redesign AstrBot provider management.
- Add automatic fallback to `get_using_provider()`.
