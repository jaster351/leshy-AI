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
from leshy.telegram_vision_context import RecentTelegramPhotos, refers_to_recent_photo
from leshy.telegram_vision import _ollama_vision_backend


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
        with patch.dict(os.environ, {"LESHY_VISION_ENDPOINT": "", "LESHY_VISION_MODEL": ""}), patch(
            "leshy.telegram_vision._ollama_vision_backend", return_value=None
        ):
            with self.assertRaisesRegex(VisionNotConfigured, "ollama pull"):
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
                return Response(json.dumps({
                    "choices": [{"message": {"content": "На фото тест"}}]
                }, ensure_ascii=False).encode("utf-8"))
        def download(req, timeout):
            self.assertEqual(req.full_url.split("/file/bot")[0], "https://api.telegram.org")
            return Response(b"\xff\xd8\xffDATA")
        with patch.dict(os.environ, {
            "LESHY_VISION_ENDPOINT": "http://127.0.0.1:8080/v1/chat/completions",
            "LESHY_VISION_MODEL": "local-vision",
        }), patch("leshy.telegram_vision.request.urlopen", side_effect=download), patch(
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
                raise error.HTTPError(req.full_url, 400, "Bad Request", {}, None)
        with patch.dict(os.environ, {
            "LESHY_VISION_ENDPOINT": "http://127.0.0.1:8080/v1/chat/completions",
            "LESHY_VISION_MODEL": "local-vision",
        }), patch("leshy.telegram_vision.request.urlopen", return_value=Response()), patch(
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


    def test_auto_detect_local_ollama_checks_vision_capability(self):
        class Response:
            def __init__(self, obj):
                self.raw = json.dumps(obj).encode()
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, n=-1): return self.raw[:n]

        calls = []
        class Opener:
            def open(self, req, *, timeout):
                calls.append((req.full_url, timeout))
                if req.full_url.endswith("/api/tags"):
                    return Response({"models": [
                        {"name": "llama3.2:3b"},
                        {"name": "qwen2.5vl:3b"},
                    ]})
                self_model = json.loads(req.data)["model"]
                return Response({"capabilities": ["completion", "vision"]}
                                if self_model == "qwen2.5vl:3b" else
                                {"capabilities": ["completion"]})
        with patch.dict(os.environ, {"LESHY_VISION_OLLAMA_URL": "http://127.0.0.1:11434"}), patch(
            "leshy.telegram_vision.request.build_opener", return_value=Opener()
        ) as opener:
            self.assertEqual(_ollama_vision_backend(), (
                "http://127.0.0.1:11434/api/chat", "qwen2.5vl:3b"))
        from urllib.request import ProxyHandler
        self.assertTrue(any(isinstance(x, ProxyHandler) and not x.proxies
                            for x in opener.call_args.args))
        self.assertTrue(all(url.startswith("http://127.0.0.1:11434/api/")
                            for url, _ in calls))

    def test_ollama_photo_submission_uses_native_images_not_text_only(self):
        sent = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, n=-1):
                return json.dumps(
                    {"message": {"content": "На снимке белка."}}, ensure_ascii=False
                ).encode("utf-8")
        class Opener:
            def open(self, req, timeout):
                sent.append((req.full_url, json.loads(req.data), timeout))
                return Response()
        with patch.dict(os.environ, {"LESHY_VISION_ENDPOINT": "", "LESHY_VISION_MODEL": ""}), patch(
            "leshy.telegram_vision._ollama_vision_backend",
            return_value=("http://127.0.0.1:11434/api/chat", "qwen2.5vl:3b")
        ), patch.object(LocalTelegramVision, "_download", return_value=b"\xff\xd8\xffDATA"), patch(
            "leshy.telegram_vision.request.build_opener", return_value=Opener()
        ):
            text = LocalTelegramVision(Mock()).describe(self.message("Кто это?"))
        self.assertEqual(text, "На снимке белка.")
        self.assertEqual(sent[0][1]["model"], "qwen2.5vl:3b")
        self.assertEqual(sent[0][1]["messages"][0]["content"], "Кто это?")
        self.assertEqual(len(sent[0][1]["messages"][0]["images"]), 1)
        self.assertEqual(sent[0][0], "http://127.0.0.1:11434/api/chat")

    def test_auto_detect_rejects_remote_ollama_url(self):
        with patch.dict(os.environ, {
            "LESHY_VISION_ENDPOINT": "", "LESHY_VISION_MODEL": "",
            "LESHY_VISION_OLLAMA_URL": "https://remote.example.org",
        }):
            with self.assertRaises(VisionNotConfigured):
                LocalTelegramVision(Mock()).describe(self.message())

    def test_recent_photo_followup_is_bound_to_same_sender_and_chat(self):
        photos = RecentTelegramPhotos(ttl_seconds=600)
        self.assertTrue(photos.remember(555, "987", self.message()))
        image = photos.recall(555, "987", "Кто это?")
        self.assertEqual(image["photo"][0]["file_id"], "photo-big")
        self.assertEqual(image["caption"], "Кто это?")
        self.assertIsNone(photos.recall(555, "other", "Кто это?"))
        self.assertIsNone(photos.recall(777, "987", "Кто это?"))
        self.assertIsNone(photos.recall(555, "987", "Кто ты?"))
        self.assertTrue(refers_to_recent_photo("Что на фотографии?"))
        self.assertFalse(refers_to_recent_photo("/status"))

    def test_recent_photo_context_expires_without_storing_bytes(self):
        photos = RecentTelegramPhotos(ttl_seconds=5, limit=1)
        photos.remember(555, "987", self.message())
        self.assertNotIn("DATA", str(photos._entries))
        with photos._lock:
            key = ("555", "987")
            stamp, payload = photos._entries[key]
            photos._entries[key] = (stamp - 20, payload)
        self.assertIsNone(photos.recall(555, "987", "Кто это?"))

    def test_telegram_followup_without_reply_uses_recent_photo(self):
        bridge = FastTelegramBridge.__new__(FastTelegramBridge)
        bridge._bot_id = 123
        bridge._bot_username = "leshy_bot"
        bridge._social = Mock()
        bridge._social.addressed_to_leshy.return_value = (True, "private")
        bridge._social.is_group.return_value = False
        bridge._can_chat = Mock(return_value=True)
        bridge._resolve_message_text = Mock(
            side_effect=AssertionError("Followup must not go to text-only LLM"))
        bridge._handle_vision_message = Mock()
        bridge._recent_photos = RecentTelegramPhotos()
        bridge._handle_message(self.message())
        followup = {
            "chat": {"id": 555, "type": "private"},
            "from": {"id": 987}, "message_id": 101, "text": "Кто это?",
        }
        bridge._handle_message(followup)
        self.assertEqual(bridge._handle_vision_message.call_count, 2)
        routed = bridge._handle_vision_message.call_args_list[1]
        self.assertEqual(routed.args[0]["photo"][0]["file_id"], "photo-big")
        self.assertEqual(routed.args[0]["caption"], "Кто это?")
        bridge._resolve_message_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
