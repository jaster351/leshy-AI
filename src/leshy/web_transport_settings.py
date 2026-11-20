"""Настройки HTTP/HTTPS и закрытое хранилище транспортного слоя."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading


DEFAULTS = {"http_enabled": True, "http_port": 8765, "https_enabled": True,
            "https_port": 8766, "hostname": ""}


def _windows_private_acl(path: Path) -> None:
    """Ограничить доступ текущим Windows-пользователем и SYSTEM без UAC."""
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    adv.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                                            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    adv.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
                                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    adv.SetNamedSecurityInfoW.restype = wintypes.DWORD
    token = wintypes.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise OSError("Не удалось защитить хранилище HTTPS")
    sid_text = wintypes.LPWSTR()
    descriptor = ctypes.c_void_p()
    try:
        needed = wintypes.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not adv.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
            raise OSError("Не удалось определить владельца хранилища HTTPS")
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise OSError("Не удалось определить права хранилища HTTPS")
        inherit = "OICI" if path.is_dir() else ""
        sddl = f"D:P(A;{inherit};FA;;;{sid_text.value})(A;{inherit};FA;;;SY)"
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise OSError("Не удалось подготовить права хранилища HTTPS")
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not adv.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present),
                                             ctypes.byref(dacl), ctypes.byref(defaulted)):
            raise OSError("Не удалось прочитать права хранилища HTTPS")
        if adv.SetNamedSecurityInfoW(str(path), 1, 0x80000004, None, None, dacl, None):
            raise OSError("Не удалось применить закрытые права хранилища HTTPS")
    finally:
        if descriptor.value:
            kernel.LocalFree(descriptor)
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        kernel.CloseHandle(token)


def reject_link(path: Path) -> None:
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Символические ссылки в хранилище HTTPS запрещены")


def private_path(path: Path) -> None:
    reject_link(path)
    if os.name == "nt":
        _windows_private_acl(path)
    else:
        path.chmod(0o700 if path.is_dir() else 0o600)


def security_root(brain_dir: Path | str) -> Path:
    root = Path(brain_dir) / "web-security"
    reject_link(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_path(root)
    return root


def atomic_json(path: Path, value: dict) -> None:
    reject_link(path)
    fd, name = tempfile.mkstemp(prefix=".settings-", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        private_path(temp)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def hostname(value: object) -> str:
    text = str(value or "").strip().rstrip(".").lower()
    if not text:
        return ""
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        pass
    try:
        text = text.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Некорректное имя HTTPS-сервера") from exc
    if len(text) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                              for label in text.split(".")):
        raise ValueError("Введите имя сервера или IP без схемы, порта и пути")
    return text


def validate_settings(value: dict) -> dict:
    result = {key: value.get(key, default) for key, default in DEFAULTS.items()}
    for key in ("http_enabled", "https_enabled"):
        if type(result[key]) is not bool:
            raise ValueError("Состояние HTTP/HTTPS должно быть логическим")
    for key in ("http_port", "https_port"):
        if type(result[key]) is not int or not 1 <= result[key] <= 65535:
            raise ValueError("Порт должен быть целым числом от 1 до 65535")
    if result["http_port"] == result["https_port"]:
        raise ValueError("HTTP и HTTPS должны использовать разные порты")
    if not result["http_enabled"] and not result["https_enabled"]:
        raise ValueError("Нельзя выключить одновременно HTTP и HTTPS")
    result["hostname"] = hostname(result["hostname"])
    if not result["http_enabled"] and not result["hostname"]:
        raise ValueError("Для режима только HTTPS укажите имя сервера или IP из SAN сертификата")
    return result


class WebTransportSettings:
    def __init__(self, brain_dir: Path | str):
        self.root = security_root(brain_dir)
        self.path = self.root / "transport.json"
        self.lock = threading.RLock()

    def load(self) -> dict:
        with self.lock:
            reject_link(self.path)
            if not self.path.exists():
                return dict(DEFAULTS)
            if self.path.stat().st_size > 8192:
                raise ValueError("Файл настроек HTTP/HTTPS слишком большой")
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("Некорректный файл настроек HTTP/HTTPS")
            return validate_settings(value)

    def save(self, body: dict) -> dict:
        if body.get("confirm") is not True:
            raise ValueError("Подтвердите изменение сетевых настроек")
        with self.lock:
            value = validate_settings({**self.load(), **body})
            atomic_json(self.path, value)
            return value

    def effective(self, explicit_http_port: int | None = None) -> dict:
        value = self.load()
        override = explicit_http_port
        if override is None:
            env = os.getenv("LESHY_PORT")
            if env is not None:
                try:
                    override = int(env)
                except ValueError as exc:
                    raise ValueError("Некорректный LESHY_PORT") from exc
        if override is not None:
            value["http_port"] = override
        if os.getenv("LESHY_HTTPS_PORT") is not None:
            try:
                value["https_port"] = int(os.environ["LESHY_HTTPS_PORT"])
            except ValueError as exc:
                raise ValueError("Некорректный LESHY_HTTPS_PORT") from exc
        return validate_settings(value)


def browser_url(brain_dir: Path | str, explicit_http_port: int | None = None) -> str:
    value = WebTransportSettings(brain_dir).effective(explicit_http_port)
    if value["http_enabled"]:
        return f"http://127.0.0.1:{value['http_port']}/"
    host = value["hostname"] or "localhost"
    if ":" in host:
        host = "[" + host + "]"
    return f"https://{host}:{value['https_port']}/"
