"""Check that explicit NAS commands bypass web search but preserve deduplication."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from leshy import chat_idempotency as ci
from leshy import torrent_parser_chat as router
from leshy.realtime_chat import StreamEvent


PROMPT = "Скачай фильм, обитель зла."


class _Ledger:
    def __init__(self):
        self.completed = {}
        self.claims = 0

    def claim(self, request_id, *, owner_id, channel, session_id):
        self.claims += 1
        if request_id in self.completed:
            return {"acquired": False, "status": "done",
                    "request_id": request_id, "response": self.completed[request_id]}
        return {"acquired": True, "status": "in_progress", "request_id": request_id}

    def bind_session(self, *args):
        return None

    def complete(self, request_id, response, *, session_id=None):
        self.completed[request_id] = response

    def fail(self, request_id, message):
        raise AssertionError("Unexpected ledger failure: " + str(message))


class RoutePriorityTests(unittest.TestCase):
    def _install_mock_runtime(self, ledger, generic, parser):
        """Mock startup wraps and restore global methods after every test."""
        patches = [
            patch.object(ci, "_INSTALLED", False),
            patch.object(ci, "_ORIGINAL_STREAM", None),
            patch.object(ci, "_ORIGINAL_CHAT", None),
            patch.object(ci, "_ORIGINAL_TELEGRAM_HANDLE", None),
            patch.object(ci, "ledger_for", return_value=ledger),
            patch.object(ci, "stream_parser_chat", side_effect=parser),
            patch.object(ci.RealtimeChatManager, "stream", generic),
            patch.object(ci.LeshyApp, "chat", lambda app, msg, **kw: {}),
            patch.object(ci.FastTelegramBridge, "_handle_message", lambda bot, msg: None),
            patch.object(ci, "event"),
        ]
        for context in patches:
            context.start()
            self.addCleanup(context.stop)
        ci.install_global_chat_idempotency()

    def test_web_and_telegram_bypass_search_and_retry_is_idempotent(self):
        ledger = _Ledger()
        generic = Mock(side_effect=AssertionError("LLM/web search must not run"))
        parser_calls = []

        def route(manager, message, *, owner_id, session_id, request_id):
            parser_calls.append((message, owner_id, session_id, request_id))
            yield StreamEvent("start", {"session_id": "session-1", "request_id": request_id})
            yield StreamEvent("delta", {"text": "Отправлено на NAS"})
            yield StreamEvent("done", {"session_id": "session-1",
                                       "answer": "Отправлено на NAS",
                                       "request_id": request_id})

        self._install_mock_runtime(ledger, generic, route)
        manager = SimpleNamespace(app=SimpleNamespace())
        for owner in ("web-owner", "telegram:123"):
            rid = "route-" + owner
            result = list(ci.RealtimeChatManager.stream(
                manager, PROMPT, owner_id=owner, request_id=rid,
            ))
            self.assertEqual([item.type for item in result], ["start", "delta", "done"])
            self.assertEqual(result[-1].data["answer"], "Отправлено на NAS")
            replay = list(ci.RealtimeChatManager.stream(
                manager, PROMPT, owner_id=owner, request_id=rid,
            ))
            self.assertEqual([item.type for item in replay], ["duplicate"])
        self.assertEqual(len(parser_calls), 2)
        generic.assert_not_called()

    def test_normal_chat_uses_existing_pipeline(self):
        ledger = _Ledger()
        generic = Mock(return_value=iter([
            StreamEvent("start", {"session_id": "normal"}),
            StreamEvent("done", {"session_id": "normal", "answer": "Обычный чат"}),
        ]))
        def unexpected(*args, **kwargs):
            raise AssertionError("Unexpected Parser for Plex route")
        self._install_mock_runtime(ledger, generic, unexpected)
        manager = SimpleNamespace(app=SimpleNamespace())
        result = list(ci.RealtimeChatManager.stream(
            manager, "Как дела?", owner_id="web-owner", request_id="normal-request",
        ))
        self.assertEqual(result[-1].data["answer"], "Обычный чат")
        generic.assert_called_once()

    def test_parser_request_resolves_owner_from_confirmed_link(self):
        bridge = Mock()
        bridge.owner_allowed.side_effect = lambda owner: owner == "linked-web-owner"
        bridge._read.return_value = {"configured": True}
        bridge.execute_chat.return_value = "NAS подтвердил запрос"
        app = MagicMock()
        app.settings.brain_dir = "/tmp/no-real-brain"
        app.owner_identity.resolve_telegram.side_effect = lambda identity: (
            "linked-web-owner" if identity == "123" else "telegram:" + identity
        )
        app._ensure_session.return_value = "telegram-session"
        app._save_assistant.return_value = "2026-11-14T00:00:00"
        with patch.object(router, "TorrentParserBridge", return_value=bridge), patch.object(
            router, "event"
        ):
            saved = router.execute_parser_chat(
                app, PROMPT, owner_id="telegram:123", session_id=None
            )
        self.assertEqual(saved["answer"], "NAS подтвердил запрос")
        bridge.execute_chat.assert_called_once_with("linked-web-owner", PROMPT)
        args, _ = app.storage.connection.return_value.__enter__.return_value.execute.call_args
        self.assertTrue(args[0].startswith("UPDATE sessions SET"))


if __name__ == "__main__":
    unittest.main()
