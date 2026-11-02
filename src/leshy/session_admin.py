from __future__ import annotations

import secrets
from typing import Any
import uuid

from .storage import utcnow


class SessionAdminService:
    """Постоянные действия над чатами Web UI и организация истории.

    Важно: сервис не участвует в transport/SSE и не меняет механизм генерации
    ответов. Он обслуживает только метаданные чатов: rename/pin/archive/lock,
    проекты, read-only sharing и удаление.
    """

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
            conn.execute(
                "CREATE TABLE IF NOT EXISTS chat_projects("
                "id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,name TEXT NOT NULL COLLATE NOCASE,"
                "created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(owner_id,name))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS session_projects("
                "session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,"
                "project_id TEXT NOT NULL REFERENCES chat_projects(id) ON DELETE CASCADE,"
                "moved_at TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_session_projects_project ON session_projects(project_id,session_id)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS session_shares("
                "token TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,"
                "owner_id TEXT NOT NULL,created_at TEXT NOT NULL,revoked_at TEXT)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_session_shares_session ON session_shares(session_id,owner_id,revoked_at)"
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

    @staticmethod
    def _session_select() -> str:
        return (
            "SELECT s.id,s.title,s.title_source,s.pinned,s.archived,s.locked,s.created_at,s.updated_at,"
            "sp.project_id,p.name AS project_name,"
            "EXISTS(SELECT 1 FROM session_shares sh WHERE sh.session_id=s.id AND sh.owner_id=s.owner_id AND sh.revoked_at IS NULL) AS shared "
            "FROM sessions s LEFT JOIN session_projects sp ON sp.session_id=s.id "
            "LEFT JOIN chat_projects p ON p.id=sp.project_id "
        )

    def list(self, owner_id: str = "web:local", *, archived: bool = False, limit: int | str = 100) -> list[dict[str, Any]]:
        owner = self._owner(owner_id)
        with self.storage.connection() as conn:
            rows = conn.execute(
                self._session_select()
                + "WHERE s.owner_id=? AND s.archived=? "
                + "ORDER BY s.pinned DESC,CASE WHEN sp.project_id IS NULL THEN 1 ELSE 0 END,"
                + "COALESCE(p.name,'') COLLATE NOCASE,s.updated_at DESC LIMIT ?",
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
                self._session_select() + "WHERE s.id=? AND s.owner_id=?",
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
            {
                "session_id": str(session_id),
                "owner_id": self._owner(owner_id),
                "pinned": bool(pinned),
                "archived": bool(archived),
                "locked": bool(locked),
            },
        )
        return self.get(owner_id, session_id)

    def projects(self, owner_id: str = "web:local") -> list[dict[str, Any]]:
        owner = self._owner(owner_id)
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT p.id,p.name,p.created_at,p.updated_at,count(sp.session_id) AS chat_count "
                "FROM chat_projects p LEFT JOIN session_projects sp ON sp.project_id=p.id "
                "WHERE p.owner_id=? GROUP BY p.id,p.name,p.created_at,p.updated_at "
                "ORDER BY p.name COLLATE NOCASE",
                (owner,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_project(self, owner_id: str, name: str) -> dict[str, Any]:
        owner = self._owner(owner_id)
        clean = " ".join(str(name or "").split()).strip()[:80]
        if not clean:
            raise ValueError("Название проекта обязательно")
        now = utcnow()
        project_id = str(uuid.uuid4())
        try:
            with self.storage.connection() as conn:
                conn.execute(
                    "INSERT INTO chat_projects(id,owner_id,name,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (project_id, owner, clean, now, now),
                )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise ValueError("Проект с таким названием уже существует") from exc
            raise
        self.storage.audit("chat_project.created", {"project_id": project_id, "owner_id": owner, "name": clean})
        return next(project for project in self.projects(owner) if project["id"] == project_id)

    def move_to_project(self, owner_id: str, session_id: str, project_id: str | None) -> dict[str, Any]:
        owner = self._owner(owner_id)
        current = self.get(owner, session_id)
        pid = str(project_id or "").strip()
        with self.storage.connection() as conn:
            if pid:
                project = conn.execute(
                    "SELECT id FROM chat_projects WHERE id=? AND owner_id=?",
                    (pid, owner),
                ).fetchone()
                if project is None:
                    raise ValueError("Проект не найден")
                conn.execute(
                    "INSERT INTO session_projects(session_id,project_id,moved_at) VALUES(?,?,?) "
                    "ON CONFLICT(session_id) DO UPDATE SET project_id=excluded.project_id,moved_at=excluded.moved_at",
                    (str(session_id), pid, utcnow()),
                )
            else:
                conn.execute("DELETE FROM session_projects WHERE session_id=?", (str(session_id),))
        self.storage.audit(
            "session.project_moved",
            {
                "session_id": str(session_id),
                "owner_id": owner,
                "project_id": pid or None,
                "previous_project_id": current.get("project_id"),
            },
        )
        return self.get(owner, session_id)

    def create_share(self, owner_id: str, session_id: str) -> dict[str, Any]:
        owner = self._owner(owner_id)
        self.get(owner, session_id)
        with self.storage.connection() as conn:
            existing = conn.execute(
                "SELECT token,created_at FROM session_shares "
                "WHERE session_id=? AND owner_id=? AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 1",
                (str(session_id), owner),
            ).fetchone()
            if existing is not None:
                return {"token": str(existing["token"]), "created_at": str(existing["created_at"])}
            token = secrets.token_urlsafe(32)
            created_at = utcnow()
            conn.execute(
                "INSERT INTO session_shares(token,session_id,owner_id,created_at,revoked_at) VALUES(?,?,?,?,NULL)",
                (token, str(session_id), owner, created_at),
            )
        self.storage.audit("session.shared", {"session_id": str(session_id), "owner_id": owner})
        return {"token": token, "created_at": created_at}

    def resolve_share(self, token: str) -> dict[str, Any]:
        clean = str(token or "").strip()[:200]
        if not clean:
            raise ValueError("Ссылка недействительна")
        with self.storage.connection() as conn:
            share = conn.execute(
                "SELECT session_id,owner_id,created_at FROM session_shares WHERE token=? AND revoked_at IS NULL",
                (clean,),
            ).fetchone()
            if share is None:
                raise ValueError("Общий доступ не найден или отозван")
            session = conn.execute(
                "SELECT id,title,created_at,updated_at FROM sessions WHERE id=? AND owner_id=?",
                (str(share["session_id"]), str(share["owner_id"])),
            ).fetchone()
            if session is None:
                raise ValueError("Чат не найден")
            messages = conn.execute(
                "SELECT role,content,created_at FROM messages WHERE session_id=? AND role IN ('user','assistant') ORDER BY id ASC",
                (str(share["session_id"]),),
            ).fetchall()
        return {
            "session": dict(session),
            "messages": [dict(row) for row in messages],
            "shared_at": str(share["created_at"]),
        }

    def revoke_share(self, owner_id: str, session_id: str) -> dict[str, Any]:
        owner = self._owner(owner_id)
        self.get(owner, session_id)
        with self.storage.connection() as conn:
            cur = conn.execute(
                "UPDATE session_shares SET revoked_at=? WHERE session_id=? AND owner_id=? AND revoked_at IS NULL",
                (utcnow(), str(session_id), owner),
            )
        self.storage.audit("session.share_revoked", {"session_id": str(session_id), "owner_id": owner})
        return {"revoked": int(cur.rowcount or 0) > 0, "id": str(session_id)}

    def delete(self, owner_id: str, session_id: str) -> dict[str, Any]:
        current = self.get(owner_id, session_id)
        with self.storage.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE id=? AND owner_id=?", (str(session_id), self._owner(owner_id)))
        self.storage.audit(
            "session.deleted",
            {"session_id": str(session_id), "owner_id": self._owner(owner_id), "title": current.get("title", "")},
        )
        return {"deleted": True, "id": str(session_id)}


__all__ = ["SessionAdminService"]
