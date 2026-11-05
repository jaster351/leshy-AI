from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
import uuid
from typing import Any

from .browser_agent import BrowserAction, BrowserRisk
from .diagnostics import event


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class PendingApproval:
    approval_id: str
    owner_id: str
    action: BrowserAction
    summary: str
    created_at: datetime
    expires_at: datetime
    status: str = "pending"

    def public(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "owner_id": self.owner_id,
            "action": self.action.name,
            "url": self.action.url,
            "risk": self.action.risk.value,
            "description": self.action.description,
            "summary": self.summary,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "status": self.status,
        }


def _require_owner(owner_id: str) -> str:
    """Не превращать отсутствующего владельца в общий web:local."""
    owner = str(owner_id or "").strip()
    if not owner:
        raise ValueError("Для подтверждения требуется идентификатор владельца")
    if len(owner) > 200:
        raise ValueError("Идентификатор владельца превышает 200 символов")
    return owner


class ApprovalBroker:
    """Одноразовый Human Approval Gate для браузерных/внешних действий.

    Approval намеренно хранится только в памяти и имеет короткий TTL. После
    перезапуска Лешего старое разрешение нельзя повторно использовать. Секретные
    аргументы действия не выдаются API/UI — наружу возвращается только `public()`.
    """

    def __init__(self, *, ttl_seconds: int = 600):
        self.ttl_seconds = max(30, min(int(ttl_seconds), 3600))
        self._items: dict[str, PendingApproval] = {}
        self._lock = RLock()

    def _purge(self) -> None:
        now = _now()
        expired = [key for key, item in self._items.items() if item.expires_at <= now]
        for key in expired:
            item = self._items.pop(key)
            event("approval_expired", approval_id=key, action=item.action.name, risk=item.action.risk.value)

    def request(self, owner_id: str, action: BrowserAction, *, summary: str = "") -> PendingApproval:
        if action.risk is BrowserRisk.READ:
            raise ValueError("READ-действию не требуется Human Approval")
        with self._lock:
            self._purge()
            approval_id = uuid.uuid4().hex
            created = _now()
            item = PendingApproval(
                approval_id=approval_id,
                owner_id=_require_owner(owner_id),
                action=deepcopy(action),
                summary=str(summary or action.description or action.name)[:2000],
                created_at=created,
                expires_at=created + timedelta(seconds=self.ttl_seconds),
            )
            self._items[approval_id] = item
        event(
            "approval_requested",
            approval_id=approval_id,
            action=action.name,
            risk=action.risk.value,
            url=action.url,
        )
        return item

    def list(self, owner_id: str) -> list[dict[str, Any]]:
        owner = _require_owner(owner_id)
        with self._lock:
            self._purge()
            return [item.public() for item in self._items.values() if item.owner_id == owner]

    def deny(self, owner_id: str, approval_id: str) -> None:
        owner = _require_owner(owner_id)
        with self._lock:
            self._purge()
            item = self._items.get(str(approval_id))
            if item is None or item.owner_id != owner:
                raise KeyError("Approval не найден")
            self._items.pop(item.approval_id, None)
        event("approval_denied", approval_id=item.approval_id, action=item.action.name, risk=item.action.risk.value)

    def consume(self, owner_id: str, approval_id: str) -> BrowserAction:
        owner = _require_owner(owner_id)
        with self._lock:
            self._purge()
            item = self._items.get(str(approval_id))
            if item is None or item.owner_id != owner:
                raise KeyError("Approval не найден или истёк")
            self._items.pop(item.approval_id, None)
        event("approval_consumed", approval_id=item.approval_id, action=item.action.name, risk=item.action.risk.value)
        return deepcopy(item.action)


__all__ = ["ApprovalBroker", "PendingApproval"]
