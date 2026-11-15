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


def _strict_clip(content: str, target_chars: int, *, preserve_edges: bool) -> str:
    """Emergency clip for cases where the approximate two-char/token limit fails."""
    value = str(content)
    cap = max(1, min(int(target_chars), len(value)))
    if len(value) <= cap:
        return value
    marker = "\n[…]\n"
    if not preserve_edges or cap <= len(marker) + 8:
        return value[:cap] if preserve_edges else value[-cap:]
    available = cap - len(marker)
    head = max(1, available // 2)
    return value[:head] + marker + value[-(available - head):]


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
    # Составные системные инструкции могут включать длинный persona и память.
    # При сильном переполнении нельзя слепо резать их с конца: постоянные
    # пользовательские правила как раз находятся в последних системных частях.
    system_text = source[0].content if source and source[0].role == "system" else ""
    paragraphs = [part.strip() for part in system_text.split("\n\n") if part.strip()]
    dialogue = [m for m in source if m.role != "system"]
    latest_user = next((i for i in range(len(dialogue) - 1, -1, -1)
                        if dialogue[i].role == "user"), None)
    if latest_user is None and dialogue:
        latest_user = len(dialogue) - 1

    # Обязательные фрагменты: начало persona и все явно записанные правила.
    essential = set([0] if paragraphs else [])
    essential.update(i for i, part in enumerate(paragraphs)
                     if "Постоянные правила общения" in part)
    selected_parts = set(essential)
    selected_dialogue = {latest_user} if latest_user is not None else set()
    partial_dialogue: dict[int, ChatMessage] = {}
    parts = list(paragraphs)
    truncated = 0

    def render() -> list[ChatMessage]:
        system_content = "\n\n".join(parts[i] for i in sorted(selected_parts))
        answer = [ChatMessage("system", system_content)] if system_content else []
        for index in sorted(selected_dialogue):
            answer.append(partial_dialogue.get(index, dialogue[index]))
        return answer

    # Ужимаем только то, что уже нельзя разместить целиком: сперва persona,
    # затем слишком длинный текущий вопрос. Постоянные правила обрезаются лишь
    # как крайняя мера, когда одновременно сохранить всё физически невозможно.
    for _ in range(120):
        kept = render()
        if estimator(kept) <= budget:
            break
        if 0 in selected_parts and len(parts[0]) > 220:
            target = max(220, (len(parts[0]) * 2) // 3)
            parts[0] = _strict_clip(parts[0], target, preserve_edges=True)
        elif latest_user is not None:
            current = partial_dialogue.get(latest_user, dialogue[latest_user])
            if len(current.content) > 96:
                target = max(96, (len(current.content) * 2) // 3)
                partial_dialogue[latest_user] = ChatMessage(
                    current.role,
                    _strict_clip(current.content, target, preserve_edges=True),
                )
            else:
                rule_indices = [i for i in sorted(essential) if i != 0 and len(parts[i]) > 80]
                if not rule_indices:
                    break
                index = max(rule_indices, key=lambda i: len(parts[i]))
                # Предупреждение добавляется только один раз; иначе каждое
                # повторное сокращение снова увеличивало бы системный блок.
                warning = "[Часть постоянных правил не поместилась в контекст]\n"
                plain = parts[index].removeprefix(warning)
                parts[index] = warning + _strict_clip(
                    plain, max(24, len(plain) // 2), preserve_edges=True,
                )
        else:
            break
        truncated += 1

    # Свежие проверяемые факты важнее длинной старой истории.
    def system_priority(index: int) -> tuple[int, int]:
        part = parts[index]
        if any(mark in part for mark in (
            "Результаты текущего веб-поиска",
            "Текущий веб-поиск временно не выполнен",
            "Проверенные локальные знания",
        )):
            return (0, -index)
        if any(mark in part for mark in ("FACTUAL_EVIDENCE", "достоверн", "недоверенн")):
            return (1, -index)
        return (3, -index)

    optional = sorted((i for i in range(len(parts)) if i not in essential),
                      key=system_priority)

    def try_part(index: int) -> None:
        selected_parts.add(index)
        if estimator(render()) > budget:
            selected_parts.remove(index)

    # Сначала результаты текущей проверки, затем хотя бы несколько последних
    # реплик и только после этого второстепенные системные материалы.
    for index in optional:
        if system_priority(index)[0] <= 1:
            try_part(index)

    def try_turn(index: int) -> None:
        selected_dialogue.add(index)
        if estimator(render()) > budget:
            selected_dialogue.remove(index)

    recent = [i for i in range(max(0, len(dialogue) - 7), len(dialogue))
              if i not in selected_dialogue]
    for index in reversed(recent):
        try_turn(index)
    for index in optional:
        if system_priority(index)[0] > 1:
            try_part(index)

    # Оставшуюся вместимость используем для старой истории.
    for index in range(max(0, len(dialogue) - 7) - 1, -1, -1):
        if index not in selected_dialogue:
            try_turn(index)

    kept = render()
    # Защитный запас для патологически маленьких окон и Unicode-оценщика.
    # Длинный запрос сокращается с сохранением начала и конца, чтобы задача
    # и последняя цитата не исчезали из prompt.
    for _ in range(120):
        if estimator(kept) <= budget:
            break
        if latest_user is not None:
            current = partial_dialogue.get(latest_user, dialogue[latest_user])
            if len(current.content) > 24:
                partial_dialogue[latest_user] = ChatMessage(
                    current.role,
                    _strict_clip(current.content, max(24, len(current.content) * 2 // 3),
                                 preserve_edges=True),
                )
                truncated += 1
                kept = render()
                continue
        oversized = [i for i in sorted(selected_parts) if len(parts[i]) > 24]
        if not oversized:
            break
        index = max(oversized, key=lambda i: len(parts[i]))
        parts[index] = _strict_clip(parts[index], max(24, len(parts[index]) * 2 // 3),
                                    preserve_edges=True)
        truncated += 1
        kept = render()

    kept = render()
    after = estimator(kept)
    dropped = len(source) - len(kept)
    # Число потерянных частей system в счётчике не отражаем как потерянные
    # сообщения, иначе статистика ошибочно покажет удаление пользовательских реплик.
    return ContextBudgetResult(kept, before, after, budget, dropped, truncated)

__all__ = ["ContextBudgetResult", "apply_context_budget", "estimate_tokens"]