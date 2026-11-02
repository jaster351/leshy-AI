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
    total = 0
    for message in messages:
        total += 8 + max(1, (len(str(message.content)) + 1) // 2)
    return total


def _truncate_content(content: str, target_tokens: int) -> str:
    max_chars = max(64, int(target_tokens) * 2)
    value = str(content)
    if len(value) <= max_chars:
        return value
    return "[…контекст сокращён…]\n" + value[-max_chars:]


def apply_context_budget(messages: list[ChatMessage], *, context_size: int,
                         reserve_tokens: int = 1024,
                         estimator: Callable[[list[ChatMessage]], int] = estimate_tokens) -> ContextBudgetResult:
    context_size = max(512, int(context_size))
    reserve = max(128, min(int(reserve_tokens), max(128, context_size // 2)))
    budget = max(256, context_size - reserve)
    source = list(messages)
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
        candidates = sorted(range(len(kept)), key=lambda i: (kept[i].role == "user" and i == len(kept) - 1, len(kept[i].content)))
        changed = False
        for index in reversed(candidates):
            current = kept[index]
            current_tokens = max(1, (len(current.content) + 1) // 2)
            if current_tokens <= 64:
                continue
            overflow = estimator(kept) - budget
            target = max(64, current_tokens - overflow - 16)
            shortened = _truncate_content(current.content, target)
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