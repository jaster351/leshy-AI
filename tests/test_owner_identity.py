from pathlib import Path
import tempfile
import unittest

from leshy.owner_identity import OwnerIdentityService
from leshy.storage import Storage, utcnow


class OwnerIdentityTests(unittest.TestCase):
    def make_service(self):
        temp = tempfile.TemporaryDirectory()
        brain = Path(temp.name) / "LeshyBrain"
        storage = Storage(brain / "leshy.db")
        service = OwnerIdentityService(storage, brain)
        self.addCleanup(temp.cleanup)
        return storage, service, brain

    def test_unlinked_telegram_users_are_isolated_without_web_auth(self):
        _storage, service, _brain = self.make_service()
        self.assertEqual(service.resolve_telegram("1001"), "telegram:1001")
        self.assertEqual(service.resolve_telegram("1002"), "telegram:1002")
        self.assertNotEqual(service.resolve_telegram("1001"), service.resolve_telegram("1002"))

    def test_first_web_profile_preserves_legacy_web_local_history(self):
        storage, service, _brain = self.make_service()
        now = utcnow()
        with storage.connection() as conn:
            conn.execute(
                "INSERT INTO sessions(id,owner_id,title,title_source,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("legacy-session", "web:local", "Старый чат", "auto", now, now),
            )
        owner_id, cookie, _session_hash = service.ensure_web_session("", "web:local")
        self.assertTrue(cookie)
        self.assertNotEqual(owner_id, "web:local")
        with storage.connection() as conn:
            row = conn.execute("SELECT owner_id FROM sessions WHERE id='legacy-session'").fetchone()
        self.assertEqual(str(row["owner_id"]), owner_id)

    def test_link_migrates_telegram_history_into_confirmed_owner(self):
        storage, service, _brain = self.make_service()
        owner_id, _cookie, web_session_hash = service.ensure_web_session("", "web:local")
        now = utcnow()
        with storage.connection() as conn:
            conn.execute(
                "INSERT INTO sessions(id,owner_id,title,title_source,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("telegram:500:500", "telegram:500", "Telegram чат", "auto", now, now),
            )
        link = service.create_telegram_link(owner_id, web_session_hash)
        result = service.consume_telegram_link(
            link["code"],
            "500",
            username="mikhail",
            first_name="Михаил",
        )
        self.assertEqual(result["mode"], "linked")
        self.assertEqual(service.resolve_telegram("500"), owner_id)
        self.assertEqual(service.resolve_telegram("501"), "telegram:501")
        with storage.connection() as conn:
            row = conn.execute("SELECT owner_id FROM sessions WHERE id='telegram:500:500'").fetchone()
        self.assertEqual(str(row["owner_id"]), owner_id)

    def test_existing_telegram_identity_logs_new_browser_into_same_owner(self):
        _storage, service, _brain = self.make_service()
        owner_a, _cookie_a, hash_a = service.ensure_web_session("", "web:local")
        link_a = service.create_telegram_link(owner_a, hash_a)
        service.consume_telegram_link(link_a["code"], "700", username="owner")

        owner_b, cookie_b, hash_b = service.ensure_web_session("", "web:local")
        self.assertNotEqual(owner_a, owner_b)
        link_b = service.create_telegram_link(owner_b, hash_b)
        result = service.consume_telegram_link(link_b["code"], "700", username="owner")
        self.assertEqual(result["mode"], "login")
        self.assertEqual(result["owner_id"], owner_a)
        self.assertEqual(service.owner_for_web_cookie(cookie_b), owner_a)

    def test_link_code_is_one_time(self):
        _storage, service, _brain = self.make_service()
        owner_id, _cookie, web_session_hash = service.ensure_web_session("", "web:local")
        link = service.create_telegram_link(owner_id, web_session_hash)
        service.consume_telegram_link(link["code"], "900")
        with self.assertRaises(ValueError):
            service.consume_telegram_link(link["code"], "900")


if __name__ == "__main__":
    unittest.main()
