from __future__ import annotations

"""Определение режима локального Chromium без вызовов LLM.

Ubuntu Server не требует DISPLAY, Windows с рабочим столом по умолчанию
запускает видимый браузер. В Linux от root Chromium не поддерживает sandbox:
это исключение только для тестовых/root-сред (рекомендуется обычный user).
"""

from dataclasses import dataclass
import os
import platform
from typing import Mapping


@dataclass(frozen=True)
class ChromiumLaunchPolicy:
    headless: bool
    chromium_sandbox: bool
    args: tuple[str, ...]
    can_open_window: bool
    root_linux: bool


def chromium_launch_policy(
    *,
    environment: Mapping[str, str] | None = None,
    system: str | None = None,
    effective_uid: int | None = None,
) -> ChromiumLaunchPolicy:
    env = os.environ if environment is None else environment
    system_name = (system or platform.system()).lower()
    linux = system_name == "linux"
    has_display = not linux or bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))
    raw = str(env.get("LESHY_BROWSER_HEADLESS", "")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        headless = True
    elif raw in {"0", "false", "no", "off"}:
        headless = False
    else:
        headless = linux and not has_display

    # На сервере без X11/Wayland нельзя принудительно включить видимый браузер.
    if linux and not has_display:
        headless = True

    if effective_uid is None:
        getuid = getattr(os, "geteuid", None)
        effective_uid = int(getuid()) if callable(getuid) else -1
    root_linux = linux and effective_uid == 0
    args: tuple[str, ...] = ("--disable-dev-shm-usage",) if linux else ()
    if root_linux:
        args += ("--no-sandbox", "--disable-setuid-sandbox")
    return ChromiumLaunchPolicy(
        headless=headless,
        chromium_sandbox=not root_linux,
        args=args,
        can_open_window=has_display,
        root_linux=root_linux,
    )


__all__ = ["ChromiumLaunchPolicy", "chromium_launch_policy"]
