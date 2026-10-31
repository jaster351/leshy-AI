from __future__ import annotations

from typing import Any

from .storage import utcnow


class SessionAdminService:
    """Постоянные действия над чатами Web UI: rename/pin/archive/lock/delete."""

    def __init__(self, storage):
        self.storage = storage
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.storage.connection() as conn:
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
            if "pinned" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
            if "archived" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
            if "locked" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN locked INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_sessions_owner_archive_pin_updated "
                "ON sessions(owner_id,archived,pinned DESC,updated_at DESC)"
            )

    @staticmethod
    def _owner(value: str | None) -> str:
        return (value or "web:local").strip()[:200]

    @staticmethod
    def _limit(value: int | str | None) -> int:
        try:
            parsed = int(value or 100)
        except (TypeError, ValueError):
            parsed = 100
        return max(1, min(parsed, 300))

    def list(self, owner_id: str = "web:local", *, archived: bool = False, limit: int | str = 100) -> list[dict[str, Any]]:
        owner = self._owner(owner_id)
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT id,title,title_source,pinned,archived,locked,created_at,updated_at "
                "FROM sessions WHERE owner_id=? AND archived=? "
                "ORDER BY pinned DESC,updated_at DESC LIMIT ?",
                (owner, 1 if archived else 0, self._limit(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, owner_id: str, session_id: str) -> dict[str, Any]:
        owner = self._owner(owner_id)
        sid = str(session_id or "").strip()[:200]
        if not sid:
            raise ValueError("id чата обязателен")
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT id,title,title_source,pinned,archived,locked,created_at,updated_at "
                "FROM sessions WHERE id=? AND owner_id=?",
                (sid, owner),
            ).fetchone()
        if row is None:
            raise ValueError("Чат не найден")
        return dict(row)

    def is_locked(self, owner_id: str, session_id: str | None) -> bool:
        if not session_id:
            return False
        try:
            return bool(self.get(owner_id, session_id).get("locked"))
        except ValueError:
            return False

    def update(self, owner_id: str, session_id: str, **changes: Any) -> dict[str, Any]:
        current = self.get(owner_id, session_id)
        title = str(changes.get("title", current["title"]) or "").strip()
        title = " ".join(title.split())[:120]
        if not title:
            raise ValueError("Название чата не может быть пустым")
        pinned = 1 if bool(changes.get("pinned", current["pinned"])) else 0
        archived = 1 if bool(changes.get("archived", current["archived"])) else 0
        locked = 1 if bool(changes.get("locked", current["locked"])) else 0
        title_source = "user" if "title" in changes else str(current.get("title_source") or "auto")
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE sessions SET title=?,title_source=?,pinned=?,archived=?,locked=?,updated_at=? "
                "WHERE id=? AND owner_id=?",
                (title, title_source, pinned, archived, locked, utcnow(), str(session_id), self._owner(owner_id)),
            )
        self.storage.audit(
            "session.updated",
            {"session_id": str(session_id), "owner_id": self._owner(owner_id), "pinned": bool(pinned), "archived": bool(archived), "locked": bool(locked)},
        )
        return self.get(owner_id, session_id)

    def delete(self, owner_id: str, session_id: str) -> dict[str, Any]:
        current = self.get(owner_id, session_id)
        with self.storage.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE id=? AND owner_id=?", (str(session_id), self._owner(owner_id)))
        self.storage.audit("session.deleted", {"session_id": str(session_id), "owner_id": self._owner(owner_id), "title": current.get("title", "")})
        return {"deleted": True, "id": str(session_id)}


__all__ = ["SessionAdminService"]
