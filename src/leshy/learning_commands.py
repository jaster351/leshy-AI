from __future__ import annotations

import re
from typing import Any


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_DIRECT_RE = re.compile(
    r"^\s*(?:леш(?:ий|а)[,!]?\s+)?(?:пожалуйста[,!]?\s+)?(?:"
    r"изучи|изучай|исследуй|выучи|освой|научись|обучись|самообучись|"
    r"запомни\s+как\s+знание|добавь\s+(?:это\s+)?в\s+(?:базу\s+)?знани[яй]|"
    r"пополни\s+(?:свои\s+)?знания\s+(?:об|о|по)"
    r")\b[\s:—,-]*",
    re.IGNORECASE,
)
_INDIRECT_RE = re.compile(
    r"\b(?:я\s+хочу[, ]+(?:чтобы\s+)?ты|прошу\s+тебя|тебе\s+нужно|тебе\s+надо)\s+"
    r"(?:изучить|исследовать|выучить|освоить|научиться)\b[\s:—,-]*",
    re.IGNORECASE,
)
_EN_RE = re.compile(
    r"^\s*(?:leshy[,!]?\s+)?(?:please\s+)?(?:learn|study|research|remember\s+as\s+knowledge)\b[\s:—,-]*",
    re.IGNORECASE,
)


def explicit_learning_target(message: str) -> str | None:
    """Вернуть тему явной команды обучения либо None.

    Это намеренно детерминированная маршрутизация: решение пользователя «изучи»
    не должно зависеть от того, согласится ли языковая модель считать себя
    способной к обучению.
    """
    text = " ".join(str(message or "").split()).strip()
    if not text:
        return None
    match = _DIRECT_RE.search(text) or _EN_RE.search(text)
    if match:
        target = text[match.end():].strip(" .,:;—-")
        return target or text
    match = _INDIRECT_RE.search(text)
    if match:
        target = text[match.end():].strip(" .,:;—-")
        return target or text
    return None


def is_explicit_learning_command(message: str) -> bool:
    return explicit_learning_target(message) is not None


def execute_explicit_learning(app, owner_id: str, message: str) -> dict[str, Any]:
    target = explicit_learning_target(message)
    if target is None:
        raise ValueError("Сообщение не является явной командой обучения")

    urls = []
    seen: set[str] = set()
    for raw in _URL_RE.findall(str(message or "")):
        url = raw.rstrip(".,);]}")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    material_jobs: list[dict[str, Any]] = []
    research_ids: list[int] = []
    if urls:
        for url in urls[:8]:
            material_jobs.append(app.self_learning.queue_material(owner_id, url, title=target[:200]))
    else:
        research_ids.append(app.self_learning.queue_topic(target, reason="явная команда пользователя: изучить"))

    learning_enabled = bool(app.learning.is_enabled())
    if material_jobs:
        ids = [str(item.get("id") or item.get("task_id") or "?") for item in material_jobs]
        answer = (
            "Принял. Материал поставлен на изучение"
            + (f" (задача {', '.join(ids)})" if ids else "")
            + ". После обработки новые сведения пройдут проверку и попадут в знания Лешего."
        )
    else:
        answer = (
            f"Принял. Поставил тему «{target[:240]}» на исследование"
            f" (задача #{research_ids[0]}). После поиска сведения будут сохранены как candidate, проверены и затем смогут стать доверенными знаниями."
        )

    # Явная команда пользователя выполняется независимо от автономного переключателя.
    # Статус сообщаем только для диагностики; выключенный idle-learning не отменяет задачу.
    if not learning_enabled:
        answer += " Автономное фоновое обучение сейчас выключено, но эта явно заданная Вами учебная задача всё равно будет выполнена."

    return {
        "answer": answer,
        "target": target,
        "urls": urls,
        "material_jobs": material_jobs,
        "research_task_ids": research_ids,
        "learning_enabled": learning_enabled,
    }


__all__ = ["explicit_learning_target", "is_explicit_learning_command", "execute_explicit_learning"]
