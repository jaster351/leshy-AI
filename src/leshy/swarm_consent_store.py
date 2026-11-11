"""Хранение и отзыв явного разрешения на будущие вычисления роя.

Здесь сохраняется только намерение пользователя, а не включается исполнение
внешних задач. Согласие и запись в журнале фиксируются в одной транзакции.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from .swarm_resource_consent import ResourceScope, SwarmConsent

_KEY = "swarm.resource_consent.v1"
_CONFIRM = "РАЗРЕШАЮ ИСПОЛЬЗОВАНИЕ РЕСУРСОВ"
_REVISION = "2026-11-11.8"
_MAX_SECONDS = 24 * 60 * 60
_QUOTAS = {
    "cpu": "max_cpu_percent",
    "gpu": "max_gpu_percent",
    "ram": "max_ram_mb",
    "storage": "max_storage_mb",
    "network": "max_network_mb",
}


class SwarmConsentStore:
    def __init__(self, storage):
        self.storage = storage

    def _read(self):
        with self.storage.connection() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (_KEY,)).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row["value"])
            scopes = frozenset(ResourceScope(s) for s in data["scopes"])
            return SwarmConsent(
                local_owner_confirmed=data["local_owner_confirmed"] is True,
                enabled=data["enabled"] is True,
                agreement_revision=data["agreement_revision"],
                granted_at=datetime.fromisoformat(data["granted_at"]),
                expires_at=datetime.fromisoformat(data["expires_at"]),
                scopes=scopes,
                **{v: data[v] for v in _QUOTAS.values()},
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            return None  # Повреждённая запись никогда не даёт полномочий.

    def status(self) -> dict:
        consent = self._read()
        now = datetime.now(timezone.utc)
        current = bool(
            consent and consent.enabled and consent.local_owner_confirmed
            and consent.granted_at.tzinfo is not None
            and consent.expires_at.tzinfo is not None
            and consent.granted_at <= now < consent.expires_at
            and consent.agreement_revision == _REVISION
        )
        return {
            "consent_active": current,
            "resource_sharing_enabled": False,  # Исполнитель публичных задач пока отсутствует.
            "external_jobs_accepted": False,
            "agreement_revision_required": _REVISION,
            "scopes": sorted(scope.value for scope in consent.scopes) if current else [],
            "expires_at": consent.expires_at.isoformat() if current else None,
            "limits": {v: getattr(consent, v) for v in _QUOTAS.values()} if current else {},
        }

    def grant(self, *, confirmation: str, scopes: list, limits: dict,
              duration_seconds: int, agreement_revision: str) -> dict:
        if confirmation != _CONFIRM or agreement_revision != _REVISION:
            raise ValueError("Нужно подтвердить точную редакцию соглашения и отдельное согласие")
        if not isinstance(scopes, list) or not scopes or len(scopes) > 5 or not isinstance(limits, dict):
            raise ValueError("Нужно указать разрешённые ресурсы и лимиты")
        if type(duration_seconds) is not int or not (60 <= duration_seconds <= _MAX_SECONDS):
            raise ValueError("Срок согласия должен быть от минуты до 24 часов")
        try:
            allowed = frozenset(ResourceScope(scope) for scope in scopes)
        except (ValueError, TypeError) as exc:
            raise ValueError("Неизвестная категория ресурсов") from exc
        if len(allowed) != len(scopes):
            raise ValueError("Повторяющиеся категории ресурсов")
        if set(limits) != set(_QUOTAS):
            raise ValueError("Нужно явно определить лимиты всех пяти ресурсов")
        accepted = {}
        for scope, key in _QUOTAS.items():
            value = limits[scope]
            cap = 100 if scope in ("cpu", "gpu") else 1_048_576
            if type(value) is not int or value < 0 or value > cap or (scope in allowed and value == 0):
                raise ValueError(f"Недопустимый лимит ресурса: {scope}")
            if scope not in allowed and value:
                raise ValueError(f"Нельзя выделять незапрошенный ресурс: {scope}")
            accepted[key] = value

        now = datetime.now(timezone.utc)
        record = dict(
            local_owner_confirmed=True, enabled=True, agreement_revision=_REVISION,
            granted_at=now.isoformat(), expires_at=(now + timedelta(seconds=duration_seconds)).isoformat(),
            scopes=sorted(s.value for s in allowed), **accepted,
        )
        with self.storage.connection() as db:
            db.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_KEY, json.dumps(record, ensure_ascii=False, sort_keys=True)),
            )
            db.execute(
                "INSERT INTO audit_log(event_type,payload_json,created_at) VALUES(?,?,?)",
                ("swarm.resource_consent.granted", json.dumps({
                    "agreement_revision": _REVISION, "scopes": record["scopes"],
                    "limits": accepted, "expires_at": record["expires_at"],
                }, ensure_ascii=False, sort_keys=True), now.isoformat()),
            )
        return self.status()

    def revoke(self) -> dict:
        with self.storage.connection() as db:
            db.execute("DELETE FROM meta WHERE key=?", (_KEY,))
            db.execute(
                "INSERT INTO audit_log(event_type,payload_json,created_at) VALUES(?,?,?)",
                ("swarm.resource_consent.revoked", json.dumps({"agreement_revision": _REVISION}),
                 datetime.now(timezone.utc).isoformat()),
            )
        return self.status()


__all__ = ["SwarmConsentStore"]
