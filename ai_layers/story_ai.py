"""StoryAI: merged rhythm + narrative layer.

Combines pacing judgment and narrative generation into a single LLM call.
Composes RhythmAI and NarrativeAI instances, reusing their context-building
and normalization methods.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from astrbot.api import logger
from astrbot.api.star import Context

from ..game_state.character_card import build_identity_block
from .provider_failover import (
    ProviderFailoverError,
    normalize_provider_candidates,
    text_chat_with_fallback,
)
from .rhythm_ai import RhythmAI
from .narrative_ai import NarrativeAI


class StoryAI:
    """Merged pacing + narrative layer for dual-mode pipeline."""

    def __init__(
        self,
        context: Context,
        provider_name: str = None,
        config: dict = None,
        fallback_provider_names: list[str] | None = None,
        rhythm_ai: RhythmAI = None,
        narrative_ai: NarrativeAI = None,
    ):
        self.context = context
        self.provider_name = provider_name
        self.fallback_provider_names = list(fallback_provider_names or [])
        self.config = config or {}
        self.rhythm_ai = rhythm_ai
        self.narrative_ai = narrative_ai
        self.prompts = self._load_prompts()
        self._call_metrics: dict[str, dict] = {}

    def pop_call_metric(self, trace_id: str) -> dict:
        if not trace_id:
            return {}
        return self._call_metrics.pop(trace_id, {})

    def _load_prompts(self):
        prompts_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ai_prompts.json")
        try:
            with open(prompts_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"[StoryAI] Prompt config error: {e}")
            return {}

    def _get_provider_candidates(self) -> list[str]:
        candidates = normalize_provider_candidates(
            self.provider_name,
            self.fallback_provider_names,
        )
        if not candidates:
            logger.error("[StoryAI] No provider configured")
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
        """Run merged rhythm + narrative in a single LLM call.

        Returns:
            {
                "rhythm_result": { ... },      # structured pacing data for state updates
                "narrative_result": { "narrative": "...", "summary": "..." },
            }
        """
        if history is None:
            history = []

        provider_candidates = self._get_provider_candidates()
        if not provider_candidates:
            raise RuntimeError("剧情AI处理失败：未找到可用 LLM provider，请使用重试按钮。")

        # Build the merged prompt
        prompt = self._build_prompt(
            player_input=player_input,
            rule_plan=rule_plan,
            rule_result=rule_result,
            game_state=game_state,
            module_data=module_data,
            history=history,
        )
        if not prompt:
            raise RuntimeError("剧情AI处理失败：未找到可用提示词，请使用重试按钮。")

        try:
            logger.info(
                "[StoryAI] sending merged request: primary_provider=%s prompt_len=%s",
                self.provider_name,
                len(prompt),
            )
            outcome = await text_chat_with_fallback(
                context=self.context,
                primary_provider_id=self.provider_name,
                fallback_provider_ids=self.fallback_provider_names,
                prompt=prompt,
                contexts=[],
                trace_label="StoryAI.process",
                custom_api=custom_api,
            )
            llm_response = outcome.response
            response_text = (
                llm_response.completion_text
                if hasattr(llm_response, "completion_text")
                else str(llm_response)
            )
            usage_metrics = outcome.metrics
            if trace_id:
                self._call_metrics[trace_id] = usage_metrics

            result = json.loads(self._strip_json_fence(response_text))

        except ProviderFailoverError as e:
            if trace_id:
                self._call_metrics[trace_id] = e.metrics
            logger.error("[StoryAI] provider chain failed: %s", e)
            raise RuntimeError("剧情AI处理失败：所有候选模型都不可用，请检查主模型与备用模型配置。") from e
        except json.JSONDecodeError as e:
            logger.warning("[StoryAI] JSON decode failed")
            raise RuntimeError("剧情AI处理失败：返回结果不是合法 JSON，请使用重试按钮。") from e
        except Exception as e:
            logger.error(f"[StoryAI] process error: {e}")
            raise RuntimeError("剧情AI处理失败，请使用重试按钮。") from e

        return self._split_result(result, player_input, rule_plan, game_state, module_data)

    def _split_result(
        self,
        raw: dict,
        player_input: str,
        rule_plan: dict,
        game_state: dict,
        module_data: dict,
    ) -> dict:
        """Split combined LLM output into rhythm_result + narrative_result."""
        if not isinstance(raw, dict):
            raw = {}

        # --- Extract and normalize narrative ---
        narrative_text = str(raw.get("narrative") or "").strip()
        narrative_text = re.sub(r"<br\s*/?>", "\n", narrative_text, flags=re.IGNORECASE)
        if not narrative_text:
            raise RuntimeError("剧情AI生成失败：返回叙述内容为空，请使用重试按钮。")

        summary = str(raw.get("summary") or "").strip()
        if not summary:
            summary = self.narrative_ai._build_default_summary(player_input) if self.narrative_ai else "玩家执行了一个动作"

        narrative_result = {
            "narrative": narrative_text,
            "summary": summary,
        }

        # --- Extract and normalize rhythm fields ---
        base_result = self.rhythm_ai._build_base_result(player_input, rule_plan, game_state, module_data)
        rhythm_result = self.rhythm_ai._normalize_result(raw, base_result)

        return {
            "rhythm_result": rhythm_result,
            "narrative_result": narrative_result,
        }

    def _build_prompt(
        self,
        player_input: str,
        rule_plan: dict,
        rule_result: dict,
        game_state: dict,
        module_data: dict,
        history: list,
    ) -> str:
        # --- Get the merged prompt template ---
        prompt_template = self.config.get("story_ai_prompt", "").strip()
        if not prompt_template:
            prompt_template = self.prompts.get("story_ai_prompt", "")
        if not prompt_template:
            logger.error("[StoryAI] story_ai_prompt not found")
            return ""

        # --- Build rhythm context (from RhythmAI methods) ---
        current_location = game_state.get("current_location", "master_bedroom")
        round_count = game_state.get("round_count", 0)
        clues_found = game_state.get("world_state", {}).get("clues_found", [])
        stages = module_data.get("module_info", {}).get("stages", "")
        history_summaries = self.rhythm_ai._build_history_summaries(game_state)
        scene_context = self.rhythm_ai._build_scene_context(game_state, module_data, rule_plan, player_input=player_input)

        # --- Build narrative context (from NarrativeAI methods) ---
        narrative_history = game_state.get("narrative_history", [])
        history_text = self.narrative_ai._build_history_text(narrative_history)
        recent_dialogue_text = self.narrative_ai._build_recent_dialogue_text(history)

        rhythm_result_preview = self.rhythm_ai._build_base_result(player_input, rule_plan, game_state, module_data)
        raw_npc_context = rhythm_result_preview.get("npc_context", {})
        raw_npc_guide = rhythm_result_preview.get("npc_action_guide", {})
        dialogue_npcs, _, npc_action_guide = self.narrative_ai._sanitize_npc_prompt_inputs(raw_npc_context, raw_npc_guide)
        dialogue_memory = self.narrative_ai._build_dialogue_memory(history, player_input, npc_action_guide)
        npc_dialogue_contract = self.narrative_ai._build_npc_dialogue_contract(dialogue_npcs, npc_action_guide)

        # --- Fill template placeholders ---
        prompt = prompt_template
        prompt = prompt.replace("{player_identity_block}", build_identity_block(game_state.get("character_card")))
        prompt = prompt.replace("{current_location}", current_location)
        prompt = prompt.replace("{round_count}", str(round_count))
        prompt = prompt.replace("{clues_found}", json.dumps(clues_found, ensure_ascii=False))
        prompt = prompt.replace("{stages}", stages)
        prompt = prompt.replace("{history_summaries}", history_summaries)
        prompt = prompt.replace("{player_input}", player_input)
        prompt = prompt.replace("{rule_plan}", json.dumps(rule_plan or {}, ensure_ascii=False, indent=2))
        prompt = prompt.replace("{rule_result}", json.dumps(rule_result or {}, ensure_ascii=False, indent=2))
        prompt = prompt.replace("{scene_context}", scene_context)
        prompt = prompt.replace("{recent_dialogue}", recent_dialogue_text)
        prompt = prompt.replace("{history_text}", history_text)
        prompt = prompt.replace("{dialogue_memory}", json.dumps(dialogue_memory, ensure_ascii=False))
        prompt = prompt.replace("{npc_dialogue_contract}", json.dumps(npc_dialogue_contract, ensure_ascii=False) if npc_dialogue_contract else "none")

        # --- Append rhythm additional tasks (NPC guide schema, memory updates, ending request) ---
        prompt += self._build_rhythm_additional_tasks(rule_plan)

        # --- Append narrative constraints ---
        prompt += self._build_narrative_constraints(rhythm_result_preview, dialogue_npcs, history)

        return prompt

    def _build_rhythm_additional_tasks(self, rule_plan: dict) -> str:
        """Append the NPC guide, memory update, and ending request schemas (from RhythmAI prompt)."""
        parts = [
            "\n\n# NPC行动指导任务",
            "如果当前场景有可交互NPC，输出 npc_action_guide。",
            "npc_action_guide 是给叙述部分的硬合约——你自己写叙述时必须遵守。",
            "",
            "# npc_action_guide schema",
            "{",
            '  "focus_npc": "npc name or null",',
            '  "attitude": "current attitude toward the player",',
            '  "dialogue_act": "acknowledge/probe/reveal/warn/propose_plan/confirm_help/refuse/listen",',
            '  "response_strategy": "how the NPC should respond this turn",',
            '  "next_line_goal": "what the NPC wants to confirm or push this turn",',
            '  "voice_style": "brief note on tone and phrasing",',
            '  "must_acknowledge": ["what in the latest player input must be directly reacted to"],',
            '  "knowledge_boundary": "what the NPC must not invent or overstate",',
            '  "should_open_door": false',
            "}",
            "",
            "# NPC记忆更新任务",
            "当场景中有可交互NPC且玩家本轮涉及NPC互动时，输出 npc_memory_updates。",
            "仅基于本轮玩家实际说了什么/做了什么来提取事实，不要推测。",
            "trust_change_reasons 中的key必须来自NPC数据中的 available_trust_reasons 列表。",
            "信任匹配原则：宽松匹配。只要语义接近就应触发，一轮中多个reason同时触发是正常的。",
            "",
            "# 结局请求",
            "如果你判断当前状态已满足某个结局的进入时机，输出 ending_request.requested=true 并指定 ending_id。",
            "这只是请求，系统会再次校验。如果条件不够，必须输出 requested=false。",
        ]

        input_classification = (rule_plan or {}).get("input_classification", "action")
        if input_classification == "dialogue":
            parts.extend([
                "",
                "# 输入分类：对话",
                "系统检测到玩家输入包含引号，判定为【对话】。引号内是玩家角色的台词，不是实际行动。",
            ])

        # 玩家视角 NPC 称谓任务（与 rhythm_ai 共用同一份说明）
        parts.append(self.rhythm_ai.build_player_visible_npcs_task_block())

        return "\n".join(parts)

    def _build_narrative_constraints(self, rhythm_preview: dict, dialogue_npcs: dict, history: list) -> str:
        """Append narrative writing constraints (from NarrativeAI prompt)."""
        parts = [
            "\n\n# 叙述写作约束",
            "- 使用第二人称（\"你\"）。",
            "- 低氛围场景50-100字，高氛围场景100-200字。",
            "- 直接回应玩家当前的话，不要只重复房间描述。",
            "- 把 recent_dialogue 作为短期记忆权威来源。",
            "- 把 dialogue_memory 作为既定事实。",
            "- 当 npc_dialogue_contract 存在时，它是NPC意图和语气的最高优先级权威。",
            "- 不要重复已回答的问题。",
            "- 不要发明NPC秘密或超出 knowledge_boundary 的确定性内容。",
            "- 不要使用Markdown格式。允许的内联HTML标签：<b> <strong> <i> <em> <s> <del>",
            "- 禁止使用 <br>。换行用 \\n，分段用 \\n\\n。",
        ]

        has_npc = bool(dialogue_npcs)
        if not has_npc:
            parts.extend([
                "- 当前场景没有可对话NPC。不要继续另一个房间的对话，不要生成NPC引语。",
            ])

        location_context = rhythm_preview.get("location_context", {})
        threat_entities = list(location_context.get("present_threats", []) or [])
        if threat_entities:
            parts.extend([
                f"- 威胁实体在场: {', '.join(threat_entities)}。它们不会说话，只描述动作/姿态/距离/压迫感。",
            ])

        return "\n".join(parts)
