from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import os
from pathlib import Path


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob_from_bytes(data: bytes):
    if not data:
        buffer = (ctypes.c_byte * 1)()
        return _DataBlob(0, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer
    buffer = (ctypes.c_byte * len(data)).from_buffer_copy(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _dpapi_protect(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source, keepalive = _blob_from_bytes(data)
    target = _DataBlob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(source),
        "Leshy AI local secret",
        None,
        None,
        None,
        0x1,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(target),
    )
    _ = keepalive
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source, keepalive = _blob_from_bytes(data)
    target = _DataBlob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        0x1,
        ctypes.byref(target),
    )
    _ = keepalive
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


def save_secret(path: Path | str, value: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = str(value).encode("utf-8")
    if os.name == "nt":
        payload = b"dpapi:" + base64.b64encode(_dpapi_protect(raw))
    else:
        payload = b"plain:" + base64.b64encode(raw)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_secret(path: Path | str) -> str:
    path = Path(path)
    if not path.is_file():
        return ""
    payload = path.read_bytes().strip()
    if not payload:
        return ""
    if payload.startswith(b"dpapi:"):
        if os.name != "nt":
            raise RuntimeError("DPAPI-секрет можно расшифровать только в исходном Windows-профиле")
        raw = _dpapi_unprotect(base64.b64decode(payload[6:], validate=True))
    elif payload.startswith(b"plain:"):
        raw = base64.b64decode(payload[6:], validate=True)
    else:
        # Миграция раннего plaintext-формата: прочитать один раз; следующий save переведёт его в новый формат.
        raw = payload
    return raw.decode("utf-8").strip()
