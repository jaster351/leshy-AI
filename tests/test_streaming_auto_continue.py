from __future__ import annotations

import json
import threading
import time
import unittest
from unittest.mock import patch, Mock

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

    def test_empty_reasoning_stream_does_not_auto_continue_six_times(self):
        provider = _Provider()
        empty = _StreamResponse(
            'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n',
            'data: [DONE]\n',
        )
        from leshy.llm import LLMUnavailable
        with patch("leshy.streaming_backends.request.urlopen", return_value=empty) as urlopen:
            with self.assertRaises(LLMUnavailable):
                list(_llama_stream(provider, [ChatMessage("user", "Привет")]))
        self.assertEqual(urlopen.call_count, 1)

    def test_long_first_round_does_not_start_second_generation(self):
        provider = _Provider()
        response = _StreamResponse(
            'data: {"choices":[{"delta":{"content":"Ответ получен"},"finish_reason":null}]}\n',
            'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n',
            'data: [DONE]\n',
        )
        # Не подменять monotonic: новый SSE watchdog тоже зависит от часов.
        with patch("leshy.streaming_backends._AUTO_CONTINUATION_FIRST_ROUND_BUDGET_SECONDS", 0):
            with patch("leshy.streaming_backends.request.urlopen", return_value=response) as urlopen:
                answer = "".join(_llama_stream(provider, [ChatMessage("user", "Задача")]))
        self.assertIn("Ответ получен", answer)
        self.assertIn("лимиту времени", answer)
        self.assertEqual(urlopen.call_count, 1)

    def test_deadline_stops_sse_even_if_server_sends_trickle(self):
        provider = _Provider()
        provider.request_timeout = 0.10

        class SlowResponse:
            def __init__(self):
                self.was_closed = threading.Event()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
                return False
            def close(self):
                self.was_closed.set()
            def __iter__(self):
                # Имитация SSE, который держит соединение открытым, пока
                # ещё есть токены: read возвращает строки без [DONE].
                while not self.was_closed.is_set():
                    time.sleep(0.01)
                    yield b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":null}]}\n'

        response = SlowResponse()
        with patch("leshy.streaming_backends.request.urlopen", return_value=response):
            started = time.monotonic()
            chunks = list(_llama_stream(provider, [ChatMessage("user", "Длинный запрос")]))
        self.assertIn("Ответ остановлен по лимиту времени", "".join(chunks))
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertTrue(response.was_closed.is_set())

    def test_qwen_default_uses_visible_non_thinking_mode(self):
        import os
        from leshy.accelerator_runtime import _visible_answer_options
        with patch.dict(os.environ, {"LESHY_QWEN_THINKING": ""}):
            self.assertEqual(_visible_answer_options("Qwen3.5-9B")["reasoning_effort"], "none")
        with patch.dict(os.environ, {"LESHY_QWEN_THINKING": "on"}):
            self.assertEqual(_visible_answer_options("Qwen3.5-9B"), {})
        self.assertEqual(_visible_answer_options("Llama-8B"), {})

    def test_cancel_closes_active_stream_without_waiting_socket_timeout(self):
        provider = _Provider()
        provider.request_timeout = 10
        cancel = threading.Event()

        class WaitingResponse:
            def __init__(self):
                self.ready = threading.Event()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
                return False
            def close(self):
                self.ready.set()
            def __iter__(self):
                self.ready.wait(5)
                return iter(())

        response = WaitingResponse()
        def cancel_soon():
            time.sleep(0.05)
            cancel.set()
        helper = threading.Thread(target=cancel_soon, daemon=True)
        helper.start()
        with patch("leshy.streaming_backends.request.urlopen", return_value=response):
            started = time.monotonic()
            self.assertEqual(
                list(_llama_stream(provider, [ChatMessage("user", "Отмена")], cancel_event=cancel)),
                [],
            )
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertTrue(response.ready.is_set())
        helper.join(timeout=1)

    def test_context_error_single_oversized_user_is_compacted(self):
        from leshy.context_budget import estimate_tokens
        from leshy.streaming_backends import _shrink_messages_for_context
        messages = [ChatMessage("user", "Очень длинный вопрос: " + "контекст " * 1100)]
        compacted = _shrink_messages_for_context(messages, 8830, 4096)
        self.assertIsNotNone(compacted)
        self.assertLessEqual(estimate_tokens(compacted), 3072)
        self.assertTrue(compacted[0].content.startswith("Очень длинный вопрос:"))

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
