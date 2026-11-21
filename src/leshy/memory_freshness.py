"""Единая проверка срока обычной памяти без чтения и изменения хранилища."""
from __future__ import annotations

from datetime import datetime, timezone


def memory_is_fresh(expires_at: object, *, now: datetime | None = None) -> bool:
    """Пустой срок постоянен; неверный/истёкший срок не разрешает контекст.

    Legacy ISO без зоны трактуется как UTC одинаково на Windows и Linux.
    Равенство границе срока уже означает истечение.
    """
    if expires_at is None:
        return True
    if not isinstance(expires_at, str):
        return False
    value = expires_at.strip()
    if not value:
        return True
    try:
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        current = datetime.now(timezone.utc) if now is None else now
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return expiry > current
    except (TypeError, ValueError, OverflowError, AttributeError):
        return False


__all__ = ["memory_is_fresh"]
