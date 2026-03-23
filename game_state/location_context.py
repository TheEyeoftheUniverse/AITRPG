import copy
from typing import Any, Dict, List


THREAT_ENTITY_NAMES = {"管家"}


def is_threat_entity(npc_name: str, npc_data: dict = None) -> bool:
    if npc_name in THREAT_ENTITY_NAMES:
        return True
    npc_data = npc_data if isinstance(npc_data, dict) else {}
    return bool(
        npc_data.get("is_threat_entity")
        or npc_data.get("entity_type") == "threat_entity"
    )


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
    return normalized


def get_module_npcs(module_data: dict) -> Dict[str, Dict[str, Any]]:
    npcs = (module_data or {}).get("npcs", {})
    if not isinstance(npcs, dict):
        return {}

    normalized_npcs = {}
    for npc_name, npc_data in npcs.items():
        normalized = _normalize_entity_record(npc_name, npc_data)
        if not normalized or is_threat_entity(npc_name, normalized):
            continue
        normalized_npcs[npc_name] = normalized
    return normalized_npcs


def get_module_threat_entities(module_data: dict) -> Dict[str, Dict[str, Any]]:
    threat_entities = {}

    legacy_npcs = (module_data or {}).get("npcs", {})
    if isinstance(legacy_npcs, dict):
        for entity_name, entity_data in legacy_npcs.items():
            normalized = _normalize_entity_record(entity_name, entity_data)
            if normalized and is_threat_entity(entity_name, normalized):
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
    active_npc_present_description = npc_present_description if present_npcs else ""
    active_threat_present_description = threat_present_description if present_threats else ""
    active_presence_description = _join_descriptions(
        active_npc_present_description,
        active_threat_present_description,
    )

    # Keep the original module field untouched. Runtime-only assembled text lives in a separate field.
    location_context["runtime_description"] = _join_descriptions(
        raw_location_context.get("description", ""),
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

    return location_context


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
