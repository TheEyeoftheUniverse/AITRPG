# NPC Preset Task System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a lightweight module-driven NPC preset task layer that supports Emily's solo investigation route, report delivery, new hidden exit, and cooperative escape branching with minimal changes to the current companion system.

**Architecture:** Reuse the existing NPC runtime state, companion task flow, round advancement, reveal conditions, and ending location handling. Add a thin `preset_task` runtime object plus a small executor in `SessionManager`, let `RuleAI` request tasks by `task_id`, and keep the module as the source of truth for report text, exit visibility, and ending data.

**Tech Stack:** Python, unittest/pytest-compatible tests, JSON module config, existing `SessionManager` / `RuleAI` / plugin orchestration

---

### Task 1: Lock The Module Schema And Exit Data

**Files:**
- Modify: `aitrpg/modules/default_module.json`
- Test: `aitrpg/tests/test_npc_preset_tasks.py`

**Step 1: Write the failing test**

```python
def test_emily_exit_is_hidden_until_report_clue_is_obtained():
    manager = SessionManager("default_module")
    session_id = "preset-task-exit-visibility"
    manager.create_session(session_id, "default_module")

    assert manager._is_location_visible(session_id, "emily_exit") is False

    state = manager.get_session(session_id)
    state["world_state"]["clues_found"].append("艾米莉的调查报告")

    assert manager._is_location_visible(session_id, "emily_exit") is True
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_npc_preset_tasks.py::test_emily_exit_is_hidden_until_report_clue_is_obtained -v`

Expected: FAIL because `emily_exit` does not exist in the module yet.

**Step 3: Write minimal implementation**

Add to `default_module.json`:

```json
"preset_tasks": {
  "solo_search_escape": {
    "actor": "艾米莉",
    "kind": "solo_search",
    "duration_rounds": 8,
    "return_to": "guest_bedroom",
    "requirements": {
      "min_trust": 0.5,
      "actor_location": "guest_bedroom"
    },
    "on_complete": {
      "set_flags": {
        "emily_report_pending": true,
        "emily_found_exit": true
      }
    },
    "report": {
      "clue": "艾米莉的调查报告",
      "text": "调查报告正文……"
    }
  }
},
"locations": {
  "emily_exit": {
    "name": "艾米莉标出的出口",
    "hidden_name": "纯黑地洞",
    "hidden": true,
    "show_name_when_visible": true,
    "reveal_conditions": {
      "node_visible": ["艾米莉的调查报告"]
    },
    "is_ending_location": true,
    "ending_id": "emily_escaped"
  }
}
```

Also add `emily_escaped` to ending definitions.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_npc_preset_tasks.py::test_emily_exit_is_hidden_until_report_clue_is_obtained -v`

Expected: PASS

**Step 5: Commit**

```bash
git add modules/default_module.json tests/test_npc_preset_tasks.py
git commit -m "feat: add emily preset task module data"
```

### Task 2: Add Preset Task Runtime State And Solo Task Advancement

**Files:**
- Modify: `aitrpg/game_state/session_manager.py`
- Test: `aitrpg/tests/test_npc_preset_tasks.py`

**Step 1: Write the failing test**

```python
def test_solo_search_task_returns_emily_after_eight_rounds():
    manager = SessionManager("default_module")
    session_id = "preset-task-solo-rounds"
    manager.create_session(session_id, "default_module")
    state = manager.get_session(session_id)
    emily = state["world_state"]["npcs"]["艾米莉"]
    emily["trust_level"] = 0.8

    result = manager.start_preset_task(session_id, "艾米莉", "solo_search_escape")
    assert result["success"] is True
    assert emily["preset_task"]["rounds_left"] == 8

    for _ in range(8):
        manager.update_state(session_id, {"world_changes": {}})

    state = manager.get_session(session_id)
    emily = state["world_state"]["npcs"]["艾米莉"]
    assert emily["location"] == "guest_bedroom"
    assert emily["preset_task"] == {}
    assert state["world_state"]["flags"]["emily_report_pending"] is True
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_npc_preset_tasks.py::test_solo_search_task_returns_emily_after_eight_rounds -v`

Expected: FAIL because `start_preset_task()` and preset task advancement do not exist.

**Step 3: Write minimal implementation**

In `session_manager.py`, add:

```python
def start_preset_task(self, session_id: str, npc_name: str, task_id: str) -> dict:
    # load module preset_tasks, validate trust/location, write npc_state["preset_task"]

def _advance_preset_tasks(self, state: dict) -> dict:
    # decrement rounds_left, hide actor while offstage, return actor to guest_bedroom on completion,
    # set emily_report_pending flag, clear preset_task
```

Hook `_advance_preset_tasks(state)` into both:

```python
update_state()
advance_round()
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_npc_preset_tasks.py::test_solo_search_task_returns_emily_after_eight_rounds -v`

Expected: PASS

**Step 5: Commit**

```bash
git add game_state/session_manager.py tests/test_npc_preset_tasks.py
git commit -m "feat: add solo preset task runtime advancement"
```

### Task 3: Deliver Emily's Report Through Dialogue And Unlock The Exit

**Files:**
- Modify: `aitrpg/game_state/session_manager.py`
- Modify: `aitrpg/main.py`
- Test: `aitrpg/tests/test_npc_preset_tasks.py`

**Step 1: Write the failing test**

```python
def test_pending_report_is_delivered_on_next_dialogue_only():
    manager = SessionManager("default_module")
    session_id = "preset-task-report-delivery"
    manager.create_session(session_id, "default_module")
    state = manager.get_session(session_id)
    state["world_state"]["flags"]["emily_report_pending"] = True

    report = manager.deliver_pending_npc_reports(session_id, "艾米莉")
    assert report["delivered"] is True
    assert report["clue"] == "艾米莉的调查报告"

    state = manager.get_session(session_id)
    assert "艾米莉的调查报告" in state["world_state"]["clues_found"]
    assert state["world_state"]["flags"]["emily_report_pending"] is False
    assert state["world_state"]["flags"]["emily_report_delivered"] is True
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_npc_preset_tasks.py::test_pending_report_is_delivered_on_next_dialogue_only -v`

Expected: FAIL because there is no pending-report delivery API and no main flow integration.

**Step 3: Write minimal implementation**

In `session_manager.py`, add:

```python
def deliver_pending_npc_reports(self, session_id: str, npc_name: str) -> dict:
    # if emily_report_pending, fetch report text/clue from module preset task config,
    # append clue to clues_found, flip flags, return payload for narrative injection
```

In `main.py`, before generating the NPC reply for Emily dialogue, call:

```python
pending_report = self.session_manager.deliver_pending_npc_reports(session_id, "艾米莉")
```

If delivered, merge the returned report text into the narrative context so Emily can say the configured report content in this turn.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_npc_preset_tasks.py::test_pending_report_is_delivered_on_next_dialogue_only -v`

Expected: PASS

**Step 5: Commit**

```bash
git add game_state/session_manager.py main.py tests/test_npc_preset_tasks.py
git commit -m "feat: deliver emily report clue through dialogue"
```

### Task 4: Let RuleAI Request Preset Tasks By Task ID

**Files:**
- Modify: `aitrpg/ai_layers/rule_ai.py`
- Modify: `aitrpg/main.py`
- Test: `aitrpg/tests/test_npc_preset_tasks.py`

**Step 1: Write the failing test**

```python
def test_rule_ai_extracts_solo_search_preset_task_request():
    rule_ai = RuleAI(_DummyContext(), config={})
    manager = SessionManager("default_module")
    session_id = "preset-task-rule-ai"
    manager.create_session(session_id, "default_module")
    state = manager.get_session(session_id)
    state["current_location"] = "guest_bedroom"
    state["world_state"]["npcs"]["艾米莉"]["trust_level"] = 0.8

    normalized = rule_ai._extract_preset_task_request(
        player_input="你吸引管家，我去查全屋",
        scene_npcs=rule_ai._get_scene_npcs(state, manager.get_module_data(session_id), "你吸引管家，我去查全屋"),
        module_data=manager.get_module_data(session_id),
    )
    assert normalized["target_npc"] == "艾米莉"
    assert normalized["task_id"] == "solo_search_escape"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_npc_preset_tasks.py::test_rule_ai_extracts_solo_search_preset_task_request -v`

Expected: FAIL because preset-task request parsing does not exist.

**Step 3: Write minimal implementation**

Add to `rule_ai.py`:

```python
def _extract_preset_task_request(self, player_input: str, scene_npcs: dict, module_data: dict) -> dict:
    # detect keywords for solo_search_escape and cooperative_escape
    # only return target_npc + task_id
```

Expose it in the returned rule plan:

```python
rule_plan["preset_task_request"] = request
```

In `main.py`, after companion command handling and before RhythmAI, add:

```python
preset_request = (rule_plan or {}).get("preset_task_request", {})
if preset_request.get("task_id"):
    self.session_manager.start_preset_task(session_id, preset_request["target_npc"], preset_request["task_id"])
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_npc_preset_tasks.py::test_rule_ai_extracts_solo_search_preset_task_request -v`

Expected: PASS

**Step 5: Commit**

```bash
git add ai_layers/rule_ai.py main.py tests/test_npc_preset_tasks.py
git commit -m "feat: request preset tasks from rule ai"
```

### Task 5: Add Cooperative Escape Branching

**Files:**
- Modify: `aitrpg/game_state/session_manager.py`
- Modify: `aitrpg/modules/default_module.json`
- Test: `aitrpg/tests/test_npc_preset_tasks.py`

**Step 1: Write the failing test**

```python
def test_cooperative_escape_failure_resets_trust_and_returns_emily():
    manager = SessionManager("default_module")
    session_id = "preset-task-coop-failure"
    manager.create_session(session_id, "default_module")
    state = manager.get_session(session_id)
    emily = state["world_state"]["npcs"]["艾米莉"]
    emily["trust_level"] = 0.8

    result = manager.start_preset_task(session_id, "艾米莉", "cooperative_escape")
    assert result["success"] is True

    manager.resolve_preset_task_branch(session_id, "艾米莉", branch="player_abandoned")

    state = manager.get_session(session_id)
    emily = state["world_state"]["npcs"]["艾米莉"]
    assert emily["location"] == "guest_bedroom"
    assert emily["trust_level"] == 0.0
    assert state["world_state"]["flags"]["emily_refuses_cooperation"] is True
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_npc_preset_tasks.py::test_cooperative_escape_failure_resets_trust_and_returns_emily -v`

Expected: FAIL because the cooperative branch resolution API does not exist.

**Step 3: Write minimal implementation**

In `session_manager.py`, add:

```python
def resolve_preset_task_branch(self, session_id: str, npc_name: str, branch: str) -> dict:
    # handle cooperative_escape branches:
    # player_handoff_success -> set butler target player, emily follow player
    # player_abandoned -> emily back to guest_bedroom, trust 0, refusal flag
```

Wire this to existing movement/arrival logic only where needed. Do not rewrite chase logic; only switch targets and reuse current grace-round behavior.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_npc_preset_tasks.py::test_cooperative_escape_failure_resets_trust_and_returns_emily -v`

Expected: PASS

**Step 5: Commit**

```bash
git add game_state/session_manager.py modules/default_module.json tests/test_npc_preset_tasks.py
git commit -m "feat: add cooperative preset task branching"
```

### Task 6: Run Focused Regression Suite

**Files:**
- Test: `aitrpg/tests/test_npc_preset_tasks.py`
- Test: `aitrpg/tests/test_butler_door_guard.py`
- Test: `aitrpg/tests/test_cross_wall_dialogue_system.py`

**Step 1: Write the regression checklist**

```text
1. New solo preset task tests pass
2. Butler door guard tests still pass
3. Cross-wall dialogue tests still pass
4. black_pit visibility/ending path is unchanged
```

**Step 2: Run focused tests**

Run: `pytest tests/test_npc_preset_tasks.py tests/test_butler_door_guard.py tests/test_cross_wall_dialogue_system.py -v`

Expected: PASS

**Step 3: Fix any regression with minimal code**

If a regression appears, patch the smallest affected branch only. Do not refactor unrelated movement, reveal, or ending code.

**Step 4: Re-run the focused suite**

Run: `pytest tests/test_npc_preset_tasks.py tests/test_butler_door_guard.py tests/test_cross_wall_dialogue_system.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_npc_preset_tasks.py tests/test_butler_door_guard.py tests/test_cross_wall_dialogue_system.py game_state/session_manager.py ai_layers/rule_ai.py main.py modules/default_module.json
git commit -m "feat: add emily preset task escape routes"
```
