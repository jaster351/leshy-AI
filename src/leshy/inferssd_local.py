"""Local InferSSD backend: stream supported MoE experts from local NVMe.

InferSSD is the open-source SSD expert streaming engine from WiCi (not a Wi-Fi
GPU). It accepts GGUF, not original Safetensors; this provider is intentionally
separate from inkling_original.InklingOriginalLLM.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
from urllib import request, error, parse

from .llm import LLMUnavailable, finalize_model_output
from .inferssd_observations import infer_home, native_streaming_status

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _local_call(port: int, route: str, *, data: bytes | None = None, timeout: float = 10):
    if not (1 <= int(port) <= 65535) or not route.startswith("/") or route.startswith("//"):
        raise ValueError("Unsafe InferSSD request")
    url = f"http://127.0.0.1:{int(port)}{route}"
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    # Never send user chat messages through an inherited system proxy or HTTP redirect.
    return request.build_opener(request.ProxyHandler({}), _NoRedirect()).open(req, timeout=timeout)


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class InferSSDLocalLLM:
    """Optional on-demand local engine. No remote API, no wireless GPU."""
    n_ctx = 4096
    max_tokens = 1024

    def __init__(self, brain_dir: Path, gguf_path: Path):
        self.brain_dir = Path(brain_dir)
        self.model_path = Path(gguf_path).expanduser()
        self.model = self.model_path.name
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._log = None
        self._port: int | None = None
        self._model_id = self.model

    def _candidate_homes(self) -> list[Path]:
        result = []
        chosen = os.environ.get("INFERSSD_HOME", "").strip()
        for home in (
            Path(chosen).expanduser() if chosen else None,
            self.brain_dir / "runtime" / "inferssd",
            infer_home(),
        ):
            if home is None:
                continue
            normalized = home.resolve()
            if normalized not in result:
                result.append(normalized)
        return result

    def _installed_home(self) -> Path:
        for home in self._candidate_homes():
            candidate = (
                home / "current" / "bin" / "inferssd.exe" if os.name == "nt"
                else home / "bin" / "inferssd"
            )
            if candidate.is_file():
                return home
        return self._candidate_homes()[0]

    def _executable(self) -> str:
        configured = os.getenv("LESHY_INFERSSD_EXECUTABLE", "").strip()
        candidates: list[str] = []
        if configured:
            candidates.append(configured)
        else:
            # Official release's native executable is under the "current"
            # junction. Search the persistent LeshyBrain directory first,
            # then any explicitly configured or legacy user-profile install.
            if os.name == "nt":
                for home in self._candidate_homes():
                    candidates.append(str(home / "current" / "bin" / "inferssd.exe"))
            else:
                for home in self._candidate_homes():
                    candidates.append(str(home / "bin" / "inferssd"))
                candidates.append(str(Path.home() / ".local" / "bin" / "inferssd"))
            # Official Windows install exposes inferssd.cmd to the shell, while
            # its actual executable lives in current/bin/inferssd.exe.
            if os.name == "nt":
                localappdata = os.getenv("LOCALAPPDATA", "").strip()
                if localappdata:
                    candidates.append(str(Path(localappdata) / "inferssd" / "current" / "bin" / "inferssd.exe"))
                for name in ("inferssd.exe", "inferssd"):
                    value = shutil.which(name)
                    if value:
                        candidates.append(value)
            else:
                found = shutil.which("inferssd")
                if found:
                    candidates.append(found)
        for candidate in candidates:
            exe = Path(candidate).expanduser()
            if not exe.is_file():
                continue
            if os.name == "nt" and exe.suffix.lower() in {".cmd", ".bat"}:
                # Do not pass arbitrary model paths to cmd.exe.
                continue
            return str(exe.resolve())
        raise LLMUnavailable(
            "InferSSD не установлен или нативный executable не найден; "
            "укажите LESHY_INFERSSD_EXECUTABLE. https://github.com/wici-ai/inferssd"
        )

    def _validate_model(self):
        if not self.model_path.is_file() or self.model_path.suffix.lower() != ".gguf":
            raise LLMUnavailable("InferSSD принимает только локальную GGUF-модель; оригинальный Safetensors не поддерживается")
        with self.model_path.open("rb") as source:
            if source.read(4) != b"GGUF":
                raise LLMUnavailable("Выбранный файл не является GGUF")

    def _ensure_server(self):
        if self._process is not None and self._process.poll() is None:
            return
        self._validate_model()
        exe = self._executable()
        port = _unused_port()
        log_path = self.brain_dir / "logs" / "inferssd-local.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logfile = log_path.open("ab")
        command = [
            exe, "serve", "-m", str(self.model_path.resolve()),
            "--host", "127.0.0.1", "--port", str(port),
        ]
        # The upstream Windows installer publishes a .cmd wrapper that sets
        # INFERSSD_HOME and INFERSSD_RESOURCES. We launch its *native* executable
        # directly to avoid cmd.exe argument interpretation: supply exactly the
        # same resource descriptor to this local subprocess only.
        child_env = os.environ.copy()
        if os.name != "nt":
            for home in self._candidate_homes():
                wrapper = home / "bin" / "inferssd"
                if wrapper.is_file() and wrapper.resolve() == Path(exe).resolve():
                    child_env["INFERSSD_HOME"] = str(home)
                    break
        if os.name == "nt":
            for home in self._candidate_homes():
                native = home / "current" / "bin" / "inferssd.exe"
                if native.is_file() and Path(exe).resolve() == native.resolve():
                    resources = home / "current" / "engine-resources.json"
                    if not resources.is_file():
                        logfile.close()
                        raise LLMUnavailable("Native InferSSD resource descriptor missing after installation")
                    child_env["INFERSSD_HOME"] = str(home)
                    child_env["INFERSSD_RESOURCES"] = str(resources.resolve())
                    break
        try:
            process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=logfile, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW, close_fds=True, env=child_env,
            )
        except Exception:
            logfile.close()
            raise
        self._log, self._process, self._port = logfile, process, port
        deadline = time.monotonic() + max(15, min(600, int(os.getenv("LESHY_INFERSSD_STARTUP_SECONDS", "180"))))
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.close()
                raise LLMUnavailable("InferSSD завершился во время запуска; проверьте LeshyBrain/logs/inferssd-local.log")
            try:
                with _local_call(port, "/v1/models", timeout=2) as response:
                    data = json.loads(response.read(512 * 1024).decode("utf-8"))
                models = data.get("data", [])
                if isinstance(models, list) and models and isinstance(models[0], dict):
                    model_id = models[0].get("id")
                    if isinstance(model_id, str) and model_id.strip():
                        self._model_id = model_id
                        return
            except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
                time.sleep(1)
        self.close()
        raise LLMUnavailable("InferSSD не поднял локальный API в отведённое время; проверьте журнал")

    def chat(self, messages):
        with self._lock:
            self._ensure_server()
            payload = {
                "model": self._model_id,
                "messages": [{"role": m.role, "content": str(m.content)} for m in messages],
                "temperature": 0.3,
                "max_tokens": self.max_tokens,
                "stream": False,
            }
            try:
                with _local_call(
                    self._port, "/v1/chat/completions",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    timeout=max(30, min(600, int(os.getenv("LESHY_INFERSSD_REQUEST_SECONDS", "180")))),
                ) as response:
                    data = json.loads(response.read(16 * 1024 * 1024).decode("utf-8"))
                answer = finalize_model_output(data["choices"][0]["message"]["content"])
                if not answer:
                    raise LLMUnavailable("InferSSD вернул пустой ответ")
                return answer
            except LLMUnavailable:
                raise
            except (OSError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                raise LLMUnavailable(f"Ошибка локального InferSSD: {type(exc).__name__}: {exc}") from exc

    def resident_status(self):
        # Read only owned native run observations. An API response/loaded process
        # never counts as verified expert offloading.
        evidence = native_streaming_status(self.model_path, home=self._installed_home())
        return {
            "loaded": self._process is not None and self._process.poll() is None,
            "backend": "inferssd-local",
            "ssd_streaming_requested": True,
            "ssd_streaming_verified": evidence["ssd_streaming_verified"],
            "ssd_streaming_evidence": evidence,
            "model": str(self.model_path.resolve()),
            "model_exists": self.model_path.is_file(),
            "remote_gpu": False,
        }

    def close(self):
        with self._lock:
            process = self._process
            self._process = None
            self._port = None
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if self._log is not None:
                self._log.close()
                self._log = None
