from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch
from urllib import request

from leshy.accelerator_runtime import LlamaServerLLM, _open_local_llama


class LocalLlamaProxyBypassTests(TestCase):
    def test_health_and_completion_open_only_direct_loopback(self):
        opener = MagicMock()
        with patch("leshy.accelerator_runtime.request.build_opener", return_value=opener) as create:
            for url in (
                "http://127.0.0.1:45789/health",
                request.Request("http://127.0.0.1:45789/v1/chat/completions", data=b"{}"),
            ):
                _open_local_llama(url, timeout=2.0)
        self.assertEqual(create.call_count, 2)
        for call in create.call_args_list:
            proxy = call.args[0]
            self.assertIsInstance(proxy, request.ProxyHandler)
            self.assertEqual(proxy.proxies, {})
        self.assertEqual(opener.open.call_count, 2)

    def test_external_and_nonlocal_hosts_are_never_accepted(self):
        bad_urls = (
            "https://api.telegram.org/bot123/getMe",
            "https://github.com/",
            "http://localhost:8080/health",
            "http://192.168.0.12:8080/health",
            "http://127.0.0.2:8080/health",
            "http://127.0.0.1/health",
            "http://127.0.0.1:8080@evil.example.com/health",
        )
        with patch("leshy.accelerator_runtime.request.build_opener") as make:
            for url in bad_urls:
                with self.subTest(url=url):
                    with self.assertRaises(ValueError):
                        _open_local_llama(url, timeout=0.1)
        make.assert_not_called()

    def test_health_keeps_sanitized_failure_reason(self):
        provider = LlamaServerLLM.__new__(LlamaServerLLM)
        with patch("leshy.accelerator_runtime._open_local_llama", side_effect=ConnectionRefusedError("private")):
            self.assertFalse(provider._is_healthy("http://127.0.0.1:45789"))
        self.assertEqual(provider._last_health_error, "ConnectionRefusedError")

    def test_health_200_marks_ready(self):
        provider = LlamaServerLLM.__new__(LlamaServerLLM)
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch("leshy.accelerator_runtime._open_local_llama", return_value=response):
            self.assertTrue(provider._is_healthy("http://127.0.0.1:45789"))
        self.assertEqual(provider._last_health_error, "")
