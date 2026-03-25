# LLM Failure Retry-Only Design

**Date:** 2026-03-25

## Decision

Treat LLM failure as a hard stop, not as an invitation to degrade into local prose assembly.

## Scope

- `RuleAI`, `RhythmAI`, and `NarrativeAI` raise explicit errors instead of returning fallback data after provider failures, invalid JSON, or unusable outputs.
- `NarrativeAI` no longer performs local stitched narrative generation or hidden internal retry passes.
- Runtime normalization and chase context stop carrying `narrative_fallback` payloads.
- The default module removes the remaining `narrative_fallback` configuration blocks so the mechanism is deleted rather than merely disabled.

## Rationale

- The old behavior let a failed AI turn masquerade as a successful one, which broke player trust and degraded the TRPG experience.
- Surfacing failure preserves state integrity and makes the retry button the only recovery path the player needs to understand.
- Removing the fallback data path reduces the risk of future code accidentally reactivating the same mechanism.

## Validation

- Unit-test each AI layer to confirm invalid LLM output now raises.
- Run the existing regression tests to ensure unrelated runtime logic still works.
