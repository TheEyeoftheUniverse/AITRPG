from astrbot.api import logger
from astrbot.api.star import Context
from ..game_state.location_context import (
    build_runtime_location_context,
    get_module_npcs,
    get_module_threat_entities,
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
            "# Output note\n"
            "You may add npc_action_guide alongside the existing JSON fields."
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

    def _build_npc_action_guide(self, player_input: str, rule_plan: dict, npc_context: dict) -> dict:
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
        trust_level = float(runtime_state.get("trust_level", 0.0) or 0.0)
        trust_threshold = float(npc_data.get("trust_threshold", 0.5) or 0.5)
        npc_memory = runtime_state.get("memory", {}) if isinstance(runtime_state.get("memory"), dict) else {}
        player_facts = npc_memory.get("player_facts", {}) if isinstance(npc_memory.get("player_facts"), dict) else {}
        conversation_flags = npc_memory.get("conversation_flags", {}) if isinstance(npc_memory.get("conversation_flags"), dict) else {}
        pending_questions = npc_memory.get("pending_questions", []) if isinstance(npc_memory.get("pending_questions"), list) else []
        dialogue_guide = (
            npc_data.get("dialogue_guide", {})
            if isinstance(npc_data.get("dialogue_guide"), dict)
            else {}
        )
        key_info = npc_data.get("key_info", {}) if isinstance(npc_data.get("key_info"), dict) else {}
        lower_input = str(player_input or "").lower()
        next_line_goal = self._derive_next_line_goal(conversation_flags, pending_questions, player_facts, trust_level, trust_threshold)

        if trust_level >= trust_threshold:
            response_strategy = (
                dialogue_guide.get("high_trust")
                or dialogue_guide.get("cooperation")
                or npc_data.get("current_state")
                or "Stay alert but cooperate."
            )
            revealable_info = list(key_info.keys())
            should_open_door = "open" in lower_input or "cooperate" in lower_input
            if not next_line_goal:
                next_line_goal = "Confirm the cooperation plan and share key information."
        elif trust_level >= max(0.2, trust_threshold / 2):
            response_strategy = (
                dialogue_guide.get("medium_trust")
                or npc_data.get("current_state")
                or "Keep probing but share a small amount of information."
            )
            revealable_info = list(key_info.keys())[:1]
            should_open_door = False
            if not next_line_goal:
                next_line_goal = "Test the player's intent and maybe reveal one useful clue."
        else:
            response_strategy = (
                dialogue_guide.get("low_trust")
                or npc_data.get("first_appearance")
                or "Answer through the door and keep the player at distance."
            )
            revealable_info = []
            should_open_door = False
            if not next_line_goal:
                next_line_goal = "Verify who the player is and whether they can be trusted."

        return {
            "focus_npc": focused_npc,
            "attitude": attitude,
            "response_strategy": response_strategy,
            "next_line_goal": next_line_goal,
            "revealable_info": revealable_info,
            "should_open_door": should_open_door,
        }

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
            }
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
        npc_action_guide = self._build_npc_action_guide(player_input, rule_plan, npc_context)

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
            "world_changes": self._build_npc_soft_memory_changes(npc_action_guide),
            "creative_additions": {},
            "continuity_flag": None,
        }

    def _derive_next_line_goal(
        self,
        conversation_flags: dict,
        pending_questions: list,
        player_facts: dict,
        trust_level: float,
        trust_threshold: float,
    ) -> str:
        if pending_questions:
            mapping = {
                "player_name": "Confirm the player's name.",
                "player_origin": "Confirm how the player got here.",
                "supporting_evidence": "Ask the player for evidence that supports their story.",
                "player_goal": "Confirm what the player wants to do next.",
            }
            first_pending = pending_questions[0]
            return mapping.get(first_pending, str(first_pending))

        if not conversation_flags.get("knows_player_name"):
            return "Confirm the player's name."
        if not conversation_flags.get("knows_player_origin_claim"):
            return "Confirm how the player got here."
        if not conversation_flags.get("evidence_presented") and trust_level < trust_threshold:
            return "Ask the player for proof before trusting them."
        if not conversation_flags.get("knows_player_goal"):
            return "Ask what the player wants from the NPC."
        if player_facts and trust_level >= max(0.2, trust_threshold / 2):
            return "React to the player's known story and move the exchange forward."
        return ""

    def _build_npc_soft_memory_changes(self, npc_action_guide: dict) -> dict:
        if not isinstance(npc_action_guide, dict):
            return {}
        focus_npc = npc_action_guide.get("focus_npc")
        if not focus_npc:
            return {}

        next_line_goal = str(npc_action_guide.get("next_line_goal") or "").strip()
        memory_update = {
            "last_impression": {
                "focus": next_line_goal,
            } if next_line_goal else {},
        }
        memory_update = {key: value for key, value in memory_update.items() if value}
        if not memory_update:
            return {}

        return {
            "npc_updates": {
                focus_npc: {
                    "memory": memory_update,
                }
            }
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
