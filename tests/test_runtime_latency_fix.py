from __future__ import annotations

import unittest

from leshy.llm import ChatMessage
from leshy.context_budget import apply_context_budget, estimate_tokens
from leshy.realtime_chat import RealtimeChatManager
from leshy.research import DdgsSearch
from leshy.runtime_latency_fix import (
    REALTIME_CONTEXT_CHAR_BUDGET,
    SUPPORTED_DDGS_BACKENDS,
    compact_realtime_messages,
    install_runtime_latency_fixes,
)


class _Knowledge:
    def search(self, *args, **kwargs):
        # Даже наличие trusted-знания не должно отключать live-refresh погоды.
        return [{"claim": "старый прогноз", "source_url": "https://example.test/old"}]

    def search_candidates(self, *args, **kwargs):
        return []


class _App:
    knowledge = _Knowledge()

    @staticmethod
    def _needs_live_search(_text: str) -> bool:
        # Regression проверяет, что защитный dynamic-data контракт работает
        # независимо от старого вспомогательного marker-router.
        return False


class RuntimeLatencyFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_runtime_latency_fixes()

    def test_exact_weather_phrase_from_runtime_log_forces_live_search(self):
        manager = RealtimeChatManager(_App())
        plan = manager._search_plan("Какая сегодня погода в Челябинске?")
        self.assertTrue(plan["needed"])
        self.assertEqual(plan["reason"], "требуются актуальные данные")

    def test_ddgs_defaults_do_not_include_backends_missing_in_runtime(self):
        self.assertEqual(DdgsSearch.DEFAULT_BACKENDS, SUPPORTED_DDGS_BACKENDS)
        self.assertNotIn("bing", SUPPORTED_DDGS_BACKENDS)
        self.assertNotIn("google", SUPPORTED_DDGS_BACKENDS)
        self.assertNotIn("yandex", SUPPORTED_DDGS_BACKENDS)
        self.assertIn("grokipedia", SUPPORTED_DDGS_BACKENDS)

    def test_long_24_message_history_is_compacted_before_llama_server(self):
        messages = [ChatMessage("system", "основные инструкции " + "x" * 900)]
        for index in range(11):
            messages.append(ChatMessage("user", f"старый вопрос {index} " + "у" * 500))
            messages.append(ChatMessage("assistant", f"старый ответ {index} " + "а" * 500))
        messages.append(ChatMessage(
            "system",
            "Результаты текущего веб-поиска:\n" + "свежая погода " * 220,
        ))
        current = "Какая сегодня погода в Челябинске?"
        messages.append(ChatMessage("user", current))

        # Раннее ограничение 2600 символов отключено. Реальный backend
        # выбирает бюджет токенов из n_ctx и резерва генерации.
        prepared = compact_realtime_messages(messages)
        self.assertEqual(prepared, messages)
        self.assertEqual(REALTIME_CONTEXT_CHAR_BUDGET, 0)
        result = apply_context_budget(prepared, context_size=4096, reserve_tokens=1024)

        self.assertLessEqual(result.tokens_after, result.budget)
        self.assertLessEqual(estimate_tokens(result.messages), 3072)
        self.assertEqual(result.messages[-1].role, "user")
        self.assertEqual(result.messages[-1].content, current)
        self.assertTrue(any("Результаты текущего веб-поиска" in item.content for item in result.messages))
        self.assertLess(len(result.messages), len(messages))

    def test_short_context_is_unchanged(self):
        messages = [
            ChatMessage("system", "инструкция"),
            ChatMessage("user", "Привет"),
        ]
        compacted = compact_realtime_messages(messages)
        self.assertEqual([(m.role, m.content) for m in compacted], [(m.role, m.content) for m in messages])


if __name__ == "__main__":
    unittest.main()
