"""Server-side creator/server-owner roles for Leshy.

The creator is NOT inferred from an LLM answer, a guest cookie, a Telegram
display name, or knowledge files. Bootstrap requires a Telegram-confirmed Web
session from loopback on the locally administered computer.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any


def _telegram_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[1-9][0-9]{1,19}", text) else ""


def _creator_store(telegram) -> Path:
    return Path(telegram.brain_dir) / "secrets" / "creator_role.json"


def creator_telegram_id(telegram) -> str:
    """Explicit local assignment only. No automatic promotion of server owners."""
    override = _telegram_id(os.getenv("LESHY_CREATOR_TELEGRAM_ID", ""))
    if override:
        return override
    try:
        path = _creator_store(telegram)
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 4096:
            return ""
        data = json.loads(path.read_text(encoding="utf-8"))
        return _telegram_id(data.get("telegram_user_id")) if isinstance(data, dict) else ""
    except (OSError, UnicodeError, ValueError, TypeError):
        return ""


def role_for_telegram(telegram, telegram_user_id: str | int) -> str:
    sender = _telegram_id(telegram_user_id)
    if not sender:
        return "user"
    if sender == creator_telegram_id(telegram):
        return "creator"
    owner = _telegram_id(getattr(telegram, "_access")().get("owner_id"))
    return "server_owner" if owner == sender else "user"


def role_for_web(telegram, identity, owner_uuid: str) -> str:
    """Only verified Telegram links, not an anonymous Web session, confer roles."""
    if not identity or not owner_uuid:
        return "user"
    try:
        result = identity.status(owner_uuid)
        ids = [str(row.get("user_id") or "") for row in result.get("telegram", [])]
    except (ValueError, OSError, TypeError, KeyError):
        return "user"
    roles = [role_for_telegram(telegram, uid) for uid in ids]
    if "creator" in roles:
        return "creator"
    if "server_owner" in roles:
        return "server_owner"
    return "user"


def register_local_creator(telegram, telegram_user_id: str) -> dict[str, Any]:
    """Initial creator binding from verified Telegram login on local machine.

    Never call from Telegram text, an open web API, a peer or an LLM tool.
    The HTTP caller must enforce loopback + server-side session + same-origin
    JSON + one-time explicit click. A second user cannot seize an existing role.
    """
    user_id = _telegram_id(telegram_user_id)
    if not user_id:
        raise ValueError("Не подтверждён числовой Telegram ID")
    current = creator_telegram_id(telegram)
    if current and current != user_id:
        raise PermissionError("Создатель этой установки уже зарегистрирован")
    if os.getenv("LESHY_CREATOR_TELEGRAM_ID", "") and current != user_id:
        raise PermissionError("Создатель установлен локальной конфигурацией")
    path = _creator_store(telegram)
    if not current:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"schema": 1, "telegram_user_id": user_id}, ensure_ascii=False, indent=2)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    # Local installation owner privileges are separately recorded in Telegram
    # access policy, preserving whether general conversation is open to all.
    access = telegram._access()
    if access.get("owner_id") != user_id:
        telegram._save_access(
            allow_all=bool(access.get("allow_all")),
            owner_id=user_id,
        )
    return {
        "role": "creator", "creator_configured": True,
        "server_owner": True, "telegram_user_id": user_id,
    }


ROLE_DISPLAY = {
    "creator": "создатель",
    "server_owner": "хозяин сервера",
    "user": "пользователь",
}

__all__ = [
    "role_for_telegram", "role_for_web", "register_local_creator",
    "creator_telegram_id", "ROLE_DISPLAY",
]
