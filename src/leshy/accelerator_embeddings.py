from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from urllib import error, request

from .accelerator_runtime import LlamaCppRuntimeManager
from .diagnostics import event, exception

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LlamaServerEmbeddings:
    """Embedding provider через локальный llama-server accelerated backend."""

    def __init__(
        self,
        manager: LlamaCppRuntimeManager,
        backend: str,
        model_path: Path,
        *,
        ensure_model,
        n_ctx: int = 2048,
        n_gpu_layers: int = -1,
        device: str | None = None,
        gpu_count: int = 1,
        startup_timeout: float = 20.0,
        request_timeout: float = 30.0,
    ):
        self.manager = manager
        self.backend = backend
        self.model_path = Path(model_path)
        self.ensure_model = ensure_model
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self.device = device
        self.gpu_count = max(1, int(gpu_count))
        self.startup_timeout = max(5.0, float(startup_timeout))
        self.request_timeout = max(5.0, float(request_timeout))
        self.model = f"{backend}:{self.model_path.name}"
        self._process: subprocess.Popen | None = None
        self._port: int | None = None
        self._lock = threading.RLock()
        self._embed_lock = threading.RLock()

    def available(self) -> bool:
        return self.model_path.is_file()

    def _stop_process(self) -> None:
        if self._process is not None and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
        self._process = None
        self._port = None

    @staticmethod
    def _is_healthy(base: str, timeout: float = 0.75) -> bool:
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
                self.model = f"{self.backend}:{self.model_path.name}"
            server = self.manager.ensure(self.backend)
            port = _free_local_port()
            env = dict(os.environ)
            cpu_threads = max(1, int(os.cpu_count() or 1))
            args = [
                str(server), "-m", str(self.model_path),
                "--host", "127.0.0.1", "--port", str(port),
                "-c", str(max(512, self.n_ctx)), "-np", "1",
                "-t", str(cpu_threads),
                "--embedding", "--pooling", "mean", "--no-warmup",
            ]
            if self.backend == "vulkan":
                args += ["-ngl", "all", "-sm", "layer", "--fit", "on"]
            if self.backend == "openvino":
                env["GGML_OPENVINO_DEVICE"] = self.device or "NPU"
                env["GGML_OPENVINO_STATEFUL_EXECUTION"] = "0"
                if (self.device or "").upper().startswith("NPU"):
                    args[args.index("-c") + 1] = str(min(max(512, self.n_ctx), 1024))
            log_path = self.manager.root / f"{self.backend}-embeddings.log"
            log_handle = log_path.open("ab")
            self._process = subprocess.Popen(
                args,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=_CREATE_NO_WINDOW,
                cwd=str(server.parent),
            )
            self._port = port
            base = f"http://127.0.0.1:{port}"
            event(
                "accelerator_embeddings_start",
                backend=self.backend,
                device=self.device,
                gpu_count=self.gpu_count if self.backend == "vulkan" else 0,
                cpu_threads=cpu_threads,
                startup_timeout_seconds=self.startup_timeout,
            )
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    self._stop_process()
                    raise RuntimeError(f"Embedding backend {self.backend} завершился. См. {log_path}")
                if self._is_healthy(base):
                    event("accelerator_embeddings_ready", backend=self.backend, device=self.device, port=port)
                    return base
                time.sleep(0.25)
            self._stop_process()
            raise RuntimeError(
                f"Embedding backend {self.backend} не готов за {int(self.startup_timeout)} секунд"
            )

    def embed(self, text: str) -> list[float]:
        with self._embed_lock:
            try:
                base = self._ensure_server()
                payload = json.dumps({"input": text[:16000], "encoding_format": "float"}).encode("utf-8")
                req = request.Request(
                    base + "/v1/embeddings",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with request.urlopen(req, timeout=self.request_timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                vector = body["data"][0]["embedding"]
                return [float(value) for value in vector]
            except error.HTTPError as exc:
                self._stop_process()
                exception(
                    "accelerator_embedding_http_failed",
                    exc,
                    backend=self.backend,
                    device=self.device,
                    status=exc.code,
                )
                raise RuntimeError(f"Embedding backend {self.backend} вернул HTTP {exc.code}") from exc
            except Exception as exc:
                self._stop_process()
                exception("accelerator_embedding_failed", exc, backend=self.backend, device=self.device)
                raise RuntimeError(f"Embedding backend {self.backend} недоступен: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            self._stop_process()
