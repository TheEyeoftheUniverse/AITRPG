from astrbot.api import logger
from astrbot.api.star import Context
from ..game_state.location_context import (
    get_entity_first_appearance,
    get_entity_profile_text,
    is_threat_entity,
)
from .provider_failover import (
    ProviderFailoverError,
    normalize_provider_candidates,
    text_chat_with_fallback,
)
from .usage_metrics import extract_provider_meta, extract_usage_metrics

import json
import os
import re


class NarrativeAI:
    """Narrative layer: turn structured outputs into player-facing prose."""

    RECENT_DIALOGUE_TURNS = 10
    OPENING_USER_TEXT = "缓缓苏醒"

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
            logger.error(f"[NarrativeAI] Prompt config not found: {prompts_path}")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"[NarrativeAI] Prompt config JSON error: {e}")
            return {}

    def _get_provider_candidates(self) -> list[str]:
        candidates = normalize_provider_candidates(
            self.provider_name,
            self.fallback_provider_names,
        )
        if not candidates:
            logger.error("[NarrativeAI] narrative_ai_provider is not configured")
        return candidates

    def _get_provider_meta(self, provider) -> dict:
        provider_meta = extract_provider_meta(provider)
        return {
            "id": provider_meta.get("id"),
            "model": provider_meta.get("configured_model"),
            "base_url": provider_meta.get("base_url"),
        }

    async def _chat_once(self, provider, prompt: str):
        return await provider.text_chat(prompt=prompt, contexts=[])

    async def generate(
        self,
        player_input: str,
        rule_plan: dict,
        rule_result: dict,
        rhythm_result: dict,
        narrative_history: list,
        history: list = None,
        trace_id: str = None,
    ) -> dict:
        if history is None:
            history = []

        provider_candidates = self._get_provider_candidates()
        usage_metrics = {}

        if not provider_candidates:
            logger.error("[NarrativeAI] No provider available")
            raise RuntimeError("文案AI生成失败：未找到可用 LLM provider，请使用重试按钮。")

        prompt = self._build_prompt(
            player_input=player_input,
            rule_plan=rule_plan,
            rule_result=rule_result,
            rhythm_result=rhythm_result,
            narrative_history=narrative_history,
            history=history,
        )
        if not prompt:
            raise RuntimeError("文案AI生成失败：未找到可用提示词，请使用重试按钮。")

        try:
            logger.info(
                "[NarrativeAI] sending request: primary_provider=%s fallback_count=%s prompt_len=%s prompt_history_turns=%s provider_history_turns=%s preview=%s",
                self.provider_name,
                len(provider_candidates) - 1,
                len(prompt),
                len(narrative_history or []),
                len(history or []),
                self._trim_text(prompt.replace("\n", "\\n"), 220),
            )
            outcome = await text_chat_with_fallback(
                context=self.context,
                primary_provider_id=self.provider_name,
                fallback_provider_ids=self.fallback_provider_names,
                prompt=prompt,
                contexts=[],
                trace_label="NarrativeAI.generate",
            )
            llm_response = outcome.response
            response_text = (
                llm_response.completion_text
                if hasattr(llm_response, "completion_text")
                else str(llm_response)
            )
            usage_metrics = outcome.metrics
            response_text = self._strip_json_fence(response_text)
            result = json.loads(response_text)
        except ProviderFailoverError as e:
            logger.error("[NarrativeAI] provider chain failed: %s", e)
            if trace_id:
                self._call_metrics[trace_id] = e.metrics
            raise RuntimeError("文案AI生成失败：所有候选模型都不可用，请检查主模型与备用模型配置。") from e
        except json.JSONDecodeError as e:
            logger.warning(f"[NarrativeAI] JSON decode failed. Response={response_text}")
            if trace_id:
                self._call_metrics[trace_id] = usage_metrics
            raise RuntimeError("文案AI生成失败：返回结果不是合法 JSON，请使用重试按钮。") from e
        except Exception as e:
            logger.error(
                "[NarrativeAI] generate error: type=%s err=%r cause=%r",
                type(e).__name__,
                e,
                getattr(e, "__cause__", None),
            )
            if trace_id:
                self._call_metrics[trace_id] = usage_metrics
            raise RuntimeError("文案AI生成失败，请使用重试按钮。") from e

        narrative = self._normalize_narrative_text(result.get("narrative")).strip()
        if not narrative:
            if trace_id:
                self._call_metrics[trace_id] = usage_metrics
            raise RuntimeError("文案AI生成失败：返回内容为空，请使用重试按钮。")

        summary = str(result.get("summary") or "").strip() or self._build_default_summary(player_input)

        logger.info(f"[NarrativeAI] Narrative generated, len={len(narrative)}")
        if trace_id:
            self._call_metrics[trace_id] = usage_metrics
        return {
            "narrative": narrative,
            "summary": summary,
        }

    def _strip_json_fence(self, text: str) -> str:
        text = (text or "").strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    def _normalize_narrative_text(self, text: str) -> str:
        content = str(text or "")
        if not content:
            return ""
        return re.sub(r"<br\s*/?>", "\n", content, flags=re.IGNORECASE)

    def _build_history_text(self, narrative_history: list, recent_turn_count: int = RECENT_DIALOGUE_TURNS) -> str:
        history_list = list(narrative_history) if narrative_history else []
        if not history_list:
            return "No older turn summaries."

        if len(history_list) <= recent_turn_count:
            return "No summaries older than the recent dialogue window."

        older_entries = history_list[:-recent_turn_count]
        parts = []

        for i, entry in enumerate(older_entries):
            if isinstance(entry, dict):
                round_num = entry.get("round", i + 1)
                player_text = self._trim_text(entry.get("player_input", ""), 70)
                summary = self._trim_text(entry.get("summary", ""), 70)
                if player_text and summary:
                    parts.append(f"[Round {round_num}] player: {player_text} | summary: {summary}")
                elif summary:
                    parts.append(f"[Round {round_num}] {summary}")
            else:
                parts.append(str(entry))

        return "\n".join(parts) if parts else "No older turn summaries."

    def _build_prompt(
        self,
        player_input: str,
        rule_plan: dict,
        rule_result: dict,
        rhythm_result: dict,
        narrative_history: list,
        history: list = None,
    ) -> str:
        if history is None:
            history = []

        rhythm_result = self._normalize_rhythm_result(rhythm_result)
        rule_plan = rule_plan if isinstance(rule_plan, dict) else {}

        normalized_action = rule_plan.get("normalized_action", {})
        location_context = rhythm_result.get("location_context", {})
        object_context = rhythm_result.get("object_context")
        threat_entity_context = rhythm_result.get("threat_entity_context", {})
        raw_npc_context = rhythm_result.get("npc_context", {})
        raw_npc_action_guide = rhythm_result.get("npc_action_guide", {})
        dialogue_npcs, _, npc_action_guide = self._sanitize_npc_prompt_inputs(raw_npc_context, raw_npc_action_guide)
        threat_entities = list(location_context.get("present_threats", []) or [])
        butler_chase = {}
        if isinstance(rhythm_result.get("threat_chase"), dict):
            butler_chase = rhythm_result.get("threat_chase", {})
        elif isinstance(rhythm_result.get("butler_chase"), dict):
            butler_chase = rhythm_result.get("butler_chase", {})
        atmosphere_guide = rhythm_result.get("atmosphere_guide", {})
        feasible = rhythm_result.get("feasible", True)
        hint = rhythm_result.get("hint")
        stage_assessment = rhythm_result.get("stage_assessment", "")
        history_text = self._build_history_text(
            narrative_history,
            recent_turn_count=self.RECENT_DIALOGUE_TURNS,
        )
        recent_dialogue_text = self._build_recent_dialogue_text(history)
        dialogue_memory = self._build_dialogue_memory(history, player_input, npc_action_guide)
        compact_location = self._compact_location_context(location_context)
        compact_object = self._compact_object_context(object_context)
        compact_npc = self._compact_npc_context(dialogue_npcs, npc_action_guide)
        npc_dialogue_contract = self._build_npc_dialogue_contract(dialogue_npcs, npc_action_guide)
        compact_threats = self._compact_threat_entity_context(threat_entity_context)
        compact_check = self._compact_check_result(rule_result)
        has_current_scene_npc = bool(compact_npc)
        creative_additions = rhythm_result.get("creative_additions", {})
        continuity_flag = rhythm_result.get("continuity_flag")
        follow_arrival_reaction_context = (
            rhythm_result.get("follow_arrival_reaction_context", {})
            if isinstance(rhythm_result.get("follow_arrival_reaction_context"), dict)
            else {}
        )

        input_classification = rule_plan.get("input_classification", "action")
        if input_classification == "dialogue":
            classification_note = "dialogue（系统识别：引号内是玩家角色的台词，不是行动）"
        else:
            classification_note = "action（系统识别：玩家在执行行动）"

        player_block = (
            "Player turn input:\n"
            f"- raw_input: {self._trim_text(player_input, 120)}\n"
            f"- input_type: {classification_note}\n"
            f"- normalized_action: {json.dumps(normalized_action, ensure_ascii=False)}"
        )

        rule_block = (
            "Rule layer summary:\n"
            f"- target_kind: {normalized_action.get('target_kind')}\n"
            f"- target_key: {normalized_action.get('target_key')}\n"
            f"- feasible: {rule_plan.get('feasibility', {}).get('ok', True)}\n"
            f"- check: {json.dumps(rule_plan.get('check', {}), ensure_ascii=False)}\n"
            f"- result: {json.dumps(compact_check, ensure_ascii=False)}"
        )

        rhythm_block = (
            "Rhythm layer output:\n"
            f"- feasible: {feasible}\n"
            f"- stage_assessment: {stage_assessment}\n"
            f"- location_context: {json.dumps(compact_location, ensure_ascii=False)}\n"
            f"- object_context: {json.dumps(compact_object, ensure_ascii=False) if compact_object else 'none'}\n"
            f"- npc_context: {json.dumps(compact_npc, ensure_ascii=False) if compact_npc else 'none'}\n"
            f"- threat_entity_context: {json.dumps(compact_threats, ensure_ascii=False) if compact_threats else 'none'}\n"
            f"- npc_action_guide: {json.dumps(npc_action_guide, ensure_ascii=False) if npc_action_guide else 'none'}\n"
            f"- npc_dialogue_contract: {json.dumps(npc_dialogue_contract, ensure_ascii=False) if npc_dialogue_contract else 'none'}\n"
            f"- dialogue_memory: {json.dumps(dialogue_memory, ensure_ascii=False)}\n"
            f"- atmosphere_guide: {json.dumps(atmosphere_guide, ensure_ascii=False)}"
        )
        if follow_arrival_reaction_context:
            rhythm_block += (
                "\n- follow_arrival_reaction_context: "
                f"{json.dumps(follow_arrival_reaction_context, ensure_ascii=False)}"
            )

        has_creative = isinstance(creative_additions, dict) and any(
            v for v in creative_additions.values() if v
        )
        if has_creative:
            rhythm_block += f"\n- creative_additions: {json.dumps(creative_additions, ensure_ascii=False)}"
        if continuity_flag:
            rhythm_block += f"\n- continuity_flag: {continuity_flag}"

        if threat_entities:
            rhythm_block += f"\n- threat_entities: {json.dumps(threat_entities, ensure_ascii=False)}"

        if butler_chase.get("active"):
            compact_chase = {
                "active": butler_chase.get("active"),
                "status": butler_chase.get("status"),
                "entity_name": butler_chase.get("entity_name"),
                "entity_location": butler_chase.get("entity_location") or butler_chase.get("butler_location"),
                "player_location": butler_chase.get("player_location"),
                "blocked_at": butler_chase.get("blocked_at"),
                "last_target_location": butler_chase.get("last_target_location"),
                "same_location_rounds": butler_chase.get("same_location_rounds"),
                "player_relation": butler_chase.get("player_relation"),
            }
            rhythm_block += f"\n- threat_chase: {json.dumps(compact_chase, ensure_ascii=False)}"

        if not feasible and hint:
            rhythm_block += f"\n- blocked_reason: {hint}"

        prompt_template = self.config.get("narrative_ai_prompt", "").strip()
        if not prompt_template:
            prompt_template = self.prompts.get("narrative_ai_prompt", "")

        if not prompt_template:
            logger.error("[NarrativeAI] narrative_ai_prompt not found")
            return ""

        location_name = location_context.get("name", "unknown location")
        prompt = prompt_template.replace("{rule_info}", f"{player_block}\n\n{rule_block}")
        prompt = prompt.replace(
            "{rhythm_info}",
            f"{rhythm_block}\n\nRecent dialogue transcript:\n{recent_dialogue_text}\n\nLonger history summaries:\n{history_text}",
        )
        prompt = prompt.replace("{location}", location_name)

        prompt += (
            "\n\n# Additional constraints\n"
            "- Respond to the player's current words, not only to the room description.\n"
            "- Treat the recent dialogue transcript as the authoritative short-term memory for what was just asked and answered.\n"
            "- Treat dialogue_memory as already established facts for this conversation unless the player explicitly changes their statement.\n"
            "- When npc_dialogue_contract exists, it is the highest-priority authority for what the NPC knows, how the NPC speaks, and what the NPC may reveal this turn.\n"
            "- Treat npc_action_guide and npc_dialogue_contract as stronger than recent_dialogue if they conflict on reveal limits or NPC intent.\n"
            "- If the player already answered a question in the recent dialogue transcript, continue from that answer instead of asking the exact same question again.\n"
            "- If the latest player_input is an answer to the NPC's previous question, acknowledge that answer and ask a different follow-up or provide a new reaction.\n"
            "- If npc_action_guide exists, use it to decide how the NPC replies this turn.\n"
            "- Directly react to npc_dialogue_contract.dialogue_plan.must_acknowledge before changing topic.\n"
            "- Shape the reply according to npc_dialogue_contract.dialogue_plan.dialogue_act.\n"
            "- If npc_dialogue_contract.allowed_reveals is non-empty, only those approved texts may be stated directly as NPC knowledge this turn.\n"
            "- Never reveal items listed in npc_dialogue_contract.forbidden_reveals, even if they appear elsewhere in npc_context.\n"
            "- Do not invent new NPC secrets, plans, deductions, or certainty beyond npc_dialogue_contract.knowledge_boundary.\n"
            "- When the player is clearly talking to an NPC, move the dialogue forward instead of restating the same atmosphere.\n"
            "- Only mention a cross-wall NPC when npc_context explicitly contains that NPC for this turn.\n"
            "- If npc_context does not explicitly contain a cross-wall NPC, do not mention adjacent-room NPC silence, movement, breathing, or reactions.\n"
        )
        if rhythm_result.get("arrival_mode"):
            prompt += (
                "- This turn is a pure arrival into the scene. The player moved here but did not speak.\n"
                "- If an entity is present, write its reaction to the player's arrival, not a reply to words the player never said.\n"
                "- Do not imply the player introduced themselves, explained anything, or asked a question on this turn.\n"
            )
        if not has_current_scene_npc:
            prompt += (
                "- npc_context is none for this turn. There is no dialogue-capable NPC in the player's current location.\n"
                "- Do not continue a conversation from another room as if the NPC can still immediately hear and answer.\n"
                "- Do not generate new NPC dialogue, quoted replies, or remote call-and-response unless the current scene data explicitly contains a dialogue-capable NPC.\n"
                "- Treat recent dialogue as background memory only; the active response must come from the current room and current action.\n"
            )
        if threat_entities:
            prompt += (
                f"- Threat entities are present: {', '.join(threat_entities)}.\n"
                "- Threat entities are not NPCs. They cannot speak, ask questions, or hold a conversation.\n"
                "- You may describe their posture, movement, breathing, distance, gaze pressure, or pursuit.\n"
                "- Do not write quoted speech, polite verbal exchange, or reported dialogue for threat entities.\n"
            )
        if butler_chase.get("active") and butler_chase.get("status") != "blocked":
            relation = butler_chase.get("player_relation", "separate_rooms")
            threat_label = str(butler_chase.get("entity_name") or "the primary threat entity").strip()
            prompt += (
                f"- PRIMARY THREAT CHASE IS ACTIVE. {threat_label} is pursuing the player. This turn MUST convey ongoing chase tension.\n"
                "- Do NOT write this as a calm room introduction. The threat has not ended.\n"
            )
            if relation == "same_room":
                prompt += "- The primary threat is in the SAME ROOM. Write direct, immediate physical threat and pressure.\n"
            elif relation == "separate_rooms":
                prompt += (
                    "- The primary threat is in a DIFFERENT ROOM but closing in. Write approaching footsteps, distant breathing, "
                    "the sense of being hunted. The player just escaped but the pursuit continues.\n"
                )
            elif relation == "blocked_outside_current_room":
                prompt += "- The primary threat is BLOCKED outside the current room. Write door-pressure, waiting presence, muffled sounds.\n"
        elif butler_chase.get("active") and butler_chase.get("status") == "blocked":
            prompt += (
                "- The primary threat chase is active but the threat is currently blocked by a door.\n"
                "- Pure movement can use normal room descriptions, but the overall atmosphere should still feel uneasy.\n"
            )
        return prompt

    def _build_compact_retry_prompt(
        self,
        player_input: str,
        rule_plan: dict,
        rule_result: dict,
        rhythm_result: dict,
        history: list
    ) -> str:
        rhythm_result = self._normalize_rhythm_result(rhythm_result)
        rule_plan = rule_plan if isinstance(rule_plan, dict) else {}
        normalized_action = rule_plan.get("normalized_action", {})
        raw_npc_context = rhythm_result.get("npc_context", {})
        raw_npc_action_guide = rhythm_result.get("npc_action_guide", {})
        dialogue_npcs, _, npc_action_guide = self._sanitize_npc_prompt_inputs(raw_npc_context, raw_npc_action_guide)
        threat_entities = list((rhythm_result.get("location_context", {}) or {}).get("present_threats", []) or [])
        npc_dialogue_contract = self._build_npc_dialogue_contract(dialogue_npcs, npc_action_guide)
        butler_chase = {}
        if isinstance(rhythm_result.get("threat_chase"), dict):
            butler_chase = rhythm_result.get("threat_chase", {})
        elif isinstance(rhythm_result.get("butler_chase"), dict):
            butler_chase = rhythm_result.get("butler_chase", {})
        compact_payload = {
            "player_input": self._trim_text(player_input, 120),
            "action": normalized_action,
            "feasible": rhythm_result.get("feasible", True),
            "hint": rhythm_result.get("hint"),
            "check": self._compact_check_result(rule_result),
            "location": self._compact_location_context(rhythm_result.get("location_context", {})),
            "object": self._compact_object_context(rhythm_result.get("object_context")),
            "npc": self._compact_npc_context(dialogue_npcs, npc_action_guide),
            "threat_entity_context": self._compact_threat_entity_context(rhythm_result.get("threat_entity_context")),
            "npc_action_guide": npc_action_guide,
            "npc_dialogue_contract": npc_dialogue_contract,
            "threat_entities": threat_entities,
            "recent_dialogue": self._build_recent_dialogue_messages(history),
            "dialogue_memory": self._build_dialogue_memory(
                history,
                player_input,
                npc_action_guide,
            ),
            "current_scene_has_npc": bool(dialogue_npcs),
            "arrival_mode": bool(rhythm_result.get("arrival_mode")),
        }
        follow_arrival_reaction_context = (
            rhythm_result.get("follow_arrival_reaction_context", {})
            if isinstance(rhythm_result.get("follow_arrival_reaction_context"), dict)
            else {}
        )
        if follow_arrival_reaction_context:
            compact_payload["follow_arrival_reaction_context"] = follow_arrival_reaction_context
        if butler_chase.get("active"):
            compact_payload["threat_chase"] = {
                "active": butler_chase.get("active"),
                "status": butler_chase.get("status"),
                "entity_name": butler_chase.get("entity_name"),
                "entity_location": butler_chase.get("entity_location") or butler_chase.get("butler_location"),
                "player_location": butler_chase.get("player_location"),
                "blocked_at": butler_chase.get("blocked_at"),
                "player_relation": butler_chase.get("player_relation"),
            }
        creative_additions = rhythm_result.get("creative_additions", {})
        has_creative = isinstance(creative_additions, dict) and any(
            v for v in creative_additions.values() if v
        )
        if has_creative:
            compact_payload["creative_additions"] = creative_additions
        continuity_flag = rhythm_result.get("continuity_flag")
        if continuity_flag:
            compact_payload["continuity_flag"] = continuity_flag
        prompt = (
            "You are the narrative layer for a TRPG session.\n"
            "Use the structured data below to write one short in-world response and one short summary.\n"
            "Reply as JSON only: {\"narrative\":\"...\",\"summary\":\"...\"}\n"
            "Keep the response concise and directly answer the player's latest words.\n"
            "Treat recent_dialogue as the authoritative short-term memory.\n"
            "Treat dialogue_memory as already established facts.\n"
            "When npc_dialogue_contract exists, it is the highest-priority authority for NPC intent, tone, and reveal limits.\n"
            "Do not ask the exact same question again if the player already answered it in recent_dialogue.\n"
            "React to npc_dialogue_contract.dialogue_plan.must_acknowledge before changing topic.\n"
            "Follow npc_dialogue_contract.dialogue_plan.dialogue_act.\n"
            "If npc_dialogue_contract.allowed_reveals is non-empty, only those approved texts may be stated directly.\n"
            "Never reveal npc_dialogue_contract.forbidden_reveals.\n"
            "Do not invent NPC secrets or certainty beyond npc_dialogue_contract.knowledge_boundary.\n"
            "If creative_additions is present, naturally weave the non-null entries into the narrative.\n"
            "If continuity_flag is present, treat it as the canonical explanation for the improvised details.\n\n"
            f"{json.dumps(compact_payload, ensure_ascii=False, indent=2)}"
        )
        if not compact_payload["current_scene_has_npc"]:
            prompt += (
                "\n\nNo dialogue-capable NPC is present in the player's current location for this turn.\n"
                "Do not continue dialogue from another room.\n"
                "Do not write quoted NPC speech or imply that an off-screen NPC immediately answers.\n"
                "Focus on the player's current room, current action, and immediate sensory feedback."
            )
        if compact_payload["threat_entities"]:
            prompt += (
                f"\n\nThreat entities are present: {', '.join(compact_payload['threat_entities'])}.\n"
                "Threat entities are not NPCs and cannot speak.\n"
                "Describe silent pressure, movement, gaze, posture, distance, or pursuit.\n"
                "Do not write quoted speech for them."
            )
        if compact_payload["arrival_mode"]:
            prompt += (
                "\n\nThis turn is scene arrival only.\n"
                "The player moved into the room and did not say anything.\n"
                "If something reacts, make it a reaction to presence or footsteps, not a reply to dialogue."
            )
        if compact_payload.get("threat_chase", {}).get("active") and compact_payload.get("threat_chase", {}).get("status") != "blocked":
            relation = compact_payload.get("threat_chase", {}).get("player_relation", "separate_rooms")
            threat_label = str(compact_payload.get("threat_chase", {}).get("entity_name") or "the primary threat entity").strip()
            prompt += (
                f"\n\nPRIMARY THREAT CHASE IS ACTIVE. {threat_label} is pursuing the player.\n"
                "This turn MUST convey ongoing chase tension. Do NOT write a calm room introduction.\n"
            )
            if relation == "same_room":
                prompt += "The primary threat is in the SAME ROOM — write direct physical threat.\n"
            elif relation == "separate_rooms":
                prompt += "The primary threat is in a DIFFERENT ROOM but closing in — write approaching footsteps, distant breathing, the sense of being hunted.\n"
        return prompt

    def _normalize_rhythm_result(self, rhythm_result: dict) -> dict:
        if not isinstance(rhythm_result, dict):
            return {}

        normalized = dict(rhythm_result)
        if not isinstance(normalized.get("location_context"), dict):
            normalized["location_context"] = {}
        if normalized.get("object_context") is not None and not isinstance(normalized.get("object_context"), dict):
            normalized["object_context"] = None
        if not isinstance(normalized.get("threat_entity_context"), dict):
            normalized["threat_entity_context"] = {}
        if not isinstance(normalized.get("npc_context"), dict):
            normalized["npc_context"] = {}
        if not isinstance(normalized.get("npc_action_guide"), dict):
            normalized["npc_action_guide"] = {}
        if not isinstance(normalized.get("atmosphere_guide"), dict):
            normalized["atmosphere_guide"] = {}
        if not isinstance(normalized.get("creative_additions"), dict):
            normalized["creative_additions"] = {}
        cf = normalized.get("continuity_flag")
        normalized["continuity_flag"] = str(cf).strip() if cf else None
        return normalized

    def _build_default_summary(self, player_input: str) -> str:
        text = str(player_input or "").strip()
        if not text:
            return "玩家执行了一个动作"
        if len(text) <= 30:
            return f"玩家：{text}"
        return f"玩家：{text[:27]}..."

    def _get_message_kind(self, message: dict) -> str:
        if not isinstance(message, dict):
            return ""
        metadata = message.get("metadata")
        if isinstance(metadata, dict):
            kind = str(metadata.get("aitrpg_kind") or "").strip()
            if kind:
                return kind
        return str(message.get("aitrpg_kind") or message.get("kind") or "").strip()

    def _is_opening_turn(self, user_message: dict, assistant_message: dict) -> bool:
        if self._get_message_kind(user_message) == "opening":
            return True
        if self._get_message_kind(assistant_message) == "opening":
            return True
        user_content = self._extract_message_text(user_message.get("content"))
        return user_content == self.OPENING_USER_TEXT

    def _build_recent_dialogue_messages(self, history: list, turn_limit: int = RECENT_DIALOGUE_TURNS) -> list:
        if not isinstance(history, list):
            return []

        items = []
        for message in history:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = self._extract_message_text(message.get("content"))
            content = self._trim_text(content, 120)
            if not content:
                continue
            items.append({
                "role": role,
                "content": content,
                "kind": self._get_message_kind(message),
            })

        turns = []
        idx = 0
        while idx + 1 < len(items):
            user_message = items[idx]
            assistant_message = items[idx + 1]
            if user_message["role"] != "user" or assistant_message["role"] != "assistant":
                idx += 1
                continue
            if self._is_opening_turn(user_message, assistant_message):
                idx += 2
                continue
            turns.append([user_message, assistant_message])
            idx += 2

        recent_turns = turns[-turn_limit:]
        flattened = []
        for turn in recent_turns:
            flattened.extend(turn)
        return flattened

    def _build_recent_dialogue_text(self, history: list, turn_limit: int = RECENT_DIALOGUE_TURNS) -> str:
        recent_messages = self._build_recent_dialogue_messages(history, turn_limit=turn_limit)
        if not recent_messages:
            return "No recent dialogue."

        lines = []
        for message in recent_messages:
            role = "玩家" if message["role"] == "user" else "旁白/NPC"
            lines.append(f"- {role}: {message['content']}")
        return "\n".join(lines)

    def _build_dialogue_memory(self, history: list, player_input: str, npc_action_guide: dict) -> dict:
        recent_messages = self._build_recent_dialogue_messages(
            history,
            turn_limit=self.RECENT_DIALOGUE_TURNS,
        )
        player_claims = [
            item["content"]
            for item in recent_messages
            if item.get("role") == "user" and item.get("content")
        ]
        latest_npc_line = next(
            (item["content"] for item in reversed(recent_messages) if item.get("role") == "assistant"),
            "",
        )
        latest_player_claim = self._trim_text(player_input, 120)
        established_facts = self._extract_player_facts(player_claims + ([latest_player_claim] if latest_player_claim else []))
        pending_question = self._extract_pending_question(latest_npc_line)
        focus_npc = npc_action_guide.get("focus_npc") if isinstance(npc_action_guide, dict) else None
        return {
            "focus_npc": focus_npc,
            "latest_npc_line": self._trim_text(latest_npc_line, 120),
            "pending_question": pending_question,
            "latest_player_reply": latest_player_claim,
            "recent_player_claims": [self._trim_text(item, 80) for item in player_claims[-3:]],
            "established_facts": established_facts,
        }

    def _extract_pending_question(self, text: str) -> str:
        text = str(text or "").strip()
        if not text:
            return ""
        question_markers = ["？", "?", "谁", "为什么", "为何", "怎么", "来这里", "做什么", "叫什么"]
        if any(marker in text for marker in question_markers):
            return self._trim_text(text, 120)
        return ""

    def _extract_player_facts(self, claims: list) -> dict:
        facts = {
            "name_or_identity": "",
            "origin_or_reason": "",
            "current_goal": "",
        }
        for raw_claim in claims:
            claim = str(raw_claim or "").strip()
            if not claim:
                continue
            lowered = claim.lower()
            if not facts["name_or_identity"] and any(marker in claim for marker in ["我是", "我叫", "叫我", "身份"]) :
                facts["name_or_identity"] = self._trim_text(claim, 80)
            if not facts["origin_or_reason"] and any(marker in claim for marker in ["不知道怎么", "怎么来", "为什么在", "来到这里", "来这里", "醒来", "被困", "进来"]) :
                facts["origin_or_reason"] = self._trim_text(claim, 80)
            if not facts["current_goal"] and any(marker in claim for marker in ["想", "要", "打算", "离开", "合作", "找", "调查"]):
                facts["current_goal"] = self._trim_text(claim, 80)
            if not facts["name_or_identity"] and any(marker in lowered for marker in ["i am", "i'm", "my name is"]):
                facts["name_or_identity"] = self._trim_text(claim, 80)

        return {key: value for key, value in facts.items() if value}

    def _extract_message_text(self, content) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
            return "".join(parts).strip()
        return str(content or "").strip()

    def _get_dialogue_npc_context(self, npc_context: dict) -> dict:
        if not isinstance(npc_context, dict):
            return {}
        return {
            name: data
            for name, data in npc_context.items()
            if not self._is_nonverbal_npc(name, data)
        }

    def _get_nonverbal_npc_context(self, npc_context: dict) -> dict:
        if not isinstance(npc_context, dict):
            return {}
        return {
            name: data
            for name, data in npc_context.items()
            if self._is_nonverbal_npc(name, data)
        }

    def _sanitize_npc_prompt_inputs(self, npc_context: dict, npc_action_guide: dict):
        dialogue_npcs = self._get_dialogue_npc_context(npc_context)
        nonverbal_npcs = self._get_nonverbal_npc_context(npc_context)
        sanitized_guide = npc_action_guide if isinstance(npc_action_guide, dict) else {}
        focus_npc = sanitized_guide.get("focus_npc")
        if focus_npc and focus_npc not in dialogue_npcs:
            sanitized_guide = {}
        return dialogue_npcs, nonverbal_npcs, sanitized_guide

    def _compact_npc_context(self, npc_context: dict, npc_action_guide: dict) -> dict:
        dialogue_npcs, _, sanitized_guide = self._sanitize_npc_prompt_inputs(npc_context, npc_action_guide)
        if not dialogue_npcs:
            return {}

        focus_npc = sanitized_guide.get("focus_npc") if isinstance(sanitized_guide, dict) else None
        if focus_npc and focus_npc in dialogue_npcs:
            source = {focus_npc: dialogue_npcs[focus_npc]}
        else:
            first_key = next(iter(dialogue_npcs))
            source = {first_key: dialogue_npcs[first_key]}

        compact = {}
        for name, data in source.items():
            runtime_state = data.get("runtime_state", {}) if isinstance(data, dict) else {}
            npc_memory = runtime_state.get("memory", {}) if isinstance(runtime_state.get("memory"), dict) else {}
            compact[name] = {
                "name": data.get("name", name) if isinstance(data, dict) else name,
                "appearance": self._trim_text(get_entity_profile_text(data, "appearance"), 80) if isinstance(data, dict) else "",
                "current_state": self._trim_text(get_entity_profile_text(data, "current_state"), 100) if isinstance(data, dict) else "",
                "first_appearance": self._trim_text(get_entity_first_appearance(data), 100) if isinstance(data, dict) else "",
                "attitude": runtime_state.get("attitude"),
                "trust_level": runtime_state.get("trust_level"),
                "soft_state": runtime_state.get("soft_state", {}),
                "enabled_systems": list(data.get("enabled_systems", []) or []) if isinstance(data, dict) else [],
                "reveal_state": data.get("reveal_state", {}) if isinstance(data, dict) else {},
                "memory": self._compact_npc_memory(npc_memory),
            }
        return compact

    def _build_npc_dialogue_contract(self, npc_context: dict, npc_action_guide: dict) -> dict:
        if not isinstance(npc_context, dict) or not isinstance(npc_action_guide, dict):
            return {}

        focus_npc = str(npc_action_guide.get("focus_npc") or "").strip()
        if not focus_npc or focus_npc not in npc_context:
            return {}

        npc_data = npc_context.get(focus_npc, {})
        runtime_state = npc_data.get("runtime_state", {}) if isinstance(npc_data.get("runtime_state"), dict) else {}
        npc_memory = runtime_state.get("memory", {}) if isinstance(runtime_state.get("memory"), dict) else {}

        return {
            "focus_npc": focus_npc,
            "persona": {
                "appearance": self._trim_text(get_entity_profile_text(npc_data, "appearance"), 80),
                "personality": self._trim_text(get_entity_profile_text(npc_data, "personality"), 90),
                "background_summary": self._trim_text(get_entity_profile_text(npc_data, "background"), 140),
                "current_state": self._trim_text(get_entity_profile_text(npc_data, "current_state"), 100),
                "soft_state_summary": self._trim_text(((runtime_state.get("soft_state") or {}).get("summary") if isinstance(runtime_state.get("soft_state"), dict) else ""), 120),
                "speaking_style": self._trim_text(npc_action_guide.get("voice_style", ""), 100),
            },
            "relationship": {
                "attitude": npc_action_guide.get("attitude") or runtime_state.get("attitude"),
                "trust_level": runtime_state.get("trust_level"),
                "companion_state": runtime_state.get("companion_state"),
                "cross_wall": bool(npc_action_guide.get("cross_wall") or npc_data.get("cross_wall")),
                "cross_wall_heard_only": bool(npc_action_guide.get("cross_wall_heard_only")),
            },
            "enabled_systems": list(npc_data.get("enabled_systems", []) or []),
            "reveal_state": npc_data.get("reveal_state", {}) if isinstance(npc_data.get("reveal_state"), dict) else {},
            "memory": self._compact_npc_memory(npc_memory),
            "dialogue_plan": {
                "dialogue_act": str(npc_action_guide.get("dialogue_act") or "").strip(),
                "response_strategy": self._trim_text(npc_action_guide.get("response_strategy", ""), 120),
                "next_line_goal": self._trim_text(npc_action_guide.get("next_line_goal", ""), 80),
                "must_acknowledge": self._sanitize_string_list(npc_action_guide.get("must_acknowledge"), limit=3),
                "should_open_door": bool(npc_action_guide.get("should_open_door")),
            },
            "allowed_reveals": self._sanitize_allowed_reveals(npc_action_guide.get("allowed_reveals"), limit=3),
            "forbidden_reveals": self._sanitize_string_list(npc_action_guide.get("forbidden_reveals"), limit=6),
            "knowledge_boundary": self._trim_text(npc_action_guide.get("knowledge_boundary", ""), 180),
        }

    def _compact_threat_entity_context(self, threat_entity_context: dict) -> dict:
        if not isinstance(threat_entity_context, dict) or not threat_entity_context:
            return {}

        compact = {}
        for name, data in threat_entity_context.items():
            if not isinstance(data, dict):
                continue
            behavior = data.get("behavior", {}) if isinstance(data.get("behavior"), dict) else {}
            runtime_state = data.get("runtime_state", {}) if isinstance(data.get("runtime_state"), dict) else {}
            compact[name] = {
                "name": data.get("name", name),
                "appearance": self._trim_text(data.get("appearance", ""), 100),
                "appearance_warning": self._trim_text(data.get("appearance_warning", ""), 120),
                "current_state": self._trim_text(data.get("current_state", ""), 120),
                "behavior": {
                    key: self._trim_text(value, 100)
                    for key, value in behavior.items()
                    if value
                },
                "runtime_state": {
                    "location": str(runtime_state.get("location") or "").strip(),
                },
            }
        return compact

    def _is_nonverbal_npc(self, npc_name: str, npc_data: dict) -> bool:
        return is_threat_entity(npc_name, npc_data) or not isinstance((npc_data or {}).get("dialogue"), dict)

    def _compact_location_context(self, location_context: dict) -> dict:
        if not isinstance(location_context, dict):
            return {}
        return {
            "name": location_context.get("name"),
            "description": self._trim_text(
                location_context.get("runtime_description", location_context.get("description", "")),
                160,
            ),
            "base_description": self._trim_text(location_context.get("description", ""), 120),
            "active_npc_present_description": self._trim_text(
                location_context.get("active_npc_present_description", ""),
                120,
            ),
            "active_threat_present_description": self._trim_text(
                location_context.get("active_threat_present_description", ""),
                120,
            ),
            "present_threats": list(location_context.get("present_threats", []) or []),
            "threat_present": bool(location_context.get("threat_present")),
            "atmosphere": location_context.get("atmosphere"),
        }

    def _compact_object_context(self, object_context: dict):
        if not isinstance(object_context, dict):
            return None
        return {
            "name": object_context.get("name"),
            "type": object_context.get("type"),
            "used_for": object_context.get("used_for"),
            "success_result": self._trim_text(object_context.get("success_result", ""), 120),
            "failure_result": self._trim_text(object_context.get("failure_result", ""), 80),
        }

    def _compact_npc_memory(self, npc_memory: dict) -> dict:
        if not isinstance(npc_memory, dict):
            return {}

        player_facts = self._normalize_player_facts(npc_memory.get("player_facts", {}))
        compact_facts = {}
        for key, value in player_facts.items():
            if not isinstance(value, dict):
                continue
            fact_value = str(value.get("value") or "").strip()
            if fact_value:
                compact_facts[key] = self._trim_text(fact_value, 80)

        conversation_flags = npc_memory.get("conversation_flags", {}) if isinstance(npc_memory.get("conversation_flags"), dict) else {}
        pending_questions = npc_memory.get("pending_questions", []) if isinstance(npc_memory.get("pending_questions"), list) else []
        answered_questions = npc_memory.get("answered_questions", []) if isinstance(npc_memory.get("answered_questions"), list) else []
        topics_discussed = npc_memory.get("topics_discussed", []) if isinstance(npc_memory.get("topics_discussed"), list) else []
        evidence_seen = self._compact_memory_items(npc_memory.get("evidence_seen"), item_key="key", limit=2)
        promises = self._compact_memory_items(npc_memory.get("promises"), item_key="content", limit=2)
        last_impression = npc_memory.get("last_impression", {}) if isinstance(npc_memory.get("last_impression"), dict) else {}

        compact = {
            "player_facts": compact_facts,
            "conversation_flags": conversation_flags,
            "pending_questions": pending_questions[:3],
            "answered_questions": answered_questions[:3],
            "topics_discussed": topics_discussed[:5],
            "evidence_seen": evidence_seen,
            "promises": promises,
            "last_impression": {
                key: self._trim_text(value, 60)
                for key, value in last_impression.items()
                if value
            },
        }
        return {key: value for key, value in compact.items() if value}

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

    def _compact_memory_items(self, items, item_key: str, limit: int) -> list:
        if not isinstance(items, list):
            return []
        compact = []
        for item in items:
            text = ""
            if isinstance(item, dict):
                text = str(item.get(item_key) or "").strip()
            elif item:
                text = str(item).strip()
            if text and text not in compact:
                compact.append(self._trim_text(text, 80))
            if len(compact) >= limit:
                break
        return compact

    def _sanitize_allowed_reveals(self, items, limit: int = 3) -> list:
        if not isinstance(items, list):
            return []

        sanitized = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            text = str(item.get("text") or "").strip()
            marker = (key, text)
            if not key or not text or marker in seen:
                continue
            sanitized.append({
                "key": key,
                "text": self._trim_text(text, 140),
            })
            seen.add(marker)
            if len(sanitized) >= limit:
                break
        return sanitized

    def _sanitize_string_list(self, values, limit: int) -> list:
        if not isinstance(values, list):
            return []
        result = []
        for item in values:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(self._trim_text(text, 80))
            if len(result) >= limit:
                break
        return result

    def _compact_check_result(self, rule_result: dict) -> dict:
        rule_result = rule_result if isinstance(rule_result, dict) else {}
        return {
            "check_type": rule_result.get("check_type"),
            "skill": rule_result.get("skill"),
            "difficulty": rule_result.get("difficulty"),
            "success": rule_result.get("success"),
            "result_description": rule_result.get("result_description"),
        }

    def _trim_text(self, value: str, limit: int) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)] + "..."

    def _strip_trailing_punctuation(self, text: str) -> str:
        return str(text or "").rstrip("。！？!?；;，,、 ")
