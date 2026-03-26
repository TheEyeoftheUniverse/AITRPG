import sys
import types
import unittest
from pathlib import Path


astrbot_module = types.ModuleType("astrbot")
astrbot_api_module = types.ModuleType("astrbot.api")
astrbot_star_module = types.ModuleType("astrbot.api.star")
astrbot_event_module = types.ModuleType("astrbot.api.event")
quart_module = types.ModuleType("quart")


class _DummyLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _DummyContext:
    pass


class _DummyStar:
    def __init__(self, context=None):
        self.context = context


class _DummyAstrMessageEvent:
    pass


class _DummyFilter:
    class EventMessageType:
        ALL = "all"

    @staticmethod
    def command(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

    @staticmethod
    def event_message_type(*args, **kwargs):
        def decorator(func):
            return func
        return decorator


def _dummy_register(*args, **kwargs):
    def decorator(obj):
        return obj
    return decorator


astrbot_api_module.logger = _DummyLogger()
astrbot_api_module.star = astrbot_star_module
astrbot_api_module.event = astrbot_event_module
astrbot_star_module.Context = _DummyContext
astrbot_star_module.Star = _DummyStar
astrbot_star_module.register = _dummy_register
astrbot_event_module.filter = _DummyFilter
astrbot_event_module.AstrMessageEvent = _DummyAstrMessageEvent
astrbot_module.api = astrbot_api_module

quart_module.Quart = object
quart_module.render_template = lambda *args, **kwargs: None
quart_module.request = types.SimpleNamespace(get_json=None)
quart_module.jsonify = lambda *args, **kwargs: None
quart_module.make_response = lambda *args, **kwargs: None

sys.modules.setdefault("astrbot", astrbot_module)
sys.modules.setdefault("astrbot.api", astrbot_api_module)
sys.modules.setdefault("astrbot.api.star", astrbot_star_module)
sys.modules.setdefault("astrbot.api.event", astrbot_event_module)
sys.modules.setdefault("quart", quart_module)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aitrpg.ai_layers.provider_failover import normalize_provider_candidates, text_chat_with_fallback
from aitrpg.main import AITRPGPlugin
from aitrpg.ai_layers.rule_ai import RuleAI
from aitrpg.ai_layers.usage_metrics import extract_usage_metrics
from aitrpg.game_state.session_manager import SessionManager


class _StubRawCompletion:
    def __init__(self, model=None):
        self.model = model


class _StubResponse:
    def __init__(self, completion_text, raw_model=None, role="assistant"):
        self.completion_text = completion_text
        self.raw_completion = _StubRawCompletion(raw_model) if raw_model else None
        self.role = role


class _StubProvider:
    def __init__(self, completion_text, provider_id="stub", configured_model="stub-model", actual_model=None, role="assistant"):
        self.completion_text = completion_text
        self.provider_config = {"id": provider_id, "model": configured_model}
        self.actual_model = actual_model
        self.role = role
        self.call_count = 0

    def get_model(self):
        return self.provider_config.get("model")

    async def text_chat(self, prompt, contexts):
        self.call_count += 1
        return _StubResponse(self.completion_text, raw_model=self.actual_model, role=self.role)


class _FailingProvider:
    def __init__(self, provider_id="broken", configured_model="broken-model"):
        self.provider_config = {"id": provider_id, "model": configured_model}
        self.call_count = 0

    def get_model(self):
        return self.provider_config.get("model")

    async def text_chat(self, prompt, contexts):
        self.call_count += 1
        raise ConnectionError("Connection error.")


class _Context:
    def __init__(self, providers):
        self.providers = providers

    def get_provider(self, provider_name):
        return self.providers.get(provider_name)

    def get_provider_by_id(self, provider_id):
        return self.providers.get(provider_id)

    def get_all_providers(self):
        return list(self.providers.values())


class ProviderFailoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_normalize_provider_candidates_dedupes_and_keeps_order(self):
        candidates = normalize_provider_candidates("primary", ["backup-a", "primary", "", "backup-b", "backup-a"])
        self.assertEqual(candidates, ["primary", "backup-a", "backup-b"])

    async def test_text_chat_with_fallback_uses_backup_on_connection_error(self):
        primary = _FailingProvider("primary")
        backup = _StubProvider(
            '{"intent":"inspect","target":null,"category":"observe"}',
            provider_id="backup",
            configured_model="backup-model",
            actual_model="backup-model",
        )
        outcome = await text_chat_with_fallback(
            context=_Context({"primary": primary, "backup": backup}),
            primary_provider_id="primary",
            fallback_provider_ids=["backup"],
            prompt="inspect room",
            contexts=[],
            trace_label="test",
        )

        self.assertEqual(outcome.metrics["provider_id"], "backup")
        self.assertTrue(outcome.metrics["fallback_used"])
        self.assertEqual(outcome.metrics["selected_attempt_index"], 2)
        self.assertEqual(len(outcome.metrics["attempts"]), 2)
        self.assertEqual(primary.call_count, 1)
        self.assertEqual(backup.call_count, 1)


class RuleAiRetryOnlyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = SessionManager("default_module")
        self.session_id = "llm-failure-retry-only"
        self.manager.create_session(self.session_id, "default_module")
        self.state = self.manager.get_session(self.session_id)
        self.module_data = self.manager.get_module_data(self.session_id)

    async def test_rule_ai_invalid_json_does_not_fallback_after_success(self):
        primary = _StubProvider(
            "not-json",
            provider_id="primary",
            configured_model="primary-model",
            actual_model="primary-model",
        )
        backup = _StubProvider(
            '{"intent":"inspect","target":null,"category":"observe"}',
            provider_id="backup",
            configured_model="backup-model",
            actual_model="backup-model",
        )
        rule_ai = RuleAI(
            _Context({"primary": primary, "backup": backup}),
            provider_name="primary",
            fallback_provider_names=["backup"],
            config={"rule_ai_intent_prompt": "{player_input}"},
        )

        with self.assertRaises(RuntimeError):
            await rule_ai.parse_intent("inspect room", trace_id="rule-json")

        metrics = rule_ai.pop_call_metric("rule-json")
        self.assertEqual(metrics["provider_id"], "primary")
        self.assertFalse(metrics["fallback_used"])
        self.assertEqual(metrics["selected_attempt_index"], 1)
        self.assertEqual(primary.call_count, 1)
        self.assertEqual(backup.call_count, 0)


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


class CurrentTurnRetryPayloadTests(unittest.TestCase):
    def _make_plugin(self):
        plugin = AITRPGPlugin(_DummyContext())
        plugin._action_progress = {}
        plugin._last_action_cache = {}
        return plugin

    def test_progress_payload_exposes_rule_partial_results_while_running(self):
        plugin = self._make_plugin()
        session_id = "partial-rule-running"
        plugin._begin_action_progress(session_id, "inspect room")
        plugin._finish_progress_step(session_id, "rule_intent", {}, "done")
        plugin._finish_progress_step(session_id, "rule_adjudication", {}, "done")
        plugin._finish_progress_step(session_id, "rule_check", {}, "done")
        plugin._last_action_cache[session_id] = {
            "player_input": "inspect room",
            "rule_plan": {"intent": "inspect"},
            "rule_result": {"success": True},
            "hard_changes": {"flags": {"rope_cut": True}},
        }

        payload = plugin.get_action_progress_payload(session_id)

        self.assertEqual(payload["partial_results"]["rule_plan"]["intent"], "inspect")
        self.assertTrue(payload["partial_results"]["rule_result"]["success"])
        self.assertEqual(payload["retry_from_hint"], "rhythm")
        self.assertFalse(payload["can_retry"])

    def test_progress_payload_maps_rhythm_failure_to_rhythm_retry(self):
        plugin = self._make_plugin()
        session_id = "partial-rhythm-error"
        plugin._begin_action_progress(session_id, "talk")
        plugin._start_progress_step(session_id, "rhythm", "running")
        plugin._last_action_cache[session_id] = {
            "player_input": "talk",
            "rule_plan": {"intent": "talk"},
            "rule_result": {"success": True},
            "hard_changes": {},
        }
        plugin._fail_progress_step(session_id, "rhythm", "boom", {})
        plugin._complete_action_progress(session_id, status="error", message="boom")

        payload = plugin.get_action_progress_payload(session_id)

        self.assertEqual(payload["retry_from_hint"], "rhythm")
        self.assertTrue(payload["can_retry"])

    def test_progress_payload_keeps_rhythm_result_for_narrative_retry(self):
        plugin = self._make_plugin()
        session_id = "partial-narrative-error"
        plugin._begin_action_progress(session_id, "look around")
        plugin._start_progress_step(session_id, "narrative", "running")
        plugin._last_action_cache[session_id] = {
            "player_input": "look around",
            "rule_plan": {"intent": "inspect"},
            "rule_result": {"success": True},
            "hard_changes": {},
            "rhythm_result": {"feasible": True, "hint": "continue"},
        }
        plugin._fail_progress_step(session_id, "narrative", "boom", {})
        plugin._complete_action_progress(session_id, status="error", message="boom")

        payload = plugin.get_action_progress_payload(session_id)

        self.assertEqual(payload["retry_from_hint"], "narrative")
        self.assertTrue(payload["partial_results"]["rhythm_result"]["feasible"])
        self.assertTrue(payload["can_retry"])

    def test_progress_payload_disables_retry_after_completed_turn(self):
        plugin = self._make_plugin()
        session_id = "partial-completed"
        plugin._begin_action_progress(session_id, "move")
        plugin._complete_action_progress(session_id, status="completed", message="done")
        plugin._last_action_cache[session_id] = {
            "player_input": "move",
            "rule_plan": {"intent": "move"},
            "rule_result": {"success": True},
            "hard_changes": {},
            "rhythm_result": {"feasible": True},
            "narrative_result": {"narrative": "ok", "summary": "ok"},
        }

        payload = plugin.get_action_progress_payload(session_id)

        self.assertFalse(payload["can_retry"])
        self.assertEqual(payload["retry_from_hint"], "narrative")


if __name__ == "__main__":
    unittest.main()
