from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
import time
from urllib import request


OFFICIAL_QWEN3_8B_Q4_URL = "https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf"
OFFICIAL_QWEN3_8B_Q4_SHA256 = "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785"
OFFICIAL_QWEN3_8B_Q4_SIZE = 5_027_783_488


class ModelDownloader:
    def __init__(self, target: Path):
        self.target = Path(target)
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._verified_path = self.target.with_suffix(self.target.suffix + ".verified.json")
        exists = self.target.is_file()
        self._status = {
            "state": "ready" if exists else "missing",
            "downloaded": self.target.stat().st_size if exists else 0,
            "total": OFFICIAL_QWEN3_8B_Q4_SIZE,
            "error": "",
            "verified": self._cached_verification_matches() if exists else False,
        }

    def status(self) -> dict:
        with self._lock:
            result = dict(self._status)
        result["path"] = str(self.target)
        result["exists"] = self.target.is_file()
        if result["exists"]:
            try:
                result["size"] = self.target.stat().st_size
            except OSError:
                result["size"] = 0
        return result

    def _cached_verification_matches(self) -> bool:
        if not self.target.is_file() or not self._verified_path.is_file():
            return False
        try:
            stat = self.target.stat()
            data = json.loads(self._verified_path.read_text(encoding="utf-8"))
            return (
                int(data.get("size", -1)) == int(stat.st_size)
                and int(data.get("mtime_ns", -1)) == int(stat.st_mtime_ns)
                and str(data.get("sha256", "")).lower() == OFFICIAL_QWEN3_8B_Q4_SHA256
            )
        except Exception:
            return False

    def _write_verified_cache(self, digest: str) -> None:
        stat = self.target.stat()
        payload = {
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "sha256": digest.lower(),
            "verified_at": time.time(),
        }
        self._verified_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _sha256(self) -> str:
        digest = hashlib.sha256()
        processed = 0
        total = self.target.stat().st_size
        self._set(state="verifying", downloaded=processed, total=total, error="", verified=False)
        with self.target.open("rb") as handle:
            while True:
                block = handle.read(8 * 1024 * 1024)
                if not block:
                    break
                digest.update(block)
                processed += len(block)
                self._set(downloaded=processed)
        return digest.hexdigest().lower()

    def verify_existing(self, *, repair: bool = False) -> bool:
        """Проверяет локальный GGUF по точному размеру и SHA-256.

        При repair=True повреждённый файл не удаляется безвозвратно: он
        переименовывается в *.corrupt-<timestamp>, после чего запускается
        повторная загрузка официальной модели.
        """
        if not self.target.is_file():
            self._set(state="missing", downloaded=0, total=OFFICIAL_QWEN3_8B_Q4_SIZE, error="", verified=False)
            if repair:
                self.start(force=True)
            return False

        try:
            size = self.target.stat().st_size
        except OSError as exc:
            self._set(state="error", error=str(exc)[:1000], verified=False)
            return False

        if size != OFFICIAL_QWEN3_8B_Q4_SIZE:
            error = f"Неверный размер модели: {size}, ожидается {OFFICIAL_QWEN3_8B_Q4_SIZE}"
            self._set(state="invalid", downloaded=size, total=OFFICIAL_QWEN3_8B_Q4_SIZE, error=error, verified=False)
            if repair:
                self._quarantine_and_redownload()
            return False

        if self._cached_verification_matches():
            self._set(state="ready", downloaded=size, total=size, error="", verified=True)
            return True

        try:
            digest = self._sha256()
        except Exception as exc:
            self._set(state="error", error=str(exc)[:1000], verified=False)
            return False

        if digest != OFFICIAL_QWEN3_8B_Q4_SHA256:
            error = f"SHA-256 модели не совпал: {digest}"
            self._set(state="invalid", downloaded=size, total=size, error=error, verified=False)
            if repair:
                self._quarantine_and_redownload()
            return False

        self._write_verified_cache(digest)
        self._set(state="ready", downloaded=size, total=size, error="", verified=True)
        return True

    def ensure_verified(self) -> Path:
        if self.verify_existing(repair=True):
            return self.target
        status = self.status()
        if status.get("state") == "downloading":
            raise RuntimeError("Локальная модель повреждена или отсутствует; повторная загрузка уже запущена")
        raise RuntimeError(str(status.get("error") or "Локальная модель не готова"))

    def _quarantine_and_redownload(self) -> None:
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            quarantine = self.target.with_name(self.target.name + f".corrupt-{stamp}")
            self.target.replace(quarantine)
        except OSError:
            try:
                self.target.unlink(missing_ok=True)
            except OSError:
                pass
        self._verified_path.unlink(missing_ok=True)
        self.start(force=True)

    def start(self, *, force: bool = False) -> dict:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            if self.target.is_file() and not force:
                # Не доверяем одному факту существования файла. Точная проверка
                # выполняется до первого inference через ensure_verified().
                size = self.target.stat().st_size
                self._status.update(
                    state="ready",
                    downloaded=size,
                    total=OFFICIAL_QWEN3_8B_Q4_SIZE,
                    error="",
                    verified=self._cached_verification_matches(),
                )
                return self.status()
            self._status = {
                "state": "downloading",
                "downloaded": 0,
                "total": OFFICIAL_QWEN3_8B_Q4_SIZE,
                "error": "",
                "verified": False,
            }
            self._thread = threading.Thread(target=self._run, name="leshy-model-download", daemon=True)
            self._thread.start()
            return self.status()

    def _set(self, **values) -> None:
        with self._lock:
            self._status.update(values)

    def _run(self) -> None:
        self.target.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.target.with_suffix(self.target.suffix + ".download")
        hasher = hashlib.sha256()
        downloaded = 0
        try:
            req = request.Request(OFFICIAL_QWEN3_8B_Q4_URL, headers={"User-Agent": "Leshy-AI/1.10"})
            with request.urlopen(req, timeout=60) as response, temporary.open("wb") as out:
                total = int(response.headers.get("Content-Length") or OFFICIAL_QWEN3_8B_Q4_SIZE)
                self._set(total=total)
                while True:
                    chunk = response.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
                    self._set(downloaded=downloaded)
                out.flush()
            digest = hasher.hexdigest().lower()
            if downloaded != OFFICIAL_QWEN3_8B_Q4_SIZE:
                raise RuntimeError(
                    f"Размер загруженной модели не совпал: {downloaded}, ожидается {OFFICIAL_QWEN3_8B_Q4_SIZE}"
                )
            if digest != OFFICIAL_QWEN3_8B_Q4_SHA256:
                raise RuntimeError(f"SHA-256 модели не совпал: {digest}")
            temporary.replace(self.target)
            self._write_verified_cache(digest)
            self._set(
                state="ready",
                downloaded=downloaded,
                total=downloaded,
                error="",
                verified=True,
            )
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            self._set(state="error", error=str(exc)[:1000], verified=False)
