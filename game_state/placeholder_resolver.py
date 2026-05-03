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
import random
import re
from typing import Any, Dict, List, Optional

from .character_card import (
    ATTRIBUTE_RANGES,
    BACKGROUND_FIELDS,
    _BACKGROUND_LABEL_ZH,
    get_check_value,
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


# ============================================================================
# Phase 5: 硬 placeholder ({检定:X} / {自动:X>=N})
# 在开局时解析, 骰子结果回灌到 module_data 文本 + 累积 pending_checks 供前端
# 骰点动画管线。d100+threshold 逻辑内联实现, 复用 get_check_value 做取值;
# 不 import rule_ai 以避免 game_state -> ai_layers 循环依赖。
# 需求文档: docs/requirements/2026-05-03-placeholder-and-background-routing.md §3.2.2
# ============================================================================

# {检定:侦查}, {检定:侦查/困难}, {检定:STR/极难} — 允许中英文 skill/attribute 名
_HARD_CHECK_RE = re.compile(r"\{检定:([^}/]+?)(?:/([^}/]+?))?\}")

# {自动:STR>=60} — 仅允许属性名 (中英文)
_HARD_AUTO_RE = re.compile(r"\{自动:([A-Za-z一-鿿]+?)>=(\d+)\}")

# 模块级 accumulator, 由 resolve_hard_placeholders 内部的 _replace 闭包写入;
# 调用方通过 get_and_clear_pending_checks() 消费。
_pending_checks: List[dict] = []


# ---- 内联 d100 辅助 (公式与 rule_ai 完全一致, 仅 5 行) ----

def _compute_threshold(player_skill: int, difficulty: str) -> int:
    if not isinstance(player_skill, int) or player_skill < 0:
        return 0
    if difficulty == "困难":
        return max(1, player_skill // 2)
    if difficulty == "极难":
        return max(1, player_skill // 5)
    return max(1, player_skill)


def _describe_result(success: bool, critical_success: bool, critical_failure: bool) -> str:
    if critical_success:
        return "大成功"
    if critical_failure:
        return "大失败"
    return "成功" if success else "失败"


def _normalize_hard_difficulty(raw: Optional[str]) -> str:
    """把 / 后面的难度文本归一为 '普通'/'困难'/'极难'。"""
    if not isinstance(raw, str) or not raw.strip():
        return "普通"
    d = raw.strip()
    if "极难" in d:
        return "极难"
    if "困难" in d:
        return "困难"
    return "普通"


# ---- public API ----

def get_and_clear_pending_checks() -> list:
    """返回并清空累积的 pending_checks, 供调用方注入前端 dice_rolls 管线。"""
    result = list(_pending_checks)
    _pending_checks.clear()
    return result


def resolve_hard_placeholders(
    text: Any,
    card: Optional[Dict[str, Any]] = None,
    player_state: Optional[Dict[str, Any]] = None,
) -> str:
    """解析单个字符串中的 {检定:X} / {自动:X>=N}, 原地骰 d100 并替换为结果文本。
    累积 structured pending_checks 到 _pending_checks。

    非字符串输入 / 空串 / 无匹配 均原样返回。
    软 placeholder ({属性:X} 等) 不处理 — 留给 resolve_placeholders。
    """
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""
    return _resolve_hard_placeholders_impl(text, card, player_state)


def resolve_hard_in(
    obj: Any,
    card: Optional[Dict[str, Any]] = None,
    player_state: Optional[Dict[str, Any]] = None,
) -> Any:
    """递归在 dict / list / str 中解析硬 placeholder, 返回新结构 (不 mutate 输入)。"""
    if isinstance(obj, str):
        return resolve_hard_placeholders(obj, card, player_state)
    if isinstance(obj, dict):
        return {k: resolve_hard_in(v, card, player_state) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_hard_in(v, card, player_state) for v in obj]
    if isinstance(obj, tuple):
        return tuple(resolve_hard_in(v, card, player_state) for v in obj)
    return obj


def _resolve_hard_placeholders_impl(text: str, card, player_state) -> str:
    """内部实现: 在 resolve_hard_placeholders 的作用域内定义闭包。"""
    def _replace_check(match):
        original = match.group(0)
        skill_name = match.group(1).strip()
        raw_diff = match.group(2)
        difficulty = _normalize_hard_difficulty(raw_diff)

        if not skill_name:
            return original

        # card 有完整 attributes + skills; 优先用它, 降级到 player_state
        lookup = card if isinstance(card, dict) and (card.get("attributes") or card.get("skills")) else player_state
        player_value = get_check_value(skill_name, lookup)
        threshold = _compute_threshold(player_value, difficulty)
        roll = random.randint(1, 100)
        success = roll <= threshold
        critical_success = roll <= 5
        critical_failure = roll >= 96
        desc = _describe_result(success, critical_success, critical_failure)

        # 人类可读的替换文本
        diff_label = f"（{difficulty}）" if difficulty != "普通" else ""
        result_text = f"[检定: {skill_name}{diff_label} d100={roll}/{threshold} {desc}]"

        _pending_checks.append({
            "type": "skill_check",
            "label": f"{skill_name}检定{diff_label}",
            "skill": skill_name,
            "difficulty": difficulty,
            "player_skill": player_value,
            "roll": roll,
            "threshold": threshold,
            "success": success,
            "critical_success": critical_success,
            "critical_failure": critical_failure,
            "description": desc,
        })
        return result_text

    def _replace_auto(match):
        original = match.group(0)
        attr_name = match.group(1).strip()
        threshold_str = match.group(2)
        try:
            required = int(threshold_str)
        except (TypeError, ValueError):
            return original

        if not attr_name:
            return original

        lookup = card if isinstance(card, dict) and (card.get("attributes") or card.get("skills")) else player_state
        player_value = get_check_value(attr_name, lookup)

        if player_value >= required:
            result_text = f"自动通过（{attr_name}={player_value}≥{required}）"
            _pending_checks.append({
                "type": "skill_check",
                "label": f"{attr_name}自动判定",
                "skill": attr_name,
                "difficulty": "自动",
                "player_skill": player_value,
                "roll": None,
                "threshold": required,
                "success": True,
                "critical_success": False,
                "critical_failure": False,
                "description": f"自动通过（{attr_name}={player_value}≥{required}）",
            })
            return result_text

        # 未达标 -> 退化为普通检定
        difficulty = "普通"
        threshold = _compute_threshold(player_value, difficulty)
        roll = random.randint(1, 100)
        success = roll <= threshold
        critical_success = roll <= 5
        critical_failure = roll >= 96
        desc = _describe_result(success, critical_success, critical_failure)

        result_text = (
            f"[未达标: {attr_name}={player_value}<{required}, "
            f"退化为{attr_name}检定 d100={roll}/{threshold} {desc}]"
        )

        _pending_checks.append({
            "type": "skill_check",
            "label": f"{attr_name}检定（自动退化）",
            "skill": attr_name,
            "difficulty": difficulty,
            "player_skill": player_value,
            "roll": roll,
            "threshold": threshold,
            "success": success,
            "critical_success": critical_success,
            "critical_failure": critical_failure,
            "description": desc,
        })
        return result_text

    text = _HARD_CHECK_RE.sub(_replace_check, text)
    text = _HARD_AUTO_RE.sub(_replace_auto, text)
    return text
