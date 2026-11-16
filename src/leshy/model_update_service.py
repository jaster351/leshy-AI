"""Owner-controlled model release discovery and hash-verified staging.

A newer GGUF is NOT evidence of a better model. This service never mutates the
active model selection; promotion requires a separate, measured regression gate.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any
from urllib import request

from .diagnostics import event, problem
from .model_auto_update import StableModelUpdateManager, MAX_MODEL_BYTES


MODES = frozenset({"manual", "notify", "auto"})
DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60


class ModelUpdateService:
    def __init__(self, brain_dir: Path | str, models_dir: Path | str, catalog):
        self.brain_dir = Path(brain_dir)
        self.models_dir = Path(models_dir)
        self.catalog = catalog
        self.state_file = self.brain_dir / "settings" / "model-update-policy.json"
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.updater = StableModelUpdateManager(self.brain_dir, self.models_dir, catalog)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._loop: threading.Thread | None = None
        self._runtime: dict[str, Any] = {
            "state": "idle", "error": "", "last_check_epoch": 0,
            "available_model": "", "available_version": "",
            "downloaded": 0, "total": 0, "candidate": "",
        }

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, UnicodeError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        temp = self.state_file.with_name(self.state_file.name + ".tmp")
        try:
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self.state_file)
        finally:
            temp.unlink(missing_ok=True)

    def mode(self) -> str:
        mode = str(self._read().get("mode") or "auto")
        return mode if mode in MODES else "notify"

    def configure(self, mode: str) -> dict[str, Any]:
        if mode not in MODES:
            raise ValueError("Режим: manual, notify или auto")
        with self._lock:
            data = self._read()
            data["mode"] = mode
            self._write(data)
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            live = dict(self._runtime)
            busy = bool(self._worker and self._worker.is_alive())
            data = self._read()
        live.update({
            "mode": self.mode(),
            "busy": busy,
            "active_model": self.catalog.selected_name(),
            "candidate": str(data.get("candidate") or live["candidate"]),
            "candidate_sha256": str(data.get("candidate_sha256") or ""),
            "last_error": live.get("error") or "",
            "last_check_epoch": float(data.get("last_check_epoch") or live["last_check_epoch"]),
            "interval_seconds": DEFAULT_INTERVAL_SECONDS,
            "quality_gate": "required-not-yet-implemented",
            "automatic_activation": False,
            "policy": "New models are staged only; no model is promoted without measured quality and runtime checks.",
        })
        return live

    def request_check(self, *, download: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._worker and self._worker.is_alive():
                return self.status()
            self._runtime.update(state="queued", error="", downloaded=0)
            self._worker = threading.Thread(
                target=self._check, kwargs={"download": bool(download)},
                name="leshy-model-release-check", daemon=True,
            )
            self._worker.start()
        return self.status()

    def _set(self, **fields: Any) -> None:
        with self._lock:
            self._runtime.update(fields)

    @staticmethod
    def _safe_filename(value: str) -> str:
        filename = str(value or "")
        if (not filename or filename != Path(filename).name
                or filename in {".", ".."} or "\\" in filename or "/" in filename
                or not filename.lower().endswith(".gguf")
                or filename.lower().startswith("mmproj")):
            raise ValueError("Недопустимое имя GGUF-модели")
        return filename

    def _stage(self, manifest: dict[str, Any]) -> None:
        filename = self._safe_filename(manifest["filename"])
        target = self.models_dir / filename
        expected_size = int(manifest["size"])
        expected_hash = manifest["sha256"].lower()
        if target.is_symlink():
            raise ValueError("Ссылка вместо файла модели запрещена")
        # Existing files are never overwritten. A different digest is a
        # conflict, not permission to replace a working model.
        if target.exists():
            if not target.is_file() or target.stat().st_size != expected_size:
                raise FileExistsError("Модель с таким именем уже существует")
            digest = sha256()
            with target.open("rb") as source:
                for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected_hash:
                raise FileExistsError("Существующая модель имеет другой SHA-256")
        else:
            usage = shutil.disk_usage(self.models_dir)
            if usage.free < expected_size + 512 * 1024 * 1024:
                raise OSError("Недостаточно свободного места для модели и резерва")
            temporary = self.models_dir / (filename + ".part")
            if temporary.exists() or temporary.is_symlink():
                raise FileExistsError("Незавершённая загрузка уже существует")
            digest = sha256()
            received = 0
            self._set(state="downloading", downloaded=0, total=expected_size)
            try:
                req = request.Request(manifest["url"], headers={"User-Agent": "Leshy-AI/model-release-check"})
                with request.urlopen(req, timeout=90) as response, temporary.open("xb") as output:
                    if response.geturl().lower().startswith("http://"):
                        raise ValueError("Незащищённая переадресация GGUF")
                    for block in iter(lambda: response.read(8 * 1024 * 1024), b""):
                        received += len(block)
                        if received > expected_size or received > MAX_MODEL_BYTES:
                            raise ValueError("GGUF превышает ожидаемый размер")
                        output.write(block)
                        digest.update(block)
                        self._set(downloaded=received)
                    output.flush()
                    os.fsync(output.fileno())
                if received != expected_size or digest.hexdigest() != expected_hash:
                    raise ValueError("Размер или SHA-256 GGUF не совпадает с манифестом")
                valid, _ = self.catalog.inspect_gguf(temporary)
                if not valid:
                    raise ValueError("Скачанный файл не содержит GGUF")
                # Do not replace an existing file created concurrently.
                if target.exists() or target.is_symlink():
                    raise FileExistsError("Целевая модель появилась во время загрузки")
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        valid, _ = self.catalog.inspect_gguf(target)
        if not valid:
            raise ValueError("Модель не прошла проверку заголовка GGUF")
        with self._lock:
            data = self._read()
            data.update(candidate=filename, candidate_sha256=expected_hash,
                        candidate_version=manifest["version"], staged_at=time.time())
            self._write(data)
        self._set(state="pending_quality_check", candidate=filename)
        event("model_candidate_staged", model=filename, version=manifest["version"])

    def _check(self, *, download: bool) -> None:
        self._set(state="checking", error="", last_check_epoch=time.time())
        try:
            manifest = self.updater.fetch_manifest()
            self._safe_filename(manifest["filename"])
            self._set(available_model=manifest["filename"], available_version=manifest["version"],
                      total=manifest["size"])
            with self._lock:
                data = self._read()
                data["last_check_epoch"] = time.time()
                data["available_model"] = manifest["filename"]
                data["available_version"] = manifest["version"]
                self._write(data)
            if manifest["filename"] == self.catalog.selected_name():
                self._set(state="current")
                return
            if download and self.mode() == "auto":
                self._stage(manifest)
            else:
                self._set(state="available")
        except Exception as exc:
            self._set(state="error", error=f"{type(exc).__name__}: {exc}"[:500])
            problem("model_release_check_failed", type(exc).__name__)

    def start(self) -> None:
        with self._lock:
            if self._loop and self._loop.is_alive():
                return
            self._stop.clear()
            self._loop = threading.Thread(target=self._periodic, name="leshy-model-release-loop", daemon=True)
            self._loop.start()

    def _periodic(self) -> None:
        # Avoid competing with startup model warmup.
        if self._stop.wait(120):
            return
        while not self._stop.is_set():
            mode = self.mode()
            if mode != "manual":
                self.request_check(download=(mode == "auto"))
            if self._stop.wait(DEFAULT_INTERVAL_SECONDS):
                break

    def stop(self) -> None:
        self._stop.set()


__all__ = ["ModelUpdateService", "MODES"]
