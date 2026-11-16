"""Deterministic guard against replaying an answer the user just rejected.

No extra web search, tool execution, or unbounded model retry is authorized by
this filter. A correction is checked against the immediately preceding reply
from the same owner's session only.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any, Iterable


def _normal(text: str) -> str:
    return " ".join(str(text or "").casefold().replace("ё", "е").split())


_CORRECTION = re.compile(
    r"^(?:леший[,!:]?\s+)?(?:нет[,!:]?\s+|"
    r"ты\s+(?:снова|опять|неправильно|ошиб)|"
    r"это\s+(?:не\s+то|неверно|неправильно|ошиб)|"
    r"я\s+(?:просил|попросил)|"
    r"почему\s+(?:ты\s+)?(?:снова|опять|не)|"
    r"исправь\s+(?:ошибку|ответ|это)|"
    r"не\s+(?:это|тот\s+ответ)|"
    r"вернись\s+к\s+(?:вопросу|задаче))",
    re.IGNORECASE,
)
_REPEAT_COMMAND = re.compile(
    r"^(?:леший[,!:]?\s+)?(?:повтори|скопируй|продублируй)\s+"
    r"(?:свой|тот\s+же|предыдущий|ответ|текст)",
    re.IGNORECASE,
)


def is_correction_request(message: str) -> bool:
    text = _normal(message)
    return bool(0 < len(text) <= 1100
                and not _REPEAT_COMMAND.match(text)
                and _CORRECTION.match(text))


def _role_content(row: Any) -> tuple[str, str]:
    try:
        return str(row["role"]), str(row["content"])
    except (KeyError, IndexError, TypeError):
        return str(getattr(row, "role", "")), str(getattr(row, "content", ""))


def previous_assistant_answer(rows: Iterable[Any]) -> str:
    for row in rows:
        role, content = _role_content(row)
        if role == "assistant" and content.strip():
            return content
    return ""


def _same_answer(previous: str, answer: str) -> bool:
    old = _normal(previous)
    new = _normal(answer)
    if min(len(old), len(new)) < 45:
        return False
    if old == new:
        return True
    if min(len(old), len(new)) >= 120:
        length_ratio = min(len(old), len(new)) / max(len(old), len(new))
        if length_ratio >= 0.82 and SequenceMatcher(None, old[:5000], new[:5000]).ratio() >= 0.87:
            return True
    return False


def protect_corrected_answer(message: str, rows: Iterable[Any], answer: str) -> tuple[str, str]:
    """Return the answer unchanged or a truthful, non-looping stop message."""
    if not is_correction_request(message):
        return answer, ""
    previous = previous_assistant_answer(rows)
    if not previous or not answer:
        return answer, ""
    if _same_answer(previous, answer):
        return (
            "Повторный ответ совпал с ранее отклонённым. Я остановил его публикацию "
            "и не считаю исходное задание выполненным. Новые внешние действия не запускались.",
            "rejected_answer_repeated",
        )
    # A memory/document correction cannot turn into an unrelated parcel
    # tracker result or an OS tutorial. These are known erroneous routes.
    current = _normal(message)
    response = _normal(answer)
    if any(x in current for x in ("knowledge", "инструкц", "памят", "cloud4box")):
        if (("посылк" in response or "диспетчер устройств" in response)
                and not ("посылк" in current or "диспетчер устройств" in current)):
            return (
                "Ответ относится к посторонней теме. Я остановил его публикацию; "
                "сохранение инструкции не подтверждено. Новые инструменты не запускались.",
                "response_topic_mismatch",
            )
    return answer, ""


__all__ = ["is_correction_request", "previous_assistant_answer",
           "protect_corrected_answer"]