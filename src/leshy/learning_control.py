from __future__ import annotations

from .storage import Storage


class LearningControl:
    """Постоянный переключатель автономного интернет-обучения.

    Состояние хранится в SQLite-базе `LeshyBrain`, поэтому перезапуск или
    обновление приложения не могут незаметно сбросить выбор пользователя.
    """

    META_KEY = "idle_learning_enabled"

    def __init__(self, storage: Storage, default_enabled: bool = False):
        self.storage = storage
        with self.storage.connection() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (self.META_KEY,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES(?,?)",
                    (self.META_KEY, "1" if default_enabled else "0"),
                )

    def is_enabled(self) -> bool:
        with self.storage.connection() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (self.META_KEY,)).fetchone()
        return bool(row and str(row["value"]).strip().lower() in {"1", "true", "yes", "on"})

    def set_enabled(self, enabled: bool, *, reason: str = "пользователь") -> bool:
        value = "1" if bool(enabled) else "0"
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (self.META_KEY, value),
            )
        self.storage.audit(
            "learning.control_changed",
            {"enabled": bool(enabled), "reason": str(reason)[:200]},
        )
        return bool(enabled)

    def toggle(self, *, reason: str = "пользователь") -> bool:
        return self.set_enabled(not self.is_enabled(), reason=reason)

    @property
    def enabled(self) -> bool:
        return self.is_enabled()
