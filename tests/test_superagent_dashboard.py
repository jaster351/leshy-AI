import unittest
from types import SimpleNamespace
from leshy.superagent_dashboard import execute_action, list_tickets, ticket_details


class FakeService:
    def __init__(self):
        self.tasks = {
            "SA-123": {"id": "SA-123", "title": "Проверить сервер", "state": "working", "attempt": 1,
                       "owner_id": "web:alice", "latest_report": "", "attention": ""},
            "SA-456": {"id": "SA-456", "title": "Помочь с кодом", "state": "failed", "attempt": 2,
                       "owner_id": "web:bob", "latest_report": "", "attention": ""},
        }
        self.calls = []
        self.plans = SimpleNamespace(snapshot=self.plan_snapshot, control=self.plan_control, create=self.plan_create)

    def get(self, ticket_id, *, owner_id):
        item = self.tasks[ticket_id]
        if item["owner_id"] != owner_id:
            raise PermissionError("Not your task")
        return dict(item)

    def list(self, owner_id, *, limit=50):
        return [dict(x) for x in self.tasks.values() if x["owner_id"] == owner_id][:limit]

    def create(self, owner_id, session_id, value):
        self.calls.append(("create", owner_id, session_id, value))
        self.tasks["SA-789"] = {"id":"SA-789", "owner_id":owner_id, "title":value, "state":"queued"}
        return self.get("SA-789", owner_id=owner_id)

    def stop_ticket(self, ticket_id, owner_id):
        self.calls.append(("stop", ticket_id))
        self.tasks[ticket_id]["state"] = "stopped"
        return self.get(ticket_id, owner_id=owner_id)

    def retry(self, ticket_id, owner_id):
        self.calls.append(("retry", ticket_id))
        self.tasks[ticket_id]["state"] = "queued"
        return self.get(ticket_id, owner_id=owner_id)

    def answer(self, ticket_id, owner_id, text):
        self.calls.append(("answer", ticket_id, text))
        self.tasks[ticket_id]["state"] = "queued"
        return self.get(ticket_id, owner_id=owner_id)

    def plan_snapshot(self, ticket_id, owner_id):
        raise KeyError("No plan")

    def plan_create(self, ticket_id, owner_id, steps):
        self.calls.append(("plan_create", ticket_id, len(steps)))
        return {"id": ticket_id, "state": "active", "steps": steps}

    def plan_control(self, ticket_id, owner_id, action):
        self.calls.append((action, ticket_id))
        return {"id":ticket_id, "state":"paused"}


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeService()

    def test_list_owner_isolation_and_button_states(self):
        rows = list_tickets(self.service, "web:alice")["tasks"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["can_stop"])
        self.assertFalse(rows[0]["can_retry"])
        self.assertNotIn("owner_id", rows[0])

    def test_stop_owner_is_checked_by_service(self):
        with self.assertRaises(PermissionError):
            execute_action(self.service, "web:bob", {"action":"stop", "id":"SA-123"})
        self.assertEqual(self.service.calls, [])
        result = execute_action(self.service, "web:alice", {"action":"stop", "id":"SA-123"})
        self.assertEqual(result["task"]["state"], "stopped")
        with self.assertRaises(ValueError):
            execute_action(self.service, "web:alice", {"action":"stop", "id":"SA-123"})

    def test_retry_only_failed_or_blocked(self):
        with self.assertRaises(ValueError):
            execute_action(self.service, "web:alice", {"action":"retry", "id":"SA-123"})
        result = execute_action(self.service, "web:bob", {"action":"retry", "id":"SA-456"})
        self.assertEqual(result["task"]["state"], "queued")

    def test_answer_waiting_only_and_owner_scoped(self):
        self.service.tasks["SA-123"]["state"] = "waiting"
        with self.assertRaises(ValueError):
            execute_action(self.service, "web:alice", {"action":"answer", "id":"SA-123", "text":" "})
        result = execute_action(self.service, "web:alice", {"action":"answer", "id":"SA-123", "text":"Да"})
        self.assertEqual(result["task"]["state"], "queued")

    def test_create_explicit_scope_and_validation(self):
        with self.assertRaises(ValueError):
            execute_action(self.service, "web:alice", {"action":"create", "text":"  "})
        task = execute_action(self.service, "web:alice", {"action":"create", "text":"Изучи ошибку"})["task"]
        self.assertEqual(task["state"], "queued")
        self.assertEqual(self.service.calls[0], ("create", "web:alice", "", "Изучи ошибку"))

    def test_details_do_not_expose_secrets(self):
        task = ticket_details(self.service, "web:alice", "SA-123")["task"]
        self.assertNotIn("owner_id", task)
        self.assertNotIn("mandate", task)
        self.assertEqual(task["steps"], [])

    def test_plan_actions_deny_cross_owner(self):
        with self.assertRaises(PermissionError):
            execute_action(self.service, "web:bob", {"action":"plan_pause", "id":"SA-123"})
        value = execute_action(self.service, "web:alice", {"action":"plan_pause", "id":"SA-123"})
        self.assertEqual(value["plan"]["state"], "paused")

    def test_plan_create_requires_valid_nonterminal_task(self):
        with self.assertRaises(ValueError):
            execute_action(self.service, "web:alice", {"action":"plan_create", "id":"SA-123", "steps": []})
        with self.assertRaises(PermissionError):
            execute_action(self.service, "web:bob", {"action":"plan_create", "id":"SA-123", "steps":[{"id":"a", "title":"A"}]})
        plan = execute_action(self.service, "web:alice", {"action":"plan_create", "id":"SA-123", "steps":[{"id":"a", "title":"A"}]})
        self.assertEqual(plan["plan"]["state"], "active")
        self.service.tasks["SA-123"]["state"] = "stopped"
        with self.assertRaises(ValueError):
            execute_action(self.service, "web:alice", {"action":"plan_create", "id":"SA-123", "steps":[{"id":"a", "title":"A"}]})

    def test_unsupported_actions_rejected(self):
        with self.assertRaises(ValueError):
            execute_action(self.service, "web:alice", {"action":"delete", "id":"SA-123"})


if __name__ == "__main__":
    unittest.main()
