"""Pathological context budgets must fit even with Cyrillic and emojis."""
from __future__ import annotations

from leshy.context_budget import apply_context_budget, estimate_tokens
from leshy.llm import ChatMessage


def test_tiny_context_with_many_unicode_characters_never_exceeds_budget():
    messages = [
        ChatMessage("system", "правило безопасности " * 1500),
        ChatMessage("assistant", "старый ответ " * 1500),
        ChatMessage("user", "ВАЖНАЯ ЦЕЛЬ: " + "🤖" * 2500 + "КОНЕЦ ЗАПРОСА"),
    ]
    result = apply_context_budget(messages, context_size=512, reserve_tokens=256)
    assert result.tokens_after <= result.budget
    assert result.messages[-1].role == "user"
    assert "ВАЖНАЯ ЦЕЛЬ" in result.messages[-1].content
    assert "КОНЕЦ ЗАПРОСА" in result.messages[-1].content


def test_strict_fallback_preserves_one_latest_user_after_old_turns():
    messages = [ChatMessage("system", "инструкции " * 3000)]
    for _ in range(20):
        messages.append(ChatMessage("assistant", "длинный ответ " * 900))
    messages.append(ChatMessage("user", "НЕ ПОТЕРЯЙ ТЕКУЩИЙ ВОПРОС" + "Ж" * 7000))
    result = apply_context_budget(messages, context_size=512, reserve_tokens=256)
    assert result.tokens_after <= result.budget
    assert result.messages[-1].role == "user"
    assert "НЕ ПОТЕРЯЙ" in result.messages[-1].content


def test_unmodified_short_context_does_not_truncate():
    messages = [ChatMessage("system", "правила"), ChatMessage("user", "привет")]
    result = apply_context_budget(messages, context_size=4096)
    assert result.messages == messages
    assert result.tokens_before == result.tokens_after
    assert result.dropped_messages == 0
