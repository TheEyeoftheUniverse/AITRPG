from astrbot.api import logger
from astrbot.api.star import Context
import json
import random
import os


class RuleAI:
    """规则AI - 负责意图解析和规则判定"""

    def __init__(self, context: Context, provider_name: str = None, config: dict = None):
        self.context = context
        self.provider_name = provider_name
        self.config = config or {}
        self.rules = self._load_rules()
        self.prompts = self._load_prompts()

    def _load_rules(self):
        """加载COC规则"""
        return """
# COC 7版核心规则

## 技能检定
- 投1d100，结果 ≤ 技能值则成功
- 困难检定：技能值/2
- 极难检定：技能值/5
- 大成功：投出01-05
- 大失败：投出96-100

## SAN检定
- 遭遇恐怖事件时触发
- 投1d100，结果 ≤ SAN值则成功
- 失败扣除SAN值（根据事件严重程度）
- SAN归零导致疯狂

## 调查规则
- 侦查：发现物理线索
- 图书馆：查阅资料
- 聆听：听到声音
"""

    def _load_prompts(self):
        """加载AI提示词配置"""
        prompts_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ai_prompts.json")
        try:
            with open(prompts_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"[RuleAI] 未找到提示词配置文件: {prompts_path}")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"[RuleAI] 提示词配置文件JSON格式错误: {e}")
            return {}

    async def parse_intent(self, player_input: str) -> dict:
        """
        第一次调用：解析玩家意图

        Args:
            player_input: 玩家输入的文本

        Returns:
            意图JSON: {"intent": "search", "target": "书架", "category": "调查"}
        """
        # 获取指定的LLM提供商
        provider = None
        if self.provider_name:
            provider = self.context.get_provider(self.provider_name)
            if not provider:
                logger.warning(f"[RuleAI] 未找到提供商 {self.provider_name}，使用默认提供商")

        if not provider:
            provider = self.context.get_using_provider()

        if not provider:
            logger.error("[RuleAI] 未找到LLM提供商")
            return {"intent": "unknown", "target": None, "category": "其他"}

        # 使用配置中的提示词模板，如果没有则使用默认
        prompt_template = self.config.get("rule_ai_intent_prompt", "").strip()
        if not prompt_template:
            prompt_template = self.prompts.get("rule_ai_intent_prompt", "")

        if not prompt_template:
            logger.error("[RuleAI] 未找到意图解析提示词")
            return {"intent": "unknown", "target": None, "category": "其他"}

        prompt = prompt_template.replace("{player_input}", player_input)

        try:
            # 调用LLM
            llm_response = await provider.text_chat(prompt, [])

            # 提取文本内容
            response_text = llm_response.completion_text if hasattr(llm_response, 'completion_text') else str(llm_response)

            # 解析JSON
            result = json.loads(response_text)
            return result

        except json.JSONDecodeError:
            logger.warning(f"[RuleAI] JSON解析失败，使用默认值。响应: {response_text}")
            return {
                "intent": "unknown",
                "target": player_input,
                "category": "其他"
            }
        except Exception as e:
            logger.error(f"[RuleAI] 意图解析出错: {e}")
            return {
                "intent": "unknown",
                "target": player_input,
                "category": "其他"
            }

    async def judge(self, rhythm_result: dict, player_state: dict) -> dict:
        """
        第二次调用：执行规则判定

        Args:
            rhythm_result: 节奏AI的输出
            player_state: 玩家状态

        Returns:
            判定结果JSON
        """
        # 如果不需要检定，直接返回
        if not rhythm_result.get("check_required"):
            return {
                "check_type": None,
                "success": True,
                "result_description": "无需检定"
            }

        skill_name = rhythm_result.get("check_required")
        difficulty = rhythm_result.get("difficulty", "普通")

        # 获取玩家技能值
        player_skill = player_state.get("skills", {}).get(skill_name, 50)

        # 计算难度修正
        if difficulty == "困难":
            threshold = player_skill // 2
        elif difficulty == "极难":
            threshold = player_skill // 5
        else:  # 普通
            threshold = player_skill

        # 投骰
        roll = random.randint(1, 100)

        # 判定成功
        success = roll <= threshold

        # 大成功/大失败
        critical_success = roll <= 5
        critical_failure = roll >= 96

        result = {
            "check_type": "skill_check",
            "skill": skill_name,
            "difficulty": difficulty,
            "player_skill": player_skill,
            "threshold": threshold,
            "roll": roll,
            "success": success,
            "critical_success": critical_success,
            "critical_failure": critical_failure,
            "result_description": self._get_result_description(success, critical_success, critical_failure)
        }

        logger.info(f"[RuleAI] 判定结果: {skill_name} {roll}/{threshold} {'成功' if success else '失败'}")

        return result

    async def resolve_check(self, rhythm_result: dict, player_state: dict) -> dict:
        """根据模组物品字段执行代码检定。"""
        object_context = rhythm_result.get("object_context")
        if not isinstance(object_context, dict):
            return {
                "check_type": None,
                "success": True,
                "result_description": "无需检定"
            }

        skill_name = object_context.get("check_required")
        difficulty = self._normalize_difficulty(object_context.get("difficulty"))
        if not skill_name or difficulty == "无需判定":
            return {
                "check_type": "auto_check" if skill_name else None,
                "skill": skill_name,
                "difficulty": difficulty,
                "success": True,
                "result_description": "自动成功" if skill_name else "无需检定"
            }

        player_skill = int(player_state.get("skills", {}).get(skill_name, 0))
        threshold = self._get_threshold(player_skill, difficulty)
        roll = random.randint(1, 100)
        success = roll <= threshold
        critical_success = roll <= 5
        critical_failure = roll >= 96

        result = {
            "check_type": "skill_check",
            "skill": skill_name,
            "difficulty": difficulty,
            "player_skill": player_skill,
            "threshold": threshold,
            "roll": roll,
            "success": success,
            "critical_success": critical_success,
            "critical_failure": critical_failure,
            "result_description": self._get_result_description(success, critical_success, critical_failure)
        }

        logger.info(f"[RuleAI] 判定结果: {skill_name} {roll}/{threshold} {'成功' if success else '失败'}")
        return result

    def _normalize_difficulty(self, difficulty) -> str:
        """将模组中的难度描述归一化为代码判定等级。"""
        text = str(difficulty or "").strip()
        if not text:
            return "无需判定"
        if "无需判定" in text or "直接成功" in text or "自动成功" in text:
            return "无需判定"
        if "极难" in text:
            return "极难"
        if "困难" in text:
            return "困难"
        return "普通"

    def _get_threshold(self, player_skill: int, difficulty: str) -> int:
        """根据难度计算成功阈值。"""
        if difficulty == "困难":
            return max(1, player_skill // 2)
        if difficulty == "极难":
            return max(1, player_skill // 5)
        return player_skill

    def _get_result_description(self, success, critical_success, critical_failure):
        """获取判定结果描述"""
        if critical_success:
            return "大成功！"
        elif critical_failure:
            return "大失败！"
        elif success:
            return "成功"
        else:
            return "失败"
