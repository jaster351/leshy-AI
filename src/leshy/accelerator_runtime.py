from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from typing import Any
from urllib import error, request
import zipfile

from .diagnostics import event, exception
from .llm import ChatMessage, LLMUnavailable
from .logging_paths import central_log_path

_GITHUB_RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=15"
_RUNTIME_LOCK = threading.RLock()
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass(frozen=True)
class RuntimeAsset:
    backend: str
    tag: str
    name: str
    url: str
    digest: str


class LlamaCppRuntimeManager:
    """Управляет официальными локальными llama.cpp runtime внутри LeshyBrain."""

    def __init__(self, brain_dir: Path):
        self.brain_dir = Path(brain_dir)
        self.root = self.brain_dir / "runtime" / "llama.cpp"
        self.root.mkdir(parents=True, exist_ok=True)

    def server_log_path(self, backend: str) -> Path:
        return central_log_path(self.brain_dir, f"{backend}-server.log")

    def _asset_match(self, backend: str, name: str) -> bool:
        lowered = name.lower()
        if os.name != "nt":
            return False
        if backend == "vulkan":
            # Универсальный GPU runtime: AMD Radeon, NVIDIA и Intel с Vulkan-драйвером.
            return lowered.endswith(".zip") and "bin-win-vulkan-x64" in lowered
        if backend == "openvino":
            return lowered.endswith(".zip") and "bin-win-openvino" in lowered and "x64" in lowered
        if backend == "cpu":
            return lowered.endswith(".zip") and "bin-win-cpu-x64" in lowered
        return False

    def discover(self, backend: str) -> RuntimeAsset:
        req = request.Request(
            _GITHUB_RELEASES,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Leshy-Accelerator-Runtime/1.10"},
        )
        with request.urlopen(req, timeout=30) as response:
            releases = json.loads(response.read().decode("utf-8"))
        if not isinstance(releases, list):
            raise RuntimeError("GitHub вернул неожиданный список llama.cpp releases")
        for release in releases:
            if not isinstance(release, dict):
                continue
            tag = str(release.get("tag_name") or "")
            for asset in release.get("assets") or []:
                if not isinstance(asset, dict):
                    continue
                name = str(asset.get("name") or "")
                if not self._asset_match(backend, name):
                    continue
                url = str(asset.get("browser_download_url") or "")
                digest = str(asset.get("digest") or "")
                if not url or not digest.startswith("sha256:"):
                    continue
                return RuntimeAsset(backend, tag, name, url, digest)
        raise RuntimeError(f"Не найдена официальная Windows-сборка llama.cpp для backend {backend}")

    def backend_dir(self, backend: str) -> Path:
        return self.root / backend

    def server_path(self, backend: str) -> Path | None:
        folder = self.backend_dir(backend)
        if not folder.exists():
            return None
        matches = list(folder.rglob("llama-server.exe" if os.name == "nt" else "llama-server"))
        return matches[0] if matches else None

    def ensure(self, backend: str, progress=None) -> Path:
        existing = self.server_path(backend)
        if existing:
            return existing
        with _RUNTIME_LOCK:
            existing = self.server_path(backend)
            if existing:
                return existing
            asset = self.discover(backend)
            destination = self.backend_dir(backend)
            destination.parent.mkdir(parents=True, exist_ok=True)
            event("accelerator_runtime_download_start", backend=backend, tag=asset.tag, asset=asset.name)
            with tempfile.TemporaryDirectory(prefix="leshy-runtime-") as tmp:
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
                staged = destination.with_name(destination.name + ".staging")
                if staged.exists():
                    shutil.rmtree(staged, ignore_errors=True)
                shutil.copytree(unpacked, staged)
                manifest = {
                    "schema": 1,
                    "backend": backend,
                    "tag": asset.tag,
                    "asset": asset.name,
                    "source": asset.url,
                    "sha256": asset.digest.split(":", 1)[1],
                    "project": "ggml-org/llama.cpp",
                }
                (staged / "leshy-runtime.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                if destination.exists():
                    shutil.rmtree(destination, ignore_errors=True)
                os.replace(staged, destination)
            result = self.server_path(backend)
            if not result:
                raise RuntimeError(f"llama-server не найден после установки backend {backend}")
            event("accelerator_runtime_ready", backend=backend, tag=asset.tag, server=result)
            return result

    @staticmethod
    def _download(asset: RuntimeAsset, target: Path, progress=None) -> None:
        req = request.Request(asset.url, headers={"User-Agent": "Leshy-Accelerator-Runtime/1.10"})
        digest = sha256()
        downloaded = 0
        with request.urlopen(req, timeout=90) as response, target.open("wb") as handle:
            total = int(response.headers.get("Content-Length") or 0)
            while True:
                block = response.read(4 * 1024 * 1024)
                if not block:
                    break
                handle.write(block)
                digest.update(block)
                downloaded += len(block)
                if progress:
                    try:
                        progress({
                            "backend": asset.backend,
                            "downloaded_bytes": downloaded,
                            "total_bytes": total,
                            "percent": round(downloaded * 100 / total, 1) if total else None,
                        })
                    except Exception:
                        pass
            handle.flush()
            os.fsync(handle.fileno())
        expected = asset.digest.split(":", 1)[1].lower()
        actual = digest.hexdigest().lower()
        if actual != expected:
            target.unlink(missing_ok=True)
            raise RuntimeError(f"SHA-256 runtime {asset.name} не совпадает")


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LlamaServerLLM:
    """Постоянный локальный llama-server, удерживающий модель в RAM/VRAM между запросами."""

    def __init__(
        self,
        manager: LlamaCppRuntimeManager,
        backend: str,
        model_path: Path,
        *,
        ensure_model,
        n_ctx: int = 4096,
        n_gpu_layers: int = -1,
        max_tokens: int = 1536,
        device: str | None = None,
        gpu_count: int = 1,
        startup_timeout: float = 30.0,
        request_timeout: float = 90.0,
    ):
        self.manager = manager
        self.backend = backend
        self.model_path = Path(model_path)
        self.ensure_model = ensure_model
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self.max_tokens = max_tokens
        self.device = device
        self.gpu_count = max(1, int(gpu_count))
        self.startup_timeout = max(5.0, float(startup_timeout))
        self.request_timeout = max(10.0, float(request_timeout))
        self.model = self.model_path.name
        self._process: subprocess.Popen | None = None
        self._port: int | None = None
        self._lock = threading.RLock()
        self._chat_lock = threading.RLock()
        self._ready_since: float | None = None

    def _stop_process(self) -> None:
        if self._process is not None and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                    self._process.wait(timeout=3)
                except Exception:
                    pass
        self._process = None
        self._port = None
        self._ready_since = None

    def _is_healthy(self, base: str, timeout: float = 0.75) -> bool:
        try:
            with request.urlopen(base + "/health", timeout=timeout) as response:
                return response.status == 200
        except Exception:
            return False

    def _ensure_server(self) -> str:
        with self._lock:
            if self._process is not None and self._process.poll() is None and self._port:
                base = f"http://127.0.0.1:{self._port}"
                if self._is_healthy(base):
                    return base
                self._stop_process()

            if not self.model_path.is_file():
                self.model_path = Path(self.ensure_model())
                self.model = self.model_path.name
            server = self.manager.ensure(self.backend)
            port = _free_local_port()
            env = dict(os.environ)
            cpu_threads = max(1, int(os.cpu_count() or 1))
            args = [
                str(server),
                "-m", str(self.model_path),
                "--host", "127.0.0.1",
                "--port", str(port),
                "-c", str(max(512, self.n_ctx)),
                "-np", "1",
                "-t", str(cpu_threads),
            ]
            if self.backend == "vulkan":
                # --fit сам подбирает число GPU-слоёв по реально свободной VRAM.
                # Нельзя одновременно задавать -ngl all: свежий llama.cpp в этом
                # случае отключает fit и снова пытается загрузить всё в видеопамять.
                args += ["-sm", "layer", "--fit", "on"]
            if self.backend == "openvino":
                env["GGML_OPENVINO_DEVICE"] = self.device or "NPU"
                env["GGML_OPENVINO_STATEFUL_EXECUTION"] = "0" if (self.device or "").upper().startswith("NPU") else "1"
                if (self.device or "").upper().startswith("NPU"):
                    args[args.index("-c") + 1] = str(min(max(512, self.n_ctx), 2048))
            log_path = self.manager.server_log_path(self.backend)
            log_handle = log_path.open("ab")
            try:
                self._process = subprocess.Popen(
                    args,
                    env=env,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    creationflags=_CREATE_NO_WINDOW,
                    cwd=str(server.parent),
                )
            except Exception:
                log_handle.close()
                raise
            finally:
                try:
                    log_handle.close()
                except Exception:
                    pass
            self._port = port
            base = f"http://127.0.0.1:{port}"
            event(
                "accelerator_server_start",
                backend=self.backend,
                device=self.device,
                gpu_count=self.gpu_count if self.backend == "vulkan" else 0,
                cpu_threads=cpu_threads,
                startup_timeout_seconds=self.startup_timeout,
                resident=True,
            )
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    self._stop_process()
                    raise LLMUnavailable(f"Локальный {self.backend} backend завершился при запуске. См. {log_path}")
                if self._is_healthy(base, timeout=0.75):
                    self._ready_since = time.monotonic()
                    event("accelerator_server_ready", backend=self.backend, port=port, device=self.device, resident=True)
                    return base
                time.sleep(0.25)
            self._stop_process()
            raise LLMUnavailable(
                f"Локальный {self.backend} backend не стал готов за {int(self.startup_timeout)} секунд"
            )

    def warmup(self) -> bool:
        """Загружает runtime и модель заранее и оставляет сервер resident."""
        self._ensure_server()
        event("accelerator_backend_warmed", backend=self.backend, device=self.device, resident=True)
        return True

    def resident_status(self) -> dict[str, Any]:
        alive = self._process is not None and self._process.poll() is None and bool(self._port)
        return {
            "backend": self.backend,
            "device": self.device,
            "resident": bool(alive),
            "port": self._port if alive else None,
            "ready_seconds": round(time.monotonic() - self._ready_since, 1) if alive and self._ready_since else 0.0,
        }

    def chat(self, messages: list[ChatMessage]) -> str:
        with self._chat_lock:
            try:
                base = self._ensure_server()
                payload = json.dumps({
                    "messages": [{"role": m.role, "content": m.content} for m in messages],
                    "temperature": 0.25,
                    "top_p": 0.92,
                    "max_tokens": self.max_tokens,
                    "stream": False,
                }).encode("utf-8")
                req = request.Request(
                    base + "/v1/chat/completions",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with request.urlopen(req, timeout=self.request_timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                text = str(body["choices"][0]["message"]["content"] or "").strip()
                if not text:
                    raise LLMUnavailable("Ускоренный backend вернул пустой ответ")
                return text
            except error.HTTPError as exc:
                if exc.code >= 500:
                    self._stop_process()
                exception("accelerator_chat_http_failed", exc, backend=self.backend, device=self.device, status=exc.code)
                raise LLMUnavailable(
                    f"Ускоренный backend {self.backend} вернул HTTP {exc.code}"
                ) from exc
            except LLMUnavailable:
                raise
            except Exception as exc:
                self._stop_process()
                exception("accelerator_chat_failed", exc, backend=self.backend, device=self.device)
                raise LLMUnavailable(f"Ускоренный backend {self.backend} не смог выполнить запрос: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            self._stop_process()
