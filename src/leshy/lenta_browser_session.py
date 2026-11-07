from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import time
from typing import Any

from .browser_profile_cdp import profile_cdp_url, profile_may_be_in_use, profile_busy_message
from .browser_launch_policy import chromium_launch_policy
from .system_browser import ensure_system_browser


_ALLOWED_DELIVERY = {"account", "delivery", "pickup"}
_ALLOWED_PAYMENT = {"account", "self", "cash", "card_on_delivery", "card_online"}
_ALLOWED_SUBSTITUTIONS = {"automatic", "ask", "none"}


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
    root: Path
    state_path: Path

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

    @property
    def owner_key(self) -> str:
        return _owner_key(self.owner_id)

    def load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def preferences(self) -> dict[str, Any]:
        state = self.load()
        delivery = str(state.get("delivery_mode") or "account")
        payment = str(state.get("payment_mode") or "account")
        substitutions = str(state.get("substitution_policy") or "automatic")
        return {
            "delivery_mode": delivery if delivery in _ALLOWED_DELIVERY else "account",
            "payment_mode": payment if payment in _ALLOWED_PAYMENT else "account",
            "substitution_policy": substitutions if substitutions in _ALLOWED_SUBSTITUTIONS else "automatic",
            "always_confirm_order": True,
        }

    def update_preferences(
        self,
        *,
        delivery_mode: str | None = None,
        payment_mode: str | None = None,
        substitution_policy: str | None = None,
    ) -> dict[str, Any]:
        state = self.load()
        if delivery_mode is not None:
            value = str(delivery_mode).strip()
            if value not in _ALLOWED_DELIVERY:
                raise ValueError("Некорректный способ получения")
            state["delivery_mode"] = value
        if payment_mode is not None:
            value = str(payment_mode).strip()
            if value not in _ALLOWED_PAYMENT:
                raise ValueError("Некорректный способ оплаты")
            state["payment_mode"] = value
        if substitution_policy is not None:
            value = str(substitution_policy).strip()
            if value not in _ALLOWED_SUBSTITUTIONS:
                raise ValueError("Некорректная политика замены товаров")
            state["substitution_policy"] = value
        state.update({"schema": 4, "site": "lenta.com", "owner_key": self.owner_key})
        self._save(state)
        return self.preferences()

    def mark_login_required(self, reason: str = "") -> dict[str, Any]:
        state = self.load()
        state.update({
            "schema": 4,
            "site": "lenta.com",
            "owner_key": self.owner_key,
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
            "schema": 4,
            "site": "lenta.com",
            "owner_key": self.owner_key,
            "login_required": False,
            "authenticated": True,
            "last_login_check": time.time(),
            "account_hint": str(account_hint or "")[:200],
        })
        self._save(state)
        return state

    def remember_store(self, store_id: str | int, *, channel: str = "lo") -> dict[str, Any]:
        """Запомнить магазин/канал только внутри профиля конкретного пользователя."""
        value = str(store_id or "").strip()
        if not value:
            raise ValueError("store_id не может быть пустым")
        state = self.load()
        state.update({
            "schema": 4,
            "site": "lenta.com",
            "owner_key": self.owner_key,
            "store_id": value[:120],
            "channel": str(channel or "lo").strip()[:20] or "lo",
            "last_store_update": time.time(),
        })
        self._save(state)
        return state

    def open_login_window(self, url: str = "https://lenta.com/") -> dict[str, Any]:
        """Открыть профиль так, чтобы Browser Use мог повторно использовать окно.

        Авторизацию и SMS/2FA выполняет человек в видимом Edge/Chrome.
        Не закрываем его окно и не запускаем второй процесс с занятым профилем.
        """
        browser = ensure_system_browser(auto_install=True)
        existing = profile_cdp_url(self.profile_dir)
        if existing:
            self.mark_login_required("Окно авторизации доступно для подключения через CDP")
            return {
                "browser": browser.name,
                "profile_dir": str(self.profile_dir),
                "owner_key": self.owner_key,
                "url": str(url or "https://lenta.com/"),
                "reused": True,
            }
        launch_policy = chromium_launch_policy()
        if not launch_policy.can_open_window:
            raise RuntimeError(
                "Для первоначальной авторизации в «Ленте» нужна графическая сессия DISPLAY/Wayland. "
                "Публичный каталог доступен через headless Browser Agent."
            )
        if profile_may_be_in_use(self.profile_dir):
            raise RuntimeError(profile_busy_message())
        args = [
            browser.executable,
            f"--user-data-dir={self.profile_dir}",
            "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            *launch_policy.args,
            str(url or "https://lenta.com/"),
        ]
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        process = subprocess.Popen(  # noqa: S603
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        # DevToolsActivePort создаётся асинхронно после старта Chrome/Edge.
        # Подтверждаем, что запущенное окно действительно подключаемо.
        connected = False
        for _ in range(40):
            if profile_cdp_url(self.profile_dir, timeout=0.2):
                connected = True
                break
            if process.poll() is not None:
                break
            time.sleep(0.15)
        if not connected:
            raise RuntimeError(
                "Окно Edge/Chrome запустилось без доступного CDP. "
                "Закройте окно отдельного профиля «Ленты» и повторите вход; "
                "при повторении проверьте защитные политики браузера и запуск от имени пользователя."
            )
        self.mark_login_required("Открыто окно авторизации с локальным CDP")
        return {
            "browser": browser.name,
            "profile_dir": str(self.profile_dir),
            "owner_key": self.owner_key,
            "url": str(url or "https://lenta.com/"),
            "reused": False,
        }

    def clear(self) -> dict[str, Any]:
        previous = self.load()
        state = {
            "schema": 4,
            "site": "lenta.com",
            "owner_key": self.owner_key,
            "login_required": True,
            "authenticated": False,
            "last_login_check": time.time(),
            "delivery_mode": previous.get("delivery_mode", "account"),
            "payment_mode": previous.get("payment_mode", "account"),
            "substitution_policy": previous.get("substitution_policy", "automatic"),
        }
        self._save(state)
        return state

    def status(self) -> dict[str, Any]:
        state = self.load()
        return {
            "site": "lenta.com",
            "owner_key": self.owner_key,
            "profile_dir": str(self.profile_dir),
            "persistent_profile": True,
            "isolated_per_user": True,
            "stores_passwords": False,
            "stores_card_details": False,
            "authenticated": bool(state.get("authenticated")) and not bool(state.get("login_required")),
            "login_required": bool(state.get("login_required", True)),
            "last_login_check": state.get("last_login_check"),
            "account_hint": str(state.get("account_hint") or ""),
            "store_id": str(state.get("store_id") or ""),
            "channel": str(state.get("channel") or "lo"),
            "preferences": self.preferences(),
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
