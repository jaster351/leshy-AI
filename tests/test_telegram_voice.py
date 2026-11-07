from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from leshy.telegram import TelegramBridge
from leshy.telegram_fast import FastTelegramBridge
from leshy.realtime_chat import StreamEvent


class _App:
    def __init__(self):
        self.chat_calls = []
        self._voice_asr = None

    def chat(self, text, *, owner_id, session_id):
        self.chat_calls.append((text, owner_id, session_id))
        return {"answer": "Готово"}


class _ASR:
    def __init__(self, text="Что сегодня нового?"):
        self.text = text
        self.calls = []

    def transcribe(self, audio, *, content_type):
        self.calls.append((audio, content_type))
        return SimpleNamespace(text=self.text)


class TelegramVoiceTests(unittest.TestCase):
    def make_bridge(self, temp, cls=TelegramBridge):
        app = _App()
        bridge = cls(app, Path(temp) / "LeshyBrain")
        bridge.configure("", [], allow_all=True, owner_id="100")
        sent = []
        answers = []
        bridge._send = lambda chat_id, text: sent.append((chat_id, text))
        bridge._send_answer = lambda chat_id, text, **kw: answers.append((chat_id, text))
        bridge._send_typing = lambda chat_id: None
        bridge._set_reaction = lambda *args, **kwargs: None
        return app, bridge, sent, answers

    @staticmethod
    def message(user=200, *, chat_type="private"):
        return {
            "message_id": 77,
            "chat": {"id": 12, "type": chat_type},
            "from": {"id": user},
            "voice": {"file_id": "VOICE", "file_size": 1000, "duration": 2},
        }

    def test_private_voice_is_transcribed_then_answered(self):
        with tempfile.TemporaryDirectory() as temp:
            app, bridge, _sent, answers = self.make_bridge(temp)
            app._voice_asr = _ASR()
            bridge._download_telegram_voice = lambda voice: b"OggSdata"
            bridge._handle_message(self.message())
            self.assertEqual(app._voice_asr.calls, [(b"OggSdata", "audio/ogg")])
            self.assertEqual(app.chat_calls, [("Что сегодня нового?", "telegram:200", "telegram:12:200")])
            self.assertEqual(answers, [(12, "Готово")])

    def test_caption_and_voice_are_combined(self):
        with tempfile.TemporaryDirectory() as temp:
            app, bridge, _sent, _answers = self.make_bridge(temp)
            app._voice_asr = _ASR("вот запись")
            bridge._download_telegram_voice = lambda voice: b"OggS"
            message = self.message()
            message["caption"] = "Объясни:"
            bridge._handle_message(message)
            self.assertEqual(app.chat_calls[0][0], "Объясни:\nвот запись")

    def test_unauthorized_voice_is_never_downloaded(self):
        with tempfile.TemporaryDirectory() as temp:
            app, bridge, sent, _answers = self.make_bridge(temp)
            bridge.configure("", [], allow_all=False, owner_id="100")
            bridge._download_telegram_voice = lambda voice: self.fail("download before authorization")
            bridge._handle_message(self.message(user=201))
            self.assertEqual(app.chat_calls, [])
            self.assertIn("Доступ", sent[0][1])

    def test_unaddressed_group_voice_is_never_downloaded(self):
        with tempfile.TemporaryDirectory() as temp:
            app, bridge, sent, _answers = self.make_bridge(temp)
            bridge._bot_id = 999
            bridge._download_telegram_voice = lambda voice: self.fail("download without direct address")
            bridge._handle_message(self.message(chat_type="supergroup"))
            self.assertEqual(app.chat_calls, [])
            self.assertEqual(sent, [])

    def test_group_voice_reply_to_bot_is_answered(self):
        with tempfile.TemporaryDirectory() as temp:
            app, bridge, _sent, answers = self.make_bridge(temp)
            bridge._bot_id = 999
            app._voice_asr = _ASR("да, расскажи подробнее")
            bridge._download_telegram_voice = lambda voice: b"OggS"
            message = self.message(chat_type="group")
            message["reply_to_message"] = {
                "message_id": 5, "from": {"id": 999, "first_name": "Леший"},
                "text": "Я нашёл информацию.",
            }
            bridge._handle_message(message)
            self.assertEqual(len(app.chat_calls), 1)
            self.assertIn("да, расскажи подробнее", app.chat_calls[0][0])
            self.assertEqual(answers, [(12, "Готово")])

    def test_failed_transcription_does_not_call_llm_or_leak_token(self):
        with tempfile.TemporaryDirectory() as temp:
            app, bridge, sent, _answers = self.make_bridge(temp)
            bridge._download_telegram_voice = lambda voice: (_ for _ in ()).throw(RuntimeError("TOKEN_SECRET"))
            bridge._handle_message(self.message())
            self.assertEqual(app.chat_calls, [])
            self.assertEqual(len(sent), 1)
            self.assertNotIn("TOKEN_SECRET", sent[0][1])

    def test_empty_transcription_does_not_call_llm(self):
        with tempfile.TemporaryDirectory() as temp:
            app, bridge, sent, _answers = self.make_bridge(temp)
            app._voice_asr = _ASR("")
            bridge._download_telegram_voice = lambda voice: b"OggS"
            bridge._handle_message(self.message())
            self.assertEqual(app.chat_calls, [])
            self.assertIn("не удалось разобрать речь", sent[0][1])

    def test_declared_oversize_rejected_before_api(self):
        with tempfile.TemporaryDirectory() as temp:
            _app, bridge, _sent, _answers = self.make_bridge(temp)
            bridge._api = lambda *a, **kw: self.fail("getFile on oversized voice")
            with self.assertRaisesRegex(ValueError, "20 МБ"):
                bridge._download_telegram_voice({"file_id": "f", "file_size": 21 * 1024 * 1024})

    def test_declared_overlong_rejected_before_api(self):
        with tempfile.TemporaryDirectory() as temp:
            _app, bridge, _sent, _answers = self.make_bridge(temp)
            bridge._api = lambda *a, **kw: self.fail("getFile on long voice")
            with self.assertRaisesRegex(ValueError, "пяти минут"):
                bridge._download_telegram_voice({"file_id": "f", "duration": 301})

    def test_getfile_and_bounded_download(self):
        with tempfile.TemporaryDirectory() as temp:
            _app, bridge, _sent, _answers = self.make_bridge(temp)
            bridge._api = lambda method, payload, timeout: {"file_path": "voice/one.oga", "file_size": 4}
            bridge._token = lambda: "test:TOKEN"
            result = io.BytesIO(b"OggS")
            result.headers = {"Content-Length": "4"}
            with patch("leshy.telegram.request.urlopen", return_value=result) as mocked:
                self.assertEqual(bridge._download_telegram_voice({"file_id": "F"}), b"OggS")
                self.assertIn("/file/bottest:TOKEN/voice/one.oga", mocked.call_args.args[0].full_url)

    def test_rejects_traversal_path_from_getfile(self):
        with tempfile.TemporaryDirectory() as temp:
            _app, bridge, _sent, _answers = self.make_bridge(temp)
            bridge._api = lambda method, payload, timeout: {"file_path": "../hidden"}
            with self.assertRaisesRegex(RuntimeError, "некорректный путь"):
                bridge._download_telegram_voice({"file_id": "F"})

    def test_limits_actual_download_when_headers_underreport(self):
        with tempfile.TemporaryDirectory() as temp:
            _app, bridge, _sent, _answers = self.make_bridge(temp)
            bridge._api = lambda method, payload, timeout: {"file_path": "voice/test.oga"}
            bridge._token = lambda: "test:TOKEN"
            result = io.BytesIO(b"123456789")
            result.headers = {}
            with patch("leshy.telegram._MAX_TELEGRAM_VOICE_BYTES", 8), patch("leshy.telegram.request.urlopen", return_value=result):
                with self.assertRaisesRegex(ValueError, "20 МБ"):
                    bridge._download_telegram_voice({"file_id": "F"})

    def test_realtime_bridge_voice_reaches_stream(self):
        with tempfile.TemporaryDirectory() as temp:
            app, bridge, sent, _answers = self.make_bridge(temp, cls=FastTelegramBridge)
            app._voice_asr = _ASR("Привет, Леший!")
            bridge._download_telegram_voice = lambda voice: b"OggS"
            streams = []
            finalized = []
            bridge._wait_for_foreground_slot = lambda: None
            bridge._realtime.stream = lambda text, **kw: (
                streams.append((text, kw)) or iter([StreamEvent("done", {"answer": "Слушаю!"})])
            )
            bridge._finalize_answer = lambda chat_id, msg_id, text, reply_id, **kw: (
                finalized.append((chat_id, text, reply_id)) or 300
            )
            message = self.message()
            message.pop("message_id")  # Обходим глобальный SQLite ledger в unit-тесте.
            bridge._handle_message(message)
            self.assertEqual(streams[0][0], "Привет, Леший!")
            self.assertEqual(streams[0][1]["owner_id"], "telegram:200")
            self.assertEqual(finalized, [(12, "Слушаю!", None)])
            self.assertEqual(sent, [])

    def test_realtime_bridge_denied_group_not_downloaded(self):
        with tempfile.TemporaryDirectory() as temp:
            app, bridge, sent, _answers = self.make_bridge(temp, cls=FastTelegramBridge)
            bridge._download_telegram_voice = lambda voice: self.fail("unexpected download")
            message = self.message(chat_type="group")
            message.pop("message_id")
            bridge._handle_message(message)
            self.assertEqual(app.chat_calls, [])
            self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
