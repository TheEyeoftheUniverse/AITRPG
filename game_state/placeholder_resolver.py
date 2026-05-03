"""模组占位符解析器 (Phase 3, 软 placeholder)。

支持四种语法, 严格白名单匹配:

  {个人描述} / {思想/信念} / {重要之人} / {意义非凡之地} / {宝贵之物} / {特质}
      6 项 background 中文别名, 兼容老写法

  {背景:personal_description} / {背景:个人描述} / {背景:全部}
      显式 background 引用; 中英 key 都接受; "全部" 聚合 6 项

  {属性:STR} / {属性:LUCK}
      8 大属性 + LUCK 数值

  {技能:侦查} / {技能:图书馆}
      技能数值 (中文技能名)

不识别的 placeholder 保留原文 + warning 日志, 不阻断流程。未填写字段
展开为空串 (不是 "未填写"), 与需求 §3.2.1 对齐。idempotent: 仅匹配 4
种已知前缀, 不与 prompt 模板的 {round_count} 等系统占位符冲突 (后者
没有这 4 种前缀)。

需求文档: docs/requirements/2026-05-03-placeholder-and-background-routing.md §3.2.1
"""

import logging
import re
from typing import Any, Dict, Optional

from .character_card import (
    ATTRIBUTE_RANGES,
    BACKGROUND_FIELDS,
    _BACKGROUND_LABEL_ZH,
)


logger = logging.getLogger(__name__)


# 单层 {...} 匹配 (不允许嵌套花括号)
_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")

# 中文 background 标签 -> 英文 key, 复用 character_card 现成映射不重写
_BG_ZH_TO_EN: Dict[str, str] = {v: k for k, v in _BACKGROUND_LABEL_ZH.items()}

# 8 大属性 + LUCK
_ATTRIBUTE_KEYS = set(ATTRIBUTE_RANGES.keys()) | {"LUCK"}

# 用于 {属性:X} 错误日志, 避免每次重新拼字符串
_ATTRIBUTE_KEYS_HINT = "/".join(sorted(_ATTRIBUTE_KEYS))


def _get_background_field(card: Optional[Dict[str, Any]], field: str) -> Optional[str]:
    """返回指定 background 字段的值 (中英 key 都接受)。
    None 表示 field 不在 schema 内; 空串表示合法但未填写。"""
    if not isinstance(card, dict):
        return None
    en_key = field if field in BACKGROUND_FIELDS else _BG_ZH_TO_EN.get(field)
    if en_key is None:
        return None
    bg = card.get("background") or {}
    return str(bg.get(en_key, "") or "")


def _get_all_background(card: Optional[Dict[str, Any]]) -> str:
    """聚合 6 项 background, 用 'zh_label: value' 拼接, 空字段跳过。"""
    if not isinstance(card, dict):
        return ""
    bg = card.get("background") or {}
    parts = []
    for en_key in BACKGROUND_FIELDS:
        v = str(bg.get(en_key, "") or "").strip()
        if v:
            parts.append(f"{_BACKGROUND_LABEL_ZH[en_key]}: {v}")
    return "; ".join(parts)


def _get_attribute(card: Optional[Dict[str, Any]],
                   player_state: Optional[Dict[str, Any]],
                   key: str) -> Optional[str]:
    if key not in _ATTRIBUTE_KEYS:
        return None
    if isinstance(card, dict):
        attrs = card.get("attributes") or {}
        if key in attrs:
            return str(attrs[key])
    if isinstance(player_state, dict):
        # PRESET_PLAYER_PROFILE 没有 attributes 字段, 但 LUCK 在 top-level
        if key == "LUCK" and "luck" in player_state:
            return str(player_state["luck"])
    return ""


def _get_skill(card: Optional[Dict[str, Any]],
               player_state: Optional[Dict[str, Any]],
               key: str) -> Optional[str]:
    """返回技能数值。None 表示 key 形式非法 (空/超长); 空串表示卡里无该技能。"""
    if not key or len(key) > 20:
        return None
    if isinstance(card, dict):
        skills = card.get("skills") or {}
        if key in skills:
            return str(skills[key])
    if isinstance(player_state, dict):
        skills = player_state.get("skills") or {}
        if key in skills:
            return str(skills[key])
    return ""


def resolve_placeholders(text: Any,
                         card: Optional[Dict[str, Any]],
                         player_state: Optional[Dict[str, Any]] = None) -> str:
    """解析单个字符串中的软 placeholder。
    text 非字符串时 return "" (调用方一般传 str, 但 dict 遍历会经过非字符串值)。"""
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""

    def _replace(match):
        original = match.group(0)
        inner = match.group(1).strip()
        if not inner:
            return original

        # 1) 单字段中文别名: {个人描述} / {思想/信念} / ...; 以及聚合 {全部}
        if inner == "全部":
            return _get_all_background(card)
        if inner in _BG_ZH_TO_EN:
            v = _get_background_field(card, inner)
            return v if v is not None else original

        # 2) 显式前缀
        if ":" in inner:
            prefix, _, value = inner.partition(":")
            prefix = prefix.strip()
            value = value.strip()
            if prefix == "背景":
                if value == "全部":
                    return _get_all_background(card)
                v = _get_background_field(card, value)
                if v is None:
                    logger.warning(
                        "placeholder_resolver: 未识别 background key %r "
                        "(合法: 6 项英文 key + 中文别名 + '全部')",
                        original,
                    )
                    return original
                return v
            if prefix == "属性":
                v = _get_attribute(card, player_state, value)
                if v is None:
                    logger.warning(
                        "placeholder_resolver: 未识别 attribute key %r (合法: %s)",
                        original, _ATTRIBUTE_KEYS_HINT,
                    )
                    return original
                return v
            if prefix == "技能":
                v = _get_skill(card, player_state, value)
                if v is None:
                    logger.warning(
                        "placeholder_resolver: 技能 key 形式非法 %r "
                        "(须为 1-20 字符的非空中文技能名)",
                        original,
                    )
                    return original
                return v

        # 3) 不属于 4 种已知形式 -> 保留原文, 不打 warning
        # (留给上层 prompt 模板 / 其他系统; 例如 {round_count} 由 ai_layer 自己处理)
        return original

    return _PLACEHOLDER_RE.sub(_replace, text)


def resolve_in(obj: Any,
               card: Optional[Dict[str, Any]],
               player_state: Optional[Dict[str, Any]] = None) -> Any:
    """递归在 dict / list / str 中解析 placeholder, 返回新结构 (不 mutate 输入)。
    其他标量类型 (int / bool / None / ...) 原样返回。"""
    if isinstance(obj, str):
        return resolve_placeholders(obj, card, player_state)
    if isinstance(obj, dict):
        return {k: resolve_in(v, card, player_state) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_in(v, card, player_state) for v in obj]
    if isinstance(obj, tuple):
        return tuple(resolve_in(v, card, player_state) for v in obj)
    return obj
