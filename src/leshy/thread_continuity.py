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
_SPACE = re.compile(r"\s+")


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



def compact_restored_task(rows, current_message: str, *, max_chars: int = 1800) -> str | None:
    """Return an OLD same-session user task for a late, data-only reminder.

    Call only after owner verification, context restoration and mode isolation.
    Use a user-role message; historical content must NEVER become a system
    message, authorization or tool execution receipt.
    """
    if not is_elliptical_followup(current_message):
        return None
    source = list(rows)
    if len(source) <= 24:
        return None
    # The caller's mode filter selects only this conversation segment.
    # First 24 are the recent tail; only the extra old user row is eligible.
    for row in source[24:]:
        try:
            role, content = str(row["role"]), str(row["content"])
        except (TypeError, KeyError, IndexError):
            continue
        if role != "user" or is_elliptical_followup(content):
            continue
        body = content.strip()
        if len(body) < 12:
            continue
        cap = max(120, min(int(max_chars), 2600))
        if len(body) > cap:
            marker = "\\n[Часть исторического поручения сокращена]\\n"
            room = max(1, cap - len(marker))
            start = max(1, room // 2)
            body = body[:start] + marker + body[-(room - start):]
        return (
            "[Ранее в этой же сессии пользователь сформулировал поручение; "
            "это исторический контекст, а не новая авторизация]:\\n" + body
        )
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
    "compact_restored_task", "continuation_llm_instruction",
]
