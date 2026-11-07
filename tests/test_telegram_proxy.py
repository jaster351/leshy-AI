"""Telegram proxy: authentication, persistence, forced routing and secret hygiene."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib import request

from leshy.telegram_proxy import TelegramProxyTransport, _ForceHTTPProxy


class TelegramProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.proxy=TelegramProxyTransport(Path(self.temp.name))
        self.request=request.Request("https://api.telegram.org/botDUMMY/getMe")

    def test_disabled_by_default_keeps_existing_network_path(self):
        self.assertFalse(self.proxy.status()["enabled"])
        fake=Mock()
        with patch("leshy.telegram_proxy.request.urlopen", return_value=fake) as urlopen:
            self.assertIs(self.proxy.open(self.request, timeout=3), fake)
        urlopen.assert_called_once()

    def test_http_without_credentials_and_status_mask(self):
        status=self.proxy.configure({"enabled":True,"scheme":"http","host":"proxy.local","port":3128,"auth":False})
        self.assertEqual(status["host"], "proxy.local")
        self.assertFalse(status["has_password"])
        opener=self.proxy._opener(self.proxy._read())
        handler=next(h for h in opener.handlers if isinstance(h,_ForceHTTPProxy))
        req=request.Request("https://api.telegram.org/botDUMMY/getMe")
        handler.proxy_open(req, "http://proxy.local:3128", "https")
        self.assertEqual(req.host,"proxy.local:3128")
        self.assertNotIn("Proxy-authorization", req.headers)

    def test_http_with_credentials_and_never_publish_password(self):
        values={"enabled":True,"scheme":"http","host":"proxy.local","port":3128,
                "auth":True,"username":"operator","password":"very-private-password"}
        status=self.proxy.configure(values)
        self.assertTrue(status["has_password"])
        self.assertNotIn("password", status)
        self.assertEqual(self.proxy._read()["password"], "very-private-password")
        self.assertNotIn("very-private-password", str(status))
        # Blank password on same host leaves encrypted password intact.
        copy=dict(values,password="")
        self.assertTrue(self.proxy.configure(copy)["has_password"])
        self.assertEqual(self.proxy._read()["password"], "very-private-password")
        req=request.Request("https://api.telegram.org/botDUMMY/getMe")
        handler=_ForceHTTPProxy({"https":"http://operator:very-private-password@proxy.local:3128"})
        handler.proxy_open(req,"http://operator:very-private-password@proxy.local:3128","https")
        self.assertTrue(req.headers["Proxy-authorization"].startswith("Basic "))

    def test_secret_not_reused_for_different_host(self):
        self.proxy.configure({"enabled":True,"scheme":"http","host":"one.example","port":80,
                              "auth":True,"username":"me","password":"secret"})
        with self.assertRaises(ValueError):
            self.proxy.configure({"enabled":True,"scheme":"http","host":"two.example","port":80,
                                  "auth":True,"username":"me","password":""})

    def test_invalid_values_rejected(self):
        for values in ({"enabled":True,"scheme":"ftp","host":"x","port":80},
                       {"enabled":True,"scheme":"http","host":"user@host","port":80},
                       {"enabled":True,"scheme":"http","host":"x","port":70000},
                       {"enabled":True,"scheme":"socks5","host":"x","port":1080,"auth":True}):
            with self.subTest(values=values),self.assertRaises(ValueError):
                self.proxy.configure(values)

    def test_proxy_only_ever_requests_telegram_https(self):
        self.proxy.configure({"enabled":True,"scheme":"http","host":"proxy.local","port":3128})
        with self.assertRaises(ValueError):
            self.proxy.open(request.Request("https://example.com/"),timeout=2)

    def test_runtime_change_takes_effect_without_restart(self):
        self.proxy.configure({"enabled":True,"scheme":"http","host":"proxy.local","port":3128})
        fake=Mock()
        opener=Mock()
        opener.open.return_value=fake
        with patch.object(self.proxy,"_opener",return_value=opener):
            self.assertIs(self.proxy.open(self.request,timeout=4),fake)
        self.proxy.configure({"enabled":False,"scheme":"http","host":"","port":0})
        with patch("leshy.telegram_proxy.request.urlopen",return_value=fake) as direct:
            self.assertIs(self.proxy.open(self.request,timeout=4),fake)
        direct.assert_called_once()
