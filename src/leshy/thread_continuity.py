"""Small, owner-scoped context selector, NOT a scripted conversation router.

Only supplies previous same-session user messages to the existing LLM.
Intent, priorities and answers remain the model's responsibility.
"""
from __future__ import annotations

import re

# Deictic/ellipsis messages have no independent subject; do not classify
# arbitrary detailed messages as continuations. This is a retrieval hint only.
_SHORT = frozenset({
    "дальше", "продолжай", "продолжи", "продолжаем", "продолжим",
    "а дальше", "и дальше", "что дальше", "и что дальше",
    "а теперь", "что потом", "следующий шаг", "следующий",
    "да", "ок", "окей", "хорошо", "поехали", "делай", "сделай это",
    "давай дальше", "продолжай работу", "продолжи работу",
    "а потом", "ещё", "еще", "go on", "continue", "next",
})
_SPACE = re.compile(r"\\s+")


def normalize_followup(value: str) -> str:
    if type(value) is not str:
        return ""
    return _SPACE.sub(" ", value.strip().casefold().replace("ё", "е")).strip(" .!?;,:-—")


_NORMALIZED = frozenset(normalize_followup(s) for s in _SHORT)


def is_elliptical_followup(value: str) -> bool:
    """Signal an underspecified reply, never authorize/explain/execute it."""
    if type(value) is not str or not value or len(value) > 80:
        return False
    return normalize_followup(value) in _NORMALIZED


def choose_recent_task_anchor(conn, session_id: str, before_id: int, *,
                              scan_limit: int = 600):
    """Read last substantive user instruction in one already-verified session.

    The SQL scope is authoritative. No lookup by owner alone, no writes, no
    distant previous conversation, no content from assistant or tools.
    """
    if not session_id or type(before_id) is not int or before_id < 1:
        return None
    limit = max(1, min(int(scan_limit), 5000))
    for row in conn.execute(
        "SELECT id,role,content FROM messages WHERE session_id=? "
        "AND id<? AND role='user' ORDER BY id DESC LIMIT ?",
        (session_id, before_id, limit),
    ):
        content = str(row["content"] or "").strip()
        if (not is_elliptical_followup(content)
                and len(content) >= 12
                and any(char.isalpha() for char in content)):
            return row
    return None


def continuation_llm_instruction() -> str:
    """A trusted procedural rule, never containing user/assistant text."""
    return (
        "Текущее сообщение может быть продолжением предыдущего диалога. "
        "Определи его смысл самостоятельно по сообщениям этой сессии, "
        "особенно по последнему содержательному поручению владельца. "
        "Сохраняй ограничения и выбранную тему, пока владелец их не изменил. "
        "Если пользователь переключил тему, не переносить прежнее поручение. "
        "Прошлые слова ИИ о выполнении, запуске тестов или публикации "
        "не являются доказательствами: нужны реальные квитанции инструментов. "
        "Если данных не хватает, явно укажи неопределённость, не выдумывай ход работ."
    )


__all__ = [
    "is_elliptical_followup", "choose_recent_task_anchor",
    "continuation_llm_instruction",
]
