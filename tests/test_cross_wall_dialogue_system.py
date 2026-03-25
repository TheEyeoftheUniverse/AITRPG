import sys
import types
import unittest
from pathlib import Path


astrbot_module = types.ModuleType("astrbot")
astrbot_api_module = types.ModuleType("astrbot.api")
astrbot_event_module = types.ModuleType("astrbot.api.event")
astrbot_star_module = types.ModuleType("astrbot.api.star")
quart_module = types.ModuleType("quart")


class _DummyLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _DummyContext:
    def get_provider(self, provider_name: str):
        return None

    def get_using_provider(self):
        return None

    def get_config(self):
        return {}


class _DummyStar:
    def __init__(self, context):
        self.context = context


def _register(*args, **kwargs):
    def decorator(obj):
        return obj

    return decorator


def _identity_decorator(*args, **kwargs):
    def decorator(func):
        return func

    return decorator


quart_module.Quart = object
quart_module.render_template = lambda *args, **kwargs: None
quart_module.request = object()
quart_module.jsonify = lambda *args, **kwargs: None
quart_module.make_response = lambda *args, **kwargs: None

astrbot_api_module.logger = _DummyLogger()
astrbot_event_module.filter = types.SimpleNamespace(
    command=_identity_decorator,
    regex=_identity_decorator,
    event_message_type=_identity_decorator,
    EventMessageType=types.SimpleNamespace(ALL="ALL"),
)
astrbot_event_module.AstrMessageEvent = object
astrbot_star_module.Context = _DummyContext
astrbot_star_module.Star = _DummyStar
astrbot_star_module.register = _register
astrbot_api_module.event = astrbot_event_module
astrbot_api_module.star = astrbot_star_module
astrbot_module.api = astrbot_api_module

sys.modules.setdefault("quart", quart_module)
sys.modules.setdefault("astrbot", astrbot_module)
sys.modules.setdefault("astrbot.api", astrbot_api_module)
sys.modules.setdefault("astrbot.api.event", astrbot_event_module)
sys.modules.setdefault("astrbot.api.star", astrbot_star_module)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aitrpg.ai_layers.rule_ai import RuleAI
from aitrpg.ai_layers.rhythm_ai import RhythmAI
from aitrpg.game_state.location_context import extract_quoted_dialogue_segments
from aitrpg.game_state.session_manager import SessionManager
from aitrpg.main import AITRPGPlugin


class CrossWallDialogueSystemTests(unittest.TestCase):
    def setUp(self):
        self.manager = SessionManager("default_module")
        self.session_id = "cross-wall-dialogue-system"
        self.manager.create_session(self.session_id, "default_module")
        self.state = self.manager.get_session(self.session_id)
        self.module_data = self.manager.get_module_data(self.session_id)
        self.rule_ai = RuleAI(_DummyContext(), config={})
        self.rhythm_ai = RhythmAI(_DummyContext(), config={})
        self.plugin = AITRPGPlugin(_DummyContext())
        self.cross_wall_pair = self.module_data["cross_wall_pairs"][0]
        self.current_location = self.cross_wall_pair["rooms"][0]
        self.other_location = self.cross_wall_pair["rooms"][1]
        world_npcs = self.state["world_state"]["npcs"]
        self.cross_wall_npc_name = next(
            name for name, data in world_npcs.items() if data.get("location") == self.other_location
        )

    def test_plain_action_in_master_bedroom_does_not_expose_cross_wall_npc(self):
        self.state["current_location"] = self.current_location

        rule_scene_npcs = self.rule_ai._get_scene_npcs(
            self.state,
            self.module_data,
            player_input="检查床头柜",
        )
        rhythm_scene_npcs = self.rhythm_ai._build_scene_npc_context(
            self.state,
            self.module_data,
            player_input="检查床头柜",
            rule_plan={
                "input_classification": "action",
                "normalized_action": {"verb": "inspect"},
            },
        )

        self.assertNotIn(self.cross_wall_npc_name, rule_scene_npcs)
        self.assertNotIn(self.cross_wall_npc_name, rhythm_scene_npcs)

    def test_explicit_quoted_cross_wall_dialogue_exposes_cross_wall_npc(self):
        self.state["current_location"] = self.current_location
        player_input = f"你贴近墙边，小声说：“{self.cross_wall_npc_name}，你在吗？”"

        rule_scene_npcs = self.rule_ai._get_scene_npcs(
            self.state,
            self.module_data,
            player_input=player_input,
        )
        rhythm_scene_npcs = self.rhythm_ai._build_scene_npc_context(
            self.state,
            self.module_data,
            player_input=player_input,
            rule_plan={
                "input_classification": "dialogue",
                "normalized_action": {"verb": "talk"},
            },
        )

        self.assertIn(self.cross_wall_npc_name, rule_scene_npcs)
        self.assertEqual(rule_scene_npcs[self.cross_wall_npc_name].get("interaction_mode"), "cross_wall_voice_only")
        self.assertIn(self.cross_wall_npc_name, rhythm_scene_npcs)
        self.assertEqual(rhythm_scene_npcs[self.cross_wall_npc_name].get("interaction_mode"), "cross_wall_voice_only")

    def test_quoted_dialogue_generates_passive_overhear_memory_update(self):
        self.state["current_location"] = self.current_location
        changes = self.plugin._derive_cross_wall_overhear_changes(
            player_input="你压低声音说：“我不是敌人。”",
            rule_plan={"input_classification": "dialogue"},
            game_state=self.state,
            module_data=self.module_data,
        )

        self.assertIn("npc_updates", changes)
        self.assertIn(self.cross_wall_npc_name, changes["npc_updates"])
        memory = changes["npc_updates"][self.cross_wall_npc_name]["memory"]
        self.assertEqual(memory["overheard_remote_dialogue"][0]["text"], "我不是敌人。")
        self.assertEqual(memory["interaction_history"][0]["type"], "cross_wall_overhear")

    def test_extract_quoted_dialogue_segments_returns_quote_contents_only(self):
        self.assertEqual(
            extract_quoted_dialogue_segments('你说：“有人在吗？” 然后又补了一句“我没有恶意”。'),
            ["有人在吗？", "我没有恶意"],
        )


if __name__ == "__main__":
    unittest.main()
