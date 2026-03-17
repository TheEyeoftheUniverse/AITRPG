from astrbot.api import logger
from astrbot.api.star import Context
import json


class NarrativeAI:
    """文案AI - 负责生成沉浸式叙述文本"""

    def __init__(self, context: Context, provider_name: str = None):
        self.context = context
        self.provider_name = provider_name

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
            response = await provider.text_chat(prompt, [])

            # 解析JSON
            result = json.loads(response)

            logger.info(f"[NarrativeAI] 文案生成完成，长度: {len(result.get('narrative', ''))}")
            return result

        except json.JSONDecodeError:
            logger.warning(f"[NarrativeAI] JSON解析失败。响应: {response}")
            # 如果JSON解析失败，尝试直接使用响应作为叙述
            return {
                "narrative": response,
                "summary": "玩家进行了探索"
            }
        except Exception as e:
            logger.error(f"[NarrativeAI] 文案生成出错: {e}")
            return self._get_default_narrative(rule_result, rhythm_result)

    def _build_prompt(self, rule_result: dict, rhythm_result: dict, narrative_history: list):
        """构建文案AI的提示词"""
        # 历史总结
        history_text = "\n".join(narrative_history[-5:]) if narrative_history else "游戏刚开始"

        # 规则判定信息
        rule_info = ""
        if rule_result.get("check_type"):
            rule_info = f"""
规则判定：
- 技能: {rule_result.get('skill')}
- 难度: {rule_result.get('difficulty')}
- 投骰: {rule_result.get('roll')}/{rule_result.get('threshold')}
- 结果: {rule_result.get('result_description')}
"""

        # 节奏AI信息
        rhythm_info = ""
        if rhythm_result.get("feasible"):
            outcome = rhythm_result.get("success_outcome" if rule_result.get("success", True) else "failure_outcome", {})
            rhythm_info = f"""
剧情进展：
- 可行性: {rhythm_result.get('reason')}
- 结果: {outcome.get('description', '继续探索')}
- 进度: {int(rhythm_result.get('current_progress', 0) * 100)}%
"""

        prompt = f"""你是一个TRPG文案AI，负责生成沉浸式的克苏鲁风格叙述文本。

# 历史总结
{history_text}

# 本轮信息
{rule_info}
{rhythm_info}

# 你的任务
1. 根据规则判定结果和剧情进展，生成一段沉浸式的叙述文本（100-200字）
2. 保持克苏鲁恐怖氛围：昏暗、诡异、不安
3. 细节描写丰富，但不剧透后续剧情
4. 同时生成一个简短的总结（20字内），用于保存到历史

# 风格要求
- 使用第二人称"你"
- 描写环境细节和玩家感受
- 营造紧张感和恐怖氛围
- 不要过度夸张

# 输出格式（JSON）
{{
    "narrative": "你走近布满灰尘的书架，小心翼翼地翻找着。突然，你的手指触碰到一本与众不同的书——封面上沾着暗红色的血迹，散发着令人不安的气息。你的心跳加速，理智值微微下降...",
    "summary": "玩家搜查书架成功，发现血日记，SAN-2"
}}

只输出JSON，不要其他内容。"""

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
