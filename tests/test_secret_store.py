from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from leshy.secret_store import load_secret, save_secret


class SecretStoreTests(unittest.TestCase):
    def test_secret_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "telegram_token.dat"
            save_secret(path, "123456:secret-token")
            self.assertEqual(load_secret(path), "123456:secret-token")
            raw = path.read_bytes()
            self.assertNotIn(b"123456:secret-token", raw)

    def test_missing_secret_is_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(load_secret(Path(temp) / "missing.dat"), "")


if __name__ == "__main__":
    unittest.main()
