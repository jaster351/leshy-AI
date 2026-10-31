from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import zipfile
from typing import Any, Callable

from .accelerator_runtime import LlamaCppRuntimeManager
from .diagnostics import event, problem


@dataclass(slots=True)
class RuntimePolicy:
    mode: str = "auto"  # auto | modern | legacy

    def normalized(self) -> "RuntimePolicy":
        mode = str(self.mode or "auto").strip().lower()
        if mode not in {"auto", "modern", "legacy"}:
            mode = "auto"
        return RuntimePolicy(mode=mode)


class RuntimePolicyStore:
    def __init__(self, brain_dir: Path | str):
        self.path = Path(brain_dir) / "settings" / "runtime-policy.json"

    def load(self) -> RuntimePolicy:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return RuntimePolicy(mode=str(raw.get("mode") or "auto")).normalized()
        except Exception:
            return RuntimePolicy()

    def save(self, policy: RuntimePolicy) -> RuntimePolicy:
        value = policy.normalized()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(asdict(value), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
        return value


class CompatibleRuntimeManager(LlamaCppRuntimeManager):
    """Llama.cpp runtime с автообновлением для новых GGUF и сохранением legacy.

    auto   — новые архитектуры получают свежий runtime, старые используют текущий;
    modern — всегда проверяется и ставится свежий официальный llama.cpp;
    legacy — используется сохранённый предыдущий runtime, если он есть.
    """

    MODERN_MODEL_MARKERS = (
        "qwen3.5", "qwen35", "qwen3.8", "qwen38", "llama-4", "llama4", "gemma-3", "gemma3",
    )

    def __init__(self, brain_dir: Path | str, model_name_provider: Callable[[], str] | None = None):
        super().__init__(Path(brain_dir))
        self.policy_store = RuntimePolicyStore(brain_dir)
        self.model_name_provider = model_name_provider or (lambda: "")
        self.legacy_root = self.root / "legacy"
        self.legacy_root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def requires_modern_runtime(cls, model_name: str) -> bool:
        value = str(model_name or "").lower()
        return any(marker in value for marker in cls.MODERN_MODEL_MARKERS)

    @staticmethod
    def _manifest(folder: Path) -> dict[str, Any]:
        try:
            value = json.loads((folder / "leshy-runtime.json").read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def legacy_backend_dir(self, backend: str) -> Path:
        return self.legacy_root / backend

    def legacy_server_path(self, backend: str) -> Path | None:
        folder = self.legacy_backend_dir(backend)
        if not folder.exists():
            return None
        matches = list(folder.rglob("llama-server.exe" if os.name == "nt" else "llama-server"))
        return matches[0] if matches else None

    def _preserve_current_as_legacy(self, backend: str) -> None:
        current = self.backend_dir(backend)
        if not current.exists() or not self.server_path(backend):
            return
        destination = self.legacy_backend_dir(backend)
        staged = destination.with_name(destination.name + ".staging")
        shutil.rmtree(staged, ignore_errors=True)
        shutil.copytree(current, staged)
        shutil.rmtree(destination, ignore_errors=True)
        os.replace(staged, destination)
        event(
            "accelerator_runtime_legacy_preserved",
            backend=backend,
            tag=str(self._manifest(destination).get("tag") or "unknown"),
        )

    def ensure_latest(self, backend: str, progress=None) -> Path:
        asset = self.discover(backend)
        destination = self.backend_dir(backend)
        current_manifest = self._manifest(destination)
        current = self.server_path(backend)
        if current and str(current_manifest.get("tag") or "") == asset.tag:
            return current

        with tempfile.TemporaryDirectory(prefix="leshy-runtime-modern-") as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / asset.name
            self._download(asset, archive, progress)
            unpacked = tmp_path / "unpacked"
            unpacked.mkdir()
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(unpacked)
            servers = list(unpacked.rglob("llama-server.exe" if os.name == "nt" else "llama-server"))
            if not servers:
                raise RuntimeError(f"В {asset.name} отсутствует llama-server")
            staged = destination.with_name(destination.name + ".modern-staging")
            shutil.rmtree(staged, ignore_errors=True)
            shutil.copytree(unpacked, staged)
            (staged / "leshy-runtime.json").write_text(
                json.dumps({
                    "schema": 1,
                    "backend": backend,
                    "tag": asset.tag,
                    "asset": asset.name,
                    "source": asset.url,
                    "sha256": asset.digest.split(":", 1)[1],
                    "project": "ggml-org/llama.cpp",
                    "installed_at": time.time(),
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if current:
                self._preserve_current_as_legacy(backend)
            shutil.rmtree(destination, ignore_errors=True)
            os.replace(staged, destination)

        result = self.server_path(backend)
        if not result:
            raise RuntimeError(f"Свежий llama-server не найден после установки backend {backend}")
        event("accelerator_runtime_modern_ready", backend=backend, tag=asset.tag, server=result)
        return result

    def ensure(self, backend: str, progress=None) -> Path:
        policy = self.policy_store.load()
        model_name = str(self.model_name_provider() or "")
        if policy.mode == "legacy":
            legacy = self.legacy_server_path(backend)
            if legacy:
                return legacy
            return super().ensure(backend, progress)

        needs_modern = policy.mode == "modern" or (policy.mode == "auto" and self.requires_modern_runtime(model_name))
        if needs_modern:
            try:
                return self.ensure_latest(backend, progress)
            except Exception as exc:
                problem("accelerator_runtime_modern_update_failed", str(exc), backend=backend, model=model_name)
                current = self.server_path(backend)
                if current:
                    return current
                raise
        return super().ensure(backend, progress)

    def status(self) -> dict[str, Any]:
        policy = self.policy_store.load()
        backends: dict[str, Any] = {}
        for backend in ("vulkan", "openvino", "cpu"):
            current_dir = self.backend_dir(backend)
            legacy_dir = self.legacy_backend_dir(backend)
            backends[backend] = {
                "current": bool(self.server_path(backend)),
                "current_tag": str(self._manifest(current_dir).get("tag") or ""),
                "legacy": bool(self.legacy_server_path(backend)),
                "legacy_tag": str(self._manifest(legacy_dir).get("tag") or ""),
            }
        model_name = str(self.model_name_provider() or "")
        return {
            "mode": policy.mode,
            "model": model_name,
            "model_requires_modern": self.requires_modern_runtime(model_name),
            "backends": backends,
            "modes": {
                "auto": "Авто: свежий runtime для новых архитектур, совместимый режим для старых моделей.",
                "modern": "Современный: всегда использовать/обновлять актуальный официальный llama.cpp.",
                "legacy": "Legacy: предпочитать сохранённую предыдущую версию llama.cpp для старых GGUF.",
            },
        }


__all__ = ["CompatibleRuntimeManager", "RuntimePolicy", "RuntimePolicyStore"]
