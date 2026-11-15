"""Общее извлечение истории для Web, Telegram и фоновых задач.

В режиме «Безлимит» вся сохранённая история остаётся доступной через поиск
по старым сообщениям без ограничения глубины SQL-запроса; в prompt передаётся
только подходящая по теме часть, укладывающаяся в окно текущей модели.
"""
from __future__ import annotations

import re

_TOKEN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{3,}", re.UNICODE)
_IGNORE = {
    "пожалуйста", "скажи", "расскажи", "ответь", "вопрос", "вопросу",
    "предыдущий", "первоначальный", "первоначальному", "помнишь",
    "вернись", "проверь", "посмотри", "сейчас", "можешь", "модель",
    "сделай", "снова", "почему", "когда", "какой", "где", "what",
    "could", "would", "please", "when", "which", "show", "them",
}


def _key_terms(message: str) -> tuple[str, ...]:
    found = []
    for word in _TOKEN.findall(str(message or "").lower()):
        if word in _IGNORE or word in found:
            continue
        if len(word) >= 3:
            found.append(word)
    return tuple(sorted(found, key=len, reverse=True)[:4])


def load_session_context(conn, session_id: str, current_message: str, settings) -> list:
    """Читать только свою сессию, проверенную вызывающим кодом по owner_id."""
    limit = max(4, min(200, int(settings.history_limit())))
    recent = list(conn.execute(
        "SELECT id,role,content FROM messages WHERE session_id=? "
        "ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    ).fetchall())
    if not settings.unlimited_history() or not recent:
        return recent

    # История хранится без удаления. Поиск выполняется по всей старой истории,
    # а не путём загрузки тысячи диалогов в оперативную память.
    boundary = min(int(row["id"]) for row in recent)
    ids = {int(row["id"]) for row in recent}
    older = []
    for term in _key_terms(current_message):
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        records = conn.execute(
            "SELECT id,role,content FROM messages "
            "WHERE session_id=? AND id<? AND role='user' "
            "AND lower(content) LIKE ? ESCAPE '\\' "
            "ORDER BY id DESC LIMIT 6",
            (session_id, boundary, f"%{escaped}%"),
        ).fetchall()
        for row in records:
            if int(row["id"]) not in ids:
                ids.add(int(row["id"]))
                older.append(row)
        if len(older) >= 12:
            break
    return sorted(recent + older[:12], key=lambda row: int(row["id"]), reverse=True)


__all__ = ["load_session_context"]
