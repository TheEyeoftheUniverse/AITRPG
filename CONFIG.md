# AITRPG 配置说明

## 插件配置项

在AstrBot的WebUI中，可以为AITRPG插件配置以下选项：

### 1. AI模型配置

#### rule_ai_provider
- **说明**: 规则AI使用的LLM提供商
- **用途**: 负责解析玩家意图和执行规则判定
- **默认值**: `gpt`
- **推荐模型**: GPT-5（需要精确的意图理解和规则执行）

#### rhythm_ai_provider
- **说明**: 节奏AI使用的LLM提供商
- **用途**: 负责对比模组内容和控制剧情节奏
- **默认值**: `deepseek`
- **推荐模型**: DeepSeek v3（需要长上下文和剧情理解）

#### narrative_ai_provider
- **说明**: 文案AI使用的LLM提供商
- **用途**: 负责生成沉浸式的叙述文本
- **默认值**: `claude`
- **推荐模型**: Claude 4.6 Sonnet（擅长创意写作和氛围营造）

### 2. 模组配置

#### module_name
- **说明**: 使用的模组名称
- **默认值**: `default_module`
- **格式**: 不含.json后缀的文件名
- **示例**: 如果你的模组文件是 `my_adventure.json`，则填写 `my_adventure`

## 配置步骤

1. 打开AstrBot的WebUI（通常是 http://localhost:6185）
2. 进入"插件管理"页面
3. 找到"AI驱动TRPG跑团系统"插件
4. 点击"配置"按钮
5. 修改配置项
6. 保存并重启AstrBot

## LLM提供商配置

在使用插件之前，需要先在AstrBot中配置LLM提供商：

1. 进入AstrBot的"LLM配置"页面
2. 添加你需要的提供商（如GPT、DeepSeek、Claude）
3. 填写API密钥和其他必要信息
4. 测试连接是否正常
5. 在插件配置中引用这些提供商的名称

## 提供商名称对应关系

AstrBot中配置的提供商名称需要与插件配置中的名称对应：

- 如果你在AstrBot中配置的GPT提供商名称是"gpt"，则 `rule_ai_provider` 填写 "gpt"
- 如果你在AstrBot中配置的DeepSeek提供商名称是"deepseek"，则 `rhythm_ai_provider` 填写 "deepseek"
- 如果你在AstrBot中配置的Claude提供商名称是"claude"，则 `narrative_ai_provider` 填写 "claude"

## 注意事项

1. 提供商名称必须与AstrBot中配置的名称完全一致（区分大小写）
2. 如果找不到指定的提供商，插件会尝试使用默认提供商
3. 修改配置后需要重启AstrBot才能生效
4. 确保所有配置的提供商都已正确配置API密钥
5. 不同的模型会影响游戏体验，建议使用推荐的模型组合

## 成本估算

使用推荐的模型组合（GPT-5 + DeepSeek v3 + Claude 4.6），一局完整游戏（30轮）的成本约为：

- 规则AI（GPT-5）: ~$0.02
- 节奏AI（DeepSeek v3）: ~$0.01
- 文案AI（Claude 4.6）: ~$0.03
- **总计**: ~$0.06

如果使用低成本渠道，成本可能更低。
