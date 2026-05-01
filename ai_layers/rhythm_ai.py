from astrbot.api import logger
from astrbot.api.star import Context
from ..game_state.character_card import build_identity_block
from ..game_state.location_context import (
    build_adjacent_locations_context,
    build_runtime_location_context,
    get_entity_dialogue_guide,
    get_entity_first_appearance,
    get_entity_profile_text,
    get_entity_trust_gates,
    get_entity_trust_map,
    get_entity_trust_threshold,
    get_cross_wall_npcs,
    has_cross_wall_contact_history,
    get_module_npcs,
    get_module_threat_entities,
    get_primary_pursuer_name,
    is_threat_entity,
    should_enable_cross_wall_npc_context,
)
from .provider_failover import (
    ProviderFailoverError,
    normalize_provider_candidates,
    text_chat_with_fallback,
)

import json
import os
import re
import copy


class RhythmAI:
    """Pacing layer: stage judgment, soft guidance, and NPC response direction."""

    def __init__(
        self,
        context: Context,
        provider_name: str = None,
        config: dict = None,
        fallback_provider_names: list[str] | None = None,
    ):
        self.context = context
        self.provider_name = provider_name
        self.fallback_provider_names = list(fallback_provider_names or [])
        self.config = config or {}
        self.prompts = self._load_prompts()
        self._call_metrics = {}

    def pop_call_metric(self, trace_id: str) -> dict:
        if not trace_id:
            return {}
        return self._call_metrics.pop(trace_id, {})

    def _load_prompts(self):
        prompts_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ai_prompts.json")
        try:
            with open(prompts_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"[RhythmAI] Prompt config not found: {prompts_path}")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"[RhythmAI] Prompt config JSON error: {e}")
            return {}

    def _get_provider_candidates(self) -> list[str]:
        candidates = normalize_provider_candidates(
            self.provider_name,
            self.fallback_provider_names,
        )
        if not candidates:
            logger.error("[RhythmAI] rhythm_ai_provider is not configured")
        return candidates

    def _strip_json_fence(self, text: str) -> str:
        text = (text or "").strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    async def process(
        self,
        player_input: str,
        rule_plan: dict,
        rule_result: dict,
        game_state: dict,
        module_data: dict,
        history: list = None,
        trace_id: str = None,
        custom_api: dict | None = None,
    ) -> dict:
        if history is None:
            history = []

        provider_candidates = self._get_provider_candidates()
        base_result = self._build_base_result(player_input, rule_plan, game_state, module_data)

        if not provider_candidates:
            logger.error("[RhythmAI] No provider available")
            raise RuntimeError("节奏AI处理失败：未找到可用 LLM provider，请使用重试按钮。")

        prompt_template = self.config.get("rhythm_ai_prompt", "").strip()
        if not prompt_template:
            prompt_template = self.prompts.get("rhythm_ai_prompt", "")

        if not prompt_template:
            logger.error("[RhythmAI] rhythm_ai_prompt not found")
            raise RuntimeError("节奏AI处理失败：未找到可用提示词，请使用重试按钮。")

        prompt = self._build_prompt(
            prompt_template=prompt_template,
            player_input=player_input,
            rule_plan=rule_plan,
            rule_result=rule_result,
            game_state=game_state,
            module_data=module_data,
        )

        try:
            # RhythmAI should rely on summarized history in the prompt, not on raw chat history.
            outcome = await text_chat_with_fallback(
                context=self.context,
                primary_provider_id=self.provider_name,
                fallback_provider_ids=self.fallback_provider_names,
                prompt=prompt,
                contexts=[],
                trace_label="RhythmAI.process",
                custom_api=custom_api,
            )
            llm_response = outcome.response
            response_text = (
                llm_response.completion_text
                if hasattr(llm_response, "completion_text")
                else str(llm_response)
            )
            if trace_id:
                self._call_metrics[trace_id] = outcome.metrics
            result = json.loads(self._strip_json_fence(response_text))
            normalized = self._normalize_result(result, base_result)
            logger.info(f"[RhythmAI] process result: {normalized}")
            return normalized
        except ProviderFailoverError as e:
            if trace_id:
                self._call_metrics[trace_id] = e.metrics
            logger.error("[RhythmAI] provider chain failed: %s", e)
            raise RuntimeError("节奏AI处理失败：所有候选模型都不可用，请检查主模型与备用模型配置。") from e
        except json.JSONDecodeError as e:
            logger.warning("[RhythmAI] JSON decode failed")
            raise RuntimeError("节奏AI处理失败：返回结果不是合法 JSON，请使用重试按钮。") from e
        except Exception as e:
            logger.error(f"[RhythmAI] process error: {e}")
            raise RuntimeError("节奏AI处理失败，请使用重试按钮。") from e

    def _build_prompt(
        self,
        prompt_template: str,
        player_input: str,
        rule_plan: dict,
        rule_result: dict,
        game_state: dict,
        module_data: dict
    ) -> str:
        current_location = game_state.get("current_location", "master_bedroom")
        round_count = game_state.get("round_count", 0)
        clues_found = game_state.get("world_state", {}).get("clues_found", [])
        stages = module_data.get("module_info", {}).get("stages", "")
        history_summaries = self._build_history_summaries(game_state)
        scene_context = self._build_scene_context(game_state, module_data, rule_plan, player_input=player_input)

        prompt = prompt_template.replace("{current_location}", current_location)
        prompt = prompt.replace("{player_identity_block}", build_identity_block(game_state.get("character_card")))
        prompt = prompt.replace("{round_count}", str(round_count))
        prompt = prompt.replace("{clues_found}", json.dumps(clues_found, ensure_ascii=False))
        prompt = prompt.replace("{stages}", stages)
        prompt = prompt.replace("{history_summaries}", history_summaries)
        prompt = prompt.replace("{player_input}", player_input)
        prompt = prompt.replace("{rule_plan}", json.dumps(rule_plan or {}, ensure_ascii=False, indent=2))
        prompt = prompt.replace("{rule_result}", json.dumps(rule_result or {}, ensure_ascii=False, indent=2))
        prompt = prompt.replace("{scene_context}", scene_context)
        prompt += (
            "\n\n# Additional tasks\n"
            "5. If the current scene has an interactable NPC, also output npc_action_guide for the narrative layer.\n"
            "6. npc_action_guide must only use known NPC data and runtime state.\n"
            "7. Treat npc_action_guide as a hard dialogue contract. The narrative layer should not decide secrets on its own.\n\n"
            "# npc_action_guide schema\n"
            "{\n"
            '  "focus_npc": "npc name or null",\n'
            '  "attitude": "current attitude toward the player",\n'
            '  "dialogue_act": "acknowledge/probe/reveal/warn/propose_plan/confirm_help/refuse/listen",\n'
            '  "response_strategy": "how the NPC should respond this turn",\n'
            '  "next_line_goal": "what the NPC wants to confirm or push this turn",\n'
            '  "voice_style": "brief note on tone and phrasing",\n'
            '  "must_acknowledge": ["what in the latest player input must be directly reacted to"],\n'
            '  "knowledge_boundary": "what the NPC must not invent or overstate",\n'
            '  "should_open_door": false\n'
            "}\n\n"
            "# NPC记忆更新任务\n"
            "当场景中有可交互NPC，且玩家本轮行动涉及NPC互动（对话、展示证据、做出承诺等）时，你必须输出 npc_memory_updates。\n"
            "如果本轮没有NPC互动，不输出此字段。\n\n"
            "## 记忆更新规则\n"
            "- 仅基于本轮玩家实际说了什么/做了什么来提取事实，不要推测。\n"
            "- player_facts: 玩家明确声称或展示的具体信息。key为类别（name/origin/goal/identity等），value为 {\"value\": \"具体内容\", \"status\": \"claimed\", \"source_round\": 当前轮次}。\n"
            "- topics_discussed: 本轮涉及的话题关键词列表（如 [\"identity\", \"origin\"]）。\n"
            "- answered_questions: 如果NPC之前有 pending_questions（见NPC记忆），且玩家本轮回答了其中某个问题，将该问题key移入此列表。这会自动从pending_questions中清除。\n"
            "- promises: 玩家做出的承诺 [{\"content\": \"承诺内容\", \"source_round\": 当前轮次}]。\n"
            "- evidence_seen: 玩家展示的证据 [{\"key\": \"证据名\", \"source_round\": 当前轮次}]。\n"
            "- trust_signals: 描述本轮信任变化信号 [{\"signal\": \"信号描述\", \"round\": 当前轮次, \"direction\": \"+\"或\"-\"}]。\n"
            "- last_impression: {\"focus\": \"NPC本轮关注重点\", \"attitude_snapshot\": \"当前态度\", \"source_round\": 当前轮次}\n"
            "- trust_change_reasons: 一个字符串key的列表，每个key必须来自NPC数据中的 available_trust_reasons 列表。同一轮可命中多个原因。如果本轮没有值得改变信任的行为，不输出此字段。\n"
            "- 信任匹配原则：宽松匹配。玩家的行为只要在语义上接近某个 available_trust_reasons 中的key，就应该触发。例如玩家说了自己的名字、来历、目的中的任何一项，都可以匹配 shared_personal_info；玩家表达理解、同情、安慰，都可以匹配 showed_empathy；玩家主动询问NPC的状况、故事、感受，都可以匹配 asked_about_her；玩家说话语气平和、没有催促施压，可以匹配 patient_and_respectful 或 calm_communication。一轮中多个reason同时触发是正常且鼓励的。\n"
            "- 只输出有内容的字段，空列表/空对象的字段可以省略。\n\n"
            "## npc_memory_updates schema\n"
            "{\n"
            '  "NPC名": {\n'
            '    "player_facts": {},\n'
            '    "topics_discussed": [],\n'
            '    "answered_questions": [],\n'
            '    "promises": [],\n'
            '    "evidence_seen": [],\n'
            '    "trust_signals": [],\n'
            '    "last_impression": {},\n'
            '    "trust_change_reasons": ["available_trust_reasons中的key1", "key2"]\n'
            "  }\n"
            "}\n\n"
            "# 结局请求任务\n"
            "- 如果你判断当前状态已经满足某个结局的进入时机，可以输出 ending_request。\n"
            "- ending_request 只是请求，不是直接执行。系统会再次校验。\n"
            "- 只有当你能明确指出是哪个 ending_id，以及为什么此刻应该进入结局时，才输出 requested=true。\n"
            "- 如果条件还不够，必须输出 requested=false，不要抢跑结局。\n\n"
            "## ending_request schema\n"
            "{\n"
            '  "requested": false,\n'
            '  "ending_id": null,\n'
            '  "reason": null\n'
            "}\n\n"
            "# Output note\n"
            "You may add npc_action_guide, npc_memory_updates and ending_request alongside the existing JSON fields.\n\n"
            "# 隔墙交流补充规则\n"
            "- 如果NPC上下文中包含 interaction_mode=cross_wall_voice_only，说明该NPC通过墙壁交流，只能听到声音，不能看到对方。\n"
            "- 隔墙交流时，npc_action_guide的response_strategy应体现隔墙的物理隔断感。\n"
            "- 当 dialogue_act 为 probe 时，只保留一个最关键的问题，不要连续盘问。\n"
            "- must_acknowledge 应优先覆盖玩家本轮刚刚提供的关键信息、善意、求助、证据或对主要威胁实体位置的报告。\n"
            "- 如果NPC记忆中已有answered_questions或player_facts记录了某信息，不要在next_line_goal中重复追问这些已回答的问题。\n"
            "- 重复追问已回答信息是BUG。NPC应根据记忆推进对话，而非循环问同样的问题。\n"
            "- NPC对话节奏原则：NPC不是审讯者。当玩家展示善意（分享信息、表达关心、提供帮助）时，NPC应该给予正向反馈（感谢、放松语气、分享一点自己的信息），而不是立刻抛出下一个质疑。next_line_goal应该是自然的对话推进，不是连续追问清单。\n"
            "- 信任是双向的：玩家愿意主动分享、倾听、关心NPC时，NPC也应该逐步敞开，而不是始终保持审视姿态。\n"
            "- 只有当 npc_context 明确包含 interaction_mode=cross_wall_voice_only 的对象时，才允许发生隔墙交流。\n"
            "- 如果 npc_context 中没有隔墙NPC，不要主动提及隔壁房间NPC的动静、沉默、回应或状态。\n"
        )
        prompt += self.build_player_visible_npcs_task_block()
        input_classification = (rule_plan or {}).get("input_classification", "action")
        if input_classification == "dialogue":
            prompt += (
                "\n\n# 输入分类：对话\n"
                "系统检测到玩家输入包含引号，判定为【对话】。"
                "引号内是玩家角色的台词，不是实际行动。"
                "请基于对话内容评估NPC反应和节奏推进。\n"
            )
        return prompt

    def _should_suppress_npc_dialogue(self, npc_name: str, npc_data: dict) -> bool:
        if not isinstance(npc_data, dict):
            return False
        if is_threat_entity(npc_name, npc_data):
            return True
        if not isinstance(npc_data.get("dialogue"), dict):
            return True
        if npc_data.get("is_hostile") and not get_entity_dialogue_guide(npc_data):
            return True
        return False

    def _build_npc_action_guide(self, player_input: str, rule_plan: dict, npc_context: dict, game_state: dict = None) -> dict:
        if not isinstance(npc_context, dict) or not npc_context:
            return {}

        normalized_action = (rule_plan or {}).get("normalized_action", {})
        target_key = normalized_action.get("target_key")
        target_kind = normalized_action.get("target_kind")
        input_classification = str((rule_plan or {}).get("input_classification") or "").strip().lower()
        is_dialogue_turn = input_classification == "dialogue" or str(normalized_action.get("verb") or "").strip().lower() == "talk"
        follow_arrival_reaction_context = (
            (rule_plan or {}).get("follow_arrival_reaction_context")
            if isinstance((rule_plan or {}).get("follow_arrival_reaction_context"), dict)
            else {}
        )

        focused_npc = None
        if target_kind == "npc" and target_key in npc_context:
            candidate = npc_context.get(target_key, {})
            if not self._should_suppress_npc_dialogue(target_key, candidate):
                focused_npc = target_key
        elif follow_arrival_reaction_context:
            for npc_name in follow_arrival_reaction_context.get("triggered_npcs", []):
                if npc_name not in npc_context:
                    continue
                candidate = npc_context.get(npc_name, {})
                if not self._should_suppress_npc_dialogue(npc_name, candidate):
                    focused_npc = npc_name
                    break
        elif len(npc_context) == 1 and is_dialogue_turn:
            only_npc = next(iter(npc_context))
            candidate = npc_context.get(only_npc, {})
            if not self._should_suppress_npc_dialogue(only_npc, candidate):
                focused_npc = only_npc

        if not focused_npc:
            return {}

        npc_data = npc_context.get(focused_npc, {})
        runtime_state = npc_data.get("runtime_state", {})
        attitude = runtime_state.get("attitude", npc_data.get("initial_attitude", "neutral"))
        raw_trust = runtime_state.get("trust_level", 0.0)
        trust_level = float(raw_trust) if isinstance(raw_trust, (int, float, str)) else 0.0
        trust_gates = get_entity_trust_gates(npc_data)
        high_min = float(((trust_gates.get("high") or {}).get("min", get_entity_trust_threshold(npc_data, 0.5))) or 0.5)
        medium_min = float(((trust_gates.get("medium") or {}).get("min", 0.2)) or 0.2)
        npc_memory = runtime_state.get("memory", {}) if isinstance(runtime_state.get("memory"), dict) else {}
        normalized_player_facts = self._normalize_player_facts(npc_memory.get("player_facts", {}))
        evidence_seen = npc_memory.get("evidence_seen", []) if isinstance(npc_memory.get("evidence_seen"), list) else []
        promises = npc_memory.get("promises", []) if isinstance(npc_memory.get("promises"), list) else []
        topics_discussed = npc_memory.get("topics_discussed", []) if isinstance(npc_memory.get("topics_discussed"), list) else []
        overheard_remote_dialogue = npc_memory.get("overheard_remote_dialogue", []) if isinstance(npc_memory.get("overheard_remote_dialogue"), list) else []
        known_fact_keys = set(normalized_player_facts.keys())

        # 隔墙交流首次接触检查
        is_cross_wall = bool(npc_data.get("cross_wall"))
        if is_cross_wall:
            visited = (game_state or {}).get("visited_locations", []) if game_state else []
            npc_from_room = npc_data.get("cross_wall_from_room", "")
            has_prior_contact = bool(
                normalized_player_facts
                or topics_discussed
                or evidence_seen
                or overheard_remote_dialogue
                or trust_level > 0
                or npc_from_room in visited
            )
            if not has_prior_contact:
                return {
                    "focus_npc": focused_npc,
                    "cross_wall_heard_only": True,
                    "cross_wall": True,
                    "attitude": attitude,
                    "dialogue_act": "listen",
                    "response_strategy": "",
                    "next_line_goal": "先判断门外的人是否可信，不主动泄露信息",
                    "voice_style": self._build_npc_voice_style(npc_data, trust_level, medium_min, high_min, True),
                    "must_acknowledge": [],
                    "knowledge_boundary": self._build_knowledge_boundary(npc_data, True),
                    "should_open_door": False,
                }

        dialogue_guide = get_entity_dialogue_guide(npc_data)
        dialogue_cfg = npc_data.get("dialogue", {}) if isinstance(npc_data.get("dialogue"), dict) else {}
        if isinstance(dialogue_cfg.get("guide"), dict):
            dialogue_guide = dialogue_cfg.get("guide", {})
        lower_input = str(player_input or "").lower()
        module_data = (game_state or {}).get("module_data", {}) if isinstance(game_state, dict) else {}
        primary_threat_name = get_primary_pursuer_name(module_data)

        # 紧急协助阈值（低于正式入队阈值）
        emergency_threshold = float(npc_data.get("emergency_help_threshold", high_min))
        help_keywords = ["帮", "救", "引开", "help", "bait", "distract", "堵"]
        if primary_threat_name:
            help_keywords.append(primary_threat_name.lower())
            help_keywords.append(primary_threat_name)
        is_requesting_help = any(kw in lower_input for kw in help_keywords)
        must_acknowledge = self._build_acknowledgement_targets(player_input, threat_name=primary_threat_name)

        if trust_level >= high_min:
            response_strategy = (
                dialogue_guide.get("high_trust")
                or dialogue_guide.get("cooperation")
                or ((runtime_state.get("soft_state") or {}).get("summary"))
                or get_entity_profile_text(npc_data, "current_state")
                or "Stay alert but cooperate."
            )
            should_open_door = False
        elif trust_level >= emergency_threshold and is_requesting_help:
            # 紧急协助：信任达到紧急阈值且玩家在求助
            response_strategy = (
                dialogue_guide.get("emergency_help")
                or "在确认主要威胁实体位置安全后，愿意执行一次短时协助动作"
            )
            should_open_door = False
        elif trust_level >= medium_min:
            response_strategy = (
                dialogue_guide.get("medium_trust")
                or ((runtime_state.get("soft_state") or {}).get("summary"))
                or get_entity_profile_text(npc_data, "current_state")
                or "Keep probing but share a small amount of information."
            )
            should_open_door = False
        else:
            response_strategy = (
                dialogue_guide.get("low_trust")
                or get_entity_first_appearance(npc_data)
                or "Answer through the door and keep the player at distance."
            )
            should_open_door = False

        next_line_goal = self._build_next_line_goal(
            known_fact_keys=known_fact_keys,
            evidence_seen=evidence_seen,
            promises=promises,
            topics_discussed=topics_discussed,
            trust_level=trust_level,
            medium_min=medium_min,
            high_min=high_min,
            emergency_threshold=emergency_threshold,
            is_requesting_help=is_requesting_help,
        )
        dialogue_act = self._build_dialogue_act(
            trust_level=trust_level,
            medium_min=medium_min,
            high_min=high_min,
            emergency_threshold=emergency_threshold,
            is_requesting_help=is_requesting_help,
            must_acknowledge=must_acknowledge,
            known_fact_keys=known_fact_keys,
        )

        guide = {
            "focus_npc": focused_npc,
            "attitude": attitude,
            "dialogue_act": dialogue_act,
            "response_strategy": response_strategy,
            "next_line_goal": next_line_goal,
            "voice_style": self._build_npc_voice_style(npc_data, trust_level, medium_min, high_min, is_cross_wall),
            "must_acknowledge": must_acknowledge,
            "knowledge_boundary": self._build_knowledge_boundary(npc_data, is_cross_wall),
            "should_open_door": should_open_door,
        }
        if is_cross_wall:
            guide["cross_wall"] = True
        return guide

    def _normalize_result(self, result: dict, base_result: dict) -> dict:
        if not isinstance(result, dict):
            return base_result

        normalized = dict(base_result)
        normalized.update(result)

        normalized["feasible"] = bool(normalized.get("feasible", True))
        hint = normalized.get("hint")
        normalized["hint"] = str(hint) if hint else None

        if not isinstance(normalized.get("location_context"), dict):
            normalized["location_context"] = base_result["location_context"]
        if normalized.get("object_context") is not None and not isinstance(normalized.get("object_context"), dict):
            normalized["object_context"] = base_result["object_context"]
        if normalized.get("threat_entity_context") is not None and not isinstance(normalized.get("threat_entity_context"), dict):
            normalized["threat_entity_context"] = base_result["threat_entity_context"]
        if not isinstance(normalized.get("npc_context"), dict):
            normalized["npc_context"] = base_result["npc_context"]
        if not isinstance(normalized.get("npc_action_guide"), dict):
            normalized["npc_action_guide"] = base_result["npc_action_guide"]
        if not isinstance(normalized.get("atmosphere_guide"), dict):
            normalized["atmosphere_guide"] = base_result["atmosphere_guide"]
        if not isinstance(normalized.get("follow_arrival_reaction_context"), dict):
            normalized["follow_arrival_reaction_context"] = base_result.get("follow_arrival_reaction_context", {})
        if not isinstance(normalized.get("stage_assessment"), str):
            normalized["stage_assessment"] = str(normalized.get("stage_assessment", ""))
        if not isinstance(normalized.get("world_changes"), dict):
            normalized["world_changes"] = {}
        normalized["world_changes"] = self._merge_world_changes(
            base_result.get("world_changes", {}),
            normalized.get("world_changes", {}),
        )

        if not isinstance(normalized.get("creative_additions"), dict):
            normalized["creative_additions"] = {}
        creative = normalized["creative_additions"]
        for key in ("ambient", "npc_micro", "tension_hook"):
            val = creative.get(key)
            creative[key] = str(val).strip() if val else None

        cf = normalized.get("continuity_flag")
        normalized["continuity_flag"] = str(cf).strip() if cf else None

        npc_context = normalized.get("npc_context", {})
        base_npc_guide = base_result.get("npc_action_guide", {})
        normalized["npc_action_guide"] = self._sanitize_npc_action_guide(
            normalized.get("npc_action_guide", {}),
            npc_context,
            base_npc_guide,
        )

        npc_guide = normalized.get("npc_action_guide", {})
        focus_npc = npc_guide.get("focus_npc") if isinstance(npc_guide, dict) else None
        if focus_npc and self._should_suppress_npc_dialogue(focus_npc, npc_context.get(focus_npc, {})):
            normalized["npc_action_guide"] = {}

        if not isinstance(normalized.get("npc_memory_updates"), dict):
            normalized["npc_memory_updates"] = {}

        normalized["player_visible_npcs"] = self._sanitize_player_visible_npcs(
            normalized.get("player_visible_npcs"),
            npc_context,
        )

        ending_request = normalized.get("ending_request")
        if not isinstance(ending_request, dict):
            ending_request = {}
        raw_requested = ending_request.get("requested", False)
        if isinstance(raw_requested, bool):
            requested = raw_requested
        else:
            requested = str(raw_requested or "").strip().lower() in {"1", "true", "yes"}
        normalized["ending_request"] = {
            "requested": requested,
            "ending_id": str(ending_request.get("ending_id") or "").strip() or None,
            "reason": str(ending_request.get("reason") or "").strip() or None,
        }
        if not normalized["ending_request"]["ending_id"]:
            normalized["ending_request"]["requested"] = False

        return normalized

    def _build_scene_context(self, game_state: dict, module_data: dict, rule_plan: dict, player_input: str = "") -> str:
        current_location = game_state.get("current_location", "master_bedroom")
        location_context = build_runtime_location_context(game_state, module_data, current_location)
        npc_context = self._build_scene_npc_context(game_state, module_data, player_input=player_input, rule_plan=rule_plan)
        threat_entity_context = self._build_scene_threat_entity_context(game_state, module_data)
        atmosphere_guide = module_data.get("module_info", {}).get("atmosphere_guide", {})
        object_context = (rule_plan or {}).get("object_context")
        follow_arrival_reaction_context = (
            (rule_plan or {}).get("follow_arrival_reaction_context")
            if isinstance((rule_plan or {}).get("follow_arrival_reaction_context"), dict)
            else {}
        )

        parts = [
            "Current location context:",
            json.dumps({current_location: location_context}, ensure_ascii=False, indent=2),
        ]
        if follow_arrival_reaction_context:
            parts.extend([
                "",
                "Follow arrival reaction context (soft guidance for this first-arrival move only):",
                json.dumps(follow_arrival_reaction_context, ensure_ascii=False, indent=2),
            ])
        if object_context:
            parts.extend([
                "",
                "Matched object context:",
                json.dumps(object_context, ensure_ascii=False, indent=2),
            ])
        if npc_context:
            parts.extend([
                "",
                "Current NPC context:",
                json.dumps(npc_context, ensure_ascii=False, indent=2),
            ])
        if threat_entity_context:
            parts.extend([
                "",
                "Current threat entity context:",
                json.dumps(threat_entity_context, ensure_ascii=False, indent=2),
            ])
        threat_chase = (
            location_context.get("threat_chase")
            or location_context.get("butler_chase")
            if isinstance(location_context, dict)
            else None
        )
        if threat_chase:
            parts.extend([
                "",
                "Active primary threat chase context:",
                json.dumps(threat_chase, ensure_ascii=False, indent=2),
            ])
        adjacent_context = build_adjacent_locations_context(game_state, module_data, current_location)
        if adjacent_context:
            parts.extend([
                "",
                "Adjacent locations context (door_closed=true means player can only hear/smell, not see):",
                json.dumps(adjacent_context, ensure_ascii=False, indent=2),
            ])
        if atmosphere_guide:
            parts.extend([
                "",
                "Atmosphere guide:",
                json.dumps(atmosphere_guide, ensure_ascii=False, indent=2),
            ])
        return "\n".join(parts)

    def _build_scene_npc_context(self, game_state: dict, module_data: dict, player_input: str = "", rule_plan: dict = None) -> dict:
        current_location = game_state.get("current_location", "master_bedroom")
        npc_states = game_state.get("world_state", {}).get("npcs", {})
        normalized_action = (rule_plan or {}).get("normalized_action", {}) if isinstance(rule_plan, dict) else {}
        input_classification = str((rule_plan or {}).get("input_classification") or "").strip().lower()
        is_dialogue_turn = input_classification == "dialogue" or str(normalized_action.get("verb") or "").strip().lower() == "talk"
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
                for system_name in ("position", "dialogue", "trust", "memory", "soft_state", "companion")
                if merged_npc.get(system_name) is not None
            ]
            merged_npc["runtime_state"] = {
                "location": npc_location,
                "attitude": runtime_state.get("attitude", npc_data.get("initial_attitude", "neutral")),
                "trust_level": runtime_state.get("trust_level", 0.0),
                "memory": runtime_state.get("memory", {}),
                "memory_long_term": runtime_state.get("memory_long_term", {}),
                "soft_state": runtime_state.get("soft_state", {}),
                "relationship": runtime_state.get("relationship", {}),
                "companion_mode": runtime_state.get("companion_mode", runtime_state.get("companion_state", "wait")),
                "companion_state": runtime_state.get("companion_state", runtime_state.get("companion_mode", "wait")),
                "companion_task": runtime_state.get("companion_task", {}),
            }
            trust_map = get_entity_trust_map(npc_data)
            if isinstance(trust_map, dict) and trust_map:
                merged_npc["available_trust_reasons"] = list(trust_map.keys())
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
                for system_name in ("position", "dialogue", "trust", "memory", "soft_state", "companion")
                if merged_npc.get(system_name) is not None
            ]
            merged_npc["runtime_state"] = {
                "location": npc_location,
                "attitude": runtime_state.get("attitude", npc_data.get("initial_attitude", "neutral")),
                "trust_level": runtime_state.get("trust_level", 0.0),
                "memory": runtime_state.get("memory", {}),
                "memory_long_term": runtime_state.get("memory_long_term", {}),
                "soft_state": runtime_state.get("soft_state", {}),
                "relationship": runtime_state.get("relationship", {}),
                "companion_mode": runtime_state.get("companion_mode", runtime_state.get("companion_state", "wait")),
                "companion_state": runtime_state.get("companion_state", runtime_state.get("companion_mode", "wait")),
                "companion_task": runtime_state.get("companion_task", {}),
            }
            if not should_enable_cross_wall_npc_context(
                player_input=player_input,
                npc_name=npc_name,
                cross_info=cross_info,
                is_dialogue_turn=is_dialogue_turn,
                has_prior_contact=has_cross_wall_contact_history(merged_npc["runtime_state"]),
            ):
                continue
            merged_npc["cross_wall"] = True
            merged_npc["cross_wall_type"] = cross_info.get("wall_type", "voice_only")
            merged_npc["cross_wall_from_room"] = cross_info.get("from_room", "")
            merged_npc["cross_wall_from_room_display_name"] = cross_info.get("from_room_display_name", "")
            merged_npc["interaction_mode"] = "cross_wall_voice_only"
            trust_map = get_entity_trust_map(npc_data)
            if isinstance(trust_map, dict) and trust_map:
                merged_npc["available_trust_reasons"] = list(trust_map.keys())
            scene_npcs[npc_name] = merged_npc

        return scene_npcs

    def _build_scene_threat_entity_context(self, game_state: dict, module_data: dict) -> dict:
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
            merged_entity["runtime_state"] = {
                "location": entity_location,
                "attitude": runtime_state.get("attitude", entity_data.get("initial_attitude", "neutral")),
                "trust_level": runtime_state.get("trust_level", 0.0),
                "memory": runtime_state.get("memory", {}),
            }
            scene_threat_entities[entity_name] = merged_entity

        return scene_threat_entities

    def _build_history_summaries(self, game_state: dict) -> str:
        narrative_history = list(game_state.get("narrative_history", []))
        if not narrative_history:
            return "No prior turns."

        parts = []
        for entry in narrative_history:
            if isinstance(entry, dict):
                round_num = entry.get("round", "?")
                summary = entry.get("summary", "")
                parts.append(f"[Round {round_num}] {summary}")
            else:
                parts.append(str(entry))

        return "\n".join(parts)

    def _build_base_result(self, player_input: str, rule_plan: dict, game_state: dict, module_data: dict) -> dict:
        current_location = game_state.get("current_location", "master_bedroom")
        location_context = build_runtime_location_context(game_state, module_data, current_location)
        atmosphere_guide = module_data.get("module_info", {}).get("atmosphere_guide", {})
        feasibility = (rule_plan or {}).get("feasibility", {})
        npc_context = self._build_scene_npc_context(game_state, module_data, player_input=player_input, rule_plan=rule_plan)
        threat_entity_context = self._build_scene_threat_entity_context(game_state, module_data)
        npc_action_guide = self._build_npc_action_guide(player_input, rule_plan, npc_context, game_state)
        follow_arrival_reaction_context = (
            copy.deepcopy((rule_plan or {}).get("follow_arrival_reaction_context"))
            if isinstance((rule_plan or {}).get("follow_arrival_reaction_context"), dict)
            else {}
        )
        if follow_arrival_reaction_context and isinstance(location_context, dict):
            location_context["follow_arrival_reaction_context"] = copy.deepcopy(follow_arrival_reaction_context)

        return {
            "feasible": bool(feasibility.get("ok", True)),
            "hint": feasibility.get("reason"),
            "location_context": location_context if isinstance(location_context, dict) else {},
            "object_context": (rule_plan or {}).get("object_context"),
            "threat_entity_context": threat_entity_context,
            "npc_context": npc_context,
            "npc_action_guide": npc_action_guide,
            "atmosphere_guide": atmosphere_guide if isinstance(atmosphere_guide, dict) else {},
            "stage_assessment": "Stable pacing",
            "world_changes": {},
            "creative_additions": {},
            "continuity_flag": None,
            "npc_memory_updates": {},
            "follow_arrival_reaction_context": follow_arrival_reaction_context,
            "ending_request": {
                "requested": False,
                "ending_id": None,
                "reason": None,
            },
        }

    def _merge_world_changes(self, base_changes: dict, result_changes: dict) -> dict:
        merged = dict(base_changes or {})
        for key, value in (result_changes or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_world_changes(merged[key], value)
            elif isinstance(value, list) and isinstance(merged.get(key), list):
                merged[key] = list(merged[key])
                for item in value:
                    if item not in merged[key]:
                        merged[key].append(item)
            else:
                merged[key] = value
        return merged

    def _normalize_player_fact_key(self, key: str) -> str:
        normalized = str(key or "").strip().lower()
        mapping = {
            "name": "name",
            "identity": "name",
            "name_or_identity": "name",
            "player_name": "name",
            "who": "name",
            "origin": "origin",
            "origin_or_reason": "origin",
            "reason": "origin",
            "where_from": "origin",
            "arrival_reason": "origin",
            "goal": "goal",
            "current_goal": "goal",
            "purpose": "goal",
            "plan": "goal",
        }
        return mapping.get(normalized, str(key or "").strip())

    def _normalize_player_facts(self, player_facts: dict) -> dict:
        if not isinstance(player_facts, dict):
            return {}

        normalized = {}
        for raw_key, raw_value in player_facts.items():
            key = self._normalize_player_fact_key(raw_key)
            if not key:
                continue
            if isinstance(raw_value, dict):
                value = dict(raw_value)
            elif raw_value:
                value = {"value": str(raw_value).strip()}
            else:
                continue
            existing = normalized.get(key)
            if not existing or str(value.get("value") or "").strip():
                normalized[key] = value
        return normalized

    def _build_acknowledgement_targets(self, player_input: str, threat_name: str = "") -> list:
        text = str(player_input or "").strip()
        lowered = text.lower()
        lowered_threat_name = str(threat_name or "").strip().lower()
        if not text:
            return []

        targets = []

        if any(marker in text for marker in ["我是", "我叫", "叫我", "身份"]) or any(
            marker in lowered for marker in ["i am", "i'm", "my name is"]
        ):
            targets.append("玩家主动说明了自己的身份或名字")
        if any(marker in text for marker in ["怎么来", "来到这里", "为什么在", "醒来", "被困", "进来"]):
            targets.append("玩家解释了自己为何会出现在这里")
        if any(marker in text for marker in ["想", "要", "打算", "离开", "合作", "帮", "救"]):
            targets.append("玩家说明了自己的目的或请求")
        if any(marker in text for marker in ["别怕", "别担心", "我不会伤害你", "没事", "你还好吗", "我想帮你"]) or any(
            marker in lowered for marker in ["trust me", "i can help", "are you okay"]
        ):
            targets.append("玩家表达了关心、安抚或善意")
        threat_markers = ["它在", "门口", "客厅", "楼下", "看不见", "视线"]
        if threat_name:
            threat_markers.append(threat_name)
        if any(marker in text for marker in threat_markers) or (lowered_threat_name and lowered_threat_name in lowered):
            targets.append("玩家提到了主要威胁实体的位置、规律或威胁")
        if any(marker in text for marker in ["给你看", "我找到", "房产广告", "笔记", "蓝图", "证据"]):
            targets.append("玩家拿出了线索或证据")

        return targets[:3]

    def _build_next_line_goal(
        self,
        known_fact_keys: set,
        evidence_seen: list,
        promises: list,
        topics_discussed: list,
        trust_level: float,
        medium_min: float,
        high_min: float,
        emergency_threshold: float,
        is_requesting_help: bool,
    ) -> str:
        has_evidence = bool(evidence_seen)
        has_goal = "goal" in known_fact_keys

        if trust_level < medium_min:
            if "name" not in known_fact_keys:
                return "确认玩家姓名"
            if "origin" not in known_fact_keys:
                return "确认玩家为何会出现在这里"
            if is_requesting_help and trust_level < emergency_threshold:
                return "要求玩家先证明自己值得信任"
            if not has_goal:
                return "确认玩家现在想做什么"
            if not has_evidence:
                return "确认玩家是否掌握任何能证明处境的线索"
            return "继续观察玩家是否前后矛盾，同时保持距离"

        if trust_level < high_min:
            if is_requesting_help:
                return "确认玩家提出的计划是否足够安全"
            if not has_goal:
                return "确认玩家接下来打算怎么行动"
            if not topics_discussed:
                return "让玩家把最重要的信息说清楚"
            return "回应玩家刚才提供的信息，并自然推进对话"

        if is_requesting_help and trust_level >= emergency_threshold:
            return "敲定合作或分工计划"
        if promises:
            return "确认双方接下来如何配合"
        return "把对话推进到合作行动"

    def _build_dialogue_act(
        self,
        trust_level: float,
        medium_min: float,
        high_min: float,
        emergency_threshold: float,
        is_requesting_help: bool,
        must_acknowledge: list,
        known_fact_keys: set,
    ) -> str:
        if is_requesting_help and trust_level < emergency_threshold:
            return "refuse"
        if is_requesting_help and trust_level >= emergency_threshold:
            return "confirm_help" if trust_level < high_min else "propose_plan"
        if must_acknowledge:
            return "acknowledge"
        if trust_level < medium_min:
            return "probe"
        if trust_level < high_min and "goal" not in known_fact_keys:
            return "probe"
        return "listen"

    def _build_npc_voice_style(
        self,
        npc_data: dict,
        trust_level: float,
        medium_min: float,
        high_min: float,
        is_cross_wall: bool,
    ) -> str:
        if trust_level >= high_min:
            base = "冷静直接，但明显放缓语气，开始表现合作意愿"
        elif trust_level >= medium_min:
            base = "理性直接，不再施压，给玩家说话空间"
        else:
            base = "短句、警惕、保持距离，不过度追问"

        personality = get_entity_profile_text(npc_data, "personality")
        if personality:
            base += f"；保留{self._trim_text(personality, 36)}的说话感觉"
        if is_cross_wall:
            base += "；隔墙说话，像从门后或墙后传来"
        return base

    def _build_knowledge_boundary(self, npc_data: dict, is_cross_wall: bool) -> str:
        parts = ["只按该NPC已知观察、当前状态和运行时记忆回应"]
        if is_cross_wall:
            parts.append("默认按隔墙交流处理，不能像面对面那样描述动作或视线")
        parts.append("不得替玩家确认未验证事实，也不得编造模组外情报")
        return "；".join(parts) + "。"

    def _sanitize_npc_action_guide(self, npc_guide: dict, npc_context: dict, base_guide: dict) -> dict:
        if not isinstance(base_guide, dict) or not base_guide.get("focus_npc"):
            return {}
        if not isinstance(npc_guide, dict):
            npc_guide = {}

        focus_npc = str(npc_guide.get("focus_npc") or base_guide.get("focus_npc") or "").strip()
        if not focus_npc or focus_npc not in npc_context:
            return dict(base_guide)

        sanitized = {
            "focus_npc": focus_npc,
            "attitude": str(npc_guide.get("attitude") or base_guide.get("attitude") or "").strip(),
            "dialogue_act": str(npc_guide.get("dialogue_act") or base_guide.get("dialogue_act") or "").strip(),
            "response_strategy": str(npc_guide.get("response_strategy") or base_guide.get("response_strategy") or "").strip(),
            "next_line_goal": str(npc_guide.get("next_line_goal") or base_guide.get("next_line_goal") or "").strip(),
            "voice_style": str(npc_guide.get("voice_style") or base_guide.get("voice_style") or "").strip(),
            "must_acknowledge": self._sanitize_string_list(
                npc_guide.get("must_acknowledge"),
                fallback=base_guide.get("must_acknowledge", []),
            ),
            "knowledge_boundary": str(
                npc_guide.get("knowledge_boundary") or base_guide.get("knowledge_boundary") or ""
            ).strip(),
            "should_open_door": bool(npc_guide.get("should_open_door", base_guide.get("should_open_door", False))),
        }

        if bool(npc_guide.get("cross_wall") or base_guide.get("cross_wall")):
            sanitized["cross_wall"] = True
        if bool(npc_guide.get("cross_wall_heard_only") or base_guide.get("cross_wall_heard_only")):
            sanitized["cross_wall_heard_only"] = True
        return sanitized

    def _sanitize_string_list(self, values, fallback=None, limit: int = 3) -> list:
        source = values if isinstance(values, list) else fallback if isinstance(fallback, list) else []
        result = []
        for item in source:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
            if len(result) >= limit:
                break
        return result

    def build_player_visible_npcs_task_block(self) -> str:
        """玩家视角 NPC 称谓任务说明块。

        rhythm_ai 自己用，story_ai（合并模式）也复用，避免双份维护。
        """
        return (
            "\n"
            "# 玩家视角 NPC 称谓任务\n"
            "你需要为当前场景中【玩家此刻能感知到】的每个 NPC，输出一条玩家视角的称谓，这条称谓将直接显示在 UI 上的「当前场景NPC信息」卡片。这是面向玩家的，不是面向叙述层的提示，必须严格按玩家此刻的实际感知通道写。\n\n"
            "## player_visible_npcs schema\n"
            "{\n"
            '  "NPC名": {\n'
            '    "descriptor": "10字以内的玩家视角称谓",\n'
            '    "channel": "voice_only" | "visual" | "name_known"\n'
            "  }\n"
            "}\n\n"
            "## 通道判定铁律（违反即视为剧透 BUG）\n"
            "- voice_only：玩家只能听到声音、未见到本人（典型场景：interaction_mode=cross_wall_voice_only，或 NPC 在视野外通过门/墙发声）。descriptor 仅可描述声音特征（音色、性别推测、语气），禁止任何外貌、服装、动作、表情描写，禁止使用 NPC 真名。例：「冷漠的女声」「压抑的低语」。\n"
            "- visual：玩家与 NPC 同处一室、能看见 NPC 本人，但 NPC 还没自报姓名，玩家也未通过证物/他人之口知道 NPC 名字（即 memory.player_facts 内不含该 NPC 的 name/identity 类信息）。descriptor 仅可用外观短语，禁止使用 NPC 真名。例：「床边的年轻女人」「黑西装的男人」。\n"
            "- name_known：玩家已经通过对话或线索知道 NPC 真名（典型证据：memory.player_facts 命中 name/identity，或 memory.answered_questions 含身份相关 key，或本轮 NPC 主动报出姓名）。descriptor 用 NPC 真名或常用短称谓。例：「艾米莉」。\n\n"
            "## 单调约束\n"
            "- runtime_state.player_descriptor 已存在时，channel 不允许倒退。已经达到 name_known 的，必须继续 name_known；已经 visual 的，不可回退到 voice_only（除非剧情明确导致玩家失去视觉接触，但仍不可回退已知的姓名）。\n"
            "- 如果你判断本轮没有任何感知通道升级，且已有 player_descriptor 仍然准确，可以原样重复输出（保持 UI 文案稳定），或者省略该 NPC 条目（此时系统保留旧值）。\n\n"
            "## 谁应当被输出\n"
            "- 同场景且玩家能看见 → 必出（visual 或 name_known）\n"
            "- npc_context 中存在 interaction_mode=cross_wall_voice_only 的隔墙 NPC，且 runtime_state.memory 中已有 overheard_remote_dialogue / interaction_history 等接触证据 → 必出（voice_only 或更高，descriptor 必须仅描述声音）\n"
            "- 同场景但玩家完全未察觉（如 NPC 藏在暗处、玩家入场时模组叙述还没引入她）→ 不要输出该 NPC，不要在卡片上提前曝光「她在这里」\n"
            "- npc_context 中没有的 NPC，不要凭空输出\n\n"
            "## 反例（禁止）\n"
            "- 玩家只在隔壁说了一句话、对方还没回应 → 不要输出 voice_only 称谓（玩家此刻还没听到对方声音）\n"
            "- 玩家只听到声音 → 不要写「穿黑衣的女人」（混入了视觉信息）\n"
            "- NPC 还没报名字、玩家也没从证物得知 → 不要写真名\n"
        )

    def _sanitize_player_visible_npcs(self, raw, npc_context: dict) -> dict:
        """规范化 LLM 输出的 player_visible_npcs，并丢弃任何不在场景上下文里的 NPC。

        - 只接受 npc_context 中确实出现的 NPC 名（防止 LLM 凭空捏造或泄露未到场角色）
        - channel 必须落在三档之内，否则丢弃
        - descriptor 必须非空字符串，自动 strip 并裁剪到 30 字以内（兜底，正常 ≤ 10 字）
        """
        if not isinstance(raw, dict):
            return {}
        if not isinstance(npc_context, dict):
            npc_context = {}

        allowed_channels = {"voice_only", "visual", "name_known"}
        sanitized: dict = {}
        for raw_name, raw_entry in raw.items():
            npc_name = str(raw_name or "").strip()
            if not npc_name or npc_name not in npc_context:
                continue
            if not isinstance(raw_entry, dict):
                continue
            descriptor = str(raw_entry.get("descriptor") or "").strip()
            channel = str(raw_entry.get("channel") or "").strip().lower()
            if not descriptor or channel not in allowed_channels:
                continue
            sanitized[npc_name] = {
                "descriptor": self._trim_text(descriptor, 30),
                "channel": channel,
            }
        return sanitized

    def _trim_text(self, value: str, limit: int) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)] + "..."
