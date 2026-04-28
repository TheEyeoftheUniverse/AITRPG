import json
import os
import copy
import random
from collections import deque
from typing import Dict, Any, List, Set
from .location_context import (
    DEFAULT_RUNTIME_MEMORY_TEMPLATE,
    build_runtime_location_context,
    get_entity_dialogue_guide,
    get_entity_first_appearance,
    get_module_all_entities,
    get_module_npcs,
    get_primary_pursuer_name,
    get_primary_pursuer_settings,
    normalize_module_data,
)


PRIMARY_PURSUER_ROLE = "primary_pursuer"


PRESET_PLAYER_PROFILE = {
    "name": "调查员",
    "san": 65,
    "hp": 12,
    "skills": {
        "侦查": 60,
        "图书馆": 60,
        "聆听": 50,
        "教育": 60,
        "心理学": 50,
        "说服": 50,
        "话术": 40,
        "潜行": 40,
        "斗殴": 45,
        "闪避": 30
    },
    "inventory": ["手电筒"]
}


class SessionManager:
    """游戏会话管理器"""

    def __init__(self, default_module_name: str = "default_module"):
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.default_module_name = default_module_name
        self.default_module_data = self._load_module(default_module_name)

    def list_modules(self) -> list:
        """列出所有可用模组"""
        modules_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "modules")
        modules = []
        for filename in sorted(os.listdir(modules_dir)):
            if filename.endswith(".json"):
                module_path = os.path.join(modules_dir, filename)
                try:
                    with open(module_path, "r", encoding="utf-8-sig") as f:
                        data = json.load(f)
                    info = data.get("module_info", {})
                    modules.append({
                        "filename": filename[:-5],
                        "name": info.get("name", filename[:-5]),
                        "module_type": info.get("module_type", ""),
                        "description": info.get("description", ""),
                        "opening": info.get("opening", "")
                    })
                except Exception:
                    pass
        return modules

    def _load_module(self, module_name: str = "default_module"):
        """加载模组数据"""
        # 从JSON文件加载模组
        module_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "modules",
            f"{module_name}.json"
        )

        try:
            with open(module_path, "r", encoding="utf-8-sig") as f:
                module_data = json.load(f)
            return normalize_module_data(module_data)
        except FileNotFoundError:
            # 如果文件不存在，返回一个最小化的默认模组
            return normalize_module_data({
                "module_info": {
                    "name": "默认模组",
                    "theme": "克苏鲁恐怖",
                    "target_rounds": 30
                },
                "locations": {},
                "objects": {},
                "npcs": {},
                "escape_conditions": {}
            })
        except json.JSONDecodeError as e:
            raise ValueError(f"模组JSON格式错误: {e}")

    def create_session(self, session_id: str, module_filename: str = None):
        """创建新游戏会话"""
        module_filename = module_filename or self.default_module_name
        module_data = self._load_module(module_filename)
        initial_location = self._get_initial_location(module_data)

        self.sessions[session_id] = {
            "session_id": session_id,
            "module_filename": module_filename,
            "module_data": module_data,
            "current_location": initial_location,
            "round_count": 0,

            "player": {
                "name": "调查员",
                "san": 65,
                "hp": 12,
                "skills": {
                    "侦查": 60,
                    "图书馆": 40,
                    "聆听": 50
                },
                "inventory": ["手电筒"]
            },

            "world_state": {
                "clues_found": [],
                "npcs": self._build_initial_npc_state(module_data),
                "triggered_sanchecks": [],
                "follow_arrival_seen": {},
                "flags": {
                    "door_unlocked": False,
                    "truth_revealed": False
                }
            },

            "influence_dimensions": self._build_default_influence_dimensions(),

            # 三层AI的上下文
            "rhythm_context": [],  # 节奏AI保存游戏状态变化
            "narrative_history": deque(),  # 文案AI保存每轮历史总结，长期保留供摘要回放
            "visited_locations": [initial_location],  # 已访问过的location key列表
        }
        self.sessions[session_id]["player"] = self._build_default_player_state()
        self._ensure_runtime_defaults(self.sessions[session_id], module_data)

    def _get_initial_location(self, module_data: Dict[str, Any]) -> str:
        """获取模组的初始位置"""
        module_info = module_data.get("module_info", {})
        configured = module_info.get("start_location")
        locations = module_data.get("locations", {})

        if configured in locations:
            return configured
        if "master_bedroom" in locations:
            return "master_bedroom"
        if locations:
            return next(iter(locations))
        return "master_bedroom"

    def _build_initial_npc_state(self, module_data: Dict[str, Any]) -> Dict[str, Any]:
        """根据模组NPC定义生成初始世界状态"""
        npc_states = {}
        module_npcs = get_module_npcs(module_data)
        primary_pursuer_name = self._get_primary_pursuer_name(module_data)
        for npc_name, npc_data in get_module_all_entities(module_data).items():
            trust_module = npc_data.get("trust") if isinstance(npc_data.get("trust"), dict) else {}
            initial_trust = float(trust_module.get("initial", 0.0) or 0.0)
            memory_module = npc_data.get("memory") if isinstance(npc_data.get("memory"), dict) else None
            companion_module = npc_data.get("companion") if isinstance(npc_data.get("companion"), dict) else None
            soft_state_module = npc_data.get("soft_state") if isinstance(npc_data.get("soft_state"), dict) else None
            npc_states[npc_name] = {
                "attitude": npc_data.get("initial_attitude", "中立"),
                "relationship": {
                    "trust": round(initial_trust, 2),
                },
                "trust_level": round(initial_trust, 2),
                "memory": self._build_initial_npc_memory(memory_module),
                "memory_long_term": self._build_initial_npc_long_term_memory(memory_module),
                "preset_task": {},
                "preset_task_history": [],
            }
            initial_location = str(
                npc_data.get("location")
                or ((npc_data.get("position") or {}).get("initial_location") if isinstance(npc_data.get("position"), dict) else "")
                or ""
            ).strip()
            if initial_location:
                npc_states[npc_name]["location"] = initial_location
            if soft_state_module:
                npc_states[npc_name]["soft_state"] = self._build_initial_soft_state(soft_state_module)
            if primary_pursuer_name and npc_name == primary_pursuer_name:
                npc_states[npc_name]["chase_state"] = self._build_default_butler_chase_state()
            if companion_module and npc_name in module_npcs:
                default_mode = str(companion_module.get("default_mode") or "wait").strip() or "wait"
                npc_states[npc_name]["companion_mode"] = default_mode
                npc_states[npc_name]["companion_state"] = default_mode
                npc_states[npc_name]["companion_task"] = {}
        return npc_states

    def _build_initial_npc_memory(self, memory_module: Dict[str, Any] = None) -> Dict[str, Any]:
        runtime_memory = copy.deepcopy(DEFAULT_RUNTIME_MEMORY_TEMPLATE)
        if isinstance(memory_module, dict):
            runtime_defaults = memory_module.get("runtime_defaults", {})
            if isinstance(runtime_defaults, dict):
                for key, value in runtime_defaults.items():
                    runtime_memory[key] = copy.deepcopy(value)
        return runtime_memory

    def _build_initial_npc_long_term_memory(self, memory_module: Dict[str, Any] = None) -> Dict[str, Any]:
        if not isinstance(memory_module, dict):
            return {}
        long_term = memory_module.get("long_term", {})
        return copy.deepcopy(long_term) if isinstance(long_term, dict) else {}

    def _build_initial_soft_state(self, soft_state_module: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "tag": str(soft_state_module.get("initial_tag") or "neutral").strip() or "neutral",
            "summary": str(soft_state_module.get("initial_summary") or "").strip(),
            "updated_round": 0,
        }

    def _build_default_butler_chase_state(self) -> Dict[str, Any]:
        return {
            "active": False,
            "status": "idle",
            "target": None,
            "activation_round": None,
            "last_target_location": None,
            "same_location_rounds": 0,
            "blocked_at": None,
        }

    def _sync_single_npc_runtime_state(self, npc_state: Dict[str, Any], npc_data: Dict[str, Any] = None):
        if not isinstance(npc_state, dict):
            return

        npc_data = npc_data if isinstance(npc_data, dict) else {}
        trust_cfg = npc_data.get("trust") if isinstance(npc_data.get("trust"), dict) else {}
        relationship = npc_state.get("relationship")
        if not isinstance(relationship, dict):
            relationship = {}
            npc_state["relationship"] = relationship
        trust_value = npc_state.get("trust_level", relationship.get("trust", trust_cfg.get("initial", 0.0)))
        trust_value = round(float(trust_value or 0.0), 2)
        relationship["trust"] = trust_value
        npc_state["trust_level"] = trust_value

        memory_cfg = npc_data.get("memory") if isinstance(npc_data.get("memory"), dict) else None
        if not isinstance(npc_state.get("memory"), dict):
            npc_state["memory"] = self._build_initial_npc_memory(memory_cfg)
        if "memory_long_term" not in npc_state or not isinstance(npc_state.get("memory_long_term"), dict):
            npc_state["memory_long_term"] = self._build_initial_npc_long_term_memory(memory_cfg)

        soft_state_cfg = npc_data.get("soft_state") if isinstance(npc_data.get("soft_state"), dict) else None
        if soft_state_cfg:
            soft_state = npc_state.get("soft_state")
            if not isinstance(soft_state, dict):
                soft_state = self._build_initial_soft_state(soft_state_cfg)
                npc_state["soft_state"] = soft_state
            soft_state.setdefault("tag", str(soft_state_cfg.get("initial_tag") or "neutral").strip() or "neutral")
            soft_state.setdefault("summary", str(soft_state_cfg.get("initial_summary") or "").strip())
            soft_state.setdefault("updated_round", 0)

        companion_cfg = npc_data.get("companion") if isinstance(npc_data.get("companion"), dict) else None
        if companion_cfg:
            default_mode = str(companion_cfg.get("default_mode") or "wait").strip() or "wait"
            companion_mode = str(npc_state.get("companion_mode") or npc_state.get("companion_state") or default_mode).strip() or default_mode
            npc_state["companion_mode"] = companion_mode
            npc_state["companion_state"] = companion_mode
            companion_task = npc_state.get("companion_task")
            if not isinstance(companion_task, dict):
                npc_state["companion_task"] = {}

        if not isinstance(npc_state.get("preset_task"), dict):
            npc_state["preset_task"] = {}
        if not isinstance(npc_state.get("preset_task_history"), list):
            npc_state["preset_task_history"] = []

    def _get_primary_pursuer_name(self, module_data: Dict[str, Any]) -> str:
        return get_primary_pursuer_name(module_data)

    def _get_primary_pursuer_name_from_state(self, state: Dict[str, Any]) -> str:
        module_data = (state or {}).get("module_data", {}) if isinstance(state, dict) else {}
        return self._get_primary_pursuer_name(module_data)

    def _get_primary_pursuer_settings(self, module_data: Dict[str, Any]) -> Dict[str, Any]:
        return get_primary_pursuer_settings(module_data)

    def _get_module_preset_tasks(self, module_data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(module_data, dict):
            return {}
        preset_tasks = module_data.get("preset_tasks", {})
        return preset_tasks if isinstance(preset_tasks, dict) else {}

    def _get_module_micro_scenes(self, module_data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(module_data, dict):
            return {}
        micro_scenes = module_data.get("micro_scenes", {})
        return micro_scenes if isinstance(micro_scenes, dict) else {}

    def _append_preset_task_history(self, npc_state: Dict[str, Any], entry: Dict[str, Any]):
        if not isinstance(npc_state, dict) or not isinstance(entry, dict):
            return
        history = npc_state.setdefault("preset_task_history", [])
        if not isinstance(history, list):
            history = []
            npc_state["preset_task_history"] = history
        history.append(copy.deepcopy(entry))

    def _is_player_movement_restricted_by_pursuer_state(self, state: Dict[str, Any]) -> bool:
        if not isinstance(state, dict):
            return False
        chase_state = self._get_butler_runtime_state(state).get("chase_state", {})
        if not isinstance(chase_state, dict):
            return False
        current_location = str(state.get("current_location") or "").strip()
        blocked_at = str(chase_state.get("blocked_at") or "").strip()
        if bool(chase_state.get("active")) and str(chase_state.get("status") or "").strip() == "blocked":
            return bool(current_location) and blocked_at == current_location
        return (
            bool(chase_state.get("active"))
            and str(chase_state.get("status") or "").strip() != "blocked"
            and str(chase_state.get("target") or "player").strip() == "player"
        )

    def _is_micro_scene_visible(self, state: Dict[str, Any], micro_scene_cfg: Dict[str, Any]) -> bool:
        if not isinstance(state, dict) or not isinstance(micro_scene_cfg, dict):
            return False
        parent_location = str(micro_scene_cfg.get("parent_location") or "").strip()
        current_location = str(state.get("current_location") or "").strip()
        if not parent_location or current_location != parent_location:
            return False

        visible_when = micro_scene_cfg.get("visible_when", {}) if isinstance(micro_scene_cfg.get("visible_when"), dict) else {}
        if visible_when.get("guard_room_is_parent") and self._get_butler_guard_room_from_state(state) != parent_location:
            return False

        inventory = state.get("player", {}).get("inventory", []) if isinstance(state.get("player", {}).get("inventory"), list) else []
        required_inventory = [
            str(item or "").strip()
            for item in visible_when.get("requires_inventory", [])
            if str(item or "").strip()
        ]
        if required_inventory and any(item not in inventory for item in required_inventory):
            return False

        flags = state.get("world_state", {}).get("flags", {}) if isinstance(state.get("world_state", {}).get("flags"), dict) else {}
        required_flags = [
            str(item or "").strip()
            for item in visible_when.get("requires_flags", [])
            if str(item or "").strip()
        ]
        if required_flags and any(not flags.get(flag) for flag in required_flags):
            return False

        return True

    def _build_first_entry_block_config(
        self,
        entry_cfg: Dict[str, Any],
        *,
        fallback_flag: str = "",
        fallback_text: str = "",
        fallback_reason_flag: str = "",
        fallback_reason: str = "",
    ) -> Dict[str, Any]:
        entry_cfg = entry_cfg if isinstance(entry_cfg, dict) else {}
        first_entry = entry_cfg.get("first_entry_blocked", {})
        if not isinstance(first_entry, dict):
            first_entry = {}

        legacy_flag = str(entry_cfg.get("first_enter_warning_flag") or "").strip()
        legacy_text = str(entry_cfg.get("first_enter_text") or "").strip()
        flag = str(first_entry.get("flag") or legacy_flag or fallback_flag or "").strip()
        text = str(first_entry.get("text") or legacy_text or fallback_text or "").strip()
        if not flag or not text:
            return {}

        mode = str(first_entry.get("mode") or "block").strip().lower()
        if mode != "warn_only":
            mode = "block"

        reason_flag = str(first_entry.get("reason_flag") or fallback_reason_flag or "").strip()
        reason_value = str(first_entry.get("reason_value") or fallback_reason or "").strip()

        requires_current_location = first_entry.get("requires_current_location", [])
        if isinstance(requires_current_location, str):
            requires_current_location = [requires_current_location]
        elif not isinstance(requires_current_location, list):
            requires_current_location = []

        visited_locations_on_block = first_entry.get("visited_locations_on_block", [])
        if isinstance(visited_locations_on_block, str):
            visited_locations_on_block = [visited_locations_on_block]
        elif not isinstance(visited_locations_on_block, list):
            visited_locations_on_block = []

        set_flags = first_entry.get("set_flags", {})
        return {
            "flag": flag,
            "text": text,
            "mode": mode,
            "reason_flag": reason_flag,
            "reason_value": reason_value,
            "requires_current_location": [
                str(value or "").strip()
                for value in requires_current_location
                if str(value or "").strip()
            ],
            "visited_locations_on_block": [
                str(value or "").strip()
                for value in visited_locations_on_block
                if str(value or "").strip()
            ],
            "set_flags": copy.deepcopy(set_flags) if isinstance(set_flags, dict) else {},
        }

    def _consume_first_entry_block(
        self,
        state: Dict[str, Any],
        block_cfg: Dict[str, Any],
        *,
        current_location: str = "",
    ) -> str:
        if not isinstance(state, dict) or not isinstance(block_cfg, dict) or not block_cfg.get("flag"):
            return ""

        active_location = str(current_location or state.get("current_location") or "").strip()
        required_locations = block_cfg.get("requires_current_location", [])
        if isinstance(required_locations, list) and required_locations and active_location not in required_locations:
            return ""

        world_state = state.setdefault("world_state", {})
        flags = world_state.setdefault("flags", {})
        flag = str(block_cfg.get("flag") or "").strip()
        if flags.get(flag):
            return ""

        flags[flag] = True
        reason_flag = str(block_cfg.get("reason_flag") or "").strip()
        reason_value = str(block_cfg.get("reason_value") or "").strip()
        if reason_flag and reason_value:
            flags[reason_flag] = reason_value

        set_flags = block_cfg.get("set_flags", {})
        if isinstance(set_flags, dict) and set_flags:
            flags.update(copy.deepcopy(set_flags))

        visited = state.setdefault("visited_locations", [])
        for location_key in block_cfg.get("visited_locations_on_block", []):
            if location_key not in visited:
                visited.append(location_key)

        return str(block_cfg.get("text") or "").strip()

    def _get_location_first_entry_block_config(self, state: Dict[str, Any], target_key: str) -> Dict[str, Any]:
        if not isinstance(state, dict):
            return {}
        module_data = state.get("module_data", {}) if isinstance(state.get("module_data"), dict) else {}
        locations = module_data.get("locations", {}) if isinstance(module_data.get("locations"), dict) else {}
        location_cfg = locations.get(target_key, {}) if isinstance(locations.get(target_key), dict) else {}
        return self._build_first_entry_block_config(location_cfg)

    def get_available_micro_scenes(self, session_id: str) -> Dict[str, Dict[str, Any]]:
        state = self.sessions.get(session_id)
        if not state:
            return {}
        self._ensure_runtime_defaults(state)
        module_data = self.get_module_data(session_id)
        available = {}
        for micro_scene_id, cfg in self._get_module_micro_scenes(module_data).items():
            if not isinstance(cfg, dict):
                continue
            if self._is_micro_scene_visible(state, cfg):
                available[micro_scene_id] = copy.deepcopy(cfg)
        return available

    def enter_micro_scene(self, session_id: str, micro_scene_id: str) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state:
            return {"success": False, "message": "会话不存在"}

        self._ensure_runtime_defaults(state)
        module_data = self.get_module_data(session_id)
        micro_scenes = self._get_module_micro_scenes(module_data)
        micro_scene = micro_scenes.get(micro_scene_id, {}) if isinstance(micro_scenes.get(micro_scene_id), dict) else {}
        if not micro_scene:
            return {"success": False, "message": "微场景不存在"}
        if not self._is_micro_scene_visible(state, micro_scene):
            return {"success": False, "message": "当前无法进入该微场景"}

        warning_text = self._consume_first_entry_block(
            state,
            self._build_first_entry_block_config(micro_scene),
        )
        if warning_text:
            return {
                "success": True,
                "warning_only": True,
                "micro_scene_id": micro_scene_id,
                "message": warning_text or "你本能地停下了。再继续下去，只会触发更糟的后果。",
            }

        ending_id = str(
            micro_scene.get("ending_on_reenter")
            or micro_scene.get("ending_on_enter")
            or ""
        ).strip()
        if ending_id:
            self.trigger_ending(session_id, ending_id)
            return {
                "success": True,
                "ending_triggered": True,
                "micro_scene_id": micro_scene_id,
                "ending_id": ending_id,
                "message": str(state.get("world_state", {}).get("flags", {}).get("ending_hardcoded_text") or "").strip(),
            }

        return {
            "success": True,
            "micro_scene_id": micro_scene_id,
            "message": str(micro_scene.get("first_enter_text") or micro_scene.get("description") or "").strip(),
        }

    def _find_awaiting_cooperative_handoff(self, state: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        if not isinstance(state, dict):
            return "", {}
        npc_states = state.get("world_state", {}).get("npcs", {})
        if not isinstance(npc_states, dict):
            return "", {}
        for npc_name, npc_state in npc_states.items():
            if not isinstance(npc_state, dict):
                continue
            preset_task = npc_state.get("preset_task", {})
            if not isinstance(preset_task, dict):
                continue
            if (
                str(preset_task.get("kind") or "").strip() == "cooperative_escape"
                and str(preset_task.get("phase") or "").strip() == "await_handoff"
            ):
                return npc_name, npc_state
        return "", {}

    def _render_text_template(self, template: str, **kwargs) -> str:
        rendered = str(template or "")
        for key, value in kwargs.items():
            rendered = rendered.replace("{" + key + "}", str(value or ""))
        return rendered

    def _get_primary_pursuer_message_from_state(
        self,
        state: Dict[str, Any],
        key: str,
        default: str,
        **kwargs,
    ) -> str:
        module_data = (state or {}).get("module_data", {}) if isinstance(state, dict) else {}
        settings = self._get_primary_pursuer_settings(module_data)
        messages = settings.get("messages", {}) if isinstance(settings.get("messages"), dict) else {}
        template = str(messages.get(key) or "").strip() or default
        entity_name = self._get_primary_pursuer_name(module_data)
        return self._render_text_template(template, entity_name=entity_name, **kwargs).strip()

    def _get_primary_pursuer_warning_location(self, module_data: Dict[str, Any]) -> str:
        settings = self._get_primary_pursuer_settings(module_data)
        configured = str(settings.get("warning_location") or "").strip()
        if configured:
            return configured
        pursuer_name = self._get_primary_pursuer_name(module_data)
        all_entities = get_module_all_entities(module_data)
        pursuer_data = all_entities.get(pursuer_name, {}) if pursuer_name else {}
        return str(pursuer_data.get("location") or "").strip()

    def _build_default_influence_dimensions(self, module_data: Dict[str, Any] = None) -> Dict[str, Any]:
        dims_cfg = (
            (module_data or self.default_module_data or {})
            .get("endings", {})
            .get("influence_dimensions", {})
            .get("dimensions", {})
        )
        if not isinstance(dims_cfg, dict) or not dims_cfg:
            return {
                "escape_success": False,
                "ritual_destroyed": False,
                "npc_together": False,
                "truth_revealed": False,
                "butler_gaze": False,
                "san_remaining": 65,
                "rounds_used": 0,
            }
        result = {}
        for dim_name, dim_cfg in dims_cfg.items():
            default_val = dim_cfg.get("default") if isinstance(dim_cfg, dict) else None
            if default_val is None:
                # infer type from name
                if dim_name in {"san_remaining", "rounds_used"}:
                    default_val = 0
                else:
                    default_val = False
            result[dim_name] = default_val
        return result

    def _get_insane_ending_description(self, state: Dict[str, Any]) -> str:
        module_data = (state or {}).get("module_data", {}) if isinstance(state, dict) else {}
        ending_conditions = (
            module_data.get("endings", {}).get("ending_conditions", {})
            if isinstance(module_data.get("endings"), dict)
            else {}
        )
        insane = ending_conditions.get("insane", {}) if isinstance(ending_conditions, dict) else {}
        description = str(insane.get("description") or "").strip()
        if description:
            return description
        return "疯狂结局：你永久疯狂了，彻底迷失在了这个世界，再也无人知晓你的下落。"

    def _ensure_runtime_defaults(self, state: Dict[str, Any], module_data: Dict[str, Any] = None):
        if not isinstance(state, dict):
            return

        module_data = module_data or state.get("module_data") or self.default_module_data
        world_state = state.setdefault("world_state", {})
        npc_states = world_state.setdefault("npcs", {})
        follow_arrival_seen = world_state.setdefault("follow_arrival_seen", {})
        if not isinstance(follow_arrival_seen, dict):
            follow_arrival_seen = {}
            world_state["follow_arrival_seen"] = follow_arrival_seen
        valid_locations = set((module_data.get("locations") or {}).keys()) if isinstance(module_data, dict) else set()
        normalized_follow_seen = {}
        for npc_name, seen_locations in follow_arrival_seen.items():
            if not npc_name:
                continue
            values = seen_locations if isinstance(seen_locations, list) else [seen_locations]
            cleaned = []
            for location_key in values:
                key = str(location_key or "").strip()
                if not key:
                    continue
                if valid_locations and key not in valid_locations:
                    continue
                if key not in cleaned:
                    cleaned.append(key)
            normalized_follow_seen[str(npc_name)] = cleaned
        world_state["follow_arrival_seen"] = normalized_follow_seen
        module_npcs_map = get_module_all_entities(module_data) if isinstance(module_data, dict) else {}
        friendly_npcs = set(get_module_npcs(module_data).keys()) if isinstance(module_data, dict) else set()
        primary_pursuer_name = self._get_primary_pursuer_name(module_data)

        for npc_name, npc_data in module_npcs_map.items():
            runtime_state = npc_states.setdefault(npc_name, {})
            if not isinstance(runtime_state, dict):
                runtime_state = {}
                npc_states[npc_name] = runtime_state
            runtime_state.setdefault("attitude", npc_data.get("initial_attitude", "中立"))
            initial_location = str(
                npc_data.get("location")
                or ((npc_data.get("position") or {}).get("initial_location") if isinstance(npc_data.get("position"), dict) else "")
                or ""
            ).strip()
            if initial_location:
                runtime_state.setdefault("location", initial_location)
            self._sync_single_npc_runtime_state(runtime_state, npc_data)
            if primary_pursuer_name and npc_name == primary_pursuer_name:
                chase_state = runtime_state.setdefault("chase_state", {})
                if not isinstance(chase_state, dict):
                    chase_state = {}
                    runtime_state["chase_state"] = chase_state
                for key, value in self._build_default_butler_chase_state().items():
                    chase_state.setdefault(key, value)

        _mod = state.get("module_data")
        influence = state.setdefault("influence_dimensions", self._build_default_influence_dimensions(_mod))
        for key, value in self._build_default_influence_dimensions(_mod).items():
            influence.setdefault(key, value)
        self._sync_influence_dimensions(state)

    def _sync_influence_dimensions(self, state: Dict[str, Any]):
        if not isinstance(state, dict):
            return

        _mod = state.get("module_data") or {}
        influence = state.setdefault("influence_dimensions", self._build_default_influence_dimensions(_mod))
        for key, value in self._build_default_influence_dimensions(_mod).items():
            influence.setdefault(key, value)

        dim_configs = _mod.get("endings", {}).get("influence_dimensions", {}).get("dimensions", {})
        if not isinstance(dim_configs, dict):
            return

        player = state.setdefault("player", {})
        world_state = state.setdefault("world_state", {})
        flags = world_state.setdefault("flags", {})
        npc_states = world_state.get("npcs", {}) if isinstance(world_state.get("npcs"), dict) else {}
        current_location = str(state.get("current_location") or "").strip()

        for dim_name, dim_cfg in dim_configs.items():
            if not isinstance(dim_cfg, dict):
                continue
            default = dim_cfg.get("default")
            source = str(dim_cfg.get("source") or "").strip()

            if source == "player.san":
                influence[dim_name] = int(player.get("san", 0) or 0)
            elif source == "round_count":
                influence[dim_name] = int(state.get("round_count", 0) or 0)
            elif source == "npc_same_location":
                # 检查是否有同伴NPC在同一位置
                module_npcs = get_module_npcs(_mod)
                together = False
                for npc_name, npc_cfg in module_npcs.items():
                    if not isinstance(npc_cfg, dict) or not isinstance(npc_cfg.get("companion"), dict):
                        continue
                    runtime = npc_states.get(npc_name, {})
                    npc_location = str((runtime or {}).get("location") or npc_cfg.get("location") or "").strip()
                    if current_location and npc_location == current_location:
                        together = True
                        break
                influence[dim_name] = bool(flags.get(dim_name, together))
            elif source.startswith("npc_runtime."):
                # 通用NPC运行时字段读取: npc_runtime.NPC名.字段名
                parts = source.split(".", 2)
                if len(parts) == 3:
                    npc_name, field = parts[1], parts[2]
                    runtime = npc_states.get(npc_name, {})
                    raw = (runtime or {}).get(field, default)
                    try:
                        influence[dim_name] = float(raw) if isinstance(default, (int, float)) else raw
                    except (ValueError, TypeError):
                        influence[dim_name] = default
            elif source.startswith("flags."):
                flag_name = source[6:]
                influence[dim_name] = bool(flags.get(flag_name, default))
            elif isinstance(default, bool):
                # bool 类型：走 equivalent_flags/clues/inventory 解析
                influence[dim_name] = self._resolve_bool_influence_dim(state, dim_name)
            # 无 source 且非 bool：保持 default 或已有值

    def _get_butler_runtime_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_runtime_defaults(state)
        npc_states = state.setdefault("world_state", {}).setdefault("npcs", {})
        pursuer_name = self._get_primary_pursuer_name_from_state(state)
        if not pursuer_name:
            return {}
        return npc_states.setdefault(pursuer_name, {})

    def get_butler_state(self, session_id: str) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state:
            return {}
        return copy.deepcopy(self._get_butler_runtime_state(state))

    def is_butler_active(self, session_id: str) -> bool:
        state = self.sessions.get(session_id)
        if not state:
            return False
        chase_state = self._get_butler_runtime_state(state).get("chase_state", {})
        return bool(chase_state.get("active"))

    def get_butler_location(self, session_id: str) -> str:
        state = self.sessions.get(session_id)
        if not state:
            return ""
        butler_state = self._get_butler_runtime_state(state)
        return str(butler_state.get("location") or "").strip()

    def _get_butler_guard_room_from_state(self, state: Dict[str, Any]) -> str:
        if not isinstance(state, dict):
            return ""
        butler_state = self._get_butler_runtime_state(state)
        chase_state = butler_state.get("chase_state", {})
        if not isinstance(chase_state, dict) or not chase_state.get("active"):
            return ""
        if str(chase_state.get("status") or "").strip() != "blocked":
            return ""
        return str(chase_state.get("blocked_at") or butler_state.get("location") or "").strip()

    def get_butler_guard_room(self, session_id: str) -> str:
        state = self.sessions.get(session_id)
        return self._get_butler_guard_room_from_state(state)

    def _get_butler_contact_location_from_state(self, state: Dict[str, Any]) -> str:
        if not isinstance(state, dict):
            return ""
        butler_state = self._get_butler_runtime_state(state)
        chase_state = butler_state.get("chase_state", {})
        if not isinstance(chase_state, dict) or not chase_state.get("active"):
            return ""
        if str(chase_state.get("status") or "").strip() == "blocked":
            return ""
        return str(butler_state.get("location") or "").strip()

    def get_butler_contact_location(self, session_id: str) -> str:
        state = self.sessions.get(session_id)
        return self._get_butler_contact_location_from_state(state)

    def _classify_butler_door_transition(self, state: Dict[str, Any], current: str, target: str) -> str | None:
        guard_room = self._get_butler_guard_room_from_state(state)
        if not guard_room or not current or not target or current == target:
            return None
        if current == guard_room:
            return "exit_guarded_room"
        if target == guard_room:
            return "enter_guarded_room"
        return None

    def get_butler_chase_context(self, session_id: str) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state:
            return {}
        return self.build_butler_chase_context(state)

    def build_butler_chase_context(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(state, dict):
            return {}

        module_data = (state or {}).get("module_data", {}) if isinstance(state, dict) else {}
        pursuer_settings = self._get_primary_pursuer_settings(module_data)
        pursuer_name = self._get_primary_pursuer_name(module_data)
        butler_state = self._get_butler_runtime_state(state)
        chase_state = butler_state.get("chase_state", {})
        if not isinstance(chase_state, dict):
            chase_state = self._build_default_butler_chase_state()

        current_location = str(state.get("current_location") or "").strip()
        butler_location = str(butler_state.get("location") or "").strip()
        blocked_at = self._get_butler_guard_room_from_state(state)
        contact_location = self._get_butler_contact_location_from_state(state)
        target = chase_state.get("target")

        relation = "unknown"
        if butler_location and current_location:
            if contact_location and contact_location == current_location:
                relation = "same_room"
            elif blocked_at and blocked_at == current_location:
                relation = "blocked_outside_current_room"
            else:
                relation = "separate_rooms"

        return {
            "active": bool(chase_state.get("active")),
            "status": str(chase_state.get("status") or "idle"),
            "target": target,
            "entity_name": pursuer_name or None,
            "butler_location": butler_location or None,
            "entity_location": butler_location or None,
            "contact_location": contact_location or None,
            "player_location": current_location or None,
            "guard_room": blocked_at or None,
            "blocked_at": blocked_at or None,
            "last_target_location": chase_state.get("last_target_location"),
            "same_location_rounds": int(chase_state.get("same_location_rounds", 0) or 0),
            "player_relation": relation,
        }

    def is_player_with_active_butler(self, session_id: str) -> bool:
        state = self.sessions.get(session_id)
        if not state or not self.is_butler_active(session_id):
            return False
        return state.get("current_location") == self.get_butler_contact_location(session_id)

    def should_use_butler_arrival_judgement(self, session_id: str, target_key: str) -> bool:
        state = self.sessions.get(session_id)
        if not state or self.is_butler_active(session_id):
            return False
        if target_key != self.get_butler_location(session_id):
            return False
        warning_location = self._get_primary_pursuer_warning_location(self.get_module_data(session_id))
        return bool(target_key) and target_key == warning_location

    def _resolve_entity_location_from_state(self, state: Dict[str, Any], entity_name: str) -> str:
        if not isinstance(state, dict):
            return ""
        entity_name = str(entity_name or "").strip()
        if not entity_name:
            return ""
        if entity_name == "player":
            return str(state.get("current_location") or "").strip()
        world_npcs = state.get("world_state", {}).get("npcs", {})
        npc_state = world_npcs.get(entity_name, {}) if isinstance(world_npcs, dict) else {}
        return str(npc_state.get("location") or "").strip()

    def _merge_runtime_changes(self, base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
        base = copy.deepcopy(base) if isinstance(base, dict) else {}
        incoming = incoming if isinstance(incoming, dict) else {}
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                base[key] = self._merge_runtime_changes(base[key], value)
            elif isinstance(value, list) and isinstance(base.get(key), list):
                merged = list(base[key])
                for item in value:
                    if item not in merged:
                        merged.append(item)
                base[key] = merged
            else:
                base[key] = copy.deepcopy(value)
        return base

    def _find_shortest_path(
        self,
        module_data: Dict[str, Any],
        start: str,
        destination: str,
        blocked_edges: Set[tuple] = None,
    ) -> List[str]:
        if not start or not destination or start == destination:
            return [start] if start else []
        graph = self._get_adjacency_graph(module_data)
        if start not in graph or destination not in graph:
            return []
        blocked_edges = blocked_edges if isinstance(blocked_edges, set) else set()
        queue = deque([[start]])
        visited = {start}
        while queue:
            path = queue.popleft()
            node = path[-1]
            for neighbor in graph.get(node, []):
                if (node, neighbor) in blocked_edges:
                    continue
                if neighbor in visited:
                    continue
                next_path = path + [neighbor]
                if neighbor == destination:
                    return next_path
                visited.add(neighbor)
                queue.append(next_path)
        return []

    def _advance_companion_tasks(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(state, dict):
            return {}

        module_data = state.get("module_data", {})
        session_id = str(state.get("session_id") or "").strip()
        locked_exits = self._get_locked_exits(session_id) if session_id else set()
        npc_states = state.get("world_state", {}).get("npcs", {})
        module_npcs = get_module_npcs(module_data)
        changes: Dict[str, Any] = {}

        for npc_name, npc_cfg in module_npcs.items():
            npc_runtime = npc_states.get(npc_name, {})
            if not isinstance(npc_runtime, dict):
                continue
            mode = str(npc_runtime.get("companion_mode") or npc_runtime.get("companion_state") or "").strip()
            task = npc_runtime.get("companion_task", {})
            if not isinstance(task, dict):
                task = {}
                npc_runtime["companion_task"] = task

            if mode == "follow":
                target_entity = str(task.get("target_entity") or "player").strip() or "player"
                lag = max(0, int(task.get("lag", 0) or 0))
                target_location = self._resolve_entity_location_from_state(state, target_entity)
                if self._coerce_companion_flag(task.get("awaiting_exit_release")):
                    if target_location and target_location == str(npc_runtime.get("location") or "").strip():
                        task["awaiting_exit_release"] = False
                    else:
                        continue
                desired_location = target_location
                if lag > 0:
                    desired_location = str(task.get("last_target_location") or "").strip()
                    task["last_target_location"] = target_location
                if desired_location and desired_location != npc_runtime.get("location"):
                    npc_runtime["location"] = desired_location
                    changes = self._merge_runtime_changes(changes, {"npc_locations": {npc_name: desired_location}})
                continue

            if mode != "bait":
                continue

            destination = str(task.get("destination") or "").strip()
            target_entity = str(task.get("target_entity") or "").strip()
            current_location = str(npc_runtime.get("location") or "").strip()
            if not destination or destination not in module_data.get("locations", {}):
                continue

            if current_location and current_location != destination:
                path = self._find_shortest_path(module_data, current_location, destination, blocked_edges=locked_exits)
                if len(path) >= 2:
                    next_location = path[1]
                    npc_runtime["location"] = next_location
                    changes = self._merge_runtime_changes(changes, {"npc_locations": {npc_name: next_location}})

            if target_entity:
                primary_pursuer = self._get_primary_pursuer_name_from_state(state)
                if target_entity == primary_pursuer:
                    pursuer_state = self._get_butler_runtime_state(state)
                    chase_state = pursuer_state.setdefault("chase_state", self._build_default_butler_chase_state())
                    chase_state["active"] = True
                    if chase_state.get("status") == "blocked":
                        chase_state["status"] = "pursuing"
                        chase_state["activation_round"] = None
                        chase_state["same_location_rounds"] = 0
                        chase_state.pop("blocked_at", None)
                    else:
                        chase_state["status"] = "alerted"
                    chase_state["target"] = npc_name
                else:
                    target_runtime = npc_states.get(target_entity, {})
                    if isinstance(target_runtime, dict):
                        target_runtime["companion_mode"] = "follow"
                        target_runtime["companion_state"] = "follow"
                        target_runtime["companion_task"] = {
                            "type": "follow",
                            "target_entity": npc_name,
                            "lag": max(0, int(task.get("target_follow_lag", 1) or 1)),
                            "last_target_location": None,
                        }

        return changes

    def _finalize_companion_tasks(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(state, dict):
            return {}

        npc_states = state.get("world_state", {}).get("npcs", {})
        changes: Dict[str, Any] = {}
        primary_pursuer = self._get_primary_pursuer_name_from_state(state)

        for npc_name, npc_runtime in npc_states.items():
            if not isinstance(npc_runtime, dict):
                continue
            if str(npc_runtime.get("companion_mode") or "") != "bait":
                continue
            task = npc_runtime.get("companion_task", {})
            if not isinstance(task, dict):
                continue
            destination = str(task.get("destination") or "").strip()
            target_entity = str(task.get("target_entity") or "").strip()
            if not destination or not target_entity:
                continue

            target_reached = False
            if target_entity == primary_pursuer:
                pursuer_state = self._get_butler_runtime_state(state)
                chase_state = pursuer_state.get("chase_state", {})
                target_reached = (
                    str(pursuer_state.get("location") or "").strip() == destination
                    or str(chase_state.get("blocked_at") or "").strip() == destination
                )
            else:
                target_reached = self._resolve_entity_location_from_state(state, target_entity) == destination

            if not target_reached:
                continue

            npc_runtime["companion_mode"] = str(task.get("on_complete_self") or "wait").strip() or "wait"
            npc_runtime["companion_state"] = npc_runtime["companion_mode"]
            npc_runtime["companion_task"] = {}
            changes = self._merge_runtime_changes(changes, {
                "npc_updates": {
                    npc_name: {
                        "companion_mode": npc_runtime["companion_mode"],
                        "companion_state": npc_runtime["companion_state"],
                        "companion_task": {},
                    }
                }
            })

            target_runtime = npc_states.get(target_entity, {})
            if target_entity != primary_pursuer and isinstance(target_runtime, dict):
                target_runtime["companion_mode"] = str(task.get("on_complete_target") or "wait").strip() or "wait"
                target_runtime["companion_state"] = target_runtime["companion_mode"]
                target_runtime["companion_task"] = {}
                changes = self._merge_runtime_changes(changes, {
                    "npc_updates": {
                        target_entity: {
                            "companion_mode": target_runtime["companion_mode"],
                            "companion_state": target_runtime["companion_state"],
                            "companion_task": {},
                        }
                    }
                })

            if target_entity == primary_pursuer:
                pursuer_state = self._get_butler_runtime_state(state)
                chase_state = pursuer_state.setdefault("chase_state", self._build_default_butler_chase_state())
                chase_state["activation_round"] = None
                chase_state["last_target_location"] = destination
                chase_state["same_location_rounds"] = 0
                destination_data = state.get("module_data", {}).get("locations", {}).get(destination, {})
                if isinstance(destination_data, dict) and destination_data.get("has_door"):
                    pursuer_state["location"] = destination
                    chase_state["active"] = True
                    chase_state["status"] = "blocked"
                    chase_state["target"] = None
                    chase_state["blocked_at"] = destination
                    changes = self._merge_runtime_changes(changes, {
                        "npc_updates": {
                            primary_pursuer: {
                                "location": destination,
                                "chase_state": {
                                    "active": True,
                                    "status": "blocked",
                                    "target": None,
                                    "activation_round": None,
                                    "last_target_location": destination,
                                    "same_location_rounds": 0,
                                    "blocked_at": destination,
                                },
                            }
                        }
                    })
                else:
                    chase_state["active"] = False
                    chase_state["status"] = "waiting"
                    chase_state["target"] = None
                    chase_state["blocked_at"] = None
                    changes = self._merge_runtime_changes(changes, {
                        "npc_updates": {
                            primary_pursuer: {
                                "chase_state": {
                                    "active": False,
                                    "status": "waiting",
                                    "target": None,
                                    "activation_round": None,
                                    "last_target_location": destination,
                                    "same_location_rounds": 0,
                                    "blocked_at": None,
                                }
                            }
                        }
                    })

        return changes

    def start_preset_task(self, session_id: str, npc_name: str, task_id: str) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state:
            return {"success": False, "message": "会话不存在"}

        self._ensure_runtime_defaults(state)
        module_data = self.get_module_data(session_id)
        preset_tasks = self._get_module_preset_tasks(module_data)
        task_cfg = preset_tasks.get(task_id, {})
        if not isinstance(task_cfg, dict):
            return {"success": False, "message": "预设任务不存在"}

        npc_states = state.get("world_state", {}).get("npcs", {})
        npc_state = npc_states.get(npc_name, {}) if isinstance(npc_states, dict) else {}
        if not isinstance(npc_state, dict):
            return {"success": False, "message": f"NPC {npc_name} 不存在"}

        actor_name = str(task_cfg.get("actor") or "").strip()
        if actor_name and actor_name != npc_name:
            return {"success": False, "message": f"{npc_name} 不能执行该预设任务"}

        current_task = npc_state.get("preset_task", {})
        if isinstance(current_task, dict) and current_task.get("task_id"):
            return {"success": False, "message": f"{npc_name} 已在执行其他预设任务"}

        requirements = task_cfg.get("requirements", {}) if isinstance(task_cfg.get("requirements"), dict) else {}
        min_trust = float(requirements.get("min_trust", 0.0) or 0.0)
        if float(npc_state.get("trust_level", 0.0) or 0.0) < min_trust:
            return {"success": False, "message": f"{npc_name} 的信任不足，拒绝执行该计划"}

        actor_location = str(requirements.get("actor_location") or "").strip()
        current_location = str(npc_state.get("location") or "").strip()
        kind = str(task_cfg.get("kind") or "").strip()
        staging_location = str(task_cfg.get("staging_location") or "").strip()
        # decoy 类型有 staging_location 时跳过 actor_location 检查（启动时会自动瞬移）
        if actor_location and current_location != actor_location:
            if not (kind == "decoy" and staging_location):
                return {"success": False, "message": f"{npc_name} 当前不在可执行该任务的位置"}

        player_location_req = str(requirements.get("player_location") or "").strip()
        player_current = str(state.get("current_location") or "").strip()
        if player_location_req and player_current != player_location_req:
            return {"success": False, "message": f"玩家当前不在可执行该任务的位置（需要在 {player_location_req}）"}

        started_round = int(state.get("round_count", 0) or 0)
        preset_task: Dict[str, Any]
        if kind == "solo_search":
            staging_location = str(task_cfg.get("staging_location") or "").strip()
            if staging_location:
                npc_state["location"] = staging_location
            preset_task = {
                "task_id": task_id,
                "kind": kind,
                "status": "running",
                "phase": "scheduled",
                "rounds_left": max(1, int(task_cfg.get("duration_rounds", 1) or 1)),
                "return_to": str(task_cfg.get("return_to") or current_location).strip() or current_location,
                "started_round": started_round,
            }
        elif kind == "cooperative_escape":
            failure_cfg = task_cfg.get("failure", {}) if isinstance(task_cfg.get("failure"), dict) else {}
            preset_task = {
                "task_id": task_id,
                "kind": kind,
                "status": "running",
                "phase": "scheduled",
                "staging_location": str(task_cfg.get("staging_location") or "").strip(),
                "return_to": str(failure_cfg.get("return_to") or current_location).strip() or current_location,
                "started_round": started_round,
            }
        elif kind == "decoy":
            staging_location = str(task_cfg.get("staging_location") or "").strip()
            # NPC 瞬移到 staging_location
            if staging_location:
                npc_state["location"] = staging_location
            npc_state["companion_mode"] = "wait"
            npc_state["companion_state"] = "wait"
            npc_state["companion_task"] = {}
            npc_state["preset_task"] = {}
            # 管家 target 转为 NPC
            primary_pursuer = self._get_primary_pursuer_name_from_state(state)
            decoy_changes: Dict[str, Any] = {}
            if primary_pursuer:
                pursuer_state = self._get_butler_runtime_state(state)
                chase_state = pursuer_state.setdefault("chase_state", self._build_default_butler_chase_state())
                chase_state["active"] = True
                chase_state["status"] = "alerted"
                chase_state["target"] = npc_name
                chase_state["activation_round"] = None
                chase_state["same_location_rounds"] = 0
                chase_state["blocked_at"] = None
                chase_state["last_target_location"] = staging_location or str(pursuer_state.get("location") or "").strip()
                if staging_location:
                    pursuer_state["location"] = staging_location
                decoy_changes = self._merge_runtime_changes(decoy_changes, {
                    "npc_updates": {
                        primary_pursuer: {
                            "location": staging_location,
                            "chase_state": copy.deepcopy(chase_state),
                        }
                    }
                })
            # set_flags
            on_complete_cfg = task_cfg.get("on_complete", {}) if isinstance(task_cfg.get("on_complete"), dict) else {}
            set_flags = on_complete_cfg.get("set_flags", {}) if isinstance(on_complete_cfg.get("set_flags"), dict) else {}
            if set_flags:
                state.setdefault("world_state", {}).setdefault("flags", {}).update(copy.deepcopy(set_flags))
                decoy_changes = self._merge_runtime_changes(decoy_changes, {"flags": copy.deepcopy(set_flags)})
            _note = str(task_cfg.get("on_start_note") or "").strip()
            if _note:
                decoy_changes["movement_note"] = _note
            decoy_changes = self._merge_runtime_changes(decoy_changes, {
                "npc_updates": {
                    npc_name: {
                        "location": npc_state.get("location", ""),
                        "companion_mode": "wait",
                        "companion_state": "wait",
                        "companion_task": {},
                        "preset_task": {},
                    }
                }
            })
            self._append_preset_task_history(
                npc_state,
                {"round": started_round, "task_id": task_id, "event": "started_and_completed"},
            )
            return {
                "success": True,
                "task_id": task_id,
                "preset_task": {},
                "changes": decoy_changes,
            }
        else:
            return {"success": False, "message": "当前预设任务类型尚未实现"}

        npc_state["preset_task"] = preset_task
        self._append_preset_task_history(
            npc_state,
            {
                "round": started_round,
                "task_id": task_id,
                "event": "started",
            },
        )
        return {
            "success": True,
            "task_id": task_id,
            "preset_task": copy.deepcopy(preset_task),
            "changes": {
                "npc_updates": {
                    npc_name: {
                        "location": npc_state.get("location", ""),
                        "preset_task": copy.deepcopy(preset_task),
                    }
                }
            },
        }

    def _advance_preset_tasks(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(state, dict):
            return {}

        module_data = state.get("module_data", {}) if isinstance(state.get("module_data"), dict) else {}
        preset_tasks = self._get_module_preset_tasks(module_data)
        npc_states = state.get("world_state", {}).get("npcs", {})
        flags = state.setdefault("world_state", {}).setdefault("flags", {})
        changes: Dict[str, Any] = {}

        for npc_name, npc_runtime in npc_states.items():
            if not isinstance(npc_runtime, dict):
                continue
            preset_task = npc_runtime.get("preset_task", {})
            if not isinstance(preset_task, dict):
                continue
            task_id = str(preset_task.get("task_id") or "").strip()
            if not task_id:
                continue

            task_cfg = preset_tasks.get(task_id, {}) if isinstance(preset_tasks.get(task_id), dict) else {}
            kind = str(preset_task.get("kind") or task_cfg.get("kind") or "").strip()
            phase = str(preset_task.get("phase") or "").strip()

            if kind == "solo_search":
                if phase not in {"scheduled", "offstage"}:
                    continue

                if phase == "scheduled":
                    preset_task["phase"] = "offstage"
                    npc_runtime["location"] = ""
                    _note = str(task_cfg.get("on_start_note") or "").strip()
                    if _note:
                        changes["movement_note"] = _note

                rounds_left = max(0, int(preset_task.get("rounds_left", 0) or 0) - 1)
                preset_task["rounds_left"] = rounds_left

                if rounds_left > 0:
                    _note = str(task_cfg.get("in_progress_note") or "").strip()
                    if _note and "movement_note" not in changes:
                        changes["movement_note"] = _note
                    changes = self._merge_runtime_changes(
                        changes,
                        {
                            "npc_updates": {
                                npc_name: {
                                    "location": npc_runtime.get("location", ""),
                                    "preset_task": copy.deepcopy(preset_task),
                                }
                            }
                        },
                    )
                    continue

                return_to = str(preset_task.get("return_to") or task_cfg.get("return_to") or "").strip()
                if return_to:
                    npc_runtime["location"] = return_to

                on_complete = task_cfg.get("on_complete", {}) if isinstance(task_cfg.get("on_complete"), dict) else {}
                set_flags = on_complete.get("set_flags", {}) if isinstance(on_complete.get("set_flags"), dict) else {}
                flags.update(copy.deepcopy(set_flags))
                npc_clues = on_complete.get("npc_clues", [])
                if isinstance(npc_clues, list) and npc_clues:
                    existing = npc_runtime.setdefault("memory", {}).setdefault("known_clues", [])
                    for clue in npc_clues:
                        if clue and clue not in existing:
                            existing.append(clue)
                _note = str(task_cfg.get("on_complete_note") or "").strip()
                if _note:
                    changes["movement_note"] = _note
                npc_runtime["preset_task"] = {}
                self._append_preset_task_history(
                    npc_runtime,
                    {
                        "round": int(state.get("round_count", 0) or 0),
                        "task_id": task_id,
                        "event": "completed",
                    },
                )
                changes = self._merge_runtime_changes(
                    changes,
                    {
                        "npc_updates": {
                            npc_name: {
                                "location": npc_runtime.get("location", ""),
                                "preset_task": {},
                            }
                        },
                        "flags": copy.deepcopy(set_flags),
                    },
                )
                continue

            if kind == "cooperative_escape":
                if phase != "scheduled":
                    continue

                staging_location = str(preset_task.get("staging_location") or task_cfg.get("staging_location") or "").strip()
                if staging_location:
                    npc_runtime["location"] = staging_location
                preset_task["phase"] = "await_handoff"
                _note = str(task_cfg.get("on_start_note") or "").strip()
                if _note:
                    changes["movement_note"] = _note
                changes = self._merge_runtime_changes(
                    changes,
                    {
                        "npc_updates": {
                            npc_name: {
                                "location": npc_runtime.get("location", ""),
                                "preset_task": copy.deepcopy(preset_task),
                            }
                        }
                    },
                )

                primary_pursuer = self._get_primary_pursuer_name_from_state(state)
                if primary_pursuer:
                    pursuer_state = self._get_butler_runtime_state(state)
                    chase_state = pursuer_state.setdefault("chase_state", self._build_default_butler_chase_state())
                    chase_state["active"] = True
                    chase_state["status"] = "alerted"
                    chase_state["target"] = npc_name
                    chase_state["activation_round"] = None
                    chase_state["same_location_rounds"] = 0
                    chase_state["blocked_at"] = None
                    chase_state["last_target_location"] = staging_location or str(pursuer_state.get("location") or "").strip()
                    changes = self._merge_runtime_changes(
                        changes,
                        {
                            "npc_updates": {
                                primary_pursuer: {
                                    "chase_state": {
                                        "active": True,
                                        "status": "alerted",
                                        "target": npc_name,
                                        "activation_round": None,
                                        "same_location_rounds": 0,
                                        "blocked_at": None,
                                        "last_target_location": chase_state.get("last_target_location"),
                                    }
                                }
                            }
                        },
                    )

        return changes

    def _resolve_preset_task_branch_state(self, state: Dict[str, Any], npc_name: str, branch: str) -> Dict[str, Any]:
        if not isinstance(state, dict):
            return {}

        module_data = state.get("module_data", {}) if isinstance(state.get("module_data"), dict) else {}
        module_entities = get_module_all_entities(module_data)
        preset_tasks = self._get_module_preset_tasks(module_data)
        npc_states = state.get("world_state", {}).get("npcs", {})
        npc_runtime = npc_states.get(npc_name, {}) if isinstance(npc_states, dict) else {}
        if not isinstance(npc_runtime, dict):
            return {}

        preset_task = npc_runtime.get("preset_task", {})
        if not isinstance(preset_task, dict):
            return {}
        task_id = str(preset_task.get("task_id") or "").strip()
        task_cfg = preset_tasks.get(task_id, {}) if isinstance(preset_tasks.get(task_id), dict) else {}
        if str(preset_task.get("kind") or task_cfg.get("kind") or "").strip() != "cooperative_escape":
            return {}

        changes: Dict[str, Any] = {}
        current_round = int(state.get("round_count", 0) or 0)
        if branch == "player_handoff_success":
            success_cfg = task_cfg.get("success", {}) if isinstance(task_cfg.get("success"), dict) else {}
            npc_runtime["location"] = str(state.get("current_location") or npc_runtime.get("location") or "").strip()
            npc_runtime["companion_mode"] = str(success_cfg.get("npc_mode") or "follow").strip() or "follow"
            npc_runtime["companion_state"] = npc_runtime["companion_mode"]
            npc_runtime["companion_task"] = {
                "type": "follow",
                "target_entity": str(success_cfg.get("handoff_target") or "player").strip() or "player",
                "lag": 0,
                "last_target_location": None,
                "awaiting_exit_release": False,
            }
            npc_runtime["preset_task"] = {}
            self._append_preset_task_history(
                npc_runtime,
                {
                    "round": current_round,
                    "task_id": task_id,
                    "event": "branch_player_handoff_success",
                },
            )

            primary_pursuer = self._get_primary_pursuer_name_from_state(state)
            if primary_pursuer:
                pursuer_state = self._get_butler_runtime_state(state)
                chase_state = pursuer_state.setdefault("chase_state", self._build_default_butler_chase_state())
                chase_state["active"] = True
                chase_state["status"] = "pursuing"
                chase_state["target"] = "player"
                chase_state["activation_round"] = current_round
                chase_state["same_location_rounds"] = 0
                chase_state["blocked_at"] = None
                chase_state["last_target_location"] = str(state.get("current_location") or "").strip()
                changes = self._merge_runtime_changes(
                    changes,
                    {
                        "npc_updates": {
                            primary_pursuer: {
                                "chase_state": {
                                    "active": True,
                                    "status": "pursuing",
                                    "target": "player",
                                    "activation_round": current_round,
                                    "same_location_rounds": 0,
                                    "blocked_at": None,
                                    "last_target_location": chase_state["last_target_location"],
                                }
                            }
                        }
                    },
                )

            self._sync_single_npc_runtime_state(npc_runtime, module_entities.get(npc_name, {}))
            _note = str(success_cfg.get("movement_note") or "").strip()
            if _note:
                changes["movement_note"] = _note
            changes = self._merge_runtime_changes(
                changes,
                {
                    "npc_updates": {
                        npc_name: {
                            "location": npc_runtime.get("location", ""),
                            "companion_mode": npc_runtime["companion_mode"],
                            "companion_state": npc_runtime["companion_state"],
                            "companion_task": copy.deepcopy(npc_runtime["companion_task"]),
                            "preset_task": {},
                        }
                    }
                },
            )
            return changes

        if branch == "player_abandoned":
            failure_cfg = task_cfg.get("failure", {}) if isinstance(task_cfg.get("failure"), dict) else {}
            return_to = str(failure_cfg.get("return_to") or preset_task.get("return_to") or "").strip()
            if return_to:
                npc_runtime["location"] = return_to
            npc_runtime["companion_mode"] = "wait"
            npc_runtime["companion_state"] = "wait"
            npc_runtime["companion_task"] = {}
            if "trust_set" in failure_cfg:
                npc_runtime["trust_level"] = round(float(failure_cfg.get("trust_set", 0.0) or 0.0), 2)
            npc_runtime["preset_task"] = {}
            set_flags = failure_cfg.get("set_flags", {}) if isinstance(failure_cfg.get("set_flags"), dict) else {}
            state.setdefault("world_state", {}).setdefault("flags", {}).update(copy.deepcopy(set_flags))
            _note = str(failure_cfg.get("movement_note") or "").strip()
            if _note:
                changes["movement_note"] = _note
            self._append_preset_task_history(
                npc_runtime,
                {
                    "round": current_round,
                    "task_id": task_id,
                    "event": "branch_player_abandoned",
                },
            )
            self._sync_single_npc_runtime_state(npc_runtime, module_entities.get(npc_name, {}))
            changes = self._merge_runtime_changes(
                changes,
                {
                    "npc_updates": {
                        npc_name: {
                            "location": npc_runtime.get("location", ""),
                            "trust_level": npc_runtime.get("trust_level", 0.0),
                            "companion_mode": "wait",
                            "companion_state": "wait",
                            "companion_task": {},
                            "preset_task": {},
                        }
                    },
                    "flags": copy.deepcopy(set_flags),
                },
            )
            return changes

        return {}

    def resolve_preset_task_branch(self, session_id: str, npc_name: str, branch: str) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state:
            return {"success": False, "message": "会话不存在"}
        changes = self._resolve_preset_task_branch_state(state, npc_name, branch)
        return {
            "success": bool(changes),
            "branch": branch,
            "changes": changes,
        }

    def deliver_pending_npc_reports(self, session_id: str, npc_name: str) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state:
            return {"delivered": False}

        self._ensure_runtime_defaults(state)
        module_data = self.get_module_data(session_id)
        preset_tasks = self._get_module_preset_tasks(module_data)
        world_state = state.setdefault("world_state", {})
        flags = world_state.setdefault("flags", {})
        clues_found = world_state.setdefault("clues_found", [])
        npc_states = world_state.setdefault("npcs", {})

        for task_id, task_cfg in preset_tasks.items():
            if not isinstance(task_cfg, dict):
                continue
            if str(task_cfg.get("actor") or "").strip() != str(npc_name or "").strip():
                continue
            report_cfg = task_cfg.get("report", {}) if isinstance(task_cfg.get("report"), dict) else {}
            pending_flag = str(report_cfg.get("pending_flag") or "").strip()
            delivered_flag = str(report_cfg.get("delivered_flag") or "").strip()
            if not pending_flag or not flags.get(pending_flag):
                continue

            clue_raw = report_cfg.get("clue") or ""
            clues = clue_raw if isinstance(clue_raw, list) else [str(clue_raw).strip()] if clue_raw else []
            text = str(report_cfg.get("text") or "").strip()
            for clue in clues:
                if clue and clue not in clues_found:
                    clues_found.append(clue)
            flags[pending_flag] = False
            if delivered_flag:
                flags[delivered_flag] = True
            npc_clues = report_cfg.get("npc_clues_on_deliver", [])
            if isinstance(npc_clues, list) and npc_clues:
                npc_rt = npc_states.get(npc_name, {})
                existing = npc_rt.setdefault("memory", {}).setdefault("known_clues", [])
                for c in npc_clues:
                    if c and c not in existing:
                        existing.append(c)
            return {
                "delivered": True,
                "task_id": task_id,
                "npc_name": npc_name,
                "clue": clues[0] if len(clues) == 1 else None,
                "clues": copy.deepcopy(clues),
                "text": text,
            }

        return {"delivered": False}

    # ── 同伴状态管理 ──

    def _coerce_companion_flag(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes"}

    def _should_hold_remote_follow(
        self,
        module_data: Dict[str, Any],
        npc_module: Dict[str, Any],
        companion_cfg: Dict[str, Any],
        npc_state: Dict[str, Any],
        target_location: str,
        explicit_exit: bool,
    ) -> bool:
        if explicit_exit or not target_location:
            return False
        npc_location = str(npc_state.get("location") or npc_module.get("location") or "").strip()
        if not npc_location or npc_location == target_location:
            return False
        location_data = module_data.get("locations", {}).get(npc_location, {})
        if not isinstance(location_data, dict) or not location_data.get("has_door"):
            return False
        if self._coerce_companion_flag(companion_cfg.get("require_explicit_exit")):
            return True
        return bool(
            isinstance(npc_module.get("dialogue"), dict)
            or bool(get_entity_dialogue_guide(npc_module))
            or bool(get_entity_first_appearance(npc_module))
        )

    def set_companion_state(self, session_id: str, npc_name: str, target_state: str, command_payload: Dict[str, Any] = None) -> Dict[str, Any]:
        """设置 NPC 的硬行为模式，并在需要时写入任务参数。"""
        state = self.sessions.get(session_id)
        if not state:
            return {"success": False, "message": "会话不存在"}

        npc_state = state["world_state"].get("npcs", {}).get(npc_name)
        if not npc_state or not isinstance(npc_state, dict):
            return {"success": False, "message": f"NPC {npc_name} 不存在"}

        module_data = self.get_module_data(session_id)
        npc_module = get_module_npcs(module_data).get(npc_name) or {}
        companion_cfg = npc_module.get("companion") if isinstance(npc_module.get("companion"), dict) else None
        if not companion_cfg:
            return {"success": False, "message": f"{npc_name} 不是可同伴化的NPC"}

        allowed_modes = companion_cfg.get("enabled_modes", [])
        if target_state not in allowed_modes:
            return {"success": False, "message": f"{npc_name} 不支持{target_state}模式"}

        unlock_trust = float(companion_cfg.get("unlock_trust", 0.5) or 0.5)
        trust_level = float(npc_state.get("trust_level", 0.0))
        if trust_level < unlock_trust:
            return {"success": False, "message": f"{npc_name}的信任不足，拒绝了你的请求"}

        payload = command_payload if isinstance(command_payload, dict) else {}
        task = {}
        if target_state == "follow":
            follow_target = str(
                payload.get("follow_target")
                or payload.get("target_entity")
                or "player"
            ).strip() or "player"
            lag = max(0, int(payload.get("lag", 0) or 0))
            explicit_exit = self._coerce_companion_flag(payload.get("explicit_exit"))
            current_target_location = self._resolve_entity_location_from_state(state, follow_target)
            awaiting_exit_release = self._should_hold_remote_follow(
                module_data,
                npc_module,
                companion_cfg,
                npc_state,
                str(current_target_location or "").strip(),
                explicit_exit,
            )
            task = {
                "type": "follow",
                "target_entity": follow_target,
                "lag": lag,
                "last_target_location": None,
                "awaiting_exit_release": awaiting_exit_release,
            }
            if current_target_location and lag == 0 and not awaiting_exit_release:
                npc_state["location"] = current_target_location
        elif target_state == "bait":
            target_entity = str(
                payload.get("target_entity")
                or self._get_primary_pursuer_name(module_data)
                or ""
            ).strip()
            destination = str(
                payload.get("destination")
                or payload.get("target_room")
                or self._resolve_entity_location_from_state(state, npc_name)
                or ""
            ).strip()
            if not target_entity:
                return {"success": False, "message": "诱饵任务缺少 target_entity"}
            if not destination:
                return {"success": False, "message": "诱饵任务缺少 destination"}
            if destination not in module_data.get("locations", {}):
                return {"success": False, "message": "诱饵任务目标地点无效"}
            task = {
                "type": "bait",
                "target_entity": target_entity,
                "destination": destination,
                "target_follow_lag": max(0, int(payload.get("target_follow_lag", 1) or 1)),
                "complete_when": "target_reaches_destination",
                "on_complete_self": str(payload.get("on_complete_self") or "wait").strip() or "wait",
                "on_complete_target": str(payload.get("on_complete_target") or "wait").strip() or "wait",
            }

        npc_state["companion_mode"] = target_state
        npc_state["companion_state"] = target_state
        npc_state["companion_task"] = task

        return {
            "success": True,
            "message": f"{npc_name}状态变更为{target_state}",
            "new_state": target_state,
            "task": copy.deepcopy(task),
        }

    def get_companion_state(self, session_id: str, npc_name: str) -> str:
        """获取NPC同伴状态。"""
        state = self.sessions.get(session_id)
        if not state:
            return "wait"
        npc_state = state["world_state"].get("npcs", {}).get(npc_name, {})
        return str(npc_state.get("companion_mode") or npc_state.get("companion_state") or "wait")

    def get_following_companions(self, session_id: str) -> list:
        """获取所有处于follow状态的NPC名列表。"""
        state = self.sessions.get(session_id)
        if not state:
            return []
        companions = []
        for npc_name, npc_data in state["world_state"].get("npcs", {}).items():
            task = npc_data.get("companion_task", {}) if isinstance(npc_data, dict) else {}
            if (
                isinstance(npc_data, dict)
                and str(npc_data.get("companion_mode") or npc_data.get("companion_state") or "") == "follow"
                and str(task.get("target_entity") or "player").strip() == "player"
                and not self._coerce_companion_flag(task.get("awaiting_exit_release"))
            ):
                companions.append(npc_name)
        return companions

    def _is_player_follow_companion(self, npc_state: Dict[str, Any]) -> bool:
        if not isinstance(npc_state, dict):
            return False
        task = npc_state.get("companion_task", {}) if isinstance(npc_state.get("companion_task"), dict) else {}
        return (
            str(npc_state.get("companion_mode") or npc_state.get("companion_state") or "").strip() == "follow"
            and str(task.get("target_entity") or "player").strip() == "player"
            and not self._coerce_companion_flag(task.get("awaiting_exit_release"))
        )

    def get_follow_companions_at_location(self, session_id: str, location_key: str) -> List[str]:
        state = self.sessions.get(session_id)
        if not state:
            return []
        target_location = str(location_key or "").strip()
        if not target_location:
            return []

        npc_states = state.get("world_state", {}).get("npcs", {})
        companions = []
        for npc_name in get_module_npcs(self.get_module_data(session_id)).keys():
            npc_state = npc_states.get(npc_name, {})
            npc_location = str((npc_state or {}).get("location") or "").strip()
            if npc_location != target_location:
                continue
            if self._is_player_follow_companion(npc_state):
                companions.append(npc_name)
        return companions

    def has_non_follow_present_npc(self, session_id: str, location_key: str) -> bool:
        state = self.sessions.get(session_id)
        if not state:
            return False
        target_location = str(location_key or "").strip()
        if not target_location:
            return False

        module_npcs = get_module_npcs(self.get_module_data(session_id))
        npc_states = state.get("world_state", {}).get("npcs", {})
        for npc_name, npc_module in module_npcs.items():
            npc_state = npc_states.get(npc_name, {})
            npc_location = str(
                (npc_state or {}).get("location")
                or (npc_module.get("location") if isinstance(npc_module, dict) else "")
                or ""
            ).strip()
            if npc_location != target_location:
                continue
            if not self._is_player_follow_companion(npc_state):
                return True
        return False

    def _normalize_npc_reaction_entry(self, entry: Any, default_field: str) -> Dict[str, Any]:
        if isinstance(entry, dict):
            return copy.deepcopy(entry)
        text = str(entry or "").strip()
        if not text:
            return {}
        return {default_field: text}

    def get_follow_arrival_reaction_context(self, session_id: str, location_key: str) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state:
            return {}

        module_data = self.get_module_data(session_id)
        locations = module_data.get("locations", {}) if isinstance(module_data, dict) else {}
        objects = module_data.get("objects", {}) if isinstance(module_data, dict) else {}
        target_location = str(location_key or "").strip()
        location_data = locations.get(target_location, {})
        if not isinstance(location_data, dict):
            return {}

        self._ensure_runtime_defaults(state, module_data)
        follow_arrival_seen = state.get("world_state", {}).get("follow_arrival_seen", {})
        if not isinstance(follow_arrival_seen, dict):
            follow_arrival_seen = {}

        payload = {
            "location_key": target_location,
            "location_name": str(location_data.get("name") or target_location).strip() or target_location,
            "triggered_npcs": [],
            "npcs": {},
        }

        location_reactions = location_data.get("npc_reactions", {})
        if not isinstance(location_reactions, dict):
            location_reactions = {}

        for npc_name in self.get_follow_companions_at_location(session_id, target_location):
            seen_locations = follow_arrival_seen.get(npc_name, [])
            if isinstance(seen_locations, list) and target_location in seen_locations:
                continue

            npc_payload = {}
            location_entry = self._normalize_npc_reaction_entry(location_reactions.get(npc_name), "follow_arrival")
            if location_entry:
                npc_payload["location"] = location_entry

            object_payload = {}
            for object_name in location_data.get("objects", []):
                object_data = objects.get(object_name, {})
                if not isinstance(object_data, dict):
                    continue
                object_reactions = object_data.get("npc_reactions", {})
                if not isinstance(object_reactions, dict):
                    continue
                object_entry = self._normalize_npc_reaction_entry(object_reactions.get(npc_name), "comment")
                if object_entry:
                    object_payload[object_name] = object_entry

            if not location_entry and not object_payload:
                continue

            payload["triggered_npcs"].append(npc_name)
            if object_payload:
                npc_payload["objects"] = object_payload
            payload["npcs"][npc_name] = npc_payload

        return payload if payload["triggered_npcs"] else {}

    def mark_follow_arrival_reactions_seen(self, session_id: str, reaction_context: Dict[str, Any]):
        state = self.sessions.get(session_id)
        if not state or not isinstance(reaction_context, dict):
            return

        target_location = str(reaction_context.get("location_key") or "").strip()
        triggered_npcs = reaction_context.get("triggered_npcs", [])
        if not target_location or not isinstance(triggered_npcs, list):
            return

        self._ensure_runtime_defaults(state)
        follow_arrival_seen = state.get("world_state", {}).setdefault("follow_arrival_seen", {})
        for npc_name in triggered_npcs:
            name = str(npc_name or "").strip()
            if not name:
                continue
            seen_locations = follow_arrival_seen.setdefault(name, [])
            if not isinstance(seen_locations, list):
                seen_locations = []
                follow_arrival_seen[name] = seen_locations
            if target_location not in seen_locations:
                seen_locations.append(target_location)

    def execute_bait_action(self, session_id: str, bait_entity: str, target_room: str = None) -> Dict[str, Any]:
        """兼容旧调用：默认将主要追逐威胁引到指定房间。"""
        primary_pursuer = self._get_primary_pursuer_name(self.get_module_data(session_id))
        if not primary_pursuer:
            return {"success": False, "message": "当前模组没有主要追逐威胁"}
        return self.set_companion_state(
            session_id,
            bait_entity,
            "bait",
            {
                "target_entity": primary_pursuer,
                "destination": target_room,
                "target_follow_lag": 1,
            },
        )

    def block_butler_with_current_room_door(self, session_id: str) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state:
            return {"success": False, "message": "会话不存在"}
        if not self.is_butler_active(session_id):
            return {
                "success": False,
                "message": self._get_primary_pursuer_message_from_state(
                    state,
                    "not_pursuing",
                    "{entity_name}当前没有在追逐你",
                ),
            }

        module_data = self.get_module_data(session_id)
        current_location = state.get("current_location")
        room_data = module_data.get("locations", {}).get(current_location, {})
        if not isinstance(room_data, dict) or not room_data.get("has_door"):
            return {"success": False, "message": "这里没有门可以关上"}

        butler_state = self._get_butler_runtime_state(state)
        chase_state = butler_state.setdefault("chase_state", self._build_default_butler_chase_state())
        if not isinstance(chase_state, dict):
            chase_state = self._build_default_butler_chase_state()
            butler_state["chase_state"] = chase_state

        butler_state["location"] = current_location
        chase_state["active"] = True
        chase_state["status"] = "blocked"
        chase_state["target"] = "player"
        chase_state["blocked_at"] = current_location
        chase_state["last_target_location"] = current_location
        chase_state["same_location_rounds"] = 0
        self._sync_influence_dimensions(state)
        return {
            "success": True,
            "blocked_at": current_location,
            "message": self._get_primary_pursuer_message_from_state(
                state,
                "door_blocked_success",
                "你及时关上了门，暂时把{entity_name}拦在了外面。",
            ),
        }

    def unblock_butler(self, session_id: str, new_target: str = "player"):
        """当新活物进入主要追逐威胁视野时，解除其 blocked 状态。"""
        state = self.sessions.get(session_id)
        if not state:
            return
        butler_state = self._get_butler_runtime_state(state)
        chase_state = butler_state.get("chase_state", {})
        if chase_state.get("status") == "blocked":
            chase_state["status"] = "pursuing"
            chase_state["target"] = new_target
            chase_state["activation_round"] = None
            chase_state["same_location_rounds"] = 0
            chase_state.pop("blocked_at", None)

    def has_butler_living_room_warning(self, session_id: str) -> bool:
        state = self.sessions.get(session_id)
        if not state:
            return False
        flags = state.get("world_state", {}).get("flags", {})
        return bool(flags.get("butler_living_room_warning_shown"))

    def should_activate_butler_on_entry(self, session_id: str, target_key: str) -> bool:
        state = self.sessions.get(session_id)
        if not state or self.is_butler_active(session_id):
            return False
        return bool(target_key) and target_key == self.get_butler_location(session_id)

    def should_activate_butler_for_action(self, session_id: str, player_input: str) -> bool:
        state = self.sessions.get(session_id)
        if not state or self.is_butler_active(session_id):
            return False
        if state.get("current_location") != self.get_butler_location(session_id):
            return False

        text = str(player_input or "").strip()
        if not text:
            return False

        direct_keywords = [
            "深入客厅",
            "进入客厅深处",
            "往里走",
            "继续深入",
            "靠近",
            "接近",
            "逼近",
            "走近",
            "上前",
            "往前",
            "向前",
            "深入",
        ]
        lowered = text.lower()
        if any(keyword in text for keyword in direct_keywords):
            return True
        return any(keyword in lowered for keyword in ["move closer", "approach", "go deeper", "step forward"])

    def build_butler_activation_changes(self, session_id: str, reason: str) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state or self.is_butler_active(session_id):
            return {}

        pursuer_name = self._get_primary_pursuer_name_from_state(state)
        if not pursuer_name:
            return {}
        butler_location = self.get_butler_location(session_id)
        current_location = state.get("current_location")
        return {
            "flags": {
                "butler_activated": True,
                "butler_activation_reason": reason,
            },
            "npc_updates": {
                pursuer_name: {
                    "location": butler_location or current_location,
                    "chase_state": {
                        "active": True,
                        "status": "alerted",
                        "target": "player",
                        "activation_round": None,
                        "last_target_location": current_location,
                        "same_location_rounds": 0,
                        "blocked_at": None,
                    },
                }
            },
        }

    def _get_check_threshold(self, player_skill: int, difficulty: str) -> int:
        difficulty = str(difficulty or "").strip()
        if difficulty == "困难":
            return max(1, player_skill // 2)
        if difficulty == "极难":
            return max(1, player_skill // 5)
        return max(1, player_skill)

    def _roll_skill_check(self, player_state: Dict[str, Any], skill_name: str, difficulty: str = "普通") -> Dict[str, Any]:
        player_skill = int(player_state.get("skills", {}).get(skill_name, 0) or 0)
        threshold = self._get_check_threshold(player_skill, difficulty)
        roll = random.randint(1, 100)
        success = roll <= threshold
        return {
            "check_type": "skill_check",
            "skill": skill_name,
            "difficulty": difficulty,
            "player_skill": player_skill,
            "threshold": threshold,
            "roll": roll,
            "success": success,
            "critical_success": roll <= 5,
            "critical_failure": roll >= 96,
            "result_description": "成功" if success else "失败",
        }

    def _capture_player_by_butler_state(self, state: Dict[str, Any], reason: str):
        if not isinstance(state, dict):
            return
        influence = state.setdefault("influence_dimensions", self._build_default_influence_dimensions())
        influence["butler_gaze"] = True
        world_flags = state.setdefault("world_state", {}).setdefault("flags", {})
        world_flags["butler_capture_reason"] = reason
        # Use two-phase ending: trigger first, conclude after LLM generates ending
        hardcoded_text = self._get_ending_hardcoded_text(state, "insane", "butler_gaze")
        self._trigger_ending_state(state, "insane", hardcoded_text)
        self._sync_influence_dimensions(state)

    def capture_player_by_butler(self, session_id: str, reason: str) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state:
            return {}
        self._capture_player_by_butler_state(state, reason)
        state["round_count"] = int(state.get("round_count", 0) or 0) + 1
        self._sync_influence_dimensions(state)
        return copy.deepcopy(state)

    def is_game_over(self, session_id: str) -> bool:
        state = self.sessions.get(session_id)
        if not state:
            return False
        flags = state.get("world_state", {}).get("flags", {})
        return bool(flags.get("game_over"))

    def get_game_over_message(self, session_id: str) -> str:
        state = self.sessions.get(session_id)
        if not state:
            return "结局已触发。"
        flags = state.get("world_state", {}).get("flags", {})
        return str(flags.get("ending_text") or self._get_insane_ending_description(state)).strip()

    # ─── Two-phase ending system ───

    def _get_ending_hardcoded_text(self, state: Dict[str, Any], ending_id: str, sub_type: str = "") -> str:
        """Get hardcoded ending text from module data."""
        module_data = (state or {}).get("module_data", {}) if isinstance(state, dict) else {}
        ending_conditions = (
            module_data.get("endings", {}).get("ending_conditions", {})
            if isinstance(module_data.get("endings"), dict)
            else {}
        )
        ending = ending_conditions.get(ending_id, {}) if isinstance(ending_conditions, dict) else {}

        # Check for sub-type specific text (e.g., insane has butler_gaze vs san_zero)
        if sub_type:
            sub_text = str(ending.get(f"hardcoded_text_{sub_type}") or "").strip()
            if sub_text:
                return sub_text

        # Check for general hardcoded_text
        text = str(ending.get("hardcoded_text") or "").strip()
        if text:
            return text

        # Fallback to description
        return str(ending.get("description") or "结局已触发。").strip()

    def _trigger_ending_state(self, state: Dict[str, Any], ending_id: str, hardcoded_text: str):
        """Phase 1: Set ending_phase to 'triggered'. Game is NOT over yet - allows one more LLM call."""
        if not isinstance(state, dict):
            return
        world_flags = state.setdefault("world_state", {}).setdefault("flags", {})
        world_flags["ending_phase"] = "triggered"
        world_flags["ending_id"] = ending_id
        world_flags["ending_hardcoded_text"] = hardcoded_text
        # Do NOT set game_over here - that happens in conclude

    def trigger_ending(self, session_id: str, ending_id: str, sub_type: str = "") -> str:
        """Trigger an ending (phase 1). Returns the hardcoded text to display."""
        state = self.sessions.get(session_id)
        if not state:
            return "结局已触发。"
        hardcoded_text = self._get_ending_hardcoded_text(state, ending_id, sub_type)
        self._trigger_ending_state(state, ending_id, hardcoded_text)
        self._sync_influence_dimensions(state)
        return hardcoded_text

    def conclude_ending(self, session_id: str):
        """Phase 2: Conclude the ending. Sets game_over = True."""
        state = self.sessions.get(session_id)
        if not state:
            return
        world_flags = state.setdefault("world_state", {}).setdefault("flags", {})
        world_flags["ending_phase"] = "concluded"
        world_flags["game_over"] = True

    def allows_epilogue(self, session_id: str) -> bool:
        """Check if current ending allows post-ending free chat (epilogue)."""
        state = self.sessions.get(session_id)
        if not state:
            return False
        flags = state.get("world_state", {}).get("flags", {})
        ending_id = flags.get("ending_id", "")
        if not ending_id or flags.get("ending_phase") != "concluded":
            return False
        module_data = state.get("module_data", {})
        ending_data = module_data.get("endings", {}).get("ending_conditions", {}).get(ending_id, {})
        return bool(ending_data.get("allow_epilogue"))

    def is_ending_triggered(self, session_id: str) -> bool:
        """Check if an ending has been triggered but not yet concluded."""
        state = self.sessions.get(session_id)
        if not state:
            return False
        flags = state.get("world_state", {}).get("flags", {})
        return flags.get("ending_phase") == "triggered"

    def get_ending_phase(self, session_id: str) -> str:
        """Get current ending phase: None, 'triggered', or 'concluded'."""
        state = self.sessions.get(session_id)
        if not state:
            return None
        flags = state.get("world_state", {}).get("flags", {})
        return flags.get("ending_phase") or None

    def get_ending_id(self, session_id: str) -> str:
        """Get the current ending type identifier."""
        state = self.sessions.get(session_id)
        if not state:
            return None
        flags = state.get("world_state", {}).get("flags", {})
        return flags.get("ending_id") or None

    def get_ending_display(self, session_id: str) -> dict:
        """Get display_name and overlay_text for the current ending from module config."""
        state = self.sessions.get(session_id)
        if not state:
            return {}
        ending_id = (state.get("world_state", {}).get("flags", {}) or {}).get("ending_id", "")
        if not ending_id:
            return {}
        module_data = state.get("module_data", {})
        ending_data = module_data.get("endings", {}).get("ending_conditions", {}).get(ending_id, {})
        return {
            "display_name": ending_data.get("display_name", "结局"),
            "overlay_text": ending_data.get("overlay_text", "你的冒险到此结束了。"),
            "allow_epilogue": bool(ending_data.get("allow_epilogue")),
        }

    def get_ending_context(self, session_id: str) -> Dict[str, Any]:
        """Build context for NarrativeAI ending generation."""
        state = self.sessions.get(session_id)
        if not state:
            return {}
        module_data = state.get("module_data", {})
        flags = state.get("world_state", {}).get("flags", {})
        ending_id = flags.get("ending_id", "")
        endings = module_data.get("endings", {})
        ending_conditions = endings.get("ending_conditions", {})
        ending_data = ending_conditions.get(ending_id, {})
        influence_dims = endings.get("influence_dimensions", {}).get("dimensions", {})

        # 艾米莉同行信息（供逃脱结局后日谈使用）
        npc_states = state.get("world_state", {}).get("npcs", {})
        emily_state = npc_states.get("艾米莉", {}) if isinstance(npc_states, dict) else {}
        emily_info = {
            "trust_level": float(emily_state.get("trust_level", 0) or 0),
            "companion_mode": str(emily_state.get("companion_mode") or ""),
            "location": str(emily_state.get("location") or ""),
            "together": (
                str(emily_state.get("location") or "") == str(state.get("current_location") or "")
                and str(emily_state.get("companion_mode") or "") == "follow"
            ),
        }

        return {
            "ending_id": ending_id,
            "ending_description": ending_data.get("description", ""),
            "hardcoded_text": flags.get("ending_hardcoded_text", ""),
            "influence_dimensions": state.get("influence_dimensions", {}),
            "influence_descriptions": influence_dims,
            "player_state": {
                "san": state.get("player", {}).get("san", 0),
                "hp": state.get("player", {}).get("hp", 0),
                "inventory": state.get("player", {}).get("inventory", []),
            },
            "emily_info": emily_info,
            "clues_found": state.get("world_state", {}).get("clues_found", []),
            "round_count": state.get("round_count", 0),
        }

    def _normalize_ending_rule_list(self, values) -> List[str]:
        if not isinstance(values, list):
            return []
        return [str(item or "").strip() for item in values if str(item or "").strip()]

    def _get_ending_validation_config(self, state: Dict[str, Any], ending_id: str) -> Dict[str, Any]:
        if not isinstance(state, dict) or not ending_id:
            return {}
        module_data = state.get("module_data", {})
        endings = module_data.get("endings", {}) if isinstance(module_data.get("endings"), dict) else {}
        ending_conditions = endings.get("ending_conditions", {}) if isinstance(endings.get("ending_conditions"), dict) else {}
        ending_data = ending_conditions.get(ending_id, {}) if isinstance(ending_conditions.get(ending_id), dict) else {}
        validation = ending_data.get("validation", {})
        return validation if isinstance(validation, dict) else {}

    def ending_requires_ai_request(self, session_id: str, ending_id: str) -> bool:
        state = self.sessions.get(session_id)
        if not state:
            return False
        validation = self._get_ending_validation_config(state, ending_id)
        return bool(validation.get("require_ai_request"))

    def _resolve_bool_influence_dim(self, state: Dict[str, Any], dim_name: str) -> bool:
        """Read equivalent_flags/clues/inventory from module config to resolve a bool influence dimension."""
        if not isinstance(state, dict):
            return False
        module_data = state.get("module_data") or self.default_module_data or {}
        dim_cfg = (
            module_data.get("endings", {})
            .get("influence_dimensions", {})
            .get("dimensions", {})
            .get(dim_name, {})
        )
        world_state = state.get("world_state", {})
        flags = world_state.get("flags", {}) if isinstance(world_state.get("flags"), dict) else {}
        clues = world_state.get("clues_found", []) if isinstance(world_state.get("clues_found"), list) else []
        inventory = state.get("player", {}).get("inventory", []) if isinstance(state.get("player", {}).get("inventory"), list) else []
        influence = state.get("influence_dimensions", {})
        if bool(influence.get(dim_name)):
            return True
        if bool(flags.get(dim_name)):
            return True
        if isinstance(dim_cfg, dict):
            for alias in (dim_cfg.get("equivalent_flags") or []):
                if flags.get(alias):
                    return True
            for alias in (dim_cfg.get("equivalent_clues") or []):
                if alias in clues:
                    return True
            for alias in (dim_cfg.get("equivalent_inventory") or []):
                if alias in inventory:
                    return True
        return False

    def _is_effective_ritual_destroyed(self, state: Dict[str, Any]) -> bool:
        return self._resolve_bool_influence_dim(state, "ritual_destroyed")


    def _get_effective_npc_together(self, state: Dict[str, Any]) -> bool:
        if not isinstance(state, dict):
            return False
        current_location = str(state.get("current_location") or "").strip()
        module_npcs = get_module_npcs(state.get("module_data", {}))
        npc_states = state.get("world_state", {}).get("npcs", {}) if isinstance(state.get("world_state", {}).get("npcs"), dict) else {}
        for npc_name, npc_cfg in module_npcs.items():
            if not isinstance(npc_cfg, dict) or not isinstance(npc_cfg.get("companion"), dict):
                continue
            runtime = npc_states.get(npc_name, {})
            npc_location = str((runtime or {}).get("location") or npc_cfg.get("location") or "").strip()
            if current_location and npc_location == current_location:
                return True
        return False

    def _get_effective_ending_flag(self, state: Dict[str, Any], flag_name: str) -> bool:
        if not isinstance(state, dict) or not flag_name:
            return False
        key = str(flag_name).strip()
        world_state = state.get("world_state", {})
        flags = world_state.get("flags", {}) if isinstance(world_state.get("flags"), dict) else {}
        influence = state.get("influence_dimensions", {}) if isinstance(state.get("influence_dimensions"), dict) else {}
        if key == "npc_together":
            return bool(flags.get("npc_together")) or self._get_effective_npc_together(state) or bool(influence.get("npc_together"))
        return self._resolve_bool_influence_dim(state, key)

    def validate_ending_request(self, session_id: str, ending_id: str) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state:
            return {"valid": False, "errors": ["session_missing"], "ending_id": ending_id}
        validation = self._get_ending_validation_config(state, ending_id)
        if not validation:
            return {"valid": True, "errors": [], "ending_id": ending_id}

        errors = []
        required_flags = self._normalize_ending_rule_list(validation.get("required_flags"))
        for flag_name in required_flags:
            if not self._get_effective_ending_flag(state, flag_name):
                errors.append(f"missing_flag:{flag_name}")

        required_any_flags = self._normalize_ending_rule_list(validation.get("required_any_flags"))
        if required_any_flags and not any(self._get_effective_ending_flag(state, flag_name) for flag_name in required_any_flags):
            errors.append(f"missing_any_flag:{'|'.join(required_any_flags)}")

        current_location = str(state.get("current_location") or "").strip()
        required_locations = self._normalize_ending_rule_list(
            validation.get("required_current_locations") or validation.get("required_player_locations") or validation.get("allowed_current_locations")
        )
        if required_locations and current_location not in required_locations:
            errors.append(f"wrong_location:{current_location or 'unknown'}")

        if bool(validation.get("require_npc_together")) and not self._get_effective_npc_together(state):
            errors.append("npc_not_together")

        return {"valid": not errors, "errors": errors, "ending_id": ending_id}

    def process_ending_request(self, session_id: str, rhythm_result: Dict[str, Any] = None) -> Dict[str, Any]:
        result = {
            "requested": False,
            "triggered": False,
            "ending_id": None,
            "reason": None,
            "validation_errors": [],
        }
        state = self.sessions.get(session_id)
        if not state or self.get_ending_phase(session_id):
            return result

        ending_request = (rhythm_result or {}).get("ending_request", {}) if isinstance(rhythm_result, dict) else {}
        if not isinstance(ending_request, dict) or not ending_request.get("requested"):
            return result

        ending_id = str(ending_request.get("ending_id") or "").strip()
        result["requested"] = True
        result["ending_id"] = ending_id or None
        result["reason"] = str(ending_request.get("reason") or "").strip() or None
        if not ending_id:
            result["validation_errors"] = ["missing_ending_id"]
            return result
        if not self.ending_requires_ai_request(session_id, ending_id):
            result["validation_errors"] = ["ending_not_ai_driven"]
            return result

        validation = self.validate_ending_request(session_id, ending_id)
        if not validation.get("valid"):
            result["validation_errors"] = validation.get("errors", [])
            return result

        influence = state.setdefault("influence_dimensions", self._build_default_influence_dimensions())
        if ending_id == "escaped":
            influence["ritual_destroyed"] = self._is_effective_ritual_destroyed(state)
            influence["escape_success"] = True
        influence["npc_together"] = self._get_effective_npc_together(state)
        self.trigger_ending(session_id, ending_id)
        result["triggered"] = True
        return result

    def check_san_ending(self, session_id: str) -> bool:
        """Check if SAN <= 0 and trigger insane ending if so. Returns True if ending triggered."""
        state = self.sessions.get(session_id)
        if not state:
            return False
        # Don't re-trigger if already in ending phase
        if self.get_ending_phase(session_id):
            return False
        san = state.get("player", {}).get("san", 65)
        if san <= 0:
            self.trigger_ending(session_id, "insane", "san_zero")
            return True
        return False

    def is_sancheck_triggered(self, session_id: str, entity_name: str) -> bool:
        """检查某实体的 sancheck 是否已触发过。"""
        state = self.sessions.get(session_id, {})
        return entity_name in state.get("world_state", {}).get("triggered_sanchecks", [])

    def record_sancheck(self, session_id: str, entity_name: str):
        """记录某实体的 sancheck 已触发，防止重复触发。"""
        state = self.sessions.get(session_id)
        if not state:
            return
        triggered = state.get("world_state", {}).setdefault("triggered_sanchecks", [])
        if entity_name not in triggered:
            triggered.append(entity_name)

    def check_ritual_destruction_ending(self, session_id: str) -> bool:
        """Check if ritual has been destroyed and trigger escape ending if so."""
        state = self.sessions.get(session_id)
        if not state:
            return False
        if self.get_ending_phase(session_id):
            return False
        if self.ending_requires_ai_request(session_id, "escaped"):
            return False
        ritual_destroyed = self._is_effective_ritual_destroyed(state)
        if ritual_destroyed:
            validation = self.validate_ending_request(session_id, "escaped")
            if not validation.get("valid"):
                return False
            influence = state.setdefault("influence_dimensions", self._build_default_influence_dimensions())
            influence["ritual_destroyed"] = True
            influence["escape_success"] = True
            influence["npc_together"] = self._get_effective_npc_together(state)
            self.trigger_ending(session_id, "escaped")
            return True
        return False

    def check_location_ending(self, session_id: str) -> bool:
        """Check if current location triggers an ending (e.g., outside → lost)."""
        state = self.sessions.get(session_id)
        if not state:
            return False
        if self.get_ending_phase(session_id):
            return False
        current = state.get("current_location", "")
        module_data = state.get("module_data", {})
        loc_data = module_data.get("locations", {}).get(current, {})
        if loc_data.get("is_ending_location"):
            ending_id = loc_data.get("ending_id", "getlost")
            if ending_id in {"escaped", "emily_escaped"}:
                influence = state.setdefault("influence_dimensions", self._build_default_influence_dimensions())
                influence["escape_success"] = True
                influence["npc_together"] = self._get_effective_npc_together(state)
            self.trigger_ending(session_id, ending_id)
            return True
        return False

    def _evaluate_butler_exposure(self, state: Dict[str, Any]) -> bool:
        if not isinstance(state, dict):
            return False
        if bool(state.get("world_state", {}).get("flags", {}).get("game_over")):
            return True

        butler_state = self._get_butler_runtime_state(state)
        chase_state = butler_state.setdefault("chase_state", self._build_default_butler_chase_state())
        if not isinstance(chase_state, dict):
            chase_state = self._build_default_butler_chase_state()
            butler_state["chase_state"] = chase_state

        if not chase_state.get("active"):
            chase_state["same_location_rounds"] = 0
            return False

        contact_location = self._get_butler_contact_location_from_state(state)
        if contact_location and state.get("current_location") == contact_location:
            chase_state["same_location_rounds"] = int(chase_state.get("same_location_rounds", 0) or 0) + 1
            if chase_state["same_location_rounds"] >= 2:
                self._capture_player_by_butler_state(state, "stayed_with_butler_too_long")
                return True
            return False

        chase_state["same_location_rounds"] = 0
        return False

    def _is_butler_activation_grace_exit(
        self,
        state: Dict[str, Any],
        current_location: str,
        target_location: str,
    ) -> bool:
        if not isinstance(state, dict):
            return False
        if not current_location or not target_location or current_location == target_location:
            return False

        butler_state = self._get_butler_runtime_state(state)
        chase_state = butler_state.get("chase_state", {})
        if not isinstance(chase_state, dict) or not chase_state.get("active"):
            return False
        if str(chase_state.get("target") or "player").strip() != "player":
            return False
        if str(chase_state.get("status") or "").strip() == "blocked":
            return False

        contact_location = self._get_butler_contact_location_from_state(state)
        if not contact_location or current_location != contact_location:
            return False

        activation_round = chase_state.get("activation_round")
        if activation_round is None:
            return False

        return int(activation_round or 0) == int(state.get("round_count", 0) or 0)

    def _advance_butler_chase(self, state: Dict[str, Any]) -> Dict[str, Any]:
        butler_state = self._get_butler_runtime_state(state)
        chase_state = butler_state.setdefault("chase_state", self._build_default_butler_chase_state())
        if not isinstance(chase_state, dict):
            chase_state = self._build_default_butler_chase_state()
            butler_state["chase_state"] = chase_state

        if not chase_state.get("active"):
            chase_state["status"] = "idle"
            chase_state["target"] = None
            chase_state["activation_round"] = None
            chase_state["last_target_location"] = None
            chase_state["blocked_at"] = None
            return {}

        # 主要追逐威胁被门阻隔时不移动
        if chase_state.get("status") == "blocked":
            return {}

        chase_state["status"] = "pursuing"
        chase_state["blocked_at"] = None
        target = chase_state.get("target", "player")

        current_round = int(state.get("round_count", 0) or 0)
        if target == "player":
            target_location = state.get("current_location")
        else:
            # 追踪NPC诱饵
            target_npc_state = state.get("world_state", {}).get("npcs", {}).get(target, {})
            target_location = target_npc_state.get("location", state.get("current_location"))

        activation_round = chase_state.get("activation_round")
        if activation_round is None:
            chase_state["activation_round"] = current_round
            chase_state["last_target_location"] = target_location
            return {}

        if activation_round == current_round:
            chase_state["last_target_location"] = target_location
            return {}

        destination = chase_state.get("last_target_location") or target_location
        previous_butler_location = butler_state.get("location")

        # 检查目标是否在有门房间内 - 主要追逐威胁被门阻隔
        module_data = state.get("module_data", {})
        dest_location_data = module_data.get("locations", {}).get(destination, {})
        if dest_location_data.get("has_door") and destination != previous_butler_location:
            # 目标进入了有门的房间，主要追逐威胁被阻隔在门外
            butler_state["location"] = destination
            chase_state["status"] = "blocked"
            chase_state["blocked_at"] = destination
            chase_state["last_target_location"] = target_location
            pursuer_name = self._get_primary_pursuer_name_from_state(state)
            if pursuer_name and destination != previous_butler_location:
                return {
                    "npc_locations": {
                        pursuer_name: destination
                    }
                }
            return {}

        butler_state["location"] = destination
        chase_state["last_target_location"] = target_location

        if destination and destination != previous_butler_location:
            pursuer_name = self._get_primary_pursuer_name_from_state(state)
            if not pursuer_name:
                return {}
            return {
                "npc_locations": {
                    pursuer_name: destination
                }
            }
        return {}

    def advance_round(self, session_id: str, rhythm_result: Dict[str, Any] = None) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state:
            return {}

        self._ensure_runtime_defaults(state)
        state["round_count"] = int(state.get("round_count", 0) or 0) + 1

        if isinstance(rhythm_result, dict):
            state["rhythm_context"].append({
                "round": state["round_count"],
                "stage_assessment": rhythm_result.get("stage_assessment", ""),
                "world_changes": rhythm_result.get("world_changes", {})
            })

        runtime_changes = {}
        _pause_dim = str(
            (state.get("module_data") or self.default_module_data or {})
            .get("endings", {})
            .get("influence_dimensions", {})
            .get("pause_advance_when") or ""
        ).strip()
        if not _pause_dim or not bool(state.get("influence_dimensions", {}).get(_pause_dim)):
            runtime_changes = self._merge_runtime_changes(runtime_changes, self._advance_preset_tasks(state))
            runtime_changes = self._advance_butler_chase(state)
            self._evaluate_butler_exposure(state)

        self._sync_influence_dimensions(state)
        return runtime_changes

    def _build_default_player_state(self) -> Dict[str, Any]:
        """返回固定预设角色卡。"""
        return copy.deepcopy(PRESET_PLAYER_PROFILE)

    def has_session(self, session_id: str) -> bool:
        """检查会话是否存在"""
        return session_id in self.sessions

    def get_active_preset_task_note(self, session_id: str) -> str:
        """若有进行中的 preset_task，返回其当前阶段对应的 movement_note，否则返回空串。"""
        state = self.sessions.get(session_id) or {}
        npc_states = state.get("world_state", {}).get("npcs", {})
        module_data = state.get("module_data") or self.default_module_data or {}
        preset_tasks_cfg = module_data.get("preset_tasks", {})
        for npc_state in npc_states.values():
            if not isinstance(npc_state, dict):
                continue
            pt = npc_state.get("preset_task", {})
            if not isinstance(pt, dict) or not pt.get("task_id"):
                continue
            if str(pt.get("status") or "").strip() != "running":
                continue
            task_id = str(pt.get("task_id") or "").strip()
            phase = str(pt.get("phase") or "").strip()
            task_cfg = preset_tasks_cfg.get(task_id, {}) if isinstance(preset_tasks_cfg.get(task_id), dict) else {}
            if phase == "offstage":
                return str(task_cfg.get("in_progress_note") or "").strip()
            if phase == "await_handoff":
                return str(task_cfg.get("await_handoff_note") or task_cfg.get("on_start_note") or "").strip()
        return ""

    def get_session(self, session_id: str) -> Dict[str, Any]:
        """获取会话状态"""
        return self.sessions.get(session_id)

    def export_session(self, session_id: str) -> Dict[str, Any]:
        """导出会话状态，便于持久化"""
        state = self.sessions.get(session_id)
        if not state:
            return None

        return self._serialize_value(state)

    def restore_session(self, session_id: str, saved_state: Dict[str, Any]):
        """从持久化数据恢复会话状态"""
        if not saved_state:
            return

        restored_state = dict(saved_state)
        restored_state["session_id"] = session_id

        module_filename = restored_state.get("module_filename") or self.default_module_name
        module_data = restored_state.get("module_data")
        if not module_data:
            module_data = self._load_module(module_filename)
        else:
            module_data = normalize_module_data(module_data)

        restored_state["module_filename"] = module_filename
        restored_state["module_data"] = module_data
        restored_state["current_location"] = restored_state.get("current_location") or self._get_initial_location(module_data)
        restored_state["visited_locations"] = list(restored_state.get("visited_locations") or [restored_state["current_location"]])
        restored_state["round_count"] = int(restored_state.get("round_count", 0))
        restored_state["rhythm_context"] = list(restored_state.get("rhythm_context") or [])

        narrative_history = restored_state.get("narrative_history") or []
        if not isinstance(narrative_history, deque):
            narrative_history = deque(narrative_history)
        restored_state["narrative_history"] = narrative_history
        default_player = self._build_default_player_state()

        player_state = dict(restored_state.get("player") or {})
        player_state.setdefault("name", "调查员")
        player_state.setdefault("san", 65)
        player_state.setdefault("hp", 12)
        player_state.setdefault("skills", {
            "侦查": 60,
            "图书馆": 40,
            "聆听": 50
        })
        player_state.setdefault("inventory", ["手电筒"])
        restored_state["player"] = player_state
        player_state["name"] = player_state.get("name") or default_player["name"]
        player_state["skills"] = copy.deepcopy(default_player["skills"])
        if not player_state.get("inventory"):
            player_state["inventory"] = list(default_player["inventory"])

        world_state = dict(restored_state.get("world_state") or {})
        world_state.setdefault("clues_found", [])
        world_state.setdefault("flags", {
            "door_unlocked": False,
            "truth_revealed": False
        })
        world_state.setdefault("npcs", self._build_initial_npc_state(module_data))
        module_entities = get_module_all_entities(module_data)
        for npc_name, npc_state in list(world_state.get("npcs", {}).items()):
            if not isinstance(npc_state, dict):
                world_state["npcs"][npc_name] = {
                    "memory": self._build_initial_npc_memory((module_entities.get(npc_name) or {}).get("memory"))
                }
                continue
            npc_state.setdefault("memory", self._build_initial_npc_memory((module_entities.get(npc_name) or {}).get("memory")))
            self._sync_single_npc_runtime_state(npc_state, module_entities.get(npc_name, {}))
        restored_state["world_state"] = world_state

        restored_state["influence_dimensions"] = dict(
            restored_state.get("influence_dimensions") or self._build_default_influence_dimensions()
        )
        for key, value in self._build_default_influence_dimensions().items():
            restored_state["influence_dimensions"].setdefault(key, value)

        self.sessions[session_id] = restored_state
        self._ensure_runtime_defaults(self.sessions[session_id], module_data)

    def delete_session(self, session_id: str):
        """删除会话"""
        if session_id in self.sessions:
            del self.sessions[session_id]

    def update_state(self, session_id: str, rhythm_result: Dict[str, Any]):
        """根据节奏AI的结果更新游戏状态"""
        if session_id not in self.sessions:
            return

        state = self.sessions[session_id]
        self._ensure_runtime_defaults(state)

        # 更新轮次
        state["round_count"] += 1

        # 更新世界状态
        if "world_changes" in rhythm_result:
            changes = rhythm_result["world_changes"]
            if "clues" in changes:
                for clue in changes["clues"]:
                    if clue not in state["world_state"]["clues_found"]:
                        state["world_state"]["clues_found"].append(clue)

            if "san_delta" in changes:
                san_delta = int(changes.get("san_delta", 0) or 0)
                state["player"]["san"] = max(0, state["player"].get("san", 0) + san_delta)

            if "inventory_add" in changes:
                inventory = state["player"].setdefault("inventory", [])
                for item in changes["inventory_add"]:
                    if item and item not in inventory:
                        inventory.append(item)

            if "inventory_remove" in changes:
                inventory = state["player"].setdefault("inventory", [])
                for item in changes["inventory_remove"]:
                    if item in inventory:
                        inventory.remove(item)

            if "flags" in changes and isinstance(changes["flags"], dict):
                state["world_state"].setdefault("flags", {})
                state["world_state"]["flags"].update(changes["flags"])

            # 玩家位置由代码控制（move_player方法），不再从节奏AI结果更新

            # 更新NPC位置
            if "npc_locations" in changes:
                for npc_name, location in changes["npc_locations"].items():
                    if npc_name in state["world_state"].get("npcs", {}):
                        state["world_state"]["npcs"][npc_name]["location"] = location

            if "npc_updates" in changes and isinstance(changes["npc_updates"], dict):
                npc_state = state["world_state"].setdefault("npcs", {})
                module_entities = get_module_all_entities(state.get("module_data", {}))
                for npc_name, update in changes["npc_updates"].items():
                    if npc_name not in npc_state:
                        npc_state[npc_name] = {}
                    if isinstance(update, dict):
                        # 提取trust_delta（瞬态字段，不存入状态）
                        trust_delta = update.pop("trust_delta", None)
                        self._deep_merge_dict(npc_state[npc_name], update)
                        npc_state[npc_name].setdefault("memory", self._build_initial_npc_memory((module_entities.get(npc_name) or {}).get("memory")))
                        # 应用信任增量
                        if isinstance(trust_delta, (int, float)):
                            current_trust = float(npc_state[npc_name].get("trust_level", 0.0))
                            npc_state[npc_name]["trust_level"] = round(
                                max(0.0, min(1.0, current_trust + trust_delta)), 2
                            )
                        self._sync_single_npc_runtime_state(npc_state[npc_name], module_entities.get(npc_name, {}))
                    elif isinstance(update, str):
                        npc_state[npc_name]["location"] = update

                # 自动清除已回答的pending_questions
                for npc_name, npc_data in npc_state.items():
                    memory = npc_data.get("memory")
                    if not isinstance(memory, dict):
                        continue
                    answered = set(memory.get("answered_questions", []))
                    if answered:
                        pending = memory.get("pending_questions", [])
                        memory["pending_questions"] = [q for q in pending if q not in answered]

            if "threat_entity_updates" in changes and isinstance(changes["threat_entity_updates"], dict):
                npc_state = state["world_state"].setdefault("npcs", {})
                module_entities = get_module_all_entities(state.get("module_data", {}))
                for entity_name, update in changes["threat_entity_updates"].items():
                    if entity_name not in npc_state:
                        npc_state[entity_name] = {}
                    if isinstance(update, dict):
                        self._deep_merge_dict(npc_state[entity_name], update)
                        self._sync_single_npc_runtime_state(npc_state[entity_name], module_entities.get(entity_name, {}))
                    elif isinstance(update, str):
                        npc_state[entity_name]["location"] = update

        # 保存节奏AI上下文（阶段判断+世界变化）
        state["rhythm_context"].append({
            "round": state["round_count"],
            "stage_assessment": rhythm_result.get("stage_assessment", ""),
            "world_changes": rhythm_result.get("world_changes", {})
        })
        runtime_changes = {}
        _pause_dim = str(
            (state.get("module_data") or self.default_module_data or {})
            .get("endings", {})
            .get("influence_dimensions", {})
            .get("pause_advance_when") or ""
        ).strip()
        if not _pause_dim or not bool(state.get("influence_dimensions", {}).get(_pause_dim)):
            runtime_changes = self._merge_runtime_changes(runtime_changes, self._advance_preset_tasks(state))
            runtime_changes = self._merge_runtime_changes(runtime_changes, self._advance_companion_tasks(state))
            runtime_changes = self._merge_runtime_changes(runtime_changes, self._advance_butler_chase(state))
            runtime_changes = self._merge_runtime_changes(runtime_changes, self._finalize_companion_tasks(state))
            self._evaluate_butler_exposure(state)
        self._sync_influence_dimensions(state)
        return runtime_changes

    def add_narrative_summary(self, session_id: str, player_input: str, narrative: str, summary: str):
        """添加文案记录到历史"""
        if session_id not in self.sessions:
            return

        state = self.sessions[session_id]
        state["narrative_history"].append({
            "round": state["round_count"],
            "player_input": player_input,
            "narrative": narrative,
            "summary": summary
        })

    def get_opening(self, session_id: str = None) -> str:
        """获取游戏开场白"""
        module_data = self.get_module_data(session_id)

        # 从模组数据中读取开场白
        opening_text = module_data.get("module_info", {}).get("opening", "")

        if not opening_text:
            # 如果模组没有开场白，使用默认的
            opening_text = """你是一名私家侦探，接到委托调查一座废弃的宅邸。

当你推开吱呀作响的大门，一股霉味扑面而来。你发现自己身处一间昏暗的卧室中，窗外传来诡异的声响...

你的目标是找到真相，并活着离开这里。"""

        # 获取模组名称
        module_name = module_data.get("module_info", {}).get("name", "AI驱动TRPG")

        return f"""🎲 {module_name}

{opening_text}

━━━━━━━━━━━━━━━━
👤 调查员
  理智: 65
  生命: 12
  技能: 侦查60 图书馆40 聆听50

📍 当前位置: 卧室
━━━━━━━━━━━━━━━━"""

    # ─── 地图与移动相关方法 ───

    def _build_name_to_key_map(self, module_data: Dict[str, Any]) -> Dict[str, str]:
        """构建 {中文显示名 → location key} 映射"""
        locations = module_data.get("locations", {})
        name_map = {}
        for key, loc_data in locations.items():
            name = loc_data.get("name", "")
            if name:
                name_map[name] = key
        return name_map

    def _get_adjacency_graph(self, module_data: Dict[str, Any]) -> Dict[str, List[str]]:
        """构建邻接表，将exits中的显示名转为key，并加入passage objects的leads_to连接"""
        locations = module_data.get("locations", {})
        name_to_key = self._build_name_to_key_map(module_data)
        graph = {}
        for key, loc_data in locations.items():
            exits = loc_data.get("exits", [])
            neighbors = []
            for exit_name in exits:
                neighbor_key = name_to_key.get(exit_name)
                if neighbor_key:
                    neighbors.append(neighbor_key)
            graph[key] = neighbors

        # 加入passage objects的leads_to连接（如"地下室入口"从kitchen通向basement）
        objects = module_data.get("objects", {})
        for obj_name, obj_data in objects.items():
            leads_to = obj_data.get("leads_to")
            if leads_to and leads_to in locations:
                from_loc = obj_data.get("location", "")
                if from_loc in graph and leads_to not in graph[from_loc]:
                    graph[from_loc].append(leads_to)

        return graph

    def _get_locked_exits(self, session_id: str) -> Set[tuple]:
        """扫描所有objects，找到有leads_to且requires未满足的，返回锁定的边集合 {(from_key, to_key)}"""
        state = self.sessions.get(session_id)
        if not state:
            return set()

        module_data = self.get_module_data(session_id)
        objects = module_data.get("objects", {})
        inventory = state.get("player", {}).get("inventory", [])
        clues_found = state.get("world_state", {}).get("clues_found", [])
        all_items = set(inventory) | set(clues_found)

        locked = set()
        for obj_name, obj_data in objects.items():
            leads_to = obj_data.get("leads_to")
            requires = obj_data.get("requires")
            requires_any = obj_data.get("requires_any")
            if leads_to and (requires or requires_any):
                satisfied = True
                if requires and not all(req in all_items for req in requires):
                    satisfied = False
                if requires_any and not any(req in all_items for req in requires_any):
                    satisfied = False
                if not satisfied:
                    from_loc = obj_data.get("location", "")
                    locked.add((from_loc, leads_to))

        return locked

    def _check_reveal_conditions(self, session_id: str, conditions: list) -> bool:
        """检查reveal_conditions中的条件列表是否有任一满足"""
        if not conditions:
            return False
        state = self.sessions.get(session_id)
        if not state:
            return False

        inventory = state.get("player", {}).get("inventory", [])
        clues_found = state.get("world_state", {}).get("clues_found", [])
        all_items = set(inventory) | set(clues_found)

        for cond in conditions:
            if cond in all_items:
                return True
            if self._get_effective_ending_flag(state, str(cond or "").strip()):
                return True

        return False

    def _is_location_visible(self, session_id: str, location_key: str) -> bool:
        """判断地点是否已经满足显示条件。"""
        module_data = self.get_module_data(session_id)
        loc_data = module_data.get("locations", {}).get(location_key, {})
        if not loc_data:
            return False

        if not loc_data.get("hidden", False):
            return True

        reveal_conds = loc_data.get("reveal_conditions", {})
        return self._check_reveal_conditions(session_id, reveal_conds.get("node_visible", []))

    def _get_map_frontier(self, session_id: str) -> Set[str]:
        """返回所有已访问地点向外一层可见的未探索地点。"""
        state = self.sessions.get(session_id)
        if not state:
            return set()

        graph = self._get_adjacency_graph(self.get_module_data(session_id))
        visited_locations = set(state.get("visited_locations", []))
        frontier = set()

        for location_key in visited_locations:
            for neighbor_key in graph.get(location_key, []):
                if neighbor_key in visited_locations:
                    continue
                if not self._is_location_visible(session_id, neighbor_key):
                    continue
                frontier.add(neighbor_key)

        return frontier

    def _get_adjacent_moves(self, session_id: str) -> Set[str]:
        state = self.sessions.get(session_id)
        if not state:
            return set()

        current = state["current_location"]
        graph = self._get_adjacency_graph(self.get_module_data(session_id))
        locked_exits = self._get_locked_exits(session_id)
        available_moves = set()

        for neighbor_key in graph.get(current, []):
            if (current, neighbor_key) in locked_exits:
                continue
            if not self._is_location_visible(session_id, neighbor_key):
                continue
            if self._classify_butler_door_transition(state, current, neighbor_key):
                continue
            available_moves.add(neighbor_key)

        return available_moves

    def _get_available_moves(self, session_id: str) -> Set[str]:
        """返回当前地点可以直接前往的相邻地点。"""
        state = self.sessions.get(session_id)
        if not state:
            return set()

        available_moves = set(self._get_adjacent_moves(session_id))
        if self._is_player_movement_restricted_by_pursuer_state(state):
            return available_moves

        current = state["current_location"]
        graph = self._get_adjacency_graph(self.get_module_data(session_id))
        locked_exits = self._get_locked_exits(session_id)
        visited_locations = set(state.get("visited_locations", []))

        for neighbor_key in graph.get(current, []):
            if (current, neighbor_key) in locked_exits:
                continue
            if not self._is_location_visible(session_id, neighbor_key):
                continue
            available_moves.add(neighbor_key)

        queue = [current]
        seen = {current}
        while queue:
            node = queue.pop(0)
            for neighbor_key in graph.get(node, []):
                if neighbor_key in seen:
                    continue
                if (node, neighbor_key) in locked_exits:
                    continue
                if neighbor_key not in visited_locations:
                    continue
                if not self._is_location_visible(session_id, neighbor_key):
                    continue
                seen.add(neighbor_key)
                queue.append(neighbor_key)
                available_moves.add(neighbor_key)

        return available_moves

    def _get_location_display_name(self, session_id: str, location_key: str, is_visited: bool) -> str:
        """根据已探索状态和揭示条件返回地点显示名。"""
        module_data = self.get_module_data(session_id)
        loc_data = module_data.get("locations", {}).get(location_key, {})
        if not loc_data:
            return location_key

        if not is_visited:
            if loc_data.get("show_name_when_visible"):
                return str(loc_data.get("hidden_name") or loc_data.get("name") or location_key)
            return "?"

        hidden_name = loc_data.get("hidden_name")
        if hidden_name:
            reveal_conds = loc_data.get("reveal_conditions", {})
            if not self._check_reveal_conditions(session_id, reveal_conds.get("true_name", [])):
                return hidden_name

        return loc_data.get("name", location_key)

    def move_player(self, session_id: str, target_key: str) -> Dict[str, Any]:
        """
        代码控制移动玩家到目标位置

        Returns:
            {"success": bool, "message": str}
        """
        state = self.sessions.get(session_id)
        if not state:
            return {"success": False, "message": "会话不存在"}

        module_data = self.get_module_data(session_id)
        locations = module_data.get("locations", {})
        if target_key not in locations:
            return {"success": False, "message": "无效的目标位置"}

        current = state["current_location"]
        if target_key == current:
            return {"success": True, "message": "已在该位置"}

        door_transition = self._classify_butler_door_transition(state, current, target_key)
        if door_transition == "exit_guarded_room":
            self.capture_player_by_butler(session_id, "opened_guarded_door")
            return {
                "success": False,
                "caught": True,
                "message": self._get_primary_pursuer_message_from_state(
                    state,
                    "opened_guarded_door",
                    "你一推开门，那道早已守在门外的视线立刻迎了上来。",
                ),
            }
        if door_transition == "enter_guarded_room":
            return {
                "success": False,
                "warning_blocked": True,
                "message": self._get_primary_pursuer_message_from_state(
                    state,
                    "guarded_room_blocked",
                    "{entity_name}还守在门后。现在开门，只会把自己送进它的视线里。",
                ),
            }

        # BFS检查可达性
        available_moves = self._get_available_moves(session_id)
        if target_key not in available_moves:
            return {"success": False, "message": "目标位置不可达（路径被锁定）"}

        # 执行移动
        adjacent_moves = self._get_adjacent_moves(session_id)
        available_moves = self._get_available_moves(session_id)
        if target_key not in available_moves:
            if self._is_player_movement_restricted_by_pursuer_state(state) and target_key not in adjacent_moves:
                return {
                    "success": False,
                    "message": self._get_primary_pursuer_message_from_state(
                        state,
                        "movement_restricted",
                        "{entity_name}已被激活。现在你只能逐格移动到相邻场景。",
                    ),
                }
            return {"success": False, "message": "目标位置不可达（路径被锁定）"}

        first_entry_cfg = self._get_location_first_entry_block_config(state, target_key)
        first_entry_mode = str(first_entry_cfg.get("mode") or "block").strip().lower()
        if first_entry_mode != "warn_only":
            first_entry_mode = "block"

        if first_entry_mode == "block":
            first_entry_block_text = self._consume_first_entry_block(
                state,
                first_entry_cfg,
                current_location=current,
            )
        else:
            first_entry_block_text = ""

        if first_entry_block_text:
            return {
                "success": False,
                "warning_blocked": True,
                "message": first_entry_block_text,
            }

        dodge_result = None
        movement_note = None
        if self._is_player_movement_restricted_by_pursuer_state(state):
            butler_location = self._get_butler_contact_location_from_state(state)
            activation_grace_exit = self._is_butler_activation_grace_exit(state, current, target_key)
            needs_dodge = (
                bool(butler_location)
                and (current == butler_location or target_key == butler_location)
                and not activation_grace_exit
            )
            if needs_dodge:
                dodge_result = self._roll_skill_check(state.get("player", {}), "闪避", "普通")
                if not dodge_result.get("success"):
                    self.capture_player_by_butler(session_id, "dodge_failed")
                    return {
                        "success": False,
                        "caught": True,
                        "message": self._get_primary_pursuer_message_from_state(
                            state,
                            "dodge_fail",
                            "你试图从{entity_name}身边脱身，却被那具不自然的人形慢慢逼住。下一瞬，它强迫你迎上了那道目光。",
                        ),
                        "check_result": dodge_result,
                    }
                movement_note = "你在那具迟缓却精准的人形逼近前猛地侧身，从它的封锁里惊险脱出。"

        previous_location = current
        state["current_location"] = target_key

        cooperative_npc_name, cooperative_npc_state = self._find_awaiting_cooperative_handoff(state)
        if cooperative_npc_name and isinstance(cooperative_npc_state, dict):
            cooperative_location = str(cooperative_npc_state.get("location") or "").strip()
            branch = "player_handoff_success" if target_key == cooperative_location else "player_abandoned"
            branch_changes = self._resolve_preset_task_branch_state(state, cooperative_npc_name, branch)
            _task_note = str(branch_changes.get("movement_note") or "").strip()
            if _task_note:
                movement_note = f"{_task_note} {movement_note}".strip() if movement_note else _task_note

        if self.is_butler_active(session_id):
            butler_state = self._get_butler_runtime_state(state)
            chase_state = butler_state.setdefault("chase_state", self._build_default_butler_chase_state())
            if isinstance(chase_state, dict) and chase_state.get("target", "player") == "player":
                # Butler trails the player's last room, not the room just entered.
                chase_state["last_target_location"] = previous_location

        # 跟随状态的NPC一起移动
        for npc_name, npc_data in state["world_state"].get("npcs", {}).items():
            if not isinstance(npc_data, dict):
                continue
            if str(npc_data.get("companion_mode") or npc_data.get("companion_state") or "") != "follow":
                continue
            task = npc_data.get("companion_task", {})
            if not isinstance(task, dict):
                continue
            follow_target = str(task.get("target_entity") or "player").strip() or "player"
            lag = max(0, int(task.get("lag", 0) or 0))
            if follow_target == "player" and lag == 0 and not self._coerce_companion_flag(task.get("awaiting_exit_release")):
                npc_data["location"] = target_key

        # 标记已访问
        if target_key not in state["visited_locations"]:
            state["visited_locations"].append(target_key)

        if first_entry_mode == "warn_only":
            first_entry_warning_text = self._consume_first_entry_block(
                state,
                first_entry_cfg,
                current_location=previous_location,
            )
            if first_entry_warning_text:
                movement_note = (
                    f"{first_entry_warning_text} {movement_note}".strip()
                    if movement_note
                    else first_entry_warning_text
                )

        return {
            "success": True,
            "message": f"moved to {locations[target_key].get('name', target_key)}",
            "previous_location": previous_location,
            "check_result": dodge_result,
            "movement_note": movement_note,
        }

        return {"success": True, "message": f"移动到{locations[target_key].get('name', target_key)}"}

    def get_reachable_locations(self, session_id: str) -> Set[str]:
        """BFS遍历exits图，跳过锁定出口，返回所有从当前位置可达的location key集合"""
        state = self.sessions.get(session_id)
        if not state:
            return set()

        module_data = self.get_module_data(session_id)
        current = state["current_location"]
        graph = self._get_adjacency_graph(module_data)
        locked_exits = self._get_locked_exits(session_id)

        # 将locked_exits中的location key对应回来（locked中from是location key of the object）
        # 需要将object.location转为location key
        # 注意：locked_exits中的from_loc是object的location字段（这是一个location key）
        # 所以 (from_loc, to_loc) 就是 (location_key, location_key)

        visited = set()
        queue = [current]
        visited.add(current)

        while queue:
            node = queue.pop(0)
            for neighbor in graph.get(node, []):
                if neighbor in visited:
                    continue
                # 检查这条边是否被锁定
                if (node, neighbor) in locked_exits:
                    continue
                visited.add(neighbor)
                queue.append(neighbor)

        return visited

    def get_map_data(self, session_id: str) -> Dict[str, Any]:
        """返回完整地图数据给前端（含战争迷雾计算）"""
        state = self.sessions.get(session_id)
        if not state:
            return {}

        module_data = self.get_module_data(session_id)
        locations = module_data.get("locations", {})
        visited_locations = set(state.get("visited_locations", []))
        visible_keys = visited_locations | self._get_map_frontier(session_id)
        reachable = self._get_available_moves(session_id)
        locked_exits = self._get_locked_exits(session_id)
        name_to_key = self._build_name_to_key_map(module_data)
        available_micro_scenes = self.get_available_micro_scenes(session_id)
        danger_locations = set()
        if self.is_butler_active(session_id) or self.has_butler_living_room_warning(session_id):
            butler_location = self.get_butler_location(session_id)
            if butler_location:
                danger_locations.add(butler_location)

        # 构建可见节点
        visible_locations = {}
        for key in visible_keys:
            loc_data = locations.get(key, {})
            if not loc_data:
                continue
            # 检查hidden节点是否满足node_visible条件
            if loc_data.get("hidden", False):
                reveal_conds = loc_data.get("reveal_conditions", {})
                node_visible_conds = reveal_conds.get("node_visible", [])
                if not self._check_reveal_conditions(session_id, node_visible_conds):
                    continue  # 隐藏且未解锁，不出现在地图上

            # 计算显示名（三层逻辑）
            is_visited = key in visited_locations
            if not is_visited:
                if loc_data.get("show_name_when_visible"):
                    display_name = str(loc_data.get("hidden_name") or loc_data.get("name") or key)
                else:
                    display_name = "?"
            else:
                # 已访问，检查是否有hidden_name且true_name条件未满足
                hidden_name = loc_data.get("hidden_name")
                if hidden_name:
                    reveal_conds = loc_data.get("reveal_conditions", {})
                    true_name_conds = reveal_conds.get("true_name", [])
                    if self._check_reveal_conditions(session_id, true_name_conds):
                        display_name = loc_data.get("name", key)  # 里名
                    else:
                        display_name = hidden_name  # 表名
                else:
                    display_name = loc_data.get("name", key)

            visible_locations[key] = {
                "display_name": display_name,
                "floor": loc_data.get("floor", 1),
                "visited": is_visited,
            }

        # 将当前房间下可用的微场景挂入地图数据，供前端显式展示为特殊入口。
        for micro_scene_id, micro_scene_cfg in available_micro_scenes.items():
            parent_location = str(micro_scene_cfg.get("parent_location") or "").strip()
            if not parent_location:
                continue
            parent_loc = locations.get(parent_location, {})
            visible_locations[micro_scene_id] = {
                "display_name": str(micro_scene_cfg.get("display_name") or micro_scene_cfg.get("name") or micro_scene_id),
                "floor": parent_loc.get("floor", 1),
                "visited": True,
                "is_micro_scene": True,
                "parent_location": parent_location,
            }
            reachable.add(micro_scene_id)

        # 应用持久化的地图腐蚀
        corrupt_map = state.get("world_state", {}).get("corrupt_map", {})
        for key, corrupted_name in corrupt_map.items():
            if key in visible_locations:
                visible_locations[key]["display_name"] = corrupted_name

        # 构建可见边
        edges = []
        visible_keys = set(visible_locations.keys())
        seen_edges = set()
        for key in visible_keys:
            loc_data = locations.get(key, {})
            for exit_name in loc_data.get("exits", []):
                neighbor_key = name_to_key.get(exit_name)
                if neighbor_key and neighbor_key in visible_keys:
                    edge_pair = tuple(sorted([key, neighbor_key]))
                    if edge_pair not in seen_edges:
                        seen_edges.add(edge_pair)
                        is_locked = (key, neighbor_key) in locked_exits or (neighbor_key, key) in locked_exits
                        edges.append({
                            "from": key,
                            "to": neighbor_key,
                            "locked": is_locked,
                        })

        for micro_scene_id, micro_scene_cfg in available_micro_scenes.items():
            parent_location = str(micro_scene_cfg.get("parent_location") or "").strip()
            if not parent_location or parent_location not in visible_keys:
                continue
            edge_pair = tuple(sorted([parent_location, micro_scene_id]))
            if edge_pair in seen_edges:
                continue
            seen_edges.add(edge_pair)
            edges.append({
                "from": parent_location,
                "to": micro_scene_id,
                "locked": False,
            })

        # 收集NPC位置（非威胁实体，仅玩家已访问过的位置）
        npc_marker_locations = set()
        npc_states = state.get("world_state", {}).get("npcs", {})
        module_npcs = get_module_npcs(module_data)
        for npc_name in module_npcs:
            npc_runtime = npc_states.get(npc_name, {})
            npc_loc = npc_runtime.get("location", module_npcs[npc_name].get("location"))
            if npc_loc and npc_loc in visible_locations and npc_loc in visited_locations:
                npc_marker_locations.add(npc_loc)

        return {
            "locations": visible_locations,
            "edges": edges,
            "current_location": state["current_location"],
            "reachable": list(reachable),
            "danger_locations": [
                location_key for location_key in danger_locations
                if location_key in visible_locations
            ],
            "npc_locations": [
                location_key for location_key in npc_marker_locations
                if location_key in visible_locations
            ],
        }

    def get_module_data(self, session_id: str = None):
        """获取模组数据"""
        if session_id and session_id in self.sessions:
            module_data = self.sessions[session_id].get("module_data")
            if module_data:
                return module_data

        return self.default_module_data

    def get_location_context(self, session_id: str, location_key: str = None) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state:
            return {}
        module_data = self.get_module_data(session_id)
        return build_runtime_location_context(state, module_data, location_key)

    def _serialize_value(self, value):
        """递归序列化会话状态中的 deque 等对象"""
        if isinstance(value, deque):
            return list(value)
        if isinstance(value, dict):
            return {
                key: self._serialize_value(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._serialize_value(item) for item in value]
        return value

    def _deep_merge_dict(self, base: Dict[str, Any], incoming: Dict[str, Any]):
        for key, value in incoming.items():
            if isinstance(value, dict):
                existing = base.get(key)
                if not isinstance(existing, dict):
                    existing = {}
                    base[key] = existing
                self._deep_merge_dict(existing, value)
                continue

            if isinstance(value, list):
                if key in {"pending_questions"}:
                    base[key] = [item for item in value if item]
                    continue
                existing = base.get(key)
                if not isinstance(existing, list):
                    existing = []
                    base[key] = existing
                for item in value:
                    if item not in existing:
                        existing.append(item)
                continue

            base[key] = value
