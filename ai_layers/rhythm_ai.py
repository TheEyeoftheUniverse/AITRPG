from astrbot.api import logger
from astrbot.api.star import Context
from ..game_state.location_context import (
    build_adjacent_locations_context,
    build_runtime_location_context,
    get_cross_wall_npcs,
    get_module_npcs,
    get_module_threat_entities,
    is_threat_entity,
)
from .usage_metrics import extract_usage_metrics

import json
import os


class RhythmAI:
    """Pacing layer: stage judgment, soft guidance, and NPC response direction."""

    def __init__(self, context: Context, provider_name: str = None, config: dict = None):
        self.context = context
        self.provider_name = provider_name
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

    def _get_provider(self):
        provider = None
        if self.provider_name:
            provider = self.context.get_provider(self.provider_name)
            if not provider:
                logger.warning(f"[RhythmAI] Provider {self.provider_name} not found, fallback to current provider")
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

    async def process(
        self,
        intent: dict,
        player_input: str,
        rule_plan: dict,
        rule_result: dict,
        game_state: dict,
        module_data: dict,
        history: list = None,
        trace_id: str = None,
    ) -> dict:
        if history is None:
            history = []

        provider = self._get_provider()
        base_result = self._build_base_result(player_input, rule_plan, game_state, module_data)

        if not provider:
            logger.warning("[RhythmAI] No provider available, using base result")
            return base_result

        prompt_template = self.config.get("rhythm_ai_prompt", "").strip()
        if not prompt_template:
            prompt_template = self.prompts.get("rhythm_ai_prompt", "")

        if not prompt_template:
            logger.warning("[RhythmAI] rhythm_ai_prompt not found, using base result")
            return base_result

        prompt = self._build_prompt(
            prompt_template=prompt_template,
            intent=intent,
            player_input=player_input,
            rule_plan=rule_plan,
            rule_result=rule_result,
            game_state=game_state,
            module_data=module_data,
        )

        try:
            # RhythmAI should rely on summarized history in the prompt, not on raw chat history.
            llm_response = await provider.text_chat(prompt=prompt, contexts=[])
            response_text = (
                llm_response.completion_text
                if hasattr(llm_response, "completion_text")
                else str(llm_response)
            )
            if trace_id:
                self._call_metrics[trace_id] = extract_usage_metrics(llm_response, prompt, response_text)
            result = json.loads(self._strip_json_fence(response_text))
            normalized = self._normalize_result(result, base_result)
            logger.info(f"[RhythmAI] process result: {normalized}")
            return normalized
        except json.JSONDecodeError:
            logger.warning("[RhythmAI] JSON decode failed, using base result")
            return base_result
        except Exception as e:
            logger.error(f"[RhythmAI] process error: {e}")
            return base_result

    def _build_prompt(
        self,
        prompt_template: str,
        intent: dict,
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
        scene_context = self._build_scene_context(game_state, module_data, rule_plan)

        prompt = prompt_template.replace("{current_location}", current_location)
        prompt = prompt.replace("{round_count}", str(round_count))
        prompt = prompt.replace("{clues_found}", json.dumps(clues_found, ensure_ascii=False))
        prompt = prompt.replace("{stages}", stages)
        prompt = prompt.replace("{history_summaries}", history_summaries)
        prompt = prompt.replace("{player_input}", player_input)
        prompt = prompt.replace("{intent}", json.dumps(intent or {}, ensure_ascii=False))
        prompt = prompt.replace("{rule_plan}", json.dumps(rule_plan or {}, ensure_ascii=False, indent=2))
        prompt = prompt.replace("{rule_result}", json.dumps(rule_result or {}, ensure_ascii=False, indent=2))
        prompt = prompt.replace("{scene_context}", scene_context)
        prompt += (
            "\n\n# Additional tasks\n"
            "5. If the current scene has an interactable NPC, also output npc_action_guide for the narrative layer.\n"
            "6. npc_action_guide must only use known NPC data and runtime state.\n\n"
            "# npc_action_guide schema\n"
            "{\n"
            '  "focus_npc": "npc name or null",\n'
            '  "attitude": "current attitude toward the player",\n'
            '  "response_strategy": "how the NPC should respond this turn",\n'
            '  "next_line_goal": "what the NPC wants to confirm or push this turn",\n'
            '  "revealable_info": ["keys that may be naturally revealed this turn"],\n'
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
            "# Output note\n"
            "You may add npc_action_guide and npc_memory_updates alongside the existing JSON fields.\n\n"
            "# 隔墙交流补充规则\n"
            "- 如果NPC上下文中包含 interaction_mode=cross_wall_voice_only，说明该NPC通过墙壁交流，只能听到声音，不能看到对方。\n"
            "- 隔墙交流时，npc_action_guide的response_strategy应体现隔墙的物理隔断感。\n"
            "- 如果NPC记忆中已有answered_questions或player_facts记录了某信息，不要在next_line_goal中重复追问这些已回答的问题。\n"
            "- 重复追问已回答信息是BUG。NPC应根据记忆推进对话，而非循环问同样的问题。\n"
        )
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
        if npc_name == "管家":
            return True
        if not isinstance(npc_data, dict):
            return False
        if npc_data.get("is_hostile") and not npc_data.get("dialogue_guide") and not npc_data.get("key_info"):
            return True
        return False

    def _build_npc_action_guide(self, player_input: str, rule_plan: dict, npc_context: dict, game_state: dict = None) -> dict:
        if not isinstance(npc_context, dict) or not npc_context:
            return {}

        normalized_action = (rule_plan or {}).get("normalized_action", {})
        target_key = normalized_action.get("target_key")
        target_kind = normalized_action.get("target_kind")

        focused_npc = None
        if target_kind == "npc" and target_key in npc_context:
            candidate = npc_context.get(target_key, {})
            if not self._should_suppress_npc_dialogue(target_key, candidate):
                focused_npc = target_key
        elif len(npc_context) == 1:
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
        trust_gates = npc_data.get("trust_gates", {})
        high_min = float(trust_gates.get("high", {}).get("min", npc_data.get("trust_threshold", 0.5)))
        medium_min = float(trust_gates.get("medium", {}).get("min", 0.2))

        # 隔墙交流首次接触检查
        is_cross_wall = bool(npc_data.get("cross_wall"))
        if is_cross_wall:
            npc_memory = runtime_state.get("memory", {}) if isinstance(runtime_state.get("memory"), dict) else {}
            visited = (game_state or {}).get("visited_locations", []) if game_state else []
            npc_from_room = npc_data.get("cross_wall_from_room", "")
            has_prior_contact = bool(
                npc_memory.get("player_facts")
                or npc_memory.get("topics_discussed")
                or npc_memory.get("evidence_seen")
                or trust_level > 0
                or npc_from_room in visited
            )
            if not has_prior_contact:
                return {
                    "focus_npc": focused_npc,
                    "cross_wall_heard_only": True,
                    "cross_wall": True,
                    "attitude": attitude,
                    "response_strategy": "",
                    "next_line_goal": "",
                    "revealable_info": [],
                    "should_open_door": False,
                }

        dialogue_guide = (
            npc_data.get("dialogue_guide", {})
            if isinstance(npc_data.get("dialogue_guide"), dict)
            else {}
        )
        key_info = npc_data.get("key_info", {}) if isinstance(npc_data.get("key_info"), dict) else {}
        lower_input = str(player_input or "").lower()

        # 紧急协助阈值（低于正式入队阈值）
        emergency_threshold = float(npc_data.get("emergency_help_threshold", high_min))
        help_keywords = ["帮", "救", "引开", "help", "bait", "distract", "管家", "堵"]
        is_requesting_help = any(kw in lower_input for kw in help_keywords)

        if trust_level >= high_min:
            response_strategy = (
                dialogue_guide.get("high_trust")
                or dialogue_guide.get("cooperation")
                or npc_data.get("current_state")
                or "Stay alert but cooperate."
            )
            revealable_info = list(key_info.keys())
            should_open_door = "open" in lower_input or "cooperate" in lower_input
        elif trust_level >= emergency_threshold and is_requesting_help:
            # 紧急协助：信任达到紧急阈值且玩家在求助
            response_strategy = (
                dialogue_guide.get("emergency_help")
                or "在确认管家位置安全后，愿意执行一次短时协助动作"
            )
            revealable_info = list(key_info.keys())[:1]
            should_open_door = False
        elif trust_level >= medium_min:
            response_strategy = (
                dialogue_guide.get("medium_trust")
                or npc_data.get("current_state")
                or "Keep probing but share a small amount of information."
            )
            revealable_info = list(key_info.keys())[:1]
            should_open_door = False
        else:
            response_strategy = (
                dialogue_guide.get("low_trust")
                or npc_data.get("first_appearance")
                or "Answer through the door and keep the player at distance."
            )
            revealable_info = []
            should_open_door = False

        guide = {
            "focus_npc": focused_npc,
            "attitude": attitude,
            "response_strategy": response_strategy,
            "next_line_goal": "",
            "revealable_info": revealable_info,
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

        npc_guide = normalized.get("npc_action_guide", {})
        focus_npc = npc_guide.get("focus_npc") if isinstance(npc_guide, dict) else None
        npc_context = normalized.get("npc_context", {})
        if focus_npc and self._should_suppress_npc_dialogue(focus_npc, npc_context.get(focus_npc, {})):
            normalized["npc_action_guide"] = {}

        if not isinstance(normalized.get("npc_memory_updates"), dict):
            normalized["npc_memory_updates"] = {}

        return normalized

    def _build_scene_context(self, game_state: dict, module_data: dict, rule_plan: dict) -> str:
        current_location = game_state.get("current_location", "master_bedroom")
        location_context = build_runtime_location_context(game_state, module_data, current_location)
        npc_context = self._build_scene_npc_context(game_state, module_data)
        threat_entity_context = self._build_scene_threat_entity_context(game_state, module_data)
        atmosphere_guide = module_data.get("module_info", {}).get("atmosphere_guide", {})
        object_context = (rule_plan or {}).get("object_context")

        parts = [
            "Current location context:",
            json.dumps({current_location: location_context}, ensure_ascii=False, indent=2),
        ]
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
        butler_chase = location_context.get("butler_chase") if isinstance(location_context, dict) else None
        if butler_chase:
            parts.extend([
                "",
                "Active butler chase context:",
                json.dumps(butler_chase, ensure_ascii=False, indent=2),
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

    def _build_scene_npc_context(self, game_state: dict, module_data: dict) -> dict:
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
            merged_npc["runtime_state"] = {
                "location": npc_location,
                "attitude": runtime_state.get("attitude", npc_data.get("initial_attitude", "neutral")),
                "trust_level": runtime_state.get("trust_level", 0.0),
                "memory": runtime_state.get("memory", {}),
                "companion_state": runtime_state.get("companion_state", "inactive"),
            }
            trust_map = npc_data.get("trust_map", {})
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
            merged_npc["runtime_state"] = {
                "location": npc_location,
                "attitude": runtime_state.get("attitude", npc_data.get("initial_attitude", "neutral")),
                "trust_level": runtime_state.get("trust_level", 0.0),
                "memory": runtime_state.get("memory", {}),
                "companion_state": runtime_state.get("companion_state", "inactive"),
            }
            merged_npc["cross_wall"] = True
            merged_npc["cross_wall_type"] = cross_info.get("wall_type", "voice_only")
            merged_npc["cross_wall_from_room"] = cross_info.get("from_room", "")
            merged_npc["interaction_mode"] = "cross_wall_voice_only"
            trust_map = npc_data.get("trust_map", {})
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
        npc_context = self._build_scene_npc_context(game_state, module_data)
        threat_entity_context = self._build_scene_threat_entity_context(game_state, module_data)
        npc_action_guide = self._build_npc_action_guide(player_input, rule_plan, npc_context, game_state)

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
