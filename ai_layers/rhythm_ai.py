from astrbot.api import logger
from astrbot.api.star import Context
import json


class RhythmAI:
    """节奏AI - 负责对比模组和控制剧情节奏"""

    def __init__(self, context: Context):
        self.context = context

    async def process(self, intent: dict, player_input: str, game_state: dict) -> dict:
        """
        处理玩家行动，对比模组，控制剧情节奏

        Args:
            intent: 规则AI解析的意图
            player_input: 玩家原始输入
            game_state: 当前游戏状态

        Returns:
            节奏AI输出JSON
        """
        # 获取LLM提供商（使用DeepSeek）
        provider = self.context.get_using_provider()
        if not provider:
            logger.error("[RhythmAI] 未找到LLM提供商")
            return self._get_default_result()

        # 构建提示词
        prompt = self._build_prompt(intent, player_input, game_state)

        try:
            # 调用LLM
            response = await provider.text_chat(prompt, [])

            # 解析JSON
            result = json.loads(response)

            logger.info(f"[RhythmAI] 节奏AI输出: {result}")
            return result

        except json.JSONDecodeError:
            logger.warning(f"[RhythmAI] JSON解析失败。响应: {response}")
            return self._get_default_result()
        except Exception as e:
            logger.error(f"[RhythmAI] 节奏AI处理出错: {e}")
            return self._get_default_result()

    def _build_prompt(self, intent: dict, player_input: str, game_state: dict):
        """构建节奏AI的提示词"""
        # 获取模组数据（从session_manager传入）
        current_location = game_state.get("current_location", "bedroom")
        progress = game_state.get("progress", 0.0)
        round_count = game_state.get("round_count", 0)
        clues_found = game_state.get("world_state", {}).get("clues_found", [])

        prompt = f"""你是一个TRPG节奏AI，负责根据模组内容控制剧情节奏。

# 当前游戏状态
- 位置: {current_location}
- 进度: {int(progress * 100)}%
- 轮次: {round_count}
- 已发现线索: {clues_found}

# 玩家行动
- 原始输入: {player_input}
- 意图解析: {json.dumps(intent, ensure_ascii=False)}

# 模组信息（简化版）
当前场景：卧室
- 可交互物品：日记、床、衣柜
- 出口：走廊

物品"日记"：
- 需要侦查检定（普通难度）
- 成功：发现日记，揭示宅邸秘密，进度+20%，SAN-2
- 失败：没找到有用的东西

# 你的任务
1. 判断玩家行动是否可行（是否在当前场景）
2. 决定是否需要检定，以及难度
3. 描述成功和失败的可能结果
4. 更新游戏进度
5. 如果玩家卡住（连续3轮无进展），给出提示

# 输出格式（JSON）
{{
    "feasible": true/false,
    "reason": "可行性说明",
    "check_required": "侦查/图书馆/聆听/null",
    "difficulty": "普通/困难/极难",
    "success_outcome": {{
        "description": "成功时的描述",
        "clue": "线索名称（如果有）",
        "progress_gain": 0.2
    }},
    "failure_outcome": {{
        "description": "失败时的描述",
        "consequence": "后果"
    }},
    "current_progress": {progress + 0.1},
    "player_changes": {{
        "san": -2,
        "hp": 0
    }},
    "world_changes": {{
        "clues": ["日记"]
    }},
    "hint": "提示内容（如果需要）"
}}

只输出JSON，不要其他内容。"""

        return prompt

    def _get_default_result(self):
        """获取默认结果（当AI调用失败时）"""
        return {
            "feasible": True,
            "reason": "继续探索",
            "check_required": None,
            "difficulty": "普通",
            "success_outcome": {
                "description": "你继续探索",
                "progress_gain": 0.05
            },
            "failure_outcome": {
                "description": "没有发现",
                "consequence": "无"
            },
            "current_progress": 0.05,
            "player_changes": {},
            "world_changes": {},
            "hint": None
        }
