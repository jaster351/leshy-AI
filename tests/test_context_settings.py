"""Настройка контекста и восстановление истории общих Web/Telegram вызовов."""
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from leshy.context_settings import ContextSettingsStore
from leshy.context_history import load_session_context


class ContextSettingsTests(unittest.TestCase):
    def test_auto_manual_and_unlimited_do_not_exceed_physical_model(self):
        with TemporaryDirectory() as tmp:
            store = ContextSettingsStore(Path(tmp))
            self.assertEqual(store.load()["mode"], "auto")
            self.assertEqual(store.context_size(4096), 4096)
            store.save(mode="manual", level=0)
            self.assertEqual(store.context_size(4096), 1024)
            self.assertLessEqual(store.context_size(2048), 2048)
            store.save(mode="manual", level=50)
            self.assertGreater(store.context_size(4096), 1024)
            self.assertLess(store.context_size(4096), 4096)
            store.save(mode="manual", level=100)
            self.assertTrue(store.unlimited_history())
            self.assertEqual(store.context_size(4096), 4096)
            self.assertEqual(store.context_size(512), 512)
            self.assertEqual(store.status()["affects"],
                             ["web", "telegram", "superagent", "shared_llm"])
            self.assertTrue(store.status()["learning_unchanged"])

    def test_persists_selection_and_rejects_invalid_values(self):
        with TemporaryDirectory() as tmp:
            store = ContextSettingsStore(Path(tmp))
            store.save(mode="manual", level=73)
            loaded = ContextSettingsStore(Path(tmp))
            self.assertEqual(loaded.load(), {"mode": "manual", "level": 73})
            for kwargs in (
                {"mode": "root", "level": 10},
                {"mode": "manual", "level": 101},
                {"mode": "manual", "level": -1},
                {"mode": "manual", "level": True},
            ):
                with self.subTest(kwargs=kwargs):
                    with self.assertRaises(ValueError):
                        loaded.save(**kwargs)

    def test_original_question_recovered_after_long_history(self):
        with TemporaryDirectory() as tmp:
            store = ContextSettingsStore(Path(tmp))
            connection = sqlite3.connect(":memory:")
            connection.row_factory = sqlite3.Row
            connection.execute(
                "CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT)"
            )
            connection.execute(
                "INSERT INTO messages(session_id,role,content) VALUES(?,?,?)",
                ("s1", "user", "Расскажи, когда выйдет GTA 6, сколько дней осталось?"),
            )
            for index in range(100):
                connection.execute(
                    "INSERT INTO messages(session_id,role,content) VALUES(?,?,?)",
                    ("s1", "assistant", "Промежуточный ответ " + str(index)),
                )
                connection.execute(
                    "INSERT INTO messages(session_id,role,content) VALUES(?,?,?)",
                    ("s1", "user", "Обсуждаем сериал " + str(index)),
                )
            connection.execute(
                "INSERT INTO messages(session_id,role,content) VALUES(?,?,?)",
                ("s2", "user", "Чужая секретная история пользователя"),
            )
            connection.execute(
                "INSERT INTO messages(session_id,role,content) VALUES(?,?,?)",
                ("s1", "user", "и ответь на мой первоначальный вопрос"),
            )
            auto = load_session_context(
                connection, "s1", "и ответь на мой первоначальный вопрос", store
            )
            self.assertTrue(any("GTA 6" in row["content"] for row in auto))
            self.assertFalse(any("Чужая секретная" in row["content"] for row in auto))
            store.save(mode="manual", level=100)
            full = load_session_context(connection, "s1", "GTA 6", store)
            self.assertTrue(any("GTA 6" in row["content"] for row in full))
            self.assertLessEqual(len(full), 105)
            connection.close()


if __name__ == "__main__":
    unittest.main()
