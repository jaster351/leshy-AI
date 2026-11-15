"""Восстановление истории одной проверенной сессии без уничтожения данных.

Длинная история остаётся на диске. Режим «Авто» ищет относящиеся к вопросу
старые сообщения, а «Безлимит» позволяет искать по всей сохранённой сессии.
Это не означает неограниченное число токенов в одном запросе модели.
"""
from __future__ import annotations

import re

_TOKEN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{3,}", re.UNICODE)
_INITIAL_TOPIC = re.compile(
    r"(?i)(?:первоначальн|перв(?:ый|ому|ого) вопрос|"
    r"что я спросил(?:а)? вначале|сам(?:ый|ому) перв(?:ый|ому)|"
    r"initial question|original question|first question)"
)
_IGNORE = {
    "пожалуйста", "скажи", "расскажи", "ответь", "вопрос", "вопросу",
    "предыдущий", "первоначальный", "первоначальному", "помнишь",
    "вернись", "проверь", "посмотри", "сейчас", "можешь", "модель",
    "сделай", "снова", "почему", "когда", "какой", "где", "what",
    "could", "would", "please", "when", "which", "show", "them",
}


def _key_terms(message: str) -> tuple[str, ...]:
    found = []
    for word in _TOKEN.findall(str(message or "").casefold()):
        if word not in _IGNORE and word not in found:
            found.append(word)
    return tuple(sorted(found, key=len, reverse=True)[:4])


def load_session_context(conn, session_id: str, current_message: str, settings) -> list:
    """Возвратить реплики только своей сессии; проверка owner_id — до вызова."""
    recent_limit = max(4, min(200, int(settings.history_limit())))
    recent = list(conn.execute(
        "SELECT id,role,content FROM messages WHERE session_id=? "
        "ORDER BY id DESC LIMIT ?",
        (session_id, recent_limit),
    ).fetchall())
    if not recent:
        return []

    boundary = min(int(row["id"]) for row in recent)
    ids = {int(row["id"]) for row in recent}
    terms = _key_terms(current_message)
    initial_question = bool(_INITIAL_TOPIC.search(str(current_message)))
    if not terms and not initial_question:
        return recent

    # В автоматическом режиме ищем по недавнему прошлому; в режиме
    # «Безлимит» ограничений глубины нет. Читаем SQLite построчно.
    unlimited = bool(settings.unlimited_history())
    max_scan = None if unlimited else 600
    query = (
        "SELECT id,role,content FROM messages "
        "WHERE session_id=? AND id<? AND role='user' ORDER BY id DESC"
    )
    if max_scan is not None:
        query += " LIMIT ?"
        args = (session_id, boundary, max_scan)
    else:
        args = (session_id, boundary)

    found = []
    for row in conn.execute(query, args):
        content = str(row["content"]).casefold()
        score = sum(int(term in content) for term in terms)
        if score and (len(terms) >= 2 or len(terms[0]) >= 5):
            found.append((score, int(row["id"]), row))
    found.sort(key=lambda item: (item[0], item[1]), reverse=True)

    older = []
    for _, _, row in found[:8]:
        if int(row["id"]) not in ids:
            older.append(row)
            ids.add(int(row["id"]))

    if initial_question:
        # «Ответь на мой первоначальный вопрос» должно восстанавливать
        # сам вопрос даже через сотни реплик, а не угадывать его по ключам.
        originals = conn.execute(
            "SELECT id,role,content FROM messages WHERE session_id=? "
            "AND role='user' ORDER BY id ASC LIMIT 12",
            (session_id,),
        ).fetchall()
        initial = next(
            (row for row in originals
             if len(str(row["content"]).strip()) >= 15
             and not _INITIAL_TOPIC.search(str(row["content"]))),
            None,
        )
        if initial is not None and int(initial["id"]) not in ids:
            older.append(initial)
            ids.add(int(initial["id"]))

    # Не превращаем добавленную старую историю в системные инструкции.
    # Сохраняем исходные user-роли и хронологический порядок.
    return sorted(recent + older, key=lambda row: int(row["id"]), reverse=True)


__all__ = ["load_session_context"]
