from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import threading
import time
from typing import Any
from urllib import request

from .diagnostics import event, problem


DEFAULT_MANIFEST_URL = "https://raw.githubusercontent.com/jaster351/leshy-AI/main/models/stable.json"
MAX_MODEL_BYTES = 128 * 1024 * 1024 * 1024


class StableModelUpdateManager:
    """Безопасное автообновление рекомендованной локальной GGUF-модели.

    Новая модель сначала загружается во временный файл, затем проверяется по
    точному размеру и SHA-256. Только после этого selection-файл переключается
    на новую модель. Текущий процесс продолжает использовать старую модель до
    следующего запуска. При неудачном старте внешний bootstrap может вызвать
    rollback_pending().
    """

    def __init__(self, brain_dir: Path | str, models_dir: Path | str, catalog):
        self.brain_dir = Path(brain_dir)
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.catalog = catalog
        self.settings_dir = self.brain_dir / "settings"
        self.settings_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.settings_dir / "model-auto-update.json"
        self.manifest_url = os.getenv("LESHY_MODEL_MANIFEST_URL", DEFAULT_MANIFEST_URL).strip() or DEFAULT_MANIFEST_URL
        self.interval_seconds = max(900, int(os.getenv("LESHY_MODEL_UPDATE_SECONDS", "21600") or 21600))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._runtime = {
            "state": "idle",
            "last_error": "",
            "downloaded": 0,
            "total": 0,
            "manifest_version": "",
            "available_model": "",
            "last_check_epoch": 0.0,
        }

    def _read_state(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _write_state(self, payload: dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.state_path)

    @staticmethod
    def _validate_manifest(raw: dict[str, Any]) -> dict[str, Any]:
        if int(raw.get("schema") or 0) != 1:
            raise ValueError("Неподдерживаемая схема manifest модели")
        filename = Path(str(raw.get("filename") or "")).name
        url = str(raw.get("url") or "").strip()
        digest = str(raw.get("sha256") or "").strip().lower()
        size = int(raw.get("size") or 0)
        version = str(raw.get("version") or "").strip()
        model_id = str(raw.get("model_id") or filename).strip()
        if not filename.lower().endswith(".gguf"):
            raise ValueError("Manifest должен указывать GGUF-файл")
        if not url.startswith("https://"):
            raise ValueError("URL модели должен использовать HTTPS")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("Некорректный SHA-256 в manifest")
        if size <= 0 or size > MAX_MODEL_BYTES:
            raise ValueError("Некорректный размер модели в manifest")
        if not version:
            raise ValueError("В manifest не указана версия")
        return {
            "filename": filename,
            "url": url,
            "sha256": digest,
            "size": size,
            "version": version,
            "model_id": model_id,
            "notes": str(raw.get("notes") or "")[:2000],
        }

    def fetch_manifest(self) -> dict[str, Any]:
        req = request.Request(self.manifest_url, headers={"User-Agent": "Leshy-AI/model-updater"})
        with request.urlopen(req, timeout=20) as response:
            data = response.read(256_000)
        raw = json.loads(data.decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Manifest модели должен быть JSON-объектом")
        return self._validate_manifest(raw)

    @staticmethod
    def _version_key(value: str) -> tuple:
        parts: list[Any] = []
        for chunk in str(value).replace("-", ".").split("."):
            try:
                parts.append(int(chunk))
            except ValueError:
                parts.append(chunk.lower())
        return tuple(parts)

    def _is_newer(self, manifest: dict[str, Any], state: dict[str, Any]) -> bool:
        installed_version = str(state.get("installed_version") or "")
        installed_id = str(state.get("installed_model_id") or "")
        target = self.models_dir / str(manifest["filename"])
        if not target.is_file():
            return True
        if not installed_version:
            # Файл уже есть, но state ещё не создавался: считаем текущий manifest
            # установленным, чтобы не качать 5 ГБ повторно.
            return False
        if installed_id and installed_id != manifest["model_id"]:
            return True
        return self._version_key(str(manifest["version"])) > self._version_key(installed_version)

    def _set_runtime(self, **values: Any) -> None:
        with self._lock:
            self._runtime.update(values)

    def check(self, *, install: bool = True) -> dict[str, Any]:
        self._set_runtime(state="checking", last_error="", last_check_epoch=time.time())
        manifest = self.fetch_manifest()
        state = self._read_state()
        self._set_runtime(
            manifest_version=manifest["version"],
            available_model=manifest["filename"],
            total=manifest["size"],
        )
        if not state.get("installed_version") and (self.models_dir / manifest["filename"]).is_file():
            state.update(
                installed_version=manifest["version"],
                installed_model_id=manifest["model_id"],
                installed_filename=manifest["filename"],
                initialized_at=time.time(),
            )
            self._write_state(state)
        if not self._is_newer(manifest, state):
            self._set_runtime(state="current", downloaded=0)
            return self.status()
        self._set_runtime(state="available")
        if install:
            self.install(manifest)
        return self.status()

    def install(self, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
        manifest = manifest or self.fetch_manifest()
        target = self.models_dir / manifest["filename"]
        temporary = target.with_name(target.name + ".download")
        previous = self.catalog.selected_name()
        hasher = sha256()
        downloaded = 0
        self._set_runtime(state="downloading", downloaded=0, total=manifest["size"], last_error="")
        try:
            req = request.Request(manifest["url"], headers={"User-Agent": "Leshy-AI/model-updater"})
            with request.urlopen(req, timeout=60) as response, temporary.open("wb") as output:
                while True:
                    chunk = response.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > manifest["size"] or downloaded > MAX_MODEL_BYTES:
                        raise RuntimeError("Размер модели превысил ожидаемый")
                    output.write(chunk)
                    hasher.update(chunk)
                    self._set_runtime(downloaded=downloaded)
                output.flush()
                os.fsync(output.fileno())
            if downloaded != manifest["size"]:
                raise RuntimeError(f"Получено {downloaded} байт, ожидается {manifest['size']}")
            digest = hasher.hexdigest().lower()
            if digest != manifest["sha256"]:
                raise RuntimeError(f"SHA-256 не совпал: {digest}")
            valid, gguf_version = self.catalog.inspect_gguf(temporary)
            if not valid:
                raise RuntimeError("Загруженный файл не является GGUF")
            os.replace(temporary, target)
            self.catalog.select(target.name)
            state = self._read_state()
            state.update(
                installed_version=manifest["version"],
                installed_model_id=manifest["model_id"],
                installed_filename=manifest["filename"],
                installed_sha256=manifest["sha256"],
                previous_model=previous,
                pending_validation=True,
                pending_since=time.time(),
                gguf_version=gguf_version,
            )
            self._write_state(state)
            self._set_runtime(state="pending_restart", downloaded=downloaded)
            event(
                "model_auto_update_staged",
                previous_model=previous,
                model=target.name,
                version=manifest["version"],
            )
            return self.status()
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            self._set_runtime(state="error", last_error=str(exc)[:1000])
            problem("model_auto_update_failed", str(exc))
            raise

    def mark_startup_valid(self, active_model: str) -> None:
        state = self._read_state()
        if not state.get("pending_validation"):
            return
        if Path(str(state.get("installed_filename") or "")).name != Path(active_model).name:
            return
        state["pending_validation"] = False
        state["validated_at"] = time.time()
        state["previous_model"] = ""
        self._write_state(state)
        event("model_auto_update_validated", model=Path(active_model).name)

    def rollback_pending(self) -> str:
        state = self._read_state()
        previous = Path(str(state.get("previous_model") or "")).name
        if not state.get("pending_validation") or not previous:
            return ""
        target = self.models_dir / previous
        if not target.is_file():
            problem("model_auto_update_rollback_failed", "Предыдущая модель отсутствует", previous_model=previous)
            return ""
        self.catalog.select(previous)
        state["pending_validation"] = False
        state["rolled_back_at"] = time.time()
        state["rollback_from"] = str(state.get("installed_filename") or "")
        state["installed_filename"] = previous
        state["installed_version"] = "rollback"
        state["previous_model"] = ""
        self._write_state(state)
        event("model_auto_update_rolled_back", model=previous)
        return previous

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="leshy-model-auto-update", daemon=True)
        self._thread.start()
        event("model_auto_update_worker_started", interval_seconds=self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # Не конкурируем с прогревом модели и стартовым восстановлением Telegram.
        if self._stop.wait(120.0):
            return
        while not self._stop.is_set():
            try:
                self.check(install=True)
            except Exception:
                pass
            if self._stop.wait(float(self.interval_seconds)):
                return

    def status(self) -> dict[str, Any]:
        with self._lock:
            runtime = dict(self._runtime)
        state = self._read_state()
        runtime.update(
            manifest_url=self.manifest_url,
            interval_seconds=self.interval_seconds,
            auto_update=True,
            installed_version=str(state.get("installed_version") or ""),
            installed_filename=str(state.get("installed_filename") or ""),
            pending_validation=bool(state.get("pending_validation")),
            previous_model=str(state.get("previous_model") or ""),
        )
        return runtime


__all__ = ["StableModelUpdateManager", "DEFAULT_MANIFEST_URL"]
