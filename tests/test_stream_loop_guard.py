from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import patch

from leshy.llm import ChatMessage
from leshy.stream_loop_guard import StreamLoopGuard
from leshy.streaming_backends import _llama_stream, _ollama_stream


_DIALOGUE = (
    "Пользователь: «А как это сделать?»\n"
    "Я: «Нужно просто поговорить с ним».\n"
)


class _SseResponse:
    def __init__(self, chunks: list[str]):
        self._lines = [
            ("data: " + json.dumps({
                "choices": [{"delta": {"content": chunk}, "finish_reason": None}]
            }, ensure_ascii=False) + "\n").encode("utf-8")
            for chunk in chunks
        ]
        self._lines.append(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n')
        self._lines.append(b"data: [DONE]\n")
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed = True

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        self.closed = True


class _LlamaProvider:
    backend = "vulkan"
    model = "Qwen3"
    max_tokens = 512
    request_timeout = 5

    def _chat_activity(self):
        from contextlib import nullcontext
        return nullcontext()

    def _ensure_server(self):
        return "http://127.0.0.1:12345"


class _OllamaProvider:
    model = "local"
    base_url = "http://127.0.0.1:11434"
    timeout = 5


class StreamLoopGuardTests(unittest.TestCase):
    def test_regression_exact_support_dialogue_repetition(self):
        guard = StreamLoopGuard()
        generated = "Вступление: история из техподдержки.\n" + _DIALOGUE * 50
        result = ""
        for index in range(0, len(generated), 7):
            result += guard.push(generated[index:index + 7])
            if guard.triggered:
                break
        self.assertTrue(guard.triggered)
        self.assertIn("Вступление", result)
        self.assertLessEqual(result.count("Нужно просто поговорить с ним"), 4)
        self.assertLess(len(result), len(generated) // 4)

    def test_single_large_chunk_stops_and_keeps_useful_prefix(self):
        guard = StreamLoopGuard()
        output = guard.push("Начало выступления: разные шутки.\n" + _DIALOGUE * 30)
        self.assertTrue(guard.triggered)
        self.assertIn("Начало выступления", output)
        self.assertLess(len(output), 500)

    def test_distinct_paragraphs_and_short_refrain_are_untouched(self):
        examples = [
            " ".join(
                f"История {i}: пользователь открывает новый тикет, финал уникален."
                for i in range(40)
            ),
            "Очень смешно. " * 100,
            "Привет! " * 100,
        ]
        for sample in examples:
            with self.subTest(sample=sample[:20]):
                guard = StreamLoopGuard()
                output = "".join(guard.push(sample[i:i + 5]) for i in range(0, len(sample), 5))
                self.assertFalse(guard.triggered)
                self.assertEqual(output, sample)

    def test_llama_stream_stops_repeated_dialogue_before_token_limit(self):
        response = _SseResponse([_DIALOGUE] * 30)
        with patch("leshy.streaming_backends._open_local_llama", return_value=response) as opened:
            answer = "".join(_llama_stream(_LlamaProvider(), [
                ChatMessage("user", "Расскажи стендап о техподдержке"),
            ]))
        self.assertIn("Генерация остановлена", answer)
        self.assertLessEqual(answer.count("Нужно просто поговорить с ним"), 4)
        self.assertTrue(response.closed)
        self.assertEqual(opened.call_count, 1)
        payload = json.loads(opened.call_args.args[0].data.decode("utf-8"))
        self.assertGreater(payload["repeat_penalty"], 1)
        self.assertGreater(payload["repeat_last_n"], 64)

    def test_ollama_stream_stops_the_same_loop(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __iter__(self):
                for _ in range(30):
                    yield (json.dumps({"message": {"content": _DIALOGUE}}) + "\n").encode("utf-8")

        with patch("leshy.streaming_backends.request.urlopen", return_value=Response()) as opened:
            answer = "".join(_ollama_stream(_OllamaProvider(), [ChatMessage("user", "Стендап")]))
        self.assertIn("Генерация остановлена", answer)
        self.assertLessEqual(answer.count("Нужно просто поговорить с ним"), 4)
        payload = json.loads(opened.call_args.args[0].data.decode("utf-8"))
        self.assertGreater(payload["options"]["repeat_penalty"], 1)


if __name__ == "__main__":
    unittest.main()
