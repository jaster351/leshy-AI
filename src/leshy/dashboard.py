from __future__ import annotations

from pathlib import Path
from typing import Any

from .diagnostics import recent_problems
from .storage import utcnow


class DashboardService:
    """Данные и действия полноценного Web UI без дублирования бизнес-логики LeshyApp."""

    def __init__(self, app):
        self.app = app
        self.storage = app.storage

    @staticmethod
    def _owner(value: str | None) -> str:
        return (value or "web:local").strip()[:200]

    @staticmethod
    def _limit(value: int | str | None, default: int = 50, maximum: int = 200) -> int:
        try:
            parsed = int(value or default)
        except (TypeError, ValueError):
            parsed = default
        return max(1, min(parsed, maximum))

    def overview(self, owner_id: str = "web:local") -> dict[str, Any]:
        owner = self._owner(owner_id)
        status = self.app.status()
        with self.storage.connection() as conn:
            task_rows = conn.execute(
                "SELECT status,count(*) AS n FROM user_tasks WHERE owner_id=? GROUP BY status",
                (owner,),
            ).fetchall()
            research_rows = conn.execute(
                "SELECT status,count(*) AS n FROM research_tasks GROUP BY status"
            ).fetchall()
        return {
            "status": status,
            "tasks": {str(row["status"]): int(row["n"]) for row in task_rows},
            "research": {str(row["status"]): int(row["n"]) for row in research_rows},
        }

    def sessions(self, owner_id: str = "web:local", limit: int = 50) -> list[dict[str, Any]]:
        owner = self._owner(owner_id)
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT id,title,created_at,updated_at FROM sessions WHERE owner_id=? ORDER BY updated_at DESC LIMIT ?",
                (owner, self._limit(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def memories(self, owner_id: str = "web:local", limit: int = 100) -> list[dict[str, Any]]:
        return self.app.memories(self._owner(owner_id), limit=self._limit(limit, 100, 200))

    def knowledge(self, status: str = "all", limit: int = 100) -> list[dict[str, Any]]:
        status = str(status or "all").strip().lower()
        allowed = {"candidate", "trusted", "conflict", "rejected", "stale"}
        with self.storage.connection() as conn:
            if status in allowed:
                rows = conn.execute(
                    "SELECT id,topic,claim,source_url,source_title,captured_at,verified_at,confidence,status FROM knowledge WHERE status=? ORDER BY id DESC LIMIT ?",
                    (status, self._limit(limit, 100, 300)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id,topic,claim,source_url,source_title,captured_at,verified_at,confidence,status FROM knowledge ORDER BY id DESC LIMIT ?",
                    (self._limit(limit, 100, 300),),
                ).fetchall()
        return [dict(row) for row in rows]

    def research_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT id,query,reason,status,attempts,last_error,created_at,updated_at FROM research_tasks ORDER BY id DESC LIMIT ?",
                (self._limit(limit, 100, 300),),
            ).fetchall()
        return [dict(row) for row in rows]

    def learning_history(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT id,kind,title,url,site,detail,started_at FROM learning_history ORDER BY id DESC LIMIT ?",
                (self._limit(limit, 100, 300),),
            ).fetchall()
        return [dict(row) for row in rows]

    def tasks(self, owner_id: str = "web:local", status: str = "all", limit: int = 100) -> list[dict[str, Any]]:
        owner = self._owner(owner_id)
        status = str(status or "all").strip().lower()
        allowed = {"open", "in_progress", "done", "cancelled"}
        with self.storage.connection() as conn:
            if status in allowed:
                rows = conn.execute(
                    "SELECT * FROM user_tasks WHERE owner_id=? AND status=? ORDER BY priority DESC,updated_at DESC,id DESC LIMIT ?",
                    (owner, status, self._limit(limit, 100, 300)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM user_tasks WHERE owner_id=? ORDER BY CASE status WHEN 'in_progress' THEN 0 WHEN 'open' THEN 1 WHEN 'done' THEN 2 ELSE 3 END,priority DESC,updated_at DESC,id DESC LIMIT ?",
                    (owner, self._limit(limit, 100, 300)),
                ).fetchall()
        return [dict(row) for row in rows]

    def create_task(
        self,
        *,
        owner_id: str = "web:local",
        title: str,
        details: str = "",
        priority: int = 0,
        due_at: str | None = None,
    ) -> dict[str, Any]:
        owner = self._owner(owner_id)
        title = " ".join(str(title or "").split()).strip()
        if not title:
            raise ValueError("Название задачи обязательно")
        details = str(details or "").strip()[:10000]
        priority = max(-10, min(int(priority or 0), 10))
        now = utcnow()
        with self.storage.connection() as conn:
            cur = conn.execute(
                "INSERT INTO user_tasks(owner_id,title,details,status,priority,due_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (owner, title[:500], details, "open", priority, due_at or None, now, now),
            )
            task_id = int(cur.lastrowid)
            row = conn.execute("SELECT * FROM user_tasks WHERE id=?", (task_id,)).fetchone()
        self.storage.audit("task.created", {"id": task_id, "owner_id": owner, "title": title[:500]})
        return dict(row)

    def update_task(self, *, owner_id: str = "web:local", task_id: int, **changes: Any) -> dict[str, Any]:
        owner = self._owner(owner_id)
        allowed_status = {"open", "in_progress", "done", "cancelled"}
        with self.storage.connection() as conn:
            row = conn.execute("SELECT * FROM user_tasks WHERE id=? AND owner_id=?", (int(task_id), owner)).fetchone()
            if row is None:
                raise ValueError("Задача не найдена")
            title = " ".join(str(changes.get("title", row["title"])).split()).strip()[:500]
            if not title:
                raise ValueError("Название задачи обязательно")
            details = str(changes.get("details", row["details"]) or "").strip()[:10000]
            status = str(changes.get("status", row["status"]) or "open").strip().lower()
            if status not in allowed_status:
                raise ValueError("Некорректный статус задачи")
            priority = max(-10, min(int(changes.get("priority", row["priority"]) or 0), 10))
            due_at = changes.get("due_at", row["due_at"])
            conn.execute(
                "UPDATE user_tasks SET title=?,details=?,status=?,priority=?,due_at=?,updated_at=? WHERE id=? AND owner_id=?",
                (title, details, status, priority, due_at or None, utcnow(), int(task_id), owner),
            )
            updated = conn.execute("SELECT * FROM user_tasks WHERE id=?", (int(task_id),)).fetchone()
        self.storage.audit("task.updated", {"id": int(task_id), "owner_id": owner, "status": status})
        return dict(updated)

    def capabilities(self) -> list[dict[str, Any]]:
        status = self.app.status()
        return [
            {"id": "chat", "title": "Локальный чат", "ready": bool(status.get("model_ready")), "detail": "Qwen3-8B через локальный runtime"},
            {"id": "search", "title": "Интернет-поиск", "ready": True, "detail": "Live search + сохранение candidate-знаний"},
            {"id": "memory", "title": "Долговременная память", "ready": True, "detail": "Изолированная память пользователя в SQLite"},
            {"id": "learning", "title": "Самообучение", "ready": True, "detail": "Управляемый research → candidate → verification → trusted"},
            {"id": "telegram", "title": "Telegram", "ready": bool((status.get("telegram") or {}).get("configured")), "detail": "Long polling, список разрешённых Telegram ID"},
            {"id": "tasks", "title": "Задачи", "ready": True, "detail": "Локальный список задач с состояниями и приоритетом"},
            {"id": "logs", "title": "Диагностика", "ready": True, "detail": "Санитизированные runtime/probleмные логи"},
        ]

    def logs(self, limit: int = 120) -> dict[str, Any]:
        log_dir = Path(self.app.settings.brain_dir) / "logs"
        main_log = log_dir / "leshy.log"
        lines: list[str] = []
        if main_log.is_file():
            try:
                lines = main_log.read_text(encoding="utf-8", errors="replace").splitlines()[-self._limit(limit, 120, 500):]
            except OSError:
                lines = []
        return {
            "path": str(log_dir),
            "lines": lines,
            "problems": recent_problems(self._limit(limit, 50, 500)),
        }
