from astrbot.api import logger
from astrbot.api.star import Context
import json


class RhythmAI:
    """节奏AI - 负责对比模组和控制剧情节奏"""

    def __init__(self, context: Context, provider_name: str = None, config: dict = None, module_data: dict = None):
        self.context = context
        self.provider_name = provider_name
        self.config = config or {}
        self.module_data = module_data or {}

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
        # 获取指定的LLM提供商
        provider = None
        if self.provider_name:
            provider = self.context.get_provider(self.provider_name)
            if not provider:
                logger.warning(f"[RhythmAI] 未找到提供商 {self.provider_name}，使用默认提供商")

        if not provider:
            provider = self.context.get_using_provider()

        if not provider:
            logger.error("[RhythmAI] 未找到LLM提供商")
            return self._get_default_result()

        # 构建提示词
        prompt = self._build_prompt(intent, player_input, game_state)

        try:
            # 调用LLM
            llm_response = await provider.text_chat(prompt, [])

            # 提取文本内容
            response_text = llm_response.completion_text if hasattr(llm_response, 'completion_text') else str(llm_response)

            # 清理markdown代码块标记
            response_text = response_text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            # 解析JSON
            result = json.loads(response_text)

            logger.info(f"[RhythmAI] 节奏AI输出: {result}")
            return result

        except json.JSONDecodeError:
            logger.warning(f"[RhythmAI] JSON解析失败。响应: {response_text}")
            return self._get_default_result()
        except Exception as e:
            logger.error(f"[RhythmAI] 节奏AI处理出错: {e}")
            return self._get_default_result()

    def _build_module_context(self, game_state: dict) -> str:
        """从模组数据动态生成当前场景的上下文"""
        current_location = game_state.get("current_location", "bedroom")

        # 获取当前场景信息
        locations = self.module_data.get("locations", {})
        location_data = locations.get(current_location, {})

        if not location_data:
            return "当前场景信息不可用"

        # 构建场景描述
        context_parts = []
        context_parts.append(f"当前场景：{location_data.get('name', current_location)}")
        context_parts.append(f"- 描述: {location_data.get('description', '无描述')}")
        context_parts.append(f"- 可交互物品：{', '.join(location_data.get('objects', []))}")
        context_parts.append(f"- 出口：{', '.join(location_data.get('exits', []))}")

        # 添加物品详细信息
        objects = self.module_data.get("objects", {})
        for obj_name in location_data.get("objects", []):
            obj_data = objects.get(obj_name, {})
            if obj_data:
                context_parts.append(f"\n物品\"{obj_name}\"：")
                if obj_data.get("check_required"):
                    context_parts.append(f"- 需要{obj_data.get('check_required')}检定（{obj_data.get('difficulty', '普通')}难度）")
                if obj_data.get("success_result"):
                    context_parts.append(f"- 成功：{obj_data.get('success_result')}")
                if obj_data.get("failure_result"):
                    context_parts.append(f"- 失败：{obj_data.get('failure_result')}")

        return "\n".join(context_parts)

    def _build_prompt(self, intent: dict, player_input: str, game_state: dict):
        """构建节奏AI的提示词"""
        current_location = game_state.get("current_location", "bedroom")
        progress = game_state.get("progress", 0.0)
        round_count = game_state.get("round_count", 0)
        clues_found = game_state.get("world_state", {}).get("clues_found", [])

        # 生成模组上下文
        module_context = self._build_module_context(game_state)

        # 使用配置中的提示词模板
        prompt_template = self.config.get("rhythm_ai_prompt", "").strip()
        if not prompt_template:
            prompt_template = """你是一个TRPG节奏AI，负责根据模组内容控制剧情节奏。

# 当前游戏状态
- 位置: {current_location}
- 进度: {progress}%
- 轮次: {round_count}
- 已发现线索: {clues_found}

# 玩家行动
- 原始输入: {player_input}
- 意图解析: {intent}

# 模组信息
{module_context}

# 你的任务
1. 判断玩家行动是否可行（是否在当前场景）
2. 找到模组中最匹配的物品/场景
3. **直接复制**模组中的success_result和failure_result作为description，一字不改
4. 决定是否需要检定（根据模组中的check_required字段）
5. 判断当前剧情阶段（探索/解谜/逃离/结局）
6. 更新游戏进度

# 核心规则
- description字段必须是模组原文的**完全复制**，不要改写、不要总结、不要添加内容
- 如果模组中没有完全匹配的物品，选择最接近的物品，使用其原文
- 如果完全没有相关内容，description设为null

# 输出格式（JSON）
{{
    "feasible": true/false,
    "reason": "可行性说明",
    "stage": "探索/解谜/逃离/结局",
    "check_required": "侦查/图书馆/聆听/null",
    "difficulty": "普通/困难/极难",
    "success_outcome": {{
        "description": "模组中的success_result原文",
        "clue": "线索名称",
        "progress_gain": 0.2
    }},
    "failure_outcome": {{
        "description": "模组中的failure_result原文",
        "consequence": "后果"
    }},
    "current_progress": {current_progress},
    "player_changes": {{
        "san": -2,
        "hp": 0
    }},
    "world_changes": {{
        "clues": ["线索名"]
    }},
    "hint": "提示内容（如果需要）",
    "style_context": {{
        "theme": "模组主题",
        "location": "当前场景名称",
        "atmosphere": 0.5
    }}
}}

只输出JSON，不要其他内容。"""

        # 替换占位符
        prompt = prompt_template.replace("{current_location}", current_location)
        prompt = prompt.replace("{progress}", str(int(progress * 100)))
        prompt = prompt.replace("{round_count}", str(round_count))
        prompt = prompt.replace("{clues_found}", str(clues_found))
        prompt = prompt.replace("{player_input}", player_input)
        prompt = prompt.replace("{intent}", json.dumps(intent, ensure_ascii=False))
        prompt = prompt.replace("{module_context}", module_context)
        prompt = prompt.replace("{current_progress}", str(progress + 0.1))

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
