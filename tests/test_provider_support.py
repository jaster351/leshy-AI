import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from leshy.provider_support import (
    FACTS, STYLE, is_support_draft_request, support_draft_context,
)


class GenericSupportTests(unittest.TestCase):
    def test_trigger(self):
        self.assertTrue(is_support_draft_request("/support Вопрос клиента"))
        self.assertFalse(is_support_draft_request("1.1.1.1"))
        self.assertFalse(is_support_draft_request("Как работает SSH?"))

    def test_style(self):
        text = support_draft_context("/support Есть ли гарантированный канал?")
        self.assertIn("РОВНО ОДИН", text)
        self.assertIn("на «Вы»", text)

    def test_local_encrypted_profile(self):
        from cryptography.fernet import Fernet
        with tempfile.TemporaryDirectory() as base:
            key = Fernet.generate_key()
            folder = Path(base) / "private"
            folder.mkdir()
            data = {"facts":[{"cues":["канал"],"text":"Проверить договор."}]}
            (folder / "support_profile.enc").write_bytes(
                Fernet(key).encrypt(json.dumps(data).encode())
            )
            with patch.dict(os.environ, {"LESHY_SUPPORT_PROFILE_KEY":key.decode()}):
                self.assertIn("Проверить договор.", support_draft_context(
                    "/support Уточнить канал", brain_dir=base))

    def test_no_branded_or_internal_data_in_public_source(self):
        import inspect
        import leshy.provider_support as p
        source = inspect.getsource(p).lower()
        self.assertNotIn("client_ip_address=", source)
        self.assertNotIn("api_secret=", source)
