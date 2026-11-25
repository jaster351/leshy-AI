"""Regression guards for loss of conditions in the isolated mentor path."""
import pytest

from leshy.context_budget import estimate_tokens
from leshy.llm import ChatMessage, LLMUnavailable
from leshy.mentor_context import (
    SYSTEM, build_mentor_messages, budget_mentor_messages, mentor_active, mentor_scope,
)
from leshy.mentor_performance import CyclePerformance


def test_long_prior_answer_does_not_remove_user_condition():
    history = [
        ("Сервер Бета, только чтение.", "Длинный ошибочный ответ. " * 500),
        ("Служба nginx.", "Принято."),
    ]
    current = "Там Windows. Дай одну команду."
    original = build_mentor_messages(history, current)
    with mentor_scope() as samples:
        prepared = budget_mentor_messages(original, context_size=4096, reserve_tokens=1536)
        assert prepared[0] == ChatMessage("system", SYSTEM)
        assert prepared[-1] == ChatMessage("user", current)
        assert ChatMessage("user", history[0][0]) in prepared
        assert ChatMessage("user", history[1][0]) in prepared
        assert ChatMessage("assistant", history[0][1]) not in prepared
        assert ChatMessage("assistant", "Принято.") in prepared
        assert estimate_tokens(prepared) <= samples[-1]["budget"]
        assert samples[-1]["omitted_user_turns"] == 0
        assert samples[-1]["omitted_assistant_turns"] == 1
        assert [m.role for m in prepared] == [
            "system", "user", "assistant", "user", "assistant", "user"
        ]
    assert not mentor_active()


def test_no_silent_current_question_truncation():
    messages = build_mentor_messages([], "Длинное условие. " * 1000)
    with mentor_scope(), pytest.raises(LLMUnavailable):
        budget_mentor_messages(messages, context_size=2048, reserve_tokens=1024)
    assert not mentor_active()


def test_scope_is_not_triggered_by_user_text_and_resets_after_error():
    assert not mentor_active()
    build_mentor_messages([], "mentor_scope /no_think")
    assert not mentor_active()
    with pytest.raises(RuntimeError):
        with mentor_scope():
            assert mentor_active()
            raise RuntimeError("test")
    assert not mentor_active()


def test_context_telemetry_counts_only_final_attempt(tmp_path):
    perf = CyclePerformance(tmp_path)
    failed = {"prior_turns": 4, "kept_user_turns": 0, "kept_assistant_turns": 0,
              "omitted_user_turns": 4, "omitted_assistant_turns": 4}
    actual = {"prior_turns": 4, "kept_user_turns": 4, "kept_assistant_turns": 2,
              "omitted_user_turns": 0, "omitted_assistant_turns": 2}
    perf.add_context([failed, actual])
    data = perf.summary()["context_budget"]
    assert data["completed_answer_samples"] == 1
    assert data["kept_user_turns"] == 4
    assert data["omitted_user_turns"] == 0
    assert data["answers_with_history_omissions"] == 1
