import unittest
import sys
import types
from pathlib import Path


astrbot_module = types.ModuleType("astrbot")
astrbot_api_module = types.ModuleType("astrbot.api")
astrbot_star_module = types.ModuleType("astrbot.api.star")


class _DummyLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _DummyContext:
    pass


astrbot_api_module.logger = _DummyLogger()
astrbot_api_module.star = astrbot_star_module
astrbot_star_module.Context = _DummyContext
astrbot_module.api = astrbot_api_module

sys.modules.setdefault("astrbot", astrbot_module)
sys.modules.setdefault("astrbot.api", astrbot_api_module)
sys.modules.setdefault("astrbot.api.star", astrbot_star_module)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aitrpg.ai_layers.narrative_ai import NarrativeAI
from aitrpg.ai_layers.rhythm_ai import RhythmAI
from aitrpg.ai_layers.rule_ai import RuleAI
from aitrpg.game_state.session_manager import SessionManager


class _StubResponse:
    def __init__(self, completion_text: str):
        self.completion_text = completion_text


class _StubProvider:
    def __init__(self, completion_text: str):
        self.completion_text = completion_text
        self.provider_config = {"id": "stub", "model": "stub-model"}

    async def text_chat(self, prompt: str, contexts: list):
        return _StubResponse(self.completion_text)


class _StubContext:
    def __init__(self, provider):
        self.provider = provider

    def get_provider(self, provider_name: str):
        return self.provider

    def get_using_provider(self):
        return self.provider


class LlmFailureRetryOnlyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = SessionManager("default_module")
        self.session_id = "llm-failure-retry-only"
        self.manager.create_session(self.session_id, "default_module")
        self.state = self.manager.get_session(self.session_id)
        self.module_data = self.manager.get_module_data(self.session_id)

    async def test_rule_ai_parse_intent_invalid_json_raises(self):
        rule_ai = RuleAI(
            _StubContext(_StubProvider("not-json")),
            config={"rule_ai_intent_prompt": "{player_input}"},
        )

        with self.assertRaisesRegex(RuntimeError, "规则AI意图解析失败"):
            await rule_ai.parse_intent("查看房间")

    async def test_rule_ai_adjudicate_action_invalid_json_raises(self):
        rule_ai = RuleAI(
            _StubContext(_StubProvider("not-json")),
            config={"rule_ai_action_prompt": "{player_input} {intent} {current_location}"},
        )

        with self.assertRaisesRegex(RuntimeError, "规则AI动作裁定失败"):
            await rule_ai.adjudicate_action(
                player_input="查看房间",
                intent={"intent": "inspect", "target": None, "category": "观察"},
                game_state=self.state,
                module_data=self.module_data,
            )

    async def test_rhythm_ai_invalid_json_raises(self):
        rhythm_ai = RhythmAI(
            _StubContext(_StubProvider("not-json")),
            config={"rhythm_ai_prompt": "{player_input} {intent} {rule_plan} {rule_result} {scene_context}"},
        )

        with self.assertRaisesRegex(RuntimeError, "节奏AI处理失败"):
            await rhythm_ai.process(
                intent={"intent": "inspect"},
                player_input="查看房间",
                rule_plan={"feasibility": {"ok": True}},
                rule_result={"success": True},
                game_state=self.state,
                module_data=self.module_data,
                history=[],
            )

    async def test_narrative_ai_invalid_json_raises_instead_of_echoing_raw_text(self):
        narrative_ai = NarrativeAI(
            _StubContext(_StubProvider("这是旧兜底路径最容易直接回显给玩家的原文")),
            config={"narrative_ai_prompt": "{rule_info}\n{rhythm_info}\n{location}"},
        )

        with self.assertRaisesRegex(RuntimeError, "文案AI生成失败"):
            await narrative_ai.generate(
                player_input="查看房间",
                rule_plan={
                    "normalized_action": {"verb": "inspect", "target_kind": "location", "target_key": "master_bedroom"},
                    "feasibility": {"ok": True},
                    "location_context": self.manager.get_location_context(self.session_id),
                    "input_classification": "action",
                },
                rule_result={"check_type": None, "success": True, "result_description": "无需检定"},
                rhythm_result={
                    "feasible": True,
                    "stage_assessment": "测试",
                    "location_context": self.manager.get_location_context(self.session_id),
                    "object_context": None,
                    "npc_context": {},
                    "threat_entity_context": {},
                    "npc_action_guide": {},
                    "atmosphere_guide": {},
                },
                narrative_history=[],
                history=[],
            )


if __name__ == "__main__":
    unittest.main()
