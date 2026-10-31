from __future__ import annotations

import threading
import time

from .storage import Storage


_SAFE_CURRICULUM = (
    "Python standard library and packaging best practices",
    "Linux system administration fundamentals",
    "Windows system administration fundamentals",
    "computer networking fundamentals",
    "SQLite reliability backup and recovery",
    "local LLM inference llama.cpp GGUF",
    "information retrieval provenance and source verification",
    "cybersecurity defensive hardening fundamentals",
)


class SelfLearningWorker:
    """Фоновый безопасный цикл обучения.

    Он не отправляет пользовательские сообщения в интернет автоматически. Когда очередь пуста,
    берёт следующую нейтральную тему из локального учебного плана. Явно добавленные пользователем
    темы исследуются через ту же очередь.
    """

    META_CURSOR = "learning.curriculum_cursor"

    def __init__(self, storage: Storage, learning_control, research_engine):
        self.storage = storage
        self.learning_control = learning_control
        self.research = research_engine
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error = ""
        self._last_result: dict | None = None
        self._state = "stopped"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="leshy-self-learning", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "state": self._state,
            "enabled": bool(self.learning_control.is_enabled()),
            "last_error": self._last_error,
            "last_result": self._last_result,
        }

    def queue_topic(self, query: str, reason: str = "тема пользователя") -> int:
        query = " ".join(str(query).split()).strip()
        if not query:
            raise ValueError("Пустая тема исследования")
        if len(query) > 500:
            raise ValueError("Тема исследования слишком длинная")
        return self.research.enqueue(query, reason=reason)

    def _next_curriculum_topic(self) -> str:
        with self.storage.connection() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (self.META_CURSOR,)).fetchone()
            cursor = int(row["value"]) if row and str(row["value"]).isdigit() else 0
            topic = _SAFE_CURRICULUM[cursor % len(_SAFE_CURRICULUM)]
            conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (self.META_CURSOR, str(cursor + 1)),
            )
        return topic

    def _has_queued(self) -> bool:
        with self.storage.connection() as conn:
            row = conn.execute("SELECT 1 FROM research_tasks WHERE status='queued' LIMIT 1").fetchone()
        return row is not None

    def _run(self) -> None:
        self._state = "idle"
        idle_rounds = 0
        while not self._stop.is_set():
            if not self.learning_control.is_enabled():
                self._state = "disabled"
                self._stop.wait(2.0)
                continue
            try:
                self._state = "learning"
                if not self._has_queued():
                    idle_rounds += 1
                    # Не создавать новые темы каждую секунду: новый автономный цикл примерно раз в 30 минут.
                    if idle_rounds >= 900:
                        self.research.enqueue(self._next_curriculum_topic(), reason="автономный учебный план")
                        idle_rounds = 0
                    else:
                        self._state = "idle"
                        self._stop.wait(2.0)
                        continue
                result = self.research.run_next()
                self._last_result = result
                self._last_error = ""
                self._state = "idle"
                self._stop.wait(5.0)
            except Exception as exc:
                self._last_error = str(exc)[:1000]
                self._state = "error"
                self._stop.wait(15.0)
