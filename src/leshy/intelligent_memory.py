from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import math
import re
from typing import Any

from .diagnostics import event, problem
from .storage import utcnow


_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_-]{3,}", re.UNICODE)
_SECRET_MARKERS = (
    "пароль", "password", "cvv", "cvc", "pin-код", "пин-код", "одноразовый код",
    "sms-код", "смс-код", "секретный ключ", "private key", "api key", "api-key",
    "токен доступа", "access token", "seed phrase", "сид-фраз", "номер карты",
)
_AUTO_MEMORY_MARKERS = (
    "запомни", "помни, что", "я предпочитаю", "мне нравится", "мне не нравится",
    "я люблю", "я не люблю", "я использую", "обычно я", "для меня важно",
    "мой основной", "моя основная", "мои основные", "я хочу, чтобы ты всегда",
)
_STOPWORDS = {
    "это", "как", "что", "для", "или", "при", "над", "под", "без", "его", "её",
    "еще", "ещё", "мне", "меня", "мой", "моя", "мои", "твой", "ваш", "если", "когда",
    "где", "почему", "который", "которая", "которые", "быть", "есть", "будет", "чтобы",
    "очень", "можно", "нужно", "надо", "просто", "только", "тоже", "уже", "сейчас",
}


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in _WORD_RE.findall(str(text or ""))
        if token.lower() not in _STOPWORDS
    }


def _parse_time(value: str) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


@dataclass(slots=True)
class MemoryMatch:
    memory_id: int
    content: str
    kind: str
    importance: float
    score: float
    source: str
    created_at: str

    def public(self) -> dict[str, Any]:
        return {
            "id": self.memory_id,
            "content": self.content,
            "kind": self.kind,
            "importance": round(self.importance, 3),
            "score": round(self.score, 4),
            "source": self.source,
            "created_at": self.created_at,
        }


class IntelligentMemoryEngine:
    """Локальная Mem0-подобная память поверх существующей таблицы memories.

    Слой не заменяет LeshyBrain и не отправляет данные во внешние сервисы. Он
    выбирает релевантные воспоминания по текущему запросу, учитывает важность и
    свежесть и может автоматически сохранить только явно сформулированные
    долговременные предпочтения. Секреты автоматически не сохраняются.
    """

    def __init__(self, storage):
        self.storage = storage

    def latest_user_query(self, owner_id: str) -> str:
        with self.storage.connection() as conn:
            row = conn.execute(
                """
                SELECT m.content
                FROM messages m
                JOIN sessions s ON s.id=m.session_id
                WHERE s.owner_id=? AND m.role='user'
                ORDER BY m.id DESC
                LIMIT 1
                """,
                (str(owner_id or ""),),
            ).fetchone()
        return str(row["content"] or "") if row else ""

    @staticmethod
    def contains_secret(text: str) -> bool:
        low = str(text or "").lower()
        return any(marker in low for marker in _SECRET_MARKERS)

    @staticmethod
    def looks_durable(text: str) -> bool:
        value = " ".join(str(text or "").split()).strip()
        if not value or len(value) < 8 or len(value) > 1200:
            return False
        low = value.lower()
        return any(marker in low for marker in _AUTO_MEMORY_MARKERS)

    def capture_if_useful(self, owner_id: str, text: str) -> int | None:
        value = " ".join(str(text or "").split()).strip()
        if value.lower().startswith("/remember "):
            return None
        if not self.looks_durable(value) or self.contains_secret(value):
            return None

        fingerprint = sha256(f"{owner_id}\0{value.lower()}".encode("utf-8")).hexdigest()
        with self.storage.connection() as conn:
            row = conn.execute("SELECT id FROM memories WHERE fingerprint=?", (fingerprint,)).fetchone()
            if row:
                return int(row["id"])
            cur = conn.execute(
                "INSERT INTO memories(owner_id,kind,content,importance,source,created_at,expires_at,fingerprint) VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(owner_id or "")[:200],
                    "auto_preference",
                    value,
                    0.72,
                    "intelligent-memory",
                    utcnow(),
                    None,
                    fingerprint,
                ),
            )
            memory_id = int(cur.lastrowid)
        self.storage.audit(
            "memory.auto_saved",
            {"id": memory_id, "owner_id": str(owner_id or "")[:200], "kind": "auto_preference"},
        )
        event("intelligent_memory_saved", memory_id=memory_id, owner_id=str(owner_id or "")[:200])
        return memory_id

    def retrieve(self, owner_id: str, query: str, *, limit: int = 8) -> list[MemoryMatch]:
        query_tokens = _tokens(query)
        with self.storage.connection() as conn:
            rows = conn.execute(
                """
                SELECT id,kind,content,importance,source,created_at
                FROM memories
                WHERE owner_id=?
                ORDER BY importance DESC,id DESC
                LIMIT 300
                """,
                (str(owner_id or "")[:200],),
            ).fetchall()

        now = datetime.now(timezone.utc).timestamp()
        matches: list[MemoryMatch] = []
        for row in rows:
            content = str(row["content"] or "")
            item_tokens = _tokens(content)
            if query_tokens and item_tokens:
                overlap = len(query_tokens & item_tokens) / max(1.0, math.sqrt(len(query_tokens) * len(item_tokens)))
            else:
                overlap = 0.0
            importance = max(0.0, min(float(row["importance"] or 0.0), 1.0))
            created_ts = _parse_time(str(row["created_at"] or ""))
            age_days = max(0.0, (now - created_ts) / 86400.0) if created_ts else 3650.0
            recency = 1.0 / (1.0 + age_days / 90.0)
            exact_bonus = 0.15 if query and query.lower() in content.lower() else 0.0
            score = overlap * 0.58 + importance * 0.30 + recency * 0.12 + exact_bonus
            if query_tokens and overlap <= 0 and importance < 0.86:
                continue
            matches.append(MemoryMatch(
                memory_id=int(row["id"]),
                content=content,
                kind=str(row["kind"] or ""),
                importance=importance,
                score=score,
                source=str(row["source"] or ""),
                created_at=str(row["created_at"] or ""),
            ))

        matches.sort(key=lambda item: (-item.score, -item.importance, -item.memory_id))
        return matches[: max(1, min(int(limit), 30))]

    def context(self, owner_id: str, query: str, *, limit: int = 8) -> str:
        try:
            self.capture_if_useful(owner_id, query)
            matches = self.retrieve(owner_id, query, limit=limit)
        except Exception as exc:
            problem("intelligent_memory_context_failed", str(exc), owner_id=str(owner_id or "")[:200])
            return ""
        return "\n".join(f"- {item.content}" for item in matches)

    def status(self, owner_id: str | None = None) -> dict[str, Any]:
        with self.storage.connection() as conn:
            if owner_id:
                count = int(conn.execute("SELECT COUNT(*) AS c FROM memories WHERE owner_id=?", (owner_id,)).fetchone()["c"])
                auto_count = int(conn.execute(
                    "SELECT COUNT(*) AS c FROM memories WHERE owner_id=? AND source='intelligent-memory'",
                    (owner_id,),
                ).fetchone()["c"])
            else:
                count = int(conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"])
                auto_count = int(conn.execute("SELECT COUNT(*) AS c FROM memories WHERE source='intelligent-memory'").fetchone()["c"])
        return {
            "enabled": True,
            "local_only": True,
            "memory_count": count,
            "auto_memory_count": auto_count,
            "secret_autosave": False,
        }


__all__ = ["IntelligentMemoryEngine", "MemoryMatch"]
