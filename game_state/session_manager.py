import json
import os
import copy
from collections import deque
from typing import Dict, Any, List, Set


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
                "flags": {
                    "door_unlocked": False,
                    "truth_revealed": False
                }
            },

            "influence_dimensions": {
                "escape_success": False,
                "npc_together": False,
                "truth_revealed": False
            },

            # 三层AI的上下文
            "rhythm_context": [],  # 节奏AI保存游戏状态变化
            "narrative_history": deque(maxlen=15),  # 文案AI保存历史总结
            "visited_locations": [initial_location],  # 已访问过的location key列表
        }
        self.sessions[session_id]["player"] = self._build_default_player_state()

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
        for npc_name, npc_data in module_data.get("npcs", {}).items():
            npc_states[npc_name] = {
                "attitude": npc_data.get("initial_attitude", "中立"),
                "trust_level": 0.0,
                "memory": self._build_initial_npc_memory(),
            }
            if npc_data.get("location"):
                npc_states[npc_name]["location"] = npc_data["location"]
        return npc_states

    def _build_initial_npc_memory(self) -> Dict[str, Any]:
        return {
            "player_facts": {},
            "evidence_seen": [],
            "promises": [],
            "topics_discussed": [],
            "pending_questions": [],
            "conversation_flags": {},
            "last_impression": {},
        }

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
            narrative_history = deque(narrative_history, maxlen=15)
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

        restored_state["influence_dimensions"] = dict(restored_state.get("influence_dimensions") or {
            "escape_success": False,
            "npc_together": False,
            "truth_revealed": False
        })

        self.sessions[session_id] = restored_state

    def delete_session(self, session_id: str):
        """删除会话"""
        if session_id in self.sessions:
            del self.sessions[session_id]

    def update_state(self, session_id: str, rhythm_result: Dict[str, Any]):
        """根据节奏AI的结果更新游戏状态"""
        if session_id not in self.sessions:
            return

        state = self.sessions[session_id]

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
                        self._deep_merge_dict(npc_state[npc_name], update)
                        npc_state[npc_name].setdefault("memory", self._build_initial_npc_memory())
                    elif isinstance(update, str):
                        npc_state[npc_name]["location"] = update

        # 保存节奏AI上下文（阶段判断+世界变化）
        state["rhythm_context"].append({
            "round": state["round_count"],
            "stage_assessment": rhythm_result.get("stage_assessment", ""),
            "world_changes": rhythm_result.get("world_changes", {})
        })

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

    def _get_available_moves(self, session_id: str) -> Set[str]:
        """返回当前地点可以直接前往的相邻地点。"""
        state = self.sessions.get(session_id)
        if not state:
            return set()

        current = state["current_location"]
        graph = self._get_adjacency_graph(self.get_module_data(session_id))
        locked_exits = self._get_locked_exits(session_id)
        visited_locations = set(state.get("visited_locations", []))
        available_moves = set()

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
        state["current_location"] = target_key

        # 标记已访问
        if target_key not in state["visited_locations"]:
            state["visited_locations"].append(target_key)

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

        return {
            "locations": visible_locations,
            "edges": edges,
            "current_location": state["current_location"],
            "reachable": list(reachable),
        }

    def get_module_data(self, session_id: str = None):
        """获取模组数据"""
        if session_id and session_id in self.sessions:
            module_data = self.sessions[session_id].get("module_data")
            if module_data:
                return module_data

        return self.default_module_data

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
                existing = base.get(key)
                if not isinstance(existing, list):
                    existing = []
                    base[key] = existing
                for item in value:
                    if item not in existing:
                        existing.append(item)
                continue

            base[key] = value
