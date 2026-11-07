"""Telegram media: no image via text-only LLM, only local vision endpoint."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from leshy.telegram_vision import (
    LocalTelegramVision, VisionNotConfigured, VisionUnavailable, image_attachment,
)
from leshy.telegram_fast import FastTelegramBridge


class TelegramVisionTests(unittest.TestCase):
    def message(self, caption=""):
        return {
            "chat": {"id": 555, "type": "private"},
            "from": {"id": 987},
            "message_id": 100,
            "photo": [{"file_id": "photo-low", "file_size": 200},
                      {"file_id": "photo-big", "file_size": 400}],
            "caption": caption,
        }

    def test_image_without_caption_is_detected(self):
        self.assertEqual(image_attachment(self.message())["file_id"], "photo-big")
        self.assertIsNone(image_attachment({"document": {"mime_type": "application/pdf"}}))

    def test_largest_photo_under_limit_is_selected(self):
        message = self.message()
        message["photo"].append({"file_id": "oversize", "file_size": 12 * 1024 * 1024})
        self.assertEqual(image_attachment(message)["file_id"], "photo-big")

    def test_missing_vision_model_returns_explicit_reason(self):
        bridge = Mock()
        with patch.dict(os.environ, {"LESHY_VISION_ENDPOINT": "", "LESHY_VISION_MODEL": ""}):
            with self.assertRaises(VisionNotConfigured):
                LocalTelegramVision(bridge).describe(self.message())
        bridge._api.assert_not_called()

    def test_external_endpoint_is_forbidden_before_downloading(self):
        bridge = Mock()
        with patch.dict(os.environ, {
            "LESHY_VISION_ENDPOINT": "https://example.org/v1/chat/completions",
            "LESHY_VISION_MODEL": "vision",
        }):
            with self.assertRaises(VisionNotConfigured):
                LocalTelegramVision(bridge).describe(self.message())
        bridge._api.assert_not_called()

    def test_complete_vision_request_uses_bytes_and_getfile(self):
        bridge = Mock()
        bridge._api.return_value = {"file_size": 4, "file_path": "photos/file_1.jpg"}
        bridge._token.return_value = "secret-should-not-appear"
        payloads = []
        class Response:
            def __init__(self, data):
                self.data = data
            def read(self, n=-1):
                return self.data
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
        class Opener:
            def open(self, req, timeout):
                if req.full_url.startswith("https://api.telegram.org/file/bot"):
                    self.assert_no_proxy = True
                    return Response(b"\xff\xd8\xffDATA")
                payloads.append(json.loads(req.data))
                return Response(b'{"choices":[{"message":{"content":"На фото тест"}}]}')
        with patch.dict(os.environ, {
            "LESHY_VISION_ENDPOINT": "http://127.0.0.1:8080/v1/chat/completions",
            "LESHY_VISION_MODEL": "local-vision",
        }), patch(
            "leshy.telegram_vision.request.build_opener", return_value=Opener()
        ) as opener_factory:
            answer = LocalTelegramVision(bridge).describe(self.message("Что видишь?"))
        self.assertIn("На фото", answer)
        self.assertEqual(payloads[0]["model"], "local-vision")
        from urllib.request import ProxyHandler
        self.assertTrue(any(
            isinstance(item, ProxyHandler) and not item.proxies
            for item in opener_factory.call_args.args
        ))
        self.assertEqual(payloads[0]["messages"][0]["content"][0]["text"], "Что видишь?")
        self.assertTrue(payloads[0]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        bridge._api.assert_called_once_with("getFile", {"file_id": "photo-big"}, timeout=12)

    def test_download_opener_rejects_proxy_and_redirects(self):
        from urllib.request import ProxyHandler
        from leshy.telegram_vision import _RejectRedirects
        bridge = Mock()
        bridge._api.return_value = {"file_size": 4, "file_path": "photos/file.jpg"}
        bridge._token.return_value = "secret"
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, *_): return b"\xff\xd8\xffDATA"
        opener = Mock()
        opener.open.return_value = Response()
        with patch("leshy.telegram_vision.request.build_opener", return_value=opener) as build:
            self.assertTrue(LocalTelegramVision(bridge)._download({"file_id": "file"}).startswith(b"\\xff\\xd8\\xff"))
        handlers = build.call_args.args
        self.assertTrue(any(isinstance(h, ProxyHandler) and not h.proxies for h in handlers))
        self.assertTrue(any(isinstance(h, _RejectRedirects) for h in handlers))

    def test_reply_to_photo_uses_quoted_image_and_current_question(self):
        bridge = FastTelegramBridge.__new__(FastTelegramBridge)
        bridge._bot_id = 123
        bridge._bot_username = "leshy_bot"
        bridge._social = Mock()
        bridge._social.addressed_to_leshy.return_value = (True, "private")
        bridge._social.is_group.return_value = False
        bridge._can_chat = Mock(return_value=True)
        bridge._resolve_message_text = Mock(
            side_effect=AssertionError("Reply photo must not go to text LLM")
        )
        bridge._send_answer = Mock()
        bridge.app = SimpleNamespace(
            _ensure_session=Mock(return_value="telegram:555:987"),
            _save_assistant=Mock(),
            storage=Mock(),
        )
        quoted_photo = self.message()
        current = {
            "chat": {"id": 555, "type": "private"},
            "from": {"id": 987},
            "message_id": 101,
            "text": "Опиши фотографию, на которую я отвечаю",
            "reply_to_message": quoted_photo,
        }
        captured = []
        def describe(vision, payload):
            captured.append(payload)
            return "На фотографии дерево"
        with patch("leshy.telegram_fast.LocalTelegramVision.describe", autospec=True,
                   side_effect=describe):
            bridge._handle_message(current)
        bridge._resolve_message_text.assert_not_called()
        self.assertEqual(captured[0]["caption"], current["text"])
        self.assertEqual(image_attachment(captured[0])["file_id"], "photo-big")
        bridge._send_answer.assert_called_once_with(
            555, "На фотографии дерево", reply_to_message_id=101,
        )

    def test_http_400_reports_multimodal_format_instead_of_timeout(self):
        from urllib import error
        bridge = Mock()
        bridge._api.return_value = {"file_path": "photos/file.jpg", "file_size": 4}
        bridge._token.return_value = "no-log-secret"
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, *_): return b"\xff\xd8\xffDATA"
        class Opener:
            def open(self, req, timeout):
                if req.full_url.startswith("https://api.telegram.org/file/bot"):
                    return Response()
                raise error.HTTPError(req.full_url, 400, "Bad Request", {}, None)
        with patch.dict(os.environ, {
            "LESHY_VISION_ENDPOINT": "http://127.0.0.1:8080/v1/chat/completions",
            "LESHY_VISION_MODEL": "local-vision",
        }), patch(
            "leshy.telegram_vision.request.build_opener", return_value=Opener()
        ):
            with self.assertRaisesRegex(VisionUnavailable, "мультимодальный запрос"):
                LocalTelegramVision(bridge).describe(self.message())

    def test_caption_does_not_enter_text_llm_route(self):
        bridge = FastTelegramBridge.__new__(FastTelegramBridge)
        bridge._bot_id = 123
        bridge._bot_username = "leshy_bot"
        bridge._social = Mock()
        bridge._social.addressed_to_leshy.return_value = (True, "private")
        bridge._social.is_group.return_value = False
        bridge._can_chat = Mock(return_value=True)
        bridge._is_owner = Mock(return_value=True)
        bridge._resolve_message_text = Mock(side_effect=AssertionError("Text LLM route forbidden"))
        bridge._send_answer = Mock()
        bridge.app = SimpleNamespace(
            _ensure_session=Mock(return_value="telegram:555:987"),
            _save_assistant=Mock(),
            storage=Mock(),
        )
        with patch("leshy.telegram_fast.LocalTelegramVision.describe", return_value="Вижу кота"):
            bridge._handle_message(self.message("Опиши фото"))
        bridge._resolve_message_text.assert_not_called()
        bridge._send_answer.assert_called_once_with(555, "Вижу кота", reply_to_message_id=100)


if __name__ == "__main__":
    unittest.main()
