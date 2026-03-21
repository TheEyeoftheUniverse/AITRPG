# 模组文件说明

这个目录用于存放TRPG模组的JSON文件。

## 模组文件结构

每个模组文件都是一个JSON文件，包含以下结构：

```json
{
  "module_info": {
    "name": "模组名称",
    "theme": "主题（如：克苏鲁恐怖）",
    "target_rounds": 30,
    "description": "模组简介"
  },
  "locations": {
    "location_id": {
      "name": "地点名称",
      "description": "地点描述",
      "objects": ["物品1", "物品2"],
      "exits": ["出口1", "出口2"],
      "danger_level": 1
    }
  },
  "objects": {
    "物品名称": {
      "type": "clue/danger/item",
      "check_required": "侦查/图书馆/聆听/意志/null",
      "difficulty": "普通/困难/极难",
      "san_cost": -2,
      "success_result": "成功时的描述",
      "failure_result": "失败时的描述"
    }
  },
  "npcs": {
    "NPC名称": {
      "name": "显示名称",
      "initial_attitude": "友好/中立/敌对",
      "can_escape_together": true/false,
      "key_info": "NPC掌握的关键信息",
      "trust_threshold": 0.6,
      "description": "NPC描述"
    }
  },
  "escape_conditions": {
    "minimum_progress": 0.6,
    "required_items": ["必需物品"],
    "optional": ["可选条件"]
  },
  "endings": {
    "ending_id": {
      "conditions": {
        "progress": 0.8,
        "truth_revealed": true,
        "npc_together": true
      },
      "description": "结局描述"
    }
  }
}
```

## 如何创建新模组

1. 复制 `default_module.json` 文件
2. 重命名为你的模组名称（如 `my_module.json`）
3. 编辑JSON内容，修改地点、物品、NPC等
4. 在AstrBot的WebUI中，将配置项 `module_name` 设置为你的模组名称（不含.json后缀）

## 字段说明

### locations（地点）
- `location_id`: 地点的唯一标识符（用于代码引用）
- `name`: 显示给玩家的地点名称
- `description`: 地点的基础描述。应只写场景本身稳定存在的信息
- `npc_present_description`: 可选。仅当该地点当前确实有NPC在场时，才会附加到 `description` 后面的额外描述
- `objects`: 该地点可交互的物品列表
- `exits`: 可以前往的其他地点
- `danger_level`: 危险等级（1-5），影响AI的叙述风格

### objects（物品）
- `type`: 物品类型
  - `clue`: 线索类物品，推进剧情
  - `danger`: 危险物品，可能造成伤害
  - `item`: 普通物品，可以拾取使用
- `check_required`: 需要的技能检定（null表示无需检定）
- `difficulty`: 检定难度
- `san_cost`: SAN值损失（负数）
- `success_result`: 检定成功时的结果描述
- `failure_result`: 检定失败时的结果描述

### npcs（NPC）
- `initial_attitude`: 初始态度
- `can_escape_together`: 是否可以一起逃离
- `key_info`: NPC掌握的关键信息
- `trust_threshold`: 信任阈值（0-1），达到后可获得关键信息

### endings（结局）
- `conditions`: 触发条件（可以是进度、标志位等）
- `description`: 结局描述文本

## 注意事项

1. JSON文件必须是有效的JSON格式，注意逗号和引号
2. 所有中文字符都应该使用UTF-8编码
3. 物品名称、地点ID等标识符要保持一致
4. 建议先在JSON验证工具中检查格式是否正确
5. 修改模组后需要重启AstrBot才能生效
