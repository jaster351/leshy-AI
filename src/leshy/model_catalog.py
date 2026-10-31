from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import BinaryIO, Any

from .diagnostics import event
from .model_download import ModelDownloader


DEFAULT_CHAT_MODEL = "Qwen3-8B-Q4_K_M.gguf"
MAX_MODEL_UPLOAD_BYTES = 128 * 1024 * 1024 * 1024


class LocalModelVerifier:
    """Проверка пользовательской GGUF-модели без привязки к конкретному SHA-256."""

    def __init__(self, target: Path):
        self.target = Path(target)

    def status(self) -> dict[str, Any]:
        exists = self.target.is_file()
        valid = False
        error = ""
        version = None
        size = 0
        if exists:
            try:
                size = self.target.stat().st_size
                valid, version = LocalModelCatalog.inspect_gguf(self.target)
                if not valid:
                    error = "Файл не содержит сигнатуру GGUF"
            except Exception as exc:
                error = str(exc)[:1000]
        return {
            "state": "ready" if exists and valid else ("invalid" if exists else "missing"),
            "downloaded": size,
            "total": size,
            "error": error,
            "verified": bool(valid),
            "path": str(self.target),
            "exists": exists,
            "size": size,
            "gguf_version": version,
            "verification": "gguf-header",
        }

    def ensure_verified(self) -> Path:
        status = self.status()
        if status.get("exists") and status.get("verified"):
            return self.target
        raise RuntimeError(str(status.get("error") or "Локальная GGUF-модель не готова"))

    def start(self, *, force: bool = False) -> dict[str, Any]:
        # Пользовательские модели не скачиваются автоматически: файл уже должен
        # находиться в LeshyBrain/models или быть загружен через Web UI.
        return self.status()


class LocalModelCatalog:
    """Каталог локальных GGUF-моделей с постоянным выбором активной модели."""

    def __init__(self, brain_dir: Path, models_dir: Path, *, default_name: str = DEFAULT_CHAT_MODEL):
        self.brain_dir = Path(brain_dir)
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.settings_dir = self.brain_dir / "settings"
        self.settings_dir.mkdir(parents=True, exist_ok=True)
        self.selection_path = self.settings_dir / "model.json"
        self.default_name = Path(default_name).name or DEFAULT_CHAT_MODEL
        self._lock = threading.RLock()

    @staticmethod
    def inspect_gguf(path: Path) -> tuple[bool, int | None]:
        try:
            with Path(path).open("rb") as handle:
                header = handle.read(8)
            if len(header) < 8 or header[:4] != b"GGUF":
                return False, None
            return True, int.from_bytes(header[4:8], "little", signed=False)
        except OSError:
            return False, None

    def _safe_path(self, name: str) -> Path:
        candidate_name = Path(str(name or "")).name
        if not candidate_name.lower().endswith(".gguf") or candidate_name in {".gguf", ""}:
            raise ValueError("Можно выбрать только файл модели .gguf")
        path = (self.models_dir / candidate_name).resolve()
        root = self.models_dir.resolve()
        if path.parent != root:
            raise ValueError("Модель должна находиться непосредственно в LeshyBrain/models")
        return path

    def selected_name(self) -> str:
        try:
            payload = json.loads(self.selection_path.read_text(encoding="utf-8"))
            name = Path(str(payload.get("active_model") or "")).name
            path = self._safe_path(name)
            valid, _ = self.inspect_gguf(path)
            if path.is_file() and valid:
                return path.name
        except Exception:
            pass
        default = self.models_dir / self.default_name
        if default.is_file():
            return default.name
        models = self.list_models()
        return str(models[0]["name"]) if models else self.default_name

    def active_path(self) -> Path:
        return self.models_dir / self.selected_name()

    def list_models(self) -> list[dict[str, Any]]:
        active = self.selected_name_from_file()
        rows: list[dict[str, Any]] = []
        for path in sorted(self.models_dir.glob("*.gguf"), key=lambda p: p.name.lower()):
            if not path.is_file():
                continue
            valid, version = self.inspect_gguf(path)
            try:
                stat = path.stat()
                size = int(stat.st_size)
                modified = float(stat.st_mtime)
            except OSError:
                size = 0
                modified = 0.0
            rows.append({
                "name": path.name,
                "size": size,
                "modified_epoch": modified,
                "valid_gguf": bool(valid),
                "gguf_version": version,
                "active": path.name == active,
            })
        return rows

    def selected_name_from_file(self) -> str:
        try:
            payload = json.loads(self.selection_path.read_text(encoding="utf-8"))
            return Path(str(payload.get("active_model") or "")).name
        except Exception:
            return ""

    def select(self, name: str) -> Path:
        path = self._safe_path(name)
        if not path.is_file():
            raise FileNotFoundError(f"Модель не найдена: {path.name}")
        valid, version = self.inspect_gguf(path)
        if not valid:
            raise ValueError("Выбранный файл не является GGUF-моделью")
        payload = {
            "schema": 1,
            "active_model": path.name,
            "gguf_version": version,
            "updated_at": time.time(),
        }
        temporary = self.selection_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.selection_path)
        event("local_model_selected", model=path.name, size=path.stat().st_size, gguf_version=version)
        return path

    def status(self, active_path: Path | None = None) -> dict[str, Any]:
        active = Path(active_path).name if active_path is not None else self.selected_name()
        items = self.list_models()
        for item in items:
            item["active"] = item["name"] == active
        return {
            "models_dir": str(self.models_dir),
            "active_model": active,
            "count": len(items),
            "items": items,
            "supports_upload": True,
            "format": "GGUF",
        }

    def save_upload(self, stream: BinaryIO, *, length: int, filename: str) -> dict[str, Any]:
        if length <= 0:
            raise ValueError("Пустой файл модели")
        if length > MAX_MODEL_UPLOAD_BYTES:
            raise ValueError("Файл модели превышает лимит 128 ГБ")
        target = self._safe_path(filename)
        if target.exists():
            raise FileExistsError(f"Файл {target.name} уже существует")
        temporary = target.with_name(target.name + ".upload")
        written = 0
        try:
            with temporary.open("wb") as handle:
                remaining = int(length)
                while remaining > 0:
                    chunk = stream.read(min(8 * 1024 * 1024, remaining))
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
                    remaining -= len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if written != int(length):
                raise ValueError(f"Загрузка оборвалась: получено {written} из {length} байт")
            valid, version = self.inspect_gguf(temporary)
            if not valid:
                raise ValueError("Загруженный файл не является GGUF-моделью")
            os.replace(temporary, target)
            event("local_model_uploaded", model=target.name, size=written, gguf_version=version)
            return {
                "name": target.name,
                "size": written,
                "valid_gguf": True,
                "gguf_version": version,
            }
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def apply_saved_model_to_settings(settings) -> LocalModelCatalog:
    catalog = LocalModelCatalog(settings.brain_dir, settings.native_models_dir, default_name=settings.native_chat_file)
    selected = catalog.selected_name()
    settings.native_chat_file = selected
    return catalog


def configure_app_model_verifier(app, catalog: LocalModelCatalog) -> None:
    active = Path(app.model_path)
    if active.name == catalog.default_name:
        app.model_downloader = ModelDownloader(active)
    else:
        app.model_downloader = LocalModelVerifier(active)


def switch_app_model(app, catalog: LocalModelCatalog, name: str) -> dict[str, Any]:
    """Переключить активную GGUF-модель без пересборки приложения."""
    target = catalog.select(name)
    old_llm = app.llm
    old_model = Path(app.model_path).name
    closer = getattr(old_llm, "close", None)
    if callable(closer):
        closer()
    app.settings.native_chat_file = target.name
    app.model_path = target
    configure_app_model_verifier(app, catalog)
    app.llm = app._build_llm()
    event("local_model_switched", previous_model=old_model, model=target.name)
    return catalog.status(active_path=target)


__all__ = [
    "DEFAULT_CHAT_MODEL",
    "LocalModelCatalog",
    "LocalModelVerifier",
    "apply_saved_model_to_settings",
    "configure_app_model_verifier",
    "switch_app_model",
]
