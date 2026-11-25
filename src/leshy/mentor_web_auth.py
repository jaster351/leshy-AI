"""Owner-only Windows device-flow broker. No credentials in HTTP responses."""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path

from .mentor_delivery_request import _reparse, request_delivery
from .mentor_view import _read

# Public OAuth client identifier for the dedicated, repository-scoped GitHub App.
CLIENT_ID = "Iv23lihvvKBZ7yRSrqfE"
_DEVICE = re.compile(rb"MENTOR_DEVICE:([A-Z0-9]{4}-[A-Z0-9]{4}):([0-9]{1,4})")
_ERROR = re.compile(rb"MENTOR_AUTH_ERROR:(setup|profile|lock|device|confirmation|permissions|storage)")
_ERRORS = {
    "setup": "Не удалось подготовить локальную авторизацию.",
    "profile": "Леший и обновлятор должны работать под одной учётной записью Windows.",
    "lock": "Авторизация занята обновлятором. Повторите попытку.",
    "device": "GitHub не выдал код. Проверьте доступность GitHub и Device Flow приложения.",
    "confirmation": "Подтверждение отклонено, истекло или связь с GitHub прервалась.",
    "permissions": "Приложению нужен доступ только к закрытому AI-mentor (Contents: read/write).",
    "storage": "Не удалось сохранить локальную авторизацию.",
}


class MentorWebAuth:
    def __init__(self, brain_dir):
        self.brain = Path(brain_dir)
        self.root = self.brain / "ai-mentor"
        self.lock = threading.Lock()
        self.state = "idle"
        self.code = ""
        self.expires = 0.0
        self.error = ""

    def _script(self):
        if os.name != "nt" or not self.brain.is_dir() or _reparse(self.brain):
            raise ValueError("Подключение через интерфейс доступно для штатной Windows-установки.")
        brain = self.brain.resolve()
        if len(brain.parents) < 3:
            raise ValueError("Не найден штатный репозиторий Лешего.")
        repo = brain.parents[2]
        if brain != repo / "dist" / "Leshy" / "LeshyBrain" or not (repo / ".git").exists():
            raise ValueError("Не найден штатный репозиторий Лешего.")
        script = repo / "windows" / "mentor-authorize.ps1"
        if not script.is_file() or _reparse(script) or _reparse(script.parent):
            raise ValueError("Не найден штатный скрипт авторизации.")
        if self.root.exists() and _reparse(self.root):
            raise ValueError("Недопустимый каталог LiveMentor.")
        return script

    def _saved(self):
        try:
            if not self.root.is_dir() or _reparse(self.root) or _reparse(self.brain):
                return False
            config = _read(self.root / "config.json")
            token = self.root / "github_token.dat"
            return (config.get("enabled") is True
                    and config.get("repository") == "jaster351/AI-mentor"
                    and token.is_file() and not _reparse(token))
        except OSError:
            return False

    def snapshot(self):
        try:
            self._script()
            available = True
        except (ValueError, OSError):
            available = False
        with self.lock:
            result = {"available": available, "saved": self._saved(),
                      "state": self.state, "error": self.error}
            remaining = max(0, int(self.expires - time.monotonic()))
            if self.state == "waiting" and remaining:
                result.update(user_code=self.code, expires_in=remaining)
            return result

    def start(self):
        script = self._script()
        with self.lock:
            if self.state in {"starting", "waiting", "finishing"}:
                return {"state": self.state}
            self.state, self.code, self.error, self.expires = "starting", "", "", 0.0
            thread = threading.Thread(target=self._run, args=(script,), daemon=True,
                                      name="leshy-mentor-web-auth")
            try:
                thread.start()
            except RuntimeError:
                self.state = "error"
                self.error = "Не удалось запустить авторизацию."
                raise
        return {"state": "starting"}

    def _run(self, script):
        proc = None
        timer = None
        confirmed = False
        message = "Не удалось завершить авторизацию GitHub."
        try:
            exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            proc = subprocess.Popen(
                [str(exe), "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(script),
                 "-ClientId", CLIENT_ID, "-EnableMentor", "-WebFlow"],
                cwd=str(script.parent.parent), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=0x08000000, shell=False,
            )
            def expire():
                try:
                    if proc.poll() is None:
                        proc.kill()
                except OSError:
                    pass
            timer = threading.Timer(1900, expire)
            timer.daemon = True
            timer.start()
            with proc.stdout as output:
                for line in iter(lambda: output.readline(1024), b""):
                    line = line.strip()
                    device = _DEVICE.fullmatch(line)
                    failure = _ERROR.fullmatch(line)
                    if device and 1 <= int(device[2]) <= 1800:
                        with self.lock:
                            self.state = "waiting"
                            self.code = device[1].decode("ascii")
                            self.expires = time.monotonic() + int(device[2])
                    elif failure:
                        message = _ERRORS[failure[1].decode("ascii")]
                    elif line == b"MENTOR_AUTH_OK":
                        confirmed = True
                        with self.lock:
                            self.state, self.code, self.expires = "finishing", "", 0.0
            if proc.wait(timeout=30) == 0 and confirmed and self._saved():
                # Use the existing fixed scheduled task; no direct GitHub tokens
                # or user-provided shell arguments cross the Web API.
                try:
                    request_delivery(self.brain)
                except (OSError, ValueError):
                    pass
                with self.lock:
                    self.state, self.error = "connected", ""
                return
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        finally:
            if timer is not None:
                timer.cancel()
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except (OSError, subprocess.SubprocessError):
                    pass
            with self.lock:
                self.code, self.expires = "", 0.0
                if self.state != "connected":
                    self.state, self.error = "error", message
