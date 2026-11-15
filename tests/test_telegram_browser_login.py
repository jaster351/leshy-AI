"""Telegram bot approved Web login must not depend on a public domain."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from leshy.owner_identity import OwnerIdentityService
from leshy.storage import Storage, utcnow
from leshy.telegram_browser_login import TelegramBrowserLogin


class TelegramBrowserLoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        directory = Path(self.tmp.name)
        self.identity = OwnerIdentityService(Storage(directory / "db.sqlite3"), directory)
        self.login = TelegramBrowserLogin(self.identity)
        self.guest, self.guest_cookie, self.guest_session = self.identity.ensure_web_session("", "web:local")
        self.actor = {"id": 12345, "first_name": "Иван", "last_name": "Тестовый",
                      "username": "example_tg", "language_code": "ru"}

    def _add_guest_chat(self, owner, sid):
        now = utcnow()
        with self.identity.storage.connection() as conn:
            conn.execute("INSERT INTO sessions(id,owner_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                         (sid, owner, "Гостевой диалог", now, now))
            conn.execute("INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                         (sid, "user", "Текущая гостевая переписка", now))

    def _start_button(self, session=None, uid="12345"):
        requested = self.login.begin(session or self.guest_session)
        prompt = self.login.prepare_telegram_confirmation(requested["token"], uid)
        self.assertEqual(prompt["short_code"], requested["short_code"])
        return requested, prompt

    def test_new_user_sign_in_preserves_guest_history(self):
        self._add_guest_chat(self.guest, "guest-conversation")
        started, prompt = self._start_button()
        result = self.login.finish(prompt["callback"], "12345", self.actor, True)
        self.assertEqual(result["mode"], "linked")
        self.assertEqual(result["owner_id"], self.guest)
        self.assertEqual(self.login.status(self.guest_session)["status"], "confirmed")
        self.assertEqual(self.identity.owner_for_web_cookie(self.guest_cookie), self.guest)
        with self.identity.storage.connection() as conn:
            row = conn.execute("SELECT owner_id FROM sessions WHERE id='guest-conversation'").fetchone()
        self.assertEqual(row["owner_id"], self.guest)
        profile = self.login.profile(self.guest, {})
        self.assertEqual(profile["provider"], "telegram")
        self.assertEqual(profile["name"], "Иван Тестовый")
        self.assertEqual(profile["username"], "example_tg")
        self.assertEqual(profile["language_code"], "ru")
        self.assertNotIn("token", str(self.identity.status(self.guest)))

    def test_existing_telegram_login_merges_new_browser_guest_chat(self):
        begun = self.login.begin(self.guest_session)
        first_button = self.login.prepare_telegram_confirmation(begun["token"], "12345")
        self.login.finish(first_button["callback"], "12345", self.actor, True)
        second_owner, second_cookie, second_session = self.identity.ensure_web_session("", "web:local")
        self._add_guest_chat(second_owner, "second-guest-conversation")
        started = self.login.begin(second_session)
        next_button = self.login.prepare_telegram_confirmation(started["token"], "12345")
        done = self.login.finish(next_button["callback"], "12345", self.actor, True)
        self.assertEqual(done["mode"], "login")
        self.assertEqual(done["owner_id"], self.guest)
        self.assertEqual(self.identity.owner_for_web_cookie(second_cookie), self.guest)
        with self.identity.storage.connection() as conn:
            row = conn.execute("SELECT owner_id FROM sessions WHERE id='second-guest-conversation'").fetchone()
        self.assertEqual(row["owner_id"], self.guest)

    def test_wrong_telegram_sender_and_group_replay_fail(self):
        started, prompt = self._start_button()
        with self.assertRaises(ValueError):
            self.login.finish(prompt["callback"], "54321", {"id": 54321}, True)
        with self.assertRaises(PermissionError):
            self.login.prepare_telegram_confirmation(started["token"], "54321")
        self.assertEqual(self.login.status(self.guest_session)["status"], "pending")

    def test_denial_and_reuse_are_not_logins(self):
        started, prompt = self._start_button()
        result = self.login.finish(prompt["callback"], "12345", self.actor, False)
        self.assertEqual(result["mode"], "denied")
        with self.assertRaises(ValueError):
            self.login.finish(prompt["callback"], "12345", self.actor, True)
        self.assertEqual(self.login.status(self.guest_session)["status"], "denied")
        self.assertEqual(self.identity.resolve_telegram("12345"), "telegram:12345")

    def test_expired_and_superseded_tokens_rejected(self):
        started, prompt = self._start_button()
        replacement = self.login.begin(self.guest_session)
        with self.assertRaises(ValueError):
            self.login.finish(prompt["callback"], "12345", self.actor, True)
        with self.assertRaises(ValueError):
            self.login.prepare_telegram_confirmation(started["token"], "12345")
        with self.identity.storage.connection() as conn:
            conn.execute("UPDATE telegram_browser_login SET expires_at=0 WHERE status='pending'")
        with self.assertRaises(ValueError):
            self.login.prepare_telegram_confirmation(replacement["token"], "12345")

    def test_account_switch_does_not_merge_another_users_chats(self):
        owner2, cookie2, session2 = self.identity.ensure_web_session("", "web:local")
        self._add_guest_chat(self.guest, "account-a-dialogue")
        self._add_guest_chat(owner2, "account-b-dialogue")
        a = self.login.begin(self.guest_session)
        ac = self.login.prepare_telegram_confirmation(a["token"], "12345")
        self.login.finish(ac["callback"], "12345", self.actor, True)
        b = self.login.begin(session2)
        bc = self.login.prepare_telegram_confirmation(b["token"], "8888")
        self.login.finish(bc["callback"], "8888", {
            "id": 8888, "first_name": "Другой",
        }, True)
        # A shared browser signs in with the second Telegram account.
        switch = self.login.begin(self.guest_session)
        sc = self.login.prepare_telegram_confirmation(switch["token"], "8888")
        result = self.login.finish(sc["callback"], "8888", {
            "id": 8888, "first_name": "Другой",
        }, True)
        self.assertEqual(result["mode"], "login")
        self.assertEqual(self.identity.owner_for_web_cookie(self.guest_cookie), owner2)
        with self.identity.storage.connection() as conn:
            first = conn.execute("SELECT owner_id FROM sessions WHERE id='account-a-dialogue'").fetchone()
            second = conn.execute("SELECT owner_id FROM sessions WHERE id='account-b-dialogue'").fetchone()
        self.assertEqual(first["owner_id"], self.guest)
        self.assertEqual(second["owner_id"], owner2)

    def test_telegram_callback_sender_identity_must_match(self):
        _, confirmation = self._start_button()
        with self.assertRaises(PermissionError):
            self.login.finish(confirmation["callback"], "12345", {"id": 5555}, True)
        self.assertEqual(self.login.status(self.guest_session)["status"], "pending")

    def test_profile_photo_has_no_external_url_or_token(self):
        started, prompt = self._start_button()
        self.login.finish(prompt["callback"], "12345", self.actor, True)

        class FakeBot:
            def _api(self, method, payload, timeout=0):
                self.method = method
                return {"photos": [[{"file_id": "small"}, {"file_id": "large"}]]}

        self.login.sync_verified_telegram_profile(FakeBot(), "12345")
        profile = self.login.profile(self.guest, {})
        self.assertEqual(profile["avatar_url"], "/v1/identity/telegram/avatar")
        self.assertNotIn("api.telegram.org", str(profile))
        self.assertNotIn("large", str(profile))


if __name__ == "__main__":
    unittest.main()
