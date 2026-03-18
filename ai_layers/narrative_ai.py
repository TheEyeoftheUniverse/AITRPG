from astrbot.api import logger
from astrbot.api.star import Context
import json
import os


class NarrativeAI:
    """文案AI - 负责生成沉浸式叙述文本"""

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
            logger.error(f"[NarrativeAI] 未找到提示词配置文件: {prompts_path}")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"[NarrativeAI] 提示词配置文件JSON格式错误: {e}")
            return {}

    async def generate(self, rule_result: dict, rhythm_result: dict, narrative_history: list) -> dict:
        """
        生成最终叙述文本和总结

        Args:
            rule_result: 规则AI的判定结果
            rhythm_result: 节奏AI的剧情进展
            narrative_history: 历史总结列表

        Returns:
            {"narrative": "叙述文本", "summary": "本轮总结"}
        """
        # 获取指定的LLM提供商
        provider = None
        if self.provider_name:
            provider = self.context.get_provider(self.provider_name)
            if not provider:
                logger.warning(f"[NarrativeAI] 未找到提供商 {self.provider_name}，使用默认提供商")

        if not provider:
            provider = self.context.get_using_provider()

        if not provider:
            logger.error("[NarrativeAI] 未找到LLM提供商")
            return self._get_default_narrative(rule_result, rhythm_result)

        # 构建提示词
        prompt = self._build_prompt(rule_result, rhythm_result, narrative_history)

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

            logger.info(f"[NarrativeAI] 文案生成完成，长度: {len(result.get('narrative', ''))}")
            return result

        except json.JSONDecodeError:
            logger.warning(f"[NarrativeAI] JSON解析失败。响应: {response_text}")
            # 如果JSON解析失败，尝试直接使用响应作为叙述
            return {
                "narrative": response_text,
                "summary": "玩家进行了探索"
            }
        except Exception as e:
            logger.error(f"[NarrativeAI] 文案生成出错: {e}")
            return self._get_default_narrative(rule_result, rhythm_result)

    def _build_prompt(self, rule_result: dict, rhythm_result: dict, narrative_history: list):
        """构建文案AI的提示词"""
        # 历史总结（将deque转换为list以支持切片）
        history_list = list(narrative_history) if narrative_history else []
        history_text = "\n".join(history_list[-5:]) if history_list else "游戏刚开始"

        # 规则判定信息
        rule_info = ""
        if rule_result.get("check_type"):
            rule_info = f"""规则判定：
- 技能: {rule_result.get('skill')}
- 难度: {rule_result.get('difficulty')}
- 投骰: {rule_result.get('roll')}/{rule_result.get('threshold')}
- 结果: {rule_result.get('result_description')}"""

        # 节奏AI信息
        rhythm_info = ""
        if rhythm_result.get("feasible"):
            outcome = rhythm_result.get("success_outcome" if rule_result.get("success", True) else "failure_outcome", {})
            rhythm_info = f"""剧情进展：
- 可行性: {rhythm_result.get('reason')}
- 结果: {outcome.get('description', '继续探索')}
- 进度: {int(rhythm_result.get('current_progress', 0) * 100)}%"""

        # 风格上下文（从节奏AI获取）
        style_context = rhythm_result.get("style_context", {})
        theme = style_context.get("theme", "克苏鲁恐怖")
        location = style_context.get("location", "未知场景")
        atmosphere = style_context.get("atmosphere", 0.5)

        # 使用配置中的提示词模板
        prompt_template = self.config.get("narrative_ai_prompt", "").strip()
        if not prompt_template:
            prompt_template = self.prompts.get("narrative_ai_prompt", "")

        if not prompt_template:
            logger.error("[NarrativeAI] 未找到文案AI提示词")
            return ""

        # 替换占位符
        prompt = prompt_template.replace("{history_text}", history_text)
        prompt = prompt.replace("{rule_info}", rule_info)
        prompt = prompt.replace("{rhythm_info}", rhythm_info)
        prompt = prompt.replace("{theme}", theme)
        prompt = prompt.replace("{location}", location)
        prompt = prompt.replace("{atmosphere}", str(atmosphere))

        return prompt

    def _get_default_narrative(self, rule_result: dict, rhythm_result: dict):
        """获取默认叙述（当AI调用失败时）"""
        # 简单的模板生成
        if rule_result.get("success"):
            narrative = "你的行动取得了成功。"
        else:
            narrative = "你的行动没有取得预期的效果。"

        return {
            "narrative": narrative,
            "summary": "玩家进行了探索"
        }
