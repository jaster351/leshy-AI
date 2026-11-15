"""Regression: Marketplace must forward session_id into the core chat context.

Both Web and Telegram call the same RealtimeChatManager._build_context.
The semantic dialogue-memory implementation passes session_id by keyword.
"""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from leshy.llm import ChatMessage
from leshy.marketplace_chat import install_marketplace_chat_bridge
from leshy.realtime_chat import RealtimeChatManager


class MarketplaceSessionContextTests(unittest.TestCase):
    def setUp(self):
        self.observed = []

        def original(manager, message, owner_id, rows, *,
                     search_plan=None, lookup_query=None, session_id=None):
            self.observed.append({
                "message": message, "owner_id": owner_id, "rows": rows,
                "search_plan": search_plan, "lookup_query": lookup_query,
                "session_id": session_id,
            })
            return [ChatMessage("system", "base"), ChatMessage("user", message)], [], False

        self.context_patch = patch.object(RealtimeChatManager, "_build_context", original)
        self.context_patch.start()
        self.addCleanup(self.context_patch.stop)
        install_marketplace_chat_bridge()

    def test_telegram_message_preserves_session_identity(self):
        manager = SimpleNamespace(app=SimpleNamespace(marketplace=None))
        session_id = "telegram:123:123"
        messages, sources, searched = RealtimeChatManager._build_context(
            manager, "Доброе утро", "telegram:123", [],
            search_plan={"needed": False},
            lookup_query="",
            session_id=session_id,
        )
        self.assertEqual(self.observed[0]["session_id"], session_id)
        self.assertEqual(self.observed[0]["owner_id"], "telegram:123")
        self.assertEqual(messages[-1].content, "Доброе утро")
        self.assertEqual(sources, [])
        self.assertFalse(searched)

    def test_web_session_and_marketplace_context_still_work(self):
        marketplace = SimpleNamespace(
            skill_context=lambda: "test-skill",
            plugin_context=lambda: "test-plugin",
        )
        manager = SimpleNamespace(app=SimpleNamespace(marketplace=marketplace))
        messages, _, _ = RealtimeChatManager._build_context(
            manager, "Привет", "owner-uuid", [],
            lookup_query="Привет", session_id="web-session",
        )
        self.assertEqual(self.observed[0]["session_id"], "web-session")
        self.assertEqual(self.observed[0]["lookup_query"], "Привет")
        self.assertIn("test-skill", messages[1].content)
        self.assertIn("test-plugin", messages[2].content)
        self.assertEqual(messages[0].content, "base")

    def test_session_id_optional_for_previous_callers(self):
        manager = SimpleNamespace(app=SimpleNamespace(marketplace=None))
        RealtimeChatManager._build_context(manager, "test", "owner", [])
        self.assertIsNone(self.observed[0]["session_id"])

    def test_installation_is_idempotent(self):
        original_wrapped = RealtimeChatManager._build_context
        install_marketplace_chat_bridge()
        self.assertIs(RealtimeChatManager._build_context, original_wrapped)


if __name__ == "__main__":
    unittest.main()
