# AI驱动TRPG跑团系统

AITRPG 是一个运行在 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 上的 TRPG 插件，面向 COC 风格的开放探索、NPC 协作、威胁追逐和动态叙事。

当前仓库元数据版本：`v3.1.0`

## 当前状态

- 默认是三层链路：`Rule AI -> Rhythm AI -> Narrative AI`
- WebUI 可切到合并模式：`Rule AI -> Story AI`，把节奏和叙述合并成一次调用
- 每层支持独立 provider 和 fallback；插件不再内置任何默认模型配置
- 失败后支持断点重试，前序层结果会从缓存恢复，不重复跑整轮
- WebUI 提供中断存档、恢复存档、自定义 OpenAI 兼容 API、角色卡配置和地图点击移动
- 地图使用 Cytoscape 渲染，支持战争迷雾、可达性提示、微场景入口、追逐限制和节点/边视觉样式
- 模组编辑器仍然可以直接访问 `/trpg/module-editor/`，但主页入口当前暂时关闭

## 核心能力

- **规则与叙事分离**：规则层产出硬变化，节奏层产出软变化，合并后再交给叙述层
- **NPC / 威胁实体系统**：位置、记忆、信任、Reveal、同伴模式、追逐状态、堵门与接触判定
- **微场景系统**：关键入口可挂在父场景边上，不参与普通路径寻路
- **地图系统**：可达性、锁门说明、不可达原因、地图腐蚀、NPC/危险/微场景角标、定制节点图标与边样式
- **COC7 角色卡**：属性、技能、职业、背景、起始物品、随机生成、模板导出、JSON 导入、服务端硬校验
- **存档与恢复**：Web 会话持久化、显式继续存档、过期存档清理
- **自定义 API**：前端可按层填写或统一填写 OpenAI 兼容 API，保存在浏览器本地

## 安装与启动

1. 先部署 AstrBot，并确认插件目录可用。
2. 将本仓库放入 AstrBot 的插件目录。
3. 在插件配置里填写真实可用的 provider ID。
4. 启动 AstrBot 后，访问 `http://<host>:<webui_port>/trpg/`。

默认端口是 `9999`。完整配置说明见 [CONFIG.md](./CONFIG.md) 和 [_conf_schema.json](./_conf_schema.json)。

## 主要配置项

| 字段 | 说明 |
|------|------|
| `module_name` | 默认模组文件名，不含 `.json` |
| `rule_ai_provider` | 规则层 provider ID |
| `rule_ai_provider_fallbacks` | 规则层 fallback 列表 |
| `rhythm_ai_provider` | 节奏层 provider ID |
| `rhythm_ai_provider_fallbacks` | 节奏层 fallback 列表 |
| `narrative_ai_provider` | 文案层 provider ID |
| `narrative_ai_provider_fallbacks` | 文案层 fallback 列表 |
| `webui_port` | WebUI 端口 |

## 仓库结构

```text
aitrpg/
├── main.py
├── metadata.yaml
├── _conf_schema.json
├── CONFIG.md
├── ai_prompts.json
├── theatrical_parser.py
├── ai_layers/
│   ├── rule_ai.py
│   ├── rhythm_ai.py
│   ├── narrative_ai.py
│   ├── story_ai.py
│   ├── provider_failover.py
│   └── usage_metrics.py
├── data/
├── docs/
├── game_state/
│   ├── session_manager.py
│   ├── character_card.py
│   ├── location_context.py
│   ├── placeholder_resolver.py
│   └── save_store.py
├── modules/
├── tests/
├── tools/
│   └── module-editor/
└── webui/
```

## 文档

- 配置说明：[CONFIG.md](./CONFIG.md)
- 模组字段说明：[modules/README.md](./modules/README.md)
- 模组编辑器说明：[tools/module-editor/README.md](./tools/module-editor/README.md)

## 许可证

AGPL-3.0
