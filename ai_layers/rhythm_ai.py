from astrbot.api import logger
from astrbot.api.star import Context
import json
import os


class RhythmAI:
    """节奏AI - 负责对比模组和控制剧情节奏"""

    def __init__(self, context: Context, provider_name: str = None, config: dict = None, module_data: dict = None):
        self.context = context
        self.provider_name = provider_name
        self.config = config or {}
        self.module_data = module_data or {}
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
            prompt_template = self.prompts.get("rhythm_ai_prompt", "")

        if not prompt_template:
            logger.error("[RhythmAI] 未找到节奏AI提示词")
            return ""

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
