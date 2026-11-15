"""Regression: Telegram photo can load via the signed-in browser cookie.

The image endpoint is a normal <img> request: browsers cannot attach the
API Authorization bearer header there. It MUST still require a Web session.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from leshy.owner_identity import OwnerIdentityService
from leshy.storage import Storage
from leshy.telegram_browser_login import TelegramBrowserLogin


class TelegramAvatarRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        brain = Path(self.temp.name)
        self.identity = OwnerIdentityService(Storage(brain / "db.sqlite"), brain)
        self.owner, self.cookie, self.session = self.identity.ensure_web_session("", "web:local")
        self.login = TelegramBrowserLogin(self.identity)
        self.sender = {"id": 12345, "first_name": "Тест"}
        started = self.login.begin(self.session)
        button = self.login.prepare_telegram_confirmation(started["token"], "12345")
        self.login.finish(button["callback"], "12345", self.sender, True)

    def test_missing_photo_is_pending_until_bot_returns_image(self):
        profile = self.login.profile(self.owner, {})
        self.assertTrue(profile["photo_pending"])
        self.assertFalse(profile["avatar_url"])
        class Bot:
            calls = 0
            def _api(self, method, payload, timeout=8):
                self.calls += 1
                return {"photos": [[{"file_id": "actual-photo-file"}]]}
        bot = Bot()
        with patch("leshy.telegram_browser_login.threading.Thread") as worker:
            self.assertTrue(self.login.ensure_photo_refresh(bot, self.owner))
            self.assertFalse(self.login.ensure_photo_refresh(bot, self.owner))
            self.assertEqual(worker.call_count, 1)
            # Simulate the queued refresh without contacting Telegram.
            kwargs = worker.call_args.kwargs
            kwargs["target"](*kwargs["args"])
        profile = self.login.profile(self.owner, {})
        self.assertFalse(profile["photo_pending"])
        self.assertEqual(profile["avatar_url"], "/v1/identity/telegram/avatar")
        self.assertEqual(bot.calls, 1)

    def test_never_fetch_photos_for_unlinked_guests(self):
        other, _, _ = self.identity.ensure_web_session("", "web:local")
        with patch("leshy.telegram_browser_login.threading.Thread") as worker:
            self.assertFalse(self.login.ensure_photo_refresh(None, other))
            worker.assert_not_called()

    def test_cookie_only_avatar_endpoint_not_guarded_by_bearer(self):
        root = Path(__file__).resolve().parents[1]
        module = (root / "src/leshy/web_server_impl17.py").read_text(encoding="utf-8")
        start = module.index('if route == "/v1/identity/telegram/avatar":')
        end = module.index('return super().do_GET()', start)
        route = module[start:end]
        self.assertIn("self._existing_web_owner()", route)
        self.assertNotIn("if not self._authorized()", route)
        self.assertIn("self.send_header(\"Vary\", \"Cookie\")", route)


if __name__ == "__main__":
    unittest.main()
