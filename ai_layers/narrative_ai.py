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

    async def generate(self, rule_result: dict, rhythm_result: dict, narrative_history: list, history: list = None) -> dict:
        """
        生成最终叙述文本和总结

        Args:
            rule_result: 规则AI的判定结果
            rhythm_result: 节奏AI的剧情进展
            narrative_history: 历史总结列表
            history: 对话历史

        Returns:
            {"narrative": "叙述文本", "summary": "本轮总结"}
        """
        if history is None:
            history = []
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

    def _build_history_text(self, narrative_history: list) -> str:
        """构建历史文本：10轮内用完整文案，超过10轮的用小总结替换"""
        history_list = list(narrative_history) if narrative_history else []
        if not history_list:
            return "游戏刚开始"

        total = len(history_list)
        cutoff = max(0, total - 10)  # 10轮以内保留完整文案

        parts = []
        for i, entry in enumerate(history_list):
            if isinstance(entry, dict):
                round_num = entry.get("round", i + 1)
                if i < cutoff:
                    parts.append(f"[第{round_num}轮] {entry.get('summary', '')}")
                else:
                    parts.append(f"[第{round_num}轮] {entry.get('narrative', entry.get('summary', ''))}")
            else:
                # 兼容旧格式（纯字符串）
                parts.append(str(entry))

        return "\n\n".join(parts)

    def _build_prompt(self, rule_result: dict, rhythm_result: dict, narrative_history: list):
        """构建文案AI的提示词"""
        rhythm_result = self._normalize_rhythm_result(rhythm_result)

        # 规则判定信息
        rule_info = ""
        if rule_result.get("check_type"):
            rule_info = f"""规则判定：
- 技能: {rule_result.get('skill')}
- 难度: {rule_result.get('difficulty')}
- 投骰: {rule_result.get('roll')}/{rule_result.get('threshold')}
- 结果: {rule_result.get('result_description')}"""

        # 节奏AI信息（完整传递场景/物品字段和氛围指南）
        location_context = rhythm_result.get("location_context", {})
        object_context = rhythm_result.get("object_context", None)
        atmosphere_guide = rhythm_result.get("atmosphere_guide", {})
        feasible = rhythm_result.get("feasible", True)
        hint = rhythm_result.get("hint", None)
        stage_assessment = rhythm_result.get("stage_assessment", "")

        rhythm_info = f"""剧情信息：
- 行动可行: {feasible}
- 阶段判断: {stage_assessment}
- 当前场景字段: {json.dumps(location_context, ensure_ascii=False)}
- 涉及物品字段: {json.dumps(object_context, ensure_ascii=False) if object_context else '无'}
- 氛围指南: {json.dumps(atmosphere_guide, ensure_ascii=False)}"""

        if not feasible and hint:
            rhythm_info += f"\n- 不可行原因（供参考，请自然融入叙述）: {hint}"

        location = location_context.get("name", "未知场景")

        # 使用配置中的提示词模板
        prompt_template = self.config.get("narrative_ai_prompt", "").strip()
        if not prompt_template:
            prompt_template = self.prompts.get("narrative_ai_prompt", "")

        if not prompt_template:
            logger.error("[NarrativeAI] 未找到文案AI提示词")
            return ""

        # 替换占位符
        prompt = prompt_template.replace("{rule_info}", rule_info)
        prompt = prompt.replace("{rhythm_info}", rhythm_info)
        prompt = prompt.replace("{location}", location)

        return prompt

    def _normalize_rhythm_result(self, rhythm_result: dict) -> dict:
        """兜底规范化节奏层输出，避免字段类型异常导致文案层崩溃"""
        if not isinstance(rhythm_result, dict):
            return {}

        normalized = dict(rhythm_result)
        if not isinstance(normalized.get("location_context"), dict):
            normalized["location_context"] = {}
        if normalized.get("object_context") is not None and not isinstance(normalized.get("object_context"), dict):
            normalized["object_context"] = None
        if not isinstance(normalized.get("atmosphere_guide"), dict):
            normalized["atmosphere_guide"] = {}
        return normalized

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
