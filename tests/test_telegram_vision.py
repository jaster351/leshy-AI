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
                payloads.append(json.loads(req.data))
                return Response(b'{"choices":[{"message":{"content":"На фото тест"}}]}')
        def download(req, timeout):
            self.assertEqual(req.full_url.split("/file/bot")[0], "https://api.telegram.org")
            return Response(b"\\xff\\xd8\\xffDATA")
        with patch.dict(os.environ, {
            "LESHY_VISION_ENDPOINT": "http://127.0.0.1:8080/v1/chat/completions",
            "LESHY_VISION_MODEL": "local-vision",
        }), patch("leshy.telegram_vision.request.urlopen", side_effect=download), patch(
            "leshy.telegram_vision.request.build_opener", return_value=Opener()
        ):
            answer = LocalTelegramVision(bridge).describe(self.message("Что видишь?"))
        self.assertIn("На фото", answer)
        self.assertEqual(payloads[0]["model"], "local-vision")
        self.assertEqual(payloads[0]["messages"][0]["content"][0]["text"], "Что видишь?")
        self.assertTrue(payloads[0]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        bridge._api.assert_called_once_with("getFile", {"file_id": "photo-big"}, timeout=12)

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
