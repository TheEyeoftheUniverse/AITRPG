import json
import os
from collections import deque
from typing import Dict, Any


class SessionManager:
    """游戏会话管理器"""

    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.module_data = self._load_module()

    def _load_module(self):
        """加载模组数据"""
        # 暂时返回硬编码的简易模组，后续从JSON文件加载
        return {
            "module_info": {
                "name": "逃离诡宅",
                "theme": "克苏鲁恐怖",
                "target_rounds": 30
            },
            "locations": {
                "bedroom": {
                    "name": "卧室",
                    "description": "昏暗的房间，空气中弥漫着霉味",
                    "objects": ["日记", "床", "衣柜"],
                    "exits": ["走廊"],
                    "danger_level": 1
                },
                "hallway": {
                    "name": "走廊",
                    "description": "狭长的走廊，墙上挂着破旧的画像",
                    "objects": ["画像", "门"],
                    "exits": ["卧室", "客厅", "地下室"],
                    "danger_level": 2
                }
            },
            "objects": {
                "日记": {
                    "type": "clue",
                    "check_required": "侦查",
                    "difficulty": "普通",
                    "clue_value": 0.2,
                    "san_cost": -2,
                    "success_result": "发现日记，揭示了宅邸的黑暗秘密",
                    "failure_result": "没找到有用的东西"
                }
            },
            "npcs": {
                "管家": {
                    "initial_attitude": "中立",
                    "can_escape_together": True,
                    "key_info": "知道后门密码"
                }
            },
            "escape_conditions": {
                "minimum_progress": 0.6,
                "required_items": ["钥匙"],
                "optional": ["NPC同行", "真相揭露"]
            }
        }

    def create_session(self, session_id: str):
        """创建新游戏会话"""
        self.sessions[session_id] = {
            "session_id": session_id,
            "current_location": "bedroom",
            "progress": 0.0,
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

        # 更新进度
        if "current_progress" in rhythm_result:
            state["progress"] = rhythm_result["current_progress"]

        # 更新轮次
        state["round_count"] += 1

        # 更新玩家状态
        if "player_changes" in rhythm_result:
            changes = rhythm_result["player_changes"]
            if "san" in changes:
                state["player"]["san"] += changes["san"]
            if "hp" in changes:
                state["player"]["hp"] += changes["hp"]

        # 更新世界状态
        if "world_changes" in rhythm_result:
            changes = rhythm_result["world_changes"]
            if "clues" in changes:
                for clue in changes["clues"]:
                    if clue not in state["world_state"]["clues_found"]:
                        state["world_state"]["clues_found"].append(clue)

        # 保存节奏AI上下文
        state["rhythm_context"].append({
            "round": state["round_count"],
            "progress": state["progress"],
            "changes": rhythm_result
        })

    def add_narrative_summary(self, session_id: str, summary: str):
        """添加文案总结到历史"""
        if session_id not in self.sessions:
            return

        state = self.sessions[session_id]
        state["narrative_history"].append(summary)

    def get_opening(self) -> str:
        """获取游戏开场白"""
        return """你是一名私家侦探，接到委托调查一座废弃的宅邸。

当你推开吱呀作响的大门，一股霉味扑面而来。你发现自己身处一间昏暗的卧室中，窗外传来诡异的声响...

你的目标是找到真相，并活着离开这里。

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
