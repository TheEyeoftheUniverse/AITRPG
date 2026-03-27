# 模组字段说明

这个目录存放 TRPG 模组 JSON。这里的 README 只讲字段，不包含任何示例模组的剧情说明。

模组文件在运行时按 `文件名去掉 .json 后缀` 作为 `module_name` 加载。

## 顶层结构

一个模组通常由这些顶层字段组成：

- `module_info`
- `mechanics`
- `locations`
- `objects`
- `npcs`
- `threat_entities`
- `preset_tasks`
- `micro_scenes`
- `endings`

未使用的部分可以省略，但字段类型必须正确。

## module_info

描述模组基础信息和全局提示。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | 模组显示名称 |
| `module_type` | string | 模组类型，如短模、长模、单人模组 |
| `target_rounds` | number | 目标轮次数，用于节奏参考 |
| `description` | string | 模组简介 |
| `stages` | string | 阶段说明，供节奏层参考 |
| `atmosphere_guide` | object | 不同氛围区间的文风提示 |
| `atmosphere_note` | string | 氛围字段说明 |
| `prompt_context` | string | 预留给系统或说明用途 |
| `opening` | string | 开场文本 |

## mechanics

放全局机制配置。当前最重要的是 `primary_pursuer`。

### mechanics.primary_pursuer

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `entity_name` | string | 主追逐实体名称，通常对应 `threat_entities` 里的 key |
| `warning_location` | string | 首次触发威胁压迫感的位置 key |
| `messages` | object | 若干硬规则提示文案 |

`messages` 当前常见字段：

- `not_pursuing`
- `door_blocked_success`
- `movement_restricted`
- `dodge_fail`
- `guarded_object_blocked`

## locations

`locations` 是地图节点定义，key 为地点唯一标识。

### 基础字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | 地点内部真名 |
| `description` | string | 基础描述 |
| `atmosphere` | number | 氛围值，通常是 `0.0 ~ 1.0` |
| `objects` | string[] | 当前地点可交互物品名列表 |
| `exits` | string[] | 相邻地点显示名列表 |
| `floor` | number | 楼层，用于地图布局 |

### 场景状态字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `has_door` | bool | 该地点是否被视为有门场景，会影响守门与阻隔 |
| `npc_location` | bool | 可选，标识这是一个 NPC 常驻或关键会面点 |
| `has_butler` | bool | 可选，兼容旧模组对威胁地点的标注 |
| `sancheck` | string | 进入地点时的 SAN 检定配置，格式如 `1/3` |

### 条件显示字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `hidden` | bool | 是否默认隐藏 |
| `hidden_name` | string | 地图或 UI 上的表名 |
| `show_name_when_visible` | bool | 未进入但已显现时是否直接显示名称 |
| `reveal_conditions` | object | 隐藏地点显现条件 |
| `first_entry_blocked` | object | 首次尝试进入时阻止并返回提示 |

`reveal_conditions` 当前常用子字段：

- `node_visible`
- `true_name`

这些条件通常由线索、物品或 flag 驱动。

`first_entry_blocked` 适用于任何“第一次尝试进入时先警告、第二次才真正进入”的地点。

常见子字段：

- `flag`
- `text`
- `reason_flag`
- `reason_value`
- `requires_current_location`
- `visited_locations_on_block`
- `set_flags`

### 结局字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `is_ending_location` | bool | 是否为地点触发结局 |
| `ending_id` | string | 进入该地点时触发的结局 ID |
| `is_final_location` | bool | 可选，表示最终关键地点 |

### 动态描述字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `npc_present_description` | string | 当前有可交互 NPC 在场时追加描述 |
| `threat_present_description` | string | 当前有威胁实体在场时追加描述 |
| `entity_present_description` | string | 通用兼容字段 |

### NPC 到场反应字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `npc_reactions` | object | 跟随 NPC 首次抵达该场景时的软反应 |

`npc_reactions` 的值通常按 NPC 名称分组，内部可包含：

- `follow_arrival`
- `knowledge`

## objects

`objects` 的 key 通常直接使用玩家可见物品名。

### 基础交互字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `type` | string | `clue`、`danger`、`item` 等 |
| `check_required` | string\|null | 检定技能，如 `侦查`、`图书馆`、`聆听` |
| `difficulty` | string | `无需判定`、`普通`、`困难`、`极难` |
| `success_result` | string | 成功结果 |
| `failure_result` | string | 失败结果 |
| `sancheck` | string | 该物品触发的 SAN 检定配置 |

### 规则字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `can_take` | bool | 是否允许拾取 |
| `requires` | string[] | 交互或显现所需条件 |
| `leads_to` | string | 该物品是否通向某地点 key |
| `location` | string | 该物品所属地点 key |

### NPC 到场反应

和 `locations` 一样，`objects` 也可配置 `npc_reactions`。

## npcs

`npcs` 用于可互动、可记忆、可建立关系的实体。

### 基础字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | 显示名称 |
| `location` | string | 初始地点 key |
| `description` | string | 基础描述 |
| `appearance` | string | 外观描述 |
| `personality` | string | 性格摘要 |
| `background` | string | 背景摘要 |
| `current_state` | string | 当前状态摘要 |
| `first_appearance` | string | 初见文本或初见提示 |
| `initial_attitude` | string | 初始态度 |

### 推荐模块字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `position` | object | 位置信息，当前常用 `initial_location` |
| `dialogue` | object\|null | 对话系统配置；为 `null` 时表示不能正常说话 |
| `trust` | object\|null | 信任系统配置 |
| `memory` | object\|null | 记忆系统配置 |
| `soft_state` | object\|null | 互动基调配置 |
| `companion` | object\|null | 同伴系统配置 |

### dialogue

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `guide` | object | 对话提示和边界 |
| `first_appearance` | string | 初见补充文本 |

### trust

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `initial` | number | 初始信任值 |
| `map` | object | 信任变化原因映射 |
| `threshold` | number | 关键阈值 |
| `gates` | object | 分层门槛定义 |

### memory

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `long_term` | object | 长期记忆 |
| `runtime_defaults` | object | 运行期记忆默认值 |

### soft_state

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `initial_tag` | string | 初始互动标签 |
| `initial_summary` | string | 初始互动摘要 |

### companion

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `enabled_modes` | string[] | 通常是 `follow`、`wait`、`bait` |
| `default_mode` | string | 默认模式 |
| `require_explicit_exit` | bool | 是否必须显式退出模式 |
| `unlock_trust` | number | 开放同伴行为所需信任值 |

## threat_entities

`threat_entities` 用于不可按普通 NPC 处理的危险实体。

常见字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | 显示名称 |
| `location` | string | 初始地点 |
| `appearance` | string | 外观描述 |
| `appearance_warning` | string | 初见警告 |
| `behavior` | object | 行为配置 |
| `current_state` | string | 当前状态 |
| `memory` | null | 一般威胁实体不使用普通记忆系统 |
| `soft_state` | null | 通常不走普通软状态 |
| `companion` | object\|null | 如果需要特殊移动控制可配置 |
| `is_primary_pursuer` | bool | 是否作为主追逐实体 |

## preset_tasks

`preset_tasks` 用于后端硬执行的 NPC 预设任务。AI 只请求 `task_id`，不直接编排步骤。

### 基础字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `actor` | string | 执行任务的 NPC 名称 |
| `kind` | string | 当前支持如 `solo_search_escape`、`cooperative_escape` |
| `requirements` | object | 触发前提 |

### 独立调查类字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `duration_rounds` | number | 离场持续轮数 |
| `return_to` | string | 完成后回归地点 |
| `on_complete` | object | 完成时写入的变化 |
| `report` | object | 回房后交付给玩家的报告配置 |

### 配合逃脱类字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `staging_location` | string | 初始站位地点 |
| `success` | object | 玩家接手成功时的变化 |
| `failure` | object | 玩家背弃或失败时的变化 |

### requirements 常见子字段

- `min_trust`
- `required_player_locations`
- `required_npc_locations`

### report 常见子字段

- `pending_flag`
- `delivered_flag`
- `clue`
- `text`

## micro_scenes

`micro_scenes` 是当前房间下的特殊入口，不参与普通地图寻路，但会显示在当前房间地图节点旁。

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `parent_location` | string | 所属父场景 key |
| `display_name` | string | 玩家在地图上看到的名称 |
| `visible_when` | object | 显现条件 |
| `first_entry_blocked` | object | 首次进入阻止并提示 |
| `first_enter_warning_flag` | string | 旧字段，兼容用 |
| `first_enter_text` | string | 旧字段，兼容用 |
| `ending_on_enter` | string | 一进入就触发的结局 |
| `ending_on_reenter` | string | 第二次进入触发的结局 |
| `description` | string | 无结局时的文本兜底 |

### visible_when 常见子字段

- `guard_room_is_parent`
- `requires_inventory`
- `requires_flags`

### first_entry_blocked 子字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `flag` | string | 首次阻止写入的标记 |
| `text` | string | 返回给玩家的提示文本 |
| `reason_flag` | string | 可选，附加原因标记 |
| `reason_value` | string | 可选，附加原因值 |
| `requires_current_location` | string[] | 只在特定当前位置阻止 |
| `visited_locations_on_block` | string[] | 被阻止时顺手点亮的地点 |
| `set_flags` | object | 被阻止时额外写入的 flags |

## endings

`endings` 由两部分组成：

- `ending_conditions`
- `influence_dimensions`

### ending_conditions.<ending_id>

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `description` | string | 结局描述 |
| `hardcoded_text` | string | 结局硬编码文本 |
| `hardcoded_text_<subtype>` | string | 细分子条件结局文本 |
| `validation` | object | AI 请求结局时的后端校验 |

### validation 常见子字段

- `required_flags`
- `required_any_flags`
- `required_current_locations`
- `required_player_locations`
- `required_npc_locations`

### influence_dimensions

用于结局维度统计和描述。常见结构：

- `dimensions`
- `descriptions`

## 编写建议

- 模组 key 尽量稳定，不要依赖显示名做内部标识
- 剧情条件尽量落到 `flags`、`clues`、`inventory` 这类持久状态
- 微场景适合做硬导向警告、坏结局和一次性入口
- 预设任务只让 AI 选 `task_id`，具体行为放在模组和后端里
- 隐藏地点与表里名拆分时，优先使用 `hidden_name` + `reveal_conditions`

## 注意事项

1. 模组文件必须是合法 JSON。
2. 文件编码使用 UTF-8。
3. 场景 `exits` 填的是显示名，不是地点 key。
4. 代码判断依赖大量 key 和 flag，重命名时要同步所有引用。
5. 新字段上线后，应同步补 README 和测试。
