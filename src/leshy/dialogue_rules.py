from __future__ import annotations

"""Verified, owner-scoped dialogue rules set explicitly through the chat.

A model's promise to remember is not a database write. Only a direct user
instruction matching this narrow intent is persisted, and a receipt is returned
only after a read-back. Stored rules affect presentation, not tool permissions.
"""

from hashlib import sha256
import re

from .storage import utcnow


_MAX_RULE_LENGTH = 1200
_MAX_RULES = 12
_MAX_TOTAL_CHARS = 3600
_KIND = "dialogue_rule"
_PREFIXES = (
    "установи постоянное правило",
    "установи следующие правила",
    "установи правила общения",
    "постоянное правило общения",
    "категорически запрещаю",
    "запомни правило",
    "запомни следующие правила",
    "сохрани правило",
    "сохрани следующие правила",
    "сохрани как постоянное правило",
    "хочу, чтобы ты всегда",
    "я хочу, чтобы ты всегда",
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_ -]?key|password|пароль|token|токен|секретный ключ)\s*[:=]\s*\S+"
)


def is_direct_rule_request(message: str) -> bool:
    """Only the user's direct request; never scan quoted pages or model output."""
    # Do not promote pasted Markdown quotes, list items or headers to direct
    # instructions. Only whitespace and a direct vocative are allowed.
    head = str(message or "").lstrip().casefold()
    if head.startswith("леший,"):
        head = head[len("леший,"):].lstrip()
    return any(head.startswith(prefix) for prefix in _PREFIXES)


def save_direct_rule(storage, owner_id: str, message: str) -> tuple[int | None, str]:
    """Persist one explicit rule, or explain why nothing was saved."""
    if not is_direct_rule_request(message):
        return None, "Это сообщение не является явным запросом на сохранение правила."
    content = str(message or "").strip()
    owner = str(owner_id or "").strip()[:200]
    if not owner:
        return None, "Не удалось определить профиль. Правило не сохранено."
    if owner.casefold() in {"web:local", "shared", "standalone"}:
        return None, (
            "Правило не сохранено: используется общий или анонимный профиль. "
            "Сначала войдите в индивидуальный профиль, чтобы не смешивать память пользователей."
        )
    if len(content) > _MAX_RULE_LENGTH:
        return None, (
            f"Правило не сохранено: сообщение длиннее {_MAX_RULE_LENGTH} символов. "
            "Отправьте правила отдельными короткими сообщениями, чтобы каждое "
            "можно было надёжно загрузить в контекст модели."
        )
    if _SECRET_ASSIGNMENT.search(content):
        return None, "Правило не сохранено: сообщение может содержать секрет. Удалите секрет и повторите."
    canonical = " ".join(content.casefold().split())
    fingerprint = sha256((owner + "\0dialogue_rule\0" + canonical).encode("utf-8")).hexdigest()
    with storage.connection() as conn:
        found = conn.execute(
            "SELECT id FROM memories WHERE owner_id=? AND kind=? AND fingerprint=?",
            (owner, _KIND, fingerprint),
        ).fetchone()
        if found:
            memory_id = int(found["id"])
            return memory_id, f"Это правило уже сохранено для текущего профиля. ID записи: {memory_id}."
        counts = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(content)),0) AS chars "
            "FROM memories WHERE owner_id=? AND kind=?",
            (owner, _KIND),
        ).fetchone()
        if int(counts["n"]) >= _MAX_RULES or int(counts["chars"]) + len(content) > _MAX_TOTAL_CHARS:
            return None, (
                "Правило не сохранено: достигнут лимит постоянных правил для "
                "контекстного окна модели. Сначала сократите или объедините прежние правила."
            )
        cur = conn.execute(
            "INSERT INTO memories(owner_id,kind,content,importance,source,created_at,expires_at,fingerprint) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (owner, _KIND, content, 1.0, "explicit-chat-rule", utcnow(), None, fingerprint),
        )
        memory_id = int(cur.lastrowid)
        verified = conn.execute(
            "SELECT content FROM memories WHERE id=? AND owner_id=? AND kind=?",
            (memory_id, owner, _KIND),
        ).fetchone()
        if verified is None or str(verified["content"]) != content:
            raise RuntimeError("Не удалось подтвердить запись постоянного правила")
    storage.audit("dialogue_rule.saved", {"id": memory_id, "owner_id": owner})
    return memory_id, (
        f"Правило сохранено в локальной памяти текущего профиля. ID записи: {memory_id}. "
        "Оно будет включаться в контекст будущих ответов этого профиля. "
        "Синхронизация с другими профилями или устройствами этим не подтверждается."
    )


def persistent_rule_context(storage, owner_id: str) -> str:
    """Load every accepted rule, independently of relevance retrieval and learning."""
    owner = str(owner_id or "").strip()[:200]
    if not owner:
        return ""
    with storage.connection() as conn:
        rows = conn.execute(
            "SELECT content FROM memories WHERE owner_id=? AND kind=? "
            "ORDER BY id ASC LIMIT ?",
            (owner, _KIND, _MAX_RULES),
        ).fetchall()
    if not rows:
        return ""
    return (
        "Постоянные правила общения, явно записанные пользователем именно этого профиля. "
        "Учитывай их при формировании ответа, но не считай их разрешением на действия с ОС, "
        "доступ к чужим данным или изменение ограничений безопасности. "
        "Не утверждай, что правило сохранено, если запись не была подтверждена:\n"
        + "\n".join(f"{i}. {str(row['content'])}" for i, row in enumerate(rows, 1))
    )


def is_rule_inspection_request(message: str) -> bool:
    """Answer a question about stored rules from SQLite, not model guesses."""
    raw = str(message or "").strip()
    if not raw or len(raw) > 320 or "\n" in raw or "\r" in raw:
        return False
    text = " ".join(raw.casefold().replace("ё", "е").split())
    text = re.sub(r"^леший[,!:]?\s+", "", text)
    if not re.match(r"^(?:какие|покажи|перечисли|проверь|ты\s+помнишь|что\s+ты\s+сохранил)", text):
        return False
    # A directive to save a document is not a question about older rules.
    # In particular, "проверь, что инструкция сохранена" must not list rule #6.
    from .owner_knowledge_capture import (is_owner_document_save_request,
                                         is_owner_document_status_request)
    if is_owner_document_save_request(message) or is_owner_document_status_request(message):
        return False
    return bool(
        (("правила" in text or "правило" in text) and
         ("общения" in text or "постоянн" in text or "сохраненн" in text) and
         any(term in text for term in ("помниш", "покажи", "перечисли", "сохран", "какие", "проверь")))
        or text.startswith("покажи мои правила")
    )


def verified_rule_report(storage, owner_id: str) -> str:
    """Show only rows actually present for this owner, including provenance."""
    owner = str(owner_id or "").strip()[:200]
    if not owner:
        return "Не удалось определить текущий профиль; проверить правила нельзя."
    with storage.connection() as conn:
        rows = conn.execute(
            "SELECT id,content,created_at,source FROM memories "
            "WHERE owner_id=? AND kind=? ORDER BY id ASC LIMIT ?",
            (owner, _KIND, _MAX_RULES),
        ).fetchall()
    if not rows:
        return (
            "В долговременной памяти этого профиля нет подтверждённых постоянных "
            "правил общения. Слова модели «запомнил» сами по себе не являются "
            "доказательством записи."
        )
    return (
        "Подтверждённые записи правил общения из локальной базы этого профиля:\n"
        + "\n".join(
            f"{i}. ID {row['id']}, источник: {row['source']}, "
            f"дата: {row['created_at']}; правило: {row['content']}"
            for i, row in enumerate(rows, 1)
        )
        + "\nПрименение моделью и синхронизация с другими профилями отдельно не проверены."
    )


__all__ = ["is_direct_rule_request", "save_direct_rule", "persistent_rule_context", "is_rule_inspection_request", "verified_rule_report"]
