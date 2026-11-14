"""Owner-scoped bridge between Leshy and the separate Parser for Plex NAS service.

No torrent parsing or downloading happens in Leshy. All commands are handled
by the separately installed Parser for Plex server.
"""
from __future__ import annotations

import ipaddress
import json
from pathlib import Path
import re
import threading
from urllib import error, parse, request

from .secret_store import load_secret, save_secret


DEFAULT_ADDRESS = "http://192.168.0.19:8766"
_INTENT = re.compile(
    r"^\s*(?:(?:алиса[,! ]*)?попроси\s+помощника\s+по\s+дому\s+)?"
    r"(?:скачай|скачать|загрузи|загрузить|найди\s+торрент\s+на|"
    r"найти\s+торрент\s+на)\s+"
    r"(фильм|кино|группу|исполнителя|музыку|альбом|песню|трек)\s+(.+?)\s*[.!?]*$",
    re.IGNORECASE,
)
_LOCK = threading.RLock()
_OPENER = request.build_opener(request.ProxyHandler({}))


def parse_request(message: str) -> tuple[str, str] | None:
    match = _INTENT.fullmatch(str(message or "").strip())
    if not match:
        return None
    kind = "movie" if match.group(1).lower() in {"фильм", "кино"} else "music"
    title = match.group(2).strip().strip("!?., ")
    return (kind, title) if 0 < len(title) <= 200 else None


def _base_url(url: str) -> str:
    raw = str(url or "").strip().rstrip("/")
    u = parse.urlsplit(raw)
    if not u.hostname or u.username or u.password or u.query or u.fragment or u.scheme not in {"http", "https"}:
        raise ValueError("Укажите http(s) адрес сервера Parser for Plex")
    if u.path not in {"", "/", "/alice", "/v1/jobs"}:
        raise ValueError("Адрес должен заканчиваться на порт, /alice или /v1/jobs")
    # Unencrypted HTTP is acceptable only inside the user's trusted LAN.
    if u.scheme == "http":
        try:
            host = ipaddress.ip_address(u.hostname)
        except ValueError:
            raise ValueError("Для HTTP используйте LAN IP; для DNS-имён нужен HTTPS") from None
        if not (host.is_private or host.is_loopback):
            raise ValueError("Для внешнего адреса требуется HTTPS")
    try:
        port = u.port
    except ValueError:
        raise ValueError("Некорректный порт") from None
    if port is not None and not (1 <= port <= 65535):
        raise ValueError("Некорректный порт")
    return parse.urlunsplit((u.scheme, u.netloc, "", "", ""))


class TorrentParserBridge:
    def __init__(self, brain_dir: str | Path):
        root = Path(brain_dir)
        self.settings = root / "settings" / "torrent_parser.json"
        self.secret = root / "secrets" / "torrent_parser_token.secret"

    def _read(self):
        try:
            data = json.loads(self.settings.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def status(self):
        data = self._read()
        return {
            "address": data.get("address") or DEFAULT_ADDRESS,
            "configured": bool(data.get("configured")),
            "paired": bool(load_secret(self.secret)),
        }

    def owner_allowed(self, owner_id: str) -> bool:
        data = self._read()
        return bool(data.get("configured") and data.get("owner_id")
                    and str(owner_id) == data["owner_id"])

    def configure(self, address: str, owner_id: str):
        base = _base_url(address)
        owner = str(owner_id or "").strip()
        if not owner or owner == "web:local":
            raise PermissionError("Требуется вход владельца в веб-интерфейс Лешего")
        with _LOCK:
            previous = self._read()
            if previous.get("address") != base or previous.get("owner_id") != owner:
                self.secret.unlink(missing_ok=True)
            payload = {"address": base, "owner_id": owner, "configured": True}
            self.settings.parent.mkdir(parents=True, exist_ok=True)
            temp = self.settings.with_suffix(".json.tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.settings)
            try:
                self.settings.chmod(0o600)
            except OSError:
                pass
        return self.status()

    @staticmethod
    def _request(url, body=None, secret=None, timeout=4.0):
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if secret:
            headers["Authorization"] = "Bearer " + secret
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
        try:
            with _OPENER.open(req, timeout=timeout) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError("Слишком большой ответ Parser for Plex")
                result = json.loads(raw.decode("utf-8"))
                return result if isinstance(result, dict) else {}
        except error.HTTPError as exc:
            if exc.code == 401:
                raise PermissionError("Ключ парсера не принят. Сброс выполняется только на NAS") from None
            if exc.code == 409:
                raise PermissionError("Первичная привязка закрыта или уже выполнена. Для повторной нужен reset на NAS") from None
            raise RuntimeError("Parser for Plex: HTTP " + str(exc.code)) from None
        except (error.URLError, TimeoutError, OSError) as exc:
            raise ConnectionError("Parser for Plex недоступен: " + type(exc).__name__) from None

    def test(self):
        data = self._read()
        addr = _base_url(data.get("address") or DEFAULT_ADDRESS)
        return self._request(addr + "/health", timeout=3)

    def pair(self, owner_id: str):
        if not self.owner_allowed(owner_id):
            raise PermissionError("Настройте адрес и войдите под владельцем")
        if load_secret(self.secret):
            return self.status()
        with _LOCK:
            if load_secret(self.secret):
                return self.status()
            base = self._read()["address"]
            reply = self._request(base + "/v1/pair", {"client_name": "Leshy"}, timeout=5)
            received = str(reply.get("token") or "")
            if len(received) < 32:
                raise ValueError("Parser for Plex не вернул действительный ключ")
            save_secret(self.secret, received)
        return self.status()

    def submit(self, owner_id: str, kind: str, title: str):
        if not self.owner_allowed(owner_id):
            return {"status": "not_configured", "message": "Настройте подключение к Parser for Plex в параметрах Лешего."}
        secret = load_secret(self.secret)
        if not secret:
            return {"status": "unpaired", "message": "Откройте настройки Лешего и выполните первичную привязку Parser for Plex."}
        data = self._request(self._read()["address"] + "/v1/jobs",
                             {"kind": kind, "query": title, "wait_seconds": 2.5},
                             secret=secret, timeout=6)
        return data

    def execute_chat(self, owner_id: str, message: str):
        command = parse_request(message)
        if not command:
            return None
        kind, title = command
        try:
            info = self.submit(owner_id, kind, title)
            return str(info.get("message") or "Команда принята.")
        except (ConnectionError, ValueError, RuntimeError, PermissionError) as exc:
            return str(exc)
