"""Hourly, locally approved AI-mentor exchange. No ordinary chat or remote commands."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from urllib import request

from .diagnostics import event
from .mentor_batch import BatchRunner, atomic, now, read_obj, INTERVAL

MAX_BYTES = 2 * 1024 * 1024


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class MentorBridge:
    def __init__(self, brain_dir: Path, app):
        self.root = Path(brain_dir) / "ai-mentor"
        self.app = app
        self.stop_event = threading.Event()
        self.thread = None
        self.github = request.build_opener(_NoRedirect())

    def start(self):
        if self.root.is_symlink() or not (self.root / "config.json").is_file():
            return
        self.thread = threading.Thread(target=self._run, name="leshy-ai-mentor", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=1)

    def _gh(self, repo, token, path="", payload=None, method="GET"):
        headers = {"Accept": "application/json", "User-Agent": "AI-mentor",
                   "Authorization": "Bearer " + token}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request("https://api.github.com/repos/" + repo + path,
                              data=data, headers=headers, method=method)
        with self.github.open(req, timeout=20) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Превышен размер ответа GitHub")
        return json.loads(raw)

    def _run(self):
        if self.stop_event.wait(15):
            return
        runner = BatchRunner(self.root, self.app, self.stop_event, self._gh)
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                config = runner.config()
                if config.get("enabled") is not True:
                    atomic(self.root / "status.json", {"status": "disabled", "timestamp": now()})
                    return
                clock = self.root / "cycle.json"
                if clock.exists():
                    last = read_obj(clock).get("started_epoch")
                    if not isinstance(last, (int, float)) or not 0 < last < 1e12:
                        raise ValueError("Некорректные часы учебного цикла")
                    remaining = max(0, min(INTERVAL, INTERVAL - (time.time() - last)))
                    if remaining > 0 and self.stop_event.wait(remaining):
                        return
                    config = runner.config()
                    if config.get("enabled") is not True:
                        return
                atomic(clock, {"started_epoch": time.time()})
                started = time.monotonic()
                atomic(self.root / "status.json", {"status": "checking", "timestamp": now()})
                status = runner.cycle(config)
                atomic(self.root / "status.json", {"status": status, "timestamp": now()})
            except Exception as exc:
                event("mentor_bridge_error", error_type=type(exc).__name__)
                try:
                    atomic(self.root / "status.json", {"status": "error", "timestamp": now(),
                                                      "error_type": type(exc).__name__})
                except OSError:
                    pass
            if self.stop_event.wait(max(1, INTERVAL - (time.monotonic() - started))):
                return
