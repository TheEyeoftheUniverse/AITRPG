from astrbot.api import logger
from astrbot.api.star import Context
import json
import os


class RhythmAI:
    """节奏AI - 负责对比模组和控制剧情节奏"""

    def __init__(self, context: Context, provider_name: str = None, config: dict = None):
        self.context = context
        self.provider_name = provider_name
        self.config = config or {}
        self.prompts = self._load_prompts()

    def _load_prompts(self):
        """加载AI提示词配置"""
        prompts_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ai_prompts.json")
        try:
            with open(prompts_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"[RhythmAI] 未找到提示词配置文件: {prompts_path}")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"[RhythmAI] 提示词配置文件JSON格式错误: {e}")
            return {}

    async def process(self, intent: dict, player_input: str, game_state: dict, module_data: dict, history: list = None) -> dict:
        """
        处理玩家行动，对比模组，控制剧情节奏

        Args:
            intent: 规则AI解析的意图
            player_input: 玩家原始输入
            game_state: 当前游戏状态
            history: 对话历史

        Returns:
            节奏AI输出JSON
        """
        if history is None:
            history = []
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
        prompt = self._build_prompt(intent, player_input, game_state, module_data)

        try:
            # 调用LLM
            llm_response = await provider.text_chat(prompt, history)

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
            result = self._normalize_result(result, module_data)

            logger.info(f"[RhythmAI] 节奏AI输出: {result}")
            return result

        except json.JSONDecodeError:
            logger.warning(f"[RhythmAI] JSON解析失败。响应: {response_text}")
            return self._get_default_result()
        except Exception as e:
            logger.error(f"[RhythmAI] 节奏AI处理出错: {e}")
            return self._get_default_result()

    def _build_module_context(self, game_state: dict, module_data: dict) -> str:
        """从模组数据动态生成当前场景的上下文（传递完整原始JSON字段）"""
        current_location = game_state.get("current_location", "master_bedroom")

        locations = module_data.get("locations", {})
        location_data = locations.get(current_location, {})

        if not location_data:
            return "当前场景信息不可用"

        # 当前场景完整字段
        context_parts = []
        context_parts.append("当前场景完整字段（原文）：")
        context_parts.append(json.dumps({current_location: location_data}, ensure_ascii=False, indent=2))

        # 当前场景内所有物品的完整字段
        objects = module_data.get("objects", {})
        scene_objects = {}
        for obj_name in location_data.get("objects", []):
            obj_data = objects.get(obj_name, {})
            if obj_data:
                scene_objects[obj_name] = obj_data

        if scene_objects:
            context_parts.append("\n场景内物品完整字段（原文）：")
            context_parts.append(json.dumps(scene_objects, ensure_ascii=False, indent=2))

        # 全模组物品位置索引（帮助节奏AI识别玩家模糊提及的物品）
        all_objects = module_data.get("objects", {})
        if all_objects:
            obj_location_index = {
                name: data.get("location", "未知")
                for name, data in all_objects.items()
            }
            context_parts.append("\n全模组物品初始位置索引（用于识别玩家模糊提及的物品是否属于本模组）：")
            context_parts.append(json.dumps(obj_location_index, ensure_ascii=False, indent=2))

        # 模组氛围指南（原文）
        atmosphere_guide = module_data.get("module_info", {}).get("atmosphere_guide", {})
        if atmosphere_guide:
            context_parts.append("\n模组氛围指南（原文，直接复制到输出的 atmosphere_guide 字段）：")
            context_parts.append(json.dumps(atmosphere_guide, ensure_ascii=False, indent=2))

        return "\n".join(context_parts)

    def _build_history_summaries(self, game_state: dict) -> str:
        """构建历史行动摘要文本"""
        narrative_history = list(game_state.get("narrative_history", []))
        if not narrative_history:
            return "暂无历史行动（游戏刚开始）"

        parts = []
        for entry in narrative_history:
            if isinstance(entry, dict):
                round_num = entry.get("round", "?")
                summary = entry.get("summary", "")
                parts.append(f"[第{round_num}轮] {summary}")
            else:
                parts.append(str(entry))

        return "\n".join(parts)

    def _build_prompt(self, intent: dict, player_input: str, game_state: dict, module_data: dict):
        """构建节奏AI的提示词"""
        current_location = game_state.get("current_location", "master_bedroom")
        round_count = game_state.get("round_count", 0)
        clues_found = game_state.get("world_state", {}).get("clues_found", [])

        stages = module_data.get("module_info", {}).get("stages", "")

        # 生成模组上下文（完整原始字段）
        module_context = self._build_module_context(game_state, module_data)

        # 生成历史行动摘要
        history_summaries = self._build_history_summaries(game_state)

        # 使用配置中的提示词模板
        prompt_template = self.config.get("rhythm_ai_prompt", "").strip()
        if not prompt_template:
            prompt_template = self.prompts.get("rhythm_ai_prompt", "")

        if not prompt_template:
            logger.error("[RhythmAI] 未找到节奏AI提示词")
            return ""

        # 替换占位符
        prompt = prompt_template.replace("{current_location}", current_location)
        prompt = prompt.replace("{round_count}", str(round_count))
        prompt = prompt.replace("{clues_found}", str(clues_found))
        prompt = prompt.replace("{player_input}", player_input)
        prompt = prompt.replace("{intent}", json.dumps(intent, ensure_ascii=False))
        prompt = prompt.replace("{module_context}", module_context)
        prompt = prompt.replace("{stages}", stages)
        prompt = prompt.replace("{history_summaries}", history_summaries)

        return prompt

    def _get_default_result(self):
        """获取默认结果（当AI调用失败时）"""
        return {
            "feasible": True,
            "hint": None,
            "location_context": {},
            "object_context": None,
            "atmosphere_guide": {},
            "stage_assessment": "无法判断当前剧情阶段",
            "world_changes": {}
        }

    def _normalize_result(self, result: dict, module_data: dict) -> dict:
        """规范化节奏AI输出，避免异常字段类型污染后续流程"""
        if not isinstance(result, dict):
            logger.warning(f"[RhythmAI] 节奏AI输出不是对象，已回退默认值: {result}")
            return self._get_default_result()

        normalized = self._get_default_result()
        normalized.update(result)

        normalized["feasible"] = bool(normalized.get("feasible", True))
        if normalized.get("hint") is not None and not isinstance(normalized.get("hint"), str):
            normalized["hint"] = str(normalized.get("hint"))
        if not isinstance(normalized.get("stage_assessment"), str):
            normalized["stage_assessment"] = str(normalized.get("stage_assessment", ""))

        locations = module_data.get("locations", {})
        objects = module_data.get("objects", {})
        atmosphere_guide = module_data.get("module_info", {}).get("atmosphere_guide", {})

        location_context = normalized.get("location_context")
        if isinstance(location_context, str):
            normalized["location_context"] = locations.get(location_context, {})
        elif not isinstance(location_context, dict):
            normalized["location_context"] = {}

        object_context = normalized.get("object_context")
        if isinstance(object_context, str):
            object_data = objects.get(object_context)
            normalized["object_context"] = (
                {"name": object_context, **object_data}
                if isinstance(object_data, dict) else None
            )
        elif object_context is not None and not isinstance(object_context, dict):
            normalized["object_context"] = None

        if not isinstance(normalized.get("atmosphere_guide"), dict):
            normalized["atmosphere_guide"] = atmosphere_guide if isinstance(atmosphere_guide, dict) else {}

        world_changes = normalized.get("world_changes")
        normalized["world_changes"] = world_changes if isinstance(world_changes, dict) else {}

        return normalized
