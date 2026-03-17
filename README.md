# AI驱动TRPG跑团系统

基于三层AI架构的COC跑团系统，支持开放探索和动态剧情生成。

## 功能特点

- 🎲 **三层AI架构**：规则AI + 节奏AI + 文案AI，职责分离
- 🎮 **开放探索**：AI驱动的动态剧情，而非固定分支
- 📖 **沉浸式叙述**：高质量文案生成，营造克苏鲁恐怖氛围
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

### 4. 启动游戏

在聊天中发送：
```
/trpg
```

## 使用说明

### 命令列表

- `/trpg` - 开始新游戏
- `/trpg_reset` - 重置游戏
- `/trpg_status` - 查看当前状态

### 游戏玩法

1. 发送 `/trpg` 开始游戏
2. 阅读开场白，了解当前情况
3. 输入你的行动（自然语言）
4. AI会处理你的行动并返回结果
5. 继续探索，寻找线索，逃离诡宅

### 示例对话

```
玩家: /trpg
系统: [显示开场白]

玩家: 我想搜查房间里的书架
系统: [显示AI工作流 + 叙述文本]

玩家: 我打开日记阅读
系统: [显示AI工作流 + 叙述文本]
```

## 项目结构

```
AITRPG/
├── main.py                    # 插件主入口
├── metadata.yaml              # 插件元数据
├── ai_layers/                 # 三层AI
│   ├── rule_ai.py            # 规则AI（意图解析+判定）
│   ├── rhythm_ai.py          # 节奏AI（剧情控制）
│   └── narrative_ai.py       # 文案AI（叙述生成）
├── game_state/                # 游戏状态管理
│   └── session_manager.py    # 会话管理器
├── modules/                   # 模组数据（待添加）
└── rules/                     # 规则数据（待添加）
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
- **节奏AI**：保存游戏状态（进度、NPC、场景）
- **文案AI**：保存历史总结（最近15轮）

## 开发计划

- [x] Day 1: 插件框架 + 三层AI集成
- [ ] Day 2: 模组数据 + 游戏状态优化
- [ ] Day 3: 规则完善 + 测试
- [ ] Day 4: 前端可视化 + 部署

## 作者

TheEyeoftheUniverse

## 许可证

MIT License
