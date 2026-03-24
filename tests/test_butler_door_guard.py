import unittest

from game_state.session_manager import SessionManager


class ButlerDoorGuardTests(unittest.TestCase):
    def setUp(self):
        self.manager = SessionManager("default_module")
        self.session_id = "door-guard-test"
        self.manager.create_session(self.session_id, "default_module")
        self.state = self.manager.get_session(self.session_id)
        self.state["player"]["skills"]["闪避"] = 100
        self.butler = self.state["world_state"]["npcs"]["管家"]

    def _set_butler_chase(self, *, location: str, status: str, blocked_at: str | None = None, target="player"):
        self.butler["location"] = location
        self.butler["chase_state"].update(
            {
                "active": True,
                "status": status,
                "target": target,
                "activation_round": 0,
                "last_target_location": location,
                "same_location_rounds": 0,
                "blocked_at": blocked_at,
            }
        )

    def test_same_room_capture_requires_one_extra_round(self):
        self.state["current_location"] = "living_room"
        self._set_butler_chase(location="living_room", status="pursuing")

        self.manager.update_state(self.session_id, {"world_changes": {}})
        self.assertFalse(self.manager.is_ending_triggered(self.session_id))
        self.assertEqual(self.butler["chase_state"]["same_location_rounds"], 1)

        self.manager.update_state(self.session_id, {"world_changes": {}})
        self.assertTrue(self.manager.is_ending_triggered(self.session_id))
        self.assertEqual(
            self.state["world_state"]["flags"].get("butler_capture_reason"),
            "stayed_with_butler_too_long",
        )

    def test_guarded_room_does_not_force_false_dodge_in_hallway(self):
        self.state["current_location"] = "second_floor_hallway"
        self._set_butler_chase(location="guest_bedroom", status="blocked", blocked_at="guest_bedroom", target=None)

        chase = self.manager.get_butler_chase_context(self.session_id)
        self.assertEqual(chase["butler_location"], "guest_bedroom")
        self.assertEqual(chase["player_relation"], "separate_rooms")

        result = self.manager.move_player(self.session_id, "first_floor_hallway")
        self.assertTrue(result["success"])
        self.assertIsNone(result.get("check_result"))

    def test_entering_guarded_room_is_blocked_without_game_over(self):
        self.state["current_location"] = "second_floor_hallway"
        self._set_butler_chase(location="guest_bedroom", status="blocked", blocked_at="guest_bedroom", target=None)

        result = self.manager.move_player(self.session_id, "guest_bedroom")
        self.assertFalse(result["success"])
        self.assertTrue(result.get("warning_blocked"))
        self.assertFalse(result.get("caught", False))
        self.assertFalse(self.manager.is_ending_triggered(self.session_id))

    def test_opening_guarded_room_door_from_inside_triggers_capture(self):
        self.state["current_location"] = "guest_bedroom"
        self._set_butler_chase(location="guest_bedroom", status="blocked", blocked_at="guest_bedroom", target=None)

        self.assertEqual(self.manager._get_available_moves(self.session_id), set())

        result = self.manager.move_player(self.session_id, "second_floor_hallway")
        self.assertFalse(result["success"])
        self.assertTrue(result["caught"])
        self.assertTrue(self.manager.is_ending_triggered(self.session_id))
        self.assertEqual(
            self.state["world_state"]["flags"].get("butler_capture_reason"),
            "opened_guarded_door",
        )

    def test_bait_completion_keeps_door_destination_in_guard_state(self):
        bait_npc = next(name for name in self.state["world_state"]["npcs"] if name != "管家")
        self.state["world_state"]["npcs"][bait_npc]["companion_mode"] = "bait"
        self.state["world_state"]["npcs"][bait_npc]["companion_task"] = {
            "destination": "guest_bedroom",
            "target_entity": "管家",
            "on_complete_self": "wait",
        }
        self._set_butler_chase(location="guest_bedroom", status="blocked", blocked_at="guest_bedroom", target=bait_npc)

        changes = self.manager._finalize_companion_tasks(self.state)

        self.assertEqual(self.butler["location"], "guest_bedroom")
        self.assertTrue(self.butler["chase_state"]["active"])
        self.assertEqual(self.butler["chase_state"]["status"], "blocked")
        self.assertEqual(self.butler["chase_state"]["blocked_at"], "guest_bedroom")
        self.assertEqual(
            changes["npc_updates"][bait_npc]["companion_mode"],
            "wait",
        )


if __name__ == "__main__":
    unittest.main()
