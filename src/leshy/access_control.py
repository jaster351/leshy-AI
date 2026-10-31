from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from secrets import token_urlsafe

from .logging_paths import central_log_path


@dataclass(frozen=True, slots=True)
class AdminModeSession:
    """Короткоживущая администраторская сессия владельца."""

    session_id: str
    owner_person_id: str
    created_at: datetime
    expires_at: datetime

    @property
    def active(self) -> bool:
        return datetime.now(timezone.utc) < self.expires_at


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    reason: str
    audit_required: bool = False


class ConversationAccessDenied(PermissionError):
    pass


class ConversationAccessPolicy:
    """Изоляция личных переписок с явным исключением для админ-режима владельца."""

    def __init__(self, owner_person_id: str, brain_dir: Path | None = None):
        owner_person_id = str(owner_person_id).strip()
        if not owner_person_id:
            raise ValueError("Не задан person_id владельца")
        self.owner_person_id = owner_person_id
        self.brain_dir = Path(brain_dir) if brain_dir is not None else None

    def open_admin_mode(self, actor_person_id: str, *, ttl_seconds: int = 900) -> AdminModeSession:
        actor_person_id = str(actor_person_id).strip()
        if actor_person_id != self.owner_person_id:
            raise ConversationAccessDenied("Администраторский режим доступен только владельцу")
        ttl_seconds = max(60, min(int(ttl_seconds), 3600))
        now = datetime.now(timezone.utc)
        return AdminModeSession(
            session_id=token_urlsafe(24),
            owner_person_id=actor_person_id,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )

    def can_read_history(
        self,
        actor_person_id: str,
        target_person_id: str,
        *,
        admin_session: AdminModeSession | None = None,
        explicit_request: bool = False,
    ) -> AccessDecision:
        actor = str(actor_person_id).strip()
        target = str(target_person_id).strip()
        if not actor or not target:
            return AccessDecision(False, "Не удалось определить пользователя")

        if actor == target:
            return AccessDecision(True, "Пользователь читает собственную переписку")

        if actor != self.owner_person_id:
            return AccessDecision(False, "Чужая переписка недоступна")

        if not explicit_request:
            return AccessDecision(False, "Для просмотра чужой переписки нужен явный запрос владельца")

        if admin_session is None:
            return AccessDecision(False, "Администраторский режим не включён")
        if admin_session.owner_person_id != self.owner_person_id:
            return AccessDecision(False, "Администраторская сессия принадлежит другому пользователю")
        if not admin_session.active:
            return AccessDecision(False, "Администраторская сессия истекла")

        return AccessDecision(True, "Разрешено владельцу в администраторском режиме", audit_required=True)

    def require_read_history(
        self,
        actor_person_id: str,
        target_person_id: str,
        *,
        admin_session: AdminModeSession | None = None,
        explicit_request: bool = False,
        purpose: str = "",
    ) -> None:
        decision = self.can_read_history(
            actor_person_id,
            target_person_id,
            admin_session=admin_session,
            explicit_request=explicit_request,
        )
        if not decision.allowed:
            raise ConversationAccessDenied(decision.reason)
        if decision.audit_required:
            self._audit_admin_read(actor_person_id, target_person_id, purpose=purpose)

    def _audit_admin_read(self, actor_person_id: str, target_person_id: str, *, purpose: str) -> None:
        if self.brain_dir is None:
            return
        path = central_log_path(self.brain_dir, "privacy-audit.jsonl")
        record = {
            "event": "admin_conversation_read",
            "actor_ref": self._safe_ref(actor_person_id),
            "target_ref": self._safe_ref(target_person_id),
            "purpose": str(purpose or "проверка работы Лешего")[:160],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    @staticmethod
    def _safe_ref(person_id: str) -> str:
        return sha256(str(person_id).encode("utf-8")).hexdigest()[:16]
