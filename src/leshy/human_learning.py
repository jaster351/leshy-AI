from __future__ import annotations

from dataclasses import dataclass
import json
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib import request
from urllib.parse import urlparse

from .diagnostics import event, problem
from .storage import Storage, utcnow
from .video_knowledge import VideoKnowledgeIngestor, VideoSegment, VideoSource


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

    Единая модель: обнаружить -> изучить -> сохранить происхождение ->
    перепроверить -> уведомить владельца -> при явном разрешении выполнить
    реакцию. Разрешение на реакции хранится для конкретной подписки и не
    распространяется на другие сайты или аккаунты.
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
                if str(row["status"]) in {"failed", "blocked"}:
                    conn.execute(
                        "UPDATE learning_materials SET status='queued',last_error=NULL,updated_at=? WHERE id=?",
                        (now, int(row["id"])),
                    )
                    row = conn.execute("SELECT * FROM learning_materials WHERE id=?", (int(row["id"]),)).fetchone()
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

    def next_material(self) -> dict[str, Any] | None:
        with self.storage.connection() as conn:
            row = conn.execute("SELECT * FROM learning_materials WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            material_id = int(row["id"])
            conn.execute(
                "UPDATE learning_materials SET status='processing',updated_at=?,last_error=NULL WHERE id=?",
                (utcnow(), material_id),
            )
            row = conn.execute("SELECT * FROM learning_materials WHERE id=?", (material_id,)).fetchone()
        return dict(row)

    def finish_material(
        self,
        material_id: int,
        *,
        status: str,
        summary: str,
        title: str = "",
        author: str = "",
        external_id: str = "",
        metadata: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        if status not in {"done", "blocked", "failed"}:
            raise ValueError("Некорректный итоговый статус материала")
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE learning_materials SET status=?,summary=?,title=CASE WHEN ?<>'' THEN ? ELSE title END,"
                "author=CASE WHEN ?<>'' THEN ? ELSE author END,external_id=CASE WHEN ?<>'' THEN ? ELSE external_id END,"
                "metadata_json=?,updated_at=?,learned_at=?,last_error=? WHERE id=?",
                (
                    status,
                    str(summary or "")[:4000],
                    str(title or ""), str(title or "")[:500],
                    str(author or ""), str(author or "")[:500],
                    str(external_id or ""), str(external_id or "")[:500],
                    json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                    utcnow(),
                    utcnow() if status == "done" else None,
                    str(error or "")[:1000] or None,
                    int(material_id),
                ),
            )

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
                "SELECT * FROM social_subscriptions WHERE status IN ('active','error') AND next_check_at<=? ORDER BY next_check_at,id LIMIT ?",
                (now_epoch, max(1, min(int(limit), 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_checked(self, subscription_id: int, *, error: str = "") -> None:
        sub = self.subscription(subscription_id)
        interval = max(self.MIN_MONITOR_SECONDS, int(sub["monitor_interval_seconds"]))
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE social_subscriptions SET status=?,last_checked_at=?,next_check_at=?,last_error=?,updated_at=? WHERE id=?",
                ("error" if error else "active", utcnow(), time.time() + interval, str(error or "")[:1000] or None, utcnow(), int(subscription_id)),
            )

    def set_last_seen(self, subscription_id: int, external_id: str) -> None:
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE social_subscriptions SET last_seen_external_id=?,updated_at=? WHERE id=?",
                (str(external_id or "")[:500], utcnow(), int(subscription_id)),
            )

    def record_item(
        self,
        subscription_id: int,
        *,
        external_id: str,
        url: str,
        title: str = "",
        published_at: str = "",
        notified: bool = False,
    ) -> tuple[dict[str, Any], bool]:
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
                    "INSERT INTO social_items(subscription_id,external_id,url,title,published_at,notified,created_at) VALUES(?,?,?,?,?,?,?)",
                    (int(subscription_id), external_id, str(url)[:2000], str(title or "")[:500], str(published_at or "")[:100], int(bool(notified)), now),
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

    def pending_notifications(self, owner_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT i.*,s.platform,s.target_name,s.target_url,s.notify_new FROM social_items i "
                "JOIN social_subscriptions s ON s.id=i.subscription_id "
                "WHERE s.owner_id=? AND i.notified=0 AND s.notify_new=1 ORDER BY i.id LIMIT ?",
                (str(owner_id or "local")[:200], max(1, min(int(limit), 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_notified(self, item_id: int) -> None:
        with self.storage.connection() as conn:
            conn.execute("UPDATE social_items SET notified=1 WHERE id=?", (int(item_id),))

    def set_screenshot(self, item_id: int, screenshot_path: str) -> None:
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE social_items SET screenshot_path=? WHERE id=?",
                (str(screenshot_path or "")[:2000], int(item_id)),
            )

    def set_reaction(self, item_id: int, reaction: str, *, applied: bool = False) -> None:
        reaction = str(reaction or "none").lower()
        if reaction not in {"none", "like", "dislike"}:
            raise ValueError("Некорректная реакция")
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE social_items SET reaction=?,reaction_applied=? WHERE id=?",
                (reaction, int(bool(applied)), int(item_id)),
            )

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
            processing = int(conn.execute("SELECT COUNT(*) c FROM learning_materials WHERE status='processing'").fetchone()["c"])
            done = int(conn.execute("SELECT COUNT(*) c FROM learning_materials WHERE status='done'").fetchone()["c"])
            active = int(conn.execute("SELECT COUNT(*) c FROM social_subscriptions WHERE status IN ('active','error')").fetchone()["c"])
            pending = int(conn.execute("SELECT COUNT(*) c FROM social_items WHERE notified=0").fetchone()["c"])
        return {
            "queued_materials": queued,
            "processing_materials": processing,
            "learned_materials": done,
            "active_subscriptions": active,
            "pending_notifications": pending,
            "platforms": ["youtube", "x", "vk", "twitch", "web", "local-files"],
        }


class MaterialLearningWorker:
    """Фоновое изучение явных материалов и публикаций из подписок.

    Текст и доступные субтитры изучаются уже сейчас. Если субтитров нет, задача
    остаётся blocked с явной причиной: нужен локальный ASR/vision слой. Это лучше,
    чем притворяться, что Леший понял видео только по заголовку.
    """

    def __init__(self, coordinator: HumanLearningCoordinator, knowledge, web_fetcher):
        self.coordinator = coordinator
        self.knowledge = knowledge
        self.web_fetcher = web_fetcher
        self.video_ingestor = VideoKnowledgeIngestor(knowledge)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="leshy-material-learning", daemon=True)
        self._thread.start()
        event("material_learning_worker_started")

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "last_error": self._last_error,
        }

    @staticmethod
    def _claims(text: str, *, limit: int = 80) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for chunk in re.split(r"(?<=[.!?])\s+", " ".join(str(text or "").split())):
            value = chunk.strip()
            if 35 <= len(value) <= 500 and value not in seen:
                seen.add(value)
                out.append(value)
            if len(out) >= limit:
                break
        return out

    def _ingest_text(self, *, topic: str, source: str, title: str, source_type: str, text: str) -> int:
        accepted = 0
        for claim in self._claims(text):
            self.knowledge.add_candidate(
                topic=topic,
                claim=claim,
                source_url=source,
                source_title=title,
                source_type=source_type,
                metadata_json={"human_learning": True, "captured_via": source_type},
            )
            accepted += 1
        if accepted:
            self.knowledge.verify_topic(topic)
        return accepted

    @staticmethod
    def _yt_info(url: str) -> dict[str, Any]:
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("Для видеоплатформ требуется пакет yt-dlp") from exc
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        if not isinstance(info, dict):
            raise RuntimeError("Видеоплатформа не вернула метаданные")
        return info

    @staticmethod
    def _subtitle_candidates(info: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[tuple[int, int, dict[str, Any]]] = []
        for bucket_name in ("subtitles", "automatic_captions"):
            bucket = info.get(bucket_name) or {}
            if not isinstance(bucket, dict):
                continue
            for lang, rows in bucket.items():
                if not isinstance(rows, list):
                    continue
                lang_rank = 0 if str(lang).lower().startswith("ru") else 1 if str(lang).lower().startswith("en") else 2
                for row in rows:
                    if not isinstance(row, dict) or not row.get("url"):
                        continue
                    ext = str(row.get("ext") or "").lower()
                    ext_rank = 0 if ext == "vtt" else 1 if ext in {"srv1", "srv2", "srv3"} else 2
                    candidates.append((lang_rank, ext_rank, row))
        return [row for _, _, row in sorted(candidates, key=lambda item: (item[0], item[1]))]

    @staticmethod
    def _parse_vtt(text: str, duration_seconds: float) -> list[VideoSegment]:
        def to_seconds(value: str) -> float:
            parts = value.replace(",", ".").split(":")
            try:
                if len(parts) == 3:
                    return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                if len(parts) == 2:
                    return float(parts[0]) * 60 + float(parts[1])
            except ValueError:
                return 0.0
            return 0.0

        segments: list[VideoSegment] = []
        blocks = re.split(r"\n\s*\n", str(text or "").replace("\r\n", "\n"))
        for block in blocks:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            timing_index = next((i for i, line in enumerate(lines) if "-->" in line), -1)
            if timing_index < 0:
                continue
            timing = lines[timing_index].split("-->", 1)
            start = to_seconds(timing[0].strip().split()[0])
            end = to_seconds(timing[1].strip().split()[0])
            speech = " ".join(re.sub(r"<[^>]+>", " ", line) for line in lines[timing_index + 1:])
            speech = " ".join(speech.split())
            if speech:
                segments.append(VideoSegment(start, max(end, start), transcript=speech))
        if not segments and text.strip():
            cleaned = re.sub(r"<[^>]+>", " ", text)
            cleaned = re.sub(r"(?m)^WEBVTT.*$", " ", cleaned)
            cleaned = " ".join(cleaned.split())
            if cleaned:
                segments.append(VideoSegment(0.0, max(0.0, duration_seconds), transcript=cleaned))
        return segments[:5000]

    def _fetch_segments(self, info: dict[str, Any]) -> list[VideoSegment]:
        duration = float(info.get("duration") or 0.0)
        for row in self._subtitle_candidates(info):
            try:
                req = request.Request(str(row["url"]), headers={"User-Agent": "Mozilla/5.0"})
                with request.urlopen(req, timeout=20) as response:
                    raw = response.read(5_000_000)
                text = raw.decode("utf-8", errors="replace")
                segments = self._parse_vtt(text, duration)
                if segments:
                    return segments
            except Exception:
                continue
        return []

    def _process_video(self, row: dict[str, Any]) -> None:
        material_id = int(row["id"])
        source = str(row["source"])
        kind = str(row["source_kind"])
        info = self._yt_info(source)
        title = str(info.get("title") or row.get("title") or source)[:500]
        author = str(info.get("uploader") or info.get("channel") or info.get("creator") or "")[:500]
        external_id = str(info.get("id") or "")[:500]
        duration = float(info.get("duration") or 0.0)
        segments = self._fetch_segments(info)
        metadata = {
            "extractor": str(info.get("extractor_key") or info.get("extractor") or ""),
            "webpage_url": str(info.get("webpage_url") or source),
            "channel_url": str(info.get("channel_url") or info.get("uploader_url") or ""),
            "duration_seconds": duration,
            "subtitle_segments": len(segments),
            "asr_pending": not bool(segments),
            "vision_pending": True,
        }
        if not segments:
            self.coordinator.finish_material(
                material_id,
                status="blocked",
                title=title,
                author=author,
                external_id=external_id,
                metadata=metadata,
                summary="Метаданные получены, но доступных субтитров нет. Нужен локальный ASR и visual-анализ.",
            )
            return
        result = self.video_ingestor.ingest(
            topic=title,
            source=VideoSource(external_id or str(material_id), source, title, source_kind=kind),
            segments=segments,
            verify=True,
        )
        self.coordinator.finish_material(
            material_id,
            status="done",
            title=title,
            author=author,
            external_id=external_id,
            metadata=metadata,
            summary=(
                f"Изучены доступные субтитры: {len(segments)} фрагментов; "
                f"извлечено утверждений: {int(result.get('accepted_claims') or 0)}. "
                "Визуальный слой будет дополнен локальной vision-моделью."
            ),
        )

    def _process_web(self, row: dict[str, Any]) -> None:
        material_id = int(row["id"])
        source = str(row["source"])
        text = self.web_fetcher.fetch_text(source)
        title = str(row.get("title") or urlparse(source).hostname or source)[:500]
        accepted = self._ingest_text(topic=title, source=source, title=title, source_type="material-web", text=text)
        self.coordinator.finish_material(
            material_id,
            status="done",
            title=title,
            metadata={"characters": len(text), "vision_pending": False, "asr_pending": False},
            summary=f"Веб-материал прочитан; извлечено утверждений: {accepted}.",
        )

    def _process_local_text(self, row: dict[str, Any]) -> None:
        material_id = int(row["id"])
        source = str(row["source"])
        path = Path(source).expanduser()
        if not path.is_file():
            raise FileNotFoundError(source)
        if path.suffix.lower() not in {".txt", ".md"}:
            self.coordinator.finish_material(
                material_id,
                status="blocked",
                summary="Файл зарегистрирован. Для этого формата ещё нужен специализированный локальный адаптер.",
                metadata={"adapter_pending": True, "suffix": path.suffix.lower()},
            )
            return
        text = path.read_text(encoding="utf-8", errors="replace")[:2_000_000]
        title = str(row.get("title") or path.name)[:500]
        accepted = self._ingest_text(topic=title, source=path.as_uri(), title=title, source_type="material-local-text", text=text)
        self.coordinator.finish_material(
            material_id,
            status="done",
            title=title,
            metadata={"characters": len(text), "local": True},
            summary=f"Локальный текстовый материал изучен; извлечено утверждений: {accepted}.",
        )

    def _process(self, row: dict[str, Any]) -> None:
        material_id = int(row["id"])
        kind = str(row["source_kind"])
        event("material_learning_started", material_id=material_id, source_kind=kind)
        try:
            if kind in {"youtube", "x", "vk", "twitch"}:
                self._process_video(row)
            elif kind == "web":
                self._process_web(row)
            elif kind == "document-file":
                self._process_local_text(row)
            else:
                self.coordinator.finish_material(
                    material_id,
                    status="blocked",
                    summary="Источник поставлен в очередь, но для этого типа ещё нужен ASR/vision/document адаптер.",
                    metadata={"adapter_pending": True, "source_kind": kind},
                )
            self._last_error = ""
        except Exception as exc:
            self._last_error = str(exc)[:1000]
            problem("material_learning_failed", self._last_error, material_id=material_id, source_kind=kind)
            self.coordinator.finish_material(
                material_id,
                status="failed",
                summary="Не удалось изучить материал.",
                error=self._last_error,
            )

    def _run(self) -> None:
        while not self._stop.is_set():
            row = self.coordinator.next_material()
            if row is None:
                self._stop.wait(2.0)
                continue
            self._process(row)
            self._stop.wait(0.2)


class SocialWatchWorker:
    """Фоновое обнаружение новых публикаций.

    yt-dlp используется для публичных метаданных. Вход в аккаунт, настоящая
    подписка и реакции выполняются отдельным Computer Use адаптером, который
    должен сначала понять экран, затем выполнить действие и визуально подтвердить
    результат. На первом запуске создаётся baseline, чтобы не объявлять старые
    ролики как новые.
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
            item_url = str(entry.get("webpage_url") or entry.get("url") or "")
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
            newest_id = str(discovered[0]["id"]) if discovered else ""
            baseline = not bool(str(sub.get("last_seen_external_id") or "").strip())
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
                    notified=baseline,
                )
                if created and not baseline:
                    new_items.append(saved)
                    if bool(sub["auto_learn"]):
                        material = self.coordinator.queue_material(str(sub["owner_id"]), item["url"], title=item["title"])
                        self.coordinator.attach_material_to_item(int(saved["id"]), int(material["id"]))
            if newest_id:
                self.coordinator.set_last_seen(subscription_id, newest_id)
            self.coordinator.mark_checked(subscription_id)
            self._last_error = ""
            if baseline:
                event("social_watch_baseline_created", subscription_id=subscription_id, items=len(discovered))
            elif new_items:
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


__all__ = [
    "HumanLearningCoordinator",
    "MaterialLearningWorker",
    "MaterialRef",
    "SocialWatchWorker",
    "detect_source_kind",
]
