"""Private support facts bootstrap, no provider-specific data or keys in Git.

A signed Fernet profile can be imported from the owner's existing local archive
at startup. The key is stored only using Windows CurrentUser DPAPI (or an existing
explicit environment override). This does NOT synchronize keys to the swarm.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import zipfile


_ARCHIVE_PREFIX = "Local-Support-Profile"
_PROFILE = "support_profile.enc"
_KEY = "support_profile.key"
_DPAPI_FILE = "support.key.dpapi"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _crypt(data: bytes, *, unprotect: bool) -> bytes:
    """Windows CurrentUser DPAPI; secret is never passed on the command line."""
    if os.name != "nt":
        raise OSError("DPAPI is supported on Windows only")
    c = ctypes.WinDLL("crypt32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    name = "CryptUnprotectData" if unprotect else "CryptProtectData"
    fn = getattr(c, name)
    if unprotect:
        fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    else:
        fn.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    fn.restype = wintypes.BOOL
    k.LocalFree.argtypes = [ctypes.c_void_p]
    k.LocalFree.restype = ctypes.c_void_p
    input_buffer = ctypes.create_string_buffer(data)
    input_blob = _Blob(len(data), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte)))
    output_blob = _Blob()
    if unprotect:
        ok = fn(ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob))
    else:
        ok = fn(ctypes.byref(input_blob), "Leshy private support key", None, None, None, 0, ctypes.byref(output_blob))
    if not ok:
        raise OSError(ctypes.get_last_error(), "Windows DPAPI operation failed")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        k.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


def support_key(brain_dir: Path | str) -> bytes | None:
    """Existing operator key or DPAPI-bound key; never return invalid keys."""
    candidate = os.getenv("LESHY_SUPPORT_PROFILE_KEY", "").strip()
    if candidate:
        try:
            from cryptography.fernet import Fernet
            key = candidate.encode("ascii")
            Fernet(key)
            return key
        except (ImportError, UnicodeError, ValueError):
            return None
    path = Path(brain_dir) / "private" / _DPAPI_FILE
    if os.name != "nt" or not path.is_file() or path.is_symlink():
        return None
    try:
        if path.stat().st_size > 2048:
            return None
        from cryptography.fernet import Fernet
        key = _crypt(path.read_bytes(), unprotect=True)
        Fernet(key)
        return key
    except (OSError, ValueError, ImportError):
        return None


def _profile_valid(ciphertext: bytes, key: bytes) -> bool:
    try:
        from cryptography.fernet import Fernet
        clear = Fernet(key).decrypt(ciphertext)
        payload = json.loads(clear.decode("utf-8"))
        return isinstance(payload, dict) and isinstance(payload.get("facts"), list)
    except (ImportError, UnicodeError, ValueError):
        return False
    except Exception:
        return False


def _candidate_archives(install_root: Path, brain_dir: Path):
    places = [
        brain_dir / "private",
        install_root,
        Path(os.environ.get("USERPROFILE") or Path.home()) / "Downloads",
        Path.home() / "Downloads",
    ]
    found = set()
    for directory in places:
        try:
            for candidate in sorted(directory.glob(_ARCHIVE_PREFIX + "*.zip")):
                if candidate.is_symlink() or candidate in found:
                    continue
                found.add(candidate)
                if candidate.is_file() and candidate.stat().st_size <= 1_000_000:
                    yield candidate
        except OSError:
            continue


def bootstrap_private_support(install_root: Path | str, brain_dir: Path | str) -> bool:
    """Zero-command discovery of owner's downloaded bundle; fail closed.

    Returns True when an encrypted profile with matching operator key is active.
    Does not delete anything in Downloads or change permanent environment vars.
    """
    root = Path(install_root)
    brain = Path(brain_dir)
    folder = brain / "private"
    target = folder / _PROFILE
    key = support_key(brain)
    if key and target.is_file() and _profile_valid(target.read_bytes(), key):
        return True

    for archive in _candidate_archives(root, brain):
        try:
            with zipfile.ZipFile(archive) as item:
                if any(name not in {_PROFILE, _KEY, "README.txt", "Install-Private-Support.ps1"} for name in item.namelist()):
                    continue
                secret = item.read(_KEY)
                encrypted = item.read(_PROFILE)
            if len(encrypted) > 196608 or not _profile_valid(encrypted, secret.strip()):
                continue
            if not (key or os.name == "nt"):
                continue
            folder.mkdir(parents=True, exist_ok=True)
            # Validate first, then atomically install. No plaintext files created.
            if not key:
                protected = _crypt(secret.strip(), unprotect=False)
                tmp_key = folder / (_DPAPI_FILE + ".tmp")
                tmp_key.write_bytes(protected)
                tmp_key.replace(folder / _DPAPI_FILE)
            else:
                if not _profile_valid(encrypted, key):
                    continue
            tmp_profile = folder / (_PROFILE + ".tmp")
            tmp_profile.write_bytes(encrypted)
            tmp_profile.replace(target)
            return True
        except (OSError, ValueError, RuntimeError, KeyError, zipfile.BadZipFile):
            continue
    return False


__all__ = ["bootstrap_private_support", "support_key"]
