import json
import os
import copy
import random
from collections import deque
from typing import Dict, Any, List, Set
from .location_context import build_runtime_location_context, get_module_all_entities, get_module_npcs


BUTLER_NPC_NAME = "管家"
LIVING_ROOM_FIRST_ENTRY_WARNING_MESSAGE = (
    "你刚想踏进客厅深处，那道背对入口的人形便极轻地调整了站姿，像是已经把你的存在纳入了视野。"
    "直觉告诉你，现在贸然进去只会立刻引来它的注意。你暂时退了回来，也许该先想办法把它引开。"
)
OUTSIDE_LOST_WARNING_MESSAGE = (
    "你刚想推开厨房的后门，门外那片灰蒙蒙的平原便先一步压进了你的视野。"
    "那里没有道路，没有参照物，也看不出任何尽头。直觉告诉你，从这里逃走绝不是正确的选择。"
    "你停在门边，没有真正踏出去。"
)


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
                    with open(module_path, "r", encoding="utf-8") as f:
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

    def load_module_for_session(self, session_id: str, module_filename: str):
        """为会话加载指定模组"""
        if session_id not in self.sessions:
            return

        module_data = self._load_module(module_filename)
        initial_location = self._get_initial_location(module_data)
        self.sessions[session_id]["module_filename"] = module_filename
        self.sessions[session_id]["module_data"] = module_data
        self.sessions[session_id]["current_location"] = initial_location
        self.sessions[session_id]["visited_locations"] = [initial_location]
        self.sessions[session_id]["player"] = self._build_default_player_state()
        self.sessions[session_id]["world_state"]["npcs"] = self._build_initial_npc_state(module_data)
        self.sessions[session_id]["influence_dimensions"] = self._build_default_influence_dimensions()
        self._ensure_runtime_defaults(self.sessions[session_id], module_data)

    def _load_module(self, module_name: str = "default_module"):
        """加载模组数据"""
        # 从JSON文件加载模组
        module_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "modules",
            f"{module_name}.json"
        )

        try:
            with open(module_path, "r", encoding="utf-8") as f:
                module_data = json.load(f)
            return module_data
        except FileNotFoundError:
            # 如果文件不存在，返回一个最小化的默认模组
            return {
                "module_info": {
                    "name": "默认模组",
                    "theme": "克苏鲁恐怖",
                    "target_rounds": 30
                },
                "locations": {},
                "objects": {},
                "npcs": {},
                "escape_conditions": {}
            }
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
        for npc_name, npc_data in get_module_all_entities(module_data).items():
            npc_states[npc_name] = {
                "attitude": npc_data.get("initial_attitude", "中立"),
                "trust_level": 0.0,
                "memory": self._build_initial_npc_memory(),
            }
            if npc_data.get("location"):
                npc_states[npc_name]["location"] = npc_data["location"]
            if npc_name == BUTLER_NPC_NAME:
                npc_states[npc_name]["chase_state"] = self._build_default_butler_chase_state()
            # 非威胁NPC初始化同伴状态
            if npc_name in module_npcs:
                npc_states[npc_name]["companion_state"] = "inactive"
        return npc_states

    def _build_initial_npc_memory(self) -> Dict[str, Any]:
        return {
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
        }

    def _build_default_butler_chase_state(self) -> Dict[str, Any]:
        return {
            "active": False,
            "status": "idle",
            "target": None,
            "activation_round": None,
            "last_target_location": None,
            "same_location_rounds": 0,
        }

    def _build_default_influence_dimensions(self) -> Dict[str, Any]:
        return {
            "escape_success": False,
            "npc_together": False,
            "truth_revealed": False,
            "butler_gaze": False,
            "san_remaining": 65,
            "rounds_used": 0,
        }

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
        module_npcs_map = get_module_all_entities(module_data) if isinstance(module_data, dict) else {}
        friendly_npcs = set(get_module_npcs(module_data).keys()) if isinstance(module_data, dict) else set()

        for npc_name, npc_data in module_npcs_map.items():
            runtime_state = npc_states.setdefault(npc_name, {})
            if not isinstance(runtime_state, dict):
                runtime_state = {}
                npc_states[npc_name] = runtime_state
            runtime_state.setdefault("attitude", npc_data.get("initial_attitude", "中立"))
            runtime_state.setdefault("trust_level", 0.0)
            runtime_state.setdefault("memory", self._build_initial_npc_memory())
            if npc_data.get("location"):
                runtime_state.setdefault("location", npc_data.get("location"))
            if npc_name in friendly_npcs:
                runtime_state.setdefault("companion_state", "inactive")
            if npc_name == BUTLER_NPC_NAME:
                chase_state = runtime_state.setdefault("chase_state", {})
                if not isinstance(chase_state, dict):
                    chase_state = {}
                    runtime_state["chase_state"] = chase_state
                for key, value in self._build_default_butler_chase_state().items():
                    chase_state.setdefault(key, value)

        influence = state.setdefault("influence_dimensions", self._build_default_influence_dimensions())
        for key, value in self._build_default_influence_dimensions().items():
            influence.setdefault(key, value)
        self._sync_influence_dimensions(state)

    def _sync_influence_dimensions(self, state: Dict[str, Any]):
        if not isinstance(state, dict):
            return

        influence = state.setdefault("influence_dimensions", self._build_default_influence_dimensions())
        for key, value in self._build_default_influence_dimensions().items():
            influence.setdefault(key, value)

        player = state.setdefault("player", {})
        world_state = state.setdefault("world_state", {})
        flags = world_state.setdefault("flags", {})
        influence["truth_revealed"] = bool(flags.get("truth_revealed", influence.get("truth_revealed", False)))
        influence["san_remaining"] = int(player.get("san", 0) or 0)
        influence["rounds_used"] = int(state.get("round_count", 0) or 0)

    def _get_butler_runtime_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_runtime_defaults(state)
        npc_states = state.setdefault("world_state", {}).setdefault("npcs", {})
        return npc_states.setdefault(BUTLER_NPC_NAME, {})

    def _get_module_special_message(self, session_id: str, key: str, default: str = "") -> str:
        module_data = self.get_module_data(session_id)
        if not isinstance(module_data, dict):
            return default

        special_messages = module_data.get("special_messages", {})
        if not isinstance(special_messages, dict):
            return default

        message = special_messages.get(key)
        if isinstance(message, str):
            message = message.strip()
            if message:
                return message
        return default

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

    def get_butler_chase_context(self, session_id: str) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state:
            return {}
        return self.build_butler_chase_context(state)

    def build_butler_chase_context(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(state, dict):
            return {}

        butler_state = self._get_butler_runtime_state(state)
        chase_state = butler_state.get("chase_state", {})
        if not isinstance(chase_state, dict):
            chase_state = self._build_default_butler_chase_state()

        current_location = str(state.get("current_location") or "").strip()
        butler_location = str(butler_state.get("location") or "").strip()
        blocked_at = str(chase_state.get("blocked_at") or "").strip()
        target = chase_state.get("target")

        relation = "unknown"
        if butler_location and current_location:
            if butler_location == current_location:
                relation = "same_room"
            elif blocked_at and blocked_at == current_location:
                relation = "blocked_outside_current_room"
            else:
                relation = "separate_rooms"

        return {
            "active": bool(chase_state.get("active")),
            "status": str(chase_state.get("status") or "idle"),
            "target": target,
            "butler_location": butler_location or None,
            "player_location": current_location or None,
            "blocked_at": blocked_at or None,
            "last_target_location": chase_state.get("last_target_location"),
            "same_location_rounds": int(chase_state.get("same_location_rounds", 0) or 0),
            "player_relation": relation,
        }

    def is_player_with_active_butler(self, session_id: str) -> bool:
        state = self.sessions.get(session_id)
        if not state or not self.is_butler_active(session_id):
            return False
        return state.get("current_location") == self.get_butler_location(session_id)

    def should_use_butler_arrival_judgement(self, session_id: str, target_key: str) -> bool:
        state = self.sessions.get(session_id)
        if not state or self.is_butler_active(session_id):
            return False
        if target_key != self.get_butler_location(session_id):
            return False
        return target_key == "living_room"

    # ── 同伴状态管理 ──

    def set_companion_state(self, session_id: str, npc_name: str, target_state: str) -> Dict[str, Any]:
        """设置NPC同伴状态。需要信任达到高信任门阈值。"""
        state = self.sessions.get(session_id)
        if not state:
            return {"success": False, "message": "会话不存在"}

        npc_state = state["world_state"].get("npcs", {}).get(npc_name)
        if not npc_state or not isinstance(npc_state, dict):
            return {"success": False, "message": f"NPC {npc_name} 不存在"}

        current_companion = npc_state.get("companion_state")
        if current_companion is None:
            return {"success": False, "message": f"{npc_name} 不是可同伴化的NPC"}

        # 检查信任门阈值
        module_data = self.get_module_data(session_id)
        npc_module = (module_data or {}).get("npcs", {}).get(npc_name, {})
        trust_gates = npc_module.get("trust_gates", {})
        high_min = float(trust_gates.get("high", {}).get("min", npc_module.get("trust_threshold", 0.5)))
        trust_level = float(npc_state.get("trust_level", 0.0))

        if trust_level < high_min:
            return {"success": False, "message": f"{npc_name}的信任不足，拒绝了你的请求"}

        # 首次解锁
        if current_companion == "inactive":
            npc_state["companion_state"] = target_state
        else:
            npc_state["companion_state"] = target_state

        # follow时同步位置
        if target_state == "follow":
            npc_state["location"] = state["current_location"]

        return {"success": True, "message": f"{npc_name}状态变更为{target_state}", "new_state": target_state}

    def get_companion_state(self, session_id: str, npc_name: str) -> str:
        """获取NPC同伴状态。"""
        state = self.sessions.get(session_id)
        if not state:
            return "inactive"
        return state["world_state"].get("npcs", {}).get(npc_name, {}).get("companion_state", "inactive")

    def get_following_companions(self, session_id: str) -> list:
        """获取所有处于follow状态的NPC名列表。"""
        state = self.sessions.get(session_id)
        if not state:
            return []
        companions = []
        for npc_name, npc_data in state["world_state"].get("npcs", {}).items():
            if isinstance(npc_data, dict) and npc_data.get("companion_state") == "follow":
                companions.append(npc_name)
        return companions

    def execute_bait_action(self, session_id: str, bait_entity: str, target_room: str = None) -> Dict[str, Any]:
        """执行诱饵行动：将管家引到有门房间，然后关门阻隔。"""
        state = self.sessions.get(session_id)
        if not state:
            return {"success": False, "message": "会话不存在"}

        module_data = self.get_module_data(session_id)
        locations = module_data.get("locations", {})

        # 确定诱饵位置
        if bait_entity == "player":
            bait_location = state["current_location"]
        else:
            npc_state = state["world_state"].get("npcs", {}).get(bait_entity, {})
            bait_location = npc_state.get("location")

        if not bait_location:
            return {"success": False, "message": "无法确定诱饵位置"}

        room = target_room or bait_location
        room_data = locations.get(room, {})
        if not room_data.get("has_door"):
            return {"success": False, "message": "这个房间没有门可以关"}

        # 管家追踪目标切换到诱饵
        butler_state = self._get_butler_runtime_state(state)
        chase_state = butler_state.get("chase_state", {})
        chase_state["target"] = bait_entity
        chase_state["last_target_location"] = room
        chase_state["active"] = True

        return {"success": True, "butler_target": bait_entity, "bait_room": room}

    def block_butler_with_current_room_door(self, session_id: str) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state:
            return {"success": False, "message": "会话不存在"}
        if not self.is_butler_active(session_id):
            return {"success": False, "message": "管家当前没有在追逐你"}

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

        chase_state["active"] = True
        chase_state["status"] = "blocked"
        chase_state["target"] = "player"
        chase_state["blocked_at"] = current_location
        chase_state["last_target_location"] = current_location
        self._sync_influence_dimensions(state)
        return {
            "success": True,
            "blocked_at": current_location,
            "message": "你及时关上了门，暂时把管家拦在了外面。",
        }

    def unblock_butler(self, session_id: str, new_target: str = "player"):
        """当新活物进入管家视野时，解除管家的blocked状态。"""
        state = self.sessions.get(session_id)
        if not state:
            return
        butler_state = self._get_butler_runtime_state(state)
        chase_state = butler_state.get("chase_state", {})
        if chase_state.get("status") == "blocked":
            chase_state["status"] = "pursuing"
            chase_state["target"] = new_target
            chase_state.pop("blocked_at", None)

    def has_butler_living_room_warning(self, session_id: str) -> bool:
        state = self.sessions.get(session_id)
        if not state:
            return False
        flags = state.get("world_state", {}).get("flags", {})
        return bool(flags.get("butler_living_room_warning_shown"))

    def _set_butler_living_room_warning_state(self, state: Dict[str, Any], reason: str):
        if not isinstance(state, dict):
            return

        world_state = state.setdefault("world_state", {})
        flags = world_state.setdefault("flags", {})
        flags["butler_living_room_warning_shown"] = True
        flags["butler_living_room_warning_reason"] = reason

        butler_state = self._get_butler_runtime_state(state)
        chase_state = butler_state.setdefault("chase_state", self._build_default_butler_chase_state())
        if not isinstance(chase_state, dict):
            chase_state = self._build_default_butler_chase_state()
            butler_state["chase_state"] = chase_state
        chase_state["active"] = False
        chase_state["status"] = "waiting"
        chase_state["target"] = None
        chase_state["activation_round"] = None
        chase_state["last_target_location"] = state.get("current_location")

    def trigger_butler_living_room_warning(self, session_id: str, reason: str) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state:
            return {}
        self._set_butler_living_room_warning_state(state, reason)
        self._sync_influence_dimensions(state)
        return copy.deepcopy(state)

    def should_warn_on_living_room_entry(self, session_id: str, target_key: str) -> bool:
        state = self.sessions.get(session_id)
        if not state or self.is_butler_active(session_id):
            return False
        if target_key != "living_room" or target_key != self.get_butler_location(session_id):
            return False
        return not self.has_butler_living_room_warning(session_id)

    def should_activate_butler_on_entry(self, session_id: str, target_key: str) -> bool:
        state = self.sessions.get(session_id)
        if not state or self.is_butler_active(session_id):
            return False
        if not self.has_butler_living_room_warning(session_id):
            return False
        return bool(target_key) and target_key == self.get_butler_location(session_id) == "living_room"

    def has_outside_lost_warning(self, session_id: str) -> bool:
        state = self.sessions.get(session_id)
        if not state:
            return False
        flags = state.get("world_state", {}).get("flags", {})
        return bool(flags.get("outside_lost_warning_shown"))

    def _set_outside_lost_warning_state(self, state: Dict[str, Any], reason: str):
        if not isinstance(state, dict):
            return

        world_state = state.setdefault("world_state", {})
        flags = world_state.setdefault("flags", {})
        flags["outside_lost_warning_shown"] = True
        flags["outside_lost_warning_reason"] = reason

        # Reveal "outside" on the map as "后门" (via hidden_name)
        visited = state.setdefault("visited_locations", [])
        if "outside" not in visited:
            visited.append("outside")

    def trigger_outside_lost_warning(self, session_id: str, reason: str) -> Dict[str, Any]:
        state = self.sessions.get(session_id)
        if not state:
            return {}
        self._set_outside_lost_warning_state(state, reason)
        self._sync_influence_dimensions(state)
        return copy.deepcopy(state)

    def should_warn_on_outside_entry(self, session_id: str, current_key: str, target_key: str) -> bool:
        state = self.sessions.get(session_id)
        if not state or self.get_ending_phase(session_id):
            return False
        if target_key != "outside":
            return False
        return not self.has_outside_lost_warning(session_id)

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

        butler_location = self.get_butler_location(session_id)
        current_location = state.get("current_location")
        return {
            "flags": {
                "butler_activated": True,
                "butler_activation_reason": reason,
            },
            "npc_updates": {
                BUTLER_NPC_NAME: {
                    "location": butler_location or current_location,
                    "chase_state": {
                        "active": True,
                        "status": "alerted",
                        "target": "player",
                        "activation_round": None,
                        "last_target_location": current_location,
                        "same_location_rounds": 0,
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
            "clues_found": state.get("world_state", {}).get("clues_found", []),
            "round_count": state.get("round_count", 0),
        }

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
        flags = state.get("world_state", {}).get("flags", {})
        clues = state.get("world_state", {}).get("clues_found", [])
        inventory = state.get("player", {}).get("inventory", [])
        # Check multiple possible flag names the RuleAI might use
        ritual_destroyed = (
            flags.get("ritual_destroyed")
            or flags.get("仪式已破坏")
            or "已破坏仪式" in clues
            or "已破坏仪式" in inventory
            or flags.get("carpet_burned")
            or flags.get("符咒地毯已焚毁")
        )
        if ritual_destroyed:
            influence = state.setdefault("influence_dimensions", self._build_default_influence_dimensions())
            influence["escape_success"] = True
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

        if state.get("current_location") == butler_state.get("location"):
            chase_state["same_location_rounds"] = int(chase_state.get("same_location_rounds", 0) or 0) + 1
            if chase_state["same_location_rounds"] >= 3:
                self._capture_player_by_butler_state(state, "stayed_with_butler_too_long")
                return True
            return False

        chase_state["same_location_rounds"] = 0
        return False

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
            return {}

        # 管家被门阻隔时不移动
        if chase_state.get("status") == "blocked":
            return {}

        chase_state["status"] = "pursuing"
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

        # 检查目标是否在有门房间内 - 管家被门阻隔
        module_data = state.get("module_data", {})
        dest_location_data = module_data.get("locations", {}).get(destination, {})
        if dest_location_data.get("has_door") and destination != previous_butler_location:
            # 目标进入了有门的房间，管家被阻隔在门外
            chase_state["status"] = "blocked"
            chase_state["blocked_at"] = destination
            chase_state["last_target_location"] = target_location
            return {}

        butler_state["location"] = destination
        chase_state["last_target_location"] = target_location

        if destination and destination != previous_butler_location:
            return {
                "npc_locations": {
                    BUTLER_NPC_NAME: destination
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
        if not bool(state.get("influence_dimensions", {}).get("butler_gaze")):
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
        for npc_name, npc_state in list(world_state.get("npcs", {}).items()):
            if not isinstance(npc_state, dict):
                world_state["npcs"][npc_name] = {
                    "memory": self._build_initial_npc_memory()
                }
                continue
            npc_state.setdefault("memory", self._build_initial_npc_memory())
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
                for npc_name, update in changes["npc_updates"].items():
                    if npc_name not in npc_state:
                        npc_state[npc_name] = {}
                    if isinstance(update, dict):
                        # 提取trust_delta（瞬态字段，不存入状态）
                        trust_delta = update.pop("trust_delta", None)
                        self._deep_merge_dict(npc_state[npc_name], update)
                        npc_state[npc_name].setdefault("memory", self._build_initial_npc_memory())
                        # 应用信任增量
                        if isinstance(trust_delta, (int, float)):
                            current_trust = float(npc_state[npc_name].get("trust_level", 0.0))
                            npc_state[npc_name]["trust_level"] = round(
                                max(0.0, min(1.0, current_trust + trust_delta)), 2
                            )
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
                for entity_name, update in changes["threat_entity_updates"].items():
                    if entity_name not in npc_state:
                        npc_state[entity_name] = {}
                    if isinstance(update, dict):
                        self._deep_merge_dict(npc_state[entity_name], update)
                    elif isinstance(update, str):
                        npc_state[entity_name]["location"] = update

        # 保存节奏AI上下文（阶段判断+世界变化）
        state["rhythm_context"].append({
            "round": state["round_count"],
            "stage_assessment": rhythm_result.get("stage_assessment", ""),
            "world_changes": rhythm_result.get("world_changes", {})
        })
        runtime_changes = {}
        if not bool(state.get("influence_dimensions", {}).get("butler_gaze")):
            runtime_changes = self._advance_butler_chase(state)
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
            if leads_to and requires:
                # 检查requires中的所有物品是否都已获得
                if not all(req in all_items for req in requires):
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

        return any(cond in all_items for cond in conditions)

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
            available_moves.add(neighbor_key)

        return available_moves

    def _get_available_moves(self, session_id: str) -> Set[str]:
        """返回当前地点可以直接前往的相邻地点。"""
        state = self.sessions.get(session_id)
        if not state:
            return set()

        available_moves = set(self._get_adjacent_moves(session_id))
        if self.is_butler_active(session_id):
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

        # BFS检查可达性
        available_moves = self._get_available_moves(session_id)
        if target_key not in available_moves:
            return {"success": False, "message": "目标位置不可达（路径被锁定）"}

        # 执行移动
        adjacent_moves = self._get_adjacent_moves(session_id)
        available_moves = self._get_available_moves(session_id)
        if target_key not in available_moves:
            if self.is_butler_active(session_id) and target_key not in adjacent_moves:
                return {"success": False, "message": "管家已被激活。现在你只能逐格移动到相邻场景。"}
            return {"success": False, "message": "目标位置不可达（路径被锁定）"}

        if self.should_warn_on_living_room_entry(session_id, target_key):
            self.trigger_butler_living_room_warning(session_id, "player_attempted_first_entry_to_living_room")
            return {
                "success": False,
                "warning_blocked": True,
                "message": self._get_module_special_message(
                    session_id,
                    "first_living_room_entry_blocked",
                    LIVING_ROOM_FIRST_ENTRY_WARNING_MESSAGE,
                ),
            }

        if self.should_warn_on_outside_entry(session_id, current, target_key):
            self.trigger_outside_lost_warning(session_id, "player_attempted_first_exit_through_kitchen_backdoor")
            return {
                "success": False,
                "warning_blocked": True,
                "message": self._get_module_special_message(
                    session_id,
                    "first_outside_entry_blocked",
                    OUTSIDE_LOST_WARNING_MESSAGE,
                ),
            }

        dodge_result = None
        movement_note = None
        if self.is_butler_active(session_id):
            butler_location = self.get_butler_location(session_id)
            needs_dodge = current == butler_location or target_key == butler_location
            if needs_dodge:
                dodge_result = self._roll_skill_check(state.get("player", {}), "闪避", "普通")
                if not dodge_result.get("success"):
                    self.capture_player_by_butler(session_id, "dodge_failed")
                    return {
                        "success": False,
                        "caught": True,
                        "message": "你试图从管家身边脱身，却被那具不自然的人形慢慢逼住。下一瞬，它强迫你迎上了那道目光。",
                        "check_result": dodge_result,
                    }
                movement_note = "你在那具迟缓却精准的人形逼近前猛地侧身，从它的封锁里惊险脱出。"

        previous_location = current
        state["current_location"] = target_key

        if self.is_butler_active(session_id):
            butler_state = self._get_butler_runtime_state(state)
            chase_state = butler_state.setdefault("chase_state", self._build_default_butler_chase_state())
            if isinstance(chase_state, dict) and chase_state.get("target", "player") == "player":
                # Butler trails the player's last room, not the room just entered.
                chase_state["last_target_location"] = previous_location

        # 跟随状态的NPC一起移动
        for npc_name, npc_data in state["world_state"].get("npcs", {}).items():
            if isinstance(npc_data, dict) and npc_data.get("companion_state") == "follow":
                npc_data["location"] = target_key

        # 标记已访问
        if target_key not in state["visited_locations"]:
            state["visited_locations"].append(target_key)

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
