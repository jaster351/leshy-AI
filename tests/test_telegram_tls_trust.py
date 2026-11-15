from __future__ import annotations

import os
import ssl
from unittest import TestCase
from unittest.mock import patch

from leshy.telegram_proxy import _telegram_tls_context


class TelegramTLSContextTests(TestCase):
    def test_tls_verification_remains_required(self):
        with patch.dict(os.environ, {"LESHY_TELEGRAM_CA_BUNDLE": ""}):
            context = _telegram_tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_missing_custom_ca_is_an_error_not_automatic_bypass(self):
        with patch.dict(os.environ, {"LESHY_TELEGRAM_CA_BUNDLE": "Z:/missing/telegram-root-ca.pem"}):
            with self.assertRaises(FileNotFoundError):
                _telegram_tls_context()
