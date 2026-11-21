"""Регрессионные тесты сохранности правил и единого бюджета контекста."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from leshy.advanced_modules_runtime import _compose_memory_context
from leshy.context_budget import apply_context_budget, estimate_tokens
from leshy.llm import ChatMessage
from leshy.runtime_latency_fix import compact_realtime_messages


class _Intelligent:
    def latest_user_query(self, owner_id: str) -> str:
        return "Как прошёл тест, и какие правила общения действуют?"

    def context(self, owner_id: str, query: str, *, limit: int, capture_text=None) -> str:
        return (
            "- Постоянно отвечай на Вы\n"
            "- Найдено релевантное знание о тестировании"
        )


class _Graph:
    def context(self, owner_id: str, query: str, *, limit: int) -> str:
        return "Связанный факт из GraphRAG"


def test_permanent_rules_load_when_intelligent_memory_is_enabled():
    app = SimpleNamespace(storage=object(), intelligent_memory=_Intelligent(), knowledge_graph=_Graph())
    permanent = (
        "Постоянные правила общения, явно записанные пользователем именно этого профиля.\n"
        "1. Постоянно отвечай на Вы\n"
        "2. Не выдумывай результаты инструментов"
    )
    with patch("leshy.advanced_modules_runtime.module_enabled", return_value=True):
        prompt = _compose_memory_context(app, "owner:verified", permanent, query="Какие правила общения действуют?")
    assert "Постоянно отвечай на Вы" in prompt
    assert "Не выдумывай результаты инструментов" in prompt
    assert "Дополнительная интеллектуальная память" in prompt
    assert "Связанный факт из GraphRAG" in prompt
    # Дубликат, найденный Mem0, не добавляет ещё одну копию того же правила.
    assert prompt.count("Постоянно отвечай на Вы") == 1


def test_permanent_rules_load_when_intelligent_memory_is_disabled():
    app = SimpleNamespace(storage=object(), intelligent_memory=_Intelligent(), knowledge_graph=_Graph())
    rule = "Постоянные правила общения\n1. Отвечай на языке пользователя"
    with patch("leshy.advanced_modules_runtime.module_enabled", return_value=False):
        assert _compose_memory_context(app, "owner:verified", rule) == rule


def test_large_dialogue_keeps_owner_rules_and_current_topic():
    core = "/no_think\nТы — Леший; отвечай по существу."
    stored = (
        "Долговременная память этого пользователя:\n"
        "Постоянные правила общения, явно записанные пользователем именно этого профиля.\n"
        "1. Не выдумывай информацию о выполненных командах.\n"
        "2. Отвечай на Вы и продолжай первоначальный вопрос."
    )
    messages = [ChatMessage("system", core), ChatMessage("system", stored)]
    messages += [ChatMessage("user", f"История задачи {i} " + "вопрос " * 100)
                 for i in range(20)]
    messages += [
        ChatMessage("assistant", "Прежний ответ " + "данные " * 100),
        ChatMessage("user", "Вернись к вопросу о GTA VI. Какая дата релиза?"),
    ]
    prepared = compact_realtime_messages(messages)
    assert prepared == messages  # прежний ранний preclip отсутствует
    result = apply_context_budget(prepared, context_size=4096, reserve_tokens=1024)
    assert result.tokens_after <= result.budget
    assert estimate_tokens(result.messages) <= 3072
    system = result.messages[0].content
    assert "Не выдумывай информацию" in system
    assert "Отвечай на Вы" in system
    assert "GTA VI" in result.messages[-1].content


def test_recent_history_is_retained_when_token_budget_allows_it():
    conversation = [ChatMessage("system", "Ты Леший, помощник. " + "правило " * 80)]
    messages = list(conversation)
    for i in range(4):
        messages.append(ChatMessage("user", f"Вопрос {i}: проверяем диалог."))
        messages.append(ChatMessage("assistant", f"Ответ {i}: без догадок."))
    messages.append(ChatMessage("user", "А что было в вопросе 3?"))
    result = apply_context_budget(messages, context_size=2048, reserve_tokens=512)
    assert result.tokens_after <= result.budget
    assert any("Вопрос 3" in m.content for m in result.messages)
    assert "вопросе 3" in result.messages[-1].content
