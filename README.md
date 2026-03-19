# AI驱动TRPG跑团系统

基于三层AI架构的COC跑团系统，支持开放探索和动态剧情生成。

## 功能特点

- 🎲 **三层AI架构**：规则AI + 节奏AI + 文案AI，职责分离
- 🎮 **开放探索**：AI驱动的动态剧情，而非固定分支
- 📖 **沉浸式叙述**：高质量文案生成，风格由模组氛围指南动态驱动
- 📦 **模组化设计**：完整的模组JSON格式，支持多模组选择
- 🤖 **AI工作流可视化**：实时展示三层AI的决策过程

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

在AstrBot的WebUI中配置以下LLM提供商：
- GPT-5（规则AI）
- DeepSeek v3（节奏AI）
- Claude 4.6 Sonnet（文案AI）

### 4. 配置插件

在AstrBot的WebUI中，找到AITRPG插件的配置页面，可以设置：

- **rule_ai_provider**: 规则AI使用的LLM提供商（默认：gpt）
- **rhythm_ai_provider**: 节奏AI使用的LLM提供商（默认：deepseek）
- **narrative_ai_provider**: 文案AI使用的LLM提供商（默认：claude）
- **module_name**: 使用的模组名称（默认：default_module）

### 5. 启动游戏

在聊天中发送：
```
/trpg          # 列出可用模组
/trpg 1        # 选择第1个模组并开始游戏
```

## 使用说明

### 命令列表

- `/trpg` - 列出可用模组
- `/trpg [序号]` - 选择模组并开始游戏
- `/trpg_reset` - 重置游戏
- `/trpg_status` - 查看当前状态

### 游戏玩法

1. 发送 `/trpg` 查看可用模组列表
2. 发送 `/trpg 1` 选择模组并开始游戏
3. 阅读开场白，了解当前情况
4. 输入你的行动（自然语言）
5. AI会处理你的行动并返回结果
6. 继续探索，寻找线索，完成模组目标

### 示例对话

```
玩家: /trpg
系统: [显示模组列表]

玩家: /trpg 1
系统: [显示模组开场白]

玩家: 观察一下周围的环境
系统: [显示AI工作流 + 叙述文本]

玩家: 走到走廊去看看
系统: [显示AI工作流 + 叙述文本]
```

## 项目结构

```
AITRPG/
├── main.py                    # 插件主入口（命令处理 + 三层AI调度）
├── metadata.yaml              # 插件元数据和配置项
├── ai_prompts.json            # AI提示词配置
├── ai_layers/                 # 三层AI
│   ├── rule_ai.py            # 规则AI（意图解析+判定）
│   ├── rhythm_ai.py          # 节奏AI（剧情控制+模组对比）
│   └── narrative_ai.py       # 文案AI（叙述生成）
├── game_state/                # 游戏状态管理
│   └── session_manager.py    # 会话管理器（多模组支持）
└── modules/                   # 模组数据
    ├── default_module.json   # 默认模组「门缝」
    └── README.md             # 模组编辑说明
```

## 技术架构

### 数据流

```
玩家输入
    ↓
[规则AI-1] 意图解析
    ↓
[节奏AI] 对比模组 + 控制节奏
    ↓
[规则AI-2] 执行判定
    ↓
[文案AI] 生成叙述
    ↓
返回玩家
```

### 上下文管理

- **规则AI**：无状态，每次独立调用
- **节奏AI**：接收历史行动摘要 + 当前游戏状态（位置、线索、轮次），动态跟踪玩家/NPC位置
- **文案AI**：通过AstrBot原生对话历史管理上下文，近10轮保留完整文案，更早轮次自动压缩为摘要

### 模组数据流

节奏AI以完整JSON字段传递模组数据（场景、物品、氛围指南），不做任何摘要或提取。文案AI根据氛围指南中的atmosphere值区间动态调整文风。

## 开发计划

- [x] Day 1: 插件框架 + 三层AI集成
- [x] Day 1.5: 模组JSON化 + 模型配置
- [x] Day 2: AI数据流重构（完整JSON字段传递）+ 模组选择流程
- [x] Day 3: 上下文管理优化 + 位置追踪 + 历史摘要传递
- [ ] Day 4: 前端可视化 + 部署

## 自定义模组

你可以创建自己的模组文件：

1. 复制 `modules/default_module.json`
2. 修改地点、物品、NPC等内容
3. 将新模组文件放入 `modules/` 目录，游戏启动时会自动列出

详细说明请查看 `modules/README.md`

## 作者

TheEyeoftheUniverse

## 许可证

MIT License
