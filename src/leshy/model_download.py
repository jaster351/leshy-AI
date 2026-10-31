from __future__ import annotations

import hashlib
from pathlib import Path
import threading
from urllib import request


OFFICIAL_QWEN3_8B_Q4_URL = "https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf"
OFFICIAL_QWEN3_8B_Q4_SHA256 = "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785"


class ModelDownloader:
    def __init__(self, target: Path):
        self.target = Path(target)
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._status = {"state": "ready" if self.target.is_file() else "missing", "downloaded": 0, "total": 0, "error": ""}

    def status(self) -> dict:
        with self._lock:
            result = dict(self._status)
        result["path"] = str(self.target)
        result["exists"] = self.target.is_file()
        return result

    def start(self) -> dict:
        with self._lock:
            if self.target.is_file():
                self._status.update(state="ready", error="")
                return self.status()
            if self._thread and self._thread.is_alive():
                return self.status()
            self._status = {"state": "downloading", "downloaded": 0, "total": 0, "error": ""}
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
            req = request.Request(OFFICIAL_QWEN3_8B_Q4_URL, headers={"User-Agent": "Leshy-AI/1.10.3"})
            with request.urlopen(req, timeout=60) as response, temporary.open("wb") as out:
                total = int(response.headers.get("Content-Length") or 0)
                self._set(total=total)
                while True:
                    chunk = response.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
                    self._set(downloaded=downloaded)
            digest = hasher.hexdigest().lower()
            if digest != OFFICIAL_QWEN3_8B_Q4_SHA256:
                raise RuntimeError(f"SHA-256 модели не совпал: {digest}")
            temporary.replace(self.target)
            self._set(state="ready", downloaded=downloaded, total=downloaded, error="")
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            self._set(state="error", error=str(exc)[:1000])
