import unittest

from app.activity import activity_feed, public_run


class ActivityFeedTests(unittest.TestCase):
    def test_projects_real_tool_events_without_sensitive_payloads(self):
        activity = activity_feed([
            {"seq": 1, "kind": "model_response", "payload": {"tool_names": ["discover"]}, "created_at": "2026-09-11T10:00:00Z"},
            {"seq": 2, "kind": "tool_started", "payload": {"tool": "discover", "arguments": {"query": "PRIVATE SQL"}}, "created_at": "2026-09-11T10:00:01Z"},
            {"seq": 3, "kind": "tool_result", "payload": {"tool": "discover", "result": {"status": "ok", "sql": "SELECT secret"}}, "created_at": "2026-09-11T10:00:02Z"},
        ])

        self.assertEqual(3, len(activity))
        self.assertEqual("model_response", activity[0]["kind"])
        self.assertIn("discover", activity[0]["detail"])
        self.assertEqual("discover çalıştırılıyor", activity[1]["title"])
        self.assertEqual("discover tamamlandı", activity[2]["title"])
        rendered = str(activity)
        self.assertNotIn("PRIVATE SQL", rendered)
        self.assertNotIn("SELECT secret", rendered)

    def test_recovery_and_failure_have_distinct_user_messages(self):
        activity = activity_feed([
            {"seq": 1, "kind": "tool_recovered", "payload": {"tool": "execute"}},
            {"seq": 2, "kind": "tool_result", "payload": {"tool": "execute", "result": {"status": "blocked", "errors": [{"message": "private"}]}}},
        ])

        self.assertIn("kurtarıldı", activity[0]["title"])
        self.assertIn("tamamlanamadı", activity[1]["title"])
        self.assertNotIn("private", str(activity))

    def test_run_finished_reflects_terminal_status_not_always_completed(self):
        activity = activity_feed([
            {"seq": 1, "kind": "run_finished", "payload": {"status": "completed"}},
            {"seq": 2, "kind": "run_finished", "payload": {"status": "blocked"}},
            {"seq": 3, "kind": "run_finished", "payload": {"status": "failed"}},
            {"seq": 4, "kind": "run_finished", "payload": {"status": "needs_input"}},
        ])
        self.assertIn("tamamlandı", activity[0]["title"])
        self.assertIn("engellendi", activity[1]["title"])
        self.assertNotIn("tamamlandı", activity[2]["title"])
        self.assertIn("kullanıcı girdisi", activity[3]["title"])

    def test_model_and_plan_outcomes_are_labeled(self):
        activity = activity_feed([
            {"seq": 1, "kind": "model_response", "payload": {"tool_names": [], "finish_reason": "length"}},
            {"seq": 2, "kind": "plan_validation", "payload": {"tool": "execute", "validation": {"status": "blocked"}}},
            {"seq": 3, "kind": "model_response", "payload": {"tool_names": ["research_web", "ask_user"]}},
        ])
        self.assertIn("kesildi", activity[0]["title"])
        self.assertIn("reddedildi", activity[1]["title"])
        # newly mapped tools carry real descriptions, not the generic fallback
        self.assertIn("web", activity[2]["detail"].lower())
        self.assertIn("soru", activity[2]["detail"].lower())

    def test_public_run_omits_agent_state_and_keeps_browser_metadata(self):
        run = public_run({"run_id": "run_1", "workspace_id": "workspace_1", "message": "Soru", "status": "running",
                          "state": {"messages": [{"tool_calls": [{"arguments": "SELECT private"}]}]}, "result": None})

        self.assertEqual("run_1", run["run_id"])
        self.assertEqual("Soru", run["message"])
        self.assertNotIn("state", run)
        self.assertNotIn("SELECT private", str(run))
