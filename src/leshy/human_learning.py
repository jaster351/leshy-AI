from __future__ import annotations

from dataclasses import dataclass
import json
import threading
import time
from typing import Any
from urllib.parse import urlparse

from .diagnostics import event, problem
from .storage import Storage, utcnow


@dataclass(frozen=True, slots=True)
class MaterialRef:
    source: str
    source_kind: str
    title: str = ""
    author: str = ""
    external_id: str = ""


def detect_source_kind(source: str) -> str:
    value = str(source or "").strip()
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host in {"youtube.com", "youtu.be", "m.youtube.com"}:
        return "youtube"
    if host in {"x.com", "twitter.com"} or host.endswith(".x.com"):
        return "x"
    if host == "vk.com" or host.endswith(".vk.com"):
        return "vk"
    if host == "twitch.tv" or host.endswith(".twitch.tv"):
        return "twitch"
    if parsed.scheme in {"http", "https"}:
        return "web"
    lower = value.lower()
    if lower.endswith((".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v")):
        return "video-file"
    if lower.endswith((".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus")):
        return "audio-file"
    if lower.endswith((".pdf", ".doc", ".docx", ".ppt", ".pptx", ".txt", ".md")):
        return "document-file"
    if lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")):
        return "image-file"
    return "unknown"


class HumanLearningCoordinator:
    """Очередь материалов и постоянное наблюдение за авторами/каналами.

    Цель слоя — единая модель поведения для YouTube, X, VK, Twitch, сайтов и
    локальных материалов: обнаружить -> изучить -> сохранить происхождение ->
    перепроверить -> уведомить владельца -> при явном разрешении выполнить
    реакцию. Само наличие подписки не разрешает реакции от имени пользователя.
    """

    MIN_MONITOR_SECONDS = 60
    DEFAULT_MONITOR_SECONDS = 15 * 60

    def __init__(self, storage: Storage):
        self.storage = storage
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.storage.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS learning_materials(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    author TEXT NOT NULL DEFAULT '',
                    external_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('queued','processing','done','blocked','failed')),
                    summary TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    learned_at TEXT,
                    last_error TEXT,
                    UNIQUE(owner_id,source)
                );
                CREATE INDEX IF NOT EXISTS ix_learning_materials_status ON learning_materials(status,id);

                CREATE TABLE IF NOT EXISTS social_subscriptions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    target_url TEXT NOT NULL,
                    target_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('active','paused','blocked','error')),
                    monitor_interval_seconds INTEGER NOT NULL DEFAULT 900,
                    auto_learn INTEGER NOT NULL DEFAULT 1,
                    notify_new INTEGER NOT NULL DEFAULT 1,
                    reaction_policy TEXT NOT NULL DEFAULT 'none' CHECK(reaction_policy IN ('none','evaluate_and_react')),
                    last_seen_external_id TEXT NOT NULL DEFAULT '',
                    last_checked_at TEXT,
                    next_check_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_id,target_url)
                );
                CREATE INDEX IF NOT EXISTS ix_social_subscriptions_due ON social_subscriptions(status,next_check_at);

                CREATE TABLE IF NOT EXISTS social_items(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subscription_id INTEGER NOT NULL REFERENCES social_subscriptions(id) ON DELETE CASCADE,
                    external_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    published_at TEXT NOT NULL DEFAULT '',
                    learned_material_id INTEGER REFERENCES learning_materials(id),
                    reaction TEXT NOT NULL DEFAULT 'none' CHECK(reaction IN ('none','like','dislike')),
                    reaction_applied INTEGER NOT NULL DEFAULT 0,
                    notified INTEGER NOT NULL DEFAULT 0,
                    screenshot_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(subscription_id,external_id)
                );
                CREATE INDEX IF NOT EXISTS ix_social_items_subscription ON social_items(subscription_id,id DESC);
                """
            )

    def queue_material(self, owner_id: str, source: str, *, title: str = "") -> dict[str, Any]:
        owner = str(owner_id or "local").strip()[:200]
        source = str(source or "").strip()
        if not source:
            raise ValueError("Источник материала не указан")
        now = utcnow()
        kind = detect_source_kind(source)
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT * FROM learning_materials WHERE owner_id=? AND source=?",
                (owner, source),
            ).fetchone()
            if row is not None:
                return dict(row)
            cur = conn.execute(
                "INSERT INTO learning_materials(owner_id,source,source_kind,title,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (owner, source, kind, str(title or "")[:500], "queued", now, now),
            )
            material_id = int(cur.lastrowid)
        self.storage.audit("human_learning.material_queued", {"id": material_id, "owner_id": owner, "source": source, "kind": kind})
        event("material_learning_queued", material_id=material_id, source_kind=kind)
        return self.material(material_id)

    def material(self, material_id: int) -> dict[str, Any]:
        with self.storage.connection() as conn:
            row = conn.execute("SELECT * FROM learning_materials WHERE id=?", (int(material_id),)).fetchone()
        if row is None:
            raise KeyError("Материал не найден")
        return dict(row)

    def subscribe(
        self,
        owner_id: str,
        target_url: str,
        *,
        target_name: str = "",
        interval_seconds: int | None = None,
        auto_learn: bool = True,
        notify_new: bool = True,
        allow_reactions: bool = False,
    ) -> dict[str, Any]:
        owner = str(owner_id or "local").strip()[:200]
        target_url = str(target_url or "").strip()
        if not target_url.startswith(("http://", "https://")):
            raise ValueError("Для подписки нужен HTTP(S) URL автора или канала")
        platform = detect_source_kind(target_url)
        if platform not in {"youtube", "x", "vk", "twitch", "web"}:
            platform = "web"
        interval = max(self.MIN_MONITOR_SECONDS, min(int(interval_seconds or self.DEFAULT_MONITOR_SECONDS), 86400))
        policy = "evaluate_and_react" if allow_reactions else "none"
        now = utcnow()
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT id FROM social_subscriptions WHERE owner_id=? AND target_url=?",
                (owner, target_url),
            ).fetchone()
            if row is None:
                cur = conn.execute(
                    "INSERT INTO social_subscriptions(owner_id,platform,target_url,target_name,status,monitor_interval_seconds,auto_learn,notify_new,reaction_policy,next_check_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (owner, platform, target_url, str(target_name or "")[:500], "active", interval, int(auto_learn), int(notify_new), policy, 0, now, now),
                )
                subscription_id = int(cur.lastrowid)
            else:
                subscription_id = int(row["id"])
                conn.execute(
                    "UPDATE social_subscriptions SET platform=?,target_name=?,status='active',monitor_interval_seconds=?,auto_learn=?,notify_new=?,reaction_policy=?,next_check_at=0,last_error=NULL,updated_at=? WHERE id=?",
                    (platform, str(target_name or "")[:500], interval, int(auto_learn), int(notify_new), policy, now, subscription_id),
                )
        self.storage.audit("human_learning.subscription_saved", {"id": subscription_id, "owner_id": owner, "platform": platform, "target_url": target_url, "reaction_policy": policy})
        event("social_subscription_saved", subscription_id=subscription_id, platform=platform, reactions=allow_reactions)
        return self.subscription(subscription_id)

    def subscription(self, subscription_id: int) -> dict[str, Any]:
        with self.storage.connection() as conn:
            row = conn.execute("SELECT * FROM social_subscriptions WHERE id=?", (int(subscription_id),)).fetchone()
        if row is None:
            raise KeyError("Подписка не найдена")
        return dict(row)

    def subscriptions(self, owner_id: str) -> list[dict[str, Any]]:
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM social_subscriptions WHERE owner_id=? ORDER BY id DESC",
                (str(owner_id or "local")[:200],),
            ).fetchall()
        return [dict(row) for row in rows]

    def due_subscriptions(self, now_epoch: float | None = None, *, limit: int = 10) -> list[dict[str, Any]]:
        now_epoch = float(time.time() if now_epoch is None else now_epoch)
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM social_subscriptions WHERE status='active' AND next_check_at<=? ORDER BY next_check_at,id LIMIT ?",
                (now_epoch, max(1, min(int(limit), 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_checked(self, subscription_id: int, *, error: str = "") -> None:
        sub = self.subscription(subscription_id)
        interval = max(self.MIN_MONITOR_SECONDS, int(sub["monitor_interval_seconds"]))
        state = "error" if error else "active"
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE social_subscriptions SET status=?,last_checked_at=?,next_check_at=?,last_error=?,updated_at=? WHERE id=?",
                (state, utcnow(), time.time() + interval, str(error or "")[:1000] or None, utcnow(), int(subscription_id)),
            )

    def record_item(self, subscription_id: int, *, external_id: str, url: str, title: str = "", published_at: str = "") -> tuple[dict[str, Any], bool]:
        external_id = str(external_id or url).strip()[:500]
        now = utcnow()
        created = False
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT * FROM social_items WHERE subscription_id=? AND external_id=?",
                (int(subscription_id), external_id),
            ).fetchone()
            if row is None:
                cur = conn.execute(
                    "INSERT INTO social_items(subscription_id,external_id,url,title,published_at,created_at) VALUES(?,?,?,?,?,?)",
                    (int(subscription_id), external_id, str(url)[:2000], str(title or "")[:500], str(published_at or "")[:100], now),
                )
                item_id = int(cur.lastrowid)
                created = True
            else:
                item_id = int(row["id"])
        with self.storage.connection() as conn:
            item = conn.execute("SELECT * FROM social_items WHERE id=?", (item_id,)).fetchone()
        return dict(item), created

    def attach_material_to_item(self, item_id: int, material_id: int) -> None:
        with self.storage.connection() as conn:
            conn.execute("UPDATE social_items SET learned_material_id=? WHERE id=?", (int(material_id), int(item_id)))

    @staticmethod
    def reaction_for_score(score: float, *, allowed: bool) -> str:
        if not allowed:
            return "none"
        value = max(-1.0, min(float(score), 1.0))
        if value >= 0.35:
            return "like"
        if value <= -0.35:
            return "dislike"
        return "none"

    def status(self) -> dict[str, Any]:
        with self.storage.connection() as conn:
            queued = int(conn.execute("SELECT COUNT(*) c FROM learning_materials WHERE status='queued'").fetchone()["c"])
            done = int(conn.execute("SELECT COUNT(*) c FROM learning_materials WHERE status='done'").fetchone()["c"])
            active = int(conn.execute("SELECT COUNT(*) c FROM social_subscriptions WHERE status='active'").fetchone()["c"])
            pending = int(conn.execute("SELECT COUNT(*) c FROM social_items WHERE notified=0").fetchone()["c"])
        return {
            "queued_materials": queued,
            "learned_materials": done,
            "active_subscriptions": active,
            "pending_notifications": pending,
            "platforms": ["youtube", "x", "vk", "twitch", "web", "local-files"],
        }


class SocialWatchWorker:
    """Фоновое обнаружение новых публикаций.

    На первом этапе yt-dlp используется только для публичных метаданных. Вход в
    аккаунт, подписка и реакции выполняются отдельным Computer Use адаптером,
    который обязан визуально подтвердить действие до фиксации результата.
    """

    def __init__(self, coordinator: HumanLearningCoordinator):
        self.coordinator = coordinator
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="leshy-social-watch", daemon=True)
        self._thread.start()
        event("social_watch_started")

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "last_error": self._last_error,
            **self.coordinator.status(),
        }

    @staticmethod
    def _discover(url: str) -> list[dict[str, Any]]:
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("Для мониторинга видеоплатформ требуется пакет yt-dlp") from exc
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "playlistend": 8,
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        if not isinstance(info, dict):
            return []
        entries = info.get("entries") or []
        result: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item_url = str(entry.get("url") or entry.get("webpage_url") or "")
            if item_url and not item_url.startswith(("http://", "https://")):
                webpage = entry.get("webpage_url")
                if webpage:
                    item_url = str(webpage)
            result.append({
                "id": str(entry.get("id") or item_url),
                "url": item_url,
                "title": str(entry.get("title") or ""),
                "published_at": str(entry.get("timestamp") or entry.get("upload_date") or ""),
            })
        return result

    def _process_subscription(self, sub: dict[str, Any]) -> None:
        subscription_id = int(sub["id"])
        try:
            discovered = self._discover(str(sub["target_url"]))
            new_items: list[dict[str, Any]] = []
            for item in reversed(discovered):
                if not item["url"]:
                    continue
                saved, created = self.coordinator.record_item(
                    subscription_id,
                    external_id=item["id"],
                    url=item["url"],
                    title=item["title"],
                    published_at=item["published_at"],
                )
                if created:
                    new_items.append(saved)
                    if bool(sub["auto_learn"]):
                        material = self.coordinator.queue_material(str(sub["owner_id"]), item["url"], title=item["title"])
                        self.coordinator.attach_material_to_item(int(saved["id"]), int(material["id"]))
            self.coordinator.mark_checked(subscription_id)
            self._last_error = ""
            if new_items:
                event("social_watch_new_items", subscription_id=subscription_id, count=len(new_items))
        except Exception as exc:
            self._last_error = str(exc)[:1000]
            problem("social_watch_failed", self._last_error, subscription_id=subscription_id)
            self.coordinator.mark_checked(subscription_id, error=self._last_error)

    def _run(self) -> None:
        while not self._stop.is_set():
            due = self.coordinator.due_subscriptions(limit=4)
            if not due:
                self._stop.wait(5.0)
                continue
            for sub in due:
                if self._stop.is_set():
                    return
                self._process_subscription(sub)
            self._stop.wait(1.0)


__all__ = ["HumanLearningCoordinator", "MaterialRef", "SocialWatchWorker", "detect_source_kind"]
