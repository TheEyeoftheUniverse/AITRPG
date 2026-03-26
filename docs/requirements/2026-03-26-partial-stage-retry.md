# Partial Stage Retry Requirements

**Date:** 2026-03-26

**Goal:** Cache the complete output of each AI stage for the current turn as soon as that stage finishes, so the WebUI can show partial workflow results immediately and retry can resume from the failed stage instead of restarting from RuleAI every time.

## Accepted Behavior

- When RuleAI finishes for the current turn, the server must immediately cache that turn's `intent`, `rule_plan`, `rule_result`, `hard_changes`, and step telemetry.
- When RhythmAI finishes for the current turn, the server must immediately cache that turn's `rhythm_result` and step telemetry.
- The WebUI must be able to fetch in-progress partial results during the same turn and update the left workflow panels before the whole turn completes.
- If RhythmAI fails after RuleAI succeeded, retry must resume from `rhythm`.
- If NarrativeAI fails after RuleAI and RhythmAI succeeded, retry must resume from `narrative`.
- If RuleAI fails, retry must resume from `rule`.
- Retry only uses the current turn cache. A new player action replaces the previous turn's temporary retry cache.
- Existing error telemetry and progress rendering must remain available.

## Constraints

- Reuse the existing current-turn retry/cache flow where practical instead of introducing a second parallel cache model.
- Do not persist these partial results as long-term history; they are only for the current turn's recovery and live workflow display.
- Keep existing deterministic non-LLM branches working, including endings and hard-coded movement checks.
- Keep the existing top-bar retry UX; only change its default retry behavior to use the failed stage automatically.

## Non-Goals

- Redesign the overall WebUI layout.
- Preserve partial cache across later player turns or across unrelated sessions.
- Add multi-turn checkpointing beyond the current action.
