from __future__ import annotations

import threading
import time

from .human_learning import HumanLearningCoordinator, MaterialLearningWorker, SocialWatchWorker
from .scientific_learning import (
    SCIENTIFIC_MATERIAL_TARGET,
    is_academic_url,
    news_query,
    scientific_query,
)
from .storage import Storage


class SelfLearningWorker:
    """Постоянный фоновый цикл обучения Лешего.

    Автономная научная программа зависит от пользовательского переключателя
    самообучения. Явно заданные пользователем материалы и подписки выполняются
    независимо от этого переключателя: если пользователь сказал «изучи» или
    «следи за автором», выключенная автономная инициативность не отменяет задачу.
    """

    META_SCIENCE_CURSOR = "learning.science_cursor"
    META_NEWS_CURSOR = "learning.news_cursor"
    META_LAST_NEWS_EPOCH = "learning.last_news_epoch"

    AUTONOMOUS_INTERVAL_SECONDS = 45.0
    NEWS_INTERVAL_SECONDS = 15 * 60.0
    ERROR_BACKOFF_SECONDS = 90.0
    STOP_JOIN_TIMEOUT_SECONDS = 5.0

    def __init__(self, storage: Storage, learning_control, research_engine):
        self.storage = storage
        self.learning_control = learning_control
        self.research = research_engine
        self.human_learning = HumanLearningCoordinator(storage)
        self.material_learning = MaterialLearningWorker(
            self.human_learning,
            research_engine.knowledge,
            research_engine.fetcher,
        )
        self.social_watch = SocialWatchWorker(self.human_learning)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error = ""
        self._last_result: dict | None = None
        self._state = "stopped"
        self._next_autonomous_at = 0.0

    def start(self) -> None:
        # Явные пользовательские учебные задачи и подписки должны работать даже
        # при выключенной автономной инициативе.
        self.material_learning.start()
        self.social_watch.start()
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="leshy-self-learning", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Останавливает все learning workers и дожидается прекращения записи.

        Раньше метод только выставлял Event. Из-за этого ``stop_background()``
        мог вернуться, пока daemon-потоки ещё работали с SQLite/brain_dir. Это
        создавало гонку при штатном завершении, перезапуске и очистке временного
        профиля. Join выполняется только для чужого потока, поэтому вызов остаётся
        безопасным и идемпотентным.
        """
        self._stop.set()
        self.material_learning.stop()
        self.social_watch.stop()
        current = threading.current_thread()
        workers = (
            self._thread,
            getattr(self.material_learning, "_thread", None),
            getattr(self.social_watch, "_thread", None),
        )
        for worker in workers:
            if worker is None or worker is current or not worker.is_alive():
                continue
            worker.join(timeout=self.STOP_JOIN_TIMEOUT_SECONDS)
        self._state = "stopped"

    def _meta_int(self, key: str, default: int = 0) -> int:
        with self.storage.connection() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        try:
            return int(str(row["value"])) if row else int(default)
        except (TypeError, ValueError):
            return int(default)

    def _set_meta(self, key: str, value: int | float | str) -> None:
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def _scientific_material_count(self) -> int:
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT source_url FROM knowledge WHERE source_url<>''"
            ).fetchall()
        return sum(1 for row in rows if is_academic_url(str(row["source_url"])))

    def status(self) -> dict:
        scientific_materials = self._scientific_material_count()
        target = SCIENTIFIC_MATERIAL_TARGET
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "state": self._state,
            "enabled": bool(self.learning_control.is_enabled()),
            "last_error": self._last_error,
            "last_result": self._last_result,
            "scientific_materials": scientific_materials,
            "scientific_target": target,
            "scientific_goal_reached": scientific_materials >= target,
            "scientific_progress_percent": round(min(100.0, scientific_materials * 100.0 / target), 1),
            "news_monitoring": True,
            "news_interval_seconds": int(self.NEWS_INTERVAL_SECONDS),
            "last_news_epoch": self._meta_int(self.META_LAST_NEWS_EPOCH, 0),
            "human_learning": self.human_learning.status(),
            "material_learning_worker": self.material_learning.status(),
            "social_watch": self.social_watch.status(),
        }

    def queue_topic(self, query: str, reason: str = "тема пользователя") -> int:
        query = " ".join(str(query).split()).strip()
        if not query:
            raise ValueError("Пустая тема исследования")
        if len(query) > 500:
            raise ValueError("Тема исследования слишком длинная")
        return self.research.enqueue(query, reason=reason)

    def queue_material(self, owner_id: str, source: str, *, title: str = "") -> dict:
        self.material_learning.start()
        return self.human_learning.queue_material(owner_id, source, title=title)

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
    ) -> dict:
        self.social_watch.start()
        self.material_learning.start()
        return self.human_learning.subscribe(
            owner_id,
            target_url,
            target_name=target_name,
            interval_seconds=interval_seconds,
            auto_learn=auto_learn,
            notify_new=notify_new,
            allow_reactions=allow_reactions,
        )

    def subscriptions(self, owner_id: str) -> list[dict]:
        return self.human_learning.subscriptions(owner_id)

    def pending_social_notifications(self, owner_id: str, *, limit: int = 20) -> list[dict]:
        return self.human_learning.pending_notifications(owner_id, limit=limit)

    def _has_queued(self, *, manual_only: bool = False) -> bool:
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM research_tasks WHERE status='queued' "
                "AND (?=0 OR reason='тема пользователя' "
                "OR reason LIKE 'явная команда пользователя:%') LIMIT 1",
                (int(bool(manual_only)),),
            ).fetchone()
        return row is not None

    def _news_due(self, now_epoch: float) -> bool:
        last = float(self._meta_int(self.META_LAST_NEWS_EPOCH, 0))
        return last <= 0 or now_epoch - last >= self.NEWS_INTERVAL_SECONDS

    def _queue_news(self, now_epoch: float) -> int:
        cursor = self._meta_int(self.META_NEWS_CURSOR, 0)
        query = news_query(cursor)
        task_id = self.research.enqueue(query, reason="автономный мониторинг свежих новостей")
        self._set_meta(self.META_NEWS_CURSOR, cursor + 1)
        self._set_meta(self.META_LAST_NEWS_EPOCH, int(now_epoch))
        return task_id

    def _queue_science(self) -> int:
        cursor = self._meta_int(self.META_SCIENCE_CURSOR, 0)
        item = scientific_query(cursor)
        learned = self._scientific_material_count()
        reason = (
            f"научная программа: академический материал {learned}/{SCIENTIFIC_MATERIAL_TARGET}; "
            f"приоритет источника {item.domain}"
            if learned < SCIENTIFIC_MATERIAL_TARGET
            else f"непрерывное научное обновление после достижения цели {SCIENTIFIC_MATERIAL_TARGET}; {item.domain}"
        )
        task_id = self.research.enqueue(item.query, reason=reason)
        self._set_meta(self.META_SCIENCE_CURSOR, cursor + 1)
        return task_id

    def _queue_autonomous_work(self, now_epoch: float) -> int:
        if self._news_due(now_epoch):
            return self._queue_news(now_epoch)
        return self._queue_science()

    def _run(self) -> None:
        self._state = "idle"
        while not self._stop.is_set():
            autonomous = self.learning_control.is_enabled()
            if not autonomous and not self._has_queued(manual_only=True):
                self._state = "disabled"
                self._next_autonomous_at = 0.0
                self._stop.wait(2.0)
                continue

            try:
                now = time.time()
                if autonomous and not self._has_queued():
                    if self._next_autonomous_at > now:
                        self._state = "waiting_rate_limit"
                        self._stop.wait(min(2.0, max(0.1, self._next_autonomous_at - now)))
                        continue
                    self._queue_autonomous_work(now)

                self._state = "learning"
                result = self.research.run_next(manual_only=not autonomous)
                self._last_result = result
                self._last_error = ""
                if result and result.get("error"):
                    if autonomous:
                        self._next_autonomous_at = time.time() + self.ERROR_BACKOFF_SECONDS
                    self._state = "backoff"
                else:
                    if autonomous:
                        self._next_autonomous_at = time.time() + self.AUTONOMOUS_INTERVAL_SECONDS
                    self._state = "idle" if autonomous else "manual_only"
                self._stop.wait(2.0)
            except Exception as exc:
                self._last_error = str(exc)[:1000]
                self._state = "error"
                self._next_autonomous_at = time.time() + self.ERROR_BACKOFF_SECONDS
                self._stop.wait(15.0)
