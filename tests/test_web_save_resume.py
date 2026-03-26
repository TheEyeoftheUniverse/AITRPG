import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
PROJECT_ROOT = os.path.dirname(REPO_ROOT)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if "astrbot" not in sys.modules:
    astrbot_module = types.ModuleType("astrbot")
    astrbot_api_module = types.ModuleType("astrbot.api")

    class _DummyLogger:
        def warning(self, *args, **kwargs):
            return None

        def info(self, *args, **kwargs):
            return None

        def error(self, *args, **kwargs):
            return None

    astrbot_api_module.logger = _DummyLogger()
    astrbot_module.api = astrbot_api_module
    sys.modules["astrbot"] = astrbot_module
    sys.modules["astrbot.api"] = astrbot_api_module

from aitrpg.game_state.save_store import JsonSaveStore
from aitrpg.game_state.session_manager import SessionManager
from aitrpg.webui.server import create_trpg_app


class _DummyPlugin:
    def __init__(self):
        self.session_manager = SessionManager("default_module")


class WebSaveResumeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = os.path.join(REPO_ROOT, "data", f"_test_web_save_{next(tempfile._get_candidate_names())}")
        os.makedirs(self.temp_dir, exist_ok=True)
        self.cookie_id = "resume-cookie"
        self.session_id = "web_resume"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _patched_save_store(self):
        temp_dir = self.temp_dir

        class _TempJsonSaveStore(JsonSaveStore):
            def __init__(self, _base_dir: str):
                super().__init__(temp_dir)

        return _TempJsonSaveStore

    def _write_saved_session(self, *, round_count: int = 3, current_location: str = "master_bedroom"):
        manager = SessionManager("default_module")
        manager.create_session(self.session_id, "default_module")
        state = manager.get_session(self.session_id)
        state["round_count"] = round_count
        state["current_location"] = current_location
        if current_location not in state["visited_locations"]:
            state["visited_locations"].append(current_location)

        saved_state = manager.export_session(self.session_id)
        store = JsonSaveStore(self.temp_dir)
        store.save(self.cookie_id, {
            "saved_at": "2026-03-26T14:32:10+08:00",
            "game_over": False,
            "ending_phase": None,
            "web_session": {
                "session_id": self.session_id,
                "game_started": True,
                "history": [],
                "chat_messages": [{"role": "assistant", "content": "开场"}],
                "last_workflow": None,
                "module_index": 0,
                "conv_id": None,
            },
            "game_state": saved_state,
        })

    async def test_save_summary_does_not_restore_runtime_session(self):
        self._write_saved_session(round_count=8, current_location="master_bedroom")
        plugin = _DummyPlugin()

        with patch("aitrpg.webui.server.JsonSaveStore", self._patched_save_store()):
            app = create_trpg_app(plugin)
            app.testing = True
            client = app.test_client()
            client.set_cookie("localhost", "trpg_session", self.cookie_id)

            response = await client.get("/trpg/api/save-summary")
            payload = await response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["has_save"])
        self.assertEqual(payload["save"]["round_count"], 8)
        self.assertEqual(payload["save"]["current_location"], "master_bedroom")
        self.assertEqual(payload["save"]["current_location_name"], "主卧")
        self.assertFalse(plugin.session_manager.has_session(self.session_id))

    async def test_resume_restores_saved_session_explicitly(self):
        self._write_saved_session(round_count=5, current_location="study")
        plugin = _DummyPlugin()

        with patch("aitrpg.webui.server.JsonSaveStore", self._patched_save_store()):
            app = create_trpg_app(plugin)
            app.testing = True
            client = app.test_client()
            client.set_cookie("localhost", "trpg_session", self.cookie_id)

            state_response = await client.get("/trpg/api/state")
            state_payload = await state_response.get_json()
            self.assertFalse(state_payload["game_started"])
            self.assertFalse(plugin.session_manager.has_session(self.session_id))

            resume_response = await client.post("/trpg/api/resume", json={})
            resume_payload = await resume_response.get_json()

        self.assertEqual(resume_response.status_code, 200)
        self.assertTrue(resume_payload["success"])
        self.assertTrue(resume_payload["game_started"])
        self.assertTrue(plugin.session_manager.has_session(self.session_id))
        restored_state = plugin.session_manager.get_session(self.session_id)
        self.assertEqual(restored_state["round_count"], 5)
        self.assertEqual(restored_state["current_location"], "study")
        self.assertIn("map_data", resume_payload)


if __name__ == "__main__":
    unittest.main()
