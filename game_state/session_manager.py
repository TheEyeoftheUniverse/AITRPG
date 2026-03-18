import json
import os
from collections import deque
from typing import Dict, Any


class SessionManager:
    """游戏会话管理器"""

    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.module_data = self._load_module()

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

    def create_session(self, session_id: str):
        """创建新游戏会话"""
        self.sessions[session_id] = {
            "session_id": session_id,
            "current_location": "master_bedroom",
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
                "npcs": {
                    "管家": {
                        "attitude": "中立",
                        "trust_level": 0.5
                    }
                },
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
            "narrative_history": deque(maxlen=15)  # 文案AI保存历史总结
        }

    def has_session(self, session_id: str) -> bool:
        """检查会话是否存在"""
        return session_id in self.sessions

    def get_session(self, session_id: str) -> Dict[str, Any]:
        """获取会话状态"""
        return self.sessions.get(session_id)

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

        # 保存节奏AI上下文（阶段判断+世界变化）
        state["rhythm_context"].append({
            "round": state["round_count"],
            "stage_assessment": rhythm_result.get("stage_assessment", ""),
            "world_changes": rhythm_result.get("world_changes", {})
        })

    def add_narrative_summary(self, session_id: str, narrative: str, summary: str):
        """添加文案记录到历史"""
        if session_id not in self.sessions:
            return

        state = self.sessions[session_id]
        state["narrative_history"].append({
            "round": state["round_count"],
            "narrative": narrative,
            "summary": summary
        })

    def get_opening(self) -> str:
        """获取游戏开场白"""
        # 从模组数据中读取开场白
        opening_text = self.module_data.get("module_info", {}).get("opening", "")

        if not opening_text:
            # 如果模组没有开场白，使用默认的
            opening_text = """你是一名私家侦探，接到委托调查一座废弃的宅邸。

当你推开吱呀作响的大门，一股霉味扑面而来。你发现自己身处一间昏暗的卧室中，窗外传来诡异的声响...

你的目标是找到真相，并活着离开这里。"""

        # 获取模组名称
        module_name = self.module_data.get("module_info", {}).get("name", "AI驱动TRPG")

        return f"""🎲 {module_name}

{opening_text}

━━━━━━━━━━━━━━━━
👤 调查员
  理智: 65
  生命: 12
  技能: 侦查60 图书馆40 聆听50

📍 当前位置: 卧室
━━━━━━━━━━━━━━━━"""

    def get_module_data(self):
        """获取模组数据"""
        return self.module_data
