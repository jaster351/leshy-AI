from __future__ import annotations

"""Локальные настройки LAN-кластера. Секреты/адреса не сохраняются здесь."""

from .storage import utcnow

_KEY = "cluster.discovery.enabled"


def autodiscovery_enabled(storage) -> bool:
    with storage.connection() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (_KEY,)).fetchone()
    if row is None:
        return True  # Совместимость с прежним автоматическим обнаружением.
    return str(row["value"]).strip().lower() in {"1", "true", "yes", "on"}


def set_autodiscovery_enabled(storage, enabled: bool) -> bool:
    with storage.connection() as conn:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_KEY, "1" if enabled else "0"),
        )
    storage.audit("cluster.autodiscovery.changed", {"enabled": bool(enabled), "updated_at": utcnow()})
    return bool(enabled)


__all__ = ["autodiscovery_enabled", "set_autodiscovery_enabled"]
