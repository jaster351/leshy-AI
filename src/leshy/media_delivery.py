from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any
from urllib import request

from .diagnostics import event, problem
from .nas_settings import NasSettingsStore
from .research import SafeWebFetcher
from .storage import Storage, utcnow


# Проверяется право на сам материал, а не способ доставки. BitTorrent/torrent/magnet
# являются нейтральными технологиями и не блокируются как класс.
RIGHTS_ALLOWED = {"owned", "authorized", "public_domain", "open_license", "official_free", "purchased"}
AUDIO_EXTENSIONS = {".flac", ".alac", ".wav", ".aiff", ".aif", ".m4a", ".aac", ".opus", ".ogg", ".mp3"}
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".webm", ".mov", ".avi", ".ts", ".m2ts"}
TORRENT_EXTENSIONS = {".torrent"}


@dataclass(frozen=True, slots=True)
class NasPaths:
    root: Path | None
    torrent_watch: Path | None
    music: Path | None
    video: Path | None

    @classmethod
    def from_env(cls) -> "NasPaths":
        def env_path(name: str) -> Path | None:
            raw = os.getenv(name, "").strip()
            return Path(raw).expanduser() if raw else None

        root = env_path("LESHY_NAS_ROOT")
        torrent_watch = env_path("LESHY_NAS_TORRENT_WATCH")
        music = env_path("LESHY_NAS_MUSIC")
        video = env_path("LESHY_NAS_VIDEO")
        if root is not None:
            torrent_watch = torrent_watch or root / "torrents" / "watch"
            music = music or root / "music"
            video = video or root / "video"
        return cls(root=root, torrent_watch=torrent_watch, music=music, video=video)

    @classmethod
    def from_store(cls, brain_dir: Path | str) -> "NasPaths":
        config = NasSettingsStore(brain_dir).load()
        return cls(
            root=Path(config.root) if config.root else None,
            torrent_watch=Path(config.torrent_watch) if config.torrent_watch else None,
            music=Path(config.music) if config.music else None,
            video=Path(config.video) if config.video else None,
        )

    def configured(self) -> bool:
        return any(path is not None for path in (self.root, self.torrent_watch, self.music, self.video))


class MediaDeliveryManager:
    """Доставляет разрешённые медиафайлы и torrent-файлы в NAS.

    Torrent-файл не считается пиратским сам по себе. Для интернет-источника
    проверяется правовой статус конкретного материала. SMB-доступ восстанавливается
    из постоянных настроек NAS; пароль не попадает в журнал доставки.
    """

    MAX_REMOTE_BYTES = 25 * 1024 * 1024 * 1024

    def __init__(self, storage: Storage, paths: NasPaths | None = None, *, brain_dir: Path | str | None = None):
        self.storage = storage
        self.nas_store = NasSettingsStore(brain_dir) if brain_dir is not None else None
        self.paths = paths or (NasPaths.from_store(brain_dir) if brain_dir is not None else NasPaths.from_env())
        self._ensure_schema()

    def refresh_paths(self) -> NasPaths:
        if self.nas_store is not None:
            self.paths = NasPaths.from_store(self.nas_store.brain_dir)
        return self.paths

    def _ensure_nas_access(self) -> None:
        if self.nas_store is not None:
            result = self.nas_store.connect()
            if result.get("attempted") and not result.get("connected"):
                raise RuntimeError(str(result.get("error") or "Не удалось подключиться к NAS по SMB"))
        self.refresh_paths()

    def _ensure_schema(self) -> None:
        with self.storage.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS media_deliveries(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    media_kind TEXT NOT NULL,
                    rights_basis TEXT NOT NULL,
                    destination TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('queued','downloading','copied','failed','blocked')),
                    bytes_written INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_media_deliveries_owner ON media_deliveries(owner_id,id DESC);
                CREATE INDEX IF NOT EXISTS ix_media_deliveries_status ON media_deliveries(status,id);
                """
            )

    @staticmethod
    def media_kind(path_or_name: str) -> str:
        suffix = Path(path_or_name).suffix.lower()
        if suffix in TORRENT_EXTENSIONS:
            return "torrent"
        if suffix in AUDIO_EXTENSIONS:
            return "music"
        if suffix in VIDEO_EXTENSIONS:
            return "video"
        return "file"

    @staticmethod
    def audio_quality_score(*, codec: str = "", bitrate_kbps: int = 0, lossless: bool = False) -> int:
        codec = str(codec or "").lower()
        if lossless or codec in {"flac", "alac", "wav", "aiff", "ape", "wv"}:
            return 1000 + max(0, int(bitrate_kbps))
        codec_bonus = {"opus": 180, "aac": 160, "vorbis": 140, "mp3": 100}.get(codec, 0)
        return codec_bonus + max(0, int(bitrate_kbps))

    def _destination_dir(self, kind: str) -> Path:
        self._ensure_nas_access()
        selected = {
            "torrent": self.paths.torrent_watch,
            "music": self.paths.music,
            "video": self.paths.video,
            "file": self.paths.root,
        }.get(kind)
        if selected is None:
            raise RuntimeError(f"Папка NAS для типа {kind!r} не настроена")
        selected.mkdir(parents=True, exist_ok=True)
        return selected

    @staticmethod
    def _require_rights(rights_basis: str, *, source_type: str) -> str:
        rights = str(rights_basis or "unknown").strip().lower()
        if source_type == "local" and rights == "unknown":
            return "owned"
        if rights not in RIGHTS_ALLOWED:
            raise PermissionError(
                "Для интернет-загрузки нужен известный правовой статус материала: "
                "owned/authorized/public_domain/open_license/official_free/purchased. "
                "Это ограничение относится к материалу, а не к BitTorrent."
            )
        return rights

    def _insert(self, owner_id: str, source: str, source_type: str, kind: str, rights: str, metadata: dict[str, Any] | None) -> int:
        now = utcnow()
        with self.storage.connection() as conn:
            cur = conn.execute(
                "INSERT INTO media_deliveries(owner_id,source,source_type,media_kind,rights_basis,status,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    str(owner_id or "local")[:200], str(source)[:4000], source_type, kind, rights, "queued",
                    json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True), now, now,
                ),
            )
            return int(cur.lastrowid)

    def _finish(self, delivery_id: int, *, status: str, destination: str = "", bytes_written: int = 0, error: str = "") -> None:
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE media_deliveries SET status=?,destination=?,bytes_written=?,updated_at=?,last_error=? WHERE id=?",
                (status, str(destination)[:4000], max(0, int(bytes_written)), utcnow(), str(error)[:1000] or None, int(delivery_id)),
            )

    def deliver_local(
        self,
        owner_id: str,
        source_path: str | Path,
        *,
        rights_basis: str = "unknown",
        metadata: dict[str, Any] | None = None,
        destination_name: str = "",
    ) -> dict[str, Any]:
        source = Path(source_path).expanduser()
        if not source.is_file():
            raise FileNotFoundError(str(source))
        rights = self._require_rights(rights_basis, source_type="local")
        kind = self.media_kind(destination_name or source.name)
        delivery_id = self._insert(owner_id, str(source), "local", kind, rights, metadata)
        try:
            destination_dir = self._destination_dir(kind)
            name = Path(destination_name).name if destination_name else source.name
            destination = destination_dir / name
            temporary = destination.with_name(destination.name + ".leshy-part")
            shutil.copy2(source, temporary)
            if destination.exists():
                destination.unlink()
            temporary.replace(destination)
            size = destination.stat().st_size
            self._finish(delivery_id, status="copied", destination=str(destination), bytes_written=size)
            event("nas_media_delivered", delivery_id=delivery_id, kind=kind, destination=str(destination), bytes=size)
            return self.delivery(delivery_id)
        except Exception as exc:
            self._finish(delivery_id, status="failed", error=str(exc))
            problem("nas_media_delivery_failed", str(exc), delivery_id=delivery_id, kind=kind)
            raise

    def download_to_nas(
        self,
        owner_id: str,
        url: str,
        *,
        destination_name: str,
        rights_basis: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = str(url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("Поддерживаются только HTTP(S) URL")
        rights = self._require_rights(rights_basis, source_type="remote")
        safe_name = Path(destination_name).name
        if not safe_name:
            raise ValueError("Не указано имя файла")
        kind = self.media_kind(safe_name)
        delivery_id = self._insert(owner_id, url, "remote", kind, rights, metadata)
        destination_dir = self._destination_dir(kind)
        destination = destination_dir / safe_name
        try:
            SafeWebFetcher._validate_public_url(url)
            self._finish(delivery_id, status="downloading")
            req = request.Request(url, headers={"User-Agent": "LeshyMediaDelivery/1.0"})
            with request.urlopen(req, timeout=60) as response:
                SafeWebFetcher._validate_public_url(response.geturl())
                declared = int(response.headers.get("Content-Length") or 0)
                if declared > self.MAX_REMOTE_BYTES:
                    raise ValueError("Удалённый файл превышает лимит безопасной загрузки")
                with tempfile.NamedTemporaryFile(prefix="leshy-download-", suffix=".part", delete=False, dir=destination_dir) as out:
                    temp_path = Path(out.name)
                    total = 0
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self.MAX_REMOTE_BYTES:
                            raise ValueError("Удалённый файл превысил лимит безопасной загрузки")
                        out.write(chunk)
            if destination.exists():
                destination.unlink()
            temp_path.replace(destination)
            self._finish(delivery_id, status="copied", destination=str(destination), bytes_written=destination.stat().st_size)
            event("nas_media_downloaded", delivery_id=delivery_id, kind=kind, destination=str(destination))
            return self.delivery(delivery_id)
        except Exception as exc:
            try:
                if "temp_path" in locals() and temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass
            self._finish(delivery_id, status="failed", error=str(exc))
            problem("nas_media_download_failed", str(exc), delivery_id=delivery_id, kind=kind)
            raise

    def delivery(self, delivery_id: int) -> dict[str, Any]:
        with self.storage.connection() as conn:
            row = conn.execute("SELECT * FROM media_deliveries WHERE id=?", (int(delivery_id),)).fetchone()
        if row is None:
            raise KeyError("Операция доставки не найдена")
        return dict(row)

    def status(self) -> dict[str, Any]:
        self.refresh_paths()
        return {
            "configured": self.paths.configured(),
            "root": str(self.paths.root) if self.paths.root else "",
            "torrent_watch": str(self.paths.torrent_watch) if self.paths.torrent_watch else "",
            "music": str(self.paths.music) if self.paths.music else "",
            "video": str(self.paths.video) if self.paths.video else "",
            "allowed_rights": sorted(RIGHTS_ALLOWED),
            "torrent_protocol_allowed": True,
        }


__all__ = ["MediaDeliveryManager", "NasPaths", "RIGHTS_ALLOWED"]
