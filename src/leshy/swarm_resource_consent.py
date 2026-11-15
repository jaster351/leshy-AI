"""Политика доступа к ресурсам роя: при сомнении запрещать выполнение.

Это переиспользуемая проверка. Её ещё необходимо встроить во все рабочие
маршруты. Перед внешним заданием нужно вызвать authorize_resource_task;
действия с ОС и файлами требуют отдельного локального подтверждения.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class ResourceScope(str, Enum):
    CPU = "cpu"
    GPU = "gpu"
    RAM = "ram"
    STORAGE = "storage"
    NETWORK = "network"


@dataclass(frozen=True)
class SwarmConsent:
    """Явное локальное разрешение, которое нельзя выводить из факта скачивания."""
    local_owner_confirmed: bool
    enabled: bool
    agreement_revision: str
    granted_at: datetime
    expires_at: datetime
    scopes: frozenset[ResourceScope]
    max_cpu_percent: int = 0
    max_gpu_percent: int = 0
    max_ram_mb: int = 0
    max_storage_mb: int = 0
    max_network_mb: int = 0


@dataclass(frozen=True)
class ResourceTask:
    source: str
    scope: ResourceScope
    requested_amount: int
    peer_identity_verified: bool = False
    is_remote_os_action: bool = False
    is_sensitive_data_access: bool = False
    is_destructive: bool = False
    is_network_attack: bool = False


_LIMITS = {
    ResourceScope.CPU: "max_cpu_percent",
    ResourceScope.GPU: "max_gpu_percent",
    ResourceScope.RAM: "max_ram_mb",
    ResourceScope.STORAGE: "max_storage_mb",
    ResourceScope.NETWORK: "max_network_mb",
}


def authorize_resource_task(
    consent: Optional[SwarmConsent],
    task: ResourceTask,
    *,
    current_revision: str,
    now: Optional[datetime] = None,
) -> tuple[bool, str]:
    """Проверить допустимость задания до исполнения; неизвестное запрещать.

    Подлинность узла проверяет транспорт, а не само заявление внутри задания.
    Исполнитель дополнительно обязан соблюдать квоты во время работы.
    """
    if consent is None or not consent.enabled or not consent.local_owner_confirmed:
        return False, "explicit_owner_consent_required"
    if not current_revision or consent.agreement_revision != current_revision:
        return False, "agreement_revision_changed"
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or consent.granted_at.tzinfo is None or consent.expires_at.tzinfo is None:
        return False, "invalid_consent_timestamp"
    if not consent.granted_at <= timestamp < consent.expires_at:
        return False, "consent_expired_or_not_yet_valid"
    if task.is_remote_os_action or task.is_sensitive_data_access or task.is_destructive or task.is_network_attack:
        return False, "prohibited_task_type"
    if task.source != "authenticated_swarm_peer" or task.peer_identity_verified is not True:
        return False, "peer_identity_not_verified"
    if not isinstance(task.scope, ResourceScope) or task.scope not in consent.scopes:
        return False, "resource_scope_not_granted"
    limit = getattr(consent, _LIMITS[task.scope])
    if not isinstance(task.requested_amount, int) or isinstance(task.requested_amount, bool) or task.requested_amount <= 0:
        return False, "invalid_resource_request"
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0 or task.requested_amount > limit:
        return False, "resource_quota_exceeded"
    return True, "explicit_consent_and_quota_verified"
