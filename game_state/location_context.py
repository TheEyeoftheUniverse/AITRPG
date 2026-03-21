import copy
from typing import Any, Dict, List


THREAT_ENTITY_NAMES = {"管家"}


def is_threat_entity(npc_name: str, npc_data: dict = None) -> bool:
    if npc_name in THREAT_ENTITY_NAMES:
        return True
    npc_data = npc_data if isinstance(npc_data, dict) else {}
    return bool(npc_data.get("is_threat_entity"))


def _get_npc_runtime_location(npc_name: str, game_state: dict, module_data: dict) -> str:
    npc_states = (game_state or {}).get("world_state", {}).get("npcs", {})
    runtime_state = npc_states.get(npc_name, {}) if isinstance(npc_states, dict) else {}
    npc_data = (module_data or {}).get("npcs", {}).get(npc_name, {})
    return runtime_state.get("location", npc_data.get("location"))


def get_present_npcs_for_location(game_state: dict, module_data: dict, location_key: str) -> List[str]:
    if not location_key:
        return []

    npcs = (module_data or {}).get("npcs", {})
    present_npcs = []
    for npc_name, npc_data in npcs.items():
        if is_threat_entity(npc_name, npc_data):
            continue
        if _get_npc_runtime_location(npc_name, game_state, module_data) == location_key:
            present_npcs.append(npc_name)
    return present_npcs


def get_present_threats_for_location(game_state: dict, module_data: dict, location_key: str) -> List[str]:
    if not location_key:
        return []

    npcs = (module_data or {}).get("npcs", {})
    present_threats = []
    for npc_name, npc_data in npcs.items():
        if not is_threat_entity(npc_name, npc_data):
            continue
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
    active_presence_description = npc_present_description if (present_npcs or present_threats) else ""

    # Keep the original module field untouched. Runtime-only assembled text lives in a separate field.
    location_context["runtime_description"] = _join_descriptions(
        raw_location_context.get("description", ""),
        active_presence_description,
    )
    location_context["active_npc_present_description"] = active_presence_description
    location_context["active_threat_present_description"] = active_presence_description if present_threats else ""
    location_context["present_npcs"] = present_npcs
    location_context["present_threats"] = present_threats
    location_context["npc_present"] = bool(present_npcs)
    location_context["threat_present"] = bool(present_threats)
    location_context["entity_present"] = bool(present_npcs or present_threats)
    location_context["threat_entities"] = list(present_threats)

    return location_context
