# Follow Arrival Reactions Design

**Date:** 2026-03-25

**Goal:** 为跟随中的 NPC 提供“首次到达某地点时”的软反应入口，并允许模组为地点和场景物品声明该 NPC 的评价与已知判断，供节奏层和文案层参考。

## Scope

- 仅处理 `follow` 状态下的随行 NPC。
- 仅处理“纯移动到场”场景。
- 仅在 `NPC + 地点` 首次命中时触发一次额外的节奏/文案演出。
- 不改规则层判定逻辑，不把这些字段视为硬事实或硬条件。

## Module Shape

在 `locations.<location_key>` 与 `objects.<object_key>` 下新增软字段：

```json
{
  "npc_reactions": {
    "艾米莉": {
      "follow_arrival": "首次跟随到达该地点时的总体反应",
      "knowledge": "她对该地点能做出的判断"
    }
  }
}
```

```json
{
  "npc_reactions": {
    "艾米莉": {
      "recognition": "她如何识别这个物品",
      "comment": "她对该物品的评价/关注点",
      "knowledge": "她知道的与该物品相关的信息"
    }
  }
}
```

这些字段属于软编码：

- 只给 RhythmAI / NarrativeAI 参考。
- 不进入 `requires`、`inventory`、`clues_found` 等硬状态。
- 允许缺省；缺省时不触发该 NPC 的到场软反应。

## Trigger Rule

后端增加一条通用钩子，而不是写死艾米莉或具体场景：

- 玩家执行的是纯 `move`
- 至少一个 NPC 当前 `companion_mode == "follow"`
- 该 NPC 当前与玩家一同到达目标地点
- 目标地点或其场景物品中，存在该 NPC 的 `npc_reactions`
- 该 `NPC + 地点` 组合此前未触发过

满足后：

- 本轮继续走现有到场链路
- 强制触发一次 RhythmAI
- 继续触发 NarrativeAI
- 本轮结果中附带此次 `follow_arrival_reaction_context`
- 同时记录该 `NPC + 地点` 已消费

## Runtime State

在 session state 中新增软记录：

```json
{
  "world_state": {
    "follow_arrival_seen": {
      "艾米莉": ["study", "kitchen"]
    }
  }
}
```

语义：

- 按 `NPC + 地点` 去重
- 同地点换另一个随行 NPC，仍可首次触发
- 同一 NPC 重返同地点，不再触发

## Context Injection

RhythmAI / NarrativeAI 不直接扫全模组，而是读取整理后的运行时上下文：

- `location_context.follow_arrival_reactions`
- `location_context.follow_arrival_objects`
- `rhythm_result.follow_arrival_reaction_context`

其中：

- `follow_arrival_reactions` 聚合当前地点下、针对随行 NPC 的地点反应
- `follow_arrival_objects` 聚合当前地点物品里、针对随行 NPC 的物品反应
- `follow_arrival_reaction_context` 只保留本轮真正首次命中的 `NPC + 地点` 数据

## Non-Goals

- 不做完整协作任务执行器
- 不做逐物品自动逐条发言
- 不让规则层基于这些字段新增硬变化
- 不让每次移动都调用额外 LLM

