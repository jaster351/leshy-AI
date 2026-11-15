"""A Web guest has a durable owner UUID, separate history and memory."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from leshy.owner_identity import OwnerIdentityService
from leshy.storage import Storage, utcnow


class GuestWebIdentityIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        brain = Path(self.tmp.name)
        self.store = Storage(brain / "leshy.db")
        self.identity = OwnerIdentityService(self.store, brain)

    def _record_chat(self, owner, session_id, text):
        with self.store.connection() as conn:
            now = utcnow()
            conn.execute(
                "INSERT INTO sessions(id,owner_id,title,created_at,updated_at)"
                "VALUES(?,?,?,?,?)", (session_id, owner, text, now, now),
            )
            conn.execute(
                "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                (session_id, "user", text, now),
            )

    def test_two_unsigned_browsers_get_distinct_profiles(self):
        a, cookie_a, _ = self.identity.ensure_web_session("", "web:local")
        b, cookie_b, _ = self.identity.ensure_web_session("", "web:local")
        self.assertNotEqual(a, b)
        self.assertNotEqual(cookie_a, cookie_b)
        self._record_chat(a, "a-guest-chat", "Вопрос гостя А")
        self._record_chat(b, "b-guest-chat", "Вопрос гостя Б")
        with self.store.connection() as conn:
            a_history = conn.execute("SELECT id FROM sessions WHERE owner_id=?", (a,)).fetchall()
            b_history = conn.execute("SELECT id FROM sessions WHERE owner_id=?", (b,)).fetchall()
        self.assertEqual([v["id"] for v in a_history], ["a-guest-chat"])
        self.assertEqual([v["id"] for v in b_history], ["b-guest-chat"])

    def test_guest_returns_after_reload_without_account(self):
        a, cookie, _ = self.identity.ensure_web_session("", "web:local")
        self._record_chat(a, "persistent-guest", "Привет, Леший")
        owner_after, fresh_cookie, _ = self.identity.ensure_web_session(cookie)
        self.assertEqual(owner_after, a)
        self.assertIsNone(fresh_cookie)
        self.assertEqual(self.identity.owner_for_web_cookie(cookie), a)

    def test_browser_does_not_gain_access_using_claimed_owner_id(self):
        a, cookie_a, _ = self.identity.ensure_web_session("", "web:local")
        b, cookie_b, _ = self.identity.ensure_web_session("", a)
        self.assertNotEqual(a, b)
        self.assertEqual(self.identity.owner_for_web_cookie(cookie_a), a)
        self.assertEqual(self.identity.owner_for_web_cookie(cookie_b), b)


if __name__ == "__main__":
    unittest.main()
