from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from leshy.telegram import TelegramBridge


class _App:
    def __init__(self):
        self.chat_calls = []
        self.learning_calls = []

    def chat(self, text, *, owner_id, session_id):
        self.chat_calls.append((text, owner_id, session_id))
        return {"answer": "ok"}

    def set_learning(self, enabled):
        self.learning_calls.append(bool(enabled))
        return bool(enabled)

    def status(self):
        return {"version": "test", "model": "test", "learning_enabled": False}


class TelegramAccessTests(unittest.TestCase):
    def make_bridge(self, root: Path):
        app = _App()
        bridge = TelegramBridge(app, root / "LeshyBrain")
        sent = []
        answers = []
        bridge._send = lambda chat_id, text: sent.append((chat_id, text))
        bridge._send_answer = lambda chat_id, text: answers.append((chat_id, text))
        bridge._send_typing = lambda chat_id: None
        return app, bridge, sent, answers

    def test_allow_all_requires_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            _, bridge, _, _ = self.make_bridge(Path(temp))
            with self.assertRaisesRegex(ValueError, "хозяина"):
                bridge.configure("", [], allow_all=True, owner_id="")

    def test_allow_all_allows_chat_but_not_admin_for_stranger(self):
        with tempfile.TemporaryDirectory() as temp:
            app, bridge, sent, answers = self.make_bridge(Path(temp))
            bridge.configure("", [], allow_all=True, owner_id="100")
            bridge._handle_message({"chat": {"id": 1}, "from": {"id": 200}, "text": "привет"})
            self.assertEqual(len(app.chat_calls), 1)
            self.assertEqual(app.chat_calls[0][1], "telegram:200")
            self.assertEqual(answers, [(1, "ok")])

            bridge._handle_message({"chat": {"id": 1}, "from": {"id": 200}, "text": "/learning_on"})
            self.assertEqual(app.learning_calls, [])
            self.assertTrue(any("только хозяину" in text for _, text in sent))

    def test_owner_always_has_access_and_admin_rights(self):
        with tempfile.TemporaryDirectory() as temp:
            app, bridge, _, _ = self.make_bridge(Path(temp))
            bridge.configure("", [], allow_all=False, owner_id="100")
            bridge._handle_message({"chat": {"id": 1}, "from": {"id": 100}, "text": "/learning_on"})
            self.assertEqual(app.learning_calls, [True])
            status = bridge.status()
            self.assertEqual(status["owner_id"], "100")
            self.assertFalse(status["allow_all"])

    def test_single_legacy_allowed_user_is_migrated_as_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            _, bridge, _, _ = self.make_bridge(Path(temp))
            bridge.configure("", ["77"])
            self.assertEqual(bridge.status()["owner_id"], "77")


if __name__ == "__main__":
    unittest.main()
