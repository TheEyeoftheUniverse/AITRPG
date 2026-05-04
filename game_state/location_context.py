import copy
import re
from typing import Any, Dict, List

from .placeholder_resolver import format_upcoming_checks, strip_check_results


DEFAULT_RUNTIME_MEMORY_TEMPLATE: Dict[str, Any] = {
    "player_facts": {},
    "topics_discussed": [],
    "pending_questions": [],
    "answered_questions": [],
    "promises": [],
    "evidence_seen": [],
    "trust_signals": [],
    "last_impression": {},
    "applied_trust_reasons": [],
    "overheard_remote_dialogue": [],
    "emergency_context": {},
    "interaction_history": [],
    "triggered_events": [],
    "known_clues": [],
}

DEFAULT_CROSS_WALL_TRIGGER_KEYWORDS = (
    "隔壁",
    "门后",
    "墙那边",
    "敲门",
    "敲墙",
    "贴门",
    "朝门后",
    "对门后",
)


VALID_EDGE_STYLES = ("solid", "dashed", "double", "single-arrow")
VALID_EDGE_DIRECTIONS = ("to", "from")


def normalize_exit_entry(entry: Any) -> Dict[str, Any]:
    """规范化模组里的一项 exit, 返回 {to, style, directed, direction}.

    向后兼容: 字符串 -> {to: <name>, style: "solid", directed: False, direction: "to"}.
    新富格式: dict, 接受可选 style / directed / direction. 任何不识别的取值都退回默认.
    无法解析为有效目标名时, 返回 {to: ""}, 调用方按"未知出口"丢弃即可.
    """
    if isinstance(entry, str):
        return {
            "to": entry,
            "style": "solid",
            "directed": False,
            "direction": "to",
        }
    if isinstance(entry, dict):
        target = entry.get("to") or entry.get("target") or entry.get("name") or ""
        if not isinstance(target, str):
            target = ""
        style = entry.get("style", "solid")
        if not (isinstance(style, str) and style in VALID_EDGE_STYLES):
            style = "solid"
        directed_raw = entry.get("directed", False)
        directed = bool(directed_raw) if not isinstance(directed_raw, str) else directed_raw.strip().lower() in ("true", "1", "yes")
        direction = entry.get("direction", "to")
        if not (isinstance(direction, str) and direction in VALID_EDGE_DIRECTIONS):
            direction = "to"
        # single-arrow 必然 directed; directed 但不是 single-arrow 时把 style 升级到 single-arrow
        # 让前端只用 style 一个开关就能拿到正确视觉.
        if style == "single-arrow":
            directed = True
        elif directed:
            style = "single-arrow"
        return {
            "to": target.strip(),
            "style": style,
            "directed": directed,
            "direction": direction,
        }
    return {"to": "", "style": "solid", "directed": False, "direction": "to"}


def is_threat_entity(npc_name: str, npc_data: dict = None) -> bool:
    npc_data = npc_data if isinstance(npc_data, dict) else {}
    return bool(
        npc_data.get("is_threat_entity")
        or npc_data.get("entity_type") == "threat_entity"
    )


def entity_can_speak(entity_data: dict = None) -> bool:
    entity_data = entity_data if isinstance(entity_data, dict) else {}
    dialogue = entity_data.get("dialogue", "__missing__")
    if dialogue == "__missing__":
        return not is_threat_entity(str(entity_data.get("name") or ""), entity_data)
    return isinstance(dialogue, dict)


def get_entity_dialogue_module(entity_data: dict = None) -> Dict[str, Any]:
    entity_data = entity_data if isinstance(entity_data, dict) else {}
    dialogue = entity_data.get("dialogue", {})
    return dialogue if isinstance(dialogue, dict) else {}


def get_entity_trust_module(entity_data: dict = None) -> Dict[str, Any]:
    entity_data = entity_data if isinstance(entity_data, dict) else {}
    trust = entity_data.get("trust", {})
    return trust if isinstance(trust, dict) else {}


def get_entity_memory_module(entity_data: dict = None) -> Dict[str, Any]:
    entity_data = entity_data if isinstance(entity_data, dict) else {}
    memory = entity_data.get("memory", {})
    return memory if isinstance(memory, dict) else {}


def get_entity_long_term_memory(entity_data: dict = None) -> Dict[str, Any]:
    memory = get_entity_memory_module(entity_data)
    long_term = memory.get("long_term", {})
    return long_term if isinstance(long_term, dict) else {}


def get_entity_dialogue_guide(entity_data: dict = None) -> Dict[str, Any]:
    entity_data = entity_data if isinstance(entity_data, dict) else {}
    dialogue = get_entity_dialogue_module(entity_data)
    guide = dialogue.get("guide", {})
    return guide if isinstance(guide, dict) else {}


def get_entity_first_appearance(entity_data: dict = None) -> str:
    entity_data = entity_data if isinstance(entity_data, dict) else {}
    dialogue = get_entity_dialogue_module(entity_data)
    text = str(dialogue.get("first_appearance") or "").strip()
    if text:
        return text
    long_term = get_entity_long_term_memory(entity_data)
    text = str(long_term.get("first_appearance") or "").strip()
    if text:
        return text
    return str(entity_data.get("first_appearance") or "").strip()


def get_entity_trust_map(entity_data: dict = None) -> Dict[str, Any]:
    entity_data = entity_data if isinstance(entity_data, dict) else {}
    trust = get_entity_trust_module(entity_data)
    mapping = trust.get("map", {})
    return mapping if isinstance(mapping, dict) else {}


def get_entity_trust_gates(entity_data: dict = None) -> Dict[str, Any]:
    entity_data = entity_data if isinstance(entity_data, dict) else {}
    trust = get_entity_trust_module(entity_data)
    gates = trust.get("gates", {})
    return gates if isinstance(gates, dict) else {}


def get_entity_trust_threshold(entity_data: dict = None, default: float = 0.5) -> float:
    entity_data = entity_data if isinstance(entity_data, dict) else {}
    trust = get_entity_trust_module(entity_data)
    raw_value = trust.get("threshold", default)
    try:
        return float(raw_value or default)
    except (TypeError, ValueError):
        return float(default)


def get_entity_profile_text(entity_data: dict = None, key: str = "") -> str:
    entity_data = entity_data if isinstance(entity_data, dict) else {}
    field = str(key or "").strip()
    if not field:
        return ""

    if field == "first_appearance":
        return get_entity_first_appearance(entity_data)

    if field == "current_state":
        soft_state = entity_data.get("soft_state", {})
        if isinstance(soft_state, dict):
            summary = str(soft_state.get("initial_summary") or "").strip()
            if summary:
                return summary
        long_term = get_entity_long_term_memory(entity_data)
        for candidate in ("current_situation", "current_state"):
            text = str(long_term.get(candidate) or "").strip()
            if text:
                return text
        return str(entity_data.get("current_state") or "").strip()

    long_term = get_entity_long_term_memory(entity_data)
    text = str(long_term.get(field) or "").strip()
    if text:
        return text
    return str(entity_data.get(field) or "").strip()


def get_primary_pursuer_settings(module_data: dict) -> Dict[str, Any]:
    mechanics = (module_data or {}).get("mechanics", {})
    if not isinstance(mechanics, dict):
        return {}
    primary_pursuer = mechanics.get("primary_pursuer", {})
    return primary_pursuer if isinstance(primary_pursuer, dict) else {}


def get_primary_pursuer_name(module_data: dict) -> str:
    settings = get_primary_pursuer_settings(module_data)
    configured = str(settings.get("entity_name") or settings.get("entity") or "").strip()
    if configured:
        return configured

    explicit_threats = (module_data or {}).get("threat_entities", {})
    if isinstance(explicit_threats, dict):
        for entity_name, entity_data in explicit_threats.items():
            if isinstance(entity_data, dict) and entity_data.get("is_primary_pursuer"):
                return str(entity_name).strip()
        if len(explicit_threats) == 1:
            return str(next(iter(explicit_threats))).strip()

    legacy_npcs = (module_data or {}).get("npcs", {})
    if isinstance(legacy_npcs, dict):
        for entity_name, entity_data in legacy_npcs.items():
            if isinstance(entity_data, dict) and entity_data.get("is_primary_pursuer"):
                return str(entity_name).strip()

    return ""


def normalize_module_data(module_data: dict) -> Dict[str, Any]:
    if not isinstance(module_data, dict):
        return {}

    normalized = copy.deepcopy(module_data)

    npcs = normalized.get("npcs", {})
    if isinstance(npcs, dict):
        normalized["npcs"] = {
            entity_name: _normalize_entity_record(entity_name, entity_data)
            for entity_name, entity_data in npcs.items()
            if isinstance(entity_data, dict)
        }

    threat_entities = normalized.get("threat_entities", {})
    if isinstance(threat_entities, dict):
        normalized["threat_entities"] = {
            entity_name: _normalize_entity_record(entity_name, entity_data, is_threat=True)
            for entity_name, entity_data in threat_entities.items()
            if isinstance(entity_data, dict)
        }

    return normalized


def _normalize_nullish_module(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {"", "none", "null"}:
        return None
    return value


def _normalize_position_module(entity_data: dict) -> dict:
    position = _normalize_nullish_module(entity_data.get("position"))
    if isinstance(position, dict):
        normalized = copy.deepcopy(position)
        normalized.setdefault("initial_location", entity_data.get("location"))
        return normalized
    return {
        "initial_location": entity_data.get("location"),
    }


def _normalize_dialogue_module(entity_name: str, entity_data: dict, is_threat: bool) -> dict | None:
    explicit = _normalize_nullish_module(entity_data.get("dialogue", "__missing__"))
    if explicit != "__missing__":
        if explicit is None:
            return None
        if isinstance(explicit, dict):
            normalized = copy.deepcopy(explicit)
            normalized.setdefault("guide", {})
            normalized.setdefault("first_appearance", entity_data.get("first_appearance", ""))
            return normalized
        return None

    return None


def _normalize_trust_module(entity_data: dict, can_speak: bool) -> dict | None:
    explicit = _normalize_nullish_module(entity_data.get("trust", "__missing__"))
    if explicit != "__missing__":
        if explicit is None:
            return None
        if isinstance(explicit, dict):
            normalized = copy.deepcopy(explicit)
            normalized.setdefault("initial", float(normalized.get("initial", 0.0) or 0.0))
            normalized.setdefault("map", copy.deepcopy(normalized.get("map", {})))
            normalized.setdefault(
                "threshold",
                float(normalized.get("threshold", 0.5) or 0.5),
            )
            normalized.setdefault("gates", copy.deepcopy(normalized.get("gates", {})))
            return normalized
        return None

    return None


def _build_default_long_term_memory(entity_data: dict) -> dict:
    summary = {}
    for key in ("appearance", "personality", "background", "current_state", "first_appearance"):
        value = str(entity_data.get(key) or "").strip()
        if value:
            summary[key] = value
    return summary


def _normalize_memory_module(entity_data: dict, is_threat: bool, can_speak: bool) -> dict | None:
    explicit = _normalize_nullish_module(entity_data.get("memory", "__missing__"))
    if explicit != "__missing__":
        if explicit is None:
            return None
        if isinstance(explicit, dict):
            normalized = copy.deepcopy(explicit)
            normalized.setdefault("long_term", copy.deepcopy(normalized.get("long_term", {})))
            runtime_defaults = normalized.get("runtime_defaults")
            if not isinstance(runtime_defaults, dict):
                runtime_defaults = {}
            merged_defaults = copy.deepcopy(DEFAULT_RUNTIME_MEMORY_TEMPLATE)
            for key, value in runtime_defaults.items():
                merged_defaults[key] = copy.deepcopy(value)
            normalized["runtime_defaults"] = merged_defaults
            return normalized
        return None

    if is_threat and not can_speak:
        return None

    return {
        "long_term": _build_default_long_term_memory(entity_data),
        "runtime_defaults": copy.deepcopy(DEFAULT_RUNTIME_MEMORY_TEMPLATE),
    }


def _normalize_soft_state_module(entity_data: dict, can_speak: bool) -> dict | None:
    explicit = _normalize_nullish_module(entity_data.get("soft_state", "__missing__"))
    if explicit != "__missing__":
        if explicit is None:
            return None
        if isinstance(explicit, dict):
            normalized = copy.deepcopy(explicit)
            normalized.setdefault("initial_tag", str(normalized.get("initial_tag") or "").strip() or ("guarded" if can_speak else "neutral"))
            normalized.setdefault(
                "initial_summary",
                str(normalized.get("initial_summary") or "").strip() or str(entity_data.get("current_state") or "").strip(),
            )
            return normalized
        return None

    if not can_speak:
        return None

    return {
        "initial_tag": "guarded",
        "initial_summary": str(entity_data.get("current_state") or entity_data.get("first_appearance") or "").strip(),
    }


def _normalize_companion_module(entity_data: dict, trust_module: dict | None, is_threat: bool) -> dict | None:
    explicit = _normalize_nullish_module(entity_data.get("companion", "__missing__"))
    if explicit != "__missing__":
        if explicit is None:
            return None
        if isinstance(explicit, dict):
            normalized = copy.deepcopy(explicit)
            normalized.setdefault("enabled_modes", copy.deepcopy(normalized.get("enabled_modes", ["follow", "wait", "bait"])))
            normalized.setdefault("default_mode", str(normalized.get("default_mode") or "wait").strip() or "wait")
            normalized.setdefault("require_explicit_exit", False)
            if "unlock_trust" not in normalized:
                high_gate = ((trust_module or {}).get("gates", {}) or {}).get("high", {})
                normalized["unlock_trust"] = float(high_gate.get("min", (trust_module or {}).get("threshold", 0.5)) or 0.5)
            return normalized
        return None

    return None


def _normalize_entity_record(entity_name: str, entity_data: dict, is_threat: bool = False) -> dict:
    if not isinstance(entity_data, dict):
        return {}

    normalized = copy.deepcopy(entity_data)
    normalized.setdefault("name", entity_name)
    if is_threat:
        normalized["is_threat_entity"] = True
        normalized.setdefault("entity_type", "threat_entity")
    elif is_threat_entity(entity_name, normalized):
        normalized["is_threat_entity"] = True
        normalized.setdefault("entity_type", "threat_entity")

    can_speak = entity_can_speak({
        **normalized,
        "dialogue": normalized.get("dialogue", "__missing__"),
    })
    trust_module = _normalize_trust_module(normalized, can_speak)
    normalized["position"] = _normalize_position_module(normalized)
    normalized["dialogue"] = _normalize_dialogue_module(entity_name, normalized, is_threat)
    normalized["trust"] = trust_module
    normalized["memory"] = _normalize_memory_module(normalized, is_threat, can_speak)
    normalized["soft_state"] = _normalize_soft_state_module(normalized, can_speak)
    normalized["companion"] = _normalize_companion_module(normalized, trust_module, is_threat)

    return normalized


def get_module_npcs(module_data: dict) -> Dict[str, Dict[str, Any]]:
    npcs = (module_data or {}).get("npcs", {})
    if not isinstance(npcs, dict):
        return {}

    primary_pursuer_name = get_primary_pursuer_name(module_data)
    normalized_npcs = {}
    for npc_name, npc_data in npcs.items():
        normalized = _normalize_entity_record(npc_name, npc_data)
        if not normalized or is_threat_entity(npc_name, normalized) or (primary_pursuer_name and npc_name == primary_pursuer_name):
            continue
        normalized_npcs[npc_name] = normalized
    return normalized_npcs


def get_module_threat_entities(module_data: dict) -> Dict[str, Dict[str, Any]]:
    threat_entities = {}
    primary_pursuer_name = get_primary_pursuer_name(module_data)

    legacy_npcs = (module_data or {}).get("npcs", {})
    if isinstance(legacy_npcs, dict):
        for entity_name, entity_data in legacy_npcs.items():
            normalized = _normalize_entity_record(entity_name, entity_data)
            if normalized and (
                is_threat_entity(entity_name, normalized)
                or (primary_pursuer_name and entity_name == primary_pursuer_name)
            ):
                threat_entities[entity_name] = normalized

    explicit_threats = (module_data or {}).get("threat_entities", {})
    if isinstance(explicit_threats, dict):
        for entity_name, entity_data in explicit_threats.items():
            normalized = _normalize_entity_record(entity_name, entity_data, is_threat=True)
            if normalized:
                threat_entities[entity_name] = normalized

    return threat_entities


def get_module_all_entities(module_data: dict) -> Dict[str, Dict[str, Any]]:
    merged = {}
    merged.update(get_module_npcs(module_data))
    merged.update(get_module_threat_entities(module_data))
    return merged


def _get_npc_runtime_location(npc_name: str, game_state: dict, module_data: dict) -> str:
    npc_states = (game_state or {}).get("world_state", {}).get("npcs", {})
    runtime_state = npc_states.get(npc_name, {}) if isinstance(npc_states, dict) else {}
    npc_data = get_module_all_entities(module_data).get(npc_name, {})
    return runtime_state.get("location", npc_data.get("location"))


def get_present_npcs_for_location(game_state: dict, module_data: dict, location_key: str) -> List[str]:
    if not location_key:
        return []

    present_npcs = []
    for npc_name, npc_data in get_module_npcs(module_data).items():
        if _get_npc_runtime_location(npc_name, game_state, module_data) == location_key:
            present_npcs.append(npc_name)
    return present_npcs


def get_present_threats_for_location(game_state: dict, module_data: dict, location_key: str) -> List[str]:
    if not location_key:
        return []

    present_threats = []
    for npc_name, npc_data in get_module_threat_entities(module_data).items():
        if _get_npc_runtime_location(npc_name, game_state, module_data) == location_key:
            present_threats.append(npc_name)
    return present_threats


def _join_descriptions(base_description: str, extra_description: str) -> str:
    base_description = str(base_description or "").strip()
    extra_description = str(extra_description or "").strip()

    if not base_description:
        return extra_description
    if not extra_description:
        return base_description

    if base_description.endswith(("。", "！", "？", ".", "!", "?", "”", "\"", "'")):
        return f"{base_description}{extra_description}"
    return f"{base_description} {extra_description}"


def build_runtime_location_context(
    game_state: Dict[str, Any],
    module_data: Dict[str, Any],
    location_key: str = None,
    resolved_placeholders: Dict[str, Any] = None,
) -> Dict[str, Any]:
    current_location = location_key or (game_state or {}).get("current_location", "master_bedroom")
    raw_location_context = (module_data or {}).get("locations", {}).get(current_location, {})
    if not isinstance(raw_location_context, dict):
        return {}

    location_context = copy.deepcopy(raw_location_context)
    present_npcs = get_present_npcs_for_location(game_state, module_data, current_location)
    present_threats = get_present_threats_for_location(game_state, module_data, current_location)
    npc_present_description = str(raw_location_context.get("npc_present_description") or "").strip()
    threat_present_description = str(
        raw_location_context.get("threat_present_description")
        or raw_location_context.get("entity_present_description")
        or npc_present_description
        or ""
    ).strip()
    active_npc_present_description = strip_check_results(npc_present_description) if present_npcs else ""
    active_threat_present_description = strip_check_results(threat_present_description) if present_threats else ""
    active_presence_description = _join_descriptions(
        active_npc_present_description,
        active_threat_present_description,
    )

    # Keep the original module field untouched. Runtime-only assembled text lives in a separate field.
    raw_desc = str(raw_location_context.get("description", "") or "")
    location_context["runtime_description"] = _join_descriptions(
        strip_check_results(raw_desc),
        active_presence_description,
    )
    location_context["active_npc_present_description"] = active_npc_present_description
    location_context["active_threat_present_description"] = active_threat_present_description
    location_context["present_npcs"] = present_npcs
    location_context["present_threats"] = present_threats
    location_context["npc_present"] = bool(present_npcs)
    location_context["threat_present"] = bool(present_threats)
    location_context["entity_present"] = bool(present_npcs or present_threats)
    location_context["threat_entities"] = list(present_threats)
    primary_pursuer_name = get_primary_pursuer_name(module_data)
    pursuer_state = ((game_state or {}).get("world_state", {}).get("npcs", {}) or {}).get(primary_pursuer_name, {})
    chase_state = (pursuer_state or {}).get("chase_state", {})
    if isinstance(chase_state, dict) and chase_state.get("active"):
        current_loc = current_location
        pursuer_location = str((pursuer_state or {}).get("location") or "").strip()
        status = str(chase_state.get("status") or "idle")
        blocked_at = ""
        contact_location = pursuer_location
        if status == "blocked":
            blocked_at = str(chase_state.get("blocked_at") or pursuer_location or "").strip()
            contact_location = ""
        relation = "unknown"
        if pursuer_location and current_loc:
            if contact_location and contact_location == current_loc:
                relation = "same_room"
            elif blocked_at and blocked_at == current_loc:
                relation = "blocked_outside_current_room"
            else:
                relation = "separate_rooms"
        chase_context = {
            "active": True,
            "status": status,
            "target": chase_state.get("target"),
            "entity_name": primary_pursuer_name or None,
            "entity_location": pursuer_location or None,
            "contact_location": contact_location or None,
            "guard_room": blocked_at or None,
            "player_location": current_loc,
            "blocked_at": blocked_at or None,
            "player_relation": relation,
        }
        location_context["threat_chase"] = chase_context
        location_context["butler_chase"] = chase_context

    location_context["upcoming_checks"] = format_upcoming_checks(
        module_data,
        current_location,
        resolved=resolved_placeholders,
    )

    return location_context


def get_cross_wall_npcs(
    game_state: Dict[str, Any],
    module_data: Dict[str, Any],
    current_location: str,
) -> Dict[str, Dict[str, Any]]:
    """获取可通过隔墙交流到达的NPC（不在当前房间但配置了cross_wall_pairs）。"""
    cross_wall_pairs = (module_data or {}).get("cross_wall_pairs", [])
    if not isinstance(cross_wall_pairs, list):
        return {}
    npc_states = (game_state or {}).get("world_state", {}).get("npcs", {})
    locations = (module_data or {}).get("locations", {})
    result: Dict[str, Dict[str, Any]] = {}

    for pair in cross_wall_pairs:
        if not isinstance(pair, dict):
            continue
        rooms = pair.get("rooms", [])
        if not isinstance(rooms, list) or current_location not in rooms:
            continue
        other_rooms = [r for r in rooms if r != current_location]
        for other_room in other_rooms:
            other_room_data = locations.get(other_room, {}) if isinstance(locations.get(other_room), dict) else {}
            trigger_keywords = [
                str(keyword or "").strip()
                for keyword in pair.get("trigger_keywords", [])
                if str(keyword or "").strip()
            ] if isinstance(pair.get("trigger_keywords"), list) else []
            for npc_name, npc_data in get_module_npcs(module_data).items():
                runtime = npc_states.get(npc_name, {}) if isinstance(npc_states, dict) else {}
                npc_loc = runtime.get("location", npc_data.get("location"))
                if npc_loc == other_room:
                    result[npc_name] = {
                        "cross_wall": True,
                        "wall_type": pair.get("type", "voice_only"),
                        "from_room": other_room,
                        "from_room_display_name": str(other_room_data.get("name") or other_room).strip(),
                        "quoted_dialogue_only": bool(pair.get("quoted_dialogue_only", True)),
                        "passive_overhear": bool(pair.get("passive_overhear", pair.get("type", "voice_only") == "voice_only")),
                        "expose_npc_context_only_on_trigger": bool(pair.get("expose_npc_context_only_on_trigger", True)),
                        "trigger_keywords": trigger_keywords,
                    }
    return result


def extract_quoted_dialogue_segments(text: str) -> List[str]:
    source = str(text or "").strip()
    if not source:
        return []

    patterns = (
        r"“([^”]+)”",
        r'"([^"\r\n]+)"',
        r"「([^」]+)」",
        r"『([^』]+)』",
    )
    segments: List[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, source):
            content = str(match or "").strip()
            if content and content not in segments:
                segments.append(content)
    return segments


def has_cross_wall_contact_history(runtime_state: dict = None) -> bool:
    runtime_state = runtime_state if isinstance(runtime_state, dict) else {}
    memory = runtime_state.get("memory", {}) if isinstance(runtime_state.get("memory"), dict) else {}
    return bool(
        runtime_state.get("trust_level")
        or memory.get("player_facts")
        or memory.get("topics_discussed")
        or memory.get("answered_questions")
        or memory.get("promises")
        or memory.get("evidence_seen")
        or memory.get("overheard_remote_dialogue")
        or memory.get("interaction_history")
    )


def should_enable_cross_wall_npc_context(
    player_input: str,
    npc_name: str,
    cross_info: dict = None,
    is_dialogue_turn: bool = False,
    has_prior_contact: bool = False,
) -> bool:
    cross_info = cross_info if isinstance(cross_info, dict) else {}
    if not is_dialogue_turn:
        return False
    if not cross_info.get("expose_npc_context_only_on_trigger", True):
        return True
    if has_prior_contact:
        return True

    text = str(player_input or "").strip()
    lowered = text.lower()
    room_name = str(cross_info.get("from_room_display_name") or "").strip()
    trigger_keywords = [
        str(keyword or "").strip()
        for keyword in cross_info.get("trigger_keywords", [])
        if str(keyword or "").strip()
    ] or list(DEFAULT_CROSS_WALL_TRIGGER_KEYWORDS)

    if npc_name and str(npc_name).lower() in lowered:
        return True
    if room_name and room_name in text:
        return True
    return any(keyword in text for keyword in trigger_keywords)


def build_adjacent_locations_context(
    game_state: Dict[str, Any],
    module_data: Dict[str, Any],
    location_key: str = None,
) -> List[Dict[str, Any]]:
    """构建当前场景相邻场景的上下文列表（仅description + 门状态）。"""
    current_location = location_key or (game_state or {}).get("current_location", "master_bedroom")
    locations = (module_data or {}).get("locations", {})
    current_loc_data = locations.get(current_location, {})
    if not isinstance(current_loc_data, dict):
        return []

    # 构建 显示名 → location key 映射
    name_to_key = {}
    for key, loc_data in locations.items():
        name = (loc_data if isinstance(loc_data, dict) else {}).get("name", "")
        if name:
            name_to_key[name] = key

    visited = (game_state or {}).get("visited_locations", [])
    exits = current_loc_data.get("exits", [])

    adjacent = []
    for exit_name in exits:
        adj_key = name_to_key.get(exit_name)
        if not adj_key or adj_key not in locations:
            continue
        adj_data = locations[adj_key]
        if not isinstance(adj_data, dict):
            continue

        has_door = bool(adj_data.get("has_door"))
        door_closed = has_door and adj_key not in visited

        adjacent.append({
            "name": exit_name,
            "description": adj_data.get("description", ""),
            "door_closed": door_closed,
        })

    return adjacent
