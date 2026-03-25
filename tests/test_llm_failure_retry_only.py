import sys
import types
import unittest
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
from aitrpg.ai_layers.usage_metrics import extract_usage_metrics
from aitrpg.game_state.session_manager import SessionManager


class _StubRawCompletion:
    def __init__(self, model: str | None = None):
        self.model = model


class _StubResponse:
    def __init__(self, completion_text: str, raw_model: str | None = None):
        self.completion_text = completion_text
        self.raw_completion = _StubRawCompletion(raw_model) if raw_model else None


class _StubProvider:
    def __init__(
        self,
        completion_text: str,
        provider_id: str = "stub",
        configured_model: str = "stub-model",
        actual_model: str | None = None,
    ):
        self.completion_text = completion_text
        self.actual_model = actual_model
        self.provider_config = {"id": provider_id, "model": configured_model}

    def get_model(self):
        return self.provider_config.get("model")

    async def text_chat(self, prompt: str, contexts: list):
        return _StubResponse(self.completion_text, raw_model=self.actual_model)


class _StubContext:
    def __init__(self, provider):
        self.provider = provider

    def get_provider(self, provider_name: str):
        return self.provider

    def get_using_provider(self):
        return self.provider


class _ExplicitMissingProviderContext:
    def __init__(self, current_provider):
        self.current_provider = current_provider

    def get_provider(self, provider_name: str):
        return None

    def get_using_provider(self):
        return self.current_provider


class _ContextWithProviderByIdOnly:
    def __init__(self, provider):
        self.provider = provider

    def get_provider_by_id(self, provider_id: str):
        return self.provider if provider_id == self.provider.provider_config.get("id") else None


class _ContextWithAllProvidersOnly:
    def __init__(self, providers):
        self.providers = providers

    def get_all_providers(self):
        return list(self.providers)


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
            provider_name="stub",
            config={"rule_ai_intent_prompt": "{player_input}"},
        )

        with self.assertRaisesRegex(RuntimeError, "规则AI意图解析失败|瑙勫垯AI鎰忓浘瑙ｆ瀽澶辫触"):
            await rule_ai.parse_intent("查看房间")

    async def test_rule_ai_adjudicate_action_invalid_json_raises(self):
        rule_ai = RuleAI(
            _StubContext(_StubProvider("not-json")),
            provider_name="stub",
            config={"rule_ai_action_prompt": "{player_input} {intent} {current_location}"},
        )

        with self.assertRaisesRegex(RuntimeError, "规则AI动作裁定失败|瑙勫垯AI鍔ㄤ綔瑁佸畾澶辫触"):
            await rule_ai.adjudicate_action(
                player_input="查看房间",
                intent={"intent": "inspect", "target": None, "category": "观察"},
                game_state=self.state,
                module_data=self.module_data,
            )

    async def test_rhythm_ai_invalid_json_raises(self):
        rhythm_ai = RhythmAI(
            _StubContext(_StubProvider("not-json")),
            provider_name="stub",
            config={"rhythm_ai_prompt": "{player_input} {intent} {rule_plan} {rule_result} {scene_context}"},
        )

        with self.assertRaisesRegex(RuntimeError, "节奏AI处理失败|鑺傚AI澶勭悊澶辫触"):
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
            provider_name="stub",
            config={"narrative_ai_prompt": "{rule_info}\n{rhythm_info}\n{location}"},
        )

        with self.assertRaisesRegex(RuntimeError, "文案AI生成失败|鏂囨AI鐢熸垚澶辫触"):
            await narrative_ai.generate(
                player_input="查看房间",
                rule_plan={
                    "normalized_action": {
                        "verb": "inspect",
                        "target_kind": "location",
                        "target_key": "master_bedroom",
                    },
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

    async def test_rule_ai_missing_explicit_provider_does_not_fallback(self):
        fallback_provider = _StubProvider('{"intent":"inspect","target":null,"category":"观察"}')
        rule_ai = RuleAI(
            _ExplicitMissingProviderContext(fallback_provider),
            provider_name="missing-provider",
            config={"rule_ai_intent_prompt": "{player_input}"},
        )

        with self.assertRaisesRegex(RuntimeError, "Provider ID|LLM provider|候选模型"):
            await rule_ai.parse_intent("查看房间")

    async def test_rhythm_ai_missing_explicit_provider_does_not_fallback(self):
        fallback_provider = _StubProvider('{"feasible":true,"hint":"ok","stage_assessment":"ok"}')
        rhythm_ai = RhythmAI(
            _ExplicitMissingProviderContext(fallback_provider),
            provider_name="missing-provider",
            config={"rhythm_ai_prompt": "{player_input} {intent} {rule_plan} {rule_result} {scene_context}"},
        )

        with self.assertRaisesRegex(RuntimeError, "LLM provider|候选模型"):
            await rhythm_ai.process(
                intent={"intent": "inspect"},
                player_input="查看房间",
                rule_plan={"feasibility": {"ok": True}},
                rule_result={"success": True},
                game_state=self.state,
                module_data=self.module_data,
                history=[],
            )

    async def test_narrative_ai_missing_explicit_provider_does_not_fallback(self):
        fallback_provider = _StubProvider('{"narrative":"ok","summary":"ok"}')
        narrative_ai = NarrativeAI(
            _ExplicitMissingProviderContext(fallback_provider),
            provider_name="missing-provider",
            config={"narrative_ai_prompt": "{rule_info}\n{rhythm_info}\n{location}"},
        )

        with self.assertRaisesRegex(RuntimeError, "LLM provider|候选模型"):
            await narrative_ai.generate(
                player_input="查看房间",
                rule_plan={
                    "normalized_action": {
                        "verb": "inspect",
                        "target_kind": "location",
                        "target_key": "master_bedroom",
                    },
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

    async def test_missing_rule_provider_config_fails_instead_of_using_current_provider(self):
        rule_ai = RuleAI(
            _StubContext(_StubProvider('{"intent":"inspect","target":null,"category":"观察"}')),
            provider_name=None,
            config={"rule_ai_intent_prompt": "{player_input}"},
        )

        with self.assertRaisesRegex(RuntimeError, "Provider ID|LLM provider"):
            await rule_ai.parse_intent("查看房间")


    async def test_rhythm_ai_supports_context_get_provider_by_id(self):
        rhythm_ai = RhythmAI(
            _ContextWithProviderByIdOnly(
                _StubProvider(
                    '{"feasible":true,"stage_assessment":"ok","npc_action_guide":{},"atmosphere_guide":{}}',
                    provider_id="stub",
                )
            ),
            provider_name="stub",
            config={"rhythm_ai_prompt": "{player_input} {intent} {rule_plan} {rule_result} {scene_context}"},
        )

        result = await rhythm_ai.process(
            intent={"intent": "inspect"},
            player_input="inspect room",
            rule_plan={"feasibility": {"ok": True}},
            rule_result={"success": True},
            game_state=self.state,
            module_data=self.module_data,
            history=[],
        )

        self.assertEqual(result["stage_assessment"], "ok")

    async def test_rule_ai_parse_intent_supports_context_get_provider_by_id(self):
        rule_ai = RuleAI(
            _ContextWithProviderByIdOnly(
                _StubProvider(
                    '{"intent":"inspect","target":null,"category":"observe"}',
                    provider_id="stub",
                )
            ),
            provider_name="stub",
            config={"rule_ai_intent_prompt": "{player_input}"},
        )

        result = await rule_ai.parse_intent("inspect room")

        self.assertEqual(result["intent"], "inspect")

    async def test_narrative_ai_supports_context_get_all_providers(self):
        narrative_ai = NarrativeAI(
            _ContextWithAllProvidersOnly(
                [
                    _StubProvider(
                        '{"narrative":"You inspect the room.","summary":"inspect"}',
                        provider_id="stub",
                    )
                ]
            ),
            provider_name="stub",
            config={"narrative_ai_prompt": "{rule_info}\n{rhythm_info}\n{location}"},
        )

        result = await narrative_ai.generate(
            player_input="inspect room",
            rule_plan={
                "normalized_action": {
                    "verb": "inspect",
                    "target_kind": "location",
                    "target_key": "master_bedroom",
                },
                "feasibility": {"ok": True},
                "location_context": self.manager.get_location_context(self.session_id),
                "input_classification": "action",
            },
            rule_result={"check_type": None, "success": True, "result_description": "ok"},
            rhythm_result={
                "feasible": True,
                "stage_assessment": "test",
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

        self.assertEqual(result["summary"], "inspect")


class UsageMetricsTests(unittest.TestCase):
    def test_extract_usage_metrics_prefers_actual_response_model(self):
        provider = _StubProvider(
            "{}",
            provider_id="selected-provider",
            configured_model="configured-model",
            actual_model="actual-model",
        )
        response = _StubResponse("{}", raw_model="actual-model")

        metrics = extract_usage_metrics(response, "prompt", "{}", provider=provider)

        self.assertEqual(metrics["provider_id"], "selected-provider")
        self.assertEqual(metrics["configured_model"], "configured-model")
        self.assertEqual(metrics["actual_model"], "actual-model")
        self.assertEqual(metrics["model_display"], "selected-provider / actual-model")


if __name__ == "__main__":
    unittest.main()
