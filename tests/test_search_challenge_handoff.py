from __future__ import annotations

import unittest
from unittest.mock import Mock
from types import SimpleNamespace

from leshy.search_challenge_handoff import ChallengeBroker


class FakePage:
    url = "https://www.bing.com/search?q=example"

    def __init__(self):
        self.passed = False
        self.mouse = SimpleNamespace(
            click=Mock(side_effect=self._click),
            wheel=Mock(),
        )
        self.keyboard = SimpleNamespace(insert_text=Mock(), press=Mock())
        self.reload = Mock()

    def _click(self, x, y):
        self.passed = True

    def screenshot(self, **kwargs):
        return b"\x89PNG\r\n\x1a\n"


class HandoffTests(unittest.TestCase):
    def test_live_page_accepts_owner_click_then_resumes(self):
        broker = ChallengeBroker()
        page = FakePage()
        ticket = broker.create(page, "bing")
        self.assertIsNotNone(ticket)
        self.assertTrue(ticket.ticket)
        self.assertTrue(broker.get(ticket.ticket)["screenshot"].startswith("data:image/png;base64,"))
        self.assertTrue(broker.enqueue(ticket.ticket, "click", x=120, y=150))
        self.assertTrue(broker.await_solution(page, ticket, lambda: page.passed))
        page.mouse.click.assert_called_once_with(120, 150)
        self.assertEqual(broker.get(ticket.ticket)["status"], "solved")
        broker.complete(ticket, [SimpleNamespace(title="Article", url="https://example.org", snippet="Result")])
        public = broker.get(ticket.ticket)
        self.assertEqual(public["results"][0]["title"], "Article")
        self.assertFalse(public["screenshot"])

    def test_random_ticket_required_and_actions_bounded(self):
        broker = ChallengeBroker()
        ticket = broker.create(FakePage(), "bing")
        self.assertIsNone(broker.get("unknown"))
        self.assertFalse(broker.enqueue("unknown", "click", x=4, y=5))
        self.assertFalse(broker.enqueue(ticket.ticket, "click", x=999999, y=50))
        self.assertFalse(broker.enqueue(ticket.ticket, "press", text="Control+Shift+J"))
        self.assertFalse(broker.enqueue(ticket.ticket, "type", text="X" * 257))
        self.assertFalse(broker.enqueue(ticket.ticket, "eval", text="window.alert(1)"))

    def test_cancel_does_not_mark_challenge_solved(self):
        broker = ChallengeBroker()
        page = FakePage()
        ticket = broker.create(page, "google")
        self.assertTrue(broker.enqueue(ticket.ticket, "cancel"))
        self.assertFalse(broker.await_solution(page, ticket, lambda: False))
        self.assertEqual(broker.get(ticket.ticket)["status"], "cancelled")
        self.assertFalse(broker.enqueue(ticket.ticket, "click", x=10, y=10))


if __name__ == "__main__":
    unittest.main()
