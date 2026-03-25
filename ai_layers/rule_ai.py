from astrbot.api import logger
from astrbot.api.star import Context
from ..game_state.location_context import (
    build_adjacent_locations_context,
    build_runtime_location_context,
    get_entity_first_appearance,
    get_entity_profile_text,
    get_entity_trust_threshold,
    get_cross_wall_npcs,
    get_module_npcs,
    get_module_threat_entities,
    get_primary_pursuer_name,
    get_primary_pursuer_settings,
    is_threat_entity,
)
from .usage_metrics import extract_usage_metrics

import json
import os
import random
from typing import Any, Dict


class RuleAI:
    """Rule layer: action parsing, feasibility, checks, and hard outcomes."""

    # 引号字符集：中文引号、日式方框引号、ASCII双引号
    DIALOGUE_QUOTE_CHARS = set('\u201c\u201d\u2018\u2019\u300c\u300d\u300e\u300f\u0022')

    def __init__(self, context: Context, provider_name: str = None, config: dict = None):
        self.context = context
        self.provider_name = provider_name
        self.config = config or {}
        self.rules = self._load_rules()
        self.prompts = self._load_prompts()
        self._call_metrics = {}

    def pop_call_metric(self, trace_id: str) -> dict:
        if not trace_id:
            return {}
        return self._call_metrics.pop(trace_id, {})

    def is_dialogue_input(self, text: str) -> bool:
        """检测玩家输入是否包含引号（中文引号、日式方框引号、ASCII双引号），
        如果包含则硬编码判定为"对话"，防止AI将台词误判为实际行动。"""
        return any(ch in self.DIALOGUE_QUOTE_CHARS for ch in (text or ""))

    def _npc_can_speak(self, npc_data: dict) -> bool:
        return isinstance((npc_data or {}).get("dialogue"), dict)

    def _load_rules(self):
        return """
# COC 7 core rules
- Skill check: roll d100, success if roll <= threshold
- Hard: threshold = skill / 2
- Extreme: threshold = skill / 5
- Critical success: 01-05
- Fumble: 96-100
"""

    def _load_prompts(self):
        prompts_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ai_prompts.json")
        try:
            with open(prompts_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"[RuleAI] Prompt config not found: {prompts_path}")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"[RuleAI] Prompt config JSON error: {e}")
            return {}

    def _get_provider(self):
        provider = None
        if self.provider_name:
            provider = self.context.get_provider(self.provider_name)
            if not provider:
                logger.warning(f"[RuleAI] Provider {self.provider_name} not found, fallback to current provider")
        if not provider:
            provider = self.context.get_using_provider()
        return provider

    def _strip_json_fence(self, text: str) -> str:
        text = (text or "").strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    async def parse_intent(self, player_input: str, trace_id: str = None) -> dict:
        provider = self._get_provider()
        if not provider:
            logger.error("[RuleAI] No provider available for parse_intent")
            raise RuntimeError("规则AI意图解析失败：未找到可用 LLM provider，请使用重试按钮。")

        prompt_template = self.config.get("rule_ai_intent_prompt", "").strip()
        if not prompt_template:
            prompt_template = self.prompts.get("rule_ai_intent_prompt", "")

        if not prompt_template:
            logger.error("[RuleAI] rule_ai_intent_prompt not found")
            raise RuntimeError("规则AI意图解析失败：未找到可用提示词，请使用重试按钮。")

        prompt = prompt_template.replace("{player_input}", player_input)

        try:
            llm_response = await provider.text_chat(prompt=prompt, contexts=[])
            response_text = llm_response.completion_text if hasattr(llm_response, "completion_text") else str(llm_response)
            if trace_id:
                self._call_metrics[trace_id] = extract_usage_metrics(llm_response, prompt, response_text)
            return json.loads(self._strip_json_fence(response_text))
        except json.JSONDecodeError as e:
            logger.warning(f"[RuleAI] parse_intent JSON decode failed: {player_input}")
            raise RuntimeError("规则AI意图解析失败：返回结果不是合法 JSON，请使用重试按钮。") from e
        except Exception as e:
            logger.error(f"[RuleAI] parse_intent error: {e}")
            raise RuntimeError("规则AI意图解析失败，请使用重试按钮。") from e

    async def adjudicate_action(
        self,
        player_input: str,
        intent: dict,
        game_state: dict,
        module_data: dict,
        trace_id: str = None,
    ) -> dict:
        provider = self._get_provider()
        if not provider:
            logger.error("[RuleAI] No provider available for adjudicate_action")
            raise RuntimeError("规则AI动作裁定失败：未找到可用 LLM provider，请使用重试按钮。")

        prompt_template = self.config.get("rule_ai_action_prompt", "").strip()
        if not prompt_template:
            prompt_template = self.prompts.get("rule_ai_action_prompt", "")

        if not prompt_template:
            logger.error("[RuleAI] rule_ai_action_prompt not found")
            raise RuntimeError("规则AI动作裁定失败：未找到可用提示词，请使用重试按钮。")

        prompt = self._build_action_prompt(prompt_template, player_input, intent, game_state, module_data)

        try:
            llm_response = await provider.text_chat(prompt=prompt, contexts=[])
            response_text = llm_response.completion_text if hasattr(llm_response, "completion_text") else str(llm_response)
            if trace_id:
                self._call_metrics[trace_id] = extract_usage_metrics(llm_response, prompt, response_text)
            result = json.loads(self._strip_json_fence(response_text))
            normalized = self._normalize_action_plan(result, player_input, intent, game_state, module_data)
            logger.info(f"[RuleAI] adjudicate_action result: {normalized}")
            return normalized
        except json.JSONDecodeError as e:
            logger.warning(f"[RuleAI] adjudicate_action JSON decode failed. Input={player_input}")
            raise RuntimeError("规则AI动作裁定失败：返回结果不是合法 JSON，请使用重试按钮。") from e
        except Exception as e:
            logger.error(f"[RuleAI] adjudicate_action error: {e}")
            raise RuntimeError("规则AI动作裁定失败，请使用重试按钮。") from e

    async def resolve_check(self, adjudication_result: dict, player_state: dict) -> dict:
        feasibility = adjudication_result.get("feasibility", {}) if isinstance(adjudication_result, dict) else {}
        if not feasibility.get("ok", True):
            return {
                "check_type": None,
                "success": False,
                "result_description": feasibility.get("reason") or "无法执行"
            }

        check_data = adjudication_result.get("check", {}) if isinstance(adjudication_result, dict) else {}
        required = bool(check_data.get("required"))
        skill_name = check_data.get("skill")
        difficulty = self._normalize_difficulty(check_data.get("difficulty"))

        if not required or not skill_name or difficulty == "无需判定":
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

        logger.info(f"[RuleAI] resolve_check: {skill_name} {roll}/{threshold} {'成功' if success else '失败'}")
        return result

    def resolve_sancheck(self, entity_context: dict, player_san: int, session_manager, session_id: str):
        """执行 SAN 检定。返回 sancheck_result dict 或 None（无需检定）。"""
        if not isinstance(entity_context, dict):
            return None
        sancheck_spec = entity_context.get("sancheck")
        if not sancheck_spec:
            return None

        entity_name = entity_context.get("name", "unknown")

        # 已触发过则不重复
        if session_manager.is_sancheck_triggered(session_id, entity_name):
            return None

        # 解析 "0/3" 格式
        success_loss, fail_loss = self._parse_sancheck_spec(sancheck_spec)

        # 1d100 检定，阈值 = 当前 SAN
        threshold = player_san
        roll = random.randint(1, 100)
        success = roll <= threshold
        san_loss = success_loss if success else fail_loss

        # 记录已触发
        session_manager.record_sancheck(session_id, entity_name)

        logger.info(f"[RuleAI] resolve_sancheck: {entity_name} {roll}/{threshold} {'成功' if success else '失败'} SAN{san_loss}")
        return {
            "check_type": "sancheck",
            "entity_name": entity_name,
            "sancheck_spec": sancheck_spec,
            "threshold": threshold,
            "roll": roll,
            "success": success,
            "san_loss": san_loss,
        }

    def _parse_sancheck_spec(self, spec: str):
        """解析 '0/3' → (0, -3)。返回 (success_loss, fail_loss) 均为负数或0。"""
        parts = str(spec).split("/")
        success_val = int(parts[0]) if len(parts) > 0 else 0
        fail_val = int(parts[1]) if len(parts) > 1 else 0
        return (-abs(success_val), -abs(fail_val))

    def build_hard_changes(
        self,
        player_input: str,
        adjudication_result: dict,
        rule_result: dict,
        game_state: dict = None,
        sancheck_result: dict = None
    ) -> dict:
        if not isinstance(adjudication_result, dict):
            return {}

        feasibility = adjudication_result.get("feasibility", {})
        if not feasibility.get("ok", True):
            return {}

        normalized_action = adjudication_result.get("normalized_action", {})
        object_context = adjudication_result.get("object_context")
        check_success = bool(rule_result.get("success", True))
        effect_key = "on_success" if check_success else "on_failure"
        effect_plan = adjudication_result.get(effect_key, {})
        effect_plan = effect_plan if isinstance(effect_plan, dict) else {}

        changes = {}

        clues = self._ensure_list(effect_plan.get("discover_clues"))
        inventory_add = self._ensure_list(effect_plan.get("add_inventory"))
        inventory_remove = self._ensure_list(effect_plan.get("remove_inventory"))
        flags = effect_plan.get("set_flags", {})
        npc_updates = effect_plan.get("npc_updates", {})
        if not isinstance(npc_updates, dict):
            npc_updates = {}

        if isinstance(object_context, dict) and check_success:
            object_name = object_context.get("name")
            object_type = object_context.get("type")
            action_verb = str(normalized_action.get("verb") or "").lower()
            can_take = bool(object_context.get("can_take"))

            if object_type == "clue" and object_name and object_name not in clues:
                clues.append(object_name)
            if can_take and object_name and action_verb in {"take", "pickup", "obtain", "loot"} and object_name not in inventory_add:
                inventory_add.append(object_name)

        # SAN 变化由 sancheck 系统驱动
        if sancheck_result and sancheck_result.get("san_loss"):
            changes["san_delta"] = sancheck_result["san_loss"]

        if clues:
            changes["clues"] = clues
        if inventory_add:
            changes["inventory_add"] = inventory_add
        if inventory_remove:
            changes["inventory_remove"] = inventory_remove
        if isinstance(flags, dict) and flags:
            changes["flags"] = flags
        if isinstance(npc_updates, dict) and npc_updates:
            pure_npc = {}
            threat_updates = {}
            threat_names = set(get_module_threat_entities((game_state or {}).get("module_data", {})).keys())
            for name, upd in npc_updates.items():
                if name in threat_names or is_threat_entity(name, upd if isinstance(upd, dict) else None):
                    threat_updates[name] = upd
                else:
                    pure_npc[name] = upd
            if pure_npc:
                changes["npc_updates"] = pure_npc
            if threat_updates:
                changes["threat_entity_updates"] = threat_updates

        current_location = str((game_state or {}).get("current_location") or "").strip()
        location_data = adjudication_result.get("location_context", {}) if isinstance(adjudication_result, dict) else {}
        if not isinstance(location_data, dict):
            location_data = {}
        if (
            check_success
            and str(normalized_action.get("verb") or "").lower() == "close"
            and current_location
            and bool(location_data.get("has_door"))
        ):
            module_data = (game_state or {}).get("module_data", {}) if isinstance(game_state, dict) else {}
            pursuer_name = get_primary_pursuer_name(module_data)
            pursuer_state = ((game_state or {}).get("world_state", {}).get("npcs", {}) or {}).get(pursuer_name, {}) if pursuer_name else {}
            chase_state = (pursuer_state or {}).get("chase_state", {})
            if pursuer_name and isinstance(chase_state, dict) and chase_state.get("active"):
                changes["threat_entity_updates"] = self._merge_nested_dict(
                    changes.get("threat_entity_updates", {}),
                    {
                        pursuer_name: {
                            "chase_state": {
                                "active": True,
                                "status": "blocked",
                                "target": "player",
                                "blocked_at": current_location,
                                "last_target_location": current_location,
                            }
                        }
                    },
                )

        return changes

    def _build_action_prompt(
        self,
        prompt_template: str,
        player_input: str,
        intent: dict,
        game_state: dict,
        module_data: dict
    ) -> str:
        current_location = game_state.get("current_location", "master_bedroom")
        location_context = build_runtime_location_context(game_state, module_data, current_location)
        scene_objects = self._get_scene_objects(game_state, module_data)
        scene_npcs = self._get_scene_npcs(game_state, module_data)
        scene_threat_entities = self._get_scene_threat_entities(game_state, module_data)
        prompt_scene_npcs = self._compact_npc_context_for_prompt(scene_npcs)
        prompt_threat_entities = self._compact_threat_context_for_prompt(scene_threat_entities)
        inventory = game_state.get("player", {}).get("inventory", [])
        clues_found = game_state.get("world_state", {}).get("clues_found", [])
        reachable = sorted(self._get_reachable_locations(game_state, module_data))

        prompt = prompt_template.replace("{player_input}", player_input)
        prompt = prompt.replace("{intent}", json.dumps(intent or {}, ensure_ascii=False))
        prompt = prompt.replace("{current_location}", current_location)
        prompt = prompt.replace("{location_context}", json.dumps(location_context, ensure_ascii=False, indent=2))
        prompt = prompt.replace("{scene_objects}", json.dumps(scene_objects, ensure_ascii=False, indent=2))
        prompt = prompt.replace("{scene_npcs}", json.dumps(prompt_scene_npcs, ensure_ascii=False, indent=2))
        adjacent_context = build_adjacent_locations_context(game_state, module_data, current_location)
        prompt = prompt.replace("{adjacent_locations}", json.dumps(adjacent_context, ensure_ascii=False, indent=2))
        prompt = prompt.replace("{inventory}", json.dumps(inventory, ensure_ascii=False))
        prompt = prompt.replace("{clues_found}", json.dumps(clues_found, ensure_ascii=False))
        prompt = prompt.replace("{reachable_locations}", json.dumps(reachable, ensure_ascii=False))
        threat_chase = (
            location_context.get("threat_chase")
            or location_context.get("butler_chase")
            if isinstance(location_context, dict)
            else None
        )
        prompt += (
            "\n\n# 当前场景威胁实体字段\n"
            f"{json.dumps(prompt_threat_entities, ensure_ascii=False, indent=2)}\n\n"
            "# 额外约束\n"
            "- threat_entity_context 必须是当前场景威胁实体的原始字段；若没有命中则为 null。\n"
            "- threat entity 不是 NPC。它不能说话，不能被当作普通对话对象。\n"
            "- normalized_action.target_kind 可使用 threat_entity。\n"
            "- 输出JSON时，请在顶层加入 threat_entity_context 字段。\n"
        )
        prompt += (
            "\n# 当前主要威胁追逐状态补充\n"
            f"{json.dumps(threat_chase or {}, ensure_ascii=False, indent=2)}\n"
            "- 如果 threat_chase.active 为 true，说明玩家处于持续追逐压力中。即使主要威胁实体不在当前房间，也要据此理解玩家的逃跑、关门、拖延和阻拦意图。\n"
            "- 如果当前场景 has_door=true，且玩家表达关门、顶门、抵门、堵门或反锁的意思，在追逐中优先理解为试图把主要威胁实体阻隔在门外。\n"
        )
        if self.is_dialogue_input(player_input):
            prompt += (
                "\n# 输入分类：对话\n"
                "系统检测到玩家输入包含引号，判定为【对话】。"
                "引号内的文字是玩家角色说出的台词，不是实际行动。"
                "请将 normalized_action.verb 设为 \"talk\"，不要将台词内容理解为行动指令。\n"
            )
        else:
            prompt += (
                "\n# 输入分类：行动\n"
                "系统判定玩家输入为【行动】。请将其作为玩家角色的实际动作或行为处理。\n"
            )
        # 隔墙交流约束
        cross_wall_npcs = [n for n, d in scene_npcs.items() if d.get("cross_wall")]
        if cross_wall_npcs:
            prompt += (
                "\n# 隔墙交流约束\n"
                f"以下NPC通过墙壁交流（voice_only）：{cross_wall_npcs}\n"
                "- 玩家与这些NPC之间只能听到声音，不能获得视觉信息\n"
                "- 不能描写对面房间的视觉细节\n"
                "- 对话应体现隔墙/门后的物理隔断感\n"
                "- 这些NPC仍然可以作为对话目标（target_kind=npc），feasibility应为ok\n"
            )
        return prompt

    def _normalize_action_plan(
        self,
        result: dict,
        player_input: str,
        intent: dict,
        game_state: dict,
        module_data: dict
    ) -> dict:
        current_location = game_state.get("current_location", "master_bedroom")
        location_context = build_runtime_location_context(game_state, module_data, current_location)
        scene_objects = self._get_scene_objects(game_state, module_data)
        scene_npcs = self._get_scene_npcs(game_state, module_data)
        scene_threat_entities = self._get_scene_threat_entities(game_state, module_data)
        all_objects = module_data.get("objects", {})

        normalized = self._get_fallback_action_plan(player_input, intent, game_state, module_data)
        if isinstance(result, dict):
            normalized.update(result)

        normalized_action = normalized.get("normalized_action")
        if not isinstance(normalized_action, dict):
            normalized_action = {}
        normalized_action.setdefault("verb", self._infer_verb(player_input, intent))
        normalized_action.setdefault("target_kind", "unknown")
        normalized_action.setdefault("target_key", None)
        normalized_action.setdefault("raw_target_text", str((intent or {}).get("target") or "").strip())
        if self._should_default_to_scene_npc(player_input, normalized_action, scene_npcs):
            only_npc = next(iter(scene_npcs))
            normalized_action["target_kind"] = "npc"
            normalized_action["target_key"] = only_npc
            if not normalized_action.get("raw_target_text"):
                normalized_action["raw_target_text"] = only_npc
        normalized["normalized_action"] = normalized_action

        feasibility = normalized.get("feasibility")
        if not isinstance(feasibility, dict):
            feasibility = {"ok": True, "reason": None}
        feasibility["ok"] = bool(feasibility.get("ok", True))
        reason = feasibility.get("reason")
        feasibility["reason"] = str(reason) if reason else None
        normalized["feasibility"] = feasibility

        object_context = normalized.get("object_context")
        if isinstance(object_context, str):
            object_data = all_objects.get(object_context)
            normalized["object_context"] = (
                {"name": object_context, **object_data}
                if isinstance(object_data, dict) else None
            )
        elif not isinstance(object_context, dict):
            normalized["object_context"] = None

        threat_entity_context = normalized.get("threat_entity_context")
        if isinstance(threat_entity_context, str):
            threat_entity_context = scene_threat_entities.get(threat_entity_context)
        elif not isinstance(threat_entity_context, dict):
            threat_entity_context = None
        normalized["threat_entity_context"] = threat_entity_context

        target_key = normalized_action.get("target_key")

        if isinstance(normalized["object_context"], dict):
            normalized["object_context"].setdefault("name", normalized_action.get("target_key"))
        elif target_key and normalized_action.get("target_kind") == "object" and target_key in scene_objects:
            normalized["object_context"] = {"name": target_key, **scene_objects[target_key]}

        if isinstance(normalized["threat_entity_context"], dict):
            normalized["threat_entity_context"].setdefault("name", target_key)
        elif target_key and normalized_action.get("target_kind") == "threat_entity" and target_key in scene_threat_entities:
            normalized["threat_entity_context"] = {"name": target_key, **scene_threat_entities[target_key]}

        if target_key and normalized_action.get("target_kind") == "object" and target_key not in scene_objects:
            if target_key in all_objects:
                feasibility["ok"] = False
                feasibility["reason"] = feasibility["reason"] or "目标物品不在当前场景"
            normalized["object_context"] = None if target_key not in scene_objects else normalized["object_context"]

        if target_key and normalized_action.get("target_kind") == "threat_entity" and target_key not in scene_threat_entities:
            feasibility["ok"] = False
            feasibility["reason"] = feasibility["reason"] or "目标威胁实体不在当前场景"
            normalized["threat_entity_context"] = None

        if normalized_action.get("target_kind") == "threat_entity" and isinstance(normalized["threat_entity_context"], dict):
            action_verb = str(normalized_action.get("verb") or "").lower()
            if action_verb == "talk":
                feasibility["ok"] = False
                feasibility["reason"] = feasibility["reason"] or "威胁实体不是可对话NPC，它不会回应你的交谈"
                normalized["check"] = {
                    "required": False,
                    "skill": None,
                    "difficulty": "无需判定",
                }

        if isinstance(normalized.get("object_context"), dict):
            requires = normalized["object_context"].get("requires")
            if requires and not self._requirements_met(requires, game_state):
                feasibility["ok"] = False
                feasibility["reason"] = feasibility["reason"] or "缺少必要条件，暂时无法这样做"
                normalized["check"] = {
                    "required": False,
                    "skill": None,
                    "difficulty": "无需判定",
                }
            access_block_reason = self._get_object_access_block_reason(normalized["object_context"], game_state)
            if access_block_reason:
                feasibility["ok"] = False
                feasibility["reason"] = feasibility["reason"] or access_block_reason
                normalized["check"] = {
                    "required": False,
                    "skill": None,
                    "difficulty": "无需判定",
                }

        if isinstance(normalized.get("object_context"), dict) and not (
            (normalized["object_context"].get("requires")
             and not self._requirements_met(normalized["object_context"].get("requires"), game_state))
            or self._get_object_access_block_reason(normalized["object_context"], game_state)
        ):
            normalized["check"] = self._build_object_check(normalized["object_context"])

        self._apply_close_door_override(normalized, player_input, game_state, module_data)
        normalized["location_context"] = location_context if isinstance(location_context, dict) else {}

        npc_context = normalized.get("npc_context")
        if not isinstance(npc_context, dict):
            npc_context = scene_npcs
        normalized["npc_context"] = npc_context

        companion_command = normalized.get("companion_command")
        normalized["companion_command"] = self._normalize_companion_command(
            companion_command,
            normalized_action,
            player_input,
            scene_npcs,
            module_data,
        )

        if normalized_action.get("target_kind") == "npc" and target_key in scene_npcs:
            npc_data = scene_npcs.get(target_key, {})
            if str(normalized_action.get("verb") or "").lower() == "talk" and not self._npc_can_speak(npc_data):
                feasibility["ok"] = False
                feasibility["reason"] = feasibility["reason"] or "这个对象不能正常说话，不会回应交谈"

        check_data = normalized.get("check")
        if not isinstance(check_data, dict):
            check_data = {}
        check_data["required"] = bool(check_data.get("required", False))
        check_data["skill"] = check_data.get("skill")
        check_data["difficulty"] = self._normalize_difficulty(check_data.get("difficulty"))
        normalized["check"] = check_data

        normalized["on_success"] = self._normalize_effect_plan(normalized.get("on_success"))
        normalized["on_failure"] = self._normalize_effect_plan(normalized.get("on_failure"))

        return normalized

    def _normalize_effect_plan(self, effect_plan: Any) -> dict:
        effect_plan = effect_plan if isinstance(effect_plan, dict) else {}
        return {
            "discover_clues": self._ensure_list(effect_plan.get("discover_clues")),
            "add_inventory": self._ensure_list(effect_plan.get("add_inventory")),
            "remove_inventory": self._ensure_list(effect_plan.get("remove_inventory")),
            "set_flags": effect_plan.get("set_flags") if isinstance(effect_plan.get("set_flags"), dict) else {},
            "npc_updates": effect_plan.get("npc_updates") if isinstance(effect_plan.get("npc_updates"), dict) else {},
        }

    def _get_fallback_action_plan(
        self,
        player_input: str,
        intent: dict,
        game_state: dict,
        module_data: dict
    ) -> dict:
        current_location = game_state.get("current_location", "master_bedroom")
        location_context = build_runtime_location_context(game_state, module_data, current_location)
        scene_objects = self._get_scene_objects(game_state, module_data)
        scene_npcs = self._get_scene_npcs(game_state, module_data)
        scene_threat_entities = self._get_scene_threat_entities(game_state, module_data)
        all_objects = module_data.get("objects", {})

        raw_target = str((intent or {}).get("target") or "").strip()
        verb = self._infer_verb(player_input, intent)
        plan = {
            "normalized_action": {
                "verb": verb,
                "target_kind": "unknown",
                "target_key": None,
                "raw_target_text": raw_target,
            },
            "feasibility": {
                "ok": True,
                "reason": None,
            },
            "location_context": location_context if isinstance(location_context, dict) else {},
            "object_context": None,
            "threat_entity_context": None,
            "npc_context": scene_npcs,
            "check": {
                "required": False,
                "skill": None,
                "difficulty": "无需判定",
            },
            "companion_command": {
                "target_npc": None,
                "command": None,
                "follow_target": None,
                "lag": 0,
                "target_entity": None,
                "destination": None,
            },
            "on_success": {
                "discover_clues": [],
                "add_inventory": [],
                "remove_inventory": [],
                "set_flags": {},
                "npc_updates": {},
            },
            "on_failure": {
                "discover_clues": [],
                "add_inventory": [],
                "remove_inventory": [],
                "set_flags": {},
                "npc_updates": {},
            },
        }

        object_key = self._match_target(raw_target or player_input, scene_objects)
        global_object_key = self._match_target(raw_target or player_input, all_objects)
        npc_key = self._match_target(raw_target or player_input, scene_npcs)
        threat_key = self._match_target(raw_target or player_input, scene_threat_entities)
        if not npc_key and self._should_default_to_scene_npc(player_input, plan["normalized_action"], scene_npcs):
            npc_key = next(iter(scene_npcs))

        if npc_key:
            plan["normalized_action"]["target_kind"] = "npc"
            plan["normalized_action"]["target_key"] = npc_key
            if not plan["normalized_action"].get("raw_target_text"):
                plan["normalized_action"]["raw_target_text"] = npc_key
            if verb == "talk" and not self._npc_can_speak(scene_npcs.get(npc_key, {})):
                plan["feasibility"]["ok"] = False
                plan["feasibility"]["reason"] = "这个对象不能正常说话，不会回应交谈"
            plan["companion_command"] = self._normalize_companion_command(
                plan.get("companion_command"),
                plan["normalized_action"],
                player_input,
                scene_npcs,
                module_data,
            )
            return plan

        if threat_key:
            plan["normalized_action"]["target_kind"] = "threat_entity"
            plan["normalized_action"]["target_key"] = threat_key
            plan["threat_entity_context"] = {"name": threat_key, **scene_threat_entities[threat_key]}
            if not plan["normalized_action"].get("raw_target_text"):
                plan["normalized_action"]["raw_target_text"] = threat_key
            if verb == "talk":
                plan["feasibility"]["ok"] = False
                plan["feasibility"]["reason"] = "威胁实体不是可对话NPC，它不会回应你的交谈"
            return plan

        if object_key:
            object_data = scene_objects[object_key]
            object_context = {"name": object_key, **object_data}
            plan["normalized_action"]["target_kind"] = "object"
            plan["normalized_action"]["target_key"] = object_key
            plan["object_context"] = object_context
            plan["check"] = self._build_object_check(object_context)

            requires = object_data.get("requires")
            if requires and not self._requirements_met(requires, game_state):
                plan["feasibility"]["ok"] = False
                plan["feasibility"]["reason"] = "缺少必要条件，暂时无法这样做"
                plan["check"]["required"] = False
                plan["check"]["skill"] = None
                plan["check"]["difficulty"] = "无需判定"
                return plan

            access_block_reason = self._get_object_access_block_reason(object_context, game_state)
            if access_block_reason:
                plan["feasibility"]["ok"] = False
                plan["feasibility"]["reason"] = access_block_reason
                plan["check"]["required"] = False
                plan["check"]["skill"] = None
                plan["check"]["difficulty"] = "无需判定"
                return plan

            if object_data.get("type") == "clue":
                plan["on_success"]["discover_clues"].append(object_key)
            if object_data.get("can_take") and verb in {"take", "pickup", "obtain", "loot"}:
                plan["on_success"]["add_inventory"].append(object_key)
            return plan

        if global_object_key:
            plan["normalized_action"]["target_kind"] = "object"
            plan["normalized_action"]["target_key"] = global_object_key
            plan["feasibility"]["ok"] = False
            plan["feasibility"]["reason"] = "目标物品不在当前场景"
            return plan

        self._apply_close_door_override(plan, player_input, game_state, module_data)
        plan["companion_command"] = self._normalize_companion_command(
            plan.get("companion_command"),
            plan["normalized_action"],
            player_input,
            scene_npcs,
            module_data,
        )
        return plan

    def _normalize_companion_command(
        self,
        command_data: Any,
        normalized_action: dict,
        player_input: str,
        scene_npcs: Dict[str, Dict[str, Any]],
        module_data: dict,
    ) -> dict:
        normalized = {
            "target_npc": None,
            "command": None,
            "follow_target": None,
            "lag": 0,
            "target_entity": None,
            "destination": None,
            "explicit_exit": False,
        }
        source = command_data if isinstance(command_data, dict) else {}
        if normalized_action.get("target_kind") == "npc" and normalized_action.get("target_key") in scene_npcs:
            normalized["target_npc"] = normalized_action.get("target_key")

        for key in ("target_npc", "command", "follow_target", "target_entity"):
            value = str(source.get(key) or "").strip()
            if value:
                normalized[key] = value

        if source.get("lag") not in (None, ""):
            try:
                normalized["lag"] = max(0, int(source.get("lag", 0) or 0))
            except (TypeError, ValueError):
                normalized["lag"] = 0

        destination = self._match_location_target(str(source.get("destination") or "").strip(), module_data)
        if destination:
            normalized["destination"] = destination

        text = str(player_input or "").strip()
        lowered = text.lower()
        if isinstance(source.get("explicit_exit"), bool):
            normalized["explicit_exit"] = bool(source.get("explicit_exit"))
        elif str(source.get("explicit_exit") or "").strip().lower() in {"true", "1", "yes"}:
            normalized["explicit_exit"] = True

        if not normalized["command"]:
            if any(keyword in text for keyword in ["跟我走", "跟着我", "跟上", "一起行动"]) or any(keyword in lowered for keyword in ["follow me", "come with me"]):
                normalized["command"] = "follow"
            elif any(keyword in text for keyword in ["你在这等", "待在这", "留在这里", "别动", "原地待命"]) or any(keyword in lowered for keyword in ["wait here", "stay here", "hold position"]):
                normalized["command"] = "wait"
            elif any(keyword in text for keyword in ["引开", "诱饵", "带到", "引到", "拖到"]) or any(keyword in lowered for keyword in ["bait", "distract", "draw away", "lead to"]):
                normalized["command"] = "bait"

        if self._requests_explicit_exit(text):
            normalized["explicit_exit"] = True

        if normalized["command"] == "follow":
            normalized["follow_target"] = normalized["follow_target"] or "player"
            if source.get("lag") in (None, ""):
                normalized["lag"] = 0
        elif normalized["command"] == "bait":
            if not normalized["target_entity"]:
                normalized["target_entity"] = self._match_companion_target(text, module_data, exclude=normalized["target_npc"])
            if not normalized["destination"]:
                normalized["destination"] = self._match_location_target(text, module_data)
        else:
            normalized["follow_target"] = None
            normalized["lag"] = 0
            normalized["target_entity"] = None
            normalized["destination"] = None

        if normalized["command"] not in {"follow", "wait", "bait"}:
            normalized["command"] = None
            normalized["target_npc"] = None if not source.get("target_npc") else normalized["target_npc"]

        if not normalized["target_npc"] and len(scene_npcs) == 1 and normalized["command"]:
            normalized["target_npc"] = next(iter(scene_npcs))

        return normalized

    def _requests_explicit_exit(self, player_input: str) -> bool:
        text = str(player_input or "").strip()
        lowered = text.lower()
        if not text:
            return False
        zh_keywords = ["开门", "把门打开", "出来", "出门", "出来吧", "出来跟我", "跟我出来"]
        en_keywords = ["open the door", "come out", "step out", "open up"]
        return any(keyword in text for keyword in zh_keywords) or any(keyword in lowered for keyword in en_keywords)

    def _match_location_target(self, target_text: str, module_data: dict) -> str:
        text = str(target_text or "").strip()
        if not text:
            return None
        locations = module_data.get("locations", {}) if isinstance(module_data, dict) else {}
        candidates = {}
        for location_key, location_data in locations.items():
            if isinstance(location_data, dict):
                candidates[location_key] = {
                    "name": location_data.get("name", location_key),
                    "hidden_name": location_data.get("hidden_name", ""),
                }
        lowered = text.lower()
        for key, data in candidates.items():
            names = [str(key), str(data.get("name") or ""), str(data.get("hidden_name") or "")]
            if any(name and (name == text or name.lower() == lowered or name in text or name.lower() in lowered) for name in names):
                return key
        return None

    def _match_companion_target(self, target_text: str, module_data: dict, exclude: str = "") -> str:
        text = str(target_text or "").strip()
        if not text:
            return None
        exclude = str(exclude or "").strip()
        candidates = {}
        for entity_name, entity_data in get_module_npcs(module_data).items():
            if entity_name == exclude:
                continue
            candidates[entity_name] = entity_data
        for entity_name, entity_data in get_module_threat_entities(module_data).items():
            if entity_name == exclude:
                continue
            candidates[entity_name] = entity_data
        return self._match_target(text, candidates)

    def _requirements_met(self, requirements: Any, game_state: dict) -> bool:
        requirements = self._ensure_list(requirements)
        inventory = set(game_state.get("player", {}).get("inventory", []))
        clues_found = set(game_state.get("world_state", {}).get("clues_found", []))
        flags = game_state.get("world_state", {}).get("flags", {})

        for requirement in requirements:
            if requirement in inventory or requirement in clues_found:
                continue
            if isinstance(flags, dict) and flags.get(requirement):
                continue
            return False
        return True

    def _get_object_access_block_reason(self, object_context: dict, game_state: dict) -> str:
        if not isinstance(object_context, dict):
            return None

        object_location = str(object_context.get("location") or "").strip()
        object_name = str(object_context.get("name") or "目标物品").strip()
        module_data = (game_state or {}).get("module_data", {}) if isinstance(game_state, dict) else {}
        pursuer_name = get_primary_pursuer_name(module_data)
        pursuer_settings = get_primary_pursuer_settings(module_data)
        pursuer_messages = pursuer_settings.get("messages", {}) if isinstance(pursuer_settings.get("messages"), dict) else {}

        if object_context.get("requires_primary_pursuer_gone") or object_context.get("requires_butler_gone"):
            pursuer_location = self._get_npc_location(pursuer_name, game_state) if pursuer_name else ""
            if object_location and pursuer_location == object_location:
                template = str(pursuer_messages.get("guarded_object_blocked") or "").strip()
                if not template:
                    template = "{object_name}还在{entity_name}的看守范围内，你现在无法靠近"
                return (
                    template
                    .replace("{object_name}", object_name)
                    .replace("{entity_name}", pursuer_name or "主要威胁实体")
                )

        requires_npc_absent = self._ensure_list(object_context.get("requires_npc_absent"))
        for npc_name in requires_npc_absent:
            if self._get_npc_location(str(npc_name), game_state) == object_location:
                return f"{npc_name}还在这里，你暂时无法接近{object_name}"

        return None

    def _get_npc_location(self, npc_name: str, game_state: dict) -> str:
        world_npcs = game_state.get("world_state", {}).get("npcs", {})
        npc_state = world_npcs.get(npc_name, {}) if isinstance(world_npcs, dict) else {}
        return str(npc_state.get("location") or "").strip()

    def _get_scene_objects(self, game_state: dict, module_data: dict) -> Dict[str, Dict[str, Any]]:
        current_location = game_state.get("current_location", "master_bedroom")
        location_data = module_data.get("locations", {}).get(current_location, {})
        objects = module_data.get("objects", {})
        scene_objects = {}
        for object_name in location_data.get("objects", []):
            object_data = objects.get(object_name)
            if isinstance(object_data, dict):
                scene_objects[object_name] = object_data
        return scene_objects

    def _get_scene_npcs(self, game_state: dict, module_data: dict) -> Dict[str, Dict[str, Any]]:
        current_location = game_state.get("current_location", "master_bedroom")
        npc_states = game_state.get("world_state", {}).get("npcs", {})
        scene_npcs = {}
        for npc_name, npc_data in get_module_npcs(module_data).items():
            runtime_state = npc_states.get(npc_name, {})
            npc_location = runtime_state.get("location", npc_data.get("location"))
            if npc_location != current_location:
                continue

            merged_npc = dict(npc_data)
            merged_npc.setdefault("name", npc_name)
            merged_npc["enabled_systems"] = [
                system_name
                for system_name in ("position", "dialogue", "trust", "memory", "reveal", "soft_state", "companion")
                if merged_npc.get(system_name) is not None
            ]
            merged_npc["runtime_state"] = {
                "location": npc_location,
                "attitude": runtime_state.get("attitude", npc_data.get("initial_attitude", "中立")),
                "trust_level": runtime_state.get("trust_level", 0.0),
                "memory": runtime_state.get("memory", {}),
                "memory_long_term": runtime_state.get("memory_long_term", {}),
                "soft_state": runtime_state.get("soft_state", {}),
                "relationship": runtime_state.get("relationship", {}),
                "companion_mode": runtime_state.get("companion_mode", runtime_state.get("companion_state", "wait")),
                "companion_state": runtime_state.get("companion_state", runtime_state.get("companion_mode", "wait")),
                "companion_task": runtime_state.get("companion_task", {}),
            }
            scene_npcs[npc_name] = merged_npc

        # 追加隔墙可交流NPC
        cross_wall = get_cross_wall_npcs(game_state, module_data, current_location)
        for npc_name, cross_info in cross_wall.items():
            if npc_name in scene_npcs:
                continue
            npc_data = get_module_npcs(module_data).get(npc_name)
            if not npc_data:
                continue
            runtime_state = npc_states.get(npc_name, {})
            npc_location = runtime_state.get("location", npc_data.get("location"))

            merged_npc = dict(npc_data)
            merged_npc.setdefault("name", npc_name)
            merged_npc["enabled_systems"] = [
                system_name
                for system_name in ("position", "dialogue", "trust", "memory", "reveal", "soft_state", "companion")
                if merged_npc.get(system_name) is not None
            ]
            merged_npc["runtime_state"] = {
                "location": npc_location,
                "attitude": runtime_state.get("attitude", npc_data.get("initial_attitude", "中立")),
                "trust_level": runtime_state.get("trust_level", 0.0),
                "memory": runtime_state.get("memory", {}),
                "memory_long_term": runtime_state.get("memory_long_term", {}),
                "soft_state": runtime_state.get("soft_state", {}),
                "relationship": runtime_state.get("relationship", {}),
                "companion_mode": runtime_state.get("companion_mode", runtime_state.get("companion_state", "wait")),
                "companion_state": runtime_state.get("companion_state", runtime_state.get("companion_mode", "wait")),
                "companion_task": runtime_state.get("companion_task", {}),
            }
            merged_npc["cross_wall"] = True
            merged_npc["cross_wall_type"] = cross_info.get("wall_type", "voice_only")
            merged_npc["cross_wall_from_room"] = cross_info.get("from_room", "")
            scene_npcs[npc_name] = merged_npc

        return scene_npcs

    def _get_scene_threat_entities(self, game_state: dict, module_data: dict) -> Dict[str, Dict[str, Any]]:
        current_location = game_state.get("current_location", "master_bedroom")
        npc_states = game_state.get("world_state", {}).get("npcs", {})
        scene_threat_entities = {}
        for entity_name, entity_data in get_module_threat_entities(module_data).items():
            runtime_state = npc_states.get(entity_name, {})
            entity_location = runtime_state.get("location", entity_data.get("location"))
            if entity_location != current_location:
                continue

            merged_entity = dict(entity_data)
            merged_entity.setdefault("name", entity_name)
            merged_entity["enabled_systems"] = [
                system_name
                for system_name in ("position", "dialogue", "trust", "memory", "reveal", "soft_state", "companion")
                if merged_entity.get(system_name) is not None
            ]
            merged_entity["runtime_state"] = {
                "location": entity_location,
                "attitude": runtime_state.get("attitude", entity_data.get("initial_attitude", "中立")),
                "trust_level": runtime_state.get("trust_level", 0.0),
                "memory": runtime_state.get("memory", {}),
                "memory_long_term": runtime_state.get("memory_long_term", {}),
                "soft_state": runtime_state.get("soft_state", {}),
                "relationship": runtime_state.get("relationship", {}),
                "companion_mode": runtime_state.get("companion_mode", runtime_state.get("companion_state")),
                "companion_state": runtime_state.get("companion_state", runtime_state.get("companion_mode")),
                "companion_task": runtime_state.get("companion_task", {}),
            }
            scene_threat_entities[entity_name] = merged_entity
        return scene_threat_entities

    def _compact_npc_context_for_prompt(self, scene_npcs: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        compact = {}
        for npc_name, npc_data in (scene_npcs or {}).items():
            if not isinstance(npc_data, dict):
                continue
            runtime_state = npc_data.get("runtime_state", {}) if isinstance(npc_data.get("runtime_state"), dict) else {}
            companion_task = runtime_state.get("companion_task", {}) if isinstance(runtime_state.get("companion_task"), dict) else {}
            memory = runtime_state.get("memory", {}) if isinstance(runtime_state.get("memory"), dict) else {}
            reveal_state = npc_data.get("reveal_state", {}) if isinstance(npc_data.get("reveal_state"), dict) else {}
            compact[npc_name] = {
                "name": npc_data.get("name", npc_name),
                "enabled_systems": list(npc_data.get("enabled_systems", []) or []),
                "cross_wall": bool(npc_data.get("cross_wall")),
                "cross_wall_from_room": npc_data.get("cross_wall_from_room", ""),
                "appearance": self._trim_text(get_entity_profile_text(npc_data, "appearance"), 80),
                "current_state": self._trim_text(get_entity_profile_text(npc_data, "current_state"), 120),
                "first_appearance": self._trim_text(get_entity_first_appearance(npc_data), 100),
                "trust_threshold": get_entity_trust_threshold(npc_data),
                "available_trust_reasons": list(npc_data.get("available_trust_reasons", []) or [])[:20],
                "reveal_state": reveal_state,
                "runtime_state": {
                    "location": runtime_state.get("location"),
                    "attitude": runtime_state.get("attitude"),
                    "trust_level": runtime_state.get("trust_level"),
                    "soft_state": runtime_state.get("soft_state", {}),
                    "companion_mode": runtime_state.get("companion_mode"),
                    "companion_task": companion_task,
                    "memory": {
                        "player_facts": memory.get("player_facts", {}),
                        "evidence_seen": memory.get("evidence_seen", []),
                        "topics_discussed": memory.get("topics_discussed", []),
                        "answered_questions": memory.get("answered_questions", []),
                    },
                },
            }
        return compact

    def _compact_threat_context_for_prompt(self, scene_threat_entities: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        compact = {}
        for entity_name, entity_data in (scene_threat_entities or {}).items():
            if not isinstance(entity_data, dict):
                continue
            runtime_state = entity_data.get("runtime_state", {}) if isinstance(entity_data.get("runtime_state"), dict) else {}
            compact[entity_name] = {
                "name": entity_data.get("name", entity_name),
                "enabled_systems": list(entity_data.get("enabled_systems", []) or []),
                "appearance": self._trim_text(get_entity_profile_text(entity_data, "appearance"), 80),
                "current_state": self._trim_text(get_entity_profile_text(entity_data, "current_state"), 120),
                "behavior": entity_data.get("behavior", {}) if isinstance(entity_data.get("behavior"), dict) else {},
                "runtime_state": {
                    "location": runtime_state.get("location"),
                    "companion_mode": runtime_state.get("companion_mode"),
                    "companion_task": runtime_state.get("companion_task", {}) if isinstance(runtime_state.get("companion_task"), dict) else {},
                },
            }
        return compact

    def _trim_text(self, text: str, limit: int) -> str:
        value = str(text or "").strip()
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 1)] + "…"

    def _get_reachable_locations(self, game_state: dict, module_data: dict):
        current_location = game_state.get("current_location", "master_bedroom")
        graph = self._build_graph(module_data)
        visited = {current_location}
        queue = [current_location]

        while queue:
            location_key = queue.pop(0)
            for neighbor_key in graph.get(location_key, []):
                if neighbor_key in visited:
                    continue
                visited.add(neighbor_key)
                queue.append(neighbor_key)

        return visited

    def _build_graph(self, module_data: dict):
        locations = module_data.get("locations", {})
        name_to_key = {
            location_data.get("name"): location_key
            for location_key, location_data in locations.items()
            if location_data.get("name")
        }
        graph = {}
        for location_key, location_data in locations.items():
            neighbors = []
            for exit_name in location_data.get("exits", []):
                neighbor_key = name_to_key.get(exit_name)
                if neighbor_key:
                    neighbors.append(neighbor_key)
            graph[location_key] = neighbors

        for object_data in module_data.get("objects", {}).values():
            leads_to = object_data.get("leads_to")
            from_loc = object_data.get("location")
            if from_loc in graph and leads_to in locations and leads_to not in graph[from_loc]:
                graph[from_loc].append(leads_to)

        return graph

    def _match_target(self, target_text: str, candidates: Dict[str, Dict[str, Any]]) -> str:
        target_text = str(target_text or "").strip()
        if not target_text:
            return None

        lowered_target = target_text.lower()
        for key, data in candidates.items():
            name = str(data.get("name", key))
            if target_text == key or target_text == name:
                return key
            if key.lower() in lowered_target or name.lower() in lowered_target:
                return key
            if lowered_target in key.lower() or lowered_target in name.lower():
                return key
        return None

    def _infer_verb(self, player_input: str, intent: dict) -> str:
        text = str(player_input or "")
        lowered = text.lower()
        intent_name = str((intent or {}).get("intent") or "").lower()

        # 硬编码：带引号 → 对话
        if self.is_dialogue_input(text):
            return "talk"

        if any(keyword in text for keyword in ["拿", "捡", "拾取", "带走"]) or any(keyword in lowered for keyword in ["take", "pick", "grab", "loot"]):
            return "take"
        if any(keyword in text for keyword in ["烧", "点燃", "焚毁"]) or "burn" in lowered:
            return "burn"
        if any(keyword in text for keyword in ["切断", "砍", "破坏"]) or any(keyword in lowered for keyword in ["cut", "destroy", "break"]):
            return "destroy"
        if self._is_close_door_action(text):
            return "close"
        if any(keyword in text for keyword in ["说", "问", "交谈", "对话"]) or intent_name == "talk":
            return "talk"
        if any(keyword in text for keyword in ["用", "使用"]) or intent_name == "use":
            return "use"
        if any(keyword in text for keyword in ["看", "调查", "检查", "观察", "搜", "翻"]) or intent_name == "search":
            return "inspect"
        if intent_name:
            return intent_name
        return "interact"

    def _is_close_door_action(self, player_input: str) -> bool:
        text = str(player_input or "").strip()
        if not text:
            return False
        lowered = text.lower()
        cn_keywords = ["关门", "把门关上", "关上门", "顶住门", "堵门", "抵住房门", "反锁", "锁门", "拦住房门"]
        en_keywords = ["shut the door", "close the door", "bar the door", "lock the door", "hold the door"]
        return any(keyword in text for keyword in cn_keywords) or any(keyword in lowered for keyword in en_keywords)

    def _apply_close_door_override(self, plan: dict, player_input: str, game_state: dict, module_data: dict):
        if not isinstance(plan, dict) or not self._is_close_door_action(player_input):
            return

        current_location = game_state.get("current_location", "master_bedroom")
        location_data = (module_data or {}).get("locations", {}).get(current_location, {})
        has_door = bool((location_data or {}).get("has_door"))

        normalized_action = plan.setdefault("normalized_action", {})
        feasibility = plan.setdefault("feasibility", {"ok": True, "reason": None})
        check = plan.setdefault("check", {})

        normalized_action["verb"] = "close"
        normalized_action["target_kind"] = "location"
        normalized_action["target_key"] = current_location
        if not normalized_action.get("raw_target_text"):
            normalized_action["raw_target_text"] = "门"

        check["required"] = False
        check["skill"] = None
        check["difficulty"] = "无需判定"

        if not has_door:
            feasibility["ok"] = False
            feasibility["reason"] = feasibility.get("reason") or "这里没有门可以关上"
            return

        module_data = (game_state or {}).get("module_data", {}) if isinstance(game_state, dict) else {}
        pursuer_name = get_primary_pursuer_name(module_data)
        pursuer_state = ((game_state or {}).get("world_state", {}).get("npcs", {}) or {}).get(pursuer_name, {}) if pursuer_name else {}
        chase_state = (pursuer_state or {}).get("chase_state", {})
        if isinstance(chase_state, dict) and chase_state.get("active"):
            feasibility["ok"] = True
            feasibility["reason"] = None

    def _should_default_to_scene_npc(self, player_input: str, normalized_action: dict, scene_npcs: Dict[str, Dict[str, Any]]) -> bool:
        if not isinstance(scene_npcs, dict) or len(scene_npcs) != 1:
            return False
        if not isinstance(normalized_action, dict):
            return False
        if normalized_action.get("target_kind") == "npc" and normalized_action.get("target_key"):
            return False

        verb = str(normalized_action.get("verb") or "").lower()
        if verb == "talk":
            return True
        return self._looks_like_direct_speech(player_input)

    def _looks_like_direct_speech(self, player_input: str) -> bool:
        text = str(player_input or "").strip()
        if not text:
            return False
        # 硬编码：带引号 → 对话
        if self.is_dialogue_input(text):
            return True
        lowered = text.lower()
        speech_markers = [
            "“", "\"", "：", ":", "你好", "您好", "我是", "我叫", "请问", "谁在里面",
            "hello", "hi", "i am", "i'm", "who are you", "can you", "please"
        ]
        if any(marker in text for marker in speech_markers[:8]):
            return True
        if any(marker in lowered for marker in speech_markers[8:]):
            return True
        return len(text) >= 4 and not any(
            keyword in text for keyword in ["调查", "查看", "观察", "搜索", "拿", "取", "使用", "烧", "破坏"]
        )

    def _build_object_check(self, object_context: dict) -> dict:
        object_context = object_context if isinstance(object_context, dict) else {}
        return {
            "required": bool(object_context.get("check_required")),
            "skill": object_context.get("check_required"),
            "difficulty": self._normalize_difficulty(object_context.get("difficulty")),
        }

    def _merge_nested_dict(self, base: dict, incoming: dict) -> dict:
        merged = dict(base or {})
        for key, value in (incoming or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_nested_dict(merged[key], value)
            elif isinstance(value, list) and isinstance(merged.get(key), list):
                merged[key] = list(merged[key])
                for item in value:
                    if item not in merged[key]:
                        merged[key].append(item)
            else:
                merged[key] = value
        return merged

    def _ensure_list(self, value: Any):
        if value is None:
            return []
        if isinstance(value, list):
            return [item for item in value if item]
        if value:
            return [value]
        return []

    def _normalize_difficulty(self, difficulty) -> str:
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
        if difficulty == "困难":
            return max(1, player_skill // 2)
        if difficulty == "极难":
            return max(1, player_skill // 5)
        return player_skill

    def _get_result_description(self, success, critical_success, critical_failure):
        if critical_success:
            return "大成功"
        if critical_failure:
            return "大失败"
        if success:
            return "成功"
        return "失败"

    def resolve_assist_check(self, player_result: dict, npc_name: str, npc_skills: dict, skill: str, difficulty: str) -> dict:
        """NPC协助检定：NPC独立投骰，任一成功则整体成功。"""
        npc_skill_value = int(npc_skills.get(skill, 30))
        threshold = self._get_threshold(npc_skill_value, difficulty)
        roll = random.randint(1, 100)
        npc_success = roll <= threshold

        npc_roll = {
            "npc_name": npc_name,
            "skill": skill,
            "npc_skill": npc_skill_value,
            "threshold": threshold,
            "roll": roll,
            "success": npc_success,
        }

        combined_success = player_result.get("success", False) or npc_success
        logger.info(f"[RuleAI] assist_check: {npc_name} {skill} {roll}/{threshold} {'成功' if npc_success else '失败'}, 综合: {'成功' if combined_success else '失败'}")

        result = dict(player_result)
        result["success"] = combined_success
        result["assist"] = True
        result["npc_roll"] = npc_roll
        if not player_result.get("success") and npc_success:
            result["result_description"] = f"{npc_name}协助成功"
        return result
