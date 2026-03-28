# AI驱动TRPG跑团系统

基于三层 AI 架构的 TRPG 网页插件，面向 COC 风格的探索、追逐、NPC 协作、结局分支和恐怖演出。

当前版本：`v2.2.0`

## 项目概览

这个项目把一次跑团拆成了三层：

- 规则层负责动作裁定、检定、移动、同伴指令和硬状态变化
- 节奏层负责阶段评估、压力变化、NPC 互动连续性和结局请求
- 文案层负责把已经确定的状态组织成最终叙述

代码层仍然保留关键硬规则，不把所有决定交给模型。当前项目已经覆盖了地图可达性、隐藏节点显现、威胁实体追逐、门阻隔、微场景、NPC 预设任务、Web 存档恢复和分层重试。

## 核心能力

- 三层 AI 流程：规则 AI、节奏 AI、文案 AI 分工明确
- 地图系统：BFS 可达性、战争迷雾、隐藏地点、条件显现、SVG 地图
- NPC 系统：记忆、信任、Reveal、Soft State、Companion、预设任务
- 威胁实体系统：追逐、堵门、接触判定、逐格移动限制
- 微场景系统：用于硬导向警告或坏结局入口，不参与普通地图寻路
- 结局系统：地点触发、硬条件校验、AI 请求后端复核
- WebUI：AI 工作流面板、聊天区、玩家状态区、地图交互
- 存档系统：Web 会话持久化、显式继续存档入口、断层重试

## 配置项

插件本身主要依赖以下配置项：

- `module_name`
- `rule_ai_provider`
- `rule_ai_provider_fallbacks`
- `rhythm_ai_provider`
- `rhythm_ai_provider_fallbacks`
- `narrative_ai_provider`
- `narrative_ai_provider_fallbacks`
- `webui_port`

详细字段可以直接查看 [metadata.yaml](/C:/Users/26459/Desktop/AI驱动跑团项目/aitrpg/metadata.yaml) 和 [_conf_schema.json](/C:/Users/26459/Desktop/AI驱动跑团项目/aitrpg/_conf_schema.json)。

## 仓库结构

```text
aitrpg/
├── main.py
├── metadata.yaml
├── _conf_schema.json
├── ai_prompts.json
├── theatrical_parser.py
├── ai_layers/
├── game_state/
├── webui/
├── modules/
└── LICENSE
```

主要目录职责：

- [main.py](/C:/Users/26459/Desktop/AI驱动跑团项目/aitrpg/main.py)：插件入口、三层 AI 调度、Web 行动主流程
- [ai_layers](/C:/Users/26459/Desktop/AI驱动跑团项目/aitrpg/ai_layers)：规则、节奏、文案三层实现
- [game_state](/C:/Users/26459/Desktop/AI驱动跑团项目/aitrpg/game_state)：地图、状态、NPC、威胁实体、结局、存档
- [webui](/C:/Users/26459/Desktop/AI驱动跑团项目/aitrpg/webui)：Quart 接口、前端模板、样式、脚本
- [modules](/C:/Users/26459/Desktop/AI驱动跑团项目/aitrpg/modules)：模组 JSON 和模组字段文档

## 文档导航

- 模组字段说明请看 [modules/README.md](/C:/Users/26459/Desktop/AI驱动跑团项目/aitrpg/modules/README.md)

## 作者

TheEyeoftheUniverse

## 许可证

AGPL-3.0
