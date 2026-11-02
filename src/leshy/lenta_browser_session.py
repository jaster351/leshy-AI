from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from typing import Any


def _owner_key(owner_id: str) -> str:
    """Стабильный безопасный ключ каталога для конкретного пользователя."""
    owner = str(owner_id or "").strip()[:200]
    if not owner:
        raise ValueError("owner_id пользователя обязателен")
    digest = sha256(owner.encode("utf-8")).hexdigest()[:24]
    prefix = re.sub(r"[^a-zA-Z0-9_.-]+", "-", owner).strip("-._")[:32] or "user"
    return f"{prefix}-{digest}"


@dataclass(slots=True)
class LentaBrowserSession:
    """Постоянное состояние авторизации Ленты для одного владельца.

    Каждый owner_id получает отдельный браузерный профиль. Cookie, localStorage и
    sessionStorage одного пользователя не должны использоваться для другого.
    Леший не сохраняет пароль, SMS-коды и реквизиты банковской карты отдельно от
    защищённого хранилища самого браузера.
    """

    brain_dir: Path
    owner_id: str

    def __init__(self, brain_dir: Path | str, owner_id: str):
        self.brain_dir = Path(brain_dir)
        self.owner_id = str(owner_id or "").strip()[:200]
        key = _owner_key(self.owner_id)
        self.root = self.brain_dir / "browser" / "profiles" / "lenta" / key
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "session.json"

    @property
    def profile_dir(self) -> Path:
        return self.root

    def load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def mark_login_required(self, reason: str = "") -> dict[str, Any]:
        state = self.load()
        state.update({
            "schema": 2,
            "site": "lenta.com",
            "owner_key": _owner_key(self.owner_id),
            "login_required": True,
            "authenticated": False,
            "last_login_check": time.time(),
            "reason": str(reason or "")[:500],
        })
        self._save(state)
        return state

    def mark_authenticated(self, *, account_hint: str = "") -> dict[str, Any]:
        state = self.load()
        state.update({
            "schema": 2,
            "site": "lenta.com",
            "owner_key": _owner_key(self.owner_id),
            "login_required": False,
            "authenticated": True,
            "last_login_check": time.time(),
            "account_hint": str(account_hint or "")[:200],
        })
        self._save(state)
        return state

    def clear(self) -> dict[str, Any]:
        state = {
            "schema": 2,
            "site": "lenta.com",
            "owner_key": _owner_key(self.owner_id),
            "login_required": True,
            "authenticated": False,
            "last_login_check": time.time(),
        }
        self._save(state)
        return state

    def status(self) -> dict[str, Any]:
        state = self.load()
        return {
            "site": "lenta.com",
            "owner_key": _owner_key(self.owner_id),
            "profile_dir": str(self.profile_dir),
            "persistent_profile": True,
            "isolated_per_user": True,
            "stores_passwords": False,
            "stores_card_details": False,
            "authenticated": bool(state.get("authenticated")) and not bool(state.get("login_required")),
            "login_required": bool(state.get("login_required", True)),
            "last_login_check": state.get("last_login_check"),
            "account_hint": str(state.get("account_hint") or ""),
        }

    def _save(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)


class LentaSessionStore:
    """Фабрика строго изолированных сессий Ленты по owner_id."""

    def __init__(self, brain_dir: Path | str):
        self.brain_dir = Path(brain_dir)

    def for_owner(self, owner_id: str) -> LentaBrowserSession:
        return LentaBrowserSession(self.brain_dir, owner_id)


__all__ = ["LentaBrowserSession", "LentaSessionStore"]
