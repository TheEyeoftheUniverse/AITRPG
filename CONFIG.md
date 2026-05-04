# The Call of AI 配置说明

## 重要结论

The Call of AI 不再内置任何默认模型配置。

以下三项都需要你在 AstrBot 插件配置里显式填写，而且值必须是 AstrBot 里真实存在的 provider ID：

- `rule_ai_provider`
- `rule_ai_provider_fallbacks`
- `rhythm_ai_provider`
- `rhythm_ai_provider_fallbacks`
- `narrative_ai_provider`
- `narrative_ai_provider_fallbacks`

主模型为空时会直接报错。备用列表可以为空。

## 配置项

### `rule_ai_provider`

- 用途：规则AI，负责意图解析、动作裁定、规则判定
- 要求：必填
- 填写内容：AstrBot 中聊天模型 provider 的精确 ID

### `rule_ai_provider_fallbacks`

- 用途：规则AI备用模型列表
- 要求：选填
- 填写内容：AstrBot 中聊天模型 provider 的精确 ID 列表
- 行为：主模型不可用时按顺序切换

### `rhythm_ai_provider`

- 用途：节奏AI，负责节奏评估、软引导、软变化补充
- 要求：必填
- 填写内容：AstrBot 中聊天模型 provider 的精确 ID

### `rhythm_ai_provider_fallbacks`

- 用途：节奏AI备用模型列表
- 要求：选填
- 填写内容：AstrBot 中聊天模型 provider 的精确 ID 列表
- 行为：主模型不可用时按顺序切换

### `narrative_ai_provider`

- 用途：文案AI，负责生成玩家可见叙述文本
- 要求：必填
- 填写内容：AstrBot 中聊天模型 provider 的精确 ID

### `narrative_ai_provider_fallbacks`

- 用途：文案AI备用模型列表
- 要求：选填
- 填写内容：AstrBot 中聊天模型 provider 的精确 ID 列表
- 行为：主模型不可用时按顺序切换

### `module_name`

- 用途：默认模组名
- 默认值：`default_module`
- 填写内容：`modules/` 目录里的 JSON 文件名，不含 `.json`

### `webui_port`

- 用途：WebUI 端口
- 默认值：`9999`

## 如何填写 provider ID

不要填写“模型名猜测值”，要填写 AstrBot 里 provider 的真实 ID。

例如，如果 AstrBot 里真实存在的 provider ID 是：

- `openai/gpt-5.2`
- `openai/gpt-5`
- `myproxy/deepseek-v3`

那插件配置里就必须逐项填写这些完整 ID 之一，而不是只写：

- `gpt`
- `deepseek`
- `claude`

## 生效方式

修改插件配置后，需要让 AstrBot 重新加载插件或重启服务，新的 provider 配置才会进入插件实例。

## 切换规则

只有 provider/服务不可用时才会切到备用模型，例如：

- provider ID 不存在
- 网络错误、超时
- 429
- 5xx
- 鉴权失效
- provider disabled / unavailable

如果主模型已经返回内容，但内容不是合法 JSON，这类错误不会自动切到备用模型。

## 故障排查

如果你怀疑配置没生效，先看后端日志里的初始化行。修复后，插件会打印一条类似：

```text
[The Call of AI] Effective plugin config: module=default_module, rule_ai=openai/gpt-5.2, rhythm_ai=openai/gpt-5, narrative_ai=myproxy/deepseek-v3
```

如果这里还是 `<unset>`，说明插件实例没有拿到插件配置，或配置项为空。

如果这里有值，但随后报 `Provider ... not found`，说明你填写的 provider ID 和 AstrBot 当前实际存在的 provider ID 不一致。
