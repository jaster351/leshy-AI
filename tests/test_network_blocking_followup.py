"""Regression tests for TSPU explanations in the same chat session."""
import sqlite3
import unittest
from contextlib import contextmanager

from leshy.network_blocking_runtime import (
    _explain_saved_diagnostic,
    _is_diagnostic_followup,
    _previous_diagnostic,
)


LEGACY = (
    "109.248.175.75: IP заблокирован ТСПУ с высокой уверенностью (100%). "
    "тип сервера не определён.\nРешение: сменить IP-адрес."
)
MODERN = (
    "IP-адрес 109.248.175.75. Блокировка ТСПУ не подтверждена.\n\n"
    "Проверка 109.248.175.75\n"
    "Время проверки: 2026-11-04 15:30:00 UTC\n"
    "Локально TCP/22 (SSH/Linux): timeout\n"
    "Cheburcheck: проверить не удалось\n"
    "Check-Host TCP/22: RU: timeout; DE: connected\n"
    "Запуски инструментов (ID; статус; время UTC):\n"
    "tcp.connect: receipt-123; success; 2026-11-04T15:30:00Z"
)


class FakeStorage:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE sessions (id TEXT, owner_id TEXT)")
        self.conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)")
        self.conn.execute("INSERT INTO sessions VALUES ('chat-1', 'telegram:owner')")
        self.conn.execute("INSERT INTO sessions VALUES ('chat-2', 'telegram:owner')")
        self.conn.commit()

    @contextmanager
    def connection(self):
        yield self.conn

    def save(self, session, role, body):
        self.conn.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (?,?,?)",
            (session, role, body),
        )
        self.conn.commit()


class App:
    def __init__(self):
        self.storage = FakeStorage()


class NetworkBlockingFollowupTests(unittest.TestCase):
    def test_user_can_ask_how_it_was_decided(self):
        self.assertTrue(_is_diagnostic_followup("Как ты это понял?"))
        self.assertTrue(_is_diagnostic_followup("Покажи результаты"))
        self.assertTrue(_is_diagnostic_followup("На основании чего?"))
        self.assertFalse(_is_diagnostic_followup("Купи новый SSD"))
        self.assertFalse(_is_diagnostic_followup(""))

    def test_legacy_hundred_percent_claim_is_not_explained_with_fabrication(self):
        app = App()
        app.storage.save("chat-1", "assistant", LEGACY)
        last = _previous_diagnostic(app, "telegram:owner", "chat-1")
        self.assertEqual(last, LEGACY)
        result = _explain_saved_diagnostic(last)
        self.assertIn("нельзя достоверно обосновать", result)
        self.assertIn("100% ТСПУ", result)
        self.assertNotIn("выполнил проверку через Яндекс", result)

    def test_new_report_is_replayed_without_rerunning_probes(self):
        app = App()
        app.storage.save("chat-1", "assistant", MODERN)
        last = _previous_diagnostic(app, "telegram:owner", "chat-1")
        self.assertEqual(last, MODERN)
        result = _explain_saved_diagnostic(last)
        self.assertIn("tcp.connect: receipt-123", result)
        self.assertIn("Check-Host TCP/22: RU: timeout; DE: connected", result)
        self.assertIn("не выполнял новую проверку", result)

    def test_followup_does_not_cross_owner_boundary(self):
        app = App()
        app.storage.save("chat-1", "assistant", MODERN)
        self.assertIsNone(_previous_diagnostic(app, "telegram:another", "chat-1"))
        self.assertIsNone(_previous_diagnostic(app, "telegram:owner", "chat-2"))

    def test_ordinary_previous_reply_is_not_hijacked(self):
        app = App()
        app.storage.save("chat-1", "assistant", "Привет! Как дела?")
        self.assertIsNone(_previous_diagnostic(app, "telegram:owner", "chat-1"))

    def test_intervening_user_reply_blocks_stale_diagnostic(self):
        app = App()
        app.storage.save("chat-1", "assistant", MODERN)
        app.storage.save("chat-1", "user", "Это отдельный вопрос.")
        self.assertIsNone(_previous_diagnostic(app, "telegram:owner", "chat-1"))


if __name__ == "__main__":
    unittest.main()
