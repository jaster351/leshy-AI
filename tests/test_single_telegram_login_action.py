"""Only one Telegram sign-in action is presented in Leshy Web settings."""
from pathlib import Path
import unittest


class SingleTelegramSignInUiTests(unittest.TestCase):
    def test_one_automatic_telegram_signin_button(self):
        source = (Path(__file__).resolve().parents[1] / "web" / "owner-identity-panel.js").read_text(encoding="utf-8")
        self.assertEqual(source.count('id="ownerIdentityTelegramLogin"'), 1)
        self.assertIn("document.getElementById('ownerIdentityTelegramLogin')?.addEventListener('click',startTelegramLogin)", source)
        self.assertIn("data.telegram_login_button_available", source)
        self.assertIn("linked?"+chr(96), source)
        self.assertIn("id=\"ownerIdentityUnlink\"", source)
        self.assertNotIn("ownerIdentityLinkBox", source)
        self.assertNotIn("ownerIdentityLink')", source)
        self.assertNotIn("Привязка Telegram (резервный способ)", source)

    def test_backend_keeps_legacy_pairing_capability(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "leshy" / "owner_identity.py").read_text(encoding="utf-8")
        self.assertIn("def create_telegram_link(", source)
        self.assertIn("def consume_telegram_link(", source)
        login = (Path(__file__).resolve().parents[1] / "src" / "leshy" / "telegram_browser_login.py").read_text(encoding="utf-8")
        self.assertIn('mode = "login" if existing else "linked"', login)


if __name__ == "__main__":
    unittest.main()
