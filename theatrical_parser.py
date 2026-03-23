"""
演出效果标签解析器

解析模组文本字段中的自定义演出标签，返回干净文本和结构化效果列表。

支持的标签:
  <paragraph>文本</paragraph>          额外独立消息
  <system-echo>文本</system-echo>      伪系统消息
  <inject-input>文本</inject-input>    输入框注入
  <glitch>文本</glitch>                乱码闪烁 (原文内联)
  <echo-text>阶段1|阶段2</echo-text>  渐进展示 (原文内联)
  <map-corrupt>key|显示名</map-corrupt>  地图节点污染
"""

import re


# ── 外部效果标签 (内容从clean_text中移除) ──
_PARAGRAPH_RE = re.compile(r"<paragraph(?:\s*=\s*([^>]+))?>(.*?)</paragraph>", re.DOTALL | re.IGNORECASE)
_EXTERNAL_TAGS = [
    ("system_echo",  re.compile(r"<system-echo>(.*?)</system-echo>",   re.DOTALL)),
    ("inject_input", re.compile(r"<inject-input>(.*?)</inject-input>", re.DOTALL)),
]

# ── 内联效果标签 (内容保留在clean_text中，用标记包裹) ──
_GLITCH_RE = re.compile(r"<glitch>(.*?)</glitch>", re.DOTALL)
_ECHO_TEXT_RE = re.compile(r"<echo-text>(.*?)</echo-text>", re.DOTALL)

# ── map-corrupt: 管道分隔 <map-corrupt>key|显示名</map-corrupt> ──
_MAP_CORRUPT_RE = re.compile(r"<map-corrupt>(.*?)</map-corrupt>", re.DOTALL)

# 多余空行压缩
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def _parse_delay_ms(raw_value: str | None) -> int | None:
    """Parse delay values like '3000ms'. Invalid values fall back to defaults."""
    if not raw_value:
        return None
    match = re.fullmatch(r"(\d+)\s*ms", raw_value.strip(), re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def parse_theatrical_tags(text: str) -> dict:
    """解析文本中的演出标签。

    内联效果 (glitch, echo_text) 的内容保留在 clean_text 中，
    用 %%GLITCH:n%%...%%/GLITCH%% 或 %%ECHO:n%%...%%/ECHO%% 标记包裹，
    前端负责将标记转换为可动画的 span。

    Returns:
        {
            "clean_text": str,
            "effects": [
                {"type": "paragraph",    "content": "..."},
                {"type": "paragraph",    "content": "...", "delay_ms": 3000},
                {"type": "system_echo",  "content": "..."},
                {"type": "inject_input", "content": "..."},
                {"type": "glitch",       "content": "...", "inline_id": 0},
                {"type": "echo_text",    "phases": ["...", "..."], "inline_id": 1},
                {"type": "map_corrupt",  "target": "key", "content": "..."},
            ]
        }
    """
    if not text:
        return {"clean_text": "", "effects": []}

    # 收集所有匹配: (start, end, effect_dict, replacement)
    # replacement: None → 从文本中移除; str → 替换为该字符串
    matches = []
    inline_counter = 0

    # ── 内联标签: glitch ──
    for m in _GLITCH_RE.finditer(text):
        content = m.group(1).strip()
        iid = inline_counter
        inline_counter += 1
        marker = f"%%GLITCH:{iid}%%{content}%%/GLITCH%%"
        effect = {"type": "glitch", "content": content, "inline_id": iid}
        matches.append((m.start(), m.end(), effect, marker))

    # ── 内联标签: echo_text ──
    for m in _ECHO_TEXT_RE.finditer(text):
        content = m.group(1).strip()
        phases = [p.strip() for p in content.split("|") if p.strip()]
        iid = inline_counter
        inline_counter += 1
        display = phases[0] if phases else content
        marker = f"%%ECHO:{iid}%%{display}%%/ECHO%%"
        effect = {"type": "echo_text", "phases": phases, "inline_id": iid}
        matches.append((m.start(), m.end(), effect, marker))

    # ── 外部标签: paragraph, system_echo, inject_input ──
    for m in _PARAGRAPH_RE.finditer(text):
        delay_ms = _parse_delay_ms(m.group(1))
        content = m.group(2).strip()
        effect = {"type": "paragraph", "content": content}
        if delay_ms is not None:
            effect["delay_ms"] = delay_ms
        matches.append((m.start(), m.end(), effect, None))

    for tag_type, pattern in _EXTERNAL_TAGS:
        for m in pattern.finditer(text):
            content = m.group(1).strip()
            effect = {"type": tag_type, "content": content}
            matches.append((m.start(), m.end(), effect, None))

    # ── map-corrupt: 管道分隔格式 ──
    for m in _MAP_CORRUPT_RE.finditer(text):
        raw = m.group(1).strip()
        parts = raw.split("|", 1)
        if len(parts) == 2:
            target = parts[0].strip()
            content = parts[1].strip()
        else:
            continue  # 格式不正确，跳过
        effect = {"type": "map_corrupt", "target": target, "content": content}
        matches.append((m.start(), m.end(), effect, None))

    if not matches:
        return {"clean_text": text, "effects": []}

    # 按出现位置排序
    matches.sort(key=lambda x: x[0])

    # ── 处理嵌套标签 ──
    # 若 match j 完全包含在 match i 内，则:
    #   1. 从外层效果的 content 中剥离内层标签原文
    #   2. 标记为 nested，clean_text 移除时跳过（外层移除已覆盖）
    nested = set()
    for i in range(len(matches)):
        si, ei = matches[i][0], matches[i][1]
        for j in range(len(matches)):
            if i == j:
                continue
            sj, ej = matches[j][0], matches[j][1]
            if sj >= si and ej <= ei and (ej - sj) < (ei - si):
                nested.add(j)
                # 从外层效果内容中处理内层标签
                inner_raw = text[sj:ej]
                inner_replacement = matches[j][3]
                eff_i = matches[i][2]
                if "content" in eff_i:
                    if inner_replacement is not None:
                        # 内联标签嵌套在外层: 保留为标记（如 %%GLITCH:0%%text%%/GLITCH%%）
                        eff_i["content"] = eff_i["content"].replace(inner_raw, inner_replacement).strip()
                    else:
                        # 外部标签嵌套在外层: 剥离
                        eff_i["content"] = eff_i["content"].replace(inner_raw, "").strip()

    # 提取有序效果列表（包含嵌套效果，仍按顺序触发）
    effects = [m[2] for m in matches]

    # 从后向前替换/移除标签，跳过嵌套匹配（外层移除已覆盖）
    clean = text
    for idx in range(len(matches) - 1, -1, -1):
        if idx in nested:
            continue
        start, end, _, replacement = matches[idx]
        if replacement is not None:
            clean = clean[:start] + replacement + clean[end:]
        else:
            clean = clean[:start] + clean[end:]

    # 清理多余空白
    clean = _MULTI_NEWLINE_RE.sub("\n\n", clean).strip()

    return {"clean_text": clean, "effects": effects}
