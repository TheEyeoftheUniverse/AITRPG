# Partial Stage Retry Design

**Date:** 2026-03-26

## Summary

The current code already supports `retry_from=rule|rhythm|narrative`, but the WebUI mostly receives structured workflow data only after the whole turn finishes. This makes the left workflow panels lag behind the actual backend progress and causes retry to feel like a full restart even when RuleAI or RhythmAI already succeeded.

The design is to treat current-turn cache as the single source of truth for both:

- live workflow display during processing
- resume-from-failed-stage retry

## Approach

### 1. Cache stage outputs immediately

Extend the current-turn cache in `main.py` so that every successful stage writes its complete output immediately:

- Rule stage writes: `intent`, `rule_plan`, `rule_result`, `hard_changes`, optional `sancheck_result`, and cached step metrics.
- Rhythm stage writes: `rhythm_result` and cached step metrics.
- Narrative stage may write `narrative_result` when complete, but retry only needs `rule` and `rhythm` stage outputs to avoid re-running successful earlier stages.

### 2. Publish partial results through `/trpg/api/progress`

Extend the progress API to return:

- `progress`
- `partial_results`
- `retry_from_hint`

`partial_results` mirrors the current-turn cache fields that are safe for the WebUI to render while the turn is still running.

### 3. Determine retry start from the failed stage

When a stage fails, derive a default retry hint from the failed step key:

- `rule_intent`, `rule_adjudication`, `rule_check` -> `rule`
- `rhythm` -> `rhythm`
- `narrative` -> `narrative`

Store that hint in the current-turn cache so both the action-error response and later progress polling expose the same retry target.

### 4. Update left workflow panels incrementally

The frontend progress polling in `app.js` should update:

- rule panel as soon as `rule_result` exists
- rhythm panel as soon as `rhythm_result` exists

This keeps the player-facing workflow synchronized with the actual backend stage completion.

## Data Flow

1. Player sends action.
2. Backend initializes current-turn cache.
3. RuleAI finishes.
4. Backend writes rule outputs into current-turn cache.
5. `/trpg/api/progress` exposes those rule outputs.
6. Frontend polling updates the rule panel immediately.
7. RhythmAI finishes.
8. Backend writes rhythm output into current-turn cache.
9. `/trpg/api/progress` exposes rhythm output.
10. Frontend polling updates the rhythm panel immediately.
11. If a later stage fails, backend marks the failed step in telemetry and writes `retry_from_hint`.
12. Retry button sends the hinted stage automatically.

## Verification Targets

- Rule stage success should appear in the left workflow before RhythmAI or NarrativeAI completes.
- Rhythm stage success should appear in the left workflow before NarrativeAI completes.
- Rhythm failure should not trigger a second RuleAI call on retry.
- Narrative failure should not trigger second RuleAI or RhythmAI calls on retry.
- Starting a new action should replace the previous turn's temporary retry cache.
