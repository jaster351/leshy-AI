"""Telegram proxy: authentication, persistence, forced routing and secret hygiene."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib import request

from leshy.telegram_proxy import TelegramProxyTransport, _ForceHTTPProxy, _NoRedirects


class TelegramProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.proxy=TelegramProxyTransport(Path(self.temp.name))
        self.request=request.Request("https://api.telegram.org/botDUMMY/getMe")

    def test_disabled_by_default_keeps_existing_network_path(self):
        self.assertFalse(self.proxy.status()["enabled"])
        fake=Mock()
        with patch("leshy.telegram_proxy.request.build_opener") as factory:
            factory.return_value.open.return_value = fake
            self.assertIs(self.proxy.open(self.request, timeout=3), fake)
        self.assertTrue(any(isinstance(h, _NoRedirects) for h in factory.call_args.args))

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


    def test_bot_api_uses_proxy_transport(self):
        import io
        from types import SimpleNamespace
        from leshy.telegram import TelegramBridge
        with tempfile.TemporaryDirectory() as temp:
            bridge=TelegramBridge(SimpleNamespace(),Path(temp))
            bridge._token=lambda:"dummy-token"
            class Response:
                def __enter__(self): return self
                def __exit__(self,*args): return False
                def read(self): return b'{"ok":true,"result":{"id":123}}'
            bridge.proxy.open=Mock(return_value=Response())
            result=bridge._api("getMe",timeout=7)
            self.assertEqual(result["id"],123)
            args=bridge.proxy.open.call_args
            self.assertEqual(args.kwargs["timeout"],7)
            self.assertTrue(args.args[0].full_url.startswith("https://api.telegram.org/bot"))

    def test_voice_download_uses_same_proxy(self):
        from types import SimpleNamespace
        from leshy.telegram import TelegramBridge
        with tempfile.TemporaryDirectory() as temp:
            bridge=TelegramBridge(SimpleNamespace(),Path(temp))
            bridge._token=lambda:"dummy-token"
            bridge._api=Mock(return_value={"file_path":"voice/record.ogg","file_size":8})
            class Response:
                headers={}
                def __enter__(self): return self
                def __exit__(self,*args): return False
                def read(self,n): return b"OggSdata"
            bridge.proxy.open=Mock(return_value=Response())
            audio=bridge._download_telegram_voice({"file_id":"VOICE","file_size":8,"duration":2})
            self.assertEqual(audio,b"OggSdata")
            req=bridge.proxy.open.call_args.args[0]
            self.assertIn("/file/botdummy-token/voice/record.ogg",req.full_url)

    def test_telegram_image_download_uses_same_proxy(self):
        from types import SimpleNamespace
        from leshy.telegram_vision import LocalTelegramVision
        with tempfile.TemporaryDirectory() as temp:
            bridge=SimpleNamespace()
            bridge._api=Mock(return_value={"file_path":"photos/p.jpg","file_size":5})
            bridge._token=lambda:"dummy-token"
            class Response:
                def __enter__(self): return self
                def __exit__(self,*args): return False
                def read(self,n): return b"\xff\xd8\xffDATA"
            bridge.proxy=SimpleNamespace(open=Mock(return_value=Response()))
            data=LocalTelegramVision(bridge)._download({"file_id":"photo","file_size":5})
            self.assertTrue(data.startswith(b"\xff\xd8\xff"))
            self.assertIn("/file/botdummy-token/photos/p.jpg",bridge.proxy.open.call_args.args[0].full_url)

    def test_invalid_values_rejected(self):
        for values in ({"enabled":True,"scheme":"ftp","host":"x","port":80},
                       {"enabled":True,"scheme":"http","host":"user@host","port":80},
                       {"enabled":True,"scheme":"http","host":"x","port":70000},
                       {"enabled":True,"scheme":"socks5","host":"x","port":1080,"auth":True}):
            with self.subTest(values=values),self.assertRaises(ValueError):
                self.proxy.configure(values)

    def test_corrupt_config_does_not_block_settings_repair(self):
        self.proxy.path.parent.mkdir(parents=True,exist_ok=True)
        self.proxy.path.write_bytes(b"plain:not-json")
        self.assertIn("error",self.proxy.status())
        saved=self.proxy.configure({
            "enabled":True,"scheme":"http","host":"proxy.local","port":3128
        })
        self.assertTrue(saved["enabled"])
        self.assertNotIn("error",self.proxy.status())

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
        with patch("leshy.telegram_proxy.request.build_opener") as factory:
            factory.return_value.open.return_value=fake
            self.assertIs(self.proxy.open(self.request,timeout=4),fake)
        self.assertTrue(any(isinstance(h,_NoRedirects) for h in factory.call_args.args))

    def test_redirect_rejected_without_leaking_bot_token(self):
        token = "SECRET-BOT-TOKEN"
        req = request.Request(f"https://api.telegram.org/bot{token}/getMe")
        with self.assertRaises(ConnectionError) as caught:
            _NoRedirects().redirect_request(req, None, 302, "Found", {"Location": "https://example.com/"}, "https://example.com/")
        self.assertNotIn(token, str(caught.exception))

    def test_system_proxy_policy_preserved_with_redirect_guard(self):
        with patch("leshy.telegram_proxy.request.build_opener") as factory:
            factory.return_value.open.return_value = Mock()
            self.proxy.open(self.request, timeout=2)
        handlers = factory.call_args.args
        self.assertTrue(any(isinstance(h, request.HTTPSHandler) for h in handlers))
        self.assertTrue(any(isinstance(h, _NoRedirects) for h in handlers))
        self.assertFalse(any(isinstance(h, request.ProxyHandler) for h in handlers))

    def test_http_302_is_blocked_by_real_opener_pipeline(self):
        """HTTPErrorProcessor должен передать 302 запретителю перенаправлений."""
        import io
        from email.message import Message
        from urllib.response import addinfourl

        token = "SECRET-BOT-TOKEN"
        target = request.Request(f"https://api.telegram.org/bot{token}/getMe")

        class FakeHTTPS(request.HTTPSHandler):
            def https_open(self, req):
                headers = Message()
                headers["Location"] = "https://example.com/collect"
                response = addinfourl(io.BytesIO(b""), headers, req.full_url, 302)
                response.code = 302
                response.msg = "Found"
                return response

        opener = request.build_opener(request.ProxyHandler({}), FakeHTTPS(), _NoRedirects())
        with self.assertRaises(ConnectionError) as caught:
            opener.open(target, timeout=2)
        self.assertNotIn(token, str(caught.exception))
