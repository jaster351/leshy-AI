"""Owner-scoped directory setting for SuperAgent credential files.

The directory grants only a narrow, read-only lookup scope. No credentials
are copied into the DB, responses, logs, Knowledge or swarm messages.
Remote connections and secret parsing require separate approved tools.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from .storage import utcnow

_KEY_PREFIX = "superagent.credentials_dir."
_MAX_PATH = 2048
_MAX_ENTRIES = 20000


def _owner_key(owner_id: str) -> str:
    owner = str(owner_id or "").strip()
    if not owner or owner == "web:local":
        raise PermissionError("Требуется подтверждённая Web-сессия владельца")
    return _KEY_PREFIX + hashlib.sha256(owner.encode("utf-8")).hexdigest()


def _stored_path(storage: Any, owner_id: str) -> str:
    with storage.connection() as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key=?", (_owner_key(owner_id),)
        ).fetchone()
    return str(row["value"]) if row is not None else ""


def get_credential_folder(storage: Any, owner_id: str) -> dict[str, Any]:
    """Return only the configured directory, never file contents or secrets."""
    value = _stored_path(storage, owner_id)
    available = bool(value) and Path(value).is_dir() and not Path(value).is_symlink()
    return {
        "path": value,
        "configured": bool(value),
        "available": available,
        "scope": "leshy_host",
    }


def set_credential_folder(storage: Any, owner_id: str, raw_path: str) -> dict[str, Any]:
    """Set or clear a directory explicitly chosen by this Web owner."""
    key = _owner_key(owner_id)
    if not isinstance(raw_path, str):
        raise ValueError("Путь к папке должен быть строкой")
    value = raw_path.strip()
    if len(value) > _MAX_PATH or any(ord(ch) < 32 for ch in value):
        raise ValueError("Некорректный путь к папке")
    if value:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            raise ValueError("Укажите абсолютный путь на компьютере Лешего")
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("Папка не существует или недоступна") from exc
        if not resolved.is_dir():
            raise ValueError("Указанный путь не является папкой")
        if resolved == Path(resolved.anchor):
            raise ValueError("Нельзя разрешать чтение целого диска или корневой папки")
        if not os.access(resolved, os.R_OK | os.X_OK):
            raise ValueError("У Лешего нет прав читать эту папку")
        value = str(resolved)
    with storage.connection() as conn:
        if value:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        else:
            conn.execute("DELETE FROM meta WHERE key=?", (key,))
    # Do not write either directory names or credential contents to the audit log.
    storage.audit("superagent.credential_folder.changed", {
        "configured": bool(value), "updated_at": utcnow(),
    })
    return get_credential_folder(storage, owner_id)


def find_credential_file(storage: Any, owner_id: str, filename: str) -> Path:
    """Resolve an exact basename inside the configured tree, without reading it.

    Reject traversal, symlinks escaping the tree and ambiguous file names.
    This helper is for a future credential-handling tool, never for the LLM.
    """
    if not isinstance(filename, str) or not filename or len(filename) > 255:
        raise ValueError("Укажите имя файла учётных данных")
    if filename in {".", ".."} or any(
        ch in filename for ch in ("/", "\\", "\x00", ":")
    ) or any(ord(ch) < 32 for ch in filename):
        raise ValueError("Укажите только название файла без пути")
    configured = _stored_path(storage, owner_id)
    if not configured:
        raise PermissionError("Сначала укажите папку в настройках SuperAgent")
    configured_root = Path(configured)
    # Do not follow a swapped symlink after the owner saved the original folder.
    if configured_root.is_symlink():
        raise PermissionError("Папка была заменена ссылкой; настройте её заново")
    root = configured_root.resolve(strict=True)
    if root != configured_root or not root.is_dir():
        raise FileNotFoundError("Папка учётных данных больше не доступна")
    found: list[Path] = []
    inspected = 0

    def fail_walk(error: OSError) -> None:
        raise PermissionError("Не удалось прочитать одну из вложенных папок") from error

    for base, dirs, files in os.walk(root, followlinks=False, onerror=fail_walk):
        dirs[:] = [name for name in dirs if not (Path(base) / name).is_symlink()]
        inspected += len(dirs) + len(files)
        if inspected > _MAX_ENTRIES:
            raise ValueError("Слишком большая папка для безопасного поиска")
        for candidate_name in files:
            if candidate_name.casefold() != filename.casefold():
                continue
            candidate = Path(base) / candidate_name
            if candidate.is_symlink() or not candidate.is_file():
                continue
            actual = candidate.resolve(strict=True)
            if not actual.is_relative_to(root):
                continue
            found.append(actual)
            if len(found) > 1:
                raise ValueError("Найдено несколько файлов с таким именем; уточните папку")
    if not found:
        raise FileNotFoundError("Файл с указанным именем не найден")
    return found[0]


__all__ = ["get_credential_folder", "set_credential_folder", "find_credential_file"]
