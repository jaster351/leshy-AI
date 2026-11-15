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
import sys
import zlib
import zipfile


_ARCHIVE_PREFIX = "Local-Support-Profile"
_PROFILE = "support_profile.enc"
_KEY = "support_profile.key"
_DPAPI_FILE = "support.key.dpapi"
_BUNDLED_PROFILE = "support_reference.enc"
_KEY_DOWNLOAD = "Leshy-Support-Key.key"


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


def decrypt_support_profile(ciphertext: bytes, key: bytes) -> dict | None:
    """Authenticated Fernet decrypt; accepts both original JSON and zipped JSON.

    Bound the expanded size so a modified public ciphertext cannot exhaust RAM.
    """
    try:
        from cryptography.fernet import Fernet
        if len(ciphertext) > 196608:
            return None
        raw = Fernet(key).decrypt(ciphertext.strip())
        if not raw.startswith(b"{"):
            decoder = zlib.decompressobj()
            raw = decoder.decompress(raw, 131072)
            if not decoder.eof or decoder.unconsumed_tail:
                return None
        if len(raw) > 131072:
            return None
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) and isinstance(value.get("facts"), list) else None
    except Exception:
        return None


def _profile_valid(ciphertext: bytes, key: bytes) -> bool:
    return decrypt_support_profile(ciphertext, key) is not None


def _bundled_ciphertext(install_root: Path) -> bytes:
    """PyInstaller --onedir data lives under sys._MEIPASS, not exe directory."""
    paths = [install_root / "resources" / _BUNDLED_PROFILE]
    base = getattr(sys, "_MEIPASS", None)
    if base:
        paths.insert(0, Path(base) / "resources" / _BUNDLED_PROFILE)
    for file in paths:
        try:
            if file.is_file() and not file.is_symlink() and file.stat().st_size <= 196608:
                return file.read_bytes()
        except OSError:
            continue
    return b""


def _candidate_keys(install_root: Path, brain_dir: Path):
    locations = [
        brain_dir / "private",
        install_root,
        Path(os.environ.get("USERPROFILE") or Path.home()) / "Downloads",
        Path.home() / "Downloads",
    ]
    seen = set()
    for directory in locations:
        path = directory / _KEY_DOWNLOAD
        try:
            if path not in seen and path.is_file() and not path.is_symlink() and path.stat().st_size < 200:
                seen.add(path)
                yield path.read_bytes().strip()
        except OSError:
            continue


def _save_profile(folder: Path, encrypted: bytes, operator_key: bytes, existing_key: bytes | None) -> bool:
    if not _profile_valid(encrypted, operator_key):
        return False
    if existing_key and existing_key != operator_key:
        return False
    if existing_key is None and os.name != "nt":
        return False
    folder.mkdir(parents=True, exist_ok=True)
    if existing_key is None:
        protected = _crypt(operator_key, unprotect=False)
        temporary = folder / (_DPAPI_FILE + ".tmp")
        temporary.write_bytes(protected)
        temporary.replace(folder / _DPAPI_FILE)
    temp_profile = folder / (_PROFILE + ".tmp")
    temp_profile.write_bytes(encrypted)
    temp_profile.replace(folder / _PROFILE)
    return True


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
    """Discover the owner key once; encrypted reference is already in the EXE.

    The key is never in Git, public build artifacts or program output.
    Only CurrentUser DPAPI (or an explicit owner environment override) unlocks.
    """
    root = Path(install_root)
    brain = Path(brain_dir)
    folder = brain / "private"
    target = folder / _PROFILE
    key = support_key(brain)
    try:
        if key and target.is_file() and _profile_valid(target.read_bytes(), key):
            return True
    except OSError:
        pass

    # A user who previously installed the key can immediately use a newly
    # published encrypted knowledge revision without another download.
    bundled = _bundled_ciphertext(root)
    if key and bundled:
        try:
            if _save_profile(folder, bundled, key, key):
                return True
        except OSError:
            pass

    # Prefer only a downloaded key: public repo already contains ciphertext.
    if bundled:
        for secret in _candidate_keys(root, brain):
            try:
                if _save_profile(folder, bundled, secret, key):
                    return True
            except OSError:
                continue

    # Backward compatibility with the already distributed owner-only ZIP.
    for archive in _candidate_archives(root, brain):
        try:
            with zipfile.ZipFile(archive) as item:
                if any(name not in {_PROFILE, _KEY, "README.txt", "Install-Private-Support.ps1"} for name in item.namelist()):
                    continue
                secret = item.read(_KEY).strip()
                encrypted = item.read(_PROFILE)
            # Use the ZIP only to unlock the bundled version if one exists.
            ciphertext = bundled if bundled and _profile_valid(bundled, secret) else encrypted
            if _save_profile(folder, ciphertext, secret, key):
                return True
        except (OSError, ValueError, RuntimeError, KeyError, zipfile.BadZipFile):
            continue
    return False


__all__ = ["bootstrap_private_support", "decrypt_support_profile", "support_key"]
