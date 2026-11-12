"""SSH-based OS verification using user-owned credential files.

Only fixed read-only commands are executed. No secret text is sent to the LLM,
audit logs, Knowledge or swarm. Unknown SSH host keys are rejected.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from .superagent_credential_settings import find_credential_file
from .remote_os_fingerprint import validate_target

_MAX_FILE = 16384
_NAME = re.compile(r"(?iu)\b(?:файл(?:е|а|ом)?|file)\s+(?:[«\"'`])?([\w.-]+\.(?:txt|ini|json|env))(?=[\s.,;!?»\"'`]|$)")
_LOGIN_KEYS = frozenset({"login", "username", "user", "логин", "пользователь", "имя_пользователя"})
_PASSWORD_KEYS = frozenset({"password", "pass", "passwd", "пароль"})
_HOST_KEYS = frozenset({"host", "ip", "server", "сервер", "адрес"})


def credential_file_from_request(request: str) -> str | None:
    matches = list(dict.fromkeys(m.group(1) for m in _NAME.finditer(str(request or ""))))
    return matches[0] if len(matches) == 1 else None


def _read_text(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_size > _MAX_FILE:
            raise ValueError("Файл доступа должен быть обычным файлом размером до 16 КБ")
        with os.fdopen(fd, "rb") as file:
            fd = -1
            raw = file.read(_MAX_FILE + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    if len(raw) > _MAX_FILE:
        raise ValueError("Файл доступа слишком большой")
    try:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return raw.decode("utf-16")
        return raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("Не удалось прочитать кодировку файла доступа (UTF-8/UTF-16)") from exc


def _mapping(text: str, host: str) -> tuple[str, str]:
    """Parse one unambiguous account. Never include secret values in errors."""
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        entries: list[dict[str, Any]]
        if isinstance(parsed.get("servers"), list):
            entries = [x for x in parsed["servers"] if isinstance(x, dict)]
        else:
            entries = [parsed]
        selected = []
        for entry in entries:
            flat = {str(k).lower().strip(): v for k, v in entry.items()}
            target = next((str(v).strip() for k, v in flat.items() if k in _HOST_KEYS), "")
            if not target or target == host:
                selected.append(flat)
        if len(selected) != 1:
            raise ValueError("Не удалось однозначно выбрать запись сервера в файле доступа")
        fields = selected[0]
    else:
        fields = {}
        seen = set()
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";", "[")):
                continue
            match = re.match(r"^([\w\u0400-\u04ff -]+)\s*(?:=|:)\s*(.*?)\s*$", line)
            if not match:
                raise ValueError("Неоднозначный формат файла доступа: используйте login= и password=")
            key, value = match.group(1).lower().strip().replace(" ", "_"), match.group(2)
            if key in _LOGIN_KEYS | _PASSWORD_KEYS | _HOST_KEYS:
                if key in seen:
                    raise ValueError("В файле доступа найдено несколько одинаковых полей")
                seen.add(key)
                fields[key] = value
        target = next((str(v).strip() for k, v in fields.items() if k in _HOST_KEYS), "")
        if target and target != host:
            raise ValueError("Адрес в файле доступа не совпадает с указанным сервером")
    login_values = [str(v).strip() for k, v in fields.items() if k in _LOGIN_KEYS]
    pass_values = [str(v) for k, v in fields.items() if k in _PASSWORD_KEYS]
    if len(login_values) != 1 or len(pass_values) != 1 or not login_values[0] or not pass_values[0]:
        raise ValueError("В файле требуется ровно один логин и один пароль")
    if any(ord(ch) < 32 for ch in login_values[0]) or len(login_values[0]) > 256:
        raise ValueError("Некорректный логин")
    return login_values[0], pass_values[0]


def verify_ssh_os(storage: Any, owner_id: str, host: str, credential_filename: str) -> dict[str, Any]:
    """Verify the actual OS with uname or Windows ver; never run updates."""
    target = validate_target(host)
    filepath = find_credential_file(storage, owner_id, credential_filename)
    login, password = _mapping(_read_text(filepath), target)
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("SSH-компонент paramiko не установлен") from exc
    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()
        known_hosts = Path.home() / ".ssh" / "known_hosts"
        if known_hosts.is_file():
            client.load_host_keys(str(known_hosts))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(hostname=target, port=22, username=login, password=password,
                       timeout=5, banner_timeout=5, auth_timeout=5,
                       allow_agent=False, look_for_keys=False)
        for command, kind in (("uname -s", "unix"), ("cmd.exe /c ver", "windows")):
            stdin, stdout, stderr = client.exec_command(command, get_pty=False, timeout=5)
            stdin.close()
            text = stdout.read(250).decode("utf-8", errors="replace")
            code = stdout.channel.recv_exit_status()
            if code != 0:
                continue
            clean = text.strip()
            if kind == "unix":
                if clean == "Linux":
                    return {"host": target, "os_family": "Linux", "verified": True, "method": "ssh:uname"}
                if clean in {"FreeBSD", "OpenBSD", "NetBSD", "Darwin"}:
                    return {"host": target, "os_family": clean, "verified": True, "method": "ssh:uname"}
            elif "windows" in clean.lower():
                return {"host": target, "os_family": "Windows", "verified": True, "method": "ssh:ver"}
        return {"host": target, "os_family": "unknown", "verified": False, "method": "ssh:unknown"}
    finally:
        client.close()


__all__ = ["credential_file_from_request", "verify_ssh_os"]
