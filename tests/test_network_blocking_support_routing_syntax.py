"""Regression for syntax and support-intent bypass in TCP diagnostic routing."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from leshy.network_blocking_runtime import _matches


class NetworkBlockingSupportRoutingSyntaxTests(unittest.TestCase):
    def test_ticket_drafting_is_not_a_request_to_scan_quoted_ip(self):
        for text in (
            "Ответь клиенту: на сервере 1.1.1.1 не работает SSH",
            "Напиши ответ клиенту по адресу 1.1.1.1",
            "/support сообщение клиента: 1.1.1.1 недоступен",
            "Вопрос клиента: заблокирован TCP/22?",
        ):
            with self.subTest(text=text):
                with patch("leshy.network_blocking_runtime._enabled", return_value=True):
                    self.assertFalse(_matches(object(), text))


if __name__ == "__main__":
    unittest.main()
