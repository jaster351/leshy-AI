from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import patch

from leshy.llm import ChatMessage
from leshy.streaming_backends import _llama_stream, _looks_truncated_text, _strip_continuation_overlap


class _StreamResponse:
    def __init__(self, *payloads: str): self.lines = [item.encode("utf-8") for item in payloads]
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False
    def __iter__(self): return iter(self.lines)


class _Provider:
    backend = "vulkan"
    max_tokens = 256
    request_timeout = 5
    def __init__(self): self._chat_lock = threading.Lock(); self.stopped = False
    def _ensure_server(self): return "http://127.0.0.1:12345"
    def _stop_process(self): self.stopped = True


class StreamingAutoContinueTests(unittest.TestCase):
    def test_finish_reason_length_continues_without_user_command(self):
        provider = _Provider()
        first = _StreamResponse(
            'data: {"choices":[{"delta":{"content":"Начало ответа, которое оборвалось за"},"finish_reason":null}]}\n',
            'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n', 'data: [DONE]\n')
        second = _StreamResponse(
            'data: {"choices":[{"delta":{"content":" пределом и теперь завершено."},"finish_reason":null}]}\n',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n', 'data: [DONE]\n')
        with patch("leshy.streaming_backends.request.urlopen", side_effect=[first, second]) as urlopen:
            result = "".join(_llama_stream(provider, [ChatMessage("user", "Дай длинный ответ")]))
        self.assertEqual(result, "Начало ответа, которое оборвалось за пределом и теперь завершено.")
        self.assertEqual(urlopen.call_count, 2)
        payload = json.loads(urlopen.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertIn("Продолжи предыдущий ответ ровно с места обрыва", payload["messages"][-1]["content"])
        self.assertEqual(payload["messages"][-2]["role"], "assistant")

    def test_normal_stop_does_not_force_extra_generation(self):
        provider = _Provider()
        response = _StreamResponse(
            'data: {"choices":[{"delta":{"content":"Готовый законченный ответ."},"finish_reason":null}]}\n',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n', 'data: [DONE]\n')
        with patch("leshy.streaming_backends.request.urlopen", return_value=response) as urlopen:
            result = "".join(_llama_stream(provider, [ChatMessage("user", "Короткий вопрос")]))
        self.assertEqual(result, "Готовый законченный ответ.")
        self.assertEqual(urlopen.call_count, 1)

    def test_obvious_mid_sentence_tail_is_detected_without_finish_reason(self):
        text = ("Подробный ответ с большим количеством полезного контекста. " * 4) + "Продолжение зависит от данных за"
        self.assertTrue(_looks_truncated_text(text))
        self.assertFalse(_looks_truncated_text("Это полностью законченный ответ." * 8))

    def test_repeated_tail_is_removed_from_internal_continuation(self):
        previous = "Ответ уже содержит длинный повторяемый фрагмент в самом конце"
        continuation = "длинный повторяемый фрагмент в самом конце и дальше новый текст."
        self.assertEqual(_strip_continuation_overlap(previous, continuation), " и дальше новый текст.")


if __name__ == "__main__": unittest.main()
