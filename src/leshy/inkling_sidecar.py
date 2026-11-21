"""Loopback-only sidecar for original Inkling 2 Mini in frozen Windows Leshy.

Heavy torch/Transformers dependencies are installed in the external Python
environment, not bundled in PyInstaller. The original model is never converted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib import request, error

from .llm import LLMUnavailable, finalize_model_output
from .inkling_original import checkpoint_status, REPOSITORY

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _local_url(port: int, suffix: str, data: bytes | None = None, timeout: float = 5):
    req = request.Request(
        f"http://127.0.0.1:{port}{suffix}", data=data,
        method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    return request.build_opener(request.ProxyHandler({}), _NoRedirect()).open(req, timeout=timeout)


class InklingOriginalSidecarLLM:
    model = REPOSITORY
    n_ctx = 8192
    max_tokens = 1024

    def __init__(self, brain_dir: Path):
        self.brain_dir = Path(brain_dir).resolve()
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._port: int | None = None
        self._log = None

    def _python_command(self):
        override = os.environ.get("LESHY_INKLING_PYTHON", "").strip()
        if override:
            if not Path(override).is_file():
                raise LLMUnavailable(f"Python для оригинала не найден: {override}")
            return [override]
        isolated = self.brain_dir / "runtime" / "inkling-original-venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if isolated.is_file():
            return [str(isolated.resolve())]
        if getattr(sys, "frozen", False) and os.name == "nt":
            launcher = shutil.which("py")
            if launcher:
                return [launcher, "-3.11"]
            raise LLMUnavailable("Установите Python 3.11 и выполните Prepare-InklingOriginal.ps1")
        return [sys.executable]

    @staticmethod
    def _free_port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _ensure_running(self):
        if self._proc is not None and self._proc.poll() is None:
            return
        current = checkpoint_status(self.brain_dir)
        if current.get("state") != "downloaded":
            raise LLMUnavailable(f"Оригинальный Inkling 2 Mini пока не загружен полностью: {current.get('state')}")
        port = self._free_port()
        log_path = self.brain_dir / "logs" / "inkling-original-sidecar.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = log_path.open("ab")
        command = [
            *self._python_command(), "-m", "leshy.inkling_original",
            "serve", "--brain-dir", str(self.brain_dir), "--port", str(port),
        ]
        # The dedicated model venv intentionally does not install the full
        # leshy-ai distribution: import only the checked-out source modules.
        # Keep PYTHONPATH scoped to this model subprocess, never to Leshy.exe.
        child_env = os.environ.copy()
        source = child_env.get("LESHY_INKLING_SOURCE_DIR", "").strip()
        if source and (Path(source) / "leshy" / "inkling_original.py").is_file():
            child_env["PYTHONPATH"] = source
            version_file = Path(source).parent / "VERSION"
            if version_file.is_file():
                child_env["LESHY_VERSION_FILE"] = str(version_file)
        try:
            self._proc = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=self._log,
                stderr=subprocess.STDOUT, close_fds=True, creationflags=CREATE_NO_WINDOW,
                env=child_env,
            )
        except Exception as exc:
            self.close()
            raise LLMUnavailable(f"Не удалось запустить локальный Python sidecar: {exc}") from exc
        self._port = port
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                self.close()
                raise LLMUnavailable("Inkling Python sidecar завершился при запуске; см. LeshyBrain/logs/inkling-original-sidecar.log")
            try:
                with _local_url(port, "/health", timeout=2) as response:
                    value = json.load(response)
                if value.get("ready") is True:
                    return
            except (OSError, ValueError, TimeoutError):
                time.sleep(0.5)
        self.close()
        raise LLMUnavailable("Inkling Python sidecar не поднял локальный API")

    def chat(self, messages):
        with self._lock:
            self._ensure_running()
            payload = json.dumps({
                "model": self.model,
                "messages": [{"role": m.role, "content": str(m.content)} for m in messages],
                "stream": False,
            }, ensure_ascii=False).encode("utf-8")
            try:
                with _local_url(self._port, "/v1/chat/completions", data=payload, timeout=600) as response:
                    body = json.loads(response.read(16 * 1024 * 1024).decode("utf-8"))
                answer = finalize_model_output(body["choices"][0]["message"]["content"])
                if not answer:
                    raise LLMUnavailable("Inkling вернул пустой ответ")
                return answer
            except error.HTTPError as exc:
                details = exc.read(2000).decode("utf-8", errors="replace")
                raise LLMUnavailable(f"Оригинал Inkling не смог ответить: HTTP {exc.code}, {details}") from exc
            except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                raise LLMUnavailable(f"Локальный Inkling sidecar недоступен: {type(exc).__name__}: {exc}") from exc

    def resident_status(self):
        return {
            "loaded": self._proc is not None and self._proc.poll() is None,
            "backend": "original-inkling-python-sidecar",
            "remote_gpu": False,
            "checkpoint": checkpoint_status(self.brain_dir).get("state"),
        }

    def close(self):
        with self._lock:
            proc = self._proc
            self._proc = None
            self._port = None
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            if self._log is not None:
                self._log.close()
                self._log = None
