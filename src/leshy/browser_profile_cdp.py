from __future__ import annotations

"""Безопасное повторное подключение к личному Chromium-профилю Лешего.

На Windows пользователь может оставить открытым окно входа в «Ленту».
Нельзя одновременно запускать второй браузер с тем же user-data-dir:
Chromium блокирует профиль, и Browser Use выдаёт ошибку CDP.
Окно входа запускаем с отладкой на динамическом localhost-порту; повторные
действия Browser Use подключаются к нему, не копируя cookies.
"""

import json
from pathlib import Path
from urllib import request
from urllib.parse import urlsplit


def profile_cdp_url(profile_dir: Path | str, *, timeout: float = 0.7) -> str | None:
    """Возвращает URL только *живого* локального CDP той же директории.

    Порт из DevToolsActivePort может быть устаревшим либо указывать на чужой
    локальный процесс. Поэтому сверяем websocket path с /json/version.
    Никогда не обращаемся к URL из внешнего запроса пользователя.
    """
    try:
        lines = (Path(profile_dir) / "DevToolsActivePort").read_text(
            encoding="utf-8"
        ).splitlines()
        if len(lines) < 2:
            return None
        port = int(lines[0].strip())
        ws_path = lines[1].strip()
        if not (1024 <= port <= 65535) or not ws_path.startswith("/devtools/browser/"):
            return None
        endpoint = f"http://127.0.0.1:{port}"
        opener = request.build_opener(request.ProxyHandler({}))
        with opener.open(endpoint + "/json/version", timeout=timeout) as response:
            data = json.load(response)
        actual = str(data.get("webSocketDebuggerUrl") or "")
        parsed = urlsplit(actual)
        if (
            parsed.scheme != "ws"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.port != port
            or parsed.path != ws_path
        ):
            return None
        return endpoint
    except (OSError, ValueError, TypeError, KeyError, TimeoutError, UnicodeError, json.JSONDecodeError):
        return None
    except Exception:
        # Некорректная/отключенная отладка не должна ломать чат.
        return None


def profile_may_be_in_use(profile_dir: Path | str) -> bool:
    root = Path(profile_dir)
    return any((root / name).exists() for name in (
        "SingletonLock", "SingletonSocket", "lockfile",
    ))


def profile_busy_message() -> str:
    return (
        "Окно авторизации «Ленты» заняло профиль Edge/Chrome, "
        "но было открыто без подключаемого CDP. Закройте только окно браузера "
        "с отдельным профилем «Ленты» (обычные окна Edge закрывать не нужно), "
        "затем напишите Лешему «войти в Ленту» и авторизуйтесь в новом окне. "
        "В новой версии Леший сможет использовать это окно без закрытия. "
        "Cookies и авторизация профиля сохраняются."
    )


__all__ = ["profile_cdp_url", "profile_may_be_in_use", "profile_busy_message"]
