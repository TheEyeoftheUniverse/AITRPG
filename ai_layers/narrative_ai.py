from astrbot.api import logger
from astrbot.api.star import Context

import json
import os


class NarrativeAI:
    """Narrative layer: turn structured outputs into player-facing prose."""

    def __init__(self, context: Context, provider_name: str = None, config: dict = None):
        self.context = context
        self.provider_name = provider_name
        self.config = config or {}
        self.prompts = self._load_prompts()

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

    def _get_provider(self):
        provider = None
        if self.provider_name:
            provider = self.context.get_provider(self.provider_name)
            if not provider:
                logger.warning(
                    f"[NarrativeAI] Provider {self.provider_name} not found, fallback to current provider"
                )
        if not provider:
            provider = self.context.get_using_provider()
        return provider

    def _get_provider_meta(self, provider) -> dict:
        provider_config = getattr(provider, "provider_config", {}) or {}
        return {
            "id": provider_config.get("id") or getattr(getattr(provider, "meta", lambda: None)(), "id", None),
            "model": provider_config.get("model") or getattr(provider, "get_model", lambda: None)(),
            "base_url": provider_config.get("api_base"),
        }

    async def _chat_once(self, provider, prompt: str):
        return await provider.text_chat(prompt=prompt, contexts=[])

    async def _chat_with_fresh_provider(self, provider, prompt: str):
        provider_cls = getattr(provider, "__class__", None)
        provider_config = getattr(provider, "provider_config", None)
        provider_settings = getattr(provider, "provider_settings", None)
        if not provider_cls or not isinstance(provider_config, dict) or provider_settings is None:
            raise RuntimeError("provider does not expose rebuildable config")

        fresh_provider = provider_cls(dict(provider_config), provider_settings)
        try:
            return await fresh_provider.text_chat(prompt=prompt, contexts=[])
        finally:
            terminate = getattr(fresh_provider, "terminate", None)
            if callable(terminate):
                await terminate()

    async def generate(
        self,
        player_input: str,
        rule_plan: dict,
        rule_result: dict,
        rhythm_result: dict,
        narrative_history: list,
        history: list = None
    ) -> dict:
        if history is None:
            history = []

        provider = self._get_provider()

        if not provider:
            logger.error("[NarrativeAI] No provider available")
            return self._build_local_fallback_narrative(player_input, rule_plan, rule_result, rhythm_result)

        prompt = self._build_prompt(
            player_input=player_input,
            rule_plan=rule_plan,
            rule_result=rule_result,
            rhythm_result=rhythm_result,
            narrative_history=narrative_history,
            history=history,
        )

        try:
            provider_meta = self._get_provider_meta(provider)
            logger.info(
                "[NarrativeAI] sending request: provider=%s model=%s prompt_len=%s prompt_history_turns=%s provider_history_turns=%s preview=%s",
                provider_meta.get("id"),
                provider_meta.get("model"),
                len(prompt),
                len(narrative_history or []),
                len(history or []),
                self._trim_text(prompt.replace("\n", "\\n"), 220),
            )
            # Narrative prompt already contains the condensed history and current structured context.
            # Sending the raw provider history again makes this request much heavier than the rule/rhythm calls.
            llm_response = await self._chat_once(provider, prompt)
            response_text = (
                llm_response.completion_text
                if hasattr(llm_response, "completion_text")
                else str(llm_response)
            )
            response_text = self._strip_json_fence(response_text)
            result = json.loads(response_text)

            narrative = str(result.get("narrative") or "").strip()
            summary = str(result.get("summary") or "").strip()
            if not narrative:
                return self._build_local_fallback_narrative(player_input, rule_plan, rule_result, rhythm_result)
            if not summary:
                summary = self._build_default_summary(player_input)

            logger.info(f"[NarrativeAI] Narrative generated, len={len(narrative)}")
            return {
                "narrative": narrative,
                "summary": summary,
            }
        except json.JSONDecodeError:
            logger.warning(f"[NarrativeAI] JSON decode failed. Response={response_text}")
            return {
                "narrative": response_text,
                "summary": self._build_default_summary(player_input),
            }
        except Exception as e:
            logger.error(
                "[NarrativeAI] generate error: type=%s err=%r cause=%r",
                type(e).__name__,
                e,
                getattr(e, "__cause__", None),
            )
            compact_prompt = self._build_compact_retry_prompt(
                player_input=player_input,
                rule_plan=rule_plan,
                rule_result=rule_result,
                rhythm_result=rhythm_result,
                history=history,
            )
            try:
                logger.info(
                    "[NarrativeAI] retry with compact prompt: prompt_len=%s preview=%s",
                    len(compact_prompt),
                    self._trim_text(compact_prompt.replace("\n", "\\n"), 220),
                )
                retry_response = await self._chat_once(provider, compact_prompt)
                retry_text = (
                    retry_response.completion_text
                    if hasattr(retry_response, "completion_text")
                    else str(retry_response)
                )
                retry_text = self._strip_json_fence(retry_text)
                retry_result = json.loads(retry_text)
                narrative = str(retry_result.get("narrative") or "").strip()
                summary = str(retry_result.get("summary") or "").strip()
                if narrative:
                    return {
                        "narrative": narrative,
                        "summary": summary or self._build_default_summary(player_input),
                    }
            except Exception as retry_error:
                logger.error(
                    "[NarrativeAI] compact retry failed: type=%s err=%r cause=%r",
                    type(retry_error).__name__,
                    retry_error,
                    getattr(retry_error, "__cause__", None),
                )

            try:
                logger.info("[NarrativeAI] retry with fresh provider instance")
                fresh_response = await self._chat_with_fresh_provider(provider, compact_prompt)
                fresh_text = (
                    fresh_response.completion_text
                    if hasattr(fresh_response, "completion_text")
                    else str(fresh_response)
                )
                fresh_text = self._strip_json_fence(fresh_text)
                fresh_result = json.loads(fresh_text)
                narrative = str(fresh_result.get("narrative") or "").strip()
                summary = str(fresh_result.get("summary") or "").strip()
                if narrative:
                    return {
                        "narrative": narrative,
                        "summary": summary or self._build_default_summary(player_input),
                    }
            except Exception as fresh_error:
                logger.error(
                    "[NarrativeAI] fresh provider retry failed: type=%s err=%r cause=%r",
                    type(fresh_error).__name__,
                    fresh_error,
                    getattr(fresh_error, "__cause__", None),
                )

            return self._build_local_fallback_narrative(player_input, rule_plan, rule_result, rhythm_result)

    def _strip_json_fence(self, text: str) -> str:
        text = (text or "").strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    def _build_history_text(self, narrative_history: list) -> str:
        history_list = list(narrative_history) if narrative_history else []
        if not history_list:
            return "游戏开始。"

        total = len(history_list)
        cutoff = max(0, total - 6)
        parts = []

        for i, entry in enumerate(history_list):
            if isinstance(entry, dict):
                round_num = entry.get("round", i + 1)
                player_text = self._trim_text(entry.get("player_input", ""), 70)
                summary = self._trim_text(entry.get("summary", ""), 70)
                if player_text and summary:
                    parts.append(f"[第{round_num}轮] 玩家：{player_text} | 摘要：{summary}")
                elif summary:
                    parts.append(f"[第{round_num}轮] {summary}")
            else:
                parts.append(str(entry))

        return "\n".join(parts[cutoff:])

    def _build_prompt(
        self,
        player_input: str,
        rule_plan: dict,
        rule_result: dict,
        rhythm_result: dict,
        narrative_history: list,
        history: list
    ) -> str:
        rhythm_result = self._normalize_rhythm_result(rhythm_result)
        rule_plan = rule_plan if isinstance(rule_plan, dict) else {}

        normalized_action = rule_plan.get("normalized_action", {})
        location_context = rhythm_result.get("location_context", {})
        object_context = rhythm_result.get("object_context")
        npc_context = rhythm_result.get("npc_context", {})
        npc_action_guide = rhythm_result.get("npc_action_guide", {})
        atmosphere_guide = rhythm_result.get("atmosphere_guide", {})
        feasible = rhythm_result.get("feasible", True)
        hint = rhythm_result.get("hint")
        stage_assessment = rhythm_result.get("stage_assessment", "")
        history_text = self._build_history_text(narrative_history)
        recent_dialogue_text = self._build_recent_dialogue_text(history)
        dialogue_memory = self._build_dialogue_memory(history, player_input, npc_action_guide)
        compact_location = self._compact_location_context(location_context)
        compact_object = self._compact_object_context(object_context)
        compact_npc = self._compact_npc_context(npc_context, npc_action_guide)
        compact_check = self._compact_check_result(rule_result)
        has_current_scene_npc = bool(compact_npc)

        player_block = (
            "Player turn input:\n"
            f"- raw_input: {self._trim_text(player_input, 120)}\n"
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
            f"- npc_action_guide: {json.dumps(npc_action_guide, ensure_ascii=False) if npc_action_guide else 'none'}\n"
            f"- dialogue_memory: {json.dumps(dialogue_memory, ensure_ascii=False)}\n"
            f"- atmosphere_guide: {json.dumps(atmosphere_guide, ensure_ascii=False)}"
        )

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
            "- If the player already answered a question in the recent dialogue transcript, continue from that answer instead of asking the exact same question again.\n"
            "- If the latest player_input is an answer to the NPC's previous question, acknowledge that answer and ask a different follow-up or provide a new reaction.\n"
            "- If npc_action_guide exists, use it to decide how the NPC replies this turn.\n"
            "- When the player is clearly talking to an NPC, move the dialogue forward instead of restating the same atmosphere.\n"
        )
        if rhythm_result.get("arrival_mode"):
            prompt += (
                "- This turn is a pure arrival into the scene. The player moved here but did not speak.\n"
                "- If an NPC is present, write their reaction to the player's arrival, not a reply to words the player never said.\n"
                "- Do not imply the player introduced themselves, explained anything, or asked a question on this turn.\n"
            )
        if not has_current_scene_npc:
            prompt += (
                "- npc_context is none for this turn. There is no interactable NPC in the player's current location.\n"
                "- Do not continue a conversation from another room as if the NPC can still immediately hear and answer.\n"
                "- Do not generate new NPC dialogue, quoted replies, or remote call-and-response unless the current scene data explicitly contains that NPC.\n"
                "- Treat recent dialogue as background memory only; the active response must come from the current room and current action.\n"
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
        compact_payload = {
            "player_input": self._trim_text(player_input, 120),
            "action": normalized_action,
            "feasible": rhythm_result.get("feasible", True),
            "hint": rhythm_result.get("hint"),
            "check": self._compact_check_result(rule_result),
            "location": self._compact_location_context(rhythm_result.get("location_context", {})),
            "object": self._compact_object_context(rhythm_result.get("object_context")),
            "npc": self._compact_npc_context(
                rhythm_result.get("npc_context", {}),
                rhythm_result.get("npc_action_guide", {}),
            ),
            "npc_action_guide": rhythm_result.get("npc_action_guide", {}),
            "recent_dialogue": self._build_recent_dialogue_messages(history),
            "dialogue_memory": self._build_dialogue_memory(
                history,
                player_input,
                rhythm_result.get("npc_action_guide", {}),
            ),
            "current_scene_has_npc": bool(rhythm_result.get("npc_context")),
            "arrival_mode": bool(rhythm_result.get("arrival_mode")),
        }
        prompt = (
            "You are the narrative layer for a TRPG session.\n"
            "Use the structured data below to write one short in-world response and one short summary.\n"
            "Reply as JSON only: {\"narrative\":\"...\",\"summary\":\"...\"}\n"
            "Keep the response concise and directly answer the player's latest words.\n"
            "Treat recent_dialogue as the authoritative short-term memory.\n"
            "Treat dialogue_memory as already established facts.\n"
            "Do not ask the exact same question again if the player already answered it in recent_dialogue.\n\n"
            f"{json.dumps(compact_payload, ensure_ascii=False, indent=2)}"
        )
        if not compact_payload["current_scene_has_npc"]:
            prompt += (
                "\n\nNo NPC is present in the player's current location for this turn.\n"
                "Do not continue dialogue from another room.\n"
                "Do not write quoted NPC speech or imply that an off-screen NPC immediately answers.\n"
                "Focus on the player's current room, current action, and immediate sensory feedback."
            )
        if compact_payload["arrival_mode"]:
            prompt += (
                "\n\nThis turn is scene arrival only.\n"
                "The player moved into the room and did not say anything.\n"
                "If an NPC reacts, make it a reaction to presence or footsteps, not a reply to dialogue."
            )
        return prompt

    def _normalize_rhythm_result(self, rhythm_result: dict) -> dict:
        if not isinstance(rhythm_result, dict):
            return {}

        normalized = dict(rhythm_result)
        if not isinstance(normalized.get("location_context"), dict):
            normalized["location_context"] = {}
        if normalized.get("object_context") is not None and not isinstance(normalized.get("object_context"), dict):
            normalized["object_context"] = None
        if not isinstance(normalized.get("npc_context"), dict):
            normalized["npc_context"] = {}
        if not isinstance(normalized.get("npc_action_guide"), dict):
            normalized["npc_action_guide"] = {}
        if not isinstance(normalized.get("atmosphere_guide"), dict):
            normalized["atmosphere_guide"] = {}
        return normalized

    def _build_default_summary(self, player_input: str) -> str:
        text = str(player_input or "").strip()
        if not text:
            return "玩家执行了一个动作"
        if len(text) <= 30:
            return f"玩家：{text}"
        return f"玩家：{text[:27]}..."

    def _build_recent_dialogue_messages(self, history: list, limit: int = 6) -> list:
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
            })

        if not items:
            return []
        return items[-limit:]

    def _build_recent_dialogue_text(self, history: list, limit: int = 6) -> str:
        recent_messages = self._build_recent_dialogue_messages(history, limit=limit)
        if not recent_messages:
            return "暂无最近对话。"

        lines = []
        for message in recent_messages:
            role = "玩家" if message["role"] == "user" else "旁白/NPC"
            lines.append(f"- {role}: {message['content']}")
        return "\n".join(lines)

    def _build_dialogue_memory(self, history: list, player_input: str, npc_action_guide: dict) -> dict:
        recent_messages = self._build_recent_dialogue_messages(history, limit=8)
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

    def _get_default_narrative(self, player_input: str, rule_result: dict, rhythm_result: dict):
        hint = (rhythm_result or {}).get("hint")
        if hint and rule_result and rule_result.get("success") is False:
            narrative = f"你试着这么做，但现在还行不通：{hint}"
        elif rule_result and rule_result.get("success"):
            narrative = "你的行动有了进展。"
        else:
            narrative = "你的行动没有得到预期结果。"

        return {
            "narrative": narrative,
            "summary": self._build_default_summary(player_input),
        }

    def _build_local_fallback_narrative(
        self,
        player_input: str,
        rule_plan: dict,
        rule_result: dict,
        rhythm_result: dict,
    ) -> dict:
        rule_plan = rule_plan if isinstance(rule_plan, dict) else {}
        rhythm_result = rhythm_result if isinstance(rhythm_result, dict) else {}
        normalized_action = rule_plan.get("normalized_action", {}) if isinstance(rule_plan.get("normalized_action"), dict) else {}
        npc_guide = rhythm_result.get("npc_action_guide", {}) if isinstance(rhythm_result.get("npc_action_guide"), dict) else {}
        npc_context = rhythm_result.get("npc_context", {}) if isinstance(rhythm_result.get("npc_context"), dict) else {}
        object_context = rhythm_result.get("object_context") if isinstance(rhythm_result.get("object_context"), dict) else None
        location_context = rhythm_result.get("location_context", {}) if isinstance(rhythm_result.get("location_context"), dict) else {}
        feasible = bool(rhythm_result.get("feasible", True))
        hint = str(rhythm_result.get("hint") or "").strip()

        if not feasible and hint:
            return {
                "narrative": f"你试着这么做，但眼下行不通。{hint}",
                "summary": self._build_default_summary(player_input),
            }

        if self._is_arrival_mode(normalized_action, rhythm_result) and npc_context:
            return self._build_local_npc_arrival_narrative(npc_guide, npc_context, location_context)

        if self._is_talking_to_npc(player_input, normalized_action, npc_guide, npc_context):
            return self._build_local_npc_reply(player_input, npc_guide, npc_context)

        if object_context:
            success_result = str(object_context.get("success_result") or "").strip()
            failure_result = str(object_context.get("failure_result") or "").strip()
            if rule_result and rule_result.get("success") and success_result:
                return {
                    "narrative": success_result,
                    "summary": self._build_default_summary(player_input),
                }
            if rule_result and rule_result.get("success") is False and failure_result:
                return {
                    "narrative": failure_result,
                    "summary": self._build_default_summary(player_input),
                }

        location_name = location_context.get("name") or "当前地点"
        description = self._trim_text(location_context.get("description", ""), 90)
        if description:
            narrative = f"你留在{location_name}，周围的一切并没有立刻给出更多回应。{description}"
        elif rule_result and rule_result.get("success"):
            narrative = "你的行动推动了局面，但眼下还需要继续观察。"
        else:
            narrative = "短暂的沉默之后，局面没有出现更明显的变化。"

        return {
            "narrative": narrative,
            "summary": self._build_default_summary(player_input),
        }

    def _is_arrival_mode(self, normalized_action: dict, rhythm_result: dict) -> bool:
        if isinstance(rhythm_result, dict) and rhythm_result.get("arrival_mode"):
            return True
        if isinstance(normalized_action, dict) and normalized_action.get("verb") == "move":
            return True
        return False

    def _is_talking_to_npc(
        self,
        player_input: str,
        normalized_action: dict,
        npc_guide: dict,
        npc_context: dict,
    ) -> bool:
        if not isinstance(npc_context, dict) or not npc_context:
            return False
        if self._is_arrival_mode(normalized_action, {}):
            return False
        if isinstance(normalized_action, dict) and normalized_action.get("target_kind") == "npc":
            return True
        focus_npc = npc_guide.get("focus_npc") if isinstance(npc_guide, dict) else None
        if focus_npc and focus_npc in npc_context:
            return True
        text = str(player_input or "").strip()
        if not text:
            return False
        lowered = text.lower()
        speech_markers = ["“", "\"", "：", ":", "你好", "我是", "请问", "谁", "hello", "hi", "i am", "i'm"]
        return any(marker in text for marker in speech_markers[:8]) or any(marker in lowered for marker in speech_markers[8:])

    def _build_local_npc_reply(self, player_input: str, npc_guide: dict, npc_context: dict) -> dict:
        focus_npc = npc_guide.get("focus_npc") if isinstance(npc_guide, dict) else None
        npc_data = npc_context.get(focus_npc, {}) if focus_npc in npc_context else {}
        npc_name = focus_npc or "门后的女人"
        attitude = str(npc_guide.get("attitude") or npc_data.get("initial_attitude") or "警惕").strip()
        response_strategy = str(npc_guide.get("response_strategy") or "").strip()
        next_line_goal = str(npc_guide.get("next_line_goal") or "").strip()
        should_open_door = bool(npc_guide.get("should_open_door"))
        revealable_info = npc_guide.get("revealable_info", [])
        revealable_info = revealable_info if isinstance(revealable_info, list) else []
        key_info = npc_data.get("key_info", {}) if isinstance(npc_data.get("key_info"), dict) else {}
        runtime_state = npc_data.get("runtime_state", {}) if isinstance(npc_data.get("runtime_state"), dict) else {}
        npc_memory = runtime_state.get("memory", {}) if isinstance(runtime_state.get("memory"), dict) else {}
        conversation_flags = npc_memory.get("conversation_flags", {}) if isinstance(npc_memory.get("conversation_flags"), dict) else {}
        player_facts = npc_memory.get("player_facts", {}) if isinstance(npc_memory.get("player_facts"), dict) else {}
        evidence_seen = npc_memory.get("evidence_seen", []) if isinstance(npc_memory.get("evidence_seen"), list) else []
        promises = npc_memory.get("promises", []) if isinstance(npc_memory.get("promises"), list) else []
        topics_discussed = npc_memory.get("topics_discussed", []) if isinstance(npc_memory.get("topics_discussed"), list) else []
        trust_level = float(runtime_state.get("trust_level", 0.0) or 0.0)
        has_prior_conversation = bool(
            player_facts
            or evidence_seen
            or promises
            or topics_discussed
            or any(conversation_flags.values())
            or trust_level > 0
        )

        preface = "门后安静了一瞬，像是在判断你这句话值不值得相信。"
        if should_open_door:
            door_line = "随后门闩轻轻一响，门却只松开一道极窄的缝。"
        else:
            door_line = "门没有开，只是那道贴在门后的呼吸声变得更清楚了一些。"

        if revealable_info:
            info_key = next((item for item in revealable_info if item in key_info), None)
        else:
            info_key = None

        if info_key:
            clue_text = self._trim_text(str(key_info.get(info_key) or ""), 80)
            spoken_line = f"“先听着，{clue_text}”"
        elif "name" in next_line_goal.lower() and not conversation_flags.get("knows_player_name"):
            spoken_line = "“名字。”"
        elif ("got here" in next_line_goal.lower() or "origin" in next_line_goal.lower()) and not conversation_flags.get("knows_player_origin_claim"):
            spoken_line = "“你到底是怎么到这里来的？”"
        elif ("evidence" in next_line_goal.lower() or "proof" in next_line_goal.lower()) and not conversation_flags.get("evidence_presented"):
            spoken_line = "“光靠嘴说不够。你有证据吗？”"
        elif "trust" in next_line_goal.lower() or "verify" in next_line_goal.lower():
            spoken_line = "“这种话谁都能编。你是谁，为什么会在这里？”"
        elif "cooperation" in next_line_goal.lower() or "合作" in next_line_goal:
            spoken_line = "“如果你不是来找死的，就把你知道的先说清楚，我们再谈怎么合作。”"
        elif "reveal" in next_line_goal.lower():
            spoken_line = "“我可以告诉你一点事，但你最好先证明自己不是麻烦。”"
        else:
            spoken_line = "“我听见了。继续说。”"

        tone = ""
        trimmed_strategy = self._trim_text(self._strip_trailing_punctuation(response_strategy), 24)
        if response_strategy:
            if has_prior_conversation:
                tone = f"{npc_name}的语气依旧{attitude}，明显带着{trimmed_strategy}。"
            else:
                tone = f"{npc_name}的语气{attitude}，明显带着{trimmed_strategy}。"
        elif attitude:
            if has_prior_conversation:
                tone = f"{npc_name}的语气依旧{attitude}。"
            else:
                tone = f"{npc_name}的语气{attitude}。"

        narrative_parts = [preface]
        if tone:
            narrative_parts.append(tone)
        narrative_parts.append(spoken_line)
        narrative_parts.append(door_line)
        narrative = "".join(narrative_parts)

        summary = f"与{npc_name}对话"
        if str(player_input or "").strip():
            summary = self._build_default_summary(player_input)
        return {
            "narrative": narrative,
            "summary": summary,
        }

    def _build_local_npc_arrival_narrative(self, npc_guide: dict, npc_context: dict, location_context: dict) -> dict:
        focus_npc = npc_guide.get("focus_npc") if isinstance(npc_guide, dict) else None
        if focus_npc and focus_npc in npc_context:
            npc_name = focus_npc
            npc_data = npc_context.get(focus_npc, {})
        else:
            npc_name = next(iter(npc_context))
            npc_data = npc_context.get(npc_name, {})

        location_name = location_context.get("name") or "当前地点"
        base_description = str(
            location_context.get("description")
            or location_context.get("runtime_description")
            or ""
        ).strip()
        arrival_hint = str(
            npc_data.get("first_appearance")
            or location_context.get("active_npc_present_description")
            or npc_data.get("current_state")
            or ""
        ).strip()
        attitude = str(npc_guide.get("attitude") or npc_data.get("initial_attitude") or "").strip()

        parts = [f"你来到了{location_name}。"]
        if base_description:
            parts.append(base_description)
        if arrival_hint:
            parts.append(arrival_hint)
        elif attitude:
            parts.append(f"{npc_name}显然已经察觉到你的到来，态度{attitude}。")

        narrative = "\n\n".join(part for part in parts if part)
        return {
            "narrative": narrative,
            "summary": f"移动到{location_name}",
        }

    def _trim_text(self, value: str, limit: int) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)] + "..."

    def _strip_trailing_punctuation(self, text: str) -> str:
        return str(text or "").rstrip("。！？!?；;，,、 ")

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

    def _compact_npc_context(self, npc_context: dict, npc_action_guide: dict) -> dict:
        if not isinstance(npc_context, dict) or not npc_context:
            return {}
        focus_npc = None
        if isinstance(npc_action_guide, dict):
            focus_npc = npc_action_guide.get("focus_npc")
        if focus_npc and focus_npc in npc_context:
            source = {focus_npc: npc_context[focus_npc]}
        else:
            first_key = next(iter(npc_context))
            source = {first_key: npc_context[first_key]}

        compact = {}
        for name, data in source.items():
            runtime_state = data.get("runtime_state", {}) if isinstance(data, dict) else {}
            npc_memory = runtime_state.get("memory", {}) if isinstance(runtime_state.get("memory"), dict) else {}
            compact[name] = {
                "name": data.get("name", name) if isinstance(data, dict) else name,
                "appearance": self._trim_text(data.get("appearance", ""), 80) if isinstance(data, dict) else "",
                "current_state": self._trim_text(data.get("current_state", ""), 100) if isinstance(data, dict) else "",
                "first_appearance": self._trim_text(data.get("first_appearance", ""), 100) if isinstance(data, dict) else "",
                "attitude": runtime_state.get("attitude"),
                "trust_level": runtime_state.get("trust_level"),
                "memory": self._compact_npc_memory(npc_memory),
            }
        return compact

    def _compact_npc_memory(self, npc_memory: dict) -> dict:
        if not isinstance(npc_memory, dict):
            return {}

        player_facts = npc_memory.get("player_facts", {}) if isinstance(npc_memory.get("player_facts"), dict) else {}
        compact_facts = {}
        for key, value in player_facts.items():
            if not isinstance(value, dict):
                continue
            fact_value = str(value.get("value") or "").strip()
            if fact_value:
                compact_facts[key] = self._trim_text(fact_value, 80)

        conversation_flags = npc_memory.get("conversation_flags", {}) if isinstance(npc_memory.get("conversation_flags"), dict) else {}
        pending_questions = npc_memory.get("pending_questions", []) if isinstance(npc_memory.get("pending_questions"), list) else []
        topics_discussed = npc_memory.get("topics_discussed", []) if isinstance(npc_memory.get("topics_discussed"), list) else []

        compact = {
            "player_facts": compact_facts,
            "conversation_flags": conversation_flags,
            "pending_questions": pending_questions[:3],
            "topics_discussed": topics_discussed[:5],
        }
        return {key: value for key, value in compact.items() if value}

    def _compact_check_result(self, rule_result: dict) -> dict:
        rule_result = rule_result if isinstance(rule_result, dict) else {}
        return {
            "check_type": rule_result.get("check_type"),
            "skill": rule_result.get("skill"),
            "difficulty": rule_result.get("difficulty"),
            "success": rule_result.get("success"),
            "result_description": rule_result.get("result_description"),
        }
