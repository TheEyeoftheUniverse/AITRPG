# AI驱动TRPG跑团系统

基于三层AI架构的COC风格跑团系统，支持开放探索、动态剧情生成和沉浸式恐怖演出。

## 功能特点

- **三层AI架构**：规则AI（裁定） → 节奏AI（控制） → 文案AI（叙述），职责分离
- **开放探索**：AI驱动的动态剧情，BFS可达性地图移动，战争迷雾
- **NPC系统**：记忆系统（对话追踪）、信任系统（trust_map查表）、同伴状态机（follow/wait/bait）
- **威胁实体**：管家追踪AI、门阻隔机制、闪避检定、协助检定
- **演出效果系统**：模组作者可使用自定义标签触发视觉效果（乱码闪烁、伪系统消息、幽灵打字、地图污染等）
- **两阶段结局**：硬编码文本触发 → LLM生成结局叙述
- **独立Web界面**：三栏布局（AI工作流 | 聊天 | 玩家状态+SVG地图）
- **存档系统**：支持Web会话持久化与恢复

## 快速开始

### 1. 安装AstrBot

```bash
uv tool install astrbot
astrbot init
```

### 2. 安装插件

将本插件目录复制到AstrBot的插件目录：
```
~/.astrbot/data/plugins/aitrpg/
```

### 3. 配置API

在AstrBot的WebUI中配置LLM提供商，然后在AITRPG插件配置页面设置：

- **rule_ai_provider**: 规则AI使用的LLM提供商（默认：gpt）
- **rhythm_ai_provider**: 节奏AI使用的LLM提供商（默认：deepseek）
- **narrative_ai_provider**: 文案AI使用的LLM提供商（默认：claude）
- **webui_port**: Web游戏界面端口（默认：9999）

### 4. 访问Web界面

插件启动后，浏览器访问：
```
http://<服务器IP>:9999/
```
选择模组即可开始游戏。也可通过聊天平台发送 `/trpg` 开始。

## 项目结构

```
AITRPG/
├── main.py                    # 插件主入口（命令处理 + 三层AI调度 + 结局系统）
├── metadata.yaml              # 插件元数据和配置项
├── ai_prompts.json            # AI提示词配置
├── theatrical_parser.py       # 演出效果标签解析器
├── ai_layers/                 # 三层AI
│   ├── rule_ai.py            # 规则AI（意图解析 + 判定 + 同伴指令）
│   ├── rhythm_ai.py          # 节奏AI（节奏控制 + NPC记忆 + 信任变化）
│   └── narrative_ai.py       # 文案AI（叙述生成）
├── game_state/                # 游戏状态管理
│   ├── session_manager.py    # 会话管理（移动、地图、NPC、威胁实体、结局）
│   ├── location_context.py   # 实体工具函数
│   └── save_store.py         # JSON存档
├── webui/                     # Web游戏界面
│   ├── server.py             # Quart API路由 + 演出效果集成
│   ├── templates/index.html  # 三栏游戏界面
│   └── static/               # CSS + JS（SVG地图渲染 + 演出效果执行）
└── modules/                   # 模组数据
    ├── default_module.json   # 默认模组「门缝」
    └── README.md             # 模组编辑说明
```

## 技术架构

### AI数据流

```
玩家输入（Web界面 或 聊天平台）
    ↓
代码层：移动处理（BFS可达性）、管家追踪、门阻隔
    ↓
[规则AI] 意图解析 + 可行性判断 + 检定规划 + 同伴指令识别
    ↓
[节奏AI] 阶段评估 + 氛围控制 + NPC记忆更新 + 信任变化
    ↓
[文案AI] 叙述生成（可嵌入演出效果标签）
    ↓
代码层：状态更新、演出标签解析、地图数据
    ↓
返回前端：叙述 + 演出效果 + 游戏状态 + 地图
```

### 演出效果系统

模组作者和文案AI均可在文本中使用以下标签触发前端视觉效果：

| 标签 | 效果 | 位置 |
|------|------|------|
| `<glitch>文本</glitch>` | 乱码闪烁 | 原文内联 |
| `<echo-text>阶段1\|阶段2</echo-text>` | 渐进切换 | 原文内联 |
| `<paragraph>文本</paragraph>` | 独立消息 | 新气泡 |
| `<system-echo>文本</system-echo>` | 红色伪系统消息 | 居中气泡 |
| `<inject-input>文本</inject-input>` | 幽灵打字 | 输入框 |
| `<map-corrupt>key\|名称</map-corrupt>` | 地图节点污染 | SVG地图 |

标签支持嵌套（如 `<system-echo>文本<inject-input>内容</inject-input></system-echo>`），连续 map-corrupt 会批量执行。

## 自定义模组

1. 复制 `modules/default_module.json`
2. 修改地点、物品、NPC、结局等内容
3. 将新模组文件放入 `modules/` 目录，游戏启动时会自动列出

详细说明请查看 `modules/README.md`

## 作者

TheEyeoftheUniverse

## 许可证

MIT License
