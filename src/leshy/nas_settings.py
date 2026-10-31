from __future__ import annotations

from dataclasses import asdict, dataclass
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
from typing import Any

from .secret_store import load_secret, save_secret


_DEFAULT_TORRENT_WATCH = r"\\Nas\для торрент файлов"


@dataclass(slots=True)
class NasSettings:
    root: str = ""
    torrent_watch: str = _DEFAULT_TORRENT_WATCH
    music: str = ""
    video: str = ""
    smb_server: str = r"\\Nas"
    smb_username: str = ""
    smb_domain: str = ""

    def normalized(self) -> "NasSettings":
        def clean(value: str, limit: int = 4000) -> str:
            return str(value or "").strip()[:limit]

        server = clean(self.smb_server)
        if server and not server.startswith("\\\\"):
            server = "\\\\" + server.lstrip("\\")
        return NasSettings(
            root=clean(self.root),
            torrent_watch=clean(self.torrent_watch) or _DEFAULT_TORRENT_WATCH,
            music=clean(self.music),
            video=clean(self.video),
            smb_server=server or r"\\Nas",
            smb_username=clean(self.smb_username, 500),
            smb_domain=clean(self.smb_domain, 500),
        )


class NasSettingsStore:
    """Постоянные настройки NAS и зашифрованный SMB-пароль.

    На Windows пароль защищается DPAPI текущего профиля пользователя. JSON никогда
    не содержит пароль. На других ОС используется существующий fallback secret_store.
    """

    def __init__(self, brain_dir: Path | str):
        self.brain_dir = Path(brain_dir)
        self.settings_path = self.brain_dir / "settings" / "nas.json"
        self.password_path = self.brain_dir / "secrets" / "nas-smb-password.secret"

    def load(self) -> NasSettings:
        defaults = NasSettings()
        if not self.settings_path.is_file():
            return defaults
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return defaults
        if not isinstance(raw, dict):
            return defaults
        return NasSettings(
            root=str(raw.get("root") or ""),
            torrent_watch=str(raw.get("torrent_watch") or _DEFAULT_TORRENT_WATCH),
            music=str(raw.get("music") or ""),
            video=str(raw.get("video") or ""),
            smb_server=str(raw.get("smb_server") or r"\\Nas"),
            smb_username=str(raw.get("smb_username") or ""),
            smb_domain=str(raw.get("smb_domain") or ""),
        ).normalized()

    def save(self, config: NasSettings, *, password: str | None = None) -> NasSettings:
        value = config.normalized()
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(asdict(value), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self.settings_path)
        if password is not None and str(password):
            save_secret(self.password_path, str(password))
        return value

    def clear_password(self) -> None:
        self.password_path.unlink(missing_ok=True)

    def password(self) -> str:
        return load_secret(self.password_path)

    def password_saved(self) -> bool:
        return self.password_path.is_file() and bool(self.password())

    @staticmethod
    def _unc_server(path: str) -> str:
        value = str(path or "").strip()
        if not value.startswith("\\\\"):
            return ""
        parts = [part for part in value[2:].split("\\") if part]
        return "\\\\" + parts[0] if parts else ""

    def effective_server(self, config: NasSettings | None = None) -> str:
        value = config or self.load()
        return (
            self._unc_server(value.torrent_watch)
            or self._unc_server(value.root)
            or self._unc_server(value.music)
            or self._unc_server(value.video)
            or value.smb_server
        )

    def connect(self) -> dict[str, Any]:
        config = self.load()
        server = self.effective_server(config)
        if not server:
            return {"attempted": False, "connected": False, "error": "SMB server не указан"}
        if os.name != "nt":
            return {
                "attempted": False,
                "connected": False,
                "server": server,
                "error": "Автоматическая SMB-авторизация через Windows доступна только на Windows",
            }

        password = self.password()
        username = config.smb_username.strip()
        if not username:
            # Windows может использовать уже активную сессию/учётные данные профиля.
            return {"attempted": False, "connected": True, "server": server, "using_saved_credentials": False}

        if config.smb_domain.strip() and "\\" not in username and "@" not in username:
            username = f"{config.smb_domain.strip()}\\{username}"

        class NETRESOURCEW(ctypes.Structure):
            _fields_ = [
                ("dwScope", wintypes.DWORD),
                ("dwType", wintypes.DWORD),
                ("dwDisplayType", wintypes.DWORD),
                ("dwUsage", wintypes.DWORD),
                ("lpLocalName", wintypes.LPWSTR),
                ("lpRemoteName", wintypes.LPWSTR),
                ("lpComment", wintypes.LPWSTR),
                ("lpProvider", wintypes.LPWSTR),
            ]

        mpr = ctypes.WinDLL("mpr", use_last_error=True)
        add = mpr.WNetAddConnection2W
        add.argtypes = [ctypes.POINTER(NETRESOURCEW), wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        add.restype = wintypes.DWORD
        cancel = mpr.WNetCancelConnection2W
        cancel.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.BOOL]
        cancel.restype = wintypes.DWORD

        resource = NETRESOURCEW()
        resource.dwType = 1  # RESOURCETYPE_DISK
        resource.lpRemoteName = server
        code = int(add(ctypes.byref(resource), password or None, username, 0))
        if code == 1219:
            # Уже существует подключение к этому серверу с другими данными.
            cancel(server, False, True)
            code = int(add(ctypes.byref(resource), password or None, username, 0))
        if code not in {0, 85}:
            return {
                "attempted": True,
                "connected": False,
                "server": server,
                "using_saved_credentials": bool(password),
                "error": f"WNetAddConnection2W error {code}",
            }
        return {
            "attempted": True,
            "connected": True,
            "server": server,
            "using_saved_credentials": bool(password),
        }

    def public_status(self, *, probe: bool = False) -> dict[str, Any]:
        config = self.load()
        status: dict[str, Any] = {
            **asdict(config),
            "password_saved": self.password_saved(),
            "effective_server": self.effective_server(config),
        }
        if probe:
            status["connection"] = self.connect()
            paths = {
                "torrent_watch_exists": bool(config.torrent_watch and Path(config.torrent_watch).exists()),
                "root_exists": bool(config.root and Path(config.root).exists()),
                "music_exists": bool(config.music and Path(config.music).exists()),
                "video_exists": bool(config.video and Path(config.video).exists()),
            }
            status.update(paths)
        return status


__all__ = ["NasSettings", "NasSettingsStore"]
