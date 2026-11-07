from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .llm import ChatMessage


@dataclass(frozen=True)
class ContextBudgetResult:
    messages: list[ChatMessage]
    tokens_before: int
    tokens_after: int
    budget: int
    dropped_messages: int
    truncated_messages: int


def estimate_tokens(messages: list[ChatMessage]) -> int:
    """Консервативная оценка для русского/многоязычного Qwen в 4096 context.

    Старый len/2 недооценивал токены кириллицы, после чего llama-server
    получал контекст длиннее собственного n_ctx. Без внешнего tokenizer
    считаем не-ASCII символ как минимум одним токеном, ASCII — 1/3.
    """
    total = 0
    for message in messages:
        value = str(message.content)
        ascii_count = sum(1 for char in value if ord(char) < 128)
        other_count = len(value) - ascii_count
        total += 16 + max(1, (ascii_count + 2) // 3 + other_count)
    return total


def _normalize_system_messages(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Свести системный контекст в один первый блок для строгих chat-template.

    Некоторые GGUF (в частности Qwen3.5) допускают system только первым сообщением.
    Леший собирает системный контекст из нескольких независимых слоёв, поэтому перед
    любым backend объединяем их, сохраняя исходный порядок содержимого.
    """
    system_parts = [str(message.content).strip() for message in messages if message.role == "system" and str(message.content).strip()]
    dialogue = [message for message in messages if message.role != "system"]
    if not system_parts:
        return dialogue
    return [ChatMessage("system", "\n\n".join(system_parts))] + dialogue


def _truncate_content(content: str, target_tokens: int, *, preserve_head: bool = False) -> str:
    """Обрезать текст строго до лимита, сохранив формулировку текущей цели.

    Пользовательская цель часто стоит в начале длинного сообщения, а
    цитируемый документ или вложение — в конце. Для последнего user
    сохраняем обе части, а не только хвост вложения.
    """
    max_chars = max(64, int(target_tokens) * 2)
    value = str(content)
    if len(value) <= max_chars:
        return value
    marker = "[…контекст сокращён…]\n"
    available = max(1, max_chars - len(marker))
    if preserve_head:
        head_chars = min(available, max(48, available // 2))
        tail_chars = available - head_chars
        return value[:head_chars] + marker + (value[-tail_chars:] if tail_chars else "")
    return marker + value[-available:]


def apply_context_budget(messages: list[ChatMessage], *, context_size: int,
                         reserve_tokens: int = 1024,
                         estimator: Callable[[list[ChatMessage]], int] = estimate_tokens) -> ContextBudgetResult:
    context_size = max(512, int(context_size))
    reserve = max(128, min(int(reserve_tokens), max(128, context_size // 2)))
    budget = max(256, context_size - reserve)
    source = _normalize_system_messages(list(messages))
    before = estimator(source)
    if before <= budget:
        return ContextBudgetResult(source, before, before, budget, 0, 0)
    system = [m for m in source if m.role == "system"]
    dialogue = [m for m in source if m.role != "system"]
    tail = dialogue[-2:]
    older = dialogue[:-2]
    kept = system + tail
    dropped = len(older)
    while estimator(kept) > budget and len(system) > 1:
        system.pop(1)
        dropped += 1
        kept = system + tail
    truncated = 0
    while estimator(kept) > budget and kept:
        # Сначала ужимаем старый диалог и систему; актуальный запрос
        # пользователя — последний кандидат на сокращение.
        candidates = sorted(
            range(len(kept)),
            key=lambda i: (
                kept[i].role == "user" and i == len(kept) - 1,
                -len(kept[i].content),
            ),
        )
        changed = False
        for index in candidates:
            current = kept[index]
            current_tokens = max(1, (len(current.content) + 1) // 2)
            if current_tokens <= 64:
                continue
            overflow = estimator(kept) - budget
            target = max(64, current_tokens - overflow - 16)
            is_current_user = current.role == "user" and index == len(kept) - 1
            shortened = _truncate_content(
                current.content, target, preserve_head=is_current_user or current.role == "system"
            )
            if shortened != current.content:
                kept[index] = ChatMessage(current.role, shortened)
                truncated += 1
                changed = True
                break
        if not changed:
            break
    after = estimator(kept)
    return ContextBudgetResult(kept, before, after, budget, dropped, truncated)


__all__ = ["ContextBudgetResult", "apply_context_budget", "estimate_tokens"]