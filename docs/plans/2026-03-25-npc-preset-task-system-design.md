# NPC预设任务系统设计

日期：2026-03-25

## 设计结论

本次不新建第二套 NPC 状态机，而是在现有：

1. `trust_level`
2. `companion_mode`
3. `companion_task`
4. `advance_round`
5. `update_state`
6. `reveal_conditions`

之上增加一层“模组声明式预设任务编排器”。

推荐原因：

1. 改动面最小。
2. 现有 `follow / wait / bait`、追逐、门阻隔都能直接复用。
3. 能把复杂剧情行为固定为模组定义，避免每次都依赖 LLM 临场发挥。

## 总体结构

### 1. 模组层

在 `default_module.json` 中新增：

1. `preset_tasks`
2. `special_clues` 或直接复用现有 clue 文本区域
3. 新隐藏地点 `emily_exit`
4. 新结局 `emily_escaped`

### 2. 运行时层

在 NPC runtime 上增加：

```json
{
  "preset_task": {
    "task_id": "solo_search_escape",
    "phase": "offstage",
    "rounds_left": 8,
    "status": "running",
    "started_round": 12
  }
}
```

第一版只支持一个 NPC 同时持有一个预设任务。

### 3. 执行层

`SessionManager` 新增薄执行器：

1. `start_preset_task(session_id, npc_name, task_id)`
2. `_advance_preset_tasks(state)`
3. `deliver_pending_npc_reports(session_id, npc_name)`

推进时机放在现有：

1. `update_state()`
2. `advance_round()`

中，与现有 `companion_task` 同轮推进。

### 4. AI 层

`RuleAI` 只新增：

```json
{
  "preset_task_request": {
    "target_npc": "艾米莉",
    "task_id": "solo_search_escape",
    "reason": "玩家提出由自己吸引管家，由艾米莉独立调查"
  }
}
```

后端校验通过后启动任务。

## 模组写法建议

### 1. 预设任务

```json
{
  "preset_tasks": {
    "solo_search_escape": {
      "actor": "艾米莉",
      "requirements": {
        "min_trust": 0.5,
        "actor_location": "guest_bedroom"
      },
      "kind": "solo_search",
      "duration_rounds": 8,
      "return_to": "guest_bedroom",
      "on_complete": {
        "set_flags": {
          "emily_report_pending": true,
          "emily_found_exit": true
        }
      },
      "report": {
        "clue": "艾米莉的调查报告",
        "text": "她在地下区域发现了稳定出口，并标出路线。"
      }
    },
    "cooperative_escape": {
      "actor": "艾米莉",
      "requirements": {
        "min_trust": 0.5,
        "actor_location": "guest_bedroom"
      },
      "kind": "cooperative_escape",
      "staging_location": "second_floor_hallway",
      "success": {
        "npc_mode": "follow",
        "handoff_target": "player"
      },
      "failure": {
        "return_to": "guest_bedroom",
        "trust_set": 0.0,
        "set_flags": {
          "emily_refuses_cooperation": true
        }
      }
    }
  }
}
```

第一版不追求动作序列 DSL，优先用少数字段表达固定任务类型。

## 任务流设计

### A. 独立调查

#### 启动

1. 玩家在次卧门口或艾米莉所在场景提出分工。
2. `RuleAI` 命中 `solo_search_escape`。
3. `SessionManager.start_preset_task()` 校验信任值和位置。
4. 艾米莉 runtime 进入：
   - `preset_task.status=running`
   - `preset_task.phase=offstage`
   - `preset_task.rounds_left=8`
5. 地图与场景渲染层对持有 `offstage` 任务的 NPC 隐藏。

#### 推进

1. 每轮 `_advance_preset_tasks()` 让 `rounds_left -= 1`。
2. 到 0 时：
   - 艾米莉位置写回 `guest_bedroom`
   - 写入 `emily_report_pending=true`
   - 清空 `preset_task`

#### 交付

1. 玩家下一次和艾米莉对话。
2. 系统先检查 `emily_report_pending`。
3. 命中后把模组中的调查报告文本注入当前叙事上下文。
4. 同时发放线索 `艾米莉的调查报告`。
5. 写入：
   - `emily_report_delivered=true`
   - `emily_report_pending=false`

### B. 配合逃脱

#### 启动

1. 艾米莉先 `move_to second_floor_hallway`。
2. 管家切换为追逐艾米莉。
3. 记录任务分支状态为 `await_handoff`。

#### 玩家接手

1. 玩家进入 `second_floor_hallway`。
2. 本轮视为交接回合，不做闪避检定。
3. 管家目标切换为玩家。
4. 艾米莉切回 `follow player`。
5. 任务成功结束。

#### 玩家背叛

1. 玩家不接手，直接远离。
2. 艾米莉无法离开走廊。
3. 艾米莉退回 `guest_bedroom`。
4. 信任值清零。
5. 写入 `emily_refuses_cooperation=true`。

## 新地点设计

### 系统内部

```json
{
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

### 命名规则

1. `key` 必须唯一。
2. 内部 `name` 必须唯一。
3. 玩家可见名允许与旧出口叙事一致，因此使用 `hidden_name=纯黑地洞`。

这样可避免当前 `name -> key` 映射冲突，同时满足玩家体验。

## 文件改动边界

### 主要文件

1. `aitrpg/modules/default_module.json`
2. `aitrpg/game_state/session_manager.py`
3. `aitrpg/ai_layers/rule_ai.py`
4. `aitrpg/main.py`
5. `aitrpg/tests/test_npc_preset_tasks.py`

### 尽量不动

1. `rhythm_ai.py`
2. `narrative_ai.py`

这两层优先通过新增上下文字段或已有 clue/flag 复用来接入，不做大改。

## 错误处理

### 启动失败

以下情况直接拒绝启动：

1. 目标 NPC 不存在。
2. `task_id` 不存在。
3. 信任值不足。
4. NPC 不在要求位置。
5. NPC 已在执行其他预设任务。

### 交付失败保护

如果 `emily_report_pending=true` 但报告配置缺失：

1. 仍发放线索。
2. 使用简短兜底文本。
3. 写日志，避免玩家被卡死。

## 测试策略

### 单元测试

至少覆盖：

1. `solo_search_escape` 启动成功。
2. 艾米莉离场期间不出现在地图。
3. 第 8 回合回到次卧并挂起报告。
4. 对话后发放 `艾米莉的调查报告`。
5. `emily_exit` 在获得线索后才可见。
6. 进入 `emily_exit` 触发 `emily_escaped`。
7. `cooperative_escape` 接手成功分支。
8. `cooperative_escape` 背叛失败分支。

### 回归重点

1. 现有 `follow / wait / bait` 不回归。
2. 现有 `black_pit` 结局不受影响。
3. 地图同名地点不冲突。
