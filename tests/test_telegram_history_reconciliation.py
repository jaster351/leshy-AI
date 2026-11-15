"""Telegram and Web expose the same stored chats after verified login.

Legacy bot requests and vision handlers used owner_id='telegram:<id>'.
Existing Telegram users could sign in successfully without importing these
sessions; Web's owner-scoped /v1/sessions list consequently remained empty.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from leshy.owner_identity import OwnerIdentityService
from leshy.storage import Storage, utcnow
from leshy.telegram_browser_login import TelegramBrowserLogin


class TelegramHistoryReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        brain = Path(self.temp.name)
        self.identity = OwnerIdentityService(Storage(brain / "main.sqlite3"), brain)
        self.login = TelegramBrowserLogin(self.identity)
        self.owner, self.cookie, self.cookie_hash = self.identity.ensure_web_session("", "web:local")
        self.sender = {"id": 12345, "first_name": "User", "username": "verified_telegram"}

    def _link(self, *, owner_cookie_hash=None):
        session = owner_cookie_hash or self.cookie_hash
        start = self.login.begin(session)
        info = self.login.prepare_telegram_confirmation(start["token"], "12345")
        return self.login.finish(info["callback"], "12345", self.sender, True)

    def _chat(self, owner, sid, content):
        now = utcnow()
        with self.identity.storage.connection() as conn:
            conn.execute(
                "INSERT INTO sessions(id,owner_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                (sid, owner, content, now, now),
            )
            conn.execute(
                "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                (sid, "user", content, now),
            )

    def _session_owner(self, sid):
        with self.identity.storage.connection() as conn:
            return conn.execute(
                "SELECT owner_id FROM sessions WHERE id=?", (sid,),
            ).fetchone()["owner_id"]

    def test_existing_telegram_signin_imports_legacy_chat(self):
        self._link()
        legacy = "telegram:12345"
        self._chat(legacy, "telegram:12345:12345", "Старый диалог с ботом")
        # Opening the Web profile from another computer signs into the
        # already linked Telegram account, not a new identity.
        guest2, cookie2, session2 = self.identity.ensure_web_session("", "web:local")
        self._chat(guest2, "guest2", "До входа")
        result = self._link(owner_cookie_hash=session2)
        self.assertEqual(result["mode"], "login")
        self.assertEqual(self.identity.owner_for_web_cookie(cookie2), self.owner)
        self.assertEqual(self._session_owner("telegram:12345:12345"), self.owner)
        self.assertEqual(self._session_owner("guest2"), self.owner)
        with self.identity.storage.connection() as conn:
            rows = conn.execute(
                "SELECT id FROM sessions WHERE owner_id=?", (self.owner,),
            ).fetchall()
        self.assertEqual(
            {row["id"] for row in rows},
            {"telegram:12345:12345", "guest2"},
        )

    def test_subsequent_legacy_photo_and_voice_messages_are_visible(self):
        self._link()
        # Historical direct-storage code paths bypass owner canonicalization.
        self._chat("telegram:12345", "telegram:private:photo", "Фото")
        self._chat("telegram:12345", "telegram:group:photo", "Группа")
        state = self.login.reconcile_linked_telegram_history(self.owner)
        self.assertTrue(state["linked"])
        self.assertEqual(state["sessions_moved"], 2)
        self.assertEqual(self._session_owner("telegram:private:photo"), self.owner)
        self.assertEqual(self._session_owner("telegram:group:photo"), self.owner)
        again = self.login.reconcile_linked_telegram_history(self.owner)
        self.assertEqual(again["sessions_moved"], 0)
        with self.identity.storage.connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        self.assertEqual(count, 2)

    def test_unlinked_telegram_never_imported_to_guest_or_other_user(self):
        foreign_user = "telegram:99999"
        self._chat(foreign_user, "someone_else", "Чужой чат")
        self.assertFalse(self.login.reconcile_linked_telegram_history(self.owner)["linked"])
        self._link()
        self.login.reconcile_linked_telegram_history(self.owner)
        self.assertEqual(self._session_owner("someone_else"), foreign_user)

    def test_legacy_memory_and_preferences_migrate_with_messages(self):
        self._link()
        with self.identity.storage.connection() as conn:
            conn.execute(
                "INSERT INTO user_preferences(owner_id,key,value_json,updated_at) "
                "VALUES(?,?,?,?)", ("telegram:12345", "language", '"ru"', utcnow()),
            )
        self.login.reconcile_linked_telegram_history(self.owner)
        with self.identity.storage.connection() as conn:
            row = conn.execute(
                "SELECT value_json FROM user_preferences WHERE owner_id=? AND key=?",
                (self.owner, "language"),
            ).fetchone()
        self.assertEqual(row["value_json"], '"ru"')


if __name__ == "__main__":
    unittest.main()
