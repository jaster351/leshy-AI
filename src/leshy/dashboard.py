from __future__ import annotations

from pathlib import Path
import re
import threading
from typing import Any

from .diagnostics import recent_problems, event
from .llm import ChatMessage
from .storage import utcnow


class DashboardService:
    """Данные и действия полноценного Web UI без дублирования бизнес-логики LeshyApp."""

    def __init__(self, app):
        self.app = app
        self.storage = app.storage
        self._title_lock = threading.Lock()
        self._title_thread: threading.Thread | None = None
        self._auto_title_notice_sent = False

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

    @staticmethod
    def _clean_generated_title(value: str, fallback: str) -> str:
        text = str(value or "").strip()
        text = re.sub(r"(?is)<think>.*?</think>", " ", text)
        text = re.sub(r"(?im)^\s*(название|заголовок)\s*[:—-]\s*", "", text)
        text = text.strip(" \t\r\n`*_#\"'«»")
        text = " ".join(text.split())
        if not text:
            text = " ".join(str(fallback or "Новый диалог").split())
        if len(text) > 64:
            text = text[:61].rstrip(" ,.;:-—") + "…"
        return text or "Новый диалог"

    def _auto_title_one(self, owner: str, session_id: str) -> None:
        """Только детерминированный заголовок. Никаких фоновых LLM-вызовов.

        Ручной вызов допускается лишь для сессии с заголовком 'auto', которая
        действительно содержит пользовательское сообщение. Старые auto-llm
        названия не перезаписываются.
        """
        from time import monotonic

        started = monotonic()
        try:
            with self.storage.connection() as conn:
                session = conn.execute(
                    "SELECT id,title,title_source FROM sessions WHERE id=? AND owner_id=?",
                    (session_id, owner),
                ).fetchone()
                if session is None or str(session["title_source"] or "") != "auto":
                    event("auto_title_skipped", reason="not_auto", session_id=session_id)
                    return
                first = conn.execute(
                    "SELECT content FROM messages WHERE session_id=? AND role='user' "
                    "ORDER BY id ASC LIMIT 1",
                    (session_id,),
                ).fetchone()
            if first is None:
                event("auto_title_skipped", reason="no_new_user_message", session_id=session_id)
                return
            title = self._clean_generated_title(str(first["content"])[:160], "Новый диалог")
            with self.storage.connection() as conn:
                conn.execute(
                    "UPDATE sessions SET title=?,title_source='auto-local' "
                    "WHERE id=? AND owner_id=? AND title_source='auto'",
                    (title, session_id, owner),
                )
            event("auto_title_finished", method="deterministic", latency_ms=int((monotonic()-started)*1000))
        except Exception:
            event("auto_title_skipped", reason="local_title_failed")

    def _schedule_auto_title(self, owner: str, rows: list[Any]) -> None:
        """Список диалогов — операция чтения, не сигнал для LLM или ретрая.

        После рестарта список старых сессий часто загружается автоматически.
        Даже дешёвый auto-title не должен изменять историю при этом событии.
        """
        if self._auto_title_notice_sent:
            return
        if any(str(row["title_source"] or "") == "auto" for row in rows):
            self._auto_title_notice_sent = True
            event("auto_title_skipped", reason="session_listing_no_new_messages")

    def sessions(self, owner_id: str = "web:local", limit: int = 50) -> list[dict[str, Any]]:
        owner = self._owner(owner_id)
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT id,title,title_source,created_at,updated_at FROM sessions WHERE owner_id=? ORDER BY updated_at DESC LIMIT ?",
                (owner, self._limit(limit)),
            ).fetchall()
        self._schedule_auto_title(owner, list(rows))
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
        # Сохраняем старые поля ready/detail для Web UI.
        # Не объявляем модуль работающим, пока runtime этого не подтвердил.
        records = self.app.capabilities.snapshot(self.app)["features"]
        return [
            {
                **entry,
                "ready": entry["readiness"] == "ready",
                "detail": entry["readiness_detail"],
            }
            for entry in records
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

    def clear_logs(self) -> dict[str, Any]:
        """Очистить только runtime-логи внутри LeshyBrain/logs.

        Каталог, база данных, память, модели, настройки и другие пользовательские
        данные не затрагиваются. Файлы обнуляются, где это возможно, чтобы открытые
        дескрипторы текущего процесса могли продолжить писать в те же пути.
        """
        log_dir = (Path(self.app.settings.brain_dir) / "logs").resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        cleared = 0
        failed: list[str] = []
        for path in log_dir.rglob("*"):
            if not path.is_file():
                continue
            try:
                resolved = path.resolve()
                if log_dir not in resolved.parents:
                    continue
                with path.open("w", encoding="utf-8"):
                    pass
                cleared += 1
            except OSError as exc:
                failed.append(f"{path.name}: {exc}")
        self.storage.audit("logs.cleared", {"files": cleared, "failed": len(failed)})
        return {"cleared_files": cleared, "failed": failed[:20], "path": str(log_dir)}
