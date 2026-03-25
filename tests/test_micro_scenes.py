import unittest

from game_state.session_manager import SessionManager


class MicroSceneTests(unittest.TestCase):
    def setUp(self):
        self.manager = SessionManager("default_module")
        self.session_id = "micro-scene-test"
        self.manager.create_session(self.session_id, "default_module")
        self.state = self.manager.get_session(self.session_id)

    def _set_guard_room(self, room_key: str):
        butler = next(
            npc for npc in self.state["world_state"]["npcs"].values()
            if isinstance(npc, dict) and "chase_state" in npc
        )
        butler["location"] = room_key
        butler["chase_state"].update(
            {
                "active": True,
                "status": "blocked",
                "target": None,
                "activation_round": None,
                "last_target_location": room_key,
                "same_location_rounds": 0,
                "blocked_at": room_key,
            }
        )

    def test_guarded_room_exposes_peek_micro_scene(self):
        self.state["current_location"] = "master_bedroom"
        self._set_guard_room("master_bedroom")

        available = self.manager.get_available_micro_scenes(self.session_id)

        self.assertIn("master_bedroom_peek", available)

    def test_location_first_entry_blocked_warns_then_allows(self):
        self.state["current_location"] = "first_floor_hallway"

        first_result = self.manager.move_player(self.session_id, "living_room")
        self.assertFalse(first_result["success"])
        self.assertTrue(first_result.get("warning_blocked"))
        self.assertTrue(self.state["world_state"]["flags"].get("butler_living_room_warning_shown"))

        second_result = self.manager.move_player(self.session_id, "living_room")
        self.assertTrue(second_result["success"])
        self.assertEqual(self.state["current_location"], "living_room")

    def test_first_peek_only_warns(self):
        self.state["current_location"] = "master_bedroom"
        self._set_guard_room("master_bedroom")

        result = self.manager.enter_micro_scene(self.session_id, "master_bedroom_peek")

        self.assertTrue(result.get("success"))
        self.assertTrue(result.get("warning_only"))
        self.assertFalse(self.manager.is_ending_triggered(self.session_id))
        self.assertTrue(self.state["world_state"]["flags"].get("master_bedroom_peek_warned"))

    def test_second_peek_triggers_bad_ending(self):
        self.state["current_location"] = "master_bedroom"
        self._set_guard_room("master_bedroom")
        self.state["world_state"]["flags"]["master_bedroom_peek_warned"] = True

        result = self.manager.enter_micro_scene(self.session_id, "master_bedroom_peek")

        self.assertTrue(result.get("ending_triggered"))
        self.assertTrue(self.manager.is_ending_triggered(self.session_id))
        self.assertEqual(self.manager.get_ending_id(self.session_id), "door_peek_gaze")

    def test_kitchen_suicide_micro_scene_requires_knife(self):
        self.state["current_location"] = "kitchen"
        available = self.manager.get_available_micro_scenes(self.session_id)
        self.assertNotIn("kitchen_suicide", available)

        self.state["player"]["inventory"].append("刀具")
        available = self.manager.get_available_micro_scenes(self.session_id)
        self.assertIn("kitchen_suicide", available)


if __name__ == "__main__":
    unittest.main()
