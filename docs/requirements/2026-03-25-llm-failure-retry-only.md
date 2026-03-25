# LLM Failure Retry-Only Requirements

**Date:** 2026-03-25

**Goal:** Remove every runtime fallback path that keeps the turn flowing after a RuleAI, RhythmAI, or NarrativeAI LLM failure. When an AI layer fails, the turn must fail and the player must rely on the existing retry button instead of seeing locally stitched fallback prose.

## Accepted Behavior

- If `RuleAI.parse_intent()` fails because the provider is unavailable, the response is invalid, or the call raises, the turn ends in an error state.
- If `RuleAI.adjudicate_action()` fails for the same reasons, the turn ends in an error state.
- If `RhythmAI.process()` fails for the same reasons, the turn ends in an error state.
- If `NarrativeAI.generate()` fails, returns invalid JSON, or omits usable narrative text, the turn ends in an error state.
- The WebUI must continue surfacing the failed step and keep the retry button workflow available.
- The server may still return partial structured results that were completed before the failure, but it must not fabricate player-facing fallback narrative text.

## Constraints

- Keep hard-coded ending text and other non-LLM deterministic game logic intact.
- Do not remove the existing retry UX.
- Remove runtime references to `narrative_fallback` so the old stitched-text mechanism is not reachable anymore.

## Non-Goals

- Redesign the retry button UX.
- Rewrite unrelated prompt content or normal successful narrative behavior.
