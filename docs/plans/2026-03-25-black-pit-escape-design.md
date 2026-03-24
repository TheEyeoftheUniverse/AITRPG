# Black Pit Escape Design

**Goal:** Remove AI judgment from the escape ending path by turning escape into a fully hard-coded sequence: destroy the ritual, reveal a new map node, move into that node, trigger the escape ending.

**Validated Design:**
- `escaped` is no longer AI-driven.
- Destroying the ritual does not immediately end the game.
- Destroying the ritual reveals a new location connected to the ritual chamber: `纯黑地洞`.
- The player must move into `纯黑地洞` to trigger the escape ending.
- `npc_together` remains a narrative dimension only. It affects ending flavor, not whether the ending can trigger.

**Runtime Consequences:**
- The system must hard-derive ritual destruction state from successful destruction of ritual objects instead of relying on LLM-authored flags alone.
- Hidden location reveal checks must support runtime flags, not only clues/inventory.
- The map should be able to show `纯黑地洞` by name as soon as it becomes visible.

**Module Consequences:**
- Add a hidden location `black_pit` with reveal condition `ritual_destroyed`.
- Connect `ritual_chamber -> black_pit`.
- Mark `black_pit` as `is_ending_location=true` with `ending_id=escaped`.
- Change `escaped.validation.required_current_locations` to `black_pit`.
- Remove `require_ai_request` from `escaped`.

**Non-Goals:**
- No new companion task executor.
- No new AI-side ending heuristics.
- No extra soft hint layer telling the model when to end.
