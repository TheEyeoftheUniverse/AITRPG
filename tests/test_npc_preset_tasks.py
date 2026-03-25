import unittest

from game_state.session_manager import SessionManager


class NPCPresetTaskTests(unittest.TestCase):
    def setUp(self):
        self.manager = SessionManager("default_module")
        self.session_id = "npc-preset-task"
        self.manager.create_session(self.session_id, "default_module")
        self.state = self.manager.get_session(self.session_id)

    def _emily_state(self):
        return self.state["world_state"]["npcs"]["艾米莉"]

    def test_emily_exit_hidden_until_report_clue(self):
        self.assertFalse(self.manager._is_location_visible(self.session_id, "emily_exit"))
        self.state["world_state"]["clues_found"].append("艾米莉的调查报告")
        self.assertTrue(self.manager._is_location_visible(self.session_id, "emily_exit"))

    def test_start_solo_search_requirements(self):
        emily = self._emily_state()
        emily["trust_level"] = 0.8
        result = self.manager.start_preset_task(self.session_id, "艾米莉", "solo_search_escape")
        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("task_id"), "solo_search_escape")
        self.assertEqual(emily["preset_task"]["rounds_left"], 8)

    def test_solo_search_runs_eight_rounds(self):
        emily = self._emily_state()
        emily["trust_level"] = 0.8
        self.manager.start_preset_task(self.session_id, "艾米莉", "solo_search_escape")

        for _ in range(8):
            self.manager.update_state(self.session_id, {"world_changes": {}})

        emily = self._emily_state()
        self.assertEqual(emily["location"], "guest_bedroom")
        self.assertTrue(self.state["world_state"]["flags"].get("emily_report_pending"))
        self.assertEqual(emily.get("preset_task", {}), {})

    def test_deliver_pending_report_sets_clue(self):
        self.state["world_state"]["flags"]["emily_report_pending"] = True

        report = self.manager.deliver_pending_npc_reports(self.session_id, "艾米莉")

        self.assertTrue(report.get("delivered"))
        self.assertEqual(report.get("clue"), "艾米莉的调查报告")
        self.assertIn("艾米莉的调查报告", self.state["world_state"]["clues_found"])
        self.assertTrue(self.state["world_state"]["flags"].get("emily_report_delivered"))
        self.assertFalse(self.state["world_state"]["flags"].get("emily_report_pending"))

    def test_cooperative_escape_failure_resets_emily(self):
        emily = self._emily_state()
        emily["trust_level"] = 0.8
        self.manager.start_preset_task(self.session_id, "艾米莉", "cooperative_escape")

        self.manager.update_state(self.session_id, {"world_changes": {}})
        self.manager.resolve_preset_task_branch(self.session_id, "艾米莉", branch="player_abandoned")

        emily = self._emily_state()
        self.assertEqual(emily["location"], "guest_bedroom")
        self.assertEqual(emily["trust_level"], 0.0)
        self.assertTrue(self.state["world_state"]["flags"].get("emily_refuses_cooperation"))


if __name__ == "__main__":
    unittest.main()
